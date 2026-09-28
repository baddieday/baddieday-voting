# Plan Stufe 2 „Merkmale und eine Bewertung“ – Lernschleife Publikum

Stand 25.09.2026 (2. Fassung, Befunde der Plan-Prüfung eingearbeitet) · Branch `lernschleife-publikum` **nach dem
Merge von Regisseur 2.0** (`claude/compassionate-curie-uwv39i`, Probe-Merge ohne Konflikte) · Spec §8, §10.5,
§10.6 (nur Erwartungszeile), §12, §13, §14 Punkt 2 · Startauftrag Abschnitt 3–6

**Fertig, wenn (Spec §14):** `/gewichte` zeigt beide Quoten (Nutzer, Publikum) **und** ein Clip mit Bot-Opfern
bekommt sichtbar weniger Punkte.
Konkret prüfbar (🧪 hier): Ende-zu-Ende mit zwei künstlichen Replays, die sich nur in `eliminiert_bot` unterscheiden
→ `analyze`/`decide`/`render` → `clips.punkte` des Bot-Clips ist um `bot_opfer × 2` niedriger, die Begründung
nennt „Bot-Opfer“. `/gewichte` zeigt die Publikums-Quote **ab dem ersten Publikums-Paar** mit Zusatz „(n Paare, zählt
für die Schranke erst ab 10)“ (Paket D) – im Ende-zu-Ende-Test also schon mit einem Paar.
🏠: dasselbe mit einem echten Match gegen Bots (Florian, nach der Installation). Die Publikums-Quote erscheint am
echten System erst, wenn zwei Posts derselben Plattform und Art einen echten Score haben (ohne „Basis zu klein“,
also frühestens ab dem 7. bewerteten Post dieser Art).
Wichtig: Punkte ändern sich nur für **neu gerenderte** Clips oder nach `pipeline merkmale nachtragen` (render
friert `punkte` ein, verarbeitung.py:367-369).

## Leitplanken (gelten für jedes Paket, zusätzlich zu Stufe 1)

1. **Kill-Zählung bleibt, wie sie ist.** `vorbewertung.kandidaten`, `gruppiere*`, `punkte_fuer`, `anlauf_start`,
   die Kill-Regel in `replay._meine_ereignisse` (Z. 141-150) und die Werte von `kill_punkte`, `victory_royale`,
   `laenge`, `lautstaerke` ändern sich **nicht**. Punkte ändern sich nur über **neue** Merkmale mit eigenen
   Gewichten. Beleg: Goldtest in Paket A2 (alle bestehenden Replay-Vorlagen: Titel, Gruppen, Fenster, alte fünf
   Merkmale byte-gleich; mit neuen Merkmalen = 0 ist `bewerte` gleich dem alten Wert).
2. **Regisseur 2.0 bleibt kompatibel.** `regie.Kandidat` behält `max_gruppe`, `victory`, `merkmale` (= die
   **momente**-Schlüssel, effekte.py liest z. B. `jubel_laut_s`), `intensitaet` bleibt eine Zahl je Kandidat
   (Bogen, Hook = zweitstärkster, Kürzen). Die Effekt-Titel (Kill-Titel, VICTORY ROYALE, Serien) hängen an
   `max_gruppe`/`victory` und dürfen sich nicht ändern (Goldtest in Paket C). `regie.schema.json` bleibt
   unverändert (keine neuen Segment-Schlüssel).
3. **Eine Formel.** Die Summe Σ Gewicht·Wert steht genau einmal: `vorbewertung.roh_score` (ungerundet).
   `bewerte` (Text + Rundung), `lernen.score/trefferquote/trainiere`, `regie.kandidaten`, `erwartung` benutzen sie.
   `regie.KILL_PUNKTE` entfällt zugunsten von `[vorbewertung].kill_punkte`.
4. **`clips.merkmale` bleibt ein flaches Zahlen-Dict** (db.merkmale wendet `float()` an). Schreiben nur über
   `merkmale.aktualisiere_clip` (eine Stelle). `mic_stand` ist eine Spalte, kein JSON-Schlüssel.
   **Schlüssel vorhanden = gemessen (auch 0), Schlüssel fehlt = unbekannt** (Annahme S2-A17). Beim Bewerten zählt
   „unbekannt“ als 0 (Spec §8.2), beim Lernen wird ein unbekanntes neues Merkmal nicht verglichen (Paket D).
5. **Nie wecken** (kein `pruefe_speicher(wecken=True)`, nichts Neues in `cli.WECKEN`, kein `big.herzschlag`). Neue
   Befehle ohne getrennten Betrieb (Puffer) per `_vorab_ablehnen` mit Exit 2 abweisen wie `momente`. Je neuem Modul
   (merkmale, mikro, erwartung) ein Test nach `tests/test_speicher_wecken.py`.
6. **n8n-Vertrag unverändert:** `render` endet weiter mit genau einer JSON-Zeile, Exit-Code wie bisher; der
   Mic-Schritt darf nichts auf stdout/stderr des render-Prozesses schreiben.
7. **Kein Kreis-Import, Gewichte werden durchgereicht.** Import-Regel (steht auch in den Modul-Docstrings, der
   Vertrag prüft sie per Test):
   - `merkmale` → nur `db`, `replay`, `vorbewertung`, `zeit`, `konfig`; **nie** `lernen`, `mikro`, `stimmung`,
     `verarbeitung`.
   - `lernen` → darf `merkmale` importieren (ab D); **nie** `mikro`, `stimmung`, `verarbeitung`.
   - `mikro` → `db`, `merkmale`, `lernen`, `konfig` auf Modulebene; `stimmung` **nur innerhalb** von
     `clips_nachziehen` (Funktions-Import mit Kommentar).
   - `stimmung` → `mikro` und `lernen` auf Modulebene (ab B).
   - Gewichte und Version holt **immer der äußerste Aufrufer einmal** per `lernen.aktuelle` und reicht sie durch:
     `cli._cmd_merkmale`, `mikro.clips_nachziehen`, `stimmung.analysiere` (einmal vor der Schleife),
     `verarbeitung.analyze`. `merkmale.*` und `mikro.in_clip_uebernehmen` bekommen sie als Parameter.
8. Tests zuerst (rot → grün), Testlauf-Ausgabe zitieren, alte Tests bleiben grün. Bricht ein alter Test: Befund an
   den Dirigenten, **nicht** den Test anpassen. Kandidaten für Brüche sind unten je Paket genannt.
9. **Der Vertrag ist die einzige Quelle für Signaturen.** Weicht ein Paket-Text ab, gilt der Vertrag; der Bauer
   meldet die Abweichung als Befund.

## Wellen und Abhängigkeiten

```
Vertrag (Dirigent) ──► Welle 1: A1 Bausteine ──► Welle 2 parallel: A2 · B · C · D · E · G ──► Welle 3: F Zusammenführen
```

- **A1** ist klein (reine Funktionen) und wird von allen anderen gebraucht – deshalb allein vorweg.
- In Welle 2 sind die Dateilisten disjunkt, mit Ausnahmen auf **Funktionsebene** (ausdrücklich zugeteilt):
  - `cli.py`: A2 `_cmd_replay` · B `_cmd_schritt` · D `_cmd_gewichte` · E `_cmd_lernstand` · G `_cmd_publikum`
  - `bot/texte.py`: D `gewichte_text` · E `clip_text`
  - `lernbot.py`: C nur `_balken` · E `_sende_entwuerfe`, `entwurf_text`, `/lernstand`
  Alle neuen Parser/Befehle legt der Vertrag an – kein Paket fasst `baue_parser` an.
  Allein zugeteilt: `stimmung.py` (B), `bot/aktionen.py` (A2, nur `LEISE_MELDUNGEN`), `regie.py` (C).
- Merge-Reihenfolge in Welle 3: A2 → B → D → C → E → G. C bringt die `_balken`-Korrektur selbst mit (negative
  `intensitaet` ist ab C möglich), damit kein Zwischenstand abstürzt.
- Abhängigkeit zur Laufzeit (nicht beim Bauen): C, E und die neu gerechneten Punkte in A2/B brauchen
  `lernen.aktuelle` mit aufgefüllten Startgewichten (D). Bis D gemergt ist, übergeben ihre Tests Gewichte explizit.
- Testdateien: Jedes Paket legt **eigene** Testdateien an (z. B. `test_merkmale_moment.py`, `test_mikro.py`,
  `test_lernen.py`, `test_ende_zu_ende_stufe2.py`), statt wie Spec §13 `test_ende_zu_ende.py`/`test_publikum.py` zu
  erweitern: So ändern parallele Bauer nie dieselbe Testdatei. Inhaltlich deckt das §13 ab (Annahme S2-A19).

## Vertrag (Dirigent, Commit „Stufe 2: Vertrag“)

Siehe Abschnitt „Vertragsentwurf“ am Ende. Kurz: `merkmale.py`, `mikro.py`, `erwartung.py` als Skelett;
`vorbewertung.MERKMALE`/`MERKMAL_NAMEN` auf 17 Einträge; neue Konfig-Schlüssel mit Kommentar; `db.MIGRATIONEN`
um drei `gewichte`-Spalten; `lernen.Paar`/`Ergebnis` um Felder mit Standardwerten; `publikum.robust_z` bekommt den
Parameter `minimum` (Standard = heutige Konstante); neue CLI-Befehle `pipeline merkmale nachtragen` und
`pipeline stimmung --clips [--session ID]` als Verdrahtung auf Stubs; Import-Regel als Test. Danach
Gesamt-Testlauf: muss grün sein (die neuen Merkmale sind überall 0, also ändert sich kein Score). Möglicher Bruch:
Tests, die die Zeilenzahl von `/gewichte` oder die Versionsnummer nach `aktualisiere` prüfen → dann Befund.

