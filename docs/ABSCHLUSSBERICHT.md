# Abschlussbericht Sprint „Regisseur“

Stand: 24.09.2026 · Branch `sprint-regisseur` · Draft-PR https://github.com/baddieday/baddieday-voting/pull/1

> **Nachtrag 25.09. (E19):** Der Datenweg ist neu – Puffer auf dem Mini, pve-big nur noch nachts als Lager.
> Einführung Schritt für Schritt: `docs/PUFFER.md`. Die Host-Schritte unten bleiben gültig und gehören zu R3.

## Das Wichtigste zuerst
- **Die Sitzung lief nicht auf dem Mini**, sondern in einem Cloud-Container ohne Heimnetz. Alles ist gebaut und
  hier getestet – mit künstlichen Videos, künstlicher Sprache (espeak) und echten NCS-Titeln. **Nichts davon lief
  bisher an deinen echten Clips.**
- Die zweite Sitzung („root-b0“), mit der ich die Arbeit teilen sollte, läuft auf dem **vServer**, nicht im LXC.
  Sie hat die Umgebung geprüft (Teil A), mehr ging von dort nicht: Der LXC „clips“ war im Tailnet **offline**,
  pve-big ist gar nicht im Tailnet. Die Aufgaben B–D in `docs/AUFGABEN-VOR-ORT.md` braucht eine Sitzung im LXC.
  Sie meldete außerdem, dass sie auf deinen ausdrücklichen Wunsch auf dem vServer einen Benutzer `claude` mit
  sudo und die gh-CLI eingerichtet hat (Regel 4 „VPS nicht verändern“ – deine Entscheidung, nur zur Kenntnis).
- **pve-big wurde nie geweckt** (von hier unmöglich). Das Sicherheitsnetz ist fertig, aber noch nicht
  eingerichtet: Solange der SSH-Zugang zu pve-big fehlt, weckt die Pipeline ihn gar nicht (Regel 3).
  **Das gilt jetzt auch für den bisherigen Weckweg** (n8n-Schritte, `/paket`, `highlight`): Schläft pve-big,
  enden sie mit Exit 3 und n8n meldet einen Fehler, statt ihn unkontrolliert zu wecken (E12, abschaltbar mit
  `[big].alter_weckweg_nur_mit_aus = false`). Während du spielst, weckt der Gaming-PC pve-big wie bisher selbst.
- **Der Lern-Bot hat noch nichts gesendet**: `LEARN_BOT_TOKEN` fehlte. Dieser Bericht liegt deshalb im Repo.
- Bestehende n8n-Workflows, der Clip-Bot und der VPS sind unverändert.
- Tests: 59 → 111 (alle grün, ein optionaler Whisper-Test läuft mit `CLIP_TEST_WHISPER=1`).
- Ein unabhängiger Prüfer hat den Sprint-Code durchgesehen: 8 Befunde (darunter 2 schwere zu Regel 3),
  alle behoben und mit Tests abgesichert (E12).

## Was läuft (hier ausprobiert)
| Teil | Ausprobiert mit | Nicht ausprobiert |
|---|---|---|
| Sicherheitsnetz pve-big (`pipeline big`, Timer) | gefälschtem pve-big (SSH-Skript), Frist, Sperre, Halten-Marken | echtes SSH, echtes Herunterfahren |
| Bestandsaufnahme | nachgebautem Speicher (Game/Chat-Spuren, stumme Spur) | echtem /srv/clips, echter iGPU |
| Material-Kopie | Replays/Sessions/Videos, Platzmangel → letzte 90 s | echtem Wecken |
| Stimmung | künstlichen Knallen, Replay mit Tod, **echtem Whisper small** (espeak-Stimme) | echter Mikro-Spur |
| Musik | Klick-Spuren 90/128/150 BPM (±1 %), 10 echte NCS-Titel geladen | – |
| Regisseur (`compose`) | 16–32 künstlichen Momenten, beide Formate | echten Momenten |
| Entwurf rendern | CPU: Farb-Test prüft jedes Segment, Dauer ±2 Bilder, < 48 MB | VA-API (nur Befehlsaufbau), NVENC |
| Lern-Bot | Fake-Telegram: Entwurf senden, 👍/👎 + Gründe, Musik annehmen, Abendstand | echter Bot |
| Lernen | Gründe verändern den nächsten Entwurf (andere Musik, kürzer, ruhiger) | echte Bewertungen |
| Session vorbei | Windows-Helfer unter PowerShell 7 (Linux), Mini-Seite | Windows PowerShell 5.1 |

## Wie man es benutzt
Kurz (ausführlich: `docs/REGIE.md`):
```bash
pipeline stimmung                          # Momente bekommen eine Stimmung (Whisper + Regeln + 1× Claude)
pipeline musik ncs --stimmung episch       # Musik laden (Quellenangabe wird mitgespeichert)
pipeline entwurf-neu --format short        # Schnittliste + Entwurf -> der Lern-Bot schickt ihn
pipeline render-entwurf <id> --final       # volle Qualität auf pve-big (1× wecken, danach aus)
```
Im Lern-Bot: Audio mit Quellenangabe schicken, `/entwurf short`, 👍/👎 und Gründe tippen, `/lernstand`.

