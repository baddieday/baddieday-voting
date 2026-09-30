"""Kalibrier-Bericht (B5, erste Stufe, 30.09.): ein echtes Match mit Waffen-Nummern, Stimmung und Standbildern."""

import asyncio
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from clip_pipeline import kalibrierung
from clip_pipeline.zeit import iso, jetzt
from tests.hilfen import HAT_FFMPEG, MitSpeicher, testvideo
from tests.test_merkmale import ICH, START, daten, elim


@unittest.skipUnless(HAT_FFMPEG, "ffmpeg fehlt")
class Kalibrierung(MitSpeicher):
    def setUp(self):
        super().setUp()
        self.konfig.daten.setdefault("merkmale", {})["waffen"] = {"sniper": [7]}
        clip = self.clip_anlegen(match_id="m1", start=START, max_gruppe=2)
        video = testvideo(self.konfig.wurzel / "clips" / "m1_01.mp4", dauer=6.0)
        self.con.execute("UPDATE clips SET clip_pfad = ? WHERE id = ?", (self.konfig.relativ(video), clip))
        self.con.execute("""INSERT INTO momente (schluessel, clip_id, match_id, datei, start_s, ende_s, kills, stimmung,
                            sicherheit, quelle, merkmale, text, erstellt, geaendert)
                            VALUES (?, ?, 'm1', ?, 0, 6, 2, 'episch', 0.7, 'regel', '{}', 'boah was für ein Treffer', ?, ?)""",
                         (f"clip:{clip}", clip, str(video), iso(jetzt()), iso(jetzt())))
        ordner = self.konfig.ordner("sessions") / "m1"
        ordner.mkdir(parents=True, exist_ok=True)
        (ordner / "replay.json").write_text(json.dumps(daten([elim(12, ICH, "A", waffe=7, bot=False),
                                                              elim(15, ICH, "B", waffe=9, bot=True)])), encoding="utf-8")

    def test_bericht_mit_waffen_stimmung_und_bildern(self):
        e = kalibrierung.bericht(self.con, self.konfig, "m1", whisper=False)
        clip = e["clips"][0]
        self.assertEqual([(k["waffe"], k["kategorie"], k["bot"]) for k in clip["kills"]],
                         [(7, "sniper", False), (9, "sonstige", True)])
        self.assertEqual((clip["stimmung"], clip["text"]), ("episch", "boah was für ein Treffer"))
        self.assertEqual(len(clip["bilder"]), 3)
        self.assertTrue(all(Path(b).is_file() for b in clip["bilder"]))
        self.assertEqual(e["waffen_unbekannt"], [9])                        # 9 steht in keiner Liste
        self.assertTrue((Path(e["ordner"]) / "bericht.json").is_file())
        text = kalibrierung.clip_text(clip)
        self.assertIn("Stimmung: episch (70%, regel)", text)
        self.assertIn("Waffe 7 (sniper) · Spieler", text)
        self.assertIn("Waffen-Nummern ohne Kategorie: 9", kalibrierung.schluss_text(e))

    def test_unbekannte_session(self):
        with self.assertRaises(kalibrierung.KalibrierFehler):
            kalibrierung.bericht(self.con, self.konfig, "gibts-nicht")
        with self.assertRaises(kalibrierung.KalibrierFehler):
            kalibrierung.bericht(self.con, self.konfig, "../etc")

    def test_bot_schickt_album_je_clip(self):
        try:
            from clip_pipeline import lernbot_kalibrierung
        except ImportError:
            self.skipTest("python-telegram-bot fehlt")
        gesendet = []

        async def media(chat, medien):
            gesendet.append(("album", len(medien), medien[0].caption))

        async def text(chat, t, **kw):
            gesendet.append(("text", t))

        app = SimpleNamespace(bot=SimpleNamespace(send_media_group=media, send_message=text),
                              bot_data={"konfig": self.konfig, "erlaubt": 42})
        self.konfig.daten.setdefault("stimmung", {})["whisper"] = False
        asyncio.run(lernbot_kalibrierung.laufe(app, "m1"))
        self.assertEqual(gesendet[0][:2], ("album", 3))
        self.assertIn("#1 Test", gesendet[0][2])
        self.assertTrue(gesendet[-1][1].startswith("🧪 Kalibrierung m1: 1 Clips"))


if __name__ == "__main__":
    unittest.main()
