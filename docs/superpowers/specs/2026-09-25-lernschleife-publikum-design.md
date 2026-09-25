# Lernschleife „Publikum“ – Design

Stand: 25.09.2026 · Branch `lernschleife-publikum` · abgestimmt am Handy (Abschnitte 1–5 einzeln freigegeben)

## 1. Ziel

Der Regisseur soll Shorts bauen, die auf TikTok laufen, und **schnell lernen, was ankommt** – vor allem beim
Publikum, in zweiter Linie bei dir. Du bleibst der Vorfilter: Kein Short und kein Zusammenschnitt geht ohne deine
Freigabe raus. Zielbild in einem halben Jahr: Der Bot legt dir die richtigen Shorts vor, du gibst frei, und du
kannst an einer Zahl ablesen, ob er besser wird (Trefferquote seiner Erwartung gegen dein Urteil und gegen das
Publikum).

Deine Hypothesen, die das System prüfen soll: Multikills/Serien · Sniper/weite Distanz · lustige Fails mit
Mic-Reaktion · Clutch/Endgame/Victory.

Rahmen (mit dir festgelegt): Publikum ist das Hauptsignal · TikTok-Zahlen kommen per Screenshot **und** später
per Display API · jeder dritte Short darf ein Experiment sein · Ziel ist ein Short pro Tag.

## 2. Ist-Zustand und warum er nicht reicht

- **Vorbewertung** (`vorbewertung.py`, `lernen.py`): fünf lineare Gewichte, paarweise gelernt aus Battles und
  Freigaben. Zwei Merkmale (`lautstaerke`, `kommentar`) sind immer 0 – faktisch lernt das Modell nur
  „Kills gegen Victory gegen Länge“. Wichtige Informationen aus dem Replay-JSON (Waffe, Bot-Opfer, Match-Phase,
  Umgehauen-werden) werden in `replay.py` verworfen.
- **Regisseur** (`regie.py`, `regie_lernen.py`, `lernbot.py`): eigene Intensitäts-Formel, Lernen per Handregeln
  aus 👍/👎 und sechs Gründen. Ein 👍 auf einen Short mit sechs Momenten gibt allen sechs +0,5 – das System
  erfährt nie, *welcher* Moment gut war. Nach jeder Bewertung baut der Bot einen komplett neuen Entwurf
  (Abwechslung 70 %), sodass dein Urteil keiner einzelnen Änderung zuzuordnen ist. Der Spannungsbogen ist fest
  (Höhepunkt am Schluss) – für TikTok vermutlich falsch, aber nie geprüft.
- **Publikum:** Regisseur-Entwürfe fließen nach der Bewertung nirgends hin (kein Upload-Paket, kein Link). Für
  Einzelclip-Shorts werden Links gespeichert (`veroeffentlichungen`), aber keine Zahlen. Es gibt kein einziges
  Publikumssignal im System.

Der Flaschenhals ist also nicht der Algorithmus, sondern **Information pro Antipper** und **fehlende Merkmale**.

## 3. Nicht-Ziele

- Kein automatisches Posten (API-Uploads sind ohne App-Review nur privat sichtbar, siehe README).
- Kein neuronales Netz, kein Bild-Modell. Alles bleibt linear, deterministisch und erklärbar (CLAUDE.md:
  „einfach vor schlau“, Lernen „einfach und nachvollziehbar“).
- Kein Umbau des n8n-Vertrags, keine Änderung der Session-Schnittliste, kein Wecken von pve-big.
- Kein YouTube in dieser Stufe (Datenmodell ist plattformneutral, YouTube kommt später mit derselben Struktur).

## 4. Architektur in einem Bild

```
Replay + Aufnahmen ─► Merkmale je Moment (neu: Waffe, Bot, Phase, Clutch, Mic) ─┐
                                                                                 ▼
                              ┌──────────── Moment-Modell (eine Bewertung für Clip-Bot und Regisseur) ◄──┐
                              ▼                                                                          │
Clip-Bot: ✅/🗑️, Battles ──► Paare (schnell) ──────────────────────────────────────────────────────────┤
                              │                                                                          │
Regisseur: compose nach Rezept (Hook · Länge · Tempo · Art) ─► Lern-Bot: Erwartung, 👍/👎, bester/schwächster,
           kontrollierte Variante ─► Freigabe ─► 📦 Upload-Paket ─► du postest ─► /link ─► posts        │
                              ▲                                                                          │
                              │            Screenshot (claude -p, Leserecht) + Display API ─► publikum_messungen
                              │                                                                          │
                       Rezept-Lerner ◄──── Publikums-Score (nach 7 Tagen) ─► Paare (langsam) ────────────┘
                              ▲
                  Wochenbericht: Regeln + 1× claude -p (nur Vorschläge, du tippst „testen“)
```

Zwei Schleifen: **schnell** (dein Urteil, täglich, ab Tag 1 wirksam) und **langsam** (Publikum, wöchentlich,
erste belastbare Aussagen nach ~4 Wochen bei einem Post pro Tag). Beide speisen dieselben zwei Lerner.

## 5. Datenmodell (neue Datei `src/clip_pipeline/publikum.sql`, portables SQL wie E3)

Bestehende Tabellen und ihre CHECK-Bedingungen werden **nicht** verändert. Neue Zustände leben in neuen Tabellen.

