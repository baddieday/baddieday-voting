"""Mehrbenutzer, Stufe 1, Schritt 5 und 6: Dienst-Vorlagen je Freund mit Sandbox, das Update kennt Freunde, Speicher.

Vier Teile:
- Vorlagen (deploy/benutzer): derselbe Sandbox-Block in jeder Vorlage, eigener Benutzer, eigener Ordner, Florians
  Bereiche verdeckt, kein Schreibpfad außerhalb des eigenen Ordners; systemd kennt jeden Schlüssel.
- Kernel-Nachbau (nur als root mit Mount-Namensräumen, sonst übersprungen): die Mounts des Sandbox-Blocks in einem
  privaten Namensraum über einer Scheinwurzel – eine fremde uid sieht nur die Sperrdatei, schreiben darf sie sie nicht,
  und flock über O_RDONLY wartet auf Florian und umgekehrt. Nichts davon berührt das echte System.
- Update (INNEN-Teil von alles-aktualisieren.sh mit Attrappen wie tests/test_deploy_publikum.py): Freundes-Datenbank
  vorher gesichert, Vorlagen auf der Platte, nie eingeschaltet, laufende Freundes-Bots neu; ohne Freunde keine Änderung.
- Freunde-Volume (deploy/pve-mini/freunde-volume.sh, Schritt 6) mit Attrappen wie tests/test_deploy_puffer.py: Probe
  ändert nichts, Pool-Grenze mit vollem Puffer, zweiter Lauf überspringt Fertiges, der Rückweg hängt nur aus.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

from clip_pipeline import cli, konfig, sperre

from tests.test_deploy_puffer import (CT_ZUSTAND, DEPLOY, KONFIG, PROJEKT, STUBS, MitStubs, assertReihenfolge,
                                      lies_unit)

BENUTZER = DEPLOY / "benutzer"
UPDATE = DEPLOY / "pve-mini" / "alles-aktualisieren.sh"
# Alle Vorlagen im Ordner – auch spätere (einrichten@, pruefen@) bekommen so dieselben Prüfungen
DIENSTE = {p.name.split("@")[0]: p for p in sorted(BENUTZER.glob("clip-freund-*@.service"))}
TIMER = {p.name.split("@")[0]: p for p in sorted(BENUTZER.glob("clip-freund-*@.timer"))}
VORLAGEN = [*DIENSTE.values(), *TIMER.values()]
PIPELINE = "/opt/clip-pipeline/.venv/bin/pipeline"
INSTANZ = "/var/lib/clip-benutzer/%i"
BLOCK = re.compile(r"^# --- Sandbox:.*?^# --- Ende Sandbox ---$", flags=re.M | re.S)


def sandbox(pfad: Path) -> str:
    treffer = BLOCK.search(pfad.read_text(encoding="utf-8"))
    if treffer is None:
        raise AssertionError(f"{pfad.name}: Sandbox-Block fehlt")
    return treffer.group(0)


def werte(text: str) -> dict[str, list[str]]:
    """Schlüssel=Wert-Zeilen (ohne Kommentare); mehrfache Schlüssel als Liste, Leerzeichen trennt einzelne Werte."""
    ergebnis: dict[str, list[str]] = {}
    for zeile in text.splitlines():
        zeile = zeile.strip()
        if zeile and not zeile.startswith(("#", ";", "[")) and "=" in zeile:
            schluessel, wert = zeile.split("=", 1)
            ergebnis.setdefault(schluessel.strip(), []).extend(wert.split())
    return ergebnis


def ohne_minus(pfade: list[str]) -> list[str]:
    return [p.removeprefix("-") for p in pfade]


def innen_teil() -> str:
    """Der Teil von alles-aktualisieren.sh, der im CT unter der Sperre läuft (bash -c "$INNEN")."""
    return re.search(r"^INNEN='\n(.*?)^'$", UPDATE.read_text(encoding="utf-8"), flags=re.M | re.S).group(1)


class Vorlagen(unittest.TestCase):
    def test_sandbox_in_jeder_vorlage_woertlich_gleich(self):
        self.assertLessEqual({"clip-freund-bot", "clip-freund-scan", "clip-freund-abend"}, set(DIENSTE))
        self.assertEqual(set(TIMER), {"clip-freund-scan", "clip-freund-abend"})
        bloecke = {d.name: sandbox(d) for d in DIENSTE.values()}
        self.assertEqual(len(set(bloecke.values())), 1, "Sandbox-Block weicht ab")
        s = werte(next(iter(bloecke.values())))
        self.assertEqual(s["User"], ["clip-%i"])
        self.assertEqual(s["Group"], ["clip-%i"])
        # %i, nie %I – %I machte aus „max-2“ den Pfad „max/2“
        self.assertEqual(s["Environment"], [f"CLIP_INSTANZ={INSTANZ}", f"HOME={INSTANZ}/cache",
                                            f"XDG_CACHE_HOME={INSTANZ}/cache", f"HF_HOME={INSTANZ}/cache/huggingface"])
        for schluessel, wert in (("ProtectSystem", "strict"), ("ProtectHome", "true"), ("PrivateTmp", "true"),
                                 ("NoNewPrivileges", "true"), ("WorkingDirectory", "/opt/clip-pipeline"),
                                 ("SupplementaryGroups", "render"), ("Nice", "10"), ("CPUWeight", "50"),
                                 ("MemoryMax", "3G")):
            with self.subTest(schluessel):
                self.assertEqual(s[schluessel], [wert])
        self.assertEqual(s["TemporaryFileSystem"], ["/srv:ro", "/var/lib/clip-pipeline:ro", "/var/lib/clip-benutzer:ro",
                                                    "/opt/clip-pipeline/config:ro"])
        self.assertEqual(s["BindPaths"], [INSTANZ])
        self.assertEqual(s["BindReadOnlyPaths"], ["/var/lib/clip-pipeline/pipeline.lock",
                                                  "/opt/clip-pipeline/config/pipeline.toml"])
        self.assertEqual(s["InaccessiblePaths"], ["-/opt/clip-pipeline/.env", "-/opt/clip-regie"])

    def test_kein_schreibpfad_ausserhalb_des_eigenen_ordners(self):
        for vorlage in DIENSTE.values():
            s = werte(vorlage.read_text(encoding="utf-8"))
            with self.subTest(vorlage.name):
                # Schreiben nur über BindPaths, und nur in den eigenen Ordner; keine von systemd angelegten Ordner
                for schluessel in ("ReadWritePaths", "StateDirectory", "CacheDirectory", "LogsDirectory",
                                   "RuntimeDirectory", "ConfigurationDirectory", "EnvironmentFile"):
                    self.assertNotIn(schluessel, s)
                self.assertTrue(all(p == INSTANZ or p.startswith(INSTANZ + "/") for p in ohne_minus(s["BindPaths"])))
                self.assertTrue(all(p.endswith(":ro") for p in s["TemporaryFileSystem"]))
                # keine Pfade oder Zugänge von außen: die Pipeline liest sie selbst aus I/.env und instanz.toml
                namen = [e.split("=", 1)[0] for e in s["Environment"]]
                self.assertEqual(namen, ["CLIP_INSTANZ", "HOME", "XDG_CACHE_HOME", "HF_HOME"])

    def test_florians_bereiche_sind_verdeckt(self):
        s = werte(sandbox(DIENSTE["clip-freund-bot"]))
        weg = [p.split(":")[0] for p in s["TemporaryFileSystem"]] + ohne_minus(s["InaccessiblePaths"])
        if s["ProtectHome"] == ["true"]:
            weg += ["/home", "/root"]   # dort liegen Florians Claude-Anmeldung, Whisper-Modell und Schlüssel
        for bereich in (*konfig.FLORIAN_BEREICHE, *konfig.CLAUDE_VERBOTEN, "/opt/clip-pipeline/.env"):
            with self.subTest(bereich):
                self.assertTrue(any(bereich == p or bereich.startswith(p + "/") for p in weg), bereich)
        # sichtbar bleibt aus Florians Ordner nur die Sperrdatei, aus der Konfig nur das Startwissen
        for pfad in s["BindReadOnlyPaths"]:
            self.assertIn(str(Path(pfad).parent), weg)

    def test_sperre_ist_florians_datei(self):
        """Der fest eingebundene Sperrpfad = Florians sperre.pfad (Repo-Konfig, [sperre].datei leer, M2/M3) = der Pfad
        aus der Vorlage config/instanz.beispiel.toml. Sonst rechnete ein Freund neben Florian her."""
        self.assertEqual(KONFIG["sperre"]["datei"], "")
        florian = sperre.pfad(konfig.Konfig(KONFIG, PROJEKT / "config" / "pipeline.toml"))
        beispiel = tomllib.loads((PROJEKT / "config" / "instanz.beispiel.toml").read_text(encoding="utf-8"))
        gebunden = werte(sandbox(DIENSTE["clip-freund-bot"]))["BindReadOnlyPaths"]
        self.assertEqual(str(florian), "/var/lib/clip-pipeline/pipeline.lock")
        self.assertEqual(beispiel["sperre"]["datei"], str(florian))
        self.assertIn(str(florian), gebunden)
        # das Startwissen ist genau die Datei, die lade_instanz liest
        self.assertIn("/opt/clip-pipeline/" + konfig.STANDARD_KONFIG.relative_to(konfig.PROJEKT).as_posix(), gebunden)

    def test_befehle_wie_geplant(self):
        parser = cli.baue_parser()
        befehle = {}
        for name, vorlage in DIENSTE.items():
            s = werte(vorlage.read_text(encoding="utf-8"))
            self.assertEqual(s["ExecStart"][0], PIPELINE, name)
            befehle[name] = parser.parse_args(s["ExecStart"][1:])   # jeden Schalter gibt es wirklich
        self.assertIs(befehle["clip-freund-bot"].fn, cli._cmd_lernbot)
        scan = befehle["clip-freund-scan"]
        self.assertEqual((scan.fn, scan.verarbeiten, scan.max, scan.versuche), (cli._cmd_scan, True, 1, 3))
        abend = befehle["clip-freund-abend"]
        self.assertIs(abend.fn, cli._cmd_sitzungen)
        self.assertFalse(abend.ohne_claude)   # KI nur mit eigenem Zugang – ohne Token startet claude ohnehin nie (M30)
        for name in ("clip-freund-scan", "clip-freund-abend"):
            s = lies_unit(DIENSTE[name])
            with self.subTest(name):
                self.assertTrue(befehle[name].sperren)                   # rechnet nur unter der einen Sperre
                self.assertEqual(s["Type"], ["oneshot"])
                self.assertEqual(s["SuccessExitStatus"], ["3 4"])        # 4 = Sperre belegt: nächster Timer-Lauf
                self.assertNotIn("WantedBy", s)                           # startet nur über seinen Timer
        bot = lies_unit(DIENSTE["clip-freund-bot"])
        self.assertEqual((bot["Type"], bot["Restart"], bot["WantedBy"]),
                         (["simple"], ["always"], ["multi-user.target"]))

    def test_timer(self):
        for name, abstand in (("clip-freund-scan", "5min"), ("clip-freund-abend", "10min")):
            t = lies_unit(TIMER[name])
            with self.subTest(name):
                self.assertEqual(t["OnUnitActiveSec"], [abstand])
                self.assertEqual(t["WantedBy"], ["timers.target"])
                self.assertNotIn("Unit", t)   # startet den Dienst gleichen Namens mit derselben Instanz

    def test_kopf_sagt_wie_eingeschaltet_wird(self):
        for vorlage in VORLAGEN:
            text = vorlage.read_text(encoding="utf-8")
            with self.subTest(vorlage.name):
                self.assertIn("benutzer-anlegen.sh", text)
                self.assertNotIn("enable --now clip-freund", text)   # nicht von Hand einschalten
        for vorlage in DIENSTE.values():
            self.assertIn("alles-aktualisieren.sh", vorlage.read_text(encoding="utf-8"))
            self.assertIn("journalctl -u clip-freund-", vorlage.read_text(encoding="utf-8"))

    @unittest.skipUnless(shutil.which("systemd-analyze"), "systemd-analyze fehlt")
    def test_systemd_kennt_jeden_schluessel(self):
        """systemd-analyze verify je Instanz „max“ – ein Tippfehler im Schlüssel wäre sonst nur eine stille Warnung,
        und die Sandbox fehlte. ExecStart zeigt dafür auf /bin/true (den Code gibt es hier nicht unter /opt)."""
        with tempfile.TemporaryDirectory() as ordner:
            for vorlage in VORLAGEN:
                text = vorlage.read_text(encoding="utf-8").replace(f"ExecStart={PIPELINE}", "ExecStart=/bin/true")
                (Path(ordner) / vorlage.name).write_text(text, encoding="utf-8")
            for vorlage in VORLAGEN:
                instanz = Path(ordner) / vorlage.name.replace("@.", "@max.")
                r = subprocess.run(["systemd-analyze", "verify", str(instanz)], capture_output=True, text=True,
                                   timeout=120)
                eigene = [z for z in (r.stdout + r.stderr).splitlines() if "clip-freund" in z]
                with self.subTest(vorlage.name):
                    self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                    self.assertEqual(eigene, [])


# --- Kernel-Nachbau ---------------------------------------------------------------------------------------------------

PIPELINE_UID, FREUND_UID, EVA_UID = 64601, 64602, 64603   # frei gewählt, ohne Benutzer-Eintrag

SICHT = """
import errno, fcntl, importlib.util, json, os, sys
from pathlib import Path
u, sperre_py = sys.argv[1], sys.argv[2]
spec = importlib.util.spec_from_file_location("sperre", sperre_py)
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
erg = {n: sorted(os.listdir(u + p)) for n, p in (("florian", "/var/lib/clip-pipeline"),
       ("freunde", "/var/lib/clip-benutzer"), ("srv", "/srv"), ("config", "/opt/clip-pipeline/config"))}
