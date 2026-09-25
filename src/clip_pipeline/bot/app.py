"""Telegram-Anbindung (python-telegram-bot, Polling).

Dieser Bot ist der EINZIGE Empfänger von Updates für seinen Token. n8n darf über
denselben Token höchstens Nachrichten senden, nie einen Telegram-Trigger benutzen.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from datetime import timedelta
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaVideo, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, Conflict
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, filters

from .. import caption, db, erwartung, highlight, lernen, publikum, shorts
from ..konfig import Konfig, SpeicherOffline
from ..medien import MedienFehler
from ..zeit import aus_iso, iso, jetzt
from . import aktionen, texte

log = logging.getLogger("clip-bot")


def _markup(knoepfe: aktionen.Knoepfe | None) -> InlineKeyboardMarkup | None:
    if knoepfe is None:
        return None
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=d) for t, d in reihe] for reihe in knoepfe])


def _daten(context: ContextTypes.DEFAULT_TYPE):
    return context.bot_data["con"], context.bot_data["konfig"], context.bot_data["erlaubt"]


def _clip_text(con, konfig: Konfig, clip_id: int) -> str:
    clip = db.clip(con, clip_id)
    return texte.clip_text(clip, db.match(con, clip["match_id"]), konfig.wert("zeit.zeitzone", "Europe/Berlin"))


def _upload_text(con, clip_id: int, stand: dict[str, bool]) -> str:
    """Upload-Checkliste (texte.upload_text) und darunter je Post der Lernschleife eine Zeile mit seiner Nummer.
    Die Nummer brauchst du für die Zahlen: Screenshot aus der TikTok-Statistik an den Lern-Bot, Bildunterschrift
    „#17“ (Spec §7.1). Ohne Post (z. B. nur clip-battle.de abgehakt) keine Zeile. Nur Datenbank, kein Dateizugriff."""
    text = texte.upload_text(db.clip(con, clip_id), stand)
    zeilen = [
        f"📈 Post #{post['id']} ({aktionen.PLATTFORM_NAMEN[plattform]}) – für die Zahlen: Screenshot an den Lern-Bot, "
        f"Bildunterschrift #{post['id']}"
        # alle Post-Plattformen: auch ein Post von früher zählt, wenn die Plattform nicht mehr in [publikum] steht
        for plattform in publikum.PLATTFORMEN
        if (post := publikum.post_zu(con, "clip", clip_id, plattform)) is not None
    ]
    return "\n\n".join([text, "\n".join(zeilen)]) if zeilen else text


# --- Outbox: neue Clips verschicken -------------------------------------------

async def sende_meldungen(app: Application) -> int:
    """Kurze Hinweise (z. B. Kills ohne Aufnahme) – brauchen keinen Speicher, gehen also immer.
    Meldungen aus Puffer/Lager (Morgenprüfung, Abgleich) warten die Ruhezeit ab ([telegram].leise_von/leise_bis)."""
    con, konfig, chat = app.bot_data["con"], app.bot_data["konfig"], app.bot_data["erlaubt"]
    gesendet = 0
    for m in aktionen.faellige_meldungen(con, konfig):
        await app.bot.send_message(chat, m["text"])
        con.execute("UPDATE meldungen SET gesendet = ? WHERE id = ?", (iso(jetzt()), m["id"]))
        gesendet += 1
    return gesendet


