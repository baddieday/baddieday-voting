"""Vertrag Stufe 2 „Merkmale und eine Bewertung“ (Plan docs/superpowers/plans/2026-09-25-lernschleife-stufe-2.md).

Prüft, was alle Pakete gemeinsam voraussetzen: 17 Merkmale mit Namen und Startgewicht, neue Konfig-Abschnitte, die
Migration der Tabelle gewichte, die Import-Regel (per AST, ohne etwas auszuführen) und die Verdrahtung der neuen
Befehle (`merkmale nachtragen`, `stimmung --clips`), die ohne getrennten Betrieb sofort mit Exit 2 ablehnen.
"""

from __future__ import annotations

import ast
import contextlib
import io
import json
import os
import sqlite3
import tempfile
import tomllib
import unittest
from importlib import resources
from pathlib import Path
from unittest import mock

from clip_pipeline import cli, db, erwartung, merkmale, mikro, publikum
from clip_pipeline.vorbewertung import MERKMAL_NAMEN, MERKMALE

WURZEL = Path(__file__).resolve().parent.parent
QUELLEN = WURZEL / "src" / "clip_pipeline"


def _pipeline_toml() -> dict:
    """Nur config/pipeline.toml – ohne lokal.toml, damit der Test überall dasselbe sieht."""
    return tomllib.loads((WURZEL / "config" / "pipeline.toml").read_text(encoding="utf-8"))


def _importe(modul: str, *, nur_modulebene: bool) -> set[str]:
    """Namen der clip_pipeline-Module, die `modul` importiert (relativ: `from . import x`, `from .x import y`)."""
    baum = ast.parse((QUELLEN / f"{modul}.py").read_text(encoding="utf-8"))
    knoten = baum.body if nur_modulebene else list(ast.walk(baum))
    namen: set[str] = set()
    for k in knoten:
        if isinstance(k, ast.ImportFrom) and k.level == 1:
            if k.module:
                namen.add(k.module.split(".")[0])
            else:
                namen.update(a.name for a in k.names)
        elif isinstance(k, ast.ImportFrom) and (k.module or "").startswith("clip_pipeline."):
            namen.add(k.module.split(".")[1])
    return namen


class Merkmale(unittest.TestCase):
    def test_siebzehn_merkmale_in_fester_reihenfolge(self):
        self.assertEqual(len(MERKMALE), 17)
        self.assertEqual(MERKMALE[:5], ("kill_punkte", "victory_royale", "laenge", "lautstaerke", "kommentar"))
        self.assertEqual(set(MERKMALE[5:]), set(merkmale.REPLAY_MERKMALE) | set(merkmale.MIC_MERKMALE))

    def test_jedes_merkmal_hat_einen_kurzen_namen(self):
        for m in MERKMALE:
            with self.subTest(m):
                self.assertLessEqual(len(MERKMAL_NAMEN[m]), 15)  # sonst verrutscht die /gewichte-Tabelle

    def test_jedes_merkmal_hat_ein_startgewicht(self):
        start = _pipeline_toml()["vorbewertung"]["startgewichte"]
        self.assertEqual(set(start), set(MERKMALE))
        self.assertEqual(start["bot_opfer"], -2.0)  # Fertig-Kriterium Stufe 2: Bot-Opfer kosten Punkte


class Konfig(unittest.TestCase):
    def test_neue_abschnitte(self):
        t = _pipeline_toml()
        self.assertEqual(set(t["merkmale"]["waffen"]), {"sniper", "nahkampf", "sonstige"})
        for liste in t["merkmale"]["waffen"].values():
            self.assertTrue(all(isinstance(n, int) for n in liste))  # GunType-ZAHLEN, keine Namen
        for schluessel in ("endgame_spieler", "clutch_vor_s", "clutch_nach_s", "mic", "mic_je_lauf"):
            self.assertIn(schluessel, t["merkmale"])
        for schluessel in ("gewicht_freigabe", "mindest_publikum_paare"):
            self.assertIn(schluessel, t["lernen"])
        for schluessel in ("mindest_urteile", "referenz", "schritte", "lernrate", "l2", "start_a", "start_b",
                           "start_c", "mad_minimum"):
            self.assertIn(schluessel, t["erwartung"])


