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
  Einzige Ausnahme (M106): der Briefkasten der Freunde – eigener SFTP-Dienst auf Port 2222, nur Aufnahmen von Freunden
  auf dem Durchweg zum Mini, nie durch n8n (`docs/BRIEFKASTEN.md`).
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
  jeder ffmpeg-Aufruf hat einen Wächter (180 s ohne CPU-Zeit = abbrechen), danach rendert der Rückfall auf der CPU
  (seit 09.10. auch beim Schneiden der Clips). ffmpeg stirbt mit seinem Aufrufer (`setpriv --pdeathsig`), sonst
  rechnete es nach einem Absturz verwaist ohne Sperre weiter; fertige Dateien kommen per fsync auf die Platte, bevor sie
  ihren Namen bekommen (`medien.uebernehmen`), sonst kann nach einem Stromausfall eine leere Datei „fertig“ sein.

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
- Mehrbenutzer (M1): Wie viel Speicher ist auf dem vServer frei (für den Briefkasten der Freunde, Stufe 2)?
- Stufe 3 (M148): Soll der Mini deine n8n-Matches, die seit über 3 h auf `neu` stehen, selbst nachholen (stündlicher
  Timer neben n8n)? Ja oder Nein – bis dahin Alarm von n8n und `pipeline process <ID>` von Hand.
- Stufe 3 (M149): Zweiter Rechner oder Cloud nur nach einem Messbefund aus `pipeline laufzeiten` (Auslöser in
  `docs/WORKER.md`) – dann: welcher Rechner, bei der Cloud Anbieter, Monatslimit in Euro und Text der Zustimmung.
- Stufe 4 (M157, M165–M170), gebündelt: (1) Darf clip-battle.de einen Zähler bekommen (nur Aufrufe je Video-Code und
  Tag, ohne Cookie und IP) – ja oder nein, und wer macht vorher die Rechtsprüfung? (2) YouTube verbinden (eigene
  Google-App, nur Leserechte – dann wären „wie lange geschaut wird“ und neue Abos je Video messbar)? (3) TikTok-Follower
  als Wochenwert (einmal neu zustimmen)? (4) Passt Zuschauer 0,5 · Follower 0,2 · Webseite 0,3 zu „clip-battle.de
  bewerben ist das Hauptziel“? (5) Wie viele Shorts lädst du pro Woche wirklich hoch (danach richtet sich, wann der
  erste Vergleich kommt)?
- Stufe 5 (M178, M169): (1) Soll Humor/Überraschung (Twist mit 1–2 lustigen Szenen) als fünfter Aufbau in den einfachen
  Modus? Dann kämen Fail-Szenen in „🎬 Neues Video“, und die Liga würde langsamer – ohne Antwort bleibt es bei vier.
  (2) Sollen die Zuschauerzahlen deiner Freunde abgeholt werden? Bis dahin bleibt ihre Liga leer.

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
  Einzige Ausnahme seit 08.10. (Florian: Ja): Der Puffer gibt Rohvideos über 14 Tage frei, deren Kopie im Lager
  bestätigt ist (Stufe B5, siehe 08.10.). Im Lager, bei Clips und in der Datenbank gilt „Nie löschen“ weiter.
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
    größerem Spielbild (`rahmen_zoom` bis ×1,3, begrenzt so, dass der Kill-Titel über der Bedienzone lesbar bleibt).
  - **Cutter-Kritik** je Entwurf: Handwerksregeln, dazu Claude als Senior-Cutter auf einem Kontaktbogen (nur
    Leserecht, Schema `kritik`, höchstens 20 am Tag).
  - **Aus den Noten lernt er selbst:** Stil-Wahl per Thompson-Sampling; die Gründe des KI-Cutters speisen das
    Regie-Lernen, wobei deine Bewertung vorgeht; Entwürfe unter der Mindest-Note (50) sortiert er selbst aus.
  - Abschaltbar und einstellbar in ⚙️. Das Publikum bleibt das Hauptsignal. Doku: `docs/REGIE.md`, „Regisseur 3.0“.
- 2026-09-30 (Florian: „warum nicht, ich dachte du hast alles schon vorbereitet“): **TikTok verbinden per `/tiktok`**
  im Lern-Bot bzw. `pipeline publikum anmelden [--code …]` – die Spec (§7.2) sah das vor, gebaut war nur das Erneuern
  vorhandener Tokens. `.env` braucht nur Client Key und Secret; Tokens nur in `publikum-oauth.json` (0600), nie im
  Chat oder Log; Rücksprung `https://clip-battle.de/tiktok/callback` (Seite muss nicht existieren, `[tiktok].redirect_uri`).
- 2026-09-30 (Florian: „nicht jeden Clip per Hand separat freigeben“): **Der Clip-Bot entscheidet selbst.**
  - **Vollautonom** (Florian: „muss es Referenzen geben, wenn ich sage, es soll autonom passieren?“ – nein): JEDER Clip
    wird beim Senden sofort entschieden – Regel (Serie ≥ 3 oder Victory → frei), ein gemessenes Tor, sonst die eigene
    Erwartung (≥ 50 % frei, darunter weich aussortiert; ohne Erwartung: Serie ≥ 2 frei). Keine Referenz-Urteile, keine
    Stichproben (`stichprobe_jede = 0`), keine Wartezeit; offene Altfälle entscheidet er gleich mit. Alles ohne Ton mit
    🤖-Zeile, pro Match eine stille Zusammenfassung; umdrehen am Clip freiwillig („🚫 Ganz raus“ = dein Verwerfen).
    `vollautonom = false` stellt den vorsichtigen Weg (Unsichere zu dir, 24-h-Frist) wieder her.
  - Aussortieren ist immer weich (bleibt Material für Regie, Highlight, Mikro); nur dein 🗑️ schließt aus. Nichts gelöscht.
  - `clips.freigabe_quelle` (du/auto) + `vorgelegt`/`auto_*`; automatische Entscheidungen zählen nie beim Lernen, in der
    Erwartung oder im Tor. `/auto`, `/clip <nr>`, ⚙️ auch im Clip-Bot (Modus an/👀 probe/aus, Genauigkeit,
    Aussortieren, Frist). n8n-Vertrag unverändert. Annahmen A1–A9: `docs/ENTSCHEIDUNGEN.md`, „Auto-Freigabe“.
- 2026-09-30 (Cutter-Maßstab 1.0, Florian: „mach die Schnittregeln professionell“):
  - Benotet wird das **fertige Video** (ein ffmpeg-Messlauf, Bordmittel): 14 Kriterien mit stetigen Kurven, 6 K.O.-Tore
    (Schwarz, Standbild, > 3 Blitze/s, Ton, Länge, doppelter Moment) deckeln auf 40 und sortieren aus.
  - Der Renderer garantiert die Hygiene: −14 LUFS / −1,5 dBTP, einheitlicher Limiter, kurze Musik-Kanten, Strobe ≤ 2,5 Hz.
  - Gewichte lernt `massstab.py` selbst (Faktoren 0,5–2) – nur von Lehrern, die die Note nicht kennen: KI-Cutter blind
    (20 Bilder + Wellenform), Publikum, dein 👍/👎. Schwelle = Q20 der eigenen Noten (40–50), ab 20 gemessenen Entwürfen.
  - `pipeline kritik --id N`, `pipeline massstab --nachmessen`; Doku `docs/REGIE.md` „Cutter-Maßstab“, Annahmen
    `docs/ENTSCHEIDUNGEN.md`.
- 2026-10-05 (Florian: „mein persönlicher Impact wird zu wenig gewertet – am Schluss soll er es aber selbstständig besser
  machen als ich“): **Dein Geschmack ist der Start, das Publikum übernimmt mit Belegen.**
  - Regie-Lernen: ein KI-Cutter-Urteil wirkt mit einem Drittel deines Schritts (`regie_lernen.KI_STAERKE` 0,34); deine
    letzte ausdrückliche Ansage je Gegensatz („zu kurz“/„zu lang“, „zu viele Effekte“/„mehr Action“) überstimmt
    widersprechende KI-Gründe, bis du sie selbst änderst.
  - Publikums-Modell: deine Daumen zählen wie `autonom.dein_gewicht` Videos (je 10 Bewertungen eins, 2–12; vorher fest 2).
  - Cutter-Maßstab: ein Paar aus deinem 👍/👎 zählt 2,0 (vorher 1,0). `/lernstand` zeigt „👤 Dein Einfluss“ in Prozent.
