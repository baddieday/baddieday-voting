"""⚙️ Einstellungen im Lern-Bot (29.09.): Werte per Knopf umstellen statt in config/lokal.toml auf dem Server.

/einstellungen oder der Knopf „⚙️ Einstellungen“ zeigt die Liste; ein Tipp auf eine Zeile öffnet ihre Optionen (✓ =
gerade aktiv), „↩️ Standard“ lässt wieder die Datei gelten. Bei „🎯 Clips“ gibt es zusätzlich „📅 Match wählen“ mit den
neuesten Matches. Alles gilt ab dem nächsten Entwurf. Die Logik steckt in einstellungen.py (ohne Telegram testbar).

08.10. (Florian: „wenn ich alles per Hand einstellen muss … es soll autonom sein“): Das Menü gibt es nur noch im
Experten-Modus. Im einfachen Modus zeigt /einstellungen nur, was gerade gilt (uebersicht) – ohne Knöpfe; Knöpfe alter
Menü-Nachrichten ändern dort nichts mehr. Die Tabelle `einstellungen` bleibt, wie sie ist.

Knöpfe (Callback-Daten ≤ 64 Byte): s:m Menü · s:o:<i> Optionen · s:w:<i>:<j> Wert wählen · s:r:<i> Standard ·
s:l Match-Liste · s:x:<match-id> Match wählen.
"""

from __future__ import annotations

import logging
import sqlite3

from . import einstellungen, regeln
from .einstellungen import KATALOG, QUELLE
from .konfig import Konfig
from .verarbeitung import SESSION_ID

log = logging.getLogger("pipeline")
KLICK_MUSTER = r"^s:"
HERKUNFT = {"bot": "📱", "datei": "🗂", "standard": ""}


# --- Texte und Knöpfe (ohne Telegram testbar) ------------------------------------------------

def _alle(con: sqlite3.Connection, konfig: Konfig, alle: bool | None) -> bool:
    """Volles Menü? Ausdrücklich (s:a) oder im Experten-Modus; sonst die vier einfachen (06.10.)."""
    return bool(alle) or einstellungen.experte(con, konfig)


def uebersicht(con: sqlite3.Connection, konfig: Konfig, clip_bot: bool = False) -> str:
    """Einfacher Modus (08.10.): was gerade gilt – ohne Knöpfe. Clips, Szenen, Aufbau und die Genres der Musik
    entscheidet der Bot (einstellungen.EINFACH_FEST; neue Songs lädt musik.nachschub), Länge, Effekte und einzelne
    Songs ändern nur deine ❌-Gründe (regeln.py). clip_bot: der
    Clip-Bot kennt /experte nicht – dort zeigt die letzte Zeile auf den Lern-Bot (Prüfung 08.10.)."""
    k = einstellungen.anwenden(con, konfig)
    _, hinweis = einstellungen.quell_matches(con, k)
    abend = "🎯 nur Spielabend "
    zeilen = ["⚙️ Einstellen musst du nichts – das entscheide ich selbst.",
              "🎯 Clips: dein neuester Spielabend" + (f", {hinweis.removeprefix(abend)}"
                                                     if hinweis and hinweis.startswith(abend) else ""),
              "🎬 Aufbau, Tempo und Zeitlupe lerne ich aus deinen ✅/❌ und den Zuschauerzahlen.",   # Stufe 5, 08.10.
              "🎵 Musik: Techno, Hardstyle, Hardcore und Phonk – neue Songs hole ich mir selbst.",   # Stufe 4, 08.10.
              regeln.regeln_zeile(con, k)]
    if (mindestens := float(k.wert("regie.short_mindestens_s", 0.0) or 0.0)) > 0 and not regeln.ziel_regel(con, k):
        zeilen.append(f"⏱️ Shorts nie kürzer als {mindestens:.0f} s – so hast du es eingestellt.")
    zeilen += ["Länge, Effekte und Songs änderst du mit deinem Grund unter ❌.",
               "🔧 Alles von Hand: /experte im Lern-Bot" if clip_bot else "🔧 Alles von Hand: /experte"]
    return "\n".join(zeilen)


