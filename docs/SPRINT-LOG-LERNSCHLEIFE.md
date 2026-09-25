# Sprint-Log „Lernschleife Publikum“ (ab 25.09.2026)

Legende: ✅ fertig und hier getestet · 🧪 gebaut, nur mit künstlichem Material getestet · 🏠 braucht das echte System ·
⛔ Blocker. Spec: `docs/superpowers/specs/2026-09-25-lernschleife-publikum-design.md`, Plan Stufe 1:
`docs/superpowers/plans/2026-09-25-lernschleife-stufe-1.md`, Bedienung und Installation: `docs/PUBLIKUM.md`.
Annahmen stehen **nicht** hier, sondern einmal in `docs/ENTSCHEIDUNGEN.md`, „Annahmen im Sprint Lernschleife“.

## Stufe 1 · Fundament

### Stand
- Umgebung wie E1: Cloud-Container ohne Heimnetz, kein Telegram-Token, kein `claude -p` für `pipeline`. Alles ist hier
  mit künstlichem Material geprüft (Fake-Telegram, gefälschtes `claude`, Farbtest-Videos) – 🧪.
- Gebaut (Commits „Stufe 1: Vertrag“, „Paket a–g“): `publikum.sql` + Migration, `publikum.py` (Posts, Messungen,
  Plausibilität, Hand-Eingabe, Score), Screenshot- und Hand-Eingabe im Lern-Bot (`lernbot_zahlen`, `screenshot`,
  `claude_aufruf`), Upload-Fassung, 📦 Upload-Paket, Häkchen und `/link` im Lern-Bot (`lernbot_paket`,
  `entwurf.upload_fassung`), Posts aus Häkchen und `/link` im Clip-Bot, `pipeline publikum bewerten` + Timer
  `clip-publikum`, `/publikum`, Ruhezeit, Stolperdraht gegen geheime Dateien, Ende-zu-Ende-Test.
- Prüfer-Panel (5 Linsen) und Gegenprüfer je Befund: 32 Befunde, **alle bestätigt** (einige mit geänderter Schwere
  oder kleinerem Vorschlag), keiner ganz verworfen. Nachbesserung im Commit „Stufe 1: Befunde des Panels behoben“.
- Testläufe der Nachbesserung (nur betroffene Module, `PYTHONPATH=src`):
  - `tests.test_publikum tests.test_publikum_cli tests.test_lernbot_zahlen tests.test_screenshot
    tests.test_lernbot_paket tests.test_ende_zu_ende_publikum tests.test_deploy_publikum tests.test_bot_app
    tests.test_secrets_dateien tests.test_keine_secrets tests.test_deploy_puffer tests.test_quellen`
    → `Ran 372 tests` / `OK` (vorher, ohne die neuen Tests: 231 Tests in den ersten sieben Modulen, `OK`).
  - `tests.test_upload_paket` (echte Renders mit Farbtest-Material) → `Ran 25 tests in 102.519s` / `OK`.
  - Merge-Probe gegen Regisseur 2.0 (`git merge-file`, Basis b394f4a, R2.0-Stand d9095fd nur gelesen) für
    `entwurf.py`, `cli.py`, `lernbot.py`, `README.md`, `config/pipeline.toml`, `config/lokal.beispiel.toml`:
    0 Konflikte, `entwurf.py`/`cli.py` kompilieren. `CLAUDE.md` und `docs/ENTSCHEIDUNGEN.md` hängen wie geplant am
    Ende an – beim Merge beide Blöcke behalten, R2.0 zuerst.
- Gesamtläufe (`python -m unittest discover -s tests -t .`):
  - **Vor dem Sprint:** 495 Tests auf `main` nach Regisseur 2.0 (grün, 3 übersprungen – `docs/SPRINT-LOG.md`).
  - **Nach ddec0d8** (Stufe 1 mit Nachbesserung, vor dem Merge mit main): `Ran 733 tests` / `OK (skipped=2)`.
  - Drop-in mit nachgebautem `e19-puffer.conf` daneben: `systemd-analyze security --offline=true` meldet „read-only
    access to home directories“ (🧪).

