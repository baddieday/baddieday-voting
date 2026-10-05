"""🔥 Viral (viral.py, 05.10.): KI-Einschätzung je Moment, Mischung je Variante, Fail-Video mit Hook und Standbild."""

import json
import unittest
from pathlib import Path
from unittest import mock

from clip_pipeline import caption, claude_aufruf, lern_features, regie, viral

from tests.hilfen import HAT_FFMPEG
from tests.regie_hilfen import FAILS, MOMENTE, MitRegieMaterial

class MitFails(MitRegieMaterial):
    """Fail-Momente über regie_hilfen.fails_anlegen; die KI-Tests schalten [viral].ki selbst ein."""


class Titel(unittest.TestCase):
    def test_nur_fakten_und_schrift(self):
        self.assertEqual(viral.pruefe_titel("Platz 2 😭", {"platz": 2}), "PLATZ 2")
        self.assertEqual(viral.pruefe_titel("3 Kills ... und weg", {"kills_vorher_30s": 3}), "3 KILLS … UND WEG")

    def test_erfundene_zahl_wird_verworfen(self):
        self.assertIsNone(viral.pruefe_titel("PLATZ 1 – FAST", {"platz": 2}))
        self.assertIsNone(viral.pruefe_titel("X" * 31, {}))


@unittest.skipUnless(HAT_FFMPEG, "ffmpeg fehlt")
class Einschaetzung(MitFails):
    def setUp(self):
        super().setUp()
        self.konfig.daten.setdefault("viral", {})["ki"] = True     # claude selbst ist gemockt

    def antwort(self, daten, hinweis=None):
        return claude_aufruf.ClaudeAntwort(daten, hinweis, None, gestartet=True)

    def test_ki_einmal_je_moment_mit_geprueftem_titel(self):
        namen = self.fails_anlegen(FAILS[:2])
        daten = {"momente": [{"id": "m1", "viral": 30, "humor": 40, "spannung": 20, "titel": "PLATZ 7", "grund": "x"},
                             {"id": "m2", "viral": 91, "humor": 80, "spannung": 70, "titel": "PLATZ 2 – 3 KILLS"}]}
        with mock.patch.object(claude_aufruf, "frage_json", return_value=self.antwort(daten)) as frage:
            e = viral.einschaetzen(self.con, self.konfig, schluessel=namen)
            self.assertEqual((e["neu"], e["aufrufe"]), (2, 1))
            self.assertEqual(viral.einschaetzen(self.con, self.konfig, schluessel=namen)["neu"], 0)  # schon da
        self.assertEqual(frage.call_count, 1)
        ki = viral.einschaetzungen(self.con)
        self.assertIsNone(ki[namen[0]]["titel"])                  # „PLATZ 7“ steht in keinem Fakt
        self.assertEqual((ki[namen[1]]["viral"], ki[namen[1]]["titel"]), (91, "PLATZ 2 – 3 KILLS"))

    def test_ki_ausfall_regel_gilt(self):
        namen = self.fails_anlegen(FAILS[:1])
        with mock.patch.object(claude_aufruf, "frage_json", return_value=self.antwort(None, "claude Exit 1")):
            e = viral.einschaetzen(self.con, self.konfig, schluessel=namen)
        self.assertEqual(e["neu"], 0)
        self.assertIn("Regel-Score", e["hinweise"][0])
        self.assertEqual(viral.einschaetzungen(self.con), {})


