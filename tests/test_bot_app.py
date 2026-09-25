"""Der Bot lässt sich zusammenbauen (ohne Netzwerk) und verschickt die Outbox richtig.

Dazu (Lernschleife „Publikum“, Spec §10.4, §13): Häkchen und /link im Clip-Bot legen den Post an (Tabelle posts)."""

import asyncio
import json
import sqlite3
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import mock

from clip_pipeline import big, db, konfig as konfig_modul, lager, publikum, shorts
from clip_pipeline.bot import aktionen
from clip_pipeline.zeit import UTC, iso, lokal_zu_utc

from tests.hilfen import MitSpeicher
from tests.test_lager import MitAbgleich

try:
    from clip_pipeline.bot import app as bot_app
except ImportError:  # python-telegram-bot nicht installiert
    bot_app = None


@unittest.skipIf(bot_app is None, "python-telegram-bot fehlt")
class BotApp(MitSpeicher):
    def setUp(self):
        super().setUp()
        # Ruhezeit ausdrücklich setzen – die Tests sollen nicht von pipeline.toml oder lokal.toml abhängen
        self.konfig.daten.setdefault("telegram", {}).update(leise_von="23:00", leise_bis="08:00")

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
        with mock.patch.object(aktionen, "jetzt", return_value=_um(4, 35)):  # mitten in der Ruhezeit (bis 08:00)
            self.assertEqual(asyncio.run(bot_app.sende_meldungen(fake)), 1)
        self.assertEqual(gesendet, ["⚠️ 2 Kills ohne Aufnahme"])  # andere Meldungen wie bisher sofort
        with mock.patch.object(aktionen, "jetzt", return_value=_um(8, 0)):
            self.assertEqual(asyncio.run(bot_app.sende_meldungen(fake)), 2)
            self.assertEqual(asyncio.run(bot_app.sende_meldungen(fake)), 0)
        self.assertEqual(gesendet[1:], ["💾 Puffer wird knapp", "🗄️ Lager-Abgleich: 1 Datei noch nicht im Lager"])

    def test_clip_videos_nachts_ohne_ton(self):
        """Gilt absichtlich immer, auch ohne getrennten Betrieb ([lager] leer) – abschalten: leise_von = ""."""
        self.assertFalse(self.konfig.getrennt)
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

    def test_highlight_videos_nachts_ohne_ton(self):
        (self.konfig.ordner("highlights") / "h.vorschau.mp4").write_bytes(b"video")
        gesendet = []

        async def send_video(**kwargs):
            gesendet.append(kwargs)
            return SimpleNamespace(message_id=9, video=None)

        fake = SimpleNamespace(bot_data={"con": self.con, "konfig": self.konfig, "erlaubt": 42},
                               bot=SimpleNamespace(send_video=send_video))
        for stunde in (2, 14):
            self.con.execute(
                "INSERT INTO highlights (name, datei, vorschau, clips, dauer, erstellt) VALUES "
                "(?, 'highlights/h.mp4', 'highlights/h.vorschau.mp4', 5, '02:10', 'x')", (f"highlight-{stunde}",)
            )
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
        with self.lager_tabu():  # auch kein statvfs im Lager: der Platz kommt aus der Tabelle
            text = _status_text(self.con, self.konfig)
        self.assertRegex(text, r"Lager: \d+ GB frei, letzter Abgleich \d\d:\d\d ok · 0 offen")
        self.assertEqual(self.geweckt, [])

    def test_status_antwortet_auch_bei_fehler(self):
        with mock.patch.object(lager, "status", side_effect=OSError("kaputt")):
            text = _status_text(self.con, self.konfig)
        self.assertIn("Lager: Stand nicht lesbar (kaputt)", text)
        self.assertIn("📊", text)


# --- Lernschleife „Publikum“: Posts aus dem Clip-Bot (Spec §10.4 letzter Punkt, Plan Stufe 1 Paket d) -------------

