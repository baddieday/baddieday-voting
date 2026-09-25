# Plan Stufe 1 „Fundament“ – Lernschleife Publikum

Stand 25.09.2026 (überarbeitet nach der Plan-Prüfung, siehe letzter Abschnitt) · Branch `lernschleife-publikum`
(Basis 0af709b) · Spec §5, §6, §7.1, §10.4, §12, §13, §14 Punkt 1

**Fertig, wenn (Spec §14):** ein echter TikTok-Post per Screenshot gemessen und bewertet ist (🏠, am Mini).
Wegen Annahme A1 (Score erst ab `alter_tage`) heißt das: Messung ab Tag 3, Score nach 7 Tagen – für die Abnahme
kann `[publikum].alter_tage = 3` in `config/lokal.toml` stehen (Beispiel in `config/lokal.beispiel.toml`).
**Erwartetes Ergebnis der Abnahme:** `bewertet_utc` gesetzt, Score **0** mit Vermerk „Basis zu klein“ (unter 5
bewerteten Posts gibt es keinen Score ≠ 0, Spec §6.3). Scores werden nie überschrieben: Posts, die in dieser
Probezeit bewertet werden, behalten ihre Tag-3-Messung – deshalb danach `alter_tage` wieder entfernen.
Hier im Container: alles 🧪 mit künstlichem Material (Fake-Telegram, gefälschtes `claude`, Farbtest-Videos).

## Harte Leitplanken für jedes Paket

1. **Nie wecken.** Kein `pruefe_speicher(wecken=True)`, nichts in `cli.WECKEN`, kein `big.herzschlag`/`wach_halten`
   in neuem Code. Die Weckmuster in `lernbot.baue_entwurf` (lernbot.py:153) und `bot/app.sende_paket`
   (app.py:239) **nicht** kopieren. Je neuem Modul ein Test nach `tests/test_speicher_wecken.py:27-54`
   (`host`/`wol_mac` setzen, `Konfig._host_erreichbar` → False, `konfig.sende_wake_on_lan` und `big.wach_halten`
   patchen, `assert_not_called`), wo sinnvoll zusätzlich `socket.create_connection` als AssertionError
   (Muster `tests/test_lager.py:31-52`).
2. **Sperre:** Rendern der Upload-Fassung unter `sperre(konfig.datenbank.with_suffix(".lock"),
   warten_s=[sperre].warten_s)` im Thread mit eigener DB-Verbindung (E9, wie lernbot.py:151-158, aber ohne
   `big.herzschlag` und ohne Wecken). Belegt → `sperre.Gesperrt`. `pipeline publikum bewerten` ist reine
   DB-Arbeit → `sperren=False`.
3. **Portables SQL (E3):** `INSERT … ON CONFLICT … DO NOTHING/UPDATE`, nie `INSERT OR IGNORE`, Zeiten mit
   `zeit.iso()`. Bestehende CHECKs nicht anfassen. **`db.transaktion` ist nicht verschachtelbar** (`BEGIN
   IMMEDIATE`): Funktionen, die „in der Transaktion des Aufrufers“ laufen (post_anlegen, link_nachtragen,
   speichere_messung), öffnen keine eigene; wer zwei Schritte in eine Transaktion legen will, zieht den Rumpf in
   eine Hilfsfunktion ohne Transaktion (siehe Paket d).
4. **Zeit testbar:** Im Vertrag hat jedes neue Modul `from .zeit import iso, jetzt` auf Modulebene (Tests:
   `mock.patch.object(modul, "jetzt", …)`, Muster tests/test_bot_app.py:110), öffentliche Funktionen haben
   `zeit: datetime | None = None` (auch `lernbot_zahlen.aufraeumen(app, zeit=None)`). Den im Startauftrag
   genannten Zeit-Ersatz in tests/hilfen.py gibt es nicht; dies sind die echten Muster.
5. **Keine Secrets**, auch nicht in Logs: httpx bleibt auf WARNING; Bilder, Chat-IDs und Claude-Rohantworten nie
   ins Log (Claude-JSON gehört nur in `publikum_messungen.roh`), Pfade höchstens gekürzt. **Telegram-Downloads:**
   Nach `get_file()` enthält `File.file_path` die URL **mit dem Bot-Token** (`…/file/bot<TOKEN>/…`, geprüft in
   python-telegram-bot 22.8, `_bot.py:4131`). `file_path`/URLs nie loggen, nie an den Nutzer schicken, nie in
   eine Ausnahme-Nachricht packen; bei Download-Fehlern nur `type(e).__name__` loggen. Der Stolperdraht
   `tests/test_keine_secrets.py` sieht nur Repo-Dateien, keine Logs – deshalb ein eigener Test in (b).
6. **Zusammenführbarkeit mit Regisseur 2.0** (Branch `claude/compassionate-curie-uwv39i`): `entwurf._bild`,
   Effektkette, `rendere` (außer den drei Vertrags-Parametern), `regie.py`, `schema.py`, `regie_lernen.GRUENDE`,
   `lernbot.HILFE`/`knoepfe_gruende`/`entwurf_text`, `cli`-`bewerte`-choices, `[regie.effekte]` **nicht**
   anfassen. Neues in neue Module oder ans Dateiende; Signaturen nur um Schlüsselwort-Parameter am Ende erweitern.
   Die festen Stellen stehen im Abschnitt „Zusammenführen mit Regisseur 2.0“.
7. **Kommentare nach Startauftrag §4:** Modul-Docstring mit Lernidee, Docstring je öffentlicher Funktion mit
   Zahlenbeispiel und Fehlerverhalten, jede Formel genau einmal (Score-Formeln nur in `publikum.py`, Short-Dauer
   nur in `shorts.gesamtdauer`, Budget nur in `entwurf.rendere`), jede Zahl ohne Konfig kommentiert.
8. **Tests laufen so** (sonst lädt Python den Code des Haupt-Checkouts):
   `cd <worktree> && PYTHONPATH=src /home/user/baddieday-voting/.venv/bin/python -m unittest tests.MODUL`.
   Der Gesamtlauf dauert > 15 min (Rendern); modulweise laufen lassen und die letzte Zeile zitieren.
   (`tests.test_db` gibt es nicht – die bisherige Migrationsprobe steht in `tests.test_veroeffentlichung`.)
9. **Git:** Bauer fügen nur mit `git add <datei>` hinzu, nie mit `git add -A`/`.`; kein Rebase, kein Force-Push.

## Vertrag (angelegt, nicht committet)

