"""Sicherheitsnetz für pve-big: Wächter, Halten-Marken, Frist, Wecken nur mit gesichertem Herunterfahren."""

import contextlib
import io
import json
import os
import stat
import subprocess
from datetime import timedelta
from pathlib import Path
from unittest import mock

from clip_pipeline import big, cli, konfig
from clip_pipeline.konfig import SpeicherOffline
from clip_pipeline.sperre import sperre
from clip_pipeline.zeit import iso, jetzt

from tests.hilfen import MitSpeicher

PROJEKT = Path(__file__).resolve().parents[1]


class FalscherBig:
    """Spielt pve-big: ein Skript statt ssh, das Aufrufe mitschreibt und Status aus einer Datei liefert."""

    def __init__(self, ordner: Path):
        self.ordner = ordner
        self.an = True
        self.status = {"uptime_s": 3600, "smb": 0, "ffmpeg": 0}
        self.skript = ordner / "ssh-falsch"
        self.skript.write_text(
            "#!/bin/sh\n"
            f'echo "$1" >> "{ordner}/aufrufe"\n'
            f'[ -f "{ordner}/kaputt" ] && exit 255\n'
            f'if [ "$1" = status ]; then cat "{ordner}/status.json"; fi\n'
            f'if [ "$1" = aus ]; then touch "{ordner}/aus"; fi\n'
        )
        self.skript.chmod(self.skript.stat().st_mode | stat.S_IEXEC)
        self.schreibe()

    def schreibe(self):
        (self.ordner / "status.json").write_text(json.dumps(self.status))

    def wach(self, _konfig=None):
        # nach "aus" ist er aus
        return self.an and not (self.ordner / "aus").exists()

    @property
    def aufrufe(self) -> list[str]:
        datei = self.ordner / "aufrufe"
        return datei.read_text().split() if datei.exists() else []


class Waechter(MitSpeicher):
    def setUp(self):
        super().setUp()
        self.big = FalscherBig(self.tmp)
        self.konfig.daten["speicher"].update(wol_mac="aa:bb:cc:dd:ee:ff", wecken_warten_s=30)
        self.konfig.daten["big"].update(host="pve-big", ssh=[str(self.big.skript)], mindest_wach_min=20, aus_warten_s=5)
        patcher = mock.patch.object(big, "wach", side_effect=self.big.wach)
        patcher.start()
        self.addCleanup(patcher.stop)
        schlaf = mock.patch("time.sleep")
        schlaf.start()
        self.addCleanup(schlaf.stop)

    def test_schlaeft_nichts_tun(self):
        self.big.an = False
        e = big.waechter(self.konfig)
        self.assertEqual((e["wach"], e["aus"]), (False, False))
        self.assertEqual(self.big.aufrufe, [])

    def test_ohne_auftrag_aus(self):
        e = big.waechter(self.konfig)
        self.assertTrue(e["aus"], e)
        self.assertEqual(self.big.aufrufe, ["status", "aus"])
        self.assertIn("zuletzt_aus", big.lies_zustand(self.konfig))

    def test_halten_marke_verhindert_aus_und_verfaellt(self):
        big.setze_marke(self.konfig, "material", 30, "Kopie läuft")
        e = big.waechter(self.konfig)
        self.assertFalse(e["aus"])
        self.assertIn("material", e["grund"])
        # 31 Minuten später ist die Marke abgelaufen
        e = big.waechter(self.konfig, zeit=jetzt() + timedelta(minutes=31))
        self.assertTrue(e["aus"])
        self.assertEqual(big.marken(self.konfig), [])

    def test_pipeline_sperre_verhindert_aus(self):
        with sperre(self.konfig.datenbank.with_suffix(".lock")):
            e = big.waechter(self.konfig)
        self.assertFalse(e["aus"])
        self.assertIn("Sperre", e["grund"])
        self.assertTrue(big.waechter(self.konfig)["aus"])  # Sperre wieder frei

    def test_gerade_geweckt_smb_und_ffmpeg_verhindern_aus(self):
        for status, wort in (({"uptime_s": 300, "smb": 0, "ffmpeg": 0}, "min wach"),
                             ({"uptime_s": 9999, "smb": 2, "ffmpeg": 0}, "SMB"),
                             ({"uptime_s": 9999, "smb": 0, "ffmpeg": 1}, "ffmpeg")):
            self.big.status = status
            self.big.schreibe()
            e = big.waechter(self.konfig)
            self.assertFalse(e["aus"])
            self.assertIn(wort, e["grund"])
        self.assertNotIn("aus", self.big.aufrufe)

    def test_steuerung_kaputt_gibt_alarm_statt_aus(self):
        (self.tmp / "kaputt").touch()
        e = big.waechter(self.konfig)
        self.assertFalse(e["aus"])
        self.assertIn("alarm", e)

    def test_frist_faehrt_einmal_unbedingt_herunter(self):
        self.konfig.daten["big"]["frist"] = iso(jetzt() - timedelta(minutes=1))
        big.setze_marke(self.konfig, "render", 60, "läuft noch")
        e = big.waechter(self.konfig)
        self.assertTrue(e["aus"])
        self.assertIn("Frist", e["grund"])
        self.assertIn("frist_aus", big.lies_zustand(self.konfig))
        # Danach gilt wieder die normale Regel (Marke hält ihn an, falls jemand ihn von Hand startet)
        (self.tmp / "aus").unlink()
        self.assertFalse(big.waechter(self.konfig)["aus"])

    def test_cli_alarm_landet_beim_lern_bot(self):
        (self.tmp / "kaputt").touch()
        with mock.patch("clip_pipeline.cli.lade", return_value=self.konfig), contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()) as aus:
            code = cli.main(["big", "waechter"])
        self.assertEqual(code, 0)
        self.assertIn("alarm", json.loads(aus.getvalue().strip().splitlines()[-1]))
        zeile = self.con.execute("SELECT text FROM lern_meldungen").fetchone()
        self.assertIn("pve-big", zeile["text"])


