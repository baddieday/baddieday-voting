"""Caption = Beschreibung (nur aus Fakten) + Hashtag-Vorlage + Werbung für clip-battle.de."""

from __future__ import annotations

import json
import re
import sqlite3
import string
import subprocess
import tomllib
from datetime import datetime

from .vorbewertung import gruppiere
from .zeit import aus_iso

KILLTYP_TAG = {"einzel": "elimination", "double": "doublekill", "triple": "triplekill", "multi": "multikill"}


class CaptionFehler(RuntimeError):
    pass


def fakten(clip: sqlite3.Row | dict, match: sqlite3.Row | dict | None, fenster_s: float = 10.0) -> dict:
    """Alles, was die Beschreibung verwenden darf – nichts anderes."""
    zeiten: list[datetime] = [aus_iso(z) for z in json.loads(clip["kill_zeiten"])]
    groesste = max(gruppiere(zeiten, fenster_s), key=len)  # die Serie, nach der der Clip benannt ist
    spanne = (groesste[-1] - groesste[0]).total_seconds()
    return {
        "kills": int(clip["max_gruppe"]),
        "typ": clip["typ"],
        "sekunden": max(1, round(spanne)),
        "victory_royale": bool(clip["victory_royale"]),
        "platzierung": (match["platzierung"] if match else None),
        "kills_match": (match["kills"] if match else None),
    }


def fuelle(vorlage: str, werte: dict) -> str:
    """Wie str.format, aber ein unbekannter oder leerer Platzhalter ist ein Fehler statt einer Lücke."""
    for _, feld, _, _ in string.Formatter().parse(vorlage):
        if feld is None:
            continue
        if feld not in werte or werte[feld] in (None, ""):
            raise CaptionFehler(f"Platzhalter {{{feld}}} hat keinen Wert")
    return vorlage.format(**werte)


def lade_beschreibungen(pfad) -> dict[str, list[str]]:
    return tomllib.loads(pfad.read_text(encoding="utf-8"))


def beschreibung_aus_vorlage(f: dict, bausteine: dict[str, list[str]], clip_id: int) -> str:
    schluessel = "victory_royale" if f["victory_royale"] else f["typ"]
    varianten = bausteine.get(schluessel) or bausteine.get(f["typ"]) or ["Fortnite-Highlight"]
    # Variante hängt von der Clip-Nummer ab: reproduzierbar, trotzdem abwechslungsreich
    for versatz in range(len(varianten)):
        try:
            return fuelle(varianten[(clip_id + versatz) % len(varianten)], f)
        except CaptionFehler:
            continue  # z. B. {platzierung} unbekannt -> nächste Variante probieren
    return "Fortnite-Highlight"


def _zahlen(text: str) -> set[str]:
    return set(re.findall(r"\d+", text))


def pruefe_ki_text(text: str, f: dict, max_laenge: int) -> bool:
    """Die KI darf umformulieren, aber keine Zahl erfinden."""
    if not text or len(text) > max_laenge:
        return False
    erlaubt = {str(v) for v in f.values() if isinstance(v, int) and not isinstance(v, bool)}
    return _zahlen(text) <= erlaubt


def ki_beschreibung(f: dict, *, timeout_s: float, max_laenge: int) -> str | None:
    """Fragt `claude -p` (Max-Abo, kein API-Key) nach einer Umformulierung. None bei jedem Problem."""
    auftrag = (
        "Formuliere eine kurze, packende deutsche Beschreibung (max. "
        f"{max_laenge} Zeichen, 1 Emoji erlaubt) für einen Fortnite-Clip. Verwende AUSSCHLIESSLICH diese Fakten, "
        "erfinde nichts (keine Waffen, Orte, Gegner, Zahlen): "
        + json.dumps(f, ensure_ascii=False)
        + ' Antworte nur mit JSON: {"beschreibung": "..."}'
    )
    try:
        ergebnis = subprocess.run(
            ["claude", "-p", "--output-format", "json", "--allowedTools", "Read", auftrag],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout_s, check=False,
        )
        if ergebnis.returncode != 0:
            return None
        antwort = json.loads(ergebnis.stdout).get("result", "")
        treffer = re.search(r"\{.*\}", antwort, re.DOTALL)
        text = json.loads(treffer.group(0)).get("beschreibung", "").strip() if treffer else ""
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, AttributeError):
        return None
    return text if pruefe_ki_text(text, f, max_laenge) else None


def baue(clip: sqlite3.Row | dict, match: sqlite3.Row | dict | None, konfig) -> str:
    f = fakten(clip, match, float(konfig.wert("vorbewertung.multikill_fenster_s", 10.0)))
    # Beschreibung aus decide (claude -p, dort schon gegen die Fakten geprüft) hat Vorrang
    beschreibung = (clip["beschreibung"] if "beschreibung" in clip.keys() else None) or None
    if not beschreibung and konfig.wert("caption.ki", False):
        beschreibung = ki_beschreibung(
            f, timeout_s=float(konfig.wert("caption.ki_timeout_s", 90)), max_laenge=int(konfig.wert("caption.max_laenge", 150))
        )
    if not beschreibung:
        bausteine = lade_beschreibungen(konfig.projektpfad(konfig.wert("caption.beschreibungen")))
        beschreibung = beschreibung_aus_vorlage(f, bausteine, int(clip["id"]))
    vorlage = konfig.projektpfad(konfig.wert("caption.vorlage")).read_text(encoding="utf-8")
    killtyp = "victoryroyale" if f["victory_royale"] else KILLTYP_TAG.get(f["typ"], "fortnite")
    return fuelle(vorlage, {"beschreibung": beschreibung, "killtyp": killtyp}).strip()
