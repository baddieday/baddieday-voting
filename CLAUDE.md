# Projekt: Clip-Pipeline
Fortnite-Aufnahmen → automatische Highlights → Bewertung per Telegram → Veröffentlichung mit Werbung für clip-battle.de

## Wie wir zusammenarbeiten (wichtig, immer beachten)
- **Ziel vor allem anderen:** ein selbst lernendes System, das sich durch meine künftigen Eingaben (und das Publikum)
  verbessert – im besten Fall autonom. Es soll **funktionieren**; 50-mal geprüft oder perfekt dokumentiert muss es nicht sein.
- Kommuniziere auf **Deutsch** und erkläre kurz, was du tust und warum (Lernprojekt) – ohne dafür auf mein OK zu warten.
- **Selbstständig arbeiten.** Bei Unklarheit eine sinnvolle Annahme treffen, kurz vermerken (Commit oder Sprint-Log) und
  weitermachen. Fehler dürfen passieren – sie werden behoben.
- **Nur fragen, wenn etwas gelöscht würde oder Datenverlust droht** (Rohdaten, Clips, Datenbank, Lager). Alles andere –
  Code ändern, committen, pushen, mergen, einspielen, Pakete, Dienste – ohne Rückfrage.
- **Schlank arbeiten:** Tests je Funktion nur für den normalen Weg und den wichtigsten Fehlerfall (keine
  Randfall-Sammlungen). Ein Prüfer auf echte Fehler (Absturz, falsche Rechnung, Datenverlust); Gegenprüfung nur bei
  blockierenden Befunden. Volle Testsuite einmal am Ende einer Stufe. Doku kurz.
- Arbeite in **Stufen**; jede Stufe ist für sich nutzbar.
- Berechtigungsabfragen **niemals** global abschalten (kein `--dangerously-skip-permissions`, kein `bypassPermissions`).
  **Einzige Ausnahme:** im LXC `claude-bau` auf pve-mini, und nur, wenn ich Claude selbst mit
  `--dangerously-skip-permissions` starte. Auch dann gilt: vor Löschen oder drohendem Datenverlust fragen; `.env` und
  `~/.ssh` nicht lesen.
- **Keine Secrets** in Code oder Repo: Tokens gehören in `.env`, `.env` steht in `.gitignore`. Liefere eine `.env.example`.
- Neue Entscheidungen trägst du selbst kurz unter „Entscheidungen“ ein.

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
- VA-API-Render (iGPU) kann sporadisch hängen (26.09.: Zusammenschnitt, 40 min ohne Fortschritt, Sperre blockiert) →
  jeder ffmpeg-Aufruf hat einen Wächter (180 s ohne CPU-Zeit = abbrechen), danach rendert der Rückfall auf der CPU.

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
- 2026-09-23: ~~Jeder freigegebene Clip muss auf YouTube Shorts **und** TikTok (eigene Kanäle); danach Link auf clip-battle.de einreichen. Der Bot verfolgt das je Plattform nach und erinnert täglich.~~ – ersetzt am 26.09.
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
- 2026-09-25 (L1, Spec E21): **Publikum ist das Hauptsignal**; zwei Schleifen (dein Urteil täglich, Publikum wöchentlich) speisen dieselben Lerner.
- 2026-09-25 (L2, Spec E22): **Eine Moment-Bewertung** für Clip-Bot und Regisseur; neue Merkmale aus dem Replay-JSON; Lernen bleibt linear, paarweise, gedeckelt.
- 2026-09-25 (L3, Spec E23): **Rezepte** als Stellschrauben mit Stufen; jeder dritte Post ein Experiment nach Unsicherheit; du gibst jeden Post frei.
- 2026-09-25 (L4, Spec E24): **Zahlen zuerst per Screenshot** (claude -p, Leserecht), Display API im Sandbox-Modus als zweite Stufe; Wiedergabezeit nur aus der App.
- 2026-09-25 (L5, Spec E25): Der **Wochen-Analyst schlägt nur vor** (Schema-geprüft), entscheidet nie; jede Hypothese wird per Knopf getestet.
- 2026-09-25 (L6, Spec E26): **Clip-Bot minimal angefasst** (`posts` aus `/link`, Erwartungs-Zeile, Battle-Paarung nach Unsicherheit); n8n-Vertrag und Session-Schnittliste unverändert.
  L1–L6 sind die Nummern dieser Entscheidungen (E21 war schon vergeben); sie bleiben so (Florian 25.09.: „L1–L6 ok“). Die offenen Annahmen des Sprints (A1–A40) und Rückfragen R1–R5: `docs/ENTSCHEIDUNGEN.md`, „Annahmen im Sprint Lernschleife“.
