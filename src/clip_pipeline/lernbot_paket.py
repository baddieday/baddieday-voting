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

Regeln (Annahmen A5, A26, A27 im Plan):
  - Nur 👍-Entwürfe im Format "short" bekommen Paket, Häkchen und Post („kein Short ohne deine Freigabe“; ein
    Zusammenschnitt 16:9 liefert kein Publikumssignal, Spec §9.1). Andere → kurzer Hinweis, kein Post.
  - Die Checkliste nennt nur die Post-Plattformen ([publikum].plattformen); clip-battle.de bekommt für Entwürfe
    (noch) keinen Punkt – Rückfrage an Florian.
  - Der Stand je Plattform liegt in `posts` (veroeffentlichungen bleibt Clip-Sache), nachgesehen mit
    publikum.post_zu – kein eigenes SQL gegen posts.
  - Links erkennt bot.aktionen.plattform_aus_url (nur https, bekannte Domains) – dieselbe Regel wie im Clip-Bot.
  - Kein Weckversuch: Fehlen die Moment-Dateien (der Puffer hält Rohvideos 14 Tage) oder läuft der Mini nicht im
    getrennten Betrieb, sagt der Bot das klar – anders als das alte Clip-Bot-/paket weckt dieser Weg pve-big nie
    (Spec §12).

Export-Vertrag mit Regisseur 2.0 (Plan Stufe 1): Es gibt genau EINEN 📦-Knopf im Lern-Bot, `pk:<eid>:` aus diesem
Modul. Die Upload-Fassung liegt in <wurzel>/<[publikum].upload_ordner>/<name>/ (entwurf.upload_ziel), der Pfad in
entwuerfe.upload_pfad. Regisseur 2.0 Stufe 3 erweitert baue_paket und entwurf.upload_fassung (Einzelclips,
Sicherung ins Lager) und baut keinen zweiten Weg daneben.

Rendern läuft per asyncio.to_thread mit eigener DB-Verbindung (SQLite-Verbindungen wandern nicht zwischen
Threads, E9), unter sperre(konfig.datenbank.with_suffix(".lock"), warten_s=[sperre].warten_s) und höchstens
einmal gleichzeitig (bot_data["paket_arbeitet"]). Nie ins Log: Datei-URLs von Telegram (Token), Chat-IDs.

Zeit: `jetzt` ist auf Modulebene importiert – Tests ersetzen `lernbot_paket.jetzt` (mock.patch.object).

Vertrag Stufe 1 (Skelett): `registriere` und `knoepfe_nach_fertig` sind schon echt (lernbot.py ruft sie), alles
andere baut Paket (c).
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime

from . import publikum
from .bot import aktionen
from .konfig import Konfig
from .zeit import iso, jetzt  # noqa: F401 – im Modul importiert, damit Tests `lernbot_paket.jetzt` ersetzen können

log = logging.getLogger("lern-bot")

# Callback-Präfixe dieses Moduls: pk:<eid>:  Upload-Paket bauen · pt:<eid>:t|y  TikTok/YouTube erledigt
PRAEFIXE = ("pk", "pt")
KLICK_MUSTER = r"^(pk|pt):"
# Kürzel wie im Clip-Bot (bot.aktionen.PLATTFORM_KUERZEL), aber nur Plattformen, für die es Posts gibt – also
# ohne clip-battle.de ("c"). Abgeleitet statt abgeschrieben: {"y": "youtube", "t": "tiktok"}.
PLATTFORM_KUERZEL = {k: p for k, p in aktionen.PLATTFORM_KUERZEL.items() if p in publikum.PLATTFORMEN}
NUR_FORMAT = "short"  # Paket, Häkchen und Post nur für Shorts (Annahme A26)

Knoepfe = list[list[tuple[str, str]]]


def knoepfe_nach_fertig(eid: int, bewertung: sqlite3.Row | None, fmt: str) -> Knoepfe | None:
    """Knöpfe unter einem fertig bewerteten Entwurf: bei 👍 auf einen Short „📦 Upload-Paket“, sonst keine (None).

    Beispiel: knoepfe_nach_fertig(41, {"daumen": 1, …}, "short") == [[("📦 Upload-Paket", "pk:41:")]];
    knoepfe_nach_fertig(42, {"daumen": 1, …}, "zusammenschnitt") is None."""
    if fmt != NUR_FORMAT or bewertung is None or int(bewertung["daumen"]) <= 0:
        return None
    return [[("📦 Upload-Paket", f"pk:{eid}:")]]


def lies_link_argumente(args: list[str]) -> tuple[int, str]:
    """`/link 41 <url>` oder `/link e41 <url>` → (41, url). ValueError mit Aufruf-Hilfe bei allem anderen
    („Aufruf: /link 41 https://www.tiktok.com/@…/video/…“)."""
    raise NotImplementedError


