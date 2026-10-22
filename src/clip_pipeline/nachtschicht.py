"""Nachtschicht – Fassungen im Wettbewerb (22.10., Florian: „ich möchte dass maschine learning autonom den clip schnitt
verbessert … ich möchte nicht mehr selbst voten müssen … die videos sollen mit jedem schnitt besser werden“).

Im einfachen Modus entsteht ein Short (Abend-Video, 🎬) nicht mehr einmal, sondern im Wettbewerb: Die erste Fassung ist
die wie bisher (regie_lernen + geschmack + deine Regeln). Dazu kommen [nachtschicht].fassungen − 1 weitere, die sich in
genau EINER Stellschraube von ihr unterscheiden (ARTEN, im Wechsel je Video): anderer Aufbau, anderes Tempo, andere
Zeitlupe, andere Länge (±10 s, nie gegen deine ⏱️/⏳-Grenze), mehr Anlauf, anderer Song. Alle werden gerendert und am
fertigen Video gemessen (Cutter-Maßstab, kritik.bewerte ohne KI); Fassungen mit verletztem K.O.-Tor fallen raus. Die
besten zwei nach Messung treten blind vor dem KI-Cutter an (kritik.ki_paarvergleich: zwei Kontaktbögen, zwei
Wellenformen, zwei Pläne – „welche ist besser und warum“). Die Siegerin kommt zu dir; die anderen siehst du nie
(entwuerfe.auto_verworfen = 'nachtschicht:…', wie still verworfene Entwürfe; nichts wird gelöscht).

Lernen ohne dein Tippen: jeder KI-Paarvergleich steht in `wettbewerbe` (quelle 'ki') und lehrt
  - geschmack.statistik: nur die Schrauben, in denen sich Siegerin und Verliererin unterscheiden (Gewicht KI_GEWICHT ×
    Sicherheit) – ein Vergleich zweier gleicher Aufbauten sagt nichts über den Aufbau;
  - massstab: als KI-Paar (blind, Maske wie die KI-Note) – die Faktoren der 14 Kriterien.
Die Messung selbst (regel_score, quelle 'messung') lehrt nie – sie wählt nur aus (massstab, Regel 3: Entscheidungen
lesen f, schreiben nie in Lehrer-Tabellen). Dein ✅/❌ zählt weiter (Gewicht 1 statt 0,34), das Publikum geht vor beiden,
sobald Zahlen kommen.

Grenzen: nur einfacher Modus, nur Short ohne 🔥 Viral und ohne neue Fassung nach ❌ (die muss „anders“ sein und tauscht
Szenen); keine weitere Fassung nach [nachtschicht].max_minuten oder unter frei_gb freiem Platz im Regie-Ordner; alles
unter der einen Rechen-Sperre des Aufrufers (sitzung, lernbot.baue_entwurf). Stirbt der Lauf nach dem Rendern der ersten
Fassung, gibt aufraeumen() sie nach VERWAIST_MIN Minuten frei – sonst bliebe der Abend ohne Video.
"""

from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import time
from datetime import timedelta
from pathlib import Path

from . import entwurf as entwurf_modul
from . import geschmack, kritik, regie, regie_lernen, stile
from .konfig import Konfig
from .zeit import iso, jetzt

log = logging.getLogger("pipeline")

BASIS = "nachtschicht:basis"          # die erste Fassung, solange der Wettbewerb läuft (verliert sie: FASSUNG)
FASSUNG = "nachtschicht:fassung"      # eine Mitbewerberin – bleibt so markiert, wenn sie verliert
GLEICH = "nachtschicht:gleich"        # Plan Zeichen für Zeichen wie eine andere Fassung – nicht gerendert
MARKEN = (BASIS, FASSUNG, GLEICH)
ARTEN = ("aufbau", "tempo", "zeitlupe", "laenge", "anlauf", "song")
ART_NAMEN = {"basis": "wie gelernt", "aufbau": "anderer Aufbau", "tempo": "anderes Tempo",
             "zeitlupe": "andere Zeitlupe", "laenge": "andere Länge", "anlauf": "mehr Anlauf", "song": "anderer Song"}
