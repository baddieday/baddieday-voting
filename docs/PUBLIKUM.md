# Lernschleife „Publikum“ – Bedienung und Installation (Stufe 1)

Bisher lernt der Regisseur nur aus deinem 👍/👎. Ob ein Short **auf TikTok** ankommt, erfährt er nie. Die
Lernschleife „Publikum“ (Spec `docs/superpowers/specs/2026-09-25-lernschleife-publikum-design.md`) schließt diese
Lücke: Jeder gepostete Short wird ein **Post** mit Nummer, du schickst dem Lern-Bot ab und zu einen Screenshot der
TikTok-Statistik, und nach einer Woche rechnet die Pipeline daraus einen **Publikums-Score**. Stufe 1 ist das
Fundament: Posts, Zahlen, Score, Anzeige. Gelernt wird aus dem Score ab Stufe 2.

```
👍 im Lern-Bot ─► 📦 Upload-Paket ─► du postest auf TikTok ─► /link ─► Post #17
                                                                        │
     Screenshot „#17“ (Claude liest, nur Leserecht) oder Hand-Eingabe ─► Messungen (Tag 3, Tag 7, …)
                                                                        │
     10:00 täglich: pipeline publikum bewerten ─► Score (einmal, ab Tag 7) ─► 📊 Meldung · /publikum
```

Alles läuft auf dem Mini, im Puffer-Betrieb; **nichts davon weckt pve-big**. Zeichen in dieser Anleitung:
🧪 = hier im Container mit künstlichem Material ausprobiert (gefälschtes Telegram, gefälschtes `claude`) ·
🏠 = am echten System noch nicht ausprobiert – das machst du bei der Installation.

## Begriffe

| Wort | Bedeutung |
|---|---|
| **Post** | ein veröffentlichtes Video auf einer Plattform (Tabelle `posts`): ein Regisseur-Entwurf („Entwurf 41“) oder ein Einzelclip aus dem Clip-Bot („Clip 88“). Je Video und Plattform genau einer. |
| **Post-Nummer** | `#17` – nennt der Bot nach `/link`, steht auch in `/publikum`. Die brauchst du für Screenshots. Nicht dasselbe wie die Entwurfs- oder Clip-Nummer. |
| **Messung** | die Zahlen eines Posts zu einem Zeitpunkt (Views, Likes, Kommentare, Shares, Saves, Ø Wiedergabe, „vollständig angesehen“). Zahlen wachsen, deshalb mehrere Messungen je Post. |
| **Publikums-Score** | eine Zahl je Post, etwa zwischen −2,5 und +2,5: Wie lag der Post im Vergleich zu **deinen** letzten 20 Posts? 0 = üblich, + = besser, − = schwächer. Einmal gesetzt, nie wieder geändert. |
| **Vergleichsbasis** | die zuletzt bewerteten Posts derselben Plattform, die **vor** diesem Post gepostet wurden (`[publikum].fenster`, 20). |
| **Ruhezeit** | `[telegram].leise_von` bis `leise_bis` (23:00–08:00): Publikums-Meldungen warten, bis sie vorbei ist. |

## So läuft es für dich

### 1. Vom Short zum Post
- **Lern-Bot (Entwürfe):** 👍 auf einen Short → ✅ fertig → unter dem Entwurf steht „📦 Upload-Paket“. Der Bot
  rendert die Upload-Fassung (1080×1920, auf dem Mini) und schickt sie als **Datei**, dazu die Caption zum Kopieren
  (mit der Quellenangabe der Musik – die muss in die Beschreibung) und ein Häkchen je Plattform. Nur 👍-Shorts
  bekommen ein Paket (kein Short ohne deine Freigabe; ein Zusammenschnitt 16:9 liefert kein TikTok-Signal).
- Du postest auf TikTok, dann: `/link 41 https://www.tiktok.com/@…/video/…` (auch `/link e41 …`). Der Bot legt den
  Post an und nennt dir die **Post-Nummer**. Ein falscher Link? Einfach nochmal `/link` – der neue ersetzt ihn.
  Das Häkchen ohne Link legt den Post auch an (Link später per `/link`).
