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

Technik: Das Bild liegt nur in einem temporären Ordner (tempfile.mkdtemp, im Dienst PrivateTmp) und wird nach der
Auswertung gelöscht – auch bei jedem Fehler –, nie im Puffer (/srv/clips), dort wird nichts gelöscht (CLAUDE.md
„Nie löschen“, Annahme A21). Claude läuft per asyncio.to_thread ohne Datenbank; Prüfen und Speichern passiert im
Event-Loop mit bot_data["con"]. Jeder Claude-Aufruf wird gezählt (claude_aufruf.protokolliere). Nichts hier
weckt pve-big.

Zustand: bot_data["publikum"] hält höchstens EINEN offenen Vorgang (du bist der einzige Nutzer), als dict mit
  art      "bild" (wartet auf pl:-Wahl) | "rueckfrage" (wartet auf pm:) | "hand" (wartet auf Text)
  post_id  None, solange kein Post gewählt ist
  ordner   Temp-Ordner mit dem Bild (nur bei art "bild")
  werte    gelesene bzw. eingegebene Zahlen (bei "rueckfrage"), quelle, roh
  seit     Zeitpunkt (für aufraeumen)
Ein neues Foto oder ein neuer „#17 …“-Text ersetzt einen offenen Vorgang: dessen Bild wird sofort gelöscht, und
der Bot sagt „vorheriges Bild verworfen“. Ein Klick, der nicht zum offenen Vorgang passt (alte Nachricht,
Doppelklick), bekommt „Schon erledigt.“ und ändert nichts.

