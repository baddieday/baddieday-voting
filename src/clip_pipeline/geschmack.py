"""Dein Geschmack (Stufe 2 des Umbaus, 07.10., Florian: „Lernen zurück“ – aus ✅/❌ und KI-Urteil, mutig ausprobieren,
einmal pro Woche sehen, was er gelernt hat).

Der Bot stellt bei jedem Short drei Schrauben selbst ein und merkt sich, was ankommt:

  Aufbau     ⚡ schnelle Montage · 📖 erzählt · 📈 Steigerung · 🎬 Kino   (stile.STILE)
  Tempo      schnell (Segmente ×0,8) · ruhig (×1,25)
  Zeitlupe   viel (bis 8 je Video) · wenig (höchstens 2)

Lehrer (je Video und Schraube, Beta-Verteilung je Wahl):
  - dein ✅ = Treffer, ❌ = Fehlschlag (Gewicht 1). Dein Grund grenzt ein: ⏱️/⏳/🎵 haben mit den Schrauben nichts zu
    tun (die regeln deine festen Regeln), 😵/🎆/💥 betreffen nur Tempo und Zeitlupe, 🥱 oder ❌ ohne Grund alle drei.
  - die KI-Note des fertigen Videos (0–100, kritik.py) mit einem Drittel Gewicht (wie regie_lernen.KI_STAERKE).
Alte Bewertungen zählen sofort für den Aufbau (der Stil steht schon in den Parametern alter Entwürfe).

Wahl: Thompson-Sampling je Schraube; „mutig“ (geschmack.mut, Standard 0,5): bei jedem zweiten Video wird eine Schraube
bewusst auf ihre am wenigsten erprobte Einstellung gestellt (Experiment). Derselbe Aufbau nie dreimal hintereinander.
Deine Regeln (regeln.anwenden) kommen danach – sie gehen immer vor.

Die KI schaut sich jedes gesendete Video danach im Hintergrund an (ki_nachtragen, Lern-Bot-Schleife) – das Video
kommt dadurch nicht später. Sonntags ab 18 Uhr fasst wochen_text die Woche zusammen (Lern-Meldung woche:<JJJJ-Www>).
"""

from __future__ import annotations

import copy
import json
import logging
import random
import sqlite3
from datetime import timedelta

from . import db, einstellungen, regeln, stile
from .konfig import Konfig
from .zeit import iso, jetzt, utc_zu_lokal

log = logging.getLogger("pipeline")

KNOEPFE: dict[str, tuple[str, ...]] = {"aufbau": stile.ROTATION, "tempo": ("schnell", "ruhig"),
                                      "zeitlupe": ("viel", "wenig")}
NAMEN = {("aufbau", "montage"): "Aufbau „schnelle Montage“", ("aufbau", "story"): "Aufbau „erzählt“",
         ("aufbau", "steigerung"): "Aufbau „Steigerung“", ("aufbau", "kino"): "Aufbau „Kino“",
         ("tempo", "schnell"): "schnelle Schnitte", ("tempo", "ruhig"): "ruhige Schnitte",
         ("zeitlupe", "viel"): "viel Zeitlupe", ("zeitlupe", "wenig"): "wenig Zeitlupe"}
TEMPO = {"schnell": 0.8, "ruhig": 1.25}
ZEITLUPEN = {"viel": 8, "wenig": 2}
KI_GEWICHT = 0.34                                  # wie regie_lernen.KI_STAERKE: die KI zählt ein Drittel von dir
NICHT_GESCHMACK = {"kurz", "lang", "musik"}        # dafür gibt es feste Regeln – die Schrauben sind unschuldig
EFFEKT_GRUENDE = {"hektisch", "effekte_viel", "action"}
MUT_STANDARD = 0.5


def _wahl_aus(parameter_json: str | None) -> dict:
    try:
        p = json.loads(parameter_json or "{}") or {}
    except (TypeError, ValueError):
        return {}
    wahl = dict(p.get("geschmack") or {})
    if "aufbau" not in wahl and p.get("stil") in KNOEPFE["aufbau"]:   # alte Entwürfe: nur der Stil ist bekannt
        wahl["aufbau"] = p["stil"]
    return {k: v for k, v in wahl.items() if k in KNOEPFE and v in KNOEPFE[k]}


