"""Der Bot lässt sich zusammenbauen (ohne Netzwerk) und verschickt die Outbox richtig."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock

from tests.hilfen import MitSpeicher

try:
    from clip_pipeline.bot import app as bot_app
except ImportError:  # python-telegram-bot nicht installiert
    bot_app = None


@unittest.skipIf(bot_app is None, "python-telegram-bot fehlt")
class BotApp(MitSpeicher):
    def test_aufbau(self):
        app = bot_app.baue_app(self.konfig, "123456:TEST", 42)
        befehle = {c for h in app.handlers[0] for c in getattr(h, "commands", ())}
        self.assertTrue({"battle", "rangliste", "gewichte", "offen", "status"} <= befehle)
        app.bot_data["con"].close()

    def test_outbox_sendet_vorbewertete_clips(self):
        cid = self.clip_anlegen(status="vorbewertet", file_id=None)
        vorschau = self.konfig.ordner("sessions") / "m1" / "vorschau" / "v.mp4"
        vorschau.parent.mkdir(parents=True)
        vorschau.write_bytes(b"video")
        self.con.execute("UPDATE clips SET vorschau_pfad = 'sessions/m1/vorschau/v.mp4' WHERE id = ?", (cid,))

        gesendet = []

        async def send_video(**kwargs):
            gesendet.append(kwargs)
            return SimpleNamespace(message_id=7, video=SimpleNamespace(file_id="F1"))

        fake = SimpleNamespace(bot_data={"con": self.con, "konfig": self.konfig, "erlaubt": 42},
                               bot=SimpleNamespace(send_video=send_video))
        self.assertEqual(asyncio.run(bot_app.sende_outbox(fake)), 1)
        self.assertEqual(gesendet[0]["chat_id"], 42)
        self.assertIn("Clip #1", gesendet[0]["caption"])
        zeile = self.con.execute("SELECT status, tg_file_id, tg_nachricht_id FROM clips").fetchone()
        self.assertEqual(tuple(zeile), ("gesendet", "F1", 7))

        # Speicher offline -> nichts senden, nichts verlieren
        self.clip_anlegen(status="vorbewertet", file_id=None)
        with mock.patch.object(self.konfig, "pruefe_speicher", side_effect=bot_app.SpeicherOffline("aus")):
            self.assertEqual(asyncio.run(bot_app.sende_outbox(fake)), 0)

    def test_meldungen_einmal_senden(self):
        from clip_pipeline import db

        self.assertTrue(db.meldung(self.con, "ohne_video:s1", "⚠️ 2 Kills ohne Aufnahme"))
        self.assertFalse(db.meldung(self.con, "ohne_video:s1", "⚠️ doppelt"))  # gleicher Schlüssel -> nicht nochmal
        texte_gesendet = []

        async def send_message(chat, text, **_):
            texte_gesendet.append((chat, text))

        fake = SimpleNamespace(bot_data={"con": self.con, "konfig": self.konfig, "erlaubt": 42},
                               bot=SimpleNamespace(send_message=send_message))
        self.assertEqual(asyncio.run(bot_app.sende_meldungen(fake)), 1)
        self.assertEqual(asyncio.run(bot_app.sende_meldungen(fake)), 0)
        self.assertEqual(texte_gesendet, [(42, "⚠️ 2 Kills ohne Aufnahme")])

    def test_outbox_sendet_highlight_zur_freigabe(self):
        vorschau = self.konfig.ordner("highlights") / "h.vorschau.mp4"
        vorschau.write_bytes(b"video")
        self.con.execute(
            "INSERT INTO highlights (name, datei, vorschau, clips, dauer, erstellt) VALUES "
            "('highlight-2026-09-25', 'highlights/h.mp4', 'highlights/h.vorschau.mp4', 5, '02:10', 'x')"
        )
        gesendet = []

        async def send_video(**kwargs):
            gesendet.append(kwargs)
            return SimpleNamespace(message_id=9, video=None)

        fake = SimpleNamespace(bot_data={"con": self.con, "konfig": self.konfig, "erlaubt": 42},
                               bot=SimpleNamespace(send_video=send_video))
        self.assertEqual(asyncio.run(bot_app.sende_outbox(fake)), 1)
        self.assertIn("highlight-2026-09-25", gesendet[0]["caption"])
        knoepfe = [b.callback_data for reihe in gesendet[0]["reply_markup"].inline_keyboard for b in reihe]
        self.assertEqual(knoepfe, ["hf:1", "hv:1"])
        self.assertEqual(self.con.execute("SELECT status FROM highlights").fetchone()[0], "gesendet")
        self.assertEqual(asyncio.run(bot_app.sende_outbox(fake)), 0)  # nicht doppelt


if __name__ == "__main__":
    unittest.main()