```sql
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
```

Neue Spalten an bestehenden Tabellen (per `ALTER TABLE … ADD COLUMN`, idempotent in `db.migriere`):
`clips.mic_stand TEXT` (NULL = unbekannt, sonst Zeitpunkt der Whisper-Analyse), `entwuerfe.rezept TEXT`
(JSON, das benutzte Rezept), `entwuerfe.upload_pfad TEXT` (Upload-Fassung). `clips.merkmale` bekommt die neuen
Schlüssel (JSON, keine Spaltenänderung).

## 6. Publikums-Score

Berechnung in `publikum.py`, `score_fuer(post, messungen, vergleichsbasis)`, deterministisch:

1. **Messung wählen:** die Messung, die dem Alter `[publikum].alter_tage` (7) am nächsten liegt, frühestens ab
   `mindest_alter_tage` (3). Vorher bleibt `score` NULL („noch nicht bewertet“).
2. **Komponenten:**
   - Wiedergabe `r = min(1.2, wiedergabe_s / dauer_s)`; fehlt `wiedergabe_s`: `r = voll_prozent / 100`; fehlt
     beides: `r = None`.
   - Engagement `e = (likes + 2·shares + saves + kommentare) / max(views, 1)`.
   - Reichweite `v = ln(1 + views)`.
3. **Vergleichsbasis:** die letzten `[publikum].fenster` (20) bewerteten Posts derselben Plattform. Jede
   Komponente wird robust standardisiert: `z = (x − Median) / (1,4826 · MAD)`, MAD mindestens 0,05, `z` auf
   ±2,5 begrenzt. Unter 5 Posts in der Basis: `z = 0` für alle (kein Lernen aus dem Nichts, Score bleibt 0 mit
   Vermerk „Basis zu klein“).
   *Geändert 25.09. (Florian, R3):* Minimum **je Teil** aus `[publikum.mad_minimum]` statt pauschal 0,05; die
   benutzten Minima stehen in `score_teile.mad_minimum` – siehe `docs/ENTSCHEIDUNGEN.md` (R3, A44).
4. **Score** `= 0,5·z_r + 0,3·z_e + 0,2·z_v`; ohne Wiedergabe: `0,6·z_e + 0,4·z_v` und Vermerk „ohne Wiedergabe“.
5. `score_teile` speichert `r, e, v, z_r, z_e, z_v, Median/MAD der Basis, Messungs-ID, Vermerke`.

Warum relativ: Follower-Wachstum und die Verteilung durch TikTok (erste Welle Glück oder Pech) dürfen nicht als
„gelernt“ durchgehen. Warum Wiedergabe zuerst: Sie ist das einzige Signal, das direkt Auswahl und Dramaturgie
misst; Views messen vor allem TikTok.

Der Score wird von `pipeline publikum bewerten` gesetzt (täglicher Timer, siehe Betrieb) – einmal je Post, danach
unverändert (`bewertet_utc`). Das hält Paare und Rezept-Stände stabil.

## 7. Zahlen erfassen

### 7.1 Screenshot an den Lern-Bot (Stufe 1)
- Handler für Fotos (nur `erlaubt`-Nutzer). Bildunterschrift `#17` = Post-Nummer; ohne Nummer antwortet der Bot
  mit Knöpfen der letzten fünf Posts ohne Messung der letzten 24 h (`pl:<post_id>:`), das Bild wartet so lange
  in `tmp` (höchstens 10 min, dann verworfen mit Hinweis).
- Auswertung in einem Thread: `claude -p --allowedTools "Read" --output-format json` mit festem Prompt
  (`templates/screenshot-prompt.txt`) und dem Bildpfad. Erwartete Antwort nach
  `schemas/publikum.schema.json`: `{views, likes, kommentare, shares, saves, wiedergabe_s, voll_prozent}` –
  jedes Feld optional (null), Zahlen als Ganzzahl bzw. Sekunden. Zeitangaben wie „0:07“ oder „7,3 s“ wandelt der
  Prompt in Sekunden; die Pipeline prüft trotzdem.
- **Plausibilität:** Gegenüber der letzten Messung desselben Posts dürfen views/likes/kommentare/shares/saves
  nicht sinken; `wiedergabe_s ≤ 1,5 · dauer_s`; `voll_prozent ≤ 100`. Verstoß → Bot zeigt die gelesenen Zahlen
  und fragt „Stimmt das? ✅ / ✏️ von Hand“. Nichts wird ungeprüft gespeichert.
- **Fallback Hand:** Antwort `views likes wiedergabe voll%` (Leerzeichen-getrennt, „–“ für unbekannt), z. B.
  `1240 61 6.8 34`. Quelle `hand`.
  *Geändert 25.09. (Florian, R4):* optional dahinter `kommentare shares saves`, also 4 oder 7 Werte
  (`1240 61 6.8 34 3 5 2`) – siehe `docs/ENTSCHEIDUNGEN.md` (R4, A41).
- Kosten: ein Aufruf je Screenshot. `[lernbot].screenshot_claude = false` schaltet auf Hand-Eingabe um.
- Das Bild wird nach der Auswertung gelöscht (kein Bildarchiv – nur die Zahlen und das JSON).

