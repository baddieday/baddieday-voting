"""Getrennter Betrieb (E19): Puffer auf dem Mini, Lager auf pve-big – Konfig und big.py.

Zwei Temp-Ordner spielen Puffer und Lager. Sie liegen auf demselben Dateisystem; die st_dev-Prüfung wird deshalb
per patch umgangen (außer im Test, der genau diesen Abbruch beweist). Kein echter Host, kein echtes WoL.
"""

import contextlib
import io
import os
import socket
import subprocess
import sys
import tomllib
import unittest
from datetime import timedelta
from pathlib import Path
from time import sleep as time_sleep
from unittest import mock

from clip_pipeline import big, cli, konfig
from clip_pipeline.konfig import KonfigFehler, SpeicherOffline
from clip_pipeline.sperre import sperre
from clip_pipeline.zeit import iso, jetzt

from tests.hilfen import MitSpeicher
from tests.test_big import FalscherBig

PROJEKT = Path(__file__).resolve().parents[1]
ECHT_DATEISYSTEM = konfig._dateisystem  # vor jedem patch gemerkt


class Uhr:
    """Falsche Uhr für Warteschleifen: sleep() rückt monotonic() vor, statt zu warten."""

    def __init__(self, bei_schlaf=None):
        self.jetzt = 0.0
        self.schlaefe = 0
        self.bei_schlaf = bei_schlaf

    def monotonic(self) -> float:
        return self.jetzt

    def sleep(self, sekunden: float) -> None:
        self.jetzt += sekunden
        self.schlaefe += 1
        if self.bei_schlaf:
            self.bei_schlaf(self.schlaefe)


@contextlib.contextmanager
def fremde_sperre(pfad: Path, bereit: Path):
    """Ein ANDERER Prozess hält die flock-Sperre (wie n8n-render oder ein Lager-Abgleich)."""
    halter = subprocess.Popen([sys.executable, "-c", (
        "import fcntl, os, sys, time; fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT); "
        "fcntl.flock(fd, fcntl.LOCK_EX); open(sys.argv[2], 'w').close(); time.sleep(30)"), str(pfad), str(bereit)])
    try:
        for _ in range(200):
            if bereit.exists():
                break
            time_sleep(0.05)
        yield
    finally:
        halter.kill()
        halter.wait()
        bereit.unlink(missing_ok=True)


class MitLager(MitSpeicher):
    """Speicher = Puffer (mit .clip-puffer), daneben ein Lager (mit .clip-lager); getrennter Betrieb an."""

    def setUp(self):
        super().setUp()
        self.puffer = self.konfig.wurzel
        self.lager = self.tmp / "lager"
        self.lager.mkdir()
        (self.puffer / ".clip-puffer").touch()
        (self.lager / ".clip-lager").touch()
        self.konfig.daten["lager"]["wurzel"] = str(self.lager)
        # Temp-Ordner liegen auf einem Dateisystem: st_dev-Prüfung umgehen (eigener Test beweist den Abbruch)
        geraete = {self.puffer: 1, self.lager: 2}
        patcher = mock.patch.object(konfig, "_dateisystem", side_effect=lambda p: geraete.get(Path(p), 3))
        patcher.start()
        self.addCleanup(patcher.stop)


# --- Konfig ---------------------------------------------------------------------------

