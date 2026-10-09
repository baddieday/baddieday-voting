# Weitere Rechner (Worker) – lokal zuerst, Vertrag v1

Stufe 3 Mehrbenutzer, Stand 09.10.2026. **Nur Doku:** Im Code gibt es keinen Fern- oder Cloud-Rechner und keinen
Cloud-Schalter – gebaut wird erst mit dem ersten Worker und deinem Ja (M149). Annahmen M148–M152:
`docs/ENTSCHEIDUNGEN.md`, „Mehrbenutzer“.

**Kurz:** Heute rechnet nur der Mini – für dich und für jeden Freund. Ein zweiter Rechner oder die Cloud brächte keinen
Zeitgewinn: Das Hin- und Herschicken dauert etwa so lange wie das Rechnen. Diese Seite hält fest, woran man merkt, dass
sich das ändert, und welche Regeln dann gelten. Die Cloud bleibt aus.

## Lokal zuerst – die sechs Fragen aus Abschnitt D, Stand heute
| Frage | Antwort heute |
|---|---|
| 1. Wo liegen die Quelldateien? | Im Puffer des Mini (bei Freunden in `I/daten`). Ins Lager auf pve-big kommen sie erst mit dem Abgleich um 10 Uhr, die Regie-Dateien (`regie/`) nie. |
| 2. Welche geeignete Hardware ist erreichbar? | Immer der Mini (8 Kerne, 12 GB, iGPU nur zum Kodieren, mit Rückfall auf den Prozessor). pve-big nur tagsüber beim Abgleich (NVENC nie bestätigt), der Gaming-PC nur beim Spielen. |
| 3. Wo wird der Auftrag am schnellsten fertig? | Auf dem Mini: nichts zu übertragen, nichts zu wecken. Schnitt und Effekte rechnen auf dem Prozessor (rund 50 s Rechenzeit je 45-s-Short ohne Kodieren), eine Grafikkarte spart nur das Kodieren. |
| 4. Wie groß wäre der Netzwerktransfer? | Lokal 0. Je 48-s-Short 89 MB (nur die genutzten Stellen) bis 329 MB (ganze Aufnahmen): 15–53 s bei 50 Mbit/s Upload, im Heimnetz rund 3 s. Das Rendern selbst dauert 58–86 s (Entwurf bzw. Upload-Fassung). |
| 5. Was würde Cloud-Verarbeitung kosten? | Nichts – die Cloud ist aus. Zur Übertragung kämen 1–3 min Start einer Cloud-Maschine dazu. |
| 6. Liegt die Freigabe des Benutzers vor? | Nein – weder für die Cloud noch für einen zweiten Rechner. |

Eine Auswahl-Logik gäbe heute immer „Mini“ zurück – deshalb wird sie nicht gebaut. Was die Fragen 3 und 4 später
brauchen, misst `pipeline laufzeiten` schon: Rechenzeit je Encoder, Rückfälle und die Eingabe-Größe je Video.
Alle Zahlen hier stammen aus dem Container (4 vCPU, nur Prozessor, künstliches Material) und gelten nur als Verhältnis
(M152); echte Aufnahmen haben eher höhere Bitraten. Echte Mini-Zahlen liefert `pipeline laufzeiten` vor Ort.

## Die Rechner – Urteil
- **Mini:** einziger Rechner. Die Daten liegen dort, er läuft immer, eine Sperre gilt für alle, ffmpeg mit VA-API,
  Wächter und Rückfall auf den Prozessor sind da.
- **pve-big:** erster Kandidat, aber nur als Mitfahrer beim Abgleich (tagsüber, einmal am Tag) für Aufträge, die warten
  können. Neue Szenen liegen erst nach 10 Uhr im Lager, NVENC ist unbestätigt und hat keinen Rückfall, und im Lager
  wird nie gelöscht – er bräuchte einen eigenen Arbeitsordner.
- **Gaming-PC:** nur nach dem Spielen und mit Einrichtung von Hand. Beim Spielen kostet Rendern FPS und gefährdet die
  Aufnahmen, NVENC teilt er sich mit der Nvidia App, Python und ffmpeg fehlen, und unter Windows gibt es keinen
  Hänger-Wächter. Das Abend-Video entsteht 45 min nach dem letzten Match – dann ist er meist aus.