### 7.2 TikTok Display API (Stufe 4)
- Einrichtung (du, mit Anleitung in `docs/PUBLIKUM.md`): App auf developers.tiktok.com, Produkte Login Kit +
  Display API, Scopes `user.info.basic`, `video.list`; **Sandbox** mit deinem Konto als Testnutzer (bis 10),
  kein Review. `TIKTOK_CLIENT_KEY`/`TIKTOK_CLIENT_SECRET` in `.env`.
- Rücksprung: statische Seite `https://clip-battle.de/tiktok/callback` (nur HTML: zeigt den `code`-Parameter
  groß an). `pipeline publikum anmelden` gibt die Anmelde-URL aus (auch der Bot per `/tiktok`), du öffnest sie am
  Handy, kopierst den Code, `/tiktok <code>` tauscht ihn gegen Tokens. Tokens in
  `/var/lib/clip-pipeline/tiktok.json` (0600), Refresh automatisch, nie im Repo, nie im Log.
- `pipeline publikum holen`: `/v2/video/list/` (Felder `id, create_time, share_url, duration, view_count,
  like_count, comment_count, share_count`), seitenweise bis 60 Tage zurück. Zuordnung zu `posts`: erst
  `video_id`, sonst `gepostet_utc` ± 30 min und Dauer ± 2 s (dann wird `video_id` nachgetragen). Nicht
  zuordenbare Videos werden einmalig gemeldet („3 Videos ohne Post – /link nachtragen“).
- Ausfall: einmal am Tag eine Meldung (Schlüssel `publikum:api:<datum>`), sonst still. Liefert die Sandbox
  keine Zähler, bleibt Screenshot der einzige Weg – das reicht für alles Weitere (Wiedergabe kommt ohnehin nur
  von dort).

## 8. Moment-Modell (eine Bewertung für alle)

### 8.1 Merkmale (`vorbewertung.MERKMALE`, alle in `clips.merkmale` und `momente.merkmale`)

| Merkmal | Wert | Quelle |
|---|---|---|
| `kill_punkte` | wie heute (1/3/6/10 je Gruppe) | Replay |
| `victory_royale` | 0/1 | Replay |
| `platzierung` | `1 / platz` (Sieg 1,0, Platz 10 = 0,1), unbekannt 0 – überschneidet sich beim Sieg bewusst mit `victory_royale`, beide haben eigene Gewichte | Replay `ich.platzierung` |
| `sniper` | Anteil der Kills mit Sniper-Waffe (0..1) | `eliminierungen[].waffe` (GunType) |
| `nahkampf` | Anteil Shotgun/SMG/Pickaxe (0..1) | dito |
| `bot_opfer` | Anteil der Kills, deren Opfer Bots waren (0..1) | `eliminierungen[].eliminiert_bot` |
| `phase` | Zeitpunkt des ersten Ereignisses des Moments im Match, 0 (Start) … 1 (Ende) | `t_ms / laenge_ms` |
| `endgame` | 1, wenn beim Kill ≤ `[merkmale].endgame_spieler` (10) Spieler übrig | `spieler_gesamt` − Killfeed-Einträge bis dahin |
| `clutch` | 1, wenn ≤ 30 s vor einem Kill selbst umgehauen und 10 s danach nicht gestorben | Replay-Ereignisse `knock_erlitten`, `tod` |
| `mic_lachen`, `mic_jubel`, `mic_frust` | Wortzähler aus dem Transkript, je auf 3 begrenzt | Whisper (`stimmung.py`) |
| `mic_laut` | Anzahl lauter Mikro-Spitzen, auf 3 begrenzt | `jubel_laut` |
| `spitzen` | Spielton-Spitzen, auf 4 begrenzt | `spitzen` |
| `laenge` | wie heute | Schnittliste |

`replay.py` gibt je Ereignis zusätzlich `waffe`, `opfer_bot` und `verbleibend` (Spieler) aus – reine Erweiterung
der Datenklasse `MeinEreignis`, keine Änderung der Kill-Regel von 24.09. `replay2json` bleibt unverändert (die
Felder stehen schon im JSON). Die GunType-Werte werden in `config/pipeline.toml` `[merkmale.waffen]` auf
`sniper`/`nahkampf`/`sonstige` abgebildet; unbekannte Werte werden einmal gemeldet und zählen als `sonstige`.

Startgewichte (`[vorbewertung.startgewichte]`, Vorschlag – deine Vorgaben, nicht Gelerntes): `platzierung 2`,
`sniper 1`, `nahkampf 0`, `bot_opfer −2`, `phase 0,5`, `endgame 1`, `clutch 2`, `mic_lachen 1`, `mic_jubel 1`,
`mic_frust 0,5`, `mic_laut 0,5`, `spitzen 0,25`; bisherige bleiben. Leine wie heute (±50 %, mindestens ±0,5) –
für Merkmale mit Startwert 0 also ±0,5.

### 8.2 Eine Bewertung für Clip-Bot und Regisseur
- `vorbewertung.bewerte(merkmale, gewichte)` bleibt die einzige Score-Funktion. `regie.kandidaten()` ersetzt
  seine `intensitaet`-Formel durch `moment_score = bewerte(merkmale, aktuelle Gewichte)`; obendrauf wie bisher
  Stimmungs-Bonus, Freigabe +1, Elo-Term, Moment-Bonus, Abwechslungs-Abzug. `intensitaet` (für den Bogen)
  wird zu `moment_score` ohne Boni.
