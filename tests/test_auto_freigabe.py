"""Auto-Freigabe im Clip-Bot (30.09., Florian: „nicht jeden Clip per Hand separat freigeben“).

T1 Frist · T2 kein Zirkelschluss (Lernen, Erwartung, Tor) · T3 weich aussortiert bleibt Material · T4 Tor (stufe)
· T5 Sofort-Entscheidung in sende_outbox · T6 Fehler → normaler Weg · T7 Korrektur, Bestätigung, Rückgängig
· T8 Migration ohne Datenverlust. Die Tests schalten die Automatik ausdrücklich an (tests/hilfen setzt „aus“).
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from clip_pipeline import auto_freigabe, db, einstellungen, erwartung, highlight, lernen, regie, stimmung
from clip_pipeline.bot import aktionen
from clip_pipeline.zeit import iso

from tests.test_erwartung import UHR, MitErwartung

try:
    from clip_pipeline.bot import app as bot_app
except ImportError:  # python-telegram-bot nicht installiert
    bot_app = None


class MitAuto(MitErwartung):
    def setUp(self):
        super().setUp()
        self.konfig.daten["auto_freigabe"] = dict(auto_freigabe.STANDARD, modus="an")

    def p(self, cid: int, wahrschein: float) -> None:
        """Festgeschriebene Erwartung von Hand (wie beim Senden)."""
        self.con.execute("INSERT INTO erwartungen (art, ziel_id, wahrschein, grundlage, erstellt)"
                         " VALUES ('clip', ?, ?, '{}', ?)", (cid, wahrschein, iso(UHR)))

    def spalten(self, cid: int, *namen: str) -> tuple:
        return tuple(self.con.execute(f"SELECT {', '.join(namen)} FROM clips WHERE id = ?", (cid,)).fetchone())

    def auto(self, cid: int, *, art: str = "sofort", vorschlag: str | None = None, quelle: str = "auto") -> None:
        status = self.spalten(cid, "status")[0]
        self.con.execute("UPDATE clips SET freigabe_quelle = ?, auto_art = ?, auto_vorschlag = ? WHERE id = ?",
                         (quelle, art, vorschlag or status, cid))


class Frist(MitAuto):
    """T1: offene Clips entscheidet der Bot nach frist_h selbst."""

    def test_altbestand_vollautonom_sofort(self):
        offen = self.clip("gesendet", 3.0)                                  # vor dem Update gesendet, nie beantwortet
        self.p(offen, 0.7)
        ergebnis = auto_freigabe.frist(self.con, self.konfig, zeit=UHR)
        self.assertIn({"id": offen, "status": "freigegeben"}, [{"id": e["id"], "status": e["status"]} for e in ergebnis])
        self.assertTrue(self.spalten(offen, "auto_grund")[0].startswith("vollautonom"))
        self.assertIn("offener Clip selbst entschieden", auto_freigabe.frist_text(ergebnis, 24))   # ohne „nach 24 h“

    def test_frist_entscheidet_nach_p_und_laesst_sich_abschalten(self):
        self.konfig.daten["auto_freigabe"]["vollautonom"] = False          # klassischer Weg: erst nach der Frist
        alt = iso(UHR - timedelta(hours=25))
        gut, schlecht, frisch = self.clip("gesendet", 3.0), self.clip("gesendet", 1.0), self.clip("gesendet", 1.0)
        self.con.execute("UPDATE clips SET vorgelegt = ? WHERE id IN (?, ?)", (alt, gut, schlecht))
        self.p(gut, 0.7)
        self.p(schlecht, 0.2)
        ergebnis = auto_freigabe.frist(self.con, self.konfig, zeit=UHR)
        self.assertEqual({e["id"]: e["status"] for e in ergebnis}, {gut: "freigegeben", schlecht: "verworfen"})
        self.assertEqual(self.spalten(gut, "status", "freigabe_quelle"), ("freigegeben", "auto"))
        self.assertEqual(self.spalten(schlecht, "status", "freigabe_quelle"), ("verworfen", "auto"))
        self.assertEqual(self.spalten(frisch, "status"), ("gesendet",))       # vorgelegt war leer → erst jetzt
        text = self.con.execute("SELECT text FROM ereignisse WHERE clip_id = ? ORDER BY id DESC", (gut,)).fetchone()[0]
        self.assertIn("(auto: Frist 24 h", text)
        self.assertIn("✅ #", auto_freigabe.frist_text(ergebnis, 24))

        # frist_h = 0: nie – auch ein längst fälliger Clip bleibt offen
        self.konfig.daten["auto_freigabe"]["frist_h"] = 0
        self.con.execute("UPDATE clips SET vorgelegt = ? WHERE id = ?", (alt, frisch))
        self.assertEqual(auto_freigabe.frist(self.con, self.konfig, zeit=UHR), [])
        self.assertEqual(self.spalten(frisch, "status"), ("gesendet",))

    @unittest.skipIf(bot_app is None, "python-telegram-bot fehlt")
    def test_frist_meldet_auch_wenn_telegram_bremst(self):
        # Altbestand-Welle: Flood-Limit beim Bearbeiten darf weder die ⏰-Meldung noch den Lauf kosten
        from telegram.error import RetryAfter

        alt = iso(UHR - timedelta(hours=25))
        ids = [self.clip("gesendet", 3.0) for _ in range(3)]
        for i, cid in enumerate(ids):
            self.con.execute("UPDATE clips SET vorgelegt = ?, tg_nachricht_id = ? WHERE id = ?", (alt, 500 + i, cid))
            self.p(cid, 0.7)
        bearbeitet = []

        async def edit_message_caption(**kwargs):
            if bearbeitet:
                raise RetryAfter(5)
            bearbeitet.append(kwargs["message_id"])

        fake = SimpleNamespace(bot_data={"con": self.con, "konfig": self.konfig, "erlaubt": 42},
                               bot=SimpleNamespace(edit_message_caption=edit_message_caption))
        self.assertEqual(asyncio.run(bot_app.automat_lauf(fake)), 3)
        self.assertEqual(bearbeitet, [500])
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM meldungen WHERE schluessel LIKE 'frist:%'")
                         .fetchone()[0], 1)


class KeinZirkelschluss(MitAuto):
    """T2: automatische Entscheidungen füttern weder Lernen noch Erwartung noch das Tor."""

    def messung(self):
        paare, n, _ = lernen.sammle_paare(self.con, zonen_name="Europe/Berlin", wechsel_stunde=6, max_pro_abend=500)
        return (len(paare), n, len(erwartung._urteile(self.con, "clip")), erwartung.trefferquote(self.con, "clip"),
                auto_freigabe.band(self.con, ab=0.8, fenster=100))

    def test_nur_deine_urteile_zaehlen(self):
        for status, wert in (("freigegeben", 0.9), ("freigegeben", 0.95), ("verworfen", 0.85)):
            self.p(self.clip(status, 3.0), wert)
        vorher = self.messung()
        for i in range(20):  # 20 automatisch entschiedene Clips mit hoher Erwartung
            cid = self.clip("freigegeben" if i % 2 else "verworfen", 6.0)
            self.p(cid, 0.95)
            self.auto(cid)
        self.assertEqual(self.messung(), vorher)

        # Korrektur (Automatik gab frei, du hast verworfen): zählt überall – im Tor als Fehltreffer
        korrigiert = self.clip("verworfen", 6.0)
        self.p(korrigiert, 0.95)
        self.auto(korrigiert, vorschlag="freigegeben", quelle="du")
        nachher = self.messung()
        self.assertEqual(nachher[1], vorher[1] + 1)                       # Lernen
        self.assertEqual(nachher[2], vorher[2] + 1)                       # Erwartung (_urteile)
        self.assertEqual(nachher[3][1], vorher[3][1] + 1)                 # Trefferquote
        self.assertEqual(nachher[4], (vorher[4][0], vorher[4][1] + 1))    # Tor: ein Urteil mehr, kein Treffer mehr

        # Bestätigung („👍 Stimmt“ auf einem Sofort-Clip): Lernen und Erwartung ja, Tor nein
        bestaetigt = self.clip("freigegeben", 6.0)
        self.p(bestaetigt, 0.95)
        self.auto(bestaetigt, quelle="du")
        danach = self.messung()
        self.assertEqual((danach[1], danach[2]), (nachher[1] + 1, nachher[2] + 1))
        self.assertEqual(danach[4], nachher[4])


class Weich(MitAuto):
    """T3: automatisch aussortierte Clips bleiben Material, von dir verworfene nicht; Auto-Freigaben ohne Bonus."""

    def material(self, status: str, quelle: str | None) -> int:
        cid = self.clip_anlegen(status=status)
        datei = self.tmp / f"moment-{cid}.mp4"
        datei.write_bytes(b"x")
        self.con.execute("UPDATE clips SET clip_pfad = ?, freigabe_quelle = ? WHERE id = ?",
                         (f"sessions/m1/clips/{cid}.mp4", quelle, cid))
        mk = {"kills": 1, "max_gruppe": 1, "kill_sekunden": [8.0], "spitzen": 1, "jubel_laut": 0}
        self.con.execute(
            """INSERT INTO momente (schluessel, clip_id, match_id, datei, start_s, ende_s, kills, stimmung, sicherheit,
                                    quelle, merkmale, erstellt, geaendert)
               VALUES (?, ?, 'm1', ?, 0, 20, 1, 'episch', 0.8, 'regel', ?, 'x', 'x')""",
            (f"clip:{cid}", cid, str(datei), json.dumps(mk)))
        return cid

    def test_weich_aussortiert_bleibt_material(self):
        weich, hart = self.material("verworfen", "auto"), self.material("verworfen", "du")
        auto_frei, du_frei = self.material("freigegeben", "auto"), self.material("freigegeben", "du")
        ks, _ = regie.kandidaten_mit_bericht(self.con, dict(regie.PARAMETER), [], gewichte={"kill_punkte": 1.0},
                                             kill_tabelle=[0, 1, 3, 6, 10])
        punkte = {k.clip_id: k.punkte for k in ks}
        self.assertIn(weich, punkte)
        self.assertNotIn(hart, punkte)
        self.assertGreater(punkte[du_frei], punkte[auto_frei])           # +historisch nur für deine Freigabe

        # Highlight-Auswahl: dieselbe Menge – abgefangen, bevor irgendetwas gerendert wird
        with mock.patch.object(stimmung, "analysiere", side_effect=RuntimeError("halt")) as analyse, \
                self.assertRaises(RuntimeError):
            highlight.erstelle(self.con, self.konfig, "hl-test", 3650)
        self.assertEqual(set(analyse.call_args.kwargs["nur_clips"]), {weich, auto_frei, du_frei})

        self.con.execute("UPDATE clips SET mic_stand = NULL")
        self.assertEqual(db.ohne_mic_analyse(self.con), 3)                # alle außer dem hart verworfenen


class Tor(MitAuto):
    """T4: die Stufe wächst mit gemessener Genauigkeit."""

    def test_stufe(self):
        for _ in range(16):
            self.p(self.clip("freigegeben", 6.0), 0.92)
        fehl = [self.clip("verworfen", 1.0) for _ in range(3)]
        for cid in fehl:
            self.p(cid, 0.87)  # im Band ab 85 %, aber verworfen → dort 16/19 = 84 % < 90 %
        stand = auto_freigabe.stufe(self.con, self.konfig)
        self.assertEqual(stand["frei_ab"], 0.9)
        self.assertEqual([z["urteil"] for z in stand["zeilen"]["frei"]], ["wenig", "ok", "nein"])

        # Dieselben 3 Fehltreffer mitten im 90-%-Band und Ziel 95 % → keine Stufe (die Regel gilt trotzdem)
        self.con.execute(f"UPDATE erwartungen SET wahrschein = 0.92 WHERE ziel_id IN ({','.join('?' * 3)})", fehl)
        self.konfig.daten["auto_freigabe"]["ziel_quote"] = 0.95
        self.assertIsNone(auto_freigabe.stufe(self.con, self.konfig)["frei_ab"])


@unittest.skipIf(bot_app is None, "python-telegram-bot fehlt")
class SofortImClipBot(MitAuto):
    """T5/T6: sende_outbox entscheidet klare Fälle sofort – im Zweifel (Fehler) kommt der Clip normal."""

    def setUp(self):
        super().setUp()
        self.konfig.daten.setdefault("telegram", {}).update(leise_von="", leise_bis="")
        vorschau = self.konfig.ordner("sessions") / "m1" / "vorschau" / "v.mp4"
        vorschau.parent.mkdir(parents=True, exist_ok=True)
        vorschau.write_bytes(b"video")
        self.gesendet: list[dict] = []

        async def send_video(**kwargs):
            self.gesendet.append(kwargs)
            return SimpleNamespace(message_id=100 + len(self.gesendet), video=SimpleNamespace(file_id="F"))

        self.fake = SimpleNamespace(bot_data={"con": self.con, "konfig": self.konfig, "erlaubt": 42},
                                    bot=SimpleNamespace(send_video=send_video))
        for _ in range(16):  # Tor: 16 deiner Urteile mit hoher Erwartung, alle freigegeben → aktiv ab 80 %
            self.p(self.clip("freigegeben", 6.0), 0.97)

    def neu(self, wahrschein: float, max_gruppe: int = 1) -> int:
        cid = self.clip_anlegen(status="vorbewertet", max_gruppe=max_gruppe, file_id=None)
        self.con.execute("UPDATE clips SET vorschau_pfad = 'sessions/m1/vorschau/v.mp4' WHERE id = ?", (cid,))
        self.p(cid, wahrschein)
        return cid

    @staticmethod
    def knoepfe(kwargs) -> list[tuple[str, str]]:
        return [(b.text, b.callback_data) for reihe in kwargs["reply_markup"].inline_keyboard for b in reihe]

    def test_sofort_stichprobe_regel_und_aus(self):
        self.konfig.daten["auto_freigabe"]["stichprobe_jede"] = 5          # Stichproben nur noch auf Wunsch
        sofort, triple = self.neu(0.9), self.neu(0.01, max_gruppe=3)
        self.clip("gesendet", 1.0)                                         # Platzhalter: nächster Clip ist #20
        stich = self.neu(0.9)
        self.assertEqual((sofort, triple, stich), (17, 18, 20))
        self.assertEqual(asyncio.run(bot_app.sende_outbox(self.fake)), 3)
        a, b, c = self.gesendet
        self.assertTrue(a["disable_notification"])
        self.assertIn("🤖 automatisch – Erwartung 90 % ≥ Stufe 80 %", a["caption"])
        self.assertEqual(self.knoepfe(a), [("👍 Stimmt", "f:17"), ("🗑️ Doch aussortieren", "v:17")])
        self.assertEqual(self.spalten(sofort, "status", "freigabe_quelle", "auto_art", "tg_file_id"),
                         ("freigegeben", "auto", "sofort", "F"))
        self.assertEqual(self.spalten(triple, "status", "auto_grund"), ("freigegeben", "Regel: 3er-Serie"))
        self.assertIn("🎲 Stichprobe – ich hätte freigegeben", c["caption"])
        self.assertFalse(c["disable_notification"])
        self.assertEqual(self.knoepfe(c), [("✅ Freigeben", "f:20"), ("🗑️ Verwerfen", "v:20")])
        self.assertEqual(self.spalten(stich, "status", "auto_art"), ("gesendet", "stichprobe"))

        einstellungen.setze(self.con, "auto_freigabe.modus", "aus")        # ⚙️ wirkt ab der nächsten Runde
        normal = self.neu(0.99)
        self.assertEqual(asyncio.run(bot_app.sende_outbox(self.fake)), 1)
        self.assertEqual(self.spalten(normal, "status", "freigabe_quelle", "auto_art"), ("gesendet", None, None))
        self.assertNotIn("🤖", self.gesendet[-1]["caption"])

    def test_vollautonom_ohne_urteile_von_dir(self):
        # Florian 30.09.: „muss es Referenzen geben, wenn ich sage, es soll autonom passieren?“ – nein
        self.con.execute("DELETE FROM erwartungen WHERE ziel_id < 17")      # nur Testdaten: kein Tor, keine Urteile
        self.con.execute("UPDATE clips SET status = 'gesendet' WHERE id < 17")
        gut, schwach, ohne = self.neu(0.7), self.neu(0.2), self.clip_anlegen(status="vorbewertet", max_gruppe=2,
                                                                                 file_id=None)
        self.con.execute("UPDATE clips SET vorschau_pfad = 'sessions/m1/vorschau/v.mp4' WHERE id = ?", (ohne,))
        self.assertEqual(asyncio.run(bot_app.sende_outbox(self.fake)), 3)
        self.assertEqual(self.spalten(gut, "status", "freigabe_quelle"), ("freigegeben", "auto"))
        self.assertEqual(self.spalten(schwach, "status", "freigabe_quelle"), ("verworfen", "auto"))   # weich
        self.assertEqual(self.spalten(ohne, "status", "auto_grund"), ("freigegeben", "ohne Erwartung, 2er-Serie"))
        self.assertTrue(all(g["disable_notification"] for g in self.gesendet))                       # nichts piept
        self.assertEqual(self.knoepfe(self.gesendet[1]), [("✅ Doch freigeben", f"f:{schwach}"),
                                                          ("🚫 Ganz raus", f"v:{schwach}")])
        self.konfig.daten["auto_freigabe"]["vollautonom"] = False             # alter Weg: Unsichere kommen zu dir
        frage = self.neu(0.7)
        asyncio.run(bot_app.sende_outbox(self.fake))
        self.assertEqual(self.spalten(frage, "status", "freigabe_quelle"), ("gesendet", None))

    def test_fehler_in_der_automatik_schickt_den_clip_normal(self):
        cid = self.neu(0.9)
        with mock.patch.object(auto_freigabe, "vorschlag", side_effect=RuntimeError("kaputt")), \
                self.assertLogs("clip-bot", "ERROR"):
            self.assertEqual(asyncio.run(bot_app.sende_outbox(self.fake)), 1)
        self.assertEqual(self.spalten(cid, "status", "freigabe_quelle"), ("gesendet", None))
        self.assertEqual(self.knoepfe(self.gesendet[0]), [("✅ Freigeben", f"f:{cid}"), ("🗑️ Verwerfen", f"v:{cid}")])


class Rueckweg(MitAuto):
    """T7: Korrektur, Bestätigung und Rückgängig an einem automatisch entschiedenen Clip."""

    def auto_frei(self) -> int:
        cid = self.clip("vorbewertet", 6.0, gesendet=False)
        aktionen.als_gesendet(self.con, cid, 5, "F", auto_freigabe.Vorschlag("sofort", "freigegeben", "Regel: 3er-Serie"))
        self.assertEqual(self.spalten(cid, "status", "freigabe_quelle"), ("freigegeben", "auto"))
        return cid

    def test_korrigieren_bestaetigen_rueckgaengig(self):
        korrigiert = self.auto_frei()
        self.assertEqual(aktionen.entscheide(self.con, korrigiert, "verworfen").hinweis, "🗑️ Verworfen")
        self.assertEqual(self.spalten(korrigiert, "status", "freigabe_quelle", "auto_art", "auto_vorschlag"),
                         ("verworfen", "du", "sofort", "freigegeben"))

        bestaetigt = self.auto_frei()
        self.assertEqual(aktionen.entscheide(self.con, bestaetigt, "freigegeben").hinweis, "👍 Bestätigt")
        self.assertEqual(self.spalten(bestaetigt, "status", "freigabe_quelle"), ("freigegeben", "du"))

        self.con.execute("UPDATE clips SET vorgelegt = 'alt' WHERE id = ?", (bestaetigt,))
        self.assertEqual(aktionen.rueckgaengig(self.con, bestaetigt).neuer_status, "gesendet")
        status, quelle, vorgelegt = self.spalten(bestaetigt, "status", "freigabe_quelle", "vorgelegt")
        self.assertEqual((status, quelle), ("gesendet", None))
        self.assertNotEqual(vorgelegt, "alt")


class Migration(MitAuto):
    """T8: eine alte Datenbank ohne die Spalten bekommt sie – ohne Zeilen oder Status zu verlieren."""

    def test_alte_datenbank(self):
        for status in ("gesendet", "freigegeben", "verworfen"):
            self.clip_anlegen(status=status)
        for spalte in ("freigabe_quelle", "vorgelegt", "auto_vorschlag", "auto_art", "auto_grund"):
            self.con.execute(f"ALTER TABLE clips DROP COLUMN {spalte}")
        vorher = self.con.execute("SELECT id, status FROM clips ORDER BY id").fetchall()
        self.con.close()
        self.con = db.verbinde(self.konfig.datenbank)
        namen = {z["name"] for z in self.con.execute("PRAGMA table_info(clips)")}
        self.assertTrue({"freigabe_quelle", "vorgelegt", "auto_vorschlag", "auto_art", "auto_grund"} <= namen)
        self.assertEqual([tuple(z) for z in self.con.execute("SELECT id, status FROM clips ORDER BY id")],
                         [tuple(z) for z in vorher])
        self.assertIsInstance(self.con, sqlite3.Connection)
        self.assertTrue(Path(self.konfig.datenbank).is_file())


if __name__ == "__main__":
    unittest.main()
