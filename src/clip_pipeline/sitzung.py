"""Session vorbei (`pipeline sitzungen`, Timer alle 10 min): Gaming-PC meldet das Ende eines Spielabends.

Der Windows-Helfer schreibt sitzungen/session_<zeit>.json (Match-IDs des Abends) auf den Speicher. Hier:
  1. neue Dateien lesen – pve-big wird dafür NICHT geweckt (schläft er, nächster Versuch beim nächsten Timer)
  2. warten, bis n8n alle Matches verarbeitet hat (höchstens `warten_h`, danach mit Hinweis weiter)
  3. Stimmung für neue Momente, dann ein Short nur aus den Momenten dieses Abends -> der Lern-Bot schickt ihn
Die Datei selbst bleibt liegen (sie ist klein; verarbeitet wird über die Tabelle `sitzungen`).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import timedelta

from . import entwurf, regie, regie_lernen, stimmung
from .konfig import Konfig
from .verarbeitung import SESSION_ID
from .zeit import aus_iso, iso, jetzt

log = logging.getLogger("pipeline")


def verarbeite(con: sqlite3.Connection, konfig: Konfig, *, claude: bool = True, whisper: bool = True) -> dict:
    konfig.pruefe_speicher()  # wirft SpeicherOffline -> Exit 3, kein Wecken
    ordner = konfig.wurzel / str(konfig.wert("sitzungen.ordner", "sitzungen"))
    fertig = {z["name"] for z in con.execute("SELECT name FROM sitzungen")}
    ergebnis: dict = {"neu": [], "wartet": [], "fehler": []}
    for datei in sorted(ordner.glob("session_*.json")) if ordner.is_dir() else []:
        name = datei.stem
        if name in fertig or not SESSION_ID.fullmatch(name):
            continue
        try:
            daten = json.loads(datei.read_text(encoding="utf-8-sig"))  # Windows schreibt mit BOM
            matches = [m for m in daten.get("matches", []) if isinstance(m, str) and SESSION_ID.fullmatch(m)]
            ende = aus_iso(daten["ende_utc"]) if daten.get("ende_utc") else jetzt()
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            ergebnis["fehler"].append(f"{name}: {e}")
            continue
        status = {z["id"]: z["status"] for z in con.execute(
            f"SELECT id, status FROM matches WHERE id IN ({','.join('?' for _ in matches) or 'NULL'})", matches)}
        offen = [m for m in matches if status.get(m) != "verarbeitet"]
        hinweis = None
        if offen:
            if jetzt() - ende < timedelta(hours=float(konfig.wert("sitzungen.warten_h", 2))):
                ergebnis["wartet"].append({"sitzung": name, "offen": offen})
                continue
            hinweis = f"{len(offen)} Match(es) nicht verarbeitet: {', '.join(offen[:5])}"
        stimmung.analysiere(con, konfig, claude=claude, whisper=whisper)
        entwurf_id = None
        try:
            parameter, ziel = regie_lernen.aktuelle(con, konfig)
            e = regie.erstelle(con, konfig, str(konfig.wert("sitzungen.format", "short")), parameter=parameter,
                               ziel=ziel, nur_matches=set(matches))
            entwurf.entwurf(con, konfig, e["entwurf"])
            entwurf_id = e["entwurf"]
        except regie.RegieFehler as fehler:
            hinweis = "; ".join(filter(None, [hinweis, str(fehler)]))
        con.execute(
            """INSERT INTO sitzungen (name, matches, ende_utc, entwurf_id, hinweis, verarbeitet) VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT (name) DO NOTHING""",
            (name, json.dumps(matches), iso(ende), entwurf_id, hinweis, iso(jetzt())),
        )
        ergebnis["neu"].append({"sitzung": name, "matches": len(matches), "entwurf": entwurf_id, "hinweis": hinweis})
    return ergebnis
