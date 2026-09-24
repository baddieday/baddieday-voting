"""deploy/pve-mini/regie-starten.sh gegen Stubs: Wecken (echtes WoL-Paket), Warten, Reihenfolge der Schritte."""

import os
import socket
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

SKRIPT = Path(__file__).resolve().parents[1] / "deploy/pve-mini/regie-starten.sh"


@unittest.skipUnless(os.geteuid() == 0, "braucht root (UDP-Port 9)")
class RegieStarten(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        t = self.t = Path(self._tmp.name)
        (t / "stub").mkdir()
        stubs = {
            # pct: status running, exec protokolliert; die MAC kommt aus "lokal.toml"
            "pct": '''#!/bin/bash
echo "pct $*" >> "$STUB/aufrufe"
case "$1" in
  status) echo "status: running" ;;
  exec) shift 3; case "$1" in awk) echo "40:61:86:2e:9a:ff" ;; test) exit 1 ;; esac ;;
esac''',
            # findmnt: erst nach 3 Abfragen "eingehängt" (pve-big fährt hoch)
            "findmnt": '''#!/bin/bash
n=$(( $(cat "$STUB/n" 2>/dev/null || echo 0) + 1 )); echo $n > "$STUB/n"; [ $n -gt 3 ]''',
            "sleep": "#!/bin/bash\ntrue",
        }
        for name, inhalt in stubs.items():
            (t / "stub" / name).write_text(inhalt + "\n")
            (t / "stub" / name).chmod(0o755)

    def tearDown(self):
        self._tmp.cleanup()

    def test_ablauf_mit_wecken(self):
        empfang = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        empfang.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        empfang.bind(("127.0.0.1", 9))
        empfang.settimeout(10)
        paket = {}
        threading.Thread(target=lambda: paket.update(daten=empfang.recv(200)), daemon=True).start()
        env = {**os.environ, "PATH": f"{self.t}/stub:{os.environ['PATH']}", "STUB": str(self.t / "stub"),
               "WOL_ZIEL": "127.0.0.1", "WARTE_S": "0"}
        r = subprocess.run(["bash", str(SKRIPT)], capture_output=True, text=True, env=env, timeout=60)
        empfang.close()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(paket["daten"], b"\xff" * 6 + bytes.fromhex("4061862e9aff") * 16)  # echtes WoL-Paket
        aufrufe = (self.t / "stub/aufrufe").read_text()
        reihenfolge = ["fetch -q origin +refs/heads/sprint-regisseur:refs/remotes/origin/sprint-regisseur",
                       "worktree add", "pip install -q -e '.[whisper]'", "musik ncs", "stimmung --max 40",
                       "clip-lernbot", "entwurf-neu --format short"]
        positionen = [aufrufe.index(teil) for teil in reihenfolge]
        self.assertEqual(positionen, sorted(positionen))
        self.assertIn("runuser -l pipeline -c", aufrufe)
        self.assertIn("GIT_TERMINAL_PROMPT=0", aufrufe)
        self.assertNotIn("/opt/clip-pipeline/.venv", aufrufe)  # Produktion wird nie benutzt/verändert
        self.assertIn("/Lern-Bot /start", r.stdout.replace("deinem Lern-Bot ", "/Lern-Bot "))

    def test_ohne_mac_kein_raten(self):
        (self.t / "stub/pct").write_text('#!/bin/bash\necho "pct $*" >> "$STUB/aufrufe"\n'
                                         '[ "$1" = status ] && echo "status: running"; exit 0\n')
        (self.t / "stub/findmnt").write_text("#!/bin/bash\nexit 1\n")
        env = {**os.environ, "PATH": f"{self.t}/stub:{os.environ['PATH']}", "STUB": str(self.t / "stub")}
        r = subprocess.run(["bash", str(SKRIPT)], capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(r.returncode, 1)
        self.assertIn("Keine MAC", r.stdout)
