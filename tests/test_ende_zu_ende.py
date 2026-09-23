"""Ganzer Ablauf nach dem n8n-Vertrag mit echtem FFmpeg: prepare -> analyze -> decide -> render.

Das Replay wird durch ein erfundenes Match ersetzt (kein .replay-Inhalt nötig);
das echte replay2json ist an eigenen Replays geprüft (siehe README).
"""

import contextlib
import io
import json
import os
import subprocess
import time
import unittest
from datetime import datetime, timedelta
from unittest import mock

from clip_pipeline import cli, erfassung, medien, schema, verarbeitung
from clip_pipeline.replay import Match, MeinEreignis
from clip_pipeline.zeit import UTC, aus_iso, utc_zu_lokal

from tests.hilfen import HAT_FFMPEG, MitSpeicher, testvideo

SID = "2026-09-21_21-42-22"


def mit_video_und_replay(test: MitSpeicher) -> list[datetime]:
    """Legt ein Nvidia-Highlight (20 s) und das Replay an; gibt zwei Kill-Zeitpunkte (Sekunde 8 und 11) zurück."""
    ende = datetime(2026, 9, 21, 19, 53, 34, tzinfo=UTC)
    name = utc_zu_lokal(ende, "Europe/Berlin").strftime("Fortnite %Y.%m.%d - %H.%M.%S.22.Doppeleliminierung.DVR.mp4")
    video = testvideo(test.konfig.ordner("eingang") / "nvidia" / name, dauer=20, tonspuren=2, creation_time=ende)
    replay = test.konfig.ordner("replays") / "UnsavedReplay-2026.09.21-21.42.22.replay"
    replay.write_bytes(b"x")
    alt = time.time() - 600
    for pfad in (video, replay):
        os.utime(pfad, (alt, alt))
    erfassung.erfasse_aufnahmen(test.con, test.konfig)
    aufnahme = test.con.execute("SELECT start_utc, ende_utc FROM aufnahmen").fetchone()
    test.assertEqual(aufnahme["ende_utc"], "2026-09-21T19:53:35.400Z")  # creation_time + Versatz 1,4 s
    start = aus_iso(aufnahme["start_utc"])
    return [start + timedelta(seconds=8), start + timedelta(seconds=11)]


def match_mit(kills: list[datetime]) -> Match:
    ende = kills[-1] + timedelta(minutes=3)
    return Match(SID, ende - timedelta(minutes=15), ende, "Test", 3, len(kills), "argument",
                 [MeinEreignis(z, "kill") for z in kills])


@unittest.skipUnless(HAT_FFMPEG, "ffmpeg fehlt")
class Vertragsablauf(MitSpeicher):
    def test_vier_schritte(self):
        kills = mit_video_und_replay(self)
        self.konfig.daten["decide"]["claude"] = False  # Regel-Schnittliste

        vorbereitet = verarbeitung.prepare(self.con, self.konfig, SID)
        self.assertEqual((vorbereitet["replay"], vorbereitet["aufnahmen_im_match"]),
                         ("replays/UnsavedReplay-2026.09.21-21.42.22.replay", 1))
        with mock.patch("clip_pipeline.replay.lies", return_value=(match_mit(kills), {"test": True})):
            analyse = verarbeitung.analyze(self.con, self.konfig, SID)
        self.assertEqual((analyse["kills"], analyse["kandidaten"], analyse["kill_quelle"]), (2, 1, "replay"))

        entschieden = verarbeitung.decide(self.con, self.konfig, SID)
        self.assertEqual((entschieden["clips"], entschieden["entschieden_von"]), (1, "regel"))
        liste = json.loads((verarbeitung.ordner(self.konfig, SID) / "schnittliste.json").read_text(encoding="utf-8"))
        self.assertEqual(schema.pruefe(liste, schema.lade("schnittliste")), [])
        self.assertEqual((liste["clips"][0]["kill_sekunden"], liste["clips"][0]["start_s"], liste["clips"][0]["ende_s"]),
                         ([8.0, 11.0], 0.0, 16.0))

        ergebnis = verarbeitung.render(self.con, self.konfig, SID)
        self.assertEqual((ergebnis["clips"], ergebnis["top_label"]), (1, "Double Kill"))
        self.assertGreaterEqual(ergebnis["top_score"], 3)
        clip = self.con.execute("SELECT * FROM clips").fetchone()
        self.assertEqual((clip["status"], clip["max_gruppe"]), ("vorbewertet", 2))
        self.assertTrue(clip["clip_pfad"].startswith(f"sessions/{SID}/clips/"))
        info = medien.probe(self.konfig.absolut(clip["clip_pfad"]))
        self.assertEqual((len(info.tonspuren), info.vfr, round(info.fps)), (2, False, 30))  # beide Spuren, feste Rate
        self.assertAlmostEqual(info.dauer_s, 16.0, delta=0.2)
        vorschau = medien.probe(self.konfig.absolut(clip["vorschau_pfad"]))
        self.assertEqual(len(vorschau.tonspuren), 1)  # für Telegram gemischt

        # idempotent: alles nochmal -> nichts doppelt
        self.assertTrue(verarbeitung.decide(self.con, self.konfig, SID)["uebersprungen"])
        self.assertEqual(verarbeitung.render(self.con, self.konfig, SID)["neu"], 0)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM clips").fetchone()[0], 1)


