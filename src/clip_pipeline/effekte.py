"""Effekt-Planer des Regisseurs 2.0: welcher Effekt wann und wie stark – reines Python, ohne ffmpeg.

Plan ≠ Render: Nur plane() (aufgerufen in regie.erstelle) entscheidet. Jedes Ereignis steht in der Schnittliste
(segmente[].effekte: Quellzeit t_s, Stärke 0..1, feste Namen aus schemas/regie.schema.json). Der Renderer übersetzt
nur – über zeitleiste() und uebergangs_fenster() – und liest nie liste["parameter"]. auf_zeitleiste()/auf_quelle()
sind die EINZIGE Umrechnung zwischen Quelle und Zeitleiste.

Spielbild clean (Entscheidung 25.09.): Im Spielbild gibt es nur Übergänge, Zoom (punch, akzent, meme), später die
Zeitlupe und den Farblook – keinen Blitz, kein Wackeln, keinen Glitch-Stoß (Glitch nur als Übergangsart). Texte
liegen außerhalb des Spielbilds: im Short Kill-Titel und Zähler im unscharfen Rand, im 16:9-Zusammenschnitt kein
Zähler und ein Kill-Titel nur während der Übergangsblende direkt nach dem Segment mit der Serie.

Anker = die sichtbare Aktion: mein Umhauen (merkmale.aktion_sekunden), sonst der Kill. Gezählt wird wie in der
Vorbewertung: Kette = Kills mit ≤ [vorbewertung].multikill_fenster_s Abstand (nach Kill-Zeit).

plane() in dieser Reihenfolge:
   1. sichtbar     Anker ≥ 0,1 s vom Segmentrand bzw. vom Übergangs-Griff entfernt -> segment["kill_s"]
   2. Ketten       über alle Kills des Moments; Ereignisse nur an sichtbaren Ankern
   3. Finisher     die letzte Aktion einer Kette: Punch + Bass-Hit; die anderen Kills: Mini-Punch + Tick
   4. Titel        einmal je Kette ab titel_ab_kette Kills, am Ende der Kette (DOUBLE … PENTA, ab 6 MULTI KILL);
                   VICTORY ROYALE ersetzt überlappende Titel. 16:9: nur in der Blende nach dem Moment (nach seinem
                   letzten Teil – Jump-Cuts liegen innerhalb der Serie)
   5. Zähler       „KILLS n“ je sichtbarem Kill, laufende Summe im Video (nur Short). Short: Titel und Zähler enden
                   spätestens am Anfang einer Zoom-Blende (xfade zoomin vergrößert das ganze Bild, das Spielbild
                   wüchse unter den Text)
   6. Tod          Punch + Einschlag (nur frustriert hat dafür Stärke), kein Titel
   7. Jubel        Meme-Zoom + Pop auf der ersten Jubel-Spitze (nur lustig)
   8. Riser        endet auf dem ersten Kill des Höhepunkts (wenn davor ≥ 1,5 s Video liegen)
   9. Whoosh       auf jedem weichen Übergang (nicht Schnitt, nicht Abblende über Schwarz)
  10. Budget       Zoom-Ereignisse ≥ effekt_abstand_s auseinander (Vorrang Finisher > Meme > Punch > Akzent),
                   kein Zoom-Start in einer Übergangsblende
  11. Akzente      kleiner Zoom auf einem Musik-Beat, wenn max_ruhe_s lang weder Schnitt noch Zoom war
  12. Stärke unter der Schwelle fällt weg, Zeiten auf ms
Übergänge haben immer Stärke 1 (§4), auch der Glitch-Übergang.
Gelernt (regie_lernen): effekt_staerke[stimmung] wirkt auf alle Effekte, effekt_hektik nur auf HEKTISCH (Beat-Akzente).
"""

from __future__ import annotations

import bisect
import copy
from dataclasses import dataclass

from . import schema
from .konfig import Konfig

RAND_S = 0.1             # sichtbar: so weit weg vom Segmentrand bzw. vom Übergangs-Griff
PUNCH_S = 0.35
AKZENT_S = 0.2
MEME_S = 0.83            # 0,08 s hinein, 0,6 s halten, 0,15 s zurück
TITEL_VERSATZ_S = 0.1    # Titel kurz nach der Aktion
TITEL_MAX_S = 1.2
TITEL_MIN_S = 0.4        # kürzer würde er nur aufblitzen -> weglassen
VICTORY_VERSATZ_S = 0.4
VICTORY_MIN_S = 1.0
TEXT_MAX_S = 3.0         # Schema: dauer_s ≤ 3
ZAEHLER_MAX_S = 1.5
RISER_S = 1.5
MAX_KILL_S = 20          # Schema: kill_s maxItems
MAX_JE_SEGMENT = 24      # Schema: effekte maxItems
MAX_JE_LISTE = 400       # regie.pruefe_liste

