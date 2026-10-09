"""Abnahme Stufe 3 (Mehrbenutzer, „Hybrider Render-Manager“, Schritt 2): Auftragsergebnisse bleiben korrekt – auch bei
Worker-Ausfall, Wiederholung und Verbindungsabbruch (Florians Auftrag). Echtes ffmpeg, kleines Testmaterial.

  1. Worker-Ausfall: `pipeline sitzungen` rendert das Abend-Video, nach Renderbeginn stirbt die ganze Prozessgruppe
     (SIGKILL – wie systemd an der Zeitgrenze oder ein Stromausfall). Die Sperre ist frei, der Entwurf hat keine
     Datei, unter dem Endnamen liegt kein Video. Der nächste Lauf rendert auf der CPU nach – genau ein Video, Länge
     ±2 Bilder zum Plan; ein dritter Lauf tut nichts.
  2. Verwaistes ffmpeg: Stirbt sein Aufrufer (kill -9), endet ffmpeg nach höchstens 1 s (setpriv, M143) – aus dem
     Haupt- wie aus einem Arbeits-Thread (der Lern-Bot rendert in Threads).
  3. Verbindungsabbruch: n8n ruft `render --session` über SSH ohne Terminal auf. Reißt die Verbindung ab, rechnet
     der Schritt zu Ende (Commit je Clip) und erst die JSON-Zeile am Ende scheitert (BrokenPipeError). Die Clips sind
     gespeichert, die Wiederholung meldet neu = 0 und lässt jede Datei, wie sie ist (SHA-256).
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

from clip_pipeline import cli, db, entwurf, medien, sitzung, sperre
from clip_pipeline.zeit import iso, jetzt

from tests.hilfen import HAT_FFMPEG
from tests.regie_hilfen import MOMENTE, MitRegieMaterial
from tests.test_ende_zu_ende import SID
from tests.test_ende_zu_ende_stufe2 import match_bis_decide, umgebung

SRC = str(Path(medien.__file__).resolve().parents[1])   # für die Kindprozesse (python -I sieht kein PYTHONPATH)

# Kind für 1.: wie `pipeline sitzungen` – unter der einen Rechen-Sperre, ohne claude und Whisper, ohne Netz
KIND_ABEND = r"""
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from clip_pipeline import db, musik, sitzung, sperre
from clip_pipeline.konfig import Konfig

def kein_netz(*args, **kwargs):
    raise OSError("kein Netz in Tests")

musik._hole = kein_netz
k = Konfig(daten=json.loads(Path(sys.argv[2]).read_text(encoding="utf-8")), quelle=Path(sys.argv[3]))
con = db.verbinde(k.datenbank)
with sperre.sperre(sperre.pfad(k), warten_s=0):
    sitzung.verarbeite(con, k, claude=False, whisper=False)
"""

# Kind für 2.: startet über medien.fuehre_aus ein ffmpeg, das 60 s rechnet (Echtzeit) – im Haupt- oder Arbeits-Thread
KIND_VERWAIST = r"""
import sys, threading
sys.path.insert(0, sys.argv[1])
from clip_pipeline import medien

befehl = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-re", "-f", "lavfi", "-i",
          "testsrc=size=320x240:rate=30:duration=60", "-f", "null", "-"]

def lauf():
    medien.fuehre_aus(befehl, "Verwaist", stillstand_s=None)

if sys.argv[2] == "thread":
    t = threading.Thread(target=lauf)
    t.start()
    t.join()
else:
    lauf()
