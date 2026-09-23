# Server einrichten (Stufe 0) – Schritt für Schritt

Aufbau:

```
Gaming-PC (Windows) ──SMB──► GROSSER PVE-HOST (Speicher, läuft bei Bedarf, Wake-on-LAN)
                                   │ NFS
                                   ▼
                             MINI-PVE (immer an) ─► LXC "clips": Pipeline + Telegram-Bot + SQLite
                                   ▲
vServer: n8n ──SSH über Tailscale──┘            Handy ◄── Telegram
```

Warum so? Die Clips liegen nur auf dem großen Host (viel Speicher). Rechnen und der Bot laufen auf dem
Mini, weil der immer an ist. Die Datenbank liegt auf dem Mini, damit Battles und Klicks auch funktionieren,
wenn der große Host schläft. Telegram merkt sich jedes einmal gesendete Video (`file_id`).

> Alle Befehle als root auf dem jeweiligen Host, außer wo `sudo -u pipeline` steht.
> Platzhalter in `<spitzen Klammern>` ersetzen.

---

## A. Großer PVE-Host (Speicher)

### A1. Speicherort anlegen
```bash
zfs create <pool>/clips            # ohne ZFS: mkdir -p /srv/clips
# Ein fester Benutzer für alle Dateien. UID 101000 = "pipeline" (UID 1000) im unprivilegierten LXC.
groupadd -g 101000 clips && useradd -u 101000 -g 101000 -M -s /usr/sbin/nologin clips
cd /<pool>/clips
mkdir -p eingang replays sessions highlights musik archiv papierkorb
touch .clip-speicher               # Markierung: "hier ist wirklich der Clip-Speicher"
chown -R clips:clips .
```
*Lerneffekt:* In einem unprivilegierten Container wird jede UID um 100000 verschoben. Wer drinnen UID 1000 ist,
ist draußen 101000. Deshalb gehören die Dateien UID 101000.

### A2. Freigabe für Windows (Samba)
```bash
apt install samba
smbpasswd -a clips                 # Passwort selbst wählen (nicht ins Repo!)
cat >> /etc/samba/smb.conf <<'EOF'
[clips]
   path = /<pool>/clips
   valid users = clips
   force user = clips
   force group = clips
   read only = no
EOF
systemctl restart smbd
```
Unter Windows: Explorer → `\\<grosser-host>\clips` öffnen → Anmelden als `clips` (Anmeldedaten merken).

### A3. Freigabe für den Mini (NFS)
```bash
apt install nfs-kernel-server
echo '/<pool>/clips <ip-des-mini>(rw,sync,no_subtree_check,all_squash,anonuid=101000,anongid=101000)' >> /etc/exports
exportfs -ra
```
`all_squash` + `anonuid` sorgt dafür, dass alles, was der Mini schreibt, dem Benutzer `clips` gehört.

### A4. Wake-on-LAN
1. Im BIOS „Wake on LAN“ / „Power on by PCI-E“ einschalten.
2. Netzwerkkarte und MAC herausfinden: `ip -br link` (z. B. `enp3s0 … aa:bb:cc:dd:ee:ff`).
3. Dauerhaft aktivieren – in `/etc/network/interfaces` beim Interface ergänzen:
   `post-up /usr/sbin/ethtool -s <nic> wol g`  (ggf. `apt install ethtool`)
4. Die MAC kommt in `windows/uebertragung.psd1` (WakeOnLanMac).

---

## B. Mini-PVE (immer an)

### B1. NFS auf dem Mini-Host einhängen
```bash
apt install nfs-common
mkdir -p /mnt/clips
echo '<grosser-host>:/<pool>/clips /mnt/clips nfs soft,timeo=50,retrans=3,bg,nofail,_netdev 0 0' >> /etc/fstab
mount /mnt/clips && ls /mnt/clips   # .clip-speicher muss zu sehen sein
```
*Warum `soft`?* Wenn der große Host schläft, bekommt ein Zugriff nach kurzer Zeit einen Fehler, statt ewig zu
hängen. Zusätzlich prüft die Pipeline vorher per Netzwerk-Ping (config `[speicher].host`), ob er wach ist.

