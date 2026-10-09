# Mehrbenutzer – eine Instanz je Freund (Entscheidung M1, 08.10.2026)

Ziel Stufe 1: Ein Freund bekommt auf dem Mini seine eigene, vollständig getrennte Pipeline. Zwei Benutzer arbeiten
unabhängig und ohne Zugriff aufeinander. Florian merkt nichts. Annahmen M2–M83: `docs/ENTSCHEIDUNGEN.md`,
„Mehrbenutzer (Clip-Pipeline 4.0)“. Stand: Schritt 1 bis 9 sind umgesetzt (eine Rechen-Sperre, Instanz-Modus,
Freund-Pipeline ohne n8n, Trennung Ende-zu-Ende geprüft, Dienst-Vorlagen mit Sandbox, Speicher für Freunde, Freund
anlegen und prüfen mit einem Befehl, Einladungslink statt Telegram-Zahl, eigener Claude-Zugang per /claude). Seite für
Freunde: `docs/FREUNDE.md`. Stufe 2 („Freunde liefern selbst“, Annahmen ab M85): Schritt 1 Briefkasten auf dem
vServer (`docs/BRIEFKASTEN.md`), Schritt 2 Abholen am Mini und Schritt 3 Abholen einschalten (unten) sind gebaut –
eingeschaltet wird je Freund mit `benutzer-anlegen.sh`, Schritt „Briefkasten“. Stufe 3 („Hybrider Render-Manager“,
Annahmen M139–M152) ist fertig: Schritt 1 Laufzeiten messen, Schritt 2 Ausfallsicher, Schritt 3 Florian zuerst und
Schritt 4 Vertrag für weitere Rechner (`docs/WORKER.md`, nur Doku) – Stufenbericht unten. Stufe 4 („Qualitäts- und
Erfolgsmessung“, Annahmen M155–M174) ist fertig: Schritt 1 `pipeline erfolg`, Schritt 2 ehrliche Sätze im
Sonntagsbericht, in /lernstand und /publikum – Stufenbericht unten; Zähler auf clip-battle.de und Video-Code in der
Caption erst nach Florians Ja.

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
  - `clip-freund-einrichten@`, `clip-freund-pruefen@` – einmalig, gestartet von `benutzer-anlegen.sh` und
    `benutzer-pruefen.sh` (Schritt 7)
  - `clip-freund-koppeln@` – Einladungslink, einmalig, gestartet von `benutzer-anlegen.sh` (Schritt 8)
  - `clip-freund-abholen@` + Timer – alle 2 min `briefkasten abholen` (Stufe 2; an erst nach grüner Probe-Abholung)
  - `clip-freund-lager@` – ohne Timer, `lager abgleich` mit seinem Unterordner im Lager (Stufe 2; gestartet nur vom
    Rundgang `clip-lager-freunde.service`, der als root bei Florians Abgleich mitfährt)
- **Gemeinsam:** nur Florians Sperrdatei `/var/lib/clip-pipeline/pipeline.lock`, dazu Prozessor, Grafikchip und Netz.

## Ordner (I = `/var/lib/clip-benutzer/<name>`)
| Pfad | Rechte | Inhalt |
|---|---|---|
| `/var/lib/clip-benutzer` | root 0711 | – |
| I | `root:clip-<name>` 0750 | `instanz.toml` und `.env` (beide `root:clip-<name>` 0640), Marke `.clip-benutzer` |
| I/db | 0700 | `pipeline.db`, `publikum-oauth.json`, `mikro.anstoss`, `big-zustand`, `kopplung.json` (0600); Stufe 2: `briefkasten.json`, `pc-status.json`, `pipeline.briefkasten.lock` |
| I/daten (= Puffer) | 0700 | `.clip-speicher`, `.clip-puffer`, `eingang/`, `replays/`, `sessions/`, `sitzungen/`, `export/`; Stufe 2: `.abholen/` (Zwischenablage des Abholers) |
| I/briefkasten (Stufe 2) | `root:clip-<name>` 0750 | Schlüssel `abholen`, `pc` (je mit `.pub`), `known_hosts`, `known_hosts_pc` – alle `root:clip-<name>` 0640: er liest sie, tauschen kann er sie nicht |
| I/lager (Stufe 2) | root:root 0755, leer | Einhängepunkt: nur im Dienst `clip-freund-lager@` liegt hier sein Unterordner `freunde/<name>` aus dem Lager |
| I/regie, I/musik, I/material, I/sfx, I/cache | 0700 | I/cache ist auch HOME und Whisper-Cache |

## Konfig im Instanz-Modus (`CLIP_INSTANZ=I`, umgesetzt in Schritt 2)
`konfig.lade()` schaut zuerst nach `CLIP_INSTANZ`. Ohne die Variable (Florian) läuft alles wie bisher; mit ihr gilt
nur, was dem Freund gehört (`konfig.lade_instanz`). Gesetzt, aber leer, ist ein Konfig-Fehler (Exit 2) – nie still
Florians Konfig (M42). Vorlagen: `config/instanz.beispiel.toml`, `.env.example` (unten).
- **Ordner:** absoluter Pfad, Name aus a-z, 0-9, - (2–27 Zeichen), die Marke `I/.clip-benutzer` nennt genau diesen
  Namen. I darf sich nicht mit Florians Bereichen überschneiden (`/var/lib/clip-pipeline`, `/srv`, `/opt/clip-regie`,
  Code-Ordner).
- **Umgebung:** Als Erstes fliegen `TELEGRAM_*`, `LEARN_BOT_*`, `TIKTOK_*`, `YOUTUBE_*`, `CLAUDE_*`, `ANTHROPIC_*` und
  `CLIP_EPIC_ID` raus, danach gilt nur `I/.env` – und dort nur diese Namen (sonst Exit 2). Florians `.env` und
  `lokal.toml` werden nie geöffnet. Verboten: `--konfig`, `CLIP_KONFIG`, `CLIP_SPEICHER`, `CLIP_DATENBANK` (Exit 2).
- **Quellen:** Repo-`pipeline.toml` plus `I/instanz.toml`. Dort erlaubt: `[schnitt]`, `[zeit]`, `[merkmale.waffen]`
  (nur Schlüssel, die die Repo-Konfig dort kennt), `[sperre]` `datei`/`warten_s`, `[instanz]` `claude`, seit Stufe 2
  `[briefkasten]` (host, port, oeffentlich, drossel_kbit, loeschen, karenz_h – geprüft, siehe unten) und `[instanz]`
  `lager` (true/false, siehe „Lager für Freunde“). Alles andere (Datenbank, Lager-Pfade, pve-big, Pfade …) ist ein
  Konfig-Fehler.
- **Erzwungen:** `I/db/pipeline.db`, Puffer `I/daten`, `I/regie`, `I/musik`, `I/material`, `I/sfx`, Zustand von
  pve-big in `I/db`; kein Host, keine MAC, kein SSH (auch kein Schlüssel); Aufräumen aus. Ohne Lager-Schalter:
  Freigeben im Puffer aus, Lager-Pfad `I/kein-lager` (darf es nicht geben – jeder Lager-Zugriff scheitert sicher,
  Puffer-Betrieb ohne Lager). Mit `[instanz] lager = true` (Stufe 2): Lager `I/lager`, Marke `.clip-lager-<name>`,
  Freigeben wie bei Florian (B5).
- **Sperre:** `[sperre].datei` ist Pflicht, absolut, außerhalb von I und muss es schon geben (Florians Datei – eine
  Instanz legt nie eine eigene an, auch nicht bei einem Tippfehler, M41). Fehlt `warten_s`, wartet ein Freund 900 s.
- **Pfadwächter:** Jeder Datenpfad (auch die Unterordner im Puffer) muss aufgelöst in I liegen – ein Link hinaus ist
  ein Konfig-Fehler.
- **KI nur mit eigenem Claude-Zugang** (Florian: „Eigener Claude-Zugang“): ein Langzeit-Token aus `claude setup-token`
  im Abo des Freundes, in `I/db/claude-token` (schreibt sein Bot per `/claude`, Schritt 9) oder als
  `CLAUDE_CODE_OAUTH_TOKEN` in `I/.env`. Es wird erst beim Aufruf gelesen (ein später verbundener Zugang wirkt ohne
  Neustart) und kommt nie in die Umgebung des Prozesses. claude startet dann mit eigener, kleiner Umgebung: Token,
  `HOME=I/cache`, `CLAUDE_CONFIG_DIR=I/cache/claude`, fester Suchpfad `/usr/local/bin:/usr/bin:/bin`, `LANG`,
  `DISABLE_AUTOUPDATER=1`. Programm: `[instanz].claude` oder `claude` aus dem festen Suchpfad – nie aus `/home`,
  `/root`, `/var/lib/clip-pipeline` oder `/opt/clip-regie`, auch nicht über einen Link. Ohne Token startet claude bei
  ihm nie; KI-Note, KI-Cutter und KI-Einschätzung werden dann gar nicht erst vorbereitet (keine Rechenzeit unter der
  gemeinsamen Sperre). 📋 Stand sagt „KI-Note: aus – verbinde dein Claude mit /claude“ und nichts zu TikTok. Tageslimits
  gelten je Instanz (eigene Datenbank).