## Welle 1

### Paket A1 – Bausteine: eine Formel, Moment-Merkmale, Schreiben in `clips.merkmale`
Spec §8.1 (Mic-Zeilen), §8.2 (eine Bewertung) · Dateien: `vorbewertung.py` (nur `roh_score`, `bewerte`),
`merkmale.py` (nur `aus_momente`, `mic_vollstaendig`, `fuer_moment`, `aktualisiere_clip`),
`tests/test_merkmale_moment.py`

- `vorbewertung.roh_score(merkmale, gewichte) -> float`: Σ über MERKMALE, ungerundet, fehlend = 0. `bewerte` nutzt
  sie, Ausgabe von `bewerte` byte-gleich wie heute (Test mit den alten Testfällen).
- `merkmale.aus_momente(moment_merkmale) -> dict[str, float]`: **die eine Zuordnung** momente → Merkmale:
  `mic_lachen = min(lachen, 3)`, `mic_jubel = min(jubel, 3)`, `mic_frust = min(frust, 3)`,
  `mic_laut = min(jubel_laut, 3)`, `spitzen = min(spitzen, 4)`. Fehlende Schlüssel fehlen auch im Ergebnis
  (nicht 0 – „unbekannt“, Leitplanke 4). **Ausnahme ohne Mikro:** Steht `mikro_spur` im Dict, ist `None` und es gibt
  kein `fehler`, dann sind `mic_lachen`, `mic_jubel`, `mic_frust`, `mic_laut` = 0.0 (gemessen: kein Mikro, also
  nichts gesagt). Kommentar im Code: `spitzen` in momente ist der **rohe** Zähler.
- `merkmale.mic_vollstaendig(moment_merkmale) -> bool` (Regel für `mic_stand`, Annahme S2-A6):
  `("lachen" in mk) or ("mikro_spur" in mk and mk["mikro_spur"] is None and "fehler" not in mk)`.
  Begründung im Docstring: `stimmung.merkmale` schreibt `mikro_spur = bestand.mikro_spur(...)`, das ist `None`
  ohne Mikro (bestand.py:81-88); `0` wäre ein echter Spur-Index. Bei einem Messfehler (`fehler`) fehlt
  `mikro_spur` ganz.
- `merkmale.fuer_moment(clip_merkmale | None, moment_merkmale | None, kill_tabelle) -> dict[str, float]`:
  Clip-Moment = Zahlen aus `clips.merkmale` ∪ `aus_momente(moment_merkmale)` (Mic-Werte aus momente gewinnen,
  weil jünger). Datei-Moment (kein Clip) = `kill_punkte = punkte_fuer(max_gruppe, kill_tabelle)`,
  `victory_royale` aus momente, dazu `aus_momente`; **keine** Replay-Merkmale, kein `laenge`, kein `lautstaerke`
  (Abweichung von Spec §8.1, Annahme S2-A9, Rückfrage 4). Docstring: Datei-Momente legt heute nur
  `stimmung.momente_aus_dateien` an, ohne Match und ohne Kills (stimmung.py:173) – `max_gruppe` ist dort immer 0.
  Nur Zahlen im Ergebnis, Listen/Texte werden verworfen.
- `merkmale.aktualisiere_clip(con, clip_id, neue, gewichte, version) -> bool`: mischt die Zahlen aus `neue` in
  `clips.merkmale` (nie Listen; fehlende Schlüssel werden **nicht** mit 0 angelegt), setzt bei Status
  `vorbewertet` auch `punkte`, `begruendung`, `gewichte_version` neu (noch nicht gesendet, Annahme S2-A5); sonst
  bleiben sie (was der Bot gezeigt hat). True, wenn sich etwas geändert hat. Idempotent. Läuft in der Transaktion
  des Aufrufers. Holt **keine** Gewichte selbst (Leitplanke 7).
- Tests: Zuordnung und Deckel, fehlende Schlüssel, ohne Mikro → Mic-Werte 0, `spitzen` roh vs. gedeckelt;
  `mic_vollstaendig` für: `lachen` da → True · `mikro_spur None` → True · Spur 0 mit Mikro ohne Whisper → False ·
  Spur 1 ohne Whisper → False · `fehler` → False; Datei-Moment; Listen werden verworfen; `aktualisiere_clip` je
  Status; zweiter Aufruf ändert nichts; `bewerte` unverändert.

## Welle 2 (parallel)

### Paket A2 – Replay-Merkmale (§8.1, `[merkmale.waffen]`)
Dateien: `replay.py`, `merkmale.py` (nur `waffen_kategorie`, `aus_replay`, `melde_unbekannte_waffen`,
`nachtragen_replay`), `verarbeitung.py` (nur `analyze`), `cli.py` (nur `_cmd_replay`), `bot/aktionen.py` (nur
`LEISE_MELDUNGEN`), `tests/test_merkmale.py`, `tests/test_replay_merkmale_gold.py`

- `MeinEreignis` bekommt `waffe: int | None`, `opfer_bot: bool | None`, `verbleibend: int | None` (Standard None,
  rein additiv). `_meine_ereignisse(eliminierungen, start, meine_ids, spieler_gesamt=None)`; `letzter_knock`
  speichert zusätzlich die Waffe. **Waffe eines Kills = Waffe MEINES Umhauens, sonst die des Erledigens**
  (Sniper-Knock, Teammate erledigt → Sniper). `verbleibend` = `spieler_gesamt` − Anzahl finaler Einträge
  (`knock = false`, `selbst` eingeschlossen) mit `t_ms` ≤ diesem Ereignis, ≥ 0; ohne `spieler_gesamt` None.
  **Nicht aus dem Killfeed** (kein `t_ms`, enthält Knocks/Wiederbelebungen; Spec-Abweichung, Annahme S2-A2).
  Die Zeilen 141-150 bleiben unverändert; `test_kill_gehoert_dem_der_umhaut` bleibt grün.
- `merkmale.waffen_kategorie(waffe, konfig) -> str`: **rein** (keine DB, keine Meldung). Aus `[merkmale.waffen]`
  (Listen von GunType-Zahlen `sniper`, `nahkampf`, `sonstige`); unbekannte Zahl und `None` → `sonstige`.
- `merkmale.aus_replay(match, kill_zeiten, konfig) -> dict[str, float]`: **rein**, liest `[merkmale]` und
  `[merkmale.waffen]` aus `konfig`. Liefert die sieben Replay-Merkmale `platzierung, sniper, nahkampf, bot_opfer,
  phase, endgame, clutch` (sonst nichts). Zuordnung Kill ↔ Ereignis über den Zeitpunkt: alle Kill-Ereignisse mit
  `zeit_utc` in den Kill-Zeiten des Kandidaten (gleiche Zeit = gleiche Gruppe = gleicher Kandidat, auch beim
  Team-Wipe; Test mit Wipe).
  - `platzierung = 1 / platz` (unbekannt 0), je Match gleich.
  - `sniper`/`nahkampf`/`bot_opfer` = Anteil an den zugeordneten Kills (0 Kills → 0).
  - `phase = (erste Aktion des Moments − Replay-Start) / (Replay-Ende − Replay-Start)`, auf 0..1 begrenzt.
    Bezug ist die **erste Aktion** (mein Umhauen) des Moments. Einschränkung im Docstring: `laenge_ms` ist die
    Länge **meines** Replays (Annahme S2-A3).
  - `endgame = 1`, wenn bei irgendeinem Kill des Moments `verbleibend ≤ [merkmale].endgame_spieler`.
  - `clutch = 1`, wenn für einen Kill (Zeit T = Kill-Zeitpunkt) ein `knock_erlitten` in `[T − clutch_vor_s, T]`
    liegt und kein `tod` in `(T, T + clutch_nach_s]`. Endet das Replay vorher, zählt das als „nicht gestorben“.
  - Rekorder-Rückfall (`match.ich_quelle is None`) oder kein Match: nur `platzierung` (falls bekannt), Rest 0.
- `merkmale.melde_unbekannte_waffen(con, konfig, sid, match) -> list[int]` (Annahme S2-A4): sammelt die
  GunType-Zahlen meiner Kills, die in keiner Liste stehen. Schon gemeldete Zahlen stehen als **Vermerk** in
  `meldungen` (Schlüssel `merkmale:waffe:<n>`, mit `gesendet = erstellt` angelegt, damit der Bot sie nie
  verschickt). Gibt es neue Zahlen: Vermerke anlegen und **eine** Sammelmeldung `merkmale:waffen:<sid>` („Neue
  Waffen-Nummern in Match <sid>: 12, 27 – zählen als sonstige. Eintragen in config/lokal.toml [merkmale.waffen];
  bestimmen mit `pipeline replay <datei>`, docs/PUBLIKUM.md“). Rückgabe: die neu gemeldeten Zahlen.
- `bot/aktionen.py`: `LEISE_MELDUNGEN` um `"merkmale:"` ergänzen – die Sammelmeldung entsteht nachts nach `analyze`
  und wartet die Ruhezeit ab.
