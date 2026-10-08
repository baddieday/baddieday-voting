"""Mehrbenutzer, Stufe 1, Schritt 9: eigener Claude-Zugang per /claude im Bot eines Freundes (lernbot_claude).

claude ist eine Attrappe ([instanz].claude, ein kleines Python-Skript): Wie `claude setup-token` gibt sie im Terminal
einen Anmelde-Link aus – mit den Steuerzeichen des echten (Cursor-Sprünge statt Leerzeichen, Hyperlink-Folge vor dem
sichtbaren Link, Abfragen ans Terminal) –, liest eine Zeile und gibt ein Token aus (oder eine Fehlermeldung, oder gar
nichts). Danach läuft sie weiter wie das echte; beenden muss sie der Bot. Telegram ist eine Attrappe ohne Netz
(send_message, delete_message). Kein Test startet ein echtes claude; Token, Code und Link sind Test-Werte.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import stat
import sys
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from clip_pipeline import claude_aufruf

from tests.hilfen import MitSpeicher
from tests.test_instanz import FLORIAN_UMGEBUNG, MitInstanzen

try:
    from telegram.ext import ApplicationHandlerStop

    from clip_pipeline import lernbot, lernbot_claude
except ImportError:  # python-telegram-bot nicht installiert
    lernbot = None

MAX_ZAHL = 222
TOKEN = "sk-ant-oat01-" + "Ab3_dE-fGh" * 9 + "-xYz123AA"   # Form wie aus `claude setup-token`
CODE = "q7Wz2KpL9xVb4NcR8tYs3MdF6gHj1#St4tEoAuthTestW3rt"  # Form wie auf der Anmeldeseite
LINK = ("https://claude.com/cai/oauth/authorize?code=true&client_id=00000000-test-0000-0000-000000000000"
        "&response_type=code&redirect_uri=https%3A%2F%2Fplatform.claude.com%2Foauth%2Fcode%2Fcallback"
        "&scope=user%3Ainference&code_challenge=TestChallenge0123456789&code_challenge_method=S256&state=TestState42")

# Die Attrappe: art "ok" (Token nach dem richtigen Code), "fehler" (Fehlermeldung wie das echte bei einem falschen Code),
# "stumm" (nach dem Code nichts mehr), "haengt" (nicht einmal ein Link). Sie merkt sich Nummer, Aufruf, Umgebung und
# Terminal-Breite in ihrem Arbeitsordner (I/cache) – nie den Code.
ATTRAPPE = r'''#!{python}
import json, os, sys, time
art = {art!r}
with open("attrappe.json", "w") as datei:
    json.dump({{"pid": os.getpid(), "argv": sys.argv[1:], "env": dict(os.environ),
               "spalten": os.get_terminal_size(1).columns}}, datei)
if art == "haengt":
    time.sleep(600)
w = sys.stdout.write
w("\x1b7\x1b8Welcome\x1b[9Gto\x1b[12GClaude\x1b[19GCode\r\r\n\r\r\n")
w("\x1b[2G\x1b[38;5;246mBrowser\x1b[10Gdidn't\x1b[17Gopen?\x1b[23GUse\x1b[27Gthe\x1b[31Gurl\x1b[35Gbelow\x1b[39m\r\r\n\r\r\n")
w("\x1b]8;id=t1;{link}\x07\x1b[38;5;246m{link}\x1b[39m\x1b]8;;\x07\r\r\n\r\r\n\r\r\n")
w("\x1b[2GPaste\x1b[8Gcode\x1b[13Ghere\x1b[18Gif\x1b[21Gprompted\x1b[30G>\r\r\n\x1b[>0q\x1b[?u\x1b[c")
sys.stdout.flush()
code = sys.stdin.readline().strip()
if art == "ok" and code == {code!r}:
    w("\x1b[2G\x1b[32m*\x1b[39m\x1b[4GLong-lived\x1b[15Gauthentication\x1b[30Gtoken\x1b[36Gcreated\r\r\n\r\r\n")
    w("Your\x1b[6GOAuth\x1b[12Gtoken:\r\r\n\r\r\n\x1b[1m{token}\x1b[22m\r\r\n\r\r\nStore\x1b[7Git\r\r\n")
elif art != "stumm":
    w("\x1b(B\x0f\r ****\r\r\n \r OAuth error: Request failed with status code 400\r\r Press Enter to retry. \r\r\r")
sys.stdout.flush()
time.sleep(600)
'''


async def bis(bedingung, frist_s: float = 15.0) -> None:
    ende = time.monotonic() + frist_s
    while not bedingung():
        if time.monotonic() > ende:
            raise AssertionError("Bedingung nicht erfüllt")
        await asyncio.sleep(0.02)


async def fertig(ablauf) -> None:
    """Bis die Anmeldung zu Ende ist – höchstens 30 s: endet sie nie (Frist fehlt), wird der Test rot statt zu hängen."""
    await asyncio.wait_for(ablauf.aufgabe, 30)


class FakeBot:
    def __init__(self):
        self.gesendet: list[tuple[int, str, dict]] = []
        self.geloescht: list[tuple[int, int]] = []
        self.loeschen_geht = True

    async def send_message(self, chat_id, text, **kw):
        self.gesendet.append((chat_id, text, kw))
        return SimpleNamespace(message_id=900 + len(self.gesendet))

    async def delete_message(self, chat_id, message_id, **_):
        if not self.loeschen_geht:
            raise RuntimeError("Message can't be deleted")   # wie Telegram bei einer zu alten Nachricht
        self.geloescht.append((chat_id, message_id))
        return True

    def texte(self) -> list[str]:
        return [t for _, t, _ in self.gesendet]


@unittest.skipIf(lernbot is None, "python-telegram-bot fehlt")
class ClaudeVerbinden(MitInstanzen):
    """max (Freund) mit der Attrappe als [instanz].claude; Florians Zugänge stehen in der Umgebung (MitInstanzen)."""

    def setUp(self):
        super().setUp()
        self.claude = self.tmp / "bin" / "claude"
        self.claude.parent.mkdir()
        self.attrappe("ok")
        with (self.max / "instanz.toml").open("a", encoding="utf-8") as datei:
            datei.write(f'[instanz]\nclaude = "{self.claude}"\n')
        self.k = self.lade(self.max)
        self.bot = FakeBot()
        self.app = SimpleNamespace(bot=self.bot, bot_data={"konfig": self.k, "erlaubt": MAX_ZAHL})
        self.context = SimpleNamespace(application=self.app, bot_data=self.app.bot_data, args=[])
        self.token_datei = self.max / "db" / claude_aufruf.TOKEN_DATEI

    def attrappe(self, art: str) -> None:
        self.claude.write_text(ATTRAPPE.format(python=sys.executable, art=art, link=LINK, code=CODE, token=TOKEN),
                               encoding="utf-8")
        self.claude.chmod(0o755)
        (self.max / "cache" / "attrappe.json").unlink(missing_ok=True)

    def nachricht(self, text: str, nr: int):
        return SimpleNamespace(effective_message=SimpleNamespace(text=text, chat_id=MAX_ZAHL, message_id=nr))

    def lauf(self, szenario) -> str:
        """szenario() in einem Event-Loop – Rückgabe: JEDE Log-Zeile dabei (auch DEBUG), mit Tracebacks."""
        zeilen: list[str] = []
        sammler = logging.Handler(logging.DEBUG)
        sammler.emit = lambda r: zeilen.append(logging.Formatter().format(r))
        wurzel = logging.getLogger()
        stufe = wurzel.level
        wurzel.addHandler(sammler)
        wurzel.setLevel(logging.DEBUG)
        try:
            asyncio.run(szenario())
        finally:
            wurzel.removeHandler(sammler)
            wurzel.setLevel(stufe)
        return "\n".join(zeilen)

    async def claude_tippen(self):
        """Der Freund tippt /claude; zurück kommt die laufende Anmeldung, sobald der Bot etwas geschickt hat."""
        vorher = len(self.bot.gesendet)
        await lernbot_claude.cmd_claude(self.nachricht("/claude", 1), self.context)
        ablauf = self.app.bot_data[lernbot_claude.ABLAUF]
        await bis(lambda: len(self.bot.gesendet) > vorher or ablauf.aufgabe.done())
        return ablauf

    def attrappe_lief(self) -> dict:
        return json.loads((self.max / "cache" / "attrappe.json").read_text(encoding="utf-8"))

    def assertBeendet(self, pid: int) -> None:
        with self.assertRaises(ProcessLookupError):   # beendet und abgeholt – kein Rest, kein Zombie
            os.kill(pid, 0)

    def assertNichtVerraten(self, log: str, *geheim: str) -> None:
        for wert in geheim:
            self.assertNotIn(wert, log)
            for text in self.bot.texte():
                if text != lernbot_claude.LINK_TEXT.format(link=LINK):   # den Link bekommt nur der Freund, einmal
                    self.assertNotIn(wert, text)

    def test_link_code_token(self):
        async def szenario():
            ablauf = await self.claude_tippen()
            ((chat, text, kw),) = self.bot.gesendet
            self.assertEqual((chat, text), (MAX_ZAHL, lernbot_claude.LINK_TEXT.format(link=LINK)))
            self.assertTrue(kw["link_preview_options"].is_disabled)
            with self.assertRaises(ApplicationHandlerStop):   # der Code kommt zu keinem anderen Handler
                await lernbot_claude.bei_text(self.nachricht(f" {CODE}\n", 77), self.context)
            await fertig(ablauf)

        log = self.lauf(szenario)
        self.assertEqual(self.token_datei.read_text(encoding="utf-8"), TOKEN + "\n")
        self.assertEqual(stat.S_IMODE(self.token_datei.stat().st_mode), 0o600)
        self.assertEqual(claude_aufruf.instanz_token(self.k), TOKEN)   # claude_aufruf nimmt es beim nächsten Aufruf
        self.assertEqual(self.bot.geloescht, [(MAX_ZAHL, 77)])         # die Code-Nachricht ist aus dem Chat
        self.assertEqual(self.bot.texte()[1:], [lernbot_claude.VERBUNDEN])
        lief = self.attrappe_lief()
        self.assertEqual((lief["argv"], lief["spalten"]), (["setup-token"], lernbot_claude.BREITE))
        # dieselbe kleine Umgebung wie claude_aufruf in der Instanz – nichts vom Bot (Bot-Token, Florians Zugänge)
        env = lief["env"]
        self.assertEqual(set(env) - {"LC_CTYPE"}, {"HOME", "CLAUDE_CONFIG_DIR", "PATH", "LANG", "DISABLE_AUTOUPDATER",
                                                   "TERM"})
        self.assertEqual((env["HOME"], env["CLAUDE_CONFIG_DIR"], env["DISABLE_AUTOUPDATER"]),
                         (str(self.max / "cache"), str(self.max / "cache" / "claude"), "1"))
        self.assertFalse(set(env.values()) & set(FLORIAN_UMGEBUNG.values()))
        self.assertBeendet(lief["pid"])
        self.assertNotIn(lernbot_claude.ABLAUF, self.app.bot_data)
        self.assertIn("/claude: Claude verbunden", log)
        self.assertNichtVerraten(log, TOKEN, CODE, LINK, CODE.split("#")[1])

    def test_ohne_token_nichts_gespeichert_und_claude_beendet(self):
        # falscher Code: claude meldet einen Fehler – das beendet das Warten sofort (sonst hier 30 s)
        faelle = (("fehler", {"TOKEN_FRIST_S": 30.0}, lernbot_claude.KEIN_TOKEN, True),
                  ("stumm", {"TOKEN_FRIST_S": 0.5}, lernbot_claude.KEIN_TOKEN, True),  # nach dem Code kommt nichts
                  ("haengt", {"LINK_FRIST_S": 0.5}, lernbot_claude.KEIN_LINK, False),  # nicht einmal ein Link
                  ("ok", {"FRIST_S": 3.0}, lernbot_claude.ABGELAUFEN, False))          # der Freund schickt keinen Code
        for art, fristen, antwort, mit_code in faelle:
            with self.subTest(art=art):
                self.bot.gesendet.clear()
                self.bot.geloescht.clear()
                self.attrappe(art)

                async def szenario():
                    with mock.patch.multiple(lernbot_claude, **fristen):
                        ablauf = await self.claude_tippen()
                        if mit_code:
                            with self.assertRaises(ApplicationHandlerStop):
                                await lernbot_claude.bei_text(self.nachricht(CODE, 78), self.context)
                        await fertig(ablauf)

                beginn = time.monotonic()
                log = self.lauf(szenario)
                self.assertLess(time.monotonic() - beginn, 15)
                self.assertFalse(self.token_datei.exists())
                self.assertEqual(self.bot.texte()[-1], antwort)
                self.assertIn(lernbot_claude.AUSWEG, antwort)            # Ausweg: Token direkt schicken
                self.assertEqual(self.bot.geloescht, [(MAX_ZAHL, 78)] if mit_code else [])
                self.assertBeendet(self.attrappe_lief()["pid"])
                self.assertNotIn(lernbot_claude.ABLAUF, self.app.bot_data)
                self.assertNichtVerraten(log, CODE, LINK)
        # claude gar nicht da (nicht ausführbar): Florian muss es installieren – nichts gestartet, nichts gespeichert
        self.claude.chmod(0o644)
        self.bot.gesendet.clear()

        async def ohne_claude():
            await fertig(await self.claude_tippen())

        self.lauf(ohne_claude)
        self.assertEqual(self.bot.texte(), [lernbot_claude.CLAUDE_FEHLT])
        self.assertIn("Florian muss Claude einmal auf dem Mini installieren", lernbot_claude.CLAUDE_FEHLT)
        self.assertFalse(self.token_datei.exists())

    def test_token_direkt_geschickt(self):
        async def szenario():
            # Ohne Anmeldung geht ein normaler Text weiter zu den anderen Handlern – nichts gelöscht, nichts gesagt
            self.assertIsNone(await lernbot_claude.bei_text(self.nachricht("hallo", 5), self.context))
            # „/claude <Token>“: gespeichert und gelöscht, claude startet gar nicht erst
            await lernbot_claude.cmd_claude(self.nachricht(f"/claude {TOKEN}", 6), self.context)
            self.assertNotIn(lernbot_claude.ABLAUF, self.app.bot_data)
            self.assertEqual(self.token_datei.read_text(encoding="utf-8"), TOKEN + "\n")
            self.token_datei.unlink()
            # Während einer Anmeldung: das Token gilt, die Anmeldung endet samt claude
            ablauf = await self.claude_tippen()
            self.bot.loeschen_geht = False   # scheitert das Löschen, bleibt es dabei
            with self.assertRaises(ApplicationHandlerStop):
                await lernbot_claude.bei_text(self.nachricht(f"hier: {TOKEN}", 79), self.context)
            self.assertTrue(ablauf.aufgabe.done())

        log = self.lauf(szenario)
        self.assertEqual(self.token_datei.read_text(encoding="utf-8"), TOKEN + "\n")
        self.assertEqual(stat.S_IMODE(self.token_datei.stat().st_mode), 0o600)
        self.assertEqual(self.bot.geloescht, [(MAX_ZAHL, 6)])
        link = lernbot_claude.LINK_TEXT.format(link=LINK)
        self.assertEqual(self.bot.texte(), [lernbot_claude.VERBUNDEN, link, lernbot_claude.VERBUNDEN])
        self.assertBeendet(self.attrappe_lief()["pid"])
        self.assertNotIn(lernbot_claude.ABLAUF, self.app.bot_data)
        self.assertNichtVerraten(log, TOKEN)


@unittest.skipIf(lernbot is None, "python-telegram-bot fehlt")
class NurBeiFreunden(MitInstanzen):
    def test_freund_hat_claude_statt_tiktok(self):
        k = self.lade(self.max)
        app = lernbot.baue_app(k, "123456:TEST", MAX_ZAHL)
        self.addCleanup(app.bot_data["con"].close)
        befehle = {c for h in app.handlers[0] for c in getattr(h, "commands", ())}
        self.assertIn("claude", befehle)
        self.assertNotIn("tiktok", befehle)
        self.assertEqual([type(h).__name__ for h in app.handlers[-1]], ["MessageHandler"])   # vor allen Text-Handlern
        for experte in (False, True):
            text = lernbot.hilfe_text(experte, freund=True)
            self.assertIn(lernbot.CLAUDE_ZEILE, text)
            self.assertNotIn("/tiktok", text)


@unittest.skipIf(lernbot is None, "python-telegram-bot fehlt")
class FlorianUnveraendert(MitSpeicher):
    def test_kein_claude_hilfe_wie_bisher(self):
        from clip_pipeline import lernbot_publikum

        app = lernbot.baue_app(self.konfig, "123456:TEST", 42)
        self.addCleanup(app.bot_data["con"].close)
        befehle = {c for h in app.handlers[0] for c in getattr(h, "commands", ())}
        self.assertIn("tiktok", befehle)
        self.assertNotIn("claude", befehle)
        self.assertEqual(list(app.handlers), [0])   # kein Handler vor den anderen
        self.assertEqual(lernbot.hilfe_text(False), lernbot.HILFE)
        self.assertEqual(lernbot.hilfe_text(True), lernbot.HILFE_EXPERTE + lernbot_publikum.HILFE_ZUSATZ)
        self.assertIn(lernbot.TIKTOK_ZEILE, lernbot.HILFE_EXPERTE)


if __name__ == "__main__":
    unittest.main()
