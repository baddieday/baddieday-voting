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

from clip_pipeline import cli, db, erfassung, medien, schema, verarbeitung
from clip_pipeline.replay import Match, MeinEreignis
from clip_pipeline.zeit import UTC, aus_iso, iso, jetzt, utc_zu_lokal

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

        k = json.loads((verarbeitung.ordner(self.konfig, SID) / "analyse.json").read_text(encoding="utf-8"))["kandidaten"][0]
        self.assertEqual((k["aktion_sekunden"], k["grenzen"]["spaetester_start_s"]), ([8.0, 11.0], 6.0))  # ohne Umhauen

        entschieden = verarbeitung.decide(self.con, self.konfig, SID)
        self.assertEqual((entschieden["clips"], entschieden["entschieden_von"]), (1, "regel"))
        liste = json.loads((verarbeitung.ordner(self.konfig, SID) / "schnittliste.json").read_text(encoding="utf-8"))
        self.assertEqual(schema.pruefe(liste, schema.lade("schnittliste")), [])
        self.assertEqual((liste["clips"][0]["kill_sekunden"], liste["clips"][0]["start_s"], liste["clips"][0]["ende_s"]),
                         ([8.0, 11.0], 0.0, 16.0))
        self.assertEqual(liste["clips"][0]["aktion_sekunden"], [8.0, 11.0])

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

        self.assertEqual((ergebnis["ohne_video"], ergebnis["warnung"]), (0, None))

        # idempotent: alles nochmal -> nichts doppelt
        self.assertTrue(verarbeitung.decide(self.con, self.konfig, SID)["uebersprungen"])
        self.assertEqual(verarbeitung.render(self.con, self.konfig, SID)["neu"], 0)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM clips").fetchone()[0], 1)


class OhneVideo(MitSpeicher):
    def test_warnung_bei_frischem_match_genau_einmal(self):
        from clip_pipeline.zeit import iso, jetzt

        ende = jetzt() - timedelta(minutes=20)
        self.con.execute(
            "INSERT INTO matches (id, replay_pfad, start_utc, ende_utc, erstellt, geaendert) VALUES (?, 'r', ?, ?, 'x', 'x')",
            (SID, iso(ende - timedelta(minutes=15)), iso(ende)),
        )
        verarbeitung.ordner(self.konfig, SID).mkdir(parents=True)
        (verarbeitung.ordner(self.konfig, SID) / "schnittliste.json").write_text(json.dumps({
            "clips": [], "ohne_video": [{"nr": 1, "titel": "Double Kill", "kill_zeiten_utc": ["a", "b"]}],
        }), encoding="utf-8")
        ergebnis = verarbeitung.render(self.con, self.konfig, SID)
        self.assertEqual(ergebnis["ohne_video"], 2)
        self.assertIn("Nvidia", ergebnis["warnung"])
        verarbeitung.render(self.con, self.konfig, SID)  # nochmal -> keine zweite Meldung
        meldungen = self.con.execute("SELECT text FROM meldungen").fetchall()
        self.assertEqual(len(meldungen), 1)
        self.assertIn("2 Kill(s) ohne Aufnahme", meldungen[0]["text"])


