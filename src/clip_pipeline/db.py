"""SQLite-Zugriff. Bot und Pipeline teilen sich dieselbe Datenbank-Datei.

WAL-Modus erlaubt gleichzeitiges Lesen und Schreiben; busy_timeout lässt einen
Schreiber kurz warten, statt sofort "database is locked" zu melden.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from importlib import resources
from pathlib import Path
from typing import Iterator

from .zeit import iso, jetzt

CLIP_STATUS = ("neu", "vorbewertet", "gesendet", "freigegeben", "verworfen", "veroeffentlicht", "im_highlight")
BEWERTET = ("freigegeben", "veroeffentlicht", "im_highlight")  # gilt als "gut"

# Spalten, die nach der ersten Version dazugekommen sind: (Tabelle, Spalte, Typ)
MIGRATIONEN = [("clips", "short_pfad", "TEXT"), ("clips", "beschreibung", "TEXT"), ("clips", "highlight_id", "TEXT")]


def verbinde(pfad: Path | str) -> sqlite3.Connection:
    pfad = Path(pfad)
    if str(pfad) != ":memory:":
        pfad.parent.mkdir(parents=True, exist_ok=True)
    # isolation_level=None: kein verstecktes BEGIN – Transaktionen steuern wir selbst
    con = sqlite3.connect(pfad, timeout=30, isolation_level=None)
    try:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        con.execute("PRAGMA busy_timeout = 30000")
        if str(pfad) != ":memory:":
            con.execute("PRAGMA journal_mode = WAL")
        con.executescript(resources.files("clip_pipeline").joinpath("schema.sql").read_text(encoding="utf-8"))
        for tabelle, spalte, typ in MIGRATIONEN:
            if spalte not in {z["name"] for z in con.execute(f"PRAGMA table_info({tabelle})")}:
                con.execute(f"ALTER TABLE {tabelle} ADD COLUMN {spalte} {typ}")
    except BaseException:
        con.close()  # sonst bleibt die Datei (unter Windows) gesperrt
        raise
    return con


@contextmanager
def transaktion(con: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE holt sofort die Schreibsperre – so gibt es keine halben Änderungen."""
    con.execute("BEGIN IMMEDIATE")
    try:
        yield con
    except BaseException:
        con.execute("ROLLBACK")
        raise
    con.execute("COMMIT")


def protokoll(con: sqlite3.Connection, art: str, text: str, *, clip_id: int | None = None, match_id: str | None = None) -> None:
    con.execute(
        "INSERT INTO ereignisse (zeit, art, clip_id, match_id, text) VALUES (?, ?, ?, ?, ?)",
        (iso(jetzt()), art, clip_id, match_id, text),
    )


def status_wechsel(con: sqlite3.Connection, clip_id: int, von: tuple[str, ...], nach: str) -> bool:
    """Ändert den Status nur, wenn der alte Status passt. True = geändert."""
    if nach not in CLIP_STATUS:
        raise ValueError(f"Unbekannter Status {nach!r}")
    zeit = iso(jetzt())
    platzhalter = ", ".join("?" for _ in von)
    cursor = con.execute(
        f"""UPDATE clips
               SET status = ?, geaendert = ?,
                   entschieden = CASE WHEN ? IN ('freigegeben', 'verworfen') THEN ? ELSE entschieden END
             WHERE id = ? AND status IN ({platzhalter})""",
        (nach, zeit, nach, zeit, clip_id, *von),
    )
    if cursor.rowcount == 1:
        protokoll(con, "status", f"{'/'.join(von)} -> {nach}", clip_id=clip_id)
        return True
    return False


def clip(con: sqlite3.Connection, clip_id: int) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()


def match(con: sqlite3.Connection, match_id: str) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()


def merkmale(zeile: sqlite3.Row) -> dict[str, float]:
    return {k: float(v) for k, v in json.loads(zeile["merkmale"]).items()}


def anzahl_je_status(con: sqlite3.Connection) -> dict[str, int]:
    return {z["status"]: z["n"] for z in con.execute("SELECT status, COUNT(*) AS n FROM clips GROUP BY status")}
