"""Mehrbenutzer, Stufe 1, Schritt 4 (PR 4): n8n kommt nur mit den Vertragsbefehlen durch (deploy/n8n-lauf.sh).

n8n ruft per SSH an; in authorized_keys ist sein Schlüssel auf deploy/n8n-lauf.sh festgenagelt (command=…). Das Skript
lässt nur die Befehle aus dem Vertrag durch und startet sie ohne Shell. Mit Freunden auf dem Mini ist es die Grenze
zwischen n8n (vServer im Rechenzentrum) und ihren Instanzen: kein --konfig, kein CLIP_INSTANZ=, kein scan, kein
Zusatzwort, keine ungültige Session-ID. Bisher gab es für das Skript keinen Test.

Getestet wird eine Kopie, in der /opt/clip-pipeline auf einen Temp-Ordner zeigt (Muster tests/test_deploy_puffer.py);
bin/pipeline ist dort eine Attrappe, die nur mitschreibt, womit sie aufgerufen wurde. Die Vertragsaufrufe stammen aus
den n8n-Workflows selbst (1-match-verarbeiten.json, 2-highlight-video.json) – ändert sich dort ein Befehl, merkt es
dieser Test.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

PROJEKT = Path(__file__).resolve().parents[1]
SKRIPT = PROJEKT / "deploy" / "n8n-lauf.sh"
WORKFLOWS = (PROJEKT / "1-match-verarbeiten.json", PROJEKT / "2-highlight-video.json")
OPT = "/opt/clip-pipeline"
SID = "2026-09-23_20-15-33"
WERTE = {"session": SID, "id": "highlight-2026-10-08", "tage": "14"}   # was n8n in die Ausdrücke einsetzt
AUSDRUCK = re.compile(r"\{\{\s*\$\('[^']*'\)\.first\(\)\.json\.(\w+)\s*\}\}")
ABGEWIESEN = '{"fehler": "Aufruf nicht erlaubt"}'

# Steht statt /opt/clip-pipeline/bin/pipeline: schreibt Arbeitsordner und Argumente mit, Exit wie ATTRAPPE_EXIT
ATTRAPPE = """#!/bin/sh
{ printf 'cwd=%s\\n' "$PWD"; for a in "$@"; do printf 'arg=%s\\n' "$a"; done; } >> "$AUFRUFE"
echo '{"attrappe": true}'
exit "${ATTRAPPE_EXIT:-0}"
"""


def vertragsaufrufe() -> list[tuple[str, str]]:
    """(Knoten, Befehl als Vorlage mit {bin}) für jeden SSH-Knoten der Workflows – mit den Werten von oben."""
    aufrufe = []
    for datei in WORKFLOWS:
        for knoten in json.loads(datei.read_text(encoding="utf-8"))["nodes"]:
            p = knoten.get("parameters", {})
            if "command" not in p:
                continue
            assert p.get("cwd") == OPT, knoten["name"]   # Arbeitsordner laut Vertrag
            befehl = AUSDRUCK.sub(lambda m: "{bin}" if m[1] == "pipelineBin" else WERTE[m[1]], p["command"].lstrip("="))
            aufrufe.append((knoten["name"], befehl))
    return aufrufe


class N8nEinstieg(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(os.path.realpath(self._tmp.name))
        self.opt = self.tmp / "opt"                                      # statt /opt/clip-pipeline
        self.bin = f"{self.opt}/bin/pipeline"
        (self.opt / "bin").mkdir(parents=True)
        Path(self.bin).write_text(ATTRAPPE, encoding="utf-8")
        Path(self.bin).chmod(0o755)
        self.skript = self.tmp / "n8n-lauf.sh"
        self.skript.write_text(SKRIPT.read_text(encoding="utf-8").replace(OPT, str(self.opt)), encoding="utf-8")
        self.skript.chmod(0o755)
        self.aufrufe = self.tmp / "aufrufe"

    def lauf(self, befehl: str | None, **umgebung: str) -> subprocess.CompletedProcess:
        """Wie sshd mit command=: das Skript direkt starten, n8ns Befehl in SSH_ORIGINAL_COMMAND (None = keiner)."""
        self.aufrufe.unlink(missing_ok=True)   # jeder Lauf schreibt frisch mit
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.tmp), "AUFRUFE": str(self.aufrufe),
               **umgebung}
        if befehl is not None:
            env["SSH_ORIGINAL_COMMAND"] = befehl
        return subprocess.run([str(self.skript)], env=env, cwd=self.tmp, capture_output=True, text=True, timeout=10)

    def mitgeschrieben(self) -> list[str]:
        """Womit die Attrappe im letzten Lauf aufgerufen wurde (leer: gar nicht)."""
        return self.aufrufe.read_text(encoding="utf-8").splitlines() if self.aufrufe.exists() else []

    def test_vertragsaufrufe_kommen_durch(self):
        aufrufe = vertragsaufrufe()
        self.assertEqual([k for k, _ in aufrufe], ["1 Vorbereiten", "2 Analysieren", "3 Schnittliste", "4 Rendern",
                                                   "Highlight bauen"])
        aufrufe.append(("status (docs/SERVER.md)", "{bin} status"))
        # n8n setzt für den Arbeitsordner „cd '…' ; “ bzw. „cd … && “ davor – beides kennt das Skript
        for knoten, vorlage in aufrufe:
            for davor in ("", f"cd '{self.opt}' ; ", f"cd {self.opt} && "):
                befehl = davor + vorlage.format(bin=self.bin)
                with self.subTest(befehl=befehl):
                    r = self.lauf(befehl)
                    self.assertEqual((r.returncode, r.stdout, r.stderr), (0, '{"attrappe": true}\n', ""))
                    argumente = vorlage.format(bin="").split()
                    self.assertEqual(self.mitgeschrieben(), [f"cwd={self.opt}", *(f"arg={a}" for a in argumente)])

    def test_exit_code_und_json_der_pipeline_kommen_unveraendert_an(self):
        # n8n wertet Exit-Code und letzte JSON-Zeile aus (Fehler-Alarm) – das Skript startet per exec, nichts dazwischen
        r = self.lauf(f"{self.bin} render --session {SID}", ATTRAPPE_EXIT="3")
        self.assertEqual((r.returncode, r.stdout), (3, '{"attrappe": true}\n'))

    def test_alles_andere_wird_abgewiesen(self):
        kanarie = self.tmp / "kanarie"
        faelle = {
            "--konfig": f"{self.bin} --konfig /tmp/fremd.toml render --session {SID}",
            "vorangestelltes CLIP_INSTANZ=":
                f"CLIP_INSTANZ=/var/lib/clip-benutzer/max {self.bin} render --session {SID}",
            "scan --verarbeiten": f"{self.bin} scan --verarbeiten",
            "Zusatzwort --benutzer": f"{self.bin} render --session {SID} --benutzer max",
            "ungültige Session-ID": f"{self.bin} render --session ../geheim",
            "angehängter Shell-Befehl": f"{self.bin} render --session {SID}; touch {kanarie}",
            "cd in einen anderen Ordner": f"cd /tmp && {self.bin} render --session {SID}",
            "ohne Befehl (Anmeldung mit dem Schlüssel)": None,
        }
        for fall, befehl in faelle.items():
            with self.subTest(fall=fall):
                r = self.lauf(befehl)
                self.assertEqual((r.returncode, r.stdout.strip()), (2, ABGEWIESEN))
                self.assertEqual(self.mitgeschrieben(), [])   # die Pipeline wurde gar nicht erst gestartet
        self.assertFalse(kanarie.exists())                     # nichts lief über eine Shell

    def test_zweite_zeile_wird_nie_ausgefuehrt(self):
        # Das Skript liest nur die erste Zeile; ausgeführt wird höchstens der geprüfte Vertragsbefehl – nie eine Zeile
        # dahinter (über eine Shell gestartet, entstünde die Kanarie)
        kanarie = self.tmp / "kanarie"
        r = self.lauf(f"{self.bin} render --session {SID}\ntouch {kanarie}")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.mitgeschrieben(), [f"cwd={self.opt}", "arg=render", "arg=--session", f"arg={SID}"])
        self.assertFalse(kanarie.exists())


if __name__ == "__main__":
    unittest.main()
