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
from telegram.error import BadRequest, Conflict, RetryAfter, TelegramError
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, filters

from .. import (auto_freigabe, caption, db, einstellungen, erwartung, highlight, lernbot_einstellungen, lernen,
               merkmale, publikum, shorts, uebertragung)
from ..konfig import Konfig, SpeicherOffline
from ..medien import MedienFehler
from ..sperre import Gesperrt, pfad as sperre_pfad, sperre
from ..zeit import aus_iso, iso, jetzt
from . import aktionen, texte

log = logging.getLogger("clip-bot")


def _markup(knoepfe: aktionen.Knoepfe | None) -> InlineKeyboardMarkup | None:
    if knoepfe is None:
        return None
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=d) for t, d in reihe] for reihe in knoepfe])


def _daten(context: ContextTypes.DEFAULT_TYPE):
    return context.bot_data["con"], context.bot_data["konfig"], context.bot_data["erlaubt"]


def _clip_text(con, konfig: Konfig, clip_id: int, vorschlag: auto_freigabe.Vorschlag | None = None) -> str:
    """Bildunterschrift eines Clips. Die Erwartung wird hier nur GELESEN (/offen, nach einem Klick) – festgeschrieben
    wird sie einmal beim ersten Senden (sende_outbox, Spec §10.5).

    vorschlag: beim ersten Senden die Entscheidung der Auto-Freigabe, BEVOR sie in der Datenbank steht – so zeigt die
    Bildunterschrift die 🤖-Zeile (sofort) bzw. 🎲/👀 schon. Die Zusatzzeile (⏰/🎲/👀) liest die ⚙️-Werte mit."""
    clip = dict(db.clip(con, clip_id))
    if vorschlag is not None:
        clip.update(auto_art=vorschlag.art, auto_vorschlag=vorschlag.ziel, auto_grund=vorschlag.grund,
                    **({"status": vorschlag.ziel, "freigabe_quelle": "auto"} if vorschlag.art == "sofort" else {}))
    try:
        zeile = auto_freigabe.hinweis_zeile(clip, einstellungen.anwenden(con, konfig))
    except Exception:  # eine Zusatzzeile darf keine Bildunterschrift verhindern
        log.exception("Hinweis der Auto-Freigabe für Clip #%s nicht gebaut", clip_id)
        zeile = None
    return texte.clip_text(clip, db.match(con, clip["match_id"]), konfig.wert("zeit.zeitzone", "Europe/Berlin"),
                           erwartung=erwartung.gespeichert(con, "clip", clip_id), auto_zeile=zeile)


