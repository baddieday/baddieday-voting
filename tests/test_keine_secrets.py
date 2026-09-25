"""Stolperdraht: Kein Token, Schlüssel oder Passwort in einer Datei, die git verfolgt.

Das Repo ist öffentlich (seit 25.09.2026). Secrets gehören nur in `.env` bzw. `config/lokal.toml` (beide in
.gitignore). Dieser Test durchsucht alle getrackten Dateien nach typischen Token-Mustern und schlägt an, bevor
ein versehentlich eingefügter Wert gepusht wird. Test-Werte sind erlaubt, wenn die Zeile sie ausdrücklich als
solche kennzeichnet („kein echter Token“) oder der Wert erkennbar künstlich ist (enthält „test“/„beispiel“).
"""

import re
import subprocess
import unittest
from pathlib import Path

WURZEL = Path(__file__).resolve().parents[1]

# Name -> Muster. Bewusst eng gefasst: lieber ein klarer Treffer als viele Fehlalarme, die man wegklickt.
MUSTER = {
    "Telegram-Bot-Token": re.compile(r"\b\d{8,10}:AA[A-Za-z0-9_-]{33}\b"),
    "GitHub-Token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})"),
    "Anthropic-Key": re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}"),
    "OpenAI-Key": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9]{32,}"),
    "Tailscale-Auth-Key": re.compile(r"\btskey-[A-Za-z0-9-]{20,}"),
    "AWS-Zugangsschlüssel": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "Privater Schlüssel": re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----"),
    # Zuweisung eines langen Werts an einen Secret-Namen, z. B. TIKTOK_CLIENT_SECRET=abc… oder password: "…"
    "Secret-Zuweisung": re.compile(
        r"(?i)\b[A-Z0-9_]*(?:token|secret|passwor[dt]|api_?key|client_?key)\b\s*[:=]\s*[\"']?(?P<wert>[A-Za-z0-9/+_.=-]{16,})"),
}

# Markierte oder offensichtlich künstliche Werte (Tests, Doku-Beispiele)
ERLAUBT_ZEILE = re.compile(r"(?i)kein echter token|test-wert|beispiel")
ERLAUBT_WERT = re.compile(r"(?i)test|beispiel|example|platzhalter|xxxx|dein|your|changeme|<|\$\{|environ|getenv")


def getrackte_dateien() -> list[Path]:
    ergebnis = subprocess.run(["git", "-C", str(WURZEL), "ls-files", "-z"], capture_output=True, check=True)
    return [WURZEL / n for n in ergebnis.stdout.decode("utf-8", "replace").split("\0") if n]


def funde(text: str) -> list[tuple[int, str]]:
    """(Zeilennummer, Mustername) je Treffer – ohne den Wert selbst, damit er nie in einer Testausgabe landet."""
    ergebnis = []
    for nr, zeile in enumerate(text.splitlines(), 1):
        if ERLAUBT_ZEILE.search(zeile):
            continue
        for name, muster in MUSTER.items():
            for treffer in muster.finditer(zeile):
                wert = treffer.groupdict().get("wert") or treffer.group(0)
                if not ERLAUBT_WERT.search(wert):
                    ergebnis.append((nr, name))
    return ergebnis


class KeineSecrets(unittest.TestCase):
    def test_muster_erkennen_echte_formen(self):
        # Künstlich zusammengesetzt, damit diese Datei selbst keinen „echten“ Wert enthält
        telegram = "123456789:" + "AA" + "Qx7vB2kLm9Np4Rs8Tu1Vw3Yz5Ab6Cd0Ef"  # Form: Bot-ID, „:AA“, 33 Zeichen
        self.assertEqual(funde(f'TOKEN = "{telegram}"'), [(1, "Telegram-Bot-Token")])
        self.assertEqual(funde("TIKTOK_CLIENT_SECRET=" + "q8Zr2Lp5Wx9Mn3Kt7Hv1"), [(1, "Secret-Zuweisung")])
        self.assertEqual(funde("-----BEGIN OPENSSH " + "PRIVATE KEY-----"), [(1, "Privater Schlüssel")])
        # Erlaubt: gekennzeichnete Test-Werte, Platzhalter, Umgebungsvariablen
        self.assertEqual(funde('TOKEN = "707755969:AAtesttesttesttesttesttesttesttest_-ab"  # Test-Wert'), [])
        self.assertEqual(funde("TELEGRAM_TOKEN=<dein-token-hier>"), [])
        self.assertEqual(funde('token = os.environ["TELEGRAM_TOKEN"]'), [])

    def test_keine_secrets_in_getrackten_dateien(self):
        try:
            dateien = getrackte_dateien()
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("kein git-Checkout")
        gefunden = []
        for pfad in dateien:
            if pfad == Path(__file__).resolve() or not pfad.is_file():
                continue
            roh = pfad.read_bytes()
            if b"\0" in roh[:8192]:
                continue  # Binärdatei
            gefunden += [f"{pfad.relative_to(WURZEL)}:{nr} ({name})" for nr, name in funde(roh.decode("utf-8", "replace"))]
        self.assertEqual(gefunden, [], "Möglicher Secret-Wert im Repo – in .env bzw. config/lokal.toml verschieben")


if __name__ == "__main__":
    unittest.main()