# Feste Zeiten: Häkchen am 25.09. mittags, alles Weitere relativ dazu
HAKEN = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
# Ein TikTok-Link mit Video-ID im Pfad (ausgedacht) – publikum.video_id_aus_url liest die Ziffern
TIKTOK = "https://www.tiktok.com/@testkanal/video/7300123456789012345"
TIKTOK_ID = "7300123456789012345"


class PostsAusDemClipBot(MitSpeicher):
    """Häkchen (`t:`/`y:`/`c:`) und /link im Clip-Bot legen zusätzlich den posts-Datensatz an – ohne Telegram."""

    def setUp(self):
        super().setUp()
        # Unabhängig von pipeline.toml/lokal.toml: nur TikTok bekommt Posts, Short mit Endcard 2,5 s
        self.konfig.daten["publikum"]["plattformen"] = ["tiktok"]
        self.konfig.daten["shorts"].update(endcard=True, endcard_s=2.5)
        self.cid = self.clip_anlegen(status="freigegeben")  # Clip 0–20 s im Quellvideo (tests/hilfen.py)

    def posts(self) -> list[sqlite3.Row]:
        return self.con.execute("SELECT * FROM posts ORDER BY id").fetchall()

    def veroeffentlichung(self, plattform: str, clip_id: int | None = None) -> tuple | None:
        zeile = self.con.execute("SELECT erledigt, url FROM veroeffentlichungen WHERE clip_id = ? AND plattform = ?",
                                 (clip_id or self.cid, plattform)).fetchone()
        return tuple(zeile) if zeile else None

    def test_haekchen_tiktok_legt_einen_post_an(self):
        antwort, stand = aktionen.plattform_erledigt(self.con, self.cid, "tiktok", self.konfig, zeit=HAKEN)
        self.assertEqual(antwort.hinweis, "TikTok ✅")  # Text wie bisher
        self.assertTrue(stand["tiktok"])
        [post] = self.posts()
        self.assertEqual(
            (post["art"], post["ziel"], post["clip_id"], post["entwurf_id"], post["plattform"], post["url"],
             post["video_id"], post["gepostet_utc"], post["experiment"], post["score"]),
            ("clip", f"clip:{self.cid}", self.cid, None, "tiktok", None, None, iso(HAKEN), 0, None),
        )
        # Dauer wie beim Rendern: 20 s Clip + 2,5 s Endcard − 0,5 s Überblendung (shorts.gesamtdauer)
        self.assertEqual(post["dauer_s"], 22.0)
        self.assertEqual(post["dauer_s"], shorts.gesamtdauer(20.0, self.konfig))
        self.assertEqual(json.loads(post["rezept"]), {"hook": "stark_zuerst", "laenge": "kurz", "tempo": "none",
                                                      "machart": "roh", "experiment": False})
        merkmale = json.loads(post["merkmale"])
        self.assertEqual(merkmale["hook_moment"], f"clip:{self.cid}")
        self.assertEqual([m["clip_id"] for m in merkmale["momente"]], [self.cid])
        self.assertEqual(self.veroeffentlichung("tiktok"), (iso(HAKEN), None))

    def test_ohne_endcard_ist_der_post_so_lang_wie_der_clip(self):
        self.konfig.daten["shorts"]["endcard"] = False
        aktionen.plattform_erledigt(self.con, self.cid, "tiktok", self.konfig, zeit=HAKEN)
        self.assertEqual(self.posts()[0]["dauer_s"], 20.0)

    def test_doppelklick_bleibt_ein_post_mit_dem_ersten_zeitpunkt(self):
        aktionen.plattform_erledigt(self.con, self.cid, "tiktok", self.konfig, zeit=HAKEN)
        antwort, stand = aktionen.plattform_erledigt(self.con, self.cid, "tiktok", self.konfig,
                                                     zeit=HAKEN + timedelta(hours=1))
        self.assertTrue(stand["tiktok"])
        [post] = self.posts()
        self.assertEqual(post["gepostet_utc"], iso(HAKEN))
        self.assertEqual(self.veroeffentlichung("tiktok"), (iso(HAKEN), None))

    def test_link_nach_dem_haekchen_traegt_url_und_video_id_nach(self):
        aktionen.plattform_erledigt(self.con, self.cid, "tiktok", self.konfig, zeit=HAKEN)
        antwort, stand = aktionen.link_speichern(self.con, self.cid, TIKTOK, self.konfig,
                                                 zeit=HAKEN + timedelta(hours=2))
        self.assertTrue(stand["tiktok"])
        [post] = self.posts()
        self.assertEqual((post["url"], post["video_id"], post["gepostet_utc"]), (TIKTOK, TIKTOK_ID, iso(HAKEN)))
        self.assertEqual(self.veroeffentlichung("tiktok"), (iso(HAKEN), TIKTOK))

        # Ein zweiter /link ersetzt einen falschen Link – im Post und in veroeffentlichungen
        neu = "https://www.tiktok.com/@testkanal/video/7300999999999999999"
        aktionen.link_speichern(self.con, self.cid, neu, self.konfig, zeit=HAKEN + timedelta(hours=3))
        [post] = self.posts()
        self.assertEqual((post["url"], post["video_id"], post["gepostet_utc"]),
                         (neu, "7300999999999999999", iso(HAKEN)))
        self.assertEqual(self.veroeffentlichung("tiktok"), (iso(HAKEN), neu))

    def test_link_ohne_vorheriges_haekchen_legt_den_post_an(self):
        antwort, stand = aktionen.link_speichern(self.con, self.cid, TIKTOK, self.konfig, zeit=HAKEN)
        self.assertEqual(antwort.hinweis, "TikTok ✅")
        self.assertTrue(stand["tiktok"])
        [post] = self.posts()
        self.assertEqual((post["ziel"], post["url"], post["video_id"], post["gepostet_utc"]),
                         (f"clip:{self.cid}", TIKTOK, TIKTOK_ID, iso(HAKEN)))
        self.assertEqual(self.veroeffentlichung("tiktok"), (iso(HAKEN), TIKTOK))

    def test_altbestand_post_gilt_ab_dem_ersten_haekchen(self):
        """Clip, der schon vor der Lernschleife abgehakt war: /link heute legt den Post mit dem Zeitpunkt des
        damaligen Häkchens an – so stimmt das Alter der Messungen (Score erst nach alter_tage)."""
        frueher = HAKEN - timedelta(days=10)
        self.con.execute("INSERT INTO veroeffentlichungen (clip_id, plattform, erledigt) VALUES (?, 'tiktok', ?)",
                         (self.cid, iso(frueher)))
        aktionen.link_speichern(self.con, self.cid, TIKTOK, self.konfig, zeit=HAKEN)
        [post] = self.posts()
        self.assertEqual((post["gepostet_utc"], post["url"]), (iso(frueher), TIKTOK))

    def test_clip_battle_bekommt_nie_einen_post(self):
        antwort, stand = aktionen.link_speichern(self.con, self.cid, "https://clip-battle.de/clip/5", self.konfig,
                                                 zeit=HAKEN)
        self.assertTrue(stand["clipbattle"])  # wie bisher abgehakt und der Link gemerkt …
        self.assertEqual(self.veroeffentlichung("clipbattle"), (iso(HAKEN), "https://clip-battle.de/clip/5"))
        aktionen.plattform_erledigt(self.con, self.cid, "clipbattle", self.konfig, zeit=HAKEN)
        self.assertEqual(self.posts(), [])  # … aber kein Post
        # auch nicht, wenn jemand "clipbattle" in [publikum].plattformen schreibt (ignoriert mit Warnung)
        self.konfig.daten["publikum"]["plattformen"] = ["tiktok", "clipbattle"]
        with self.assertLogs("pipeline", "WARNING"):
            aktionen.plattform_erledigt(self.con, self.cid, "clipbattle", self.konfig, zeit=HAKEN)
        self.assertEqual(self.posts(), [])

    def test_youtube_nur_wenn_in_publikum_plattformen(self):
        antwort, stand = aktionen.link_speichern(self.con, self.cid, "https://youtube.com/shorts/abc", self.konfig,
                                                 zeit=HAKEN)
        self.assertTrue(stand["youtube"])
        self.assertEqual(self.posts(), [])  # Standard ["tiktok"]: kein YouTube in Stufe 1 (Spec §3)

        self.konfig.daten["publikum"]["plattformen"] = ["tiktok", "youtube"]
        zweiter = self.clip_anlegen(status="freigegeben")
        aktionen.link_speichern(self.con, zweiter, "https://youtube.com/shorts/xyz", self.konfig, zeit=HAKEN)
        [post] = self.posts()
        self.assertEqual((post["plattform"], post["ziel"], post["url"], post["video_id"]),
                         ("youtube", f"clip:{zweiter}", "https://youtube.com/shorts/xyz", None))

    def test_post_plattform_ohne_eintrag_in_der_checkliste(self):
        """YouTube bekommt Posts ([publikum]), steht aber nicht in [veroeffentlichung] – dann gibt es keine
        Checklisten-Zeile, und der Post gilt ab diesem /link."""
        self.konfig.daten["veroeffentlichung"].update(pflicht=["tiktok"], zusatz=[])
        self.konfig.daten["publikum"]["plattformen"] = ["tiktok", "youtube"]
        antwort, stand = aktionen.link_speichern(self.con, self.cid, "https://youtube.com/shorts/abc", self.konfig,
                                                 zeit=HAKEN)
        self.assertEqual(stand, {"tiktok": False})  # Checkliste wie bisher: YouTube kommt darin nicht vor
        [post] = self.posts()
        self.assertEqual((post["plattform"], post["gepostet_utc"], post["url"]),
                         ("youtube", iso(HAKEN), "https://youtube.com/shorts/abc"))
        self.assertIsNone(self.veroeffentlichung("youtube"))

    def test_nicht_freigegebener_clip_wird_wie_bisher_abgelehnt(self):
        offen = self.clip_anlegen(status="gesendet")
        antwort, stand = aktionen.link_speichern(self.con, offen, TIKTOK, self.konfig, zeit=HAKEN)
        self.assertIsNone(stand)
        self.assertEqual(antwort.hinweis, f"Clip #{offen} ist nicht freigegeben")
        self.assertEqual(self.posts(), [])
        self.assertIsNone(self.veroeffentlichung("tiktok", offen))

    def test_unbekannter_link_wie_bisher(self):
        antwort, stand = aktionen.link_speichern(self.con, self.cid, "https://example.com/x", self.konfig, zeit=HAKEN)
        self.assertIsNone(stand)
        self.assertIn("Unbekannter Link", antwort.hinweis)
        self.assertEqual(self.posts(), [])

    def test_fehler_in_post_anlegen_nimmt_auch_das_haekchen_zurueck(self):
        aktionen.plattform_erledigt(self.con, self.cid, "youtube", self.konfig, zeit=HAKEN)  # YouTube schon da
        with mock.patch.object(publikum, "post_anlegen", side_effect=ValueError("Dauer 0 s ist keine Videolänge")), \
                self.assertLogs("clip-bot", "ERROR") as logs:
            antwort, stand = aktionen.plattform_erledigt(self.con, self.cid, "tiktok", self.konfig, zeit=HAKEN)
        self.assertIsNone(stand)
        self.assertTrue(antwort.hinweis.startswith("⚠️ TikTok nicht abgehakt"), antwort.hinweis)
        self.assertIn("Dauer 0 s", antwort.hinweis)
        self.assertLessEqual(len(antwort.hinweis), aktionen.HINWEIS_MAX)  # Telegram: Knopf-Antwort ≤ 200 Zeichen
        self.assertIn(f"Clip #{self.cid}", logs.output[0])
        # eine Transaktion: kein Häkchen, kein Statuswechsel auf „veröffentlicht“, kein Post
        self.assertEqual(self.veroeffentlichung("tiktok"), (None, None))
        self.assertEqual(db.clip(self.con, self.cid)["status"], "freigegeben")
        self.assertEqual(self.posts(), [])

        with mock.patch.object(publikum, "post_anlegen", side_effect=KeyError("Clip 1 gibt es nicht")), \
                self.assertLogs("clip-bot", "ERROR"):
            antwort, stand = aktionen.link_speichern(self.con, self.cid, TIKTOK, self.konfig, zeit=HAKEN)
        self.assertIsNone(stand)
        self.assertIn("Clip 1 gibt es nicht", antwort.hinweis)
        self.assertEqual(self.veroeffentlichung("tiktok"), (None, None))  # auch die url ist zurückgerollt
        self.assertEqual(self.posts(), [])

    def test_langer_grund_wird_gekuerzt(self):
        """Auch mit dem längsten Plattform-Namen und einem sehr langen Grund bleibt die Knopf-Antwort erlaubt kurz."""
        self.konfig.daten["publikum"]["plattformen"] = ["youtube"]
        with mock.patch.object(publikum, "post_anlegen", side_effect=ValueError("x" * 500)), \
                self.assertLogs("clip-bot", "ERROR"):
            antwort, stand = aktionen.plattform_erledigt(self.con, self.cid, "youtube", self.konfig, zeit=HAKEN)
        self.assertIsNone(stand)
        self.assertTrue(antwort.hinweis.startswith("⚠️ YouTube Shorts nicht abgehakt"), antwort.hinweis)
        self.assertLessEqual(len(antwort.hinweis), aktionen.HINWEIS_MAX)

    def test_fehler_in_link_nachtragen_nimmt_haekchen_url_und_post_zurueck(self):
        with mock.patch.object(publikum, "link_nachtragen", side_effect=ValueError("Leerer Link")), \
                self.assertLogs("clip-bot", "ERROR"):
            antwort, stand = aktionen.link_speichern(self.con, self.cid, TIKTOK, self.konfig, zeit=HAKEN)
        self.assertIsNone(stand)
        self.assertTrue(antwort.hinweis.startswith("⚠️ TikTok nicht abgehakt"), antwort.hinweis)
        self.assertIsNone(self.veroeffentlichung("tiktok"))  # nicht einmal die Checklisten-Zeile: alles zurückgerollt
        self.assertEqual(self.posts(), [])  # der schon angelegte Post ist mit zurückgerollt

    def test_kaputte_merkmale_und_danach_nochmal(self):
        """Echter Fehler statt Attrappe: kaputtes JSON in clips.merkmale. Nach der Korrektur klappt dasselbe Häkchen."""
        self.con.execute("UPDATE clips SET merkmale = '{kaputt' WHERE id = ?", (self.cid,))
        with self.assertLogs("clip-bot", "ERROR"):
            antwort, stand = aktionen.plattform_erledigt(self.con, self.cid, "tiktok", self.konfig, zeit=HAKEN)
        self.assertIsNone(stand)
        self.assertEqual((self.veroeffentlichung("tiktok"), self.posts()), (None, []))

        self.con.execute("UPDATE clips SET merkmale = '{}' WHERE id = ?", (self.cid,))
        antwort, stand = aktionen.plattform_erledigt(self.con, self.cid, "tiktok", self.konfig, zeit=HAKEN)
        self.assertTrue(stand["tiktok"])
        self.assertEqual(len(self.posts()), 1)

    def test_fehlende_konfig_ist_eine_klare_meldung(self):
        del self.konfig.daten["publikum"]["plattformen"]
        with self.assertLogs("clip-bot", "ERROR"):
            antwort, stand = aktionen.plattform_erledigt(self.con, self.cid, "tiktok", self.konfig, zeit=HAKEN)
        self.assertIsNone(stand)
        self.assertIn("[publikum].plattformen", antwort.hinweis)
        self.assertIsNone(self.veroeffentlichung("tiktok"))  # nicht einmal die Checklisten-Zeile: alles zurückgerollt

    def test_datenbankfehler_fliegen_weiter(self):
        """Nur fachliche Fehler werden zur Meldung; eine kaputte Datenbank bleibt ein echter Fehler (bot.app.bei_fehler
        loggt ihn) – trotzdem ist nichts halb gespeichert."""
        with mock.patch.object(publikum, "post_anlegen", side_effect=sqlite3.OperationalError("disk I/O error")):
            with self.assertRaises(sqlite3.OperationalError):
                aktionen.plattform_erledigt(self.con, self.cid, "tiktok", self.konfig, zeit=HAKEN)
        self.assertIsNone(self.veroeffentlichung("tiktok"))  # nicht einmal die Checklisten-Zeile: alles zurückgerollt
        self.assertFalse(self.con.in_transaction)

    def test_veroeffentlicht_erst_nach_allen_pflicht_plattformen(self):
        aktionen.link_speichern(self.con, self.cid, TIKTOK, self.konfig, zeit=HAKEN)
        self.assertEqual(db.clip(self.con, self.cid)["status"], "freigegeben")
        antwort, _ = aktionen.plattform_erledigt(self.con, self.cid, "youtube", self.konfig, zeit=HAKEN)
        self.assertEqual(antwort.hinweis, "YouTube Shorts ✅ – veröffentlicht!")
        self.assertEqual(db.clip(self.con, self.cid)["status"], "veroeffentlicht")
        self.assertEqual(len(self.posts()), 1)  # nur TikTok

    def test_zeit_kommt_ohne_parameter_aus_jetzt(self):
        with mock.patch.object(aktionen, "jetzt", return_value=HAKEN + timedelta(minutes=5)):
            aktionen.plattform_erledigt(self.con, self.cid, "tiktok", self.konfig)
        self.assertEqual(self.posts()[0]["gepostet_utc"], iso(HAKEN + timedelta(minutes=5)))

    def test_nie_wecken_kein_netz_kein_dateizugriff(self):
        """Häkchen und /link arbeiten nur mit der Datenbank (Annahme A8): kein Wecken von pve-big, kein Netz, keine
        Datei – der Clip-Bot darf nicht an einem schlafenden Host oder hängenden NFS stehen bleiben."""
        self.konfig.daten["speicher"].update(host="pve-gross", wol_mac="aa:bb:cc:dd:ee:ff")
        with mock.patch.object(konfig_modul.Konfig, "_host_erreichbar", return_value=False), \
                mock.patch("clip_pipeline.konfig.sende_wake_on_lan") as wol, \
                mock.patch.object(big, "wach_halten") as wach, \
                mock.patch("socket.create_connection", side_effect=AssertionError("Netzwerkzugriff")), \
                mock.patch("builtins.open", side_effect=AssertionError("Dateizugriff")), \
                mock.patch("pathlib.Path.exists", side_effect=AssertionError("Dateizugriff")), \
                mock.patch("pathlib.Path.is_file", side_effect=AssertionError("Dateizugriff")):
            aktionen.plattform_erledigt(self.con, self.cid, "tiktok", self.konfig, zeit=HAKEN)
            aktionen.link_speichern(self.con, self.cid, TIKTOK, self.konfig, zeit=HAKEN)
        self.assertEqual(len(self.posts()), 1)
        self.assertEqual(self.posts()[0]["url"], TIKTOK)
        wol.assert_not_called()
        wach.assert_not_called()


