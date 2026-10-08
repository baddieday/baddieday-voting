"""🔥 Viral-Video (05.10., Florian: „Ich will nur den Button drücken und es soll ein virales Video bei rumkommen … das
soll er von Zeit zu Zeit durch Daten lernen, nicht durch händisch 100te Videos bewerten … keine zu engen
Regie-Regeln, eher professioneller Schnitt mit einem Hauch Humor!“).

Drei Bausteine, alles ohne Bewertungspflicht:

1. KI-Einschätzung je Moment (einschaetzen): claude -p sieht je Moment einen kleinen Kontaktbogen (5 Standbilder um
   Tod bzw. Finisher), die Fakten (Kills, Platz, Bot/Sturm, Waffen-Kategorie, Mikro-Zähler) und den Transkript-Anfang
   und gibt viral/humor/spannung (0–100), einen Titel (≤ 30 Zeichen, nur aus Fakten – geprüft wie die Caption: keine
   erfundene Zahl) und einen Grund. Nur Leserecht, Schema moment_viral, Tageslimit [viral].ki_pro_tag, je Moment
   einmal (Tabelle moment_einschaetzungen). Ohne KI (aus, Limit, Fehler) gilt die Regel (regel) – ein Startwert.
   Die Einschätzung ist EINGABE für Auswahl, Reihenfolge und das Publikums-Lernen, nie selbst Lernziel.

2. Mischung je Variante (mischen, ordne) – der Bot wählt sie selbst:
     twist      überwiegend starke Highlights, dazu 1–2 Fails/Gags als Twist (Hauptweg: Startwissen in [viral.prior])
     highlight  nur Highlights
     fail       nur Fails, steigend, der schlimmste zuletzt (Payoff)
   Auswahl nach Punkten (Gelerntes wie bei jedem Short) + KI-Bonus ([viral].ki_gewicht × (viral − 50)/10).
   Werkzeuge (Hook-Teaser vorn, Zeitlupe und Standbild am Tod) kommen je Entwurf mit einer Wahrscheinlichkeit aus
   [viral.werkzeuge] – keine Pflicht; welche ankommen, lernt das Publikums-Modell (lern_features).

3. Variantenwahl (waehle_variante): Thompson-Sampling wie stile.py über die Cutter-Noten (nur gemessene, mit den
   aktuellen Faktoren neu gerechnet) und – sobald da – das Publikum; nie dreimal dieselbe hintereinander. Fest
   wählbar in ⚙️ ([viral].variante).

Leitplanken bleiben nur die des Shorts: Länge/Momente, Lesbarkeit (Texte im Rand), Blitz-Regel, Titel nur aus Fakten.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import sqlite3
import tempfile
from pathlib import Path

from . import db, medien, schema
from .konfig import Konfig
from .zeit import aus_iso, iso, jetzt

log = logging.getLogger("pipeline")

VARIANTEN = {"twist": "😅 Highlights mit Twist", "highlight": "⚡ Highlights", "fail": "💀 Fail-Video"}
FAILS = {"twist": "mit", "highlight": "ohne", "fail": "nur"}   # regie.kandidaten_mit_bericht(fails=…)
KNOEPFE = ("viral", *VARIANTEN)                                  # /entwurf viral|twist|highlight|fail, /viral
KI_FELDER = ("viral", "humor", "spannung")
SCHEMA = "moment_viral"
TITEL_MAX = 30
TITEL_ZEICHEN = re.compile(r"[^A-Z0-9ÄÖÜ .,!?…–-]")
PRIOR = {"twist": (3.0, 1.0), "highlight": (2.0, 1.0), "fail": (1.0, 1.0)}     # Beta(a, b) ohne Daten
WERKZEUGE = {"hook_teaser": 0.6, "tod_lupe": 0.7, "tod_standbild": 0.6}
FAIL_FORMAT = {"max_s": 60.0, "ziel_s": 40.0, "max_momente": 8}               # reines Fail-Video: 30–60 s, 4–8 Fails
HUMOR_TWIST_AB = 60          # ohne Fail darf ein Moment mit so viel Humor (KI) oder Stimmung „lustig“ der Twist sein
TWIST_FAIL_BONUS = 20        # Vorrang für Fails als Twist (twist_wert)
BILDER = (-3.0, -1.5, -0.5, 0.3, 1.5)   # Standbilder relativ zum Anker (Tod bzw. Finisher)
BILD_B = 240


# --- 1. KI-Einschätzung ---------------------------------------------------------------------------

def _vorlage(konfig: Konfig) -> str:
    return konfig.projektpfad(str(konfig.wert("viral.prompt", "templates/viral-prompt.txt"))).read_text(encoding="utf-8")


def ki_version(konfig: Konfig) -> str:
    """sha256(Prompt + Schema)[:12] – steht an jeder Einschätzung."""
    text = _vorlage(konfig) + json.dumps(schema.lade(SCHEMA), sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def einschaetzungen(con: sqlite3.Connection) -> dict[str, dict]:
    """Gespeicherte KI-Einschätzungen je Moment-Schlüssel."""
    return {z["schluessel"]: {**{k: int(z[k]) for k in KI_FELDER}, "titel": z["titel"], "grund": z["grund"],
                              "quelle": "ki"}
            for z in con.execute("SELECT * FROM moment_einschaetzungen")}


def _ki_heute(con: sqlite3.Connection) -> int:
    return con.execute("SELECT COUNT(*) FROM moment_einschaetzungen WHERE substr(erstellt, 1, 10) = ?",
                       (iso(jetzt())[:10],)).fetchone()[0]


def _zahl(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def pruefe_titel(text: str | None, fakten: dict) -> str | None:
    """KI-Titel säubern und prüfen: Großbuchstaben, nur Zeichen, die die Schrift kann (kein Emoji), ≤ 30 Zeichen,
    jede Zahl gehört zu ihrem Wort (PLATZ n, n KILLS) und ist genau ihr Fakt. None = verworfen. Beispiel: ("Platz 2 😭", {"platz": 2}) → "PLATZ 2"."""
    if not text:
        return None
    sauber = re.sub(r"\s+", " ", TITEL_ZEICHEN.sub("", str(text).upper().replace("...", "…"))).strip(" ,.-–")
    if not sauber or len(sauber) > TITEL_MAX:
        return None
    gebunden = 0
    for muster, werte in ZAHL_WORT:
        for treffer in re.finditer(muster, sauber):
            if int(treffer.group(1)) not in {int(v) for v in werte(fakten) if _zahl(v)}:
                return None
            gebunden += 1
    if len(re.findall(r"\d+", sauber)) != gebunden:   # eine Zahl ohne ihr Wort („5 SEKUNDEN“): erfunden
        return None
    return sauber if all(passt(fakten) for muster, passt in WORT_PRUEFUNG if re.search(muster, sauber)) else None


def _serie(f: dict) -> int:
    return max(int(f.get("kills_serie") or 0), int(f.get("kills_vorher_30s") or 0))


# Jede Zahl im KI-Titel gehört zu ihrem Wort und muss genau ihr Fakt sein (07.10.: vorher reichte irgendeine Zahl aus
# den Fakten – „PLATZ 5“ ging durch, weil 5 der Fail-Score war)
ZAHL_WORT = (
    (r"(?:PLATZ|TOP|#)\s*(\d+)", lambda f: [f.get("platz")]),
    (r"(\d+)\s*(?:KILLS?|ELIMS?)\b", lambda f: [f.get("kills_vorher_30s"), f.get("kills_serie")]),
)

# Wortaussagen im KI-Titel brauchen ihren Fakt (nichts erfinden): sonst gilt der Regel-Titel
WORT_PRUEFUNG = (
    (r"\bBOTS?\b", lambda f: bool(f.get("killer_bot")) if f.get("art") == "fail" else bool(f.get("bot_opfer"))),
    (r"\b(SELBST|STURM|FALLSCHADEN)\b", lambda f: bool(f.get("selbst"))),
    (r"\b(VICTORY|SIEG|GEWONNEN)\b", lambda f: bool(f.get("victory_royale"))),
    (r"\bDOUBLE\b", lambda f: _serie(f) >= 2),
    (r"\bTRIPLE\b", lambda f: _serie(f) >= 3),
    (r"\bQUAD", lambda f: _serie(f) >= 4),
)


def fakten(mk: dict, clip_mk: dict | None, stimmung: str | None, max_gruppe: int = 0, victory: bool = False) -> dict:
    """Was die KI (und der Titel-Prüfer) über einen Moment wissen darf – nur Daten, keine Pfade, keine Namen."""
    from . import fail

    f = {"art": "fail" if mk.get("fail") else "highlight", "stimmung": stimmung,
         "kills_serie": int(max_gruppe or mk.get("max_gruppe") or 0), "victory_royale": bool(victory),
         "mikro": {k: mk.get(k) for k in ("lachen", "jubel", "frust", "jubel_laut") if mk.get(k) is not None}}
    if mk.get("fail"):
        f.update(fail.fakten_text(mk))
    for k in ("sniper", "nahkampf", "bot_opfer", "endgame", "clutch", "platzierung"):
        if clip_mk and _zahl(clip_mk.get(k)):
            f[k] = clip_mk[k]
    return f


def regel(mk: dict, stimmung: str | None, max_gruppe: int = 0, victory: bool = False) -> dict:
    """Rückfall ohne KI (Startwert, nachvollziehbar): viral aus Fail-Score bzw. Kill-Serie und Victory, humor aus
    Lachen/Stimmung (Fail mit Bot oder selbst +20), spannung aus Spielton-Spitzen und Kills."""
    lachen = float(mk.get("lachen") or 0)
    if mk.get("fail"):
        viral = 20 + 8 * float(mk.get("fail_score") or 0)
        humor = 30 + 20 * bool(mk.get("killer_bot") or mk.get("selbst")) + 15 * min(lachen, 2)
    else:
        viral = 10 + 9 * min(10, (0, 1, 3, 6, 10)[min(int(max_gruppe or 0), 4)]) + 15 * bool(victory)
        humor = 15 + 25 * min(lachen, 2)
    if stimmung == "lustig":
        humor = max(humor, 70)
    spannung = 15 * min(float(mk.get("spitzen") or 0), 4) + 10 * min(int(max_gruppe or mk.get("kills") or 0), 4)
    rund = lambda x: int(max(0, min(100, round(x))))  # noqa: E731
    return {"viral": rund(viral), "humor": rund(humor), "spannung": rund(spannung), "titel": None, "grund": None,
            "quelle": "regel"}


def _anker(mk: dict, dauer: float) -> float:
    if mk.get("tod_sekunde") is not None:
        return float(mk["tod_sekunde"])
    kills = [float(x) for x in mk.get("kill_sekunden") or []]
    return kills[-1] if kills else dauer / 2


def kontaktbogen(datei: Path, ziel: Path, anker: float, dauer: float) -> Path:
    """5 Standbilder (je BILD_B px breit) nebeneinander um den Anker – ein ffmpeg-Aufruf. Je Zeitpunkt genau ein
    Bild: das erste ab dem Zeitpunkt (Fenster 0,25 s), weitere im selben Fenster sperrt der Mindestabstand – sonst
    füllten bei 60 fps zwei Bilder je Zeitpunkt die Kachel, und Tod/Kill fehlten (Prüfer 05.10.)."""
    zeiten = sorted({round(min(max(0.0, anker + d), max(0.0, dauer - 0.1)), 2) for d in BILDER})
    fenster = "+".join(f"between(t,{t:.3f},{t + 0.25:.3f})" for t in zeiten)
    auswahl = f"({fenster})*(isnan(prev_selected_t)+gte(t-prev_selected_t,0.3))"
    medien.fuehre_aus(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(datei),
                       "-vf", f"select='{auswahl}',scale={BILD_B}:-2,tile={len(zeiten)}x1",
                       "-frames:v", "1", "-q:v", "5", str(ziel)], "Kontaktbogen Moment", timeout=60)
    return ziel


def _offen(con: sqlite3.Connection, schluessel: list[str] | None) -> list[sqlite3.Row]:
    """Momente ohne Einschätzung, die wichtigsten zuerst: Fails nach Fail-Score, Highlights nach Clip-Punkten."""
    zeilen = con.execute(
        f"""SELECT m.*, c.merkmale AS clip_merkmale, c.max_gruppe AS clip_gruppe, c.victory_royale AS clip_victory,
                   c.punkte AS clip_punkte
              FROM momente m LEFT JOIN clips c ON c.id = m.clip_id
             WHERE NOT EXISTS (SELECT 1 FROM moment_einschaetzungen e WHERE e.schluessel = m.schluessel)
               AND NOT ({db.hart_verworfen_sql('c.')} AND c.id IS NOT NULL)""").fetchall()
    if schluessel is not None:
        reihe = {s: i for i, s in enumerate(schluessel)}
        return sorted((z for z in zeilen if z["schluessel"] in reihe), key=lambda z: reihe[z["schluessel"]])

    def prio(z) -> tuple:
        try:
            mk = json.loads(z["merkmale"])
        except json.JSONDecodeError:
            mk = {}
        wert = float(mk.get("fail_score") or 0) if mk.get("fail") else float(z["clip_punkte"] or z["kills"] or 0)
        return (-wert, -(z["id"] or 0))
    return sorted(zeilen, key=prio)


AUFTRAG_KOPF = "Lies mit dem Read-Tool die Datei momente.json und die darin genannten Bilder im aktuellen Ordner.\n"


def einschaetzen(con: sqlite3.Connection, konfig: Konfig, *, maximal: int | None = None,
                 schluessel: list[str] | None = None) -> dict:
    """KI-Einschätzung für noch offene Momente – höchstens maximal (sonst [viral].ki_je_lauf), nie über das
    Tageslimit [viral].ki_pro_tag, je Aufruf [viral].ki_je_aufruf Momente. Wirft nie (Fehler → Hinweis, Regel gilt).
    Rückgabe {"neu": n, "aufrufe": n, "hinweise": [...]}."""
    from . import claude_aufruf  # hier: claude_aufruf importiert verarbeitung (Kreis vermeiden)

    ergebnis: dict = {"neu": 0, "aufrufe": 0, "hinweise": []}
    if not konfig.wert("viral.ki", True) or not claude_aufruf.ki_moeglich(konfig):   # Freund ohne eigenen Zugang (M1)
        return ergebnis
    rest = int(konfig.wert("viral.ki_pro_tag", 40)) - _ki_heute(con)
    n = min(rest, maximal if maximal is not None else int(konfig.wert("viral.ki_je_lauf", 12)))
    if n <= 0:
        if rest <= 0:
            ergebnis["hinweise"].append("KI-Einschätzung: Tageslimit erreicht – Regel-Score")
        return ergebnis
    offen = [z for z in _offen(con, schluessel) if Path(z["datei"]).is_file()][:n]
    je_aufruf = max(1, int(konfig.wert("viral.ki_je_aufruf", 6)))
    version = ki_version(konfig)
    for i in range(0, len(offen), je_aufruf):
        teil = offen[i:i + je_aufruf]
        with tempfile.TemporaryDirectory() as tmp:
            eintraege, fakten_je = [], {}
            for j, z in enumerate(teil, 1):
                try:
                    mk = json.loads(z["merkmale"])
                    clip_mk = json.loads(z["clip_merkmale"]) if z["clip_merkmale"] else None
                    bild = f"m{j}.jpg"
                    kontaktbogen(Path(z["datei"]), Path(tmp) / bild, _anker(mk, float(z["ende_s"])),
                                 float(z["ende_s"]))
                except (json.JSONDecodeError, medien.MedienFehler, OSError) as e:
                    log.info("Einschätzung %s: kein Kontaktbogen (%s)", z["schluessel"], str(e)[:80])
                    continue
                f = fakten(mk, clip_mk, z["stimmung"], int(z["clip_gruppe"] or mk.get("max_gruppe") or 0),
                           bool(z["clip_victory"] or mk.get("victory_royale")))
                fakten_je[f"m{j}"] = (z["schluessel"], f)
                eintraege.append({"id": f"m{j}", "bild": bild, "fakten": f, "text": (z["text"] or "")[:300]})
            if not eintraege:
                continue
            (Path(tmp) / "momente.json").write_text(json.dumps(eintraege, ensure_ascii=False, indent=1),
                                                     encoding="utf-8")
            antwort = claude_aufruf.frage_json(konfig, AUFTRAG_KOPF + _vorlage(konfig), Path(tmp), schema_name=SCHEMA,
                                               timeout_s=float(konfig.wert("viral.timeout_s", 240)))
        ergebnis["aufrufe"] += 1
        try:
            claude_aufruf.protokolliere(con, "viral", antwort)
        except Exception:  # noqa: BLE001 – Zählen ist Nebensache
            log.exception("Einschätzung protokollieren")
        if antwort.daten is None:
            ergebnis["hinweise"].append(f"KI-Einschätzung: {antwort.hinweis} – Regel-Score")
            break  # Limit oder Ausfall: nicht weiter gegen das Abo laufen
        zeit = iso(jetzt())
        for e in antwort.daten["momente"]:
            if e.get("id") not in fakten_je:
                continue
            moment, f = fakten_je.pop(e["id"])
            with db.transaktion(con):
                con.execute(
                    """INSERT INTO moment_einschaetzungen (schluessel, viral, humor, spannung, titel, grund, version,
                                                         erstellt) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT (schluessel) DO NOTHING""",
                    (moment, int(e["viral"]), int(e["humor"]), int(e["spannung"]), pruefe_titel(e.get("titel"), f),
                     str(e.get("grund") or "")[:140] or None, version, zeit))
            ergebnis["neu"] += 1
    return ergebnis


# --- 2. Mischung ------------------------------------------------------------------------------------

def format_fuer(fmt: dict, variante: str) -> dict:
    """Short-Regeln je Variante: das reine Fail-Video hat 4–8 Fails und 30–60 s (Ziel 40 s)."""
    if variante != "fail":
        return fmt
    neu = dict(fmt)
    neu["max_s"] = min(fmt["max_s"], FAIL_FORMAT["max_s"])
    neu["ziel_s"] = min(neu["max_s"], max(fmt["min_s"], FAIL_FORMAT["ziel_s"]))
    neu["max_momente"] = min(int(fmt.get("max_momente", 10)), FAIL_FORMAT["max_momente"])
    return neu


def _ist_twist(k, ki: dict) -> bool:
    return k.fail or k.stimmung == "lustig" or ki["humor"] >= HUMOR_TWIST_AB


def twist_wert(k) -> float:
    """Wie gut taugt ein Moment als Twist? Humor zählt doppelt, dazu viral; ein Fail bekommt TWIST_FAIL_BONUS
    (der Bruch „eben noch Triple Kill, jetzt tot“ ist der stärkere Twist als ein Gag zwischendurch)."""
    return 2 * k.ki["humor"] + k.ki["viral"] + TWIST_FAIL_BONUS * bool(k.fail)


def mischen(con: sqlite3.Connection, konfig: Konfig, alle: list, variante: str, p: dict, fmt: dict
            ) -> tuple[list, list, list[str]]:
    """(Kandidaten, Pflicht-Momente, Hinweise) für eine Variante. Jeder Kandidat bekommt seine Einschätzung (k.ki,
    KI oder Regel) und den KI-Bonus auf Punkte und Stärke. twist: die besten twist_anzahl Twist-Momente (Fails,
    Gags) sind Pflicht, die übrigen Fails fallen weg – der Rest (auch lustige Kills) sind Highlights. Fail-Titel: KI-Titel,
    wenn er die Prüfung bestand, sonst der Regel-Titel (fail.titel)."""
    gewicht = float(konfig.wert("viral.ki_gewicht", 1.0))
    ki = einschaetzungen(con)
    hinweise: list[str] = []
    for k in alle:
        k.ki = ki.get(k.schluessel) or regel(k.merkmale, k.stimmung, k.max_gruppe, k.victory)
        bonus = round(gewicht * (k.ki["viral"] - 50) / 10, 2) if k.ki["quelle"] == "ki" else 0.0
        k.punkte, k.intensitaet = round(k.punkte + bonus, 2), round(k.intensitaet + bonus, 2)
        if k.fail and k.ki.get("titel"):  # erneut prüfen: KI-Titel bis 07.10. kannten noch den falschen Platz
            k.titel = pruefe_titel(k.ki["titel"], fakten(k.merkmale, None, k.stimmung, k.max_gruppe, k.victory)) \
                or k.titel
    if variante != "twist":
        return alle, [], hinweise
    twists = sorted((k for k in alle if _ist_twist(k, k.ki)), key=lambda k: (-twist_wert(k), k.schluessel))
    anzahl = max(1, min(2, int(p.get("twist_anzahl", 1))))
    pflicht = twists[:anzahl]
    if not pflicht:
        hinweise.append("Twist: kein Fail und kein Gag im Material – nur Highlights")
    # Übrige Fails fallen weg (höchstens twist_anzahl Fails); lustige Highlights bleiben Highlights
    rest = [k for k in alle if not k.fail and not any(k is t for t in pflicht)]
    # Ein Highlight, das dieselbe Szene zeigt wie ein Twist (gleiches Match, Zeitspannen überlappen – z. B. Triple
    # und Tod 5 s später), fällt weg: sonst stünde sie doppelt im Video und das Tor „doppelt“ deckelte die Note
    doppelt = [k for k in rest if any(_ueberlappt(k, t) for t in pflicht)]
    if doppelt:
        rest = [k for k in rest if not any(k is d for d in doppelt)]
        hinweise.append(f"Twist: {len(doppelt)} Highlight(s) mit derselben Szene weggelassen")
    # Der Twist ist nie der Höhepunkt: Seine Intensität (beim Fail der fail_score, eine andere Skala) bleibt unter
    # dem Median der Highlights – sonst gälte er beim Payoff-Kriterium als Höhepunkt bei ~60 % (Teilnote 0)
    if rest and pflicht:
        werte = sorted(k.intensitaet for k in rest)
        deckel = round(werte[len(werte) // 2] - 0.01, 2)
        for t in pflicht:
            t.intensitaet = min(t.intensitaet, deckel)
    return rest + pflicht, pflicht, hinweise


def _spanne(k) -> tuple[float, float] | None:
    """UTC-Spanne der Moment-Datei in Sekunden (None ohne start_utc)."""
    if not k.start_utc:
        return None
    try:
        a = aus_iso(k.start_utc).timestamp()
    except ValueError:
        return None
    return a, a + float(k.dauer_s)


def _ueberlappt(a, b) -> bool:
    """Zeigen zwei Momente desselben Matches dieselbe Szene? Ihre Dateien überlappen um mehr als 1 s."""
    if not a.match_id or a.match_id != b.match_id:
        return False
    sa, sb = _spanne(a), _spanne(b)
    return bool(sa and sb and min(sa[1], sb[1]) - max(sa[0], sb[0]) > 1.0)


def ordne(gewaehlt: list, variante: str, pflicht: list, grund_reihe) -> list:
    """Reihenfolge: fail = steigend (der schlimmste zuletzt); twist = Highlights im Bogen des Stils, die Twists bei
    ~60 % (zwei: ~35 % und ~70 %), nie ganz vorn, nie als Höhepunkt; highlight = Bogen des Stils.
    grund_reihe(liste, reihenfolge=None) baut den Bogen (regie.bogen mit den Stil-Parametern)."""
    if variante == "fail":
        return grund_reihe(gewaehlt, "steigend")
    twists = [k for k in gewaehlt if any(k is t for t in pflicht)]
    basis = grund_reihe([k for k in gewaehlt if not any(k is t for t in twists)], None)
    if not twists or len(basis) < 1:
        return basis + twists
    stellen = [0.6] if len(twists) == 1 else [0.35, 0.7]
    reihe = list(basis)
    for t, anteil in sorted(zip(sorted(twists, key=lambda k: twist_wert(k)), stellen), key=lambda x: -x[1]):
        pos = min(len(basis) - 1, max(1, round(len(basis) * anteil))) if len(basis) > 1 else 0
        reihe.insert(pos, t)
    return reihe


def parameter(con: sqlite3.Connection, konfig: Konfig, variante: str, p: dict) -> dict:
    """Werkzeuge und Mischung je Entwurf in die Regie-Parameter (stehen damit in der Schnittliste und im Lernen):
    Hook-Teaser, Zeitlupe und Standbild am Tod je mit Wahrscheinlichkeit aus [viral.werkzeuge] (deterministisch je
    Entwurf), twist_anzahl 1–2. Das reine Fail-Video läuft steigend."""
    n = con.execute("SELECT COUNT(*) FROM entwuerfe WHERE variante IS NOT NULL").fetchone()[0]
    zufall = random.Random(f"viral:{variante}:{n}")
    werte = {**WERKZEUGE, **{k: float(v) for k, v in (konfig.wert("viral.werkzeuge", {}) or {}).items()
                             if k in WERKZEUGE and _zahl(v)}}
    p = dict(p)
    for name, wahrscheinlich in werte.items():
        p[name] = zufall.random() < max(0.0, min(1.0, wahrscheinlich))
    p["variante"] = variante
    p["twist_anzahl"] = 2 if variante == "twist" and zufall.random() < 0.35 else 1
    if variante == "fail":
        p["reihenfolge"], p["hook_staerkster"] = "steigend", False
    return p


def werkzeuge_im_video(segmente: list[dict]) -> dict[str, bool]:
    """Welche Werkzeuge wirklich im Video stecken (nicht nur gewürfelt): Hook-Segment, Standbild, Zeitlupe, die
    kurz vor dem Tod endet."""
    return {"hook_teaser": any(s.get("rolle") == "hook" for s in segmente),
            "tod_standbild": any(s.get("standbild") for s in segmente),
            "tod_lupe": any(s.get("lupe") and s.get("tod_s") is not None
                            and float(s["tod_s"]) - 0.5 <= float(s["lupe"]["bis_s"]) <= float(s["tod_s"]) + 1e-3
                            for s in segmente)}


def bilanz(reihe: list, variante: str, p: dict, segmente: list[dict] | None = None) -> dict:
    """Was im Video steckt – für Bot-Text, Caption und Publikums-Lernen (lern_features): Anteile Fail/Humor,
    KI-Mittel (nur echte KI-Einschätzungen), Anteil mit KI, Werkzeuge (aus den Segmenten, sonst die Würfel)."""
    n = max(1, len(reihe))
    mit_ki = [k for k in reihe if (k.ki or {}).get("quelle") == "ki"]
    mittel = {f"ki_{f}": round(sum(k.ki[f] for k in mit_ki) / len(mit_ki), 1) if mit_ki else None for f in KI_FELDER}
    return {"variante": variante, "momente": len(reihe), "fails": sum(1 for k in reihe if k.fail),
            "fail_anteil": round(sum(1 for k in reihe if k.fail) / n, 3),
            "humor_anteil": round(sum(1 for k in reihe if (k.ki or {}).get("humor", 0) >= HUMOR_TWIST_AB
                                      or k.stimmung == "lustig") / n, 3),
            "ki_anteil": round(len(mit_ki) / n, 3), **mittel,
            "werkzeuge": werkzeuge_im_video(segmente) if segmente is not None else {w: bool(p.get(w)) for w in WERKZEUGE}}


# --- 3. Variantenwahl ---------------------------------------------------------------------------------

def statistik(con: sqlite3.Connection, konfig: Konfig | None = None) -> dict[str, dict]:
    """Je Variante: Urteile n und Summe der Noten (0..1) – Cutter-Maßstab (nur gemessene Kritiken, aktuelle
    Faktoren, KI mit κ) und Publikum doppelt, wie stile.statistik."""
    from . import kritik, massstab   # spät: massstab → regie_lernen → stile

    werte: dict[str, dict] = {v: {"n": 0, "summe": 0.0} for v in VARIANTEN}
    faktoren, kappa, start = massstab.faktoren(con), massstab.ki_gewicht(con), massstab.start_gewichte(konfig)
    zeilen = con.execute("""SELECT e.id, e.variante, e.format, k.teile, k.tore, k.ki_score FROM kritiken k
                              JOIN entwuerfe e ON e.id = k.entwurf_id
                             WHERE e.variante IS NOT NULL AND k.mess_version >= 1""").fetchall()
    publikum = massstab._publikum(con, {int(z["id"]): None for z in zeilen})
    for z in zeilen:
        if z["variante"] not in werte:
            continue
        try:
            teile, tore = json.loads(z["teile"] or "{}"), json.loads(z["tore"] or "null")
        except ValueError:
            continue
        _, note = kritik.note_jetzt(teile, tore, z["format"], z["ki_score"], faktoren=faktoren, kappa=kappa,
                                    start=start)
        werte[z["variante"]]["n"] += 1
        werte[z["variante"]]["summe"] += max(0.0, min(1.0, note / 100))
        if (pub := publikum.get(int(z["id"]))) is not None:
            werte[z["variante"]]["n"] += 2
            werte[z["variante"]]["summe"] += 2 * max(0.0, min(1.0, (pub["y"] + 1) / 2))
    return werte


def verfuegbar(con: sqlite3.Connection) -> dict[str, bool]:
    """Welche Varianten das Material hergibt: fail braucht 4 Fail-Momente, twist mindestens einen Fail oder Gag."""
    fails = con.execute("SELECT COUNT(*) FROM momente WHERE schluessel LIKE 'fail:%'").fetchone()[0]
    gags = con.execute("SELECT COUNT(*) FROM momente WHERE stimmung = 'lustig' OR schluessel LIKE 'fail:%'").fetchone()[0]
    humor = con.execute("SELECT COUNT(*) FROM moment_einschaetzungen WHERE humor >= ?", (HUMOR_TWIST_AB,)).fetchone()[0]
    return {"highlight": True, "twist": gags + humor > 0, "fail": fails >= 4}


def waehle_variante(con: sqlite3.Connection, konfig: Konfig) -> str:
    """Variante des nächsten 🔥 Viral-Videos: fest aus [viral].variante (⚙️) oder Thompson-Sampling über Startwissen
    ([viral.prior], Beta) + Noten. Deterministisch je Entwurf; nie dreimal dieselbe hintereinander."""
    da = verfuegbar(con)
    fest = str(konfig.wert("viral.variante", "auto") or "auto")
    if fest in VARIANTEN and da.get(fest):
        return fest
    n = con.execute("SELECT COUNT(*) FROM entwuerfe WHERE variante IS NOT NULL").fetchone()[0]
    zufall = random.Random(f"viral:{n}")
    prior = {v: tuple((konfig.wert(f"viral.prior.{v}", None) or PRIOR[v])) for v in VARIANTEN}
    werte = statistik(con, konfig)
    zuege = sorted(((zufall.betavariate(prior[v][0] + w["summe"], prior[v][1] + w["n"] - w["summe"]), v)
                    for v, w in werte.items() if da.get(v)), reverse=True)
    letzte = [z["variante"] for z in con.execute(
        "SELECT variante FROM entwuerfe WHERE variante IS NOT NULL ORDER BY id DESC LIMIT 2")]
    for _wert, v in zuege:
        if not (len(letzte) == 2 and letzte[0] == letzte[1] == v):
            return v
    return zuege[0][1] if zuege else "highlight"


def varianten_zeile(con: sqlite3.Connection, konfig: Konfig | None = None) -> str:
    """Für /lernstand: „🔥 Viral (Cutter-Score im Schnitt): 😅 Twist 72 (4×) · …“."""
    werte = statistik(con, konfig)
    teile = [f"{VARIANTEN[v]} {100 * w['summe'] / w['n']:.0f} ({w['n']}×)" if w["n"] else f"{VARIANTEN[v]} –"
             for v, w in werte.items()]
    ki = con.execute("SELECT COUNT(*) FROM moment_einschaetzungen").fetchone()[0]
    return "🔥 Viral-Varianten (lernt selbst): " + " · ".join(teile) + f" · {ki} Momente KI-eingeschätzt"
