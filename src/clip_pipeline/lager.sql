-- Getrennter Betrieb (E19): was aus dem Puffer (Mini) im Lager (pve-big) nachweislich angekommen ist.
-- Nur portables SQL wie regie.sql (Umzug nach Postgres, docs/ENTSCHEIDUNGEN.md E3).

-- Je Datei im Puffer: im Lager zurückgelesen und gleich (SHA-256). Nur was hier steht, gilt als gesichert.
CREATE TABLE IF NOT EXISTS lager (
    relativ        TEXT PRIMARY KEY,   -- Pfad relativ zur Wurzel, '/' als Trenner (in Puffer und Lager gleich)
    groesse        INTEGER NOT NULL,
    mtime_ns       INTEGER NOT NULL,   -- mtime der bestätigten Puffer-Datei (ns)
    sha256         TEXT    NOT NULL,
    bestaetigt     TEXT    NOT NULL,   -- ISO-UTC: im Lager zurückgelesen und gleich
    lager_relativ  TEXT    NOT NULL,   -- meist = relativ; bei Konflikt versionierter Name
    zuerst_gesehen TEXT                -- ISO-UTC: erstmals im Puffer gesehen (für B5)
);

-- Protokoll der Läufe (pipeline lager abgleich | uebernehmen)
CREATE TABLE IF NOT EXISTS lager_laeufe (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    art      TEXT NOT NULL,            -- abgleich | uebernahme
    start    TEXT NOT NULL,
    ende     TEXT,
    ergebnis TEXT                      -- JSON
);
