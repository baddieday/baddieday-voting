"""Morgenprüfung des Puffers (E19): je Thema höchstens eine Meldung am Tag, montags ein Lebenszeichen.

Puffer und Lager sind Temp-Ordner (MitAbgleich aus test_lager). Die Prüfung darf pve-big nie wecken und das Lager
nie anfassen – wach_halten zählt mit, lager_tabu macht jeden Blick ins Lager zum Fehler. Freier Platz und Samba
sind ersetzt, damit das Ergebnis nicht vom Testrechner abhängt.
"""

import json
import shutil
import subprocess
import unittest
from collections import namedtuple
from datetime import timedelta
from unittest import mock

from clip_pipeline import db, lager, puffer
from clip_pipeline.konfig import KonfigFehler
from clip_pipeline.zeit import iso, jetzt, utc_zu_lokal

from tests.test_lager import MitAbgleich

ECHT_SAMBA = puffer._samba_aktiv
Platte = namedtuple("Platte", "total used free")


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
        patcher = [mock.patch("shutil.disk_usage", return_value=Platte(500e9, 400e9, 100e9)),
                   mock.patch.object(puffer, "_samba_aktiv", return_value=None)]
        self.platte, self.samba = (p.start() for p in patcher)
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
        self.assertIn("automatisch gelöscht wird noch nichts", text)
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
        jünger als lager_spaetestens_h ist – sonst käme nach jeder schlechten Nacht ein Fehlalarm."""
        self.datei(self.puffer, "eingang/a.mp4", b"x" * 1000)
        self.con.execute("DELETE FROM lager_laeufe")
        self.lauf_eintragen(20)
        self.lauf_eintragen(3, ok=False, abbruch="SpeicherOffline: Lager-Host pve-big schläft")
        self.assertEqual(self.pruefe(), [])

    def test_nichts_offen_keine_meldung(self):
        self.lauf_eintragen(100)  # lange her, aber nichts wartet
        self.assertEqual(self.pruefe(), [])

    def test_noch_nie_ein_abgleich(self):
        self.con.execute("DELETE FROM lager_laeufe")
        self.datei(self.puffer, "replays/r.replay", b"r")
        self.assertEqual(self.pruefe(), [f"puffer:lager:{self.tag()}"])
        self.assertIn("noch nie ein Abgleich", self.text(f"puffer:lager:{self.tag()}"))

    def test_letzter_lauf_mit_fehlern_nicht_doppelt(self):
        self.zeit = jetzt()  # der echte Abgleich meldet unter dem heutigen Datum
        self.datei(self.puffer, "eingang/b.mp4", b"y")
        with mock.patch.object(lager, "kopiere_geprueft", side_effect=PermissionError("nein")):
            self.assertEqual(self.lauf("lager", "abgleich")[0], 1)
        self.geweckt.clear()
        self.assertTrue(self.text(f"lager:{self.tag()}"))  # der Abgleich hat sich schon selbst gemeldet
        ohne_woche = lambda neu: [s for s in neu if not s.startswith("puffer:woche:")]  # heute evtl. Montag
        self.assertEqual(ohne_woche(self.pruefe()), [])  # … die Morgenprüfung wiederholt es nicht
        self.con.execute("DELETE FROM meldungen")
        self.assertEqual(ohne_woche(self.pruefe()), [f"puffer:lager:{self.tag()}"])
        self.assertIn("nicht ins Lager gekommen", self.text(f"puffer:lager:{self.tag()}"))

    def test_puffer_pruefung_fehlgeschlagen(self):
        (self.puffer / ".clip-puffer").unlink()
        self.assertEqual(self.pruefe(), [f"puffer:lager:{self.tag()}"])
        text = self.text(f"puffer:lager:{self.tag()}")
        self.assertIn("Puffer-Prüfung fehlgeschlagen", text)
        self.assertIn("fehlt .clip-puffer", text)

    def test_konflikt_meldung_verschluckt_puffer_pruefung_nicht(self):
        """Hat der Abgleich heute nur Rohdaten-Konflikte gemeldet, kommt eine danach fehlgeschlagene Puffer-Prüfung
        trotzdem gleich – nicht erst mit dem nächsten Abgleich."""
        db.meldung(self.con, f"lager:{self.tag()}", "🗄️ Lager-Abgleich: 1 Rohdatei(en) lagen im Lager schon …")
        (self.puffer / ".clip-puffer").unlink()
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


if __name__ == "__main__":
    unittest.main()
