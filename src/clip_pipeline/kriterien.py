"""Cutter-Maßstab 1.0 (30.09., Spec §2): 14 Kriterien mit stetigen Kurven und 6 K.O.-Tore – rein.

Keine Datenbank, kein ffmpeg: Eingang sind die Schnittliste (was geplant war: Kills, Schnitte, Beats, Effekte), die
Messung am fertigen Video (messung.Messung, hier nur über Attribute gelesen) und das Render-Sidecar (geometrie:
b, h, band, spiel_anteile, normiert …). Fehlt die Messung ganz (m = None oder version 0), rechnet jedes Kriterium mit
Plan-Näherung, das es kann (quelle "plan"), die übrigen sind None; die Tore stehen dann auf None (kein Deckel).

Kurven sind Startwerte aus dem Handwerk (EW = Erfahrungswert, siehe Spec §2.1); gelernt werden nur die Gewichte
(massstab.py, Faktoren f_k). Setzt sich ein Kriterium aus Teilen zusammen und fehlt ein Teil, wird über die übrigen
renormiert („fehlt = unbekannt, nicht 0“). Die Note:
    M_f = 100 · Σ w0_k f_k t_k / Σ w0_k f_k   über alle Kriterien mit t_k ≠ None;  Tor verletzt → höchstens 40.

Beats: die Schnittliste kennt nur den Titel – der Aufrufer (kritik.bewerte) setzt liste["musik"]["beats"] =
json.loads(tracks.beats) (Musikzeit, vor Abzug von musik.start_s). Ohne Beats: beat_sync und ende.takt None.
Zeitraster der Ton-Reihen (M, S, Mv, Mm): 0,1 s ab 0 (Index i ↔ i·0,1 s), außer die Messung bringt t_ton mit.
"""

from __future__ import annotations

import bisect
import math
import statistics
from dataclasses import asdict, dataclass, field

from . import effekt_filter, effekte

FORMATE = ("short", "zusammenschnitt")
RASTER_S = 0.1            # Ton-Reihen und Leerlauf-Raster
DECKEL = 40.0             # Tor verletzt → Note höchstens so hoch
SCD_MIN = 8.0             # Szenenwechsel-Score ab hier (scdet)
SCD_ZUSAMMEN_S = 0.1      # scdet meldet 16,00/16,03 doppelt
SCHNITT_TOLERANZ_S = 0.1  # geplanter Schnitt ↔ gemessener Szenenwechsel
FREMD_TOLERANZ_S = 0.12   # C′: so weit weg von allem Geplanten
W_ZUSAMMEN_S = 0.25       # Bildwechsel näher beieinander zählen einmal
BEAT_TOLERANZ_S = 0.045   # Annahme A4
MOTIV_S = 0.15            # effektdosis: Anlass (Kill, Beat, Schnitt) so nah am Effekt
KILL_UMFELD_S = 1.5       # effektdosis.kon: Impulse um die Kills
GEMESSEN_MIN = 0.6        # darunter lernt massstab nicht aus dem Entwurf (Spec §3.3)
# Arten auf der Zeitleiste (effekte.zeitleiste)
IMPACTS = {"punch", "meme", "shake", "tilt", "flash", "strobe", "farbpop", "kontrast", "hue", "negativ", "blur",
           "pixel", "vignette", "rgb"}
BILD_EFFEKTE = {"flash", "strobe", "negativ", "hue", "blur", "pixel", "farbpop", "kontrast"}   # lösen scdet aus
W_STARTS = {"punch", "meme", "einzug", "tilt", "akzent"}   # bei Gaming-Shorts ist der Punch-in der Schnitt
SCHWARZ_BLENDEN = {"fade", "fadeblack"}
# Safe-Zonen je Plattform (oben, unten, links, rechts) als Anteil – bei mehreren gilt das Strengste (Spec §2.1, #13)
SAFE_ZONEN = {"tiktok": (0.10, 0.25, 0.04, 0.13), "youtube": (0.10, 0.25, 0.04, 0.13),
              "instagram": (0.14, 0.35, 0.04, 0.13)}
PLATTFORMEN = ("tiktok", "youtube")                       # Annahme A3

# gruppe, formate, name (Anzeige), befund-Vorlage, ki_maske (True: KI-Paare verschieben den Faktor nicht – die KI
# hört nichts, Spec §5.2)
KRITERIEN: dict[str, dict] = {
    "hook": {"gruppe": "Hook", "name": "Hook", "formate": FORMATE, "befund": "erste Aktion bei {} s",
             "ki_maske": False},
    "leerlauf": {"gruppe": "Pacing", "name": "Leerlauf", "formate": FORMATE, "befund": "{} ohne Aktion",
                 "ki_maske": False},
    "reiztakt": {"gruppe": "Pacing", "name": "Reiztakt", "formate": FORMATE, "befund": "alle {} s ein Bildwechsel",
                 "ki_maske": False},
    "tempokurve": {"gruppe": "Pacing", "name": "Tempokurve", "formate": FORMATE,
                   "befund": "Tempo zum Höhepunkt ×{}", "ki_maske": False},
    "cut_on_action": {"gruppe": "Pacing", "name": "Schnitt in Bewegung", "formate": FORMATE,
                      "befund": "{} der Schnitte in Bewegung", "ki_maske": False},
    "beat_sync": {"gruppe": "Pacing", "name": "Beat-Sync", "formate": FORMATE,
                  "befund": "{} der Schnitte auf dem Beat", "ki_maske": True},
    "payoff": {"gruppe": "Dramaturgie", "name": "Payoff", "formate": FORMATE, "befund": "Höhepunkt bei {} s",
               "ki_maske": False},
    "bogen": {"gruppe": "Dramaturgie", "name": "Spannungsbogen", "formate": FORMATE, "befund": "Tal {} s",
              "ki_maske": False},
    "ende": {"gruppe": "Dramaturgie", "name": "Ende", "formate": FORMATE, "befund": "Ende {}", "ki_maske": False},
    "tonmix": {"gruppe": "Ton", "name": "Tonmix", "formate": FORMATE, "befund": "{}", "ki_maske": True},
    "spielbild": {"gruppe": "Bild", "name": "Spielbild", "formate": ("short",), "befund": "Spielbild {} der Höhe",
                  "ki_maske": False},
    "bild_technik": {"gruppe": "Bild", "name": "Bildtechnik", "formate": FORMATE, "befund": "{}", "ki_maske": False},
    "lesbarkeit": {"gruppe": "Text", "name": "Lesbarkeit", "formate": FORMATE, "befund": "{}", "ki_maske": False},
    "effektdosis": {"gruppe": "Effekte", "name": "Effektdosis", "formate": FORMATE, "befund": "{}",
                    "ki_maske": False},
}

# Startgewichte w0 in Prozent (Short, Zusammenschnitt), Spec §2.1 – Summe je Format 100. Gruppen im Short:
# Hook 20 · Pacing 30 · Dramaturgie 17 · Ton 13 · Bild 11 · Effekte 7 · Text 2
_PROZENT = {"hook": (20, 8), "leerlauf": (11, 11), "reiztakt": (6, 6), "tempokurve": (5, 7),
            "cut_on_action": (3, 4), "beat_sync": (5, 7), "payoff": (9, 11), "bogen": (3, 9), "ende": (5, 5),
            "tonmix": (13, 15), "spielbild": (6, 0), "bild_technik": (5, 8), "lesbarkeit": (2, 2),
            "effektdosis": (7, 7)}
START: dict[str, dict[str, float]] = {fmt: {k: v[i] / 100 for k, v in _PROZENT.items() if v[i]}
                                      for i, fmt in enumerate(FORMATE)}


# --- Kurven -----------------------------------------------------------------------------------------------------

def auf(x: float, a: float, b: float) -> float:
    """0 bei x ≤ a, 1 bei x ≥ b, dazwischen linear."""
    if x <= a:
        return 0.0
    if x >= b:
        return 1.0
    return (x - a) / (b - a)


def ab(x: float, a: float, b: float) -> float:
    """1 bei x ≤ a, 0 bei x ≥ b, dazwischen linear."""
    return 1.0 - auf(x, a, b)


