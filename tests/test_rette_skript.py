"""deploy/pve-mini/rette-clips-start.sh gegen einen nachgebauten pve-mini (Stubs für pct, mount, systemctl).

Der Container ist ein Ordner (ROOTFS), pct mount/pull/push arbeiten darauf. Braucht root (chown pipeline).
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
    "pct": r"""#!/bin/bash
echo "pct $*" >> "$STUB/aufrufe"
case "$1" in
  status) echo "status: $(cat "$STUB/status")" ;;
  start) echo running > "$STUB/status" ;;
  mount) [ "$(cat "$STUB/status")" = stopped ] || exit 2; echo "mounted CT $2" ;;
  unmount) : ;;
  set)
    if [ "$3" = --delete ]; then
      awk -v k="$4:" '/^\[/ {s=1} !(s==0 && index($0, k) == 1) {print}' "$KONF" > "$STUB/k" && cat "$STUB/k" > "$KONF"
    elif [ "$3" = --onboot ]; then
      awk -v v="$4" '/^\[/ && !d {print "onboot: " v; d=1} !(index($0, "onboot:") == 1 && !s) {print} /^\[/ {s=1} END {if (!d) print "onboot: " v}' "$KONF" > "$STUB/k" && cat "$STUB/k" > "$KONF"
    fi ;;
  pull) cp "$ROOTFS$3" "$4" ;;
  push)
    [ "$(cat "$STUB/status")" = running ] || exit 2
    cp "$3" "$ROOTFS$4"; shift 4
    while [ $# -gt 0 ]; do case "$1" in --user) chown "$2" "$ROOTFS/opt/clip-pipeline/.env";; --group) chgrp "$2" "$ROOTFS/opt/clip-pipeline/.env";; --perms) chmod "$2" "$ROOTFS/opt/clip-pipeline/.env";; esac; shift 2; done ;;
esac
""",
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


@unittest.skipUnless(os.geteuid() == 0, "braucht root")
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
        self.assertFalse(Path("/var/lib/lxc/102").exists(), "Test hat einen echten CT-Pfad benutzt!")

    def lauf(self, eingabe=""):
        t = self.t
        umgebung = {**os.environ, "PATH": f"{t}/stub:{os.environ['PATH']}", "STUB": str(t / "stub"),
                    "CTROOT": str(t / "ct"), "FSTAB": str(t / "etc/fstab"), "KONF": str(t / "etc/102.conf"),
                    "SBIN": str(t / "sbin"), "UNITS": str(t / "units"), "SICH": str(t / "sicherung"),
                    "BIG": str(t / "mnt/big"), "ALT": "/mnt/clips", "ROOTFS": str(t / "ct"),
                    "ORIGINAL": str(t / "original")}
        return subprocess.run(["bash", str(SKRIPT)], input=eingabe, capture_output=True, text=True, env=umgebung,
                              timeout=60)

    def test_rettung_und_wiederholung(self):
        t = self.t
        r = self.lauf(TOKEN + "\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        big = t / "mnt/big"
        zeilen = (t / "etc/fstab").read_text().splitlines()
        bind = zeilen.index(f"{big} {big} none bind,shared 0 0")
        nfs = zeilen.index(f"pve-big:/tank/clips {big}/clips nfs soft,timeo=50,retrans=3,nofail,_netdev,"
                           "x-systemd.mount-timeout=20 0 0")  # bg raus
        self.assertLess(bind, nfs)                                    # Bind VOR dem NFS (mount -a)
        self.assertIn("/dev/pve/root / ext4 errors=remount-ro 0 1", zeilen)

        konf = (t / "etc/102.conf").read_text()
        aktiv, snapshot = konf.split("[vorher]")
        self.assertNotIn("mp0:", aktiv)
        self.assertIn("mp0: /mnt/clips,mp=/srv/clips", snapshot)  # Snapshot bleibt, wie er war
        self.assertIn(f"lxc.mount.entry: {big} srv/big none rbind,rslave,create=dir 0 0", aktiv)
        self.assertIn("onboot: 1", aktiv)

        aufrufe = (t / "stub/aufrufe").read_text()
        self.assertIn("umount -l /mnt/clips", aufrufe)          # toter Mount nur gelöst
        self.assertIn("systemctl enable --now clips-nfs-einhaengen.timer", aufrufe)
        self.assertLess(aufrufe.index("pct mount 102"), aufrufe.index("pct start 102"))  # Link vor dem Start
        self.assertNotIn(TOKEN, aufrufe + r.stdout + r.stderr)  # Token nie in Befehlszeilen oder Ausgabe

        link = t / "ct/srv/clips"
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), "/srv/big/clips")
        env = t / "ct/opt/clip-pipeline/.env"
        self.assertEqual(env.read_text(), f"TELEGRAM_BOT_TOKEN=alt\nLEARN_BOT_TOKEN={TOKEN}\n")
        st = env.stat()
        self.assertEqual((pwd.getpwuid(st.st_uid).pw_name, stat.S_IMODE(st.st_mode)), ("pipeline", 0o600))
        self.assertEqual((t / "original/fstab").read_text(), FSTAB)
        self.assertEqual((t / "original/102.conf").read_text(), KONF)
        self.assertTrue(os.access(t / "original/zurueck.sh", os.X_OK))
        self.assertEqual(subprocess.run(["bash", "-n", str(t / "original/zurueck.sh")]).returncode, 0)
        self.assertEqual(list(Path("/run").glob("clips-token.*")), [])  # Token-Zwischendatei weg

        # Zweiter Lauf bei LAUFENDEM, schon umgebautem CT: kein Stopp nötig, nichts doppelt, Original bleibt
        r = self.lauf(TOKEN + "\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Link und Start übersprungen", r.stdout)
        fstab2 = (t / "etc/fstab").read_text()
        self.assertEqual(fstab2.count("bind,shared"), 1)
        self.assertEqual(fstab2.count("x-systemd.mount-timeout=20"), 1)
        self.assertEqual((t / "etc/102.conf").read_text().count("rbind,rslave"), 1)
        self.assertEqual(env.read_text().count("LEARN_BOT_TOKEN="), 1)
        self.assertEqual((t / "original/fstab").read_text(), FSTAB)  # Rückweg zeigt weiter auf den Urzustand

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

    def test_unbekannter_aufbau_aendert_nichts(self):
        (self.t / "etc/fstab").write_text("/dev/pve/root / ext4 defaults 0 1\n")
        r = self.lauf()
        self.assertEqual(r.returncode, 1)
        self.assertIn("unbekannter Aufbau", r.stdout)
        self.assertEqual((self.t / "etc/102.conf").read_text(), KONF)
        self.assertFalse((self.t / "original").exists())

    def test_nicht_leeres_srv_clips_bleibt(self):
        (self.t / "ct/srv/clips/wichtig.mp4").write_text("x")
        r = self.lauf("\n")
        self.assertIn("nicht leer – nichts verändert", r.stdout)
        self.assertTrue((self.t / "ct/srv/clips/wichtig.mp4").is_file())

    def test_nachzieher_haengt_ein_und_loest_tote_mounts(self):
        self.lauf("\n")
        nachzieher = self.t / "sbin/clips-nfs-einhaengen"
        fstab = self.t / "etc/nachzieher-fstab"
        umgebung = {**os.environ, "PATH": f"{self.t}/stub:{os.environ['PATH']}", "STUB": str(self.t / "stub"),
                    "FSTAB": str(fstab), "Z": "/mnt/big/clips", "ZAEHLER": str(self.t / "still")}
        aufrufe = self.t / "stub/aufrufe"
        aufrufe.write_text("")
        (self.t / "stub/eingehaengt").write_text("")
        fstab.write_text("127.0.0.1:/tank/clips /mnt/big/clips nfs soft 0 0\n")
        subprocess.run([str(nachzieher)], env=umgebung, check=True, timeout=30)
        self.assertNotIn("mount", aufrufe.read_text())  # Port 2049 zu -> kein Mount-Versuch
        server = socket.socket()
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 2049))
        server.listen(1)
        threading.Thread(target=lambda: server.accept()[0].close(), daemon=True).start()
        try:
            subprocess.run([str(nachzieher)], env=umgebung, check=True, timeout=30)
        finally:
            server.close()
        self.assertIn("mount /mnt/big/clips", aufrufe.read_text())
        # pve-big schläft wieder: nach 3 stillen Prüfungen (90 s) wird der tote Mount gelöst
        for _ in range(2):
            subprocess.run([str(nachzieher)], env=umgebung, check=True, timeout=30)
        self.assertNotIn("umount", aufrufe.read_text())
        subprocess.run([str(nachzieher)], env=umgebung, check=True, timeout=30)
        self.assertIn("umount -l /mnt/big/clips", aufrufe.read_text())


if __name__ == "__main__":
    unittest.main()
