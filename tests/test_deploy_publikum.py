"""Deploy und Doku der Lernschleife „Publikum“, Stufe 1 (Spec §12, §14; Plan Paket e, Annahmen A16, A17).

Kein echter Host: Geprüft werden die Dateien selbst – die Unit-Dateien des täglichen Laufs (clip-publikum), das
Drop-in, mit dem claude im Lern-Bot-Dienst laufen darf, und die Anleitung docs/PUBLIKUM.md. Ob systemd das
Drop-in genau so anwendet, lässt sich hier nicht ausprobieren (kein systemd im Container) – das steht in der
Anleitung als 🏠-Schritt mit einer Probe.
"""

from __future__ import annotations

import re
import unittest

from clip_pipeline import cli

from tests.test_deploy_puffer import DEPLOY, KONFIG, PROJEKT, assertReihenfolge, lies_unit

SYSTEMD = DEPLOY / "systemd"
DIENST = SYSTEMD / "clip-publikum.service"
TIMER = SYSTEMD / "clip-publikum.timer"
DROP_IN = SYSTEMD / "clip-lernbot.service.d" / "claude.conf"
ANLEITUNG = PROJEKT / "docs" / "PUBLIKUM.md"
PIPELINE = "/opt/clip-pipeline/.venv/bin/pipeline"


def abschnitt(text: str, ueberschrift: str) -> str:
    """Ein ##-Abschnitt der Anleitung (bis zur nächsten ##-Überschrift)."""
    treffer = re.search(rf"^## {re.escape(ueberschrift)}.*?(?=^## |\Z)", text, flags=re.M | re.S)
    if treffer is None:
        raise AssertionError(f"Abschnitt „## {ueberschrift}“ fehlt in {ANLEITUNG.name}")
    return treffer.group(0)


class Units(unittest.TestCase):
    def test_dienst_wie_die_morgenpruefung(self):
        s = lies_unit(DIENST)
        self.assertEqual(s["ExecStart"], [f"{PIPELINE} publikum bewerten"])
        self.assertEqual(s["Type"], ["oneshot"])
        for schluessel, wert in (("User", "pipeline"), ("WorkingDirectory", "/opt/clip-pipeline"),
                                 ("NoNewPrivileges", "true"), ("PrivateTmp", "true"), ("ProtectSystem", "strict"),
                                 ("ProtectHome", "true")):
            with self.subTest(schluessel):
                self.assertEqual(s[schluessel], [wert])
        self.assertEqual(s["ReadWritePaths"], ["/var/lib/clip-pipeline"])  # nur die Datenbank – nie Puffer oder Lager
        self.assertIn("TimeoutStartSec", s)
        # Exit 1 (ein Post nicht bewertbar) und 2 (Konfig) sollen in systemd rot sein – kein SuccessExitStatus
        self.assertNotIn("SuccessExitStatus", s)
        text = DIENST.read_text(encoding="utf-8")
        self.assertIn(f"Von Hand: sudo -u pipeline {PIPELINE} publikum bewerten", text)
        self.assertIn("main", text)          # Kommentar: welcher Checkout
        self.assertIn("weckt pve-big NIE", text)

    def test_timer_taeglich_um_zehn(self):
        t = lies_unit(TIMER)
        self.assertEqual(t["OnCalendar"], ["*-*-* 10:00"])  # die Uhrzeit steht nur hier (A16)
        self.assertEqual(t["Persistent"], ["true"])
        self.assertEqual(t["RandomizedDelaySec"], ["10min"])
        self.assertEqual(t["WantedBy"], ["timers.target"])
        self.assertNotIn("Unit", t)  # startet clip-publikum.service (gleicher Name)
        self.assertNotIn("uhrzeit", KONFIG["publikum"])  # keine zweite Wahrheit in der Konfig

    def test_befehl_gibt_es_und_braucht_keine_sperre(self):
        args = cli.baue_parser().parse_args(lies_unit(DIENST)["ExecStart"][0].split()[1:])
        self.assertIs(args.fn, cli._cmd_publikum)
        self.assertFalse(args.sperren)
        self.assertNotIn(args.befehl, cli.WECKEN)

    def test_drop_in_fuer_claude_im_lern_bot(self):
        self.assertTrue((SYSTEMD / "clip-lernbot.service").is_file())  # das Drop-in gehört zu diesem Dienst
        d = lies_unit(DROP_IN)
        self.assertIn("[Service]", DROP_IN.read_text(encoding="utf-8"))
        self.assertEqual(d["ProtectHome"], ["read-only"])
        # "-": fehlt /home/pipeline, startet der Bot trotzdem. Genau EIN Eintrag, kein leeres „ReadWritePaths=“ –
        # ein leerer Eintrag setzte die Liste zurück, dann wäre auch /var/lib/clip-pipeline (Datenbank) nur lesbar.
        self.assertEqual(d["ReadWritePaths"], ["-/home/pipeline"])
        self.assertIn("DISABLE_AUTOUPDATER=1", d["Environment"])
        self.assertNotIn("ProtectSystem", d)  # bleibt strict aus der Haupt-Unit
        # die Haupt-Unit bleibt, wie sie ist (dort steht weiter ProtectHome=true)
        self.assertEqual(lies_unit(SYSTEMD / "clip-lernbot.service")["ProtectHome"], ["true"])