- 2026-09-25: **Schlank und selbstständig** (Florian): Nachfragen nur bei Löschen oder drohendem Datenverlust, sonst
  selbstständig mit vermerkten Annahmen. Tests schlank (normaler Weg + wichtigster Fehlerfall), ein Prüfer, Gegenprüfung
  nur bei Blockierendem, volle Suite einmal je Stufe, Doku kurz. Gilt auch für den Sprint „Lernschleife“ und ersetzt
  dort das 5er-Panel und die Gegenprüfung je Befund aus dem Startauftrag. Ziel: ein selbst lernendes System.
- 2026-09-26: **Einzelne Momente werden nicht hochgeladen** (Florian) – auch kein Triple Kill. Hochgeladen werden nur
  die Shorts aus dem Lern-Bot und das Highlight-Video (1–2 Wochen). Der Clip-Bot zeigt nach der Freigabe keinen 📦-Knopf
  mehr und erinnert nur noch an freigegebene Highlight-Videos, bis „✅ Hochgeladen“ getippt ist. Freigaben dienen
  Bewertung, Lernen und der Highlight-Auswahl. `/paket` und `/link` bleiben für Ausnahmen (nicht mehr in der Hilfe).
- 2026-09-26: **Stimmen im Upload nur, wenn der Moment sie braucht** (Lachen, Jubel, laute Mikro-Spitzen oder Stimmung
  „lustig“ = Gag; `merkmale.stimmen_gebraucht`). Sonst nur Spur 0 (Spielton) – gilt für Entwürfe/Upload-Fassung,
  Highlight-Video und Clip-Shorts (nur noch Ausnahme über /paket). Für die Bewertung zählen alle Stimmen; Vorschauen zum Bewerten behalten alle Spuren.
- 2026-09-26: **Mic-Schritt als systemd-Dienst** `clip-mikro` (path + timer) statt Kindprozess von render; render schreibt
  nur `/var/lib/clip-pipeline/mikro.anstoss`.
- 2026-09-27: **Regisseur – Abwechslung mit Cooldown (3 Entwürfe) und Frische-Quote (50 %)**, weil der anteilige
  Punkte-Abzug allein nur die Spitze rotieren ließ (Simulation: 18 von 120 Momenten in 20 Shorts, jetzt 83).
  **Nachlegen nach dem Kürzen** (Ursache der kurzen Shorts seit „Multikills am Stück“), Grund **„⏱️ zu kurz“**
  (dauer_faktor konnte nur fallen), Format-Dauern über `[regie.formate.<format>]`, Rückfall auf den Bot-Clip statt
  stillem Wegfall bei fehlender Moment-Datei. Ursachenprüfung mit Gegenprüfern; Diagnose für den Mini: `docs/DIAGNOSE-SHORTS.md`.
- 2026-09-27: **Lern-Bot: Schnitt lernt je Format, Lernen wird sichtbar** (Florian: „ich bewerte gefühlt ins Leere“).
  Schnitt-Gründe wirken nur auf das bewertete Format, Inhalt (Momente, Stimmung, Musik) auf beide. Unter jedem Entwurf
  „🔁 Schon bewertet“ je Moment und „🧠 Aus #n“ mit der Wirkung der letzten Bewertung; Kurzbefehl-Tastatur. Knöpfe werden
  sofort beantwortet, Updates laufen nebenläufig (vorher strikt nacheinander – Ursache der trägen Knöpfe).
