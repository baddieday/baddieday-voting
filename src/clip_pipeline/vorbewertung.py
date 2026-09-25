"""Vorbewertung: aus Kills werden Clip-Kandidaten mit Punkten und Begründung.

Score = Summe über alle Merkmale (Wert × Gewicht). Die Gewichte kommen aus der
Konfiguration (Startgewichte) oder – sobald genug Bewertungen da sind – aus lernen.py.

Gezählt (Serien, Punkte, Titel) wird nach dem Kill-Zeitpunkt. Der Clip beginnt aber vor der
ersten AKTION (mein Umhauen) – beim Team-Wipe liegt die Action Sekunden vor den Kills.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .zeitleiste import Zeitleiste

log = logging.getLogger("pipeline")
MAX_DAUER_S = 60.0  # längster Clip (auch Grenze für den Schnitt von decide)
FREMD_NACH_S = 0.5  # ein Anlauf beginnt so viel nach dem Kill eines früheren Clips …
FREMD_VOR_AKTION_S = 1.0  # … aber spätestens so viel vor der ersten Aktion

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


def gruppiere_paare(paare: list[tuple[datetime, datetime]], fenster_s: float) -> list[list[tuple[datetime, datetime]]]:
    """Wie gruppiere, aber je Kill mit seiner Aktion (Kill, Aktion). Die Kette zählt nur den Kill-Zeitpunkt."""
    gruppen: list[list[tuple[datetime, datetime]]] = []
    for paar in sorted(paare):
        if gruppen and (paar[0] - gruppen[-1][-1][0]).total_seconds() <= fenster_s:
            gruppen[-1].append(paar)
        else:
            gruppen.append([paar])
    return gruppen


def punkte_fuer(anzahl: int, tabelle: list[float]) -> float:
    return float(tabelle[min(anzahl, len(tabelle) - 1)])


def laenge_merkmal(dauer_s: float, frei_s: float) -> float:
    """0 bis frei_s Sekunden, danach 1,0 je 10 Sekunden darüber."""
    return round(max(0.0, dauer_s - frei_s) / 10.0, 2)


def laenge_ab_kill(start_s: float, ende_s: float, erster_kill_s: float, vorne_s: float, frei_s: float) -> float:
    """Längen-Merkmal, gezählt ab erstem Kill − vorne_s: Der Anlauf vor der ersten Aktion kostet keine Punkte.

    Der Kill-Anker wird wie die Schnittpunkte auf ms gerundet – so ergibt ein Clip ohne Anlauf genau den alten Wert.
    """
    return laenge_merkmal(ende_s - max(start_s, round(erster_kill_s - vorne_s, 3)), frei_s)


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
    aktionen: list[list[datetime]] = field(default_factory=list)  # parallel zu gruppen; leer = Aktion = Kill
    hinweis: str | None = None  # z. B. wenn der Anlauf auf MAX_DAUER_S gekürzt wurde

    @property
    def kill_zeiten(self) -> list[datetime]:
        return sorted(z for g in self.gruppen for z in g)

    @property
    def aktion_zeiten(self) -> list[datetime]:
        """Parallel zu kill_zeiten (gleiche Reihenfolge = nach Kill sortiert); nicht monoton."""
        if len(self.aktionen) != len(self.gruppen):
            return self.kill_zeiten
        paare = sorted((k, a) for g, ag in zip(self.gruppen, self.aktionen) for k, a in zip(g, ag))
        return [a for _, a in paare]

    @property
    def erste_aktion(self) -> datetime:
        return min(self.aktion_zeiten)

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


def anlauf_start(erste_aktion: float, ende: float, vorne: float, fruehere_kills: Iterable[float] = (), *,
                 fruehestens: float | None = None, spaetestens: float | None = None) -> tuple[float, float, bool]:
    """Beginn eines Clips, der vor der ersten Aktion anläuft – eine Regel für analyze und den Nachschnitt.

    Zeiten in Sekunden auf derselben Achse. Beginn = erste Aktion − vorne, begrenzt auf fruehestens (Beginn der
    Aufnahme) und spaetestens (analyze: das bisherige Kill-Fenster). Liegt im Anlauf ein Kill eines früheren Clips,
    beginnt der Clip FREMD_NACH_S danach, höchstens bis FREMD_VOR_AKTION_S vor der Aktion – sonst stünde derselbe
    Kill in zwei Clips (Battle, Zusammenschnitt) und in kill_sekunden dieses Clips. Wäre der Clip bis `ende` länger
    als MAX_DAUER_S, wird vorne gekappt (nie später als spaetestens).
    Gibt (Beginn, Beginn vor der Kappung, fremder Kill im Anlauf?) zurück."""
    start = erste_aktion - vorne
    if fruehestens is not None:
        start = max(start, fruehestens)
    if spaetestens is not None:
        start = min(start, spaetestens)
    fremd = False
    if davor := [s for s in fruehere_kills if start <= s < erste_aktion]:
        neu = min(max(davor) + FREMD_NACH_S, erste_aktion - FREMD_VOR_AKTION_S)
        if neu > start:
            start, fremd = neu, True
    ohne_kappung = start
    if ende - start > MAX_DAUER_S:
        grenze = ende - MAX_DAUER_S if spaetestens is None else min(spaetestens, ende - MAX_DAUER_S)
        start = max(start, grenze)
    return start, ohne_kappung, fremd


def _anlauf(k: Kandidat, vorne: timedelta, fruehere_kills: list[datetime]) -> None:
    """Zieht den Start auf erste Aktion − vorne vor (Regel: anlauf_start); nie später als das Kill-Fenster."""
    sek = lambda z: (z - k.erste_aktion).total_seconds()  # noqa: E731 – Achse: Sekunden ab der ersten Aktion
    kill_start, ende = sek(k.start_utc), sek(k.ende_utc)
    start, ohne_kappung, fremd = anlauf_start(0.0, ende, vorne.total_seconds(), [sek(z) for z in fruehere_kills],
                                              spaetestens=kill_start)
    if fremd:
        log.info("%s: Anlauf beginnt nach dem Kill eines früheren Clips (%.1f s vor der Aktion)", k.titel, -ohne_kappung)
    if start > ohne_kappung:
        k.hinweis = (f"{k.titel}: Clip ab erster Aktion wäre {ende - ohne_kappung:.0f} s lang "
                     f"(max. {MAX_DAUER_S:.0f} s) – vorne gekürzt auf {ende - start:.0f} s")
        log.warning(k.hinweis)
    k.start_utc = k.erste_aktion + timedelta(seconds=start)


def kandidaten(zeitleiste: Zeitleiste, einstellungen: dict) -> list[Kandidat]:
    """Bildet Gruppen, legt Clip-Fenster mit Puffer darum und fasst Überlappungen zusammen.

    Zusammengefasst wird nach dem Kill-Fenster (erster Kill − vorne … letzter Kill + hinten) wie bisher –
    so bleiben Titel und Punkte gleich. Erst danach beginnt der Clip vor der ersten Aktion (anlauf_start),
    aber nach den Kills früherer Clips.
    """
    vorne = timedelta(seconds=float(einstellungen["puffer_vorne_s"]))
    hinten = timedelta(seconds=float(einstellungen["puffer_hinten_s"]))
    tabelle = list(einstellungen["kill_punkte"])

    liste: list[Kandidat] = []
    for paare in gruppiere_paare(zeitleiste.kill_aktionen, float(einstellungen["multikill_fenster_s"])):
        gruppe, aktionen = [k for k, _ in paare], [a for _, a in paare]
        start, ende = gruppe[0] - vorne, gruppe[-1] + hinten
        if liste and start <= liste[-1].ende_utc:
            # Fenster überlappen -> ein gemeinsamer Clip
            liste[-1].gruppen.append(gruppe)
            liste[-1].aktionen.append(aktionen)
            liste[-1].ende_utc = max(liste[-1].ende_utc, ende)
        else:
            liste.append(Kandidat(nr=len(liste) + 1, gruppen=[gruppe], start_utc=start, ende_utc=ende,
                                  aktionen=[aktionen]))

    if liste and zeitleiste.victory_royale:
        liste[-1].victory_royale = True  # der letzte Kill des Matches bekommt den Sieg-Bonus

    fruehere_kills: list[datetime] = []  # der Anlauf darf keinen Kill eines früheren Clips mitnehmen
    for k in liste:
        _anlauf(k, vorne, fruehere_kills)
        fruehere_kills.extend(k.kill_zeiten)
        ab_kill = max(k.start_utc, k.kill_zeiten[0] - vorne)  # Länge ohne Anlauf (wie laenge_ab_kill)
        k.merkmale = {
            "kill_punkte": sum(punkte_fuer(len(g), tabelle) for g in k.gruppen),
            "victory_royale": 1.0 if k.victory_royale else 0.0,
            "laenge": laenge_merkmal((k.ende_utc - ab_kill).total_seconds(), float(einstellungen["laenge_frei_s"])),
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
