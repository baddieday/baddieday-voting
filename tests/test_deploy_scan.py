"""M148 (Florian 22.10.: „ja“): Der Mini verarbeitet deine Matches selbst – clip-scan.timer statt nur n8n.

Geprüft werden die Unit-Dateien (Befehl, Sandbox wie clip-sitzungen, Claude-Anmeldung wie der Lern-Bot) und
`scan --mindestalter`: Ein frisch erfasstes Match wartet, bis die letzten Aufnahmen vom PC da sein können.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from clip_pipeline import cli
from clip_pipeline.zeit import UTC, iso, jetzt

from tests.hilfen import MitSpeicher
from tests.test_deploy_puffer import DEPLOY, lies_unit
from tests.test_scan_grenzen import ScanGrenzen

DIENST = DEPLOY / "systemd" / "clip-scan.service"
TIMER = DEPLOY / "systemd" / "clip-scan.timer"
PIPELINE = "/opt/clip-pipeline/.venv/bin/pipeline"


class Units(unittest.TestCase):
    def test_dienst_befehl_und_sandbox(self):
        s = lies_unit(DIENST)
        befehl = s["ExecStart"][0].split()
        self.assertEqual(befehl[:3], [PIPELINE, "scan", "--verarbeiten"])
        args = cli.baue_parser().parse_args(befehl[1:])
        self.assertEqual((args.max, args.versuche, args.mindestalter), (1, 3, 10))
        self.assertTrue(args.sperren)
        for schluessel, wert in (("User", "pipeline"), ("Type", "oneshot"), ("NoNewPrivileges", "true"),
                                 ("PrivateTmp", "true"), ("ProtectSystem", "strict"), ("ProtectHome", "read-only")):
            with self.subTest(schluessel):
                self.assertEqual(s[schluessel], [wert])
        self.assertEqual(s["ReadWritePaths"], ["/var/lib/clip-pipeline /srv/clips -/srv/puffer"])
        self.assertIn("CLAUDE_CONFIG_DIR=/var/lib/clip-pipeline/claude", s["Environment"])
        self.assertEqual(s["SuccessExitStatus"], ["3 4"])

    def test_timer_alle_fuenf_minuten(self):
        t = lies_unit(TIMER)
        self.assertEqual((t["OnUnitActiveSec"], t["WantedBy"]), (["5min"], ["timers.target"]))
        self.assertNotIn("Unit", t)


class Mindestalter(MitSpeicher):
    """Wie ScanGrenzen (gefälschtes process, drei alte Matches), dazu eines, das gerade erst erfasst wurde. Die
    Hilfen kommen von ScanGrenzen, ohne dessen Tests ein zweites Mal laufen zu lassen."""
    falsch, scan, status = ScanGrenzen.falsch, ScanGrenzen.scan, ScanGrenzen.status

    def setUp(self):
        super().setUp()
        for i, mid in enumerate(("m1", "m2", "m3")):                    # wie ScanGrenzen: m1 ist das älteste
            start = datetime(2026, 10, 8, 18 + i, 0, tzinfo=UTC)
            self.con.execute("INSERT INTO matches (id, replay_pfad, start_utc, ende_utc, erstellt, geaendert) "
                             "VALUES (?, ?, ?, ?, ?, ?)", (mid, f"replays/{mid}.replay", iso(start),
                                                           iso(start + timedelta(minutes=20)), iso(start), iso(start)))
        self.aufrufe: list[str] = []
        neu = jetzt()
        self.con.execute("INSERT INTO matches (id, replay_pfad, start_utc, ende_utc, erstellt, geaendert) "
                         "VALUES ('frisch', 'replays/frisch.replay', ?, ?, ?, ?)",
                         (iso(neu - timedelta(minutes=25)), iso(neu - timedelta(minutes=5)), iso(neu), iso(neu)))

    def test_frisches_match_wartet_aeltere_laufen(self):
        code, ergebnis = self.scan("--mindestalter", "10")
        self.assertEqual(code, 0)
        self.assertEqual(self.aufrufe, ["m1", "m2", "m3"])
        self.assertIn("frisch", ergebnis["offen"])                     # bleibt offen, nächster Lauf
        self.assertEqual(self.status()["frisch"], "neu")

    def test_ohne_schalter_wie_bisher(self):
        self.scan()
        self.assertEqual(self.aufrufe, ["m1", "m2", "m3", "frisch"])