- 2026-09-27: **Kurzbefehle als Knöpfe im Chat** (nicht als Ersatz-Tastatur, Florian). **Musik: Techno/Industrial und
  Rock statt EDM** – NCS-Genre-Filter (`musik ncs --genre hart`: techno, hardcore, electronic-rock, dance-rock,
  midtempo-bass; Metal führt NCS nicht), `tracks.genre`, Bonus 1,5 für `[musik].genres_bevorzugt`. Alte Titel bleiben.
- 2026-09-27: **Lern-Bot spricht mit Telegram über IPv4** (`[lernbot].nur_ipv4`). Messung auf dem Mini: Bot je Klick
  < 0,3 s, aber Klicks kamen gebündelt 15–20 s später an – die lange Warteabfrage über IPv6 (Fritz!Box/Telekom) hing.
- 2026-09-27 (B3): **Vorbewertung lernt ab 10 statt 20 Bewertungen** (`[lernen].mindestens`); das Vertrauen wächst weiter
  mit n bis 60, die Schranken bleiben. `/gewichte` zeigt eine Fortschrittszeile bis zum Lernen bzw. vollen Vertrauen.
- 2026-09-28 (B4): **`claude -p`-Aufrufe vereinheitlicht** – `decide` (`verarbeitung.frage_claude`), `stimmung`
  und `caption.ki_beschreibung` laufen jetzt über `claude_aufruf.frage_json` statt eigenem `subprocess.run`
  (vorher ohne `stdin=DEVNULL`, ohne `--no-session-persistence`; `caption` sogar fest `"claude"` statt
  `[decide].programm`). Rückgabeform je Aufrufer unverändert, n8n-Vertrag unangetastet. Neue Schemas
  `momente_stimmung`, `beschreibung`.
- 2026-09-28 (B5, Florian: „nicht jeden Clip freigeben und bewerten müssen – das kann eine KI schneller, sagen
  will ich trotzdem, was hochgeladen wird“): **Lern-Bot filtert automatisch vor**, als Zusatz zum bestehenden
  Bewertungsweg (nichts entfernt). Neu `[lernbot].auto_schwelle` (Standard 0,0 = aus): liegt die Erwartung
  (`erwartung.py`, die schon vorhandene Vorhersage „gibst du 👍?“) eines frisch gebauten Entwurfs darunter, wird er
  still verworfen (`entwuerfe.auto_verworfen`, kein Foto an dich) und der Bot baut automatisch den nächsten – bis zu
  `auto_versuche_max` (Standard 3), danach kommt der letzte Versuch trotzdem durch. Neue Funktion
  `erwartung.vorhersage` (wie `festschreiben`, aber ohne zu speichern) – ein still verworfener Entwurf bekommt
  bewusst KEINE Zeile in `erwartungen`/`entwurf_bewertungen`, sonst würde die Erwartung ihr eigenes Urteil als
  Treffer zählen (Zirkelschluss). Du siehst weiterhin jeden Entwurf, der die Schwelle schafft, mit den normalen
  👍/👎-Knöpfen – die letzte Entscheidung bleibt bei dir. `/stand` zeigt zusätzlich, wie viele automatisch aussortiert
  wurden.
- 2026-09-28 (Regisseur 2.1, Florian: „immer die gleichen Übergänge, keine weiteren Effekte – das darf ruhig
  ordentlich viral sein, mit Slowmo und beschleunigt, viele Spezialeffekte, keine doppelten“): **Mehr Effekte im
  Spielbild, nichts doppelt.** Ersetzt „Spielbild clean“ vom 25.09. in diesem Punkt (Blitz, Wackeln und
  Farbversatz-Stoß sind jetzt erlaubt); Texte bleiben außerhalb des Spielbilds, alles bleibt mit
  `[regie.effekte] an = false` abschaltbar und über 🎆/💥/😵 lernbar. (1) **Übergangs-Mix:** Pool je Stimmung
  (33 xfade-Arten im Schema, u. a. whip_up/whip_right, radial, circleclose, smooth*, diag*, hblur, cover/reveal,
  flash = Weißblende), gemischte Runden ohne direkte Wiederholung, deterministisch aus der Momentfolge
  (`effekte.Uebergangsmix`). (2) **Speed-Ramps:** Zeitlupe um den Finisher (0,5; episch mit Serie ≥ 3 oder
  Victory 0,25) und Zeitraffer (2×) über einen langen Anlauf, beides im selben Segment möglich; die Zeitleiste
  (Beats) bleibt, nur die Quelle wird angepasst; Deckel `max_lupen`/`max_raffer` = 2. Renderer: `setpts` vor
  `fps`, Ton in Stücken. (3) **Impacts:** flash/shake/rgb, die Finisher wechseln den Stil (Punch · Punch+Blitz ·
  Wackeln · Punch+RGB), der Tod wackelt und blitzt. Doku: `docs/REGIE.md`, Abschnitt „Effekte“.
