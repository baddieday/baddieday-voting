"""Mehrbenutzer, Stufe 1, Schritt 2 (M1): Instanz-Modus – CLIP_INSTANZ lädt nur die eigenen Werte.

Ein Freund läuft als eigene Instanz I (CLIP_INSTANZ=I): Startwissen aus der Repo-pipeline.toml, darüber I/instanz.toml,
Zugänge nur aus I/.env, jeder Datenpfad fest unter I, Puffer-Betrieb ohne Lager, KI nur mit eigenem Claude-Zugang.
Florian (ohne CLIP_INSTANZ) bleibt genau wie vorher. Kein Test startet ein echtes claude oder berührt Florians Pfade;
alle Zugangswerte sind künstliche Test-Werte.
"""

from __future__ import annotations

import contextlib
import fcntl
import io
import json
import os
import shutil
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from clip_pipeline import (aufraeumen, big, claude_aufruf, cli, db, entwurf, geschmack, lager, material, mikro, musik,
                           publikum_adapter, regie, sfx, sperre)
from clip_pipeline import konfig as konfig_mod
from clip_pipeline.konfig import KonfigFehler, SpeicherOffline, liegt_in
from clip_pipeline.zeit import iso, jetzt

from tests.hilfen import MitSpeicher, als_instanz, instanz_anlegen
from tests.test_screenshot import GUELTIG, FakeClaude

try:
    from clip_pipeline import lernbot
    import telegram  # noqa: F401
except ImportError:  # python-telegram-bot nicht installiert
    lernbot = None

PROJEKT = Path(__file__).resolve().parents[1]
ECHTES_READ_TEXT = Path.read_text   # vor jedem Mock gemerkt
FLORIAN_PFADE = ("/var/lib/clip-pipeline", "/srv/clips", "/srv/puffer", "/srv/big")
# Florians Zugänge – stehen vor jedem Instanz-Test in der Umgebung
FLORIAN_UMGEBUNG = {"TELEGRAM_BOT_TOKEN": "florian-test-clipbot", "TELEGRAM_ALLOWED_USER_ID": "111",
                    "LEARN_BOT_TOKEN": "florian-test-lernbot", "TIKTOK_CLIENT_KEY": "florian-test-tiktok",
                    "TIKTOK_CLIENT_SECRET": "florian-test-tiktok-geheim", "YOUTUBE_ACCESS_TOKEN": "florian-test-yt",
                    "CLIP_EPIC_ID": "florian-test-epic", "CLAUDE_CONFIG_DIR": "/var/lib/clip-pipeline/claude",
                    "CLAUDE_CODE_OAUTH_TOKEN": "florian-test-claude", "ANTHROPIC_API_KEY": "florian-test-anthropic"}
MAX_ENV = "LEARN_BOT_TOKEN=max-test-lernbot\nLEARN_BOT_ALLOWED_USER_ID=222\nCLIP_EPIC_ID=max-test-epic\n"
MAX_TOKEN = "sk-ant-oat01-test-max-0123456789abcdef"   # Form wie aus `claude setup-token`, Test-Wert
PUFFER_ORDNER = ("eingang", "replays", "sessions", "highlights", "musik", "archiv", "papierkorb")


def abgeleitete_pfade(k: konfig_mod.Konfig) -> dict[str, Path]:
    """Alle Orte, an denen die Pipeline mit dieser Konfig liest oder schreibt – außer der gemeinsamen Sperre."""
    pfade = {"datenbank": k.datenbank, "puffer": k.wurzel, "lager": k.lager_wurzel,
             "upload": entwurf.upload_ziel(k, {"name": "short-1"}), "regie": regie.ordner(k), "musik": musik.ordner(k),
             "material": material.ordner(k), "sfx": sfx.ordner(k), "oauth": publikum_adapter.cache_pfad(k),
             "mikro": k.datenbank.parent / mikro.ANSTOSS_NAME, "big_zustand": big.zustand_datei(k),
             "big_halten": big.halten_ordner(k), "lager_sperre": big.lager_sperre(k)}
    pfade.update({f"puffer/{n}": k.ordner(n) for n in PUFFER_ORDNER})
    return pfade


