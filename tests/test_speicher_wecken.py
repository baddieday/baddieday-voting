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

    def test_ohne_gesichertes_herunterfahren_kein_wecken(self):
        # Sprint-Regel 3: auch der alte Weckweg weckt nur, wenn pve-big danach sicher wieder ausgeht
        with mock.patch.object(konfig.Konfig, "_host_erreichbar", return_value=False), \
                mock.patch("clip_pipeline.konfig.sende_wake_on_lan") as wol:
            with self.assertRaises(SpeicherOffline) as fehler:
                self.konfig.pruefe_speicher(wecken=True)
        self.assertIn("nicht geweckt", str(fehler.exception))
        wol.assert_not_called()

    def test_schlafender_host_wird_geweckt(self):
        self.konfig.daten["big"]["alter_weckweg_nur_mit_aus"] = False  # bisheriges Verhalten
        with mock.patch.object(konfig.Konfig, "_host_erreichbar", side_effect=[False, False, True]), \
                mock.patch("clip_pipeline.konfig.sende_wake_on_lan") as wol, mock.patch("time.sleep"):
            self.konfig.pruefe_speicher(wecken=True)
        # je Warterunde erneut (ein einzelnes Paket verpufft, wenn der Host gerade noch herunterfährt)
        self.assertEqual(wol.call_args_list, [mock.call("aa:bb:cc:dd:ee:ff")] * 2)

    def test_ohne_wecken_sofort_offline(self):
        with mock.patch.object(konfig.Konfig, "_host_erreichbar", return_value=False), \
                mock.patch("clip_pipeline.konfig.sende_wake_on_lan") as wol:
            with self.assertRaises(SpeicherOffline):
                self.konfig.pruefe_speicher()
        wol.assert_not_called()


class LokaleKonfig(unittest.TestCase):
    def test_lokal_toml_ueberschreibt_nur_einzelne_werte(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            haupt = Path(tmp) / "pipeline.toml"
            haupt.write_text('[speicher]\nwurzel = "/srv/clips"\nhost = ""\n[elo]\nstart = 1500.0\n', encoding="utf-8")
            (Path(tmp) / "lokal.toml").write_text('[speicher]\nhost = "192.168.178.51"\n', encoding="utf-8")
            with mock.patch.dict(os.environ, {}, clear=False):
                for name in ("CLIP_SPEICHER", "CLIP_DATENBANK", "CLIP_EPIC_ID"):
                    os.environ.pop(name, None)
                k = konfig.lade(haupt)
        self.assertEqual(k.wert("speicher.host"), "192.168.178.51")
        self.assertEqual(k.wert("speicher.wurzel"), "/srv/clips")  # bleibt erhalten
        self.assertEqual(k.wert("elo.start"), 1500.0)


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
