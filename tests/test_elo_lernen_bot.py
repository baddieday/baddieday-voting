import unittest
from datetime import date, datetime, timedelta

from clip_pipeline import elo, lernen
from clip_pipeline.bot import aktionen, texte
from clip_pipeline.zeit import UTC

from tests.hilfen import MitSpeicher


class Elo(unittest.TestCase):
    def test_zahlen_wie_in_clips_voter(self):
        a, b = elo.battle(elo.Stand(), elo.Stand(), "a")
        self.assertEqual((a.elo, b.elo, a.rd, a.battles), (1524.0, 1476.0, 329.0, 1))
        self.assertEqual([elo.k_faktor(n) for n in (0, 9, 10, 24, 25)], [48, 48, 32, 32, 24])

    def test_ueberspringen_aendert_nur_unsicherheit(self):
        a, b = elo.battle(elo.Stand(1600, 100, 3), elo.Stand(1400, 76, 3), "s")
        self.assertEqual((a.elo, a.rd, b.rd, a.battles), (1600, 94.0, 75.0, 4))

    def test_rangliste_bestraft_unsicherheit(self):
        self.assertLess(elo.ranglisten_wert(1530, 330), elo.ranglisten_wert(1510, 120))


def m(kill=1.0, laut=0.0) -> dict:
    return {"kill_punkte": kill, "victory_royale": 0.0, "laenge": 0.0, "lautstaerke": laut, "kommentar": 0.0}


class Lernen(MitSpeicher):
    def _abend(self, tag: int, anzahl: int) -> None:
        """Du gibst immer den lauteren Clip frei – bei gleichen Kills."""
        start = datetime(2026, 9, tag, 19, 0, tzinfo=UTC)
        for i in range(anzahl):
            laut = i % 2 == 0
            self.clip_anlegen(status="freigegeben" if laut else "verworfen", start=start + timedelta(minutes=i),
                              merkmale=m(3.0, 0.9 if laut else 0.1), match_id=f"m{tag}")

    def test_unter_mindestmenge_bleiben_startgewichte(self):
        self._abend(1, 10)
        e = lernen.berechne(self.con, self.konfig)
        self.assertFalse(e.aktiv)
        self.assertEqual(e.werte, e.start)

    def test_lernt_lautstaerke_und_bleibt_an_der_leine(self):
        for tag in range(1, 6):
            self._abend(tag, 8)  # 40 Bewertungen
        e = lernen.berechne(self.con, self.konfig)
        self.assertTrue(e.aktiv, e.grund)
        self.assertGreater(e.werte["lautstaerke"], e.start["lautstaerke"])
        self.assertLessEqual(e.werte["lautstaerke"], 1.5)  # Leine: ±50 %
        self.assertGreaterEqual(e.trefferquote, e.trefferquote_start)
        version, _ = lernen.aktualisiere(self.con, self.konfig)
        self.assertEqual(version, 1)
        self.assertEqual(lernen.aktualisiere(self.con, self.konfig)[0], 1)  # unverändert -> keine neue Version


class BotAktionen(MitSpeicher):
    def test_parse(self):
        self.assertEqual(aktionen.parse("b:17:a"), ("b", 17, "a"))
        with self.assertRaises(ValueError):
            aktionen.parse("x:1")

    def test_doppelklick_und_rueckgaengig(self):
        cid = self.clip_anlegen()
        self.assertEqual(aktionen.entscheide(self.con, cid, "freigegeben").neuer_status, "freigegeben")
        zweiter = aktionen.entscheide(self.con, cid, "freigegeben")
        self.assertIsNone(zweiter.knoepfe)  # nichts geändert
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM ereignisse WHERE clip_id = ?", (cid,)).fetchone()[0], 1)
        self.assertEqual(aktionen.rueckgaengig(self.con, cid).neuer_status, "gesendet")
        self.assertEqual(aktionen.entscheide(self.con, cid, "verworfen").neuer_status, "verworfen")

    def test_outbox(self):
        cid = self.clip_anlegen(status="vorbewertet", file_id=None)
        self.assertEqual([z["id"] for z in aktionen.outbox(self.con)], [cid])
        aktionen.als_gesendet(self.con, cid, 99, "FILE")
        self.assertEqual(aktionen.outbox(self.con), [])
        self.assertEqual(tuple(self.con.execute("SELECT status, tg_file_id FROM clips").fetchone()), ("gesendet", "FILE"))

    def test_battle_einmal_entscheiden(self):
        self.clip_anlegen(status="freigegeben")
        self.clip_anlegen(status="freigegeben", elo=1600)
        self.clip_anlegen(status="verworfen")  # nimmt nicht teil
        self.clip_anlegen(status="freigegeben", file_id=None)  # ohne Telegram-Datei: kein Battle möglich
        bid, a, b = aktionen.neues_battle(self.con)
        self.assertEqual({a["id"], b["id"]}, {1, 2})
        antwort, battle = aktionen.entscheide_battle(self.con, bid, "b", self.konfig)
        self.assertEqual((antwort.hinweis, battle["ergebnis"]), ("B gewinnt", "b"))
        self.assertEqual(aktionen.entscheide_battle(self.con, bid, "a", self.konfig)[0].hinweis, "Schon entschieden")
        stand = {z["id"]: (z["battles"], z["siege"], z["niederlagen"]) for z in self.con.execute("SELECT * FROM clips")}
        self.assertEqual((stand[a["id"]], stand[b["id"]]), ((1, 0, 1), (1, 1, 0)))
        self.assertIn("B gewinnt", texte.battle_ergebnis(battle))

    def test_saison_und_rangliste(self):
        self.assertEqual(aktionen.saison(date(2026, 10, 6), self.konfig), (date(2026, 10, 5), date(2026, 10, 18)))
        self.clip_anlegen(status="freigegeben", elo=1600)
        zeilen, _ = aktionen.rangliste(self.con, self.konfig, heute=date(2026, 9, 22))
        self.assertEqual(len(zeilen), 1)


if __name__ == "__main__":
    unittest.main()
