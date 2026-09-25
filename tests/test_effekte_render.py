"""Regisseur 2.0, Effekte im echten Video (entwurf.rendere) – kleine Listen aus 640×360-Quellen, zusammen ≤ 90 s.

Geprüft wird, was man sieht und hört: Zoom nur auf dem Spielbild, Texte nie darüber (auch nicht beim Zoom),
Titel im 16:9 nur in der Blende, Look auf einer Graurampe, Klänge auf der geplanten Zeit, Farbe je Segment."""

import os
import subprocess
import time
import unittest

import numpy as np

from clip_pipeline import effekt_filter, entwurf

from tests.effekt_hilfen import (QUADRAT, RAMPE_MAX, RATE, bilder, einsatz, mini_liste, pcm, quadrat_video,
                                 rampe_video)
from tests.hilfen import HAT_FFMPEG, MitSpeicher
from tests.regie_hilfen import FARBEN, farbe_bei, farbvideo, naechste_farbe
from tests.test_entwurf import dauern


@unittest.skipUnless(HAT_FFMPEG, "ffmpeg fehlt")
class Render(MitSpeicher):
    def setUp(self):
        super().setUp()
        self.konfig.daten.setdefault("regie", {})["effekte"] = {"an": True, "sfx_ordner": str(self.tmp / "sfx")}
        self.konfig.daten["schnitt"]["vaapi_geraet"] = str(self.tmp / "keine-gpu")  # immer libx264

    def rendere(self, liste: dict, name: str):
        ziel = self.tmp / f"{name}.mp4"
        entwurf.rendere(liste, ziel, self.konfig)
        d = dauern(ziel)
        self.assertAlmostEqual(d["video"], liste["dauer_s"], delta=2 / liste["fps"])
        self.assertAlmostEqual(d["audio"], liste["dauer_s"], delta=0.05)
        return ziel

    def test_short_zoom_nur_spielbild_texte_daneben_klaenge_auf_zeit(self):
        # Zeitleiste: Segment 1 0–3 s, Blende (dissolve 0,6 s) um 3,0, Segment 2 3–7 s.
        # Bei 4,95: Punch (Scheitel 5,0), Bass-Hit, DOUBLE KILL und „KILLS 2“; Whoosh mit Spitze auf dem Schnitt.
        q = quadrat_video(self.tmp / "q.mp4")
        liste = mini_liste([(q, 1.0, 4.0, ("schnitt", 0.0)), (q, 1.0, 5.0, ("dissolve", 0.6))], ereignisse=[
            (2, "punch", 4.95, {"dauer_s": 0.35}), (2, "sfx", 4.95, {"klang": "basshit"}),
            (2, "titel", 4.95, {"dauer_s": 1.2, "text": "DOUBLE KILL"}),
            (2, "zaehler", 4.95, {"dauer_s": 1.5, "zahl": 2}), (2, "sfx", 3.0, {"klang": "whoosh"})])
        video = self.rendere(liste, "short")
        b, h = 720, 1280
        oben, unten = effekt_filter.spielbild(b, h)
        # Bildnummern bei 30 fps: 4,5 / 5,0 (Zoom-Scheitel) / 5,07 (Pop-Maximum der Texte) / 5,2 / 5,67 s
        vor, scheitel, pop, texte, nach = 135, 150, 152, 156, 170
        f = {n: x.astype(int) for n, x in bilder(video, [vor, scheitel, pop, texte, nach]).items()}

        def quadrat_breite(bild):
            zeile = bild[(oben + unten) // 2]
            weiss = np.nonzero(zeile > 150)[0]
            return weiss[-1] - weiss[0] + 1

        w0 = quadrat_breite(f[vor])
        self.assertAlmostEqual(w0, QUADRAT * b / 640, delta=3)                        # 640 px Quelle -> 720 px
        self.assertAlmostEqual(quadrat_breite(f[scheitel]) / w0, 1.25, delta=0.03)   # Punch Stärke 1: Zoom 1,25
        self.assertAlmostEqual(quadrat_breite(f[nach]), w0, delta=2)
        grund = np.median(f[vor][oben + 2:unten - 2])
        rand_oben, rand_unten = slice(oben - 40, oben - 2), slice(unten + 2, unten + 15)
        # das ganze Spielbild außer dem (gezoomten) Quadrat in der Mitte – auch mittig über und unter ihm
        r = 0.65 * 1.25 * QUADRAT * b / 640
        ys, xs = np.mgrid[oben + 2:unten - 2, 0:b]
        neben = (np.abs(xs - b / 2) > r) | (np.abs(ys - (oben + unten) / 2) > r)
        for n in (scheitel, pop, texte):
            with self.subTest(bild=n):
                # im Spielbild neben dem Quadrat nur Grau: kein Text, kein schwarzer Textrand
                band = f[n][oben + 2:unten - 2]
                self.assertLessEqual(np.abs(band - grund)[neben].max(), 12)
                # der unscharfe Hintergrund zoomt nicht mit, das Spielbild wächst nicht über seinen Rand
                for rand in (rand_oben, rand_unten):
                    self.assertLessEqual(np.abs(f[n][rand] - f[vor][rand]).max(), 6)
        # Titel unter, Zähler über dem Spielbild – vorher war dort nichts Weißes
        titel, zaehler = slice(unten, int(0.75 * h)), slice(int(0.15 * h), oben)
        for n in (pop, texte):
            self.assertGreater((f[n][titel] > 200).sum(), 300, n)
            self.assertGreater((f[n][zaehler] > 200).sum(), 200, n)
        self.assertEqual((f[vor][titel] > 200).sum() + (f[vor][zaehler] > 200).sum(), 0)
        # Klänge: Bass-Hit setzt auf stillem Spielton genau bei 4,95 ein, der Whoosh ist am Schnitt am lautesten
        ton = pcm(video)
        self.assertAlmostEqual(einsatz(ton, -40, ab_s=4.5), 4.95, delta=0.015)
        fenster = int(0.02 * RATE)
        energie = np.convolve(ton[int(2.6 * RATE):int(3.4 * RATE)] ** 2, np.ones(fenster) / fenster, mode="same")
        self.assertAlmostEqual(2.6 + np.argmax(energie) / RATE, 3.0, delta=0.03)

    def test_short_4_zu_3_texte_neben_dem_hoeheren_spielbild(self):
        # 4:3-Aufnahme (480×360): im Short ist das Spielbild 720×540 (y 370 … 910) statt 720×406 – Titel und Zähler
        # rücken mit, auch im größten Moment des Pop-ins (Bild 33 = 1,1 s)
        q = quadrat_video(self.tmp / "q43.mp4", dauer=4.0, breite=480)
        liste = mini_liste([(q, 0.5, 3.0, ("schnitt", 0.0))], quelle_dauer=4.0, ereignisse=[
            (1, "titel", 1.0, {"dauer_s": 1.2, "text": "VICTORY ROYALE"}), (1, "zaehler", 1.0, {"dauer_s": 1.5, "zahl": 12})])
        video = self.rendere(liste, "short43")
        b, h = 720, 1280
        oben, unten = effekt_filter.spielbild(b, h, effekt_filter.spiel_hoehe(b, 480, 360))
        self.assertEqual((oben, unten), (370, 910))
        f = {n: x.astype(int) for n, x in bilder(video, [15, 33, 39]).items()}
        grund = np.median(f[15][oben + 2:unten - 2])
        r = 0.65 * QUADRAT * b / 480
        ys, xs = np.mgrid[oben + 2:unten - 2, 0:b]
        neben = (np.abs(xs - b / 2) > r) | (np.abs(ys - (oben + unten) / 2) > r)
        for n in (33, 39):
            with self.subTest(bild=n):
                self.assertLessEqual(np.abs(f[n][oben + 2:unten - 2] - grund)[neben].max(), 12)
                self.assertGreater((f[n][unten:int(0.75 * h)] > 200).sum(), 200)          # Titel darunter
                self.assertGreater((f[n][int(0.15 * h):oben] > 200).sum(), 200)          # Zähler darüber
        self.assertEqual((f[15][unten:int(0.75 * h)] > 200).sum() + (f[15][int(0.15 * h):oben] > 200).sum(), 0)

    def test_16_9_look_und_titel_nur_in_der_blende(self):
        # Graurampe, Look cinematic 0,8; TRIPLE KILL genau in der Blende (dissolve 0,6 s um 3,0: 2,7 … 3,3)
        q = rampe_video(self.tmp / "rampe.mp4")
        liste = mini_liste([(q, 0.5, 3.5, ("schnitt", 0.0)), (q, 0.5, 3.0, ("dissolve", 0.6))], fmt="zusammenschnitt",
                           look=("cinematic", 0.8), quelle_dauer=6.0,
                           ereignisse=[(1, "titel", 2.7, {"dauer_s": 0.6, "text": "TRIPLE KILL"})])
        video = self.rendere(liste, "zusammenschnitt")
        f = {n: x.astype(int) for n, x in bilder(video, [45, 75, 87, 90, 93, 105], "rgb24").items()}
        bild = f[45]
        breite = bild.shape[1]
        for grau, richtung in ((64, 1), (191, -1)):          # Schatten blaugrün, Lichter warm
            x = int(grau / RAMPE_MAX * breite)
            block = bild[300:420, x - 4:x + 4]
            b_minus_r = block[..., 2].mean() - block[..., 0].mean()
            self.assertGreaterEqual(richtung * b_minus_r, 6, (grau, b_minus_r))
        # Titel: reinweiß (≥ 235 in allen Kanälen) mitten in der Blende, 0,2 s davor und danach nichts
        for n in (87, 90, 93):
            self.assertGreater((f[n] >= 235).all(axis=2).sum(), 1000, n)
        for n in (75, 105):
            self.assertEqual((f[n] >= 235).all(axis=2).sum(), 0, n)

    def test_farben_je_segment_und_neue_uebergaenge(self):
        # Jede neue Übergangsart einmal; in der Mitte jedes Segments bleibt die geplante Farbe erkennbar
        quellen = [farbvideo(self.tmp / f"f{i}.mp4", FARBEN[i], 5.0) for i in range(5)]
        arten = [("schnitt", 0.0), ("whip", 0.25), ("zoom", 0.3), ("squeeze", 0.3), ("glitch", 0.2)]
        teile = [(q, 1.0, 3.4, art) for q, art in zip(quellen, arten)]    # je 2,4 s: Zeitleiste 0 … 12 s
        liste = mini_liste(teile, look=("cinematic", 0.8), quelle_dauer=5.0, ereignisse=[
            (2, "punch", 3.5, {"dauer_s": 0.35}), (2, "sfx", 3.5, {"klang": "basshit"}),
            (3, "akzent", 6.0, {"dauer_s": 0.2}), (4, "meme", 7.6, {"dauer_s": 0.83}), (4, "sfx", 7.6, {"klang": "pop"}),
            (5, "titel", 10.0, {"dauer_s": 1.2, "text": "VICTORY ROYALE"}),
            (5, "zaehler", 10.0, {"dauer_s": 1.5, "zahl": 3})])
        video = self.rendere(liste, "farben")
        for i, s in enumerate(liste["segmente"]):
            mitte = (s["zeit_start"] + s["zeit_ende"]) / 2
            self.assertEqual(naechste_farbe(farbe_bei(video, mitte)), i, s["uebergang"])
        # VICTORY ROYALE (längster Titel) im größten Moment des Pop-ins: ganz im Bild, nicht am Rand abgeschnitten
        bild = bilder(video, [303])[303]                                      # 10,1 s
        oben, unten = effekt_filter.spielbild(720, 1280)
        spalten = np.nonzero((bild[unten:int(0.75 * 1280)] > 200).any(axis=0))[0]
        self.assertGreater(len(spalten), 200)
        self.assertGreater(spalten[0], 0.1 * 720)
        self.assertLess(spalten[-1], 0.9 * 720)

    @unittest.skipUnless(os.environ.get("CLIP_LEISTUNG") == "1", "nur mit CLIP_LEISTUNG=1 (dauert Minuten)")
    def test_leistung_45_s_short(self):
        q = self.tmp / "quelle.mp4"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc2=size=1920x1080:rate=60:duration=48", "-f", "lavfi", "-i", "sine=f=300:d=48",
                        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(q)], check=True)
        arten = [("schnitt", 0.0), ("whip", 0.25), ("schnitt", 0.0), ("zoom", 0.3), ("glitch", 0.2), ("schnitt", 0.0)]
        teile = [(q, 1.0 + 7.5 * i, 8.5 + 7.5 * i, art) for i, art in enumerate(arten)]
        ereignisse = []
        for nr in range(1, 7):
            t = 7.5 * (nr - 1)
            ereignisse += [(nr, "punch", t + 2.0, {"dauer_s": 0.35}), (nr, "punch", t + 4.0, {"dauer_s": 0.35}),
                           (nr, "akzent", t + 5.5, {"dauer_s": 0.2}), (nr, "sfx", t + 4.0, {"klang": "basshit"}),
                           (nr, "titel", t + 4.1, {"dauer_s": 1.2, "text": "DOUBLE KILL"}),
                           (nr, "zaehler", t + 4.0, {"dauer_s": 1.5, "zahl": nr})]
        zeiten = {}
        for an in (False, True):
            liste = mini_liste(teile, ereignisse=ereignisse if an else [], look=("cinematic", 0.8), an=an,
                               quelle_dauer=48.0)
            start = time.monotonic()
            entwurf.rendere(liste, self.tmp / f"leistung-{an}.mp4", self.konfig)
            zeiten[an] = time.monotonic() - start
        print(f"\n45-s-Short: ohne Effekte {zeiten[False]:.1f} s, mit {zeiten[True]:.1f} s")
        self.assertLessEqual(zeiten[True] / zeiten[False], 1.25)