class KonfigGetrennt(MitSpeicher):
    def test_ohne_lager_kein_getrennter_betrieb(self):
        self.assertFalse(self.konfig.getrennt)
        with self.assertRaises(KonfigFehler) as fehler:
            self.konfig.lager_wurzel
        self.assertIn("[lager].wurzel leer", str(fehler.exception))
        for aufruf in (self.konfig.pruefe_lager, self.konfig.pruefe_getrennt):
            with self.assertRaises(KonfigFehler):
                aufruf()
        del self.konfig.daten["lager"]  # auch ganz ohne Abschnitt (alte Konfig)
        self.assertFalse(self.konfig.getrennt)
        self.konfig.daten["lager"] = {"wurzel": "   "}  # nur Leerzeichen zählt als leer
        self.assertFalse(self.konfig.getrennt)

    def test_mit_lager_getrennt(self):
        self.konfig.daten["lager"]["wurzel"] = " /srv/big/clips "
        self.assertTrue(self.konfig.getrennt)
        self.assertEqual(self.konfig.lager_wurzel, Path("/srv/big/clips"))

    def test_lager_erreichbar_fragt_big_host_und_lager_port(self):
        self.konfig.daten["lager"].update(wurzel=str(self.tmp), port=2049)
        with mock.patch("socket.create_connection") as verbindung:
            self.assertTrue(self.konfig.lager_erreichbar())  # ohne Host: nur die Markierung entscheidet
            verbindung.assert_not_called()
            self.konfig.daten["speicher"]["host"] = "speicher-host"
            self.assertTrue(self.konfig.lager_erreichbar())
            self.assertEqual(verbindung.call_args.args[0], ("speicher-host", 2049))  # Rückfall [speicher].host
            self.konfig.daten["big"]["host"] = "pve-big"
            self.assertTrue(self.konfig.lager_erreichbar())
            self.assertEqual(verbindung.call_args.args[0], ("pve-big", 2049))        # [big].host hat Vorrang
            self.assertEqual(verbindung.call_args.kwargs["timeout"], 2)
            verbindung.side_effect = OSError("schläft")
            self.assertFalse(self.konfig.lager_erreichbar())

    def test_lager_erreichbar_echt_ueber_127_0_0_1(self):
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen()
            port = server.getsockname()[1]
            self.konfig.daten["lager"].update(wurzel=str(self.tmp), port=port)
            self.konfig.daten["big"]["host"] = "127.0.0.1"
            self.assertTrue(self.konfig.lager_erreichbar())
        self.assertFalse(self.konfig.lager_erreichbar())  # Port wieder zu