LAENGE_SCHRITT_S = 10.0
ANLAUF_FAKTOR = 1.5
ANLAUF_MAX = {"puffer_vor_s": 6.0, "puffer_nach_s": 4.0}   # wie die gelernten Grenzen in regie_lernen
SONG_MALUS = 10.0
VERWAIST_MIN = 90
NICHT_VERGLICHEN = frozenset({"name", "parameter", "hinweise", "wettbewerb"})


def aktiv(konfig: Konfig) -> bool:
    """Nur im einfachen Modus ([regie].geschmack aus einstellungen.EINFACH_FEST), mit [nachtschicht].an und mindestens
    zwei Fassungen. /experte: wie bisher eine Fassung."""
    return bool(konfig.wert("regie.geschmack", False)) and bool(konfig.wert("nachtschicht.an", True)) \
        and int(konfig.wert("nachtschicht.fassungen", 4)) >= 2


def markiere(con: sqlite3.Connection, entwurf_id: int, marke: str = BASIS) -> None:
    """Die Lern-Bot-Schleife schickt nur gerenderte Entwürfe ohne auto_verworfen – so lange markiert, kommt nichts raus."""
    con.execute("UPDATE entwuerfe SET auto_verworfen = ? WHERE id = ?", (marke, entwurf_id))


def freigeben(con: sqlite3.Connection, entwurf_id: int, info: dict | None = None) -> None:
    """Die Siegerin darf zu dir: Marke weg. info (fassungen, ki, art, grund) kommt als „wettbewerb“ in ihre Schnittliste –
    lernbot.wettbewerb_zeile zeigt daraus die 🥊-Zeile. Ohne info (Fehlerweg, aufraeumen) nur die Marke."""
    if info:
        z = con.execute("SELECT schnittliste FROM entwuerfe WHERE id = ?", (entwurf_id,)).fetchone()
        if z is not None:
            try:
                pfad = Path(z["schnittliste"])
                liste = json.loads(pfad.read_text(encoding="utf-8"))
                liste["wettbewerb"] = info
                tmp = pfad.with_suffix(".tmp")
                tmp.write_text(json.dumps(liste, ensure_ascii=False, indent=2), encoding="utf-8")
                tmp.replace(pfad)
            except (OSError, ValueError):
                log.exception("Wettbewerb in die Schnittliste von #%s schreiben", entwurf_id)
    con.execute("UPDATE entwuerfe SET auto_verworfen = NULL WHERE id = ? AND auto_verworfen IN (?, ?)",
                (entwurf_id, BASIS, FASSUNG))


def aufraeumen(con: sqlite3.Connection) -> list[int]:
    """Verwaiste erste Fassungen freigeben: Stirbt der Lauf nach dem Rendern (Strom, Update, Neustart), bliebe das
    Abend-Video sonst für immer markiert und käme nie. Nach VERWAIST_MIN Minuten seit dem Anlegen – länger dauert kein
    Wettbewerb ([nachtschicht].max_minuten plus Rendern und Messen). Aufrufer: sitzung.verarbeite (alle 10 min)."""
    grenze = iso(jetzt() - timedelta(minutes=VERWAIST_MIN))
    ids = [int(z["id"]) for z in con.execute(
        "SELECT id FROM entwuerfe WHERE auto_verworfen = ? AND erstellt < ? ORDER BY id", (BASIS, grenze))]
    for eid in ids:
        log.warning("Nachtschicht: erste Fassung #%s blieb markiert (Lauf abgebrochen?) – gebe sie frei", eid)
        freigeben(con, eid)
    return ids


# --- Fassungen -------------------------------------------------------------------------------------------------

def _grenzen(wert: float, schluessel: str) -> float:
    unten, oben = stile.GRENZEN[schluessel]
    return round(max(unten, min(oben, wert)), 3)


