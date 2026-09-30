"""Plattformneutrale Publikumsqualität für kleine Datenmengen.

Fehlende Kennzahlen bleiben unbekannt. Verfügbare Qualitätskomponenten werden
neu gewichtet; Reichweite erhält selbst dann höchstens 8 Prozent. Die festen
Startwerte sind transparente Priors, keine behaupteten Plattform-Benchmarks.
Eigene ältere, vergleichbare Posts ersetzen sie schrittweise durch Median/MAD.
"""

from __future__ import annotations

import math
import statistics

from .zeit import aus_iso


# Name: Gewicht, Startwert, kleinste Skala. Watchtime und Retention sind dieselbe
# Komponente, damit averageViewDuration + averageViewPercentage nicht doppelt zählen.
KOMPONENTEN = {
    "retention": (0.40, 0.50, 0.25),
    "completion": (0.18, 0.30, 0.22),
    "shares": (0.12, 0.01, 0.01),
    "saves": (0.10, 0.005, 0.005),
    "follows": (0.05, 0.002, 0.002),
    "rewatches": (0.04, 0.05, 0.05),
    "kommentare": (0.04, 0.004, 0.004),
    "likes": (0.02, 0.04, 0.04),
    "reichweite": (0.05, math.log1p(1000), 1.5),
}


def _zahl(daten, name):
    try:
        wert = daten[name]
        if wert is None or isinstance(wert, bool):
            return None
        wert = float(wert)
        return wert if math.isfinite(wert) and wert >= 0 else None
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def _get(daten, name, standard=None):
    try:
        return daten[name]
    except (KeyError, IndexError, TypeError):
        return standard


def _alter(post, messung):
    try:
        return max(0.0, (aus_iso(messung["gemessen_utc"]) - aus_iso(post["gepostet_utc"]))
                   .total_seconds() / 86400)
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def _roh(post, messung):
    views = _zahl(messung, "views")
    dauer = _zahl(post, "dauer_s")
    retention = _zahl(messung, "retention_prozent")
    wiedergabe = _zahl(messung, "wiedergabe_s")
    completion = _zahl(messung, "voll_prozent")
    teile = {name: None for name in KOMPONENTEN}
    if retention is not None:
        teile["retention"] = min(1.5, retention / 100)
    elif wiedergabe is not None and dauer:
        teile["retention"] = min(1.5, wiedergabe / dauer)
    if completion is not None:
        teile["completion"] = min(1.0, completion / 100)
    if views is not None and views > 0:
        for name in ("likes", "kommentare", "shares", "saves", "follows", "rewatches"):
            wert = _zahl(messung, name)
            if wert is not None:
                # Kleine Stichproben werden zum Startwert geschrumpft. Unbekannte
                # Zähler gelangen NICHT als künstliche Null in diese Rechnung.
                prior = KOMPONENTEN[name][1]
                teile[name] = (min(wert / views, 2.0) * views + 100 * prior) / (views + 100)
        impressions = _zahl(messung, "impressions")
        alter = _alter(post, messung)
        if impressions:
            # Gleich große Ausspielung vergleichen, nicht bloße kumulierte Views.
            teile["reichweite"] = math.log1p(1000 * min(2.0, views / impressions))
        elif alter is not None:
            # Näherung der abflachenden Reichweitenkurve; die gleich alte Basis
            # unten wird bevorzugt. Kein Bonus nur fürs längere Online-Sein.
            teile["reichweite"] = math.log1p(views / math.sqrt(max(0.5, alter)))
    return teile


