"""Stimmen raus (23.10.): Die Stimmen der Mitspieler stecken im Spielton. Vor dem Mischen trennt Demucs sie je Szene
heraus (stimmen_raus.bereinige, Cache), danach prüft Whisper die Tonstücke, die ins Video gehen – mit Stimmen gibt es
kein Video (StimmenFehler). Demucs und Whisper werden hier ersetzt (ein Skript, das kopiert; eine feste Wortzahl)."""

import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from clip_pipeline import entwurf, regie, shorts, stimmen_raus
from clip_pipeline.medien import MedienFehler

from tests.hilfen import HAT_FFMPEG, MitSpeicher
from tests.regie_hilfen import MOMENTE, MitRegieMaterial
from tests.test_entwurf import BildGraph

FALSCHES_DEMUCS = r"""#!/usr/bin/env bash
# Ersatz für demucs im Test: schreibt <out>/<modell>/no_vocals.wav (Kopie der Eingabe) und zählt die Aufrufe.
set -e
out=""; modell="htdemucs"; eingabe=""
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out="$2"; shift 2;;
    -n) modell="$2"; shift 2;;
    -d|-j|--filename) shift 2;;
    --two-stems=*) shift;;
    *) eingabe="$1"; shift;;
  esac
done
mkdir -p "$out/$modell"
cp "$eingabe" "$out/$modell/no_vocals.wav"
echo x >> "$(dirname "$0")/demucs-aufrufe"
"""


def falsches_demucs(ordner: Path) -> Path:
    skript = ordner / "demucs"
    skript.write_text(FALSCHES_DEMUCS, encoding="utf-8")
    skript.chmod(0o755)
    return skript


def aufrufe(skript: Path) -> int:
    zaehler = skript.with_name("demucs-aufrufe")
    return len(zaehler.read_text().splitlines()) if zaehler.is_file() else 0


def tonvideo(ziel: Path, dauer: float = 2.0) -> Path:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", f"color=c=0x336699:s=320x180:r=30:d={dauer}", "-f", "lavfi",
                    "-i", f"sine=frequency=440:duration={dauer}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(ziel)], check=True)
    return ziel


@unittest.skipUnless(HAT_FFMPEG, "ffmpeg fehlt")
class Bereinigen(MitSpeicher):
    def setUp(self):
        super().setUp()
        self.skript = falsches_demucs(self.tmp)
        self.konfig.daten["regie"]["ton"].update(stimmen_entfernen=True, demucs=str(self.skript))
        self.video = tonvideo(self.tmp / "clip.mp4")

    def test_saubere_wav_im_cache_zweiter_aufruf_sofort(self):
        self.assertTrue(stimmen_raus.aktiv(self.konfig))
        w = stimmen_raus.bereinige(self.konfig, self.video, 0.5, 1.0)
        self.assertTrue(w.is_file() and w.stat().st_size > 0)
        self.assertEqual(w.parent, stimmen_raus.cache(self.konfig))
        self.assertEqual(aufrufe(self.skript), 1)
        self.assertEqual(stimmen_raus.bereinige(self.konfig, self.video, 0.5, 1.0), w)
        self.assertEqual(aufrufe(self.skript), 1)   # aus dem Cache
        # anderer Ausschnitt = neue Arbeit; kein Arbeitsordner bleibt liegen
        stimmen_raus.bereinige(self.konfig, self.video, 1.0, 0.5)
        self.assertEqual(aufrufe(self.skript), 2)
        self.assertEqual([p for p in w.parent.iterdir() if p.suffix != ".wav"], [])

    def test_ohne_demucs_nur_pruefung(self):
        self.konfig.daten["regie"]["ton"]["demucs"] = str(self.tmp / "gibt-es-nicht")
        with mock.patch.object(stimmen_raus, "demucs_programm", return_value=None):
            self.assertFalse(stimmen_raus.aktiv(self.konfig))
            with self.assertRaises(MedienFehler):
                stimmen_raus.bereinige(self.konfig, self.video, 0.0, 1.0)