def _klick(con, konfig, daten: str, nutzer: int = 42) -> tuple[list, list]:
    """Knopf im Clip-Bot drücken (bot_app.bei_klick) ohne Telegram: gibt (Knopf-Antworten, geänderte Texte) zurück."""
    antworten, texte_neu = [], []

    async def answer(text=None, **_):
        antworten.append(text)

    async def edit_message_text(text, **_):
        texte_neu.append(text)

    query = SimpleNamespace(data=daten, from_user=SimpleNamespace(id=nutzer), answer=answer,
                            edit_message_text=edit_message_text, message=SimpleNamespace(reply_markup=None))
    context = SimpleNamespace(bot_data={"con": con, "konfig": konfig, "erlaubt": 42})
    asyncio.run(bot_app.bei_klick(SimpleNamespace(callback_query=query), context))
    return antworten, texte_neu


def _link(con, konfig, *args: str) -> str:
    """/link im Clip-Bot ohne Telegram: liefert den Antworttext."""
    antworten = []

    async def reply_text(text, **_):
        antworten.append(text)

    update = SimpleNamespace(effective_message=SimpleNamespace(reply_text=reply_text))
    context = SimpleNamespace(bot_data={"con": con, "konfig": konfig, "erlaubt": 42}, args=list(args))
    asyncio.run(bot_app.cmd_link(update, context))
    return antworten[0]


