"""🔎 /warum (06.10., Florian: „Gefühlt bewerte ich genau den gleichen Mist wie früher … liegt zu wenig Material vor?“)

Die Antwort aus deinen echten Daten statt einer Vermutung – nur lesen, nichts ändern, weckt nie. Drei Fragen:
1. Material: Wie viele Momente gibt es, welche Art (Einzelkill, Double, Triple+, Fail, lustig), wie viele neu, und
   wie viele lässt die Clip-Auswahl (⚙️) übrig?
2. Wiederholung: Wie viele verschiedene Momente stecken in den letzten Entwürfen?
3. Lernen: Lehren deine Bewertungen die Formel, die Momente auswählt (lernen.entwurf_paare), und was hat sie gelernt?
Darunter ein Fazit mit den Engpässen, die die Zahlen zeigen.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import timedelta

from . import db, einstellungen, lernen, regie
from .konfig import Konfig
from .vorbewertung import MERKMAL_NAMEN
from .zeit import aus_iso, jetzt

FENSTER = 10          # so viele letzte Entwürfe für die Wiederholung
KNAPP_JE_SHORT = 3    # weniger als 3 Shorts voller Momente (3 × max_momente) in der Auswahl gilt als knapp
EINZEL_VIEL = 0.6     # ab diesem Anteil Einzelkills/ohne Kill ist das Rohmaterial selbst der Engpass
NEU_TAGE = 7


def _zahl(x: float) -> str:
    return f"{x:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def material(con: sqlite3.Connection, nur_matches: set[str] | None) -> dict:
    """Momente (ohne die, die du 🗑️ verworfen hast) nach Art, neu in NEU_TAGE Tagen und in der Clip-Auswahl."""
    zeilen = con.execute(f"""SELECT m.schluessel, m.match_id, m.start_utc, m.stimmung, m.merkmale,
                                    c.max_gruppe, c.victory_royale
                               FROM momente m LEFT JOIN clips c ON c.id = m.clip_id
                              WHERE NOT ({db.hart_verworfen_sql('c.')} AND c.id IS NOT NULL)""").fetchall()
    arten: Counter = Counter()
    grenze = jetzt() - timedelta(days=NEU_TAGE)
    neu = auswahl = 0
    match_ids = set()
    for z in zeilen:
        if z["schluessel"].startswith("fail:"):
            arten["Fail"] += 1
            continue
        try:
            mk = json.loads(z["merkmale"] or "{}")
        except ValueError:
            mk = {}
        gruppe = int(z["max_gruppe"] if z["max_gruppe"] is not None else mk.get("max_gruppe", 0) or 0)
        arten[("ohne Kill", "Einzelkill", "Double")[gruppe] if gruppe < 3 else "Triple+"] += 1
        arten["Victory"] += bool(z["victory_royale"] or mk.get("victory_royale"))
        arten["lustig"] += z["stimmung"] == "lustig"
        match_ids.add(z["match_id"])
        try:
            neu += bool(z["start_utc"]) and aus_iso(z["start_utc"]) >= grenze
        except ValueError:
            pass
        auswahl += nur_matches is None or z["match_id"] in nur_matches
    matches = len(match_ids - {None})
    kills = sum(arten[a] for a in ("ohne Kill", "Einzelkill", "Double", "Triple+"))
    return {"gesamt": kills, "arten": arten, "neu": neu, "auswahl": auswahl, "matches": matches}


def wiederholung(con: sqlite3.Connection, fenster: int = FENSTER) -> dict:
    """Verschiedene Momente in den letzten Entwürfen, Plätze (Momente je Entwurf einmal) und die häufigsten."""
    je_entwurf = [set(m for m in liste if m) for liste in regie.gezeigte_momente(con, fenster)]
    je_entwurf = [m for m in je_entwurf if m]
    zaehler = Counter(m for momente in je_entwurf for m in momente)
    return {"entwuerfe": len(je_entwurf), "plaetze": sum(len(m) for m in je_entwurf), "verschieden": len(zaehler),
            "haeufigste": zaehler.most_common(3)}


def text(con: sqlite3.Connection, konfig: Konfig) -> str:
    konfig = einstellungen.anwenden(con, konfig)
    nur_matches, quell_hinweis = einstellungen.quell_matches(con, konfig)
    m, w = material(con, nur_matches), wiederholung(con)
    e = lernen.berechne(con, konfig)
    max_momente = int(regie.FORMATE["short"]["max_momente"])
    a = m["arten"]
    zeilen = ["🔎 Warum sieht es gleich aus? (aus deinen Daten)",
              f"📦 Material: {m['gesamt']} Momente aus {m['matches']} Matches, davon {m['neu']} neu in {NEU_TAGE} Tagen",
              "   " + " · ".join(f"{name} {a[name]}" for name in ("Einzelkill", "Double", "Triple+", "ohne Kill",
                                                                     "Victory", "lustig", "Fail") if a[name]),
              f"🎯 Clip-Auswahl: {quell_hinweis or 'alle'} → {m['auswahl']} Momente zur Wahl"]
    if w["entwuerfe"]:
        haeufig = ", ".join(f"{k} {n}×" for k, n in w["haeufigste"] if n > 1)
        zeilen.append(f"🔁 Letzte {w['entwuerfe']} Entwürfe: {w['verschieden']} verschiedene Momente auf {w['plaetze']} "
                      "Plätzen" + (f" – am häufigsten {haeufig}" if haeufig else ""))
    n_paare = e.paare_je_quelle.get("entwurf", 0)
    zeilen.append(f"🧠 Moment-Formel: {'lernt' if e.aktiv else e.grund} · {e.entwuerfe} deiner Entwurf-Bewertungen "
                  f"→ {n_paare} Moment-Paare · {e.freigaben} Freigaben · {e.battles} Battles · "
                  f"{e.paare_je_quelle.get('publikum', 0)} Publikums-Paare")
    if e.aktiv:
        diffs = sorted(((abs(e.werte[k] - e.start[k]), k) for k in e.werte if k in e.start), reverse=True)[:3]
        geaendert = [f"{MERKMAL_NAMEN.get(k, k)} {_zahl(e.start[k])} → {_zahl(e.werte[k])}" for d, k in diffs if d >= 0.01]
        if geaendert:
            zeilen.append("   gelernt: " + " · ".join(geaendert))
    fazit = []
    if m["auswahl"] < KNAPP_JE_SHORT * max_momente:
        fazit.append(f"📉 Wenig Material: {m['auswahl']} Momente zur Wahl, ein Short braucht 4–{max_momente} – "
                     "der Bot muss wiederholen."
                     + (f" In ⚙️ auf „alle“ stellen gibt {m['gesamt']}." if nur_matches is not None
                        and m["gesamt"] > m["auswahl"] else ""))
    if m["gesamt"] and (a["Einzelkill"] + a["ohne Kill"]) / m["gesamt"] >= EINZEL_VIEL:
        anteil = round(100 * (a["Einzelkill"] + a["ohne Kill"]) / m["gesamt"])
        fazit.append(f"🥱 {anteil} % sind Einzelkills oder ohne Kill – das ist das Rohmaterial; der Schnitt kann es nur "
                     "verpacken. Abwechslung bringen Fails, Lacher und Multikills (🔥 Viral mischt sie).")
    if w["plaetze"] and w["verschieden"] / w["plaetze"] < 0.5:
        fazit.append(f"🔁 Viel Wiederholung: nur {w['verschieden']} verschiedene auf {w['plaetze']} Plätzen.")
    if not m["neu"]:
        fazit.append(f"🕹️ Seit {NEU_TAGE} Tagen keine neuen Momente – es kommt nichts Frisches nach.")
    if not e.aktiv:
        fazit.append(f"🧠 Die Formel, die Momente auswählt, lernt noch nicht ({e.grund}).")
    zeilen.append("Fazit:" if fazit else "Fazit: In den Zahlen ist kein klarer Engpass zu sehen.")
    zeilen += [f"• {f}" for f in fazit]
    return "\n".join(zeilen)