- **Clip-Bot (Einzelclips):** wie bisher über 📦 `/paket`, das Häkchen TikTok bzw. `/link <clip-nr> <url>`. Neu:
  Dabei entsteht zusätzlich der Post. Zwei Bots, zwei Nummernkreise – im Clip-Bot ist 88 ein Clip, im Lern-Bot ist
  41 ein Entwurf.
- Posts gibt es nur für die Plattformen in `[publikum].plattformen` (Standard: nur TikTok). clip-battle.de ist
  nie ein Post – dort wird eingereicht, nicht geschaut.

### 2. Zahlen per Screenshot
- In der TikTok-App das Video öffnen → Statistik/Analysen → Screenshot. Dem **Lern-Bot** als **Foto** schicken,
  Bildunterschrift `#17`. Als Datei gehen JPG, PNG, WebP; HEIC vom iPhone nicht („Bitte als Foto schicken“).
- Ohne `#Nummer` fragt der Bot mit Knöpfen nach (die fünf jüngsten Posts ohne Messung der letzten 24 h). Das Bild
  wartet höchstens 10 min, dann ist es weg.
- Claude liest die Zahlen (ein `claude -p` je Bild, nur Leserecht, zählt gegen dein Abo). Passen sie zur letzten
  Messung, bestätigt der Bot. Sonst zeigt er, was er gelesen hat, und fragt „Stimmt das? ✅ / ✏️ von Hand“ – z. B.
  wenn Views gesunken wären (fast immer ein Lesefehler). **Nichts wird ungeprüft gespeichert.**
- Das Bild wird nach der Auswertung gelöscht – es gibt kein Bildarchiv, nur die Zahlen (und Claudes Antwort als
  Text in der Datenbank, zum Nachprüfen).
- **Wann?** Der Score nimmt die Messung, die **Tag 7** am nächsten liegt, und nur Messungen ab **Tag 3** (die
  ersten Tage verteilt TikTok noch). Gut: ein Screenshot um Tag 3 und einer um Tag 7.

### 3. Hand-Eingabe
- Geht immer, auch ohne Bild: `#17 1240 61 6.8 34` = Views, Likes, Ø Wiedergabe in Sekunden, „vollständig
  angesehen“ in Prozent. `–` (oder `-`) für unbekannt, Komma geht auch (`6,8`).
- Nach ✏️ oder wenn Claude nichts lesen konnte, reichen die vier Zahlen ohne `#17`: `1240 61 6.8 34`.
- Views und Likes sind ganze Zahlen – `1.240` (mit Tausenderpunkt) lehnt der Bot ab, statt 1,24 daraus zu machen.
- Kommentare, Shares und Saves kennt die Hand-Eingabe nicht; sie zählen dann als 0 (Vermerk „Engagement
  unvollständig“). Wo es geht, lieber den Screenshot.
- `[lernbot].screenshot_claude = false` schaltet Claude ganz ab – dann fragt der Bot gleich nach den Zahlen.

### 4. Der Publikums-Score – was die Zahl bedeutet
Drei Teile, jeweils gegen deinen **Median** der Vergleichsbasis (robust: ein Ausreißer-Post wirft ihn nicht um):
- **Wiedergabe** (Gewicht 0,5): welcher Anteil des Videos im Schnitt gesehen wurde – misst direkt Auswahl und
  Schnitt. Fehlt die Ø Wiedergabe, zählt „vollständig angesehen“.
- **Likes je View** (Engagement, 0,3): (Likes + 2 · Shares + Saves + Kommentare) / Views.
- **Views** (Reichweite, 0,2): als Logarithmus – 10 000 statt 1 000 ist ein ähnlicher Schritt wie 1 000 statt 100.

