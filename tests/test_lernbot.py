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


async def _merke(liste, text):
    liste.append(text)


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

    async def edit_message_reply_markup(self, **kw):
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
        self.assertTrue({"entwurf", "musik", "lernstand", "stand", "hilfe", "experte"} <= befehle)
        self.assertGreater(app.concurrent_updates, 0)   # 27.09.: Klicks warten nicht aufeinander
        app.bot_data["con"].close()

    def test_mehr_gruende_und_stand_kurz(self):
        """06.10.: „➕ mehr Gründe“ zeigt alle neun ohne die Bewertung zu ändern; 📋 Stand ist im einfachen Modus kurz."""
        self.momente_anlegen(MOMENTE[:6])
        self.musik_anlegen(150, "episch")
        e = regie.erstelle(self.con, self.konfig, "short")
        eid = e["entwurf"]
        q = self.klick(f"d:{eid}:-1")
        knoepfe = [b.callback_data for reihe in q.bearbeitet[0]["reply_markup"].inline_keyboard for b in reihe]
        self.assertIn(f"g:{eid}:mehr", knoepfe)
        self.assertNotIn(f"g:{eid}:hektisch", knoepfe)
        q = self.klick(f"g:{eid}:mehr")
        self.assertEqual(q.antworten, ["Alle Gründe"])
        knoepfe = [b.callback_data for reihe in q.bearbeitet[0]["reply_markup"].inline_keyboard for b in reihe]
        self.assertIn(f"g:{eid}:hektisch", knoepfe)
        self.assertEqual(self.con.execute("SELECT gruende FROM entwurf_bewertungen WHERE entwurf_id = ?", (eid,)).fetchone()[0], "[]")
        text = lernbot.stand_kurz(self.con, self.konfig)
        self.assertTrue(text.startswith("📋 Stand"))
        self.assertIn("👍/👎 von dir: 1 (0 👍 · 1 👎)", text)
        self.assertIn("⏱️ Shorts: Ziel 45 s", text)
        self.assertLessEqual(len(text.splitlines()), 8)
        antworten = []

        async def reply_text(text, **_):
            antworten.append(text)

        asyncio.run(lernbot.cmd_stand(SimpleNamespace(effective_message=SimpleNamespace(reply_text=reply_text)), self.context))
        self.assertEqual(antworten, [text])

    def test_bildunterschrift_zaehler_und_gelernt(self):
        zeile = {"id": 7}
        liste = {"format": "short", "dauer_s": 44, "stimmung": "episch", "bogen": [1, 2, 3],
                 "segmente": [{"moment": "a", "bewertet": 0}, {"moment": "b", "bewertet": 2},
                              {"moment": "b", "teil": 2, "bewertet": 2}, {"moment": "c", "bewertet": 1}],
                 "gelernt": {"entwurf": 6, "format": "zusammenschnitt", "aenderungen": ["3 Momente seltener"]}}
        text = lernbot.entwurf_text(zeile, liste)
        self.assertIn("🔁 Schon bewertet: ① neu ② 2× ③ 1×", text)
        self.assertIn("🧠 Aus #6 (Zusammenschnitt): 3 Momente seltener", text)
        alt = {**liste, "segmente": [{"moment": "a"}], "gelernt": None}           # alte Schnittliste: keine Zeilen
        self.assertNotIn("🔁", lernbot.entwurf_text(zeile, alt))

    def test_kurzbefehle_als_knoepfe_im_chat(self):
        # 27.09.: Knöpfe im Chat wie beim Bewerten (k:0:<ziel>), nicht die Tastatur ersetzen
        gesendet = []
        q = FakeQuery("k:0:lernstand")
        q.message = SimpleNamespace(reply_text=lambda t, **kw: _merke(gesendet, t))
        update = SimpleNamespace(callback_query=q, effective_message=q.message)
        asyncio.run(lernbot.bei_kurzknopf(update, self.context))
        self.assertEqual(q.antworten, [None])                                   # Spinner sofort weg
        self.assertTrue(gesendet[0].startswith("🧠 AUTONOMES LERNEN"))
        q = FakeQuery("k:0:zusammenschnitt")
        asyncio.run(lernbot.bei_kurzknopf(SimpleNamespace(callback_query=q, effective_message=None), self.context))
        self.assertEqual((q.antworten, len(self.aufgaben)), (["🎬 Zusammenschnitt kommt …"], 1))
        app = lernbot.baue_app(self.konfig, "123456:TEST", 42)
        muster = [h.pattern.pattern for h in app.handlers[0] if getattr(h, "callback", None) is lernbot.bei_kurzknopf]
        self.assertEqual(muster, ["^k:"])
        app.bot_data["con"].close()

    def test_telegram_ueber_ipv4(self):
        # 27.09.: über IPv6 blieb die Warteabfrage auf dem Mini hängen – Standard ist IPv4, abschaltbar
        bot, updates = lernbot.anfragen(self.konfig)
        self.assertIsNotNone(bot._client_kwargs["transport"])
        self.assertIsNotNone(updates._client_kwargs["transport"])
        self.konfig.daten.setdefault("lernbot", {})["nur_ipv4"] = False
        bot, _ = lernbot.anfragen(self.konfig)
        self.assertIsNone(bot._client_kwargs["transport"])

    def test_knopf_antwortet_vor_der_datenbank(self):
        # 27.09. („Buttons laden lange“): answerCallbackQuery geht raus, bevor Bewertung und Caption gebaut werden
        from unittest import mock

        self.momente_anlegen(MOMENTE[:8])
        self.musik_anlegen(150, "episch")
        eid = lernbot.baue_entwurf(self.konfig, "short")
        asyncio.run(lernbot.sende_entwuerfe(self.app))
        q = FakeQuery(f"d:{eid}:1")
        with mock.patch.object(regie_lernen, "bewerte", side_effect=AssertionError("Datenbank hängt")), \
                self.assertRaises(AssertionError):
            asyncio.run(lernbot.bei_klick(SimpleNamespace(callback_query=q), self.context))
        self.assertEqual(q.antworten, ["Danke! Gründe antippen (optional), dann ✅ fertig."])
        self.assertEqual(q.bearbeitet, [])
        q = self.klick(f"g:{eid}:nix")                    # unbekannter Grund: klare Antwort statt Absturz
        self.assertEqual(q.antworten, ["Unbekannter Knopf."])

    def test_entwurf_fail_baut_und_schickt_fail_video(self):
        # 05.10. (Fail-Format/🔥 Viral): /entwurf fail nimmt der Bot an, baut ein Fail-Video (Standbild, Hook) und
        # schickt es mit Viral-Zeile; „Nächster Entwurf“ führt wieder zu 🔥 Viral (der Bot wählt neu)
        self.context.args = ["fail"]
        asyncio.run(lernbot.cmd_entwurf(SimpleNamespace(effective_message=None), self.context))
        self.assertEqual(len(self.aufgaben), 1)
        self.konfig.daten["regie"]["effekte"]["an"] = True
        self.konfig.daten.setdefault("viral", {})["werkzeuge"] = {"hook_teaser": 1.0, "tod_lupe": 1.0,
                                                                   "tod_standbild": 1.0}
        self.fails_anlegen()
        self.musik_anlegen(120, "frustriert")
        eid = lernbot.baue_entwurf(self.konfig, "fail")
        zeile = self.con.execute("SELECT * FROM entwuerfe WHERE id = ?", (eid,)).fetchone()
        self.assertEqual((zeile["format"], zeile["variante"], zeile["status"]), ("short", "fail", "gerendert"))
        liste = json.loads(Path(zeile["schnittliste"]).read_text(encoding="utf-8"))
        self.assertEqual(liste["segmente"][0]["rolle"], "hook")
        self.assertTrue(any(s.get("standbild") for s in liste["segmente"]))
        asyncio.run(lernbot.sende_entwuerfe(self.app))
        video = self.bot.videos[0]
        self.assertIn("💀 Fail-Video", video["caption"])
        knoepfe = [b.callback_data for reihe in video["reply_markup"].inline_keyboard for b in reihe]
        self.assertIn("k:0:viral", knoepfe)

    def test_clip_auswahl_ein_match(self):
        # 29.09. (⚙️ Einstellungen): nur Momente aus dem gewählten Match; der Hinweis steht vorn im Entwurf
        from clip_pipeline import einstellungen
        from clip_pipeline.zeit import iso, jetzt

        # Genug Material innerhalb des gewählten Matches für einen zulässigen Short;
        # die beiden fremden Momente dürfen trotz ihrer vorhandenen Dateien nicht hineinkommen.
        self.momente_anlegen([(stimmung, serie, kills, "m2" if i < 6 else match)
                              for i, (stimmung, serie, kills, match) in enumerate(MOMENTE[:8])])
        self.musik_anlegen(150, "episch")
        for n in range(1, 5):
            self.con.execute("""INSERT INTO matches (id, replay_pfad, start_utc, ende_utc, erstellt, geaendert)
                                VALUES (?, ?, ?, ?, ?, ?)""", (f"m{n}", f"/r/m{n}.replay", f"2026-09-28T1{n}:00:00Z",
                                                               f"2026-09-28T1{n}:20:00Z", iso(jetzt()), iso(jetzt())))
        einstellungen.setze(self.con, "lernbot.quelle", "match:m2")
        eid = lernbot.baue_entwurf(self.konfig, "short")
        liste = json.loads(Path(self.con.execute("SELECT schnittliste FROM entwuerfe WHERE id = ?",
                                                 (eid,)).fetchone()[0]).read_text(encoding="utf-8"))
        ausgewaehlt = {s["moment"] for s in liste["segmente"]}
        self.assertTrue(ausgewaehlt <= {f"datei:{i}" for i in range(1, 7)}, ausgewaehlt)
        self.assertGreaterEqual(len(ausgewaehlt), 4)
        self.assertGreaterEqual(liste["dauer_s"], 30)
        self.assertLessEqual(liste["dauer_s"], 75)
        self.assertTrue(liste["hinweise"][0].startswith("🎯 nur Match"), liste["hinweise"])

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
        self.assertEqual(daten, [f"pk:{eid}:", "k:0:short", "k:0:stand", f"d:{eid}:1", f"d:{eid}:-1"])

        self.assertEqual(self.klick(f"d:{eid}:-1", von=7).antworten, ["Nicht erlaubt."])  # fremde Person
        q = self.klick(f"d:{eid}:-1")
        gruende = [b.callback_data for reihe in q.bearbeitet[0]["reply_markup"].inline_keyboard for b in reihe]
        self.assertIn(f"g:{eid}:musik", gruende)
        self.klick(f"g:{eid}:musik")
        q = self.klick(f"g:{eid}:hektisch")
        self.assertIn("☑️ 😵 zu hektisch", [b.text for reihe in q.bearbeitet[0]["reply_markup"].inline_keyboard for b in reihe])
        self.klick(f"g:{eid}:hektisch")  # abwählen
        q = self.klick(f"x:{eid}:")
        nach_fertig = [b.callback_data for reihe in q.bearbeitet[0]["reply_markup"].inline_keyboard for b in reihe]
        self.assertEqual(nach_fertig, [f"pk:{eid}:", *[d for reihe in lernbot.knoepfe_kurzbefehle() for _, d in reihe]])
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

    def test_neuer_entwurf_sortiert_automatisch_aus(self):
        # B5: pruefe_auto_verwerfen sagt "verwirf" für den ersten Versuch, der letzte Versuch kommt immer durch
        from unittest import mock
        self.momente_anlegen(MOMENTE[:8])
        self.musik_anlegen(150, "episch")
        self.konfig.daten["lernbot"].update(auto_schwelle=0.5, auto_versuche_max=2)
        with mock.patch.object(lernbot, "pruefe_auto_verwerfen", return_value="Erwartung 10 % unter Schwelle 50 %"):
            eid = asyncio.run(lernbot.neuer_entwurf(self.app, "short"))
        zeilen = self.con.execute("SELECT id, auto_verworfen FROM entwuerfe ORDER BY id").fetchall()
        self.assertEqual(len(zeilen), 2)
        self.assertIsNotNone(zeilen[0]["auto_verworfen"])   # erster Versuch: aussortiert
        self.assertIsNone(zeilen[1]["auto_verworfen"])      # letzter Versuch: kommt trotzdem durch
        self.assertEqual(eid, zeilen[1]["id"])
        self.assertEqual(len(self.bot.videos), 1)           # nur der zweite wurde geschickt
        self.assertTrue(any("automatisch aussortiert" in t for _, t in self.bot.texte))

    def test_ohne_auto_schwelle_wie_bisher(self):
        self.momente_anlegen(MOMENTE[:8])
        self.musik_anlegen(150, "episch")   # auto_schwelle 0.0 (Standard) = aus
        asyncio.run(lernbot.neuer_entwurf(self.app, "short"))
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM entwuerfe").fetchone()[0], 1)
        self.assertEqual(len(self.bot.videos), 1)
        self.assertFalse(any("automatisch aussortiert" in t for _, t in self.bot.texte))

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
            eid = lernbot.baue_entwurf(self.konfig, "short")
        self.assertTrue(eid)                                                # Entwurf kommt trotzdem
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM entwuerfe").fetchone()[0], 3)
        # 27.09.: der Fehler steht als erster Hinweis in der Schnittliste – der Bot zeigt ihn (vorher nur im Log)
        zeile = self.con.execute("SELECT schnittliste FROM entwuerfe WHERE id = ?", (eid,)).fetchone()
        with open(zeile["schnittliste"], encoding="utf-8") as f:
            hinweise = json.load(f)["hinweise"]
        self.assertTrue(hinweise[0].startswith("Stimmung nachziehen fehlgeschlagen: RuntimeError: Whisper kaputt"), hinweise)

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
        self.assertTrue(self.bot.texte[0][1].startswith("📋 Stand"))   # 06.10.: im einfachen Modus die kurze Fassung
        self.assertEqual(len(self.bot.texte), 1 + 3)  # Bericht in 3 Stücke geteilt
        self.assertTrue(all(len(t) <= lernbot.TEXT_MAX for _, t in self.bot.texte))


