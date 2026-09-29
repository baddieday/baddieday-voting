"""Produktgrenzen und tatsächliche Planwirkung, ohne umfangreiche Render-Fixtures."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from clip_pipeline import entwurf, regie
from clip_pipeline.medien import MedienFehler
from tests.regie_hilfen import MitRegieMaterial


class FormatUndPlan(MitRegieMaterial):
    def kandidaten(self, anzahl=20):
        return [regie.Kandidat(f"datei:{i}", str(self.tmp / "quelle.mp4"), 20.0, "episch",
                               20.0 - i, 20.0 - i, None, f"m{i}", (4.0, 12.0), (5.0, 11.5), "Test")
                for i in range(anzahl)]

    def compose(self, fmt, parameter, anzahl=20):
        with mock.patch.object(regie, "kandidaten_mit_bericht", return_value=(
                self.kandidaten(anzahl), {"ohne_datei": 0, "ersetzt": 0, "gesperrt": 0})):
            return regie.erstelle(self.con, self.konfig, fmt, parameter=parameter)

    def test_harte_grenzen_trotz_altkonfig_und_zu_wenig_material(self):
        self.konfig.daten["regie"]["formate"] = {
            "short": {"min_s": 15, "max_s": 600},
            "zusammenschnitt": {"min_s": 180, "max_s": 300, "ziel_s": 300}}
        for fmt, (unten, oben) in regie.DAUER_GRENZEN.items():
            for ziel in (1, 1000):
                with self.subTest(fmt=fmt, ziel=ziel):
                    e = self.compose(fmt, {"ziel_dauer_s": ziel})
                    self.assertGreaterEqual(e["dauer_s"], unten)
                    self.assertLessEqual(e["dauer_s"], oben)
            with self.assertRaises(regie.RegieFehler):
                self.compose(fmt, {}, anzahl=1)

    def test_gelerntes_ziel_und_hook_aendern_den_gespeicherten_plan(self):
        basis = {"abwechslung": 0.0, "dauer_faktor": 0.6, "ziel_dauer_s": 35.0}
        vorher = json.loads(Path(self.compose("short", basis)["datei"]).read_text(encoding="utf-8"))
        nachher = json.loads(Path(self.compose("short", {**basis, "ziel_dauer_s": 65.0,
            "hook_staerkster": True, "autonom": {"version": 4}})["datei"]).read_text(encoding="utf-8"))
        self.assertLess(vorher["dauer_s"], 45)
        self.assertGreaterEqual(nachher["dauer_s"], 60)
        self.assertLessEqual(nachher["dauer_s"], 75)
        self.assertNotEqual(vorher["segmente"][0]["moment"], nachher["segmente"][0]["moment"])
        self.assertEqual(nachher["bogen"][0], max(nachher["bogen"]))
        self.assertEqual(nachher["parameter"]["autonom"]["version"], 4)

    def test_render_misst_video_statt_plan_und_verweigert_falsche_laenge(self):
        for fmt, geplant, wirklich in (("short", 75, 75), ("zusammenschnitt", 75, 75),
                                       ("short", 75, 75.03), ("zusammenschnitt", 75, 74.97),
                                       ("short", 60, 20), ("zusammenschnitt", 120, 121)):
            with self.subTest(fmt=fmt, wirklich=wirklich):
                liste = {"format": fmt, "dauer_s": geplant, "fps": 30}
                antwort = SimpleNamespace(returncode=0, stdout=json.dumps({"streams": [{"duration": wirklich}]}))
                with mock.patch.object(entwurf.subprocess, "run", return_value=antwort):
                    if geplant == wirklich:
                        self.assertEqual(entwurf._pruefe_renderdauer(liste, Path("film.mp4")), wirklich)
                    else:
                        with self.assertRaises(MedienFehler):
                            entwurf._pruefe_renderdauer(liste, Path("film.mp4"))
