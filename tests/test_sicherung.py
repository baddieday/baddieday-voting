"""`pipeline sicherung` (B1): Datenbank sichern, unabhängig vom Betriebsmodus (auch ohne [lager]/getrennten Betrieb)."""

import contextlib
import io
import json
import unittest
from unittest import mock

from clip_pipeline import cli, lager

from tests.hilfen import MitSpeicher


class Sicherung(MitSpeicher):
    def _cli(self, argv: list[str]) -> tuple[int, dict]:
        ausgabe = io.StringIO()
        with mock.patch("clip_pipeline.cli.lade", return_value=self.konfig), \
                contextlib.redirect_stdout(ausgabe), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(argv)
        return code, json.loads(ausgabe.getvalue().strip().splitlines()[-1])

    def test_sichert_ohne_getrennten_betrieb(self):
        code, e = self._cli(["sicherung"])
        self.assertEqual(code, 0)
        self.assertTrue((self.konfig.wurzel / e["sicherung"]).is_file())

    def test_rotiert_alte_sicherungen(self):
        self.konfig.daten.setdefault("lager", {})["sicherungen_behalten"] = 1
        ordner = self.konfig.wurzel / lager.SICHERUNG
        ordner.mkdir(parents=True, exist_ok=True)
        alt = ordner / "pipeline-2020-01-01.db"
        alt.write_bytes(b"x")

        code, e = self._cli(["sicherung"])

        self.assertEqual(code, 0)
        self.assertFalse(alt.exists())
        self.assertTrue((self.konfig.wurzel / e["sicherung"]).is_file())


if __name__ == "__main__":
    unittest.main()
