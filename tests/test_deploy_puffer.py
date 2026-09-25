"""E19 Deploy: Puffer auf pve-mini, Samba im CT, Timer für Abgleich und Morgenprüfung (docs/PUFFER.md).

Geprüft werden die Skripte (bash -n, shellcheck, Probe-Modus, Nachfragen und Abbrüche), die Unit-Dateien und die
Anleitung. Kein echter Host: pct, lvs, systemctl & Co. sind Stubs in einem Temp-Ordner, die nur mitschreiben, womit
sie aufgerufen wurden. Was die Skripte selbst anlegen (Sicherung, sbin, Units), landet in Temp-Ordnern.
"""

from __future__ import annotations

import fcntl
import os
import re
import shutil
import subprocess
import tempfile
import tomllib
import unittest
from datetime import datetime
from pathlib import Path

PROJEKT = Path(__file__).resolve().parents[1]
DEPLOY = PROJEKT / "deploy"
EINRICHTEN = DEPLOY / "pve-mini/puffer-einrichten.sh"
ZURUECK = DEPLOY / "pve-mini/puffer-zurueck.sh"
LVM_STATUS = DEPLOY / "pve-mini/clip-lvm-status"
SAMBA = DEPLOY / "mini/samba-einrichten.sh"
SMB_VORLAGE = DEPLOY / "mini/smb-puffer.conf"
SKRIPTE = [EINRICHTEN, ZURUECK, LVM_STATUS, SAMBA]
KONFIG = tomllib.loads((PROJEKT / "config/pipeline.toml").read_text(encoding="utf-8"))


def lies_unit(pfad: Path) -> dict[str, list[str]]:
    """Schlüssel=Wert-Zeilen einer systemd-Unit (ohne Kommentare); mehrfache Schlüssel als Liste."""
    werte: dict[str, list[str]] = {}
    for zeile in pfad.read_text(encoding="utf-8").splitlines():
        zeile = zeile.strip()
        if zeile and not zeile.startswith(("#", ";", "[")) and "=" in zeile:
            schluessel, wert = zeile.split("=", 1)
            werte.setdefault(schluessel.strip(), []).append(wert.strip())
    return werte


def assertReihenfolge(test: unittest.TestCase, text: str, teile: list[str]) -> None:
    """Jeder Teil kommt in text vor, und zwar nach dem vorigen."""
    stelle = -1
    for teil in teile:
        stelle = text.find(teil, stelle + 1)
        test.assertNotEqual(stelle, -1, f"{teil!r} fehlt oder steht vor dem vorigen Teil:\n{text}")


def anleitung() -> tuple[str, dict[str, str]]:
    """docs/PUFFER.md: (Text vor R0, {"R0": Abschnitt, ...})."""
    teile = re.split(r"^## (R\d) · ", (PROJEKT / "docs/PUFFER.md").read_text(encoding="utf-8"), flags=re.M)
    return teile[0], dict(zip(teile[1::2], teile[2::2]))


def lies_smb(pfad: Path) -> dict[str, dict[str, str]]:
    """smb.conf als {abschnitt: {parameter: wert}}; Namen klein wie bei Samba."""
    abschnitte: dict[str, dict[str, str]] = {}
    aktuell: dict[str, str] = {}
    for zeile in pfad.read_text(encoding="utf-8").splitlines():
        zeile = zeile.strip()
        if not zeile or zeile.startswith(("#", ";")):
            continue
        if zeile.startswith("["):
            aktuell = abschnitte.setdefault(zeile.strip("[]").lower(), {})
        else:
            name, wert = zeile.split("=", 1)
            aktuell[" ".join(name.lower().split())] = wert.strip()
    return abschnitte


