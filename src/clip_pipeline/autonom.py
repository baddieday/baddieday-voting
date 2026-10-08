"""Kleine, robuste Publikumsschleife ohne Bewertungs-Pflicht.

Ein Video ist eine Gruppe, auch auf mehreren Plattformen. Messungen ersetzen y,
werden nie als weitere Trainingsbeispiele addiert. Ridge-Prior, gedeckeltes Gewicht,
zeitlicher Holdout und ein unveränderlicher Verlauf schützen den Champion.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from . import lern_features as features
from .zeit import aus_iso, iso, jetzt

MODELL_VERSION = 1


def _json(x):
    return json.dumps(x, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _hash(x):
    return hashlib.sha256(_json(x).encode()).hexdigest()


def snapshot_speichern(con, post_id: int, daten: dict) -> None:
    """Einmalig einfrieren; bestehende Post-/Bewertungsdaten bleiben unangetastet."""
    if con.execute("SELECT 1 FROM video_lerndaten WHERE post_id=?", (post_id,)).fetchone():
        return
    post = dict(con.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone())
    snap = copy.deepcopy(daten)
    liste = snap.get("schnittliste") or {}
    if not liste and post.get("entwurf_id"):
        entwurf = con.execute("SELECT format,schnittliste,parameter FROM entwuerfe WHERE id=?",
                              (post["entwurf_id"],)).fetchone()
        if entwurf:
            snap["format"] = entwurf["format"]
            snap["parameter"] = json.loads(entwurf["parameter"])
            try:
                liste = json.loads(Path(entwurf["schnittliste"]).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                snap["historisch_unvollstaendig"] = True
    snap.update(dauer_s=post["dauer_s"], plattform=post["plattform"], gepostet_utc=post["gepostet_utc"],
                feature_version=features.VERSION, schnittliste=liste)
    snap.setdefault("format", liste.get("format", "short"))
    p = liste.get("parameter") or snap.get("parameter") or {}
    auto = p.get("autonom") or {}
    version = int(auto.get("version", 0))
    x = features.extrahiere(snap)
    con.execute("INSERT OR IGNORE INTO video_lerndaten VALUES(?,?,?,?,?,?)",
                (post_id, post["ziel"], _json(snap), _json(x), version, iso(jetzt())))
    exp = auto.get("exploration")
    if exp:
        con.execute("INSERT OR IGNORE INTO lern_experimente "
                    "(post_id,hypothese,variable,erwartet,confidence_vorher,details) VALUES(?,?,?,?,?,?)",
                    (post_id, exp["hypothese"], exp["variable"], exp.get("erwartet"),
                     auto.get("confidence", 0), _json(exp)))


def zeitgewicht(alter_tage: float) -> float:
    # Ein Viertel bleibt Langzeitwissen; der aktuelle Anteil halbiert sich in 60 Tagen.
    return .25 + .75 * 2 ** (-max(0., alter_tage)/60.)


def _beispiele(con) -> list[dict]:
    from .audience_score import berechne

    posts = [dict(r) for r in con.execute("SELECT * FROM posts ORDER BY gepostet_utc,id")]
    basis, gruppen = [], {}
    for p in posts:
        snapshot_speichern(con, p["id"], {"dauer_s": p["dauer_s"], "rezept": json.loads(p["rezept"]),
                                         "merkmale": json.loads(p["merkmale"])})
        rows = con.execute("SELECT * FROM publikum_messungen WHERE post_id=? "
                           "ORDER BY gemessen_utc DESC,id DESC", (p["id"],)).fetchall()
        if not rows:
            continue
        kandidaten = []
        for row in rows:
            m = dict(row)
            if aus_iso(m["gemessen_utc"]) < aus_iso(p["gepostet_utc"]):
                continue
            b = [v for v in basis if v["post"]["ziel"] != p["ziel"]
                 and v["post"]["gepostet_utc"] < p["gepostet_utc"]
                 and v["messung"]["gemessen_utc"] <= m["gemessen_utc"]]
            s = berechne(p, m, b)
            if s["score"] is not None and s["confidence"] > 0:
                # Ein API-Zählerupdate verdrängt keine ältere Watchtime-Messung.
                # Ganze Messungen auswählen, keine Zähler verschiedener Alter mischen.
                coverage = sum(v["gewicht"] for k, v in s["components"].items() if k != "reichweite")
                kandidaten.append((s["confidence"], coverage, m["gemessen_utc"], m["id"], m, s))
        if not kandidaten:
            continue
        _, _, _, _, m, s = max(kandidaten, key=lambda v: v[:4])
        # Referenz nur früher veröffentlichte Videos; niemals dasselbe Video als Basis.
        basis.append({"post": p, "messung": m})
        h = _hash({k: v for k, v in m.items() if k not in ("id", "erstellt", "roh")})
        con.execute("INSERT INTO audience_ergebnisse VALUES(?,?,?,?,?,?,?) "
                    "ON CONFLICT(post_id) DO UPDATE SET messung_id=excluded.messung_id,"
                    "input_hash=excluded.input_hash,score=excluded.score,confidence=excluded.confidence,"
                    "teile=excluded.teile,aktualisiert=excluded.aktualisiert",
                    (p["id"], m["id"], h, s["score"], s["confidence"], _json(s), iso(jetzt())))
        data = con.execute("SELECT * FROM video_lerndaten WHERE post_id=?", (p["id"],)).fetchone()
        snapshot = json.loads(data["snapshot"])
        gruppen.setdefault(p["ziel"], []).append({"id": p["id"], "x": json.loads(data["features"]),
            "y": float(s["score"]), "confidence": float(s["confidence"]), "zeit": p["gepostet_utc"],
            "messzeit": m["gemessen_utc"], "format": snapshot["format"], "dauer_s": p["dauer_s"]})
    ergebnis = []
    for gruppe, posts_ in gruppen.items():
        # Crossposts sind EIN Video; Reichweite erzeugt keine künstlich größere Stichprobe.
        conf = sum(p["confidence"] for p in posts_)
        y = sum(p["y"]*p["confidence"] for p in posts_)/conf
        p = posts_[0]
        ergebnis.append({**p, "gruppe": gruppe, "ids": [q["id"] for q in posts_], "y": y,
                         "confidence": conf/len(posts_), "zeit": min(q["zeit"] for q in posts_),
                         "messzeit": max(q["messzeit"] for q in posts_)})
    return sorted(ergebnis, key=lambda e: (e["zeit"], e["gruppe"]))


def _gewicht(e, zeit):
    alter = (aus_iso(zeit)-aus_iso(e["zeit"])).total_seconds()/86400
    # Virale Qualität wirkt bis 1,5-fach, nie unbeschränkt durch Views.
    return max(.05, e["confidence"]) * zeitgewicht(alter) * (1 + .5*max(0., e["y"]))


def trainiere(beispiele: list[dict], zeit: str) -> dict:
    """Gewichtete Ridge-Regression mit neutralem Prior und beobachteten Merkmalen."""
    namen = sorted({k for e in beispiele for k in e["x"]})
    if not beispiele:
        return {"namen": [], "mittel": {}, "gewichte": {}, "bias": 0., "beispiele": []}
    mittel = {}
    for k in namen:
        bekannt = [e["x"][k] for e in beispiele if k in e["x"]]
        mittel[k] = 0. if ":" in k and k.startswith(("track:", "stimmung:", "uebergang:")) else float(np.mean(bekannt))
    x = np.array([[1.] + [e["x"].get(k, mittel[k])-mittel[k] for k in namen] for e in beispiele])
    w = np.array([_gewicht(e, zeit) for e in beispiele])
    y = np.array([e["y"] for e in beispiele])
    prior = np.diag([1.] + [1.2]*len(namen))
    beta = np.linalg.solve(x.T @ (w[:, None]*x) + prior, x.T @ (w*y))
    beta = np.clip(beta, -.65, .65)
    return {"namen": namen, "mittel": mittel, "bias": float(beta[0]),
            "gewichte": dict(zip(namen, map(float, beta[1:]))), "beispiele": beispiele,
            "feature_version": features.VERSION, "verfahren": "ridge-prior-v1"}


def vorhersage(modell: dict, x: dict) -> float:
    mittel = modell.get("mittel", {})
    wert = modell.get("bias", 0.) + sum(w*(x.get(k, mittel.get(k, 0.))-mittel.get(k, 0.))
                                        for k, w in modell.get("gewichte", {}).items())
    return max(-1., min(1., wert))


def validiere(beispiele: list[dict], champion: dict | None, zeit: str) -> dict:
    """Nur neue Videogruppen dürfen den bereits trainierten Champion prüfen.

    Nach erfolgreicher Prüfung wird auf allen Daten neu gefittet; die gespeicherten
    Fehler stammen explizit vom Fit OHNE Holdout, nicht vom finalen Modell.
    """
    gelernt = {e["gruppe"] for e in (champion or {}).get("beispiele", [])}
    n_holdout = max(1, math.ceil(len(beispiele)*.2))
    holdout = beispiele[-n_holdout:]
    holdout = [e for e in holdout if e["gruppe"] not in gelernt]
    ids = {e["gruppe"] for e in holdout}
    grenzzeit = min((e["messzeit"] for e in holdout), default=zeit)
    zug = [e for e in beispiele if e["gruppe"] not in ids and e["messzeit"] <= grenzzeit]
    info = {"training": [e["gruppe"] for e in zug], "holdout": sorted(ids), "promote": False,
            "grund": "Mindestens vier Videos und neue, unabhängige Publikumsdaten erforderlich."}
    if len(zug) < 3 or not holdout:
        return info
    probe = trainiere(zug, zeit)
    alt = champion or {"bias": 0., "gewichte": {}}
    def fehler(m):
        return sum(_gewicht(e, zeit)*(e["y"]-vorhersage(m, e["x"]))**2 for e in holdout) / sum(_gewicht(e, zeit) for e in holdout)
    neu, vorher = fehler(probe), fehler(alt)
    # Kein schlechterer Challenger; ein kleiner Sicherheitsabstand verhindert Rauschen.
    info.update(challenger_mse=neu, champion_mse=vorher, promote=neu+.002 < vorher,
                grund="Zeitlicher Vergleich auf bisher ungesehenen Videos.")
    return info


def champion(con):
    row = con.execute("SELECT * FROM lernstaende WHERE status='champion'").fetchone()
    if row is None:
        return None
    return {**dict(row), "modell": json.loads(row["modell"])}


def aktualisieren(con, konfig=None) -> dict:
    """Atomar und wiederholbar, auch innerhalb einer Messungs-Transaktion."""
    eigenstaendig = not con.in_transaction
    if eigenstaendig:
        con.execute("BEGIN IMMEDIATE")
    con.execute("SAVEPOINT autonom_lernen")
    try:
        beispiele = _beispiele(con)
        fingerprint = _hash({"version": MODELL_VERSION, "daten": [
            {k: (round(v, 3) if isinstance(v, float) else v) for k, v in e.items()
             if k not in ("id", "messzeit")} for e in beispiele]})
        vorhanden = con.execute("SELECT version FROM lernstaende WHERE input_hash=?", (fingerprint,)).fetchone()
        if not beispiele or vorhanden:
            ergebnis = {"geaendert": False, "version": vorhanden[0] if vorhanden else 0}
        else:
            alt = champion(con)
            zeit = max(e["messzeit"] for e in beispiele)
            pruefung = validiere(beispiele, alt["modell"] if alt else None, zeit)
            modell = trainiere(beispiele, zeit)
            n = len(beispiele)
            confidence = n/(n+6.) * sum(e["confidence"] for e in beispiele)/n
            version = con.execute("SELECT COALESCE(MAX(version),0)+1 FROM lernstaende").fetchone()[0]
            if pruefung["promote"]:
                con.execute("UPDATE lernstaende SET status='archiv' WHERE status='champion'")
            langfristig = sum(e["y"] for e in beispiele)/n
            aktuell = sum(e["y"]*_gewicht(e, zeit) for e in beispiele)/sum(_gewicht(e, zeit) for e in beispiele)
            con.execute("INSERT INTO lernstaende VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (version, iso(jetzt()), fingerprint, alt["version"] if alt else None,
                         "champion" if pruefung["promote"] else "challenger", n,
                         _json([e["gruppe"] for e in beispiele]), _json(modell), confidence,
                         _json(pruefung), langfristig, aktuell))
            for e in beispiele:
                for pid in e["ids"]:
                    con.execute("UPDATE lern_experimente SET tatsaechlich=?,confidence_nachher=? WHERE post_id=?",
                                (e["y"], confidence, pid))
            ergebnis = {"geaendert": True, "version": version, "champion": pruefung["promote"],
                        "datenmenge": n, "validierung": pruefung}
        con.execute("RELEASE autonom_lernen")
        if eigenstaendig:
            con.execute("COMMIT")
        return ergebnis
    except BaseException:
        con.execute("ROLLBACK TO autonom_lernen")
        con.execute("RELEASE autonom_lernen")
        if eigenstaendig:
            con.execute("ROLLBACK")
        raise


def _optionen(fmt):
    return {"ziel_dauer_s": [45., 55., 65., 72.] if fmt == "short" else [80., 95., 110., 120.],
            "seg_min_faktor": [1., 1.4, 1.8], "beats_pro_schnitt": [1, 2, 4],
            "hook_staerkster": [False, True], "effekt_hektik": [.5, .9, 1.2],
            "musik_pegel": [.15, .3, .5]}


def _setze_x(x, k, wert, fmt):
    x = dict(x)
    if k == "ziel_dauer_s":
        x.update(features.dauer_features(wert, fmt))
    else:
        x[k] = float(wert)/features.STEUERUNG[k]
    return x


def dein_gewicht(con) -> float:
    """Wie viele Publikums-Videos deine bisherigen Bewertungen im Publikums-Modell wert sind: je 10 eins, 2 bis 12."""
    try:
        n_du = con.execute("SELECT COUNT(*) FROM entwurf_bewertungen").fetchone()[0]
    except Exception:  # noqa: BLE001 – alte Datenbank ohne Tabelle: wie bisher
        n_du = 0
    return max(2., min(12., n_du/10.))


def plan_parameter(con, konfig, fmt, parameter, musik_ziele):
    """Historische Präferenzen sind ein schrumpfender Prior, Publikum plant autonom."""
    from .regie import PARAMETER, format_regeln

    p, musik = copy.deepcopy(parameter), copy.deepcopy(musik_ziele)
    if fmt not in ("short", "zusammenschnitt"):
        return p, musik
    regeln, _ = format_regeln(konfig, fmt)
    unten, oben = (30., 75.) if fmt == "short" else (75., 120.)
    # Gerundet (08.10.): 45 × 55/45 ergibt 55,00000000000001 – dann galt 55 nicht als „vorher“, und ein Versuch
    # „55 s“ wäre keiner gewesen (deine Grenze 55 aus regeln.laenge ist der häufigste Start)
    prior_ziel = round(max(45. if fmt == "short" else 75., min(oben, regeln["ziel_s"]*p["dauer_faktor"])), 3)
    p["ziel_dauer_s"] = prior_ziel
    p.setdefault("hook_staerkster", False)
    alt = champion(con)
    if not alt:
        exploration = None
        zaehler = con.execute("SELECT COUNT(*) FROM entwuerfe WHERE format=?", (fmt,)).fetchone()[0]
        # Auch ohne Champion Datenvielfalt erzeugen; sonst könnte der feste Prior
        # seine eigenen Varianten nie mit Publikum vergleichen.
        if (zaehler+1) % 7 == 0:
            optionen = _optionen(fmt)
            k = list(optionen)[(zaehler//7) % len(optionen)]
            vorher = p.get(k, PARAMETER.get(k, False))
            werte = [v for v in optionen[k] if v != vorher]
            wert = werte[len(werte)//2]
            p[k] = wert
            exploration = {"variable": k, "vorher": vorher, "wert": wert,
                           "hypothese": f"{k}={wert} wird erstmals mit Publikum untersucht.", "erwartet": None}
        p["autonom"] = {"version": 0, "confidence": 0., "beitraege": {}, "exploration": exploration}
        return p, musik
    modell = alt["modell"]
    beispiele = [e for e in modell["beispiele"] if e["format"] == fmt]
    n = len(beispiele)
    if not n:
        return p, musik
    # Dein Geschmack als Start (05.10., Florian: „mein persönlicher Impact wird zu wenig gewertet“): deine Daumen
    # zählen wie dein_gewicht(con) Videos (je 10 Bewertungen eins, 2 bis 12) – vorher fest 2, d. h. 120 Daumen
    # waren nach 8 Publikums-Videos nur noch 20 % wert. Das Publikum übernimmt, sobald es genug echte Zahlen gibt.
    anteil = n/(n+dein_gewicht(con))
    for k in ("moment_bonus", "stimmung_bonus", "track_malus"):
        p[k] = {name: value*(1-anteil) for name, value in p.get(k, {}).items()}
    # Dieselben Daten wirken auch in der vorhandenen Musik-/Stimmungsauswahl.
    for k, w in modell["gewichte"].items():
        if k.startswith("track:"):
            track = k.split(":", 1)[1]
            p["track_malus"][track] = p["track_malus"].get(track, 0.) - 2*anteil*w
        elif k.startswith("stimmung:"):
            stimmung = k.split(":", 1)[1]
            p["stimmung_bonus"][stimmung] = p["stimmung_bonus"].get(stimmung, 0.) + 2*anteil*w
    optionen = _optionen(fmt)
    x = dict(modell["mittel"])
    for k in optionen:
        wert = p.get(k, PARAMETER.get(k, False))
        x = _setze_x(x, k, wert, fmt)
    beitraege, sicherheiten = {}, {}
    for k, werte in optionen.items():
        if k != "ziel_dauer_s" and not any(k in e["x"] for e in beispiele):
            continue
        vorher = p.get(k, PARAMETER.get(k, False))
        def nutzen(wert):
            probe = _setze_x(x, k, wert, fmt)
            # Bevorzugt gestützte Optionen; Regression extrapoliert nicht blind an die Grenze.
            if k == "ziel_dauer_s":
                support = sum(e["confidence"]*math.exp(-.5*((e["dauer_s"]-wert)/8.)**2) for e in beispiele)
            else:
                skala = features.STEUERUNG[k]
                support = sum(e["confidence"]*math.exp(-.5*((e["x"].get(k, .5)-float(wert)/skala)/.22)**2)
                              for e in beispiele if k in e["x"])
            return vorhersage(modell, probe)-.06/(1+support)
        scores = sorted([(nutzen(v), i, v) for i, v in enumerate(werte)], reverse=True)
        wert = scores[0][2]
        diff = nutzen(wert)-nutzen(vorher)
        if diff > .005:
            p[k] = wert
            beitraege[k] = round(diff, 4)
            x = _setze_x(x, k, wert, fmt)
        sicherheiten[k] = abs(scores[0][0]-scores[1][0]) if len(scores) > 1 else 1.
    # 1 von 7 tatsächlich erzeugten Entwürfen: genau eine unsichere Stellschraube ändern.
    zaehler = con.execute("SELECT COUNT(*) FROM entwuerfe WHERE format=?", (fmt,)).fetchone()[0]
    exploration = None
    if (zaehler+1) % 7 == 0 and sicherheiten:
        k = min(sicherheiten, key=sicherheiten.get)
        aktuell = p.get(k, PARAMETER.get(k, False))
        alternativen = [v for v in optionen[k] if v != aktuell]
        wert = alternativen[(zaehler//7) % len(alternativen)]
        exploration = {"variable": k, "vorher": aktuell, "wert": wert,
                       "hypothese": f"{k}={wert} könnte das Publikumsergebnis verbessern.",
                       "erwartet": vorhersage(modell, _setze_x(x, k, wert, fmt))}
        p[k] = wert
    p["ziel_dauer_s"] = max(unten, min(oben, p["ziel_dauer_s"]))
    p["publikum_gewichte"] = {k: max(-1., min(1., modell["gewichte"].get("inhalt:"+k, 0.)/skala*anteil))
                               for k, skala in features.INHALT_SKALEN.items()}
    p["autonom"] = {"version": alt["version"], "confidence": alt["confidence"],
                    "historischer_anteil": 1-anteil, "beitraege": beitraege, "exploration": exploration}
    return p, musik


def ueberblick(con) -> dict:
    c = champion(con)
    n = con.execute("SELECT COUNT(DISTINCT p.ziel) FROM posts p JOIN audience_ergebnisse a ON a.post_id=p.id").fetchone()[0]
    neu = con.execute("SELECT version FROM lernstaende ORDER BY version DESC LIMIT 1").fetchone()
    erkenntnisse = []
    if c:
        modell = c["modell"]
        if modell["gewichte"]:
            dauer = [(v, k) for k, v in modell["gewichte"].items() if k.startswith("dauer_")]
            if dauer:
                v, k = max(dauer)
                if v > .02:
                    z = int(k.split("_")[1])
                    erkenntnisse.append(f"Videos um {z} Sekunden zeigen derzeit bessere Publikumsreaktionen.")
            for k, positiv, negativ in (("seg_min_faktor", "Ruhigere Schnittfolgen", "Schnellere Schnittfolgen"),
                                        ("hook_staerkster", "Ein starker Einstieg", "Ein aufbauender Einstieg"),
                                        ("musik_pegel", "Mehr Musik", "Weniger Musik")):
                w = modell["gewichte"].get(k, 0.)
                if abs(w) > .02:
                    erkenntnisse.append((positiv if w > 0 else negativ)+" korrelieren mit besseren Ergebnissen.")
    return {"veroeffentlicht": con.execute("SELECT COUNT(DISTINCT ziel) FROM posts").fetchone()[0],
            "ausgewertet": n, "version": c["version"] if c else 0, "letzter_lernlauf": neu[0] if neu else 0,
            "confidence": c["confidence"] if c else 0., "erkenntnisse": erkenntnisse,
            "long_term_score": c["long_term_score"] if c else None,
            "recent_score": c["recent_score"] if c else None}
