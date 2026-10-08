"""Mehrbenutzer, Stufe 1, Schritt 7: `pipeline benutzer pruefen|einrichten` und die instanz.toml für einen neuen Freund.

pruefen läuft im Dienst als der Freund in seiner Sandbox – hier stellt eine Scheinwurzel den Namensraum nach (Florians
Pfade darunter), und die Rechte werden wie für einen normalen Benutzer geprüft (als root wäre sonst alles erlaubt).
einrichten lädt Whisper-Modell und Musik nur einmal – Whisper und NCS sind Attrappen, nichts geht ins Netz.
"""

from __future__ import annotations

import contextlib
import copy
import importlib.machinery
import io
import json
import os
import sys
import tempfile
import tomllib
import types
import unittest
from pathlib import Path
from unittest import mock

from clip_pipeline import benutzer, claude_aufruf, cli, db, musik, sperre
from clip_pipeline import konfig as konfig_mod
from clip_pipeline.konfig import KonfigFehler

from tests.hilfen import als_instanz, instanz_anlegen

MAX_ENV = "LEARN_BOT_TOKEN=max-test-lernbot\nLEARN_BOT_ALLOWED_USER_ID=222\nCLIP_EPIC_ID=max-test-epic\n"
REPO = tomllib.loads(konfig_mod.STANDARD_KONFIG.read_text(encoding="utf-8"))


def kann_wie_freund(pfad, modus: int) -> bool:
    """os.access wie für einen normalen Benutzer, dem alles hier gehört: nur die Besitzer-Bits zählen. So prüft der
    Test die Rechte auch, wenn er als root läuft."""
    try:
        bits = os.stat(pfad).st_mode
    except OSError:
        return False
    return all(bits & maske for flag, maske in ((os.R_OK, 0o400), (os.W_OK, 0o200), (os.X_OK, 0o100))
               if modus & flag)


def aufruf(*argumente: str) -> tuple[int, dict]:
    ausgabe = io.StringIO()
    with contextlib.redirect_stdout(ausgabe), contextlib.redirect_stderr(io.StringIO()):
        code = cli.main(list(argumente))
    return code, json.loads(ausgabe.getvalue().strip().splitlines()[-1])


