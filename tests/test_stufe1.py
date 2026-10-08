"""Stufe 1 (07.10., Florian: „Bot macht alles … Impact wird nicht gewertet … lieber kein Video … zu viele
Nachrichten“): deine Gründe als feste Regeln, nur starke Szenen, Songs wechseln, Abend-Video mit Statuszeile,
✅/❌ im Lern-Bot, Clip-Bot still."""

import asyncio
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from clip_pipeline import einstellungen, regeln, regie, regie_lernen, sitzung
from clip_pipeline.zeit import iso, jetzt

from tests.hilfen import HAT_FFMPEG, MitSpeicher
from tests.regie_hilfen import MOMENTE, MitRegieMaterial

try:
    from clip_pipeline import lernbot
    from clip_pipeline.bot import app as bot_app
except ImportError:  # python-telegram-bot fehlt
    lernbot = bot_app = None

LISTE = {"format": "short", "stimmung": "episch", "bogen": [6.0, 1.0, 3.0, 0.5],
         "parameter": {"ziel_dauer_s": 45.0}, "dauer_s": 46.0, "musik": {"track_id": 3, "titel": "Song A"},
         "segmente": [{"moment": "a", "intensitaet": 6.0, "rolle": "hook", "match_id": "m1"},
                      {"moment": "a", "intensitaet": 6.0, "match_id": "m1"}, {"moment": "b", "intensitaet": 1.0},
                      {"moment": "c", "intensitaet": 3.0, "match_id": "m2"}, {"moment": "d", "intensitaet": 0.5}]}
# MOMENTE: Serie ≥ 2 haben datei:1, 2, 3, 5, 12, 16 – die übrigen zehn sind Einzelkills oder ohne Kill
STARK = {"datei:1", "datei:2", "datei:3", "datei:5", "datei:12", "datei:16"}


class Regeln(MitRegieMaterial):
    def k(self):
        return einstellungen.anwenden(self.con, self.konfig)

    def test_jeder_grund_wirkt_sofort_und_sichtbar(self):
        self.konfig.daten["regie"]["effekte"]["an"] = True    # wie ab Werk – „aus“ zählt seit 08.10. als Stufe „aus“
        self.assertIn("55 s lang (vorher 45 s)", regeln.wende_an(self.con, self.k(), "kurz", LISTE))
        self.assertIn("65 s lang (vorher 55 s)", regeln.wende_an(self.con, self.k(), "kurz", LISTE))  # wirkt jedes Mal
        self.assertIn("55 s lang (vorher 65 s)", regeln.wende_an(self.con, self.k(), "lang", LISTE))
        self.assertIn("2 schwächeren tausche ich gegen neue", regeln.wende_an(self.con, self.k(), "langweilig", LISTE))
        self.assertEqual(regeln.gesperrt(self.con, "moment"), set())                  # 🥱 sperrt nichts (07.10.)
        self.assertEqual(regeln.langweilig_teilung(LISTE), (["a", "c"], ["b", "d"]))
        self.assertIn("„Song A“ spiele ich nie wieder", regeln.wende_an(self.con, self.k(), "musik", LISTE))
        self.assertEqual(regeln.gesperrt(self.con, "track"), {"3"})
        self.assertIn("„ruhig“", regeln.wende_an(self.con, self.k(), "hektisch", LISTE))
        p = regeln.anwenden(self.con, self.k(), "short", dict(regie.PARAMETER))
        self.assertEqual((p["ziel_dauer_s"], p["effekt_hektik"], p["musik_rotation"]), (55.0, 0.5, regeln.MUSIK_ROTATION))
        self.assertIn("„aus“", regeln.wende_an(self.con, self.k(), "hektisch", LISTE))
        self.assertFalse(self.k().wert("regie.effekte.an"))                                    # Stufe „aus“
        self.assertIn("Shorts 55 s · Effekte aus", regeln.regeln_zeile(self.con, self.k()))
        self.assertEqual(regeln.matches_aus(LISTE), {"m1", "m2"})

    def test_grenze_der_short_laenge(self):
        einstellungen.setze(self.con, regeln.ZIEL_SCHLUESSEL, 75.0)
        self.assertIn("geht bei Shorts nicht", regeln.wende_an(self.con, self.k(), "kurz", LISTE))
        self.assertEqual(regeln.ziel_regel(self.con, self.konfig), 75.0)

    def test_alter_schalter_effekte_aus_ist_stufe_aus(self):
        """08.10.: „✨ Effekte aus“ (⚙️ vom 06.10.) ohne Stufe ist Stufe „aus“ – vorher stand im 📋 Stand „Effekte
        normal“, und „😵 Zu hektisch“ schaltete die Effekte wieder EIN."""
        self.konfig.daten["regie"]["effekte"]["an"] = True                                # Datei: an (ab Werk)
        einstellungen.setze(self.con, "regie.effekte.an", False)                          # alter ⚙️-Schalter
        self.assertEqual(regeln.stufe(self.con, self.k()), 0)
        self.assertIn("Effekte aus", regeln.regeln_zeile(self.con, self.k()))
        self.assertIn("schon auf „aus“", regeln.wende_an(self.con, self.k(), "hektisch", LISTE))
        self.assertFalse(self.k().wert("regie.effekte.an"))                                # bleibt aus
        einstellungen.zuruecksetzen(self.con, regeln.STUFE_SCHLUESSEL)
        einstellungen.zuruecksetzen(self.con, "regie.effekte.an")                         # ohne Schalter: wie bisher
        self.assertIsNone(regeln.stufe(self.con, self.k()))
        self.assertIn("Effekte normal", regeln.regeln_zeile(self.con, self.k()))


