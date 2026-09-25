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
  rendert die Upload-Fassung (1080×1920, auf dem Mini – aus derselben Schnittliste wie der Entwurf, also mit
  denselben Effekten des Regisseurs 2.0; Kill-Titel und Zähler bleiben im unscharfen Rand, nie im Spielbild)
  und schickt sie als **Datei**, dazu die Caption zum Kopieren
  (mit der Quellenangabe der Musik – die muss in die Beschreibung) und ein Häkchen je Plattform. Nur 👍-Shorts
  bekommen ein Paket (kein Short ohne deine Freigabe; ein Zusammenschnitt 16:9 liefert kein TikTok-Signal).
- Du postest auf TikTok, dann: `/link 41 https://www.tiktok.com/@…/video/…` (auch `/link e41 …`). Der Bot legt den
  Post an und nennt dir die **Post-Nummer**. Ein falscher Link? Einfach nochmal `/link` – der neue ersetzt ihn.
  Das Häkchen ohne Link legt den Post auch an (Link später per `/link`).
- **Clip-Bot (Einzelclips):** wie bisher über 📦 `/paket`, das Häkchen TikTok bzw. `/link <clip-nr> <url>`. Neu:
  Dabei entsteht zusätzlich der Post. Häkchen, Link und Post gehören zusammen: Scheitert der Post (sehr selten, z. B.
  beschädigte Clip-Daten), bleibt auch das Häkchen weg und der Bot nennt den Grund (Annahme A35; Ausweg unter
  „Was tun, wenn …“). Zwei Bots, zwei Nummernkreise – im Clip-Bot ist 88 ein Clip, im Lern-Bot ist 41 ein Entwurf.
- Posts gibt es nur für die Plattformen in `[publikum].plattformen` (Standard: nur TikTok). clip-battle.de ist
  nie ein Post – dort wird eingereicht, nicht geschaut.

### 2. Zahlen per Screenshot
- In der TikTok-App das Video öffnen → Statistik/Analysen → Screenshot. Dem **Lern-Bot** als **Foto** schicken,
  Bildunterschrift `#17`. Als Datei gehen JPG, PNG, WebP; HEIC vom iPhone nicht („Bitte als Foto schicken“).
- Ohne `#Nummer` fragt der Bot mit Knöpfen nach (die fünf jüngsten Posts ohne Messung der letzten 24 h). Das Bild
  wartet höchstens 10 min, dann ist es weg. Es gelten nur die Knöpfe der **jüngsten** Frage – ein Knopf aus einer
  älteren Nachricht antwortet „Schon erledigt.“ und ordnet nichts zu.
- Der Bot wartet immer auf höchstens **eine** Sache. Schickst du ein neues Foto oder einen neuen `#…`-Text, während
  er noch auf etwas anderes wartet, sagt er, was dabei verworfen wird: „🗑 Vorheriges Bild verworfen.“, „🗑 Rückfrage
  zu #17 verworfen – diese Zahlen sind NICHT gespeichert …“ oder „🗑 Hand-Eingabe für #17 abgebrochen …“. Geht es
  um denselben Post, ist das eine Korrektur – dann kommt kein Hinweis.
- Claude liest die Zahlen (ein `claude -p` je Bild, nur Leserecht, zählt gegen dein Abo). Passen sie zur letzten
  Messung, bestätigt der Bot. Sonst zeigt er, was er gelesen hat, und fragt „Stimmt das? ✅ / ✏️ von Hand“ – z. B.
  wenn Views gesunken wären (fast immer ein Lesefehler). **Nichts wird ungeprüft gespeichert.**
- Das Bild wird nach der Auswertung gelöscht – es gibt kein Bildarchiv, nur die Zahlen (und Claudes Antwort als
  Text in der Datenbank, zum Nachprüfen).
- **Wann?** Der Score nimmt die Messung, die **Tag 7** am nächsten liegt, und nur Messungen ab **Tag 3** (die
  ersten Tage verteilt TikTok noch). Gut: ein Screenshot um Tag 3 und einer um Tag 7.

### 3. Hand-Eingabe
- Geht immer, auch ohne Bild: `#17 1240 61 6.8 34` = Views, Likes, Ø Wiedergabe **in Sekunden**, „vollständig
  angesehen“ in Prozent. `–` (oder `-`) für unbekannt, Komma geht auch (`6,8`), `34%` auch. Zeigt TikTok die
  Wiedergabe als `0:07`, tippst du `7` – der Bot sagt es dir auch, wenn du `0:07` schickst.
