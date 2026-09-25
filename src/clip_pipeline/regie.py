"""Der Regisseur (`pipeline compose`): aus Momenten mit Stimmung wird eine Schnittliste (JSON).

Formate:
  zusammenschnitt  16:9, 3–5 min je nach Material
  short            9:16, 30–45 s, unscharfer Rand, Schriftzug "clip-battle.de"

Schritte (jeder für sich nachvollziehbar, Zahlen in PARAMETER und [regie] der Konfig):
  1. Auswahl     Punkte je Moment: Kill-Serie (1/3/6/10), Victory, Stimmung, Elo, gelernte Vorlieben
                 (Stimmung und je Moment aus deinen 👍/👎). Abwechslung: Wer im letzten Entwurf war, verliert
                 70 % seiner Punkte, im vorletzten 35 % usw. (zusammen höchstens 100 %) – so kommen nicht immer
                 dieselben Momente, die stärksten aber regelmäßig wieder. Als Anteil, weil die Punkte weit
                 streuen (Einzelkill 3, Vierfach-Kill 13): ein fester Abzug ließe die stärksten immer vorn.
                 Verworfene Clips nie; höchstens n Momente aus demselben Match.
  2. Bogen       Einstieg = zweitstärkster Moment (Hook), dann steigend, bei ~60 % eine Atempause
                 (lustig/chill), der stärkste zum Schluss. Keine gleiche Stimmung / kein gleiches Match
                 zweimal hintereinander, wenn es sich vermeiden lässt.
  3. Musik       passend zur vorherrschenden Stimmung (Quellen-Stimmung, Energie relativ zur Bibliothek,
                 Tempo); Start so versetzt, dass der "Drop" des Titels auf den Höhepunkt fällt.
  4. Schnitt     jede Grenze auf einem Beat (jedem n-ten); kein Kill wird abgeschnitten.
  5. Übergänge   je Stimmung des folgenden Moments; die Mitte des Übergangs liegt genau auf dem Beat.
Die Schnittliste wird gegen schemas/regie.schema.json und fachlich geprüft, bevor sie gespeichert wird.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from . import schema
from .konfig import Konfig
from .musik import ZIEL
from .zeit import iso, jetzt

log = logging.getLogger("pipeline")

FORMATE = {
    # Dauer gesamt, Segmentlänge, Auflösung
    "zusammenschnitt": {"min_s": 180.0, "max_s": 300.0, "seg_min_s": 3.0, "seg_max_s": 25.0, "b": 1920, "h": 1080},
    "short": {"min_s": 30.0, "max_s": 45.0, "seg_min_s": 2.5, "seg_max_s": 12.0, "b": 1080, "h": 1920},
}
STIMMUNG_WERT = {"episch": 3.0, "spannend": 2.0, "lustig": 1.5, "frustriert": 1.0, "chill": 0.5}
KILL_PUNKTE = [0, 1, 3, 6, 10]
# Übergang in einen Moment hinein, je Stimmung: (xfade-Art, Dauer in s). "schnitt" = harter Schnitt.
UEBERGANG = {"episch": ("schnitt", 0.0), "spannend": ("schnitt", 0.0), "lustig": ("wipeleft", 0.3),
             "frustriert": ("fadeblack", 0.5), "chill": ("fade", 0.8)}

# Gelernte Regie-Parameter: Startwerte. regie_lernen.py verschiebt sie anhand deiner Bewertungen.
PARAMETER = {
    "puffer_vor_s": 2.5,          # vor dem ersten Kill ("abgeschnitten" -> mehr)
    "puffer_nach_s": 1.5,         # nach dem letzten Kill
    "seg_min_faktor": 1.0,        # Mindestlänge je Segment ("zu hektisch" -> länger)
    "beats_pro_schnitt": 1,       # nur auf jedem n-ten Beat schneiden ("zu hektisch" -> 2, 4)
    "dauer_faktor": 1.0,          # Ziel-Gesamtdauer ("zu lang" -> kürzer)
    "uebergang_faktor": 1.0,      # Länge der Übergänge
    "musik_pegel": 0.35,
    "stimmung_bonus": {},         # Stimmung -> Zusatzpunkte ("Stimmung getroffen" + 👍)
    "track_malus": {},            # Track-ID -> Abzug ("Musik passt nicht")
    "moment_bonus": {},           # Moment -> Zusatzpunkte (👍 +, 👎 ohne Grund −, "Clips langweilig" −−)
    "abwechslung": 0.7,           # Anteil der Punkte, den ein Moment aus dem letzten Entwurf verliert (je älter: halb)
    "max_je_match": 3,
}
ABWECHSLUNG_FENSTER = 12          # so viele letzte Entwürfe zählen für die Abwechslung


class RegieFehler(RuntimeError):
    pass


@dataclass
class Kandidat:
    schluessel: str
    datei: str
    dauer_s: float
    stimmung: str
    intensitaet: float
    punkte: float
    clip_id: int | None
    match_id: str | None
    kern: tuple[float, float]      # gewünschter Ausschnitt (Quelle, Sekunden)
    muss: tuple[float, float]      # darf nicht angeschnitten werden (erster Kill − 1 s … letzter Kill + 0,5 s)
    grund: str
    merkmale: dict = field(default_factory=dict)
    abzug: float = 0.0             # schon gezeigt (Abwechslung): Punkte, die in `punkte` schon abgezogen sind
    gezeigt: int = 0               # in wie vielen der letzten Entwürfe


# --- 1. Auswahl ----------------------------------------------------------------------

def _kern(mk: dict, dauer: float, p: dict) -> tuple[tuple[float, float], tuple[float, float], str]:
    kills = sorted(mk.get("kill_sekunden") or [])
    if kills:
        kern = (kills[0] - p["puffer_vor_s"], kills[-1] + p["puffer_nach_s"])
        muss = (kills[0] - 1.0, kills[-1] + 0.5)
        grund = f"{len(kills)} Kill(s)"
    else:
        ereignisse = [t for t in [mk.get("tod_sekunde"), *(mk.get("jubel_laut_s") or []), *(mk.get("spitzen_s") or [])]
                      if t is not None]
        mitte = ereignisse[0] if ereignisse else dauer / 2
        kern, muss = (mitte - 4.0, mitte + 3.0), (mitte - 1.0, mitte + 1.0)
        grund = "Tod" if mk.get("tod_sekunde") is not None else ("Jubel/Spitze" if ereignisse else "Mitte")
    # Nur den nutzbaren Teil der Datei (am Ende braucht der Schnitt noch Bilder, siehe plane_zeitleiste) –
    # sonst lässt ein Moment mit Action in den letzten Zehnteln jeden compose scheitern.
    nutzbar = max(0.5, dauer - 0.25)
    klemme = lambda a, b: (max(0.0, min(a, nutzbar)), max(0.0, min(b, nutzbar)))  # noqa: E731
    return klemme(*kern), klemme(*muss), grund


def gezeigte_momente(con: sqlite3.Connection, fenster: int = ABWECHSLUNG_FENSTER) -> list[list[str]]:
    """Momente der letzten Entwürfe, neuester zuerst (aus den gespeicherten Schnittlisten)."""
    ergebnis = []
    for z in con.execute("SELECT schnittliste FROM entwuerfe ORDER BY id DESC LIMIT ?", (fenster,)):
        try:
            with open(z["schnittliste"], encoding="utf-8") as f:
                ergebnis.append([s["moment"] for s in json.load(f).get("segmente", [])])
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            ergebnis.append([])  # Datei fehlt: zählt als Entwurf, aber ohne Momente
    return ergebnis


def abwechslung(frueher: list[list[str]], anteil: float) -> dict[str, tuple[float, int]]:
    """Moment -> (Anteil der Punkte, der abgezogen wird; wie oft gezeigt).
    Letzter Entwurf: der volle Anteil, davor je Entwurf die Hälfte; zusammen höchstens 1 (= alle Punkte)."""
    schon: dict[str, tuple[float, int]] = {}
    for alter, momente in enumerate(frueher):
        for m in set(momente):
            wert, n = schon.get(m, (0.0, 0))
            schon[m] = (wert + anteil * 0.5 ** alter, n + 1)
    return {m: (round(min(1.0, w), 3), n) for m, (w, n) in schon.items()}


def kandidaten(con: sqlite3.Connection, p: dict, frueher: list[list[str]] | None = None) -> list[Kandidat]:
    schon = abwechslung(frueher or [], float(p.get("abwechslung", 0.0)))
    zeilen = con.execute(
        """SELECT m.*, c.status AS clip_status, c.elo AS elo, c.punkte AS clip_punkte, c.max_gruppe AS max_gruppe,
                  c.victory_royale AS victory_royale
             FROM momente m LEFT JOIN clips c ON c.id = m.clip_id
            ORDER BY m.id"""
    ).fetchall()
    ergebnis = []
    for z in zeilen:
        if z["clip_status"] == "verworfen" or not Path(z["datei"]).is_file():
            continue
        mk = json.loads(z["merkmale"])
        dauer = float(z["ende_s"]) - float(z["start_s"])
        serie = int(z["max_gruppe"] if z["max_gruppe"] is not None else mk.get("max_gruppe", 0) or 0)
        victory = int(z["victory_royale"] or mk.get("victory_royale", 0) or 0)
        intensitaet = (KILL_PUNKTE[min(serie, 4)] + 5 * victory + STIMMUNG_WERT[z["stimmung"]]
                       + 0.5 * min(mk.get("spitzen", 0), 4) / 4 + 0.5 * min(mk.get("jubel_laut", 0), 2))
        punkte = intensitaet + float(p["stimmung_bonus"].get(z["stimmung"], 0.0))
        if z["clip_status"] in ("freigegeben", "veroeffentlicht", "im_highlight"):
            punkte += 1.0
        if z["elo"] is not None:
            punkte += (float(z["elo"]) - 1500.0) / 100.0
        punkte += float(p.get("moment_bonus", {}).get(z["schluessel"], 0.0))
        anteil, gezeigt = schon.get(z["schluessel"], (0.0, 0))
        abzug = round(anteil * max(punkte, 1.0), 2)  # auch schwache Momente (< 1 Punkt) verlieren etwas
        kern, muss, grund = _kern(mk, dauer, p)
        ergebnis.append(Kandidat(z["schluessel"], z["datei"], dauer, z["stimmung"], round(intensitaet, 2),
                                 round(punkte - abzug, 2), z["clip_id"], z["match_id"], kern, muss, grund, mk,
                                 abzug, gezeigt))
    return ergebnis


def waehle(kandidaten_: list[Kandidat], fmt: dict, p: dict) -> tuple[list[Kandidat], float, list[str]]:
    """Beste Momente, bis die Ziel-Dauer erreicht ist. Ziel richtet sich nach dem Material."""
    hinweise = []
    seg_min = fmt["seg_min_s"] * p["seg_min_faktor"]
    laenge = lambda k: min(fmt["seg_max_s"], max(seg_min, k.kern[1] - k.kern[0]))  # noqa: E731
    vorrat = sum(laenge(k) for k in kandidaten_)
    ziel = min(fmt["max_s"], max(fmt["min_s"], 0.8 * vorrat)) * p["dauer_faktor"]
    ziel = min(fmt["max_s"], max(fmt["min_s"], ziel))
    if vorrat < fmt["min_s"]:
        hinweise.append(f"nur {vorrat:.0f} s Material – kürzer als {fmt['min_s']:.0f} s")
    gewaehlt, summe, je_match = [], 0.0, {}
    for k in sorted(kandidaten_, key=lambda k: (-k.punkte, k.schluessel)):
        if summe >= ziel:
            break
        if k.match_id and je_match.get(k.match_id, 0) >= int(p["max_je_match"]):
            continue
        gewaehlt.append(k)
        summe += laenge(k)
        if k.match_id:
            je_match[k.match_id] = je_match.get(k.match_id, 0) + 1
    return gewaehlt, ziel, hinweise


# --- 2. Spannungsbogen -----------------------------------------------------------------

def bogen(gewaehlt: list[Kandidat], fmt_name: str) -> list[Kandidat]:
    if len(gewaehlt) <= 2:
        return sorted(gewaehlt, key=lambda k: k.intensitaet)
    nach_staerke = sorted(gewaehlt, key=lambda k: (-k.intensitaet, k.schluessel))
    hoehepunkt, hook, rest = nach_staerke[0], nach_staerke[1], nach_staerke[2:]
    mitte = sorted(rest, key=lambda k: (k.intensitaet, k.schluessel))
    # Atempause: ruhigster lustiger/chilliger Moment an ca. 60 % der Mitte
    pausen = [k for k in mitte if k.stimmung in ("lustig", "chill")]
    if pausen and len(mitte) >= 3 and fmt_name == "zusammenschnitt":
        pause = pausen[0]
        mitte.remove(pause)
        mitte.insert(int(round(len(mitte) * 0.6)), pause)
    reihe = [hook, *mitte, hoehepunkt]
    # Abwechslung: gleiche Stimmung oder gleiches Match direkt nacheinander -> mit einem späteren tauschen
    for i in range(2, len(reihe) - 1):
        vorher = reihe[i - 1]
        if reihe[i].stimmung == vorher.stimmung or (reihe[i].match_id and reihe[i].match_id == vorher.match_id):
            for j in range(i + 1, len(reihe) - 1):
                if reihe[j].stimmung != vorher.stimmung and reihe[j].match_id != vorher.match_id:
                    reihe[i], reihe[j] = reihe[j], reihe[i]
                    break
    return reihe


# --- 3. Musik ------------------------------------------------------------------------

def _rang(werte: list[float], wert: float) -> float:
    """Anteil der Werte, die kleiner sind (0..1) – Energie relativ zur eigenen Bibliothek."""
    if len(werte) <= 1:
        return 0.5
    return sum(1 for w in werte if w < wert) / (len(werte) - 1)


def waehle_musik(con: sqlite3.Connection, stimmung: str, gesamt_s: float, p: dict, ziel: dict | None = None
                 ) -> tuple[sqlite3.Row | None, dict[int, float]]:
    ziel = ziel or ZIEL
    tracks = con.execute("SELECT * FROM tracks WHERE beats IS NOT NULL ORDER BY id").fetchall()
    if not tracks:
        return None, {}
    energien = [float(t["energie"] or 0) for t in tracks]
    benutzt = {z["track_id"]: z["n"] for z in con.execute(
        "SELECT track_id, COUNT(*) AS n FROM entwuerfe WHERE track_id IS NOT NULL GROUP BY track_id")}
    wertung = {}
    for t in tracks:
        stimmungen = json.loads(t["stimmungen"] or "[]")
        passt = 1.0 if stimmungen[:1] == [stimmung] else 0.5 if stimmung in stimmungen else 0.0
        energie = 1.0 - abs(_rang(energien, float(t["energie"] or 0)) - ziel[stimmung]["energie"])
        tempo = max(0.0, 1.0 - abs(float(t["bpm"] or 0) - ziel[stimmung]["bpm"]) / 80)
        wert = 2.0 * passt + 1.0 * energie + 0.6 * tempo
        wert -= float(p["track_malus"].get(str(t["id"]), 0.0))
        wert -= 0.15 * benutzt.get(t["id"], 0)              # Abwechslung zwischen Entwürfen
        wert -= 0.3 * (float(t["dauer_s"] or 0) < gesamt_s)  # müsste wiederholt werden
        wertung[t["id"]] = round(wert, 3)
    beste = max(tracks, key=lambda t: (wertung[t["id"]], -t["id"]))
    return beste, wertung


def drop(verlauf: list[float]) -> float:
    """Sekunde des stärksten Anstiegs im Energie-Verlauf (Mittel der nächsten 2 s minus der letzten 4 s)."""
    besten, zeit = -1.0, 0.0
    for t in range(4, len(verlauf) - 2):
        anstieg = sum(verlauf[t:t + 2]) / 2 - sum(verlauf[t - 4:t]) / 4
        if anstieg > besten:
            besten, zeit = anstieg, float(t)
    return zeit


def naechster(werte: list[float], ziel: float) -> float:
    return min(werte, key=lambda w: abs(w - ziel)) if werte else ziel


# --- 4./5. Zeitleiste auf dem Beat, Übergänge ------------------------------------------------

def plane_zeitleiste(reihe: list[Kandidat], raster: list[float], fmt: dict, p: dict, fps: int) -> list[dict]:
    """Legt Segmentgrenzen auf Beats. Gibt Segmente mit Quelle (start/ende) und Zeitleiste (zeit_*) zurück."""
    seg_min = fmt["seg_min_s"] * p["seg_min_faktor"]
    segmente, t = [], 0.0
    for nr, k in enumerate(reihe, 1):
        # Am Dateiende 0,25 s frei lassen: Schnitt/Übergang brauchen dort noch Bilder (entwurf._griffe, UEBERHANG_S)
        nutzbar = max(0.5, k.dauer_s - 0.25)
        muss_laenge = max(0.5, k.muss[1] - k.muss[0])
        wunsch = min(fmt["seg_max_s"], max(seg_min, k.kern[1] - k.kern[0], muss_laenge))
        wunsch = min(wunsch, nutzbar)
        unten, oben = max(seg_min, muss_laenge), min(nutzbar, max(wunsch, muss_laenge) + 2.0)
        passend = [b for b in raster if unten - 1e-6 <= b - t <= oben + 1e-6]
        ende = naechster(passend, t + wunsch) if passend else t + max(min(wunsch, nutzbar), min(unten, nutzbar))
        laenge = ende - t
        # Quelle: Kern mittig, lieber etwas mehr Anlauf; die Muss-Zone bleibt immer drin
        extra = laenge - (k.kern[1] - k.kern[0])
        start = k.kern[0] - 0.6 * extra
        start = min(start, k.muss[0])
        start = max(start, k.muss[1] - laenge)
        start = max(0.0, min(start, nutzbar - laenge))
        art, dauer = UEBERGANG[k.stimmung]
        dauer = round(dauer * p["uebergang_faktor"], 3) if nr > 1 else 0.0
        segmente.append({
            "nr": nr, "moment": k.schluessel, "clip_id": k.clip_id, "match_id": k.match_id, "datei": k.datei,
            "stimmung": k.stimmung, "intensitaet": k.intensitaet, "grund": k.grund,
            "punkte": k.punkte, "abzug": k.abzug, "gezeigt": k.gezeigt,
            "quelle_start_s": round(start, 3), "quelle_ende_s": round(start + laenge, 3),
            "quelle_dauer_s": round(k.dauer_s, 3), "muss": [round(k.muss[0], 3), round(k.muss[1], 3)],
            "zeit_start": round(t, 3), "zeit_ende": round(ende, 3),
            "uebergang": {"art": art if nr > 1 else "schnitt", "dauer_s": dauer},
            "auf_beat": bool(passend),
        })
        t = ende
    # Übergänge brauchen "Griffe": die halbe Übergangsdauer vor und nach dem Segment aus der Quelle.
    # Gibt die Quelle das nicht her, wird der Übergang kürzer (bis hin zum harten Schnitt).
    for i in range(1, len(segmente)):
        vorher, jetzt_ = segmente[i - 1], segmente[i]
        verfuegbar = min(vorher["quelle_dauer_s"] - vorher["quelle_ende_s"], jetzt_["quelle_start_s"])
        d = min(jetzt_["uebergang"]["dauer_s"], 2 * max(0.0, verfuegbar))
        if d < 2.0 / fps:
            jetzt_["uebergang"] = {"art": "schnitt", "dauer_s": 0.0}
        else:
            jetzt_["uebergang"]["dauer_s"] = round(d, 3)
    return segmente


def pruefe_liste(liste: dict) -> list[str]:
    """Schema plus fachliche Regeln: Zeitleiste lückenlos, Quelle im Video, kein Kill angeschnitten, Dauer im Rahmen."""
    fehler = schema.pruefe(liste, schema.lade("regie"))
    if fehler:
        return fehler
    t = 0.0
    for s in liste["segmente"]:
        if abs(s["zeit_start"] - t) > 1e-3:
            fehler.append(f"Segment {s['nr']}: Lücke in der Zeitleiste")
        t = s["zeit_ende"]
        if s["quelle_start_s"] < -1e-3 or s["quelle_ende_s"] > s["quelle_dauer_s"] + 1e-3:
            fehler.append(f"Segment {s['nr']}: außerhalb des Videos")
        if s["quelle_start_s"] > s["muss"][0] + 1e-3 or s["quelle_ende_s"] < s["muss"][1] - 1e-3:
            fehler.append(f"Segment {s['nr']}: schneidet die Action an")
        if abs((s["zeit_ende"] - s["zeit_start"]) - (s["quelle_ende_s"] - s["quelle_start_s"])) > 1e-3:
            fehler.append(f"Segment {s['nr']}: Länge Quelle ≠ Zeitleiste")
    if abs(t - liste["dauer_s"]) > 1e-3:
        fehler.append("Gesamtdauer stimmt nicht")
    return fehler


# --- Hauptfunktion -----------------------------------------------------------------------

def ordner(konfig: Konfig) -> Path:
    return Path(str(konfig.wert("regie.ordner", "/var/lib/clip-pipeline/regie")))


def erstelle(con: sqlite3.Connection, konfig: Konfig, fmt_name: str, *, parameter: dict | None = None,
             name: str | None = None, ziel: dict | None = None, nur_matches: set[str] | None = None) -> dict:
    """nur_matches: nur Momente aus diesen Matches (z. B. ein Spielabend)."""
    if fmt_name not in FORMATE:
        raise RegieFehler(f"Unbekanntes Format {fmt_name!r}")
    fmt = FORMATE[fmt_name]
    p = {**PARAMETER, **(parameter or {})}
    fps = int(konfig.wert("regie.fps", 60 if fmt_name == "zusammenschnitt" else 30))
    frueher = gezeigte_momente(con)
    alle = [k for k in kandidaten(con, p, frueher) if nur_matches is None or k.match_id in nur_matches]
    if not alle:
        raise RegieFehler("Keine Momente mit Stimmung" + (" in diesen Matches" if nur_matches else "")
                          + " – erst `pipeline stimmung`")
    gewaehlt, ziel_s, hinweise = waehle(alle, fmt, p)
    reihe = bogen(gewaehlt, fmt_name)

    # Vorherrschende Stimmung (nach Länge gewichtet) bestimmt die Musik
    anteile: dict[str, float] = {}
    for k in reihe:
        anteile[k.stimmung] = anteile.get(k.stimmung, 0.0) + (k.kern[1] - k.kern[0])
    haupt = max(anteile, key=lambda s: (anteile[s], STIMMUNG_WERT[s]))
    track, wertung = waehle_musik(con, haupt, ziel_s, p, ziel)

    raster: list[float] = []
    versatz = 0.0
    if track is not None:
        schlaege = json.loads(track["beats"])
        # Höhepunkt (letztes Segment) beginnt ungefähr bei ziel_s minus seiner Länge
        hoehe_start = max(0.0, ziel_s - min(fmt["seg_max_s"], reihe[-1].kern[1] - reihe[-1].kern[0]))
        versatz = max(0.0, drop(json.loads(track["verlauf"] or "[]")) - hoehe_start)
        if versatz + ziel_s > float(track["dauer_s"]):
            versatz = max(0.0, float(track["dauer_s"]) - ziel_s)
        versatz = naechster([b for b in schlaege if b <= versatz + 1] or [0.0], versatz)
        schritt = max(1, int(p["beats_pro_schnitt"]))
        raster = [round(b - versatz, 3) for b in schlaege if b - versatz > 0.2][schritt - 1::schritt]
    else:
        hinweise.append("keine Musik in der Bibliothek – ohne Musik, Schnitte nicht auf dem Beat")

    segmente = plane_zeitleiste(reihe, raster, fmt, p, fps)
    # Beat-Raster kürzt Segmente -> bis zum Ziel nachlegen: erst mit Match-Grenze, notfalls ohne
    passt_nicht: list[Kandidat] = []  # eigene Liste: `alle` bleibt die ganze Auswahl (für Zählung und Hinweis)
    for mit_grenze in (True, False):
        while segmente and segmente[-1]["zeit_ende"] < ziel_s - 1e-6:
            je_match: dict[str, int] = {}
            for k in reihe:
                je_match[k.match_id or ""] = je_match.get(k.match_id or "", 0) + 1
            rest = [k for k in alle if k not in gewaehlt and k not in passt_nicht
                    and (not mit_grenze or not k.match_id or je_match.get(k.match_id, 0) < int(p["max_je_match"]))]
            if not rest:
                break
            naechster_ = max(rest, key=lambda k: (k.punkte, k.schluessel))
            neue_reihe = bogen([*gewaehlt, naechster_], fmt_name)
            neue_segmente = plane_zeitleiste(neue_reihe, raster, fmt, p, fps)
            if neue_segmente[-1]["zeit_ende"] > fmt["max_s"] + 1e-6:
                passt_nicht.append(naechster_)
                continue
            gewaehlt.append(naechster_)
            reihe, segmente = neue_reihe, neue_segmente
            if not mit_grenze and (h := f"mehr als {p['max_je_match']} Momente aus einem Match") not in hinweise:
                hinweise.append(h)
    # Zu lang (Short!)? Den Moment mit den wenigsten Punkten (inkl. Gelerntem und Abwechslung) aus der Mitte
    # streichen und neu planen – der Höhepunkt am Schluss bleibt
    while segmente and segmente[-1]["zeit_ende"] > fmt["max_s"] + 1e-6 and len(reihe) > 1:
        mitte = reihe[:-1]
        reihe.remove(min(mitte, key=lambda k: (k.punkte, k.intensitaet, k.schluessel)))
        segmente = plane_zeitleiste(reihe, raster, fmt, p, fps)
    gesamt = segmente[-1]["zeit_ende"] if segmente else 0.0
    if gesamt < fmt["min_s"] - 1e-6:
        hinweise.append(f"Dauer {gesamt:.1f} s unter {fmt['min_s']:.0f} s – zu wenig Material")
    neu = sum(1 for s in segmente if s["gezeigt"] == 0)
    if frueher and len(alle) < 3 * len(segmente):
        hinweise.append(f"nur {len(alle)} Momente zur Auswahl – für mehr Abwechslung mehr Clips analysieren")

    if name is None:
        name = basis = f"{fmt_name}-{jetzt():%Y%m%d-%H%M%S}"
        n = 1
        while con.execute("SELECT 1 FROM entwuerfe WHERE name = ?", (name,)).fetchone():
            n += 1
            name = f"{basis}-{n}"
    liste = {
        "version": 3, "art": "regie", "name": name, "format": fmt_name,
        "aufloesung": [fmt["b"], fmt["h"]], "fps": fps, "dauer_s": round(gesamt, 3),
        "stimmung": haupt, "parameter": p,
        "musik": None if track is None else {
            "track_id": track["id"], "datei": track["datei"], "titel": track["titel"], "kuenstler": track["kuenstler"],
            "quelle": track["quelle"], "bpm": track["bpm"], "start_s": round(versatz, 3),
            "pegel": float(p["musik_pegel"]), "wertung": {str(k): v for k, v in wertung.items()},
        },
        "overlay": str(konfig.wert("shorts.overlay_text", "clip-battle.de")) if fmt_name == "short" else None,
        "bogen": [s["intensitaet"] for s in segmente],
        "auswahl": {"kandidaten": len(alle), "neu": neu, "schon_gezeigt": len(segmente) - neu,
                    "abwechslung": float(p.get("abwechslung", 0.0))},
        "segmente": segmente,
        "hinweise": hinweise,
        "erstellt": iso(jetzt()),
    }
    if fehler := pruefe_liste(liste):
        raise RegieFehler("Schnittliste ungültig: " + "; ".join(fehler[:3]))
    ziel_datei = ordner(konfig) / f"{name}.json"
    ziel_datei.parent.mkdir(parents=True, exist_ok=True)
    tmp = ziel_datei.with_suffix(".tmp")
    tmp.write_text(json.dumps(liste, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(ziel_datei)
    cur = con.execute(
        """INSERT INTO entwuerfe (name, format, schnittliste, parameter, track_id, dauer_s, erstellt)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (name, fmt_name, str(ziel_datei), json.dumps(p, ensure_ascii=False), track["id"] if track else None,
         round(gesamt, 3), iso(jetzt())),
    )
    return {"entwurf": cur.lastrowid, "name": name, "format": fmt_name, "datei": str(ziel_datei),
            "dauer_s": round(gesamt, 1), "segmente": len(segmente), "stimmung": haupt,
            "musik": liste["musik"]["titel"] if track else None, "neu": neu, "hinweise": hinweise}