- Die Stimmungs-Analyse liefert die Mic-Merkmale in `momente.merkmale`; `pipeline stimmung --clips` (neu,
  Hintergrundschritt nach `render`, `nice 15`, unter der Pipeline-Sperre, ohne Claude) schreibt sie zusätzlich in
  `clips.merkmale` und setzt `clips.mic_stand`. Bis dahin zählen Mic-Merkmale 0 und `/gewichte` weist die Zahl
  der Clips „ohne Mic-Analyse“ aus.

### 8.3 Lernen aus drei Paar-Quellen (`lernen.py`)
- Wie heute: paarweises Nachjustieren, Mindestmenge, wachsendes Vertrauen, Leine, „nie schlechter als der
  Start“. Neu: jedes Paar trägt ein Gewicht (Schrittweite × Gewicht) und eine Quelle.
- **Battles** (1,0) und **Freigaben** (`[lernen].gewicht_freigabe = 0.5`, Paare wie heute pro Abend gebildet).
  Das halbe Gewicht für Freigaben ist eine Änderung gegenüber heute: Ein Battle ist ein direkter Vergleich, eine
  Freigabe nur ein indirekter.
- **Publikum** (1,0): Für je zwei bewertete Posts derselben Plattform und Art mit `|score_A − score_B| ≥
  [publikum].paar_abstand` (0,5): bei `art = clip` das Paar (Clip_A > Clip_B); bei `art = entwurf` das Paar der
  **Hook-Momente** (der erste Moment im Short). Höchstens `[publikum].max_paare` (200) jüngste Paare, damit alte
  Posts nicht dominieren.
- Trefferquote getrennt: `trefferquote_nutzer` (Battles + Freigaben) und `trefferquote_publikum`. Die Schranke
  „nie schlechter als der Start“ gilt für **beide** Quoten einzeln – gelernte Gewichte werden nur aktiv, wenn sie
  weder dein Urteil noch das Publikum schlechter vorhersagen als die Startgewichte. `/gewichte` zeigt beide
  Quoten und die Anzahl je Quelle; laufen sie auseinander („du magst X, das Publikum Y“), steht das im Text.

## 9. Rezept-Lerner (`rezepte.py`)

### 9.1 Stellschrauben und Stufen (`[rezepte]`)

| Stellschraube | Stufen | Umsetzung in `compose` |
|---|---|---|
| `hook` | `stark_zuerst` · `teaser` · `aufbau` | stark_zuerst: stärkster Moment als Segment 1, dann absteigend, zweitstärkster am Schluss · teaser: 1,5 s um den letzten Kill des stärksten Moments als Segment 1 (harter Schnitt), dann Aufbau wie heute, Höhepunkt am Ende in voller Länge · aufbau: heutiger Bogen |
| `laenge` | `kurz` (≤ 20 s) · `mittel` (25–32 s) · `lang` (40–45 s) | Ziel-Dauer je Stufe, `dauer_faktor` aus `regie_lernen` multipliziert weiterhin |
| `tempo` | `beat1` · `beat2` | `beats_pro_schnitt` 1 bzw. 2 (die „zu hektisch“-Regel hebt weiter nur an) |
| `machart` | `roh` · `regie` | roh: `shorts.rendere` des besten Clips ohne Musik (heutiges Upload-Paket) · regie: Regisseur mit Musik |

Ein Rezept gilt je Post; für Zusammenschnitte (16:9) bleibt vorerst der heutige Bogen (kein Publikumssignal,
zu selten für Lernen). `posts.art` (clip/entwurf) sagt, *woher* das Video kommt; `rezept.machart` (roh/regie)
ist die Stellschraube, die das Publikum bewertet – ein über den Clip-Bot geposteter Einzelclip ist `art = clip`
mit `machart = roh`.

### 9.2 Schätzung und Auswahl
- Je Plattform, Stellschraube und Stufe: `n`, `mittel` (Ø Publikums-Score der bewerteten Posts mit dieser Stufe),
  `unsicherheit = 1 / sqrt(n + 1)`. Aus der schnellen Schleife zusätzlich `nutzer_siege / nutzer_paare` aus
  kontrollierten Varianten (Abschnitt 10.3).
- **Rangwert** je Stufe `= mittel + [rezepte].nutzer_gewicht (0,5) · (siegquote − 0,5)`; ohne Nutzer-Paare
  zählt nur `mittel`. Stufen ohne Daten haben `mittel = 0`.
- **Ausbeuten** (zwei von drei Posts): je Stellschraube die Stufe mit dem höchsten Rangwert; bei Gleichstand die
  mit weniger `n`.
- **Experiment** (jeder dritte Post je Plattform, gezählt aus `posts`): je Stellschraube ein Zufallswert
  `x_stufe ~ Normal(rangwert, unsicherheit)` mit festem Seed aus Plattform und der Anzahl bisher erzeugter
  Experiment-Entwürfe (reproduzierbar, und ein abgelehntes Experiment führt zu einem neuen Los); die Stufe mit
  dem größten `x` gewinnt. Liegt eine Hypothese mit `status = 'testen'` und `posts_offen > 0` vor, bekommt deren
  Stellschraube deren Stufe, die übrigen werden gezogen.