- **Optional dahinter Kommentare, Shares, Saves:** `#17 1240 61 6.8 34 3 5 2`. Es sind also **4 oder 7** Zahlen –
  kennst du nur einen der drei, die anderen mit `–` (`… 34 3 – 2`). Mit den dreien ist das Engagement vollständig;
  ohne sie zählen sie als 0 (Vermerk „Engagement unvollständig“). 5, 6 oder mehr als 7 Zahlen lehnt der Bot ab,
  damit keine Zahl still im falschen Feld landet.
- Nach ✏️ oder wenn Claude nichts lesen konnte, reichen die Zahlen ohne `#17`: `1240 61 6.8 34` (oder mit
  `3 5 2` dahinter).
- Alle Zähler (Views, Likes, Kommentare, Shares, Saves) sind ganze Zahlen – `1.240` (mit Tausenderpunkt) lehnt der
  Bot ab, statt 1,24 daraus zu machen. Auch die drei Zusatz-Zähler prüft er gegen die letzte Messung (sinken sie,
  fragt er nach).
- `[lernbot].screenshot_claude = false` schaltet Claude ganz ab – dann fragt der Bot gleich nach den Zahlen.

### 4. Der Publikums-Score – was die Zahl bedeutet
Drei Teile, jeweils gegen deinen **Median** der Vergleichsbasis (robust: ein Ausreißer-Post wirft ihn nicht um):
- **Wiedergabe** (Gewicht 0,5): welcher Anteil des Videos im Schnitt gesehen wurde – misst direkt Auswahl und
  Schnitt. Fehlt die Ø Wiedergabe, zählt „vollständig angesehen“.
- **Reaktionen je View** (Engagement, 0,3): (Likes + 2 · Shares + Saves + Kommentare) / Views. Nicht nur die
  Likes – deshalb heißt es im Bot „Reaktionen“ (Annahme A36).
- **Views** (Reichweite, 0,2): als Logarithmus – 10 000 statt 1 000 ist ein ähnlicher Schritt wie 1 000 statt 100.

„Über“ oder „unter“ misst der Score in deiner **üblichen Streuung** (MAD = typischer Abstand zum Median, genauer:
der Median aller Abstände – auch den wirft ein Ausreißer nicht um). Sind deine letzten Posts fast gleich, wäre sie
fast 0 und jeder winzige Unterschied riesig – deshalb hat sie je Teil ein Minimum (`[publikum.mad_minimum]`:
Wiedergabe 0,05 · Reaktionen je View 0,005 · Views 0,1). Je Teil, weil die Teile ganz verschieden streuen:
Reaktionen je View liegen um 0,05 und streuen nur um 0,01 – mit einem Minimum von 0,05 wie bei der Wiedergabe
hätte ein Post mit 0,09 statt z ≈ 1,35 nur 0,27 bekommen, fast wirkungslos.

Ohne Wiedergabe werden Reaktionen je View und Views auf 0,6/0,4 hochgerechnet („ohne Wiedergabe“). Solange weniger als
**5** bewertete Posts zum Vergleich da sind, ist der Score **0** mit Vermerk „Basis zu klein“ – aus dem Nichts wird
nichts gelernt. Der Score wird **einmal** gesetzt, sobald der Post 7 Tage alt ist, und danach nie überschrieben;
spätere Screenshots ändern ihn nicht mehr. Die Rechnung steht in `src/clip_pipeline/publikum.py` (Spec §6).

