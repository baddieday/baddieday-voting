"""Lern-Bot: vom bewerteten Entwurf zum Post – Upload-Paket, Häkchen, /link (Spec §10.4).

So läuft es für dich:
  1. Nach 👍 und „✅ fertig“ steht unter einem Short-Entwurf „📦 Upload-Paket“ (`pk:<eid>:`).
  2. Der Bot rendert die Upload-Fassung (1080×1920, entwurf.upload_fassung – auf dem Mini, unter der
     Pipeline-Sperre, nie auf pve-big) und schickt sie als Datei (Telegram komprimiert Dateien nicht neu),
     dazu die Caption zum Kopieren (caption.entwurf_caption, mit Musik-Quellenangabe) und eine Checkliste mit
     einem Häkchen je Plattform aus [publikum].plattformen (`pt:<eid>:t` für TikTok, `pt:<eid>:y` für YouTube).
  3. Du postest, dann `/link 41 https://www.tiktok.com/@…/video/…` (auch `/link e41`). Das legt den Post an
     (publikum.post_anlegen) und trägt den Link ein (publikum.link_nachtragen) und nennt dir die **Post-Nummer** –
     die brauchst du für die Screenshots („#17“). Ein Häkchen ohne Link legt den Post ebenfalls an (Link später
     per /link; ein zweiter /link ersetzt einen falschen).

Regeln (Annahmen A5, A26, A27 – docs/ENTSCHEIDUNGEN.md, „Annahmen im Sprint Lernschleife“):
  - Nur 👍-Entwürfe im Format "short" bekommen Paket, Häkchen und Post („kein Short ohne deine Freigabe“; ein
    Zusammenschnitt 16:9 liefert kein Publikumssignal, Spec §9.1). Andere → kurzer Hinweis, kein Post.
  - Die Checkliste nennt nur die Post-Plattformen ([publikum].plattformen); clip-battle.de bekommt für Entwürfe
    keinen Punkt (Florian 25.09., Rückfrage R5 in docs/ENTSCHEIDUNGEN.md: keine clip-battle.de-Checkliste).
  - Der Stand je Plattform liegt in `posts` (veroeffentlichungen bleibt Clip-Sache), nachgesehen mit
    publikum.post_zu – kein eigenes SQL gegen posts.
  - Links erkennt bot.aktionen.plattform_aus_url (nur https, bekannte Domains) – dieselbe Regel wie im Clip-Bot.
  - Kein Weckversuch: Fehlen die Moment-Dateien (der Puffer hält Rohvideos 14 Tage) oder läuft der Mini nicht im
    getrennten Betrieb, sagt der Bot das klar – anders als das alte Clip-Bot-/paket weckt dieser Weg pve-big nie
    (Spec §12).

Export-Vertrag mit Regisseur 2.0 (docs/ENTSCHEIDUNGEN.md): Es gibt genau EINEN 📦-Knopf im Lern-Bot, `pk:<eid>:` aus diesem
Modul. Die Upload-Fassung liegt in <wurzel>/<[publikum].upload_ordner>/<name>/ (entwurf.upload_ziel), der Pfad in
entwuerfe.upload_pfad. Regisseur 2.0 Stufe 3 erweitert baue_paket und entwurf.upload_fassung (Einzelclips,
Sicherung ins Lager) und baut keinen zweiten Weg daneben.

Rendern läuft per asyncio.to_thread mit eigener DB-Verbindung (SQLite-Verbindungen wandern nicht zwischen
Threads, E9), unter sperre(konfig.datenbank.with_suffix(".lock"), warten_s=[sperre].warten_s) und höchstens
einmal gleichzeitig (bot_data["paket_arbeitet"]). Nie ins Log: Datei-URLs von Telegram (Token), Chat-IDs.

Zeit: `jetzt` ist auf Modulebene importiert – Tests ersetzen `lernbot_paket.jetzt` (mock.patch.object).

Fehler: Was fachlich nicht geht (kein 👍, kein Short, fremder Link …), beantworten die Funktionen mit einem Satz
für dich statt mit einer Ausnahme. Technische Fehler (Schnittliste fehlt, ffmpeg scheitert, Sperre belegt) fangen
die Telegram-Handler ab: du bekommst einen kurzen Satz, die Details stehen im Log.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
from datetime import datetime
from html import escape
from pathlib import Path

from . import caption, db, entwurf, lernbot, publikum
from .bot import aktionen
from .konfig import Konfig, KonfigFehler
from .medien import MedienFehler
from .sperre import Gesperrt, sperre
from .zeit import jetzt  # im Modul importiert, damit Tests `lernbot_paket.jetzt` ersetzen können

log = logging.getLogger("lern-bot")

# Callback-Präfixe dieses Moduls: pk:<eid>:  Upload-Paket bauen · pt:<eid>:t|y  TikTok/YouTube erledigt
PRAEFIXE = ("pk", "pt")
KLICK_MUSTER = r"^(pk|pt):"
# Kürzel wie im Clip-Bot (bot.aktionen.PLATTFORM_KUERZEL), aber nur Plattformen, für die es Posts gibt – also
# ohne clip-battle.de ("c"). Abgeleitet statt abgeschrieben: {"y": "youtube", "t": "tiktok"}.
PLATTFORM_KUERZEL = {k: p for k, p in aktionen.PLATTFORM_KUERZEL.items() if p in publikum.PLATTFORMEN}
KUERZEL_VON = {p: k for k, p in PLATTFORM_KUERZEL.items()}  # die Gegenrichtung: "tiktok" → "t"
NUR_FORMAT = "short"  # Paket, Häkchen und Post nur für Shorts (Annahme A26)

# /link <nr> <url>: die Nummer des Entwurfs, wahlweise mit „e“ davor (Annahme A3). Nur Ziffern 0–9 – \d ließe auch
# andere Schriften zu („٤١“).
LINK_NUMMER = re.compile(r"[eE]?([0-9]+)")
LINK_AUFRUF = "Aufruf: /link 41 https://www.tiktok.com/@…/video/… (41 = Nummer des Entwurfs, auch e41)"
FEHLER_MAX = 300   # so viel Fehlertext geht an dich – genug für den Grund; ffmpeg-Ausgaben wären sonst seitenlang

Knoepfe = list[list[tuple[str, str]]]


def _name(plattform: str) -> str:
    """Plattform so, wie du sie kennst: "tiktok" → "TikTok" (Namen aus dem Clip-Bot)."""
    return aktionen.PLATTFORM_NAMEN.get(plattform, plattform)


def knoepfe_nach_fertig(eid: int, bewertung: sqlite3.Row | None, fmt: str) -> Knoepfe | None:
    """Knöpfe unter einem fertig bewerteten Entwurf: bei 👍 auf einen Short „📦 Upload-Paket“, sonst keine (None).

    Beispiel: knoepfe_nach_fertig(41, {"daumen": 1, …}, "short") == [[("📦 Upload-Paket", "pk:41:")]];
    knoepfe_nach_fertig(42, {"daumen": 1, …}, "zusammenschnitt") is None."""
    if fmt != NUR_FORMAT or bewertung is None or int(bewertung["daumen"]) <= 0:
        return None
    return [[("📦 Upload-Paket", f"pk:{eid}:")]]


def _parse(daten: str) -> tuple[str, int, str]:
    """Callback-Daten "pk:41:" → ("pk", 41, ""), "pt:41:t" → ("pt", 41, "t"). ValueError bei allem anderen
    (wie lernbot.parse, nur mit den Präfixen dieses Moduls)."""
    teile = (daten or "").split(":")
    if len(teile) != 3 or teile[0] not in PRAEFIXE or not teile[1].isdigit():
        raise ValueError(daten)
    return teile[0], int(teile[1]), teile[2]


def lies_link_argumente(args: list[str]) -> tuple[int, str]:
    """`/link 41 <url>` oder `/link e41 <url>` → (41, url). ValueError mit Aufruf-Hilfe bei allem anderen
    („Aufruf: /link 41 https://www.tiktok.com/@…/video/…“). Ob der Link passt, prüft erst link_speichern.
    Beispiel: ["e41", "https://www.tiktok.com/@ich/video/7300"] → (41, "https://www.tiktok.com/@ich/video/7300")."""
    if len(args) != 2:
        raise ValueError(LINK_AUFRUF)
    treffer = LINK_NUMMER.fullmatch(args[0])
    if treffer is None:
        raise ValueError(LINK_AUFRUF)
    return int(treffer.group(1)), args[1]


def paket_erlaubt(con: sqlite3.Connection, entwurf_id: int) -> str | None:
    """Die fachliche Prüfung für Paket, Häkchen und /link: None = erlaubt, sonst der Grund als Satz für dich.
    Regeln: Entwurf existiert („Entwurf #41 gibt es nicht“) · Format short („Upload-Paket gibt es nur für
    Shorts“) · mit 👍 bewertet („Entwurf #41 ist nicht mit 👍 bewertet“)."""
    zeile = con.execute("SELECT format FROM entwuerfe WHERE id = ?", (entwurf_id,)).fetchone()
    # Regel 1: den Entwurf gibt es (Tippfehler bei /link)
    if zeile is None:
        return f"Entwurf #{entwurf_id} gibt es nicht."
    # Regel 2: nur Shorts – ein Zusammenschnitt (16:9) liefert kein Publikumssignal (Annahme A26)
    if zeile["format"] != NUR_FORMAT:
        return (f"Upload-Paket gibt es nur für Shorts (Entwurf #{entwurf_id} ist ein "
                f"{lernbot.FORMAT_NAMEN.get(zeile['format'], zeile['format'])}).")
    # Regel 3: nur mit 👍 – kein Short geht ohne deine Freigabe raus (Annahme A5)
    bewertung = con.execute("SELECT daumen FROM entwurf_bewertungen WHERE entwurf_id = ?", (entwurf_id,)).fetchone()
    if bewertung is None or int(bewertung["daumen"]) <= 0:
        return f"Entwurf #{entwurf_id} ist nicht mit 👍 bewertet."
    return None


def checkliste_stand(con: sqlite3.Connection, konfig: Konfig, entwurf_id: int) -> dict[str, bool]:
    """{plattform: erledigt?} für publikum.post_plattformen(konfig) – erledigt = publikum.post_zu findet einen Post.
    Beispiel: {"tiktok": False} vor dem Häkchen, {"tiktok": True} danach."""
    return {p: publikum.post_zu(con, "entwurf", entwurf_id, p) is not None for p in publikum.post_plattformen(konfig)}


def knoepfe_checkliste(entwurf_id: int, stand: dict[str, bool]) -> Knoepfe:
    """Je offene Plattform ein Knopf „✅ TikTok erledigt“ (`pt:<eid>:t`); erledigte fallen weg, alle erledigt → []."""
    return [[(f"✅ {_name(p)} erledigt", f"pt:{entwurf_id}:{KUERZEL_VON[p]}")]
            for p, erledigt in stand.items() if not erledigt and p in KUERZEL_VON]


def _checkliste_text(entwurf_id: int, stand: dict[str, bool]) -> str:
    """Text der Checkliste (HTML): je Plattform ✅ oder ⬜ und was als Nächstes zu tun ist."""
    if not stand:
        return "📋 Keine Post-Plattform eingestellt ([publikum].plattformen) – für diesen Entwurf entsteht kein Post."
    zeilen = [f"📋 <b>Entwurf #{entwurf_id} – Checkliste</b>"]
    zeilen += [f"{'✅' if erledigt else '⬜'} {_name(p)}" for p, erledigt in stand.items()]
    zeilen.append(f"Gepostet? Häkchen antippen und den Link schicken: <code>/link {entwurf_id} https://…</code> – "
                  "dann bekommst du die Post-Nummer für die Screenshots.")
    return "\n".join(zeilen)


def haken_setzen(con: sqlite3.Connection, konfig: Konfig, entwurf_id: int, plattform: str,
                 zeit: datetime | None = None) -> tuple[str, int | None]:
    """Häkchen: legt den Post an (ohne Link), in einer Transaktion (db.transaktion). Rückgabe (Antworttext,
    post_id oder None), z. B. („TikTok ✅ – Post #17. Link später mit /link 41 …“, 17). Doppelklick → derselbe
    Post, Text „schon erledigt (Post #17)“. paket_erlaubt verneint → (Grund, None). Plattform außerhalb
    [publikum].plattformen → Hinweis, kein Post.

    zeit (Standard: jetzt) wird gepostet_utc – aber nur beim ersten Häkchen. Liest die Schnittliste des Entwurfs
    (publikum.entwurf_post_daten, auf dem Mini): fehlt sie, fliegt FileNotFoundError, ist sie kaputt ValueError
    – bei_klick meldet das."""
    # Regel: Posts nur für die Plattformen der Lernschleife (Annahme A9)
    if plattform not in publikum.post_plattformen(konfig):
        return f"{_name(plattform)} wird in der Lernschleife nicht verfolgt ([publikum].plattformen) – kein Post.", None
    if grund := paket_erlaubt(con, entwurf_id):
        return grund, None
    daten = publikum.entwurf_post_daten(con, konfig, entwurf_id)
    with db.transaktion(con):
        post_id, neu = publikum.post_anlegen(con, art="entwurf", ziel_id=entwurf_id, plattform=plattform, daten=daten,
                                             zeit=zeit or jetzt())
    if not neu:
        return f"{_name(plattform)}: schon erledigt (Post #{post_id})", post_id
    return f"{_name(plattform)} ✅ – Post #{post_id}. Link später mit /link {entwurf_id} …", post_id


def link_speichern(con: sqlite3.Connection, konfig: Konfig, entwurf_id: int, url: str,
                   zeit: datetime | None = None) -> tuple[str, int | None]:
    """/link im Lern-Bot: Plattform mit aktionen.plattform_aus_url erkennen, dann in EINER Transaktion
    publikum.post_anlegen (legt an oder findet den vorhandenen) und publikum.link_nachtragen (ersetzt einen
    falschen Link). Rückgabe (Antworttext mit Post-Nummer, post_id oder None), z. B. („🔗 Post #17 (TikTok) –
    für Screenshots: Bildunterschrift #17“, 17).
    Kein Post bei: unbekanntem Link (nicht https oder fremde Domain), clip-battle.de („für Entwürfe kein Post“),
    Plattform außerhalb [publikum].plattformen, paket_erlaubt verneint – jeweils (Hinweis, None).

    Eine Transaktion heißt: Scheitert link_nachtragen, gibt es auch keinen neuen Post (nichts Halbes). Gab es den
    Post schon (Häkchen), bleibt sein gepostet_utc. Fehlende oder kaputte Schnittliste: FileNotFoundError bzw.
    ValueError an den Aufrufer (cmd_link meldet es)."""
    url = url.strip()
    plattform = aktionen.plattform_aus_url(url)
    # Regel 1: nur https-Links bekannter Plattformen (dieselbe Erkennung wie im Clip-Bot)
    if plattform is None:
        return "Unbekannter Link – erwartet https://… von TikTok oder YouTube.", None
    # Regel 2: clip-battle.de ist keine Post-Plattform – dort wird eingereicht, nicht geschaut
    if plattform not in publikum.PLATTFORMEN:
        return (f"{_name(plattform)}-Links legen für Entwürfe keinen Post an (Einreichen gibt es nur im Clip-Bot). "
                "Für die Zahlen brauche ich den TikTok-Link."), None
    # Regel 3: nur Plattformen, auf denen die Lernschleife Zahlen sammelt (Annahme A9)
    if plattform not in publikum.post_plattformen(konfig):
        return f"{_name(plattform)} wird in der Lernschleife nicht verfolgt ([publikum].plattformen) – kein Post.", None
    # Regel 4: 👍-Short (paket_erlaubt)
    if grund := paket_erlaubt(con, entwurf_id):
        return grund, None
    daten = publikum.entwurf_post_daten(con, konfig, entwurf_id)
    with db.transaktion(con):
        vorher = publikum.post_zu(con, "entwurf", entwurf_id, plattform)
        post_id, _ = publikum.post_anlegen(con, art="entwurf", ziel_id=entwurf_id, plattform=plattform, daten=daten,
                                           zeit=zeit or jetzt())
        publikum.link_nachtragen(con, post_id, url)
    ersetzt = vorher is not None and vorher["url"] not in (None, url)
    was = "Link ersetzt" if ersetzt else "gespeichert"
    return f"🔗 Post #{post_id} ({_name(plattform)}) {was} – für Screenshots: Bildunterschrift #{post_id}", post_id


def baue_paket(konfig: Konfig, entwurf_id: int) -> dict:
    """Läuft im Thread: eigene DB-Verbindung (db.verbinde), Sperre holen, Upload-Fassung rendern
    (entwurf.upload_fassung, idempotent) und Caption bauen (caption.entwurf_caption(con, liste, konfig)).
    Rückgabe {"datei": Pfad, "caption": Text, "dateiname": "clip-battle_e41.mp4"}.
    MedienFehler/KonfigFehler/sperre.Gesperrt gehen an den Aufrufer (sende_paket sagt es dir im Bot). Weckt nie.

    Die Caption kommt zuerst: sie ist billig, und ein Fehler in der Vorlage (CaptionFehler) soll keinen Render
    kosten. Die Sperre hält nur das Rendern; ist sie länger als [sperre].warten_s belegt → Gesperrt."""
    con = db.verbinde(konfig.datenbank)
    try:
        zeile = con.execute("SELECT schnittliste FROM entwuerfe WHERE id = ?", (entwurf_id,)).fetchone()
        if zeile is None:
            raise MedienFehler(f"Entwurf {entwurf_id} unbekannt")
        liste = json.loads(Path(zeile["schnittliste"]).read_text(encoding="utf-8"))
        text = caption.entwurf_caption(con, liste, konfig)
        # Rendern ist ein rechenintensiver Schritt: dieselbe Sperre wie Pipeline und Entwürfe (nur einer gleichzeitig)
        with sperre(konfig.datenbank.with_suffix(".lock"), warten_s=float(konfig.wert("sperre.warten_s", 7200))):
            ergebnis = entwurf.upload_fassung(con, konfig, entwurf_id)
    finally:
        con.close()
    return {"datei": ergebnis["datei"], "caption": text, "dateiname": f"clip-battle_e{entwurf_id}.mp4"}


async def sende_paket(app, entwurf_id: int) -> None:
    """Paket bauen (Thread) und schicken: Datei per send_document, Caption als <pre> (zum Kopieren), Checkliste
    mit Knöpfen. Fehler: dir kurz den Grund („⚠️ Upload-Paket: Moment-Datei fehlt …“, ohne Pfade mit Token o. ä.),
    Details ins Log. bot_data["paket_arbeitet"] wird immer zurückgesetzt.

    Bekannte Fehler (MedienFehler, KonfigFehler, CaptionFehler, Datei-/JSON-Fehler) gehen mit ihrem Text an dich;
    ein unerwarteter Fehler nur mit seinem Typ – sein Text könnte Dinge enthalten, die nicht in den Chat gehören."""
    con, konfig, chat = app.bot_data["con"], app.bot_data["konfig"], app.bot_data["erlaubt"]
    app.bot_data["paket_arbeitet"] = True
    try:
        await app.bot.send_message(chat, f"📦 Baue das Upload-Paket für Entwurf #{entwurf_id} (1080×1920) … "
                                         "Rendert gerade ein Entwurf, warte ich auf ihn.")
        try:
            paket = await asyncio.to_thread(baue_paket, konfig, entwurf_id)
        except Gesperrt:
            log.warning("Upload-Paket Entwurf #%s: Pipeline-Sperre belegt", entwurf_id)
            await app.bot.send_message(chat, "⏳ Gerade läuft ein anderer rechenintensiver Schritt (Pipeline-Sperre). "
                                             "Drück 📦 später noch einmal.")
            return
        except (MedienFehler, KonfigFehler, caption.CaptionFehler, OSError, ValueError) as fehler:
            log.warning("Upload-Paket Entwurf #%s: %s", entwurf_id, fehler)
            await app.bot.send_message(chat, f"⚠️ Upload-Paket: {str(fehler)[:FEHLER_MAX]}")
            return
        with open(paket["datei"], "rb") as datei:
            # Als Datei (nicht als Video): Telegram komprimiert Dateien nicht neu, du lädst genau diese Fassung hoch.
            # Zeitgrenzen wie beim Clip-Bot-Paket: bis 48 MB hochladen dauert, 300 s Lesen/Schreiben, 30 s Verbinden
            await app.bot.send_document(chat, document=datei, filename=paket["dateiname"],
                                        caption=f"📦 Entwurf #{entwurf_id} – Upload-Fassung",
                                        read_timeout=300, write_timeout=300, connect_timeout=30)
        await app.bot.send_message(chat, f"<pre>{escape(paket['caption'])}</pre>", parse_mode="HTML")
        stand = checkliste_stand(con, konfig, entwurf_id)
        knoepfe = knoepfe_checkliste(entwurf_id, stand)
        await app.bot.send_message(chat, _checkliste_text(entwurf_id, stand), parse_mode="HTML",
                                   reply_markup=lernbot._markup(knoepfe) if knoepfe else None)
    except Exception as fehler:  # der Bot soll weiterlaufen; Details (mit Traceback) nur ins Log
        log.exception("Upload-Paket Entwurf #%s fehlgeschlagen", entwurf_id)
        await app.bot.send_message(chat, f"⚠️ Upload-Paket fehlgeschlagen ({type(fehler).__name__}) – Details im Log.")
    finally:
        app.bot_data["paket_arbeitet"] = False


async def bei_klick(update, context) -> None:
    """Knöpfe pk: und pt:. Prüft selbst, ob der Klick von dir kommt – sonst „Nicht erlaubt.“. Doppelklick auf pk:
    während des Renderns → „⏳ Paket wird schon gebaut“. pt: → haken_setzen, Knöpfe mit knoepfe_checkliste neu.

    pk: startet sende_paket als eigene Aufgabe (application.create_task): Der Handler ist sofort fertig, der Bot
    nimmt weiter Klicks an, und ein zweiter 📦-Klick sieht bot_data["paket_arbeitet"] schon gesetzt."""
    from telegram.error import BadRequest

    query = update.callback_query
    con, konfig = context.bot_data["con"], context.bot_data["konfig"]
    if query.from_user is None or query.from_user.id != context.bot_data["erlaubt"]:
        await query.answer("Nicht erlaubt.")
        return
    try:
        aktion, eid, extra = _parse(query.data)
    except ValueError:
        await query.answer("Unbekannter Knopf.")
        return

    if aktion == "pk":
        if grund := paket_erlaubt(con, eid):
            await query.answer(grund[:aktionen.HINWEIS_MAX])  # Telegram zeigt am Knopf höchstens 200 Zeichen
            return
        if context.bot_data.get("paket_arbeitet"):
            await query.answer("⏳ Paket wird schon gebaut – gleich kommt es.")
            return
        context.bot_data["paket_arbeitet"] = True  # sofort setzen, nicht erst in der Aufgabe (Doppelklick)
        await query.answer("📦 Paket wird gebaut …")
        context.application.create_task(sende_paket(context.application, eid))
        return

    plattform = PLATTFORM_KUERZEL.get(extra)
    if plattform is None:
        await query.answer("Unbekannter Knopf.")
        return
    try:
        text, post_id = haken_setzen(con, konfig, eid, plattform)
    except (OSError, ValueError, KeyError) as fehler:  # Schnittliste fehlt/kaputt – Details ins Log
        log.warning("Häkchen Entwurf #%s: kein Post (%s: %s)", eid, type(fehler).__name__, fehler)
        await query.answer("⚠️ Kein Post angelegt – Details im Log.")
        return
    await query.answer(text[:aktionen.HINWEIS_MAX])
    if post_id is None:
        return
    stand = checkliste_stand(con, konfig, eid)
    knoepfe = knoepfe_checkliste(eid, stand)
    try:
        await query.edit_message_text(_checkliste_text(eid, stand), parse_mode="HTML",
                                      reply_markup=lernbot._markup(knoepfe) if knoepfe else None)
    except BadRequest:  # „message is not modified“ beim Doppelklick – nichts zu tun
        pass


async def cmd_link(update, context) -> None:
    """/link <entwurf-nr> <url> (auch e<nr>). Antwort: der Text aus link_speichern (mit Post-Nummer) bzw. die
    Aufruf-Hilfe aus lies_link_argumente. Wirft nicht – Fehler landen als kurzer Satz bei dir, Details im Log."""
    nachricht = update.effective_message
    try:
        eid, url = lies_link_argumente(list(context.args or []))
    except ValueError as fehler:
        await nachricht.reply_text(str(fehler))
        return
    try:
        text, _ = link_speichern(context.bot_data["con"], context.bot_data["konfig"], eid, url)
    except (OSError, ValueError, KeyError) as fehler:  # Schnittliste fehlt/kaputt, Post verschwunden
        log.warning("/link Entwurf #%s: kein Post (%s: %s)", eid, type(fehler).__name__, fehler)
        text = f"⚠️ Link nicht gespeichert: {str(fehler)[:FEHLER_MAX]}"
    except Exception as fehler:
        log.exception("/link Entwurf #%s fehlgeschlagen", eid)
        text = f"⚠️ Link nicht gespeichert ({type(fehler).__name__}) – Details im Log."
    await nachricht.reply_text(text)


def registriere(app, nur_ich) -> None:
    """Hängt die Handler in die Lern-Bot-App (aufgerufen von lernbot.baue_app, vor dessen Klick-Handler)."""
    from telegram.ext import CallbackQueryHandler, CommandHandler

    app.add_handler(CommandHandler("link", cmd_link, filters=nur_ich))
    app.add_handler(CallbackQueryHandler(bei_klick, pattern=KLICK_MUSTER))