def trap(x: float, a: float, b: float, c: float, d: float) -> float:
    """Trapez: 0 bei x ≤ a oder x ≥ d, 1 in [b, c], dazwischen linear (d = ∞: nach oben offen)."""
    if x <= a:
        return 0.0
    if x < b:
        return (x - a) / (b - a)
    if x <= c or math.isinf(d):
        return 1.0
    if x >= d:
        return 0.0
    return (d - x) / (d - c)


def _mische(teile: list[tuple[float, float | None]]) -> float | None:
    """Gewichtetes Mittel über die vorhandenen Teile (renormiert); None, wenn keiner da ist."""
    da = [(g, w) for g, w in teile if w is not None]
    summe = sum(g for g, _ in da)
    return None if not da or summe <= 0 else sum(g * w for g, w in da) / summe


# --- Teilnote ---------------------------------------------------------------------------------------------------

@dataclass
class Teilnote:
    """Eine Teilnote 0..1 – gespeichert als {wert, quelle, roh, befund, zeit_s} (kritiken.teile)."""
    wert: float
    quelle: str = "video"          # video | plan
    roh: dict = field(default_factory=dict)
    befund: str | None = None
    zeit_s: float | None = None

    def als_dict(self) -> dict:
        return asdict(self)


def teilwert(x) -> float | None:
    """Teilnote, gespeichertes Dict oder Zahl → Wert 0..1 (None bleibt None)."""
    if x is None:
        return None
    if isinstance(x, (int, float)):
        w = float(x)
    elif isinstance(x, dict):
        w = x.get("wert")
    else:
        w = getattr(x, "wert", None)
    if w is None or not math.isfinite(float(w)):
        return None
    return max(0.0, min(1.0, float(w)))


def _feld(x, name: str):
    if isinstance(x, dict):
        return x.get(name)
    return getattr(x, name, None)


def _rund(roh: dict) -> dict:
    return {k: (round(v, 3) if isinstance(v, float) and math.isfinite(v) else v) for k, v in roh.items()
            if v is not None}


def _de(x: float, stellen: int = 1) -> str:
    """Zahl mit deutschem Komma: 2.9 → „2,9“."""
    return f"{x:.{stellen}f}".replace(".", ",")


def _proz(x: float) -> str:
    return f"{round(100 * x)} %"


def _mmss(t: float) -> str:
    t = max(0, int(round(t)))
    return f"{t // 60}:{t % 60:02d}"


# --- Messreihen (Duck-Typing auf messung.Messung) ---------------------------------------------------------------

def _ohne_messung(m) -> bool:
    return m is None or not getattr(m, "version", 1)


def _liste(x) -> list:
    return list(x) if x else []


def _endlich(werte) -> list[float]:
    return [float(v) for v in werte if v is not None and math.isfinite(float(v))]


def _ton_zeiten(m, reihe: list) -> list[float]:
    t = _liste(getattr(m, "t_ton", None))
    return t if len(t) == len(reihe) else [i * RASTER_S for i in range(len(reihe))]


def _ton_im(m, reihe: list | None, a: float, b: float) -> list[float]:
    """Werte einer Ton-Reihe mit Zeit in [a, b] (ohne −inf)."""
    reihe = _liste(reihe)
    return _endlich(v for t, v in zip(_ton_zeiten(m, reihe), reihe) if a - 1e-9 <= t <= b + 1e-9)


def _db(v) -> float:
    """Pegel als Zahl: fehlt oder −inf = −120 (Stille)."""
    return float(v) if v is not None and math.isfinite(float(v)) else -120.0


def _index(zeiten: list[float], t: float) -> int | None:
    """Index des Zeitpunkts, der t am nächsten liegt (zeiten aufsteigend)."""
    if not zeiten:
        return None
    i = bisect.bisect_left(zeiten, t)
    if i >= len(zeiten) or (i > 0 and t - zeiten[i - 1] <= zeiten[i] - t):
        i -= 1
    return i


def _ton_da(m) -> bool:
    return not _ohne_messung(m) and bool(getattr(m, "ton_da", True)) and bool(_liste(getattr(m, "m", None)))


def _bild_zeiten(m) -> list[float]:
    t = _liste(getattr(m, "t_bild", None))
    if t:
        return [float(x) for x in t]
    fps = float(getattr(m, "fps", 0) or 30.0)
    return [i / fps for i in range(len(_liste(getattr(m, "ydif_b", None))))]


def _fps(m) -> float:
    fps = float(getattr(m, "fps", 0) or 0)
    if fps > 0:
        return fps
    t = _bild_zeiten(m)
    return (len(t) - 1) / (t[-1] - t[0]) if len(t) > 1 and t[-1] > t[0] else 30.0


def _dauer(liste: dict, m) -> float:
    if not _ohne_messung(m) and getattr(m, "dauer_s", None):
        return float(m.dauer_s)
    segs = liste.get("segmente") or []
    return float(liste.get("dauer_s") or (segs[-1].get("zeit_ende", 0.0) if segs else 0.0) or 0.0)


def _ydif_norm(m, sperr: list[float]) -> list[float]:
    """ŶDIF: YDIF des Spielbild-Bands ÷ Median des Videos; Bilder ±1 um Schnitte/Blenden zählen nicht zum Median."""
    ydif = [float(v) if v is not None and math.isfinite(float(v)) else 0.0 for v in _liste(getattr(m, "ydif_b", None))]
    if not ydif:
        return []
    zeiten = _bild_zeiten(m)[:len(ydif)]
    rand = 1.5 / _fps(m)
    basis = [v for t, v in zip(zeiten, ydif) if not any(abs(t - s) <= rand for s in sperr)] or ydif
    median = statistics.median(basis)
    if median <= 1e-9:
        pos = [v for v in basis if v > 0]
        median = statistics.mean(pos) if pos else 1.0
    return [v / median for v in ydif]


def _zusammen(zeiten, abstand: float) -> list[float]:
    """Sortiert; Zeiten näher als abstand am vorigen Treffer fallen weg (der erste bleibt)."""
    ergebnis: list[float] = []
    for t in sorted(zeiten):
        if not ergebnis or t - ergebnis[-1] >= abstand - 1e-9:
            ergebnis.append(t)
    return ergebnis


def _nah(t: float, zeiten, toleranz: float) -> bool:
    return any(abs(t - z) <= toleranz + 1e-9 for z in zeiten)


# --- Ereignisse -------------------------------------------------------------------------------------------------

@dataclass
class Ereignisse:
    """Zeiten im fertigen Video (Spec §2.0). e: (Art, t, Dauer) der Impacts; fenster: (Art, von, bis) der Blenden."""
    c: list[float] = field(default_factory=list)          # harte Schnitte (gemessen, sonst Plan)
    ue: list[float] = field(default_factory=list)         # Blendenmitten
    c_fremd: list[float] = field(default_factory=list)    # ungeplante Szenenwechsel
    k: list[float] = field(default_factory=list)          # Kills (ohne Hook)
    t_p: float | None = None                              # Payoff
    e: list[tuple[str, float, float]] = field(default_factory=list)
    o: list[float] = field(default_factory=list)          # Ton-Onsets
    v: list[float] = field(default_factory=list)          # Bewegungsspitzen
    w: list[float] = field(default_factory=list)          # Bildwechsel
    beats: list[float] = field(default_factory=list)
    alle: list[float] = field(default_factory=list)
    fenster: list[tuple[str, float, float]] = field(default_factory=list)
    kills: list[tuple[float, float]] = field(default_factory=list)   # (t, Intensität des Segments)
    hook_kills: list[float] = field(default_factory=list)
    zeitleiste: list = field(default_factory=list)        # alle Ereignisse aus effekte.zeitleiste
    tempo: list[tuple[float, float]] = field(default_factory=list)   # Zeitlupen-Fenster auf der Zeitleiste
    ydif: list[float] = field(default_factory=list)       # ŶDIF je Bild (leer ohne Messung)
    c_aus_plan: bool = False


def _segmente(liste: dict) -> list[dict]:
    return [s for s in liste.get("segmente") or [] if "zeit_start" in s and "zeit_ende" in s]


def _uebergang(s: dict) -> dict:
    return s.get("uebergang") or {"art": "schnitt", "dauer_s": 0.0}