### 5. /publikum
Im Lern-Bot: `/publikum` (die letzten 10) oder `/publikum 20` (höchstens 30). Beispiel:
```
📊 Publikum · 12 Posts, 5 mit Score (die letzten 10, neueste zuerst)
#17 TikTok · Entwurf 41 · 4 Tage · 👁 1 240 ❤️ 61 ⏱ 6,8 s 🏁 34 % (Tag 4) · Score noch offen (ab 7 Tagen)
#16 TikTok · Clip 89 · 8 Tage · noch keine Zahlen – Screenshot mit #16 schicken · Score offen (braucht eine Messung ab Tag 3 mit Views)
#12 TikTok · Clip 88 · 9 Tage · 👁 5 000 ❤️ 300 ⏱ 12 s (Tag 7) · Score +0,8 (Wiedergabe über, Reaktionen je View unter, Views über deinem Median)
🤖 Claude diese Woche: 3 Aufrufe
```
Zeichen: 👁 Views · ❤️ Likes · ⏱ Ø Wiedergabe · 🏁 ganz angesehen. „(Tag 4)“ ist das Alter des Posts bei der letzten
Messung. „Score kommt beim nächsten Lauf“ heißt: alles da, der Timer war nur noch nicht dran. Die letzte Zeile zählt
die Screenshot-Auswertungen seit Montag 00:00, bei denen claude wirklich lief (fand der Dienst claude gar nicht,
zählt das nicht; `decide` und Stimmung zählen noch nicht mit, Annahme A18).

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
| Lern-Bot | Foto mit `#17` · Text `#17 1240 61 6.8 34` (optional `3 5 2` dahinter) | Zahlen per Screenshot bzw. von Hand |
| Lern-Bot | `/hilfe` | alles oben in Kurzform |
| Clip-Bot | Häkchen TikTok · `/link <clip> <url>` | wie bisher – legt zusätzlich den Post an (scheitert der Post, bleibt das Häkchen weg, Meldung mit Grund) |

`pipeline publikum bewerten` hält den Vertrag aller Befehle ein: Logs auf stderr, letzte Zeile auf stdout = eine
JSON-Zeile, z. B. `{"bewertet": 1, "ohne_messung": 0, "noch_zu_jung": 3, "fehler": 0, "posts": [{"id": 17,
"score": 0.0}], "meldung": true}`. Exit 0 ok (auch: nichts fällig) · 1 mindestens ein Post nicht bewertbar (die
anderen sind trotzdem bewertet) · 2 ein `[publikum]`-Schlüssel fehlt ganz, oder ein Wert in `[publikum.gewichte]`
bzw. `[publikum.mad_minimum]` ist unbrauchbar (keine Zahl; ein Minimum nicht größer als 0). Beliebig oft
aufrufbar – ein zweiter Lauf ändert nichts.

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
| `[publikum.gewichte].engagement` | `0.3` | Gewicht der Reaktionen je View (Engagement) |
| `[publikum.gewichte].reichweite` | `0.2` | Gewicht der Views |
| `[publikum.mad_minimum].wiedergabe` | `0.05` | kleinste Streuung der Wiedergabe (Anteil des Videos) beim Vergleich – 5 Prozentpunkte |
| `[publikum.mad_minimum].engagement` | `0.005` | kleinste Streuung der Reaktionen je View – die liegen um 0,05 und streuen um 0,01; größer = Ausreißer zählen weniger |
| `[publikum.mad_minimum].reichweite` | `0.1` | kleinste Streuung der Views (als ln(1 + Views)) – 0,1 ≈ 10 % mehr Views. Alle drei müssen größer als 0 sein; gelten nur für Scores, die danach gesetzt werden |
| `[lernbot].screenshot_claude` | `true` | Screenshots per `claude -p` lesen; `false` = gleich Hand-Eingabe |
| `[lernbot].screenshot_prompt` | `"templates/screenshot-prompt.txt"` | fester Auftrag an Claude (was lesen, wie antworten); relativ zum Projektordner, nicht zu `[speicher].wurzel` |
| `[lernbot].screenshot_timeout_s` | `120` | danach wird `claude` abgebrochen → Hand-Eingabe (der Aufruf zählt trotzdem, claude lief ja) |
| `[decide].programm` | `"claude"` | Pfad zu `claude` – **nur wenn claude unter /home liegt** (`sudo -iu pipeline command -v claude`) den vollen Pfad eintragen (systemd kennt `~/.local/bin` nicht). Gilt auch für `decide` aus n8n und die Stimmung: ein falscher Pfad schaltet Claude dort still ab |
| `[telegram].leise_von` / `leise_bis` | `"23:00"` / `"08:00"` | Ruhezeit: Publikums-Meldungen warten; `leise_von = ""` schaltet sie ab |
| `[vorschau].max_mb` | `48` | Obergrenze für die Upload-Fassung (Telegram-Bots dürfen höchstens 50 MB senden) |

Die Uhrzeit des täglichen Laufs steht **nur** im Timer (`deploy/systemd/clip-publikum.timer`, 10:00) – nicht in
der Konfig (eine Wahrheit, wie beim Abgleich).

## Installation

🏠 Alles in diesem Abschnitt machst du selbst, im CT `clips` als root. Hier im Container ist nichts davon gelaufen –
es gibt hier weder systemd-Dienste noch einen claude-Login für `pipeline`. Geprüft ist nur: die Unit-Dateien und
das Drop-in lassen sich von `systemd-analyze verify` lesen, und mit einem nachgebauten `e19-puffer.conf` daneben
meldet `systemd-analyze security --offline=true` für den Lern-Bot „read-only access to home directories“ (🧪).

