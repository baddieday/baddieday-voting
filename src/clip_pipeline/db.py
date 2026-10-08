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

# 27.09.: Genre aus dem NCS-Genre-Filter (Techno, Electronic Rock …) – der Regisseur bevorzugt [musik].genres_bevorzugt
MIGRATIONEN += [("tracks", "genre", "TEXT")]

# B5 (28.09.): Grund, warum ein Entwurf automatisch aussortiert wurde, bevor er dir gezeigt wurde ([lernbot].
# auto_schwelle), NULL = normal. Eigene Spalte statt neuem Status – die CHECK-Liste von entwuerfe.status ließe
# sich nur mit Tabellen-Umbau ändern (wie schon bei highlights.hochgeladen).
MIGRATIONEN += [("entwuerfe", "auto_verworfen", "TEXT")]
MIGRATIONEN += [("highlights", "entwurf_id", "INTEGER")]

# Additiv: historische Messungen und Bewertungen bleiben unverändert erhalten.
MIGRATIONEN += [("publikum_messungen", name, typ) for name, typ in (
    ("impressions", "INTEGER"), ("retention_prozent", "REAL"), ("follows", "INTEGER"),
    ("profilaufrufe", "INTEGER"), ("rewatches", "INTEGER"), ("skip_prozent", "REAL"), ("metriken", "TEXT"))]

# Auto-Freigabe im Clip-Bot (30.09., Florian: „nicht jeden Clip per Hand separat freigeben“): wer entschieden hat
# ('du' · 'auto' · NULL = offen oder Altbestand, zählt als du), seit wann der Clip offen liegt (Grundlage der Frist),
# was die Automatik beim Senden vorschlug, auf welchem Weg und warum. Eigene Spalten statt neuem Status – die
# CHECK-Liste von clips.status ließe sich nur mit Tabellen-Umbau ändern (wie bei highlights.hochgeladen).
MIGRATIONEN += [("clips", "freigabe_quelle", "TEXT"), ("clips", "vorgelegt", "TEXT"), ("clips", "auto_vorschlag", "TEXT"),
                ("clips", "auto_art", "TEXT"), ("clips", "auto_grund", "TEXT")]

# Cutter-Maßstab 1.0 (30.09., Spec §3.4): Teilnoten, Plan-Näherung und Tore je Kritik (JSON), Version der Messung
# (0 = nicht gemessen), des KI-Prompts (nur gleiche Versionen sind vergleichbar) und der Maßstab-Faktoren.
MIGRATIONEN += [("kritiken", "teile", "TEXT"), ("kritiken", "plan_teile", "TEXT"), ("kritiken", "tore", "TEXT"),
                ("kritiken", "mess_version", "INTEGER"), ("kritiken", "ki_version", "TEXT"),
                ("kritiken", "massstab_version", "INTEGER")]

# 🔥 Viral (05.10., viral.py): Mischung eines Viral-Videos (twist · highlight · fail), NULL = normaler Entwurf. Eigene
# Spalte statt neuem Format – die CHECK-Liste von entwuerfe.format ließe sich nur mit Tabellen-Umbau ändern; gerendert
# wird ein Viral-Video wie jeder Short (format = 'short').
MIGRATIONEN += [("entwuerfe", "variante", "TEXT")]
# Stufe 1 (07.10.): Telegram-Nachricht je Lern-Meldung – die Statuszeile „🎮 Abend erkannt“ wird später durch das
# Video ersetzt (gelöscht) bzw. zu „kein Video, weil …“ umgeschrieben, statt eine zweite Nachricht zu schicken
MIGRATIONEN += [("lern_meldungen", "tg_nachricht_id", "INTEGER")]
# Merkliste (08.10., Florian tippt nie etwas zweimal): was nach deinem Tipp noch kommt – neue Fassung nach einem Grund,
# Upload-Paket nach ✅ – als JSON mit Versuchen und Erledigt-Vermerk (lernbot.folge_merken); NULL = nichts
MIGRATIONEN += [("entwurf_bewertungen", "folge", "TEXT")]
# Stufe 3 (08.10., „der Clip-Bot meldet sich nur bei Problemen“): 1 = Routine (Start oder glattes Ende einer
# Übertragung) – der stille Clip-Bot vermerkt sie im einfachen Modus nur (bot.app._nur_probleme); NULL = normal
MIGRATIONEN += [("meldungen", "routine", "INTEGER")]


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


def meldung(con: sqlite3.Connection, schluessel: str, text: str, routine: bool = False) -> bool:
    """Legt eine Nachricht für den Bot an – je Schlüssel nur einmal. True = neu angelegt. routine (08.10.): nichts zu
    tun, nichts schiefgegangen (Start oder glattes Ende einer Übertragung) – der stille Clip-Bot vermerkt sie nur."""
    cursor = con.execute(
        "INSERT OR IGNORE INTO meldungen (schluessel, text, erstellt, routine) VALUES (?, ?, ?, ?)",
        (schluessel, text, iso(jetzt()), 1 if routine else None),
    )
    return cursor.rowcount == 1


