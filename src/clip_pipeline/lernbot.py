"""Lern-Bot (`pipeline lernbot`, eigener Token LEARN_BOT_TOKEN, Polling) – getrennt vom Clip-Bot.

Was er tut:
  - nimmt Audiodateien als Musik an: Bildunterschrift = Quellenangabe (Pflicht), "#episch" o. ä. = Stimmung;
    misst Tempo und Energie und antwortet mit dem Ergebnis
  - schickt Entwürfe (Short/Zusammenschnitt) mit 👍/👎; danach Gründe zum An-/Abwählen:
    Musik passt nicht · zu hektisch · Stimmung getroffen · zu lang · abgeschnitten · Clips langweilig ·
    zu viele Effekte · mehr Action (4 Reihen zu je 2 Knöpfen)
  - speichert die Bewertungen (entwurf_bewertungen) – der nächste `compose` lernt daraus (regie_lernen.py)
  - analysiert vor jedem Entwurf ein paar weitere Clips (Stimmung), damit die Auswahl wächst
  - schickt Meldungen aus lern_meldungen (Alarme, abends ein Satz zum Stand, Abschlussbericht)
Befehle: /entwurf [short|zusammenschnitt] · /musik · /lernstand · /stand · /hilfe
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import sqlite3
import tempfile
from datetime import datetime
from html import escape
from pathlib import Path

from . import big, db, entwurf, musik, regie, regie_lernen, stimmung
from .konfig import Konfig, SpeicherOffline
from .zeit import iso, jetzt, utc_zu_lokal

log = logging.getLogger("lern-bot")
TEXT_MAX = 4000
FORMAT_NAMEN = {"short": "Short", "zusammenschnitt": "Zusammenschnitt"}

HILFE = """<b>Lern-Bot des Regisseurs</b>
🎵 <b>Musik schicken:</b> Audiodatei mit Bildunterschrift = Quellenangabe
   (z. B. „Song: Künstler - Titel / Music provided by NoCopyrightSounds / …“), optional <code>#episch</code>,
   <code>#spannend</code>, <code>#lustig</code>, <code>#frustriert</code> oder <code>#chill</code>.
