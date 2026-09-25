"""Die vier Schritte einer Session – Vertrag mit n8n (siehe CLAUDE.md, "Schnittstelle zu n8n").

  prepare --session ID   Replay finden, neue Aufnahmen erfassen, Session-Ordner anlegen
  analyze --session ID   Replay lesen -> Kills -> Kandidaten -> passende Aufnahme  (analyse.json)
  decide  --session ID   claude -p justiert Schnitt + Beschreibung, Prüfung per Schema (schnittliste.json)
  render  --session ID   schneiden, Lautstärke messen, Punkte, Vorschau, Datenbank

Jeder Schritt ist idempotent: Fertiges wird übersprungen, nichts geht kaputt.
Session-ID = Zeitstempel des Replays, z. B. 2026-09-23_20-15-33.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import sqlite3
import subprocess
from datetime import timedelta
from pathlib import Path

from . import caption, db, erfassung, lernen, medien, replay, schema, schnittliste, vorbewertung, zeitleiste
from .konfig import Konfig
from .zeit import aus_iso, iso, jetzt, utc_zu_lokal

log = logging.getLogger("pipeline")
SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
ZEITSTEMPEL_ID = re.compile(r"^(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})$")
RAND = timedelta(minutes=5)


class SessionFehler(RuntimeError):
    pass


def pruefe_id(sid: str) -> str:
    """Zweite Prüfung zusätzlich zu n8n: nur harmlose Zeichen, damit daraus sicher ein Ordnername wird."""
    if not isinstance(sid, str) or not SESSION_ID.fullmatch(sid):
        raise SessionFehler(f"Ungültige Session-ID {sid!r}")
    return sid


def ordner(konfig: Konfig, sid: str) -> Path:
    return konfig.ordner("sessions") / pruefe_id(sid)


def _schreibe_json(pfad: Path, daten: dict) -> None:
    tmp = pfad.with_suffix(".tmp")
    tmp.write_text(json.dumps(daten, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(pfad)  # atomar: nie eine halb geschriebene Datei


def _lies_json(pfad: Path, fehlt: str) -> dict:
    if not pfad.is_file():
        raise SessionFehler(fehlt)
    return json.loads(pfad.read_text(encoding="utf-8"))


def _replay_fuer(konfig: Konfig, sid: str) -> Path | None:
    ordner_replays = konfig.ordner("replays")
    if treffer := ZEITSTEMPEL_ID.fullmatch(sid):
        j, mo, t, h, mi, s = treffer.groups()
        kandidat = ordner_replays / f"UnsavedReplay-{j}.{mo}.{t}-{h}.{mi}.{s}.replay"
        if kandidat.is_file():
            return kandidat
    return next((p for p in sorted(ordner_replays.glob("*.replay")) if replay.match_id(p) == sid), None)


# --- prepare --------------------------------------------------------------------

def prepare(con: sqlite3.Connection, konfig: Konfig, sid: str) -> dict:
    pruefe_id(sid)
    konfig.pruefe_speicher()
    neu = erfassung.erfasse_aufnahmen(con, konfig)
    pfad = _replay_fuer(konfig, sid)
    if pfad is None:
        raise SessionFehler(f"Kein Replay für Session {sid} in {konfig.ordner('replays')}")
    relativ = konfig.relativ(pfad)
    if db.match(con, sid) is None:
        start, ende = erfassung.match_zeiten(pfad, konfig.wert("zeit.zeitzone", "Europe/Berlin"))  # wie scan
        zeit = iso(jetzt())
        con.execute(
            "INSERT OR IGNORE INTO matches (id, replay_pfad, start_utc, ende_utc, erstellt, geaendert) VALUES (?, ?, ?, ?, ?, ?)",
            (sid, relativ, iso(start), iso(ende), zeit, zeit),
        )
    ordner(konfig, sid).mkdir(parents=True, exist_ok=True)
    zeile = db.match(con, sid)
    im_match = erfassung.aufnahmen_im_zeitraum(con, aus_iso(zeile["start_utc"]) - RAND, aus_iso(zeile["ende_utc"]) + RAND)
    return {"session": sid, "replay": relativ, "aufnahmen_neu": neu.get("neu", 0), "aufnahmen_im_match": len(im_match)}


# --- analyze --------------------------------------------------------------------

def analyze(con: sqlite3.Connection, konfig: Konfig, sid: str) -> dict:
    konfig.pruefe_speicher()
    zeile = db.match(con, pruefe_id(sid))
    if zeile is None:
        raise SessionFehler(f"Session {sid} unbekannt – erst prepare")
    arbeitsordner = ordner(konfig, sid)
    arbeitsordner.mkdir(parents=True, exist_ok=True)

    warnungen: list[str] = []
    match = None
    start, ende = aus_iso(zeile["start_utc"]), aus_iso(zeile["ende_utc"])
    try:
        match, roh = replay.lies(konfig.absolut(zeile["replay_pfad"]), konfig)
        _schreibe_json(arbeitsordner / "replay.json", roh)
        start, ende = match.start_utc, match.ende_utc
    except replay.ReplayFehler as e:
        warnungen.append(str(e))

    aufnahmen = erfassung.aufnahmen_im_zeitraum(con, start - RAND, ende + RAND)
    zl = zeitleiste.baue(match, aufnahmen, start, ende)
    warnungen += zl.warnungen
    einstellungen = konfig.abschnitt("vorbewertung")
    _, gewichte = lernen.aktuelle(con, konfig)
    prioritaet = list(konfig.wert("auswahl.prioritaet", []))

    kandidaten, ohne_video = [], []
    for k in vorbewertung.kandidaten(zl, einstellungen):
        s = schnittliste.waehle_aufnahme(k, aufnahmen, prioritaet)
        if s is None:
            ohne_video.append({"nr": k.nr, "titel": k.titel, "kill_zeiten_utc": [iso(z) for z in k.kill_zeiten]})
            continue
        k.merkmale["laenge"] = vorbewertung.laenge_merkmal(s.dauer_s, float(einstellungen["laenge_frei_s"]))
        k.punkte, k.begruendung = vorbewertung.bewerte(k.merkmale, gewichte, k.titel)
        kandidaten.append({
            "nr": k.nr, "titel": k.titel, "typ": k.typ, "kills": k.kills, "max_gruppe": k.max_gruppe,
            "victory_royale": k.victory_royale,
            "kill_zeiten_utc": [iso(z) for z in k.kill_zeiten],
            "kill_sekunden": [round(s.aufnahme.sekunde(z), 2) for z in k.kill_zeiten],
            "serie_sekunden": serie_sekunden({"kill_zeiten_utc": [iso(z) for z in k.kill_zeiten]},
                                             float(einstellungen["multikill_fenster_s"])),
            "aufnahme": s.aufnahme.pfad, "quelle": s.aufnahme.quelle, "abdeckung": s.abdeckung,
            "vorschlag": {"start_s": s.start_s, "ende_s": s.ende_s},
            "grenzen": {"min_start_s": 0.0, "max_ende_s": round(s.aufnahme.dauer_s, 3)},
            "merkmale": k.merkmale, "punkte": k.punkte, "begruendung": k.begruendung,
        })
    if ohne_video:
        warnungen.append(f"{len(ohne_video)} Kandidat(en) ohne passende Aufnahme")

    analyse = {
        "version": 1, "session": sid,
        "match": {
            "replay": zeile["replay_pfad"], "build": match.build if match else None, "platzierung": zl.platzierung,
            "victory_royale": zl.victory_royale, "kills": len(zl.kills), "kill_quelle": zl.quelle,
        },
        "warnungen": warnungen, "kandidaten": kandidaten, "ohne_video": ohne_video,
    }
    _schreibe_json(arbeitsordner / "analyse.json", analyse)
    with db.transaktion(con):
        con.execute(
            """UPDATE matches SET start_utc = ?, ende_utc = ?, kill_quelle = ?, platzierung = ?, victory_royale = ?,
                                  kills = ?, build = ?, hinweise = ?, geaendert = ? WHERE id = ?""",
            (iso(start), iso(ende), zl.quelle, zl.platzierung, int(zl.victory_royale), len(zl.kills),
             match.build if match else None, "\n".join(warnungen) or None, iso(jetzt()), sid),
        )
    return {"session": sid, "kills": len(zl.kills), "kandidaten": len(kandidaten), "kill_quelle": zl.quelle, "warnungen": warnungen}


# --- decide ---------------------------------------------------------------------

AUFTRAG = """Du bist Cutter für Fortnite-Shorts. Lies mit dem Read-Tool die Datei analyse.json im aktuellen Ordner.
Sie enthält Kill-Kandidaten mit Vorschlag (start_s/ende_s, Sekunden in der Aufnahme), Grenzen und Kill-Sekunden.
Lege für JEDEN Kandidaten den Schnitt fest: kurz vor der Action beginnen, kurz nach dem letzten Kill enden.
Regeln: start_s >= grenzen.min_start_s und start_s <= erste kill_sekunden - 2; ende_s <= grenzen.max_ende_s und
ende_s >= letzte kill_sekunden + 1; Dauer 5 bis 60 Sekunden.
Optional "beschreibung": deutsch, max. 120 Zeichen, höchstens 1 Emoji, NUR aus den Fakten (max_gruppe als
Anzahl Kills, typ, serie_sekunden, victory_royale, match.platzierung, match.kills). Keine Waffen, Orte, Namen
und keine anderen Zahlen erfinden.
Optional "grund": ein kurzer Satz zu deiner Entscheidung.
Antworte ausschließlich mit JSON in dieser Form:
{"clips": [{"nr": 1, "start_s": 12.5, "ende_s": 31.0, "beschreibung": "...", "grund": "..."}]}"""


def _json_aus_text(text: str) -> dict | None:
    anfang, ende = text.find("{"), text.rfind("}")
    if anfang < 0 or ende <= anfang:
        return None
    try:
        return json.loads(text[anfang : ende + 1])
    except json.JSONDecodeError:
        return None


def frage_claude(konfig: Konfig, arbeitsordner: Path) -> tuple[dict[int, dict] | None, str | None]:
    """Fragt claude -p (Max-Abo, nur Leserechte). Gibt ({nr: wahl} oder None, Hinweis) zurück."""
    programm = shutil.which(str(konfig.wert("decide.programm", "claude")))
    if not programm:
        return None, "claude nicht gefunden – Regel-Schnittliste"
    try:
        ergebnis = subprocess.run(
            [programm, "-p", "--output-format", "json", "--allowedTools", "Read", AUFTRAG],
            cwd=arbeitsordner, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=float(konfig.wert("decide.timeout_s", 180)), check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"claude nicht nutzbar ({type(e).__name__}) – Regel-Schnittliste"
    if ergebnis.returncode != 0:
        return None, f"claude Exit {ergebnis.returncode} – Regel-Schnittliste"
    try:
        huelle = json.loads(ergebnis.stdout)
    except json.JSONDecodeError:
        return None, "claude-Ausgabe kein JSON – Regel-Schnittliste"
    if huelle.get("is_error"):
        return None, "claude meldet Fehler (Limit?) – Regel-Schnittliste"
    antwort = _json_aus_text(str(huelle.get("result", "")))
    if antwort is None:
        return None, "claude-Antwort ohne JSON – Regel-Schnittliste"
    if fehler := schema.pruefe(antwort, schema.lade("entscheidung")):
        return None, f"claude-Antwort verletzt Schema ({fehler[0]}) – Regel-Schnittliste"
    return {int(c["nr"]): c for c in antwort["clips"]}, None


def serie_sekunden(kandidat: dict, fenster_s: float) -> int:
    """Dauer der größten Kill-Serie in ganzen Sekunden (mindestens 1) – ein erlaubter Fakt für Beschreibungen."""
    zeiten = [aus_iso(z) for z in kandidat["kill_zeiten_utc"]]
    serie = max(vorbewertung.gruppiere(zeiten, fenster_s), key=len)
    return max(1, round((serie[-1] - serie[0]).total_seconds()))


def pruefe_wahl(wahl: dict, kandidat: dict) -> str | None:
    """Fachliche Prüfung eines Schnitts – das Schema allein kennt die Kill-Zeiten nicht."""
    start, ende = float(wahl["start_s"]), float(wahl["ende_s"])
    erster, letzter = kandidat["kill_sekunden"][0], kandidat["kill_sekunden"][-1]
    if start < kandidat["grenzen"]["min_start_s"] or ende > kandidat["grenzen"]["max_ende_s"] + 0.01:
        return "außerhalb der Aufnahme"
    if start > erster - 2 + 0.01 or ende < letzter + 1 - 0.01:
        return "schneidet einen Kill ab"
    if not 5 <= ende - start <= 60:
        return "Dauer nicht 5–60 s"
    return None


def decide(con: sqlite3.Connection, konfig: Konfig, sid: str) -> dict:
    arbeitsordner = ordner(konfig, sid)
    analyse_pfad, ziel = arbeitsordner / "analyse.json", arbeitsordner / "schnittliste.json"
    analyse = _lies_json(analyse_pfad, f"Session {sid}: analyse.json fehlt – erst analyze")
    if ziel.is_file() and ziel.stat().st_mtime >= analyse_pfad.stat().st_mtime:
        vorhanden = json.loads(ziel.read_text(encoding="utf-8"))
        return {"session": sid, "clips": len(vorhanden["clips"]), "entschieden_von": vorhanden["entschieden_von"], "uebersprungen": True}

    hinweise: list[str] = []
    antwort = None
    if analyse["kandidaten"] and konfig.wert("decide.claude", True):
        antwort, hinweis = frage_claude(konfig, arbeitsordner)
        if hinweis:
            hinweise.append(hinweis)
    version, gewichte = lernen.aktuelle(con, konfig)
    frei = float(konfig.wert("vorbewertung.laenge_frei_s", 30))
    fenster = float(konfig.wert("vorbewertung.multikill_fenster_s", 10))

    clips = []
    for k in analyse["kandidaten"]:
        start, ende = k["vorschlag"]["start_s"], k["vorschlag"]["ende_s"]
        beschreibung = grund = None
        wahl = (antwort or {}).get(k["nr"])
        if wahl:
            if fehler := pruefe_wahl(wahl, k):
                hinweise.append(f"Clip {k['nr']}: Claude-Schnitt verworfen ({fehler})")
            else:
                start, ende, grund = round(float(wahl["start_s"]), 3), round(float(wahl["ende_s"]), 3), wahl.get("grund")
            text = (wahl.get("beschreibung") or "").strip()
            fakten = {"kills": k["max_gruppe"], "sekunden": serie_sekunden(k, fenster),
                      "platzierung": analyse["match"]["platzierung"], "kills_match": analyse["match"]["kills"]}
            if text and caption.pruefe_ki_text(text, fakten, 150):
                beschreibung = text
            elif text:
                hinweise.append(f"Clip {k['nr']}: Beschreibung verworfen (erfundene Zahl oder zu lang)")
        merkmale = dict(k["merkmale"], laenge=vorbewertung.laenge_merkmal(ende - start, frei))
        punkte, begruendung = vorbewertung.bewerte(merkmale, gewichte, k["titel"])
        clips.append({
            **{f: k[f] for f in ("nr", "titel", "typ", "kills", "max_gruppe", "victory_royale", "kill_zeiten_utc",
                                 "kill_sekunden", "aufnahme", "quelle", "abdeckung")},
            "start_s": start, "ende_s": ende,
            "min_start_s": k["grenzen"]["min_start_s"], "max_ende_s": k["grenzen"]["max_ende_s"],
            "merkmale": merkmale, "punkte": punkte, "begruendung": begruendung,
            "beschreibung": beschreibung, "grund": grund,
        })

    liste = {
        "version": 2, "session": sid, "entschieden_von": "claude" if antwort is not None else "regel",
        "gewichte_version": version, "match": analyse["match"], "warnungen": analyse["warnungen"] + hinweise,
        "clips": clips, "ohne_video": analyse["ohne_video"],
    }
    if fehler := schema.pruefe(liste, schema.lade("schnittliste")):
        raise SessionFehler("Schnittliste verletzt das Schema: " + "; ".join(fehler[:3]))
    _schreibe_json(ziel, liste)
    return {"session": sid, "clips": len(clips), "entschieden_von": liste["entschieden_von"], "hinweise": hinweise}


# --- render ---------------------------------------------------------------------

def _lautstaerke(con: sqlite3.Connection, konfig: Konfig, aufnahme, clip_datei: Path, hinweise: list[str]) -> float:
    """0..1: Wie weit liegt die lauteste Stelle im Clip über dem Durchschnitt der ganzen Aufnahme?"""
    if aufnahme.tonspuren == 0:
        return 0.0
    try:
        referenz = aufnahme.lautheit_i
        if referenz is None:
            referenz, _ = medien.lautheit(konfig.absolut(aufnahme.pfad))
            con.execute("UPDATE aufnahmen SET lautheit_i = ? WHERE pfad = ?", (referenz, aufnahme.pfad))
        _, spitze = medien.lautheit(clip_datei)
    except medien.MedienFehler as e:
        hinweise.append(f"Lautstärke nicht messbar: {str(e)[:120]}")
        return 0.0
    spanne = float(konfig.wert("vorbewertung.lautstaerke_spanne_lu", 15.0))
    return round(min(1.0, max(0.0, (spitze - referenz) / spanne)), 2)


def render(con: sqlite3.Connection, konfig: Konfig, sid: str) -> dict:
    konfig.pruefe_speicher()
    arbeitsordner = ordner(konfig, sid)
    liste = _lies_json(arbeitsordner / "schnittliste.json", f"Session {sid}: schnittliste.json fehlt – erst decide")
    if db.match(con, sid) is None:
        raise SessionFehler(f"Session {sid} unbekannt – erst prepare")
    version, gewichte = lernen.aktuelle(con, konfig)
    hinweise: list[str] = []
    neu = 0
    for c in liste["clips"]:
        if con.execute("SELECT 1 FROM clips WHERE match_id = ? AND nr = ?", (sid, c["nr"])).fetchone():
            continue  # schon fertig (idempotent)
        aufnahme = erfassung.aufnahme_nach_pfad(con, c["aufnahme"])
        if aufnahme is None:
            hinweise.append(f"Clip {c['nr']}: Aufnahme {c['aufnahme']} nicht mehr vorhanden")
            continue
        dauer = c["ende_s"] - c["start_s"]
        clip_datei = arbeitsordner / "clips" / f"{c['nr']:03d}_{c['typ']}_{c['kills']}k.mp4"
        medien.schneide(
            konfig.absolut(aufnahme.pfad), c["start_s"], dauer, clip_datei, fps=aufnahme.fps,
            encoder=str(konfig.wert("schnitt.encoder", "libx264")), crf=int(konfig.wert("schnitt.crf", 18)),
            vaapi_geraet=str(konfig.wert("schnitt.vaapi_geraet", "/dev/dri/renderD128")),
        )
        merkmale = dict(c["merkmale"], lautstaerke=_lautstaerke(con, konfig, aufnahme, clip_datei, hinweise))
        punkte, begruendung = vorbewertung.bewerte(merkmale, gewichte, c["titel"])
        vorschau_datei = arbeitsordner / "vorschau" / f"{c['nr']:03d}.mp4"
        medien.vorschau(
            clip_datei, vorschau_datei,
            max_bytes=int(float(konfig.wert("vorschau.max_mb", 48)) * 1_000_000),
            kurze_seite=int(konfig.wert("vorschau.kurze_seite", 720)),
        )
        start_utc = aufnahme.start_utc + timedelta(seconds=c["start_s"])
        zeit = iso(jetzt())
        with db.transaktion(con):
            cursor = con.execute(
                """INSERT INTO clips (match_id, nr, status, titel, typ, kills, max_gruppe, victory_royale, kill_zeiten,
                                      start_utc, ende_utc, quelle_pfad, quelle_start_s, quelle_ende_s, merkmale, punkte,
                                      begruendung, beschreibung, gewichte_version, clip_pfad, vorschau_pfad,
                                      elo, elo_rd, erstellt, geaendert)
                   VALUES (?, ?, 'vorbewertet', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    sid, c["nr"], c["titel"], c["typ"], c["kills"], c["max_gruppe"], int(c["victory_royale"]),
                    json.dumps(c["kill_zeiten_utc"]), iso(start_utc), iso(start_utc + timedelta(seconds=dauer)),
                    aufnahme.pfad, c["start_s"], c["ende_s"], json.dumps(merkmale), punkte, begruendung,
                    c.get("beschreibung"), version, konfig.relativ(clip_datei), konfig.relativ(vorschau_datei),
                    float(konfig.wert("elo.start", 1500.0)), float(konfig.wert("elo.unsicherheit_start", 350.0)), zeit, zeit,
                ),
            )
            db.protokoll(con, "clip", f"{c['titel']}: {punkte} Punkte", clip_id=cursor.lastrowid, match_id=sid)
        neu += 1

    kills_ohne_video = sum(len(k["kill_zeiten_utc"]) for k in liste.get("ohne_video", []))
    warnung = (
        f"{kills_ohne_video} Kill(s) ohne Aufnahme – liefen Nvidia Highlights und SteelSeries Moments?"
        if kills_ohne_video else None
    )
    with db.transaktion(con):
        con.execute("UPDATE matches SET status = 'verarbeitet', geaendert = ? WHERE id = ?", (iso(jetzt()), sid))
        db.protokoll(con, "match", f"render: {neu} neue Clips", match_id=sid)
        match_ende = aus_iso(db.match(con, sid)["ende_utc"])
        # Nur für frische Matches melden – beim Nachholen alter Matches wäre das nur Rauschen
        if warnung and jetzt() - match_ende < timedelta(hours=12):
            zeit = utc_zu_lokal(match_ende, konfig.wert("zeit.zeitzone", "Europe/Berlin"))
            db.meldung(con, f"ohne_video:{sid}", f"⚠️ Match bis {zeit:%H:%M} Uhr: {warnung}")
    zeilen = con.execute("SELECT titel, punkte FROM clips WHERE match_id = ? ORDER BY punkte DESC, nr", (sid,)).fetchall()
    return {
        "session": sid, "clips": len(zeilen), "neu": neu,
        "top_label": zeilen[0]["titel"] if zeilen else None,
        "top_score": zeilen[0]["punkte"] if zeilen else 0,
        "ohne_video": kills_ohne_video, "warnung": warnung,
        "hinweise": hinweise,
    }


def process(con: sqlite3.Connection, konfig: Konfig, sid: str) -> dict:
    """Alle vier Schritte hintereinander (für Handbetrieb und `scan --verarbeiten`)."""
    ergebnis = {"prepare": prepare(con, konfig, sid), "analyze": analyze(con, konfig, sid)}
    ergebnis["decide"] = decide(con, konfig, sid)
    ergebnis["render"] = render(con, konfig, sid)
    return {"session": sid, **{k: v for k, v in ergebnis["render"].items() if k != "session"}, "schritte": ergebnis}
