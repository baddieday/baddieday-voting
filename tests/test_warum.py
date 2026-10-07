"""06.10. (Florian: „gefühlt bewerte ich genau den gleichen Mist wie früher“): Entwurf-Bewertungen lehren die
Moment-Formel (lernen.entwurf_paare), /warum zeigt Material, Wiederholung und Lernen aus den echten Daten."""

import os
from pathlib import Path

from clip_pipeline import lernen, regie, regie_lernen, warum

from tests.regie_hilfen import MOMENTE, MitRegieMaterial

KILLS = [0, 1, 3, 6, 10]


class EntwurfLehrtFormel(MitRegieMaterial):
    def setUp(self):
        super().setUp()
        self.momente_anlegen(MOMENTE[:10])
        self.musik_anlegen(150, "episch")
        self.gut = regie.erstelle(self.con, self.konfig, "short")
        self.schlecht = regie.erstelle(self.con, self.konfig, "short")
        regie_lernen.bewerte(self.con, self.gut["entwurf"], daumen=1)
        regie_lernen.bewerte(self.con, self.schlecht["entwurf"], daumen=-1)      # 👎 ohne Grund: die Szenen

    def test_gemocht_gegen_nicht_gemocht_und_warum(self):
        paare, n = lernen.entwurf_paare(self.con, KILLS, 300)
        self.assertEqual(n, 2)
        self.assertTrue(paare)
        self.assertTrue(all(p.art == "entwurf" for p in paare))
        e = lernen.berechne(self.con, self.konfig)
        self.assertEqual((e.paare_je_quelle["entwurf"], e.entwuerfe), (len(paare), 2))
        self.assertIn("2 Entwürfe", lernen.datenbasis_text(e))
        text = warum.text(self.con, self.konfig)
        self.assertIn("Momente aus", text)
        self.assertIn("2 deiner Entwurf-Bewertungen", text)
        self.assertIn("📉 Wenig Material: 10 Momente zur Wahl", text)          # 10 < 3 Shorts × 10 Momente
        regie_lernen.bewerte(self.con, self.schlecht["entwurf"], grund="langweilig")   # 🥱 = Schnitt (07.10.)
        self.assertEqual(lernen.entwurf_paare(self.con, KILLS, 300), ([], 1))   # … sagt nichts über die Szenen

    def test_fehlende_schnittliste_wird_uebersprungen(self):
        os.remove(self.schlecht["datei"])
        paare, n = lernen.entwurf_paare(self.con, KILLS, 300)
        self.assertEqual((paare, n), ([], 1))                                  # nur noch der gemochte, kein Paar
        self.assertFalse(Path(self.schlecht["datei"]).exists())
