"""Mehrbenutzer, Stufe 2, Schritt 5: Lager für Freunde – fährt bei Florians Abgleich mit (M98–M101, M107, M125 ff.).

Drei Teile:
- Instanz mit Lager-Schalter ([instanz] lager = true): derselbe Abgleich wie bei Florian (`pipeline lager abgleich`,
  lager.py unverändert) sichert den Puffer des Freundes nach I/lager und gibt danach alte Rohvideos frei. I/lager spielt
  den per NFS gebundenen Unterordner freunde/<name>: Gerätenummer und Dateisystem-Typ per patch (wie
  tests/test_getrennt.MitLager). Kein NFS, Florians Marke sichtbar, die Marke eines anderen Freundes: nichts kopiert,
  nichts gelöscht. Ohne Schalter genau Stufe 1. Kein Netz, kein Wecken.
- Rundgang (deploy/benutzer/lager-freunde.sh) in einer Scheinwurzel mit Attrappen (systemctl, runuser, findmnt, date,
  sleep; pipeline schreibt nur mit; „pve-big“ ist ein TCP-Port auf 127.0.0.1): schläft pve-big, startet nichts und die
  Marke ist wieder gelöst; wach: Freunde nacheinander, Marke verlängert, Herzschlag, am Ende gelöst und
  zusammengefasst; er wartet, solange Florians Abgleich läuft; stillgelegt, ohne Schalter, nach 18 Uhr: kein Start.
- Florian unverändert: lager.py, big.py und clip-lager.service/.timer sind Zeichen für Zeichen die von main d72349f; bei
  ihm prüft pruefe_getrennt nichts davon.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import threading
import time
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

from clip_pipeline import big, cli, konfig as konfig_mod, lager
from clip_pipeline.konfig import KonfigFehler
from clip_pipeline.zeit import iso, jetzt

from tests.hilfen import als_instanz
from tests.test_getrennt import MitLager
from tests.test_instanz import MitInstanzen, abdruck

PROJEKT = Path(__file__).resolve().parents[1]
RUNDGANG = PROJEKT / "deploy" / "benutzer" / "lager-freunde.sh"
TAG = 86400


# --- Instanz mit Lager-Schalter ----------------------------------------------------------------------------------------

class MitLagerSchalter(MitInstanzen):
    """max mit [instanz] lager = true und dem Einhängepunkt I/lager; darin wie im Lager-Dienst sein Unterordner mit
    Marke .clip-lager-max (NFS per patch). eva und Florians Sperre liegen daneben – sie dürfen sich nie ändern."""

    def setUp(self):
        super().setUp()
        toml = self.max / "instanz.toml"
        toml.write_text(toml.read_text(encoding="utf-8") + "\n[instanz]\nlager = true\n", encoding="utf-8")
        self.lager = self.max / "lager"
        self.lager.mkdir()
        (self.lager / ".clip-lager-max").touch()
        self.typ = {self.lager: "nfs4"}   # was /proc/self/mountinfo im Lager-Dienst sagt
        for p in (mock.patch.object(konfig_mod, "_dateisystem",
                                    side_effect=lambda p: 2 if Path(p) == self.lager else 1),
                  mock.patch.object(konfig_mod, "_dateisystem_typ", side_effect=lambda p: self.typ.get(Path(p))),
                  # frisch geschriebene Testdateien zählen nicht als „wird noch geschrieben“ (ctime) – wie ruhe_min = 0
                  mock.patch.object(lager, "_ruhe_grenze", return_value=time.time() + 3600),
                  mock.patch("socket.create_connection", side_effect=AssertionError("Netzwerkzugriff!")),
                  mock.patch.object(konfig_mod, "sende_wake_on_lan", side_effect=AssertionError("Wake-on-LAN!")),
                  mock.patch.object(big, "sende_wake_on_lan", side_effect=AssertionError("Wake-on-LAN!"))):
            p.start()
            self.addCleanup(p.stop)

    def datei(self, rel: str, inhalt: bytes, alter_tage: float) -> Path:
        pfad = self.max / "daten" / rel
        pfad.parent.mkdir(parents=True, exist_ok=True)
        pfad.write_bytes(inhalt)
        t = time.time() - alter_tage * TAG
        os.utime(pfad, (t, t))
        return pfad

    def lauf(self, *argv: str) -> tuple[int, dict]:
        ausgabe = io.StringIO()
        with als_instanz(self.max), contextlib.redirect_stdout(ausgabe), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(list(argv))
        return code, json.loads(ausgabe.getvalue().strip().splitlines()[-1])

    def im_lager(self) -> set[str]:
        return {p.relative_to(self.lager).as_posix() for p in self.lager.rglob("*") if p.is_file()}

    def puffer(self) -> dict[str, tuple[int, int]]:
        return abdruck(self.max / "daten", ohne=self.max / "daten" / "sicherung")

    def db(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.max / "db" / "pipeline.db")
        self.addCleanup(con.close)
        return con


class AbgleichUndFreigabe(MitLagerSchalter):
    def test_drei_laeufe_kopieren_probe_freigabe(self):
        """Normaler Weg (wie sim_lager_freund.py): Lauf 1 kopiert Rohvideos, Replay und DB-Sicherung nur nach I/lager,
        Lauf 2 ist die Probe, Lauf 3 gibt genau das alte Video frei – junges Video und Replay bleiben."""
        k = self.lade(self.max)
        self.assertEqual((k.lager_wurzel, k.wert("lager.markierung"), k.wert("lager.warten_s"), k.wert("puffer.freigeben")),
                         (self.lager, ".clip-lager-max", 0, True))
        self.assertEqual([k.wert(n) for n in ("speicher.host", "speicher.wol_mac", "big.host", "big.ssh_ziel")], [""] * 4)
        alt = self.datei("eingang/nvidia/highlights/alt.mp4", b"a" * 4000, 20)
        jung = self.datei("eingang/nvidia/highlights/jung.mp4", b"j" * 4000, 1)
        replay = self.datei("replays/UnsavedReplay-2026.09.19-20.00.00.replay", b"r" * 100, 20)
        draussen = abdruck(self.tmp, ohne=self.max)

        code, e = self.lauf("lager", "abgleich")
        self.assertEqual(code, 0, e)
        self.assertEqual((e["fehler"], e["videos_uebertragen"], e.get("abbruch")), (0, 2, None))
        inhalt = self.im_lager()
        self.assertLessEqual({".clip-lager-max", "eingang/nvidia/highlights/alt.mp4",
                              "eingang/nvidia/highlights/jung.mp4", "replays/UnsavedReplay-2026.09.19-20.00.00.replay"},
                             inhalt)
        self.assertEqual(len([n for n in inhalt if n.startswith("sicherung/pipeline-")]), 1)
        self.assertEqual(abdruck(self.tmp, ohne=self.max), draussen)   # eva und Florian unverändert
        self.assertTrue(alt.exists() and jung.exists() and replay.exists())

        # Die Bestätigungen 15 Tage zurückdatieren (wie test_lager) – sonst ist nichts „seit 14 Tagen gesichert“
        vorher = iso(jetzt() - timedelta(days=15))
        con = self.db()
        con.execute("UPDATE lager SET bestaetigt = ?, zuerst_gesehen = ?", (vorher, vorher))
        con.commit()
        self.datei("eingang/nvidia/highlights/neu.mp4", b"n" * 4000, 0.05)   # etwas Neues: der Lauf braucht das Lager
        code, e = self.lauf("lager", "abgleich")
        self.assertEqual(code, 0, e)
        self.assertEqual((e["freigabe"]["probe"], e["freigabe"]["dateien"]), (True, 1))
        self.assertTrue(alt.exists())                                         # die Probe löscht nichts

        self.datei("eingang/nvidia/highlights/neu2.mp4", b"m" * 4000, 0.05)
        code, e = self.lauf("lager", "abgleich")
        self.assertEqual(code, 0, e)
        self.assertEqual((e["freigabe"]["probe"], e["freigabe"]["dateien"]), (False, 1))
        self.assertFalse(alt.exists())
        self.assertTrue(jung.exists() and replay.exists())
        self.assertEqual((self.lager / "eingang/nvidia/highlights/alt.mp4").read_bytes(), b"a" * 4000)
        self.assertEqual([z[0] for z in con.execute("SELECT art FROM ereignisse WHERE art LIKE 'puffer_%' ORDER BY id")],
                         ["puffer_probe", "puffer_frei"])
        self.assertEqual(abdruck(self.tmp, ohne=self.max), draussen)

    def test_pruefen_im_lager_dienst_gruen(self):
        code, e = self.lauf("lager", "pruefen")
        self.assertEqual((code, e["ok"], e["lager"]), (0, True, str(self.lager)))


class FalscherOrdner(MitLagerSchalter):
    """Wichtigster Fehlerfall: Statt seines NFS-Unterordners ist etwas anderes gebunden – nichts kopiert, nichts
    gelöscht, die Bestätigungen bleiben leer."""

    def setUp(self):
        super().setUp()
        self.alt = self.datei("eingang/nvidia/highlights/alt.mp4", b"a" * 4000, 20)
        self.datei("replays/UnsavedReplay-2026.09.19-20.00.00.replay", b"r" * 100, 20)

    def assertNichtsKopiert(self, code: int, e: dict, erwartet: int, text: str) -> None:
        self.assertEqual(code, erwartet, e)
        self.assertIn(text, e.get("hinweis", ""))
        self.assertLessEqual(self.im_lager(), {".clip-lager-max", ".clip-lager", ".clip-lager-eva"})
        self.assertTrue(self.alt.exists())
        self.assertEqual(self.db().execute("SELECT COUNT(*) FROM lager").fetchone()[0], 0)

    def test_kein_nfs(self):
        self.typ[self.lager] = "ext4"   # z. B. ein lokaler Ordner mit Marke statt des NFS
        vorher = self.puffer()
        code, e = self.lauf("lager", "abgleich")
        self.assertNichtsKopiert(code, e, 2, "kein NFS (ext4)")
        self.assertEqual({k: v for k, v in self.puffer().items()}, vorher)

    def test_florians_marke_sichtbar(self):
        (self.lager / ".clip-lager").touch()   # Florians Wurzel statt freunde/max gebunden
        code, e = self.lauf("lager", "abgleich")
        self.assertNichtsKopiert(code, e, 2, "das ist Florians Lager")
        code, e = self.lauf("lager", "pruefen")
        self.assertEqual(code, 2, e)

    def test_marke_eines_anderen_freundes(self):
        (self.lager / ".clip-lager-max").rename(self.lager / ".clip-lager-eva")   # freunde/eva gebunden
        code, e = self.lauf("lager", "abgleich")
        self.assertNichtsKopiert(code, e, 3, ".clip-lager-max fehlt")


class Schalter(MitInstanzen):
    """[instanz] lager: nur true/false, nur mit dem Einhängepunkt I/lager – ein echter Ordner, außerhalb des
    Lager-Dienstes root-eigen. Ohne Schalter genau Stufe 1."""

    def schreibe(self, zusatz: str) -> None:
        (self.max / "instanz.toml").write_text(f'[sperre]\ndatei = "{self.sperrdatei}"\n{zusatz}\n', encoding="utf-8")

    def test_ohne_schalter_genau_stufe_1(self):
        (self.max / "lager").mkdir()   # ein liegen gebliebener Einhängepunkt ändert nichts
        k = self.lade(self.max)
        self.assertEqual((k.lager_wurzel, k.wert("lager.markierung"), k.wert("puffer.freigeben"), k.wert("instanz.lager")),
                         (self.max / "kein-lager", ".clip-lager", False, False))
        ausgabe = io.StringIO()
        with als_instanz(self.max), contextlib.redirect_stdout(ausgabe), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["lager", "abgleich"]), 3)   # wie in Stufe 1: Lager nicht eingehängt
        self.assertEqual(os.listdir(self.max / "lager"), [])

    def test_werte_und_einhaengepunkt_geprueft(self):
        self.schreibe('[instanz]\nlager = "ja"')
        with self.assertRaisesRegex(KonfigFehler, r"\[instanz\]\.lager muss true oder false"):
            self.lade(self.max)
        self.schreibe("[instanz]\nlager = true")
        with self.assertRaisesRegex(KonfigFehler, "fehlt"):
            self.lade(self.max)
        (self.max / "lager").symlink_to(self.eva / "db")              # hinaus: schon der Pfadwächter
        with self.assertRaisesRegex(KonfigFehler, "außerhalb der Instanz"):
            self.lade(self.max)
        (self.max / "lager").unlink()
        (self.max / "lager").symlink_to(self.max / "regie")           # auch innen nie ein Link
        with self.assertRaisesRegex(KonfigFehler, "kein echter Ordner"):
            self.lade(self.max)
        (self.max / "lager").unlink()
        (self.max / "lager").mkdir()
        with mock.patch.object(konfig_mod, "ROOT_UID", os.getuid() + 1), \
                self.assertRaisesRegex(KonfigFehler, "gehört nicht root"):
            self.lade(self.max)
        # das NFS hängt gerade (soft mount: EIO) – Klartext statt Absturz beim Laden
        with mock.patch.object(konfig_mod, "_dateisystem", side_effect=OSError(5, "Eingabe-/Ausgabefehler")), \
                self.assertRaisesRegex(KonfigFehler, "nicht lesbar"):
            self.lade(self.max)

    def test_andere_dienste_sehen_nur_den_leeren_einhaengepunkt(self):
        """Bot, scan und abend laden die Konfig, aber auf dem Lager-Weg ist I/lager dort kein NFS: nichts kopiert."""
        self.schreibe("[instanz]\nlager = true")
        (self.max / "lager").mkdir()
        with mock.patch.object(konfig_mod, "ROOT_UID", os.getuid()):
            k = self.lade(self.max)
            k.pruefe_getrennt(mit_lager=False)   # die Puffer-Seite wie bisher
            with self.assertRaisesRegex(KonfigFehler, "kein NFS"):
                k.pruefe_getrennt()


class Mountinfo(unittest.TestCase):
    def test_typ_genau_am_einhaengepunkt_letzter_eintrag_zaehlt(self):
        with tempfile.TemporaryDirectory() as t:
            ziel = Path(os.path.realpath(t)) / "mit leer" / "lager"
            ziel.mkdir(parents=True)
            punkt = str(ziel).replace(" ", "\\040")
            info = Path(t) / "mountinfo"
            info.write_text("22 1 8:1 / / rw,relatime shared:1 - ext4 /dev/sda1 rw\n"
                            f"40 22 0:50 /freunde/max {punkt} rw,relatime master:5 - nfs4 192.0.2.5:/tank/clips rw\n",
                            encoding="utf-8")
            with mock.patch.object(konfig_mod, "MOUNTINFO", info):
                self.assertEqual(konfig_mod._dateisystem_typ(ziel), "nfs4")
                self.assertIsNone(konfig_mod._dateisystem_typ(ziel.parent))   # kein Einhängepunkt
                with info.open("a", encoding="utf-8") as datei:
                    datei.write(f"41 40 0:51 / {punkt} rw - tmpfs tmpfs rw\n")
                self.assertEqual(konfig_mod._dateisystem_typ(ziel), "tmpfs")


# --- Florian unverändert ---------------------------------------------------------------------------------------------

class FlorianUnveraendert(MitLager):
    # Stand main d72349f (Plan Stufe 2: Florians Lager-Code bleibt). Ändert ihn später jemand mit Absicht, hier die neue
    # Prüfsumme eintragen.
    PRUEFSUMMEN = {
        "src/clip_pipeline/lager.py": "65301e3ea93a498447f0d27d58871d5ca6b499e05fad515169c503fec0b16f8a",
        "src/clip_pipeline/big.py": "2cb454e11d69a0e654c0e43e6e4597ea2fc98f44c3c1ef015b66dec6d2cb5a9f",
        "deploy/systemd/clip-lager.service": "2bedc9874ebd51e94ec24e430ec90f6e9aa8aacc5d6d53398b6024b7cd0e7dd7",
        "deploy/systemd/clip-lager.timer": "d9bae91b2ce2bd968bb058b193a00c5e491a8ddb15571444a0925a921020a4d8",
    }

    def test_lager_code_wie_main(self):
        for datei, summe in self.PRUEFSUMMEN.items():
            inhalt = (PROJEKT / datei).read_bytes().replace(b"\r\n", b"\n")
            with self.subTest(datei):
                self.assertEqual(hashlib.sha256(inhalt).hexdigest(), summe)

    def test_bei_florian_keine_pruefung_der_freunde(self):
        with mock.patch.object(konfig_mod, "_dateisystem_typ", side_effect=AssertionError("Typ-Prüfung bei Florian")):
            self.konfig.pruefe_getrennt()
        self.assertIsNone(self.konfig.instanz)


# --- Rundgang in der Scheinwurzel -------------------------------------------------------------------------------------

# Attrappen: jeder Aufruf landet in $STUB/aufrufe. Florians Abgleich ist so oft „activating“, wie $STUB/florian sagt;
# ein scan-Timer läuft, wenn der Name in $STUB/scan steht. start clip-freund-lager@<name> merkt sich, was in dem
# Moment galt (Florians Abgleich, Herzschlag-Dateien, bisherige pipeline-Aufrufe), ExecMainStatus kommt aus
# $STUB/exit-<name>. findmnt meldet das NFS nur mit $STUB/nfs, date +%H sagt $STUNDE.
RUNDGANG_STUBS = {
    "id": '[ "$1" = -u ] && echo 0',
    "runuser": 'while [ $# -gt 0 ]; do case "$1" in -u) shift 2;; --) shift; break;; *) break;; esac; done\nexec "$@"',
    "findmnt": '[ -f "$STUB/nfs" ] && cat "$STUB/nfs"',
    "chown": "",
    "date": 'case "$*" in *+%H*) echo "${STUNDE:-12}" ;; *) PATH=/usr/bin:/bin exec date "$@" ;; esac',
    "sleep": "PATH=/usr/bin:/bin exec sleep 0.05",
    "systemctl": r'''case "$1" in
  is-active) case "$2" in
      clip-lager.service) n=$(cat "$STUB/florian" 2>/dev/null || echo 0)
        if [ "$n" -gt 0 ]; then echo $((n - 1)) > "$STUB/florian"; echo activating; else echo inactive; fi ;;
      clip-freund-scan@*) n="${2#clip-freund-scan@}"
        if grep -qx "${n%.timer}" "$STUB/scan" 2>/dev/null; then echo active; else echo inactive; fi ;;
      *) echo inactive ;;
    esac ;;
  list-jobs) cat "$STUB/jobs" 2>/dev/null ;;
  cat) [ ! -f "$STUB/ohne-vorlage" ] ;;
  start) n="${2#clip-freund-lager@}"; n="${n%.service}"
    PATH=/usr/bin:/bin sleep 0.3
    { echo "florian=$(cat "$STUB/florian" 2>/dev/null || echo 0)"; ls "$LAGER_NFS/.aktiv" 2>/dev/null
      cat "$STUB/pipeline" 2>/dev/null; } > "$STUB/start-$n" ;;
  show) n="${5#clip-freund-lager@}"; cat "$STUB/exit-${n%.service}" 2>/dev/null || echo 0 ;;
esac''',
}


class Rundgang(unittest.TestCase):
    """lager-freunde.sh mit vier Freunden: eva und max mit Lager und laufendem scan-Timer, still mit Lager, aber
    stillgelegt, ohne (kein Schalter), kaputt (Schalter, aber kein Einhängepunkt)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.t = t = Path(os.path.realpath(self._tmp.name))
        self.stub = t / "stub"
        self.stub.mkdir()
        for name, inhalt in RUNDGANG_STUBS.items():
            datei = self.stub / name
            datei.write_text(f'#!/bin/bash\necho "{name} $*" >> "$STUB/aufrufe"\n{inhalt}\n', encoding="utf-8")
            datei.chmod(0o755)
        pipeline = t / "prod/.venv/bin/pipeline"
        pipeline.parent.mkdir(parents=True)
        pipeline.write_text('#!/bin/sh\necho "$*" >> "$STUB/pipeline"\n', encoding="utf-8")
        pipeline.chmod(0o755)
        self.benutzer = t / "benutzer"
        for name, schalter, einhaengepunkt in (("eva", True, True), ("max", True, True), ("still", True, True),
                                               ("ohne", False, True), ("kaputt", True, False)):
            inst = self.benutzer / name
            inst.mkdir(parents=True)
            (inst / ".clip-benutzer").write_text(f"{name}\n", encoding="utf-8")
            (inst / "instanz.toml").write_text('[sperre]\ndatei = "/x"\n' + ("[instanz]\nlager = true\n" if schalter
                                                                             else ""), encoding="utf-8")
            if einhaengepunkt:
                (inst / "lager").mkdir()
        (self.stub / "scan").write_text("eva\nmax\nkaputt\nohne\n", encoding="utf-8")
        self.florian = t / "florian"
        self.florian.mkdir()
        self.nfs = t / "nfs/clips"
        self.nfs.mkdir(parents=True)
        (self.nfs / ".clip-lager").touch()
        (self.nfs / "eingang").mkdir()
        # „pve-big“: ein offener Port auf 127.0.0.1 (der Rundgang klopft nur kurz an)
        lauscher = socket.socket()
        lauscher.bind(("127.0.0.1", 0))
        lauscher.listen(16)
        self.addCleanup(lauscher.close)
        threading.Thread(target=self._annehmen, args=(lauscher,), daemon=True).start()
        self.env = {**os.environ, "PATH": f"{self.stub}:{os.environ['PATH']}", "STUB": str(self.stub),
                    "BENUTZER_DIR": str(self.benutzer), "PROD": str(t / "prod"), "FLORIAN_DIR": str(self.florian),
                    "LAGER_NFS": str(self.nfs), "NFS_PORT": str(lauscher.getsockname()[1]), "WARTE_S": "300",
                    "HERZ_S": "1"}

    @staticmethod
    def _annehmen(lauscher: socket.socket) -> None:
        while True:
            try:
                verbindung, _ = lauscher.accept()
            except OSError:
                return
            verbindung.close()

    def wach(self) -> None:
        (self.stub / "nfs").write_text("127.0.0.1:/tank/clips\n", encoding="utf-8")

    def lauf(self, *argumente: str, **umgebung: str) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", str(RUNDGANG), *argumente], capture_output=True, text=True,
                              env={**self.env, **umgebung}, timeout=120)

    def lies(self, name: str) -> str:
        datei = self.stub / name
        return datei.read_text(encoding="utf-8") if datei.exists() else ""

    def zusammenfassung(self) -> dict:
        return json.loads((self.florian / "lager-freunde.json").read_text(encoding="utf-8"))

    def test_skript(self):
        text = RUNDGANG.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("#!/usr/bin/env bash\n"))
        self.assertIn("\nset -euo pipefail\n", text)
        self.assertTrue(os.access(RUNDGANG, os.X_OK))
        # löscht nichts außer dem eigenen Herzschlag, weckt nie, ruft nie Florians Abgleich
        self.assertIsNone(re.search(r"\b(rmdir|unlink|shred|lvremove|wipefs|mkfs)\b|--delete|--purge", text))
        self.assertTrue(all('"$HERZ"' in z for z in text.splitlines() if re.search(r"\brm\b", z)))
        for verboten in ("wakeonlan", "etherwake", "wol_mac", "lager abgleich", "big aus", "big waechter"):
            self.assertNotIn(verboten, text)
        r = subprocess.run(["bash", "-n", str(RUNDGANG)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        if shutil.which("shellcheck"):
            r = subprocess.run(["shellcheck", "-S", "warning", str(RUNDGANG)], capture_output=True, text=True,
                               env={**os.environ, "LC_ALL": "C.UTF-8"})
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_pve_big_schlaeft_nichts_gestartet_marke_geloest(self):
        r = self.lauf()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.lies("pipeline").splitlines(),
                         ["big halten freunde --minuten 30 --grund Freunde fahren beim Lager-Abgleich mit",
                          "big loesen freunde"])
        self.assertNotIn("systemctl start", self.lies("aufrufe"))
        self.assertFalse((self.nfs / "freunde").exists())
        self.assertFalse((self.nfs / ".aktiv").exists())
        e = self.zusammenfassung()
        self.assertIn("pve-big schläft", e["lauf"]["ergebnis"])
        self.assertEqual(e["freunde"], {})
        self.assertIn("ich wecke nie", r.stdout)

    def test_wach_wartet_auf_florian_dann_nacheinander(self):
        """Florians Abgleich läuft noch zwei Blicke lang: warten, die Marke dabei verlängern. Danach eva und max
        nacheinander (Ordner und Marke im Lager, Marke 130 min, Herzschlag), still/ohne/kaputt nicht; am Ende Marke
        gelöst, Herzschlag weg, Zusammenfassung (ein Fehler bei eva wird vermerkt, ihr letzter guter Lauf bleibt)."""
        self.wach()
        (self.stub / "florian").write_text("2", encoding="utf-8")
        (self.stub / "exit-eva").write_text("2\n", encoding="utf-8")
        (self.florian / "lager-freunde.json").write_text(json.dumps(
            {"freunde": {"eva": {"zuletzt_ok": "2026-10-01T10:00:00Z"}, "alt": {"ok": True}}}), encoding="utf-8")
        r = self.lauf()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        halten = "big halten freunde --minuten {} --grund Freunde fahren beim Lager-Abgleich mit"
        self.assertEqual(self.lies("pipeline").splitlines(),
                         [halten.format(30)] * 3 + [halten.format(130)] * 2 + ["big loesen freunde"])
        starts = [z for z in self.lies("aufrufe").splitlines() if z.startswith("systemctl start")]
        self.assertEqual(starts, ["systemctl start clip-freund-lager@eva.service",
                                  "systemctl start clip-freund-lager@max.service"])
        for name in ("eva", "max"):
            beim_start = self.lies(f"start-{name}")
            with self.subTest(name):
                self.assertIn("florian=0", beim_start)                       # erst nach Florians Abgleich
                self.assertRegex(beim_start, rf"-freund-{name}-\d+-\d+\n")   # Herzschlag für clip-leerlauf
                self.assertIn(halten.format(130), beim_start)                # Marke für 2 h gesetzt
                self.assertTrue((self.nfs / "freunde" / name / f".clip-lager-{name}").is_file())
        self.assertEqual(sorted(os.listdir(self.nfs / "freunde")), ["eva", "max"])
        self.assertEqual(os.listdir(self.nfs / ".aktiv"), [])                # Herzschlag wieder weg
        e = self.zusammenfassung()
        self.assertEqual(set(e["freunde"]), {"eva", "max", "alt"})
        self.assertEqual((e["freunde"]["eva"]["ok"], e["freunde"]["eva"]["exit"], e["freunde"]["eva"]["zuletzt_ok"]),
                         (False, 2, "2026-10-01T10:00:00Z"))
        self.assertEqual((e["freunde"]["max"]["ok"], e["freunde"]["max"]["zuletzt_ok"]),
                         (True, e["freunde"]["max"]["ende"]))
        self.assertEqual(e["lauf"]["ergebnis"], "fertig")
        self.assertIn("still: stillgelegt", r.stdout)
        self.assertIn("kaputt: Lager-Schalter an, aber", r.stdout)
        self.assertNotIn("ohne", r.stdout)

    def test_nach_18_uhr_kein_start(self):
        self.wach()
        r = self.lauf(STUNDE="18")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("systemctl start", self.lies("aufrufe"))
        self.assertEqual(self.lies("pipeline").splitlines()[-1], "big loesen freunde")
        self.assertIn("keine neuen Starts", self.zusammenfassung()["lauf"]["ergebnis"])
        self.assertFalse((self.nfs / "freunde").exists())

    def test_ohne_freund_mit_lager_gar_nichts(self):
        for name in ("eva", "max", "still", "kaputt"):
            shutil.rmtree(self.benutzer / name)
        self.wach()
        r = self.lauf()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.lies("pipeline"), "")                          # keine Marke
        self.assertFalse((self.florian / "lager-freunde.json").exists())
        self.assertNotIn("systemctl", self.lies("aufrufe"))

    def test_probe_aendert_nichts(self):
        self.wach()
        r = self.lauf("--probe")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for satz in ("eva: Lager an – fährt mit", "max: Lager an – fährt mit", "still: Lager an, aber stillgelegt",
                     "pve-big ist wach", "Probe fertig – nichts verändert"):
            self.assertIn(satz, r.stdout)
        self.assertEqual(self.lies("pipeline"), "")
        self.assertNotIn("systemctl start", self.lies("aufrufe"))
        self.assertFalse((self.nfs / "freunde").exists() or (self.florian / "lager-freunde.json").exists())

    def test_ordner_nur_bei_nfs_nie_ueber_einen_link(self):
        r = self.lauf("--nfs")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("nicht per NFS eingehängt", r.stdout)
        r = self.lauf("--ordner", "max")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertFalse((self.nfs / "freunde").exists())
        self.wach()
        self.assertEqual(self.lauf("--nfs").returncode, 0)
        r = self.lauf("--ordner", "max")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((self.nfs / "freunde/max/.clip-lager-max").is_file())
        anderswo = self.t / "anderswo"
        anderswo.mkdir()
        (self.nfs / "freunde/eva").symlink_to(anderswo)
        r = self.lauf("--ordner", "eva")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("kein Ordner (Link?)", r.stdout)
        self.assertEqual(os.listdir(anderswo), [])
        self.assertEqual(self.lauf("--ordner", "../x").returncode, 2)


if __name__ == "__main__":
    unittest.main()
