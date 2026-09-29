# Clip-Pipeline

Fortnite-Aufnahmen → automatisch gefundene Highlights → Shorts und Zusammenschnitte mit Werbung für
**clip-battle.de** → veröffentlichen → echte Publikumszahlen → automatisch bessere nächste Entwürfe.
Manuelle Bewertungen und Battles bleiben freiwilliges Zusatzwissen; neue Bewertungen sind fürs Lernen nicht nötig.

## Autonomes Lernen

Im Lern-Bot einen **Short (30–75 s, zunächst bevorzugt 45–75 s)** oder **Zusammenschnitt (75–120 s)** erstellen,
direkt **📦 Upload-Paket** öffnen und veröffentlichen. Danach `/link <entwurf> <url>` schicken. Ein 👍 ist weder
für das Paket noch den Post nötig. `/lernstand` und `/stand` zeigen veröffentlichte und ausgewertete Videos,
Lernversion, Vertrauen und aktuelle Tendenzen.

Neue Messungen aktualisieren das Modell automatisch. Mit eingerichtetem Zugang holt der tägliche vorhandene
`clip-publikum`-Dienst TikTok- und YouTube-Zahlen ab. TikTok Display liefert nur einen Teil der Kennzahlen;
unbekannte Watchtime bleibt unbekannt. Instagram ist zunächst über den normalisierten Messungsimport angebunden.
Screenshots und Eingaben bleiben eine Ergänzung. Ohne Plattformzugang oder Import kommen keine Zahlen von selbst.
Einrichtung, Grenzen und Datenmodell: [Autonomes Lernen](docs/AUTONOMES_LERNEN.md).

## So läuft ein Abend ab

1. Du spielst. **Nvidia Highlights** und **SteelSeries Moments** speichern Clips automatisch,
   Fortnite schreibt ein **Replay**.
2. Alle 2 Minuten kopiert `windows/Uebertragung.ps1` neue Dateien per SMB in den **Puffer** auf dem Mini (weckt
   nie) und meldet jedes fertige Match an n8n: `POST /webhook/match-vorbei {"session": "<ID>"}`. Ins **Lager** auf
   pve-big kommt alles einmal am Tag um 10:00 (`pipeline lager abgleich`, nie nachts – `docs/PUFFER.md`).
3. n8n ruft per SSH nacheinander auf (Vertrag in `CLAUDE.md`):
   - `prepare` – Replay finden, Aufnahmen erfassen, Session-Ordner `sessions/<ID>/`
   - `analyze` – Replay lesen → **deine** Kills (nicht die deines Teams) → Multikill-Gruppen (Kette ≤ 10 s)
     → Clip-Fenster (8 s vorne, 5 s hinten) → beste Aufnahme → `analyse.json`
   - `decide` – `claude -p` justiert Schnitt und Beschreibung; Prüfung gegen Schema und Kill-Fakten,
     sonst Regel-Vorschlag → `schnittliste.json`
   - `render` – framegenau schneiden (feste Bildrate für CapCut), Lautstärke messen, Punkte, Vorschau < 50 MB
4. Der Clip-Bot bietet weiter **✅ Freigeben / 🗑️ Verwerfen** und Battles für freiwilliges Feedback an.
   Der Regisseur erstellt Entwürfe auch ohne neue Einzelbewertungen.
5. **Einzelne Momente werden nicht hochgeladen** (26.09.) – Freigaben dienen Bewertung, Lernen und dem
   Highlight-Video. Hochgeladen werden die Shorts aus dem Lern-Bot und das Highlight-Video (Erinnerung täglich,
   bis du am Video „✅ Hochgeladen“ tippst). Für Ausnahmen gibt es noch `/paket <nr>` und `/link <nr> <url>`.
6. Das Publikum liefert das Hauptsignal: Performance speichern → automatisch lernen → nächsten Entwurf planen.
   Historische Freigaben und Battles bleiben erhalten und verlieren mit mehr Publikumsdaten relativ an Gewicht.
7. Alle 14 Tage: `highlight` verwendet den gemeinsamen Regisseur für einen Zusammenschnitt (75–120 s),
   einschließlich autonomer Parameter und verknüpftem Entwurf. Der Lern-Bot bietet dafür direkt das Upload-Paket;
   im Clip-Bot bleibt die bisherige Vorschau. Im Puffer-Betrieb wird pve-big dafür nicht geweckt.
8. Gelöscht wird nichts automatisch (Entscheidung 25.09.): Wird Platz knapp, warnt der Bot einmal am Tag.

## Regisseur (Sprint 09/2026)

