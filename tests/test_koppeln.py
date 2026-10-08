"""Mehrbenutzer, Stufe 1, Schritt 8: Einladungslink statt Telegram-Zahl – `pipeline benutzer koppeln`.

Telegram ist eine Attrappe auf 127.0.0.1 (getMe, getUpdates mit offset = abhaken, sendMessage); koppeln spricht mit ihr
über dieselben Anfragen wie der Lern-Bot (lernbot.anfragen, IPv4). Der „Freund“ liest den Link aus I/db/einladung.json,
wie benutzer-anlegen.sh ihn Florian zeigt, und schickt „/start <code>“.
"""

from __future__ import annotations

import contextlib
import http.server
import io
import json
import logging
import os
import re
import stat
import tempfile
import threading
import time
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

from clip_pipeline import benutzer, cli, db
from clip_pipeline import konfig as konfig_mod

from tests.hilfen import als_instanz, instanz_anlegen

TOKEN = "700000001:AAmaxTESTtokenNURfuerTESTSabcdefgh12"
FLORIAN_TOKEN = "700000009:AAflorianLERNbotTESTtokenNURtestsXY"
MAX_ZAHL = 222333444
MAX_ENV = f"LEARN_BOT_TOKEN={TOKEN}\nCLIP_EPIC_ID=0123456789abcdef0123456789abcdef\n"
LINK = re.compile(r"https://t\.me/max_clips_bot\?start=([A-Za-z0-9_-]{32})")


class Telegram(http.server.BaseHTTPRequestHandler):
    """Bot-API wie bei Telegram, nur für den Token von max. Werte kommen als Formular (Zahlen und Listen als JSON)."""

    def do_POST(self):
        fake: FakeTelegram = self.server.fake
        roh = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8")
        werte = {}
        for name, (wert,) in urllib.parse.parse_qs(roh).items():
            try:
                werte[name] = json.loads(wert)
            except ValueError:
                werte[name] = wert
        token, _, methode = self.path.removeprefix("/bot").partition("/")
        fake.anfragen.append(methode)
        code, antwort = fake.antwort(token, methode, werte)
        daten = json.dumps(antwort).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(daten)))
        self.end_headers()
        self.wfile.write(daten)

    def log_message(self, *_):
        pass


class FakeTelegram:
    """Offene Updates bleiben liegen, bis eine Abfrage mit höherem offset sie abhakt – wie bei Telegram.
    beim_abholen(fake) läuft bei jedem getUpdates: dort „tippt der Freund“."""

    def __init__(self, test: unittest.TestCase):
        self.updates: list[dict] = []
        self.naechste = 1
        self.gesendet: list[tuple[int, str]] = []
        self.anfragen: list[str] = []
        self.konflikt = False
        self.beim_abholen = None
        self.fehler: list[BaseException] = []   # aus beim_abholen (läuft im Server-Thread) – der Test meldet sie
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Telegram)
        server.fake = self
        threading.Thread(target=server.serve_forever, daemon=True).start()
        test.addCleanup(server.server_close)
        test.addCleanup(server.shutdown)
        self.url = f"http://127.0.0.1:{server.server_address[1]}"

    def nachricht(self, text: str, *, von: int = MAX_ZAHL, chat: dict | None = None, vorname: str = "Max") -> None:
        chat = chat or {"id": von, "type": "private", "first_name": vorname}
        self.updates.append({"update_id": self.naechste, "message": {
            "message_id": self.naechste, "date": int(time.time()), "chat": chat, "text": text,
            "from": {"id": von, "is_bot": False, "first_name": vorname}}})
        self.naechste += 1

    def antwort(self, token: str, methode: str, werte: dict) -> tuple[int, dict]:
        if token != TOKEN:
            return 401, {"ok": False, "error_code": 401, "description": "Unauthorized"}
        if methode == "getMe":
            return 200, {"ok": True, "result": {"id": 700000001, "is_bot": True, "first_name": "Max Clips",
                                                "username": "max_clips_bot"}}
        if methode == "sendMessage":
            self.gesendet.append((int(werte["chat_id"]), werte["text"]))
            return 200, {"ok": True, "result": {"message_id": 900, "date": int(time.time()), "text": werte["text"],
                                                "chat": {"id": int(werte["chat_id"]), "type": "private"}}}
        if methode == "getUpdates":
            if self.konflikt:
                return 409, {"ok": False, "error_code": 409,
                             "description": "Conflict: terminated by other getUpdates request"}
            if (offset := werte.get("offset")) is not None:
                self.updates = [u for u in self.updates if u["update_id"] >= int(offset)]
            if self.beim_abholen:
                try:
                    self.beim_abholen(self)
                except BaseException as e:  # noqa: BLE001 – im Test-Thread erneut ausgelöst (Koppeln.lauf)
                    self.fehler.append(e)
            if not self.updates:
                time.sleep(min(float(werte.get("timeout") or 0), 0.05))
            return 200, {"ok": True, "result": self.updates}
        return 404, {"ok": False, "error_code": 404, "description": "Not Found"}