## Trennung – jede Schicht einzeln prüfbar
1. **Getrennte Dateien statt SQL-Filter:** eigene Datenbank, eigener Ordner. Keine SQL-Anweisung kann Daten mischen.
2. **Kernel:** eigene Benutzer-ID, Ordner 0700/0750. Signale an fremde Prozesse scheitern.
3. **Sandbox** (gleicher Block in jeder Vorlage): `ProtectSystem=strict`, `ProtectHome`, `PrivateTmp`,
   `NoNewPrivileges`; leere, schreibgeschützte Ordner über `/srv`, `/var/lib/clip-pipeline`, `/var/lib/clip-benutzer`
   und `/opt/clip-pipeline/config`; eingebunden werden nur I, die Sperrdatei (nur lesen) und `pipeline.toml`.
   Florians Datenbank, claude, Schlüssel, Puffer, Lager und andere Freunde gibt es dort gar nicht.
4. **Geheimnisse:** gehören root. Je Freund ein eigener Bot-Token und genau eine erlaubte Telegram-ID.
5. **Aufträge:** eine Sperre; Freunde warten höchstens 15 min, dann übernimmt der nächste Timer-Lauf, und fragen
   seltener nach ihr als Florian (Stufe 3, „Florian zuerst“). n8n erreicht nur Florian.

## Eine Rechen-Sperre (umgesetzt, Schritt 1)
- `sperre.pfad(konfig)` ist die einzige Stelle, die den Pfad bestimmt: `[sperre].datei`, leer = wie bisher
  `<datenbank>.lock`. Florian trägt nichts ein; ein Freund trägt Florians Datei ein.
- Darf ein Prozess die Datei nicht schreiben, öffnet er sie nur lesend – flock wirkt trotzdem, in beide Richtungen.
  Fehlt sie und lässt sie sich nicht anlegen: klarer Fehler (Exit 2), nie eine Ersatzsperre.
- `/paket` im Clip-Bot rendert jetzt auch unter der Sperre (wartet nicht, sagt „gleich nochmal“).
- Jeder gesperrte Schritt schreibt „Sperre gewartet x s, gehalten y s“ ins Log – so sieht man vor und nach dem ersten
  Freund, wie lange Schritte aufeinander warten: `journalctl -u clip-lernbot | grep "Sperre gewartet"`. Seit Stufe 3
  steht das zusätzlich in der Datenbank: `pipeline laufzeiten` (unten).

## Freund-Pipeline ohne n8n (umgesetzt, Schritt 3)
- **`scan --verarbeiten --max 1 --versuche 3`** (Timer alle 5 min): je Lauf nur das älteste offene Match, so ist die
  gemeinsame Sperre nur kurz belegt. Scheitert ein Match dreimal, bekommt es den Status `fehler`, sein Bot sagt es
  einmal („⚠️ Ein Match vom … klappt nicht …“), und die neueren kommen dran. Jeder Fehlschlag steht in `ereignisse`
  (`verarbeitung_fehler`). Nichts wird gelöscht: `pipeline process <ID>` holt ein solches Match jederzeit nach.
  Speicher offline, Konfig-Fehler oder eine belegte Datenbank zählen nie als Fehlschlag. Ohne die beiden Schalter läuft
  `scan` genau wie bisher.
- **Abend-Video** (`sitzungen`, Timer alle 10 min): Geht Whisper nicht (z. B. Modell lässt sich nicht laden), werden die
  Szenen ohne Sprache gemessen, und das Video kommt trotzdem – mit Hinweis im Ergebnis; das hilft auch Florian (vorher
  brach jeder Lauf ab). Auf ein Match mit Status `fehler` wartet der Abend nicht – bei Florian wird keins `fehler`.

## Trennung geprüft (umgesetzt, Schritt 4)
- **Generalprobe** (`tests/test_isolation.py`): Florian, max und eva spielten dasselbe Squad-Match – gleiche
  Session-ID, aber jeder mit eigener Aufnahme und eigener Epic-ID. Jeder rechnet in einem eigenen Prozess wie später im
  Dienst (`scan --verarbeiten`, danach `stimmung`), mit echtem ffmpeg. Nach jedem Lauf ist der Fingerabdruck (SHA-256)
  aller Dateien der beiden anderen und der Installation gleich; Clips, Momente und Session-Ordner entstehen nur beim
  Läufer. Jede Datenbank hat nur ihr Match mit ihren Kills, keins wird als „schon fertig“ übersprungen. Jeder Lauf nahm
  die gemeinsame Sperre; solange sie belegt ist, rechnet keiner (Exit 4).
- **n8n** (`tests/test_n8n_einstieg.py`): Die Befehle aus den n8n-Workflows kommen durch `deploy/n8n-lauf.sh`;
  `--konfig`, ein vorangestelltes `CLIP_INSTANZ=`, `scan`, ein Zusatzwort, eine ungültige Session-ID oder ein
  angehängter Shell-Befehl werden mit „Aufruf nicht erlaubt“ (Exit 2) abgewiesen, ohne dass die Pipeline startet.
- Nicht im Test: Lesen über Benutzergrenzen (alle Läufe als derselbe Benutzer) – das sichern eigene Benutzer und
  Sandbox (Schritt 5, nachgebaut im Kernel), geprüft vor Ort mit `benutzer-pruefen.sh` (Schritt 7).

## Dienste je Freund (umgesetzt, Schritt 5)
- **Vorlagen** in `deploy/benutzer/`: `clip-freund-bot@` (sein Lern-Bot), `clip-freund-scan@` + Timer (alle 5 min ein
  Match), `clip-freund-abend@` + Timer (alle 10 min Abend-Video). `%i` ist der Name. Beide Timer-Dienste: Exit 3/4 kein
  Fehler (4 = Sperre belegt), nach 1 h ohne Ende abgebrochen (seit Stufe 3, vorher 2 h = Florians Wartezeit) – dann ist
  die gemeinsame Sperre wieder frei.
- **Sandbox** – derselbe Block in jeder Vorlage (ein Test wacht darüber): Benutzer `clip-<name>`, `CLIP_INSTANZ=I`,
  HOME und Caches in `I/cache`, keine Zugänge über systemd. Alles nur lesbar, `/home` und `/root` gibt es nicht. Leere,
  schreibgeschützte Ordner über `/srv`, `/var/lib/clip-pipeline`, `/var/lib/clip-benutzer` und
  `/opt/clip-pipeline/config`; eingebunden werden nur I (schreibbar), die Sperrdatei und `pipeline.toml` (nur lesen).
  `/opt/clip-pipeline/.env` und `/opt/clip-regie` sind gesperrt. Halber Vorrang beim Prozessor, höchstens 3 GB.
  Fehlt die Sperrdatei, startet der Dienst gar nicht.
- **Eingeschaltet** wird nur über `benutzer-anlegen.sh <name>` (Schritt 7) – nie vom Update, nie von Hand.
- **Update** (`alles-aktualisieren.sh`), nur wenn es einen Freund gibt (Ordner mit Marke und Benutzer `clip-<name>`):
  vor dem Umstellen jede Freundes-Datenbank als `clip-<name>` nach `I/db/vor-update-<Zeit>.db` (nie gelöscht, nur echte
  Dateien, kein Link), danach die Vorlagen wie die übrigen Dienste (von Hand geänderte bleiben) und die laufenden
  Freundes-Bots neu. Scheitert eine Sicherung, sagt es das und stellt trotzdem um. Ohne Freunde: genau wie bisher.
- **Geprüft** (`tests/test_deploy_benutzer.py`): Vorlagen-Inhalt und `systemd-analyze verify`; Kernel-Nachbau als root
  (eine fremde uid sieht in Florians Ordner nur die Sperrdatei, schreiben darf sie sie nicht, die Sperre wirkt in beide
  Richtungen, der eigene Ordner bleibt schreibbar); das Update mit Attrappen (gesichert, nicht eingeschaltet, Bot neu,
  ohne Freunde keine Änderung, Link statt Datenbank wird nicht kopiert).

## Einmal für alle Freunde: Speicher (umgesetzt, Schritt 6)
Ein eigenes Volume für alle Freunde im Thin-Pool des Mini, im CT unter `/var/lib/clip-benutzer` (Standard 100 GB) –
nicht auf der CT-Platte (dort liegt deine Datenbank) und nicht in deinem Puffer. Läuft es voll, trifft das nur die
Freunde. Einmal vor dem ersten Freund; bis dahin merkt die Pipeline nichts davon.

Auf pve-mini (Host) als root, **nicht während eines Spielabends** (der CT ist ca. 1 min aus):
```bash
curl -fsSL https://raw.githubusercontent.com/baddieday/baddieday-voting/main/deploy/pve-mini/freunde-volume.sh -o /root/freunde-volume.sh
bash /root/freunde-volume.sh --probe     # zeigt nur, ändert nichts
bash /root/freunde-volume.sh             # fragt vor jeder Änderung (j = ja); andere Größe: --groesse 60
```
- **Bricht ab, ohne etwas zu ändern,** wenn der Pool mit ganz vollem Freunde-Volume **und** ganz vollem Puffer über
  90 % käme (dann sagt es, welche Größe passt), wenn gerade ein Pipeline-Schritt läuft, `mp2` anders belegt ist oder
  in `/var/lib/clip-benutzer` auf der CT-Platte schon etwas liegt (das Volume würde es verdecken).