TITEL = {2: "DOUBLE KILL", 3: "TRIPLE KILL", 4: "QUAD KILL", 5: "PENTA KILL"}
MULTI = "MULTI KILL"      # ab 6
VICTORY = "VICTORY ROYALE"
LOOKS = ("neutral", "cinematic", "kalt", "warm", "entsaettigt", "soft")
LOOK_JE_STIMMUNG = {"episch": "cinematic", "spannend": "kalt", "lustig": "warm", "frustriert": "entsaettigt",
                    "chill": "soft"}
ZOOM = ("punch", "akzent", "meme")    # wirkt nur auf das Spielbild
HEKTISCH = {"akzent"}                 # gedämpft durch effekt_hektik („zu hektisch“); Blitz/Wackeln gibt es nicht (Ü1)
VERGROESSERND = {"zoom"}              # Übergänge, die das GANZE Bild vergrößern (xfade zoomin) – Short: Texte enden davor
# Zoom-Budget: wer gewinnt bei zu engem Abstand (Tod-Punch zählt wie ein Finisher)
RANG_FINISHER, RANG_MEME, RANG_PUNCH, RANG_AKZENT = 3, 2, 1, 0

_GEMEINSAM = {"mini_faktor": 0.5, "titel_ab_kette": 2}
# Stärken 0..1 je Stimmung (0 = aus). uebergaenge: Rotation (Art, Dauer s) für die Übergänge IN Momente dieser Stimmung.
PROFIL = {
    "episch": {**_GEMEINSAM, "punch": 0.7, "titel": 1.0, "zaehler": 1.0, "akzent": 0.5, "max_ruhe_s": 2.5,
               "meme": 0.0, "tod_punch": 0.0, "basshit": 0.9, "tick": 0.5, "whoosh": 0.6, "pop": 0.0,
               "einschlag": 0.0, "riser": 0.6, "lupe": 0.8, "look": ("cinematic", 0.8),
               "uebergaenge": [("schnitt", 0.0), ("whip", 0.25), ("schnitt", 0.0), ("zoom", 0.3)]},
    "spannend": {**_GEMEINSAM, "punch": 0.5, "titel": 1.0, "zaehler": 0.8, "akzent": 0.4, "max_ruhe_s": 2.5,
                 "meme": 0.0, "tod_punch": 0.0, "basshit": 0.7, "tick": 0.4, "whoosh": 0.6, "pop": 0.0,
                 "einschlag": 0.0, "riser": 0.5, "lupe": 0.5, "look": ("kalt", 0.6),
                 "uebergaenge": [("whip", 0.25), ("schnitt", 0.0), ("glitch", 0.2), ("schnitt", 0.0)]},
    "lustig": {**_GEMEINSAM, "punch": 0.3, "titel": 1.0, "zaehler": 0.4, "akzent": 0.3, "max_ruhe_s": 3.0,
               "meme": 0.8, "tod_punch": 0.0, "basshit": 0.0, "tick": 0.3, "whoosh": 0.4, "pop": 0.5,
               "einschlag": 0.0, "riser": 0.0, "lupe": 0.0, "look": ("warm", 0.5),
               "uebergaenge": [("wipeleft", 0.3), ("squeeze", 0.3), ("slideleft", 0.3)]},
    "frustriert": {**_GEMEINSAM, "punch": 0.3, "titel": 0.0, "zaehler": 0.6, "akzent": 0.0, "max_ruhe_s": None,
                   "meme": 0.0, "tod_punch": 0.3, "basshit": 0.4, "tick": 0.3, "whoosh": 0.0, "pop": 0.0,
                   "einschlag": 0.7, "riser": 0.0, "lupe": 0.6, "look": ("entsaettigt", 0.7),
                   "uebergaenge": [("fadeblack", 0.5), ("glitch", 0.2)]},
    "chill": {**_GEMEINSAM, "punch": 0.0, "titel": 1.0, "zaehler": 0.0, "akzent": 0.0, "max_ruhe_s": None,
              "meme": 0.0, "tod_punch": 0.0, "basshit": 0.0, "tick": 0.0, "whoosh": 0.25, "pop": 0.0,
              "einschlag": 0.0, "riser": 0.0, "lupe": 0.0, "look": ("soft", 0.3),
              "uebergaenge": [("fade", 0.8), ("dissolve", 0.6)]},
}
STAERKEN = {"mini_faktor", "punch", "titel", "zaehler", "akzent", "meme", "tod_punch", "basshit", "tick", "whoosh",
            "pop", "einschlag", "riser", "lupe"}

# [regie.effekte]: Standardwerte und Grenzen. Weitere Schlüssel (z. B. für Export oder Hook) lesen andere Module selbst.
STANDARD = {"an": True, "profil_version": 1, "schwelle": 0.15, "effekt_abstand_s": 0.4, "max_glitch": 1,
            "sfx_ordner": "/var/lib/clip-pipeline/sfx", "sfx_pegel": 0.8, "titel_zeichenbreite": 0.75}
GRENZEN = {"profil_version": (1, 99), "schwelle": (0.0, 1.0), "effekt_abstand_s": (0.0, 5.0), "max_glitch": (0, 20),
           "sfx_pegel": (0.0, 2.0), "titel_zeichenbreite": (0.3, 1.5)}


