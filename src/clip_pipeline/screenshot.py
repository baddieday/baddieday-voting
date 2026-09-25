"""Zahlen aus einem TikTok-Statistik-Screenshot lesen (Spec §7.1).

Die TikTok-App zeigt je Video Aufrufe, Likes, Kommentare, Shares, Speicherungen, die durchschnittliche
Wiedergabezeit und „vollständig angesehen“. Eine API für die Wiedergabezeit gibt es nicht – deshalb der
Screenshot. Claude liest das Bild (nur Leserecht, `claude_aufruf.frage_json`) nach einem festen Prompt
(`templates/screenshot-prompt.txt`) und antwortet mit JSON nach `schemas/publikum.schema.json`.

Claude kann sich verlesen. Deshalb gilt: Nichts wird ungeprüft gespeichert. Dieses Modul liefert nur die
gelesenen, auf Typen gebrachten Werte; ob sie zur letzten Messung passen, prüft `publikum.pruefe_plausibel`,
und bei einem Verstoß fragt der Lern-Bot dich („Stimmt das? ✅ / ✏️ von Hand“).

Das Bild bleibt nie liegen: Der Aufrufer löscht es nach der Auswertung (kein Bildarchiv, Spec §7.1). Dieses
Modul fasst keine Datenbank an und läuft im Thread.

Einstellungen in [lernbot]: screenshot_claude (an/aus), screenshot_prompt (Vorlage), screenshot_timeout_s. Ihre
Standardwerte stehen nur in config/pipeline.toml; fehlt einer, ist das ein KonfigFehler (`einstellung`).
"""

from __future__ import annotations

import logging
import math
import shutil
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

from . import claude_aufruf, publikum
from .claude_aufruf import ClaudeAntwort
from .konfig import Konfig, KonfigFehler

log = logging.getLogger("pipeline")

# So heißt das Bild im Arbeitsordner von Claude (plus Endung); der Prompt nennt genau diesen Namen ({bild}).
BILD_STAMM = "screenshot"
# Bildformate, die claude lesen kann, nach Telegram-mime_type. Die Endung muss zum Inhalt passen – das Read-Tool
# erkennt den Bildtyp an der Endung; ein PNG namens .jpg kann scheitern. Fotos schickt Telegram immer als JPEG;
# als Datei geschickte Bilder (PNG vom PC, HEIC vom iPhone) prüft der Lern-Bot vorher (HEIC: „als Foto schicken“).
BILD_ENDUNGEN = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
# So heißen die Felder in Fehlermeldungen (Zähler wie in publikum, dazu die beiden Zeit-/Anteilswerte)
FELD_NAMEN = {**publikum.ZAEHLER_NAMEN, "wiedergabe_s": "Ø Wiedergabe", "voll_prozent": "Vollständig angesehen"}
# Unbekannte Feldnamen von Claude werden gekürzt geloggt – ein Name ist kurz, alles Längere wäre schon Inhalt
NAME_MAX_ZEICHEN = 40


@dataclass
class Lesung:
    werte: dict | None   # {views, likes, kommentare, shares, saves, wiedergabe_s, voll_prozent} – je Feld Zahl oder None
    hinweis: str | None  # warum es keine Werte gibt (claude fehlt, kaputtes JSON, Schema …), sonst None
    roh: str | None      # Claude-JSON als Text – wird mit der Messung gespeichert (publikum_messungen.roh)
    # Die Antwort von claude, wenn claude gefragt wurde – zum Mitzählen (claude_aufruf.protokolliere); None = kein
    # Aufruf (Format unlesbar, nicht eingerichtet). Scheitert erst normalisiere, steht der Grund auch hier.
    claude: ClaudeAntwort | None = None


def einstellung(konfig: Konfig, name: str):
    """Ein Wert aus [lernbot] (screenshot_claude, screenshot_prompt, screenshot_timeout_s). Die Standardwerte stehen
    nur in config/pipeline.toml; fehlt der Schlüssel, ist die Konfiguration kaputt → KonfigFehler mit dem Namen.
    Beispiel: einstellung(konfig, "screenshot_timeout_s") == 120."""
    abschnitt = konfig.abschnitt("lernbot")
    if name not in abschnitt:
        raise KonfigFehler(f"[lernbot].{name} fehlt in der Konfiguration (Standard steht in config/pipeline.toml)")
    return abschnitt[name]


def prompt(konfig: Konfig, bild_name: str) -> str:
    """Der feste Auftrag aus [lernbot].screenshot_prompt (Standard templates/screenshot-prompt.txt, über
    konfig.projektpfad), der Platzhalter {bild} ersetzt durch bild_name, z. B. "screenshot.png". Ersetzt wird mit
    str.replace, nicht mit format – die Vorlage enthält ein JSON-Beispiel mit geschweiften Klammern.
    FileNotFoundError, wenn die Datei fehlt."""
    vorlage = konfig.projektpfad(str(einstellung(konfig, "screenshot_prompt"))).read_text(encoding="utf-8")
    return vorlage.replace("{bild}", bild_name)


