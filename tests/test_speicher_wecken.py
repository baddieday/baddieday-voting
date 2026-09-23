import contextlib
import io
import json
import os
import unittest
from unittest import mock

from clip_pipeline import cli, konfig
from clip_pipeline.konfig import SpeicherOffline

from tests.hilfen import MitSpeicher


class WakeOnLan(unittest.TestCase):
    def test_magisches_paket(self):
        with mock.patch("socket.socket") as sock:
            paket = konfig.sende_wake_on_lan("AA-BB-CC-DD-EE-FF")
        self.assertEqual(len(paket), 6 + 16 * 6)
        self.assertEqual(paket[:6], b"\xff" * 6)
        self.assertEqual(paket[6:12], bytes.fromhex("AABBCCDDEEFF"))
        ziel = sock.return_value.__enter__.return_value.sendto.call_args.args[1]
        self.assertEqual(ziel, ("255.255.255.255", 9))
        with self.assertRaises(ValueError):
            konfig.sende_wake_on_lan("12:34")


class Wecken(MitSpeicher):
    def setUp(self):
        super().setUp()
        self.konfig.daten["speicher"].update(host="pve-gross", wol_mac="aa:bb:cc:dd:ee:ff", wecken_warten_s=60)

    def test_schlafender_host_wird_geweckt(self):
        with mock.patch.object(konfig.Konfig, "_host_erreichbar", side_effect=[False, False, True]), \
                mock.patch("clip_pipeline.konfig.sende_wake_on_lan") as wol, mock.patch("time.sleep"):
            self.konfig.pruefe_speicher(wecken=True)
        wol.assert_called_once_with("aa:bb:cc:dd:ee:ff")

    def test_ohne_wecken_sofort_offline(self):
        with mock.patch.object(konfig.Konfig, "_host_erreichbar", return_value=False), \
                mock.patch("clip_pipeline.konfig.sende_wake_on_lan") as wol:
            with self.assertRaises(SpeicherOffline):
                self.konfig.pruefe_speicher()
        wol.assert_not_called()


class AufraeumenTaeglich(MitSpeicher):
    def _cli(self, *argv) -> dict:
        umgebung = {"CLIP_SPEICHER": str(self.konfig.wurzel), "CLIP_DATENBANK": str(self.konfig.datenbank)}
        ausgabe = io.StringIO()
        with mock.patch.dict(os.environ, umgebung), contextlib.redirect_stdout(ausgabe), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(list(argv)), 0)
        return json.loads(ausgabe.getvalue().strip().splitlines()[-1])

    def test_hoechstens_einmal_am_tag(self):
        self.assertIn("erledigt", self._cli("aufraeumen", "--ausfuehren", "--taeglich"))
        self.assertTrue(self._cli("aufraeumen", "--ausfuehren", "--taeglich")["uebersprungen"])


if __name__ == "__main__":
    unittest.main()