- Ein Experiment-Entwurf trägt `rezept.experiment = true`; im Bot steht „🧪 Experiment: Hook ‚teaser‘“.
  Verwirfst du ihn, entsteht kein Post und kein Score – die Stufe wird als „vom Nutzer abgelehnt“ gezählt
  (`nutzer_paare + 1`, kein Sieg).
- **Der Post des Tages:** Nach „Session vorbei“ (bestehender Timer `clip-sitzungen`) oder per `/entwurf` baut
  der Bot den Entwurf nach Rezept – Ausbeuten oder Experiment, je nach Zähler. Bei `machart = roh` rendert der
  Lern-Bot den besten freigegebenen Clip des Abends als Short (`shorts.rendere`) und bietet dafür sein
  Upload-Paket an; der Post ist dann `art = clip`.
- `rezept_stand` wird bei jedem `pipeline publikum bewerten` neu berechnet (Cache); `/lernstand` zeigt je
  Stellschraube alle Stufen mit `mittel ± unsicherheit (n)`.

## 10. Lern-Bot: Erwartung, Knöpfe, kontrollierte Variante, Upload-Paket

### 10.1 Bildunterschrift eines Entwurfs
```
🎬 Entwurf #41 · Short 31 s · Stimmung episch · Rezept: Hook teaser · mittel · beat1 · regie · 🧪 Experiment
1 Triple 🎯 · 2 Clutch · 3 Lachen · 4 Double · 5 Victory
Erwartung: 👍 74 %          (fehlt bei < 10 bewerteten Entwürfen: „Erwartung: noch keine“)
🎵 …
```
Die Moment-Zeile nennt je Segment das stärkste Merkmal (Kürzel aus `MERKMAL_NAMEN`, höchstens 5 Segmente,
sonst „1–7“).

### 10.2 Knöpfe (Callback-Daten ≤ 64 Byte, `lernbot.parse` erweitert)
- Reihe 1 wie heute: 👍 `d:<eid>:1` · 👎 `d:<eid>:-1`.
- Danach Gründe wie heute plus `hook` („🪝 Hook zieht nicht“) und `reihenfolge` („🔁 Reihenfolge falsch“).
- Neu darunter: „🏆 bester“ `hb:<eid>:<nr>` und „🥱 schwächster“ `hs:<eid>:<nr>` je Segment-Nummer (1–7), dann
  „✅ fertig“ `x:<eid>:`. Gespeichert in `entwurf_bewertungen` als neue Spalten `bester INTEGER`,
  `schwaechster INTEGER` (ALTER TABLE).
- Wirkung in `regie_lernen.aktuelle`: bester → Moment-Bonus **+1**, schwächster → **−1**; die pauschalen ±0,5 für
  alle Momente **entfallen**, sobald bester oder schwächster gesetzt ist (sonst wie heute). `hook` und
  `reihenfolge` zählen für die benutzte Hook-Stufe als Nutzer-Niederlage (`nutzer_paare + 1`, kein Sieg);
  `reihenfolge` wird zusätzlich im Wochenbericht gezählt (die Reihenfolge innerhalb des Bogens ist keine
  Stellschraube; häuft sich der Grund, ist das ein Signal für eine neue).

### 10.3 Kontrollierte Variante (ersetzt „nach ✅ sofort ein komplett neuer Entwurf“)
- Nach ✅ baut der Bot den nächsten Entwurf mit **denselben Momenten** (ohne den „schwächsten“, sonst
  unverändert) und genau **einer** geänderten Stellschraube: reihum `hook → laenge → tempo`, jeweils eine
  andere Stufe als die aktuelle, und zwar die mit der größten Unsicherheit. Abwechslungs-Abzug ist für diesen
  Entwurf ausgesetzt. Unterschrift: „Variante zu #41: gleiche Momente, Hook ‚stark_zuerst‘ statt ‚teaser‘“.
- Dein 👍/👎 auf beide Entwürfe ergibt ein **Nutzer-Paar** für die geänderte Stellschraube (👍/👎 gegen 👎/👍;
  gleiches Urteil = kein Paar). Zusätzlich fragt der Bot nach der zweiten Bewertung „Welcher ist besser? A / B /
  egal“ (`ab:<eid_a>:<eid_b>:a|b|s` – bleibt unter 64 Byte); diese Antwort ersetzt das Paar aus den Daumen und
  zählt voll (Sieg oder Niederlage der geänderten Stufe, „egal“ = kein Paar).
- Nach zwei Varianten desselben Moment-Satzes kommt wieder ein frischer Entwurf (mit Abwechslung), damit die
  Auswahl nicht einschläft. `[lernbot].varianten_je_satz = 2`.

### 10.4 Vom Entwurf zum Post
- Unter einem mit 👍 bewerteten Entwurf erscheint „📦 Upload-Paket“ (`pk:<eid>:`). Das Paket entspricht dem des
  Clip-Bots: Upload-Fassung als Datei, Caption (Musik-Quellenangabe **muss** in die Beschreibung, aus
  `tracks.quelle`), Checkliste mit Häkchen je Plattform (`veroeffentlichungen` bleibt Clip-Sache; für Entwürfe
  liegt der Stand in `posts`). Im Lern-Bot legt `/link <entwurf-nr> <url>` den Post an; im Clip-Bot bleibt
  `/link <clip-nr> <url>` – zwei Bots, zwei Nummernkreise, kein Präfix nötig.
