import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from clip_pipeline import medien

# Liest das eigene Todes-Signal (prctl PR_GET_PDEATHSIG = 2) und gibt es aus: 9 = endet mit seinem Aufrufer (SIGKILL)
TODES_SIGNAL = ("import ctypes; s = ctypes.c_int(); ctypes.CDLL(None).prctl(2, ctypes.byref(s), 0, 0, 0); "
                "print(s.value)")


class Waechter(unittest.TestCase):
    def test_normaler_lauf_gibt_ausgabe(self):
        self.assertIn("ok", medien.fuehre_aus([sys.executable, "-c", "print('ok')"], "Probe", pruef_s=0.1))

    @unittest.skipUnless(medien._cpu_ticks(1) is not None, "kein /proc")
    def test_haengender_prozess_wird_abgebrochen(self):
        # schläft ohne CPU-Zeit – so sah der festgefahrene VA-API-Render aus
        with self.assertRaisesRegex(medien.MedienFehler, "hängt"):
            medien.fuehre_aus([sys.executable, "-c", "import time; time.sleep(30)"], "Probe",
                              stillstand_s=1.0, pruef_s=0.2)


@unittest.skipUnless(sys.platform.startswith("linux"), "Todes-Signal gibt es nur unter Linux")
class Mitsterben(unittest.TestCase):
    """Stufe 3 (M143): Jeder Befehl startet über setpriv und endet mit seinem Aufrufer; geht setpriv nicht, wie bisher.
    Der Test mit einem wirklich getöteten Aufrufer steht in test_abnahme_stufe3."""

    def setUp(self):
        medien._mitsterben.cache_clear()          # frische Probe – nie das Ergebnis eines anderen Tests
        self.addCleanup(medien._mitsterben.cache_clear)

    def test_befehl_endet_mit_seinem_aufrufer(self):
        if not medien._mitsterben():
            self.skipTest("setpriv fehlt oder geht hier nicht")
        self.assertEqual(medien.fuehre_aus([sys.executable, "-c", TODES_SIGNAL], "Probe", pruef_s=0.1).strip(), "9")
        # „nicht gefunden“ bleibt dieselbe Meldung wie ohne setpriv davor
        with self.assertRaisesRegex(medien.MedienFehler, "Programm 'gibt-es-nicht-xyz' nicht gefunden"):
            medien.fuehre_aus(["gibt-es-nicht-xyz"], "Probe")

    def test_ohne_setpriv_wie_bisher_mit_einer_logzeile(self):
        with mock.patch.object(medien.subprocess, "run", side_effect=FileNotFoundError("setpriv")), \
                self.assertLogs("clip_pipeline.medien", "WARNING") as logs:
            erst = medien.fuehre_aus([sys.executable, "-c", TODES_SIGNAL], "Probe", pruef_s=0.1)
            dann = medien.fuehre_aus([sys.executable, "-c", TODES_SIGNAL], "Probe", pruef_s=0.1)
        self.assertEqual((erst.strip(), dann.strip()), ("0", "0"))
        self.assertEqual(len(logs.records), 1)      # geprüft wird einmal je Prozess