Ohne Wiedergabe werden Likes je View und Views auf 0,6/0,4 hochgerechnet („ohne Wiedergabe“). Solange weniger als
**5** bewertete Posts zum Vergleich da sind, ist der Score **0** mit Vermerk „Basis zu klein“ – aus dem Nichts wird
nichts gelernt. Der Score wird **einmal** gesetzt, sobald der Post 7 Tage alt ist, und danach nie überschrieben;
spätere Screenshots ändern ihn nicht mehr. Die Rechnung steht in `src/clip_pipeline/publikum.py` (Spec §6).

### 5. /publikum
Im Lern-Bot: `/publikum` (die letzten 10) oder `/publikum 20` (höchstens 30). Beispiel:
```
📊 Publikum · 12 Posts, 5 mit Score (die letzten 10, neueste zuerst)
#17 TikTok · Entwurf 41 · 4 Tage · 👁 1 240 ❤️ 61 ⏱ 6,8 s (Tag 4) · Score noch offen (ab 7 Tagen)
#16 TikTok · Clip 89 · 8 Tage · noch keine Zahlen – Screenshot mit #16 schicken · Score offen (braucht eine Messung ab Tag 3 mit Views)
#12 TikTok · Clip 88 · 9 Tage · 👁 5 000 ❤️ 300 ⏱ 12 s (Tag 7) · Score +0,8 (Wiedergabe über, Likes je View unter, Views über deinem Median)
🤖 Claude diese Woche: 3 Aufrufe
```
„(Tag 4)“ ist das Alter des Posts bei der letzten Messung. „Score kommt beim nächsten Lauf“ heißt: alles da, der
Timer war nur noch nicht dran. Die letzte Zeile zählt die Screenshot-Auswertungen seit Montag 00:00.

### 6. Meldungen und Ruhezeit
Nach dem täglichen Lauf (10:00) kommt höchstens **eine** Meldung am Tag, nur wenn es neue Scores gibt:
`📊 2 Posts bewertet: #17 +0,8 · #18 −0,3 – /publikum`. Solange der Score 0 wegen „Basis zu klein“ ist, erklärt
eine zweite Zeile das. Holt der Timer einen verpassten Lauf nachts nach, hält der Lern-Bot die Meldung bis zum
Ende der Ruhezeit (08:00) zurück; andere Lern-Bot-Meldungen (Abendstand, Fehler) kommen wie bisher sofort.

## Befehle

| Wo | Befehl | Was |
|---|---|---|
| CT | `pipeline publikum bewerten` | Scores aller fälligen Posts setzen (Timer `clip-publikum`, 10:00); weckt nie, keine Pipeline-Sperre |
| Lern-Bot | `/publikum [anzahl]` | letzte Posts mit Zahlen und Score |
| Lern-Bot | `/link <entwurf> <url>` | Post zu einem Entwurf anlegen bzw. Link korrigieren |
| Lern-Bot | Foto mit `#17` · Text `#17 1240 61 6.8 34` | Zahlen per Screenshot bzw. von Hand |
| Lern-Bot | `/hilfe` | alles oben in Kurzform |
| Clip-Bot | Häkchen TikTok · `/link <clip> <url>` | wie bisher – legt zusätzlich den Post an |

`pipeline publikum bewerten` hält den Vertrag aller Befehle ein: Logs auf stderr, letzte Zeile auf stdout = eine
JSON-Zeile, z. B. `{"bewertet": 1, "ohne_messung": 0, "noch_zu_jung": 3, "fehler": 0, "posts": [{"id": 17,
"score": 0.0}], "meldung": true}`. Exit 0 ok (auch: nichts fällig) · 1 mindestens ein Post nicht bewertbar (die
anderen sind trotzdem bewertet) · 2 Konfiguration kaputt. Beliebig oft aufrufbar – ein zweiter Lauf ändert nichts.

## Konfig-Schlüssel

Standardwerte stehen in `config/pipeline.toml`; ändern nur in `config/lokal.toml` (einzelne Werte überschreiben,
Beispiel in `config/lokal.beispiel.toml`). `pipeline …` liest die Konfig bei jedem Aufruf, die Bots beim Start
(nach einer Änderung `systemctl restart clip-lernbot` bzw. `clip-bot`).