class PruefeLager(MitLager):
    def test_markierung_da(self):
        self.konfig.pruefe_lager()

    def test_markierung_fehlt(self):
        (self.lager / ".clip-lager").unlink()
        with self.assertRaises(SpeicherOffline) as fehler:
            self.konfig.pruefe_lager()
        self.assertIn(".clip-lager", str(fehler.exception))

    def lager_tabu(self, solange=lambda: True):
        """Path.stat im Lager verbieten, solange solange() wahr ist (pve-big schläft: NFS-Mount hinge)."""
        echt_stat = Path.stat

        def stat_ohne_lager(pfad, *a, **kw):
            if solange() and Path(pfad).is_relative_to(self.lager):
                raise AssertionError("Lager angefasst, obwohl pve-big schläft")
            return echt_stat(pfad, *a, **kw)

        return mock.patch.object(Path, "stat", stat_ohne_lager)

    def test_schlafend_mount_nicht_anfassen(self):
        with mock.patch.object(konfig.Konfig, "lager_erreichbar", return_value=False), \
                self.lager_tabu(), self.assertRaises(SpeicherOffline) as fehler:
            self.konfig.pruefe_lager()
        self.assertIn("schläft", str(fehler.exception))

    def test_wartet_auf_erreichbarkeit(self):
        # wach_halten wartet nur auf SSH (22); NFS (2049) kann danach noch fehlen → weiter warten, nicht abbrechen
        antworten = iter([False, False, True])
        wach = []

        def erreichbar() -> bool:
            wach.append(next(antworten))
            return wach[-1]

        uhr = Uhr()
        with mock.patch.object(konfig.Konfig, "lager_erreichbar", side_effect=erreichbar), \
                self.lager_tabu(solange=lambda: not any(wach)), \
                mock.patch("time.monotonic", uhr.monotonic), mock.patch("time.sleep", uhr.sleep):
            self.konfig.pruefe_lager(warten_s=240)
        self.assertEqual(wach, [False, False, True])
        self.assertEqual(uhr.schlaefe, 2)
        self.assertEqual(uhr.jetzt, 10)  # alle 5 s

    def test_nie_erreichbar_wartet_bis_zum_ende(self):
        uhr = Uhr()
        with mock.patch.object(konfig.Konfig, "lager_erreichbar", return_value=False) as erreichbar, \
                self.lager_tabu(), mock.patch("time.monotonic", uhr.monotonic), \
                mock.patch("time.sleep", uhr.sleep), self.assertRaises(SpeicherOffline) as fehler:
            self.konfig.pruefe_lager(warten_s=240)
        self.assertIn("schläft", str(fehler.exception))  # nicht "Markierung fehlt": der Host ist das Problem
        self.assertEqual(uhr.jetzt, 240)                  # erst nach der vollen Wartezeit aufgeben
        self.assertEqual(uhr.schlaefe, 48)
        self.assertEqual(erreichbar.call_count, 49)

    def test_wartet_auf_markierung(self):
        (self.lager / ".clip-lager").unlink()
        uhr = Uhr(bei_schlaf=lambda n: n == 3 and (self.lager / ".clip-lager").touch())  # NFS kommt nach 15 s
        with mock.patch("time.monotonic", uhr.monotonic), mock.patch("time.sleep", uhr.sleep):
            self.konfig.pruefe_lager(warten_s=240)
        self.assertEqual(uhr.schlaefe, 3)
        self.assertEqual(uhr.jetzt, 15)  # alle 5 s

    def test_warten_endet_mit_offline(self):
        (self.lager / ".clip-lager").unlink()
        uhr = Uhr()
        with mock.patch("time.monotonic", uhr.monotonic), mock.patch("time.sleep", uhr.sleep), \
                self.assertRaises(SpeicherOffline):
            self.konfig.pruefe_lager(warten_s=240)
        self.assertEqual(uhr.jetzt, 240)

    def test_ohne_warten_kein_schlaf(self):
        (self.lager / ".clip-lager").unlink()
        with mock.patch("time.sleep") as schlaf, self.assertRaises(SpeicherOffline):
            self.konfig.pruefe_lager()
        schlaf.assert_not_called()