class Koppeln(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(os.path.realpath(self._tmp.name))
        sperre = self.tmp / "pipeline.lock"
        sperre.touch()
        self.inst = instanz_anlegen(self.tmp / "benutzer", "max", sperre=sperre, env=MAX_ENV)
        self.env_vorher = (self.inst / ".env").read_bytes()
        self.telegram = FakeTelegram(self)
        for patch in (mock.patch.object(benutzer, "TELEGRAM_API", self.telegram.url),
                      mock.patch.object(benutzer, "KOPPELN_FRIST_S", 1.4)):
            patch.start()
            self.addCleanup(patch.stop)
        self.links: list[str] = []   # was benutzer-anlegen.sh Florian zeigen würde

    def lauf(self) -> tuple[int, dict, str]:
        """pipeline benutzer koppeln als max – Exit, JSON-Zeile, stdout und JEDE Log-Zeile (auch DEBUG)."""
        zeilen: list[str] = []
        sammler = logging.Handler(logging.DEBUG)
        sammler.emit = lambda r: zeilen.append(r.getMessage())
        wurzel = logging.getLogger()
        stufe = wurzel.level
        wurzel.addHandler(sammler)
        wurzel.setLevel(logging.DEBUG)   # strenger als im Dienst (INFO): auch DEBUG darf keinen Code zeigen
        ausgabe = io.StringIO()
        try:
            with als_instanz(self.inst, INVOCATION_ID="lauf-7"), contextlib.redirect_stdout(ausgabe), \
                    contextlib.redirect_stderr(io.StringIO()):
                code = cli.main(["benutzer", "koppeln"])
        finally:
            wurzel.removeHandler(sammler)
            wurzel.setLevel(stufe)
        if self.telegram.fehler:
            raise self.telegram.fehler[0]
        text = ausgabe.getvalue()
        return code, json.loads(text.strip().splitlines()[-1]), text + "\n".join(zeilen)

    def lies_link(self, fake: FakeTelegram) -> str:
        """Der Freund öffnet den Link: aus einladung.json (nur für ihn und root), mit der Lauf-Nummer dieses Laufs."""
        datei = self.inst / "db" / benutzer.EINLADUNG
        self.assertEqual(stat.S_IMODE(datei.stat().st_mode), 0o600)
        einladung = json.loads(datei.read_text(encoding="utf-8"))
        self.assertEqual(einladung["lauf"], "lauf-7")
        self.links.append(einladung["link"])
        return LINK.fullmatch(einladung["link"]).group(1)

    def assertNichtsGespeichert(self):
        self.assertFalse((self.inst / "db" / benutzer.KOPPLUNG).exists())
        self.assertFalse((self.inst / "db" / benutzer.EINLADUNG).exists())   # der Code galt nur in dem Lauf
        self.assertEqual((self.inst / ".env").read_bytes(), self.env_vorher)

    def test_richtiger_code_koppelt(self):
        def freund_tippt(fake):
            if not fake.gesendet and not fake.updates:
                fake.nachricht(f"/start {self.lies_link(fake)}")

        self.telegram.beim_abholen = freund_tippt
        code, erg, ausgaben = self.lauf()
        self.assertEqual(code, 0, erg)
        self.assertEqual((erg["ok"], erg["gekoppelt"], erg["name"]), (True, True, "max"))
        kopplung = self.inst / "db" / benutzer.KOPPLUNG
        self.assertEqual(stat.S_IMODE(kopplung.stat().st_mode), 0o600)
        daten = json.loads(kopplung.read_text(encoding="utf-8"))
        self.assertEqual((daten["id"], daten["vorname"]), (MAX_ZAHL, "Max"))
        self.assertEqual(self.telegram.gesendet, [(MAX_ZAHL, benutzer.VERBUNDEN)])
        self.assertEqual(self.telegram.updates, [])   # abgehakt: sein Bot sieht den Code später nicht
        self.assertFalse((self.inst / "db" / benutzer.EINLADUNG).exists())
        self.assertEqual((self.inst / ".env").read_bytes(), self.env_vorher)   # die .env schreibt nur root
        # Code, Link, Bot-Token und Zahl stehen nie in Ausgabe oder Log
        (link,) = self.links
        for geheim in (link, LINK.fullmatch(link).group(1), TOKEN, str(MAX_ZAHL)):
            self.assertNotIn(geheim, ausgaben)

    def test_falscher_alter_code_oder_fremde_nachricht_speichert_nichts(self):
        alt: list[str] = []
        self.telegram.beim_abholen = lambda fake: alt or alt.append(self.lies_link(fake))   # niemand tippt
        code, erg, _ = self.lauf()
        self.assertEqual(code, 1, erg)   # Frist abgelaufen
        self.assertEqual((erg["ok"], erg["gekoppelt"]), (False, False))
        self.assertIn("Frist abgelaufen", erg["hinweis"])
        self.assertNichtsGespeichert()

        def fremde_tippen(fake):
            if fake.naechste == 1:
                neu = self.lies_link(fake)
                self.assertNotEqual(neu, alt[0])   # jede Einladung hat einen neuen Code
                fake.nachricht("/start falschFALSCHfalschFALSCHfalsch12", von=999, vorname="Fremd")
                fake.nachricht(f"/start {alt[0]}")                                       # sein alter Link
                fake.nachricht(f"/start {neu}", von=555, chat={"id": -100777, "type": "group", "title": "Gruppe"})
                fake.nachricht(f"hallo {neu}", von=777)                                 # kein /start

        self.telegram.beim_abholen = fremde_tippen
        code, erg, ausgaben = self.lauf()
        self.assertEqual(code, 1, erg)
        self.assertIn("Frist abgelaufen", erg["hinweis"])
        self.assertNichtsGespeichert()
        # Ein falscher Start bekommt einmal „gilt nicht“, alles andere keine Antwort – nie „Verbunden“
        self.assertEqual(self.telegram.gesendet, [(999, benutzer.FALSCHER_LINK), (MAX_ZAHL, benutzer.FALSCHER_LINK)])
        self.assertEqual(self.telegram.updates, [])   # gelesen und abgehakt
        for link in self.links:
            self.assertNotIn(LINK.fullmatch(link).group(1), ausgaben)

    def test_schon_verbunden_oder_anderer_empfaenger(self):
        # Steht schon eine Zahl in .env, kann sein Bot laufen – dann fragt koppeln Telegram gar nicht erst
        (self.inst / ".env").write_text(MAX_ENV + f"LEARN_BOT_ALLOWED_USER_ID={MAX_ZAHL}\n", encoding="utf-8")
        self.env_vorher = (self.inst / ".env").read_bytes()
        code, erg, _ = self.lauf()
        self.assertEqual(code, 1, erg)
        self.assertIn("schon mit Telegram verbunden", erg["hinweis"])
        self.assertEqual(self.telegram.anfragen, [])
        self.assertNichtsGespeichert()
        # Holt ein anderes Programm die Nachrichten ab (Telegram: 409), bricht koppeln ab, ohne etwas zu speichern
        (self.inst / ".env").write_text(MAX_ENV, encoding="utf-8")
        self.env_vorher = (self.inst / ".env").read_bytes()
        self.telegram.konflikt = True
        code, erg, _ = self.lauf()
        self.assertEqual(code, 1, erg)
        self.assertIn("nur ein Empfänger", erg["hinweis"])
        self.assertNichtsGespeichert()

    def test_ohne_instanz_exit_2_florians_env_unberuehrt(self):
        florian = self.tmp / "florian"
        (florian / "config").mkdir(parents=True)
        env = florian / ".env"
        env.write_text(f"LEARN_BOT_TOKEN={FLORIAN_TOKEN}\nLEARN_BOT_ALLOWED_USER_ID=111222333\n", encoding="utf-8")
        vorher = (env.read_bytes(), env.stat().st_mtime_ns)
        ausgabe = io.StringIO()
        with mock.patch.dict(os.environ), mock.patch.object(konfig_mod, "PROJEKT", florian), \
                mock.patch.object(db, "verbinde", side_effect=AssertionError("Datenbank")), \
                contextlib.redirect_stdout(ausgabe), contextlib.redirect_stderr(io.StringIO()):
            os.environ.pop("CLIP_INSTANZ", None)
            code = cli.main(["benutzer", "koppeln"])
        erg = json.loads(ausgabe.getvalue().strip().splitlines()[-1])
        self.assertEqual(code, 2)
        self.assertEqual(erg["fehler"], "konfig")
        self.assertIn("CLIP_INSTANZ", erg["hinweis"])
        self.assertEqual((env.read_bytes(), env.stat().st_mtime_ns), vorher)
        self.assertEqual(sorted(p.name for p in florian.iterdir()), [".env", "config"])
        self.assertEqual(self.telegram.anfragen, [])   # Florians Bot wird nie gefragt


if __name__ == "__main__":
    unittest.main()
