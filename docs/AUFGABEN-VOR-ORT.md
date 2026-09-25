# Aufgaben für die Vor-Ort-Sitzung (root-b0) – Sprint „Regisseur“

Von: Cloud-Sitzung „RC“ (baddieday-voting-b3, `session_01AN2PTi6AzPKN2Ur96BmQ6j`).
Warum diese Datei: Du kannst mir Nachrichten schicken, ich dir aber nicht („this cloud session cannot message
other sessions yet“). Deshalb stehen meine Bitten hier. **Deine Antworten bitte weiter per SendMessage an mich.**

> **Stand 24.09.:** root-b0 läuft auf dem vServer („DEY“), nicht im LXC „clips“. Teil A ist erledigt (siehe
> `docs/SPRINT-LOG.md`). B–D brauchen eine Sitzung **im LXC „clips“** (der war im Tailnet gerade offline).

## Hintergrund
Der Nutzer hat mich beauftragt, bis **Mo 28.09., 20:00** den Sprint „Regisseur“ umzusetzen
(Branch `sprint-regisseur`, Draft-PR https://github.com/baddieday/baddieday-voting/pull/1). Ich laufe in
einem Cloud-Container ohne Heimnetz (kein `/srv/clips`, kein `/dev/dri`, kein Tailscale, kein pve-big).
Alles Gebaute ist hier mit künstlichem Material getestet. Stand und Entscheidungen: `docs/SPRINT-LOG.md`,
`docs/ENTSCHEIDUNGEN.md`. Der Nutzer hat gesagt, wir sollen uns den Aufwand teilen.

## Harte Regeln (gelten auch für dich)
1. Rohdaten nie löschen oder verschieben, nur kopieren (mit Prüfsumme).
2. Git-Branch `sprint-regisseur`.
3. pve-big nur für nötige Aufgaben wecken und danach sofort herunterfahren. Kann er nicht zuverlässig
   heruntergefahren werden: gar nicht wecken. Ab Mo 28.09., 20:00 nicht mehr wecken; läuft er dann: herunterfahren.
4. Bestehende n8n-Workflows, den bisherigen Bot (`clip-bot`) und den VPS **nicht** verändern.
   Die laufende Produktion unter `/opt/clip-pipeline` nicht anfassen.
5. Geheimnisse nur in `.env`, nie in Code, Commits, Logs – und nicht in Nachrichten an mich.

Laut CLAUDE.md: vor Paketinstallation, Löschen/Überschreiben, Systemänderungen und `git push` den Nutzer
fragen, sofern er es nicht schon erlaubt hat. Bitte **nichts auf `sprint-regisseur` pushen**, ohne es vorher
mit mir abzustimmen – ich pushe dort laufend.

## Neu auf dem Branch
`pipeline big status|pruefen|waechter|aus|halten` (Sicherheitsnetz; Timer `deploy/systemd/clip-big-waechter.*`,
Gegenstück `deploy/big/clip-big-steuer.sh`) · `bestand` · `material` · `stimmung` (faster-whisper small, de) ·
`musik analysieren|hinzufuegen|ncs|liste` · `compose --format zusammenschnitt|short`.
Bei mir in Arbeit: Entwürfe rendern (VA-API/CPU; NVENC auf pve-big), Lern-Bot, Windows-Helfer.

## Bitte – in dieser Reihenfolge, nach jedem Punkt kurz an mich berichten

**A) Umgebung (nur lesen)**
- Welcher Rechner genau? (`hostname`; LXC „clips“ auf pve-mini, der pve-mini-Host selbst oder etwas anderes?)
- `/srv/clips` mit `.clip-speicher` da – oder leer, weil pve-big schläft?
- `ls -l /dev/dri`, `ffmpeg -hide_banner -encoders | grep -E "vaapi|nvenc"`, `vainfo` (falls installiert)
- `df -h /var/lib/clip-pipeline /srv/clips`, `nproc`, `free -g`
- SSH-Weg zu pve-big vorhanden? (welcher Benutzer/Schlüssel – keine Schlüssel-Inhalte)
- Steht `LEARN_BOT_TOKEN` in `/opt/clip-pipeline/.env`? (nur ja/nein, **Wert nie ausgeben**)
- `sqlite3 -readonly /var/lib/clip-pipeline/pipeline.db "select status, count(*) from clips group by status"`
- Ist `claude` für den Benutzer `pipeline` angemeldet (`sudo -u pipeline claude -p "sag ok"`)?

**B) Bestandsaufnahme ohne die Produktion anzufassen**
```bash
git clone -b sprint-regisseur https://github.com/baddieday/baddieday-voting /opt/clip-regie
cd /opt/clip-regie && python3 -m venv .venv && .venv/bin/pip install -e .
CLIP_DATENBANK=/tmp/regie-test.db .venv/bin/pipeline bestand --bericht /tmp/BESTAND.md
```
(weckt pve-big nicht). Inhalt von `/tmp/BESTAND.md` an mich.

**C) pve-big – nur prüfen**
Läuft er gerade? Wie wäre er per SSH erreichbar? **Nicht wecken, nichts einrichten.** `clip-big-steuer.sh`
auf pve-big zu installieren ist eine Host-Änderung, die der Nutzer freigeben muss.

**D) Echte Stimmung (nur mit OK des Nutzers für die Paketinstallation)**
```bash
cd /opt/clip-regie && .venv/bin/pip install -e .[whisper]
sqlite3 /var/lib/clip-pipeline/pipeline.db ".backup /tmp/regie-test.db"
CLIP_DATENBANK=/tmp/regie-test.db .venv/bin/pipeline stimmung --ohne-claude
```
Braucht die Clip-Dateien: nur möglich, wenn `/srv/clips` gerade eingehängt ist (pve-big läuft) oder schon
eine lokale Material-Kopie existiert. Berichte: wird die Mikro-Spur erkannt? Verteilung der Stimmungen,
3–5 Beispiele (Stimmung + Wörter-Zähler, keine Namen), Laufzeit je Clip.

**E) Danach (mit mir abstimmen):** Musik laden (`pipeline musik ncs --stimmung episch` usw.), `compose`,
Entwurf rendern – dafür sage ich Bescheid, sobald das Rendern auf dem Branch ist.