- **PCs der Freunde und vServer:** nein. Fremde Aufnahmen auf fremden PCs wären ein Datenschutzproblem (dort läuft
  bewusst nur PowerShell ohne Installation); auf den vServer kommen keine Videos außer dem Briefkasten (M106).
- **Cloud:** zuletzt. Der Upload dauert etwa so lange wie das Rendern, dazu 1–3 min Start, Kosten und Datenschutz
  (Stimmen von Mitspielern).

## Wann ein zweiter Rechner – und was vorher kommt
Auslöser, abgelesen an `pipeline laufzeiten --tage 7`:
- Das Abend-Video ist an 3 Abenden einer Woche mehr als 30 min nach „🎮 Abend erkannt“ fertig
  (`abend_bis_video.ueber_30_min` ≥ 3), **oder**
- deine Schritte warten über 7 Tage im p90 länger als 10 min auf die Sperre (`auftraege.<Befehl>.gewartet_s.p90`).

Dann in dieser Reihenfolge: (1) lokal entlasten – `decide` wartet auf Claude ohne die Sperre, Whisper raus aus dem
Bau-Pfad, mehr Kerne für den CT; (2) ein Rechner im Heimnetz (3 s statt 53 s Übertragung), mit deinem Ja; (3) die Cloud
zuletzt, mit Zustimmung und Limit (unten).

## Vertrag v1 – für Heimserver und Cloud gleich
Der Mini legt einen Auftrag hin, der Worker prüft und rechnet, der Mini holt das Ergebnis ab, prüft es und übernimmt
es – oder rechnet selbst.
- **Eingang:** ein Ordner `a-<16 hex>/` mit `in/<sha256>` (jede Eingabe heißt nach ihrer Prüfsumme, nie nach einem Pfad
  des Mini) und zuletzt `auftrag.json` als Lieferschein (wie im Briefkasten, höchstens 1 MB): `vertrag` 1, `id`,
  `versuch`, `profil` (entwurf | upload | zusammenschnitt), `code` (Commit), `frist_s`, `max_mb`,
  `eingaben` {sha: {groesse, art}}; dazu die Werte, die das Video verändern (`[regie.ton]`, `sfx_pegel`,
  `titel_zeichenbreite`, die Schrift als sha), und die Regie-Liste – nur ihre Render-Felder, ohne `match_id` und
  Lern-Daten.
- **Prüfung auf dem Worker, bevor ffmpeg startet:** gleicher Code-Stand (sonst Absage, der Mini rendert selbst); je
  Eingabe Größe und SHA-256, nur gelistete Dateien, keine Links; `regie.pruefe_liste` mit fester Übergangsliste;
  Grenzen: höchstens 1920 Pixel je Seite, 60 fps, die Formatlänge und 200 Segmente.
- **Ausführung:** Sperre je Auftrag, eindeutige Zwischennamen, Wächter plus harte Frist (auch ohne /proc), Rückfall vom
  Hardware-Encoder auf den Prozessor, kein Netz, beschreibbar nur `a-…/`.
- **Ergebnis:** `aus/video.mp4` und zuletzt `aus/ergebnis.json` {id, versuch, sha256, groesse, dauer_s, encoder, code,
  sekunden}. Ein zweiter Aufruf liefert dasselbe Ergebnis.
- **Übernahme am Mini wie fremde Eingabe:** SHA-256 und Größe nach dem Kopieren; ffprobe (Dauer ±2 Bilder, Auflösung,
  Ton); Dekodierprüfung `ffmpeg -v error -i … -f null -` (etwa 5 % der Renderzeit – die Länge allein erkennt eine
  doppelt beschriebene Datei nicht: im Versuch 11–12 Tsd. Dekodierfehler bei richtiger Länge); höchstens `max_mb`.
  Erst dann `medien.uebernehmen` und die Datenbank nur `WHERE datei IS NULL`; sonst verwerfen und lokal rendern.