**Bevor du anfängst:**
- **Stufe 1 kommt jetzt nach `main`** – über einen eigenen PR (Branch `lernschleife-publikum` → `main`), nicht erst
  mit dem ganzen Sprint nach Stufe 5. Erst wenn du diesen PR gemergt hast, holst du `main` hier wie gewohnt mit
  `git pull` (P1). Spätere Stufen kommen genauso: PR nach `main`, dann die Schritte, die ihre Anleitung nennt.
- Der Puffer-Betrieb aus `docs/PUFFER.md` läuft (`[lager].wurzel` gesetzt) – die Upload-Fassung wird nur im Puffer
  gebaut.
- Keine neuen Pakete, keine neuen Secrets (`.env` bleibt, wie sie ist).
- Nicht während eines Spielabends (Bots werden kurz neu gestartet).
- **Aus welchem Checkout läuft der Lern-Bot?** `systemctl cat clip-lernbot | grep -E 'WorkingDirectory|ExecStart'`.
  Laut Sprint-Log läuft er aus `/opt/clip-regie` (die Unit im Repo sagt `/opt/clip-pipeline`). Dann gilt alles
  unten, was den Lern-Bot betrifft, für `/opt/clip-regie`: dort auf main bringen (P1) und dort die `lokal.toml`
  ändern (P2). Datenbank und `.env` sind für beide dieselben. **Ausnahme Abnahme:** Den Score setzt der Timer
  `clip-publikum`, und der läuft immer aus `/opt/clip-pipeline` und liest `/opt/clip-pipeline/config/lokal.toml` –
  jeder Checkout liest die `lokal.toml` neben seinem eigenen Code (`konfig.PROJEKT`). `alter_tage` gehört deshalb in
  BEIDE `lokal.toml` (wie `docs/PUFFER.md` R5).

| Schritt | Freigabe nötig? | Rückweg |
|---|---|---|
| P1 Code einspielen | **ja** – Produktion ändern, Datenbank bekommt neue Tabellen | alten Stand auschecken |
| P2 claude im Lern-Bot | **ja** – zweite claude-Anmeldung (Zugangsdatei in `/var/lib/clip-pipeline/claude`, nur für `pipeline` lesbar); das Home bleibt schreibgeschützt | Drop-in entfernen, Ordner löschen |
| P3 Timer | **ja** – neuer Timer | Timer aus |

### P1 · Code einspielen und Datenbank sichern

**Was:** `/opt/clip-pipeline` auf den neuen Stand bringen, vorher Stand und Datenbank sichern, dann beide Bots neu
starten.
**Warum:** Befehl, Bot-Funktionen und Tabellen stecken im Code. Die Datenbank bekommt beim ersten Verbinden
**neue** Tabellen (`posts`, `publikum_messungen`, `rezept_stand`, `hypothesen`, `erwartungen`) und drei neue
Spalten (`clips.mic_stand`, `entwuerfe.rezept`, `entwuerfe.upload_pfad`). Bestehende Tabellen, Spalten und
CHECK-Bedingungen bleiben, wie sie sind. Beide Bots starten gleich neu – sonst läge im Lern-Bot der alte Code im
Speicher und der neue auf der Platte, und der nächste Absturz (`Restart=always`) schaltete still um.
**Freigabe nötig?** Ja – Produktion ändern.

