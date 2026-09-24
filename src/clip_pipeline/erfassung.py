"""`pipeline scan`: neue Aufnahmen erfassen und fertige Matches (Replays) finden.

Jede Datei wird nur einmal mit ffprobe untersucht; danach merkt sich die Datenbank
Größe und Änderungszeit. Dateien, die gerade noch kopiert werden (zu jung), bleiben
bis zum nächsten Lauf liegen.
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import quellen, replay
from .konfig import Konfig
from .medien import MedienFehler, probe
from .zeit import UTC, aus_iso, iso, jetzt


def _zeile_zu_aufnahme(z: sqlite3.Row) -> quellen.Aufnahme:
    return quellen.Aufnahme(
        pfad=z["pfad"],
        quelle=z["quelle"],
        start_utc=aus_iso(z["start_utc"]),
        ende_utc=aus_iso(z["ende_utc"]),
        dauer_s=float(z["dauer_s"]),
        tonspuren=int(z["tonspuren"]),
        fps=float(z["fps"] or 0),
        ereignisse=[quellen.Ereignis.aus_dict(e) for e in json.loads(z["ereignisse"])],
        lautheit_i=z["lautheit_i"],
    )


def aufnahme_nach_pfad(con: sqlite3.Connection, pfad: str) -> quellen.Aufnahme | None:
    zeile = con.execute("SELECT * FROM aufnahmen WHERE pfad = ?", (pfad,)).fetchone()
    return _zeile_zu_aufnahme(zeile) if zeile else None


def aufnahmen_im_zeitraum(con: sqlite3.Connection, start: datetime, ende: datetime) -> list[quellen.Aufnahme]:
    zeilen = con.execute(
        "SELECT * FROM aufnahmen WHERE start_utc <= ? AND ende_utc >= ? ORDER BY start_utc",
        (iso(ende), iso(start)),
    ).fetchall()
    return [_zeile_zu_aufnahme(z) for z in zeilen]


def erfasse_aufnahmen(con: sqlite3.Connection, konfig: Konfig) -> dict:
    eingang = konfig.ordner("eingang")
    ruhezeit = float(konfig.wert("quellen.ruhezeit_s", 60))
    zone = konfig.wert("zeit.zeitzone", "Europe/Berlin")
    bekannt = {z["pfad"]: (z["groesse"], z["geaendert"]) for z in con.execute("SELECT pfad, groesse, geaendert FROM aufnahmen")}
    zaehler = {"neu": 0, "zu_jung": 0, "fehler": 0}
    fehler: list[str] = []
    if not eingang.is_dir():
        return {**zaehler, "hinweis": f"Ordner {eingang} fehlt"}

    for datei in sorted(eingang.rglob("*")):
        if not datei.is_file() or not quellen.ist_kandidat(datei):
            continue
        stat = datei.stat()
        if time.time() - stat.st_mtime < ruhezeit:
            zaehler["zu_jung"] += 1
            continue
        relativ = konfig.relativ(datei)
        geaendert = round(stat.st_mtime, 3)
        if bekannt.get(relativ) == (stat.st_size, geaendert):
            continue
        try:
            info = probe(datei)
        except MedienFehler as e:
            zaehler["fehler"] += 1
            fehler.append(str(e)[:200])
            continue
        aufnahme = quellen.aufnahme_aus(
            relativ,
            quellen.erkenne(datei.name),
            info,
            zonen_name=zone,
            versatz_nvidia_s=float(konfig.wert("quellen.nvidia.versatz_s", 0.0)),
            nachlauf_nvidia_s=float(konfig.wert("quellen.nvidia.nachlauf_s", 4.0)),
            versatz_steelseries_s=float(konfig.wert("quellen.steelseries.versatz_s", 0.0)),
        )
        con.execute(
            """INSERT INTO aufnahmen (pfad, groesse, geaendert, quelle, start_utc, ende_utc, dauer_s,
                                      tonspuren, fps, ereignisse, erfasst)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (pfad) DO UPDATE SET
                   groesse = excluded.groesse, geaendert = excluded.geaendert, quelle = excluded.quelle,
                   start_utc = excluded.start_utc, ende_utc = excluded.ende_utc, dauer_s = excluded.dauer_s,
                   tonspuren = excluded.tonspuren, fps = excluded.fps, ereignisse = excluded.ereignisse,
                   lautheit_i = NULL, erfasst = excluded.erfasst""",
            (
                relativ, stat.st_size, geaendert, aufnahme.quelle, iso(aufnahme.start_utc), iso(aufnahme.ende_utc),
                aufnahme.dauer_s, aufnahme.tonspuren, aufnahme.fps,
                json.dumps([e.als_dict() for e in aufnahme.ereignisse]), iso(jetzt()),
            ),
        )
        zaehler["neu"] += 1
    return {**zaehler, "fehlerliste": fehler[:10]} if fehler else zaehler


def match_zeiten(datei: Path, zonen_name: str) -> tuple[datetime, datetime]:
    """Vorläufige (start, ende) eines Replays: Ende = letzte Änderung (Fortnite schreibt bis zum Match-Ende),
    nie jetzt() – scan/prepare können Stunden später laufen. Start aus dem Dateinamen, sonst 30 min davor."""
    ende = datetime.fromtimestamp(datei.stat().st_mtime, UTC)
    return replay.startzeit_aus_name(datei, zonen_name) or ende - timedelta(minutes=30), ende


def erfasse_replays(con: sqlite3.Connection, konfig: Konfig) -> list[str]:
    """Legt für jedes fertige Replay ein Match an (Zeiten vorläufig, genau erst nach dem Auslesen)."""
    ordner = konfig.ordner("replays")
    ruhezeit = float(konfig.wert("replay.ruhezeit_s", 120))
    zone = konfig.wert("zeit.zeitzone", "Europe/Berlin")
    neu: list[str] = []
    if not ordner.is_dir():
        return neu
    for datei in sorted(ordner.glob("*.replay")):
        relativ = konfig.relativ(datei)
        if con.execute("SELECT 1 FROM matches WHERE replay_pfad = ?", (relativ,)).fetchone():
            continue
        stat = datei.stat()
        if time.time() - stat.st_mtime < ruhezeit:
            continue  # Fortnite schreibt noch -> Match läuft noch
        start, ende = match_zeiten(datei, zone)
        mid = replay.match_id(datei)
        if con.execute("SELECT 1 FROM matches WHERE id = ?", (mid,)).fetchone():
            mid = f"{mid}_{int(stat.st_mtime)}"
        zeit = iso(jetzt())
        con.execute(
            "INSERT INTO matches (id, replay_pfad, start_utc, ende_utc, erstellt, geaendert) VALUES (?, ?, ?, ?, ?, ?)",
            (mid, relativ, iso(start), iso(ende), zeit, zeit),
        )
        neu.append(mid)
    return neu


def scan(con: sqlite3.Connection, konfig: Konfig) -> dict:
    konfig.pruefe_speicher()
    aufnahmen = erfasse_aufnahmen(con, konfig)
    neue = erfasse_replays(con, konfig)
    offen = [z["id"] for z in con.execute("SELECT id FROM matches WHERE status = 'neu' ORDER BY start_utc")]
    return {"speicher": "ok", "aufnahmen": aufnahmen, "neue_matches": neue, "offen": offen}