class Migration(unittest.TestCase):
    def test_alte_datenbank_bekommt_die_gewichte_spalten(self):
        with tempfile.TemporaryDirectory() as tmp:
            pfad = Path(tmp) / "alt.db"
            alt = sqlite3.connect(pfad)  # Stand vor Stufe 2: nur schema.sql
            alt.executescript(resources.files("clip_pipeline").joinpath("schema.sql").read_text(encoding="utf-8"))
            alt.close()
            con = db.verbinde(pfad)
            try:
                spalten = {z["name"] for z in con.execute("PRAGMA table_info(gewichte)")}
            finally:
                con.close()
        self.assertTrue({"trefferquote_publikum", "trefferquote_publikum_start", "quellen"} <= spalten)


class ImportRegel(unittest.TestCase):
    """Leitplanke 7: kein Import-Kreis, Gewichte werden durchgereicht."""

    def test_merkmale(self):
        self.assertFalse(_importe("merkmale", nur_modulebene=False) & {"lernen", "mikro", "stimmung", "verarbeitung"})

    def test_lernen(self):
        self.assertFalse(_importe("lernen", nur_modulebene=False) & {"mikro", "stimmung", "verarbeitung"})

    def test_mikro_importiert_stimmung_nicht_auf_modulebene(self):
        self.assertNotIn("stimmung", _importe("mikro", nur_modulebene=True))
        self.assertFalse(_importe("mikro", nur_modulebene=False) & {"verarbeitung"})

    def test_neue_module_sind_importierbar(self):
        for modul in (merkmale, mikro, erwartung):
            self.assertTrue(modul.__doc__)


class PublikumRobustZ(unittest.TestCase):
    def test_minimum_ist_pflicht_und_wirkt(self):
        # Seit Florians Antwort (25.09.) gibt es kein pauschales MAD-Minimum mehr: jeder Aufrufer nennt seins
        # ([publikum.mad_minimum] je Score-Teil, [erwartung].mad_minimum) – keine versteckte Konstante.
        basis = [0.05, 0.06, 0.07, 0.08, 0.09]
        with self.assertRaises(TypeError):
            publikum.robust_z(0.09, basis)
        z, _, mad = publikum.robust_z(0.09, basis, minimum=0.005)
        self.assertAlmostEqual(mad, 0.01)
        self.assertAlmostEqual(z, 0.02 / (1.4826 * 0.01), places=6)  # das Minimum greift nicht mehr


class Verdrahtung(unittest.TestCase):
    """Neue Befehle: ohne getrennten Betrieb Exit 2 VOR der Sperre, nie in WECKEN."""

    def _cli(self, *argv: str) -> tuple[int, dict]:
        with tempfile.TemporaryDirectory() as tmp:
            umgebung = {"CLIP_SPEICHER": str(Path(tmp) / "speicher"), "CLIP_DATENBANK": str(Path(tmp) / "t.db")}
            ausgabe = io.StringIO()
            with mock.patch.dict(os.environ, umgebung), \
                    mock.patch("clip_pipeline.konfig.Konfig.getrennt", new_callable=mock.PropertyMock,
                               return_value=False), \
                    mock.patch("clip_pipeline.cli.sperre") as sperre, \
                    contextlib.redirect_stdout(ausgabe), contextlib.redirect_stderr(io.StringIO()):
                code = cli.main(list(argv))
            sperre.assert_not_called()
        return code, json.loads(ausgabe.getvalue().strip().splitlines()[-1])

    def test_merkmale_nachtragen_ohne_puffer(self):
        code, daten = self._cli("merkmale", "nachtragen")
        self.assertEqual(code, 2)
        self.assertIn("merkmale nachtragen arbeitet nur im Puffer", daten["hinweis"])

    def test_stimmung_clips_ohne_puffer(self):
        code, daten = self._cli("stimmung", "--clips", "--session", "2026-09-23_20-15-33")
        self.assertEqual(code, 2)
        self.assertIn("stimmung --clips arbeitet nur im Puffer", daten["hinweis"])

    def test_neue_befehle_wecken_nie(self):
        self.assertNotIn("merkmale", cli.WECKEN)
        self.assertNotIn("stimmung", cli.WECKEN)
        parser = cli.baue_parser()
        self.assertFalse(parser.parse_args(["merkmale", "nachtragen"]).sperren)
        self.assertTrue(parser.parse_args(["stimmung", "--clips"]).sperren)  # Whisper: unter der Pipeline-Sperre


if __name__ == "__main__":
    unittest.main()
