"""Cutter-Maßstab 1.0 lernt seine Gewichte selbst (30.09., Spec §5) – paarweise, linear, gedeckelt.

Die 14 Kriterien aus kriterien.py haben Startgewichte w0 aus dem Handwerk. Gelernt wird je Kriterium ein Faktor
f_k ∈ [0,5; 2] (u_k = ln f_k), gemeinsam für beide Formate. Jedes Paar „Entwurf a ist besser als b“ ist eine kleine
Prüfungsfrage; die Faktoren werden so verschoben, dass die Note M_f möglichst viele Fragen richtig beantwortet, und
ein Zug (λ) hält sie nah am Handwerk. Wie lernen.py und erwartung.py: bei jedem Aufruf komplett aus der Historie,
voller Batch, deterministisch.

Lehrer (Paare):
  - KI-Cutter  kritiken.ki_score, gleiche ki_version, gleiches Format, ≤ 14 Tage, |ΔK| ≥ 10. Still verworfene
               Entwürfe zählen mit (sonst wäre die Auswahl zensiert). Darf tonmix und beat_sync nicht verschieben –
               die KI hört nichts (Maske aus kriterien.KRITERIEN[k]["ki_maske"]).
  - du         👍 gegen 👎 im gleichen Format, ≤ 7 Tage (regie_lernen.bewertungen(mit_ki=False)); ein 👎 nur mit
               Inhalts-Gründen (Musik, getroffen, langweilig) sagt nichts über den Schnitt und bildet kein Paar.
  - Publikum   audience_ergebnisse.score über posts.entwurf_id, Crossposts confidence-gewichtet zusammengefasst,
               |Δy| ≥ 0,2, ≤ 30 Tage; Gewicht 2·√(c_a·c_b)·autonom.zeitgewicht(Alter).

Regeln gegen den Zirkelschluss (Spec §5.1) – bei jeder Erweiterung prüfen, der Test test_massstab bricht sonst:
  1. Der KI-Cutter bleibt blind: er sieht keine Teilnoten, keine Note und keine Messwerte (kritik.plan ohne auf_beat).
  2. massstab.lerne liest nur die Lehrer-Spalten (ki_score, ki_version, entwurf_bewertungen, audience_ergebnisse) –
     nie kritiken.score, regel_score, daumen, erwartungen.* oder entwuerfe.auto_verworfen. Sonst lernte der Maßstab
     aus seiner eigenen Note (heute gilt score = (regel + ki)/2).
  3. Entscheidungen (Schwelle, Stilwahl, Best-of-N) lesen f, schreiben aber nie in Lehrer-Tabellen.
Die Teilnoten kommen auch nicht in lern_features.extrahiere – sonst arbeiten zwei Modelle auf denselben Merkmalen
gegeneinander.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import sqlite3
import types
from dataclasses import dataclass

from . import autonom, kriterien, lernen, regie_lernen
from .erwartung import _sigma
from .konfig import Konfig
from .zeit import aus_iso, iso, jetzt

log = logging.getLogger("pipeline")

VERSION = 1                 # steigt, wenn sich das Verfahren ändert (geht in den input_hash)
QUELLEN = ("ki", "du", "publikum")
RANG = ("publikum", "du", "ki")       # Holdout-Schranke: die ranghöchste Quelle mit genug Paaren
HOLDOUT_MIN = 10            # so viele Paare braucht eine Quelle für die Holdout-Schranke
DU_FENSTER_TAGE = 7
DU_MAX_PAARE = 400          # Deckel für die Rechenzeit (Round-Robin verteilt die Paare über alle 👍)
KI_NACHBARN = 8             # je Entwurf höchstens so viele spätere Partner (hält die Paarzahl linear)
KI_PRUEF_PAARE = 8          # ab so vielen Publikumspaaren mit KI-Note auf beiden Seiten wird der KI-Cutter geprüft
GEMESSEN_MIN = kriterien.GEMESSEN_MIN

# Standard wie config/pipeline.toml [regie.massstab]
STANDARD = {"mindestens": 15, "voll_vertrauen": 60, "faktor_min": 0.5, "faktor_max": 2.0, "beta": 8.0, "l2": 4.0,
            "schritte": 400, "lernrate": 0.05, "ki_abstand": 10.0, "ki_fenster_tage": 14.0, "ki_budget": 40.0,
            "publikum_abstand": 0.2, "publikum_fenster_tage": 30.0, "publikum_halb": 8.0}


@dataclass
class MassPaar(lernen.Paar):
    """Ein Paar besser ≻ schlechter mit den Teilnoten beider Entwürfe (lernen.Paar: besser, schlechter, art, gewicht)."""
    fmt: str = "short"
    zeit: str = ""                       # der jüngere der beiden (Holdout: jüngste zuletzt)
    maske: frozenset = frozenset()       # Kriterien, die dieses Paar nicht verschieben darf
    ids: tuple = ()


def einstellungen(konfig: Konfig | None) -> dict:
    werte = dict(STANDARD)
    for k in STANDARD:
        wert = konfig.wert(f"regie.massstab.{k}") if konfig is not None else None
        if wert is not None:
            werte[k] = float(wert)
    return werte


def start_gewichte(konfig: Konfig | None) -> dict[str, dict[str, float]]:
    """{Format: {Kriterium: w0}} aus [regie.massstab.start.<format>] (Prozent), auf Summe 1 normiert; fehlt der
    Abschnitt oder ist er unbrauchbar, gilt kriterien.START."""
    ergebnis = {}
    for fmt, standard in kriterien.START.items():
        roh = konfig.wert(f"regie.massstab.start.{fmt}") if konfig is not None else None
        werte = {k: float(v) for k, v in (roh or {}).items()
                 if k in kriterien.KRITERIEN and fmt in kriterien.KRITERIEN[k]["formate"]
                 and isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0} if isinstance(roh, dict) else {}
        summe = sum(werte.values())
        ergebnis[fmt] = {k: v / summe for k, v in werte.items()} if summe > 0 else dict(standard)
    return ergebnis


# --- Daten ------------------------------------------------------------------------------------------------------

def _spalten(con: sqlite3.Connection, tabelle: str) -> set[str]:
    return {z["name"] for z in con.execute(f"PRAGMA table_info({tabelle})")}


def _entwuerfe(con: sqlite3.Connection, start: dict) -> dict[int, dict]:
    """Entwürfe mit Teilnoten, die überwiegend am Video gemessen sind (≥ 60 % des Startgewichts, Spec §3.3).
    Liest aus kritiken NUR teile, ki_score und ki_version (Regel 2) – nie score, regel_score oder daumen."""
    spalten = _spalten(con, "kritiken")
    if "teile" not in spalten:
        return {}
    version = "k.ki_version" if "ki_version" in spalten else "NULL"
    zeilen = con.execute(f"""SELECT e.id AS id, e.format AS format, e.erstellt AS erstellt, k.teile AS teile,
                                    k.ki_score AS ki_score, {version} AS ki_version
                               FROM kritiken k JOIN entwuerfe e ON e.id = k.entwurf_id
                              WHERE k.teile IS NOT NULL ORDER BY e.erstellt, e.id""").fetchall()
    ergebnis = {}
    for z in zeilen:
        try:
            teile = json.loads(z["teile"] or "{}")
        except json.JSONDecodeError:
            continue
        fmt = z["format"] if z["format"] in start else "short"
        if not isinstance(teile, dict) or kriterien.gemessen_anteil(teile, fmt, start=start) < GEMESSEN_MIN:
            continue
        ergebnis[int(z["id"])] = {
            "id": int(z["id"]), "fmt": fmt, "zeit": z["erstellt"],
            "t": {k: w for k in kriterien.KRITERIEN if (w := kriterien.teilwert(teile.get(k))) is not None},
            "k": None if z["ki_score"] is None else float(z["ki_score"]), "ki_version": z["ki_version"] or ""}
    return ergebnis


def _tage(a: str, b: str) -> float:
    return abs((aus_iso(a) - aus_iso(b)).total_seconds()) / 86400


def _paar(a: dict, b: dict, art: str, gewicht: float, maske: frozenset = frozenset()) -> MassPaar:
    return MassPaar(dict(a["t"]), dict(b["t"]), art, gewicht, fmt=a["fmt"], zeit=max(a["zeit"], b["zeit"]),
                    maske=maske, ids=(a["id"], b["id"]))


def _ki_paare(entwuerfe: dict[int, dict], e: dict) -> list[MassPaar]:
    maske = frozenset(k for k, v in kriterien.KRITERIEN.items() if v["ki_maske"])
    gruppen: dict[tuple, list[dict]] = {}
    for r in entwuerfe.values():
        if r["k"] is not None:
            gruppen.setdefault((r["fmt"], r["ki_version"]), []).append(r)
    paare = []
    for reihe in gruppen.values():
        reihe.sort(key=lambda r: (r["zeit"], r["id"]))
        for i, a in enumerate(reihe):
            for b in reihe[i + 1:i + 1 + KI_NACHBARN]:
                if _tage(a["zeit"], b["zeit"]) > e["ki_fenster_tage"]:
                    break
                if abs(a["k"] - b["k"]) >= e["ki_abstand"]:
                    gut, schlecht = (a, b) if a["k"] > b["k"] else (b, a)
                    paare.append(_paar(gut, schlecht, "ki", 1.0, maske))
    return paare


def _du_paare(con: sqlite3.Connection, entwuerfe: dict[int, dict]) -> list[MassPaar]:
    gut: dict[str, list[dict]] = {}
    schlecht: dict[str, list[dict]] = {}
    for z in regie_lernen.bewertungen(con, mit_ki=False):
        r = entwuerfe.get(int(z["entwurf_id"]))
        if r is None:
            continue
        gruende = set(json.loads(z["gruende"] or "[]"))
        if int(z["daumen"]) > 0:
            gut.setdefault(r["fmt"], []).append(r)
        elif not (gruende and gruende <= regie_lernen.INHALT_GRUENDE):   # 👎 nur wegen Inhalt: kein Schnitt-Urteil
            schlecht.setdefault(r["fmt"], []).append(r)
    paare = []
    for fmt, g in gut.items():
        s = schlecht.get(fmt, [])
        for a, b in lernen._round_robin(g, s, len(g) * len(s)):
            if _tage(a["zeit"], b["zeit"]) <= DU_FENSTER_TAGE:
                paare.append(_paar(a, b, "du", 1.0))
            if len(paare) >= DU_MAX_PAARE:
                return paare
    return paare


def _publikum(con: sqlite3.Connection, entwuerfe: dict[int, dict]) -> dict[int, dict]:
    """Entwurf → {y, c, zeit}: Crossposts sind EIN Video, confidence-gewichtet zusammengefasst (wie autonom)."""
    zeilen = con.execute("""SELECT p.entwurf_id AS entwurf_id, p.gepostet_utc AS zeit, a.score AS y,
                                   a.confidence AS c
                              FROM posts p JOIN audience_ergebnisse a ON a.post_id = p.id
                             WHERE p.art = 'entwurf' AND p.entwurf_id IS NOT NULL
                             ORDER BY p.gepostet_utc, p.id""").fetchall()
    je: dict[int, list] = {}
    for z in zeilen:
        if int(z["entwurf_id"]) in entwuerfe and z["y"] is not None and (z["c"] or 0) > 0:
            je.setdefault(int(z["entwurf_id"]), []).append(z)
    ergebnis = {}
    for eid, posts in je.items():
        c = sum(float(p["c"]) for p in posts)
        ergebnis[eid] = {"y": sum(float(p["y"]) * float(p["c"]) for p in posts) / c, "c": c / len(posts),
                         "zeit": min(p["zeit"] for p in posts)}
    return ergebnis


def _publikum_paare(entwuerfe: dict[int, dict], pub: dict[int, dict], e: dict) -> list[MassPaar]:
    jetzt_ = iso(jetzt())
    reihe = sorted(pub, key=lambda i: (pub[i]["zeit"], i))
    paare = []
    for n, i in enumerate(reihe):
        for j in reihe[n + 1:]:
            a, b = pub[i], pub[j]
            if _tage(a["zeit"], b["zeit"]) > e["publikum_fenster_tage"]:
                break
            if entwuerfe[i]["fmt"] != entwuerfe[j]["fmt"] or abs(a["y"] - b["y"]) < e["publikum_abstand"]:
                continue
            gut, schlecht = (i, j) if a["y"] > b["y"] else (j, i)
            alter = _tage(jetzt_, min(a["zeit"], b["zeit"]))
            g = 2 * math.sqrt(a["c"] * b["c"]) * autonom.zeitgewicht(alter)
            paare.append(_paar(entwuerfe[gut], entwuerfe[schlecht], "publikum", g))
    return paare


def _sammle(con: sqlite3.Connection, konfig: Konfig | None) -> tuple[list[MassPaar], dict]:
    """Alle Paare mit ihren Gewichten (Spec §5.2) und die Kennzahlen dazu."""
    e, start = einstellungen(konfig), start_gewichte(konfig)
    entwuerfe = _entwuerfe(con, start)
    pub = _publikum(con, entwuerfe)
    pub_paare = _publikum_paare(entwuerfe, pub, e)
    pi = len(pub) / (len(pub) + e["publikum_halb"])
    # Trifft der KI-Cutter das Publikum? Erst ab KI_PRUEF_PAARE Paaren mit KI-Note auf beiden Seiten
    pruef = [(entwuerfe[p.ids[0]]["k"], entwuerfe[p.ids[1]]["k"]) for p in pub_paare
             if entwuerfe[p.ids[0]]["k"] is not None and entwuerfe[p.ids[1]]["k"] is not None]
    q_ki = None
    a_ki = 1.0
    if len(pruef) >= KI_PRUEF_PAARE:
        q_ki = sum(1.0 if a > b else 0.5 if a == b else 0.0 for a, b in pruef) / len(pruef)
        a_ki = max(0.0, min(1.0, (q_ki - 0.5) / 0.2))
    ki = _ki_paare(entwuerfe, e)
    g_ki = 0.5 * (1 - pi) * a_ki * min(1.0, e["ki_budget"] / len(ki)) if ki else 0.0
    for p in ki:
        p.gewicht = g_ki
    du = _du_paare(con, entwuerfe)
    paare = [p for p in ki if p.gewicht > 0] + du + pub_paare
    meta = {"einstellungen": e, "start": start, "pi": pi, "a_ki": a_ki, "q_ki": q_ki, "ki_pruef": len(pruef),
            "je_quelle": {"ki": len(ki), "du": len(du), "publikum": len(pub_paare)}, "n_pub": len(pub)}
    return paare, meta


def paare(con: sqlite3.Connection, konfig: Konfig | None) -> list[MassPaar]:
    """Alle Lern-Paare (KI, du, Publikum) mit Gewicht – ohne zu trainieren."""
    return _sammle(con, konfig)[0]


# --- Verfahren --------------------------------------------------------------------------------------------------

def _vorbereiten(paare_: list[MassPaar], start: dict) -> list[tuple[float, list[tuple]]]:
    """(g, [(k, w0, t_a, t_b, maskiert)]) über die beidseitig gemessenen Kriterien des Formats."""
    ergebnis = []
    for p in paare_:
        w0 = start.get(p.fmt) or start["short"]
        s = [(k, w0[k], p.besser[k], p.schlechter[k], k in p.maske) for k in kriterien.KRITERIEN
             if w0.get(k) and p.besser.get(k) is not None and p.schlechter.get(k) is not None]
        if s and p.gewicht > 0:
            ergebnis.append((p.gewicht, s))
    return ergebnis


def _abstand(s: list[tuple], u: dict[str, float]) -> tuple[float, float, float, float]:
    """(d, m_a, m_b, D): Notenabstand des Paars mit den Faktoren e^u (nur über S)."""
    d_summe = a = b = 0.0
    for k, w0, ta, tb, _ in s:
        w = w0 * math.exp(u[k])
        d_summe += w
        a += w * ta
        b += w * tb
    return (a - b) / d_summe, a / d_summe, b / d_summe, d_summe


def _trainiere(paare_: list[MassPaar], start: dict, e: dict) -> dict[str, float]:
    """u = ln f per Gradientenabstieg auf Σ g·ln(1 + e^(−β d)) + λ Σ u² (Spec §5.3), u in [ln f_min, ln f_max].
    Die Schrittweite schrumpft mit der Paar-Masse Σ g (Verlust als Summe, nicht Mittel) – sonst spränge der volle
    Batch bei vielen Paaren zwischen den Schranken hin und her."""
    daten = _vorbereiten(paare_, start)
    u = {k: 0.0 for k in kriterien.KRITERIEN}
    if not daten:
        return u
    unten, oben = math.log(e["faktor_min"]), math.log(e["faktor_max"])
    beta, l2 = e["beta"], e["l2"]
    eta = e["lernrate"] / max(1.0, sum(g for g, _ in daten) / 20.0)
    for _ in range(int(e["schritte"])):
        grad = {k: 2 * l2 * v for k, v in u.items()}
        for g, s in daten:
            d, ma, mb, d_summe = _abstand(s, u)
            faktor = g * -beta * _sigma(-beta * d) / d_summe
            for k, w0, ta, tb, maskiert in s:
                if not maskiert:            # KI-Maske: Gradient 0 – die KI hört nichts
                    grad[k] += faktor * w0 * math.exp(u[k]) * ((ta - ma) - (tb - mb))
        u = {k: min(oben, max(unten, u[k] - eta * grad[k])) for k in u}
    return u


def _quote(paare_: list[MassPaar], u: dict[str, float], start: dict) -> float | None:
    """Anteil richtig sortierter Paare (Gleichstand zählt halb), ungewichtet wie lernen.trefferquote."""
    daten = _vorbereiten(paare_, start)
    if not daten:
        return None
    treffer = 0.0
    for _, s in daten:
        d = _abstand(s, u)[0]
        treffer += 1.0 if d > 1e-9 else 0.5 if d > -1e-9 else 0.0
    return treffer / len(daten)


def _hash(paare_: list[MassPaar], meta: dict) -> str:
    daten = {"version": VERSION, "einstellungen": meta["einstellungen"], "start": meta["start"],
             "paare": [[p.art, p.fmt, round(p.gewicht, 3), sorted((k, round(v, 3)) for k, v in p.besser.items()),
                        sorted((k, round(v, 3)) for k, v in p.schlechter.items()), sorted(p.maske)]
                       for p in paare_]}
    return hashlib.sha256(json.dumps(daten, sort_keys=True).encode()).hexdigest()


def _eins() -> dict[str, float]:
    return {k: 1.0 for k in kriterien.KRITERIEN}


def _lerne(paare_: list[MassPaar], meta: dict) -> dict:
    e, start = meta["einstellungen"], meta["start"]
    ids = {i for p in paare_ for i in p.ids}
    n = len(ids)
    vertrauen = min(1.0, n / max(1.0, e["voll_vertrauen"]))
    ergebnis = {"faktoren": _eins(), "gelernt": _eins(), "start": start, "paare": dict(meta["je_quelle"]),
                "datenbasis": n, "vertrauen": round(vertrauen, 3), "publikum_anteil": round(meta["pi"], 3),
                "ki_lehrer": round(meta["a_ki"], 3), "ki_treffer_publikum": meta["q_ki"], "quote_holdout": None,
                "quote_holdout_start": None, "holdout_quelle": None, "aktiv": False,
                "mindestens": int(e["mindestens"]), "voll_vertrauen": int(e["voll_vertrauen"]),
                "ki_pruef": meta["ki_pruef"], "input_hash": _hash(paare_, meta)}
    ergebnis["paare"]["ki_publikum"] = meta["ki_pruef"]
    if n < e["mindestens"]:
        ergebnis["grund"] = f"noch {int(e['mindestens']) - n} Entwürfe bis zum Lernen"
        return ergebnis
    u = _trainiere(paare_, start, e)
    ergebnis["gelernt"] = {k: round(math.exp(v), 4) for k, v in u.items()}
    # Schranke „nie schlechter als das Handwerk“: Holdout = die jüngsten 20 % der ranghöchsten Quelle mit genug Paaren
    quelle = next((q for q in RANG if meta["je_quelle"].get(q, 0) >= HOLDOUT_MIN), None)
    if quelle:
        eigene = sorted((p for p in paare_ if p.art == quelle), key=lambda p: (p.zeit, p.ids))
        _, holdout = lernen._holdout(eigene)
        if holdout:
            weg = {id(p) for p in holdout}
            u_zug = _trainiere([p for p in paare_ if id(p) not in weg], start, e)
            q = _quote(holdout, {k: vertrauen * v for k, v in u_zug.items()}, start)
            q0 = _quote(holdout, {k: 0.0 for k in u}, start)
            ergebnis.update(quote_holdout=q, quote_holdout_start=q0, holdout_quelle=quelle)
            if q is not None and q0 is not None and q < q0:
                name = {"publikum": "das Publikum", "du": "dein Urteil", "ki": "den KI-Cutter"}[quelle]
                ergebnis["grund"] = f"sortiert {name} schlechter als der Handwerks-Start"
                return ergebnis
    ergebnis["faktoren"] = {k: round(math.exp(vertrauen * v), 4) for k, v in u.items()}
    ergebnis.update(aktiv=True, grund="aktiv")
    return ergebnis


def lerne(con: sqlite3.Connection, konfig: Konfig | None) -> dict:
    """Faktoren komplett neu aus der Historie (Spec §5.3). Rückgabe: faktoren (wirksam: Vertrauen und Schranke),
    gelernt (roh), start, paare je Quelle, datenbasis (Entwürfe in Paaren), vertrauen, publikum_anteil π, ki_lehrer
    a_KI, ki_treffer_publikum q_KI, Holdout-Quoten, aktiv, grund, input_hash.
    Beispiel: 30 Entwürfe, deren KI-Note nur am Hook hängt → Hook ×1,2 …, alle Faktoren in [0,5; 2];
    unter 15 Entwürfen in Paaren bleiben alle Faktoren 1."""
    paare_, meta = _sammle(con, konfig)
    return _lerne(paare_, meta)


def _als_dict(z: sqlite3.Row) -> dict:
    paare_ = json.loads(z["paare"])
    return {"version": int(z["version"]), "faktoren": json.loads(z["faktoren"]), "gelernt": json.loads(z["gelernt"]),
            "start": json.loads(z["start"]), "paare": paare_, "datenbasis": int(z["datenbasis"]),
            "vertrauen": z["vertrauen"], "publikum_anteil": z["publikum_anteil"], "ki_lehrer": z["ki_lehrer"],
            "ki_treffer_publikum": z["ki_treffer_publikum"], "quote_holdout": z["quote_holdout"],
            "quote_holdout_start": z["quote_holdout_start"], "aktiv": bool(z["aktiv"]), "grund": z["grund"],
            "input_hash": z["input_hash"], "ki_pruef": paare_.get("ki_publikum", 0)}


def aktualisiere(con: sqlite3.Connection, konfig: Konfig | None) -> tuple[int, dict]:
    """Neue Version in massstab nur, wenn sich der input_hash ändert (Muster lernen.aktualisiere). Gleiche Paare →
    die vorhandene Version ohne neu zu trainieren. Rückgabe (Version, Ergebnis wie lerne bzw. die gespeicherte Zeile)."""
    paare_, meta = _sammle(con, konfig)
    h = _hash(paare_, meta)
    alt = con.execute("SELECT * FROM massstab WHERE input_hash = ?", (h,)).fetchone()
    if alt is not None:
        return int(alt["version"]), _als_dict(alt)
    ergebnis = _lerne(paare_, meta)
    version = con.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM massstab").fetchone()[0]
    try:
        _speichere(con, version, h, ergebnis)
    except sqlite3.IntegrityError:
        # Zwei Prozesse (Bot und Pipeline) haben gleichzeitig gelernt – der andere war schneller: seine Zeile gilt
        z = con.execute("SELECT * FROM massstab WHERE input_hash = ?", (h,)).fetchone()
        if z is None:
            raise
        return int(z["version"]), _als_dict(z)
    return version, {**ergebnis, "version": version}


def _speichere(con: sqlite3.Connection, version: int, h: str, ergebnis: dict) -> None:
    con.execute("""INSERT INTO massstab (version, erstellt, input_hash, faktoren, gelernt, start, paare, datenbasis,
                                         vertrauen, publikum_anteil, ki_lehrer, ki_treffer_publikum, quote_holdout,
                                         quote_holdout_start, aktiv, grund)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (version, iso(jetzt()), h, json.dumps(ergebnis["faktoren"]), json.dumps(ergebnis["gelernt"]),
                 json.dumps(ergebnis["start"]), json.dumps(ergebnis["paare"]), ergebnis["datenbasis"],
                 ergebnis["vertrauen"], ergebnis["publikum_anteil"], ergebnis["ki_lehrer"],
                 ergebnis["ki_treffer_publikum"], ergebnis["quote_holdout"], ergebnis["quote_holdout_start"],
                 int(ergebnis["aktiv"]), ergebnis["grund"]))