- `verarbeitung.analyze`: nach `vorbewertung.kandidaten` je Kandidat
  `k.merkmale.update(merkmale.aus_replay(match, k.kill_zeiten, konfig))` vor `bewerte`; danach einmal je Session
  `merkmale.melde_unbekannte_waffen(con, konfig, sid, match)`. `decide`/`render` reichen die Schlüssel ohne
  Änderung durch (Schnittliste: `merkmale` ist ein freies Objekt – Struktur unverändert).
- `merkmale.nachtragen_replay(con, konfig, gewichte, version, *, session=None) -> dict`
  (`{"clips", "geaendert", "ohne_replay", "waffen_gemeldet"}`): für Clips, denen die Replay-Merkmale fehlen,
  `sessions/<ID>/replay.json` **im Puffer** lesen (`match_aus_json`, Muster `stimmung._eigene_ereignisse`),
  Kill-Zeiten aus `clips.kill_zeiten` zuordnen (Toleranz 1 ms wegen ISO), `aktualisiere_clip(…, gewichte,
  version)`, je Session `melde_unbekannte_waffen`. Fehlt die Datei: zählen, nicht abbrechen. Weckt nie. Idempotent.
- `_cmd_replay` gibt je Ereignis zusätzlich `waffe`, `bot`, `verbleibend` aus (Kalibrierung, 🏠).
- Tests (`test_merkmale.py`, eigene Replay-Vorlagen mit `waffe`, `eliminiert_bot`, `spieler_gesamt`): jedes
  Merkmal einzeln; Sniper-Knock + Teammate-Finish; Bot-Opfer mit `null`; Endgame an der Grenze 10/11; Clutch mit
  und ohne Tod danach, Replay endet im Fenster; Phase; Team-Wipe; unbekannter GunType → `sonstige`;
  **zwei Matches mit teils gleichen unbekannten Zahlen → zwei Sammelmeldungen, die zweite nur mit den neuen
  Zahlen; die Vermerke tauchen in `aktionen.faellige_meldungen` nicht auf; `merkmale:`-Meldung wartet in der
  Ruhezeit**; Rekorder-Rückfall; Nachtrag idempotent und ohne Datei; weckt nie.
  `test_replay_merkmale_gold.py`: Leitplanke 1.
- Bruchgefahr: Tests, die `analyse.json`/Schnittliste-Merkmale wörtlich vergleichen (z. B.
  `tests/test_ende_zu_ende.py:205` legt `analyse.json` selbst an – dann wohl kein Bruch) → Befund, nicht anpassen.

### Paket B – Mic-Merkmale in `clips.merkmale` + Hintergrundschritt nach `render` (§8.2, §12)
Dateien: `mikro.py`, `stimmung.py` (nur `_speichere`, neu `_ergaenze_mic`, `analysiere` um die Parameter
`nur_clips` und `nur_mic`, Importe), `cli.py` (nur `_cmd_schritt`), `tests/test_mikro.py`

- **Ein Weg für alle Aufrufer:** `stimmung.analysiere` holt einmal vor der Schleife
  `version, gewichte = lernen.aktuelle(con, konfig)` und übergibt sie an
  `_speichere(con, m, stimmung, sicherheit, quelle, mk, text, *, gewichte, version)`. `_speichere` ruft nach dem
  Schreiben der momente-Zeile für Momente mit `clip_id`
  `mikro.in_clip_uebernehmen(con, clip_id, mk, gewichte, version)` auf → `merkmale.aktualisiere_clip(…,
  aus_momente(mk), gewichte, version)`, dazu `mic_stand = jetzt`, wenn `mic_vollstaendig(mk)`. Damit halten auch
  `clip-sitzungen` und der Lern-Bot die Clips aktuell, ohne eigene Logik. Import-Richtung nach Leitplanke 7:
  `stimmung` importiert `mikro` auf Modulebene, `mikro` importiert `stimmung` nur in `clips_nachziehen`.
- **Mic nachholen ohne Stimmungsverlust** (Annahme S2-A18): `analysiere(…, nur_clips=None, nur_mic=False)`.
  `nur_clips` (Liste von Clip-IDs) beschränkt auf diese Clip-Momente in genau dieser Reihenfolge. Mit `nur_mic=True`
  gelten zusätzlich Momente als offen, deren Zeile existiert, aber **nicht** `mic_vollstaendig` ist und kein
  `fehler` hat – nur wenn Whisper verfügbar ist. Für diese misst `analysiere` neu und ruft
  `_ergaenze_mic(con, m, mk_neu, text, *, gewichte, version)`: übernimmt aus der neuen Messung nur
  `stimmung.MIC_SCHLUESSEL` (`mikro_spur`, `lachen`, `jubel`, `frust`, `jubel_laut`, `jubel_laut_s`) und `text`;
  `stimmung`, `sicherheit`, `quelle` und alle anderen Schlüssel der Zeile bleiben (Claude-Stimmungen gehen nicht
  verloren). Diese Momente gehen nicht an Claude. Misslingt die Messung (`fehler`), bleibt die Zeile unverändert.
  Danach `mikro.in_clip_uebernehmen`.
- `mikro.clips_nachziehen(con, konfig, *, session=None, maximal=None) -> dict` (= `pipeline stimmung --clips`):
  holt Gewichte einmal per `lernen.aktuelle`.
  (1) Clips mit `mic_stand IS NULL`, die schon eine momente-Zeile haben → nur übernehmen (kein Whisper).
  (2) Clips (nicht verworfen, `mic_stand IS NULL`) **ohne** momente-Zeile oder mit Zeile, die nicht
  `mic_vollstaendig` ist und kein `fehler` hat (Letztere nur, wenn `whisper_da()`), zuerst die Session, dann nach
  `punkte` absteigend, dann id → `stimmung.analysiere(con, konfig, claude=False, nur_clips=…, nur_mic=True,
  maximal=n)` mit `n = maximal or [merkmale].mic_je_lauf`, damit die Pipeline-Sperre nicht lange belegt ist
  (warten_s 7200 → Exit 4 für n8n). Zeilen mit `fehler` werden nicht wiederholt (sonst belegt eine kaputte Datei
  jeden Lauf einen Platz); sie bleiben in „ohne Mic-Analyse“ sichtbar.
  Rückgabe `{"uebernommen", "analysiert", "mit_fehler", "offen"}`.
- `mikro.nachtragen(con, konfig, gewichte, version, *, session=None) -> dict`: nur Schritt (1) (Teil von
  `pipeline merkmale nachtragen`, ohne Whisper, schnell).
- `mikro.whisper_da() -> bool`: `importlib.util.find_spec("faster_whisper") is not None` – **kein** Import von
  faster-whisper/ctranslate2 im render-Prozess. Der Import passiert erst im Kind.
- `mikro.starte_im_hintergrund(konfig, sid) -> bool`: aufgerufen **nur** in `cli._cmd_schritt` nach erfolgreichem
  `render` mit `neu > 0`, wenn `[merkmale].mic`, getrennter Betrieb (Puffer) und `whisper_da()`.
  `subprocess.Popen(["nice", "-n", "15", sys.executable, "-m", "clip_pipeline", "--konfig", str(konfig.quelle),
  "stimmung", "--clips", "--session", sid, "--max", str(n)], stdin=DEVNULL, stdout=DEVNULL,
  stderr=<Logdatei konfig.datenbank.parent / "mikro.log", angehängt>, start_new_session=True)`. `--konfig` sorgt
  dafür, dass das Kind dieselbe Konfig und damit dieselbe Datenbank nutzt wie `render` (die Umgebung, z. B.
  `CLIP_DATENBANK`, erbt es ohnehin). Fehler beim Start (auch: Log nicht zu öffnen) → nur Log-Warnung, Rückgabe
  False; die render-JSON-Zeile und der Exit-Code bleiben gleich. Das Kind holt die Sperre selbst (fd nicht
  vererbt, PEP 446). Variante nach Annahme S2-A7; Rückfall ohne Code: `clip-sitzungen` erfasst die Clips ohnehin
  nach ≤ 10 min.
- `_cmd_stimmung --clips` weist ohne getrennten Betrieb mit Exit 2 ab (Verdrahtung im Vertrag).
- Tests: `_speichere`-Weg (mit/ohne Whisper, ohne Mikro-Spur → `mic_stand` gesetzt und Mic-Werte 0, Datei-Moment
  ohne Clip bleibt unberührt); **Mic nachholen: momente-Zeile ohne `lachen` mit `quelle = "claude"`, gefälschte
  Transkription verfügbar → danach `mic_stand` gesetzt, `stimmung`/`quelle` unverändert, `text` gesetzt**;
  Zeile mit `fehler` wird nicht wiederholt; `clips_nachziehen` Reihenfolge und `--max`; Popen-Argumente (gepatcht:
  nice 15, DEVNULL, start_new_session, **`--konfig` mit dem Testpfad vor `stimmung`, Log neben der Test-DB**);
  stdout von `pipeline render` endet weiter mit genau einer JSON-Zeile; kein Start bei `[merkmale].mic = false`,
  ohne Puffer, ohne faster-whisper (`find_spec` gepatcht, faster-whisper wird nicht importiert), bei `neu = 0`;
  `process`/`scan` starten nichts; weckt nie.
- Bruchgefahr: `tests/test_ende_zu_ende_publikum.py:366/388` vergleicht eingefrorene Clip-Merkmale wörtlich. Clip 1
  hat dort keine momente-Zeile; bricht der Test trotzdem (weil ein Lern-Bot-Schritt `stimmung.analysiere` für
  clip:1 anstößt), → Befund, nicht anpassen. `tests/test_stimmung.py` ruft `analysiere` ohne die neuen Parameter
  auf – muss unverändert grün bleiben.

