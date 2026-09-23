"""Die Vorbewertung lernt aus deinen Entscheidungen – einfach und nachvollziehbar.

Idee: Findest du Clip A besser als Clip B, die Formel sieht aber B vorne, werden
die Gewichte ein kleines Stück in Richtung der Merkmale verschoben, in denen A
besser war ("paarweises Nachjustieren", ein Perzeptron auf Paaren).

Paare entstehen aus
  - Battles: Gewinner > Verlierer
  - Freigaben: jeder freigegebene Clip > jeder verworfene Clip desselben Spielabends

Sicherungen: Mindestmenge, langsam wachsendes Vertrauen, Leine um die Startgewichte
und ein Vergleich mit den Startgewichten (nie schlechter werden).
Alles wird jedes Mal komplett neu aus der Historie berechnet -> reproduzierbar.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from itertools import islice, product

from .db import BEWERTET, merkmale
from .vorbewertung import MERKMALE
from .zeit import aus_iso, iso, jetzt, spielabend


@dataclass
class Paar:
    besser: dict[str, float]
    schlechter: dict[str, float]
    art: str  # battle | freigabe


@dataclass
class Ergebnis:
    werte: dict[str, float]
    start: dict[str, float]
    datenbasis: int  # Anzahl Bewertungen
    freigaben: int
    battles: int
    vertrauen: float
    trefferquote: float | None
    trefferquote_start: float | None
    aktiv: bool
    grund: str


def score(gewichte: dict[str, float], merkmal_werte: dict[str, float]) -> float:
    return sum(gewichte.get(m, 0.0) * merkmal_werte.get(m, 0.0) for m in MERKMALE)


def trefferquote(gewichte: dict[str, float], paare: list[Paar]) -> float | None:
    """Anteil der Paare, die die Gewichte richtig herum sortieren (Gleichstand zählt halb)."""
    if not paare:
        return None
    punkte = 0.0
    for p in paare:
        differenz = score(gewichte, p.besser) - score(gewichte, p.schlechter)
        punkte += 1.0 if differenz > 1e-9 else 0.5 if abs(differenz) <= 1e-9 else 0.0
    return round(punkte / len(paare), 4)


def trainiere(paare: list[Paar], start: dict[str, float], einstellungen: dict) -> dict[str, float]:
    schritt = float(einstellungen["schritt"])
    anteil = float(einstellungen["leine_anteil"])
    minimum = float(einstellungen["leine_minimum"])
    leine = {m: max(minimum, abs(start.get(m, 0.0)) * anteil) for m in MERKMALE}
    w = {m: float(start.get(m, 0.0)) for m in MERKMALE}
    for _ in range(int(einstellungen["durchlaeufe"])):
        for p in paare:
            d = {m: p.besser.get(m, 0.0) - p.schlechter.get(m, 0.0) for m in MERKMALE}
            if sum(w[m] * d[m] for m in MERKMALE) < 1.0:  # falsch herum oder zu knapp
                for m in MERKMALE:
                    w[m] += schritt * d[m]
                    w[m] = min(start.get(m, 0.0) + leine[m], max(start.get(m, 0.0) - leine[m], w[m]))
    return {m: round(v, 4) for m, v in w.items()}


def sammle_paare(con: sqlite3.Connection, *, zonen_name: str, wechsel_stunde: int, max_pro_abend: int) -> tuple[list[Paar], int, int]:
    """Gibt (Paare, Anzahl entschiedener Clips, Anzahl entschiedener Battles) zurück."""
    paare: list[Paar] = []
    battles = con.execute(
        """SELECT b.ergebnis, a.merkmale AS ma, c.merkmale AS mb
             FROM battles b JOIN clips a ON a.id = b.clip_a JOIN clips c ON c.id = b.clip_b
            WHERE b.ergebnis IN ('a', 'b') ORDER BY b.entschieden, b.id"""
    ).fetchall()
    for b in battles:
        ma, mb = json.loads(b["ma"]), json.loads(b["mb"])
        paare.append(Paar(ma, mb, "battle") if b["ergebnis"] == "a" else Paar(mb, ma, "battle"))

    platzhalter = ", ".join("?" for _ in BEWERTET)
    entschieden = con.execute(
        f"""SELECT id, status, start_utc, merkmale FROM clips
             WHERE status IN ({platzhalter}, 'verworfen') ORDER BY id""",
        BEWERTET,
    ).fetchall()
    abende: dict[str, dict[str, list]] = {}
    for c in entschieden:
        abend = spielabend(aus_iso(c["start_utc"]), zonen_name, wechsel_stunde).isoformat()
        seite = "gut" if c["status"] in BEWERTET else "schlecht"
        abende.setdefault(abend, {"gut": [], "schlecht": []})[seite].append(merkmale(c))
    for abend in sorted(abende):
        gruppe = abende[abend]
        for gut, schlecht in islice(product(gruppe["gut"], gruppe["schlecht"]), max_pro_abend):
            paare.append(Paar(gut, schlecht, "freigabe"))
    return paare, len(entschieden), len(battles)


def berechne(con: sqlite3.Connection, konfig) -> Ergebnis:
    einstellungen = konfig.abschnitt("lernen")
    start = {m: float(konfig.wert(f"vorbewertung.startgewichte.{m}", 0.0)) for m in MERKMALE}
    paare, n_freigaben, n_battles = sammle_paare(
        con,
        zonen_name=konfig.wert("zeit.zeitzone", "Europe/Berlin"),
        wechsel_stunde=int(konfig.wert("zeit.tageswechsel_stunde", 6)),
        max_pro_abend=int(einstellungen["max_paare_pro_abend"]),
    )
    n = n_freigaben + n_battles
    vertrauen = min(1.0, n / max(1, int(einstellungen["voll_vertrauen"])))
    tq_start = trefferquote(start, paare)

    def ergebnis(werte, aktiv, grund, tq):
        return Ergebnis(werte, start, n, n_freigaben, n_battles, round(vertrauen, 3), tq, tq_start, aktiv, grund)

    if n < int(einstellungen["mindestens"]):
        return ergebnis(dict(start), False, f"noch {int(einstellungen['mindestens']) - n} Bewertungen bis zum Lernen", tq_start)
    gelernt = trainiere(paare, start, einstellungen)
    werte = {m: round(start[m] + vertrauen * (gelernt[m] - start[m]), 4) for m in MERKMALE}
    tq = trefferquote(werte, paare)
    if tq is not None and tq_start is not None and tq < tq_start:
        return ergebnis(dict(start), False, "gelernte Gewichte wären schlechter als die Startgewichte", tq_start)
    return ergebnis(werte, True, "aktiv", tq)


def aktuelle(con: sqlite3.Connection, konfig) -> tuple[int, dict[str, float]]:
    """Neueste gespeicherte Gewichte, sonst die Startgewichte (Version 0)."""
    zeile = con.execute("SELECT version, werte FROM gewichte ORDER BY version DESC LIMIT 1").fetchone()
    if zeile:
        return int(zeile["version"]), {k: float(v) for k, v in json.loads(zeile["werte"]).items()}
    return 0, {m: float(konfig.wert(f"vorbewertung.startgewichte.{m}", 0.0)) for m in MERKMALE}


def aktualisiere(con: sqlite3.Connection, konfig) -> tuple[int, Ergebnis]:
    """Berechnet neu und speichert eine neue Version, falls sich etwas geändert hat."""
    ergebnis = berechne(con, konfig)
    version, bisher = aktuelle(con, konfig)
    if all(abs(bisher.get(m, 0.0) - ergebnis.werte[m]) < 1e-6 for m in MERKMALE):
        return version, ergebnis
    version += 1
    con.execute(
        """INSERT INTO gewichte (version, werte, datenbasis, vertrauen, trefferquote, trefferquote_start, erstellt)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (version, json.dumps(ergebnis.werte), ergebnis.datenbasis, ergebnis.vertrauen,
         ergebnis.trefferquote, ergebnis.trefferquote_start, iso(jetzt())),
    )
    return version, ergebnis
