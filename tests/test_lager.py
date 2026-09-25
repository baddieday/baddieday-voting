"""Lager-Abgleich und Übernahme (E19): Puffer → Lager mit Prüfsumme, einmalig Lager → Puffer.

Zwei Temp-Ordner spielen Puffer und Lager (MitLager aus test_getrennt: st_dev per patch). pve-big gibt es nicht:
big.wach_halten ist ersetzt und zählt nur mit, lager_erreichbar liefert True, jeder Netzwerkzugriff ist ein Fehler.
"""

import contextlib
import errno
import io
import json
import os
import sqlite3
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from clip_pipeline import big, cli, konfig, lager, material
from clip_pipeline.konfig import KonfigFehler
from clip_pipeline.sperre import sperre
from clip_pipeline.zeit import iso, jetzt, lokal_zu_utc, utc_zu_lokal

from tests.test_getrennt import ECHT_DATEISYSTEM, MitLager

ECHT_SHA256 = material.sha256
ECHT_WACH_HALTEN = big.wach_halten


class MitAbgleich(MitLager):
    """Getrennter Betrieb mit Puffer und Lager; pve-big „wach_halten“ wird nur gezählt."""

    def setUp(self):
        super().setUp()
        self.konfig.daten["lager"].update(ruhe_min=0, warten_s=0)  # Testdateien sind frisch geschrieben
        self.konfig.daten["lager"]["nachtruhe_von"] = ""           # nicht von der Uhrzeit abhängen (Klasse Nachtruhe)
        self.konfig.daten["big"]["host"] = "pve-big"               # damit der Abgleich wach_halten benutzt
        self.geweckt: list[str] = []
        self.wecken_erlaubt: list[bool] = []

        @contextlib.contextmanager
        def wach_halten(k, name, grund, minuten=120, *, wecken=True):
            self.geweckt.append(name)
            self.wecken_erlaubt.append(wecken)
            yield True

        for p in (mock.patch.object(big, "wach_halten", wach_halten),
                  mock.patch.object(konfig.Konfig, "lager_erreichbar", return_value=True),
                  mock.patch("socket.create_connection", side_effect=AssertionError("Netzwerkzugriff im Test"))):
            p.start()
            self.addCleanup(p.stop)

    def datei(self, wurzel: Path, rel: str, inhalt: bytes, alter_s: float = 3600) -> Path:
        pfad = wurzel / rel
        pfad.parent.mkdir(parents=True, exist_ok=True)
        pfad.write_bytes(inhalt)
        t = time.time() - alter_s
        os.utime(pfad, (t, t))
        return pfad

    def lauf(self, *argv) -> tuple[int, dict]:
        ausgabe = io.StringIO()
        with mock.patch("clip_pipeline.cli.lade", return_value=self.konfig), \
                contextlib.redirect_stdout(ausgabe), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(list(argv))
        return code, json.loads(ausgabe.getvalue().strip().splitlines()[-1])

    def zeile(self, relativ: str):
        return self.con.execute("SELECT * FROM lager WHERE relativ = ?", (relativ,)).fetchone()

    def meldungen(self) -> list:
        return self.con.execute("SELECT schluessel, text FROM meldungen ORDER BY id").fetchall()

    def lager_inhalt(self) -> set[str]:
        return {p.relative_to(self.lager).as_posix() for p in self.lager.rglob("*") if p.is_file()}

    def heute(self) -> str:
        return utc_zu_lokal(jetzt(), "Europe/Berlin").date().isoformat()

    @contextlib.contextmanager
    def lager_tabu(self):
        """Jeder stat/scandir im Lager ist ein Fehler (pve-big schläft: der NFS-Mount hinge)."""
        echt_stat, echt_lstat, echt_scandir = os.stat, os.lstat, os.scandir

        def pruefe(pfad):
            if isinstance(pfad, (str, os.PathLike)) and Path(os.fspath(pfad)).is_relative_to(self.lager):
                raise AssertionError(f"Lager angefasst: {pfad}")

        def stat(pfad, *a, **kw):
            pruefe(pfad)
            return echt_stat(pfad, *a, **kw)

        def lstat(pfad, *a, **kw):
            pruefe(pfad)
            return echt_lstat(pfad, *a, **kw)

        def scandir(pfad="."):
            pruefe(pfad)
            return echt_scandir(pfad)

        with mock.patch("os.stat", stat), mock.patch("os.lstat", lstat), mock.patch("os.scandir", scandir):
            yield


# --- Abgleich ------------------------------------------------------------------------------