@unittest.skipIf(lernbot is None, "python-telegram-bot fehlt")
class EntwurfText(unittest.TestCase):
    def test_bilanz_cooldown_und_ohne_datei(self):
        # 27.09.: der Bot zeigt, was der Regisseur wirklich sah – Cooldown und fehlende Dateien waren vorher unsichtbar
        liste = {"format": "short", "dauer_s": 40.0, "stimmung": "episch", "segmente": [{"moment": "clip:1"}],
                 "bogen": [9.0], "musik": None, "hinweise": [],
                 "auswahl": {"neu": 1, "schon_gezeigt": 0, "kandidaten": 120, "gesperrt": 14, "ohne_datei": 2}}
        self.assertIn("Auswahl aus 120 Momenten · 14 im Cooldown · 2 ohne Datei", lernbot.entwurf_text({"id": 8}, liste))

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

    def test_einfacher_modus_gruende_und_text(self):
        """06.10. (Florian: „das wird alles zu kompliziert“): einfach = vier Gründe + „➕ mehr“, kurzer Entwurfstext;
        ein schon gewählter Experten-Grund zeigt wieder alle neun."""
        eid = 10 ** 12
        reihen = lernbot.knoepfe_gruende(eid, [], kurz=True)
        self.assertEqual([len(r) for r in reihen], [2, 2, 2])
        self.assertEqual([d for r in reihen for _t, d in r],
                         [f"g:{eid}:kurz", f"g:{eid}:langweilig", f"g:{eid}:effekte_viel", f"g:{eid}:musik",
                          f"g:{eid}:mehr", f"x:{eid}:"])
        self.assertEqual(len([k for r in lernbot.knoepfe_gruende(eid, ["action"], kurz=True) for k in r]), 10)  # alle 9 + ✅
        self.assertEqual(lernbot.knoepfe_kurzbefehle(), [[("🎬 Neues Video", "k:0:viral")],
                                                          [("📋 Stand", "k:0:stand"), ("⚙️ Einstellungen", "k:0:einstellungen")]])
        self.assertEqual(len([k for r in lernbot.knoepfe_kurzbefehle(experte=True) for k in r]), 9)
        liste = {"format": "short", "dauer_s": 47.0, "stimmung": "episch", "bogen": [1, 3, 2],
                 "segmente": [{"moment": "a", "bewertet": 2}, {"moment": "b", "bewertet": 0}],
                 "auswahl": {"neu": 1, "schon_gezeigt": 1, "kandidaten": 30, "gesperrt": 9, "ohne_datei": 1},
                 "parameter": {"stil": "kino", "rahmen_zoom": 1.3, "autonom": {"version": 2, "confidence": 0.4}},
                 "effekte": {"an": True, "look": "neutral"}, "gelernt": {"entwurf": 41, "aenderungen": []},
                 "hinweise": ["a", "b", "c"]}
        kurz = lernbot.entwurf_text({"id": 7}, liste, erwartung=0.8, kritik_text="🧐 Cutter 61", kurz=True)
        for weg in ("🔮", "🧐", "🎬 Stil", "✨ Look", "🧠 Publikum", "🔁", "im Cooldown", "ohne Datei", "⚠️ c"):
            self.assertNotIn(weg, kurz)
        self.assertIn("🆕 1 neue · 1 schon gezeigt", kurz)
        self.assertIn("⚠️ b", kurz)
        voll = lernbot.entwurf_text({"id": 7}, liste, erwartung=0.8, kritik_text="🧐 Cutter 61")
        self.assertIn("🔮", voll)                                               # Experten-Modus wie bisher

    def test_knoepfe_gruende_reihen(self):
        eid = 10 ** 12
        reihen = lernbot.knoepfe_gruende(eid, ["action"])
        self.assertEqual([len(r) for r in reihen], [2, 2, 2, 2, 2])       # 9 Gründe, der letzte neben ✅
        self.assertEqual(reihen[-1], [("⏱️ zu kurz", f"g:{eid}:kurz"), ("✅ fertig", f"x:{eid}:")])
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