| Datei | Inhalt | Stand |
|---|---|---|
| `src/clip_pipeline/publikum.sql` | alle fünf Tabellen wortgetreu aus Spec §5 | echt |
| `src/clip_pipeline/db.py` | `publikum.sql` im Datei-Tupel; `MIGRATIONEN += [clips.mic_stand, entwuerfe.rezept, entwuerfe.upload_pfad]` als eigene Zeile (Originalzeile unverändert) | echt (A24) |
| `pyproject.toml` | `publikum.sql` in package-data | echt |
| `src/clip_pipeline/shorts.py` | `endcard_s(konfig)`, `gesamtdauer(dauer_s, konfig)`; `filtergraph` nutzt sie (Graph zeichengleich, 12 Fälle geprüft) | echt |
| `src/clip_pipeline/entwurf.py` | `rendere(…, volle_aufloesung=False, crf=23, kbit_max=ENTWURF_KBIT)` an 6 Stellen (Signatur, Verkleinerung, Budget-Deckel, crf, Rückfall VA-API→CPU reicht alles weiter, `ZuGross` statt MedienFehler bei zu großer Datei); am Ende `ZuGross(kbit)`, `UPLOAD_DATEI`, `UPLOAD_VERSUCHE`, `UPLOAD_CRF`, Signaturen `upload_ziel`, `upload_fassung` | rendere echt, Rest Signaturen |
| `src/clip_pipeline/publikum.py` | Posts (`post_anlegen` ohne url, `post_zu`, `link_nachtragen`), Messungen, Plausibilität, Hand-Eingabe, Score (Basis je Komponente, nur Vorgänger), `bewerte_alle` mit Fehlerzähler, `post_plattformen` | Signaturen |
| `src/clip_pipeline/claude_aufruf.py` | gemeinsamer `claude -p`-Aufruf (`--no-session-persistence`, `_json_aus_text` aus verarbeitung), `frage_json`, `protokolliere` | Signaturen |
| `src/clip_pipeline/screenshot.py` | `BILD_STAMM`, `BILD_ENDUNGEN`, `prompt(konfig, bild_name)`, `normalisiere`, `lies_zahlen` | Signaturen |
| `src/clip_pipeline/lernbot_zahlen.py` | ein offener Vorgang, `pl:`/`pm:<post_id>:`, Text „#17 …“, `endung_fuer`, `aufraeumen(app, zeit)` | `registriere` echt, Rest Signaturen |
| `src/clip_pipeline/lernbot_paket.py` | `knoepfe_nach_fertig(eid, bewertung, fmt)` (nur 👍-Short), `paket_erlaubt`, Kürzel aus `aktionen`, Upload-Paket, Häkchen, `/link` | `registriere`, `knoepfe_nach_fertig`, `PLATTFORM_KUERZEL` echt |
| `src/clip_pipeline/lernbot_publikum.py` | `/publikum` (mit Claude-Wochenzahl), Ruhezeit-Filter, Meldung nach `bewerten`, `HILFE_ZUSATZ` | `registriere`, `HILFE_ZUSATZ` echt |
| `src/clip_pipeline/lernbot.py` | nur Einhängen: `registriere` der drei Module vor `CallbackQueryHandler(bei_klick)`; im x-Zweig `knoepfe_nach_fertig(eid, bewertung, zeile["format"])`; `lernbot_zahlen.aufraeumen` in `_schleife` | echt |
| `src/clip_pipeline/caption.py` (Ende) | `entwurf_caption(con, liste, konfig)` – Fakten aus der DB | Signatur |
| `src/clip_pipeline/schemas/publikum.schema.json` | alle Felder optional/null, `minimum 0`, **kein** `maximum`, **kein** `additionalProperties: false` | echt |
| `templates/screenshot-prompt.txt` | fester Prompt, Platzhalter `{bild}` | echt |
| `config/pipeline.toml` | `[lernbot]` screenshot_claude/_prompt/_timeout_s; `[publikum]` plattformen, alter_tage, mindest_alter_tage, fenster, paar_abstand, max_paare, upload_ordner; `[publikum.gewichte]` | echt |
| `config/lokal.beispiel.toml` | Block **hinter `[speicher]`** (nicht am Dateiende): `screenshot_claude = false`, `[decide].programm` mit vollem Pfad, `alter_tage = 3` mit Hinweis auf Score 0/Einfrieren | echt |
| `tests/test_keine_secrets.py`, `.gitignore` (`.claude/`) | byte-gleich aus 0f91d98 (`git show 0f91d98:<pfad> > <pfad>`, kein Index, kein Commit; Blobs e1f2c4b / dfa42be geprüft) | echt |
| `.env.example` | unverändert – Stufe 1 braucht kein neues Secret (TikTok erst Stufe 4) | – |

Prüfungen nach dem Überarbeiten (Ausgaben zitiert):
- `tests.test_bot_app tests.test_speicher_wecken tests.test_veroeffentlichung tests.test_caption_aufraeumen
  tests.test_keine_secrets tests.test_deploy_puffer tests.test_elo_lernen_bot` → `Ran 99 tests in 54.443s` / `OK`
- `tests.test_entwurf tests.test_ende_zu_ende` → `Ran 21 tests` / `OK`; `tests.test_lernbot` → `Ran 10 tests` / `OK`
  (Zeiten unter „Testlauf nach dem Überarbeiten“).
- Migration auf einer DB aus `HEAD:schema.sql`+`regie.sql`+`lager.sql` mit Entwurfszeile, zweimal `db.verbinde`:
  Spalten `mic_stand`, `rezept`, `upload_pfad` da, alle fünf Tabellen da, Zeile erhalten, `posts` leer,
  Fremdschlüssel `publikum_messungen → posts` aktiv, idempotent.
- Secret-Scan (Muster aus `test_keine_secrets.funde`) über **alle** getrackten und neuen Dateien: 144 Dateien,
  0 Funde. Merge-Probe (`git merge-file`, Basis b394f4a gegen den R2.0-Stand im Haupt-Checkout, nur gelesen)
  für alle 10 geänderten Dateien: 0 Konflikte; `entwurf.py` danach kompilierbar.

## Reihenfolge

```
Vertrag committen (Dirigent) ─► (a) publikum.py ─► (a) zusammenführen ─► Worktrees für (b)–(e) abzweigen
(f) test_secrets_dateien ─ sofort, unabhängig          ├─► (b) Screenshot/Hand ─┐
                                                        ├─► (c) Upload/Link ────┤
                                                        ├─► (d) Clip-Bot-Posts ─┼─► Zusammenführen ► (g) Ende-zu-Ende
                                                        └─► (e) CLI/Timer/Doku ─┘   ► Gesamtlauf ► Panel ► Commit
```
**Vor dem Verteilen (Dirigent):** Der Vertrag ist nicht committet; Worktrees zweigen aber von einem Commit ab.
Empfohlen: Commit „Stufe 1: Vertrag“ (Startauftrag §3.2 erlaubt Commits auf diesem Branch), dann (a) bauen und
zusammenführen, erst danach die Worktrees für (b)–(e) abzweigen – sonst starten sie mit NotImplementedError.
Geht kein Commit, arbeiten die Bauer nacheinander im selben Worktree (die Dateien sind disjunkt). (f) braucht
keinen Vorlauf (Stolperdraht und `.gitignore` sind schon im Vertrag).

## Arbeitspakete

### (a) Publikum-Kern: Posts, Messungen, Score · Spec §5, §6, §7.1 (Plausibilität, Hand), §10.4 (Rezept, Dauer)
- **Dateien:** `src/clip_pipeline/publikum.py`; `tests/test_publikum.py` (neu).
- **Tests zuerst** (`tests/test_publikum.py`, MitSpeicher, feste Zeiten):
  - `komponenten`: Zahlenbeispiel aus dem Docstring; r aus `voll_prozent`, wenn `wiedergabe_s` fehlt; r ≤ 1,2;
    ohne views → e/v None; fehlende Zähler = 0 mit Vermerk (A10).
  - `robust_z`: Median/MAD-Beispiel, MAD-Minimum 0,05, Begrenzung ±2,5, leere Basis → ValueError; Zahlenbeispiel
    mit realistischem Engagement (e 0,05…0,09 → z 0,27 statt 1,35), damit der Effekt des MAD-Minimums sichtbar ist.
  - `score_fuer`: Basis < 5 → alle z = 0 + „Basis zu klein“; Post ohne Wiedergabe → 0,6/0,4 + „ohne Wiedergabe“;
    **Basis mit 20 Posts, aber 0 bzw. genau 4 r-Werten → 0,6/0,4 + „Basis ohne Wiedergabe“, mit 5 r-Werten
    normal**; `score_teile` enthält alle Felder aus §6.5 plus `messung_alter_tage`, `basis_n_r`; deterministisch.
  - `vergleichsbasis`: nur Posts derselben Plattform mit `gepostet_utc` **vor** dem Post; ein spät bewerteter Post
    wird nicht mit später geposteten verglichen; höchstens `fenster`; kaputtes score_teile → ValueError.
  - `waehle_messung`: nächste an 7 Tagen, nichts unter 3 Tagen, ohne views übersprungen, Gleichstand → spätere.
  - `ist_faellig`/`bewerte_alle`: Post < 7 Tage → nicht bewertet; danach genau einmal; zweiter Lauf ändert nichts
    (Idempotenz); älteste zuerst, früher bewertete zählen zur Basis späterer; andere Plattform zählt nicht;
    **kaputtes score_teile in der Basis → dieser Post `fehler`, die anderen werden trotzdem bewertet**.
  - `post_anlegen` (ohne url): zweimal → ein Post, `gepostet_utc` bleibt der erste, Rückgabe (id, False);
    `post_zu` findet ihn (und None für andere Plattform); `link_nachtragen` setzt url/video_id und ersetzt einen
    falschen Link; `video_id_aus_url` (TikTok lang, vm.tiktok.com → None, YouTube → None); unbekannte
    Plattform → ValueError; `post_plattformen` ignoriert "clipbattle" (mit Log-Warnung).
  - `pruefe_plausibel`: sinkende Zähler, wiedergabe > 1,5·Dauer, voll_prozent > 100, None wird nicht geprüft.
  - `lies_hand_eingabe`: „1240 61 6.8 34“, „1240 61 – 34“, Komma, negative Zahl, falsche Anzahl → ValueError mit Text.
  - `laenge_stufe`, `rezept_fuer_clip`, `rezept_fuer_entwurf` (beats 1/2/4, vorhandenes `entwuerfe.rezept` gewinnt),
    `clip_post_daten` ohne Dateizugriff (Datei-Pfad absichtlich ungültig), dauer_s = `shorts.gesamtdauer`
    (**`[shorts].endcard = false` → Clip-Länge**), `entwurf_post_daten` aus Schnittliste.
  - Migration auf alter DB (Muster tests/test_veroeffentlichung.py:62-70, `ALTER TABLE … DROP COLUMN` bzw. DB aus
    `schema.sql`+`regie.sql`+`lager.sql` mit Beispielzeilen) → `db.verbinde` zweimal → neue Tabellen und Spalten
    da, Daten erhalten.
  - Nie wecken: `bewerte_alle` mit gesetztem host/wol_mac, `_host_erreichbar` False → `sende_wake_on_lan` nie.