def paket_erlaubt(con: sqlite3.Connection, entwurf_id: int) -> str | None:
    """Die fachliche Prüfung für Paket, Häkchen und /link: None = erlaubt, sonst der Grund als Satz für dich.
    Regeln: Entwurf existiert („Entwurf #41 gibt es nicht“) · Format short („Upload-Paket gibt es nur für
    Shorts“) · mit 👍 bewertet („Entwurf #41 ist nicht mit 👍 bewertet“)."""
    raise NotImplementedError


def checkliste_stand(con: sqlite3.Connection, konfig: Konfig, entwurf_id: int) -> dict[str, bool]:
    """{plattform: erledigt?} für publikum.post_plattformen(konfig) – erledigt = publikum.post_zu findet einen Post.
    Beispiel: {"tiktok": False} vor dem Häkchen, {"tiktok": True} danach."""
    raise NotImplementedError


def knoepfe_checkliste(entwurf_id: int, stand: dict[str, bool]) -> Knoepfe:
    """Je offene Plattform ein Knopf „✅ TikTok erledigt“ (`pt:<eid>:t`); erledigte fallen weg, alle erledigt → []."""
    raise NotImplementedError


def haken_setzen(con: sqlite3.Connection, konfig: Konfig, entwurf_id: int, plattform: str,
                 zeit: datetime | None = None) -> tuple[str, int | None]:
    """Häkchen: legt den Post an (ohne Link), in einer Transaktion (db.transaktion). Rückgabe (Antworttext,
    post_id oder None), z. B. („TikTok ✅ – Post #17. Link später mit /link 41 …“, 17). Doppelklick → derselbe
    Post, Text „schon erledigt (Post #17)“. paket_erlaubt verneint → (Grund, None). Plattform außerhalb
    [publikum].plattformen → Hinweis, kein Post."""
    raise NotImplementedError


def link_speichern(con: sqlite3.Connection, konfig: Konfig, entwurf_id: int, url: str,
                   zeit: datetime | None = None) -> tuple[str, int | None]:
    """/link im Lern-Bot: Plattform mit aktionen.plattform_aus_url erkennen, dann in EINER Transaktion
    publikum.post_anlegen (legt an oder findet den vorhandenen) und publikum.link_nachtragen (ersetzt einen
    falschen Link). Rückgabe (Antworttext mit Post-Nummer, post_id oder None), z. B. („🔗 Post #17 (TikTok) –
    für Screenshots: Bildunterschrift #17“, 17).
    Kein Post bei: unbekanntem Link (nicht https oder fremde Domain), clip-battle.de („für Entwürfe kein Post“),
    Plattform außerhalb [publikum].plattformen, paket_erlaubt verneint – jeweils (Hinweis, None)."""
    raise NotImplementedError


def baue_paket(konfig: Konfig, entwurf_id: int) -> dict:
    """Läuft im Thread: eigene DB-Verbindung (db.verbinde), Sperre holen, Upload-Fassung rendern
    (entwurf.upload_fassung, idempotent) und Caption bauen (caption.entwurf_caption(con, liste, konfig)).
    Rückgabe {"datei": Pfad, "caption": Text, "dateiname": "clip-battle_e41.mp4"}.
    MedienFehler/KonfigFehler/sperre.Gesperrt gehen an den Aufrufer (sende_paket sagt es dir im Bot). Weckt nie."""
    raise NotImplementedError


async def sende_paket(app, entwurf_id: int) -> None:
    """Paket bauen (Thread) und schicken: Datei per send_document, Caption als <pre> (zum Kopieren), Checkliste
    mit Knöpfen. Fehler: dir kurz den Grund („⚠️ Upload-Paket: Moment-Datei fehlt …“, ohne Pfade mit Token o. ä.),
    Details ins Log. bot_data["paket_arbeitet"] wird immer zurückgesetzt."""
    raise NotImplementedError


async def bei_klick(update, context) -> None:
    """Knöpfe pk: und pt:. Prüft selbst, ob der Klick von dir kommt – sonst „Nicht erlaubt.“. Doppelklick auf pk:
    während des Renderns → „⏳ Paket wird schon gebaut“. pt: → haken_setzen, Knöpfe mit knoepfe_checkliste neu."""
    raise NotImplementedError


async def cmd_link(update, context) -> None:
    """/link <entwurf-nr> <url> (auch e<nr>). Antwort: der Text aus link_speichern (mit Post-Nummer) bzw. die
    Aufruf-Hilfe aus lies_link_argumente. Wirft nicht – Fehler landen als kurzer Satz bei dir, Details im Log."""
    raise NotImplementedError


def registriere(app, nur_ich) -> None:
    """Hängt die Handler in die Lern-Bot-App (aufgerufen von lernbot.baue_app, vor dessen Klick-Handler)."""
    from telegram.ext import CallbackQueryHandler, CommandHandler

    app.add_handler(CommandHandler("link", cmd_link, filters=nur_ich))
    app.add_handler(CallbackQueryHandler(bei_klick, pattern=KLICK_MUSTER))