### Befunde des Panels – bestätigt und behoben
Wichtig:
1. **Offene Rückfrage/Hand-Eingabe ging beim Ersetzen still verloren** (Korrektheit). Neu `_alten_vorgang_ersetzen`:
   „🗑 Rückfrage zu #17 verworfen – diese Zahlen sind NICHT gespeichert …“ bzw. „🗑 Hand-Eingabe für #17 abgebrochen …“;
   kein Hinweis bei einer Korrektur desselben Posts. Tests: `tests.test_lernbot_zahlen.Ersetzen` (4 Fälle).
2. **Alter pl:-Knopf ordnete das NEUE Bild dem Post zu** (Korrektheit). Der Vorgang merkt sich die message_id seiner
   Knopf-Nachricht; pl: und – Nebenbefund – pm: gelten nur aus genau dieser Nachricht. Tests:
   `test_knopf_aus_der_nachricht_eines_verworfenen_bildes`, `test_alter_ok_knopf_gilt_nicht_fuer_eine_neue_rueckfrage`.
3. **Bitrate der Upload-Fassung nirgends mit ihrem Wert geprüft** (Tests). VA-API-Rückfall: `-b:v` = CPU-`-maxrate` und
   > `ENTWURF_KBIT`; neuer billiger 45-s-Fall erwartet genau `7349k`; im echten Render `-maxrate` > 4000.
4. **„Auswertung im Thread“ ohne Test** (Tests). Screenshot-Test und Paket-Test prüfen, dass Claude bzw. das Rendern
   nicht im Haupt-Faden laufen.
5. **Annahmen und Entscheidungen nur im Plan** (Spec-Treue). Abschnitte in `docs/ENTSCHEIDUNGEN.md` (L1–L6,
   Export-Vertrag, A1–A40, Bauer-Annahmen, R1–R5), Einzeiler L1–L6 in `CLAUDE.md`, dieses Log.
6. **`alter_tage` für die Abnahme in der falschen lokal.toml** (Betrieb). PUBLIKUM.md: beide Dateien, Timer liest
   `/opt/clip-pipeline/config/lokal.toml`, voller Pfad beim Aufruf von Hand, Entfernen aus beiden mit `grep`-Kontrolle.
7. **claude-Pfad in P2 nur angenommen** (Betrieb). `CLAUDE="$(sudo -iu pipeline command -v claude)"`, voller Pfad in
   `[decide].programm` nur, wenn claude unter /home liegt, Warnung „gilt auch für decide aus n8n“, Prüfbefehl.
8. **Drop-in machte das ganze Home beschreibbar** (Betrieb). Jetzt `CLAUDE_CONFIG_DIR=/var/lib/clip-pipeline/claude`,
   Home nur lesbar, eigene Anmeldung des Dienstes (A17, R2). Test: kein `ReadWritePaths` im Drop-in.

Klein (Nummern laufen weiter; zwei Befunde zur Claude-Zählung sind in Punkt 9 zusammengefasst):
9. Claude-Aufrufe wurden auch ohne gestartetes claude gezählt → `ClaudeAntwort.gestartet`, Regel in `protokolliere`;
    Timeout zählt, OSError nicht (zwei Befunde, Korrektheit und Spec-Treue).
10. „ohne Wiedergabe“ fehlte im Zweig „Basis zu klein“ → ergänzt, Test.
11. Nur Views/Likes als sinkende Zähler getestet → `test_jeder_zaehler_darf_nicht_sinken` (fünf Felder ausgeschrieben).
12. Fehlende Schnittliste beim Häkchen und fehlendes Bild in `lies_zahlen` ungetestet → zwei Tests.
13. Einhängen von `aufraeumen` in `lernbot._schleife` ungetestet → `test_schleife_ruft_aufraeumen`.
14. Doppelte Konstanten → `publikum.VERMERK_BASIS_ZU_KLEIN`, `publikum.ART_NAMEN`, `aktionen.PLATTFORM_NAMEN` (YouTube
    heißt jetzt überall „YouTube Shorts“), `claude_aufruf.EREIGNIS_ART`, `aktionen.HINWEIS_MAX`.