### Paket C – Gemeinsame Bewertung im Regisseur (§8.2)
Dateien: `regie.py` (nur `kandidaten`, `erstelle`-Aufruf, Konstante `KILL_PUNKTE`), `lernbot.py` (nur `_balken`),
`tests/test_regie_bewertung.py`

- `kandidaten(con, p, frueher=None, *, gewichte, kill_tabelle)`; `erstelle` übergibt
  `lernen.aktuelle(con, konfig)[1]` und `[vorbewertung].kill_punkte`. SQL um `c.merkmale AS clip_merkmale`;
  `clip_punkte` fällt weg (unbenutzt).
- `intensitaet = moment_score = roh_score(fuer_moment(clip_mk, mk, kill_tabelle), gewichte)` (gerundet 2 Stellen
  wie heute); `punkte = intensitaet + stimmung_bonus + 1 (Status in db.BEWERTET) + Elo-Term + moment_bonus −
  abzug`. `STIMMUNG_WERT` fällt aus `intensitaet` heraus, bleibt als Tiebreak für die Musik (Spec wörtlich;
  Rückfrage 2).
- `Kandidat.merkmale` bleibt `mk` (momente-Schlüssel für effekte.py); `max_gruppe`/`victory` wie heute.
- **`lernbot._balken` robust für negative Werte** (Min-Max-Normierung; alle gleich → mittlerer Balken), weil
  `intensitaet` ab hier negativ sein kann. Heute wirft `_balken([1, -10])` einen IndexError.
- Tests: Clip-Moment nutzt `clips.merkmale` (Bot-Opfer senkt `intensitaet`); Datei-Moment rechnet über
  `max_gruppe` und Mic-Werte; **Clip-Moment mit einer Kill-Gruppe, Replay-Merkmalen 0 und `laenge` 0 ergibt
  dieselbe `intensitaet` wie ein Datei-Moment mit gleichem `max_gruppe` und gleichen Mic-Werten**; negative
  `intensitaet` bricht `erstelle` nicht; `_balken` mit `[2, -3, 1]`, `[-1, -2]`, `[1, -10]`, `[0, 0]`; Goldtest
  Effekt-Titel/Serien unverändert.
  Vorher `tests/test_regie.py` (u. a. `test_neue_momente_und_die_besten_kommen_wieder`,
  `test_auswahl_zaehlt_alle_momente`) mit neuer Formel laufen lassen – Bruch = Befund (siehe Rückfrage 2).

### Paket D – Drei Paar-Quellen, getrennte Quoten, `/gewichte` (§8.3)
Dateien: `lernen.py`, `bot/texte.py` (nur `gewichte_text`), `cli.py` (nur `_cmd_gewichte`), `tests/test_lernen.py`

- `score` → `vorbewertung.roh_score`. `Paar.gewicht`: battle 1,0 · freigabe `[lernen].gewicht_freigabe` 0,5 ·
  publikum 1,0. `trainiere`: `w += schritt · gewicht · d`; Marge 1,0 als benannte Konstante mit Kommentar.
  `trefferquote` gewichtet mit `gewicht` (Gleichstand halb, wie heute).
- **Unbekannt wird nicht verglichen** (Annahme S2-A17): Für die zwölf neuen Merkmale (`merkmale.REPLAY_MERKMALE`
  + `merkmale.MIC_MERKMALE`) ist `d[m] = 0`, wenn der Schlüssel auf einer Seite des Paars fehlt – in `trainiere`
  und `trefferquote` (dort Vergleich über Σ w·d statt zweier Scores). Sonst lernten die Gewichte „analysiert gegen
  nicht analysiert“ bzw. „nachgetragen gegen alt“. Die alten fünf Merkmale wie heute (fehlt = 0). Paar-Dicts
  werden deshalb **nicht** mit 0 aufgefüllt.
- `publikum_paare(con, konfig) -> list[Paar]`: bewertete Posts (`bewertet_utc`, `score` nicht NULL), **ohne**
  Vermerk „Basis zu klein“ (Score 0 ist keine Messung), gleiche Plattform und Art, `|Δscore| ≥ paar_abstand`,
  nicht derselbe Moment. `clip` → aktuelle `clips.merkmale` + Mic (`merkmale.fuer_moment`); `entwurf` →
  Hook-Moment (`posts.merkmale.hook_moment`) über `fuer_moment`; Rückfall eingefrorene `posts.merkmale` nur
  numerisch. Jüngste zuerst nach `max(gepostet_utc)`, dann ids; höchstens `max_paare`; Trainingsreihenfolge fest:
  Battles, Freigaben, Publikum.
- `berechne`: `trefferquote` (= Nutzer: Battles + Freigaben) und `trefferquote_publikum`, je mit Startwert.
  `trefferquote_publikum` wird **ab dem ersten Publikums-Paar** berechnet (None nur bei 0 Paaren). Nur die
  **Schranke** prüft die Publikums-Quote erst ab `[lernen].mindest_publikum_paare` (10) Paaren (Annahme S2-A10).
  Datenbasis n = Freigaben + Battles + Posts in Publikums-Paaren. `Ergebnis` füllt die neuen Felder
  (`paare_je_quelle`, `ohne_mic` = Clips mit `mic_stand IS NULL`, nicht verworfen; `auseinander`).
- `auseinander`: je Merkmal mittleres d über Nutzer- bzw. Publikums-Paare; X = stärkstes Merkmal, das nur du
  magst, Y = das nur das Publikum mag; Satz nur ab 10 Publikums-Paaren (Annahme S2-A11).
- `aktuelle`: fehlende Merkmale mit Startgewichten auffüllen (sonst zählen neue Merkmale 0, bis `aktualisiere`
  läuft → Fertig-Kriterium hinge am Zufall). `aktualisiere` speichert die neuen Spalten.
- `gewichte_text` und `_cmd_gewichte` zeigen: 17 Zeilen (Namen ≤ 15 Zeichen), „Sortier-Quote du: x % (Start y %)“,
  „Sortier-Quote Publikum: x % (Start y %) – n Paare, zählt für die Schranke erst ab 10“ (ab 10 ohne den Zusatz)
  bzw. „Publikum: noch keine Paare“, Anzahl je Quelle, „ohne Mic-Analyse: n Clips“, ggf. den Auseinander-Satz.
  Die Erwartungs-Zeilen hängt E an (Verdrahtung im Vertrag).
- Nach `pipeline publikum bewerten` mit neuen Scores `lernen.aktualisiere` aufrufen? → Annahme S2-A12: ja, in der
  Verdrahtung von `_cmd_publikum` (Paket G, eine Zeile).
- Tests (`test_lernen.py`, neu; `test_elo_lernen_bot.py` bleibt): drei Quellen mit Gewichten, Freigabe halb,
  getrennte Quoten, Schranke greift je Quote, **Publikums-Quote mit 1 Paar berechnet und angezeigt, Schranke
  prüft sie nicht**, Basis-zu-klein-Posts ausgeschlossen, gleicher Moment übersprungen, `max_paare` jüngste,
  Determinismus, **Paar mit `mic_lachen` nur auf einer Seite → Gewicht von `mic_lachen` bleibt**, **alte DB mit
  gespeicherter Gewichts-Version ohne neue Schlüssel → `aktuelle` liefert `bot_opfer = −2`**,
  `test_lernt_lautstaerke_und_bleibt_an_der_leine` mit Freigabe-Gewicht 0,5 erneut prüfen.

### Paket E – Erwartung und Trefferquote in beiden Bots (§10.5, §10.6 Zeile)
Dateien: `erwartung.py`, `bot/app.py`, `bot/texte.py` (nur `clip_text`), `lernbot.py` (nur `_sende_entwuerfe`,
`entwurf_text`, `/lernstand`), `cli.py` (nur `_cmd_lernstand`), `tests/test_erwartung.py`, `tests/test_bot_app.py`
(erweitert), `tests/test_lernbot.py` (erweitert)

- `erwartung.festschreiben(con, konfig, art, ziel_id) -> float | None`: rechnet nur, wenn noch keine Zeile da ist
  (`INSERT … ON CONFLICT (art, ziel_id) DO NOTHING`), gibt den **gespeicherten** Wert zurück; None unter
  `[erwartung].mindest_urteile` (10) Urteilen der Art (dann keine Zeile). `erwartung.gespeichert(con, art,
  ziel_id)` nur lesen.
- Modell: `σ(a·z + b·rezept + c)`, `rezept = 0` in Stufe 2 (Rangwert kommt mit rezepte.py, Stufe 3).
  Moment-Score Clip = `roh_score(fuer_moment(clips.merkmale, momente.merkmale), aktuelle Gewichte)`; Entwurf =
  Mittel über die eindeutigen Momente der Schnittliste, **aus den momente-Zeilen neu gerechnet** (nicht die
  gespeicherte `intensitaet` alter Listen). `z = publikum.robust_z(score, basis, minimum=[erwartung].mad_minimum)`
  gegen die letzten `[erwartung].referenz` (50) gesendeten derselben Art (Reihenfolge: Clips mit
  `tg_nachricht_id`, Entwürfe mit Status gesendet/bewertet, jeweils nach id absteigend – es gibt keinen
  Sende-Zeitstempel; Annahme S2-A13). Training: alle geurteilten Objekte der Art, z zur Laufzeit neu gerechnet
  (sonst startet die Erwartung nie), 50 Schritte Gradientenabstieg, Lernrate `[erwartung].lernrate`, L2 0,1 auf
  a und b (nicht c), Start a = 1, b = 0,5, c = 0. `grundlage` = JSON mit score, z, a, b, c, rezept 0,
  gewichte_version, n_urteile, median, mad.