def _schuld(daumen: int, gruende: list[str]) -> set[str]:
    """Welche Schrauben ein Urteil betrifft (✅: alle; ❌: je nach Grund)."""
    if daumen > 0 or not gruende:
        return set(KNOEPFE)
    g = set(gruende)
    rest = g - NICHT_GESCHMACK - EFFEKT_GRUENDE
    if rest:                                        # 🥱 langweilig oder ein Grund ohne feste Regel: alles
        return set(KNOEPFE)
    return {"tempo", "zeitlupe"} if g & EFFEKT_GRUENDE else set()


def beobachtungen(con: sqlite3.Connection, fmt: str = "short", seit: str | None = None) -> list[dict]:
    """Je Entwurf mit bekannter Wahl: Wahl, dein Daumen/Gründe, KI-Note, Zeitpunkt."""
    zeilen = con.execute(
        """SELECT e.id, e.parameter, e.erstellt, b.daumen, b.gruende, k.ki_score FROM entwuerfe e
             LEFT JOIN entwurf_bewertungen b ON b.entwurf_id = e.id
             LEFT JOIN kritiken k ON k.entwurf_id = e.id
            WHERE e.format = ? AND e.erstellt >= ? ORDER BY e.id""", (fmt, seit or "")).fetchall()
    ergebnis = []
    for z in zeilen:
        wahl = _wahl_aus(z["parameter"])
        if not wahl:
            continue
        try:
            gruende = json.loads(z["gruende"] or "[]")
        except ValueError:
            gruende = []
        roh = json.loads(z["parameter"] or "{}") or {}
        ergebnis.append({"id": z["id"], "wahl": wahl, "daumen": z["daumen"], "gruende": gruende,
                         "ki": z["ki_score"], "erstellt": z["erstellt"],
                         "experiment": (roh.get("geschmack") or {}).get("experiment")})
    return ergebnis


def statistik(con: sqlite3.Connection, fmt: str = "short") -> dict[str, dict[str, dict]]:
    """{Schraube: {Wahl: {"n": Gewicht, "s": Treffer-Gewicht, "ja": ✅, "nein": ❌}}} über alle Entwürfe."""
    stat = {k: {o: {"n": 0.0, "s": 0.0, "ja": 0, "nein": 0} for o in opt} for k, opt in KNOEPFE.items()}
    for b in beobachtungen(con, fmt):
        if b["daumen"] in (1, -1):
            for knopf in _schuld(b["daumen"], b["gruende"]) & set(b["wahl"]):
                w = stat[knopf][b["wahl"][knopf]]
                w["n"] += 1.0
                w["s"] += 1.0 if b["daumen"] > 0 else 0.0
                w["ja" if b["daumen"] > 0 else "nein"] += 1
        if b["ki"] is not None:
            for knopf, wahl in b["wahl"].items():
                w = stat[knopf][wahl]
                w["n"] += KI_GEWICHT
                w["s"] += KI_GEWICHT * max(0.0, min(1.0, float(b["ki"]) / 100))
    return stat


def waehle(con: sqlite3.Connection, konfig: Konfig, fmt: str = "short") -> dict:
    """Die drei Schrauben für den nächsten Entwurf (plus "experiment": welche bewusst neu probiert wird, sonst None).
    Deterministisch je Entwurf (Zufall aus Format und Anzahl der Entwürfe)."""
    n = con.execute("SELECT COUNT(*) FROM entwuerfe WHERE format = ?", (fmt,)).fetchone()[0]
    zufall = random.Random(f"geschmack:{fmt}:{n}")
    stat = statistik(con, fmt)
    wahl: dict = {}
    for knopf, optionen in KNOEPFE.items():
        zuege = sorted(((zufall.betavariate(1 + stat[knopf][o]["s"], 1 + stat[knopf][o]["n"] - stat[knopf][o]["s"]), o)
                        for o in optionen), reverse=True)
        wahl[knopf] = zuege[0][1]
    experiment = None
    if zufall.random() < float(konfig.wert("geschmack.mut", MUT_STANDARD)):
        knopf = zufall.choice(sorted(KNOEPFE))
        andere = [o for o in KNOEPFE[knopf] if o != wahl[knopf]]
        wahl[knopf] = min(andere, key=lambda o: (stat[knopf][o]["n"], zufall.random()))
        experiment = knopf
    letzte = stile._letzte(con, fmt)
    if len(letzte) == 2 and letzte[0] == letzte[1] == wahl["aufbau"]:   # nie dreimal derselbe Aufbau
        wahl["aufbau"] = max((o for o in KNOEPFE["aufbau"] if o != wahl["aufbau"]),
                             key=lambda o: (stat["aufbau"][o]["s"] + 1) / (stat["aufbau"][o]["n"] + 2))
    fest = str(konfig.wert("regie.stil", "auto") or "auto")
    if fest in stile.STILE:                                              # ⚙️ fester Stil geht vor
        wahl["aufbau"] = fest
        experiment = None if experiment == "aufbau" else experiment
    wahl["experiment"] = experiment
    return wahl