class Anleitung(unittest.TestCase):
    def setUp(self):
        self.text = ANLEITUNG.read_text(encoding="utf-8")

    def test_bedienung_ist_erklaert(self):
        for thema in ("📦", "/link", "Screenshot", "#17", "Hand-Eingabe", "1240 61 6.8 34", "/publikum",
                      "Basis zu klein", "Ruhezeit", "pipeline publikum bewerten", "## Was tun, wenn"):
            with self.subTest(thema):
                self.assertIn(thema, self.text)

    def test_jeder_konfig_schluessel_ist_erklaert(self):
        schluessel = [f"[publikum].{k}" for k, w in KONFIG["publikum"].items() if not isinstance(w, dict)]
        schluessel += [f"[publikum.gewichte].{k}" for k in KONFIG["publikum"]["gewichte"]]
        schluessel += [f"[lernbot].{k}" for k in KONFIG["lernbot"] if k.startswith("screenshot")]
        schluessel += ["[decide].programm", "[telegram].leise_von", "[vorschau].max_mb"]
        tabelle = abschnitt(self.text, "Konfig-Schlüssel")
        for name in schluessel:
            with self.subTest(name):
                self.assertIn(f"`{name}`", tabelle)

    def test_installation_in_der_richtigen_reihenfolge(self):
        installation = abschnitt(self.text, "Installation")
        assertReihenfolge(self, installation, [
            "sqlite3 /var/lib/clip-pipeline/pipeline.db \".backup",  # zuerst sichern
            "git pull --ff-only",
            "systemctl restart clip-bot",
            "claude --help | grep no-session-persistence",
            "sudo systemd-run --uid=pipeline -p ProtectHome=read-only -p ReadWritePaths=/home/pipeline --pty "
            "/home/pipeline/.local/bin/claude -p --no-session-persistence \"sag ok\"",
            "clip-lernbot.service.d",
            "systemctl daemon-reload",
            "systemctl restart clip-lernbot",
            "clip-publikum.service",
            f"sudo -u pipeline {PIPELINE} publikum bewerten",
            "systemctl enable --now clip-publikum.timer",
            "systemctl list-timers",
        ])
        for pflicht in ("**Was:**", "**Warum:**", "**Freigabe nötig?**", "**Rückweg:**", "**Was du lernst:**", "🏠"):
            with self.subTest(pflicht):
                self.assertIn(pflicht, installation)

    def test_abnahme_nennt_das_erwartete_ergebnis(self):
        abnahme = abschnitt(self.text, "Abnahme")
        for teil in ("alter_tage = 3", "Score 0", "Basis zu klein", "wieder entfernen"):
            with self.subTest(teil):
                self.assertIn(teil, abnahme)

    def test_alle_dateien_sind_genannt(self):
        for datei in ("deploy/systemd/clip-publikum.service", "deploy/systemd/clip-publikum.timer",
                      "deploy/systemd/clip-lernbot.service.d/claude.conf", "templates/screenshot-prompt.txt",
                      "config/lokal.beispiel.toml"):
            with self.subTest(datei):
                self.assertIn(datei, self.text)
                self.assertTrue((PROJEKT / datei).is_file(), datei)


class Verweise(unittest.TestCase):
    def test_regie_verweist_im_lern_bot_abschnitt(self):
        regie = (PROJEKT / "docs/REGIE.md").read_text(encoding="utf-8")
        lern_bot = abschnitt_von(regie, "## Lern-Bot (Telegram)")
        assertReihenfolge(self, lern_bot, ["Abends um 21:00", "/publikum", "docs/PUBLIKUM.md"])

    def test_readme_nennt_befehl_und_ist_nicht_veraltet(self):
        readme = (PROJEKT / "README.md").read_text(encoding="utf-8")
        for teil in ("pipeline publikum bewerten", "/publikum", "docs/PUBLIKUM.md"):
            with self.subTest(teil):
                self.assertIn(teil, readme)
        # Stand seit E19 und „Nie löschen“ (25.09.): der Gaming-PC weckt nie, nichts wird recycelt
        for veraltet in ("großen Proxmox-Host (weckt ihn", "weckt ihn die Pipeline", "wird recycelt",
                         "55 schlanke Tests"):
            with self.subTest(veraltet):
                self.assertNotIn(veraltet, readme)


def abschnitt_von(text: str, ueberschrift: str) -> str:
    """Text ab einer Überschrift bis zur nächsten gleich hohen (für Dateien außer PUBLIKUM.md)."""
    anfang = text.index(ueberschrift)
    ende = text.find("\n## ", anfang + len(ueberschrift))
    return text[anfang:] if ende == -1 else text[anfang:ende]


if __name__ == "__main__":
    unittest.main()
