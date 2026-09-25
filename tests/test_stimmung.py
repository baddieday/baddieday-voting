"""Stimmung: Wörter, Regeln, Audio-Merkmale, Tod aus dem Replay, höchstens ein Claude-Aufruf."""

import json
import os
import shutil
import subprocess
import unittest
from datetime import datetime, timedelta
from unittest import mock

from clip_pipeline import stimmung
from clip_pipeline.medien import MedienFehler
from clip_pipeline.zeit import UTC, iso

from tests.hilfen import HAT_FFMPEG, MitSpeicher

HAT_ESPEAK = shutil.which("espeak-ng") is not None
START = datetime(2026, 9, 21, 19, 0, tzinfo=UTC)


class Regeln(unittest.TestCase):
    def test_woerter(self):
        z = stimmung.woerter("Hahaha, nein! Let's go, jaaa, geil! Scheiße.")
        self.assertEqual(z, {"lachen": 1, "jubel": 3, "frust": 2})

    def test_stimmungen(self):
        faelle = [
            ({"kills": 3, "max_gruppe": 3, "jubel_laut": 1}, "episch"),
            ({"kills": 1, "max_gruppe": 1, "victory_royale": 1}, "episch"),
            ({"tod": 1, "frust": 2}, "frustriert"),
            ({"lachen": 3, "tod": 1}, "lustig"),
            ({"kills": 0, "spitzen": 0, "energie": 0.2}, "chill"),
            ({"kills": 2, "max_gruppe": 1, "spitzen": 4, "umgehauen": 1, "energie": 0.6}, "spannend"),
        ]
        for mk, erwartet in faelle:
            self.assertEqual(stimmung.entscheide(mk)[0], erwartet, mk)

    def test_ausbrueche(self):
        verlauf = [(i / 10, -30.0) for i in range(100)]
        for i in range(40, 46):  # 0,6 s laut
            verlauf[i] = (i / 10, -15.0)
        self.assertEqual(stimmung._ausbrueche(verlauf, 8), [4.0])
        self.assertEqual(stimmung._ausbrueche(verlauf, 8, min_s=1.0), [])


def video(ziel, *, spiel_knall_s=(), mikro_wav=None, dauer=8):
    """Spielspur: leises Rauschen mit lauten Knallen; Mikrospur: Sprache oder leise."""
    ziel.parent.mkdir(parents=True, exist_ok=True)
    knall = "+".join(f"between(t,{t},{t + 0.6})" for t in spiel_knall_s) or "0"
    befehl = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
              "-f", "lavfi", "-i", f"testsrc2=size=320x180:rate=30:duration={dauer}",
              "-f", "lavfi", "-i", f"anoisesrc=a=0.02:d={dauer},volume='if({knall},25,1)':eval=frame"]
    befehl += ["-i", str(mikro_wav)] if mikro_wav else ["-f", "lavfi", "-i", f"anoisesrc=a=0.001:d={dauer}"]
    befehl += ["-map", "0:v", "-map", "1:a", "-map", "2:a", "-metadata:s:a:0", "handler_name=Game",
               "-metadata:s:a:1", "handler_name=Chat", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
               "-t", str(dauer), str(ziel)]
    subprocess.run(befehl, check=True)
    return ziel


