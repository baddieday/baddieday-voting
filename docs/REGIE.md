# Regisseur – Bedienung (Sprint 09/2026)

Aus deinen Clips entstehen automatisch **Zusammenschnitte (16:9, 3–5 min)** und **Shorts (9:16, 30–45 s)** mit
Musik, Schnitten auf dem Beat und Übergängen passend zur Stimmung. Du bewertest die Entwürfe im **Lern-Bot**
mit 👍/👎 und Gründen, und der nächste Entwurf berücksichtigt das.

```
Clips (Pipeline) ─► pipeline stimmung ─► Momente mit Stimmung ─┐
Musik (NCS / Lern-Bot) ─► pipeline musik ─► Tempo, Beats, Energie ─┤
                                                                   ▼
                                      pipeline compose ─► Schnittliste (JSON)
                                                                   ▼
                    pipeline render-entwurf ─► Entwurf < 48 MB ─► Lern-Bot ─► 👍/👎 + Gründe
                    pipeline render-entwurf --final ─► pve-big (NVENC), danach aus            │
                                      ▲                                                    │
                                      └──────────── regie_lernen (nächster compose) ◄──────┘
```

## Befehle

| Befehl | Was |
|---|---|
| `pipeline big status` | Läuft pve-big? Würde der Wächter ihn ausschalten? Darf geweckt werden? |
| `pipeline big pruefen` | (pve-big läuft) Steuerung per SSH testen – **erst danach** weckt die Pipeline ihn überhaupt |
| `pipeline big waechter` | ein Durchlauf des Wächters (sonst per Timer alle 10 min) |
| `pipeline big halten <name> --minuten 60` / `loesen <name>` | pve-big bewusst wach halten / freigeben |
| `pipeline big aus [--sofort]` | von Hand herunterfahren |
| `pipeline bestand [--bericht docs/BESTAND.md]` | Replays, Videos, Tonspuren (Mikro?), VA-API, Platz – weckt nicht |
| `pipeline material [--probelauf]` | Replays, Sessions, Videos auf den Mini kopieren (1× wecken, SHA-256) |
| `pipeline stimmung [--dateien] [--neu] [--ohne-claude] [--ohne-whisper]` | Stimmung je Moment |
| `pipeline musik ncs --stimmung episch --anzahl 3` | NCS-Titel laden (mit Quellenangabe) |
| `pipeline musik hinzufuegen <datei> --quelle "…" [--titel --kuenstler]` | eigenen Titel aufnehmen |
| `pipeline musik analysieren <datei>` · `pipeline musik liste` | Tempo/Energie ansehen |
| `pipeline compose --format short\|zusammenschnitt` | Schnittliste erzeugen |
| `pipeline render-entwurf <id>` | Entwurf rendern (VA-API, sonst CPU) |
| `pipeline render-entwurf <id> --messen` | nur Renderzeit messen: rendert in eine Temp-Datei, löscht nur diese, DB bleibt gleich; letzte Zeile `{"sekunden", "encoder", "dauer_s", "aufloesung", …}` – zum Vorher/Nachher-Vergleich |
| `pipeline render-entwurf <id> --final` | volle Qualität auf pve-big (NVENC), danach sofort aus – nicht im Puffer-Betrieb (Exit 2, weckt nicht; `docs/PUFFER.md` R5) |
| `pipeline entwurf-neu --format short` | compose + Entwurf in einem Schritt |
| `pipeline sitzungen` | „Session vorbei“ vom Gaming-PC: Short des Abends (Timer) |
| `pipeline lernbot` | Lern-Bot (Dienst `clip-lernbot`) |
| `pipeline lernbot-sende --text "…"` / `--datei bericht.md` | Nachricht über den Lern-Bot |

Alle Befehle halten den Vertrag ein: Logs auf stderr, letzte Zeile auf stdout = eine JSON-Zeile, Exit 0 = ok,
3 = Speicher/pve-big nicht verfügbar (nichts geweckt), 4 = anderer Schritt läuft.

