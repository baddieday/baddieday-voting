import sys
import unittest

from clip_pipeline import medien


class Waechter(unittest.TestCase):
    def test_normaler_lauf_gibt_ausgabe(self):
        self.assertIn("ok", medien.fuehre_aus([sys.executable, "-c", "print('ok')"], "Probe", pruef_s=0.1))

    @unittest.skipUnless(medien._cpu_ticks(1) is not None, "kein /proc")
    def test_haengender_prozess_wird_abgebrochen(self):
        # schläft ohne CPU-Zeit – so sah der festgefahrene VA-API-Render aus
        with self.assertRaisesRegex(medien.MedienFehler, "hängt"):
            medien.fuehre_aus([sys.executable, "-c", "import time; time.sleep(30)"], "Probe",
                              stillstand_s=1.0, pruef_s=0.2)


if __name__ == "__main__":
    unittest.main()
