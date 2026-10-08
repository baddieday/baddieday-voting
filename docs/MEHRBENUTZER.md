# Mehrbenutzer – eine Instanz je Freund (Entscheidung M1, 08.10.2026)

Ziel Stufe 1: Ein Freund bekommt auf dem Mini seine eigene, vollständig getrennte Pipeline. Zwei Benutzer arbeiten
unabhängig und ohne Zugriff aufeinander. Florian merkt nichts. Annahmen M2–M32: `docs/ENTSCHEIDUNGEN.md`,
„Mehrbenutzer (Clip-Pipeline 4.0)“. Stand: Schritt 1 und 2 von 4 sind umgesetzt (eine Rechen-Sperre, Instanz-Modus),
der Rest ist Plan.

**Kurz:** Jeder Freund bekommt eine eigene, abgeschlossene Kopie der Pipeline – eigener Bot in Telegram, eigener
Speicher, er lernt nur aus seinen eigenen Videos. Geteilt wird nur die Rechen-Sperre: Der Mini rechnet weiter immer
nur eine Sache auf einmal. Bei Florian bleibt alles, wie es ist.

## Bausteine
- **Code:** `/opt/clip-pipeline` (main), für Freunde nur lesbar. Die Repo-`pipeline.toml` ist das Startwissen für alle.
- **Florian = Stamm-Instanz** (ohne `CLIP_INSTANZ`), unverändert: `/var/lib/clip-pipeline` (Datenbank, Sperre, claude,
  Schlüssel für pve-big), Puffer `/srv/puffer`, Lager `/srv/big/clips`, n8n, Clip-Bot, Lern-Bot.
- **Instanz je Freund:** Benutzer `clip-<name>` (ohne Anmeldung), Ordner I = `/var/lib/clip-benutzer/<name>` auf dem
  Freunde-Volume. Dienste aus `deploy/benutzer/`:
  - `clip-freund-bot@` – sein Lern-Bot
  - `clip-freund-scan@` + Timer – alle 5 min `scan --verarbeiten --max 1 --versuche 3`
  - `clip-freund-abend@` + Timer – alle 10 min `sitzungen` (Abend-Video; KI nur mit eigenem Claude-Zugang)
  - `clip-freund-einrichten@`, `clip-freund-pruefen@` – einmalig
- **Gemeinsam:** nur Florians Sperrdatei `/var/lib/clip-pipeline/pipeline.lock`, dazu Prozessor, Grafikchip und Netz.

## Ordner (I = `/var/lib/clip-benutzer/<name>`)
| Pfad | Rechte | Inhalt |
|---|---|---|
| `/var/lib/clip-benutzer` | root 0711 | – |
| I | `root:clip-<name>` 0750 | `instanz.toml` und `.env` (beide `root:clip-<name>` 0640), Marke `.clip-benutzer` |
| I/db | 0700 | `pipeline.db`, `publikum-oauth.json`, `mikro.anstoss`, `big-zustand` |
| I/daten (= Puffer) | 0700 | `.clip-speicher`, `.clip-puffer`, `eingang/`, `replays/`, `sessions/`, `sitzungen/`, `export/` |
| I/regie, I/musik, I/material, I/sfx, I/cache | 0700 | I/cache ist auch HOME und Whisper-Cache |

## Konfig im Instanz-Modus (`CLIP_INSTANZ=I`, umgesetzt in Schritt 2)
`konfig.lade()` schaut zuerst nach `CLIP_INSTANZ`. Ohne die Variable (Florian) läuft alles wie bisher; mit ihr gilt
nur, was dem Freund gehört (`konfig.lade_instanz`). Vorlagen: `config/instanz.beispiel.toml`, `.env.example` (unten).
- **Ordner:** absoluter Pfad, Name aus a-z, 0-9, - (2–27 Zeichen), die Marke `I/.clip-benutzer` nennt genau diesen
  Namen. I darf sich nicht mit Florians Bereichen überschneiden (`/var/lib/clip-pipeline`, `/srv`, `/opt/clip-regie`,
  Code-Ordner).
