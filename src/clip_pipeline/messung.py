"""Messung am fertig gerenderten Video (Cutter-Maßstab 1.0, Spec §3.1/§3.2).

Ein einziger ffmpeg-Durchlauf mit Bordmitteln liefert, was ein Senior-Editor beim Abnehmen sieht und hört:
  - Ton: Lautheit Momentary/Short-term alle 0,1 s, integrierte Lautheit, Lautheitsumfang, True Peak (ebur128), Stille
  - Vollbild (klein, 180 bzw. 320 px breit): Szenenwechsel (scdet), Schwarz (blackdetect), Helligkeit und Sättigung
  - Spielbild-Band (160 px breit): Helligkeit, Bewegung YDIF je Bild, Standbild (freezedetect)
  - Hash: 8×8-Graubild des Bands, 2 Bilder/s (doppelte Momente erkennen)
  - Stems (nur mit Musik, entwurf.rendere): Lautheit des Vordergrunds (Spiel + Stimmen) und der Musik nach dem Ducking
Hier wird nur gemessen und gelesen, bewertet wird in kriterien.py. Das Modul **wirft nie**: Was nicht klappt, steht in
Messung.fehler. Fehlt ein Filter (filter_vorhanden), fällt sein Zweig weg; scheitert der ganze Durchlauf, laufen Bild,
Ton und Stems einzeln nach, und was klappt, zählt. Scheitert alles, ist Messung.version 0.

Jeder ffmpeg-Aufruf läuft über medien.fuehre_aus (Wächter gegen Hänger). Die Zwischendateien (*.txt, hash.raw) liegen
in einem eigenen Temp-Ordner und sind danach wieder weg; bleiben soll nur, was der Aufrufer mit speichere() ablegt.
"""

from __future__ import annotations

import dataclasses
import functools
import json
import logging
import math
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .medien import MedienFehler, fuehre_aus, probe
from .shorts import _filterpfad

log = logging.getLogger(__name__)

MESS_VERSION = 1
SCD_SCHWELLE = 8              # scdet: Szenenwechsel ab diesem Score
SCD_ZUSAMMEN_S = 0.1          # scdet meldet einen Schnitt gern doppelt (16,00/16,03) -> zusammenlegen
KLEIN_HOCH, KLEIN_QUER = 180, 320   # Breite des verkleinerten Vollbilds: Short bzw. Zusammenschnitt
BAND_B = 160                  # Breite des verkleinerten Spielbild-Bands
HASH_FPS, HASH_SEITE = 2, 8   # Hash: 8×8 Graubild, 2 Bilder je Sekunde
LOUDNORM_LRA = 20             # so weit, dass loudnorm im zweiten Pass linear bleibt (entwurf.normalisiere_ton)
# Filter, die der Messdurchlauf nutzt – vor der Einführung auf dem Mini mit `ffmpeg -filters` prüfen (Spec §3.2)
FILTER = ("scdet", "blackdetect", "signalstats", "freezedetect", "ebur128", "silencedetect", "ssim", "loudnorm")
ZWEIGE = ("bild", "ton", "stems")


@dataclass
class Messung:
    """Messreihen eines Videos. m/s (und mv/mm) alle 0,1 s, die Bild-Reihen je Bild (Zeit in t_bild).
    scd: (Zeit, Score); schwarz/stand/stille: (Anfang, Ende). stand ist None, wenn freezedetect fehlt.
    hash: je halbe Sekunde 64 Bit (Pixel heller als der Mittelwert des 8×8-Bilds)."""
    version: int = MESS_VERSION
    dauer_s: float = 0.0
    fps: float = 0.0
    m: list[float] = field(default_factory=list)
    s: list[float] = field(default_factory=list)
    i_lufs: float | None = None
    lra: float | None = None
    tp_db: float | None = None
    ton_da: bool = False
    mv: list[float] | None = None
    mm: list[float] | None = None
    iv: float | None = None
    yavg_v: list[float] = field(default_factory=list)
    sat_v: list[float] = field(default_factory=list)
    yavg_b: list[float] = field(default_factory=list)
    ydif_b: list[float] = field(default_factory=list)
    t_bild: list[float] = field(default_factory=list)
    scd: list[tuple[float, float]] = field(default_factory=list)
    schwarz: list[tuple[float, float]] = field(default_factory=list)
    stand: list[tuple[float, float]] | None = None
    stille: list[tuple[float, float]] = field(default_factory=list)
    hash: list[int] = field(default_factory=list)
    dauer_ton: float | None = None
    dauer_bild: float | None = None
    fehler: list[str] = field(default_factory=list)


