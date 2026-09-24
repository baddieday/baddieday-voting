"""Session vorbei: Datei vom Gaming-PC abholen, auf n8n warten, Short nur aus dem Abend, nie wecken."""

import contextlib
import io
import json
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

from clip_pipeline import cli, sitzung
from clip_pipeline.zeit import iso, jetzt

from tests.hilfen import HAT_FFMPEG
from tests.regie_hilfen import MOMENTE, MitRegieMaterial


@unittest.skipUnless(HAT_FFMPEG, "ffmpeg fehlt")
class Sitzung(MitRegieMaterial):
    def marker(self, name, matches, ende):
        ordner = self.konfig.wurzel / "sitzungen"
        ordner.mkdir(exist_ok=True)
        text = json.dumps({"session": name, "matches": matches, "ende_utc": iso(ende)})
        (ordner / f"{name}.json").write_bytes(b"\xef\xbb\xbf" + text.encode())  # wie Windows PowerShell 5.1

    def test_ablauf(self):
        self.momente_anlegen(MOMENTE)
        self.musik_anlegen(150, "episch")
        for m in ("m1", "m2"):
            self.con.execute("INSERT INTO matches (id, replay_pfad, start_utc, ende_utc, status, erstellt, geaendert) "
                             "VALUES (?, ?, 'x', 'x', 'verarbeitet', 'x', 'x')", (m, f"replays/{m}.replay"))
        self.marker("session_2026-09-24_23-10-00", ["m1", "m2", "m9"], jetzt())
        e = sitzung.verarbeite(self.con, self.konfig, claude=False, whisper=False)
        self.assertEqual((e["neu"], e["wartet"][0]["offen"]), ([], ["m9"]))  # m9 noch nicht von n8n verarbeitet

        self.marker("session_2026-09-24_23-10-00", ["m1", "m2", "m9"], jetzt() - timedelta(hours=3))
        e = sitzung.verarbeite(self.con, self.konfig, claude=False, whisper=False)
        neu = e["neu"][0]
        self.assertIn("m9", neu["hinweis"])
        entwurf = self.con.execute("SELECT * FROM entwuerfe WHERE id = ?", (neu["entwurf"],)).fetchone()
        liste = json.loads(Path(entwurf["schnittliste"]).read_text())
        self.assertTrue({s["match_id"] for s in liste["segmente"]} <= {"m1", "m2"})  # nur der Abend
        self.assertTrue(Path(entwurf["datei"]).is_file())  # gerendert -> der Lern-Bot schickt ihn
        self.assertEqual(sitzung.verarbeite(self.con, self.konfig, claude=False, whisper=False)["neu"], [])  # einmal

    def test_speicher_schlaeft_kein_wecken(self):
        (self.konfig.wurzel / ".clip-speicher").unlink()
        self.konfig.daten["speicher"].update(host="pve-big", wol_mac="aa:bb:cc:dd:ee:ff")
        ausgabe = io.StringIO()
        with mock.patch("clip_pipeline.cli.lade", return_value=self.konfig), \
                mock.patch("clip_pipeline.konfig.Konfig._host_erreichbar", return_value=False), \
                mock.patch("clip_pipeline.konfig.sende_wake_on_lan") as wol, \
                contextlib.redirect_stdout(ausgabe), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(["sitzungen"])
        self.assertEqual(code, 3)
        wol.assert_not_called()
