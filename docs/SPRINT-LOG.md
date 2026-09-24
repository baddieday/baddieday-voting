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
