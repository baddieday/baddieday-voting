"""Ende-zu-Ende Stufe 1 der Lernschleife „Publikum“ (Spec §13 letzter Punkt, §14 Punkt 1; Plan Paket g).

Was hier in EINEM Durchlauf passiert – so, wie du es später im Alltag erlebst:
  1. Die Datenbank ist im alten Stand (vor der Lernschleife: schema.sql + regie.sql + lager.sql, mit Beispielzeilen).
     `db.verbinde` bringt sie auf den neuen Stand, ohne etwas zu verlieren.
  2. Clip-Bot: Du tippst unter Clip #1 „✅ TikTok erledigt“ → der erste Post entsteht (art clip).
  3. Lern-Bot: `/link e41 <TikTok-Link>` → der zweite Post entsteht (art entwurf, mit Link und Video-ID).
  4. Tag 3: Du schickst die Zahlen von Hand, z. B. „#1 1240 61 6,8 34“.
  5. Tag 7: Du schickst einen Screenshot mit Bildunterschrift „#1“; Claude liest ihn (hier gefälscht).
  6. Der Timer ruft `pipeline publikum bewerten`: beide Posts bekommen ihren Score – 0 mit Vermerk
     „Basis zu klein“, weil es noch keine 5 bewerteten Posts zum Vergleich gibt (Spec §6.3, Abnahme im Plan).
     Die Meldung dazu kommt im Lern-Bot an.
  7. Am nächsten Tag läuft der Timer wieder: nichts ändert sich (Scores werden nie überschrieben).
  8. `/publikum` zeigt beide Posts mit ihren Tag-7-Zahlen und „Score 0 (Basis zu klein)“.
Durchgehend gilt: pve-big wird nie geweckt, und kein Screenshot bleibt liegen.

Warum das wichtig ist: Die Unit-Tests der Pakete a–e prüfen jedes Stück für sich. Nur hier sieht man, dass die
Stücke zusammenpassen – dass die Post-Nummer aus dem Clip-Bot im Lern-Bot funktioniert, dass `bewerten` genau die
Tag-7-Messung nimmt, die der Screenshot gespeichert hat, und dass /publikum anzeigt, was `bewerten` geschrieben hat.

So echt wie hier möglich:
  - Es laufen die echten Telegram-Handler (bot.app.bei_klick, lernbot_paket.cmd_link, lernbot_zahlen.bei_text/
    bei_foto, lernbot_publikum.cmd_publikum, lernbot.sende_meldungen) – nur Telegram selbst ist gefälscht
    (Fake-Bots und -Nachrichten aus tests/test_lernbot.py und tests/test_lernbot_zahlen.py).
  - Wie im Betrieb hat jeder Teil seine eigene Verbindung zur selben Datenbank-Datei: Clip-Bot, Lern-Bot, die
    CLI (öffnet selbst eine) und der Test, der nur nachsieht.
  - Getrennter Betrieb wie auf dem Mini (E19): Puffer mit Marke, Lager „gesetzt“, aber nie angefasst.
  - `claude` ist gefälscht (FakeClaude aus tests/test_screenshot.py) – ein echter Aufruf zählte gegen das Abo.
    Zur Sicherheit zeigt [decide].programm auf ein Programm, das es nicht gibt.

Eine Uhr für alle: Jedes beteiligte Modul hat `jetzt` auf Modulebene importiert (Plan, Leitplanke 4). Der Test
ersetzt sie alle durch dieselbe Uhr `self.uhr` und stellt sie Schritt für Schritt vor – so hängt nichts vom
heutigen Datum ab, auch nicht die Zählung der Claude-Aufrufe (db.protokoll nimmt db.jetzt).

Eigene Datei statt tests/test_ende_zu_ende.py erweitern: Annahme A33 im Plan (paralleles Arbeiten, Regisseur 2.0).
Nicht Teil dieser Stufe (kommt mit Stufe 2): Paar → neue Gewichte → geänderte Erwartung. Auch nicht hier: das
📦 Upload-Paket (rendert mit ffmpeg; geprüft in tests/test_upload_paket.py und tests/test_lernbot_paket.py).
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import math
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from importlib import resources
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from clip_pipeline import big, cli, db, konfig, publikum
from clip_pipeline.bot import aktionen
from clip_pipeline.zeit import UTC, iso

from tests.hilfen import MitSpeicher
from tests.test_screenshot import KEIN_CLAUDE, FakeClaude

try:  # die Bots brauchen python-telegram-bot
    import telegram  # noqa: F401

    from clip_pipeline import lernbot, lernbot_paket, lernbot_publikum, lernbot_zahlen
    from clip_pipeline.bot import app as bot_app
    from tests.test_lernbot_zahlen import Anhang, Bot
except ImportError:
    lernbot = None

# --- Der Zeitplan (UTC; in Berlin gilt bis 25.10. Sommerzeit, also zwei Stunden mehr) ---------------------------
# Montag, 28.09., 18:00 Berlin: Clip #1 ist auf TikTok, du tippst im Clip-Bot das Häkchen
HAKEN = datetime(2026, 9, 28, 16, 0, tzinfo=UTC)
# 19:00: Entwurf 41 ist auch oben, du schickst dem Lern-Bot den Link
LINK = HAKEN + timedelta(hours=1)
# Die Zahlen kommen am Tag 3 (von Hand) und am Tag 7 (Screenshot) – je gerechnet ab dem Post
TAG_3 = timedelta(days=3)
TAG_7 = timedelta(days=7)
# Dienstag, 06.10., 10:00 Berlin: der Timer clip-publikum (beide Posts sind dann über 7 Tage alt)
BEWERTEN = datetime(2026, 10, 6, 8, 0, tzinfo=UTC)
# Mittwoch, 07.10., 10:00 Berlin: derselbe Timer am nächsten Tag
NOCHMAL = BEWERTEN + timedelta(days=1)
# Mittwoch, 11:00 Berlin: du tippst /publikum. Die Woche begann Montag, 05.10. – beide Screenshots zählen.
ANZEIGE = NOCHMAL + timedelta(hours=1)

ERLAUBT = 42  # deine Telegram-Nutzer-ID in den Fake-Bots
ENTWURF = 41  # die Nummer des Entwurfs, wie sie im Lern-Bot steht
TIKTOK_LINK = "https://www.tiktok.com/@baddieday/video/7300123456789012345"
TIKTOK_ID = "7300123456789012345"

# Merkmale des Clips, wie die Vorbewertung sie vor der Lernschleife gespeichert hat (Triple Kill)
CLIP_MERKMALE = {"kill_punkte": 6.0, "victory_royale": 0.0, "laenge": 20.0, "lautstaerke": 0.0, "kommentar": 0.0}
# Ein Moment des Regisseurs ohne Clip (Mikro-Lacher), mit seinen Merkmalen aus der Stimmungs-Analyse
LACHER = "datei:abend-2026-09-27-2"
LACHER_MERKMALE = {"lachen": 2, "spitzen": 1}

# Tag-7-Zahlen, wie Claude sie aus dem Screenshot liest – gewählt, damit man r und e im Kopf nachrechnen kann:
#   Clip-Post (22 s):    r = 11 / 22 = 0,5 · e = (300 + 2·20 + 30 + 10) / 5000 = 0,076
#   Entwurfs-Post (31 s): r = 15,5 / 31 = 0,5 · e = (100 + 2·8 + 12 + 4) / 2000 = 0,066
SCREENSHOT_CLIP = {"views": 5000, "likes": 300, "kommentare": 10, "shares": 20, "saves": 30, "wiedergabe_s": 11.0,
                   "voll_prozent": 40.0}
SCREENSHOT_ENTWURF = {"views": 2000, "likes": 100, "kommentare": 4, "shares": 8, "saves": 12, "wiedergabe_s": 15.5,
                      "voll_prozent": 35.0}


@unittest.skipIf(lernbot is None, "python-telegram-bot fehlt")
class EndeZuEndePublikum(MitSpeicher):
    """Die ganze Kette der Stufe 1 in einem Durchlauf (Aufbau im Modul-Docstring)."""

    def setUp(self):
        super().setUp()
        # Die Datenbank aus MitSpeicher ist schon neu – wir brauchen eine im alten Stand (Schritt 1)
        self.con.close()
        self.pfad = self.tmp / "clips-alt.db"
        self.konfig.daten["datenbank"]["pfad"] = str(self.pfad)
        self.einstellungen()
        self.nie_wecken_und_eine_uhr()
        self.alte_datenbank_anlegen()

    # --- Aufbau -----------------------------------------------------------------------------------------------

    def einstellungen(self) -> None:
        """Feste Werte – der Test soll nicht von config/lokal.toml abhängen."""
        k = self.konfig.daten
        k["publikum"].update(plattformen=["tiktok"], alter_tage=7, mindest_alter_tage=3, fenster=20)
        k["publikum"]["gewichte"] = {"wiedergabe": 0.5, "engagement": 0.3, "reichweite": 0.2}
        k["shorts"].update(endcard=True, endcard_s=2.5)  # Clip 20 s → Short 22 s (shorts.gesamtdauer)
        k["veroeffentlichung"].update(pflicht=["youtube", "tiktok"], zusatz=["clipbattle"])
        k["telegram"].update(leise_von="23:00", leise_bis="08:00")
        k["zeit"]["zeitzone"] = "Europe/Berlin"
        k["lernbot"].update(screenshot_claude=True, screenshot_timeout_s=120,
                            screenshot_prompt="templates/screenshot-prompt.txt")
        k["decide"]["programm"] = KEIN_CLAUDE  # Sicherheitsnetz: ein vergessener Fake findet kein echtes claude
        k.setdefault("regie", {})["ordner"] = str(self.tmp / "regie")
        # Getrennter Betrieb wie auf dem Mini (E19): Puffer mit Marke; das Lager (pve-big) ist gesetzt, existiert
        # hier aber nicht. Am Ende prüft der Test, dass dort nichts angelegt wurde (Schreiben ins Lager);
        # Weckversuche fängt nie_wecken_und_eine_uhr ab
        (self.konfig.wurzel / ".clip-puffer").touch()
        self.lager = self.tmp / "lager-auf-pve-big"
        k["lager"]["wurzel"] = str(self.lager)

    def nie_wecken_und_eine_uhr(self) -> None:
        """Wecken wäre sichtbar (Muster tests/test_speicher_wecken.py), Netz ist verboten, alle Uhren sind eine.
        Screenshots landen in einem eigenen Temp-Ordner – so lässt sich zählen, ob ein Bild liegen bleibt."""
        # wecken_warten_s = 0: ein versehentlicher Weckversuch scheitert sofort (und steht im Mock), statt zu warten
        self.konfig.daten["speicher"].update(host="pve-gross", wol_mac="aa:bb:cc:dd:ee:ff", wecken_warten_s=0)
        self.konfig.daten["big"]["host"] = "pve-gross"
        self.temp = self.tmp / "temp"
        self.temp.mkdir()
        self.uhr = HAKEN
        self.wol, self.wach, self.herz = mock.Mock(), mock.Mock(), mock.Mock()
        patches = [
            mock.patch.object(konfig.Konfig, "_host_erreichbar", return_value=False),
            mock.patch("clip_pipeline.konfig.sende_wake_on_lan", self.wol),
            mock.patch.object(big, "wach_halten", self.wach),
            mock.patch.object(big, "herzschlag", self.herz),
            mock.patch("socket.create_connection", side_effect=AssertionError("Netzwerkzugriff im Test")),
            mock.patch.object(tempfile, "tempdir", str(self.temp)),
            mock.patch.object(cli, "lade", return_value=self.konfig),  # die CLI liest keine echte Konfig-Datei
        ]
        # Eine Uhr für alle Module, die in dieser Kette nach der Zeit fragen (siehe Modul-Docstring)
        for modul in (aktionen, bot_app, cli, db, lernbot, lernbot_paket, lernbot_publikum, lernbot_zahlen, publikum):
            patches.append(mock.patch.object(modul, "jetzt", side_effect=lambda: self.uhr))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def alte_datenbank_anlegen(self) -> None:
        """Stand VOR der Lernschleife: nur schema.sql, regie.sql und lager.sql (ohne publikum.sql, ohne die neuen
        Spalten aus db.MIGRATIONEN), dazu Beispielzeilen, wie sie auf dem Mini liegen könnten. Absichtlich mit
        sqlite3 direkt statt db.verbinde – db.verbinde würde sofort migrieren."""
        start = datetime(2026, 9, 27, 18, 15, tzinfo=UTC)
        alt = sqlite3.connect(self.pfad)
        for datei in ("schema.sql", "regie.sql", "lager.sql"):
            alt.executescript(resources.files("clip_pipeline").joinpath(datei).read_text(encoding="utf-8"))
        alt.execute("INSERT INTO matches (id, replay_pfad, start_utc, ende_utc, status, platzierung, kills, erstellt,"
                    " geaendert) VALUES ('2026-09-27_20-15-33', 'replays/2026-09-27_20-15-33.replay', ?, ?,"
                    " 'verarbeitet', 4, 5, ?, ?)", (iso(start), iso(start + timedelta(minutes=20)), iso(start),
                                                    iso(start)))
        # Clip #1: freigegeben, 0–20 s aus der Aufnahme, schon mit Telegram-file_id
        alt.execute("INSERT INTO clips (id, match_id, nr, status, titel, typ, kills, max_gruppe, kill_zeiten, start_utc,"
                    " ende_utc, quelle_pfad, quelle_start_s, quelle_ende_s, merkmale, punkte, begruendung, tg_file_id,"
                    " erstellt, geaendert) VALUES (1, '2026-09-27_20-15-33', 1, 'freigegeben', 'Triple Kill',"
                    " 'triple', 3, 3, '[]', ?, ?, 'eingang/fortnite.mp4', 0, 20, ?, 6, 'Triple Kill', 'F1', ?, ?)",
                    (iso(start), iso(start + timedelta(seconds=30)), json.dumps(CLIP_MERKMALE), iso(start),
                     iso(start)))
        # YouTube war schon vor der Lernschleife erledigt – mit TikTok ist der Clip dann „veröffentlicht“
        alt.execute("INSERT INTO veroeffentlichungen (clip_id, plattform, erledigt, url) VALUES (1, 'youtube', ?,"
                    " 'https://youtube.com/shorts/abc')", (iso(HAKEN - timedelta(hours=2)),))
        # Regisseur: ein Lacher-Moment, ein Musiktitel, Entwurf 41 (Short, 31 s) mit 👍
        alt.execute("INSERT INTO momente (schluessel, clip_id, match_id, datei, start_s, ende_s, stimmung, sicherheit,"
                    " quelle, merkmale, erstellt, geaendert) VALUES (?, NULL, '2026-09-27_20-15-33',"
                    " '/srv/clips/eingang/fortnite.mp4', 300, 308, 'lustig', 0.8, 'regel', ?, ?, ?)",
                    (LACHER, json.dumps(LACHER_MERKMALE), iso(start), iso(start)))
        alt.execute("INSERT INTO tracks (id, datei, titel, kuenstler, quelle, sha256, erstellt) VALUES (1, 'titel.mp3',"
                    " 'Titel', 'NCS', 'Titel – NCS (NoCopyrightSounds)', 'abc', ?)", (iso(start),))
        alt.execute("INSERT INTO entwuerfe (id, name, format, schnittliste, parameter, track_id, dauer_s, status,"
                    " erstellt) VALUES (?, 'short-41', 'short', ?, '{\"beats_pro_schnitt\": 2}', 1, 31.0, 'bewertet',"
                    " ?)", (ENTWURF, str(self.schnittliste_anlegen()), iso(start)))
        alt.execute("INSERT INTO entwurf_bewertungen (entwurf_id, daumen, gruende, erstellt, geaendert)"
                    " VALUES (?, 1, '[]', ?, ?)", (ENTWURF, iso(start), iso(start)))
        # Eine alte Lern-Meldung, schon verschickt – sie darf nicht noch einmal kommen
        alt.execute("INSERT INTO lern_meldungen (schluessel, text, erstellt, gesendet) VALUES ('abend:2026-09-27',"
                    " '📋 Stand: …', ?, ?)", (iso(start), iso(start)))
        alt.commit()
        alt.close()

    def schnittliste_anlegen(self) -> Path:
        """Schnittliste von Entwurf 41 (Form wie regie.erstelle): Clip #1, der Lacher, dann Clip #1 noch einmal
        (Jump-Cut – zählt als EIN Moment). Sie liegt auf dem Mini, nicht im Lager."""
        segmente = [{"nr": nr, "moment": moment, "clip_id": clip_id, "zeit_start": von, "zeit_ende": bis}
                    for nr, moment, clip_id, von, bis in ((1, "clip:1", 1, 0.0, 9.0), (2, LACHER, None, 9.0, 17.0),
                                                          (3, "clip:1", 1, 17.0, 31.0))]
        liste = {"version": 3, "art": "regie", "name": "short-41", "format": "short", "aufloesung": [1080, 1920],
                 "fps": 30, "dauer_s": 31.0, "stimmung": "episch", "parameter": {"beats_pro_schnitt": 2},
                 "musik": {"datei": "titel.mp3", "titel": "Titel", "kuenstler": "NCS",
                           "quelle": "Titel – NCS (NoCopyrightSounds)", "bpm": 128, "start_s": 0.0, "pegel": 0.3},
                 "segmente": segmente, "hinweise": []}
        pfad = self.tmp / "regie" / "short-41.json"
        pfad.parent.mkdir(parents=True, exist_ok=True)
        pfad.write_text(json.dumps(liste), encoding="utf-8")
        return pfad

    # --- Die Bots und die CLI ohne Telegram ---------------------------------------------------------------------

    def bots_starten(self) -> None:
        """Clip-Bot und Lern-Bot, je mit eigener Verbindung zur selben Datei (wie zwei Dienste auf dem Mini)."""
        self.clip_bot_con = db.verbinde(self.pfad)
        self.addCleanup(self.clip_bot_con.close)
        self.lern_bot_con = db.verbinde(self.pfad)
        self.addCleanup(self.lern_bot_con.close)
        self.lern_bot = Bot()
        self.lern_app = SimpleNamespace(bot=self.lern_bot, bot_data={"con": self.lern_bot_con, "konfig": self.konfig,
                                                                     "erlaubt": ERLAUBT})
        self.lern_context = SimpleNamespace(bot_data=self.lern_app.bot_data, application=self.lern_app, args=[])

    def clip_bot_klick(self, daten: str) -> tuple[list, list]:
        """Knopf im Clip-Bot (bot.app.bei_klick): (Antworten am Knopf, neue Nachrichtentexte)."""
        antworten, texte = [], []

        async def answer(text=None, **_):
            antworten.append(text)

        async def edit_message_text(text, **_):
            texte.append(text)

        query = SimpleNamespace(data=daten, from_user=SimpleNamespace(id=ERLAUBT), answer=answer,
                                edit_message_text=edit_message_text, message=SimpleNamespace(reply_markup=None))
        context = SimpleNamespace(bot_data={"con": self.clip_bot_con, "konfig": self.konfig, "erlaubt": ERLAUBT})
        asyncio.run(bot_app.bei_klick(SimpleNamespace(callback_query=query), context))
        return antworten, texte

    def lern_bot_befehl(self, befehl, *args: str) -> list[str]:
        """/link oder /publikum im Lern-Bot: die Antworten (reply_text)."""
        antworten = []

        async def reply_text(text, **_):
            antworten.append(text)

        self.lern_context.args = list(args)
        asyncio.run(befehl(SimpleNamespace(effective_message=SimpleNamespace(reply_text=reply_text)),
                           self.lern_context))
        return antworten

    def lern_bot_text(self, text: str) -> str:
        """Freier Text an den Lern-Bot (lernbot_zahlen.bei_text): seine letzte Antwort."""
        nachricht = SimpleNamespace(photo=(), document=None, caption=None, text=text,
                                    from_user=SimpleNamespace(id=ERLAUBT))
        asyncio.run(lernbot_zahlen.bei_text(SimpleNamespace(effective_message=nachricht, callback_query=None),
                                            self.lern_context))
        return self.lern_bot.nachrichten[-1][0]

    def lern_bot_screenshot(self, bildunterschrift: str, gelesen: dict) -> tuple[list[str], FakeClaude]:
        """Foto an den Lern-Bot (lernbot_zahlen.bei_foto), claude gefälscht: (neue Antworten, der Fake).
        Die Antwort des Fakes sieht aus wie eine echte: ein Satz, dann das JSON."""
        fake = FakeClaude("Gelesen aus der TikTok-Statistik: " + json.dumps(gelesen))
        vorher = len(self.lern_bot.nachrichten)
        # photo: Telegram schickt mehrere Größen, die letzte ist die größte (lernbot_zahlen.FOTO_ENDUNG)
        nachricht = SimpleNamespace(photo=(Anhang(), Anhang()), document=None, caption=bildunterschrift, text=None,
                                    from_user=SimpleNamespace(id=ERLAUBT))
        with fake.aktiv():
            asyncio.run(lernbot_zahlen.bei_foto(SimpleNamespace(effective_message=nachricht, callback_query=None),
                                                self.lern_context))
        return [text for text, _ in self.lern_bot.nachrichten[vorher:]], fake

    def pipeline_publikum_bewerten(self) -> tuple[int, dict, list[str]]:
        """`pipeline publikum bewerten` wie vom Timer (cli.main): (Exit, JSON der letzten Zeile, alle stdout-Zeilen)."""
        aus = io.StringIO()
        with contextlib.redirect_stdout(aus), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(["publikum", "bewerten"])
        zeilen = aus.getvalue().strip().splitlines()
        return code, json.loads(zeilen[-1]), zeilen

    # --- Nachsehen (mit der Verbindung des Tests) ---------------------------------------------------------------

    def post(self, post_id: int) -> dict:
        return dict(publikum.post(self.con, post_id))

    def messungen(self, post_id: int) -> list[dict]:
        return [dict(z) for z in self.con.execute(
            "SELECT * FROM publikum_messungen WHERE post_id = ? ORDER BY gemessen_utc, id", (post_id,))]

    def lern_meldungen(self) -> list[dict]:
        return [dict(z) for z in self.con.execute("SELECT schluessel, text, gesendet FROM lern_meldungen ORDER BY id")]

    # --- Die Kette ---------------------------------------------------------------------------------------------

    def test_vom_haekchen_bis_zum_score(self):
        self.schritt_1_alte_datenbank_migrieren()
        self.bots_starten()
        clip_post = self.schritt_2_clip_bot_haekchen()
        entwurf_post = self.schritt_3_lern_bot_link()
        self.schritt_4_tag_3_von_hand(clip_post, entwurf_post)
        self.schritt_5_tag_7_screenshot(clip_post, entwurf_post)
        self.schritt_6_bewerten(clip_post, entwurf_post)
        self.schritt_7_zweiter_lauf_aendert_nichts(clip_post, entwurf_post)
        self.schritt_8_publikum_zeigt_den_score(clip_post, entwurf_post)
        # Durchgehend: kein Weckversuch, kein Herzschlag, das Lager auf pve-big nie berührt
        self.wol.assert_not_called()
        self.wach.assert_not_called()
        self.herz.assert_not_called()
        self.assertFalse(self.lager.exists())

    def schritt_1_alte_datenbank_migrieren(self) -> None:
        """db.verbinde auf der alten Datei: neue Tabellen und Spalten da, alle alten Zeilen noch da, posts leer."""
        alt = sqlite3.connect(self.pfad)
        tabellen_alt = {z[0] for z in alt.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        spalten_alt = {z[1] for z in alt.execute("PRAGMA table_info(entwuerfe)")}
        alt.close()
        self.assertNotIn("posts", tabellen_alt)  # wirklich der alte Stand
        self.assertNotIn("rezept", spalten_alt)

        self.con = db.verbinde(self.pfad)
        tabellen = {z["name"] for z in self.con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertLessEqual({"posts", "publikum_messungen", "rezept_stand", "hypothesen", "erwartungen"}, tabellen)
        self.assertIn("mic_stand", {z["name"] for z in self.con.execute("PRAGMA table_info(clips)")})
        self.assertLessEqual({"rezept", "upload_pfad"},
                             {z["name"] for z in self.con.execute("PRAGMA table_info(entwuerfe)")})
        self.assertEqual(tuple(self.con.execute("SELECT titel, status FROM clips WHERE id = 1").fetchone()),
                         ("Triple Kill", "freigegeben"))
        self.assertEqual(self.con.execute("SELECT name FROM entwuerfe WHERE id = ?", (ENTWURF,)).fetchone()[0],
                         "short-41")
        self.assertEqual(self.con.execute("SELECT daumen FROM entwurf_bewertungen").fetchone()[0], 1)
        self.assertEqual(self.con.execute("SELECT merkmale FROM momente").fetchone()[0], json.dumps(LACHER_MERKMALE))
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM veroeffentlichungen").fetchone()[0], 1)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM posts").fetchone()[0], 0)

    def schritt_2_clip_bot_haekchen(self) -> int:
        """Clip-Bot, Häkchen TikTok unter Clip #1 → Post (art clip), Dauer aus der Datenbank, Rezept abgeleitet."""
        self.uhr = HAKEN
        antworten, texte = self.clip_bot_klick("t:1")
        self.assertEqual(antworten, ["TikTok ✅ – veröffentlicht!"])  # YouTube war schon erledigt (alte Zeile)
        post = publikum.post_zu(self.con, "clip", 1, "tiktok")
        self.assertIsNotNone(post)
        post_id = post["id"]
        # Die Checkliste nennt die Post-Nummer – die brauchst du im Lern-Bot für die Zahlen
        self.assertEqual(len(texte), 1)
        self.assertIn(f"📈 Post #{post_id} (TikTok) – für die Zahlen: Screenshot an den Lern-Bot, "
                      f"Bildunterschrift #{post_id}", texte[0])
        self.assertEqual((post["art"], post["ziel"], post["plattform"]), ("clip", "clip:1", "tiktok"))
        self.assertEqual(post["gepostet_utc"], iso(HAKEN))
        self.assertEqual(post["dauer_s"], 22.0)  # 20 s Clip + 2,5 s Endcard − 0,5 s Überblendung
        self.assertEqual(json.loads(post["rezept"]), {"hook": "stark_zuerst", "laenge": "kurz", "tempo": "none",
                                                      "machart": "roh", "experiment": False})
        self.assertEqual(json.loads(post["merkmale"])["momente"][0]["merkmale"], CLIP_MERKMALE)
        self.assertIsNone(post["url"])  # der Link käme erst mit /link im Clip-Bot
        self.assertEqual(self.con.execute("SELECT status FROM clips WHERE id = 1").fetchone()[0], "veroeffentlicht")
        return post_id

    def schritt_3_lern_bot_link(self) -> int:
        """Lern-Bot, /link e41 <TikTok-Link> → Post (art entwurf) mit url und video_id; Rezept und Merkmale aus der
        Schnittliste und den alten Zeilen (Clip-Merkmale, Lacher-Moment, Musik)."""
        self.uhr = LINK
        (antwort,) = self.lern_bot_befehl(lernbot_paket.cmd_link, f"e{ENTWURF}", TIKTOK_LINK)
        post = publikum.post_zu(self.con, "entwurf", ENTWURF, "tiktok")
        self.assertIsNotNone(post)
        post_id = post["id"]
        self.assertEqual(antwort, f"🔗 Post #{post_id} (TikTok) gespeichert – für Screenshots: Bildunterschrift "
                                  f"#{post_id}")
        self.assertEqual((post["art"], post["entwurf_id"], post["url"], post["video_id"]),
                         ("entwurf", ENTWURF, TIKTOK_LINK, TIKTOK_ID))
        self.assertEqual((post["gepostet_utc"], post["dauer_s"]), (iso(LINK), 31.0))
        self.assertEqual(json.loads(post["rezept"]), {"hook": "aufbau", "laenge": "mittel", "tempo": "beat2",
                                                      "machart": "regie", "experiment": False})
        merkmale = json.loads(post["merkmale"])
        self.assertEqual([m["moment"] for m in merkmale["momente"]], ["clip:1", LACHER])  # Jump-Cut zählt einmal
        self.assertEqual([m["merkmale"] for m in merkmale["momente"]], [CLIP_MERKMALE, LACHER_MERKMALE])
        self.assertEqual(merkmale["hook_moment"], "clip:1")
        self.assertEqual(merkmale["musik"], {"titel": "Titel", "quelle": "Titel – NCS (NoCopyrightSounds)"})
        return post_id

    def schritt_4_tag_3_von_hand(self, clip_post: int, entwurf_post: int) -> None:
        """Tag 3: „#<nr> views likes wiedergabe voll%“ als Text an den Lern-Bot → Messung mit Quelle hand."""
        self.uhr = HAKEN + TAG_3
        self.assertEqual(self.lern_bot_text(f"#{clip_post} 1240 61 6,8 34"),
                         f"💾 #{clip_post} gespeichert (von Hand): 👁 1 240 · ❤️ 61 · 💬 – · ↗️ – · 🔖 – · ⏱ 6,8 s · "
                         "✅ 34 %")
        self.uhr = LINK + TAG_3
        self.assertTrue(self.lern_bot_text(f"#{entwurf_post} 800 40 9,5 30").startswith(
            f"💾 #{entwurf_post} gespeichert (von Hand)"))
        (messung,) = self.messungen(clip_post)
        self.assertEqual((messung["quelle"], messung["gemessen_utc"], messung["views"], messung["wiedergabe_s"],
                          messung["shares"], messung["roh"]), ("hand", iso(HAKEN + TAG_3), 1240, 6.8, None, None))
        self.assertEqual(len(self.messungen(entwurf_post)), 1)

    def schritt_5_tag_7_screenshot(self, clip_post: int, entwurf_post: int) -> None:
        """Tag 7: Screenshot mit „#<nr>“ → Claude liest (gefälscht), die Zahlen passen zu Tag 3 → gespeichert.
        Claude sieht nur das eine Bild, und danach liegt kein Bild mehr herum (kein Bildarchiv, Spec §7.1)."""
        for post_id, gepostet, gelesen, antwort in (
                (clip_post, HAKEN, SCREENSHOT_CLIP, "👁 5 000 · ❤️ 300 · 💬 10 · ↗️ 20 · 🔖 30 · ⏱ 11 s · ✅ 40 %"),
                (entwurf_post, LINK, SCREENSHOT_ENTWURF, "👁 2 000 · ❤️ 100 · 💬 4 · ↗️ 8 · 🔖 12 · ⏱ 15,5 s · ✅ 35 %")):
            self.uhr = gepostet + TAG_7
            antworten, fake = self.lern_bot_screenshot(f"Stand Tag 7 #{post_id}", gelesen)
            self.assertEqual(antworten, [f"🔎 Lese die Zahlen für #{post_id} …",
                                         f"💾 #{post_id} gespeichert (Screenshot): {antwort}"])
            (aufruf,) = fake.aufrufe
            self.assertEqual(aufruf["dateien"], ["screenshot.jpg"])            # nur das Bild, sonst nichts
            self.assertIn("--allowedTools", aufruf["befehl"])
            self.assertEqual(aufruf["befehl"][aufruf["befehl"].index("--allowedTools") + 1], "Read")  # nur lesen
            messung = self.messungen(post_id)[-1]
            self.assertEqual((messung["quelle"], messung["gemessen_utc"]), ("screenshot", iso(gepostet + TAG_7)))
            self.assertEqual({feld: messung[feld] for feld in publikum.FELDER}, gelesen)
            self.assertIn(json.dumps(gelesen), messung["roh"])  # Claudes Antwort bleibt zum Nachprüfen erhalten
        self.assertEqual(sorted(p.name for p in self.temp.iterdir()), [])  # beide Bilder gelöscht
        self.assertEqual([z["text"] for z in self.con.execute("SELECT text FROM ereignisse WHERE art = 'claude'")],
                         ["screenshot: ok", "screenshot: ok"])  # jeder Claude-Aufruf ist gezählt

    def schritt_6_bewerten(self, clip_post: int, entwurf_post: int) -> None:
        """Timer: `pipeline publikum bewerten` → beide Scores gesetzt, aus der Tag-7-Messung, Score 0 mit „Basis zu
        klein“. Älteste zuerst: Der Clip-Post ist schon Basis des Entwurfs-Posts (basis_n 0 bzw. 1). Die Meldung
        dazu kommt im Lern-Bot an (10:05 – außerhalb der Ruhezeit)."""
        self.uhr = BEWERTEN
        code, ergebnis, zeilen = self.pipeline_publikum_bewerten()
        self.assertEqual(code, 0)
        self.assertEqual(len(zeilen), 1, zeilen)  # stdout: genau eine JSON-Zeile (Logs gehen nach stderr)
        self.assertEqual(ergebnis, {"bewertet": 2, "ohne_messung": 0, "noch_zu_jung": 0, "fehler": 0,
                                    "posts": [{"id": clip_post, "score": 0.0}, {"id": entwurf_post, "score": 0.0}],
                                    "meldung": True})

        for post_id, r, e, views, basis_n in ((clip_post, 0.5, 0.076, 5000, 0), (entwurf_post, 0.5, 0.066, 2000, 1)):
            post = self.post(post_id)
            self.assertEqual((post["score"], post["bewertet_utc"]), (0.0, iso(BEWERTEN)))
            teile = json.loads(post["score_teile"])
            self.assertEqual(teile["messung_id"], self.messungen(post_id)[-1]["id"])  # die Tag-7-Messung, nicht Tag 3
            self.assertEqual(teile["messung_alter_tage"], 7.0)
            self.assertAlmostEqual(teile["r"], r)
            self.assertAlmostEqual(teile["e"], e)
            self.assertAlmostEqual(teile["v"], math.log(1 + views))
            self.assertEqual((teile["z_r"], teile["z_e"], teile["z_v"]), (0.0, 0.0, 0.0))
            self.assertEqual((teile["basis_n"], teile["basis_n_r"]), (basis_n, basis_n))
            self.assertEqual(teile["vermerke"], ["Basis zu klein"])  # alle Zähler da – kein „unvollständig“

        # Die Meldung: angelegt vom Befehl, verschickt vom Lern-Bot (die alte Abend-Meldung nicht noch einmal)
        text = (f"📊 2 Posts bewertet: #{clip_post} 0 · #{entwurf_post} 0 – /publikum\n"
                f"ℹ️ Score 0 = noch zu wenige bewertete Posts zum Vergleich – echte Scores ab 5 bewerteten Posts.")
        self.assertEqual([m["schluessel"] for m in self.lern_meldungen()],
                         ["abend:2026-09-27", "publikum:bewertet:2026-10-06"])
        self.uhr = BEWERTEN + timedelta(minutes=5)
        self.assertEqual(asyncio.run(lernbot.sende_meldungen(self.lern_app)), 1)
        self.assertEqual(self.lern_bot.texte[-1], (ERLAUBT, text))
        self.assertEqual(asyncio.run(lernbot.sende_meldungen(self.lern_app)), 0)  # nicht doppelt

    def schritt_7_zweiter_lauf_aendert_nichts(self, clip_post: int, entwurf_post: int) -> None:
        """Nächster Tag, derselbe Timer: nichts fällig, nichts überschrieben, keine neue Meldung."""
        vorher = [self.post(clip_post), self.post(entwurf_post)]
        messungen_vorher = self.con.execute("SELECT COUNT(*) FROM publikum_messungen").fetchone()[0]
        self.uhr = NOCHMAL
        code, ergebnis, _ = self.pipeline_publikum_bewerten()
        self.assertEqual(code, 0)
        self.assertEqual(ergebnis, {"bewertet": 0, "ohne_messung": 0, "noch_zu_jung": 0, "fehler": 0, "posts": [],
                                    "meldung": False})
        self.assertEqual([self.post(clip_post), self.post(entwurf_post)], vorher)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM publikum_messungen").fetchone()[0], messungen_vorher)
        self.assertEqual(len(self.lern_meldungen()), 2)

    def schritt_8_publikum_zeigt_den_score(self, clip_post: int, entwurf_post: int) -> None:
        """/publikum im Lern-Bot: neueste zuerst, je Post die letzte Messung (Tag 7) und „Score 0 (Basis zu klein)“,
        unten die Claude-Aufrufe dieser Woche (die zwei Screenshots vom Montag)."""
        self.uhr = ANZEIGE
        (antwort,) = self.lern_bot_befehl(lernbot_publikum.cmd_publikum)
        t = lernbot_publikum.TAUSENDER  # schmales geschütztes Leerzeichen in „2 000“
        self.assertEqual(antwort.splitlines(), [
            "📊 Publikum · 2 Posts, 2 mit Score (neueste zuerst)",
            f"#{entwurf_post} TikTok · Entwurf {ENTWURF} · 8 Tage · 👁 2{t}000 ❤️ 100 ⏱ 15,5 s ✅ 35 % (Tag 7) · "
            "Score 0 (Basis zu klein)",
            f"#{clip_post} TikTok · Clip 1 · 8 Tage · 👁 5{t}000 ❤️ 300 ⏱ 11 s ✅ 40 % (Tag 7) · Score 0 (Basis zu klein)",
            "🤖 Claude diese Woche: 2 Aufrufe",
        ])


if __name__ == "__main__":
    unittest.main()
