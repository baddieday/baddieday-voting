"""windows/Uebertragung.ps1 unter PowerShell 7 (falls installiert): Session vorbei genau einmal, Replays bleiben."""

import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

PROJEKT = Path(__file__).resolve().parents[1]
PWSH = shutil.which("pwsh") or ("/opt/pwsh/pwsh" if Path("/opt/pwsh/pwsh").is_file() else None)


@unittest.skipIf(PWSH is None, "pwsh fehlt")
class SessionVorbei(unittest.TestCase):
    def test_einmal_melden_nichts_loeschen(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            for d in ("demos", "ziel", "appdata"):
                (t / d).mkdir()
            (t / "ziel" / ".clip-speicher").touch()
            alt = time.time() - 600
            for name in ("UnsavedReplay-2026.09.24-20.15.33.replay", "UnsavedReplay-2026.09.24-20.41.02.replay"):
                (t / "demos" / name).write_text("replay")
                os.utime(t / "demos" / name, (alt, alt))
            (t / "k.psd1").write_text(
                f"@{{ Ziel = '{t}/ziel'; ZielHost = ''; WakeOnLanMac = ''; WeckenWarteSekunden = 10; WebhookUrl = ''\n"
                f"   WebhookToken = ''; RuhezeitSekunden = 60; SessionVorbeiMinuten = 20; MaxAlterTage = 30\n"
                f"   Quellen = @( @{{ Pfad = '{t}/demos'; Muster = @('*.replay'); Ziel = 'replays'; NurKopieren = $true;"
                f" RuhezeitSekunden = 120; Melden = $true }} ) }}\n")

            def lauf():
                return subprocess.run([PWSH, "-NoProfile", "-File", str(PROJEKT / "windows/Uebertragung.ps1"),
                                       "-Konfig", str(t / "k.psd1")], capture_output=True, text=True,
                                      env={**os.environ, "LOCALAPPDATA": str(t / "appdata")}, timeout=120)

            self.assertEqual(lauf().returncode, 0)
            self.assertFalse((t / "ziel" / "sitzungen").exists())  # gerade erst gespielt
            merk = t / "appdata" / "ClipPipeline" / "session.txt"
            os.utime(merk, (time.time() - 1500, time.time() - 1500))  # 25 min Ruhe
            self.assertEqual(lauf().returncode, 0)
            self.assertEqual(lauf().returncode, 0)
            dateien = list((t / "ziel" / "sitzungen").glob("session_*.json"))
            self.assertEqual(len(dateien), 1)
            daten = json.loads(dateien[0].read_text(encoding="utf-8-sig"))
            self.assertEqual(daten["matches"], ["2026-09-24_20-15-33", "2026-09-24_20-41-02"])
            self.assertEqual(len(list((t / "demos").iterdir())), 2)  # Rohdaten bleiben
