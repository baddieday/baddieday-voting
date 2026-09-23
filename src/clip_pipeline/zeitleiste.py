"""Kill-Zeitleiste eines Matches: Wann habe ich wen eliminiert?

Erste Wahl ist das Replay. Wenn es fehlt oder der Parser streikt, springen die
Rekorder ein (SteelSeries hat genauere Zeitpunkte als Nvidia). Achtung: Die
Rekorder zählen in Squads auch Team-Kills mit – das wird als Warnung vermerkt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .quellen import Aufnahme, Ereignis
from .replay import Match

DOPPELT_S = 1.5  # gleiche Meldung aus überlappenden Clips


@dataclass
class Zeitleiste:
    kills: list[datetime]  # ein Eintrag pro Kill, sortiert (UTC)
    quelle: str  # replay | steelseries | nvidia | keine
    victory_royale: bool = False
    platzierung: int | None = None
    warnungen: list[str] = field(default_factory=list)


def aus_replay(match: Match) -> Zeitleiste:
    return Zeitleiste(
        kills=sorted(match.kills),
        quelle="replay",
        victory_royale=match.victory_royale,
        platzierung=match.platzierung,
        warnungen=list(match.warnungen),
    )


def _ohne_doppelte(ereignisse: list[Ereignis]) -> list[Ereignis]:
    ergebnis: list[Ereignis] = []
    for e in sorted(ereignisse, key=lambda e: e.zeit_utc):
        if ergebnis:
            letzte = ergebnis[-1]
            if letzte.anzahl == e.anzahl and (e.zeit_utc - letzte.zeit_utc).total_seconds() < DOPPELT_S:
                continue
        ergebnis.append(e)
    return ergebnis


def aus_rekordern(aufnahmen: list[Aufnahme], start: datetime, ende: datetime) -> Zeitleiste:
    im_fenster = [e for a in aufnahmen for e in a.ereignisse if start <= e.zeit_utc <= ende]
    sieg = any(e.art == "sieg" for e in im_fenster)
    for quelle in ("steelseries", "nvidia"):
        kills = _ohne_doppelte([e for e in im_fenster if e.quelle == quelle and e.art == "kill"])
        if not kills:
            continue
        zeiten: list[datetime] = []
        for e in kills:
            # SteelSeries: jeder Marker ist genau ein Kill (der Multikill-Stern vollendet eine Serie).
            # Nvidia: "Doppeleliminierung" ist eine Datei für zwei Kills.
            zeiten.extend([e.zeit_utc] * (1 if quelle == "steelseries" else e.anzahl))
        return Zeitleiste(
            kills=sorted(zeiten),
            quelle=quelle,
            victory_royale=sieg,
            warnungen=[f"Kills aus {quelle}-Daten geschätzt – in Squads zählen Rekorder auch Team-Kills"],
        )
    return Zeitleiste(kills=[], quelle="keine", victory_royale=sieg)


def baue(match: Match | None, aufnahmen: list[Aufnahme], start: datetime, ende: datetime) -> Zeitleiste:
    if match is not None and match.ich_quelle is not None:
        return aus_replay(match)
    zeitleiste = aus_rekordern(aufnahmen, start, ende)
    if match is not None:
        zeitleiste.warnungen = match.warnungen + zeitleiste.warnungen
        zeitleiste.platzierung = match.platzierung
        zeitleiste.victory_royale = zeitleiste.victory_royale or match.victory_royale
    else:
        zeitleiste.warnungen.insert(0, "Replay nicht lesbar – ⚠️ ohne Replay-Daten")
    return zeitleiste
