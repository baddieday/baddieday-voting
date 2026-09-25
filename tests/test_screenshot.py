"""Tests für claude_aufruf.py und screenshot.py: Claude fragen, Antwort prüfen, Zahlen aus dem Screenshot lesen
(Spec §7.1, §12, §13 „Screenshot-Fluss mit gefälschter Claude-Ausgabe“).

Hier läuft nie das echte `claude`: FakeClaude ersetzt `subprocess.run` und `shutil.which` im Modul claude_aufruf
und merkt sich, welche Dateien Claude im Arbeitsordner gesehen hätte. Zur Sicherheit zeigt [decide].programm in
jedem Test auf ein Programm, das es nicht gibt – ein vergessener Patch führt zu „claude nicht gefunden“, nicht zu
einem echten Aufruf (der gegen das Abo zählen würde).
"""

from __future__ import annotations

import contextlib
import json
import math
import subprocess
import tempfile
from pathlib import Path
from unittest import mock

from clip_pipeline import claude_aufruf, publikum, schema, screenshot
from clip_pipeline.claude_aufruf import ClaudeAntwort

from tests.hilfen import MitSpeicher

GUELTIG = {"views": 1240, "likes": 61, "kommentare": 3, "shares": 5, "saves": 2, "wiedergabe_s": 6.8,
           "voll_prozent": 34.0}
# Nur in der Rohantwort, nie in einem Log: so lässt sich prüfen, dass die Antwort nicht ins Log wandert
ROH_MARKE = "ROHANTWORT-NUR-FUER-DIE-DATENBANK"
KEIN_CLAUDE = "claude-gibt-es-in-tests-nicht"


class FakeClaude:
    """Ersetzt `claude -p`. result: Text im Feld „result“ der JSON-Hülle (wie claude --output-format json).

    Merkt sich je Aufruf den Befehl, die Schlüsselwort-Argumente und die Dateinamen im Arbeitsordner (cwd) – so
    prüfen Tests, dass Claude genau das Bild (und nur das Bild) sieht. waehrend: wird während des Aufrufs
    ausgeführt (z. B. um zu simulieren, dass der Vorgang inzwischen verworfen wurde)."""

    def __init__(self, result: str | None = None, *, returncode: int = 0, is_error: bool = False,
                 stdout: str | None = None, fehler: BaseException | None = None, waehrend=None):
        self.result = json.dumps(GUELTIG) if result is None else result
        self.returncode, self.is_error, self.stdout, self.fehler, self.waehrend = (
            returncode, is_error, stdout, fehler, waehrend)
        self.aufrufe: list[dict] = []

    def __call__(self, befehl, **kw):
        ordner = Path(kw["cwd"])
        self.aufrufe.append({"befehl": list(befehl), "kw": kw, "dateien": sorted(p.name for p in ordner.iterdir()),
                             "ordner": ordner})
        if self.waehrend is not None:
            self.waehrend()
        if self.fehler is not None:
            raise self.fehler
        stdout = self.stdout if self.stdout is not None else json.dumps(
            {"type": "result", "is_error": self.is_error, "result": self.result})
        return subprocess.CompletedProcess(befehl, self.returncode, stdout=stdout, stderr="")

    @contextlib.contextmanager
    def aktiv(self, programm: str | None = "/opt/fake/claude"):
        """Fake einschalten. programm=None: shutil.which findet kein claude."""
        with mock.patch.object(claude_aufruf.shutil, "which", return_value=programm) as which, \
                mock.patch.object(claude_aufruf.subprocess, "run", side_effect=self) as lauf:
            yield lauf, which


class MitClaudeKonfig(MitSpeicher):
    """Feste [lernbot]-Werte und ein eigener Temp-Ordner, damit Tests zählen können, was liegen bleibt."""

    def setUp(self):
        super().setUp()
        self.konfig.daten["decide"]["programm"] = KEIN_CLAUDE  # Sicherheitsnetz, siehe Modul-Docstring
        self.konfig.daten["lernbot"].update(screenshot_claude=True, screenshot_timeout_s=120,
                                            screenshot_prompt="templates/screenshot-prompt.txt")
        self.temp = self.tmp / "temp"
        self.temp.mkdir()
        patcher = mock.patch.object(tempfile, "tempdir", str(self.temp))
        patcher.start()
        self.addCleanup(patcher.stop)

    def temp_reste(self) -> list[str]:
        return sorted(p.name for p in self.temp.iterdir())


# --- claude_aufruf.frage_json -------------------------------------------------------------------------------