Automatische Zusammenschnitte (16:9) und Shorts (9:16) mit Musik, Schnitten auf dem Beat und Übergängen je
Stimmung; Veröffentlichung und optionales Feedback im eigenen **Lern-Bot**. Der Regisseur lernt aus der
Publikumsperformance. Dazu ein Sicherheitsnetz, das pve-big
herunterfährt, wenn nichts zu tun ist. Bedienung: `docs/REGIE.md` · Entscheidungen: `docs/ENTSCHEIDUNGEN.md` ·
Stand und Host-Änderungen: `docs/ABSCHLUSSBERICHT.md`.

## Bestehende Lernschleife „Publikum"

Jeder verknüpfte Entwurf wird ein **Post** mit eingefrorenen Merkmalen. Der neue Audience-Success-Score und der
versionierte Lerner ergänzen die vorhandenen Tabellen. Der frühere Wochen-Score bleibt für ältere Auswertungen
erhalten; er ist keine Wartebedingung für das neue Lernen. `/publikum` zeigt die Post-Details.
Weckt nie pve-big. Frühere Bedienung und Installation: `docs/PUBLIKUM.md`; aktueller Ablauf:
[Autonomes Lernen](docs/AUTONOMES_LERNEN.md).

**Stufe 2 – Merkmale und eine Bewertung:** 17 statt 5 Merkmale (aus dem Replay: Platzierung, Sniper/Nahkampf,
Bot-Opfer, Match-Phase, Endgame, Clutch; aus Mikro und Spielton: Lachen, Jubel, Frust, laute Spitzen), eine
Bewertung für Clip-Bot und Regisseur, Lernen auch aus Publikums-Paaren, Erwartung in beiden Bots. Die Mic-Werte
misst ein Hintergrundschritt nach `render` (n8n wartet nicht). Installation: `docs/PUBLIKUM.md`, „Stufe 2“.

## Befehle

| Befehl | Was |
|---|---|
| `bin/pipeline prepare\|analyze\|decide\|render --session <ID>` | die vier Schritte (Vertrag mit n8n) |
| `bin/pipeline highlight --id <ID> --tage 14` | Highlight-Video → `{"clips": n, "dauer": "mm:ss"}` |
| `pipeline process <ID>` · `pipeline scan [--verarbeiten]` | alle Schritte von Hand · offene Matches finden |
| `pipeline replay <datei>` | deine Kills eines Replays in Ortszeit (Kalibrierung) |
| `pipeline short <clip>` · `pipeline caption <clip>` | Short rendern · Caption erzeugen |
| `pipeline gewichte [--neu]` · `pipeline status` | Lernstand · Überblick |
| `pipeline aufraeumen [--liste] [--ausfuehren]` | Probelauf bzw. wirklich aufräumen |
| `pipeline momente nachschneiden [--tage 14] [--probe]` | Multikill-Momente ab dem ersten Umhauen neu schneiden (nur Puffer, neue Dateien, weckt nie; erst `--probe`) |
| `pipeline bot` | Telegram-Bot (läuft als Dienst) |
| `pipeline merkmale nachtragen [--session ID]` | Replay- und Mic-Merkmale für vorhandene Clips nachrechnen (nur Puffer, ohne Whisper, weckt nie) |
| `pipeline stimmung --clips [--session ID] [--max n]` | Mic-Schritt von Hand (Whisper, ohne Claude, nur Puffer) |
| `pipeline publikum bewerten` | verfügbare Plattformdaten holen, Scores und autonomes Modell aktualisieren (Timer, weckt nie) |

Telegram: `/battle` `/rangliste` `/gewichte` `/uploads` `/offen` `/status` `/hilfe`
Lern-Bot: `/entwurf` `/musik` `/lernstand` `/stand` `/publikum` `/link <entwurf> <url>` `/hilfe` · Screenshot mit `#<post>`

Ausgabe: Logs auf stderr, letzte Zeile auf stdout = eine JSON-Zeile.
Exit-Codes: 0 ok · 1 Fehler · 2 Aufruf/Konfig · 3 Speicher offline (großer Host schläft) · 4 Sperre nicht bekommen

## Auf dem Windows-PC ausprobieren

