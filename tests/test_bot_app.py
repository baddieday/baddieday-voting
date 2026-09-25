"""Der Bot lässt sich zusammenbauen (ohne Netzwerk) und verschickt die Outbox richtig."""

import asyncio
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest import mock

from clip_pipeline import db, lager
from clip_pipeline.bot import aktionen
from clip_pipeline.zeit import lokal_zu_utc

from tests.hilfen import MitSpeicher
from tests.test_lager import MitAbgleich

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

    def test_puffer_und_lager_warten_die_ruhezeit_ab(self):
        db.meldung(self.con, "puffer:platz:2026-09-25", "💾 Puffer wird knapp")
        db.meldung(self.con, "ohne_video:s1", "⚠️ 2 Kills ohne Aufnahme")
        db.meldung(self.con, "lager:2026-09-25", "🗄️ Lager-Abgleich: 1 Datei noch nicht im Lager")
        gesendet = []

        async def send_message(chat, text, **_):
            gesendet.append(text)

        fake = SimpleNamespace(bot_data={"con": self.con, "konfig": self.konfig, "erlaubt": 42},
                               bot=SimpleNamespace(send_message=send_message))
        with mock.patch.object(aktionen, "jetzt", return_value=_um(4, 35)):  # nach dem Abgleich um 04:30
            self.assertEqual(asyncio.run(bot_app.sende_meldungen(fake)), 1)
        self.assertEqual(gesendet, ["⚠️ 2 Kills ohne Aufnahme"])  # andere Meldungen wie bisher sofort
        with mock.patch.object(aktionen, "jetzt", return_value=_um(8, 0)):
            self.assertEqual(asyncio.run(bot_app.sende_meldungen(fake)), 2)
            self.assertEqual(asyncio.run(bot_app.sende_meldungen(fake)), 0)
        self.assertEqual(gesendet[1:], ["💾 Puffer wird knapp", "🗄️ Lager-Abgleich: 1 Datei noch nicht im Lager"])

    def test_clip_videos_nachts_ohne_ton(self):
        vorschau = self.konfig.ordner("sessions") / "m1" / "vorschau" / "v.mp4"
        vorschau.parent.mkdir(parents=True)
        vorschau.write_bytes(b"video")
        gesendet = []

        async def send_video(**kwargs):
            gesendet.append(kwargs)
            return SimpleNamespace(message_id=7, video=SimpleNamespace(file_id="F1"))

        fake = SimpleNamespace(bot_data={"con": self.con, "konfig": self.konfig, "erlaubt": 42},
                               bot=SimpleNamespace(send_video=send_video))
        for stunde in (2, 14):
            cid = self.clip_anlegen(status="vorbewertet", file_id=None)
            self.con.execute("UPDATE clips SET vorschau_pfad = 'sessions/m1/vorschau/v.mp4' WHERE id = ?", (cid,))
            with mock.patch.object(aktionen, "jetzt", return_value=_um(stunde)):
                self.assertEqual(asyncio.run(bot_app.sende_outbox(fake)), 1)  # sofort, auch nachts
        self.assertEqual([k["disable_notification"] for k in gesendet], [True, False])

    def test_status_ohne_getrennten_betrieb_ohne_lager_zeile(self):
        text = _status_text(self.con, self.konfig)
        self.assertIn("Speicher: online", text)
        self.assertNotIn("Lager", text)


def _um(stunde: int, minute: int = 0) -> datetime:
    return lokal_zu_utc(datetime(2026, 9, 25, stunde, minute), "Europe/Berlin")


def _status_text(con, konfig) -> str:
    """/status ohne Telegram: liefert den Antworttext."""
    antworten = []

    async def reply_text(text, **_):
        antworten.append(text)

    update = SimpleNamespace(effective_message=SimpleNamespace(reply_text=reply_text))
    context = SimpleNamespace(bot_data={"con": con, "konfig": konfig, "erlaubt": 42})
    asyncio.run(bot_app.cmd_status(update, context))
    return antworten[0]