- 2026-10-05 (Fail-Format, Florian: „Es soll Viewer ziehen, nicht nur lustig sein“ und „nur den Button drücken … mit KI
  und aus Daten lernen, nicht durch 100te Bewertungen … professioneller Schnitt mit einem Hauch Humor“):
  - **Fail-Momente als Material** (`fail.py`): je eigenem Tod ein Moment aus dem Rohvideo im Puffer (12 s vor bis 3 s
    nach dem Tod, `fail:<match>:<sekunde>`). Fakten: Platz, Kills davor, Bot, selbst, Waffe. Automatisch im Mic-Schritt
    nach render, von Hand `pipeline fail --session ID | --nachziehen`. n8n-Vertrag und render-JSON unverändert.
  - Kein starres Regelwerk: Der Fail-Score ist nur Startwert. Die **KI schätzt je Moment** viral/humor/spannung ein
    (`viral.py`, claude -p, Kontaktbogen + Fakten + Transkript, Tageslimit, je Moment einmal). Titel nur aus Fakten,
    ohne Emoji.
  - **Ein Knopf „🔥 Viral-Video“** (`/viral`): Der Bot wählt die Mischung selbst (Twist = Highlights + 1–2
    Fails/Gags, Highlights oder reines Fail-Video). Die Wahl lernt per Thompson-Sampling über Cutter-Noten und
    Publikum. Der Bot baut mehrere Fassungen und schickt die beste.
  - Hook-Teaser, Zeitlupe und Standbild am Tod sind Werkzeuge mit Wahrscheinlichkeit, keine Pflicht. Variante,
    Mischung, KI-Mittel und Werkzeuge sind Merkmale fürs Publikums-Lernen. Fail-Momente kommen nur in 🔥 Viral.
    Doku `docs/REGIE.md` „Fail-Format und 🔥 Viral-Video“.
- 2026-10-06 (Florian: „gefühlt bewerte ich genau den gleichen Mist wie früher … liegt zu wenig Material vor?“):
  **Deine Entwurf-Bewertungen lehren jetzt die Moment-Formel.** Ursache im Code: Dein 👍/👎 im Lern-Bot änderte nur
  den Schnitt und den Bonus genau der gezeigten Momente. Die Formel, die neue Momente auswählt (`lernen.py`), lernte
  nur aus Clip-Bot-Freigaben und Battles, und Freigaben machst du seit der Auto-Freigabe (30.09.) nicht mehr.
  - Neue Paar-Quelle `entwurf`: je Moment die Summe deiner Urteile (👍 +1, „🥱 langweilig“ −1, 👎 ohne Grund −0,5,
    nur Schnitt-Gründe 0); Plus-Momente > Minus-Momente, Gewicht 0,5 (`[lernen].gewicht_entwurf`). Fails bleiben
    draußen. Alle gespeicherten Bewertungen wirken sofort; der Lern-Bot rechnet vor jedem Entwurf neu.
  - **🔎 /warum** (Knopf, `pipeline warum`): Material, Wiederholung und Lernstand aus den echten Daten, mit Fazit.
    Nur lesen.
- 2026-10-06 (Florian: „wie kann ich die Videos wieder länger werden lassen? Der Bot macht sie schon wieder sackrisch
  kurz“): **Der Cooldown hält jetzt das Ziel ein, nicht nur 30 s.**
  - Ursache im Code: Die Sperre für Momente aus den letzten 3 Entwürfen wurde nur aufgehoben, wenn die freien Momente
    nicht einmal 30 s (min_s) ergaben. Bei wenig Material oder enger Clip-Auswahl endeten Shorts bei 30–35 s statt beim
    Ziel (45–75 s). Im Test wurde ein 65-s-Ziel so zu 34 s. Jetzt zählt das Ziel; das Nachlegen nimmt notfalls
    gesperrte Momente („Cooldown aufgehoben – sonst zu kurz“). Gezeigte Momente verlieren weiter Punkte.
  - **⚙️ „Short-Länge“** (`[regie].short_mindestens_s`): Untergrenze fürs Ziel (45/55/65/75 s); Lernen, KI-Cutter und
    Publikum dürfen nur darüber gehen. Standard 0 = wie bisher gelernt.
- 2026-10-06 (Florian: „Das ist und wird doch alles zu kompliziert. Kannst du das simplifizieren?“): **Einfacher Modus
  im Lern-Bot ist Standard.** Zählung vorher: 14 Befehle, 9 Menü-Knöpfe, 9 Gründe-Knöpfe, 13 Einstellungen, Lernstand
  19 Zeilen. Jetzt: ein Knopf „🎬 Neues Video“ (= 🔥 Viral, der Bot wählt Mischung und Fassung), 👍/👎, bei 👎 vier
  Gründe (zu kurz · langweilig · zu viele Effekte · Musik) + „➕ mehr“, ⚙️ mit vier Einstellungen (Clips, Short-Länge,
  Effekte, Musik) + „🔧 Alle Einstellungen“, „📋 Stand“ in sechs Zeilen ohne Fachbegriffe, Entwurfstext ohne
  Lernstand/Stil/Look/Kritik/Erwartung. **Nichts gelöscht:** `/experte` (oder ⚙️ → 🔧 → Experten-Modus) schaltet alles
  Bisherige wieder ein (`[lernbot].experte`). Clip-Bot, Lern-Systeme und n8n-Vertrag unverändert. Regel für mich:
  neue Stellschrauben kommen nur noch hinter „🔧 Alle Einstellungen“, und ich erkläre dir Änderungen ohne
  Fachbegriffe.
- 2026-10-07 (Stufe 1 des Umbaus, Florian: „macht keinen Spaß mehr … immer die gleichen Clips, Spannungskurve, Musik …
  nichts dagegen tun können“; Antworten: Bot macht alles · gute Szenen = Multikills, Clutches, Spannung · viel mehr
  Musik · zuerst einfach & zuverlässig · ❌ → kurz fragen, dann neu · zu wenig Szenen → lieber kein Video):
  - **Ein Ablauf:** Abend vorbei → Statuszeile „🎮 Abend erkannt“ → Video nur aus starken Szenen des Abends (die
    Zeile verschwindet) oder „kein Video, weil …“ (die Zeile wird umgeschrieben). Unter dem Video nur ✅ Hochladen
    (Upload-Paket) und ❌ Nicht gut.
  - **❌ → ein Tipp = feste Regel** (`regeln.py`, kein Lernen, keine KI dazwischen) und sofort eine neue Fassung aus
    denselben Matches: ⏱️/⏳ Ziel ±10 s (`regie.short_ziel_s`), 🥱 schwächere Hälfte der Szenen für immer gesperrt,
    🎵 Song für immer gesperrt (Tabelle `sperren`), 😵 Effekte eine Stufe ruhiger (`regie.effekt_stufe`), 🔁 neu.
  - **Nur starke Szenen** (`regie.szenen = "stark"`): Multikill, Victory, Clutch, Kill im Endkampf; unter 4 → kein
    Video. Songs: keiner kommt vor 8 Videos wieder. Aufbau: Montage → Story → Steigerung → Kino der Reihe nach.
  - **Ruhe:** Clip-Bot still (`[bot].clips_zeigen = false`, nur Warnungen/Highlights); im einfachen Modus fest aus:
    KI-Cutter, Selbst-Aussortieren, Vorfilter, Abendstand (`einstellungen.EINFACH_FEST`). Fail-Videos nur über /experte.
  - Nach jedem Update „✅ Neue Version läuft“; 📋 Stand zeigt deine Regeln und den letzten Abend. Anleitung:
    `docs/SO-GEHTS.md`. Nächste Stufen: Spannung ohne Kill erkennen · viel mehr Musik · Lernen sichtbar zurück.
  - Nach der Prüfung: Ein unterbrochenes Rendern (Update, Neustart) holt der nächste Timer-Lauf nach (bis 12 h),
    die Effekt-Stufe geht vor dem alten ✨-Schalter, der Clip-Bot ist nur still, wenn die Auto-Freigabe auf „an“
    steht (sonst bekäme kein Clip eine Entscheidung), Doppeltipps wenden eine Regel nur einmal an. Die n8n-Nachricht
    „Match verarbeitet“ ist in `1-match-verarbeiten.json` deaktiviert (in n8n selbst einmal von Hand).
- 2026-10-07 (Stufe 2 des Umbaus „Lernen zurück“, Florian: lernen aus ✅/❌ und KI-Urteil · mutig ausprobieren ·
  einmal pro Woche sehen; Reihenfolge danach: Spannung ohne Kill = Endkampf, langes Feuergefecht, knapp überlebt ·
  Musik = Techno/Hardstyle und Phonk; hochgeladen wird auf TikTok und YouTube Shorts):
  - `geschmack.py` stellt im einfachen Modus drei Schrauben selbst ein: Aufbau (Montage/Story/Steigerung/Kino),
    Tempo (Segmente ×0,8/×1,25), Zeitlupe (max_lupen 8/2). Thompson-Sampling je Schraube; Lehrer: dein ✅/❌
    (Gewicht 1, der Grund grenzt ein – ⏱️/⏳/🎵 treffen keine Schraube, 😵/🎆/💥 nur Tempo/Zeitlupe) und die KI-Note
    (0,34). Alte Bewertungen zählen sofort für den Aufbau. Ersetzt die feste Aufbau-Reihenfolge aus Stufe 1.
  - Mutig (`geschmack.mut` 0,5, ⚙️ → 🔧 → 🧪): jedes zweite Video stellt eine Schraube auf ihre am wenigsten
    erprobte Einstellung; nie dreimal derselbe Aufbau. Deine Regeln (`regeln.anwenden`) kommen danach.
  - KI-Urteil erst nach dem Senden (Lern-Bot-Schleife, eigener Thread, Pipeline-Sperre, je Entwurf ein Versuch pro
    Bot-Lauf, Tageslimit wie bisher) – das Video kommt nicht später; beim Bauen bleibt die KI im einfachen Modus aus.
  - Wochenbericht sonntags ab 18 Uhr (Lern-Meldung `woche:<JJJJ-Www>`, ohne Videos keiner); 📋 Stand zeigt eine Zeile.
  - Nach der Prüfung: Von der KI zählt im einfachen Modus nur die Note (ihre Gründe lehren das Regie-Lernen nicht mehr
    mit); übersteuert das Publikums-Modell eine Schraube, bekommt sie für dieses Video weder Lob noch Tadel; Zeitlupe
    zählt nicht bei Effekten aus; Fail-Videos zählen nicht für den Aufbau; die Sperre gilt nur fürs Messen, nicht
    für den Claude-Aufruf.