- **Upload-Fassung** (`entwurf.upload_fassung`): 1080×1920, `crf 20`, Bitrate so begrenzt, dass die Datei unter
  `[vorschau].max_mb` (48) bleibt (bei 45 s ≈ 8,5 Mbit/s), VA-API wenn da, sonst CPU; auf dem Mini, nie
  pve-big. `render-entwurf --final` bleibt für später (Puffer-Betrieb: Exit 2, siehe PUFFER.md R5).
- Einzelclips, die über das bestehende Upload-Paket des Clip-Bots gepostet werden: dort legt `link_speichern`
  bzw. `plattform_erledigt` zusätzlich den `posts`-Datensatz an (`art = clip`, Rezept abgeleitet:
  `hook = stark_zuerst`, `laenge` nach Dauer, `tempo = none`, `machart = roh`). Das ist die einzige Änderung
  am Clip-Bot neben 10.6.

### 10.5 Erwartung und Trefferquote
- Beim Senden (Clip oder Entwurf) berechnet `erwartung.py` eine Wahrscheinlichkeit und schreibt sie fest
  (`erwartungen`). Modell: logistische Funktion `σ(a·z_moment + b·rezept + c)` mit `z_moment` = Moment-Score
  (Clip) bzw. Mittel der Moment-Scores (Entwurf), standardisiert gegen die letzten 50 gesendeten; `rezept` =
  Rangwert-Summe des Rezepts (Clip: 0). `a, b, c` werden bei jedem Aufruf aus allen bisherigen Urteilen neu
  geschätzt (50 Schritte Gradientenabstieg, L2 0,1, Start `a = 1, b = 0,5, c = 0`) – deterministisch. Unter 10
  Urteilen der jeweiligen Art: keine Erwartung.
- Treffer: `(wahrschein ≥ 0,5) == (Urteil positiv)`. `/lernstand` und `/gewichte` zeigen die Trefferquote der
  letzten 20 und aller Urteile, getrennt für Clips und Entwürfe. Das ist die Kennzahl für „lernt er?“.

### 10.6 Clip-Bot: Battles nach Unsicherheit
`aktionen.neues_battle` wählt A wie heute (wenigste Battles) und B so, dass `|score_A − score_B|` nach dem
Moment-Modell am kleinsten ist – dort ist das Modell am unsichersten, dein Urteil sagt am meisten (statt
Elo-Nähe); Rückfall auf Elo-Nähe, solange das Modell inaktiv ist (`lernen.berechne(...).aktiv = False`).
Die Clip-Nachricht bekommt eine Zeile „Erwartung: ✅ 78 %“ (`texte.clip_text`).

## 11. Wochenbericht und Analyst (`bericht.py`, `analyst.py`)

