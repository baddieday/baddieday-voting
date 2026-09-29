"""⚙️ Einstellungen im Lern-Bot (29.09.): Werte per Knopf umstellen statt in config/lokal.toml auf dem Server.

/einstellungen oder der Knopf „⚙️ Einstellungen“ zeigt die Liste; ein Tipp auf eine Zeile öffnet ihre Optionen (✓ =
gerade aktiv), „↩️ Standard“ lässt wieder die Datei gelten. Bei „🎯 Clips“ gibt es zusätzlich „📅 Match wählen“ mit den
neuesten Matches. Alles gilt ab dem nächsten Entwurf. Die Logik steckt in einstellungen.py (ohne Telegram testbar).

Knöpfe (Callback-Daten ≤ 64 Byte): s:m Menü · s:o:<i> Optionen · s:w:<i>:<j> Wert wählen · s:r:<i> Standard ·
s:l Match-Liste · s:x:<match-id> Match wählen.
"""

from __future__ import annotations

import logging
import sqlite3

from . import einstellungen
from .einstellungen import KATALOG, QUELLE
from .konfig import Konfig
from .verarbeitung import SESSION_ID

log = logging.getLogger("pipeline")
KLICK_MUSTER = r"^s:"
HERKUNFT = {"bot": "📱", "datei": "🗂", "standard": ""}


# --- Texte und Knöpfe (ohne Telegram testbar) ------------------------------------------------

def menue_text(con: sqlite3.Connection, konfig: Konfig, meldung: str | None = None) -> str:
    teile = [f"✓ {meldung}" if meldung else None,
             "⚙️ Einstellungen – gelten ab dem nächsten Entwurf.",
             "📱 = hier im Bot gesetzt · 🗂 = aus der Konfigdatei · ohne Zeichen = Standard"]
    for e, wert, herkunft in einstellungen.aktuell(con, konfig):
        teile.append(f"{e.titel}: {einstellungen.anzeige(e, wert, con, konfig)} {HERKUNFT[herkunft]}".rstrip())
    _, hinweis = einstellungen.quell_matches(con, einstellungen.anwenden(con, konfig))
    if hinweis:
        teile.append(f"Gerade: {hinweis}")
    return "\n".join(t for t in teile if t)


def menue_knoepfe(con: sqlite3.Connection, konfig: Konfig) -> list[list[tuple[str, str]]]:
    return [[(f"{e.titel}: {einstellungen.anzeige(e, wert, con, konfig)}", f"s:o:{i}")]
            for i, (e, wert, _h) in enumerate(einstellungen.aktuell(con, konfig))]


def optionen_text(i: int) -> str:
    e = KATALOG[i]
    return f"{e.titel}\n{e.hilfe}"


def optionen_knoepfe(con: sqlite3.Connection, konfig: Konfig, i: int) -> list[list[tuple[str, str]]]:
    e, wert, _herkunft = einstellungen.aktuell(con, konfig)[i]
    reihen = [[(("✓ " if w == wert else "") + text, f"s:w:{i}:{j}")] for j, (w, text) in enumerate(e.optionen)]
    if e.schluessel == QUELLE:
        gewaehlt = isinstance(wert, str) and wert.startswith("match:") and wert != einstellungen.NEUESTES_MATCH
        reihen.append([(("✓ " if gewaehlt else "") + "📅 Match wählen", "s:l")])
    reihen.append([("↩️ Standard", f"s:r:{i}"), ("⬅️ zurück", "s:m")])
    return reihen


def match_knoepfe(con: sqlite3.Connection, konfig: Konfig) -> list[list[tuple[str, str]]]:
    reihen = [[(einstellungen.match_zeile(z, konfig), f"s:x:{z['id']}")]
              for z in einstellungen.letzte_matches(con, konfig) if len(f"s:x:{z['id']}".encode()) <= 64]
    i = next(n for n, e in enumerate(KATALOG) if e.schluessel == QUELLE)
    return reihen + [[("⬅️ zurück", f"s:o:{i}")]]