- 2026-10-07 (Florian: „warum sendet er jetzt immer 2 Videos?“ – zwei verschiedene auf einmal): Die Lern-Bot-Schleife
  schickte gerenderte Entwürfe alle 30 s los, auch während `neuer_entwurf` eine Fassung noch prüfte; fiel sie durch,
  kam die nächste Fassung hinterher. Jetzt schickt die Schleife nichts, solange der Bot baut (`sende_wenn_frei`).
- 2026-10-07 (Florian: „es kommen immer noch die Clips, die vor 14 Tagen gut waren, nicht die neueste Session“): Drei
  Ursachen im Code. (1) „🎬 Neues Video“ nahm ab Werk alle Clips im Puffer (14 Tage) – die alten starken gewannen;
  jetzt ab Werk der neueste Spielabend (`lernbot.quelle = "abend"`), 🎯 Clips wieder im einfachen ⚙️-Menü.
  (2) Das Abend-Video wartete auf die Datei vom PC, die nur mit `SessionVorbeiMinuten > 0` kommt (ab Werk 0) – jetzt
  erkennt der Mini das Abend-Ende selbst (`sitzung.auto_abend`: 45 min kein neues Match, nur der letzte Block der
  letzten 18 h, nie ältere Abende). (3) `alles-aktualisieren.sh` richtete `clip-sitzungen.timer` nie ein – jetzt
  installiert und schaltet es ihn ein.
- 2026-10-07 (Florian: „ich tippe ❌ → 🥱 und sehe das gleiche Video mit anderen Schnitten“; 🥱 = der Schnitt
  langweilt, die Szenen sind ok): **🥱 sperrt nichts mehr** – ersetzt bei 🥱 den Stufe-1-Punkt „schwächere Hälfte für
  immer gesperrt“ (alte Zeilen bleiben in `sperren`, gelten aber nicht; 🎵-Sperren wirken weiter).
  - Die neue Fassung ist **anders geschnitten**: Aufbau mit anderer Reihenfolge, Tempo umgedreht (mindestens 15 %
    spürbar), anderer Song (`regeln.neue_fassung`, `geschmack.waehle(anders=…)`). Fester Stil aus ⚙️ und deine Regeln
    gehen vor.
  - **Szenen:** die stärkere Hälfte bleibt, die schwächere fehlt nur in dieser Fassung. Ersatz nur aus nie gesehenen
    Szenen: erst vom Abend (auch Einzelkills), dann starke früherer Abende; sonst kein Video mit klarem Satz
    (`regie.fassung_kandidaten`, `KeineNeuenSzenen`).
  - **Dieselbe Szene** unter mehreren Schlüsseln (Clip, Nvidia, SteelSeries): Fenster überlappen ≥ 3 s oder ≥ 40 %
    (`szenen.py`). Je Video nur einmal, Abwechslung und Cooldown je Szene – in allen Videos.
  - **Lernen:** 🥱 zählt nicht mehr gegen die Szenen (Moment-Formel und Moment-Bonus), nur gegen Aufbau/Tempo/Zeitlupe.
    Annahmen Z1–Z9: `docs/ENTSCHEIDUNGEN.md`, „🥱 = Schnitt“.
  - **Texte** (Florian: „fehlerhafte Texte“, vereinfachen): ❌ am Highlight-Video lässt es nur weg (keine Short-Regel,
    kein Short); ~~baut der Bot gerade, wirkt ein Grund nicht („tipp gleich nochmal“)~~ – seit 08.10. Merkliste; zu kurze starke Szenen ergeben eine klare
    „kein Video“-Zeile; „Abend vom“ = Spielabend (06:00); ✅ bleibt stehen; Waffen-Nummern nur ins Log,
    Publikums-Scores nur unter /experte; im einfachen Modus „Video“/„Szenen“ statt „Entwurf“/„Momente“.