def _variante(p: dict, art: str, basis_id: int, konfig: Konfig, fmt_regeln: dict, track_id: int | None) -> dict | None:
    """p mit genau einer anderen Stellschraube (Kopie) oder None, wenn die Art hier nichts ändern kann."""
    q = dict(p)
    wahl = dict(q.get("geschmack") or {})
    lauf = basis_id // len(ARTEN)        # welche der Alternativen bzw. Richtungen dieses Video bekommt
    if art == "aufbau":
        aktuell = q.get("stil")
        andere = [o for o in stile.ROTATION if o != aktuell]
        if not andere:
            return None
        neu = andere[lauf % len(andere)]
        if aktuell in stile.STILE:   # die Faktoren des bisherigen Stils herausrechnen, sonst wirkten beide
            for schluessel, faktor in stile.STILE[aktuell]["faktoren"].items():
                q[schluessel] = round(float(q.get(schluessel, 1.0)) / faktor, 3)
        q = stile.mit_stil(q, neu)
        wahl["aufbau"] = neu
    elif art == "tempo":
        aktuell = wahl.get("tempo")
        neu = "ruhig" if aktuell == "schnell" else "schnell" if aktuell == "ruhig" else ("ruhig", "schnell")[lauf % 2]
        faktor = geschmack.TEMPO[neu] / (geschmack.TEMPO[aktuell] if aktuell in geschmack.TEMPO else 1.0)
        q["seg_min_faktor"] = _grenzen(float(q.get("seg_min_faktor", 1.0)) * faktor, "seg_min_faktor")
        if q["seg_min_faktor"] == p.get("seg_min_faktor"):
            return None
        wahl["tempo"] = neu
    elif art == "zeitlupe":
        if not konfig.wert("regie.effekte.an", True):
            return None
        aktuell = wahl.get("zeitlupe") or next((n for n, w in geschmack.ZEITLUPEN.items() if w == q.get("max_lupen")),
                                               None)
        neu = "wenig" if aktuell == "viel" else "viel"
        q["max_lupen"] = geschmack.ZEITLUPEN[neu]
        wahl["zeitlupe"] = neu
    elif art == "laenge":
        ziel = float(q.get("ziel_dauer_s") or fmt_regeln["ziel_s"])
        richtung, grenze = q.get("laenge_richtung"), q.get("laenge_grenze_s")
        schritt = LAENGE_SCHRITT_S if lauf % 2 == 0 else -LAENGE_SCHRITT_S
        if richtung == "kurz" and grenze is not None and ziel - LAENGE_SCHRITT_S < float(grenze):
            schritt = LAENGE_SCHRITT_S            # „mindestens …“: nie darunter
        elif richtung == "lang" and grenze is not None and ziel + LAENGE_SCHRITT_S > float(grenze):
            schritt = -LAENGE_SCHRITT_S           # „höchstens …“: nie darüber
        neu = max(float(fmt_regeln["min_s"]), min(float(fmt_regeln["max_s"]), ziel + schritt))
        if abs(neu - ziel) < LAENGE_SCHRITT_S / 2:
            return None
        q["ziel_dauer_s"] = round(neu, 1)
    elif art == "anlauf":
        for schluessel, oben in ANLAUF_MAX.items():
            q[schluessel] = round(min(oben, float(q.get(schluessel, regie.PARAMETER[schluessel])) * ANLAUF_FAKTOR), 2)
        if all(q[s] == p.get(s) for s in ANLAUF_MAX):
            return None
    elif art == "song":
        if track_id is None:
            return None
        q["track_malus"] = {**(q.get("track_malus") or {}), str(track_id): SONG_MALUS}
    else:
        return None
    wahl["experiment"] = None   # kein „mutig“-Versuch: die Fassung ist der Versuch
    q["geschmack"] = wahl
    if isinstance(q.get("autonom"), dict):   # ein Publikums-Versuch gilt nur für die erste Fassung (lern_experimente)
        q["autonom"] = {**q["autonom"], "exploration": None}
    q["nachtschicht"] = {"art": art, "basis": basis_id}
    return q


def varianten(p: dict, anzahl: int, basis_id: int, konfig: Konfig, fmt: str,
              track_id: int | None) -> list[tuple[str, dict]]:
    """Bis zu anzahl Fassungen (Art, Parameter) zur ersten Fassung mit Parametern p – die Arten im Wechsel je Video
    (ARTEN ab basis_id), jede höchstens einmal; was nichts ändern würde, fällt aus."""
    fmt_regeln = regie.format_regeln(konfig, fmt)[0]
    ergebnis: list[tuple[str, dict]] = []
    for i in range(len(ARTEN)):
        if len(ergebnis) >= anzahl:
            break
        art = ARTEN[(basis_id + i) % len(ARTEN)]
        q = _variante(p, art, basis_id, konfig, fmt_regeln, track_id)
        if q is not None:
            ergebnis.append((art, q))
    return ergebnis


