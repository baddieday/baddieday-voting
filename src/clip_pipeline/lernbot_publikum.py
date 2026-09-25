"""Lern-Bot: /publikum, die Meldung nach dem täglichen Bewerten und die Ruhezeit dafür (Spec §12, §14 Stufe 1).

Worum es geht: Die Lernschleife „Publikum“ sammelt zu jedem geposteten Short die TikTok-Zahlen und macht daraus
nach einer Woche einen Publikums-Score (publikum.py). Dieses Modul ist die Stelle, an der du davon etwas siehst:
  - /publikum zeigt die letzten Posts mit Post-Nummer (die brauchst du für die Screenshots, „#17“), Plattform,
    Alter, letzter Messung und – sobald gesetzt – dem Score mit seinen Teilen in Worten. So siehst du ohne
    Datenbank, was die Lernschleife gerade weiß und wo noch Zahlen fehlen. Die letzte Zeile zählt die
    Claude-Aufrufe der Woche (Spec §12; /lernstand zeigt sie ab Stufe 3 mit der Rezept-Tabelle, Annahme A34).
  - Nach `pipeline publikum bewerten` (täglicher Timer) kommt höchstens eine Meldung am Tag: „📊 2 Posts bewertet …“.

Die Lernidee dahinter in Alltagssprache: Ein Score ist nur so gut wie die Zahlen, aus denen er entsteht. Wenn du
auf einen Blick siehst, welcher Post noch keine Messung hat oder warum ein Score 0 ist („Basis zu klein“), kannst
du die Lücke schließen (Screenshot schicken), statt einer Zahl zu trauen, die es noch gar nicht gibt.

Ruhezeit: Meldungen mit Schlüsseln `publikum:…` (und später `woche:…`, Stufe 5) verschickt der Lern-Bot nicht
in [telegram].leise_von … leise_bis; sie bleiben liegen und kommen danach. Die Clip-Bot-Liste
`bot.aktionen.LEISE_MELDUNGEN` gilt nur für die Tabelle `meldungen` des Clip-Bots – der Lern-Bot liest
`lern_meldungen` und braucht deshalb einen eigenen Filter (Annahme A15). Die Uhrzeit-Logik ist dieselbe
(bot.aktionen.ruhezeit) – keine zweite Kopie.

Hilfe: HILFE_ZUSATZ wird von lernbot.cmd_hilfe an lernbot.HILFE angehängt (HILFE selbst bleibt unverändert, weil
Regisseur 2.0 dort ändern könnte – so kommen sich beide beim Zusammenführen nicht in die Quere).

Alles hier liest nur die Datenbank (und schreibt höchstens eine Lern-Meldung) – kein Netz außer Telegram, kein
Rendern, kein Wecken von pve-big. Rechnungen (Alter eines Posts, Fälligkeit, Zahlformat) kommen aus publikum.py,
auch dessen kleine Helfer (_alter_tage, _zahl, _einstellung) – lieber einen „privaten“ Namen benutzen als die
Rechnung ein zweites Mal hinschreiben (Startauftrag §4). Hier wird nur angezeigt.

Zeit: `jetzt` ist auf Modulebene importiert – Tests ersetzen `lernbot_publikum.jetzt` (mock.patch.object).
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
from datetime import datetime, timedelta

from . import db, publikum
from .bot import aktionen
from .konfig import Konfig
from .zeit import iso, jetzt, utc_zu_lokal  # jetzt im Modul importiert, damit Tests `lernbot_publikum.jetzt` ersetzen

log = logging.getLogger("lern-bot")

# Lern-Meldungen mit diesen Schlüssel-Anfängen warten die Ruhezeit ab
LEISE_LERN_MELDUNGEN = ("publikum:", "woche:")

# /publikum zeigt so viele Posts, wenn du keine Zahl angibst – passt auf einen Handy-Bildschirm
PUBLIKUM_STANDARD = 10
# ... und höchstens so viele: 30 Zeilen à gut 100 Zeichen sind zwei Telegram-Nachrichten; mehr liest niemand
PUBLIKUM_MAX = 30
# Scores in Meldungen mit einer Nachkommastelle: Scores liegen zwischen −2,5 und +2,5, eine Stelle reicht zum
# Vergleichen (gespeichert ist er genauer, siehe publikum.SCORE_STELLEN)
ANZEIGE_STELLEN = 1
# Tausender-Trenner in Zahlen („1 240“): schmales, geschütztes Leerzeichen – Telegram bricht die Zahl nicht um
TAUSENDER = " "
# So nennen die Meldungen die drei Score-Teile (wie im Wochenbericht, Spec §11.1)
TEIL_NAMEN = {"r": "Wiedergabe", "e": "Likes je View", "v": "Views"}
PLATTFORM_NAMEN = {"tiktok": "TikTok", "youtube": "YouTube"}
ART_NAMEN = {"clip": "Clip", "entwurf": "Entwurf"}
# ereignisse.art, unter der claude_aufruf.protokolliere jeden neuen Claude-Aufruf zählt (Spec §12)
CLAUDE_EREIGNIS = "claude"
# Vermerk aus publikum.score_fuer, wenn weniger als publikum.MINDEST_BASIS Posts zum Vergleich da sind (Score 0)
BASIS_ZU_KLEIN = "Basis zu klein"

# Anhang an lernbot.HILFE (HTML wie dort)
HILFE_ZUSATZ = """
📊 <b>Publikum (TikTok-Zahlen):</b>
📦 Nach 👍 auf einen Short: „Upload-Paket“ – Video als Datei, Caption zum Kopieren, Häkchen je Plattform.
🔗 /link <code>41 https://www.tiktok.com/@…/video/…</code> – Post zu Entwurf 41 anlegen, der Bot nennt die Post-Nummer.
📸 Screenshot der TikTok-Statistik mit Bildunterschrift <code>#17</code> (Post-Nummer) – Claude liest die Zahlen.
✏️ Von Hand: <code>#17 1240 61 6.8 34</code> = Views, Likes, Ø Wiedergabe (s), ganz angesehen (%), „–“ = unbekannt.
/publikum – letzte Posts mit Zahlen und Score (nach 7 Tagen)"""


# --- Ruhezeit ----------------------------------------------------------------------------

def faellige_lern_meldungen(con: sqlite3.Connection, konfig: Konfig, zeit: datetime | None = None) -> list[sqlite3.Row]:
    """Ungesendete lern_meldungen (id, schluessel, text) in der Reihenfolge ihrer id; in der Ruhezeit
    (aktionen.ruhezeit) ohne die mit LEISE_LERN_MELDUNGEN – die bleiben liegen und kommen nach leise_bis.
    Beispiel: um 23:30 mit Ruhezeit 23:00–08:00 → „publikum:bewertet:…“ fehlt, ein Alarm „fehler:…“ ist dabei.
    Ruhezeit aus (leise_von leer) oder ungültig → alle (so hält ein Tippfehler in der Konfig nichts auf)."""
    zeilen = con.execute(
        "SELECT id, schluessel, text FROM lern_meldungen WHERE gesendet IS NULL ORDER BY id").fetchall()
    if not aktionen.ruhezeit(konfig, zeit or jetzt()):
        return zeilen
    return [z for z in zeilen if not z["schluessel"].startswith(LEISE_LERN_MELDUNGEN)]


# --- Kleine Anzeige-Helfer (je Format genau eine Stelle) ----------------------------------

def score_text(score: float) -> str:
    """Ein Score für Meldungen: Vorzeichen, eine Nachkommastelle, Komma; was auf 0 rundet → „0“.
    Beispiele: 0.8 → „+0,8“, −0.3 → „−0,3“ (echtes Minuszeichen), 0.04 → „0“."""
    gerundet = round(score, ANZEIGE_STELLEN)
    if gerundet == 0:  # auch −0,0: ein Score, der auf 0 rundet, bekommt kein Vorzeichen
        return "0"
    vorzeichen = "+" if gerundet > 0 else "−"
    return f"{vorzeichen}{abs(gerundet):.{ANZEIGE_STELLEN}f}".replace(".", ",")


def _anzahl(n: int) -> str:
    """Ganze Zahl mit Tausender-Trenner: 1240 → „1 240“ (schmales Leerzeichen)."""
    return f"{n:,}".replace(",", TAUSENDER)


def _tage(tage: float) -> str:
    """Alter in ganzen Tagen, abgerundet (wie „ab Tag 3“ in der Spec): 0,4 → „heute“, 1,2 → „1 Tag“, 4,1 → „4 Tage“."""
    ganz = math.floor(tage)
    if ganz < 1:
        return "heute"
    return "1 Tag" if ganz == 1 else f"{ganz} Tage"


def _score_teile(zeile: sqlite3.Row) -> dict:
    """score_teile eines Posts als dict. Kaputtes JSON → {}: die Anzeige soll nicht an einem alten Fehler hängen
    (`pipeline publikum bewerten` meldet so einen Post ohnehin mit Nummer im Log)."""
    try:
        teile = json.loads(zeile["score_teile"] or "{}")
    except json.JSONDecodeError:
        return {}
    return teile if isinstance(teile, dict) else {}


def score_worte(teile: dict) -> str:
    """Die Teile eines Scores in Worten – warum er so ausfällt. Leer, wenn es nichts zu sagen gibt.

    Beispiele:
      z_r 1,1 · z_e −0,4 · z_v 0,9 → „Wiedergabe über, Likes je View unter, Views über deinem Median“
      Vermerk „Basis zu klein“ → „Basis zu klein“ (dann sind alle z = 0 – ein Vergleich wäre nichtssagend)
      ohne r → „Likes je View unter, Views gleich deinem Median · ohne Wiedergabe“
    Weitere Vermerke aus publikum.score_fuer (z. B. „Engagement unvollständig“) stehen hinten, mit „·“ getrennt."""
    vermerke = [str(v) for v in teile.get("vermerke") or []]
    if BASIS_ZU_KLEIN in vermerke:
        return " · ".join([BASIS_ZU_KLEIN] + [v for v in vermerke if v != BASIS_ZU_KLEIN])
    vergleich = []
    for teil, name in TEIL_NAMEN.items():
        z = teile.get(f"z_{teil}")
        if isinstance(z, (int, float)) and not isinstance(z, bool):  # None = Teil nicht gerechnet (z. B. ohne r)
            vergleich.append(f"{name} {'über' if z > 0 else 'unter' if z < 0 else 'gleich'}")
    stuecke = [", ".join(vergleich) + " deinem Median"] if vergleich else []
    return " · ".join(stuecke + vermerke)


# --- Meldung nach dem Bewerten -------------------------------------------------------------

def meldung_nach_bewerten(con: sqlite3.Connection, ergebnis: dict, zeit: datetime | None = None) -> bool:
    """Nach `pipeline publikum bewerten`: sind neue Scores gesetzt, eine Lern-Meldung
    `publikum:bewertet:<datum>` („📊 2 Posts bewertet: #17 +0,8 · #18 −0,3 – /publikum“). Je Tag höchstens eine
    (ON CONFLICT (schluessel) DO NOTHING in db.lern_meldung); ohne neue Scores keine. True = neu angelegt.

    ergebnis: Rückgabe von publikum.bewerte_alle (gebraucht wird nur "posts": [{"id", "score"}]).
    Das Datum ist das UTC-Datum von `zeit` – der Timer läuft um 10:00 Ortszeit, da ist es derselbe Tag.
    Ist bei einem Post „Basis zu klein“ vermerkt (Score 0), erklärt eine zweite Zeile die 0 – sonst sähe sie aus
    wie ein schlechter Post. Verschickt wird die Meldung vom Lern-Bot, nicht in der Ruhezeit (faellige_lern_meldungen)."""
    posts = ergebnis.get("posts") or []
    if not posts:
        return False
    wort = "Post" if len(posts) == 1 else "Posts"
    liste = " · ".join(f"#{p['id']} {score_text(p['score'])}" for p in posts)
    text = f"📊 {len(posts)} {wort} bewertet: {liste} – /publikum"
    zeilen = [publikum.post(con, p["id"]) for p in posts]
    if any(z is not None and BASIS_ZU_KLEIN in (_score_teile(z).get("vermerke") or []) for z in zeilen):
        text += (f"\nℹ️ Score 0 = noch zu wenige bewertete Posts zum Vergleich – echte Scores ab "
                 f"{publikum.MINDEST_BASIS} bewerteten Posts.")
    return db.lern_meldung(con, f"publikum:bewertet:{(zeit or jetzt()):%Y-%m-%d}", text)


# --- /publikum ------------------------------------------------------------------------------

def claude_aufrufe_woche(con: sqlite3.Connection, konfig: Konfig, zeit: datetime | None = None) -> int:
    """Claude-Aufrufe seit Montag 00:00 Ortszeit ([zeit].zeitzone): Zeilen in `ereignisse` mit art 'claude'
    (geschrieben von claude_aufruf.protokolliere – je Screenshot einer). Beispiel: Mittwoch, 3 Screenshots seit
    Montag → 3. Die alten Aufrufe von `decide`/`stimmung` schreiben kein solches Ereignis und zählen nicht (A18).
    Die eine Stelle für „Claude diese Woche“ – /lernstand (Stufe 3) und der Wochenbericht (Stufe 5) nutzen sie."""
    lokal = utc_zu_lokal(zeit or jetzt(), konfig.wert("zeit.zeitzone", "Europe/Berlin"))
    # Montag dieser Woche, 00:00 Ortszeit (weekday(): Montag = 0); iso() rechnet danach nach UTC um – auch über
    # eine Zeitumstellung hinweg richtig, weil die Zone die Uhrzeit des Montags kennt
    montag = (lokal - timedelta(days=lokal.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return con.execute("SELECT COUNT(*) FROM ereignisse WHERE art = ? AND zeit >= ?",
                       (CLAUDE_EREIGNIS, iso(montag))).fetchone()[0]


def _zahlen_text(zeile: sqlite3.Row, messung: sqlite3.Row | None) -> str:
    """Die letzte Messung kurz: „👁 1 240 ❤️ 61 ⏱ 6,8 s (Tag 4)“; ohne Messung ein Hinweis, was zu tun ist."""
    if messung is None:
        return f"noch keine Zahlen – Screenshot mit #{zeile['id']} schicken"
    teile = []
    if messung["views"] is not None:
        teile.append(f"👁 {_anzahl(messung['views'])}")
    if messung["likes"] is not None:
        teile.append(f"❤️ {_anzahl(messung['likes'])}")
    if messung["wiedergabe_s"] is not None:
        teile.append(f"⏱ {publikum._zahl(messung['wiedergabe_s'])} s")
    if messung["voll_prozent"] is not None:
        teile.append(f"✅ {publikum._zahl(messung['voll_prozent'])} %")
    # Tag der Messung (Alter des Posts beim Messen) – daran siehst du, ob sie schon für den Score zählt (ab Tag 3)
    tag = math.floor(publikum._alter_tage(zeile["gepostet_utc"], messung["gemessen_utc"]))
    return " ".join(teile or ["Zahlen unbekannt"]) + f" (Tag {tag})"


def _score_zustand(con: sqlite3.Connection, zeile: sqlite3.Row, konfig: Konfig, zeit: datetime) -> str:
    """Score-Teil einer /publikum-Zeile – vier Fälle:
      bewertet                     → „Score +0,8 (Wiedergabe über, … deinem Median)“
      jünger als alter_tage        → „Score noch offen (ab 7 Tagen)“
      alt genug, passende Messung  → „Score kommt beim nächsten Lauf“ (der Timer war noch nicht dran)
      alt genug, keine Messung     → „Score offen (braucht eine Messung ab Tag 3 mit Views)“"""
    if zeile["bewertet_utc"] is not None:
        worte = score_worte(_score_teile(zeile))
        return f"Score {score_text(zeile['score'])}" + (f" ({worte})" if worte else "")
    if not publikum.ist_faellig(zeile, konfig, zeit):
        return f"Score noch offen (ab {publikum._zahl(publikum._einstellung(konfig, 'alter_tage'))} Tagen)"
    messungen = con.execute("SELECT * FROM publikum_messungen WHERE post_id = ? ORDER BY gemessen_utc, id",
                            (zeile["id"],)).fetchall()
    if publikum.waehle_messung(zeile, messungen, konfig) is not None:
        return "Score kommt beim nächsten Lauf"
    mindest = publikum._zahl(publikum._einstellung(konfig, "mindest_alter_tage"))
    return f"Score offen (braucht eine Messung ab Tag {mindest} mit Views)"


def publikum_text(con: sqlite3.Connection, konfig: Konfig, grenze: int = PUBLIKUM_STANDARD,
                  zeit: datetime | None = None) -> str:
    """Text für /publikum: die letzten `grenze` Posts, neueste zuerst, je Zeile z. B.
    „#17 TikTok · Entwurf 41 · 4 Tage · 👁 1 240 ❤️ 61 ⏱ 6,8 s (Tag 4) · Score noch offen (ab 7 Tagen)“ bzw.
    „#12 TikTok · Clip 88 · 9 Tage · … · Score +0,8 (Wiedergabe über, Likes je View unter deinem Median)“ bzw.
    „… · Score 0 (Basis zu klein)“. Erste Zeile: „📊 Publikum · 12 Posts, 5 mit Score (die letzten 10, neueste
    zuerst)“. Letzte Zeile: „🤖 Claude diese Woche: 3 Aufrufe“ (claude_aufrufe_woche). Ohne Posts: kurzer Satz,
    wie ein Post entsteht (📦 → /link). Nur lesen – der Text ändert nichts in der Datenbank.
    KonfigFehler, wenn [publikum].alter_tage bzw. mindest_alter_tage fehlt (cmd_publikum fängt das ab)."""
    zeitpunkt = zeit or jetzt()
    claude = claude_aufrufe_woche(con, konfig, zeitpunkt)
    fuss = f"🤖 Claude diese Woche: {claude} {'Aufruf' if claude == 1 else 'Aufrufe'}"
    # COUNT(bewertet_utc) zählt nur Zeilen, in denen die Spalte gesetzt ist – also die Posts mit Score
    anzahl, bewertet = con.execute("SELECT COUNT(*), COUNT(bewertet_utc) FROM posts").fetchone()
    if not anzahl:
        return ("📊 Noch keine Posts. So entsteht einer: 👍 auf einen Short → ✅ fertig → 📦 Upload-Paket → auf "
                "TikTok posten → /link <entwurf> <TikTok-Link>. Im Clip-Bot zählt das Häkchen TikTok bzw. /link dort.\n"
                + fuss)
    auswahl = "neueste zuerst" if anzahl <= grenze else f"die letzten {grenze}, neueste zuerst"
    zeilen = [f"📊 Publikum · {anzahl} Posts, {bewertet} mit Score ({auswahl})"]
    for zeile in con.execute("SELECT * FROM posts ORDER BY gepostet_utc DESC, id DESC LIMIT ?", (grenze,)).fetchall():
        ziel_id = zeile["clip_id"] if zeile["art"] == "clip" else zeile["entwurf_id"]
        zeilen.append(" · ".join([
            f"#{zeile['id']} {PLATTFORM_NAMEN.get(zeile['plattform'], zeile['plattform'])}",
            f"{ART_NAMEN[zeile['art']]} {ziel_id}",
            _tage(publikum._alter_tage(zeile["gepostet_utc"], zeitpunkt)),
            _zahlen_text(zeile, publikum.letzte_messung(con, zeile["id"])),
            _score_zustand(con, zeile, konfig, zeitpunkt),
        ]))
    zeilen.append(fuss)
    return "\n".join(zeilen)


async def cmd_publikum(update, context) -> None:
    """/publikum [anzahl] (Standard 10, höchstens 30 – mehr wird auf 30 gekürzt). Antwort: publikum_text, bei
    Überlänge in Stücken (lernbot.stuecke). Eine ungültige Anzahl (keine Zahl, 0, negativ, zwei Zahlen) →
    „Aufruf: /publikum oder /publikum 20“. Wirft nicht: Geht beim Lesen etwas schief, steht der Fehler im Log und
    du bekommst eine kurze Meldung mit der Fehlerart (nie mit Pfaden oder Inhalten)."""
    from . import lernbot  # hier, nicht oben: lernbot hängt dieses Modul in baue_app ein (sonst ein Import-Kreis)

    nachricht = update.effective_message
    argumente = context.args or []
    try:
        anzahl = int(argumente[0]) if argumente else PUBLIKUM_STANDARD
    except ValueError:
        anzahl = 0  # keine Zahl → wie eine ungültige Anzahl behandeln
    if len(argumente) > 1 or anzahl < 1:
        await nachricht.reply_text("Aufruf: /publikum oder /publikum 20")
        return
    try:
        text = publikum_text(context.bot_data["con"], context.bot_data["konfig"], min(anzahl, PUBLIKUM_MAX))
    except Exception as fehler:  # der Bot soll wegen einer Anzeige nicht stolpern – Details stehen im Log
        log.exception("/publikum")
        await nachricht.reply_text(f"⚠️ /publikum ging gerade nicht ({type(fehler).__name__}) – Details im Log.")
        return
    for stueck in lernbot.stuecke(text):
        await nachricht.reply_text(stueck)


def registriere(app, nur_ich) -> None:
    """Hängt /publikum in die Lern-Bot-App (aufgerufen von lernbot.baue_app)."""
    from telegram.ext import CommandHandler

    app.add_handler(CommandHandler("publikum", cmd_publikum, filters=nur_ich))
