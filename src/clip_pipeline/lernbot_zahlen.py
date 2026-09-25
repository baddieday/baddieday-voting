"""Lern-Bot: Publikumszahlen annehmen – Screenshot oder Hand-Eingabe (Spec §7.1).

So läuft es für dich:
  1. Du schickst dem Lern-Bot einen Screenshot der TikTok-Statistik, Bildunterschrift „#17“ (= Post-Nummer,
     die der Bot nach /link nennt; /publikum zeigt sie auch). Am besten als Foto; als Datei gehen JPG, PNG und
     WebP (HEIC vom iPhone nicht – dann bittet der Bot, es als Foto zu schicken).
     Ohne Nummer fragt der Bot mit Knöpfen der letzten fünf Posts ohne Messung (`pl:<post_id>:`); das Bild
     wartet so lange (höchstens 10 min, dann verworfen mit Hinweis).
  2. Claude liest die Zahlen (screenshot.py). Passen sie zur letzten Messung (publikum.pruefe_plausibel), werden
     sie gespeichert und der Bot bestätigt sie. Sonst zeigt er die gelesenen Zahlen, nennt den Verstoß und fragt
     „Stimmt das? ✅ / ✏️ von Hand“ (`pm:<post_id>:ok` / `pm:<post_id>:hand`).
  3. Hand-Eingabe (wenn [lernbot].screenshot_claude = false, Claude nichts lesen konnte oder du ✏️ tippst): du
     antwortest mit `views likes wiedergabe voll%`, z. B. `1240 61 6.8 34` („–“ für unbekannt). Ganz ohne Bild
     geht es auch: `#17 1240 61 6.8 34` als Text. Auch Hand-Zahlen prüft der Bot gegen die letzte Messung (ein
     Tippfehler lässt sonst die Views „sinken“) und fragt bei einem Verstoß mit denselben Knöpfen nach.
     Wartet der Bot auf eine Antwort zu gelesenen Zahlen, darfst du statt ✏️ auch gleich die richtigen Zahlen
     schicken.

Technik: Das Bild liegt nur in einem temporären Ordner (tempfile.mkdtemp, im Dienst PrivateTmp) und wird nach der
Auswertung gelöscht – auch bei jedem Fehler –, nie im Puffer (/srv/clips), dort wird nichts gelöscht (CLAUDE.md
„Nie löschen“, Annahme A21). Claude läuft per asyncio.to_thread ohne Datenbank; Prüfen und Speichern passiert im
Event-Loop mit bot_data["con"]. Jeder Claude-Aufruf wird gezählt (claude_aufruf.protokolliere). Nichts hier
weckt pve-big.

Zustand: bot_data["publikum"] hält höchstens EINEN offenen Vorgang (du bist der einzige Nutzer), als dict mit
  art      "bild" (wartet auf pl:-Wahl) | "rueckfrage" (wartet auf pm:) | "hand" (wartet auf Text)
  post_id  None, solange kein Post gewählt ist
  ordner   Temp-Ordner mit dem Bild (nur bei art "bild"; None, sobald das Bild gelöscht ist)
  bild     Pfad des Bildes in diesem Ordner
  werte    gelesene bzw. eingegebene Zahlen (bei "rueckfrage"), quelle, roh
  seit     Zeitpunkt (für aufraeumen) – jeder Schritt (Post gewählt, ✏️) startet die 10 min neu
Ein neues Foto oder ein neuer „#17 …“-Text ersetzt einen offenen Vorgang: dessen Bild wird sofort gelöscht, und
der Bot sagt „vorheriges Bild verworfen“. Ein Klick, der nicht zum offenen Vorgang passt (alte Nachricht,
Doppelklick), bekommt „Schon erledigt.“ und ändert nichts. Kommt Claudes Ergebnis erst, nachdem der Vorgang
verworfen wurde (aufraeumen), wird es nicht mehr gespeichert – gezählt wird der Aufruf trotzdem.

Secrets: Nach get_file() steht in File.file_path die Download-URL MIT dem Bot-Token
(https://api.telegram.org/file/bot<TOKEN>/…). file_path und URLs deshalb nie loggen, nie an dich schicken, nie in
eine Ausnahme-Nachricht packen; bei einem Download-Fehler nur den Ausnahmetyp loggen (`type(e).__name__`) und
dir „Download fehlgeschlagen“ sagen. Ebenso nie ins Log: Bilder, Chat-IDs, Claude-Rohantworten, gelesene Zahlen,
Pfade der Bilder.

Zeit: `jetzt` ist auf Modulebene importiert – Tests ersetzen `lernbot_zahlen.jetzt` (mock.patch.object).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from . import claude_aufruf, db, lernbot, publikum, screenshot
from .bot.aktionen import PLATTFORM_NAMEN
from .zeit import aus_iso, jetzt, utc_zu_lokal  # jetzt im Modul importiert: Tests ersetzen `lernbot_zahlen.jetzt`

log = logging.getLogger("lern-bot")

# Callback-Präfixe dieses Moduls (≤ 64 Byte je Knopf): pl:<post_id>:        Post für ein wartendes Bild wählen
#                                                        pm:<post_id>:ok|hand  Zahlen bestätigen / von Hand eingeben
PRAEFIXE = ("pl", "pm")
KLICK_MUSTER = r"^(pl|pm):"
WARTEN_S = 600  # so lange wartet ein offener Vorgang (Bild ohne Post-Nummer, Rückfrage, Hand-Eingabe) – Spec §7.1
WARTEN_MIN = WARTEN_S // 60  # dieselbe Wartezeit in Minuten – so steht sie in Meldungen an dich

Knoepfe = list[list[tuple[str, str]]]

# Unter diesem Schlüssel liegt der offene Vorgang in bot_data (Aufbau im Modul-Docstring)
ZUSTAND = "publikum"
# Antworten auf die Rückfrage: pm:<post_id>:ok bzw. pm:<post_id>:hand
RUECKFRAGE_ANTWORTEN = ("ok", "hand")
# „#17“ irgendwo in einer Bildunterschrift; \b: „#17abc“ ist keine Nummer
POST_NUMMER = re.compile(r"#(\d+)\b")
# Messungs-Text „#17 1240 61 6.8 34“: die Nummer ganz vorn, der Rest geht an publikum.lies_hand_eingabe
MESSUNGS_TEXT = re.compile(r"^\s*#(\d+)\b(.*)$", re.DOTALL)

# Telegram liefert Fotos immer als JPEG, in mehreren Größen – die letzte ist die größte (Zahlen am besten lesbar)
FOTO_ENDUNG = ".jpg"
# Endung aus dem Dateinamen, wenn Telegram keinen mime_type nennt. „.jpeg“ wird zu „.jpg“, weil lies_zahlen nur die
# Endungen aus screenshot.BILD_ENDUNGEN annimmt (eine Liste, nicht zwei).
DATEI_ENDUNGEN = {".jpg": ".jpg", ".jpeg": ".jpg", ".png": ".png", ".webp": ".webp"}
# So heißt das empfangene Bild im wartenden Temp-Ordner (Claude sieht später nur eine Kopie, screenshot.BILD_STAMM)
EINGANG_STAMM = "eingang"

# Anzeige in Knöpfen und Meldungen
ART_NAMEN = {"clip": "Clip", "entwurf": "Entwurf"}
QUELLEN_NAMEN = {"screenshot": "Screenshot", "hand": "von Hand"}
# werte_text: Symbol je Zähler (so knapp, dass alle sieben Werte in eine Handy-Zeile passen)
ZAEHLER_SYMBOLE = (("views", "👁"), ("likes", "❤️"), ("kommentare", "💬"), ("shares", "↗️"), ("saves", "🔖"))
HAND_BITTE = ("✏️ Bitte die Zahlen für #{nr} von Hand: views likes wiedergabe voll% – z. B. „1240 61 6,8 34“, "
              "„–“ für unbekannt.")
UNVERSTANDEN = ("🤔 Das verstehe ich nicht. Zahlen für einen Post: Screenshot mit #Nummer in der Bildunterschrift "
                "oder Text „#17 1240 61 6,8 34“. Mehr unter /hilfe.")


# --- Texte und Knöpfe (ohne Telegram testbar) -----------------------------------------------------------------

def post_nummer_aus_text(text: str | None) -> int | None:
    """„#17“ irgendwo in der Bildunterschrift → 17; sonst None. Beispiel: "Stand Tag 3 #17" → 17."""
    treffer = POST_NUMMER.search(text or "")
    return int(treffer.group(1)) if treffer else None