15. Kommentar zu `MINDEST_BASIS` beschrieb die verworfene Regel → neu gefasst.
16. „Likes je View“ für e → „Reaktionen je View“ (Bot, PUBLIKUM.md; A36).
17. Falsche Begründung zu Regel 3 in `upload_fassung` → Kommentar ehrlich.
18. `rendere` ohne Docstring → Docstring (Parameter, Budget-Formel mit Zahlenbeispiel, `final`, Fehler).
19. Zwei Regeln für Standardwerte unerklärt → Satz in `publikum.einstellung`/`screenshot.einstellung`.
20. Private Helfer über Modulgrenzen → `publikum.einstellung`, `alter_in_tagen`, `anzahl_text`, `dezimal_text`.
21. „nach 7 Tagen“ fest in /hilfe, CLI-Hilfe, README → ohne feste Zahl bzw. mit Verweis auf `[publikum].alter_tage`.
22. Hand-Eingabe ohne Einheit, ✅ doppelt belegt → `publikum.HAND_FORM` mit „in Sekunden“, Fehlertext „bitte in
    Sekunden“, „34%“ erlaubt, 🏁 für „ganz angesehen“, Legende in /hilfe (A40, A37).
23. Clip-Bot-Transaktion (Häkchen nur mit Post) nicht als Abweichung markiert → A35, PUBLIKUM.md §1 und „Befehle“.
24. Kontrolle `systemctl cat … | tail -4` zeigte bei zwei Drop-ins das falsche → `systemctl show …` mit Erwartung und
    Erklärung (Gegenprüfer: klein).
25. „Exit 2 bei falsch geschriebenem Schlüssel“ stimmte nicht → PUBLIKUM.md und Kommentar in `cli._cmd_publikum`.
26. Probe in P2 lascher als der Dienst → dieselben Schutzregeln (Haupt-Unit + Drop-in), `WorkingDirectory=/tmp` und
    die Schalter des Bots; Test leitet die Probe aus den Unit-Dateien ab.
27. Kein „Was tun“ für „TikTok nicht abgehakt“ → Eintrag mit Journal und Notausgang `plattformen = []`.
28. P2 aufschieben unmöglich → P1 startet beide Bots neu, P2 nennt `screenshot_claude = false` als Weg ohne claude.
29. `screenshot_prompt`/`screenshot_timeout_s` ohne Kommentar → Kommentare in `config/pipeline.toml`.
30. Verweise „im Plan“ im Code → auf `docs/ENTSCHEIDUNGEN.md`; Rückfrage-Stellen neutral (R3, R4, R5); Docstrings von
    `protokolliere` und `claude_aufrufe_woche` nennen /publikum.
31. **Zahlformat an drei Stellen** (Kommentare; Gegenprüfer: klein statt wichtig). Eine Stelle in `publikum`:
    `anzahl_text`, `dezimal_text` (erst runden, dann „,0“ weg), `SYMBOLE`, `TAUSENDER`; Rückfrage, Bestätigung und
    /publikum nutzen sie.

### Verworfen – ganz oder in Teilen (mit Begründung, Muster E17)
Kein Befund wurde ganz verworfen. Verworfen wurden Teile der Vorschläge bzw. Folgerungen, jeweils vom Gegenprüfer
begründet:
- **pm:-Antwort „Nicht mehr offen – nichts gespeichert“ statt „Schon erledigt.“:** verworfen. Dieselbe Antwort gilt für
  den Doppelklick nach erfolgreichem ✅ – dort wäre „nichts gespeichert“ falsch; der Bot kann beide Fälle ohne weiteren
  Zustand nicht unterscheiden. Der Verlust steht jetzt beim Ersetzen im Chat (A29).
