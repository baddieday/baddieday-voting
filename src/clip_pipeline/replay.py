"""Fortnite-Replays auslesen (über das C#-Programm tools/replay2json).

Zeitbasis (gemessen am 21.09.2026, Build 42.20):
  replay_start  = Ortszeit, zu der Fortnite das Replay angelegt hat
  t_ms          = Millisekunden seit replay_start
  -> Zeitpunkt eines Kills = replay_start + t_ms

Stufe 2 (Spec §8.1): Je Ereignis stehen zusätzlich Waffe (GunType-Zahl), Bot-Opfer und verbleibende Spieler
bereit – daraus rechnet merkmale.aus_replay die Replay-Merkmale. Die Kill-Regel vom 24.09. bleibt unverändert.
"""

from __future__ import annotations

import json
import re
import subprocess
from bisect import bisect_right
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
    aktion_utc: datetime | None = None  # nur bei kill: mein Umhauen dieses Gegners (None = kein eigenes Umhauen)
    # Stufe 2 (Spec §8.1), rein additiv – None = unbekannt (altes JSON, Rekorder, ältere Aufrufer):
    waffe: int | None = None        # GunType-ZAHL; bei kill die Waffe MEINES Umhauens, sonst die des Erledigens
    opfer_bot: bool | None = None   # war das Opfer ein Bot? (replay2json eliminiert_bot; null = unbekannt)
    verbleibend: int | None = None  # Spieler noch im Match nach diesem Ereignis (spieler_gesamt − finale Eliminierungen)

    @property
    def aktion(self) -> datetime:
        """Wann die Action zu diesem Ereignis stattfand: mein Umhauen, sonst der Zeitpunkt selbst."""
        return self.aktion_utc or self.zeit_utc


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
    def kill_aktionen(self) -> list[tuple[datetime, datetime]]:
        """(Kill-Zeitpunkt, Aktions-Zeitpunkt) je Kill, sortiert nach Kill – für Clip-Fenster mit Anlauf."""
        return sorted((e.zeit_utc, e.aktion) for e in self.ereignisse if e.art == "kill")

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


def _waffe(eintrag: dict) -> int | None:
    """GunType aus replay2json als Zahl (FortniteReplayReader 3.1.0 liefert nur ein Byte, keinen Namen)."""
    wert = eintrag.get("waffe")
    return int(wert) if isinstance(wert, (int, float)) and not isinstance(wert, bool) else None


def _bot(eintrag: dict) -> bool | None:
    """eliminiert_bot aus replay2json: True/False, null oder fehlend = unbekannt (None)."""
    wert = eintrag.get("eliminiert_bot")
    return bool(wert) if wert is not None else None


def _verbleibend_rechner(eliminierungen: list[dict], spieler_gesamt: int | None):
    """Funktion t_ms → verbleibende Spieler (Annahme S2-A2).

    verbleibend = spieler_gesamt − Anzahl finaler Eliminierungen (knock = false, Selbst-Eliminierungen
    eingeschlossen) mit t_ms ≤ diesem Zeitpunkt, nie unter 0. Bewusst NICHT aus dem Killfeed: der hat kein t_ms
    und enthält auch Knocks und Wiederbelebungen. Ohne spieler_gesamt ist das Ergebnis immer None (unbekannt).
    Beispiel: 100 Spieler, 89 finale Eliminierungen bis t_ms 95000, meine bei 100000 → bei 100000 noch 10 übrig.
    """
    if spieler_gesamt is None:
        return lambda t_ms: None
    finale = sorted(int(e["t_ms"]) for e in eliminierungen if e.get("t_ms") is not None and not e.get("knock"))
    # bisect_right zählt auch Einträge mit genau demselben t_ms (Team-Wipe: alle sterben gleichzeitig)
    return lambda t_ms: max(0, int(spieler_gesamt) - bisect_right(finale, t_ms))