class Entscheidung(MitSpeicher):
    def _analyse(self) -> None:
        verarbeitung.ordner(self.konfig, SID).mkdir(parents=True)
        (verarbeitung.ordner(self.konfig, SID) / "analyse.json").write_text(json.dumps({
            "version": 1, "session": SID, "warnungen": [], "ohne_video": [],
            "match": {"replay": None, "build": None, "platzierung": 3, "victory_royale": False, "kills": 5, "kill_quelle": "replay"},
            "kandidaten": [
                {"nr": n, "titel": "Double Kill", "typ": "double", "kills": 2, "max_gruppe": 2, "victory_royale": False,
                 "kill_zeiten_utc": ["2026-09-21T19:53:24.974Z", "2026-09-21T19:53:29.453Z"], "kill_sekunden": [30.0, 34.0],
                 "aufnahme": "eingang/x.mp4", "quelle": "steelseries", "abdeckung": 1.0,
                 "vorschlag": {"start_s": 22.0, "ende_s": 39.0}, "grenzen": {"min_start_s": 0.0, "max_ende_s": 120.0},
                 "merkmale": {"kill_punkte": 3.0, "victory_royale": 0.0, "laenge": 0.0, "lautstaerke": 0.0, "kommentar": 0.0},
                 "punkte": 3.0, "begruendung": "Double Kill 3,0"}
                for n in (1, 2, 3)
            ],
        }), encoding="utf-8")

    def test_claude_wird_geprueft(self):
        self._analyse()
        antwort = {
            1: {"nr": 1, "start_s": 25.0, "ende_s": 37.0, "beschreibung": "Double in 4 Sekunden 🔥", "grund": "enger"},
            2: {"nr": 2, "start_s": 31.0, "ende_s": 40.0},  # schneidet den ersten Kill ab -> verworfen
            3: {"nr": 3, "start_s": 22.0, "ende_s": 39.0, "beschreibung": "7 Kills mit der Pumpgun"},  # erfundene Zahl
        }
        with mock.patch.object(verarbeitung, "frage_claude", return_value=(antwort, None)):
            ergebnis = verarbeitung.decide(self.con, self.konfig, SID)
        self.assertEqual(ergebnis["entschieden_von"], "claude")
        liste = json.loads((verarbeitung.ordner(self.konfig, SID) / "schnittliste.json").read_text(encoding="utf-8"))
        c1, c2, c3 = liste["clips"]
        self.assertEqual((c1["start_s"], c1["ende_s"], c1["beschreibung"]), (25.0, 37.0, "Double in 4 Sekunden 🔥"))
        self.assertEqual((c2["start_s"], c2["ende_s"]), (22.0, 39.0))  # Vorschlag bleibt
        self.assertIsNone(c3["beschreibung"])
        self.assertEqual(len(ergebnis["hinweise"]), 2)

    def test_claude_ausgabe_parsen_und_schema(self):
        self._analyse()
        huelle = {"is_error": False, "result": 'Hier: {"clips": [{"nr": 1, "start_s": 24, "ende_s": 38}]}'}
        fertig = subprocess.CompletedProcess([], 0, stdout=json.dumps(huelle), stderr="")
        with mock.patch("shutil.which", return_value="claude"), mock.patch("subprocess.run", return_value=fertig) as lauf:
            antwort, hinweis = verarbeitung.frage_claude(self.konfig, verarbeitung.ordner(self.konfig, SID))
        self.assertEqual((antwort[1]["start_s"], hinweis), (24, None))
        aufruf = lauf.call_args
        self.assertEqual(aufruf.args[0][1:6], ["-p", "--output-format", "json", "--allowedTools", "Read"])
        self.assertEqual(aufruf.kwargs["cwd"], verarbeitung.ordner(self.konfig, SID))

        falsch = subprocess.CompletedProcess([], 0, stdout=json.dumps({"result": '{"clips": [{"nr": 1, "start_s": 1, "ende_s": 9, "rm": 1}]}'}), stderr="")
        with mock.patch("shutil.which", return_value="claude"), mock.patch("subprocess.run", return_value=falsch):
            antwort, hinweis = verarbeitung.frage_claude(self.konfig, verarbeitung.ordner(self.konfig, SID))
        self.assertIsNone(antwort)
        self.assertIn("Schema", hinweis)


class Vertrag(MitSpeicher):
    def _cli(self, *argv) -> tuple[int, dict]:
        umgebung = {"CLIP_SPEICHER": str(self.konfig.wurzel), "CLIP_DATENBANK": str(self.konfig.datenbank)}
        ausgabe = io.StringIO()
        with mock.patch.dict(os.environ, umgebung), contextlib.redirect_stdout(ausgabe), \
                contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(list(argv))
        return code, json.loads(ausgabe.getvalue().strip().splitlines()[-1])  # letzte Zeile = JSON

    def test_session_id_wird_geprueft(self):
        code, daten = self._cli("prepare", "--session", "../../etc")
        self.assertEqual(code, 1)
        self.assertIn("Ungültige Session-ID", daten["fehler"])

    def test_fehlendes_replay_ist_fehler_mit_json(self):
        code, daten = self._cli("prepare", "--session", "2026-01-01_00-00-00")
        self.assertEqual(code, 1)
        self.assertIn("Kein Replay", daten["fehler"])

    def test_speicher_offline(self):
        (self.konfig.wurzel / ".clip-speicher").unlink()
        code, daten = self._cli("render", "--session", SID)
        self.assertEqual((code, daten["fehler"]), (3, "speicher_offline"))


if __name__ == "__main__":
    unittest.main()