def _erwartung_festschreiben(con, konfig: Konfig, clip_id: int) -> None:
    """Erwartung VOR dem Senden festschreiben (sie muss feststehen, bevor du urteilst). Ein Fehler hier darf das
    Senden nicht aufhalten: der Clip kommt dann mit „Erwartung: noch keine“, der Fehler steht im Log."""
    try:
        erwartung.festschreiben(con, konfig, "clip", clip_id)
    except Exception:
        log.exception("Erwartung für Clip #%s nicht festgeschrieben", clip_id)


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
    Meldungen aus Puffer/Lager (Morgenprüfung, Abgleich) warten die Ruhezeit ab ([telegram].leise_von/leise_bis).
    Stufe 3 (08.10.): Routine (Start und glattes Ende einer Übertragung, meldungen.routine) wird im stillen einfachen
    Modus nur als gesendet vermerkt (_nur_probleme) – Fehler, Abbrüche und Warnungen kommen immer."""
    con, konfig, chat = app.bot_data["con"], app.bot_data["konfig"], app.bot_data["erlaubt"]
    try:
        uebertragung.hole_meldungen(con, konfig)
    except Exception:
        # Ein kaputter/unzugänglicher PC-Bericht darf die übrige Outbox nicht blockieren.
        log.exception("Übertragungsberichte konnten nicht eingelesen werden")
    gesendet = 0
    ruhig = None   # erst ausrechnen, wenn eine Routine-Meldung ansteht
    for m in aktionen.faellige_meldungen(con, konfig):
        if m["routine"]:
            if ruhig is None:
                ruhig = _nur_probleme(con, konfig)
            if ruhig:
                con.execute("UPDATE meldungen SET gesendet = ? WHERE id = ?", (iso(jetzt()), m["id"]))
                continue
        optionen = {"disable_notification": aktionen.ruhezeit(konfig)} if m["schluessel"].startswith("uebertragung:") else {}
        if m["schluessel"].startswith(AUTO_MELDUNGEN):  # Zusammenfassungen der Auto-Freigabe immer ohne Ton
            optionen = {"disable_notification": True}
        await app.bot.send_message(chat, m["text"], **optionen)
        con.execute("UPDATE meldungen SET gesendet = ? WHERE id = ?", (iso(jetzt()), m["id"]))
        gesendet += 1
    return gesendet


# Meldungen der Auto-Freigabe (Zusammenfassung je Match, Frist) – kommen immer ohne Ton
AUTO_MELDUNGEN = ("auto:", "frist:")


def _auto_stand(con, k: Konfig) -> dict | None:
    """Aktive Stufen der Auto-Freigabe, einmal je Runde; None = Modus aus oder Fehler (dann alles normal)."""
    try:
        if auto_freigabe.werte(k)["modus"] == "aus":
            return None
        return auto_freigabe.stufe(con, k)
    except Exception:
        log.exception("Auto-Freigabe: Stufen nicht berechnet – diese Runde geht alles zu dir")
        return None


def _vorschlag(con, k: Konfig, clip, stand: dict | None) -> auto_freigabe.Vorschlag | None:
    """Vorschlag der Auto-Freigabe für einen Clip. Im Zweifel fragt der Bot: jeder Fehler → None (normaler Weg)."""
    if stand is None:
        return None
    try:
        return auto_freigabe.vorschlag(con, k, clip, erwartung.gespeichert(con, "clip", clip["id"]), stand)
    except Exception:
        log.exception("Auto-Freigabe für Clip #%s fehlgeschlagen – er geht normal zu dir", clip["id"])
        return None


async def sende_outbox(app: Application) -> int:
    """Neue Clips (vorbewertet) und Highlight-Videos verschicken. Auto-Freigabe (30.09.): Nach dem Festschreiben der
    Erwartung entscheidet auto_freigabe.vorschlag; ein Sofort-Clip kommt trotzdem, aber ohne Ton, mit 🤖-Zeile und
    Knöpfen zum Umdrehen. ⚙️-Werte (einstellungen.anwenden) gelten ab der nächsten Runde; für Speicher und Pfade
    bleibt die geladene Konfig."""
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
    k = einstellungen.anwenden(con, konfig) if zeilen else konfig  # nur zum Lesen von [auto_freigabe]
    stand = _auto_stand(con, k) if zeilen else None
    zeigen = not _still(k)
    for z in zeilen:
        if not zeigen:   # Stufe 1 (07.10.): still – entscheiden wie immer, nur ohne Nachricht an dich
            _erwartung_festschreiben(con, konfig, z["id"])
            aktionen.als_gesendet(con, z["id"], None, None, _vorschlag(con, k, z, stand))
            gesendet += 1
            continue
        pfad = konfig.absolut(z["vorschau_pfad"]) if z["vorschau_pfad"] else None
        if pfad is None or not pfad.is_file():
            log.warning("Vorschau für Clip #%s fehlt: %s", z["id"], pfad)
            continue
        _erwartung_festschreiben(con, konfig, z["id"])  # vor send_video: die Bildunterschrift zeigt sie schon
        vorschlag = _vorschlag(con, k, z, stand)
        try:
            text = _clip_text(con, konfig, z["id"], vorschlag)
        except Exception:  # im Zweifel fragt der Bot: ohne Automatik weiter
            log.exception("Bildunterschrift mit Auto-Freigabe für Clip #%s fehlgeschlagen", z["id"])
            vorschlag = None
            text = _clip_text(con, konfig, z["id"])
        sofort = vorschlag is not None and vorschlag.art == "sofort"
        knoepfe = aktionen.knoepfe_auto(z["id"], vorschlag.ziel) if sofort else aktionen.knoepfe_neu(z["id"])
        with pfad.open("rb") as datei:
            nachricht = await app.bot.send_video(
                chat_id=chat, video=datei, caption=text, parse_mode=ParseMode.HTML,
                reply_markup=_markup(knoepfe), supports_streaming=True,
                disable_notification=leise or sofort, read_timeout=300, write_timeout=300, connect_timeout=30,
            )
        aktionen.als_gesendet(con, z["id"], nachricht.message_id, nachricht.video.file_id if nachricht.video else None,
                              vorschlag)
        gesendet += 1
    nur_lernbot = bool(highlights) and _nur_probleme(con, konfig)
    for h in highlights:
        if nur_lernbot and h["entwurf_id"] is not None:
            # Stufe 3 (08.10.): Das 2-Wochen-Video schickt nur noch der Lern-Bot (sein Entwurf), dein ✅/❌ dort
            # entscheidet (highlight.entscheide_entwurf). Ohne Entwurf (Altbestand) sähest du es nie – dann wie bisher.
            aktionen.highlight_gesendet(con, h["id"], None)
            gesendet += 1
            continue
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


def _still(k) -> bool:
    """Clip-Bot still (Stufe 1, 07.10.) – nur, wenn die Auto-Freigabe wirklich selbst entscheidet. Bei „probe“ oder
    „aus“ entscheidest du; ohne Nachricht bekäme ein Clip nie eine Entscheidung (keine Battles, kein Highlight)."""
    return not k.wert("bot.clips_zeigen", False) and auto_freigabe.werte(k)["modus"] == "an"


def _nur_probleme(con, konfig: Konfig) -> bool:
    """Stufe 3 (08.10., „der Clip-Bot meldet sich nur bei Problemen“): Clip-Bot still (_still) UND Lern-Bot im
    einfachen Modus. Dann kommt das 2-Wochen-Video nur im Lern-Bot, es gibt keine Erinnerung ans Hochladen, und Start
    und glattes Ende einer Übertragung werden nur vermerkt. Fehler, Abbrüche und Warnungen kommen weiter. Unter
    /experte bleibt alles wie vorher – im Zweifel (Lesefehler) auch: lieber eine Nachricht zu viel als eine zu wenig."""
    try:
        k = einstellungen.anwenden(con, konfig)
        return _still(k) and not einstellungen.experte(con, k)
    except Exception:  # noqa: BLE001
        log.exception("Ruhe im Clip-Bot nicht bestimmt – diese Runde wie bisher")
        return False


async def automat_lauf(app: Application) -> int:
    """Auto-Freigabe außerhalb des Sendens (braucht keinen Speicher): Frist für offene Clips – die alte Nachricht
    bekommt die 🤖-Zeile und Knöpfe zum Umdrehen, dazu eine stille Meldung „⏰ … selbst entschieden“ – und die
    Zusammenfassung je fertigem Match (Meldung auto:<match>, einmal). Rückgabe: Anzahl Frist-Entscheidungen."""
    con, konfig, chat = app.bot_data["con"], app.bot_data["konfig"], app.bot_data["erlaubt"]
    k = einstellungen.anwenden(con, konfig)
    w = auto_freigabe.werte(k)
    if w["modus"] == "aus":
        return 0
    ergebnis = auto_freigabe.frist(con, k)
    if _still(k):   # still (07.10.): Zusammenfassungen gelten als erledigt, nichts an dich
        for match_id in auto_freigabe.faellige_zusammenfassungen(con):
            if db.meldung(con, f"auto:{match_id}", auto_freigabe.zusammenfassung_text(con, match_id, k)):
                con.execute("UPDATE meldungen SET gesendet = ? WHERE schluessel = ?", (iso(jetzt()), f"auto:{match_id}"))
        return len(ergebnis)
    if ergebnis:  # zuerst die Meldung: die Entscheidungen stehen schon fest, auch wenn Telegram gleich streikt
        db.meldung(con, f"frist:{iso(jetzt())}", auto_freigabe.frist_text(ergebnis, w["frist_h"]))
    for e in ergebnis:
        if not e["tg_nachricht_id"]:
            continue
        clip = db.clip(con, e["id"])  # aktueller Stand – Florian kann während der Schleife schon umgedreht haben
        try:
            await app.bot.edit_message_caption(
                chat_id=chat, message_id=e["tg_nachricht_id"], caption=_clip_text(con, konfig, e["id"]),
                parse_mode=ParseMode.HTML, reply_markup=_markup(_knoepfe_fuer(clip)))
        except RetryAfter:  # Flood-Limit: die übrigen alten Nachrichten bleiben wie sie sind, die Entscheidung gilt
            log.warning("Frist: Telegram bremst – die restlichen alten Nachrichten bleiben diesmal unbearbeitet")
            break
        except TelegramError as fehler:  # Nachricht weg, zu alt, Netz: die Entscheidung gilt trotzdem
            log.warning("Frist: Nachricht zu Clip #%s nicht bearbeitet: %s", e["id"], fehler)
    for match_id in auto_freigabe.faellige_zusammenfassungen(con):
        db.meldung(con, f"auto:{match_id}", auto_freigabe.zusammenfassung_text(con, match_id, k))
    return len(ergebnis)


async def erinnere(app: Application) -> bool:
    """Erinnert an Highlight-Videos, die seit über erinnerung_h freigegeben, aber noch nicht hochgeladen sind –
    höchstens einmal je erinnerung_h. Einzelne Momente werden nicht hochgeladen (Entscheidung 26.09.).
    Stufe 3 (08.10.): im stillen einfachen Modus aus – dort heißt ✅ im Lern-Bot schon „hochgeladen“ (/uploads zeigt
    ältere offene Videos weiter)."""
    con, konfig, chat = app.bot_data["con"], app.bot_data["konfig"], app.bot_data["erlaubt"]
    stunden = float(konfig.wert("veroeffentlichung.erinnerung_h", 24))
    grenze = jetzt() - timedelta(hours=stunden)
    letzte = con.execute("SELECT zeit FROM ereignisse WHERE art = 'erinnerung' ORDER BY id DESC LIMIT 1").fetchone()
    if letzte and aus_iso(letzte["zeit"]) > grenze:
        return False
    videos = [h for h in aktionen.offene_highlight_videos(con) if h["entschieden"] and aus_iso(h["entschieden"]) < grenze]
    if not videos or _nur_probleme(con, konfig):
        return False
    await app.bot.send_message(chat, "⏰ " + texte.offene_uploads_text(videos), parse_mode=ParseMode.HTML)
    db.protokoll(con, "erinnerung", f"{len(videos)} Highlight-Video(s) nicht hochgeladen")
    return True


async def _outbox_schleife(app: Application) -> None:
    intervall = float(app.bot_data["konfig"].wert("telegram.outbox_intervall_s", 30))
    while True:
        for aufgabe in (sende_meldungen, sende_outbox, automat_lauf, erinnere):
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
                             lager=_lager_zeile(con, konfig), auto=db.anzahl_auto(con))
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
        # Wer nachsieht, bekommt mehr Zeit: die Frist der Auto-Freigabe beginnt neu
        con.execute("UPDATE clips SET vorgelegt = ? WHERE id = ?", (iso(jetzt()), z["id"]))
        nachricht = await context.bot.send_video(
            chat_id=chat, video=z["tg_file_id"], caption=_clip_text(con, konfig, z["id"]),
            parse_mode=ParseMode.HTML, reply_markup=_markup(aktionen.knoepfe_neu(z["id"])),
        )
        con.execute("UPDATE clips SET tg_nachricht_id = ? WHERE id = ?", (nachricht.message_id, z["id"]))


def _knoepfe_fuer(clip) -> aktionen.Knoepfe | None:
    """Passende Knöpfe für einen Clip: offen ✅/🗑️, automatisch entschieden 👍/umdrehen, von dir entschieden ↩️."""
    if clip["status"] in ("vorbewertet", "gesendet"):
        return aktionen.knoepfe_neu(clip["id"])
    if clip["status"] in ("freigegeben", "verworfen"):
        if clip["freigabe_quelle"] == "auto":
            return aktionen.knoepfe_auto(clip["id"], clip["status"])
        return aktionen.knoepfe_entschieden(clip["id"], clip["status"])
    return None


async def cmd_clip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/clip <nr>: einen Clip per tg_file_id erneut schicken, mit den passenden Knöpfen – so lässt sich auch ein Clip
    umdrehen, dessen Nachricht weit oben steht."""
    con, konfig, chat = _daten(context)
    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text("Aufruf: /clip <Clip-Nummer>")
        return
    clip = db.clip(con, int(context.args[0]))
    if clip is None or not clip["tg_file_id"]:
        await update.effective_message.reply_text(
            f"Clip #{context.args[0]} gibt es nicht oder er wurde noch nicht gesendet.")
        return
    nachricht = await context.bot.send_video(
        chat_id=chat, video=clip["tg_file_id"], caption=_clip_text(con, konfig, clip["id"]),
        parse_mode=ParseMode.HTML, reply_markup=_markup(_knoepfe_fuer(clip)), disable_notification=True,
    )
    con.execute("UPDATE clips SET tg_nachricht_id = ? WHERE id = ?", (nachricht.message_id, clip["id"]))