- 2026-10-08 (Florian: „Wenn ich alles per Hand einstellen kann/muss, brauchen wir über das Ziel nicht weiter zu reden –
  autonom und besser und schneller als mit der Hand zu schneiden“): **Nichts mehr von Hand.** Plan in 5 Stufen und
  Annahmen N1–N8: `docs/ENTSCHEIDUNGEN.md`, „Nichts mehr von Hand“.
  - **Stufe 1 (umgesetzt):** einfacher Modus ohne ⚙️ und ohne „stell um“/„tipp nochmal“; fest: neuester Abend, Stil
    automatisch, nur starke Szenen (`einstellungen.EINFACH_FEST`, `einstellungen.fest`). 🥱 hebt gesenkte Effekte
    wieder. Merkliste `entwurf_bewertungen.folge`: jeder Tipp wird gespeichert und erledigt, sobald der Bot frei ist.
    Eine Render-Panne beim Abend-Video holt der Timer einmal auf der CPU nach.
  - **Florians Antworten:** Der Puffer darf Rohvideos nach 14 Tagen löschen, wenn ihre Kopie im Lager bestätigt ist
    (ersetzt für den Puffer „Nie löschen“ vom 25.09., Stufe 2). Keine Auto-Updates. Dünne Abende mit nie gesehenen
    starken Szenen auffüllen (Stufe 4).
  - **Stufe 2 (umgesetzt): Puffer gibt frei** (`lager.gib_frei`, `[puffer].freigeben = true`): nur am Ende eines
    fehlerfreien Abgleichs, der das Lager erreicht hat; nur Videos aus `eingang/`, deren Aufnahme UND Bestätigung im
    Lager älter als 14 Tage sind, die im Puffer unverändert sind und deren Lager-Kopie jetzt da, gleich groß und beim
    Zurücklesen gleich (SHA-256) ist. Kein Link, kein fremdes Dateisystem im Pfad. Erster Lauf mit etwas zum Freigeben
    = Probe; jede Löschung in `ereignisse` (`puffer_frei`), eine Zeile in der Abschlussmeldung. Annahmen N9–N15.
    Kein Auto-Update (Florian: „Nein, ich spiele selbst ein“).
  - **Stufe 3: 2-Wochen-Video nur im Lern-Bot, Clip-Bot wirklich still** (Clip-Bot still UND einfacher Modus,
    `bot.app._nur_probleme`): Das Highlight-Video schickt nur noch der Lern-Bot („🏆 Dein 2-Wochen-Video“); ✅ dort =
    freigegeben und hochgeladen (Paket aus der fertigen Datei – Stufe 2 gibt seine ältesten Szenen schon frei –, ohne
    TikTok-Häkchen, dafür „Volle Qualität: Netzlaufwerk clips → Ordner highlights“), ❌ = verworfen. Keine Erinnerung
    ans Hochladen. Start und glattes Ende einer Übertragung werden nur vermerkt (`meldungen.routine`, setzt der
    Schreiber); Fehler, Abbrüche, Probe, liegen gebliebene Videos und Warnungen kommen weiter. /experte wie bisher.
    Annahmen N16–N20.
  - **Stufe 3: ✅ legt den TikTok-Post selbst an** (`lernbot_paket.posts_anlegen`): Im einfachen Modus entsteht der
    Post, sobald das Paket eines Shorts bei dir ist (je Plattform aus `[publikum].plattformen`, idempotent, ohne Link
    und Video-Nummer – das Video findet der tägliche Abruf). Keine Checkliste, kein „✅ TikTok erledigt“, kein /link;
    dort steht „Lad es hoch – die Zahlen hole ich mir danach selbst.“ Scheitert das Paket, kein Post (der nächste
    Versuch der Merkliste legt ihn an); scheitert nur der Post, kommt das Paket nicht doppelt. Kein Post für das
    2-Wochen-Video und Querformat. /experte wie bisher. Annahmen N21–N24.
  - **Stufe 3: Der Bot findet dein hochgeladenes Video selbst – nur eindeutig** (`publikum_adapter._tiktok_zuordnen`):
    Kein Link und kein Achten auf den Zeitpunkt mehr (vorher zählte nur ein Upload bis 30 min nach dem Häkchen). Ein
    Video passt zu einem offenen Post bei ±72 h und ±2 s Länge; zugeordnet wird nur, wenn beide nur zueinander passen,
    bei mehreren entscheidet die erste Zeile der Beschreibung (die Caption des Pakets, nachgerechnet). Zusätzlich zur
    Spec: eine andere erste Zeile schließt ein Video aus, und ohne passende erste Zeile wird erst zugeordnet, wenn das
    Fenster zu ist. Sonst bleibt der Post offen. `gepostet_utc` wird die Upload-Zeit (Tag 7 ab dem Upload). N25–N29.
  - **Stufe 3: Auch Flops bekommen ihre Zuschauer-Note** (`publikum_adapter.importiere`): Gleiche Zahlen wie beim
    letzten Abruf zählen nach 20 h wieder als Messung (der Timer streut bis 10 min), am selben Tag bleibt es bei einer.
    Vorher bekam ein Video, das ab Tag 2 nicht mehr wächst, nie eine Messung ab Tag 3 und damit nie einen Score – nur
    per Screenshot. Abweichend von der Spec nur, bis der Post seinen Score hat (der wird nie überschrieben, und jede
    Messung lässt `autonom` alles neu rechnen – ohne Grenze fast 5 min je Lauf). N30–N32.
  - **Stufe 3: Zahlen-Abruf richtet sich selbst ein, 📋 sagt ehrlich, wer lehrt:** `alles-aktualisieren.sh` richtet
    `clip-publikum` ein und schaltet den Timer an wie `clip-sitzungen` (Rückweg-Zeile; von dir angepasste Timer werden
    weder überschrieben noch eingeschaltet – gilt jetzt für beide). 📋 endet mit „🧠 Lernt aus: deinen ✅/❌ · KI-Note (läuft |
    fehlt – Claude-Anmeldung nötig | kommt mit dem nächsten Video) · Zuschauern (n Videos ausgewertet | TikTok nicht
    verbunden – einmal /tiktok | n ✅-Videos nach 3 Tagen noch ohne Zahlen)“ (`geschmack.lehrer_zeile`, ohne Netz:
    `kritiken`, `posts`, `.env` und Token-Datei wie `publikum_adapter._token`); sie ersetzt „🧠 Gelernt aus …“ und „📊
    Publikum: …“. Dieselbe Zeile im Wochenbericht, wenn ein ✅-Video nach 3 Tagen keine Zahlen hat. N33–N37.
  - **Prüfung Stufe 2/3 (drei Prüfer, nachgestellt):** Voller Puffer: Der Abgleich läuft ohne DB-Sicherung weiter und
    gibt frei (vorher brach er jeden Tag daran ab). Löschen nur strenger: auch 14 Tage vor der jüngsten Aufnahme (Uhr
    des Mini kann springen); fehlt eine Lager-Kopie, wird sie beim nächsten Abgleich neu kopiert statt täglich gewarnt;
    ein Kopierfehler hält die Freigabe weiter an, die Meldung sagt es. Abruf: Posts ohne Score zuerst (sonst ab ~100
    Posts keine Noten mehr). Erste Caption-Zeile im einfachen Modus mit Songtitel, am Post gespeichert (zwei ✅ eines
    Abends bekamen vorher nie Zahlen). Nach dem Paket: „füg den Text oben unverändert ein“. KI-Note im 📋 mit echtem
    Grund; n8n „Highlight-Video fertig“ aus; Kopfzeile bleibt nach dem Tipp. N38–N47.
  - **Stufe 4: Zu kurz? Erst mehr Anlauf** (`regie.mehr_anlauf`): Bleibt ein Short im einfachen Modus unter 30 s oder
    mehr als 10 s unter dem Ziel, plant der Bot einmal neu – Anlauf/Ausklang mindestens 4/3 s statt gelernt 2,5/1,5 s,
    dieselben Szenen, kein Füllmaterial (vorher „kein Video, die starken Szenen ergeben nur 22 s“ bzw. 32 s bei Ziel
    45 s; jetzt 34 bzw. 44 s). Reicht es nicht, „kein Video“ wie bisher; gilt auch für 🥱; /experte unverändert. N48–N50.
  - **Stufe 4: Nachschub – starke Szenen, die du noch nie gesehen hast** (`regie.erstelle`, Florian 08.10.: „Ja,
    auffüllen“): Hat der Abend weniger als 4 ungesehene starke Szenen oder bleibt das Video auch mit mehr Anlauf zu
    kurz, kommen nie gezeigte starke Szenen früherer Abende dazu – nur aus Matches der letzten 12 Tage (rohdaten_tage
    − 2, sonst fehlt beim ✅ das Rohvideo), nie Einzelkills, nie Fails, höchstens die Hälfte, vorn immer eine Szene vom
    Abend; im Video steht „+2 Szenen von früheren Abenden“. Gilt fürs Abend-Video, 🎬 und jede neue Fassung nach ❌
    (auch 🥱, dort jetzt mit denselben Grenzen). „Kein Video“ nur, wenn auch das nicht reicht – der Satz sagt, warum.
    Nachgestellt: dünner Abend (2 starke) vorher „kein Video“, jetzt 41 s mit 2 + 2 Szenen; 🎬 nach dem Abend-Video
    vorher dieselben 4 Szenen, jetzt 3 neue von 6. /experte und eine Match-Wahl dort exakt wie bisher. N51–N59.
  - **Stufe 4: Nachtrag – spät angekommenes Material zählt noch** (`sitzung._nachtrag`): Endete der neueste Abend mit
    „kein Video“ und kommt danach noch etwas an (Clip-Dateien, Matches, die n8n später fertig hat, ein Match, das der
    PC erst beim nächsten Start schickt), baut der 10-Minuten-Timer das Video bis 24 h nach dem Abend selbst nach –
    vorher blieb es für immer bei „kein Video“. Nur mit neuen Szenen seit dem letzten Versuch, erst nach 45 min ohne
    weitere Szene und ohne neues Match (wer nach einer Pause weiterspielt, bekommt das Video erst danach); höchstens
    ein Video je Abend (keins, wenn 🎬 inzwischen eins gemacht hat), nie ältere Abende, nur einfacher Modus. Beim
    selbst erkannten Abend zählen spät angekommene Matches desselben Abends mit (Lücke ≤ 2 h). Die Statuszeile wird
    zu „🎮 Nachtrag: Abend vom …“ und geht mit dem Video; reicht es wieder nicht, bleibt es still. Nachgestellt
    (Prüfer s5, s5b, PC früh aus): vorher für immer „kein Video“, jetzt je ein Video; nach einer Pause weitergespielt:
    vorher Video mitten im Spielen, jetzt danach. N60–N65.
  - **Stufe 4: Abwechslung mit Ermüdung** (Florian 08.10.: „Manche Szenen nerven einfach nur noch … auch wenn es gute
    Bewertungen hat“, dann als Korrektur zu „jede Szene nur einmal“: „die Momente dürfen ruhig öfter und gemischter
    genutzt werden aber nur weil ein Clip gut ist muss der nicht immer egal wo verwendet werden … bessere öfters zeigen
    aber nicht permanent“; `szenen.verlauf`, `regie.erstelle`): Im einfachen Modus gehen neue Szenen immer vor. Eine
    Szene, die gerade erst (48 h) in einem deiner letzten 3 Videos lief, kommt nicht ins nächste (Fassungen eines Videos
    zählen als eines; für die Länge nie aufgehoben – nur wenn 🎬 oder eine neue Fassung sonst gar kein Video hätte,
    sperren die letzten 2). Bekannte starke dürfen wiederkommen, verlieren aber je Einsatz der letzten 30 Tage die
    Hälfte ihrer Punkte; höchstens die Hälfte eines Videos bekannte (bis 4 Szenen dürfen es mehr sein), ganz ohne Neues
    auch nur bekannte – nie mehr als 2, die schon zusammen in einem Video waren (Fassungen zählen mit). 🎬 und neue
    Fassungen planen bis zu dreimal – vom Abend, gemischt aus den letzten 12 Tagen, gemischt und locker (beliebig viele
    bekannte, aus einem alten Video höchstens die Hälfte) – und nehmen den ersten Plan, der das Ziel bis auf 2 s
    erreicht, sonst den längsten; nie unter 4 Szenen. Das Abend-Video bleibt beim Abend (vorn vom Abend, höchstens die
    Hälfte von früher, Nachschub schon, wenn es sein Ziel verfehlt). Eine neue Fassung nach ❌ behält die Szenen ihres
    Videos (keine Wiederholung, außer sie liefen gerade erst in einem anderen); 🥱 tauscht gegen neue, dann bekannte
    starke, zuletzt Einzelkills vom Abend. Unter dem Video „♻️ 2 Szenen kennst du schon“. Werte intern in `[regie]`
    (`ermuedung_tage`, `sperre_stunden` …), kein ⚙️. Nachgestellt (4 Abende, dann 14× 🎬): die beste Szene in Video 1,
    5, 9, 13, 17 (vorher nie wieder), die anderen 3–4-mal, nie zweimal in vier Videos hintereinander; 🎬 nach dem
    Abend-Video vorher „Kein neues Video“, jetzt ein gemischtes. Ersetzt „Jede Szene nur in einem Video“ (N66–N70);
    /experte, 2-Wochen-Video, 🔥 Viral und Lernen unverändert, gelöscht wird nichts. N84–N91, nach der Prüfung N92–N98.
  - **Stufe 4: Musik füllt sich selbst auf** (Florian 07.10.: „Techno/Hardstyle und Phonk“, „viel mehr Musik“;
    `musik.nachschub`, `einstellungen.DEINE_GENRES`): Im einfachen Modus haben nur noch deine Genres Vorrang bei der
    Musikwahl – Techno, Hardstyle, Hardcore, Phonk, Brazilian Phonk (auch im 2-Wochen-Video; Rock nicht mehr, alte Titel
    bleiben). Sind davon weniger als 16 Songs frei (🎵-Sperren zählen ab), lädt `pipeline sitzungen` am Ende eines
    Laufs selbst bis zu 10 NCS-Titel mit Quellenangabe nach – nur 10–17 Uhr, höchstens einmal am Tag (Merker vor dem
    Laden), Fehler nur ins Log, kein Chat. Abweichung vom Plan: gezählt werden nur deine Genres – live hat NCS nur 9
    Techno- und 4 Hardcore-Titel, und Titel anderer Genres hielten die Gesamtzahl über 16, Hardstyle und Phonk kämen
    nie. /experte wie bisher (Musik von Hand); gelöscht wird nichts. N71–N74.
  - **Stufe 5: Zuschauer lehren Aufbau, Tempo und Zeitlupe** (`geschmack.statistik`, Florian: „keine 100 oder 1000
    Videos bewerten“, „mach doch endlich ein Video das sich immer wieder verbessert“): Im einfachen Modus sind die
    Zuschauer jetzt der dritte Lehrer neben deinem ✅/❌ (Gewicht 1) und der KI-Note (0,34) – vorher lernten die drei
    Schrauben dort gar nicht vom Publikum. Jedes hochgeladene Video mit Zuschauer-Note zählt für jede Schraube, die
    darin wirkte, doppelt (Treffer (y + 1)/2, wie `stile.statistik` unter /experte). Nachgestellt (Publikum mag
    „erzählt“): Anteil „erzählt“ in den Videos 31–60 von 24 auf 56 %, Zuschauer-Note im Schnitt +0,01 → +0,34. Der
    Wochenbericht sagt „👀 Bei den Zuschauern kommt gut an: …“. Verstellt das Publikums-Modell nur Feinwerte des Aufbaus,
    zählt der Aufbau trotzdem (sonst lernte ihn niemand mehr). Ohne Zuschauer-Noten alles wie bisher; /experte
    unverändert. N75–N78.
  - **Stufe 5: ⏱️/⏳ als Grenze mit Richtung** (`regeln.laenge`, `regie_lernen.laengen_richtung`): Im einfachen Modus
    ist deine Länge keine feste Zahl mehr, die nur du umstellen kannst: Nach deinem letzten „⏱️ zu kurz“ ist sie eine
    Untergrenze, nach „⏳ zu lang“ eine Obergrenze (Richtung aus deinen Bewertungen, kein neuer Schlüssel). Dein Wert ist
    der Start; darüber bzw. darunter wählt das Publikums-Modell mit Belegen, nie dagegen – ohne Zuschauerzahlen bleibt
    es genau dein Wert (nicht der alte gelernte, der nach vielen „zu kurz“ bei 75 s stünde). ⏱️/⏳ zählen von dem Video,
    das du gesehen hast (10 s über bzw. unter Ziel oder echter Länge); Texte „mindestens“/„höchstens“. Ein Längen-Versuch
    des Publikums-Modells, den die Grenze verschiebt, fällt für das Video weg (sonst stünde ein nie getesteter Versuch in
    `lern_experimente`). Nachgestellt mit Publikum, das 60–70 s mag: Untergrenze 55 → 65 s; ⏳ am 65-s-Video →
    höchstens 55. /experte und ein ⚙️-Wert ohne ⏱️/⏳ bleiben fest. N79–N83.
  - **Prüfung Stufe 4/5 (zwei Prüfer, nachgestellt):** Die Sperre galt ohne Zeitgrenze – nach einer Woche Pause blieb
    der neue Abend ohne Video (jetzt 40 s); die Fassung eines älteren Videos holte Szenen aus dem Video direkt davor.
    🎬 nahm einen Abend-Plan bis 10 s unter dem Ziel, obwohl gemischt genug da war (43 statt 62 s bei „mindestens
    65 s“), und ließ 3 Szenen aus einem alten Video als Video durch – jetzt die drei Pläne oben. ⏱️/⏳ an einem älteren
    Video verschoben deine Grenze gegen deine Richtung (⏱️ an 40 s bei „mindestens 55“ ergab 50) – jetzt nie zurück
    („schon mindestens 55 s“). Nach „⏳ höchstens …“ hält er deine Grenze jetzt wirklich ein: Die letzte Szene schoss
    vorher darüber (61 statt 53 s bei „höchstens 55“), und mehr Anlauf gibt es nur noch unter 30 s (vorher wurden aus
    43 s 62 s); nur wenn schon 4 Szenen länger sind, bleibt es länger. Bleibt ein Short unter deiner Mindestlänge,
    steht es darunter. Nachgestellt mit 10 Abenden Vorgeschichte, dann 10× 🎬: 11 von 11 Videos mit 46–71 s, keine
    Szene in mehr als 3. N92–N99.
