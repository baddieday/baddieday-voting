"""/pc im Lern-Bot eines Freundes (Mehrbenutzer, Stufe 2, Schritt 4): sein PC-Programm als ZIP.

Florian 08.10.: Freunde brauchen keinen eigenen Server – „Telegram wie bei dir“. Nur in der Instanz eines Freundes mit
Briefkasten ([briefkasten].host): lernbot.baue_app hängt dieses Modul nur dort ein. Bei Florian gibt es /pc nicht.

/pc baut das ZIP jedes Mal frisch aus dem laufenden Stand – kein Auto-Update; eine neue Version heißt: /pc erneut und
Freund-Einrichten.cmd erneut (M97). Darin, flach (Windows „Alle extrahieren“ legt den Ordner selbst an):
  Freund-Hochladen.ps1, Freund-Einrichten.ps1, Freund-Einrichten.cmd   aus <Projekt>/windows (die .cmd mit CRLF)
  freund.psd1    öffentliche Adresse des vServers, Port, Benutzer bk-<name>, Drossel beim Spielen – kein Geheimnis
  pc             sein Upload-Schlüssel (I/briefkasten/pc) – darf nur hochladen, nur in sein eigenes Fach
  known_hosts    der Hostschlüssel des Briefkastens unter der öffentlichen Adresse (I/briefkasten/known_hosts_pc)
Nie der Abhol-Schlüssel, nie etwas von Florian oder anderen Freunden. Der Schlüssel steht nie im Log. Er reist über den
eigenen Bot (Telegram-Cloud-Chat) – sein Wert ist gering: nur hochladen, nur ins eigene Fach, austauschbar (M88).

Dazu die Zeile für 📋 Stand: „💻 PC: zuletzt vor 5 min · 2 unterwegs“ bzw. „noch nicht verbunden – tipp /pc“.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import re
import sqlite3
import zipfile
from pathlib import Path

from . import briefkasten
from .konfig import PROJEKT, Konfig
from .zeit import aus_iso, jetzt, utc_zu_lokal

log = logging.getLogger("lern-bot")

SKRIPTE = ("Freund-Hochladen.ps1", "Freund-Einrichten.ps1", "Freund-Einrichten.cmd")
DROSSEL_KBIT = 2000        # M95/M117: solange Fortnite läuft, höchstens 2 Mbit/s (0 hieße Pause)
SCHLUESSEL_MAX = 16 * 1024
# Kopf und Fuß eines privaten OpenSSH-Schlüssels – zusammengesetzt, damit diese Datei selbst kein Treffer für
# tests/test_keine_secrets.py ist
KOPF = b"-----BEGIN OPENSSH " + b"PRIVATE KEY-----"
FUSS = b"-----END OPENSSH " + b"PRIVATE KEY-----"
HOSTKEY = re.compile(r"ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI[A-Za-z0-9+/]{43}")

ANLEITUNG = ("💻 Dein PC-Programm – so geht's:\n"
             "1. Die Datei auf deinem Windows-PC speichern, Rechtsklick → „Alle extrahieren“.\n"
             "2. Im neuen Ordner Freund-Einrichten.cmd doppelklicken. Kommt „Der Computer wurde durch Windows "
             "geschützt“: „Weitere Informationen“ → „Trotzdem ausführen“.\n"
             "3. In Fortnite: Einstellungen → Replays aufzeichnen: an.\n"
             "Danach lädt dein PC deine Aufnahmen von selbst hoch – beim Spielen langsam, damit nichts ruckelt. Darin "
             "steckt dein Schlüssel: nicht weitergeben. Neue Version: einfach wieder /pc.")
NICHT_FERTIG = "⚠️ Dein Briefkasten bei Florian ist noch nicht fertig eingerichtet – sag Florian Bescheid."
SCHIEF = "⚠️ Das PC-Programm ging gerade nicht raus – versuch es gleich nochmal mit /pc."
NICHT_VERBUNDEN = "💻 PC: noch nicht verbunden – tipp /pc"


class PaketFehler(RuntimeError):
    """Etwas fehlt, das nur Florian einrichten kann – der Text geht so an den Freund (ohne Pfade)."""


def verfuegbar(konfig: Konfig) -> bool:
    """/pc gibt es nur in der Instanz eines Freundes mit Briefkasten."""
    return konfig.instanz is not None and briefkasten.aktiv(konfig)


def psd1(name: str, adresse: str, port: int, benutzer: str, datum: str) -> bytes:
    """freund.psd1 – die Werte sind geprüft (konfig._briefkasten: Adresse nur A-Z, 0-9, Punkt, Strich, Doppelpunkt;
    Name nach INSTANZ_NAME), ein Anführungszeichen kann also nicht hineingeraten. Mit BOM: Windows PowerShell 5.1 läse
    die Umlaute in den Kommentaren sonst falsch (wie Stolperdraht51 für windows/*.psd1)."""
    text = (f"# PC-Programm von {name} – gebaut von deinem Bot am {datum} (Clip-Pipeline, docs/FREUNDE.md).\n"
            "# Nicht von Hand ändern. Neue Version: im Bot /pc tippen und Freund-Einrichten.cmd noch einmal starten.\n"
            "@{\n"
            f"    Adresse  = '{adresse}'\n"
            f"    Port     = {int(port)}\n"
            f"    Benutzer = '{benutzer}'\n"
            "    # Solange Fortnite läuft, höchstens so schnell hochladen (kbit/s; 2000 = 2 Mbit/s). 0 = beim Spielen "
            "Pause.\n"
            f"    DrosselBeimSpielenKbit = {DROSSEL_KBIT}\n"
            "}\n")
    return b"\xef\xbb\xbf" + text.encode("utf-8")


def _lies(pfad: Path, hoechstens: int) -> bytes:
    """Eine Datei aus I/briefkasten, ohne einem Link zu folgen, höchstens so groß. Fehlt sie: PaketFehler."""
    try:
        fd = os.open(pfad, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as e:
        log.warning("/pc: %s fehlt oder ist nicht lesbar (%s)", pfad.name, type(e).__name__)
        raise PaketFehler(NICHT_FERTIG) from None
    with os.fdopen(fd, "rb") as datei:
        inhalt = datei.read(hoechstens + 1)
    if len(inhalt) > hoechstens:
        log.warning("/pc: %s ist größer als %d Byte", pfad.name, hoechstens)
        raise PaketFehler(NICHT_FERTIG)
    return inhalt


def _known_hosts(ordner: Path, adresse: str, port: int) -> bytes:
    """known_hosts_pc: genau eine Zeile „[<öffentlich>]:<port> ssh-ed25519 …“ (bei Port 22 ohne Klammern) – so, wie
    benutzer-anlegen.sh sie schreibt (M112). Passt sie nicht zur Adresse in [briefkasten], kommt kein Paket."""
    text = _lies(ordner / "known_hosts_pc", 4096).decode("ascii", "replace").strip()
    host = adresse if port == 22 else f"[{adresse}]:{port}"
    teile = text.split(" ", 1)
    if "\n" in text or len(teile) != 2 or teile[0] != host or not HOSTKEY.fullmatch(teile[1]):
        log.warning("/pc: known_hosts_pc passt nicht zu %s", host)
        raise PaketFehler(NICHT_FERTIG)
    return (text + "\n").encode("ascii")


def baue_zip(konfig: Konfig) -> tuple[str, bytes]:
    """(Dateiname, ZIP) – jedes Mal frisch. PaketFehler, wenn etwas fehlt, das nur Florian einrichten kann."""
    if not verfuegbar(konfig):
        raise PaketFehler(NICHT_FERTIG)
    name = str(konfig.wert("instanz.name"))
    adresse = str(konfig.wert("briefkasten.oeffentlich", "") or "").strip()
    port = int(konfig.wert("briefkasten.port", 2222))
    if not adresse:
        log.warning("/pc: [briefkasten].oeffentlich fehlt in der instanz.toml")
        raise PaketFehler(NICHT_FERTIG)
    ordner = konfig.instanz / "briefkasten"
    schluessel = _lies(ordner / "pc", SCHLUESSEL_MAX)
    if not schluessel.lstrip().startswith(KOPF) or FUSS not in schluessel:
        log.warning("/pc: briefkasten/pc ist kein privater OpenSSH-Schlüssel")
        raise PaketFehler(NICHT_FERTIG)
    known = _known_hosts(ordner, adresse, port)
    datum = f"{utc_zu_lokal(jetzt(), konfig.wert('zeit.zeitzone', 'Europe/Berlin')):%d.%m.%Y}"
    puffer = io.BytesIO()
    with zipfile.ZipFile(puffer, "w", zipfile.ZIP_DEFLATED) as z:
        for skript in SKRIPTE:
            inhalt = (PROJEKT / "windows" / skript).read_bytes()
            if skript.endswith(".cmd"):   # cmd.exe will CRLF – egal, wie git die Datei auf dem Mini ausgecheckt hat
                inhalt = inhalt.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
            z.writestr(skript, inhalt)
        z.writestr("freund.psd1", psd1(name, adresse, port, str(konfig.wert("briefkasten.benutzer")), datum))
        z.writestr("pc", schluessel)
        z.writestr("known_hosts", known)
    return f"ClipUpload-{name}.zip", puffer.getvalue()


# --- 📋 Stand ------------------------------------------------------------------------------------------------------

def _vor(sekunden: float) -> str:
    if sekunden < 120:
        return "gerade eben"
    if sekunden < 3600:
        return f"vor {int(sekunden // 60)} min"
    if sekunden < 48 * 3600:
        return f"vor {int(sekunden // 3600)} h"
    return f"vor {int(sekunden // 86400)} Tagen"


def pc_zeile(con: sqlite3.Connection, konfig: Konfig) -> str | None:
    """„💻 PC: zuletzt vor 5 min · 2 unterwegs“ – unterwegs = noch auf dem PC (laut seinem Status) plus noch im
    Briefkasten (abholung offen). Ohne Status vom PC: „noch nicht verbunden – tipp /pc“. Bei Florian None."""
    if not verfuegbar(konfig):
        return None
    status = briefkasten.pc_status(konfig)
    if status is None:
        return NICHT_VERBUNDEN
    try:
        zuletzt = _vor(max(0.0, (jetzt() - aus_iso(str(status.get("zeit_utc")))).total_seconds()))
    except (TypeError, ValueError):
        zuletzt = "mit unbekannter Uhrzeit"
    n = briefkasten.unterwegs(con, status)
    return f"💻 PC: zuletzt {zuletzt} · " + (f"{n} unterwegs" if n else "nichts unterwegs")


# --- Telegram ------------------------------------------------------------------------------------------------------

async def cmd_pc(update, context) -> None:
    """/pc: das ZIP bauen (Thread) und als Datei schicken. Fehler: ein kurzer Satz, Einzelheiten nur ins Log."""
    app = context.application
    konfig = app.bot_data["konfig"]
    try:
        dateiname, inhalt = await asyncio.to_thread(baue_zip, konfig)
    except PaketFehler as fehler:
        await _sag_still(app, str(fehler))
        return
    except Exception as fehler:  # noqa: BLE001 – nur die Art ins Log: der Text könnte einen Pfad enthalten
        log.error("/pc: Paket nicht gebaut (%s)", type(fehler).__name__)
        await _sag_still(app, SCHIEF)
        return
    try:
        await app.bot.send_document(app.bot_data["erlaubt"], document=inhalt, filename=dateiname, caption=ANLEITUNG,
                                    read_timeout=120, write_timeout=120, connect_timeout=30)
    except Exception as fehler:  # noqa: BLE001
        log.warning("/pc: ZIP ging nicht raus (%s)", type(fehler).__name__)
        await _sag_still(app, SCHIEF)
        return
    log.info("/pc: PC-Programm geschickt (%d Byte)", len(inhalt))


async def _sag_still(app, text: str) -> None:
    try:
        await app.bot.send_message(app.bot_data["erlaubt"], text)
    except Exception as fehler:  # noqa: BLE001
        log.warning("/pc: Antwort ging nicht raus (%s)", type(fehler).__name__)


def registriere(app, nur_ich) -> None:
    """/pc – nur für Freunde mit Briefkasten, aufgerufen von lernbot.baue_app."""
    from telegram.ext import CommandHandler

    app.add_handler(CommandHandler("pc", cmd_pc, filters=nur_ich))