async def sende_outbox(app: Application) -> int:
    con, konfig, chat = app.bot_data["con"], app.bot_data["konfig"], app.bot_data["erlaubt"]
    zeilen = aktionen.outbox(con)
    highlights = aktionen.highlight_outbox(con)
    if not zeilen and not highlights:
        return 0
    try:
        konfig.pruefe_speicher()
    except SpeicherOffline as e:
        log.info("Outbox wartet: %s", e)
        return 0
    gesendet = 0
    leise = aktionen.ruhezeit(konfig)  # nachts kommen Clips weiter sofort, aber ohne Ton (gilt auch ohne [lager])
    for z in zeilen:
        pfad = konfig.absolut(z["vorschau_pfad"]) if z["vorschau_pfad"] else None
        if pfad is None or not pfad.is_file():
            log.warning("Vorschau für Clip #%s fehlt: %s", z["id"], pfad)
            continue
        with pfad.open("rb") as datei:
            nachricht = await app.bot.send_video(
                chat_id=chat, video=datei, caption=_clip_text(con, konfig, z["id"]), parse_mode=ParseMode.HTML,
                reply_markup=_markup(aktionen.knoepfe_neu(z["id"])), supports_streaming=True,
                disable_notification=leise, read_timeout=300, write_timeout=300, connect_timeout=30,
            )
        aktionen.als_gesendet(con, z["id"], nachricht.message_id, nachricht.video.file_id if nachricht.video else None)
        gesendet += 1
    for h in highlights:
        pfad = konfig.absolut(h["vorschau"]) if h["vorschau"] else None
        if pfad is None or not pfad.is_file():
            log.warning("Vorschau für Highlight %s fehlt: %s", h["name"], pfad)
            continue
        with pfad.open("rb") as datei:
            nachricht = await app.bot.send_video(
                chat_id=chat, video=datei, caption=texte.highlight_text(h), parse_mode=ParseMode.HTML,
                reply_markup=_markup(aktionen.knoepfe_highlight(h["id"])), supports_streaming=True,
                disable_notification=leise, read_timeout=300, write_timeout=300, connect_timeout=30,
            )
        aktionen.highlight_gesendet(con, h["id"], nachricht.message_id)
        gesendet += 1
    return gesendet


async def erinnere(app: Application) -> bool:
    """Erinnert an Clips, die seit über erinnerung_h freigegeben, aber nicht überall hochgeladen sind."""
    con, konfig, chat = app.bot_data["con"], app.bot_data["konfig"], app.bot_data["erlaubt"]
    stunden = float(konfig.wert("veroeffentlichung.erinnerung_h", 24))
    grenze = jetzt() - timedelta(hours=stunden)
    letzte = con.execute("SELECT zeit FROM ereignisse WHERE art = 'erinnerung' ORDER BY id DESC LIMIT 1").fetchone()
    if letzte and aus_iso(letzte["zeit"]) > grenze:
        return False
    offen = [(c, f) for c, f in aktionen.offene_uploads(con, konfig) if c["entschieden"] and aus_iso(c["entschieden"]) < grenze]
    if not offen:
        return False
    await app.bot.send_message(chat, "⏰ " + texte.offene_uploads_text(offen), parse_mode=ParseMode.HTML)
    db.protokoll(con, "erinnerung", f"{len(offen)} Clip(s) noch nicht überall hochgeladen")
    return True


async def _outbox_schleife(app: Application) -> None:
    intervall = float(app.bot_data["konfig"].wert("telegram.outbox_intervall_s", 30))
    while True:
        for aufgabe in (sende_meldungen, sende_outbox, erinnere):
            try:
                await aufgabe(app)
            except Exception:  # der Bot soll wegen eines Versandfehlers nicht sterben
                log.exception("Fehler in %s", aufgabe.__name__)
        await asyncio.sleep(intervall)


async def _nach_start(app: Application) -> None:
    app.bot_data["outbox"] = asyncio.create_task(_outbox_schleife(app))
    log.info("Bot läuft. Outbox alle %s s.", app.bot_data["konfig"].wert("telegram.outbox_intervall_s", 30))


async def _vor_ende(app: Application) -> None:
    if aufgabe := app.bot_data.get("outbox"):
        aufgabe.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await aufgabe


# --- Befehle --------------------------------------------------------------------

async def cmd_hilfe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(texte.HILFE, parse_mode=ParseMode.HTML)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    con, konfig, _ = _daten(context)
    try:
        konfig.pruefe_speicher()
        speicher = "online"
    except SpeicherOffline:
        speicher = "offline (großer Host schläft?)"
    letzte = con.execute("SELECT id, status FROM matches ORDER BY start_utc DESC LIMIT 1").fetchone()
    text = texte.status_text(db.anzahl_je_status(con), speicher, f"{letzte['id']} ({letzte['status']})" if letzte else None,
                             lager=_lager_zeile(con, konfig))
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


