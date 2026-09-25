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
        self.aufgaben = []
        self.app = SimpleNamespace(bot=self.bot, bot_data={"con": self.con, "konfig": self.konfig, "erlaubt": 42},
                                   create_task=lambda koro: self.aufgaben.append(koro))
        self.context = SimpleNamespace(bot_data=self.app.bot_data, application=self.app, args=[])

    def tearDown(self):
        for koro in self.aufgaben:  # nicht ausgeführte Lernschleifen-Aufgaben schließen (sonst RuntimeWarning)
            koro.close()
        super().tearDown()

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
        self.assertIn("🆕 ", video["caption"])  # wie viele Momente neu sind
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

    def test_gleichzeitig_senden_nur_einmal(self):
        self.momente_anlegen(MOMENTE[:6])
        lernbot.baue_entwurf(self.konfig, "short")

        async def beide():
            return await asyncio.gather(lernbot.sende_entwuerfe(self.app), lernbot.sende_entwuerfe(self.app))

        self.assertEqual(sorted(asyncio.run(beide())), [0, 1])
        self.assertEqual(len(self.bot.videos), 1)

    def test_nach_bewertung_kommt_der_naechste(self):
        self.momente_anlegen(MOMENTE[:8])
        self.musik_anlegen(150, "episch")
        eid = lernbot.baue_entwurf(self.konfig, "short")
        asyncio.run(lernbot.sende_entwuerfe(self.app))
        self.klick(f"d:{eid}:1")
        q = self.klick(f"x:{eid}:")
        self.assertIn("nächste Entwurf kommt gleich", q.antworten[0])
        self.assertEqual(len(self.aufgaben), 1)
        asyncio.run(self.aufgaben[0])                              # Lernschleife läuft
        self.assertEqual(len(self.bot.videos), 2)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM entwuerfe").fetchone()[0], 2)
        self.assertEqual(list((self.konfig.wurzel / ".aktiv").glob("*")) if (self.konfig.wurzel / ".aktiv").exists() else [], [])

    def test_vor_dem_entwurf_weitere_clips_analysieren(self):
        from unittest import mock
        self.momente_anlegen(MOMENTE[:8])
        with mock.patch.object(lernbot.stimmung, "analysiere",
                               return_value={"analysiert": 3, "stimmungen": {"episch": 3}}) as analyse:
            lernbot.baue_entwurf(self.konfig, "short")
        self.assertEqual((analyse.call_args.kwargs["claude"], analyse.call_args.kwargs["maximal"]), (False, 10))
        self.konfig.daten["lernbot"] = {"stimmung_je_entwurf": 0}
        with mock.patch.object(lernbot.stimmung, "analysiere") as analyse:
            lernbot.baue_entwurf(self.konfig, "short")
        analyse.assert_not_called()
        self.konfig.daten["lernbot"] = {"stimmung_je_entwurf": 5}
        with mock.patch.object(lernbot.stimmung, "analysiere", side_effect=RuntimeError("Whisper kaputt")), \
                self.assertLogs("lern-bot", "ERROR"):
            self.assertTrue(lernbot.baue_entwurf(self.konfig, "short"))  # Entwurf kommt trotzdem
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM entwuerfe").fetchone()[0], 3)

    def test_blick_auf_leerlauf_haelt_die_schleife_nicht_auf(self):
        import threading
        from unittest import mock
        frei, aufrufe = threading.Event(), []

        def haengt(konfig):  # wie ein NFS-Lesezugriff, während pve-big ausgeht
            aufrufe.append(1)
            frei.wait(5)

        async def ablauf():
            with mock.patch.object(lernbot.big, "merke_leerlauf", side_effect=haengt):
                lernbot.blick_auf_leerlauf(self.app)       # kehrt sofort zurück
                await asyncio.sleep(0.05)
                lernbot.blick_auf_leerlauf(self.app)       # der erste hängt noch -> kein zweiter Faden
                await asyncio.sleep(0.05)
                self.assertEqual(len(aufrufe), 1)
                frei.set()
                await self.app.bot_data["leerlauf_blick"]
                lernbot.blick_auf_leerlauf(self.app)
                await self.app.bot_data["leerlauf_blick"]
                self.assertEqual(len(aufrufe), 2)

        asyncio.run(ablauf())

    def test_schlafender_speicher_ohne_erlaubnis_klare_meldung(self):
        (self.konfig.wurzel / ".clip-speicher").unlink()
        self.konfig.daten["speicher"].update(host="192.0.2.1", wol_mac="aa:bb:cc:dd:ee:ff")
        from unittest import mock
        with mock.patch("clip_pipeline.konfig.Konfig._host_erreichbar", return_value=False), \
                mock.patch("clip_pipeline.konfig.sende_wake_on_lan") as wol:
            self.assertIsNone(asyncio.run(lernbot.neuer_entwurf(self.app, "short")))
        wol.assert_not_called()                                    # ohne gesichertes Aus kein Wecken
        texte = [t for _, t in self.bot.texte]
        self.assertIn("pve-big schläft", texte[0])
        self.assertIn("nicht geweckt", texte[1])
        self.assertFalse(self.app.bot_data["arbeitet"])

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