def abdruck(wurzel: Path, ohne: Path) -> dict[str, tuple[int, int]]:
    """(Größe, mtime) jeder Datei unter wurzel außer im Ordner ohne – so sieht man, ob außerhalb etwas entstand."""
    return {str(p.relative_to(wurzel)): (p.stat().st_size, p.stat().st_mtime_ns) for p in wurzel.rglob("*")
            if p.is_file() and not liegt_in(p, ohne)}


class MitInstanzen(unittest.TestCase):
    """Florians Sperrdatei und zwei Freunde (max, eva) in einem Temp-Ordner, Florians Zugänge in der Umgebung. Nach
    jedem Test ist die Umgebung wieder wie vorher."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(os.path.realpath(self._tmp.name))
        self.sperrdatei = self.tmp / "florian" / "pipeline.lock"
        self.sperrdatei.parent.mkdir()
        self.sperrdatei.touch()
        umgebung = mock.patch.dict(os.environ, FLORIAN_UMGEBUNG)
        umgebung.start()
        self.addCleanup(umgebung.stop)   # stellt auch die gleich entfernten Variablen wieder her
        for name in ("CLIP_INSTANZ", "CLIP_KONFIG", "CLIP_SPEICHER", "CLIP_DATENBANK", "LEARN_BOT_ALLOWED_USER_ID"):
            os.environ.pop(name, None)
        self.max = instanz_anlegen(self.tmp / "freunde", "max", sperre=self.sperrdatei, env=MAX_ENV)
        self.eva = instanz_anlegen(self.tmp / "freunde", "eva", sperre=self.sperrdatei,
                                   env="LEARN_BOT_TOKEN=eva-test-lernbot\n")

    def lade(self, inst: Path, **umgebung) -> konfig_mod.Konfig:
        with als_instanz(inst, **umgebung):
            return konfig_mod.lade()


# --- Datenisolation --------------------------------------------------------------------------------------------

class Datenisolation(MitInstanzen):
    def test_alle_pfade_unter_der_eigenen_instanz(self):
        k = self.lade(self.max)
        self.assertEqual(k.instanz, self.max)
        for name, pfad in abgeleitete_pfade(k).items():
            self.assertTrue(liegt_in(pfad, self.max), f"{name}: {pfad}")
        self.assertEqual(sperre.pfad(k), self.sperrdatei)   # geteilt wird nur Florians Rechen-Sperre
        self.assertTrue(mikro.anstossen(k, "2026-10-08_20-00-00"))
        self.assertTrue((self.max / "db" / mikro.ANSTOSS_NAME).is_file())
        # kein Host, keine MAC, kein SSH zu pve-big, nichts wird gelöscht, Florians claude-Weg gebremst
        self.assertEqual([k.wert(n) for n in ("speicher.host", "speicher.wol_mac", "big.host", "big.ssh_ziel",
                                              "big.ssh_schluessel", "decide.programm")], [""] * 6)
        self.assertEqual((k.wert("puffer.freigeben"), k.wert("aufraeumen.aktiv"), k.wert("sperre.warten_s")),
                         (False, False, 900))

    def test_zwei_instanzen_ueberlappen_nicht_und_meiden_florians_pfade(self):
        a, b = abgeleitete_pfade(self.lade(self.max)), abgeleitete_pfade(self.lade(self.eva))
        for name in a:
            self.assertFalse(liegt_in(a[name], self.eva) or liegt_in(b[name], self.max), name)
            for florian in FLORIAN_PFADE:
                self.assertFalse(liegt_in(a[name], florian) or liegt_in(b[name], florian), f"{name} in {florian}")


# --- Zugangsdaten ---------------------------------------------------------------------------------------------

class Zugangsdaten(MitInstanzen):
    def test_nur_eigene_zugaenge_florians_env_und_lokal_toml_nie_gelesen(self):
        projekt = self.tmp / "projekt"
        (projekt / "config").mkdir(parents=True)
        shutil.copy(konfig_mod.STANDARD_KONFIG, projekt / "config" / "pipeline.toml")
        (projekt / "config" / "lokal.toml").write_text('[lager]\nwurzel = "/srv/big/clips"\n[big]\nssh_ziel = '
                                                       '"root@pve-big"\n[replay]\nich = "florian-test-epic"\n')
        (projekt / ".env").write_text("TELEGRAM_BOT_TOKEN=florian-test-clipbot\nLEARN_BOT_ALLOWED_USER_ID=111\n")
        gelesen: list[Path] = []

        def lesen(pfad, *args, **kwargs):
            gelesen.append(Path(pfad))
            return ECHTES_READ_TEXT(pfad, *args, **kwargs)

        with mock.patch.object(konfig_mod, "PROJEKT", projekt), \
                mock.patch.object(konfig_mod, "STANDARD_KONFIG", projekt / "config" / "pipeline.toml"), \
                mock.patch.object(Path, "read_text", autospec=True, side_effect=lesen), als_instanz(self.max):
            k = konfig_mod.lade()
            umgebung = {n: os.environ.get(n) for n in (*FLORIAN_UMGEBUNG, "LEARN_BOT_ALLOWED_USER_ID")}
        self.assertIn(self.max / ".env", gelesen)
        self.assertNotIn(projekt / ".env", gelesen)
        self.assertNotIn(projekt / "config" / "lokal.toml", gelesen)
        self.assertEqual(umgebung, {**dict.fromkeys(FLORIAN_UMGEBUNG), "LEARN_BOT_TOKEN": "max-test-lernbot",
                                    "CLIP_EPIC_ID": "max-test-epic", "LEARN_BOT_ALLOWED_USER_ID": "222"})
        self.assertEqual((k.wert("replay.ich"), k.wert("big.ssh_ziel"), k.lager_wurzel),
                         ("max-test-epic", "", self.max / "kein-lager"))

    def test_env_fremder_name_oder_unlesbar_ist_konfigfehler(self):
        (self.max / ".env").write_text("LEARN_BOT_TOKEN=max-test-lernbot\nCLIP_SPEICHER=/srv/puffer\n")
        with self.assertRaisesRegex(KonfigFehler, "CLIP_SPEICHER gehört nicht"):
            self.lade(self.max)
        with mock.patch.object(konfig_mod, "lies_env", side_effect=PermissionError(13, "Keine Berechtigung")), \
                self.assertRaisesRegex(KonfigFehler, "nicht lesbar"):
            self.lade(self.max)
        (self.max / ".env").write_text(MAX_ENV)

        def lesen(pfad, *args, **kwargs):   # instanz.toml gehört der falschen Gruppe
            if pfad.name == "instanz.toml":
                raise PermissionError(13, "Keine Berechtigung")
            return ECHTES_READ_TEXT(pfad, *args, **kwargs)

        with mock.patch.object(Path, "read_text", autospec=True, side_effect=lesen), \
                self.assertRaisesRegex(KonfigFehler, "instanz.toml nicht lesbar"):
            self.lade(self.max)

    @unittest.skipIf(lernbot is None, "python-telegram-bot nicht installiert")
    def test_lernbot_ohne_eigenen_token_endet_mit_2(self):
        (self.eva / ".env").write_text("TELEGRAM_ALLOWED_USER_ID=333\n")   # Florians Bot-Token steht in der Umgebung
        with als_instanz(self.eva), mock.patch.object(lernbot, "baue_app", side_effect=AssertionError("Bot gebaut")), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(lernbot.starte(konfig_mod.lade()), 2)


# --- Manipulation ---------------------------------------------------------------------------------------------

class Manipulation(MitInstanzen):
    def schreibe_toml(self, inhalt: str) -> None:
        (self.max / "instanz.toml").write_text(inhalt, encoding="utf-8")

    def test_instanz_toml_darf_nur_erlaubtes(self):
        for zusatz in ('[datenbank]\npfad = "/var/lib/clip-pipeline/pipeline.db"', '[lager]\nwurzel = "/srv/big/clips"',
                       '[big]\nssh_ziel = "root@pve-big"', '[speicher]\nwurzel = "/srv/puffer"',
                       '[decide]\nprogramm = "claude"', "[gibtsnicht]\nx = 1", "[schnitt]\nunbekannt = 1",
                       "[merkmale]\nmic = false", '[instanz]\nwurzel = "/tmp"'):
            with self.subTest(zusatz=zusatz.split("\n")[0]):
                self.schreibe_toml(f'[sperre]\ndatei = "{self.sperrdatei}"\n{zusatz}\n')
                with self.assertRaisesRegex(KonfigFehler, "nicht erlaubt"):
                    self.lade(self.max)
        # die Vorlage passt – mit Florians Sperrdatei aus dem Temp-Ordner (es muss sie geben, M41)
        vorlage = (PROJEKT / "config" / "instanz.beispiel.toml").read_text(encoding="utf-8")
        self.assertIn('datei = "/var/lib/clip-pipeline/pipeline.lock"', vorlage)
        self.schreibe_toml(vorlage.replace("/var/lib/clip-pipeline/pipeline.lock", str(self.sperrdatei)))
        k = self.lade(self.max)
        self.assertEqual((k.wert("schnitt.encoder"), k.wert("sperre.warten_s"), k.wert("zeit.zeitzone")),
                         ("h264_vaapi", 900, "Europe/Berlin"))

    def test_falscher_aufruf_endet_mit_exit_2(self):
        for name in ("CLIP_KONFIG", "CLIP_SPEICHER", "CLIP_DATENBANK"):
            with self.subTest(name=name), self.assertRaisesRegex(KonfigFehler, name):
                self.lade(self.max, **{name: "/srv/puffer"})
        ausgabe = io.StringIO()
        with als_instanz(self.max), contextlib.redirect_stdout(ausgabe), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(["--konfig", "config/pipeline.toml", "scan"])
        self.assertEqual(code, 2)
        self.assertIn("--konfig", json.loads(ausgabe.getvalue().strip().splitlines()[-1])["fehler"])

    def test_leeres_clip_instanz_ist_ein_fehler_nie_florians_konfig(self):
        """Gesetzt, aber leer (z. B. ein Fehler in einer Vorlage): früher lief das still mit Florians .env und
        Datenbank (Prüfung S2, M42). Jetzt Exit 2 mit Klartext, Florians .env wird nie gelesen."""
        for wert in ("", "   "):
            with self.subTest(wert=repr(wert)):
                ausgabe = io.StringIO()
                with mock.patch.dict(os.environ, {"CLIP_INSTANZ": wert}), \
                        mock.patch.object(konfig_mod, "lade_env", side_effect=AssertionError("Florians .env")), \
                        contextlib.redirect_stdout(ausgabe), contextlib.redirect_stderr(io.StringIO()):
                    code = cli.main(["scan"])
                self.assertEqual(code, 2)
                self.assertIn("CLIP_INSTANZ", json.loads(ausgabe.getvalue().strip().splitlines()[-1])["fehler"])

    def test_ordner_marke_und_sperre_werden_geprueft(self):
        (self.max / ".clip-benutzer").write_text("eva\n")
        with self.assertRaisesRegex(KonfigFehler, "nennt 'eva'"):
            self.lade(self.max)
        (self.max / ".clip-benutzer").unlink()
        with self.assertRaisesRegex(KonfigFehler, "fehlt"):
            self.lade(self.max)
        for roh in ("freunde/max", "/var/lib/clip-pipeline/max", str(self.tmp / "Max")):
            with self.subTest(roh=roh), self.assertRaises(KonfigFehler):
                self.lade(Path(roh))
        (self.max / ".clip-benutzer").write_text("max\n")
        for zeile, muster in (("", r"\[sperre\]\.datei fehlt"), ('datei = "pipeline.lock"', "absoluter Pfad"),
                              (f'datei = "{self.max / "db" / "eigen.lock"}"', "außerhalb der Instanz")):
            self.schreibe_toml(f"[sperre]\n{zeile}\n")
            with self.subTest(sperre=zeile), self.assertRaisesRegex(KonfigFehler, muster):
                self.lade(self.max)

    def test_sperre_mit_tippfehler_ergibt_nie_eine_eigene(self):
        """Florian rechnet gerade. Ein Tippfehler im Sperrpfad legte früher still eine eigene Sperre an, und der Freund
        rechnete daneben her (Prüfung S1, M41). Jetzt Exit 2 mit Klartext, nirgends eine neue Sperrdatei."""
        self.schreibe_toml(f'[sperre]\ndatei = "{self.sperrdatei.with_name("pipline.lock")}"\nwarten_s = 0\n')
        ausgabe = io.StringIO()
        with open(self.sperrdatei) as florian:
            fcntl.flock(florian, fcntl.LOCK_EX)
            with als_instanz(self.max), contextlib.redirect_stdout(ausgabe), contextlib.redirect_stderr(io.StringIO()):
                code = cli.main(["scan"])
        self.assertEqual(code, 2)
        self.assertIn("[sperre].datei", json.loads(ausgabe.getvalue().strip().splitlines()[-1])["fehler"])
        self.assertEqual(sorted(self.tmp.rglob("*.lock")), [self.sperrdatei])

    def test_pfadwaechter_link_hinaus_und_vorhandenes_lager(self):
        aussen = self.tmp / "aussen"
        aussen.mkdir()
        shutil.rmtree(self.max / "regie")
        (self.max / "regie").symlink_to(aussen)
        with self.assertRaisesRegex(KonfigFehler, "außerhalb der Instanz"):
            self.lade(self.max)
        (self.max / "regie").unlink()
        (self.max / "kein-lager").mkdir()
        with self.assertRaisesRegex(KonfigFehler, "kein Lager"):
            self.lade(self.max)

    def test_claude_programm_nie_aus_florians_bereich_oder_home(self):
        for pfad in ("/home/pipeline/.local/bin/claude", "/var/lib/clip-pipeline/claude/claude", "/root/claude",
                     "/opt/clip-regie/claude", "claude"):
            self.schreibe_toml(f'[sperre]\ndatei = "{self.sperrdatei}"\n[instanz]\nclaude = "{pfad}"\n')
            with self.subTest(pfad=pfad), self.assertRaisesRegex(KonfigFehler, r"\[instanz\]\.claude"):
                self.lade(self.max)


# --- Puffer-Betrieb ohne Lager (wie Prototyp zusammen/kein_lager_versuch.py) ------------------------------------

class PufferOhneLager(MitInstanzen):
    def test_puffer_ja_lager_nie(self):
        k = self.lade(self.max)
        vorher = abdruck(self.tmp, ohne=self.max)
        self.assertTrue(k.getrennt)
        k.pruefe_getrennt(mit_lager=False)
        with self.assertRaises(KonfigFehler):
            k.pruefe_getrennt()
        with self.assertRaises(big.WeckenVerboten):
            with big.wach_halten(k, "test", "Probe"):
                pass
        con = db.verbinde(k.datenbank)
        self.addCleanup(con.close)
        with self.assertRaises(SpeicherOffline), contextlib.redirect_stderr(io.StringIO()):
            lager.abgleich(con, k)
        with self.assertRaises(KonfigFehler):
            aufraeumen.pruefe_erlaubt(k)
        self.assertFalse(os.path.lexists(self.max / "kein-lager"))
        self.assertEqual(abdruck(self.tmp, ohne=self.max), vorher)   # außerhalb der Instanz nichts geschrieben


# --- KI nur mit eigenem Claude-Zugang -------------------------------------------------------------------------

class KI(MitInstanzen):
    def setUp(self):
        super().setUp()
        self.arbeit = self.tmp / "arbeit"
        self.arbeit.mkdir()

    def frage(self, k, programm: str = "/opt/fake/claude"):
        with FakeClaude().aktiv(programm) as (lauf, which):
            antwort = claude_aufruf.frage_json(k, "Lies", self.arbeit, schema_name="publikum", timeout_s=5)
        return antwort, lauf, which

    def test_ohne_token_startet_claude_nie(self):
        k = self.lade(self.max)
        con = db.verbinde(k.datenbank)
        self.addCleanup(con.close)
        liste, video = self.max / "regie" / "short-1.json", self.max / "regie" / "short-1.mp4"
        liste.write_text('{"segmente": [], "format": "short"}', encoding="utf-8")
        video.write_bytes(b"kein echtes Video")   # ohne Sperre im Weg würde hier gemessen (ffmpeg) und gefragt
        eid = con.execute("INSERT INTO entwuerfe (name, format, schnittliste, parameter, status, erstellt, datei) "
                          "VALUES ('short-1', 'short', ?, '{}', 'gesendet', ?, ?)",
                          (str(liste), iso(jetzt()), str(video))).lastrowid
        with mock.patch("subprocess.run") as lauf, mock.patch("subprocess.Popen") as prozess, \
                mock.patch.object(claude_aufruf.shutil, "which", return_value="/opt/fake/claude"):
            antwort = claude_aufruf.frage_json(k, "Lies", self.arbeit, schema_name="publikum", timeout_s=5)
            note = geschmack.ki_nachtragen(k, eid)
            zeile = geschmack.lehrer_zeile(con, k)
        self.assertEqual((antwort.hinweis, antwort.gestartet, note), (claude_aufruf.KEIN_ZUGANG, False, None))
        self.assertFalse(lauf.called or prozess.called)
        self.assertIn("KI-Note: aus – verbinde dein Claude mit /claude", zeile)   # Schritt 9: der Weg steht dabei
        self.assertNotIn("/tiktok", zeile)

    def test_mit_token_eigene_umgebung_nie_florians(self):
        (self.max / "db" / "claude-token").write_text(MAX_TOKEN + "\n", encoding="utf-8")
        k = self.lade(self.max)
        florian = {**FLORIAN_UMGEBUNG, "HOME": "/home/pipeline", "PATH": "/home/pipeline/.local/bin:/usr/bin"}
        with mock.patch.dict(os.environ, florian):
            antwort, lauf, which = self.frage(k)
        self.assertEqual(antwort.daten, GUELTIG)
        env = lauf.call_args.kwargs["env"]
        self.assertEqual(set(env), {"CLAUDE_CODE_OAUTH_TOKEN", "HOME", "CLAUDE_CONFIG_DIR", "PATH", "LANG",
                                    "DISABLE_AUTOUPDATER"})
        self.assertEqual((env["CLAUDE_CODE_OAUTH_TOKEN"], env["DISABLE_AUTOUPDATER"]), (MAX_TOKEN, "1"))
        self.assertTrue(liegt_in(env["HOME"], self.max / "cache") and liegt_in(env["CLAUDE_CONFIG_DIR"], self.max))
        self.assertFalse(set(env.values()) & set(florian.values()))
        self.assertNotIn("/home", env["PATH"])
        which.assert_called_once_with("claude", path=claude_aufruf.SUCHPFAD)
        con = db.verbinde(k.datenbank)
        self.addCleanup(con.close)
        self.assertIn("· KI-Note (kommt mit dem nächsten Video) · Zuschauern (noch kein Video ausgewertet)",
                      geschmack.lehrer_zeile(con, k))

    def test_token_aus_env_wirkt_ohne_neustart_und_bleibt_aus_der_umgebung(self):
        k = self.lade(self.max)
        self.assertEqual(self.frage(k)[0].hinweis, claude_aufruf.KEIN_ZUGANG)
        with (self.max / ".env").open("a", encoding="utf-8") as datei:
            datei.write(f"CLAUDE_CODE_OAUTH_TOKEN={MAX_TOKEN}\n")
        _, lauf, _ = self.frage(k)   # dieselbe Konfig – das Token wird erst beim Aufruf gelesen
        self.assertEqual(lauf.call_args.kwargs["env"]["CLAUDE_CODE_OAUTH_TOKEN"], MAX_TOKEN)
        with als_instanz(self.max):
            konfig_mod.lade()
            self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", os.environ)   # Florians ist weg, das eigene kommt nie hinein

    def test_claude_unter_home_wird_nicht_gestartet(self):
        (self.max / "db" / "claude-token").write_text(MAX_TOKEN, encoding="utf-8")
        antwort, lauf, _ = self.frage(self.lade(self.max), programm="/home/pipeline/.local/bin/claude")
        self.assertIn("/home", antwort.hinweis)
        self.assertFalse(lauf.called)


class FlorianUnveraendert(MitSpeicher):
    def test_claude_aufruf_wie_bisher_ohne_eigene_umgebung(self):
        (self.tmp / "arbeit").mkdir()
        with FakeClaude().aktiv() as (lauf, which):
            antwort = claude_aufruf.frage_json(self.konfig, "Lies", self.tmp / "arbeit", schema_name="publikum",
                                               timeout_s=5)
        self.assertEqual(antwort.daten, GUELTIG)
        self.assertNotIn("env", lauf.call_args.kwargs)
        which.assert_called_once_with(str(self.konfig.wert("decide.programm")))
        self.assertIsNone(self.konfig.instanz)


# --- Migration: ohne CLIP_INSTANZ alles wie vorher ---------------------------------------------------------------

class Migration(unittest.TestCase):
    def test_ohne_instanz_dieselben_werte_und_dieselbe_umgebung(self):
        with tempfile.TemporaryDirectory() as t:
            projekt = Path(t) / "projekt"
            (projekt / "config").mkdir(parents=True)
            standard = projekt / "config" / "pipeline.toml"
            shutil.copy(konfig_mod.STANDARD_KONFIG, standard)
            (projekt / "config" / "lokal.toml").write_text(
                '[speicher]\nhost = "192.168.1.20"\nwol_mac = "aa:bb:cc:dd:ee:ff"\n[lager]\nwurzel = "/srv/big/clips"\n'
                '[big]\nssh_ziel = "root@pve-big"\n')
            (projekt / ".env").write_text("LEARN_BOT_TOKEN=florian-test-lernbot\nCLIP_EPIC_ID=florian-test-epic\n"
                                          "TELEGRAM_ALLOWED_USER_ID=111\n")
            with mock.patch.object(konfig_mod, "PROJEKT", projekt), mock.patch.object(konfig_mod, "STANDARD_KONFIG",
                                                                                     standard), \
                    mock.patch.dict(os.environ, {"TELEGRAM_ALLOWED_USER_ID": "999"}):
                for name in ("CLIP_INSTANZ", "CLIP_KONFIG", "CLIP_SPEICHER", "CLIP_DATENBANK", "LEARN_BOT_TOKEN",
                             "CLIP_EPIC_ID"):
                    os.environ.pop(name, None)
                vorher = dict(os.environ)
                k = konfig_mod.lade()
                nachher = dict(os.environ)
            erwartet = tomllib.loads(standard.read_text(encoding="utf-8"))
            erwartet["speicher"].update(host="192.168.1.20", wol_mac="aa:bb:cc:dd:ee:ff")
            erwartet["lager"]["wurzel"], erwartet["big"]["ssh_ziel"] = "/srv/big/clips", "root@pve-big"
            erwartet["replay"]["ich"] = "florian-test-epic"
            self.assertEqual((k.daten, k.quelle), (erwartet, standard))
            # .env füllt nur Lücken – die Umgebung geht vor (TELEGRAM_ALLOWED_USER_ID bleibt 999)
            self.assertEqual(nachher, {**vorher, "LEARN_BOT_TOKEN": "florian-test-lernbot",
                                       "CLIP_EPIC_ID": "florian-test-epic"})
            self.assertIsNone(k.instanz)
            self.assertEqual(sperre.pfad(k), k.datenbank.parent / "pipeline.lock")


if __name__ == "__main__":
    unittest.main()
