# Mehrbenutzer – eine Instanz je Freund (Entscheidung M1, 08.10.2026)

Ziel Stufe 1: Ein Freund bekommt auf dem Mini seine eigene, vollständig getrennte Pipeline. Zwei Benutzer arbeiten
unabhängig und ohne Zugriff aufeinander. Florian merkt nichts. Annahmen M2–M83: `docs/ENTSCHEIDUNGEN.md`,
„Mehrbenutzer (Clip-Pipeline 4.0)“. Stand: Schritt 1 bis 9 sind umgesetzt (eine Rechen-Sperre, Instanz-Modus,
Freund-Pipeline ohne n8n, Trennung Ende-zu-Ende geprüft, Dienst-Vorlagen mit Sandbox, Speicher für Freunde, Freund
anlegen und prüfen mit einem Befehl, Einladungslink statt Telegram-Zahl, eigener Claude-Zugang per /claude). Seite für
Freunde: `docs/FREUNDE.md`.

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
- **Gemeinsam:** nur Florians Sperrdatei `/var/lib/clip-pipeline/pipeline.lock`, dazu Prozessor, Grafikchip und Netz.

## Ordner (I = `/var/lib/clip-benutzer/<name>`)
| Pfad | Rechte | Inhalt |
|---|---|---|
| `/var/lib/clip-benutzer` | root 0711 | – |
| I | `root:clip-<name>` 0750 | `instanz.toml` und `.env` (beide `root:clip-<name>` 0640), Marke `.clip-benutzer` |
| I/db | 0700 | `pipeline.db`, `publikum-oauth.json`, `mikro.anstoss`, `big-zustand`, `kopplung.json` (0600) |
| I/daten (= Puffer) | 0700 | `.clip-speicher`, `.clip-puffer`, `eingang/`, `replays/`, `sessions/`, `sitzungen/`, `export/` |
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
  (nur Schlüssel, die die Repo-Konfig dort kennt), `[sperre]` `datei`/`warten_s`, `[instanz]` `claude`. Alles andere
  (Datenbank, Lager, pve-big, Pfade …) ist ein Konfig-Fehler.
- **Erzwungen:** `I/db/pipeline.db`, Puffer `I/daten`, `I/regie`, `I/musik`, `I/material`, `I/sfx`, Zustand von
  pve-big in `I/db`; kein Host, keine MAC, kein SSH (auch kein Schlüssel); Freigeben im Puffer und Aufräumen aus;
  Lager-Pfad `I/kein-lager` (darf es nicht geben – jeder Lager-Zugriff scheitert sicher, Puffer-Betrieb ohne Lager).
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
  Fehler (4 = Sperre belegt), nach 2 h ohne Ende abgebrochen – dann ist die gemeinsame Sperre wieder frei.
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
- **Im Anlege-Skript** (Schritt 8 von 10): zeigt dir den Link (nur den dieses Laufs), wartet, liest danach als root
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
- Gibt es Freunde, sichert das Update zusätzlich jede Instanz-Datenbank, legt die Vorlagen nur auf die Platte (schaltet
  sie nie ein) und startet laufende Freundes-Bots neu (umgesetzt, Schritt 5). Florian spielt alles selbst ein; kein
  Auto-Update.

## Schnittstellen
- **n8n-Vertrag:** unverändert, nur für Florian. Freunde laufen ohne n8n über Timer.
- **Windows-Skript und Samba `[clips]`:** unverändert.

## Stufen
1. **Sichere Benutzertrennung** (dieser Plan): gemeinsame Sperre, Instanz-Modus, Freund-Pipeline ohne n8n,
   Isolationstests, Dienst-Vorlagen mit Sandbox, Freunde-Volume (ohne Samba je Freund, M12), Anlegen und Prüfen mit
   einem Befehl, Einladungslink, eigener Claude-Zugang per /claude (alles umgesetzt).
2. **Freunde liefern selbst:** Briefkasten auf dem vServer + kleines Programm für den PC, Lager je Freund mit
   Freigabe nach 14 Tagen, Meldungen an den Freund, Auto-Freigabe und 2-Wochen-Video ohne Clip-Bot.
3. Warteschlange vor der Sperre (Vorrang, Laufzeit-Protokoll), Auftrags-Vertrag für Rechen-Arbeiter.
4. Kampagnenlink je Instanz; neue Zielgrößen versioniert neben dem alten Score.
5. Liga je Instanz-Datenbank.