### 11.1 Regel-Bericht (immer, ohne KI)
Sonntag `[analyst].uhrzeit` (18:00), Timer `clip-wochenbericht`, versandt über den Lern-Bot
(`lern_meldungen`, Schlüssel `woche:<ISO-Woche>`):
1. Posts der Woche: Anzahl, davon bewertet; bester und schwächster Post mit Score-Komponenten in Worten
   („#17: Wiedergabe 0,91 (Top 10 %), Likes je View über Median, Views unter Median“).
2. Rezept-Tabelle je Stellschraube: Stufe, `mittel`, `n`, Nutzer-Siegquote.
3. Hypothesen-Check (deine vier): Ø Score der Posts, deren Hook-Moment das Merkmal trägt (`kill_punkte ≥ 3`,
   `sniper > 0`, `mic_lachen + mic_laut > 0`, `clutch = 1 oder endgame = 1`), jeweils gegen alle anderen, mit `n`.
4. Trefferquoten (Nutzer, Publikum, Erwartung), Anzahl Claude-Aufrufe der Woche, Clips ohne Mic-Analyse.
5. Offene Hypothesen des Analysten und Ergebnis der getesteten (`posts_offen = 0` → `geprueft` mit Ø Score
   der drei Posts gegen die vorherige Stufe).

### 11.2 Analyst (ein `claude -p` pro Woche)
- Dossier `pipeline wochenbericht --dossier` (Markdown, ≤ 30 kB): Posts der letzten `[analyst].wochen` (4) mit
  Rezept, Hook-Merkmalen, Score-Komponenten; Gewichte und Quoten; Rezept-Tabelle; Ergebnisse getesteter
  Hypothesen; deine Gründe-Häufigkeiten aus dem Lern-Bot.
- Aufruf `claude -p --allowedTools "Read" --output-format json`, Prompt `templates/analyst-prompt.txt`, Antwort
  nach `schemas/analyst.schema.json`: `beobachtungen: [string ≤ 200]`, `hypothesen: [{these, stellschraube ∈
  Stellschrauben | null, stufe | null, erwartung}]` (höchstens 3), `warnungen: [string]`. Prüfung: Schema, nur
  bekannte Stellschrauben/Stufen, keine Post-Nummern, die es nicht gibt. Unbrauchbar → nur Regel-Bericht plus
  Zeile „Analyst: keine gültige Antwort“.
- Versand: Beobachtungen als Text, jede Hypothese mit Knöpfen „🧪 testen“ (`t:<id>:1`) / „✖️“ (`t:<id>:0`).
  „testen“ setzt `status = 'testen', posts_offen = 3`; die nächsten drei Experiment-Plätze bekommen diese Stufe.
- Der Analyst ändert nie Gewichte, Konfiguration oder Rezept-Stände. `[analyst].claude = false` lässt nur den
  Regel-Bericht laufen.

## 12. Betrieb

- Alles auf dem Mini (LXC `clips`), im Puffer-Betrieb; kein Schritt weckt pve-big (keine `wecken=True`-Aufrufe
  in neuem Code; Test prüft das wie in `test_speicher_wecken.py`).
- Timer (`deploy/systemd/`): `clip-publikum.timer` täglich `[publikum].uhrzeit` (10:00; ruft
  `pipeline publikum holen` (nur mit API) und `pipeline publikum bewerten`) · `clip-wochenbericht.timer` So 18:00
  · Mic-Analyse hängt an `render` (Hintergrund, `nice 15`, gleiche `flock`-Sperre, `[merkmale].mic = true`).
- Konfiguration (`config/pipeline.toml`, neue Abschnitte): `[publikum]` (`alter_tage 7`, `mindest_alter_tage 3`,
  `fenster 20`, `paar_abstand 0.5`, `max_paare 200`, `uhrzeit "10:00"`, `gewichte {wiedergabe 0.5, engagement
  0.3, reichweite 0.2}`), `[rezepte]` (Stufen je Stellschraube, `experiment_jeder 3`, `nutzer_gewicht 0.5`),
  `[merkmale]` (`endgame_spieler 10`, `clutch_vor_s 30`, `clutch_nach_s 10`, `mic true`, `[merkmale.waffen]`),
  `[lernbot]` (`screenshot_claude true`, `varianten_je_satz 2`), `[analyst]` (`wochentag "So"`, `uhrzeit
  "18:00"`, `wochen 4`, `claude true`, `max_hypothesen 3`), `[tiktok]` (`callback_url`, `sandbox true`).
- Secrets: `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET` in `.env` (`.env.example` ergänzt); Tokens unter
  `/var/lib/clip-pipeline/tiktok.json`; Logs ohne Tokens (httpx auf WARNING wie heute).
- Claude-Aufrufe: je Screenshot einer, pro Woche einer, sonst wie heute. `/lernstand` zählt die Woche
  (`lern_meldungen`-unabhängig, Tabelle `ereignisse`, `art = 'claude'`).
- Idempotenz: `publikum holen`/`bewerten`/`wochenbericht` können beliebig oft laufen; Scores werden nie
  überschrieben; Meldungen je Schlüssel einmal.
- Ruhezeit: Meldungen aus `publikum:`/`woche:` warten die Ruhezeit ab (Erweiterung von `LEISE_MELDUNGEN`).

## 13. Tests (unittest, wie bisher)

- `test_publikum.py`: Score-Komponenten, robuste Standardisierung, Basis zu klein, ohne Wiedergabe, Messung
  nach Alter wählen, Paar-Bildung (Abstand, Art, Hook-Moment), Idempotenz von `bewerten`.
- `test_merkmale.py`: jedes neue Merkmal an konstruierten Replay-JSONs (Sniper, Bot-Opfer, Endgame aus dem
  Killfeed, Clutch mit/ohne Tod danach, Phase); unbekannter GunType → `sonstige` + Meldung.
- `test_lernen.py` (erweitert): drei Quellen mit Gewichten, getrennte Trefferquoten, Schranke gilt für beide.
- `test_rezepte.py`: Schätzung, Rangwert mit Nutzer-Paaren, Ausbeuten/Experiment mit festem Seed, Hypothese
  belegt den Experiment-Platz, abgelehntes Experiment zählt als Nutzer-Niederlage.
- `test_regie.py` (erweitert): die drei Hook-Stufen erzeugen die erwartete Segment-Reihenfolge; kein Kill
  angeschnitten; Längen-Stufen treffen ihr Ziel; kontrollierte Variante ändert genau eine Stellschraube und
  behält die Momente.
- `test_lernbot.py` (erweitert, Fake-Telegram): neue Knöpfe und Callback-Daten ≤ 64 Byte, bester/schwächster,
  A/B-Frage, Screenshot-Fluss mit gefälschter Claude-Ausgabe (gültig, kaputtes JSON, unplausible Zahlen →
  Rückfrage), Hand-Fallback, Upload-Paket für Entwürfe, `/link e41`.
- `test_erwartung.py`: Festschreiben beim Senden, keine Erwartung unter 10 Urteilen, Trefferquote.
- `test_tiktok.py`: Client mit aufgezeichneten Antworten (Seiten, Token-Refresh, Fehler), Zuordnung per
  `video_id` und per Zeit/Dauer, Ausfall-Meldung einmal am Tag.
- `test_bericht.py` / `test_analyst.py`: Regel-Bericht aus Beispieldaten, Dossier-Größe, Schema-Prüfung der
  Analyst-Antwort, „testen“ setzt `posts_offen`, ungültige Antwort → nur Regel-Bericht.
- `test_bot_app.py` (erweitert): `link_speichern` legt `posts` an; Battle-Paarung nach Unsicherheit mit Rückfall.
- Ende-zu-Ende (`test_ende_zu_ende.py` erweitert): Post → zwei Messungen → Score → Paar → neue Gewichte →
  geänderte Erwartung; Migration auf einer Datenbank im alten Stand.
- Kein neuer Code weckt pve-big (Prüfung wie `test_speicher_wecken.py`).

## 14. Stufen (jede für sich nutzbar, je Stufe: bauen mit Tests → Review → Installation nach Anleitung)

1. **Fundament:** `publikum.sql` + Migration, `posts` aus `/link` (Clip-Bot) und aus dem Lern-Bot (Upload-Paket,
   Upload-Fassung, `/link e<nr>`), Screenshot-Eingang mit Claude und Hand-Fallback, Score + `pipeline publikum
   bewerten` + Timer, `/publikum` (letzte Posts mit Zahlen und Score). *Fertig, wenn:* ein echter TikTok-Post
   per Screenshot gemessen und nach 3 Tagen bewertet ist.
2. **Merkmale und eine Bewertung:** `replay.py`-Erweiterung, neue Merkmale, `[merkmale.waffen]`, Mic-Hintergrund-
   schritt, gemeinsame Bewertung im Regisseur, Publikums-Paare in `lernen.py`, getrennte Trefferquoten,
   Erwartung + Trefferquote im Lern-Bot und Clip-Bot. *Fertig, wenn:* `/gewichte` beide Quoten zeigt und ein
   Clip mit Bot-Opfern sichtbar weniger Punkte bekommt.
3. **Rezepte:** `rezepte.py`, Hook-Stufen in `compose`, Experiment-Plätze, neue Knöpfe (bester/schwächster,
   hook, reihenfolge), kontrollierte Variante mit A/B-Frage, Battles nach Unsicherheit. *Fertig, wenn:* drei
   aufeinanderfolgende Entwürfe im Bot als Original, Variante und Experiment gekennzeichnet sind und
   `/lernstand` die Rezept-Tabelle zeigt.
4. **Display API:** TikTok-App, Rücksprungseite auf clip-battle.de, `/tiktok`, `publikum holen`, Zuordnung,
   Ausfall-Meldung. *Fertig, wenn:* die tägliche Messung ohne dein Zutun ankommt.
5. **Wochenbericht + Analyst:** Regel-Bericht, Dossier, Claude-Aufruf mit Schema, Hypothesen-Knöpfe.
   *Fertig, wenn:* ein Bericht mit mindestens einer testbaren Hypothese im Lern-Bot ankam und „testen“ den
   nächsten Experiment-Platz belegt hat.

Stufen 1–3 zuerst; sie tragen die Lernschleife. Erwartung: schnelle Schleife wirkt ab Stufe 3 sofort, die
Publikums-Schleife braucht bei einem Post pro Tag etwa vier Wochen Daten für die ersten belastbaren Aussagen.
Je Stufe ein eigener Implementierungsplan (writing-plans), damit jede Stufe für sich geprüft und installiert wird.

## 15. Risiken und offene Punkte

- **Sandbox liefert keine Zähler:** dann bleibt die API weg; der Screenshot-Weg trägt alles (bewusst so
  gereiht).
- **Screenshot-Layouts ändern sich:** Prompt und Schema sind tolerant (alle Felder optional), Plausibilität
  fängt Lesefehler; Hand-Fallback bleibt.
- **Wenig Daten:** Unter 5 bewerteten Posts gibt es keinen Score ≠ 0; das Modell lernt dann nur aus dir.
  Duplikate/Reposts auf TikTok vermeiden (kein A/B desselben Materials online – Varianten laufen nur im Bot).
- **Whisper-Last:** je Clip ~30–60 s CPU auf dem Mini, mit `nice` und Sperre; abschaltbar (`[merkmale].mic`).
- **Zusammenschnitte (16:9)** lernen in dieser Runde nur über das Moment-Modell, nicht über Rezepte.
- **Rücksprung über clip-battle.de:** braucht eine statische Datei auf dem Webspace (du legst sie an; Inhalt
  liefert Stufe 4).
- **Ordnungs-Grund „Reihenfolge falsch“** ist bewusst nur ein Zähler; wird er häufig, folgt eine Stellschraube
  für die Mitte des Bogens (nächste Runde).

## 16. Entscheidungen (Vorschlag für `docs/ENTSCHEIDUNGEN.md`, nach deinem OK)

- **E21** Publikum ist das Hauptsignal; zwei Schleifen (dein Urteil täglich, Publikum wöchentlich) speisen dieselben Lerner.
- **E22** Eine Moment-Bewertung für Clip-Bot und Regisseur; neue Merkmale aus dem vorhandenen Replay-JSON; Lernen bleibt linear, paarweise, gedeckelt.
- **E23** Rezepte als Stellschrauben mit Stufen; jeder dritte Post ein Experiment nach Unsicherheit; du gibst jeden Post frei.
- **E24** Zahlen zuerst per Screenshot (claude -p, Leserecht), Display API im Sandbox-Modus als zweite Stufe; Wiedergabezeit gibt es nur aus der App.
- **E25** Der Wochen-Analyst schlägt nur vor (Schema-geprüft), entscheidet nie; jede Hypothese wird per Knopf getestet.
- **E26** Der Clip-Bot wird minimal angefasst: `posts` aus `/link`, Erwartungs-Zeile, Battle-Paarung nach Unsicherheit. n8n-Vertrag und Session-Schnittliste unverändert.
