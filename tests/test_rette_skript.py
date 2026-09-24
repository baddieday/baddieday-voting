"""deploy/pve-mini/rette-clips-start.sh gegen einen nachgebauten pve-mini (Stubs für pct, mount, systemctl).

Braucht root (pct exec läuft wirklich, in einem eigenen Mount-Namespace mit Attrappen für /srv und /opt des CT).
"""

import os
import pwd
import shutil
import socket
import stat
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

PROJEKT = Path(__file__).resolve().parents[1]
SKRIPT = PROJEKT / "deploy/pve-mini/rette-clips-start.sh"
TOKEN = "707755969:AAtesttesttesttesttesttesttesttest_-ab"  # Test-Wert, kein echter Token

FSTAB = """# <file system> <mount point> <type> <options> <dump> <pass>
/dev/pve/root / ext4 errors=remount-ro 0 1
pve-big:/tank/clips /mnt/clips nfs soft,timeo=50,retrans=3,bg,nofail,_netdev 0 0
"""
KONF = """arch: amd64
hostname: clips
mp0: /mnt/clips,mp=/srv/clips
rootfs: local-lvm:vm-102-disk-0,size=16G
unprivileged: 1
lxc.cgroup2.devices.allow: c 10:200 rwm
lxc.mount.entry: /dev/net/tun dev/net/tun none bind,create=file

[vorher]
mp0: /mnt/clips,mp=/srv/clips
snaptime: 1
"""

STUBS = {
    # pct: status aus Datei, set --delete/--onboot bearbeitet die Konfig wie Proxmox (nur aktiver Abschnitt)
    "pct": r'''#!/bin/bash
echo "pct $*" >> "$STUB/aufrufe"
case "$1" in
  status) echo "status: $(cat "$STUB/status")" ;;
  start) echo running > "$STUB/status" ;;
  set)
    if [ "$3" = --delete ]; then
      awk -v k="$4:" '/^\[/ {s=1} !(s==0 && index($0, k) == 1) {print}' "$KONF" > "$STUB/k" && cat "$STUB/k" > "$KONF"
    elif [ "$3" = --onboot ]; then
      awk -v v="$4" '/^\[/ && !d {print "onboot: " v; d=1} !(index($0, "onboot:") == 1 && !s) {print} /^\[/ {s=1} END {if (!d) print "onboot: " v}' "$KONF" > "$STUB/k" && cat "$STUB/k" > "$KONF"
    fi ;;
  exec)
    shift 4  # exec 102 -- sh
    [ "$1" = -c ] || exit 9
    exec unshare -m bash -c '/usr/bin/mount --bind "$CTROOT/srv" /srv && /usr/bin/mount --bind "$CTROOT/opt" /opt && exec sh -c "$1"' _ "$2" ;;
esac
''',
    "findmnt": r'''#!/bin/bash
ziel="${@: -1}"
grep -qxF "$ziel" "$STUB/eingehaengt" 2>/dev/null || exit 1
[[ "$*" == *PROPAGATION* ]] && echo "$ziel shared"; exit 0
''',
    "mount": '#!/bin/bash\necho "mount $*" >> "$STUB/aufrufe"; echo "${@: -1}" >> "$STUB/eingehaengt"\n',
    "umount": '#!/bin/bash\necho "umount $*" >> "$STUB/aufrufe"; grep -vxF "${@: -1}" "$STUB/eingehaengt" > "$STUB/e" ; mv "$STUB/e" "$STUB/eingehaengt"\n',
    "systemctl": '#!/bin/bash\necho "systemctl $*" >> "$STUB/aufrufe"\n',
    "logger": '#!/bin/bash\necho "logger $*" >> "$STUB/aufrufe"\n',
}