@dataclass
class Ereignis:
    """Ein Effekt auf der Zeitleiste (t in Sekunden im Video) – das, was der Renderer umsetzt. nr = Segment."""
    art: str
    t: float
    dauer: float
    staerke: float
    text: str | None = None
    zahl: int | None = None
    klang: str | None = None
    nr: int = 0


@dataclass
class _Plan:
    """Ereignis während der Planung: Segment-Index i, Zeit t auf der Zeitleiste, Vorrang im Zoom-Budget."""
    art: str
    i: int
    t: float
    staerke: float
    dauer: float | None = None
    text: str | None = None
    zahl: int | None = None
    klang: str | None = None
    rang: int = 0


# --- Profil und Konfiguration ------------------------------------------------------------------

def _zahl(wert) -> bool:
    return isinstance(wert, (int, float)) and not isinstance(wert, bool)


def _grenze(wert: float, unten: float, oben: float) -> float:
    return round(max(unten, min(oben, float(wert))), 3)


def uebergangs_arten() -> list[str]:
    """Erlaubte Übergangs-Arten – eine Quelle: das Schema der Schnittliste."""
    segment = schema.lade("regie")["properties"]["segmente"]["items"]
    return list(segment["properties"]["uebergang"]["properties"]["art"]["enum"])


def profil(konfig: Konfig, stimmung: str) -> tuple[dict, list[str]]:
    """PROFIL[stimmung] mit deinen Überschreibungen aus [regie.effekte.<stimmung>]: (Profil, Hinweise).
    Stärken werden auf 0..1 gestutzt; look/uebergaenge nur mit bekannten Namen, sonst Hinweis (wie vorgaben()).

    Beispiel in config/lokal.toml:
        [regie.effekte.episch]
        punch = 0.5                   # sanfterer Zoom
        look = "warm"                 # und look_staerke = 0.4
        uebergaenge = [["whip", 0.25], ["schnitt", 0]]"""
    prof = copy.deepcopy(PROFIL[stimmung])
    hinweise: list[str] = []
    roh = konfig.wert(f"regie.effekte.{stimmung}", {})
    if not isinstance(roh, dict):
        return prof, [f"regie.effekte.{stimmung} ignoriert (kein Abschnitt)"]
    arten = uebergangs_arten()
    for name, wert in roh.items():
        wo = f"regie.effekte.{stimmung}.{name}"
        if name == "look" and wert in LOOKS:
            prof["look"] = (wert, prof["look"][1])
        elif name == "look_staerke" and _zahl(wert):
            prof["look"] = (prof["look"][0], _grenze(wert, 0.0, 1.0))
        elif name == "uebergaenge" and isinstance(wert, list) and wert and all(
                isinstance(u, list) and len(u) == 2 and u[0] in arten and _zahl(u[1]) for u in wert):
            prof["uebergaenge"] = [(u[0], 0.0 if u[0] == "schnitt" else _grenze(u[1], 0.0, 3.0)) for u in wert]
        elif name == "max_ruhe_s" and _zahl(wert):
            prof["max_ruhe_s"] = None if wert <= 0 else _grenze(wert, 0.5, 10.0)   # 0 = keine Beat-Akzente
        elif name == "titel_ab_kette" and _zahl(wert):
            prof["titel_ab_kette"] = int(_grenze(wert, 2, 6))
        elif name in STAERKEN and _zahl(wert):
            prof[name] = _grenze(wert, 0.0, 1.0)
        else:
            hinweise.append(f"{wo} ignoriert (unbekannt oder ungültiger Wert)")
    return prof, hinweise


def einstellungen(konfig: Konfig) -> tuple[dict, list[str]]:
    """[regie.effekte] mit Standardwerten und den Profilen je Stimmung: (Einstellungen, Hinweise)."""
    roh = konfig.wert("regie.effekte", {}) or {}
    e, hinweise = dict(STANDARD), []
    for name, wert in (roh.items() if isinstance(roh, dict) else []):
        if name in PROFIL:
            continue  # Stimmungs-Abschnitt, siehe profil()
        if isinstance(wert, dict):
            hinweise.append(f"regie.effekte.{name} ignoriert (keine Stimmung)")
        elif name in STANDARD:
            standard = STANDARD[name]
            if isinstance(standard, (bool, str)) and type(wert) is type(standard):
                e[name] = wert
            elif name in GRENZEN and _zahl(wert):
                unten, oben = GRENZEN[name]
                e[name] = int(_grenze(wert, unten, oben)) if isinstance(standard, int) else _grenze(wert, unten, oben)
            else:
                hinweise.append(f"regie.effekte.{name} ignoriert (falscher Typ)")
    e["profile"] = {}
    for stimmung in PROFIL:
        e["profile"][stimmung], h = profil(konfig, stimmung)
        hinweise += h
    return e, hinweise


