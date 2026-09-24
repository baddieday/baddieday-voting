# PLAN – Clip-Pipeline

## Sprint „Regisseur“ (24.–28.09.2026) – Änderungen am Plan

**Neues Zielbild (gilt vor allem, was unten steht):** Zentrale mit Warteschlange und Postgres **auf dem Mini**;
der VPS (n8n) ist nur noch Zusatz. Bis dahin bleibt der n8n-Vertrag unverändert in Betrieb.
Alle neuen Tabellen sind in portablem SQL geschrieben (`src/clip_pipeline/regie.sql`, Entscheidung E3).

Neu dazugekommen (Bedienung: `docs/REGIE.md`, Begründungen: `docs/ENTSCHEIDUNGEN.md`, Verlauf: `docs/SPRINT-LOG.md`):

| Ziel | Stand | Befehl |
|---|---|---|
| 1 Sicherheitsnetz pve-big | ✅ gebaut + getestet · 🏠 SSH-Zugang auf pve-big fehlt | `pipeline big …`, Timer `clip-big-waechter` |
| 2 Bestandsaufnahme | ✅ gebaut · 🏠 echter Lauf durch die Vor-Ort-Sitzung | `pipeline bestand` |
| 3 Material auf den Mini | ✅ gebaut · 🏠 braucht Ziel 1 auf dem echten System | `pipeline material` |
| 4 Stimmung | ✅ gebaut, Whisper echt getestet (künstliche Stimme) | `pipeline stimmung` |
| 5 Lern-Bot | ✅ gebaut, ohne Netzwerk getestet · 🏠 Token fehlt | `pipeline lernbot`, Dienst `clip-lernbot` |
| 6 Regisseur | ✅ gebaut + getestet | `pipeline compose` |
| 7 Rendern | ✅ Entwurf (CPU) echt getestet · VA-API/NVENC nur Befehlsaufbau | `pipeline render-entwurf [--final]` |
| 8 Lernen | ✅ gebaut + getestet | `regie_lernen.py`, `/lernstand` |
| 9 Session vorbei | ✅ vorbereitet (Standard aus), unter PowerShell 7 getestet | `SessionVorbeiMinuten`, `pipeline sitzungen` |

Offene Stufen aus dem alten Plan, die der Sprint berührt:
- Stufe 5c Whisper: jetzt vorhanden (`pip install -e .[whisper]`), bisher nur für die Stimmung genutzt –
  Untertitel in Shorts fehlen weiterhin.
- Stufe 7 Highlight-Video: bleibt; der Regisseur ist die bessere Grundlage (Bogen, Beat-Schnitt). Vorschlag:
  `highlight` später auf `compose --format zusammenschnitt` umstellen.

---

## Umsetzungsstand (23.09.2026, nachmittags)

Gebaut und getestet (55 Tests + echter Lauf mit dem Match vom 21.09.; deine n8n-Workflows 1–3 geprüft):

| Stufe | Stand |
|---|---|
| 0 Fundament | Anleitung fertig (`docs/SERVER.md`) – **machst du** |
| 1 Kill-Daten | ✅ `replay2json` (Windows + Linux-Build), Zeitabgleich kalibriert (< 1 s), Rekorder als Rückfall |
| 2 Vorbewertung + Clips + Vorschau | ✅ Multikill-Ketten, Punkte, Lautstärke, CapCut-Clips (feste Bildrate, beide Tonspuren) |
| 3 Telegram-Bot | ✅ Freigabe, Rückgängig, Battle + Elo (Variante A), Rangliste, Outbox, Whitelist |
| 4 n8n | ✅ Vertrag aus CLAUDE.md umgesetzt (`bin/pipeline`, prepare/analyze/decide/render, flock, JSON, Webhook vom Gaming-PC); Workflows baust/prüfst du |
| 5 Shorts + Caption + Upload | ✅ Short 9:16 mit Overlay + Endcard, Caption, Upload-Paket, Nachverfolgung YouTube/TikTok/clip-battle.de · ⏳ Untertitel (Whisper) |
| 6 Lernen | ✅ paarweises Nachjustieren, Mindestmenge, Leine, `/gewichte` |
| 7 Highlight-Video | ✅ Auswahl nach Elo, Überblendungen, lizenzierte Musik mit Ducking, Freigabe im Bot, Wake-on-LAN |
| Übertragung | ✅ `windows/Uebertragung.ps1` (alle 2 min, nur kopieren, Wake-on-LAN, Meldung an n8n) |
| Aufräumen | ✅ nach 182 Tagen recyceln, Multikills ab 3 Kills dauerhaft archivieren |

### Nächste Schritte (brauchen deine Entscheidung)
1. **Stufe 0 aufsetzen** nach `docs/SERVER.md` und die n8n-Workflows gegen den Vertrag prüfen.
2. **Whisper** (Untertitel + Merkmal „Kommentar“): Installation von `faster-whisper` freigeben?
   Hinweis: SteelSeries speichert „Game“ + „Chat“ – ob dein Mikro mit drauf ist, prüfen wir an einer Aufnahme.
3. **Inoffizielle API für clip-battle.de** (Repo `E:\GIT\clip-battle`), Entwurf:
   - `POST /api/pipeline/clips` mit `Authorization: Bearer <token>`; Token nur als Hash in der DB, an deinen
     Account gebunden, im Profil erzeug- und widerrufbar; CSRF-frei nur für diesen Token-Weg; Rate-Limit.
   - Body `{"url": "https://youtube.com/shorts/…", "titel": "…"}` → derselbe Provider-Resolver, dieselbe
     Duplikat- und Moderationsprüfung wie das Web-Formular (Link-only bleibt).
   - Pipeline-Seite: sobald `/link <nr> <youtube-url>` eingetragen ist, reicht der Bot automatisch ein und hakt
     „clip-battle.de“ ab.
   - Offen: Auf welchem Branch? (aktuell `codex/release-1.7.12-battle-ui`)
4. **YouTube/TikTok-API**: Entwickler-Apps anlegen und Audit beantragen – dann kann der Bot selbst hochladen.
5. **Musik**: lizenzierte Titel nach `musik/` legen, je Titel eine `<titel>.lizenz.txt`.

> Die Abschnitte unten sind die Analyse vom Vormittag (Phase 1). Wo sie vom Umsetzungsstand abweichen
> (z. B. zwei Proxmox-Hosts, Nvidia + SteelSeries, Vertrag mit n8n), gelten dieser Abschnitt und CLAUDE.md.

---

Stand Phase 1: 23.09.2026 vormittags · **Analyse & Plan**

Inhalt
0. Kurzfassung
1. Analyse: clips_voter & Geschwister-Repos
2. Entwurf: Telegram-Port
3. Entwurf: Vorbewertung & Lernen
4. Kill-Daten: Parser, Zeitabgleich, Fallback
5. Entwurf: Caption-Generator & Werbung
6. Architektur-Überblick (Datenfluss, Ordner, Status, DB, Schnittliste)
7. Stufenplan
8. Annahmen (bitte korrigieren)
9. Rückfragen

---

## 0. Kurzfassung

- `clips_voter` liegt **nicht** unter `./clips_voter`, sondern unter `E:\GIT\Netzwerk\work\clips_voter` (GitHub `baddieday/clips_voter`). Analysiert wurde der lokale Stand – inklusive vieler **uncommitteter** Änderungen.
- **clips_voter ist nicht die Codebasis von clip-battle.de.** clip-battle.de ist das Repo `E:\GIT\clip-battle` (GitHub `baddieday/clip-battle`, Version 1.7.12). clips_voter gehört zu clip-voter.de.
- **Widerspruch zu CLAUDE.md:** clips_voter hat **kein aktives Battle/Elo**. Bewertet wird dort mit Buttons „Banger / Maybe / Skip“ (5 / 3 / 1 Sterne). Elo existiert nur
  - in einer **nie committeten** Datei `src/community_clip_os.py` (keine Aufrufer, keine Tabelle, kein Test) und
  - in einer **abweichenden** Variante im Schwester-Repo `cliphub` (`E:\GIT\Netzwerk\Clipvoterde`). → Rückfrage 4.