## Lern-Bot (Telegram)
- Neuer Bot beim @BotFather, Token als `LEARN_BOT_TOKEN` in `.env` (nicht der Token des Clip-Bots!).
- **Musik schicken:** Audiodatei, Bildunterschrift = Quellenangabe (Pflicht), optional `#episch`,
  `#spannend`, `#lustig`, `#frustriert`, `#chill`.
- **Entwürfe:** kommen automatisch (z. B. nach „Session vorbei“) oder per `/entwurf short`.
  👍/👎 → Gründe an-/abwählen → ✅ fertig. Danach baut der Bot sofort den nächsten (Lernschleife,
  `[lernbot].naechster_nach_bewertung`) und analysiert dabei 10 weitere Clips (`stimmung_je_entwurf`).
  Schläft pve-big, weckt der Bot ihn – aber nur, wenn er danach sicher wieder ausgeht (clip-leerlauf scharf).
- Unter jedem Entwurf steht „🆕 3 neue · 2 schon gezeigt · Auswahl aus 40 Momenten“ und mit Effekten
  „✨ Look cinematic · 23 Impacts“ (Impacts = Ereignisse im Effekt-Plan: Zooms, Titel, Zähler, Klänge;
  später dazu „Hook ✓ · Zeitlupe ✓“). „n Momente“ zählt Momente, nicht Segmente.
- Gründe: 8 Knöpfe in 4 Reihen zu je 2, darunter ✅ fertig.
- `/lernstand` zeigt, was der Regisseur gelernt hat, `/musik` die Titel, `/stand` einen Satz zum Stand.
- Abends um 21:00 kommt ein Satz zum Stand (`[lernbot].abend_uhrzeit`).
- **Publikum (TikTok-Zahlen, Lernschleife):** nach 👍 auf einen Short „📦 Upload-Paket“, nach dem Posten
  `/link <entwurf> <url>` → Post-Nummer; Screenshot der TikTok-Statistik mit `#<post>` (oder von Hand
  `#17 1240 61 6.8 34`); `/publikum` zeigt Zahlen und Score (ab 7 Tagen, Timer `clip-publikum` um 10:00).
  Bedienung, Konfig-Schlüssel und Installation: `docs/PUBLIKUM.md`.

## Selbst steuern – auch ohne Bot
- **Vorgaben** in `config/lokal.toml` (Beispiel mit allen Schlüsseln: `config/lokal.beispiel.toml`):
  `[regie.vorgaben]` für Schnitt-Tempo, Länge, Puffer, Übergänge, Musik-Pegel und bevorzugte Stimmungen,
  `[regie.musik_ziele.<stimmung>]` für Tempo und Energie der Musik. Das sind **Startwerte**: Deine Bewertungen
  verschieben von dort aus weiter. Unbekannte oder unsinnige Werte werden gemeldet und auf Grenzen gestutzt.
- **Bewerten ohne Telegram:** `pipeline bewerte <entwurf> --gut|--schlecht [--grund hektisch --grund lang]`
  (Gründe: `musik`, `hektisch`, `getroffen`, `lang`, `abgeschnitten`, `langweilig`, `effekte_viel`, `action`;
  sie stehen nur einmal im Code, in `regie_lernen.GRUENDE`) – dieselbe Wirkung wie die Knöpfe.
- **Nachsehen:** `pipeline lernstand` (bzw. `/lernstand` im Bot) zeigt Vorgaben und Gelerntes.

## Was die Gründe bewirken
| Grund | Wirkung beim nächsten compose |
|---|---|
| 🎵 Musik passt nicht | dieser Titel bekommt −1 (je Nennung) |
| 😵 zu hektisch | Segmente +15 % länger, Übergänge +10 %; ab +30 % nur jeder 2., ab +70 % jeder 4. Beat; dazu Hektik ×0,9 (Beat-Akzente schwächer, 0,3 … 1,3) |
| 🎯 Stimmung getroffen | Hauptstimmung +0,5; Musikziel dieser Stimmung rückt 20 % zum benutzten Titel |
| ⏳ zu lang | Ziel-Dauer −10 % (bis 60 %) |
| ✂️ abgeschnitten | +0,5 s vor, +0,3 s nach den Kills |
| 🥱 Clips langweilig | jeder Moment dieses Entwurfs −1 Punkt (kommt seltener) |
| 🎆 zu viele Effekte | Effekt-Stärke der Hauptstimmung ×0,85 (bis 0,1) – alle Effekte dieser Stimmung schwächer, schwache fallen unter die Schwelle weg |
| 💥 mehr Action | Effekt-Stärke der Hauptstimmung ×1,15 (bis 1,5) |
| 🎆 + 💥 zugleich | nichts (heben sich auf) |
| 👍 | jeder Moment dieses Entwurfs +0,5 (kommt öfter wieder) |
| 👎 ohne Grund | jeder Moment dieses Entwurfs −0,5 (bei 👎 *mit* Grund lag es nicht an den Clips) |
| 👍/👎 ohne Grund | Hauptstimmung ±0,25 (ab 3 Bewertungen) |