class MitNamensraum(unittest.TestCase):
    """So sieht max seinen Namensraum: in Florians Ordner nur die Sperrdatei (nur lesbar), in der Konfig nur das
    Startwissen, /srv leer, unter den Freunden nur er selbst; seine Konfig und .env gehören root (nur lesbar)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(os.path.realpath(self._tmp.name))
        self.wurzel = self.tmp / "wurzel"
        self.sperre = self.wurzel / "var/lib/clip-pipeline/pipeline.lock"
        self.sperre.parent.mkdir(parents=True)
        self.sperre.touch()
        (self.wurzel / "opt/clip-pipeline/config").mkdir(parents=True)
        (self.wurzel / "opt/clip-pipeline/config/pipeline.toml").touch()
        (self.wurzel / "srv").mkdir()
        self.inst = instanz_anlegen(self.tmp / "benutzer", "max", sperre=self.sperre, env=MAX_ENV)
        self.addCleanup(self.inst.chmod, 0o750)   # vor dem Aufräumen wieder beschreibbar (auch ohne root)
        for datei in ("instanz.toml", ".env", ".clip-benutzer"):
            (self.inst / datei).chmod(0o440)
        self.inst.chmod(0o550)
        self.sperre.chmod(0o444)
        cache = str(self.inst / "cache")
        self.umgebung = {"HOME": cache, "XDG_CACHE_HOME": cache, "HF_HOME": f"{cache}/huggingface"}
        (self.tmp / "leer").mkdir()
        for patch in (mock.patch.object(benutzer, "WURZEL", self.wurzel),
                      mock.patch.object(benutzer, "_kann", kann_wie_freund),
                      mock.patch.object(claude_aufruf, "SUCHPFAD", str(self.tmp / "leer"))):   # kein claude
            patch.start()
            self.addCleanup(patch.stop)


class Pruefen(MitNamensraum):
    def test_normalfall_alles_getrennt(self):
        with als_instanz(self.inst, **self.umgebung):
            code, erg = aufruf("benutzer", "pruefen")
        self.assertEqual(code, 0, erg)
        self.assertEqual((erg["ok"], erg["name"], erg["befunde"]), (True, "max", []))
        st = os.stat(self.sperre)
        self.assertEqual(erg["sperre"], {"pfad": str(self.sperre), "dev": st.st_dev, "ino": st.st_ino})
        self.assertIn("claude ist auf dem Mini nicht installiert", " ".join(erg["hinweise"]))   # nur Hinweis
        self.assertFalse((self.inst / "db" / "pipeline.db").exists())   # nachsehen legt nichts an

    def test_fremder_pfad_und_beschreibbare_env_sind_befunde(self):
        (self.wurzel / "srv/puffer").mkdir()                    # Florians Puffer im Namensraum
        (self.inst / ".env").chmod(0o640)                       # .env beschreibbar
        (self.tmp / "benutzer" / "eva").mkdir()                 # ein anderer Freund sichtbar
        with als_instanz(self.inst, **self.umgebung):
            code, erg = aufruf("benutzer", "pruefen")
        self.assertEqual(code, 1)
        self.assertFalse(erg["ok"])
        pfade = {b["pfad"] for b in erg["befunde"]}
        self.assertEqual(pfade, {str(self.wurzel / "srv/puffer"), str(self.inst / ".env"),
                                 str(self.tmp / "benutzer" / "eva")})

    def test_nur_in_einer_instanz(self):
        with mock.patch.dict(os.environ), mock.patch.object(db, "verbinde", side_effect=AssertionError("Datenbank")):
            os.environ.pop("CLIP_INSTANZ", None)
            code, erg = aufruf("benutzer", "pruefen")
        self.assertEqual(code, 2)
        self.assertEqual(erg["fehler"], "konfig")
        self.assertIn("CLIP_INSTANZ", erg["hinweis"])


class FakeWhisper:
    """faster_whisper.utils.download_model: lädt ein Modell in den „Cache“ – local_files_only fragt nur nach."""

    def __init__(self):
        self.cache: set[str] = set()
        self.geladen: list[str] = []

    def download_model(self, name, local_files_only=False, **_):
        if name in self.cache:
            return f"/cache/{name}"
        if local_files_only:
            raise FileNotFoundError(name)
        self.geladen.append(name)
        self.cache.add(name)
        return f"/cache/{name}"

    def module(self) -> dict[str, types.ModuleType]:
        paket = types.ModuleType("faster_whisper")
        paket.__spec__ = importlib.machinery.ModuleSpec("faster_whisper", None)
        utils = types.ModuleType("faster_whisper.utils")
        utils.download_model = self.download_model
        paket.utils = utils
        return {"faster_whisper": paket, "faster_whisper.utils": utils}


class Einrichten(MitNamensraum):
    def test_zweiter_lauf_laedt_nichts_doppelt(self):
        whisper = FakeWhisper()
        ncs: list[tuple[list[str], int]] = []

        def ncs_laden(con, konfig, genres, anzahl=40):
            ncs.append((list(genres), anzahl))
            for i in range(anzahl):
                con.execute("INSERT INTO tracks (datei, titel, quelle, sha256, beats, genre, erstellt) "
                            "VALUES (?, ?, 'NCS', ?, '[]', ?, '2026-10-08T10:00:00+00:00')",
                            (f"t{i}.mp3", f"Titel {i}", f"sha{i}", genres[i % len(genres)]))
            return con.execute("SELECT * FROM tracks").fetchall()

        laeufe = []
        with als_instanz(self.inst, **self.umgebung), mock.patch.dict(sys.modules, whisper.module()), \
                mock.patch.object(musik, "ncs_genres_laden", side_effect=ncs_laden):
            for _ in range(2):
                laeufe.append(aufruf("benutzer", "einrichten"))
        (code1, erst), (code2, dann) = laeufe
        self.assertEqual((code1, code2), (0, 0), (erst, dann))
        self.assertEqual(erst["eingerichtet"], {"datenbank": "angelegt", "whisper": "Modell small geladen",
                                                "musik": "16 Songs von NCS geladen"})
        self.assertEqual(dann["eingerichtet"], {"datenbank": "schon da", "whisper": "Modell small schon da",
                                                "musik": "16 Songs der Genres schon da"})
        self.assertEqual(whisper.geladen, ["small"])                    # das Modell nur einmal
        self.assertEqual(len(ncs), 1)                                   # NCS nur beim ersten Lauf
        self.assertEqual(ncs[0][1], benutzer.MUSIK_ZIEL)
        self.assertEqual(set(ncs[0][0]), {"techno", "hardstyle", "hardcore", "phonk", "brazilian-phonk"})
        self.assertTrue((self.inst / "db" / "pipeline.db").is_file())
        self.assertTrue(dann["ok"])


class InstanzToml(unittest.TestCase):
    def test_aus_florians_konfig_nur_sperre_rechner_und_waffen(self):
        with tempfile.TemporaryDirectory() as ordner:
            tmp = Path(os.path.realpath(ordner))
            daten = copy.deepcopy(REPO)
            daten["datenbank"]["pfad"] = str(tmp / "florian" / "pipeline.db")
            daten["speicher"].update(host="192.0.2.51", wol_mac="aa:bb:cc:dd:ee:ff")
            daten["schnitt"].update(encoder="h264_vaapi", crf=23)
            daten["merkmale"]["waffen"] = {"sniper": [12], "nahkampf": [3, 27], "sonstige": [], "laser": [9]}
            florian = konfig_mod.Konfig(daten, konfig_mod.STANDARD_KONFIG)
            lock = sperre.pfad(florian)
            lock.parent.mkdir()
            lock.touch()
            text = benutzer.instanz_toml(florian, "max")
            werte = tomllib.loads(text)
            self.assertEqual(werte["sperre"], {"datei": str(lock), "warten_s": 900})
            self.assertEqual(werte["schnitt"], {"encoder": "h264_vaapi", "vaapi_geraet": "/dev/dri/renderD128"})
            self.assertEqual(werte["merkmale"]["waffen"], {"sniper": [12], "nahkampf": [3, 27], "sonstige": []})
            self.assertEqual(set(werte), {"sperre", "schnitt", "merkmale"})
            for fremd in ("192.0.2.51", "aa:bb", "laser", "crf", "pipeline.db"):
                self.assertNotIn(fremd, text)
            # die Instanz lädt sie genau so
            inst = instanz_anlegen(tmp / "benutzer", "max", sperre=None, env=MAX_ENV)
            (inst / "instanz.toml").write_text(text, encoding="utf-8")
            with als_instanz(inst):
                k = konfig_mod.lade()
                self.assertEqual(k.wert("schnitt.encoder"), "h264_vaapi")
                self.assertEqual(sperre.pfad(k), lock)
                with self.assertRaises(KonfigFehler):   # in einer Instanz nie – sie liest Florians Konfig
                    benutzer.instanz_toml(k, "eva")


if __name__ == "__main__":
    unittest.main()
