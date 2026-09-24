"""deploy/big/clip-leerlauf: pve-big schaltet sich nach Leerlauf selbst aus – nur echte Zugriffe zählen."""

import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

SKRIPT = Path(__file__).resolve().parents[1] / "deploy/big/clip-leerlauf"
DATASET = "tank/clips"


def objset(name: str, writes=0, nread=0, nunlinks=0) -> str:
    return ("34 1 0x01 7 2160 5214840160 25648829591\nname                            type data\n"
            f"dataset_name                    7    {name}\nwrites                          4    {writes}\n"
            f"nwritten                        4    {writes * 4096}\nreads                           4    0\n"
            f"nread                           4    {nread}\nnunlinks                        4    {nunlinks}\n"
            "nunlinked                       4    0\n")


class Leerlauf(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        t = self.t = Path(self._tmp.name)
        for d in ("stub", "kstat/tank", "clients", "zustand", "clips/eingang", "clips/.aktiv"):
            (t / d).mkdir(parents=True)
        (t / "clips/.clip-speicher").touch()
        (t / "leasetime").write_text("90\n")
        self.zfs(writes=10, nread=1000)
        (t / "kstat/tank/objset-0x99").write_text(objset("tank/vm-100-disk-0", writes=5))  # fremdes Dataset
        (t / "nfsd").write_text("proc3 22 0 5 0 7 3 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0\n"
                                "proc4ops 72 " + " ".join(["0"] * 72) + "\n")
        self.uptime(60)
        self.konf(TROCKEN="1", LEERLAUF_MIN="20", MINDEST_WACH_MIN="10")
        # Stubs: standardmäßig "nichts los"
        leer = {"smbstatus": 'echo \'{"open_files": {}}\'', "qm": "echo 'VMID NAME STATUS'",
                "pct": "echo 'VMID Status Lock Name'", "who": "true", "pgrep": "exit 1", "fuser": "exit 1",
                "systemd-inhibit": "true", "zpool": "echo 'state: ONLINE'", "pvesh": "echo '[]'",
                "zfs": f"echo {DATASET}"}
        for name, rumpf in leer.items():
            self.stub(name, rumpf)

    def tearDown(self):
        self._tmp.cleanup()

    # --- Hilfen ---
    def stub(self, name, rumpf):
        p = self.t / "stub" / name
        p.write_text(f"#!/bin/bash\n{rumpf}\n")
        p.chmod(0o755)

    def zfs(self, **werte):
        (self.t / "kstat/tank/objset-0x36").write_text(objset(DATASET, **werte))

    def uptime(self, s):
        (self.t / "uptime").write_text(f"{s}.00 1.00\n")

    def konf(self, **werte):
        (self.t / "konf").write_text(f"SPEICHER={self.t}/clips\nDATASET={DATASET}\n"
                                     + "".join(f"{k}={v}\n" for k, v in werte.items()))

    def lauf(self):
        t = self.t
        env = {**os.environ, "PATH": f"{t}/stub:{os.environ['PATH']}", "CLIP_LL_KONF": str(t / "konf"),
               "CLIP_LL_UPTIME": str(t / "uptime"), "CLIP_LL_KSTAT": str(t / "kstat"),
               "CLIP_LL_NFSD_STAT": str(t / "nfsd"), "CLIP_LL_NFSD_CLIENTS": str(t / "clients"),
               "CLIP_LL_LEASETIME": str(t / "leasetime"), "CLIP_LL_ZUSTAND": str(t / "zustand"),
               "CLIP_LL_HALTEN": str(t / "halten"), "CLIP_LL_AUS_DATEI": str(t / "automatik-aus"),
               "CLIP_LL_AUS_BEFEHL": f"touch {t}/AUSGESCHALTET", "CLIP_LL_WARTE": "0"}
        r = subprocess.run(["python3", str(SKRIPT)], capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.status = json.loads((t / "zustand/status.json").read_text()) if (t / "zustand/status.json").exists() else {}
        return r.stdout

    @property
    def aus(self):
        return (self.t / "AUSGESCHALTET").exists()

    def ruhig_bis_aus(self):
        """Mehrere Minuten ohne Zugriff durchspielen, bis 25 min Uptime."""
        ausgabe = ""
        for s in (60, 300, 600, 900, 1200, 1500):
            self.uptime(s)
            ausgabe += self.lauf()
        return ausgabe

    # --- Fälle ---
    def test_nichts_los_aus_nach_leerlauf_nur_wenn_scharf(self):
        ausgabe = self.ruhig_bis_aus()
        self.assertEqual(ausgabe.count("würde ausschalten"), 1)  # TROCKEN=1: nur protokollieren, einmal
        self.assertFalse(self.aus)
        self.konf(TROCKEN="0", LEERLAUF_MIN="20", MINDEST_WACH_MIN="10")
        self.uptime(1560)
        self.assertIn("schalte aus", self.lauf())
        self.assertTrue(self.aus)

    def test_mindest_wachzeit_nach_dem_hochfahren(self):
        self.konf(TROCKEN="0", LEERLAUF_MIN="1", MINDEST_WACH_MIN="10")
        self.uptime(300)
        self.lauf()
        self.assertFalse(self.aus)
        self.uptime(660)
        self.lauf()
        self.assertTrue(self.aus)  # Uhr lief ab dem Hochfahren, nicht erst ab Minute 10

    def test_schreiben_setzt_uhr_zurueck_lesen_erst_ab_schwelle(self):
        self.uptime(600)
        self.lauf()
        self.zfs(writes=11, nread=1000)                 # eine Datei geschrieben
        self.uptime(1500)
        self.lauf()
        self.assertEqual(self.status["ruhig_seit_s"], 0)
        self.zfs(writes=11, nread=1000 + 4096)          # nur ein paar KiB gelesen (Metadaten)
        self.uptime(1560)
        self.lauf()
        self.assertEqual(self.status["ruhig_seit_s"], 60)
        self.zfs(writes=11, nread=1000 + 4096 + 50 * 1024 * 1024)  # Video gelesen
        self.uptime(1620)
        self.lauf()
        self.assertEqual(self.status["ruhig_seit_s"], 0)

    def test_fremdes_dataset_und_stat_zaehlen_nicht(self):
        self.uptime(600)
        self.lauf()
        (self.t / "kstat/tank/objset-0x99").write_text(objset("tank/vm-100-disk-0", writes=999))
        # GETATTR/LOOKUP/ACCESS über NFS (der Bot prüft .clip-speicher) – proc3 Feld getattr/lookup/access steigt
        (self.t / "nfsd").write_text("proc3 22 0 900 0 900 900 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0\n"
                                     "proc4ops 72 " + " ".join(["0"] * 72) + "\n")
        self.uptime(1500)
        self.lauf()
        self.assertEqual(self.status["gruende"], [])

    def test_nfs_datenoperationen_zaehlen(self):
        self.uptime(600)
        self.lauf()
        ops = ["0"] * 72
        ops[25] = "40"  # READ
        (self.t / "nfsd").write_text("proc3 22 0 5 0 7 3 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0\n"
                                     "proc4ops 72 " + " ".join(ops) + "\n")
        self.uptime(700)
        self.lauf()
        self.assertIn("nfs4 +40", self.status["gruende"])

    def test_offene_nfs_datei_nur_bei_lebendem_client(self):
        c = self.t / "clients/5"
        c.mkdir()
        (c / "states").write_text('- 0x1: { type: open, access: rw, deny: --, filename: "eingang/x.mp4" }\n'
                                  '- 0x2: { type: open, access: r-, deny: --, filename: ".clip-speicher" }\n')
        (c / "info").write_text('clientid: 0x5\naddress: "192.168.1.5:815"\nstatus: confirmed\n'
                                "seconds from last renew: 12\n")
        self.uptime(1500)
        self.lauf()
        self.assertEqual(self.status["gruende"], ["NFS offen: 192.168.1.5:815 eingang/x.mp4"])
        (c / "info").write_text('address: "192.168.1.5:815"\nstatus: courtesy\nseconds from last renew: 400\n')
        self.lauf()
        self.assertEqual(self.status["gruende"], [])  # abgestürzter Mini hält nicht wach

    def test_smb_nur_datei_mit_datenzugriff(self):
        teil = self.t / "clips/eingang/a.mp4.teil"
        teil.write_text("x")
        os.utime(teil, (1, 1))  # alte mtime (Windows setzt sie zurück) – aber frische ctime
        daten = {"open_files": {
            str(teil): {"service_path": str(self.t / "clips"), "filename": "eingang/a.mp4.teil",
                        "opens": {"1": {"access_mask": {"hex": "0x00120196"}}}},
            "dir": {"service_path": str(self.t / "clips"), "filename": "eingang",
                    "opens": {"2": {"access_mask": {"hex": "0x00100081"}}}}}}
        self.stub("smbstatus", f"echo '{json.dumps(daten)}'")
        self.uptime(1500)
        self.lauf()
        self.assertIn("SMB offen: eingang/a.mp4.teil", self.status["gruende"])
        self.assertIn("Kopie läuft: eingang/a.mp4.teil", self.status["gruende"])
        self.assertEqual(len([g for g in self.status["gruende"] if g.startswith("SMB")]), 1)  # Ordner zählt nicht

    def test_herzschlag_frisch_haelt_alt_nicht(self):
        schlag = self.t / "clips/.aktiv/mini-pipeline"
        schlag.touch()
        self.uptime(1500)
        self.lauf()
        self.assertEqual(self.status["gruende"], ["Herzschlag: mini-pipeline"])
        os.utime(schlag, (time.time() - 600, time.time() - 600))
        self.lauf()
        self.assertEqual(self.status["gruende"], [])

    def test_gaeste_menschen_arbeit_halten(self):
        self.uptime(1500)
        self.stub("pct", "printf 'VMID Status Lock Name\\n101 running  web\\n102 stopped x\\n'")
        self.lauf()
        self.assertEqual(self.status["gruende"], ["Gast 101 läuft"])
        self.konf(TROCKEN="1", LEERLAUF_MIN="20", MINDEST_WACH_MIN="10", GAESTE_ERLAUBT="101")
        self.stub("who", "echo 'root pts/0 2026-09-24 10:00'")
        self.stub("pgrep", '[ "$2" = ffmpeg ] && echo 4711; exit 0')
        (self.t / "halten").touch()
        self.lauf()
        self.assertEqual(sorted(self.status["gruende"]), sorted(["Halten-Datei", "ffmpeg läuft", "angemeldet: root pts/0"]))

    def test_kaputte_quelle_heisst_wach(self):
        (self.t / "kstat/tank/objset-0x36").unlink()
        self.konf(TROCKEN="0", LEERLAUF_MIN="1", MINDEST_WACH_MIN="1")
        self.uptime(3600)
        ausgabe = self.lauf()
        self.assertIn("WARNUNG Quelle unklar", ausgabe)
        self.assertIn("Quelle unklar", self.status["gruende"])
        self.assertFalse(self.aus)
        self.uptime(3660)
        self.assertNotIn("WARNUNG", self.lauf())  # höchstens stündlich

    def test_gegenprobe_verhindert_aus(self):
        self.konf(TROCKEN="0", LEERLAUF_MIN="20", MINDEST_WACH_MIN="10")
        zaehler = self.t / "who-zaehler"
        # beim ersten Blick niemand da, bei der Gegenprobe meldet sich jemand an
        self.stub("who", f'n=$(cat {zaehler} 2>/dev/null || echo 0); echo $((n+1)) > {zaehler}; '
                         f'[ $n -ge 1 ] && echo "root pts/1 2026-09-24 10:00"; exit 0')
        self.uptime(1500)
        ausgabe = self.lauf()
        self.assertIn("Gegenprobe: doch aktiv", ausgabe)
        self.assertFalse(self.aus)

    def test_automatik_aus_datei(self):
        (self.t / "automatik-aus").touch()
        self.konf(TROCKEN="0", LEERLAUF_MIN="1", MINDEST_WACH_MIN="1")
        self.uptime(9999)
        self.lauf()
        self.assertFalse(self.aus)


if __name__ == "__main__":
    unittest.main()