def staerke(wert: float, p: dict, stimmung: str, effekt: str, schwelle: float = STANDARD["schwelle"]) -> float:
    """Profilwert × gelernte effekt_staerke[stimmung] (× effekt_hektik bei hektischen Effekten), 0..1.
    Unter der Schwelle -> 0 (Effekt fällt weg)."""
    s = float(wert) * float((p.get("effekt_staerke") or {}).get(stimmung, 1.0))
    if effekt in HEKTISCH:
        s *= float(p.get("effekt_hektik", 1.0))
    s = max(0.0, min(1.0, s))
    return 0.0 if s < schwelle - 1e-9 else round(s, 3)


def titel_text(n: int) -> str:
    return TITEL.get(n, MULTI) if n >= 2 else ""


# --- Ketten und Anker ------------------------------------------------------------------------

def _ketten(paare: list[tuple[float, float]], fenster_s: float) -> list[list[tuple[float, float]]]:
    """(Kill, Anker)-Paare nach Kill-Zeit zu Ketten: ≤ fenster_s zum vorherigen Kill (auf ms: 17,3 − 7,3 = 10)."""
    gruppen: list[list[tuple[float, float]]] = []
    for paar in sorted(paare):
        if gruppen and round(paar[0] - gruppen[-1][-1][0], 3) <= fenster_s:
            gruppen[-1].append(paar)
        else:
            gruppen.append([paar])
    return gruppen


def ketten(kill_s: list[float], fenster_s: float) -> list[list[float]]:
    """Float-Variante von vorbewertung.gruppiere: Kette = Kills mit höchstens fenster_s Abstand zum vorherigen."""
    return [[k for k, _ in kette] for kette in _ketten([(float(k), float(k)) for k in kill_s], fenster_s)]


def kills_mit_anker(mk: dict) -> list[tuple[float, float]]:
    """(Kill, Anker) je Kill, nach Kill-Zeit sortiert. Anker = mein Umhauen (wie im Schnitt, regie._aktionen),
    bei alten Momenten ohne aktion_sekunden der Kill selbst."""
    from .regie import _aktionen  # regie importiert dieses Modul

    kills = [float(k) for k in mk.get("kill_sekunden") or []]
    aktionen = _aktionen(mk)
    return sorted(zip(kills, aktionen if aktionen is not None else kills))


# --- Übergänge -----------------------------------------------------------------------------

def uebergang(stimmung: str, index: int, ist_hoehepunkt: bool, p: dict, an: bool, glitch_zaehler: int, *,
              profil_: dict | None = None, max_glitch: int = STANDARD["max_glitch"]) -> tuple[str, float]:
    """Übergang in einen Moment: (Art, Dauer s). Aus: regie.UEBERGANG wie bisher. An: Rotation aus dem Profil
    (index = frühere Momente derselben Stimmung); in einen epischen/spannenden Höhepunkt harter Schnitt auf den
    Drop; mehr als max_glitch Glitches -> Whip. Dauer × uebergang_faktor."""
    if not an:
        from .regie import UEBERGANG

        art, dauer = UEBERGANG[stimmung]
    elif ist_hoehepunkt and stimmung in ("episch", "spannend"):
        art, dauer = "schnitt", 0.0
    else:
        folge = (profil_ or PROFIL[stimmung])["uebergaenge"]
        art, dauer = folge[index % len(folge)]
        if art == "glitch" and glitch_zaehler >= max_glitch:
            art, dauer = "whip", 0.25
    return art, round(dauer * float(p.get("uebergang_faktor", 1.0)), 3)


def uebergangs_fenster(liste: dict) -> list[tuple[str, float, float, float]]:
    """(Art, von, bis, Stärke) je weichem Übergang auf der Zeitleiste: die Blende um die Segmentgrenze.
    Harte Schnitte (auch Jump-Cuts innerhalb einer Serie) haben kein Fenster. Stärke: bei Übergängen immer 1 (§4)."""
    fenster = []
    for i, s in enumerate(liste["segmente"]):
        u = s["uebergang"]
        if i > 0 and u["art"] != "schnitt" and u["dauer_s"] > 0:
            d = u["dauer_s"]
            fenster.append((u["art"], round(s["zeit_start"] - d / 2, 3), round(s["zeit_start"] + d / 2, 3), 1.0))
    return fenster


def _in_fenster(t: float, fenster: list[tuple[float, float]]) -> bool:
    return any(a + 1e-6 < t < b - 1e-6 for a, b in fenster)


# --- Zeit: die einzige Umrechnung Quelle <-> Zeitleiste ----------------------------------------------

def _zuschlag(seg: dict, t_q: float) -> float:
    lupe = seg.get("lupe")
    if not lupe or t_q <= lupe["ab_s"]:
        return 0.0
    return (min(t_q, lupe["bis_s"]) - lupe["ab_s"]) * (1 / lupe["faktor"] - 1)


def auf_zeitleiste(seg: dict, t_q: float) -> float:
    """Quellzeit (Sekunden in der Moment-Datei) -> Zeit im Video. Eine Zeitlupe dehnt ab_s … bis_s um 1/faktor."""
    return seg["zeit_start"] + (t_q - seg["quelle_start_s"]) + _zuschlag(seg, t_q)