def _lager_zeile(con, konfig: Konfig) -> str | None:
    """Im getrennten Betrieb (E19) eine Zeile zum Lager. Nur lesen: Tabelle + Puffer – weckt nie, fasst das Lager
    nicht an. Ohne getrennten Betrieb None."""
    if not konfig.getrennt:
        return None
    from .. import lager

    try:
        return lager.status(con, konfig)["zeile"]
    except Exception as e:  # /status soll trotzdem antworten
        log.warning("Lager-Status: %s", e)
        return f"Lager: Stand nicht lesbar ({str(e)[:100]})"


async def cmd_offen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    con, konfig, chat = _daten(context)
    zeilen = con.execute("SELECT id, tg_file_id FROM clips WHERE status = 'gesendet' ORDER BY id").fetchall()
    if not zeilen:
        await update.effective_message.reply_text("Nichts offen 👍")
        return
    for z in zeilen:
        if not z["tg_file_id"]:
            continue
        nachricht = await context.bot.send_video(
            chat_id=chat, video=z["tg_file_id"], caption=_clip_text(con, konfig, z["id"]),
            parse_mode=ParseMode.HTML, reply_markup=_markup(aktionen.knoepfe_neu(z["id"])),
        )
        con.execute("UPDATE clips SET tg_nachricht_id = ? WHERE id = ?", (nachricht.message_id, z["id"]))


async def sende_battle(context: ContextTypes.DEFAULT_TYPE) -> None:
    con, _, chat = _daten(context)
    ergebnis = aktionen.neues_battle(con)
    if ergebnis is None:
        await context.bot.send_message(chat, "Für ein Battle brauche ich mindestens 2 freigegebene Clips.")
        return
    battle_id, a, b = ergebnis
    await context.bot.send_media_group(
        chat,
        [
            InputMediaVideo(a["tg_file_id"], caption=f"A · #{a['id']} {a['titel']}"),
            InputMediaVideo(b["tg_file_id"], caption=f"B · #{b['id']} {b['titel']}"),
        ],
    )
    # An Alben erlaubt Telegram keine Knöpfe -> eigene Nachricht direkt darunter
    nachricht = await context.bot.send_message(
        chat, texte.battle_frage(battle_id, a, b), parse_mode=ParseMode.HTML,
        reply_markup=_markup(aktionen.knoepfe_battle(battle_id)),
    )
    aktionen.merke_battle_nachricht(con, battle_id, nachricht.message_id)


async def cmd_battle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await sende_battle(context)