```powershell
py -3.14 -m venv .venv
.venv\Scripts\pip install -e .[windows]
dotnet build tools\replay2json -c Release -o tools\replay2json\bin\out
.venv\Scripts\pipeline replay "$env:LOCALAPPDATA\FortniteGame\Saved\Demos\<datei>.replay"
```
Mit einem Test-Speicher (Ordner mit leerer Datei `.clip-speicher`, darin `eingang\` und `replays\`):
```powershell
$env:CLIP_SPEICHER = "D:\clip-test"; $env:CLIP_DATENBANK = "D:\clip-test\test.db"
.venv\Scripts\pipeline process 2026-09-21_21-42-22
```

## Tests

```powershell
.venv\Scripts\python -m unittest discover -s tests -t .
```
Einige hundert Tests (die Zahl steht in der letzten Zeile des Laufs, „Ran … tests“), darunter echte FFmpeg-Läufe
(Schnitt, Tonspuren, feste Bildrate, Vorschau, Short, Highlight, Entwürfe), der Vertrag mit n8n (Session-ID-Prüfung,
JSON als letzte Zeile, Exit-Codes) und „nie wecken“. Der ganze Lauf dauert wegen des Renderns eine Viertelstunde und
mehr; einzelne Module gehen schneller: `python -m unittest tests.test_publikum`.

## Replay-Parser

`tools/replay2json` (C#, NuGet `FortniteReplayReader` 3.1.0) gibt ein Replay als JSON aus.
- Windows: `dotnet build tools\replay2json -c Release -o tools\replay2json\bin\out`
- Linux-Server (kein .NET nötig): `dotnet publish tools\replay2json -c Release -r linux-x64 --self-contained -p:PublishSingleFile=true -o tools\replay2json\bin\linux-x64`, Datei per `scp` kopieren
- Geprüft mit Fortnite-Build **42.20**. Nach Fortnite-Updates: `pipeline replay` an einem neuen Replay testen
  und die Kill-Zahl mit der Match-Zusammenfassung vergleichen.
- Streikt der Parser, springen die Rekorder-Daten ein (SteelSeries-Marker, Nvidia-Dateinamen);
  der Clip ist in Telegram mit „⚠️ ohne Replay-Daten“ markiert.

## Was gemessen wurde (Kalibrierung 23.09.2026, echtes Match vom 21.09.)

- Replay-Startzeit = Ortszeit; Kill-Zeitpunkt = Start + `t_ms`.
- `IsReplayOwner` ist in Build 42.20 nicht gesetzt → deine Epic-ID steht in `config/pipeline.toml`.
- Nvidia: Uhrzeit im Namen = Clip-Ende, echtes Ende 0,9–1,9 s später (Versatz 1,4 s); `creation_time`
  ist verlässlicher als der Name. „Am Boden“ = du hast jemanden umgehauen. Nvidia schreibt „Dreifacheliminerung“.
- SteelSeries: `recording_timestamp` = Clip-Ende, Abweichung < 0,5 s.
- Beide Rekorder zählen in Squads auch Team-Kills → das Replay ist die Wahrheit.
- Nachgemessen am Kill-Zähler im Bild: Die geschnittenen Clips liegen weniger als 1 s daneben.

## Aufbau

```
bin/pipeline             Einstieg laut n8n-Vertrag
config/pipeline.toml     alle Zahlen und Pfade
templates/               Caption-Vorlage und Beschreibungs-Bausteine
src/clip_pipeline/       verarbeitung (prepare/analyze/decide/render) · quellen (Rekorder) · replay
                         zeitleiste · vorbewertung · schnittliste · medien (FFmpeg) · shorts · highlight
                         caption · elo · lernen · aufraeumen · erfassung · schema + schemas/ · db + schema.sql
                         sperre (flock) · cli · bot/ (texte, aktionen, app)
                         Lernschleife: publikum (+ publikum.sql) · lernbot_zahlen · lernbot_paket · lernbot_publikum
                         · screenshot (Zahlen aus dem Bild lesen) · claude_aufruf (claude -p mit Schema, gezählt)
                         · merkmale (Replay-/Mic-Merkmale) · mikro (Mic-Schritt) · erwartung
tools/replay2json/       Replay -> JSON (C#)
windows/                 Übertragung Gaming-PC -> großer Host (+ Meldung an n8n)
deploy/                  systemd-Dienste, abgesicherter SSH-Einstieg für n8n
docs/SERVER.md           Server einrichten (Proxmox, NFS, Samba, Wake-on-LAN, Tailscale, n8n, Telegram)
docs/PUBLIKUM.md         Lernschleife „Publikum“: Bedienung, Konfig, Installation
```

## Häufige Fragen

**Medal.tv zusätzlich installieren?** Nein. Nvidia und SteelSeries erfassen die Momente schon; ein dritter
Rekorder kostet nur Leistung und erzeugt Duplikate. Die Pipeline nimmt pro Moment die Aufnahme mit der besten
Abdeckung.

**Wie viele KI-Agenten in n8n?** Keinen. n8n ist der Dirigent und stößt nur Skripte an. Die einzige
KI-Entscheidung (`decide`) läuft per `claude -p` über dein Max-Abo auf dem Mini – ohne API-Key.

**Muss ich weiter bewerten?** Nein. Direkt das Upload-Paket öffnen, veröffentlichen und den Link zuordnen.
Sobald Publikumszahlen eintreffen, lernt das System selbstständig. Die Entscheidung, was du veröffentlichst,
bleibt bei dir; das Erstellen eines Pakets lädt noch nichts auf eine Plattform hoch.