def auf_quelle(seg: dict, t_z: float) -> float:
    """Umkehrung von auf_zeitleiste."""
    u = t_z - seg["zeit_start"]
    lupe = seg.get("lupe")
    if lupe:
        ab, f = lupe["ab_s"] - seg["quelle_start_s"], lupe["faktor"]
        gedehnt = (lupe["bis_s"] - lupe["ab_s"]) / f
        if ab < u <= ab + gedehnt:
            return lupe["ab_s"] + (u - ab) * f
        if u > ab + gedehnt:
            return seg["quelle_start_s"] + u - (lupe["bis_s"] - lupe["ab_s"]) * (1 / f - 1)
    return seg["quelle_start_s"] + u


def zeitleiste(liste: dict) -> list[Ereignis]:
    """Alle Effekt-Ereignisse auf der Zeitleiste, sortiert. Leer, wenn die Effekte aus sind (oder version 3).
    dauer = Dauer im Video (bei sfx 0: der Klang hat seine eigene Länge)."""
    if not (liste.get("effekte") or {}).get("an"):
        return []
    ergebnis = []
    for s in liste["segmente"]:
        for e in s.get("effekte") or []:
            ergebnis.append(Ereignis(e["art"], round(auf_zeitleiste(s, e["t_s"]), 3), float(e.get("dauer_s", 0.0)),
                                     float(e["staerke"]), e.get("text"), e.get("zahl"), e.get("klang"), s["nr"]))
    return sorted(ergebnis, key=lambda e: (e.t, e.nr, e.art, e.klang or ""))


# --- Planer -------------------------------------------------------------------------------

def _sichtbar(segmente: list[dict], i: int) -> tuple[float, float]:
    """Quellfenster, in dem ein Anker zu sehen ist: RAND_S weg vom Rand, bei weichen Übergängen zusätzlich die
    halbe Blende (dort mischen sich zwei Bilder)."""
    s = segmente[i]
    r_v = s["uebergang"]["dauer_s"] / 2 if i > 0 and s["uebergang"]["art"] != "schnitt" else 0.0
    return s["quelle_start_s"] + r_v + RAND_S, s["quelle_ende_s"] - _r_hinten(segmente, i) - RAND_S


def _r_hinten(segmente: list[dict], i: int) -> float:
    if i + 1 >= len(segmente):
        return 0.0
    u = segmente[i + 1]["uebergang"]
    return u["dauer_s"] / 2 if u["art"] != "schnitt" else 0.0


def _wichtig(e: _Plan) -> int:
    """Was bei zu vielen Ereignissen (Schema-Grenzen) zuletzt wegfällt."""
    if e.art == "titel":
        return 7
    if e.art == "sfx":
        return {"basshit": 6, "einschlag": 6, "riser": 5, "whoosh": 5, "pop": 4}.get(e.klang or "", 1)
    if e.art == "punch":
        return 6 if e.rang >= RANG_FINISHER else 2
    return {"meme": 4, "zaehler": 3}.get(e.art, 0)