# --- ffmpeg -------------------------------------------------------------------------------------------

@functools.lru_cache(maxsize=1)
def filter_vorhanden() -> frozenset[str]:
    """Namen aller Filter dieses ffmpeg (`ffmpeg -hide_banner -filters`); leer, wenn ffmpeg fehlt oder scheitert."""
    try:
        text = fuehre_aus(["ffmpeg", "-hide_banner", "-filters"], "ffmpeg-Filter", timeout=60)
    except MedienFehler as exc:
        log.warning("ffmpeg-Filter nicht lesbar: %s", exc)
        return frozenset()
    namen = set()
    for zeile in text.splitlines():
        teile = zeile.split()
        # Zeilen wie „ .S. blackdetect       V->V       Detect video …“ – Flags aus T, S, C und Punkten
        if len(teile) >= 3 and re.fullmatch(r"[TSC.]{2,3}", teile[0]) and "->" in teile[2]:
            namen.add(teile[1])
    return frozenset(namen)


def _crop(band: tuple[int, int] | None) -> str:
    return f"crop=iw:{band[1] - band[0]}:0:{band[0]}," if band else ""


def graph(band: tuple[int, int] | None, klein_b: int, dateien: dict[str, Path], vorhanden: frozenset[str],
          stems: bool, *, zweige: tuple[str, ...] = ZWEIGE) -> str:
    """Filtergraph des Messdurchlaufs. band: (y0, y1) des Spielbilds (None = volle Höhe); klein_b: Breite des
    verkleinerten Vollbilds; dateien: Zieldateien der Metadaten „voll“, „band“, „ton“, „vorder“, „musik“.
    Eingang 0 = das Video, Eingang 1 = stems.mka (zwei Mono-Spuren: Vordergrund, Musik). Ausgänge: [vs] [vb] [vh]
    (Bild), [a] (Ton), [s1] [s2] (Stems) – jeder Zweig mit benanntem Ausgang, sonst verweigert ffmpeg 6.1 den Graphen.
    Fehlt ein Filter, fällt er aus seiner Kette (Beispiel: ohne scdet keine Szenenwechsel, der Rest läuft); ohne
    ebur128 und silencedetect gibt es keinen Ton-Zweig, ohne ebur128 keine Stems. zweige: nur diese Teile bauen
    (der Rückfall in messe misst Bild, Ton und Stems einzeln)."""
    def da(name: str, text: str) -> list[str]:
        return [text] if name in vorhanden else []

    teile = []
    if "bild" in zweige:
        voll = [f"scale={klein_b}:-2:flags=area", *da("scdet", f"scdet=threshold={SCD_SCHWELLE}"),
                *da("blackdetect", "blackdetect=d=0.1:pix_th=0.10"), *da("signalstats", "signalstats"),
                f"metadata=mode=print:file={_filterpfad(dateien['voll'])}"]
        streifen = [f"{_crop(band)}scale={BAND_B}:-2:flags=area", *da("signalstats", "signalstats"),
                    *da("freezedetect", "freezedetect=n=-60dB:d=0.5"),
                    f"metadata=mode=print:file={_filterpfad(dateien['band'])}"]
        teile += ["[0:v]split=3[v1][v2][v3]", f"[v1]{','.join(voll)}[vs]", f"[v2]{','.join(streifen)}[vb]",
                  f"[v3]{_crop(band)}fps={HASH_FPS},scale={HASH_SEITE}:{HASH_SEITE}:flags=area,format=gray[vh]"]
    if "ton" in zweige and ({"ebur128", "silencedetect"} & vorhanden):
        kette = [*da("ebur128", "ebur128=peak=true:metadata=1"), *da("silencedetect", "silencedetect=n=-50dB:d=0.3"),
                 f"ametadata=mode=print:file={_filterpfad(dateien['ton'])}"]
        teile.append(f"[0:a]{','.join(kette)}[a]")
    if stems and "stems" in zweige and "ebur128" in vorhanden:
        # Vordergrund mit allen Schlüsseln (auch I für iv), Musik nur M
        teile += [f"[1:a:0]ebur128=metadata=1,ametadata=mode=print:file={_filterpfad(dateien['vorder'])}[s1]",
                  f"[1:a:1]ebur128=metadata=1,ametadata=mode=print:key=lavfi.r128.M:"
                  f"file={_filterpfad(dateien['musik'])}[s2]"]
    return ";".join(teile)