def lern_meldung(con: sqlite3.Connection, schluessel: str, text: str) -> bool:
    """Nachricht für den Lern-Bot (nicht den Clip-Bot) – je Schlüssel nur einmal. True = neu angelegt."""
    cursor = con.execute(
        "INSERT INTO lern_meldungen (schluessel, text, erstellt) VALUES (?, ?, ?) ON CONFLICT (schluessel) DO NOTHING",
        (schluessel, text, iso(jetzt())),
    )
    return cursor.rowcount == 1


def status_wechsel(con: sqlite3.Connection, clip_id: int, von: tuple[str, ...], nach: str, *,
                   quelle: str = "du", grund: str | None = None) -> bool:
    """Ändert den Status nur, wenn der alte Status passt. True = geändert.

    quelle: wer entscheidet ("du" oder "auto", Auto-Freigabe 30.09.). Bei freigegeben/verworfen wird sie in
    clips.freigabe_quelle gemerkt, bei gesendet/vorbewertet (wieder offen) geleert, sonst (veröffentlicht, im
    Highlight) bleibt sie. Bei "auto" steht der grund mit im Protokoll: „gesendet -> freigegeben (auto: …)“."""
    if nach not in CLIP_STATUS:
        raise ValueError(f"Unbekannter Status {nach!r}")
    if quelle not in ("du", "auto"):
        raise ValueError(f"Unbekannte Quelle {quelle!r}")
    zeit = iso(jetzt())
    platzhalter = ", ".join("?" for _ in von)
    cursor = con.execute(
        f"""UPDATE clips
               SET status = ?, geaendert = ?,
                   entschieden = CASE WHEN ? IN ('freigegeben', 'verworfen') THEN ? ELSE entschieden END,
                   freigabe_quelle = CASE WHEN ? IN ('freigegeben', 'verworfen') THEN ?
                                          WHEN ? IN ('gesendet', 'vorbewertet') THEN NULL
                                          ELSE freigabe_quelle END
             WHERE id = ? AND status IN ({platzhalter})""",
        (nach, zeit, nach, zeit, nach, quelle, nach, clip_id, *von),
    )
    if cursor.rowcount == 1:
        zusatz = f" (auto: {grund})" if quelle == "auto" else ""
        protokoll(con, "status", f"{'/'.join(von)} -> {nach}{zusatz}", clip_id=clip_id)
        return True
    return False


def clip(con: sqlite3.Connection, clip_id: int) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()


def match(con: sqlite3.Connection, match_id: str) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()


def merkmale(zeile: sqlite3.Row) -> dict[str, float]:
    return {k: float(v) for k, v in json.loads(zeile["merkmale"]).items()}


def hart_verworfen_sql(alias: str = "") -> str:
    """SQL-Bedingung „von dir verworfen“ (Auto-Freigabe 30.09.): Nur dein 🗑️ schließt einen Clip aus Regie, Highlight,
    Stimmung und Mikro aus – was die Automatik aussortiert, bleibt Material (weich). NULL zählt als du (sichere Seite).
    alias: Tabellen-Kürzel mit Punkt, z. B. "c." → „(c.status = 'verworfen' AND c.freigabe_quelle IS NOT 'auto')“."""
    return f"({alias}status = 'verworfen' AND {alias}freigabe_quelle IS NOT 'auto')"


def hart_verworfen(status: str | None, quelle: str | None) -> bool:
    """Python-Gegenstück zu hart_verworfen_sql (für Zeilen, die schon gelesen sind, z. B. im Regisseur)."""
    return status == "verworfen" and quelle != "auto"


def anzahl_auto(con: sqlite3.Connection) -> dict[str, int]:
    """Automatisch entschiedene Clips für /status: {"frei": n, "weg": m}."""
    zeile = con.execute(
        """SELECT COALESCE(SUM(status = 'freigegeben'), 0) AS frei, COALESCE(SUM(status = 'verworfen'), 0) AS weg
             FROM clips WHERE freigabe_quelle = 'auto'""").fetchone()
    return {"frei": int(zeile["frei"]), "weg": int(zeile["weg"])}


def ohne_mic_analyse(con: sqlite3.Connection) -> int:
    """Wie viele Clips haben noch keine vollständige Mic-Analyse? (mic_stand leer, nicht von dir verworfen)

    Die eine Zählung für „offen“ in `pipeline stimmung --clips` (mikro.clips_nachziehen) und „ohne Mic-Analyse“ in
    /gewichte (lernen.berechne) – so zeigen beide immer dieselbe Zahl. Von dir Verworfene zählen nicht: Die misst
    niemand mehr nach (automatisch aussortierte schon, hart_verworfen_sql). Fehler: sqlite3-Fehler gehen an den Aufrufer.
    Beispiel: 3 Clips ohne mic_stand, davon 1 verworfen → 2.
    """
    return int(con.execute(
        f"SELECT COUNT(*) FROM clips WHERE mic_stand IS NULL AND NOT {hart_verworfen_sql()}").fetchone()[0])


def anzahl_je_status(con: sqlite3.Connection) -> dict[str, int]:
    return {z["status"]: z["n"] for z in con.execute("SELECT status, COUNT(*) AS n FROM clips GROUP BY status")}
