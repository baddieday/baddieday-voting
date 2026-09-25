# Projekt: Clip-Pipeline
Fortnite-Aufnahmen → automatische Highlights → Bewertung per Telegram → Veröffentlichung mit Werbung für clip-battle.de

## Wie wir zusammenarbeiten (wichtig, immer beachten)
- Das ist ein privates **Lernprojekt**. Ich habe Linux-Grundlagen und will verstehen, was passiert – nicht nur fertigen Code bekommen.
- Kommuniziere auf **Deutsch**. Erkläre jeden Schritt kurz, **bevor** du ihn ausführst (was, warum, was ich dabei lerne).
- **Frag nach, statt zu raten.** Wenn etwas in dieser Datei dem Code in `clips_voter` widerspricht oder unklar ist: nachfragen.
- Arbeite in **kleinen Stufen**. Jede Stufe muss für sich allein nutzbar und testbar sein.
- **Vorher fragen** bei: Löschen/Überschreiben von Dateien, Pakete installieren, Systemeinstellungen ändern, git push, allem Irreversiblen.
- Berechtigungsabfragen **niemals** global abschalten (kein `--dangerously-skip-permissions`, kein `bypassPermissions`).
  **Einzige Ausnahme:** im LXC `claude-bau` auf pve-mini, und nur, wenn ich Claude selbst mit
  `--dangerously-skip-permissions` starte. Auch dann gilt: vor `git push`, Löschen/Überschreiben,
  Paketinstallation und Änderungen außerhalb des Repos trotzdem fragen; `.env` und `~/.ssh` nicht lesen.
- **Keine Secrets** in Code oder Repo: Tokens gehören in `.env`, `.env` steht in `.gitignore`. Liefere eine `.env.example`.
- Neue Entscheidungen, die wir treffen, ergänzt du nach Rückfrage in dieser Datei unter „Entscheidungen“.

## Ziel
1. Sobald ein Fortnite-Match vorbei ist, werden die Aufnahmen **im Hintergrund** verarbeitet.
2. Highlights werden **automatisch gefunden und vorbewertet** – auf Basis der Kill-Daten aus den Fortnite-Replays: Einzelkill < Double Kill < Triple Kill < mehr; Bonus für Victory Royale.
3. Ich bewerte per **Telegram** (Port des Battle-Prinzips aus `clips_voter`: zwei Clips, ich wähle den besseren → Elo). Die Vorbewertung **lernt aus meinen Bewertungen**.
4. Ausgaben aus einer zentralen Schnittliste: Einzelclips mit Puffer (für CapCut), Shorts im Hochformat mit Untertiteln, alle 2 Wochen ein Highlight-Video mit Übergängen und Musik.
5. Veröffentlichung mit Caption = grobe Beschreibung + Hashtag-Vorlage + Werbung für **clip-battle.de**. Das Promoten von clip-battle.de ist das Hauptziel.

