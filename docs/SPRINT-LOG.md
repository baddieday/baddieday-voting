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

### 25.09.: Regisseur 2.0 – Stufe 0 (Vorarbeit: schneller rendern, Messpunkt)
- 🧪 **`entwurf._bild`:** `fps=` ist jetzt der erste Filter je Segment (alle weiteren Filter sehen nur noch die Bilder,
  die ins Ergebnis kommen – bei 60-fps-Aufnahmen die Hälfte). Short: der unscharfe Hintergrund wird auf b/4 × h/4
  weichgezeichnet (`boxblur=5:2` statt `20:2` auf voller Fläche, gleich unscharf) und wieder hochskaliert; das Spielbild
  bleibt wie vorher. 16:9: nur fps zuerst.
- **Lokal gemessen** (Container, 4 CPUs, parallel andere Agenten; nur der Bildteil, Quelle 1080p60 testsrc2 10 s, ohne
  Encoder): 720×1280 **9,6 → 3,1 s**, 1080×1920 **17,3 → 3,8 s**. Einzelbild alt/neu: PSNR 38,6 dB (im reinen
  Unschärfe-Band 37 dB) – optisch gleich. Farbtests je Segment, Bild-/Tondauer und Größe weiter grün.
- 🧪 **Neu: `pipeline render-entwurf <id> --messen`** – rendert wie ein Entwurf, aber in einen eigenen Temp-Ordner neben
  der Schnittliste, löscht nur diesen, Datenbank unverändert; letzte Zeile
  `{"entwurf", "sekunden", "encoder", "dauer_s", "aufloesung", "mb"}`. Nicht zusammen mit `--final`.
- Tests: `MitRegieMaterial.setUp` setzt `[regie.effekte] an = false`, damit alle Bestandstests ohne Effekte laufen;
  neu `BildGraph` (String-Test fps zuerst, Unschärfe auf b/4 × h/4) und `Messen` (keine Datei bleibt, DB gleich, JSON).
- 🏠 **Offen – Messung auf dem Mini (Frage 2):** im LXC clips als `pipeline`:
  `sqlite3 /var/lib/clip-pipeline/pipeline.db "SELECT quelle, fps, COUNT(*) FROM aufnahmen GROUP BY 1,2"` ·
  `ffmpeg -version | head -1` · vorher (alter Stand): `time /opt/clip-pipeline/bin/pipeline entwurf-neu --format short`
  · nachher (Stufe 0 eingespielt, dieselbe Schnittliste): `/opt/clip-pipeline/bin/pipeline render-entwurf <id> --messen`.
  Werte hier eintragen.

### 25.09.: Regisseur 2.0 – Stufe 1, Teil A (Effekt-Plan, Schnittliste v4)
- 🧪 **Neu `effekte.py` (Planer, reines Python):** Profile je Stimmung, Ketten wie die Vorbewertung, Finisher-Punch +
  Bass-Hit, Mini-Punch + Tick, ein Kill-Titel je Serie am Ende (Anker = mein Umhauen), VICTORY ROYALE, Zähler (nur
  Short), Tod-Punch + Einschlag, Meme + Pop, Riser, Whoosh, Zoom-Budget, Beat-Akzente. Spielbild clean: kein Blitz,
  kein Wackeln, kein Glitch-Ereignis. `zeitleiste(liste)` und `uebergangs_fenster(liste)` für den Renderer.
- 🧪 **Schnittliste v4:** `effekte` oben, je Segment `kill_s`, `effekte` (≤ 24), `rolle`/`lupe` (für Stufe 4/5 schon
  geprüft), neue Übergänge whip/zoom/glitch/squeeze/dissolve; `schema.py` kennt `maxItems` und lehnt NaN/Infinity ab.
  v3-Listen bleiben gültig.
- 🧪 **regie.py:** schreibt version 4; Übergänge über `effekte.uebergang` (aus = wie vorher, zeichengleich getestet);
  Kandidat hat `max_gruppe` und `victory`. `[regie.effekte]` in `config/pipeline.toml` (an = true).
- Tests: `tests/test_effekte_plan.py` (31 Tests, < 1 s, ohne ffmpeg).
- **Offen:** Renderer (Teil B) und Klänge (Teil C) fehlen noch – bis dahin kann das alte `entwurf.py` die neuen
  Übergangsnamen nicht rendern. Vor dem Einspielen auf den Mini also Teil B abwarten oder `an = false` setzen.