class PruefeGetrennt(MitLager):
    def pruefe_fehler(self, *worte: str, mit_lager: bool = True) -> None:
        with self.assertRaises(KonfigFehler) as fehler:
            self.konfig.pruefe_getrennt(mit_lager=mit_lager)
        for wort in worte:
            self.assertIn(wort, str(fehler.exception))

    def test_alles_richtig(self):
        self.konfig.pruefe_getrennt()
        self.konfig.pruefe_getrennt(mit_lager=False)

    def test_gleiches_dateisystem_bricht_ab(self):
        # die echte Funktion: beide Temp-Ordner liegen wirklich auf demselben Dateisystem
        self.assertEqual(ECHT_DATEISYSTEM(self.puffer), os.stat(self.lager).st_dev)
        with mock.patch.object(konfig, "_dateisystem", ECHT_DATEISYSTEM):
            self.pruefe_fehler("demselben Dateisystem")

    def test_link_auf_das_lager_bricht_ab(self):
        # /srv/clips zeigt versehentlich aufs Lager statt auf den Puffer
        link = self.tmp / "clips-link"
        link.symlink_to(self.lager, target_is_directory=True)
        self.konfig.daten["speicher"]["wurzel"] = str(link)
        self.pruefe_fehler("fehlt .clip-puffer")  # "Puffer" ohne Puffer-Marke
        # selbst mit passend umgelegten Marken fällt auf, dass es derselbe Ordner ist
        (self.lager / ".clip-lager").unlink()
        (self.lager / ".clip-puffer").touch()
        self.pruefe_fehler("derselbe Ordner")

    def test_vertauschte_marken(self):
        (self.puffer / ".clip-lager").touch()
        self.pruefe_fehler("Im Puffer", ".clip-lager", "vertauscht")
        self.pruefe_fehler("vertauscht", mit_lager=False)
        (self.puffer / ".clip-lager").unlink()
        (self.lager / ".clip-puffer").touch()
        self.pruefe_fehler("Im Lager", ".clip-puffer", "vertauscht")
        self.konfig.pruefe_getrennt(mit_lager=False)  # die Puffer-Seite allein ist in Ordnung
        # auch ein kaputter Link als Marke zählt
        (self.lager / ".clip-puffer").unlink()
        (self.lager / ".clip-puffer").symlink_to(self.tmp / "gibt-es-nicht")
        self.pruefe_fehler("vertauscht")

    def test_fehlende_marken_und_ordner(self):
        (self.lager / ".clip-lager").unlink()
        self.pruefe_fehler("Im Lager", "fehlt .clip-lager")
        (self.lager / ".clip-lager").touch()
        (self.puffer / ".clip-puffer").unlink()
        self.pruefe_fehler("Im Puffer", "fehlt .clip-puffer")
        (self.puffer / ".clip-puffer").touch()
        self.konfig.daten["lager"]["wurzel"] = str(self.tmp / "nicht-eingehaengt")
        self.pruefe_fehler("gibt es nicht")
        self.konfig.daten["speicher"]["wurzel"] = str(self.tmp / "kein-puffer")
        self.pruefe_fehler("Puffer", "gibt es nicht", mit_lager=False)

    def test_nur_puffer_seite_fasst_das_lager_nicht_an(self):
        self.konfig.daten["lager"]["wurzel"] = str(self.tmp / "schlafender-mount")
        with mock.patch.object(konfig.Konfig, "lager_erreichbar", side_effect=AssertionError("Lager gefragt")):
            self.konfig.pruefe_getrennt(mit_lager=False)

    def test_schlafendes_lager_offline_statt_haengen(self):
        with mock.patch.object(konfig.Konfig, "lager_erreichbar", return_value=False), \
                mock.patch("os.path.samefile", side_effect=AssertionError("Lager angefasst")), \
                self.assertRaises(SpeicherOffline):
            self.konfig.pruefe_getrennt()

    def test_liest_keine_inhalte(self):
        # Nur stat()/exists: OPEN/READ über NFS hielte pve-big wach
        with mock.patch("builtins.open", side_effect=AssertionError("open")), \
                mock.patch("os.open", side_effect=AssertionError("os.open")), \
                mock.patch("io.open", side_effect=AssertionError("io.open")):
            self.konfig.pruefe_getrennt()
            self.konfig.pruefe_lager()


# --- big.py im getrennten Betrieb ------------------------------------------------------------