def plane(segmente: list[dict], reihe: list, p: dict, konfig: Konfig, fmt_name: str, fps: int,
          beats: list[float], *, stimmung: str) -> dict:
    """Füllt je Segment kill_s (sichtbare Anker) und effekte (Ereignisse in Quellzeit) und gibt liste["effekte"]
    zurück. reihe: die Kandidaten (regie.Kandidat) der Segmente; beats: alle Track-Beats − Versatz (leer ohne Musik);
    stimmung: Hauptstimmung des Videos (Look)."""
    e, _ = einstellungen(konfig)
    prof = e["profile"]
    kurz = fmt_name == "short"
    schwelle, abstand = e["schwelle"], e["effekt_abstand_s"]
    kette_s = float(konfig.wert("vorbewertung.multikill_fenster_s", 10.0))
    gleich = 1.0 / max(1, int(fps))  # näher als ein Bild = gleichzeitig
    momente = {k.schluessel: k for k in reihe}
    alle_fenster = uebergangs_fenster({"segmente": segmente})
    fenster = [(a, b) for _, a, b, _ in alle_fenster]
    gross = [(a, b) for art, a, b, _ in alle_fenster if art in VERGROESSERND]
    plan: list[_Plan] = []

    def stark(i: int, wert: float, effekt: str) -> float:
        return staerke(wert, p, segmente[i]["stimmung"], effekt, schwelle)

    def dazu(ziel: list, art: str, i: int, t: float, wert: float, **felder) -> None:
        if wert > 0:
            ziel.append(_Plan(art, i, round(t, 3), wert, **felder))

    for s in segmente:  # neu planen = von vorn
        s.pop("kill_s", None)
        s.pop("effekte", None)
    je_moment: dict[str, list[int]] = {}
    for i, s in enumerate(segmente):
        if s.get("rolle") != "hook":  # Hook (Stufe 4) plant seine Ereignisse selbst
            je_moment.setdefault(s["moment"], []).append(i)
    grenzen = [_sichtbar(segmente, i) for i in range(len(segmente))]

    def wo(idx: list[int], t_q: float) -> int | None:
        return next((i for i in idx if grenzen[i][0] - 1e-6 <= t_q <= grenzen[i][1] + 1e-6), None)

    titel: list[_Plan] = []
    victory: list[_Plan] = []
    gezaehlt: list[tuple[float, int]] = []   # (Zeit, Segment) je sichtbarem Kill
    for moment, idx in je_moment.items():
        k = momente.get(moment)
        mk = k.merkmale if k is not None else {}
        paare = kills_mit_anker(mk)
        # 1. sichtbar
        for i in idx:
            if sichtbar := sorted({round(a, 3) for _, a in paare if wo([i], a) is not None}):
                segmente[i]["kill_s"] = sichtbar[:MAX_KILL_S]
        # 2./3. Ketten, Finisher
        alle = _ketten(paare, kette_s)
        laengste = max(range(len(alle)), key=lambda j: (len(alle[j]), j)) if alle else -1
        for j, kette in enumerate(alle):
            fin = max(range(len(kette)), key=lambda n: (kette[n][1], kette[n][0], n))  # letzte Aktion der Kette
            for n, (_, a) in enumerate(kette):
                if (i := wo(idx, a)) is None:
                    continue
                pr, t = prof[segmente[i]["stimmung"]], auf_zeitleiste(segmente[i], a)
                if n == fin:
                    dazu(plan, "punch", i, t, stark(i, pr["punch"], "punch"), dauer=PUNCH_S, rang=RANG_FINISHER)
                    dazu(plan, "sfx", i, t, stark(i, pr["basshit"], "basshit"), klang="basshit")
                else:
                    dazu(plan, "punch", i, t, stark(i, pr["punch"] * pr["mini_faktor"], "punch"), dauer=PUNCH_S,
                         rang=RANG_PUNCH)
                    dazu(plan, "sfx", i, t, stark(i, pr["tick"], "tick"), klang="tick")
                gezaehlt.append((t, i))
            # 4. Titel: einmal je Kette, an ihrem Ende. Die längste Kette des Moments heißt wie seine Serie
            # (max_gruppe, wie Bot und Elo zählen; ein Kill der Serie kann vor der Datei liegen) – ein einzelner
            # sichtbarer Kill wird aber nie zum Multikill
            anzahl = len(kette)
            if j == laengste and anzahl >= 2 and k is not None:
                anzahl = max(anzahl, int(k.max_gruppe or 0))
            if (i := wo(idx, kette[fin][1])) is not None and anzahl >= prof[segmente[i]["stimmung"]]["titel_ab_kette"]:
                t = auf_zeitleiste(segmente[i], kette[fin][1]) + TITEL_VERSATZ_S
                dazu(titel, "titel", i, t, stark(i, prof[segmente[i]["stimmung"]]["titel"], "titel"),
                     text=titel_text(anzahl), rang=anzahl)
        if k is not None and k.victory and alle:
            letzte = alle[-1]
            a = max(a for _, a in letzte)
            if (i := wo(idx, a)) is not None:
                s = segmente[i]
                start, ende = auf_zeitleiste(s, a) + VICTORY_VERSATZ_S, s["zeit_ende"]
                if ende - start < VICTORY_MIN_S:
                    start = max(s["zeit_start"], ende - VICTORY_MIN_S)
                dazu(victory, "titel", i, start, stark(i, prof[s["stimmung"]]["titel"], "titel"), text=VICTORY,
                     dauer=min(TEXT_MAX_S, ende - start), rang=99)
        # 6. Tod, 7. Jubel
        if (tod := mk.get("tod_sekunde")) is not None and (i := wo(idx, float(tod))) is not None:
            pr, t = prof[segmente[i]["stimmung"]], auf_zeitleiste(segmente[i], float(tod))
            dazu(plan, "punch", i, t, stark(i, pr["tod_punch"], "punch"), dauer=PUNCH_S, rang=RANG_FINISHER)
            dazu(plan, "sfx", i, t, stark(i, pr["einschlag"], "einschlag"), klang="einschlag")
        for jubel in sorted(float(x) for x in mk.get("jubel_laut_s") or []):
            if (i := wo(idx, jubel)) is not None:
                pr, t = prof[segmente[i]["stimmung"]], auf_zeitleiste(segmente[i], jubel)
                dazu(plan, "meme", i, t, stark(i, pr["meme"], "meme"), dauer=MEME_S, rang=RANG_MEME)
                dazu(plan, "sfx", i, t, stark(i, pr["pop"], "pop"), klang="pop")
                break

    plan += _titel_setzen(titel, victory, segmente, fenster, kurz, gross)
    if kurz:  # 5. Zähler (im 16:9 kein Zähler)
        plan += _zaehler(gezaehlt, segmente, prof, stark, gleich, gross)
    # 8. Riser in den ersten sichtbaren Kill des Höhepunkts
    if reihe and (idx := je_moment.get(reihe[-1].schluessel)):
        erste = min(((auf_zeitleiste(segmente[i], a), i) for i in idx for a in segmente[i].get("kill_s", [])),
                    default=None)
        if erste is not None and erste[0] >= RISER_S - 1e-6:
            t, i = erste
            dazu(plan, "sfx", i, t, stark(i, prof[segmente[i]["stimmung"]]["riser"], "riser"), klang="riser")
    # 9. Whoosh auf den weichen Übergängen (Spitze auf dem Schnitt)
    for i, s in enumerate(segmente[1:], 1):
        if s["uebergang"]["art"] not in ("schnitt", "fadeblack") and s["uebergang"]["dauer_s"] > 0:
            dazu(plan, "sfx", i, s["zeit_start"], stark(i, prof[s["stimmung"]]["whoosh"], "whoosh"), klang="whoosh")
    # 10. Budget: Zoom nie in einer Blende, Abstand ≥ effekt_abstand_s; gleichzeitige Treffer-Klänge nur einmal
    zooms = []
    for z in sorted((z for z in plan if z.art in ZOOM and not _in_fenster(z.t, fenster)),
                    key=lambda z: (-z.rang, -z.staerke, z.t, z.i)):
        if all(abs(z.t - b.t) >= abstand - 1e-9 for b in zooms):
            zooms.append(z)
    plan = _klaenge_einmal([z for z in plan if z.art not in ZOOM], gleich) + zooms
    # 11. Beat-Akzente
    if beats:
        plan += _akzente(beats, segmente, prof, stark, [z.t for z in zooms], fenster, abstand)
    _speichern([z for z in plan if z.staerke > 0], segmente)

    look, look_staerke = prof[stimmung]["look"]
    look_staerke = staerke(look_staerke, p, stimmung, "look", schwelle)
    return {"an": True, "profil_version": int(e["profil_version"]), "look": look if look_staerke > 0 else "neutral",
            "look_staerke": look_staerke, "hook": False, "loop": False}