class FrageJson(MitClaudeKonfig):
    def frage(self, fake: FakeClaude, programm: str | None = "/opt/fake/claude") -> tuple[ClaudeAntwort, mock.Mock]:
        ordner = self.tmp / "arbeit"
        ordner.mkdir(exist_ok=True)
        (ordner / "screenshot.jpg").write_bytes(b"\xff\xd8\xff")
        with fake.aktiv(programm) as (lauf, _which):
            antwort = claude_aufruf.frage_json(self.konfig, "Lies {bild}", ordner, schema_name="publikum",
                                               timeout_s=5)
        return antwort, lauf

    def test_gueltige_antwort(self):
        roh = "Hier die Zahlen: " + json.dumps(GUELTIG)
        antwort, lauf = self.frage(FakeClaude(roh))
        self.assertEqual(antwort, ClaudeAntwort(GUELTIG, None, roh, gestartet=True))
        # Aufruf: nur Leserecht, JSON-Hülle, kein Sitzungsverlauf (sonst Bildarchiv unter ~/.claude/projects)
        befehl = lauf.call_args.args[0]
        self.assertEqual(befehl[0], "/opt/fake/claude")
        self.assertEqual(befehl[1:7], ["-p", "--output-format", "json", "--allowedTools", "Read",
                                       "--no-session-persistence"])
        self.assertEqual(befehl[-1], "Lies {bild}")
        self.assertEqual(lauf.call_args.kwargs["cwd"], self.tmp / "arbeit")
        self.assertEqual(lauf.call_args.kwargs["timeout"], 5)
        self.assertIs(lauf.call_args.kwargs["stdin"], subprocess.DEVNULL)  # wartet nie auf Eingaben

    def test_claude_meldet_fehler(self):
        antwort, _ = self.frage(FakeClaude("Usage limit reached", is_error=True))
        self.assertEqual(antwort, ClaudeAntwort(None, "claude meldet Fehler (Limit?)", None, gestartet=True))

    def test_antwort_ohne_json(self):
        antwort, _ = self.frage(FakeClaude("Ich sehe leider keine Zahlen."))
        self.assertIsNone(antwort.daten)
        self.assertEqual(antwort.hinweis, "claude-Antwort ohne JSON")
        self.assertEqual(antwort.roh, "Ich sehe leider keine Zahlen.")
        self.assertTrue(antwort.gestartet)  # claude lief – das zählt gegen das Abo

    def test_kaputtes_json(self):
        antwort, _ = self.frage(FakeClaude('{"views": 1240, "likes": }'))
        self.assertEqual((antwort.daten, antwort.hinweis), (None, "claude-Antwort ohne JSON"))

    def test_schema_verstoss(self):
        antwort, _ = self.frage(FakeClaude('{"views": "viel"}'))
        self.assertIsNone(antwort.daten)
        self.assertEqual(antwort.hinweis,
                         "Antwort passt nicht zum Schema: $.views: erwartet number/null, bekommen str")
        self.assertEqual(antwort.roh, '{"views": "viel"}')
        self.assertTrue(antwort.gestartet)

    def test_negative_zahl_verletzt_schema(self):
        antwort, _ = self.frage(FakeClaude('{"likes": -3}'))
        self.assertIsNone(antwort.daten)
        self.assertIn("Schema", antwort.hinweis)

    def test_exit_ungleich_null(self):
        antwort, _ = self.frage(FakeClaude(returncode=1))
        self.assertEqual(antwort, ClaudeAntwort(None, "claude Exit 1", None, gestartet=True))

    def test_huelle_kein_json(self):
        antwort, _ = self.frage(FakeClaude(stdout="Fehler: nicht angemeldet"))
        self.assertEqual(antwort, ClaudeAntwort(None, "claude-Ausgabe kein JSON", None, gestartet=True))

    def test_zeitueberschreitung(self):
        antwort, _ = self.frage(FakeClaude(fehler=subprocess.TimeoutExpired(["claude"], 5)))
        # claude lief bis zum Abbruch – das hat das Abo sehr wahrscheinlich belastet, also zählt es
        self.assertEqual(antwort, ClaudeAntwort(None, "claude nicht nutzbar (TimeoutExpired)", None, gestartet=True))

    def test_programm_startet_nicht(self):
        antwort, _ = self.frage(FakeClaude(fehler=PermissionError("keine Rechte")))
        self.assertEqual(antwort, ClaudeAntwort(None, "claude nicht nutzbar (PermissionError)", None))
        self.assertFalse(antwort.gestartet)  # der Prozess startete gar nicht – zählt nicht

    def test_kein_claude_im_pfad(self):
        fake = FakeClaude()
        antwort, lauf = self.frage(fake, programm=None)
        self.assertEqual(antwort, ClaudeAntwort(None, "claude nicht gefunden", None))
        self.assertFalse(antwort.gestartet)
        lauf.assert_not_called()

    def test_programm_nicht_eingestellt(self):
        del self.konfig.daten["decide"]["programm"]
        antwort, lauf = self.frage(FakeClaude())
        self.assertEqual(antwort, ClaudeAntwort(None, "[decide].programm fehlt in der Konfiguration", None))
        self.assertFalse(antwort.gestartet)
        lauf.assert_not_called()

    def test_unbekanntes_schema_fragt_gar_nicht(self):
        fake = FakeClaude()
        with fake.aktiv() as (lauf, _):
            antwort = claude_aufruf.frage_json(self.konfig, "x", self.tmp, schema_name="gibt-es-nicht",
                                               timeout_s=5)
        self.assertIsNone(antwort.daten)
        self.assertIn("Schema gibt-es-nicht", antwort.hinweis)
        self.assertFalse(antwort.gestartet)
        lauf.assert_not_called()

    def test_log_nennt_hinweis_aber_nie_die_antwort(self):
        with self.assertLogs("pipeline", level="INFO") as logs:
            antwort, _ = self.frage(FakeClaude(json.dumps({**GUELTIG, "notiz": ROH_MARKE})))
        self.assertIsNotNone(antwort.daten)
        text = "\n".join(logs.output)
        self.assertIn("claude (publikum): ok", text)
        self.assertNotIn(ROH_MARKE, text)
        self.assertNotIn("1240", text)
        with self.assertLogs("pipeline", level="INFO") as logs:
            self.frage(FakeClaude(ROH_MARKE))
        self.assertIn("claude-Antwort ohne JSON", "\n".join(logs.output))
        self.assertNotIn(ROH_MARKE, "\n".join(logs.output))


