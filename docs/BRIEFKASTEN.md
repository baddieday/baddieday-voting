# Briefkasten der Freunde (vServer)

Deine Freunde laden ihre Aufnahmen in einen **Briefkasten auf deinem vServer** hoch. Dein Mini holt sie dort über
Tailscale ab und schneidet das Video. So braucht kein Freund einen eigenen Server, und niemand kommt in dein Heimnetz.

Technisch ist der Briefkasten ein zweiter, eigener SSH-Dienst nur für Dateien (SFTP) auf **Port 2222**. Dein normaler
SSH-Zugang und n8n bleiben unberührt; Videos gehen nie durch n8n. Plan und Annahmen: `docs/ENTSCHEIDUNGEN.md`,
Abschnitt „Mehrbenutzer“, ab M85.

## Was wo liegt
| Was | Wo |
|---|---|
| Konfig, eigener Hostschlüssel, Schlüssel der Freunde (alles root) | `/etc/briefkasten/` (`sshd_config`, `hochladen/`, `abholen/`, `briefkasten.conf`) |
| Fach je Freund: eigenes Dateisystem fester Größe | Bild `/srv/briefkasten/bilder/bk-<name>.img`, eingehängt unter `/srv/briefkasten/fach/bk-<name>/fach` |
| Rückweg-Skript, Sicherungen alter Fassungen | `/root/briefkasten/` |
| Dienst und Protokoll | `briefkasten-sshd` · `journalctl -u briefkasten-sshd` |

Im Fach: `videos/`, `replays/`, `sitzungen/`, `status/` (gehören dem Freund) und die Marke `.clip-briefkasten`
(gehört root). Der vServer prüft und rechnet nichts.

## Wer darf was
- **PC des Freundes** (öffentliche Adresse, Port 2222): nur hochladen und umbenennen. Nicht lesen, nicht löschen, keine
  Ordner anlegen, kein fertiges Ziel überschreiben, nicht an die Marke.
- **Dein Mini** (nur über die Tailnet-Adresse des vServers und nur von der Adresse des CT clips): nur lesen.
- **Jeder Freund sieht nur sein Fach.** Ein volles Fach trifft nur ihn („Failure“, alles bleibt auf seinem PC). Ist ein
  Fach nicht eingehängt, scheitert der Upload („Permission denied“) – auf die Systemplatte wird nie geschrieben.
- **Gelöscht wird im Briefkasten nichts** – bis zu deinem Ja (siehe unten).

## Einmal einrichten (vServer, als root)
1. Nur nachsehen: `systemd-detect-virt` (kvm oder vm?), `losetup -f`, `ssh -V`, `ip -4 addr show tailscale0`,
   `ss -ltn | grep 2222`, `df -h /`. Im CT clips: `tailscale ip -4` – das ist die Mini-IP.
2. Die drei Skripte holen:
   ```
   mkdir -p /root/briefkasten && cd /root/briefkasten && for f in einrichten freund pruefen; do curl -fsSLO https://raw.githubusercontent.com/baddieday/baddieday-voting/main/deploy/vserver/briefkasten-$f.sh; done
   ```
3. `bash briefkasten-einrichten.sh --mini-ip <Mini-IP> --probe` zeigt nur. Danach ohne `--probe`: Es fragt vor jeder
   Änderung (j/N), legt Gruppe, Ordner, Konfig und Dienst an, eine ufw-Regel nur nach „j“, und prüft am Ende.
4. In der **Firewall des Hosters** TCP 2222 öffnen.
5. Tailnet-Regel: Der CT clips muss den vServer auf Port 2222 erreichen (bei „alle dürfen alles“ nichts zu tun).
   `tailscale ping <vServer>` im CT sollte direkt gehen, nicht über DERP.

Bricht es ab (Container ohne Loop-Geräte, Tailscale im Userspace-Modus, Port belegt, zu wenig Platz), ist nichts
verändert – bitte melden.

## Je Freund
Im CT als root `bash /opt/clip-pipeline/deploy/benutzer/benutzer-anlegen.sh max` (erst mit `--probe`), Schritt 9
„Briefkasten“ mit j:
1. Beim ersten Freund fragt es einmal die Tailnet-Adresse des vServers (dort `tailscale ip -4`), seinen öffentlichen
   Namen (dahin laden die PCs) und den Port (Enter = 2222). Gemerkt in `/etc/clip-briefkasten.conf`, sobald der
   Briefkasten antwortet.