Alles wird bei jedem Lauf aus den gespeicherten Bewertungen neu berechnet und ist begrenzt – ein Ausreißer
kann nichts kaputt machen (je Moment höchstens ±3).

## Abwechslung – warum nicht immer dieselben Clips kommen
Jeder Moment hat Punkte (Kill-Serie 1/3/6/10, Victory +5, Stimmung, Elo, Freigabe, Gelerntes). Früher gewannen
bei jedem Entwurf dieselben Top-Momente; nur die Musik wechselte. Jetzt verliert ein Moment, der im **letzten**
Entwurf war, **70 %** seiner Punkte, einer aus dem vorletzten 35 %, davor 17,5 % … (zusammen höchstens 100 %,
die letzten 12 Entwürfe zählen). Ein Vierfach-Kill (13,5 Punkte) fällt damit nach einem Auftritt auf 4 und
kommt ein, zwei Entwürfe später wieder; dazwischen kommen die anderen dran.
Einstellbar: `[regie.vorgaben] abwechslung = 0.7` (0 = immer die besten, 1 = maximal wechseln).
Je mehr Clips eine Stimmung haben, desto mehr Auswahl: `regie-starten.sh` analysiert die nächsten 40, der
Bot vor jedem Entwurf 10 weitere.

## Multikills am Stück – Serie und Jump-Cut
Bei einem Team-Wipe sterben alle umgehauenen Gegner im selben Augenblick. Die Kill-Zeiten liegen dann alle beim
Wipe, die eigentliche Action (das Umhauen) aber oft 10–20 s davor. Deshalb kennt der Regisseur je Kill zusätzlich
den **Aktions-Zeitpunkt** (`merkmale.aktion_sekunden`, parallel zu `kill_sekunden`, = mein Umhauen; ohne Umhauen
der Kill selbst). Gezählt wird weiter wie bisher – ein Team-Wipe bleibt ein Triple, Punkte und Elo ändern sich nicht.

| Regel | Wert |
|---|---|
| Anfang des Moments | `puffer_vor_s` vor der **frühesten Aktion** (nicht mehr vor dem ersten Kill) |
| Muss-Zone (wird nie angeschnitten) | 1 s vor der ersten Aktion … 0,5 s nach dem letzten Kill |
| **Serie** = Moment mit ≥ 2 Kills und Aktions-Zeiten | bleibt **ein Stück**, darf länger sein als `seg_max_s`: bis `serie_max_s` |
| `serie_max_s` | Short **20 s**, Zusammenschnitt **30 s** |
| **Jump-Cut**: Pause zwischen zwei Aktionen/Kills > `luecke_max_s` (Start **4 s**) | 1,5 s nach der vorigen Aktion raus, 2,0 s vor der nächsten wieder rein – harter Schnitt, gleiche Datei, Reihenfolge bleibt |
| Serie passt selbst mit Jump-Cuts nicht in `serie_max_s` | im **Short nicht gewählt** (Hinweis „n Serie(n) zu lang für Short“), im **Zusammenschnitt ganz** – nie zerteilt |

So sieht das in der Schnittliste aus: Ein Moment mit Jump-Cut wird zu mehreren Segmenten hintereinander, mit
gleichem `moment` und gleicher `datei` und dem Feld `teil` = 1, 2, … Ab Teil 2 ist der Übergang ein harter Schnitt.
Die Schnittpunkte zwischen den Teilen liegen fest an der Action; beweglich sind nur der Anfang von Teil 1 (Anlauf)
und das Ende des letzten Teils – nur dieses rastet auf den Beat ein. Beispiel Team-Wipe (Sekunden im Clip):