- Urteil: Clip positiv = Status in `db.BEWERTET`, negativ = `verworfen`; Entwurf positiv = `daumen > 0`.
  Zusammenschnitte zählen als Entwurf mit (Annahme S2-A14).
- `trefferquote(con, art, letzte=None) -> (treffer, n)` zur Laufzeit; `trefferquote_text(con, konfig)` für
  `/gewichte` und `/lernstand` („Erwartung getroffen: Clips 14/20 (70 %) · alle 30/45 …“).
- Clip-Bot: `sende_outbox` schreibt **vor** `send_video` fest; `cmd_offen`, Caption-Edit nach Klick lesen nur.
  `texte.clip_text(..., erwartung=None)`: „Erwartung: ✅ 78 %“ bzw. „Erwartung: noch keine“; Caption auf 1024
  Zeichen begrenzen (Begründung kürzen) – Test mit allen 17 Merkmalen ≠ 0. `cmd_gewichte` hängt
  `trefferquote_text` an.
- Lern-Bot: `_sende_entwuerfe` schreibt vor `send_video` fest; `entwurf_text(..., erwartung=None)` setzt die Zeile
  **vor** die Hinweise (sonst schneidet `[:1000]` sie ab); Edit nach Klick liest nur. `/lernstand` und
  `pipeline lernstand` bekommen die Quote als Zusatz aus `erwartung` (Muster HILFE_ZUSATZ, keine Überschneidung
  mit `_effekte_zeile`). `_balken` gehört C.
- Tests: Festschreiben beim Senden über Fake-Telegram in beiden Bots, zweites Senden/`/offen`/Edit ändert
  `wahrschein` nicht, unter 10 Urteilen keine Zeile + „noch keine“, Trefferquote letzte 20/alle je Art,
  Determinismus (gleiche DB → gleiche a, b, c), `entwurf_text` ohne Erwartung (bestehender Test), weckt nie.

### Paket G – Nachtrag Stufe 1 (feststehende Nutzer-Antworten, vor dem ersten echten Score)
Dateien: `publikum.py`, `lernbot_zahlen.py`, `lernbot_publikum.py`, `cli.py` (nur `_cmd_publikum`, eine Zeile
`lernen.aktualisiere` nach `bewerten`), `tests/test_publikum_stufe2.py`

- MAD-Minimum je Komponente aus `[publikum.mad_minimum]`, **passend zur Skala** (Annahme S2-A16, Rückfrage 1):
  `wiedergabe 0.05` (r ist ein Anteil der Videolänge: 5 Prozentpunkte), `engagement 0.005` (e = Interaktionen je
  View, typisch 0,02–0,10: 0,5 Prozentpunkte), `reichweite 0.1` (v = ln(1 + Views): 0,1 ≈ 10 % mehr Views).
  Heute gilt 0,05 für alle drei; das drückt beim Engagement z von 1,35 auf 0,27 (Beispiel im Docstring von
  `robust_z`, publikum.py:486-495). Scores werden nie neu gerechnet – deshalb muss der Startwert vor dem ersten
  echten Score stehen. `score_fuer` übergibt `minimum` an `robust_z`; `score_teile` nennt das benutzte Minimum
  je Komponente; das Zahlenbeispiel im Docstring von `robust_z` wird angepasst.
- Hand-Eingabe: 4 Pflichtwerte wie heute, optional zusätzlich `kommentare shares saves` (7 Felder); Hilfetexte
  anpassen. Bestehende 4-Feld-Eingaben bleiben gültig.
- Tests: Minimum je Komponente wirkt (Engagement-Beispiel ergibt z ≈ 1,35 statt 0,27), **mit `minimum = 0,05` für
  alle drei ergibt sich das alte Ergebnis**, fehlender Abschnitt `[publikum.mad_minimum]` → Standardwerte, 4 und 7
  Felder, 5/6 Felder → Rückfrage.

## Welle 3 – Paket F Zusammenführen (Dirigent + ein Bauer)

Dateien: `tests/test_ende_zu_ende_stufe2.py`, `tests/test_speicher_wecken.py`-Muster für merkmale/mikro/
erwartung, `docs/PUBLIKUM.md`, `docs/REGIE.md`, `README.md`, `docs/ENTSCHEIDUNGEN.md` (Annahmen S2-*),
`docs/SPRINT-LOG-LERNSCHLEIFE.md`, `config/lokal.beispiel.toml`.

- Ende-zu-Ende 🧪: zwei künstliche Replays (mit/ohne Bot-Opfer) → analyze/decide/render → Punkte-Differenz =
  Fertig-Kriterium; `/gewichte` (Fake-Telegram) zeigt beide Quoten – die Publikums-Quote mit „1 Paar, zählt für
  die Schranke erst ab 10“; Post → Messungen → Score → Publikums-Paar → neue Gewichte → geänderte Erwartung;
  Migration auf alter DB (alte `gewichte`-Zeile, `mic_stand` NULL); Mic-Kindprozess mit gepatchtem Popen bekommt
  `--konfig` der Testkonfig.
- Installation (Anleitung für Florian, 🏠): Deploy, `pipeline merkmale nachtragen`, `pipeline replay <datei>` an
  bekannten Sniper-/Shotgun-Kills → `[merkmale.waffen]` in `config/lokal.toml` eintragen, prüfen, ob
  `faster-whisper` da ist, **prüfen, dass logind den Mic-Kindprozess nach dem SSH-Aufruf von n8n nicht beendet**
  (`loginctl show-user pipeline -p KillUserProcesses` bzw. `grep KillUserProcesses /etc/systemd/logind.conf`;
  erwartet „no“, Debian-Standard), `/gewichte` ansehen.

## Zur Nutzerfrage (für die Hauptsitzung)

- **Wie viele Clips sind verfügbar?** Das kann kein Agent sehen: Die Datenbank liegt nur auf dem Mini
  (`/var/lib/clip-pipeline/pipeline.db`), im Container gibt es keine, und Host-Zugriff haben wir nicht. Ablesen:
  `sudo -u pipeline /opt/clip-pipeline/.venv/bin/pipeline status` (Clips je Status, Matches, Aufnahmen) oder
  `/status` im Clip-Bot.
- **„Vom pve-big die Datenbank aufstocken“:** Die Datenbank selbst liegt auf dem Mini und enthält schon jeden Clip,
  der je gerendert wurde. pve-big ist das **Lager** (Dateien). Was im Puffer fehlt, sind Rohvideos (`eingang/`),
  die älter als 14 Tage sind (R4 hat nur diese 14 Tage übernommen). Ohne sie kann der Regisseur daraus keine
  Momente bauen, und nie verarbeitete Matches bleiben liegen. **`pipeline material` ist dafür der falsche Weg:** Im
  getrennten Betrieb (E19) weckt es nicht, sondern bricht ab (material.py:205-206). Der richtige Weg (nur Florian,
  nur tagsüber, kein Agent):
  1. pve-big von Hand einschalten (wie PUFFER.md R4), dann Probelauf – weckt nicht, kopiert nichts, zeigt Anzahl
     und GB: `sudo -u pipeline /opt/clip-pipeline/.venv/bin/pipeline lager uebernehmen --von /srv/big/clips --nach
     /srv/puffer --eingang-tage N --probelauf`. Faustregel aus R4: mehr als die Hälfte des Puffers (über 48 GB)
     → nicht weitermachen, melden.
  2. Echt, als eigener Dienst wie in R4 (`systemd-run --unit=clip-uebernahme …`). Die Übernahme weckt/hält pve-big
     selbst und kennt **keine** Nachtruhe (PUFFER.md) – deshalb nur tagsüber. Im Lager wird nur gelesen.
  3. Danach im CT: `pipeline scan --verarbeiten` (offene Matches → neue Clips in der Datenbank),
     `pipeline momente nachschneiden --tage N` (Multikills der älteren Tage mit Aktion neu schneiden),
     `pipeline stimmung --dateien --max 20` (kurze Rohvideos → Momente für den Regisseur; mehrmals möglich).
  Der Puffer löscht nichts (`[puffer].freigeben = false`) – die Morgenprüfung warnt, wenn er knapp wird.
  Für die neuen Merkmale von Stufe 2 braucht es pve-big nicht: `pipeline merkmale nachtragen` (kommt mit Stufe 2)
  rechnet sie aus `sessions/<ID>/replay.json` und den momente-Zeilen im Puffer nach.
- **Wie lange noch?** Schätzung, keine Zusage. Richtwert Stufe 1: Vertrag 11:23 → Ende-zu-Ende 13:00, also etwa
  1,5 h für 7 Pakete. Stufe 2 ist größer. Offen: Merge Regisseur 2.0 und Vertrag (Dirigent) → A1 (etwa 20–30 min)
  → Welle 2 mit 6 Paketen parallel (etwa 45–75 min) → F, Review-Panel, Befunde einarbeiten (etwa 45–60 min). Grob
  **2,5–3,5 h** bis Stufe 2 fertig und geprüft auf dem Branch ist, wenn nichts blockiert. Die Rückfragen halten den
  Bau nicht auf. Nur Rückfrage 1 (MAD-Minimum) muss beantwortet sein, bevor der erste echte Score entsteht.
  Danach bleibt 🏠 bei Florian: Deploy auf dem Mini, `merkmale nachtragen`, Waffen-Nummern kalibrieren,
  faster-whisper und logind prüfen (etwa 30–60 min). Die Spec sieht danach noch Stufe 3 (Rezepte) vor – erst die
  schließt die schnelle Lernschleife –, außerdem die Stufen 4 und 5. Die Publikums-Schleife braucht laut Spec §14
  bei einem Post pro Tag etwa vier Wochen Daten.