- **Sonst:** CT-Konfig sichern (`/root/freunde-volume/`), Rückweg-Skript schreiben, CT aus, Volume als `mp2` anlegen
  (nicht im vzdump-Backup, wie der Puffer), CT an. Im CT gehört die Wurzel dann root mit Rechten 0711: Jeder Freund
  kommt nur in seinen eigenen Ordner und sieht die anderen nicht. Ein zweiter Lauf überspringt Fertiges.
- **Prüfen:** `pct config 102 | grep clip-benutzer` · `pct exec 102 -- ls -ld /var/lib/clip-benutzer` (zeigt
  `drwx--x--x … root root`).
- **Rückweg:** `bash /root/freunde-volume/zurueck.sh` (auch mit `--probe`) hängt das Volume nur aus – gelöscht wird
  nichts, es bleibt als `unusedN` in der CT-Konfig. Solange Dienste von Freunden laufen, weigert es sich. Ein neuer
  Lauf von `freunde-volume.sh` hängt genau dieses Volume wieder ein, statt ein leeres neues anzulegen.
- **Größer machen** (nur wachsen, nichts geht verloren): `pct resize 102 mp2 150G` – vorher `lvs pve/data` ansehen.
  Wird es knapp, sagt es dir die Morgenprüfung (unter 15 GB frei, Stufe 2, Schritt 6).
- Geprüft (`tests/test_deploy_benutzer.py`, Attrappen wie beim Puffer): Probe ändert nichts, Pool-Grenze mit vollem
  Puffer, zweiter Lauf ohne Änderung, Rückweg hängt nur aus und sperrt bei laufenden Freunden, Wiedereinhängen.

## Neuen Freund anlegen (umgesetzt, Schritt 7)
Einmal vorher: das Freunde-Volume (oben). Je Freund rund 5 min (die meiste Zeit lädt das Whisper-Modell):
1. Bei @BotFather mit `/newbot` einen eigenen Bot für ihn anlegen (z. B. „Max Clips“), Token bereithalten.
2. Er schickt dir seine Epic-Konto-ID (epicgames.com → Konto, 32 Zeichen). Seine Telegram-Zahl braucht es nicht mehr.
3. Im CT als root, erst ansehen, dann echt (fragt einmal „j“, dann Token und Epic-ID unsichtbar):
   ```bash
   bash /opt/clip-pipeline/deploy/benutzer/benutzer-anlegen.sh max --probe
   bash /opt/clip-pipeline/deploy/benutzer/benutzer-anlegen.sh max
   ```
4. Unterwegs zeigt es dir seinen **Einladungslink** – schick ihn ihm (gilt 15 min, nur einmal). Er tippt ihn an und
   drückt in Telegram auf Start; sein Bot antwortet „Verbunden“, das Skript trägt seine Zahl ein und schaltet seinen Bot
   ein. Ist er gerade nicht da: Strg+C (oder 15 min warten) – später nochmal das Skript, dann kommt ein neuer Link.
5. Am Ende steht „Alles getrennt“ – schick ihm `docs/FREUNDE.md`.

Was das Skript tut – passt etwas nicht, bricht es vor der ersten Änderung ab; ein zweiter Lauf überspringt Fertiges:
- prüft den Namen (keine Namen deiner Dienste), das Freunde-Volume (eigener Speicher, root, 0711) und deine Sperre (=
  die in den Vorlagen) und baut seine `instanz.toml` aus deiner wirksamen Konfig: nur Sperre, Rechnerwerte, Waffen;
- fragt Bot-Token und Epic-Konto-ID verdeckt ab (einen Token, den du oder ein Freund schon nutzt, lehnt es ab) und
  schreibt sie nur in seine `.env` (root:clip-<name>, 0640) – nie auf den Bildschirm, nie ins Log;
- legt Benutzer `clip-<name>` ohne Anmeldung an, seine Ordner, macht deine Sperrdatei für alle lesbar (nur
  `chmod 644`) und legt fehlende Dienst-Vorlagen hin;
- richtet auf Wunsch (j/N) seinen Briefkasten ein (Stufe 2, oben);
- richtet in seiner Sandbox ein (Datenbank, Whisper-Modell, Musik seiner Genres) und prüft dort die Trennung, dann
  verbindet es ihn über den Einladungslink mit Telegram (unten) – erst danach schaltet es seine Dienste ein, nur für ihn
  (ohne Verbindung bleibt nur sein Bot aus);
- prüft zum Schluss alles und bietet an, deine eigenen Rechte zu schärfen (j/N, nur `chmod`: dein Ordner nur noch
  passierbar; Datenbank, claude, Schlüssel, `.env` und `lokal.toml` nur für dich; Rückweg unter `/root/benutzer-rechte/`).
  Nur, was pipeline gehört – deine Dienste laufen als pipeline und merken davon nichts. Was root gehört (z. B. eine mit
  nano angelegte `lokal.toml`), bleibt mit Hinweis, wie es ist; sonst könnten deine Dienste es nicht mehr lesen (M84).

Danach (alle im CT als root, `…` = `/opt/clip-pipeline/deploy/benutzer`):
- **Prüfen**, ändert nichts: `bash …/benutzer-pruefen.sh max` – Rechte, dieselbe Sperrdatei (in seiner Sandbox Gerät
  und Inode wie bei dir), alle Bot-Tokens verschieden (nur als Prüfsumme verglichen), nichts von dir oder den anderen in
  seiner Sandbox, dazu sein Bot-Link. Exit 1 bei einem Befund.
- **Ein Match nachholen** (Status `fehler`): `bash …/benutzer-befehl.sh max process <ID>` – ein pipeline-Befehl als er,
  in derselben Sandbox wie seine Dienste.
- **Ausschalten**, Daten bleiben: `bash …/benutzer-stilllegen.sh max` (auch eine offene Einladung) – wieder an mit
  `benutzer-anlegen.sh max`.
- **KI für ihn** (freiwillig): sein eigenes Claude-Abo – er tippt in seinem Bot `/claude` (unten, Schritt 9). Einmal
  vorher von dir: claude global installiert (z. B. per npm, nicht unter `/home`). `benutzer-pruefen.sh` sagt, was fehlt.

**Abnahme Stufe 1 vor Ort:** Test-Freund mit eigenem Test-Bot anlegen und eine Kopie eines deiner Abende *als er* in
seinen Puffer legen – bei dir wird nichts verschoben oder gelöscht (deine Dateien im Puffer sind für alle lesbar):
```bash
runuser -u clip-test -- cp -n --preserve=timestamps /srv/puffer/replays/<Abend>_*.replay /var/lib/clip-benutzer/test/daten/replays/
runuser -u clip-test -- cp -n --preserve=timestamps <Aufnahmen des Abends> /var/lib/clip-benutzer/test/daten/eingang/
```
Erwartet: Sein Abend-Video kommt nur in seinem Bot, `benutzer-pruefen.sh test` ist grün, dein nächster n8n-Lauf und dein
Abend-Video laufen wie immer. Wartezeiten vorher und nachher: `journalctl -u clip-lernbot | grep "Sperre gewartet"`.
- Geprüft (`tests/test_benutzer.py`, `tests/test_deploy_benutzer.py`): `benutzer pruefen` im nachgestellten Namensraum
  (sauber → ok; Florians Puffer, eine beschreibbare `.env` oder ein anderer Freund → Exit 1 mit dem Pfad), `einrichten`
  lädt beim zweiten Lauf nichts; die Skripte in einer Scheinwurzel: Probe ändert nichts, zweiter Lauf ohne Änderung,
  derselbe Token in zwei `.env` ist ein Befund, Stilllegen schaltet nur aus, der Einzelbefehl nur mit Sandbox.

## Einladungslink statt Telegram-Zahl (umgesetzt, Schritt 8)
Der Freund muss keine Zahl suchen: Er tippt einen Link an und drückt Start.
- **In seiner Sandbox** (`pipeline benutzer koppeln`, Vorlage `clip-freund-koppeln@`, gestartet nur von
  `benutzer-anlegen.sh`): fragt Telegram nach dem Namen seines Bots, macht einen Einmal-Code (32 Zeichen, zufällig) und
  legt den Link `t.me/<bot>?start=<code>` in `I/db/einladung.json` – nur für ihn und root lesbar, nie im Log. Dann
  liest es bis zu 15 min die Nachrichten seines Bots. Kommt „/start <code>“ in einem Einzel-Chat, schreibt es die Zahl
  des Absenders (mit Vorname) nach `I/db/kopplung.json` (0600) und antwortet „✅ Verbunden!“. Falscher oder alter Code,
  Gruppen, andere Nachrichten: nichts gespeichert (ein falscher Start bekommt einmal „Dieser Einladungslink gilt nicht
  (mehr)“). Alles Gelesene wird abgehakt – sein Bot sieht den Code später nicht. Nach der Frist: Exit 1.