```
Umhauen 8,0   Umhauen 11,8           ……… 17 s nichts ………           Wipe 28,7–28,9 (3 Kills)
|--- Teil 1: 5,5 … 13,3 (Anlauf + beide Umhauen) ---|  ✂  |--- Teil 2: 26,7 … 30,4 (Wipe) ---|
                                   im Video: 7,8 s + 3,7 s = 11,5 s am Stück
```

- Jump-Cuts gibt es für jeden Moment mit Aktions-Zeiten, auch für einen Einzelkill mit weit entferntem Umhauen
  (der bleibt aber bei `seg_max_s`).
- `luecke_max_s` wirkt mindestens mit 1,5 + 2,0 + 0,5 = 4 s, damit sich Teile nie überlappen. Eine Vorgabe in
  `[regie.vorgaben]` ist dafür (noch) nicht vorgesehen. Genau 4,0 s ist noch keine Lücke (auf ms gerechnet).
- Liegt ein Umhauen **vor dem Dateibeginn** (`aktion_sekunden` < 0: alter Bot-Clip, kurze Aufnahme, 60-s-Kappung),
  gilt die Lücke dahinter wie jede andere: Was ganz vor der Datei liegt, fällt weg, der Moment beginnt 2,0 s vor
  der nächsten Aktion in der Datei – kein Schnipsel vom Dateianfang.
- „n Momente“ im Lern-Bot, der Bogen und „neu / schon gezeigt“ zählen **Momente**, nicht Segmente.
- Alte Momente ohne `aktion_sekunden` verhalten sich exakt wie vorher (geprüft in `tests/test_regie_serie.py`).
  Vorhandene Multikill-Momente bekommen die Aktions-Zeiten per `pipeline momente nachschneiden` (neu geschnitten
  aus dem Rohvideo im Puffer) bzw. beim nächsten `pipeline stimmung`.

## Stimmungen und Übergänge
| Stimmung | erkannt an (Punkte-Regeln in `stimmung.punkte`) | Übergang in den Moment |
|---|---|---|
| episch | Serie ≥ 3, Victory Royale, Jubel nach Kill | harter Schnitt auf dem Beat |
| spannend | Kills, viele Spitzen im Spielton, umgehauen aber überlebt | harter Schnitt |
| lustig | Lachen im Transkript („haha“) | Wischblende 0,3 s |
| frustriert | Tod, Frust-Wörter | Abblende über Schwarz 0,5 s |
| chill | keine Kills, wenig los, leise | weiche Überblendung 0,8 s |

Die Mitte jedes Übergangs liegt genau auf dem Beat; kein Kill wird angeschnitten (fachliche Prüfung der
Schnittliste vor dem Speichern). Mit Effekten (unten) wechseln die Übergänge je Stimmung nach einer festen Rotation.

## Effekte (Regisseur 2.0) – der Plan
Der Regisseur schreibt für jeden Entwurf einen **Effekt-Plan** in die Schnittliste (`version 4`): welcher Effekt
wann (Quellzeit `t_s`) und wie stark (0 … 1). Der Renderer setzt den Plan nur um – so lässt sich alles ohne Video
prüfen (`tests/test_effekte_plan.py`). Code: `src/clip_pipeline/effekte.py`, Werte je Stimmung in `effekte.PROFIL`.

Deine Vorgaben vom 25.09. (gehen der ursprünglichen Planung vor):
1. **Spielbild clean:** im Spielbild nur Übergänge, **Zoom** (Punch, Beat-Akzent, Meme), später die Zeitlupe und
   der **Farblook** – kein Blitz, kein Wackeln, kein Glitch-Stoß (Glitch nur als Übergangsart). Der Zoom wirkt nur
   auf das Spielbild: im Short nie auf den unscharfen Hintergrund, nie auf Texte. Tod (frustriert): nur Punch +
   Einschlag.