- **Versuchsnummer als Fencing:** Jeder neue Versuch desselben Auftrags zählt `versuch` hoch. Reißt die Verbindung ab,
  fragt der Mini wieder nach dem Stand; nach `frist_s` rendert er selbst. Ein spätes Ergebnis mit alter Versuchsnummer
  wird verworfen – ein abgehängter Worker überschreibt nie ein neueres Ergebnis.

## Zugang
Muster: `deploy/big/clip-big-steuer.sh` und `deploy/n8n-lauf.sh`.
- Eigener Benutzer `clip-worker` ohne Shell; in `authorized_keys` `command="…",restrict,from="<Tailnet-IP des Mini>"`.
- Je Worker ein eigener Schlüssel auf dem Mini und ein gepinnter Hostschlüssel (wie `big.ssh_befehl`).
- Nur die Verben `version`, `hat`, `datei`, `auftrag`, `start`, `status`, `ergebnis`, `fertig`; ID und Prüfsumme per
  Regex geprüft, alles andere endet mit Exit 2.
- Der Worker hat keinen Weg zurück – der Mini holt ab.
- Freunde vergeben nie Aufträge: Ihr Instanz-Modus hat keinen Host, kein SSH und keinen Schlüssel (M27).

## Nur die nötigen Daten, nur so lange wie nötig
- Übertragen werden nur die genutzten Stellen der Aufnahmen, die Musik und die Schrift – keine Datenbank, keine Tokens,
  keine Replays, keine Namen.
- Die Mikro-Spur (bei SteelSeries „Chat“ – darauf können auch Mitspieler zu hören sein) nur mit Zustimmung und nur für
  Szenen, die Stimmen brauchen (Segmentfeld `stimmen`). Vorschlag: Stimmen von Mitspielern verlassen das Haus nie.
- Die Kopien auf dem Worker sind nach `fertig`, spätestens nach 24 h weg. Das ist ein Löschen – es kommt erst mit deinem
  Ja zum ersten Worker.

## Cloud – zusätzlich
- `[worker].cloud = false` ab Werk; die Zustimmung ist ein Eintrag mit Datum in `lokal.toml`, den nur du setzt.
- Hartes Monatslimit in Euro, vor jedem Start gegen ein Kassenbuch in der Datenbank geprüft: Monatssumme plus Schätzung
  des Auftrags müssen darunter bleiben, sonst rendert der Mini selbst (Grenzen beim Anbieter greifen zu spät).
- Die Cloud-Maschine wird nach dem Auftrag beendet.
- Im Code kommt all das erst mit dem ersten Worker – heute gibt es weder Schalter noch Kassenbuch.

## Pflicht-Tests vor dem ersten Fern-Worker
Falsche Verben oder IDs werden abgewiesen · manipulierte Eingaben oder falsche Prüfsummen werden abgelehnt, bevor
ffmpeg startet · Pfade oder Links nach draußen und eingeschleuste Filter werden abgewiesen · ein Doppelstart ergibt nur
einen Lauf · ein kaputtes, zu großes oder zu spätes Ergebnis (alte Versuchsnummer) wird nie übernommen · ein Freund kann
keinen Auftrag vergeben. Bleiben, wie sie sind: `FinalPruefung` (test_entwurf), `SteuerSkript` (test_big),
`test_n8n_einstieg`.

## Erste Auftragsart, falls je nötig: Whisper
Statt eines Videos nur die Mikro-Spur: rund 100 KB (25 s Opus mit 32 kbit/s) statt 89–329 MB – Prinzip 1 erlaubt
ausdrücklich „höchstens die Mikro-Spur“. Whisper ist rechenintensiv (11 s für einen 4-s-Satz auf 4 Kernen, mit Laden).
Ob Whisper oder das Rendern den Mini bremst, zeigt `bau` in `pipeline laufzeiten` (Stimmung, Schnitt und Rendern
getrennt). Auch hier gilt die Zustimmung für die Mikro-Spur (oben) – im Heimnetz passt das zum Vorschlag, in der
Cloud nicht.

## Vorläufer v0: `render-entwurf --final`
Der heutige Fernweg zu pve-big bleibt unverändert im Code, ist im Puffer-Betrieb aus und wird nicht wieder
eingeschaltet – Gründe in `docs/REGIE.md`, „Befehle“. Ein künftiger Worker folgt Vertrag v1.