| Schlüssel | Standard | Bedeutung |
|---|---|---|
| `[publikum].plattformen` | `["tiktok"]` | für diese Plattformen entstehen Posts; `["tiktok", "youtube"]` schaltet YouTube zu, `[]` die Posts ab. clip-battle.de wird ignoriert (mit Warnung im Log). |
| `[publikum].alter_tage` | `7` | ab diesem Alter wird bewertet, mit der Messung, die diesem Alter am nächsten liegt |
| `[publikum].mindest_alter_tage` | `3` | jüngere Messungen zählen nicht für den Score |
| `[publikum].fenster` | `20` | so viele zuletzt bewertete Posts bilden die Vergleichsbasis |
| `[publikum].paar_abstand` | `0.5` | (ab Stufe 2) Mindestabstand zweier Scores, damit daraus ein Lern-Paar wird |
| `[publikum].max_paare` | `200` | (ab Stufe 2) höchstens so viele jüngste Publikums-Paare |
| `[publikum].upload_ordner` | `"export"` | Ordner der Upload-Fassungen im Puffer (`/srv/puffer/export/<name>/`) |
| `[publikum.gewichte].wiedergabe` | `0.5` | Gewicht der Wiedergabe im Score |
| `[publikum.gewichte].engagement` | `0.3` | Gewicht von Likes je View |
| `[publikum.gewichte].reichweite` | `0.2` | Gewicht der Views |
| `[lernbot].screenshot_claude` | `true` | Screenshots per `claude -p` lesen; `false` = gleich Hand-Eingabe |
| `[lernbot].screenshot_prompt` | `"templates/screenshot-prompt.txt"` | fester Auftrag an Claude (was lesen, wie antworten) |
| `[lernbot].screenshot_timeout_s` | `120` | danach wird `claude` abgebrochen → Hand-Eingabe |
| `[decide].programm` | `"claude"` | Pfad zu `claude` – im Dienst den **vollen Pfad** eintragen (systemd kennt `~/.local/bin` nicht) |
| `[telegram].leise_von` / `leise_bis` | `"23:00"` / `"08:00"` | Ruhezeit: Publikums-Meldungen warten; `leise_von = ""` schaltet sie ab |
| `[vorschau].max_mb` | `48` | Obergrenze für die Upload-Fassung (Telegram-Bots dürfen höchstens 50 MB senden) |

Die Uhrzeit des täglichen Laufs steht **nur** im Timer (`deploy/systemd/clip-publikum.timer`, 10:00) – nicht in
der Konfig (eine Wahrheit, wie beim Abgleich).

## Installation

🏠 Alles in diesem Abschnitt machst du selbst, im CT `clips` als root. Hier im Container ist nichts davon gelaufen –
es gibt hier weder systemd-Dienste noch einen claude-Login für `pipeline`. Geprüft ist nur: die Unit-Dateien und
das Drop-in lassen sich von `systemd-analyze verify` lesen (🧪).

**Bevor du anfängst:**
- Der Sprint „Lernschleife Publikum“ ist in `main` (Draft-PR gemergt), und der Puffer-Betrieb aus
  `docs/PUFFER.md` läuft (`[lager].wurzel` gesetzt) – die Upload-Fassung wird nur im Puffer gebaut.
- Keine neuen Pakete, keine neuen Secrets (`.env` bleibt, wie sie ist).
- Nicht während eines Spielabends (Bots werden kurz neu gestartet).
- **Aus welchem Checkout läuft der Lern-Bot?** `systemctl cat clip-lernbot | grep -E 'WorkingDirectory|ExecStart'`.
  Laut Sprint-Log läuft er aus `/opt/clip-regie` (die Unit im Repo sagt `/opt/clip-pipeline`). Dann gilt alles
  unten, was den Lern-Bot betrifft, für `/opt/clip-regie`: dort auf main bringen (P1) und dort die `lokal.toml`
  ändern (P2, Abnahme). Datenbank und `.env` sind für beide dieselben.