- **Umgebung:** Als Erstes fliegen `TELEGRAM_*`, `LEARN_BOT_*`, `TIKTOK_*`, `YOUTUBE_*`, `CLAUDE_*`, `ANTHROPIC_*` und
  `CLIP_EPIC_ID` raus, danach gilt nur `I/.env` – und dort nur diese Namen (sonst Exit 2). Florians `.env` und
  `lokal.toml` werden nie geöffnet. Verboten: `--konfig`, `CLIP_KONFIG`, `CLIP_SPEICHER`, `CLIP_DATENBANK` (Exit 2).
- **Quellen:** Repo-`pipeline.toml` plus `I/instanz.toml`. Dort erlaubt: `[schnitt]`, `[zeit]`, `[merkmale.waffen]`
  (nur Schlüssel, die die Repo-Konfig dort kennt), `[sperre]` `datei`/`warten_s`, `[instanz]` `claude`. Alles andere
  (Datenbank, Lager, pve-big, Pfade …) ist ein Konfig-Fehler.
- **Erzwungen:** `I/db/pipeline.db`, Puffer `I/daten`, `I/regie`, `I/musik`, `I/material`, `I/sfx`, Zustand von
  pve-big in `I/db`; kein Host, keine MAC, kein SSH (auch kein Schlüssel); Freigeben im Puffer und Aufräumen aus;
  Lager-Pfad `I/kein-lager` (darf es nicht geben – jeder Lager-Zugriff scheitert sicher, Puffer-Betrieb ohne Lager).
- **Sperre:** `[sperre].datei` ist Pflicht, absolut und außerhalb von I. Fehlt `warten_s`, wartet ein Freund 900 s.
- **Pfadwächter:** Jeder Datenpfad (auch die Unterordner im Puffer) muss aufgelöst in I liegen – ein Link hinaus ist
  ein Konfig-Fehler.
- **KI nur mit eigenem Claude-Zugang** (Florian: „Eigener Claude-Zugang“): ein Langzeit-Token aus `claude setup-token`
  im Abo des Freundes, in `I/db/claude-token` (schreibt später sein Bot per `/claude`) oder als
  `CLAUDE_CODE_OAUTH_TOKEN` in `I/.env`. Es wird erst beim Aufruf gelesen (ein später verbundener Zugang wirkt ohne
  Neustart) und kommt nie in die Umgebung des Prozesses. claude startet dann mit eigener, kleiner Umgebung: Token,
  `HOME=I/cache`, `CLAUDE_CONFIG_DIR=I/cache/claude`, fester Suchpfad `/usr/local/bin:/usr/bin:/bin`, `LANG`,
  `DISABLE_AUTOUPDATER=1`. Programm: `[instanz].claude` oder `claude` aus dem festen Suchpfad – nie aus `/home`,
  `/root`, `/var/lib/clip-pipeline` oder `/opt/clip-regie`, auch nicht über einen Link. Ohne Token startet claude bei
  ihm nie; KI-Note, KI-Cutter und KI-Einschätzung werden dann gar nicht erst vorbereitet (keine Rechenzeit unter der
  gemeinsamen Sperre). 📋 Stand sagt „KI-Note: aus (kein eigener Claude-Zugang)“ und nichts zu TikTok. Tageslimits
  gelten je Instanz (eigene Datenbank).

## Trennung – jede Schicht einzeln prüfbar
1. **Getrennte Dateien statt SQL-Filter:** eigene Datenbank, eigener Ordner. Keine SQL-Anweisung kann Daten mischen.
2. **Kernel:** eigene Benutzer-ID, Ordner 0700/0750. Signale an fremde Prozesse scheitern.
3. **Sandbox** (gleicher Block in jeder Vorlage): `ProtectSystem=strict`, `ProtectHome`, `PrivateTmp`,
   `NoNewPrivileges`; leere, schreibgeschützte Ordner über `/srv`, `/var/lib/clip-pipeline`, `/var/lib/clip-benutzer`
   und `/opt/clip-pipeline/config`; eingebunden werden nur I, die Sperrdatei (nur lesen) und `pipeline.toml`.
   Florians Datenbank, claude, Schlüssel, Puffer, Lager und andere Freunde gibt es dort gar nicht.