- 2026-10-08 (M1, Clip-Pipeline 4.0 – Florian: „Wie kann ich Freunden das an die Hand geben, ohne dass sie einen Server
  oder ähnliches brauchen?“ → „Telegram wie bei dir“, für dich selbst „Auch einfacher“, und „du baust es noch komplett
  kaputt, wenn du so weiter machst. Ich wollte es simplifizieren“): **Mehrbenutzer = eine abgeschlossene Instanz je
  Freund.** Plan: `docs/MEHRBENUTZER.md`, Annahmen M2–M23: `docs/ENTSCHEIDUNGEN.md`, „Mehrbenutzer (Clip-Pipeline 4.0)“.
  - Je Freund ein eigener Linux-Benutzer `clip-<name>`, ein eigener Ordner `/var/lib/clip-benutzer/<name>` (Datenbank,
    Puffer, Regie, Musik, Cache), Konfig und `.env` gehören root, genau ein eigener Lern-Bot; gestartet nur aus
    systemd-Vorlagen mit `CLIP_INSTANZ` und einer Sandbox, die alles von dir ausblendet. Gleicher Code, gleiche Regeln.
  - Bei dir ändert sich nichts (Konfig, Umgebung, Sperrpfad, Dienste, n8n-Vertrag). Keine Benutzer-Spalte in deiner
    Datenbank – verworfen (336 SQL-Anweisungen); getrennt wird durch eigene Dateien, Kernel und Sandbox.
  - Geteilt wird nur deine Rechen-Sperre (`[sperre].datei`, leer = wie bisher `<datenbank>.lock`): Freunde dürfen sie
    nur lesen, flock wirkt trotzdem; fehlt sie und lässt sich nicht anlegen, klarer Fehler statt Ersatzsperre.
    Mitbehoben: `/paket` im Clip-Bot rendert jetzt auch unter der Sperre. Jeder gesperrte Schritt loggt „Sperre
    gewartet x s, gehalten y s“ (Messgrundlage vor und nach dem ersten Freund).
  - Deine Antworten: **Weg** = Briefkasten auf dem vServer (kleiner Upload-Dienst, nicht n8n, keine Videos durch n8n),
    der Mini holt über Tailscale ab und rechnet – kommt mit Stufe 2, Samba-Freigaben je Freund entfallen. **Speicher**
    „wie bei dir, mit Lager“ – eigener Lager-Unterordner je Freund auf pve-big, Freigabe im Puffer nach 14 Tagen bei
    bestätigter Kopie; ab Stufe 2, vorher wird bei Freunden nichts gelöscht. **KI** = eigener Claude-Zugang je Freund;
    ohne ihn bleibt sie bei ihm aus, deine Anmeldung ist nie Rückfall.
  - Kein Auto-Update, keine automatische Installation: eingeschaltet wird nur über `benutzer-anlegen.sh`, das du selbst
    startest.
  - Schritt 2 (Instanz-Modus): `CLIP_INSTANZ=I` lädt nur die eigenen Werte (`konfig.lade_instanz`) – Repo-Konfig plus
    `I/instanz.toml` (wenige erlaubte Abschnitte), Zugänge nur aus `I/.env`, alle Pfade unter I, kein Lager, nichts
    gelöscht; was nicht passt, endet mit Exit 2. KI nur mit eigenem Claude-Zugang des Freundes (Token aus `claude
    setup-token` in `I/db/claude-token` oder `I/.env`, eigene kleine Umgebung); ohne startet claude bei ihm nie. Bei
    dir unverändert. M24–M32.
  - Schritt 3 (Freund-Pipeline ohne n8n): `scan --verarbeiten --max 1 --versuche 3` per Timer – je Lauf nur das
    älteste offene Match (die Sperre ist nur kurz belegt); nach drei Fehlschlägen Status `fehler`, eine Zeile an seinen
    Bot, die neueren kommen dran, nichts gelöscht (`pipeline process <ID>` holt es nach). Ohne die Schalter wie bisher.
    Abend-Video: Geht Whisper nicht, misst es die Szenen ohne Sprache, und das Video kommt trotzdem (hilft auch dir –
    vorher brach jeder Lauf ab); auf ein aufgegebenes Match wartet der Abend nicht. M33–M37.
  - Schritt 4 (Trennung geprüft, nur Tests): Generalprobe mit dir, max und eva im selben Squad-Match – jeder in
    einem eigenen Prozess mit echtem ffmpeg. Keiner ändert eine Datei der anderen (Fingerabdruck), jede Datenbank
    hat nur ihr Match, alle nehmen die eine Sperre. Dazu der erste Test für den n8n-Einstieg (`deploy/n8n-lauf.sh`,
    unverändert): nur die Vertragsbefehle kommen durch. M38–M40.
  - Prüfung von Schritt 1–4: zwei kleine Lücken geschlossen, die vor Ort schon die Sandbox abfängt. Ein Tippfehler im
    Sperrpfad eines Freundes legte still eine eigene Sperre an (dann rechnete er neben dir her) – jetzt muss es deine
    Sperrdatei geben, sonst startet bei ihm nichts. Ein leeres `CLIP_INSTANZ` lief still mit deinen Werten – jetzt ein
    Fehler. Bei dir unverändert. M41–M42.
  - Schritt 5 (Dienste je Freund): Vorlagen in `deploy/benutzer/` – sein Lern-Bot, alle 5 min ein Match, alle 10 min
    das Abend-Video. Jede mit derselben Sandbox: eigener Benutzer, nur sein Ordner schreibbar, von dir nur die
    Sperrdatei (lesen) und das Startwissen; deine Datenbank, claude, Schlüssel, Puffer, Lager und andere Freunde gibt
    es dort nicht. Ein hängender Lauf gibt die Sperre nach 2 h frei. Das Update tut nur etwas, wenn es Freunde gibt:
    ihre Datenbanken vorher sichern (als sie selbst, kein Link), Vorlagen hinlegen – nie einschalten –, laufende
    Freundes-Bots neu. Eingeschaltet wird nur über `benutzer-anlegen.sh` (nächster Schritt). M43–M49.
  - Schritt 6 (Speicher für Freunde): `deploy/pve-mini/freunde-volume.sh` legt einmal ein Volume für alle Freunde an
    (Standard 100 GB, im CT `/var/lib/clip-benutzer`, nicht auf der CT-Platte, nicht im Puffer) – CT ca. 1 min aus.
    Die Pool-Grenze rechnet deinen Puffer voll mit und nennt sonst die Größe, die passt. Das Rückweg-Skript hängt nur
    aus; ein neuer Lauf hängt dasselbe Volume wieder ein. Samba je Freund entfällt (Briefkasten ab Stufe 2). M50–M54.
  - Schritt 7 (Freund anlegen): `deploy/benutzer/benutzer-anlegen.sh <name>` (erst `--probe`) – ein Befehl, wiederholbar,
    löscht nie. Zugänge verdeckt, nur in seiner `.env`; ein Bot-Token, den du schon nutzt, wird abgelehnt. Seine Dienste
    gehen erst an, wenn die Prüfung in seiner Sandbox grün ist. Danach `benutzer-pruefen.sh` (alles getrennt, Bot-Link),
    `benutzer-stilllegen.sh` (aus, Daten bleiben), `benutzer-befehl.sh` (ein Befehl in seiner Sandbox). Deine eigenen
    Rechte schärft es nur auf dein „j“ (nur chmod, Rückweg-Skript). Für Freunde: `docs/FREUNDE.md`. M55–M68.
  - Schritt 8 (Einladungslink): Statt seine Telegram-Zahl zu suchen, tippt der Freund einen Link an und drückt Start.
    `benutzer-anlegen.sh` zeigt dir den Link (gilt 15 min, nur einmal), wartet und trägt seine Zahl selbst ein – erst
    dann geht sein Bot an. Der Code steht nie im Log; je Bot bleibt es bei einem Empfänger. Mitbehoben: Stilllegen hält
    jetzt auch Schritte an, die gerade laufen. M69–M74.
  - Schritt 9 (eigener Claude-Zugang): Der Freund tippt in seinem Bot `/claude`, bekommt einen Anmelde-Link und schickt
    den Code zurück – der Bot löscht die Nachricht und legt das Token nur in seinen Ordner. Ein Token vom eigenen PC
    geht auch direkt. Nach 10 min oder bei einem Fehler wird nichts gespeichert; Code und Token stehen nie im Log. Bei
    dir gibt es `/claude` nicht. Einmal nötig: claude global auf dem Mini. M75–M83.
  - Prüfung von Schritt 5–9: Das freiwillige Schärfen deiner Rechte fasst nur noch an, was pipeline gehört. Vorher wurde
    eine `lokal.toml`, die root gehört (mit nano als root angelegt), nur noch für root lesbar – alle deine Dienste wären
    beim Start abgestürzt. Jetzt bleibt sie, wie sie ist, und das Skript sagt es. M84.