| Schritt | Freigabe nötig? | Rückweg |
|---|---|---|
| P1 Code einspielen | **ja** – Produktion ändern, Datenbank bekommt neue Tabellen | alten Stand auschecken |
| P2 claude im Lern-Bot | **ja** – Dienst darf ins Home von `pipeline` schreiben | Drop-in entfernen |
| P3 Timer | **ja** – neuer Timer | Timer aus |

### P1 · Code einspielen und Datenbank sichern

**Was:** `/opt/clip-pipeline` auf den neuen Stand bringen, vorher Stand und Datenbank sichern.
**Warum:** Befehl, Bot-Funktionen und Tabellen stecken im Code. Die Datenbank bekommt beim ersten Verbinden
**neue** Tabellen (`posts`, `publikum_messungen`, `rezept_stand`, `hypothesen`, `erwartungen`) und drei neue
Spalten (`clips.mic_stand`, `entwuerfe.rezept`, `entwuerfe.upload_pfad`). Bestehende Tabellen, Spalten und
CHECK-Bedingungen bleiben, wie sie sind.
**Freigabe nötig?** Ja – Produktion ändern.

```bash
# im CT als root
cd /opt/clip-pipeline
[ -e /var/lib/clip-pipeline/vor-publikum.sha ] || sudo -u pipeline git rev-parse HEAD | tee /var/lib/clip-pipeline/vor-publikum.sha
[ -e /var/lib/clip-pipeline/vor-publikum.db ] || sudo -u pipeline sqlite3 /var/lib/clip-pipeline/pipeline.db ".backup /var/lib/clip-pipeline/vor-publikum.db"
sudo -u pipeline git fetch -q origin && sudo -u pipeline git log --oneline HEAD..origin/main   # was alles mitkommt
sudo -u pipeline git pull --ff-only
sudo -u pipeline .venv/bin/pip install -q -e '.[whisper]'   # keine neuen Pakete – hält nur die Installation aktuell
systemctl restart clip-bot
# nur falls /opt/clip-regie existiert (Lern-Bot, wie in docs/PUFFER.md R3) – neu gestartet wird er in P2:
[ -d /opt/clip-regie ] && sudo -u pipeline git -C /opt/clip-regie fetch -q origin \
  && sudo -u pipeline git -C /opt/clip-regie checkout -q --detach origin/main
sudo -u pipeline .venv/bin/pipeline status                   # verbindet einmal: legt die neuen Tabellen an
sudo -u pipeline sqlite3 /var/lib/clip-pipeline/pipeline.db "SELECT COUNT(*) FROM posts"   # 0 – Tabelle ist da
```
Der Lern-Bot bekommt den neuen Stand in P2 (dort wird er ohnehin neu gestartet).
**Rückweg:** `sudo -u pipeline git checkout -q "$(cat /var/lib/clip-pipeline/vor-publikum.sha)"` (ebenso in
`/opt/clip-regie`, falls umgestellt – `git -C /opt/clip-regie reflog` zeigt den alten Stand), dann
`systemctl restart clip-bot clip-lernbot`. Die neuen Tabellen stören den alten Code nicht. Im Notfall
`vor-publikum.db` zurückkopieren – nur bei gestoppten Bots und Timern; alles seit P1 ist dann weg.
**Was du lernst:** `[ -e datei ] || befehl` (ein Wiederholen überschreibt die Sicherung nicht), `sqlite3 .backup`
(stimmige Kopie im laufenden Betrieb), Migration beim Verbinden (`CREATE TABLE IF NOT EXISTS`, `ALTER TABLE … ADD
COLUMN` nur, wenn die Spalte fehlt – deshalb beliebig oft ausführbar).

### P2 · claude im Lern-Bot-Dienst