async def cmd_rangliste(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    con, konfig, _ = _daten(context)
    zeilen, saison = aktionen.rangliste(con, konfig)
    await update.effective_message.reply_text(texte.rangliste_text(zeilen, saison), parse_mode=ParseMode.HTML)


async def cmd_gewichte(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    con, konfig, _ = _daten(context)
    version, ergebnis = lernen.aktualisiere(con, konfig)
    text = texte.gewichte_text(ergebnis, version)
    if zusatz := erwartung.trefferquote_text(con, konfig):  # Spec §10.5 – leer, solange nichts zu zeigen ist
        text += "\n" + escape(zusatz)
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


async def sende_paket(context: ContextTypes.DEFAULT_TYPE, clip_id: int) -> None:
    """Short (als Datei, unverändert) + Caption zum Kopieren + Checkliste mit Häkchen je Plattform."""
    con, konfig, chat = _daten(context)
    clip = db.clip(con, clip_id)
    if clip is None or clip["status"] not in db.BEWERTET:
        await context.bot.send_message(chat, "Upload-Pakete gibt es nur für freigegebene Clips.")
        return
    try:
        konfig.pruefe_speicher()
    except SpeicherOffline:
        await context.bot.send_message(chat, "💤 Der große Host schläft – ich wecke ihn (bis zu 3 Minuten) …")
        try:
            await asyncio.to_thread(konfig.pruefe_speicher, True)
        except SpeicherOffline:
            await context.bot.send_message(chat, f"⚠️ Host nicht aufgewacht – später nochmal: /paket {clip_id}")
            return
    ziel = konfig.absolut(clip["short_pfad"]) if clip["short_pfad"] else None
    if ziel is None or not ziel.is_file():
        ziel = shorts.ziel_fuer(konfig, clip)
        await context.bot.send_message(chat, f"🎬 Rendere Short für Clip #{clip_id} …")
        try:
            # Rendern dauert: in einem eigenen Thread, damit der Bot weiter reagiert (DB bleibt im Haupt-Thread)
            await asyncio.to_thread(shorts.rendere, konfig.absolut(clip["clip_pfad"]), ziel, konfig)
        except MedienFehler as e:
            await context.bot.send_message(chat, f"⚠️ Short fehlgeschlagen: {escape(str(e)[:300])}")
            return
        con.execute("UPDATE clips SET short_pfad = ?, geaendert = ? WHERE id = ?", (konfig.relativ(ziel), iso(jetzt()), clip_id))
    match = db.match(con, clip["match_id"])
    text = await asyncio.to_thread(caption.baue, dict(clip), dict(match) if match else None, konfig)
    with ziel.open("rb") as datei:
        await context.bot.send_document(
            chat, document=datei, filename=f"clip-battle_{clip_id}.mp4", caption=f"📦 Short für Clip #{clip_id}",
            read_timeout=300, write_timeout=300, connect_timeout=30,
        )
    await context.bot.send_message(chat, f"<pre>{escape(text)}</pre>", parse_mode=ParseMode.HTML)
    stand = aktionen.upload_stand(con, clip_id, konfig)
    await context.bot.send_message(
        chat, texte.upload_text(clip, stand), parse_mode=ParseMode.HTML,
        reply_markup=_markup(aktionen.knoepfe_upload(clip_id, stand)),
    )


async def cmd_paket(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text("Aufruf: /paket <Clip-Nummer>")
        return
    await sende_paket(context, int(context.args[0]))


async def cmd_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    con, konfig, _ = _daten(context)
    if len(context.args or []) != 2 or not context.args[0].isdigit():
        await update.effective_message.reply_text("Aufruf: /link <Clip-Nummer> <https://…>")
        return
    clip_id = int(context.args[0])
    antwort, stand = aktionen.link_speichern(con, clip_id, context.args[1], konfig)
    # escape: der Hinweis kann einen Fehlergrund mit „<“ enthalten – als HTML würde Telegram die Antwort ablehnen
    text = escape(antwort.hinweis) if stand is None else _upload_text(con, clip_id, stand)
    await update.effective_message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=_markup(antwort.knoepfe) if stand is not None else None
    )


async def cmd_uploads(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    con, konfig, _ = _daten(context)
    text = texte.offene_uploads_text(aktionen.offene_uploads(con, konfig))
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


# --- Knöpfe ---------------------------------------------------------------------

async def bei_klick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    con, konfig, erlaubt = _daten(context)
    if query.from_user is None or query.from_user.id != erlaubt:
        await query.answer("Nicht erlaubt.")
        return
    try:
        aktion, nummer, extra = aktionen.parse(query.data)
    except ValueError:
        await query.answer("Unbekannter Knopf.")
        return

    if aktion == "n":
        await query.answer()
        await sende_battle(context)
        return

    if aktion == "p":
        await query.answer("📦 Paket wird gebaut …")
        await sende_paket(context, nummer)
        return

    if aktion in ("hf", "hv"):
        h = highlight.entscheide(con, nummer, freigeben=aktion == "hf")
        await query.answer("Highlight nicht gefunden" if h is None else ("✅ Freigegeben" if aktion == "hf" else "🗑️ Verworfen"))
        if h is not None:
            with contextlib.suppress(BadRequest):
                await query.edit_message_caption(caption=texte.highlight_text(h), parse_mode=ParseMode.HTML, reply_markup=None)
        return

    if aktion in aktionen.PLATTFORM_KUERZEL:
        antwort, stand = aktionen.plattform_erledigt(con, nummer, aktionen.PLATTFORM_KUERZEL[aktion], konfig)
        await query.answer(antwort.hinweis)
        if stand is None:  # nicht abgehakt (Grund steht in der Antwort) – die Checkliste bleibt, wie sie war
            return
        with contextlib.suppress(BadRequest):
            await query.edit_message_text(
                _upload_text(con, nummer, stand), parse_mode=ParseMode.HTML, reply_markup=_markup(antwort.knoepfe)
            )
        return

    if aktion == "b":
        antwort, battle = aktionen.entscheide_battle(con, nummer, extra, konfig)
        await query.answer(antwort.hinweis)  # genau eine Antwort pro Klick
        if battle is not None and battle["ergebnis"] is not None:
            with contextlib.suppress(BadRequest):  # "message is not modified" bei Doppelklick
                await query.edit_message_text(
                    texte.battle_ergebnis(battle), parse_mode=ParseMode.HTML, reply_markup=_markup(antwort.knoepfe)
                )
            _lernen(con, konfig)
        return

    if aktion in ("f", "v"):
        antwort = aktionen.entscheide(con, nummer, "freigegeben" if aktion == "f" else "verworfen")
    else:
        antwort = aktionen.rueckgaengig(con, nummer)
    await query.answer(antwort.hinweis)
    if db.clip(con, nummer) is not None:
        with contextlib.suppress(BadRequest):
            await query.edit_message_caption(
                caption=_clip_text(con, konfig, nummer), parse_mode=ParseMode.HTML,
                reply_markup=_markup(antwort.knoepfe) if antwort.knoepfe is not None else query.message.reply_markup,
            )
    _lernen(con, konfig)


def _lernen(con, konfig) -> None:
    """Nach jeder Entscheidung Gewichte neu berechnen (wirkt auf künftige Clips)."""
    try:
        version, ergebnis = lernen.aktualisiere(con, konfig)
        log.info("Gewichte Version %s (%s)", version, ergebnis.grund)
    except Exception:
        log.exception("Lernen fehlgeschlagen")


async def bei_fehler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    if isinstance(context.error, Conflict):
        log.error("Ein anderes Programm holt Updates für diesen Token ab (n8n-Telegram-Trigger?). Bitte dort entfernen.")
        return
    log.error("Fehler im Bot", exc_info=context.error)


def baue_app(konfig: Konfig, token: str, erlaubt: int) -> Application:
    app = Application.builder().token(token).post_init(_nach_start).post_stop(_vor_ende).build()
    app.bot_data.update(con=db.verbinde(konfig.datenbank), konfig=konfig, erlaubt=erlaubt)
    nur_ich = filters.User(user_id=erlaubt)
    for name, funktion in (
        ("start", cmd_hilfe), ("hilfe", cmd_hilfe), ("help", cmd_hilfe), ("status", cmd_status),
        ("offen", cmd_offen), ("battle", cmd_battle), ("rangliste", cmd_rangliste), ("gewichte", cmd_gewichte),
        ("paket", cmd_paket), ("uploads", cmd_uploads), ("link", cmd_link),
    ):
        app.add_handler(CommandHandler(name, funktion, filters=nur_ich))
    app.add_handler(CallbackQueryHandler(bei_klick))
    app.add_error_handler(bei_fehler)
    return app


def starte(konfig: Konfig) -> int:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    erlaubt = os.environ.get("TELEGRAM_ALLOWED_USER_ID", "").strip()
    if not token or not erlaubt.isdigit():
        print("TELEGRAM_BOT_TOKEN und TELEGRAM_ALLOWED_USER_ID in .env eintragen (siehe .env.example).")
        return 2
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # httpx würde sonst URLs mit Token loggen
    app = baue_app(konfig, token, int(erlaubt))
    # drop_pending_updates=False: Klicks, während der Bot neu startet, gehen nicht verloren
    app.run_polling(allowed_updates=["message", "callback_query"], drop_pending_updates=False)
    return 0