def lies_text_eingabe(text: str) -> tuple[int, dict] | None:
    """Hand-Eingabe ohne Bild: „#17 1240 61 6.8 34“ → (17, Werte wie publikum.lies_hand_eingabe). Fehlt die
    #Nummer am Anfang → None (dann ist es kein Messungs-Text). ValueError aus lies_hand_eingabe bei kaputten Zahlen
    (auch bei „#17“ ganz ohne Zahlen – dann nennt der Fehlertext die erwartete Form)."""
    treffer = MESSUNGS_TEXT.match(text)
    if treffer is None:
        return None
    return int(treffer.group(1)), publikum.lies_hand_eingabe(treffer.group(2))


def knoepfe_posts(posts: list, *, zeitzone: str = "UTC") -> Knoepfe:
    """Ein Knopf je Post („#17 · Entwurf 41 · TikTok · 26.09.“, `pl:17:`), untereinander. posts: Zeilen aus
    `posts` (publikum.posts_ohne_messung). Das Datum ist der Post-Tag in `zeitzone` (der Lern-Bot gibt
    [zeit].zeitzone mit – sonst stünde ein Post von 00:30 Uhr beim Vortag)."""
    knoepfe: Knoepfe = []
    for p in posts:
        ziel_id = p["clip_id"] if p["art"] == "clip" else p["entwurf_id"]
        tag = utc_zu_lokal(aus_iso(p["gepostet_utc"]), zeitzone)
        text = (f"#{p['id']} · {ART_NAMEN[p['art']]} {ziel_id} · "
                f"{PLATTFORM_NAMEN.get(p['plattform'], p['plattform'])} · {tag:%d.%m.}")
        knoepfe.append([(text, f"pl:{p['id']}:")])
    return knoepfe


