"""Messung am fertigen Video (Cutter-Maßstab 1.0, messung.py): ein Durchlauf findet Schnitte, Schwarz, Stille und
die Lautheit; eine kaputte Datei liefert Fehler statt einer Ausnahme."""

import subprocess
import tempfile
import unittest
from pathlib import Path

from clip_pipeline import messung

from tests.hilfen import HAT_FFMPEG

BILD = 1 / 30


def testvideo(ziel: Path) -> Path:
    """8 s, 720×1280, 30 fps: testsrc 0–3,5 s, Schwarz 3,5–3,8 s, testsrc2 bis 8 s; Sinus −20 dBFS, Stille 5–6 s."""
    graph = ("[0:v][1:v][2:v]concat=n=3:v=1:a=0,format=yuv420p[v];"
             "[3:a]volume=-2dB,volume=enable='between(t,5,6)':volume=0,aformat=channel_layouts=stereo[a]")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc=size=720x1280:rate=30:duration=3.5",
                    "-f", "lavfi", "-i", "color=black:size=720x1280:rate=30:duration=0.3",
                    "-f", "lavfi", "-i", "testsrc2=size=720x1280:rate=30:duration=4.2",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=8",
                    "-filter_complex", graph, "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-t", "8", str(ziel)], check=True)
    return ziel


@unittest.skipUnless(HAT_FFMPEG, "ffmpeg fehlt")
class Messe(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_schnitte_schwarz_stille_lautheit(self):
        video = testvideo(self.tmp / "entwurf.mp4")
        ordner = self.tmp / "kritik-1"
        m = messung.messe(video, ordner, band=(437, 843), stems=None, timeout_s=180)
        self.assertEqual((m.version, m.fehler), (messung.MESS_VERSION, []))
        self.assertEqual(len(m.ydif_b), 240)                                  # je Bild ein Wert
        self.assertEqual(len(m.t_bild), 240)
        for schnitt in (3.5, 3.8):                                            # in das Schwarz und wieder heraus
            self.assertTrue(any(abs(t - schnitt) <= BILD + 1e-6 for t, _score in m.scd), (schnitt, m.scd))
        self.assertEqual(len(m.schwarz), 1)
        self.assertAlmostEqual(m.schwarz[0][0], 3.5, delta=BILD)
        self.assertAlmostEqual(m.schwarz[0][1], 3.8, delta=BILD)
        self.assertEqual(len(m.stille), 1)
        self.assertAlmostEqual(m.stille[0][0], 5.0, delta=0.1)
        self.assertAlmostEqual(m.stille[0][1], 6.0, delta=0.1)
        referenz = messung.loudnorm_messen(video, -14, -1.5)["input_i"]     # zweiter Messer im selben ffmpeg
        self.assertAlmostEqual(m.i_lufs, referenz, delta=1.0)
        self.assertAlmostEqual(len(m.m), 80, delta=2)                         # alle 0,1 s
        self.assertLess(m.tp_db, -15)
        self.assertEqual(len(m.hash), 16)                                     # 2 Bilder/s
        self.assertEqual((m.dauer_bild, m.dauer_ton), (8.0, 8.0))
        self.assertEqual([p.name for p in ordner.iterdir()], [])              # Zwischendateien wieder weg
        messung.speichere(m, ordner / "messung.json")
        wieder = messung.lade(ordner / "messung.json")
        self.assertEqual((wieder.schwarz, len(wieder.ydif_b), wieder.i_lufs), (m.schwarz, 240, round(m.i_lufs, 2)))

    def test_kaputte_datei_ohne_ausnahme(self):
        kaputt = self.tmp / "kaputt.mp4"
        kaputt.write_bytes(b"kein Video" * 100)
        m = messung.messe(kaputt, self.tmp / "kritik-2", band=None, stems=None, timeout_s=60)
        self.assertEqual(m.version, 0)
        self.assertTrue(m.fehler)
        self.assertIsNone(messung.lade(self.tmp / "gibt-es-nicht.json"))


if __name__ == "__main__":
    unittest.main()