def _moment_ende(segmente: list[dict], i: int) -> int:
    """Letztes Segment des Moments von Segment i: Teile nach einem Jump-Cut (teil > 1) gehören noch dazu."""
    while (i + 1 < len(segmente) and segmente[i + 1]["moment"] == segmente[i]["moment"]
           and segmente[i + 1].get("teil", 1) > 1):
        i += 1
    return i


def _bis_blende(t: float, dauer: float, gross: list[tuple[float, float]]) -> float:
    """Short: Ein Text endet spätestens am Anfang der nächsten Zoom-Blende. Dort vergrößert xfade zoomin das ganze
    Bild, das Spielbild wüchse über den unscharfen Rand unter den Text (Ü2)."""
    return min([dauer, *(max(0.0, a - t) for a, b in gross if b > t + 1e-6)])


def _titel_setzen(titel: list[_Plan], victory: list[_Plan], segmente: list[dict], fenster: list[tuple[float, float]],
                  kurz: bool, gross: list[tuple[float, float]]) -> list[_Plan]:
    """Short: Titel am Ende der Kette, bis zum nächsten Titel (≤ 1,2 s, ≤ Segmentende, ≤ Anfang einer Zoom-Blende);
    VICTORY ROYALE ersetzt überlappende. 16:9: ein Titel nur während der Blende direkt nach dem Moment (nach seinem
    letzten Teil) – bei hartem Schnitt oder am Ende keiner; wollen mehrere dorthin, gewinnt der höchste."""
    if not kurz:
        je_fenster: dict[int, _Plan] = {}
        for z in [*titel, *victory]:
            i = _moment_ende(segmente, z.i)
            if i + 1 >= len(segmente):
                continue
            u = segmente[i + 1]["uebergang"]
            if u["art"] == "schnitt" or u["dauer_s"] <= 0:
                continue
            a = segmente[i + 1]["zeit_start"] - u["dauer_s"] / 2
            neu = _Plan("titel", i, round(a, 3), z.staerke, dauer=u["dauer_s"], text=z.text, rang=z.rang)
            alt = je_fenster.get(i)
            if alt is None or (neu.rang, neu.t) > (alt.rang, alt.t):
                je_fenster[i] = neu
        return list(je_fenster.values())
    for z in titel:
        z.dauer = min(TITEL_MAX_S, segmente[z.i]["zeit_ende"] - z.t)
    for v in victory:  # ersetzt überlappende Kill-Titel
        titel = [z for z in titel if z.t + z.dauer <= v.t + 1e-6 or z.t >= v.t + v.dauer - 1e-6]
    alle = sorted([*titel, *victory], key=lambda z: z.t)
    for z, naechster in zip(alle, alle[1:]):
        z.dauer = min(z.dauer, naechster.t - z.t)
    for z in alle:
        z.dauer = _bis_blende(z.t, z.dauer, gross)
    return [z for z in alle if z.dauer >= TITEL_MIN_S - 1e-6 and not _in_fenster(z.t, fenster)]