def anwenden(con: sqlite3.Connection, konfig: Konfig, fmt: str, p: dict) -> dict:
    """Gelernte Parameter mit den drei Schrauben (Kopie): Aufbau als Stil, Tempo relativ, Zeitlupe als Obergrenze."""
    if fmt not in stile.FORMATE:
        return p
    wahl = waehle(con, konfig, fmt)
    p = stile.mit_stil(p, wahl["aufbau"])
    unten, oben = stile.GRENZEN["seg_min_faktor"]
    p["seg_min_faktor"] = round(max(unten, min(oben, float(p.get("seg_min_faktor", 1.0)) * TEMPO[wahl["tempo"]])), 3)
    p["max_lupen"] = ZEITLUPEN[wahl["zeitlupe"]]
    p["geschmack"] = wahl
    return p


# --- KI-Urteil im Hintergrund ------------------------------------------------------------------------------------

def offen_fuer_ki(con: sqlite3.Connection, ohne: set[int] | frozenset = frozenset()) -> int | None:
    """Neuester gesendeter Short der letzten 3 Tage ohne KI-Note (und nicht schon versucht)."""
    for z in con.execute("""SELECT e.id FROM entwuerfe e LEFT JOIN kritiken k ON k.entwurf_id = e.id
                             WHERE e.format = 'short' AND e.status IN ('gesendet', 'bewertet') AND e.datei IS NOT NULL
                               AND e.erstellt >= ? AND k.ki_score IS NULL ORDER BY e.id DESC LIMIT 10""",
                         (iso(jetzt() - timedelta(days=3)),)):
        if z["id"] not in ohne:
            return int(z["id"])
    return None


def ki_nachtragen(konfig: Konfig, entwurf_id: int) -> float | None:
    """Misst (falls nötig) und lässt die KI den Entwurf blind benoten – eigene Verbindung (läuft in einem Thread),
    unter der Pipeline-Sperre (belegt: sperre.Gesperrt). Rückgabe: KI-Note oder None (Tageslimit, Claude-Fehler)."""
    from . import kritik
    from .sperre import sperre

    con = db.verbinde(konfig.datenbank)
    try:
        k = einstellungen.anwenden(con, konfig)
        daten = copy.deepcopy(k.daten)   # nie die geladene Konfig ändern
        daten.setdefault("regie", {}).setdefault("kritik", {})["ki"] = True   # einfacher Modus: KI nur hier, nach dem Senden
        k = Konfig(daten=daten, quelle=k.quelle)
        with sperre(konfig.datenbank.with_suffix(".lock"), warten_s=5.0):   # belegt: Gesperrt, der Bot probiert später
            return kritik.bewerte(con, k, entwurf_id).get("ki_score")
    finally:
        con.close()


# --- Wochenbericht -----------------------------------------------------------------------------------------------

def _anteil(w: dict) -> float:
    return (w["s"] + 1) / (w["n"] + 2)


def _bewertet(w: dict) -> str:
    gesamt = w["ja"] + w["nein"]
    return f" ({w['ja']} von {gesamt} ✅)" if gesamt else ""