- **Nur ein Empfänger je Bot:** koppeln läuft nur, solange keine Telegram-Zahl in seiner `.env` steht – ohne sie holt
  sein Bot nie Nachrichten ab (er beendet sich sofort). Das Anlege-Skript hält ihn vorher an (falls doch etwas läuft)
  und schaltet ihn erst nach der Kopplung ein. Meldet Telegram trotzdem einen zweiten Empfänger, bricht koppeln ab,
  ohne etwas zu speichern. Telegram fragt es wie sein Lern-Bot (über IPv4), keine neue Bibliothek.
- **Im Anlege-Skript** (Schritt 8 von 12): zeigt dir den Link (nur den dieses Laufs), wartet, liest danach als root
  `kopplung.json` (folgt keinem Link, nur eine Zahl und der Vorname) und hängt die Zahl als
  `LEARN_BOT_ALLOWED_USER_ID` an seine `.env` (bleibt root:clip-<name>, 0640). Strg+C beendet nur das Warten: die
  Einladung gilt weiter, der nächste Lauf übernimmt die Zahl ohne neuen Link.
- Geprüft (`tests/test_koppeln.py`, Telegram als Attrappe auf 127.0.0.1): richtiger Code → Zahl in `kopplung.json`
  (0600), „Verbunden“, nichts im Log; falscher oder alter Code, Gruppe, andere Nachricht → nichts gespeichert, nach der
  Frist Exit 1; schon verbunden oder ein zweiter Empfänger → nichts; ohne `CLIP_INSTANZ` Exit 2, deine `.env` bleibt,
  Telegram wird nicht gefragt. Das Skript (`tests/test_deploy_benutzer.py`): Zahl über die Kopplung, Bot erst danach,
  ohne Kopplung nur der Bot aus, ein Link statt `kopplung.json` wird nicht gelesen, ein alter Link nie gezeigt.

## Eigener Claude-Zugang per /claude (umgesetzt, Schritt 9)
Der Freund verbindet sein Claude-Abo selbst – ohne Konsole und ohne dich:
- **`/claude`** in seinem Bot startet `claude setup-token` in einem Pseudo-Terminal, mit derselben kleinen Umgebung wie
  jeder Claude-Aufruf seiner Instanz (HOME und Claude-Ordner in `I/cache`, kein Auto-Update, nichts aus der Umgebung des
  Bots). Den Anmelde-Link schickt der Bot ihm; seine nächste Nachricht ist der Code. Der Bot löscht sie, gibt den Code
  ein und legt das Token nach `I/db/claude-token` (0600) – ab dem nächsten Aufruf benotet die KI seine Videos.
- Ein Token aus `claude setup-token` auf seinem PC kann er auch direkt schicken (gespeichert, Nachricht gelöscht).
- **Fristen:** Link 60 s, Token nach dem Code 60 s, alles 10 min. Danach, bei einem Fehler oder einem neuen `/claude`
  wird claude beendet; gespeichert wird nichts, die Antwort nennt den Ausweg. Fehlt claude: „Florian muss Claude einmal
  auf dem Mini installieren“. Code und Token stehen nie im Log, nie in der Datenbank, nie wieder im Chat.
- **Bei dir** gibt es `/claude` nicht, `/tiktok` hat nur dein Bot. 📋 Stand eines Freundes ohne Zugang: „KI-Note: aus –
  verbinde dein Claude mit /claude“.
- Geprüft (`tests/test_claude_verbinden.py`, claude als Attrappe mit den Steuerzeichen des echten): Link → Code →
  Token-Datei 0600 mit genau dem Token, Code-Nachricht gelöscht, nichts im Log, claude beendet; Fehlermeldung,
  Schweigen, kein Link, Frist, claude fehlt → nichts gespeichert, claude beendet, freundliche Antwort; Token direkt;
  dein Bot ohne `/claude`, deine Hilfe gleich. Den Link findet es auch im echten `claude setup-token` (2.1.294).

## Briefkasten abholen (Stufe 2, Schritt 2 und 3 – umgesetzt)
Der PC eines Freundes lädt in sein Fach im Briefkasten (`docs/BRIEFKASTEN.md`); `pipeline briefkasten abholen` holt es
in seinen Puffer, alle 2 min über `clip-freund-abholen@` (Timer). Annahmen M89–M93, M95, M96, M103, M104, M108–M116.
- **Nur in der Instanz**, nur lesend über das Tailnet als `bk-<name>` mit `I/briefkasten/abholen` (Hostschlüssel
  gepinnt), höchstens 20 Mbit/s. Bei Florian Exit 2, bevor etwas angefasst wird. Ohne `[briefkasten].host`: „aus“.
- **Fertig ist eine Datei erst mit Lieferschein** (Name, Größe, SHA-256, Zeit vom PC). Geholt wird nach
  `I/daten/.abholen`, geprüft (Größe, SHA-256 kalt zurückgelesen), dann bekommt sie die Zeit vom PC (höchstens jetzt −
  130 s – scan nimmt sie sofort) und per `os.link` ihren Namen. Nie überschrieben; fremde Namen (auch ein Datum, das es
  nicht gibt) nie angefasst. Drei Fehlversuche: aufgegeben, eine Zeile an den Freund.
- **Reihenfolge:** Videos → Replays (nur ohne offenes Video) → Sitzungsdatei (erst, wenn ihre Matches verarbeitet sind,
  spätestens nach 24 h). So baut `sitzungen` danach sofort das Abend-Video; `auto_abend` ist bei Freunden mit
  Briefkasten aus.
- **Keine Rechen-Sperre**, eigene Sperre, weckt nie, löscht im Briefkasten nichts. Bremse: nur holen, wenn danach
  10 GB bzw. 10 % frei bleiben. Meldungen je Thema höchstens einmal am Tag (Fach 80 %, nicht erreichbar seit 24 h,
  Platz knapp, Datei aufgegeben).
- **Nachsehen:** `bash deploy/benutzer/benutzer-befehl.sh <name> briefkasten status` (ohne Netz: letzter Kontakt,
  Füllstand, Dateien je Zustand). Die Tabelle `abholung` gibt es nur in seiner Datenbank.
- **Einschalten (Schritt 3):** `benutzer-anlegen.sh <name>`, Schritt 9 „Briefkasten“ (j/N): einmal die Adressen des
  vServers (gemerkt in `/etc/clip-briefkasten.conf`, sobald der Briefkasten antwortet), zwei eigene Schlüssel in
  `I/briefkasten`, Hostschlüssel über das Tailnet gepinnt, `[briefkasten]` an seine `instanz.toml` angehängt, eine
  Zeile für den vServer, dann eine Probe-Abholung in seiner Sandbox – erst wenn die grün ist, geht der Timer an. Ist
  etwas rot, bleibt nur das Abholen aus; ein neuer Lauf setzt fort.
- **Vorlage:** wie die übrigen, derselbe Sandbox-Block; Exit 3/4 kein Fehler, nach 2 h Schluss (der nächste Lauf
  setzt fort), Platte im Leerlauf-Vorrang. Netz braucht er nur zum vServer (Tailnet, Port 2222); einen Netz-Zaun gibt es
  noch nicht (M19, vor Ort prüfen: M104).
- **Prüfen und Stilllegen:** `benutzer-pruefen.sh` zeigt Rechte und Schlüssel (kein Schlüssel doppelt), ob das Abholen
  an ist, wann der Briefkasten zuletzt erreicht wurde, den Füllstand und ob sich sein PC gemeldet hat;
  `benutzer-stilllegen.sh` schaltet auch das Abholen aus (sein Fach auf dem vServer bleibt: dort `--sperren`).

## PC-Programm der Freunde (Stufe 2, Schritt 4 – umgesetzt)
Der Freund tippt in seinem Bot `/pc` (nur bei Freunden mit Briefkasten), entpackt das ZIP und startet
`Freund-Einrichten.cmd`. Das Programm (`windows/Freund-Hochladen.ps1`, Aufgabe „Clip-Upload“ alle 2 min, Windows
PowerShell 5.1, kein Admin) lädt fertige Aufnahmen und Replays in sein Fach – je Datei `.teil`, umbenennen,
Lieferschein; Replays erst nach den Aufnahmen ihres Matches; 45 min nach dem letzten Match die Abend-Datei; beim Spielen
2 Mbit/s; es löscht nie. Anleitung für Freunde: `docs/FREUNDE.md`, für Florian: `docs/BRIEFKASTEN.md`. Annahmen M94,
M97, M117–M124.
- **Ein ZIP, jedes Mal frisch:** drei Skripte, `freund.psd1` (öffentliche Adresse, Port, `bk-<name>`), sein
  PC-Schlüssel und der Hostschlüssel – nie der Abhol-Schlüssel, nichts von anderen.
- **Rückmeldung:** `status/pc-status.json` (was wartet, was übersprungen ist); daraus meldet sein Bot „PC verbunden“,
  eine falsche Zeitzone und Aufnahmen ohne Replay, 📋 zeigt „PC: zuletzt vor … · n unterwegs“.

## Lager für Freunde (Stufe 2, Schritt 5 – umgesetzt)
„Wie bei dir, mit Lager“: Hat Florians täglicher Abgleich pve-big ohnehin geweckt, fahren die Freunde mit. Ein Freund
weckt nie. Annahmen M98–M101, M107, M125–M130.
- **Ort:** im bestehenden Lager `freunde/<name>/` (im CT `/srv/big/clips/freunde/<name>`) mit Marke
  `.clip-lager-<name>` – kein neues Dataset, kein neuer Export, keine neue Einhängung.