def verarbeite_klick(con: sqlite3.Connection, konfig: Konfig, daten: str) -> tuple[str, list, str | None]:
    """(Text, Knöpfe, kurze Antwort) für einen Knopf s:… – ändert bei s:w/s:r/s:x die Einstellung. ValueError bei
    unbekanntem Knopf (der Aufrufer antwortet dann „Unbekannter Knopf.“)."""
    teile = daten.split(":")
    art = teile[1] if len(teile) > 1 else ""
    if art == "m":
        return menue_text(con, konfig), menue_knoepfe(con, konfig), None
    if art == "l":
        return "📅 Welches Match? (die neuesten)", match_knoepfe(con, konfig), None
    if art == "x" and len(teile) == 3 and SESSION_ID.fullmatch(teile[2]):
        einstellungen.setze(con, QUELLE, f"match:{teile[2]}")
        meldung = f"🎯 Clips: {einstellungen.anzeige(einstellungen.NACH_SCHLUESSEL[QUELLE], 'match:' + teile[2], con, konfig)}"
        return menue_text(con, konfig, meldung), menue_knoepfe(con, konfig), "Gespeichert"
    if art in ("o", "w", "r") and len(teile) >= 3 and teile[2].isdigit() and int(teile[2]) < len(KATALOG):
        i = int(teile[2])
        e = KATALOG[i]
        if art == "o":
            return optionen_text(i), optionen_knoepfe(con, konfig, i), None
        if art == "r":
            einstellungen.zuruecksetzen(con, e.schluessel)
            return menue_text(con, konfig, f"{e.titel}: wieder aus der Datei bzw. Standard"), \
                menue_knoepfe(con, konfig), "Standard"
        if len(teile) == 4 and teile[3].isdigit() and int(teile[3]) < len(e.optionen):
            wert, text = e.optionen[int(teile[3])]
            einstellungen.setze(con, e.schluessel, wert)
            return menue_text(con, konfig, f"{e.titel}: {text}"), menue_knoepfe(con, konfig), "Gespeichert"
    raise ValueError(f"Unbekannter Einstellungs-Knopf {daten!r}")


# --- Telegram ------------------------------------------------------------------------------

def _markup(knoepfe):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=d) for t, d in reihe] for reihe in knoepfe])


async def cmd_einstellungen(update, context) -> None:
    con, konfig = context.bot_data["con"], context.bot_data["konfig"]
    await update.effective_message.reply_text(menue_text(con, konfig), reply_markup=_markup(menue_knoepfe(con, konfig)))


async def bei_klick(update, context) -> None:
    query = update.callback_query
    if query.from_user is None or query.from_user.id != context.bot_data["erlaubt"]:
        await query.answer("Nicht erlaubt.")
        return
    con, konfig = context.bot_data["con"], context.bot_data["konfig"]
    try:
        text, knoepfe, antwort = verarbeite_klick(con, konfig, query.data or "")
    except ValueError:
        await query.answer("Unbekannter Knopf.")
        return
    await query.answer(antwort)
    try:
        await query.edit_message_text(text, reply_markup=_markup(knoepfe))
    except Exception as fehler:  # z. B. „message is not modified“ bei einem Doppeltipp – kein Problem
        log.info("Einstellungen: Nachricht nicht geändert (%s)", fehler)


def registriere(app, nur_ich) -> None:
    """Hängt /einstellungen und die s:-Knöpfe in die Lern-Bot-App (aufgerufen von lernbot.baue_app)."""
    from telegram.ext import CallbackQueryHandler, CommandHandler

    app.add_handler(CommandHandler("einstellungen", cmd_einstellungen, filters=nur_ich))
    app.add_handler(CallbackQueryHandler(bei_klick, pattern=KLICK_MUSTER))