```bash
# im CT als root
cd /opt/clip-pipeline
[ -e /var/lib/clip-pipeline/vor-publikum.sha ] || sudo -u pipeline git rev-parse HEAD | tee /var/lib/clip-pipeline/vor-publikum.sha
[ -e /var/lib/clip-pipeline/vor-publikum.db ] || sudo -u pipeline sqlite3 /var/lib/clip-pipeline/pipeline.db ".backup /var/lib/clip-pipeline/vor-publikum.db"
sudo -u pipeline git fetch -q origin && sudo -u pipeline git log --oneline HEAD..origin/main   # was alles mitkommt
sudo -u pipeline git pull --ff-only
sudo -u pipeline .venv/bin/pip install -q -e '.[whisper]'   # keine neuen Pakete – hält nur die Installation aktuell
# nur falls /opt/clip-regie existiert (Lern-Bot, wie in docs/PUFFER.md R3):
[ -d /opt/clip-regie ] && sudo -u pipeline git -C /opt/clip-regie fetch -q origin \
  && sudo -u pipeline git -C /opt/clip-regie checkout -q --detach origin/main
systemctl restart clip-bot clip-lernbot
sudo -u pipeline .venv/bin/pipeline status                   # verbindet einmal: legt die neuen Tabellen an
sudo -u pipeline sqlite3 /var/lib/clip-pipeline/pipeline.db "SELECT COUNT(*) FROM posts"   # 0 – Tabelle ist da
```
Der Lern-Bot läuft damit schon auf dem neuen Stand – bis P2 kann er Screenshots aber nicht lesen lassen und fragt
nach der Hand-Eingabe („claude nicht gefunden“ o. ä.).
**Rückweg:** `sudo -u pipeline git checkout -q "$(cat /var/lib/clip-pipeline/vor-publikum.sha)"` (ebenso in
`/opt/clip-regie`, falls umgestellt – `git -C /opt/clip-regie reflog` zeigt den alten Stand), dann
`systemctl restart clip-bot clip-lernbot`. Die neuen Tabellen stören den alten Code nicht. Im Notfall
`vor-publikum.db` zurückkopieren – nur bei gestoppten Bots und Timern; alles seit P1 ist dann weg.
**Was du lernst:** `[ -e datei ] || befehl` (ein Wiederholen überschreibt die Sicherung nicht), `sqlite3 .backup`
(stimmige Kopie im laufenden Betrieb), Migration beim Verbinden (`CREATE TABLE IF NOT EXISTS`, `ALTER TABLE … ADD
COLUMN` nur, wenn die Spalte fehlt – deshalb beliebig oft ausführbar).

### P2 · claude im Lern-Bot-Dienst

**Was:** Nachsehen, wo `claude` für `pipeline` liegt; eine eigene Anmeldung für den Dienst in
`/var/lib/clip-pipeline/claude` anlegen; ausprobieren, ob `claude` unter denselben Schutzregeln und mit denselben
Schaltern läuft wie im Dienst; dann das Drop-in `deploy/systemd/clip-lernbot.service.d/claude.conf` einspielen.
**Warum:** `clip-lernbot.service` hat `ProtectHome=true` – der Dienst sieht `/home` gar nicht. Das Drop-in macht
`/home` **nur lesbar** (falls claude in `/home/pipeline/.local/bin` liegt) und legt Anmeldung und Verlauf von claude
über `CLAUDE_CONFIG_DIR` nach `/var/lib/clip-pipeline/claude`, das der Dienst ohnehin beschreiben darf. Das Home
bleibt schreibgeschützt: Dort liegen `~/.ssh/authorized_keys` mit der Sperre des n8n-Schlüssels und die claude-Datei,
die `decide` aus n8n ohne Schutz startet – ein Fehler im Lern-Bot soll beides nicht ändern können.
`--no-session-persistence` verhindert, dass claude jeden Screenshot in seinem Verlauf aufhebt (sonst entstünde ein
Bildarchiv durch die Hintertür) – deshalb muss die installierte claude-Version den Schalter kennen.
**Freigabe nötig?** Ja – eine zweite claude-Anmeldung (dieselbe Anmeldung wie sonst, nur ein zweites Mal, für den
Dienst): die Zugangsdatei liegt in `/var/lib/clip-pipeline/claude` (Ordner 700, nur `pipeline`). Das Home bleibt
schreibgeschützt. (Offene Rückfrage R2 in `docs/ENTSCHEIDUNGEN.md`: So ist es jetzt Standard; die frühere Variante
„Dienst darf ins ganze Home schreiben“ würde die Sperre des n8n-Schlüssels angreifbar machen.)