class KeinVideoText(unittest.TestCase):
    def test_ohne_einstellungs_tipp_im_einfachen_modus(self):
        """08.10.: Im einfachen Modus kein „⚙️ → …“ mehr (umstellen kannst du dort nichts); unter /experte wie bisher."""
        z = regie.ZuWenigSzenen(2, 4, 6)                                      # mit Einzelkills ginge es
        einfach = sitzung.kein_video_text("06.10.", z, offen=1, experte=False)
        self.assertNotIn("⚙️", einfach)
        self.assertIn("1 Match war noch nicht fertig.", einfach)              # vorher „kam nie bei mir an“
        self.assertIn("⚙️ → 🎯 Szenen", sitzung.kein_video_text("06.10.", z, experte=True))
        self.assertEqual(z.satz(experte=False), "🎬 Kein Video: nur 2 starke Szenen (Multikill, Victory, Clutch oder "
                                                "Endkampf), ein Video braucht 4.")         # 🎬 Neues Video
        kurz = regie.ZuKurz(20.0, 30.0, 4, 3, nur_starke=True)                 # Lern-Bot: Clips des Abends angeschaut
        kurz.quelle = "🎯 nur Spielabend 06.10. (3 Matches)"
        self.assertEqual(kurz.tipp(experte=False), "Angeschaut habe ich: Spielabend 06.10. (3 Matches).")
        self.assertIn("Andere Auswahl: ⚙️ → 🎯 Clips.", kurz.satz())                   # Experte: wie bisher


@unittest.skipUnless(HAT_FFMPEG, "ffmpeg fehlt")
class StarkeSzenen(MitRegieMaterial):
    def setUp(self):
        super().setUp()
        self.konfig.daten["regie"]["szenen"] = "stark"
        self.momente_anlegen(MOMENTE)
        self.musik_anlegen(150, "episch")

    def baue(self):
        k = einstellungen.anwenden(self.con, self.konfig)
        p = regeln.anwenden(self.con, k, "short", regie_lernen.aktuelle(self.con, k, "short")[0])
        e = regie.erstelle(self.con, k, "short", parameter=p)
        return json.loads(Path(e["datei"]).read_text(encoding="utf-8"))

    def test_nur_starke_szenen_und_gesperrte_nie(self):
        liste = self.baue()
        self.assertTrue({s["moment"] for s in liste["segmente"]} <= STARK)
        regeln.sperre(self.con, "moment", ["datei:1", "datei:2", "datei:3"], "langweilig")   # alte 🥱-Sperre
        self.assertIn("datei:1", {s["moment"] for s in self.baue()["segmente"]})          # wirkt seit 07.10. nicht
        regeln.sperre(self.con, "moment", ["datei:5", "datei:12", "datei:16"], "von_hand")    # andere Sperren schon
        with self.assertRaises(regie.ZuWenigSzenen) as fehler:                            # nur noch 3 starke
            self.baue()
        self.assertEqual((fehler.exception.stark, fehler.exception.mindestens), (3, 4))

    def test_song_wechselt_und_gesperrter_nie(self):
        self.musik_anlegen(140, "episch", name="Zwei")           # anderes Tempo: gleicher Klick wäre derselbe Titel
        self.musik_anlegen(160, "episch", name="Drei")
        songs = [self.baue()["musik"]["track_id"] for _ in range(3)]
        self.assertEqual(len(set(songs)), 3)                                               # drei Videos, drei Songs
        regeln.sperre(self.con, "track", [str(songs[0])], "musik")
        self.assertNotIn(songs[0], [self.baue()["musik"]["track_id"] for _ in range(3)])