### 25.09.: Regisseur 2.0 – Stufe 1, Teil B (Renderer: Effekte ins Bild)
- 🧪 **Neu `effekt_filter.py`:** übersetzt den Plan in ffmpeg-Filter, entscheidet nichts. **Zoom nur aufs Spielbild**
  (je Segment `scale` mit `eval=frame`, mittig per `overlay` auf das unveränderte Bild – im Short vor dem Einsetzen in
  den unscharfen Hintergrund, im 16:9 vor dem Rand); neue Übergänge → xfade (whip→slideleft, zoom→zoomin,
  glitch→pixelize, squeeze→squeezeh, dissolve); Whip/Glitch-Filter nur in der Blende; Look im Short je Segment auf
  Spielbild und kleinem Hintergrund, im 16:9 einmal nach den Übergängen; Kill-Titel/Zähler animiert (Pop-in,
  Ein-/Ausblenden, Hineingleiten). Short: Zähler zwischen clip-battle.de und Spielbild, Titel darunter – auch beim
  Pop nie im Spielbild (y 34–66 %). 16:9: kein Zähler, Titel mittig nur in der Blende.
- 🧪 **`entwurf.py`:** hängt Zoom je Segment und die Effektkette nach der xfade-Kette ein, mischt die Klänge aus
  `sfx.py` nach dem Ducking dazu (WAVs als weitere Eingänge hinter der Musik), Ende weiter `…null[vout]`. **Aus = heute:**
  mit `an = false` bzw. version 3 ist der Graph zeichengleich mit dem von vorher (fester Vergleichstext im Test).
- 🧪 **Graph-Größe:** Der Graph ist ein Argument auf der Befehlszeile (Linux: ≤ 128 KB je Argument). `compose` prüft
  ihn beim Planen (`entwurf.graph_fehler`) und bricht über 64 KB mit „Schnittliste zu groß“ ab. 40 Segmente mit 400
  Ereignissen (Planer-Grenze): 59 KB. Zahlen deshalb kurz (höchstens 4 Nachkommastellen, ohne Nullen am Ende).
- **Zwei Abweichungen von der Spezifikation (§4), beide gemessen:** Glitch mit `chromashift` statt `rgbashift` – ein
  RGB-Filter lässt ffmpeg jedes Bild umrechnen, auch außerhalb der 0,2-s-Blende (lokal 9 ms je Bild bei 720×1280);
  Look mit `eq` + `colorcorrect` statt `eq` + `curves` (8 ms statt 2 ms je Bild). Beide rechnen direkt in YUV.
- **Lokal gemessen** (CPU-Zeit, nur Filtergraph, 45-s-Short aus 1080p60 testsrc2, 6 Segmente, je 2 Punches, Akzent,
  Titel, 2 Zähler, Whip/Zoom/Glitch-Übergänge): ohne Effekte 46,6 s, Zoom 48,1, Look 48,1, Texte 46,6, **alles 50,3 s
  (+8 %)**. Der erste Entwurf (Look und Zoom-Overlay auf jedem Bild, global) lag bei +27 %.
- Tests: `tests/test_effekte_graph.py` (20 Tests, < 1 s; u. a. fester Vergleichstext „Aus = heute“, VA-API-Befehl),
  `tests/test_effekte_render.py` (3 Render-Tests, lokal 25–35 s unter Last; optional `CLIP_LEISTUNG=1`: 45-s-Short
  mit/ohne Effekte), Hilfen in `tests/effekt_hilfen.py`. Ende-zu-Ende lokal: `compose --format short` mit Effekten,
  gerendert 44,8 s, Bild/Ton genau.
- 🏠 **Offen – Messung auf dem Mini:** Stufe 1 einspielen, einen Short mit Effekten komponieren und
  `pipeline render-entwurf <id> --messen` – Sekunden je Video-Sekunde mit der Stufe-0-Messung vergleichen (Ziel
  ≤ 1,2×, ≤ 90 s). Klänge und Look einmal am Handy ansehen/anhören.

### 25.09.: Regisseur 2.0 – Stufe 2 (Lernen: „🎆 zu viele Effekte“ / „💥 mehr Action“)
- 🧪 **Zwei neue Gründe** im Lern-Bot, hinten angehängt (alte Bewertungen bleiben gültig): 🎆 zu viele Effekte
  (`effekte_viel`) und 💥 mehr Action (`action`). Die Knöpfe stehen jetzt in 4 Reihen zu je 2, darunter ✅ fertig.
  `pipeline bewerte --grund …` nimmt die Gründe aus derselben Liste (`regie_lernen.GRUENDE`).