4. **Geheimnisse:** gehören root. Je Freund ein eigener Bot-Token und genau eine erlaubte Telegram-ID.
5. **Aufträge:** eine Sperre; Freunde warten höchstens 15 min, dann übernimmt der nächste Timer-Lauf. n8n erreicht nur
   Florian.

## Eine Rechen-Sperre (umgesetzt, Schritt 1)
- `sperre.pfad(konfig)` ist die einzige Stelle, die den Pfad bestimmt: `[sperre].datei`, leer = wie bisher
  `<datenbank>.lock`. Florian trägt nichts ein; ein Freund trägt Florians Datei ein.
- Darf ein Prozess die Datei nicht schreiben, öffnet er sie nur lesend – flock wirkt trotzdem, in beide Richtungen.
  Fehlt sie und lässt sie sich nicht anlegen: klarer Fehler (Exit 2), nie eine Ersatzsperre.
- `/paket` im Clip-Bot rendert jetzt auch unter der Sperre (wartet nicht, sagt „gleich nochmal“).
- Jeder gesperrte Schritt schreibt „Sperre gewartet x s, gehalten y s“ ins Log – so sieht man vor und nach dem ersten
  Freund, wie lange Schritte aufeinander warten: `journalctl -u clip-lernbot | grep "Sperre gewartet"`.

## Florians Antworten (08.10.) und was daraus folgt
| Frage | Antwort | Folge |
|---|---|---|
| Wie kommen die Aufnahmen zum Mini? | „Muss das auf dem Mini sein, ich hab doch einen externen n8n-Server?!“ | Briefkasten auf dem vServer: kleiner Upload-Dienst, nicht n8n, keine Videos durch n8n. Der Mini holt über Tailscale ab und rechnet. Kommt mit Stufe 2. Keine Samba-Freigabe je Freund, keine eigene psd1 in Stufe 1 |
| Speicher, alte Rohvideos? | „Wie bei dir, mit Lager“ | Ab Stufe 2: Rohvideos ins Lager auf pve-big (eigener Unterordner je Freund), im Puffer nach 14 Tagen frei bei bestätigter Kopie (wie B5). In Stufe 1 wird bei Freunden nichts gelöscht |
| KI für Freunde? | „Eigener Claude-Zugang“ | Jeder Freund mit eigenem Claude-Abo; ohne Zugang bleibt die KI bei ihm aus |

Geplanter Lager-Abgleich ab Stufe 2 (nur geplant): Florians täglicher Abgleich hält pve-big wach; danach läuft je
Freund ein Abgleich als dessen eigener Benutzer in der Sandbox, der nur seinen eigenen Unterordner sieht. Ein Freund
weckt nie. Offen: freier Speicher auf dem vServer für den Briefkasten.

## Migration
- Keine Daten werden bewegt, das Schema bleibt. Ohne `CLIP_INSTANZ` und ohne `[sperre].datei` läuft alles wie bisher.
- Das Update sichert zusätzlich jede Instanz-Datenbank, legt die Vorlagen nur auf die Platte (schaltet sie nie ein) und
  startet laufende Freundes-Bots neu. Florian spielt alles selbst ein; kein Auto-Update.

## Schnittstellen
- **n8n-Vertrag:** unverändert, nur für Florian. Freunde laufen ohne n8n über Timer.
- **Windows-Skript und Samba `[clips]`:** unverändert.

## Stufen
1. **Sichere Benutzertrennung** (dieser Plan): gemeinsame Sperre, Instanz-Modus, Freund-Pipeline ohne n8n,
   Isolationstests, Dienst-Vorlagen mit Sandbox, Freunde-Volume, Anlegen und Prüfen mit einem Befehl, Einladungslink.
2. **Freunde liefern selbst:** Briefkasten auf dem vServer + kleines Programm für den PC, Lager je Freund mit
   Freigabe nach 14 Tagen, Meldungen an den Freund, Auto-Freigabe und 2-Wochen-Video ohne Clip-Bot.
3. Warteschlange vor der Sperre (Vorrang, Laufzeit-Protokoll), Auftrags-Vertrag für Rechen-Arbeiter.
4. Kampagnenlink je Instanz; neue Zielgrößen versioniert neben dem alten Score.
5. Liga je Instanz-Datenbank.