def _ausgaenge(text: str) -> list[str]:
    return re.findall(r"\[(vs|vb|vh|a|s1|s2)\](?:;|$)", text)


def _lauf(video: Path, stems: Path | None, graph_text: str, dateien: dict[str, Path], ordner: Path,
          timeout_s: float) -> str:
    """Ein ffmpeg-Durchlauf über den Graphen; Rückgabe stdout + stderr (dort stehen black_*, freeze_*, silence_*)."""
    from .entwurf import _graph_argumente   # erst hier: entwurf importiert dieses Modul

    raus = _ausgaenge(graph_text)
    befehl = ["ffmpeg", "-hide_banner", "-nostdin", "-nostats", "-v", "info", "-y", "-i", str(video)]
    if "s1" in raus and stems is not None:
        befehl += ["-i", str(stems)]
    befehl += _graph_argumente(graph_text, ordner / "messung.filtergraph.txt")
    null = [x for x in raus if x != "vh"]
    for x in null:
        befehl += ["-map", f"[{x}]"]
    if null:
        befehl += ["-f", "null", "-"]
    if "vh" in raus:
        befehl += ["-map", "[vh]", "-f", "rawvideo", "-pix_fmt", "gray", str(dateien["hash"])]
    return fuehre_aus(befehl, f"Messung {video.name}", timeout=timeout_s)


# --- Lesen --------------------------------------------------------------------------------------------

def _bloecke(pfad: Path) -> list[tuple[float, dict[str, str]]]:
    """metadata=mode=print-Datei: je Bild „frame:… pts_time:T“, danach key=value-Zeilen."""
    if not pfad.is_file():
        return []
    bloecke: list[tuple[float, dict[str, str]]] = []
    for zeile in pfad.read_text(encoding="utf-8", errors="replace").splitlines():
        if treffer := re.search(r"pts_time:(\S+)", zeile):
            bloecke.append((float(treffer.group(1)), {}))
        elif bloecke and "=" in zeile:
            schluessel, _, wert = zeile.partition("=")
            bloecke[-1][1][schluessel.strip()] = wert.strip()
    return bloecke


def _reihe(bloecke: list[tuple[float, dict[str, str]]], schluessel: str) -> list[float]:
    return [float(d[schluessel]) for _t, d in bloecke if schluessel in d]


def _intervalle(text: str, anfang: str, ende: str, dauer: float) -> list[tuple[float, float]]:
    """Paare aus stderr, z. B. „silence_start: 5.01“ … „silence_end: 6.02“; ein offenes Intervall endet am Videoende."""
    ergebnis, offen = [], None
    for name, wert in re.findall(rf"({anfang}|{ende})\s*[:=]\s*(-?[\d.]+)", text):
        if name == anfang:
            offen = float(wert)
        elif offen is not None:
            ergebnis.append((offen, float(wert)))
            offen = None
    if offen is not None:
        ergebnis.append((offen, max(offen, dauer)))
    return ergebnis