def _zaehler(gezaehlt: list[tuple[float, int]], segmente: list[dict], prof: dict, stark, gleich: float,
             gross: list[tuple[float, float]]) -> list[_Plan]:
    """„KILLS n“ je sichtbarem Kill in Zeitleisten-Reihenfolge; gleichzeitige Kills zählen zusammen. Endet vor einer
    Zoom-Blende (_bis_blende)."""
    gruppen: list[list[tuple[float, int]]] = []
    for t, i in sorted(gezaehlt):
        if gruppen and t - gruppen[-1][0][0] < gleich:
            gruppen[-1].append((t, i))
        else:
            gruppen.append([(t, i)])
    ergebnis, summe = [], 0
    for n, gruppe in enumerate(gruppen):
        summe += len(gruppe)
        t, i = gruppe[0]
        bis = gruppen[n + 1][0][0] if n + 1 < len(gruppen) else t + ZAEHLER_MAX_S
        wert = stark(i, prof[segmente[i]["stimmung"]]["zaehler"], "zaehler")
        dauer = _bis_blende(t, min(ZAEHLER_MAX_S, bis - t), gross)
        if wert > 0 and dauer > 0:  # gezählt wird auch ohne Anzeige (z. B. chill)
            ergebnis.append(_Plan("zaehler", i, round(t, 3), wert, dauer=dauer, zahl=min(99, summe)))
    return ergebnis


def _klaenge_einmal(ereignisse: list[_Plan], gleich: float) -> list[_Plan]:
    """Treffer-Klänge (Bass-Hit, Tick, Einschlag) zur selben Zeit nur einmal: der wichtigste bleibt."""
    treffer = {"einschlag": 3, "basshit": 2, "tick": 1}
    behalten: list[_Plan] = []
    for z in sorted(ereignisse, key=lambda z: (-treffer.get(z.klang or "", 0), -z.staerke, z.t)):
        if z.klang in treffer and any(b.klang in treffer and abs(b.t - z.t) < gleich for b in behalten):
            continue
        behalten.append(z)
    return behalten


def _akzente(beats: list[float], segmente: list[dict], prof: dict, stark, zoom_t: list[float],
             fenster: list[tuple[float, float]], abstand: float) -> list[_Plan]:
    """Kleiner Zoom auf einem Beat, wenn max_ruhe_s lang weder ein Schnitt noch ein Zoom war – nicht in einer
    Blende, nicht kurz vor einem anderen Zoom, nicht über das Segmentende hinaus."""
    starts = [s["zeit_start"] for s in segmente]
    dauer = segmente[-1]["zeit_ende"] if segmente else 0.0
    zoom_t = sorted(zoom_t)
    ergebnis = []
    for b in sorted({round(float(x), 3) for x in beats}):
        if not 0 < b < dauer:
            continue
        i = bisect.bisect_right(starts, b) - 1
        s = segmente[i]
        pr = prof[s["stimmung"]]
        wert = stark(i, pr["akzent"], "akzent")
        if s.get("rolle") == "hook" or pr["max_ruhe_s"] is None or wert <= 0:
            continue
        ruhe = pr["max_ruhe_s"]
        k = bisect.bisect_right(zoom_t, b)
        if (b - s["zeit_start"] < ruhe - 1e-6 or (k and b - zoom_t[k - 1] < ruhe - 1e-6)
                or (k < len(zoom_t) and zoom_t[k] - b < abstand - 1e-9)
                or b + AKZENT_S > s["zeit_ende"] - _r_hinten(segmente, i) + 1e-6 or _in_fenster(b, fenster)):
            continue
        ergebnis.append(_Plan("akzent", i, b, wert, dauer=AKZENT_S, rang=RANG_AKZENT))
        bisect.insort(zoom_t, b)
    return ergebnis


def _speichern(ereignisse: list[_Plan], segmente: list[dict]) -> None:
    """Ereignisse als Quellzeit ins Segment (Schema-Form); zu viele -> das Unwichtigste fällt weg."""
    je_segment: dict[int, list[_Plan]] = {}
    for z in ereignisse:
        je_segment.setdefault(z.i, []).append(z)
    for i, liste in je_segment.items():
        je_segment[i] = sorted(liste, key=lambda z: (-_wichtig(z), z.t))[:MAX_JE_SEGMENT]
    alle = sorted((z for liste in je_segment.values() for z in liste), key=lambda z: (-_wichtig(z), z.t, z.i))
    for z in sorted(alle[:MAX_JE_LISTE], key=lambda z: (z.i, z.t, z.art, z.klang or "")):
        s = segmente[z.i]
        eintrag: dict = {"art": z.art,
                         "t_s": round(min(max(auf_quelle(s, z.t), s["quelle_start_s"]), s["quelle_ende_s"]), 3),
                         "staerke": round(z.staerke, 3)}
        if z.dauer is not None:
            eintrag["dauer_s"] = round(max(0.0, min(TEXT_MAX_S, z.dauer)), 3)
        for feld in ("text", "zahl", "klang"):
            if getattr(z, feld) is not None:
                eintrag[feld] = getattr(z, feld)
        s.setdefault("effekte", []).append(eintrag)