**Was:** Erst ausprobieren, ob `claude` unter denselben Schutzregeln läuft wie der Dienst; dann das Drop-in
`deploy/systemd/clip-lernbot.service.d/claude.conf` einspielen und den vollen Pfad zu claude eintragen.
**Warum:** `clip-lernbot.service` hat `ProtectHome=true` – der Dienst sieht `/home` gar nicht, claude und seine
Anmeldung liegen aber in `/home/pipeline`. Das Drop-in macht `/home` lesbar und nur `/home/pipeline` beschreibbar,
ohne die Haupt-Unit zu ändern. `--no-session-persistence` verhindert, dass claude jeden Screenshot in seinem
Verlauf unter `~/.claude/projects/` aufhebt (sonst entstünde ein Bildarchiv durch die Hintertür) – deshalb muss die
installierte claude-Version den Schalter kennen.
**Freigabe nötig?** Ja – der Lern-Bot darf danach ins Home von `pipeline` schreiben (Rückfrage 2 im Plan: lieber ein
eigenes `CLAUDE_CONFIG_DIR` unter `/var/lib/clip-pipeline`? Dann einmal neu anmelden).

```bash
# im CT als root
cd /opt/clip-pipeline
sudo -u pipeline /home/pipeline/.local/bin/claude --version
sudo -u pipeline /home/pipeline/.local/bin/claude --help | grep no-session-persistence   # muss eine Zeile zeigen
# Probe mit denselben Schutzregeln wie das Drop-in – Antwort „ok“ (oder ähnlich) erwartet:
sudo systemd-run --uid=pipeline -p ProtectHome=read-only -p ReadWritePaths=/home/pipeline --pty /home/pipeline/.local/bin/claude -p --no-session-persistence "sag ok"
mkdir -p /etc/systemd/system/clip-lernbot.service.d
cp deploy/systemd/clip-lernbot.service.d/claude.conf /etc/systemd/system/clip-lernbot.service.d/
# lokal.toml des Checkouts, aus dem der Lern-Bot läuft (siehe „Bevor du anfängst“; hier /opt/clip-pipeline):
[ -e config/lokal.toml.vor-publikum ] || cp -a config/lokal.toml config/lokal.toml.vor-publikum
nano config/lokal.toml                     # unter [decide]: programm = "/home/pipeline/.local/bin/claude"
systemctl daemon-reload
systemctl restart clip-lernbot
systemctl cat clip-lernbot | tail -4       # das Drop-in steht unter der Haupt-Unit
journalctl -u clip-lernbot -n 20           # „Lern-Bot läuft.“
```
Gibt es `[decide]` in `lokal.toml` schon, die Zeile dort ergänzen (ein Abschnitt darf nur einmal vorkommen).
Findet `grep` den Schalter nicht: erst claude aktualisieren (`sudo -u pipeline /home/pipeline/.local/bin/claude
update`) – bis dahin `[lernbot].screenshot_claude = false`, dann gibt es nur die Hand-Eingabe.
**Prüfen:** Im Lern-Bot `/hilfe` – unten steht der Block „📊 Publikum“; `/publikum` antwortet „Noch keine Posts …“.
Den ersten echten Screenshot prüfst du in der Abnahme.
**Rückweg:** `rm /etc/systemd/system/clip-lernbot.service.d/claude.conf`, `lokal.toml.vor-publikum` zurück nach
`lokal.toml` kopieren (oder `[lernbot].screenshot_claude = false` eintragen), `systemctl daemon-reload`,
`systemctl restart clip-lernbot`.
**Was du lernst:** Drop-ins (`<dienst>.service.d/*.conf` ergänzt eine Unit, `systemctl cat` zeigt beides),
`ProtectHome=true` vs. `read-only`, das „-“ vor einem Pfad (fehlt er, startet der Dienst trotzdem), mit
`systemd-run -p …` Schutzregeln ausprobieren, bevor ein Dienst davon abhängt, und warum Dienste `~/.local/bin`
nicht im PATH haben.

### P3 · Timer clip-publikum