@unittest.skipUnless(HAT_FFMPEG, "ffmpeg fehlt")
class Abend(MitRegieMaterial):
    def abend(self, name, matches):
        for m in matches:
            self.con.execute("INSERT INTO matches (id, replay_pfad, start_utc, ende_utc, status, erstellt, geaendert) "
                             "VALUES (?, ?, 'x', 'x', 'verarbeitet', 'x', 'x')", (m, f"replays/{m}.replay"))
        ordner = self.konfig.wurzel / "sitzungen"
        ordner.mkdir(exist_ok=True)
        (ordner / f"{name}.json").write_text(json.dumps({"session": name, "matches": matches,
                                                         "ende_utc": iso(jetzt())}), encoding="utf-8")
        return sitzung.verarbeite(self.con, self.konfig, claude=False, whisper=False)["neu"][0]

    def meldung(self, schluessel):
        z = self.con.execute("SELECT text FROM lern_meldungen WHERE schluessel = ?", (schluessel,)).fetchone()
        return z["text"] if z else None

    def test_video_aus_starken_szenen_sonst_klare_zeile(self):
        self.konfig.daten["regie"]["szenen"] = "stark"
        self.momente_anlegen([(s, 3, [5.0, 7.0, 9.0], "a1") for s in ("episch", "spannend", "episch", "spannend")]
                             + [("chill", 1, [8.0], "b1")] * 5)
        self.musik_anlegen(150, "episch")
        neu = self.abend("session_2026-10-06_23-00-00", ["a1"])
        self.assertIsNotNone(neu["entwurf"])                                               # 4 starke Szenen: Video
        self.assertIn("ich baue dein Video", self.meldung("abend:session_2026-10-06_23-00-00"))
        # Rendern unterbrochen (Update, Neustart): der nächste Timer-Lauf baut das Video fertig, statt es zu vergessen
        self.con.execute("UPDATE entwuerfe SET status = 'neu', datei = NULL WHERE id = ?", (neu["entwurf"],))
        e = sitzung.verarbeite(self.con, self.konfig, claude=False, whisper=False)
        self.assertEqual(e["nachgeholt"], ["session_2026-10-06_23-00-00"])
        self.assertEqual(self.con.execute("SELECT status FROM entwuerfe WHERE id = ?", (neu["entwurf"],)).fetchone()[0],
                         "gerendert")
        neu = self.abend("session_2026-10-07_23-00-00", ["b1"])                            # nur Einzelkills
        self.assertIsNone(neu["entwurf"])
        text = self.meldung("kein:session_2026-10-07_23-00-00")
        self.assertIn("keine starke Szene", text)
        self.assertIn("kein Video", text)


class FakeBot:
    def __init__(self):
        self.texte, self.bearbeitet, self.geloescht, self.videos = [], [], [], []

    async def send_message(self, chat, text, **kw):
        self.texte.append(text)
        return SimpleNamespace(message_id=500 + len(self.texte))

    async def edit_message_text(self, text, **kw):
        self.bearbeitet.append((kw["message_id"], text))

    async def delete_message(self, chat, message_id):
        self.geloescht.append(message_id)

    async def send_video(self, **kw):
        self.videos.append(kw)
        return SimpleNamespace(message_id=900 + len(self.videos), video=SimpleNamespace(file_id="F"))


