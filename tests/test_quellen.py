import unittest
from datetime import datetime

from clip_pipeline.medien import Probe
from clip_pipeline.quellen import aufnahme_aus, erkenne, steelseries_meta
from clip_pipeline.zeit import UTC, lokal_zu_utc


class Dateinamen(unittest.TestCase):
    def test_nvidia_varianten(self):
        fall = erkenne("Fortnite 2026.09.21 - 21.53.34.22.Doppeleliminierung.DVR.mp4")
        self.assertEqual((fall.quelle, fall.ereignis, fall.name_ist_ende), ("nvidia_highlight", "Doppeleliminierung", True))
        self.assertEqual(fall.zeit_lokal, datetime(2026, 9, 21, 21, 53, 34))
        self.assertEqual(erkenne("Fortnite 2026.09.22 - 23.49.27.03.Am Boden.DVR.mp4").ereignis, "Am Boden")
        self.assertEqual(erkenne("Fortnite 2026.05.14 - 22.10.13.136.DVR.mp4").quelle, "nvidia_dvr")
        manuell = erkenne("Fortnite 2026.05.08 - 22.58.36.43.mp4")
        self.assertEqual((manuell.quelle, manuell.name_ist_ende), ("nvidia_aufnahme", False))
        self.assertEqual(erkenne("Fortnite Screenshot 2026.09.21 - 22.17.01.34.Sieg.png").quelle, "nvidia_screenshot")

    def test_steelseries_und_fremdes(self):
        self.assertEqual(erkenne("Fortnite__2026-09-21__21-53-30.mp4").quelle, "steelseries")
        self.assertIsNone(erkenne("Minecraft 2026.05.30 - 15.47.51.01.DVR.mp4"))
        self.assertIsNone(erkenne("urlaub.mp4"))


class Zeitspannen(unittest.TestCase):
    def test_nvidia_highlight_mit_creation_time_und_versatz(self):
        info = Probe(20.0, 1920, 1080, 60, True, ["SoundHandle"], {"creation_time": "2026-09-21T19:56:26.000000Z"})
        a = aufnahme_aus("x.mp4", erkenne("Fortnite 2026.09.21 - 21.56.21.24.Eliminiert.DVR.mp4"), info,
                         zonen_name="Europe/Berlin", versatz_nvidia_s=1.4, nachlauf_nvidia_s=5.4)
        # creation_time (UTC) gewinnt gegen den Namen, Versatz verschiebt das Ende
        self.assertEqual(a.ende_utc, datetime(2026, 9, 21, 19, 56, 27, 400000, tzinfo=UTC))
        self.assertEqual((a.ende_utc - a.start_utc).total_seconds(), 20.0)
        self.assertEqual((a.ereignisse[0].art, a.ereignisse[0].zeit_utc.second), ("tod", 22))

    def test_nvidia_tippfehler_dreifach(self):
        info = Probe(30.0, 1920, 1080, 60, True, ["a"], {})
        a = aufnahme_aus("x.mp4", erkenne("Fortnite 2026.09.21 - 21.00.00.01.Dreifacheliminerung.DVR.mp4"), info,
                         zonen_name="Europe/Berlin")
        self.assertEqual((a.ereignisse[0].art, a.ereignisse[0].anzahl), ("kill", 3))
        self.assertEqual(a.ende_utc, lokal_zu_utc(datetime(2026, 9, 21, 21, 0, 0), "Europe/Berlin"))

    def test_steelseries_metadaten(self):
        meta = ('{"recording_timestamp":"2026-09-21T21:53:36+02:00","full_length":120.0,"gamesense_events":['
                '{"type":"WIN","unlocalized_display_name":"QUEST COMPLETE","clip_timestamp":34.9},'
                '{"type":"KILL","unlocalized_display_name":"ELIM","clip_timestamp":110.9},'
                '{"type":"STAR","unlocalized_display_name":"DOUBLE KILL","clip_timestamp":114.9}]}')
        tags = {"STEELSERIES_META0001": meta[40:], "STEELSERIES_META0000": meta[:40]}  # absichtlich unsortiert
        self.assertEqual(steelseries_meta(tags)["full_length"], 120.0)
        info = Probe(120.0, 1920, 1080, 30, False, ["Game", "Chat"], tags)
        a = aufnahme_aus("x.mp4", erkenne("Fortnite__2026-09-21__21-53-30.mp4"), info, zonen_name="Europe/Berlin")
        self.assertEqual(a.ende_utc, datetime(2026, 9, 21, 19, 53, 36, tzinfo=UTC))
        self.assertEqual([(e.art, e.anzahl) for e in a.ereignisse], [("kill", 1), ("kill", 2)])  # Quest ignoriert
        self.assertAlmostEqual(a.sekunde(a.ereignisse[0].zeit_utc), 110.9, places=3)


if __name__ == "__main__":
    unittest.main()
