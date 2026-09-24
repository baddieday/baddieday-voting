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