@unittest.skipUnless(os.geteuid() == 0 and shutil.which("unshare"), "braucht root und unshare")
class RetteSkript(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        t = self.t = Path(self._tmp.name)
        for d in ("stub", "etc", "sbin", "units", "ct/srv/clips", "ct/srv/big", "ct/opt/clip-pipeline"):
            (t / d).mkdir(parents=True)
        (t / "etc/fstab").write_text(FSTAB)
        (t / "etc/102.conf").write_text(KONF)
        (t / "stub/status").write_text("stopped\n")
        (t / "stub/eingehaengt").write_text("/mnt/clips\n")  # der tote NFS-Mount
        for name, inhalt in STUBS.items():
            (t / "stub" / name).write_text(inhalt)
            (t / "stub" / name).chmod(0o755)
        try:
            pwd.getpwnam("pipeline")
        except KeyError:
            subprocess.run(["useradd", "-M", "-s", "/usr/sbin/nologin", "pipeline"], check=True)
        env = t / "ct/opt/clip-pipeline/.env"
        env.write_text("TELEGRAM_BOT_TOKEN=alt\n")
        shutil.chown(env, "pipeline", "pipeline")
        env.chmod(0o600)

    def tearDown(self):
        self._tmp.cleanup()
        self.assertFalse(Path("/srv/clips").is_symlink(), "Test hat das echte /srv verändert!")

    def lauf(self, eingabe=""):
        t = self.t
        umgebung = {**os.environ, "PATH": f"{t}/stub:{os.environ['PATH']}", "STUB": str(t / "stub"),
                    "CTROOT": str(t / "ct"), "FSTAB": str(t / "etc/fstab"), "KONF": str(t / "etc/102.conf"),
                    "SBIN": str(t / "sbin"), "UNITS": str(t / "units"), "SICH": str(t / "sicherung"),
                    "BIG": str(t / "mnt/big"), "ALT": "/mnt/clips"}
        return subprocess.run(["bash", str(SKRIPT)], input=eingabe, capture_output=True, text=True, env=umgebung,
                              timeout=60)

    def test_rettung_und_wiederholung(self):
        t = self.t
        r = self.lauf(TOKEN + "\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        big = t / "mnt/big"
        fstab = (t / "etc/fstab").read_text()
        self.assertIn(f"pve-big:/tank/clips {big}/clips nfs soft,timeo=50,retrans=3,bg,nofail,_netdev 0 0", fstab)
        self.assertIn(f"{big} {big} none bind,shared 0 0", fstab)
        self.assertIn("/dev/pve/root / ext4 errors=remount-ro 0 1", fstab)  # Rest unverändert

        konf = (t / "etc/102.conf").read_text()
        aktiv, snapshot = konf.split("[vorher]")
        self.assertNotIn("mp0:", aktiv)
        self.assertIn("mp0: /mnt/clips,mp=/srv/clips", snapshot)  # Snapshot bleibt, wie er war
        self.assertIn(f"lxc.mount.entry: {big} srv/big none rbind,rslave,create=dir 0 0", aktiv)
        self.assertIn("onboot: 1", aktiv)

        aufrufe = (t / "stub/aufrufe").read_text()
        self.assertIn("umount -l /mnt/clips", aufrufe)          # toter Mount nur gelöst
        self.assertIn("systemctl enable --now clips-nfs-einhaengen.timer", aufrufe)
        self.assertIn("pct start 102", aufrufe)
        self.assertNotIn(TOKEN, aufrufe + r.stdout + r.stderr)  # Token nie in Befehlszeilen oder Ausgabe

        link = t / "ct/srv/clips"
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), "/srv/big/clips")
        env = t / "ct/opt/clip-pipeline/.env"
        self.assertEqual(env.read_text(), f"TELEGRAM_BOT_TOKEN=alt\nLEARN_BOT_TOKEN={TOKEN}\n")
        st = env.stat()
        self.assertEqual((pwd.getpwuid(st.st_uid).pw_name, stat.S_IMODE(st.st_mode)), ("pipeline", 0o600))
        self.assertTrue((t / "sicherung/fstab").read_text() == FSTAB)
        self.assertTrue((t / "sicherung/102.conf").read_text() == KONF)

        # Zweiter Lauf (z. B. nach Neustart des Hosts): nichts doppelt, Token ersetzt statt verdoppelt
        (t / "stub/status").write_text("stopped\n")
        r = self.lauf(TOKEN + "\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((t / "etc/fstab").read_text().count("bind,shared"), 1)
        self.assertEqual((t / "etc/102.conf").read_text().count("rbind,rslave"), 1)
        self.assertEqual(env.read_text().count("LEARN_BOT_TOKEN="), 1)
        self.assertIn("Link steht schon", r.stdout)

    def test_ohne_token_und_mit_falschem_token(self):
        r = self.lauf("\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("LEARN_BOT_TOKEN", (self.t / "ct/opt/clip-pipeline/.env").read_text())
        (self.t / "stub/status").write_text("stopped\n")
        r = self.lauf("das ist kein token\n")
        self.assertIn("nicht wie ein Bot-Token", r.stdout)

    def test_laufender_ct_wird_nicht_angefasst(self):
        (self.t / "stub/status").write_text("running\n")
        r = self.lauf()
        self.assertEqual(r.returncode, 1)
        self.assertIn("pct shutdown 102", r.stdout)
        self.assertEqual((self.t / "etc/fstab").read_text(), FSTAB)
        self.assertEqual((self.t / "etc/102.conf").read_text(), KONF)

    def test_nicht_leeres_srv_clips_bleibt(self):
        (self.t / "ct/srv/clips/wichtig.mp4").write_text("x")
        r = self.lauf("\n")
        self.assertIn("nicht leer – nichts verändert", r.stdout)
        self.assertTrue((self.t / "ct/srv/clips/wichtig.mp4").is_file())

    def test_nachzieher_haengt_nur_ein_wenn_pve_big_antwortet(self):
        self.lauf("\n")
        nachzieher = self.t / "sbin/clips-nfs-einhaengen"
        fstab = self.t / "etc/nachzieher-fstab"
        umgebung = {**os.environ, "PATH": f"{self.t}/stub:{os.environ['PATH']}", "STUB": str(self.t / "stub"),
                    "FSTAB": str(fstab), "Z": "/mnt/big/clips"}
        (self.t / "stub/aufrufe").write_text("")
        fstab.write_text("127.0.0.1:/tank/clips /mnt/big/clips nfs soft 0 0\n")
        subprocess.run([str(nachzieher)], env=umgebung, check=True, timeout=30)
        self.assertNotIn("mount", (self.t / "stub/aufrufe").read_text())  # Port 2049 zu -> kein Mount-Versuch
        server = socket.socket()
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 2049))
        server.listen(1)
        threading.Thread(target=lambda: server.accept(), daemon=True).start()
        try:
            subprocess.run([str(nachzieher)], env=umgebung, check=True, timeout=30)
        finally:
            server.close()
        self.assertIn("mount /mnt/big/clips", (self.t / "stub/aufrufe").read_text())


if __name__ == "__main__":
    unittest.main()