def kills_je_segment(liste: dict) -> list[tuple[dict, list[float]]]:
    """(Segment, Kill-Zeiten im Video) – dieselbe Regel wie kritik._kills (kill_s, ohne Anker die Muss-Spanne),
    hier aber mit Hook-Segmenten (der Aufrufer filtert)."""
    segs = _segmente(liste)
    mit_anker = any(s.get("kill_s") for s in segs if s.get("rolle") != "hook")
    ergebnis = []
    for s in segs:
        punkte = s.get("kill_s") or ([] if mit_anker or s.get("rolle") == "hook" else list(s.get("muss") or []))
        zeiten = []
        for k in punkte:
            if "quelle_start_s" in s and s["quelle_start_s"] - 1e-6 <= k <= s.get("quelle_ende_s", k) + 1e-6:
                zeiten.append(effekte.auf_zeitleiste(s, float(k)))
        ergebnis.append((s, zeiten))
    return ergebnis


def _beats(liste: dict, dauer: float) -> list[float]:
    musik = liste.get("musik") or {}
    roh = musik.get("beats")
    if not roh:
        return []
    start = float(musik.get("start_s") or 0.0)
    # bis 0,5 s über das Ende hinaus: ein Beat knapp NACH dem Ende zählt für „Ende auf dem Takt“ (Review 30.09.)
    return [round(float(b) - start, 3) for b in roh if 0.0 <= float(b) - start <= dauer + 0.5]


def ereignisse(liste: dict, m) -> Ereignisse:
    """Alle Ereignisse der Spec §2.0 aus Plan und (wenn da) Messung. m = None: nur Plan (O, V, C′ leer)."""
    ev = Ereignisse()
    segs = _segmente(liste)
    dauer = _dauer(liste, m)
    try:
        ev.fenster = [(art, a, b) for art, a, b, _ in effekte.uebergangs_fenster(liste)]
    except (KeyError, TypeError):
        ev.fenster = []
    ev.ue = [round((a + b) / 2, 3) for _, a, b in ev.fenster]
    c_plan = [float(s["zeit_start"]) for i, s in enumerate(segs) if i > 0 and _uebergang(s).get("art") == "schnitt"]
    try:
        ev.zeitleiste = effekte.zeitleiste(liste)
    except (KeyError, TypeError):
        ev.zeitleiste = []
    ev.e = [(e.art, e.t, e.dauer) for e in ev.zeitleiste if e.art in IMPACTS and e.staerke > 0]
    for s, zeiten in kills_je_segment(liste):
        if s.get("rolle") == "hook":
            ev.hook_kills += zeiten
        else:
            ev.kills += [(t, float(s.get("intensitaet") or 0.0)) for t in zeiten]
    ev.kills.sort()
    ev.k = [t for t, _ in ev.kills]
    # Payoff: letzter Kill im Segment mit der höchsten Intensität, bei Gleichstand das spätere
    bester = None
    for s, zeiten in kills_je_segment(liste):
        if s.get("rolle") != "hook" and zeiten:
            schluessel = (float(s.get("intensitaet") or 0.0), max(zeiten))
            if bester is None or schluessel >= bester:
                bester = schluessel
    ev.t_p = bester[1] if bester else None
    ev.beats = _beats(liste, dauer)
    for s in segs:
        if (lupe := s.get("lupe")) and "quelle_start_s" in s:
            ev.tempo.append((effekte.auf_zeitleiste(s, float(lupe["ab_s"])),
                             effekte.auf_zeitleiste(s, float(lupe["bis_s"]))))
    tempo_grenzen = []
    for s in segs:
        for w in (s.get("lupe"), s.get("raffer")):
            if w and "quelle_start_s" in s:
                tempo_grenzen += [effekte.auf_zeitleiste(s, float(w["ab_s"])),
                                  effekte.auf_zeitleiste(s, float(w["bis_s"]))]

    ev.c = list(c_plan)
    if not _ohne_messung(m):
        scd = _zusammen([float(t) for t, score in _liste(getattr(m, "scd", None)) if float(score) >= SCD_MIN],
                        SCD_ZUSAMMEN_S)
        gemessen = []
        for c in c_plan:
            nahe = [t for t in scd if abs(t - c) <= SCHNITT_TOLERANZ_S + 1e-9]
            gemessen.append(min(nahe, key=lambda t: abs(t - c)) if nahe else None)
        treffer = sum(1 for g in gemessen if g is not None)
        if c_plan and treffer < len(c_plan) / 2:
            ev.c_aus_plan = True
        else:
            ev.c = [g if g is not None else c for g, c in zip(gemessen, c_plan)]
        bild_fenster = [(t, t + d) for art, t, d in ((e.art, e.t, e.dauer) for e in ev.zeitleiste)
                        if art in BILD_EFFEKTE]
        geplant = [(c, c) for c in c_plan + ev.c] + [(a, b) for _, a, b in ev.fenster] + bild_fenster
        ev.c_fremd = [t for t in scd if not any(a - FREMD_TOLERANZ_S <= t <= b + FREMD_TOLERANZ_S for a, b in geplant)]
        ev.ydif = _ydif_norm(m, ev.c + ev.ue)
        ev.o = _onsets(m)
        ev.v = _spitzen(m, ev)
    starts = [e.t for e in ev.zeitleiste if e.art in W_STARTS and e.staerke > 0]
    ev.w = _zusammen(ev.c + ev.ue + ev.c_fremd + starts + tempo_grenzen, W_ZUSAMMEN_S)
    ev.alle = sorted(ev.c + ev.ue + ev.k + [t for _, t, _ in ev.e] + ev.o + ev.v)
    return ev


def _onsets(m) -> list[float]:
    """O: aus dem Vordergrund-Stem (sonst Mix) M(t) − min M(t−0,5…t−0,1) ≥ 6 LU und M(t) ≥ I − 6; ≥ 0,25 s Abstand."""
    reihe, i_ref = _liste(getattr(m, "mv", None)), getattr(m, "iv", None)
    if not reihe:
        if not _ton_da(m):
            return []
        reihe, i_ref = _liste(m.m), getattr(m, "i_lufs", None)
    zeiten = _ton_zeiten(m, reihe)
    werte = [_db(v) for v in reihe]
    treffer = []
    for i, (t, v) in enumerate(zip(zeiten, werte)):
        if t < 0.5:
            continue
        vorher = werte[bisect.bisect_left(zeiten, t - 0.5 - 1e-9):bisect.bisect_right(zeiten, t - 0.1 + 1e-9)]
        if vorher and v - min(vorher) >= 6.0 and (i_ref is None or v >= float(i_ref) - 6.0):
            if not treffer or t - treffer[-1] >= 0.25 - 1e-9:
                treffer.append(t)
    return treffer


def _spitzen(m, ev: Ereignisse) -> list[float]:
    """V: lokale Maxima mit ŶDIF ≥ 2,5, ≥ 0,3 s Abstand, nicht ±1 Bild um Schnitte/Blenden."""
    y, zeiten = ev.ydif, _bild_zeiten(m)
    rand = 1.5 / _fps(m)
    kandidaten = []
    for i in range(len(y)):
        if y[i] >= 2.5 and (i == 0 or y[i] >= y[i - 1]) and (i + 1 >= len(y) or y[i] >= y[i + 1]) \
                and i < len(zeiten) and not _nah(zeiten[i], ev.c + ev.ue, rand):
            kandidaten.append((y[i], zeiten[i]))
    gewaehlt: list[float] = []
    for _, t in sorted(kandidaten, reverse=True):      # stärkste zuerst, dann Mindestabstand
        if not _nah(t, gewaehlt, 0.3 - 1e-6):
            gewaehlt.append(t)
    return sorted(gewaehlt)


# --- Die 14 Kriterien -------------------------------------------------------------------------------------------

@dataclass
class _Lage:
    liste: dict
    m: object
    g: dict
    fmt: str
    ev: Ereignisse
    dauer: float

    @property
    def gemessen(self) -> bool:
        return not _ohne_messung(self.m)

    @property
    def quelle(self) -> str:
        return "video" if self.gemessen else "plan"

    def bild_im(self, reihe_name: str, a: float, b: float) -> list[float]:
        reihe = self.ev.ydif if reihe_name == "ydif" else _liste(getattr(self.m, reihe_name, None))
        return _endlich(v for t, v in zip(_bild_zeiten(self.m), reihe) if a - 1e-9 <= t <= b + 1e-9)