class Abgleich(MitAbgleich):
    def test_kopie_und_bestaetigung(self):
        dateien = {"eingang/nvidia/a.mp4": b"video" * 1000, "replays/r.replay": bytes(range(256)) * 50,
                   "sessions/s1/analyse.json": b'{"k": 1}', "sitzungen/abend.json": b"{}"}
        for rel, inhalt in dateien.items():
            self.datei(self.puffer, rel, inhalt)
        # bleibt im Puffer: versteckt, unfertig, Link, Ordner außerhalb von [lager].ordner
        self.datei(self.puffer, "eingang/.versteckt.mp4", b"x")
        self.datei(self.puffer, "eingang/b.mp4.teil", b"x")
        self.datei(self.puffer, "sessions/s1/c.tmp.mp4", b"x")
        self.datei(self.puffer, "sessions/.aktiv/herz", b"")
        self.datei(self.puffer, "papierkorb/weg.mp4", b"x")
        (self.puffer / "sessions" / "s1" / "link.json").symlink_to(self.puffer / "sessions" / "s1" / "analyse.json")

        code, e = self.lauf("lager", "abgleich")
        self.assertEqual(code, 0, e)
        sicherung = f"sicherung/pipeline-{self.heute()}.db"
        self.assertEqual((e["kopiert"], e["fehler"], e["versioniert"], e["sicherung"]), (5, 0, 0, sicherung))
        self.assertTrue(e["ok"])
        self.assertEqual(self.geweckt, ["lager"])
        self.assertEqual(self.lager_inhalt(), set(dateien) | {".clip-lager", sicherung})
        for rel, inhalt in dateien.items():
            quelle, ziel = self.puffer / rel, self.lager / rel
            self.assertEqual(ziel.read_bytes(), inhalt)
            self.assertEqual(ziel.stat().st_mtime_ns, quelle.stat().st_mtime_ns)  # mtime übernommen
            z = self.zeile(rel)
            self.assertEqual((z["groesse"], z["mtime_ns"], z["sha256"], z["lager_relativ"]),
                             (len(inhalt), quelle.stat().st_mtime_ns, ECHT_SHA256(quelle), rel))
            self.assertTrue(z["bestaetigt"] and z["zuerst_gesehen"])
        self.assertFalse([p for p in self.lager.rglob("*.teil")])
        lauf = self.con.execute("SELECT * FROM lager_laeufe").fetchone()
        self.assertEqual(lauf["art"], "abgleich")
        self.assertTrue(json.loads(lauf["ergebnis"])["ok"] and lauf["ende"])
        self.assertEqual(self.meldungen(), [])
        # Rohdaten im Puffer unverändert (nur gelesen)
        self.assertEqual((self.puffer / "eingang/nvidia/a.mp4").read_bytes(), dateien["eingang/nvidia/a.mp4"])

    def test_nichts_offen_weckt_nicht(self):
        self.datei(self.puffer, "replays/r.replay", b"replay")
        self.assertEqual(self.lauf("lager", "abgleich")[0], 0)
        self.assertEqual(self.geweckt, ["lager"])
        # zweiter Lauf: nur die neue DB-Sicherung ist offen – sie weckt pve-big nicht, das Lager bleibt unberührt
        with self.lager_tabu():
            code, e = self.lauf("lager", "abgleich")
        self.assertEqual(code, 0, e)
        self.assertEqual(self.geweckt, ["lager"])
        self.assertEqual((e["offen"], e["kopiert"], e["lager_gebraucht"], e["ok"]), (1, 0, False, True))
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM lager_laeufe WHERE ende IS NOT NULL").fetchone()[0], 2)
        stand = lager.status(self.con, self.konfig)  # die wartende Sicherung zählt nicht als "offen"
        self.assertEqual((stand["offen"], stand["sicherung_offen"]), (0, 1))
        self.assertTrue(stand["zeile"].endswith("ok · 0 offen"), stand["zeile"])

    def test_sicherung_allein_weckt_erst_nach_tagen(self):
        self.assertEqual(self.lauf("lager", "abgleich")[0], 0)  # allererste Sicherung: noch keine im Lager
        self.assertEqual(self.geweckt, ["lager"])
        self.con.execute("UPDATE lager SET bestaetigt = ?", (iso(jetzt() - timedelta(days=8)),))
        self.assertEqual(self.lauf("lager", "abgleich")[0], 0)
        self.assertEqual(self.geweckt, ["lager", "lager"])
        self.konfig.daten["lager"]["sicherung_wecken_tage"] = 0  # 0 = nie allein
        self.con.execute("UPDATE lager SET bestaetigt = ?", (iso(jetzt() - timedelta(days=80)),))
        self.assertEqual(self.lauf("lager", "abgleich")[1]["lager_gebraucht"], False)

    def test_sicherung_der_datenbank(self):
        ordner = self.puffer / "sicherung"
        ordner.mkdir()
        for tag in range(1, 33):
            (ordner / f"pipeline-2026-08-{tag:02d}.db").write_bytes(b"alt")
        (ordner / "notiz.txt").write_text("bleibt")
        self.con.execute("INSERT INTO meldungen (schluessel, text, erstellt) VALUES ('probe', 'x', 'y')")
        self.assertEqual(self.lauf("lager", "abgleich")[0], 0)
        heute = ordner / f"pipeline-{self.heute()}.db"
        namen = sorted(p.name for p in ordner.iterdir())
        self.assertEqual(len([n for n in namen if n.startswith("pipeline-")]), 30)  # sicherungen_behalten
        self.assertIn("notiz.txt", namen)
        self.assertNotIn("pipeline-2026-08-03.db", namen)
        self.assertIn("pipeline-2026-08-04.db", namen)
        self.assertFalse([n for n in namen if n.startswith(".") or n.endswith(("-wal", "-shm", "-journal"))])
        kopie = sqlite3.connect(heute)
        try:
            self.assertEqual(kopie.execute("PRAGMA journal_mode").fetchone()[0], "delete")
            self.assertEqual(kopie.execute("SELECT text FROM meldungen WHERE schluessel = 'probe'").fetchone()[0], "x")
            self.assertEqual(kopie.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        finally:
            kopie.close()
        self.assertEqual((self.lager / "sicherung" / heute.name).read_bytes(), heute.read_bytes())

    def test_ruhezeit(self):
        self.konfig.daten["lager"]["ruhe_min"] = 10
        self.datei(self.puffer, "eingang/frisch.mp4", b"x", alter_s=3600)  # mtime alt, ctime aber frisch
        self.assertEqual(lager.sammle(self.con, self.konfig), [])
        self.assertEqual([o.relativ for o in lager.sammle(self.con, self.konfig, fertig=["eingang/frisch.mp4"])],
                         ["eingang/frisch.mp4"])
        self.konfig.daten["lager"]["ruhe_min"] = 0
        self.assertEqual(len(lager.sammle(self.con, self.konfig)), 1)

    def test_rohdaten_zuerst(self):
        for rel in ("archiv/a.mp4", "sessions/s/x.json", "replays/r.replay", "eingang/e.mp4"):
            self.datei(self.puffer, rel, b"x")
        self.assertEqual([o.relativ for o in lager.sammle(self.con, self.konfig)],
                         ["eingang/e.mp4", "replays/r.replay", "sessions/s/x.json", "archiv/a.mp4"])

    def test_gleiche_datei_im_lager_nur_bestaetigen(self):
        quelle = self.datei(self.puffer, "replays/r.replay", b"gleich" * 100)
        ziel = self.datei(self.lager, "replays/r.replay", b"gleich" * 100, alter_s=99999)
        vorher = ziel.stat().st_mtime_ns
        with mock.patch.object(lager, "kopiere_geprueft", wraps=lager.kopiere_geprueft) as kopie:
            code, e = self.lauf("lager", "abgleich")
        self.assertEqual(code, 0, e)
        self.assertEqual((e["bestaetigt"], e["kopiert"]), (1, 1))  # kopiert: nur die DB-Sicherung
        self.assertEqual([c.args[0].name for c in kopie.call_args_list], [f"pipeline-{self.heute()}.db"])
        self.assertEqual(ziel.stat().st_mtime_ns, vorher)          # im Lager nichts angefasst
        z = self.zeile("replays/r.replay")
        self.assertEqual((z["mtime_ns"], z["lager_relativ"]), (quelle.stat().st_mtime_ns, "replays/r.replay"))

    def test_rohdaten_konflikt_versioniert_nie_ueberschrieben(self):
        quelle = self.datei(self.puffer, "eingang/nvidia/a.mp4", b"neu!")
        self.datei(self.lager, "eingang/nvidia/a.mp4", b"alt")                # andere Größe
        self.datei(self.puffer, "replays/r.replay", b"ABC")
        self.datei(self.lager, "replays/r.replay", b"abc")                    # gleiche Größe, anderer Inhalt
        mtime_r = (self.puffer / "replays/r.replay").stat().st_mtime_ns
        self.datei(self.lager, f"replays/r~{mtime_r}.replay", b"xyz")         # auch der erste Name ist belegt
        self.datei(self.puffer, "sessions/s/analyse.json", b'{"neu": 1}')
        self.datei(self.lager, "sessions/s/analyse.json", b'{"alt": 1}')      # abgeleitet: wird ersetzt
        code, e = self.lauf("lager", "abgleich")
        self.assertEqual(code, 0, e)
        self.assertEqual((e["versioniert"], e["fehler"]), (2, 0))
        mtime = quelle.stat().st_mtime_ns
        self.assertEqual((self.lager / "eingang/nvidia/a.mp4").read_bytes(), b"alt")
        self.assertEqual((self.lager / f"eingang/nvidia/a~{mtime}.mp4").read_bytes(), b"neu!")
        self.assertEqual((self.lager / "replays/r.replay").read_bytes(), b"abc")
        self.assertEqual((self.lager / f"replays/r~{mtime_r}.replay").read_bytes(), b"xyz")
        self.assertEqual((self.lager / f"replays/r~{mtime_r}-2.replay").read_bytes(), b"ABC")
        self.assertEqual(self.zeile("eingang/nvidia/a.mp4")["lager_relativ"], f"eingang/nvidia/a~{mtime}.mp4")
        self.assertEqual((self.lager / "sessions/s/analyse.json").read_bytes(), b'{"neu": 1}')
        [(schluessel, text)] = self.meldungen()
        self.assertEqual(schluessel, f"lager:{self.heute()}")
        self.assertIn("nichts überschrieben", text)
        self.assertIn("Nichts verloren – beide Fassungen liegen im Lager", text)
        # Bestätigung verloren (ältere DB-Sicherung eingespielt, Absturz zwischen replace und Eintrag): der nächste
        # Lauf findet die abgelegten Fassungen und bestätigt nur – keine weitere Kopie im Lager
        self.con.execute("DELETE FROM lager WHERE relativ IN ('eingang/nvidia/a.mp4', 'replays/r.replay')")
        vorher = {rel: (self.lager / rel).stat().st_mtime_ns for rel in self.lager_inhalt()}
        with mock.patch.object(lager, "kopiere_geprueft", wraps=lager.kopiere_geprueft) as kopie:
            code, e = self.lauf("lager", "abgleich")
        self.assertEqual(code, 0, e)
        self.assertEqual(self.geweckt, ["lager", "lager"])  # wirklich geweckt, nicht nur die Sicherung offen
        self.assertEqual((e["versioniert"], e["kopiert"], e["fehler"]), (2, 1, 0))  # kopiert: nur die DB-Sicherung
        self.assertEqual([c.args[0].name for c in kopie.call_args_list], [f"pipeline-{self.heute()}.db"])
        self.assertEqual(self.lager_inhalt(), set(vorher))
        for rel, mtime_ns in vorher.items():  # Rohdaten im Lager nicht angefasst
            if not rel.startswith("sicherung/"):
                self.assertEqual((self.lager / rel).stat().st_mtime_ns, mtime_ns, rel)
        self.assertEqual(self.zeile("eingang/nvidia/a.mp4")["lager_relativ"], f"eingang/nvidia/a~{mtime}.mp4")
        self.assertEqual(self.zeile("replays/r.replay")["lager_relativ"], f"replays/r~{mtime_r}-2.replay")
        self.assertEqual(self.zeile("replays/r.replay")["sha256"], ECHT_SHA256(self.puffer / "replays/r.replay"))
        # und danach ist nichts mehr offen außer der nächsten Sicherung: pve-big bleibt aus
        self.assertEqual(self.lauf("lager", "abgleich")[1]["lager_gebraucht"], False)

    def test_quelle_aendert_sich_waehrend_der_kopie(self):
        quelle = self.datei(self.puffer, "eingang/a.mp4", b"a" * 5000)

        def sha_mit_nachschreiben(pfad):
            if Path(pfad).name.startswith(".a.mp4."):  # Zurücklesen der Zwischendatei: Quelle wächst gerade
                with open(quelle, "ab") as f:
                    f.write(b"mehr")
            return ECHT_SHA256(pfad)

        with mock.patch.object(lager, "sha256", side_effect=sha_mit_nachschreiben):
            code, e = self.lauf("lager", "abgleich")
        self.assertEqual(code, 1, e)
        self.assertEqual((e["fehler"], e["kopiert"]), (1, 1))  # die DB-Sicherung ging durch
        self.assertIn("geändert", e["fehler_liste"][0])
        self.assertIsNone(self.zeile("eingang/a.mp4"))
        self.assertFalse((self.lager / "eingang" / "a.mp4").exists())
        self.assertFalse(list(self.lager.rglob("*.teil")))
        [(_, text)] = self.meldungen()
        self.assertIn("Nichts verloren", text)
        # beim nächsten Abgleich klappt es (Datei ist fertig)
        code, e = self.lauf("lager", "abgleich")
        self.assertEqual((code, e["kopiert"]), (0, 2))
        self.assertEqual((self.lager / "eingang" / "a.mp4").read_bytes(), b"a" * 5000 + b"mehr")
        self.assertEqual(len(self.meldungen()), 1)  # höchstens eine Meldung am Tag

    def test_pruefsummenfehler(self):
        self.datei(self.puffer, "replays/r.replay", b"replay")

        def falsch(pfad):
            return "0" * 64 if Path(pfad).name.endswith(".teil") else ECHT_SHA256(pfad)

        with mock.patch.object(lager, "sha256", side_effect=falsch):
            code, e = self.lauf("lager", "abgleich")
        self.assertEqual(code, 1)
        self.assertEqual(e["fehler"], 2)  # auch die DB-Sicherung
        self.assertIn("Prüfsumme", e["fehler_liste"][0])
        self.assertIsNone(self.zeile("replays/r.replay"))
        self.assertEqual(self.lager_inhalt(), {".clip-lager"})
        self.assertEqual(list(self.lager.rglob("*.teil")), [])
        self.assertFalse(json.loads(self.con.execute("SELECT ergebnis FROM lager_laeufe").fetchone()[0])["ok"])

    def test_einzelner_dateifehler_der_rest_laeuft(self):
        for rel in ("eingang/a.mp4", "eingang/b.mp4"):
            self.datei(self.puffer, rel, b"x")
        echt = lager.kopiere_geprueft

        def kopie(quelle, ziel, erwartet=None):
            if quelle.name == "a.mp4":
                raise PermissionError(errno.EACCES, "Zugriff verweigert")
            return echt(quelle, ziel, erwartet)

        with mock.patch.object(lager, "kopiere_geprueft", side_effect=kopie):
            code, e = self.lauf("lager", "abgleich")
        self.assertEqual((code, e["fehler"], e["kopiert"]), (1, 1, 2))
        self.assertTrue((self.lager / "eingang/b.mp4").is_file())

    def test_eio_bricht_die_schleife_ab(self):
        for rel in ("eingang/a.mp4", "eingang/b.mp4"):
            self.datei(self.puffer, rel, b"x")
        with mock.patch.object(lager, "kopiere_geprueft", side_effect=OSError(errno.EIO, "E/A-Fehler")) as kopie:
            code, e = self.lauf("lager", "abgleich")
        self.assertEqual(code, 3, e)
        self.assertIn("E/A-Fehler", e["abbruch"])
        self.assertEqual(kopie.call_count, 1)
        self.assertEqual(len(self.meldungen()), 1)

    def test_lager_markierung_verschwindet_unterwegs(self):
        for rel in ("eingang/a.mp4", "eingang/b.mp4"):
            self.datei(self.puffer, rel, b"x")
        echt = lager.kopiere_geprueft

        def kopie_und_nfs_weg(quelle, ziel, erwartet=None):
            sha = echt(quelle, ziel, erwartet)
            (self.lager / ".clip-lager").unlink()  # NFS weg: dahinter läge der leere Einhängepunkt
            return sha

        with mock.patch.object(lager, "kopiere_geprueft", side_effect=kopie_und_nfs_weg):
            code, e = self.lauf("lager", "abgleich")
        self.assertEqual(code, 3, e)
        self.assertEqual(e["kopiert"], 1)
        self.assertIn(".clip-lager", e["abbruch"])
        self.assertFalse((self.lager / "eingang/b.mp4").exists())

    def test_alte_teil_reste_im_lager(self):
        self.datei(self.puffer, "eingang/a.mp4", b"x")
        alt = self.datei(self.lager, "eingang/.a.mp4.4711.teil", b"rest", alter_s=7200)
        frisch = self.datei(self.lager, "eingang/.b.mp4.4712.teil", b"rest", alter_s=60)
        fremd = self.datei(self.lager, "eingang/.notiz", b"bleibt", alter_s=7200)
        self.assertEqual(self.lauf("lager", "abgleich")[0], 0)
        self.assertFalse(alt.exists())
        self.assertTrue(frisch.exists() and fremd.exists())

    def test_probelauf(self):
        self.datei(self.puffer, "eingang/a.mp4", b"x" * 1000)
        with self.lager_tabu():
            code, e = self.lauf("lager", "abgleich", "--probelauf")
        self.assertEqual(code, 0, e)
        self.assertEqual((e["probelauf"], e["offen"], e["dateien"], e["wuerde_wecken"]), (True, 1, ["eingang/a.mp4"], True))
        self.assertEqual(self.geweckt, [])
        self.assertFalse((self.puffer / "sicherung").exists())
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM lager_laeufe").fetchone()[0], 0)


class Verwechslung(MitAbgleich):
    """Puffer und Lager verwechselbar → Exit 2, im Lager NICHTS kopiert."""

    def setUp(self):
        super().setUp()
        self.datei(self.puffer, "eingang/a.mp4", b"x")
        self.datei(self.puffer, "sessions/s/analyse.json", b"{}")

    def pruefe_abbruch(self, *worte, geweckt: list):
        code, e = self.lauf("lager", "abgleich")
        self.assertEqual(code, 2, e)
        for wort in worte:
            self.assertIn(wort, e["hinweis"])
        self.assertEqual(self.lager_inhalt() - {".clip-lager", ".clip-puffer"}, set())
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM lager").fetchone()[0], 0)
        self.assertEqual(self.geweckt, geweckt)
        [(_, text)] = self.meldungen()
        self.assertIn("nichts kopiert", text)
        self.assertIn("Nichts verloren", text)

    def test_gleiches_dateisystem(self):
        with mock.patch.object(konfig, "_dateisystem", ECHT_DATEISYSTEM):
            self.pruefe_abbruch("demselben Dateisystem", geweckt=["lager"])  # erst nach dem Wecken prüfbar

    def test_vertauschte_marken(self):
        (self.puffer / ".clip-lager").touch()
        self.pruefe_abbruch("vertauscht", geweckt=[])  # Puffer-Seite: sofort, ohne zu wecken

    def test_marke_puffer_im_lager(self):
        (self.lager / ".clip-puffer").touch()
        self.pruefe_abbruch("Im Lager", "vertauscht", geweckt=["lager"])

    def test_link_zeigt_aufs_lager(self):
        link = self.tmp / "clips-link"
        link.symlink_to(self.lager, target_is_directory=True)
        self.konfig.daten["speicher"]["wurzel"] = str(link)
        self.pruefe_abbruch("fehlt .clip-puffer", geweckt=[])


class OfflineUndSperre(MitAbgleich):
    def setUp(self):
        super().setUp()
        self.datei(self.puffer, "eingang/a.mp4", b"x")

    def test_lager_offline_exit_3(self):
        (self.lager / ".clip-lager").unlink()  # pve-big geweckt, NFS aber nicht eingehängt
        code, e = self.lauf("lager", "abgleich")
        self.assertEqual((code, e["fehler"]), (3, "speicher_offline"))
        self.assertIn(".clip-lager", e["hinweis"])
        self.assertEqual(self.lager_inhalt(), set())
        lauf = json.loads(self.con.execute("SELECT ergebnis FROM lager_laeufe").fetchone()[0])
        self.assertFalse(lauf["ok"])
        self.assertIn("SpeicherOffline", lauf["abbruch"])
        self.assertEqual(self.meldungen(), [])  # vorübergehend: meldet erst die Morgenprüfung

    def test_wecken_verboten_exit_3_ohne_wol(self):
        # das echte wach_halten: pve-big schläft, ohne MAC darf er nicht geweckt werden
        with mock.patch.object(big, "wach_halten", ECHT_WACH_HALTEN), \
                mock.patch.object(big, "wach", return_value=False), \
                mock.patch.object(big, "sende_wake_on_lan") as wol:
            code, e = self.lauf("lager", "abgleich")
        self.assertEqual((code, e["fehler"]), (3, "nicht_geweckt"))
        self.assertIn("MAC", e["hinweis"])
        wol.assert_not_called()
        self.assertEqual(self.lager_inhalt(), {".clip-lager"})

    def test_sperre_belegt_exit_4(self):
        with sperre(big.lager_sperre(self.konfig)):
            code, e = self.lauf("lager", "abgleich")
            self.assertEqual((code, e["fehler"]), (4, "gesperrt"))
            code, _ = self.lauf("lager", "uebernehmen", "--von", str(self.lager), "--nach", str(self.puffer))
            self.assertEqual(code, 4)
        self.assertEqual(self.geweckt, [])
        self.assertEqual(self.lager_inhalt(), {".clip-lager"})
        self.assertEqual(self.lauf("lager", "abgleich")[0], 0)  # danach wieder frei

    def test_ohne_getrennten_betrieb_exit_2(self):
        self.konfig.daten["lager"]["wurzel"] = ""
        for befehl in ("abgleich", "status"):
            code, e = self.lauf("lager", befehl)
            self.assertEqual(code, 2)
            self.assertEqual(e["fehler"], "kein getrennter Betrieb: [lager].wurzel leer")
        self.assertEqual(self.geweckt, [])


# --- Nachtruhe -----------------------------------------------------------------------------

def _um(stunde: int, minute: int = 0) -> datetime:
    return lokal_zu_utc(datetime(2026, 9, 25, stunde, minute), "Europe/Berlin")


def _fenster(von_min: int, bis_min: int) -> dict:
    """Nachtruhe relativ zur echten Uhrzeit (Ortszeit): (-60, 60) = jetzt mittendrin, (60, 120) = jetzt nicht."""
    lokal = utc_zu_lokal(jetzt(), "Europe/Berlin")
    return {"nachtruhe_von": f"{lokal + timedelta(minutes=von_min):%H:%M}",
            "nachtruhe_bis": f"{lokal + timedelta(minutes=bis_min):%H:%M}"}


class Nachtruhe(MitAbgleich):
    """Der Lüfter von pve-big soll niemanden wecken: In der Nachtruhe weckt der Abgleich nie – auch nicht beim
    Nachholen nach einem Neustart des Mini (Persistent=true ist nur ein Lauf zur Unzeit). Läuft er, wird abgeglichen."""

    def setUp(self):
        super().setUp()
        self.datei(self.puffer, "eingang/a.mp4", b"x" * 1000)

    def test_zeitfenster(self):
        self.konfig.daten["lager"].update(nachtruhe_von="22:00", nachtruhe_bis="08:00")
        erwartet = {(21, 59): False, (22, 0): True, (0, 30): True, (4, 30): True, (7, 59): True, (8, 0): False,
                    (10, 0): False, (11, 0): False}
        for (stunde, minute), ruhe in erwartet.items():
            self.assertEqual(lager.nachtruhe(self.konfig, _um(stunde, minute)), ruhe, (stunde, minute))
        del self.konfig.daten["lager"]["nachtruhe_von"], self.konfig.daten["lager"]["nachtruhe_bis"]
        self.assertTrue(lager.nachtruhe(self.konfig, _um(3)))  # ohne Eintrag (alte lokal.toml): 22:00–08:00
        self.assertFalse(lager.nachtruhe(self.konfig, _um(10)))

    def test_leer_ist_aus(self):
        for von, bis in (("", "08:00"), ("22:00", ""), ("", "")):
            self.konfig.daten["lager"].update(nachtruhe_von=von, nachtruhe_bis=bis)
            self.assertFalse(lager.nachtruhe(self.konfig, _um(3)), (von, bis))

    def test_tippfehler_schaltet_nicht_still_ab(self):
        self.konfig.daten["lager"].update(nachtruhe_von="25:00", nachtruhe_bis="8")
        with self.assertRaises(KonfigFehler):
            lager.nachtruhe(self.konfig, _um(3))
        code, e = self.lauf("lager", "abgleich")
        self.assertEqual((code, e["fehler"]), (2, "konfig"))
        self.assertIn("nachtruhe", e["hinweis"])
        self.assertEqual(self.geweckt, [])
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM lager_laeufe").fetchone()[0], 0)
        self.assertEqual(self.meldungen(), [])

    def test_schlafend_wird_nicht_geweckt(self):
        self.konfig.daten["lager"].update(_fenster(-60, 60))
        with mock.patch.object(big, "wach", return_value=False) as wach, self.lager_tabu():
            code, e = self.lauf("lager", "abgleich")
            with self.assertLogs("pipeline", "INFO") as logs:
                lager.abgleich(self.con, self.konfig)  # zweiter Lauf, z. B. nachgeholt nach einem Neustart
        self.assertEqual(code, 0, e)
        self.assertTrue(e["nachtruhe"])
        self.assertEqual((e["offen"], e["kopiert"], e["lager_gebraucht"], e["fehler"]), (2, 0, False, 0))
        self.assertEqual(self.geweckt, [])  # kein wach_halten, also auch kein Wake-on-LAN
        self.assertEqual(wach.call_count, 2)
        self.assertTrue(any("Nachtruhe" in z and "nicht geweckt" in z for z in logs.output), logs.output)
        self.assertEqual(self.lager_inhalt(), {".clip-lager"})
        self.assertEqual(self.meldungen(), [])  # keine Meldung
        lauf = json.loads(self.con.execute("SELECT ergebnis FROM lager_laeufe").fetchone()[0])
        self.assertTrue(lauf["nachtruhe"])
        # kein Erfolg: das Offene liegt noch im Puffer – sonst schwiege die Morgenprüfung, auch wenn es anhält
        stand = lager.status(self.con, self.konfig)
        self.assertIsNone(stand["letzter_erfolg"])
        self.assertIn("in der Nachtruhe übersprungen · 1 offen", stand["zeile"])

    def test_wach_wird_abgeglichen_ohne_zu_wecken(self):
        self.konfig.daten["lager"].update(_fenster(-60, 60))
        with mock.patch.object(big, "wach", return_value=True):
            code, e = self.lauf("lager", "abgleich")
        self.assertEqual(code, 0, e)
        self.assertNotIn("nachtruhe", e)
        self.assertEqual((e["kopiert"], e["ok"]), (2, True))
        self.assertEqual(self.geweckt, ["lager"])
        self.assertEqual(self.wecken_erlaubt, [False])  # wach_halten darf nicht wecken, falls er gerade einschläft
        self.assertTrue((self.lager / "eingang/a.mp4").is_file())

    def test_einschlafen_zwischen_blick_und_abgleich_weckt_nicht(self):
        """Harte Grenze: Schläft pve-big genau zwischen dem Blick und wach_halten ein, bleibt es dabei."""
        self.konfig.daten["lager"].update(_fenster(-60, 60))
        self.konfig.daten["speicher"]["wol_mac"] = "aa:bb:cc:dd:ee:ff"
        with mock.patch.object(big, "wach_halten", ECHT_WACH_HALTEN), \
                mock.patch.object(big, "wach", side_effect=[True, False]), \
                mock.patch.object(big, "darf_wecken", return_value=None), \
                mock.patch.object(big, "sende_wake_on_lan") as wol:
            code, e = self.lauf("lager", "abgleich")
        self.assertEqual((code, e["fehler"]), (3, "nicht_geweckt"))
        self.assertIn("Nachtruhe", e["hinweis"])
        wol.assert_not_called()
        self.assertEqual(self.lager_inhalt(), {".clip-lager"})

    def test_ausserhalb_wie_bisher(self):
        self.konfig.daten["lager"].update(_fenster(60, 120))
        with mock.patch.object(big, "wach", side_effect=AssertionError("außerhalb der Nachtruhe nicht fragen")):
            code, e = self.lauf("lager", "abgleich")
        self.assertEqual(code, 0, e)
        self.assertNotIn("nachtruhe", e)
        self.assertEqual((self.geweckt, self.wecken_erlaubt), (["lager"], [True]))

    def test_leer_wie_bisher(self):
        self.konfig.daten["lager"].update(nachtruhe_von="", nachtruhe_bis="08:00")
        with mock.patch.object(big, "wach", side_effect=AssertionError("ohne Nachtruhe nicht fragen")):
            code, e = self.lauf("lager", "abgleich")
        self.assertEqual(code, 0, e)
        self.assertEqual((self.geweckt, self.wecken_erlaubt), (["lager"], [True]))

    def test_probelauf_zeigt_die_nachtruhe(self):
        self.konfig.daten["lager"].update(_fenster(-60, 60))
        with mock.patch.object(big, "wach", side_effect=AssertionError("der Probelauf fragt pve-big nicht")), \
                self.lager_tabu():
            code, e = self.lauf("lager", "abgleich", "--probelauf")
        self.assertEqual(code, 0, e)
        self.assertEqual((e["wuerde_wecken"], e["nachtruhe"]), (True, True))
        self.assertEqual(self.geweckt, [])

    def test_uebernahme_kennt_keine_nachtruhe(self):
        self.konfig.daten["lager"].update(_fenster(-60, 60), wurzel="")
        self.datei(self.lager, "replays/r.replay", b"replay")
        with mock.patch.object(big, "wach", side_effect=AssertionError("die Übernahme fragt nicht nach der Nachtruhe")):
            code, e = self.lauf("lager", "uebernehmen", "--von", str(self.lager), "--nach", str(self.puffer))
        self.assertEqual(code, 0, e)
        self.assertEqual((self.geweckt, self.wecken_erlaubt), (["uebernahme"], [True]))


# --- Status --------------------------------------------------------------------------------

class Status(MitAbgleich):
    def test_status_weckt_nie_und_fasst_das_lager_nicht_an(self):
        self.datei(self.puffer, "eingang/a.mp4", b"x" * 2000, alter_s=7200)
        with self.lager_tabu():
            code, stand = self.lauf("lager", "status")
        self.assertEqual(code, 0, stand)
        self.assertEqual((stand["getrennt"], stand["pruefung"], stand["offen"]), (True, "ok", 1))
        self.assertIsNotNone(stand["aelteste"])
        self.assertIsNone(stand["letzter_lauf"])
        self.assertRegex(stand["zeile"], r"^Puffer \d+ GB frei · Lager: noch kein Abgleich · 1 offen$")
        self.assertEqual(self.geweckt, [])

    def test_status_nach_abgleich(self):
        self.datei(self.puffer, "eingang/a.mp4", b"x")
        self.lauf("lager", "abgleich")
        stand = lager.status(self.con, self.konfig)
        self.assertTrue(stand["letzter_lauf"]["ok"])
        self.assertEqual(stand["letzter_erfolg"], stand["letzter_lauf"]["ende"])
        self.assertRegex(stand["zeile"], r"Lager: letzter Abgleich \d\d:\d\d ok · 0 offen$")
        # ein späterer Lauf mit Fehler: letzter Erfolg bleibt der alte
        self.datei(self.puffer, "eingang/b.mp4", b"y")
        with mock.patch.object(lager, "kopiere_geprueft", side_effect=PermissionError("nein")):
            self.lauf("lager", "abgleich")
        stand = lager.status(self.con, self.konfig)
        self.assertFalse(stand["letzter_lauf"]["ok"])
        self.assertIn("mit 2 Fehler(n)", stand["zeile"])  # b.mp4 und die Sicherung
        self.assertIsNotNone(stand["letzter_erfolg"])

    def test_status_pruefung_fehlgeschlagen(self):
        (self.puffer / ".clip-puffer").unlink()
        code, stand = self.lauf("lager", "status")
        self.assertEqual(code, 2)
        self.assertIn("fehlt .clip-puffer", stand["pruefung"])
        self.assertIsNone(stand["offen"])
        self.assertIn("⚠️", stand["zeile"])


# --- Übernahme -------------------------------------------------------------------------------

class Uebernahme(MitAbgleich):
    """Vor dem Umschalten: [lager].wurzel ist noch leer, die Pfade kommen ausdrücklich."""

    def setUp(self):
        super().setUp()
        self.konfig.daten["lager"]["wurzel"] = ""
        tag = 86400
        self.datei(self.lager, "eingang/alt.mp4", b"alt", alter_s=10 * tag)
        self.datei(self.lager, "eingang/neu.mp4", b"neu" * 100, alter_s=3600)
        self.datei(self.lager, "replays/r.replay", b"replay", alter_s=100 * tag)
        self.datei(self.lager, "sessions/s/analyse.json", b"{}", alter_s=50 * tag)
        self.datei(self.lager, "highlights/h.mp4", b"highlight", alter_s=20 * tag)
        self.datei(self.lager, "papierkorb/x.mp4", b"nicht", alter_s=20 * tag)
        self.datei(self.lager, "eingang/.a.mp4.12.teil", b"rest")

    def uebernehmen(self, *extra) -> tuple[int, dict]:
        return self.lauf("lager", "uebernehmen", "--von", str(self.lager), "--nach", str(self.puffer), *extra)

    def stand_lager(self) -> dict:
        return {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in self.lager.rglob("*") if p.is_file()}

    def test_eingang_tage_und_delta(self):
        vorher = self.stand_lager()
        code, e = self.uebernehmen("--eingang-tage", "3")
        self.assertEqual(code, 0, e)
        self.assertEqual((e["kopiert"], e["eingang_alt"], e["gleich"], e["konflikte"]), (4, 1, 0, 0))
        self.assertEqual(self.geweckt, ["uebernahme"])
        for rel in ("eingang/neu.mp4", "replays/r.replay", "sessions/s/analyse.json", "highlights/h.mp4"):
            self.assertEqual((self.puffer / rel).read_bytes(), (self.lager / rel).read_bytes())
            self.assertEqual((self.puffer / rel).stat().st_mtime_ns, (self.lager / rel).stat().st_mtime_ns)
            self.assertEqual(self.zeile(rel)["lager_relativ"], rel)
        self.assertFalse((self.puffer / "eingang/alt.mp4").exists())
        self.assertFalse((self.puffer / "papierkorb/x.mp4").exists())
        self.assertFalse(list(self.puffer.rglob("*.teil")))
        self.assertEqual(self.stand_lager(), vorher)  # das Lager wird nur gelesen
        # Delta: zweiter Lauf überspringt alles
        code, e = self.uebernehmen("--eingang-tage", "3")
        self.assertEqual((code, e["kopiert"], e["gleich"]), (0, 0, 4))
        # im Lager geändert (alter Betrieb schreibt noch): neue Fassung kommt, bestätigte alte Kopie wird ersetzt
        self.datei(self.lager, "sessions/s/analyse.json", b'{"neu": true}', alter_s=1800)
        code, e = self.uebernehmen()
        self.assertEqual((code, e["kopiert"], e["gleich"]), (0, 1, 3))
        self.assertEqual((self.puffer / "sessions/s/analyse.json").read_bytes(), b'{"neu": true}')
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM lager_laeufe WHERE art = 'uebernahme'").fetchone()[0], 3)

    def test_unbestaetigte_fassung_im_puffer_bleibt(self):
        self.datei(self.puffer, "replays/r.replay", b"anders")  # nicht aus dem Lager, nicht bestätigt
        self.datei(self.puffer, "highlights/h.mp4", b"highlight", alter_s=5)  # gleicher Inhalt, andere mtime
        code, e = self.uebernehmen()
        self.assertEqual(code, 1, e)
        self.assertEqual((e["konflikte"], e["konflikt_liste"], e["eingetragen"]), (1, ["replays/r.replay"], 1))
        self.assertEqual((self.puffer / "replays/r.replay").read_bytes(), b"anders")
        self.assertIsNone(self.zeile("replays/r.replay"))
        self.assertIsNotNone(self.zeile("highlights/h.mp4"))

    def test_junge_dateien_im_lager_nicht_still_weglassen(self):
        """R5: kurz vorher hat ein Pipeline-Schritt oder der PC noch ins Lager geschrieben. Die Übernahme lässt die
        Datei aus (evtl. halb fertig), sagt es aber und endet mit Exit 1 – sonst fehlte sie unbemerkt im Puffer."""
        self.konfig.daten["lager"]["ruhe_min"] = 10  # ctime aller Testdateien ist frisch: alles gilt als jung
        jung = ["eingang/neu.mp4", "replays/r.replay", "sessions/s/analyse.json", "highlights/h.mp4"]
        code, e = self.uebernehmen("--probelauf")  # der Probelauf zeigt es nur
        self.assertEqual((code, e["zu_jung"], e["kopieren"], e["zu_jung_liste"]), (0, 4, 0, jung))
        self.assertIn("wiederholen", e["hinweis"])
        code, e = self.uebernehmen()
        self.assertEqual(code, 1, e)
        # eingang/alt.mp4 ist trotz frischer ctime "eingang_alt" (mtime zählt für --eingang-tage), nicht "zu jung"
        self.assertEqual((e["kopiert"], e["zu_jung"], e["eingang_alt"], e["ok"]), (0, 4, 1, False))
        self.assertEqual(e["zu_jung_liste"], jung)
        self.assertIn("In ein paar Minuten wiederholen", e["hinweis"])
        self.assertFalse((self.puffer / "replays/r.replay").exists())
        lauf = json.loads(self.con.execute("SELECT ergebnis FROM lager_laeufe").fetchone()[0])
        self.assertEqual((lauf["ok"], lauf["zu_jung"]), (False, 4))
        # ein paar Minuten später: alles kommt
        self.konfig.daten["lager"]["ruhe_min"] = 0
        code, e = self.uebernehmen()
        self.assertEqual((code, e["kopiert"], e["zu_jung"], e["ok"]), (0, 4, 0, True))
        self.assertNotIn("hinweis", e)
        # übernommene Dateien bleiben "gleich", auch wenn ihre ctime frisch ist – nur die neue ist zu jung
        self.konfig.daten["lager"]["ruhe_min"] = 10
        self.datei(self.lager, "sessions/s2/analyse.json", b"{}", alter_s=60)
        code, e = self.uebernehmen()
        self.assertEqual((code, e["gleich"], e["zu_jung"], e["zu_jung_liste"]), (1, 4, 1, ["sessions/s2/analyse.json"]))
        self.assertFalse((self.puffer / "sessions/s2/analyse.json").exists())

    def test_probelauf(self):
        with mock.patch.object(lager, "_gb", side_effect=lambda n: n):  # Bytes statt GB: Testdateien sind winzig
            code, e = self.uebernehmen("--probelauf")
        self.assertEqual(code, 0, e)
        self.assertEqual((e["probelauf"], e["kopieren"], e["eingang_alt"]), (True, 4, 1))
        self.assertEqual(e["gb"], len(b"neu" * 100) + len(b"replay") + len(b"{}") + len(b"highlight"))
        self.assertEqual(self.geweckt, [])
        self.assertFalse((self.puffer / "replays" / "r.replay").exists())
        with mock.patch.object(konfig.Konfig, "lager_erreichbar", return_value=False):
            code, e = self.uebernehmen("--probelauf")
        self.assertEqual(code, 0)
        self.assertIn("weckt nicht", e["hinweis"])

    def test_verwechslung_nichts_kopiert(self):
        with mock.patch.object(konfig, "_dateisystem", ECHT_DATEISYSTEM):
            self.assertEqual(self.uebernehmen()[0], 2)
        (self.lager / ".clip-puffer").touch()  # Puffer-Marke im Lager
        code, e = self.uebernehmen()
        self.assertEqual(code, 2)
        self.assertIn("vertauscht", e["hinweis"])
        (self.lager / ".clip-puffer").unlink()
        # von/nach vertauscht: im "Puffer" fehlt .clip-puffer – sofort, ohne zu wecken
        code, e = self.lauf("lager", "uebernehmen", "--von", str(self.puffer), "--nach", str(self.lager))
        self.assertEqual(code, 2)
        self.assertEqual(self.geweckt, ["uebernahme", "uebernahme"])
        self.assertEqual({p.name for p in self.puffer.rglob("*") if p.is_file()}, {".clip-speicher", ".clip-puffer"})

    def test_danach_ist_im_abgleich_nichts_offen(self):
        self.assertEqual(self.uebernehmen()[0], 0)
        self.konfig.daten["lager"]["wurzel"] = str(self.lager)  # umgeschaltet
        offen = [o.relativ for o in lager.sammle(self.con, self.konfig)]
        self.assertEqual(offen, [])


if __name__ == "__main__":
    unittest.main()