- **Schalter:** `[instanz] lager = true` plus der leere, root-eigene Einhängepunkt `I/lager`; beides setzt nur
  `benutzer-anlegen.sh` (Schritt 11 „Lager“, nur bei wachem pve-big). Dann gelten Lager `I/lager`, Marke
  `.clip-lager-<name>` und Freigeben wie bei Florian; ohne Schalter genau Stufe 1.
- **Kopieren:** `clip-freund-lager@<name>` (Sandbox-Block plus genau eine Bindung `freunde/<name>` → `I/lager`, ohne
  Netz, höchstens 2 h) ruft `pipeline lager abgleich` – derselbe Code wie bei Florian: Datenbank sichern, jede Datei mit
  SHA-256 zurücklesen, Rohdaten nie überschreiben; danach gibt sein Puffer Rohvideos frei, die älter als 14 Tage und im
  Lager bestätigt sind (erster Lauf nur Probe). Vorher prüft er: `I/lager` ist NFS, Florians Marke und `freunde/` sind
  dort unsichtbar, seine Marke ist da – sonst nichts kopiert. Seine anderen Dienste sehen nur das leere `I/lager`.
- **Mitfahren:** `clip-lager-freunde.service` (root) startet zusammen mit Florians `clip-lager.service` und läuft
  `deploy/benutzer/lager-freunde.sh`: Halten-Marke „freunde“, warten bis Florians Abgleich fertig ist, prüfen ohne Wecken
  (NFS eingehängt, Port 2049, Florians Marke), dann je Freund mit Lager und laufendem scan-Timer nacheinander, neue
  Starts nur 10–18 Uhr, mit Herzschlag für clip-leerlauf; am Ende Marke lösen und
  `/var/lib/clip-pipeline/lager-freunde.json` schreiben. Schläft pve-big: nichts, die Freunde fahren beim nächsten Mal
  mit (höchstens 7 Tage, wenn Florian nichts Neues hat).
- **Nachsehen:** `bash deploy/benutzer/lager-freunde.sh --probe` (wer würde mitfahren), `journalctl -u
  clip-lager-freunde -n 50`, `bash deploy/benutzer/benutzer-befehl.sh <name> lager status`; `benutzer-pruefen.sh` zeigt
  Einhängepunkt, Rundgang und seinen letzten Lauf. `benutzer-stilllegen.sh` hält auch einen laufenden Lager-Lauf an.
- **Vor Ort einmal** (bei wachem pve-big, nach dem 10-Uhr-Abgleich): auf pve-big `exportfs -v` (all_squash,
  anonuid=101000?), im CT `findmnt -no SOURCE,FSTYPE,OPTIONS /srv/big/clips` (nfs4, soft; die Quelle muss vom CT aus
  erreichbar sein, am besten als IP), dann `benutzer-anlegen.sh <name>`, im Schritt „Lager“ j. Am Tag danach
  `journalctl -u clip-lager-freunde -n 50` und `benutzer-befehl.sh <name> lager status` – die erste Freigabe ist nur
  eine Probe.
- **Rückweg:** für alle `systemctl disable clip-lager-freunde.service`; je Freund das Rückweg-Skript in
  `/root/benutzer-lager/` (Schalter wie vorher). Seine Daten im Lager bleiben.
- **Bei Florian:** `lager.py`, `big.py` und `clip-lager.service` unverändert (Test mit Prüfsummen); ohne eingeschalteten
  Rundgang läuft sein Abgleich genau wie bisher. Neu sichtbar: der Ordner `freunde/` im Lager (auch über sein SMB
  `[clips]`) und an Tagen mit Freunden ein länger wacher pve-big (nur tagsüber).

## Morgenprüfung kennt die Freunde (Stufe 2, Schritt 6 – umgesetzt)
Florian bekommt Probleme der Freunde in seiner Morgenprüfung (11 Uhr, `puffer.py`, Thema „freunde“) – nur, wenn es das
Freunde-Volume als eigenes Dateisystem gibt; sonst ist die Morgenprüfung Zeichen für Zeichen wie vorher (Test gegen die
alte `puffer.py`). Annahmen M105, M131–M134.
- **Platz:** nur `statvfs` auf `/var/lib/clip-benutzer`, nie ein Blick hinein. Warnung unter 15 GB, Alarm unter 5 GB
  (`[puffer].freunde_warnung_frei_gb`/`freunde_alarm_frei_gb`) mit dem nächsten Schritt `pct resize 102 mp2 +50G`.
- **Lager:** aus `/var/lib/clip-pipeline/lager-freunde.json`. Der Rundgang schreibt dort jetzt auch, wer Lager hat, seit
  wann und wer stillgelegt ist (`mit_lager`). Je Freund mit Lager eine Zeile, wenn sein letzter Lauf nicht ging (einmal,
  solange er höchstens 24 h alt ist; Exit 3/4 zählt nicht) oder er seit 8 Tagen nicht ins Lager kam; nächster Schritt
  `benutzer-pruefen.sh <name>`. Ohne Rundgang-Datei weiß die Morgenprüfung nichts über die Lager der Freunde
  (`pipeline` darf ihre Konfig nicht lesen) – dann hilft nur `benutzer-pruefen.sh`.
- **Beim Freund:** nichts – seine Instanz prüft nie die anderen.

## Laufzeiten messen (Stufe 3, Schritt 1 – umgesetzt)
Jeder Rechenauftrag unter der gemeinsamen Sperre schreibt danach eine Zeile in die **eigene** Datenbank (`ereignisse`,
art `lauf`): was (der Befehl, z. B. `render` oder `sitzungen`, bzw. `lernbot-bau`, `lernbot-paket`,
`lernbot-kalibrieren`, `ki-note`, `clipbot-short`, `benutzer-einrichten`), wie lange er gewartet und gerechnet hat und
ob er ok, gesperrt oder mit Fehler endete; der Lern-Bot-Bau dazu Stimmung, Schnitt und Render. Das Render-Sidecar
`<video>.render.json` bekommt Rechenzeit, Rückfall VA-API → CPU, Eingabe-Größe und Länge.
- **Auswerten:** `pipeline laufzeiten [--tage 7]` – eine JSON-Zeile, nur lesen, keine Sperre, weckt nie: je Auftragsart
  Median, p90 und Maximum von Warten und Rechnen, gesperrte und fehlerhafte Läufe; Rendern je Encoder in Sekunden je
  Video-Sekunde (ältere Videos aus Dateizeiten); ✅ → Upload-Fassung; Abend → Video; Freigabe-Quote. Fehlt etwas, steht
  dort null. Für einen Freund: `bash deploy/benutzer/benutzer-befehl.sh <name> laufzeiten`.
- **Am Auftrag ändert sich nichts:** n8n-Vertrag, Exit-Codes, JSON-Zeile und offene Transaktionen bleiben; ein
  Schreibfehler steht nur im Log. Leere Läufe (unter 1 s) schreiben nichts, „gesperrt“ nur, wer mindestens 60 s warten
  darf. Annahmen M139–M142.

## Ausfallsicher (Stufe 3, Schritt 2 – umgesetzt)
Stürzt ein Schritt ab, fällt der Strom aus oder reißt die Verbindung, bleibt das Ergebnis richtig; der nächste Lauf
macht es sauber neu. Anders ist nur das Verhalten im Fehlerfall – Videos, Clips, Schnittlisten, n8n-Vertrag gleich.
- **ffmpeg stirbt mit:** `medien.fuehre_aus` startet jeden Befehl über `setpriv --pdeathsig KILL --` (einmal je Prozess
  geprüft; geht es nicht, wie bisher mit einer Logzeile). Stirbt der Python-Prozess (Speicher voll, `kill -9`, ein
  n8n-Schritt außerhalb von systemd), endet sein ffmpeg sofort – kein verwaistes Rendern ohne Sperre, kein zweiter
  Schreiber auf dieselbe Zwischendatei.
- **Fertige Dateien überstehen Stromausfall:** Entwürfe und Stems, Clips (auch Momente und Fails), Vorschauen, Shorts,
  das 2-Wochen-Video und seine Marke `<id>.json` kommen erst ganz auf die Platte (fsync), dann unter ihren Namen
  (`medien.uebernehmen`).
- **CPU-Rückfall beim Schneiden:** Streikt oder hängt VA-API in `medien.schneide`, schneidet libx264 einmal mit
  demselben crf nach (n8n-render, Nachschnitt, Fails, scan der Freunde).
- **Zeitgrenzen:** `clip-sitzungen` 2 h (das abgebrochene Abend-Video rendert der nächste Lauf auf der CPU nach), die
  Timer-Dienste der Freunde 1 h – kürzer als Florians 2 h Wartezeit. Das Update übernimmt beides nur in Dateien, die
  niemand angepasst hat (sonst „weicht vom Repo ab“).
- **Abnahme:** `tests/test_abnahme_stufe3.py` – SIGKILL der ganzen Prozessgruppe mitten im Abend-Video (Sperre frei,
  kein halbes Video, Nachholen auf der CPU, genau ein Video ±2 Bilder, dritter Lauf tut nichts), verwaistes ffmpeg nach
  höchstens 1 s weg (Haupt- und Arbeits-Thread), abgerissene Ausgabe bei `render` (Clips gespeichert, Wiederholung
  neu = 0, Dateien gleich). Annahmen M143–M146.