async def cmd_auto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/auto: Stand der Auto-Freigabe (Modus, Stufen, letzte 7 Tage, Korrekturen) mit Knopf ⚙️ Einstellungen – den
    gibt es seit 08.10. nur im Experten-Modus (im einfachen entscheidet der Bot, ein Menü gibt es dort nicht)."""
    con, konfig, _ = _daten(context)
    u = auto_freigabe.ueberblick(con, einstellungen.anwenden(con, konfig))
    knopf = [[("⚙️ Einstellungen", "s:m")]] if einstellungen.experte(con, konfig) else None
    await update.effective_message.reply_text(texte.auto_text(u), parse_mode=ParseMode.HTML,
                                              reply_markup=_markup(knopf))


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


def _rendere_short(konfig: Konfig, quelle, ziel, stimmen) -> None:
    """Short für /paket unter der Pipeline-Sperre rendern – wie jeder rechenintensive Schritt (eine Sperre für den
    ganzen Mini, M1). Ohne Warten: Rechnet gerade etwas anderes → Gesperrt. Der Clip-Bot arbeitet Nachrichten
    nacheinander ab; ein langes Warten hielte jeden Klick auf."""
    with sperre(sperre_pfad(konfig), warten_s=0):
        shorts.rendere(quelle, ziel, konfig, stimmen=stimmen)


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
            # Mikro/Chat nur bei Lachen, Jubel oder Gags (merkmale.stimmen_fuer_clip)
            await asyncio.to_thread(_rendere_short, konfig, konfig.absolut(clip["clip_pfad"]), ziel,
                                    merkmale.stimmen_fuer_clip(con, clip_id))
        except Gesperrt:
            await context.bot.send_message(chat, f"⏳ Gerade rechnet ein anderer Schritt – gleich nochmal: /paket {clip_id}")
            return
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
    text = texte.offene_uploads_text(aktionen.offene_highlight_videos(con))
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
            knoepfe = aktionen.knoepfe_highlight_freigegeben(h["id"]) if h["status"] == "freigegeben" else None
            with contextlib.suppress(BadRequest):
                await query.edit_message_caption(caption=texte.highlight_text(h), parse_mode=ParseMode.HTML,
                                                 reply_markup=_markup(knoepfe))
        return

    if aktion == "hu":
        h = highlight.hochgeladen(con, nummer)
        await query.answer("Highlight nicht gefunden" if h is None else "✅ Hochgeladen")
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
    # clip_bot: die gemeinsame ⚙️-Übersicht (lernbot_einstellungen) verweist hier auf /experte im Lern-Bot
    app.bot_data.update(con=db.verbinde(konfig.datenbank), konfig=konfig, erlaubt=erlaubt, clip_bot=True)
    nur_ich = filters.User(user_id=erlaubt)
    for name, funktion in (
        ("start", cmd_hilfe), ("hilfe", cmd_hilfe), ("help", cmd_hilfe), ("status", cmd_status),
        ("offen", cmd_offen), ("battle", cmd_battle), ("rangliste", cmd_rangliste), ("gewichte", cmd_gewichte),
        ("paket", cmd_paket), ("uploads", cmd_uploads), ("link", cmd_link), ("auto", cmd_auto), ("clip", cmd_clip),
    ):
        app.add_handler(CommandHandler(name, funktion, filters=nur_ich))
    # ⚙️ auch im Clip-Bot (gemeinsame Liste mit dem Lern-Bot) – VOR bei_klick, sonst fängt der musterlose Handler
    # die s:-Knöpfe ab
    lernbot_einstellungen.registriere(app, nur_ich)
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
