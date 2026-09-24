"""Musik: Tempo/Beats an Klick-Spuren mit bekanntem Tempo, Quellenangabe Pflicht, NCS-Seite auslesen."""

import subprocess
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from clip_pipeline import musik

from tests.hilfen import HAT_FFMPEG, MitSpeicher


def klicks(ziel: Path, bpm: float, dauer: float = 20) -> Path:
    ziel.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", f"sine=f=1000:d={dauer},volume='if(lt(mod(t\\,{60 / bpm})\\,0.03)\\,1\\,0)':eval=frame",
                    "-f", "lavfi", "-i", f"anoisesrc=a=0.01:d={dauer}", "-filter_complex", "amix=inputs=2", str(ziel)],
                   check=True)
    return ziel


NCS_SEITE = """<tr><td><a class="player-play" data-url="https://ncsmusic.s3.eu-west-1.amazonaws.com/tracks/1/fly.mp3"
 data-artist="x" data-artistraw="KDH, Tatsunoshin" data-track="Fly High" data-tid="1"></a></td>
<td><a href="/flyhigh"><img></a></td></tr>"""
NCS_TITEL = """<p class="p-copy" id="panel-copy2">Song: KDH, Tatsunoshin - Fly High [NCS Release]<br />
Music provided by NoCopyrightSounds<br />Free Download/Stream: <http://ncs.io/flyhigh><br /></p>"""


@unittest.skipUnless(HAT_FFMPEG, "ffmpeg fehlt")
class Analyse(MitSpeicher):
    def test_tempo_und_beats(self):
        for bpm in (90, 128, 150):
            a = musik.analysiere(klicks(self.tmp / f"k{bpm}.wav", bpm))
            self.assertAlmostEqual(a["bpm"], bpm, delta=bpm * 0.02)
            abstand = 60 / bpm
            # Beats liegen auf den Klicks (±40 ms)
            fehler = [min(b % abstand, abstand - b % abstand) for b in a["beats"]]
            self.assertLess(float(np.median(fehler)), 0.04, bpm)
            self.assertGreater(len(a["beats"]), 20 / abstand * 0.8)
        self.assertLess(musik.analysiere(self.tmp / "k90.wav")["energie"], musik.analysiere(self.tmp / "k150.wav")["energie"])

    def test_hinzufuegen_mit_quelle(self):
        self.konfig.daten["musik"]["ordner"] = str(self.tmp / "musik")
        datei = klicks(self.tmp / "eingang" / "titel.wav", 128)
        with self.assertRaises(ValueError):
            musik.hinzufuegen(self.con, self.konfig, datei, titel="T", quelle="  ")
        t = musik.hinzufuegen(self.con, self.konfig, datei, titel="Klick", kuenstler="Test", quelle="CC0", stimmung="chill")
        self.assertTrue(datei.is_file())  # Quelle bleibt liegen
        self.assertEqual((self.tmp / "musik" / "Test - Klick.lizenz.txt").read_text().strip(), "CC0")
        self.assertEqual(t["stimmungen"].split('"')[1], "chill")  # Hinweis zählt zuerst
        again = musik.hinzufuegen(self.con, self.konfig, datei, titel="Anders", quelle="CC0")
        self.assertEqual(again["id"], t["id"])  # gleiche Datei (Prüfsumme) -> kein zweiter Eintrag

    def test_bildunterschrift(self):
        titel, kuenstler, quelle = musik.aus_bildunterschrift("Song: Ailow - akina\nMusic provided by NCS #chill", "a.mp3")
        self.assertEqual((titel, kuenstler), ("akina", "Ailow"))
        self.assertNotIn("#chill", quelle)
        self.assertEqual(musik.stimmung_aus_text("super #Episch"), "episch")
        self.assertEqual(musik.aus_bildunterschrift("CC-BY Max", "Max - Lied.mp3")[:2], ("Lied", "Max"))

    def test_ncs_laden(self):
        self.konfig.daten["musik"]["ordner"] = str(self.tmp / "musik")
        mp3 = klicks(self.tmp / "fly.wav", 150).read_bytes()

        def hole(url, timeout=30):
            if "music-search" in url:
                return NCS_SEITE.encode()
            if url.endswith("/flyhigh"):
                return NCS_TITEL.encode()
            return mp3

        with mock.patch.object(musik, "_hole", side_effect=hole):
            neu = musik.ncs_laden(self.con, self.konfig, "episch", anzahl=2)
            self.assertEqual([t["titel"] for t in neu], ["Fly High"])  # nur ein Treffer, drei Filter
            self.assertEqual(musik.ncs_laden(self.con, self.konfig, "episch"), [])  # schon da
        lizenz = (self.tmp / "musik" / "KDH_ Tatsunoshin - Fly High.lizenz.txt").read_text()
        self.assertEqual(lizenz.count("NoCopyrightSounds"), 1)
        self.assertIn("ncs.io/flyhigh", lizenz)