@unittest.skipIf(bot_app is None, "python-telegram-bot fehlt")
class PostNummerImClipBot(MitSpeicher):
    """Die Checkliste nennt die Post-Nummer – die brauchst du für die Screenshots im Lern-Bot („#17“, Spec §7.1)."""

    def setUp(self):
        super().setUp()
        self.konfig.daten["publikum"]["plattformen"] = ["tiktok"]
        self.cid = self.clip_anlegen(status="freigegeben")

    def test_haekchen_zeigt_die_post_nummer(self):
        antworten, texte_neu = _klick(self.con, self.konfig, f"t:{self.cid}")
        self.assertEqual(antworten, ["TikTok ✅"])
        [text] = texte_neu
        self.assertIn(f"Upload-Checkliste Clip #{self.cid}", text)
        self.assertIn("📈 Post #1 (TikTok) – für die Zahlen: Screenshot an den Lern-Bot, Bildunterschrift #1", text)
        # YouTube bekommt (Standard ["tiktok"]) keinen Post, also auch keine zweite Zeile
        _, texte_neu = _klick(self.con, self.konfig, f"y:{self.cid}")
        self.assertEqual(texte_neu[0].count("📈"), 1)

    def test_link_antwort_nennt_die_post_nummer(self):
        text = _link(self.con, self.konfig, str(self.cid), TIKTOK)
        self.assertIn("✅ TikTok", text)
        self.assertIn("📈 Post #1 (TikTok)", text)

    def test_checkliste_ohne_post_ohne_zeile(self):
        text = _link(self.con, self.konfig, str(self.cid), "https://clip-battle.de/clip/5")
        self.assertIn("✅ clip-battle.de", text)
        self.assertNotIn("📈", text)

    def test_fehler_beim_haekchen_sagt_es_dir(self):
        with mock.patch.object(publikum, "post_anlegen", side_effect=ValueError("kaputt")), \
                self.assertLogs("clip-bot", "ERROR"):
            antworten, texte_neu = _klick(self.con, self.konfig, f"t:{self.cid}")
        self.assertEqual(len(antworten), 1)
        self.assertTrue(antworten[0].startswith("⚠️ TikTok nicht abgehakt"), antworten[0])
        self.assertEqual(texte_neu, [])  # Checkliste bleibt, wie sie war (das Häkchen ist nicht gesetzt)

    def test_fehler_beim_link_sagt_es_dir(self):
        with mock.patch.object(publikum, "link_nachtragen", side_effect=ValueError("kaputt")), \
                self.assertLogs("clip-bot", "ERROR"):
            text = _link(self.con, self.konfig, str(self.cid), TIKTOK)
        self.assertTrue(text.startswith("⚠️ TikTok nicht abgehakt"), text)

    def test_fehlergrund_wird_fuer_html_maskiert(self):
        """Die /link-Antwort geht als HTML raus: ein „<“ im Grund darf sie nicht kaputt machen."""
        with mock.patch.object(publikum, "post_anlegen", side_effect=ValueError("Dauer <0> & kaputt")), \
                self.assertLogs("clip-bot", "ERROR"):
            text = _link(self.con, self.konfig, str(self.cid), TIKTOK)
        self.assertIn("Dauer &lt;0&gt; &amp; kaputt", text)
        self.assertNotIn("<0>", text)

    def test_fremder_klick_legt_keinen_post_an(self):
        antworten, _ = _klick(self.con, self.konfig, f"t:{self.cid}", nutzer=99)
        self.assertEqual(antworten, ["Nicht erlaubt."])
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM posts").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
