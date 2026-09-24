# Clip-Pipeline

Fortnite-Aufnahmen → automatisch gefundene und vorbewertete Highlights → Bewertung per Telegram
(Freigeben/Verwerfen, Battles mit Elo) → Shorts mit Werbung für **clip-battle.de** → Upload auf
YouTube Shorts **und** TikTok → alle 2 Wochen ein Highlight-Video. Die Vorbewertung lernt aus deinen Entscheidungen.

## So läuft ein Abend ab

1. Du spielst. **Nvidia Highlights** und **SteelSeries Moments** speichern Clips automatisch,
   Fortnite schreibt ein **Replay**.
2. Alle 2 Minuten kopiert `windows/Uebertragung.ps1` neue Dateien auf den großen Proxmox-Host (weckt ihn per
   Wake-on-LAN) und meldet jedes fertige Match an n8n: `POST /webhook/match-vorbei {"session": "<ID>"}`.
3. n8n ruft per SSH nacheinander auf (Vertrag in `CLAUDE.md`):
   - `prepare` – Replay finden, Aufnahmen erfassen, Session-Ordner `sessions/<ID>/`
   - `analyze` – Replay lesen → **deine** Kills (nicht die deines Teams) → Multikill-Gruppen (Kette ≤ 10 s)
     → Clip-Fenster (8 s vorne, 5 s hinten) → beste Aufnahme → `analyse.json`
   - `decide` – `claude -p` justiert Schnitt und Beschreibung; Prüfung gegen Schema und Kill-Fakten,
     sonst Regel-Vorschlag → `schnittliste.json`
   - `render` – framegenau schneiden (feste Bildrate für CapCut), Lautstärke messen, Punkte, Vorschau < 50 MB
4. Der Telegram-Bot schickt dir jeden Clip: **✅ Freigeben / 🗑️ Verwerfen**.
5. **📦 Upload-Paket**: Short 1080×1920 (Overlay + Endcard „Stimm ab auf clip-battle.de“) als Datei +
   Caption zum Kopieren + Checkliste YouTube / TikTok / clip-battle.de. Erst wenn YouTube **und** TikTok
   abgehakt sind (Knopf oder `/link <nr> <url>`), gilt der Clip als veröffentlicht; offene Uploads meldet der
   Bot täglich.
6. `/battle`: zwei freigegebene Clips, du wählst den besseren → Elo. Freigaben und Battles justieren die
   Gewichte der Vorbewertung (`/gewichte`).
7. Alle 14 Tage: `highlight` baut ein Video aus den besten Clips (Überblendungen, lizenzierte Musik mit Ducking)
   und schickt eine Vorschau zur **Freigabe in den Bot**. Schläft der große Host, weckt ihn die Pipeline.
8. Nach einem halben Jahr wird recycelt – Multikills ab 3 Kills bleiben für immer im Archiv.

## Regisseur (Sprint 09/2026)

Automatische Zusammenschnitte (16:9) und Shorts (9:16) mit Musik, Schnitten auf dem Beat und Übergängen je
Stimmung; Bewertung im eigenen **Lern-Bot**, der Regisseur lernt daraus. Dazu ein Sicherheitsnetz, das pve-big
herunterfährt, wenn nichts zu tun ist. Bedienung: `docs/REGIE.md` · Entscheidungen: `docs/ENTSCHEIDUNGEN.md` ·
Stand und Host-Änderungen: `docs/ABSCHLUSSBERICHT.md`.

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
| `pipeline bot` | Telegram-Bot (läuft als Dienst) |

Telegram: `/battle` `/rangliste` `/gewichte` `/uploads` `/paket <nr>` `/link <nr> <url>` `/offen` `/status` `/hilfe`

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
55 schlanke Tests, darunter echte FFmpeg-Läufe (Schnitt, Tonspuren, feste Bildrate, Vorschau, Short,
Highlight) und der Vertrag mit n8n (Session-ID-Prüfung, JSON als letzte Zeile, Exit-Codes, Wake-on-LAN).

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
tools/replay2json/       Replay -> JSON (C#)
windows/                 Übertragung Gaming-PC -> großer Host (+ Meldung an n8n)
deploy/                  systemd-Dienste, abgesicherter SSH-Einstieg für n8n
docs/SERVER.md           Server einrichten (Proxmox, NFS, Samba, Wake-on-LAN, Tailscale, n8n, Telegram)
```

## Häufige Fragen

**Medal.tv zusätzlich installieren?** Nein. Nvidia und SteelSeries erfassen die Momente schon; ein dritter
Rekorder kostet nur Leistung und erzeugt Duplikate. Die Pipeline nimmt pro Moment die Aufnahme mit der besten
Abdeckung.

**Wie viele KI-Agenten in n8n?** Keinen. n8n ist der Dirigent und stößt nur Skripte an. Die einzige
KI-Entscheidung (`decide`) läuft per `claude -p` über dein Max-Abo auf dem Mini – ohne API-Key.

**Warum lädt die Pipeline nicht selbst zu YouTube/TikTok hoch?** Uploads über ungeprüfte API-Apps sind bei
beiden nur privat sichtbar. Bis die Apps geprüft sind, kommt das fertige Paket per Telegram, und der Bot achtet
darauf, dass kein Clip auf einer Plattform fehlt.
