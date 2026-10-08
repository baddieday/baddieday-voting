"""Persistente PC-Berichte -> Telegram: Reihenfolge, Wiederholung und Teilerfolg."""
import asyncio
import json
from types import SimpleNamespace
from unittest import mock

from clip_pipeline import db, uebertragung
from clip_pipeline.bot import aktionen, app as bot_app
from tests.hilfen import MitSpeicher


class Uebertragung(MitSpeicher):
    def setUp(self):
        super().setUp()
        self.konfig.daten["lager"]["wurzel"] = str(self.tmp / "lager")
        self.ordner = self.konfig.wurzel / "sitzungen" / "uebertragung"
        self.ordner.mkdir(parents=True)
        self.bericht = {"id": "a" * 32, "start_utc": "2026-09-30T18:00:00Z", "ende_utc": None,
                        "status": "laeuft", "videos_geplant": 2, "videos_kopiert": 0,
                        "dateien_kopiert": 0, "fehler": 0}

    def schreibe(self):
        (self.ordner / (self.bericht["id"] + ".json")).write_text(json.dumps(self.bericht), encoding="utf-8-sig")

    def test_kurzer_lauf_sendet_start_ende_einmal_auch_nachts(self):
        self.bericht.update(ende_utc="2026-09-30T18:00:05Z", status="fertig", videos_kopiert=2, dateien_kopiert=4)
        self.schreibe()
        senden = mock.AsyncMock()
        app = SimpleNamespace(bot_data={"con": self.con, "konfig": self.konfig, "erlaubt": 42},
                              bot=SimpleNamespace(send_message=senden))
        db.meldung(self.con, "uebertragung:lager:1:start", "Lager gestartet")
        with mock.patch.object(aktionen, "ruhezeit", return_value=True):
            self.assertEqual(asyncio.run(bot_app.sende_meldungen(app)), 3)
            self.assertEqual(asyncio.run(bot_app.sende_meldungen(app)), 0)
        texte = [c.args[1] for c in senden.call_args_list]
        self.assertIn("gestartet", texte[1])
        self.assertIn("abgeschlossen", texte[2])
        self.assertIn("Videos: 2", texte[2])
        self.assertIn("Dateien insgesamt: 4", texte[2])
        self.assertTrue(all(c.kwargs["disable_notification"] for c in senden.call_args_list))
        self.assertTrue((self.ordner / ("a" * 32 + ".json")).exists())

    def test_still_einfach_nur_probleme(self):
        """Stufe 3 (08.10.): Clip-Bot still und Lern-Bot einfach – Start und glattes Ende werden nur vermerkt (vorher
        je Kopierlauf zwei Nachrichten), ein Lauf mit Fehlern kommt wie bisher."""
        self.konfig.daten["bot"]["clips_zeigen"] = False
        self.konfig.daten["auto_freigabe"]["modus"] = "an"
        self.bericht.update(ende_utc="2026-09-30T18:00:05Z", status="fertig", videos_kopiert=2, dateien_kopiert=4)
        self.schreibe()
        self.bericht.update(id="b" * 32, status="fehler", videos_kopiert=1, fehler=1)
        self.schreibe()
        senden = mock.AsyncMock()
        app = SimpleNamespace(bot_data={"con": self.con, "konfig": self.konfig, "erlaubt": 42},
                              bot=SimpleNamespace(send_message=senden))
        self.assertEqual(asyncio.run(bot_app.sende_meldungen(app)), 1)
        self.assertIn("mit Fehlern", senden.call_args.args[1])
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM meldungen WHERE gesendet IS NULL").fetchone()[0], 0)

    def test_teilerfolg_telegram_ausfall_und_kaputte_datei_verlieren_nichts(self):
        self.schreibe()
        self.assertEqual(uebertragung.hole_meldungen(self.con, self.konfig), 1)
        self.bericht.update(ende_utc="2026-09-30T18:03:00Z", status="fehler", videos_kopiert=1,
                            dateien_kopiert=2, fehler=1)
        self.schreibe()
        (self.ordner / ("b" * 32 + ".json")).write_text("{kaputt", encoding="utf-8")
        self.assertEqual(uebertragung.hole_meldungen(self.con, self.konfig), 1)
        senden = mock.AsyncMock(side_effect=OSError("Telegram offline"))
        app = SimpleNamespace(bot_data={"con": self.con, "konfig": self.konfig, "erlaubt": 42},
                              bot=SimpleNamespace(send_message=senden))
        with self.assertRaises(OSError):
            asyncio.run(bot_app.sende_meldungen(app))
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM meldungen WHERE gesendet IS NULL").fetchone()[0], 2)
        senden.side_effect = None
        self.assertEqual(asyncio.run(bot_app.sende_meldungen(app)), 2)
        ende = senden.call_args.args[1]
        self.assertIn("mit Fehlern", ende)
        self.assertIn("Videos: 1", ende)
        self.assertIn("Fehler: 1", ende)
        self.konfig.daten["lager"]["wurzel"] = ""
        with mock.patch("pathlib.Path.glob", side_effect=AssertionError("pve-big angefasst")):
            self.assertEqual(uebertragung.hole_meldungen(self.con, self.konfig), 0)