- 2026-10-09 (Stufe 2 Mehrbenutzer, „Freunde liefern selbst“ – Plan aus zwei Entwürfen, sechs Schritte; Löschen im
  Briefkasten erst nach deinem Ja): Freunde laden ihre Aufnahmen in einen Briefkasten auf dem vServer, der Mini holt sie
  über Tailscale ab, ihr Lager fährt bei deinem täglichen Abgleich mit. Bei dir ändert sich nichts. Annahmen ab M85:
  `docs/ENTSCHEIDUNGEN.md`, „Mehrbenutzer“.
  - Schritt 1 (Briefkasten auf dem vServer, nur Skripte): eigener SFTP-Dienst `briefkasten-sshd` auf Port 2222 neben
    deinem normalen SSH; je Freund ein Fach fester Größe (Standard 20 GB, mindestens 8; das System behält 15 % und
    10 GB frei). Der PC des Freundes darf nur hochladen, der Mini nur lesen und nur über das Tailnet; Freunde sehen
    einander nicht; dort wird nichts gelöscht. `deploy/vserver/briefkasten-{einrichten,freund,pruefen}.sh`, je mit
    `--probe`, j/N und Rückweg; Anleitung mit Abnahme von Hand: `docs/BRIEFKASTEN.md`. Ausnahme zu „keine Videos auf
    den vServer“: M106. M85–M88.
  - Schritt 2 (Abholen am Mini, noch nicht eingeschaltet): `pipeline briefkasten abholen|status` nur in der Instanz
    eines Freundes (bei dir Exit 2, nichts angefasst). Holt nur Dateien mit Lieferschein (Größe, Prüfsumme, Zeit vom
    PC), prüft sie nach dem Zurücklesen und legt sie unter ihrem Namen in seinen Puffer – nie überschrieben, fremde
    Namen nie angefasst; Videos vor Replays, die Abend-Datei erst, wenn seine Matches fertig sind. Keine Rechen-Sperre,
    weckt nie, löscht im Briefkasten nichts. Mit Briefkasten erkennt sein Mini den Abend nicht selbst
    (`[sitzungen].auto_abend`, bei dir weiter an). M89–M93, M96, M103.
  - Schritt 3 (Abholen einschalten): `benutzer-anlegen.sh <name>` hat den Schritt „Briefkasten“ (j/N) – einmal die
    Adressen des vServers, zwei eigene Schlüssel für ihn (der private nie auf dem Bildschirm), Hostschlüssel über das
    Tailnet, eine Zeile für den vServer, dann eine Probe-Abholung in seiner Sandbox; erst wenn die grün ist, holt
    `clip-freund-abholen@` alle 2 min ab. Rot: nur das Abholen bleibt aus, ein neuer Lauf setzt fort. Prüfen und
    Stilllegen kennen das Abholen; das Update legt die Vorlage nur hin. M95, M104, M108–M116.
  - Schritt 4 (PC-Programm und /pc): Der Freund tippt in seinem Bot `/pc`, entpackt die Datei und doppelklickt
    `Freund-Einrichten.cmd` – kein Admin, keine Installation. Danach lädt sein PC alle 2 min fertige Fortnite-Aufnahmen
    und Replays hoch (beim Spielen langsam, 2 Mbit/s): je Datei erst halb, dann umbenannt, dann der Lieferschein; ein
    Replay erst nach den Aufnahmen seines Matches; 45 min nach dem letzten Match die Abend-Datei. Er löscht nie etwas.
    Sein Bot meldet „PC verbunden“, eine falsche Zeitzone und Aufnahmen ohne Replay, 📋 zeigt, wann der PC sich zuletzt
    meldete. Bei dir gibt es `/pc` nicht, `Uebertragung.ps1` bleibt, wie es ist. Vor Ort einmal unter echter
    PowerShell 5.1 mit `-Probe` prüfen (docs/BRIEFKASTEN.md). M94, M97, M117–M124.
  - Schritt 5 (Lager für Freunde): Hat dein täglicher Abgleich pve-big ohnehin geweckt, fahren Freunde mit Lager mit –
    `clip-lager-freunde.service` hängt sich an `clip-lager.service`, hält pve-big mit der Marke „freunde“ wach und
    sichert je Freund nacheinander (neue Starts 10–18 Uhr, höchstens 2 h) seinen Puffer nach `freunde/<name>` in deinem
    Lager; danach gibt sein Puffer Rohvideos nach 14 Tagen frei wie bei dir. Der Rundgang weckt nie, im Lager wird
    nichts gelöscht. Einschalten je Freund: `benutzer-anlegen.sh`, Schritt „Lager“ (nur bei wachem pve-big, mit Probe
    der Bindung; rot = Schalter wieder aus). Dein Lager-Code bleibt Zeichen für Zeichen gleich. M98–M101, M107,
    M125–M130.
  - Schritt 6 (Morgenprüfung kennt die Freunde): Gibt es das Freunde-Volume, meldet deine Morgenprüfung im Thema
    „freunde“, wenn dort weniger als 15 GB frei sind (Alarm unter 5 GB, nächster Schritt `pct resize`), und je Freund mit
    Lager eine Zeile, wenn sein letzter Lager-Lauf nicht ging oder er seit 8 Tagen nicht ins Lager kam (nächster Schritt
    `benutzer-pruefen.sh <name>`). Hineingeschaut wird nie, nur der freie Platz gemessen. Ohne Freunde-Volume ist die
    Morgenprüfung Zeichen für Zeichen wie vorher. M105, M131–M134.
  - Prüfung: Auf einem vServer mit älterem SSH (Ubuntu 20.04, Debian 11) wäre kein Upload eines Freundes fertig
    geworden – das Umbenennen nach dem Hochladen wurde abgewiesen, obwohl alle Proben grün waren. Das PC-Programm
    benennt jetzt auf die alte Art um, die jede Version kennt und die nie überschreibt; mit echtem SSH 8.2 und 9.6
    nachgestellt. Die Mindestversion auf dem vServer bleibt 8. M135.
    Dazu drei Lücken aus derselben Prüfung geschlossen: Änderte sich eine Aufnahme beim Hochladen, kam sie nie an
    (jetzt ersetzt der PC sie oben durch die aktuelle Fassung, dann der Lieferschein); riss die Verbindung zwischen
    Abend-Datei und Lieferschein ab, kam für den Abend nie ein Video (jetzt wird die Datei neu geschrieben); ein
    absichtlich verschachtelter Lieferschein blockierte das Abholen dieses Freundes für immer (jetzt ungültig, nach 3
    Versuchen aufgegeben). M136–M138.