## Annahmen (bis Florian antwortet; gehen nach `docs/ENTSCHEIDUNGEN.md`)

- **S2-A1** `kommentar` und `lautstaerke` bleiben in MERKMALE (Spec „bisherige bleiben“); `lautstaerke` wird echt
  gemessen, `kommentar` ist weiter immer 0 (Spec §2 irrt bei `lautstaerke`). → 17 Merkmale.
- **S2-A2** `verbleibend` aus `eliminierungen` (knock = false) statt aus dem Killfeed.
- **S2-A3** `phase` bezieht sich auf die erste Aktion und die Länge meines Replays.
- **S2-A4** `[merkmale.waffen]` startet mit leeren Listen: Alles zählt als `sonstige`. Neue GunType-Zahlen kommen als
  **eine Sammelmeldung je Session** (nur noch nicht gemeldete Zahlen; Vermerk je Zahl in `meldungen`, nie
  verschickt), die die Ruhezeit abwartet – die Meldungen sind die Kalibrierhilfe (🏠).
- **S2-A5** Neue Merkmale ändern `punkte`/`begruendung` nur bei Clips im Status `vorbewertet`.
- **S2-A6** `mic_stand` wird gesetzt, wenn Whisper lief (`lachen` vorhanden) oder die Aufnahme sicher kein Mikro
  hat (`mikro_spur` ist `None`, kein `fehler`); ohne faster-whisper bleibt es NULL (nur `mic_laut`/`spitzen` werden
  übernommen). Messfehler zählen nicht als vollständig.
- **S2-A7** Mic-Schritt als losgelöster Kindprozess aus `cli._cmd_schritt` (render), `mic_je_lauf = 3`, mit
  `--konfig` der render-Konfig und Log neben der Datenbank; der Timer `clip-sitzungen` ist der Rückfall.
- **S2-A8** Der Mic-Schritt legt **neue** momente-Zeilen mit `claude=False` an (wie der Lern-Bot heute); diese
  bekommen keine spätere Claude-Nachprüfung.
- **S2-A9** **Abweichung von Spec §8.1** („alle Merkmale in clips.merkmale und momente.merkmale“): Datei-Momente
  (Rohvideos ohne Clip) bekommen keine Replay-Merkmale, kein `laenge` und kein `lautstaerke` – nur `kill_punkte`
  aus `max_gruppe` (heute immer 0), `victory_royale` und die Mic-Werte. Grund: `momente_aus_dateien` kennt weder
  Match noch Kills (stimmung.py:173). Folge in der Regisseur-Rangliste: Clip-Momente tragen zusätzlich
  Abzüge (`bot_opfer`, `laenge`) und Zuschläge (`platzierung`, `clutch`, `endgame`, `phase`), Datei-Momente
  nicht. Weil Datei-Momente keine Kills haben, liegen sie meist ohnehin hinten. → Rückfrage 4.
- **S2-A10** Die Publikums-Quote wird ab dem ersten Paar berechnet und angezeigt; die Schranke prüft sie erst ab
  10 Publikums-Paaren.
- **S2-A11** „Du magst X, das Publikum Y“ über das Vorzeichen des mittleren Merkmals-Unterschieds je Quelle.
- **S2-A12** Nach `pipeline publikum bewerten` wird neu gelernt.
- **S2-A13** „Letzte 50 gesendete“ = nach id, weil es keinen Sende-Zeitstempel gibt; Standardisierung robust
  (Median/MAD wie der Publikums-Score, eine Formel).
- **S2-A14** Zusammenschnitt-Entwürfe bekommen auch eine Erwartung und zählen bei den Entwürfen mit.
- **S2-A15** Erwartungs-Treffer „letzte 20“ nach `erwartungen.erstellt`.
- **S2-A16** MAD-Minimum des Publikums-Scores je Komponente passend zur Skala: wiedergabe 0,05 · engagement 0,005 ·
  reichweite 0,1 (statt 0,05 für alle). Muss vor dem ersten echten Score entschieden sein. → Rückfrage 1.
- **S2-A17** „Fehlt = unbekannt“: Für die zwölf neuen Merkmale wird beim Lernen ein Merkmal, das auf einer Seite
  eines Paars fehlt, nicht verglichen (d = 0). Beim Bewerten zählt es 0 (Spec §8.2). Ohne Mikro gelten die
  Mic-Werte als gemessen = 0.
- **S2-A18** Fehlt einer vorhandenen momente-Zeile die Mic-Analyse, holt der Mic-Schritt nur die Mic-Werte nach;
  Stimmung, Sicherheit und Quelle (auch „claude“) bleiben. Zeilen mit Messfehler werden nicht wiederholt.
- **S2-A19** Neue Testdateien je Paket statt Erweiterung von `test_ende_zu_ende.py`/`test_publikum.py`
  (Spec §13), damit parallele Pakete nie dieselbe Testdatei ändern.

## Rückfragen an Florian (nach Wichtigkeit, Bau wartet nicht)

1. **MAD-Minimum des Publikums-Scores** (vor dem ersten echten Score!): Heute gilt 0,05 für Wiedergabe, Engagement
   und Reichweite. Beim Engagement (Werte um 0,05, Streuung um 0,01) macht das aus einem klaren Ausreißer
   (z 1,35) fast nichts (z 0,27). Vorschlag passend zur Skala: Wiedergabe 0,05 · Engagement 0,005 · Reichweite 0,1.
   Einverstanden? Scores werden nie neu gerechnet – ein falscher Startwert bliebe für immer in den Daten.
2. **Regisseur:** `STIMMUNG_WERT` (episch +3 … chill +0,5) fällt laut Spec aus der Momentstärke – dadurch ändern
   sich Auswahl und Bogen spürbar. So lassen, oder als Stimmungs-Bonus in `punkte` behalten?
3. **Mic-Schritt:** Kindprozess aus `render` (schnell, läuft aber außerhalb von systemd; würde logind mit
   `KillUserProcesses=yes` beim Ende der SSH-Sitzung von n8n beendet – Debian-Standard ist „no“, geprüft ist es
   nicht) oder eine kleine systemd-Einheit (`clip-mikro.path`, sauberer, ein Host-Schritt mehr)?
4. **Datei-Momente** (Rohvideos ohne Clip) haben keine Replay-Merkmale und keinen Längen-Abzug (S2-A9). So lassen,
   oder sollen sie später einem Match zugeordnet werden (über die Aufnahmezeit), damit sie dieselben Merkmale
   bekommen?
5. **`kommentar`** ist immer 0 und wird durch `mic_*` ersetzt – streichen (16 Merkmale) oder stehen lassen?
6. **Alte Clips:** Sollen `punkte`/Begründung schon gesendeter Clips beim Nachtragen neu gerechnet werden
   (Bot zeigt dann andere Zahlen als damals) oder nur die Merkmale fürs Lernen?
7. **Publikum gegen dich:** Ab wie vielen Publikums-Paaren darf das Publikum dein gelerntes Modell blockieren
   (Vorschlag 10)?

## Vertragsentwurf (Commit „Stufe 2: Vertrag“, legt der Dirigent an)

Regel: Neue Funktionen = Docstring + `raise NotImplementedError`. Ausnahme (markiert mit `# Vertrag: … bis Paket X`):
Stubs, die ein BESTEHENDER Pfad aufruft, liefern einen neutralen Wert, damit die Suite grün bleibt.
Der Vertrag ist die **einzige Quelle** für Signaturen (Leitplanke 9). Signaturen bestehender Funktionen, die nur ein
Paket ändert (§3a stimmung, §8a regie/lernbot), stehen hier als **Festlegung**; der Vertrags-Commit ändert diese
Dateien nicht.

### 0. Import-Regel (in die Modul-Docstrings von merkmale, mikro, lernen, stimmung; Test in §10)
    merkmale  → db, replay, vorbewertung, zeit, konfig      nie: lernen, mikro, stimmung, verarbeitung
    lernen    → db, vorbewertung, zeit, merkmale (ab D)     nie: mikro, stimmung, verarbeitung
    mikro     → db, merkmale, lernen, konfig                stimmung NUR in clips_nachziehen (Funktions-Import)
    stimmung  → mikro, lernen (Modulebene, ab B) + wie heute
    Gewichte/Version holt der äußerste Aufrufer einmal per lernen.aktuelle und reicht sie durch.

