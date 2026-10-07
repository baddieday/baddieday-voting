"""Szenen (07.10., Florian: „Ich tippe 🥱 und sehe das gleiche Video mit anderen Schnitten“): Wann zeigen zwei Momente
dieselbe Spielszene?

Dieselbe Szene kann unter mehreren Schlüsseln liegen – Clip der Pipeline (clip:…), Nvidia-Highlight oder
SteelSeries-Moment (datei:…). Szene = Spielzeit-Fenster [start_utc, start_utc + (ende_s − start_s)] aus der Tabelle
momente (start_utc ist der Zeitpunkt von start_s). Zwei Momente zeigen dieselbe Szene, wenn sich ihre Fenster um
mindestens 3 s oder um mindestens 40 % des kürzeren überlappen. Nicht transitiv: nur direkte Nachbarn, sonst entstünden
Ketten über einen ganzen Abend.

Ausnahmen: ohne start_utc zählt nur der gleiche Schlüssel. Zwei Clips sind nie dieselbe Szene – die Pipeline legt je
Kill-Serie genau einen Clip an (nur der Puffer überlappt, bis 4,5 s, vorbewertung.anlauf_start), und zwei verschiedene
Matches können sich nicht überlappen (CI 07.10.: Testclips mit gleicher Uhrzeit schrumpften sonst einen Short auf eine
Szene). Doppelt sind also Aufnahmen ohne Clip (datei:…) gegenüber Clips oder einander.
Fail-Momente (nur 🔥 Viral) bleiben draußen, damit Viral unverändert bleibt.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable

from .zeit import aus_iso

UEBERLAPP_S = 3.0
UEBERLAPP_ANTEIL = 0.4

_LISTEN: dict[str, frozenset[str]] = {}   # Schnittliste -> Momente (Listen werden nur einmal geschrieben)


def index(con: sqlite3.Connection) -> dict[str, set[str]]:
    """Schlüssel -> Schlüssel derselben Szene (ohne sich selbst). Nur Momente mit Nachbarn stehen drin."""
    fenster = []
    for z in con.execute("SELECT schluessel, match_id, start_utc, start_s, ende_s FROM momente "
                         "WHERE start_utc IS NOT NULL AND schluessel NOT LIKE 'fail:%'"):
        try:
            t0 = aus_iso(z["start_utc"]).timestamp()
            laenge = max(0.0, float(z["ende_s"]) - float(z["start_s"] or 0.0))
        except (TypeError, ValueError):
            continue
        fenster.append((t0, t0 + laenge, z["schluessel"], z["match_id"]))
    fenster.sort()
    nachbarn: dict[str, set[str]] = {}
    for i, (a0, a1, a, a_match) in enumerate(fenster):
        for j in range(i + 1, len(fenster)):   # über den Index – eine Kopie der Restliste je Runde wäre quadratisch
            b0, b1, b, b_match = fenster[j]
            if b0 >= a1:
                break   # nach Beginn sortiert: alle weiteren beginnen noch später
            if a.startswith("clip:") and b.startswith("clip:"):
                continue   # zwei Clips: je Kill-Serie einer, und verschiedene Matches überlappen nie (sonst ein
                           # Uhrzeit-Fehler) – doppelt sind Aufnahmen ohne Clip (datei:…, Nvidia/SteelSeries)
            ueberlapp = min(a1, b1) - b0
            kuerzer = min(a1 - a0, b1 - b0)
            if ueberlapp >= UEBERLAPP_S or (kuerzer > 0 and ueberlapp >= UEBERLAPP_ANTEIL * kuerzer):
                nachbarn.setdefault(a, set()).add(b)
                nachbarn.setdefault(b, set()).add(a)
    return nachbarn


def erweitert(schluessel: Iterable[str], idx: dict[str, set[str]]) -> set[str]:
    """Die Schlüssel samt allen Schlüsseln derselben Szene."""
    ergebnis: set[str] = set()
    for s in schluessel:
        ergebnis |= {s, *idx.get(s, ())}
    return ergebnis


def eine_je_szene(kandidaten: list, idx: dict[str, set[str]], bevorzugt: Iterable[str] = ()) -> list:
    """Je Szene ein Kandidat (regie.Kandidat): zuerst die bevorzugten (🥱: die behaltenen Szenen), dann starke, dann
    die mit Clip, dann die mit den meisten Punkten. Fail-Momente bleiben unberührt; die Reihenfolge bleibt."""
    bevorzugt = set(bevorzugt)
    gewaehlt: set[str] = set()
    bleibt: set[int] = set()
    # Starke Szenen und Clips (Kill-Daten aus dem Replay) vor den Punkten: sonst gewänne ein lauter Datei-Zwilling
    # ohne Kills, flöge danach bei „nur starke Szenen“ raus – und die Szene fehlte ganz (Prüfung 07.10.)
    for k in sorted(kandidaten, key=lambda k: (k.schluessel not in bevorzugt, not getattr(k, "stark", False),
                                               k.clip_id is None, -k.punkte, k.schluessel)):
        if not k.fail and idx.get(k.schluessel, set()) & gewaehlt:
            continue
        gewaehlt.add(k.schluessel)
        bleibt.add(id(k))
    return [k for k in kandidaten if id(k) in bleibt]


def momente_der_liste(pfad: str | None) -> frozenset[str]:
    """Momente einer gespeicherten Schnittliste (leer, wenn die Datei fehlt) – je Pfad einmal gelesen."""
    if not pfad:
        return frozenset()
    if pfad not in _LISTEN:
        try:
            with open(pfad, encoding="utf-8") as f:
                segmente = json.load(f).get("segmente") or []
        except (OSError, ValueError, AttributeError, TypeError):
            return frozenset()   # nicht merken: fehlt sie nur kurz, zählt sie beim nächsten Mal
        _LISTEN[pfad] = frozenset(s["moment"] for s in segmente if isinstance(s, dict) and s.get("moment"))
    return _LISTEN[pfad]


def jemals_gezeigt(con: sqlite3.Connection) -> set[str]:
    """Momente, die du schon in einem Video gesehen hast – nur gesendete Entwürfe (still aussortierte oder nie fertig
    gerenderte hast du nie gesehen)."""
    gezeigt: set[str] = set()
    for z in con.execute("SELECT schnittliste FROM entwuerfe WHERE auto_verworfen IS NULL "
                         "AND status IN ('gesendet', 'bewertet')"):
        gezeigt |= momente_der_liste(z["schnittliste"])
    return gezeigt
