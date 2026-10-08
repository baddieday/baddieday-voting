"""/claude im Lern-Bot eines Freundes (Mehrbenutzer M1, Stufe 1, Schritt 9): eigenen Claude-Zugang verbinden.

Florian 08.10.: „Eigener Claude-Zugang“ – jeder Freund nutzt sein eigenes Claude-Abo. Nur in der Instanz eines
Freundes: lernbot.baue_app hängt dieses Modul nur dort ein (statt /tiktok). Bei Florian gibt es /claude nicht, sein
Bot bleibt, wie er ist.

/claude → `claude setup-token` (das Claude-Code-Kommando für ein Langzeit-Token aus dem eigenen Abo) läuft in einem
Pseudo-Terminal, mit derselben kleinen Umgebung wie claude_aufruf in der Instanz (HOME und CLAUDE_CONFIG_DIR unter
I/cache, DISABLE_AUTOUPDATER=1 – nie Florians Umgebung) und so breit, dass der Link nicht umbricht. Der Anmelde-Link aus
der Ausgabe geht an den Freund. Seine nächste Textnachricht ist der Code: sofort gelöscht, ins Terminal geschrieben,
das Token aus der Ausgabe atomar nach I/db/claude-token (0600, claude_aufruf.token_speichern) – claude_aufruf liest es
beim nächsten Aufruf, ohne Neustart. Schickt er gleich ein Token (sk-ant-oat01-…, z. B. aus `claude setup-token` auf
seinem PC), wird es genauso gespeichert und die Nachricht gelöscht.

Fristen: Link 60 s, Token nach dem Code 60 s, alles zusammen 10 min. Danach, bei jedem Fehler, bei einem neuen /claude
und beim Beenden des Bots endet claude (eigene Prozessgruppe: TERM, nach 2 s KILL); gespeichert wird nur ein Token. Die
Anmeldung ist eine eigene asyncio-Aufgabe und liest das Terminal im Event-Loop (add_reader) – der Bot blockiert nie, und
ein Neustart wartet nicht 10 min auf sie (Application.stop wartet auf jede Aufgabe aus app.create_task).

Code, Token und Link stehen nie im Log (nur was passiert und Fehlerarten), nie in der Datenbank und nie wieder im
Chat; die Ausgabe von claude bleibt nur im Speicher dieser Anmeldung und wird mit ihrem Ende verworfen.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import signal
import struct
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import claude_aufruf
from .konfig import KonfigFehler

log = logging.getLogger("lern-bot")

ABLAUF = "claude_ablauf"   # bot_data: die laufende Anmeldung (höchstens eine)
LINK_FRIST_S = 60.0        # so lange darf claude für den Anmelde-Link brauchen …
TOKEN_FRIST_S = 60.0       # … und nach dem Code für das Token
FRIST_S = 600.0            # die ganze Anmeldung ab /claude (10 min)
BREITE = 1000              # Spalten des Terminals: der Link (rund 400 Zeichen) bricht so nicht um
MAX_AUSGABE = 256 * 1024   # mehr behält eine Anmeldung von der Ausgabe nicht (die Oberfläche zeichnet sich neu)

# Ein Langzeit-Token aus `claude setup-token`. In einer Nachricht darf es am Ende stehen, in der Ausgabe nur mit einem
# Zeichen dahinter – sonst ist es vielleicht erst halb gelesen
TOKEN = r"sk-ant-oat01-[A-Za-z0-9_-]{20,4000}"
TOKEN_IN_NACHRICHT = re.compile(TOKEN)
TOKEN_IN_AUSGABE = re.compile(TOKEN + r"(?=[^A-Za-z0-9_-])")
FEHLER_IN_AUSGABE = re.compile(r"(?i)\berror\b|press enter to retry")   # z. B. „OAuth error: … status code 400“
CODE_FORM = re.compile(r"[!-~]{8,2048}")                               # eine Zeile sichtbarer Zeichen ohne Leerzeichen

# Steuerzeichen der Terminal-Oberfläche: Wörter stehen per Cursor-Sprung (ESC[nG) nebeneinander, der Link kommt
# zusätzlich als Hyperlink-Folge (ESC]8;…) vor dem sichtbaren Text, dazu Farben und Abfragen ans Terminal
_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_SPRUNG = re.compile(r"\x1b\[[0-9;]*[CG]")
_CSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_ESC = re.compile(r"\x1b[()][0-9A-Za-z]|\x1b[@-_78=>]")
_STEUER = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_ADRESSE = re.compile(r"https://\S+(?=\s)")

LINK_TEXT = ("🤖 Claude verbinden: Öffne den Link, melde dich mit deinem Claude-Konto an und schick mir den Code, den "
             "du danach siehst.\n\n{link}\n\nDer Link gilt 10 Minuten.")
VERBUNDEN = "✅ Claude verbunden – ab jetzt schaut die KI bei dir mit."
AUSWEG = ("Oder: Führ auf deinem PC „claude setup-token“ aus und schick mir das Token, das mit sk-ant-oat01- "
          "anfängt.")
KEIN_LINK = ("⚠️ Claude hat mir gerade keinen Anmelde-Link gegeben – nichts gespeichert. Versuch es später nochmal mit "
             "/claude.\n" + AUSWEG)
KEIN_TOKEN = ("⚠️ Mit diesem Code hat es nicht geklappt – nichts gespeichert. Tipp /claude für einen neuen Link.\n"
              + AUSWEG)
ABGELAUFEN = "⏱️ Die Anmeldung ist abgelaufen – nichts gespeichert. Tipp /claude für einen neuen Link.\n" + AUSWEG
NICHT_GEKLAPPT = ("⚠️ Das hat gerade nicht geklappt – nichts gespeichert. Versuch es später nochmal mit /claude.\n"
                  + AUSWEG)
CLAUDE_FEHLT = ("⚠️ Claude fehlt noch auf dem Mini – Florian muss Claude einmal auf dem Mini installieren. Danach klappt "
                "/claude.")
KEIN_CODE = ("🤔 Das sieht nicht nach dem Code aus. Schick mir nur den Code von der Anmeldeseite – eine Zeile ohne "
             "Leerzeichen.")
PRUEFT = "⏳ Einen Moment – ich prüfe den Code gerade."
NICHT_GESPEICHERT = "⚠️ Das Token konnte ich nicht speichern – sag Florian Bescheid."


# --- Ausgabe lesen (ohne Telegram testbar) --------------------------------------------------------------------------

def lesbar(roh: str) -> str:
    """Ausgabe ohne Steuerzeichen; ein Cursor-Sprung wird ein Leerzeichen (so stehen Wörter getrennt da)."""
    text = _OSC.sub("", roh)
    text = _SPRUNG.sub(" ", text)
    text = _CSI.sub("", text)
    text = _ESC.sub("", text)
    return _STEUER.sub("", text)


def finde_link(text: str) -> str | None:
    """Der Anmelde-Link: die erste vollständige https-Adresse mit „oauth“ und „authorize“ (sonst None)."""
    return next((a for a in _ADRESSE.findall(text) if "oauth" in a.lower() and "authorize" in a.lower()), None)


def finde_token(text: str) -> str | None:
    """Nach dem Code: das Token (vollständig gelesen); '' bei einer Fehlermeldung von claude (nicht weiter warten);
    None, solange nichts davon da ist."""
    if treffer := TOKEN_IN_AUSGABE.search(text):
        return treffer.group(0)
    return "" if FEHLER_IN_AUSGABE.search(text) else None


class Anmeldung:
    """`claude setup-token` in einem Pseudo-Terminal. Gelesen wird im Event-Loop (add_reader), nie blockierend."""

    def __init__(self, programm: str, umgebung: dict[str, str], ordner: Path) -> None:
        import fcntl
        import termios

        self._loop = asyncio.get_running_loop()
        haupt, neben = os.openpty()
        try:
            # Größe vor dem Start: die Oberfläche bricht Zeilen an der Breite um – auch den Link
            fcntl.ioctl(neben, termios.TIOCSWINSZ, struct.pack("HHHH", 50, BREITE, 0, 0))
            # Eigene Sitzung = eigene Prozessgruppe: beenden trifft claude und alles, was es startet (Browser-Versuch)
            self.prozess = subprocess.Popen([programm, "setup-token"], stdin=neben, stdout=neben, stderr=neben,
                                            cwd=ordner, env=umgebung, start_new_session=True)
        except BaseException:
            os.close(haupt)
            raise
        finally:
            os.close(neben)
        os.set_blocking(haupt, False)
        self._fd: int | None = haupt
        self._roh = bytearray()
        self._neu = asyncio.Event()
        self._zu = False
        self._loop.add_reader(haupt, self._lies)

    def _lies(self) -> None:
        try:
            daten = os.read(self._fd, 65536)
        except BlockingIOError:
            return
        except OSError:   # EIO: claude ist beendet, das Terminal hat keine Gegenseite mehr
            daten = b""
        if daten:
            self._roh += daten
            del self._roh[:-MAX_AUSGABE]
        else:
            self._zu = True
            self._loop.remove_reader(self._fd)
        self._neu.set()

    def text(self) -> str:
        return lesbar(self._roh.decode("utf-8", "replace"))

    async def warte(self, finde, frist_s: float):
        """finde(text) so lange, bis es nicht None liefert – oder claude endet bzw. frist_s um ist (dann None)."""
        ende = self._loop.time() + frist_s
        while True:
            self._neu.clear()
            if (treffer := finde(self.text())) is not None:
                return treffer
            rest = ende - self._loop.time()
            if rest <= 0 or self._zu:
                return None
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._neu.wait(), min(rest, 1.0))

    async def schreibe(self, zeile: str) -> None:
        """Eine Zeile ins Terminal, abgeschlossen mit Enter (\\r) – wie getippt. Die Ausgabe davor wird verworfen
        (das Token kommt erst danach)."""
        self._roh.clear()
        rest = zeile.encode("utf-8") + b"\r"
        ende = self._loop.time() + 5
        while rest:
            try:
                rest = rest[os.write(self._fd, rest):]
            except BlockingIOError:
                if self._loop.time() > ende:
                    raise TimeoutError("Terminal nimmt nichts an") from None
                await asyncio.sleep(0.05)

    def _signal(self, sig: int) -> bool:
        """Signal an claudes Prozessgruppe, solange claude läuft – danach nie (die Nummer könnte neu vergeben sein)."""
        if self.prozess.poll() is not None:
            return False
        with contextlib.suppress(ProcessLookupError):
            os.killpg(self.prozess.pid, sig)
        return True

    async def beenden(self) -> None:
        """claude beenden (TERM, nach 2 s KILL) und das Terminal schließen; die Ausgabe wird verworfen. Mehrfach
        aufrufbar – auch, wenn die Aufgabe dabei abgebrochen wird."""
        try:
            if self._signal(signal.SIGTERM):
                for _ in range(40):
                    if self.prozess.poll() is not None:
                        break
                    await asyncio.sleep(0.05)
        finally:
            if self._signal(signal.SIGKILL):
                with contextlib.suppress(subprocess.TimeoutExpired):
                    self.prozess.wait(timeout=5)   # nach KILL sofort vorbei
            if self._fd is not None:
                self._loop.remove_reader(self._fd)
                os.close(self._fd)
                self._fd = None
            self._roh.clear()


# --- Ablauf im Bot -------------------------------------------------------------------------------------------------

@dataclass
class Ablauf:
    """Eine laufende Anmeldung: phase „start“ (bis der Link raus ist) → „code“ (wartet auf den Code) → „prueft“."""
    phase: str = "start"
    codes: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=1))
    aufgabe: asyncio.Task | None = None


async def cmd_claude(update, context) -> None:
    """/claude: neue Anmeldung im Hintergrund – eine laufende endet still (ihr Link gilt dann nicht mehr).
    „/claude sk-ant-oat01-…“ ist ein direkt geschicktes Token (Befehle sieht bei_text nicht)."""
    app = context.application
    nachricht = update.effective_message
    if treffer := TOKEN_IN_NACHRICHT.search(nachricht.text or ""):
        await _token_direkt(app, nachricht, treffer.group(0))
        return
    await abbrechen(app)
    ablauf = Ablauf()
    app.bot_data[ABLAUF] = ablauf
    # Eigene Aufgabe statt app.create_task: Application.stop wartet auf jede create_task-Aufgabe – ein Neustart des Bots
    # hinge sonst bis zu 10 min an einer offenen Anmeldung. lernbot.baue_app bricht sie beim Beenden ab.
    ablauf.aufgabe = asyncio.get_running_loop().create_task(_anmelden(app, ablauf))


async def abbrechen(app) -> None:
    """Eine laufende Anmeldung still beenden (neues /claude, Token direkt geschickt, Ende des Bots) – claude endet."""
    ablauf = app.bot_data.pop(ABLAUF, None)
    if ablauf is None or ablauf.aufgabe is None or ablauf.aufgabe.done():
        return
    ablauf.aufgabe.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await ablauf.aufgabe


async def _anmelden(app, ablauf: Ablauf) -> None:
    """Der ganze Ablauf: claude starten, Link schicken, auf den Code warten, Token speichern. Nichts davon ins Log."""
    konfig = app.bot_data["konfig"]
    loop = asyncio.get_running_loop()
    ende = loop.time() + FRIST_S
    anmeldung: Anmeldung | None = None
    try:
        start = claude_aufruf.instanz_umgebung(konfig)
        if isinstance(start, str):
            log.warning("/claude: %s", start)
            fehlt = start == claude_aufruf.NICHT_GEFUNDEN or start.startswith("claude liegt unter")
            await _sag(app, CLAUDE_FEHLT if fehlt else NICHT_GEKLAPPT)
            return
        programm, umgebung = start
        try:
            anmeldung = Anmeldung(programm, {**umgebung, "TERM": "xterm-256color"}, konfig.instanz / "cache")
        except OSError as e:   # nicht ausführbar o. Ä.: Sache von Florian
            log.warning("/claude: claude startet nicht (%s)", type(e).__name__)
            await _sag(app, CLAUDE_FEHLT)
            return
        log.info("/claude: Anmeldung gestartet")
        link = await anmeldung.warte(finde_link, min(LINK_FRIST_S, ende - loop.time()))
        if link is None:
            log.warning("/claude: kein Anmelde-Link von claude")
            await _sag(app, KEIN_LINK)
            return
        ablauf.phase = "code"   # vor dem Senden: ab jetzt ist die nächste Textnachricht der Code
        await _sag(app, LINK_TEXT.format(link=link), link_preview_options=_ohne_vorschau())
        log.info("/claude: Anmelde-Link geschickt, warte auf den Code")
        try:
            code = await asyncio.wait_for(ablauf.codes.get(), max(0.0, ende - loop.time()))
        except TimeoutError:
            log.info("/claude: Frist abgelaufen – nichts gespeichert")
            await _sag(app, ABGELAUFEN)
            return
        ablauf.phase = "prueft"
        await anmeldung.schreibe(code)
        del code
        token = await anmeldung.warte(finde_token, TOKEN_FRIST_S)
        if not token:
            log.warning("/claude: kein Token nach dem Code – nichts gespeichert")
            await _sag(app, KEIN_TOKEN)
            return
        try:
            claude_aufruf.token_speichern(konfig, token)
        except (OSError, ValueError) as e:   # Platte voll o. Ä.: Sache von Florian
            log.error("/claude: Token nicht gespeichert (%s)", type(e).__name__)
            await _sag(app, NICHT_GESPEICHERT)
            return
        log.info("/claude: Claude verbunden – Token in db/%s", claude_aufruf.TOKEN_DATEI)
        await _sag_still(app, VERBUNDEN)   # gespeichert ist es – ein Netzfehler hier ist kein „nicht geklappt“
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 – nur die Art ins Log: eine Meldung könnte einen Zugang enthalten
        log.error("/claude fehlgeschlagen (%s) – nichts gespeichert", type(e).__name__)
        await _sag_still(app, NICHT_GEKLAPPT)
    finally:
        if anmeldung is not None:
            await anmeldung.beenden()
        if app.bot_data.get(ABLAUF) is ablauf:
            del app.bot_data[ABLAUF]


async def bei_text(update, context) -> None:
    """Gruppe -1, vor allen anderen Text-Handlern: ein Token (jederzeit) oder der Code (nach dem Link). Dann endet die
    Verarbeitung hier (ApplicationHandlerStop) – der Text kommt zu keinem anderen Handler. Alles andere geht weiter."""
    from telegram.ext import ApplicationHandlerStop

    app = context.application
    nachricht = update.effective_message
    text = nachricht.text or ""
    if treffer := TOKEN_IN_NACHRICHT.search(text):
        await _token_direkt(app, nachricht, treffer.group(0))
        raise ApplicationHandlerStop
    ablauf = app.bot_data.get(ABLAUF)
    if ablauf is None or ablauf.phase == "start":
        return
    antwort = PRUEFT
    if ablauf.phase == "code":
        code = text.strip()
        if CODE_FORM.fullmatch(code):   # erst weitergeben, dann löschen: läuft die Frist gerade ab, kein Durcheinander
            ablauf.phase = "prueft"
            ablauf.codes.put_nowait(code)
            antwort = None
        else:
            antwort = KEIN_CODE
    await _loesche(app, nachricht)
    if antwort:
        await _sag_still(app, antwort)
    raise ApplicationHandlerStop


async def _token_direkt(app, nachricht, token: str) -> None:
    """Ein Token in einer Nachricht: speichern, die Nachricht löschen, eine laufende Anmeldung beenden."""
    try:
        claude_aufruf.token_speichern(app.bot_data["konfig"], token)
    except (OSError, ValueError, KonfigFehler) as e:
        log.error("/claude: Token nicht gespeichert (%s)", type(e).__name__)
        antwort = NICHT_GESPEICHERT
    else:
        log.info("/claude: Claude verbunden (Token geschickt) – in db/%s", claude_aufruf.TOKEN_DATEI)
        antwort = VERBUNDEN
    await _loesche(app, nachricht)
    await abbrechen(app)
    await _sag_still(app, antwort)


def _ohne_vorschau():
    from telegram import LinkPreviewOptions

    return LinkPreviewOptions(is_disabled=True)


async def _sag(app, text: str, **kw):
    return await app.bot.send_message(app.bot_data["erlaubt"], text, **kw)


async def _sag_still(app, text: str) -> None:
    """Wie _sag, aber ein Fehler (Netz) geht nur mit seiner Art ins Log."""
    try:
        await _sag(app, text)
    except Exception as e:  # noqa: BLE001
        log.warning("/claude: Antwort ging nicht raus (%s)", type(e).__name__)


async def _loesche(app, nachricht) -> None:
    """Die Nachricht mit Code oder Token aus dem Chat löschen – scheitert das (zu alt, Netz), bleibt es dabei."""
    try:
        await app.bot.delete_message(chat_id=nachricht.chat_id, message_id=nachricht.message_id)
    except Exception as e:  # noqa: BLE001
        log.info("/claude: Nachricht nicht gelöscht (%s)", type(e).__name__)


def registriere(app, nur_ich) -> None:
    """/claude und der Text-Handler in Gruppe -1 (vor lernbot_zahlen.bei_text, der sonst jeden Text nimmt) – nur für
    Freunde, aufgerufen von lernbot.baue_app."""
    from telegram.ext import CommandHandler, MessageHandler, filters

    app.add_handler(CommandHandler("claude", cmd_claude, filters=nur_ich))
    app.add_handler(MessageHandler(nur_ich & filters.TEXT & ~filters.COMMAND, bei_text), group=-1)