"""


def _stat(pid: int) -> list[str] | None:
    """Felder aus /proc/<pid>/stat nach dem Namen: [Zustand, Eltern, Gruppe, …]; None, wenn es ihn nicht gibt."""
    try:
        return Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()
    except OSError:
        return None


def lebt(pid: int) -> bool:
    felder = _stat(pid)
    return felder is not None and felder[0] not in ("Z", "X")   # Zombie = tot, nur noch nicht abgeholt


def ffmpeg_von(eltern: int) -> int | None:
    for eintrag in Path("/proc").iterdir():
        if eintrag.name.isdigit() and (felder := _stat(int(eintrag.name))) and felder[1] == str(eltern):
            with contextlib.suppress(OSError):
                if (eintrag / "comm").read_text().strip() == "ffmpeg":
                    return int(eintrag.name)
    return None


def toete_ffmpeg(pid: int) -> None:
    """Aufräumen, falls ein Test scheitert: nur, wenn unter der Nummer noch ein ffmpeg läuft."""
    with contextlib.suppress(OSError):
        if lebt(pid) and Path(f"/proc/{pid}/comm").read_text().strip() == "ffmpeg":
            os.kill(pid, signal.SIGKILL)


def fingerabdruck(ordner: Path) -> dict[str, str]:
    return {p.relative_to(ordner).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(ordner.rglob("*")) if p.is_file()}


@unittest.skipUnless(HAT_FFMPEG, "ffmpeg fehlt")
class WorkerAusfall(MitRegieMaterial):
    SITZUNG = "session_2026-10-08_23-10-00"

    def setUp(self):
        super().setUp()
        # Es „gibt“ eine Grafikeinheit: Der erste Lauf versucht VA-API (scheitert hier) und fällt auf die CPU zurück;
        # das Nachholen muss gleich auf der CPU rendern (sitzung._auf_cpu) – im Sidecar dann ohne Rückfall
        geraet = self.tmp / "renderD128"
        geraet.touch()
        self.konfig.daten["schnitt"]["vaapi_geraet"] = str(geraet)
        self.regie = Path(self.konfig.wert("regie.ordner"))

    def videos_und_reste(self) -> tuple[list[Path], list[Path]]:
        alle = sorted(self.regie.rglob("*"))
        return ([p for p in alle if p.suffix == ".mp4" and ".tmp" not in p.name],
                [p for p in alle if ".tmp" in p.name])

    def lauf(self) -> dict:
        with sperre.sperre(sperre.pfad(self.konfig), warten_s=0):
            return sitzung.verarbeite(self.con, self.konfig, claude=False, whisper=False)

    def entwurf_der_sitzung(self):
        eid = self.con.execute("SELECT entwurf_id FROM sitzungen WHERE name = ?", (self.SITZUNG,)).fetchone()[0]
        return self.con.execute("SELECT * FROM entwuerfe WHERE id = ?", (eid,)).fetchone()

    def test_abend_video_nach_absturz_genau_einmal_auf_der_cpu(self):
        self.momente_anlegen(MOMENTE[:5])
        self.musik_anlegen(150, "episch")
        for m in ("m1", "m2"):
            self.con.execute("INSERT INTO matches (id, replay_pfad, start_utc, ende_utc, status, erstellt, geaendert) "
                             "VALUES (?, ?, 'x', 'x', 'verarbeitet', 'x', 'x')", (m, f"replays/{m}.replay"))
        ordner = self.konfig.wurzel / "sitzungen"
        ordner.mkdir(parents=True, exist_ok=True)
        (ordner / f"{self.SITZUNG}.json").write_text(json.dumps(
            {"matches": ["m1", "m2"], "ende_utc": iso(jetzt() - timedelta(minutes=50))}), encoding="utf-8")
        konfig_datei = self.tmp / "konfig.json"
        konfig_datei.write_text(json.dumps(self.konfig.daten), encoding="utf-8")
        self.con.close()                            # die Datenbank gehört jetzt dem Kind

        protokoll = self.tmp / "kind.log"
        with protokoll.open("wb") as aus:
            kind = subprocess.Popen([sys.executable, "-I", "-c", KIND_ABEND, SRC, str(konfig_datei),
                                     str(self.konfig.quelle)], stdout=aus, stderr=subprocess.STDOUT,
                                    start_new_session=True)   # eigene Prozessgruppe: Python und sein ffmpeg
        self.addCleanup(lambda: kind.poll() is None and os.killpg(kind.pid, signal.SIGKILL))
        start = time.monotonic()
        while not any(self.regie.rglob("*.tmp.mp4")):
            if kind.poll() is not None or time.monotonic() - start > 120:
                self.fail("Rendern begann nicht:\n" + protokoll.read_text(encoding="utf-8", errors="replace")[-2000:])
            time.sleep(0.05)
        time.sleep(0.5)                             # ffmpeg rechnet schon
        os.killpg(kind.pid, signal.SIGKILL)         # Ausfall: Python und ffmpeg auf einen Schlag
        kind.wait()

        with sperre.sperre(sperre.pfad(self.konfig), warten_s=0):
            pass                                    # frei – sonst Gesperrt
        self.con = db.verbinde(self.konfig.datenbank)
        e = self.entwurf_der_sitzung()
        self.assertEqual((e["status"], e["datei"]), ("neu", None))
        video = Path(e["schnittliste"]).with_suffix(".mp4")
        self.assertFalse(video.exists())            # nie ein halbes Video unter dem Endnamen
        self.assertEqual(self.videos_und_reste()[0], [])

        zweiter = self.lauf()                       # der nächste Timer-Lauf
        self.assertEqual((zweiter["nachgeholt"], zweiter["neu"]), ([self.SITZUNG], []))
        e = self.entwurf_der_sitzung()
        self.assertEqual((e["status"], e["datei"]), ("gerendert", str(video)))
        self.assertEqual(self.videos_und_reste(), ([video], []))   # genau ein Video, keine Zwischendateien mehr
        liste = json.loads(Path(e["schnittliste"]).read_text(encoding="utf-8"))
        laenge = entwurf._pruefe_renderdauer(liste, video)
        self.assertLessEqual(abs(laenge - liste["dauer_s"]), 2 / liste["fps"] + 1e-6)
        sidecar = json.loads(video.with_suffix(".render.json").read_text(encoding="utf-8"))
        self.assertEqual((sidecar["encoder"], sidecar["rueckfall"]), ("libx264", False))   # gleich auf der CPU
        stand = video.stat().st_mtime_ns

        dritter = self.lauf()                       # Wiederholung: nichts mehr zu tun
        self.assertEqual((dritter["nachgeholt"], dritter["neu"]), ([], []))
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM entwuerfe").fetchone()[0], 1)
        self.assertEqual(video.stat().st_mtime_ns, stand)


@unittest.skipUnless(HAT_FFMPEG and Path("/proc/self/stat").is_file(), "ffmpeg oder /proc fehlt")
class VerwaistesFfmpeg(unittest.TestCase):
    def test_ffmpeg_endet_mit_seinem_aufrufer(self):
        if not shutil.which("setpriv") or subprocess.run([*medien.MITSTERBEN, "true"]).returncode != 0:
            self.skipTest("setpriv fehlt oder geht hier nicht")
        for wo in ("haupt", "thread"):
            with self.subTest(wo):
                helfer = subprocess.Popen([sys.executable, "-I", "-c", KIND_VERWAIST, SRC, wo],
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.addCleanup(lambda h=helfer: h.poll() is None and h.kill())
                start, ff = time.monotonic(), None
                while ff is None and time.monotonic() - start < 30:
                    ff = ffmpeg_von(helfer.pid)
                    time.sleep(0.05)
                self.assertIsNotNone(ff, "ffmpeg startete nicht")
                self.addCleanup(toete_ffmpeg, ff)
                os.kill(helfer.pid, signal.SIGKILL)  # der Aufrufer stirbt (Speicher voll, kill -9)
                helfer.wait()
                tot = time.monotonic()
                while lebt(ff) and time.monotonic() - tot < 1.0:
                    time.sleep(0.02)
                self.assertFalse(lebt(ff), "ffmpeg rechnet ohne seinen Aufrufer weiter")


class KaputteLeitung(io.StringIO):
    """stdout einer abgerissenen SSH-Verbindung ohne Terminal: jedes Schreiben scheitert."""

    def write(self, text):
        raise BrokenPipeError(32, "Broken pipe")


@unittest.skipUnless(HAT_FFMPEG, "ffmpeg fehlt")
class Verbindungsabbruch(unittest.TestCase):
    def render(self, welt, stdout) -> int:
        with mock.patch.object(cli, "lade", return_value=welt.konfig), contextlib.redirect_stdout(stdout), \
                contextlib.redirect_stderr(io.StringIO()):
            return cli.main(["render", "--session", SID])

    def test_render_rechnet_zu_ende_wiederholung_aendert_nichts(self):
        welt = umgebung(self)
        match_bis_decide(welt, bot=False)
        with self.assertRaises(BrokenPipeError):     # nur die JSON-Zeile geht verloren
            self.render(welt, KaputteLeitung())
        clips = welt.con.execute("SELECT clip_pfad, vorschau_pfad FROM clips WHERE match_id = ?", (SID,)).fetchall()
        self.assertEqual(len(clips), 1)
        for pfad in clips[0]:
            self.assertTrue(welt.konfig.absolut(pfad).is_file(), pfad)
        self.assertEqual(db.match(welt.con, SID)["status"], "verarbeitet")
        ordner = welt.konfig.ordner("sessions") / SID
        vorher = fingerabdruck(ordner)

        aus = io.StringIO()
        self.assertEqual(self.render(welt, aus), 0)  # n8n wiederholt den Schritt
        antwort = json.loads(aus.getvalue().splitlines()[-1])
        self.assertEqual((antwort["clips"], antwort["neu"]), (1, 0))
        self.assertEqual(fingerabdruck(ordner), vorher)


if __name__ == "__main__":
    unittest.main()