def knoepfe_rueckfrage(post_id: int) -> Knoepfe:
    """„✅ Stimmt“ (`pm:<post_id>:ok`) und „✏️ Von Hand“ (`pm:<post_id>:hand`) in einer Reihe – für gelesene wie für
    von Hand eingegebene Zahlen. Beispiel: knoepfe_rueckfrage(17) == [[("✅ Stimmt", "pm:17:ok"),
    ("✏️ Von Hand", "pm:17:hand")]]."""
    return [[("✅ Stimmt", f"pm:{post_id}:ok"), ("✏️ Von Hand", f"pm:{post_id}:hand")]]


def parse(daten: str | None) -> tuple[str, int, str]:
    """Callback-Daten dieses Moduls zerlegen: "pl:17:" → ("pl", 17, ""), "pm:17:ok" → ("pm", 17, "ok").
    ValueError bei allem anderen (fremdes Präfix, keine Nummer, pl: mit Antwort, pm: ohne ok/hand)."""
    teile = (daten or "").split(":")
    if len(teile) != 3 or teile[0] not in PRAEFIXE or not teile[1].isdigit():
        raise ValueError(daten)
    aktion, nummer, antwort = teile
    if (aktion == "pl" and antwort) or (aktion == "pm" and antwort not in RUECKFRAGE_ANTWORTEN):
        raise ValueError(daten)
    return aktion, int(nummer), antwort


def _ganzzahl(wert) -> str:
    """1240 → „1 240“ (Leerzeichen als Tausendertrenner – eindeutig, anders als Punkt oder Komma)."""
    return f"{int(wert):,}".replace(",", " ")


def _dezimal(wert) -> str:
    """6.8 → „6,8“, 34.0 → „34“: eine Nachkommastelle mit Komma, „,0“ fällt weg."""
    text = f"{float(wert):.1f}"
    return (text[:-2] if text.endswith(".0") else text).replace(".", ",")


