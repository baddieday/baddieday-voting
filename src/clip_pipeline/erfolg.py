"""Erfolg ehrlich messen (Mehrbenutzer, Stufe 4, M155–M164): `pipeline erfolg` – nur lesen, keine Sperre, weckt nie.

Abnahme (Florian): „Das System zeigt belegbare Unterschiede zwischen Strategien, ohne fehlende Daten zu erfinden.“

Woher die Zahlen kommen: nur aus der eingefrorenen Wochen-Note jedes Posts (posts.score_teile – die Messung nächst
Tag 7, einmal gesetzt, nie überschrieben, relativ zu deinen letzten 20 Posts derselben Plattform; publikum.score_fuer).
Nie aus KI-Note, Cutter-Note, ✅/❌ oder Erwartung. Die laufende Zuschauer-Note (audience_ergebnisse) bleibt das
Lernsignal (M155). Keine Tabelle: alles wird bei jedem Aufruf aus den eingefrorenen Eingaben gerechnet und trägt
ERFOLG_VERSION und die benutzten Gewichte (M156) – jederzeit nachrechenbar.

Einheit (M158): ein Short je Plattform. Fassungen eines Videos (Familie = kleinste Nummer aus dem Entwurf und seinem
Parameter „ersetzt“, wie szenen.verlauf) zählen einmal; ein Crosspost ist je Plattform eine eigene Einheit, Plattformen
werden nie zusammengeworfen. Nicht gezählt, jeweils mit Grund: Clip-Posts, kein Short, Fail-Video, „Basis zu klein“
(Platzhalter-0 der ersten 5 Posts je Plattform), Messung außerhalb Tag 4–10, unlesbare Score-Teile, weitere Fassung.
Noch ohne Wochenzahlen = „wartet“ (nach 14 Tagen ohne: nicht gezählt).

Drei Ziele je Einheit – gemessen · zu wenig Vergleich · wartet · nicht gemessen (Grund), nie eine 0, nie geschätzt:
  Zuschauer  aus den Teilen z_r (Bindung), z_e (Reaktionen), z_v (Reichweite) mit [publikum.gewichte] über
             publikum._gewichte – bei unveränderten Gewichten genau posts.score
  Follower   neue Follower je 1000 Views aus derselben Messung, robust gegen frühere Posts derselben Plattform mit
             Follower-Zahl (mindestens 5, kleinste Streuung 0,5) – TikTok liefert sie nicht
  Webseite   clip-battle.de zählt noch nicht (der Zähler kommt erst nach Florians OK)
Gesamt = gewichteter Mittelwert über die gemessenen Ziele ([erfolg.gewichte], M157). Verglichen wird nur innerhalb der
größten Gruppe mit denselben gemessenen Zielen.

Vergleich (M159–M161): fest m = 9 je Plattform – Aufbau 4 × eine gegen den Rest, Tempo, Zeitlupe, Länge unter 45 /
45–60 / über 60 s je gegen den Rest. Je Seite mindestens 8 Videos, Ø-Länge beider Seiten höchstens 15 % auseinander
(außer beim Merkmal Länge). Welch-Intervall mit t-Quantil nach Cornish-Fisher, Bonferroni über m = 9, 1 % Irrtum je
Plattform und Auswertung. „Belegt“ nur fürs Gesamtziel und nur, wenn das Intervall die 0 nicht enthält; die Teilziele
stehen nur beschreibend daneben (n und Median je Seite). Wortwahl: „kommt besser an“ (ein Zusammenhang, kein Beweis der
Ursache) und „sehr wahrscheinlich kein Zufall“ statt einer Prozentzahl.
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import statistics
from collections import Counter
from datetime import datetime, timedelta

from . import geschmack, publikum, publikum_adapter, szenen
from .bot.aktionen import PLATTFORM_NAMEN
from .konfig import Konfig, KonfigFehler
from .zeit import aus_iso, jetzt

ERFOLG_VERSION = 1
ZIELE = ("zuschauer", "follower", "webseite")            # gewichtet in [erfolg.gewichte]
TEILE = ("bindung", "reaktionen", "reichweite")           # Teile des Ziels Zuschauer, nur beschreibend
Z_TEIL = {"bindung": "z_r", "reaktionen": "z_e", "reichweite": "z_v"}
# Die Konstanten des Vergleichs stehen nur hier, nicht in der Konfig (M160): gelockert gäbe es wieder viele falsche
# Gewinner. m bleibt fest, damit ein Urteil nicht kippt, nur weil andere Vergleiche Daten bekommen.
VERGLEICHE = (("aufbau", "montage"), ("aufbau", "story"), ("aufbau", "steigerung"), ("aufbau", "kino"),
              ("tempo", "schnell"), ("zeitlupe", "viel"), ("laenge", "kurz"), ("laenge", "mittel"), ("laenge", "lang"))
M = 9
ALPHA = 0.01                    # Irrtum je Plattform und Auswertung, auf die m Vergleiche verteilt
MINDESTENS = 8                  # Videos je Seite
LAENGE_TOLERANZ = 0.15          # Ø-Länge beider Seiten höchstens 15 % auseinander
MESSALTER_TAGE = (4.0, 10.0)    # nur Wochenzahlen aus diesem Alter sind vergleichbar
WARTET_TAGE = 14                # so lange „wartet“ ein Post ohne Wochenzahlen, danach zählt er nicht
FOLLOWER_MAD_MINIMUM = 0.5      # kleinste Streuung der Follower je 1000 Views

# Urteile eines Vergleichs
BESSER, SCHLECHTER = "belegt besser", "belegt schlechter"
KEIN_UNTERSCHIED, ZU_WENIG, NICHT_VERGLEICHBAR = "kein Unterschied belegt", "noch zu wenig Videos", \
    "nicht vergleichbar (Länge)"
# Status eines Ziels
GEMESSEN, WENIG_VERGLEICH, WARTET, NICHT_GEMESSEN = "gemessen", "zu wenig Vergleich", "wartet", "nicht gemessen"
# Hinweise ohne Netz (wie geschmack.lehrer_zeile): wer holt die Zuschauerzahlen ab?
FREUND_OHNE_ABRUF = "Zuschauerzahlen werden bei dir noch nicht abgeholt"
TIKTOK_FEHLT = "TikTok nicht verbunden – einmal /tiktok"

# Nicht gezählt – der Grund steht so in der JSON-Zeile, die Erklärung im Text
CLIP_POST, KEIN_SHORT, FAIL, ENTWURF_FEHLT = "Clip-Post", "kein Short", "Fail-Video", "Entwurf fehlt"
BASIS_ZU_KLEIN = publikum.VERMERK_BASIS_ZU_KLEIN
MESSALTER, UNLESBAR = "Messung außerhalb Tag 4–10", "Score-Teile unlesbar"
OHNE_WOCHENZAHLEN, FASSUNG = f"keine Wochenzahlen nach {WARTET_TAGE} Tagen", "weitere Fassung"
OHNE_GEWICHT = "kein gewichtetes Ziel gemessen"
ERKLAERUNG = {CLIP_POST: "Einzelclip aus dem Clip-Bot", KEIN_SHORT: "z. B. das 2-Wochen-Video",
              FAIL: "reines Fail-Video", ENTWURF_FEHLT: "der Entwurf zum Post fehlt",
              BASIS_ZU_KLEIN: "die ersten 5 Videos je Plattform sind nur der Vergleich",
              MESSALTER: "die Wochenzahl stammt nicht aus Tag 4–10", UNLESBAR: "die gespeicherte Wochen-Note ist kaputt",
              OHNE_WOCHENZAHLEN: "nicht hochgeladen oder nicht gefunden?",
              FASSUNG: "eine andere Fassung desselben Videos zählt schon", OHNE_GEWICHT: "alle haben Gewicht 0"}

# Texte ohne Fachbegriffe
ZIEL_NAMEN = {"zuschauer": "Zuschauer", "follower": "Neue Follower", "webseite": "Besuche auf clip-battle.de",
              "bindung": "Wie lange geschaut wird", "reaktionen": "Reaktionen je View", "reichweite": "Reichweite"}
OFFEN_NAMEN = {"bindung": "wie lange geschaut wird", "follower": "neue Follower",
               "webseite": "Besuche auf clip-battle.de"}
LAENGEN = {"kurz": "Videos unter 45 s", "mittel": "Videos mit 45–60 s", "lang": "Videos über 60 s"}
# (Merkmal, Wahl) → (Subjekt am Satzanfang, Mehrzahl?, womit verglichen wird)
SATZ = {**{("aufbau", o): (geschmack.NAMEN[("aufbau", o)], False, "die anderen Aufbauten")
           for o in geschmack.KNOEPFE["aufbau"]},
        ("tempo", "schnell"): ("Schnelle Schnitte", True, "ruhige Schnitte"),
        ("tempo", "ruhig"): ("Ruhige Schnitte", True, "schnelle Schnitte"),
        ("zeitlupe", "viel"): ("Viel Zeitlupe", False, "wenig Zeitlupe"),
        ("zeitlupe", "wenig"): ("Wenig Zeitlupe", False, "viel Zeitlupe"),
        ("laenge", "kurz"): (LAENGEN["kurz"], True, "längere Videos"),
        ("laenge", "mittel"): (LAENGEN["mittel"], True, "kürzere und längere Videos"),
        ("laenge", "lang"): (LAENGEN["lang"], True, "kürzere Videos")}


def laenge_band(dauer_s: float) -> str:
    """„kurz“ unter 45 s, „mittel“ 45–60 s, „lang“ über 60 s."""
    return "kurz" if dauer_s < 45 else "mittel" if dauer_s <= 60 else "lang"


def gewichte(konfig: Konfig) -> dict[str, float]:
    """[erfolg.gewichte] als {"zuschauer", "follower", "webseite"}. Fehlt die Tabelle oder ein Ziel, steht ein
    unbekannter Name darin, ist ein Wert keine endliche Zahl, negativ, oder die Summe 0: KonfigFehler (Exit 2)."""
    tabelle = konfig.wert("erfolg.gewichte")
    if not isinstance(tabelle, dict):
        raise KonfigFehler("[erfolg.gewichte] fehlt oder ist keine Tabelle (Standard steht in config/pipeline.toml)")
    if unbekannt := sorted(set(tabelle) - set(ZIELE)):
        raise KonfigFehler(f"[erfolg.gewichte].{unbekannt[0]} ist unbekannt (erlaubt: {', '.join(ZIELE)})")
    werte: dict[str, float] = {}
    for ziel in ZIELE:
        if ziel not in tabelle:
            raise KonfigFehler(f"[erfolg.gewichte].{ziel} fehlt (Standard steht in config/pipeline.toml)")
        wert = tabelle[ziel]
        if isinstance(wert, bool) or not isinstance(wert, (int, float)) or not math.isfinite(wert):
            raise KonfigFehler(f"[erfolg.gewichte].{ziel} = {wert!r} ist keine Zahl")
        if wert < 0:
            raise KonfigFehler(f"[erfolg.gewichte].{ziel} = {wert} ist negativ")
        werte[ziel] = float(wert)
    if not sum(werte.values()) > 0:
        raise KonfigFehler("[erfolg.gewichte]: die Summe der Gewichte muss größer als 0 sein")
    return werte


# --- Statistik (nur Standardbibliothek) ----------------------------------------------------------------------------

def t_quantil(p: float, nu: float) -> float:
    """t-Quantil nach Cornish-Fisher. Bei p = 1 − 0,01/18 ab ν = 7 höchstens 0,1 % zu klein (nachgerechnet); mindestens
    8 Videos je Seite geben ν ≥ 7."""
    z = statistics.NormalDist().inv_cdf(p)
    return (z + (z**3 + z) / (4 * nu) + (5 * z**5 + 16 * z**3 + 3 * z) / (96 * nu**2)
            + (3 * z**7 + 19 * z**5 + 17 * z**3 - 15 * z) / (384 * nu**3)
            + (79 * z**9 + 776 * z**7 + 1482 * z**5 - 1920 * z**3 - 945 * z) / (92160 * nu**4))


def welch(a: list[float], b: list[float], p: float) -> tuple[float, float, float] | None:
    """(Unterschied der Mittelwerte a − b, untere Grenze, obere Grenze) – zweiseitig mit dem Quantil p. Je Seite
    mindestens 2 Werte. None, wenn beide Seiten gar nicht streuen: dann ist nichts belegbar."""
    va, vb = statistics.variance(a) / len(a), statistics.variance(b) / len(b)
    d = statistics.fmean(a) - statistics.fmean(b)
    se = math.sqrt(va + vb)
    if not se > 0:
        return None
    nu = (va + vb) ** 2 / (va**2 / (len(a) - 1) + vb**2 / (len(b) - 1))
    t = t_quantil(p, max(nu, min(len(a), len(b)) - 1))
    return d, d - t * se, d + t * se


def vergleiche(einheiten: list[dict]) -> list[dict]:
    """Die m = 9 festen Vergleiche über Einheiten mit denselben gemessenen Zielen. Je Vergleich: Merkmal, Wahl,
    Gegenseite („rest“ oder die andere Wahl), n je Seite und das Urteil; gerechnet: Unterschied der Mittelwerte im
    Gesamtziel mit Grenzen; zu wenig: wie viele Videos mindestens fehlen. Dazu je Teilziel n und Median je Seite."""
    p = 1 - ALPHA / (2 * M)
    befunde = []
    for merkmal, wahl in VERGLEICHE:
        a = [e for e in einheiten if e["merkmale"].get(merkmal) == wahl]
        b = [e for e in einheiten if e["merkmale"].get(merkmal) not in (None, wahl)]
        optionen = geschmack.KNOEPFE.get(merkmal, ())
        gegen = next(o for o in optionen if o != wahl) if len(optionen) == 2 else "rest"
        befund: dict = {"merkmal": merkmal, "wahl": wahl, "gegen": gegen, "n": [len(a), len(b)]}
        la, lb = (statistics.fmean(e["dauer"] for e in seite) if seite else 0.0 for seite in (a, b))
        if len(a) < MINDESTENS or len(b) < MINDESTENS:
            befund.update(status=ZU_WENIG, fehlen=max(0, MINDESTENS - len(a)) + max(0, MINDESTENS - len(b)))
        elif merkmal != "laenge" and max(la, lb) > (1 + LAENGE_TOLERANZ) * min(la, lb):
            befund.update(status=NICHT_VERGLEICHBAR, laenge_s=[round(la, 1), round(lb, 1)])
        elif (ergebnis := welch([e["werte"]["gesamt"] for e in a], [e["werte"]["gesamt"] for e in b], p)) is None:
            befund.update(status=KEIN_UNTERSCHIED)
        else:
            d, unten, oben = ergebnis
            befund.update(status=BESSER if unten > 0 else SCHLECHTER if oben < 0 else KEIN_UNTERSCHIED,
                          unterschied=round(d, 3), von=round(unten, 3), bis=round(oben, 3))
        teile = {}
        for teil in (*TEILE, "follower", "webseite"):
            wa, wb = ([e["werte"][teil] for e in seite if e["werte"][teil] is not None] for seite in (a, b))
            if wa and wb:
                teile[teil] = {"n": [len(wa), len(wb)],
                               "median": [round(statistics.median(wa), 3), round(statistics.median(wb), 3)]}
        befund["teilziele"] = teile
        befunde.append(befund)
    return befunde


# --- Einheiten -----------------------------------------------------------------------------------------------------

def _zahl(wert) -> float | None:
    if isinstance(wert, bool) or not isinstance(wert, (int, float)) or not math.isfinite(wert):
        return None
    return float(wert)


def _teile(text: str | None) -> dict | None:
    try:
        teile = json.loads(text or "")
    except (TypeError, ValueError):
        return None
    return teile if isinstance(teile, dict) else None


def _follower_raten(con: sqlite3.Connection, messungen: dict[int, int]) -> dict[int, float]:
    """{post_id: neue Follower je 1000 Views} aus der eingefrorenen Messung – nur mit Follower-Zahl und Views > 0."""
    ids = sorted(set(messungen.values()))
    zahlen: dict[int, tuple] = {}
    for start in range(0, len(ids), 500):
        stueck = ids[start:start + 500]
        for z in con.execute(f"SELECT id, views, follows FROM publikum_messungen WHERE id IN "
                             f"({','.join('?' for _ in stueck)})", stueck):
            zahlen[int(z["id"])] = (z["views"], z["follows"])
    raten = {}
    for post_id, mid in messungen.items():
        views, follows = zahlen.get(mid, (None, None))
        if follows is not None and views:
            raten[post_id] = 1000.0 * follows / views
    return raten


def _youtube_verbunden() -> bool:
    """Ohne Netz wie publikum_adapter._token: Access-Token oder Refresh-Token mit Client in der Umgebung (.env)."""
    if os.environ.get("YOUTUBE_ACCESS_TOKEN", "").strip():
        return True
    return all(os.environ.get(f"YOUTUBE_{n}", "").strip() for n in ("REFRESH_TOKEN", "CLIENT_ID", "CLIENT_SECRET"))


def _grund(plattform: str, ziel: str) -> str:
    """Warum ein Ziel auf dieser Plattform nicht gemessen ist – nur was sicher stimmt."""
    if ziel == "webseite":
        return "clip-battle.de zählt noch nicht"
    if plattform == "tiktok":
        return {"bindung": "TikTok liefert keine Wiedergabezeit",
                "follower": "TikTok liefert keine Follower je Video"}.get(ziel, "keine Zahl dafür in den Messungen")
    if plattform == "youtube" and not _youtube_verbunden():
        return "YouTube nicht verbunden"
    return "keine Zahl dafür in den Messungen"


def _weg(z: sqlite3.Row, teile: dict | None, zeit: datetime) -> str | None:
    """Warum ein Post keine Einheit ist (WARTET: noch ohne Wochenzahlen) – None, wenn er zählen kann."""
    if z["art"] != "entwurf":
        return CLIP_POST
    if z["e_id"] is None:
        return ENTWURF_FEHLT
    if z["format"] != "short":
        return KEIN_SHORT
    if z["variante"] == "fail":
        return FAIL
    if z["bewertet_utc"] is None or z["score"] is None:
        try:
            jung = aus_iso(z["gepostet_utc"]) >= zeit - timedelta(days=WARTET_TAGE)
        except (TypeError, ValueError):
            return UNLESBAR
        return WARTET if jung else OHNE_WOCHENZAHLEN
    if teile is None:
        return UNLESBAR
    if BASIS_ZU_KLEIN in (teile.get("vermerke") or []):
        return BASIS_ZU_KLEIN
    alter = _zahl(teile.get("messung_alter_tage"))
    if (alter is None or _zahl(teile.get("z_e")) is None or _zahl(teile.get("z_v")) is None
            or (teile.get("z_r") is not None and _zahl(teile.get("z_r")) is None)
            or (_zahl(z["dauer_s"]) or 0) <= 0):
        return UNLESBAR
    if not MESSALTER_TAGE[0] <= alter <= MESSALTER_TAGE[1]:
        return MESSALTER
    return None


def _einheit(z: sqlite3.Row, teile: dict, konfig: Konfig, w: dict[str, float], follower: dict) -> dict:
    """Eine zählende Einheit: Werte je Ziel und Teilziel (None = nicht gemessen), Status, Gesamt und Merkmale."""
    z_r = _zahl(teile.get("z_r"))
    werte: dict = {teil: _zahl(teile.get(name)) for teil, name in Z_TEIL.items()}
    gw = publikum._gewichte(konfig, mit_r=z_r is not None)   # wie publikum.score_fuer: ohne Bindung 0,6/0,4
    werte["zuschauer"] = sum(gw[c] * werte[t] for c, t in (("r", "bindung"), ("e", "reaktionen"), ("v", "reichweite"))
                             if werte[t] is not None)
    status = {"zuschauer": GEMESSEN, "reaktionen": GEMESSEN, "reichweite": GEMESSEN, "webseite": NICHT_GEMESSEN,
              "bindung": GEMESSEN if z_r is not None else
              WENIG_VERGLEICH if _zahl(teile.get("r")) is not None else NICHT_GEMESSEN}
    werte["follower"] = werte["webseite"] = None
    eigen = follower["raten"].get(z["id"])
    if eigen is None:
        status["follower"] = NICHT_GEMESSEN
    else:
        basis = [r for g, pid, r in follower["liste"].get(z["plattform"], [])
                 if g < z["gepostet_utc"] and pid != z["id"]][-follower["fenster"]:]
        if len(basis) < publikum.MINDEST_BASIS:
            status["follower"] = WENIG_VERGLEICH
        else:
            status["follower"] = GEMESSEN
            werte["follower"] = publikum.robust_z(eigen, basis, FOLLOWER_MAD_MINIMUM)[0]
    abdeckung = tuple(ziel for ziel in ZIELE if status[ziel] == GEMESSEN and w[ziel] > 0)
    summe = sum(w[ziel] for ziel in abdeckung)
    werte["gesamt"] = sum(w[ziel] * werte[ziel] for ziel in abdeckung) / summe if summe > 0 else None
    dauer = float(z["dauer_s"])
    return {"post": int(z["id"]), "dauer": dauer, "abdeckung": abdeckung, "werte": werte, "status": status,
            "merkmale": {**geschmack._wahl_aus(z["parameter"]), "laenge": laenge_band(dauer)}}


def _ziel_status(einheiten: list[dict], ziel: str, plattform: str) -> dict:
    gemessen = sum(1 for e in einheiten if e["status"][ziel] == GEMESSEN)
    if gemessen:
        return {"status": GEMESSEN, "videos": gemessen}
    wenig = sum(1 for e in einheiten if e["status"][ziel] == WENIG_VERGLEICH)
    if wenig:
        return {"status": WENIG_VERGLEICH, "videos": wenig}
    return {"status": NICHT_GEMESSEN, "grund": _grund(plattform, ziel)}


def _zuschauer_status(n: int, weg: Counter, wartet: int, plattform: str, abruf: str | None) -> dict:
    if n:
        return {"status": GEMESSEN, "videos": n}
    if weg[BASIS_ZU_KLEIN]:
        return {"status": WENIG_VERGLEICH, "videos": weg[BASIS_ZU_KLEIN]}
    if wartet:
        return {"status": WARTET, "videos": wartet}
    if abruf == FREUND_OHNE_ABRUF or (abruf and plattform == "tiktok"):
        grund = abruf
    elif plattform == "youtube" and not _youtube_verbunden():
        grund = "YouTube nicht verbunden"
    else:
        grund = "noch kein Video mit fertigen Wochenzahlen"
    return {"status": NICHT_GEMESSEN, "grund": grund}


def einheiten(con: sqlite3.Connection, konfig: Konfig, zeit: datetime | None = None,
              w: dict[str, float] | None = None) -> dict[str, dict]:
    """Je Plattform (eingerichtete zuerst, dazu jede mit Posts): {"einheiten": [Einheit …], "wartet": n,
    "weg": Counter(Grund → Anzahl)}. Eine Einheit: {"post", "dauer", "abdeckung" (gemessene Ziele mit Gewicht > 0),
    "werte" (gesamt, zuschauer, Teilziele, follower, webseite – None = nicht gemessen), "status", "merkmale"}.
    Nur lesen; zeit (Standard jetzt): bis wann ein Post ohne Wochenzahlen „wartet“."""
    zeit = zeit or jetzt()
    w = w or gewichte(konfig)
    fenster = int(publikum.einstellung(konfig, "fenster"))
    zeilen = con.execute("""SELECT p.id, p.art, p.plattform, p.entwurf_id, p.gepostet_utc, p.dauer_s, p.score,
                                   p.score_teile, p.bewertet_utc, e.id AS e_id, e.format, e.variante, e.parameter
                              FROM posts p LEFT JOIN entwuerfe e ON e.id = p.entwurf_id
                             ORDER BY p.gepostet_utc, p.id""").fetchall()
    teile = {z["id"]: _teile(z["score_teile"]) for z in zeilen if z["bewertet_utc"] is not None}
    messungen = {pid: int(t["messung_id"]) for pid, t in teile.items()
                 if t is not None and isinstance(t.get("messung_id"), int) and not isinstance(t["messung_id"], bool)}
    raten = _follower_raten(con, messungen)
    liste: dict[str, list] = {}
    for z in zeilen:   # chronologisch: frühere Posts je Plattform mit Follower-Zahl (für die Vergleichsbasis)
        if z["id"] in raten:
            liste.setdefault(z["plattform"], []).append((z["gepostet_utc"], z["id"], raten[z["id"]]))
    follower = {"raten": raten, "liste": liste, "fenster": fenster}

    eingerichtet = publikum.post_plattformen(konfig)
    namen = [p for p in publikum.PLATTFORMEN if p in eingerichtet or any(z["plattform"] == p for z in zeilen)]
    namen += sorted({z["plattform"] for z in zeilen} - set(namen))
    roh = {p: {"einheiten": [], "wartet": 0, "weg": Counter()} for p in namen}
    familien: set[tuple[str, int]] = set()
    for z in zeilen:
        pl = roh[z["plattform"]]
        grund = _weg(z, teile.get(z["id"]), zeit)
        if grund == WARTET:
            pl["wartet"] += 1
            continue
        if grund:
            pl["weg"][grund] += 1
            continue
        familie = (z["plattform"], min([int(z["entwurf_id"]), *szenen._ersetzt(z["parameter"])]))
        if familie in familien:
            pl["weg"][FASSUNG] += 1
            continue
        einheit = _einheit(z, teile[z["id"]], konfig, w, follower)
        if einheit["werte"]["gesamt"] is None:
            pl["weg"][OHNE_GEWICHT] += 1
            continue
        familien.add(familie)
        pl["einheiten"].append(einheit)
    return roh


def auswertung(con: sqlite3.Connection, konfig: Konfig, zeit: datetime | None = None) -> dict:
    """`pipeline erfolg`: nur lesen. Die JSON-Zeile – version, gewichte, m, freund, abruf und je Plattform: einheiten,
    wartet, nicht_gezaehlt (Grund → Anzahl), ziele und teilziele mit Status, vergleich (Videos und Ziele der
    verglichenen Gruppe) und befunde (die m Vergleiche). zeit (Standard jetzt): bis wann ein Post „wartet“.
    KonfigFehler bei kaputten [erfolg.gewichte] oder [publikum]-Werten (Exit 2)."""
    w = gewichte(konfig)
    roh = einheiten(con, konfig, zeit, w)
    freund = konfig.instanz is not None
    abruf = FREUND_OHNE_ABRUF if freund else (TIKTOK_FEHLT if "tiktok" in publikum.post_plattformen(konfig)
                                              and not publikum_adapter.tiktok_verbunden(konfig) else None)
    plattformen = {}
    for name, pl in roh.items():
        alle = pl["einheiten"]
        gruppen: dict[tuple, list] = {}
        for e in alle:
            gruppen.setdefault(e["abdeckung"], []).append(e)
        abdeckung, gruppe = max(gruppen.items(), key=lambda kv: (len(kv[1]), len(kv[0]), kv[0])) if gruppen else \
            ((), [])
        plattformen[name] = {
            "einheiten": len(alle), "wartet": pl["wartet"], "nicht_gezaehlt": dict(sorted(pl["weg"].items())),
            "ziele": {"zuschauer": _zuschauer_status(len(alle), pl["weg"], pl["wartet"], name, abruf),
                      **{ziel: _ziel_status(alle, ziel, name) for ziel in ("follower", "webseite")}},
            "teilziele": {teil: _ziel_status(alle, teil, name) for teil in TEILE} if alle else {},
            "vergleich": {"videos": len(gruppe), "ziele": list(abdeckung), "andere_ziele": len(alle) - len(gruppe)},
            "befunde": vergleiche(gruppe)}
    return {"version": ERFOLG_VERSION, "gewichte": w, "m": M, "irrtum": ALPHA, "mindestens": MINDESTENS,
            "freund": freund, "abruf": abruf, "plattformen": plattformen}


# --- Texte ---------------------------------------------------------------------------------------------------------

def _videos(n: int) -> str:
    return f"{n} Video{'s' if n != 1 else ''}"


def _plattform_name(plattform: str) -> str:
    return PLATTFORM_NAMEN.get(plattform, plattform)


def satz(befund: dict, wo: str = "bei den Zuschauern") -> str:
    """„Aufbau „erzählt“ kommt bei den Zuschauern besser an als die anderen Aufbauten (14 gegen 27 Videos)“ – bei zwei
    Wahlen (Tempo, Zeitlupe) immer von der besseren aus gesagt."""
    wahl, (na, nb), besser = befund["wahl"], befund["n"], befund["status"] == BESSER
    if befund["gegen"] != "rest" and not besser:
        wahl, na, nb, besser = befund["gegen"], nb, na, True
    subjekt, mehrzahl, gegen = SATZ[(befund["merkmal"], wahl)]
    return (f"{subjekt} {'kommen' if mehrzahl else 'kommt'} {wo} {'besser' if besser else 'schlechter'} an als "
            f"{gegen} ({na} gegen {nb} Videos)")


def _wo(pl: dict) -> str:
    return "bei den Zuschauern" if pl["vergleich"]["ziele"] == ["zuschauer"] else "insgesamt"


def _befund_text(befund: dict, wo: str) -> str:
    subjekt, _, gegen = SATZ[(befund["merkmal"], befund["wahl"])]
    na, nb = befund["n"]
    if befund["status"] in (BESSER, SCHLECHTER):
        return f"📌 Belegt: {satz(befund, wo)} – sehr wahrscheinlich kein Zufall."
    titel = f"· {subjekt} gegen {gegen}"
    if befund["status"] == ZU_WENIG:
        return f"{titel}: noch zu wenig Videos ({na} gegen {nb}) – frühestens nach {befund['fehlen']} weiteren."
    if befund["status"] == NICHT_VERGLEICHBAR:
        la, lb = (f"{x:.0f}" for x in befund["laenge_s"])
        return f"{titel}: nicht vergleichbar – die Videos sind im Schnitt verschieden lang ({la} gegen {lb} s)."
    return f"{titel}: noch kein Unterschied sicher ({na} gegen {nb} Videos)."


def _status_text(stand: dict) -> str:
    if stand["status"] == NICHT_GEMESSEN:
        return f"nicht gemessen ({stand['grund']})"
    if stand["status"] == WENIG_VERGLEICH:
        return f"zu wenig Vergleich ({_videos(stand['videos'])}, es braucht 5 frühere)"
    if stand["status"] == WARTET:
        return f"wartet auf die Wochenzahlen ({_videos(stand['videos'])})"
    return f"gemessen ({_videos(stand['videos'])})"


def text(bericht: dict) -> str:
    """Der Bericht für `pipeline erfolg` (stderr) – ohne Fachbegriffe."""
    w = bericht["gewichte"]
    zeilen = [f"📊 Erfolg ehrlich gemessen (Version {bericht['version']}) – es zählen nur fertige Wochenzahlen, "
              "nichts ist geschätzt.",
              "Gewichtung: " + " · ".join(f"{ZIEL_NAMEN[z]} " + f"{w[z]:g}".replace(".", ",") for z in ZIELE)
              + " – gerechnet wird nur mit dem, was gemessen ist."]
    if bericht["abruf"]:
        zeilen.append(f"⚠️ {bericht['abruf']}")
    if not bericht["plattformen"]:
        zeilen.append("Noch keine Posts und keine Plattform eingerichtet ([publikum].plattformen).")
    for plattform, pl in bericht["plattformen"].items():
        kopf = f"{_plattform_name(plattform)}: {_videos(pl['einheiten'])} mit fertigen Wochenzahlen"
        if pl["wartet"]:
            kopf += f" · {pl['wartet']} warten noch auf ihre Wochenzahlen"
        zeilen += ["", kopf]
        if pl["nicht_gezaehlt"]:
            zeilen.append("  Nicht gezählt: " + " · ".join(f"{n} × {grund} ({ERKLAERUNG.get(grund, '')})"
                                                           for grund, n in pl["nicht_gezaehlt"].items()))
        zeilen.append(f"  {ZIEL_NAMEN['zuschauer']}: {_status_text(pl['ziele']['zuschauer'])}")
        zeilen += [f"    {ZIEL_NAMEN[teil]}: {_status_text(stand)}" for teil, stand in pl["teilziele"].items()]
        zeilen += [f"  {ZIEL_NAMEN[ziel]}: {_status_text(pl['ziele'][ziel])}" for ziel in ("follower", "webseite")]
        if pl["vergleich"]["videos"]:
            wo = _wo(pl)
            zeilen.append(f"  Vergleiche (je Seite mindestens {MINDESTENS} Videos, Länge im Schnitt höchstens "
                          f"{LAENGE_TOLERANZ * 100:.0f} % verschieden):")
            zeilen += [f"    {_befund_text(b, wo)}" for b in pl["befunde"]]
            if pl["vergleich"]["andere_ziele"]:
                zeilen.append(f"    ({_videos(pl['vergleich']['andere_ziele'])} mit anderen gemessenen Zielen "
                              "zählen hier nicht mit.)")
    return "\n".join(zeilen)


def zeile_einfach(bericht: dict) -> str | None:
    """Wochenbericht im einfachen Modus (eingehängt erst in PR 2): je Plattform mit fertigen Wochenzahlen eine Zeile,
    darunter, was noch nicht gemessen wird. None ohne solche Videos und bei einem Freund (bei ihm holt niemand Zahlen
    ab, M169). Beispiele:
      „📊 Zuschauer (TikTok): 4 Videos mit fertigen Wochenzahlen – ein erster Vergleich frühestens nach 12 weiteren.“
      „📊 Zuschauer (TikTok, 23 Videos): noch kein Unterschied sicher.“
      „📊 Belegt (TikTok, 41 Videos): Aufbau „erzählt“ kommt bei den Zuschauern besser an als die anderen Aufbauten
       (14 gegen 27 Videos) – sehr wahrscheinlich kein Zufall.“
      „Noch nicht gemessen: wie lange geschaut wird, neue Follower, Besuche auf clip-battle.de.“"""
    if bericht.get("freund"):
        return None
    zeilen, gezeigt = [], []
    for plattform, pl in bericht["plattformen"].items():
        n = pl["vergleich"]["videos"]
        if not n:
            continue
        gezeigt.append(pl)
        name = _plattform_name(plattform)
        wort = "Zuschauer" if pl["vergleich"]["ziele"] == ["zuschauer"] else "Erfolg"
        status = [b["status"] for b in pl["befunde"]]
        belegt = [b for b in pl["befunde"] if b["status"] in (BESSER, SCHLECHTER)]
        if belegt:
            b = max(belegt, key=lambda b: (b["status"] == BESSER, abs(b["unterschied"])))
            zeilen.append(f"📊 Belegt ({name}, {_videos(n)}): {satz(b, _wo(pl))} – sehr wahrscheinlich kein Zufall.")
        elif KEIN_UNTERSCHIED not in status and ZU_WENIG in status:
            fehlen = min(b["fehlen"] for b in pl["befunde"] if b["status"] == ZU_WENIG)
            zeilen.append(f"📊 {wort} ({name}): {_videos(n)} mit fertigen Wochenzahlen – ein erster Vergleich "
                          f"frühestens nach {fehlen} weiteren.")
        else:
            zeilen.append(f"📊 {wort} ({name}, {_videos(n)}): noch kein Unterschied sicher.")
    if not zeilen:
        return None
    offen = [OFFEN_NAMEN[ziel] for ziel in ("bindung", "follower", "webseite")
             if not any((pl["teilziele"] if ziel == "bindung" else pl["ziele"])[ziel]["status"] == GEMESSEN
                        for pl in gezeigt)]
    if offen:
        zeilen.append("Noch nicht gemessen: " + ", ".join(offen) + ".")
    return "\n".join(zeilen)