- **Fertig, wenn:** alle Signaturen umgesetzt, Test-Modul grün, keine Score-Formel außerhalb von `publikum.py`,
  keine Dauer-Formel außerhalb von `shorts.gesamtdauer`.

### (b) Screenshot und Hand-Eingabe im Lern-Bot · Spec §7.1, §12 (Claude zählen)
- **Dateien:** `src/clip_pipeline/claude_aufruf.py`, `src/clip_pipeline/screenshot.py`,
  `src/clip_pipeline/lernbot_zahlen.py`, `templates/screenshot-prompt.txt`,
  `src/clip_pipeline/schemas/publikum.schema.json`; `tests/test_screenshot.py`, `tests/test_lernbot_zahlen.py` (neu).
- **Umsetzung:** `_json_aus_text` aus `verarbeitung` importieren (wie stimmung.py:38), nicht kopieren. Aufruf mit
  `--no-session-persistence` (kein Bildarchiv unter ~/.claude/projects). Ein offener Vorgang in
  `bot_data["publikum"]` (Docstring); ein neues Bild ersetzt ihn und löscht das alte sofort. Speichern im
  Event-Loop in `db.transaktion`.
- **Tests zuerst:**
  - `claude_aufruf.frage_json` mit gefälschtem `claude` (Muster tests/test_ende_zu_ende.py:258-275, selektiv
    `b[0] == "claude"`): gültig; `is_error`; `result` ohne JSON; Schema-Verstoß; Exit ≠ 0; `TimeoutExpired`;
    kein `claude` im PATH → jeweils `daten None` + Hinweis, nie Ausnahme. Aufrufargumente
    (`-p --output-format json --allowedTools Read --no-session-persistence`, `cwd` = Arbeitsordner mit dem Bild).
    `protokolliere` schreibt `ereignisse.art = 'claude'`.
  - `screenshot.normalisiere`: 1240.0 → 1240, 12.5 Views → ValueError, **NaN/Infinity → ValueError**, fehlende
    Felder → None, **Zusatzfeld („profilaufrufe“) → ignoriert, Werte trotzdem gelesen**; **voll_prozent 120 geht
    durchs Schema** (die Rückfrage macht pruefe_plausibel); `prompt` ersetzt `{bild}` und lässt das JSON-Beispiel
    heil; `lies_zahlen` räumt den Temp-Ordner auch im Fehlerfall weg; Endung `.png` → Datei `screenshot.png`.
  - Fake-Telegram (FakeBot/FakeQuery aus `tests/test_lernbot.py` importieren, **nicht** ändern; eigene Erweiterung
    um Foto-Download, `reply_text`, Fremd-Klick): Foto mit „#17“ → gespeichert + Bestätigung; ohne Nummer →
    Knöpfe `pl:` (≤ 64 Byte) → Klick → ausgewertet; unplausibel → Rückfrage `pm:17:ok` speichert, `pm:17:hand` →
    Hand-Eingabe; **Hand-Eingabe unplausibel (Views gesunken) → Rückfrage**; **Text „#17 1240 61 6.8 34“ ohne
    Bild → gespeichert (quelle hand)**; kaputtes JSON → direkt Hand-Eingabe; `screenshot_claude = false` → kein
    Claude-Aufruf; **zwei Fotos hintereinander → nur ein Temp-Ordner übrig + Hinweis**; **PNG als Datei → .png;
    HEIC als Datei → „Bitte als Foto schicken“, kein Aufruf**; Doppelklick bzw. `pm:` einer alten Nachricht →
    „Schon erledigt.“; fremde Person → „Nicht erlaubt.“; `aufraeumen` nach 10 min (feste Zeit) → Bild gelöscht +
    Hinweis; Bild nach Auswertung gelöscht (auch bei Fehler); Handler-Reihenfolge in `baue_app` (`pl:` landet
    nicht in `lernbot.bei_klick`).
  - **Token nie im Log:** Download schlägt fehl, `file_path` enthält einen künstlich zusammengesetzten Fake-Token
    → `assertLogs` enthält weder „bot“+Token noch die URL; die Antwort an den Nutzer auch nicht.
  - Nie wecken für den ganzen Fluss.
- **Fertig, wenn:** beide Test-Module grün, `lernbot.py` unverändert, Prompt deckt „1,2K“, „0:07“, „7,3 s“ ab.

### (c) Upload-Fassung, Upload-Paket, Häkchen und /link im Lern-Bot · Spec §10.4
- **Dateien:** `src/clip_pipeline/lernbot_paket.py`, `src/clip_pipeline/entwurf.py` (**nur** die Rümpfe von
  `upload_ziel`/`upload_fassung` am Dateiende – `rendere` ist im Vertrag fertig und wird nicht angefasst),
  `src/clip_pipeline/caption.py` (nur Rumpf von `entwurf_caption`); `tests/test_upload_paket.py`,
  `tests/test_lernbot_paket.py` (neu).
- **Umsetzung:** `upload_fassung` ruft `rendere(liste, ziel, konfig, max_bytes=…, volle_aufloesung=True,
  crf=UPLOAD_CRF, kbit_max=…)` – dieselbe Budget-Rechnung wie der Entwurf, keine zweite. Erster Versuch
  `kbit_max = [shorts].max_kbit`; bei `ZuGross` neu mit `kbit_max = int(0,75 · fehler.kbit)`, `max_bytes` bleibt;
  höchstens `UPLOAD_VERSUCHE`. Andere MedienFehler nicht wiederholen. Vorab prüfen: Format short, getrennter
  Betrieb, Dateien da (Docstring). `max_bytes` inline wie `entwurf()` (nach dem Merge: R2.0s `_max_bytes`); keine
  eigenen Helfer `_max_bytes`/`_zeile` anlegen (bringt R2.0 mit). Idempotent über `entwuerfe.upload_pfad`.
  `lernbot_paket`: Link-Erkennung nur über `aktionen.plattform_aus_url`, Post-Suche nur über `publikum.post_zu`,
  /link = `post_anlegen` + `link_nachtragen` in einer `db.transaktion`. Aufrufer holt die Sperre.