class Ruhezeit(MitSpeicher):
    """Die Ruhezeit-Regeln brauchen kein Telegram."""

    def test_ueber_mitternacht(self):
        self.konfig.daten["telegram"].update(leise_von="23:00", leise_bis="08:00")
        erwartet = {(22, 59): False, (23, 0): True, (0, 30): True, (4, 35): True, (7, 59): True, (8, 0): False,
                    (12, 0): False}
        for (stunde, minute), leise in erwartet.items():
            self.assertEqual(aktionen.ruhezeit(self.konfig, _um(stunde, minute)), leise, (stunde, minute))

    def test_am_tag_leer_und_ungueltig(self):
        self.konfig.daten["telegram"].update(leise_von="13:00", leise_bis="15:00")
        self.assertTrue(aktionen.ruhezeit(self.konfig, _um(14)))
        self.assertFalse(aktionen.ruhezeit(self.konfig, _um(15)))
        self.konfig.daten["telegram"].update(leise_von="", leise_bis="08:00")  # leer = nie leise
        self.assertFalse(aktionen.ruhezeit(self.konfig, _um(3)))
        self.konfig.daten["telegram"].update(leise_von="08:00", leise_bis="08:00")
        self.assertFalse(aktionen.ruhezeit(self.konfig, _um(8)))
        self.konfig.daten["telegram"].update(leise_von="25:00", leise_bis="8")  # Tippfehler hält nichts auf
        with self.assertLogs("clip-bot", "WARNING"):
            self.assertFalse(aktionen.ruhezeit(self.konfig, _um(3)))
        del self.konfig.daten["telegram"]["leise_von"]  # alte Konfig ohne Eintrag
        self.assertFalse(aktionen.ruhezeit(self.konfig, _um(3)))

    def test_faellige_meldungen(self):
        self.konfig.daten["telegram"].update(leise_von="23:00", leise_bis="08:00")
        for schluessel in ("puffer:woche:2026-W40", "ohne_video:s1", "lager:2026-09-25", "lagerfeuer:1"):
            db.meldung(self.con, schluessel, schluessel)
        nachts = [z["schluessel"] for z in aktionen.faellige_meldungen(self.con, self.konfig, _um(1))]
        self.assertEqual(nachts, ["ohne_video:s1", "lagerfeuer:1"])
        tags = [z["schluessel"] for z in aktionen.faellige_meldungen(self.con, self.konfig, _um(9))]
        self.assertEqual(len(tags), 4)


@unittest.skipIf(bot_app is None, "python-telegram-bot fehlt")
class StatusGetrennt(MitAbgleich):
    """/status im getrennten Betrieb: eine Zeile aus lager.status – nur lesen, nie wecken, das Lager nicht anfassen."""

    def test_lager_zeile(self):
        (self.puffer / "eingang" / "a.mp4").write_bytes(b"x")
        with self.lager_tabu():
            text = _status_text(self.con, self.konfig)
        self.assertRegex(text, r"🗄️ Puffer \d+ GB frei · Lager: noch kein Abgleich · 1 offen")
        self.assertEqual(self.geweckt, [])

    def test_lager_zeile_nach_abgleich(self):
        self.lauf("lager", "abgleich")
        self.geweckt.clear()
        with self.lager_tabu():
            text = _status_text(self.con, self.konfig)
        self.assertRegex(text, r"Lager: letzter Abgleich \d\d:\d\d ok · 0 offen")
        self.assertEqual(self.geweckt, [])

    def test_status_antwortet_auch_bei_fehler(self):
        with mock.patch.object(lager, "status", side_effect=OSError("kaputt")):
            text = _status_text(self.con, self.konfig)
        self.assertIn("Lager: Stand nicht lesbar (kaputt)", text)
        self.assertIn("📊", text)


if __name__ == "__main__":
    unittest.main()