# --- Paket E der Stufe 2: Erwartung im Lern-Bot (Spec §10.5) -------------------------------------------------------

from unittest import mock  # noqa: E402

from clip_pipeline import erwartung  # noqa: E402

from tests.test_erwartung import MitErwartung  # noqa: E402


@unittest.skipIf(lernbot is None, "python-telegram-bot fehlt")
class ErwartungImLernBot(MitErwartung):
    """Festschreiben VOR send_video, Edit nach dem Klick liest nur. Entwürfe werden direkt angelegt (ohne ffmpeg)."""

    def setUp(self):
        super().setUp()
        self.bot = FakeBot()
        self.beim_senden = []
        senden = self.bot.send_video

        async def send_video(**kw):
            eid = int(kw["caption"].split("Entwurf #")[1].split("<")[0])
            self.beim_senden.append(erwartung.gespeichert(self.con, "entwurf", eid))
            return await senden(**kw)

        self.bot.send_video = send_video
        # 06.10.: die Erwartungs-Zeile steht nur noch im Experten-Modus in der Bildunterschrift
        self.konfig.daten.setdefault("lernbot", {})["experte"] = True
        self.app = SimpleNamespace(bot=self.bot, bot_data={"con": self.con, "konfig": self.konfig, "erlaubt": 42},
                                   create_task=lambda koro: koro.close())
        self.context = SimpleNamespace(bot_data=self.app.bot_data, application=self.app, args=[])
        self.moment("datei:1", {"max_gruppe": 3})   # 6 Punkte
        self.moment("datei:2", {"max_gruppe": 1})   # 1 Punkt

    def gerendert(self, momente: list[str]) -> int:
        eid = self.entwurf(momente, status="gerendert")
        liste = json.loads(Path(self.con.execute("SELECT schnittliste FROM entwuerfe WHERE id = ?",
                                                 (eid,)).fetchone()[0]).read_text(encoding="utf-8"))
        liste.update(dauer_s=30.0, stimmung="episch", bogen=[6.0], musik=None, hinweise=["Musik fehlt"])
        Path(self.con.execute("SELECT schnittliste FROM entwuerfe WHERE id = ?", (eid,)).fetchone()[0]).write_text(
            json.dumps(liste), encoding="utf-8")
        video = self.tmp / f"entwurf{eid}.mp4"
        video.write_bytes(b"video")
        self.con.execute("UPDATE entwuerfe SET datei = ? WHERE id = ?", (str(video), eid))
        return eid

    def urteile_entwuerfe(self, n: int = 10) -> None:
        for i in range(n):
            gut = i % 2 == 0
            self.entwurf(["datei:1" if gut else "datei:2"], daumen=1 if gut else -1,
                         fmt="short" if i % 3 else "zusammenschnitt")

    def wahrschein(self, eid: int) -> float:
        return self.con.execute("SELECT wahrschein FROM erwartungen WHERE art = 'entwurf' AND ziel_id = ?",
                                (eid,)).fetchone()[0]

    def test_senden_schreibt_fest_klick_liest_nur(self):
        self.urteile_entwuerfe()
        eid = self.gerendert(["datei:1"])
        self.assertEqual(asyncio.run(lernbot.sende_entwuerfe(self.app)), 1)
        p = self.wahrschein(eid)
        self.assertEqual(self.beim_senden, [p])
        self.assertGreater(p, 0.5)
        zeile = f"Erwartung: 👍 {round(100 * p)} %"
        caption = self.bot.videos[0]["caption"]
        self.assertIn(zeile, caption)
        self.assertLess(caption.index(zeile), caption.index("⚠️ Musik fehlt"))  # vor den Hinweisen

        for _ in range(15):  # das Modell ändert sich …
            self.entwurf(["datei:1"], daumen=-1)
        self.assertEqual(asyncio.run(lernbot.sende_entwuerfe(self.app)), 0)   # … zweites Senden: nichts
        q = FakeQuery(f"d:{eid}:1")
        with mock.patch.object(erwartung, "festschreiben", side_effect=AssertionError("Klick darf nicht rechnen")):
            asyncio.run(lernbot.bei_klick(SimpleNamespace(callback_query=q), self.context))
        self.assertIn(zeile, q.bearbeitet[0]["caption"])
        self.assertIn("Bewertet: 👍", q.bearbeitet[0]["caption"])
        self.assertEqual(self.wahrschein(eid), p)                              # … die Erwartung nicht

    def test_unter_mindest_urteilen_noch_keine(self):
        self.urteile_entwuerfe(9)
        eid = self.gerendert(["datei:1"])
        asyncio.run(lernbot.sende_entwuerfe(self.app))
        self.assertIn("Erwartung: noch keine", self.bot.videos[0]["caption"])
        self.assertEqual(self.beim_senden, [None])
        self.assertIsNone(erwartung.gespeichert(self.con, "entwurf", eid))

    def test_fehler_in_der_erwartung_haelt_das_senden_nicht_auf(self):
        self.urteile_entwuerfe()
        self.gerendert(["datei:1"])
        with mock.patch.object(erwartung, "festschreiben", side_effect=ValueError("kaputt")), \
                self.assertLogs("lern-bot", "ERROR"):
            self.assertEqual(asyncio.run(lernbot.sende_entwuerfe(self.app)), 1)
        self.assertIn("Erwartung: noch keine", self.bot.videos[0]["caption"])

    def test_lernstand_mit_trefferquote(self):
        eid = self.entwurf(["datei:1"], daumen=-1)
        self.con.execute("INSERT INTO erwartungen (art, ziel_id, wahrschein, grundlage, erstellt)"
                         " VALUES ('entwurf', ?, 0.3, '{}', 'x')", (eid,))
        antworten = []

        async def reply_text(text, **_):
            antworten.append(text)

        update = SimpleNamespace(effective_message=SimpleNamespace(reply_text=reply_text))
        asyncio.run(lernbot.cmd_lernstand(update, self.context))
        self.assertTrue(antworten[0].startswith("🧠 AUTONOMES LERNEN"))
        self.assertIn("Bewertungen sind optional", antworten[0])

    def test_lernstand_ohne_quote_unveraendert(self):
        antworten = []

        async def reply_text(text, **_):
            antworten.append(text)

        update = SimpleNamespace(effective_message=SimpleNamespace(reply_text=reply_text))
        asyncio.run(lernbot.cmd_lernstand(update, self.context))
        self.assertEqual(antworten, [lernbot.autonom_text(self.con)])