**Was:** `clip-publikum.timer` einschalten – jeden Tag um 10:00 `pipeline publikum bewerten`.
**Warum:** Der Score wird einmal je Post gesetzt, sobald er 7 Tage alt ist. Ein täglicher Lauf reicht; mehrere
schaden nicht (nichts wird überschrieben, je Tag höchstens eine Meldung).
**Freigabe nötig?** Ja – neuer Timer. Der Lauf ist reine Datenbank-Arbeit, weckt nie und braucht keine Sperre.

```bash
# im CT als root
cd /opt/clip-pipeline
cp deploy/systemd/clip-publikum.service deploy/systemd/clip-publikum.timer /etc/systemd/system/
systemctl daemon-reload
sudo -u pipeline /opt/clip-pipeline/.venv/bin/pipeline publikum bewerten; echo "Exit $?"   # einmal von Hand: Exit 0
systemctl enable --now clip-publikum.timer
systemctl list-timers 'clip-*'
```
**Prüfen:** `list-timers` zeigt clip-publikum um 10:00 (plus bis zu 10 min Zufall); `journalctl -u clip-publikum`
nach dem ersten Lauf.
**Rückweg:** `systemctl disable --now clip-publikum.timer` – Posts und Messungen bleiben, nur der Score kommt
nicht mehr von selbst (von Hand geht er weiter).
**Was du lernst:** `Type=oneshot` (ein Lauf, dann fertig), `Persistent=true` (verpassten Lauf nachholen),
`RandomizedDelaySec` (nicht auf die Sekunde genau), und warum Exit 1 hier rot sein soll: dann steht im Journal,
welcher Post nicht bewertet werden konnte.

## Abnahme (Stufe 1)

„Fertig, wenn ein echter TikTok-Post per Screenshot gemessen und bewertet ist.“ Damit du nicht 7 Tage warten
musst, bewertet die Pipeline für die Abnahme schon ab Tag 3:
1. In `config/lokal.toml` (Beispiel in `config/lokal.beispiel.toml`):
   ```toml
   [publikum]
   alter_tage = 3
   ```
   dann `systemctl restart clip-lernbot` (für die Anzeige in `/publikum`).
2. Einen 👍-Short über 📦 posten, `/link <entwurf> <TikTok-Link>` → der Bot nennt die Post-Nummer, z. B. `#1`.
3. Ab Tag 3: Screenshot der Statistik mit `#1` an den Lern-Bot → der Bot bestätigt die gelesenen Zahlen.
4. Nächster Lauf (10:00 oder von Hand `pipeline publikum bewerten`) → JSON `"bewertet": 1`; Meldung
   `📊 1 Post bewertet: #1 0 – /publikum` mit der Zeile „Score 0 = noch zu wenige bewertete Posts …“; in
   `/publikum`: `Score 0 (Basis zu klein)`.

**Erwartet:** `bewertet_utc` gesetzt, **Score 0**, Vermerk **„Basis zu klein“** – unter 5 bewerteten Posts gibt es
keinen Score ≠ 0. Danach `alter_tage = 3` **wieder entfernen**: Scores werden nie überschrieben – Posts, die in
dieser Probezeit bewertet werden, behalten ihre Tag-3-Messung für immer.

## Im Alltag

- **Rhythmus:** posten → `/link` → Screenshot um Tag 3 und um Tag 7 → um 10:00 bewertet die Pipeline, was 7 Tage
  alt ist → Meldung im Lern-Bot.
- **Nachsehen:** `/publikum` im Lern-Bot. Genauer, im CT:
  `sudo -u pipeline sqlite3 /var/lib/clip-pipeline/pipeline.db "SELECT id, ziel, score, score_teile FROM posts ORDER BY id DESC LIMIT 5"`
  – `score_teile` enthält alle Zwischenwerte (r, e, v, z-Werte, Median und MAD der Basis, welche Messung).