2. **Texte nur außerhalb des Spielbilds, animiert** (Pop-in über die Größe, Ein-/Ausblenden, leichtes
   Hineingleiten). Short (1080×1920, Spielbild mittig bei y 34–66 %): Zähler oben unter „clip-battle.de“ (bleibt
   dauerhaft bei y ≈ 0,12·h), Kill-Titel unten – beide auch während eines Zooms und beim Pop nie im Spielbild.
   Die Übergangsart „zoom“ (xfade zoomin) vergrößert das ganze Bild – Titel und Zähler enden darum spätestens am
   Anfang einer solchen Blende. Ist die Aufnahme höher als 16:9 (z. B. 4:3), rücken die Texte mit; lässt das
   Spielbild keinen Platz, fällt der Text weg.
   16:9-Zusammenschnitt: **kein Zähler**; ein Kill-Titel nur **während der Blende** direkt nach dem Moment mit
   der Serie (nach seinem letzten Teil – Jump-Cuts liegen innerhalb der Serie) – bei hartem Schnitt dort (oder am
   Ende) keiner.
3. **Schrift:** DejaVu Sans Bold wie bisher (`[shorts].schriften`), keine neue Schriftdatei;
   `[regie.effekte] titel_zeichenbreite = 0.75`.

| Regel | Was passiert |
|---|---|
| Anker | die sichtbare Aktion: **mein Umhauen** (`aktion_sekunden`), sonst der Kill; nur ≥ 0,1 s weg vom Schnitt bzw. außerhalb der Blende (steht als `kill_s` im Segment) |
| Kette | Kills mit ≤ 10 s Abstand (`[vorbewertung].multikill_fenster_s`, gezählt wie Bot und Elo) |
| Finisher (letzte Aktion der Kette) | Zoom-Punch + Bass-Hit; die Kills davor: Mini-Punch (halb so stark) + Tick |
| Kill-Titel | **einmal je Serie am Ende**: DOUBLE / TRIPLE / QUAD / PENTA KILL, ab 6 MULTI KILL – nie DOUBLE und TRIPLE nacheinander. Die längste Serie heißt wie `max_gruppe` des Moments |
| VICTORY ROYALE | ab letztem Kill + 0,4 s bis Segmentende (mindestens 1 s), ersetzt einen überlappenden Kill-Titel |
| Zähler „KILLS n“ | nur Short, bei jedem sichtbaren Kill, zählt über das ganze Video |
| Tod (frustriert) | Punch + dumpfer Einschlag, kein Titel |
| Jubel (lustig) | Meme-Zoom + Pop auf der ersten Jubel-Spitze |
| Riser | endet auf dem ersten Kill des Höhepunkts |
| Whoosh | auf jedem weichen Übergang (nicht bei Schnitt, Jump-Cut und Abblende über Schwarz) |
| Budget | Zooms ≥ 0,4 s auseinander (Finisher vor Meme vor Punch vor Akzent), kein Zoom-Start in einer Blende, höchstens 1 Glitch-Übergang |
| Beat-Akzent | kleiner Zoom auf einem Musik-Beat, wenn 2,5 s (lustig 3 s) weder Schnitt noch Zoom war |

| Stimmung | Look | Übergänge (Rotation) | Besonderes |
|---|---|---|---|
| episch | cinematic 0,8 | Schnitt, Whip 0,25, Schnitt, Zoom 0,3 | Punch 0,7; in den Höhepunkt immer harter Schnitt |
| spannend | kalt 0,6 | Whip 0,25, Schnitt, Glitch 0,2, Schnitt | Punch 0,5; in den Höhepunkt harter Schnitt |
| lustig | warm 0,5 | Wischen, Squeeze, Schieben (je 0,3) | Meme-Zoom, kein Bass-Hit |
| frustriert | entsättigt 0,7 | Abblende 0,5, Glitch 0,2 | kein Titel, keine Akzente |
| chill | soft 0,3 | Blende 0,8, Dissolve 0,6 | kein Punch, kein Zähler |