## Florian zuerst (Stufe 3, Schritt 3 – umgesetzt)
Warten Florian und ein Freund gleichzeitig auf die Rechen-Sperre, kommt Florian meist zuerst dran. Freund ist, wer die
Sperrdatei nur lesen darf (bei ihm schreibgeschützt eingebunden, also nicht fälschbar). Er wartet vor dem ersten Versuch
zufällig bis zu 1 s und fragt danach nur alle 4–6 s statt jede Sekunde – nie über seine Frist hinaus, an der Frist ein
letzter Versuch. Florians Schritte fragen wie bisher sofort und dann jede Sekunde. Ein laufender Auftrag wird nie
unterbrochen; keine Konfig, keine Datei, kein Dienst (`sperre.sperre`).
- **Wirkung** (der echte Code mit nachgebauter Uhr): Bei einer Übergabe ist ein Freund in 10 statt 50 % der Fälle vor
  Florian dran (drei Freunde: 27 statt 75 %). Die 1-s-Lücke zwischen zwei n8n-Schritten erwischt ein wartender Freund in
  20 statt 100 % (drei: 49 %); mit echten Prozessen 4 von 30 statt 10 von 10.
- **Preis:** Freunde kommen nach dem Freiwerden im Mittel gut 2 s später dran, bei freier Sperre 0,5 s – bei Aufträgen
  von Minuten egal. Harter Vortritt erst, wenn `pipeline laufzeiten` es verlangt (Florians n8n-Schritte warten im Median
  über 2 min oder sein Lern-Bot-Bau im p90 über 5 min). Annahme M147.

## Weitere Rechner (Stufe 3, Schritt 4 – nur Doku)
Der Mini bleibt der einzige Rechner, für Florian und jeden Freund. `docs/WORKER.md` beantwortet die sechs Fragen aus
Abschnitt D für heute – die Antwort ist immer „Mini“, weil das Übertragen je Short (15–53 s bei 50 Mbit/s) etwa so lange
dauert wie das Rendern (58–86 s, Container) –, nennt die Auslöser für einen zweiten Rechner und hält den Vertrag v1 für
Heimserver und Cloud fest: Eingaben mit Prüfsumme, Versuchsnummer gegen späte Ergebnisse, Prüfung am Mini vor der
Übernahme, eigener Zugang, nur die nötigen Daten, Cloud ab Werk aus mit Monatslimit, Pflicht-Tests. Im Code gibt es
davon nichts; `render-entwurf --final` bleibt als Vorläufer v0 aus (`docs/REGIE.md`). Annahmen M148–M152.

## Stufenbericht Stufe 3 (09.10.2026)
Abnahme „Ergebnisse bleiben korrekt bei Worker-Ausfall, Wiederholung oder Verbindungsabbruch; Laufzeiten werden
gemessen“: erfüllt für den einzigen Rechner, den Mini – Nachweis unten. Die echten Mini-Zahlen kommen erst vor Ort.

**Was wurde tatsächlich implementiert?**
- **Laufzeiten (Schritt 1):** Alle sieben Stellen, die die Rechen-Sperre nehmen, laufen über `laufzeiten.lauf` und
  schreiben je Auftrag eine Zeile in die eigene Datenbank (Warten, Rechnen, Ergebnis; beim Lern-Bot-Bau auch Stimmung,
  Schnitt, Rendern). Das Render-Sidecar merkt sich Rechenzeit, Rückfall, Eingabe-Größe und Länge.
  `pipeline laufzeiten [--tage 7]` wertet aus, ältere Videos aus Dateizeiten.
- **Ausfallsicher (Schritt 2):** ffmpeg stirbt mit seinem Aufrufer, fertige Dateien kommen per fsync auf die Platte,
  bevor sie ihren Namen bekommen (auch die Marke des 2-Wochen-Videos), Rückfall auf den Prozessor beim Schneiden,
  Zeitgrenzen (`clip-sitzungen` 2 h, Freundes-Timer 1 h statt 2 h).
- **Florian zuerst (Schritt 3):** weicher Vorrang in `sperre.sperre` – Freunde fragen alle 4–6 s statt jede Sekunde,
  mit 0–1 s Anlauf; Florian unverändert.
- **Vertrag für weitere Rechner (Schritt 4, nur Doku):** `docs/WORKER.md`, `render-entwurf --final` als Vorläufer v0,
  der falsche Satz „wiederholt n8n ihn“ im Update berichtigt.
- **Bewusst nicht gebaut:** Worker-Auswahl, Fern- und Cloud-Rechner, Cloud-Schalter, zentrale Warteschlange,
  Segment-Cache, harter Vortritt, Notbremse; der Nachhol-Timer für n8n-Matches wartet auf Florians Ja. Keine neue
  Tabelle, kein neuer Dienst, Timer, Port oder Paket; n8n-Vertrag, Sperrpfad, Videos und Bot-Texte bleiben gleich.

**Welche Funktionen wurden wiederverwendet?**
- die eine Rechen-Sperre aus Stufe 1 (`sperre.sperre`, `sperre.pfad`) – jetzt mit Messung und Vorrang, Datei und Pfad
  gleich;
- die Tabelle `ereignisse` und das Render-Sidecar `<video>.render.json` – keine Migration;
- `medien.fuehre_aus` mit dem Hänger-Wächter (180 s ohne Rechenzeit) – das Mitsterben sitzt an dieser einen Stelle;
- der Rückfall VA-API → Prozessor aus `entwurf.rendere`, jetzt auch in `medien.schneide`;
- `sitzung._nachholen` (Abend-Video bis 12 h auf dem Prozessor nachholen) fängt den neuen 2-h-Abbruch auf;
- die Update-Regel „nur unveränderte Dienst-Dateien übernehmen“ in `alles-aktualisieren.sh` – jetzt mit Test;
- Wiederholung und Idempotenz des Bestands: Timer, Merkliste, `scan --versuche 3`, `render` je Clip, Entwurf über seine
  Datei;
- für den Vertrag: Prüfung fremder Aufträge (`fuehre_final_aus`), Forced Command (`clip-big-steuer.sh`,
  `n8n-lauf.sh`), Lieferschein des Briefkastens, gepinnte Schlüssel (`big.ssh_befehl`).

**Was wurde praktisch geprüft?**
- **Worker-Ausfall** (`tests/test_abnahme_stufe3.py`, echtes ffmpeg): SIGKILL an die ganze Prozessgruppe mitten im
  Abend-Video → Sperre frei, kein halbes Video, der nächste Lauf holt auf dem Prozessor nach, genau ein Video ±2 Bilder,
  der dritte Lauf tut nichts. Verwaistes ffmpeg aus Haupt- und Arbeits-Thread nach höchstens 1 s weg (ohne setpriv
  lebte es weiter). Rückfall beim Schneiden (`test_medien`).
- **Wiederholung:** die vorhandenen Tests `test_sitzung`, `test_stufe1`, `test_scan_grenzen`, `test_upload_paket`; in
  der Abnahme tut der dritte Lauf nichts.
- **Verbindungsabbruch:** `render --session` mit abreißender Ausgabe → Clips gespeichert, die Wiederholung meldet
  neu = 0, alle Dateien gleich (SHA-256); beim Freund die Wiederaufnahme im Briefkasten (`test_briefkasten`, Stufe 2).
- **Laufzeiten** (`test_laufzeiten`): eine Zeile je Lauf, Exit 4 mit Zeile „gesperrt“, ein Schreibfehler ändert weder
  Exit-Code noch JSON-Zeile, die offene Transaktion eines Bots bleibt offen. Echter `render-entwurf`: Zeile 33,0 s =
  Sidecar 33,0 s = Dateizeiten 33,0 s.
- **Vorrang** (`test_sperre_gemeinsam`, echte flock-Sperre): Mit der alten `sperre.py` schlagen die neuen Tests fehl
  (Gegenprobe); mit echten Prozessen erwischt ein Freund die 1-s-Lücke zwischen zwei n8n-Schritten 4 von 30 statt
  10 von 10 Mal.
- **Zum Abschluss:** alle in Stufe 3 neuen oder berührten Testmodule und ihre Nachbarn – 47 Module, 845 Tests, keiner
  rot. 4 übersprungen, wie immer im Container: zwei Tests mit echtem Whisper und ein Leistungstest (nur mit Schalter),
  dazu der Briefkasten mit echtem sshd (braucht einen eigenen Namensraum). Die volle Suite läuft in der CI.
- **Prüfung danach** (ein Prüfer): zwei kleine Befunde, keiner blockierend – fsync unter Windows behoben (M153); die
  2-h-Grenze von `clip-sitzungen` bleibt gleich der Wartezeit, die Folge ist dokumentiert und per Test festgehalten
  (M154).
- **Nicht geprüft:** der Mini selbst (echte Renderzeiten, VA-API, setpriv im CT) – das zeigt `pipeline laufzeiten` vor
  Ort.

