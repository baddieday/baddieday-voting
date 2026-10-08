"""Szenen (07.10., Florian: „Ich tippe 🥱 und sehe das gleiche Video mit anderen Schnitten“): Wann zeigen zwei Momente
dieselbe Spielszene?

Dieselbe Szene kann unter mehreren Schlüsseln liegen – Clip der Pipeline (clip:…), Nvidia-Highlight oder
SteelSeries-Moment (datei:…). Szene = Spielzeit-Fenster [start_utc, start_utc + (ende_s − start_s)] aus der Tabelle
momente (start_utc ist der Zeitpunkt von start_s). Zwei Momente zeigen dieselbe Szene, wenn sich ihre Fenster um
mindestens 3 s oder um mindestens 40 % des kürzeren überlappen. Nicht transitiv: nur direkte Nachbarn, sonst entstünden
Ketten über einen ganzen Abend.

Ausnahmen: ohne start_utc zählt nur der gleiche Schlüssel. Zwei Clips sind nie dieselbe Szene – die Pipeline legt je
Kill-Serie genau einen Clip an (nur der Puffer überlappt, bis 4,5 s, vorbewertung.anlauf_start), und zwei verschiedene
Matches können sich nicht überlappen (CI 07.10.: Testclips mit gleicher Uhrzeit schrumpften sonst einen Short auf eine
Szene). Doppelt sind also Aufnahmen ohne Clip (datei:…) gegenüber Clips oder einander.
Fail-Momente (nur 🔥 Viral) bleiben draußen, damit Viral unverändert bleibt.

Abwechslung mit Ermüdung (08.10., einfacher Modus, ersetzt „jede Szene nur in einem Video“): verlauf() sagt je Szene,
ob du sie schon gesehen hast, ob sie in einem deiner letzten Videos war (gesperrt) und in wie vielen Videos der letzten
Tage sie kam (Einsätze) – Fassungen eines Videos zählen als ein Video. Eine neue Fassung nach ❌ ersetzt ihr Video
(ersetzt_kette): dessen Szenen sind für sie frei und keine Wiederholung. Nichts wird gelöscht; alles folgt aus den
Entwürfen.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import timedelta

from .zeit import aus_iso, jetzt

UEBERLAPP_S = 3.0
UEBERLAPP_ANTEIL = 0.4

_LISTEN: dict[str, frozenset[str]] = {}   # Schnittliste -> Momente (Listen werden nur einmal geschrieben)


def index(con: sqlite3.Connection) -> dict[str, set[str]]:
    """Schlüssel -> Schlüssel derselben Szene (ohne sich selbst). Nur Momente mit Nachbarn stehen drin."""
    fenster = []
    for z in con.execute("SELECT schluessel, match_id, start_utc, start_s, ende_s FROM momente "
                         "WHERE start_utc IS NOT NULL AND schluessel NOT LIKE 'fail:%'"):
        try:
            t0 = aus_iso(z["start_utc"]).timestamp()
            laenge = max(0.0, float(z["ende_s"]) - float(z["start_s"] or 0.0))
        except (TypeError, ValueError):
            continue
        fenster.append((t0, t0 + laenge, z["schluessel"], z["match_id"]))
    fenster.sort()
    nachbarn: dict[str, set[str]] = {}
    for i, (a0, a1, a, a_match) in enumerate(fenster):
        for j in range(i + 1, len(fenster)):   # über den Index – eine Kopie der Restliste je Runde wäre quadratisch
            b0, b1, b, b_match = fenster[j]
            if b0 >= a1:
                break   # nach Beginn sortiert: alle weiteren beginnen noch später
            if a.startswith("clip:") and b.startswith("clip:"):
                continue   # zwei Clips: je Kill-Serie einer, und verschiedene Matches überlappen nie (sonst ein
                           # Uhrzeit-Fehler) – doppelt sind Aufnahmen ohne Clip (datei:…, Nvidia/SteelSeries)
            ueberlapp = min(a1, b1) - b0
            kuerzer = min(a1 - a0, b1 - b0)
            if ueberlapp >= UEBERLAPP_S or (kuerzer > 0 and ueberlapp >= UEBERLAPP_ANTEIL * kuerzer):
                nachbarn.setdefault(a, set()).add(b)
                nachbarn.setdefault(b, set()).add(a)
    return nachbarn


def erweitert(schluessel: Iterable[str], idx: dict[str, set[str]]) -> set[str]:
    """Die Schlüssel samt allen Schlüsseln derselben Szene."""
    ergebnis: set[str] = set()
    for s in schluessel:
        ergebnis |= {s, *idx.get(s, ())}
    return ergebnis


def eine_je_szene(kandidaten: list, idx: dict[str, set[str]], bevorzugt: Iterable[str] = ()) -> list:
    """Je Szene ein Kandidat (regie.Kandidat): zuerst die bevorzugten (🥱: die behaltenen Szenen), dann starke, dann
    die mit Clip, dann die mit den meisten Punkten. Fail-Momente bleiben unberührt; die Reihenfolge bleibt."""
    bevorzugt = set(bevorzugt)
    gewaehlt: set[str] = set()
    bleibt: set[int] = set()
    # Starke Szenen und Clips (Kill-Daten aus dem Replay) vor den Punkten: sonst gewänne ein lauter Datei-Zwilling
    # ohne Kills, flöge danach bei „nur starke Szenen“ raus – und die Szene fehlte ganz (Prüfung 07.10.)
    for k in sorted(kandidaten, key=lambda k: (k.schluessel not in bevorzugt, not getattr(k, "stark", False),
                                               k.clip_id is None, -k.punkte, k.schluessel)):
        if not k.fail and idx.get(k.schluessel, set()) & gewaehlt:
            continue
        gewaehlt.add(k.schluessel)
        bleibt.add(id(k))
    return [k for k in kandidaten if id(k) in bleibt]


def momente_der_liste(pfad: str | None) -> frozenset[str]:
    """Momente einer gespeicherten Schnittliste (leer, wenn die Datei fehlt) – je Pfad einmal gelesen."""
    if not pfad:
        return frozenset()
    if pfad not in _LISTEN:
        try:
            with open(pfad, encoding="utf-8") as f:
                segmente = json.load(f).get("segmente") or []
        except (OSError, ValueError, AttributeError, TypeError):
            return frozenset()   # nicht merken: fehlt sie nur kurz, zählt sie beim nächsten Mal
        _LISTEN[pfad] = frozenset(s["moment"] for s in segmente if isinstance(s, dict) and s.get("moment"))
    return _LISTEN[pfad]


def jemals_gezeigt(con: sqlite3.Connection) -> set[str]:
    """Momente, die du schon in einem Video gesehen hast – nur gesendete Entwürfe (still aussortierte oder nie fertig
    gerenderte hast du nie gesehen)."""
    gezeigt: set[str] = set()
    for z in con.execute("SELECT schnittliste FROM entwuerfe WHERE auto_verworfen IS NULL "
                         "AND status IN ('gesendet', 'bewertet')"):
        gezeigt |= momente_der_liste(z["schnittliste"])
    return gezeigt


# Videos, die du gesehen hast oder die gerade zu dir unterwegs sind: fertig gerendert, aber noch nicht verschickt, oder
# ein Abend-Video, dessen Rendern der Timer nachholt (sitzung._nachholen) – sonst nähme ein 🎬, das auf die
# Pipeline-Sperre wartete, dieselben Szenen wie das Abend-Video, das gleich danach kommt. Still aussortierte nie.
_GEZEIGT = """SELECT id, schnittliste, parameter, erstellt FROM entwuerfe WHERE auto_verworfen IS NULL
                AND (status IN ('gerendert', 'gesendet', 'bewertet')
                     OR id IN (SELECT entwurf_id FROM sitzungen WHERE entwurf_id IS NOT NULL))"""


def verbraucht(con: sqlite3.Connection) -> set[str]:
    """Momente aus allen Videos, die du gesehen hast oder die gerade zu dir unterwegs sind (_GEZEIGT) – roh, ohne die
    anderen Schlüssel derselben Szene (dafür erweitert)."""
    gesehen: set[str] = set()
    for z in con.execute(_GEZEIGT):
        gesehen |= momente_der_liste(z["schnittliste"])
    return gesehen


def _ersetzt(parameter: str | None) -> list[int]:
    """Die Videos, die ein Entwurf als neue Fassung ersetzt (Parameter „ersetzt“) – leer bei kaputten Parametern."""
    try:
        alt = json.loads(parameter or "{}").get("ersetzt") or []
    except (ValueError, TypeError, AttributeError):
        return []
    return [int(i) for i in alt if isinstance(i, int)] if isinstance(alt, list) else []


@dataclass
class Verlauf:
    """Was du schon gesehen hast (verlauf) – jede Menge je Szene erweitert (alle Schlüssel derselben Szene)."""
    gesehen: set[str] = field(default_factory=set)      # je in einem Video gesehen (auch im ersetzten)
    eigen: set[str] = field(default_factory=set)        # im Video, das die neue Fassung ersetzt (samt Vorgängern)
    gesperrt: set[str] = field(default_factory=set)     # in einem deiner letzten Videos (ohne das ersetzte, ohne eigen)
    einsaetze: dict[str, int] = field(default_factory=dict)          # Szene -> Videos in den letzten Tagen
    videos: dict[str, frozenset[int]] = field(default_factory=dict)  # Szene -> frühere Videos (für „zusammen“)

    def wiederholung(self, schluessel: str) -> bool:
        """Kennst du die Szene schon aus einem früheren Video? Die Szenen des ersetzten Videos zählen nicht."""
        return schluessel in self.gesehen and schluessel not in self.eigen


def verlauf(con: sqlite3.Connection, idx: dict[str, set[str]], *, ersetzt: Iterable[int] = (), tage: float = 30.0,
            sperre: int = 3) -> Verlauf:
    """Abwechslung mit Ermüdung (08.10., Florian: „die Momente dürfen ruhig öfter und gemischter genutzt werden … bessere
    öfters zeigen, aber nicht permanent“). Ein Video = alle Fassungen eines Videos (Familie: kleinste Nummer aus dem
    Entwurf und seinem Parameter „ersetzt“), gezählt werden Videos aus _GEZEIGT, das neueste nach seiner jüngsten
    Fassung. gesperrt = Szenen der letzten `sperre` Videos; einsaetze = in wie vielen Videos der letzten `tage` Tage
    die Szene war (unlesbare Zeit zählt als neu); videos = in welchen früheren Videos (Familien) sie war.
    ersetzt (neue Fassung nach ❌, ersetzt_kette): dieses Video zählt nirgends mit, seine Szenen sind eigen – frei und
    keine Wiederholung. Beispiel: Videos #1 (a, b), #2 (c), #3 = Fassung von #1 (a, d), sperre 1 → gesperrt {a, d},
    einsaetze a 1, b 1, c 1, d 1; mit ersetzt [3, 1]: eigen {a, b, d}, gesperrt {c}."""
    ersetzt = [int(i) for i in ersetzt]
    familie_von: dict[int, int] = {}
    familien: dict[int, dict] = {}
    for z in con.execute(_GEZEIGT):
        fam = min([int(z["id"]), *_ersetzt(z["parameter"])])
        familie_von[int(z["id"])] = fam
        f = familien.setdefault(fam, {"momente": set(), "neuester": 0, "zeit": None, "unlesbar": False})
        f["momente"] |= momente_der_liste(z["schnittliste"])
        f["neuester"] = max(f["neuester"], int(z["id"]))
        try:
            t = aus_iso(z["erstellt"])
            f["zeit"] = t if f["zeit"] is None else max(f["zeit"], t)
        except (TypeError, ValueError, AttributeError):
            f["unlesbar"] = True
    weg = {familie_von[i] for i in ersetzt if i in familie_von} | ({min(ersetzt)} if ersetzt else set())
    eigen_roh = momente_der_entwuerfe(con, ersetzt)
    for fam in weg & familien.keys():
        eigen_roh |= familien[fam]["momente"]
    v = Verlauf(eigen=erweitert(eigen_roh, idx))
    grenze = jetzt() - timedelta(days=float(tage))
    videos: dict[str, set[int]] = {}
    andere = sorted((fam for fam in familien if fam not in weg), key=lambda fam: -familien[fam]["neuester"])
    for rang, fam in enumerate(andere):
        f = familien[fam]
        szenen_ = erweitert(f["momente"], idx)
        if rang < int(sperre):
            v.gesperrt |= szenen_
        if f["unlesbar"] or f["zeit"] is None or f["zeit"] >= grenze:
            for s in szenen_:
                v.einsaetze[s] = v.einsaetze.get(s, 0) + 1
        for s in szenen_:
            videos.setdefault(s, set()).add(fam)
        v.gesehen |= szenen_
    v.gesehen |= v.eigen
    v.gesperrt -= v.eigen
    v.videos = {s: frozenset(fams) for s, fams in videos.items()}
    return v


def momente_der_entwuerfe(con: sqlite3.Connection, ids: Iterable[int]) -> set[str]:
    """Momente dieser Entwürfe (aus ihren Schnittlisten; fehlt eine, zählt sie leer)."""
    ids = sorted({int(i) for i in ids})
    momente: set[str] = set()
    if ids:
        for z in con.execute(f"SELECT schnittliste FROM entwuerfe WHERE id IN ({','.join('?' for _ in ids)})", ids):
            momente |= momente_der_liste(z["schnittliste"])
    return momente


def ersetzt_kette(con: sqlite3.Connection, entwurf_id: int) -> list[int]:
    """Die Videos, die eine neue Fassung von Video entwurf_id ersetzt: dieses und alle, die es selbst schon ersetzt hat
    (Parameter „ersetzt“ der Fassung). Ihre Szenen darf die Fassung wieder nehmen. Beispiel: #5 war die Fassung von #3
    → eine Fassung von #5 ersetzt [5, 3]."""
    z = con.execute("SELECT parameter FROM entwuerfe WHERE id = ?", (int(entwurf_id),)).fetchone()
    try:
        alt = (json.loads(z["parameter"] or "{}").get("ersetzt") or []) if z is not None else []
    except (ValueError, TypeError, AttributeError):
        alt = []   # kaputte Parameter: dann ersetzt sie nur dieses Video
    return [int(entwurf_id), *(i for i in alt if isinstance(i, int) and i != int(entwurf_id))]