Der Look richtet sich nach der Hauptstimmung des Videos. Stärke = Profilwert × gelernte Effekt-Stärke; unter
`schwelle` (0,15) fällt ein Effekt weg. **Ausschalten:** `[regie.effekte] an = false` – dann sind Schnitt und
Übergänge wie vorher und die Schnittliste enthält `"effekte": {"an": false}`. Der Filtergraph ist dann
zeichengleich mit dem derselben Liste ohne Effekt-Plan (Test). Stufe 0 (feste Bildrate zuerst, Unschärfe des
Short-Hintergrunds in Viertelgröße) gilt immer, auch mit `an = false`; wer genau den Stand davor will, nimmt deren
eigenen Commit zurück (`git revert`). Einzelne Werte je Stimmung
überschreiben: `[regie.effekte.<stimmung>]` in `config/lokal.toml` (Beispiele in `config/lokal.beispiel.toml`);
Unbekanntes steht als Hinweis im Entwurf.

**Offen – deine Entscheidung (16:9-Titel am Höhepunkt):** Der Titel steht im Zusammenschnitt nur in der Blende
*nach* dem Moment. Der Höhepunkt ist aber immer der letzte Moment – danach kommt keine Blende, und in einen
epischen/spannenden Höhepunkt führt ein harter Schnitt. Darum bekommen der größte Multikill und VICTORY ROYALE im
16:9 bisher **nie** einen Titel (nachgestellt mit einem Victory-Triple als Höhepunkt). Möglich wäre eine Ausnahme
nur für den letzten Moment, z. B. der Titel kurz vor dem Ende. Bis du entscheidest, bleibt es beim Wortlaut
von Ü2.

### Effekte lernen – „🎆 zu viele Effekte“ / „💥 mehr Action“
Zwei gelernte Parameter (in `regie.PARAMETER`, gerechnet in `regie_lernen.aktuelle`, chronologisch aus allen
Bewertungen – wie die anderen Regeln):

| Parameter | Start | wirkt auf | lernt aus |
|---|---|---|---|
| `effekt_staerke[stimmung]` | 1,0 je Stimmung | **alle** Effekte von Segmenten dieser Stimmung (Zoom, Titel, Zähler, Klänge) und den Look, wenn sie die Hauptstimmung ist | 🎆 ×0,85 · 💥 ×1,15 für die **Hauptstimmung** des Entwurfs, Grenzen 0,1 … 1,5; beide zugleich: nichts; 👍/👎 ohne Grund: nichts |
| `effekt_hektik` | 1,0 | nur die Beat-Akzente – Blitz und Wackeln gibt es nicht mehr | 😵 zu hektisch ×0,9, Grenzen 0,3 … 1,3 |

Stärke eines Effekts = Profilwert × `effekt_staerke[stimmung]` (× `effekt_hektik` bei Beat-Akzenten), höchstens 1;
unter `schwelle` (0,15) fällt er weg. Übergänge, auch der Glitch-Übergang, haben immer volle Stärke (Spezifikation §4).
**Vorgaben:** `[regie.vorgaben] effekt_hektik = 0.8` und `[regie.vorgaben.effekt_staerke] chill = 0.5` (0 … 1,5;
**0 = diese Stimmung ohne Effekte**, das bleibt auch nach „💥 mehr Action“ so). `/lernstand` zeigt die Zeile
„Effekte: episch 0.85 · spannend 1.0 · … · Hektik 0.9“, Vorgaben mit „(deine Vorgabe)“ bzw. „(Vorgabe 0.5)“.

### Wie der Plan ins Bild kommt (Renderer)
`src/clip_pipeline/effekt_filter.py` baut aus dem Plan ffmpeg-Filter, `entwurf.py` hängt sie in den Graphen. Er
liest nur `liste["effekte"]` und `segmente[].effekte`, nie die gelernten Parameter.