- **Tests zuerst** (Tests, die rendern, setzen `[lager].wurzel` auf einen Temp-Ordner = getrennter Betrieb):
  - `upload_fassung` mit Farbtest-Material (regie_hilfen): 1080×1920 per ffprobe, Größe < max_bytes, `-crf 20`
    im Befehl (libx264); zweiter Aufruf überspringt; fehlende Moment-Datei → MedienFehler mit verständlichem Text
    (ffmpeg startet nicht); **Zusammenschnitt → MedienFehler „nur für Shorts“; nicht getrennt → KonfigFehler**;
    **`rendere` gepatcht wirft zweimal `ZuGross(…, 7000)` → dritter Aufruf mit kbit_max 5250, max_bytes
    unverändert; dreimal → MedienFehler; ein anderer MedienFehler → kein zweiter Versuch**; **VA-API-Rückfall
    (encoder gepatcht auf h264_vaapi, erster ffmpeg-Lauf scheitert) → zweiter Befehl libx264 in 1080×1920 mit
    crf 20**; kein NVENC im Befehl.
  - `caption.entwurf_caption(con, liste, konfig)`: Quellenangabe aus `musik.quelle` steht drin; Kill-Typ aus
    `clips.max_gruppe` (Triple → #triplekill), Victory Royale → #victoryroyale, Moment ohne Clip → „fortnite“;
    keine erfundenen Zahlen; ohne Musik keine Quellenzeile.
  - Fake-Telegram: ✅ nach 👍 zeigt `pk:<eid>:`, nach 👎 nichts (bestehender Test bleibt grün), **👍 auf einen
    Zusammenschnitt → kein pk:**; `pk:` → `send_document` (filename `clip-battle_e<eid>.mp4`), Caption als
    `<pre>`, Checkliste `pt:<eid>:t`; Doppelklick während des Renderns; Häkchen legt Post an (Doppelklick → ein
    Post); `/link e41 <url>` und `/link 41 <url>` → Post + Post-Nummer; **zweiter /link ersetzt den Link**;
    clip-battle.de-Link → Hinweis, kein Post; 👎-Entwurf und Zusammenschnitt → kein Post; Sperre wird geholt
    (belegte Sperre mit kleinem `warten_s` → klare Meldung).
  - Nie wecken (Speicher offline → Meldung, `sende_wake_on_lan` nie).
- **Fertig, wenn:** Test-Module grün, `tests.test_entwurf` und `tests.test_lernbot` weiter grün, `git diff` an
  `entwurf.py`/`caption.py` gegenüber dem Vertrag nur in den Rümpfen am Dateiende.

### (d) Clip-Bot: Posts aus Häkchen und /link · Spec §10.4 letzter Punkt
- **Dateien:** `src/clip_pipeline/bot/aktionen.py` (`plattform_erledigt`, `link_speichern`),
  `src/clip_pipeline/bot/app.py` nur falls die /link-Antwort die Post-Nummer nennen soll; `tests/test_bot_app.py`
  (erweitert, wie Spec §13).
- **Umsetzung:** Den Rumpf von `plattform_erledigt` in eine Hilfsfunktion **ohne** eigene Transaktion ziehen
  (`db.transaktion` ist nicht verschachtelbar). Darin: wenn `plattform in publikum.post_plattformen(konfig)` →
  `publikum.post_anlegen(art="clip", daten=publikum.clip_post_daten(...))`. `plattform_erledigt` = eine
  Transaktion um die Hilfsfunktion; `link_speichern` = **eine** Transaktion um Hilfsfunktion + `UPDATE
  veroeffentlichungen SET url` + `publikum.link_nachtragen(post_zu(...))`. Nie für `clipbattle`. Kein
  Dateizugriff im Handler (dauer_s aus der DB über `shorts.gesamtdauer`, A8).
- **Tests zuerst:** Häkchen TikTok → ein Post (art clip, ziel `clip:<id>`, rezept `stark_zuerst/…/none/roh`,
  dauer_s = Clip + 2,5 − 0,5); Doppelklick → weiter ein Post, erster Zeitpunkt; /link danach trägt url/video_id
  nach; /link ohne vorheriges Häkchen legt an; clip-battle.de → kein Post; YouTube bei
  `plattformen = ["tiktok"]` → kein Post, bei `["tiktok","youtube"]` → Post; nicht freigegebener Clip → wie bisher
  abgelehnt; Fehler in `post_anlegen` bzw. `link_nachtragen` rollt auch Häkchen und url zurück (eine
  Transaktion); alle alten Tests in `test_veroeffentlichung`/`test_bot_app` grün.
- **Fertig, wenn:** Test-Modul grün; `veroeffentlichungen`-Verhalten unverändert.

### (e) `pipeline publikum bewerten`, Timer, /publikum, Ruhezeit, Doku · Spec §12, §14
- **Dateien:** `src/clip_pipeline/cli.py`, `src/clip_pipeline/lernbot_publikum.py`, `src/clip_pipeline/lernbot.py`
  (nur `sende_meldungen` → `lernbot_publikum.faellige_lern_meldungen` und `cmd_hilfe` → `HILFE +
  lernbot_publikum.HILFE_ZUSATZ`; **`HILFE` selbst unverändert**), `deploy/systemd/clip-publikum.service`,
  `deploy/systemd/clip-publikum.timer`, `deploy/systemd/clip-lernbot.service.d/claude.conf` (Drop-in, A17),
  `docs/PUBLIKUM.md` (neu), `docs/REGIE.md` (ein Punkt im Abschnitt „Lern-Bot (Telegram)“ hinter „Abends um
  21:00 …“ mit Verweis auf PUBLIKUM.md – R2.0 ändert REGIE.md nur um Zeile 33–39 und ab Zeile 137),
  `README.md` (neue Befehle; veraltete Stellen README.md:11-12, :28-29 zu Wecken und Recycling, :76 Testzahl);
  `tests/test_publikum_cli.py`, `tests/test_deploy_publikum.py` (neu).
- **Umsetzung:** `pipeline publikum bewerten` als verschachtelter Unterbefehl (Muster `lager`, cli.py:517-529):
  der Parser-Block **ans Ende von `baue_parser`** (hinter „bot“), `_cmd_publikum` direkt **vor** `baue_parser`;
  `sperren=False`, nicht in `WECKEN`, letzte stdout-Zeile JSON über `_json`, Exit 1 bei `fehler > 0` (JSON-Zeile
  trotzdem), danach `lernbot_publikum.meldung_nach_bewerten`. Timer: `OnCalendar=*-*-* 10:00`,
  `Persistent=true`, `RandomizedDelaySec=10min`; Dienst wie `clip-puffer-pruefen.service` (oneshot,
  `User=pipeline`, `ProtectSystem=strict`, `ReadWritePaths=/var/lib/clip-pipeline`, Kopfkommentar mit dem Aufruf
  von Hand). Ruhezeit über `bot.aktionen.ruhezeit` (keine Kopie der Uhrzeit-Logik).
  **Drop-in** (A17): `ProtectHome=read-only`, `ReadWritePaths=-/home/pipeline` (ganzes Home desselben Benutzers:
  claude schreibt `~/.claude.json` per Temp-Datei und Umbenennen daneben; „-“: fehlt der Pfad, startet der Bot
  trotzdem – ohne „-“ startet er gar nicht), `Environment=DISABLE_AUTOUPDATER=1`. PATH: systemd kennt
  `~/.local/bin` nicht → in PUBLIKUM.md `[decide].programm` mit vollem Pfad (Beispiel in lokal.beispiel.toml)
  oder `Environment=PATH=…`.
  **PUBLIKUM.md** nennt: Probe `sudo systemd-run --uid=pipeline -p ProtectHome=read-only
  -p ReadWritePaths=/home/pipeline --pty /home/pipeline/.local/bin/claude -p --no-session-persistence "sag ok"`
  (🏠), die Prüfung `claude --help | grep no-session-persistence`, die Abnahme-Erwartung (Score 0 „Basis zu
  klein“, Einfrieren bei `alter_tage = 3`), Host-Schritte in Reihenfolge, „Was tun, wenn …“.
- **Tests zuerst:** CLI-JSON-Zeile + Exit 0, idempotent (zweimal), Exit 1 mit JSON bei `fehler > 0`;
  `socket`/Wecken gepatcht → nie aufgerufen; Unit-Dateien mit `lies_unit` aus tests/test_deploy_puffer.py
  (ExecStart, Type, OnCalendar, Persistent, ReadWritePaths, Härtung wie Bestand); Drop-in setzt
  `ProtectHome=read-only` und `ReadWritePaths` **mit „-“-Präfix**; `faellige_lern_meldungen` in/außerhalb der
  Ruhezeit (feste Zeit), andere Lern-Meldungen weiter sofort; `publikum_text` mit/ohne Posts und Score und mit
  Claude-Wochenzahl; `/hilfe` enthält HILFE_ZUSATZ; `meldung_nach_bewerten` je Tag einmal; `test_lernbot` weiter grün.
- **Fertig, wenn:** Test-Module grün; PUBLIKUM.md erklärt Screenshot-Fluss, Hand-Eingabe, /link, /publikum,
  Konfig-Schlüssel und die Host-Schritte in Reihenfolge (🏠); REGIE.md verweist darauf.

### (f) Stolperdraht gegen Secrets · Repo ist seit 25.09. öffentlich
- **Stand:** `tests/test_keine_secrets.py` und `.gitignore` (`.claude/`) sind im Vertrag – byte-gleich aus
  0f91d98 per `git show` (kein cherry-pick, also kein Commit; gleiche Blobs → beim Merge mit R2.0 kein Konflikt,
  keine zweite Fassung). `tests.test_keine_secrets` → `Ran 2 tests` / `OK`. Eigener Scan über getrackte **und**
  neue Dateien: 144 Dateien, 0 Funde. Historie selbst geprüft (`git log --all -p`, 59 Commits, hinzugefügte Zeilen
  und Commit-Texte mit denselben Mustern): 0 Treffer; nie eine `.env`, `lokal.toml`, `uebertragung.psd1`,
  `tiktok.json`, `*.pem`/`*.key`/`*.p12` oder ein privater SSH-Schlüssel hinzugefügt. (PR-/Issue-Texte auf GitHub
  hat laut Commit 0f91d98 die R2.0-Sitzung geprüft – hier nicht wiederholt.)
- **Dateien:** nur noch `tests/test_secrets_dateien.py` (neu). `.gitignore` nicht weiter ändern (sonst kein
  gleicher Blob mehr).
- **Tests zuerst:** `git ls-files` enthält keine `.env`, `*.env` (außer `.env.example`), `config/lokal.toml`,
  `windows/uebertragung.psd1`, `tiktok.json`, `*.pem`, `*.key`, `*.p12`, `*.pfx` und keine privaten SSH-Schlüssel
  (Dateiname `id_(rsa|ecdsa|ed25519|dsa)` ohne `.pub` – nicht `id_*`, das träfe auch `id_zuordnung.py`); in
  `.env.example` sind alle **aktiven** Zeilen `NAME=wert` leer (Kommentare wie `# CLIP_KONFIG=…` überspringen);
  `.gitignore` enthält `.env`, `config/lokal.toml`, `windows/uebertragung.psd1`, `.claude/`. Negativfall mit
  künstlich zusammengesetztem Wert (nie ein echter).
- **Nicht ohne Florians OK:** Epic-ID (config/pipeline.toml:196), MAC und Heimnetz-IPs ersetzen (Rückfrage 1);
  Git-Historie umschreiben (irreversibel).
- **Fertig, wenn:** beide Test-Module grün auf diesem Branch.

### (g) Ende-zu-Ende Stufe 1 · Spec §13 letzter Punkt, Startauftrag §5 · nach dem Zusammenführen von (a)–(e)
- **Dateien:** `tests/test_ende_zu_ende_publikum.py` (neu, A33).
- **Test:** DB im alten Stand (schema+regie+lager ohne publikum.sql, mit Beispielzeilen) → `db.verbinde`; Clip-Bot:
  Häkchen TikTok (`aktionen.plattform_erledigt`) → Post; Lern-Bot: `/link e41 <tiktok-url>` → Post; je Post zwei
  Messungen mit festen Zeiten (Tag 3 von Hand über `lernbot_zahlen`, Tag 7 per Screenshot mit gefälschtem
  `claude`); `pipeline publikum bewerten` über `cli.main` → score/score_teile gesetzt (Score 0, „Basis zu klein“),
  zweiter Lauf ändert nichts; `publikum_text` zeigt den Score; nie wecken.
- **Fertig, wenn:** Modul grün. Die übrigen Teile der Spec-Kette (Paar → Gewichte → Erwartung) kommen in Stufe 2.

## Dateibesitz (parallel disjunkt)

| Paket | besitzt |
|---|---|
| a | publikum.py · tests/test_publikum.py |
| b | claude_aufruf.py · screenshot.py · lernbot_zahlen.py · templates/screenshot-prompt.txt · schemas/publikum.schema.json · tests/test_screenshot.py · tests/test_lernbot_zahlen.py |
| c | lernbot_paket.py · entwurf.py (nur Rümpfe am Ende) · caption.py (nur Rumpf am Ende) · tests/test_upload_paket.py · tests/test_lernbot_paket.py |
| d | bot/aktionen.py · bot/app.py · tests/test_bot_app.py |
| e | cli.py · lernbot_publikum.py · lernbot.py · deploy/systemd/clip-publikum.* · deploy/systemd/clip-lernbot.service.d/ · docs/PUBLIKUM.md · docs/REGIE.md · README.md · tests/test_publikum_cli.py · tests/test_deploy_publikum.py |
| f | tests/test_secrets_dateien.py |
| g | tests/test_ende_zu_ende_publikum.py |
| Dirigent | docs/ENTSCHEIDUNGEN.md (Annahmen, Entscheidungen, Export-Vertrag) · CLAUDE.md · docs/SPRINT-LOG-LERNSCHLEIFE.md · Vertrag: config/*, db.py, publikum.sql, pyproject, shorts.py, entwurf.rendere, tests/test_keine_secrets.py, .gitignore |

Braucht ein Paket eine Änderung an einer fremden Datei (z. B. einen neuen Konfig-Schlüssel), meldet es das dem
Dirigenten statt sie selbst zu machen.

## Export-Vertrag mit Regisseur 2.0 (kommt beim Commit nach docs/ENTSCHEIDUNGEN.md, an den R2.0-Workflow geben)

CLAUDE.md „Export“ (25.09.) plant für R2.0 Stufe 3 einen 📦 im Lern-Bot und `pipeline export` nach
`/srv/puffer/export/<name>/`. Damit daraus nicht zwei Knöpfe, zwei Callback-Präfixe und zwei Dateien werden:
- Genau **ein** 📦 = `pk:<eid>:` aus `lernbot_paket` (Einhängen im x-Zweig von `lernbot.bei_klick` über
  `knoepfe_nach_fertig`).
- Ordner `<wurzel>/<[publikum].upload_ordner = "export">/<name>/`, Datei `entwurf.UPLOAD_DATEI` =
  `<name>_upload.mp4`, Pfad in `entwuerfe.upload_pfad` (das „Fertig-Video“).
- R2.0 Stufe 3 **erweitert** `lernbot_paket.baue_paket` und `entwurf.upload_fassung` (nummerierte Einzelclips
  daneben, tägliche Sicherung von `export/` ins Lager) und baut keinen zweiten Weg.

## Zusammenführen mit Regisseur 2.0 (Regeln, damit der Merge ohne Konflikt geht)

- `config/lokal.beispiel.toml`: unser Block steht hinter `[speicher]` (R2.0 hängt am Dateiende an) – geprüft.
- `db.py`: Originalzeile `MIGRATIONEN = […]` unverändert, unsere Spalten per `MIGRATIONEN += […]`. Die Zeile
  `for datei in (…, "publikum.sql")` und die package-data-Zeile bleiben geändert (R2.0 bringt keine SQL-Datei;
  käme eine, ist es ein Ein-Zeilen-Konflikt: beide Dateien behalten).
- `entwurf.py`: `rendere` nur an den 6 Vertrags-Stellen (geprüft: 0 Konflikte, kompiliert), Rest am Dateiende.
- `lernbot.py`: `HILFE` nicht ändern (Zusatz über `lernbot_publikum.HILFE_ZUSATZ` in `cmd_hilfe`).
- `cli.py`: Parser-Block ans Ende von `baue_parser`, `_cmd_publikum` direkt davor (R2.0 ändert `render-entwurf`
  und `bewerte`).
- `docs/REGIE.md`: nur ein Punkt im Lern-Bot-Abschnitt (R2.0-Änderungen liegen bei Zeile 33–39 und ab 137).
- `docs/ENTSCHEIDUNGEN.md`, `CLAUDE.md`: beide Seiten hängen am Ende an → beim Merge beide Blöcke behalten, R2.0
  zuerst, L1–L6 (A2) dann fortlaufend nummerieren.
- Merge-Probe mit `git merge-file` (Basis b394f4a gegen den R2.0-Stand im Haupt-Checkout, dort nur lesen) nach
  jedem Zusammenführen wiederholen – nicht erst, wenn R2.0 Commits hat.

## Bewusst später (Spec-Sätze, die Stufe 1 berühren, aber nicht hierher gehören)

- Paar-Bildung aus Publikums-Scores (§13 test_publikum „Abstand, Art, Hook-Moment“, `[publikum].paar_abstand`,
  `max_paare`) → Stufe 2 (`lernen.py`).
- Neue Schlüssel in `clips.merkmale` (§5 letzter Satz, §8.1) → Stufe 2.
- `pipeline publikum holen` im Timer (§12) → Stufe 4 (Display API); der Timer ruft bis dahin nur `bewerten`.
- Claude-Wochenzahl in `/lernstand` (§12) → Stufe 3 mit der Rezept-Tabelle; bis dahin als Zeile in `/publikum` (A34).
- `rezept_stand`, `hypothesen`, `erwartungen`: Tabellen sind da, gefüllt ab Stufe 2–5.

## Annahmen (Startauftrag Regel 6 – kommen nach `docs/ENTSCHEIDUNGEN.md`, „Annahmen im Sprint Lernschleife“)

- **A1 Score-Zeitpunkt:** `bewerten` setzt den Score erst, wenn der Post ≥ `alter_tage` (7) alt ist, mit der
  Messung am nächsten an 7 Tagen (nur Messungen ≥ 3 Tage). Sonst friert ein täglicher Lauf den Score an Tag 3 ein
  und die Regel „am nächsten an 7“ wäre wirkungslos (Spec §5 vs. §6.1/§14). Abnahme mit `alter_tage = 3`: Score 0
  („Basis zu klein“), diese Posts bleiben mit ihrer Tag-3-Messung bewertet.
- **A2 Entscheidungsnummern:** E21 ist vergeben (ENTSCHEIDUNGEN.md:298), R2.0 vergibt eigene. Bis zum Merge
  Arbeitstitel **L1–L6** (= Spec E21–E26), fortlaufende Nummern beim Merge.
- **A3 /link im Lern-Bot** nimmt `41` und `e41` (Spec §10.4 vs. §13/§14).
- **A4 Ein 📦-Knopf:** siehe „Export-Vertrag“. Die Sicherung von `export/` ins Lager kommt mit R2.0 Stufe 3
  (`[lager].ordner` wird hier nicht geändert).
- **A5 Nur 👍-Entwürfe** bekommen Paket, Häkchen und Post („kein Short ohne deine Freigabe“).
- **A6 Übergangs-Rezept für Entwürfe (bis Stufe 3):** hook `aufbau`, laenge nach Dauer, tempo `beat1` bei
  beats_pro_schnitt 1 sonst `beat2` (auch 4), machart `regie`, experiment false. Clip-Posts wie Spec (`tempo none`).
- **A7 Längen-Stufen:** Grenzen in der Mitte der Spec-Lücken: ≤ 22,5 s kurz, ≤ 36 s mittel, sonst lang.
- **A8 Dauer eines Clip-Posts** aus der DB über `shorts.gesamtdauer(Clip-Länge)` (mit Endcard: + endcard_s − 0,5;
  ohne Endcard: Clip-Länge) – dieselbe Funktion wie beim Rendern, ohne Dateizugriff im Bot.
- **A9 Plattformen:** Posts nur für `[publikum].plattformen = ["tiktok"]` (Spec §3 „kein YouTube in dieser Stufe“);
  YouTube per Konfig zuschaltbar; clip-battle.de nie.
- **A10 Fehlende Zähler** zählen im Engagement als 0 (Vermerk „Engagement unvollständig“); Messungen ohne views
  zählen nicht für den Score. (Folge für Hand-Posts: Rückfrage 4.)
- **A11 Basis** = die jüngsten 20 bewerteten Posts derselben Plattform, die **vor** dem Post gepostet wurden;
  `bewerte_alle` arbeitet älteste zuerst. `MINDEST_BASIS` gilt je Komponente: unter 5 Posts → Score 0 („Basis zu
  klein“, Spec); genug Posts, aber unter 5 r-Werten → wie „ohne Wiedergabe“ (0,6/0,4, „Basis ohne Wiedergabe“).
  `score_teile` speichert zusätzlich das Alter der gewählten Messung.
- **A12 Knöpfe ohne #Nummer:** die 5 jüngsten Posts ohne Messung in den letzten 24 h.
- **A13 Zusätzliche Callback-Daten:** `pt:<eid>:t|y` (Häkchen Entwurf), `pm:<post_id>:ok|hand` (Rückfrage).
- **A14 Upload-Fassung:** über `rendere(volle_aufloesung=True, crf=20, kbit_max=…)`; libx264 `crf 20` + maxrate;
  VA-API ohne crf mit `-b:v`; Deckel aus `[vorschau].max_mb`; bis 3 Versuche mit 0,75 · benutzter Rate (`ZuGross.kbit`).
- **A15 Ruhezeit im Lern-Bot:** eigener Filter `LEISE_LERN_MELDUNGEN = ("publikum:", "woche:")`;
  Publikums-Meldungen gehen über `lern_meldungen`/Lern-Bot.
- **A16 Uhrzeit** nur im Timer (kein `[publikum].uhrzeit` – eine Wahrheit, wie clip-lager).
- **A17 Claude im Lern-Bot-Dienst:** ~~Drop-in `ProtectHome=read-only` + `ReadWritePaths=-/home/pipeline`~~ – nach dem
  Panel (Home beschreibbar = Sperre des n8n-Schlüssels angreifbar): Drop-in `ProtectHome=read-only` +
  `CLAUDE_CONFIG_DIR=/var/lib/clip-pipeline/claude` + `DISABLE_AUTOUPDATER=1`, eigene Anmeldung des Dienstes; voller
  Pfad in `[decide].programm` nur, wenn claude unter /home liegt (🏠); Rückfall `screenshot_claude = false`.
  Stand und Begründung: docs/ENTSCHEIDUNGEN.md, „Annahmen im Sprint Lernschleife“. (Rückfrage 2.)
- **A18 claude_aufruf** ist die gemeinsame Hilfe für neue Aufrufe; `decide`/`stimmung` bleiben vorerst (n8n-Vertrag),
  die Wochenzahl zählt deshalb nur neue Aufrufe.
- **A19 /publikum** nur im Lern-Bot.
- **A20 Neue Lern-Bot-Tests** in eigenen Dateien (Spec §13 sagt „test_lernbot.py erweitert“) – wegen paralleler
  Pakete und R2.0-Änderungen an test_lernbot-nahen Stellen.
- **A21 Wartende Bilder** in `tempfile.mkdtemp` (im Dienst PrivateTmp), nie im Puffer; gelöscht nach Auswertung
  bzw. nach 10 min („Nie löschen“ gilt für Rohdaten/Clips).
- **A22 Stolperdraht** vom R2.0-Branch byte-gleich per `git show 0f91d98:<pfad>` (statt cherry-pick: kein Commit).
- **A23 Keine neuen Secrets** in Stufe 1; `.env.example` unverändert.
- **A24 Migration** über `db.MIGRATIONEN` in `db.verbinde` (Spec nennt `db.migriere`, das es nicht gibt).
- **A25 Schema-Ort** `src/clip_pipeline/schemas/publikum.schema.json`; Zähler als `number`, `screenshot.normalisiere`
  macht Ganzzahlen daraus; das Schema prüft nur Typ und „nicht negativ“, Zusatzfelder sind erlaubt (Spec §15:
  tolerant); die 100-%-Regel steht nur in `pruefe_plausibel` (→ Rückfrage statt Ablehnung).
- **A26 Nur Shorts** bekommen Paket, Häkchen und Post (ein Zusammenschnitt hätte bei 48 MB ≈ 1 Mbit/s und liefert
  kein Publikumssignal, Spec §9.1).
- **A27 Entwurfs-Checkliste** nur für Post-Plattformen, ohne clip-battle.de (Rückfrage 5).
- **A28 Hand-Eingabe** wird wie ein Screenshot gegen die letzte Messung geprüft (Rückfrage mit denselben
  pm:-Knöpfen); `#17 1240 61 6.8 34` als Text geht auch ohne Bild.
- **A29 Ein offener Vorgang** im Lern-Bot (ein Nutzer): ein neues Bild bzw. ein neuer „#17 …“-Text ersetzt den
  offenen Vorgang, ein altes Bild wird sofort gelöscht, und der Bot sagt, was verworfen wurde (Bild, Rückfrage – „NICHT
  gespeichert“ – oder Hand-Eingabe; kein Hinweis bei einer Korrektur desselben Posts). Klicks, die nicht passen (auch
  Knöpfe aus einer älteren Nachricht) → „Schon erledigt.“.
- **A30 Bildformate:** Foto (JPEG) sowie JPG/PNG/WebP als Datei, abgelegt mit passender Endung; HEIC u. a. →
  „Bitte als Foto schicken“.
- **A31 Upload-Fassung nur im getrennten Betrieb** (E19) – sonst KonfigFehler, weil die Wurzel dann das Lager auf
  pve-big wäre.
- **A32 Kein Sitzungsverlauf:** `claude -p --no-session-persistence` (sonst läge jedes Bild unter
  ~/.claude/projects – ein Bildarchiv). Kennt die claude-Version auf dem Mini den Schalter nicht, bleibt nur die
  Hand-Eingabe, bis claude aktualisiert ist (🏠; hier mit claude 2.1.281 geprüft, dass es ihn gibt).
- **A33 Ende-zu-Ende** in eigener Datei `tests/test_ende_zu_ende_publikum.py` statt test_ende_zu_ende.py zu
  erweitern (gleiche Begründung wie A20).
- **A34 Claude-Wochenzahl** bis Stufe 3 als letzte Zeile von `/publikum`.

## Rückfragen an Florian (höchstens 5, nach Wichtigkeit – ohne darauf zu warten)

1. **Öffentliches Repo – persönliche Daten:** Secrets wurden keine gefunden (144 Dateien, 0 Funde; Historie
   sauber). Aber öffentlich stehen deine Epic-ID (config/pipeline.toml:196), MAC und Heimnetz-IPs
   (config/lokal.beispiel.toml, docs/PUFFER.md u. a., 28 Stellen). Durch Platzhalter ersetzen (Epic-ID dann in
   lokal.toml)? Die Historie bleibt davon unberührt; sie umzuschreiben ginge nur mit deinem ausdrücklichen OK.
2. **Lern-Bot-Dienst und claude:** ~~Damit claude im Dienst läuft, darf der Lern-Bot in `/home/pipeline` schreiben
   (A17). Ok – oder lieber ein eigenes `CLAUDE_CONFIG_DIR` unter /var/lib/clip-pipeline (dann einmal neu anmelden)?~~
   Nach dem Panel ist `CLAUDE_CONFIG_DIR` Standard (das Home bleibt schreibgeschützt); offen bleibt nur: eine zweite
   claude-Anmeldung für den Dienst – ok? (docs/ENTSCHEIDUNGEN.md, R2)
3. **MAD-Minimum 0,05** gilt für alle Komponenten gleich und dämpft das Engagement (typischer MAD 0,01–0,02) um
   Faktor 2,5–5 – die 0,3 Gewicht wirken kaum. Ein Minimum je Komponente in `[publikum]` (z. B. e 0,005)?
4. **Hand-Eingabe** kennt nur Views/Likes/Wiedergabe/voll%; Kommentare, Shares, Saves zählen dann 0 und Hand-Posts
   wirken „schwach“. Optional `kommentare shares saves` hinten anhängen (alte Form geht weiter) – oder e ohne
   diese Zähler weglassen?
5. **clip-battle.de für Entwürfe:** Soll die Checkliste eines Entwurfs auch „clip-battle.de eingereicht“ haben
   (ohne Post, nur als Merker)? Bis dahin nur TikTok (A27).

## Nach dem Zusammenführen (Dirigent)

(g) Ende-zu-Ende, Gesamtlauf modulweise (alle `tests.test_*`), Panel nach Startauftrag §3.5 (Korrektheit/
Datenverlust, Tests, Einfachheit/Kommentare, Spec-Treue, Betrieb), Gegenprüfer je Befund, Annahmen und
Export-Vertrag in ENTSCHEIDUNGEN.md, Sprint-Log, Commit „Stufe 1: …“, Push auf `lernschleife-publikum`.
Merge-Probe gegen den R2.0-Stand (`git merge-file` bzw. `git merge --no-commit --no-ff` in einem
Wegwerf-Worktree, sobald R2.0 Commits hat).

## Testlauf nach dem Überarbeiten

Alle mit `cd /home/user/lernschleife-publikum && PYTHONPATH=src /home/user/baddieday-voting/.venv/bin/python -m
unittest …` auf dem überarbeiteten Vertrag (rendere, shorts.gesamtdauer, MIGRATIONEN +=, lernbot x-Zweig):
- `tests.test_bot_app tests.test_speicher_wecken tests.test_veroeffentlichung tests.test_caption_aufraeumen
  tests.test_keine_secrets tests.test_deploy_puffer tests.test_elo_lernen_bot` → `Ran 99 tests in 54.443s` / `OK`
- `tests.test_entwurf tests.test_ende_zu_ende` → `Ran 21 tests in 458.261s` / `OK`
- `tests.test_lernbot` → `Ran 10 tests in 432.755s` / `OK`
- `shorts.filtergraph` alt gegen neu: zeichengleich in 12 Fällen (Endcard an/aus × Layout unschaerfe/zuschnitt ×
  0/1/2 Tonspuren).
- Alle neuen Module importieren fehlerfrei; `lernbot_paket.PLATTFORM_KUERZEL == {"y": "youtube", "t": "tiktok"}`;
  `knoepfe_nach_fertig`: 👍-Short → `pk:41:`, 👍-Zusammenschnitt → None, 👎 → None.

## Prüfung des Plans (Befunde der Prüfer, je selbst nachgeprüft)

Bestätigt und behoben (Ort der Änderung):
- **rendere nicht baubar nach „nur Dateiende“ (blockierend):** bestätigt. `rendere` im Vertrag um
  `volle_aufloesung`, `crf`, `kbit_max` erweitert (faktor-Zeile unberührt), Rückfall VA-API→CPU reicht alles
  weiter, `ZuGross(kbit)` unterscheidet „zu groß“ von „Datei fehlt“; Wiederholung senkt kbit_max, max_bytes
  bleibt. Merge-Probe gegen R2.0: 0 Konflikte, kompiliert. Paket (c) fasst `rendere` nicht mehr an.
- **Basis je Komponente / median([]) (wichtig):** bestätigt (bei 0 r-Werten bräche `statistics.median` ab).
  Regel im Docstring von `score_fuer`/`robust_z`, Tests in (a), A11.
- **Kein Ende-zu-Ende-Test (wichtig):** bestätigt (§13, Startauftrag §5). Paket (g), A33.
- **📦 auch für Zusammenschnitte (wichtig, zwei Prüfer):** bestätigt (`regie.FORMATE` 180–300 s, 1920×1080).
  `knoepfe_nach_fertig(eid, bewertung, fmt)` echt im Vertrag, `lernbot.py` übergibt `zeile["format"]`,
  `paket_erlaubt`, `upload_fassung` lehnt ab, A26.
- **Hand-Eingabe ohne Plausibilität / ohne Bild nicht möglich:** bestätigt. `lernbot_zahlen` (Docstring,
  `lies_text_eingabe`), Tests in (b), A28.
- **Schema zu streng (maximum, additionalProperties), NaN/Infinity (zwei Prüfer):** bestätigt per
  `schema.pruefe` (`{'extra':1}` und `{'voll_prozent':120}` → Fehler, `{'views': nan}` → keine Fehler). Schema
  gelockert, `normalisiere` prüft `math.isfinite`, Tests in (b), A25.
- **7,3 vs. 8,5 Mbit/s:** bestätigt (48 MB·8/45 s = 8,53 gesamt; ·0,88 − 160 kbit = 7,35 Bild). Erklärung im
  Docstring von `upload_fassung`; (c) nutzt die Budget-Rechnung aus `rendere` über `kbit_max`.
- **dauer_s-Formel doppelt, endcard = false falsch (zwei Prüfer):** bestätigt (shorts.py:84–93). Neu
  `shorts.endcard_s`/`gesamtdauer` im Vertrag, `filtergraph` nutzt sie (Graph zeichengleich), A8, Test in (a).
- **Plattform-Kürzel/Link-Erkennung doppelt, gleichnamige `plattformen`:** bestätigt (aktionen.py:32, 251–263,
  210). `lernbot_paket.PLATTFORM_KUERZEL` aus `aktionen` abgeleitet, Docstrings verweisen auf
  `plattform_aus_url`, `publikum.plattformen` → `post_plattformen`.
- **Leitplanke 4 nicht im Vertrag:** bestätigt. `from .zeit import iso, jetzt` in den drei Lern-Bot-Modulen,
  `aufraeumen(app, zeit=None)`.
- **Zweites Foto / Bedeutung von `pm:<nr>` (zwei Prüfer):** bestätigt. Ein offener Vorgang, `pm:<post_id>`,
  Docstrings in `lernbot_zahlen`, A29; `_json_aus_text` aus verarbeitung (Docstring claude_aufruf).
- **Bild immer als .jpg, Sitzungsverlauf als Bildarchiv (zwei Prüfer):** bestätigt; `--no-session-persistence`
  gibt es (claude 2.1.281 hier, `claude --help`). `BILD_ENDUNGEN`, `endung_fuer`, Platzhalter `{bild}` im Prompt,
  Schalter im Aufruf, A30, A32. Ob das Read-Tool bei falscher Endung wirklich scheitert, ist hier nicht geprüft –
  die Änderung ist billig und HEIC geht ohnehin nicht.
- **Spec-Sätze ohne „verschoben“-Vermerk:** bestätigt. Abschnitt „Bewusst später“; Wochenzahl in /publikum (A34).
- **Abnahme mit alter_tage = 3 friert ein:** bestätigt. „Fertig, wenn“, A1, lokal.beispiel.toml, PUBLIKUM.md in (e).
- **Checkliste ohne clip-battle.de:** Abweichung bestätigt, Entscheidung offen → A27 + Rückfrage 5.
- **Fehlerverhalten nicht beschrieben (bewerte_alle, cmd_*, `roh`):** bestätigt. Docstrings ergänzt, `fehler` im
  Ergebnis, Exit 1 in (e).
- **REGIE.md fehlt in (e):** bestätigt (Startauftrag §7). In (e) aufgenommen, Stelle gegen den R2.0-Diff gewählt.
- **Vertrag nicht committet, cherry-pick erzeugt Commit, `.claude/` (wichtig):** bestätigt. Stolperdraht und
  `.gitignore` per `git show` ohne Commit übernommen (Blobs gleich), Reihenfolge mit Commit-Entscheidung des
  Dirigenten, Leitplanke 9 (`git add <datei>`).
- **Kein `post_zu`, widersprüchliche Link-Regeln (wichtig):** bestätigt. `publikum.post_zu` im Vertrag,
  `post_anlegen` ohne `url` – der Link nur über `link_nachtragen`.
- **`entwurf_caption` ohne DB (wichtig):** bestätigt (Segmente tragen nur nr, moment, clip_id, …, punkte, grund;
  keine Gruppe, kein Victory Royale). Signatur `entwurf_caption(con, liste, konfig)`.
- **Merge-Konflikte (lokal.beispiel.toml, HILFE, cli, MIGRATIONEN) (wichtig):** bestätigt für lokal.beispiel.toml
  (Probe vorher: 1 Konflikt, jetzt 0). Block verschoben, `MIGRATIONEN +=`, `HILFE_ZUSATZ`, cli-Reihenfolge,
  Abschnitt „Zusammenführen mit Regisseur 2.0“.
- **Export-Vertrag fehlt (wichtig):** bestätigt (CLAUDE.md Z. 136–138). Abschnitt „Export-Vertrag“,
  `entwurf.UPLOAD_DATEI`, Docstring `lernbot_paket`.
- **Drop-in ohne „-“, read-only Home, PATH (wichtig):** bestätigt für „-“ (systemd bricht beim fehlenden Pfad
  den Start ab; die bestehende Unit nutzt deshalb `-/srv/puffer`) und PATH (`useradd -m` → /home/pipeline,
  systemd ohne ~/.local/bin). Das Schreiben per Temp-Datei neben `.claude.json` ist nicht nachgeprüft (🏠) – das
  ganze Home freizugeben deckt es ab. A17, (e), Rückfrage 2.
- **Token in `file_path` (wichtig):** bestätigt im Quelltext von python-telegram-bot 22.8. Leitplanke 5,
  Modul-Docstring `lernbot_zahlen`, Test in (b).
- **Upload-Fassung ohne getrennten Betrieb auf dem Lager:** bestätigt (`konfig.getrennt`, Tests laufen sonst
  ohne `[lager].wurzel`). A31, Docstring, Test in (c).
- **`id_*` zu breit, Kommentare in .env.example:** bestätigt. Muster in (f) präzisiert.

Verworfen bzw. nicht als Mangel gewertet (mit Begründung):
- **Übersicht Spec-Abdeckung:** nur Einordnung, kein Befund.
- **„weniger als 5 r-Werte → z_r = 0“ (Teil des Befunds zu A11):** verworfen zugunsten des anderen Prüfer-
  Vorschlags („wie ohne Wiedergabe“, 0,6/0,4). z_r = 0 mit Gewicht 0,5 zöge jeden solchen Score um die Hälfte zur
  Null und machte ihn mit anderen Scores unvergleichbar; die Spec hat für fehlende Wiedergabe schon die
  Umgewichtung (§6.4). Übernommen sind aus diesem Befund „nur Vorgänger in der Basis“ und das Messungs-Alter.
- **`tests.test_db` fehlt:** kein Plan-Mangel – der Plan nannte schon `tests.test_veroeffentlichung`; nur der
  Auftragstext hatte den falschen Modulnamen. Leitplanke 8 sagt es jetzt ausdrücklich.
- **`for datei in`-Zeile und package-data-Zeile (Teil des Merge-Befunds):** nicht umgebaut. R2.0 bringt keine
  SQL-Datei (Haupt-Checkout: keine neue .sql, pyproject unverändert); die Merge-Probe zeigt 0 Konflikte. Käme
  doch eine, ist es ein Ein-Zeilen-Konflikt mit offensichtlicher Auflösung. Ein Umbau hätte eine zweite
  executescript-Zeile gekostet.
- **Hand-Eingabe verzerrt e / MAD-Minimum je Komponente:** als Wirkung bestätigt, aber Spec-Fragen, keine
  Plan-Fehler – nicht eigenmächtig geändert, Rückfragen 3 und 4, Zahlenbeispiel-Test in (a).