class LeerlaufGetrennt(MitLager):
    def test_marken_und_erreichbarkeit_aus_dem_lager(self):
        self.konfig.daten["big"].update(ssh=None, ssh_ziel="")
        self.konfig.daten["speicher"]["wol_mac"] = "aa:bb:cc:dd:ee:ff"
        self.konfig.daten["big"]["host"] = "pve-big"
        erreichbar = mock.patch.object(konfig.Konfig, "lager_erreichbar", return_value=True)
        alt_weg = mock.patch.object(konfig.Konfig, "_host_erreichbar", side_effect=AssertionError("[speicher].host"))
        with erreichbar, alt_weg:
            (self.puffer / ".leerlauf-scharf").touch()               # im Puffer zählt die Marke nicht
            self.assertIsNone(big.merke_leerlauf(self.konfig))
            self.assertIn("nicht gesichert", big.darf_wecken(self.konfig))
            (self.lager / ".leerlauf-scharf").touch()                # im Lager schon
            self.assertTrue(big.merke_leerlauf(self.konfig))
            self.assertIsNone(big.darf_wecken(self.konfig))
            (self.lager / ".leerlauf-scharf").unlink()
            (self.lager / ".leerlauf-probe").touch()
            with self.assertLogs("pipeline", "WARNING"):
                self.assertFalse(big.merke_leerlauf(self.konfig))
            self.assertIn("nicht gesichert", big.darf_wecken(self.konfig))

    def test_eingehaengt_heisst_lager_markierung(self):
        with mock.patch.object(konfig.Konfig, "lager_erreichbar", return_value=True):
            (self.lager / ".clip-lager").unlink()
            self.assertIsNone(big.merke_leerlauf(self.konfig))
            self.assertNotIn("leerlauf_fehlt_seit", big.lies_zustand(self.konfig))  # nicht eingehängt: nichts schließen
            (self.lager / ".clip-lager").touch()
            (self.puffer / ".clip-speicher").unlink()               # die Speicher-Markierung zählt nicht
            self.assertIsNone(big.merke_leerlauf(self.konfig))
            self.assertTrue(big.lies_zustand(self.konfig).get("leerlauf_fehlt_seit"))

    def test_schlafendes_lager_nicht_anfassen(self):
        echt_stat = Path.stat

        def stat_ohne_lager(pfad, *a, **kw):
            if Path(pfad).is_relative_to(self.lager):
                raise AssertionError("Lager angefasst, obwohl pve-big schläft")
            return echt_stat(pfad, *a, **kw)

        big._schreibe_zustand(self.konfig, leerlauf_fehlt_seit=iso(jetzt()))
        with mock.patch.object(konfig.Konfig, "lager_erreichbar", return_value=False), \
                mock.patch.object(Path, "stat", stat_ohne_lager):
            self.assertIsNone(big.merke_leerlauf(self.konfig))
        self.assertIsNone(big.lies_zustand(self.konfig).get("leerlauf_fehlt_seit"))  # Schlafzeit zählt nicht


class HerzschlagGetrennt(MitLager):
    def test_wach_halten_schlaegt_im_lager(self):
        with big.herzschlag(self.konfig, "lager", intervall_s=0.05, lager=True):
            time_sleep(0.2)
            dateien = list((self.lager / ".aktiv").glob("*-lager-*"))
            self.assertEqual(len(dateien), 1)
        self.assertEqual(list((self.lager / ".aktiv").glob("*")), [])  # danach weg
        self.assertFalse((self.puffer / ".aktiv").exists())

    def test_ohne_lager_markierung_kein_schreiben(self):
        (self.lager / ".clip-lager").unlink()
        with big.herzschlag(self.konfig, "lager", intervall_s=0.05, lager=True):
            time_sleep(0.15)
        self.assertFalse((self.lager / ".aktiv").exists())
        self.assertFalse((self.puffer / ".aktiv").exists())

    def test_andere_schritte_schlagen_nicht(self):
        # lernbot/material/cli arbeiten im Puffer – ihr Herzschlag hielte pve-big nur wach
        with mock.patch.object(big, "merke_leerlauf") as blick:
            with big.herzschlag(self.konfig, "lernbot", intervall_s=0.05):
                time_sleep(0.15)
        blick.assert_not_called()
        self.assertFalse((self.lager / ".aktiv").exists())
        self.assertFalse((self.puffer / ".aktiv").exists())

    def test_wach_halten_haelt_mit_herzschlag_im_lager(self):
        gesehen = []
        with mock.patch.object(big, "wach", return_value=True), mock.patch.object(big, "sende_wake_on_lan") as wol:
            with big.wach_halten(self.konfig, "lager", "Lager-Abgleich"):
                for _ in range(100):  # der erste Schlag kommt aus dem Hintergrund-Thread
                    if (self.lager / ".aktiv").is_dir() and list((self.lager / ".aktiv").glob("*")):
                        break
                    time_sleep(0.02)
                gesehen = [p.name for p in (self.lager / ".aktiv").glob("*")]
                self.assertEqual([m.name for m in big.marken(self.konfig)], ["lager"])
        wol.assert_not_called()
        self.assertTrue(any("-lager-" in n for n in gesehen), gesehen)
        self.assertEqual(list((self.lager / ".aktiv").glob("*")), [])
        self.assertFalse((self.puffer / ".aktiv").exists())