class FakeQuery:
    def __init__(self, daten):
        self.data, self.from_user = daten, SimpleNamespace(id=42)
        self.antworten, self.bearbeitet = [], []

    async def answer(self, text=None):
        self.antworten.append(text)

    async def edit_message_caption(self, **kw):
        self.bearbeitet.append(kw)


@unittest.skipIf(lernbot is None, "python-telegram-bot fehlt")
class LernBotEinfach(MitRegieMaterial):
    def setUp(self):
        super().setUp()
        self.bot, self.aufgaben = FakeBot(), []
        self.app = SimpleNamespace(bot=self.bot, bot_data={"con": self.con, "konfig": self.konfig, "erlaubt": 42},
                                   create_task=lambda koro: self.aufgaben.append(koro))
        self.context = SimpleNamespace(bot_data=self.app.bot_data, application=self.app, args=[])
        liste = self.tmp / "liste.json"
        liste.write_text(json.dumps(LISTE), encoding="utf-8")
        self.eid = self.con.execute(
            "INSERT INTO entwuerfe (name, format, schnittliste, parameter, dauer_s, status, erstellt) VALUES "
            "('t', 'short', ?, '{}', 46, 'gesendet', ?)", (str(liste), iso(jetzt()))).lastrowid

    def tearDown(self):
        for koro in self.aufgaben:
            koro.close()
        super().tearDown()

    def klick(self, daten):
        q = FakeQuery(daten)
        asyncio.run(lernbot.bei_klick(SimpleNamespace(callback_query=q), self.context))
        return q

    def test_nicht_gut_ein_tipp_regel_und_neue_fassung(self):
        q = self.klick(f"d:{self.eid}:-1")
        knoepfe = [b.callback_data for reihe in q.bearbeitet[0]["reply_markup"].inline_keyboard for b in reihe]
        self.assertEqual(knoepfe, [f"g:{self.eid}:{g}" for g in ("kurz", "lang", "langweilig", "musik", "hektisch", "neu")])
        self.klick(f"g:{self.eid}:kurz")
        self.assertEqual(regeln.ziel_regel(self.con, self.konfig), 55.0)                    # sofort gesetzt
        self.assertIn("ab jetzt 55 s", self.bot.texte[-1])
        self.assertIn("neue Fassung", self.bot.texte[-1])
        self.assertEqual([k.__name__ for k in self.aufgaben], ["neuer_entwurf"])           # neue Fassung startet
        zeile = self.con.execute("SELECT daumen, gruende FROM entwurf_bewertungen").fetchone()
        self.assertEqual((zeile["daumen"], json.loads(zeile["gruende"])), (-1, ["kurz"]))  # bleibt Lern-Material

    def test_schleife_schickt_nichts_waehrend_der_bot_baut(self):
        # 07.10. („warum sendet er immer 2 Videos?“): eine Fassung, die gerade geprüft wird, geht nicht vorab raus
        video = self.tmp / "v.mp4"
        video.write_bytes(b"x")
        self.con.execute("UPDATE entwuerfe SET status = 'gerendert', datei = ? WHERE id = ?", (str(video), self.eid))
        self.app.bot_data["arbeitet"] = True
        self.assertEqual(asyncio.run(lernbot.sende_wenn_frei(self.app)), 0)
        self.assertEqual(self.bot.videos, [])
        self.app.bot_data["arbeitet"] = False
        self.assertEqual(asyncio.run(lernbot.sende_wenn_frei(self.app)), 1)

    def test_hochladen_gibt_das_paket(self):
        q = self.klick(f"d:{self.eid}:1")
        self.assertEqual([k.__name__ for k in self.aufgaben], ["sende_paket"])
        self.assertEqual(self.con.execute("SELECT daumen FROM entwurf_bewertungen").fetchone()[0], 1)
        knoepfe = [b.callback_data for reihe in q.bearbeitet[0]["reply_markup"].inline_keyboard for b in reihe]
        self.assertEqual(knoepfe, [f"d:{self.eid}:1"])          # ✅ bleibt: ging das Paket schief, nochmal tippen

    def test_grund_waehrend_der_bot_baut_wartet(self):
        """07.10.: baut der Bot gerade, gilt der Grund noch nicht – vorher hieß es „ich baue dir jetzt eine neue
        Fassung“, gleich danach „ich baue gerade schon“, und die Fassung kam nie."""
        self.klick(f"d:{self.eid}:-1")
        self.app.bot_data["arbeitet"] = True
        q = self.klick(f"g:{self.eid}:kurz")
        self.assertIn("tipp gleich nochmal", q.antworten[-1])
        self.assertIsNone(regeln.ziel_regel(self.con, self.konfig))                         # keine Regel
        self.assertEqual((self.aufgaben, self.bot.texte), ([], []))
        self.app.bot_data["arbeitet"] = False
        self.klick(f"g:{self.eid}:kurz")                                                   # nochmal: wirkt jetzt
        self.assertEqual(regeln.ziel_regel(self.con, self.konfig), 55.0)
        self.assertEqual([k.__name__ for k in self.aufgaben], ["neuer_entwurf"])

    def test_nicht_gut_am_highlight_video_ohne_short_regel(self):
        """07.10.: ❌ am Highlight-Video (Zusammenschnitt) – vorher „Shorts sind ab jetzt 75 s lang (vorher 180 s)“,
        auch bei „⏳ Zu lang“, und dann wurde ein Short gebaut."""
        self.con.execute("UPDATE entwuerfe SET format = 'zusammenschnitt' WHERE id = ?", (self.eid,))
        q = self.klick(f"d:{self.eid}:-1")
        self.assertEqual(q.antworten, ["Verstanden – dieses Video lasse ich weg."])
        self.assertIsNone(q.bearbeitet[0]["reply_markup"])                                  # keine Gründe
        self.klick(f"g:{self.eid}:lang")                                                    # alter Knopf: genauso
        self.assertIsNone(regeln.ziel_regel(self.con, self.konfig))
        self.assertEqual((self.aufgaben, self.bot.texte), ([], []))                         # kein neuer Short
        self.assertEqual(self.con.execute("SELECT daumen FROM entwurf_bewertungen").fetchone()[0], -1)

    def test_statuszeile_wird_zu_kein_video(self):
        from clip_pipeline import db

        db.lern_meldung(self.con, "abend:s1", "🎮 Abend erkannt")
        asyncio.run(lernbot.sende_meldungen(self.app))
        db.lern_meldung(self.con, "kein:s1", "🎮 Abend vom 06.10.: nur 2 starke Szenen – heute kein Video.")
        asyncio.run(lernbot.sende_meldungen(self.app))
        self.assertEqual(len(self.bot.texte), 1)                                           # keine zweite Nachricht
        self.assertEqual(self.bot.bearbeitet, [(501, "🎮 Abend vom 06.10.: nur 2 starke Szenen – heute kein Video.")])


@unittest.skipIf(bot_app is None, "python-telegram-bot fehlt")
class ClipBotStill(MitSpeicher):
    def test_entscheidet_ohne_nachricht(self):
        self.konfig.daten["bot"]["clips_zeigen"] = False
        self.konfig.daten["auto_freigabe"]["modus"] = "an"     # still nur, wenn er selbst entscheidet (sonst nie entschieden)
        cid = self.clip_anlegen(status="vorbewertet", file_id=None)
        bot = FakeBot()
        fake = SimpleNamespace(bot_data={"con": self.con, "konfig": self.konfig, "erlaubt": 42}, bot=bot)
        self.assertEqual(asyncio.run(bot_app.sende_outbox(fake)), 1)
        self.assertEqual(bot.videos, [])                                                   # nichts im Chat
        status = self.con.execute("SELECT status, vorgelegt FROM clips WHERE id = ?", (cid,)).fetchone()
        self.assertNotEqual(status["status"], "vorbewertet")                               # trotzdem entschieden
        self.assertIsNotNone(status["vorgelegt"])


if __name__ == "__main__":
    unittest.main()