def _zusammen(scd: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Szenenwechsel näher als SCD_ZUSAMMEN_S: ein Treffer (erste Zeit, höchster Score)."""
    ergebnis: list[tuple[float, float]] = []
    for t, score in sorted(scd):
        if ergebnis and t - ergebnis[-1][0] < SCD_ZUSAMMEN_S - 1e-9:
            ergebnis[-1] = (ergebnis[-1][0], max(ergebnis[-1][1], score))
        else:
            ergebnis.append((t, score))
    return ergebnis


def _hash(pfad: Path) -> list[int]:
    if not pfad.is_file():
        return []
    roh = pfad.read_bytes()
    n = HASH_SEITE * HASH_SEITE
    werte = []
    for k in range(len(roh) // n):
        bild = roh[k * n:(k + 1) * n]
        mittel = sum(bild) / n
        werte.append(sum(1 << j for j, p in enumerate(bild) if p > mittel))
    return werte


def _lies(m: Messung, zweige: list[str], text: str, dateien: dict[str, Path], vorhanden: frozenset[str]) -> None:
    """Überträgt die Ergebnisse der gelaufenen Zweige in m."""
    if "bild" in zweige:
        voll = _bloecke(dateien["voll"])
        m.yavg_v = _reihe(voll, "lavfi.signalstats.YAVG")
        m.sat_v = _reihe(voll, "lavfi.signalstats.SATAVG")
        m.scd = _zusammen([(t, float(d.get("lavfi.scd.score", 0))) for t, d in voll if "lavfi.scd.time" in d])
        band = _bloecke(dateien["band"])
        m.t_bild = [t for t, _d in band]
        m.yavg_b = _reihe(band, "lavfi.signalstats.YAVG")
        m.ydif_b = _reihe(band, "lavfi.signalstats.YDIF")
        m.schwarz = _intervalle(text, "black_start", "black_end", m.dauer_s)
        m.stand = (_intervalle(text, "lavfi.freezedetect.freeze_start", "lavfi.freezedetect.freeze_end", m.dauer_s)
                   if "freezedetect" in vorhanden else None)
        m.hash = _hash(dateien["hash"])
    if "ton" in zweige:
        ton = _bloecke(dateien["ton"])
        m.m = _reihe(ton, "lavfi.r128.M")
        m.s = _reihe(ton, "lavfi.r128.S")
        if werte := _reihe(ton, "lavfi.r128.I"):
            m.i_lufs = werte[-1]   # integriert: der Stand am letzten Block = über das ganze Video
        if werte := _reihe(ton, "lavfi.r128.LRA"):
            m.lra = werte[-1]
        if spitzen := _reihe(ton, "lavfi.r128.true_peak"):  # linear -> dBTP
            m.tp_db = 20 * math.log10(max(spitzen)) if max(spitzen) > 0 else -120.0
        if "silencedetect" in vorhanden:
            m.stille = _intervalle(text, "silence_start", "silence_end", m.dauer_s)
    if "stems" in zweige:
        vorder = _bloecke(dateien["vorder"])
        m.mv = _reihe(vorder, "lavfi.r128.M") or None
        m.iv = (_reihe(vorder, "lavfi.r128.I") or [None])[-1]
        m.mm = _reihe(_bloecke(dateien["musik"]), "lavfi.r128.M") or None


def _stream_dauern(video: Path) -> tuple[float | None, float | None]:
    """(Tonlänge, Bildlänge) aus ffprobe je Stream – für das Tor „Ton und Bild verschieden lang“."""
    text = fuehre_aus(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration", "-of", "json",
                       str(video)], f"Stream-Dauern {video.name}", timeout=60)
    ton = bild = None
    for s in json.loads(text[text.index("{"):]).get("streams", []):
        try:
            dauer = float(s.get("duration"))
        except (TypeError, ValueError):
            continue
        if s.get("codec_type") == "audio" and ton is None:
            ton = dauer
        elif s.get("codec_type") == "video" and bild is None:
            bild = dauer
    return ton, bild


def messe(video: Path, ordner: Path, *, band: tuple[int, int] | None, stems: Path | None,
          timeout_s: float) -> Messung:
    """Misst das fertige Video in einem Durchlauf (Spec §3.2). ordner: dort entsteht kurz der Temp-Ordner der
    Zwischendateien (gleiche Platte wie der Entwurf), z. B. kritik-<id>/. band: (y0, y1) des Spielbilds im Video
    (Sidecar des Renderers, None = volle Höhe). stems: stems.mka des Renderers oder None. timeout_s: harte Grenze
    (der Aufrufer nimmt max(180, 4 · Dauer)); dazu wacht medien.fuehre_aus über Hänger.
    Wirft nie: Fehler stehen in Messung.fehler; scheitert der gemeinsame Durchlauf, laufen die Zweige einzeln nach;
    scheitert alles (auch: kaputte Datei), ist version 0."""
    m = Messung()
    try:
        _messe(m, Path(video), Path(ordner), band, stems, timeout_s)
    except Exception as exc:  # noqa: BLE001 – wirft nie, der Entwurf geht deswegen nicht verloren
        m.fehler.append(f"Messung abgebrochen: {exc}"[:500])
        if not (m.t_bild or m.m):
            m.version = 0
    return m


def _messe(m: Messung, video: Path, ordner: Path, band: tuple[int, int] | None, stems: Path | None,
           timeout_s: float) -> None:
    try:
        info = probe(video)
    except (MedienFehler, OSError, ValueError) as exc:
        m.fehler.append(f"Video nicht lesbar: {exc}"[:500])
        m.version = 0
        return
    if not (info.breite and info.hoehe):
        m.fehler.append("kein Videostrom")
        m.version = 0
        return
    m.dauer_s, m.fps, m.ton_da = info.dauer_s, info.fps, bool(info.tonspuren)
    try:
        m.dauer_ton, m.dauer_bild = _stream_dauern(video)
    except (MedienFehler, ValueError) as exc:
        m.fehler.append(f"Stream-Dauern: {exc}"[:300])
    vorhanden = filter_vorhanden()
    if fehlend := [f for f in FILTER if f not in vorhanden]:
        m.fehler.append("Filter fehlt: " + ", ".join(fehlend))
    if band is not None:  # auf das Bild begrenzen; ein Band unter 8 Zeilen misst nichts Sinnvolles
        y0, y1 = max(0, int(band[0])), min(info.hoehe, int(band[1]))
        band = (y0, y1) if y1 - y0 >= 8 else None
    klein_b = KLEIN_HOCH if info.hoehe > info.breite else KLEIN_QUER
    mit_stems = stems is not None and Path(stems).is_file() and "ebur128" in vorhanden
    zweige = ["bild", *(["ton"] if m.ton_da else []), *(["stems"] if mit_stems else [])]
    if not m.ton_da:
        m.fehler.append("kein Tonstrom")
    ordner.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".messung-", dir=ordner) as tmp:
        dateien = {n: Path(tmp) / f"{n}.txt" for n in ("voll", "band", "ton", "vorder", "musik")}
        dateien["hash"] = Path(tmp) / "hash.raw"

        def lauf(welche: list[str]) -> None:
            for d in dateien.values():
                d.unlink(missing_ok=True)
            if not (text := graph(band, klein_b, dateien, vorhanden, mit_stems, zweige=tuple(welche))):
                return   # alle Filter dieses Zweigs fehlen
            text = _lauf(video, stems if mit_stems else None, text, dateien, Path(tmp), timeout_s)
            _lies(m, welche, text, dateien, vorhanden)

        try:
            lauf(zweige)
            return
        except (MedienFehler, OSError, ValueError) as exc:
            m.fehler.append(f"Messdurchlauf: {exc}"[:500])
        geklappt = False
        for zweig in zweige:  # einzeln nach: was klappt, zählt
            try:
                lauf([zweig])
                geklappt = True
            except (MedienFehler, OSError, ValueError) as exc:
                m.fehler.append(f"Zweig {zweig}: {exc}"[:500])
        if not geklappt:
            m.version = 0


def ssim(video: Path, t_a: float, t_b: float, band: tuple[int, int] | None) -> float | None:
    """SSIM (0..1) zwischen dem Bild bei t_a und dem bei t_b, nur im Band (Loop-Naht: letztes gegen erstes Bild).
    Negative Zeiten zählen vom Ende (−0,04 = letztes Bild bei 30 fps). None, wenn ssim fehlt oder ffmpeg scheitert."""
    if "ssim" not in filter_vorhanden():
        return None

    def wo(t: float) -> list[str]:
        return ["-sseof", f"{t:.3f}"] if t < 0 else ["-ss", f"{t:.3f}"]

    ausschnitt = _crop(band)
    befehl = ["ffmpeg", "-hide_banner", "-nostdin", "-nostats", *wo(t_a), "-i", str(video), *wo(t_b), "-i",
              str(video), "-filter_complex", f"[0:v]{ausschnitt}null[a];[1:v]{ausschnitt}null[b];[a][b]ssim[v]",
              "-map", "[v]", "-frames:v", "1", "-f", "null", "-"]
    try:
        text = fuehre_aus(befehl, f"SSIM {Path(video).name}", timeout=60)
    except MedienFehler as exc:
        log.warning("SSIM nicht messbar: %s", exc)
        return None
    treffer = re.findall(r"All:\s*([\d.]+)", text)
    return float(treffer[-1]) if treffer else None


def loudnorm_messen(datei: Path, ziel_i: float, ziel_tp: float) -> dict | None:
    """Pass 1 der Lautheitsangleichung (entwurf.normalisiere_ton): loudnorm misst die erste Tonspur, Rückgabe die
    Werte als Zahlen, z. B. {"input_i": -39.1, "input_tp": -37.2, "input_lra": 1.7, "input_thresh": -49.2,
    "target_offset": 0.13, …} (stille Spur: input_i = -inf). None ohne Tonstrom, ohne loudnorm oder bei Fehler."""
    if "loudnorm" not in filter_vorhanden():
        return None
    befehl = ["ffmpeg", "-hide_banner", "-nostdin", "-nostats", "-i", str(datei), "-map", "0:a:0", "-af",
              f"loudnorm=I={ziel_i}:TP={ziel_tp}:LRA={LOUDNORM_LRA}:print_format=json", "-f", "null", "-"]
    try:
        return loudnorm_json(fuehre_aus(befehl, f"Lautheit {Path(datei).name}"))
    except MedienFehler as exc:
        log.warning("Lautheit nicht messbar: %s", exc)
        return None


def loudnorm_json(text: str) -> dict | None:
    """Der JSON-Block, den loudnorm mit print_format=json nach stderr schreibt (der letzte im Text), mit Zahlen
    statt Zeichenketten; normalization_type bleibt Text. None, wenn keiner da ist."""
    bloecke = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", text)
    if not bloecke:
        return None
    try:
        roh = json.loads(bloecke[-1])
    except ValueError:
        return None
    werte: dict = {}
    for k, v in roh.items():
        try:
            werte[k] = float(v) if k != "normalization_type" else str(v)
        except (TypeError, ValueError):
            werte[k] = v
    return werte


# --- Ablage -------------------------------------------------------------------------------------------

_RUNDEN = {"m": 1, "s": 1, "mv": 1, "mm": 1, "yavg_v": 1, "sat_v": 1, "yavg_b": 1, "ydif_b": 2, "t_bild": 3}


def speichere(m: Messung, pfad: Path) -> None:
    """Als JSON ablegen (kritik-<id>/messung.json), Reihen gerundet – ca. 30 KB je Short. Erst in eine Temp-Datei,
    dann umbenannt. Wirft nie: ein Fehler beim Schreiben landet im Log."""
    daten = dataclasses.asdict(m)
    for name, stellen in _RUNDEN.items():
        if daten.get(name) is not None:
            daten[name] = [round(x, stellen) for x in daten[name]]
    for name in ("scd", "schwarz", "stand", "stille"):
        if daten.get(name) is not None:
            daten[name] = [[round(a, 3), round(b, 3)] for a, b in daten[name]]
    for name in ("i_lufs", "lra", "tp_db", "iv", "dauer_ton", "dauer_bild"):
        if isinstance(daten.get(name), float):
            daten[name] = round(daten[name], 2) if math.isfinite(daten[name]) else None
    try:
        pfad = Path(pfad)
        pfad.parent.mkdir(parents=True, exist_ok=True)
        tmp = pfad.with_name(pfad.name + ".tmp")
        tmp.write_text(json.dumps(daten, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(pfad)
    except OSError as exc:
        log.warning("Messung nicht gespeichert (%s): %s", pfad, exc)


def lade(pfad: Path) -> Messung | None:
    """Liest messung.json; None, wenn die Datei fehlt oder nicht passt (dann misst der Aufrufer neu)."""
    try:
        daten = json.loads(Path(pfad).read_text(encoding="utf-8"))
        felder = {f.name for f in dataclasses.fields(Messung)}
        m = Messung(**{k: v for k, v in daten.items() if k in felder})
        for name in ("scd", "schwarz", "stille"):
            setattr(m, name, [(float(a), float(b)) for a, b in getattr(m, name) or []])
        if m.stand is not None:
            m.stand = [(float(a), float(b)) for a, b in m.stand]
        return m
    except (OSError, ValueError, TypeError) as exc:
        log.info("Messung nicht lesbar (%s): %s", pfad, exc)
        return None