def _neueste(con: sqlite3.Connection) -> sqlite3.Row | None:
    try:
        return con.execute("SELECT * FROM massstab ORDER BY version DESC LIMIT 1").fetchone()
    except sqlite3.OperationalError:     # alte Datenbank ohne Tabelle
        return None


def faktoren(con: sqlite3.Connection) -> dict[str, float]:
    """Wirksame Faktoren der neuesten Version, sonst alle 1 (Handwerks-Start). Für kriterien.note/verluste."""
    z = _neueste(con)
    werte = _eins()
    if z is not None:
        werte.update({k: float(v) for k, v in json.loads(z["faktoren"]).items() if k in werte})
    return werte


# --- Anzeige ----------------------------------------------------------------------------------------------------

def _de(x: float, stellen: int = 2) -> str:
    return f"{x:.{stellen}f}".replace(".", ",")


def _schwaechste(con: sqlite3.Connection, anzahl: int = 3, letzte: int = 20) -> str | None:
    if "teile" not in _spalten(con, "kritiken"):
        return None
    summen: dict[str, list[float]] = {}
    for z in con.execute("SELECT teile FROM kritiken WHERE teile IS NOT NULL ORDER BY erstellt DESC, entwurf_id DESC "
                         "LIMIT ?", (letzte,)):
        try:
            teile = json.loads(z["teile"] or "{}")
        except json.JSONDecodeError:
            continue
        for k in kriterien.KRITERIEN:
            if (w := kriterien.teilwert((teile or {}).get(k))) is not None:
                summen.setdefault(k, []).append(w)
    if not summen:
        return None
    mittel = sorted((sum(v) / len(v), k) for k, v in summen.items())[:anzahl]
    return (f"Schwächstes im Schnitt (letzte {letzte}): "
            + " · ".join(f"{kriterien.KRITERIEN[k]['name']} {_de(m)}" for m, k in mittel))