Secrets: Nach get_file() steht in File.file_path die Download-URL MIT dem Bot-Token
(https://api.telegram.org/file/bot<TOKEN>/…). file_path und URLs deshalb nie loggen, nie an dich schicken, nie in
eine Ausnahme-Nachricht packen; bei einem Download-Fehler nur den Ausnahmetyp loggen (`type(e).__name__`) und
dir „Download fehlgeschlagen“ sagen. Ebenso nie ins Log: Bilder, Chat-IDs, Claude-Rohantworten.

Zeit: `jetzt` ist auf Modulebene importiert – Tests ersetzen `lernbot_zahlen.jetzt` (mock.patch.object).

Vertrag Stufe 1 (Skelett): `registriere` ist schon echt (lernbot.baue_app ruft es), alles andere baut Paket (b).
"""

from __future__ import annotations

import logging
from datetime import datetime

from .zeit import iso, jetzt  # noqa: F401 – im Modul importiert, damit Tests `lernbot_zahlen.jetzt` ersetzen können

log = logging.getLogger("lern-bot")

# Callback-Präfixe dieses Moduls (≤ 64 Byte je Knopf): pl:<post_id>:        Post für ein wartendes Bild wählen
#                                                        pm:<post_id>:ok|hand  Zahlen bestätigen / von Hand eingeben
PRAEFIXE = ("pl", "pm")
KLICK_MUSTER = r"^(pl|pm):"
WARTEN_S = 600  # so lange wartet ein offener Vorgang (Bild ohne Post-Nummer, Rückfrage, Hand-Eingabe) – Spec §7.1

Knoepfe = list[list[tuple[str, str]]]


def post_nummer_aus_text(text: str | None) -> int | None:
    """„#17“ irgendwo in der Bildunterschrift → 17; sonst None. Beispiel: "Stand Tag 3 #17" → 17."""
    raise NotImplementedError


def lies_text_eingabe(text: str) -> tuple[int, dict] | None:
    """Hand-Eingabe ohne Bild: „#17 1240 61 6.8 34“ → (17, Werte wie publikum.lies_hand_eingabe). Fehlt die
    #Nummer am Anfang → None (dann ist es kein Messungs-Text). ValueError aus lies_hand_eingabe bei kaputten Zahlen."""
    raise NotImplementedError


def knoepfe_posts(posts: list) -> Knoepfe:
    """Ein Knopf je Post („#17 · Entwurf 41 · 26.09.“, `pl:17:`), untereinander."""
    raise NotImplementedError


def knoepfe_rueckfrage(post_id: int) -> Knoepfe:
    """„✅ Stimmt“ (`pm:<post_id>:ok`) und „✏️ Von Hand“ (`pm:<post_id>:hand`) in einer Reihe – für gelesene wie für
    von Hand eingegebene Zahlen. Beispiel: knoepfe_rueckfrage(17) == [[("✅ Stimmt", "pm:17:ok"),
    ("✏️ Von Hand", "pm:17:hand")]]."""
    raise NotImplementedError


def werte_text(werte: dict) -> str:
    """Gelesene Zahlen lesbar, z. B. „👁 1 240 · ❤️ 61 · 💬 3 · ↗️ 5 · 🔖 2 · ⏱ 6,8 s · ✅ 34 %“; None = „–“."""
    raise NotImplementedError


def endung_fuer(mime_type: str | None, dateiname: str | None) -> str | None:
    """Endung, unter der ein als Datei geschicktes Bild abgelegt wird: aus mime_type (screenshot.BILD_ENDUNGEN),
    sonst aus dem Dateinamen (.jpg/.jpeg/.png/.webp). None = Format, das claude nicht lesen kann (z. B. HEIC) –
    der Bot antwortet dann „Bitte als Foto schicken“. Beispiel: ("image/png", "x.png") → ".png"."""
    raise NotImplementedError


async def bei_foto(update, context) -> None:
    """Screenshot angekommen (Foto → .jpg, oder Bild als Datei → endung_fuer). Mit #Nummer: sofort auswerten;
    ohne: Post-Knöpfe. Ersetzt einen offenen Vorgang (altes Bild sofort löschen, Hinweis). Download-Fehler: nur
    den Ausnahmetyp loggen (file_path enthält den Bot-Token), dir „Download fehlgeschlagen“ sagen."""
    raise NotImplementedError


async def bei_text(update, context) -> None:
    """Freier Text: offene Hand-Eingabe (`1240 61 6.8 34`) oder ein ganzer Messungs-Text (`#17 1240 61 6.8 34`,
    lies_text_eingabe). Beides wird mit pruefe_plausibel geprüft; bei einem Verstoß Rückfrage mit pm:-Knöpfen.
    Sonst ein kurzer Hinweis auf /hilfe. Kaputte Zahlen → der Fehlertext aus lies_hand_eingabe, Vorgang bleibt offen."""
    raise NotImplementedError


async def bei_klick(update, context) -> None:
    """Knöpfe pl: und pm:. Prüft selbst, ob der Klick von dir kommt (CallbackQueryHandler kennt keinen Filter) –
    sonst „Nicht erlaubt.“. Passt der Klick nicht zum offenen Vorgang (andere post_id, schon gespeichert,
    Doppelklick) → „Schon erledigt.“, nichts passiert. pm:<post_id>:ok speichert die gezeigten Zahlen,
    pm:<post_id>:hand öffnet die Hand-Eingabe für diesen Post."""
    raise NotImplementedError


async def aufraeumen(app, zeit: datetime | None = None) -> int:
    """Offene Vorgänge älter als WARTEN_S verwerfen: Bild löschen, dir kurz sagen („⌛ Screenshot verworfen – bitte
    nochmal mit #Nummer“). Wird von lernbot._schleife regelmäßig aufgerufen (nur mit app). Rückgabe: Anzahl
    verworfener Vorgänge (0 oder 1). zeit=None → jetzt(); Tests setzen zeit oder ersetzen lernbot_zahlen.jetzt."""
    raise NotImplementedError


def registriere(app, nur_ich) -> None:
    """Hängt die Handler in die Lern-Bot-App (aufgerufen von lernbot.baue_app, VOR dessen allgemeinem
    Klick-Handler – sonst landete `pl:17:` in der Suche nach Entwurf 17)."""
    from telegram.ext import CallbackQueryHandler, MessageHandler, filters

    app.add_handler(MessageHandler(nur_ich & (filters.PHOTO | filters.Document.IMAGE), bei_foto))
    app.add_handler(MessageHandler(nur_ich & filters.TEXT & ~filters.COMMAND, bei_text))
    app.add_handler(CallbackQueryHandler(bei_klick, pattern=KLICK_MUSTER))