### 1. src/clip_pipeline/vorbewertung.py (nur Namen – keine Formel)
    MERKMALE = ("kill_punkte", "victory_royale", "laenge", "lautstaerke", "kommentar",
                "platzierung", "sniper", "nahkampf", "bot_opfer", "phase", "endgame", "clutch",
                "mic_lachen", "mic_jubel", "mic_frust", "mic_laut", "spitzen")
    MERKMAL_NAMEN += {"platzierung": "Platzierung", "sniper": "Sniper", "nahkampf": "Nahkampf",
                      "bot_opfer": "Bot-Opfer", "phase": "Match-Phase", "endgame": "Endgame", "clutch": "Clutch",
                      "mic_lachen": "Mic: Lachen", "mic_jubel": "Mic: Jubel", "mic_frust": "Mic: Frust",
                      "mic_laut": "Mic: laut", "spitzen": "Ton-Spitzen"}   # alle ≤ 15 Zeichen (/gewichte-Tabelle)
    def roh_score(merkmale: dict[str, float], gewichte: dict[str, float]) -> float:
        """Σ Gewicht·Wert über MERKMALE, fehlend = 0, ungerundet – DIE Score-Formel (Spec §8.2). Paket A1."""
        raise NotImplementedError

### 2. src/clip_pipeline/merkmale.py (neu; Modul-Docstring: Spec §8.1/§8.2, Lernidee, „fehlt = unbekannt“, Import-Regel §0)
    MIC_DECKEL = 3        # Wortzähler/Mikro-Spitzen: mehr als 3 sagt nichts Neues (Spec §8.1)
    SPITZEN_DECKEL = 4    # Spielton-Spitzen (Spec §8.1)
    REPLAY_MERKMALE = ("platzierung", "sniper", "nahkampf", "bot_opfer", "phase", "endgame", "clutch")
    MIC_MERKMALE = ("mic_lachen", "mic_jubel", "mic_frust", "mic_laut", "spitzen")
    def aus_momente(moment_merkmale: dict) -> dict[str, float]: ...                     # A1 (ohne Mikro: mic_* = 0)
    def mic_vollstaendig(moment_merkmale: dict) -> bool: ...                             # A1 ("lachen" in mk) or (mikro_spur is None, kein fehler)
    def fuer_moment(clip_merkmale: dict | None, moment_merkmale: dict | None,
                    kill_tabelle: list[float]) -> dict[str, float]: ...                  # A1
    def aktualisiere_clip(con, clip_id: int, neue: dict[str, float],
                          gewichte: dict[str, float], version: int) -> bool: ...          # A1
    def waffen_kategorie(waffe: int | None, konfig) -> str: ...                          # A2, rein: "sniper"|"nahkampf"|"sonstige"
    def aus_replay(match, kill_zeiten: list[datetime], konfig) -> dict[str, float]: ...  # A2, rein (keine DB)
    def melde_unbekannte_waffen(con, konfig, sid: str, match) -> list[int]: ...          # A2, 1 Sammelmeldung je Session
    def nachtragen_replay(con, konfig, gewichte: dict[str, float], version: int, *,
                          session: str | None = None) -> dict: ...                        # A2 {"clips","geaendert","ohne_replay","waffen_gemeldet"}

### 3. src/clip_pipeline/mikro.py (neu; Docstring: §8.2/§12, warum losgelöst, warum nie wecken, Import-Regel §0)
    LOG_NAME = "mikro.log"   # liegt neben der Datenbank (konfig.datenbank.parent)
    def whisper_da() -> bool: ...                                                         # B: importlib.util.find_spec, kein Import
    def in_clip_uebernehmen(con, clip_id: int, moment_merkmale: dict,
                            gewichte: dict[str, float], version: int) -> bool: ...        # B
    def clips_nachziehen(con, konfig, *, session: str | None = None,
                         maximal: int | None = None) -> dict: ...                          # B (= stimmung --clips; holt Gewichte 1×)
    def nachtragen(con, konfig, gewichte: dict[str, float], version: int, *,
                   session: str | None = None) -> dict: ...                                # B (ohne Whisper)
    def starte_im_hintergrund(konfig, sid: str) -> bool: ...                               # B (Popen: nice 15, --konfig konfig.quelle,
                                                                                           #    DEVNULL, Log neben DB, start_new_session)

### 3a. src/clip_pipeline/stimmung.py – Festlegung für Paket B (Vertrags-Commit ändert die Datei nicht)
    MIC_SCHLUESSEL = ("mikro_spur", "lachen", "jubel", "frust", "jubel_laut", "jubel_laut_s")
    def _speichere(con, m, stimmung, sicherheit, quelle, mk, text, *,
                   gewichte: dict[str, float], version: int) -> None
    def _ergaenze_mic(con, m, mk_neu: dict, text: str | None, *,
                      gewichte: dict[str, float], version: int) -> None      # nur MIC_SCHLUESSEL + text; stimmung/quelle bleiben
    def analysiere(con, konfig, *, dateien=False, neu=False, claude=True, whisper=True, maximal=None,
                   nur_clips: list[int] | None = None, nur_mic: bool = False) -> dict   # holt Gewichte 1× vor der Schleife

### 4. src/clip_pipeline/erwartung.py (neu; Docstring: §10.5, logistische Funktion in Alltagssprache)
    ARTEN = ("clip", "entwurf")
    def moment_score(con, konfig, art: str, ziel_id: int, gewichte: dict[str, float]) -> float | None: ...
    def modell(con, konfig, art: str) -> dict | None: ...          # {"a","b","c","n_urteile","median","mad"}; None unter mindest_urteile
    def festschreiben(con, konfig, art: str, ziel_id: int) -> float | None: ...   # INSERT … ON CONFLICT (art, ziel_id) DO NOTHING
    def gespeichert(con, art: str, ziel_id: int) -> float | None: ...
    def trefferquote(con, art: str, letzte: int | None = None) -> tuple[int, int]: ...
    def trefferquote_text(con, konfig) -> str:
        return ""   # Vertrag: neutral bis Paket E (von cmd_gewichte/_cmd_gewichte schon aufgerufen)

### 5. src/clip_pipeline/lernen.py (nur Datenklassen, Standardwerte → alte Aufrufer laufen)
    @dataclass
    class Paar:
        besser: dict[str, float]       # NICHT mit 0 auffüllen: fehlt = unbekannt (S2-A17)
        schlechter: dict[str, float]
        art: str                   # battle | freigabe | publikum  (= Quelle, Spec §8.3)
        gewicht: float = 1.0       # battle 1,0 · freigabe [lernen].gewicht_freigabe · publikum 1,0
    @dataclass
    class Ergebnis:                # bestehende Felder unverändert; trefferquote = Nutzer-Quote (Battles + Freigaben)
        ...
        trefferquote_publikum: float | None = None          # ab dem 1. Publikums-Paar; None nur bei 0 Paaren
        trefferquote_publikum_start: float | None = None
        paare_je_quelle: dict[str, int] = field(default_factory=dict)
        ohne_mic: int = 0
        auseinander: str | None = None
    def publikum_paare(con, konfig) -> list[Paar]: raise NotImplementedError      # D

### 6. src/clip_pipeline/db.py
    # Stufe 2 (Spec §8.3): Publikums-Quote und Paar-Zahlen je Version – eigene Zeile (Merge-freundlich)
    MIGRATIONEN += [("gewichte", "trefferquote_publikum", "REAL"), ("gewichte", "trefferquote_publikum_start", "REAL"),
                    ("gewichte", "quellen", "TEXT")]      # quellen: JSON {"battle": n, "freigabe": n, "publikum": n}

### 7. src/clip_pipeline/publikum.py (nur Signatur, Verhalten gleich)
    def robust_z(x: float, basis: list[float], minimum: float = MAD_MINIMUM) -> tuple[float, float, float]:
        # Rumpf wie heute, max(mad, minimum) statt max(mad, MAD_MINIMUM)

### 8. src/clip_pipeline/cli.py und bot/app.py (nur Verdrahtung neuer Befehle/Schalter)
- `stimmung`: neue Schalter `--clips` und `--session ID` (pruefe_id); `_cmd_stimmung`:
  `if args.clips: _json(mikro.clips_nachziehen(con, konfig, session=args.session, maximal=args.max)); return 0`.
  `_vorab_ablehnen`: `stimmung --clips` ohne getrennten Betrieb → Exit 2 (wie `momente`).
- neu: `merkmale nachtragen [--session ID]`, sperren=False, nicht in WECKEN, ohne Puffer Exit 2:

      def _cmd_merkmale(args, konfig, con) -> int:
          version, gewichte = lernen.aktuelle(con, konfig)   # einmal holen, durchreichen (Import-Regel §0)
          _json({"replay": merkmale.nachtragen_replay(con, konfig, gewichte, version, session=args.session),
                 "mic": mikro.nachtragen(con, konfig, gewichte, version, session=args.session)})
          return 0

- `_cmd_gewichte`: am Ende `if t := erwartung.trefferquote_text(con, konfig): print(t)` (Rest gehört D).
- `bot/app.py cmd_gewichte`: Text + `erwartung.trefferquote_text(con, konfig)` (neutral) anhängen (Rest gehört E).

### 8a. Festlegungen für C (Vertrags-Commit ändert die Dateien nicht)
    regie.kandidaten(con, p, frueher=None, *, gewichte: dict[str, float], kill_tabelle: list[float]) -> list[Kandidat]
    lernbot._balken(werte: list[float]) -> str     # Min-Max-Normierung, negative Werte erlaubt

