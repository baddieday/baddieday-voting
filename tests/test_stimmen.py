"""Stimmen im Upload nur, wenn der Moment sie braucht (Florian, 26.09.): Lachen, Jubel, laute Mikro-Spitzen oder ein
Gag (Stimmung „lustig“). Sonst nur Spur 0 (Spielton). Für die Bewertung zählt weiter alles; Vorschauen bleiben gleich.
"""

import json
import unittest

from clip_pipeline import entwurf, merkmale, regie, shorts
from clip_pipeline.zeit import iso, jetzt

from tests.hilfen import MitSpeicher
from tests.regie_hilfen import MOMENTE, MitRegieMaterial
from tests.test_entwurf import BildGraph


class Regel(unittest.TestCase):
    def test_lachen_jubel_gag_ja_sonst_nein(self):
        self.assertTrue(merkmale.stimmen_gebraucht({"lachen": 1}, "spannend"))
        self.assertTrue(merkmale.stimmen_gebraucht({"jubel_laut": 2}, "episch"))
        self.assertTrue(merkmale.stimmen_gebraucht({}, "lustig"))
        self.assertFalse(merkmale.stimmen_gebraucht({"frust": 3, "spitzen": 5}, "frustriert"))
        self.assertFalse(merkmale.stimmen_gebraucht(None, None))


class FuerClip(MitSpeicher):
    def moment(self, clip_id, mk, stimmung="spannend"):
        self.con.execute(
            """INSERT INTO momente (schluessel, match_id, clip_id, datei, start_s, ende_s, kills, stimmung, sicherheit,
                                    quelle, merkmale, erstellt, geaendert)
               VALUES (?, 'm1', ?, 'x.mp4', 0, 20, 1, ?, 0.8, 'regel', ?, ?, ?)""",
            (f"clip:{clip_id}", clip_id, stimmung, json.dumps(mk), iso(jetzt()), iso(jetzt())))

    def test_aus_momente_oder_clip_merkmalen(self):
        mit_lachen = self.clip_anlegen()
        self.moment(mit_lachen, {"lachen": 2})
        self.assertTrue(merkmale.stimmen_fuer_clip(self.con, mit_lachen))
        # keine momente-Zeile: Mic-Werte aus clips.merkmale (vom Mic-Schritt übernommen)
        jubel = self.clip_anlegen(merkmale={"kill_punkte": 1.0, "mic_jubel": 1.0})
        self.assertTrue(merkmale.stimmen_fuer_clip(self.con, jubel))
        still = self.clip_anlegen()
        self.assertFalse(merkmale.stimmen_fuer_clip(self.con, still))  # nichts bekannt → nur Spielton
        self.assertFalse(merkmale.stimmen_fuer_clip(self.con, 999))


class Graphen(unittest.TestCase):
    def test_entwurf_nimmt_ohne_stimmen_nur_spur_0(self):
        liste = BildGraph.liste("short")
        liste["segmente"][0]["stimmen"] = False
        liste["segmente"][1]["stimmen"] = True   # Segment 2 ohne Schlüssel: alte Liste → wie bisher alle Spuren
        graph, _ = entwurf.filtergraph(liste, [2, 2, 2], b=720, h=1280, musik_eingang=None, schrift=None)
        self.assertIn("[0:a:0]", graph)
        self.assertNotIn("[0:a:1]", graph)
        self.assertIn("[1:a:1]", graph)
        self.assertIn("[2:a:1]", graph)

    def test_short_ohne_stimmen_nur_spur_0(self):
        from clip_pipeline import konfig
        k = konfig.lade()
        mit, _ = shorts.filtergraph(dauer=10, fps=30, tonspuren=2, layout="unschaerfe", konfig=k)
        ohne, _ = shorts.filtergraph(dauer=10, fps=30, tonspuren=2, layout="unschaerfe", konfig=k, stimmen=False)
        self.assertIn("[0:a:1]", mit)
        self.assertNotIn("[0:a:1]", ohne)
        self.assertIn("[0:a:0]", ohne)


class RegieSegmente(MitRegieMaterial):
    def test_jedes_segment_sagt_ob_stimmen(self):
        self.momente_anlegen(MOMENTE[:8])
        e = regie.erstelle(self.con, self.konfig, "short")
        with open(e["datei"], encoding="utf-8") as f:
            liste = json.load(f)
        self.assertEqual(regie.pruefe_liste(liste), [])  # Schema kennt das Feld
        for s in liste["segmente"]:
            zeile = self.con.execute("SELECT merkmale, stimmung FROM momente WHERE schluessel = ?",
                                     (s["moment"],)).fetchone()
            self.assertEqual(s["stimmen"], merkmale.stimmen_gebraucht(json.loads(zeile["merkmale"]), zeile["stimmung"]))


if __name__ == "__main__":
    unittest.main()
