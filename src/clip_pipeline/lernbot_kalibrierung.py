"""/kalibrieren im Lern-Bot (B5, erste Stufe, 30.09.): ein echtes Match als Album aufs Handy.

`/kalibrieren` nimmt das neueste Match mit Clips, `/kalibrieren <Match-ID>` ein bestimmtes. Je Clip kommen drei
Standbilder mit Bildunterschrift (Stimmung mit Sicherheit, Kills mit Waffen-Nummer und Kategorie, Merkmale,
Transkript-Anfang), am Ende die Waffen-Nummern, die in [merkmale.waffen] noch fehlen. Läuft im Hintergrund unter der
Pipeline-Sperre (Whisper ist rechenintensiv), weckt nie. Die Logik steckt in kalibrierung.py.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from . import db, einstellungen, kalibrierung
from .konfig import Konfig

log = logging.getLogger("pipeline")


def baue(konfig: Konfig, sid: str) -> dict:
    """Bericht im Thread – eigene SQLite-Verbindung, gleiche Sperre wie die Pipeline."""
    from .sperre import sperre

    con = db.verbinde(konfig.datenbank)
    try:
        with sperre(konfig.datenbank.with_suffix(".lock"), warten_s=float(konfig.wert("sperre.warten_s", 7200))):
            return kalibrierung.bericht(con, konfig, sid)
    finally:
        con.close()


async def laufe(app, sid: str) -> None:
    chat = app.bot_data["erlaubt"]
    try:
        ergebnis = await asyncio.to_thread(baue, app.bot_data["konfig"], sid)
    except kalibrierung.KalibrierFehler as fehler:
        await app.bot.send_message(chat, f"⚠️ {fehler}")
        return
    except Exception as fehler:  # dir kurz sagen, was los ist – Details ins Log
        log.exception("Kalibrierung fehlgeschlagen")
        await app.bot.send_message(chat, f"⚠️ Kalibrierung fehlgeschlagen: {str(fehler)[:300]}")
        return
    from telegram import InputMediaPhoto

    for clip in ergebnis["clips"]:
        text = kalibrierung.clip_text(clip)
        if clip["bilder"]:
            await app.bot.send_media_group(chat, [InputMediaPhoto(Path(b).read_bytes(), caption=text if i == 0 else None)
                                                  for i, b in enumerate(clip["bilder"])])
        else:
            await app.bot.send_message(chat, text)
    await app.bot.send_message(chat, kalibrierung.schluss_text(ergebnis))


async def cmd_kalibrieren(update, context) -> None:
    con, konfig = context.bot_data["con"], context.bot_data["konfig"]
    sid = (context.args or [None])[0]
    if sid is None:
        neueste = einstellungen.letzte_matches(con, konfig, 1)
        if not neueste:
            await update.effective_message.reply_text("Noch kein Match mit Clips.")
            return
        sid = neueste[0]["id"]
    await update.effective_message.reply_text(f"🧪 Kalibriere Match {sid} – die Alben kommen gleich (Whisper braucht "
                                              "je Clip etwa eine halbe Minute).")
    context.application.create_task(laufe(context.application, sid))


def registriere(app, nur_ich) -> None:
    """Hängt /kalibrieren in die Lern-Bot-App (aufgerufen von lernbot.baue_app)."""
    from telegram.ext import CommandHandler

    app.add_handler(CommandHandler("kalibrieren", cmd_kalibrieren, filters=nur_ich))
