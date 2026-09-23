"""Vorbewertung: aus Kills werden Clip-Kandidaten mit Punkten und Begründung.

Score = Summe über alle Merkmale (Wert × Gewicht). Die Gewichte kommen aus der
Konfiguration (Startgewichte) oder – sobald genug Bewertungen da sind – aus lernen.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .zeitleiste import Zeitleiste

MERKMALE = ("kill_punkte", "victory_royale", "laenge", "lautstaerke", "kommentar")
MERKMAL_NAMEN = {
    "kill_punkte": "Kill-Punkte",
    "victory_royale": "Victory Royale",
    "laenge": "Länge",
    "lautstaerke": "Lautstärke",
    "kommentar": "Kommentar",
}


def typ(anzahl: int) -> str:
    return {1: "einzel", 2: "double", 3: "triple"}.get(anzahl, "multi")


def typ_name(anzahl: int) -> str:
    return {1: "Einzelkill", 2: "Double Kill", 3: "Triple Kill"}.get(anzahl, f"{anzahl}-fach Kill")


def zahl(wert: float, stellen: int = 1) -> str:
    """Deutsche Schreibweise: 6.75 -> '6,8'."""
    return f"{wert:.{stellen}f}".replace(".", ",").replace("-", "−")


def gruppiere(zeiten: list[datetime], fenster_s: float) -> list[list[datetime]]:
    """Kette: Ein Kill gehört zur Gruppe, wenn er höchstens fenster_s nach dem vorherigen liegt."""
    gruppen: list[list[datetime]] = []
    for zeitpunkt in sorted(zeiten):
        if gruppen and (zeitpunkt - gruppen[-1][-1]).total_seconds() <= fenster_s:
            gruppen[-1].append(zeitpunkt)
        else:
            gruppen.append([zeitpunkt])
    return gruppen


def punkte_fuer(anzahl: int, tabelle: list[float]) -> float:
    return float(tabelle[min(anzahl, len(tabelle) - 1)])


def laenge_merkmal(dauer_s: float, frei_s: float) -> float:
    """0 bis frei_s Sekunden, danach 1,0 je 10 Sekunden darüber."""
    return round(max(0.0, dauer_s - frei_s) / 10.0, 2)


@dataclass
class Kandidat:
    nr: int
    gruppen: list[list[datetime]]
    start_utc: datetime
    ende_utc: datetime
    victory_royale: bool = False
    merkmale: dict[str, float] = field(default_factory=dict)
    punkte: float = 0.0
    begruendung: str = ""

    @property
    def kill_zeiten(self) -> list[datetime]:
        return sorted(z for g in self.gruppen for z in g)

    @property
    def kills(self) -> int:
        return sum(len(g) for g in self.gruppen)

    @property
    def max_gruppe(self) -> int:
        return max(len(g) for g in self.gruppen)

    @property
    def typ(self) -> str:
        return typ(self.max_gruppe)

    @property
    def titel(self) -> str:
        teile = [typ_name(len(g)) for g in self.gruppen]
        if self.victory_royale:
            teile.append("Victory Royale")
        return " + ".join(teile)

    @property
    def dauer_s(self) -> float:
        return (self.ende_utc - self.start_utc).total_seconds()


def kandidaten(zeitleiste: Zeitleiste, einstellungen: dict) -> list[Kandidat]:
    """Bildet Gruppen, legt Clip-Fenster mit Puffer darum und fasst Überlappungen zusammen."""
    vorne = timedelta(seconds=float(einstellungen["puffer_vorne_s"]))
    hinten = timedelta(seconds=float(einstellungen["puffer_hinten_s"]))
    tabelle = list(einstellungen["kill_punkte"])

    liste: list[Kandidat] = []
    for gruppe in gruppiere(zeitleiste.kills, float(einstellungen["multikill_fenster_s"])):
        start, ende = gruppe[0] - vorne, gruppe[-1] + hinten
        if liste and start <= liste[-1].ende_utc:
            # Fenster überlappen -> ein gemeinsamer Clip
            liste[-1].gruppen.append(gruppe)
            liste[-1].ende_utc = max(liste[-1].ende_utc, ende)
        else:
            liste.append(Kandidat(nr=len(liste) + 1, gruppen=[gruppe], start_utc=start, ende_utc=ende))

    if liste and zeitleiste.victory_royale:
        liste[-1].victory_royale = True  # der letzte Kill des Matches bekommt den Sieg-Bonus

    for k in liste:
        k.merkmale = {
            "kill_punkte": sum(punkte_fuer(len(g), tabelle) for g in k.gruppen),
            "victory_royale": 1.0 if k.victory_royale else 0.0,
            "laenge": laenge_merkmal(k.dauer_s, float(einstellungen["laenge_frei_s"])),
            "lautstaerke": 0.0,
            "kommentar": 0.0,
        }
    return liste


def bewerte(merkmale: dict[str, float], gewichte: dict[str, float], titel: str = "Kills") -> tuple[float, str]:
    """Rechnet den Score aus und schreibt jeden Summanden nachvollziehbar auf."""
    summe = 0.0
    teile: list[str] = []
    for merkmal in MERKMALE:
        wert = float(merkmale.get(merkmal, 0.0))
        gewicht = float(gewichte.get(merkmal, 0.0))
        beitrag = wert * gewicht
        summe += beitrag
        if wert == 0:
            continue
        name = titel if merkmal == "kill_punkte" else MERKMAL_NAMEN[merkmal]
        if gewicht == 1.0:
            teile.append(f"{name} {zahl(beitrag)}")
        else:
            teile.append(f"{name} {zahl(wert, 2)} × {zahl(gewicht, 2)} = {zahl(beitrag)}")
    return round(summe, 2), " · ".join(teile) or "keine Merkmale"