def lernstand_zeilen(con: sqlite3.Connection, konfig: Konfig | None) -> list[str]:
    """Die Maßstab-Zeilen für regie_lernen.lernstand_text (Spec §5.5), aus der neuesten gespeicherten Version:
      📐 Cutter-Maßstab v12 – lernt (Vertrauen 38 %, Publikum führt zu 20 %)
      Fortschritt: ▓▓▓▓░░░░░░ 23/60 bis volles Vertrauen (38 %) – lernt seit 15
      Hook ×1,42 (20→27 %) ▲ · Leerlauf ×1,10 ▲ · Reiztakt ×0,71 ▼
      Paare: 184 KI · 6 du · 3 Publikum · Holdout 68 % (Handwerk 61 %)
      KI-Cutter trifft das Publikum: noch zu wenig (3/8)
      Schwächstes im Schnitt (letzte 20): Spielbild 0,31 · Hook 0,52 · Ende 0,58"""
    e = einstellungen(konfig)
    z = _neueste(con)
    zeilen = []
    if z is None:
        zeilen.append("📐 Cutter-Maßstab – Handwerks-Start (noch nichts gelernt)")
    else:
        d = _als_dict(z)
        if d["aktiv"]:
            zeilen.append(f"📐 Cutter-Maßstab v{d['version']} – lernt (Vertrauen {round(100 * d['vertrauen'])} %, "
                          f"Publikum führt zu {round(100 * d['publikum_anteil'])} %)")
        else:
            zeilen.append(f"📐 Cutter-Maßstab v{d['version']} – Handwerks-Start ({d['grund']})")
        fortschritt = lernen.fortschritt_zeile(types.SimpleNamespace(
            datenbasis=d["datenbasis"], mindestens=int(e["mindestens"]), voll_vertrauen=int(e["voll_vertrauen"]),
            vertrauen=d["vertrauen"]))
        if fortschritt:
            zeilen.append(fortschritt)
        f = {k: float(v) for k, v in d["faktoren"].items() if k in kriterien.KRITERIEN}
        bewegt = sorted((k for k, v in f.items() if abs(math.log(max(v, 1e-9))) > 0.01),
                        key=lambda k: (-abs(math.log(f[k])), k))[:5]
        if bewegt:
            w0 = start_gewichte(konfig)["short"]
            summe = sum(w * f.get(k, 1.0) for k, w in w0.items())
            teile = []
            for i, k in enumerate(bewegt):
                text = f"{kriterien.KRITERIEN[k]['name']} ×{_de(f[k])}"
                if i == 0 and k in w0 and summe > 0:
                    text += f" ({round(100 * w0[k])}→{round(100 * w0[k] * f[k] / summe)} %)"
                teile.append(text + (" ▲" if f[k] > 1 else " ▼"))
            zeilen.append(" · ".join(teile))
        p = d["paare"]
        zeile = f"Paare: {p.get('ki', 0)} KI · {p.get('du', 0)} du · {p.get('publikum', 0)} Publikum"
        if d["quote_holdout"] is not None:
            zeile += (f" · Holdout {round(100 * d['quote_holdout'])} % "
                      f"(Handwerk {round(100 * (d['quote_holdout_start'] or 0))} %)")
        zeilen.append(zeile)
        if d["ki_treffer_publikum"] is None:
            zeilen.append(f"KI-Cutter trifft das Publikum: noch zu wenig ({d['ki_pruef']}/{KI_PRUEF_PAARE})")
        else:
            zeilen.append(f"KI-Cutter trifft das Publikum: {round(100 * d['ki_treffer_publikum'])} % "
                          f"→ zählt ×{_de(d['ki_lehrer'], 1)}")
    if schwach := _schwaechste(con):
        zeilen.append(schwach)
    return zeilen
