"""Aufnahmen erkennen: Welcher Rekorder? Welche Zeitspanne (UTC)? Welche Ereignisse?

Geprüft an echten Dateien vom 21.09.2026:

Nvidia App (Namen: "<Spiel> JJJJ.MM.TT - HH.MM.SS.<Zähler>[.<Ereignis>][.DVR].mp4")
  - mit .DVR (Videobeweis) oder Ereignis (Highlights): Uhrzeit = Speicherzeitpunkt = ENDE
  - ohne beides (manuelle Aufnahme, Alt+F9): Uhrzeit = START
  - Tag "creation_time" enthält dieselbe Sekunde in UTC
  - Highlights enden ca. 4 s nach dem Ereignis und zählen in Squads auch Team-Kills
SteelSeries Moments (Namen: "<Spiel>__JJJJ-MM-TT__HH-MM-SS.mp4")
  - JSON in den Format-Tags STEELSERIES_META0000, 0001, ... (zusammensetzen!)
  - recording_timestamp (mit Zeitzone) = ENDE, gamesense_events[].clip_timestamp = Sekunde im Clip
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from .medien import Probe
from .zeit import aus_iso, iso, lokal_zu_utc

NVIDIA_NAME = re.compile(
    r"^(?P<spiel>.+?) (?P<datum>\d{4}\.\d{2}\.\d{2}) - (?P<zeit>\d{2}\.\d{2}\.\d{2})\.(?P<zaehler>\d+)"
    r"(?:\.(?!DVR\.)(?P<ereignis>[^.]+))?(?P<dvr>\.DVR)?\.(?P<endung>mp4|png)$",
    re.IGNORECASE,
)
STEELSERIES_NAME = re.compile(
    r"^(?P<spiel>.+?)__(?P<datum>\d{4}-\d{2}-\d{2})__(?P<zeit>\d{2}-\d{2}-\d{2})[^.]*\.mp4$",
    re.IGNORECASE,
)

# Nvidia-Ereignisnamen (deutsch, wie in F:\Clips gefunden, plus englische Varianten)
NVIDIA_EREIGNISSE: dict[str, tuple[str, int]] = {
    "eliminierung": ("kill", 1),
    "doppeleliminierung": ("kill", 2),
    "dreifacheliminierung": ("kill", 3),
    "dreifacheliminerung": ("kill", 3),  # so schreibt es Nvidia tatsächlich
    "mehrfacheliminierung": ("kill", 4),
    "am boden": ("knock", 1),
    "eliminiert": ("tod", 1),
    "sieg": ("sieg", 1),
    "elimination": ("kill", 1),
    "double elimination": ("kill", 2),
    "triple elimination": ("kill", 3),
    "multi elimination": ("kill", 4),
    "knocked": ("knock", 1),
    "eliminated": ("tod", 1),
    "victory": ("sieg", 1),
}

STEELSERIES_MULTI = {"DOUBLE": 2, "TRIPLE": 3, "QUAD": 4, "PENTA": 5, "HEXA": 6}


@dataclass
class Ereignis:
    zeit_utc: datetime
    art: str  # kill | knock | tod | sieg
    anzahl: int = 1  # bei Multikills: Größe laut Rekorder
    quelle: str = ""

    def als_dict(self) -> dict:
        return {"zeit": iso(self.zeit_utc), "art": self.art, "anzahl": self.anzahl, "quelle": self.quelle}

    @classmethod
    def aus_dict(cls, daten: dict) -> "Ereignis":
        return cls(aus_iso(daten["zeit"]), daten["art"], int(daten.get("anzahl", 1)), daten.get("quelle", ""))


@dataclass
class Erkennung:
    """Was sich allein aus dem Dateinamen ablesen lässt."""

    quelle: str  # nvidia_highlight | nvidia_dvr | nvidia_aufnahme | nvidia_screenshot | steelseries
    zeit_lokal: datetime  # Uhrzeit aus dem Namen (Ortszeit, ohne Zone)
    name_ist_ende: bool
    ereignis: str | None = None


@dataclass
class Aufnahme:
    pfad: str  # relativ zur Speicher-Wurzel
    quelle: str
    start_utc: datetime
    ende_utc: datetime
    dauer_s: float
    tonspuren: int
    fps: float
    ereignisse: list[Ereignis] = field(default_factory=list)
    lautheit_i: float | None = None

    def enthaelt(self, zeitpunkt: datetime, toleranz_s: float = 0.0) -> bool:
        rand = timedelta(seconds=toleranz_s)
        return self.start_utc - rand <= zeitpunkt <= self.ende_utc + rand

    def sekunde(self, zeitpunkt: datetime) -> float:
        """Position eines Zeitpunkts im Video in Sekunden."""
        return (zeitpunkt - self.start_utc).total_seconds()


def erkenne(dateiname: str) -> Erkennung | None:
    """Erkennt Fortnite-Aufnahmen am Namen. Andere Spiele und fremde Dateien -> None."""
    if treffer := NVIDIA_NAME.match(dateiname):
        spiel = treffer["spiel"].strip().lower()
        if not spiel.startswith("fortnite"):
            return None
        zeit = datetime.strptime(f"{treffer['datum']} {treffer['zeit']}", "%Y.%m.%d %H.%M.%S")
        ereignis = (treffer["ereignis"] or "").strip() or None
        if treffer["endung"].lower() == "png":
            return Erkennung("nvidia_screenshot", zeit, True, ereignis)
        if ereignis:
            return Erkennung("nvidia_highlight", zeit, True, ereignis)
        if treffer["dvr"]:
            return Erkennung("nvidia_dvr", zeit, True)
        return Erkennung("nvidia_aufnahme", zeit, False)
    if treffer := STEELSERIES_NAME.match(dateiname):
        if not treffer["spiel"].strip().lower().startswith("fortnite"):
            return None
        zeit = datetime.strptime(f"{treffer['datum']} {treffer['zeit']}", "%Y-%m-%d %H-%M-%S")
        return Erkennung("steelseries", zeit, True)
    return None


def steelseries_meta(tags: dict[str, str]) -> dict | None:
    """Setzt die auf mehrere Tags verteilte SteelSeries-JSON wieder zusammen."""
    teile = sorted((k, v) for k, v in tags.items() if k.upper().startswith("STEELSERIES_META"))
    if not teile:
        return None
    try:
        return json.loads("".join(v for _, v in teile))
    except json.JSONDecodeError:
        return None


def _steelseries_ereignisse(meta: dict, start_utc: datetime) -> list[Ereignis]:
    ergebnis = []
    for roh in meta.get("gamesense_events") or []:
        typ = str(roh.get("type", "")).upper()
        name = str(roh.get("unlocalized_display_name", "")).upper()
        try:
            zeitpunkt = start_utc + timedelta(seconds=float(roh.get("clip_timestamp", 0)))
        except (TypeError, ValueError):
            continue
        multi = next((n for wort, n in STEELSERIES_MULTI.items() if wort in name), 0)
        if typ == "KILL":
            ergebnis.append(Ereignis(zeitpunkt, "kill", 1, "steelseries"))
        elif multi:
            # Der Stern markiert den Kill, der den Multikill vollendet – er zählt als weiterer Kill
            ergebnis.append(Ereignis(zeitpunkt, "kill", multi, "steelseries"))
        elif "VICTORY" in name:
            ergebnis.append(Ereignis(zeitpunkt, "sieg", 1, "steelseries"))
        elif typ in ("DEATH", "DIED") or name in ("ELIMINATED", "DEATH"):
            ergebnis.append(Ereignis(zeitpunkt, "tod", 1, "steelseries"))
        # alles andere (z. B. "QUEST COMPLETE") ist für uns uninteressant
    return ergebnis


def _nvidia_referenz(erkennung: Erkennung, tags: dict[str, str], zonen_name: str) -> datetime:
    """Uhrzeit aus dem Namen -> UTC. creation_time (UTC) ist genauer gegen Zeitumstellungen."""
    aus_name = lokal_zu_utc(erkennung.zeit_lokal, zonen_name)
    if erstellt := tags.get("creation_time"):
        try:
            aus_tag = aus_iso(erstellt)
            if abs((aus_tag - aus_name).total_seconds()) <= 120:
                return aus_tag.replace(microsecond=0)
        except ValueError:
            pass
    return aus_name


def aufnahme_aus(
    relativer_pfad: str,
    erkennung: Erkennung,
    info: Probe,
    *,
    zonen_name: str,
    versatz_nvidia_s: float = 0.0,
    nachlauf_nvidia_s: float = 4.0,
    versatz_steelseries_s: float = 0.0,
) -> Aufnahme:
    """Berechnet Start/Ende in UTC und liest Ereignisse aus Name bzw. Metadaten."""
    dauer = info.dauer_s
    ereignisse: list[Ereignis] = []

    if erkennung.quelle == "steelseries":
        meta = steelseries_meta(info.tags) or {}
        ende = None
        if stempel := meta.get("recording_timestamp"):
            try:
                ende = aus_iso(stempel)
            except ValueError:
                ende = None
        if ende is None:
            ende = lokal_zu_utc(erkennung.zeit_lokal, zonen_name)
        ende += timedelta(seconds=versatz_steelseries_s)
        dauer = float(meta.get("full_length") or dauer)
        start = ende - timedelta(seconds=dauer)
        ereignisse = _steelseries_ereignisse(meta, start)
    else:
        referenz = _nvidia_referenz(erkennung, info.tags, zonen_name) + timedelta(seconds=versatz_nvidia_s)
        if erkennung.name_ist_ende:
            ende, start = referenz, referenz - timedelta(seconds=dauer)
        else:
            start, ende = referenz, referenz + timedelta(seconds=dauer)
        if erkennung.ereignis:
            art, anzahl = NVIDIA_EREIGNISSE.get(erkennung.ereignis.strip().lower(), ("", 0))
            if art:
                zeitpunkt = ende - timedelta(seconds=nachlauf_nvidia_s if dauer > 0 else 0)
                ereignisse.append(Ereignis(zeitpunkt, art, anzahl, "nvidia"))

    return Aufnahme(
        pfad=relativer_pfad,
        quelle=erkennung.quelle,
        start_utc=start,
        ende_utc=ende,
        dauer_s=dauer,
        tonspuren=len(info.tonspuren),
        fps=info.fps,
        ereignisse=ereignisse,
    )


def ist_kandidat(pfad: Path) -> bool:
    return pfad.suffix.lower() in (".mp4", ".png") and erkenne(pfad.name) is not None
