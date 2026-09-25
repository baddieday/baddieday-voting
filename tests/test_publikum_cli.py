"""Tests für `pipeline publikum bewerten` und seinen Weg bis in den Lern-Bot (Spec §6, §12, §14 Stufe 1).

Die Kette, die hier geprüft wird:
  1. Der Timer ruft `pipeline publikum bewerten` (cli.py): Scores setzen, eine JSON-Zeile, Exit-Code – ohne
     Pipeline-Sperre, ohne pve-big zu wecken.
  2. Sind neue Scores gesetzt, legt der Befehl EINE Lern-Meldung je Tag an (lernbot_publikum.meldung_nach_bewerten).
  3. Der Lern-Bot schickt sie – aber nicht in der Ruhezeit (lernbot_publikum.faellige_lern_meldungen, eingehängt in
     lernbot.sende_meldungen).
  4. `/publikum` zeigt die Posts mit Zahlen und Score, `/hilfe` erklärt die neuen Knöpfe und Befehle.

Alle Zeiten sind fest (T0 und Tage danach aus tests/test_publikum.py) – kein Test hängt vom heutigen Datum ab.
Telegram ist gefälscht (FakeBot aus tests/test_lernbot.py, eine kleine Nachricht mit reply_text hier).
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import re
from datetime import datetime, timedelta
from html.parser import HTMLParser
from types import SimpleNamespace
from unittest import mock

from clip_pipeline import big, cli, db, konfig, lernbot_publikum, publikum, sperre
from clip_pipeline.zeit import UTC, iso

from tests.test_publikum import T0, MitPublikum, tag

try:  # der Lern-Bot braucht python-telegram-bot; die reine Rechnung nicht
    import telegram  # noqa: F401

    from clip_pipeline import lernbot
    from tests.test_lernbot import FakeBot
except ImportError:
    lernbot = None


def ortszeit(jahr: int, monat: int, tag_: int, stunde: int, minute: int = 0) -> datetime:
    """Ein Zeitpunkt in Berliner Ortszeit als UTC – im September gilt Sommerzeit (UTC+2)."""
    return datetime(jahr, monat, tag_, stunde, minute, tzinfo=UTC) - timedelta(hours=2)


class MitBewertung(MitPublikum):
    """Posts, Messungen und feste Ruhezeit (23:00–08:00) – unabhängig von einer lokalen Konfiguration."""

    def setUp(self):
        super().setUp()
        self.konfig.daten["telegram"].update(leise_von="23:00", leise_bis="08:00")
        self.konfig.daten["zeit"]["zeitzone"] = "Europe/Berlin"

    def gemessener_post(self, *, gepostet: datetime = T0, views: int = 1240, wiedergabe_s: float = 6.8,
                        am: float = 7.0) -> int:
        """Ein Post mit einer Messung `am` Tage nach dem Posten (Hand-Eingabe, wie in der Abnahme)."""
        post_id = self.post(gepostet=gepostet)
        self.messung(post_id, gepostet + timedelta(days=am), views=views, likes=61, wiedergabe_s=wiedergabe_s)
        return post_id

    def lern_meldungen(self) -> list:
        return self.con.execute("SELECT schluessel, text, gesendet FROM lern_meldungen ORDER BY id").fetchall()


# --- 1. pipeline publikum bewerten ----------------------------------------------------------------------------

class CliBewerten(MitBewertung):
    def lauf(self, zeit: datetime = tag(8)) -> tuple[int, dict, list[str]]:
        """cli.main mit der Test-Konfig und fester Zeit: (Exit, letzte stdout-Zeile als JSON, alle stdout-Zeilen)."""
        aus = io.StringIO()
        with mock.patch.object(cli, "lade", return_value=self.konfig), mock.patch.object(cli, "jetzt", return_value=zeit), \
                contextlib.redirect_stdout(aus), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(["publikum", "bewerten"])
        zeilen = aus.getvalue().strip().splitlines()
        return code, json.loads(zeilen[-1]), zeilen

    def test_json_zeile_exit_0_und_meldung(self):
        post_id = self.gemessener_post()
        code, ergebnis, zeilen = self.lauf()
        self.assertEqual(code, 0)
        self.assertEqual(len(zeilen), 1, zeilen)  # Logs gehen nach stderr – stdout ist genau eine JSON-Zeile
        self.assertEqual((ergebnis["bewertet"], ergebnis["fehler"], ergebnis["meldung"]), (1, 0, True))
        self.assertEqual(ergebnis["posts"], [{"id": post_id, "score": 0.0}])
        zeile = publikum.post(self.con, post_id)
        self.assertEqual((zeile["score"], zeile["bewertet_utc"]), (0.0, iso(tag(8))))  # die Zeit des Laufs
        self.assertIn("Basis zu klein", json.loads(zeile["score_teile"])["vermerke"])  # Abnahme: Score 0 (Plan)
        meldungen = self.lern_meldungen()
        self.assertEqual([m["schluessel"] for m in meldungen], ["publikum:bewertet:2026-09-09"])

    def test_zweiter_lauf_aendert_nichts(self):
        post_id = self.gemessener_post()
        self.lauf()
        vorher = dict(publikum.post(self.con, post_id))
        code, ergebnis, _ = self.lauf(tag(8) + timedelta(hours=1))
        self.assertEqual(code, 0)
        self.assertEqual((ergebnis["bewertet"], ergebnis["posts"], ergebnis["meldung"]), (0, [], False))
        self.assertEqual(dict(publikum.post(self.con, post_id)), vorher)  # Score nie überschrieben
        self.assertEqual(len(self.lern_meldungen()), 1)

    def test_nichts_faellig_keine_meldung(self):
        self.gemessener_post(gepostet=tag(5), am=3)  # erst 3 Tage alt am Tag 8
        code, ergebnis, _ = self.lauf()
        self.assertEqual(code, 0)
        self.assertEqual((ergebnis["bewertet"], ergebnis["noch_zu_jung"], ergebnis["meldung"]), (0, 1, False))
        self.assertEqual(self.lern_meldungen(), [])

    def test_ohne_posts(self):
        code, ergebnis, _ = self.lauf()
        self.assertEqual(code, 0)
        self.assertEqual(ergebnis, {"bewertet": 0, "ohne_messung": 0, "noch_zu_jung": 0, "fehler": 0, "posts": [],
                                    "meldung": False})

    def test_fehler_exit_1_mit_json_und_die_anderen_trotzdem_bewertet(self):
        kaputt = self.bewerteter_post(gepostet=T0 - timedelta(days=20), r=0.3, e=0.05, v=6.0)
        self.con.execute("UPDATE posts SET score_teile = '{kaputt' WHERE id = ?", (kaputt,))
        opfer = self.gemessener_post()                                    # hat den kaputten Post in der Basis
        anderer = self.post(gepostet=T0 - timedelta(days=30), plattform="youtube")  # andere Plattform, andere Basis
        self.messung(anderer, T0 - timedelta(days=23), views=50, likes=1)
        with self.assertLogs("pipeline", "WARNING") as logs:
            code, ergebnis, zeilen = self.lauf()
        self.assertEqual(code, 1)
        self.assertEqual(len(zeilen), 1)  # die JSON-Zeile kommt trotzdem
        self.assertEqual((ergebnis["fehler"], ergebnis["bewertet"]), (1, 1))
        self.assertEqual(ergebnis["posts"], [{"id": anderer, "score": 0.0}])
        self.assertTrue(any(f"#{opfer}" in z for z in logs.output), logs.output)
        self.assertIsNone(publikum.post(self.con, opfer)["bewertet_utc"])
        self.assertTrue(ergebnis["meldung"])  # der bewertete Post wird trotzdem gemeldet

    def test_fehlender_konfig_schluessel_exit_2(self):
        self.gemessener_post()
        del self.konfig.daten["publikum"]["alter_tage"]
        code, ergebnis, _ = self.lauf()
        self.assertEqual(code, 2)
        self.assertEqual(ergebnis["fehler"], "konfig")
        self.assertIn("alter_tage", ergebnis["hinweis"])

    def test_braucht_keine_pipeline_sperre(self):
        # Reine Datenbank-Arbeit: Läuft gerade ein render (Sperre belegt), wartet der Timer nicht (Plan Leitplanke 2)
        self.gemessener_post()
        self.konfig.daten["sperre"]["warten_s"] = 0
        with sperre.sperre(self.konfig.datenbank.with_suffix(".lock"), warten_s=0):
            code, ergebnis, _ = self.lauf()
        self.assertEqual((code, ergebnis["bewertet"]), (0, 1))

    def test_befehl_im_parser_ohne_sperre_und_nicht_in_wecken(self):
        args = cli.baue_parser().parse_args(["publikum", "bewerten"])
        self.assertEqual((args.fn, args.sperren, args.aktion), (cli._cmd_publikum, False, "bewerten"))
        self.assertNotIn("publikum", cli.WECKEN)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.baue_parser().parse_args(["publikum"])  # ohne Unterbefehl: Aufruf-Fehler (argparse, Exit 2)


class NieWecken(MitBewertung):
    """Spec §12: kein Schritt der Lernschleife weckt pve-big (Muster tests/test_speicher_wecken.py)."""

    def setUp(self):
        super().setUp()
        self.konfig.daten["speicher"].update(host="pve-gross", wol_mac="aa:bb:cc:dd:ee:ff", wecken_warten_s=60)
        self.konfig.daten["big"]["host"] = "pve-gross"
        for p in (mock.patch.object(konfig.Konfig, "_host_erreichbar", return_value=False),
                  mock.patch("socket.create_connection", side_effect=AssertionError("Netzwerkzugriff im Test"))):
            p.start()
            self.addCleanup(p.stop)

    def test_bewerten_und_publikum_text_wecken_nie(self):
        self.gemessener_post()
        aus = io.StringIO()
        with mock.patch("clip_pipeline.konfig.sende_wake_on_lan") as wol, \
                mock.patch.object(big, "wach_halten") as wach, mock.patch.object(big, "herzschlag") as herz, \
                mock.patch.object(cli, "lade", return_value=self.konfig), \
                mock.patch.object(cli, "jetzt", return_value=tag(8)), \
                contextlib.redirect_stdout(aus), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(["publikum", "bewerten"])
            text = lernbot_publikum.publikum_text(self.con, self.konfig, zeit=tag(8))
            lernbot_publikum.faellige_lern_meldungen(self.con, self.konfig, tag(8))
        self.assertEqual(code, 0, aus.getvalue())
        self.assertIn("Score 0", text)
        wol.assert_not_called()
        wach.assert_not_called()
        herz.assert_not_called()


# --- 2. Meldung nach dem Bewerten ----------------------------------------------------------------------------

class MeldungNachBewerten(MitBewertung):
    def bewertet(self, *scores: float) -> dict:
        """Ein Ergebnis wie aus bewerte_alle – mit echten Posts, deren Score schon gesetzt ist."""
        posts = []
        for score in scores:
            post_id = self.bewerteter_post(gepostet=T0, r=0.3, e=0.05, v=6.0)
            teile = {"r": 0.3, "e": 0.05, "v": 6.0, "vermerke": []}
            self.con.execute("UPDATE posts SET score = ?, score_teile = ? WHERE id = ?",
                             (score, json.dumps(teile), post_id))
            posts.append({"id": post_id, "score": score})
        return {"bewertet": len(posts), "ohne_messung": 0, "noch_zu_jung": 0, "fehler": 0, "posts": posts}

    def test_zwei_posts_eine_meldung(self):
        ergebnis = self.bewertet(0.8, -0.3)
        a, b = (p["id"] for p in ergebnis["posts"])
        self.assertTrue(lernbot_publikum.meldung_nach_bewerten(self.con, ergebnis, tag(8)))
        (m,) = self.lern_meldungen()
        self.assertEqual(m["schluessel"], "publikum:bewertet:2026-09-09")
        self.assertEqual(m["text"], f"📊 2 Posts bewertet: #{a} +0,8 · #{b} −0,3 – /publikum")

    def test_je_tag_hoechstens_eine(self):
        self.assertTrue(lernbot_publikum.meldung_nach_bewerten(self.con, self.bewertet(1.2), tag(8)))
        self.assertFalse(lernbot_publikum.meldung_nach_bewerten(self.con, self.bewertet(0.5), tag(8.4)))
        self.assertTrue(lernbot_publikum.meldung_nach_bewerten(self.con, self.bewertet(0.5), tag(9)))
        self.assertEqual([m["schluessel"] for m in self.lern_meldungen()],
                         ["publikum:bewertet:2026-09-09", "publikum:bewertet:2026-09-10"])

    def test_ohne_neue_scores_keine_meldung(self):
        leer = {"bewertet": 0, "ohne_messung": 2, "noch_zu_jung": 1, "fehler": 1, "posts": []}
        self.assertFalse(lernbot_publikum.meldung_nach_bewerten(self.con, leer, tag(8)))
        self.assertEqual(self.lern_meldungen(), [])

    def test_ein_post_mit_basis_zu_klein_erklaert_die_null(self):
        post_id = self.gemessener_post()
        ergebnis = publikum.bewerte_alle(self.con, self.konfig, tag(8))
        self.assertTrue(lernbot_publikum.meldung_nach_bewerten(self.con, ergebnis, tag(8)))
        text = self.lern_meldungen()[0]["text"]
        self.assertTrue(text.startswith(f"📊 1 Post bewertet: #{post_id} 0 – /publikum"), text)
        self.assertIn("Score 0 = noch zu wenige bewertete Posts zum Vergleich – echte Scores ab 5 bewerteten Posts", text)

    def test_score_text(self):
        for score, text in ((0.8, "+0,8"), (-0.3, "−0,3"), (0.0, "0"), (0.04, "0"), (-0.04, "0"), (2.5, "+2,5"),
                            (-1.25, "−1,2")):  # genau ,5: Pythons round rundet zur geraden Ziffer
            with self.subTest(score):
                self.assertEqual(lernbot_publikum.score_text(score), text)


# --- 3. Ruhezeit im Lern-Bot -----------------------------------------------------------------------------------

class Ruhezeit(MitBewertung):
    def setUp(self):
        super().setUp()
        db.lern_meldung(self.con, "abend:2026-09-24", "📋 Stand: …")
        db.lern_meldung(self.con, "publikum:bewertet:2026-09-24", "📊 1 Post bewertet: #1 0 – /publikum")
        db.lern_meldung(self.con, "fehler:entwurf", "⚠️ Entwurf fehlgeschlagen")
        db.lern_meldung(self.con, "woche:2026-W39", "📈 Wochenbericht")

    def schluessel(self, zeit: datetime) -> list[str]:
        return [z["schluessel"] for z in lernbot_publikum.faellige_lern_meldungen(self.con, self.konfig, zeit)]

    def test_in_der_ruhezeit_warten_publikum_und_woche(self):
        self.assertEqual(self.schluessel(ortszeit(2026, 9, 24, 23, 30)), ["abend:2026-09-24", "fehler:entwurf"])
        self.assertEqual(self.schluessel(ortszeit(2026, 9, 25, 7, 59)), ["abend:2026-09-24", "fehler:entwurf"])

    def test_ausserhalb_alle_in_ihrer_reihenfolge(self):
        alle = ["abend:2026-09-24", "publikum:bewertet:2026-09-24", "fehler:entwurf", "woche:2026-W39"]
        self.assertEqual(self.schluessel(ortszeit(2026, 9, 25, 8, 0)), alle)
        self.assertEqual(self.schluessel(ortszeit(2026, 9, 24, 22, 59)), alle)

    def test_ruhezeit_aus(self):
        self.konfig.daten["telegram"]["leise_von"] = ""
        self.assertEqual(len(self.schluessel(ortszeit(2026, 9, 24, 23, 30))), 4)

    def test_gesendete_kommen_nicht_nochmal(self):
        self.con.execute("UPDATE lern_meldungen SET gesendet = 'x' WHERE schluessel LIKE 'abend:%'")
        self.assertEqual(self.schluessel(ortszeit(2026, 9, 25, 9, 0))[0], "publikum:bewertet:2026-09-24")

    def test_ohne_zeit_gilt_jetzt(self):
        with mock.patch.object(lernbot_publikum, "jetzt", return_value=ortszeit(2026, 9, 24, 23, 30)):
            self.assertEqual(len(lernbot_publikum.faellige_lern_meldungen(self.con, self.konfig)), 2)


@mock.patch.object(lernbot_publikum, "jetzt", return_value=ortszeit(2026, 9, 24, 23, 30))
class LernBotSendet(MitBewertung):
    """lernbot.sende_meldungen benutzt den Filter – der Rest des Lern-Bots bleibt, wie er ist."""

    def setUp(self):
        super().setUp()
        if lernbot is None:
            self.skipTest("python-telegram-bot fehlt")
        self.bot = FakeBot()
        self.app = SimpleNamespace(bot=self.bot, bot_data={"con": self.con, "konfig": self.konfig, "erlaubt": 42})

    def test_publikum_nachts_liegen_lassen_und_morgens_schicken(self, _jetzt):
        db.lern_meldung(self.con, "publikum:bewertet:2026-09-24", "📊 1 Post bewertet: #1 0 – /publikum")
        db.lern_meldung(self.con, "abend:2026-09-24", "📋 Stand: …")
        self.assertEqual(asyncio.run(lernbot.sende_meldungen(self.app)), 1)
        self.assertEqual(self.bot.texte, [(42, "📋 Stand: …")])  # andere Lern-Meldungen weiter sofort
        with mock.patch.object(lernbot_publikum, "jetzt", return_value=ortszeit(2026, 9, 25, 8, 5)):
            self.assertEqual(asyncio.run(lernbot.sende_meldungen(self.app)), 1)
            self.assertEqual(asyncio.run(lernbot.sende_meldungen(self.app)), 0)  # nicht doppelt
        self.assertEqual(self.bot.texte[1], (42, "📊 1 Post bewertet: #1 0 – /publikum"))


# --- 4. /publikum und /hilfe ----------------------------------------------------------------------------------

class PublikumText(MitBewertung):
    JETZT = ortszeit(2026, 9, 30, 12, 0)  # ein Mittwoch; die Woche beginnt Montag, 28.09., 00:00 Ortszeit

    def clip_post(self, clip_id: int, gepostet: datetime) -> int:
        daten = {"dauer_s": 22.0, "rezept": publikum.rezept_fuer_clip(22.0), "merkmale": {}}
        return publikum.post_anlegen(self.con, art="clip", ziel_id=clip_id, plattform="tiktok", daten=daten,
                                     zeit=gepostet)[0]

    def entwurf_post(self, entwurf_id: int, gepostet: datetime) -> int:
        daten = {"dauer_s": 31.0, "rezept": publikum.rezept_fuer_clip(31.0), "merkmale": {}}
        return publikum.post_anlegen(self.con, art="entwurf", ziel_id=entwurf_id, plattform="tiktok", daten=daten,
                                     zeit=gepostet)[0]

    def setze_score(self, post_id: int, score: float, **teile) -> None:
        self.con.execute("UPDATE posts SET score = ?, score_teile = ?, bewertet_utc = ? WHERE id = ?",
                         (score, json.dumps(teile), iso(self.JETZT), post_id))

    def claude(self, zeit: datetime, art: str = "claude") -> None:
        self.con.execute("INSERT INTO ereignisse (zeit, art, text) VALUES (?, ?, 'screenshot: ok')", (iso(zeit), art))

    def text(self, grenze: int = 10) -> list[str]:
        return lernbot_publikum.publikum_text(self.con, self.konfig, grenze, self.JETZT).splitlines()

    def test_ohne_posts_erklaert_wie_ein_post_entsteht(self):
        zeilen = self.text()
        self.assertIn("Noch keine Posts", zeilen[0])
        self.assertIn("📦", zeilen[0])
        self.assertIn("/link", zeilen[0])
        self.assertEqual(zeilen[-1], "🤖 Claude diese Woche: 0 Aufrufe")

    def test_zeilen_neueste_zuerst_mit_zahlen_und_score(self):
        alt = self.clip_post(88, self.JETZT - timedelta(days=9))
        self.messung(alt, self.JETZT - timedelta(days=2), views=5000, likes=300, wiedergabe_s=12.0)
        self.setze_score(alt, 0.8, r=0.5, e=0.06, v=8.5, z_r=1.1, z_e=-0.4, z_v=0.9, vermerke=[])
        ohne_zahlen = self.clip_post(89, self.JETZT - timedelta(days=8))
        neu = self.entwurf_post(41, self.JETZT - timedelta(days=4, hours=2))
        self.messung(neu, self.JETZT - timedelta(hours=1), views=1240, likes=61, wiedergabe_s=6.8)  # Alter 4 d 1 h
        zeilen = self.text()
        self.assertEqual(zeilen[0], "📊 Publikum · 3 Posts, 1 mit Score (neueste zuerst)")
        self.assertEqual(zeilen[1], f"#{neu} TikTok · Entwurf 41 · 4 Tage · 👁 1 240 ❤️ 61 ⏱ 6,8 s (Tag 4) · "
                                    "Score noch offen (ab 7 Tagen)")
        self.assertEqual(zeilen[2], f"#{ohne_zahlen} TikTok · Clip 89 · 8 Tage · noch keine Zahlen – Screenshot mit "
                                    f"#{ohne_zahlen} schicken · Score offen (braucht eine Messung ab Tag 3 mit Views)")
        self.assertEqual(zeilen[3], f"#{alt} TikTok · Clip 88 · 9 Tage · 👁 5 000 ❤️ 300 ⏱ 12 s (Tag 7) · "
                                    "Score +0,8 (Wiedergabe über, Likes je View unter, Views über deinem Median)")
        self.assertEqual(zeilen[-1], "🤖 Claude diese Woche: 0 Aufrufe")

    def test_score_null_basis_zu_klein(self):
        post_id = self.clip_post(88, self.JETZT - timedelta(days=8))
        self.messung(post_id, self.JETZT - timedelta(days=1), views=900, wiedergabe_s=5.0)
        self.setze_score(post_id, 0.0, r=0.2, e=0.0, v=6.8, z_r=0.0, z_e=0.0, z_v=0.0,
                         vermerke=["Engagement unvollständig", "Basis zu klein"])
        self.assertTrue(self.text()[1].endswith("· Score 0 (Basis zu klein · Engagement unvollständig)"),
                        self.text()[1])

    def test_score_ohne_wiedergabe(self):
        post_id = self.clip_post(88, self.JETZT - timedelta(days=8))
        self.messung(post_id, self.JETZT - timedelta(days=1), views=900, likes=20)
        self.setze_score(post_id, -0.3, r=None, e=0.02, v=6.8, z_r=None, z_e=-0.5, z_v=0.0,
                         vermerke=["ohne Wiedergabe"])
        self.assertTrue(self.text()[1].endswith(
            "· Score −0,3 (Likes je View unter, Views gleich deinem Median · ohne Wiedergabe)"), self.text()[1])

    def test_faellig_mit_messung_kommt_beim_naechsten_lauf(self):
        post_id = self.clip_post(88, self.JETZT - timedelta(days=8))
        self.messung(post_id, self.JETZT - timedelta(days=1), views=900)
        self.assertTrue(self.text()[1].endswith("· Score kommt beim nächsten Lauf"), self.text()[1])

    def test_kaputtes_score_teile_zeigt_den_score_trotzdem(self):
        post_id = self.clip_post(88, self.JETZT - timedelta(days=8))
        self.setze_score(post_id, 1.4)
        self.con.execute("UPDATE posts SET score_teile = '{kaputt' WHERE id = ?", (post_id,))
        self.assertTrue(self.text()[1].endswith("· Score +1,4"), self.text()[1])

    def test_heute_und_ein_tag(self):
        self.clip_post(1, self.JETZT - timedelta(hours=5))
        self.clip_post(2, self.JETZT - timedelta(days=1, hours=1))
        zeilen = self.text()
        self.assertIn(" · Clip 1 · heute · ", zeilen[1])
        self.assertIn(" · Clip 2 · 1 Tag · ", zeilen[2])

    def test_grenze(self):
        for i in range(5):
            self.clip_post(i + 1, self.JETZT - timedelta(days=5 - i))
        zeilen = self.text(grenze=2)
        self.assertEqual(zeilen[0], "📊 Publikum · 5 Posts, 0 mit Score (die letzten 2, neueste zuerst)")
        self.assertEqual(len(zeilen), 1 + 2 + 1)
        self.assertIn("Clip 5", zeilen[1])

    def test_claude_aufrufe_seit_montag_ortszeit(self):
        montag = ortszeit(2026, 9, 28, 0, 0)  # 27.09., 22:00 UTC
        self.claude(montag - timedelta(minutes=1))       # Sonntag 23:59 – letzte Woche
        self.claude(montag)                              # Montag 00:00 – zählt
        self.claude(self.JETZT - timedelta(hours=1))     # zählt
        self.claude(self.JETZT - timedelta(hours=2), art="speicher")  # anderes Ereignis
        self.assertEqual(lernbot_publikum.claude_aufrufe_woche(self.con, self.konfig, self.JETZT), 2)
        self.assertEqual(self.text()[-1], "🤖 Claude diese Woche: 2 Aufrufe")
        self.con.execute("DELETE FROM ereignisse WHERE zeit >= ?", (iso(self.JETZT - timedelta(hours=1)),))
        self.assertEqual(self.text()[-1], "🤖 Claude diese Woche: 1 Aufruf")

    def test_woche_beginnt_auch_am_montag_selbst(self):
        montag_frueh = ortszeit(2026, 9, 28, 0, 30)
        self.claude(ortszeit(2026, 9, 27, 12, 0))   # Sonntag
        self.claude(ortszeit(2026, 9, 28, 0, 10))   # Montag
        self.assertEqual(lernbot_publikum.claude_aufrufe_woche(self.con, self.konfig, montag_frueh), 1)

    def test_ohne_zeit_gilt_jetzt(self):
        self.clip_post(1, self.JETZT - timedelta(days=2))
        with mock.patch.object(lernbot_publikum, "jetzt", return_value=self.JETZT):
            self.assertIn(" · 2 Tage · ", lernbot_publikum.publikum_text(self.con, self.konfig))


class Nachricht:
    """Die Nachricht, auf die ein Befehl antwortet (reply_text sammelt die Antworten)."""

    def __init__(self):
        self.antworten: list[tuple[str, dict]] = []

    async def reply_text(self, text, **kw):
        self.antworten.append((text, kw))


class Befehle(MitBewertung):
    def setUp(self):
        super().setUp()
        if lernbot is None:
            self.skipTest("python-telegram-bot fehlt")

    def befehl(self, funktion, *args: str) -> list[tuple[str, dict]]:
        nachricht = Nachricht()
        context = SimpleNamespace(bot_data={"con": self.con, "konfig": self.konfig, "erlaubt": 42}, args=list(args))
        asyncio.run(funktion(SimpleNamespace(effective_message=nachricht), context))
        return nachricht.antworten

    def test_publikum_standard_zehn(self):
        for i in range(12):
            self.post(gepostet=T0 + timedelta(hours=i))
        with mock.patch.object(lernbot_publikum, "jetzt", return_value=tag(1)):
            (antwort, _), = self.befehl(lernbot_publikum.cmd_publikum)
        self.assertIn("(die letzten 10, neueste zuerst)", antwort)
        self.assertEqual(len(re.findall(r"^#\d+ ", antwort, re.M)), 10)

    def test_publikum_mit_anzahl_und_hoechstens_dreissig(self):
        for i in range(35):
            self.post(gepostet=T0 + timedelta(hours=i))
        with mock.patch.object(lernbot_publikum, "jetzt", return_value=tag(3)):
            zwanzig = "\n".join(t for t, _ in self.befehl(lernbot_publikum.cmd_publikum, "20"))
            alle = "\n".join(t for t, _ in self.befehl(lernbot_publikum.cmd_publikum, "99"))  # 30 Zeilen: zwei Stücke
        self.assertEqual(len(re.findall(r"^#\d+ ", zwanzig, re.M)), 20)
        self.assertEqual(len(re.findall(r"^#\d+ ", alle, re.M)), 30)

    def test_publikum_falscher_aufruf(self):
        for args in (("abc",), ("0",), ("-3",), ("5", "6")):
            with self.subTest(args):
                self.assertEqual(self.befehl(lernbot_publikum.cmd_publikum, *args),
                                 [("Aufruf: /publikum oder /publikum 20", {})])

    def test_publikum_langer_text_in_stuecken(self):
        with mock.patch.object(lernbot_publikum, "publikum_text", return_value=("x" * 3000 + "\n") * 3):
            antworten = self.befehl(lernbot_publikum.cmd_publikum)
        self.assertEqual(len(antworten), 3)
        self.assertTrue(all(len(t) <= lernbot.TEXT_MAX for t, _ in antworten))

    def test_publikum_wirft_nicht(self):
        with mock.patch.object(lernbot_publikum, "publikum_text", side_effect=RuntimeError("DB kaputt")), \
                self.assertLogs("lern-bot", "ERROR"):
            antworten = self.befehl(lernbot_publikum.cmd_publikum)
        self.assertEqual(len(antworten), 1)
        self.assertIn("⚠️", antworten[0][0])
        self.assertIn("RuntimeError", antworten[0][0])

    def test_hilfe_mit_publikum_zusatz(self):
        ((text, kw),) = self.befehl(lernbot.cmd_hilfe)
        self.assertEqual(text, lernbot.HILFE + lernbot_publikum.HILFE_ZUSATZ)
        self.assertEqual(kw, {"parse_mode": "HTML"})
        self.assertLessEqual(len(text), 4096)  # Telegram: höchstens 4096 Zeichen je Nachricht
        self.assertIn("/publikum", text)
        # Telegram lehnt HTML mit unbekannten oder offenen Tags ab – dann käme gar keine Hilfe
        offen: list[str] = []
        parser = _TagPruefer(offen)
        parser.feed(text)
        self.assertEqual(offen, [])
        self.assertLessEqual(parser.tags, {"b", "code"})

    def test_publikum_im_lern_bot_angemeldet(self):
        app = lernbot.baue_app(self.konfig, "123456:TEST", 42)
        try:
            befehle = {c for gruppe in app.handlers.values() for h in gruppe for c in getattr(h, "commands", ())}
            self.assertIn("publikum", befehle)
        finally:
            app.bot_data["con"].close()


class _TagPruefer(HTMLParser):
    """Merkt sich alle Tags und prüft, dass jedes geöffnete wieder geschlossen wird."""

    def __init__(self, offen: list[str]):
        super().__init__()
        self.offen, self.tags = offen, set()

    def handle_starttag(self, tag_, attrs):
        self.tags.add(tag_)
        self.offen.append(tag_)

    def handle_endtag(self, tag_):
        if self.offen and self.offen[-1] == tag_:
            self.offen.pop()
        else:
            self.offen.append(f"/{tag_}")
