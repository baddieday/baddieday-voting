"""Mehrbenutzer, Stufe 2, PR 1: der Briefkasten der Freunde auf dem vServer (deploy/vserver/briefkasten-*.sh).

Drei Teile:
- Skripte: bash -n, shellcheck, Kopf, und nichts darin löscht (kein rm, userdel, wipefs, …; mkfs nur für ein neues
  Bild).
- Scheinwurzel mit Attrappen (useradd, usermod, groupadd, fallocate, mkfs.ext4, mount, umount, findmnt, systemctl,
  ufw, ss, ip, df, losetup, systemd-detect-virt, sshd) – läuft ohne root: Die Probe ändert nichts; der echte Lauf legt
  alles genau einmal an, ein zweiter ändert nichts; zu wenig Platz, kein Loop-Gerät, ein Container oder Tailscale im
  Userspace-Modus brechen vor jeder Änderung ab; der Rückweg schaltet nur aus. Je Freund: Fach, Marke, Unterordner,
  zwei Schlüsseldateien; wachsen, sperren; ein Schlüssel bei zwei Freunden, fremde Namen und Schlüssel mit Optionen
  werden abgelehnt. Statisch an der erzeugten sshd_config: Hochladen ohne Lesen, Löschen, mkdir, Links, posix-rename;
  Abholen nur im Block „Match LocalAddress“ mit -R und eigener Schlüsseldatei. Der normale SSH-Zugang bleibt unberührt.
- Echter sshd (nur als root mit Mount- und Netz-Namensraum und einem sshd – installiert oder über BRIEFKASTEN_SSHD –,
  sonst übersprungen, wie der Kernel-Nachbau in test_deploy_benutzer): Die Skripte richten in einem privaten
  Namensraum zwei Fächer ein, dann gilt: Der PC-Schlüssel lädt hoch (put, reput, rename), liest, löscht und legt keine
  Ordner an, überschreibt per rename nichts und kommt nicht an die Marke; der Mini-Schlüssel liest nur, nur über die
  Tailnet-Adresse und nur von der Adresse des Mini; max kommt nicht in das Fach von eva; nicht eingehängt heißt
  „Permission denied“, voll heißt „Failure“; gesperrt kommt niemand herein; sshd -t und briefkasten-pruefen.sh sind grün.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_deploy_puffer import assertReihenfolge

PROJEKT = Path(__file__).resolve().parents[1]
VSERVER = PROJEKT / "deploy" / "vserver"
EINRICHTEN, FREUND, PRUEFEN = (VSERVER / f"briefkasten-{n}.sh" for n in ("einrichten", "freund", "pruefen"))
SKRIPTE = (EINRICHTEN, FREUND, PRUEFEN)
GB = 10 ** 9
TS_IP = "100.100.1.1"      # Tailnet-Adresse des vServers (Attrappe ip)
MINI_IP = "100.100.1.2"    # Tailnet-Adresse des CT clips
# Was das Hochladen nie darf (plan: read, remove, mkdir, setstat, symlink, hardlink, posix-rename, copy-data)
VERBOTEN = {"read", "remove", "rmdir", "mkdir", "setstat", "fsetstat", "lsetstat", "symlink", "hardlink",
            "posix-rename", "copy-data"}
# Ein Lösch-Befehl am Anfang einer Anweisung (auch nach tu, then, &&, $( …) – die SFTP-Wörter „remove“/„rmdir“ in
# der Liste dessen, was das Hochladen nicht darf, sind keine Befehle
NIE_LOESCHEN = re.compile(r"(?:^|[;&|({]|\bthen|\bdo|\belse|\btu|\bexec|\bxargs)\s*"
                          r"(?:rm|rmdir|userdel|groupdel|deluser|delgroup|shred|wipefs|unlink|truncate|lvremove)\b"
                          r"|--delete|--purge|\bufw\s+delete", re.M)

# Attrappen: jeder Aufruf landet in $STUB/aufrufe. passwd/group/shadow liegen in $STUB; chown merkt sich den Besitzer,
# stat gibt ihn zurück (Rechte echt). Ein Fach-Bild ist eine kleine Textdatei („groesse=…“, nach mkfs „fs=ext4“);
# mount/umount/findmnt führen $STUB/mounts, df rechnet mit DF_GESAMT/DF_FREI abzüglich aller Bilder. systemctl merkt
# sich Eingeschaltetes ($STUB/an) und Laufendes ($STUB/aktiv). Löschen darf nie vorkommen (Exit 99).
ATTRAPPEN = {
    "id": '[ "$1" = -u ] && echo 0',
    "systemd-detect-virt": 'case "${1:-}" in --container) [ "${CONTAINER:-0}" = 1 ]; exit ;; esac\n'
                           'if [ "${CONTAINER:-0}" = 1 ]; then echo lxc; else echo "${VIRT:-kvm}"; fi',
    "losetup": 'if [ "${KEIN_LOOP:-0}" = 1 ]; then echo "losetup: cannot find an unused loop device" >&2; exit 1; fi\n'
               'echo /dev/loop3',
    "ip": 'if [ "${KEIN_TAILSCALE:-0}" = 1 ]; then echo "Device \\"tailscale0\\" does not exist." >&2; exit 1; fi\n'
          'printf "4: tailscale0    inet %s/32 scope global tailscale0\\\\       valid_lft forever\\n" "${TS_IP}"',
    "ss": 'if grep -qx briefkasten-sshd "$STUB/aktiv" 2>/dev/null || [ "${SS_BELEGT:-0}" = 1 ]; then\n'
          '  echo "LISTEN 0      128          0.0.0.0:2222      0.0.0.0:*"; fi',
    "sshd": 'case "$1" in\n'
            '  -V) echo "OpenSSH_${SSHD_VERSION:-9.6p1} Ubuntu-3ubuntu13.19, OpenSSL 3.0.13 30 Jan 2024" >&2 ;;\n'
            '  -t) [ -f "$3" ] || exit 1; exit "${SSHD_T_RC:-0}" ;;\n'
            'esac',
    "df": r'''p="${*: -1}"
echo "     1B-blocks          Used         Avail"
z="$(awk -v p="$p" '$1 == p' "$STUB/mounts" 2>/dev/null)"
if [ -n "$z" ]; then
  g="$(sed -n 's/^groesse=//p' "$(cut -d' ' -f2 <<< "$z")")"; b="${FACH_BELEGT:-0}"
  echo "$g $b $(( g - b ))"
else
  belegt=0
  for f in "$TESTORDNER"/srv/briefkasten/bilder/*.img; do
    [ -f "$f" ] && belegt=$(( belegt + $(sed -n 's/^groesse=//p' "$f") ))
  done
  g="${DF_GESAMT:-200000000000}"; f=$(( ${DF_FREI:-150000000000} - belegt ))
  echo "$g $(( g - f )) $f"
fi''',
    "getent": 'f="$STUB/$1"; [ -f "$f" ] || exit 2\ngrep -m1 "^$2:" "$f" || exit 2',
    "groupadd": 'n="${*: -1}"; echo "$n:x:$(( 900 + $(wc -l < "$STUB/group") )):" >> "$STUB/group"',
    "useradd": r'''n="${*: -1}"; g=""; pw="!"; sh=""
while [ $# -gt 1 ]; do case "$1" in --gid) g="$2"; shift ;; --password) pw="$2"; shift ;; --shell) sh="$2"; shift ;; esac
  shift; done
gid="$(grep "^$g:" "$STUB/group" | cut -d: -f3)"
echo "$n:x:$(( 1500 + $(wc -l < "$STUB/passwd") )):$gid::/nonexistent:$sh" >> "$STUB/passwd"
echo "$n:$pw:20000:0:99999:7:::" >> "$STUB/shadow"''',
    "usermod": 'n="${*: -1}"\ncase "$1" in -L) sed -i "s/^$n:/$n:!/" "$STUB/shadow" ;; -U) sed -i "s/^$n:!/$n:/" "$STUB/shadow" ;; esac',
    "chown": 'b="$1"; shift\nfor p in "$@"; do case "$p" in "$TESTORDNER"/*) echo "$p $b" >> "$STUB/besitzer" ;;\n'
             '  *) echo "chown außerhalb der Testordner: $p" >&2; exit 99 ;; esac; done',
    "chmod": 'case "${*: -1}" in "$TESTORDNER"/*) PATH=/usr/bin:/bin exec chmod "$@" ;; esac\n'
             'echo "chmod außerhalb der Testordner: $*" >&2; exit 99',
    "stat": r'''if [ "$1" = -c ]; then case "$2" in
  "%U:%G %a") [ -e "$3" ] || exit 1
    b="$(awk -v p="$3" '$1 == p {b = $2} END {print b}' "$STUB/besitzer" 2>/dev/null)"
    echo "${b:-root:root} $(PATH=/usr/bin:/bin stat -c %a "$3")"; exit 0 ;;
  %s) case "$3" in *.img) sed -n 's/^groesse=//p' "$3"; exit 0 ;; esac ;;
  "%s %b %B") case "$3" in *.img) g="$(sed -n 's/^groesse=//p' "$3")"
    if [ "${LOECHER:-0}" = 1 ]; then echo "$g 8 512"; else echo "$g $(( g / 512 )) 512"; fi; exit 0 ;; esac ;;
esac; fi
PATH=/usr/bin:/bin exec stat "$@"''',
    "fallocate": r'''[ "$1" = -l ] || exit 1
if [ -f "$3" ]; then sed -i "s/^groesse=.*/groesse=$2/" "$3"; else printf 'groesse=%s\n' "$2" > "$3"; fi''',
    "mkfs.ext4": 'f="${*: -1}"\nif grep -q "^fs=" "$f"; then echo "mkfs über ein vorhandenes Dateisystem!" >&2; exit 99; fi\n'
                 'echo fs=ext4 >> "$f"',
    "blkid": 'grep -q "^fs=ext4" "${*: -1}" 2>/dev/null && echo ext4\ntrue',
    "mount": r'''[ "$1 $2 $3" = "-t ext4 -o" ] || exit 1
[ -d "$6" ] && grep -q "^fs=ext4" "$5" || exit 32
echo "$6 $5 $4" >> "$STUB/mounts"''',
    "umount": r'''if [ "${BELEGT:-0}" = 1 ]; then echo "umount: $1: target is busy." >&2; exit 32; fi
grep -v "^$1 " "$STUB/mounts" > "$STUB/mounts.neu" || true; cat "$STUB/mounts.neu" > "$STUB/mounts"''',
    "findmnt": r'''z="$(awk -v p="${*: -1}" '$1 == p' "$STUB/mounts" 2>/dev/null)"; [ -n "$z" ] || exit 1
o="$(cut -d' ' -f3 <<< "$z")"
case "$3" in FSTYPE) echo ext4 ;; *) echo "ext4 rw,${o#loop,},relatime" ;; esac''',
    "e2fsck": 'exit "${E2FSCK_RC:-0}"',
    "resize2fs": "",
    "systemctl": r'''case "$1" in
  is-active) shift; q=0; if [ "$1" = -q ]; then q=1; shift; fi
    if grep -qx "$1" "$STUB/aktiv" 2>/dev/null; then z=active; else z=inactive; fi
    [ "$q" = 1 ] || echo "$z"; [ "$z" = active ] ;;
  is-enabled) shift; [ "$1" = -q ] && shift; grep -qx "$1" "$STUB/an" 2>/dev/null ;;
  enable) shift; jetzt=0; for u in "$@"; do case "$u" in --now) jetzt=1 ;;
    *) echo "$u" >> "$STUB/an"; [ "$jetzt" = 0 ] || echo "$u" >> "$STUB/aktiv" ;; esac; done ;;
  disable) shift; for u in "$@"; do case "$u" in --now) ;; *) for f in an aktiv; do
    grep -vx "$u" "$STUB/$f" > "$STUB/$f.neu" 2>/dev/null || true; cat "$STUB/$f.neu" > "$STUB/$f"; done ;; esac; done ;;
esac''',
    "ufw": r'''case "$1" in
  status) if [ "${UFW:-an}" = aus ]; then echo "Status: inactive"; exit 0; fi
    printf 'Status: active\n\nTo                         Action      From\n--                         ------      ----\n'
    printf '22/tcp                     ALLOW       Anywhere\n'; cat "$STUB/ufw-regeln" 2>/dev/null || true ;;
  allow) echo "$2                   ALLOW       Anywhere" >> "$STUB/ufw-regeln" ;;
esac''',
    # Löschen darf nie vorkommen – auch nicht aus Versehen über eine Attrappe
    "rm": "exit 99", "rmdir": "exit 99", "userdel": "exit 99", "groupdel": "exit 99", "wipefs": "exit 99",
    "shred": "exit 99",
}
# Aufrufe, die etwas ändern (Probe, „nein“ und Abbrüche: keiner davon)
AENDERUNGEN = ("groupadd", "useradd", "usermod", "chown", "chmod", "fallocate", "mkfs", "mount", "umount", "e2fsck",
               "resize2fs", "systemctl enable", "systemctl disable", "systemctl start", "systemctl stop",
               "systemctl restart", "systemctl reload", "systemctl daemon-reload", "ufw allow", "ssh-keygen -q",
               "rm", "rmdir", "userdel", "groupdel", "wipefs", "shred")
FSTAB_ANFANG = "UUID=1234-abcd / ext4 errors=remount-ro 0 1\n"
ADMIN_SSHD = "Port 22\nPermitRootLogin prohibit-password\n"


def pruefe_skript(test: unittest.TestCase, pfad: Path) -> None:
    text = pfad.read_text(encoding="utf-8")
    test.assertTrue(text.startswith("#!/usr/bin/env bash\n"), pfad.name)
    test.assertIn("\nset -euo pipefail\n", text, pfad.name)
    test.assertTrue(os.access(pfad, os.X_OK), f"{pfad.name} nicht ausführbar")
    befehle = "\n".join(z for z in text.splitlines() if not z.lstrip().startswith("#"))
    test.assertIsNone(NIE_LOESCHEN.search(befehle), pfad.name)
    # Der normale SSH-Zugang wird nie angefasst: /etc/ssh kommt höchstens im Text vor, nie in einem Befehl
    for zeile in text.splitlines():
        if "/etc/ssh" in zeile:
            test.assertRegex(zeile, r"^\s*#|bleibt unberührt", pfad.name)
    r = subprocess.run(["bash", "-n", str(pfad)], capture_output=True, text=True)
    test.assertEqual(r.returncode, 0, f"{pfad.name}: {r.stderr}")
    if shutil.which("shellcheck"):
        r = subprocess.run(["shellcheck", "-S", "warning", str(pfad)], capture_output=True, text=True,
                           env={**os.environ, "LC_ALL": "C.UTF-8"})
        test.assertEqual(r.returncode, 0, r.stdout + r.stderr)


def sshd_bloecke(text: str) -> dict[str, dict[str, str]]:
    """sshd_config als {"global" | "Match …": {schlüssel (klein): wert}} – wie sshd: Match gilt bis zum nächsten Match."""
    bloecke: dict[str, dict[str, str]] = {"global": {}}
    aktuell = bloecke["global"]
    for zeile in text.splitlines():
        zeile = zeile.strip()
        if not zeile or zeile.startswith("#"):
            continue
        schluessel, _, wert = zeile.partition(" ")
        if schluessel.lower() == "match":
            aktuell = bloecke.setdefault(f"Match {wert.strip()}", {})
        else:
            aktuell.setdefault(schluessel.lower(), wert.strip())
    return bloecke


def erlaubnisliste(forcecommand: str) -> set[str]:
    teile = forcecommand.split()
    return set(teile[teile.index("-p") + 1].split(",")) if "-p" in teile else set()


class Skripte(unittest.TestCase):
    def test_kopf_syntax_shellcheck_nie_loeschen(self):
        for skript in SKRIPTE:
            with self.subTest(skript.name):
                pruefe_skript(self, skript)

    def test_mkfs_nur_fuer_ein_neues_bild(self):
        """Ein Dateisystem entsteht nur in einem Bild, das es eben noch nicht gab – nie über einem vorhandenen Fach."""
        zeilen = FREUND.read_text(encoding="utf-8").splitlines()
        stellen = [i for i, z in enumerate(zeilen) if "mkfs" in z and not z.lstrip().startswith("#")]
        self.assertEqual(len(stellen), 1)
        davor = [z.strip() for z in zeilen[:stellen[0]] if z.strip() and not z.lstrip().startswith("#")]
        self.assertEqual(davor[-2:][0], 'if [ ! -e "$BILD" ]; then')
        self.assertTrue(davor[-1].startswith("tu fallocate -l"))
        self.assertIn("-E nodiscard", zeilen[stellen[0]])   # sonst gäbe mkfs den reservierten Platz gleich frei
        for skript in (EINRICHTEN, PRUEFEN):
            self.assertNotIn("mkfs", skript.read_text(encoding="utf-8"))


class Scheinwurzel(unittest.TestCase):
    """Die drei Skripte gegen eine Scheinwurzel: /etc/briefkasten, /srv/briefkasten, Units, fstab, /root/briefkasten
    liegen im Testordner; der normale SSH (/etc/ssh/sshd_config) liegt dort auch und darf sich nie ändern."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.t = t = Path(os.path.realpath(self._tmp.name))
        self.stub = t / "stub"
        self.stub.mkdir()
        keygen = shutil.which("ssh-keygen")
        if not keygen:
            self.skipTest("ssh-keygen fehlt")
        attrappen = {**ATTRAPPEN, "ssh-keygen": f"exec {shlex.quote(keygen)} \"$@\""}
        for name, inhalt in attrappen.items():
            datei = self.stub / name
            datei.write_text(f'#!/bin/bash\necho "{name} $*" >> "$STUB/aufrufe"\n{inhalt}\n', encoding="utf-8")
            datei.chmod(0o755)
        (self.stub / "passwd").write_text("root:x:0:0:root:/root:/bin/bash\n", encoding="utf-8")
        (self.stub / "group").write_text("root:x:0:\n", encoding="utf-8")
        (self.stub / "shadow").write_text("root:*:20000:0:99999:7:::\n", encoding="utf-8")
        self.etc = t / "etc/briefkasten"
        self.srv = t / "srv/briefkasten"
        self.units = t / "etc/systemd/system"
        self.ablage = t / "root/briefkasten"
        self.fstab = t / "etc/fstab"
        for ordner in (self.units, t / "srv", t / "root", t / "run/systemd/system", t / "run/sshd", t / "etc/ssh",
                       t / "schluessel"):
            ordner.mkdir(parents=True, exist_ok=True)
        self.fstab.write_text(FSTAB_ANFANG, encoding="utf-8")
        (t / "etc/ssh/sshd_config").write_text(ADMIN_SSHD, encoding="utf-8")
        self.env = {**os.environ, "PATH": f"{self.stub}:{os.environ['PATH']}", "STUB": str(self.stub),
                    "TESTORDNER": str(t), "BK_ETC": str(self.etc), "BK_SRV": str(self.srv), "UNITS": str(self.units),
                    "ABLAGE": str(self.ablage), "FSTAB": str(self.fstab), "SYSTEMD_LAUF": str(t / "run/systemd/system"),
                    "PRIVSEP": str(t / "run/sshd"), "TS_IP": TS_IP, "LC_ALL": "C.UTF-8"}

    # --- Hilfen ---------------------------------------------------------------------------------------------------

    def lauf(self, skript: Path, *argumente: str, eingabe: str = "", **umgebung: str) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", str(skript), *argumente], input=eingabe, capture_output=True, text=True,
                              env={**self.env, **umgebung}, timeout=120)

    def ok(self, r: subprocess.CompletedProcess, code: int = 0) -> subprocess.CompletedProcess:
        self.assertEqual(r.returncode, code, r.stdout + r.stderr)
        return r

    def aufrufe(self) -> str:
        datei = self.stub / "aufrufe"
        return datei.read_text(encoding="utf-8") if datei.exists() else ""

    def neu(self) -> None:
        (self.stub / "aufrufe").unlink(missing_ok=True)

    def aenderungen(self) -> list[str]:
        return [z for z in self.aufrufe().splitlines() if z.startswith(AENDERUNGEN)]

    def zustand(self) -> dict[str, tuple[int, bytes]]:
        """Jeder Pfad außer den Attrappen (und deren Merkdateien): Rechte und Inhalt."""
        return {str(p.relative_to(self.t)): (p.lstat().st_mode, p.read_bytes() if p.is_file() else b"")
                for p in self.t.rglob("*")
                if not p.is_relative_to(self.stub) and not p.is_relative_to(self.t / "schluessel")}

    def besitzer(self, pfad: Path) -> str:
        datei = self.stub / "besitzer"
        zeilen = [z.split(" ", 1)[1] for z in datei.read_text(encoding="utf-8").splitlines()
                  if z.split(" ", 1)[0] == str(pfad)] if datei.exists() else []
        return zeilen[-1] if zeilen else "root:root"

    def modus(self, pfad: Path) -> int:
        return stat.S_IMODE(pfad.stat().st_mode)

    def schluessel(self, name: str) -> str:
        pfad = self.t / "schluessel" / name
        if not pfad.exists():
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", name, "-f", str(pfad)], check=True)
        return pfad.with_suffix(".pub").read_text(encoding="utf-8").strip()

    def einrichten(self, **umgebung: str) -> subprocess.CompletedProcess:
        """Der echte Lauf: einrichten, ufw-Regel, einschalten (je j)."""
        return self.ok(self.lauf(EINRICHTEN, "--mini-ip", MINI_IP, eingabe="j\nj\nj\n", **umgebung))

    def freund(self, name: str, *mehr: str, eingabe: str = "j\n", code: int = 0, **umgebung: str):
        return self.ok(self.lauf(FREUND, name, "--pc", self.schluessel(f"{name}-pc"), "--abholen",
                                 self.schluessel(f"{name}-mini"), *mehr, eingabe=eingabe, **umgebung), code)

    def fach(self, name: str) -> Path:
        return self.srv / "fach" / f"bk-{name}" / "fach"

    # --- Einrichten -----------------------------------------------------------------------------------------------

    def test_einrichten_probe_aendert_nichts(self):
        vorher = self.zustand()
        r = self.ok(self.lauf(EINRICHTEN, "--mini-ip", MINI_IP, "--probe"))
        self.assertEqual(self.zustand(), vorher)
        self.assertEqual(self.aenderungen(), [])
        assertReihenfolge(self, r.stdout, [
            "PROBE", "Loop-Geräte: ja (/dev/loop3 ist frei)", "sshd:", "(OpenSSH_9.6)",
            f"Tailnet-Adresse des vServers: {TS_IP}", f"Mini (CT clips): {MINI_IP}", "Port 2222: frei",
            "Für Fächer frei: 120 GB", "$ groupadd --system briefkasten", f"$ mkdir {self.etc}",
            f"$ mkdir {self.srv}/bilder", f"$ chmod 700 {self.srv}/bilder", "$ ssh-keygen -q -t ed25519",
            f"würde {self.etc}/sshd_config schreiben, vorher geprüft mit sshd -t -f",
            "ListenAddress 0.0.0.0:2222", f"Match LocalAddress {TS_IP}", "$ systemctl daemon-reload",
            "ufw: Port 2222/tcp öffnen", "$ ufw allow 2222/tcp comment 'Briefkasten der Freunde'",
            "Firewall des Hosters", "$ systemctl enable --now briefkasten-sshd",
            f"Probe fertig – nichts verändert. Echt:  bash {EINRICHTEN} --mini-ip {MINI_IP}"])

    def test_einrichten_einmal_dann_nichts_mehr_rueckweg_schaltet_nur_aus(self):
        r = self.einrichten()
        self.assertIn("briefkasten:x:", (self.stub / "group").read_text(encoding="utf-8"))
        for pfad, modus in ((self.etc, 0o755), (self.etc / "hochladen", 0o755), (self.etc / "abholen", 0o755),
                            (self.srv, 0o755), (self.srv / "fach", 0o755), (self.srv / "bilder", 0o700),
                            (self.etc / "sshd_config", 0o644), (self.etc / "briefkasten.conf", 0o644),
                            (self.units / "briefkasten-sshd.service", 0o644)):
            with self.subTest(pfad.name):
                self.assertEqual((self.besitzer(pfad), self.modus(pfad)), ("root:root", modus))
        self.assertTrue((self.etc / "hostkey_ed25519").is_file())
        self.assertEqual((self.etc / "briefkasten.conf").read_text(encoding="utf-8").splitlines()[1:],
                         ["PORT=2222", f"TAILNET_IP={TS_IP}", f"MINI_IP={MINI_IP}"])
        self.assertFalse((self.etc / "sshd_config.neu").exists())
        aufrufe = self.aufrufe()
        self.assertIn(f"sshd -t -f {self.etc}/sshd_config.neu", aufrufe)   # erst geprüft, dann übernommen
        self.assertIn("ufw allow 2222/tcp comment Briefkasten der Freunde", aufrufe)
        self.assertIn("systemctl enable --now briefkasten-sshd", aufrufe)
        # nur der eigene Dienst – der normale SSH bleibt unberührt
        self.assertEqual({z.split()[-1] for z in aufrufe.splitlines() if z.startswith("systemctl ")
                          and not z.startswith("systemctl daemon-reload")}, {"briefkasten-sshd"})
        self.assertEqual((self.t / "etc/ssh/sshd_config").read_text(encoding="utf-8"), ADMIN_SSHD)
        unit = (self.units / "briefkasten-sshd.service").read_text(encoding="utf-8")
        self.assertNotRegex(unit, r"(?m)^RuntimeDirectory")       # /run/sshd teilt er sich mit dem normalen SSH
        self.assertIn("ExecStartPre=/bin/mkdir -p -m 0755 /run/sshd", unit)
        self.assertIn(f"ExecStartPre={self.stub}/sshd -t -f {self.etc}/sshd_config", unit)
        self.assertIn(f"ExecStart={self.stub}/sshd -D -e -f {self.etc}/sshd_config", unit)
        self.assertIn("KillMode=process", unit)                   # Neustart beendet keinen laufenden Upload
        self.assertIn("WantedBy=multi-user.target", unit)
        self.assertIn("Alles in Ordnung.", r.stdout)               # briefkasten-pruefen.sh am Ende
        self.assertIn("Noch kein Fach", r.stdout)

        # Zweiter Lauf: keine Änderung, keine Datei anders
        vorher = self.zustand()
        self.neu()
        r = self.ok(self.lauf(EINRICHTEN, eingabe="j\n"))     # Mini-IP und Port aus briefkasten.conf
        self.assertEqual(self.aenderungen(), [])
        self.assertEqual(self.zustand(), vorher)
        for satz in ("Gruppe briefkasten: schon da", "Hostschlüssel: schon da", "sshd_config: schon da",
                     "ufw: Port 2222/tcp ist schon offen", "briefkasten-sshd läuft schon", "Rückweg-Skript:"):
            self.assertIn(satz, r.stdout)

        # Rückweg: schaltet nur aus – Fächer, Schlüssel, Konfig bleiben
        rueckweg = self.ablage / "zurueck.sh"
        self.assertEqual(self.modus(rueckweg), 0o700)
        pruefe_skript(self, rueckweg)
        self.neu()
        self.ok(self.lauf(rueckweg, "--probe"))
        self.assertEqual(self.aenderungen(), [])
        vorher = self.zustand()
        r = self.ok(self.lauf(rueckweg, eingabe="j\n"))
        self.assertEqual(self.aenderungen(), ["systemctl disable --now briefkasten-sshd"])
        self.assertEqual(self.zustand(), vorher)
        self.assertIn("gelöscht ist nichts", r.stdout)

    def test_einrichten_bricht_vor_jeder_aenderung_ab(self):
        vorher = self.zustand()
        for umgebung, satz in (({"KEIN_LOOP": "1"}, "kein freies Loop-Gerät"),
                               ({"CONTAINER": "1"}, "der vServer ist ein Container (lxc)"),
                               ({"KEIN_TAILSCALE": "1"}, "Userspace-Modus"),
                               ({"SS_BELEGT": "1"}, "auf Port 2222 lauscht schon etwas anderes"),
                               ({"SSHD_VERSION": "7.9p1"}, "OpenSSH_7.9 ist zu alt"),
                               # 40 GB, davon 15 frei: das System behält 10 GB -> 5 GB für Fächer, zu wenig für eins
                               ({"DF_GESAMT": str(40 * GB), "DF_FREI": str(15 * GB)}, "für Fächer bleiben nur 5 GB")):
            with self.subTest(satz):
                self.neu()
                r = self.ok(self.lauf(EINRICHTEN, "--mini-ip", MINI_IP, eingabe="j\nj\nj\n", **umgebung), 1)
                self.assertIn(satz, r.stdout)
                self.assertIn("Abgebrochen", r.stdout)
                self.assertEqual(self.aenderungen(), [])
                self.assertEqual(self.zustand(), vorher)
        for argumente in (["--mini-ip", "192.168.1.5"], ["--mini-ip", TS_IP], [], ["--mini-ip", MINI_IP, "--port", "80"],
                          ["--mini-ip", "100.064.1.2"]):
            with self.subTest(argumente=argumente):
                self.neu()
                r = self.lauf(EINRICHTEN, *argumente, eingabe="j\n")
                self.assertIn(r.returncode, (1, 2), r.stdout + r.stderr)
                self.assertEqual(self.aenderungen(), [])
                self.assertEqual(self.zustand(), vorher)

    def test_sshd_config_zwei_profile(self):
        """Statisch: Hochladen (global) mit fester Erlaubnisliste ohne Lesen, Löschen, …; Abholen nur im Block
        Match LocalAddress <Tailnet-IP>, nur lesen, eigene Schlüsseldatei. Ab OpenSSH 9.8 Bremse ohne das Tailnet."""
        for version, strafen in (("9.6p1", False), ("9.8p1", True)):
            with self.subTest(version):
                shutil.rmtree(self.etc, ignore_errors=True)
                shutil.rmtree(self.ablage, ignore_errors=True)
                (self.stub / "aktiv").unlink(missing_ok=True)
                self.einrichten(SSHD_VERSION=version)
                text = (self.etc / "sshd_config").read_text(encoding="utf-8")
                b = sshd_bloecke(text)
                self.assertEqual(set(b), {"global", f"Match LocalAddress {TS_IP}"})
                g, m = b["global"], b[f"Match LocalAddress {TS_IP}"]
                hochladen = g["forcecommand"]
                self.assertTrue(hochladen.startswith("internal-sftp -d /fach -u 0077 -p "))
                self.assertEqual(erlaubnisliste(hochladen) & VERBOTEN, set())
                self.assertLessEqual({"open", "write", "close", "rename", "opendir", "readdir", "statvfs"},
                                     erlaubnisliste(hochladen))
                self.assertNotIn("-P", hochladen.split())
                self.assertEqual(g["authorizedkeysfile"], f"{self.etc}/hochladen/%u")
                self.assertEqual(m, {"authorizedkeysfile": f"{self.etc}/abholen/%u",
                                     "forcecommand": "internal-sftp -d /fach -R"})
                for schluessel, wert in (("listenaddress", "0.0.0.0:2222"), ("passwordauthentication", "no"),
                                         ("kbdinteractiveauthentication", "no"), ("usepam", "no"),
                                         ("permitrootlogin", "no"), ("allowgroups", "briefkasten"),
                                         ("disableforwarding", "yes"), ("permittty", "no"),
                                         ("authenticationmethods", "publickey"), ("loglevel", "VERBOSE"),
                                         ("chrootdirectory", f"{self.srv}/fach/%u"),
                                         ("hostkey", f"{self.etc}/hostkey_ed25519"),
                                         ("pidfile", "/run/briefkasten-sshd.pid")):
                    self.assertEqual(g[schluessel], wert, schluessel)
                self.assertEqual(g.get("persourcepenaltyexemptlist"), "100.64.0.0/10" if strafen else None)

    # --- Je Freund ------------------------------------------------------------------------------------------------

    def test_freund_anlegen_einmal_dann_nichts_mehr(self):
        self.einrichten()
        self.neu()
        vorher = self.zustand()
        r = self.freund("max", "--probe")
        self.assertEqual(self.zustand(), vorher)
        self.assertEqual(self.aenderungen(), [])
        self.assertIn("$ useradd --system --gid briefkasten --no-create-home --home-dir /nonexistent --shell "
                      "/usr/sbin/nologin --password '*' bk-max", r.stdout)
        self.assertIn("Probe fertig – nichts verändert", r.stdout)

        r = self.freund("max")
        u = "bk-max:x:"
        passwd = [z for z in (self.stub / "passwd").read_text(encoding="utf-8").splitlines() if z.startswith(u)]
        gid = next(z for z in (self.stub / "group").read_text(encoding="utf-8").splitlines()
                   if z.startswith("briefkasten:")).split(":")[2]
        self.assertEqual(len(passwd), 1)
        self.assertEqual(passwd[0].split(":")[3:], [gid, "", "/nonexistent", "/usr/sbin/nologin"])
        self.assertIn("bk-max:*:", (self.stub / "shadow").read_text(encoding="utf-8"))   # nicht „!“: das sperrte
        bild = self.srv / "bilder/bk-max.img"
        self.assertEqual(bild.read_text(encoding="utf-8"), f"groesse={20 * GB}\nfs=ext4\n")
        self.assertEqual((self.besitzer(bild), self.modus(bild)), ("root:root", 0o600))
        aufrufe = self.aufrufe()
        self.assertIn(f"mkfs.ext4 -q -m 0 -E nodiscard -L bk-max {bild}", aufrufe)
        mp = self.fach("max")
        zeile = f"{bild} {mp} ext4 loop,noexec,nosuid,nodev,nofail,X-fstrim.notrim 0 0"
        self.assertEqual(self.fstab.read_text(encoding="utf-8"),
                         FSTAB_ANFANG + f"# Briefkasten-Fach von max (briefkasten-freund.sh)\n{zeile}\n")
        self.assertEqual(next(self.ablage.glob("sicherung/*/fstab")).read_text(encoding="utf-8"), FSTAB_ANFANG)
        self.assertIn(f"mount -t ext4 -o loop,noexec,nosuid,nodev {bild} {mp}", aufrufe)
        for pfad in (mp.parent, mp):   # chroot: alles root, nicht schreibbar für ihn
            self.assertEqual((self.besitzer(pfad), self.modus(pfad)), ("root:root", 0o755))
        self.assertEqual((mp / ".clip-briefkasten").read_text(encoding="utf-8"), "bk-max\n")
        self.assertEqual((self.besitzer(mp / ".clip-briefkasten"), self.modus(mp / ".clip-briefkasten")),
                         ("root:root", 0o644))
        for o in ("videos", "replays", "sitzungen", "status"):
            self.assertEqual((self.besitzer(mp / o), self.modus(mp / o)), ("bk-max:briefkasten", 0o700), o)
        hoch = self.etc / "hochladen/bk-max"
        ab = self.etc / "abholen/bk-max"
        self.assertEqual(hoch.read_text(encoding="utf-8"), f"restrict {self.schluessel('max-pc')}\n")
        self.assertEqual(ab.read_text(encoding="utf-8"), f'from="{MINI_IP}",restrict {self.schluessel("max-mini")}\n')
        for pfad in (hoch, ab):
            self.assertEqual((self.besitzer(pfad), self.modus(pfad)), ("root:root", 0o644))
        self.assertIn("Alles in Ordnung.", r.stdout)               # briefkasten-pruefen.sh max am Ende

        # Zweiter Lauf mit derselben Zeile: nichts geändert
        vorher = self.zustand()
        self.neu()
        r = self.freund("max")
        self.assertEqual(self.aenderungen(), [])
        self.assertEqual(self.zustand(), vorher)
        self.assertIn("schon eingehängt", r.stdout)
        self.assertIn("fstab: schon da", r.stdout)

    def test_freund_zu_wenig_platz(self):
        self.einrichten()
        vorher = self.zustand()
        # 200 GB, 42 frei: das System behält 30 GB (15 %) -> 12 GB passen, 20 nicht
        for frei, satz in ((42, f"So viel passt:  bash {FREUND} max --groesse 12"),
                           (35, "auch für das kleinste Fach (8 GB) ist kein Platz")):
            with self.subTest(frei=frei):
                self.neu()
                r = self.freund("max", code=1, DF_FREI=str(frei * GB))
                self.assertIn(satz, r.stdout)
                self.assertEqual(self.aenderungen(), [])
                self.assertEqual(self.zustand(), vorher)
        r = self.freund("max", "--groesse", "12", DF_FREI=str(42 * GB))
        self.assertEqual((self.srv / "bilder/bk-max.img").read_text(encoding="utf-8").splitlines()[0],
                         f"groesse={12 * GB}")

    def test_freunde_getrennt_schluessel_namen(self):
        """Isolation und Autorisierung: Ein Schlüssel gehört genau einem Freund und einer Rolle; Namen nur nach dem
        Muster der Pipeline; ein Schlüssel ist nur ein Schlüssel (keine Optionen, kein zweiter Eintrag)."""
        self.einrichten()
        self.freund("max")
        vorher = self.zustand()
        eva = (self.schluessel("eva-pc"), self.schluessel("eva-mini"))
        max_pc, max_mini = self.schluessel("max-pc"), self.schluessel("max-mini")
        for pc, abholen, satz in ((max_pc, eva[1], "diesen PC-Schlüssel gibt es schon (hochladen/bk-max)"),
                                  (eva[0], max_mini, "diesen Mini-Schlüssel gibt es schon (abholen/bk-max)"),
                                  (eva[0], eva[0], "zwei verschiedene Schlüssel"),
                                  (f'command="/bin/sh" {eva[0]}', eva[1], "kein öffentlicher Ed25519-Schlüssel"),
                                  (eva[0] + "\n" + max_pc, eva[1], "kein öffentlicher Ed25519-Schlüssel"),
                                  ("ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQ test", eva[1],
                                   "kein öffentlicher Ed25519-Schlüssel")):
            with self.subTest(satz):
                self.neu()
                r = self.ok(self.lauf(FREUND, "eva", "--pc", pc, "--abholen", abholen, eingabe="j\n"), 1)
                self.assertIn(satz, r.stdout)
                self.assertEqual(self.aenderungen(), [])
                self.assertEqual(self.zustand(), vorher)
        for name in ("Max", "../eva", "e", "eva/../max", "-eva", "eva max"):
            with self.subTest(name=name):
                self.neu()
                r = self.ok(self.lauf(FREUND, name, "--pc", eva[0], "--abholen", eva[1], eingabe="j\n"), 2)
                self.assertEqual(self.aufrufe(), "")
        # Eine von Hand verbogene Mini-Adresse in briefkasten.conf schleust keine Optionen in die Schlüsselzeile
        conf = self.etc / "briefkasten.conf"
        echt = conf.read_text(encoding="utf-8")
        conf.write_text(echt.replace(f"MINI_IP={MINI_IP}", f'MINI_IP={MINI_IP}",command="/bin/sh'), encoding="utf-8")
        self.neu()
        r = self.ok(self.lauf(FREUND, "eva", "--pc", eva[0], "--abholen", eva[1], eingabe="j\n"), 1)
        self.assertIn("keine Tailnet-Adresse", r.stdout)
        self.assertEqual(self.aenderungen(), [])
        conf.write_text(echt, encoding="utf-8")
        self.assertEqual(self.zustand(), vorher)
        # eva mit eigenen Schlüsseln: eigenes Bild, eigener chroot, eigene Schlüsseldateien – max bleibt, wie er war
        max_vorher = {k: v for k, v in self.zustand().items() if "bk-max" in k}
        self.freund("eva")
        self.assertEqual({k: v for k, v in self.zustand().items() if "bk-max" in k}, max_vorher)
        self.assertNotIn(max_pc.split()[1], (self.etc / "hochladen/bk-eva").read_text(encoding="utf-8"))
        self.assertEqual(self.besitzer(self.fach("eva") / "videos"), "bk-eva:briefkasten")
        # Derselbe Schlüssel bei zwei Freunden (von Hand kopiert) ist ein Befund der Prüfung
        (self.etc / "hochladen/bk-eva").write_text(f"restrict {max_pc}\n", encoding="utf-8")
        r = self.ok(self.lauf(PRUEFEN), 1)
        self.assertIn("derselbe Schlüssel steht in hochladen/bk-eva und hochladen/bk-max", r.stdout)

    def test_wachsen_und_sperren(self):
        self.einrichten()
        self.freund("max")
        bild = self.srv / "bilder/bk-max.img"
        mp = self.fach("max")
        # nur wachsen: kleiner oder gleich ändert nichts
        vorher = self.zustand()
        self.neu()
        r = self.ok(self.lauf(FREUND, "max", "--groesse", "10", eingabe="j\n"))
        self.assertIn("Nur wachsen: Das Fach hat schon 20 GB", r.stdout)
        self.assertEqual(self.aenderungen(), [])
        self.assertEqual(self.zustand(), vorher)
        # gerade in Benutzung: umount scheitert -> nichts vergrößert, bleibt eingehängt
        self.neu()
        r = self.ok(self.lauf(FREUND, "max", "--groesse", "30", eingabe="j\nj\n", BELEGT="1"), 1)
        self.assertIn("gerade in Benutzung", r.stdout)
        self.assertNotIn("fallocate", self.aufrufe())
        self.assertIn(str(mp), (self.stub / "mounts").read_text(encoding="utf-8"))
        # Wachsen: aushängen, Bild größer, prüfen, Dateisystem größer, wieder einhängen – Marke und Ordner bleiben
        self.neu()
        r = self.ok(self.lauf(FREUND, "max", "--groesse", "30", eingabe="j\nj\n"))
        assertReihenfolge(self, self.aufrufe(), [f"umount {mp}", f"fallocate -l {30 * GB} {bild}",
                                                 f"e2fsck -f -p {bild}", f"resize2fs {bild}", f"mount -t ext4"])
        self.assertNotIn("mkfs", self.aufrufe())
        self.assertEqual(bild.read_text(encoding="utf-8"), f"groesse={30 * GB}\nfs=ext4\n")
        self.assertEqual((mp / ".clip-briefkasten").read_text(encoding="utf-8"), "bk-max\n")
        self.assertIn("Fertig: Fach von max (30 GB)", r.stdout)
        # Zu groß für den Platz: nennt, was passt
        r = self.ok(self.lauf(FREUND, "max", "--groesse", "500", eingabe="j\n"), 1)
        self.assertRegex(r.stdout, rf"So viel passt:  bash {re.escape(str(FREUND))} max --groesse \d+")
        # Sperren und wieder einschalten: nur der Schalter am Benutzer
        vorher = {k: v for k, v in self.zustand().items()}
        self.neu()
        self.ok(self.lauf(FREUND, "max", "--sperren", eingabe="j\n"))
        self.assertEqual(self.aenderungen(), ["usermod -L bk-max"])
        self.assertIn("bk-max:!*:", (self.stub / "shadow").read_text(encoding="utf-8"))
        r = self.ok(self.lauf(PRUEFEN, "max"))
        self.assertIn("gesperrt – wieder an: briefkasten-freund.sh max --entsperren", r.stdout)
        self.neu()
        r = self.ok(self.lauf(FREUND, "max", "--sperren"))
        self.assertIn("schon gesperrt", r.stdout)
        self.assertEqual(self.aenderungen(), [])
        self.ok(self.lauf(FREUND, "max", "--entsperren", eingabe="j\n"))
        self.assertIn("bk-max:*:", (self.stub / "shadow").read_text(encoding="utf-8"))
        self.assertEqual(self.zustand(), vorher)

    def test_pruefen_findet_ausgehaengt_und_loecher(self):
        self.einrichten()
        self.freund("max")
        self.assertIn("Alles in Ordnung.", self.ok(self.lauf(PRUEFEN)).stdout)
        (self.stub / "mounts").write_text("", encoding="utf-8")   # nach einem Neustart nicht eingehängt
        r = self.ok(self.lauf(PRUEFEN, "max", LOECHER="1"), 1)
        self.assertIn("nicht eingehängt – Hochladen scheitert (Permission denied)", r.stdout)
        self.assertIn("nicht mehr fest reserviert", r.stdout)
        # ein neuer Lauf von briefkasten-freund.sh hängt es wieder ein
        self.neu()
        self.freund("max")
        self.assertEqual(self.aenderungen()[-1:], [f"mount -t ext4 -o loop,noexec,nosuid,nodev "
                                                    f"{self.srv}/bilder/bk-max.img {self.fach('max')}"])