- 2026-09-28 (Regisseur 2.2, Florian: „das wird langweilig … mir ist egal, wie lange es rechnen muss, hauptsächlich
  es kommt ein sehr gutes Video raus … keine doppelten“): **Rechenzeit ist Nebensache, Dichte und Vielfalt zählen.**
  Effekt-Katalog im Spielbild (nur ffmpeg-Bordmittel, kein Paket): Negativ, Blur-Hit, Strobe, Farb-Pop,
  Kontrast-Punch, Farbrad, Vignetten-Puls, Pixel-Hit, Dutch-Tilt, Zoom-Einzug, Ken-Burns-Drift – dazu Punch, Blitz,
  Wackeln, RGB. Dichte: Einstieg auf jedem harten Schnitt, Drift auf jedem Segment, Beat-Effekte schon nach 0,8 s
  Ruhe (statt 2,5 s), Zeitlupe auf bis zu 8 Momenten (statt 2). Keine Wiederholung: eigene Stil-Rotationen für
  Finisher (11 Stile), Nebenkills, Beats und Schnitte, je Video an anderer Stelle beginnend (`effekte.Stilfolge`).
  Große Filtergraphen gehen als Datei an ffmpeg (`-/filter_complex`), damit lange Zusammenschnitte nicht an der
  Befehlszeile scheitern. Weiter über 🎆/💥/😵 lernbar und mit `[regie.effekte] an = false` abschaltbar.
  Tailscale-Deploy aus der Cloud-Sitzung blockt der Berechtigungs-Check („Containment Escape“) – Einspielen bis zur
  Freigabe per `regie-aktualisieren.sh` auf pve-mini.
