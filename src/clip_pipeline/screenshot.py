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

Vertrag Stufe 1 (Skelett): Paket (b) baut die Rümpfe.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .konfig import Konfig

# So heißt das Bild im Arbeitsordner von Claude (plus Endung); der Prompt nennt genau diesen Namen ({bild}).
BILD_STAMM = "screenshot"
# Bildformate, die claude lesen kann, nach Telegram-mime_type. Die Endung muss zum Inhalt passen – das Read-Tool
# erkennt den Bildtyp an der Endung; ein PNG namens .jpg kann scheitern. Fotos schickt Telegram immer als JPEG;
# als Datei geschickte Bilder (PNG vom PC, HEIC vom iPhone) prüft der Lern-Bot vorher (HEIC: „als Foto schicken“).
BILD_ENDUNGEN = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


@dataclass
class Lesung:
    werte: dict | None   # {views, likes, kommentare, shares, saves, wiedergabe_s, voll_prozent} – je Feld Zahl oder None
    hinweis: str | None  # warum es keine Werte gibt (claude fehlt, kaputtes JSON, Schema …), sonst None
    roh: str | None      # Claude-JSON als Text – wird mit der Messung gespeichert (publikum_messungen.roh)


def prompt(konfig: Konfig, bild_name: str) -> str:
    """Der feste Auftrag aus [lernbot].screenshot_prompt (Standard templates/screenshot-prompt.txt, über
    konfig.projektpfad), der Platzhalter {bild} ersetzt durch bild_name, z. B. "screenshot.png". Ersetzt wird mit
    str.replace, nicht mit format – die Vorlage enthält ein JSON-Beispiel mit geschweiften Klammern.
    FileNotFoundError, wenn die Datei fehlt."""
    raise NotImplementedError


def normalisiere(antwort: dict) -> dict:
    """Bringt Claudes (schon schema-geprüfte) Antwort in die Form einer Messung: alle Felder aus publikum.FELDER,
    Zähler als int (1240.0 → 1240), wiedergabe_s und voll_prozent als float, fehlende Felder als None.
    ValueError bei Unsinn: Zähler mit Nachkommastellen (12.5 Views), NaN oder Unendlich (json.loads nimmt „NaN“
    an, das Schema lässt es als number durch – math.isfinite prüft hier). Negative Werte lehnt schon das Schema ab.
    Unbekannte Schlüssel (z. B. "profilaufrufe", wenn TikTok mehr anzeigt) werden ignoriert und nur mit ihrem
    Namen geloggt – das Schema ist tolerant (Spec §15). Ob die Werte zur letzten Messung passen (z. B. voll_prozent
    über 100), prüft nicht dieses Modul, sondern publikum.pruefe_plausibel – mit Rückfrage statt Ablehnung."""
    raise NotImplementedError


def lies_zahlen(konfig: Konfig, bild: Path) -> Lesung:
    """Liest die Zahlen aus einem Screenshot. Kopiert das Bild als BILD_STAMM + bild.suffix (z. B.
    "screenshot.png") in einen eigenen temporären Arbeitsordner (Claude sieht nur diesen), fragt
    claude_aufruf.frage_json(prompt(konfig, name), schema_name="publikum", timeout_s=[lernbot].screenshot_timeout_s)
    und normalisiert. Endung nicht in BILD_ENDUNGEN → Lesung(None, "Bildformat … kann ich nicht lesen", None) ohne
    Aufruf. Der temporäre Ordner wird immer entfernt; das Original `bild` löscht der Aufrufer.
    Wirft nie – Fehler stehen in Lesung.hinweis (auch ValueError aus normalisiere)."""
    raise NotImplementedError
