"""Gemeinsame Test-Hilfen: temporärer Speicher, Test-Datenbank, künstliche Videos."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from clip_pipeline import db, konfig
from clip_pipeline.zeit import UTC, iso

HAT_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


class MitSpeicher(unittest.TestCase):
    """Legt pro Test einen leeren Speicher (mit Markierung) und eine eigene Datenbank an."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.konfig = konfig.lade()
        self.konfig.daten["speicher"]["wurzel"] = str(self.tmp / "speicher")
        # Nie einen echten Host oder Mount berühren – auch wenn config/lokal.toml Werte setzt
        self.konfig.daten["speicher"].update(host="", wol_mac="")
        self.konfig.daten.setdefault("lager", {})["wurzel"] = ""
        self.konfig.daten["datenbank"]["pfad"] = str(self.tmp / "test.db")
        # Tests sollen nicht vom Datum abhängen: Sprint-Frist aus, Zustand von pve-big im Testordner
        self.konfig.daten.setdefault("big", {}).update(frist="", zustand_ordner=str(self.tmp / "zustand"), host="",
                                                       ssh_ziel="")
        for name in ("eingang", "replays", "sessions", "highlights", "musik", "archiv", "papierkorb"):
            self.konfig.ordner(name).mkdir(parents=True, exist_ok=True)
        (self.konfig.wurzel / ".clip-speicher").touch()
        self.con = db.verbinde(self.konfig.datenbank)

    def tearDown(self) -> None:
        self.con.close()
        self._tmp.cleanup()

    def clip_anlegen(self, *, status="gesendet", start: datetime | None = None, max_gruppe=1, merkmale=None,
                     file_id="datei", match_id="m1", elo=1500.0) -> int:
        start = start or datetime(2026, 9, 21, 19, 0, tzinfo=UTC)
        if not self.con.execute("SELECT 1 FROM matches WHERE id = ?", (match_id,)).fetchone():
            self.con.execute(
                "INSERT INTO matches (id, replay_pfad, start_utc, ende_utc, erstellt, geaendert, platzierung, kills)"
                " VALUES (?, ?, ?, ?, ?, ?, 3, 4)",
                (match_id, f"replays/{match_id}.replay", iso(start), iso(start), iso(start), iso(start)),
            )
        nr = self.con.execute("SELECT COUNT(*) FROM clips WHERE match_id = ?", (match_id,)).fetchone()[0] + 1
        zeiten = [iso(start + timedelta(seconds=10 + 3 * i)) for i in range(max_gruppe)]
        merkmale = merkmale or {"kill_punkte": 1.0, "victory_royale": 0.0, "laenge": 0.0, "lautstaerke": 0.0, "kommentar": 0.0}
        cursor = self.con.execute(
            """INSERT INTO clips (match_id, nr, status, titel, typ, kills, max_gruppe, kill_zeiten, start_utc, ende_utc,
                                  quelle_pfad, quelle_start_s, quelle_ende_s, merkmale, punkte, begruendung,
                                  tg_file_id, elo, erstellt, geaendert)
               VALUES (?, ?, ?, 'Test', 'einzel', ?, ?, ?, ?, ?, 'eingang/x.mp4', 0, 20, ?, 1, 'Test', ?, ?, ?, ?)""",
            (match_id, nr, status, max_gruppe, max_gruppe, json.dumps(zeiten), iso(start),
             iso(start + timedelta(seconds=30)), json.dumps(merkmale), file_id, elo, iso(start), iso(start)),
        )
        return int(cursor.lastrowid)


def testvideo(ziel: Path, *, dauer=6.0, tonspuren=2, fps=30, creation_time: datetime | None = None) -> Path:
    """Erzeugt ein kleines Video mit Testbild und Sinustönen (eine Spur pro Ton)."""
    ziel.parent.mkdir(parents=True, exist_ok=True)
    befehl = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
              "-f", "lavfi", "-i", f"testsrc2=size=640x360:rate={fps}:duration={dauer}"]
    for i in range(tonspuren):
        befehl += ["-f", "lavfi", "-i", f"sine=frequency={440 + 220 * i}:duration={dauer}"]
    befehl += ["-map", "0:v"] + [x for i in range(tonspuren) for x in ("-map", f"{i + 1}:a")]
    befehl += ["-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest"]
    if creation_time:
        befehl += ["-metadata", f"creation_time={creation_time.strftime('%Y-%m-%dT%H:%M:%S.000000Z')}"]
    subprocess.run(befehl + [str(ziel)], check=True)
    return ziel
