"""Mehrbenutzer, Stufe 2, Schritt 4: /pc im Lern-Bot eines Freundes (src/clip_pipeline/lernbot_pc.py).

Telegram ist eine Attrappe ohne Netz (send_document, send_message). Die Schlüssel sind Test-Werte in der Form eines
privaten OpenSSH-Schlüssels (Kopf und Fuß zur Laufzeit zusammengesetzt – diese Datei enthält keinen „echten“). Geprüft:
  - Das ZIP wird jedes Mal frisch gebaut und enthält genau seine Dateien: die drei Skripte aus windows/ (die .cmd mit
    CRLF), freund.psd1 mit öffentlicher Adresse, Port und bk-<name>, seinen PC-Schlüssel und known_hosts_pc – nie den
    Abhol-Schlüssel, nie etwas von eva.
  - Fehlt etwas (Schlüssel, öffentliche Adresse) oder passt known_hosts_pc nicht: kein Paket, ein Satz an ihn.
  - /pc gibt es nur im Bot eines Freundes mit Briefkasten; bei Florian nicht, seine Hilfe bleibt Zeichen für Zeichen.
  - 📋 Stand: „noch nicht verbunden – tipp /pc“ bzw. „zuletzt vor … · n unterwegs“.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from clip_pipeline import db
from clip_pipeline.zeit import iso, jetzt

from tests.hilfen import MitSpeicher
from tests.test_instanz import MitInstanzen

try:
    from clip_pipeline import lernbot, lernbot_pc
except ImportError:  # python-telegram-bot nicht installiert
    lernbot = None

PROJEKT = Path(__file__).resolve().parents[1]
PWSH = shutil.which("pwsh")
MAX_ZAHL = 222
KOPF = "-----BEGIN OPENSSH " + "PRIVATE KEY-----"
FUSS = "-----END OPENSSH " + "PRIVATE KEY-----"
HOSTKEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI" + "t" * 43
SKRIPTE = ("Freund-Hochladen.ps1", "Freund-Einrichten.ps1", "Freund-Einrichten.cmd")


def schluessel(merkmal: str) -> bytes:
    """Test-Wert in der Form eines privaten OpenSSH-Schlüssels, mit einem Merkmal, das sich wiederfinden lässt."""
    return f"{KOPF}\nb3BlbnNzaC1rZXktdjEAAAAA{merkmal}TestWert\n{FUSS}\n".encode()


class FakeBot:
    def __init__(self, document_geht: bool = True):
        self.dokumente: list[tuple[int, bytes, dict]] = []
        self.texte: list[str] = []
        self.document_geht = document_geht

    async def send_document(self, chat_id, document, **kw):
        if not self.document_geht:
            raise RuntimeError("Request Entity Too Large")
        self.dokumente.append((chat_id, document, kw))

    async def send_message(self, chat_id, text, **kw):
        self.texte.append(text)


@unittest.skipIf(lernbot is None, "python-telegram-bot fehlt")
class PcPaket(MitInstanzen):
    """max mit Briefkasten (Tailnet 100.64.0.7, öffentlich vserver.example.org), eva ohne – beide mit Schlüsseln."""

    def setUp(self):
        super().setUp()
        with (self.max / "instanz.toml").open("a", encoding="utf-8") as datei:
            datei.write('[briefkasten]\nhost = "100.64.0.7"\noeffentlich = "vserver.example.org"\n')
        for inst, name in ((self.max, "max"), (self.eva, "eva")):
            bk = inst / "briefkasten"
            bk.mkdir()
            (bk / "pc").write_bytes(schluessel(f"{name}PC"))
            (bk / "abholen").write_bytes(schluessel(f"{name}ABHOLEN"))
            (bk / "known_hosts_pc").write_text(f"[vserver.example.org]:2222 {HOSTKEY}\n", encoding="ascii")
            (bk / "known_hosts").write_text(f"[100.64.0.7]:2222 {HOSTKEY}\n", encoding="ascii")
        self.k = self.lade(self.max)

    def entpacke(self, inhalt: bytes) -> dict[str, bytes]:
        with zipfile.ZipFile(io.BytesIO(inhalt)) as z:
            return {n: z.read(n) for n in z.namelist()}

    def test_zip_hat_genau_seine_dateien_und_nichts_fremdes(self):
        name, inhalt = lernbot_pc.baue_zip(self.k)
        self.assertEqual(name, "ClipUpload-max.zip")
        dateien = self.entpacke(inhalt)
        self.assertEqual(set(dateien), {*SKRIPTE, "freund.psd1", "pc", "known_hosts"})
        for skript in SKRIPTE:
            repo = (PROJEKT / "windows" / skript).read_bytes()
            if skript.endswith(".cmd"):
                repo = repo.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
                self.assertNotIn(b"\n", dateien[skript].replace(b"\r\n", b""))   # nur CRLF
            self.assertEqual(dateien[skript], repo, skript)
        self.assertEqual(dateien["pc"], (self.max / "briefkasten" / "pc").read_bytes())
        self.assertEqual(dateien["known_hosts"], (self.max / "briefkasten" / "known_hosts_pc").read_bytes())
        psd1 = dateien["freund.psd1"]
        self.assertTrue(psd1.startswith(b"\xef\xbb\xbf"))                      # BOM für Windows PowerShell 5.1
        for zeile in ("Adresse  = 'vserver.example.org'", "Port     = 2222", "Benutzer = 'bk-max'",
                      "DrosselBeimSpielenKbit = 2000"):
            self.assertIn(zeile, psd1.decode("utf-8-sig"))
        alles = b"".join(dateien.values())
        for fremd in ("maxABHOLEN", "evaPC", "evaABHOLEN", "100.64.0.7"):          # kein Abhol-Schlüssel, nichts von eva,
            self.assertNotIn(fremd.encode(), alles)                                # keine Tailnet-Adresse
        self.assertNotEqual(lernbot_pc.baue_zip(self.k)[1], b"")                   # jedes Mal frisch gebaut

        if PWSH:   # die psd1 liest PowerShell wirklich so, wie das PC-Programm sie braucht
            with tempfile.TemporaryDirectory() as tmp:
                datei = Path(tmp) / "freund.psd1"
                datei.write_bytes(psd1)
                r = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command",
                                    f"Import-PowerShellDataFile -LiteralPath '{datei}' | ConvertTo-Json -Compress"],
                                   capture_output=True, text=True, timeout=60)
            self.assertEqual(json.loads(r.stdout), {"Adresse": "vserver.example.org", "Port": 2222,
                                                    "Benutzer": "bk-max", "DrosselBeimSpielenKbit": 2000}, r.stderr)

    def test_ohne_schluessel_adresse_oder_passenden_hostschluessel_kein_paket(self):
        bk = self.max / "briefkasten"
        (bk / "pc").rename(bk / "pc.weg")
        with self.assertRaises(lernbot_pc.PaketFehler) as fehler, self.assertLogs("lern-bot", "WARNING") as log:
            lernbot_pc.baue_zip(self.k)
        self.assertEqual(str(fehler.exception), lernbot_pc.NICHT_FERTIG)
        self.assertIn("pc fehlt", log.output[0])
        (bk / "pc.weg").rename(bk / "pc")
        (bk / "known_hosts_pc").write_text(f"[anderer.example.org]:2222 {HOSTKEY}\n")   # anderer vServer
        with self.assertRaises(lernbot_pc.PaketFehler), self.assertLogs("lern-bot", "WARNING"):
            lernbot_pc.baue_zip(self.k)
        (bk / "known_hosts_pc").write_text(f"[vserver.example.org]:2222 {HOSTKEY}\n")
        toml = self.max / "instanz.toml"
        toml.write_text(toml.read_text().replace('oeffentlich = "vserver.example.org"\n', ""))
        with self.assertRaises(lernbot_pc.PaketFehler), self.assertLogs("lern-bot", "WARNING"):
            lernbot_pc.baue_zip(self.lade(self.max))
        self.assertIn("sag Florian Bescheid", lernbot_pc.NICHT_FERTIG)

    def lauf(self, szenario) -> str:
        """szenario() in einem Event-Loop – Rückgabe: jede Log-Zeile dabei (auch DEBUG)."""
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

    def test_cmd_pc_schickt_das_zip_und_nie_den_schluessel_ins_log(self):
        bot = FakeBot()
        app = SimpleNamespace(bot=bot, bot_data={"konfig": self.k, "erlaubt": MAX_ZAHL})
        context = SimpleNamespace(application=app, bot_data=app.bot_data, args=[])
        log = self.lauf(lambda: lernbot_pc.cmd_pc(SimpleNamespace(), context))
        ((chat, inhalt, kw),) = bot.dokumente
        self.assertEqual((chat, kw["filename"], kw["caption"]), (MAX_ZAHL, "ClipUpload-max.zip", lernbot_pc.ANLEITUNG))
        self.assertEqual(self.entpacke(inhalt)["pc"], (self.max / "briefkasten" / "pc").read_bytes())
        self.assertLessEqual(len(lernbot_pc.ANLEITUNG), 1024)                    # Telegram: Bildunterschrift ≤ 1024
        self.assertNotIn("maxPC", log)
        self.assertEqual(bot.texte, [])

        bot = FakeBot(document_geht=False)                                         # Telegram nimmt es nicht: ein Satz
        app.bot = bot
        self.lauf(lambda: lernbot_pc.cmd_pc(SimpleNamespace(), context))
        self.assertEqual(bot.texte, [lernbot_pc.SCHIEF])

    def test_pc_nur_im_bot_eines_freundes_mit_briefkasten(self):
        for inst, mit_pc in ((self.max, True), (self.eva, False)):
            with self.subTest(inst.name):
                k = self.lade(inst)
                app = lernbot.baue_app(k, "123456:TEST", MAX_ZAHL)
                self.addCleanup(app.bot_data["con"].close)
                befehle = {c for h in app.handlers[0] for c in getattr(h, "commands", ())}
                self.assertEqual("pc" in befehle, mit_pc)
                self.assertIn("claude", befehle)
                hilfe = lernbot.hilfe_text(False, freund=True, pc=lernbot_pc.verfuegbar(k))
                self.assertEqual(lernbot.PC_ZEILE in hilfe, mit_pc)
                self.assertEqual(lernbot.PC_ZEILE in lernbot.hilfe_text(True, freund=True, pc=mit_pc), mit_pc)

    def test_stand_zeile(self):
        con = db.verbinde(self.k.datenbank)
        self.addCleanup(con.close)
        self.assertEqual(lernbot_pc.pc_zeile(con, self.k), lernbot_pc.NICHT_VERBUNDEN)
        (self.max / "db" / "pc-status.json").write_text(json.dumps(
            {"zeit_utc": iso(jetzt() - timedelta(minutes=5, seconds=20)), "offen": [{"quelle": "videos", "anzahl": 2}]}))
        self.assertEqual(lernbot_pc.pc_zeile(con, self.k), "💻 PC: zuletzt vor 5 min · 2 unterwegs")
        con.execute("CREATE TABLE abholung (status TEXT)")                         # dazu noch eine im Briefkasten
        con.execute("INSERT INTO abholung VALUES ('offen'), ('abgeholt')")
        self.assertEqual(lernbot_pc.pc_zeile(con, self.k), "💻 PC: zuletzt vor 5 min · 3 unterwegs")
        eva = self.lade(self.eva)
        self.assertIsNone(lernbot_pc.pc_zeile(con, eva))                          # ohne Briefkasten keine Zeile

        antworten: list[str] = []

        async def antwort(text, **_):
            antworten.append(text)

        update = SimpleNamespace(effective_message=SimpleNamespace(reply_text=antwort))
        asyncio.run(lernbot.cmd_stand(update, SimpleNamespace(bot_data={"con": con, "konfig": self.k})))
        self.assertTrue(antworten[0].endswith("\n💻 PC: zuletzt vor 5 min · 3 unterwegs"), antworten[0])


@unittest.skipIf(lernbot is None, "python-telegram-bot fehlt")
class FlorianOhnePc(MitSpeicher):
    def test_kein_pc_und_hilfe_wie_bisher(self):
        from clip_pipeline import lernbot_publikum

        app = lernbot.baue_app(self.konfig, "123456:TEST", 42)
        self.addCleanup(app.bot_data["con"].close)
        befehle = {c for h in app.handlers[0] for c in getattr(h, "commands", ())}
        self.assertNotIn("pc", befehle)
        self.assertFalse(lernbot_pc.verfuegbar(self.konfig))
        self.assertIsNone(lernbot_pc.pc_zeile(self.con, self.konfig))
        self.assertEqual(lernbot.hilfe_text(False), lernbot.HILFE)
        self.assertEqual(lernbot.hilfe_text(True), lernbot.HILFE_EXPERTE + lernbot_publikum.HILFE_ZUSATZ)
        with self.assertRaises(lernbot_pc.PaketFehler):
            lernbot_pc.baue_zip(self.konfig)


if __name__ == "__main__":
    unittest.main()