- 🧪 **Neue Lern-Parameter** in `regie.PARAMETER`: `effekt_staerke` (je Stimmung, Start 1,0) und `effekt_hektik` (1,0).
  Regeln für die **Hauptstimmung** des Entwurfs: 🎆 ×0,85, 💥 ×1,15 (0,1 … 1,5), beide zugleich nichts; 😵 zu hektisch
  zusätzlich Hektik ×0,9 (0,3 … 1,3). Vorgaben: `[regie.vorgaben] effekt_hektik` und
  `[regie.vorgaben.effekt_staerke] <stimmung> = 0 … 1,5` (0 = ohne Effekte, bleibt 0).
- **Hektik nach deiner Entscheidung „Spielbild clean“:** Blitz und Wackeln gibt es nicht mehr, darum dämpft die Hektik
  nur noch die **Beat-Akzente**. (Eine zusätzliche Dämpfung der Glitch-Übergänge über ein neues Feld
  `uebergang.staerke` ist in der Prüfung wieder herausgeflogen – die Spezifikation sagt „Übergänge mit Stärke 1“.)
- 🧪 `/lernstand` bzw. `pipeline lernstand`: neue Zeile „Effekte: episch 1.0 · … · Hektik 1.0“ (Vorgaben
  gekennzeichnet). Unter jedem Entwurf: „✨ Look cinematic · 23 Impacts“ (Impacts = Ereignisse im Effekt-Plan).
- Tests: `tests/test_regie.py` Klasse `EffekteLernen` (8 Tests ohne Video, u. a. Regression: eine feste Folge alter
  Bewertungen ergibt genau die Parameter von vorher), dazu in `test_lernbot.py` (Knöpfe, ✨-Zeile) und
  `test_effekte_plan.py` (Hektik dämpft nur Akzente, `effekt_staerke.episch = 0` → episch ohne Effekte, Schema).
- Ausprobieren: im Lern-Bot unter einem Entwurf 👎 → „🎆 zu viele Effekte“ → ✅; danach `/lernstand`.

### 25.09.: Regisseur 2.0 – Prüfung der Stufen 0–2 (Befunde behoben)
- 🧪 **16:9-Titel bei Jump-Cuts:** Lag der Finisher im ersten Teil eines Moments, fiel der Titel still weg (nach dem
  Teil kommt nur der Jump-Cut). Jetzt gehört er in die Blende nach dem **letzten Teil** des Moments.
- 🧪 **Short, Übergang „zoom“:** xfade zoomin vergrößert das ganze Bild (mit ffmpeg nachgemessen: nach einem Drittel
  der Blende füllt das Spielbild die ganze Höhe). Titel und Zähler enden jetzt spätestens am Anfang dieser Blende.
- 🧪 **Aufnahmen höher als 16:9 (4:3, 16:10, 5:4):** `rendere` misst die Quellen, die Texte rücken mit dem höheren
  Spielbild mit; ist kein Platz, fällt der Text weg (vorher lag z. B. bei 4:3 der Titel ~44 px im Spielbild).
- 🧪 **Vereinfacht:** `uebergang.staerke` ist wieder weg (Schema, Planer, Renderer, Test-Hilfe) – Übergänge immer
  mit Stärke 1 wie in §4; „zu hektisch“ dämpft nur die Beat-Akzente.
- Tests: Render-Test prüft jetzt auch das Pop-Maximum (Bild 152) und das ganze Spielbild außer dem Quadrat; neuer
  Render-Test mit 4:3-Quelle. Gegenprobe mit absichtlich falschem Code (alte 16:9-Annahme, Zähler im Spielbild):
  beide werden an den Pixeln erkannt.
- **„Aus = heute“** heißt: der Graph ist mit `an = false` zeichengleich mit derselben Liste ohne Effekt-Plan. Stufe 0
  gilt immer – deshalb Stufe 0 **als eigenen Commit** einspielen, dann ist `git revert` der Rückweg.
- **Offen – deine Entscheidung:** (1) Im 16:9 bekommt der Höhepunkt (letzter Moment) nie einen Titel, weil danach
  keine Blende mehr kommt – eine Ausnahme für den letzten Moment? (docs/REGIE.md, „Offen“). (2) Zwei getrennte
  Serien im selben Moment: im 16:9 kommt nur der höhere Titel in die eine Blende danach – so lassen?
- ✅ **Ganze Suite** (Stufen 0–2 gemeinsam, lokal ffmpeg 6.1): 495 Tests grün, 3 übersprungen (u. a. Leistung ohne
  `CLIP_LEISTUNG=1`), 21 min unter Last. Nichts committet – Vorschlag: Stufe 0, Stufe 1 (A+B+C), Stufe 2 und diese
  Prüfung als getrennte Commits.
