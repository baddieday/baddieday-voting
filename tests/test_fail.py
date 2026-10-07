"""Fail-Momente (fail.py, 05.10.): je eigenem Tod ein Moment aus dem Rohvideo im Puffer, Fakten und Fail-Score.

Replay (anonymisiert): 10 Spieler, fünf fremde finale Eliminierungen, dann mein Triple Kill (101/104/108 s), ich werde
bei 118 s umgehauen und bei 120 s erledigt – Fortnite zeigt Platz 2 (Replay ich.platzierung).
"""

import json
import unittest
from datetime import timedelta

from clip_pipeline import fail, replay
from clip_pipeline.zeit import iso, jetzt

from tests.hilfen import HAT_FFMPEG, testvideo
from tests.test_nachschnitt import MitPuffer

SITZUNG = "2026-10-04_21-00-00"
QUELLE = "eingang/steelseries/Fortnite__2026-10-04__21-02-00.mp4"
FREMDE = [{"t_ms": t * 1000, "eliminator": f"X{i}", "eliminiert": f"Y{i}", "knock": False}
          for i, t in enumerate((50, 55, 60, 65, 70), 1)]
TRIPLE = [{"t_ms": 100_000, "eliminator": "ICH", "eliminiert": "G1", "knock": True, "selbst": False},
          {"t_ms": 101_000, "eliminator": "ICH", "eliminiert": "G1", "knock": False, "selbst": False},
          {"t_ms": 104_000, "eliminator": "ICH", "eliminiert": "G2", "knock": False, "selbst": False},
          {"t_ms": 108_000, "eliminator": "ICH", "eliminiert": "G3", "knock": False, "selbst": False}]
TOD = [{"t_ms": 118_000, "eliminator": "P9", "eliminiert": "ICH", "knock": True, "selbst": False,
        "eliminator_bot": False, "waffe": 5},
       {"t_ms": 120_000, "eliminator": "P9", "eliminiert": "ICH", "knock": False, "selbst": False,
        "eliminator_bot": False, "waffe": 7}]


def roh(eliminierungen, start=None) -> dict:
    return {"replay_start": iso(start or jetzt().replace(microsecond=0) - timedelta(hours=1)),
            "replay_start_kind": "Utc", "laenge_ms": 600_000, "spieler_gesamt": 10, "ich": {"epic_id": "ICH", "platzierung": 2},
            "eliminierungen": eliminierungen}


def match(eliminierungen) -> replay.Match:
    return replay.match_aus_json(roh(eliminierungen), SITZUNG, zonen_name="Europe/Berlin")