- 2026-10-09 (Stufe 3 Mehrbenutzer, „Hybrider Render-Manager“ – Plan aus zwei Entwürfen, vier Schritte): Der Mini
  bleibt der einzige Rechner für dich und die Freunde – Stufe 3 misst zuerst, sichert gegen Ausfälle ab und gibt dir an
  der Sperre Vorrang; ein zweiter Rechner oder die Cloud kommt erst nach einem Messbefund (Annahmen ab M139:
  `docs/ENTSCHEIDUNGEN.md`, „Mehrbenutzer“).
  - Schritt 1 (Laufzeiten messen): Jeder Rechenauftrag unter der Sperre schreibt danach eine Zeile in die eigene
    Datenbank – wie lange er gewartet und gerechnet hat und ob er geklappt hat (beim Lern-Bot-Bau auch Stimmung, Schnitt
    und Rendern einzeln); jedes gerenderte Video merkt sich Rechenzeit, ob die Grafikeinheit auf den Prozessor
    zurückfiel und wie groß das Material war. `pipeline laufzeiten [--tage 7]` (Freund: `benutzer-befehl.sh <name>
    laufzeiten`) fasst das zusammen: typische und längste Zeiten je Auftrag, Rendern je Grafikeinheit/Prozessor (ältere
    Videos aus Dateizeiten), ✅ → Upload, Abend → Video und wie oft du ✅ tippst – nur lesen, weckt nie. Am Auftrag
    ändert sich nichts: n8n-Vertrag, Exit-Codes, JSON-Zeile und offene Transaktionen bleiben, ein Schreibfehler steht
    nur im Log. M139–M142.
  - Schritt 2 (Ausfallsicher): Stirbt ein Schritt, endet sein ffmpeg mit – kein Video rechnet mehr heimlich ohne
    Sperre weiter. Videos, Clips, Vorschauen und die Marke des 2-Wochen-Videos kommen erst ganz auf die Platte, dann
    unter ihren Namen (nach einem Stromausfall nie eine leere Datei unter dem Endnamen). Streikt die Grafikeinheit beim
    Schneiden der Clips, schneidet der Prozessor nach, statt das Match zu verlieren. `clip-sitzungen` bricht nach 2 h
    ab (das Abend-Video holt der nächste Lauf auf dem Prozessor nach), die Timer der Freunde nach 1 h statt 2 h – das
    Update übernimmt das nur in Dateien, die du nicht selbst angepasst hast. Abnahme-Tests: Absturz mitten im
    Abend-Video, verwaistes ffmpeg, abgerissene n8n-Verbindung. Ergebnisse und n8n-Vertrag bleiben gleich. M143–M146.
  - Schritt 3 (Florian zuerst): Warten du und Freunde gleichzeitig auf die Rechen-Sperre, kommst du meist zuerst dran.
    Freunde fragen nur noch alle 4–6 s statt jede Sekunde und warten vor dem ersten Versuch zufällig bis zu 1 s;
    erkannt wird ein Freund daran, dass er die Sperrdatei nur lesen darf (nicht fälschbar). Bei einer Übergabe ist ein
    Freund in 10 statt 50 % der Fälle vor dir dran (drei Freunde: 27 statt 75 %), die 1-s-Lücke zwischen zwei
    n8n-Schritten erwischt ein wartender Freund in 20 statt 100 %. Deine Schritte fragen wie bisher sofort und dann
    jede Sekunde; ein laufender Auftrag wird nie unterbrochen. Harter Vortritt erst, wenn `laufzeiten` es zeigt. M147.
  - Schritt 4 (Vertrag für weitere Rechner, nur Doku) und Abschluss: `docs/WORKER.md` beantwortet die sechs Fragen
    aus Abschnitt D für heute – der Mini bleibt der einzige Rechner, weil das Hin- und Herschicken eines Shorts
    (15–53 s) etwa so lange dauert wie das Rendern (58–86 s). Dort stehen auch die Auslöser für einen zweiten Rechner
    (Abend-Video an 3 Abenden einer Woche über 30 min, oder deine Wartezeit im p90 über 10 min) und der Vertrag v1 für
    Heimserver und Cloud – gebaut wird er erst mit dem ersten Worker und deinem Ja, die Cloud bleibt aus.
    `render-entwurf --final` bleibt als Vorläufer v0 aus (`docs/REGIE.md`). Das Update sagte „schlägt er fehl,
    wiederholt n8n ihn“ – falsch: n8n meldet den Fehler, nachholen mit `pipeline process <ID>`. Stufenbericht in
    `docs/MEHRBENUTZER.md`: Schneller wird nichts. Neu ist: Abstürze und Stromausfall hinterlassen keine kaputten
    Dateien, eine Panne der Grafikeinheit beim Schneiden kostet kein Match mehr, du kommst an der Sperre meist zuerst
    dran, und Laufzeiten sind sichtbar. Echte Mini-Zahlen fehlen noch (`pipeline laufzeiten` vor Ort). M148–M152.
  - Prüfung (ein Prüfer, zwei kleine Befunde, nichts Blockierendes): Unter Windows (nur zum Entwickeln) endete jedes
    Rendern beim neuen Auf-die-Platte-Schreiben – behoben, auf dem Mini ändert sich nichts. `clip-sitzungen` bleibt
    bei 2 h: Wartet es die ganzen 2 h auf die Sperre, beendet systemd es ohne Zeile „gesperrt“ (nichts geht verloren).
    Länger hieße: Hängt das Abend-Video selbst, endete ein n8n-Schritt, der kurz danach kommt, mit Exit 4
    (nachgestellt); ein Test hält das fest. M153–M154.