@unittest.skipIf(lernbot is None, "python-telegram-bot fehlt")
class AutoVerwerfen(ErwartungImLernBot):
    """B5 ([lernbot].auto_schwelle): pruefe_auto_verwerfen entscheidet, ohne etwas in erwartungen zu speichern –
    das bleibt festschreiben beim tatsächlichen Senden vorbehalten (sonst zählt die Erwartung ihr eigenes
    still-verworfenes Urteil als Treffer)."""

    def test_unter_schwelle_nennt_den_grund(self):
        self.urteile_entwuerfe()
        self.konfig.daten["lernbot"]["auto_schwelle"] = 0.9   # so hoch, dass ein Double-Kill-Entwurf durchfällt
        eid = self.gerendert(["datei:1"])
        grund = lernbot.pruefe_auto_verwerfen(self.con, self.konfig, eid)
        self.assertIsNotNone(grund)
        self.assertIn("unter Schwelle 90 %", grund)
        self.assertIsNone(erwartung.gespeichert(self.con, "entwurf", eid))  # nichts gespeichert

    def test_schwelle_aus_laesst_immer_durch(self):
        self.urteile_entwuerfe()
        eid = self.gerendert(["datei:2"])   # der schwache Moment – ohne Schwelle trotzdem None
        self.assertIsNone(lernbot.pruefe_auto_verwerfen(self.con, self.konfig, eid))

    def test_ohne_modell_laesst_immer_durch(self):
        self.konfig.daten["lernbot"]["auto_schwelle"] = 0.9
        eid = self.gerendert(["datei:1"])   # nur 0 Urteile bisher – keine Erwartung möglich
        self.assertIsNone(lernbot.pruefe_auto_verwerfen(self.con, self.konfig, eid))


@unittest.skipIf(lernbot is None, "python-telegram-bot fehlt")
class EntwurfTextErwartung(unittest.TestCase):
    LISTE = {"format": "short", "dauer_s": 32.0, "stimmung": "episch", "segmente": [{"moment": "clip:1"}],
             "bogen": [9.0], "musik": None, "hinweise": ["x" * 400] * 5}

    def test_zeile_vor_den_hinweisen_und_nie_abgeschnitten(self):
        text = lernbot.entwurf_text({"id": 7}, self.LISTE, erwartung=0.8)
        self.assertIn("Erwartung: 👍 80 %", text)
        self.assertLess(text.index("Erwartung"), text.index("⚠️"))
        self.assertLessEqual(len(text), 1000)
        self.assertIn("Erwartung: 👎 70 %", lernbot.entwurf_text({"id": 7}, self.LISTE, erwartung=0.3))

    def test_ohne_erwartung_noch_keine(self):
        text = lernbot.entwurf_text({"id": 7}, self.LISTE)
        self.assertIn("Erwartung: noch keine", text)
        self.assertLess(text.index("Erwartung"), text.index("⚠️"))


if __name__ == "__main__":
    unittest.main()