# --- Echter sshd ------------------------------------------------------------------------------------------------------

# lo hochfahren und Adressen dazu – nur im eigenen Netz-Namensraum (SIOCSIFFLAGS/SIOCSIFADDR, ohne iproute2)
NETZ_PY = r'''
import fcntl, socket, struct, sys
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
def hoch(name):
    flags = struct.unpack("16sh", fcntl.ioctl(s, 0x8913, struct.pack("16sh", name.encode(), 0)))[1]
    fcntl.ioctl(s, 0x8914, struct.pack("16sh", name.encode(), flags | 1))
hoch("lo")
for i, ip in enumerate(sys.argv[1:], 1):
    for anfrage, wert in ((0x8916, ip), (0x891c, "255.255.255.255")):
        fcntl.ioctl(s, anfrage, struct.pack("16s", f"lo:{i}".encode())
                    + struct.pack("HH4s8x", socket.AF_INET, 0, socket.inet_aton(wert)))
    hoch(f"lo:{i}")
'''

# Läuft in `unshare -m -n`: tmpfs über /srv und /run, Kopien von passwd/group/shadow, eigene Adressen. Die Skripte
# richten den Briefkasten und zwei Fächer ein (Attrappen nur für das System drumherum; ein Fach ist ein tmpfs mit 8 MB
# statt eines Loop-Bilds), dann läuft der echte sshd mit der erzeugten Konfig. Jeder Versuch schreibt
# <W>/fall/<name>.rc und .out; was im Fach liegt, steht in <W>/fall/*.txt.
TREIBER = r'''set -euo pipefail
W="$1"; REPO="$2"; SSHD_ECHT="$3"; LIBS="$4"
PUBLIC=198.51.100.7; TS=100.100.1.1; MINI=100.100.1.2
python3 -I "$W/netz.py" "$TS" "$MINI" "$PUBLIC"
mount -t tmpfs -o mode=0755 tmpfs /srv
mount -t tmpfs -o mode=0755 tmpfs /run
mkdir -p /run/sshd /run/systemd/system /srv/units /srv/ablage
: > /srv/fstab
cp /etc/passwd "$W/passwd"; cp /etc/group "$W/group"; cp /etc/shadow "$W/shadow" 2>/dev/null || : > "$W/shadow"
grep -q '^sshd:' "$W/passwd" || { echo 'sshd:x:990:65534::/run/sshd:/usr/sbin/nologin' >> "$W/passwd"
                                  echo 'sshd:*:20000:0:99999:7:::' >> "$W/shadow"; }
mount --bind "$W/passwd" /etc/passwd; mount --bind "$W/group" /etc/group; mount --bind "$W/shadow" /etc/shadow
export PATH="$W/stub:$PATH" STUB="$W/stub" LIBS SSHD_ECHT TS_IP="$TS" BK_ETC=/srv/etc-briefkasten UNITS=/srv/units \
       ABLAGE=/srv/ablage FSTAB=/srv/fstab
bash "$REPO/deploy/vserver/briefkasten-einrichten.sh" --mini-ip "$MINI" > "$W/einrichten.log" 2>&1 <<< $'j\nj\nj\n'
for n in max eva; do
  for r in pc mini; do ssh-keygen -q -t ed25519 -N '' -C "$n-$r" -f "$W/$n-$r"; done
  bash "$REPO/deploy/vserver/briefkasten-freund.sh" "$n" --groesse 8 --pc "$(cat "$W/$n-pc.pub")" \
    --abholen "$(cat "$W/$n-mini.pub")" > "$W/freund-$n.log" 2>&1 <<< $'j\n'
done
mkdir -p "$W/fall"
LD_LIBRARY_PATH="$LIBS" "$SSHD_ECHT" -t -f "$BK_ETC/sshd_config" > "$W/fall/sshd-t.out" 2>&1 \
  && echo 0 > "$W/fall/sshd-t.rc" || echo $? > "$W/fall/sshd-t.rc"
LD_LIBRARY_PATH="$LIBS" "$SSHD_ECHT" -D -e -f "$BK_ETC/sshd_config" 2> "$W/sshd.log" &
SSHD=$!
trap 'kill $SSHD 2>/dev/null || true' EXIT
for _ in $(seq 50); do python3 -I -c "import socket; socket.create_connection(('$PUBLIC', 2222), 1)" 2>/dev/null && break
  sleep 0.1; done
H="$(cut -d' ' -f1,2 "$BK_ETC/hostkey_ed25519.pub")"
printf '[%s]:2222 %s\n' "$PUBLIC" "$H" "$TS" "$H" > "$W/known_hosts"
# fall <name> <schlüssel> <benutzer> <adresse> <quelle> <batch>
fall() {
  printf '%s\n' "$6" > "$W/fall/$1.batch"
  if sftp -q -b "$W/fall/$1.batch" -P 2222 -i "$W/$2" -F /dev/null -o IdentitiesOnly=yes -o BatchMode=yes \
       -o UserKnownHostsFile="$W/known_hosts" -o GlobalKnownHostsFile=/dev/null -o StrictHostKeyChecking=yes \
       -o BindAddress="$5" -o ConnectTimeout=10 "$3@$4" > "$W/fall/$1.out" 2>&1; then echo 0 > "$W/fall/$1.rc"
  else echo $? > "$W/fall/$1.rc"; fi
}
cd "$W"
N="Fortnite 2026.10.08 - 20.15.33.02.DVR.mp4"
head -c 300000 /dev/urandom > "$N"; head -c 100000 "$N" > halb.mp4
head -c 10000000 /dev/urandom > gross.mp4
echo '{"name": "x"}' > liefer.json
F=/srv/briefkasten/fach
fall pc_hoch max-pc bk-max "$PUBLIC" "$PUBLIC" "put halb.mp4 \"videos/$N.teil\"
reput \"$N\" \"videos/$N.teil\"
rename \"videos/$N.teil\" \"videos/$N\"
put liefer.json \"videos/$N.lieferschein.teil\"
rename \"videos/$N.lieferschein.teil\" \"videos/$N.lieferschein\"
put liefer.json status/pc-status.json
ls -1 videos
df"
cmp -s "$N" "$F/bk-max/fach/videos/$N" && echo gleich > "$W/fall/hochgeladen.txt" || echo anders > "$W/fall/hochgeladen.txt"
fall pc_lesen max-pc bk-max "$PUBLIC" "$PUBLIC" "get \"videos/$N\" zurueck.mp4"
fall pc_loeschen max-pc bk-max "$PUBLIC" "$PUBLIC" "rm \"videos/$N\""
fall pc_mkdir max-pc bk-max "$PUBLIC" "$PUBLIC" "mkdir videos/neu"
fall pc_ueberschreiben max-pc bk-max "$PUBLIC" "$PUBLIC" "put liefer.json videos/b.teil
rename videos/b.teil \"videos/$N\""
fall pc_marke max-pc bk-max "$PUBLIC" "$PUBLIC" "put liefer.json .clip-briefkasten"
fall pc_marke_weg max-pc bk-max "$PUBLIC" "$PUBLIC" "rm .clip-briefkasten"
fall pc_wurzel max-pc bk-max "$PUBLIC" "$PUBLIC" "ls -1 -a /
ls -1 /fach"
fall pc_voll max-pc bk-max "$PUBLIC" "$PUBLIC" "put gross.mp4 videos/gross.mp4.teil"
fall pc_tailnet max-pc bk-max "$TS" "$MINI" "ls"
head -c 50000 "$N" > geholt.mp4
fall mini_lesen max-mini bk-max "$TS" "$MINI" "ls -1 -a /fach
ls -1 videos
reget \"videos/$N\" geholt.mp4
get status/pc-status.json status.json"
cmp -s "$N" geholt.mp4 && echo gleich > "$W/fall/abgeholt.txt" || echo anders > "$W/fall/abgeholt.txt"
fall mini_schreiben max-mini bk-max "$TS" "$MINI" "put liefer.json videos/x.mp4"
fall mini_loeschen max-mini bk-max "$TS" "$MINI" "rm \"videos/$N\""
fall mini_umbenennen max-mini bk-max "$TS" "$MINI" "rename \"videos/$N\" videos/y.mp4"
fall mini_oeffentlich max-mini bk-max "$PUBLIC" "$MINI" "ls"
fall mini_falsche_quelle max-mini bk-max "$TS" "$PUBLIC" "ls"
fall max_als_eva_pc max-pc bk-eva "$PUBLIC" "$PUBLIC" "ls"
fall max_als_eva_mini max-mini bk-eva "$TS" "$MINI" "ls"
fall eva_hoch eva-pc bk-eva "$PUBLIC" "$PUBLIC" "put liefer.json videos/e.teil"
fall max_zu_eva max-pc bk-max "$PUBLIC" "$PUBLIC" "ls ../bk-eva
ls /srv/briefkasten/fach/bk-eva"
ls -1 "$F/bk-eva/fach/videos" > "$W/fall/eva-videos.txt"
ls -1 "$F/bk-max/fach/videos" > "$W/fall/max-videos.txt"
bash "$REPO/deploy/vserver/briefkasten-freund.sh" eva --sperren > "$W/sperren.log" 2>&1 <<< $'j\n'
fall eva_gesperrt eva-pc bk-eva "$PUBLIC" "$PUBLIC" "ls"
bash "$REPO/deploy/vserver/briefkasten-freund.sh" eva --entsperren > "$W/entsperren.log" 2>&1 <<< $'j\n'
fall eva_entsperrt eva-pc bk-eva "$PUBLIC" "$PUBLIC" "ls"
bash "$REPO/deploy/vserver/briefkasten-pruefen.sh" > "$W/fall/pruefen.out" 2>&1 \
  && echo 0 > "$W/fall/pruefen.rc" || echo $? > "$W/fall/pruefen.rc"
umount "$F/bk-eva/fach"
fall eva_ausgehaengt eva-pc bk-eva "$PUBLIC" "$PUBLIC" "put liefer.json x.teil"
fall eva_ausgehaengt_ordner eva-pc bk-eva "$PUBLIC" "$PUBLIC" "put liefer.json videos/x.teil"
ls -1A "$F/bk-eva/fach" > "$W/fall/eva-ausgehaengt.txt"
'''


