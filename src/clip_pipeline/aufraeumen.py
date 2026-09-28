"""`pipeline aufraeumen`: Speicher auf dem großen Host sauber halten.

Regel (Wunsch vom 23.09.2026):
  - Nach [aufraeumen].nach_tagen (182 = ein halbes Jahr) wird recycelt,
  - AUSSER Multikills ab [aufraeumen].archiv_ab_kills (3): die wandern für immer nach archiv/.
Recyceln heißt: erst in papierkorb/<Datum>/ verschieben, nach papierkorb_tage endgültig löschen.
Ohne --ausfuehren wird nur angezeigt, was passieren würde (Probelauf).

Standardmäßig aus (Entscheidung 25.09.: nie automatisch löschen) und im getrennten Betrieb (E19: Puffer + Lager)
immer gesperrt, egal was [aufraeumen].aktiv sagt – siehe pruefe_erlaubt.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from .db import transaktion
from .konfig import Konfig, KonfigFehler
from .zeit import UTC, aus_iso, jetzt


@dataclass
class Aktion:
    pfad: Path
    ziel: str  # archiv | papierkorb | loeschen
    grund: str


def pruefe_erlaubt(konfig: Konfig) -> None:
    """Verweigert (KonfigFehler), wenn aufraeumen in dieser Konfig nicht laufen darf:
      1. Getrennter Betrieb (E19): aufraeumen würde Dateien im Puffer verschieben und die Pfade in der Datenbank
         umschreiben – der Abgleich ins Lager sähe sie danach unter neuem Namen, und im Puffer wird in dieser
         Stufe nichts gelöscht (Freigabe erst mit B5). Gilt immer, unabhängig von [aufraeumen].aktiv.
      2. [aufraeumen].aktiv nicht gesetzt: Standard ist aus (Entscheidung 25.09., „nie automatisch löschen“) –
         betrifft vor allem künftige Betriebsarten ohne [lager]-Abgleich (Front C: lokal/portabel/andere Spiele),
         wo Punkt 1 nicht greift und der Löschweg (papierkorb_tage) sonst unbemerkt scharf wäre."""
    if konfig.getrennt:
        raise KonfigFehler("aufraeumen ist im getrennten Betrieb (Puffer + Lager) gesperrt: es würde Dateien im Puffer "
                           "verschieben und Pfade in der Datenbank umschreiben. Timer clip-aufraeumen ausschalten "
                           "(docs/PUFFER.md, R6).")
    if not konfig.wert("aufraeumen.aktiv", False):
        raise KonfigFehler("aufraeumen ist standardmäßig aus (nie automatisch löschen, Entscheidung 25.09.) – "
                           "erst mit [aufraeumen].aktiv = true erlaubt (z. B. config/lokal.toml).")


def _wertvolle_fenster(con: sqlite3.Connection, ab_kills: int) -> list[tuple[datetime, datetime]]:
    zeilen = con.execute("SELECT start_utc, ende_utc FROM clips WHERE max_gruppe >= ?", (ab_kills,)).fetchall()
    return [(aus_iso(z["start_utc"]), aus_iso(z["ende_utc"])) for z in zeilen]


def _wertvolle_dateien(con: sqlite3.Connection, ab_kills: int) -> set[str]:
    dateien: set[str] = set()
    for z in con.execute(
        """SELECT c.clip_pfad, c.vorschau_pfad, c.short_pfad, c.quelle_pfad, m.replay_pfad
             FROM clips c JOIN matches m ON m.id = c.match_id WHERE c.max_gruppe >= ?""",
        (ab_kills,),
    ):
        dateien.update(p for p in z if p)
    # Auch Rekorder-Ereignisse zählen (falls ein Match nie verarbeitet wurde)
    for z in con.execute("SELECT pfad, ereignisse FROM aufnahmen"):
        if any(e.get("art") == "kill" and int(e.get("anzahl", 1)) >= ab_kills for e in json.loads(z["ereignisse"])):
            dateien.add(z["pfad"])
    return dateien


def plane(con: sqlite3.Connection, konfig: Konfig, heute: datetime | None = None) -> list[Aktion]:
    pruefe_erlaubt(konfig)
    heute = heute or jetzt()
    grenze = heute - timedelta(days=int(konfig.wert("aufraeumen.nach_tagen", 182)))
    ab_kills = int(konfig.wert("aufraeumen.archiv_ab_kills", 3))
    wertvoll = _wertvolle_dateien(con, ab_kills)
    fenster = _wertvolle_fenster(con, ab_kills)
    zeitspannen = {
        z["pfad"]: (aus_iso(z["start_utc"]), aus_iso(z["ende_utc"]))
        for z in con.execute("SELECT pfad, start_utc, ende_utc FROM aufnahmen")
    }

    aktionen: list[Aktion] = []
    for name in ("eingang", "replays", "sessions"):
        ordner = konfig.ordner(name)
        if not ordner.is_dir():
            continue
        for datei in sorted(p for p in ordner.rglob("*") if p.is_file()):
            if name == "sessions" and datei.suffix.lower() != ".mp4":
                continue  # JSON-Dateien sind winzig und bleiben als Protokoll
            geaendert = datetime.fromtimestamp(datei.stat().st_mtime, UTC)
            if geaendert > grenze:
                continue
            relativ = konfig.relativ(datei)
            spanne = zeitspannen.get(relativ)
            ueberlappt = spanne is not None and any(s <= spanne[1] and spanne[0] <= e for s, e in fenster)
            if relativ in wertvoll or ueberlappt:
                aktionen.append(Aktion(datei, "archiv", f"Multikill ab {ab_kills} – dauerhaft behalten"))
            else:
                aktionen.append(Aktion(datei, "papierkorb", f"älter als {grenze:%d.%m.%Y}"))

    # Papierkorb-Tage, deren Frist abgelaufen ist
    korb = konfig.ordner("papierkorb")
    frist = int(konfig.wert("aufraeumen.papierkorb_tage", 14))
    if korb.is_dir():
        for tag in sorted(p for p in korb.iterdir() if p.is_dir()):
            try:
                datum = date.fromisoformat(tag.name)
            except ValueError:
                continue
            if datum <= (heute - timedelta(days=frist)).date():
                aktionen.append(Aktion(tag, "loeschen", f"seit {frist} Tagen im Papierkorb"))
    return aktionen


def _pfade_anpassen(con: sqlite3.Connection, alt: str, neu: str | None) -> None:
    """Datenbank nachziehen, damit keine Einträge auf verschobene Dateien zeigen."""
    for spalte in ("clip_pfad", "vorschau_pfad", "short_pfad"):
        con.execute(f"UPDATE clips SET {spalte} = ? WHERE {spalte} = ?", (neu, alt))
    if neu:
        con.execute("UPDATE clips SET quelle_pfad = ? WHERE quelle_pfad = ?", (neu, alt))
        con.execute("UPDATE aufnahmen SET pfad = ? WHERE pfad = ?", (neu, alt))
        con.execute("UPDATE matches SET replay_pfad = ? WHERE replay_pfad = ?", (neu, alt))
    else:
        con.execute("DELETE FROM aufnahmen WHERE pfad = ?", (alt,))


def fuehre_aus(con: sqlite3.Connection, konfig: Konfig, aktionen: list[Aktion], heute: datetime | None = None) -> dict:
    pruefe_erlaubt(konfig)
    heute = heute or jetzt()
    wurzel = konfig.wurzel.resolve()
    korb = konfig.ordner("papierkorb").resolve()
    zaehler = {"archiv": 0, "papierkorb": 0, "loeschen": 0}
    for a in aktionen:
        if a.ziel == "loeschen":
            ziel = a.pfad.resolve()
            if korb not in ziel.parents:  # Sicherung: gelöscht wird nur im Papierkorb
                raise RuntimeError(f"Weigere mich, außerhalb des Papierkorbs zu löschen: {ziel}")
            shutil.rmtree(ziel)
        else:
            relativ = konfig.relativ(a.pfad)
            basis = konfig.ordner("archiv") if a.ziel == "archiv" else korb / heute.date().isoformat()
            ziel = basis / relativ
            ziel.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(a.pfad), str(ziel))
            neu = ziel.resolve().relative_to(wurzel).as_posix() if a.ziel == "archiv" else None
            with transaktion(con):  # je Datei: Datei und Datenbank bleiben zusammen stimmig
                _pfade_anpassen(con, relativ, neu)
        zaehler[a.ziel] += 1
    return zaehler
