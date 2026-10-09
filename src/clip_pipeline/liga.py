"""Regie-Liga (Mehrbenutzer Stufe 5, M178–M186): welcher Aufbau bei den Zuschauern belegt am besten ankommt – nur
lesen, reine Rechnung. Keine Tabelle, nichts geschrieben, kein Netz, keine Sperre, weckt nie.

Abnahme (Florian): „Benutzer können nachvollziehen, was das System ausprobiert und tatsächlich gelernt hat.“

Belege kommen nur aus den erfolg-Einheiten der Hauptplattform ([publikum].plattformen[0], heute TikTok): die
eingefrorene Wochen-Note jedes hochgeladenen Shorts, je Video einmal (Fassungen einmal, ein Crosspost zählt nur auf der
Hauptplattform), die ersten 5 nur Vergleich. Nie KI-Note, Cutter-Note, ✅/❌, Erwartung, Lernstand-Versionen, Zeit oder
Tippen. TikTok und YouTube werden nie zusammengelegt; andere Plattformen stehen nur in `pipeline erfolg`.

Stichtag ist Sonntag 18 Uhr Ortszeit. stand() spielt alle Stichtage aus EINER Abfrage (erfolg.einheiten) nach: An jedem
zählt nur, was bis dahin eine fertige Wochen-Note hatte (bewertet_utc ≤ Stichtag; von mehreren Fassungen die zuerst
hochgeladene, deren Note schon fest war – wie erfolg damals). Jedes Nachsehen ist folgenlos und ergibt denselben
Verlauf. Wer [publikum.gewichte] oder [erfolg.gewichte] ändert, rechnet die Geschichte neu – Version und Gewichte stehen
in jeder Ausgabe.

Regel (M180, M181):
  1. Noch kein bester Aufbau (kein Startwert): erfolg.vergleiche nennt denselben Aufbau an zwei Stichtagen
     nacheinander „belegt besser als die anderen Aufbauten“ – der 📊-Satz aus Stufe 4.
  2. Bester Aufbau C seit Stichtag T: Ein anderer Aufbau X löst ab, wenn erfolg.paarweise(X, C) an zwei Stichtagen
     nacheinander „belegt besser“ ergibt – nur mit Videos, die nach T hochgeladen wurden, auf beiden Seiten. Die Videos
     der Krönung verteidigen nie, „gegen den Rest“ löst nie ab, „schlechter“ stürzt nie.
  Mehrere Kandidaten: der mit dem größten Unterschied. Ein Stichtag ohne neue fertige Wochen-Note entscheidet nichts –
  sonst bestätigte sich ein Kandidat mit denselben Videos ein zweites Mal.

Erfahrung (M183) = gezählte Einheiten bis zum Abrufzeitpunkt (je Aufbau, Tempo, Zeitlupe, Länge und gesamt; „neu“ =
in den 7 Tagen davor fertig geworden). Level je Strategie nur aus der Erfahrung (1 unter 8 · 2 ab 8 · 3 ab 16 · 4 ab
32 · 5 ab 64) – sagt, wie gut sie vermessen ist, nie „besser“. Liga-Level nur aus Ereignissen der Stichtage:
1 sammelt · 2 vergleicht (ein Aufbau-Vergleich hatte genug Videos) · 3 bester Aufbau belegt · +1 je Ablösung; es sinkt
nie. Vertrauen in Worten (M184), die Spanne von–bis nur für /experte und `pipeline erfolg`, nie Prozent.
Versuche (M186): bewusst Neues der 7 Tage vor dem Abruf (geschmack.experiment und der Versuch des Publikums-Modells),
je Video einmal, mit einfachen Namen; nach einer Krönung heißen Videos mit anderem Aufbau Herausforderer.

Im einfachen Modus (Stufe 5, Schritt 2, M187–M191): Sonntagsbericht (geschmack.wochen_text mit neu_zeilen,
bester_zeile, versuche_zeile, level_zeile, ziel_zeile) und eine Zeile in 📋 (stand_zeile) – beides erst, wenn auf der
Hauptplattform Zuschauerzahlen ankommen (zahlen_kommen_an); 🧪 auch ohne Zahlen. Unter /experte: text() oben in
/lernstand.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import datetime, timedelta

from . import erfolg, geschmack, publikum, szenen
from .bot.aktionen import PLATTFORM_NAMEN
from .konfig import Konfig
from .zeit import UTC, aus_iso, iso, jetzt, utc_zu_lokal, zone

LIGA_VERSION = 1
STICHTAG_STUNDE = 18                       # Sonntag 18 Uhr Ortszeit – wie der Sonntagsbericht
STUFEN = (8, 16, 32, 64)                   # Level 2 … 5 ab so vielen gezählten Videos (8 = vergleichbar)
NEU_TAGE = 7                               # „+k“ und Versuche: die 7 Tage vor bis
LIGA_STUFEN = {1: "sammelt", 2: "vergleicht", 3: "bester Aufbau belegt"}
NOCH_BESSER = "noch besseren Aufbau gefunden"   # Liga-Level 4, 5 …: je Ablösung eins mehr
AUFBAUTEN = geschmack.KNOEPFE["aufbau"]
MERKMALE = ("aufbau", "tempo", "zeitlupe", "laenge")
OPTIONEN = {**geschmack.KNOEPFE, "laenge": ("kurz", "mittel", "lang")}
LAENGE_NAMEN = {"kurz": "unter 45 s", "mittel": "45–60 s", "lang": "über 60 s"}


def name(merkmal: str, wahl: str) -> str:
    """„„erzählt““, „schnelle Schnitte“, „viel Zeitlupe“, „45–60 s“ – ohne Fachbegriffe."""
    if merkmal == "laenge":
        return LAENGE_NAMEN[wahl]
    return geschmack.NAMEN[(merkmal, wahl)].removeprefix("Aufbau ")


def _videos(n: int) -> str:
    return f"{n} Video{'s' if n != 1 else ''}"


def level(videos: int) -> int:
    """Level einer Strategie nur aus ihrer Erfahrung: 1 unter 8 · 2 ab 8 · 3 ab 16 · 4 ab 32 · 5 ab 64 Videos."""
    return 1 + sum(1 for s in STUFEN if videos >= s)


def hauptplattform(konfig: Konfig) -> str | None:
    """Die erste Plattform aus [publikum].plattformen (heute TikTok) – nur auf ihr rechnet die Liga (M179)."""
    plattformen = publikum.post_plattformen(konfig)
    return plattformen[0] if plattformen else None


def zahlen_kommen_an(con: sqlite3.Connection, konfig: Konfig, bis: datetime | None = None) -> bool:
    """Kommen auf der Hauptplattform Zuschauerzahlen an? Eine Messung eines Shorts genügt (M189) – dieselbe Zählung wie
    stand()["messungen"], nur billig (eine Zeile, ohne Netz). Danach schalten die Liga-Zeilen in Bericht und 📋, nicht
    danach, ob jemand ein Freund ist (M190)."""
    haupt = hauptplattform(konfig)
    if not haupt:
        return False
    sql, werte = ("SELECT 1 FROM posts p JOIN publikum_messungen m ON m.post_id = p.id "
                  "WHERE p.plattform = ? AND p.art = 'entwurf'"), [haupt]
    if bis is not None:
        sql += " AND m.gemessen_utc <= ?"
        werte.append(iso(bis))
    return con.execute(sql + " LIMIT 1", werte).fetchone() is not None


# --- Stichtage -----------------------------------------------------------------------------------------------------

def _sonntag(tag, zonen_name: str) -> datetime:
    return datetime(tag.year, tag.month, tag.day, STICHTAG_STUNDE, tzinfo=zone(zonen_name)).astimezone(UTC)


def letzter_stichtag(zeit: datetime, zonen_name: str) -> datetime:
    """Der letzte Sonntag 18 Uhr Ortszeit bis einschließlich zeit (UTC; Sommerzeit über die Zeitzonen-Datenbank)."""
    lokal = utc_zu_lokal(zeit, zonen_name)
    tag = lokal.date() - timedelta(days=lokal.isoweekday() % 7)
    stichtag = _sonntag(tag, zonen_name)
    return stichtag if stichtag <= zeit else _sonntag(tag - timedelta(days=7), zonen_name)


def stichtage(von: datetime, bis: datetime, zonen_name: str) -> list[datetime]:
    """Alle Stichtage ab von (einschließlich) bis bis (einschließlich), älteste zuerst."""
    tage, ende = [], letzter_stichtag(bis, zonen_name)
    tag = utc_zu_lokal(letzter_stichtag(von, zonen_name), zonen_name).date()
    while (stichtag := _sonntag(tag, zonen_name)) <= ende:
        if stichtag >= von:
            tage.append(stichtag)
        tag += timedelta(days=7)
    return tage


# --- Nachspielen ---------------------------------------------------------------------------------------------------

def _grundlage(con: sqlite3.Connection, konfig: Konfig, bis: datetime) -> dict:
    """Die eine Abfrage: Hauptplattform, Gewichte und die erfolg-Einheiten (mit Fassungen) als Kandidaten mit
    Zeitpunkten, nach fertig sortiert. Unlesbare Zeitpunkte zählen nicht (Grund „Score-Teile unlesbar“)."""
    w = erfolg.gewichte(konfig)
    haupt = hauptplattform(konfig)
    pl = erfolg.einheiten(con, konfig, bis, w).get(haupt) if haupt else None
    pl = pl or {"einheiten": [], "fassungen": [], "wartet": 0, "weg": Counter()}
    weg = Counter(pl["weg"])
    kandidaten = []
    for e in (*pl["einheiten"], *pl["fassungen"]):
        try:
            k = {"fertig": aus_iso(e["fertig"]), "gepostet": aus_iso(e["gepostet"]), "e": e}
        except (TypeError, ValueError):
            weg[erfolg.UNLESBAR] += 1
            continue
        k["ordnung"] = (e["gepostet"], e["post"])   # wie erfolg: in Upload-Reihenfolge zählt die erste Fassung
        kandidaten.append(k)
    kandidaten.sort(key=lambda k: (k["fertig"], k["e"]["post"]))
    return {"w": w, "haupt": haupt, "kandidaten": kandidaten, "wartet": pl["wartet"], "weg": weg,
            "zone": str(konfig.wert("zeit.zeitzone", "Europe/Berlin"))}


def _aufnehmen(aktiv: dict[int, dict], k: dict) -> bool:
    """Kandidat k hat jetzt eine fertige Wochen-Note: Je Familie zählt die zuerst hochgeladene Fassung mit fertiger
    Note. True, wenn sich damit etwas geändert hat."""
    alt = aktiv.get(k["e"]["familie"])
    if alt is not None and alt["ordnung"] <= k["ordnung"]:
        return False
    aktiv[k["e"]["familie"]] = k
    return True


def _einheiten(aktiv: dict[int, dict]) -> list[dict]:
    return [k["e"] for k in sorted(aktiv.values(), key=lambda k: k["ordnung"])]


def _urteile(einheiten: list[dict], bester: str | None, seit: datetime | None) -> tuple[dict[str, dict], list[str]]:
    """Je Aufbau der Befund: ohne besten Aufbau aus erfolg.vergleiche (eine gegen die anderen), sonst paarweise gegen
    ihn – beide Seiten nur mit Videos, die nach seit hochgeladen wurden. Dazu die Ziele der verglichenen Gruppe."""
    if bester is None:   # genau der 📊-Satz aus Stufe 4 – nur ohne die beschreibenden Teilziele
        ziele, gruppe = erfolg.groesste_gruppe(einheiten)
        return {b["wahl"]: b for b in erfolg.vergleiche(gruppe, ("aufbau",), teilziele=False)}, list(ziele)
    frisch = [e for e in einheiten if aus_iso(e["gepostet"]) > seit]
    ziele, gruppe = erfolg.groesste_gruppe(frisch)
    seite = {o: [e for e in gruppe if e["merkmale"].get("aufbau") == o] for o in AUFBAUTEN}
    return ({o: {"merkmal": "aufbau", "wahl": o, "gegen": bester, **erfolg.paarweise(seite[o], seite[bester])}
             for o in AUFBAUTEN if o != bester}, list(ziele))


def _kandidat(befunde: dict[str, dict]) -> dict | None:
    besser = [b for b in befunde.values() if b["status"] == erfolg.BESSER]
    return max(besser, key=lambda b: b["unterschied"]) if besser else None


def _eintrag(stichtag: datetime, art: str, befund: dict, ziele: list[str], **mehr) -> dict:
    return {"stichtag": iso(stichtag), "art": art, "aufbau": befund["wahl"], "gegen": befund["gegen"],
            "n": befund["n"], "unterschied": befund["unterschied"], "von": befund["von"], "bis": befund["bis"],
            "ziele": ziele, **mehr}


def _nachspielen(kandidaten: list[dict], tage: list[datetime]) -> dict:
    """Stichtag für Stichtag: Krönung, Ablösung, Kandidat (vorn). verlauf: je Stichtag mit Kandidat ein Eintrag
    „vorn“, je Krönung oder Ablösung einer „bester“. aktiv: die gezählten Einheiten am letzten Stichtag."""
    aktiv: dict[int, dict] = {}
    i, bester, seit, vorn, vergleicht, verlauf = 0, None, None, None, None, []
    for stichtag in tage:
        neu = False
        while i < len(kandidaten) and kandidaten[i]["fertig"] <= stichtag:
            neu = _aufnehmen(aktiv, kandidaten[i]) or neu
            i += 1
        if not neu:        # nichts Neues fertig: dieser Sonntag entscheidet nichts, ein Kandidat bleibt vorläufig
            continue
        befunde, ziele = _urteile(_einheiten(aktiv), bester, seit)
        if vergleicht is None and any(b["status"] != erfolg.ZU_WENIG for b in befunde.values()):
            vergleicht = stichtag
        kandidat = _kandidat(befunde)
        if kandidat is not None and vorn is not None and kandidat["wahl"] == vorn["wahl"]:
            verlauf.append(_eintrag(stichtag, "bester", kandidat, ziele, vorher=bester))
            bester, seit, vorn = kandidat["wahl"], stichtag, None
            continue
        if kandidat is not None:
            verlauf.append(_eintrag(stichtag, "vorn", kandidat, ziele))
        vorn = kandidat
    return {"aktiv": aktiv, "rest": kandidaten[i:], "bester": bester, "seit": seit, "vorn": vorn,
            "vergleicht": vergleicht, "verlauf": verlauf}


def champion(con: sqlite3.Connection, konfig: Konfig) -> str | None:
    """Der belegte beste Aufbau („montage“, „story“ …) oder None – dieselbe Rechnung wie stand(), einmal je Aufruf,
    ohne die Teile für die Anzeige (für geschmack.waehle, Stufe 5 PR 3)."""
    bis = jetzt()
    g = _grundlage(con, konfig, bis)
    if not g["kandidaten"]:
        return None
    return _nachspielen(g["kandidaten"], stichtage(g["kandidaten"][0]["fertig"], bis, g["zone"]))["bester"]


# --- Stand ---------------------------------------------------------------------------------------------------------

def _zahl(x: float) -> str:
    return f"{x:+.2f}".replace(".", ",").replace("-", "−")


def _spanne(befund: dict) -> str:
    return f"Spanne {_zahl(befund['von'])} bis {_zahl(befund['bis'])}" if "von" in befund else ""


def _wort(befund: dict, bester: str | None, seit_text: str, wo: str = "bei den Zuschauern") -> str:
    """Vertrauen in Worten – nie eine Prozentzahl (M184)."""
    status, gegen = befund["status"], befund["gegen"]
    if status == erfolg.ZU_WENIG:   # nach einer Krönung zählen beim Aufbau nur Videos seitdem
        return f"noch {_videos(befund['fehlen'])}" + (f" seit dem {seit_text}" if bester and gegen == bester else "")
    if status == erfolg.NICHT_VERGLEICHBAR:
        return "nicht vergleichbar (Länge)"
    if status == erfolg.KEIN_UNTERSCHIED:
        return "kein Unterschied sicher"
    if befund["merkmal"] != "aufbau":   # Tempo, Zeitlupe, Länge: ohne Krone
        return f"{erfolg.satz(befund, wo)} – belegt, aber ohne Krone"
    if status == erfolg.SCHLECHTER:
        return "belegt schlechter als " + (name("aufbau", bester) if bester else "die anderen")
    return f"liegt diesmal vor {name('aufbau', gegen)}" if bester else "liegt diesmal vorn"


def _befund(befund: dict, **mehr) -> dict:
    return {k: befund[k] for k in ("merkmal", "wahl", "gegen", "status", "n", "fehlen", "laenge_s", "unterschied",
                                   "von", "bis") if k in befund} | mehr


def _ziel(befunde: dict[str, dict], bester: str | None, vorn: dict | None, basis_fehlt: int) -> dict:
    """Das nächste Ziel: einen Kandidaten bestätigen, den ersten Vergleich erreichen, Herausforderer sammeln oder den
    Vergleich genauer machen."""
    if vorn is not None:
        return {"art": "vorn", "aufbau": vorn["wahl"], "gegen": vorn["gegen"], "n": vorn["n"]}
    werte = list(befunde.values())
    if all(b["status"] == erfolg.ZU_WENIG for b in werte):
        b = min(werte, key=lambda b: (b["fehlen"], -sum(b["n"])))
        if bester is None:
            return {"art": "erster_vergleich", "fehlen": b["fehlen"] + basis_fehlt}
        return {"art": "herausforderer", "aufbau": b["wahl"], "gegen": bester, "n": b["n"], "fehlen": b["fehlen"]}
    b = max((b for b in werte if b["status"] != erfolg.ZU_WENIG), key=lambda b: sum(b["n"]))
    return {"art": "genauer", "aufbau": b["wahl"], "gegen": b["gegen"], "n": b["n"]}


def _versuch_name(exp: dict) -> str | None:
    """Einfacher Name für den Versuch des Publikums-Modells (autonom.exploration) – None, wenn unbekannt."""
    k, wert, vorher = exp.get("variable"), exp.get("wert"), exp.get("vorher")
    zahl = all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in (wert, vorher))
    if k == "hook_staerkster" and isinstance(wert, bool):
        return "stärkste Szene zuerst" if wert else "Einstieg mit Anlauf"
    if k == "ziel_dauer_s" and isinstance(wert, (int, float)) and not isinstance(wert, bool):
        return f"Länge {wert:.0f} s"
    if k == "beats_pro_schnitt" and wert in (1, 2, 4):
        return "Schnitt auf jeden Beat" if wert == 1 else f"Schnitt auf jeden {wert}. Beat"
    mehr_weniger = {"musik_pegel": ("Musik lauter", "Musik leiser"),
                    "seg_min_faktor": ("ruhigere Schnitte", "schnellere Schnitte"),
                    "effekt_hektik": ("hektischere Effekte", "ruhigere Effekte")}
    if k in mehr_weniger and zahl and wert != vorher:
        return mehr_weniger[k][0 if wert > vorher else 1]
    return None


def _versuche(con: sqlite3.Connection, haupt: str | None, bis: datetime, bester: str | None,
              seit: datetime | None) -> dict:
    """Was in den 7 Tagen vor bis bewusst ausprobiert wurde (gesendete Shorts, je Video und Versuch einmal):
    gezeigt · hochgeladen (Post auf der Hauptplattform) · mit Zahlen (mindestens eine Messung). Nach einer Krönung dazu
    die Herausforderer (andere Aufbauten, je Video einmal). Kein Urteil je Versuch."""
    von = bis - timedelta(days=NEU_TAGE)
    zeilen = con.execute("""SELECT id, parameter, erstellt FROM entwuerfe
                             WHERE format = 'short' AND status IN ('gesendet', 'bewertet')
                               AND (variante IS NULL OR variante <> 'fail') AND erstellt >= ? AND erstellt < ?
                             ORDER BY id""", (iso(von), iso(bis))).fetchall()
    posts: dict[int, bool] = {}
    if haupt and zeilen:
        ids = [z["id"] for z in zeilen]
        for p in con.execute(f"""SELECT p.entwurf_id, EXISTS (SELECT 1 FROM publikum_messungen m
                                         WHERE m.post_id = p.id AND m.gemessen_utc <= ?) AS zahlen
                                   FROM posts p WHERE p.art = 'entwurf' AND p.plattform = ? AND p.erstellt <= ?
                                    AND p.entwurf_id IN ({','.join('?' for _ in ids)})""",
                             (iso(bis), haupt, iso(bis), *ids)):
            posts[int(p["entwurf_id"])] = posts.get(int(p["entwurf_id"]), False) or bool(p["zahlen"])
    versuche: dict[tuple, dict] = {}            # (Familie, Name) → hochgeladen, mit Zahlen
    arten: dict[str, dict] = {}                 # Name → Quelle, Schraube, Wahl
    herausforderer: dict[int, str] = {}         # Familie → Aufbau
    for z in zeilen:
        try:
            p = json.loads(z["parameter"] or "{}") or {}
        except (TypeError, ValueError):
            continue
        if not isinstance(p, dict):
            continue
        familie = min([int(z["id"]), *szenen._ersetzt(z["parameter"])])
        neu: list[tuple[str, dict]] = []
        g = p.get("geschmack") if isinstance(p.get("geschmack"), dict) else {}
        knopf = g.get("experiment")
        if knopf in geschmack.KNOEPFE and g.get(knopf) in geschmack.KNOEPFE[knopf]:
            neu.append((geschmack.NAMEN[(knopf, g[knopf])], {"quelle": "geschmack", "knopf": knopf, "wahl": g[knopf]}))
        auto = p.get("autonom") if isinstance(p.get("autonom"), dict) else {}
        if isinstance(exp := auto.get("exploration"), dict) and (n := _versuch_name(exp)):
            neu.append((n, {"quelle": "publikum", "knopf": exp.get("variable"), "wahl": exp.get("wert")}))
        for n, art in neu:
            arten.setdefault(n, art)
            v = versuche.setdefault((familie, n), {"hochgeladen": False, "zahlen": False})
            v["hochgeladen"] = v["hochgeladen"] or z["id"] in posts
            v["zahlen"] = v["zahlen"] or posts.get(z["id"], False)
        aufbau = geschmack._wahl_aus(z["parameter"]).get("aufbau")
        if bester and seit and aufbau and aufbau != bester and aus_iso(z["erstellt"]) > seit:
            herausforderer.setdefault(familie, aufbau)
    liste = []
    for n, art in arten.items():
        je = [v for (_, vn), v in versuche.items() if vn == n]
        liste.append({"name": n, **art, "gezeigt": len(je), "hochgeladen": sum(v["hochgeladen"] for v in je),
                      "mit_zahlen": sum(v["zahlen"] for v in je)})
    return {"von": iso(von), "bis": iso(bis), "liste": liste,
            "herausforderer": dict(Counter(herausforderer.values()).most_common())}


def stand(con: sqlite3.Connection, konfig: Konfig, bis: datetime | None = None) -> dict:
    """Die Regie-Liga bis bis (Standard jetzt) – nur lesen. Entschieden wird nur an den Stichtagen bis bis.
    JSON-Schlüssel (für `pipeline erfolg`, Sonntagsbericht und /experte):
      version, erfolg_version, gewichte {erfolg, publikum}, plattform, abruf, zeitzone, bis, stichtag (der letzte)
      bester, seit · liga_level, liga_stufe · kandidat (liegt vorn, noch nicht bestätigt) · ereignis (Krönung oder
      Ablösung genau am letzten Stichtag) · verlauf
      erfahrung {gesamt, neu, basis [x, 5], aufbau, tempo, zeitlupe, laenge} (bis bis) · seit_kroenung {gesamt, aufbau}
      level {merkmal: {wahl: 1…5}} · vertrauen {aufbau: Befund mit Wort} · feinheiten [Befunde Tempo, Zeitlupe, Länge]
      ziel (das nächste Ziel) · versuche {von, bis, liste, herausforderer} · messungen (Posts der Hauptplattform mit
      Zahlen) · offen (Ziele, die bei keinem gezählten Video gemessen sind: bindung, follower, webseite – für „Noch nicht
      gemessen“ unter einem neuen 🥇) · nicht_gezaehlt, wartet (wie `pipeline erfolg` zum Zeitpunkt bis).
    KonfigFehler bei kaputten [erfolg.gewichte] oder [publikum]-Werten."""
    bis = bis or jetzt()
    g = _grundlage(con, konfig, bis)
    haupt, kandidaten, zonen_name = g["haupt"], g["kandidaten"], g["zone"]
    letzter = letzter_stichtag(bis, zonen_name)
    erg = _nachspielen(kandidaten, stichtage(kandidaten[0]["fertig"], bis, zonen_name) if kandidaten else [])
    bester, seit, vorn = erg["bester"], erg["seit"], erg["vorn"]
    seit_text = utc_zu_lokal(seit, zonen_name).strftime("%d.%m.") if seit else ""

    # Stand am letzten Stichtag (danach entschieden): je Aufbau, Feinheiten, nächstes Ziel
    am_stichtag = _einheiten(erg["aktiv"])
    ziele, gruppe = erfolg.groesste_gruppe(am_stichtag)
    wo = "bei den Zuschauern" if ziele in ((), ("zuschauer",)) else "insgesamt"
    alle = erfolg.vergleiche(gruppe)
    befunde = {b["wahl"]: b for b in alle if b["merkmal"] == "aufbau"} if bester is None else \
        _urteile(am_stichtag, bester, seit)[0]

    # Erfahrung bis bis (auch, was nach dem letzten Stichtag fertig wurde)
    aktiv = dict(erg["aktiv"])
    for k in erg["rest"]:
        if k["fertig"] <= bis:
            _aufnehmen(aktiv, k)
    einheiten = _einheiten(aktiv)
    zaehler = {m: Counter(e["merkmale"].get(m) for e in einheiten) for m in MERKMALE}
    erfahrung = {m: {o: zaehler[m][o] for o in OPTIONEN[m]} for m in MERKMALE}
    bewertet = con.execute("SELECT COUNT(*) FROM posts WHERE plattform = ? AND bewertet_utc IS NOT NULL "
                           "AND bewertet_utc <= ?", (haupt or "", iso(bis))).fetchone()[0]
    basis = min(publikum.MINDEST_BASIS, bewertet)
    frisch = Counter(e["merkmale"].get("aufbau") for e in einheiten if seit and aus_iso(e["gepostet"]) > seit)

    kronen = [v for v in erg["verlauf"] if v["art"] == "bester"]
    liga_level = 2 + len(kronen) if kronen else 1 + (erg["vergleicht"] is not None)
    messungen = con.execute("""SELECT COUNT(DISTINCT p.id) FROM posts p JOIN publikum_messungen m ON m.post_id = p.id
                                WHERE p.plattform = ? AND p.art = 'entwurf' AND m.gemessen_utc <= ?""",
                            (haupt or "", iso(bis))).fetchone()[0]
    return {
        "version": LIGA_VERSION, "erfolg_version": erfolg.ERFOLG_VERSION,
        "gewichte": {"erfolg": g["w"], "publikum": {publikum.KONFIG_NAMEN[t]: v
                                                    for t, v in publikum._gewichte(konfig, mit_r=True).items()}},
        "plattform": haupt, "abruf": erfolg.abruf_hinweis(konfig), "zeitzone": zonen_name, "bis": iso(bis),
        "stichtag": iso(letzter), "bester": bester, "seit": iso(seit) if seit else None,
        "liga_level": liga_level, "liga_stufe": LIGA_STUFEN.get(liga_level, NOCH_BESSER),
        "kandidat": _befund(vorn) if vorn else None,
        "ereignis": kronen[-1] if kronen and aus_iso(kronen[-1]["stichtag"]) == letzter else None,
        "verlauf": erg["verlauf"],
        "erfahrung": {"gesamt": len(einheiten),
                      "neu": sum(1 for k in aktiv.values() if k["fertig"] > bis - timedelta(days=NEU_TAGE)),
                      "basis": [basis, publikum.MINDEST_BASIS], **erfahrung},
        "seit_kroenung": {"gesamt": sum(frisch.values()), "aufbau": {o: frisch[o] for o in AUFBAUTEN}}
        if bester else None,
        "level": {m: {o: level(n) for o, n in erfahrung[m].items()} for m in MERKMALE},
        "vertrauen": {o: ({"status": "bester", "wort": f"belegt seit {seit_text}"} if o == bester else
                          _befund(befunde[o], wort=_wort(befunde[o], bester, seit_text))) for o in AUFBAUTEN},
        "feinheiten": [_befund(b, wort=_wort(b, bester, seit_text, wo)) for b in alle if b["merkmal"] != "aufbau"],
        "ziel": _ziel(befunde, bester, vorn, max(0, publikum.MINDEST_BASIS - bewertet)),
        "versuche": _versuche(con, haupt, bis, bester, seit),
        "messungen": messungen,
        "offen": [ziel for ziel in ("bindung", "follower", "webseite")
                  if not any(e["status"][ziel] == erfolg.GEMESSEN for e in einheiten)],
        "nicht_gezaehlt": dict(sorted(g["weg"].items())), "wartet": g["wartet"],
    }


# --- Text für die Konsole ------------------------------------------------------------------------------------------

def _datum(wert: str, zonen_name: str) -> str:
    return utc_zu_lokal(aus_iso(wert), zonen_name).strftime("%d.%m.")


def _heute(st: dict) -> bool:
    """Wurde der beste Aufbau am Tag von bis gekrönt (Sonntagsbericht in der Krönungswoche)? Dann „ab heute“."""
    return bool(st["seit"]) and (utc_zu_lokal(aus_iso(st["seit"]), st["zeitzone"]).date()
                                 == utc_zu_lokal(aus_iso(st["bis"]), st["zeitzone"]).date())


def ziel_text(st: dict) -> str:
    """Das nächste Ziel in einem Satz, z. B. „Erster Vergleich der Aufbauten frühestens nach 19 weiteren Videos mit
    Zuschauerzahlen.“ – für `pipeline erfolg` und (mit 🔜 davor) den Sonntagsbericht."""
    z, bester = st["ziel"], st["bester"]
    seit = _datum(st["seit"], st["zeitzone"]) if st["seit"] else ""
    if z["art"] == "erster_vergleich":
        return (f"Erster Vergleich der Aufbauten frühestens nach {z['fehlen']} weiteren "
                f"Video{'s' if z['fehlen'] != 1 else ''} mit Zuschauerzahlen.")
    if z["art"] == "herausforderer":
        if not sum(z["n"]):
            ab = "heute" if _heute(st) else f"dem {seit}"
            return (f"Kann ein anderer Aufbau {name('aufbau', bester)} schlagen? Es zählen nur Videos ab {ab} – "
                    f"frühestens nach {z['fehlen']} weiteren.")
        a, b = z["n"]   # „5 von 8“ – steht der beste Aufbau selbst noch unter 8 seit der Krönung: „5 und 3 von je 8“
        stand_ = f"{a} von {erfolg.MINDESTENS}" if b >= erfolg.MINDESTENS else f"{a} und {b} von je {erfolg.MINDESTENS}"
        return f"{name('aufbau', z['aufbau'])} gegen {name('aufbau', bester)}: {stand_} Videos seit dem {seit}"   # „14.06.“
    a, b = z["n"]
    if z["art"] == "vorn" and bester is None:
        return (f"Aufbau {name('aufbau', z['aufbau'])} liegt diesmal vorn ({a} gegen {b} Videos) – bestätigt es sich "
                "am nächsten Sonntag mit neuen Zahlen, wird er dein bester Aufbau.")
    if z["art"] == "vorn":
        return (f"{name('aufbau', z['aufbau'])} liegt diesmal vor {name('aufbau', bester)} ({a} gegen {b} Videos seit "
                f"dem {seit}) – bestätigt es sich am nächsten Sonntag mit neuen Zahlen, löst er ihn ab.")
    if bester is None:
        return "Noch kein Aufbau kommt sicher besser an – jedes weitere Video macht den Vergleich genauer."
    return (f"{name('aufbau', z['aufbau'])} gegen {name('aufbau', bester)}: {a} gegen {b} Videos seit dem {seit} – "
            "noch kein Unterschied sicher.")


def _wie(st: dict, krone: dict, plattform: str | None = None) -> str:
    """Wie ein Aufbau bester wurde: „kommt bei den Zuschauern besser an als die anderen Aufbauten“ bzw. „hat „erzählt“
    auf Videos seit dem 07.03. geschlagen“ – mit plattform (Bericht) „… bei den Zuschauern auf TikTok besser an …“."""
    if krone["vorher"] is None:
        wo = "bei den Zuschauern" if krone["ziele"] == ["zuschauer"] else "insgesamt"
        return f"kommt {wo}{f' auf {plattform}' if plattform else ''} besser an als die anderen Aufbauten"
    kronen = [v for v in st["verlauf"] if v["art"] == "bester"]
    davor = kronen[kronen.index(krone) - 1]
    return f"hat {name('aufbau', krone['vorher'])} auf Videos seit dem {_datum(davor['stichtag'], st['zeitzone'])} geschlagen"


# --- Zeilen für den einfachen Modus (Stufe 5, Schritt 2) -------------------------------------------------------------
# Ohne Prozent und Fachbegriffe; „belegt“ heißt nur, was die Liga gekrönt hat (M188). 🥇 bester Aufbau · 🏅 Level und
# Erfahrung · 🔜 nächstes Ziel · 🧪 ausprobiert.

def neu_zeilen(st: dict) -> list[str]:
    """Krönung oder Ablösung genau am letzten Stichtag: „🥇 Neuer bester Aufbau: „erzählt“ – kommt bei den Zuschauern
    auf TikTok besser an als die anderen Aufbauten (14 gegen 27 Videos, zwei Sonntage nacheinander), sehr wahrscheinlich
    kein Zufall.“ und darunter „Noch nicht gemessen: …“ (nur hier, M188). [] ohne ein solches Ereignis."""
    k = st["ereignis"]
    if not k:
        return []
    wie = _wie(st, k, PLATTFORM_NAMEN.get(st["plattform"], st["plattform"]))
    zeilen = [f"🥇 Neuer bester Aufbau: {name('aufbau', k['aufbau'])} – {wie} ({k['n'][0]} gegen {k['n'][1]} Videos, "
              "zwei Sonntage nacheinander), sehr wahrscheinlich kein Zufall."]
    if st["offen"]:
        zeilen.append("Noch nicht gemessen: " + ", ".join(erfolg.OFFEN_NAMEN[z] for z in st["offen"]) + ".")
    return zeilen


def bester_zeile(st: dict) -> str | None:
    """„🥇 Bester Aufbau: „erzählt“ (belegt seit 07.03.)“ – None ohne besten Aufbau."""
    if not st["bester"]:
        return None
    return f"🥇 Bester Aufbau: {name('aufbau', st['bester'])} (belegt seit {_datum(st['seit'], st['zeitzone'])})"


def versuche_zeile(st: dict) -> str | None:
    """🧪 mit Namen statt „n× bewusst etwas Neues“: „🧪 Ausprobiert: Aufbau „Kino“ (1×) · Musik lauter (1×)“; nach einer
    Krönung „🧪 Herausforderer diese Woche: Aufbau „Steigerung“ (1×) · „Kino“ (1×)“, dahinter, was sonst neu war.
    Gezählt wird je Video einmal (gezeigt). None, wenn nichts bewusst neu war."""
    vs = st["versuche"]
    teile = [f"{v['name']} ({v['gezeigt']}×)" for v in vs["liste"]]
    if not vs["herausforderer"]:
        return "🧪 Ausprobiert: " + " · ".join(teile) if teile else None
    namen = [f"{geschmack.NAMEN[('aufbau', o)] if i == 0 else name('aufbau', o)} ({n}×)"
             for i, (o, n) in enumerate(vs["herausforderer"].items())]
    rest = [f"{v['name']} ({v['gezeigt']}×)" for v in vs["liste"]   # Aufbau-Versuche sind schon Herausforderer
            if not (v["quelle"] == "geschmack" and v["knopf"] == "aufbau")]
    return "🧪 Herausforderer diese Woche: " + " · ".join(namen) + (" · dazu ausprobiert: " + " · ".join(rest)
                                                                     if rest else "")


def level_zeile(st: dict) -> str:
    """„🏅 Level 2 – vergleicht · Erfahrung: 31 Videos mit fertigen Zuschauerzahlen (+3)“ – mit bestem Aufbau kürzer
    („Erfahrung: 41 Videos (+3)“, der 🥇-Satz sagt schon, woher), am Anfang mit „die ersten 5 sind nur der Vergleich“."""
    e = st["erfahrung"]
    zeile = f"🏅 Level {st['liga_level']} – {st['liga_stufe']} · Erfahrung: {_videos(e['gesamt'])}"
    if not st["bester"]:
        zeile += " mit fertigen Zuschauerzahlen"
    if e["basis"][0] < e["basis"][1]:
        zeile += f" (die ersten {e['basis'][1]} sind nur der Vergleich: {e['basis'][0]} von {e['basis'][1]})"
    elif e["neu"]:
        zeile += f" (+{e['neu']})"
    return zeile


def ziel_zeile(st: dict) -> str:
    """„🔜 …“ – das nächste Ziel (ziel_text)."""
    return f"🔜 {ziel_text(st)}"


def stand_zeile(st: dict) -> str:
    """Die eine Liga-Zeile in 📋 (M189): „🏅 Level 2 – vergleicht · 31 Videos mit Zuschauerzahlen · bester Aufbau noch
    nicht belegt“ bzw. „🥇 Bester Aufbau: „erzählt“ (belegt seit 07.03.) · Level 3 · 41 Videos mit Zuschauerzahlen“."""
    e = st["erfahrung"]
    videos = f"{_videos(e['gesamt'])} mit Zuschauerzahlen"
    if st["bester"]:
        return f"{bester_zeile(st)} · Level {st['liga_level']} · {videos}"
    if e["basis"][0] < e["basis"][1]:
        videos += f" (die ersten {e['basis'][1]} sind nur der Vergleich: {e['basis'][0]} von {e['basis'][1]})"
    return f"🏅 Level {st['liga_level']} – {st['liga_stufe']} · {videos} · bester Aufbau noch nicht belegt"


# --- Der ganze Abschnitt (pipeline erfolg, /lernstand unter /experte) -------------------------------------------------

def text(st: dict) -> str:
    """Der Abschnitt „Regie-Liga“ für `pipeline erfolg` (stderr) und oben in /lernstand (nur /experte, M192) – ohne
    Fachbegriffe, ohne Prozent; die Spanne von–bis steht nur hier."""
    zonen_name = st["zeitzone"]
    kopf = f"🥇 Regie-Liga (Version {st['version']})"
    if st["plattform"] is None:
        return f"{kopf}: keine Plattform eingerichtet ([publikum].plattformen) – die Liga bleibt leer."
    plattform = PLATTFORM_NAMEN.get(st["plattform"], st["plattform"])
    zeilen = [f"{kopf} – {plattform}, Stand Sonntag {_datum(st['stichtag'], zonen_name)}, 18 Uhr. Es zählen nur "
              "fertige Wochenzahlen der Hauptplattform – nie KI-Note, ✅/❌ oder Zeit."]
    if st["abruf"]:
        zeilen.append(f"⚠️ {st['abruf']}")
    if st["bester"]:
        k = [v for v in st["verlauf"] if v["art"] == "bester"][-1]
        zeilen.append(f"Bester Aufbau: {name('aufbau', st['bester'])} – belegt seit {_datum(st['seit'], zonen_name)}: "
                      f"{_wie(st, k)} ({k['n'][0]} gegen {k['n'][1]} Videos, zwei Sonntage nacheinander), sehr "
                      "wahrscheinlich kein Zufall.")
    else:
        zeilen.append("Bester Aufbau: noch keiner belegt.")
    e = st["erfahrung"]
    zeile = (f"🏅 Liga-Level {st['liga_level']} – {st['liga_stufe']} · Erfahrung bis jetzt: {_videos(e['gesamt'])} mit "
             f"fertigen Zuschauerzahlen (+{e['neu']} in 7 Tagen)")
    if e["basis"][0] < e["basis"][1]:
        zeile += f" · die ersten {e['basis'][1]} sind nur der Vergleich: {e['basis'][0]} von {e['basis'][1]}"
    zeilen.append(zeile)
    seit = _datum(st["seit"], zonen_name) if st["seit"] else ""
    zeilen.append("Je Aufbau (Videos · Level · Stand am Sonntag):")
    for o in AUFBAUTEN:
        v = st["vertrauen"][o]
        frisch = f" (seit dem {seit}: {st['seit_kroenung']['aufbau'][o]})" if st["bester"] else ""
        spanne = f" ({_spanne(v)})" if "von" in v and v["status"] != "bester" else ""
        zeilen.append(f"  {name('aufbau', o)}: {_videos(e['aufbau'][o])}{frisch} · Level {st['level']['aufbau'][o]} · "
                      f"{v['wort']}{spanne}")
    zeilen.append("Feinheiten (nur Erfahrung und Stand, keine Krone):")
    for merkmal in ("tempo", "zeitlupe", "laenge"):
        optionen = " · ".join(f"{name(merkmal, o)} {e[merkmal][o]} (Level {st['level'][merkmal][o]})"
                              for o in OPTIONEN[merkmal])
        stand_ = [b for b in st["feinheiten"] if b["merkmal"] == merkmal]
        worte = " · ".join((f"{name(merkmal, b['wahl'])}: " if merkmal == "laenge" else "") + b["wort"]
                           + (f" ({_spanne(b)})" if b["status"] == erfolg.KEIN_UNTERSCHIED and "von" in b else "")
                           for b in stand_)
        zeilen.append(f"  {optionen} – {worte}")
    zeilen.append(f"🔜 {ziel_text(st)}")
    if st["verlauf"]:
        teile = []
        for v in st["verlauf"][-8:]:
            wer, n = name("aufbau", v["aufbau"]), f"{v['n'][0]} gegen {v['n'][1]} Videos"
            if v["art"] == "vorn":
                teile.append(f"{_datum(v['stichtag'], zonen_name)} {wer} liegt vorn ({n})" if v["gegen"] == "rest" else
                             f"{_datum(v['stichtag'], zonen_name)} {wer} liegt vor {name('aufbau', v['gegen'])} ({n})")
            elif v["vorher"] is None:
                teile.append(f"{_datum(v['stichtag'], zonen_name)} {wer} wird bester Aufbau ({n})")
            else:
                teile.append(f"{_datum(v['stichtag'], zonen_name)} {wer} löst {name('aufbau', v['vorher'])} ab ({n})")
        zeilen.append("Verlauf: " + ("… · " if len(st["verlauf"]) > 8 else "") + " · ".join(teile))
    vs = st["versuche"]
    bis = _datum(vs["bis"], zonen_name)
    if vs["liste"]:
        zeilen.append(f"🧪 Ausprobiert in den 7 Tagen bis {bis}: " + " · ".join(
            f"{v['name']} ({v['gezeigt']}× gezeigt, {v['hochgeladen']}× hochgeladen, {v['mit_zahlen']}× mit Zahlen)"
            for v in vs["liste"]))
    else:
        zeilen.append(f"🧪 In den 7 Tagen bis {bis} nichts bewusst Neues ausprobiert.")
    if vs["herausforderer"]:
        zeilen.append("Herausforderer in diesen 7 Tagen: " + " · ".join(
            f"{name('aufbau', o)} ({n}×)" for o, n in vs["herausforderer"].items()))
    if st["nicht_gezaehlt"]:
        zeilen.append("Nicht gezählt: " + " · ".join(f"{n} × {grund}" for grund, n in st["nicht_gezaehlt"].items()))
    return "\n".join(zeilen)
