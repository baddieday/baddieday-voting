"""Bestandsaufnahme: Replays zählen, Mikro-Spur erkennen (auch eine stumme), VA-API ehrlich melden."""

import subprocess
import unittest

from clip_pipeline import bestand

from tests.hilfen import HAT_FFMPEG, MitSpeicher, testvideo


def video_mit_spuren(ziel, namen, stumm=()):
    """Video mit benannten Tonspuren; Spuren in `stumm` sind wirklich still."""
    ziel.parent.mkdir(parents=True, exist_ok=True)
    befehl = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=4"]
    for i, _ in enumerate(namen):
        quelle = "anullsrc=r=48000:cl=mono" if i in stumm else f"sine=frequency={300 + 200 * i}"
        befehl += ["-f", "lavfi", "-t", "4", "-i", quelle]
    befehl += ["-map", "0:v"] + [x for i in range(len(namen)) for x in ("-map", f"{i + 1}:a")]
    for i, name in enumerate(namen):
        befehl += [f"-metadata:s:a:{i}", f"handler_name={name}"]
    subprocess.run(befehl + ["-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(ziel)], check=True)


@unittest.skipUnless(HAT_FFMPEG, "ffmpeg fehlt")
class Bestand(MitSpeicher):
    def test_bestand(self):
        w = self.konfig.wurzel
        for name in ("UnsavedReplay-2026.09.21-21.42.22.replay", "UnsavedReplay-2026.09.23-20.15.33.replay"):
            (w / "replays" / name).write_bytes(b"x" * 1000)
        video_mit_spuren(w / "eingang/steelseries/Fortnite__1.mp4", ["Game", "Chat"])
        video_mit_spuren(w / "eingang/steelseries/Fortnite__2.mp4", ["Game", "Chat"], stumm={1})
        testvideo(w / "eingang/nvidia/aufnahmen/a.mp4", tonspuren=1)
        self.konfig.daten["schnitt"]["vaapi_geraet"] = str(self.tmp / "keine-gpu")

        b = bestand.erstelle(self.konfig)
        s = b["speicher"]
        self.assertEqual((s["replays"]["anzahl"], s["videos"]["anzahl"]), (2, 3))
        self.assertEqual(s["replays"]["neueste"], "UnsavedReplay-2026.09.23-20.15.33.replay")
        proben = {p["datei"].rsplit("/", 1)[-1]: p for o in s["videos"]["ordner"].values() for p in o["stichprobe"]}
        self.assertEqual(proben["Fortnite__1.mp4"]["mikro_spur"], 1)
        self.assertIsNone(proben["Fortnite__2.mp4"]["mikro_spur"])  # Chat-Spur ist stumm
        self.assertFalse(proben["Fortnite__2.mp4"]["spuren"][1]["signal"])
        self.assertIsNone(proben["a.mp4"]["mikro_spur"])
        self.assertEqual(s["mikro_stichprobe"], {"ja": 1, "nein": 2})
        self.assertFalse(b["vaapi"]["encode_ok"])
        self.assertTrue(b["platz"])
        text = bestand.als_markdown(b)
        self.assertIn("Replays: **2**", text)
        self.assertIn("Chat (leer)", text)

    def test_speicher_offline_weckt_nicht(self):
        (self.konfig.wurzel / ".clip-speicher").unlink()
        b = bestand.erstelle(self.konfig, messen=False)
        self.assertIsNone(b["speicher"])
        self.assertIn("Markierungsdatei", b["speicher_offline"])