sperrdatei = u + "/var/lib/clip-pipeline/pipeline.lock"
try:
    os.close(os.open(sperrdatei, os.O_RDWR)); erg["rdwr"] = "ok"
except OSError as e:
    erg["rdwr"] = errno.errorcode[e.errno]
fd = m.oeffne(Path(sperrdatei))
erg["oeffne"] = "nur lesen" if fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDONLY else "schreiben"
os.close(fd)
Path(u + "/var/lib/clip-benutzer/max/db/probe.txt").write_text("max")
erg["eigen"] = "geschrieben"
print(json.dumps(erg), flush=True)
"""

SPERRE = """
import importlib.util, sys
spec = importlib.util.spec_from_file_location("sperre", sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
datei, modus = sys.argv[2], sys.argv[3]
if modus == "halten":
    with m.sperre(datei, warten_s=0):
        print("haelt", flush=True)
        sys.stdin.readline()
else:
    try:
        with m.sperre(datei, warten_s=0):
            print("frei", flush=True)
    except m.Gesperrt:
        print("belegt", flush=True)
        if modus == "warten":
            with m.sperre(datei, warten_s=30):
                print("bekommen", flush=True)
"""


def _namensraum_moeglich() -> str | None:
    """Grund, warum der Kernel-Nachbau hier nicht geht – oder None."""
    if sys.platform != "linux" or os.geteuid() != 0:
        return "nur als root unter Linux"
    for programm in ("unshare", "setpriv", "mount", "timeout"):
        if not shutil.which(programm):
            return f"{programm} fehlt"
    r = subprocess.run(["unshare", "-m", "--propagation", "private", "true"], capture_output=True, timeout=30)
    return None if r.returncode == 0 else "keine Mount-Namensräume"


class KernelNachbau(unittest.TestCase):
    """Die Mounts des Sandbox-Blocks (aus der Vorlage gelesen, %i = max) in einem privaten Mount-Namensraum über einer
    Scheinwurzel – wie systemd sie anlegt: Quellen aus der normalen Sicht, Ziele in einer eigenen Unit-Wurzel. Florians
    Ordner hat die Rechte von heute (0755, Datenbank 0644): geschützt wird durch die Sandbox, nicht durch Rechte."""

    @classmethod
    def setUpClass(cls):
        if grund := _namensraum_moeglich():
            raise unittest.SkipTest(grund)
        cls._tmp = tempfile.TemporaryDirectory(prefix="clip-kern-")
        r = Path(cls._tmp.name)
        if any(not os.stat(p).st_mode & 0o001 for p in r.parents):
            cls._tmp.cleanup()
            raise unittest.SkipTest(f"{r.parent} ist für fremde Benutzer nicht erreichbar")
        os.chmod(r, 0o755)
        cls.host, cls.unit, werkzeug = r / "host", r / "unit", r / "werkzeug"

        def ordner(pfad: Path, uid: int = 0, gid: int | None = None, modus: int = 0o755):
            neu = [teil for teil in (*reversed(pfad.parents), pfad) if not teil.exists()]
            for teil in neu:
                teil.mkdir()
                os.chmod(teil, 0o755)   # neue Zwischenordner: für alle betretbar (unabhängig von der umask)
            os.chown(pfad, uid, uid if gid is None else gid)
            os.chmod(pfad, modus)

        def datei(pfad: Path, inhalt: str, uid: int = 0, modus: int = 0o644):
            pfad.write_text(inhalt, encoding="utf-8")
            os.chown(pfad, uid, uid)
            os.chmod(pfad, modus)

        p = PIPELINE_UID
        florian = cls.host / "var/lib/clip-pipeline"
        ordner(florian, p)
        datei(florian / "pipeline.lock", "", p)
        datei(florian / "pipeline.db", "florian-geheim", p)
        ordner(florian / "claude", p, modus=0o700)
        ordner(cls.host / "srv/puffer", p)
        datei(cls.host / "srv/puffer/aufnahme.mp4", "florian-geheim", p)
        ordner(cls.host / "opt/clip-pipeline/config", p)
        datei(cls.host / "opt/clip-pipeline/config/pipeline.toml", "[sperre]\n", p)
        datei(cls.host / "opt/clip-pipeline/config/lokal.toml", "florian-geheim", p)
        ordner(cls.host / "var/lib/clip-benutzer", modus=0o711)
        for name, uid in (("max", FREUND_UID), ("eva", EVA_UID)):
            inst = cls.host / "var/lib/clip-benutzer" / name
            ordner(inst, 0, uid, 0o750)          # root:clip-<name> 0750 wie im Plan
            ordner(inst / "db", uid, modus=0o700)
            datei(inst / "db" / "pipeline.db", f"{name}-geheim", uid, 0o600)
        ordner(cls.unit)
        ordner(werkzeug)
        for name, inhalt in (("sicht.py", SICHT), ("sperre_probe.py", SPERRE),
                             ("sperre.py", Path(sperre.__file__).read_text(encoding="utf-8"))):
            datei(werkzeug / name, inhalt)
        cls.sperre_py, cls.sicht_py, cls.probe_py = (str(werkzeug / n) for n in ("sperre.py", "sicht.py",
                                                                                    "sperre_probe.py"))
        cls.python = os.path.realpath(sys.executable)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def mounts(self) -> str:
        """Die Mount-Befehle des Sandbox-Blocks: tmpfs, eigene Bindungen, Sperrdatei und Startwissen nur lesbar."""
        s = werte(sandbox(DIENSTE["clip-freund-bot"]).replace("%i", "max"))
        h, u = (shlex.quote(str(x)) for x in (self.host, self.unit))
        leer = [e.removesuffix(":ro") for e in s["TemporaryFileSystem"]]
        z = ["set -euo pipefail", f"mount --rbind {h} {u}"]
        z += [f"mount -t tmpfs -o mode=0755 tmpfs {u}{p}" for p in leer]
        for p in s["BindPaths"]:
            z += [f"mkdir -p {u}{p}", f"mount --bind {h}{p} {u}{p}"]
        for p in s["BindReadOnlyPaths"]:
            z += [f"if [ -d {h}{p} ]; then mkdir -p {u}{p}; else touch {u}{p}; fi", f"mount --bind {h}{p} {u}{p}",
                  f"mount -o remount,bind,ro {u}{p}"]
        z += [f"mount -o remount,ro {u}{p}" for p in leer]
        return "\n".join([*z, 'exec "$@"'])

    def als_freund(self, *argumente: str) -> list[str]:
        return ["timeout", "60", "unshare", "-m", "--propagation", "private", "bash", "-c", self.mounts(), "ns",
                "setpriv", f"--reuid={FREUND_UID}", f"--regid={FREUND_UID}", "--clear-groups", "--no-new-privs",
                "--", self.python, "-I", "-B", *argumente]

    def als_florian(self, *argumente: str) -> list[str]:
        return ["timeout", "60", "setpriv", f"--reuid={PIPELINE_UID}", f"--regid={PIPELINE_UID}", "--clear-groups",
                "--", self.python, "-I", "-B", *argumente]

    def starte(self, befehl: list[str]) -> subprocess.Popen:
        proc = subprocess.Popen(befehl, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True)

        def weg():
            proc.kill()
            try:
                proc.communicate(timeout=30)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                pass

        self.addCleanup(weg)
        return proc

    def test_freund_sieht_nur_sperrdatei_und_eigenen_ordner(self):
        r = subprocess.run(self.als_freund(self.sicht_py, str(self.unit), self.sperre_py), capture_output=True,
                           text=True, timeout=90)
        self.assertEqual(r.returncode, 0, r.stderr)
        sicht = json.loads(r.stdout.splitlines()[-1])
        self.assertEqual(sicht["florian"], ["pipeline.lock"])     # keine Datenbank, kein claude
        self.assertEqual(sicht["freunde"], ["max"])               # eva gibt es nicht
        self.assertEqual(sicht["srv"], [])                        # kein Puffer, kein Lager
        self.assertEqual(sicht["config"], ["pipeline.toml"])      # keine lokal.toml
        self.assertIn(sicht["rdwr"], ("EACCES", "EROFS"))         # Florians Sperrdatei nie zum Schreiben
        self.assertEqual(sicht["oeffne"], "nur lesen")            # sperre.oeffne fällt auf O_RDONLY zurück
        self.assertEqual(sicht["eigen"], "geschrieben")           # der eigene Ordner bleibt schreibbar
        self.assertEqual((self.host / "var/lib/clip-benutzer/max/db/probe.txt").read_text(encoding="utf-8"), "max")
        self.assertEqual(os.listdir(self.unit), [], "Namensraum nicht privat – Mount ist draußen sichtbar")

    def test_sperre_wirkt_in_beide_richtungen(self):
        florian_sperre = str(self.host / "var/lib/clip-pipeline/pipeline.lock")
        freund_sperre = str(self.unit / "var/lib/clip-pipeline/pipeline.lock")
        # Florian rechnet: der Freund bekommt die Sperre nicht und wartet, bis Florian fertig ist
        florian = self.starte(self.als_florian(self.probe_py, self.sperre_py, florian_sperre, "halten"))
        self.assertEqual(florian.stdout.readline().strip(), "haelt", florian.stderr.read() if florian.poll() else "")
        freund = self.starte(self.als_freund(self.probe_py, self.sperre_py, freund_sperre, "warten"))
        self.assertEqual(freund.stdout.readline().strip(), "belegt")
        florian.stdin.write("\n")
        florian.stdin.flush()
        self.assertEqual(freund.stdout.readline().strip(), "bekommen")
        self.assertEqual(freund.wait(timeout=60), 0)
        # der Freund rechnet (Sperre nur lesend geöffnet): Florian bekommt sie nicht
        freund = self.starte(self.als_freund(self.probe_py, self.sperre_py, freund_sperre, "halten"))
        self.assertEqual(freund.stdout.readline().strip(), "haelt")
        r = subprocess.run(self.als_florian(self.probe_py, self.sperre_py, florian_sperre, "versuch"),
                           capture_output=True, text=True, timeout=90)
        self.assertEqual(r.stdout.strip(), "belegt", r.stderr)
        freund.stdin.write("\n")
        freund.stdin.flush()
        self.assertEqual(freund.wait(timeout=60), 0)
        r = subprocess.run(self.als_florian(self.probe_py, self.sperre_py, florian_sperre, "versuch"),
                           capture_output=True, text=True, timeout=90)
        self.assertEqual(r.stdout.strip(), "frei", r.stderr)


# --- Update mit Freunden ----------------------------------------------------------------------------------------------

class UpdateMitFreunden(unittest.TestCase):
    """INNEN-Teil von alles-aktualisieren.sh gegen einen Mini-Checkout (deploy/systemd + deploy/benutzer); runuser,
    systemctl und getent sind Attrappen, die mitschreiben. Benutzer clip-max „gibt es“ (getent), runuser wechselt nicht
    wirklich den Benutzer – geprüft wird, dass es mit -u clip-max gerufen wird."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.t = Path(self._tmp.name)
        self.prod, self.units, self.stub, self.benutzer = (self.t / n for n in ("prod", "units", "stub", "benutzer"))
        for teil in ("systemd", "benutzer"):
            shutil.copytree(DEPLOY / teil, self.prod / "deploy" / teil)
        git = ["git", "-C", str(self.prod), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
               "-c", "commit.gpgsign=false"]
        for befehl in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "Stand"]):
            subprocess.run(git + befehl, check=True, capture_output=True)
        self.stand = subprocess.run(git + ["rev-parse", "HEAD"], check=True, capture_output=True,
                                    text=True).stdout.strip()
        self.units.mkdir()
        for name in ("clip-bot.service", "clip-lernbot.service"):
            shutil.copy(DEPLOY / "systemd" / name, self.units / name)
        self.stub.mkdir()
        stubs = {"runuser": 'echo "runuser $*" >> "$STUB/aufrufe"\n'
                            'while [ $# -gt 0 ]; do case "$1" in -u) shift 2;; --) shift; break;; *) break;; esac; '
                            'done\nexec "$@"',
                 "systemctl": 'echo "systemctl $*" >> "$STUB/aufrufe"\ncase "$1" in\n'
                              '  cat) for u in "$UNITS/$2" "$UNITS/$2.service"; do test -f "$u" && exec cat "$u"; '
                              'done; exit 1;;\n'
                              '  is-enabled) [ "$2" = -q ] && shift; grep -qx "$2" "$STUB/an" 2>/dev/null;;\n'
                              '  enable) shift; for u in "$@"; do case "$u" in -*) ;; *) echo "$u" >> "$STUB/an";; '
                              'esac; done;;\n'
                              '  list-units) cat "$STUB/laufend" 2>/dev/null;;\nesac',
                 "getent": '[ "$1" = passwd ] && grep -qx "$2" "$STUB/benutzer" 2>/dev/null && '
                           'echo "$2:x:64602:64602::/nonexistent:/usr/sbin/nologin"'}
        for name, inhalt in stubs.items():
            (self.stub / name).write_text(f"#!/bin/sh\n{inhalt}\n", encoding="utf-8")
            (self.stub / name).chmod(0o755)
        (self.stub / "benutzer").write_text("clip-max\n", encoding="utf-8")
        self.benutzer.mkdir()
        (self.benutzer / "lost+found").mkdir()   # die Wurzel des Freunde-Volumes
        (self.benutzer / "ohne-marke" / "db").mkdir(parents=True)
        (self.benutzer / "anders" / "db").mkdir(parents=True)
        (self.benutzer / "anders" / ".clip-benutzer").write_text("max\n", encoding="utf-8")   # Marke passt nicht

    def tearDown(self):
        self._tmp.cleanup()

    def freund(self, name: str = "max") -> Path:
        inst = self.benutzer / name
        (inst / "db").mkdir(parents=True)
        (inst / ".clip-benutzer").write_text(f"{name}\n", encoding="utf-8")
        return inst

    def lauf(self) -> subprocess.CompletedProcess:
        env = {**os.environ, "PATH": f"{self.stub}:{os.environ['PATH']}", "STUB": str(self.stub),
               "UNITS": str(self.units), "BENUTZER_DIR": str(self.benutzer)}
        # innen <PROD> <REGIE> <MIT_REGIE> <ALT_PROD> <ZIEL_PROD> <ALT_REGIE> <ZIEL_REGIE> <STEMPEL> <UNITS> <SICH>
        r = subprocess.run(["bash", "-c", innen_teil(), "innen", str(self.prod), str(self.t / "regie"), "0",
                            self.stand, self.stand, "", "", "TEST", str(self.units), str(self.t / "sich")],
                           capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    def aufrufe(self) -> list[str]:
        datei = self.stub / "aufrufe"
        return datei.read_text(encoding="utf-8").splitlines() if datei.exists() else []

    def test_freund_gesichert_vorlagen_da_nicht_eingeschaltet_bot_neu(self):
        db = self.freund() / "db" / "pipeline.db"
        con = sqlite3.connect(db)   # bleibt offen: die Zeile steht nur im WAL – die Sicherung muss sie trotzdem haben
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("CREATE TABLE matches (id TEXT)")
        con.execute("INSERT INTO matches VALUES ('m1')")
        con.commit()
        self.addCleanup(con.close)
        (self.stub / "laufend").write_text("clip-freund-bot@max.service loaded active running Lern-Bot von max\n",
                                           encoding="utf-8")
        r = self.lauf()
        kopie = db.with_name("vor-update-TEST.db")
        mit = sqlite3.connect(kopie)
        self.assertEqual(mit.execute("SELECT id FROM matches").fetchall(), [("m1",)])
        mit.close()
        aufrufe = self.aufrufe()
        sicherung = next(i for i, a in enumerate(aufrufe) if a.startswith("runuser -u clip-max -- python3"))
        umstellen = next(i for i, a in enumerate(aufrufe) if " checkout " in a)
        self.assertLess(sicherung, umstellen)                     # vor dem Umstellen, als clip-max
        for vorlage in VORLAGEN:
            with self.subTest(vorlage.name):
                self.assertEqual((self.units / vorlage.name).read_text(encoding="utf-8"),
                                 vorlage.read_text(encoding="utf-8"))
        self.assertFalse([a for a in aufrufe if "clip-freund" in a and ("enable" in a or " start " in a)])
        self.assertFalse((self.stub / "an").exists() and "clip-freund" in (self.stub / "an").read_text())
        self.assertIn("systemctl restart clip-freund-bot@max.service", aufrufe)
        self.assertIn("systemctl restart clip-lernbot", aufrufe)  # Florians Bots wie bisher
        self.assertIn("clip-freund-bot@*.service", (self.t / "sich.zurueck.sh").read_text(encoding="utf-8"))
        self.assertIn(f"Datenbank von max → {kopie}", r.stdout)
        # Zweiter Lauf: eine von Hand angepasste Vorlage bleibt, eine vorhandene Sicherung wird nie überschrieben
        eigen = self.units / "clip-freund-scan@.timer"
        eigen.write_text(eigen.read_text(encoding="utf-8").replace("5min", "7min"), encoding="utf-8")
        groesse = kopie.stat().st_size
        r = self.lauf()
        self.assertIn("clip-freund-scan@.timer weicht vom Repo ab", r.stdout)
        self.assertIn("7min", eigen.read_text(encoding="utf-8"))
        self.assertIn("Sicherung von max fehlgeschlagen", r.stdout)   # vor-update-TEST.db gibt es schon
        self.assertEqual(kopie.stat().st_size, groesse)

    def test_ohne_freunde_keine_aenderung(self):
        vorher = sorted(p.relative_to(self.benutzer).as_posix() for p in self.benutzer.rglob("*"))
        r = self.lauf()
        self.assertEqual(sorted(p.relative_to(self.benutzer).as_posix() for p in self.benutzer.rglob("*")), vorher)
        self.assertFalse([n for n in os.listdir(self.units) if n.startswith("clip-freund")])
        self.assertFalse([a for a in self.aufrufe() if "clip-freund" in a or "runuser -u clip-" in a])
        self.assertNotIn("clip-freund", (self.t / "sich.zurueck.sh").read_text(encoding="utf-8"))
        for wort in ("Freund", "Vorlage", "Datenbank von", "ohne-marke", "anders", "lost+found"):
            self.assertNotIn(wort, r.stdout)

    def test_link_auf_fremde_datenbank_wird_nicht_gesichert(self):
        """Wichtigster Fehlerfall: Statt seiner Datenbank liegt ein Link (z. B. auf Florians). Die Sicherung nimmt nur
        echte Dateien – sonst landete Florians Datenbank als Kopie im Ordner des Freundes. Das Update läuft weiter."""
        florian = self.t / "florian" / "pipeline.db"
        florian.parent.mkdir()
        sqlite3.connect(florian).close()
        db = self.freund() / "db" / "pipeline.db"
        db.symlink_to(florian)
        r = self.lauf()
        self.assertIn("Sicherung von max übersprungen – Link statt Datenbank", r.stdout)
        self.assertFalse(db.with_name("vor-update-TEST.db").exists())
        self.assertFalse([a for a in self.aufrufe() if a.startswith("runuser -u clip-max")])   # nicht einmal versucht
        self.assertIn("systemctl restart clip-lernbot", self.aufrufe())   # umgestellt wurde trotzdem
        self.assertTrue((self.units / "clip-freund-bot@.service").exists())


class Skript(unittest.TestCase):
    def test_bash_n_auch_innen(self):
        with tempfile.TemporaryDirectory() as ordner:
            innen = Path(ordner) / "innen.sh"
            innen.write_text("#!/usr/bin/env bash\n" + innen_teil(), encoding="utf-8")
            for skript in (UPDATE, innen):
                r = subprocess.run(["bash", "-n", str(skript)], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, f"{skript.name}: {r.stderr}")

    @unittest.skipUnless(shutil.which("shellcheck"), "shellcheck fehlt")
    def test_shellcheck_auch_innen(self):
        """Den INNEN-Teil sieht shellcheck im Skript nur als Text – deshalb einzeln."""
        with tempfile.TemporaryDirectory() as ordner:
            innen = Path(ordner) / "innen.sh"
            innen.write_text("#!/usr/bin/env bash\n" + innen_teil(), encoding="utf-8")
            r = subprocess.run(["shellcheck", "-S", "warning", str(UPDATE), str(innen)], capture_output=True,
                               text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_innen_ohne_hochkomma_und_ohne_einschalten_fuer_freunde(self):
        innen = innen_teil()
        self.assertNotIn("'", innen)   # INNEN steht in '…' – ein Hochkomma beendete den Text mitten im Skript
        self.assertNotRegex(innen, r"enable[^\n]*clip-freund")
        self.assertNotRegex(innen, r"\brm\b")


# --- Freunde-Volume (pve-mini, Schritt 6) -----------------------------------------------------------------------------

VOLUME = DEPLOY / "pve-mini" / "freunde-volume.sh"
# CT 102 wie auf dem Mini: Puffer als mp1. mp2 auf /var/lib/clip-benutzer nur im Snapshot -> zählt nicht
CT_KONF = """arch: amd64
hostname: clips
onboot: 1
rootfs: local-lvm:vm-102-disk-0,size=16G
unprivileged: 1
lxc.mount.entry: /mnt/big srv/big none rbind,rslave,create=dir 0 0
mp1: local-lvm:vm-102-disk-1,mp=/srv/puffer,backup=0,mountoptions=noatime;discard,size=96G
[vorher]
mp2: local-lvm:vm-102-disk-7,mp=/var/lib/clip-benutzer
snaptime: 1
"""
# Attrappen: jeder Aufruf landet in $STUB/aufrufe (MitStubs). pct ändert die CT-Konfig wie Proxmox (nur den aktiven
# Abschnitt; --delete lässt das Volume als unusedN stehen, SPEICHER:GB legt vm-102-disk-2 an). Was im CT laufen soll
# (sh -c …), läuft in der Scheinwurzel $WURZEL: Die Pfad-Argumente zeigen dorthin, findmnt/stat/chown/chmod sind
# Attrappen – „eingehängt“ ist, was im aktiven Abschnitt auf /var/lib/clip-benutzer zeigt, solange der CT läuft.
AKTIV = "aktiv() { awk '/^\\[/ {exit} {print}' \"$KONF\"; }\n"
VOLUME_STUBS = {
    "pct": "set -o pipefail\n" + CT_ZUSTAND + AKTIV + r'''case "$1" in
  status) echo "status: $(ct)" ;;
  shutdown) echo stopped > "$STUB/ct" ;;
  start) echo running > "$STUB/ct"
         ! aktiv | grep -q "mp=/var/lib/clip-benutzer" || mkdir -p "$WURZEL/var/lib/clip-benutzer" ;;
  set) shift 2
    if [ "$1" = --delete ]; then
      vol="$(aktiv | awk -v k="$2:" '$1 == k {split($2, t, ","); print t[1]; exit}')"
      sed -i "1,/^\[/ {/^$2: /d}" "$KONF"; sed -i "1i unused0: $vol" "$KONF"
    else
      vol="${2%%,*}"; rest="${2#*,}"
      case "$vol" in *:vm-*) ;; *) rest="$rest,size=${vol#*:}G"; vol="${vol%%:*}:vm-102-disk-2" ;; esac
      sed -i "1,/^\[/ {/^unused[0-9]*: $vol\$/d}" "$KONF"; sed -i "1i ${1#--}: $vol,$rest" "$KONF"
    fi ;;
  exec) shift 3; case "$1" in
    sh) skript="$3"; shift 4; args=(); for a in "$@"; do args+=("$WURZEL$a"); done
        sh -c "$skript" sh "${args[@]}" | sed 's/$/\r/' ;;
    runuser) exit "${SPERRE_BELEGT:-0}" ;;
    systemctl) printf '%s' "${FREUNDE_DIENSTE:-}" ;;
  esac ;;
esac''',
    "lvs": 'case "${*: -1}" in pve/data) echo "${LVS:-  400.00  30.00  2.00}" ;; *) echo "${LVS_PUFFER:-  96.00  50.00}" ;; '
           'esac',
    "pvesm": '[ "$1" = path ] && echo "/dev/pve/${2#*:}"',
    "tune2fs": STUBS["tune2fs"],
    "findmnt": CT_ZUSTAND + AKTIV + '[ "$(ct)" = running ] && aktiv | grep -q "mp=/var/lib/clip-benutzer"',
    # %d: das Volume ist ein eigener Speicher (DEV_ZIEL=1: derselbe wie / und Puffer); "%U %a": Rechte aus der Wurzel
    "stat": r'''case "$2" in
  %d) case "$3" in */var/lib/clip-benutzer) echo "${DEV_ZIEL:-2}" ;; *) echo 1 ;; esac ;;
  *) echo "${BESITZER:-root} $(PATH=/usr/bin:/bin stat -c %a "$3")" ;;
esac''',
    "chown": "",
    "chmod": r'''case "${*: -1}" in "$TESTORDNER"/*) PATH=/usr/bin:/bin exec chmod "$@" ;; esac
echo "chmod außerhalb der Testordner: $*" >&2; exit 99''',
    "systemctl": "",
    # Löschen darf nie vorkommen – auch nicht aus Versehen über eine Attrappe
    "rm": "exit 1", "lvremove": "exit 1", "wipefs": "exit 1", "mkfs.ext4": "exit 1",
}
# Aufrufe, die etwas ändern (Probe, „nein“ und Abbrüche: keiner davon)
VOLUME_AENDERUNGEN = ("pct set", "pct shutdown", "pct start", "tune2fs -m", "chown", "chmod", "rm ", "lvremove",
                      "wipefs", "mkfs")
NIE_LOESCHEN = re.compile(r"\b(rm|rmdir|lvremove|lvreduce|wipefs|mkfs|shred)\b|pct\s+destroy|pvesm\s+free|--purge")


def pruefe_volume_skript(test: unittest.TestCase, pfad: Path) -> None:
    """bash -n, shellcheck, und nichts darin löscht: kein rm/lvremove/wipefs/…, --delete nur für den Einhängepunkt."""
    text = pfad.read_text(encoding="utf-8")
    test.assertTrue(text.startswith("#!/usr/bin/env bash\n"), pfad.name)
    test.assertIn("\nset -euo pipefail\n", text, pfad.name)
    test.assertIsNone(NIE_LOESCHEN.search(text), pfad.name)
    test.assertEqual(set(re.findall(r"--delete\s+(\S+)", text)) - {'"$MP"'}, set(), pfad.name)  # nie „unusedN“
    r = subprocess.run(["bash", "-n", str(pfad)], capture_output=True, text=True)
    test.assertEqual(r.returncode, 0, f"{pfad.name}: {r.stderr}")
    if shutil.which("shellcheck"):
        r = subprocess.run(["shellcheck", "-S", "warning", str(pfad)], capture_output=True, text=True,
                           env={**os.environ, "LC_ALL": "C.UTF-8"})
        test.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class FreundeVolume(MitStubs):
    SKRIPT = VOLUME

    def setUp(self):
        super().setUp()
        for name, inhalt in VOLUME_STUBS.items():
            datei = self.stub / name
            datei.write_text(f'#!/bin/bash\necho "{name} $*" >> "$STUB/aufrufe"\n{inhalt}\n', encoding="utf-8")
            datei.chmod(0o755)
        self.konf = self.t / "102.conf"
        self.konf.write_text(CT_KONF, encoding="utf-8")
        self.wurzel = self.t / "wurzel"                       # die Sicht im CT
        (self.wurzel / "srv/puffer").mkdir(parents=True)
        self.ziel = self.wurzel / "var/lib/clip-benutzer"     # heute ein leerer Ordner auf der CT-Platte
        self.ziel.mkdir(parents=True)
        self.ziel.chmod(0o755)
        self.ablage = self.t / "ablage"                       # /root/freunde-volume
        self.umgebung.update(KONF=str(self.konf), WURZEL=str(self.wurzel), ABLAGE=str(self.ablage),
                             TESTORDNER=str(self.t))

    def zurueck(self, *argumente: str, eingabe: str = "", **umgebung: str) -> subprocess.CompletedProcess:
        env = {**os.environ, **self.umgebung, **umgebung}
        return subprocess.run(["bash", str(self.ablage / "zurueck.sh"), *argumente], input=eingabe,
                              capture_output=True, text=True, env=env, timeout=60)

    def aenderungen(self) -> list[str]:
        return [z for z in self.aufrufe().splitlines() if z.startswith(VOLUME_AENDERUNGEN)]

    def neu(self):
        """Aufrufliste leeren – für den nächsten Lauf."""
        (self.stub / "aufrufe").unlink(missing_ok=True)

    def aktiv(self) -> str:
        return self.konf.read_text(encoding="utf-8").split("[vorher]")[0]

    def test_skript(self):
        pruefe_volume_skript(self, VOLUME)
        self.assertTrue(os.access(VOLUME, os.X_OK))

    def test_probe_zeigt_alles_und_aendert_nichts(self):
        r = self.lauf("--probe")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        assertReihenfolge(self, r.stdout, [
            "Im CT: /var/lib/clip-benutzer – leerer Ordner auf der CT-Platte",
            "Puffer /srv/puffer: 96.00 GB, belegt 50.00 % – kann noch 48.0 GB wachsen",
            "Mit ganz vollem Freunde-Volume (100 GB) und ganz vollem Puffer: 67.0 % (Grenze 90 %)",   # 30 + 148/4
            "nicht während eines Spielabends", "$ pct shutdown 102 --timeout 180",
            "$ pct set 102 --mp2 'local-lvm:100,mp=/var/lib/clip-benutzer,backup=0,mountoptions=noatime;discard'",
            "$ tune2fs -m 0 '<Gerät des neuen Volumes>'", "$ pct start 102", "chmod 711",
            "Probe fertig – nichts verändert"])
        self.assertEqual(self.aenderungen(), [])
        self.assertIn("lvs --noheadings --nosuffix --units g -o lv_size,data_percent /dev/pve/vm-102-disk-1",
                      self.aufrufe())
        self.assertEqual(self.konf.read_text(encoding="utf-8"), CT_KONF)
        self.assertFalse(self.ablage.exists())                 # keine Sicherung, kein Rückweg-Skript
        self.assertEqual(stat.S_IMODE(self.ziel.stat().st_mode), 0o755)

    def test_anlegen_wiederholen_rueckweg_wieder_einhaengen(self):
        # Normaler Weg: CT kurz aus, Volume anlegen, Reserve aus, solange er aus ist (ext4-MMP), Wurzel root 0711
        r = self.lauf(eingabe="j\nj\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        volume = "mp2: local-lvm:vm-102-disk-2,mp=/var/lib/clip-benutzer,backup=0,mountoptions=noatime;discard,size=100G"
        assertReihenfolge(self, self.aufrufe(), [
            "pct exec 102 -- runuser -u pipeline -- flock -n /var/lib/clip-pipeline/pipeline.lock true",
            "pct shutdown 102 --timeout 180", "pct set 102 --mp2 local-lvm:100,mp=/var/lib/clip-benutzer",
            "tune2fs -m 0 /dev/pve/vm-102-disk-2", "pct start 102", "chown root:root", "chmod 711"])
        self.assertIn(volume, self.aktiv())
        self.assertEqual(stat.S_IMODE(self.ziel.stat().st_mode), 0o711)
        self.assertEqual((self.ablage / "volume").read_text(encoding="utf-8"), "local-lvm:vm-102-disk-2\n")
        self.assertEqual((self.ablage / "original/102.conf").read_text(encoding="utf-8"), CT_KONF)   # Stand vorher
        rueckweg = self.ablage / "zurueck.sh"
        self.assertEqual(stat.S_IMODE(rueckweg.stat().st_mode), 0o700)
        pruefe_volume_skript(self, rueckweg)
        self.assertIn("Rückweg:    bash", r.stdout)

        # Zweiter Lauf: alles fertig – keine Frage, keine Änderung, die Pool-Grenze zählt nicht mehr
        self.neu()
        r = self.lauf(LVS="  200.00  89.00  2.00")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for satz in ("schon eingehängt: mp2 = local-lvm:vm-102-disk-2", "eingehängt, eigener Speicher, root, 0711",
                     "schon da – übersprungen", "schon richtig – übersprungen", "zurueck.sh (schon da)"):
            self.assertIn(satz, r.stdout)
        self.assertEqual(self.aenderungen(), [])
        self.assertNotIn("? ", r.stdout)

        # Rückweg: hängt nur aus – Proxmox behält das Volume als unusedN, gelöscht wird nichts
        self.neu()
        r = self.zurueck("--probe")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("$ pct set 102 --delete mp2", r.stdout)
        self.assertEqual(self.aenderungen(), [])
        r = self.zurueck(eingabe="j\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.aenderungen(), ["pct shutdown 102 --timeout 180", "pct set 102 --delete mp2",
                                              "pct start 102"])
        self.assertIn("unused0: local-lvm:vm-102-disk-2", self.aktiv())
        self.assertNotIn("clip-benutzer", self.aktiv())
        self.assertIn("Die Daten der Freunde bleiben im Volume local-lvm:vm-102-disk-2", r.stdout)

        # Nochmal einrichten: dasselbe Volume wieder einhängen – kein neues, keine Pool-Rechnung, kein tune2fs
        self.neu()
        r = self.lauf(eingabe="j\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Früher angelegtes Freunde-Volume gefunden: local-lvm:vm-102-disk-2", r.stdout)
        self.assertEqual(self.aenderungen(), [
            "pct shutdown 102 --timeout 180",
            "pct set 102 --mp2 local-lvm:vm-102-disk-2,mp=/var/lib/clip-benutzer,backup=0,mountoptions=noatime;discard",
            "pct start 102"])
        self.assertNotIn("lvs", self.aufrufe())
        self.assertIn("schon richtig – übersprungen", r.stdout)
        self.assertNotIn("unused", self.aktiv())

    def test_pool_zu_knapp_mit_vollem_puffer(self):
        # Wichtigster Fehlerfall: 60 % + (100 GB + 48 GB, die dem Puffer noch fehlen) / 400 GB = 97 % – ohne den Puffer
        # wären es 85 % und das Volume käme durch, bis beide voll sind und alle Gäste auf pve-mini stehen
        r = self.lauf(eingabe="j\nj\n", LVS="  400.00  60.00  2.00")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("ganz vollem Puffer: 97.0 % (Grenze 90 %)", r.stdout)
        self.assertIn("Zu knapp", r.stdout)
        self.assertIn(f"So viel passt:  bash {VOLUME} --groesse 72", r.stdout)   # (90 - 60) * 4 - 48
        self.assertEqual(self.aenderungen(), [])
        self.assertFalse(self.ablage.exists())
        r = self.lauf("--probe", "--groesse", "72", LVS="  400.00  60.00  2.00")   # genau 90 %: geht
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("$ pct set 102 --mp2 'local-lvm:72,mp=/var/lib/clip-benutzer", r.stdout)
        self.assertEqual(self.aenderungen(), [])

    def test_ziel_auf_der_ct_platte_nicht_leer(self):
        # Das Volume würde den Inhalt verdecken – nichts anlegen, nichts herunterfahren
        (self.ziel / "max").mkdir()
        r = self.lauf(eingabe="j\nj\n")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("das Volume würde den Inhalt verdecken", r.stdout)
        self.assertEqual(self.aenderungen(), [])
        self.assertEqual(self.konf.read_text(encoding="utf-8"), CT_KONF)

    def test_sperre_belegt_oder_nein(self):
        for eingabe, umgebung, satz in (("j\n", {"SPERRE_BELEGT": "1"}, "Pipeline-Schritt"),
                                        ("n\n", {}, "Abgebrochen – nichts verändert.")):
            with self.subTest(satz):
                r = self.lauf(eingabe=eingabe, **umgebung)
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
                self.assertIn(satz, r.stdout)
                self.assertEqual(self.aenderungen(), [])
                self.assertEqual(self.konf.read_text(encoding="utf-8"), CT_KONF)
                self.assertFalse(self.ablage.exists())

    def test_rueckweg_nicht_solange_freunde_laufen(self):
        self.assertEqual(self.lauf(eingabe="j\nj\n").returncode, 0)
        self.neu()
        laufend = ("clip-freund-bot@max.service loaded active running Lern-Bot von max\n"
                   "clip-freund-scan@max.timer loaded active waiting Ein Match von max\n")
        r = self.zurueck(eingabe="j\n", FREUNDE_DIENSTE=laufend)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("laufen noch Dienste von Freunden", r.stdout)
        self.assertIn("systemctl disable --now clip-freund-bot@max.service clip-freund-scan@max.timer", r.stdout)
        self.assertEqual(self.aenderungen(), [])
        self.assertIn("mp=/var/lib/clip-benutzer", self.aktiv())

    def test_falscher_aufruf(self):
        for argumente, umgebung in ((["--prob"], {}), (["--groesse"], {}), (["--groesse", "viel"], {}),
                                    ([], {"MP": "mp0"})):
            with self.subTest(argumente=argumente, umgebung=umgebung):
                r = self.lauf(*argumente, **umgebung)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertEqual(self.aufrufe(), "")


if __name__ == "__main__":
    unittest.main()