```bash
# im CT als root
cd /opt/clip-pipeline
CLAUDE="$(sudo -iu pipeline command -v claude)"; echo "$CLAUDE"   # -i = Login-Shell, also der PATH von pipeline
sudo -u pipeline "$CLAUDE" --version
sudo -u pipeline "$CLAUDE" --help | grep no-session-persistence   # muss eine Zeile zeigen
# eigener Ordner für die Anmeldung des Dienstes, dort einmal anmelden (/login, danach /exit):
sudo -u pipeline install -d -m 700 /var/lib/clip-pipeline/claude
sudo -u pipeline env CLAUDE_CONFIG_DIR=/var/lib/clip-pipeline/claude "$CLAUDE"
# Probe mit denselben Schutzregeln wie der Dienst (Haupt-Unit + Drop-in) und denselben Schaltern wie der Bot:
sudo systemd-run --uid=pipeline --pty -p WorkingDirectory=/tmp \
  -p ProtectSystem=strict -p PrivateTmp=true -p NoNewPrivileges=true \
  -p ProtectHome=read-only -p ReadWritePaths=/var/lib/clip-pipeline \
  -p Environment=CLAUDE_CONFIG_DIR=/var/lib/clip-pipeline/claude -p Environment=DISABLE_AUTOUPDATER=1 \
  "$CLAUDE" -p --output-format json --allowedTools Read --no-session-persistence "sag ok"
mkdir -p /etc/systemd/system/clip-lernbot.service.d
cp deploy/systemd/clip-lernbot.service.d/claude.conf /etc/systemd/system/clip-lernbot.service.d/
# NUR wenn $CLAUDE unter /home liegt (z. B. /home/pipeline/.local/bin/claude) – lokal.toml des Checkouts, aus dem
# der Lern-Bot läuft (siehe „Bevor du anfängst“; hier /opt/clip-pipeline):
[ -e config/lokal.toml.vor-publikum ] || cp -a config/lokal.toml config/lokal.toml.vor-publikum
nano config/lokal.toml                     # unter [decide]: programm = "<Ausgabe von echo oben>"
systemctl daemon-reload
systemctl restart clip-lernbot
systemctl show clip-lernbot -p ProtectHome -p ReadWritePaths -p Environment
journalctl -u clip-lernbot -n 20           # „Lern-Bot läuft.“
```
- **Ist `echo` leer**, hat `pipeline` kein claude: erst `docs/SERVER.md` B3 erledigen – oder P2 aufschieben (unten).
- **Die Probe** antwortet mit einer JSON-Zeile: `"is_error":false` und ein `"result"` wie „ok“ = alles gut.
  `"is_error":true` heißt meist: Anmeldung fehlt (nochmal die Zeile mit `CLAUDE_CONFIG_DIR` und `/login`) oder
  Limit erreicht.
- **`systemctl show`** zeigt die wirksamen Werte: `ProtectHome=read-only`, in `ReadWritePaths` steht
  `/var/lib/clip-pipeline` (und `/srv/puffer`), in `Environment` stehen `CLAUDE_CONFIG_DIR=/var/lib/clip-pipeline/claude`
  und `DISABLE_AUTOUPDATER=1`. `systemctl cat clip-lernbot` zeigt außer `claude.conf` auch `e19-puffer.conf` aus
  `docs/PUFFER.md` R5 (nur `ReadWritePaths=/srv/puffer`). Drop-ins gelten alphabetisch; Listen wie
  `ReadWritePaths` addieren sich, bei Einzelwerten wie `ProtectHome` gewinnt das spätere – deshalb prüft
  `systemctl show` und nicht das Ende von `systemctl cat`.
- **`[decide].programm` nur ändern, wenn claude unter /home liegt.** Liegt es in `/usr/local/bin` oder `/usr/bin`,
  bleibt `lokal.toml`, wie sie ist (Dienst und SSH finden es dort ohnehin). Der Schlüssel gilt auch für `decide` aus
  n8n und für die Stimmung – ein falscher Pfad schaltet Claude dort **still** ab, dann gilt die Regel-Schnittliste.
  Läuft der Lern-Bot aus `/opt/clip-pipeline`, danach prüfen, was `decide` findet (mit dem PATH von SSH/systemd):
  `sudo -u pipeline env -i HOME=/home/pipeline PATH=/usr/local/bin:/usr/bin:/bin /opt/clip-pipeline/.venv/bin/python -c "import shutil; from clip_pipeline import konfig; print(shutil.which(konfig.lade().wert('decide.programm')) or 'NICHT GEFUNDEN')"`
- Gibt es `[decide]` in `lokal.toml` schon, die Zeile dort ergänzen (ein Abschnitt darf nur einmal vorkommen).
- Findet `grep` den Schalter nicht: erst claude aktualisieren (`sudo -u pipeline "$CLAUDE" update`) – bis dahin
  P2 aufschieben.
- **P2 aufschieben oder ablehnen:** In der `lokal.toml` des Checkouts, aus dem der Lern-Bot läuft, unter `[lernbot]`
  `screenshot_claude = false` eintragen, dann `systemctl restart clip-lernbot`. Es gibt dann nur die Hand-Eingabe,
  ohne den Umweg über „claude nicht gefunden“. Wie bei `[decide]`: Gibt es den Abschnitt schon, dort ergänzen.

