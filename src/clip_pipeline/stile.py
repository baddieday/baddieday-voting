"""Schnittstile (Regisseur 3.0, 30.09., Florian: „immer die selbe Grütze … er muss das selbst erkennen und lernen“).

Vorher sah jeder Short gleich aus: ganzes 16:9-Bild als schmaler Streifen (ein Drittel der Höhe) im unscharfen Rand,
gleicher Spannungsbogen, gleiches Tempo. Ein Stil ist ein Schnitt-Konzept, wie es ein Cutter wählt:

  ⚡ Montage     groß gezoomt, schnelle Schnitte auf jedem Beat, stärkster Moment zuerst, mehr Musik
  📖 Story       mittlerer Zoom, zeitlich erzählt (Höhepunkt am Ende), ruhigere Schnitte, weniger Effekte, leisere Musik
  📈 Steigerung  vom schwächsten zum stärksten Moment, jeder Schnitt legt nach
  🎬 Kino        sehr groß gezoomt, lange Einstellungen, Hook vorn, dezentere Effekte
  🎞️ Klassik     der bisherige Aufbau (ganzes Bild, Bogen) – als Vergleich

Stile verändern die gelernten Werte RELATIV (z. B. Segmentlänge ×0,75) – was aus Bewertungen und Publikum gelernt
ist, bleibt wirksam. „rahmen_zoom“ vergrößert im Short das Spielbild (Mitte, die Ränder fallen weg; die Texte bleiben
im Rand darüber/darunter).

Welcher Stil kommt, entscheidet der Bot selbst: Thompson-Sampling über die Cutter-Scores (kritik.py) der bisherigen
Entwürfe je Stil – ohne dein 👍/👎. Ein Stil mit guten Noten kommt öfter, jeder bekommt weiter Chancen, und nie
dreimal derselbe hintereinander. In ⚙️ Einstellungen lässt sich ein Stil fest wählen ([regie].stil).
"""

from __future__ import annotations

import json
import random
import sqlite3

from .konfig import Konfig

STILE: dict[str, dict] = {
    "montage": {"titel": "⚡ Montage", "rahmen_zoom": 1.45, "reihenfolge": "bogen", "hook_staerkster": True,
                "faktoren": {"seg_min_faktor": 0.75, "effekt_hektik": 1.2, "musik_pegel": 1.3}, "beats": 1},
    "story": {"titel": "📖 Story", "rahmen_zoom": 1.25, "reihenfolge": "chronologisch", "hook_staerkster": False,
              "faktoren": {"seg_min_faktor": 1.4, "effekt_hektik": 0.7, "musik_pegel": 0.6}, "beats": 2},
    "steigerung": {"titel": "📈 Steigerung", "rahmen_zoom": 1.3, "reihenfolge": "steigend", "hook_staerkster": False,
                   "faktoren": {"seg_min_faktor": 0.9}, "beats": None},
    "kino": {"titel": "🎬 Kino", "rahmen_zoom": 1.6, "reihenfolge": "bogen", "hook_staerkster": True,
             "faktoren": {"seg_min_faktor": 1.2, "effekt_hektik": 0.85, "musik_pegel": 0.8}, "beats": None},
    "klassik": {"titel": "🎞️ Klassik", "rahmen_zoom": 1.0, "reihenfolge": "bogen", "hook_staerkster": None,
                "faktoren": {}, "beats": None},
}
GRENZEN = {"seg_min_faktor": (0.5, 2.0), "effekt_hektik": (0.3, 1.3), "musik_pegel": (0.0, 1.0)}
FORMATE = ("short",)   # der Rahmen-Zoom gilt nur im Hochformat; der Zusammenschnitt bleibt vorerst wie er ist


def _stil_von(parameter_json: str | None) -> str | None:
    try:
        return (json.loads(parameter_json or "{}") or {}).get("stil")
    except (TypeError, ValueError):
        return None