- Replay-Parser: **FortniteReplayDecompressor** (C#, NuGet-Paket `FortniteReplayReader` 3.1.0 vom 13.09.2026, MIT-Lizenz). Er ist aktiv gepflegt, geprüft bis Fortnite-Build 42.10 und liefert Eliminierungen mit Zeitstempel, den Replay-Besitzer, die Platzierung und die Startzeit des Replays.
- clip-battle.de nimmt **nur Links** an (YouTube/Shorts, Twitch; TikTok nur im Sandbox-Test). Es gibt keine Datei-Uploads und keinen API-Schlüssel. Daraus folgt: erst auf YouTube Shorts hochladen, dann den Link per „Teilen → Clip Battle“ einreichen.
- Stufenplan 0–7. Große Stufen sind in Teilstufen aufgeteilt. **Erste Baustufe nach deinem OK: 1a `replay2json`.** Sie läuft auch schon auf dem Gaming-PC, während du Stufe 0 (Proxmox) aufsetzt.

---

## 1. Analyse

### 1.1 Welche Repos gibt es?

| Ordner | GitHub | Was | Stand |
|---|---|---|---|
| `E:\GIT\Netzwerk\work\clips_voter` | baddieday/clips_voter | Windows-App: Aufnahmen → Highlights → Review per Telegram/Discord | 84 Commits, letzter am 19.04.2026, viele uncommittete Änderungen |
| `E:\GIT\Netzwerk\Clipvoterde` | baddieday/cliphub | „Community Clip OS“: Discord-Duelle mit Elo, Wochen-Rangliste | 5 Commits, 09.–10.04.2026 |
| `E:\GIT\clip-battle` | baddieday/clip-battle | **clip-battle.de** (Web + Android-App) | v1.7.12, 142 Commits, letzter am 31.08.2026 |

### 1.2 clips_voter in einfachen Worten

**Zweck:** Eine lokale Windows-App. Sie beobachtet einen Aufnahme-Ordner, sucht in jeder Aufnahme die beste Stelle, schneidet sie im Hochformat, schickt sie zur Bewertung an Telegram/Discord und lernt aus deinen Bewertungen.

**Was mit einer Aufnahme passiert (Architektur):**
1. `watcher/ingest.py` – beobachtet den Ordner und erkennt Duplikate per Datei-Hash.
2. `analyzer/heuristic.py` – misst Lautstärke, Szenenwechsel und Bewegung und findet so das beste Zeitfenster. Außerdem liest es Kill-Marker aus **SteelSeries-GG**-Metadaten (nicht Nvidia!).
3. `analyzer/ai_scorer.py` – lässt OpenAI oder Ollama Kategorie, Hook und Caption bestimmen.
4. `analyzer/feedback.py` – korrigiert den KI-Score anhand deiner Sterne.
5. `renderer/tiktok_renderer.py` – FFmpeg rendert 9:16 oder 16:9, mit Text und gemischten Tonspuren.
6. `telegram_bot/bot.py` – schickt Video und Buttons und speichert deine Antwort.
7. `compiler/pipeline.py` – baut einen Wochen-Zusammenschnitt mit Überblendungen (xfade).
8. `maintenance/editor_export.py` – exportiert nummerierte Clips plus CSV/JSON für Schnittprogramme.

**Tech-Stack:** Python 3.11, SQLAlchemy 2 + SQLite, python-telegram-bot ≥ 20.7 (async), FastAPI (lokale Oberfläche), PySide6 (Windows-Fenster), FFmpeg, numpy/scipy/librosa, openai. Lizenz: GPL-3.0 – es ist dein eigener Code, die Übernahme ist also unproblematisch.

**Datenmodell (Tabellen):**
- `clips`: Status, Heuristik-Werte, KI-Werte, Schnittfenster, Captions, Render-Pfad, Telegram-IDs, `human_rating` (1–5)
- `compilations` (Zusammenschnitte), `jobs` (Warteschlange), `system_events` (Protokoll)
- 4 Tabellen für den zentralen Review-Hub (`external_review_submissions`, `review_votes`, `review_hub_invites`, `review_hub_device_tokens`)

**Bewertung heute:** Banger (5) / Maybe (3) / Skip (1), dazu Gründe (zu früh, zu spät, zu kurz, zu lang, langweilig, großartig). **Keine Battles.**

### 1.3 Battles, Voting und Elo – genau erklärt

**Elo in einem Satz:** Jeder Clip hat eine Zahl (Rating). Gewinnt ein Clip gegen einen stärkeren, steigt seine Zahl stark. Gewinnt er gegen einen schwächeren, steigt sie nur wenig.

- Erwartung, dass A gewinnt: `E_A = 1 / (1 + 10^((R_B − R_A) / 400))`
- Nach dem Duell: `R_A_neu = R_A + K · (S_A − E_A)`, mit `S_A` = 1 bei Sieg und 0 bei Niederlage
- Beispiel: Zwei neue Clips mit je 1500 → Erwartung 0,5. Mit K = 48 bekommt der Gewinner +24 (1524), der Verlierer −24 (1476).
- **K** ist die „Schrittgröße“: Große K lernen schnell, kleine K sind stabil.

**Zwei Varianten gefunden – sie unterscheiden sich:**

| | Variante A: clips_voter `src/community_clip_os.py` | Variante B: cliphub `services/ranking_service.py` |
|---|---|---|
| Git-Status | nie committet, nirgends aufgerufen, keine Tabelle, kein Test | in cliphub aktiv benutzt |
| Startwert | 1500 | 1000 |
| Unsicherheit | 350, × 0,94 je Duell, min. 75 | 350, × 0,92 je Duell, min. 70 |
| K-Faktor | 48 (unter 10 Duellen), 32 (unter 25), danach 24 | 24 + min(Unsicherheit, 200) / 25 → 32 bis ca. 26,8 |
| „Überspringen“ | Rating bleibt, nur Unsicherheit × 0,94 | Rating bleibt, nur Unsicherheit × 0,97 (min. 75) |
| Ranglisten-Wert | Rating − 0,5 · Unsicherheit | Rating − Unsicherheit · Faktor (aus Konfig) |
| Rang-Status | vorläufig → gewertet (8 Duelle, 5 Voter) → siegfähig (15 Duelle, 8 Voter) | gleich |
| Saison | nur Text-Schlüssel „JJJJ-season-1“ + ISO-Woche, kein Reset | Duelle nur innerhalb einer Woche (`week_start`) |
| Paarbildung | nicht vorhanden | offenes Duell zuerst, sonst erster Clip vs. Clip mit ähnlichstem Rating und wenigsten Duellen |

Der „Ranglisten-Wert“ zieht die Unsicherheit ab. So landet ein Clip mit nur einem Glückssieg nicht gleich auf Platz 1.

**Zum Vergleich clip-battle.de (kein Elo):** Sterne 1–5 (angezeigt als Durchschnitt × 2), dazu ein „Momentum“ = 18 + 7 · aktuelle Stimmen + 2 · Monatsstimmen + 1,4 · Reaktionen + 4 · Duellsiege (begrenzt auf 12–99). Duelle zählen nur Stimmen in Prozent. Vor dem Abstimmen müssen beide Clips zu mindestens 25 % angesehen werden.

### 1.4 Was lässt sich für den Telegram-Port wiederverwenden?

**Direkt übernehmen (kopieren, anpassen, Herkunft im Kommentar vermerken):**
- Elo-Funktionen (Variante A oder B) – etwa 40 Zeilen reine Mathematik
- `media_compression.py` – FFmpeg-Profile, um unter die 50-MB-Grenze von Telegram zu kommen
- Bot-Grundgerüst: Polling mit `allowed_updates`, Erkennen von `Conflict` (ein zweiter Empfänger holt Updates ab), Callback-Schema `aktion:id:extra`
- Idee aus `analyzer/feedback.py`: Lernen erst ab einer Mindestzahl, Vertrauen wächst langsam, Korrektur ist begrenzt
- `compiler/pipeline.py` → `_render_xfade`: Überblendungen (Stufe 7)
- `renderer/tiktok_renderer.py`: 9:16-Filtergraph, Tonspur-Rollen (Spiel/Mikro), Text-Escaping für `drawtext` (Stufe 5)
- `maintenance/editor_export.py`: nummerierte Dateinamen und Manifest (Stufe 2b)

**Nicht übernehmen:**
- Windows-App (PySide6), Installer, Code-Signing, lokale FastAPI-Oberfläche
- Discord-Hub, zentraler Review-Hub, Browser-Fallback
- OpenAI/Ollama-Scorer (bei uns: Replay-Daten + `claude -p`)
- SteelSeries-Metadaten (du nimmst jetzt mit der Nvidia App auf)
- Sterne-Skala 1–5 (bei uns: Freigeben/Verwerfen + Battle)

**Schwachstellen im clips_voter-Bot, die wir beim Port bewusst anders machen:**
1. **Keine Absender-Prüfung:** Wer den Bot findet, kann `/status`, `/stats` usw. aufrufen. → Bei uns gibt es eine Whitelist mit deiner Telegram-User-ID; alle anderen werden ignoriert.
2. **`drop_pending_updates=True`:** Klicks, die du machst, während der Bot neu startet, gehen verloren. → Bei uns steht es auf `False`, Telegram hebt die Updates bis zu 24 h auf.
3. **Doppelte Klick-Bestätigung:** `query.answer()` wird zu Beginn aufgerufen und im Fehlerfall ein zweites Mal. Telegram erlaubt aber nur eine Antwort pro Klick (der Commit „Make Telegram callback acknowledgements non-fatal“ deutet genau darauf hin). → Bei uns gibt es genau eine Antwort pro Klick.
4. **Kein Doppelklick-Schutz:** Zweimal „Approve“ kann zwei Verschiebe-Jobs anlegen. → Bei uns ändert sich der Status nur, wenn der alte Status noch passt: `UPDATE … WHERE status = 'gesendet'`.
5. **Fehler verschwinden still:** Handler fangen `Exception` breit ab und schreiben nur ins Log. → Bei uns bekommst du eine kurze Fehlermeldung im Chat.

### 1.5 Ist clips_voter die Codebasis von clip-battle.de? Gibt es eine Einreich-Anbindung?

**Nein.** clip-battle.de ist das Repo `clip-battle` (Python/Flask, SQLite, Android-App über Capacitor).
- **Nur Links:** YouTube-Videos, YouTube Shorts und Twitch-Clips; TikTok nur im Sandbox-Modus für Tester. Dateien werden bewusst nie gespeichert („Link-only invariant“).
- **Einreichen:** über das Web-Formular `/submit` (Login nötig) oder als installierte App über „Teilen“ (Share-Target `/share/import`).
- **API:** Die `/api/…`-Endpunkte brauchen eine Browser-Sitzung mit CSRF-Token. Es gibt **keinen API-Schlüssel** für Skripte oder Bots.
- **Social Distribution** (`SOCIAL_DISTRIBUTION_READINESS_DE.md`): Aktuell wird nur der clip-battle-Link geteilt. Automatisches Hochladen zu YouTube/TikTok ist bewusst nicht gebaut (Audit, Rechte).

→ Folgen für uns: siehe 5.4 und Rückfrage 5.

---

## 2. Entwurf: Telegram-Port

### 2.1 Sprache & Bibliotheken
- **Python 3.11+** und **python-telegram-bot** (aktuelle Version, async) – wie in clips_voter.
- **SQLite über Pythons eingebautes `sqlite3`** mit einer lesbaren `schema.sql` statt SQLAlchemy. Grund: Du siehst jedes SQL direkt und kannst jederzeit mit `sqlite3 pipeline.db` hineinschauen. clip-battle macht es genauso.
- **FFmpeg** als Systempaket.

### 2.2 Wer spricht mit Telegram? (Bot, n8n, Datenbank)

**Regel:** Genau ein Programm holt Updates ab – der Bot `clip-bot` auf dem Heimserver (systemd-Dienst, Polling).

- **n8n** startet nur Skripte per SSH und verschickt keine Videos. Über denselben Bot-Token darf n8n höchstens `sendMessage` für Fehlermeldungen verwenden – **niemals** einen Telegram-Trigger, denn der würde die Updates „wegschnappen“.
- **Übergabe über die Datenbank („Outbox“):** Die Pipeline setzt fertige Clips auf `vorbewertet`. Der Bot prüft alle 30 s, ob neue da sind, und sendet sie. Vorteile: Pipeline und Bot kennen sich nicht und sind einzeln testbar. Wenn der Bot kurz aus ist, geht nichts verloren.
- Laufen versehentlich doch zwei Empfänger, meldet Telegram `Conflict`. Der Bot erkennt das und schreibt eine klare Meldung ins Log.

```
Pipeline (per SSH von n8n)          SQLite pipeline.db            clip-bot (systemd, Polling)
  pipeline process …  ──schreibt──►  clips.status = vorbewertet  ◄──liest alle 30 s──┐
                                                                                     │ sendVideo
                                     clips.status = freigegeben  ◄──Button-Klick──── Telegram ◄── Handy
n8n ── nur bei Fehlern: sendMessage ─────────────────────────────────────────────►   Telegram
```

### 2.3 Neuer Clip
```
🎬 Clip #42 · Triple Kill
⭐ Vorbewertung: 7,8 Punkte
🔫 3 Eliminierungen in 7 s · Platz 3
Begründung: Triple Kill 6,0 · Lautstärke 0,8 · Kommentar 1,0
[✅ Freigeben] [🗑️ Verwerfen]
```
- Dazu ein Video: verkleinerte Vorschau, höchstens 50 MB (720p, H.264).
- Nach dem Klick wird der Text ergänzt („✅ Freigegeben 21:14“). Statt der Buttons erscheint [↩️ Rückgängig]. Rückgängig geht, solange der Clip noch nicht veröffentlicht ist.
- Callback-Daten (Telegram erlaubt max. 64 Byte): `f:42` freigeben, `v:42` verwerfen, `u:42` rückgängig, `b:17:a` / `b:17:b` / `b:17:s` für Battle 17.

### 2.4 Battle-Modus
- Start über `/battle`. Später optional automatisch x Battles pro Tag.
- **Paarbildung** (angelehnt an cliphub):
  - nur **freigegebene** Clips
  - der Clip mit den wenigsten Battles zuerst
  - Gegner ist der Clip mit der ähnlichsten Elo
  - dasselbe Paar nicht zweimal hintereinander
- **Anzeige:** zwei Videos als Album. Telegram erlaubt an Alben keine Buttons, deshalb kommt direkt darunter eine Nachricht mit **[⬅️ A] [B ➡️] [🤷 Überspringen]**.
- **Einmal-Entscheidung:** Das Battle wird **vor** dem Senden in der DB angelegt. Ein Klick entscheidet es genau einmal (`UPDATE battles SET gewinner = … WHERE id = … AND gewinner IS NULL`).
- **Nachvollziehbarkeit:** Elo vorher/nachher wird mitgespeichert. So ist jede Zahl nachprüfbar und alles lässt sich neu berechnen.
- **Elo-Formel:** wie in clips_voter (Variante A oder B → Rückfrage 4).
- **Saison:** Vorschlag 2 Wochen = Zeitraum des Highlight-Videos. Elo wird nicht zurückgesetzt; die Saison ist nur ein Filter dafür, welche Clips ins Highlight-Video kommen.
- `/rangliste` zeigt die Top 10 der aktuellen Saison.

### 2.5 Befehle
`/start` `/hilfe` · `/status` (Warteschlange, letzter Lauf, Fehler, freier Speicher) · `/offen` (unentschiedene Clips erneut senden) · `/battle` · `/rangliste` · `/gewichte` · `/paket <id>` (ab Stufe 5)

### 2.6 Wie Freigaben und Battles zum Lernsignal werden
- **Freigeben/Verwerfen** heißt: „Dieser Clip ist besser als die verworfenen Clips desselben Abends.“ Daraus entstehen Paare (freigegeben, verworfen).
- **Battle-Sieg** ist direkt ein Paar (Gewinner, Verlierer). Überspringen erzeugt kein Paar.
- Beide Signale landen in **einer** Paarliste, und ein einziges Lernverfahren verarbeitet sie (3.4).
- Die Elo ist **kein Merkmal** der Vorbewertung, sondern das Urteil, an dem sich die Vorbewertung messen lassen muss.

---

## 3. Entwurf: Vorbewertung & Lernen

### 3.1 Von Kills zu Clip-Kandidaten
1. **Aus dem Replay:** alle Eliminierungen, bei denen ich der Eliminator bin. Knocks und Selbst-Eliminierungen zählen nicht.
2. **Nach Zeit sortieren und Ketten bilden:** Liegt ein Kill höchstens 10 s nach dem vorherigen, gehört er zur selben Gruppe.
   - Beispiel: Kills bei 100 s, 106 s, 113 s und 140 s.
   - Gruppe 1 = Triple (106 − 100 = 6 und 113 − 106 = 7, beides ≤ 10). Gruppe 2 = Einzelkill.
   - Die Alternative „10 s ab dem ersten Kill“ würde daraus Double + Single machen. Ich nehme die Kette an → Annahme A1.
3. **Victory Royale** (Platzierung 1): +5 für die Gruppe mit dem letzten Kill des Matches → A2.
4. **Clip-Grenzen:** erster Kill − 8 s bis letzter Kill + 5 s. Überschneiden sich Fenster, werden sie zu einem Clip zusammengelegt und ihre Punkte addiert → A4.

### 3.2 Merkmale und Startgewichte (konfigurierbar in `config/pipeline.yaml`)

| Merkmal | Wert | Startgewicht | ab Stufe |
|---|---|---|---|
| `kill_punkte` | Einzel 1 · Double 3 · Triple 6 · 4+ 10 | 1,0 | 2 |
| `victory_royale` | 0 oder 1 | 5,0 | 2 |
| `laenge` | Sekunden über 30 s ÷ 10 (sonst 0) | −0,5 | 2 |
| `lautstaerke` | 0–1: Pegelspitze Spielton im Fenster, relativ zum Match-Durchschnitt | 1,0 | 2 |
| `kommentar` | 0–1: Sprachanteil auf der Mikro-Spur + Bonus für Schlüsselwörter | 1,0 | 5c (Whisper) |

**Score = Σ Gewicht × Wert.**

Beispiel: Triple Kill (6), Lautstärke 0,8, noch kein Kommentar, 21 s → 6 + 0,8 + 0 + 0 = **6,8**.

Die Stufentabelle 1/3/6/10 bleibt fest; gelernt wird nur der Multiplikator davor. Solange Whisper fehlt, ist `kommentar` = 0. So bleibt Stufe 2 klein.

### 3.3 Begründung
Jeder Summand wird als Text gespeichert, zum Beispiel „Triple Kill 6,0 · Lautstärke 0,8 × 1,0 = 0,8“. Er erscheint in Telegram und in der Schnittliste.

### 3.4 Lernverfahren: „paarweise nachjustieren“
**Idee:** Wenn du Clip A besser findest als Clip B, die Formel aber B vorne sieht, verschieben sich die Gewichte ein kleines Stück in Richtung der Merkmale, in denen A besser war.

Das Modell wird bei jeder Änderung **komplett aus der Historie neu berechnet**. Dadurch ist es deterministisch und jederzeit nachvollziehbar.
1. **Paare sammeln:**
   - jedes Battle (Gewinner > Verlierer)
   - jede Freigabe: jeder freigegebene Clip > jeder verworfene Clip desselben Abends (max. 20 Paare pro Abend)
2. **Start:** `w = Startgewichte`.
3. **Lernschleife** (chronologisch, 5 Durchläufe): Für jedes Paar ist `d = Merkmale_Gewinner − Merkmale_Verlierer`.
   - Ist `w · d < 1` (die Formel liegt falsch oder zu knapp), dann gilt `w ← w + 0,05 · d`.
4. **Leine:** Jedes Gewicht bleibt innerhalb von ±50 % seines Startwerts (mindestens ±0,5).
5. **Vertrauen:** `w_aktiv = Start + c · (w − Start)` mit `c = min(1, N / 60)`. N ist die Anzahl deiner Bewertungen.
6. **Mindestmenge:** Das Lernen ist erst ab **N ≥ 20** Bewertungen aktiv (Freigaben/Verwerfungen + Battles). Vorher gelten die Startgewichte.
7. **Sicherheitscheck:** Neue Gewichte werden nur übernommen, wenn sie die bisherigen Paare mindestens so oft richtig sortieren wie die Startgewichte.

**Warum so?** Das Verfahren ist linear (wie in CLAUDE.md gewünscht) und jeder Schritt ist erklärbar. Es ist begrenzt wie `feedback.py` in clips_voter und kommt ohne Bibliothek aus (etwa 20 Zeilen). Mit künstlichen Daten lässt es sich gut testen.

### 3.5 Anzeige `/gewichte`
```
🧠 Vorbewertung – Gewichte (Version 7, aktiv seit 12.10.)
Datenbasis: 34 Bewertungen (21 Freigaben/Verwerfungen, 13 Battles) · Vertrauen 57 %
Merkmal          Start   Aktuell   Δ
Kill-Punkte      1,00    1,12     +0,12
Victory Royale   5,00    4,40     −0,60
Lautstärke       1,00    1,35     +0,35
Kommentar        1,00    1,00      0,00
Länge (>30 s)   −0,50   −0,62     −0,12
Trefferquote: 74 % (Startgewichte: 65 %)
```
Die Gewichte werden versioniert gespeichert. An jedem Clip steht, mit welcher Version er bewertet wurde.

---

## 4. Kill-Daten

### 4.1 Parser-Recherche (Stand 23.09.2026)

| Parser | Sprache | Stand | Bewertung |
|---|---|---|---|
| [FortniteReplayDecompressor](https://github.com/Shiqan/FortniteReplayDecompressor), NuGet `FortniteReplayReader` | C# / .NET 10 | v3.1.0 vom 13.09.2026; Fix für Build 41.00 am 06.09.2026 ([PR #77](https://github.com/Shiqan/FortniteReplayDecompressor/pull/77)), geprüft mit Builds 40.x bis 42.10 | **Empfehlung:** gepflegt, MIT, liefert alles Nötige |
| [xNocken/replay-reader](https://github.com/xNocken/replay-reader) (npm) | Node.js | Pflegezustand unklar | Reserve, falls die 1. Wahl bricht |
| `fortnite-replay-parser` / `fortnite-replay-reader` (PyPI) | Python | Stand Chapter 4 bzw. älter | nicht geeignet |

**Was wir aus dem Parser nutzen** (Namen laut [Doku](https://fortnitereplaydecompressor.readthedocs.io/en/latest/introduction/), genaue Prüfung in 1a):
- Replay-Info: `Timestamp` (Startzeit der Replay-Aufnahme), `LengthInMs`, `IsLive`, Build/Changelist
- Spieler mit `IsPlayersReplay == true` = ich: `EpicId`, `Placement`, `TotalKills`
- [Eliminierungen](https://sl-x-tnt-fortnitereplaydecompressor.mintlify.app/api/models/player-elimination): `Eliminator`, `Eliminated`, `Knocked`, `Timestamp` (ms), `DeathCause`

**Umsetzung:** ein kleines Konsolenprogramm `tools/replay2json` (C#, ca. 80 Zeilen), das JSON ausgibt. Python ruft es auf und liest nur dieses JSON. Der Rest bleibt Python, und der Parser ist austauschbar: Ein Reserve-Parser muss nur dasselbe JSON liefern.

```json
{
  "parser": "FortniteReplayReader 3.1.0",
  "build": "++Fortnite+Release-42.10-CL-…",
  "replay_start_utc": "2026-09-23T18:14:58Z",
  "laenge_ms": 1432000,
  "ich": {"epic_id": "…", "platzierung": 3, "kills_gesamt": 5},
  "eliminierungen": [
    {"t_ms": 646100, "eliminator": "…", "eliminiert": "…", "knock": false, "ursache": "Shotgun"}
  ]
}
```
Rechenbeispiel (passt zur Schnittliste in 6.5): 18:14:58 + 646,1 s = 18:25:44,1 UTC. Das Video startete um 18:15:33 UTC (Dateiname 20.15.33 Sommerzeit). Der Kill liegt also bei 611,1 s im Video, plus Korrektur 1,2 s = **612,3 s**.
**Plausibilitätscheck:** Die Zahl meiner Eliminierungen muss zu `kills_gesamt` passen, sonst gibt es eine Warnung.

**Wo läuft das Programm?** Später auf dem Heimserver im LXC (.NET-10-Runtime für Linux). Die Replays sind klein und liegen dort ohnehin neben den Videos. Zum Ausprobieren läuft es vorher auch auf dem Gaming-PC. Installationen erst nach Rückfrage.

### 4.2 Zeitabgleich Replay ↔ Video
Beide Uhren werden auf echte Uhrzeit (UTC) umgerechnet:
- **Kill (UTC)** = `replay_start_utc` + `t_ms`
- **Video-Start (UTC)** kommt aus dem Dateinamen der Nvidia App, z. B. `Fortnite 2026.09.23 - 20.15.33.02.mp4`. Das ist lokale Zeit (Europe/Berlin, **Sommer-/Winterzeit beachten**), die wir nach UTC umrechnen.
  - Manuelle Aufnahme: Die Zeit im Namen ist der Start (Annahme, wird geprüft).
  - Instant Replay (`….DVR.mp4`): Die Zeit im Namen ist vermutlich der Speicherzeitpunkt, also das Ende. Dann gilt Start = Zeit im Namen − Videolänge (per `ffprobe`).
- **Position im Video** = Kill (UTC) − Video-Start (UTC) + `offset_s`
- **Kalibrierung in Stufe 1b:** Zwei bis drei Runden spielen, bei Kills die Sekunde im Video suchen und mit der berechneten Sekunde vergleichen. Die Differenz kommt als `offset_s` in die Konfig.
  - Schwankt die Differenz von Match zu Match (etwa weil `t_ms` ab Match-Start statt ab Replay-Start zählt), nehmen wir zusätzlich den Match-Startzeitpunkt aus den Spieldaten.
- **Gegenproben:** `creation_time` aus `ffprobe` und die Datei-Änderungszeit. Weichen sie um mehr als 5 s ab, gibt es eine Warnung.
- **Uhren:** Replay-Zeit und Dateiname stammen beide vom Gaming-PC. Ein falsch gehender PC-Uhr-Fehler hebt sich deshalb weitgehend auf.
- Der **Puffer** (8 s vorne, 5 s hinten) fängt den Rest ab.
- Kills außerhalb des Videos (Aufnahme lief nicht) werden gezählt und gemeldet („2 Kills ohne Video“).

### 4.3 Wenn der Parser nach einem Update streikt
**Erkennung:**
- Programm stürzt ab oder endet mit Exit-Code ≠ 0,
- 0 eigene Eliminierungen, obwohl `kills_gesamt > 0` ist, oder
- unbekannter Build.

**Dann:**
1. **Fallback-Analyse:**
   - Lautstärke-Spitzen auf der Spieltonspur (FFmpeg `ebur128`/`astats`, Idee aus clips_voter)
   - ab Stufe 5c zusätzlich Whisper-Schlüsselwörter auf der Mikro-Spur („hab ihn“, „double“, „triple“, „gg“, „Victory“ …)
   - Die Kandidaten bekommen die Quelle `fallback`.
2. Telegram zeigt „⚠️ ohne Replay-Daten“. Die Kill-Punkte sind dann 0 oder geschätzt.
3. Die Replay-Datei bleibt liegen. Nach einem Parser-Update rechnet `pipeline reparse --seit 2026-10-01` die Punkte neu (die Clips bleiben).
4. **Regressionstest:** 3 Beispiel-Replays mit bekannter Kill-Zahl, abgelegt außerhalb von Git (Pfad per `.env`). Nach einem Fortnite-Update: `pytest -m replay`.
5. **Update-Weg:** neue Paketversion einspielen → Test → fertig. Notfalls den Reserve-Parser hinter dieselbe JSON-Schnittstelle hängen.

---

## 5. Entwurf: Caption-Generator & Werbung

### 5.1 Beschreibung nur aus Daten
**Immer:** Textbausteine aus `templates/beschreibungen.yaml`, gefüllt ausschließlich mit Fakten aus der Schnittliste:
```yaml
triple:
  - "Triple Kill in {sekunden} Sekunden 🔥"
  - "3 Eliminierungen in {sekunden} Sekunden – Triple!"
victory_royale:
  - "Victory Royale mit {kills_match} Kills 👑"
```
Welche Variante gewählt wird, hängt von der Clip-ID ab. Das ist reproduzierbar und trotzdem abwechslungsreich.

**Optional und abschaltbar:** `claude -p` formuliert um.
- Eingabe ist nur das Fakten-JSON. Aufruf: `claude -p --output-format json --allowedTools "Read" "…"`.
- Prüfung der Antwort: gültiges JSON, höchstens 150 Zeichen, und jede Zahl im Text muss in den Fakten vorkommen.
- Sonst wird der Baustein genommen. Damit kann die KI nichts erfinden, und die Pipeline läuft auch, wenn das Abo-Kontingent aufgebraucht ist.
- Einzelbilder an Claude geben: nur, wenn du es später ausdrücklich willst. Standard ist aus, weil die Gefahr erfundener Details steigt.

### 5.2 Hashtag-Vorlage
- `templates/caption.txt` wie in CLAUDE.md.
- Platzhalter: `{beschreibung}`, `{killtyp}` (→ `elimination`, `doublekill`, `triplekill`, `multikill`, `victoryroyale`), optional `{platzierung}`.
- Ein unbekannter Platzhalter führt zu einer Fehlermeldung statt zu einer stillen Lücke.
- Längengrenzen pro Plattform stehen in der Konfig (z. B. YouTube-Titel max. 100 Zeichen).

### 5.3 Werbung im Video
- **Overlay:** kleines „clip-battle.de“ (`drawtext`, halbtransparent). Die Position ist konfigurierbar und liegt außerhalb der Bereiche, die Shorts/TikTok mit Buttons überdecken (rechts, unten).
- **Endcard:** 2 s Einblender am Ende (`templates/endcard.png`, einmal per FFmpeg erzeugt oder von dir gestaltet), mit weicher Überblendung.
- Beides lässt sich in `config/pipeline.yaml` einzeln abschalten.

### 5.4 Brücke zu clip-battle.de
„Stimm ab auf clip-battle.de“ ist nur ehrlich, wenn der Clip dort auch im Battle ist. Da clip-battle.de nur Links annimmt, läuft es so:
1. Das Paket kommt per Telegram: Short + Caption zum Antippen/Kopieren.
2. Du lädst den Short auf YouTube Shorts hoch (halbautomatisch, wie in CLAUDE.md).
3. In der YouTube-App tippst du auf „Teilen“ → „Clip Battle“ (installierte App/PWA mit Share-Target). Damit ist der Clip eingereicht.
4. Im Bot drückst du [🔗 Veröffentlicht]. Der Status wechselt auf `veroeffentlicht`; optional fügst du den Link ein.

Später denkbar als eigenes Projekt im clip-battle-Repo: ein persönlicher API-Schlüssel zum automatischen Einreichen. → Rückfrage 5.

---

## 6. Architektur-Überblick

### 6.1 Datenfluss
```
Gaming-PC (Windows)                      Heimserver (Proxmox → LXC)                    vServer
  Nvidia App → Videos ──┐                /srv/clips (Bind-Mount)                         n8n (Docker)
  Fortnite  → Demos  ───┴─ Übertragung ─► eingang/, replays/                              │
                          (Rückfrage 3)                                                  │ alle 3 min
                                         pipeline scan / process  ◄──── SSH (Tailscale) ──┘
                                           │  FFmpeg, replay2json, Whisper, claude -p
                                           ▼
                                         pipeline.db (SQLite)  ◄──►  clip-bot (einziger Telegram-Empfänger)
                                                                        │
Handy ◄──────────────────────────────── Telegram ◄───────────────────────┘
```
Über das Internet reisen nur Befehle, JSON und Vorschau-Videos an Telegram. n8n sieht nie ein Video.

### 6.2 Ordner auf dem Heimserver (Bind-Mount `/srv/clips`)
```
/srv/clips/
  eingang/mit-cam/      ← Nvidia-Aufnahmen mit Facecam
  eingang/ohne-cam/     ← Nvidia-Aufnahmen ohne Facecam
  replays/              ← *.replay vom Gaming-PC
  arbeit/<match-id>/    ← CFR-Kopie, zeitleiste.json, schnittliste.json
  clips/<match-id>/     ← 001_triple-kill_6-8p.mp4 … (für CapCut)
  vorschau/             ← ≤ 50 MB für Telegram
  shorts/  highlights/
  musik/<titel>.mp3 + <titel>.lizenz.txt
```

### 6.3 Statusmodell (Vorschlag, kleine Abweichung zu CLAUDE.md → A5)
`neu → vorbewertet → gesendet → freigegeben | verworfen → veroeffentlicht → im_highlight`

In CLAUDE.md steht „bewertet“ vor „freigegeben/verworfen“. Dort passt das Wort nicht, weil die Bewertung ja die Freigabe selbst ist. Deshalb schlage ich `gesendet` vor (= liegt in Telegram, wartet auf dich).

### 6.4 Datenbank `pipeline.db`

| Tabelle | Inhalt |
|---|---|
| `matches` | Replay-Pfad, Replay-Start (UTC), Build, Platzierung, Victory Royale, Kills gesamt, Parser ok? |
| `aufnahmen` | Video-Pfad, Layout (mit-cam/ohne-cam), Start (UTC), Dauer, `offset_s`, Match |
| `clips` | Aufnahme, Start/Ende, Kills, Typ, Merkmale (JSON), Punkte, Begründung, Gewichte-Version, Status, Pfade (Clip, Vorschau, Short), Telegram-Message-ID, Elo, Unsicherheit, Zahl der Battles |
| `battles` | Clip A, Clip B, Gewinner (leer = offen, `s` = übersprungen), Elo vorher/nachher, Zeiten |
| `gewichte` | Version, Gewichte (JSON), Datenbasis, Trefferquote, aktiv? |
| `ereignisse` | Protokoll: wer hat wann was geändert (für `/status` und Fehlersuche) |

### 6.5 Schnittliste (eine JSON-Datei pro Aufnahme)
```json
{
  "version": 1,
  "aufnahme": "eingang/ohne-cam/Fortnite 2026.09.23 - 20.15.33.02.mp4",
  "layout": "ohne-cam",
  "match": {"replay": "replays/UnsavedReplay-2026.09.23-20.14.58.replay", "platzierung": 3, "victory_royale": false},
  "zeitabgleich": {"video_start_utc": "2026-09-23T18:15:33Z", "offset_s": 1.2, "quelle": "replay"},
  "gewichte_version": 0,
  "kandidaten": [
    {
      "nr": 1, "start_s": 604.3, "ende_s": 625.1,
      "kills": 3, "typ": "triple", "kill_zeiten_s": [612.3, 618.4, 620.1],
      "merkmale": {"kill_punkte": 6, "victory_royale": 0, "laenge": 0, "lautstaerke": 0.8, "kommentar": 0},
      "punkte": 6.8,
      "begruendung": "Triple Kill 6,0 · Lautstärke 0,8"
    }
  ]
}
```
Alle Ausgaben entstehen aus dieser Datei: CapCut-Clips, Vorschau, Short, Highlight-Video und Caption.

### 6.6 Geplante Repo-Struktur
```
baddieday-voting/
  CLAUDE.md  PLAN.md  README.md  .env.example  .gitignore  pyproject.toml
  config/pipeline.yaml                 ← Punkte, Fenster, Puffer, Gewichte, Pfade
  templates/caption.txt  templates/beschreibungen.yaml  templates/endcard.png
  tools/replay2json/                   ← C#: replay2json.csproj, Program.cs
  src/clip_pipeline/
    cli.py            ← Befehl „pipeline …“
    config.py  db.py  schema.sql
    replay.py         ← ruft replay2json auf
    zeitabgleich.py  vorbewertung.py  schnittliste.py
    video.py  audio.py  whisper.py
    caption.py  shorts.py  highlight.py
    elo.py  lernen.py
    bot/  main.py  freigabe.py  battle.py  texte.py
  tests/                               ← pytest, je Modul eine Datei
  deploy/systemd/clip-bot.service
  n8n/workflows/pipeline.json          ← Export ohne Zugangsdaten
```

### 6.7 Kommandozeile
```
pipeline scan                    → JSON: neue, fertige Matches
pipeline process <match-id>      → Zeitleiste, Schnittliste, Clips, Vorschau, DB-Einträge
pipeline status
pipeline reparse --seit DATUM
pipeline short <clip-id>
pipeline highlight --tage 14
pipeline gewichte [--neu-berechnen]
```
Jeder Befehl gibt am Ende eine JSON-Zeile aus, damit n8n sie direkt lesen kann. Jeder Befehl darf doppelt laufen, ohne etwas doppelt zu tun (Sperrdatei + Statusprüfung).

---

## 7. Stufenplan

Jede Stufe hat automatische Tests (pytest). Dazu kommt ein Test, den **du** mit echten Daten machst. Erst nach deinem OK beginnt die nächste Stufe.

### Stufe 0 – Fundament (machst du, ich liefere die Anleitung Schritt für Schritt)
| | Ziel | Test | Du lernst |
|---|---|---|---|
| 0a | Proxmox + Debian-13-LXC (unprivilegiert) + Bind-Mount `/srv/clips` + Benutzer `pipeline` | `pct enter <id>`, `ls /srv/clips`, `df -h` | Container vs. VM, Bind-Mounts, UID-Mapping |
| 0b | Tailscale im LXC (braucht `/dev/net/tun`), SSH-Schlüssel für n8n, SSH-Credential in n8n | vom vServer: `ssh pipeline@heimserver 'echo ok'`; n8n-SSH-Node führt `uname -a` aus | Tailscale, SSH-Schlüssel, `authorized_keys`, Benutzer mit wenig Rechten |
| 0c | *optional:* iGPU `/dev/dri/renderD128` durchreichen | `vainfo`, `ffmpeg -hwaccels` | Geräte-Durchreichung. Ohne 0c läuft alles per CPU, nur langsamer. |
| 0d | Übertragung Gaming-PC → Heimserver (je nach Rückfrage 3, z. B. Syncthing) | Datei am PC ablegen → nach ≤ 1 min auf dem Server | Dateisynchronisation über Tailscale |
| 0e | `git init` in diesem Projekt (nach Rückfrage), `.gitignore` mit `.env`, `*.db`, Medien | `git status` | Git-Grundlagen |

### Stufe 1 – Kill-Daten
**1a `replay2json`**
- **Ziel:** Ein Replay rein, JSON mit meinen Kills raus.
- **Dateien:** `tools/replay2json/replay2json.csproj`, `tools/replay2json/Program.cs`, `src/clip_pipeline/replay.py`, `tests/test_replay.py`
- **Test:** `replay2json UnsavedReplay-….replay` für 2–3 eigene Replays. Die Kill-Zahl mit der Match-Zusammenfassung im Spiel vergleichen.
- **Du lernst:** .NET-Kommandozeile und NuGet, wie ein Replay aufgebaut ist, JSON als Schnittstelle zwischen zwei Sprachen.
- **Allein nutzbar:** Für jedes Match bekommst du eine Kill-Liste. Läuft schon auf dem Gaming-PC, parallel zu Stufe 0.

**1b Zeitabgleich**
- **Ziel:** Kill-Zeitpunkte als Sekunde im Video.
- **Dateien:** `src/clip_pipeline/zeitabgleich.py`, `config/pipeline.yaml`, `tests/test_zeitabgleich.py` (Dateinamen inkl. Sommerzeit-Wechsel und `.DVR`)
- **Test:** Kalibrier-Runde. `pipeline zeitleiste <video> <replay>` gibt aus „Kill 1 bei 10:12“. Mit `ffplay -ss 612 <video>` prüfen.
- **Du lernst:** UTC vs. Ortszeit, `ffprobe`, warum es einen Puffer braucht.

**1c Match-Erkennung `pipeline scan`**
- **Ziel:** fertige Matches erkennen (`IsLive` = false und Datei seit 60 s unverändert) und Videos zeitlich dem passenden Replay zuordnen (Überlappung der Zeiträume).
- **Dateien:** `src/clip_pipeline/cli.py`, `tests/test_scan.py`
- **Test:** pytest mit Beispiel-Dateinamen. Auf dem echten Ordner prüfen, ob die JSON-Ausgabe stimmt.
- **Du lernst:** Zuordnung über Zeitintervalle, Skripte, die man gefahrlos wiederholen kann (idempotent).

### Stufe 2 – Vorbewertung + Clips + Vorschau
**2a Schnittliste**
- **Ziel:** Aus der Zeitleiste wird `schnittliste.json` mit Kandidaten, Punkten und Begründung.
- **Dateien:** `vorbewertung.py`, `schnittliste.py`, `tests/test_vorbewertung.py`
- **Test:** pytest (Ketten-Regel, 1/3/6/10, VR +5, Überlappung); danach eine echte Aufnahme.
- **Du lernst:** reine Funktionen testen, Konfiguration statt fest eingebauter Zahlen.

**2b Clips schneiden**
- **Ziel:** Umwandlung VFR → CFR, dann nummerierte Clips mit Puffer und beiden Tonspuren. Dazu das Merkmal `lautstaerke`.
- **Dateien:** `video.py`, `audio.py`, `tests/test_video.py` (mit kurzem, generiertem Testvideo)
- **Test:** Clips in CapCut importieren. Sind Bild und Ton synchron? Ist die Mikro-Spur da? (Prüfen, ob CapCut die 2. Tonspur liest. Falls nicht: Mikro-Spur zusätzlich als eigene `.m4a`.)
- **Du lernst:** VFR/CFR, Keyframes (warum `-c copy` ungenau schneidet), Tonspuren mit `-map`.

**2c Vorschau + Datenbank**
- **Ziel:** `pipeline process` komplett. Vorschau in 720p unter 50 MB (VAAPI, wenn vorhanden). Die Clips stehen danach in der DB mit Status `vorbewertet`.
- **Dateien:** `db.py`, `schema.sql`, `tests/test_db.py`
- **Test:** `sqlite3 pipeline.db "select id, status, punkte from clips"`
- **Du lernst:** SQL-Grundlagen, Status-Maschine.

### Stufe 3 – Telegram-Bot
**3a Grundgerüst**
- **Ziel:** Der Bot läuft als Dienst und antwortet nur dir.
- **Dateien:** `bot/main.py`, `.env.example` (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USER_ID`), `deploy/systemd/clip-bot.service`
- **Test:** `/status` vom Handy funktioniert. Von einem zweiten Account kommt keine Antwort. Log mitlesen mit `journalctl -u clip-bot -f`.
- **Du lernst:** BotFather, Polling vs. Webhook, systemd-Dienste, Secrets in `.env`.

**3b Freigabe**
- **Ziel:** Outbox, Freigeben / Verwerfen / Rückgängig.
- **Dateien:** `bot/freigabe.py`, `bot/texte.py`, `tests/test_freigabe.py`
- **Test:** Ein Testvideo verarbeiten → die Nachricht kommt → Klick → Status in der DB prüfen. Ein Doppelklick ändert nur einmal etwas.
- **Du lernst:** Callback-Daten, atomare Statuswechsel.

**3c Battle + Elo**
- **Ziel:** `/battle` und `/rangliste`.
- **Dateien:** `elo.py` (Port), `bot/battle.py`, `tests/test_elo.py`
- **Test:** pytest (1500 vs. 1500 mit K = 48 → 1524 / 1476), danach echte Battles am Handy.
- **Du lernst:** die Elo-Formel an echten Zahlen.

### Stufe 4 – n8n-Verkabelung
- **Ziel:** Match vorbei → Telegram-Nachricht, ohne dass du etwas anfasst.
- **Workflow:** Schedule (alle 3 min) → SSH `pipeline scan` → für jedes Match SSH `pipeline process <id>` → bei Fehler Telegram `sendMessage` (nur senden!). Überlappende Läufe verhindert eine Sperre (`flock`).
- **Dateien:** `n8n/workflows/pipeline.json` (Export ohne Zugangsdaten), Abschnitt in der README
- **Test:** eine echte Runde spielen und nichts anfassen. Nach dem Match-Ende plus Verarbeitungszeit kommt die Telegram-Nachricht.
- **Du lernst:** n8n-Workflows, SSH-Node, Fehlerpfade, warum Idempotenz wichtig ist.

### Stufe 5 – Shorts + Caption + Upload-Paket
**5a Caption**
- **Ziel:** Textbausteine + Hashtag-Vorlage, optional `claude -p` mit Prüfung.
- **Dateien:** `caption.py`, `templates/caption.txt`, `templates/beschreibungen.yaml`, `tests/test_caption.py`
- **Test:** pytest (Fakten → erwarteter Text; eine KI-Antwort mit erfundener Zahl → Baustein wird genommen).
- **Du lernst:** Vorlagen, KI-Ausgaben prüfen, `claude -p` headless.

**5b Short 9:16 + Overlay + Endcard**
- **Ziel:** Zwei Layouts:
  - `mit-cam`: Cam oben, Gameplay unten; Cam-Ausschnitt per Koordinaten in der Konfig
  - `ohne-cam`: Mitte zuschneiden, unscharfer Hintergrund
- **Dateien:** `shorts.py`, `templates/endcard.png`
- **Test:** `pipeline short 42` → Video am Handy ansehen.
- **Du lernst:** FFmpeg-Filtergraphen (`crop`, `scale`, `overlay`, `drawtext`).

**5c Untertitel + Kommentar-Merkmal**
- **Ziel:** Whisper (lokal, CPU, Deutsch) auf der Mikro-Spur → Untertitel einbrennen. Gleichzeitig wird das Merkmal `kommentar` gefüllt.
- **Dateien:** `whisper.py`
- **Test:** Untertitel sind lesbar und synchron. `kommentar` ist in der Begründung sichtbar.
- **Du lernst:** lokale Spracherkennung, Untertitelformate (SRT/ASS).

**5d Upload-Paket per Telegram**
- **Ziel:** `/paket 42` (oder ein Button) schickt:
  - den Short (≤ 50 MB, sonst ein Hinweis),
  - die Caption als antippbaren Text,
  - eine Checkliste: YouTube hochladen → Teilen → Clip Battle → [🔗 Veröffentlicht].
- **Test:** ein kompletter Durchlauf bis zum Clip auf clip-battle.de.
- **Du lernst:** Datei-Grenzen von Telegram. Option für später: eigener „Local Bot API Server“ (erlaubt bis 2 GB).

### Stufe 6 – Lernen aus Bewertungen
- **Ziel:** Verfahren aus 3.4 und `/gewichte`.
- **Dateien:** `lernen.py`, Tabelle `gewichte`, `tests/test_lernen.py`
- **Test:** pytest mit künstlicher Historie:
  - Der Gewinner ist immer lauter → das Lautstärke-Gewicht steigt.
  - Unter 20 Bewertungen ändert sich nichts.
  - Die Leine hält.
- **Du lernst:** lineares Modell, Überanpassung, warum es eine Mindestmenge braucht.

### Stufe 7 – Highlight-Video alle 2 Wochen
- **Ziel:** Aus den freigegebenen Clips der Saison entsteht ein Highlight-Video.
  - **Auswahl:** sortiert nach Ranglisten-Wert (Elo), danach nach Vorbewertung
  - **Reihenfolge:** stark anfangen, das Beste zuletzt
  - **Schnitt:** xfade-Übergänge
  - **Ton:** Musik leiser unter dem Spielton (`sidechaincompress`), Lautheit normalisieren (`loudnorm`)
  - **Lizenz:** Musiktitel ohne `.lizenz.txt` werden abgelehnt.
  - **Auslöser:** n8n-Cron alle 14 Tage → SSH `pipeline highlight --tage 14` → Telegram-Meldung mit Vorschau.
- **Dateien:** `highlight.py`, `musik/`
- **Test:** 5 Testclips → fertiges Video. Ein Titel ohne Lizenz führt zum Abbruch mit klarer Meldung.
- **Du lernst:** Audio-Mischung, Lautheit (LUFS), Musiklizenzen.

### Querschnitt (ab Stufe 2)
- tägliches DB-Backup (`sqlite3 pipeline.db ".backup …"`)
- Logs über journald
- Speicherplatz-Warnung in `/status`
- **Rohaufnahmen werden nie automatisch gelöscht** – nur nach deiner Freigabe.

---

## 8. Annahmen (bitte korrigieren, falls falsch)
- **A1** Multikill = Kette (≤ 10 s Abstand zum vorherigen Kill). Nur finale Eliminierungen zählen, keine Knocks.
- **A2** Der Victory-Royale-Bonus geht an die Kill-Gruppe mit dem letzten Kill des Matches.
- **A3** Die Startgewichte für Lautstärke (1,0), Kommentar (1,0) und Länge (−0,5 je 10 s über 30 s) sind mein Vorschlag.
- **A4** Puffer 8 s vorne, 5 s hinten. Überschneidende Fenster werden zusammengelegt.
- **A5** Status `gesendet` statt „bewertet“ (siehe 6.3).
- **A6** Python + `sqlite3` ohne SQLAlchemy. C# nur für das kleine Replay-Tool.
- **A7** Lernen ab 20 Bewertungen, volles Vertrauen ab 60.
- **A8** Nvidia-Tonspuren: Spur 1 = Spielton, Spur 2 = Mikro. Wird in 1b mit `ffprobe` geprüft.
- **A9** clips_voter bleibt, wo es ist. Ich lese nur daraus und übernehme Code als Kopie mit Herkunftsvermerk. Ein Klonen nach `./clips_voter` ist nicht nötig.
- **A10** In Duo/Squad zählen nur meine eigenen Eliminierungen.

---

## 9. Rückfragen (nach Wichtigkeit)

1. **Wie bauen wir?** Im Chat hast du ein „fertiges System ohne Bugs“ gewünscht. Dein Start-Prompt und CLAUDE.md verlangen dagegen Stufen mit Test und OK.
   **Empfehlung:** Stufe für Stufe. Ich baue eine Teilstufe inklusive automatischer Tests, du prüfst sie mit deinen echten Daten, dann folgt die nächste. Einverstanden?
2. **Wie nimmst du auf?**
   - (a) ganze Matches manuell,
   - (b) Instant Replay (nachträglich gespeicherte Ausschnitte),
   - (c) beides.

   Davon hängen Zeitabgleich und Schnitt ab. **Empfehlung: (a)** – dann schneidet die Pipeline selbst, und kein Kill fällt aus dem Video.
3. **Wie kommen Aufnahmen und Replays auf den Heimserver?**
   **Empfehlung:** Syncthing über Tailscale (Nvidia-Ordner + Demos-Ordner, nur Richtung Gaming-PC → Server). Alternativen: Windows-Freigabe (SMB) oder manuell.
   Und: Läuft der Heimserver dauerhaft?
4. **Welche Elo-Variante?**
   - A: Datei in clips_voter – Start 1500, K 48/32/24
   - B: cliphub – Start 1000, K 24–32 mit Unsicherheit

   **Empfehlung: A.** Es ist die Variante in clips_voter, und der hohe Start-K ergibt bei wenigen Battles (nur du stimmst ab) schneller eine sinnvolle Reihenfolge.
   Außerdem: Saison = 2 Wochen ohne Elo-Reset – passt das?
5. **Zielplattform und Einreichen bei clip-battle.de:** clip-battle.de nimmt öffentlich nur YouTube- und Twitch-Links an (TikTok nur im Sandbox-Test).
   **Empfehlung:** zuerst YouTube Shorts, danach den Link per „Teilen → Clip Battle“ einreichen.
   - TikTok zusätzlich (dann ohne clip-battle-Einreichung)?
   - Soll später ein API-Schlüssel für clip-battle.de gebaut werden (eigenes Projekt im clip-battle-Repo)?
