"""/tiktok im Lern-Bot (30.09., Stand und Trennen 06.10.): TikTok-Konto für die Display API verbinden, ohne Konsole.

/tiktok            → Stand (tiktok_anmeldung.status_text): verbunden als wer, Anmeldung bis wann, letzte API-Zahlen,
                     Rechte. Nicht verbunden → „❌“ und dazu die Anmelde-Adresse mit kurzer Anleitung.
/tiktok neu        → Anmelde-Adresse mit Anleitung, auch wenn schon verbunden (z. B. nach neuen Rechten im Portal)
/tiktok trennen    → Token-Datei leeren (tiktok_anmeldung.trennen)
/tiktok <adresse>  → die Adresse, auf der TikTok nach dem Zustimmen gelandet ist (oder nur den Code) → Tokens
„trennen“ und „neu“ werden VOR dem Code-Tausch erkannt – sonst ginge das Wort als Code an TikTok.
Die Tokens gehen nur in die private Token-Datei (tiktok_anmeldung.tausche) – nie in den Chat, nie ins Log.
Der Stand liest nur Token-Datei und Datenbank und läuft wie /publikum im Bot-Thread (die SQLite-Verbindung ist
nicht threadsicher); Tausch und Trennen (Netz, Datei) laufen in asyncio.to_thread, damit der Bot nicht hängt.
"""

from __future__ import annotations

import asyncio
import logging

from . import tiktok_anmeldung

log = logging.getLogger("lern-bot")

ANLEITUNG = ("🔗 TikTok verbinden:\n1. Diese Adresse öffnen und mit deinem TikTok-Konto zustimmen:\n{url}\n"
             "2. Danach landet der Browser auf {ziel} – die Seite darf leer oder ein Fehler sein.\n"
             "3. Die ganze Adresse aus der Adresszeile kopieren und hier schicken: /tiktok <adresse>\n"
             "Der Code gilt nur wenige Minuten.")


def _anleitung(konfig) -> str:
    return ANLEITUNG.format(url=tiktok_anmeldung.anmelde_url(konfig), ziel=tiktok_anmeldung.redirect_uri(konfig))


async def cmd_tiktok(update, context) -> None:
    konfig = context.bot_data["konfig"]
    con = context.bot_data["con"]
    nachricht = update.effective_message
    text = " ".join(context.args or []).strip()
    try:
        if text.lower() == "trennen":
            ergebnis = await asyncio.to_thread(tiktok_anmeldung.trennen, konfig)
            await nachricht.reply_text(tiktok_anmeldung.trennen_text(ergebnis))
            return
        if text.lower() == "neu":
            await nachricht.reply_text(_anleitung(konfig), disable_web_page_preview=True)
            return
        if not text:
            stand = tiktok_anmeldung.status(konfig, con)
            antwort = tiktok_anmeldung.status_text(stand, konfig)
            if not stand["verbunden"] and stand.get("zugang"):  # ohne Key/Secret gäbe es keine Adresse
                antwort += "\n" + _anleitung(konfig)
            await nachricht.reply_text(antwort, disable_web_page_preview=True)
            return
        ergebnis = await asyncio.to_thread(tiktok_anmeldung.tausche, konfig, text)
    except tiktok_anmeldung.AnmeldeFehler as fehler:
        await nachricht.reply_text(f"⚠️ {fehler}")
        return
    except Exception:  # Details ins Log (ohne Tokens), dir nur kurz
        log.exception("TikTok-Anmeldung fehlgeschlagen")
        await nachricht.reply_text("⚠️ TikTok-Anmeldung fehlgeschlagen – Details im Log des Lern-Bots.")
        return
    await nachricht.reply_text(tiktok_anmeldung.erfolg_text(ergebnis))


def registriere(app, nur_ich) -> None:
    """Hängt /tiktok in die Lern-Bot-App (aufgerufen von lernbot.baue_app)."""
    from telegram.ext import CommandHandler

    app.add_handler(CommandHandler("tiktok", cmd_tiktok, filters=nur_ich))