- **Vorgangsnummer in den Callback-Daten (`pl:<post_id>:<nr>`):** verworfen – weicht von Spec §7.1 ab und ist nach
  einem Neustart nicht sicher (Zähler finge bei 1 an). Stattdessen message_id (A38). Nur alte Knöpfe entfernen reicht
  nicht (kann still scheitern).
- **Gemeinsames `werte_text` mit Modus-Schalter:** verworfen – Bestätigung (alle Felder, „–“) und /publikum (nur
  bekannte Werte) sind absichtlich verschieden; zwei kurze Schleifen über dieselbe Zuordnung sind einfacher.
  `vorbewertung.zahl` nicht angefasst (Altbestand, Merge mit R2.0). Schwere „wichtig“ → „klein“ (rein kosmetisch).
- **Gemeinsame Zerlege-Hilfe für Callback-Daten:** verworfen – drei Zeilen mit verschiedenen Präfixen und Prüfungen,
  eine Hilfe hätte keinen guten Ort (`lernbot.py` ist Konfliktzone mit R2.0), Spec §10.2 fasst `lernbot.parse` erst
  in Stufe 3 an; Startauftrag §4 „lieber drei klare Funktionen als eine clevere“. Auch „still“ bei einer
  Umformulierung des Vermerks stimmte nicht – fünf Tests schlagen an.
- **Doppelprüfung in `upload_fassung` streichen oder den Hinweis in `rendere` legen:** verworfen – Plan-Regel 6
  (`rendere` nur an den Vertrags-Stellen, Merge mit R2.0), und Tests verlangen die Meldung mit „14 Tage“.
- **`Konfig.pflicht` statt zweier Helfer:** optional, nicht umgesetzt (die Regel steht jetzt ausgeschrieben). Die
  Standardwerte bestehender Schlüssel zur Pflicht zu machen: verworfen (neue Ungleichheit zu `cli.py`, `lernbot.py`).
- **/hilfe aus der Konfig füllen:** verworfen – größerer Eingriff in `lernbot.py`, als Plan (e) erlaubt, und /hilfe
  könnte an einem KonfigFehler scheitern.
- **Sonderfall nur für „0:07“:** verworfen zugunsten eines feldbezogenen Fehlertexts („bitte in Sekunden“), der auch
  „7s“ abdeckt.
- **Befund Spec-Abweichungen (c) `gepostet_utc` und (d) neue Konfig-Schlüssel:** widerlegt – (c) entspricht Spec §5,
  und beim Altbestand ist das erste Häkchen das richtige Alter; (d) ist durch A9, A4 und die CLI-Regel abgedeckt.
  Nur (a)/(b) als A35 aufgenommen. Die optionale Glättung („clip-battle.de vor der Konfig prüfen“) nicht umgesetzt:
  Sie ändert, was der bestehende Test `test_clip_battle_bekommt_nie_einen_post` erwartet (Warnung im Log).
- **Beschriftung der Fußzeile „Claude diese Woche … (Screenshots)“:** optional, nicht umgesetzt – PUBLIKUM.md §5
  erklärt es, und die Zeile ändert sich mit Stufe 3/5 ohnehin.
- **ReadOnlyPaths auf `.ssh`, `.bashrc`, `.profile`:** verworfen – eine Liste gesperrter Pfade schließt die Lücke nur
  scheinbar (Probe: claude-Datei, Hooks, `.gitconfig` blieben offen). Stattdessen `CLAUDE_CONFIG_DIR`.
- **Drop-in umbenennen (`publikum-claude.conf`):** verworfen – `e19-puffer.conf` setzt nur `ReadWritePaths` (Listen
  addieren sich), es gibt keinen Konflikt. „Still überschrieben“ widerlegt, Schwere „wichtig“ → „klein“.
