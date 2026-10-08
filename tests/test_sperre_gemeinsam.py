"""Mehrbenutzer, Stufe 1, PR 1 (M1): EINE Rechen-Sperre für den ganzen Mini.

Ein Freund hat eine eigene Datenbank, aber dieselbe Sperrdatei ([sperre].datei) wie Florian. Ohne den Schlüssel
bleibt alles wie bisher (<datenbank>.lock). Darf ein Prozess die Sperrdatei nur lesen, wirkt flock über O_RDONLY;
fehlt sie und lässt sie sich nicht anlegen, gibt es einen klaren Fehler und nie eine private Ersatzsperre.
„Nur lesen“ spielt ein Mock von os.open für genau diese eine Datei (die Tests laufen oft als root).
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import errno
import fcntl
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from clip_pipeline import big, cli, shorts
from clip_pipeline import sperre as sperre_modul
from clip_pipeline.konfig import Konfig
from clip_pipeline.sperre import Gesperrt, sperre

from tests.hilfen import MitSpeicher
from tests.test_getrennt import fremde_sperre

try:
    from clip_pipeline.bot import app as bot_app
except ImportError:  # python-telegram-bot nicht installiert
    bot_app = None

PROJEKT = Path(__file__).resolve().parents[1]
QUELLEN = PROJEKT / "src" / "clip_pipeline"
UPDATE = PROJEKT / "deploy" / "pve-mini" / "alles-aktualisieren.sh"
ECHT_OPEN = os.open   # vor jedem Mock gemerkt


@contextlib.contextmanager
def nur_lesbar(ziel: Path, fehler: int = errno.EROFS):
    """os.open darf `ziel` nur lesend öffnen (wie ein schreibgeschützt eingebundenes Florian-pipeline.lock).
    Liefert die Liste der Öffnungs-Flags für `ziel`."""
    flags_liste: list[int] = []

    def oeffnen(pfad, flags, *args, **kwargs):
        if Path(pfad) == ziel:
            flags_liste.append(flags)
            if flags & (os.O_RDWR | os.O_WRONLY | os.O_CREAT):
                raise OSError(fehler, os.strerror(fehler), str(pfad))
        return ECHT_OPEN(pfad, flags, *args, **kwargs)

    with mock.patch("os.open", side_effect=oeffnen):
        yield flags_liste


class MitZweiInstanzen(MitSpeicher):
    """A = Florian (self.konfig), B = Freund: eigene Datenbank, dieselbe Sperrdatei."""

    def setUp(self):
        super().setUp()
        self.gemeinsam = self.tmp / "mini" / "pipeline.lock"
        self.gemeinsam.parent.mkdir()
        self.konfig.daten["sperre"].update(datei=str(self.gemeinsam), warten_s=0)
        daten = copy.deepcopy(self.konfig.daten)
        daten["datenbank"]["pfad"] = str(self.tmp / "freund" / "pipeline.db")
        self.freund = Konfig(daten, self.konfig.quelle)

    def scan(self, k: Konfig) -> tuple[int, list[str]]:
        ausgabe = io.StringIO()
        with mock.patch("clip_pipeline.cli.lade", return_value=k), contextlib.redirect_stdout(ausgabe), \
                contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(["scan"])
        return code, ausgabe.getvalue().splitlines()


class EineSperre(MitZweiInstanzen):
    def test_pfad_ohne_schluessel_wie_bisher(self):
        # Migration: Florian ohne [sperre].datei (oder leer) behält <datenbank>.lock
        for wert in (None, "", "  "):
            with self.subTest(wert=wert):
                if wert is None:
                    del self.konfig.daten["sperre"]["datei"]
                else:
                    self.konfig.daten["sperre"]["datei"] = wert
                self.assertEqual(sperre_modul.pfad(self.konfig), self.tmp / "test.lock")
        self.assertEqual(sperre_modul.pfad(self.freund), self.gemeinsam)
        self.freund.daten["sperre"]["datei"] = ""   # heute: jede Datenbank hätte ihre eigene Sperre
        self.assertNotEqual(sperre_modul.pfad(self.freund), sperre_modul.pfad(self.konfig))

    def test_zweite_instanz_wartet_auf_die_erste(self):
        with fremde_sperre(sperre_modul.pfad(self.konfig), self.tmp / "bereit"):   # A rechnet (anderer Prozess)
            code, zeilen = self.scan(self.freund)
            beschaeftigt = big.pipeline_beschaeftigt(self.freund)     # auch der Belegt-Test sieht dieselbe Sperre
        self.assertEqual(code, 4)
        self.assertEqual(len(zeilen), 1)                              # genau eine JSON-Zeile
        self.assertEqual(json.loads(zeilen[0])["fehler"], "gesperrt")
        self.assertTrue(beschaeftigt)
        self.assertFalse(self.freund.datenbank.with_suffix(".lock").exists())   # keine eigene Sperre daneben
        # danach frei: B rechnet – und schreibt die Messzeile
        with self.assertLogs("pipeline", "INFO") as logs:
            code, zeilen = self.scan(self.freund)
        self.assertEqual(code, 0, zeilen)
        self.assertNotIn("fehler", json.loads(zeilen[-1]))
        self.assertTrue(any("Sperre gewartet" in z and "gehalten" in z for z in logs.output), logs.output)

    def test_fehlt_und_nicht_anlegbar_klarer_fehler(self):
        with nur_lesbar(self.gemeinsam):
            code, zeilen = self.scan(self.freund)
        self.assertEqual(code, 2)
        self.assertEqual(len(zeilen), 1)
        fehler = json.loads(zeilen[0])
        self.assertEqual(fehler["fehler"], "konfig")
        self.assertIn("fehlt und lässt sich nicht anlegen", fehler["hinweis"])
        self.assertEqual(list(self.tmp.rglob("*.lock")), [])           # nie eine Ersatzsperre, nirgends


class NurLesen(MitZweiInstanzen):
    def setUp(self):
        super().setUp()
        self.gemeinsam.touch()                                         # Florians Datei gibt es schon

    def test_sperre_wirkt_ueber_o_rdonly_in_beide_richtungen(self):
        with fremde_sperre(self.gemeinsam, self.tmp / "bereit"), nur_lesbar(self.gemeinsam):
            with self.assertRaises(Gesperrt):                          # Florian rechnet → der Freund wartet
                with sperre(self.gemeinsam, warten_s=0):
                    pass
        with nur_lesbar(self.gemeinsam) as flags:
            with sperre(self.gemeinsam):                               # der Freund rechnet → Florian wartet
                fd = ECHT_OPEN(self.gemeinsam, os.O_RDWR)
                try:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                finally:
                    os.close(fd)
        self.assertEqual(flags[-1], os.O_RDONLY)

    def test_belegt_test_in_big_ueber_o_rdonly(self):
        with fremde_sperre(self.gemeinsam, self.tmp / "bereit"), nur_lesbar(self.gemeinsam, errno.EACCES):
            self.assertTrue(big._sperre_belegt(self.gemeinsam))
        with nur_lesbar(self.gemeinsam, errno.EACCES):
            self.assertFalse(big._sperre_belegt(self.gemeinsam))


@unittest.skipIf(bot_app is None, "python-telegram-bot fehlt")
class PaketImClipBot(MitSpeicher):
    """/paket rendert nur unter der Pipeline-Sperre (vorher ganz ohne) – und wartet nicht, der Bot bleibt frei."""

    def setUp(self):
        super().setUp()
        self.konfig.daten.setdefault("caption", {})["ki"] = False
        self.cid = self.clip_anlegen(status="freigegeben")
        self.con.execute("UPDATE clips SET clip_pfad = 'sessions/m1/clips/001.mp4' WHERE id = ?", (self.cid,))
        self.nachrichten, self.dokumente, self.renders = [], [], []

        async def send_message(chat, text, **_):
            self.nachrichten.append(text)

        async def send_document(chat, **kwargs):
            self.dokumente.append(kwargs["filename"])

        self.context = SimpleNamespace(bot_data={"con": self.con, "konfig": self.konfig, "erlaubt": 42},
                                       bot=SimpleNamespace(send_message=send_message, send_document=send_document))

    def falsches_rendere(self, quelle, ziel, konfig, **_):
        self.renders.append(str(sperre_modul.pfad(konfig).resolve()) in sperre_modul.GEHALTEN)
        ziel.parent.mkdir(parents=True, exist_ok=True)
        ziel.write_bytes(b"short")
        return 5

    def test_rendert_nicht_solange_die_sperre_gehalten_wird(self):
        with mock.patch.object(shorts, "rendere", side_effect=self.falsches_rendere):
            with sperre(sperre_modul.pfad(self.konfig)):               # z. B. ein Abend-Video rendert gerade
                asyncio.run(bot_app.sende_paket(self.context, self.cid))
            self.assertEqual((self.renders, self.dokumente), ([], []))
            self.assertIn(f"⏳ Gerade rechnet ein anderer Schritt – gleich nochmal: /paket {self.cid}",
                          self.nachrichten)
            asyncio.run(bot_app.sende_paket(self.context, self.cid))   # frei: rendert – unter der Sperre
        self.assertEqual(self.renders, [True])
        self.assertEqual(self.dokumente, [f"clip-battle_{self.cid}.mp4"])


class Waechter(unittest.TestCase):
    def test_sperrpfad_nur_in_sperre_py(self):
        # Sonst entstünde wieder eine private Sperre je Datenbank, und zwei Instanzen rechneten gleichzeitig
        muster = re.compile(r"""with_suffix\(\s*["']\.lock["']\s*\)""")
        treffer = sorted(p.relative_to(QUELLEN).as_posix() for p in QUELLEN.rglob("*.py")
                         if muster.search(p.read_text(encoding="utf-8")))
        self.assertEqual(treffer, ["sperre.py"])

    def test_update_skript_fragt_sperre_pfad(self):
        text = UPDATE.read_text(encoding="utf-8")
        befehl = re.search(r"""^SPERRE="\$\(im_ct runuser .*? -c \\\n\s*'([^']+)'""", text, flags=re.M).group(1)
        self.assertIn("sperre.pfad(konfig.lade())", befehl)
        self.assertIn('[ -n "$SPERRE" ] || SPERRE="${DB%.*}.lock"', text)   # älterer Code-Stand: wie bisher
        with tempfile.TemporaryDirectory() as ordner:
            tmp = Path(ordner)
            umgebung = {**os.environ, "CLIP_DATENBANK": str(tmp / "x.db")}
            umgebung.pop("CLIP_KONFIG", None)
            r = subprocess.run([sys.executable, "-c", befehl], capture_output=True, text=True, env=umgebung,
                               timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip().splitlines()[-1], str(tmp / "x.lock"))


if __name__ == "__main__":
    unittest.main()
