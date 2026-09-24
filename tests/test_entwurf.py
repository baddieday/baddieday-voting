"""Entwürfe rendern: richtige Momente an der richtigen Stelle, Dauer exakt, klein genug für Telegram."""

import json
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from clip_pipeline import entwurf, medien, regie, regie_lernen

from tests.hilfen import HAT_FFMPEG
from tests.regie_hilfen import FARBEN, MOMENTE, MitRegieMaterial, farbe_bei, naechste_farbe


def dauern(video: Path) -> dict[str, float]:
    text = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration", "-of", "csv=p=0",
                           str(video)], capture_output=True, text=True, check=True).stdout
    return {art: float(d) for art, d in (z.split(",") for z in text.split())}


@unittest.skipUnless(HAT_FFMPEG, "ffmpeg fehlt")
class Entwurf(MitRegieMaterial):
    def pruefe(self, fmt, groesse):
        p, ziel = regie_lernen.aktuelle(self.con, self.konfig)
        e = regie.erstelle(self.con, self.konfig, fmt, parameter=p, ziel=ziel)
        liste = json.loads(Path(e["datei"]).read_text())
        r = entwurf.entwurf(self.con, self.konfig, e["entwurf"])
        video = Path(r["datei"])
        info = medien.probe(video)
        self.assertEqual([info.breite, info.hoehe], groesse)
        d = dauern(video)
        fps = liste["fps"]
        self.assertAlmostEqual(d["video"], liste["dauer_s"], delta=2 / fps)  # Übergänge verkürzen nicht
        self.assertAlmostEqual(d["audio"], liste["dauer_s"], delta=0.05)
        self.assertLess(video.stat().st_size, 48_000_000)
        # In der Mitte jedes Segments ist genau der geplante Moment zu sehen
        farbe_von = {f"datei:{i}": (i - 1) % len(FARBEN) for i in range(1, 100)}
        for s in liste["segmente"]:
            mitte = (s["zeit_start"] + s["zeit_ende"]) / 2
            self.assertEqual(naechste_farbe(farbe_bei(video, mitte)), farbe_von[s["moment"]], s)
        self.assertTrue(entwurf.entwurf(self.con, self.konfig, e["entwurf"])["uebersprungen"])
        return liste

    def test_short_und_zusammenschnitt(self):
        self.momente_anlegen(MOMENTE * 2, farbig=True)
        self.musik_anlegen(150, "episch")
        self.musik_anlegen(128, "spannend")
        liste = self.pruefe("short", [720, 1280])
        self.assertTrue(30 <= liste["dauer_s"] <= 45)
        liste = self.pruefe("zusammenschnitt", [1280, 720])
        self.assertTrue(180 <= liste["dauer_s"] <= 300)
        self.assertTrue(any(s["uebergang"]["art"] != "schnitt" for s in liste["segmente"]))

    def test_final_auftrag_schreibt_pfade_fuer_pve_big(self):
        # Moment-Dateien aus der lokalen Material-Kopie -> Pfade relativ zur Speicher-Wurzel auf pve-big
        self.konfig.daten["material"] = {"ordner": str(self.tmp / "momente")}
        self.momente_anlegen(MOMENTE[:6])
        self.musik_anlegen(150, "episch")
        e = regie.erstelle(self.con, self.konfig, "short")
        auftrag = entwurf.final_auftrag(self.con, self.konfig, e["entwurf"])
        daten = json.loads(auftrag.read_text())
        self.assertTrue(all(not s["datei"].startswith("/") for s in daten["segmente"]))
        self.assertTrue((self.konfig.wurzel / "regie" / "musik" / daten["musik"]["datei"]).is_file())
        self.assertEqual(entwurf.encoder(self.konfig, final=True)[2], "h264_nvenc")


class FinalAufBig(MitRegieMaterial):
    def test_wecken_rendern_aus(self):
        from clip_pipeline import big
        from clip_pipeline.zeit import iso, jetzt
        from tests.test_big import FalscherBig

        self.konfig.daten["material"] = {"ordner": str(self.tmp / "momente")}
        self.momente_anlegen(MOMENTE[:6])
        e = regie.erstelle(self.con, self.konfig, "short")
        falsch = FalscherBig(self.tmp)
        self.konfig.daten["speicher"].update(wol_mac="aa:bb:cc:dd:ee:ff", wecken_warten_s=30)
        self.konfig.daten["big"].update(host="pve-big", ssh=[str(falsch.skript)], aus_warten_s=5)
        big._schreibe_zustand(self.konfig, status_ok=iso(jetzt()))
        an = {"wert": False}

        def wol(_mac):
            an["wert"] = True

        with mock.patch.object(big, "wach", side_effect=lambda _k=None: an["wert"] and not (self.tmp / "aus").exists()), \
                mock.patch.object(big, "sende_wake_on_lan", side_effect=wol), mock.patch("time.sleep"):
            ergebnis = entwurf.final_auf_big(self.con, self.konfig, e["entwurf"])
        self.assertEqual(falsch.aufrufe[0], "status")
        self.assertIn("final", falsch.aufrufe)
        self.assertEqual(falsch.aufrufe[-1], "aus")  # danach sofort aus
        self.assertTrue(ergebnis["final"].startswith("regie/final/"))