def _hook(x: _Lage) -> Teilnote | None:
    ev, kandidaten = x.ev, []
    if ev.k:
        kandidaten.append(ev.k[0])
    kandidaten += ev.hook_kills[:1]             # ein Hook-Teaser mit Kill ist Aktion ganz vorn
    if x.gemessen:
        kandidaten += [o for o in ev.o if _nah(o, ev.v, 0.2)][:1]
    t1 = min(kandidaten) if kandidaten else x.dauer
    b1 = s1 = None
    if x.gemessen and ev.ydif:
        schwarz_start = any(float(a) < 0.05 for a, _ in _liste(getattr(x.m, "schwarz", None)))
        werte = x.bild_im("ydif", 0.0, 1.0)
        b1 = 0.0 if schwarz_start else (statistics.mean(werte) if werte else None)   # Annahme: startet schwarz = keine Bewegung
    if _ton_da(x.m) and getattr(x.m, "i_lufs", None) is not None:
        werte = _ton_im(x.m, x.m.m, 0.4, 1.0)
        s1 = statistics.mean(werte) - float(x.m.i_lufs) if werte else None
    if x.fmt == "zusammenschnitt":
        wert = ab(t1, 2.0, 6.0)
    else:
        wert = _mische([(0.55, ab(t1, 1.0, 3.5)), (0.25, None if b1 is None else auf(b1, 0.3, 1.0)),
                        (0.20, None if s1 is None else auf(s1, -12.0, -3.0))])
    return Teilnote(wert, x.quelle, _rund({"t1": t1, "b1": b1, "s1": s1}),
                    KRITERIEN["hook"]["befund"].format(_de(t1)), t1)


def _luecke(zeiten: list[float], dauer: float) -> tuple[float, float, float]:
    """(größte Lücke, von, bis) zwischen Ereignissen, Anfang und Ende des Videos zählen als Grenze."""
    punkte = [0.0] + sorted(t for t in zeiten if 0.0 <= t <= dauer) + [dauer]
    return max(((b - a, a, b) for a, b in zip(punkte, punkte[1:])), default=(dauer, 0.0, dauer))


def _leerlauf(x: _Lage) -> Teilnote | None:
    if x.dauer <= 0:
        return None
    alle = x.ev.alle
    i_lufs = getattr(x.m, "i_lufs", None) if _ton_da(x.m) else None
    raster = [i * RASTER_S for i in range(int(x.dauer / RASTER_S) + 1)]
    ton = _liste(getattr(x.m, "m", None)) if i_lufs is not None else []
    ton_t = _ton_zeiten(x.m, ton) if ton else []
    bild_t = _bild_zeiten(x.m)[:len(x.ev.ydif)] if x.ev.ydif else []
    tot = 0
    for t in raster:
        leer = not _nah(t, alle, 1.5)
        if not leer and (bild_t or ton):
            ruhig = []
            if bild_t:
                a, b = bisect.bisect_left(bild_t, t - 0.05), bisect.bisect_left(bild_t, t + 0.05)
                werte = x.ev.ydif[a:b] or [x.ev.ydif[_index(bild_t, t)]]
                ruhig.append(statistics.mean(werte) < 0.5)
            if ton:
                i = _index(ton_t, t)
                ruhig.append(_db(ton[i]) < float(i_lufs) - 8.0)
            leer = all(ruhig)
        tot += leer
    anteil = tot / len(raster)
    g, von, bis = _luecke(alle, x.dauer)
    a_g, b_g = (4.0, 8.0) if x.fmt == "zusammenschnitt" else (3.0, 6.0)
    wert = min(ab(anteil, 0.05, 0.20), ab(g, a_g, b_g))
    return Teilnote(wert, x.quelle, _rund({"anteil": anteil, "luecke": g}),
                    KRITERIEN["leerlauf"]["befund"].format(f"{_mmss(von)}–{_mmss(bis)}"), von)


def _abstaende(w: list[float]) -> list[float]:
    return [b - a for a, b in zip(w, w[1:]) if b > a]


def _reiztakt(x: _Lage) -> Teilnote | None:
    d = _abstaende(x.ev.w)
    mitte = statistics.median(d) if d else x.dauer
    wert = trap(mitte, 0.8, 2.0, 5.0, 9.0) if x.fmt == "zusammenschnitt" else trap(mitte, 0.6, 1.5, 3.5, 7.0)
    return Teilnote(wert, x.quelle, _rund({"median_s": mitte, "wechsel": len(x.ev.w)}),
                    KRITERIEN["reiztakt"]["befund"].format(_de(mitte)))


def _autokorrelation(werte: list[float]) -> float | None:
    if len(werte) < 3:
        return None
    mittel = statistics.mean(werte)
    nenner = sum((v - mittel) ** 2 for v in werte)
    if nenner <= 1e-12:
        return None
    return sum((a - mittel) * (b - mittel) for a, b in zip(werte, werte[1:])) / nenner


def _tempokurve(x: _Lage) -> Teilnote | None:
    w, t_p = x.ev.w, x.ev.t_p
    paare = [(a, b - a) for a, b in zip(w, w[1:]) if b > a]
    if len(paare) < 5:
        return None
    d = [p[1] for p in paare]
    vorn = [dd for a, dd in paare if a < x.dauer / 3]
    v = atmer = None
    if t_p is not None:
        anlauf = [dd for a, dd in paare if t_p - 8.0 <= a < t_p]
        if anlauf and vorn and statistics.median(vorn) > 0:
            v = statistics.median(anlauf) / statistics.median(vorn)
        atmer = min([t for t in w if t > t_p + 1e-6] or [x.dauer]) - t_p
    mittel = statistics.mean(d)
    cv = statistics.pstdev(d) / mittel if mittel > 0 else None
    r1 = _autokorrelation([math.log(dd) for dd in d])
    wert = _mische([(0.40, None if v is None else trap(v, 0.2, 0.4, 0.85, 1.2)),
                    (0.25, None if cv is None else trap(cv, 0.1, 0.35, 0.8, 1.3)),
                    (0.10, None if r1 is None else auf(r1, -0.2, 0.3)),
                    (0.25, None if atmer is None else trap(atmer, 0.3, 0.8, 2.0, 3.5))])
    if wert is None:
        return None
    return Teilnote(wert, x.quelle, _rund({"v": v, "cv": cv, "r1": r1, "atmer": atmer}),
                    KRITERIEN["tempokurve"]["befund"].format(_de(v, 2)) if v is not None else None, t_p)


def _cut_on_action(x: _Lage) -> Teilnote | None:
    if not x.gemessen or not x.ev.c or not x.ev.ydif:
        return None
    zeiten, y = _bild_zeiten(x.m), x.ev.ydif
    werte = []
    for c in x.ev.c:
        i = _index(zeiten[:len(y)], c)                  # das Schnittbild
        nach = [y[j] for j in range(i + 1, min(len(y), i + 4))]
        vor = [y[j] for j in range(max(0, i - 3), i)]
        ton = _nah(c, x.ev.o, 0.1)
        bewegt_nach = ton or (bool(nach) and statistics.mean(nach) >= 0.8)
        bewegt_vor = ton or (bool(vor) and statistics.mean(vor) >= 0.8)
        werte.append(0.6 * bewegt_nach + 0.4 * bewegt_vor)
    q = statistics.mean(werte)
    return Teilnote(auf(q, 0.15, 0.60), "video", _rund({"q": q, "schnitte": len(x.ev.c)}),
                    KRITERIEN["cut_on_action"]["befund"].format(_proz(q)))


def _beat_sync(x: _Lage) -> Teilnote | None:
    if not x.liste.get("musik"):
        return None
    schnitte = x.ev.c + x.ev.ue
    if x.ev.beats and schnitte:
        fehler = [min(abs(t - b) for b in x.ev.beats) for t in schnitte]
        q = sum(1 for f in fehler if f <= BEAT_TOLERANZ_S + 1e-9) / len(fehler)
    elif not x.gemessen and x.ev.beats == [] and _segmente(x.liste):
        # Plan-Rückfall ohne Beats: der Planer meldet auf_beat je Segment (Behauptung des Plans, nur als Näherung)
        segs = _segmente(x.liste)[1:]
        if not segs:
            return None
        q = sum(1 for s in segs if s.get("auf_beat")) / len(segs)
    else:
        return None
    return Teilnote(auf(q, 0.25, 0.75), x.quelle, _rund({"q": q}), KRITERIEN["beat_sync"]["befund"].format(_proz(q)))


