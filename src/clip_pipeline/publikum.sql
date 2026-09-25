-- Lernschleife „Publikum“ (Spec §5): Posts, Publikumszahlen, Rezept-Stand, Hypothesen, Erwartungen.
-- Wortgetreu aus der Spec übernommen. Portables SQL wie regie.sql (E3): INTEGER PRIMARY KEY ohne AUTOINCREMENT,
-- Zeiten als ISO-UTC-Text, JSON als Text. Nur CREATE … IF NOT EXISTS – db.verbinde führt die Datei bei jeder
-- Verbindung aus. Bestehende Tabellen und ihre CHECKs bleiben unverändert; neue Spalten an alten Tabellen stehen
-- in db.MIGRATIONEN (sie dürfen hier nicht referenziert werden, sonst bricht es auf einer alten Datenbank).
-- Stufe 1 füllt posts und publikum_messungen; rezept_stand, hypothesen und erwartungen folgen in Stufe 2–5.

-- Ein Post = ein veröffentlichtes Video (Einzelclip-Short oder Regisseur-Entwurf) auf einer Plattform
CREATE TABLE IF NOT EXISTS posts (
    id            INTEGER PRIMARY KEY,
    art           TEXT NOT NULL CHECK (art IN ('clip', 'entwurf')),
    ziel          TEXT NOT NULL,           -- "clip:<id>" oder "entwurf:<id>" (NULL-sicherer Schlüssel)
    clip_id       INTEGER,                 -- art = clip
    entwurf_id    INTEGER,                 -- art = entwurf
    plattform     TEXT NOT NULL,           -- tiktok | youtube
    url           TEXT,
    video_id      TEXT,                    -- aus dem Link (tiktok.com/@…/video/<id>), sonst per Zeit zugeordnet
    gepostet_utc  TEXT NOT NULL,           -- Zeitpunkt des /link bzw. des Häkchens
    dauer_s       REAL NOT NULL,
    rezept        TEXT NOT NULL,           -- JSON: {"hook": …, "laenge": …, "tempo": …, "machart": …, "experiment": bool}
    merkmale      TEXT NOT NULL,           -- JSON: Momente mit Merkmalen, Hook-Moment, Stimmung, Musik
    experiment    INTEGER NOT NULL DEFAULT 0,
    score         REAL,                    -- Publikums-Score, gesetzt nach [publikum].alter_tage
    score_teile   TEXT,                    -- JSON: Komponenten und Vergleichsbasis (nachvollziehbar)
    bewertet_utc  TEXT,
    erstellt      TEXT NOT NULL,
    UNIQUE (plattform, ziel)
);

-- Zeitreihe der Zahlen je Post (Zahlen wachsen noch – deshalb Messungen, nicht eine Zahl)
CREATE TABLE IF NOT EXISTS publikum_messungen (
    id            INTEGER PRIMARY KEY,
    post_id       INTEGER NOT NULL REFERENCES posts (id),
    gemessen_utc  TEXT NOT NULL,
    quelle        TEXT NOT NULL CHECK (quelle IN ('api', 'screenshot', 'hand')),
    views         INTEGER,
    likes         INTEGER,
    kommentare    INTEGER,
    shares        INTEGER,
    saves         INTEGER,
    wiedergabe_s  REAL,                    -- Ø Wiedergabezeit (nur Screenshot/Hand)
    voll_prozent  REAL,                    -- „vollständig angesehen“ (nur Screenshot/Hand)
    roh           TEXT,                    -- JSON: API-Antwort bzw. Claude-JSON, für Nachprüfungen
    erstellt      TEXT NOT NULL
);

-- Stand des Rezept-Lerners je Stellschraube und Stufe (jederzeit aus posts neu berechenbar; Tabelle = Cache
-- für /lernstand und den Bericht)
CREATE TABLE IF NOT EXISTS rezept_stand (
    plattform     TEXT NOT NULL,
    stellschraube TEXT NOT NULL,           -- hook | laenge | tempo | machart
    stufe         TEXT NOT NULL,
    n             INTEGER NOT NULL,
    mittel        REAL NOT NULL,           -- Ø Publikums-Score
    unsicherheit  REAL NOT NULL,           -- 1 / sqrt(n + 1)
    nutzer_siege  INTEGER NOT NULL DEFAULT 0,   -- aus kontrollierten Varianten (schnelle Schleife)
    nutzer_paare  INTEGER NOT NULL DEFAULT 0,
    berechnet_utc TEXT NOT NULL,
    PRIMARY KEY (plattform, stellschraube, stufe)
);

-- Vorschläge des Wochen-Analysten; „testen“ legt die Experiment-Plätze fest
CREATE TABLE IF NOT EXISTS hypothesen (
    id            INTEGER PRIMARY KEY,
    woche         TEXT NOT NULL,           -- ISO-Woche, z. B. 2026-W40
    these         TEXT NOT NULL,
    stellschraube TEXT,
    stufe         TEXT,
    erwartung     TEXT,
    status        TEXT NOT NULL DEFAULT 'offen' CHECK (status IN ('offen', 'testen', 'abgelehnt', 'geprueft')),
    posts_offen   INTEGER NOT NULL DEFAULT 0,   -- so viele Experiment-Posts noch auf diese Stufe legen
    ergebnis      TEXT,
    erstellt      TEXT NOT NULL
);

-- Erwartung des Bots VOR deinem Urteil (wird beim Senden festgeschrieben, nie nachträglich neu gerechnet)
CREATE TABLE IF NOT EXISTS erwartungen (
    art           TEXT NOT NULL CHECK (art IN ('clip', 'entwurf')),
    ziel_id       INTEGER NOT NULL,
    wahrschein    REAL NOT NULL,           -- 0..1, dass du freigibst / 👍 gibst
    grundlage     TEXT NOT NULL,           -- JSON: Moment-Score, Rezept-Schätzung, Modellversion
    erstellt      TEXT NOT NULL,
    PRIMARY KEY (art, ziel_id)
);
