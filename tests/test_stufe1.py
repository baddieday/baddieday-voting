"""Stufe 1 (07.10., Florian: „Bot macht alles … Impact wird nicht gewertet … lieber kein Video … zu viele
Nachrichten“): deine Gründe als feste Regeln, nur starke Szenen, Songs wechseln, Abend-Video mit Statuszeile,
✅/❌ im Lern-Bot, Clip-Bot still."""

import asyncio
import json
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from clip_pipeline import einstellungen, geschmack, regeln, regie, regie_lernen, sitzung
from clip_pipeline.medien import MedienFehler
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

    def test_langweilig_holt_gesenkte_effekte_zurueck(self):
        """08.10.: 🥱 ist das Gegenpaar zu 😵 – ohne ⚙️ blieben die Effekte nach zweimal 😵 sonst für immer aus.
        Start: alter Schalter „Effekte aus“ (MitRegieMaterial) = Stufe „aus“; je 🥱 eine Stufe, nie über „normal“."""
        self.assertEqual(regeln.stufe(self.con, self.k()), 0)
        self.assertIn("wieder etwas mehr Effekte: „ruhig“ statt „aus“",
                      regeln.wende_an(self.con, self.k(), "langweilig", LISTE))
        self.assertEqual(regeln.stufe(self.con, self.k()), 1)
        self.assertTrue(self.k().wert("regie.effekte.an"))                                  # wieder an
        w = {"geschmack": {"zeitlupe": "viel"}, "max_lupen": 8}
        self.assertIn("zeitlupe", geschmack.nur_wirksame(w, w, self.k()))                 # Zeitlupe lernt wieder mit
        self.assertIn("„normal“ statt „ruhig“", regeln.wende_an(self.con, self.k(), "langweilig", LISTE))
        self.assertNotIn("Effekte", regeln.wende_an(self.con, self.k(), "langweilig", LISTE))   # nie über „normal“
        self.assertEqual(regeln.stufe(self.con, self.k()), 2)
        gelernt = {"effekt_hektik": 1.2}                                                   # z. B. Aufbau „Montage“
        self.assertEqual(regeln.anwenden(self.con, self.k(), "short", gelernt)["effekt_hektik"], 1.2)   # wie gelernt
        einstellungen.setze(self.con, "lernbot.experte", True)                              # /experte: wie bisher fest
        self.assertEqual(regeln.anwenden(self.con, self.k(), "short", gelernt)["effekt_hektik"], 1.0)

    def test_langweilig_ohne_gesenkte_effekte_aendert_nichts(self):
        self.konfig.daten["regie"]["effekte"]["an"] = True                    # ab Werk: keine Stufe, das Gelernte gilt
        self.assertNotIn("Effekte", regeln.wende_an(self.con, self.k(), "langweilig", LISTE))
        self.assertIsNone(regeln.stufe(self.con, self.k()))
        self.assertNotIn(regeln.STUFE_SCHLUESSEL, einstellungen.gespeichert(self.con))     # keine neue Zeile


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
    """Telegram als Attrappe. asyncio.sleep(0) gibt wie ein echter Aufruf an die Event-Loop ab – sonst liefe ein
    asyncio.gather zweier Klicks streng nacheinander (Prüfung 08.10.)."""

    def __init__(self):
        self.texte, self.bearbeitet, self.geloescht, self.videos = [], [], [], []

    async def send_message(self, chat, text, **kw):
        await asyncio.sleep(0)
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
        await asyncio.sleep(0)   # wie FakeBot: echt verschachtelt
        self.antworten.append(text)

    async def edit_message_caption(self, **kw):
        await asyncio.sleep(0)
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

    def zweites_video(self):
        liste = self.tmp / "liste2.json"
        liste.write_text(json.dumps({**LISTE, "musik": {"track_id": 4, "titel": "Song B"}}), encoding="utf-8")
        return self.con.execute(
            "INSERT INTO entwuerfe (name, format, schnittliste, parameter, dauer_s, status, erstellt) VALUES "
            "('t2', 'short', ?, '{}', 46, 'gesendet', ?)", (str(liste), iso(jetzt()))).lastrowid

    def schleife(self, runden=1):
        """Wie lernbot._schleife: folge_starten, die gestarteten Aufgaben laufen gleich durch. Rückgabe: gestartet."""
        gestartet = 0
        for _ in range(runden):
            gestartet += asyncio.run(lernbot.folge_starten(self.app))
            while self.aufgaben:
                asyncio.run(self.aufgaben.pop(0))
        return gestartet

    def test_nicht_gut_ein_tipp_regel_und_neue_fassung(self):
        q = self.klick(f"d:{self.eid}:-1")
        knoepfe = [b.callback_data for reihe in q.bearbeitet[0]["reply_markup"].inline_keyboard for b in reihe]
        self.assertEqual(knoepfe, [f"g:{self.eid}:{g}" for g in ("kurz", "lang", "langweilig", "musik", "hektisch", "neu")])
        self.klick(f"g:{self.eid}:kurz")
        self.assertEqual(regeln.ziel_regel(self.con, self.konfig), 55.0)                    # sofort gesetzt
        self.assertIn("ab jetzt 55 s", self.bot.texte[-1])
        self.assertIn("neue Fassung", self.bot.texte[-1])
        self.assertEqual(lernbot.folge_zu(self.con, self.eid)["art"], "fassung")           # gemerkt, die Schleife baut
        self.assertEqual(asyncio.run(lernbot.folge_starten(self.app)), 1)
        self.assertEqual([k.__name__ for k in self.aufgaben], ["fassung_auftrag"])
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
        self.assertEqual([k.__name__ for k in self.aufgaben], ["paket_auftrag"])
        self.assertEqual(self.con.execute("SELECT daumen FROM entwurf_bewertungen").fetchone()[0], 1)
        knoepfe = [b.callback_data for reihe in q.bearbeitet[0]["reply_markup"].inline_keyboard for b in reihe]
        self.assertEqual(knoepfe, [f"d:{self.eid}:1"])          # ✅ bleibt stehen

    def test_hochladen_waehrend_er_packt_wird_gemerkt(self):
        """08.10.: vorher „⏳ Ich packe gerade ein anderes Paket – tippe gleich nochmal ✅“ – und dein ✅ war nicht einmal
        gespeichert. Jetzt zählt es sofort, und das Paket kommt nach dem laufenden."""
        self.app.bot_data["paket_arbeitet"] = True
        q = self.klick(f"d:{self.eid}:1")
        self.assertEqual(q.antworten[-1], "Verstanden – dein Upload-Paket kommt nach dem, das ich gerade packe.")
        self.assertEqual(self.con.execute("SELECT daumen FROM entwurf_bewertungen").fetchone()[0], 1)
        self.assertEqual(self.aufgaben, [])
        self.assertIn("Schon vorgemerkt", self.klick(f"d:{self.eid}:1").antworten[-1])        # Doppeltipp: einmal
        self.app.bot_data["paket_arbeitet"] = False                                         # das andere ist fertig
        self.assertEqual(asyncio.run(lernbot.folge_starten(self.app)), 1)
        self.assertEqual([k.__name__ for k in self.aufgaben], ["paket_auftrag"])
        self.assertTrue(self.app.bot_data["paket_arbeitet"])                              # vor dem Start gesetzt

    def test_grund_waehrend_der_bot_baut_wird_gemerkt(self):
        """08.10. (Florian tippt nie etwas zweimal): vorher hieß es „tipp gleich nochmal auf deinen Grund“, die Regel
        galt nicht, und ohne zweiten Tipp kam nichts. Jetzt gilt die Regel sofort, die Fassung kommt nach dem Bau."""
        self.klick(f"d:{self.eid}:-1")
        self.app.bot_data["arbeitet"] = True
        q = self.klick(f"g:{self.eid}:kurz")
        self.assertEqual(q.antworten[-1], "Verstanden – kommt nach dem Video, an dem ich gerade baue.")
        self.assertEqual(regeln.ziel_regel(self.con, self.konfig), 55.0)                    # Regel sofort
        self.assertIn("kommt nach dem Video, an dem ich gerade baue", self.bot.texte[-1])
        self.assertEqual(self.schleife(), 0)                                                # baut noch: wartet
        self.app.bot_data["arbeitet"] = False                                               # der Bau ist fertig
        with mock.patch.object(lernbot, "baue_entwurf", return_value=77) as bau:
            self.assertEqual(self.schleife(3), 1)
        self.assertEqual(bau.call_count, 1)                                                 # genau eine neue Fassung
        self.assertEqual(bau.call_args.args[2], {"m1", "m2"})                              # aus denselben Matches
        self.assertEqual(bau.call_args.args[4], self.eid)       # 08.10.: ersetzt das Video, darf seine Szenen nehmen
        self.assertEqual(lernbot.folge_zu(self.con, self.eid)["ergebnis"], "gesendet")
        self.assertFalse(self.app.bot_data["arbeitet"])

    def test_zwei_gruende_kurz_nacheinander_eine_fassung(self):
        """07.10. (Prüfung P4): Gründe an zwei Videos kurz nacheinander – die zweite Fassung gab mit „Ich baue gerade
        schon“ still auf. Jetzt gelten beide Regeln, und genau eine neue Fassung kommt: die zum neuesten ❌."""
        zweites = self.zweites_video()
        self.klick(f"d:{self.eid}:-1")
        self.klick(f"d:{zweites}:-1")

        async def beide():
            await asyncio.gather(*(lernbot.bei_klick(SimpleNamespace(callback_query=FakeQuery(d)), self.context)
                                   for d in (f"g:{self.eid}:kurz", f"g:{zweites}:musik")))

        asyncio.run(beide())
        self.assertEqual((regeln.ziel_regel(self.con, self.konfig), regeln.gesperrt(self.con, "track")), (55.0, {"4"}))
        self.assertIn("Deine Tipps von eben kommen zusammen in eine Fassung.", self.bot.texte[-1])
        with mock.patch.object(lernbot, "baue_entwurf", return_value=77) as bau:
            self.schleife(3)
        self.assertEqual(bau.call_count, 1)
        self.assertFalse(any("baue gerade schon" in t for t in self.bot.texte))
        self.assertEqual([lernbot.folge_zu(self.con, e)["ergebnis"] for e in (self.eid, zweites)],
                         ["zusammengelegt", "gesendet"])

    def test_keine_neuen_szenen_genau_ein_satz(self):
        """Prüfer (r1_dbqueue): ohne Erledigt-Vermerk käme „Diesmal keine neue Fassung“ in jeder Runde und nach jedem
        Neustart wieder. Jetzt genau ein Satz – auch nach drei Schleifenrunden und einem Neustart."""
        self.klick(f"d:{self.eid}:-1")
        self.klick(f"g:{self.eid}:langweilig")
        with mock.patch.object(lernbot, "baue_entwurf", side_effect=regie.KeineNeuenSzenen(0, 4)) as bau:
            self.schleife(3)
            neu = {k: self.app.bot_data[k] for k in ("con", "konfig", "erlaubt")}            # Neustart: Speicher weg
            self.app.bot_data.clear()
            self.app.bot_data.update(neu)
            lernbot.folge_aufraeumen(self.con)
            self.schleife(3)
        self.assertEqual(bau.call_count, 1)
        self.assertEqual(bau.call_args.args[3], self.eid)                                  # 🥱: anders als dieses Video
        self.assertEqual(sum("Diesmal keine neue Fassung" in t for t in self.bot.texte), 1)
        self.assertEqual(lernbot.folge_zu(self.con, self.eid)["ergebnis"], "kein_video")

    def test_fehler_zwei_neue_versuche_dann_ein_satz(self):
        """08.10.: statt „Tipp später nochmal“ versucht der Bot es nach 10 und nach 30 min noch einmal; nach dem dritten
        Fehlschlag ein Satz, ohne dich um etwas zu bitten."""
        self.klick(f"d:{self.eid}:-1")
        self.klick(f"g:{self.eid}:neu")
        uhr = [jetzt() + timedelta(seconds=1)]
        with mock.patch.object(lernbot, "jetzt", side_effect=lambda: uhr[0]), \
                mock.patch.object(lernbot, "baue_entwurf", side_effect=MedienFehler("ffmpeg hängt")) as bau, \
                self.assertLogs("lern-bot", "ERROR"):
            for minuten in (0, 9, 2, 29, 2, 60):     # Versuche bei 0, 11 und 42 min – davor und danach nichts
                uhr[0] += timedelta(minutes=minuten)
                self.schleife()
        self.assertEqual(bau.call_count, 3)
        fehler = [t for t in self.bot.texte if t.startswith("⚠️")]
        self.assertEqual(fehler, ["⚠️ Die neue Fassung ließ sich auch im dritten Versuch nicht bauen – ich lasse sie "
                                  "aus."])
        self.assertEqual(lernbot.folge_zu(self.con, self.eid)["ergebnis"], "aufgegeben")

    def test_neustart_holt_nur_junge_tipps_nach(self):
        """Neustart (Update): ein Tipp von vor 20 min kommt trotzdem, einer von vor 3 h nicht mehr (kein Video aus
        heiterem Himmel)."""
        zweites = self.zweites_video()
        for e in (self.eid, zweites):
            self.klick(f"d:{e}:-1")
        self.klick(f"g:{self.eid}:kurz")
        self.assertEqual(lernbot.folge_aufraeumen(self.con, jetzt() + timedelta(hours=3)), 1)       # nach 3 h: nein
        self.assertEqual(lernbot.folge_zu(self.con, self.eid)["ergebnis"], "zu_alt")
        self.klick(f"g:{zweites}:lang")
        self.assertEqual(lernbot.folge_aufraeumen(self.con, jetzt() + timedelta(minutes=20)), 0)    # nach 20 min: ja
        self.assertEqual(asyncio.run(lernbot.folge_starten(self.app)), 1)
        self.assertEqual(self.app.bot_data["fassung_fuer"], zweites)

    def test_neues_video_gescheitert_einmal_wiederholt(self):
        """08.10.: statt „Tipp später nochmal auf 🎬 Neues Video“ versucht es der Bot nach 10 min selbst noch einmal,
        danach ein Satz ohne Aufforderung. 🎬 während eines Baus: das laufende Video kommt."""
        self.app.bot_data["arbeitet"] = True
        asyncio.run(lernbot.neuer_entwurf(self.app, "short"))
        self.assertEqual(self.bot.texte[-1], "⏳ Ich baue gerade schon ein Video – es kommt gleich.")
        self.app.bot_data["arbeitet"] = False
        with mock.patch.object(lernbot, "baue_entwurf", side_effect=MedienFehler("ffmpeg hängt")) as bau, \
                self.assertLogs("lern-bot", "ERROR"):
            asyncio.run(lernbot.neuer_entwurf(self.app, "short"))
            self.assertTrue(self.bot.texte[-1].endswith("Ich versuche es in 10 Minuten noch einmal."))
            self.assertEqual(self.schleife(), 0)                                            # erst nach 10 min
            self.app.bot_data["knopf_nochmal"]["faellig"] = 0                               # … die sind um
            self.assertEqual(self.schleife(2), 1)                                           # genau einmal
        self.assertEqual(bau.call_count, 2)
        self.assertEqual(self.bot.texte[-1], "⚠️ Das Video ließ sich auch im zweiten Versuch nicht bauen.")
        self.assertFalse(any("Tipp" in t for t in self.bot.texte))

    def test_knopf_hinweis_scheitert_tipp_zaehlt_trotzdem(self):
        """Prüfung 08.10.: Lehnt Telegram den Hinweis am Knopf ab (Klick erst nach einem Neustart verarbeitet, Netz
        hängt), zählt dein Tipp trotzdem. Vorher brach alles ab: keine Regel, kein Auftrag, kein ✅ – und der nächste
        Tipp auf den Grund hieß „Schon erledigt – die neue Fassung kommt“, ohne dass je eine kam."""
        from telegram.error import TimedOut

        zweites = self.zweites_video()
        self.klick(f"d:{self.eid}:-1")
        with mock.patch.object(FakeQuery, "answer", side_effect=TimedOut()):
            self.klick(f"g:{self.eid}:kurz")
            self.klick(f"d:{zweites}:1")
        self.assertEqual(regeln.ziel_regel(self.con, self.konfig), 55.0)
        self.assertEqual(lernbot.folge_zu(self.con, self.eid)["art"], "fassung")
        self.assertEqual((lernbot.folge_zu(self.con, zweites)["art"], self.con.execute(
            "SELECT daumen FROM entwurf_bewertungen WHERE entwurf_id = ?", (zweites,)).fetchone()[0]), ("paket", 1))

    def test_neues_video_waehrend_einer_fassung_geht_nicht_verloren(self):
        """Prüfung 08.10.: 🎬 während einer neuen Fassung – kommt sie, ist sie dein Video (kein zweites); endet sie ohne
        Video (keine neuen Szenen), baut der Bot dein 🎬 gleich danach. Vorher kam „es kommt gleich“ und dann nichts."""
        zweites = self.zweites_video()
        for eid, fassung, bauten in ((self.eid, 77, [self.eid]),                           # Fassung kommt
                                     (zweites, regie.KeineNeuenSzenen(0, 4), [zweites, None])):   # ohne Video
            self.klick(f"d:{eid}:-1")
            self.klick(f"g:{eid}:langweilig")
            asyncio.run(lernbot.folge_starten(self.app))                                     # die Fassung läuft …
            asyncio.run(lernbot.neuer_entwurf(self.app, "short"))                            # … und du tippst 🎬
            self.assertEqual(self.bot.texte[-1], "⏳ Ich baue gerade schon ein Video – es kommt gleich.")
            with mock.patch.object(lernbot, "baue_entwurf", side_effect=[fassung, 78]) as bau:
                self.schleife(3)
            self.assertEqual([c.args[3] for c in bau.call_args_list], bauten)               # 🎬: nicht „anders als“
        self.assertNotIn("knopf_nochmal", self.app.bot_data)

    def zwei_wochen_video(self) -> None:
        """Das Video ist das 2-Wochen-Video (highlight.erstelle: Zusammenschnitt mit highlights.entwurf_id)."""
        self.con.execute("UPDATE entwuerfe SET format = 'zusammenschnitt' WHERE id = ?", (self.eid,))
        self.con.execute("INSERT INTO highlights (name, datei, vorschau, clips, dauer, erstellt, entwurf_id, status) "
                         "VALUES ('2026-W41', 'highlights/2026-W41.mp4', 'highlights/v.mp4', 5, '01:42', 'x', ?, "
                         "'gesendet')", (self.eid,))

    def test_nicht_gut_am_highlight_video_ohne_short_regel(self):
        """07.10.: ❌ am Highlight-Video (Zusammenschnitt) – vorher „Shorts sind ab jetzt 75 s lang (vorher 180 s)“,
        auch bei „⏳ Zu lang“, und dann wurde ein Short gebaut. 08.10.: Der Clip-Bot zeigt es nicht mehr – ❌ hier
        verwirft es auch dort (die Clips sind wieder frei)."""
        self.zwei_wochen_video()
        q = self.klick(f"d:{self.eid}:-1")
        self.assertEqual(q.antworten, ["Verstanden – dieses Video lasse ich weg."])
        self.assertIsNone(q.bearbeitet[0]["reply_markup"])                                  # keine Gründe
        self.klick(f"g:{self.eid}:lang")                                                    # alter Knopf: genauso
        self.assertIsNone(regeln.ziel_regel(self.con, self.konfig))
        self.assertEqual((self.aufgaben, self.bot.texte), ([], []))                         # kein neuer Short
        self.assertEqual(self.con.execute("SELECT daumen FROM entwurf_bewertungen").fetchone()[0], -1)
        self.assertEqual(self.con.execute("SELECT status FROM highlights").fetchone()[0], "verworfen")

    def test_zwei_wochen_video_nur_hier_und_hochladen(self):
        """Stufe 3 (08.10.): Das 2-Wochen-Video kommt nur noch im Lern-Bot (mit Kopfzeile, wofür es ist); ✅ heißt
        freigegeben und hochgeladen – der Clip-Bot erinnert nicht mehr – und bringt das Paket wie beim Short."""
        self.zwei_wochen_video()
        video = self.tmp / "v.mp4"
        video.write_bytes(b"x")
        self.con.execute("UPDATE entwuerfe SET status = 'gerendert', datei = ? WHERE id = ?", (str(video), self.eid))
        self.assertEqual(asyncio.run(lernbot.sende_entwuerfe(self.app)), 1)
        self.assertTrue(self.bot.videos[0]["caption"].startswith("🏆 <b>Dein 2-Wochen-Video</b>\n🎬 <b>Video #"))
        q = self.klick(f"d:{self.eid}:1")
        self.assertTrue(q.bearbeitet[0]["caption"].startswith("🏆 <b>Dein 2-Wochen-Video</b>\n"))   # bleibt stehen
        h = self.con.execute("SELECT status, hochgeladen FROM highlights").fetchone()
        self.assertEqual(h["status"], "freigegeben")
        self.assertIsNotNone(h["hochgeladen"])
        self.assertEqual([k.__name__ for k in self.aufgaben], ["paket_auftrag"])

    def test_statuszeile_wird_zu_kein_video_und_nachtrag(self):
        from clip_pipeline import db

        db.lern_meldung(self.con, "abend:s1", "🎮 Abend erkannt")
        asyncio.run(lernbot.sende_meldungen(self.app))
        db.lern_meldung(self.con, "kein:s1", "🎮 Abend vom 06.10.: nur 2 starke Szenen – heute kein Video.")
        asyncio.run(lernbot.sende_meldungen(self.app))
        self.assertEqual(len(self.bot.texte), 1)                                           # keine zweite Nachricht
        self.assertEqual(self.bot.bearbeitet, [(501, "🎮 Abend vom 06.10.: nur 2 starke Szenen – heute kein Video.")])
        # Stufe 4 (08.10.): kommt danach noch etwas an, wird dieselbe Zeile zum Nachtrag und geht mit dem Video
        db.lern_meldung(self.con, "nachtrag:s1", "🎮 Nachtrag: Abend vom 06.10. – ich baue dein Video.")
        asyncio.run(lernbot.sende_meldungen(self.app))
        self.assertEqual((len(self.bot.texte), self.bot.bearbeitet[-1]),
                         (1, (501, "🎮 Nachtrag: Abend vom 06.10. – ich baue dein Video.")))
        video = self.tmp / "v.mp4"
        video.write_bytes(b"x")
        self.con.execute("UPDATE entwuerfe SET status = 'gerendert', datei = ? WHERE id = ?", (str(video), self.eid))
        self.con.execute("INSERT INTO sitzungen (name, matches, ende_utc, entwurf_id, verarbeitet) VALUES "
                         "('s1', '[]', ?, ?, ?)", (iso(jetzt()), self.eid, iso(jetzt())))
        self.assertEqual(asyncio.run(lernbot.sende_entwuerfe(self.app)), 1)
        self.assertEqual(self.bot.geloescht, [501])                                        # genau einmal gelöscht


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