def menue_text(con: sqlite3.Connection, konfig: Konfig, meldung: str | None = None, alle: bool | None = None,
               clip_bot: bool = False) -> str:
    if not einstellungen.experte(con, konfig):   # 08.10.: einfacher Modus – nur anzeigen, was gilt
        return uebersicht(con, konfig, clip_bot)
    alle = _alle(con, konfig, alle)
    teile = [f"✓ {meldung}" if meldung else None,
             "⚙️ Einstellungen – gelten ab dem nächsten Video." if not alle else
             "⚙️ Einstellungen – gelten sofort (Clip-Bot) bzw. ab dem nächsten Entwurf.",
             "📱 = hier im Bot gesetzt · 🗂 = aus der Konfigdatei · ohne Zeichen = Standard" if alle else None]
    for e, wert, herkunft in einstellungen.aktuell(con, konfig):   # 📱/🗂 nur im vollen Menü – nur dort erklärt (07.10.)
        if alle or e.schluessel in einstellungen.EINFACH:
            teile.append(f"{e.titel}: {einstellungen.anzeige(e, wert, con, konfig)} {HERKUNFT[herkunft] if alle else ''}"
                         .rstrip())
    _, hinweis = einstellungen.quell_matches(con, einstellungen.anwenden(con, konfig))
    if hinweis:
        teile.append(f"Gerade: {hinweis}")
    return "\n".join(t for t in teile if t)


def menue_knoepfe(con: sqlite3.Connection, konfig: Konfig, alle: bool | None = None) -> list[list[tuple[str, str]]]:
    if not einstellungen.experte(con, konfig):   # 08.10.: einfacher Modus – keine Wert-Knöpfe
        return []
    alle = _alle(con, konfig, alle)
    reihen = [[(f"{e.titel}: {einstellungen.anzeige(e, wert, con, konfig)}", f"s:o:{i}")]
              for i, (e, wert, _h) in enumerate(einstellungen.aktuell(con, konfig))
              if alle or e.schluessel in einstellungen.EINFACH]
    if not alle:
        reihen.append([("🔧 Alle Einstellungen", "s:a")])
    return reihen


def optionen_text(i: int) -> str:
    e = KATALOG[i]
    return f"{e.titel}\n{e.hilfe}"


def optionen_knoepfe(con: sqlite3.Connection, konfig: Konfig, i: int) -> list[list[tuple[str, str]]]:
    e, wert, _herkunft = einstellungen.aktuell(con, konfig)[i]
    reihen = [[(("✓ " if w == wert else "") + text, f"s:w:{i}:{j}")] for j, (w, text) in enumerate(e.optionen)]
    if e.schluessel == QUELLE:
        gewaehlt = isinstance(wert, str) and wert.startswith("match:") and wert != einstellungen.NEUESTES_MATCH
        reihen.append([(("✓ " if gewaehlt else "") + "📅 Match wählen", "s:l")])
    zurueck = "s:m" if e.schluessel in einstellungen.EINFACH else "s:a"
    reihen.append([("↩️ Standard", f"s:r:{i}"), ("⬅️ zurück", zurueck)])
    return reihen


def match_knoepfe(con: sqlite3.Connection, konfig: Konfig) -> list[list[tuple[str, str]]]:
    reihen = [[(einstellungen.match_zeile(z, konfig), f"s:x:{z['id']}")]
              for z in einstellungen.letzte_matches(con, konfig) if len(f"s:x:{z['id']}".encode()) <= 64]
    i = next(n for n, e in enumerate(KATALOG) if e.schluessel == QUELLE)
    return reihen + [[("⬅️ zurück", f"s:o:{i}")]]


