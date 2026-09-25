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

# Lernschleife „Publikum“ (Spec §5): Zeitpunkt der Mic-Analyse (NULL = unbekannt), benutztes Rezept (JSON) und Pfad
# der Upload-Fassung 1080×1920 eines Entwurfs. Eigene Zeile statt die obere zu verlängern: So kann ein anderer
# Branch (Regisseur 2.0) oben ergänzen, ohne dass sich die Änderungen beim Zusammenführen in die Quere kommen.
MIGRATIONEN += [("clips", "mic_stand", "TEXT"), ("entwuerfe", "rezept", "TEXT"), ("entwuerfe", "upload_pfad", "TEXT")]

# Stufe 2 (Spec §8.3): Publikums-Quote und Paar-Zahlen je Gewichts-Version – eigene Zeile (Merge-freundlich wie oben).
# quellen: JSON {"battle": n, "freigabe": n, "publikum": n}
MIGRATIONEN += [("gewichte", "trefferquote_publikum", "REAL"), ("gewichte", "trefferquote_publikum_start", "REAL"),
                ("gewichte", "quellen", "TEXT")]

# Upload nur für Highlights (Entscheidung 25.09.): Häkchen „✅ Hochgeladen“ am Highlight-Video (Zeitpunkt, NULL = offen).
# Eigene Spalte statt neuem Status – die CHECK-Liste von highlights.status ließe sich nur mit Tabellen-Umbau ändern.
MIGRATIONEN += [("highlights", "hochgeladen", "TEXT")]


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
        # regie.sql: Tabellen des Regisseurs (Sprint 09/2026) · lager.sql: Abgleich Puffer → Lager (E19)
        # publikum.sql: Lernschleife „Publikum“ (posts, Messungen, …; Spec §5)
        for datei in ("schema.sql", "regie.sql", "lager.sql", "publikum.sql"):
            con.executescript(resources.files("clip_pipeline").joinpath(datei).read_text(encoding="utf-8"))
        for tabelle, spalte, typ in MIGRATIONEN:
            if spalte not in {z["name"] for z in con.execute(f"PRAGMA table_info({tabelle})")}:
                try:
                    con.execute(f"ALTER TABLE {tabelle} ADD COLUMN {spalte} {typ}")
                except sqlite3.OperationalError as e:
                    # Zwei Prozesse (z. B. beide Bots oder ein Bot und render nach einem Update) verbinden
                    # gleichzeitig: Beide sahen die Spalte als fehlend, der andere war beim ALTER schneller.
                    # Dann ist die Spalte da – genau das wollten wir. Jeder andere Fehler fliegt weiter.
                    if "duplicate column name" not in str(e):
                        raise
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


def meldung(con: sqlite3.Connection, schluessel: str, text: str) -> bool:
    """Legt eine Nachricht für den Bot an – je Schlüssel nur einmal. True = neu angelegt."""
    cursor = con.execute(
        "INSERT OR IGNORE INTO meldungen (schluessel, text, erstellt) VALUES (?, ?, ?)", (schluessel, text, iso(jetzt()))
    )
    return cursor.rowcount == 1


def lern_meldung(con: sqlite3.Connection, schluessel: str, text: str) -> bool:
    """Nachricht für den Lern-Bot (nicht den Clip-Bot) – je Schlüssel nur einmal. True = neu angelegt."""
    cursor = con.execute(
        "INSERT INTO lern_meldungen (schluessel, text, erstellt) VALUES (?, ?, ?) ON CONFLICT (schluessel) DO NOTHING",
        (schluessel, text, iso(jetzt())),
    )
    return cursor.rowcount == 1


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


def ohne_mic_analyse(con: sqlite3.Connection) -> int:
    """Wie viele Clips haben noch keine vollständige Mic-Analyse? (mic_stand leer, Status nicht verworfen)

    Die eine Zählung für „offen“ in `pipeline stimmung --clips` (mikro.clips_nachziehen) und „ohne Mic-Analyse“ in
    /gewichte (lernen.berechne) – so zeigen beide immer dieselbe Zahl. Verworfene zählen nicht: Die misst niemand
    mehr nach. Fehler: sqlite3-Fehler gehen an den Aufrufer.
    Beispiel: 3 Clips ohne mic_stand, davon 1 verworfen → 2.
    """
    return int(con.execute(
        "SELECT COUNT(*) FROM clips WHERE mic_stand IS NULL AND status <> 'verworfen'").fetchone()[0])


def anzahl_je_status(con: sqlite3.Connection) -> dict[str, int]:
    return {z["status"]: z["n"] for z in con.execute("SELECT status, COUNT(*) AS n FROM clips GROUP BY status")}