@unittest.skipUnless(HAT_FFMPEG, "ffmpeg fehlt")
class Formate(MitFails):
    def test_fail_video_steigend_mit_hook_und_standbild(self):
        self.konfig.daten["regie"]["effekte"]["an"] = True
        self.fails_anlegen()
        self.musik_anlegen(120, "frustriert")
        p = dict(regie.PARAMETER, hook_teaser=True, tod_standbild=True, tod_lupe=True, reihenfolge="steigend")
        e = regie.erstelle(self.con, self.konfig, "short", parameter=p, variante="fail")
        liste = json.loads(Path(e["datei"]).read_text(encoding="utf-8"))
        self.assertEqual(regie.pruefe_liste(liste), [])
        self.assertEqual((liste["format"], liste["variante"]), ("short", "fail"))
        self.assertTrue(4 <= e["momente"] <= 8)
        self.assertTrue(30 <= liste["dauer_s"] <= 60)
        self.assertEqual(liste["bogen"], sorted(liste["bogen"]))          # der schlimmste Fail zuletzt
        hook = liste["segmente"][0]
        self.assertEqual((hook["rolle"], hook["moment"]), ("hook", liste["segmente"][-1]["moment"]))
        self.assertIn("tod_s", hook)
        self.assertTrue(1.0 <= hook["zeit_ende"] <= 1.5)
        self.assertTrue(any(s.get("standbild") for s in liste["segmente"]))
        titel = [x["text"] for s in liste["segmente"] for x in s.get("effekte") or [] if x["art"] == "titel"]
        self.assertIn("PLATZ 2", titel)                                    # Titel aus Fakten, kein Kill-Titel
        self.assertFalse(any("KILL" == t[-4:] for t in titel))
        zeile = self.con.execute("SELECT format, variante FROM entwuerfe WHERE id = ?", (e["entwurf"],)).fetchone()
        self.assertEqual(tuple(zeile), ("short", "fail"))
        x = lern_features.extrahiere({"dauer_s": liste["dauer_s"], "schnittliste": liste})
        self.assertEqual((x["variante:fail"], x["mix_fail"], x["werkzeug:hook_teaser"]), (1.0, 1.0, 1.0))
        text = caption.entwurf_caption(self.con, liste, self.konfig)       # nur Fakten, Mitmach-Frage, clip-battle.de
        self.assertIn("Fortnite-Fails: Platz 2 · 3 Kills davor", text)
        self.assertIn("Was hättest du gemacht?", text)
        self.assertIn("Größter Fail der Woche – stimm ab auf clip-battle.de", text)

    def test_twist_ein_fail_zwischen_highlights(self):
        self.momente_anlegen(MOMENTE[:10])
        self.fails_anlegen(FAILS[:3])
        self.musik_anlegen(150, "episch")
        p = dict(regie.PARAMETER, twist_anzahl=1)
        e = regie.erstelle(self.con, self.konfig, "short", parameter=p, variante="twist")
        liste = json.loads(Path(e["datei"]).read_text(encoding="utf-8"))
        momente = [s["moment"] for s in liste["segmente"] if s.get("teil", 1) == 1 and s.get("rolle") != "hook"]
        fails = [i for i, m in enumerate(momente) if m.startswith("fail:")]
        self.assertEqual(len(fails), 1)
        self.assertTrue(0 < fails[0] < len(momente) - 1)                   # nie vorn, nie als Höhepunkt
        self.assertEqual(liste["viral"]["fails"], 1)

    def test_normale_shorts_ohne_fails(self):
        self.momente_anlegen(MOMENTE[:10])
        self.fails_anlegen(FAILS[:3])
        self.musik_anlegen(150, "episch")
        e = regie.erstelle(self.con, self.konfig, "short")
        liste = json.loads(Path(e["datei"]).read_text(encoding="utf-8"))
        self.assertFalse(any(s["moment"].startswith("fail:") for s in liste["segmente"]))
        self.assertNotIn("variante", liste)

    def test_fail_video_ohne_fails_klare_meldung(self):
        self.momente_anlegen(MOMENTE[:6])
        with self.assertRaises(regie.RegieFehler) as fehler:
            regie.erstelle(self.con, self.konfig, "short", variante="fail")
        self.assertIn("pipeline fail", str(fehler.exception))


class Variantenwahl(MitFails):
    def test_waehlt_nur_was_das_material_hergibt(self):
        self.assertEqual(viral.verfuegbar(self.con), {"highlight": True, "twist": False, "fail": False})
        self.assertEqual(viral.waehle_variante(self.con, self.konfig), "highlight")
        self.konfig.daten.setdefault("viral", {})["variante"] = "fail"    # fest gewählt, aber keine Fails
        self.assertEqual(viral.waehle_variante(self.con, self.konfig), "highlight")


if __name__ == "__main__":
    unittest.main()
