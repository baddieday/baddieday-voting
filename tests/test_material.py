"""Material-Kopie: nur kopieren (Quelle unberührt), Prüfsumme, idempotent, bei Platzmangel letzte Sekunden."""

import collections
import contextlib
import io
import json
import unittest
from unittest import mock

from clip_pipeline import cli, material, medien

from tests.hilfen import HAT_FFMPEG, MitSpeicher, testvideo

Nutzung = collections.namedtuple("Nutzung", "total used free")


@unittest.skipUnless(HAT_FFMPEG, "ffmpeg fehlt")
class Material(MitSpeicher):
    def setUp(self):
        super().setUp()
        w = self.konfig.wurzel
        self.replay = w / "replays" / "UnsavedReplay-2026.09.21-21.42.22.replay"
        self.replay.write_bytes(bytes(range(256)) * 400)
        self.session_json = w / "sessions" / "2026-09-21_21-42-22" / "analyse.json"
        self.session_json.parent.mkdir(parents=True)
        self.session_json.write_text('{"kandidaten": []}')
        self.video = testvideo(w / "eingang" / "steelseries" / "Fortnite__1.mp4", dauer=12, tonspuren=2)
        self.konfig.daten["material"] = {"ordner": str(self.tmp / "mini"), "reserve_gb": 0, "ende_s": 3}

    def _stand(self):
        return {p: (p.stat().st_mtime_ns, material.sha256(p)) for p in (self.replay, self.session_json, self.video)}

    def test_volle_kopie_mit_pruefsumme_und_idempotent(self):
        vorher = self._stand()
        e = material.hole(self.konfig, self.con)
        self.assertEqual((e["kopiert"], e["gekuerzt"], e["fehler"], e["big"]), (3, 0, [], "lief schon"))
        self.assertEqual(self._stand(), vorher)  # Rohdaten unverändert, nichts verschoben
        for rel in ("replays/UnsavedReplay-2026.09.21-21.42.22.replay", "eingang/steelseries/Fortnite__1.mp4"):
            kopie = material.lokal(self.konfig, rel)
            self.assertTrue(str(kopie).startswith(str(self.tmp / "mini")))
            self.assertEqual(material.sha256(kopie), material.sha256(self.konfig.absolut(rel)))
        zeilen = self.con.execute("SELECT art, sha256_quelle = sha256_ziel AS gleich FROM material ORDER BY quelle").fetchall()
        self.assertEqual([(z["art"], z["gleich"]) for z in zeilen], [("video", 1), ("replay", 1), ("session", 1)])
        self.assertTrue((self.tmp / "mini" / "bestand-big.md").is_file())
        self.assertEqual(material.hole(self.konfig, self.con)["posten"], 0)  # zweiter Lauf: nichts zu tun
        self.assertFalse(list((self.tmp / "mini").rglob("*.teil*")))

    def test_platzmangel_kuerzt_videos(self):
        klein = self.replay.stat().st_size + self.session_json.stat().st_size + self.video.stat().st_size // 2
        with mock.patch("clip_pipeline.material.shutil.disk_usage", return_value=Nutzung(10**12, 0, klein)):
            e = material.hole(self.konfig, self.con)
        self.assertTrue(e["videos_kuerzen"])
        self.assertEqual((e["kopiert"], e["gekuerzt"]), (2, 1))
        kurz = material.lokal(self.konfig, "eingang/steelseries/Fortnite__1.mp4")
        info = medien.probe(kurz)
        self.assertLess(info.dauer_s, 6)  # letzte ~3 s (Keyframe davor)
        self.assertEqual(len(info.tonspuren), 2)  # beide Spuren verlustfrei dabei
        zeile = self.con.execute("SELECT * FROM material WHERE art = 'video_ende'").fetchone()
        self.assertGreater(zeile["von_s"], 8)
        self.assertEqual(zeile["sha256_quelle"], material.sha256(self.video))

    def test_probelauf_kopiert_nichts(self):
        e = material.hole(self.konfig, self.con, probelauf=True)
        self.assertEqual((e["posten"], e["kopiert"]), (3, 0))
        self.assertFalse((self.tmp / "mini" / "replays").exists())

    def test_schlafender_host_ohne_gesichertes_aus_wird_nicht_geweckt(self):
        (self.konfig.wurzel / ".clip-speicher").unlink()
        self.konfig.daten["speicher"]["wol_mac"] = "aa:bb:cc:dd:ee:ff"
        self.konfig.daten["big"]["host"] = "pve-big"
        ausgabe = io.StringIO()
        with mock.patch("clip_pipeline.cli.lade", return_value=self.konfig), \
                mock.patch("clip_pipeline.big.wach", return_value=False), \
                mock.patch("clip_pipeline.big.sende_wake_on_lan") as wol, \
                contextlib.redirect_stdout(ausgabe), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(["material"])
        self.assertEqual(code, 3)
        self.assertEqual(json.loads(ausgabe.getvalue().splitlines()[-1])["fehler"], "wecken_verboten")
        wol.assert_not_called()