## Infrastruktur (Stand)
- **Gaming-PC:** Windows. Aufnahmen in `F:\Clips`: **Nvidia App** (Highlights automatisch in `Highlights\Fortnite`, Videobeweis 5 min und manuelle Aufnahmen in `Fortnite\`) und **SteelSeries Moments** (`Fortnite__*.mp4` direkt in `F:\Clips`, Tonspuren „Game“ + „Chat“). Fortnite-Replays liegen in `%LOCALAPPDATA%\FortniteGame\Saved\Demos`. Die Apps verwalten ihren Plattenplatz selbst.
- **Großer PVE-Host (pve-big):** viel Speicher, läuft **nur bei Bedarf** (Wake-on-LAN). Das **Lager**: hier liegen alle Clips
  und Rohdaten dauerhaft. Geweckt wird er höchstens einmal am Tag zum Abgleich, **tagsüber** – nie nachts (Lüfter).
  Er schaltet sich danach selbst ab (`clip-leerlauf`). NFS zum Mini.
- **Mini-PVE:** läuft **dauerhaft**. LXC mit Pipeline, Telegram-Bot und SQLite. Der **Puffer** (eigenes Volume, `/srv/puffer`,
  im CT als `/srv/clips`): Der Gaming-PC kopiert per SMB hierher, die Pipeline arbeitet nur hier und weckt nie. Das
  Lager von pve-big ist per NFS eingebunden (`/srv/big/clips`). iGPU für Hardware-Encoding durchgereicht.
- **vServer (Rechenzentrum):** n8n in Docker, der **Dirigent**. Es werden **keine Videos** dorthin übertragen.
- **Verbindung:** Tailscale zwischen Gaming-PC, Heimserver, vServer und Handy. n8n steuert den Heimserver per **SSH-Node** über Tailscale, mit eigenem Benutzer `pipeline` und SSH-Schlüssel.
- **KI-Entscheidungen:** Claude Code headless (`claude -p`) über mein Max-Abo, **kein API-Key**. Nur Leserechte (`--allowedTools "Read"`), Ausgabe als JSON.
- **Schnittprogramm:** CapCut. CapCut kann keine XML/EDL-Timelines importieren → nummerierte Einzelclips mit ein paar Sekunden Puffer vorne und hinten.

## Architekturprinzipien
1. Rechnen, wo die Daten liegen. Übers Internet reisen nur kleine Dinge (Pfade, JSON, höchstens die Mikro-Spur).
2. Videos nie durch n8n schleusen – nur Dateipfade weitergeben.
3. Feste Abläufe sind Skripte. KI nur dort, wo entschieden werden muss (Schnittliste, Beschreibung).
4. Eine zentrale **Schnittliste (JSON)** pro Aufnahme: Zeitstempel, Punkte, Begründung. Alle Ausgaben werden daraus erzeugt.
5. Einfach vor schlau (Beispiel: Eingangsordner `mit-cam` / `ohne-cam` bestimmen das Shorts-Layout, statt die Cam per KI zu erkennen).
6. Jeder Clip hat einen **Status** in einer Datenbank (Vorschlag: SQLite auf dem Heimserver): neu → vorbewertet → bewertet → freigegeben/verworfen → veröffentlicht → im Highlight-Video.
7. Telegram-Bot per **Polling** (kein öffentlicher Webhook) – passt zum Tailscale-Aufbau.

## Schnittstelle zu n8n (Vertrag – die n8n-Workflows sind schon danach gebaut)
- n8n ruft per SSH als Benutzer `pipeline` auf: `/opt/clip-pipeline/bin/pipeline <befehl>`, Arbeitsverzeichnis `/opt/clip-pipeline`.
- Befehle: `prepare --session ID` · `analyze --session ID` · `decide --session ID` · `render --session ID` · `highlight --id ID --tage 14`
- Session-ID: nur `^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$`. n8n prüft das, das Skript prüft es **zusätzlich**.
- Daten liegen unter `/srv/clips/` (Bind-Mount), je Session ein Ordner `/srv/clips/sessions/<ID>/`.
- Ausgabe: Logs nach **stderr**, als **letzte Zeile auf stdout genau eine JSON-Zeile**. Exit-Code 0 = ok, sonst Fehler.
- `render` liefert mindestens `{"clips": n, "top_label": "Triple Kill", "top_score": 6}`, `highlight` mindestens `{"clips": n, "dauer": "mm:ss"}`.
- Nur ein rechenintensiver Schritt gleichzeitig (Sperre per `flock`), weil der Mini-PC sonst in die Knie geht.
- Idempotent: Ruft man denselben Schritt für dieselbe Session erneut auf, geht nichts kaputt; Fertiges wird übersprungen.
- `decide` ruft intern `claude -p` mit `--allowedTools "Read"` und `--output-format json` auf und prüft die Schnittliste gegen ein Schema, bevor sie gespeichert wird.
- Der Gaming-PC meldet ein Match-Ende per POST an `/webhook/match-vorbei`, Header `X-Pipeline-Token`, Body `{"session": "<ID>"}`. Die ID leitet sich vom Zeitstempel des Replays ab, z. B. `2026-09-23_20-15-33`.

## Vorbewertung (Startwerte, konfigurierbar)
- Multikill = mehrere Eliminierungen durch mich innerhalb eines Zeitfensters (Start: 10 s).
- Punkte: Einzelkill 1 · Double 3 · Triple 6 · 4+ 10 · Victory Royale +5.
- Weitere Merkmale: Lautstärke-Spitzen, mein Kommentar (Whisper auf der Mikro-Spur), Clip-Länge.
- Lernen: Aus meinen Freigaben und der Battle-Elo werden die Gewichte nachjustiert. Das Verfahren muss einfach und nachvollziehbar sein (z. B. lineare Gewichte), wird erst ab einer Mindestzahl an Bewertungen aktiv, und ich kann mir die aktuellen Gewichte anzeigen lassen.

## Captions & Werbung
- Die Beschreibung stützt sich nur auf **vorhandene Daten** (Kills, Multikill, Platzierung) und bei Bedarf ein paar Einzelbilder. Nichts erfinden.
- Die Hashtag-Vorlage liegt als eigene Datei vor (z. B. `templates/caption.txt`), Entwurf:
  ```
  {beschreibung}

  ⚔️ Wer gewinnt das Battle? Stimm ab auf clip-battle.de

  #fortnite #fortniteclips #gaming #{killtyp} #clipbattle
  ```
- In Shorts- und TikTok-Beschreibungen sind Links in der Regel nicht anklickbar. Deshalb zusätzlich optional: kleines Overlay „clip-battle.de“ im Video und/oder ein kurzer Endcard-Einblender.

## Bekannte Stolpersteine
- n8n 2.x: Execute Command und Local File Trigger sind standardmäßig aus → wir nutzen den SSH-Node.
- Aufnahmen mit variabler Bildrate zuerst in konstante Bildrate umwandeln (sonst Desync in CapCut).
- Replay-Zeit ≠ Videozeit: Abgleich über echte Uhrzeit (Replay-Header, Zeitstempel im ShadowPlay-Dateinamen). Ungenauigkeit fängt der Puffer ab.
- Replay-Parser sind inoffiziell und können nach Fortnite-Updates brechen → Fallback: Lautstärke-Spitzen + Whisper.
- Telegram-Bots dürfen Dateien nur bis 50 MB senden → verkleinerte Vorschau rendern.
- Pro Bot-Token nur **ein** Empfänger von Updates (Polling ODER Webhook). n8n darf über denselben Bot nur senden.
- YouTube/TikTok: Uploads über nicht geprüfte API-Apps sind nur privat sichtbar → Veröffentlichung zunächst **halbautomatisch** (fertiges Paket aus Video + Caption per Telegram), Audit später.
- Musik nur aus einem lokal geprüften Ordner mit Lizenzvermerk je Titel. In Fortnite die lizenzierte Musik ausschalten.
- `claude -p` zählt gegen meine Abo-Limits; die Regeln dafür können sich ändern.

## Stufen (grobe Reihenfolge)
0. Fundament: Proxmox, LXC, Tailscale, SSH-Zugang für n8n (mache ich mit Anleitung selbst)
1. Kill-Daten: Replay auslesen, Kill-Zeitleiste, Zeitabgleich mit dem Video
2. Vorbewertung + Clips mit Puffer + verkleinerte Vorschau
3. Telegram-Bot: Freigabe + Battle + Elo (Port von `clips_voter`)
4. n8n-Verkabelung: Match vorbei → Pipeline → Telegram
5. Shorts (9:16, Untertitel, Overlay) + Caption + Upload-Paket
6. Lernen aus Bewertungen
7. Highlight-Video alle 2 Wochen

## Offene Fragen
- YouTube Data API / TikTok Content Posting API: Entwickler-Apps anlegen und Audit beantragen? (bis dahin halbautomatisch)
- Inoffizielle Einreich-API für clip-battle.de: Entwurf in PLAN.md, Umsetzung im Repo `E:\GIT\clip-battle` erst nach OK (Branch?).
- Whisper (Untertitel + Kommentar-Merkmal): Installation von `faster-whisper` freigeben?
- Highlight-Video: Ordner mit lizenzierter Musik anlegen.

## Entscheidungen
- 2026-09-23: Kein Medal.tv – Nvidia + SteelSeries reichen; Pipeline wählt pro Moment die Aufnahme mit bester Abdeckung.
- 2026-09-23: Kill-Wahrheit ist das Replay (Rekorder zählen in Squads Team-Kills mit); Rekorder sind Rückfall.
- 2026-09-23: Replay-Parser FortniteReplayDecompressor (NuGet FortniteReplayReader 3.1.0) als C#-Tool `tools/replay2json`; eigene Epic-ID in der Konfig (IsReplayOwner ist in Build 42.20 leer).
- 2026-09-23: ~~Clips nur auf dem großen PVE-Host~~ (ersetzt durch E19); Übertragung per `windows/Uebertragung.ps1` (alle 2 min, nur kopieren).
- 2026-09-23: Pipeline, Bot und DB auf dem Mini (immer an). Battles funktionieren über Telegram-`file_id` auch bei schlafendem großem Host.
- 2026-09-23: ~~Aufräumen: nach 182 Tagen recyceln (erst Papierkorb, nach 14 Tagen löschen)~~ – ersetzt am 25.09.; Multikills ab 3 Kills dauerhaft in `archiv/`.
- 2026-09-23: Elo = Variante A aus clips_voter (Start 1500, K 48/32/24); Saison = 14 Tage ohne Elo-Reset.
- 2026-09-23: Multikill = Kette (≤ 10 s zum vorherigen Kill), nur finale Eliminierungen; Victory-Royale-Bonus an die letzte Gruppe.
- 2026-09-23: Status `gesendet` statt „bewertet“.
- 2026-09-23: Jeder freigegebene Clip muss auf YouTube Shorts **und** TikTok (eigene Kanäle); danach Link auf clip-battle.de einreichen. Der Bot verfolgt das je Plattform nach und erinnert täglich.
- 2026-09-23: Python + sqlite3 + python-telegram-bot, schlanke Tests mit `unittest` (kein Test-Branch).
- 2026-09-23: n8n-Vertrag umgesetzt und gegen `1-match-verarbeiten.json`, `2-highlight-video.json`, `3-fehler-alarm.json` geprüft. Kein KI-Agent in n8n.
- 2026-09-23: ~~Die Pipeline weckt den großen Host selbst per Wake-on-LAN, wenn ein Schritt den Speicher braucht~~ – seit E19 weckt nur noch der tägliche Abgleich.
- 2026-09-23: Highlight-Videos kommen als Vorschau zur Freigabe in den Bot; Verwerfen gibt die Clips wieder frei.
- 2026-09-23: `decide`: Claude darf nur Schnittpunkte (innerhalb der Kill-Grenzen) und eine Beschreibung aus Fakten vorschlagen; alle Kandidaten bleiben, die Bewertung macht der Mensch.
- 2026-09-24: Kill-Gutschrift nach Fortnite-Regel: Wer umhaut, bekommt den Kill (Umhauen gilt 90 s). Geprüft an 93 eigenen Replays: 87 statt vorher 74 stimmen mit Fortnites Statistik überein.
- 2026-09-24: Bleiben Kills ohne Aufnahme, schickt der Bot eine Warnung (nur für Matches der letzten 12 h, je Match einmal).
- 2026-09-24: Übertragung überspringt Dateien, die noch zum Schreiben offen sind (laufendes Match).
- 2026-09-25 (E19): **Puffer auf dem Mini, Lager auf pve-big.** Der Gaming-PC kopiert alle 2 min per SMB auf den Mini und weckt nie;
  Pipeline, Bot, /paket und Highlight arbeiten nur im Puffer. pve-big wird höchstens einmal am Tag zum Abgleich geweckt
  (SHA-256 mit Zurücklesen, Rohdaten im Lager nie überschrieben). Einführung: `docs/PUFFER.md`.
- 2026-09-25: **Nie löschen.** Rohdaten und Clips werden nirgends automatisch gelöscht (clip-aufraeumen ist aus). Wird Speicher
  knapp (Puffer, Thin-Pool des Mini, Lager auf pve-big), kommt eine Warnung per Telegram (Prüfung einmal am Tag).
- 2026-09-25: **Abgleich tagsüber** (10:00, Prüfung 11:00) – pve-big wird nie nachts geweckt, sein Lüfter soll niemanden wecken.
- 2026-09-25: Der **Puffer hält 14 Tage Rohvideos** (`[puffer].rohdaten_tage`) – der Regisseur baut seine Momente daraus.
- 2026-09-25 (E20): Zugang für Claude über ein flüchtiges Tailnet-Gerät (Anmeldung per Link) und Tailscale SSH im check-Modus
  auf pve-big und pve-mini (nicht im LXC clips – dort nutzt n8n normales SSH).
- 2026-09-25 (E21): Im LXC `claude-bau` (pve-mini, kein Tailscale, kein Zugriff auf Server oder Clips) darf Claude
  mit `--dangerously-skip-permissions` laufen; GitHub nur über einen Deploy-Key für dieses Repo. Die Fragepflicht
  bei Irreversiblem bleibt als Regel bestehen. Voraussetzung: Branch-Schutz für `main` auf GitHub.
- 2026-09-25: **Multikills am Stück.** Zählen bleibt wie bisher (ein Team-Wipe bleibt Triple usw., Punkte/Elo unverändert).
  Neu je Kill der Aktions-Zeitpunkt = mein Umhauen: Clips beginnen 8 s vor dem ersten Umhauen; im Short bleibt eine Serie
  bis 20 s am Stück, Pausen > 4 s zwischen zwei Aktionen per Jump-Cut. Vorhandene Multikill-Momente werden aus den
  Rohvideos im Puffer neu geschnitten (neue Dateien, Bot-Clips und Elo unangetastet).
- 2026-09-25: **Regisseur 2.0 – Spielbild clean.** Im Spielbild nur Übergänge, Zeitlupe, Zoom-Punch und Farblook – kein
  Blitz, kein Wackeln, keine Texte/Grafik. Texte (Kill-Titel, Zähler, clip-battle.de) im Short animiert im unscharfen
  Bereich über/unter dem Spielbild, im 16:9-Zusammenschnitt nur kurz während einer Übergangsblende. Schrift bleibt DejaVu.
  Effekte sind Standard, abschaltbar mit `[regie.effekte] an = false`. Kein Endcard, Loop-Ende.
- 2026-09-25: **Export** nur auf Knopfdruck (📦 im Lern-Bot oder `pipeline export`) nach `/srv/puffer/export/<name>/`, nie
  automatisch gelöscht und täglich ins Lager gesichert. Kein CapCut-Paket; die nummerierten Einzelclips im Export taugen
  auch für CapCut. DaVinci-Timeline (FCPXML) als letzte Stufe.