def normalisiere(antwort: dict) -> dict:
    """Bringt Claudes (schon schema-geprüfte) Antwort in die Form einer Messung: alle Felder aus publikum.FELDER,
    Zähler als int (1240.0 → 1240), wiedergabe_s und voll_prozent als float, fehlende Felder als None.
    ValueError bei Unsinn: Zähler mit Nachkommastellen (12.5 Views), NaN oder Unendlich (json.loads nimmt „NaN“
    an, das Schema lässt es als number durch – math.isfinite prüft hier). Negative Werte lehnt schon das Schema ab.
    Unbekannte Schlüssel (z. B. "profilaufrufe", wenn TikTok mehr anzeigt) werden ignoriert und nur mit ihrem
    Namen geloggt – das Schema ist tolerant (Spec §15). Ob die Werte zur letzten Messung passen (z. B. voll_prozent
    über 100), prüft nicht dieses Modul, sondern publikum.pruefe_plausibel – mit Rückfrage statt Ablehnung.

    Beispiel: {"views": 1240.0, "likes": 61, "wiedergabe_s": 7} → {"views": 1240, "likes": 61, "kommentare": None,
    "shares": None, "saves": None, "wiedergabe_s": 7.0, "voll_prozent": None}."""
    werte: dict = {}
    for feld in publikum.FELDER:
        wert = antwort.get(feld)
        if wert is None:  # nicht auf dem Bild – unbekannt, nicht 0
            werte[feld] = None
            continue
        # Regel 1: nur endliche Zahlen – „NaN“ oder „Infinity“ sind Lesefehler, keine Statistik
        if not math.isfinite(wert):
            raise ValueError(f"{FELD_NAMEN[feld]}: {wert} ist keine endliche Zahl")
        if feld in publikum.ZAEHLER:
            # Regel 2: Zähler sind ganze Zahlen – 12,5 Views gibt es nicht (eher „12,5K“ falsch abgeschrieben)
            if not float(wert).is_integer():
                raise ValueError(f"{FELD_NAMEN[feld]}: {wert} ist keine ganze Zahl")
            werte[feld] = int(wert)
        else:
            werte[feld] = float(wert)
    unbekannt = sorted(str(name)[:NAME_MAX_ZEICHEN] for name in antwort if name not in publikum.FELDER)
    if unbekannt:  # nur die Namen – die Werte sind gelesene Zahlen und gehören nicht ins Log
        log.info("Screenshot: unbekannte Felder ignoriert: %s", ", ".join(unbekannt))
    return werte


def lies_zahlen(konfig: Konfig, bild: Path) -> Lesung:
    """Liest die Zahlen aus einem Screenshot. Kopiert das Bild als BILD_STAMM + bild.suffix (z. B.
    "screenshot.png") in einen eigenen temporären Arbeitsordner (Claude sieht nur diesen), fragt
    claude_aufruf.frage_json(prompt(konfig, name), schema_name="publikum", timeout_s=[lernbot].screenshot_timeout_s)
    und normalisiert. Endung nicht in BILD_ENDUNGEN → Lesung(None, "Bildformat … kann ich nicht lesen", None) ohne
    Aufruf. Der temporäre Ordner wird immer entfernt; das Original `bild` löscht der Aufrufer.
    Wirft nie – Fehler stehen in Lesung.hinweis (auch ValueError aus normalisiere).

    Beispiel: lies_zahlen(konfig, Path("/tmp/x/eingang.png")) → Claude sieht nur „screenshot.png“ →
    Lesung({"views": 1240, …}, None, '{"views": 1240, …}', claude=ClaudeAntwort(…))."""
    endung = bild.suffix.lower()
    if endung not in BILD_ENDUNGEN.values():
        return Lesung(None, f"Bildformat {endung or '(ohne Endung)'} kann ich nicht lesen", None)
    bild_name = BILD_STAMM + endung
    try:  # Vorlage und Zeitlimit vor dem Aufruf – fehlt etwas, wird claude gar nicht erst gefragt
        auftrag = prompt(konfig, bild_name)
        timeout_s = float(einstellung(konfig, "screenshot_timeout_s"))
    except (OSError, KonfigFehler, ValueError) as e:
        return Lesung(None, f"Screenshot-Lesen nicht eingerichtet: {e}", None)

    try:
        arbeitsordner = Path(tempfile.mkdtemp(prefix="clip-claude-"))
    except OSError as e:  # z. B. /tmp voll
        return Lesung(None, f"Kein Temp-Ordner für die Auswertung ({type(e).__name__})", None)
    try:
        try:
            shutil.copyfile(bild, arbeitsordner / bild_name)
        except OSError as e:
            return Lesung(None, f"Bild nicht lesbar ({type(e).__name__})", None)
        antwort = claude_aufruf.frage_json(konfig, auftrag, arbeitsordner, schema_name="publikum",
                                           timeout_s=timeout_s)
    finally:  # immer weg – auch bei Timeout oder Fehler (kein Bildarchiv, Spec §7.1)
        shutil.rmtree(arbeitsordner, ignore_errors=True)

    if antwort.daten is None:
        return Lesung(None, antwort.hinweis, antwort.roh, claude=antwort)
    try:
        werte = normalisiere(antwort.daten)
    except ValueError as e:
        hinweis = f"Zahlen unbrauchbar: {e}"
        return Lesung(None, hinweis, antwort.roh, claude=replace(antwort, daten=None, hinweis=hinweis))
    return Lesung(werte, None, antwort.roh, claude=antwort)