def werte_text(werte: dict) -> str:
    """Gelesene Zahlen lesbar, z. B. „👁 1 240 · ❤️ 61 · 💬 3 · ↗️ 5 · 🔖 2 · ⏱ 6,8 s · ✅ 34 %“; None = „–“."""
    teile = [f"{symbol} {'–' if werte.get(feld) is None else _ganzzahl(werte[feld])}"
             for feld, symbol in ZAEHLER_SYMBOLE]
    wiedergabe, voll = werte.get("wiedergabe_s"), werte.get("voll_prozent")
    teile.append("⏱ –" if wiedergabe is None else f"⏱ {_dezimal(wiedergabe)} s")
    teile.append("✅ –" if voll is None else f"✅ {_dezimal(voll)} %")
    return " · ".join(teile)


def endung_fuer(mime_type: str | None, dateiname: str | None) -> str | None:
    """Endung, unter der ein als Datei geschicktes Bild abgelegt wird: aus mime_type (screenshot.BILD_ENDUNGEN),
    sonst aus dem Dateinamen (.jpg/.jpeg/.png/.webp). None = Format, das claude nicht lesen kann (z. B. HEIC) –
    der Bot antwortet dann „Bitte als Foto schicken“. Beispiel: ("image/png", "x.png") → ".png".
    Nennt Telegram einen mime_type, gilt nur er: „image/heic“ bleibt None, auch wenn die Datei „.jpg“ heißt."""
    if mime_type:
        return screenshot.BILD_ENDUNGEN.get(mime_type)
    if dateiname:
        return DATEI_ENDUNGEN.get(Path(dateiname).suffix.lower())
    return None


# --- Vorgang und Versand ----------------------------------------------------------------------------------------

async def _sag(app, text: str, knoepfe: Knoepfe | None = None) -> None:
    """Nachricht an dich (bot_data["erlaubt"] – der Lern-Bot hat genau einen Empfänger). Knöpfe über
    lernbot._markup, dieselbe Umwandlung wie für die Entwurfs-Knöpfe. Reiner Text, kein HTML."""
    await app.bot.send_message(app.bot_data["erlaubt"], text,
                               reply_markup=lernbot._markup(knoepfe) if knoepfe else None)


def _bild_weg(vorgang: dict) -> None:
    """Löscht den Temp-Ordner mit dem Bild eines Vorgangs (falls noch da). Kein Bildarchiv (Spec §7.1)."""
    if vorgang.get("ordner") is not None:
        shutil.rmtree(vorgang["ordner"], ignore_errors=True)
        vorgang["ordner"] = None


def _verwerfen(bot_data: dict) -> bool:
    """Beendet den offenen Vorgang und löscht sein Bild. True, wenn dabei ein Bild verworfen wurde (dann sagt der
    Aufrufer dir das); False, wenn nichts oder nur eine Rückfrage/Hand-Eingabe offen war."""
    alt = bot_data.pop(ZUSTAND, None)
    if alt is None or alt.get("ordner") is None:
        return False
    _bild_weg(alt)
    return True


async def _knoepfe_weg(query) -> None:
    """Knöpfe unter der angeklickten Nachricht entfernen, damit kein zweiter Klick mehr möglich ist."""
    from telegram.error import TelegramError

    with contextlib.suppress(TelegramError):  # z. B. Nachricht zu alt oder schon ohne Knöpfe – egal
        await query.edit_message_reply_markup(reply_markup=None)


async def _speichern(app, post_id: int, werte: dict, quelle: str, roh: str | None) -> None:
    """Speichert eine geprüfte bzw. von dir bestätigte Messung (eigene kleine Transaktion) und schließt den
    offenen Vorgang. Die Zeit ist `jetzt()` dieses Moduls (in Tests fest)."""
    con = app.bot_data["con"]
    with db.transaktion(con):
        publikum.speichere_messung(con, post_id, werte, quelle, roh=roh, zeit=jetzt())
    app.bot_data.pop(ZUSTAND, None)
    log.info("Zahlen für Post #%s gespeichert (%s)", post_id, quelle)
    await _sag(app, f"💾 #{post_id} gespeichert ({QUELLEN_NAMEN[quelle]}): {werte_text(werte)}")


