"""Lern-Bot ohne Netzwerk: Entwürfe verschicken, 👍/👎 + Gründe speichern, Musik annehmen, Meldungen."""

import asyncio
import json
import shutil
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from clip_pipeline import db, regie, regie_lernen
from clip_pipeline.zeit import UTC

from tests.hilfen import HAT_FFMPEG
from tests.regie_hilfen import MOMENTE, MitRegieMaterial, klick_musik

try:
    from clip_pipeline import lernbot
    import telegram  # noqa: F401
except ImportError:
    lernbot = None


class FakeBot:
    def __init__(self):
        self.videos, self.texte = [], []

    async def send_video(self, **kw):
        self.videos.append(kw)
        return SimpleNamespace(message_id=100 + len(self.videos))

    async def send_message(self, chat, text, **kw):
        self.texte.append((chat, text))


class FakeQuery:
    def __init__(self, daten, von=42):
        self.data, self.from_user = daten, SimpleNamespace(id=von)
        self.antworten, self.bearbeitet = [], []

    async def answer(self, text=None):
        self.antworten.append(text)

    async def edit_message_caption(self, **kw):
        self.bearbeitet.append(kw)


@unittest.skipIf(lernbot is None or not HAT_FFMPEG, "python-telegram-bot oder ffmpeg fehlt")
class LernBot(MitRegieMaterial):
    def setUp(self):
        super().setUp()
        self.bot = FakeBot()
        self.app = SimpleNamespace(bot=self.bot, bot_data={"con": self.con, "konfig": self.konfig, "erlaubt": 42})
        self.context = SimpleNamespace(bot_data=self.app.bot_data, application=self.app, args=[])

    def klick(self, daten, von=42):
        q = FakeQuery(daten, von)
        asyncio.run(lernbot.bei_klick(SimpleNamespace(callback_query=q), self.context))
        return q

    def test_aufbau(self):
        app = lernbot.baue_app(self.konfig, "123456:TEST", 42)
        befehle = {c for h in app.handlers[0] for c in getattr(h, "commands", ())}
        self.assertTrue({"entwurf", "musik", "lernstand", "stand", "hilfe"} <= befehle)
        app.bot_data["con"].close()

    def test_entwurf_senden_und_bewerten(self):
        self.momente_anlegen(MOMENTE[:8])
        track = self.musik_anlegen(150, "episch")
        eid = lernbot.baue_entwurf(self.konfig, "short")
        self.assertEqual(asyncio.run(lernbot.sende_entwuerfe(self.app)), 1)
        self.assertEqual(asyncio.run(lernbot.sende_entwuerfe(self.app)), 0)  # nicht doppelt
        video = self.bot.videos[0]
        self.assertEqual(video["chat_id"], 42)
        self.assertIn(f"Entwurf #{eid}", video["caption"])
        daten = [b.callback_data for reihe in video["reply_markup"].inline_keyboard for b in reihe]
        self.assertEqual(daten, [f"d:{eid}:1", f"d:{eid}:-1"])

        self.assertEqual(self.klick(f"d:{eid}:-1", von=7).antworten, ["Nicht erlaubt."])  # fremde Person
        q = self.klick(f"d:{eid}:-1")
        gruende = [b.callback_data for reihe in q.bearbeitet[0]["reply_markup"].inline_keyboard for b in reihe]
        self.assertIn(f"g:{eid}:musik", gruende)
        self.klick(f"g:{eid}:musik")
        q = self.klick(f"g:{eid}:hektisch")
        self.assertIn("☑️ 😵 zu hektisch", [b.text for reihe in q.bearbeitet[0]["reply_markup"].inline_keyboard for b in reihe])
        self.klick(f"g:{eid}:hektisch")  # abwählen
        q = self.klick(f"x:{eid}:")
        self.assertIsNone(q.bearbeitet[0]["reply_markup"])
        self.assertIn("Musik passt nicht", q.bearbeitet[0]["caption"])
        zeile = self.con.execute("SELECT * FROM entwurf_bewertungen").fetchone()
        self.assertEqual((zeile["daumen"], json.loads(zeile["gruende"])), (-1, ["musik"]))
        # ... und der nächste compose weiß das
        p, _ = regie_lernen.aktuelle(self.con, self.konfig)
        self.assertEqual(p["track_malus"], {str(track["id"]): 1.0})

    def test_musik_annehmen(self):
        self.konfig.daten["musik"]["ordner"] = str(self.tmp / "musik")
        quelle = klick_musik(self.tmp / "q.mp3", 128, 20)

        class Datei:
            file_name = "Künstler - Lied.mp3"

            async def get_file(self):
                async def download_to_drive(ziel):
                    shutil.copy(quelle, ziel)
                return SimpleNamespace(download_to_drive=download_to_drive)

        antworten = []

        def nachricht(caption):
            async def reply_text(text, **_):
                antworten.append(text)
            return SimpleNamespace(audio=Datei(), document=None, caption=caption, reply_text=reply_text)

        asyncio.run(lernbot.bei_audio(SimpleNamespace(effective_message=nachricht("")), self.context))
        self.assertIn("Quellenangabe", antworten[-1])
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM tracks").fetchone()[0], 0)
        asyncio.run(lernbot.bei_audio(SimpleNamespace(effective_message=nachricht("CC-BY Künstler #chill")), self.context))
        t = self.con.execute("SELECT * FROM tracks").fetchone()
        self.assertEqual((t["titel"], t["kuenstler"], t["quelle"]), ("Lied", "Künstler", "CC-BY Künstler"))
        self.assertEqual(json.loads(t["stimmungen"])[0], "chill")
        self.assertRegex(antworten[-1], r"12[789] BPM")  # 128 ± Messgenauigkeit

    def test_meldungen_und_abendstand(self):
        self.assertFalse(lernbot.abendstand(self.con, self.konfig, datetime(2026, 9, 24, 17, 0, tzinfo=UTC)))  # 19 Uhr
        self.assertTrue(lernbot.abendstand(self.con, self.konfig, datetime(2026, 9, 24, 19, 30, tzinfo=UTC)))  # 21:30
        self.assertFalse(lernbot.abendstand(self.con, self.konfig, datetime(2026, 9, 24, 20, 0, tzinfo=UTC)))  # schon
        db.lern_meldung(self.con, "bericht", "Abschlussbericht\n" + ("x" * 3000 + "\n") * 3)
        self.assertEqual(asyncio.run(lernbot.sende_meldungen(self.app)), 2)
        self.assertEqual(asyncio.run(lernbot.sende_meldungen(self.app)), 0)
        self.assertIn("Stand:", self.bot.texte[0][1])
        self.assertEqual(len(self.bot.texte), 1 + 3)  # Bericht in 3 Stücke geteilt
        self.assertTrue(all(len(t) <= lernbot.TEXT_MAX for _, t in self.bot.texte))


if __name__ == "__main__":
    unittest.main()
