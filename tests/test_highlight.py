import subprocess
import unittest
from datetime import timedelta

from clip_pipeline import db, highlight, medien
from clip_pipeline.zeit import jetzt

from tests.hilfen import HAT_FFMPEG, MitSpeicher, testvideo


@unittest.skipUnless(HAT_FFMPEG, "ffmpeg fehlt")
class Highlight(MitSpeicher):
    def _clip(self, nr: int, dauer: float, tonspuren: int, elo: float, fps: int) -> int:
        datei = testvideo(self.konfig.ordner("sessions") / "m1" / "clips" / f"{nr}.mp4", dauer=dauer, tonspuren=tonspuren, fps=fps)
        cid = self.clip_anlegen(status="veroeffentlicht" if nr == 1 else "freigegeben", start=jetzt() - timedelta(days=2), elo=elo)
        self.con.execute("UPDATE clips SET clip_pfad = ?, quelle_start_s = 0, quelle_ende_s = ? WHERE id = ?",
                         (self.konfig.relativ(datei), dauer, cid))
        return cid

    def test_video_mit_lizenzierter_musik(self):
        a = self._clip(1, 4, 2, 1600, 30)
        b = self._clip(2, 3, 1, 1500, 60)
        c = self._clip(3, 3, 0, 1550, 30)
        alt = self.clip_anlegen(status="freigegeben", start=jetzt() - timedelta(days=30))  # zu alt
        musik = self.konfig.ordner("musik")
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "sine=frequency=330:duration=3", str(musik / "titel.mp3")], check=True)
        (musik / "titel.lizenz.txt").write_text("CC0 – Testton", encoding="utf-8")
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "sine=frequency=500:duration=3", str(musik / "ohne_lizenz.mp3")], check=True)

        ergebnis = highlight.erstelle(self.con, self.konfig, "2026-KW39", 14)
        self.assertEqual((ergebnis["clips"], ergebnis["musik"]), (3, "titel.mp3"))  # ohne Lizenz nie
        self.assertEqual(ergebnis["clip_ids"], [b, c, a])  # aufsteigend, der beste zuletzt
        info = medien.probe(self.konfig.absolut(ergebnis["datei"]))
        self.assertEqual((info.breite, info.hoehe, round(info.fps)), (1920, 1080, 60))
        self.assertAlmostEqual(info.dauer_s, 4 + 3 + 3 - 2 * 0.6, delta=0.25)
        self.assertEqual(ergebnis["dauer"], "00:09")
        self.assertEqual(db.clip(self.con, a)["status"], "im_highlight")  # war veröffentlicht
        self.assertEqual(db.clip(self.con, b)["status"], "freigegeben")   # Upload fehlt noch -> bleibt
        self.assertEqual(db.clip(self.con, b)["highlight_id"], "2026-KW39")
        self.assertIsNone(db.clip(self.con, alt)["highlight_id"])
        self.assertTrue(highlight.erstelle(self.con, self.konfig, "2026-KW39", 14)["uebersprungen"])

        # Freigabe im Bot: Vorschau existiert; Verwerfen gibt die Clips wieder frei
        zeile = self.con.execute("SELECT * FROM highlights WHERE name = '2026-KW39'").fetchone()
        self.assertLess(self.konfig.absolut(zeile["vorschau"]).stat().st_size, 48_000_000)
        verworfen = highlight.entscheide(self.con, zeile["id"], freigeben=False)
        self.assertEqual(verworfen["status"], "verworfen")
        self.assertEqual(db.clip(self.con, a)["status"], "veroeffentlicht")
        self.assertIsNone(db.clip(self.con, b)["highlight_id"])
        self.assertEqual(highlight.entscheide(self.con, zeile["id"], freigeben=True)["status"], "verworfen")  # einmalig

    def test_ohne_clips(self):
        self.assertEqual(highlight.erstelle(self.con, self.konfig, "leer", 14)["clips"], 0)


if __name__ == "__main__":
    unittest.main()
