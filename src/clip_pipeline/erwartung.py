"""Erwartung: Wie wahrscheinlich gibst du diesen Clip bzw. Entwurf frei? (Spec §10.5)

Beim Senden (Clip-Bot und Lern-Bot) wird eine Wahrscheinlichkeit berechnet und festgeschrieben (Tabelle
erwartungen, eine Zeile je Clip/Entwurf, nie überschrieben). Später zeigt der Vergleich mit deinem Urteil, ob das
Modell lernt: „Erwartung getroffen: Clips 14/20 (70 %)“.

Modell in Alltagssprache: Die logistische Funktion σ(x) = 1 / (1 + e^−x) macht aus jeder Zahl eine Wahrscheinlichkeit
zwischen 0 und 1 (0 → 50 %, groß → fast 100 %). x = a·z + b·rezept + c:
  - z sagt, wie stark der Moment-Score im Vergleich zu den letzten gesendeten ist (robust standardisiert wie der
    Publikums-Score, publikum.robust_z – eine Formel),
  - rezept ist in Stufe 2 immer 0 (der Rangwert des Rezepts kommt mit Stufe 3),
  - a, b, c werden bei jedem Aufruf aus allen bisherigen Urteilen neu geschätzt (Gradientenabstieg, deterministisch).
Unter [erwartung].mindest_urteile Urteilen einer Art gibt es keine Erwartung („noch keine“).

Treffer: (wahrschein ≥ 0,5) == (Urteil positiv). Clip positiv = Status in db.BEWERTET, negativ = verworfen;
Entwurf positiv = daumen > 0 (Zusammenschnitte zählen mit, Annahme S2-A14).
Nichts hier weckt pve-big: nur Datenbank.
"""

from __future__ import annotations

import sqlite3

from .konfig import Konfig

ARTEN = ("clip", "entwurf")


def moment_score(con: sqlite3.Connection, konfig: Konfig, art: str, ziel_id: int,
                 gewichte: dict[str, float]) -> float | None:
    """Moment-Score eines Clips bzw. Mittel über die Momente eines Entwurfs (roh_score, aus momente neu gerechnet).

    None, wenn Clip/Entwurf unbekannt ist. Paket E."""
    raise NotImplementedError


def modell(con: sqlite3.Connection, konfig: Konfig, art: str) -> dict | None:
    """Geschätzte Parameter {"a", "b", "c", "n_urteile", "median", "mad"}; None unter mindest_urteile. Paket E."""
    raise NotImplementedError


def festschreiben(con: sqlite3.Connection, konfig: Konfig, art: str, ziel_id: int) -> float | None:
    """Erwartung beim Senden festschreiben (INSERT … ON CONFLICT (art, ziel_id) DO NOTHING).

    Gibt den GESPEICHERTEN Wert zurück (auch beim zweiten Aufruf den ersten); None unter mindest_urteile (dann keine
    Zeile). Paket E."""
    raise NotImplementedError


def gespeichert(con: sqlite3.Connection, art: str, ziel_id: int) -> float | None:
    """Nur lesen: festgeschriebene Wahrscheinlichkeit oder None. Paket E."""
    raise NotImplementedError


def trefferquote(con: sqlite3.Connection, art: str, letzte: int | None = None) -> tuple[int, int]:
    """(Treffer, geurteilte Erwartungen) einer Art, optional nur die letzten n nach erwartungen.erstellt. Paket E."""
    raise NotImplementedError


def trefferquote_text(con: sqlite3.Connection, konfig: Konfig) -> str:
    """Zeile(n) für /gewichte und /lernstand, z. B. „Erwartung getroffen: Clips 14/20 (70 %) · alle 30/45 …“.

    Leerer Text = nichts anzuzeigen."""
    return ""  # Vertrag: neutral bis Paket E (cmd_gewichte und _cmd_gewichte rufen es schon auf)