- **Probe mit `env -i PATH=/usr/bin:/bin`:** verworfen – zu streng (npm-claude braucht node aus /usr/local/bin) und
  prüft nicht, was `decide` auflöst; stattdessen `shutil.which` mit dem PATH von SSH/systemd.
- **Vermerk bei P3 „erst nach P2“:** verworfen – ohne neuen Lern-Bot gibt es keine Messungen, also keine Scores und
  keine Meldungen; P3 ohne P2 ist harmlos.
- **Alle 29 Annahme-Verweise im Code umschreiben:** verworfen – mit dem Abschnitt in ENTSCHEIDUNGEN.md sind sie per
  Suche auffindbar; nur die fünf Verweise „im Plan“ umgestellt. Die 34 Annahmen stehen **nicht** in CLAUDE.md (dort nur
  L1–L6 und ein Verweis), weil sie offen sind.
- **Test auf „8972k“ im 37-s-Fall:** verworfen – hinge an der Länge des Testmaterials; stattdessen der 45-s-Fall.
- **„gestartet erst nach subprocess.run“:** korrigiert – bei einem Timeout lief claude wirklich, das zählt.

### Annahmen
A1–A43 und die Annahmen der Bauer: `docs/ENTSCHEIDUNGEN.md`, „Annahmen im Sprint Lernschleife“. Neu in der
Nachbesserung: A17 geändert (CLAUDE_CONFIG_DIR), A29 präzisiert, A35–A40. Nach Florians Antworten (unten): A41–A43
neu; A2, A10, A27, A28, A40 und die Rückfragen R3–R5 als entschieden markiert. Weiter offen: R1, R2, A35.

### 🏠 Braucht das echte System
- Installation P1–P3 aus `docs/PUBLIKUM.md` (Code einspielen, beide Bots neu starten, Drop-in, zweite claude-Anmeldung,
  Timer). Nichts davon ist hier gelaufen.
- Wo claude für `pipeline` liegt (`sudo -iu pipeline command -v claude`) und ob `[decide].programm` geändert werden muss.
- Ob claude auf dem Mini mit `CLAUDE_CONFIG_DIR` nichts ins Home schreibt und `--no-session-persistence` kennt
  (hier mit claude 2.1.281 geprüft) – die Probe in P2 zeigt es.
- Ob systemd-run im LXC die Schutz-Eigenschaften der Probe annimmt (die Haupt-Unit nutzt dieselben und läuft).
- Aus welchem Checkout der Lern-Bot läuft (`/opt/clip-regie` laut SPRINT-LOG.md) und dass `alter_tage` für die
  Abnahme in beiden `lokal.toml` steht.
- Abnahme „Fertig, wenn“: ein echter TikTok-Post per Screenshot gemessen und bewertet (Score 0, „Basis zu klein“).
- Wie die echte TikTok-Statistik aussieht (Prompt, Zeitangaben „0:07“) – hier nur mit gefälschten Claude-Antworten.

### Merge mit main und Florians Antworten (25.09.)
- **Merge:** bc314f5 holt `main` (Regisseur 2.0, Epic-ID/MAC als Variable) ohne Textkonflikte in den Branch;
  eb43c53 Nacharbeit: Upload-Fassung nutzt die R2.0-Helfer `_zeile`/`_max_bytes`, neuer Test „Upload-Fassung mit
  Effekten“, zwei Tests an R2.0 angepasst (`schema.pruefe` lehnt NaN ab; Hintergrund auf Viertelgröße).