## Getroffene Entscheidungen (Details: `docs/ENTSCHEIDUNGEN.md`)
E1 bauen und testen statt so tun als ob · E2 Branch `sprint-regisseur` · E3 neue Tabellen in portablem SQL
(Postgres-Umzug vorbereitet) · E4 pve-big per SSH mit `command=`-Sperre, Wecken nur mit nachgewiesenem
Herunterfahren, Frist · E5 Fortnite-Replays immer ganz, nur Videos kürzen · E6 Stimmung = Regeln + 1× Claude ·
E7 eigene Beat-Analyse (numpy), NCS als Musikquelle, Stimmung aus der Quelle vor gemessener Energie ·
E8 eigenes Schnittlisten-Format, Übergänge mittig auf dem Beat, gedeckelte Entwurfs-Bitrate · E9 Lern-Bot als
eigener Dienst · E10 „Session vorbei“ per Datei statt n8n · E11 Arbeitsteilung mit der Vor-Ort-Sitzung ·
E12 Prüfungsbefunde, u. a. alter Weckweg nur noch mit gesichertem Herunterfahren.

## Nötige Host-Änderungen (in dieser Reihenfolge)

### Im LXC „clips“ (pve-mini)
1. Code: erst in `/opt/clip-regie` testen (siehe `docs/AUFGABEN-VOR-ORT.md`), nach Review Branch in `main` und
   in `/opt/clip-pipeline` `git pull && .venv/bin/pip install -e .[whisper]` (neu: numpy, faster-whisper; das
   Whisper-Modell „small“ lädt beim ersten Lauf ~480 MB nach `~pipeline/.cache`).
2. Neuer Telegram-Bot beim @BotFather → `LEARN_BOT_TOKEN=…` in `/opt/clip-pipeline/.env`, dem Bot `/start` schicken.
3. SSH-Schlüssel für pve-big:
   ```bash
   sudo -u pipeline mkdir -p /var/lib/clip-pipeline/.ssh
   sudo -u pipeline ssh-keygen -t ed25519 -N '' -C clip-waechter -f /var/lib/clip-pipeline/.ssh/pve-big
   ssh-keyscan <pve-big> | sudo -u pipeline tee /var/lib/clip-pipeline/.ssh/known_hosts   # wenn pve-big läuft
   ```
4. `config/lokal.toml`: `[big] ssh_ziel = "root@<pve-big>"`, `host = "<pve-big>"`; `[speicher] wol_mac = "…"`.
5. Dienste: `clip-big-waechter.timer`, `clip-lernbot.service`, optional `clip-sitzungen.timer` nach
   `/etc/systemd/system/`, `daemon-reload`, `enable --now`.
6. Platz: Die Material-Kopie liegt in `/var/lib/clip-pipeline/material` – bei 16 GB LXC-Disk vorher
   `pipeline material --probelauf` ansehen; ggf. Disk vergrößern (`pct resize <id> rootfs +50G`) oder eigenen
   Mountpoint anlegen.

### Auf pve-mini (Proxmox-Host) – iGPU für VA-API
7. In `/etc/pve/lxc/<id>.conf`: `dev0: /dev/dri/renderD128,gid=<gid der Gruppe render im LXC>`; im LXC
   `apt install mesa-va-drivers vainfo`, Benutzer `pipeline` in Gruppe `render`. Prüfen: `pipeline bestand`
   → „Test-Encode ✅“.

### Auf pve-big
8. `deploy/big/clip-big-steuer.sh` nach `/usr/local/sbin/clip-big-steuer` (chmod 755), darin `SPEICHER` auf den
   echten Pfad setzen. (Für `final` rendert es als Benutzer `clips`, nicht als root.)
9. In `/root/.ssh/authorized_keys` (Proxmox: `/etc/pve/priv/authorized_keys`) eine Zeile:
   `command="/usr/local/sbin/clip-big-steuer",restrict,from="<IP des Mini-LXC>" ssh-ed25519 AAAA… clip-waechter`
10. Einmal, während pve-big läuft: im LXC `pipeline big pruefen` → erst danach darf die Pipeline ihn wecken.
11. Optional (Final-Render mit NVENC): Nvidia-Treiber + ffmpeg mit NVENC auf pve-big, Repo nach
    `/opt/clip-regie` mit venv. Ohne das bleiben die Entwürfe (720p) das Endprodukt.

### Nach dem Sprint
12. `[big].frist` in `config/pipeline.toml` bzw. `lokal.toml` **leeren** – sonst weckt die Pipeline pve-big nach dem
    28.09., 20:00 nie mehr (auch nicht für n8n-Aufträge).
13. Gaming-PC (ab Dienstag, optional): `SessionVorbeiMinuten = 20` in `windows\uebertragung.psd1`, vorher `-Probelauf`.

## Offene Punkte
- **Echte Daten:** Wortlisten, Schwellen (`spitze_lu`, `jubel_lu`) und Stimmungs-Regeln sind an künstlichem
  Material entworfen. Erst die Vor-Ort-Sitzung (Aufgabe D) zeigt, ob die Mikro-Spur erkannt wird und die
  Verteilung plausibel ist.
- **Musik-Energie** trennt laut gemasterte Titel schlecht → Quellen-Stimmung zählt zuerst (E7). Mit echten
  Bewertungen („Musik passt nicht“ / „Stimmung getroffen“) lernt der Regisseur nach.
- **VA-API und NVENC** sind nur im Befehlsaufbau getestet.
- **Postgres + Warteschlange:** vorbereitet (portables SQL, Logik ohne SQLite-Eigenheiten), nicht umgesetzt.
- **Untertitel** in Shorts (Whisper ist jetzt da) und Umstellung von `highlight` auf den Regisseur.
- **CLAUDE.md, Abschnitt „Entscheidungen“:** laut deinen Regeln nur nach Rückfrage – Vorschlag: E3, E4, E7, E9, E10
  dort als Einzeiler aufnehmen.
