"""Rendern einer Regie-Schnittliste (`pipeline render-entwurf <id> [--final | --messen]`).

Entwurf (auf dem Mini): klein genug für Telegram (< 48 MB), 720p bzw. 720×1280, VA-API wenn vorhanden, sonst CPU.
Final (auf pve-big, NVENC): volle Auflösung. Der Mini legt dafür einen Auftrag an, pve-big rendert ihn per
`clip-big-steuer final <name>` und fährt danach herunter (siehe big.py, deploy/big/).

Aufbau des FFmpeg-Filtergraphen:
  1. je Segment: Ausschnitt mit Übergangs-Griffen, zuerst feste Bildrate, dann Bild auf Zielgröße (16:9 mit Rand
     bzw. 9:16 mit unscharfem Hintergrund); Ton: alle Spuren (Spiel + Mikro) zu Stereo gemischt
  2. Verketten mit xfade/acrossfade – die Mitte jedes Übergangs liegt genau auf der Segmentgrenze (dem Beat)
  3. Effekte (Regisseur 2.0, nur mit liste["effekte"].an): Zoom je Segment nur auf dem Spielbild, Look (Short je
     Segment, 16:9 danach auf das ganze Bild), nach den Übergängen Blenden-Filter und Kill-Titel/Zähler
     (effekt_filter.py) – der Plan steht in der Schnittliste
  4. Musik ab ihrem Versatz, geduckt unter dem Spielton (sidechaincompress), ein- und ausgeblendet ([regie.ton]); die
     Klänge des Plans (sfx.py) kommen danach dazu
  5. Short: Schriftzug "clip-battle.de"
  6. Danach (30.09., Cutter-Maßstab): Lautheit angleichen (normalisiere_ton), Sidecar `<name>.render.json` und – nur
     mit Musik, auf Wunsch – die Stems für die Messung (messung.py)
Ohne Effekte (an = false oder version 3) ist der Graph zeichengleich mit dem von vorher.
"""

from __future__ import annotations

import functools
import json
import logging
import math
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path

from . import effekt_filter, effekte, medien, messung, musik, sfx, shorts
from .konfig import Konfig, KonfigFehler
from .medien import MedienFehler, fuehre_aus, probe

log = logging.getLogger(__name__)

ENTWURF_KURZE_SEITE = 720
UEBERHANG_S = 0.2
ENTWURF_KBIT = 4000
# Der Filtergraph ist EIN Argument auf der Befehlszeile – Linux erlaubt je Argument höchstens 128 KB. Darüber geht er
# als Datei an ffmpeg (_graph_argumente, 28.09.: viele Effekte in einem langen Zusammenschnitt). MAX_GRAPH ist nur
# noch eine Notbremse gegen einen außer Kontrolle geratenen Plan.
MAX_INLINE = 100_000
MAX_GRAPH = 4_000_000
# Ton-Hygiene (Cutter-Maßstab R1/R3, 30.09.), überschreibbar in [regie.ton]: Ziel-Lautheit und True Peak für alle
# Plattformen (Annahme A1), Musik-Kanten – einblenden nur gegen Knacken, ausblenden im Short kurz (Loop-Ende),
# im Zusammenschnitt weich
TON_STANDARD = {"lufs": -14.0, "true_peak": -1.5, "musik_ein_s": 0.03, "musik_aus_s_short": 0.25,
                "musik_aus_s_zs": 2.0}
AAC_RESERVE_DB = 1.0  # Abstand des loudnorm-Ziels zu [regie.ton] true_peak (normalisiere_ton)


@functools.lru_cache(maxsize=1)
def _ffmpeg_hauptversion() -> int:
    """Hauptversion von ffmpeg (7 für „ffmpeg version 7.1…“); 0, wenn sie sich nicht lesen lässt."""
    try:
        text = subprocess.run(["ffmpeg", "-hide_banner", "-version"], capture_output=True, text=True, check=False).stdout
    except OSError:
        return 0
    treffer = re.search(r"ffmpeg version n?(\d+)\.", text)
    return int(treffer.group(1)) if treffer else 0


def _graph_argumente(graph: str, datei: Path) -> list[str]:
    """Kleiner Graph: „-filter_complex <graph>“ wie bisher. Großer Graph: in datei schreiben und per
    „-/filter_complex <datei>“ (ffmpeg ≥ 7) bzw. „-filter_complex_script <datei>“ (ältere) übergeben."""
    if len(graph.encode()) < MAX_INLINE:
        return ["-filter_complex", graph]
    datei.write_text(graph, encoding="utf-8")
    return ["-/filter_complex" if _ffmpeg_hauptversion() >= 7 else "-filter_complex_script", str(datei)]


def schnitt_dauer(fps: int) -> float:
    """Harter Schnitt = Übergang über ein einziges Bild. Aufrunden! Ist die Dauer auch nur minimal kürzer als
    ein Bild (1/30 -> "0.0333"), bricht xfade still ab und das Video endet nach dem ersten Segment."""
    return math.ceil(10000 / fps) / 10000


def _griffe(segmente: list[dict], fps: int) -> list[tuple[float, float]]:
    """(vorne, hinten) je Segment: was zusätzlich aus der Quelle genommen wird, damit die Übergänge die
    Zeitleiste nicht verkürzen. Weiche Übergänge: je die halbe Dauer vorne und hinten (Mitte auf dem Beat).
    Harter Schnitt: das vorherige Segment bekommt das eine Übergangsbild dazu."""
    ergebnis = []
    for i, s in enumerate(segmente):
        vorne = s["uebergang"]["dauer_s"] / 2 if i > 0 and s["uebergang"]["art"] != "schnitt" else 0.0
        hinten = 0.0
        if i + 1 < len(segmente):
            naechster = segmente[i + 1]["uebergang"]
            hinten = schnitt_dauer(fps) if naechster["art"] == "schnitt" else naechster["dauer_s"] / 2
        ergebnis.append((vorne, hinten))
    return ergebnis


def _gerade(x: float) -> int:
    return int(round(x / 2) * 2)


RAHMEN_MAX = 1.6   # Short: Obergrenze der Angabe; wirksam begrenzt rahmen_grenze (lesbarer Titel über der Bedienzone)


def rahmen_zoom(liste: dict) -> float:
    """Schnittstil (stile.py, 30.09.): Short-Spielbild vergrößert und mittig zugeschnitten – statt eines schmalen
    Streifens (16:9 in 9:16 = ein Drittel der Höhe). 1,0 = wie bisher; nur Short."""
    if liste.get("format") != "short":
        return 1.0
    try:
        return max(1.0, min(RAHMEN_MAX, float((liste.get("parameter") or {}).get("rahmen_zoom", 1.0))))
    except (TypeError, ValueError):
        return 1.0


def rahmen_grenze(liste: dict, b: int, h: int, quellen: list[tuple[int, int]]) -> float:
    """Wirksamer Rahmen-Zoom (30.09.): so groß wie der Stil will, aber nur so weit, dass unter dem Spielbild der
    höchsten Quelle (4:3 ist höher als 16:9) ein lesbarer Kill-Titel über der Bedienzone Platz hat
    (effekt_filter.spiel_hoehe_max). 16:9 bei 720×1280: höchstens ×1,28; 4:3: ×1,0. quellen: (Breite, Höhe) je Datei."""
    z = rahmen_zoom(liste)
    quellen = [(qb, qh) for qb, qh in quellen if qb and qh]
    if z <= 1.0 or not quellen:
        return z
    hoch = max(qh / qb for qb, qh in quellen)
    return max(1.0, min(z, math.floor(100 * effekt_filter.spiel_hoehe_max(h) / (b * hoch)) / 100))