class Wecken(MitSpeicher):
    def setUp(self):
        super().setUp()
        self.big = FalscherBig(self.tmp)
        self.big.an = False
        self.konfig.daten["speicher"].update(wol_mac="aa:bb:cc:dd:ee:ff", wecken_warten_s=30)
        self.konfig.daten["big"].update(host="pve-big", ssh=[str(self.big.skript)], aus_warten_s=5)
        for ziel in (mock.patch("time.sleep"),):
            ziel.start()
            self.addCleanup(ziel.stop)

    def test_gruende_gegen_wecken(self):
        self.assertIn("nie erfolgreich", big.darf_wecken(self.konfig))
        big._schreibe_zustand(self.konfig, status_ok=iso(jetzt()))
        self.assertIsNone(big.darf_wecken(self.konfig))
        self.konfig.daten["big"]["frist"] = iso(jetzt() - timedelta(seconds=1))
        self.assertIn("Frist", big.darf_wecken(self.konfig))
        self.konfig.daten["big"].update(frist="", ssh=None, ssh_ziel="")
        self.assertIn("nicht eingerichtet", big.darf_wecken(self.konfig))

    def test_wecken_verboten_sendet_nichts(self):
        with mock.patch.object(big, "wach", return_value=False), \
                mock.patch.object(big, "sende_wake_on_lan") as wol, self.assertRaises(big.WeckenVerboten):
            with big.wach_halten(self.konfig, "material", "Test"):
                self.fail("darf nicht laufen")
        wol.assert_not_called()

    def test_wecken_arbeiten_sofort_aus(self):
        big._schreibe_zustand(self.konfig, status_ok=iso(jetzt()))
        zustand = {"an": False}

        def wol(_mac):
            zustand["an"] = True

        def wach(_k=None):
            return zustand["an"] and not (self.tmp / "aus").exists()

        with mock.patch.object(big, "wach", side_effect=wach), mock.patch.object(big, "sende_wake_on_lan", side_effect=wol):
            with big.wach_halten(self.konfig, "material", "Material kopieren") as bereit:
                self.assertTrue(bereit)
                self.assertEqual([m.name for m in big.marken(self.konfig)], ["material"])
                self.big.status["uptime_s"] = 240  # gerade erst hochgefahren – zählt nicht, wir haben ihn geweckt
                self.big.schreibe()
        self.assertEqual(self.big.aufrufe[-1], "aus")
        self.assertEqual(big.marken(self.konfig), [])

    def test_lief_schon_bleibt_an(self):
        with mock.patch.object(big, "wach", return_value=True), mock.patch.object(big, "sende_wake_on_lan") as wol:
            with big.wach_halten(self.konfig, "material", "Test"):
                pass
        wol.assert_not_called()
        self.assertNotIn("aus", self.big.aufrufe)

    def test_alter_weckweg_respektiert_frist(self):
        self.konfig.daten["speicher"]["host"] = "pve-big"
        self.konfig.daten["big"]["frist"] = iso(jetzt() - timedelta(seconds=1))
        with mock.patch.object(konfig.Konfig, "_host_erreichbar", return_value=False), \
                mock.patch("clip_pipeline.konfig.sende_wake_on_lan") as wol, self.assertRaises(SpeicherOffline):
            self.konfig.pruefe_speicher(wecken=True)
        wol.assert_not_called()


class SteuerSkript(MitSpeicher):
    """deploy/big/clip-big-steuer.sh: nur status und aus, alles andere abgewiesen ("aus" testen wir nicht)."""

    def lauf(self, befehl: str) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", str(PROJEKT / "deploy/big/clip-big-steuer.sh")], capture_output=True, text=True,
                              env={**os.environ, "SSH_ORIGINAL_COMMAND": befehl})

    def test_status_ist_json(self):
        ergebnis = self.lauf("status")
        self.assertEqual(ergebnis.returncode, 0, ergebnis.stderr)
        daten = json.loads(ergebnis.stdout)
        self.assertEqual(set(daten), {"uptime_s", "smb", "ffmpeg"})

    def test_alles_andere_abgewiesen(self):
        for befehl in ("", "status; rm -rf /", "aus now", "bash", "status\naus"):
            self.assertEqual(self.lauf(befehl).returncode, 2, befehl)