class Anlauf(MitSpeicher):
    """analyze mit Aktions-Zeitpunkten (Team-Wipe) – ohne FFmpeg: die Aufnahme steht nur in der Datenbank."""

    T0 = datetime(2026, 9, 21, 19, 50, tzinfo=UTC)

    def _sekunde(self, s: float) -> datetime:
        return self.T0 + timedelta(seconds=s)

    def setUp(self):
        super().setUp()
        self.konfig.daten["decide"]["claude"] = False
        self.con.execute(
            "INSERT INTO matches (id, replay_pfad, start_utc, ende_utc, erstellt, geaendert) VALUES (?, 'replays/x.replay', ?, ?, 'x', 'x')",
            (SID, iso(self.T0 - timedelta(minutes=5)), iso(self.T0 + timedelta(minutes=5))))
        self.con.execute(
            """INSERT INTO aufnahmen (pfad, groesse, geaendert, quelle, start_utc, ende_utc, dauer_s, tonspuren, fps, erfasst)
               VALUES ('eingang/steelseries/a.mp4', 1, 0, 'steelseries', ?, ?, 120, 2, 30, 'x')""",
            (iso(self.T0), iso(self._sekunde(120))))
        e = self._sekunde
        self.match = Match(SID, self.T0 - timedelta(minutes=5), self.T0 + timedelta(minutes=5), "Test", 3, 5, "argument", [
            # Team-Wipe: drei Kills fast gleichzeitig, umgehauen habe ich 15 s, 4,6 s und 0 s vorher
            MeinEreignis(e(60), "kill", aktion_utc=e(45)), MeinEreignis(e(60), "kill", aktion_utc=e(55.4)),
            MeinEreignis(e(60.5), "kill", aktion_utc=e(60.5)),
            # Double, erstes Umhauen 70 s vor dem Kill -> Anlauf wird auf 60 s begrenzt
            MeinEreignis(e(100), "kill", aktion_utc=e(30)), MeinEreignis(e(104), "kill"),
        ])

    def test_analyse_und_schnittliste_mit_aktionen(self):
        with mock.patch("clip_pipeline.replay.lies", return_value=(self.match, {"test": True})), \
                self.assertLogs("pipeline", "WARNING"):
            verarbeitung.analyze(self.con, self.konfig, SID)
        analyse = json.loads((verarbeitung.ordner(self.konfig, SID) / "analyse.json").read_text(encoding="utf-8"))
        wipe, double = analyse["kandidaten"]
        self.assertEqual((wipe["titel"], wipe["kill_sekunden"], wipe["aktion_sekunden"]),
                         ("Triple Kill", [60.0, 60.0, 60.5], [45.0, 55.4, 60.5]))
        self.assertEqual((wipe["vorschlag"], wipe["grenzen"]["spaetester_start_s"]),
                         ({"start_s": 37.0, "ende_s": 65.5}, 43.0))  # Beginn 8 s vor dem ersten Umhauen
        self.assertEqual((wipe["merkmale"]["kill_punkte"], wipe["merkmale"]["laenge"]), (6.0, 0.0))
        self.assertEqual((double["titel"], double["aktion_sekunden"], double["vorschlag"]),
                         ("Double Kill", [30.0, 104.0], {"start_s": 49.0, "ende_s": 109.0}))  # auf 60 s begrenzt
        self.assertEqual(double["grenzen"]["spaetester_start_s"], 49.0)
        self.assertTrue(any("max. 60 s" in w for w in analyse["warnungen"]))

        verarbeitung.decide(self.con, self.konfig, SID)
        liste = json.loads((verarbeitung.ordner(self.konfig, SID) / "schnittliste.json").read_text(encoding="utf-8"))
        self.assertEqual(schema.pruefe(liste, schema.lade("schnittliste")), [])
        self.assertEqual([(c["start_s"], c["aktion_sekunden"]) for c in liste["clips"]],
                         [(37.0, [45.0, 55.4, 60.5]), (49.0, [30.0, 104.0])])
        self.assertEqual([c["merkmale"]["laenge"] for c in liste["clips"]], [0.0, 0.0])

    def test_spaetester_start(self):
        sp = verarbeitung.spaetester_start
        self.assertEqual(sp(0.0, [60.0, 60.0, 60.5], [45.0, 55.4, 60.5], 37.0), 43.0)  # 2 s vor der ersten Aktion
        self.assertEqual(sp(0.0, [5.0], [-10.0], 0.0), 0.0)                              # Aktion vor der Aufnahme
        self.assertEqual(sp(0.0, [100.0, 104.0], [30.0, 104.0], 49.0), 49.0)             # auf 60 s gekappt
        self.assertEqual(sp(0.0, [10.0, 80.0], [10.0, 80.0], 2.0), 8.0)                  # nie nach erstem Kill − 2