**Wie viel schneller oder besser ist das System nachweislich?**
Schneller wird nichts – ehrlich gesagt. Gemessen im Container (nur als Verhältnis, M152):
- **Kosten:** setpriv +1,7 ms je ffmpeg-Aufruf, fsync +9 ms (8 MB) bzw. +27 ms (25 MB) je Datei, die `lauf`-Zeile
  0,8–1,2 ms je Auftrag. Derselbe 720p-Entwurf vorher 69,7 s, nachher 70,7 s (Median aus je 3) – im Rauschen, die
  Läufe einer Gruppe streuen um 5 s.
- **Besser, belegt:**
  - kein verwaistes ffmpeg mehr: nach dem Tod seines Aufrufers nach höchstens 1 s weg. Vorher rechnete es ohne Sperre
    weiter, und ein Neuversuch konnte still eine kaputte Datei schreiben (richtige Länge, 11–12 Tsd. Dekodierfehler);
  - eine Panne der Grafikeinheit beim Schneiden kostet kein Match mehr;
  - ein Hänger im Abend-Video hält die Sperre höchstens 2 h (vorher ohne Grenze), einer in den Timern der Freunde
    höchstens 1 h (vorher 2 h = genau Florians Wartezeit);
  - Vorrang: Bei einer Übergabe ist ein Freund in 10 statt 50 % der Fälle vor Florian dran (drei Freunde: 27 statt
    75 %), die 1-s-Lücke zwischen zwei n8n-Schritten erwischt er in 20 statt 100 % (drei: 49 %). Preis: Ein Freund
    kommt nach dem Freiwerden im Mittel 2,6 statt 0,5 s später dran;
  - Laufzeiten sind sichtbar: Vorher stand im Repo keine einzige Renderzeit vom Mini, und Florians n8n-Schritte und
    jeder Exit 4 hinterließen auf dem Mini keine Spur.
- **War schon so, jetzt per Test belegt:** Nach einem Absturz mitten im Abend-Video kommt beim nächsten Lauf genau ein
  richtiges Video; eine abgerissene n8n-Verbindung verliert keinen Clip.
- **Stromausfall:** geprüft ist, dass erst nach dem fsync umbenannt wird (scheitert er, bleibt der Endname frei); einen
  echten Stromausfall haben wir nicht nachgestellt.

**Was ist die nächste sinnvolle Erweiterung?**
1. Messen vor Ort, ohne neuen Code: nach dem Update `pipeline laufzeiten --tage 30` (Vorher-Grundlage aus Dateizeiten),
   nach einer Woche mit dem ersten aktiven Freund `--tage 7`. Zielwerte: kein Exit 4 bei Florians n8n-Schritten, seine
   Wartezeit im p90 höchstens ein laufender Freund-Auftrag, keine Haltezeit über 1 h.
2. Daraus selbst entscheiden: harter Vortritt (Schwellen in M147), Short gleich in Upload-Qualität (ab etwa 30 %
   Freigabe, M151), ein zweiter Rechner nur bei einem Auslöser aus `docs/WORKER.md`.
3. Florians Ja oder Nein zum Nachhol-Timer für liegengebliebene n8n-Matches (M148).
4. Danach Stufe 4: Kampagnenlink je Instanz, neue Zielgrößen versioniert neben dem alten Score.

## Erfolg ehrlich messen (Stufe 4, Schritt 1 und 2 – umgesetzt)
- **`pipeline erfolg` (Schritt 1):** nur nachsehen – keine Sperre, weckt nie, über n8n nicht erreichbar; für einen
  Freund `bash deploy/benutzer/benutzer-befehl.sh <name> erfolg`. Drei Ziele getrennt: Zuschauer (die feste
  Wochen-Note), neue Follower, Besuche auf clip-battle.de; was fehlt, heißt „nicht gemessen“ mit Grund, nie 0. Neun
  feste Vergleiche (4 Aufbauten, Tempo, Zeitlupe, 3 Längen); „belegt“ erst ab 8 Videos je Seite und nur, wenn es sehr
  wahrscheinlich kein Zufall ist. Erklärung: `docs/PUBLIKUM.md`, Abschnitt 7.
- **Ehrliche Sätze (Schritt 2):** Im Sonntagsbericht steht die 📊-Zeile aus `pipeline erfolg` statt „👀 Bei den
  Zuschauern kommt gut an“, und „🎯 Wähle ich gerade öfter/seltener“ statt „👍 Kommt gut an / 👎 Kommt weniger an“
  (dieselbe Zahl); ein Fehler darin kostet den Bericht nie. Unter /experte: /lernstand „Tendenzen (nicht belegt)“,
  /publikum „Wochen-Note (fest)“ und „Lernwert (vorläufig)“. Für Florian: `docs/SO-GEHTS.md`, „Der Sonntagsbericht“.
- **Freunde:** dieselben Regeln. Bei ihnen holt niemand Zuschauerzahlen ab – `pipeline erfolg` sagt es, der
  Sonntagsbericht schweigt dazu. Annahmen M155–M174.

## Stufenbericht Stufe 4 (09.10.2026)
Abnahme „Das System zeigt belegbare Unterschiede zwischen Strategien, ohne fehlende Daten zu erfinden“: erfüllt im
Code, in Tests und Nachstellungen – Nachweis unten. Auf Florians echten Daten gibt es noch nichts zu belegen: Posts
entstehen im einfachen Modus erst seit 08.10. automatisch, und die ersten 5 je Plattform sind nur Vergleich.

**Was wurde tatsächlich implementiert?**
- **Schritt 1 – `pipeline erfolg`** (`erfolg.py`, neu): Einheiten (ein Short je Plattform, Fassungen einmal,
  Ausschlüsse mit Grund), drei Ziele mit Status (gemessen · zu wenig Vergleich · wartet · nicht gemessen mit Grund),
  Gesamt nur über die gemessenen Ziele (`[erfolg.gewichte]` 0,5/0,2/0,3), neun feste Vergleiche mit Mindestzahl und
  Zufallsschutz, Text ohne Fachbegriffe und eine JSON-Zeile mit Version und Gewichten; kaputte Gewichte → Exit 2.
- **Schritt 2 – ehrliche Sätze:** Sonntagsbericht mit 📊-Zeile statt „👀 …“ und „🎯 Wähle ich gerade öfter/seltener“
  statt „👍/👎 Kommt (weniger) gut an“; ein Fehler in `erfolg` geht nur ins Log. /lernstand „Tendenzen (nicht belegt)“;
  /publikum „Wochen-Note (fest)“ und „Lernwert (vorläufig)“, mit „nur gegen Startwerte“, solange es keinen früheren
  Post zum Vergleich gibt.
- **Bewusst nicht gebaut:** Zähler auf clip-battle.de, Video-Code in der Caption, Abruf der Zählerzahlen,
  YouTube-Anmeldung, Kanal-Follower (PR 3–7 des Plans – brauchen Florians Ja, teils eine Rechtsprüfung und neue
  Zugänge). Keine neue Tabelle, keine Migration, kein neuer Dienst, Timer, Port oder Paket. Die Lerner rechnen
  unverändert (M163); Captions, Videos, Knöpfe, 📋 Stand, Clip-Bot und n8n-Vertrag bleiben gleich.

**Welche Funktionen wurden wiederverwendet?**
- die feste Wochen-Note der Lernschleife (`publikum.bewerte_alle`, `score_fuer`, `score_teile` mit `messung_id`), ihre
  Gewichte (`publikum._gewichte`), `publikum.robust_z` (auch für Follower) und „Basis zu klein“;
- die eingefrorene Strategie je Video (`geschmack._wahl_aus`, `KNOEPFE`, `NAMEN`) und die Fassungs-Familie
  (`szenen._ersetzt`);
- ohne Netz: `publikum_adapter.tiktok_verbunden`, `publikum.post_plattformen`; Plattform-Namen aus
  `bot.aktionen.PLATTFORM_NAMEN`;
- das Muster von `pipeline laufzeiten` (nur lesen, keine Sperre, eine JSON-Zeile); der Wochenbericht
  (`geschmack.wochenbericht`, `lern_meldungen`) mit Rechnung und Schwelle der alten 👍/👎-Zeile;
  `lernbot_publikum.score_worte`/`score_text`; `basis_n` der vorläufigen Note für „nur gegen Startwerte“;
- für die Statistik nur die Standardbibliothek (`statistics.NormalDist`), kein neues Paket.

**Was wurde praktisch geprüft?**
- **Tests** (feste Zahlen, je unter 1 s): 48 Shorts mit „erzählt“ doppelt → „belegt besser“, nie die Gegenrichtung,
  und im Sonntagsbericht „📊 Belegt (TikTok, 43 Videos) …“; Bindung, Follower, Webseite „nicht gemessen“ mit Grund;
  KI-Noten ändern nichts; Zuschauer = Wochen-Note. 9 Shorts mit Crossposts und einer Fassung → kein Befund, „frühestens
  nach 13 weiteren“. Versuch E als Test → keine Behauptung, weder aus Zuschauer- noch aus KI-Noten (mit dem alten
  Bericht rot). Kaputte Gewichte → der Bericht kommt trotzdem (ohne die Absicherung rot). /publikum mit beiden Werten
  im Ende-zu-Ende-Test der Lernschleife.