2. Es legt zwei Schlüssel für ihn an (`/var/lib/clip-benutzer/max/briefkasten`, der private Teil erscheint nie), holt
   den Hostschlüssel über das Tailnet und zeigt dessen Fingerabdruck – vergleiche ihn mit „Hostschlüssel:“ am Ende des
   Einrichtens.
3. Es zeigt dir **eine Zeile** – auf dem vServer als root ausführen (dort j), dann im CT Enter:
   ```
   bash /root/briefkasten/briefkasten-freund.sh max --pc '<Schlüssel>' --abholen '<Schlüssel>'   [--groesse 20]
   ```
4. Probe-Abholung in seiner Sandbox. Erst wenn sie grün ist, geht das Abholen an (alle 2 min). Ist sie rot, nennt es
   den Grund; danach nochmal dasselbe Skript – Schlüssel und Eintrag bleiben, nur Enter und die Probe.
5. Der Freund tippt in seinem Bot **/pc** und richtet sein PC-Programm ein (`docs/FREUNDE.md`, „Wie deine Aufnahmen zu
   Florian kommen“). Danach `bash …/benutzer-pruefen.sh max`: Briefkasten erreicht, Fach in %, PC gemeldet.

## PC-Programm der Freunde
`/pc` gibt es nur im Bot eines Freundes mit Briefkasten; der Bot baut das ZIP jedes Mal frisch: die drei Skripte aus
`windows/` (`Freund-Hochladen.ps1`, `Freund-Einrichten.ps1`, `Freund-Einrichten.cmd`), `freund.psd1` (dein öffentlicher
Name, Port, `bk-<name>`) und seinen PC-Schlüssel samt Hostschlüssel (`I/briefkasten/pc`, `known_hosts_pc`) – nie den
Abhol-Schlüssel. Das Programm lädt je Datei erst `<name>.teil`, benennt um und legt dann den Lieferschein daneben;
ein Replay erst nach den Aufnahmen seines Matches, 45 min nach dem letzten Match die Abend-Datei, dazu
`status/pc-status.json`. Es löscht nie, braucht kein Admin und keine Installation. Annahmen M94, M97, M117–M124.

**Einmal vor Ort prüfen (echte Windows PowerShell 5.1, hier nicht testbar):** beim Freund oder auf deinem PC mit
einem Test-Fach `Freund-Einrichten.cmd` laufen lassen, dann in `%LOCALAPPDATA%\ClipUpload`
`powershell -ExecutionPolicy Bypass -File .\Freund-Hochladen.ps1 -Probe`. Erwartet: „[OK] Verbunden mit Florians
Briefkasten“ – damit stimmen Pfade mit Leerzeichen, die Rechte am Schlüssel und der Hostschlüssel. In der
Aufgabenplanung läuft „Clip-Upload“ alle 2 min ohne Fenster; das Log steht in `%LOCALAPPDATA%\ClipUpload\hochladen.log`.

Auf dem vServer gilt für die Zeile:
- **Größe:** Standard 20 GB, mindestens 8 GB. Das System behält immer 15 % und mindestens 10 GB frei (n8n); passt es
  nicht, nennt das Skript die größte Größe, die geht.
- **Vergrößern:** `… max --groesse 40` (nur wachsen; das Fach ist dabei kurz ausgehängt, ein Upload setzt später fort).
- **Aus und wieder an:** `… max --sperren` / `--entsperren` (Fach und Schlüssel bleiben).
- **Schlüssel tauschen:** dieselbe Zeile mit dem neuen Schlüssel – die alte Zeile wird gesichert.

## Prüfen
`bash /root/briefkasten/briefkasten-pruefen.sh [max]` liest nur: Dienst, Port, Konfig, beide Rechte-Profile, je Fach
eingehängt, Marke, belegt, Dateien, gesperrt, Schlüssel. Derselbe Schlüssel bei zwei Freunden ist ein Befund.
„Alles in Ordnung.“ = Exit 0.