class SperreGetrennt(MitLager):
    def test_pipeline_sperre_zaehlt_nicht_lager_sperre_schon(self):
        pipeline = self.konfig.datenbank.with_suffix(".lock")
        lager = big.lager_sperre(self.konfig)
        self.assertEqual(lager.name, "test.lager.lock")
        with fremde_sperre(pipeline, self.tmp / "bereit"):
            self.assertFalse(big.pipeline_beschaeftigt(self.konfig))   # Pipeline arbeitet nur im Puffer
        with fremde_sperre(lager, self.tmp / "bereit"):
            self.assertTrue(big.pipeline_beschaeftigt(self.konfig))    # Abgleich braucht pve-big
        self.assertFalse(big.pipeline_beschaeftigt(self.konfig))

    def test_eigene_lager_sperre_haelt_nicht_an(self):
        with sperre(big.lager_sperre(self.konfig)):
            self.assertFalse(big.pipeline_beschaeftigt(self.konfig))

    def test_waechter_nennt_lager_sperre(self):
        falsch = FalscherBig(self.tmp)
        self.konfig.daten["big"].update(host="pve-big", ssh=[str(falsch.skript)], aus_warten_s=5)
        with mock.patch.object(big, "wach", side_effect=falsch.wach), mock.patch("time.sleep"):
            with fremde_sperre(big.lager_sperre(self.konfig), self.tmp / "bereit"):
                e = big.waechter(self.konfig)
            self.assertFalse(e["aus"])
            self.assertIn("Lager-Sperre", e["grund"])
            with fremde_sperre(self.konfig.datenbank.with_suffix(".lock"), self.tmp / "bereit"):
                self.assertTrue(big.waechter(self.konfig)["aus"])      # Pipeline-Sperre hält pve-big nicht an


class CliGetrennt(MitLager):
    def lauf(self, *argv) -> int:
        with mock.patch("clip_pipeline.cli.lade", return_value=self.konfig), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return cli.main(list(argv))

    def test_kein_herzschlag_um_gesperrte_befehle(self):
        def schritt(con, konfig, sid):
            time_sleep(0.2)  # ein Herzschlag-Thread hätte längst geschlagen
            return {"session": sid}

        with mock.patch.dict("clip_pipeline.verarbeitung.__dict__", {"analyze": schritt}), \
                mock.patch.object(big, "herzschlag", wraps=big.herzschlag) as herz:
            self.assertEqual(self.lauf("analyze", "--session", "s1"), 0)
        herz.assert_not_called()
        self.assertFalse((self.lager / ".aktiv").exists())
        self.assertFalse((self.puffer / ".aktiv").exists())


# --- Ohne [lager]: alles wie bisher ------------------------------------------------------------

