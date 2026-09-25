"""Klänge des Regisseurs: gleiche Formel = gleiche Datei, kein Knacken, Einsatz genau auf der Ereigniszeit."""

import hashlib
import re
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np

from clip_pipeline import konfig, sfx
from clip_pipeline.medien import MedienFehler

from tests.hilfen import HAT_FFMPEG

RATE = sfx.RATE
HEUTE_MIT_MUSIK = "[spiel][leiser]amix=inputs=2:normalize=0,alimiter=limit=0.95,apad[aout]"


def ereignis(t: float, klang: str, staerke: float = 1.0) -> dict:
    return {"art": "sfx", "t": t, "klang": klang, "staerke": staerke}


def wav(pfad: Path) -> np.ndarray:
    """Samples (n, 2) in −1..1."""
    with wave.open(str(pfad)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (RATE, 2, 2)
        roh = w.readframes(w.getnframes())
    return np.frombuffer(roh, dtype="<i2").reshape(-1, 2) / 32768


def links(eingaenge: list[str], graph: str, ausgang: str, dauer: float | None = None) -> np.ndarray:
    """Rendert einen Teilgraphen und gibt den linken Kanal zurück (32-bit float, ohne Umrechnung)."""
    befehl = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", *eingaenge, "-filter_complex", graph,
              "-map", ausgang, *(["-t", f"{dauer}"] if dauer else []), "-f", "f32le", "-"]
    roh = subprocess.run(befehl, capture_output=True, check=True).stdout
    return np.frombuffer(roh, dtype="<f4").reshape(-1, 2)[:, 0]


def dbfs(x: float) -> float:
    return 20 * np.log10(max(abs(x), 1e-12))


def mit_ordner(tmp: Path):
    k = konfig.lade()
    k.daten.setdefault("regie", {}).setdefault("effekte", {})["sfx_ordner"] = str(tmp)
    return k


