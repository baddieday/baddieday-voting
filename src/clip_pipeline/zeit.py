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


def uhrzeit(text: str) -> int:
    """Minuten seit Mitternacht, z. B. "23:00" -> 1380. ValueError bei Unsinn."""
    stunde, minute = (int(t) for t in text.strip().split(":"))
    if not (0 <= stunde < 24 and 0 <= minute < 60):
        raise ValueError(text)
    return stunde * 60 + minute


def im_zeitfenster(zeitpunkt: datetime, von: str, bis: str, zonen_name: str) -> bool:
    """Liegt zeitpunkt (Ortszeit) in von … bis? Über Mitternacht erlaubt (23:00–08:00). Leer oder von = bis: nie.
    Ungültige Uhrzeit: ValueError – was das heißt, entscheidet der Aufrufer ([telegram].leise_*, [lager].nachtruhe_*)."""
    von, bis = von.strip(), bis.strip()
    if not von or not bis:
        return False
    anfang, ende = uhrzeit(von), uhrzeit(bis)
    lokal = utc_zu_lokal(zeitpunkt, zonen_name)
    minute = lokal.hour * 60 + lokal.minute
    if anfang <= ende:
        return anfang <= minute < ende
    return minute >= anfang or minute < ende


def spielabend(zeitpunkt: datetime, zonen_name: str, wechsel_stunde: int = 6) -> date:
    """Zu welchem Abend gehört eine Runde? Runden bis 06:00 zählen zum Vortag."""
    lokal = utc_zu_lokal(zeitpunkt, zonen_name)
    return (lokal - timedelta(hours=wechsel_stunde)).date()


def sekunden(von: datetime, bis: datetime) -> float:
    return (bis - von).total_seconds()
