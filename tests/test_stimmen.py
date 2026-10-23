"""Stimmen im fertigen Video: ab Werk nie (22.10., Florian: „Stimmen deiner Mitspieler sollen und können sofort raus
geschnitten werden“ – [regie.ton] stimmen = false → nur Spur 0, Spielton). Mit stimmen = true gilt die Regel vom 26.09.:
nur, wenn der Moment sie braucht (Lachen, Jubel, laute Mikro-Spitzen oder ein Gag). Für die Bewertung zählt weiter
alles; Vorschauen bleiben gleich.
"""

import copy
import json
import unittest

from clip_pipeline import entwurf, konfig, merkmale, regie, shorts
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
    def test_entwurf_nimmt_ab_werk_nie_stimmen(self):
        # 22.10.: auch Segmente, die Stimmen „brauchen“, bekommen nur Spur 0 – die Mitspieler kommen raus
        liste = BildGraph.liste("short")
        liste["segmente"][0]["stimmen"] = False
        liste["segmente"][1]["stimmen"] = True
        graph, _ = entwurf.filtergraph(liste, [2, 2, 2], b=720, h=1280, musik_eingang=None, schrift=None)
        self.assertIn("[0:a:0]", graph)
        for i in range(3):
            self.assertNotIn(f"[{i}:a:1]", graph)
        self.assertEqual(entwurf.ton_einstellungen(konfig.lade())["stimmen"], 0.0)   # pipeline.toml: stimmen = false

    def test_entwurf_mit_stimmen_an_nur_wenn_der_moment_sie_braucht(self):
        liste = BildGraph.liste("short")
        liste["segmente"][0]["stimmen"] = False
        liste["segmente"][1]["stimmen"] = True   # Segment 2 ohne Schlüssel: alte Liste → wie bisher alle Spuren
        graph, _ = entwurf.filtergraph(liste, [2, 2, 2], b=720, h=1280, musik_eingang=None, schrift=None,
                                       ton={"stimmen": 1.0})
        self.assertIn("[0:a:0]", graph)
        self.assertNotIn("[0:a:1]", graph)
        self.assertIn("[1:a:1]", graph)
        self.assertIn("[2:a:1]", graph)

    def test_short_ohne_stimmen_nur_spur_0(self):
        k = konfig.lade()
        self.assertFalse(merkmale.stimmen_erlaubt(k))   # ab Werk aus (22.10.)
        werk, _ = shorts.filtergraph(dauer=10, fps=30, tonspuren=2, layout="unschaerfe", konfig=k)
        self.assertNotIn("[0:a:1]", werk)   # auch stimmen=True (Moment braucht sie) → nur Spielton
        self.assertIn("[0:a:0]", werk)
        an = konfig.Konfig(daten=copy.deepcopy(k.daten), quelle=k.quelle)
        an.daten["regie"]["ton"]["stimmen"] = True
        self.assertTrue(merkmale.stimmen_erlaubt(an))
        mit, _ = shorts.filtergraph(dauer=10, fps=30, tonspuren=2, layout="unschaerfe", konfig=an)
        ohne, _ = shorts.filtergraph(dauer=10, fps=30, tonspuren=2, layout="unschaerfe", konfig=an, stimmen=False)
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