class Graph(unittest.TestCase):
    """Reiner Text, ohne ffmpeg."""

    def test_ohne_klaenge_nichts(self):
        self.assertEqual(sfx.mischung([], 5), ([], ""))
        nichts = [{"art": "titel", "t": 1.0, "klang": None, "staerke": 1.0}, ereignis(2.0, "basshit", 0.0),
                  ereignis(0.0, "riser")]  # Riser endet auf 0 -> läge ganz vor dem Video
        self.assertEqual(sfx.mischung(nichts, 5), ([], ""))

    def test_aus_zeichengleich_mit_heute(self):
        self.assertEqual(sfx.abmischung(["[spiel]", "[leiser]"], False), HEUTE_MIT_MUSIK)
        self.assertEqual(sfx.abmischung(["[ax3]"], False), "[ax3]apad[aout]")
        mit = sfx.abmischung(["[spiel]", "[leiser]"], True)
        self.assertTrue(mit.startswith("[spiel][leiser][sfx]amix=inputs=3:normalize=0:duration=first,"), mit)
        self.assertTrue(sfx.abmischung(["[ax3]"], True).startswith("[ax3][sfx]amix=inputs=2:"))
        self.assertIn("alimiter=limit=0.95:level=0", mit)  # level=1 höbe die Spitze wieder auf 0 dBFS
        self.assertTrue(mit.endswith("apad[aout]"))

    def test_graph_fest_und_vollstaendig(self):
        liste = [ereignis(1.0, "basshit"), ereignis(1.2, "tick", 0.5), ereignis(1.25, "tick"),
                 ereignis(4.0, "whoosh", 0.6), ereignis(4.0, "riser"), ereignis(9.0, "basshit", 0.9)]
        klaenge, graph = sfx.mischung(liste, 7, 0.8)
        self.assertEqual(klaenge, ["basshit", "tick", "whoosh", "riser"])  # Reihenfolge wie KLAENGE
        # Reihenfolge der Ereignisse und dict/Objekt ändern nichts am Graphen
        objekte = [SimpleNamespace(nr=1, text=None, **e) for e in reversed(liste)]
        self.assertEqual(sfx.mischung(objekte, 7, 0.8), (klaenge, graph))
        for j in range(len(klaenge)):
            self.assertEqual(graph.count(f"[{7 + j}:a]"), 1)
        self.assertNotIn("[11:a]", graph)
        # Jedes Zwischen-Label genau einmal erzeugt und einmal verbraucht, [sfx] nur erzeugt
        namen = re.findall(r"\[(sfx[a-z]?\d*(?:_\d+)?)\]", graph)
        self.assertEqual(namen.count("sfx"), 1)
        self.assertTrue(graph.endswith("[sfx]"))
        for name in set(namen) - {"sfx"}:
            self.assertEqual(namen.count(name), 2, name)
        self.assertIn("volume=0.6480", graph)  # Bass-Hit: Pegel 0,9 · Stärke 0,9 · sfx_pegel 0,8
        self.assertIn("volume=0.2880", graph)  # Whoosh: 0,6 · 0,6 · 0,8
        self.assertIsNone(re.search(r"\d[eE][-+]?\d", graph))
        self.assertNotIn("null[vout]", graph)  # der VA-API-Ersatz darf die Tonkette nie treffen

    def test_spuren(self):
        # nacheinander: eine Spur (concat), kein amix
        _k, graph = sfx.mischung([ereignis(1.0, "tick"), ereignis(2.0, "basshit"), ereignis(5.0, "tick")], 1)
        self.assertIn("concat=n=3:v=0:a=1[sfx]", graph)
        self.assertNotIn("amix", graph)
        self.assertIn("adelay=delays=48000S:all=1", graph)  # Tick bei 1,0 s
        self.assertIn(f"adelay=delays={96000 - 48000 - 5760}S:all=1", graph)  # Pause nach dem Tick bis 2,0 s
        # überlappend: zwei Spuren
        _k, graph = sfx.mischung([ereignis(1.0, "basshit"), ereignis(1.3, "tick")], 1)
        self.assertIn("amix=inputs=2:normalize=0:duration=longest[sfx]", graph)
        # ein einzelnes Ereignis: direkt nach [sfx]
        _k, graph = sfx.mischung([ereignis(1.0, "pop")], 3)
        self.assertEqual(graph.split(";")[-1], "[sfxk0_0]volume=0.4000,adelay=delays=48000S:all=1[sfx]")

    def test_vor_null_abgeschnitten(self):
        # Whoosh-Mitte bei 0,1 s: Beginn läge bei −0,15 s -> 0,15 s vorne weg, keine Verzögerung
        _k, graph = sfx.mischung([ereignis(0.1, "whoosh")], 1)
        self.assertIn("atrim=start_sample=7200,asetpts=PTS-STARTPTS,volume=", graph)
        self.assertNotIn("adelay", graph)

    def test_unbekannter_klang_und_pegel(self):
        with tempfile.TemporaryDirectory() as tmp:
            k = mit_ordner(Path(tmp))
            with self.assertRaises(MedienFehler):
                sfx.datei(k, "hupe")
            self.assertEqual(list(Path(tmp).iterdir()), [])
            self.assertEqual(sfx.mischung([ereignis(1.0, "hupe")], 1), ([], ""))
            self.assertEqual(sfx.pegel(k), sfx.SFX_PEGEL)
            k.daten["regie"]["effekte"]["sfx_pegel"] = 7
            self.assertEqual(sfx.pegel(k), 2.0)