def wochen_text(con: sqlite3.Connection, konfig: Konfig, bis=None) -> str | None:
    """„🧠 Deine Woche …“ – None, wenn in den letzten 7 Tagen kein Video kam (dann Ruhe)."""
    bis = bis or jetzt()
    von = bis - timedelta(days=7)
    woche = [z for z in con.execute(
        """SELECT e.id, e.parameter, b.daumen, k.ki_score FROM entwuerfe e
             LEFT JOIN entwurf_bewertungen b ON b.entwurf_id = e.id LEFT JOIN kritiken k ON k.entwurf_id = e.id
            WHERE e.format = 'short' AND e.status IN ('gesendet', 'bewertet') AND e.erstellt >= ? AND e.erstellt < ?""",
        (iso(von), iso(bis)))]
    if not woche:
        return None
    zone = konfig.wert("zeit.zeitzone", "Europe/Berlin")
    ja = sum(1 for z in woche if z["daumen"] == 1)
    nein = sum(1 for z in woche if z["daumen"] == -1)
    ki = [float(z["ki_score"]) for z in woche if z["ki_score"] is not None]
    zeilen = [f"🧠 Deine Woche ({utc_zu_lokal(von, zone):%d.%m.}–{utc_zu_lokal(bis, zone):%d.%m.})",
              f"🎬 {len(woche)} Video{'s' if len(woche) != 1 else ''} · {ja} ✅ · {nein} ❌"
              + (f" · KI-Note im Schnitt {sum(ki) / len(ki):.0f}" if ki else "")]
    stat = statistik(con)
    erprobt = [(k, o, w) for k, opt in stat.items() for o, w in opt.items() if w["n"] >= 2]
    gut = sorted((x for x in erprobt if _anteil(x[2]) >= 0.6), key=lambda x: -_anteil(x[2]))[:3]
    schlecht = sorted((x for x in erprobt if _anteil(x[2]) <= 0.4), key=lambda x: _anteil(x[2]))[:2]
    if gut:
        zeilen.append("👍 Kommt gut an: " + " · ".join(NAMEN[(k, o)] + _bewertet(w) for k, o, w in gut))
    if schlecht:
        zeilen.append("👎 Kommt weniger an: " + " · ".join(NAMEN[(k, o)] + _bewertet(w) for k, o, w in schlecht))
    if not gut and not schlecht:
        zeilen.append("🤔 Noch kein klares Bild – ich brauche ein paar ✅/❌ mehr.")
    neu = sum(1 for z in woche if ((json.loads(z["parameter"] or "{}") or {}).get("geschmack") or {}).get("experiment"))
    if neu:
        zeilen.append(f"🧪 {neu}× bewusst etwas Neues ausprobiert.")
    zeilen.append(regeln.regeln_zeile(con, konfig).replace("📏 Deine Regeln:", "📏 Deine Regeln gelten weiter:"))
    return "\n".join(zeilen)


def wochenbericht(con: sqlite3.Connection, konfig: Konfig, jetzt_utc=None) -> bool:
    """Sonntags ab 18 Uhr (Ortszeit) einmal je Woche die Lern-Meldung woche:<JJJJ-Www>. True = neu angelegt."""
    jetzt_utc = jetzt_utc or jetzt()
    lokal = utc_zu_lokal(jetzt_utc, konfig.wert("zeit.zeitzone", "Europe/Berlin"))
    if lokal.isoweekday() != 7 or lokal.hour < 18:
        return False
    jahr, woche, _ = lokal.isocalendar()
    schluessel = f"woche:{jahr}-W{woche:02d}"
    if con.execute("SELECT 1 FROM lern_meldungen WHERE schluessel = ?", (schluessel,)).fetchone():
        return False
    text = wochen_text(con, konfig, jetzt_utc)
    return bool(text) and db.lern_meldung(con, schluessel, text)


def stand_zeile(con: sqlite3.Connection) -> str | None:
    """Für 📋 Stand: „🧠 Gelernt aus 12 Bewertungen und 8 KI-Noten – Wochenbericht sonntags“."""
    b = beobachtungen(con)
    daumen = sum(1 for x in b if x["daumen"] in (1, -1))
    ki = sum(1 for x in b if x["ki"] is not None)
    if not daumen and not ki:
        return None
    return (f"🧠 Gelernt aus {daumen} Bewertung{'en' if daumen != 1 else ''} und {ki} KI-Note{'n' if ki != 1 else ''}"
            " – Wochenbericht sonntags")

