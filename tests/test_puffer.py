"""Morgenprüfung des Puffers (E19): je Thema höchstens eine Meldung am Tag, montags ein Lebenszeichen.

Puffer und Lager sind Temp-Ordner (MitAbgleich aus test_lager). Die Prüfung darf pve-big nie wecken und das Lager
nie anfassen – wach_halten zählt mit, lager_tabu macht jeden Blick ins Lager zum Fehler. Freier Platz (Puffer und
Lager) und Samba sind ersetzt, damit das Ergebnis nicht vom Testrechner abhängt.

Thema freunde (Mehrbenutzer, Stufe 2): Das Freunde-Volume ist ein Temp-Ordner, „eigenes Dateisystem“ per patch; ein
Wächter (Audit-Hook) zählt jeden Blick hinein (öffnen, auflisten) – erlaubt ist nur der freie Platz. Ohne Freunde-Volume
ist die Ausgabe Zeichen für Zeichen die von main d72349f (Vergleich mit der alten puffer.py, wo git sie liefert).
"""

import contextlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import unittest
from collections import namedtuple
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from clip_pipeline import db, lager, puffer
from clip_pipeline.konfig import KonfigFehler
from clip_pipeline.zeit import iso, jetzt, lokal_zu_utc, utc_zu_lokal

from tests.test_lager import MitAbgleich

ECHT_SAMBA = puffer._samba_aktiv
ECHT_EINGEHAENGT = puffer._eingehaengt
Platte = namedtuple("Platte", "total used free")
PROJEKT = Path(__file__).resolve().parents[1]
MAIN = "d72349f"   # letzter Stand vor Stufe 2 – ohne Freunde-Volume muss die Morgenprüfung genau so antworten

# Wächter: Pfade, in die die Prüfung nie hineinschauen darf (Freunde-Volume). Ein Audit-Hook lässt sich nicht wieder
# entfernen – ohne Eintrag in _TABU kehrt er sofort zurück.
_TABU: list[str] = []
_GESEHEN: list[str] = []


def _waechter(ereignis: str, argumente: tuple) -> None:
    if not _TABU or ereignis not in ("open", "os.listdir", "os.scandir") or not argumente:
        return
    pfad = argumente[0]
    if isinstance(pfad, (str, bytes, os.PathLike)):
        pfad = os.fsdecode(os.fspath(pfad))
        if any(pfad == t or pfad.startswith(t + os.sep) for t in _TABU):
            _GESEHEN.append(f"{ereignis} {pfad}")


sys.addaudithook(_waechter)


@contextlib.contextmanager
def nie_hinein(pfad: Path):
    """Zählt jeden Blick in pfad (Datei öffnen, Ordner auflisten) – stat und statvfs sind erlaubt."""
    _GESEHEN.clear()
    _TABU.append(str(pfad))
    try:
        yield _GESEHEN
    finally:
        _TABU.clear()