@unittest.skipUnless(HAT_FFMPEG, "ffmpeg fehlt")
class Dateien(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.konfig = mit_ordner(self.tmp / "a")

    def tearDown(self):
        self._tmp.cleanup()

    def test_gleiche_formel_gleiche_datei(self):
        erste = {name: sfx.datei(self.konfig, name) for name in sfx.KLAENGE}
        zweite = {name: sfx.datei(mit_ordner(self.tmp / "b"), name) for name in sfx.KLAENGE}
        for name in sfx.KLAENGE:
            self.assertRegex(erste[name].name, rf"^{name}-[0-9a-f]{{8}}\.wav$")
            self.assertEqual(erste[name].name, zweite[name].name)
            md5 = [hashlib.md5(p[name].read_bytes()).hexdigest() for p in (erste, zweite)]
            self.assertEqual(md5[0], md5[1], name)

    def test_idempotent_und_nie_loeschen(self):
        fremd = self.tmp / "a" / "basshit-00000000.wav"  # z. B. eine ältere Formel
        fremd.parent.mkdir(parents=True)
        fremd.write_bytes(b"alt")
        pfad = sfx.datei(self.konfig, "basshit")
        vorher = pfad.stat().st_mtime_ns
        with mock.patch.object(sfx, "fuehre_aus") as ffmpeg:
            self.assertEqual(sfx.datei(self.konfig, "basshit"), pfad)
            self.assertEqual(sfx.eingaenge(self.konfig, ["basshit"]), ["-i", str(pfad)])
        ffmpeg.assert_not_called()
        self.assertEqual(pfad.stat().st_mtime_ns, vorher)
        self.assertEqual(fremd.read_bytes(), b"alt")
        self.assertEqual(sorted(p.name for p in pfad.parent.iterdir()), sorted([fremd.name, pfad.name]))

    def test_knackfrei_und_ohne_uebersteuern(self):
        for name, (_ausdruck, dauer, _anker, _pegel) in sfx.KLAENGE.items():
            with self.subTest(name):
                x = wav(sfx.datei(self.konfig, name))
                self.assertEqual(len(x), round(dauer * RATE))
                self.assertTrue(np.array_equal(x[:, 0], x[:, 1]))
                self.assertLess(dbfs(abs(x[-int(0.005 * RATE):]).max()), -40)  # letzte 5 ms
                self.assertLess(abs(x).max(), 0.99)  # nicht abgeschnitten
                self.assertGreater(abs(x).max(), 0.3)  # und hörbar


@unittest.skipUnless(HAT_FFMPEG, "ffmpeg fehlt")
class Mischung(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.konfig = mit_ordner(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def soll(self, liste: list[dict], n: int) -> np.ndarray:
        """Dieselbe Mischung, mit numpy direkt aus den WAVs gerechnet."""
        ergebnis = np.zeros(n)
        for e in liste:
            _ausdruck, _dauer, anker, pegel = sfx.KLAENGE[e["klang"]]
            x = wav(sfx.datei(self.konfig, e["klang"]))[:, 0]
            beginn = round((e["t"] - anker) * RATE)
            if beginn < 0:
                x, beginn = x[-beginn:], 0
            x = x[:max(0, n - beginn)]
            ergebnis[beginn:beginn + len(x)] += x * round(pegel * e["staerke"] * sfx.SFX_PEGEL, 4)
        return ergebnis

    def test_einsatz_auf_ereigniszeit(self):
        liste = [ereignis(1.0, "basshit"), ereignis(2.2, "tick"), ereignis(2.26, "tick", 0.5),
                 ereignis(3.0, "einschlag", 0.7), ereignis(4.5, "pop"), ereignis(0.1, "whoosh"),
                 ereignis(6.0, "whoosh", 0.6), ereignis(8.0, "riser", 0.6)]
        klaenge, graph = sfx.mischung(liste, 1)
        eingaenge = ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo:d=1", *sfx.eingaenge(self.konfig, klaenge)]
        x = links(eingaenge, graph, "[sfx]")
        self.assertEqual(len(x), 8 * RATE)  # endet mit dem Riser-Ende
        self.assertLess(abs(x - self.soll(liste, len(x))).max(), 1e-4)  # samplegenau, Spuren/concat verlieren nichts

        def fenster(von, bis):
            return x[int(von * RATE):int(bis * RATE)], von

        for t, spanne in ((1.0, 0.8), (2.2, 0.05), (3.0, 0.5), (4.5, 0.12)):  # Beginn = Anker
            teil, von = fenster(t - 0.05, t + spanne)
            einsatz = von + np.argmax(abs(teil) > 10 ** (-50 / 20)) / RATE
            self.assertAlmostEqual(einsatz, t, delta=0.010)
        for t in (0.1, 6.0):  # Whoosh: Energie-Maximum (100-ms-Mittel) auf dem Schnitt
            teil, von = fenster(max(0.0, t - 0.4), t + 0.4)
            energie = np.convolve(teil ** 2, np.ones(RATE // 10) / (RATE // 10), mode="same")
            self.assertAlmostEqual(von + np.argmax(energie) / RATE, t, delta=0.030)
        teil, von = fenster(6.6, 8.0)  # Riser: endet auf dem Anker, vorher wird er lauter
        self.assertAlmostEqual(von + np.nonzero(abs(teil) > 10 ** (-60 / 20))[0][-1] / RATE, 8.0, delta=0.010)
        self.assertGreater(abs(teil[-RATE // 10:]).max(), 4 * abs(teil[:RATE // 10]).max())

    def test_mit_musik_spitze_und_laenge(self):
        # Nachbau des Endes von entwurf.filtergraph: lauter Spielton, Musik geduckt, dazu gestapelte Klänge
        liste = [ereignis(1.0, "basshit"), ereignis(1.0, "basshit"), ereignis(1.0, "einschlag"),
                 ereignis(1.0, "whoosh"), ereignis(1.0, "riser"), ereignis(2.9, "basshit")]
        klaenge, teil = sfx.mischung(liste, 2)
        eingaenge = ["-f", "lavfi", "-i", "aevalsrc=exprs='0.9*sin(2*PI*220*t)':c=stereo:s=48000:d=3",
                     "-f", "lavfi", "-i", "aevalsrc=exprs='0.8*sin(2*PI*330*t)':c=stereo:s=48000:d=10",
                     *sfx.eingaenge(self.konfig, klaenge)]
        graph = ";".join(["[0:a]asplit=2[spiel][schluessel]", "[1:a]atrim=0:3[mus]",
                          "[mus][schluessel]sidechaincompress=threshold=0.05:ratio=6:attack=20:release=400[leiser]",
                          teil, sfx.abmischung(["[spiel]", "[leiser]"], True)])
        x = links(eingaenge, graph, "[aout]", 3.0)
        self.assertEqual(len(x), 3 * RATE)  # duration=first: der Spielton bestimmt die Länge
        self.assertLessEqual(dbfs(abs(x).max()), -0.3)
        # ohne Musik: dieselbe Grenze
        klaenge, teil = sfx.mischung(liste, 1)
        eingaenge = [*eingaenge[:4], *sfx.eingaenge(self.konfig, klaenge)]
        x = links(eingaenge, f"{teil};{sfx.abmischung(['[0:a]'], True)}", "[aout]", 3.0)
        self.assertEqual(len(x), 3 * RATE)
        self.assertLessEqual(dbfs(abs(x).max()), -0.3)
        # ... und der Limiter verschiebt nichts (latency=1): Bass-Hit auf Stille setzt weiter bei 1,0 s ein
        klaenge, teil = sfx.mischung([ereignis(1.0, "basshit")], 1)
        still = ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo:d=3", *sfx.eingaenge(self.konfig, klaenge)]
        x = links(still, f"{teil};{sfx.abmischung(['[0:a]'], True)}", "[aout]", 3.0)
        self.assertAlmostEqual(np.argmax(abs(x) > 10 ** (-50 / 20)) / RATE, 1.0, delta=0.002)


if __name__ == "__main__":
    unittest.main()