- **Florians Antworten – erledigt** (Commit „Stufe 1: Florians Antworten – MAD je Komponente, Hand-Eingabe erweitert,
  Installation aus main“):
  - ✅ **R3 MAD-Minimum je Komponente:** neue Tabelle `[publikum.mad_minimum]` (wiedergabe 0,05 · engagement 0,005 ·
    reichweite 0,1) mit Begründung in `config/pipeline.toml`, Beispiel in `config/lokal.beispiel.toml`.
    `publikum.robust_z` bekommt das Minimum als Parameter (Formel weiter genau einmal), `score_fuer` gibt je Teil
    sein Minimum mit und schreibt die benutzten in `score_teile.mad_minimum`. Fehlt die Tabelle oder ein Teil, oder
    ist ein Wert keine Zahl bzw. ≤ 0 → KonfigFehler (CLI Exit 2). Tests mit dem Zahlenbeispiel der Begründung
    (e 0,09 gegen 0,05 … 0,09: z ≈ 1,35 mit 0,005, ≈ 0,27 mit dem pauschalen 0,05), Reichweite (10 % mehr Views:
    z ≈ 0,64 statt 1,28), Konfig-Fehler, `lokal.toml` überschreibt nur einen Teil.
  - ✅ **R4 Hand-Eingabe erweitert:** `views likes wiedergabe voll%` bleibt; optional dahinter
    `kommentare shares saves` (je Zahl oder „–“) – genau 4 oder 7 Werte (A41). Gilt für `lies_hand_eingabe`, den Text
    „#17 …“ ohne Bild, die Bitte nach ✏️ und `/hilfe`; Bitte und Fehlertext sind ein Text (`publikum.HAND_HINWEIS`).
    Die Plausibilität gilt auch für die drei neuen Felder. Tests: sieben Werte, „–“ dazwischen, vollständiges
    Engagement, gesunkene Kommentare → Rückfrage, zu viele Felder, „1.240“ und negative Werte in den neuen Feldern.
  - ✅ **L1–L6 ok:** die Arbeitstitel bleiben (A2 entschieden, kein Umnummerieren).
  - ✅ **Keine clip-battle.de-Checkliste** für Entwürfe (R5): nichts zu bauen, A27 bleibt.
  - ✅ **Installation aus main:** Stufe 1 kommt jetzt über einen eigenen PR nach `main` (Florian will sie sofort
    einspielen). `docs/PUBLIKUM.md` „Bevor du anfängst“ geht davon aus – `git pull` aus `main` wie bisher, nicht erst
    nach Stufe 5; Test `test_stufe_1_kommt_jetzt_aus_main`. README-Dateibaum um `screenshot` und `claude_aufruf`
    ergänzt.
  - Nebenbei (A42): `[publikum.gewichte]` wird mit derselben Hilfe gelesen – ein fehlendes Gewicht ist jetzt
    KonfigFehler (Exit 2) statt eines KeyError, der als Fehler eines einzelnen Posts zählte.
- **Testläufe** (`PYTHONPATH=src`):
  - Vorher, `tests.test_publikum tests.test_publikum_cli tests.test_lernbot_zahlen tests.test_deploy_publikum
    tests.test_ende_zu_ende_publikum` → `Ran 196 tests` / `OK`.
  - Nachher, betroffene Module `tests.test_publikum tests.test_publikum_cli tests.test_lernbot_zahlen
    tests.test_deploy_publikum tests.test_ende_zu_ende_publikum tests.test_lernbot_paket tests.test_screenshot
    tests.test_bot_app tests.test_lernbot tests.test_secrets_dateien tests.test_keine_secrets tests.test_deploy_puffer`
    → `Ran 406 tests in 244.966s` / `OK` (27 Tests neu); dieselben fünf Module wie „vorher“ nach den letzten
    Doku-Änderungen → `Ran 223 tests` / `OK` (196 + 27).
  - Gesamtlauf `python -m unittest discover -s tests -t .` → `Ran 853 tests in 1253.147s` / `OK (skipped=3)` (vorher
    495 auf main, 733 nach ddec0d8; neu übersprungen ist der Leistungstest aus Regisseur 2.0,
    `test_effekte_render`, der nur mit `CLIP_LEISTUNG=1` läuft).
