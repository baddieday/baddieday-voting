"""Fortnite-Replays auslesen (über das C#-Programm tools/replay2json).

Zeitbasis (gemessen am 21.09.2026, Build 42.20):
  replay_start  = Ortszeit, zu der Fortnite das Replay angelegt hat
  t_ms          = Millisekunden seit replay_start
  -> Zeitpunkt eines Kills = replay_start + t_ms
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from .konfig import Konfig
from .zeit import UTC, lokal_zu_utc

REPLAY_NAME = re.compile(r"^UnsavedReplay-(?P<datum>\d{4}\.\d{2}\.\d{2})-(?P<zeit>\d{2}\.\d{2}\.\d{2})\.replay$", re.I)


class ReplayFehler(RuntimeError):
    pass


@dataclass
class MeinEreignis:
    zeit_utc: datetime
    art: str  # kill | knock | tod | knock_erlitten


@dataclass
class Match:
    id: str
    start_utc: datetime
    ende_utc: datetime
    build: str
    platzierung: int | None
    kills_stats: int | None
    ich_quelle: str | None
    ereignisse: list[MeinEreignis] = field(default_factory=list)
    warnungen: list[str] = field(default_factory=list)

    @property
    def kills(self) -> list[datetime]:
        return [e.zeit_utc for e in self.ereignisse if e.art == "kill"]

    @property
    def victory_royale(self) -> bool:
        return self.platzierung == 1


def match_id(pfad: Path) -> str:
    """UnsavedReplay-2026.09.21-21.42.22.replay -> 2026-09-21_21-42-22."""
    if treffer := REPLAY_NAME.match(pfad.name):
        return treffer["datum"].replace(".", "-") + "_" + treffer["zeit"].replace(".", "-")
    return re.sub(r"[^A-Za-z0-9_-]+", "_", pfad.stem)[:60]


def startzeit_aus_name(pfad: Path, zonen_name: str) -> datetime | None:
    if treffer := REPLAY_NAME.match(pfad.name):
        lokal = datetime.strptime(f"{treffer['datum']} {treffer['zeit']}", "%Y.%m.%d %H.%M.%S")
        return lokal_zu_utc(lokal, zonen_name)
    return None


def lies_json(pfad: Path, konfig: Konfig) -> dict:
    """Ruft replay2json auf und gibt dessen JSON zurück."""
    programm = konfig.replay_programm
    if not programm.exists():
        raise ReplayFehler(f"replay2json nicht gebaut: {programm} fehlt (siehe README, Abschnitt Replay-Parser)")
    befehl = [str(programm), str(pfad)]
    if ich := str(konfig.wert("replay.ich", "") or "").strip():
        befehl += ["--ich", ich]
    try:
        ergebnis = subprocess.run(
            befehl,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=float(konfig.wert("replay.timeout_s", 120)),
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise ReplayFehler(f"{pfad.name}: Zeitlimit beim Auslesen überschritten") from None
    if ergebnis.returncode != 0:
        raise ReplayFehler(f"{pfad.name}: replay2json Exit {ergebnis.returncode}: {ergebnis.stderr.strip()[:400]}")
    try:
        return json.loads(ergebnis.stdout)
    except json.JSONDecodeError as fehler:
        raise ReplayFehler(f"{pfad.name}: ungültiges JSON von replay2json ({fehler})") from None


KNOCK_GUELTIG_S = 90  # länger zurückliegendes Umhauen zählt nicht mehr (Gegner wurde vermutlich wiederbelebt)


def _meine_ereignisse(eliminierungen: list[dict], start: datetime, meine_ids: set[str]) -> list[MeinEreignis]:
    """Meine Kills nach Fortnite-Regel: Der Kill gehört dem, der den Gegner UMGEHAUEN hat.

    Beispiele (Squad):
      ich haue um, Teammate erledigt   -> mein Kill, Zeitpunkt = mein Umhauen (der Moment in meinem Video)
      Teammate haut um, ich erledige   -> Kill des Teammates (so zählt es auch Fortnite)
      ich haue um und erledige selbst  -> mein Kill, Zeitpunkt = das Erledigen
      direkter Kill ohne Umhauen (Solo) -> Kill dessen, der erledigt
    """
    ereignisse: list[MeinEreignis] = []
    letzter_knock: dict[str, tuple[datetime, str]] = {}  # Opfer -> (Zeitpunkt, wer umgehauen hat)
    for e in sorted((e for e in eliminierungen if e.get("t_ms") is not None), key=lambda e: int(e["t_ms"])):
        zeitpunkt = start + timedelta(milliseconds=int(e["t_ms"]))
        taeter = str(e.get("eliminator") or "").upper()
        opfer = str(e.get("eliminiert") or "").upper()
        if e.get("knock"):
            letzter_knock[opfer] = (zeitpunkt, taeter)
            if opfer in meine_ids:
                ereignisse.append(MeinEreignis(zeitpunkt, "knock_erlitten"))
            elif taeter in meine_ids:
                ereignisse.append(MeinEreignis(zeitpunkt, "knock"))
            continue
        if opfer in meine_ids:
            ereignisse.append(MeinEreignis(zeitpunkt, "tod"))
            continue
        knock = letzter_knock.pop(opfer, None)
        if knock and (zeitpunkt - knock[0]).total_seconds() > KNOCK_GUELTIG_S:
            knock = None
        gutgeschrieben = knock[1] if knock else taeter
        # Selbst-Eliminierung (Sturm, Sturz) zählt nur, wenn vorher jemand umgehauen hat – dann für den
        if gutgeschrieben in meine_ids and opfer and (knock or not e.get("selbst")):
            eigener_finish = taeter in meine_ids
            ereignisse.append(MeinEreignis(zeitpunkt if eigener_finish or not knock else knock[0], "kill"))
    ereignisse.sort(key=lambda e: e.zeit_utc)
    return ereignisse


def match_aus_json(daten: dict, mid: str, *, zonen_name: str, start_ist_ortszeit: bool = True) -> Match:
    """Wandelt das JSON von replay2json in ein Match (ohne Dateizugriff, gut testbar)."""
    roh_start = daten.get("replay_start")
    if not roh_start:
        raise ReplayFehler(f"{mid}: Replay ohne Startzeit")
    start = datetime.fromisoformat(roh_start.replace("Z", "+00:00"))
    if start.tzinfo is None:
        if daten.get("replay_start_kind") == "Utc" or not start_ist_ortszeit:
            start = start.replace(tzinfo=UTC)
        else:
            start = lokal_zu_utc(start, zonen_name)
    ende = start + timedelta(milliseconds=int(daten.get("laenge_ms") or 0))

    ich = daten.get("ich") or {}
    meine_ids = {str(i).upper() for i in (ich.get("epic_id"), ich.get("player_id")) if i}
    warnungen: list[str] = []
    if not meine_ids:
        warnungen.append("Eigener Spieler im Replay nicht gefunden – Epic-ID in config/pipeline.toml [replay] ich eintragen")

    ereignisse = _meine_ereignisse(daten.get("eliminierungen") or [], start, meine_ids)

    kills_stats = daten.get("stats_eliminierungen")
    anzahl_kills = sum(1 for e in ereignisse if e.art == "kill")
    if meine_ids and kills_stats is not None and anzahl_kills != kills_stats:
        warnungen.append(f"Replay zählt {kills_stats} Kills, gefunden wurden {anzahl_kills}")

    build = daten.get("build") or {}
    return Match(
        id=mid,
        start_utc=start,
        ende_utc=ende,
        build=f"{build.get('branch') or '?'} CL{build.get('changelist') or '?'}",
        platzierung=ich.get("platzierung") or daten.get("team_platzierung"),
        kills_stats=kills_stats,
        ich_quelle=daten.get("ich_quelle"),
        ereignisse=ereignisse,
        warnungen=warnungen,
    )


def lies(pfad: Path, konfig: Konfig) -> tuple[Match, dict]:
    daten = lies_json(pfad, konfig)
    match = match_aus_json(
        daten,
        match_id(pfad),
        zonen_name=konfig.wert("zeit.zeitzone", "Europe/Berlin"),
        start_ist_ortszeit=bool(konfig.wert("zeit.replay_start_ist_ortszeit", True)),
    )
    return match, daten