def _meine_ereignisse(eliminierungen: list[dict], start: datetime, meine_ids: set[str],
                      spieler_gesamt: int | None = None) -> list[MeinEreignis]:
    """Meine Kills nach Fortnite-Regel: Der Kill gehört dem, der den Gegner UMGEHAUEN hat.

    Beispiele (Squad):
      ich haue um, Teammate erledigt   -> mein Kill, Zeitpunkt = mein Umhauen (der Moment in meinem Video)
      Teammate haut um, ich erledige   -> Kill des Teammates (so zählt es auch Fortnite)
      ich haue um und erledige selbst  -> mein Kill, Zeitpunkt = das Erledigen
      direkter Kill ohne Umhauen (Solo) -> Kill dessen, der erledigt

    Zusätzlich je Kill die AKTION (aktion_utc) = mein Umhauen, falls ich umgehauen habe, sonst None.
    Gezählt und gruppiert wird weiter nach dem Kill-Zeitpunkt; die Aktion bestimmt nur, wo ein Clip beginnt.
    Wichtig beim Team-Wipe: Ist der letzte Gegner eines Teams umgehauen, sterben alle umgehauenen Gegner
    gleichzeitig – die Kill-Zeitpunkte fallen dann zusammen, das eigentliche Umhauen liegt Sekunden davor.

    Stufe 2 (Spec §8.1), ändert an der Zählung nichts: Jedes Ereignis trägt waffe, opfer_bot und verbleibend des
    Eintrags, aus dem es entsteht. Beim Kill ist die Waffe die MEINES Umhauens (Sniper-Knock, Teammate erledigt →
    Sniper), ohne eigenes Umhauen die des Erledigens; verbleibend zählt bis zum Erledigen (dann ist das Opfer
    wirklich raus). spieler_gesamt fehlt (None) → verbleibend None.
    """
    ereignisse: list[MeinEreignis] = []
    verbleibend = _verbleibend_rechner(eliminierungen, spieler_gesamt)
    letzter_knock: dict[str, tuple[datetime, str, int | None]] = {}  # Opfer -> (Zeitpunkt, wer umgehauen hat, Waffe)
    for e in sorted((e for e in eliminierungen if e.get("t_ms") is not None), key=lambda e: int(e["t_ms"])):
        zeitpunkt = start + timedelta(milliseconds=int(e["t_ms"]))
        taeter = str(e.get("eliminator") or "").upper()
        opfer = str(e.get("eliminiert") or "").upper()
        felder = {"waffe": _waffe(e), "opfer_bot": _bot(e), "verbleibend": verbleibend(int(e["t_ms"]))}
        if e.get("knock"):
            letzter_knock[opfer] = (zeitpunkt, taeter, felder["waffe"])
            if opfer in meine_ids:
                ereignisse.append(MeinEreignis(zeitpunkt, "knock_erlitten", **felder))
            elif taeter in meine_ids:
                ereignisse.append(MeinEreignis(zeitpunkt, "knock", **felder))
            continue
        if opfer in meine_ids:
            ereignisse.append(MeinEreignis(zeitpunkt, "tod", **felder))
            continue
        knock = letzter_knock.pop(opfer, None)
        if knock and (zeitpunkt - knock[0]).total_seconds() > KNOCK_GUELTIG_S:
            knock = None
        gutgeschrieben = knock[1] if knock else taeter
        # Selbst-Eliminierung (Sturm, Sturz) zählt nur, wenn vorher jemand umgehauen hat – dann für den
        if gutgeschrieben in meine_ids and opfer and (knock or not e.get("selbst")):
            eigener_finish = taeter in meine_ids
            # knock ist hier schon mein eigenes Umhauen (gutgeschrieben) und höchstens KNOCK_GUELTIG_S alt
            if knock:  # Waffe meines Umhauens (knock ist hier immer mein eigenes, siehe oben)
                felder["waffe"] = knock[2]
            ereignisse.append(MeinEreignis(zeitpunkt if eigener_finish or not knock else knock[0], "kill",
                                           aktion_utc=knock[0] if knock else None, **felder))
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

    spieler = daten.get("spieler_gesamt")
    ereignisse = _meine_ereignisse(daten.get("eliminierungen") or [], start, meine_ids,
                                   int(spieler) if spieler is not None else None)

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