@unittest.skipIf(lernbot is None, "python-telegram-bot fehlt")
class EntwurfText(unittest.TestCase):
    def test_zaehlt_momente_nicht_segmente(self):
        # Ein Multikill mit Jump-Cut besteht aus mehreren Segmenten (Teilen), bleibt aber ein Moment
        segmente = [{"moment": "clip:1", "teil": 1}, {"moment": "clip:1", "teil": 2}, {"moment": "datei:4"}]
        liste = {"format": "short", "dauer_s": 32.0, "stimmung": "episch", "segmente": segmente, "bogen": [9.0, 4.0],
                 "auswahl": {"neu": 2, "schon_gezeigt": 0, "kandidaten": 12}, "musik": None, "hinweise": []}
        text = lernbot.entwurf_text({"id": 7}, liste)
        self.assertIn("· 2 Momente", text)
        self.assertIn("🆕 2 neue · 0 schon gezeigt · Auswahl aus 12 Momenten", text)
        self.assertNotIn("✨", text)                                     # version 3 / ohne Effekte

    def test_effekte_zeile(self):
        segmente = [{"moment": "clip:1", "teil": 1, "effekte": [{"art": "punch"}, {"art": "sfx"}]},
                    {"moment": "clip:1", "teil": 2, "lupe": {"ab_s": 1.0, "bis_s": 1.5, "faktor": 0.5}},
                    {"moment": "datei:4", "effekte": [{"art": "titel"}]}, {"moment": "clip:1", "rolle": "hook"}]
        liste = {"format": "short", "dauer_s": 32.0, "stimmung": "episch", "segmente": segmente, "bogen": [9.0, 4.0],
                 "musik": None, "hinweise": ["x" * 400] * 5,
                 "effekte": {"an": True, "look": "cinematic", "look_staerke": 0.8, "hook": True, "loop": False}}
        text = lernbot.entwurf_text({"id": 7}, liste)
        self.assertIn("· 2 Momente", text)                               # Hook und Teile zählen nicht extra
        self.assertIn("✨ Look cinematic · 3 Impacts · Hook ✓ · Zeitlupe ✓", text)
        self.assertLessEqual(len(text), 1000)                            # Bildunterschrift
        segmente[1].pop("lupe")
        liste["effekte"].update(hook=False, look="neutral")
        self.assertIn("✨ Look neutral · 3 Impacts\n", lernbot.entwurf_text({"id": 7}, liste))
        liste["effekte"] = {"an": False}
        self.assertNotIn("✨", lernbot.entwurf_text({"id": 7}, liste))

    def test_knoepfe_gruende_vier_reihen(self):
        eid = 10 ** 12
        reihen = lernbot.knoepfe_gruende(eid, ["action"])
        self.assertEqual([len(r) for r in reihen], [2, 2, 2, 2, 1])       # 8 Gründe, dann ✅
        self.assertEqual(reihen[-1], [("✅ fertig", f"x:{eid}:")])
        self.assertEqual(reihen[3], [("🎆 zu viele Effekte", f"g:{eid}:effekte_viel"),
                                     ("☑️ 💥 mehr Action", f"g:{eid}:action")])
        gesehen = []
        for text, daten in (k for reihe in reihen for k in reihe):
            self.assertLessEqual(len(daten.encode()), 64)                 # Telegram: callback_data ≤ 64 Byte
            aktion, zurueck, extra = lernbot.parse(daten)
            self.assertEqual(zurueck, eid)
            if aktion == "g":
                gesehen.append(extra)
        self.assertEqual(gesehen, list(regie_lernen.GRUENDE))


if __name__ == "__main__":
    unittest.main()