- 2026-09-28 (Update-Paket, Florian: „in 5 min alles daheim updaten, möglichst schnell und selbstständig“):
  **Ein Befehl für alles:** `deploy/pve-mini/alles-aktualisieren.sh` auf pve-mini (curl aus `main`). Sichert
  Code-Stand und Datenbank (`vor-update-<Zeit>.*` neben der DB, nie gelöscht), wartet bis 10 min auf die
  Pipeline-Sperre, stellt Produktion auf `main` und den Lern-Bot auf den neueren von `main`/`lernschleife-publikum`,
  legt eigene Änderungen per `git stash` beiseite, übernimmt geänderte Dienste nur, wenn sie nicht von Hand angepasst
  sind, schaltet `clip-mikro` ein (`clip-aufraeumen` bleibt aus), startet beide Bots neu und schreibt ein
  Rückweg-Skript. Dafür kam der Sprint-Stand Lernschleife (82 Commits, 26.–28.09.) nach `main` – ohne neue volle
  Suite (letzte volle bei PR #19, danach breiter Lauf 255 Tests zu 2.1/2.2). Windows-Skript und pve-big unverändert.
- 2026-09-28 (Florian: „über 100-mal gesagt, dass die Shorts zu kurz sind“ und „Shorts sollten aus 4–10 Moments
  bestehen und zwischen 30 und 75 Sekunden liegen“): **Short = 30–75 s, 4–10 Momente, Start-Ziel 45 s.** Ursache der
  wirkungslosen Stimmen: `dauer_faktor` war auf 1,0 gedeckelt und die Short-Obergrenze 45 s stand nur in lokal.toml –
  nach 1–3 „⏱️ zu kurz“ war Schluss. Neu: `FORMATE["short"]` mit `ziel_s` 45, `min_momente` 4, `max_momente` 10;
  „zu kurz“/„zu lang“ verschieben das Ziel bis an die Grenzen (`regie.dauer_grenzen`: 0,667–1,667 = 30–75 s), jede
  Stimme wirkt. Auswahl und Nachlegen halten 4–10 Momente ein, Kürzen bleibt bei max_s. Alle gespeicherten Stimmen
  wirken sofort. `pipeline lernstand` zeigt „Short-Länge: n× zu kurz, n× zu lang … → Ziel x s“. Zusammenschnitt unverändert.
- 2026-09-29 (Florian: „nur Clips aus der neuesten Sitzung, optional aus einem Match – umstellbar“ und „viele Werte per
  Hand … nicht immer über den Server“): **⚙️ Einstellungen im Lern-Bot** (`/einstellungen` oder Knopf). Werte liegen in
  der Tabelle `einstellungen` und gehen vor lokal.toml und pipeline.toml; „↩️ Standard“ lässt wieder die Datei gelten.
  Nur Schlüssel aus `einstellungen.KATALOG`: Clip-Auswahl (`lernbot.quelle`: alle · neuester Spielabend bis 06:00 ·
  neuestes Match · ein Match per „📅 Match wählen“), Vorfilter (`lernbot.auto_schwelle`), Effekte an/aus, Musik
  Techno/Rock bevorzugt. Die Clip-Auswahl wird je Entwurf neu aufgelöst, zieht zuerst die Stimmung der Clips dieser
  Matches nach und steht als Hinweis vorn im Entwurf. Telegram statt Webseite: kein neuer Dienst, kein offener Port,
  schon auf Florian beschränkt (Prinzip 7). Weitere Werte kommen einfach als Zeile in den Katalog.
- 2026-09-30 (Queue-Punkt B5, erste Stufe): **Kalibrieren an einem echten Match** – `pipeline kalibrieren --session ID`
  und `/kalibrieren [ID]` im Lern-Bot (ohne ID: neuestes Match mit Clips). Zieht fehlende Stimmung nach (Whisper, ohne
  Claude), schreibt je Clip Kills mit Waffen-Nummer und Kategorie, Merkmale, Stimmung mit Sicherheit, Transkript-Anfang
  und 3 Standbilder nach `sessions/<ID>/kalibrierung/` (bericht.json); der Bot schickt je Clip ein Album und am Ende
  die Waffen-Nummern, die in `[merkmale.waffen]` fehlen. Pipeline-Sperre, weckt nie, löscht nichts. Bestätigen/Korrigieren
  per Knopf und Waffen direkt in lokal.toml schreiben sind die nächste Stufe.
- 2026-09-30 (Regisseur 3.0, Florian: „immer die selbe Grütze“, „er muss das selbst erkennen und lernen“, „ich möchte
  keine 100 oder 1000 Videos bewerten“): **Der Bot benotet sich selbst.**
  - **Schnittstile** im Short (Montage, Story, Steigerung, Kino, Klassik) als relative Stellschrauben, mit
    größerem Spielbild (`rahmen_zoom` bis 1,6; vorher ein schmaler Streifen).
  - **Cutter-Kritik** je Entwurf: Handwerksregeln, dazu Claude als Senior-Cutter auf einem Kontaktbogen (nur
    Leserecht, Schema `kritik`, höchstens 20 am Tag).
  - **Aus den Noten lernt er selbst:** Stil-Wahl per Thompson-Sampling; die Gründe des KI-Cutters speisen das
    Regie-Lernen, wobei deine Bewertung vorgeht; Entwürfe unter der Mindest-Note (50) sortiert er selbst aus.
  - Abschaltbar und einstellbar in ⚙️. Das Publikum bleibt das Hauptsignal. Doku: `docs/REGIE.md`, „Regisseur 3.0“.
