"""Stufe 2 des Umbaus (07.10., Florian: „Lernen zurück“ – aus ✅/❌ und KI-Urteil, mutig, Wochenbericht):
Aufbau, Tempo und Zeitlupe lernt geschmack.py; deine Regeln gehen danach immer vor."""

import json
import unittest
from datetime import datetime, timedelta, timezone

from clip_pipeline import db, einstellungen, geschmack, regie_lernen
from clip_pipeline.zeit import iso, jetzt

from tests.hilfen import MitSpeicher


class Geschmack(MitSpeicher):
    def setUp(self):
        super().setUp()
        self.con = db.verbinde(self.konfig.datenbank)
        self.konfig.daten["regie"]["stil"] = "auto"
        self.n = 0

    def tearDown(self):
        self.con.close()
        super().tearDown()

    def entwurf(self, wahl, daumen=None, gruende=(), ki=None, experiment=None, erstellt=None):
        self.n += 1
        p = {"stil": wahl.get("aufbau"), "geschmack": {**wahl, "experiment": experiment}}
        eid = self.con.execute("""INSERT INTO entwuerfe (name, format, schnittliste, parameter, status, datei, erstellt)
                                  VALUES (?, 'short', '/x.json', ?, 'gesendet', '/x.mp4', ?)""",
                               (f"e{self.n}", json.dumps(p), iso(erstellt or jetzt()))).lastrowid
        if daumen is not None:
            self.con.execute("INSERT INTO entwurf_bewertungen VALUES (?, ?, ?, ?, ?)",
                             (eid, daumen, json.dumps(list(gruende)), iso(jetzt()), iso(jetzt())))
        if ki is not None:
            self.con.execute("INSERT INTO kritiken (entwurf_id, score, regel_score, ki_score, details, erstellt) "
                             "VALUES (?, 70, 70, ?, '{}', ?)", (eid, ki, iso(jetzt())))
        return eid

    def test_lernt_aus_daumen_und_ki_und_gruende_grenzen_ein(self):
        gut = {"aufbau": "steigerung", "tempo": "ruhig", "zeitlupe": "wenig"}
        schlecht = {"aufbau": "kino", "tempo": "schnell", "zeitlupe": "viel"}
        for _ in range(4):
            self.entwurf(gut, daumen=1, ki=80)
            self.entwurf(schlecht, daumen=-1, ki=30)
        ohne_ki = self.entwurf(gut, daumen=-1, gruende=["musik"])   # 🎵 hat mit Aufbau/Tempo/Zeitlupe nichts zu tun
        self.assertEqual(geschmack.offen_fuer_ki(self.con), ohne_ki)           # die KI urteilt danach im Hintergrund
        self.assertIsNone(geschmack.offen_fuer_ki(self.con, {ohne_ki}))        # schon versucht: nicht alle 30 s nochmal
        stat = geschmack.statistik(self.con)
        self.assertEqual((stat["aufbau"]["steigerung"]["ja"], stat["aufbau"]["steigerung"]["nein"]), (4, 0))
        self.assertAlmostEqual(stat["aufbau"]["kino"]["n"], 4 + 4 * geschmack.KI_GEWICHT)
        self.konfig.daten.setdefault("geschmack", {})["mut"] = 0.0
        wahl = geschmack.waehle(self.con, self.konfig)
        self.assertEqual({k: wahl[k] for k in geschmack.KNOEPFE}, gut)
        self.assertIsNone(wahl["experiment"])
        # mutig: eine Schraube bewusst auf die am wenigsten erprobte Einstellung
        self.konfig.daten["geschmack"]["mut"] = 1.0
        wahl = geschmack.waehle(self.con, self.konfig)
        self.assertIn(wahl["experiment"], geschmack.KNOEPFE)
        self.assertNotEqual(wahl[wahl["experiment"]], gut[wahl["experiment"]])
        # Wochenbericht nennt, was ankommt und was nicht – ohne Videos in der Woche: Ruhe
        text = geschmack.wochen_text(self.con, self.konfig)
        self.assertIn("9 Videos · 4 ✅ · 5 ❌", text)
        self.assertIn("👍 Kommt gut an: Aufbau „Steigerung“ (4 von 4 ✅)", text)
        self.assertIn("Aufbau „Kino“ (0 von 4 ✅)", text)
        self.assertIsNone(geschmack.wochen_text(self.con, self.konfig, datetime(2020, 1, 1, tzinfo=timezone.utc)))

    def test_einfacher_modus_nutzt_geschmack_und_regeln_gehen_vor(self):
        k = einstellungen.anwenden(self.con, self.konfig)                       # einfacher Modus: geschmack an
        p, _ = regie_lernen.aktuelle(self.con, k, "short")
        self.assertIn(p["geschmack"]["aufbau"], geschmack.KNOEPFE["aufbau"])
        self.assertEqual(p["stil"], p["geschmack"]["aufbau"])
        self.assertEqual(p["max_lupen"], geschmack.ZEITLUPEN[p["geschmack"]["zeitlupe"]])
        # übersteuert das Publikums-Modell danach eine Schraube, bekommt sie weder Lob noch Tadel
        vorher = {"geschmack": {"aufbau": "kino", "tempo": "ruhig", "zeitlupe": "viel", "experiment": "tempo"},
                  "seg_min_faktor": 1.25, "max_lupen": 8}
        wahl = geschmack.nur_wirksame(vorher, {**vorher, "seg_min_faktor": 1.4}, self.konfig)
        self.assertEqual((wahl.get("tempo"), wahl["experiment"], wahl["aufbau"]), (None, None, "kino"))
        einstellungen.setze(self.con, "regie.stil", "kino")                     # ⚙️ fester Stil geht vor
        p, _ = regie_lernen.aktuelle(self.con, einstellungen.anwenden(self.con, self.konfig), "short")
        self.assertEqual(p["stil"], "kino")

    def test_wochenbericht_sonntags_einmal(self):
        heute = datetime.now(timezone.utc)
        sonntag = heute + timedelta(days=7 - heute.isoweekday())               # der nächste Sonntag
        abend = sonntag.replace(hour=17, minute=30)                             # 18:30/19:30 in Berlin
        self.entwurf({"aufbau": "story", "tempo": "ruhig", "zeitlupe": "viel"}, daumen=1,
                     erstellt=abend - timedelta(hours=1))
        self.assertTrue(geschmack.wochenbericht(self.con, self.konfig, abend))
        self.assertFalse(geschmack.wochenbericht(self.con, self.konfig, abend))  # je Woche einmal
        self.assertFalse(geschmack.wochenbericht(self.con, self.konfig, sonntag.replace(hour=8)))   # vormittags nie


if __name__ == "__main__":
    unittest.main()