### B2. LXC-Container anlegen
Proxmox-Oberfläche → „CT erstellen“: Debian 13, **unprivilegiert**, 2–4 Kerne, 4 GB RAM, 16 GB Disk.
Danach in `/etc/pve/lxc/<id>.conf` ergänzen:
```
mp0: /mnt/clips,mp=/srv/clips
# Tailscale braucht das TUN-Gerät:
lxc.cgroup2.devices.allow: c 10:200 rwm
lxc.mount.entry: /dev/net/tun dev/net/tun none bind,create=file
# Optional (Stufe 0c): iGPU für Hardware-Encoding
# dev0: /dev/dri/renderD128,gid=104
```

### B3. Im Container: Pipeline installieren
```bash
apt update && apt install -y python3 python3-venv ffmpeg git sqlite3 openssh-server fonts-dejavu-core sudo
useradd -m -u 1000 -s /bin/bash pipeline
mkdir -p /opt/clip-pipeline /var/lib/clip-pipeline && chown pipeline: /opt/clip-pipeline /var/lib/clip-pipeline
sudo -u pipeline git clone <dein-repo> /opt/clip-pipeline      # oder Ordner kopieren
cd /opt/clip-pipeline
sudo -u pipeline python3 -m venv .venv
sudo -u pipeline .venv/bin/pip install -e .     # -e: Code bleibt in /opt/clip-pipeline (dort liegt config/)
chmod +x bin/pipeline deploy/n8n-lauf.sh      # Einstieg laut n8n-Vertrag: /opt/clip-pipeline/bin/pipeline
```
Für `decide` (optional, sonst gilt die Regel-Schnittliste): Claude Code im Container installieren und einmal
als `pipeline` anmelden (`sudo -u pipeline claude`), damit `claude -p` über dein Max-Abo läuft.
Replay-Parser (Linux-Build, braucht kein .NET) vom Windows-PC kopieren:
```powershell
scp E:\GIT\baddieday-voting\tools\replay2json\bin\linux-x64\replay2json pipeline@<mini-lxc>:/opt/clip-pipeline/tools/replay2json/bin/linux-x64/
```
```bash
chmod +x /opt/clip-pipeline/tools/replay2json/bin/linux-x64/replay2json
```
In `config/pipeline.toml` unter `[speicher]` eintragen: `host = "<grosser-host>"` und
`wol_mac = "<MAC aus A4>"`. Dann weckt die Pipeline den großen Host selbst, z. B. für das Highlight-Video am
Freitag (der Container muss dafür an derselben Netzwerkbrücke hängen wie der große Host, meist `vmbr0`).

`.env` anlegen (siehe `.env.example`): Telegram-Token und deine Telegram-User-ID.
```bash
sudo -u pipeline cp .env.example .env && sudo -u pipeline nano .env && chmod 600 .env
```

Test:
```bash
sudo -u pipeline .venv/bin/pipeline status
sudo -u pipeline .venv/bin/pipeline replay /srv/clips/replays/<irgendein>.replay
```

### B4. Dienste
```bash
cp deploy/systemd/clip-bot.service deploy/systemd/clip-aufraeumen.* /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now clip-bot
journalctl -u clip-bot -f            # "Bot läuft." erscheint
# Aufräumen erst nach einem Blick auf den Probelauf aktivieren
# (Timer: stündlich versuchen, höchstens einmal am Tag – nur wenn der große Host gerade läuft):
sudo -u pipeline .venv/bin/pipeline aufraeumen --liste
systemctl enable --now clip-aufraeumen.timer
```

### B4b. Alte Matches nachholen (Rückstand)
`deploy/rueckstand.sh` schickt alle Replays, die noch nicht verarbeitet sind, nacheinander durch
`prepare → analyze → decide → render` (älteste zuerst). Es ist beliebig oft neu startbar, wartet bei
„Speicher offline“ oder „Sperre“ und lässt zwischen den Schritten n8n-Aufträge vor.
```bash
sudo -u pipeline /opt/clip-pipeline/deploy/rueckstand.sh --liste        # nur anzeigen, was offen ist
systemd-run --unit=clip-rueckstand --uid=pipeline --gid=pipeline \
  -p WorkingDirectory=/opt/clip-pipeline --collect /opt/clip-pipeline/deploy/rueckstand.sh
tail -f /var/lib/clip-pipeline/rueckstand.log                           # Fortschritt ansehen
```

