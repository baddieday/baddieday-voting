-- Datenbank der Clip-Pipeline (SQLite). Nachschauen mit:  sqlite3 pipeline.db ".tables"
-- Zeiten als ISO-Text in UTC (z. B. 2026-09-21T19:53:29.453Z), Pfade relativ zur Speicher-Wurzel.

PRAGMA foreign_keys = ON;

-- Jede erkannte Aufnahme (Nvidia/SteelSeries) mit ihrer echten Zeitspanne
CREATE TABLE IF NOT EXISTS aufnahmen (
    pfad        TEXT PRIMARY KEY,
    groesse     INTEGER NOT NULL,
    geaendert   REAL    NOT NULL,           -- Datei-Änderungszeit, um Änderungen zu bemerken
    quelle      TEXT    NOT NULL,
    start_utc   TEXT    NOT NULL,
    ende_utc    TEXT    NOT NULL,
    dauer_s     REAL    NOT NULL,
    tonspuren   INTEGER NOT NULL,
    fps         REAL,
    ereignisse  TEXT    NOT NULL DEFAULT '[]',  -- JSON
    lautheit_i  REAL,                           -- integrierte Lautheit (LUFS), wird bei Bedarf gemessen
    erfasst     TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_aufnahmen_zeit ON aufnahmen (start_utc, ende_utc);

-- Ein Match = ein Fortnite-Replay
CREATE TABLE IF NOT EXISTS matches (
    id             TEXT PRIMARY KEY,           -- z. B. 2026-09-21_21-42-22
    replay_pfad    TEXT NOT NULL UNIQUE,
    start_utc      TEXT NOT NULL,
    ende_utc       TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'neu' CHECK (status IN ('neu', 'verarbeitet', 'fehler')),
    kill_quelle    TEXT,                       -- replay | steelseries | nvidia | keine
    platzierung    INTEGER,
    victory_royale INTEGER NOT NULL DEFAULT 0,
    kills          INTEGER,
    build          TEXT,
    hinweise       TEXT,                       -- Warnungen oder Fehlermeldung
    erstellt       TEXT NOT NULL,
    geaendert      TEXT NOT NULL
);

-- Ein Clip = ein Highlight (eine oder mehrere Kill-Gruppen)
CREATE TABLE IF NOT EXISTS clips (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id         TEXT    NOT NULL REFERENCES matches (id),
    nr               INTEGER NOT NULL,
    status           TEXT    NOT NULL DEFAULT 'neu' CHECK (status IN (
                         'neu', 'vorbewertet', 'gesendet', 'freigegeben', 'verworfen',
                         'veroeffentlicht', 'im_highlight')),
    titel            TEXT    NOT NULL,
    typ              TEXT    NOT NULL,         -- einzel | double | triple | multi
    kills            INTEGER NOT NULL,
    max_gruppe       INTEGER NOT NULL,         -- größte Kill-Serie (3+ wird dauerhaft archiviert)
    victory_royale   INTEGER NOT NULL DEFAULT 0,
    kill_zeiten      TEXT    NOT NULL,         -- JSON-Liste (UTC)
    start_utc        TEXT    NOT NULL,
    ende_utc         TEXT    NOT NULL,
    quelle_pfad      TEXT    NOT NULL,         -- Aufnahme, aus der geschnitten wurde
    quelle_start_s   REAL    NOT NULL,
    quelle_ende_s    REAL    NOT NULL,
    merkmale         TEXT    NOT NULL,         -- JSON
    punkte           REAL    NOT NULL,
    begruendung      TEXT    NOT NULL,
    beschreibung     TEXT,                     -- von decide (claude -p), geprüft; sonst Textbausteine
    highlight_id     TEXT,                     -- in welchem Highlight-Video der Clip vorkam
    gewichte_version INTEGER NOT NULL DEFAULT 0,
    clip_pfad        TEXT,
    vorschau_pfad    TEXT,
    short_pfad       TEXT,
    tg_nachricht_id  INTEGER,
    tg_file_id       TEXT,                     -- Telegram merkt sich das Video: Battles gehen auch ohne Speicher
    elo              REAL    NOT NULL DEFAULT 1500,
    elo_rd           REAL    NOT NULL DEFAULT 350,
    battles          INTEGER NOT NULL DEFAULT 0,
    siege            INTEGER NOT NULL DEFAULT 0,
    niederlagen      INTEGER NOT NULL DEFAULT 0,
    entschieden      TEXT,
    erstellt         TEXT    NOT NULL,
    geaendert        TEXT    NOT NULL,
    UNIQUE (match_id, nr)
);
CREATE INDEX IF NOT EXISTS idx_clips_status ON clips (status);

CREATE TABLE IF NOT EXISTS battles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    clip_a          INTEGER NOT NULL REFERENCES clips (id),
    clip_b          INTEGER NOT NULL REFERENCES clips (id),
    ergebnis        TEXT CHECK (ergebnis IN ('a', 'b', 's')),  -- NULL = offen, s = übersprungen
    elo_a_vorher    REAL,
    elo_b_vorher    REAL,
    elo_a_nachher   REAL,
    elo_b_nachher   REAL,
    tg_nachricht_id INTEGER,
    erstellt        TEXT NOT NULL,
    entschieden     TEXT
);

-- Upload-Nachverfolgung: Jeder freigegebene Clip muss auf alle Pflicht-Plattformen
CREATE TABLE IF NOT EXISTS veroeffentlichungen (
    clip_id   INTEGER NOT NULL REFERENCES clips (id),
    plattform TEXT    NOT NULL,        -- youtube | tiktok | clipbattle
    erledigt  TEXT,                    -- Zeitpunkt; NULL = noch offen
    url       TEXT,
    PRIMARY KEY (clip_id, plattform)
);

-- Highlight-Videos (alle 2 Wochen) – werden im Bot freigegeben
CREATE TABLE IF NOT EXISTS highlights (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL UNIQUE,   -- --id aus n8n, z. B. highlight-2026-09-25
    datei           TEXT    NOT NULL,
    vorschau        TEXT,
    clips           INTEGER NOT NULL,
    dauer           TEXT    NOT NULL,
    musik           TEXT,
    status          TEXT    NOT NULL DEFAULT 'neu' CHECK (status IN ('neu', 'gesendet', 'freigegeben', 'verworfen')),
    tg_nachricht_id INTEGER,
    erstellt        TEXT    NOT NULL,
    entschieden     TEXT
);

-- Jede neu gelernte Gewichtung bekommt eine Versionsnummer (0 = Startgewichte)
CREATE TABLE IF NOT EXISTS gewichte (
    version            INTEGER PRIMARY KEY,
    werte              TEXT    NOT NULL,      -- JSON
    datenbasis         INTEGER NOT NULL,
    vertrauen          REAL    NOT NULL,
    trefferquote       REAL,
    trefferquote_start REAL,
    erstellt           TEXT    NOT NULL
);

-- Protokoll: was ist wann passiert
CREATE TABLE IF NOT EXISTS ereignisse (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    zeit     TEXT NOT NULL,
    art      TEXT NOT NULL,
    clip_id  INTEGER,
    match_id TEXT,
    text     TEXT
);