@unittest.skipUnless(HAT_FFMPEG, "ffmpeg fehlt")
class Durchlauf(MitSpeicher):
    def _clip(self, nr, datei, *, kills, max_gruppe, sek=0):
        cid = self.clip_anlegen(status="freigegeben", start=START + timedelta(minutes=sek), max_gruppe=max_gruppe,
                                match_id="2026-09-21_21-00-00")
        self.con.execute("UPDATE clips SET clip_pfad = ?, kills = ?, quelle_start_s = 0, quelle_ende_s = 8 WHERE id = ?",
                         (self.konfig.relativ(datei), kills, cid))
        return cid

    def setUp(self):
        super().setUp()
        self.konfig.daten["material"] = {"ordner": str(self.tmp / "mini")}
        sitzung = self.konfig.ordner("sessions") / "2026-09-21_21-00-00"
        sitzung.mkdir(parents=True)
        # Replay: ich sterbe 5 min + 4 s nach Start (im zweiten Clip)
        (sitzung / "replay.json").write_text(json.dumps({
            "replay_start": iso(START), "replay_start_kind": "Utc", "laenge_ms": 900000, "ich_quelle": "konfig",
            "ich": {"epic_id": "ICH", "platzierung": 12},
            "eliminierungen": [{"t_ms": 304000, "eliminator": "GEGNER", "eliminiert": "ICH", "knock": False}],
        }))
        clips = self.konfig.ordner("sessions") / "2026-09-21_21-00-00" / "clips"
        self.episch = self._clip(1, video(clips / "1.mp4", spiel_knall_s=(1, 3, 5)), kills=3, max_gruppe=3)
        self.tod = self._clip(2, video(clips / "2.mp4", spiel_knall_s=(2,)), kills=0, max_gruppe=0, sek=5)

    def test_ohne_whisper_und_claude(self):
        e = stimmung.analysiere(self.con, self.konfig, claude=False, whisper=False)
        self.assertEqual(e["analysiert"], 2)
        zeilen = {z["clip_id"]: z for z in self.con.execute("SELECT * FROM momente")}
        self.assertEqual(zeilen[self.episch]["stimmung"], "episch")
        self.assertEqual(zeilen[self.tod]["stimmung"], "frustriert")
        mk = json.loads(zeilen[self.episch]["merkmale"])
        self.assertEqual(mk["spitzen"], 3)  # drei Knalle erkannt
        self.assertEqual(json.loads(zeilen[self.tod]["merkmale"])["tod_sekunde"], 4.0)
        # zweiter Lauf: nichts mehr zu tun (Whisper ist teuer)
        self.assertEqual(stimmung.analysiere(self.con, self.konfig, claude=False, whisper=False)["analysiert"], 0)

    def test_ein_claude_aufruf_fuer_unsichere_und_pruefung(self):
        self.konfig.daten.setdefault("stimmung", {})["unsicher_unter"] = 1.01  # alle gelten als unsicher
        antwort = {"result": json.dumps({"momente": [
            {"id": f"clip:{self.episch}", "stimmung": "spannend"},
            {"id": f"clip:{self.tod}", "stimmung": "wütend"},       # nicht erlaubt -> Regel bleibt
            {"id": "clip:999", "stimmung": "lustig"},              # unbekannt -> ignoriert
        ]})}
        lauf = subprocess.CompletedProcess([], 0, stdout=json.dumps(antwort), stderr="")
        echt = subprocess.run
        with mock.patch("shutil.which", return_value="claude"), \
                mock.patch("subprocess.run", side_effect=lambda b, **kw: lauf if b[0] == "claude" else echt(b, **kw)) as aufruf:
            e = stimmung.analysiere(self.con, self.konfig, whisper=False)
        self.assertEqual(sum(1 for c in aufruf.call_args_list if c.args[0][0] == "claude"), 1)
        self.assertEqual(e["claude"], 1)
        self.assertIn("verworfen", e["hinweise"][0])
        zeilen = {z["clip_id"]: z for z in self.con.execute("SELECT * FROM momente")}
        self.assertEqual((zeilen[self.episch]["stimmung"], zeilen[self.episch]["quelle"]), ("spannend", "claude"))
        self.assertEqual((zeilen[self.tod]["stimmung"], zeilen[self.tod]["quelle"]), ("frustriert", "regel"))

    def test_mit_transkript(self):
        with mock.patch.object(stimmung.Transkription, "text", return_value="Hahaha, hahaha, das war so lustig"), \
                mock.patch.object(stimmung.Transkription, "verfuegbar", return_value=True), \
                mock.patch.object(stimmung.bestand, "mikro_spur", return_value=1):
            stimmung.analysiere(self.con, self.konfig, claude=False, neu=True)
        z = self.con.execute("SELECT * FROM momente WHERE clip_id = ?", (self.tod,)).fetchone()
        self.assertEqual(z["stimmung"], "lustig")  # gestorben, aber gelacht
        self.assertIn("lustig", z["text"])

    @unittest.skipUnless(HAT_ESPEAK and os.environ.get("CLIP_TEST_WHISPER"), "CLIP_TEST_WHISPER=1 und espeak-ng nötig")
    def test_echtes_whisper(self):
        wav = self.tmp / "sprache.wav"
        subprocess.run(["espeak-ng", "-v", "de", "-s", "140", "-w", str(wav), "Nein! Ich bin tot. Das ist ernsthaft unfair."], check=True)
        clips = self.konfig.ordner("sessions") / "2026-09-21_21-00-00" / "clips"
        video(clips / "2.mp4", spiel_knall_s=(2,), mikro_wav=wav)
        stimmung.analysiere(self.con, self.konfig, claude=False, neu=True)
        z = self.con.execute("SELECT * FROM momente WHERE clip_id = ?", (self.tod,)).fetchone()
        self.assertIn("tot", z["text"].lower())
        self.assertGreaterEqual(json.loads(z["merkmale"])["frust"], 2)
        self.assertEqual(z["stimmung"], "frustriert")