class Protokolliere(MitClaudeKonfig):
    def test_zaehlt_ok_und_hinweis_ohne_rohantwort(self):
        claude_aufruf.protokolliere(self.con, "screenshot", ClaudeAntwort(GUELTIG, None, ROH_MARKE, gestartet=True))
        claude_aufruf.protokolliere(self.con, "screenshot",
                                    ClaudeAntwort(None, "claude Exit 1", ROH_MARKE, gestartet=True))
        zeilen = self.con.execute("SELECT art, text FROM ereignisse ORDER BY id").fetchall()
        self.assertEqual([tuple(z) for z in zeilen],
                         [("claude", "screenshot: ok"), ("claude", "screenshot: claude Exit 1")])
        self.assertFalse(self.con.in_transaction)  # eigene kleine Transaktion, abgeschlossen

    def test_nie_gestartet_zaehlt_nicht(self):
        # Spec §12: gezählt wird der Verbrauch am Abo – ohne gestartetes claude gab es keinen (steht nur im Log)
        for hinweis in ("claude nicht gefunden", "[decide].programm fehlt in der Konfiguration",
                        "claude nicht nutzbar (PermissionError)"):
            claude_aufruf.protokolliere(self.con, "screenshot", ClaudeAntwort(None, hinweis, None))
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM ereignisse").fetchone()[0], 0)


# --- screenshot.prompt / normalisiere ------------------------------------------------------------------------

class Prompt(MitClaudeKonfig):
    def test_platzhalter_ersetzt_json_beispiel_bleibt_heil(self):
        text = screenshot.prompt(self.konfig, "screenshot.png")
        self.assertIn("Bilddatei screenshot.png", text)
        self.assertNotIn("{bild}", text)
        beispiel = json.loads(text.strip().splitlines()[-1])  # letzte Zeile = JSON-Beispiel, unverändert
        self.assertEqual(set(beispiel), set(publikum.FELDER))

    def test_prompt_deckt_abkuerzungen_und_zeiten_ab(self):
        text = screenshot.prompt(self.konfig, "screenshot.jpg")
        for form in ("1,2K", "0:07", "7,3 s", "Mio."):
            self.assertIn(form, text)

    def test_fehlende_vorlage(self):
        self.konfig.daten["lernbot"]["screenshot_prompt"] = str(self.tmp / "fehlt.txt")
        with self.assertRaises(FileNotFoundError):
            screenshot.prompt(self.konfig, "screenshot.jpg")