def _payoff(x: _Lage) -> Teilnote | None:
    t_p = x.ev.t_p
    if t_p is None or x.dauer <= 0:
        return None
    pos, rest = t_p / x.dauer, x.dauer - t_p
    hoerbar = None
    if _ton_da(x.m):
        s = _endlich(_liste(getattr(x.m, "s", None)))
        umfeld = _ton_im(x.m, getattr(x.m, "s", None), t_p - 1.0, t_p + 1.0)
        if s and umfeld:
            p90 = sorted(s)[min(len(s) - 1, int(0.9 * len(s)))]
            hoerbar = max(umfeld) >= p90 - 2.0
    faktor = 1.0 if hoerbar is None else 0.7 + 0.3 * hoerbar
    wert = min(trap(pos, 0.40, 0.70, 0.95, math.inf), ab(rest, 3.0, 8.0)) * faktor
    return Teilnote(wert, x.quelle, _rund({"pos": pos, "rest": rest, "hoerbar": hoerbar}),
                    KRITERIEN["payoff"]["befund"].format(_de(t_p)), t_p)


def _z(werte: list[float | None]) -> list[float] | None:
    da = [v for v in werte if v is not None]
    if len(da) < 2:
        return None
    mittel, sigma = statistics.mean(da), statistics.pstdev(da)
    return [0.0 if v is None or sigma <= 1e-12 else (v - mittel) / sigma for v in werte]


def _raenge(werte: list[float]) -> list[float]:
    ordnung = sorted(range(len(werte)), key=lambda i: werte[i])
    r = [0.0] * len(werte)
    i = 0
    while i < len(ordnung):
        j = i
        while j + 1 < len(ordnung) and werte[ordnung[j + 1]] == werte[ordnung[i]]:
            j += 1
        for k in range(i, j + 1):
            r[ordnung[k]] = (i + j) / 2
        i = j + 1
    return r


def _spearman(werte: list[float]) -> float | None:
    if len(werte) < 3:
        return None
    a, b = _raenge(werte), list(range(len(werte)))
    ma, mb = statistics.mean(a), statistics.mean(b)
    na = math.sqrt(sum((v - ma) ** 2 for v in a))
    nb = math.sqrt(sum((v - mb) ** 2 for v in b))
    if na <= 1e-12 or nb <= 1e-12:
        return 0.0
    return sum((p - ma) * (q - mb) for p, q in zip(a, b)) / (na * nb)


