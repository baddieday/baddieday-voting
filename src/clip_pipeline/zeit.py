"""Zeit-Hilfen. Intern rechnet die Pipeline immer in UTC.

Ortszeit kommt nur an zwei Stellen vor: beim Einlesen von Dateinamen/Replays
(Windows schreibt Ortszeit) und bei der Anzeige. Sommer-/Winterzeit übernimmt
die Zeitzonen-Datenbank (unter Windows: Paket "tzdata").
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc


@lru_cache(maxsize=None)
def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        raise RuntimeError(
            f"Zeitzone {name!r} unbekannt – unter Windows fehlt das Python-Paket 'tzdata'."
        ) from None


def lokal_zu_utc(zeitpunkt: datetime, zonen_name: str) -> datetime:
    """Ortszeit ohne Zonenangabe -> UTC. Hat der Zeitpunkt schon eine Zone, wird nur umgerechnet."""
    if zeitpunkt.tzinfo is None:
        zeitpunkt = zeitpunkt.replace(tzinfo=zone(zonen_name))
    return zeitpunkt.astimezone(UTC)


def utc_zu_lokal(zeitpunkt: datetime, zonen_name: str) -> datetime:
    return zeitpunkt.astimezone(zone(zonen_name))


def jetzt() -> datetime:
    return datetime.now(UTC)


def iso(zeitpunkt: datetime) -> str:
    """UTC-Zeitstempel als Text, z. B. 2026-09-21T19:53:29.453Z."""
    return zeitpunkt.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def aus_iso(text: str) -> datetime:
    """Gegenstück zu iso(). Zeitpunkte ohne Zone gelten als UTC."""
    zeitpunkt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return zeitpunkt if zeitpunkt.tzinfo else zeitpunkt.replace(tzinfo=UTC)


def spielabend(zeitpunkt: datetime, zonen_name: str, wechsel_stunde: int = 6) -> date:
    """Zu welchem Abend gehört eine Runde? Runden bis 06:00 zählen zum Vortag."""
    lokal = utc_zu_lokal(zeitpunkt, zonen_name)
    return (lokal - timedelta(hours=wechsel_stunde)).date()


def sekunden(von: datetime, bis: datetime) -> float:
    return (bis - von).total_seconds()