@unittest.skipUnless(HAT_FFMPEG, "ffmpeg fehlt")
class Reihenfolge(Durchlauf):
    def test_max_nimmt_die_besten_und_nie_verworfene(self):
        self.con.execute("UPDATE clips SET punkte = 1, status = 'gesendet' WHERE id = ?", (self.episch,))
        self.con.execute("UPDATE clips SET punkte = 9, status = 'freigegeben' WHERE id = ?", (self.tod,))
        e = stimmung.analysiere(self.con, self.konfig, claude=False, whisper=False, maximal=1)
        self.assertEqual(e["analysiert"], 1)
        self.assertEqual([z["clip_id"] for z in self.con.execute("SELECT clip_id FROM momente")], [self.tod])
        self.con.execute("UPDATE clips SET status = 'verworfen' WHERE id = ?", (self.episch,))
        self.assertEqual(stimmung.analysiere(self.con, self.konfig, claude=False, whisper=False)["analysiert"], 0)


SITZUNG = "2026-09-21_21-00-00"
CLIP_START = START + timedelta(minutes=5)


def team_wipe_replay(sitzung):
    """Team-Wipe im Clip ab Minute 5: umgehauen bei −3 s (vor dem Clip), +2 s und +4 s; alle sterben bei +4 s."""
    def elim(t, opfer, knock=False):
        return {"t_ms": round(t * 1000), "eliminator": "ICH", "eliminiert": opfer, "knock": knock}

    sitzung.mkdir(parents=True, exist_ok=True)
    (sitzung / "replay.json").write_text(json.dumps({
        "replay_start": iso(START), "replay_start_kind": "Utc", "laenge_ms": 900000, "ich_quelle": "konfig",
        "ich": {"epic_id": "ICH", "platzierung": 5},
        "eliminierungen": [elim(297, "OPFER-1", True), elim(302, "OPFER-2", True), elim(304, "OPFER-3", True),
                           elim(304, "OPFER-1"), elim(304, "OPFER-2"), elim(304, "OPFER-3")],
    }))


class Aktionen(MitSpeicher):
    """aktion_sekunden in den Moment-Merkmalen (Vertrag mit regie.py) – ohne FFmpeg."""

    def setUp(self):
        super().setUp()
        self.konfig.daten["material"] = {"ordner": str(self.tmp / "mini")}
        team_wipe_replay(self.konfig.ordner("sessions") / SITZUNG)
        self.wipe = self.clip_anlegen(status="freigegeben", start=CLIP_START, max_gruppe=3, match_id=SITZUNG)
        self.ohne_replay = self.clip_anlegen(status="freigegeben", start=CLIP_START, max_gruppe=1, match_id="ohne-replay")
        for cid in (self.wipe, self.ohne_replay):
            self.con.execute("UPDATE clips SET clip_pfad = ?, kills = max_gruppe, quelle_start_s = 0, quelle_ende_s = 8"
                             " WHERE id = ?", (f"sessions/{SITZUNG}/clips/{cid}.mp4", cid))

    def test_kill_und_aktion_sekunden(self):
        from clip_pipeline import replay
        e = replay.MeinEreignis
        eigene = [e(CLIP_START + timedelta(seconds=4), "kill", aktion_utc=CLIP_START - timedelta(seconds=3)),
                  e(CLIP_START + timedelta(seconds=4), "kill", aktion_utc=CLIP_START + timedelta(seconds=2)),
                  e(CLIP_START + timedelta(seconds=1), "knock"),
                  e(CLIP_START + timedelta(seconds=20), "kill")]  # außerhalb des Fensters
        self.assertEqual(stimmung.kill_und_aktion_sekunden(eigene, CLIP_START, 8.0), ([4.0, 4.0], [-3.0, 2.0]))

    def test_momente_und_merkmale(self):
        momente = {m.schluessel: m for m in stimmung.momente_aus_clips(self.con, self.konfig)}
        wipe, ohne = momente[f"clip:{self.wipe}"], momente[f"clip:{self.ohne_replay}"]
        self.assertEqual(wipe.aktion_sekunden, [-3.0, 2.0, 4.0])
        self.assertIsNone(ohne.aktion_sekunden)  # ohne Replay: alter Stand
        with mock.patch.object(stimmung.bestand, "tonspuren", side_effect=MedienFehler("keine Datei")):
            mk, _ = stimmung.merkmale(wipe, self.konfig, None)
            mk_ohne, _ = stimmung.merkmale(ohne, self.konfig, None)
        self.assertEqual((mk["kill_sekunden"], mk["aktion_sekunden"]), ([4.0, 4.0, 4.0], [-3.0, 2.0, 4.0]))
        self.assertNotIn("aktion_sekunden", mk_ohne)
        self.assertNotIn(stimmung.NACHSCHNITT, mk)


