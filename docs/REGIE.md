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
| `pipeline render-entwurf <id> --final` | volle Qualität auf pve-big (NVENC), danach sofort aus |
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
  👍/👎 → Gründe an-/abwählen → ✅ fertig.
- `/lernstand` zeigt, was der Regisseur gelernt hat, `/musik` die Titel, `/stand` einen Satz zum Stand.
- Abends um 21:00 kommt ein Satz zum Stand (`[lernbot].abend_uhrzeit`).

## Selbst steuern – auch ohne Bot
- **Vorgaben** in `config/lokal.toml` (Beispiel mit allen Schlüsseln: `config/lokal.beispiel.toml`):
  `[regie.vorgaben]` für Schnitt-Tempo, Länge, Puffer, Übergänge, Musik-Pegel und bevorzugte Stimmungen,
  `[regie.musik_ziele.<stimmung>]` für Tempo und Energie der Musik. Das sind **Startwerte**: Deine Bewertungen
  verschieben von dort aus weiter. Unbekannte oder unsinnige Werte werden gemeldet und auf Grenzen gestutzt.
- **Bewerten ohne Telegram:** `pipeline bewerte <entwurf> --gut|--schlecht [--grund hektisch --grund lang]`
  (Gründe: `musik`, `hektisch`, `getroffen`, `lang`, `abgeschnitten`) – dieselbe Wirkung wie die Knöpfe.
- **Nachsehen:** `pipeline lernstand` (bzw. `/lernstand` im Bot) zeigt Vorgaben und Gelerntes.

## Was die Gründe bewirken
| Grund | Wirkung beim nächsten compose |
|---|---|
| 🎵 Musik passt nicht | dieser Titel bekommt −1 (je Nennung) |
| 😵 zu hektisch | Segmente +15 % länger, Übergänge +10 %; ab +30 % nur jeder 2., ab +70 % jeder 4. Beat |
| 🎯 Stimmung getroffen | Hauptstimmung +0,5; Musikziel dieser Stimmung rückt 20 % zum benutzten Titel |
| ⏳ zu lang | Ziel-Dauer −10 % (bis 60 %) |
| ✂️ abgeschnitten | +0,5 s vor, +0,3 s nach den Kills |
| 👍/👎 ohne Grund | Hauptstimmung ±0,25 (ab 3 Bewertungen) |

Alles wird bei jedem Lauf aus den gespeicherten Bewertungen neu berechnet und ist begrenzt – ein Ausreißer
kann nichts kaputt machen.

## Stimmungen und Übergänge
| Stimmung | erkannt an (Punkte-Regeln in `stimmung.punkte`) | Übergang in den Moment |
|---|---|---|
| episch | Serie ≥ 3, Victory Royale, Jubel nach Kill | harter Schnitt auf dem Beat |
| spannend | Kills, viele Spitzen im Spielton, umgehauen aber überlebt | harter Schnitt |
| lustig | Lachen im Transkript („haha“) | Wischblende 0,3 s |
| frustriert | Tod, Frust-Wörter | Abblende über Schwarz 0,5 s |
| chill | keine Kills, wenig los, leise | weiche Überblendung 0,8 s |

Die Mitte jedes Übergangs liegt genau auf dem Beat; kein Kill wird angeschnitten (fachliche Prüfung der
Schnittliste vor dem Speichern).
