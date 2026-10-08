"""Freund anlegen und prüfen (Mehrbenutzer M1, Stufe 1, Schritt 7/8): `pipeline benutzer pruefen|einrichten|koppeln`.

Läuft nur in der Instanz eines Freundes (CLIP_INSTANZ) – gestartet von den Vorlagen clip-freund-pruefen@,
clip-freund-einrichten@ und clip-freund-koppeln@ (deploy/benutzer) mit DERSELBEN Sandbox wie seine übrigen Dienste.
Florian startet sie über deploy/benutzer/benutzer-anlegen.sh bzw. benutzer-pruefen.sh (als root im CT).

pruefen – sieht nur nach: lexists, stat und os.access, dazu die NAMEN in vier lokalen Ordnern (nie Inhalte, nie das
Lager, kein Netz). Im Namensraum des Freundes dürfen Florians Bereiche (Datenbank, Claude-Anmeldung, Schlüssel für
pve-big, Puffer, Clips, Lager, .env, lokal.toml, Lern-Bot-Stand, Home) und die Ordner anderer Freunde nicht da oder
wenigstens nicht erreichbar sein. Die eigenen Ordner müssen beschreibbar sein – der Instanz-Ordner selbst, instanz.toml,
.env und die Marke nicht (sie gehören root). Die Sperrdatei muss da und nur lesbar sein; (st_dev, st_ino) gehen ins
Ergebnis, damit benutzer-pruefen.sh draußen vergleichen kann, ob es wirklich Florians Datei ist. Ob claude da ist und
ob es einen eigenen Claude-Zugang gibt, ist nur ein Hinweis (claude installiert nur Florian selbst, nie automatisch).

einrichten – wiederholbar, Fertiges wird übersprungen: Datenbank anlegen; Whisper-Modell einmal in den eigenen Cache
(HF_HOME = I/cache/huggingface, so wie die Pipeline es sonst beim ersten Gebrauch lädt); Musik seiner Genres wie im
einfachen Modus (bis 16 freie Titel, unter der gemeinsamen Sperre); danach pruefen. Whisper und Musik sind Zugabe:
scheitern sie, steht es im Ergebnis, eingerichtet ist die Instanz trotzdem (Szenen ohne Sprache, Musik kommt tagsüber
von selbst über musik.nachschub).

koppeln – Einladungslink statt Telegram-Zahl (Schritt 8): fragt Telegram nach dem Namen seines Bots, macht einen
Einmal-Code und legt den Link t.me/<bot>?start=<code> in I/db/einladung.json (nur für ihn und root – nie ins Log).
Liest dann bis zu 15 min die Nachrichten seines Bots, bis „/start <code>“ in einem Einzel-Chat ankommt, schreibt die
Zahl des Absenders nach I/db/kopplung.json (0600) und antwortet „Verbunden“. Je Bot gibt es nur einen Empfänger: koppeln
läuft nur, solange keine Telegram-Zahl in seiner .env steht – ohne sie holt sein Bot nie Nachrichten ab.

instanz_toml – läuft bei FLORIAN (ohne Instanz, als pipeline): die instanz.toml eines neuen Freundes aus Florians
wirksamer Konfig – nur die gemeinsame Sperre, die Rechnerwerte und die Waffen-Nummern (M15).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import shutil
import stat
import tempfile
import time
import tomllib
from datetime import date
from pathlib import Path
from typing import Any

from . import claude_aufruf, db, sperre
from .konfig import (INSTANZ_MARKE, INSTANZ_NAME, INSTANZ_WARTEN_S, STANDARD_KONFIG, Konfig, KonfigFehler,
                     claude_verboten, liegt_in)
from .zeit import iso, jetzt

log = logging.getLogger("pipeline")

# Von hier aus wird nachgesehen – Tests setzen eine Scheinwurzel (die Pfade unten gelten darunter)
WURZEL = Path("/")
# Florians Bereiche: im Namensraum eines Freundes nicht da oder nicht erreichbar (nur lstat und access)
FLORIAN_PFADE = (
    ("/var/lib/clip-pipeline/pipeline.db", "Florians Datenbank"),
    ("/var/lib/clip-pipeline/claude", "Florians Claude-Anmeldung"),
    ("/var/lib/clip-pipeline/.ssh/pve-big", "Florians Schlüssel für pve-big"),
    ("/var/lib/clip-pipeline/publikum-oauth.json", "Florians TikTok-Zugang"),
    ("/srv/puffer", "Florians Puffer"),
    ("/srv/clips", "Florians Clips"),
    ("/srv/big", "Florians Lager auf pve-big"),
    ("/opt/clip-pipeline/.env", "Florians Zugänge"),
    ("/opt/clip-pipeline/config/lokal.toml", "Florians Rechner-Konfig"),
    ("/opt/clip-regie", "Florians Lern-Bot-Stand"),
    ("/home/pipeline", "Florians Home (Claude-Anmeldung, SSH-Schlüssel)"),
)
# Lokale Ordner, in denen der Namensraum nur genau diese Namen zeigen darf (gelesen werden nur die Namen)
NUR_DAS = (("/var/lib/clip-pipeline", {"pipeline.lock"}), ("/opt/clip-pipeline/config", {"pipeline.toml"}),
           ("/srv", set()))
EIGENE_ORDNER = ("db", "daten", "regie", "musik", "material", "sfx", "cache")
NUR_LESEN = ("instanz.toml", ".env", INSTANZ_MARKE)   # gehören root – der Freund liest sie nur
MUSIK_ZIEL = 16                                      # = musik.nachschub im Abend-Lauf (2 × regeln.MUSIK_ROTATION)


def _kann(pfad: Path, modus: int) -> bool:
    """os.access – eigene Funktion, damit Tests Rechte wie für einen normalen Benutzer nachstellen können (als root
    wäre sonst alles erlaubt)."""
    return os.access(pfad, modus)


def _erreichbar(pfad: Path) -> bool:
    """Da UND für diesen Prozess erreichbar (lesen, schreiben oder – bei Ordnern – betreten)? Nur lstat und access.
    Was da ist, aber gesperrt (InaccessiblePaths, Rechte 000) oder für diesen Benutzer zu, zählt nicht."""
    if not os.path.lexists(pfad):
        return False
    if _kann(pfad, os.R_OK) or _kann(pfad, os.W_OK):
        return True
    return os.path.isdir(pfad) and _kann(pfad, os.X_OK)


def _namen(ordner: Path) -> list[str]:
    """Die Namen in einem lokalen Ordner – leer, wenn es ihn nicht gibt oder er für diesen Benutzer zu ist."""
    try:
        return sorted(os.listdir(ordner))
    except OSError:
        return []


def _unter(wurzel: Path, pfad: str) -> Path:
    return wurzel / pfad.lstrip("/")


def _instanz(konfig: Konfig) -> Path:
    if konfig.instanz is None:
        raise KonfigFehler("pipeline benutzer läuft nur in der Instanz eines Freundes (CLIP_INSTANZ)")
    return konfig.instanz


def pruefen(konfig: Konfig, wurzel: Path | None = None) -> dict:
    """Trennung im eigenen Namensraum prüfen (nur nachsehen). Ergebnis: {"ok", "name", "befunde": [{"pfad", "grund"}],
    "hinweise": [...], "sperre": {"pfad", "dev", "ino"}} – ok genau dann, wenn es keinen Befund gibt."""
    wurzel = Path(wurzel or WURZEL)
    inst = _instanz(konfig)
    befunde: list[dict[str, str]] = []
    hinweise: list[str] = []

    def befund(pfad: Path, grund: str) -> None:
        befunde.append({"pfad": str(pfad), "grund": grund})

    # 1. Florians Bereiche – im Namensraum nicht da oder nicht erreichbar
    for pfad, was in FLORIAN_PFADE:
        if _erreichbar(p := _unter(wurzel, pfad)):
            befund(p, f"{was} ist sichtbar")
    gemeldet = {b["pfad"] for b in befunde}
    for ordner, erlaubt in NUR_DAS:
        for name in _namen(_unter(wurzel, ordner)):
            p = _unter(wurzel, ordner) / name
            if name not in erlaubt and str(p) not in gemeldet and _erreichbar(p):
                befund(p, "gehört Florian und ist sichtbar")
    # 2. andere Freunde – im Namensraum gibt es nur den eigenen Ordner
    for name in _namen(inst.parent):
        if name != inst.name and INSTANZ_NAME.fullmatch(name):
            befund(inst.parent / name, "Ordner eines anderen Freundes ist sichtbar")
    # 3. eigene Ordner beschreibbar; was root gehört, nicht
    for name in EIGENE_ORDNER:
        p = inst / name
        if p.is_symlink() or not p.is_dir() or not _kann(p, os.W_OK | os.X_OK):
            befund(p, "eigener Ordner fehlt oder ist nicht beschreibbar")
    if _kann(inst, os.W_OK):
        befund(inst, "Instanz-Ordner ist beschreibbar – er gehört root (sonst ließen sich .env und instanz.toml "
                     "austauschen)")
    for name in NUR_LESEN:
        p = inst / name
        if not p.is_file():
            befund(p, "fehlt")
        elif _kann(p, os.W_OK):
            befund(p, "ist beschreibbar – gehört root, der Freund liest sie nur")
    # 4. die eine Rechen-Sperre: da, nur lesbar; dev/ino vergleicht benutzer-pruefen.sh mit Florians Datei
    datei = sperre.pfad(konfig)
    info: dict[str, Any] = {"pfad": str(datei)}
    try:
        st = os.stat(datei)
    except OSError:
        befund(datei, "Sperrdatei fehlt – ohne sie rechnet der Freund nie")
    else:
        info.update(dev=st.st_dev, ino=st.st_ino)
        if not stat.S_ISREG(st.st_mode):
            befund(datei, "Sperrdatei ist keine normale Datei")
        elif _kann(datei, os.W_OK):
            befund(datei, "Sperrdatei ist beschreibbar – sie gehört Florian, ein Freund liest sie nur")
        elif not _kann(datei, os.R_OK):
            befund(datei, "Sperrdatei ist nicht lesbar – ohne sie rechnet der Freund nie")
    # 5. Zugänge – nur ob sie da sind, nie die Werte
    if not os.environ.get("LEARN_BOT_TOKEN", "").strip():
        befund(inst / ".env", "LEARN_BOT_TOKEN fehlt – ohne eigenen Bot kommt kein Video an")
    if not str(konfig.wert("replay.ich", "") or "").strip():
        befund(inst / ".env", "CLIP_EPIC_ID fehlt – ohne Epic-Konto-ID erkennt die Pipeline die eigenen Kills nicht "
                              "sicher")
    if not _telegram_zahl():
        hinweise.append("noch nicht mit Telegram verbunden – der Bot startet erst danach (Einladungslink über "
                        "benutzer-anlegen.sh)")
    hinweise.append(ki_hinweis(konfig))
    for name in ("HOME", "HF_HOME"):
        if not liegt_in(os.environ.get(name) or "/", inst):
            hinweise.append(f"{name} liegt nicht im eigenen Ordner – das setzen nur die Dienste clip-freund-*@")
    return {"ok": not befunde, "name": str(konfig.wert("instanz.name", inst.name)), "befunde": befunde,
            "hinweise": hinweise, "sperre": info}


def ki_hinweis(konfig: Konfig) -> str:
    """Ein Satz zur KI des Freundes – nur Hinweis, nie Befund. claude installiert Florian selbst (z. B. global per npm),
    nie automatisch; ohne eigenen Zugang bleibt die KI aus (M29/M30)."""
    eigen = str(konfig.wert("instanz.claude", "") or "").strip()
    programm = shutil.which(eigen) if eigen else shutil.which("claude", path=claude_aufruf.SUCHPFAD)
    if programm is None:
        return ("claude ist auf dem Mini nicht installiert – ohne gibt es für Freunde keine KI (Florian installiert es "
                "selbst, z. B. global per npm; nie automatisch)")
    if bereich := claude_verboten(programm):
        return f"claude liegt unter {bereich} – für Freunde nicht nutzbar (global installieren, z. B. /usr/local/bin)"
    if claude_aufruf.instanz_token(konfig) is None:
        return "claude ist da, aber kein eigener Claude-Zugang – die KI bleibt aus (Token aus `claude setup-token`)"
    return "eigener Claude-Zugang: ja"


# --- einrichten ---------------------------------------------------------------------------------------------------

def einrichten(konfig: Konfig, wurzel: Path | None = None) -> dict:
    """Datenbank, Whisper-Modell, Musik – jeweils nur, was fehlt – danach pruefen. Ergebnis wie pruefen, dazu
    "eingerichtet": {"datenbank", "whisper", "musik"} in Worten."""
    _instanz(konfig)
    da = konfig.datenbank.is_file()
    con = db.verbinde(konfig.datenbank)
    try:
        schritte = {"datenbank": "schon da" if da else "angelegt", "whisper": _whisper(konfig),
                    "musik": _musik(con, konfig)}
    finally:
        con.close()
    for was, wie in schritte.items():
        log.info("Einrichten – %s: %s", was, wie)
    return {**pruefen(konfig, wurzel), "eingerichtet": schritte}


def _whisper(konfig: Konfig) -> str:
    """Das Whisper-Modell der Konfig ([stimmung].modell) einmal in den eigenen Cache – wie es die Pipeline sonst beim
    ersten Gebrauch lädt (faster_whisper lädt über download_model in HF_HOME). Liegt es schon dort, wird nichts
    geladen. Nur mit HF_HOME im eigenen Ordner (setzt der Dienst) – sonst landete es im Home dessen, der aufruft."""
    from . import mikro

    inst = _instanz(konfig)
    if not liegt_in(os.environ.get("HF_HOME") or "/", inst):
        return "übersprungen – HF_HOME liegt nicht im eigenen Ordner (nur über clip-freund-einrichten@)"
    if not mikro.whisper_da():
        return "Whisper ist hier nicht installiert – die Szenen werden ohne Sprache gemessen"
    modell = str(konfig.wert("stimmung.modell", "small"))
    try:
        from faster_whisper.utils import download_model
    except Exception as e:  # noqa: BLE001 – kaputte Installation: Szenen ohne Sprache wie bei einem Ladefehler
        return f"Whisper lässt sich nicht laden ({type(e).__name__}) – die Szenen werden ohne Sprache gemessen"
    try:
        download_model(modell, local_files_only=True)
        return f"Modell {modell} schon da"
    except Exception:  # noqa: BLE001 – noch nicht im Cache: jetzt laden
        pass
    try:
        download_model(modell)
    except Exception as e:  # noqa: BLE001 – Netz o. Ä.: die Pipeline misst dann ohne Sprache (M37)
        log.warning("Whisper-Modell %s nicht geladen: %s", modell, e)
        return (f"Modell {modell} nicht geladen ({type(e).__name__}) – Szenen ohne Sprache, nächster Versuch beim "
                "nächsten Einrichten")
    return f"Modell {modell} geladen"


def _musik(con, konfig: Konfig) -> str:
    """Musik wie im einfachen Modus: die Genres aus einstellungen (DEINE_GENRES), bis MUSIK_ZIEL freie Titel – genau
    die Grenze, ab der musik.nachschub im Abend-Lauf (10–17 Uhr, höchstens einmal am Tag) von selbst nachlädt.
    Laden und Messen unter der gemeinsamen Sperre (wie im Abend-Lauf); der Tages-Merker des Nachschubs bleibt frei."""
    from . import einstellungen, musik  # musik braucht numpy

    k = einstellungen.anwenden(con, konfig)
    if einstellungen.experte(con, k):
        return "übersprungen – Experten-Modus (Musik von Hand)"
    genres = [g for g in k.wert("musik.genres_bevorzugt", []) or [] if g in musik.NCS_GENRES]
    frei = musik.frei_je_genre(con, genres)
    fehlen = MUSIK_ZIEL - sum(frei.values())
    if not genres or fehlen <= 0:
        return f"{sum(frei.values())} Songs der Genres schon da"
    try:
        with sperre.sperre(sperre.pfad(k), warten_s=float(k.wert("sperre.warten_s", INSTANZ_WARTEN_S)),
                           melde=log.info):
            neu = musik.ncs_genres_laden(con, k, sorted(genres, key=lambda g: frei[g]), anzahl=fehlen)
    except (sperre.Gesperrt, sperre.SperreFehler):
        return "später – gerade rechnet etwas anderes (die Musik kommt tagsüber von selbst)"
    except OSError as e:
        log.warning("Musik von NCS: %s", e)
        return f"NCS nicht erreichbar ({type(e).__name__}) – die Musik kommt tagsüber von selbst"
    return f"{len(neu)} Songs von NCS geladen"


# --- koppeln: Einladungslink statt Telegram-Zahl (Schritt 8) ----------------------------------------------------------

TELEGRAM_API = "https://api.telegram.org"   # Tests: eine Attrappe auf 127.0.0.1
KOPPELN_FRIST_S = 15 * 60                   # so lange gilt ein Einladungslink
EINLADUNG = "einladung.json"                # I/db: der Link für benutzer-anlegen.sh (0600, nie im Log)
KOPPLUNG = "kopplung.json"                  # I/db: Zahl und Vorname des Freundes (0600), liest benutzer-anlegen.sh
# So liest lernbot.starte die eine erlaubte Telegram-Zahl – steht eine davon in .env, ist er schon verbunden
ZAHL_NAMEN = ("LEARN_BOT_ALLOWED_USER_ID", "TELEGRAM_ALLOWED_USER_ID")
VERBUNDEN = "✅ Verbunden! Ab jetzt kommen deine Videos hier an – nach jedem Spielabend eins."
FALSCHER_LINK = ("Dieser Einladungslink gilt nicht (mehr). Öffne genau den Link, den du bekommen hast – er gilt "
                 "15 Minuten und nur einmal.")


def _telegram_zahl() -> str:
    return next((w for n in ZAHL_NAMEN if (w := os.environ.get(n, "").strip()).isdigit()), "")


def koppeln(konfig: Konfig) -> dict:
    """Einladungslink statt Telegram-Zahl: wartet bis zu KOPPELN_FRIST_S auf „/start <code>“ und schreibt die Zahl des
    Absenders nach I/db/kopplung.json. Ergebnis: {"ok", "name", "gekoppelt", "hinweis"} – nie Code, Link oder Zahl.
    Ohne LEARN_BOT_TOKEN: KonfigFehler. Steht schon eine Zahl in .env, kann sein Bot laufen (je Bot nur ein Empfänger):
    dann nichts tun, Telegram wird gar nicht gefragt."""
    inst = _instanz(konfig)
    name = str(konfig.wert("instanz.name", inst.name))
    token = os.environ.get("LEARN_BOT_TOKEN", "").strip()
    if not token:
        raise KonfigFehler(f"LEARN_BOT_TOKEN fehlt in {inst / '.env'} – ohne eigenen Bot gibt es nichts zu verbinden")
    if any(os.environ.get(n, "").strip() for n in ZAHL_NAMEN):
        return {"ok": False, "name": name, "gekoppelt": False,
                "hinweis": "schon mit Telegram verbunden (Telegram-Zahl in .env) – sein Bot holt die Nachrichten ab, "
                           "je Bot gibt es nur einen Empfänger. Nichts geändert"}
    # Wie im Lern-Bot: httpx loggte sonst jede Adresse samt Bot-Token; DEBUG von telegram zeigte die Nachrichten (Code)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.INFO)
    return {"name": name, **asyncio.run(_koppeln(konfig, inst, token))}


async def _koppeln(konfig: Konfig, inst: Path, token: str) -> dict:
    from telegram import Bot
    from telegram.error import InvalidToken, TelegramError

    from .lernbot import anfragen   # dieselben Anfragen wie der Lern-Bot ([lernbot].nur_ipv4)

    bot_anfragen, update_anfragen = anfragen(konfig)
    bot = Bot(token, base_url=f"{TELEGRAM_API}/bot", request=bot_anfragen, get_updates_request=update_anfragen)
    einladung = inst / "db" / EINLADUNG
    try:
        try:
            await bot.initialize()   # getMe: der Name des Bots für den Link
        except InvalidToken:   # nie den Text weitergeben – PTB schreibt den Token hinein
            return _nicht("Telegram kennt den Bot-Token nicht – bei @BotFather prüfen. Nichts gespeichert")
        except TelegramError as e:
            return _nicht(f"Telegram nicht erreichbar ({type(e).__name__}) – nichts gespeichert, später nochmal")
        code = secrets.token_urlsafe(24)   # 32 Zeichen aus A-Z a-z 0-9 _ - (Telegram erlaubt bis 64), ≈ 192 Bit
        # Mit der Lauf-Nummer von systemd: benutzer-anlegen.sh zeigt nur den Link dieses Laufs, nie einen alten
        _privat_schreiben(einladung, {"lauf": os.environ.get("INVOCATION_ID", ""),
                                      "link": f"https://t.me/{bot.username}?start={code}"})
        log.info("Einladungslink liegt bereit (%s, nur für ihn und root) – gilt %d min und nur einmal", einladung,
                 round(KOPPELN_FRIST_S / 60))
        return await _warte_auf_start(bot, inst, code)
    finally:
        einladung.unlink(missing_ok=True)   # der Code gilt nur in diesem Lauf
        try:
            await bot.shutdown()
        except Exception:  # noqa: BLE001 – das Ergebnis steht schon fest
            pass


def _nicht(grund: str) -> dict:
    return {"ok": False, "gekoppelt": False, "hinweis": grund}


async def _warte_auf_start(bot, inst: Path, code: str) -> dict:
    """Nachrichten des Bots lesen, bis „/start <code>“ kommt oder die Frist um ist. Alles Gelesene wird abgehakt."""
    from telegram.error import BadRequest, Conflict, NetworkError, RetryAfter, TelegramError

    ende = time.monotonic() + KOPPELN_FRIST_S
    offset: int | None = None
    geantwortet: set[int] = set()
    while (rest := ende - time.monotonic()) >= 1:
        try:
            updates = await bot.get_updates(offset=offset, timeout=int(min(rest, 25)), allowed_updates=["message"])
        except Conflict:
            return _nicht("ein anderes Programm holt gerade die Nachrichten dieses Bots ab (läuft sein Bot noch, oder "
                          "steckt der Token woanders?) – je Bot nur ein Empfänger. Nichts gespeichert")
        except BadRequest:   # vor NetworkError (BadRequest ist eine davon) – nochmal fragen hilft nicht
            return _nicht("Telegram lehnt die Abfrage ab (BadRequest) – nichts gespeichert")
        except (RetryAfter, NetworkError) as e:   # zu viele Anfragen, Netz, Zeitüberschreitung: kurz warten, weiter
            log.warning("Telegram: %s – nächster Versuch", type(e).__name__)
            await asyncio.sleep(min(5.0, rest))
            continue
        except TelegramError as e:
            return _nicht(f"Telegram: {type(e).__name__} – nichts gespeichert")
        for update in updates:
            offset = update.update_id + 1   # gelesen = abgehakt; die nächste Abfrage bestätigt es bei Telegram
            nachricht = update.message
            if nachricht is None:
                continue
            if _ist_einladung(nachricht, code):
                absender = nachricht.from_user
                vorname = "".join(z for z in absender.first_name or "" if z.isprintable())[:64]
                ziel = inst / "db" / KOPPLUNG
                _privat_schreiben(ziel, {"id": absender.id, "vorname": vorname, "zeit": iso(jetzt())})
                log.info("Verbunden – die Telegram-Zahl steht in %s", ziel)
                await _antworte(bot, nachricht.chat_id, VERBUNDEN)
                await _abhaken(bot, offset)
                return {"ok": True, "gekoppelt": True,
                        "hinweis": "mit Telegram verbunden – benutzer-anlegen.sh trägt die Zahl ein und schaltet den "
                                   "Bot ein"}
            if (nachricht.chat.type == "private" and (nachricht.text or "").startswith("/start")
                    and nachricht.chat_id not in geantwortet):   # falscher oder alter Link: einmal je Chat sagen
                geantwortet.add(nachricht.chat_id)
                await _antworte(bot, nachricht.chat_id, FALSCHER_LINK)
    if offset is not None:
        await _abhaken(bot, offset)
    return _nicht(f"Frist abgelaufen ({round(KOPPELN_FRIST_S / 60)} min) – der Einladungslink wurde nicht geöffnet. "
                  "Nichts gespeichert; nochmal: benutzer-anlegen.sh (neuer Link)")


def _ist_einladung(nachricht, code: str) -> bool:
    """Genau „/start <code>“ in einem Einzel-Chat, von einem Menschen, der selbst schreibt (nicht weitergeleitet)."""
    absender = nachricht.from_user
    teile = (nachricht.text or "").split()
    return (nachricht.chat.type == "private" and absender is not None and not absender.is_bot
            and absender.id == nachricht.chat_id and nachricht.forward_origin is None and len(teile) == 2
            and teile[0].split("@", 1)[0] == "/start"
            and secrets.compare_digest(teile[1].encode("utf-8"), code.encode("utf-8")))


async def _antworte(bot, chat_id: int, text: str) -> None:
    from telegram.error import TelegramError

    try:
        await bot.send_message(chat_id, text)
    except TelegramError as e:   # die Kopplung steht trotzdem bzw. ein falscher Link bleibt falsch
        log.warning("Antwort an Telegram ging nicht: %s", type(e).__name__)


async def _abhaken(bot, offset: int) -> None:
    """Gelesene Nachrichten bei Telegram bestätigen – sein Bot sieht sie später nicht noch einmal (auch nicht den
    Code)."""
    from telegram.error import TelegramError

    try:
        await bot.get_updates(offset=offset, timeout=0, allowed_updates=["message"])
    except TelegramError as e:
        log.warning("Telegram: Bestätigen ging nicht (%s)", type(e).__name__)


def _privat_schreiben(ziel: Path, daten: dict) -> None:
    """JSON atomar und nur für den Freund lesbar (0600) – wie publikum_adapter.schreibe_cache. Ein Link an der Stelle
    wird ersetzt, nie verfolgt."""
    fd, tmp = tempfile.mkstemp(prefix=f".{ziel.name}-", suffix=".tmp", dir=ziel.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as datei:
            json.dump(daten, datei, ensure_ascii=False)
            datei.flush()
            os.fsync(datei.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, ziel)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


# --- instanz.toml für einen neuen Freund (läuft bei Florian) ----------------------------------------------------------

def _toml_wert(wert: Any) -> str:
    if isinstance(wert, bool):
        return "true" if wert else "false"
    if isinstance(wert, (int, float)):
        return repr(wert)
    if isinstance(wert, str):
        return json.dumps(wert, ensure_ascii=False)   # ein JSON-String ist auch ein gültiger TOML-String
    if isinstance(wert, list):
        return "[" + ", ".join(_toml_wert(w) for w in wert) + "]"
    raise ValueError(f"Wert {wert!r} passt nicht in die instanz.toml")


def instanz_toml(konfig: Konfig, name: str) -> str:
    """Text der instanz.toml für den Freund `name` aus FLORIANS wirksamer Konfig (benutzer-anlegen.sh ruft das als
    pipeline auf): [sperre].datei = sperre.pfad (wie der Einzeiler in alles-aktualisieren.sh, M3) mit warten_s 900,
    [schnitt] encoder/vaapi_geraet und [merkmale.waffen] – nur Schlüssel, die die Repo-Konfig dort kennt (sonst lehnte
    lade_instanz die Datei ab). Keine Hosts, keine MAC, kein Lager, keine Zugänge. Läuft nur bei Florian."""
    if konfig.instanz is not None:
        raise KonfigFehler("instanz_toml liest Florians Konfig – nicht in der Instanz eines Freundes aufrufen")
    if not INSTANZ_NAME.fullmatch(name):
        raise KonfigFehler(f"Name {name!r} ungültig (2–27 Zeichen a-z, 0-9, -, vorn ein Buchstabe)")
    repo = tomllib.loads(STANDARD_KONFIG.read_text(encoding="utf-8"))
    zeilen = [f"# Konfig von {name} – geschrieben von benutzer-anlegen.sh am {date.today():%d.%m.%Y} aus Florians "
              "wirksamer Konfig.",
              "# Gehört root (root:clip-<name>, 0640): Der Freund liest sie, ändern kann er sie nicht. Erlaubt sind nur",
              "# [sperre], [schnitt], [zeit], [merkmale.waffen] und [instanz] (Vorlage: config/instanz.beispiel.toml).",
              "", "[sperre]", "# Die EINE Rechen-Sperre des Mini – Florians Datei, im Dienst nur lesbar eingebunden",
              f"datei = {_toml_wert(str(sperre.pfad(konfig)))}", f"warten_s = {INSTANZ_WARTEN_S}"]
    for abschnitt, kopf, erlaubt in (
            ("schnitt", "# Rechnerwerte wie bei Florian (sonst schnitte der Freund auf dem Prozessor)",
             ("encoder", "vaapi_geraet")),
            ("merkmale.waffen", "# Spielwissen wie bei Florian (GunType-Zahlen aus replay2json)", None)):
        bekannt = Konfig(repo, STANDARD_KONFIG).wert(abschnitt, {}) or {}
        werte = konfig.wert(abschnitt, {}) or {}
        schluessel = [s for s in (erlaubt or tuple(bekannt)) if s in bekannt and s in werte]
        if schluessel:
            zeilen += ["", f"[{abschnitt}]", kopf, *(f"{s} = {_toml_wert(werte[s])}" for s in schluessel)]
    text = "\n".join(zeilen) + "\n"
    tomllib.loads(text)   # wirft, falls doch etwas nicht passt – dann schreibt das Skript nichts
    return text
