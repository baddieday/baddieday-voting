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
Die fertige Zeile zeigt dir `benutzer-anlegen.sh` im CT (kommt mit dem nächsten Schritt):
```
bash /root/briefkasten/briefkasten-freund.sh max --pc '<Schlüssel>' --abholen '<Schlüssel>'   [--groesse 20]
```
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
1. Im CT clips als root zwei Test-Schlüssel: `ssh-keygen -t ed25519 -N '' -f /root/bk-test-pc` und
   `ssh-keygen -t ed25519 -N '' -f /root/bk-test-mini`.
2. Auf dem vServer: `bash briefkasten-freund.sh test --groesse 8 --pc '<Inhalt von bk-test-pc.pub>' --abholen '<Inhalt von bk-test-mini.pub>'`.
3. **Hochladen wie ein PC** (über die öffentliche Adresse; beim ersten Mal den Fingerabdruck mit „Hostschlüssel: …“ aus
   dem Einrichten vergleichen): `sftp -P 2222 -i /root/bk-test-pc bk-test@<öffentliche Adresse>`, dann
   `put a.mp4 videos/a.mp4.teil`, `rename videos/a.mp4.teil videos/a.mp4`, `ls -1 videos`, `df`.
   Erwartet: `get videos/a.mp4 x` und `rm videos/a.mp4` → „Permission denied“.
4. **Abholen wie der Mini** (über das Tailnet): `sftp -P 2222 -i /root/bk-test-mini bk-test@<Tailnet-IP des vServers>`,
   dann `get videos/a.mp4 /tmp/a.mp4` und `sha256sum` vergleichen. Erwartet: `put` und `rm` → „Permission denied“.
5. Erwartet abgewiesen: der Mini-Schlüssel über die öffentliche Adresse, der PC-Schlüssel über das Tailnet.
6. Danach `bash briefkasten-freund.sh test --sperren` (das Fach bleibt; wegräumen nur von Hand, wenn du willst).

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