## Abnahme von Hand (ohne PC-Programm)
Mit dem PC-Programm (oben) ist das nicht mehr nötig – zum Nachstellen bleibt es. Mit einem Test-Freund `test` (eigener
Test-Bot), angelegt mit `benutzer-anlegen.sh test` samt Schritt „Briefkasten“ (grüne Probe). Du spielst seinen PC –
im CT als root, mit Kopien deiner Aufnahmen eines Abends:
1. Seinen PC-Schlüssel für dich kopieren (als root nimmt ssh ihn nur mit 0600):
   `install -m 600 /var/lib/clip-benutzer/test/briefkasten/pc /root/bk-test-pc`.
2. In einem leeren Ordner mit Kopien: Aufnahmen (`*.mp4` mit Nvidia- oder SteelSeries-Namen), das Replay
   (`UnsavedReplay-*.replay`) und eine Abend-Datei `session_<ID>.json` mit
   `{"session": "<ID>", "matches": ["<ID>"], "ende_utc": "2026-10-09T20:45:00Z"}` – die ID ist Datum und Uhrzeit aus
   dem Replay-Namen (`UnsavedReplay-2026.10.08-20.15.33.replay` → `2026-10-08_20-15-33`). Zu jeder Datei ein
   Lieferschein:
   ```
   for f in *.mp4 *.replay session_*.json; do printf '{"name": "%s", "groesse": %s, "sha256": "%s", "mtime_ms": %s, "utc_offset_min": 120}\n' "$f" "$(stat -c %s "$f")" "$(sha256sum "$f" | cut -d' ' -f1)" "$(stat -c %Y "$f")000" > "$f.lieferschein"; done
   ```
3. Hochladen wie sein PC (öffentliche Adresse; je Datei erst `.teil`, dann umbenennen, dann der Lieferschein):
   `sftp -P 2222 -i /root/bk-test-pc -o UserKnownHostsFile=/var/lib/clip-benutzer/test/briefkasten/known_hosts_pc bk-test@<öffentlich>`,
   dann z. B. `put a.mp4 videos/a.mp4.teil`, `rename videos/a.mp4.teil videos/a.mp4`,
   `put a.mp4.lieferschein videos/a.mp4.lieferschein.teil`, `rename videos/a.mp4.lieferschein.teil videos/a.mp4.lieferschein`
   – ebenso das Replay nach `replays/` und zuletzt die Abend-Datei nach `sitzungen/`.
   Erwartet abgewiesen: `get videos/a.mp4 x`, `rm videos/a.mp4` („Permission denied“).
4. Erwartet: Binnen 2 min holt der Mini ab (`journalctl -u clip-freund-abholen@test -n 20`), dann rechnet sein Match,
   und das Abend-Video kommt **nur in seinem Bot**. Dein nächster n8n-Lauf läuft wie immer.
5. Danach `bash briefkasten-freund.sh test --sperren` auf dem vServer und `bash …/benutzer-stilllegen.sh test` im CT
   (Fach und Daten bleiben; die Kopie `/root/bk-test-pc` wegräumen, wenn du willst).

## Abschalten
- Ganz: `bash /root/briefkasten/zurueck.sh` (= `systemctl disable --now briefkasten-sshd`). Gelöscht wird nichts.
- Ein Freund: `briefkasten-freund.sh <name> --sperren`.
- Restrisiko ist eine Lücke im SSH vor der Anmeldung (Beispiel CVE-2024-6387): Updates per `apt upgrade`, im Zweifel
  ausschalten.

## Platz und Löschen – deine zwei Fragen
- **Platz:** je Freund mindestens 8 GB, empfohlen 20 GB (typisch 2,6 GB je Spielabend). Rein und raus laufen je Freund
  etwa 70 GB Verkehr im Monat.
- **Löschen:** Bis zu deinem Ja löscht der Briefkasten nichts. Ein 20-GB-Fach ist dann nach etwa 2,5 Wochen voll
  (Vielspieler etwa 1 Woche); danach scheitert der Upload sicher, alles bleibt auf dem PC des Freundes – vergrößern
  mit `--groesse`. Mit Ja löscht der Mini nur Aufnahmen, die er nachweislich komplett hat, frühestens nach 24 h, und
  der erste Lauf ist nur eine Probe (wird erst nach deinem Ja gebaut).

## Datenschutz
Aufnahmen können Sprachchat enthalten. Sie liegen unverschlüsselt im Fach beim Hoster – bis zu deinem Ja dauerhaft.
Als root kannst du alles sehen (M14); die Freunde sehen einander nicht.