**Prüfen:** Im Lern-Bot `/hilfe` – unten steht der Block „📊 Publikum“; `/publikum` antwortet „Noch keine Posts …“.
Den ersten echten Screenshot prüfst du in der Abnahme.
**Rückweg:** `rm /etc/systemd/system/clip-lernbot.service.d/claude.conf`, `lokal.toml.vor-publikum` zurück nach
`lokal.toml` kopieren (oder `[lernbot].screenshot_claude = false` eintragen), `systemctl daemon-reload`,
`systemctl restart clip-lernbot`; die zweite Anmeldung mit `rm -r /var/lib/clip-pipeline/claude` löschen.
**Was du lernst:** Drop-ins (`<dienst>.service.d/*.conf` ergänzt eine Unit), `systemctl show` für die wirksamen
Werte, `ProtectHome=true` vs. `read-only`, warum eine Liste gesperrter Pfade kein Schutz ist (es bleibt immer eine
Stelle offen – lieber gar nicht schreiben lassen), `CLAUDE_CONFIG_DIR` (Programme über die Umgebung umlenken), mit
`systemd-run -p …` Schutzregeln ausprobieren, bevor ein Dienst davon abhängt, `sudo -i` (Login-Shell mit dem PATH
des Benutzers) und warum Dienste `~/.local/bin` nicht im PATH haben.

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
1. In **`/opt/clip-pipeline/config/lokal.toml`** – daraus liest der Timer `clip-publikum`, der den Score setzt –
   und, falls der Lern-Bot aus `/opt/clip-regie` läuft, zusätzlich in `/opt/clip-regie/config/lokal.toml` (für die
   Anzeige in `/publikum`; siehe „Bevor du anfängst“). Beispiel in `config/lokal.beispiel.toml`:
   ```toml
   [publikum]
   alter_tage = 3
   ```
   Gibt es `[publikum]` dort schon, die Zeile darunter ergänzen – ein zweiter `[publikum]`-Kopf macht die Datei
   kaputt. Dann `systemctl restart clip-lernbot` (für die Anzeige in `/publikum`); der Timer braucht keinen
   Neustart, `pipeline` liest die Konfig bei jedem Aufruf. **Kontrolle:** `/publikum` zeigt bei einem jungen Post
   „Score noch offen (ab 3 Tagen)“. Steht dort „ab 7 Tagen“, ist `alter_tage` oder `[publikum]` vertippt – ein
   vertippter Name gibt keinen Fehler, er wird still ignoriert.
2. Einen 👍-Short über 📦 posten, `/link <entwurf> <TikTok-Link>` → der Bot nennt die Post-Nummer, z. B. `#1`.
3. Ab Tag 3: Screenshot der Statistik mit `#1` an den Lern-Bot → der Bot bestätigt die gelesenen Zahlen.
4. Nächster Lauf (10:00 oder von Hand `sudo -u pipeline /opt/clip-pipeline/.venv/bin/pipeline publikum bewerten`)
   → JSON `"bewertet": 1`; Meldung `📊 1 Post bewertet: #1 0 – /publikum` mit der Zeile „Score 0 = noch zu wenige
   bewertete Posts …“; in `/publikum`: `Score 0 (Basis zu klein)`.

**Erwartet:** `bewertet_utc` gesetzt, **Score 0**, Vermerk **„Basis zu klein“** – unter 5 bewerteten Posts gibt es
keinen Score ≠ 0. Danach `alter_tage = 3` **wieder entfernen**, aus beiden Dateien, dann
`systemctl restart clip-lernbot`. Zum Prüfen darf
`grep -n alter_tage /opt/clip-pipeline/config/lokal.toml /opt/clip-regie/config/lokal.toml` nichts mehr finden.
Warum so gründlich: Scores werden nie überschrieben – bleibt die Zeile in `/opt/clip-pipeline` stehen, bekommen alle
Posts dauerhaft Tag-3-Scores; Posts, die in dieser Probezeit bewertet werden, behalten ihre Tag-3-Messung für immer.

## Im Alltag

- **Rhythmus:** posten → `/link` → Screenshot um Tag 3 und um Tag 7 → um 10:00 bewertet die Pipeline, was 7 Tage
  alt ist → Meldung im Lern-Bot.