def _bogen(x: _Lage) -> Teilnote | None:
    fenster = int(x.dauer // 2.0)
    if fenster < 3:
        return None
    grenzen = [(2.0 * i, 2.0 * (i + 1)) for i in range(fenster)]
    teile = []
    if _ton_da(x.m):
        s = [(_ton_im(x.m, getattr(x.m, "s", None), a, b)) for a, b in grenzen]
        teile.append((0.4, _z([statistics.mean(v) if v else None for v in s])))
    if x.gemessen and x.ev.ydif:
        y = [x.bild_im("ydif", a, b) for a, b in grenzen]
        teile.append((0.3, _z([statistics.mean(v) if v else None for v in y])))
    kills = [sum(max(0.5, i) for t, i in x.ev.kills if a <= t < b) for a, b in grenzen]
    teile.append((0.3, _z(kills)))
    teile = [(g, z) for g, z in teile if z is not None]
    if not teile:
        return None
    summe = sum(g for g, _ in teile)
    j = [sum(g * z[i] for g, z in teile) / summe for i in range(fenster)]
    rho = _spearman(j)
    schwelle = min(j) + 0.4 * (max(j) - min(j))
    tal = lauf = 0
    for v in j:
        lauf = lauf + 1 if v < schwelle - 1e-12 else 0
        tal = max(tal, lauf)
    tal_s = 2.0 * tal
    if x.fmt == "zusammenschnitt":
        wert = _mische([(0.6, None if rho is None else auf(rho, -0.2, 0.4)), (0.4, ab(tal_s, 8.0, 20.0))])
    else:
        wert = _mische([(0.6, None if rho is None else auf(rho, -0.2, 0.4)), (0.4, ab(tal_s, 4.0, 10.0))])
    return Teilnote(wert, x.quelle, _rund({"rho": rho, "tal_s": tal_s}), KRITERIEN["bogen"]["befund"].format(_de(tal_s, 0)))


def _takt(x: _Lage) -> float | None:
    b = x.ev.beats
    if len(b) < 2:
        return None
    periode = statistics.median(_abstaende(b)) or 0.5
    return 1.0 if min(abs(x.dauer - t) for t in b) <= periode / 8 + 1e-9 else 0.2


def _ende(x: _Lage) -> Teilnote | None:
    takt = _takt(x)
    schwarz = [(float(a), float(b)) for a, b in _liste(getattr(x.m, "schwarz", None))] if x.gemessen else None
    am_ende = None if schwarz is None else [(a, b) for a, b in schwarz if b >= x.dauer - 0.1]
    i_lufs = getattr(x.m, "i_lufs", None) if _ton_da(x.m) else None
    roh: dict = {"takt": takt}
    if x.fmt == "zusammenschnitt":
        ausklang = None
        if i_lufs is not None:
            reihe = _liste(x.m.m)
            zeiten = _ton_zeiten(x.m, reihe)
            werte = [_db(v) for v in reihe]
            laut = [i for i, v in enumerate(werte) if v >= float(i_lufs) - 3.0]
            if laut:
                i0 = laut[-1]
                leise = next((i for i in range(i0 + 1, len(werte)) if werte[i] < float(i_lufs) - 20.0), None)
                ausklang = (zeiten[leise] if leise is not None else x.dauer) - zeiten[i0]
        blende = [(a, b) for art, a, b in x.ev.fenster if art in SCHWARZ_BLENDEN]
        ohne = None if am_ende is None else float(all(any(fa - 0.1 <= a and b <= fb + 0.1 for fa, fb in blende)
                                                      for a, b in am_ende))
        roh.update(ausklang=ausklang)
        wert = _mische([(0.5, takt), (0.3, None if ausklang is None else trap(ausklang, 0.0, 0.5, 2.5, 5.0)),
                        (0.2, ohne)])
        befund = "ohne Ausklang" if ausklang is not None and ausklang < 0.5 else None
    else:
        laut = None
        if i_lufs is not None:
            werte = _ton_im(x.m, x.m.m, x.dauer - 0.5, x.dauer)
            laut = statistics.mean(werte) - float(i_lufs) if werte else None
        naht = getattr(x.m, "naht", None) if x.gemessen else None
        naht = naht if naht is not None else (x.g or {}).get("naht")
        roh.update(laut=laut, naht=naht)
        wert = _mische([(0.40, None if laut is None else auf(laut, -15.0, -6.0)),
                        (0.15, None if am_ende is None else float(not am_ende)),
                        (0.25, takt), (0.20, None if naht is None else auf(float(naht), 0.15, 0.5))])
        befund = ("wird leise" if laut is not None and laut < -10 else "schwarz" if am_ende else
                  "nicht auf dem Takt" if takt is not None and takt < 1 else None)
    if wert is None:
        return None
    return Teilnote(wert, x.quelle, _rund(roh), KRITERIEN["ende"]["befund"].format(befund) if befund else None)


def _tonmix(x: _Lage) -> Teilnote | None:
    if not _ton_da(x.m):
        return None
    mv, mm, iv = _liste(getattr(x.m, "mv", None)), _liste(getattr(x.m, "mm", None)), getattr(x.m, "iv", None)
    abst_kill = abst_stimme = None
    if mv and mm and iv is not None and x.liste.get("musik"):
        n = min(len(mv), len(mm))
        zeiten = _ton_zeiten(x.m, mv)[:n]
        # (Zeit, Mv − Mm) wo der Vordergrund zählt; Musik still (−inf) = −120
        paare = [(t, float(a) - _db(b)) for t, a, b in zip(zeiten, mv[:n], mm[:n])
                 if a is not None and math.isfinite(float(a)) and float(a) >= float(iv) - 6.0]
        in_kill = [d for t, d in paare if _nah(t, x.ev.k, 0.4)]
        stimmen = [(float(s["zeit_start"]), float(s["zeit_ende"])) for s in _segmente(x.liste) if s.get("stimmen")]
        in_stimme = [d for t, d in paare if any(sa <= t <= se for sa, se in stimmen) and not _nah(t, x.ev.k, 0.4)]
        abst_kill = statistics.median(in_kill) if in_kill else None
        abst_stimme = statistics.median(in_stimme) if in_stimme else None
    lra = getattr(x.m, "lra", None)
    loecher = sum(max(0.0, float(b) - float(a)) for a, b in _liste(getattr(x.m, "stille", None)))
    wert = _mische([(0.25, None if abst_kill is None else auf(abst_kill, 0.0, 6.0)),
                    (0.20, None if abst_stimme is None else auf(abst_stimme, 3.0, 10.0)),
                    (0.25, None if lra is None else trap(float(lra), 1.0, 4.0, 10.0, 16.0)),
                    (0.30, ab(loecher, 0.0, 1.5))])
    befund = ("Kills unter der Musik" if abst_kill is not None and abst_kill < 3 else
              f"{_de(loecher)} s Tonlöcher" if loecher > 0.5 else None)
    return Teilnote(wert, "video", _rund({"abst_kill": abst_kill, "abst_stimme": abst_stimme, "lra": lra,
                                          "loecher": loecher}), befund)


def _masse(liste: dict, g: dict, fmt: str) -> tuple[int, int]:
    from .regie import FORMATE as REGIE_FORMATE   # spät: regie ist groß und braucht kriterien nicht

    if g.get("b") and g.get("h"):
        return int(g["b"]), int(g["h"])
    a = liste.get("aufloesung") or []
    if len(a) >= 2:
        return int(a[0]), int(a[1])
    f = REGIE_FORMATE.get(fmt) or REGIE_FORMATE["short"]
    return int(f["b"]), int(f["h"])


def _zoom(liste: dict) -> float:
    try:
        return max(1.0, min(1.6, float((liste.get("parameter") or {}).get("rahmen_zoom", 1.0) or 1.0)))
    except (TypeError, ValueError):
        return 1.0


def _spiel_h(liste: dict, g: dict, b: int, h: int) -> int:
    if g.get("spiel_h"):
        return int(g["spiel_h"])
    if g.get("band") and len(g["band"]) == 2:
        return int(g["band"][1]) - int(g["band"][0])
    return min(h, int(effekt_filter.spiel_hoehe(b, 16, 9) * _zoom(liste)))


def _spielbild(x: _Lage) -> Teilnote | None:
    if x.fmt != "short":
        return None
    segs, anteile = _segmente(x.liste), _liste(x.g.get("spiel_anteile"))
    if anteile and len(anteile) == len(segs):
        dauern = [max(0.0, float(s["zeit_ende"]) - float(s["zeit_start"])) for s in segs]
        anteil = sum(d * float(a) for d, a in zip(dauern, anteile)) / (sum(dauern) or 1.0)
        quelle = "video"
    else:
        b, h = _masse(x.liste, x.g, x.fmt)
        anteil = effekt_filter.spiel_hoehe(b, 16, 9) * _zoom(x.liste) / h
        quelle = "plan"
    return Teilnote(auf(anteil, 0.30, 0.55), quelle, _rund({"anteil": anteil}),
                    KRITERIEN["spielbild"]["befund"].format(_proz(anteil)))


def _bild_technik(x: _Lage) -> Teilnote | None:
    if not x.gemessen or not x.ev.ydif:
        return None
    zeiten, y = _bild_zeiten(x.m), x.ev.ydif
    in_lupe = [v for t, v in zip(zeiten, y) if any(a <= t <= b for a, b in x.ev.tempo)]
    sonst = [v for t, v in zip(zeiten, y) if not any(a <= t <= b for a, b in x.ev.tempo)]
    dopp = max([sum(1 for v in r if v < 0.05) / len(r) for r in (in_lupe, sonst) if r] or [0.0])
    stand_iv = getattr(x.m, "stand", None)
    stand = None if stand_iv is None else sum(1 for a, b in stand_iv if 0.5 <= float(b) - float(a) < 1.0)
    effekt_zeit = [(t, t + max(d, 0.1)) for _, t, d in x.ev.e]
    yavg = _liste(getattr(x.m, "yavg_b", None))
    licht = None
    if yavg:
        gut, vorher, n = 0, None, 0
        for s in _segmente(x.liste):
            werte = [float(v) for t, v in zip(zeiten, yavg) if float(s["zeit_start"]) <= t < float(s["zeit_ende"])
                     and not any(a <= t <= b for a, b in effekt_zeit) and v is not None]
            if not werte:
                continue
            mitte = statistics.median(werte)
            n += 1
            gut += 60 <= mitte <= 170 and (vorher is None or abs(mitte - vorher) <= 25)
            vorher = mitte
        licht = gut / n if n else None
    wert = _mische([(0.5, ab(dopp, 0.10, 0.50)), (0.2, None if stand is None else max(0.0, 1 - 0.34 * stand)),
                    (0.3, licht)])
    befund = (f"{_proz(dopp)} doppelte Bilder" if dopp > 0.2 else f"{stand} Standbilder" if stand else
              "Helligkeit springt" if licht is not None and licht < 0.7 else None)
    return Teilnote(wert, "video", _rund({"dopp": dopp, "stand": stand, "licht": licht}), befund)


def _texte(x: _Lage) -> list[tuple[str, float, float, float, float]]:
    """(Text, Standzeit, Mitte y, Schriftgröße, Zeichenzahl) – dieselben Lage-Rechnungen wie der Renderer."""
    b, h = _masse(x.liste, x.g, x.fmt)
    hoch = x.fmt == "short"
    zb = float(x.g.get("zeichenbreite") or 0.75)
    spiel_h = _spiel_h(x.liste, x.g, b, h) if hoch else None
    texte = []
    for e in x.ev.zeitleiste:
        if e.art == "titel" and e.text:
            mitte, groesse = effekt_filter.lage(b, h, hoch, len(e.text), zb, spiel_h)["titel"]
            texte.append((e.text, e.dauer, mitte, groesse))
        elif e.art == "zaehler" and e.zahl and hoch:
            text = f"KILLS {int(e.zahl)}"
            mitte, groesse = effekt_filter.lage(b, h, True, len(text), zb, spiel_h)["zaehler"]
            texte.append((text, e.dauer, mitte, groesse))
    if hoch and x.liste.get("overlay"):
        groesse = int(h * 0.03)                          # wie entwurf.filtergraph: y = 0,12·h (Oberkante)
        texte.append((str(x.liste["overlay"]), x.dauer, h * 0.12 + groesse / 2, groesse))
    return [(t, d, mi, gr, len(t)) for t, d, mi, gr in texte if gr >= effekt_filter.TEXT_MIN * h]


def _lesbarkeit(x: _Lage) -> Teilnote | None:
    texte = _texte(x)
    if not texte:
        return None
    b, h = _masse(x.liste, x.g, x.fmt)
    zb = float(x.g.get("zeichenbreite") or 0.75)
    zonen = [SAFE_ZONEN[p] for p in (x.g.get("plattformen") or PLATTFORMEN) if p in SAFE_ZONEN] or \
        [SAFE_ZONEN[p] for p in PLATTFORMEN]
    oben, unten, links, rechts = (max(z[i] for z in zonen) for i in range(4))
    noten, schlecht = [], None
    for text, stand, mitte, groesse, zeichen in texte:
        halb = effekt_filter.TINTE * groesse / 2 + effekt_filter.RAND * groesse
        breite = zeichen * zb * groesse
        safe = 1.0
        if x.fmt == "short":
            safe = float(mitte - halb >= oben * h and mitte + halb <= (1 - unten) * h
                         and (b - breite) / 2 >= links * b and (b + breite) / 2 <= (1 - rechts) * b)
        zps = zeichen / max(stand, 1e-3)
        note_ = min(auf(stand, 0.4, 0.83), ab(zps, 17.0, 25.0), auf(groesse / h, 0.015, 0.03), safe)
        noten.append(note_)
        if note_ < 0.5 and schlecht is None:
            schlecht = f"„{text}“ " + ("außerhalb der Safe-Zone" if not safe else f"nur {_de(stand)} s")
    return Teilnote(statistics.mean(noten), "plan", _rund({"texte": len(noten)}), schlecht)


def _impulse(x: _Lage) -> list[float]:
    """Gemessene Bild-Impulse (Helligkeits-/Sättigungssprung oder ŶDIF ≥ 3) ohne ±1 Bild um Schnitte/Blenden."""
    zeiten, y = _bild_zeiten(x.m), x.ev.ydif
    yv, sv = _liste(getattr(x.m, "yavg_v", None)), _liste(getattr(x.m, "sat_v", None))
    rand = 1.5 / _fps(x.m)
    treffer = []
    for i in range(1, len(zeiten)):
        t = zeiten[i]
        if _nah(t, x.ev.c, rand) or any(a - rand <= t <= b + rand for _, a, b in x.ev.fenster):
            continue
        sprung = (i < len(yv) and yv[i] is not None and yv[i - 1] is not None and abs(yv[i] - yv[i - 1]) >= 8) or \
                 (i < len(sv) and sv[i] is not None and sv[i - 1] is not None and abs(sv[i] - sv[i - 1]) >= 8) or \
                 (i < len(y) and y[i] >= 3.0)
        if sprung:
            treffer.append(t)
    return _zusammen(treffer, 0.1)


def _effektdosis(x: _Lage) -> Teilnote | None:
    if not (x.liste.get("effekte") or {}).get("an"):
        return None                                     # Effekte aus (⚙️): unbekannt, nicht geschenkt
    e = sorted(x.ev.e, key=lambda v: v[1])
    anlass = x.ev.k + x.ev.beats + x.ev.c
    mot = sum(1 for _, t, _ in e if _nah(t, anlass, MOTIV_S)) / len(e) if e else None
    impulse = _impulse(x) if x.gemessen and x.ev.ydif else _zusammen([t for art, t, _ in e], 0.1)
    kon = None
    if x.ev.k and x.dauer > 0:
        nah_k = [(max(0.0, k - KILL_UMFELD_S), min(x.dauer, k + KILL_UMFELD_S)) for k in x.ev.k]
        vereint: list[list[float]] = []
        for a, b in sorted(nah_k):
            if vereint and a <= vereint[-1][1]:
                vereint[-1][1] = max(vereint[-1][1], b)
            else:
                vereint.append([a, b])
        zeit_k = sum(b - a for a, b in vereint)
        in_k = sum(1 for t in impulse if any(a <= t <= b for a, b in vereint))
        rest = max(1e-6, x.dauer - zeit_k)
        kon = (in_k / max(zeit_k, 1e-6)) / max((len(impulse) - in_k) / rest, 0.05)
    dosis = len(impulse) / x.dauer * 10 if x.dauer > 0 else None
    wdh = sum(1 for a, b in zip(e, e[1:]) if a[0] == b[0])
    wert = _mische([(0.25, None if mot is None else auf(mot, 0.40, 0.85)),
                    (0.35, None if kon is None else auf(kon, 1.2, 3.0)),
                    (0.20, None if dosis is None else trap(dosis, 1.0, 3.0, 15.0, 30.0)),
                    (0.20, ab(wdh, 0.0, 3.0))])
    if wert is None:
        return None
    befund = (f"{wdh}× derselbe Effekt direkt nacheinander" if wdh >= 2 else
              "Effekte ohne Anlass" if mot is not None and mot < 0.4 else
              f"{_de(dosis, 0)} Impulse je 10 s" if dosis is not None and (dosis < 1 or dosis > 15) else None)
    return Teilnote(wert, x.quelle, _rund({"mot": mot, "kon": kon, "dosis": dosis, "wdh": wdh}), befund)


_RECHNER = {"hook": _hook, "leerlauf": _leerlauf, "reiztakt": _reiztakt, "tempokurve": _tempokurve,
            "cut_on_action": _cut_on_action, "beat_sync": _beat_sync, "payoff": _payoff, "bogen": _bogen,
            "ende": _ende, "tonmix": _tonmix, "spielbild": _spielbild, "bild_technik": _bild_technik,
            "lesbarkeit": _lesbarkeit, "effektdosis": _effektdosis}


def _format(liste: dict, fmt: str | None) -> str:
    fmt = fmt or liste.get("format") or "short"
    return fmt if fmt in FORMATE else "short"


def teilnoten(liste: dict, m, geometrie: dict | None, fmt: str | None = None) -> dict[str, Teilnote | None]:
    """Alle Kriterien des Formats → Teilnote oder None (nicht messbar). m = None (oder version 0): Plan-Näherung,
    wo es eine gibt (quelle "plan"). Wirft nie wegen fehlender Felder – ein Kriterium, das scheitert, ist None."""
    fmt = _format(liste, fmt)
    m = None if _ohne_messung(m) else m
    x = _Lage(liste, m, dict(geometrie or {}), fmt, ereignisse(liste, m), _dauer(liste, m))
    ergebnis: dict[str, Teilnote | None] = {}
    for k, info in KRITERIEN.items():
        if fmt not in info["formate"]:
            continue
        try:
            t = _RECHNER[k](x)
        except (KeyError, TypeError, ValueError, AttributeError, ZeroDivisionError, IndexError,
                statistics.StatisticsError):
            t = None                                     # ein kaputtes Feld kostet ein Kriterium, nie die Note
        if t is not None:
            t.wert = round(max(0.0, min(1.0, float(t.wert))), 3)
        ergebnis[k] = t
    return ergebnis


def plan_teile(liste: dict) -> dict[str, float]:
    """Plan-Näherung ohne Video (Rückfall, Vorhersage in S2): {Kriterium: Wert} nur für die Kriterien, die der Plan
    hergibt – stetig statt der alten Stufen 1,0/0,6/0,2 in kritik.regeln."""
    return {k: t.wert for k, t in teilnoten(liste, None, None).items() if t is not None}


# --- Tore -------------------------------------------------------------------------------------------------------

def _tor(ok: bool, text: str, wert=None) -> dict:
    return {"ok": bool(ok), "text": text, "wert": wert}


def _blitze(m) -> int | None:
    """Größte Blitz-Zahl in einem gleitenden 1-s-Fenster (WCAG 2.3.1) auf YAVG des Bands: L = ((Y−16)/219)^2,2,
    Übergang = |ΔL| ≥ 0,10 gegen das letzte Extrem, der dunklere Zustand < 0,8; Blitze = ⌊Übergänge/2⌋."""
    yavg = _liste(getattr(m, "yavg_b", None))
    if not yavg:
        return None
    zeiten = _bild_zeiten(m)
    uebergaenge: list[float] = []
    extrem, richtung = None, 0
    for t, y in zip(zeiten, yavg):
        if y is None:
            continue
        luma = max(0.0, min(1.0, (float(y) - 16) / 219)) ** 2.2
        if extrem is None or (richtung > 0 and luma > extrem) or (richtung < 0 and luma < extrem):
            extrem = luma                                # dieselbe Richtung: das Extrem wandert mit
        elif abs(luma - extrem) >= 0.10 and min(luma, extrem) < 0.8:
            uebergaenge.append(t)
            richtung = 1 if luma > extrem else -1
            extrem = luma
    beste, j = 0, 0
    for i, t in enumerate(uebergaenge):
        while uebergaenge[j] < t - 1.0 + 1e-9:
            j += 1
        beste = max(beste, (i - j + 1) // 2)
    return beste


def _hamming(a: int, b: int) -> int:
    return bin(int(a) ^ int(b)).count("1")


def _segment_bei(segs: list[dict], t: float) -> dict | None:
    return next((s for s in segs if float(s["zeit_start"]) <= t < float(s["zeit_ende"])), None)


def _doppelt(liste: dict, m) -> tuple[bool, str]:
    """G6: (a) Plan – Quellintervalle derselben Datei überlappen um > 1,5 s; (b) Bild – ≥ 3 Hash-Treffer in Folge
    mit gleichem Versatz ≥ 3 s aus verschiedenen Momenten desselben Matches (Nvidia und SteelSeries)."""
    segs = _segmente(liste)
    for i, a in enumerate(segs):
        for b in segs[i + 1:]:
            if a.get("datei") and a.get("datei") == b.get("datei") and "quelle_start_s" in a and "quelle_start_s" in b:
                ueber = min(a["quelle_ende_s"], b["quelle_ende_s"]) - max(a["quelle_start_s"], b["quelle_start_s"])
                if ueber > 1.5 + 1e-6:
                    return False, f"Moment doppelt: Segment {a.get('nr')} und {b.get('nr')} ({_de(ueber)} s)"
    hashes = _liste(getattr(m, "hash", None))
    rate = 2.0                                           # Hash-Bilder je Sekunde (Messdurchlauf fps=2)
    for versatz in range(int(3 * rate), len(hashes)):
        lauf = 0
        for i in range(len(hashes) - versatz):
            a, b = hashes[i], hashes[i + versatz]
            aussagekraeftig = 4 <= bin(int(a)).count("1") <= 60
            lauf = lauf + 1 if aussagekraeftig and _hamming(a, b) <= 6 else 0
            if lauf >= 3:
                sa, sb = _segment_bei(segs, i / rate), _segment_bei(segs, (i + versatz) / rate)
                if sa and sb and sa.get("rolle") != "hook" and sb.get("rolle") != "hook" \
                        and sa.get("moment") != sb.get("moment") and sa.get("match_id") \
                        and sa.get("match_id") == sb.get("match_id"):
                    return False, f"gleiches Bild bei {_mmss(i / rate)} und {_mmss((i + versatz) / rate)}"
    return True, ""


def tore(liste: dict, m, geometrie: dict | None) -> dict[str, dict | None]:
    """Sechs K.O.-Tore {"ok", "text", "wert"} – None, wenn nicht messbar (gilt nicht als Verstoß). Ohne Messung
    alle None: dann gibt es keinen Deckel (Spec §3.3)."""
    namen = ("schwarz", "standbild", "blitze", "ton", "technik", "doppelt")
    if _ohne_messung(m):
        return {n: None for n in namen}
    g = dict(geometrie or {})
    ergebnis: dict[str, dict | None] = {}
    try:
        fenster = [(a, b) for art, a, b, _ in effekte.uebergangs_fenster(liste) if art in SCHWARZ_BLENDEN]
    except (KeyError, TypeError):
        fenster = []
    schwarz = _liste(getattr(m, "schwarz", None))
    fremd = [(float(a), float(b)) for a, b in schwarz if float(b) - float(a) >= 0.1 - 1e-9
             and not any(fa - 0.1 <= float(a) and float(b) <= fb + 0.1 for fa, fb in fenster)]
    ergebnis["schwarz"] = _tor(not fremd, f"Schwarzbild {_mmss(fremd[0][0])} ({_de(fremd[0][1] - fremd[0][0])} s)"
                               if fremd else "", len(fremd))
    stand = getattr(m, "stand", None)
    if stand is None:
        ergebnis["standbild"] = None
    else:
        lang = [(float(a), float(b)) for a, b in stand if float(b) - float(a) >= 1.0 - 1e-9]
        ergebnis["standbild"] = _tor(not lang, f"Standbild {_mmss(lang[0][0])} ({_de(lang[0][1] - lang[0][0])} s)"
                                     if lang else "", len(lang))
    blitze = _blitze(m)
    ergebnis["blitze"] = None if blitze is None else _tor(blitze <= 3, f"Blitze {blitze}/s (Grenze 3)"
                                                          if blitze > 3 else "", blitze)
    i_lufs, tp = getattr(m, "i_lufs", None), getattr(m, "tp_db", None)
    if not g.get("normiert") or i_lufs is None or not _ton_da(m):
        ergebnis["ton"] = None
    else:
        ziel = float(g.get("ziel_lufs", -14.0))
        zu_laut = tp is not None and float(tp) > -0.5
        daneben = abs(float(i_lufs) - ziel) > 2.0
        ergebnis["ton"] = _tor(not (zu_laut or daneben), f"True Peak {_de(float(tp))} dBTP" if zu_laut else
                               f"Lautheit {_de(float(i_lufs))} LUFS (Ziel {_de(ziel, 0)})" if daneben else "",
                               round(float(i_lufs), 1))
    from .regie import DAUER_GRENZEN   # spät: regie ist groß und braucht kriterien nicht

    fmt = _format(liste, None)
    dauer = _dauer(liste, m)
    ton_d, bild_d = getattr(m, "dauer_ton", None), getattr(m, "dauer_bild", None)
    versatz = abs(float(ton_d) - float(bild_d)) if ton_d is not None and bild_d is not None else 0.0
    unten, oben = DAUER_GRENZEN.get(fmt, (0.0, math.inf))
    ausserhalb = not unten - 1e-6 <= dauer <= oben + 1e-6
    ergebnis["technik"] = _tor(versatz <= 0.2 and not ausserhalb,
                               f"Ton und Bild {_de(versatz)} s verschieden lang" if versatz > 0.2 else
                               f"Dauer {_de(dauer)} s außerhalb {_de(unten, 0)}–{_de(oben, 0)} s" if ausserhalb else "",
                               round(versatz, 3))
    ok, text = _doppelt(liste, m)
    ergebnis["doppelt"] = _tor(ok, text)
    return ergebnis


def tor_verletzt(tore_: dict | None) -> dict | None:
    """Das erste verletzte Tor (für Aussortieren und Bildunterschrift) oder None."""
    return next((t for t in (tore_ or {}).values() if t is not None and t.get("ok") is False), None)


# --- Note -------------------------------------------------------------------------------------------------------

def _gewichte(fmt: str, faktoren: dict | None, start: dict | None) -> dict[str, float]:
    w0 = (start or START).get(fmt) or START.get(fmt) or START["short"]
    return {k: float(w) * float((faktoren or {}).get(k, 1.0)) for k, w in w0.items() if w}


def note(teile: dict, tore: dict | None, fmt: str, faktoren: dict[str, float] | None, *,
         start: dict | None = None) -> float:
    """M_f = 100 · Σ w0 f t / Σ w0 f über die Kriterien mit Wert (fehlt = unbekannt); Tor verletzt → ≤ 40.
    Ohne jeden Wert 50 (nichts bekannt = Mitte). start: {Format: {k: w0}} aus der Konfig, sonst START."""
    gewichte = _gewichte(fmt, faktoren, start)
    zaehler = nenner = 0.0
    for k, w in gewichte.items():
        t = teilwert((teile or {}).get(k))
        if t is not None:
            zaehler += w * t
            nenner += w
    wert = 100.0 * zaehler / nenner if nenner > 0 else 50.0
    if tor_verletzt(tore):
        wert = min(wert, DECKEL)
    return round(wert, 1)


def verluste(teile: dict, fmt: str, faktoren: dict[str, float] | None, n: int = 2, *,
             start: dict | None = None) -> list[tuple[str, float, str]]:
    """Die n größten Verluste in Punkten (von 100): (Kriterium, Punkte, Befund) – für kritik_zeile."""
    gewichte = _gewichte(fmt, faktoren, start)
    da = {k: t for k in gewichte if (t := teilwert((teile or {}).get(k))) is not None}
    nenner = sum(gewichte[k] for k in da)
    if nenner <= 0:
        return []
    liste_ = [(k, round(100 * gewichte[k] * (1 - t) / nenner, 1), _feld(teile[k], "befund") or "")
              for k, t in da.items()]
    return sorted([v for v in liste_ if v[1] > 0], key=lambda v: (-v[1], v[0]))[:n]


def gemessen_anteil(teile: dict, fmt: str, *, start: dict | None = None) -> float:
    """Anteil des Startgewichts, der am Video gemessen ist (quelle "video"). Unter GEMESSEN_MIN lernt massstab
    nicht aus dem Entwurf (Spec §3.3)."""
    w0 = (start or START).get(fmt) or START["short"]
    gesamt = sum(w0.values())
    gemessen = sum(w for k, w in w0.items() if teilwert((teile or {}).get(k)) is not None
                   and _feld(teile[k], "quelle") == "video")
    return gemessen / gesamt if gesamt else 0.0


def als_json(teile: dict[str, Teilnote | None]) -> dict:
    """Für kritiken.teile: {k: {wert, quelle, roh, befund, zeit_s} | None}."""
    return {k: (t.als_dict() if isinstance(t, Teilnote) else t) for k, t in teile.items()}
