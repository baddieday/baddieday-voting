"""`pipeline publikum holen` (B12) und Exit 1 bei `api.fehler` (B14) – TikTok besser, Paket P3.

`holen` = nur die Plattform-Zahlen holen (publikum_adapter.abrufen), keine Scores, keine Lern-Meldung – der Handgriff
nach /tiktok, um zu sehen, ob die Anbindung liefert. `bewerten` bleibt der Timer-Weg: Abruf + Scores; schlägt der
Abruf fehl, ist der Exit 1, die Scores sind trotzdem gesetzt (`api.fehler` in der JSON-Zeile).
Netz ist gefälscht: publikum_adapter.abrufen wird gepatcht, es gibt keinen echten TikTok-Aufruf. Die Tests für
`bewerten` selbst liegen in tests/test_publikum_cli.py (Paket 4).
"""

from __future__ import annotations

import contextlib
import io
import json
import unittest
from datetime import datetime
from unittest import mock

from clip_pipeline import cli

from tests.test_publikum import tag
from tests.test_publikum_cli import MitBewertung


class Holen(MitBewertung):
    def lauf(self, argv: list[str], zeit: datetime = tag(8)) -> tuple[int, dict, list[str]]:
        """cli.main mit der Test-Konfig und fester Zeit: (Exit, letzte stdout-Zeile als JSON, alle stdout-Zeilen)."""
        aus = io.StringIO()
        with mock.patch.object(cli, "lade", return_value=self.konfig), mock.patch.object(cli, "jetzt", return_value=zeit), \
                contextlib.redirect_stdout(aus), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(argv)
        zeilen = aus.getvalue().strip().splitlines()
        return code, json.loads(zeilen[-1]), zeilen

    def test_holen_ruft_nur_den_abruf(self):
        api = {"gespeichert": 2, "fehler": 0}
        with mock.patch("clip_pipeline.publikum_adapter.abrufen", return_value=api) as abrufen, \
                mock.patch("clip_pipeline.publikum.bewerte_alle") as bewerte_alle:
            code, ergebnis, zeilen = self.lauf(["publikum", "holen"])
        self.assertEqual(code, 0)
        self.assertEqual(abrufen.call_count, 1)
        bewerte_alle.assert_not_called()                 # keine Scores – nur holen
        self.assertEqual(len(zeilen), 1, zeilen)         # Logs nach stderr, stdout genau eine JSON-Zeile
        self.assertEqual(ergebnis, api)                  # die JSON-Zeile IST das api-Objekt
        bewertet = [m["schluessel"] for m in self.lern_meldungen() if m["schluessel"].startswith("publikum:bewertet:")]
        self.assertEqual(bewertet, [])                   # keine Meldung „n Posts bewertet“

    def test_holen_exit_1_bei_fehler(self):
        with mock.patch("clip_pipeline.publikum_adapter.abrufen", return_value={"fehler": 1}):
            code, ergebnis, _ = self.lauf(["publikum", "holen"])
        self.assertEqual(code, 1)
        self.assertEqual(ergebnis["fehler"], 1)

    def test_bewerten_exit_1_wenn_der_abruf_scheitert_scores_trotzdem(self):
        """B14: Ein gescheiterter Plattform-Abruf macht den Lauf rot – die Scores sind trotzdem gesetzt."""
        post_id = self.gemessener_post()
        with mock.patch("clip_pipeline.publikum_adapter.abrufen", return_value={"fehler": 1, "gespeichert": 0}):
            code, ergebnis, zeilen = self.lauf(["publikum", "bewerten"])
        self.assertEqual(code, 1)
        self.assertEqual(len(zeilen), 1, zeilen)
        self.assertEqual((ergebnis["fehler"], ergebnis["api"]["fehler"], ergebnis["bewertet"]), (0, 1, 1))
        self.assertEqual(ergebnis["posts"], [{"id": post_id, "score": 0.0}])


class Parser(unittest.TestCase):
    def test_holen_ohne_sperre_und_nicht_in_wecken(self):
        args = cli.baue_parser().parse_args(["publikum", "holen"])
        self.assertEqual(args.aktion, "holen")
        self.assertIs(args.fn, cli._cmd_publikum)
        self.assertFalse(args.sperren)
        self.assertNotIn(args.befehl, cli.WECKEN)


if __name__ == "__main__":
    unittest.main()
