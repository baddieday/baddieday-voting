"""Stimmung: Wörter, Regeln, Audio-Merkmale, Tod aus dem Replay, höchstens ein Claude-Aufruf."""

import json
import os
import shutil
import subprocess
import unittest
from datetime import datetime, timedelta
from unittest import mock

from clip_pipeline import stimmung
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