| Effekt | ffmpeg | Wo im Graphen |
|---|---|---|
| Zoom (Punch 1 + 0,25·s, Akzent 1 + 0,06·s, Meme 1 + 0,2·s) | `scale` mit `eval=frame`, mittig per `overlay` auf das unveränderte Bild (`overlay` rechnet nur während eines Zooms) | je Segment **nur auf dem Spielbild**: im Short vor dem Einsetzen in den unscharfen Hintergrund, im 16:9 vor dem Rand |
| Übergänge Whip, Zoom, Glitch, Squeeze, Dissolve | `xfade` slideleft, zoomin, pixelize, squeezeh, dissolve | wie die alten Übergänge |
| Whip / Glitch zusätzlich | waagrechte Unschärfe (`avgblur`) bzw. Farbversatz + Rauschen (`chromashift`, `noise`) | nur während der Blende (`enable`) |
| Look | `eq` (Kontrast, Sättigung, Helligkeit) + `colorcorrect` (Farbstich in Schatten und Lichtern) | Short: je Segment auf Spielbild und kleinem Hintergrund (vor dem Hochskalieren); 16:9: einmal nach den Übergängen |
| Kill-Titel, Zähler | `drawtext`, DejaVu Sans Bold (`[shorts].schriften`): wächst kurz über seine Größe (Pop-in), blendet ein und aus, gleitet leicht herein | nach dem Look (Schrift bleibt reinweiß); Short: Zähler zwischen clip-battle.de und Spielbild, Titel darunter (über den unteren 25 % für die App-Knöpfe) – auch beim Pop nie im Spielbild; die Höhe des Spielbilds misst `rendere` an den Quellen (4:3-Aufnahmen sind höher). 16:9: Titel mittig, nur in der Blende |
| Klänge | selbst erzeugte WAVs (`sfx.py`), samplegenau verschoben | nach dem Ducking dazugemischt (die Musik weicht nur dem Spielton aus) |

Warum `colorcorrect` und `chromashift` statt `curves` und `rgbashift`: beide rechnen direkt in YUV. Ein RGB-Filter
lässt ffmpeg **jedes** Bild zweimal umrechnen, auch außerhalb der Blende – lokal gemessen bei 720×1280: `rgbashift`
9 ms je Bild (für 0,2 s Glitch), `curves` 8 ms, `colorcorrect` 2 ms. Im Short rechnet der Look zudem nur auf dem
Spielbild (ein Drittel des Bildes) und dem Hintergrund in Viertelgröße. Gemessen (CPU-Zeit des Filtergraphen,
45-s-Short aus 1080p60, ohne Encoder): ohne Effekte 46,6 s, mit Zoom, Look, Texten, Blenden-Filtern und Klängen
50,3 s (+8 %).

Der Filtergraph ist ein einziges Argument auf der Befehlszeile (Linux: höchstens 128 KB). `compose` prüft ihn
darum schon beim Planen und bricht über 64 KB ab (`Schnittliste zu groß`); 40 Segmente mit 400 Ereignissen
ergeben rund 57 KB.

## pve-big schaltet sich selbst ab (clip-leerlauf)
`deploy/big/clip-leerlauf` läuft auf pve-big jede Minute und fährt ihn nach 20 min ohne echten Zugriff auf den
Clips-Ordner herunter (Details im Kopf des Skripts). Er setzt jede Minute den Zeitstempel der leeren Datei
`<clips>/.leerlauf-scharf`; daran erkennt der Mini, dass pve-big sich selbst abschaltet – erst dann darf der
Lern-Bot ihn wecken (Regel 3). Bewusst ohne Inhalt: Lesen und Schreiben zählen als Zugriff.

Einrichten (einmal):
1. pve-mini: `bash /root/regie.sh` legt die Dateien nach `<clips>/.einrichtung` und **gibt den Einfüge-Block
   aus** (Prüfsummen aus dem git-Stand im CT, nicht von der Freigabe).
2. pve-big-Shell (Weboberfläche → pve-big → Shell): den Block einfügen. Er kopiert die Dateien in einen Ordner
   nur für root, **prüft die SHA-256-Summen** (eine veränderte Datei auf der Freigabe wird nicht ausgeführt) und
   startet `einrichten.sh`: Konfiguration aus ZFS, Gäste mit Autostart zählen nicht, `TROCKEN=0`.
3. **Shell-Fenster schließen.** Eine Konsole, in der getippt wird, hält pve-big wach; nach 20 min ohne Tippen
   zählt sie nicht mehr.

Pause bis zum nächsten Neustart: `touch /run/clip-halten` · Ganz aus: `systemctl disable --now clip-leerlauf.timer`
· Nachsehen: `journalctl -t clip-leerlauf --since -2h` (nicht mit `-f` offen lassen).