class Pruefung(MitSpeicher):
    def test_zu_viele_woerter_halten_das_video_zurueck(self):
        self.konfig.daten["regie"]["ton"]["stimmen_pruefen"] = True
        wav = self.tmp / "x.wav"
        wav.write_bytes(b"RIFF")
        with mock.patch.object(stimmen_raus, "pruefen_aktiv", return_value=True), \
                mock.patch.object(stimmen_raus, "_woerter", return_value=(7, "Einer fehlt noch, oder?")):
            with self.assertRaises(stimmen_raus.StimmenFehler) as cm:
                stimmen_raus.pruefe(self.konfig, [wav, None])
            self.assertIn("7 Wörter", str(cm.exception))
        with mock.patch.object(stimmen_raus, "pruefen_aktiv", return_value=True), \
                mock.patch.object(stimmen_raus, "_woerter", return_value=(2, "ja")):
            self.assertEqual(stimmen_raus.pruefe(self.konfig, [wav]), 2)
        self.konfig.daten["regie"]["ton"]["stimmen_pruefen"] = False
        self.assertIsNone(stimmen_raus.pruefe(self.konfig, [wav]))


class Graphen(unittest.TestCase):
    def test_ton_aus_eigenem_eingang(self):
        liste = BildGraph.liste("short")
        graph, _ = entwurf.filtergraph(liste, [1, 1, 0], b=720, h=1280, musik_eingang=None, schrift=None,
                                       ton_quellen=[5, 6, None])
        self.assertIn("[5:a:0]", graph)
        self.assertIn("[6:a:0]", graph)
        for i in range(3):
            self.assertNotIn(f"[{i}:a:", graph)
        self.assertIn("[a0]", graph)   # Ausgangsnamen bleiben – die Verkettung dahinter ist unverändert
        self.assertIn("anullsrc", graph)   # Segment ohne Ton: Stille wie bisher

    def test_short_graph_mit_zweitem_eingang(self):
        from clip_pipeline import konfig
        k = konfig.lade()
        g, _ = shorts.filtergraph(dauer=10, fps=30, tonspuren=1, layout="unschaerfe", konfig=k, ton_eingang=1)
        self.assertIn("[1:a:0]", g)
        self.assertNotIn("[0:a:", g)


@unittest.skipUnless(HAT_FFMPEG, "ffmpeg fehlt")
class Rendern(MitRegieMaterial):
    def setUp(self):
        super().setUp()
        self.skript = falsches_demucs(self.tmp)
        self.konfig.daten["regie"]["ton"].update(stimmen_entfernen=True, stimmen_pruefen=True, demucs=str(self.skript))
        self.momente_anlegen(MOMENTE[:6])

    def test_video_aus_bereinigtem_ton(self):
        e = regie.erstelle(self.con, self.konfig, "short")
        with mock.patch.object(stimmen_raus, "pruefen_aktiv", return_value=True), \
                mock.patch.object(stimmen_raus, "_woerter", return_value=(0, "")) as woerter:
            ergebnis = entwurf.entwurf(self.con, self.konfig, e["entwurf"])
        datei = Path(ergebnis["datei"])
        self.assertTrue(datei.is_file())
        n = len(json.loads(Path(e["datei"]).read_text(encoding="utf-8"))["segmente"])
        self.assertEqual(aufrufe(self.skript), n)           # je Segment einmal Demucs
        self.assertEqual(len(woerter.call_args[0][1]), n)    # geprüft wurden genau diese Tonstücke
        sidecar = json.loads(datei.with_suffix(".render.json").read_text(encoding="utf-8"))
        self.assertTrue(sidecar["stimmen_entfernt"])
        self.assertEqual(sidecar["stimmen_woerter"], 0)

    def test_mit_stimmen_kein_video(self):
        e = regie.erstelle(self.con, self.konfig, "short")
        with mock.patch.object(stimmen_raus, "pruefen_aktiv", return_value=True), \
                mock.patch.object(stimmen_raus, "_woerter", return_value=(12, "Hallo? Ich lebe noch. Danke.")):
            with self.assertRaises(stimmen_raus.StimmenFehler):
                entwurf.entwurf(self.con, self.konfig, e["entwurf"])
        self.assertFalse(Path(e["datei"]).with_suffix(".mp4").exists())
        self.assertIsNone(self.con.execute("SELECT datei FROM entwuerfe WHERE id = ?", (e["entwurf"],)).fetchone()[0])


if __name__ == "__main__":
    unittest.main()
