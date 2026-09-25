# Sprint-Log „Regisseur“ (24.09. – Mo 28.09.2026, 20:00)

Legende: ✅ fertig und hier getestet · 🧪 gebaut, nur mit künstlichem Material getestet · 🏠 braucht das echte System · ⛔ Blocker

## Blocker / Wichtig zuerst
- ⛔ **Falsche Umgebung:** Diese Sitzung läuft nicht im LXC „clips“, sondern in einem Cloud-Container ohne
  Zugang zum Heimnetz (kein `/srv/clips`, kein `/dev/dri`, kein Tailscale, kein pve-big, keine `.env`).
  → Entscheidung E1: alles bauen und hier testen, auf dem Mini nur noch installieren und starten.
- ⛔ **Kein `LEARN_BOT_TOKEN`:** Der Lern-Bot kann von hier nichts senden. Entwürfe, Stand und Bericht stehen
  deshalb in dieser Datei und im Chat.
- pve-big wurde **nicht** geweckt (von hier unmöglich).

## Stand

### 24.09.
- Pflichtlektüre gelesen: CLAUDE.md, PLAN.md, README.md, START-PROMPT.md, docs/SERVER.md, 3 Workflows.
- Branch `sprint-regisseur` angelegt; Ausgangslage 59 Tests grün.
- Werkzeuge im Container: ffmpeg 6.1 (libx264, h264_vaapi, h264_nvenc eingebaut), espeak-ng (künstliche
  deutsche Sprache zum Testen von Whisper), faster-whisper.
- ✅ Ziel 1 Sicherheitsnetz: `pipeline big …`, Timer `clip-big-waechter` (10 min), Halten-Marken, Frist,
  Weckt nur mit nachweislich funktionierendem Herunterfahren. 🏠 SSH-Zugang zu pve-big fehlt noch (Host-Änderung).
- 🧪 Ziel 2 Bestandsaufnahme: `pipeline bestand [--bericht docs/BESTAND.md]` – Replays, Videos, Mikro-Spur per
  Pegel, VA-API per Test-Encode, Platz. Hier: kein /dev/dri → VA-API ❌ (erwartet). 🏠 echter Lauf auf dem Mini.
- 🧪 Ziel 3 Material: `pipeline material [--probelauf]` – 1× wecken, SHA-256, Platzmangel → letzte 90 s.
- ✅ Ziel 4 Stimmung: `pipeline stimmung` – Regeln + 1× Claude; Whisper small (de) hier echt getestet
  (espeak-Stimme). 🏠 Mit echter Mikro-Spur prüfen, ob die Wortlisten passen.
- ✅ Musik: `pipeline musik` – eigene Beat-Analyse (±1 % Tempo, ±15 ms Beats), 10 NCS-Titel probeweise geladen.
- ✅ Ziel 6 Regisseur: `pipeline compose` – Bogen, Abwechslung, Musik nach Stimmung/Tempo, Schnitte auf dem Beat.
- ✅ Ziel 7 Rendern: `pipeline render-entwurf [--final]` – CPU hier echt (Farb-Test je Segment), VA-API/NVENC
  nur Befehlsaufbau. Zwei stille xfade-Fallen gefunden und behoben (E8).
- ✅ Ziel 8 Lernen: `regie_lernen.py` – Gründe wirken im nächsten compose (getestet).
- ✅ Ziel 5 Lern-Bot: `pipeline lernbot` – ohne Netzwerk getestet. ⛔ Token fehlt → nichts gesendet.
- ✅ Ziel 9 Session vorbei: vorbereitet (Standard aus), unter PowerShell 7 getestet.
- Du hast gebeten, die Arbeit mit der anderen Online-Sitzung zu teilen. Diese Sitzung („root-b0“) läuft aber auf
  dem **vServer**, nicht im LXC. Der LXC „clips“ ist im Tailnet offline, pve-big ist nicht im Tailnet. Deshalb
  konnte sie nur Teil A (Umgebung) erledigen. Sie hat mir geschrieben; umgekehrt geht es nicht (E11).
- Unabhängige Prüfung des Sprint-Codes: 8 Befunde, alle behoben (E12).
- Beispiel-Short mit echter NCS-Musik gerendert und dir im Chat geschickt (Testbilder statt echter Clips).
- CT 102 startete nicht (toter NFS-Pfad als `mp0`) → Rettungsskript, von dir ausgeführt; CT startet jetzt
  immer (E13). Echte Clips über `regie-starten.sh`: Musik, Stimmung der besten 40, Lern-Bot, erste Entwürfe.
- `clip-leerlauf` für pve-big (E14): Probelauf-Tests, jetzt `einrichten.sh` für den scharfen Betrieb.
- Lernschleife (E15): nach ✅ sofort der nächste Entwurf; weckt pve-big nur mit gesichertem Aus.
- Du: „Warum immer die gleichen Clips mit anderer Musik?“ → Ursache gefunden und behoben (E16).
- Du: „Warum ist pve-big noch an?“ – clip-leerlauf war noch nicht installiert. Dabei zwei Fehler gefunden, die
  ihn auch danach wachgehalten hätten (eigene Statusdatei, Lesen über NFS) → leere Marke. Danach unabhängige
  Prüfung des ganzen Mechanismus: 9 bestätigte Befunde, alle behoben (E17).