class Uebernehmen(unittest.TestCase):
    """Stufe 3 (M144): erst fsync der Datei, dann umbenennen, dann fsync des Ordners."""

    def setUp(self):
        ordner = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.tmp, self.ziel = ordner / "a.tmp.mp4", ordner / "a.mp4"
        self.tmp.write_bytes(b"video")

    def test_datei_und_ordner_auf_die_platte_ordner_fehler_egal(self):
        with mock.patch.object(medien.os, "fsync", side_effect=[None, OSError("Ordner kann kein fsync")]) as fsync:
            medien.uebernehmen(self.tmp, self.ziel)
        self.assertEqual(fsync.call_count, 2)       # Datei, dann Ordner – der Fehler beim Ordner zählt nicht
        self.assertEqual(self.ziel.read_bytes(), b"video")
        self.assertFalse(self.tmp.exists())

    def test_scheitert_fsync_der_datei_bleibt_der_endname_frei(self):
        with mock.patch.object(medien.os, "fsync", side_effect=OSError("Platte voll")), self.assertRaises(OSError):
            medien.uebernehmen(self.tmp, self.ziel)
        self.assertFalse(self.ziel.exists())        # nie eine halbe Datei unter dem Endnamen
        self.assertTrue(self.tmp.exists())

    @unittest.skipIf(sys.platform == "win32", "nachgestellt über fcntl – unter Windows gilt der echte Fall")
    def test_windows_fsync_braucht_schreibrecht(self):
        """Prüfung K1 (M153): Unter Windows ist os.fsync FlushFileBuffers, und das scheitert auf einem nur lesend
        geöffneten Handle (EBADF) – jedes Rendern endete dort, bevor die Datei ihren Namen bekam. Nachgestellt: fsync
        wie unter Windows. Der Ordner bleibt nur lesbar; sein Fehler zählt wie bisher nicht."""
        import errno
        import fcntl

        def fsync_wie_windows(fd):
            if (fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE) == os.O_RDONLY:
                raise OSError(errno.EBADF, "Bad file descriptor")

        with mock.patch.object(medien.sys, "platform", "win32"), \
                mock.patch.object(medien.os, "fsync", side_effect=fsync_wie_windows) as fsync:
            medien.uebernehmen(self.tmp, self.ziel)
        self.assertEqual(fsync.call_count, 2)
        self.assertEqual(self.ziel.read_bytes(), b"video")
        self.assertFalse(self.tmp.exists())


class SchnittRueckfall(unittest.TestCase):
    """Stufe 3 (M145): Streikt VA-API beim Schneiden, schneidet die CPU einmal nach (gleicher crf) – wie
    entwurf.rendere. ffmpeg ist hier eine Attrappe: Sie scheitert mit VA-API und schreibt sonst die Zwischendatei."""

    def setUp(self):
        self.ordner = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.ziel = self.ordner / "clips" / "001_double_2k.mp4"
        self.befehle: list[list[str]] = []

    def schneide(self, encoder: str, *, cpu_geht: bool = True) -> None:
        def lauf(befehl, was, *args, **kwargs):
            self.befehle.append(befehl)
            if "h264_vaapi" in befehl or not cpu_geht:
                raise medien.MedienFehler(f"{was} fehlgeschlagen (Exit 1):\nFailed to initialise VAAPI connection")
            Path(befehl[-1]).write_bytes(b"clip")
            return ""

        with mock.patch.object(medien, "fuehre_aus", side_effect=lauf):
            medien.schneide(self.ordner / "quelle.mp4", 12.0, 16.0, self.ziel, fps=60, encoder=encoder, crf=21)

    def test_vaapi_streikt_cpu_schneidet_nach(self):
        with self.assertLogs("clip_pipeline.medien", "WARNING"):
            self.schneide("h264_vaapi")
        vaapi, cpu = self.befehle
        self.assertEqual(self.ziel.read_bytes(), b"clip")
        self.assertFalse(self.ziel.with_name("001_double_2k.tmp.mp4").exists())
        self.assertEqual((vaapi[vaapi.index("-qp") + 1], cpu[cpu.index("-crf") + 1]), ("21", "21"))   # gleicher crf
        self.assertEqual((cpu[cpu.index("-c:v") + 1], "-vaapi_device" in cpu), ("libx264", False))
        for schalter in ("-ss", "-t", "-r"):        # dieselbe Stelle, Länge und Bildrate
            self.assertEqual(cpu[cpu.index(schalter) + 1], vaapi[vaapi.index(schalter) + 1])

    def test_cpu_fehler_ohne_zweiten_versuch(self):
        with self.assertRaises(medien.MedienFehler):
            self.schneide("libx264", cpu_geht=False)
        self.assertEqual(len(self.befehle), 1)
        self.assertFalse(self.ziel.exists())


if __name__ == "__main__":
    unittest.main()