class PruefeWahl(unittest.TestCase):
    NEU = {"kill_sekunden": [30.0, 34.0], "aktion_sekunden": [18.0, 34.0],
           "grenzen": {"min_start_s": 0.0, "max_ende_s": 120.0, "spaetester_start_s": 16.0}}
    ALT = {"kill_sekunden": [30.0, 34.0], "grenzen": {"min_start_s": 0.0, "max_ende_s": 120.0}}

    def test_neu_gegen_die_erste_aktion(self):
        self.assertIsNone(verarbeitung.pruefe_wahl({"start_s": 12, "ende_s": 36}, self.NEU))  # vor der Aktion
        self.assertIsNone(verarbeitung.pruefe_wahl({"start_s": 16, "ende_s": 36}, self.NEU))  # genau auf der Grenze
        self.assertEqual(verarbeitung.pruefe_wahl({"start_s": 20, "ende_s": 36}, self.NEU), "schneidet einen Kill ab")
        self.assertEqual(verarbeitung.pruefe_wahl({"start_s": 10, "ende_s": 34.5}, self.NEU), "schneidet einen Kill ab")
        self.assertEqual(verarbeitung.pruefe_wahl({"start_s": 0, "ende_s": 61}, self.NEU), "Dauer nicht 5–60 s")

    def test_alte_analyse_wie_bisher(self):
        self.assertIsNone(verarbeitung.pruefe_wahl({"start_s": 20, "ende_s": 36}, self.ALT))  # 2 s vor dem Kill reicht
        self.assertIsNone(verarbeitung.pruefe_wahl({"start_s": 28, "ende_s": 36}, self.ALT))
        self.assertEqual(verarbeitung.pruefe_wahl({"start_s": 29, "ende_s": 36}, self.ALT), "schneidet einen Kill ab")


class Entscheidung(MitSpeicher):
    def _analyse(self, mit_aktion: bool = False) -> None:
        verarbeitung.ordner(self.konfig, SID).mkdir(parents=True)
        neu = {"aktion_sekunden": [18.0, 34.0]} if mit_aktion else {}
        grenzen = {"min_start_s": 0.0, "max_ende_s": 120.0, **({"spaetester_start_s": 16.0} if mit_aktion else {})}
        (verarbeitung.ordner(self.konfig, SID) / "analyse.json").write_text(json.dumps({
            "version": 1, "session": SID, "warnungen": [], "ohne_video": [],
            "match": {"replay": None, "build": None, "platzierung": 3, "victory_royale": False, "kills": 5, "kill_quelle": "replay"},
            "kandidaten": [
                {"nr": n, "titel": "Double Kill", "typ": "double", "kills": 2, "max_gruppe": 2, "victory_royale": False,
                 "kill_zeiten_utc": ["2026-09-21T19:53:24.974Z", "2026-09-21T19:53:29.453Z"], "kill_sekunden": [30.0, 34.0],
                 **neu,
                 "aufnahme": "eingang/x.mp4", "quelle": "steelseries", "abdeckung": 1.0,
                 "vorschlag": {"start_s": 10.0 if mit_aktion else 22.0, "ende_s": 39.0}, "grenzen": grenzen,
                 "merkmale": {"kill_punkte": 3.0, "victory_royale": 0.0, "laenge": 0.0, "lautstaerke": 0.0, "kommentar": 0.0},
                 "punkte": 3.0, "begruendung": "Double Kill 3,0"}
                for n in (1, 2, 3)
            ],
        }), encoding="utf-8")

    def _schnittliste(self) -> dict:
        return json.loads((verarbeitung.ordner(self.konfig, SID) / "schnittliste.json").read_text(encoding="utf-8"))

    def test_claude_schnitt_vor_der_aktion(self):
        self._analyse(mit_aktion=True)
        antwort = {
            1: {"nr": 1, "start_s": 12.0, "ende_s": 37.0},  # vor dem ersten Umhauen -> gut
            2: {"nr": 2, "start_s": 20.0, "ende_s": 37.0},  # zwischen Umhauen und Kill -> verworfen
            3: {"nr": 3, "start_s": 0.0, "ende_s": 60.0},   # langer Anlauf: Länge zählt erst ab Kill − 8 s
        }
        with mock.patch.object(verarbeitung, "frage_claude", return_value=(antwort, None)):
            ergebnis = verarbeitung.decide(self.con, self.konfig, SID)
        c1, c2, c3 = self._schnittliste()["clips"]
        self.assertEqual((c1["start_s"], c1["ende_s"], c1["aktion_sekunden"]), (12.0, 37.0, [18.0, 34.0]))
        self.assertEqual((c2["start_s"], c2["ende_s"]), (10.0, 39.0))  # Vorschlag bleibt
        self.assertIn("schneidet einen Kill ab", ergebnis["hinweise"][0])
        self.assertEqual(c3["merkmale"]["laenge"], 0.8)  # 60 − 22 = 38 s -> 0,8 (nicht 3,0)
        self.assertEqual(schema.pruefe(self._schnittliste(), schema.lade("schnittliste")), [])

    def test_alte_analyse_ohne_aktion_wie_bisher(self):
        self._analyse()
        antwort = {1: {"nr": 1, "start_s": 20.0, "ende_s": 37.0}, 3: {"nr": 3, "start_s": 0.0, "ende_s": 60.0}}
        with mock.patch.object(verarbeitung, "frage_claude", return_value=(antwort, None)):
            ergebnis = verarbeitung.decide(self.con, self.konfig, SID)
        c1, c2, c3 = self._schnittliste()["clips"]
        self.assertEqual((c1["start_s"], "aktion_sekunden" in c1), (20.0, False))
        self.assertEqual((c2["start_s"], c2["merkmale"]["laenge"]), (22.0, 0.0))
        self.assertEqual(c3["merkmale"]["laenge"], 3.0)  # wie bisher: ganze Dauer zählt
        self.assertEqual(ergebnis["hinweise"], [])

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