### B5. Tailscale und SSH für n8n
```bash
curl -fsSL https://tailscale.com/install.sh | sh && tailscale up    # Link im Browser bestätigen
```
Auf dem vServer (im n8n-Container oder auf dem Host) einen eigenen Schlüssel erzeugen:
`ssh-keygen -t ed25519 -f n8n_pipeline -C n8n`. Den **öffentlichen** Teil im LXC eintragen:
```bash
chmod +x /opt/clip-pipeline/deploy/n8n-lauf.sh
sudo -u pipeline mkdir -p ~pipeline/.ssh
echo 'command="/opt/clip-pipeline/deploy/n8n-lauf.sh",no-pty,no-port-forwarding,no-agent-forwarding <inhalt von n8n_pipeline.pub>' \
  | sudo -u pipeline tee -a ~pipeline/.ssh/authorized_keys
```
*Lerneffekt:* `command=` nagelt den Schlüssel auf ein Skript fest. Das Skript lässt nur die Befehle aus dem
n8n-Vertrag durch (`prepare|analyze|decide|render --session <ID>`, `highlight --id <ID> --tage <n>`,
`status`) und prüft jedes Wort – `; rm -rf /` oder `../` werden abgewiesen (getestet).

---

## C. n8n (vServer) – nach dem Vertrag in CLAUDE.md

Die Pipeline erfüllt den Vertrag: `/opt/clip-pipeline/bin/pipeline <befehl>`, Logs auf stderr, letzte Zeile
auf stdout = eine JSON-Zeile, Exit 0 = ok, Sperre per `flock` (ein zweiter Schritt wartet), idempotent.

Deine drei Workflows im Projektordner passen dazu (geprüft am 23.09.2026):
- `1-match-verarbeiten.json`: Webhook → Session-ID prüfen → SSH `prepare` → `analyze` → `decide` → `render`
  → Telegram-Info mit `clips`, `top_label`, `top_score`.
- `2-highlight-video.json`: alle 2 Wochen freitags 18 Uhr → `highlight --id highlight-<datum> --tage 14`
  → Telegram-Info mit `clips` und `dauer`. Das Highlight kommt als Vorschau zur **Freigabe in den Bot**;
  schläft der große Host, weckt ihn die Pipeline (`wol_mac`).
- `3-fehler-alarm.json`: in den beiden anderen unter *Einstellungen → Error Workflow* auswählen.

Nach dem Import eintragen: `DEINE_CHAT_ID` (3×), SSH-Credential (Host = Tailscale-Name des LXC, User
`pipeline`, Private Key `n8n_pipeline`), Header-Auth-Credential für den Webhook (Name `X-Pipeline-Token`,
Wert = derselbe Token wie `WebhookToken` in `windows\uebertragung.psd1`), Telegram-Credential (Bot-Token).

Exit-Codes: 0 ok · 1 Fehler · 2 Aufruf · 3 Speicher offline (großer Host schläft) · 4 Sperre nicht bekommen.

**Wichtig:** Für diesen Bot-Token niemals einen „Telegram Trigger“ in n8n anlegen – den Empfang macht
ausschließlich `clip-bot`. Sonst meldet der Bot „Conflict“.

Neue Clips verschickt nicht n8n, sondern der Bot selbst (Outbox, alle 30 s). n8n sieht nie ein Video.
Einen KI-Agenten braucht n8n nicht: die einzige KI-Entscheidung (`decide`) läuft per `claude -p` auf dem Mini.

---

## D. Gaming-PC (Windows)

1. `windows\uebertragung.beispiel.psd1` nach `windows\uebertragung.psd1` kopieren, anpassen:
   `Ziel = '\\<grosser-host>\clips'`, `ZielHost`, `WakeOnLanMac`, `WebhookUrl`, `WebhookToken`
   (derselbe Token wie im n8n-Webhook; die Datei steht in `.gitignore`).
2. Probelauf: `powershell -ExecutionPolicy Bypass -File windows\Uebertragung.ps1 -Probelauf`
3. Aufgabe einrichten: `powershell -ExecutionPolicy Bypass -File windows\Aufgabe-einrichten.ps1`
4. Log: `%LOCALAPPDATA%\ClipPipeline\uebertragung.log`

In Fortnite: Einstellungen → Replays aufzeichnen = **Ein**. Lizenzierte Musik im Spiel **aus**.

---

## E. Telegram

1. In Telegram `@BotFather` → `/newbot` → Token in `.env` (`TELEGRAM_BOT_TOKEN`).
2. `@userinfobot` schreiben → deine ID in `.env` (`TELEGRAM_ALLOWED_USER_ID`).
3. Deinem neuen Bot einmal `/start` schicken (sonst darf er dir nicht schreiben).