🎬 /entwurf <code>short</code> oder /entwurf <code>zusammenschnitt</code> – neuen Entwurf bauen
👍/👎 unter jedem Entwurf, danach Gründe antippen und ✅ fertig.
/musik – Titel · /lernstand – was der Regisseur gelernt hat · /stand – kurzer Stand"""


# --- Knöpfe und Texte (ohne Telegram testbar) ------------------------------------------

def knoepfe_daumen(eid: int) -> list[list[tuple[str, str]]]:
    return [[("👍", f"d:{eid}:1"), ("👎", f"d:{eid}:-1")]]


def knoepfe_gruende(eid: int, gewaehlt: list[str]) -> list[list[tuple[str, str]]]:
    reihen, reihe = [], []
    for schluessel, text in regie_lernen.GRUENDE.items():
        reihe.append((("☑️ " if schluessel in gewaehlt else "") + text, f"g:{eid}:{schluessel}"))
        if len(reihe) == 2:
            reihen.append(reihe)
            reihe = []
    reihen.append(reihe + [("✅ fertig", f"x:{eid}:")])
    return reihen


def parse(daten: str) -> tuple[str, int, str]:
    teile = (daten or "").split(":")
    if len(teile) != 3 or teile[0] not in ("d", "g", "x") or not teile[1].isdigit():
        raise ValueError(daten)
    return teile[0], int(teile[1]), teile[2]


def _balken(werte: list[float]) -> str:
    """Mini-Diagramm des Spannungsbogens: je Wert ein Block von ▁ (kleinster) bis █ (größter).

    Min-Max-Normierung, weil die Momentstärke seit Stufe 2 negativ sein kann (Bot-Opfer, Länge; Spec §8.2) –
    vorher teilte die Funktion durch das Maximum, und [1, -10] warf einen IndexError. Sind alle Werte gleich, gibt
    es nichts zu unterscheiden: jeder bekommt den mittleren Block ▄.
    Parameter: werte – Zahlen (liste["bogen"]). Rückgabe: Text, ein Zeichen je Wert; leere Liste → "".
    Fehler: keine. Beispiel: [2, -3, 1] → "█▁▇" (−3 ist der kleinste, 2 der größte, 1 liegt bei 80 %).
    """
    zeichen = "▁▂▃▄▅▆▇█"
    if not werte:
        return ""
    tief, hoch = min(werte), max(werte)
    if hoch == tief:
        return "▄" * len(werte)
    stufen = len(zeichen) - 1
    return "".join(zeichen[round((w - tief) / (hoch - tief) * stufen)] for w in werte)


def entwurf_text(zeile: sqlite3.Row, liste: dict, bewertung: sqlite3.Row | None = None) -> str:
    m = liste.get("musik")
    momente = len({s["moment"] for s in liste["segmente"]})  # ein Moment mit Jump-Cut hat mehrere Segmente
    teile = [f"🎬 <b>Entwurf #{zeile['id']}</b> · {FORMAT_NAMEN[liste['format']]} {liste['dauer_s']:.0f} s · "
             f"Stimmung <b>{liste['stimmung']}</b> · {momente} Momente",
             f"Bogen {_balken(liste['bogen'])}"]
    if a := liste.get("auswahl"):
        teile.append(f"🆕 {a['neu']} neue · {a['schon_gezeigt']} schon gezeigt · Auswahl aus {a['kandidaten']} Momenten")
    if m:
        teile.append(f"🎵 {escape(m['titel'])} – {escape(m.get('kuenstler') or '?')} ({m.get('bpm') or 0:.0f} BPM)")
    if (fx := liste.get("effekte") or {}).get("an"):  # Regisseur 2.0: Impacts = Ereignisse im Effekt-Plan
        impacts = sum(len(s.get("effekte") or []) for s in liste["segmente"])
        teile.append(f"✨ Look {escape(str(fx.get('look', 'neutral')))} · {impacts} Impacts"
                     + (" · Hook ✓" if fx.get("hook") else "")
                     + (" · Zeitlupe ✓" if any(s.get("lupe") for s in liste["segmente"]) else ""))
    for h in liste.get("hinweise", [])[:3]:
        teile.append(f"⚠️ {escape(h)}")
    if bewertung is not None:
        gruende = [regie_lernen.GRUENDE[g] for g in json.loads(bewertung["gruende"])]
        teile.append(f"Bewertet: {'👍' if bewertung['daumen'] > 0 else '👎'}" + (" · " + ", ".join(gruende) if gruende else ""))
    else:
        teile.append("Wie findest du ihn?")
    return "\n".join(teile)[:1000]  # Bildunterschrift: max. 1024 Zeichen


def stand_satz(con: sqlite3.Connection) -> str:
    e = con.execute("SELECT COUNT(*) AS n FROM entwuerfe").fetchone()
    daumen = con.execute(
        "SELECT SUM(CASE WHEN daumen > 0 THEN 1 ELSE 0 END) AS gut, COUNT(*) AS n FROM entwurf_bewertungen").fetchone()
    momente = con.execute("SELECT COUNT(*) FROM momente").fetchone()[0]
    tracks = con.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
    return (f"📋 Stand: {momente} Momente mit Stimmung, {tracks} Musiktitel, {e['n'] or 0} Entwürfe, "
            f"davon {daumen['n'] or 0} bewertet ({daumen['gut'] or 0} 👍).")


def stuecke(text: str, groesse: int = TEXT_MAX) -> list[str]:
    """Lange Texte (Abschlussbericht) an Absätzen in Telegram-taugliche Stücke teilen."""
    teile, aktuell = [], ""
    for absatz in text.split("\n"):
        while len(absatz) > groesse:
            teile.append(absatz[:groesse])
            absatz = absatz[groesse:]
        if len(aktuell) + len(absatz) + 1 > groesse:
            teile.append(aktuell)
            aktuell = ""
        aktuell += absatz + "\n"
    if aktuell.strip():
        teile.append(aktuell)
    return [t.rstrip("\n") for t in teile if t.strip()]


# --- Arbeit im Hintergrund (eigene DB-Verbindung je Thread) ------------------------------

def speicher_da(konfig: Konfig) -> bool:
    try:
        konfig.pruefe_speicher()  # prüft zuerst per TCP, ob pve-big antwortet – hängt nicht am toten Mount
        return True
    except SpeicherOffline:
        return False


def stimmung_nachziehen(con: sqlite3.Connection, konfig: Konfig) -> int:
    """Vor jedem Entwurf die nächsten n Clips ohne Stimmung analysieren (die besten zuerst) – so wächst die
    Auswahl mit jeder Runde, ohne pve-big dafür extra wach zu halten. Ohne Claude (der zählt gegen dein Abo)."""
    n = int(konfig.wert("lernbot.stimmung_je_entwurf", 10))
    if n <= 0:
        return 0
    try:
        e = stimmung.analysiere(con, konfig, claude=False, maximal=n)
    except Exception:  # der Entwurf ist wichtiger – mit den vorhandenen Momenten weitermachen
        log.exception("Stimmung nachziehen fehlgeschlagen")
        return 0
    if e["analysiert"]:
        log.info("Stimmung für %s weitere Clips: %s", e["analysiert"], e["stimmungen"])
    return int(e["analysiert"])


def baue_entwurf(konfig: Konfig, fmt: str) -> int:
    """compose + rendern. Läuft in einem Thread; SQLite-Verbindungen dürfen nicht zwischen Threads wandern.
    Schläft pve-big, wird er geweckt – aber nur, wenn er danach sicher wieder ausgeht (big.darf_wecken)."""
    from .sperre import sperre

    konfig.pruefe_speicher(wecken=True)  # wirft SpeicherOffline mit Grund, wenn Wecken nicht erlaubt ist
    con = db.verbinde(konfig.datenbank)
    try:
        # Rendern ist ein rechenintensiver Schritt: gleiche Sperre wie die Pipeline (nur einer gleichzeitig)
        with sperre(konfig.datenbank.with_suffix(".lock"), warten_s=float(konfig.wert("sperre.warten_s", 7200))), \
                big.herzschlag(konfig, "lernbot"):
            stimmung_nachziehen(con, konfig)
            parameter, ziel = regie_lernen.aktuelle(con, konfig)
            e = regie.erstelle(con, konfig, fmt, parameter=parameter, ziel=ziel)
            entwurf.entwurf(con, konfig, e["entwurf"])
        return int(e["entwurf"])
    finally:
        con.close()


def nimm_musik(konfig: Konfig, datei: Path, bildunterschrift: str, dateiname: str) -> dict:
    con = db.verbinde(konfig.datenbank)
    try:
        titel, kuenstler, quelle = musik.aus_bildunterschrift(bildunterschrift, dateiname)
        t = musik.hinzufuegen(con, konfig, datei, titel=titel, kuenstler=kuenstler, quelle=quelle,
                              stimmung=musik.stimmung_aus_text(bildunterschrift))
        return dict(t)
    finally:
        con.close()


# --- Versand ---------------------------------------------------------------------------

def _markup(knoepfe):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=d) for t, d in reihe] for reihe in knoepfe])


def _liste(zeile: sqlite3.Row) -> dict:
    return json.loads(Path(zeile["schnittliste"]).read_text(encoding="utf-8"))


async def sende_entwuerfe(app) -> int:
    # Handler (/entwurf) und Schleife können gleichzeitig senden wollen -> nacheinander, sonst doppelt
    schloss = app.bot_data.setdefault("sende_schloss", asyncio.Lock())
    async with schloss:
        return await _sende_entwuerfe(app)


async def _sende_entwuerfe(app) -> int:
    con, chat = app.bot_data["con"], app.bot_data["erlaubt"]
    gesendet = 0
    for z in con.execute("SELECT * FROM entwuerfe WHERE status = 'gerendert' ORDER BY id").fetchall():
        pfad = Path(z["datei"] or "")
        if not pfad.is_file():
            log.warning("Entwurf #%s: Datei fehlt (%s)", z["id"], pfad)
            continue
        with pfad.open("rb") as datei:
            nachricht = await app.bot.send_video(
                chat_id=chat, video=datei, caption=entwurf_text(z, _liste(z)), parse_mode="HTML",
                reply_markup=_markup(knoepfe_daumen(z["id"])), supports_streaming=True,
                read_timeout=300, write_timeout=300, connect_timeout=30,
            )
        con.execute("UPDATE entwuerfe SET status = 'gesendet', tg_nachricht_id = ? WHERE id = ?",
                    (nachricht.message_id, z["id"]))
        gesendet += 1
    return gesendet


async def sende_meldungen(app) -> int:
    from . import lernbot_publikum  # hier, nicht oben: die Publikums-Module dürfen lernbot selbst importieren

    con, chat = app.bot_data["con"], app.bot_data["erlaubt"]
    gesendet = 0
    # Meldungen der Lernschleife (publikum:…, woche:…) warten die Ruhezeit ab, alle anderen kommen sofort (Spec §12)
    for m in lernbot_publikum.faellige_lern_meldungen(con, app.bot_data["konfig"]):
        for stueck in stuecke(m["text"]):
            await app.bot.send_message(chat, stueck)
        con.execute("UPDATE lern_meldungen SET gesendet = ? WHERE id = ?", (iso(jetzt()), m["id"]))
        gesendet += 1
    return gesendet


def abendstand(con: sqlite3.Connection, konfig: Konfig, zeit: datetime | None = None) -> bool:
    """Legt abends einmal pro Tag einen Satz zum Stand als Meldung an. True = neu angelegt."""
    lokal = utc_zu_lokal(zeit or jetzt(), konfig.wert("zeit.zeitzone", "Europe/Berlin"))
    stunde, minute = (int(x) for x in str(konfig.wert("lernbot.abend_uhrzeit", "21:00")).split(":"))
    if (lokal.hour, lokal.minute) < (stunde, minute):
        return False
    return db.lern_meldung(con, f"abend:{lokal:%Y-%m-%d}", stand_satz(con))


def blick_auf_leerlauf(app) -> None:
    """Scharfes clip-leerlauf auf pve-big gesehen? (erlaubt Wecken) – im Hintergrund und höchstens einmal
    gleichzeitig: Hängt der NFS-Mount, weil pve-big gerade ausgeht, bleibt nur dieser eine Faden stehen und
    die Schleife läuft weiter."""
    alt = app.bot_data.get("leerlauf_blick")
    if alt is not None and not alt.done():
        return
    neu = asyncio.ensure_future(asyncio.to_thread(big.merke_leerlauf, app.bot_data["konfig"]))
    neu.add_done_callback(lambda f: f.cancelled() or f.exception())  # Fehler hier sind egal
    app.bot_data["leerlauf_blick"] = neu


async def _schleife(app) -> None:
    from . import lernbot_zahlen  # hier, nicht oben: die Publikums-Module dürfen lernbot selbst importieren

    konfig = app.bot_data["konfig"]
    while True:
        blick_auf_leerlauf(app)
        # lernbot_zahlen.aufraeumen: wartende Screenshots nach 10 min verwerfen (Lernschleife, Spec §7.1)
        for aufgabe in (sende_meldungen, sende_entwuerfe, lernbot_zahlen.aufraeumen):
            try:
                await aufgabe(app)
            except Exception:
                log.exception("Fehler in %s", aufgabe.__name__)
        try:
            if konfig.wert("lernbot.abendstand", True):
                abendstand(app.bot_data["con"], konfig)
        except Exception:
            log.exception("Abendstand")
        await asyncio.sleep(float(konfig.wert("lernbot.intervall_s", 30)))


# --- Handler ---------------------------------------------------------------------------

async def cmd_hilfe(update, context) -> None:
    from . import lernbot_publikum  # hier, nicht oben: die Publikums-Module dürfen lernbot selbst importieren

    # HILFE bleibt unverändert; der Teil zur Lernschleife „Publikum“ kommt als Zusatz dahinter
    await update.effective_message.reply_text(HILFE + lernbot_publikum.HILFE_ZUSATZ, parse_mode="HTML")


async def cmd_stand(update, context) -> None:
    await update.effective_message.reply_text(stand_satz(context.bot_data["con"]))


async def cmd_lernstand(update, context) -> None:
    con, konfig = context.bot_data["con"], context.bot_data["konfig"]
    await update.effective_message.reply_text(regie_lernen.lernstand_text(con, konfig))


async def cmd_musik(update, context) -> None:
    zeilen = context.bot_data["con"].execute(
        "SELECT id, titel, kuenstler, bpm, energie, stimmungen FROM tracks ORDER BY id DESC LIMIT 30").fetchall()
    if not zeilen:
        await update.effective_message.reply_text("Noch keine Musik. Schick mir eine Audiodatei mit Quellenangabe.")
        return
    text = "\n".join(f"#{z['id']} {z['kuenstler'] or '?'} – {z['titel']} · {z['bpm'] or 0:.0f} BPM · "
                     f"E {z['energie'] or 0:.2f} · {', '.join(json.loads(z['stimmungen'] or '[]'))}" for z in zeilen)
    await update.effective_message.reply_text(text[:TEXT_MAX])


async def neuer_entwurf(app, fmt: str) -> int | None:
    """Baut einen Entwurf und schickt ihn – für /entwurf und automatisch nach jeder fertigen Bewertung."""
    chat = app.bot_data["erlaubt"]
    if app.bot_data.get("arbeitet"):
        await app.bot.send_message(chat, "⏳ Ich baue gerade schon einen Entwurf.")
        return None
    app.bot_data["arbeitet"] = True
    try:
        konfig = app.bot_data["konfig"]
        wach = await asyncio.to_thread(speicher_da, konfig)
        await app.bot.send_message(chat, f"🎬 Baue einen {FORMAT_NAMEN[fmt]} …"
                                   + ("" if wach else " 💤 pve-big schläft – ich wecke ihn (bis zu 3 min)."))
        eid = await asyncio.to_thread(baue_entwurf, konfig, fmt)
        await sende_entwuerfe(app)
        log.info("Entwurf #%s gebaut", eid)
        return eid
    except Exception as e:  # dir kurz sagen, was los ist – Details ins Log
        log.exception("Entwurf fehlgeschlagen")
        await app.bot.send_message(chat, f"⚠️ Entwurf fehlgeschlagen: {escape(str(e)[:300])}")
        return None
    finally:
        app.bot_data["arbeitet"] = False


async def cmd_entwurf(update, context) -> None:
    fmt = (context.args or ["short"])[0].lower()
    if fmt not in regie.FORMATE:
        await update.effective_message.reply_text("Aufruf: /entwurf short oder /entwurf zusammenschnitt")
        return
    await neuer_entwurf(context.application, fmt)


async def bei_audio(update, context) -> None:
    nachricht = update.effective_message
    datei_info = nachricht.audio or nachricht.document
    bildunterschrift = (nachricht.caption or "").strip()
    if not bildunterschrift:
        await nachricht.reply_text("Bitte nochmal mit Bildunterschrift = Quellenangabe (Lizenz). Ohne nehme ich keine Musik an.")
        return
    name = getattr(datei_info, "file_name", None) or "titel.mp3"
    endung = Path(name).suffix.lower() or ".mp3"
    if endung not in musik.ENDUNGEN:
        await nachricht.reply_text(f"Format {endung} kenne ich nicht ({', '.join(sorted(musik.ENDUNGEN))}).")
        return
    with tempfile.TemporaryDirectory() as tmp:
        ziel = Path(tmp) / f"eingang{endung}"
        telegram_datei = await datei_info.get_file()
        await telegram_datei.download_to_drive(ziel)
        try:
            t = await asyncio.to_thread(nimm_musik, context.bot_data["konfig"], ziel, bildunterschrift, name)
        except Exception as e:
            log.exception("Musik")
            await nachricht.reply_text(f"⚠️ Konnte die Musik nicht übernehmen: {escape(str(e)[:300])}")
            return
    await nachricht.reply_text(
        f"🎵 #{t['id']} {t['kuenstler'] or '?'} – {t['titel']}\n{t['bpm']:.0f} BPM · Energie {t['energie']:.2f} · "
        f"passt zu: {', '.join(json.loads(t['stimmungen'] or '[]'))}")


async def bei_klick(update, context) -> None:
    query = update.callback_query
    con = context.bot_data["con"]
    if query.from_user is None or query.from_user.id != context.bot_data["erlaubt"]:
        await query.answer("Nicht erlaubt.")
        return
    try:
        aktion, eid, extra = parse(query.data)
    except ValueError:
        await query.answer("Unbekannter Knopf.")
        return
    zeile = con.execute("SELECT * FROM entwuerfe WHERE id = ?", (eid,)).fetchone()
    if zeile is None:
        await query.answer("Entwurf unbekannt.")
        return
    if aktion == "d":
        bewertung = regie_lernen.bewerte(con, eid, daumen=int(extra))
        await query.answer("Danke! Gründe antippen (optional), dann ✅ fertig.")
        knoepfe = knoepfe_gruende(eid, json.loads(bewertung["gruende"]))
    elif aktion == "g":
        bewertung = regie_lernen.bewerte(con, eid, grund=extra)
        await query.answer(regie_lernen.GRUENDE[extra])
        knoepfe = knoepfe_gruende(eid, json.loads(bewertung["gruende"]))
    else:
        bewertung = con.execute("SELECT * FROM entwurf_bewertungen WHERE entwurf_id = ?", (eid,)).fetchone()
        weiter = bool(context.bot_data["konfig"].wert("lernbot.naechster_nach_bewertung", True))
        await query.answer("Gespeichert – der nächste Entwurf kommt gleich." if weiter
                           else "Gespeichert – fließt in den nächsten Entwurf ein.")
        from . import lernbot_paket  # hier, nicht oben: lernbot_paket darf lernbot selbst importieren

        knoepfe = lernbot_paket.knoepfe_nach_fertig(eid, bewertung, zeile["format"])  # 👍-Short: „📦 Upload-Paket“
        if weiter:  # Lernschleife: sofort der nächste Entwurf, schon mit dieser Bewertung eingerechnet
            context.application.create_task(neuer_entwurf(context.application, zeile["format"]))
    with contextlib.suppress(Exception):  # "message is not modified" bei Doppelklick
        await query.edit_message_caption(caption=entwurf_text(zeile, _liste(zeile), bewertung), parse_mode="HTML",
                                         reply_markup=_markup(knoepfe) if knoepfe else None)


async def bei_fehler(update, context) -> None:
    from telegram.error import Conflict

    if isinstance(context.error, Conflict):
        log.error("Ein anderes Programm holt Updates für LEARN_BOT_TOKEN ab – nur ein Empfänger erlaubt.")
        return
    log.error("Fehler im Lern-Bot", exc_info=context.error)


def baue_app(konfig: Konfig, token: str, erlaubt: int):
    from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

    async def nach_start(app) -> None:
        app.bot_data["schleife"] = asyncio.create_task(_schleife(app))
        log.info("Lern-Bot läuft.")

    async def vor_ende(app) -> None:
        if aufgabe := app.bot_data.get("schleife"):
            aufgabe.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await aufgabe

    app = Application.builder().token(token).post_init(nach_start).post_stop(vor_ende).build()
    app.bot_data.update(con=db.verbinde(konfig.datenbank), konfig=konfig, erlaubt=erlaubt)
    nur_ich = filters.User(user_id=erlaubt)
    for name, funktion in (("start", cmd_hilfe), ("hilfe", cmd_hilfe), ("help", cmd_hilfe), ("stand", cmd_stand),
                           ("lernstand", cmd_lernstand), ("musik", cmd_musik), ("entwurf", cmd_entwurf)):
        app.add_handler(CommandHandler(name, funktion, filters=nur_ich))
    app.add_handler(MessageHandler(nur_ich & (filters.AUDIO | filters.Document.AUDIO), bei_audio))
    # Lernschleife „Publikum“ (Spec §7.1, §10.4, §14 Stufe 1): Screenshots/Hand-Eingabe, Upload-Paket und /link,
    # /publikum – eigene Module, hier nur eingehängt. VOR dem allgemeinen Klick-Handler: der liest jeden Knopf als
    # Entwurfs-Knopf; die Module melden ihre Knöpfe (pl/pm, pk/pt) mit eigenem Muster an.
    from . import lernbot_paket, lernbot_publikum, lernbot_zahlen

    for modul in (lernbot_zahlen, lernbot_paket, lernbot_publikum):
        modul.registriere(app, nur_ich)
    app.add_handler(CallbackQueryHandler(bei_klick))
    app.add_error_handler(bei_fehler)
    return app


def starte(konfig: Konfig) -> int:
    token = os.environ.get("LEARN_BOT_TOKEN", "").strip()
    erlaubt = (os.environ.get("LEARN_BOT_ALLOWED_USER_ID") or os.environ.get("TELEGRAM_ALLOWED_USER_ID") or "").strip()
    if not token or not erlaubt.isdigit():
        print("LEARN_BOT_TOKEN und TELEGRAM_ALLOWED_USER_ID (oder LEARN_BOT_ALLOWED_USER_ID) in .env eintragen.")
        return 2
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # würde sonst URLs mit Token loggen
    app = baue_app(konfig, token, int(erlaubt))
    app.run_polling(allowed_updates=["message", "callback_query"], drop_pending_updates=False)
    return 0