def _sshd_fuer_test() -> tuple[str, str] | None:
    """(sshd, LD_LIBRARY_PATH): BRIEFKASTEN_SSHD (z. B. aus einem ausgepackten Paket …/x/usr/sbin/sshd) oder ein
    installierter sshd."""
    pfad = os.environ.get("BRIEFKASTEN_SSHD") or shutil.which("sshd") or "/usr/sbin/sshd"
    if not os.path.isfile(pfad) or not os.access(pfad, os.X_OK):
        return None
    wurzel = Path(pfad).resolve().parents[2]   # …/x/usr/sbin/sshd -> …/x
    libs = ":".join(str(wurzel / d) for d in ("usr/lib/x86_64-linux-gnu", "lib/x86_64-linux-gnu")
                    if (wurzel / d).is_dir()) if os.environ.get("BRIEFKASTEN_SSHD") else ""
    return pfad, libs


def _echter_sshd_moeglich() -> str | None:
    if sys.platform != "linux" or os.geteuid() != 0:
        return "nur als root unter Linux"
    for programm in ("unshare", "sftp", "ssh-keygen", "mkfs.ext4", "blkid", "fallocate", "timeout"):
        if not shutil.which(programm):
            return f"{programm} fehlt"
    if _sshd_fuer_test() is None:
        return "kein sshd (BRIEFKASTEN_SSHD setzen)"
    r = subprocess.run(["unshare", "-m", "-n", "--propagation", "private", "true"], capture_output=True, timeout=30)
    return None if r.returncode == 0 else "keine Mount- und Netz-Namensräume"


