# Stufe 2 – Vertragsentwurf (aus der Vorplanung, noch nicht angelegt)

Wird als Commit „Stufe 2: Vertrag“ angelegt (Startauftrag Abschnitt 3, Schritt 2). Stand 25.09.2026.

```text
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


```