async def _pruefen_und_speichern(app, post_id: int, werte: dict, quelle: str, roh: str | None) -> None:
    """Nichts wird ungeprüft gespeichert (Spec §7.1): Passen die Zahlen zur letzten Messung und zur Videolänge
    (publikum.pruefe_plausibel), werden sie gespeichert. Sonst wird der Vorgang zur Rückfrage – die Zahlen warten
    auf ✅ bzw. ✏️ (pm:-Knöpfe)."""
    con = app.bot_data["con"]
    zeile = publikum.post(con, post_id)
    verstoesse = publikum.pruefe_plausibel(werte, publikum.letzte_messung(con, post_id), zeile["dauer_s"])
    if not verstoesse:
        await _speichern(app, post_id, werte, quelle, roh)
        return
    app.bot_data[ZUSTAND] = {"art": "rueckfrage", "post_id": post_id, "ordner": None, "bild": None, "werte": werte,
                             "quelle": quelle, "roh": roh, "seit": jetzt()}
    log.info("Zahlen für Post #%s: Rückfrage (%s Regel(n) verletzt)", post_id, len(verstoesse))
    zeilen = [f"🤔 Zahlen für #{post_id}: {werte_text(werte)}", *(f"⚠️ {v}" for v in verstoesse), "Stimmt das?"]
    await _sag(app, "\n".join(zeilen), knoepfe_rueckfrage(post_id))


async def _auswerten(app, vorgang: dict) -> None:
    """Das wartende Bild eines Vorgangs mit gewähltem Post lesen lassen, danach prüfen und speichern.
    Claude aus ([lernbot].screenshot_claude = false) oder nichts lesbar → Hand-Eingabe für diesen Post."""
    konfig, con = app.bot_data["konfig"], app.bot_data["con"]
    post_id = vorgang["post_id"]
    if not screenshot.einstellung(konfig, "screenshot_claude"):
        _bild_weg(vorgang)  # ohne Claude braucht niemand das Bild
        vorgang["art"] = "hand"
        await _sag(app, "Screenshot-Lesen ist aus.\n" + HAND_BITTE.format(nr=post_id))
        return

    await _sag(app, f"🔎 Lese die Zahlen für #{post_id} …")
    try:
        lesung = await asyncio.to_thread(screenshot.lies_zahlen, konfig, vorgang["bild"])
    finally:
        _bild_weg(vorgang)  # nach der Auswertung gelöscht – auch wenn sie scheitert
    if lesung.claude is not None:
        claude_aufruf.protokolliere(con, "screenshot", lesung.claude)
    if app.bot_data.get(ZUSTAND) is not vorgang:  # inzwischen verworfen (aufraeumen) oder ersetzt
        log.info("Screenshot für Post #%s: Ergebnis verworfen – der Vorgang ist nicht mehr offen", post_id)
        return
    if lesung.werte is None:
        vorgang["art"] = "hand"
        await _sag(app, f"⚠️ Ich konnte die Zahlen nicht lesen ({lesung.hinweis}).\n" + HAND_BITTE.format(nr=post_id))
        return
    await _pruefen_und_speichern(app, post_id, lesung.werte, "screenshot", lesung.roh)


# --- Handler ------------------------------------------------------------------------------------------------------