class EchterSshd(unittest.TestCase):
    """Die Skripte und der echte sshd in einem privaten Mount- und Netz-Namensraum – am System ändert sich nichts."""

    @classmethod
    def setUpClass(cls):
        if grund := _echter_sshd_moeglich():
            raise unittest.SkipTest(grund)
        cls._tmp = tempfile.TemporaryDirectory(prefix="clip-briefkasten-")
        w = cls.w = Path(cls._tmp.name)
        (w / "netz.py").write_text(NETZ_PY, encoding="utf-8")
        (w / "treiber.sh").write_text(TREIBER, encoding="utf-8")
        stub = w / "stub"
        stub.mkdir()
        sshd, libs = _sshd_fuer_test()
        echt = {p: shutil.which(p) for p in ("mount", "fallocate", "df")}
        attrappen = {name: ATTRAPPEN[name] for name in ("systemd-detect-virt", "losetup", "ip", "ss", "systemctl")}
        attrappen.update({
            "ufw": 'echo "Status: inactive"',
            "sshd": f'LD_LIBRARY_PATH="$LIBS" exec {shlex.quote(sshd)} "$@"',
            # Fach = tmpfs mit 8 MB statt Loop-Bild (Loop-Geräte gehören dem ganzen System)
            "mount": f'exec {echt["mount"]} -t tmpfs -o size=8m,mode=0755,noexec,nosuid,nodev tmpfs "${{*: -1}}"',
            "fallocate": f'exec {echt["fallocate"]} -l 48M "${{*: -1}}"',
            "findmnt": r'''o="$(awk -v p="${*: -1}" '$2 == p {o = $4} END {print o}' /proc/self/mounts)"; [ -n "$o" ] || exit 1
case "$3" in FSTYPE) echo ext4 ;; *) echo "ext4 $o" ;; esac''',
            # die Fächer echt messen, das System drumherum wie ein großer vServer
            "df": f'case "${{*: -1}}" in */fach) exec {echt["df"]} "$@" ;; esac\n'
                  'echo "1B-blocks Used Avail"; echo "200000000000 50000000000 150000000000"',
            "groupadd": 'echo "${*: -1}:x:2000:" >> /etc/group',
            "useradd": r'''n="${*: -1}"; pw="!"; while [ $# -gt 1 ]; do [ "$1" = --password ] && pw="$2"; shift; done
echo "$n:x:$(( 2001 + $(grep -c '^bk-' /etc/passwd || true) )):2000::/nonexistent:/usr/sbin/nologin" >> /etc/passwd
echo "$n:$pw:20000:0:99999:7:::" >> /etc/shadow''',
            "usermod": r'''n="${*: -1}"; case "$1" in -L) a="s/^$n:/$n:!/" ;; -U) a="s/^$n:!/$n:/" ;; esac
sed "$a" /etc/shadow > "$STUB/shadow.neu"; cat "$STUB/shadow.neu" > /etc/shadow''',
        })
        for name, inhalt in attrappen.items():
            datei = stub / name
            datei.write_text(f'#!/bin/bash\necho "{name} $*" >> "$STUB/aufrufe"\n{inhalt}\n', encoding="utf-8")
            datei.chmod(0o755)
        cls.lauf = subprocess.run(["timeout", "300", "unshare", "-m", "-n", "--propagation", "private", "bash",
                                   str(w / "treiber.sh"), str(w), str(PROJEKT), sshd, libs],
                                  capture_output=True, text=True, timeout=330)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def fall(self, name: str) -> tuple[int, str]:
        rc = self.w / "fall" / f"{name}.rc"
        self.assertTrue(rc.exists(), f"{name} lief nicht: {self.lauf.stdout}{self.lauf.stderr}"
                                     f"{self.log('einrichten.log')}{self.log('freund-max.log')}")
        return int(rc.read_text().strip()), (self.w / "fall" / f"{name}.out").read_text(errors="replace")

    def log(self, name: str) -> str:
        pfad = self.w / name
        return pfad.read_text(errors="replace") if pfad.exists() else ""

    def assertVerweigert(self, name: str, meldung: str) -> None:
        rc, aus = self.fall(name)
        self.assertNotEqual(rc, 0, aus)
        self.assertIn(meldung, aus, name)

    def test_konfig_und_pruefung_gruen(self):
        self.assertEqual(self.lauf.returncode, 0, self.lauf.stdout + self.lauf.stderr + self.log("sshd.log"))
        self.assertEqual(self.fall("sshd-t"), (0, ""))
        rc, aus = self.fall("pruefen")
        self.assertEqual(rc, 0, aus)
        self.assertIn("Alles in Ordnung.", aus)

    def test_pc_schreibt_liest_nicht_loescht_nicht(self):
        rc, aus = self.fall("pc_hoch")
        self.assertEqual(rc, 0, aus)
        self.assertEqual((self.w / "fall/hochgeladen.txt").read_text().strip(), "gleich")   # put + reput + rename
        self.assertIn("videos/Fortnite 2026.10.08 - 20.15.33.02.DVR.mp4\n", aus)
        self.assertVerweigert("pc_lesen", "Permission denied")
        self.assertVerweigert("pc_loeschen", "Permission denied")
        self.assertVerweigert("pc_mkdir", "Permission denied")
        self.assertVerweigert("pc_ueberschreiben", "Failure")          # rename auf ein fertiges Ziel
        self.assertVerweigert("pc_marke", "Permission denied")
        self.assertVerweigert("pc_marke_weg", "Permission denied")
        self.assertVerweigert("pc_voll", "Failure")                     # Fach voll – nur sein Fach
        self.assertVerweigert("pc_tailnet", "Connection closed")       # PC-Schlüssel gilt nur öffentlich
        rc, aus = self.fall("pc_wurzel")                                 # im chroot: nur das eigene Fach
        self.assertEqual(rc, 0, aus)
        self.assertEqual({z.removeprefix("/") for z in aus.splitlines() if not z.startswith("sftp>")},
                         {".", "..", "fach", "fach/replays", "fach/sitzungen", "fach/status", "fach/videos"})

    def test_mini_liest_nur_ueber_das_tailnet(self):
        rc, aus = self.fall("mini_lesen")
        self.assertEqual(rc, 0, aus)
        self.assertIn(".clip-briefkasten\n", aus)
        self.assertEqual((self.w / "fall/abgeholt.txt").read_text().strip(), "gleich")
        self.assertVerweigert("mini_schreiben", "Permission denied")
        self.assertVerweigert("mini_loeschen", "Permission denied")
        self.assertVerweigert("mini_umbenennen", "Permission denied")
        self.assertVerweigert("mini_oeffentlich", "Connection closed")      # nur auf der Tailnet-Adresse
        self.assertVerweigert("mini_falsche_quelle", "Connection closed")   # from=<Mini-IP>

    def test_freunde_getrennt_ausgehaengt_gesperrt(self):
        self.assertVerweigert("max_als_eva_pc", "Connection closed")
        self.assertVerweigert("max_als_eva_mini", "Connection closed")
        rc, aus = self.fall("max_zu_eva")
        self.assertNotEqual(rc, 0, aus)
        self.assertNotIn("e.teil", aus)
        self.assertEqual((self.w / "fall/eva-videos.txt").read_text().split(), ["e.teil"])
        self.assertNotIn("e.teil", (self.w / "fall/max-videos.txt").read_text())
        self.assertVerweigert("eva_gesperrt", "Connection closed")
        self.assertEqual(self.fall("eva_entsperrt")[0], 0)
        self.assertVerweigert("eva_ausgehaengt", "Permission denied")       # nie auf die Systemplatte
        self.assertNotEqual(self.fall("eva_ausgehaengt_ordner")[0], 0)
        self.assertEqual((self.w / "fall/eva-ausgehaengt.txt").read_text(), "")


if __name__ == "__main__":
    unittest.main()
