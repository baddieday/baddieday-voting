"""Für jeden Kill-Kandidaten die beste Aufnahme wählen.

Die Schnittliste selbst (sessions/<ID>/schnittliste.json) schreibt verarbeitung.decide;
alle Ausgaben – CapCut-Clips, Vorschau, Shorts, Highlight-Video – entstehen daraus.
"""

from __future__ import annotations

from dataclasses import dataclass

from .quellen import Aufnahme
from .vorbewertung import Kandidat

KILL_TOLERANZ_S = 0.5


@dataclass
class Schnitt:
    kandidat: Kandidat
    aufnahme: Aufnahme
    start_s: float  # Position in der Aufnahme
    ende_s: float
    abdeckung: float  # Anteil des Wunsch-Fensters, den die Aufnahme enthält (0..1)

    @property
    def dauer_s(self) -> float:
        return self.ende_s - self.start_s


def waehle_aufnahme(kandidat: Kandidat, aufnahmen: list[Aufnahme], prioritaet: list[str]) -> Schnitt | None:
    """Nimmt die Aufnahme, die alle Kills enthält und das Fenster am besten abdeckt."""
    erster, letzter = kandidat.kill_zeiten[0], kandidat.kill_zeiten[-1]
    beste: tuple[tuple, Schnitt] | None = None
    for a in aufnahmen:
        if a.dauer_s <= 0:
            continue  # Screenshots
        if not (a.enthaelt(erster, KILL_TOLERANZ_S) and a.enthaelt(letzter, KILL_TOLERANZ_S)):
            continue
        von = max(a.start_utc, kandidat.start_utc)
        bis = min(a.ende_utc, kandidat.ende_utc)
        abdeckung = max(0.0, (bis - von).total_seconds()) / max(kandidat.dauer_s, 0.001)
        rang = prioritaet.index(a.quelle) if a.quelle in prioritaet else len(prioritaet)
        schluessel = (round(abdeckung, 2), -rang, a.dauer_s)
        schnitt = Schnitt(
            kandidat=kandidat,
            aufnahme=a,
            start_s=round(max(0.0, a.sekunde(von)), 3),
            ende_s=round(min(a.dauer_s, a.sekunde(bis)), 3),
            abdeckung=round(min(1.0, abdeckung), 3),
        )
        if beste is None or schluessel > beste[0]:
            beste = (schluessel, schnitt)
    return beste[1] if beste else None
