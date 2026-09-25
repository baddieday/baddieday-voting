"""Stimmung je Moment (`pipeline stimmung`): episch · lustig · spannend · frustriert · chill.

Merkmale (alles nachvollziehbar, in momente.merkmale gespeichert):
  - Lautstärke-Verlauf (EBU R128, alle 0,1 s) der Spiel- und der Mikro-Spur
      spitzen  = wie oft das Spiel deutlich über seinem eigenen Mittel liegt (Schüsse, Explosionen)
      jubel_laut = wie oft das Mikro laut wird (Schreien, Jubeln)
      energie  = 0..1, wie laut es insgesamt ist
  - Transkript der Mikro-Spur (faster-whisper "small", Deutsch) -> Wörter für Lachen, Jubel, Frust
  - aus dem Replay: Kills, größte Serie, Victory Royale, selbst umgehauen, Tod
  - kill_sekunden / aktion_sekunden (Vertrag mit regie.py und nachschnitt.py): Sekunden in der Moment-Datei,
    aktion_sekunden parallel zu kill_sekunden (gleiche Länge und Reihenfolge = nach Kill sortiert) = mein
    Umhauen je Kill; nicht monoton, darf < 0 sein (Aktion vor Dateibeginn). Fehlt der Schlüssel: alter Moment.
  - nachschnitt: Moment wurde neu aus der Quellaufnahme geschnitten (`pipeline momente nachschneiden`).
    Dann gelten datei, start_s/ende_s und start_utc der momente-Zeile, nicht die des Bot-Clips.

Stimmung = Punkte-Regeln (siehe punkte()). Die höchste Summe gewinnt; "sicherheit" sagt, wie knapp es war.
Nur die unsicheren Momente gehen gesammelt in EINEN Aufruf von `claude -p` (nur Leserechte). Dessen Antwort
wird geprüft: nur bekannte Momente, nur die fünf erlaubten Wörter – sonst gilt die Regel.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import sqlite3
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median

from . import bestand, material, replay
from .konfig import Konfig
from .medien import MedienFehler, fuehre_aus, probe
from .verarbeitung import _json_aus_text
from .zeit import aus_iso, iso, jetzt

log = logging.getLogger("pipeline")
STIMMUNGEN = ("episch", "lustig", "spannend", "frustriert", "chill")
NACHSCHNITT = "nachschnitt"  # Schlüssel in momente.merkmale (dict); wird bei jedem Lauf unverändert übernommen

# Wortlisten (klein geschrieben, ganze Wörter; "*" = beliebige Fortsetzung)
LACHEN = ("haha*", "hehe*", "hihi*", "lol", "lustig", "[lachen]", "(lachen)", "(lacht)", "*lacht*", "xd")
JUBEL = ("jaa*", "yes", "geil", "boom", "letsgo", "let's", "gg", "ez", "easy", "sieg", "victory", "weg",
         "hab", "krass", "brutal", "wahnsinn", "digga", "läuft")
FRUST = ("nein", "nee*", "scheiße", "scheisse", "fuck", "ernsthaft", "unfair", "lag", "boah", "mann", "junge",
         "warum", "wieso", "tot", "gestorben", "verdammt", "mist")


@dataclass
class Moment:
    schluessel: str
    datei: Path
    start_s: float
    ende_s: float
    start_utc: datetime | None = None
    clip_id: int | None = None
    match_id: str | None = None
    kills: int = 0
    max_gruppe: int = 0
    victory_royale: bool = False
    ereignisse: list[tuple[float, str]] = field(default_factory=list)  # (Sekunde im Moment, kill|knock_erlitten|tod)
    aktion_sekunden: list[float] | None = None  # parallel zu den Kills; None = ohne Replay (alter Stand)
    nachschnitt: dict | None = None  # Eintrag aus merkmale.nachschnitt, falls neu geschnitten


def kill_und_aktion_sekunden(eigene: list[replay.MeinEreignis], start_utc: datetime,
                             dauer_s: float) -> tuple[list[float], list[float]]:
    """Kills, deren Kill-Zeit im Fenster liegt, nach Kill sortiert – als Sekunden ab start_utc.

    Zweite Liste parallel dazu: die Aktion (mein Umhauen) je Kill; sie darf vor dem Fenster liegen (< 0).
    """
    ende = start_utc + timedelta(seconds=dauer_s)
    paare = sorted((e.zeit_utc, e.aktion) for e in eigene if e.art == "kill" and start_utc <= e.zeit_utc <= ende)
    return ([(k - start_utc).total_seconds() for k, _ in paare],
            [(a - start_utc).total_seconds() for _, a in paare])


# --- Momente finden -----------------------------------------------------------------

def _eigene_ereignisse(konfig: Konfig, match_id: str, cache: dict) -> list[replay.MeinEreignis]:
    """Ereignisse aus sessions/<id>/replay.json (lokale Kopie bevorzugt)."""
    if match_id not in cache:
        cache[match_id] = []
        rel = f"{konfig.wert('speicher.sessions', 'sessions')}/{match_id}/replay.json"
        pfad = material.lokal(konfig, rel)
        if pfad.is_file():
            try:
                roh = json.loads(pfad.read_text(encoding="utf-8"))
                m = replay.match_aus_json(roh, match_id, zonen_name=konfig.wert("zeit.zeitzone", "Europe/Berlin"),
                                          start_ist_ortszeit=bool(konfig.wert("zeit.replay_start_ist_ortszeit", True)))
                cache[match_id] = m.ereignisse
            except (json.JSONDecodeError, replay.ReplayFehler, ValueError) as e:
                log.warning("replay.json %s: %s", match_id, e)
    return cache[match_id]


def _nachgeschnittene(con: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    """momente-Zeilen mit merkmale.nachschnitt (neu geschnittene Datei) – nach Schlüssel."""
    ergebnis = {}
    for z in con.execute("SELECT * FROM momente WHERE merkmale LIKE ?", (f'%"{NACHSCHNITT}"%',)):
        try:
            mk = json.loads(z["merkmale"])
        except json.JSONDecodeError:
            continue
        if isinstance(mk, dict) and isinstance(mk.get(NACHSCHNITT), dict):
            ergebnis[z["schluessel"]] = z
    return ergebnis


def momente_aus_clips(con: sqlite3.Connection, konfig: Konfig) -> list[Moment]:
    ergebnis, cache = [], {}
    nachgeschnitten = _nachgeschnittene(con)
    # Beste zuerst (freigegebene, dann nach Punkten) – verworfene braucht der Regisseur nie
    for c in con.execute(
        """SELECT * FROM clips WHERE clip_pfad IS NOT NULL AND status != 'verworfen'
            ORDER BY CASE WHEN status IN ('freigegeben', 'veroeffentlicht', 'im_highlight') THEN 0 ELSE 1 END,
                     punkte DESC, id""").fetchall():
        schluessel = f"clip:{c['id']}"
        datei = material.lokal(konfig, c["clip_pfad"])
        start = aus_iso(c["start_utc"])
        von_s, dauer = 0.0, float(c["quelle_ende_s"]) - float(c["quelle_start_s"])
        nachschnitt = None
        if (zeile := nachgeschnitten.get(schluessel)) is not None:
            if Path(zeile["datei"]).is_file():
                # Neu geschnittene Datei behalten – sonst würde `stimmung --neu` den Nachschnitt zurücksetzen.
                # start_utc der Zeile = Zeitpunkt von start_s in der neuen Datei.
                datei, von_s = Path(zeile["datei"]), float(zeile["start_s"])
                dauer = float(zeile["ende_s"]) - von_s
                start = aus_iso(zeile["start_utc"]) if zeile["start_utc"] else start
                nachschnitt = json.loads(zeile["merkmale"])[NACHSCHNITT]
            else:
                log.warning("Nachschnitt %s fehlt (%s) – nehme den Bot-Clip", schluessel, zeile["datei"])
        m = Moment(schluessel, datei, von_s, von_s + dauer, start, c["id"], c["match_id"], int(c["kills"]),
                   int(c["max_gruppe"]), bool(c["victory_royale"]), nachschnitt=nachschnitt)
        eigene = _eigene_ereignisse(konfig, c["match_id"], cache)
        if eigene:
            m.ereignisse = [(von_s + (e.zeit_utc - start).total_seconds(), e.art) for e in eigene
                            if start <= e.zeit_utc <= start + timedelta(seconds=dauer)]
            _, aktionen = kill_und_aktion_sekunden(eigene, start, dauer)
            m.aktion_sekunden = [von_s + a for a in aktionen]
        else:  # ohne Replay: Kill-Zeiten aus der Datenbank
            m.ereignisse = [(von_s + (aus_iso(z) - start).total_seconds(), "kill") for z in json.loads(c["kill_zeiten"])]
        ergebnis.append(m)
    return ergebnis


def momente_aus_dateien(con: sqlite3.Connection, konfig: Konfig, max_s: float = 120) -> list[Moment]:
    """Kurze Rohvideos (Instant-Replays) aus der lokalen Kopie, die noch keinen Clip haben."""
    benutzt = {z["quelle_pfad"] for z in con.execute("SELECT DISTINCT quelle_pfad FROM clips")}
    wurzel = material.ordner(konfig) / str(konfig.wert("speicher.eingang", "eingang"))
    ergebnis = []
    for datei in sorted(wurzel.rglob("*")) if wurzel.is_dir() else []:
        if datei.suffix.lower() not in bestand.VIDEO_ENDUNGEN or ".teil" in datei.name:
            continue
        rel = datei.relative_to(material.ordner(konfig)).as_posix()
        if rel in benutzt:
            continue
        try:
            dauer = probe(datei).dauer_s
        except MedienFehler:
            continue
        if dauer <= max_s:
            zeile = con.execute("SELECT start_utc FROM aufnahmen WHERE pfad = ?", (rel,)).fetchone()
            start = aus_iso(zeile["start_utc"]) if zeile else None
            # Gekürzte Kopie (nur die letzten Sekunden): beginnt von_s nach dem Original
            kopie = con.execute("SELECT art, von_s FROM material WHERE quelle = ?", (rel,)).fetchone()
            if start is not None and kopie and kopie["art"] == "video_ende":
                start += timedelta(seconds=float(kopie["von_s"] or 0))
            ergebnis.append(Moment(f"datei:{rel}", datei, 0.0, dauer, start))
    return ergebnis


# --- Audio ----------------------------------------------------------------------------

_VERLAUF = re.compile(r"\bt:\s*(\d+(?:\.\d+)?)\s.*?\bM:\s*(-?\d+(?:\.\d+)?)")


def lautheit_verlauf(datei: Path, spur: int) -> list[tuple[float, float]]:
    """(Sekunde, Momentan-Lautheit in LUFS) alle 0,1 s."""
    text = fuehre_aus(["ffmpeg", "-hide_banner", "-nostdin", "-i", str(datei), "-map", f"0:a:{spur}",
                       "-af", "ebur128=framelog=info", "-f", "null", "-"], f"Lautheit-Verlauf {datei.name}")
    return [(float(t), float(m)) for t, m in _VERLAUF.findall(text)]


def _ausbrueche(verlauf: list[tuple[float, float]], ueber_lu: float, min_s: float = 0.3) -> list[float]:
    """Zeitpunkte, an denen die Lautheit mindestens min_s lang ueber_lu über dem Median liegt."""
    werte = [m for _, m in verlauf if m > -70]
    if len(werte) < 5:
        return []
    grenze = median(werte) + ueber_lu
    treffer, beginn = [], None
    for t, m in verlauf:
        if m >= grenze:
            beginn = t if beginn is None else beginn
        else:
            if beginn is not None and t - beginn >= min_s:
                treffer.append(round(beginn, 1))
            beginn = None
    if beginn is not None and verlauf and verlauf[-1][0] - beginn >= min_s:
        treffer.append(round(beginn, 1))
    return treffer


def energie(verlauf: list[tuple[float, float]]) -> float:
    """0 bei -50 LUFS und leiser, 1 bei -10 LUFS und lauter (Mittel der hörbaren Werte)."""
    werte = [m for _, m in verlauf if m > -70]
    if not werte:
        return 0.0
    return round(min(1.0, max(0.0, (sum(werte) / len(werte) + 50) / 40)), 2)


def mikro_als_wav(datei: Path, spur: int, ziel: Path) -> Path:
    fuehre_aus(["ffmpeg", "-hide_banner", "-nostdin", "-y", "-i", str(datei), "-map", f"0:a:{spur}",
                "-ac", "1", "-ar", "16000", str(ziel)], f"Mikro-Spur {datei.name}")
    return ziel


# --- Sprache --------------------------------------------------------------------------

class Transkription:
    """faster-whisper, einmal geladen für den ganzen Durchlauf (das Modell laden dauert)."""

    def __init__(self, konfig: Konfig):
        self.konfig = konfig
        self._modell = None

    def verfuegbar(self) -> bool:
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            return False
        return True

    def text(self, wav: Path) -> str:
        if self._modell is None:
            from faster_whisper import WhisperModel

            s = self.konfig.abschnitt("stimmung")
            self._modell = WhisperModel(str(s.get("modell", "small")), device=str(s.get("geraet", "cpu")),
                                        compute_type=str(s.get("rechenart", "int8")), cpu_threads=int(s.get("threads", 6)))
        abschnitte, _ = self._modell.transcribe(str(wav), language=str(self.konfig.wert("stimmung.sprache", "de")),
                                                vad_filter=True, beam_size=1)
        return " ".join(a.text.strip() for a in abschnitte).strip()


def _passt(wort: str, muster: str) -> bool:
    """"haha*" = beginnt mit, "*lacht*" = enthält, sonst genau gleich."""
    if muster.startswith("*") and muster.endswith("*"):
        return muster.strip("*") in wort
    if muster.endswith("*"):
        return wort.startswith(muster[:-1])
    return wort == muster


def woerter(text: str) -> dict[str, int]:
    """Zählt Lachen/Jubel/Frust-Wörter. "Hahaha" zählt als Lachen, "Jaaa!" als Jubel."""
    klein = text.lower().replace("let's go", "letsgo").replace("lets go", "letsgo")
    liste = re.findall(r"[\[(]?[a-zäöüß']+[\])]?", klein)
    return {name: sum(1 for w in liste if any(_passt(w, m) for m in muster))
            for name, muster in (("lachen", LACHEN), ("jubel", JUBEL), ("frust", FRUST))}


# --- Regeln ---------------------------------------------------------------------------

def punkte(mk: dict) -> dict[str, float]:
    """Punkte je Stimmung aus den Merkmalen. Bewusst einfach – jede Zeile ist eine Regel."""
    kills, serie, tod = mk.get("kills", 0), mk.get("max_gruppe", 0), mk.get("tod", 0)
    lachen, jubel, frust = mk.get("lachen", 0), mk.get("jubel", 0), mk.get("frust", 0)
    spitzen, laut, e = mk.get("spitzen", 0), mk.get("jubel_laut", 0), mk.get("energie", 0.0)
    return {
        "episch": 4.0 * (serie >= 3) + 1.0 * (serie >= 4) + 3.5 * mk.get("victory_royale", 0) + 1.0 * (serie == 2)
                  + 1.0 * min(jubel + laut, 2) * (kills > 0) - 2.0 * tod,
        "lustig": 2.5 * min(lachen, 2) + 0.5 * (lachen > 0 and (tod or frust > 0)),
        "spannend": 0.8 * min(kills, 2) + 0.5 * min(spitzen, 4) + 1.0 * mk.get("umgehauen", 0) * (not tod),
        "frustriert": 3.0 * tod + 1.0 * min(frust, 3) - 1.5 * (lachen >= 2),
        "chill": 1.5 * (kills == 0 and spitzen <= 1 and not tod) + 1.0 * (e < 0.35) - 0.5 * min(frust, 2),
    }


def entscheide(mk: dict) -> tuple[str, float, dict[str, float]]:
    p = punkte(mk)
    rangfolge = sorted(STIMMUNGEN, key=lambda s: (-p[s], STIMMUNGEN.index(s)))
    beste, zweite = p[rangfolge[0]], p[rangfolge[1]]
    sicherheit = 0.0 if beste <= 0 else round(min(1.0, (beste - max(zweite, 0)) / beste), 2)
    return rangfolge[0], sicherheit, {k: round(v, 2) for k, v in p.items()}


def merkmale(m: Moment, konfig: Konfig, sprache: Transkription | None) -> tuple[dict, str | None]:
    mk: dict = {
        "kills": m.kills or sum(1 for _, a in m.ereignisse if a == "kill"),
        "max_gruppe": m.max_gruppe, "victory_royale": int(m.victory_royale),
        "tod": int(any(a == "tod" for _, a in m.ereignisse)),
        "umgehauen": int(any(a == "knock_erlitten" for _, a in m.ereignisse)),
        "kill_sekunden": [round(t, 1) for t, a in m.ereignisse if a == "kill"],
        "tod_sekunde": next((round(t, 1) for t, a in m.ereignisse if a == "tod"), None),
        "dauer_s": round(m.ende_s - m.start_s, 1),
    }
    if m.aktion_sekunden is not None:
        mk["aktion_sekunden"] = [round(t, 1) for t in m.aktion_sekunden]  # parallel zu kill_sekunden
    if m.nachschnitt is not None:
        mk[NACHSCHNITT] = m.nachschnitt
    text = None
    try:
        info = bestand.tonspuren(m.datei)
    except MedienFehler as e:
        mk["fehler"] = str(e)[:120]
        return mk, None
    spur_mikro = bestand.mikro_spur(info["spuren"])
    mk["mikro_spur"] = spur_mikro
    if info["spuren"]:
        spiel = lautheit_verlauf(m.datei, 0)
        mk["spitzen_s"] = _ausbrueche(spiel, float(konfig.wert("stimmung.spitze_lu", 8)))
        mk["spitzen"] = len(mk["spitzen_s"])
        mk["energie"] = energie(spiel)
    if spur_mikro is not None:
        mikro = lautheit_verlauf(m.datei, spur_mikro)
        mk["jubel_laut_s"] = _ausbrueche(mikro, float(konfig.wert("stimmung.jubel_lu", 12)), min_s=0.5)
        mk["jubel_laut"] = len(mk["jubel_laut_s"])
        if sprache is not None:
            with tempfile.TemporaryDirectory() as tmp:
                text = sprache.text(mikro_als_wav(m.datei, spur_mikro, Path(tmp) / "mikro.wav"))
            mk.update(woerter(text))
    return mk, text


# --- Claude (höchstens ein Aufruf) --------------------------------------------------------

AUFTRAG = """Lies mit dem Read-Tool die Datei momente.json im aktuellen Ordner. Jeder Eintrag ist ein Moment aus
einem Fortnite-Video mit Merkmalen (kills, max_gruppe, tod, umgehauen, spitzen, jubel_laut, energie, lachen, jubel,
frust), dem Transkript meiner Mikro-Spur ("text") und dem Vorschlag der Regeln ("regel", "punkte").
Wähle für JEDEN Moment genau eine Stimmung aus: episch, lustig, spannend, frustriert, chill.
Stütze dich nur auf diese Daten. Antworte ausschließlich mit JSON:
{"momente": [{"id": "clip:1", "stimmung": "lustig"}]}"""


def frage_claude(konfig: Konfig, kandidaten: list[dict]) -> tuple[dict[str, str], str | None]:
    programm = shutil.which(str(konfig.wert("decide.programm", "claude")))
    if not programm:
        return {}, "claude nicht gefunden – Regeln gelten"
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "momente.json").write_text(json.dumps(kandidaten, ensure_ascii=False, indent=1), encoding="utf-8")
        try:
            lauf = subprocess.run([programm, "-p", "--output-format", "json", "--allowedTools", "Read", AUFTRAG],
                                  cwd=tmp, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  timeout=float(konfig.wert("stimmung.claude_timeout_s", 180)), check=False)
        except (OSError, subprocess.TimeoutExpired) as e:
            return {}, f"claude nicht nutzbar ({type(e).__name__}) – Regeln gelten"
    if lauf.returncode != 0:
        return {}, f"claude Exit {lauf.returncode} – Regeln gelten"
    try:
        huelle = json.loads(lauf.stdout)
    except json.JSONDecodeError:
        return {}, "claude-Ausgabe kein JSON – Regeln gelten"
    antwort = _json_aus_text(str(huelle.get("result", ""))) if not huelle.get("is_error") else None
    if not isinstance(antwort, dict) or not isinstance(antwort.get("momente"), list):
        return {}, "claude-Antwort unbrauchbar – Regeln gelten"
    erlaubt = {k["id"] for k in kandidaten}
    gueltig = {}
    for eintrag in antwort["momente"]:
        if isinstance(eintrag, dict) and eintrag.get("id") in erlaubt and eintrag.get("stimmung") in STIMMUNGEN:
            gueltig[eintrag["id"]] = eintrag["stimmung"]
    verworfen = len(antwort["momente"]) - len(gueltig)
    return gueltig, (f"{verworfen} Claude-Antwort(en) verworfen" if verworfen else None)


# --- Durchlauf ------------------------------------------------------------------------

def _speichere(con, m: Moment, stimmung: str, sicherheit: float, quelle: str, mk: dict, text: str | None) -> None:
    zeit = iso(jetzt())
    con.execute(
        """INSERT INTO momente (schluessel, clip_id, match_id, datei, start_s, ende_s, start_utc, kills, stimmung,
                                sicherheit, quelle, merkmale, text, erstellt, geaendert)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (schluessel) DO UPDATE SET datei = excluded.datei, start_s = excluded.start_s,
               ende_s = excluded.ende_s, start_utc = excluded.start_utc, kills = excluded.kills,
               stimmung = excluded.stimmung,
               sicherheit = excluded.sicherheit, quelle = excluded.quelle, merkmale = excluded.merkmale,
               text = excluded.text, geaendert = excluded.geaendert""",
        (m.schluessel, m.clip_id, m.match_id, str(m.datei), m.start_s, m.ende_s,
         iso(m.start_utc) if m.start_utc else None, mk.get("kills", 0), stimmung, sicherheit, quelle,
         json.dumps(mk, ensure_ascii=False), text, zeit, zeit),
    )


def analysiere(con: sqlite3.Connection, konfig: Konfig, *, dateien: bool = False, neu: bool = False,
               claude: bool = True, whisper: bool = True, maximal: int | None = None) -> dict:
    momente = momente_aus_clips(con, konfig) + (momente_aus_dateien(con, konfig) if dateien else [])
    fertig = {z["schluessel"] for z in con.execute("SELECT schluessel FROM momente")}
    offen = [m for m in momente if neu or m.schluessel not in fertig]
    fehlend = [m for m in offen if not m.datei.is_file()]
    offen = [m for m in offen if m.datei.is_file()]
    if maximal is not None:
        offen = offen[:maximal]  # die besten zuerst; der Rest beim nächsten Lauf
    sprache = Transkription(konfig) if whisper else None
    hinweise = []
    if sprache is not None and not sprache.verfuegbar():
        hinweise.append("faster-whisper nicht installiert – ohne Transkript")
        sprache = None

    ergebnisse = []
    for m in offen:
        log.info("Stimmung %s", m.schluessel)
        mk, text = merkmale(m, konfig, sprache)
        stimmung, sicherheit, p = entscheide(mk)
        ergebnisse.append((m, mk, text, stimmung, sicherheit, p))

    # Genau ein Claude-Aufruf für die unsicheren Momente
    grenze = float(konfig.wert("stimmung.unsicher_unter", 0.35))
    unsicher = [e for e in ergebnisse if e[4] < grenze][: int(konfig.wert("stimmung.claude_max", 40))]
    claude_wahl: dict[str, str] = {}
    if claude and unsicher and konfig.wert("stimmung.claude", True):
        kandidaten = [{"id": m.schluessel, "regel": s, "punkte": p, "text": (t or "")[:400],
                       **{k: v for k, v in mk.items() if not k.endswith("_s") and k != NACHSCHNITT}}
                      for m, mk, t, s, _, p in unsicher]
        claude_wahl, hinweis = frage_claude(konfig, kandidaten)
        if hinweis:
            hinweise.append(hinweis)

    zaehler = {s: 0 for s in STIMMUNGEN}
    for m, mk, text, stimmung, sicherheit, p in ergebnisse:
        mk["punkte"] = p
        quelle = "regel"
        if (wahl := claude_wahl.get(m.schluessel)) is not None:
            mk["regel"] = stimmung
            stimmung, quelle = wahl, "claude"
        _speichere(con, m, stimmung, sicherheit, quelle, mk, text)
        zaehler[stimmung] += 1
    return {"momente": len(momente), "analysiert": len(ergebnisse), "fehlende_dateien": len(fehlend),
            "stimmungen": zaehler, "claude": len(claude_wahl), "hinweise": hinweise}