class MatchEnde(MitSpeicher):
    """Match-Ende = letzte Änderung des Replays, auch wenn prepare erst Stunden später läuft (Rückstand)."""

    def _replay_und_aufnahmen(self) -> tuple[str, datetime]:
        zone = self.konfig.wert("zeit.zeitzone", "Europe/Berlin")
        ende = (jetzt() - timedelta(hours=5)).replace(microsecond=0)
        start_lokal = utc_zu_lokal(ende - timedelta(minutes=20), zone)
        replay = self.konfig.ordner("replays") / start_lokal.strftime("UnsavedReplay-%Y.%m.%d-%H.%M.%S.replay")
        replay.write_bytes(b"x")
        os.utime(replay, (ende.timestamp(), ende.timestamp()))
        for name, beginn in (("im-match", ende - timedelta(minutes=5)), ("spaeter", ende + timedelta(hours=3))):
            self.con.execute(
                """INSERT INTO aufnahmen (pfad, groesse, geaendert, quelle, start_utc, ende_utc, dauer_s, tonspuren, erfasst)
                   VALUES (?, 1, 0, 'steelseries', ?, ?, 60, 2, 'x')""",
                (f"eingang/{name}.mp4", iso(beginn), iso(beginn + timedelta(seconds=60))),
            )
        return start_lokal.strftime("%Y-%m-%d_%H-%M-%S"), ende

    def test_prepare_nimmt_mtime_statt_jetzt(self):
        sid, ende = self._replay_und_aufnahmen()
        vorbereitet = verarbeitung.prepare(self.con, self.konfig, sid)
        self.assertEqual(db.match(self.con, sid)["ende_utc"], iso(ende))
        self.assertEqual(vorbereitet["aufnahmen_im_match"], 1)  # die Aufnahme 3 h später gehört nicht dazu

    def test_scan_rechnet_gleich(self):
        sid, ende = self._replay_und_aufnahmen()
        self.assertEqual(erfassung.erfasse_replays(self.con, self.konfig), [sid])
        self.assertEqual(db.match(self.con, sid)["ende_utc"], iso(ende))


if __name__ == "__main__":
    unittest.main()