### 9. config/pipeline.toml (neue Schlüssel, je mit Kommentar)
    [merkmale]
    endgame_spieler = 10      # Endgame: beim Kill höchstens so viele Spieler übrig (Spec §8.1)
    clutch_vor_s = 30.0       # Clutch: selbst umgehauen höchstens so lange vor dem Kill …
    clutch_nach_s = 10.0      # … und danach so lange nicht gestorben
    mic = true                # Mic-Analyse im Hintergrund nach render (Whisper, nice 15); false = aus
    mic_je_lauf = 3           # höchstens so viele Clips je Hintergrundlauf (Whisper ~30–60 s je Clip, Sperre!)
    [merkmale.waffen]
    # GunType-ZAHLEN aus replay2json (kein Name!). Kalibrieren mit `pipeline replay <datei>` (docs/PUBLIKUM.md).
    # Nicht eingetragene Zahlen zählen als sonstige und kommen einmal als Sammelmeldung je Match.
    sniper = []
    nahkampf = []             # Shotgun, SMG, Spitzhacke
    sonstige = []             # bekannte andere Waffen (schaltet die Meldung ab)
    [vorbewertung.startgewichte]   # zusätzlich (Spec §8.1, Vorgaben von Florian, nicht gelernt)
    platzierung = 2.0
    sniper = 1.0
    nahkampf = 0.0
    bot_opfer = -2.0
    phase = 0.5
    endgame = 1.0
    clutch = 2.0
    mic_lachen = 1.0
    mic_jubel = 1.0
    mic_frust = 0.5
    mic_laut = 0.5
    spitzen = 0.25
    [lernen]                   # zusätzlich
    gewicht_freigabe = 0.5     # Freigabe-Paare zählen halb: indirekter Vergleich (Spec §8.3)
    mindest_publikum_paare = 10  # erst ab so vielen Publikums-Paaren prüft die Schranke die Publikums-Quote (S2-A10)
    [erwartung]                # Spec §10.5
    mindest_urteile = 10
    referenz = 50              # z gegen die letzten 50 gesendeten derselben Art
    schritte = 50
    lernrate = 0.1             # Schrittweite des Gradientenabstiegs (Spec nennt keine; Annahme)
    l2 = 0.1
    start_a = 1.0
    start_b = 0.5
    start_c = 0.0
    mad_minimum = 0.25         # Mindeststreuung der Moment-Scores (Punkte-Skala, nicht Publikums-Skala; Annahme)
    [publikum.mad_minimum]     # Mindest-Streuung je Komponente, passend zur Skala (Paket G, Annahme S2-A16, Rückfrage 1)
    wiedergabe = 0.05          # r = Anteil der Videolänge (0..1,2): 5 Prozentpunkte
    engagement = 0.005         # e = Interaktionen je View (typisch 0,02–0,10): 0,5 Prozentpunkte
    reichweite = 0.1           # v = ln(1 + Views): 0,1 ≈ 10 % mehr Views

### 10. Tests im Vertrag
- tests/test_vertrag_stufe2.py: MERKMALE hat 17 Einträge, jeder hat MERKMAL_NAMEN (≤ 15 Zeichen) und ein
  Startgewicht in pipeline.toml; `[publikum.mad_minimum]` hat drei Werte > 0; neue Module importierbar; alte DB
  bekommt die gewichte-Spalten (MIGRATIONEN); **Import-Regel §0 per AST** (merkmale importiert weder lernen, mikro,
  stimmung noch verarbeitung; lernen weder mikro, stimmung noch verarbeitung; mikro importiert stimmung nicht auf
  Modulebene).
- Gesamt-Testlauf grün (neue Merkmale sind überall 0). Möglicher Bruch: Tests auf Zeilenzahl von /gewichte oder
  Versionsnummer nach aktualisiere → Befund.

### Keine neue SQL-Datei: erwartungen und clips.mic_stand gibt es seit Stufe 1; die gewichte-Spalten kommen über MIGRATIONEN.

## Befunde der Plan-Prüfung (25.09.) – was eingearbeitet ist und was nicht

| # | Befund | Ergebnis |
|---|---|---|
| 1 | Gewichte/Version fehlen in `nachtragen_replay`/`in_clip_uebernehmen`, Kreis stimmung↔mikro | eingearbeitet: Signaturen mit `gewichte, version`, Import-Regel §0 + AST-Test, `_speichere`-Signatur §3a, Leitplanke 7 |
| 2 | `mic_vollstaendig` prüft `mikro_spur == 0` | eingearbeitet: `is None` und kein `fehler`, Tests für None/Spur 0/Fehler (A1, S2-A6) |
| 3 | Zeilen ohne Whisper bekommen nie ein Transkript | eingearbeitet: `analysiere(nur_mic=True)` + `_ergaenze_mic`, Stimmung bleibt (B, S2-A18) |
| 4 | Kindprozess ohne `--konfig`, Log-Pfad fest | eingearbeitet: `--konfig konfig.quelle`, Log neben der DB, Test (B) |
| 5 | MAD-Minimum-Startwerte = altes Verhalten | eingearbeitet: skalengerechte Startwerte, S2-A16, Rückfrage 1, Test „0,05 = alt“ (G) |
| 6 | Datei-Momente ohne Replay-Merkmale | **teilweise** – siehe unten |
| 7 | Nutzerfrage: `pipeline material` falsch, Restdauer fehlt | eingearbeitet: Abschnitt „Zur Nutzerfrage“ neu |
| 8 | `/gewichte` zeigt Publikums-Quote erst ab 10 Paaren | eingearbeitet: Anzeige ab dem 1. Paar, nur die Schranke ab 10 (D, F, S2-A10) |
| 9 | Eine Meldung je GunType-Zahl, nachts | eingearbeitet: Sammelmeldung je Session, Vermerke, Ruhezeit über `LEISE_MELDUNGEN` (A2, S2-A4) |
| 10 | `import faster_whisper` im render-Prozess | eingearbeitet: `mikro.whisper_da()` mit `find_spec` (B) |
| 11 | logind `KillUserProcesses` ungeprüft | eingearbeitet: Prüfschritt in F (🏠), Argument in Rückfrage 3 |
| 12 | `_balken`-Korrektur erst in E | eingearbeitet: nach C verschoben (Funktionsebene in `lernbot.py`) |
| 13 | Lernen „analysiert gegen nicht analysiert“ | **in anderer Form** – siehe unten |
| 14 | Widersprüche Plan ↔ Vertrag | eingearbeitet: Paket-Texte an den Vertrag angeglichen, Leitplanke 9, Rückfrage-Verweise korrigiert, Bruchgefahr 366 zu B, Testdateien als S2-A19 |

### Verworfen bzw. geändert übernommen (mit Begründung)

- **Befund 6, Teil „Replay-Merkmale auch für Datei-Momente rechnen“ – verworfen.** Der Vorschlag setzt voraus, dass
  Datei-Momente `match_id` und `kill_sekunden` haben. Das stimmt im Code nicht: `stimmung.momente_aus_dateien` legt
  sie als `Moment(f"datei:{rel}", datei, 0.0, dauer, start)` an – ohne Match, ohne Ereignisse, `max_gruppe = 0`
  (stimmung.py:173). `aus_replay` hätte nichts, dem es Kills zuordnen könnte. Die dazu beschriebene Verzerrung
  („Bot-Double als Datei-Moment vor demselben Double als Clip“) tritt deshalb nicht auf: Datei-Momente haben keine
  Kill-Punkte. **Übernommen** ist der zweite Teil: Die Abweichung von §8.1 steht jetzt ausdrücklich als S2-A9 im
  Plan, mit der verbleibenden Asymmetrie (Clip-Momente tragen `bot_opfer`/`laenge`/`platzierung`/…, Datei-Momente
  nicht), als Rückfrage 4, und C hat einen Gleichheitstest Clip-Moment ↔ Datei-Moment (eine Gruppe,
  Replay-Merkmale 0, `laenge` 0). Eine Match-Zuordnung über die Aufnahmezeit wäre neue Logik außerhalb von
  Stufe 2.
- **Befund 13 – in anderer Form übernommen.** Der Vorschlag war ein Paar-Feld `mic_beide` (d = 0 für alle
  Mic-Merkmale, wenn eine Seite `mic_stand` NULL hat). Das hätte zwei Nachteile. Ohne faster-whisper bleibt
  `mic_stand` überall NULL, und dann würden auch `mic_laut`/`spitzen` nie gelernt, obwohl sie gemessen sind. Und
  der Vergleich neuer gegen alte, noch nicht nachgetragene Replay-Merkmale bliebe ungeschützt. Stattdessen gilt
  je Schlüssel: „fehlt = unbekannt“ (S2-A17). `aus_momente` lässt Ungemessenes weg, ohne Mikro setzt es 0.
  Lernen vergleicht ein neues Merkmal nur, wenn es auf beiden Seiten da ist. Für die alten fünf Merkmale bleibt
  alles wie heute, alte Tests brechen dadurch nicht. Der Vertrag braucht kein neues Feld, nur den Hinweis an
  `Paar`.
- **Befund 9, Detail – geändert übernommen.** Die Vermerke je Zahl stehen in `meldungen` mit `gesendet = erstellt`.
  Ein normaler `db.meldung`-Eintrag würde sonst selbst verschickt, und genau diese Nachrichtenflut soll der Befund
  vermeiden. Dazu wartet die Sammelmeldung über `LEISE_MELDUNGEN` die Ruhezeit ab.
- **Befund 14, Teil „Bruchgefahr test_ende_zu_ende_publikum.py:366 betrifft höchstens B“** – übernommen, aber als
  „prüfen“ bei B geführt statt gestrichen. Der Lern-Bot ruft vor Entwürfen `stimmung.analysiere` auf
  (lernbot.py:145 im zusammengeführten Stand). Ob der Test dabei clip:1 analysiert, lässt sich ohne Lauf nicht sicher sagen.