- 2026-10-09 (Stufe 4 Mehrbenutzer, „Qualitäts- und Erfolgsmessung“ – Plan aus zwei Entwürfen; Abnahme: „belegbare
  Unterschiede zwischen Strategien, ohne fehlende Daten zu erfinden“): **Erfolg ehrlich messen.** Der Zähler auf
  clip-battle.de und der Video-Code in der Caption kommen erst nach deinem Ja (offene Fragen); bis dahin bleiben
  clip-battle.de, Captions, Bots, Lernen und n8n, wie sie sind. Annahmen ab M155: `docs/ENTSCHEIDUNGEN.md`, „Mehrbenutzer“.
  - Schritt 1 (`pipeline erfolg`, nur nachsehen, `docs/PUBLIKUM.md` Abschnitt 7): drei Ziele getrennt – Zuschauer (die
    feste Wochen-Note), neue Follower, Besuche auf clip-battle.de; was fehlt, heißt „nicht gemessen“ mit Grund, nie 0
    (auf TikTok heute: wie lange geschaut wird, Follower und clip-battle.de). Jedes hochgeladene Short zählt je
    Plattform einmal (Fassungen einmal, die ersten 5 sind nur Vergleich). Verglichen werden 9 feste Strategien (4
    Aufbauten, Tempo, Zeitlupe, 3 Längen); „belegt“ erst ab 8 Videos je Seite und nur, wenn es sehr wahrscheinlich kein
    Zufall ist. KI-Note und ✅/❌ zählen dafür nie. Gewichte `[erfolg.gewichte]` 0,5/0,2/0,3, nur in der Konfig. Freund:
    `benutzer-befehl.sh <name> erfolg`. Gemessen: ohne echten Unterschied behauptete der Wochenbericht schon nach 8
    Videos einen Verlierer (Versuch E), `erfolg` nie; bei wöchentlichem Nachsehen ein falsches „belegt“ in 3,6 % der
    Halbjahre (6,3 % im Jahr); doppelte Reaktionen in 100 % binnen eines Jahres belegt, im Median nach 16 Wochen
    (bei 3 Shorts je Woche). 300 Posts in 15 ms. Die Zeile für den Sonntagsbericht ist fertig, eingehängt wird sie mit
    Schritt 2. M155–M161, M163–M170.
  - Schritt 2 (ehrliche Sätze, `docs/SO-GEHTS.md` „Der Sonntagsbericht“): Statt „👀 Bei den Zuschauern kommt gut an“
    steht dort die 📊-Zeile aus `pipeline erfolg` – wie viele Videos noch fehlen, „noch kein Unterschied sicher“ oder
    „📊 Belegt (TikTok, 41 Videos): … – sehr wahrscheinlich kein Zufall“, darunter „Noch nicht gemessen: …“; ohne
    fertige Wochenzahlen keine Zeile. Statt „👍 Kommt gut an / 👎 Kommt weniger an“ heißt dieselbe Zahl „🎯 Wähle ich
    gerade öfter: … · seltener: …“ – was der Bot bevorzugt, kein Beweis. Ein Fehler in `erfolg` kostet den Bericht nie.
    Unter /experte: /lernstand „Tendenzen (nicht belegt)“, /publikum „Wochen-Note (fest)“ und „Lernwert (vorläufig)“.
    Lernen, 📋 Stand, Videos, Knöpfe und n8n unverändert. Nachgestellt (echter Bericht, 200 Halbjahre ohne
    echten Unterschied, 3 Shorts je Woche): vorher irgendwann eine Behauptung in 99,5 % der Halbjahre, nachher in
    5,0 %. Stufenbericht: `docs/MEHRBENUTZER.md`. M162, M171–M174.
  - Prüfung (ein Prüfer, drei kleine Befunde behoben): Bei einem Freund holt niemand Zuschauerzahlen ab –
    `benutzer-befehl.sh <name> erfolg` sagte das oben, darunter aber „warten noch auf ihre Wochenzahlen“ und „nicht
    gefunden?“. Jetzt steht dort durchgehend „nicht gemessen (Zuschauerzahlen werden bei dir noch nicht abgeholt)“ bzw.
    „noch ohne Wochenzahlen“. Bei dir und im Sonntagsbericht ändert sich nichts. M175. Dazu: „🎯 Wähle ich gerade öfter“
    nennt je Schraube nur die Wahl, die echt vorn liegt (vorher bei lauter ✅ „viel Zeitlupe“ und „wenig Zeitlupe“
    beide), und ein Test sichert, dass ohne echten Unterschied nie „belegt“ erscheint. M176–M177.
- 2026-10-09 (Stufe 5 Mehrbenutzer, „Regie-Liga“ – Plan aus zwei Entwürfen, drei Schritte; Abnahme: „Benutzer können
  nachvollziehen, was das System ausprobiert und tatsächlich gelernt hat“): **Bester Aufbau nur mit Beleg.** Erfahrung
  zählt nur Videos mit fertigen Zuschauerzahlen, nie Zeit, Tippen, ✅/❌ oder KI-Note; bis zur ersten Krönung wählt der
  Bot genau wie heute. Annahmen ab M178: `docs/ENTSCHEIDUNGEN.md`, „Mehrbenutzer“.
  - Schritt 1 (Liga rechnen, nur nachsehen, `docs/PUBLIKUM.md` Abschnitt 8): `pipeline erfolg` hat jetzt den Abschnitt
    „Regie-Liga“ (JSON `liga`) – kein neuer Befehl, keine Tabelle, nichts gespeichert. Sonntags um 18 Uhr wird
    entschieden: Ein Aufbau wird bester Aufbau, wenn er an zwei Sonntagen nacheinander belegt besser ankommt als die
    anderen; ablösen kann ihn nur einer, der ihn direkt schlägt, und nur mit Videos ab der Krönung. Dazu Erfahrung und
    Level je Aufbau, Tempo, Zeitlupe und Länge, Liga-Level (sammelt · vergleicht · bester Aufbau belegt), Vertrauen in
    Worten, das nächste Ziel und was in 7 Tagen ausprobiert wurde. Nachgestellt mit dem echten Code: ohne echten
    Unterschied eine falsche Krönung in 1,2 % der Jahre, doppelte Reaktionen im Median nach 17 Wochen gekrönt; 2 Jahre
    Geschichte in 0,15 s. Der erfolg-Teil bleibt Zeichen für Zeichen gleich; Bericht, 📋, Videos und n8n unverändert.
    M178–M186.
  - Schritt 2 (Liga im Lern-Bot, `docs/SO-GEHTS.md` „Der Sonntagsbericht“): Statt der 📊-Zeile stehen sonntags 🥇 bester
    Aufbau (in der Woche der Krönung auch ohne Video), 🧪 was ausprobiert wurde – mit Namen –, 🏅 Level und Erfahrung
    und 🔜 das nächste Ziel; gibt es einen besten Aufbau, nennt 🎯 keinen Aufbau mehr. 📋 hat eine Liga-Zeile, sobald
    Zahlen ankommen; deine ✅/❌-Zahl steht in der 🧠-Zeile („Zuschauern (läuft)“). Freunde ohne Zahlenabruf: keine
    Liga-Zeilen und keine wöchentliche 🧠-Zeile mehr. /lernstand (nur /experte): die Liga oben, das Publikums-Modell ohne
    Version und Prozent. Aufbau-Versuche bleiben sichtbar, auch wenn das Publikums-Modell Feinwerte nachsteuert –
    nachgestellt: Wahl und Lernen in 60 Entwürfen gleich, sichtbare Versuche 3 → 14. Videos, Knöpfe und n8n unverändert.
    M187–M192.
  - Schritt 3 (bester Aufbau wird Standard, `docs/REGIE.md`; Abnahme und Stufenbericht in `docs/MEHRBENUTZER.md`): Hat
    die Liga einen Aufbau gekrönt, nimmt ihn der Bot im einfachen Modus beim Short statt des Zufallszugs – „mutig“, „nie
    dreimal“, 🥱 und deine Regeln gehen vor, also etwa 6 von 10 Shorts; der 🥇-Satz der Krönungswoche sagt es. Bis zur
    ersten Krönung, mit `[geschmack] champion_standard = false` (nur `lokal.toml`) und bei einem Fehler der Liga wählt er
    Zeichen für Zeichen wie vorher (Test: je 40 Wahlen gleich wie main). Nachgestellt mit dem echten Code: ohne echten
    Unterschied eine falsche Krönung in 1,1 % der Jahre (2000 Läufe), zwei gleich gute Aufbauten wechseln danach in 0,4 %
    von 2 Jahren, doppelte Reaktionen im Median nach 17 Wochen gekrönt. Im echten Weg (DB-Weg, 100 und 40 Jahre): bis zur
    Krönung Zeichen für Zeichen dieselben Videos wie vorher, danach „erzählt“ in 61 statt 55 % der Shorts und 3,6 % mehr
    Reaktionen je Aufruf; ohne echten Unterschied 2 von 100 Jahren falsch gekrönt. Gegenprobe: alle 7 Fehler aus dem Plan
    und 8 weitere machen einen Test rot. Nebenbei behoben: ein älterer Test flackerte (Millisekunden-Grenze). M193–M195.
  - Prüfung (ein Prüfer; ein blockierender und drei kleine Befunde, alle behoben): Nach einer Krönung stand unter 🔜
    „noch kein Unterschied sicher“, obwohl der beste Aufbau sicher besser ankam – jetzt „„erzählt“ bleibt vorn – „Kino“
    kommt bisher nicht an ihn heran (…)“, sonst der Vergleich, der wirklich noch offen ist (nachgestellt: vorher in 803
    von 1430 Sonntagsberichten nach der Krönung falsch, jetzt in keinem). Ein Herausforderer bestätigt sich nur noch
    mit neuen Videos in genau seinem Vergleich (vorher reichte die neue Note eines dritten Aufbaus): Bei einem echten
    Wechsel kommen 7 von 234 Ablösungen eine Woche später, zwei gleich gute wechseln in 0,3 statt 0,4 %, sonst gleich.
    🥇 sagt „zweimal nacheinander“ statt „zwei Sonntage nacheinander“; 📋 hat beim Freund mit PC-Programm bis 7 Zeilen
    (nur Doku). M196–M197.