- **Nachstellung des echten Sonntagsberichts** (Wegwerf-Skript mit dem echten Code: 200 Halbjahre à 26 Wochen × 3
  Shorts, Aufbau, Tempo und Zeitlupe zufällig, also ohne echten Unterschied; TikTok-Zahlen wie in der Planung,
  keine ✅/❌, keine KI; dieselben Daten vorher und nachher): vorher stand in 99,5 % der Halbjahre irgendwann eine
  Behauptung im Bericht (in 28 % der Wochen, meist „👎 Kommt weniger an: …“); nachher in 5,0 % (±1,5; 33 von 5200
  Wochen), jedes Mal ein falsches „📊 Belegt“ – im Rahmen der Simulation aus Schritt 1. „👍/👎/👀“ kamen nie mehr vor.
- **Simulation der Regel** (Schritt 1, echter Score-Code, 3 Shorts je Woche, wöchentlich nachgesehen): ohne echten
  Unterschied ein falsches „belegt“ in 3,6 % (±0,6) der Halbjahre und 6,3 % (±0,8) der Jahre, je 1000 Läufe.
- **Zum Abschluss** (nach Schritt 2): die berührten Testmodule und ihre Nachbarn – 20 Module, 409 Tests, keiner rot
  (`test_geschmack`, `test_erfolg`, `test_publikum*`, `test_ende_zu_ende_publikum`, `test_lernbot*`, `test_stufe1`,
  `test_autonom`, `test_n8n_einstieg`, `test_deploy_publikum`, dazu `test_keine_secrets`, `test_secrets_dateien`,
  `test_einstellungen`, `test_laufzeiten`, `test_instanz`, `test_isolation`). Die volle Suite läuft in der CI.
- **Nicht geprüft:** Florians echte Zahlen – `pipeline erfolg` nach dem Update; ehrlich erwartet „zu wenig Videos“
  bzw. „nicht gemessen“.

**Wie viel schneller oder besser ist das System nachweislich?**
Schneller wird nichts: `pipeline erfolg` braucht für 300 Posts 15 ms, der ganze Befehl 0,25 s; der Sonntagsbericht
rechnet einmal je Woche. Besser, belegt:
- **Kaum noch falsche Gewinner:** Ohne echten Unterschied stand vorher in 81,5–100 % der Halbjahre irgendwann eine
  Gewinner-Behauptung im Bericht (Leser-Simulation des Plans; Nachstellung oben: 99,5 %). Jetzt steht nur noch
  „belegt“ da – falsch in 3,6 % der Halbjahre bzw. 6,3 % der Jahre (Simulation, je 1000 Läufe; Nachstellung: 5,0 %).
- **Echte Unterschiede werden gefunden:** doppelte Reaktionen bei einem Aufbau in 100 % binnen eines Jahres belegt
  (im Median nach 16 Wochen), ×1,6 in 99 % (Woche 22), ×1,3 nur in 49 % (Woche 32) – je 200 Läufe, 3 Shorts je Woche.
- **Nichts erfunden:** Was nicht gemessen wird, steht mit Grund da (auf TikTok heute: wie lange geschaut wird, neue
  Follower, Besuche auf clip-battle.de); vorher stand „nicht gemessen“ nirgends.
- **Eine Zahl, ein Name:** Für dasselbe Video nannte die Tagesmeldung die Wochen-Note (im Versuch +0,6) und /publikum
  ohne Namen die vorläufige Note (−0,1); jetzt stehen beide beschriftet nebeneinander.
- **Ehrlich:** Belegte Unterschiede gibt es erst nach Wochen bis Monaten (ein erster Vergleich frühestens nach etwa 7
  bzw. 12 Wochen bei 3 Shorts je Woche, M168); Besuche auf clip-battle.de erst mit dem Zähler (Florians Ja). Die
  Tendenzen in /lernstand erscheinen so oft wie vorher (bei reinem Zufall nach 16 Videos in jedem zweiten Fall) – sie
  heißen jetzt nur so, wie sie sind. Die echte Fehlalarmquote liegt mit 6,3 % im Jahr über der Schätzung des Plans
  (etwa 3 %), weil jede Woche neu nachgesehen wird (M161).

**Was ist die nächste sinnvolle Erweiterung?**
1. Vor Ort, ohne neuen Code: nach dem Update einmal `pipeline erfolg` (für einen Freund `benutzer-befehl.sh <name>
   erfolg`) und sonntags den Bericht ansehen; 📋 Stand muss „Zuschauern (n Videos ausgewertet)“ zeigen (seit Stufe 5:
   „Zuschauern (läuft)“), sonst einmal /tiktok.
2. Florians Antworten auf die gebündelten Fragen (CLAUDE.md, „Offene Fragen“): Zähler auf clip-battle.de mit
   Rechtsprüfung → Video-Code und Abruf (PR 3–5); YouTube verbinden (PR 6 – wie lange geschaut wird und neue Abos je
   Video würden messbar); TikTok-Follower als Wochenwert (PR 7); Gewichte; Shorts je Woche.
3. Stufe 5 (Liga je Instanz-Datenbank): dieselbe Vergleichsfunktion entscheidet über Champion und Herausforderer; die
   Lerner bekommen reife Werte und die Gewichte; gegen das wöchentliche Nachsehen eine strengere Regel (z. B. „belegt
   in zwei Wochen nacheinander“).

## Florians Antworten (08.10.) und was daraus folgt
| Frage | Antwort | Folge |
|---|---|---|
| Wie kommen die Aufnahmen zum Mini? | „Muss das auf dem Mini sein, ich hab doch einen externen n8n-Server?!“ | Briefkasten auf dem vServer: kleiner Upload-Dienst, nicht n8n, keine Videos durch n8n. Der Mini holt über Tailscale ab und rechnet. Kommt mit Stufe 2. Keine Samba-Freigabe je Freund, keine eigene psd1 in Stufe 1 |
| Speicher, alte Rohvideos? | „Wie bei dir, mit Lager“ | Ab Stufe 2: Rohvideos ins Lager auf pve-big (eigener Unterordner je Freund), im Puffer nach 14 Tagen frei bei bestätigter Kopie (wie B5). In Stufe 1 wird bei Freunden nichts gelöscht |
| KI für Freunde? | „Eigener Claude-Zugang“ | Jeder Freund mit eigenem Claude-Abo; ohne Zugang bleibt die KI bei ihm aus |

Offen: freier Speicher auf dem vServer für den Briefkasten.

## Migration
- Keine Daten werden bewegt, das Schema bleibt. Ohne `CLIP_INSTANZ` und ohne `[sperre].datei` läuft alles wie bisher.
- Gibt es Freunde, sichert das Update zusätzlich jede Instanz-Datenbank, legt die Vorlagen und den Lager-Rundgang
  `clip-lager-freunde.service` nur auf die Platte (schaltet sie nie ein) und startet laufende Freundes-Bots neu
  (umgesetzt, Schritt 5). Florian spielt alles selbst ein; kein
  Auto-Update.

## Schnittstellen
- **n8n-Vertrag:** unverändert, nur für Florian. Freunde laufen ohne n8n über Timer.
- **Windows-Skript und Samba `[clips]`:** unverändert (das PC-Programm der Freunde ist ein eigenes Skript).

## Stufen
1. **Sichere Benutzertrennung** (dieser Plan): gemeinsame Sperre, Instanz-Modus, Freund-Pipeline ohne n8n,
   Isolationstests, Dienst-Vorlagen mit Sandbox, Freunde-Volume (ohne Samba je Freund, M12), Anlegen und Prüfen mit
   einem Befehl, Einladungslink, eigener Claude-Zugang per /claude (alles umgesetzt).
2. **Freunde liefern selbst:** Briefkasten auf dem vServer + kleines Programm für den PC, Lager je Freund mit
   Freigabe nach 14 Tagen, Meldungen an den Freund, Auto-Freigabe und 2-Wochen-Video ohne Clip-Bot. Gebaut:
   Briefkasten (Schritt 1), Abholen am Mini (Schritt 2), Abholen einschalten (Schritt 3), PC-Programm und /pc
   (Schritt 4), Lager für Freunde (Schritt 5), Morgenprüfung kennt die Freunde (Schritt 6); Löschen im Briefkasten erst
   nach Florians Ja.
3. **Hybrider Render-Manager:** Warteschlange vor der Sperre (Vorrang, Laufzeit-Protokoll), Auftrags-Vertrag für
   Rechen-Arbeiter. Fertig: Laufzeiten messen (Schritt 1), Ausfallsicher (Schritt 2), Florian zuerst (Schritt 3),
   Vertrag für weitere Rechner als Doku (Schritt 4, `docs/WORKER.md`); Fern- und Cloud-Rechner erst nach einem
   Messbefund und Florians Ja (Stufenbericht oben).
4. **Erfolg ehrlich messen:** neue Zielgrößen versioniert neben dem alten Score, belegter Strategievergleich. Fertig:
   `pipeline erfolg` (Schritt 1), ehrliche Sätze im Sonntagsbericht, in /lernstand und /publikum (Schritt 2);
   Kampagnenlink je Instanz (Zähler auf clip-battle.de) erst nach Florians Ja und Rechtsprüfung (Stufenbericht oben).
5. Liga je Instanz-Datenbank.