@unittest.skipUnless(HAT_FFMPEG, "ffmpeg fehlt")
class Nachschnitt(MitSpeicher):
    """`stimmung --neu` darf einen neu geschnittenen Moment nicht auf den Bot-Clip zurücksetzen."""

    def setUp(self):
        super().setUp()
        self.konfig.daten["material"] = {"ordner": str(self.tmp / "mini")}
        sitzung = self.konfig.ordner("sessions") / SITZUNG
        team_wipe_replay(sitzung)
        self.bot_clip = video(sitzung / "clips" / "1.mp4", spiel_knall_s=(4,))
        self.neu = video(sitzung / "momente" / "clip_1.mp4", spiel_knall_s=(1, 8), dauer=12)
        self.cid = self.clip_anlegen(status="freigegeben", start=CLIP_START, max_gruppe=3, match_id=SITZUNG)
        self.con.execute("UPDATE clips SET clip_pfad = ?, kills = 3, quelle_start_s = 0, quelle_ende_s = 8 WHERE id = ?",
                         (self.konfig.relativ(self.bot_clip), self.cid))
        # so, wie der Nachschnitt die Zeile hinterlässt: neue Datei, beginnt 4 s vor dem Bot-Clip
        self.eintrag = {"datei_vorher": str(self.bot_clip), "anlauf_s": 4.0}
        self.con.execute(
            """INSERT INTO momente (schluessel, clip_id, match_id, datei, start_s, ende_s, start_utc, kills, stimmung,
                                    sicherheit, quelle, merkmale, erstellt, geaendert)
               VALUES (?, ?, ?, ?, 0, 12, ?, 3, 'episch', 0.5, 'regel', ?, 'x', 'x')""",
            (f"clip:{self.cid}", self.cid, SITZUNG, str(self.neu), iso(CLIP_START - timedelta(seconds=4)),
             json.dumps({"kill_sekunden": [8.0, 8.0, 8.0], "aktion_sekunden": [1.0, 6.0, 8.0],
                         stimmung.NACHSCHNITT: self.eintrag})))

    def _zeile(self):
        return self.con.execute("SELECT * FROM momente WHERE schluessel = ?", (f"clip:{self.cid}",)).fetchone()

    def test_neu_behaelt_den_nachschnitt(self):
        stimmung.analysiere(self.con, self.konfig, neu=True, claude=False, whisper=False)
        z = self._zeile()
        self.assertEqual((z["datei"], z["start_s"], z["ende_s"], z["start_utc"]),
                         (str(self.neu), 0.0, 12.0, iso(CLIP_START - timedelta(seconds=4))))
        mk = json.loads(z["merkmale"])
        self.assertEqual(mk[stimmung.NACHSCHNITT], self.eintrag)
        self.assertEqual((mk["kill_sekunden"], mk["aktion_sekunden"], mk["dauer_s"]),
                         ([8.0, 8.0, 8.0], [1.0, 6.0, 8.0], 12.0))  # auf die neue Datei bezogen

    def test_fehlt_die_datei_gilt_wieder_der_bot_clip(self):
        self.con.execute("UPDATE momente SET datei = ? WHERE schluessel = ?", (str(self.tmp / "fehlt.mp4"), f"clip:{self.cid}"))
        with self.assertLogs("pipeline", "WARNING"):
            stimmung.analysiere(self.con, self.konfig, neu=True, claude=False, whisper=False)
        z = self._zeile()
        self.assertEqual((z["datei"], z["ende_s"], z["start_utc"]), (str(self.bot_clip), 8.0, iso(CLIP_START)))
        mk = json.loads(z["merkmale"])
        self.assertNotIn(stimmung.NACHSCHNITT, mk)
        self.assertEqual((mk["kill_sekunden"], mk["aktion_sekunden"]), ([4.0, 4.0, 4.0], [-3.0, 2.0, 4.0]))