async def bei_foto(update, context) -> None:
    """Screenshot angekommen (Foto → .jpg, oder Bild als Datei → endung_fuer). Mit #Nummer: sofort auswerten;
    ohne: Post-Knöpfe. Ersetzt einen offenen Vorgang (altes Bild sofort löschen, Hinweis). Download-Fehler: nur
    den Ausnahmetyp loggen (file_path enthält den Bot-Token), dir „Download fehlgeschlagen“ sagen."""
    app, nachricht = context.application, update.effective_message
    con, konfig = app.bot_data["con"], app.bot_data["konfig"]
    if nachricht.photo:
        endung, anhang = FOTO_ENDUNG, nachricht.photo[-1]  # die größte Fassung (siehe FOTO_ENDUNG)
    else:
        anhang = nachricht.document
        endung = endung_fuer(anhang.mime_type, anhang.file_name)
    if endung is None:  # ein offener Vorgang bleibt, wie er ist – es ist ja nichts Brauchbares angekommen
        await _sag(app, "📷 Dieses Bildformat kann ich nicht lesen (z. B. HEIC vom iPhone). Bitte als Foto schicken, "
                        "nicht als Datei.")
        return

    ordner = Path(tempfile.mkdtemp(prefix="clip-screenshot-"))
    bild = ordner / f"{EINGANG_STAMM}{endung}"
    try:
        datei = await anhang.get_file()
        await datei.download_to_drive(bild)
    except Exception as e:  # jede Art Fehler (Netz, Telegram, Platte) – die Meldung könnte die URL mit Token tragen
        shutil.rmtree(ordner, ignore_errors=True)
        log.warning("Screenshot: Download fehlgeschlagen (%s)", type(e).__name__)
        await _sag(app, "⚠️ Download fehlgeschlagen – bitte das Bild nochmal schicken.")
        return

    if _verwerfen(app.bot_data):
        await _sag(app, "🗑 Vorheriges Bild verworfen.")
    vorgang = {"art": "bild", "post_id": None, "ordner": ordner, "bild": bild, "werte": None, "quelle": None,
               "roh": None, "seit": jetzt()}
    app.bot_data[ZUSTAND] = vorgang

    nummer = post_nummer_aus_text(nachricht.caption)
    if nummer is None:  # welcher Post? – die jüngsten ohne Messung in den letzten 24 h zur Wahl (Annahme A12)
        posts = publikum.posts_ohne_messung(con, zeit=jetzt())
        if not posts:
            _verwerfen(app.bot_data)
            await _sag(app, "🤷 Ich finde keinen Post ohne Messung aus den letzten 24 h. Schick den Screenshot bitte "
                            "mit #Nummer in der Bildunterschrift (/publikum zeigt die Nummern).")
            return
        await _sag(app, f"📊 Zu welchem Post gehört der Screenshot? (Das Bild wartet {WARTEN_MIN} min.)",
                   knoepfe_posts(posts, zeitzone=konfig.wert("zeit.zeitzone", "Europe/Berlin")))
        return
    if publikum.post(con, nummer) is None:
        _verwerfen(app.bot_data)
        await _sag(app, f"❓ Post #{nummer} kenne ich nicht – /publikum zeigt die Nummern.")
        return
    vorgang["post_id"] = nummer
    await _auswerten(app, vorgang)


async def bei_text(update, context) -> None:
    """Freier Text: offene Hand-Eingabe (`1240 61 6.8 34`) oder ein ganzer Messungs-Text (`#17 1240 61 6.8 34`,
    lies_text_eingabe). Beides wird mit pruefe_plausibel geprüft; bei einem Verstoß Rückfrage mit pm:-Knöpfen.
    Sonst ein kurzer Hinweis auf /hilfe. Kaputte Zahlen → der Fehlertext aus lies_hand_eingabe, Vorgang bleibt offen.
    Wartet eine Rückfrage, gelten geschickte Zahlen als Korrektur von Hand (wie ✏️ und dann die Zahlen)."""
    app = context.application
    con = app.bot_data["con"]
    text = (update.effective_message.text or "").strip()
    try:
        eingabe = lies_text_eingabe(text)
    except ValueError as fehler:
        await _sag(app, f"⚠️ {fehler}")
        return

    if eingabe is not None:  # „#17 …“: ein neuer Vorgang, auch wenn gerade einer offen ist
        post_id, werte = eingabe
        if publikum.post(con, post_id) is None:
            await _sag(app, f"❓ Post #{post_id} kenne ich nicht – /publikum zeigt die Nummern.")
            return
        if _verwerfen(app.bot_data):
            await _sag(app, "🗑 Vorheriges Bild verworfen.")
        await _pruefen_und_speichern(app, post_id, werte, "hand", None)
        return

    vorgang = app.bot_data.get(ZUSTAND)
    if vorgang is None or vorgang["art"] not in ("hand", "rueckfrage"):
        await _sag(app, UNVERSTANDEN)
        return
    try:
        werte = publikum.lies_hand_eingabe(text)
    except ValueError as fehler:
        await _sag(app, f"⚠️ {fehler}")  # der Vorgang bleibt offen – einfach nochmal schicken
        return
    await _pruefen_und_speichern(app, vorgang["post_id"], werte, "hand", None)