def verarbeite_klick(con: sqlite3.Connection, konfig: Konfig, daten: str,
                     clip_bot: bool = False) -> tuple[str, list, str | None]:
    """(Text, Knöpfe, kurze Antwort) für einen Knopf s:… – ändert bei s:w/s:r/s:x die Einstellung. ValueError bei
    unbekanntem Knopf (der Aufrufer antwortet dann „Unbekannter Knopf.“)."""
    teile = daten.split(":")
    art = teile[1] if len(teile) > 1 else ""
    if not einstellungen.experte(con, konfig):   # 08.10.: alte Menü-Knöpfe ändern im einfachen Modus nichts mehr
        return uebersicht(con, konfig, clip_bot), [], "Das entscheide ich jetzt selbst."
    if art == "m":
        return menue_text(con, konfig), menue_knoepfe(con, konfig), None
    if art == "a":   # 06.10.: alle Einstellungen (das einfache Menü zeigt nur vier)
        return menue_text(con, konfig, alle=True), menue_knoepfe(con, konfig, alle=True), None
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
        alle = e.schluessel not in einstellungen.EINFACH or None   # zurück ins Menü, aus dem der Wert kam
        if art == "r":
            einstellungen.zuruecksetzen(con, e.schluessel)
            zurueck = ("wieder aus der Datei bzw. Standard" if _alle(con, konfig, alle)
                       else "wieder Standard")   # einfaches Menü: ohne „Datei“ (07.10.)
            return menue_text(con, konfig, f"{e.titel}: {zurueck}", alle), \
                menue_knoepfe(con, konfig, alle), "Standard"
        if len(teile) == 4 and teile[3].isdigit() and int(teile[3]) < len(e.optionen):
            wert, text = e.optionen[int(teile[3])]
            einstellungen.setze(con, e.schluessel, wert)
            return menue_text(con, konfig, f"{e.titel}: {text}", alle), menue_knoepfe(con, konfig, alle), "Gespeichert"
    raise ValueError(f"Unbekannter Einstellungs-Knopf {daten!r}")


# --- Telegram ------------------------------------------------------------------------------

def _markup(knoepfe):
    """Inline-Knöpfe; ohne Knöpfe (einfacher Modus) eine leere Leiste – entfernt beim Bearbeiten die alten Knöpfe."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=d) for t, d in reihe] for reihe in knoepfe])


async def cmd_einstellungen(update, context) -> None:
    con, konfig = context.bot_data["con"], context.bot_data["konfig"]
    knoepfe = menue_knoepfe(con, konfig)   # einfacher Modus (08.10.): keine – nur die Übersicht
    await update.effective_message.reply_text(menue_text(con, konfig, clip_bot=bool(context.bot_data.get("clip_bot"))),
                                              reply_markup=_markup(knoepfe) if knoepfe else None)


async def bei_klick(update, context) -> None:
    query = update.callback_query
    if query.from_user is None or query.from_user.id != context.bot_data["erlaubt"]:
        await query.answer("Nicht erlaubt.")
        return
    con, konfig = context.bot_data["con"], context.bot_data["konfig"]
    try:
        text, knoepfe, antwort = verarbeite_klick(con, konfig, query.data or "", bool(context.bot_data.get("clip_bot")))
    except ValueError:
        await query.answer("Unbekannter Knopf.")
        return
    await query.answer(antwort)
    try:
        await query.edit_message_text(text, reply_markup=_markup(knoepfe))
    except Exception as fehler:  # z. B. „message is not modified“ bei einem Doppeltipp – kein Problem
        log.info("Einstellungen: Nachricht nicht geändert (%s)", fehler)


def registriere(app, nur_ich) -> None:
    """Hängt /einstellungen und die s:-Knöpfe in die App (Lern-Bot und Clip-Bot; aufgerufen von lernbot.baue_app und
    bot.app.baue_app – dort VOR dem musterlosen Klick-Handler)."""
    from telegram.ext import CallbackQueryHandler, CommandHandler

    app.add_handler(CommandHandler("einstellungen", cmd_einstellungen, filters=nur_ich))
    app.add_handler(CallbackQueryHandler(bei_klick, pattern=KLICK_MUSTER))
