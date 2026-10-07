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
        self.assertIn("55 s lang (vorher 45 s)", regeln.wende_an(self.con, self.k(), "kurz", LISTE))
        self.assertIn("65 s lang (vorher 55 s)", regeln.wende_an(self.con, self.k(), "kurz", LISTE))  # wirkt jedes Mal
        self.assertIn("55 s lang (vorher 65 s)", regeln.wende_an(self.con, self.k(), "lang", LISTE))
        self.assertIn("2 schwächsten", regeln.wende_an(self.con, self.k(), "langweilig", LISTE))
        self.assertEqual(regeln.gesperrt(self.con, "moment"), {"b", "d"})
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
        regeln.sperre(self.con, "moment", ["datei:1", "datei:2", "datei:3"], "langweilig")
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
        self.assertIn("nur 0 starke Szenen", text)
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

    def test_hochladen_gibt_das_paket(self):
        self.klick(f"d:{self.eid}:1")
        self.assertEqual([k.__name__ for k in self.aufgaben], ["sende_paket"])
        self.assertEqual(self.con.execute("SELECT daumen FROM entwurf_bewertungen").fetchone()[0], 1)

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
