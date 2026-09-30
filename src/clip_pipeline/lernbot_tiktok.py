"""/tiktok im Lern-Bot (30.09.): TikTok-Konto für die Display API verbinden, ohne Konsole.

/tiktok            → Anmelde-Adresse mit kurzer Anleitung
/tiktok <adresse>  → die Adresse, auf der TikTok nach dem Zustimmen gelandet ist (oder nur den Code) → Tokens
Die Tokens gehen nur in die private Token-Datei (tiktok_anmeldung.tausche) – nie in den Chat, nie ins Log.
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


async def cmd_tiktok(update, context) -> None:
    konfig = context.bot_data["konfig"]
    text = " ".join(context.args or []).strip()
    try:
        if not text:
            await update.effective_message.reply_text(
                ANLEITUNG.format(url=tiktok_anmeldung.anmelde_url(konfig),
                                 ziel=tiktok_anmeldung.redirect_uri(konfig)), disable_web_page_preview=True)
            return
        ergebnis = await asyncio.to_thread(tiktok_anmeldung.tausche, konfig, text)
    except tiktok_anmeldung.AnmeldeFehler as fehler:
        await update.effective_message.reply_text(f"⚠️ {fehler}")
        return
    except Exception:  # Details ins Log (ohne Tokens), dir nur kurz
        log.exception("TikTok-Anmeldung fehlgeschlagen")
        await update.effective_message.reply_text("⚠️ TikTok-Anmeldung fehlgeschlagen – Details im Log des Lern-Bots.")
        return
    await update.effective_message.reply_text(tiktok_anmeldung.erfolg_text(ergebnis))


def registriere(app, nur_ich) -> None:
    """Hängt /tiktok in die Lern-Bot-App (aufgerufen von lernbot.baue_app)."""
    from telegram.ext import CommandHandler

    app.add_handler(CommandHandler("tiktok", cmd_tiktok, filters=nur_ich))