class OhneLagerUnveraendert(MitSpeicher):
    def setUp(self):
        super().setUp()
        del self.konfig.daten["lager"]  # alte Konfig ohne Abschnitt

    def test_merke_leerlauf_wie_bisher(self):
        (self.konfig.wurzel / ".leerlauf-scharf").touch()
        with mock.patch.object(konfig.Konfig, "_host_erreichbar", return_value=True) as alt, \
                mock.patch.object(konfig.Konfig, "lager_erreichbar", side_effect=AssertionError("Lager")):
            self.assertTrue(big.merke_leerlauf(self.konfig))
        alt.assert_called()

    def test_herzschlag_im_speicher_auch_mit_lager_schalter(self):
        for schalter in (False, True):  # lager=True (wach_halten) ändert ohne getrennten Betrieb nichts
            with big.herzschlag(self.konfig, "render", intervall_s=0.05, lager=schalter):
                time_sleep(0.15)
                self.assertEqual(len(list((self.konfig.wurzel / ".aktiv").glob("*-render-*"))), 1)
            self.assertEqual(list((self.konfig.wurzel / ".aktiv").glob("*")), [])

    def test_pipeline_sperre_zaehlt_lager_sperre_nicht(self):
        with fremde_sperre(self.konfig.datenbank.with_suffix(".lock"), self.tmp / "bereit"):
            self.assertTrue(big.pipeline_beschaeftigt(self.konfig))
        with fremde_sperre(big.lager_sperre(self.konfig), self.tmp / "bereit"):
            self.assertFalse(big.pipeline_beschaeftigt(self.konfig))

    def test_cli_schlaegt_wie_bisher(self):
        with mock.patch("clip_pipeline.cli.lade", return_value=self.konfig), \
                mock.patch.dict("clip_pipeline.verarbeitung.__dict__", {"analyze": lambda con, k, sid: {}}), \
                mock.patch.object(big, "herzschlag", wraps=big.herzschlag) as herz, \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["analyze", "--session", "s1"]), 0)
        herz.assert_called_once_with(self.konfig, "analyze")


# --- Konfigdatei -------------------------------------------------------------------------------

class Konfigdatei(MitSpeicher):
    def test_ausgelieferte_werte(self):
        daten = tomllib.loads((PROJEKT / "config" / "pipeline.toml").read_text(encoding="utf-8"))
        self.assertEqual(daten["lager"]["wurzel"], "")  # ausgeliefert: kein getrennter Betrieb
        self.assertEqual((daten["lager"]["markierung"], daten["puffer"]["markierung"]), (".clip-lager", ".clip-puffer"))
        self.assertEqual(daten["lager"]["roh"], ["eingang", "replays"])
        self.assertTrue(set(daten["lager"]["roh"]) <= set(daten["lager"]["ordner"]))
        self.assertFalse(daten["puffer"]["freigeben"])  # Löschen im Puffer erst mit Stufe B5
        self.assertEqual(daten["puffer"]["pool_status"], "/srv/big/lvm-status.txt")
        self.assertEqual((daten["telegram"]["leise_von"], daten["telegram"]["leise_bis"]), ("23:00", "08:00"))
        self.assertEqual(daten["big"]["status_gueltig_tage"], 60)

    def test_status_gueltig_tage(self):
        self.konfig.daten["speicher"]["wol_mac"] = "aa:bb:cc:dd:ee:ff"
        self.konfig.daten["big"].update(host="pve-big", ssh=["true"])
        self.konfig.daten["big"]["status_gueltig_tage"] = 60  # wie ausgeliefert
        big._schreibe_zustand(self.konfig, status_ok=iso(jetzt() - timedelta(days=30)))
        self.assertIsNone(big.darf_wecken(self.konfig))
        big._schreibe_zustand(self.konfig, status_ok=iso(jetzt() - timedelta(days=61)))
        self.assertIn("zu lange", big.darf_wecken(self.konfig))
        del self.konfig.daten["big"]["status_gueltig_tage"]  # ohne Eintrag gilt weiter der alte Standard 14
        big._schreibe_zustand(self.konfig, status_ok=iso(jetzt() - timedelta(days=15)))
        self.assertIn("zu lange", big.darf_wecken(self.konfig))

    def test_hilfen_setzen_hosts_zurueck(self):
        self.assertEqual((self.konfig.wert("lager.wurzel"), self.konfig.wert("big.host"), self.konfig.wert("big.ssh_ziel"),
                          self.konfig.wert("speicher.wol_mac"), self.konfig.wert("speicher.host")), ("", "", "", "", ""))
        self.assertFalse(self.konfig.getrennt)


if __name__ == "__main__":
    unittest.main()