def statistik(con: sqlite3.Connection, fmt: str) -> dict[str, dict]:
    """Je Stil: Anzahl Urteile, Summe der Scores (0..1) – aus kritiken (dem Cutter-Kritiker), nicht aus 👍/👎."""
    werte: dict[str, dict] = {s: {"n": 0, "summe": 0.0} for s in STILE}
    for z in con.execute("""SELECT e.parameter, k.score FROM kritiken k JOIN entwuerfe e ON e.id = k.entwurf_id
                             WHERE e.format = ?""", (fmt,)):
        stil = _stil_von(z["parameter"])
        if stil in werte:
            werte[stil]["n"] += 1
            werte[stil]["summe"] += max(0.0, min(1.0, float(z["score"]) / 100))
    return werte


def _letzte(con: sqlite3.Connection, fmt: str, anzahl: int = 2) -> list[str | None]:
    return [_stil_von(z["parameter"]) for z in con.execute(
        "SELECT parameter FROM entwuerfe WHERE format = ? ORDER BY id DESC LIMIT ?", (fmt, anzahl))]


def waehle(con: sqlite3.Connection, konfig: Konfig, fmt: str) -> str:
    """Der Stil des nächsten Entwurfs: fest aus [regie].stil (⚙️) oder selbst gelernt (Thompson-Sampling).
    Deterministisch je Entwurf (Zufall aus Format und Anzahl der Entwürfe) – Lernstand und Entwurf sehen denselben."""
    fest = str(konfig.wert("regie.stil", "auto") or "auto")
    if fest in STILE:
        return fest
    n = con.execute("SELECT COUNT(*) FROM entwuerfe WHERE format = ?", (fmt,)).fetchone()[0]
    zufall = random.Random(f"{fmt}:{n}")
    werte = statistik(con, fmt)
    # Beta(1 + Summe der Scores, 1 + Summe der Fehlpunkte): gute Noten ziehen, wenig Erfahrung streut stark
    zuege = sorted(((zufall.betavariate(1 + w["summe"], 1 + w["n"] - w["summe"]), stil) for stil, w in werte.items()),
                   reverse=True)
    letzte = _letzte(con, fmt)
    for _wert, stil in zuege:
        if not (len(letzte) == 2 and letzte[0] == letzte[1] == stil):   # nie dreimal derselbe hintereinander
            return stil
    return zuege[0][1]


def anwenden(con: sqlite3.Connection, konfig: Konfig, fmt: str, p: dict) -> dict:
    """p (gelernte Regie-Parameter) mit dem gewählten Stil: Faktoren relativ, Rahmen, Reihenfolge, Hook, Name."""
    if fmt not in FORMATE:
        return p
    name = waehle(con, konfig, fmt)
    stil = STILE[name]
    p = dict(p)
    for schluessel, faktor in stil["faktoren"].items():
        unten, oben = GRENZEN[schluessel]
        p[schluessel] = round(max(unten, min(oben, float(p.get(schluessel, 1.0)) * faktor)), 3)
    if stil["beats"] is not None:
        p["beats_pro_schnitt"] = int(stil["beats"]) if name == "montage" else max(int(p.get("beats_pro_schnitt", 1)),
                                                                                  int(stil["beats"]))
    if stil["hook_staerkster"] is not None:
        p["hook_staerkster"] = bool(stil["hook_staerkster"])
    p.update(stil=name, rahmen_zoom=stil["rahmen_zoom"], reihenfolge=stil["reihenfolge"])
    return p


def stil_zeile(con: sqlite3.Connection, fmt: str = "short") -> str:
    """Für /lernstand: „Schnittstile (Cutter-Score im Schnitt): ⚡ Montage 71 (5×) · …“."""
    werte = statistik(con, fmt)
    teile = [f"{STILE[s]['titel']} {100 * w['summe'] / w['n']:.0f} ({w['n']}×)" if w["n"] else f"{STILE[s]['titel']} –"
             for s, w in sorted(werte.items(), key=lambda x: -(x[1]["summe"] / x[1]["n"] if x[1]["n"] else -1))]
    return "Schnittstile (Cutter-Score im Schnitt, lernt selbst): " + " · ".join(teile)