- **Nachsehen:** `/publikum` im Lern-Bot. Genauer, im CT:
  `sudo -u pipeline sqlite3 /var/lib/clip-pipeline/pipeline.db "SELECT id, ziel, score, score_teile FROM posts ORDER BY id DESC LIMIT 5"`
  – `score_teile` enthält alle Zwischenwerte (r, e, v, z-Werte, Median und MAD der Basis, die benutzten Gewichte
  und MAD-Minima, welche Messung).
- **Kosten:** ein `claude -p` je Screenshot; die Wochenzahl steht in der letzten Zeile von `/publikum`.
- **Nichts wird gelöscht:** Posts und Messungen bleiben; nur die Screenshots selbst verschwinden nach der Auswertung.

## Was tun, wenn …

- **… Claude die Zahlen nicht lesen konnte?** Der Bot fragt dann gleich nach der Hand-Eingabe – die 4 (oder 7)
  Zahlen schicken. Passiert es immer:
  P2 prüfen (`journalctl -u clip-lernbot -n 50`, Probe mit `systemd-run`), Anmeldung in
  `/var/lib/clip-pipeline/claude` abgelaufen? Übergangsweise `[lernbot].screenshot_claude = false`.
- **… der Bot „🗑 Rückfrage zu #17 verworfen – NICHT gespeichert“ sagt?** Du hast etwas Neues geschickt, während er
  noch auf deine Antwort zu #17 wartete. Die Zahlen für #17 nochmal schicken (Screenshot oder `#17 …`).
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
  Datenbank etwas änderst. Steht dort stattdessen „Unerwarteter Fehler … ValueError: could not convert string to
  float“, ist ein `[publikum]`-Wert in `lokal.toml` keine Zahl (z. B. `alter_tage = "drei"`) – korrigieren, dann
  nochmal laufen lassen.
- **… Exit 2 kommt?** Ein `[publikum]`-Schlüssel fehlt ganz (meist nach einem Update: `pipeline.toml` des Checkouts
  prüfen) – die JSON-Zeile nennt ihn. Ebenso, wenn in `[publikum.gewichte]` oder `[publikum.mad_minimum]` ein Wert
  keine Zahl ist (z. B. `engagement = "0,005"` mit Anführungszeichen und Komma) oder ein Minimum 0 bzw. negativ ist –
  die JSON-Zeile nennt den Schlüssel, z. B. `[publikum.mad_minimum].engagement`. Achtung: Ein **vertippter** Name oder Abschnitt in `lokal.toml` (z. B.
  `alter_tag`, `[publkum]`) gibt **keinen** Fehler – er wird still ignoriert, und der Standard aus `pipeline.toml`
  gilt weiter.
- **… `/publikum` „Score kommt beim nächsten Lauf“ sagt, aber nach 10:00 nichts passiert?** Meist steht
  `alter_tage` nur in einer der beiden `lokal.toml` (siehe Abnahme): Der Lern-Bot rechnet mit seiner, der Timer mit
  der aus `/opt/clip-pipeline`.
- **… der Clip-Bot „⚠️ TikTok nicht abgehakt – Post fürs Lernen ging nicht: …“ meldet?** Das ist Absicht: ohne Post
  kein Häkchen, sonst fehlte der Post still in der Lernschleife (Annahme A35). Der Clip bleibt „freigegeben“, und
  die tägliche Erinnerung läuft weiter. Beim Knopf erscheint die Meldung nur kurz; `/link` zeigt sie als Nachricht.
  Den Grund zeigt `journalctl -u clip-bot -n 50`: „Clip #88: Post für tiktok nicht angelegt – Häkchen
  zurückgenommen“ plus Traceback. Nennt er einen `[publikum]`-Schlüssel: Konfig reparieren (wie bei Exit 2). Sonst
  sind die Daten des Clips beschädigt – melde dich, bevor du in der Datenbank etwas änderst.
  Muss der Clip sofort als veröffentlicht gelten (Notausgang):
  1. In `config/lokal.toml` unter `[publikum]` `plattformen = []` eintragen (gibt es `[publikum]` schon, z. B. mit
     `alter_tage = 3`, die Zeile darunter – ein zweiter `[publikum]`-Kopf macht die Datei kaputt).
  2. `systemctl restart clip-bot` – nur den Clip-Bot, der Lern-Bot liest dieselbe Einstellung.
  3. Abhaken bzw. `/link`.
  4. Die Zeile wieder entfernen und nochmal `systemctl restart clip-bot`.
  Dieser eine Clip hat dann keinen Post, also keine Zahlen in der Lernschleife.
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