class Syntax(unittest.TestCase):
    def test_bash_n(self):
        for skript in SKRIPTE:
            r = subprocess.run(["bash", "-n", str(skript)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f"{skript.name}: {r.stderr}")

    def test_kopf_und_ausfuehrbar(self):
        for skript in SKRIPTE:
            text = skript.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("#!/usr/bin/env bash\n"), skript.name)
            self.assertIn("\nset -euo pipefail\n", text, skript.name)
            self.assertTrue(os.access(skript, os.X_OK), f"{skript.name} nicht ausführbar")

    @unittest.skipUnless(shutil.which("shellcheck"), "shellcheck fehlt")
    def test_shellcheck(self):
        r = subprocess.run(["shellcheck", *map(str, SKRIPTE)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class Units(unittest.TestCase):
    SYSTEMD = DEPLOY / "systemd"

    def test_lager(self):
        s = lies_unit(self.SYSTEMD / "clip-lager.service")
        self.assertEqual(s["ExecStart"], ["/opt/clip-pipeline/.venv/bin/pipeline lager abgleich"])
        self.assertEqual(s["Type"], ["oneshot"])
        self.assertEqual(s["SuccessExitStatus"], ["3 4"])
        t = lies_unit(self.SYSTEMD / "clip-lager.timer")
        self.assertEqual(t["OnCalendar"], ["*-*-* 10:00"])  # tagsüber, nie nachts
        self.assertEqual(t["Persistent"], ["true"])
        self.assertEqual(t["RandomizedDelaySec"], ["10min"])
        self.assertEqual(t["WantedBy"], ["timers.target"])

    def test_lager_verdeckt_das_nfs_nicht(self):
        # Um 10:00 schläft pve-big meist: /srv/big/clips ist beim Start noch ein leerer Ordner. Ein eigener
        # Bind darauf (ReadWritePaths=/srv/big/clips oder der Link /srv/clips) würde das spätere NFS verdecken.
        pfade = lies_unit(self.SYSTEMD / "clip-lager.service")["ReadWritePaths"][0].split()
        self.assertEqual(pfade, ["/var/lib/clip-pipeline", "/srv/puffer", "/srv/big"])

    def test_dienste_duerfen_in_den_puffer_schreiben(self):
        # Nach R5 ist /srv/clips ein Link auf /srv/puffer: ausdrücklich freigeben. "-": vor R1 fehlt /srv/puffer –
        # ohne das Präfix startete systemd den Dienst gar nicht (226/NAMESPACE)
        for name in ("clip-bot.service", "clip-lernbot.service", "clip-sitzungen.service"):
            with self.subTest(name):
                pfade = lies_unit(self.SYSTEMD / name)["ReadWritePaths"][0].split()
                self.assertEqual(pfade, ["/var/lib/clip-pipeline", "/srv/clips", "-/srv/puffer"])
        # clip-aufraeumen bleibt unverändert: im getrennten Betrieb ist sein Timer aus (R6)
        self.assertEqual(lies_unit(self.SYSTEMD / "clip-aufraeumen.service")["ReadWritePaths"],
                         ["/var/lib/clip-pipeline /srv/clips"])

    def test_morgenpruefung(self):
        s = lies_unit(self.SYSTEMD / "clip-puffer-pruefen.service")
        self.assertEqual(s["ExecStart"], ["/opt/clip-pipeline/.venv/bin/pipeline puffer pruefen"])
        self.assertEqual(s["ReadWritePaths"], ["/var/lib/clip-pipeline"])  # nur die Datenbank, Puffer nur lesen
        t = lies_unit(self.SYSTEMD / "clip-puffer-pruefen.timer")
        self.assertEqual(t["OnCalendar"], ["*-*-* 11:00"])  # nach dem Abgleich
        self.assertEqual(t["Persistent"], ["true"])

    def test_haertung_wie_im_bestand(self):
        for name in ("clip-lager.service", "clip-puffer-pruefen.service"):
            s = lies_unit(self.SYSTEMD / name)
            text = (self.SYSTEMD / name).read_text(encoding="utf-8")
            with self.subTest(name):
                self.assertEqual(s["User"], ["pipeline"])
                self.assertEqual(s["WorkingDirectory"], ["/opt/clip-pipeline"])
                self.assertEqual(s["NoNewPrivileges"], ["true"])
                self.assertEqual(s["ProtectSystem"], ["strict"])
                self.assertEqual(s["ProtectHome"], ["true"])
                self.assertIn("/var/lib/clip-pipeline", s["ReadWritePaths"][0].split())
                self.assertIn("main", text)  # Kommentar: welcher Checkout

    def test_lvm_status(self):
        s = lies_unit(DEPLOY / "pve-mini/clip-lvm-status.service")
        self.assertEqual(s["ExecStart"], ["/usr/local/sbin/clip-lvm-status"])
        self.assertNotIn("User", s)  # lvs braucht root
        t = lies_unit(DEPLOY / "pve-mini/clip-lvm-status.timer")
        self.assertEqual(t["OnUnitActiveSec"], ["15min"])
        self.assertEqual(t["WantedBy"], ["timers.target"])


class SambaVorlage(unittest.TestCase):
    def setUp(self):
        self.smb = lies_smb(SMB_VORLAGE)

    def test_veto_nur_aktiv(self):
        # Samba vergleicht jeden Namen auf jeder Ebene: /highlights/ ließe eingang\nvidia\highlights verschwinden
        veto = self.smb["clips"]["veto files"]
        self.assertEqual(veto, "/.aktiv/")
        namen = {n for n in veto.split("/") if n}
        for ordner in ["highlights", "sessions", "musik", *KONFIG["lager"]["ordner"], ".clip-speicher", ".clip-puffer"]:
            self.assertNotIn(ordner, namen)
        self.assertNotIn("veto files", self.smb["global"])  # gälte sonst für jede Freigabe

    def test_nur_heimnetz_nur_smb3(self):
        g = self.smb["global"]
        self.assertEqual(g["server min protocol"], "SMB3")
        self.assertEqual(g["disable netbios"], "yes")
        self.assertEqual(g["interfaces"], "127.0.0.1 192.168.178.0/24")  # nur IPv4-Heimnetz, kein IPv6/Tailscale
        self.assertEqual(g["bind interfaces only"], "yes")
        self.assertEqual(g["hosts allow"], "127.0.0.1 192.168.178.0/24")
        self.assertEqual(g["map to guest"], "never")

    def test_freigabe(self):
        c = self.smb["clips"]
        self.assertEqual(c["path"], "/srv/puffer")
        self.assertEqual(c["valid users"], "gamingpc")
        self.assertEqual((c["force user"], c["force group"]), ("pipeline", "pipeline"))
        self.assertEqual((c["create mask"], c["directory mask"]), ("0644", "0755"))
        self.assertEqual(c["strict sync"], "yes")
        self.assertEqual(c["read only"], "no")
        self.assertEqual(set(self.smb), {"global", "clips"})  # keine [homes], keine Drucker


class Konsistenz(unittest.TestCase):
    def test_ordner_wie_lager_konfig(self):
        treffer = re.search(r'^ORDNER="([^"]*)"', EINRICHTEN.read_text(encoding="utf-8"), re.M)
        self.assertEqual(treffer.group(1).split(), KONFIG["lager"]["ordner"])

    def test_lvm_status_landet_im_ct_unter_pool_status(self):
        # Host /mnt/big ist im CT /srv/big (lxc.mount.entry aus rette-clips-start.sh)
        self.assertIn('DATEI="${DATEI:-/mnt/big/lvm-status.txt}"', LVM_STATUS.read_text(encoding="utf-8"))
        rette = (DEPLOY / "pve-mini/rette-clips-start.sh").read_text(encoding="utf-8")
        self.assertIn('BIG="${BIG:-/mnt/big}"', rette)
        self.assertIn("lxc.mount.entry: $BIG srv/big none rbind", rette)
        self.assertEqual(KONFIG["puffer"]["pool_status"], "/srv/big/lvm-status.txt")

    def test_samba_zeigt_auf_den_puffer(self):
        self.assertIn("\nZIEL=/srv/puffer ", EINRICHTEN.read_text(encoding="utf-8"))
        self.assertEqual(lies_smb(SMB_VORLAGE)["clips"]["path"], "/srv/puffer")

    def test_anleitung_hat_jeden_schritt_vollstaendig(self):
        _, schritte = anleitung()
        self.assertEqual(list(schritte), [f"R{i}" for i in range(9)])
        for name, inhalt in schritte.items():
            with self.subTest(name):
                for pflicht in ("**Was:**", "**Warum:**", "**Freigabe nötig?**", "**Rückweg:**", "**Was du lernst:**"):
                    self.assertIn(pflicht, inhalt)
        self.assertIn('frist = ""', schritte["R5"])  # Pflichtschritt vor dem Umschalten
        self.assertIn("ln -sfn /srv/puffer /srv/clips", schritte["R5"])


class Anleitung(unittest.TestCase):
    def setUp(self):
        self.vorab, self.schritte = anleitung()

    def test_rueckweg_r5_erst_zufluss_stoppen_dann_abgleichen(self):
        # Samba zeigt fest auf /srv/puffer. Erst PC + smbd still, dann Dienste, Ruhezeit, Abgleich, Timer aus,
        # dann Link und Konfig zurück, erst zuletzt den PC wieder einschalten.
        r5 = self.schritte["R5"]
        rueckweg = r5[r5.index("**Rückweg:**"):r5.index("**Was du lernst:**")]
        assertReihenfolge(self, rueckweg, [
            "Disable-ScheduledTask", "systemctl stop smbd", "systemctl stop clip-bot", "flock -n", "10 min warten",
            "pipeline lager abgleich", "pipeline lager status", "systemctl disable --now clip-lager.timer "
            "clip-puffer-pruefen.timer", "ln -sfn /srv/big/clips /srv/clips", "lokal.toml.vor-e19",
            "kein getrennter Betrieb", "read only = no", "reload smbd", "Enable-ScheduledTask"])
        self.assertIn("nur zusammen mit dem Rückweg R5", self.schritte["R7"])
        # Nach R8 ist die Freigabe auf pve-big nur lesbar: ohne Rückweg R8 scheiterte jede Kopie des PCs still
        r7 = self.schritte["R7"]
        assertReihenfolge(self, r7[r7.index("**Rückweg:**"):], ["read only = no", "Rückweg R5"])
        self.assertIn("Rückweg R5", self.schritte["R8"][self.schritte["R8"].index("**Rückweg:**"):])

    def test_r5_erst_ruhezeit_dann_uebernahme_und_nur_bei_ok_weiter(self):
        # Zu junge Lager-Dateien lässt die Übernahme aus (Exit 1, zu_jung) – nach dem Umschalten fiele das niemandem auf
        r5 = self.schritte["R5"]
        hin = r5[:r5.index("**Rückweg:**")]
        assertReihenfolge(self, hin, ["Disable-ScheduledTask", "systemctl stop clip-bot", "flock -n", "10 min warten",
                                      "lager uebernehmen", 'echo "Exit $?"', "Exit 0", '"ok": true', '"zu_jung"',
                                      "Exit 3", "Exit 4", "lokal.toml.vor-e19", "ln -sfn /srv/puffer /srv/clips"])
        self.assertIn("pausieren", hin)
        self.assertNotIn("Gaming-PC aus oder", hin)  # PC aus lassen reicht nicht: beim Hochfahren kopierte er nach pve-big
        self.assertIn('"ok": true', self.schritte["R4"])
        self.assertIn("--final", hin)  # was im Puffer-Betrieb nicht mehr geht

    def test_r7_holt_das_neue_skript_auf_den_pc(self):
        r7 = self.schritte["R7"]
        assertReihenfolge(self, r7, ["git pull --ff-only", "Schreibe-PcStatus", "Ziel         =", "-Probelauf",
                                     "Enable-ScheduledTask", "pc-status.json"])
        self.assertIn("Schreibe-PcStatus", (PROJEKT / "windows/Uebertragung.ps1").read_text(encoding="utf-8-sig"))

    def test_sicherungen_nur_beim_ersten_mal(self):
        # Ein wiederholter Block darf die Sicherung für den Rückweg nicht mit dem neuen Stand überschreiben
        zeilen = [z.strip() for z in "\n".join(self.schritte.values()).splitlines()]
        sichern = [z for z in zeilen if "vor-e19" in z and any(s in z for s in ("rev-parse", ".backup", "cp -a"))]
        self.assertEqual(len(sichern), 4, sichern)  # R3: sha + db · R5: zwei lokal.toml
        for zeile in sichern:
            with self.subTest(zeile):
                self.assertTrue(zeile.startswith("[ -e "), zeile)
        # Die R5-Zeile wirklich zweimal ausführen: die erste Sicherung bleibt
        befehl = next(z for z in sichern if "/opt/clip-pipeline/config" in z)
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            befehl = befehl.replace("/opt/clip-pipeline/config", tmp)
            (t / "lokal.toml").write_text("alt\n")
            subprocess.run(["bash", "-c", befehl], check=True, timeout=10)
            (t / "lokal.toml").write_text("neu\n")
            subprocess.run(["bash", "-c", befehl], check=True, timeout=10)
            self.assertEqual((t / "lokal.toml.vor-e19").read_text(), "alt\n")
        # Sichern vor jedem git pull – auch vor Schritt 1 im Abschlussbericht (der selbst git pull macht)
        assertReihenfolge(self, self.schritte["R3"], ["vor-e19.sha", "Host-Schritte 1–12", "git pull --ff-only"])

    def test_r3_sagt_dass_der_ganze_sprint_kommt(self):
        r3 = self.schritte["R3"]
        for pflicht in ("ganze Sprint", "docs/ABSCHLUSSBERICHT.md", "pip install -q -e '.[whisper]'",
                        "big pruefen", "pipeline big status", "clip-big-waechter", "[big].frist", 'frist = ""',
                        "R3 bis R5 am selben Tag", "regie.sql", "Exit 3"):
            self.assertIn(pflicht, r3)
        for falsch in ("ändert sich am Verhalten nichts", "nur zwei neue Tabellen", "# wie vorher"):
            self.assertNotIn(falsch, r3)
        self.assertIn("gilt ab R3", self.vorab)

    def test_sperrpruefung_sagt_beide_faelle(self):
        # flock -n endet still mit 1 – die Zeilen aus der Anleitung müssen "frei" ODER "BELEGT" sagen
        zeilen = [z.strip() for z in "\n".join(self.schritte.values()).splitlines()]
        stellen = [i for i, z in enumerate(zeilen) if "flock -n" in z and "pipeline.lock" in z]
        self.assertEqual(len(stellen), 2)  # R5 hin und Rückweg R5
        with tempfile.TemporaryDirectory() as tmp:
            sperre = Path(tmp) / "pipeline.lock"
            sperre.touch()
            for i in stellen:
                befehl = "\n".join(zeilen[i:i + 2]).replace("sudo -u pipeline ", "")
                befehl = befehl.replace("/var/lib/clip-pipeline/pipeline.lock", str(sperre))
                frei = subprocess.run(["bash", "-c", befehl], capture_output=True, text=True, timeout=10)
                self.assertEqual(frei.stdout, "frei\n", frei.stderr)
                with open(sperre) as f:
                    fcntl.flock(f, fcntl.LOCK_EX)  # ein anderer "Schritt" hält die Sperre
                    belegt = subprocess.run(["bash", "-c", befehl], capture_output=True, text=True, timeout=10)
                self.assertTrue(belegt.stdout.startswith("BELEGT – "), belegt.stdout + belegt.stderr)


# Stubs: jeder Aufruf landet in $STUB/aufrufe. Verhalten über Umgebungsvariablen der Tests.
# Zustand des CT: anfangs $CT_STATUS, danach, was pct shutdown/start in $STUB/ct geschrieben haben
CT_ZUSTAND = 'ct() { cat "$STUB/ct" 2>/dev/null || echo "${CT_STATUS:-running}"; }\n'
STUBS = {
    "id": 'case "$1" in -u) echo 0 ;; pipeline) echo "uid=1000(pipeline)" ;; *) exit 1 ;; esac',
    # pct exec antwortet mit \r wie ein echtes Terminal; pct set --mpN trägt den Einhängepunkt wie Proxmox oben
    # in den aktiven Abschnitt der CT-Konfig ein
    "pct": CT_ZUSTAND + r'''case "$1" in
  status) echo "status: $(ct)" ;;
  shutdown) echo stopped > "$STUB/ct" ;;
  start) echo running > "$STUB/ct" ;;
  set) [ "$3" = --delete ] || sed -i "1i ${3#--}: $4" "$KONF" ;;
  exec) shift 3; case "$1" in
    readlink) [ -n "${LINK-/srv/big/clips}" ] || exit 1; printf '%s\r\n' "${LINK-/srv/big/clips}" ;;
    runuser) exit "${SPERRE_BELEGT:-0}" ;;
    systemctl) echo "${SMBD:-inactive}"; [ "${SMBD:-}" = active ] ;;
    df) printf 'Used\r\n 12G\r\n' ;;
  esac ;;
esac''',
    "lvs": 'echo "$LVS"',
    "pvesm": "echo /dev/pve/vm-102-disk-1",
    # 96 GB in 4-KiB-Blöcken, davon 5 % für root (RESERVE_BLOECKE). Wie das echte tune2fs: Ändern (-m) scheitert,
    # solange der CT das Volume eingehängt hat (Proxmox legt ext4 mit MMP an); -m 0 setzt die Reserve auf 0.
    "tune2fs": CT_ZUSTAND + r'''case "$1" in
  -l) echo "Block count:              25165824"
      echo "Reserved block count:     $(cat "$STUB/reserve" 2>/dev/null || echo "${RESERVE_BLOECKE:-1258291}")" ;;
  -m) if [ "$(ct)" = running ]; then
        echo "tune2fs: MMP: device currently active while trying to open $3" >&2; exit 1
      fi
      [ "$2" != 0 ] || echo 0 > "$STUB/reserve" ;;
esac''',
    "systemctl": 'case "$1" in is-enabled) exit "${TIMER_AN:-1}" ;; is-active) exit 3 ;; esac',
    "findmnt": 'exit "${FINDMNT:-0}"',
    "apt-get": "", "useradd": "", "smbpasswd": "", "pdbedit": "exit 1", "testparm": "",
}
# Änderungen, die im Probe-Modus bzw. nach "nein" nie passieren dürfen.
# "pct exec 102 -- sh": das In-CT-Skript aus Schritt 6 (legt Ordner und Marken an) – lesende Aufrufe im CT
# (readlink, runuser flock, systemctl is-active, df) laufen ohne sh.
AENDERUNGEN = ["pct set", "pct shutdown", "pct start", "pct exec 102 -- sh", "tune2fs -m", "systemctl daemon-reload",
               "systemctl enable", "systemctl disable", "systemctl start", "systemctl restart", "systemctl stop",
               "apt-get", "useradd", "smbpasswd", "testparm"]


class MitStubs(unittest.TestCase):
    SKRIPT: Path

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.t = Path(self._tmp.name)
        self.stub = self.t / "stub"
        self.stub.mkdir()
        for name, inhalt in STUBS.items():
            datei = self.stub / name
            datei.write_text(f'#!/bin/bash\necho "{name} $*" >> "$STUB/aufrufe"\n{inhalt}\n', encoding="utf-8")
            datei.chmod(0o755)
        self.umgebung = {"PATH": f"{self.stub}:{os.environ['PATH']}", "STUB": str(self.stub),
                         "SICH": str(self.t / "sich"), "ORIGINAL": str(self.t / "original")}

    def tearDown(self):
        self._tmp.cleanup()

    def lauf(self, *argumente: str, eingabe: str = "", **umgebung: str) -> subprocess.CompletedProcess:
        env = {**os.environ, **self.umgebung, **umgebung}
        return subprocess.run(["bash", str(self.SKRIPT), *argumente], input=eingabe, capture_output=True,
                              text=True, env=env, timeout=60)

    def aufrufe(self) -> str:
        datei = self.stub / "aufrufe"
        return datei.read_text(encoding="utf-8") if datei.exists() else ""

    def assertNichtsGeaendert(self):
        aufrufe = self.aufrufe()
        for aenderung in AENDERUNGEN:
            self.assertNotIn(aenderung, aufrufe)


class LvmStatus(MitStubs):
    SKRIPT = LVM_STATUS

    def setUp(self):
        super().setUp()
        (self.t / "big").mkdir()
        self.datei = self.t / "big/lvm-status.txt"

    def test_schreibt_vier_zeilen_atomar(self):
        r = self.lauf(LVS="  41.23   2.10", DATEI=str(self.datei))
        self.assertEqual(r.returncode, 0, r.stderr)
        werte = dict(z.split("=", 1) for z in self.datei.read_text(encoding="utf-8").splitlines())
        self.assertEqual(list(werte), ["zeit", "data_prozent", "meta_prozent", "root_frei_gb"])
        self.assertEqual((float(werte["data_prozent"]), float(werte["meta_prozent"])), (41.23, 2.10))
        self.assertGreaterEqual(int(werte["root_frei_gb"]), 0)
        self.assertIsNotNone(datetime.fromisoformat(werte["zeit"]).tzinfo)
        self.assertEqual(os.listdir(self.t / "big"), ["lvm-status.txt"])  # keine Zwischendatei übrig
        self.assertEqual(self.datei.stat().st_mode & 0o777, 0o644)  # im CT lesbar
        self.assertIn("lvs --noheadings --nosuffix -o data_percent,metadata_percent pve/data", self.aufrufe())

    def test_unsinn_laesst_die_alte_datei_stehen(self):
        self.datei.write_text("alt\n", encoding="utf-8")
        r = self.lauf(LVS="  Fehler beim Lesen", DATEI=str(self.datei))
        self.assertEqual(r.returncode, 1)
        self.assertIn("Unerwartetes", r.stderr)
        self.assertEqual(self.datei.read_text(encoding="utf-8"), "alt\n")
        self.assertEqual(os.listdir(self.t / "big"), ["lvm-status.txt"])


KONF = """arch: amd64
hostname: clips
onboot: 1
rootfs: local-lvm:vm-102-disk-0,size=16G
unprivileged: 1
lxc.mount.entry: /mnt/big srv/big none rbind,rslave,create=dir 0 0
{mp}
[vorher]
mp1: local-lvm:vm-102-disk-9,mp=/srv/puffer
snaptime: 1
"""
PUFFER_MP = "mp1: local-lvm:vm-102-disk-1,mp=/srv/puffer,backup=0,mountoptions=noatime;discard,size=96G"


class PufferEinrichten(MitStubs):
    SKRIPT = EINRICHTEN

    def setUp(self):
        super().setUp()
        for ordner in ("sbin", "units"):
            (self.t / ordner).mkdir()
        self.konf = self.t / "102.conf"
        self.konf.write_text(KONF.format(mp=""), encoding="utf-8")  # mp1 nur im Snapshot -> zählt nicht
        self.umgebung.update(KONF=str(self.konf), SBIN=str(self.t / "sbin"), UNITS=str(self.t / "units"),
                             LVS="  400.00  30.00  2.00")

    def test_probe_zeigt_alles_und_aendert_nichts(self):
        vorher = self.konf.read_text(encoding="utf-8")
        r = self.lauf("--probe")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("$ pct shutdown 102 --timeout 180", r.stdout)
        self.assertIn("$ pct set 102 --mp1 'local-lvm:96,mp=/srv/puffer,backup=0,mountoptions=noatime;discard'",
                      r.stdout)
        # Reserve abschalten, solange der CT aus ist (ext4-MMP)
        assertReihenfolge(self, r.stdout, ["$ pct shutdown 102", "$ pct set 102 --mp1",
                                           "$ tune2fs -m 0 '<Gerät des neuen Volumes>'", "$ pct start 102"])
        self.assertIn("Mit ganz vollem Puffer (96 GB): 54.0 %", r.stdout)
        self.assertIn("/srv/clips zeigt auf: /srv/big/clips\n", r.stdout)  # ohne \r
        self.assertIn("install -d -o pipeline -g pipeline -m 755 . eingang replays sessions", r.stdout)
        self.assertIn("$ systemctl enable --now clip-lvm-status.timer", r.stdout)
        self.assertNichtsGeaendert()
        self.assertIn("lvs --noheadings", self.aufrufe())
        self.assertEqual(self.konf.read_text(encoding="utf-8"), vorher)
        self.assertEqual(list((self.t / "sbin").iterdir()) + list((self.t / "units").iterdir()), [])
        self.assertFalse((self.t / "sich").exists() or (self.t / "original").exists())

    def test_nein_heisst_nein(self):
        r = self.lauf(eingabe="n\n")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("Abgebrochen – nichts verändert.", r.stdout)
        self.assertNichtsGeaendert()
        self.assertTrue((self.t / "original/102.conf").exists())  # gesichert wird vorher trotzdem

    def test_pool_zu_voll(self):
        r = self.lauf("--probe", LVS="  200.00  50.00  2.00")  # 50 % + 96/200 = 98 %
        self.assertEqual(r.returncode, 1)
        self.assertIn("Zu knapp", r.stdout)
        self.assertNotIn("pct set", r.stdout)

    def test_metadaten_zu_voll(self):
        r = self.lauf("--probe", LVS="  400.00  10.00  91.00")
        self.assertEqual(r.returncode, 1)
        self.assertIn("Zu knapp", r.stdout)

    def test_lvs_unsinn(self):
        r = self.lauf("--probe", LVS="  kaputt")
        self.assertEqual(r.returncode, 1)
        self.assertIn("Unerwartetes", r.stdout)

    def test_mp1_schon_anders_belegt(self):
        self.konf.write_text(KONF.format(mp="mp1: /mnt/anders,mp=/srv/anders"), encoding="utf-8")
        r = self.lauf("--probe")
        self.assertEqual(r.returncode, 1)
        self.assertIn("schon für etwas anderes belegt", r.stdout)
        self.assertIn("MP=mp2", r.stdout)

    def test_schon_eingerichtet(self):
        self.konf.write_text(KONF.format(mp=PUFFER_MP), encoding="utf-8")
        r = self.lauf("--probe", LVS="  200.00  50.00  2.00")  # Pool voll: zählt nicht, Volume gibt es ja schon
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("schon eingehängt: mp1 = local-lvm:vm-102-disk-1", r.stdout)
        self.assertIn("schon da – übersprungen", r.stdout)
        # CT läuft: tune2fs -m scheiterte an MMP -> nur der Hinweis, kein "$ tune2fs"
        self.assertIn("Reserve noch 5 % – geht nur bei ausgeschaltetem CT (ext4-MMP)", r.stdout)
        self.assertIn("pct shutdown 102; tune2fs -m 0 /dev/pve/vm-102-disk-1; pct start 102", r.stdout)
        self.assertNotIn("$ tune2fs", r.stdout)
        self.assertIn("tune2fs -l /dev/pve/vm-102-disk-1", self.aufrufe())
        self.assertNichtsGeaendert()

    def leer(self, ordner: str) -> bool:
        return not any((self.t / ordner).iterdir())

    def test_wiederholung_nein_heisst_nein(self):
        # Volume schon da: Dann sind die Fragen die einzigen Sperren – bei laufendem CT zum In-CT-Skript und zum
        # Timer (tune2fs nur als Hinweis), bei gestopptem CT zum Start, zu tune2fs und zum Timer (Schritt 6 entfällt)
        self.konf.write_text(KONF.format(mp=PUFFER_MP), encoding="utf-8")
        for status, eingabe in (("running", "n\nn\n"), ("stopped", "n\nn\nn\n")):
            with self.subTest(status):
                r = self.lauf(eingabe=eingabe, CT_STATUS=status)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertIn("schon da – übersprungen", r.stdout)
                self.assertEqual(len(re.findall(r"^übersprungen$", r.stdout, re.M)), 2, r.stdout)
                self.assertNichtsGeaendert()
                self.assertTrue(self.leer("sbin") and self.leer("units"))

    def test_wiederholung_ja_fuehrt_aus(self):
        # Gegenprobe: Mit "j" laufen die Schritte – also sind es wirklich die Fragen, die sperren. Schritt 6 braucht
        # den laufenden CT, tune2fs -m den ausgeschalteten (MMP): also zwei Läufe.
        self.konf.write_text(KONF.format(mp=PUFFER_MP), encoding="utf-8")
        r = self.lauf(eingabe="j\nj\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        aufrufe = self.aufrufe()
        self.assertIn("pct exec 102 -- sh -c set -e", aufrufe)
        self.assertIn("systemctl enable --now clip-lvm-status.timer", aufrufe)
        self.assertEqual(sorted(p.name for p in (self.t / "units").iterdir()),
                         ["clip-lvm-status.service", "clip-lvm-status.timer"])
        self.assertTrue((self.t / "sbin/clip-lvm-status").exists())
        # CT aus und bleibt aus ("n" auf die Startfrage): dann fragt Schritt 5, und "j" führt tune2fs -m 0 aus
        r = self.lauf(eingabe="n\nj\nj\n", CT_STATUS="stopped")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        aufrufe = self.aufrufe()
        self.assertEqual(aufrufe.count("tune2fs -m 0 /dev/pve/vm-102-disk-1"), 1)
        for nie in ("pct set", "pct shutdown", "pct start"):  # Volume und CT bleiben, wie sie sind
            self.assertNotIn(nie, aufrufe)

    def test_wiederholung_bei_laufendem_ct_nur_hinweis(self):
        # Erster echter Lauf auf pve-mini: tune2fs -m bei laufendem CT -> "MMP: device currently active", und
        # set -e brach vor den Schritten 6 und 7 ab. Jetzt: Hinweis fürs Wartungsfenster, kein tune2fs -m, weiter.
        self.konf.write_text(KONF.format(mp=PUFFER_MP), encoding="utf-8")
        r = self.lauf(eingabe="j\nj\n")  # genau zwei Antworten: Schritt 6 und 7 – Schritt 5 fragt nicht
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("/dev/pve/vm-102-disk-1: Reserve noch 5 % – geht nur bei ausgeschaltetem CT (ext4-MMP)",
                      r.stdout)
        self.assertIn("Im nächsten Wartungsfenster:  pct shutdown 102; tune2fs -m 0 /dev/pve/vm-102-disk-1; "
                      "pct start 102", r.stdout)
        aufrufe = self.aufrufe()
        self.assertNotIn("tune2fs -m", aufrufe)
        assertReihenfolge(self, aufrufe, ["tune2fs -l /dev/pve/vm-102-disk-1", "pct exec 102 -- sh -c set -e",
                                          "systemctl enable --now clip-lvm-status.timer"])
        self.assertIn("== Fertig.", r.stdout)
        for nie in ("pct set", "pct shutdown", "pct start"):  # den laufenden CT fasst das Skript nicht an
            self.assertNotIn(nie, aufrufe)
        # Nach dem Handgriff im Wartungsfenster: Reserve 0 -> kein Hinweis mehr
        r = self.lauf(eingabe="n\nn\n", RESERVE_BLOECKE="0")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("/dev/pve/vm-102-disk-1: schon 0 – übersprungen", r.stdout)
        self.assertNotIn("Wartungsfenster", r.stdout)

    def test_gestoppter_ct_wird_nicht_ungefragt_gestartet(self):
        # z. B. absichtlich für Wartung gestoppt, Skript nur für den Timer aus Schritt 7 wiederholt
        self.konf.write_text(KONF.format(mp=PUFFER_MP), encoding="utf-8")
        r = self.lauf(eingabe="n\nn\nn\n", CT_STATUS="stopped")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("übersprungen – CT 102 bleibt aus", r.stdout)
        self.assertIn("übersprungen – CT 102 ist aus (Schritt 4)", r.stdout)  # Schritt 6 ohne Frage übersprungen
        self.assertNichtsGeaendert()
        # "j" auf die Startfrage startet ihn; danach geht tune2fs -m nicht mehr (nur Hinweis), die übrigen Fragen
        # bleiben Fragen
        (self.stub / "aufrufe").unlink()
        r = self.lauf(eingabe="j\nn\nn\n", CT_STATUS="stopped")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.aufrufe().count("pct start 102"), 1)
        self.assertIn("Im nächsten Wartungsfenster:", r.stdout)
        self.assertEqual(len(re.findall(r"^übersprungen$", r.stdout, re.M)), 2, r.stdout)
        self.assertNotIn("pct exec 102 -- sh", self.aufrufe())
        self.assertNotIn("tune2fs -m", self.aufrufe())

    def test_neues_volume_bei_laufendem_ct_reserve_aus_solange_er_aus_ist(self):
        # Schritt 3 fährt den CT herunter: genau dann (zwischen pct set und pct start) geht tune2fs -m trotz MMP
        r = self.lauf(eingabe="j\nj\nj\n")  # Schritt 3, 6 und 7 – Schritt 4 und 5 fragen nicht
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        aufrufe = self.aufrufe()
        assertReihenfolge(self, aufrufe, ["pct shutdown 102 --timeout 180", "pct set 102 --mp1",
                                          "tune2fs -m 0 /dev/pve/vm-102-disk-1", "pct start 102",
                                          "pct exec 102 -- sh -c set -e", "systemctl enable --now clip-lvm-status.timer"])
        self.assertEqual(aufrufe.count("tune2fs -m"), 1)
        self.assertEqual(aufrufe.count("pct start 102"), 1)
        self.assertIn("/dev/pve/vm-102-disk-1: schon 0 – übersprungen", r.stdout)  # Schritt 5
        self.assertNotIn("Wartungsfenster", r.stdout)
        self.assertIn("== Fertig.", r.stdout)

    def test_neues_volume_bei_gestopptem_ct_fragt_vor_dem_start(self):
        # War der CT schon vorher aus, hat das Skript ihn nicht heruntergefahren -> nicht ungefragt starten
        r = self.lauf(eingabe="j\nn\n", CT_STATUS="stopped")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        aufrufe = self.aufrufe()
        assertReihenfolge(self, aufrufe, ["pct set 102 --mp1", "tune2fs -m 0 /dev/pve/vm-102-disk-1"])
        self.assertNotIn("pct shutdown", aufrufe)
        self.assertNotIn("pct start", aufrufe)
        self.assertIn("übersprungen – CT 102 bleibt aus", r.stdout)

    def test_pipeline_laeuft_gerade(self):
        r = self.lauf(eingabe="j\n", SPERRE_BELEGT="1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("Pipeline-Schritt", r.stdout)
        self.assertNichtsGeaendert()

    def test_falscher_aufruf(self):
        self.assertEqual(self.lauf("--prob").returncode, 2)


class PufferZurueck(MitStubs):
    SKRIPT = ZURUECK

    def setUp(self):
        super().setUp()
        self.konf = self.t / "102.conf"
        self.konf.write_text(KONF.format(mp=PUFFER_MP), encoding="utf-8")
        self.umgebung.update(KONF=str(self.konf))

    def test_bricht_ab_solange_die_pipeline_im_puffer_arbeitet(self):
        for link in ("/srv/puffer", "/srv/puffer/", "puffer"):
            with self.subTest(link):
                r = self.lauf("--probe", LINK=link)
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
                self.assertIn("arbeitet noch im Puffer", r.stdout)
                self.assertNotIn("pct set", r.stdout)
        self.assertNichtsGeaendert()

    def test_zurueckschalten_erst_zufluss_stoppen_dann_abgleichen(self):
        # Samba zeigt fest auf /srv/puffer: Kopiert der PC nach dem letzten Abgleich weiter, liegt es nur im Puffer
        r = self.lauf("--probe", LINK="/srv/puffer")
        self.assertEqual(r.returncode, 1)
        assertReihenfolge(self, r.stdout, ["Disable-ScheduledTask", "systemctl stop smbd", "clip-bot",
                                           "10 min warten", "pipeline lager abgleich", "clip-lager.timer",
                                           "ln -sfn /srv/big/clips /srv/clips", "read only = no",
                                           "Gaming-PC zurück auf pve-big"])

    def test_loeschhinweis_nennt_pc_und_lager_status(self):
        r = self.lauf("--probe")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        hinweis = r.stdout[r.stdout.index("Endgültig löschen"):]
        assertReihenfolge(self, hinweis, ["Gaming-PC kopiert nicht mehr in den Puffer", "smbd gestoppt",
                                          "\\\\192.168.178.93\\clips", "'pipeline lager status' 0 offen",
                                          "pct set 102 --delete unusedN"])
        self.assertNotIn("smbd lief eben noch", r.stdout)

    def test_laufender_smbd_warnt_vor_dem_loeschen(self):
        # Nach Rückweg R5 ist smbd gestoppt. Läuft er noch, kann der PC seit dem Abgleich in den Puffer kopiert haben.
        r = self.lauf("--probe", SMBD="active")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("ACHTUNG: smbd läuft noch", r.stdout)
        self.assertIn("smbd lief eben noch – im Zweifel NICHT löschen", r.stdout)
        self.assertNichtsGeaendert()

    def test_kein_link(self):
        r = self.lauf("--probe", LINK="")
        self.assertEqual(r.returncode, 1)
        self.assertIn("kein Link", r.stdout)

    def test_ct_aus(self):
        r = self.lauf("--probe", CT_STATUS="stopped")
        self.assertEqual(r.returncode, 1)
        self.assertIn("pct start 102", r.stdout)

    def test_probe_zeigt_aushaengen(self):
        r = self.lauf("--probe", SMBD="active", TIMER_AN="0")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Puffer: mp1 = local-lvm:vm-102-disk-1", r.stdout)
        self.assertIn("Im Puffer belegt: 12G", r.stdout)
        self.assertIn("$ pct exec 102 -- systemctl disable --now smbd", r.stdout)
        self.assertIn("$ pct set 102 --delete mp1", r.stdout)
        self.assertIn("$ systemctl disable --now clip-lvm-status.timer", r.stdout)
        self.assertIn("pct set 102 --delete unusedN", r.stdout)  # endgültiges Löschen nur als Hinweis
        self.assertNichtsGeaendert()

    def test_nein_heisst_nein(self):
        r = self.lauf(eingabe="n\nn\nn\n", SMBD="active", TIMER_AN="0")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.count("übersprungen"), 3)
        self.assertNichtsGeaendert()
        self.assertFalse((self.t / "sich").exists())


@unittest.skipIf(Path("/etc/pve").is_dir(), "läuft auf einem Proxmox-Host")
class SambaEinrichten(MitStubs):
    SKRIPT = SAMBA

    def setUp(self):
        super().setUp()
        self.puffer = self.t / "puffer"
        self.puffer.mkdir()
        (self.puffer / ".clip-puffer").touch()
        self.smb_konf = self.t / "smb.conf"
        self.umgebung.update(PUFFER=str(self.puffer), SMB_KONF=str(self.smb_konf))

    def test_probe_zeigt_alles_und_aendert_nichts(self):
        r = self.lauf("--probe")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        if not shutil.which("smbd"):
            self.assertIn("$ apt-get install -y --no-install-recommends samba", r.stdout)
        self.assertIn("$ useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin gamingpc",
                      r.stdout)
        self.assertIn("smbpasswd -a gamingpc", r.stdout)
        self.assertIn(f"$ testparm -s {self.smb_konf}.neu", r.stdout)
        self.assertIn("$ systemctl restart smbd", r.stdout)
        self.assertNichtsGeaendert()
        self.assertEqual(sorted(p.name for p in self.t.iterdir()), ["puffer", "stub"])  # keine smb.conf, keine Sicherung

    def test_nein_heisst_nein(self):
        r = self.lauf(eingabe="n\n")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("Abgebrochen", r.stdout)
        self.assertNichtsGeaendert()

    def test_lager_statt_puffer(self):
        (self.puffer / ".clip-lager").touch()
        r = self.lauf("--probe")
        self.assertEqual(r.returncode, 1)
        self.assertIn("das wäre das LAGER", r.stdout)
        self.assertNichtsGeaendert()

    def test_puffer_nicht_eingehaengt(self):
        r = self.lauf("--probe", FINDMNT="1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("nicht eingehängt", r.stdout)

    def test_ohne_puffer_marke(self):
        (self.puffer / ".clip-puffer").unlink()
        r = self.lauf("--probe")
        self.assertEqual(r.returncode, 1)
        self.assertIn(".clip-puffer fehlt", r.stdout)


if __name__ == "__main__":
    unittest.main()