# --- Wettbewerb ------------------------------------------------------------------------------------------------

def _frei_gb(konfig: Konfig) -> float:
    pfad = regie.ordner(konfig)
    while not pfad.exists() and pfad.parent != pfad:
        pfad = pfad.parent
    try:
        return shutil.disk_usage(pfad).free / 1e9
    except OSError:
        return float("inf")


def _kennung(con: sqlite3.Connection, entwurf_id: int) -> str | None:
    """Die Schnittliste ohne Name/Parameter/Hinweise als Text – gleich heißt: dasselbe Video."""
    z = con.execute("SELECT schnittliste FROM entwuerfe WHERE id = ?", (entwurf_id,)).fetchone()
    try:
        liste = json.loads(Path(z["schnittliste"]).read_text(encoding="utf-8"))
    except (TypeError, OSError, ValueError):
        return None
    return json.dumps({k: v for k, v in liste.items() if k not in NICHT_VERGLICHEN}, sort_keys=True, default=str)


def _protokoll(con: sqlite3.Connection, video: int, sieger: int, verlierer: int, quelle: str,
               sicherheit: float | None, urteil: dict | None, version: str | None) -> None:
    con.execute("""INSERT INTO wettbewerbe (video, sieger, verlierer, quelle, sicherheit, gruende, text, ki_version,
                                            erstellt) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (video, sieger, verlierer, quelle, sicherheit, json.dumps((urteil or {}).get("gruende") or []),
                 (urteil or {}).get("begruendung"), version, iso(jetzt())))


def _ordner(konfig: Konfig, basis_id: int) -> Path:
    return regie.ordner(konfig) / f"wettbewerb-{basis_id}"


def wettbewerb(con: sqlite3.Connection, konfig: Konfig, fmt: str, basis_id: int, *,
               nur_matches: set[str] | None, ziel: dict | None = None, beginn: float | None = None) -> dict:
    """Weitere Fassungen zur schon gerenderten ersten Fassung basis_id bauen, alle messen, die besten zwei vor den
    KI-Cutter, die Siegerin freigeben. Rückgabe {"video": Siegerin, "fassungen": gerendert, "ki": KI hat entschieden,
    "art": Art der Siegerin, "grund": Satz des KI-Cutters oder None, "verlierer": [...]}.
    Die erste Fassung muss vorher markiert sein (markiere) – sonst schickte die Schleife sie schon los.
    Ein Fehler beim Planen oder Rendern einer weiteren Fassung kostet nur diese Fassung; der Aufrufer fängt den Rest und
    gibt im Zweifel die erste Fassung frei (sitzung._wettbewerb, lernbot.baue_entwurf)."""
    from . import massstab   # hier wie in kritik.bewerte: massstab lädt numpy

    t0 = beginn if beginn is not None else time.monotonic()
    fassungen_max = int(konfig.wert("nachtschicht.fassungen", 4))
    max_s = 60.0 * float(konfig.wert("nachtschicht.max_minuten", 25))
    frei_gb = float(konfig.wert("nachtschicht.frei_gb", 3.0))
    zeile = con.execute("SELECT * FROM entwuerfe WHERE id = ?", (basis_id,)).fetchone()
    if zeile is None:
        raise ValueError(f"Entwurf #{basis_id} gibt es nicht")
    p = json.loads(zeile["parameter"] or "{}") or {}
    if ziel is None:
        ziel = regie_lernen.aktuelle(con, konfig, fmt)[1]
    kandidaten, arten = [basis_id], {basis_id: "basis"}
    kennungen = {basis_id: _kennung(con, basis_id)}
    for art, q in varianten(p, fassungen_max - 1, basis_id, konfig, fmt, zeile["track_id"]):
        if time.monotonic() - t0 > max_s:
            log.info("Nachtschicht #%s: Zeit um (%.0f min) – keine weitere Fassung", basis_id, max_s / 60)
            break
        if (frei := _frei_gb(konfig)) < frei_gb:
            log.warning("Nachtschicht #%s: nur %.1f GB frei – keine weitere Fassung", basis_id, frei)
            break
        try:
            e = regie.erstelle(con, konfig, fmt, parameter=q, ziel=ziel, nur_matches=nur_matches)
        except regie.RegieFehler as fehler:
            log.info("Nachtschicht #%s: Fassung „%s“ nicht möglich (%s)", basis_id, art, fehler)
            continue
        eid = int(e["entwurf"])
        kennung = _kennung(con, eid)
        if kennung is not None and kennung in kennungen.values():
            markiere(con, eid, GLEICH)   # dasselbe Video noch einmal rendern bringt nichts
            log.info("Nachtschicht #%s: Fassung „%s“ (#%s) wäre dasselbe Video – übersprungen", basis_id, art, eid)
            continue
        markiere(con, eid, FASSUNG)
        try:
            entwurf_modul.entwurf(con, konfig, eid)
        except Exception:  # noqa: BLE001 – nur diese Fassung fällt aus (Render-Panne), der Wettbewerb läuft weiter
            log.exception("Nachtschicht #%s: Fassung „%s“ (#%s) nicht gerendert", basis_id, art, eid)
            continue
        kandidaten.append(eid)
        arten[eid], kennungen[eid] = art, kennung
    noten: dict[int, tuple[float, str | None]] = {}
    for eid in kandidaten:
        try:
            k = kritik.bewerte(con, konfig, eid, ki=False, lernen=False)
            noten[eid] = (float(k["regel_score"]), k.get("tor"))
        except Exception:  # noqa: BLE001 – ungemessen zählt wie Note 0, fällt aber nicht raus
            log.exception("Nachtschicht #%s: Fassung #%s nicht gemessen", basis_id, eid)
    ohne_tor = [e for e in kandidaten if e in noten and not noten[e][1]]
    rang = sorted(ohne_tor or kandidaten, key=lambda e: (-noten.get(e, (0.0, None))[0], e))
    sieger, ki, grund, ki_zeile = rang[0], False, None, None
    if len(rang) >= 2:
        a, b = (rang[0], rang[1]) if basis_id % 2 == 0 else (rang[1], rang[0])   # mal die bessere vorn, mal hinten
        urteil, hinweis = kritik.ki_paarvergleich(con, konfig, a, b, _ordner(konfig, basis_id))
        if urteil is not None:
            s_id, v_id = (a, b) if urteil["sieger"] == "a" else (b, a)
            sicherheit = float(urteil.get("sicherheit") or 0.5)
            ki_zeile = (s_id, v_id, sicherheit, urteil)
            if sicherheit >= float(konfig.wert("nachtschicht.ki_sicherheit", 0.55)):
                sieger, ki, grund = s_id, True, (urteil.get("begruendung") or None)
            log.info("Nachtschicht #%s: KI-Cutter für #%s (Sicherheit %.0f %%)%s", basis_id, s_id, 100 * sicherheit,
                     "" if sieger == s_id else " – zu unsicher, die Messung entscheidet")
        elif hinweis:
            log.info("Nachtschicht #%s: %s", basis_id, hinweis)
    if ki_zeile is not None:   # erst jetzt: jede Zeile nennt das Video, das zu dir kam
        s_id, v_id, sicherheit, urteil = ki_zeile
        _protokoll(con, sieger, s_id, v_id, "ki", sicherheit, urteil, kritik.paar_version(konfig))
    for eid in kandidaten:
        if eid != sieger:
            _protokoll(con, sieger, sieger, eid, "messung", None, None, None)
            markiere(con, eid, FASSUNG)   # auch die erste Fassung, wenn sie verliert
    info = {"fassungen": len(kandidaten), "ki": ki, "art": arten[sieger], "grund": grund}
    freigeben(con, sieger, info)
    log.info("Nachtschicht #%s: %d Fassungen, Siegerin #%s (%s, Messung %.0f)%s", basis_id, len(kandidaten), sieger,
             ART_NAMEN.get(arten[sieger], arten[sieger]), noten.get(sieger, (0.0, None))[0],
             " – KI-Cutter" if ki else "")
    try:
        massstab.nachziehen(con, konfig, f"Wettbewerb #{basis_id}")
    except Exception:  # noqa: BLE001 – Lernen ist Zugabe, das Video ist fertig
        log.exception("Cutter-Maßstab nach dem Wettbewerb")
    return {"video": sieger, **info, "verlierer": [e for e in kandidaten if e != sieger]}
