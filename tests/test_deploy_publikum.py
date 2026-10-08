"""Deploy und Doku der Lernschleife „Publikum“, Stufe 1 (Spec §12, §14; Plan Paket e, Annahmen A16, A17).

Kein echter Host: Geprüft werden die Dateien selbst – die Unit-Dateien des täglichen Laufs (clip-publikum), das
Drop-in, mit dem claude im Lern-Bot-Dienst laufen darf, und die Anleitung docs/PUBLIKUM.md. Ob systemd das
Drop-in genau so anwendet, lässt sich hier nicht ausprobieren (kein systemd im Container) – das steht in der
Anleitung als 🏠-Schritt mit einer Probe.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from clip_pipeline import claude_aufruf, cli

from tests.test_deploy_puffer import DEPLOY, KONFIG, PROJEKT, assertReihenfolge, lies_unit

SYSTEMD = DEPLOY / "systemd"
UPDATE = DEPLOY / "pve-mini" / "alles-aktualisieren.sh"
DIENST = SYSTEMD / "clip-publikum.service"
TIMER = SYSTEMD / "clip-publikum.timer"
DROP_IN = SYSTEMD / "clip-lernbot.service.d" / "claude.conf"
ANLEITUNG = PROJEKT / "docs" / "PUBLIKUM.md"
PIPELINE = "/opt/clip-pipeline/.venv/bin/pipeline"
HAUPT_UNIT = SYSTEMD / "clip-lernbot.service"
# Hier hält claude im Lern-Bot-Dienst Anmeldung und Einstellungen – nicht im Home (siehe Drop-in)
CLAUDE_ORDNER = "/var/lib/clip-pipeline/claude"


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
        self.assertTrue(HAUPT_UNIT.is_file())  # das Drop-in gehört zu diesem Dienst
        d = lies_unit(DROP_IN)
        text = DROP_IN.read_text(encoding="utf-8")
        self.assertIn("[Service]", text)
        self.assertEqual(d["ProtectHome"], ["read-only"])  # /home lesbar (claude liegt evtl. dort), nie beschreibbar
        # Kein beschreibbares Home: dort liegen authorized_keys mit der Sperre des n8n-Schlüssels und die claude-Datei,
        # die `decide` ohne Schutz startet. Die Anmeldung liegt stattdessen in CLAUDE_CONFIG_DIR …
        self.assertNotIn("ReadWritePaths", d)
        self.assertIn(f"CLAUDE_CONFIG_DIR={CLAUDE_ORDNER}", d["Environment"])
        self.assertIn("DISABLE_AUTOUPDATER=1", d["Environment"])
        self.assertIn("authorized_keys", text)  # das Warum steht im Kommentar
        # … und die ist schon beschreibbar, weil die Haupt-Unit /var/lib/clip-pipeline freigibt
        haupt = lies_unit(HAUPT_UNIT)
        self.assertIn("/var/lib/clip-pipeline", haupt["ReadWritePaths"][0].split())
        self.assertNotIn("ProtectSystem", d)  # bleibt strict aus der Haupt-Unit
        # die Haupt-Unit bleibt, wie sie ist (dort steht weiter ProtectHome=true)
        self.assertEqual(haupt["ProtectHome"], ["true"])


class UpdateRichtetEin(unittest.TestCase):
    """08.10. (Nichts mehr von Hand): alles-aktualisieren.sh richtet clip-publikum ein und schaltet den Timer an – wie
    clip-sitzungen –, damit die Zuschauerzahlen ohne Schritt P3 von Hand kommen. Geprüft wird der INNEN-Teil (läuft im
    CT unter der Sperre) gegen einen Mini-Checkout mit den Units aus dem Repo; runuser und systemctl sind Stubs, die
    mitschreiben. Vorher installiert: nur clip-bot und clip-lernbot (Stand nach docs/SERVER.md)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.t = Path(self._tmp.name)
        self.prod, self.units, self.stub = self.t / "prod", self.t / "units", self.t / "stub"
        shutil.copytree(SYSTEMD, self.prod / "deploy" / "systemd")
        git = ["git", "-C", str(self.prod), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
               "-c", "commit.gpgsign=false"]
        for befehl in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "Stand"]):
            subprocess.run(git + befehl, check=True, capture_output=True)
        self.stand = subprocess.run(git + ["rev-parse", "HEAD"], check=True, capture_output=True,
                                    text=True).stdout.strip()
        self.units.mkdir()
        for name in ("clip-bot.service", "clip-lernbot.service"):
            shutil.copy(SYSTEMD / name, self.units / name)
        self.stub.mkdir()
        stubs = {"runuser": 'while [ $# -gt 0 ]; do case "$1" in -u) shift 2;; --) shift; break;; *) break;; esac; '
                            'done\nexec "$@"',
                 "systemctl": 'echo "systemctl $*" >> "$STUB/aufrufe"\ncase "$1" in\n'
                              '  cat) test -f "$UNITS/$2" && cat "$UNITS/$2";;\n'
                              '  is-enabled) [ "$2" = -q ] && shift; grep -qx "$2" "$STUB/an" 2>/dev/null;;\n'
                              '  enable) shift; for u in "$@"; do case "$u" in -*) ;; *) echo "$u" >> "$STUB/an";; '
                              'esac; done;;\nesac'}
        for name, inhalt in stubs.items():
            (self.stub / name).write_text(f"#!/bin/sh\n{inhalt}\n", encoding="utf-8")
            (self.stub / name).chmod(0o755)

    def tearDown(self):
        self._tmp.cleanup()

    def lauf(self) -> subprocess.CompletedProcess:
        innen = re.search(r"^INNEN='\n(.*?)^'$", UPDATE.read_text(encoding="utf-8"), flags=re.M | re.S).group(1)
        env = {**os.environ, "PATH": f"{self.stub}:{os.environ['PATH']}", "STUB": str(self.stub),
               "UNITS": str(self.units)}
        # innen <PROD> <REGIE> <MIT_REGIE> <ALT_PROD> <ZIEL_PROD> <ALT_REGIE> <ZIEL_REGIE> <STEMPEL> <UNITS> <SICH>
        return subprocess.run(["bash", "-c", innen, "innen", str(self.prod), str(self.t / "regie"), "0", self.stand,
                               self.stand, "", "", "TEST", str(self.units), str(self.t / "sich")],
                              capture_output=True, text=True, env=env, timeout=60)

    def eingeschaltet(self) -> list[str]:
        an = self.stub / "an"
        return an.read_text(encoding="utf-8").split() if an.exists() else []

    def test_neu_eingerichtet_und_eingeschaltet(self):
        r = self.lauf()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for name in ("clip-publikum.service", "clip-publikum.timer"):
            with self.subTest(name):
                self.assertEqual((self.units / name).read_text(encoding="utf-8"),
                                 (SYSTEMD / name).read_text(encoding="utf-8"))
        self.assertIn("clip-publikum.timer", self.eingeschaltet())
        self.assertIn("clip-sitzungen.timer", self.eingeschaltet())        # wie bisher
        rueckweg = (self.t / "sich.zurueck.sh").read_text(encoding="utf-8")
        self.assertRegex(rueckweg, r"systemctl disable --now .*clip-publikum\.timer")
        self.assertNotIn("clip-aufraeumen", " ".join(os.listdir(self.units)))   # „Nie löschen“ bleibt aus
        # zweiter Lauf: nichts doppelt
        self.lauf()
        self.assertEqual(self.eingeschaltet().count("clip-publikum.timer"), 1)

    def test_von_hand_angepasster_timer_bleibt_unangetastet(self):
        eigen = (SYSTEMD / "clip-publikum.timer").read_text(encoding="utf-8").replace("10:00", "12:00")
        (self.units / "clip-publikum.timer").write_text(eigen, encoding="utf-8")
        r = self.lauf()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.units / "clip-publikum.timer").read_text(encoding="utf-8"), eigen)
        self.assertIn("clip-publikum.timer weicht vom Repo ab", r.stdout)
        self.assertNotIn("clip-publikum.timer", self.eingeschaltet())      # nicht überschrieben, nicht eingeschaltet
        self.assertIn("clip-sitzungen.timer", self.eingeschaltet())