class Normalisiere(MitClaudeKonfig):
    def test_zaehler_werden_ganzzahlen(self):
        werte = screenshot.normalisiere({"views": 1240.0, "likes": 61, "wiedergabe_s": 7, "voll_prozent": 34})
        self.assertEqual(werte, {"views": 1240, "likes": 61, "kommentare": None, "shares": None, "saves": None,
                                 "wiedergabe_s": 7.0, "voll_prozent": 34.0})
        self.assertIsInstance(werte["views"], int)
        self.assertIsInstance(werte["wiedergabe_s"], float)

    def test_zaehler_mit_nachkommastellen(self):
        with self.assertRaisesRegex(ValueError, "Views: 12.5 ist keine ganze Zahl"):
            screenshot.normalisiere({"views": 12.5})

    def test_nan_und_unendlich(self):
        for wert in (math.nan, math.inf):
            for feld in ("views", "wiedergabe_s"):
                with self.subTest(feld=feld, wert=wert), self.assertRaisesRegex(ValueError, "keine endliche Zahl"):
                    screenshot.normalisiere({feld: wert})

    def test_leere_antwort(self):
        self.assertEqual(screenshot.normalisiere({}), {feld: None for feld in publikum.FELDER})

    def test_zusatzfeld_wird_ignoriert_und_nur_mit_namen_geloggt(self):
        with self.assertLogs("pipeline", level="INFO") as logs:
            werte = screenshot.normalisiere({"views": 900, "profilaufrufe": 4711})
        self.assertEqual(werte["views"], 900)
        self.assertNotIn("profilaufrufe", werte)
        self.assertIn("profilaufrufe", "\n".join(logs.output))
        self.assertNotIn("4711", "\n".join(logs.output))

    def test_voll_prozent_ueber_100_geht_durch_schema_und_normalisiere(self):
        # Die Rückfrage macht publikum.pruefe_plausibel – hier wird nichts abgelehnt (Spec §15: tolerant)
        self.assertEqual(schema.pruefe({"voll_prozent": 120}, schema.lade("publikum")), [])
        self.assertEqual(screenshot.normalisiere({"voll_prozent": 120})["voll_prozent"], 120.0)
        self.assertEqual(schema.pruefe({"views": 1, "extra": "x"}, schema.lade("publikum")), [])


# --- screenshot.lies_zahlen ------------------------------------------------------------------------------------