@lru_cache(maxsize=None)
def puffer_von_main():
    """puffer.py aus main d72349f als eigenes Modul im Paket – None ohne git oder ohne diesen Stand (flacher Klon)."""
    try:
        quelle = subprocess.run(["git", "-C", str(PROJEKT), "show", f"{MAIN}:src/clip_pipeline/puffer.py"],
                                capture_output=True, text=True, timeout=30, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    modul = importlib.util.module_from_spec(importlib.util.spec_from_loader("clip_pipeline._puffer_main", loader=None))
    modul.__package__ = "clip_pipeline"
    exec(compile(quelle, f"puffer.py@{MAIN}", "exec"), modul.__dict__)
    return modul


def _wochentag(ziel: int, ab=None):
    """Zeitpunkt nahe jetzt, dessen Ortsdatum auf den Wochentag ziel fällt (0 = Montag)."""
    ab = ab or jetzt()
    for tage in range(7):
        zeit = ab + timedelta(days=tage)
        if utc_zu_lokal(zeit, "Europe/Berlin").weekday() == ziel:
            return zeit
    raise AssertionError("unmöglich")


class MitPuffer(MitAbgleich):
    def setUp(self):
        super().setUp()
        self.pool_datei = self.tmp / "lvm-status.txt"
        self.konfig.daten["puffer"].update(pool_status=str(self.pool_datei), warnung_frei_gb=20, alarm_frei_gb=8,
                                           pool_warnung_prozent=85, pool_alarm_prozent=90, lager_spaetestens_h=36,
                                           pc_stau_h=24, pc_status_datei="sitzungen/pc-status.json")
        # Freunde-Volume und Rundgang nie vom Testrechner (auf dem Mini gäbe es beides): ab Werk gibt es kein Volume
        self.volume = self.tmp / "clip-benutzer"
        self.rundgang = self.tmp / "lager-freunde.json"
        self.konfig.daten["puffer"].update(freunde_volume=str(self.volume), freunde_lager_status=str(self.rundgang),
                                           freunde_warnung_frei_gb=15, freunde_alarm_frei_gb=5, freunde_lager_tage=8)
        lager_platte = SimpleNamespace(f_frsize=4096, f_bavail=1000 * 10**9 // 4096, f_blocks=4000 * 10**9 // 4096)
        patcher = [mock.patch("shutil.disk_usage", return_value=Platte(500e9, 400e9, 100e9)),
                   mock.patch.object(puffer, "_samba_aktiv", return_value=None),
                   mock.patch("os.statvfs", return_value=lager_platte)]  # echter Abgleich misst sonst den Testrechner
        self.platte, self.samba, _ = (p.start() for p in patcher)
        for p in patcher:
            self.addCleanup(p.stop)
        self.zeit = _wochentag(1)  # Dienstag: kein Lebenszeichen
        # Grundzustand: vor 1 h ein erfolgreicher Abgleich (sonst meldete schon pc-status.json in sitzungen/,
        # die ja auch ins Lager gehört, „noch nie ein Abgleich“)
        self.lauf_eintragen(1)

    def frei(self, gb: float) -> None:
        self.platte.return_value = Platte(500e9, 500e9 - gb * 1e9, gb * 1e9)

    def pool(self, daten: float, meta: float, alter_min: float = 10) -> None:
        self.pool_datei.write_text(f"zeit={iso(self.zeit - timedelta(minutes=alter_min))}\ndata_prozent={daten}\n"
                                   f"meta_prozent={meta}\nroot_frei_gb=40\n", encoding="utf-8")

    def pc(self, *, fehler=0, letzter_fehler=None, aelteste_h=1.0, alter_h=0.1, anzahl=2) -> None:
        bericht = self.zeit - timedelta(hours=alter_h)
        daten = {"zeit_utc": iso(bericht), "rechner": "GAMING-PC", "kopiert": 3, "fehler": fehler,
                 "letzter_fehler": letzter_fehler,
                 "offen": [{"quelle": "nvidia", "anzahl": anzahl, "aelteste_utc": iso(bericht - timedelta(hours=aelteste_h))}]}
        pfad = self.puffer / "sitzungen" / "pc-status.json"
        pfad.parent.mkdir(exist_ok=True)
        pfad.write_text(json.dumps(daten), encoding="utf-8-sig")  # auch mit BOM lesbar

    def lauf_eintragen(self, ende_vor_h: float, **ergebnis) -> None:
        ende = self.zeit - timedelta(hours=ende_vor_h)
        e = {"ok": True, "fehler": 0, "offen": 0, "kopiert": 0} | ergebnis
        self.con.execute("INSERT INTO lager_laeufe (art, start, ende, ergebnis) VALUES ('abgleich', ?, ?, ?)",
                         (iso(ende - timedelta(minutes=5)), iso(ende), json.dumps(e)))

    def pruefe(self, zeit=None) -> list[str]:
        with self.lager_tabu():
            neu = puffer.pruefe_morgens(self.con, self.konfig, zeit or self.zeit)
        self.assertEqual(self.geweckt, [])  # weckt nie
        return neu

    def tag(self, zeit=None) -> str:
        return utc_zu_lokal(zeit or self.zeit, "Europe/Berlin").date().isoformat()

    def text(self, schluessel: str) -> str:
        zeile = self.con.execute("SELECT text FROM meldungen WHERE schluessel = ?", (schluessel,)).fetchone()
        self.assertIsNotNone(zeile, schluessel)
        return zeile[0]


class Morgenpruefung(MitPuffer):
    def test_alles_gut_keine_meldung(self):
        self.pool(50, 10)
        self.pc()
        self.samba.return_value = True
        self.assertEqual(self.pruefe(), [])
        self.assertEqual(self.meldungen(), [])

    def test_ohne_getrennten_betrieb(self):
        self.konfig.daten["lager"]["wurzel"] = ""
        with self.assertRaises(KonfigFehler):
            puffer.pruefe_morgens(self.con, self.konfig)
        code, e = self.lauf("puffer", "pruefen")
        self.assertEqual((code, e["fehler"]), (2, "kein getrennter Betrieb: [lager].wurzel leer"))
        self.assertEqual(self.lauf("puffer", "status")[0], 2)

    def test_je_thema_genau_eine_meldung_am_tag(self):
        self.frei(5)
        self.pool(95, 20)
        self.pc(fehler=1)
        self.samba.return_value = False
        self.con.execute("DELETE FROM lager_laeufe")
        self.datei(self.puffer, "eingang/a.mp4", b"x")  # offen, noch nie ein Abgleich
        tag = self.tag()
        erwartet = [f"puffer:{t}:{tag}" for t in ("lager", "platz", "pool", "pc", "samba")]
        self.assertEqual(self.pruefe(), erwartet)
        self.assertEqual(self.pruefe(), [])  # zweiter Lauf am selben Tag: nichts Neues
        self.assertEqual([m["schluessel"] for m in self.meldungen()], erwartet)
        for schluessel in erwartet:  # freundlich: was ist los, nichts verloren, was tun
            text = self.text(schluessel)
            self.assertIn("Nächster Schritt", text)
            self.assertRegex(text, r"[Nn]ichts verloren")
        # nächster Tag: wieder je eine. Der Pool-Stand ist dann veraltet (älter als 2 h) – still. Der PC-Bericht
        # (24,1 h alt) zählt noch: PC_FRISCH_H = 26 h lässt 2 h Spielraum für den Timer – ein Bericht kurz vor der
        # Prüfung kommt so an höchstens zwei Morgen, aber nie gar nicht
        morgen = self.zeit + timedelta(days=1)
        neu = self.pruefe(morgen)
        self.assertIn(f"puffer:platz:{self.tag(morgen)}", neu)
        self.assertIn(f"puffer:samba:{self.tag(morgen)}", neu)
        self.assertIn(f"puffer:pc:{self.tag(morgen)}", neu)
        self.assertNotIn(f"puffer:pool:{self.tag(morgen)}", neu)
        # übermorgen ist der PC-Bericht älter als 26 h (PC aus): still
        uebermorgen = self.zeit + timedelta(days=2)
        self.assertNotIn(f"puffer:pc:{self.tag(uebermorgen)}", self.pruefe(uebermorgen))

    def test_platz(self):
        self.frei(15)
        self.assertEqual(self.pruefe(), [f"puffer:platz:{self.tag()}"])
        text = self.text(f"puffer:platz:{self.tag()}")
        self.assertIn("15.0 GB frei", text)
        self.assertIn("Warnung unter 20 GB", text)
        self.assertIn("löscht der tägliche Abgleich selbst vom Mini, sobald alles im Lager ist", text)  # B5, ab Werk
        self.konfig.daten["puffer"]["freigeben"] = False
        self.assertIn("automatisch gelöscht wird nichts",
                      puffer.status(self.con, self.konfig, self.zeit)["befunde"]["platz"])
        self.konfig.daten["puffer"]["freigeben"] = True
        self.frei(5)
        self.assertEqual(self.pruefe(), [])  # Alarm am selben Tag: das Thema ist schon gemeldet
        self.assertIn("🚨", puffer.status(self.con, self.konfig, self.zeit)["befunde"]["platz"])
        self.frei(25)
        self.assertNotIn("platz", puffer.status(self.con, self.konfig, self.zeit)["befunde"])

    def test_pool(self):
        self.pool(80, 40)
        self.assertEqual(self.pruefe(), [])
        self.pool(86, 40)
        befund = puffer.status(self.con, self.konfig, self.zeit)["befunde"]["pool"]
        self.assertIn("wird voll", befund)
        self.assertIn("Daten 86 %", befund)
        self.pool(60, 91)  # Metadaten zählen genauso
        befund = puffer.status(self.con, self.konfig, self.zeit)["befunde"]["pool"]
        self.assertIn("🚨", befund)
        self.assertIn("lvs pve/data", befund)
        self.assertEqual(self.pruefe(), [f"puffer:pool:{self.tag()}"])

    def test_pool_grenzen(self):
        """Gemeldet wird ab der Schwelle (≥), knapp darunter nicht – Daten und Metadaten gleich."""
        def befund(daten: float, meta: float) -> str:
            self.pool(daten, meta)
            return puffer.status(self.con, self.konfig, self.zeit)["befunde"].get("pool") or ""

        self.assertEqual(befund(84.9, 84.9), "")
        for daten, meta in ((85, 10), (10, 85), (89.9, 10)):
            self.assertIn("wird voll", befund(daten, meta), (daten, meta))
        for daten, meta in ((90, 10), (10, 90)):
            self.assertIn("fast voll", befund(daten, meta), (daten, meta))

    def test_pool_datei_fehlt_alt_oder_kaputt_still(self):
        self.assertEqual(self.pruefe(), [])  # fehlt
        self.pool(99, 99, alter_min=3 * 60)  # älter als 2 h
        stand = puffer.status(self.con, self.konfig, self.zeit)
        self.assertNotIn("pool", stand["befunde"])
        self.assertIn("übergangen", stand["pool"]["hinweis"])
        self.pool_datei.write_text("data_prozent=abc\n", encoding="utf-8")
        stand = puffer.status(self.con, self.konfig, self.zeit)
        self.assertNotIn("pool", stand["befunde"])
        self.assertEqual(stand["fehler"], [])
        self.konfig.daten["puffer"]["pool_status"] = ""
        self.assertNotIn("pool", puffer.status(self.con, self.konfig, self.zeit)["befunde"])

    def test_lies_pool(self):
        self.pool(87.5, 12.25)
        werte = puffer.lies_pool(self.pool_datei)
        self.assertEqual((werte["data_prozent"], werte["meta_prozent"], werte["root_frei_gb"]), (87.5, 12.25, 40))

    def test_pc_fehler_und_stau(self):
        self.assertEqual(self.pruefe(), [])  # keine pc-status.json: nichts
        self.pc(fehler=2, letzter_fehler="Zugriff verweigert", aelteste_h=30, anzahl=3)
        self.assertEqual(self.pruefe(), [f"puffer:pc:{self.tag()}"])
        text = self.text(f"puffer:pc:{self.tag()}")
        self.assertIn("Gaming-PC (GAMING-PC)", text)
        self.assertIn("2 Datei(en) nicht übertragen – Zugriff verweigert", text)
        self.assertIn("3 Datei(en) warten seit über 24 h", text)
        self.assertIn("die älteste seit 30 h, nvidia", text)
        self.assertIn("liegen noch auf dem PC", text)

    def test_pc_ohne_problem_oder_veraltet_still(self):
        self.pc(aelteste_h=2)
        self.assertNotIn("pc", puffer.status(self.con, self.konfig, self.zeit)["befunde"])
        # Stau wird zur Zeit des Berichts gemessen: ein PC, der seit Tagen aus ist, meldet nicht jeden Morgen
        self.pc(fehler=1, aelteste_h=30, alter_h=40)
        stand = puffer.status(self.con, self.konfig, self.zeit)
        self.assertNotIn("pc", stand["befunde"])
        self.assertIn("schon geprüft", stand["pc"]["hinweis"])
        (self.puffer / "sitzungen" / "pc-status.json").write_text("{kaputt", encoding="utf-8")
        stand = puffer.status(self.con, self.konfig, self.zeit)
        self.assertNotIn("pc", stand["befunde"])
        self.assertIn("unlesbar", stand["pc"]["hinweis"])

    def test_samba(self):
        self.samba.return_value = None  # nicht installiert: nichts
        self.assertEqual(self.pruefe(), [])
        self.samba.return_value = True
        self.assertEqual(self.pruefe(), [])
        self.samba.return_value = False
        self.assertEqual(self.pruefe(), [f"puffer:samba:{self.tag()}"])
        self.assertIn("systemctl restart smbd", self.text(f"puffer:samba:{self.tag()}"))

    def test_samba_nur_mit_systemctl_und_smbd(self):
        installiert = {"systemctl": "/usr/bin/systemctl"}
        with mock.patch.object(shutil, "which", lambda name, path=None: installiert.get(name)), \
                mock.patch.object(subprocess, "run") as run:
            self.assertIsNone(ECHT_SAMBA())  # systemd ja, Samba nicht installiert: nichts prüfen
            run.assert_not_called()
            installiert["smbd"] = "/usr/sbin/smbd"
            run.return_value = subprocess.CompletedProcess([], 3)
            self.assertIs(ECHT_SAMBA(), False)
            self.assertEqual(run.call_args.args[0], ["systemctl", "is-active", "--quiet", "smbd"])
            run.return_value = subprocess.CompletedProcess([], 0)
            self.assertIs(ECHT_SAMBA(), True)
        with mock.patch.object(shutil, "which", return_value=None):
            self.assertIsNone(ECHT_SAMBA())  # kein systemd (z. B. Windows)


class LagerThema(MitPuffer):
    def test_offen_und_lange_kein_erfolg(self):
        self.datei(self.puffer, "eingang/a.mp4", b"x" * 1000)
        self.lauf_eintragen(10)  # vor 10 h erfolgreich: noch keine Meldung
        self.assertEqual(self.pruefe(), [])
        self.con.execute("DELETE FROM lager_laeufe")
        self.lauf_eintragen(40)
        self.lauf_eintragen(3, ok=False, abbruch="SpeicherOffline: Lager-Host pve-big schläft")
        self.assertEqual(self.pruefe(), [f"puffer:lager:{self.tag()}"])
        text = self.text(f"puffer:lager:{self.tag()}")
        self.assertIn("1 Datei(en)", text)
        self.assertIn("seit 40 h kein erfolgreicher Abgleich", text)
        self.assertIn("Letzter Versuch", text)
        self.assertIn("pve-big schläft", text)
        self.assertIn("Nichts verloren", text)

    def test_einzelner_weck_fehlschlag_still(self):
        """Ein Abbruch (pve-big nicht geweckt) ist meist vorübergehend: keine Meldung, solange der letzte Erfolg
        jünger als lager_spaetestens_h ist – sonst käme nach jedem schlechten Abgleich ein Fehlalarm."""
        self.datei(self.puffer, "eingang/a.mp4", b"x" * 1000)
        self.con.execute("DELETE FROM lager_laeufe")
        self.lauf_eintragen(20)
        self.lauf_eintragen(3, ok=False, abbruch="SpeicherOffline: Lager-Host pve-big schläft")
        self.assertEqual(self.pruefe(), [])

    def test_nachtruhe_zaehlt_nicht_als_erfolg(self):
        """Ein Abgleich, der in der Nachtruhe nicht wecken durfte, hat nichts ins Lager gebracht. Hält das an (z. B.
        der Timer steht versehentlich wieder nachts), meldet die Morgenprüfung – mit dem Grund."""
        self.datei(self.puffer, "eingang/a.mp4", b"x" * 1000)
        self.con.execute("DELETE FROM lager_laeufe")
        self.lauf_eintragen(40)
        self.lauf_eintragen(1, nachtruhe=True, offen=1)
        self.assertEqual(self.pruefe(), [f"puffer:lager:{self.tag()}"])
        text = self.text(f"puffer:lager:{self.tag()}")
        self.assertIn("seit 40 h kein erfolgreicher Abgleich", text)
        self.assertIn("in der Nachtruhe", text)

    def test_nichts_offen_keine_meldung(self):
        self.lauf_eintragen(100)  # lange her, aber nichts wartet
        self.assertEqual(self.pruefe(), [])

    def test_noch_nie_ein_abgleich(self):
        self.con.execute("DELETE FROM lager_laeufe")
        self.datei(self.puffer, "replays/r.replay", b"r")
        self.assertEqual(self.pruefe(), [f"puffer:lager:{self.tag()}"])
        self.assertIn("noch nie ein Abgleich", self.text(f"puffer:lager:{self.tag()}"))

    def test_letzter_lauf_mit_fehlern_nicht_doppelt(self):
        # In UTC noch Vortag: für die Wiederholung zählt der konfigurierte Ortstag.
        self.zeit = lokal_zu_utc(datetime(2026, 9, 30, 0, 30), "Europe/Berlin")
        self.datei(self.puffer, "eingang/b.mp4", b"y")
        with mock.patch.object(lager, "kopiere_geprueft", side_effect=PermissionError("nein")), \
                mock.patch.object(lager, "jetzt", return_value=self.zeit):
            self.assertEqual(self.lauf("lager", "abgleich")[0], 1)
        self.geweckt.clear()
        lauf = self.con.execute("SELECT id FROM lager_laeufe ORDER BY id DESC LIMIT 1").fetchone()[0]
        ende = f"uebertragung:lager:{lauf}:ende"
        self.assertTrue(self.text(ende))  # der Abgleich hat sich schon selbst gemeldet
        self.assertEqual(self.pruefe(), [])  # … die Morgenprüfung wiederholt es heute nicht
        morgen = self.zeit + timedelta(days=1)
        self.assertEqual(self.pruefe(morgen), [f"puffer:lager:{self.tag(morgen)}"])

        # Ohne Abschluss (Start allein reicht nicht) bleibt die Warnung notwendig.
        self.con.execute("DELETE FROM meldungen WHERE schluessel = ?", (ende,))
        self.assertTrue(self.text(f"uebertragung:lager:{lauf}:start"))
        self.assertEqual(self.pruefe(), [f"puffer:lager:{self.tag()}"])
        self.assertIn("nicht ins Lager gekommen", self.text(f"puffer:lager:{self.tag()}"))
        self.con.execute("DELETE FROM meldungen WHERE schluessel = ?", (f"puffer:lager:{self.tag()}",))
        db.meldung(self.con, ende, "Abschluss")
        # Ein neuerer Fehlerlauf ohne eigene Abschlussmeldung wird nicht vom vorherigen verdeckt.
        self.lauf_eintragen(0, ok=False, fehler=2)
        self.assertEqual(self.pruefe(), [f"puffer:lager:{self.tag()}"])
        self.con.execute("DELETE FROM meldungen WHERE schluessel = ?", (f"puffer:lager:{self.tag()}",))
        db.meldung(self.con, f"lager:{self.tag()}", "Alte Tageswarnung")
        self.assertEqual(self.pruefe(), [])  # vorhandene Meldungen älterer Versionen gelten weiter

    def test_puffer_pruefung_fehlgeschlagen(self):
        (self.puffer / ".clip-puffer").unlink()
        self.assertEqual(self.pruefe(), [f"puffer:lager:{self.tag()}"])
        text = self.text(f"puffer:lager:{self.tag()}")
        self.assertIn("Puffer-Prüfung fehlgeschlagen", text)
        self.assertIn("fehlt .clip-puffer", text)

    def test_konflikt_meldung_verschluckt_puffer_pruefung_nicht(self):
        """Hat der Abgleich heute nur Rohdaten-Konflikte gemeldet, kommt eine danach fehlgeschlagene Puffer-Prüfung
        trotzdem gleich – nicht erst mit dem nächsten Abgleich."""
        self.lauf_eintragen(0)
        lauf = self.con.execute("SELECT id FROM lager_laeufe ORDER BY id DESC LIMIT 1").fetchone()[0]
        (self.puffer / ".clip-puffer").unlink()
        for schluessel in (f"lager:{self.tag()}", f"uebertragung:lager:{lauf}:ende"):
            with self.subTest(schluessel=schluessel):
                self.con.execute("DELETE FROM meldungen")
                db.meldung(self.con, schluessel, "🗄️ Lager-Abgleich: Rohdaten-Konflikt als neue Fassung gesichert")
                self.assertEqual(self.pruefe(), [f"puffer:lager:{self.tag()}"])
                self.assertIn("Puffer-Prüfung fehlgeschlagen", self.text(f"puffer:lager:{self.tag()}"))

    def test_puffer_pruefung_nicht_doppelt(self):
        """Ist der Abgleich heute an derselben Puffer-Prüfung gescheitert, hat er es schon selbst gemeldet."""
        self.zeit = jetzt()  # der echte Abgleich meldet unter dem heutigen Datum
        (self.puffer / ".clip-puffer").unlink()
        self.assertEqual(self.lauf("lager", "abgleich")[0], 2)
        self.assertIn("nichts kopiert", self.text(f"lager:{self.tag()}"))
        ohne_woche = lambda neu: [s for s in neu if not s.startswith("puffer:woche:")]  # heute evtl. Montag
        self.assertEqual(ohne_woche(self.pruefe()), [])


class LagerPlatz(MitPuffer):
    """Nie löschen, aber warnen – auch fürs Lager auf pve-big. Gemessen wird beim Abgleich; die Prüfung liest nur
    die Tabelle (lager_tabu in pruefe(): kein stat/statvfs im Lager)."""

    def messen(self, frei: float, gesamt: float = 4000, vor_h: float = 1) -> None:
        self.lauf_eintragen(vor_h, lager_frei_gb=frei, lager_gesamt_gb=gesamt)

    def befund(self) -> str:
        return puffer.status(self.con, self.konfig, self.zeit)["befunde"].get("lager_platz") or ""

    def test_ohne_messung_still(self):
        self.assertEqual(self.pruefe(), [])  # Grundzustand: ein Abgleich ohne Messung
        stand = puffer.status(self.con, self.konfig, self.zeit)
        self.assertNotIn("lager_platz", stand["befunde"])
        self.assertIn("noch nicht gemessen", stand["lager_platz"]["hinweis"])
        self.con.execute("DELETE FROM lager_laeufe")  # auch ganz ohne Lauf
        self.assertEqual(self.befund(), "")

    def test_grenzen(self):
        """Gewarnt wird UNTER der Schwelle: genau 200 GB frei ist noch gut, genau 50 GB noch kein Alarm."""
        for frei, erwartet in ((1000, ""), (200, ""), (199.99, "wird knapp"), (50, "wird knapp"), (49.99, "fast voll"),
                               (0, "fast voll")):
            self.messen(frei)
            text = self.befund()
            if erwartet:
                self.assertIn(erwartet, text, frei)
            else:
                self.assertEqual(text, "", frei)
        self.konfig.daten["puffer"].update(lager_warnung_frei_gb=500, lager_alarm_frei_gb=100)  # konfigurierbar
        self.messen(300)
        self.assertIn("wird knapp", self.befund())
        self.assertIn("Warnung unter 500 GB", self.befund())

    def test_text_freundlich_mit_naechstem_schritt(self):
        self.messen(120, gesamt=3600, vor_h=26)
        self.assertEqual(self.pruefe(), [f"puffer:lager_platz:{self.tag()}"])
        text = self.text(f"puffer:lager_platz:{self.tag()}")
        self.assertIn("🗄️ Lager auf pve-big wird knapp: noch 120 GB frei von 3600 GB (Warnung unter 200 GB", text)
        wann = utc_zu_lokal(self.zeit - timedelta(hours=26), "Europe/Berlin")
        self.assertIn(f"gemessen beim Abgleich am {wann:%d.%m. %H:%M}", text)
        self.assertIn("Nichts verloren, nichts gelöscht", text)
        self.assertIn("Nächster Schritt: bitte Platz auf pve-big schaffen oder die Platte erweitern", text)
        self.messen(20)
        self.assertIn("🚨 Lager auf pve-big fast voll: nur noch 20 GB frei", self.befund())
        self.assertIn("Alarm unter 50 GB", self.befund())

    def test_eine_meldung_am_tag(self):
        self.messen(150)
        self.assertEqual(self.pruefe(), [f"puffer:lager_platz:{self.tag()}"])
        self.assertEqual(self.pruefe(), [])  # zweiter Lauf am selben Tag
        self.messen(10)  # auch der Alarm kommt am selben Tag nicht noch einmal
        self.assertEqual(self.pruefe(), [])
        morgen = self.zeit + timedelta(days=1)
        self.assertEqual(self.pruefe(morgen), [f"puffer:lager_platz:{self.tag(morgen)}"])
        self.assertIn("fast voll", self.text(f"puffer:lager_platz:{self.tag(morgen)}"))

    def test_letzte_messung_zaehlt_auch_nach_laeufen_ohne_wecken(self):
        self.messen(30, vor_h=30)
        self.lauf_eintragen(2)  # danach nur Läufe ohne pve-big (nichts Neues): keine Messung
        self.assertIn("fast voll", self.befund())
        self.messen(900, vor_h=0.5)  # aufgeräumt, neu gemessen: still
        self.assertEqual(self.befund(), "")

    def test_lebenszeichen_zeigt_den_platz_im_lager(self):
        self.messen(1840)
        montag = _wochentag(0)
        [schluessel] = self.pruefe(montag)
        self.assertRegex(self.text(schluessel), r"Lager: 1840 GB frei.*, letzter Abgleich .+ ok · 0 offen")


class Lebenszeichen(MitPuffer):
    def test_montags_einmal_pro_woche(self):
        montag = _wochentag(0)
        jahr, woche, _ = utc_zu_lokal(montag, "Europe/Berlin").date().isocalendar()
        schluessel = f"puffer:woche:{jahr}-W{woche:02d}"
        self.assertEqual(self.pruefe(montag), [schluessel])
        self.assertEqual(self.pruefe(montag + timedelta(hours=2)), [])  # zweiter Lauf: nicht doppelt
        text = self.text(schluessel)
        self.assertRegex(text, r"Puffer 100 GB frei · Lager: letzter Abgleich .+ ok · 0 offen")
        self.assertIn("alles in Ordnung", text)
        self.assertEqual(self.pruefe(montag + timedelta(days=1)), [])  # Dienstag: nichts
        naechste = self.pruefe(montag + timedelta(days=7))
        self.assertEqual(len(naechste), 1)
        self.assertNotEqual(naechste[0], schluessel)

    def test_lebenszeichen_auch_mit_problemen(self):
        self.frei(5)
        neu = self.pruefe(_wochentag(0))
        self.assertEqual(len(neu), 2)
        self.assertTrue(neu[1].startswith("puffer:woche:"))


class Cli(MitPuffer):
    def test_status_zeigt_nur(self):
        self.frei(5)
        with self.lager_tabu():
            code, stand = self.lauf("puffer", "status")
        self.assertEqual(code, 0, stand)
        self.assertIn("platz", stand["befunde"])
        self.assertEqual(stand["platz"]["frei_gb"], 5.0)
        self.assertEqual(stand["lager"]["pruefung"], "ok")
        self.assertRegex(stand["zeile"], r"Lager: letzter Abgleich .+ ok · 0 offen$")
        self.assertEqual(self.meldungen(), [])  # status legt keine Meldung an
        self.assertEqual(self.geweckt, [])

    def test_pruefen_legt_meldungen_an(self):
        self.frei(5)
        with self.lager_tabu():
            code, e = self.lauf("puffer", "pruefen")
        self.assertEqual(code, 0, e)
        self.assertTrue(any(s.startswith("puffer:platz:") for s in e["neu"]))
        self.assertIn("platz", e["befunde"])
        self.assertEqual(self.lauf("puffer", "pruefen")[1]["neu"], [])  # zweiter Lauf: nichts Neues
        self.assertEqual(self.geweckt, [])

    def test_kaputtes_thema_verschluckt_die_anderen_nicht(self):
        self.frei(5)
        with mock.patch.object(puffer, "_pool", side_effect=RuntimeError("Programmfehler")):
            code, e = self.lauf("puffer", "pruefen")
        self.assertEqual(code, 1)
        self.assertEqual(e["fehler"], ["pool"])
        self.assertIn("platz", e["befunde"])
        self.assertIn("ließ sich nicht prüfen", e["befunde"]["pool"])
        self.assertTrue(any(s.startswith("puffer:pool:") for s in e["neu"]))


class Meldungsweg(MitPuffer):
    def test_meldungen_landen_beim_clip_bot(self):
        self.frei(5)
        self.pruefe()
        zeilen = self.con.execute("SELECT schluessel, gesendet FROM meldungen").fetchall()
        self.assertEqual([(z[0][:13], z[1]) for z in zeilen], [("puffer:platz:", None)])
        self.assertFalse(db.meldung(self.con, zeilen[0][0], "doppelt"))


class Freunde(MitPuffer):
    """Thema freunde (Mehrbenutzer Stufe 2, M131/M132): Platz auf dem Freunde-Volume und das Lager der Freunde aus der
    Zusammenfassung des Rundgangs – nur bei Florian mit eigenem Freunde-Volume. Hineingeschaut wird nie: Jeder Aufruf
    von pruefe() läuft unter dem Wächter, im Volume liegt Inhalt eines Freundes."""

    def setUp(self):
        super().setUp()
        (self.volume / "max" / "daten").mkdir(parents=True)
        (self.volume / "max" / "instanz.toml").write_text("[instanz]\nlager = true\n", encoding="utf-8")
        self.volume_frei = 60.0
        self.platte.side_effect = lambda pfad: (Platte(100e9, 100e9 - self.volume_frei * 1e9, self.volume_frei * 1e9)
                                                if Path(pfad) == self.volume else self.platte.return_value)
        patcher = mock.patch.object(puffer, "_eingehaengt", side_effect=lambda pfad: Path(pfad) == self.volume)
        self.eingehaengt = patcher.start()
        self.addCleanup(patcher.stop)

    def vor(self, **dauer) -> str:
        return iso(self.zeit - timedelta(**dauer))

    def wann(self, **dauer) -> str:
        return f"{utc_zu_lokal(self.zeit - timedelta(**dauer), 'Europe/Berlin'):%d.%m. %H:%M}"

    def rundgang_schreiben(self, mit_lager: dict, freunde: dict) -> None:
        self.rundgang.write_text(json.dumps({
            "lauf": {"start": self.vor(hours=2), "ende": self.vor(hours=1), "ergebnis": "fertig"},
            "mit_lager": mit_lager, "freunde": freunde}), encoding="utf-8")

    def pruefe(self, zeit=None) -> list[str]:
        with nie_hinein(self.volume) as gesehen:
            neu = super().pruefe(zeit)
        self.assertEqual(gesehen, [])
        return neu

    def test_genug_frei_keine_meldung(self):
        self.rundgang_schreiben({"max": {"seit": self.vor(days=20), "stillgelegt": False}},
                                {"max": {"ende": self.vor(days=1), "ok": True, "exit": 0, "zuletzt_ok": self.vor(days=1)}})
        self.assertEqual(self.pruefe(), [])
        with nie_hinein(self.volume) as gesehen:
            stand = puffer.status(self.con, self.konfig, self.zeit)
        self.assertEqual(gesehen, [])
        self.assertNotIn("freunde", stand["befunde"])
        self.assertEqual((stand["freunde"]["frei_gb"], stand["freunde"]["lager"]["mit_lager"]), (60.0, ["max"]))
        with nie_hinein(self.volume) as gesehen:  # der Wächter schlägt an, wenn doch jemand hineinschaut
            os.listdir(self.volume)
            (self.volume / "max" / "instanz.toml").read_text(encoding="utf-8")
        self.assertEqual(len(gesehen), 2)

    def test_knapp_genau_eine_meldung_am_tag(self):
        self.volume_frei = 12
        schluessel = f"puffer:freunde:{self.tag()}"
        self.assertEqual(self.pruefe(), [schluessel])
        self.assertEqual(self.pruefe(), [])  # zweiter Lauf am selben Tag
        text = self.text(schluessel)
        self.assertIn("👥 Speicher der Freunde wird knapp: noch 12.0 GB frei von 100 GB (Warnung unter 15 GB).", text)
        self.assertIn("Nichts verloren – wird es zu knapp, holt der Mini nichts mehr ab", text)
        self.assertIn("Nächster Schritt: auf pve-mini lvs pve/data ansehen", text)
        self.assertIn(f"pct resize 102 mp2 +50G (docs/MEHRBENUTZER.md); wer wie viel belegt: im CT du -sh {self.volume}/*",
                      text)
        self.volume_frei = 4
        self.assertEqual(self.pruefe(), [])  # Alarm am selben Tag: das Thema ist schon gemeldet
        morgen = self.zeit + timedelta(days=1)
        self.assertEqual(self.pruefe(morgen), [f"puffer:freunde:{self.tag(morgen)}"])
        self.assertIn("🚨 Speicher der Freunde fast voll: nur noch 4.0 GB frei von 100 GB (Alarm unter 5 GB).",
                      self.text(f"puffer:freunde:{self.tag(morgen)}"))

    def test_rundgang_eine_zeile_je_freund(self):
        """Eine Zeile: frischer Fehler (max), 8 Tage ohne guten Lauf (bob: Exit 3 zählt nur hier; ute: noch nie, Bezug
        „seit“). Keine: kai (Fehler älter als 24 h, zuletzt gut vor 7 Tagen), eva (gut), still (stillgelegt), alt (hat
        kein Lager mehr), ein Name, der nicht dem Muster folgt."""
        aktiv = lambda tage: {"seit": self.vor(days=tage), "stillgelegt": False}  # noqa: E731
        self.rundgang_schreiben(
            {"max": aktiv(20), "ute": aktiv(9), "bob": aktiv(30), "kai": aktiv(30), "eva": aktiv(30),
             "still": {"seit": self.vor(days=40), "stillgelegt": True}, "Böse;x": aktiv(30)},
            {"max": {"ende": self.vor(hours=2), "ok": False, "rc": 0, "exit": 2, "zuletzt_ok": self.vor(days=3),
                     "hinweis": "Lager ist kein NFS\x1b[31m"},
             "bob": {"ende": self.vor(hours=2), "ok": False, "exit": 3, "zuletzt_ok": self.vor(days=9)},
             "kai": {"ende": self.vor(days=2), "ok": False, "exit": 2, "zuletzt_ok": self.vor(days=7)},
             "eva": {"ende": self.vor(days=1), "ok": True, "exit": 0, "zuletzt_ok": self.vor(days=1)},
             "still": {"ende": self.vor(days=30), "ok": False, "exit": 2},
             "alt": {"ende": self.vor(hours=1), "ok": False, "exit": 2},
             "Böse;x": {"ende": self.vor(hours=1), "ok": False, "exit": 2}})
        schluessel = f"puffer:freunde:{self.tag()}"
        self.assertEqual(self.pruefe(), [schluessel])
        text = self.text(schluessel)
        self.assertEqual([z.split(":")[0] for z in text.splitlines() if z.startswith("🗄️")],
                         ["🗄️ Lager von bob", "🗄️ Lager von max", "🗄️ Lager von ute"])
        self.assertIn(f"🗄️ Lager von max: letzter Lauf am {self.wann(hours=2)} ging nicht (Exit 2, zuletzt gesichert am "
                      f"{self.wann(days=3)}) – Lager ist kein NFS[31m.", text)
        self.assertIn(f"🗄️ Lager von bob: seit 9 Tagen nicht gesichert (zuletzt gesichert am {self.wann(days=9)}).", text)
        self.assertIn("🗄️ Lager von ute: seit 9 Tagen mit Lager dabei, aber seitdem nicht gesichert (noch nie gesichert).",
                      text)
        self.assertIn(f"Letzter Rundgang der Freunde am {self.wann(hours=1)}: fertig.", text)
        self.assertIn("Nichts verloren – ohne Lager bleiben ihre Aufnahmen im Puffer des Freundes", text)
        self.assertIn("Nächster Schritt: im CT bash /opt/clip-pipeline/deploy/benutzer/benutzer-pruefen.sh bob (ebenso für "
                      "max, ute) (Abschnitt Lager), dann journalctl -u clip-freund-lager@bob -n 50", text)
        self.assertNotIn("\x1b", text)
        for name in ("kai", "eva", "still", "alt", "Böse"):
            self.assertNotIn(f"Lager von {name}", text)
        self.assertNotIn("Speicher der Freunde", text)  # genug Platz: nur die Lager-Zeilen

    def test_ohne_freunde_volume_wie_bisher(self):
        """Ohne Freunde-Volume (fehlt, gleiches Gerät wie der Ordner darüber, Schlüssel leer) und in der Instanz eines
        Freundes gibt es das Thema nicht: Stand und Meldungen sind Zeichen für Zeichen die einer Konfig ohne die neuen
        Schlüssel – obwohl alles anschlägt (Florians Themen, Volume fast voll, Fehler im Rundgang)."""
        self.volume_frei = 1
        self.rundgang_schreiben({"max": {"seit": self.vor(days=30), "stillgelegt": False}},
                                {"max": {"ende": self.vor(hours=1), "ok": False, "exit": 2}})
        self.frei(5)
        self.pool(95, 20)
        self.pc(fehler=1)
        self.samba.return_value = False
        self.con.execute("DELETE FROM lager_laeufe")
        self.datei(self.puffer, "eingang/a.mp4", b"x")
        neu_werte = dict(self.konfig.daten["puffer"])
        alt_werte = {k: v for k, v in neu_werte.items() if not k.startswith("freunde_")}

        def ausgabe(werte: dict) -> tuple:
            self.konfig.daten["puffer"] = dict(werte)
            self.con.execute("DELETE FROM meldungen")
            with nie_hinein(self.volume) as gesehen:
                stand = puffer.status(self.con, self.konfig, self.zeit)
                puffer.pruefe_morgens(self.con, self.konfig, self.zeit)
            self.assertEqual(gesehen, [])
            return json.dumps(stand, ensure_ascii=False), [tuple(m) for m in self.meldungen()]

        vorher = ausgabe(alt_werte)
        self.assertEqual(set(json.loads(vorher[0])), {"getrennt", "befunde", "fehler", "zeile", *puffer.THEMEN})
        self.assertEqual(len(vorher[1]), 5)
        self.eingehaengt.side_effect = ECHT_EINGEHAENGT  # echt: ein Temp-Ordner ist kein eigenes Dateisystem
        for fall, volume in (("gleiches Gerät", str(self.volume)), ("fehlt", str(self.tmp / "fehlt")), ("leer", "")):
            with self.subTest(fall):
                self.assertEqual(ausgabe({**neu_werte, "freunde_volume": volume}), vorher)
        self.eingehaengt.side_effect = lambda pfad: True
        self.eingehaengt.reset_mock()
        self.konfig.daten["instanz"] = {"wurzel": str(self.tmp / "inst"), "name": "max"}
        self.assertNotIn("freunde", ausgabe(neu_werte)[0])  # die Instanz eines Freundes prüft nie die anderen
        self.eingehaengt.assert_not_called()
        del self.konfig.daten["instanz"]
        stand, meldungen = ausgabe(neu_werte)  # Gegenprobe: mit Freunde-Volume schlägt das Thema an
        self.assertIn("puffer:freunde:", " ".join(m[0] for m in meldungen))
        self.assertIn("Lager von max", json.loads(stand)["befunde"]["freunde"])

    def test_wie_main_zeichen_fuer_zeichen(self):
        """Ohne Freunde-Volume antwortet die neue puffer.py wie die aus main d72349f – Stand und Meldungen, an einem
        Dienstag und an einem Montag (Lebenszeichen), einmal mit allem gut und einmal mit allem schlecht."""
        alt = puffer_von_main()
        if alt is None:
            self.skipTest(f"puffer.py aus main {MAIN} nicht lesbar (kein git oder flacher Klon)")
        self.eingehaengt.side_effect = ECHT_EINGEHAENGT
        self.volume_frei = 1
        self.rundgang_schreiben({"max": {"seit": self.vor(days=30), "stillgelegt": False}},
                                {"max": {"ende": self.vor(hours=1), "ok": False, "exit": 2}})

        def vergleiche() -> None:
            for zeit in (self.zeit, _wochentag(0, self.zeit)):
                ergebnisse = []
                for modul in (alt, puffer):
                    self.con.execute("DELETE FROM meldungen")
                    with mock.patch.object(alt, "_samba_aktiv", self.samba), nie_hinein(self.volume) as gesehen:
                        stand = modul.status(self.con, self.konfig, zeit)
                        neu = modul.melde(self.con, self.konfig, stand, zeit)
                    self.assertEqual(gesehen, [])
                    ergebnisse.append((json.dumps(stand, ensure_ascii=False), neu, [tuple(m) for m in self.meldungen()]))
                self.assertEqual(ergebnisse[1], ergebnisse[0])

        self.pool(50, 10)
        self.pc()
        self.samba.return_value = True
        vergleiche()
        self.frei(5)
        self.pool(95, 20)
        self.pc(fehler=1)
        self.samba.return_value = False
        self.con.execute("DELETE FROM lager_laeufe")
        self.datei(self.puffer, "eingang/a.mp4", b"x")
        vergleiche()


if __name__ == "__main__":
    unittest.main()