- **Kosten:** ein `claude -p` je Screenshot; die Wochenzahl steht in der letzten Zeile von `/publikum`.
- **Nichts wird gelöscht:** Posts und Messungen bleiben; nur die Screenshots selbst verschwinden nach der Auswertung.

## Was tun, wenn …

- **… Claude die Zahlen nicht lesen konnte?** Der Bot fragt dann gleich nach der Hand-Eingabe – die vier Zahlen
  schicken. Passiert es immer:
  P2 prüfen (`journalctl -u clip-lernbot -n 50`, Probe mit `systemd-run`), claude-Anmeldung abgelaufen? Übergangsweise
  `[lernbot].screenshot_claude = false`.
- **… der Bot „Views gesunken“ o. ä. fragt?** Stimmen die gelesenen Zahlen (TikTok korrigiert manchmal nach
  unten), ✅ tippen; sonst ✏️ und von Hand.
- **… `/publikum` „noch keine Zahlen“ oder „braucht eine Messung ab Tag 3“ zeigt?** Einen Screenshot mit der
  genannten `#Nummer` schicken. Ohne Messung ab Tag 3 bleibt der Post unbewertet – auch nach Wochen noch nachholbar.
- **… ich die Post-Nummer vergessen habe?** `/publikum` – oder den Screenshot ohne Nummer schicken und den Post per
  Knopf wählen.
- **… der Link falsch war?** Nochmal `/link <nr> <richtiger Link>` – der neue ersetzt den alten.
- **… `clip-publikum` rot ist (Exit 1)?** `journalctl -u clip-publikum -n 50` zeigt „Post #17 nicht bewertet
  (Fehlerart: …)“. Die anderen Posts sind trotzdem bewertet; der Post wird beim nächsten Lauf wieder versucht.
  Meist ist ein älterer Post in der Vergleichsbasis beschädigt (`score_teile`) – melde dich, bevor du in der
  Datenbank etwas änderst.
- **… Exit 2 kommt?** Ein `[publikum]`-Schlüssel fehlt oder ist falsch geschrieben (meist in `lokal.toml`) – die
  JSON-Zeile nennt ihn.
- **… die Meldung nachts nicht kommt?** Absicht: Ruhezeit bis 08:00, dann kommt sie.
- **… das Upload-Paket nicht gebaut werden kann, weil Moment-Dateien fehlen?** Der Puffer hält Rohvideos 14 Tage
  (`[puffer].rohdaten_tage`);
  ältere Entwürfe lassen sich nicht mehr in voller Größe bauen. pve-big wird dafür nicht geweckt.
- **… ein Score seltsam aussieht?** `score_teile` ansehen (siehe „Im Alltag“). Unter 5 Vergleichsposts ist er
  immer 0; „ohne Wiedergabe“ heißt, im Screenshot fehlte die Ø Wiedergabe.

## Dateien

| Datei | Wofür |
|---|---|
| `src/clip_pipeline/publikum.py` · `publikum.sql` | Posts, Messungen, Plausibilität, Score |
| `src/clip_pipeline/lernbot_zahlen.py` · `screenshot.py` · `claude_aufruf.py` | Screenshot und Hand-Eingabe im Lern-Bot |
| `src/clip_pipeline/lernbot_paket.py` | 📦 Upload-Paket, Häkchen, `/link` im Lern-Bot |
| `src/clip_pipeline/lernbot_publikum.py` | `/publikum`, Meldung nach dem Bewerten, Ruhezeit |
| `src/clip_pipeline/schemas/publikum.schema.json` | was Claude antworten darf |
| `templates/screenshot-prompt.txt` | fester Auftrag an Claude |
| `deploy/systemd/clip-publikum.service` · `deploy/systemd/clip-publikum.timer` | täglicher Lauf 10:00 (P3) |
| `deploy/systemd/clip-lernbot.service.d/claude.conf` | claude im Lern-Bot-Dienst (P2) |
| `config/pipeline.toml` · `config/lokal.beispiel.toml` | Standardwerte · Beispiel für `lokal.toml` (Abnahme, claude-Pfad) |