def berechne(post, messung, basis=()) -> dict:
    """Score -1..1, Confidence 0..1 und auswertbare Beiträge.

    ``basis`` enthält {"post": ..., "messung": ...} ausschließlich älterer
    Veröffentlichungen. Dasselbe Video, spätere Posts und fremde Plattformen
    werden zusätzlich ausgeschlossen; die Funktion trainiert nichts selbst.
    Views ohne Qualitätskennzahl ergeben keinen Qualitätsscore.
    """
    roh = _roh(post, messung)
    vergleich = []
    alter = _alter(post, messung)
    for eintrag in basis:
        p, m = eintrag["post"], eintrag["messung"]
        if _get(p, "plattform") != _get(post, "plattform"):
            continue
        if _get(p, "ziel") and _get(p, "ziel") == _get(post, "ziel"):
            continue
        if _get(p, "id") is not None and _get(p, "id") == _get(post, "id"):
            continue
        if _get(p, "account_id") != _get(post, "account_id"):
            continue
        if _get(p, "gepostet_utc") and _get(post, "gepostet_utc"):
            if aus_iso(p["gepostet_utc"]) >= aus_iso(post["gepostet_utc"]):
                continue
        vergleich.append((p, m))
    # Vergleiche nach Möglichkeit ähnliche Länge und Messalter. Bei wenig Daten
    # gilt weiter die eigene Plattformbasis; die Priors bleiben dann stärker.
    dauer = _zahl(post, "dauer_s")
    nahe = [(p, m) for p, m in vergleich if dauer and _zahl(p, "dauer_s")
            and 0.75 <= _zahl(p, "dauer_s") / dauer <= 1.33
            and alter is not None and _alter(p, m) is not None
            and 0.5 <= max(0.1, _alter(p, m)) / max(0.1, alter) <= 2]
    if len(nahe) >= 3:
        vergleich = nahe
    basis_roh = [_roh(p, m) for p, m in vergleich]
    teile = {}
    for name, wert in roh.items():
        if wert is None:
            continue
        gewicht, prior, skala = KOMPONENTEN[name]
        werte = [r[name] for r in basis_roh if r[name] is not None]
        # Impressions-basierte Reichweite nur mit ebenso gemessenen Posts
        # vergleichen: Views/Impressions und Views/Alter haben andere Nenner.
        if name == "reichweite":
            hat_impressions = bool(_zahl(messung, "impressions"))
            werte = [r[name] for r, (_, m) in zip(basis_roh, vergleich)
                     if r[name] is not None and bool(_zahl(m, "impressions")) == hat_impressions]
        anteil = len(werte) / (len(werte) + 8)
        median = statistics.median(werte) if werte else prior
        mitte = (1 - anteil) * prior + anteil * median
        mad = statistics.median(abs(w - median) for w in werte) if werte else 0.0
        standardisiert = math.tanh((wert - mitte) / max(skala, 1.4826 * mad))
        teile[name] = {"wert": round(wert, 6), "basis": round(mitte, 6),
                       "basis_n": len(werte), "score": round(standardisiert, 6), "gewicht": gewicht}
    qualitaet = {k: v for k, v in teile.items() if k != "reichweite"}
    qualitaetsgewicht = sum(v["gewicht"] for v in qualitaet.values())
    views = _zahl(messung, "views")
    if not qualitaetsgewicht or views == 0:
        return {"score": None, "audience_success_score": None, "confidence": 0.0,
                "components": teile, "fehlend": [k for k, v in roh.items() if v is None]}
    reach_weight = min(0.08, KOMPONENTEN["reichweite"][0] / (qualitaetsgewicht + 0.05)) if "reichweite" in teile else 0.0
    score = sum(v["gewicht"] * v["score"] for v in qualitaet.values()) / qualitaetsgewicht
    score = (1 - reach_weight) * score + reach_weight * teile.get("reichweite", {}).get("score", 0)
    for name, teil in teile.items():
        teil["anteil"] = round(reach_weight if name == "reichweite" else
                                 (1 - reach_weight) * teil["gewicht"] / qualitaetsgewicht, 6)
        teil["beitrag"] = round(teil["score"] * teil["anteil"], 6)
    # Vollständigkeit, Exposition und Reife sind Evidenz; bloße Views können eine
    # unbekannte Retention nicht ersetzen. Ohne Exposition ist Vertrauen begrenzt.
    exposition = math.sqrt(views / (views + 200)) if views is not None else 0.25
    reife = 0.3 + 0.7 * (1 - math.exp(-alter / 1.5)) if alter is not None else 0.5
    confidence = min(0.98, (qualitaetsgewicht + (0.05 if "reichweite" in teile else 0)) * exposition * reife)
    return {"score": round(score, 6), "audience_success_score": round(score, 6),
            "confidence": round(confidence, 6), "components": teile,
            "fehlend": [k for k, v in roh.items() if v is None]}