class LiesZahlen(MitClaudeKonfig):
    def bild(self, endung: str = ".jpg") -> Path:
        pfad = self.tmp / f"eingang{endung}"
        pfad.write_bytes(b"\x89PNG" if endung == ".png" else b"\xff\xd8\xff")
        return pfad

    def test_liest_und_raeumt_auf(self):
        fake = FakeClaude()
        bild = self.bild()
        with fake.aktiv():
            lesung = screenshot.lies_zahlen(self.konfig, bild)
        self.assertEqual(lesung.werte, GUELTIG)
        self.assertIsNone(lesung.hinweis)
        self.assertEqual(lesung.roh, json.dumps(GUELTIG))
        self.assertEqual(lesung.claude, ClaudeAntwort(GUELTIG, None, json.dumps(GUELTIG), gestartet=True))
        # Claude sah nur die Kopie „screenshot.jpg“ in einem eigenen Ordner, der danach weg ist
        aufruf = fake.aufrufe[0]
        self.assertEqual(aufruf["dateien"], ["screenshot.jpg"])
        self.assertIn("Bilddatei screenshot.jpg", aufruf["befehl"][-1])
        self.assertEqual(aufruf["kw"]["timeout"], 120.0)
        self.assertFalse(aufruf["ordner"].exists())
        self.assertEqual(self.temp_reste(), [])
        self.assertTrue(bild.exists())  # das Original löscht der Aufrufer

    def test_bild_fehlt_ist_hinweis_ohne_aufruf(self):
        # Startauftrag §5 „fehlende Dateien“: lies_zahlen wirft nie – auch nicht, wenn das Bild weg ist
        with FakeClaude().aktiv() as (lauf, _):
            lesung = screenshot.lies_zahlen(self.konfig, self.tmp / "weg.jpg")
        self.assertEqual((lesung.werte, lesung.claude), (None, None))
        self.assertEqual(lesung.hinweis, "Bild nicht lesbar (FileNotFoundError)")
        lauf.assert_not_called()
        self.assertEqual(self.temp_reste(), [])   # auch der Temp-Ordner ist weg

    def test_png_bleibt_png(self):
        fake = FakeClaude()
        with fake.aktiv():
            screenshot.lies_zahlen(self.konfig, self.bild(".png"))
        self.assertEqual(fake.aufrufe[0]["dateien"], ["screenshot.png"])
        self.assertIn("Bilddatei screenshot.png", fake.aufrufe[0]["befehl"][-1])

    def test_fehler_raeumt_auch_auf(self):
        for fake in (FakeClaude(returncode=2), FakeClaude(fehler=subprocess.TimeoutExpired(["claude"], 1)),
                     FakeClaude("nichts")):
            with self.subTest(fake=fake.result), fake.aktiv():
                lesung = screenshot.lies_zahlen(self.konfig, self.bild())
            self.assertIsNone(lesung.werte)
            self.assertTrue(lesung.hinweis)
            self.assertIsNotNone(lesung.claude)  # Claude wurde gefragt – das zählt mit
            self.assertEqual(self.temp_reste(), [])

    def test_nan_von_claude_wird_hinweis(self):
        # json.loads nimmt „NaN“ an, das Schema auch (number) – erst normalisiere lehnt es ab
        with FakeClaude('{"views": NaN, "likes": 3}').aktiv():
            lesung = screenshot.lies_zahlen(self.konfig, self.bild())
        self.assertIsNone(lesung.werte)
        self.assertEqual(lesung.hinweis, "Zahlen unbrauchbar: Views: nan ist keine endliche Zahl")
        self.assertEqual(lesung.roh, '{"views": NaN, "likes": 3}')
        self.assertEqual(lesung.claude.hinweis, lesung.hinweis)
        self.assertIsNone(lesung.claude.daten)
        self.assertEqual(self.temp_reste(), [])

    def test_unbekanntes_format_ohne_aufruf(self):
        fake = FakeClaude()
        with fake.aktiv() as (lauf, _):
            lesung = screenshot.lies_zahlen(self.konfig, self.bild(".heic"))
        self.assertEqual((lesung.werte, lesung.claude), (None, None))
        self.assertEqual(lesung.hinweis, "Bildformat .heic kann ich nicht lesen")
        lauf.assert_not_called()

    def test_fehlende_einstellung_oder_vorlage_ohne_aufruf(self):
        lernbot = self.konfig.daten["lernbot"]
        richtig = dict(lernbot)
        for aendern in (lambda: lernbot.pop("screenshot_timeout_s"),
                        lambda: lernbot.update(screenshot_prompt=str(self.tmp / "fehlt.txt"))):
            lernbot.clear()
            lernbot.update(richtig)  # je Runde nur EIN Fehler
            aendern()
            fake = FakeClaude()
            with fake.aktiv() as (lauf, _):
                lesung = screenshot.lies_zahlen(self.konfig, self.bild())
            self.assertIsNone(lesung.werte)
            self.assertIn("nicht eingerichtet", lesung.hinweis)
            self.assertIsNone(lesung.claude)
            lauf.assert_not_called()
            self.assertEqual(self.temp_reste(), [])

    def test_kein_temp_ordner_ist_hinweis_statt_ausnahme(self):
        with FakeClaude().aktiv() as (lauf, _), \
                mock.patch.object(tempfile, "mkdtemp", side_effect=OSError(28, "No space left on device")):
            lesung = screenshot.lies_zahlen(self.konfig, self.bild())
        self.assertEqual(lesung.hinweis, "Kein Temp-Ordner für die Auswertung (OSError)")
        self.assertIsNone(lesung.claude)
        lauf.assert_not_called()

    def test_ohne_claude_programm(self):
        # Sicherheitsnetz aus setUp: ohne Fake gibt es kein claude – Hinweis statt Ausnahme
        lesung = screenshot.lies_zahlen(self.konfig, self.bild())
        self.assertEqual(lesung.hinweis, "claude nicht gefunden")
        self.assertEqual(self.temp_reste(), [])


class Einstellung(MitClaudeKonfig):
    def test_fehlender_schluessel_ist_konfigfehler(self):
        from clip_pipeline.konfig import KonfigFehler

        del self.konfig.daten["lernbot"]["screenshot_claude"]
        with self.assertRaisesRegex(KonfigFehler, r"\[lernbot\]\.screenshot_claude"):
            screenshot.einstellung(self.konfig, "screenshot_claude")
        self.assertEqual(screenshot.einstellung(self.konfig, "screenshot_timeout_s"), 120)


if __name__ == "__main__":
    import unittest
    unittest.main()