def _bild(i: int, b: int, h: int, fps: int, hochformat: bool, zoom: str = "", look: str = "", tempo: str = "",
          flash: str = "", rahmen: float = 1.0) -> str:
    """fps zuerst: Alle weiteren Filter sehen nur noch die Bilder, die ins Ergebnis kommen (60 fps -> halb so viele).
    tempo (effekt_filter.tempo_video) noch davor: Zeitlupe/Zeitraffer dehnen die Quelle, fps macht daraus die
    Bilder des Videos (eine 60-fps-Aufnahme bleibt bei Faktor 0,5 im 30-fps-Short flüssig).
    zoom: Kette aus effekt_filter.zoom – sitzt auf dem Spielbild allein (Short: vor dem Einsetzen in den unscharfen
    Hintergrund, 16:9: vor dem Rand), der Hintergrund zoomt also nie mit.
    look (nur Short): Farblook auf Spielbild und kleinem Hintergrund, bevor beide zusammengesetzt werden.
    flash (effekt_filter.bildfilter): der Effekt-Katalog (Blitz, Strobe, Farb-Pop, Negativ, Blur …) nur auf dem
    Spielbild, nach dem Zoom, vor dem Look.
    rahmen (rahmen_zoom, nur Short): Spielbild rahmen-fach breiter skalieren und auf b zuschneiden (Mitte)."""
    z = f",{zoom}" if zoom else ""
    groesse = f"scale={_gerade(b * rahmen)}:-2,crop={b}:ih" if hochformat and rahmen > 1.0 else f"scale={b}:-2"
    fl = f",{flash}" if flash else ""
    lk = f",{look}" if look else ""
    kopf = f"[{i}:v]{tempo + ',' if tempo else ''}fps={fps}"
    if hochformat:  # wie shorts.py "unschaerfe": ganzes Spielbild, unscharfer Hintergrund füllt den Rand
        # Hintergrund auf b/4 × h/4 weichzeichnen (1/16 der Pixel; Radius 5 statt 20 = gleich unscharf), dann hoch
        b4, h4 = _gerade(b / 4), _gerade(h / 4)
        return (f"{kopf},split=2[hg{i}][vg{i}];"
                f"[hg{i}]scale={b4}:{h4}:force_original_aspect_ratio=increase,crop={b4}:{h4},boxblur=5:2{lk},"
                f"scale={b}:{h},eq=brightness=-0.08[hgb{i}];"
                f"[vg{i}]{groesse}{z}{fl}{lk}[vgs{i}];[hgb{i}][vgs{i}]overlay=(W-w)/2:(H-h)/2,"
                f"format=yuv420p,setsar=1,settb=AVTB[v{i}]")
    return (f"{kopf},scale={b}:{h}:force_original_aspect_ratio=decrease{z}{fl},pad={b}:{h}:(ow-iw)/2:(oh-ih)/2,"
            f"format=yuv420p,setsar=1,settb=AVTB[v{i}]")


def _ton(i: int, spuren: int, dauer: float, stimmen: bool = True, tempo: str = "") -> str:
    """Ton von Eingang i. stimmen=False: nur Spur 0 (Spielton) – Mikro/Chat ab Spur 1 nur, wenn der Moment sie
    braucht (merkmale.stimmen_gebraucht, im Segment als „stimmen“; fehlt der Schlüssel, alte Liste → alle Spuren).
    tempo (effekt_filter.tempo_ton): Zeitlupe/Zeitraffer auf dem gemischten Ton, vor apad/atrim (Stille braucht
    keins – sie ist ohnehin so lang wie das Bild)."""
    einheit = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
    if not stimmen:
        spuren = min(spuren, 1)
    if spuren == 0:
        return f"anullsrc=r=48000:cl=stereo,atrim=0:{dauer:.3f},{einheit}[a{i}]"
    tp = f",{tempo}" if tempo else ""
    if spuren == 1:
        return f"[{i}:a:0]{einheit}{tp},apad,atrim=0:{dauer:.3f}[a{i}]"
    eingaenge = "".join(f"[{i}:a:{s}]" for s in range(spuren))
    return f"{eingaenge}amix=inputs={spuren}:normalize=0,{einheit}{tp},apad,atrim=0:{dauer:.3f}[a{i}]"


def filtergraph(liste: dict, spuren: list[int], *, b: int, h: int, musik_eingang: int | None,
                schrift: Path | None, sfx_pegel: float = sfx.SFX_PEGEL,
                zeichenbreite: float = effekte.STANDARD["titel_zeichenbreite"],
                spiel_h: int | None = None, rahmen: float | None = None, ton: dict | None = None,
                stems: bool = False) -> tuple[str, float]:
    """(Graph, Länge). Eingänge: 0 … n−1 die Segmente, dann die Musik (musik_eingang), dann die Klänge des Plans in
    der Reihenfolge von sfx.mischung. schrift: für clip-battle.de und die Kill-Titel (ohne: keine Texte).
    spiel_h: Höhe des höchsten Spielbilds im Short (effekt_filter.spiel_hoehe) – die Texte bleiben darüber und
    darunter; ohne Angabe 16:9. rahmen: wirksamer Rahmen-Zoom (rahmen_grenze); ohne Angabe aus der Liste.
    ton: Werte aus [regie.ton] (ton_einstellungen; ohne Angabe TON_STANDARD) – Musik-Kanten: einblenden
    musik_ein_s, ausblenden musik_aus_s_short bzw. musik_aus_s_zs (Cutter-Maßstab R3).
    stems (nur mit Musik, Cutter-Maßstab R5): zusätzlich die Ausgänge [stv] (Vordergrund = Spiel + Stimmen vor den
    Klängen) und [stm] (Musik nach dem Ducking) für die Messung (messung.py). Ohne stems ist der Graph zeichengleich
    mit dem ohne diesen Schalter."""
    segmente = liste["segmente"]
    fps = int(liste["fps"])
    hoch = liste["format"] == "short"
    griffe = _griffe(segmente, fps)
    ereignisse = effekte.zeitleiste(liste)  # leer ohne Effekte (an = false oder version 3)
    zooms = effekt_filter.zooms_je_segment(liste, ereignisse, griffe)
    bild = effekt_filter.je_segment(liste, ereignisse, griffe, effekte.BILD)   # Katalog nur aufs Spielbild
    look = effekt_filter.look_der_liste(liste) if hoch else ""  # 16:9: einmal global (effekt_filter.global_kette)
    rahmen = rahmen_zoom(liste) if rahmen is None else rahmen
    teile, laengen = [], []
    for i, (s, (vorne, hinten)) in enumerate(zip(segmente, griffe)):
        laenge = (s["zeit_ende"] - s["zeit_start"]) + vorne + hinten
        laengen.append(laenge)
        # Tempo-Fenster (Zeitlupe/Zeitraffer) in der Zeit des Segment-Eingangs, der beim vorderen Griff beginnt –
        # ohne Fenster bleibt der Graph zeichengleich mit dem von vorher (alte Listen kennen kein quelle_start_s)
        tempo_v = tempo_a = ""
        if fenster := effekte.tempo_fenster(s):
            eingang = s["quelle_start_s"] - vorne
            tempo_v, tempo_a = effekt_filter.tempo_video(fenster, eingang), effekt_filter.tempo_ton(i, s, eingang)
        teile.append(_bild(i, b, h, fps, hoch, effekt_filter.zoom(i, zooms[i]) if i in zooms else "", look, tempo_v,
                           effekt_filter.bildfilter(bild[i], b) if i in bild else "", rahmen))
        teile.append(_ton(i, spuren[i], laenge, bool(s.get("stimmen", True)), tempo_a))
    # Verketten: offset_i = bisherige Länge − Übergangsdauer (siehe Herleitung in docs/ENTSCHEIDUNGEN.md E8)
    v, a, gesamt = "[v0]", "[a0]", laengen[0]
    for i in range(1, len(segmente)):
        u = segmente[i]["uebergang"]
        d = u["dauer_s"] if u["art"] != "schnitt" else 0.0
        art = effekt_filter.xfade_art(u["art"])
        d_x = d if d > 0 else schnitt_dauer(fps)
        teile.append(f"{v}[v{i}]xfade=transition={art}:duration={d_x:.4f}:offset={gesamt - d_x:.4f}[vx{i}]")
        # Ton gleich lang überblenden wie das Bild – sonst laufen Bild und Ton pro Schnitt auseinander
        teile.append(f"{a}[a{i}]acrossfade=d={d_x:.4f}:c1=tri:c2=tri[ax{i}]")
        gesamt += laengen[i] - d_x
        v, a = f"[vx{i}]", f"[ax{i}]"
    kette = v
    if fx := effekt_filter.global_kette(liste, ereignisse, b, h, schrift, zeichenbreite, spiel_h):
        teile.append(f"{kette}{fx}[vfx]")
        kette = "[vfx]"
    if hoch and liste.get("overlay") and schrift is not None:
        teile.append(f"{kette}drawtext=fontfile={shorts._filterpfad(schrift)}:text={shorts._text(liste['overlay'])}:"
                     f"fontsize={int(h * 0.03)}:fontcolor=white@0.9:borderw=4:bordercolor=black@0.6:"
                     f"x=(w-text_w)/2:y=h*0.12[vtext]")
        kette = "[vtext]"
    teile.append(f"{kette}null[vout]")
    # Klänge des Plans: Eingänge hinter Segmenten und Musik, Ausgang [sfx]; sie kommen NACH dem Ducking dazu
    _klaenge, sfx_teil = sfx.mischung(ereignisse, len(segmente) + (musik_eingang is not None), sfx_pegel)
    if sfx_teil:
        teile.append(sfx_teil)
    if musik_eingang is not None:
        m = liste["musik"]
        t = {**TON_STANDARD, **(ton or {})}
        ein = float(t["musik_ein_s"])
        aus = float(t["musik_aus_s_short"] if hoch else t["musik_aus_s_zs"])
        teile += [
            f"{a}asplit=3[spiel][schluessel][stv]" if stems else f"{a}asplit=2[spiel][schluessel]",
            f"[{musik_eingang}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
            f"volume={m['pegel']},atrim=0:{gesamt:.3f},afade=t=in:d={ein:g},"
            f"afade=t=out:st={max(0.0, gesamt - aus):.3f}:d={aus:g}[mus]",
            "[mus][schluessel]sidechaincompress=threshold=0.05:ratio=6:attack=20:release=400"
            + ("[leiser0]" if stems else "[leiser]"),
            *(["[leiser0]asplit=2[leiser][stm]"] if stems else []),
            sfx.abmischung(["[spiel]", "[leiser]"], bool(sfx_teil)),
        ]
    else:
        teile.append(sfx.abmischung([a], bool(sfx_teil)))
    # apad + "-t gesamt" am Ausgang: Der Ton ist immer genau so lang wie das Bild. Die Mischkette (amix,
    # sidechaincompress, alimiter) verlor im Test gelegentlich bis zu 0,1 s am Ende, nicht reproduzierbar.
    return ";".join(teile), gesamt