class Fakten(MitPuffer):
    def test_tod_auf_platz_2_nach_triple_kill(self):
        tode = fail.tode(match(FREMDE + TRIPLE + TOD), self.konfig)
        self.assertEqual(len(tode), 1)
        f = tode[0]
        self.assertEqual((f["platz"], f["verbleibend"], f["kills_vorher_30s"], f["sekunde"]), (2, 1, 3, 120))
        self.assertEqual((f["killer_bot"], f["selbst"], f["knock_erlitten"], f["waffe_gegner"]), (False, False, True, 5))
        mk = fail.neu_bewerten({k: v for k, v in f.items() if k != "tod_utc"}, self.konfig)
        # Fallhöhe: Platz ≤ 10 (2) + Platz ≤ 3 (3) + 3 Kills davor (3) + vorher umgehauen (0,5)
        self.assertEqual(mk["fail_score"], 8.5)
        self.assertEqual(mk["fail_titel"], "PLATZ 2")
        self.assertEqual(mk["fail_gruende"], ["Platz 2", "3 Kills davor"])

    def test_platz_wie_fortnite_auch_fuer_alte_momente(self):
        # Squad: 30 Spieler übrig, Fortnite zeigt aber Platz 4 – die alte Rechnung (übrige + 1) gab „PLATZ 31“
        self.con.execute("INSERT INTO matches (id, replay_pfad, start_utc, ende_utc, platzierung, erstellt, geaendert)"
                         " VALUES (?, 'r', 'x', 'x', 4, 'x', 'x')", (SITZUNG,))
        alt = {"fail": True, "platz": 31, "verbleibend": 30, "kills_vorher_30s": 0}
        for sek in (100, 300):                             # 100 s: Tod vor dem Reboot, 300 s: der letzte Tod
            self.con.execute("INSERT INTO momente (schluessel, match_id, datei, start_s, ende_s, stimmung, sicherheit,"
                             " quelle, merkmale, erstellt, geaendert)"
                             " VALUES (?, ?, '/x.mp4', 0, 15, 'frustriert', 1, 'regel', ?, 'x', 'x')",
                             (f"fail:{SITZUNG}:{sek}", SITZUNG, json.dumps(alt)))
        plaetze = fail.plaetze(self.con)
        self.assertEqual(plaetze, {f"fail:{SITZUNG}:100": None, f"fail:{SITZUNG}:300": 4})
        self.assertEqual(fail.mit_platz(alt, f"fail:{SITZUNG}:300", plaetze)["fail_titel"], "PLATZ 4")
        self.assertIsNone(fail.mit_platz(alt, f"fail:{SITZUNG}:100", plaetze)["fail_titel"])

    def test_victory_ohne_tod_kein_fail(self):
        self.assertEqual(fail.tode(match(FREMDE + TRIPLE), self.konfig), [])

    def test_altes_json_ohne_neue_felder(self):
        alt = [{k: v for k, v in e.items() if k not in ("selbst", "eliminator_bot")} for e in FREMDE + TRIPLE + TOD]
        m = match(alt)
        tod = next(e for e in m.ereignisse if e.art == "tod")
        self.assertIsNone(tod.eliminator_bot)
        self.assertIsNone(tod.selbst)
        f = fail.tode(m, self.konfig)[0]
        self.assertIsNone(f["killer_bot"])
        self.assertEqual(fail.titel(f), "PLATZ 2")       # was bekannt ist, zählt weiter


@unittest.skipUnless(HAT_FFMPEG, "ffmpeg fehlt")
class FailSession(MitPuffer):
    def setUp(self):
        super().setUp()
        start = self.t0 - timedelta(seconds=100)          # die Aufnahme beginnt 100 s nach dem Replay
        ordner = self.konfig.ordner("sessions") / SITZUNG
        ordner.mkdir(parents=True)
        (ordner / "replay.json").write_text(json.dumps(roh(FREMDE + TRIPLE + TOD, start)), encoding="utf-8")
        self.con.execute("INSERT INTO matches (id, replay_pfad, start_utc, ende_utc, erstellt, geaendert)"
                         " VALUES (?, 'replays/x.replay', ?, ?, 'x', 'x')", (SITZUNG, iso(start), iso(start)))

    def test_moment_aus_dem_rohvideo_idempotent(self):
        self.aufnahme(QUELLE, dauer=30.0, datei=testvideo(self.tmp / "roh.mp4", dauer=30.0))
        e = fail.fail_session(self.con, self.konfig, SITZUNG)
        self.assertEqual((e["tode"], e["neu"], e["fehler"]), (1, 1, 0))
        z = self.con.execute("SELECT * FROM momente WHERE schluessel = ?", (f"fail:{SITZUNG}:120",)).fetchone()
        mk = json.loads(z["merkmale"])
        # Tod bei 20 s in der Aufnahme → Moment 8 … 23 s, Tod bei 12 s in der Datei
        self.assertEqual((z["clip_id"], z["stimmung"], round(z["ende_s"])), (None, "frustriert", 15))
        self.assertEqual((mk["fail"], mk["tod_sekunde"], mk["platz"], mk["kills_vorher_30s"]), (True, 12.0, 2, 3))
        self.assertEqual(mk["kill_sekunden"], [])          # Kern um den Tod, kein Kill-Titel
        self.assertTrue((self.konfig.ordner("sessions") / SITZUNG / "momente").is_dir())
        nochmal = fail.fail_session(self.con, self.konfig, SITZUNG)
        self.assertEqual((nochmal["neu"], nochmal["schon_da"]), (0, 1))

    def test_ohne_aufnahme_kein_moment(self):
        e = fail.fail_session(self.con, self.konfig, SITZUNG)
        self.assertEqual((e["tode"], e["ohne_aufnahme"], e["neu"]), (1, 1, 0))
        self.assertIsNone(self.con.execute("SELECT 1 FROM momente").fetchone())


if __name__ == "__main__":
    unittest.main()