### 24.09. abends (neue Sitzung, am Handy mit dir)
- **Zugang:** Diese Sitzung ist per Tailscale (flüchtiges Gerät „claude-cloud“, verschwindet nach Sitzungsende) im
  Tailnet und hat root per Tailscale SSH auf pve-big und pve-mini. Dafür nötig waren: eine SSH-Regel in der Tailnet-Policy
  (`check`-Modus: deine eigenen Geräte, Bestätigung per Link, 12 h gültig), `tailscale up --reset --ssh --accept-dns=false`
  auf pve-big (war nie angemeldet – das alte `tag:heim` war in der Policy nicht definiert) und dasselbe mit
  `--force-reauth` auf pve-mini (Schlüssel war seit 23.09. abgelaufen und ebenfalls mit `tag:heim` hängen geblieben).
  Schlüssel-Ablauf ist für beide Server abgeschaltet.
- **Warum pve-big seit Stunden lief:** Auf pve-big war die ALTE Fassung von clip-leerlauf (11:46) installiert, die jede
  Minute `.leerlauf.json` schrieb und sich damit selbst wach hielt. Mit deinem OK ersetzt durch den Stand 22fe26c
  (Prüfsumme gegen git geprüft, alte Fassung als `/usr/local/sbin/clip-leerlauf.alt`), alte Statusdatei gelöscht.
  **Ergebnis: pve-big hat sich um ca. 20:30 selbst abgeschaltet.**
- **Gemessen (für E18/E19):** Aufnahmen ~2 GB je Spieltag (max 5,9 GB), Replays ~0,07 GB; 2–5 Matches pro Abend,
  Wochenende bis 17; Verarbeitung je Match Median 14 s, Schnitt 63 s, max 5 min (rueckstand.log, 82 Matches);
  Mini: ~141 GB frei im Thin-Pool, CT 102 mit 8 Kernen/12 GB/iGPU, noch kein Samba.
- **Stand auf dem Mini:** Produktion `/opt/clip-pipeline` = main (5ee5dec), `/opt/clip-regie` = 49f0608 (vor den
  Leerlauf-Korrekturen; der Lern-Bot läuft von dort). In beiden lokal.toml steht `wol_mac` → die Produktion weckt
  pve-big bei n8n-Schritten und /paket ohne Bedingung. Aktiv ist nur clip-aufraeumen.timer (weckt nicht).
- **E18 Stufe 1 analysiert:** 6 Leser + Stolperfallen-Prüfer (19 Befunde), Rollen-Panel (Senior Dev, Betrieb, QS, CEO).
- **Architekturvergleich** A (E18) gegen B (Mini-Puffer): einstimmig B → **E19**.
- **E19 gebaut** in 4 Strängen, jeder Teil: Bau mit Tests → Prüfer (Korrektheit/Datenverlust, Tests, Einfachheit) →
  Gegenprüfer je Befund → Nachbessern. Danach Schluss-QS über alles. Details siehe Commits und `docs/PUFFER.md`.
- **Nicht angefasst:** Produktion, n8n, Gaming-PC, Host-Konfigurationen (außer clip-leerlauf auf pve-big, s. o.).

### 25.09. morgens: E19 eingeführt (mit dir am Handy)
- Deine Entscheidungen: sofort einführen · Samba + 96-GB-Puffer ok · **nie löschen, aber warnen** · **pve-big nie nachts
  wecken** (Lüfter) → Abgleich 10:00, Prüfung 11:00, Nachtruhe 22–8 Uhr als harte Grenze · CLAUDE.md angepasst.
- **R0–R2:** Puffer `vm-102-disk-1` (96 GB, `/srv/puffer`), Ordner und Marken, `clip-lvm-status.timer` auf pve-mini;
  Samba im CT (Benutzer `gamingpc`, **Passwort noch nicht gesetzt** – machst du, gebraucht erst in R7).
  Zwei echte Fehler beim ersten Lauf gefunden und behoben: `tune2fs -m 0` scheitert bei eingehängtem Volume (ext4-MMP)
  → jetzt, solange der CT aus ist; Samba lauschte auch auf IPv6 (u. a. öffentliche Adresse) → nur noch IPv4-Heimnetz.
- **R3:** PR #1 mit deinem OK in main übernommen (1529664), Produktion und `/opt/clip-regie` auf main, Pakete mit
  `[whisper]`. Sicherung vorher: `/var/lib/clip-pipeline/vor-e19.sha`, `vor-e19-regie.sha`, `vor-e19.db`.
- **R4:** `.clip-lager` auf pve-big, Übernahme 637 Dateien / 11,1 GB mit SHA-256, 0 Fehler; Delta danach 0.
- **R5:** umgeschaltet – `lokal.toml` beider Checkouts (Sicherung `lokal.toml.vor-e19`), `[big].frist` geleert,
  `/srv/clips → /srv/puffer`, Drop-ins `e19-puffer.conf` für clip-bot/clip-lernbot. Schreibtest mit denselben
  Schutzregeln ok. `lager status`: getrennt, Prüfung ok.
- **R6:** clip-lager.timer 10:00, clip-puffer-pruefen.timer 11:00 aktiv, clip-aufraeumen.timer dauerhaft aus.
  Erster Abgleich von Hand ok (DB-Sicherung im Lager bestätigt), Prüfung ohne Befund.
- **Offen:** Samba-Passwort (pve-mini-Shell: `pct exec 102 -- smbpasswd -a gamingpc`) · **R7 am Dienstag vor dem
  ersten Spiel** (Gaming-PC: neues Skript, psd1-Ziel `\\192.168.178.93\clips`, cmdkey, Probelauf) – kopiert er vorher
  noch nach pve-big, geht nichts verloren, eine Delta-Übernahme holt es nach · R8 (pve-big-Freigabe nur lesen) später.