def encoder(konfig: Konfig, final: bool) -> tuple[list[str], list[str], str]:
    """(globale Optionen, Video-Optionen, Name). Final: NVENC; Entwurf: VA-API, sonst libx264.
    Bei VA-API lädt der Filtergraph die Bilder selbst auf die GPU (hwupload) – kein -vf neben -filter_complex."""
    if final:
        return [], ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "20", "-b:v", "0"], "h264_nvenc"
    geraet = str(konfig.wert("schnitt.vaapi_geraet", "/dev/dri/renderD128"))
    if Path(geraet).exists() and konfig.wert("regie.vaapi", True):
        return ["-vaapi_device", geraet], ["-c:v", "h264_vaapi"], "h264_vaapi"
    return [], ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p"], "libx264"


def rendere(liste: dict, ziel: Path, konfig: Konfig, *, final: bool = False, max_bytes: int = 48_000_000,
            encoder_name: str | None = None, volle_aufloesung: bool = False, crf: int = 23,
            kbit_max: int = ENTWURF_KBIT, vollstaendig: bool = True, stems: Path | None = None,
            _beginn: float | None = None) -> dict:
    """Rendert eine Schnittliste nach `ziel` (erst `<name>.tmp.mp4`, dann umbenannt – nie eine halbe Datei).
    Rückgabe {"datei", "mb", "dauer_s", "encoder", "aufloesung"}, z. B. {…, "encoder": "libx264",
    "aufloesung": [720, 1280]}.

    volle_aufloesung: keine Verkleinerung auf die kurze Seite 720 (für die Upload-Fassung, 1080×1920).
    crf: gilt nur für libx264. VA-API kennt kein crf und bekommt die Rate als -b:v.
    kbit_max: Deckel der Videorate. Das Budget aus max_bytes gilt immer zusätzlich:
      kbit = min(max_bytes · 8 · 0,88 / dauer / 1000 − 160, kbit_max), mindestens 400.
      Beispiel: 48 MB, 45 s → min(7349, kbit_max); der Entwurf deckelt mit ENTWURF_KBIT (4000), die
      Upload-Fassung setzt kbit_max höher (upload_fassung).
    encoder_name="libx264": CPU erzwingen – so ruft sich der Rückfall selbst auf.
    vollstaendig=False: isolierte Effekt-/Segmentvorschau; alle vollständigen Videos prüfen die Formatgrenzen.
    final (NVENC, pve-big): crf, kbit_max und max_bytes wirken nicht, es gibt keine Größenprüfung.
    stems (nur mit Musik, Cutter-Maßstab R5): zweiter Ausgang dorthin (z. B. kritik-<id>/stems.mka), zwei Mono-Spuren
      FLAC – Vordergrund und Musik nach dem Ducking – für messung.messe; ohne Musik entsteht keine Datei.
    Ton-Hygiene (R1): nach der Dauerprüfung und vor der Größenprüfung gleicht normalisiere_ton die Lautheit auf
      [regie.ton] an (zwei Pässe loudnorm); scheitert das, bleibt der Ton wie gerendert.
    Sidecar: neben ziel liegt danach `<name>.render.json` (Geometrie, Encoder, Ton – _sidecar) für die Messung, dazu
      die Laufzeit (Stufe 3, M142): render_s (Sekunden ab dem ersten Versuch, mit Lautheit), rueckfall (VA-API → CPU),
      eingabe_mb (Summe der Eingabedateien) und dauer_s (echte Bildlänge). _beginn: nur der Rückfall selbst.

    Fehler: MedienFehler, wenn eine Moment-Datei oder die Musik fehlt (vor ffmpeg) oder ffmpeg scheitert – VA-API
    fällt vorher einmal auf CPU zurück (mit denselben Werten). ZuGross(kbit) mit der benutzten Rate, wenn die Datei
    ohne final über max_bytes liegt; bei ZuGross gibt es keinen Rückfall auf CPU (entscheidet der Aufrufer)."""
    beginn = time.monotonic() if _beginn is None else _beginn   # Rückfall: die Zeit zählt ab dem ersten Versuch
    if vollstaendig or final:
        _pruefe_formatdauer(liste)
    segmente = liste["segmente"]
    b, h = liste["aufloesung"]
    if not final and not volle_aufloesung:  # Entwurf: kurze Seite 720 (Upload-Fassung: volle Größe)
        faktor = ENTWURF_KURZE_SEITE / min(b, h)
        b, h = _gerade(b * faktor), _gerade(h * faktor)
    griffe = _griffe(segmente, int(liste["fps"]))
    befehl = ["ffmpeg", "-hide_banner", "-nostdin", "-y"]
    spuren, quellen = [], []
    for s, (vorne, hinten) in zip(segmente, griffe):
        datei = Path(s["datei"])
        if not datei.is_file():
            raise MedienFehler(f"Moment-Datei fehlt: {datei}")
        info = probe(datei)
        spuren.append(len(info.tonspuren))
        quellen.append((info.breite, info.hoehe))
        start = s["quelle_start_s"] - vorne
        dauer = (s["quelle_ende_s"] - s["quelle_start_s"]) + vorne + hinten
        # Überhang: xfade braucht Bilder bis GANZ ans Ende des Übergangs, sonst bricht die Ausgabe still ab.
        # Überzählige Bilder verwirft xfade; der Ton wird im Graphen exakt auf die Länge geschnitten.
        ueberhang = max(0.0, min(UEBERHANG_S, info.dauer_s - (start + dauer)))
        befehl += ["-ss", f"{max(0.0, start):.3f}", "-t", f"{dauer + ueberhang:.3f}", "-i", str(datei)]
    # Short: Texte über/unter dem höchsten Spielbild (4:3-Aufnahme ist höher); Zoom nur so weit, dass Titel lesbar bleiben
    rahmen = rahmen_grenze(liste, b, h, quellen)
    spiel_h = max((effekt_filter.spiel_hoehe(_gerade(b * rahmen), qb, qh) for qb, qh in quellen if qb and qh), default=0)
    musik_eingang = None
    if m := liste.get("musik"):
        datei = musik.ordner(konfig) / m["datei"]
        if not datei.is_file():
            raise MedienFehler(f"Musik fehlt: {datei}")
        musik_eingang = len(segmente)
        befehl += ["-stream_loop", "-1", "-ss", f"{m['start_s']:.3f}", "-i", str(datei)]
    # Klänge des Effekt-Plans: je Klang eine WAV (wird beim ersten Mal erzeugt), Reihenfolge wie in filtergraph
    ereignisse = effekte.zeitleiste(liste)
    klaenge, _ = sfx.mischung(ereignisse, len(segmente) + (musik_eingang is not None), sfx.pegel(konfig))
    befehl += sfx.eingaenge(konfig, klaenge)
    mit_stems = stems is not None and musik_eingang is not None
    graph, gesamt = filtergraph(liste, spuren, b=b, h=h, musik_eingang=musik_eingang, schrift=_schrift(liste, konfig),
                                sfx_pegel=sfx.pegel(konfig), zeichenbreite=_zeichenbreite(konfig),
                                spiel_h=spiel_h or None, rahmen=rahmen, ton=ton_einstellungen(konfig),
                                stems=mit_stems)
    if len(graph.encode()) >= MAX_GRAPH:
        raise MedienFehler(f"Entwurf {liste['name']}: Filtergraph {len(graph.encode()) // 1000} KB – höchstens "
                           f"{MAX_GRAPH // 1000} KB")
    eingang, video, name = encoder(konfig, final)
    if encoder_name == "libx264":
        eingang, video, name = [], ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p"], "libx264"
    # Entwurf: gute Qualität, aber gedeckelt – höchstens kbit_max (ENTWURF_KBIT) und sicher unter max_bytes (Telegram).
    # (Das volle Budget auszuschöpfen hieße 40 MB für 40 s – unnötig für eine Vorschau auf dem Handy.)
    # Die Upload-Fassung (upload_fassung) setzt kbit_max höher und nutzt so das Budget aus – dieselbe Rechnung.
    kbit = max(400, min(int(max_bytes * 8 * 0.88 / max(gesamt, 1) / 1000) - 160, kbit_max))
    deckel = ["-maxrate", f"{kbit}k", "-bufsize", f"{2 * kbit}k"]
    rate = [] if final else (["-b:v", f"{kbit}k", *deckel] if name == "h264_vaapi" else ["-crf", str(crf), *deckel])
    if name == "h264_vaapi":  # VA-API: Filter-Ausgang auf die GPU hochladen
        graph = graph.replace("null[vout]", "format=nv12,hwupload[vout]")
    ziel.parent.mkdir(parents=True, exist_ok=True)
    tmp = ziel.with_name(ziel.stem + ".tmp" + ziel.suffix)
    graph_datei = ziel.with_name(ziel.stem + ".filtergraph.txt")   # nur bei großem Graphen, danach wieder weg
    befehl = befehl[:4] + eingang + befehl[4:]
    befehl += [*_graph_argumente(graph, graph_datei), "-map", "[vout]", "-map", "[aout]", "-t", f"{gesamt:.3f}",
               *video, *rate]
    befehl += ["-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(tmp)]
    stems_tmp = stems.with_name(stems.stem + ".tmp" + stems.suffix) if mit_stems else None
    if stems_tmp is not None:  # zweiter Ausgang: Stems für die Messung, gleich lang wie das Video
        stems_tmp.parent.mkdir(parents=True, exist_ok=True)
        befehl += ["-map", "[stv]", "-map", "[stm]", "-ac", "1", "-c:a", "flac", "-sample_fmt", "s16",
                   "-t", f"{gesamt:.3f}", str(stems_tmp)]   # 16 Bit reichen für die Lautheit (halb so groß)
    try:
        fuehre_aus(befehl, f"Entwurf {liste['name']}")
    except MedienFehler:
        if stems_tmp is not None:
            stems_tmp.unlink(missing_ok=True)
        if name == "h264_vaapi" and encoder_name is None:  # VA-API streikt -> CPU
            return rendere(liste, ziel, konfig, final=final, max_bytes=max_bytes, encoder_name="libx264",
                           volle_aufloesung=volle_aufloesung, crf=crf, kbit_max=kbit_max,
                           vollstaendig=vollstaendig, stems=stems, _beginn=beginn)
        raise
    finally:
        graph_datei.unlink(missing_ok=True)
    # ffmpeg kann trotz Exit 0 vorzeitig enden (z. B. xfade). Erst die tatsächlich vorhandenen Videobilder
    # prüfen; ein längerer Audiostream/Container darf einen abgebrochenen Film nicht verdecken.
    try:
        wirklich = _pruefe_renderdauer(liste, tmp, vollstaendig=vollstaendig or final)
    except MedienFehler:
        if stems_tmp is not None:
            stems_tmp.unlink(missing_ok=True)
        raise
    ton = normalisiere_ton(tmp, konfig)   # R1: Lautheit angleichen; scheitert es, bleibt der Ton wie gerendert
    groesse = tmp.stat().st_size
    if not final and groesse > max_bytes:
        tmp.unlink(missing_ok=True)
        if stems_tmp is not None:
            stems_tmp.unlink(missing_ok=True)
        raise ZuGross(f"Entwurf {liste['name']} ist {groesse // 1_000_000} MB groß (Grenze {max_bytes // 1_000_000})",
                      kbit)
    medien.uebernehmen(tmp, ziel)   # mit fsync: nach einem Stromausfall nie ein leeres Video unter dem Endnamen (M144)
    if stems_tmp is not None and stems_tmp.is_file():
        medien.uebernehmen(stems_tmp, stems)
    _sidecar(ziel, {**geometrie(liste, b, h, quellen, rahmen), "fps": int(liste["fps"]), "encoder": name, **ton,
                    "stems": str(stems) if mit_stems else None, "render_s": round(time.monotonic() - beginn, 1),
                    "rueckfall": _beginn is not None, "eingabe_mb": _eingabe_mb(befehl), "dauer_s": round(wirklich, 3)})
    return {"datei": str(ziel), "mb": round(groesse / 1e6, 1), "dauer_s": round(wirklich, 3), "encoder": name,
            "aufloesung": [b, h]}


def ton_einstellungen(konfig: Konfig) -> dict:
    """[regie.ton] mit TON_STANDARD als Rückfall, alles als Zahl (ein falscher Wert gilt als Standard)."""
    werte = {}
    for k, standard in TON_STANDARD.items():
        try:
            werte[k] = float(konfig.wert(f"regie.ton.{k}", standard))
        except (TypeError, ValueError):
            werte[k] = standard
    return werte


def geometrie(liste: dict, b: int, h: int, quellen: list[tuple[int, int]], rahmen: float) -> dict:
    """Wo das Spielbild im gerenderten Video liegt – dieselbe Rechnung wie _bild. quellen: (Breite, Höhe) je Segment
    (0 = unbekannt, dann 16:9); rahmen: wirksamer Rahmen-Zoom (rahmen_grenze).
    Rückgabe {"b", "h", "format", "band": [y0, y1], "spiel_anteile": [Spielbild-Höhe / h je Segment], "rahmen"}.
    band: Short = effekt_filter.spielbild mit dem NIEDRIGSTEN Spielbild der Liste – so liegt ein Ausschnitt sicher im
    Spielbild jedes Segments, Logo, Titel und unscharfer Rand fallen heraus. 16:9: volle Höhe, nur ein Rand oben/unten
    (Quelle breiter als 16:9) wird abgezogen – 4:3 bekommt Ränder links/rechts, die Höhe bleibt voll.
    Beispiel Short 720×1280, 16:9-Quellen, rahmen 1,0: band [436, 842], spiel_anteile je 0,3172."""
    hoch = liste.get("format") == "short"
    hoehen = []
    for qb, qh in quellen:
        qb, qh = (qb, qh) if qb and qh else (16, 9)
        if hoch:
            hoehen.append(min(h, effekt_filter.spiel_hoehe(_gerade(b * rahmen), qb, qh)))
        else:  # scale=b:h:force_original_aspect_ratio=decrease, dann pad
            hoehen.append(min(h, _gerade(qh * min(b / qb, h / qh))))
    niedrigste = min(hoehen, default=effekt_filter.spiel_hoehe(b, 16, 9) if hoch else h)
    if hoch:
        y0, y1 = effekt_filter.spielbild(b, h, niedrigste)
    else:
        y0 = ((h - niedrigste) // 2) & ~1
        y1 = y0 + niedrigste
    return {"b": b, "h": h, "format": liste.get("format"), "band": [y0, y1],
            "spiel_anteile": [round(x / h, 4) for x in hoehen], "rahmen": round(rahmen, 3)}


def _eingabe_mb(befehl: list[str]) -> float | None:
    """Summe der Eingabedateien (-i) in MB – so viel bekäme ein anderer Rechner für diesen Render (Stufe 3, M142).
    Nur eine Messzahl: None statt eines Fehlers."""
    try:
        dateien = {Path(befehl[i + 1]) for i, arg in enumerate(befehl[:-1]) if arg == "-i"}
        return round(sum(d.stat().st_size for d in dateien if d.is_file()) / 1e6, 1)
    except OSError:
        return None


def _sidecar(ziel: Path, daten: dict) -> None:
    """`<name>.render.json` neben dem Video. Nur eine Hilfe für die Messung: ein Schreibfehler kostet das Video nicht."""
    try:
        ziel.with_suffix(".render.json").write_text(json.dumps(daten, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as exc:
        log.warning("Sidecar %s nicht geschrieben: %s", ziel.with_suffix(".render.json"), exc)


def normalisiere_ton(datei: Path, konfig: Konfig) -> dict:
    """Lautheit des fertigen Videos auf [regie.ton] lufs/true_peak (−14 LUFS / −1,5 dBTP), zwei Pässe (R1, 30.09.):
    Pass 1 misst (messung.loudnorm_messen), Pass 2 gleicht linear an – das Bild wird nur kopiert (-c:v copy), der Ton
    neu kodiert (AAC 160k, 48 kHz). LRA=20, damit loudnorm linear bleibt; fehlt die Reserve für den True Peak, fällt
    er in den dynamischen Modus – das steht dann als loudnorm_modus im Sidecar, der Limiter bleibt die Sicherung.
    Erst wenn die neue Datei so lang ist wie die alte (Bild ±0,05 s, Ton da), ersetzt sie datei.
    Rückgabe {"normiert": bool, "ton_gain_db": float | None, "loudnorm_modus": "linear" | "dynamic" | None,
    "ton_hinweis": str | None}. Kein Tonstrom, stille Spur (I = −inf) oder ein Fehler: datei bleibt unverändert,
    normiert False, der Grund in ton_hinweis. Wirft nicht."""
    t = ton_einstellungen(konfig)
    # AAC hebt den True Peak beim Neukodieren um bis zu ~1,4 dB (gemessen 30.09.) -> loudnorm zielt AAC_RESERVE_DB
    # tiefer, sonst landet das fertige Video über der Grenze von Tor G4 (−0,5 dBTP) und wird vom eigenen Renderer
    # aussortiert; [regie.ton] true_peak bleibt die Zahl, die im fertigen Video gelten soll
    ziel_i, ziel_tp = t["lufs"], t["true_peak"] - AAC_RESERVE_DB
    ergebnis = {"normiert": False, "ton_gain_db": None, "loudnorm_modus": None, "ton_hinweis": None}
    werte = messung.loudnorm_messen(datei, ziel_i, ziel_tp)
    if werte is None:
        ergebnis["ton_hinweis"] = "Lautheit nicht messbar (kein Tonstrom?)"
        return ergebnis
    gemessen = [werte.get(k) for k in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")]
    if not all(isinstance(x, float) and math.isfinite(x) for x in gemessen):
        ergebnis["ton_hinweis"] = f"Ton still (I = {werte.get('input_i')}) – nicht angeglichen"
        return ergebnis
    i, tp, lra, schwelle, versatz = gemessen
    neu = datei.with_name(datei.stem + ".ton" + datei.suffix)
    try:
        ton_alt, bild_alt = messung._stream_dauern(datei)
        if bild_alt is None:
            raise MedienFehler("Bildlänge nicht messbar")
        # loudnorm gibt den Ton in 0,1-s-Blöcken aus (hinten bis zu 0,1 s mehr) -> auf die alte Länge schneiden
        laenge = ton_alt if ton_alt is not None else bild_alt
        af = (f"loudnorm=I={ziel_i}:TP={ziel_tp}:LRA={messung.LOUDNORM_LRA}:measured_I={i}:measured_TP={tp}:"
              f"measured_LRA={lra}:measured_thresh={schwelle}:offset={versatz}:linear=true:print_format=json,"
              f"aresample=48000,atrim=0:{laenge:.3f}")
        befehl = ["ffmpeg", "-hide_banner", "-nostdin", "-nostats", "-y", "-i", str(datei), "-map", "0:v", "-map",
                  "0:a", "-c:v", "copy", "-af", af, "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(neu)]
        # medien.fuehre_aus (nicht der Name hier im Modul): wer den Render-Aufruf in Tests ersetzt, trifft nur ihn
        text = medien.fuehre_aus(befehl, f"Lautheit {datei.name}")
        ton_neu, bild_neu = messung._stream_dauern(neu)
        if bild_neu is None or ton_neu is None or abs(bild_neu - bild_alt) > 0.05 or abs(ton_neu - laenge) > 0.05:
            raise MedienFehler(f"Länge nach dem Angleichen: Bild {bild_neu} s, Ton {ton_neu} s "
                               f"(vorher {bild_alt} s / {ton_alt} s)")
    except (MedienFehler, ValueError) as exc:
        neu.unlink(missing_ok=True)
        ergebnis["ton_hinweis"] = f"Lautheit nicht angeglichen: {exc}"[:300]
        log.warning("%s: %s", datei.name, ergebnis["ton_hinweis"])
        return ergebnis
    zweiter = messung.loudnorm_json(text) or {}
    neu.replace(datei)
    if all(isinstance(zweiter.get(k), float) and math.isfinite(zweiter[k]) for k in ("output_i", "input_i")):
        gain = zweiter["output_i"] - zweiter["input_i"]
    else:
        gain = ziel_i - i
    return {"normiert": True, "ton_gain_db": round(gain, 2), "loudnorm_modus": zweiter.get("normalization_type"),
            "ton_hinweis": None}


def _pruefe_formatdauer(liste: dict, dauer_s: float | None = None) -> None:
    from . import regie

    try:
        regie.pruefe_dauer(liste["format"], float(liste["dauer_s"] if dauer_s is None else dauer_s))
    except (KeyError, ValueError, regie.RegieFehler) as exc:
        raise MedienFehler(f"Ungültige Videolänge: {exc}") from exc


def _pruefe_renderdauer(liste: dict, datei: Path, *, vollstaendig: bool = True) -> float:
    messung = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                              "stream=duration", "-of", "json", str(datei)],
                             capture_output=True, text=True, encoding="utf-8", check=False)
    try:
        wirklich = float(json.loads(messung.stdout)["streams"][0]["duration"])
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        raise MedienFehler(f"Videolänge von {datei.name} nicht messbar") from exc
    geplant = float(liste["dauer_s"])
    if messung.returncode or not abs(wirklich - geplant) <= max(0.05, 2 / float(liste["fps"])):
        raise MedienFehler(f"Render unvollständig: geplant {geplant:.3f} s, Video {wirklich:.3f} s")
    if vollstaendig:
        _pruefe_formatdauer(liste, wirklich)
    return wirklich


def _schrift(liste: dict, konfig: Konfig) -> Path | None:
    """Schrift für clip-battle.de (Short) und für Kill-Titel/Zähler – nur wenn etwas zu schreiben ist."""
    texte = any(e.art in ("titel", "zaehler") for e in effekte.zeitleiste(liste))
    if texte or (liste["format"] == "short" and liste.get("overlay")):
        return shorts.schrift(konfig)
    return None


def _zeichenbreite(konfig: Konfig) -> float:
    return float(effekte.einstellungen(konfig)[0]["titel_zeichenbreite"])


def graph_fehler(liste: dict, konfig: Konfig) -> list[str]:
    """Schon beim Planen (regie.erstelle): Passt der Filtergraph der Liste auf die Befehlszeile? Gerechnet mit dem
    größten Fall – volle Auflösung, zwei Tonspuren je Segment, Schrift und Musik wie beim Rendern."""
    b, h = liste["aufloesung"]
    try:
        schrift = _schrift(liste, konfig)
    except MedienFehler:  # keine Schrift gefunden: die Länge zählt, nicht die Datei
        schrift = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
    n = len(liste["segmente"])
    graph, _ = filtergraph(liste, [2] * n, b=b, h=h, musik_eingang=n if liste.get("musik") else None, schrift=schrift,
                           sfx_pegel=sfx.pegel(konfig), zeichenbreite=_zeichenbreite(konfig),
                           ton=ton_einstellungen(konfig))
    groesse = len(graph.encode())
    if groesse >= MAX_GRAPH:
        return [f"Filtergraph {groesse // 1000} KB – höchstens {MAX_GRAPH // 1000} KB (zu viele Effekte)"]
    return []


def _zeile(con: sqlite3.Connection, entwurf_id: int) -> sqlite3.Row:
    zeile = con.execute("SELECT * FROM entwuerfe WHERE id = ?", (entwurf_id,)).fetchone()
    if zeile is None:
        raise MedienFehler(f"Entwurf {entwurf_id} unbekannt")
    return zeile


def _max_bytes(konfig: Konfig) -> int:
    return int(float(konfig.wert("vorschau.max_mb", 48)) * 1_000_000)


def entwurf(con: sqlite3.Connection, konfig: Konfig, entwurf_id: int) -> dict:
    """Rendert den Entwurf eines compose-Laufs (idempotent) und merkt ihn in der Datenbank."""
    zeile = _zeile(con, entwurf_id)
    liste = json.loads(Path(zeile["schnittliste"]).read_text(encoding="utf-8"))
    _pruefe_formatdauer(liste)
    if zeile["datei"] and Path(zeile["datei"]).is_file():
        _pruefe_renderdauer(liste, Path(zeile["datei"]))
        return {"entwurf": entwurf_id, "datei": zeile["datei"], "uebersprungen": True}
    ziel = Path(zeile["schnittliste"]).with_suffix(".mp4")
    # Stems (nur mit Musik) in den Ordner der Cutter-Kritik, wo die Messung sie sucht (kritik-<id>/, Spec §3.2)
    stems = Path(zeile["schnittliste"]).parent / f"kritik-{entwurf_id}" / "stems.mka"
    ergebnis = rendere(liste, ziel, konfig, max_bytes=_max_bytes(konfig), vollstaendig=True, stems=stems)
    con.execute("UPDATE entwuerfe SET datei = ?, status = CASE WHEN status = 'neu' THEN 'gerendert' ELSE status END "
                "WHERE id = ?", (str(ziel), entwurf_id))
    return {"entwurf": entwurf_id, **ergebnis}


def messen(con: sqlite3.Connection, konfig: Konfig, entwurf_id: int) -> dict:
    """`render-entwurf <id> --messen`: rendert wie entwurf(), aber immer neu und in einen eigenen Temp-Ordner neben
    der Schnittliste (gleiche Platte wie der echte Entwurf), der danach wieder weg ist. Die Datenbank bleibt
    unverändert – so lässt sich die Renderzeit vor und nach einer Änderung vergleichen."""
    zeile = _zeile(con, entwurf_id)
    liste = json.loads(Path(zeile["schnittliste"]).read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix=".messen-", dir=Path(zeile["schnittliste"]).parent) as ordner:
        start = time.monotonic()
        ergebnis = rendere(liste, Path(ordner) / "messung.mp4", konfig, max_bytes=_max_bytes(konfig), vollstaendig=True)
        sekunden = time.monotonic() - start
    return {"entwurf": entwurf_id, "sekunden": round(sekunden, 1), "encoder": ergebnis["encoder"],
            "dauer_s": ergebnis["dauer_s"], "aufloesung": ergebnis["aufloesung"], "mb": ergebnis["mb"]}


# --- Final auf pve-big ---------------------------------------------------------------

def final_auftrag(con: sqlite3.Connection, konfig: Konfig, entwurf_id: int) -> Path:
    """Legt den Auftrag für pve-big an: Schnittliste mit Pfaden AUF pve-big (Speicher-Wurzel) plus Musik.

    Moment-Dateien liegen auf dem Mini evtl. als Material-Kopie -> für pve-big auf den Originalpfad im Speicher
    umschreiben. Die Musik wird in den Speicher kopiert (klein)."""
    zeile = _zeile(con, entwurf_id)
    liste = json.loads(Path(zeile["schnittliste"]).read_text(encoding="utf-8"))
    material_wurzel = Path(str(konfig.wert("material.ordner", "/var/lib/clip-pipeline/material")))
    for s in liste["segmente"]:
        datei = Path(s["datei"])
        if datei.is_relative_to(material_wurzel):
            s["datei"] = datei.relative_to(material_wurzel).as_posix()
            kopie = con.execute("SELECT art, von_s FROM material WHERE quelle = ?", (s["datei"],)).fetchone()
            if kopie and kopie["art"] == "video_ende":  # gekürzte Kopie -> Zeiten auf das Original umrechnen
                von = float(kopie["von_s"] or 0)
                for feld in ("quelle_start_s", "quelle_ende_s", "quelle_dauer_s"):
                    s[feld] = round(s[feld] + von, 3)
                s["muss"] = [round(s["muss"][0] + von, 3), round(s["muss"][1] + von, 3)]
        elif datei.is_relative_to(konfig.wurzel):
            s["datei"] = konfig.relativ(datei)
        else:
            raise MedienFehler(f"{datei} liegt weder im Speicher noch in der Material-Kopie")
    auftraege = konfig.wurzel / str(konfig.wert("regie.auftraege", "regie/auftraege"))
    auftraege.mkdir(parents=True, exist_ok=True)
    if m := liste.get("musik"):
        ziel_musik = konfig.wurzel / "regie" / "musik" / m["datei"]
        ziel_musik.parent.mkdir(parents=True, exist_ok=True)
        if not ziel_musik.is_file():
            shutil.copy2(musik.ordner(konfig) / m["datei"], ziel_musik)
    datei = auftraege / f"{liste['name']}.json"
    datei.write_text(json.dumps(liste, ensure_ascii=False, indent=2), encoding="utf-8")
    return datei


def _im_speicher(konfig: Konfig, relativ: str) -> Path:
    """Relativer Pfad, der nach dem Auflösen sicher in der Speicher-Wurzel bleibt."""
    wurzel = konfig.wurzel.resolve()
    if not relativ or relativ.startswith(("/", "\\")) or ".." in Path(relativ).parts:
        raise MedienFehler(f"Pfad im Auftrag nicht erlaubt: {relativ!r}")
    pfad = (wurzel / relativ).resolve()
    if not pfad.is_relative_to(wurzel):
        raise MedienFehler(f"Pfad im Auftrag zeigt aus dem Speicher: {relativ!r}")
    return pfad


def fuehre_final_aus(konfig: Konfig, auftrag: Path) -> dict:
    """Läuft AUF pve-big (clip-big-steuer final <name>). Der Auftrag liegt auf der Freigabe – also wie fremde
    Eingabe behandeln: Schema + fachliche Prüfung, Name passend zur Datei, alle Pfade innerhalb des Speichers."""
    from . import regie
    from .verarbeitung import SESSION_ID

    liste = json.loads(auftrag.read_text(encoding="utf-8"))
    if fehler := regie.pruefe_liste(liste):  # prüft u. a. Übergangs-Arten (feste Liste) und Musik-Pegel 0..1
        raise MedienFehler("Auftrag ungültig: " + "; ".join(fehler[:3]))
    if liste["name"] != auftrag.stem or not SESSION_ID.fullmatch(liste["name"]):
        raise MedienFehler("Auftrag: Name passt nicht zur Datei")
    for s in liste["segmente"]:
        s["datei"] = str(_im_speicher(konfig, s["datei"]))
    if m := liste.get("musik"):
        if Path(m["datei"]).name != m["datei"]:
            raise MedienFehler(f"Musik-Dateiname nicht erlaubt: {m['datei']!r}")
    konfig.daten.setdefault("musik", {})["ordner"] = str(konfig.wurzel / "regie" / "musik")
    ziel = konfig.wurzel / "regie" / "final" / f"{liste['name']}.mp4"
    return rendere(liste, ziel, konfig, final=True)


def nvenc_verfuegbar() -> bool:
    try:
        text = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, check=False).stdout
    except OSError:
        return False
    return "h264_nvenc" in text


def final_auf_big(con: sqlite3.Connection, konfig: Konfig, entwurf_id: int) -> dict:
    """Ganzer Final-Weg vom Mini aus: Auftrag anlegen, pve-big (einmal) wecken, dort mit NVENC rendern,
    danach sofort herunterfahren. Weckt nur, wenn das Herunterfahren gesichert ist (big.wach_halten)."""
    from . import big

    if konfig.getrennt:
        # E19: Auftrag und neue Clips lägen im Puffer, pve-big rendert aber aus seinem Speicher (dem Lager) –
        # dort kämen sie nie an. Also gar nicht erst wecken (Ziel 2: tagsüber weckt nur noch der Abgleich).
        raise KonfigFehler("render-entwurf --final geht im Puffer-Betrieb ([lager].wurzel gesetzt) noch nicht: "
                           "pve-big sieht nur das Lager, Auftrag und Clips liegen im Puffer. pve-big wurde nicht "
                           f"geweckt. Den Entwurf auf dem Mini rendern: pipeline render-entwurf {entwurf_id}")
    minuten = float(konfig.wert("regie.final_halten_min", 60))
    with big.wach_halten(konfig, "final", f"Final-Render Entwurf {entwurf_id}", minuten=minuten):
        auftrag = final_auftrag(con, konfig, entwurf_id)
        antwort = big._fern(konfig, f"final {auftrag.stem}", timeout=minuten * 60)
    zeilen = [z for z in antwort.strip().splitlines() if z.strip()]
    ergebnis = json.loads(zeilen[-1]) if zeilen else {}
    ziel = konfig.relativ(konfig.wurzel / "regie" / "final" / f"{auftrag.stem}.mp4")
    con.execute("UPDATE entwuerfe SET final_datei = ? WHERE id = ?", (ziel, entwurf_id))
    return {"entwurf": entwurf_id, "final": ziel, **ergebnis}


# --- Upload-Fassung (Lernschleife „Publikum“, Spec §10.4) ------------------------------------

class ZuGross(MedienFehler):
    """rendere(): Die fertige Datei liegt über max_bytes. kbit = die benutzte Videorate (kbit/s) – damit kann
    upload_fassung gezielt mit weniger Rate neu rendern. Bleibt ein MedienFehler: Wer nur MedienFehler fängt
    (Lern-Bot, CLI), merkt keinen Unterschied. Steht hier unten statt oben, damit der Abschnitt beisammen bleibt
    (Python sucht den Namen erst beim Aufruf von rendere)."""

    def __init__(self, text: str, kbit: int):
        super().__init__(text)
        self.kbit = kbit


# Dateiname der Upload-Fassung im Export-Ordner <wurzel>/<[publikum].upload_ordner>/<name>/ (Export-Vertrag mit
# Regisseur 2.0, Plan Stufe 1: genau eine Datei „Fertig-Video“ je Entwurf, R2.0 Stufe 3 legt Einzelclips daneben)
UPLOAD_DATEI = "{name}_upload.mp4"
# So oft wird neu gerendert, wenn die Datei zu groß ist; jedes Mal mit 75 % der zuletzt benutzten Rate (wie
# shorts.rendere). Drei Versuche: 100 % → 75 % → 56 % – reicht auch für einen VA-API-Encoder, der überschießt.
UPLOAD_VERSUCHE = 3
UPLOAD_CRF = 20  # Spec §10.4: sichtbar besser als der Entwurf (crf 23), die Datei darf größer sein


def upload_ziel(konfig: Konfig, entwurf: sqlite3.Row) -> Path:
    """Wo die Upload-Fassung liegt: <speicher.wurzel>/<[publikum].upload_ordner>/<name>/<UPLOAD_DATEI>, z. B.
    /srv/clips/export/short-20260925-201500/short-20260925-201500_upload.mp4 (im Puffer-Betrieb ist /srv/clips der
    Puffer /srv/puffer). Derselbe Ordner, in den Regisseur 2.0 (Stufe 3) seinen Export legt (CLAUDE.md
    „Export“, Annahme A4) – so gibt es genau ein Fertig-Video je Entwurf. Rechnet nur, legt nichts an.

    KonfigFehler, wenn [publikum].upload_ordner fehlt, leer ist oder aus der Wurzel hinauszeigt (absoluter Pfad
    oder „..“) – die Upload-Fassung gehört in den Puffer, nie ins Lager auf pve-big."""
    ordner = str(konfig.wert("publikum.upload_ordner") or "").strip()
    # Fachliche Prüfung der Konfig: ein absoluter Pfad würde die Wurzel beim Zusammensetzen ersetzen
    # (Path("/srv/clips") / "/srv/big" == Path("/srv/big")), „..“ führte aus ihr heraus
    if not ordner or Path(ordner).anchor or ".." in Path(ordner).parts:
        raise KonfigFehler(f"[publikum].upload_ordner muss ein Ordner relativ zu [speicher].wurzel sein "
                           f"(z. B. \"export\"), nicht {ordner!r}")
    name = entwurf["name"]
    return konfig.wurzel / ordner / name / UPLOAD_DATEI.format(name=name)


def upload_fassung(con: sqlite3.Connection, konfig: Konfig, entwurf_id: int) -> dict:
    """Rendert die Upload-Fassung eines Entwurfs: volle Auflösung aus der Schnittliste, auf dem
    Mini mit VA-API, sonst CPU – über rendere(..., volle_aufloesung=True, crf=UPLOAD_CRF, kbit_max=…), also
    derselbe Filtergraph und dieselbe Budget-Rechnung wie beim Entwurf (keine zweite Fassung). Nie NVENC, nie
    pve-big, kein Wecken.

    Größe: Budget aus [vorschau].max_mb (48 MB, Telegram-Grenze für Bots) – Spec §10.4 nennt „bei 45 s ≈ 8,5 Mbit/s“,
    das ist die Gesamtrate 48 MB · 8 / 45 s. rendere() zieht davon 12 % Reserve und 160 kbit/s Ton ab, fürs Bild
    bleiben so ≈ 7,3 Mbit/s. kbit_max beim ersten Versuch: [shorts].max_kbit (12000) – das Budget begrenzt also.
    libx264 bekommt crf 20 plus diesen Deckel; VA-API kennt kein crf und bekommt die Rate als -b:v (rendere).
    Ist die Datei trotzdem zu groß (ZuGross), neu mit kbit_max = 0,75 · ZuGross.kbit, max_bytes bleibt gleich;
    nach UPLOAD_VERSUCHE Versuchen MedienFehler. Andere MedienFehler (ffmpeg, Datei fehlt) werden nicht wiederholt.

    Regeln (fachliche Prüfungen, vor dem Rendern):
      - Short 30–75 s oder Zusammenschnitt 75–120 s; geprüft vor und nach dem Rendern
      - nur im getrennten Betrieb (konfig.getrennt, E19): sonst ist die Wurzel das Lager auf pve-big (NFS) und
        der Lern-Bot würde dort schreiben oder hängen → KonfigFehler mit Klartext (wie cli._cmd_lager)
      - alle Moment-Dateien und die Musik vorhanden – sonst MedienFehler „Moment-Datei fehlt … (der Puffer hält
        Rohvideos 14 Tage)“, ohne dass ffmpeg startet
    max_bytes wie entwurf(): _max_bytes(konfig) = int([vorschau].max_mb · 1 000 000) – dieselbe Funktion, also eine
    einzige Stelle für die Telegram-Grenze.
    Effekte (Regisseur 2.0, liste["effekte"].an): gehen ohne Zusatz mit – rendere() rechnet Zoom, Blenden und die
    Lage der Kill-Texte aus b × h (hier 1080×1920) und der gemessenen Höhe des Spielbilds; die Texte bleiben im
    unscharfen Rand, nie im Spielbild (effekt_filter.lage).

    Idempotent über entwuerfe.upload_pfad (absoluter Pfad wie entwuerfe.datei): liegt die Datei schon da, wird
    nicht neu gerendert. Die Pipeline-Sperre holt der Aufrufer (Lern-Bot bzw. CLI). Rückgabe wie rendere() plus
    "entwurf" und "uebersprungen", z. B. {"entwurf": 41, "datei": "…_upload.mp4", "mb": 31.2, "dauer_s": 38.5,
    "encoder": "h264_vaapi", "aufloesung": [1080, 1920], "versuche": 1, "uebersprungen": False}.
    Übersprungen: nur {"entwurf", "datei", "uebersprungen": True} (wie entwurf())."""
    zeile = _zeile(con, entwurf_id)  # MedienFehler „Entwurf … unbekannt“, wenn es ihn nicht gibt

    # Beide vollständigen Formate liefern Publikumssignale; das Größenbudget skaliert mit der Dauer.
    if zeile["format"] not in ("short", "zusammenschnitt"):
        raise MedienFehler(f"Unbekanntes Upload-Format {zeile['format']!r}")
    # Regel 2: nur im getrennten Betrieb (E19). Sonst wäre [speicher].wurzel das Lager auf pve-big: der Lern-Bot
    # schriebe über NFS dorthin (oder hinge am schlafenden Mount). pruefe_getrennt(mit_lager=False) sieht zusätzlich
    # nach, ob die Wurzel wirklich der Puffer ist (Marke .clip-puffer) – nur stat(), das Lager bleibt unberührt.
    if not konfig.getrennt:
        raise KonfigFehler("Upload-Fassung nur im getrennten Betrieb ([lager].wurzel gesetzt, E19): sonst läge "
                           "[speicher].wurzel auf pve-big, und dort schreibt der Lern-Bot nicht. Nichts geweckt.")
    konfig.pruefe_getrennt(mit_lager=False)

    ziel = upload_ziel(konfig, zeile)
    liste = json.loads(Path(zeile["schnittliste"]).read_text(encoding="utf-8"))
    _pruefe_formatdauer(liste)
    # Idempotent: schon gerendert und die Datei ist noch da → nichts tun
    if zeile["upload_pfad"] and Path(zeile["upload_pfad"]).is_file():
        _pruefe_renderdauer(liste, Path(zeile["upload_pfad"]))
        return {"entwurf": entwurf_id, "datei": zeile["upload_pfad"], "uebersprungen": True}

    # Regel 3: alles Material da? rendere() prüft das auch, und zwar vor ffmpeg, aber Datei für Datei (für die
    # vorderen Segmente laufen vorher schon ffprobe-Aufrufe) und nur mit dem Pfad. Hier prüfen wir vorab alle Dateien,
    # ohne ein einziges ffprobe, und die Meldung nennt den Moment und den Grund: Der Puffer hält Rohvideos
    # [puffer].rohdaten_tage (14) Tage, ältere Momente liegen nur noch im Lager.
    tage = konfig.wert("puffer.rohdaten_tage", 14)
    for s in liste["segmente"]:
        if not Path(s["datei"]).is_file():
            raise MedienFehler(f"Moment-Datei fehlt: {s['datei']} (Moment {s['moment']}) – der Puffer hält "
                               f"Rohvideos {tage} Tage, ältere Momente liegen nur noch im Lager")
    if m := liste.get("musik"):
        if not (musik.ordner(konfig) / m["datei"]).is_file():
            raise MedienFehler(f"Musik fehlt: {musik.ordner(konfig) / m['datei']}")

    max_bytes = _max_bytes(konfig)  # wie entwurf(): Telegram-Grenze für Bots
    kbit_max = int(konfig.wert("shorts.max_kbit", 12000))  # erster Versuch: das Budget begrenzt, nicht dieser Deckel
    for versuch in range(1, UPLOAD_VERSUCHE + 1):
        try:
            ergebnis = rendere(liste, ziel, konfig, max_bytes=max_bytes, volle_aufloesung=True, crf=UPLOAD_CRF,
                               kbit_max=kbit_max, vollstaendig=True)
        except ZuGross as fehler:
            # 0,75: nächster Versuch mit drei Vierteln der zuletzt BENUTZTEN Rate (wie shorts.rendere) – die
            # Grenze max_bytes bleibt, nur die Rate sinkt
            kbit_max = int(0.75 * fehler.kbit)
            continue
        con.execute("UPDATE entwuerfe SET upload_pfad = ? WHERE id = ?", (str(ziel), entwurf_id))
        return {"entwurf": entwurf_id, **ergebnis, "versuche": versuch, "uebersprungen": False}
    raise MedienFehler(f"Upload-Fassung von Entwurf #{entwurf_id} bleibt nach {UPLOAD_VERSUCHE} Versuchen über "
                       f"{max_bytes // 1_000_000} MB")