async def bei_klick(update, context) -> None:
    """Knöpfe pl: und pm:. Prüft selbst, ob der Klick von dir kommt (CallbackQueryHandler kennt keinen Filter) –
    sonst „Nicht erlaubt.“. Passt der Klick nicht zum offenen Vorgang (andere post_id, schon gespeichert,
    Doppelklick) → „Schon erledigt.“, nichts passiert. pm:<post_id>:ok speichert die gezeigten Zahlen,
    pm:<post_id>:hand öffnet die Hand-Eingabe für diesen Post."""
    query, app = update.callback_query, context.application
    if query.from_user is None or query.from_user.id != app.bot_data["erlaubt"]:
        await query.answer("Nicht erlaubt.")
        return
    try:
        aktion, post_id, antwort = parse(query.data)
    except ValueError:
        await query.answer("Unbekannter Knopf.")
        return
    vorgang = app.bot_data.get(ZUSTAND)

    if aktion == "pl":
        # Regel: pl: passt nur zu einem Bild, das noch auf die Wahl seines Posts wartet
        if vorgang is None or vorgang["art"] != "bild" or vorgang["post_id"] is not None:
            await query.answer("Schon erledigt.")
            return
        if publikum.post(app.bot_data["con"], post_id) is None:
            await query.answer("Post unbekannt.")
            return
        await query.answer("Ich lese die Zahlen …")
        await _knoepfe_weg(query)
        vorgang.update(post_id=post_id, seit=jetzt())
        await _auswerten(app, vorgang)
        return

    # Regel: pm: passt nur zur offenen Rückfrage genau dieses Posts
    if vorgang is None or vorgang["art"] != "rueckfrage" or vorgang["post_id"] != post_id:
        await query.answer("Schon erledigt.")
        return
    await _knoepfe_weg(query)
    if antwort == "ok":
        await _speichern(app, post_id, vorgang["werte"], vorgang["quelle"], vorgang["roh"])
        await query.answer("Gespeichert.")
    else:
        vorgang.update(art="hand", werte=None, quelle=None, roh=None, seit=jetzt())
        await query.answer("Dann bitte von Hand.")
        await _sag(app, HAND_BITTE.format(nr=post_id))


async def aufraeumen(app, zeit: datetime | None = None) -> int:
    """Offene Vorgänge älter als WARTEN_S verwerfen: Bild löschen, dir kurz sagen („⌛ Screenshot verworfen – bitte
    nochmal mit #Nummer“). Wird von lernbot._schleife regelmäßig aufgerufen (nur mit app). Rückgabe: Anzahl
    verworfener Vorgänge (0 oder 1). zeit=None → jetzt(); Tests setzen zeit oder ersetzen lernbot_zahlen.jetzt.
    Beispiel: Bild um 12:00 ohne #Nummer, kein Klick → um 12:10:30 verworfen, Rückgabe 1."""
    vorgang = app.bot_data.get(ZUSTAND)
    if vorgang is None or ((zeit or jetzt()) - vorgang["seit"]).total_seconds() <= WARTEN_S:
        return 0
    if _verwerfen(app.bot_data):
        await _sag(app, "⌛ Screenshot verworfen – bitte nochmal mit #Nummer schicken.")
    else:
        nr = vorgang["post_id"]
        await _sag(app, f"⌛ Eingabe für #{nr} verworfen (nach {WARTEN_MIN} min) – schick die Zahlen bitte "
                        f"nochmal, z. B. „#{nr} 1240 61 6,8 34“.")
    log.info("Offenen Publikums-Vorgang nach %s min verworfen", WARTEN_MIN)
    return 1


def registriere(app, nur_ich) -> None:
    """Hängt die Handler in die Lern-Bot-App (aufgerufen von lernbot.baue_app, VOR dessen allgemeinem
    Klick-Handler – sonst landete `pl:17:` in der Suche nach Entwurf 17)."""
    from telegram.ext import CallbackQueryHandler, MessageHandler, filters

    app.add_handler(MessageHandler(nur_ich & (filters.PHOTO | filters.Document.IMAGE), bei_foto))
    app.add_handler(MessageHandler(nur_ich & filters.TEXT & ~filters.COMMAND, bei_text))
    app.add_handler(CallbackQueryHandler(bei_klick, pattern=KLICK_MUSTER))