class Anleitung(unittest.TestCase):
    def setUp(self):
        self.text = ANLEITUNG.read_text(encoding="utf-8")

    def test_bedienung_ist_erklaert(self):
        for thema in ("📦", "/link", "Screenshot", "#17", "Hand-Eingabe", "1240 61 6.8 34", "/publikum",
                      "Basis zu klein", "Ruhezeit", "pipeline publikum bewerten", "## Was tun, wenn",
                      # Florian 25.09.: Hand-Eingabe mit Kommentaren/Shares/Saves, MAD-Minimum je Teil
                      "#17 1240 61 6.8 34 3 5 2", "4 oder 7", "[publikum.mad_minimum]", "0,27"):
            with self.subTest(thema):
                self.assertIn(thema, self.text)

    def test_jeder_konfig_schluessel_ist_erklaert(self):
        schluessel = [f"[publikum].{k}" for k, w in KONFIG["publikum"].items() if not isinstance(w, dict)]
        # jede Untertabelle von [publikum] (gewichte, mad_minimum, …) mit jedem ihrer Schlüssel
        schluessel += [f"[publikum.{tabelle}].{k}" for tabelle, inhalt in KONFIG["publikum"].items()
                       if isinstance(inhalt, dict) for k in inhalt]
        self.assertIn("[publikum.mad_minimum].engagement", schluessel)  # die Schleife sieht die neue Tabelle
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
            "systemctl restart clip-bot clip-lernbot",               # beide Bots gleich auf den neuen Stand
            "sudo -iu pipeline command -v claude",                   # wo liegt claude wirklich? (nicht raten)
            "\"$CLAUDE\" --help | grep no-session-persistence",
            f"install -d -m 700 {CLAUDE_ORDNER}",                   # eigener Ordner für die Anmeldung des Dienstes
            "sudo systemd-run --uid=pipeline",                       # Probe mit den Regeln des Dienstes
            "clip-lernbot.service.d",
            "systemctl daemon-reload",
            "systemctl restart clip-lernbot",
            "systemctl show clip-lernbot -p ProtectHome",            # die wirksamen Werte, egal wie viele Drop-ins
            "clip-publikum.service",
            f"sudo -u pipeline {PIPELINE} publikum bewerten",
            "systemctl enable --now clip-publikum.timer",
            "systemctl list-timers",
        ])
        self.assertNotIn("tail -4", installation)  # zeigte bei zwei Drop-ins das falsche
        self.assertNotIn("ReadWritePaths=/home", installation)  # das Home bleibt schreibgeschützt
        # [decide].programm gilt auch für decide aus n8n – ein falscher Pfad schaltet Claude dort still ab
        self.assertIn("Regel-Schnittliste", installation)
        p2 = installation[installation.index("### P2"):installation.index("### P3")]
        self.assertIn("screenshot_claude = false", p2)  # P2 aufschieben oder ablehnen geht (ohne Drop-in)
        for pflicht in ("**Was:**", "**Warum:**", "**Freigabe nötig?**", "**Rückweg:**", "**Was du lernst:**", "🏠"):
            with self.subTest(pflicht):
                self.assertIn(pflicht, installation)

    def test_stufe_1_kommt_jetzt_aus_main(self):
        """Florian 25.09.: Stufe 1 wird sofort eingespielt – über einen eigenen PR nach main, danach `git pull` wie
        bisher; nicht erst nach Stufe 5 mit dem ganzen Sprint."""
        installation = abschnitt(self.text, "Installation")
        vorab = installation[:installation.index("### P1")]
        for teil in ("Stufe 1 kommt jetzt nach `main`", "eigenen PR", "`git pull`", "nicht erst"):
            with self.subTest(teil):
                self.assertIn(teil, vorab)
        self.assertNotIn("Draft-PR gemergt", installation)  # die alte Annahme „ganzer Sprint auf einmal“
        p1 = installation[installation.index("### P1"):installation.index("### P2")]
        assertReihenfolge(self, p1, ["git log --oneline HEAD..origin/main", "git pull --ff-only"])

    def test_probe_wie_der_dienst(self):
        """Die systemd-run-Probe in P2 nutzt dieselben Schutzregeln wie der Dienst (Haupt-Unit + Drop-in) und
        dieselben Schalter wie der Bot – sonst kann sie „ok“ sagen, während der Dienst scheitert."""
        installation = abschnitt(self.text, "Installation")
        start = installation.index("sudo systemd-run --uid=pipeline")
        zeilen = []
        for zeile in installation[start:].splitlines():
            zeilen.append(zeile.rstrip("\\").strip())
            if not zeile.rstrip().endswith("\\"):
                break
        probe = " ".join(zeilen)
        haupt, d = lies_unit(HAUPT_UNIT), lies_unit(DROP_IN)
        erwartet = [f"-p {k}={haupt[k][0]}" for k in ("ProtectSystem", "PrivateTmp", "NoNewPrivileges")]
        erwartet += [f"-p ProtectHome={d['ProtectHome'][0]}", "-p ReadWritePaths=/var/lib/clip-pipeline",
                     "-p WorkingDirectory=/tmp"]
        erwartet += [f"-p Environment={e}" for e in d["Environment"]]
        erwartet.append(" ".join(claude_aufruf.SCHALTER) + " \"sag ok\"")
        for teil in erwartet:
            with self.subTest(teil):
                self.assertIn(teil, probe)

    def test_abnahme_nennt_das_erwartete_ergebnis(self):
        abnahme = abschnitt(self.text, "Abnahme")
        # den Score setzt der Timer aus /opt/clip-pipeline – dessen lokal.toml zählt; von Hand mit vollem Pfad
        for teil in ("alter_tage = 3", "Score 0", "Basis zu klein", "wieder entfernen",
                     "/opt/clip-pipeline/config/lokal.toml", f"sudo -u pipeline {PIPELINE} publikum bewerten",
                     "grep -n alter_tage", "ab 3 Tagen"):
            with self.subTest(teil):
                self.assertIn(teil, abnahme)

    def test_was_tun_nennt_den_notausgang_im_clip_bot(self):
        was_tun = abschnitt(self.text, "Was tun, wenn")
        for teil in ("nicht abgehakt", "journalctl -u clip-bot", "plattformen = []", "vertippter"):
            with self.subTest(teil):
                self.assertIn(teil, was_tun)

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
