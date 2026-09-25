"""Erwartung: Wie wahrscheinlich gibst du diesen Clip bzw. Entwurf frei? (Spec §10.5)

Beim Senden (Clip-Bot und Lern-Bot) wird eine Wahrscheinlichkeit berechnet und festgeschrieben (Tabelle
erwartungen, eine Zeile je Clip/Entwurf, nie überschrieben). Später zeigt der Vergleich mit deinem Urteil, ob das
Modell lernt: „Erwartung getroffen: Clips 14/20 (70 %)“.

Modell in Alltagssprache: Die logistische Funktion σ(x) = 1 / (1 + e^−x) macht aus jeder Zahl eine Wahrscheinlichkeit
zwischen 0 und 1 (0 → 50 %, groß → fast 100 %). x = a·z + b·rezept + c:
  - z sagt, wie stark der Moment-Score im Vergleich zu den letzten gesendeten ist (robust standardisiert wie der
    Publikums-Score, publikum.robust_z – eine Formel),
  - rezept ist in Stufe 2 immer 0 (der Rangwert des Rezepts kommt mit Stufe 3),
  - a, b, c werden bei jedem Aufruf aus allen bisherigen Urteilen neu geschätzt (Gradientenabstieg, deterministisch).
Unter [erwartung].mindest_urteile Urteilen einer Art gibt es keine Erwartung („noch keine“).

Lernidee: Das Modell ist ein Schieberegler mit drei Knöpfen. a sagt, wie sehr ein starker Moment für ein „Ja“
spricht, c ist die Grundneigung (gibst du eher frei oder eher nicht?), b wartet auf das Rezept. Beim Schätzen dreht
der Gradientenabstieg die Knöpfe Schritt für Schritt so, dass die Wahrscheinlichkeiten besser zu deinen bisherigen
Urteilen passen. Die L2-Strafe zieht a und b sanft zur Null, damit zehn Urteile kein extremes Modell ergeben.
Weil alles aus der Datenbank neu gerechnet wird (keine gespeicherten a, b, c), ergibt dieselbe Datenbank immer
dieselbe Erwartung.

Festschreiben heißt: Die Zahl von damals bleibt – auch wenn das Modell später klüger ist. Nur so ist die
Trefferquote ehrlich (die Erwartung stand fest, BEVOR du geurteilt hast).

Treffer: (wahrschein ≥ 0,5) == (Urteil positiv). Clip positiv = Status in db.BEWERTET, negativ = verworfen;
Entwurf positiv = daumen > 0 (Zusammenschnitte zählen mit, Annahme S2-A14).
Vergleichsbasis für z: die letzten [erwartung].referenz gesendeten derselben Art nach id (Clips mit tg_nachricht_id,
Entwürfe mit Status gesendet/bewertet) – es gibt keinen Sende-Zeitstempel (Annahme S2-A13).
„Letzte 20“ der Trefferquote zählen nach erwartungen.erstellt (Annahme S2-A15).

Alle Stellschrauben stehen in [erwartung] (config/pipeline.toml). Gewichte holt dieses Modul selbst einmal je
Aufruf per lernen.aktuelle – es ist der äußerste Aufrufer (die Bots rufen nur festschreiben/gespeichert).
Nichts hier weckt pve-big: nur Datenbank und die Schnittlisten der Entwürfe im Regie-Ordner.
"""

from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path

from . import db, lernen, merkmale, publikum, vorbewertung
from .konfig import Konfig
from .zeit import iso, jetzt

ARTEN = ("clip", "entwurf")

# Rangwert des Rezepts: in Stufe 2 für Clips und Entwürfe immer 0 (rezepte.py kommt mit Stufe 3, Spec §10.5).
REZEPT = 0.0
# Ab dieser Wahrscheinlichkeit gilt die Erwartung als „gibst frei“ (Spec §10.5: wahrschein ≥ 0,5).
SCHWELLE = 0.5
# „Letzte 20“ der Trefferquote: Spec §10.5 nennt die Zahl fest, [erwartung] hat dafür keinen Schlüssel.
LETZTE = 20

# Die Schlüssel in [erwartung], die das Modell braucht – fehlt einer, gibt es eine klare Meldung statt eines
# stillen Standardwerts (alles steht in pipeline.toml, nichts ist hier hart codiert).
_SCHLUESSEL = ("mindest_urteile", "referenz", "schritte", "lernrate", "l2", "start_a", "start_b", "start_c",
               "mad_minimum")


def _pruefe_art(art: str) -> None:
    """Nur clip und entwurf – so heißen die Arten auch in der Tabelle erwartungen (CHECK)."""
    if art not in ARTEN:
        raise ValueError(f"Unbekannte Art {art!r} – erlaubt sind {', '.join(ARTEN)}")


def _einstellungen(konfig: Konfig) -> dict[str, float]:
    """[erwartung] als Zahlen. ValueError mit dem Namen des Schlüssels, wenn einer fehlt."""
    werte = {}
    for name in _SCHLUESSEL:
        wert = konfig.wert(f"erwartung.{name}")
        if wert is None:
            raise ValueError(f"erwartung.{name} fehlt in der Konfiguration (config/pipeline.toml, [erwartung])")
        werte[name] = float(wert)
    return werte


def _sigma(x: float) -> float:
    """Logistische Funktion, für große negative x ohne Überlauf (e^−x würde dort riesig)."""
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


# --- Moment-Score ---------------------------------------------------------------------------------------------

def _clip_merkmale(con: sqlite3.Connection, clip_id: int) -> dict | None:
    """clips.merkmale als Dict (roh, fuer_moment filtert die Zahlen); None für einen unbekannten Clip."""
    zeile = db.clip(con, clip_id)
    return None if zeile is None else json.loads(zeile["merkmale"])


def _moment_zeile(con: sqlite3.Connection, schluessel: str) -> sqlite3.Row | None:
    return con.execute("SELECT clip_id, merkmale FROM momente WHERE schluessel = ?", (schluessel,)).fetchone()


def _schnittliste_momente(pfad: str) -> list[str] | None:
    """Die eindeutigen Momente einer Schnittliste in Reihenfolge; None, wenn die Datei fehlt oder kaputt ist."""
    try:
        liste = json.loads(Path(pfad).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    # dict.fromkeys: eindeutig und in der Reihenfolge des ersten Auftretens – ein Moment mit Jump-Cut-Teilen oder
    # als Hook wiederholt zählt einmal (wie entwurf_text die Momente zählt)
    return list(dict.fromkeys(s["moment"] for s in liste.get("segmente", []) if s.get("moment")))


def moment_score(con: sqlite3.Connection, konfig: Konfig, art: str, ziel_id: int,
                 gewichte: dict[str, float]) -> float | None:
    """Moment-Score eines Clips bzw. Mittel über die Momente eines Entwurfs (roh_score, aus momente neu gerechnet).

    None, wenn Clip/Entwurf unbekannt ist. Paket E.

    Clip: roh_score(fuer_moment(clips.merkmale, momente.merkmale von „clip:<id>“, kill_punkte), gewichte); fehlt die
    momente-Zeile, zählen nur clips.merkmale. Entwurf: Mittel über die eindeutigen Momente seiner Schnittliste, jeder
    aus seiner momente-Zeile neu gerechnet – ein Clip-Moment mit den Merkmalen seines Clips, ein Datei-Moment nur mit
    momente.merkmale (Annahme S2-A9). Die in alten Listen gespeicherte `intensitaet` wird bewusst NICHT benutzt: Sie
    stammt aus einer älteren Formel und würde alte und neue Entwürfe unvergleichbar machen.
    Parameter: con; konfig ([vorbewertung].kill_punkte); art – "clip" | "entwurf"; ziel_id – clips.id bzw.
    entwuerfe.id; gewichte – vom Aufrufer (lernen.aktuelle).
    Rückgabe: float (ungerundet) oder None – Clip/Entwurf unbekannt, Schnittliste fehlt/kaputt oder kein Moment der
    Liste hat eine momente-Zeile. Fehler: ValueError bei unbekannter Art.
    Beispiel: Entwurf mit clip:4 (Double 3 Punkte, Spielton-Spitzen 2 × 0,25 → 3,5) und datei:9 (max_gruppe 3 → 6)
    → (3,5 + 6) / 2 = 4,75.
    """
    _pruefe_art(art)
    kill_tabelle = [float(x) for x in konfig.wert("vorbewertung.kill_punkte", [])]
    if art == "clip":
        clip_mk = _clip_merkmale(con, ziel_id)
        if clip_mk is None:
            return None
        moment = _moment_zeile(con, f"clip:{ziel_id}")  # Schlüssel wie stimmung.py ihn für Clip-Momente anlegt
        moment_mk = json.loads(moment["merkmale"]) if moment is not None else None
        return vorbewertung.roh_score(merkmale.fuer_moment(clip_mk, moment_mk, kill_tabelle), gewichte)

    zeile = con.execute("SELECT schnittliste FROM entwuerfe WHERE id = ?", (ziel_id,)).fetchone()
    if zeile is None:
        return None
    schluessel = _schnittliste_momente(zeile["schnittliste"])
    if schluessel is None:
        return None
    scores = []
    for s in schluessel:
        moment = _moment_zeile(con, s)
        if moment is None:
            continue  # Moment unbekannt: zählt nicht, statt als 0 den Mittelwert zu drücken
        # Clip-Moment mit den Merkmalen seines Clips; fehlt der Clip, zählt er wie ein Datei-Moment
        clip_mk = _clip_merkmale(con, moment["clip_id"]) if moment["clip_id"] is not None else None
        scores.append(vorbewertung.roh_score(
            merkmale.fuer_moment(clip_mk, json.loads(moment["merkmale"]), kill_tabelle), gewichte))
    return sum(scores) / len(scores) if scores else None


# --- Urteile, Vergleichsbasis, Modell ---------------------------------------------------------------------------

def _urteile(con: sqlite3.Connection, art: str) -> list[tuple[int, int]]:
    """(ziel_id, 1 = positiv / 0 = negativ) aller geurteilten Objekte einer Art, nach id (deterministisch)."""
    if art == "clip":
        positiv = ", ".join("?" for _ in db.BEWERTET)
        zeilen = con.execute(
            f"""SELECT id, CASE WHEN status = 'verworfen' THEN 0 ELSE 1 END AS gut FROM clips
                WHERE status = 'verworfen' OR status IN ({positiv}) ORDER BY id""", db.BEWERTET).fetchall()
    else:
        zeilen = con.execute(
            "SELECT entwurf_id AS id, CASE WHEN daumen > 0 THEN 1 ELSE 0 END AS gut FROM entwurf_bewertungen"
            " ORDER BY entwurf_id").fetchall()
    return [(int(z["id"]), int(z["gut"])) for z in zeilen]


def _gesendete(con: sqlite3.Connection, art: str, referenz: int) -> list[int]:
    """Die letzten `referenz` gesendeten derselben Art, nach id absteigend (kein Sende-Zeitstempel, S2-A13)."""
    if art == "clip":
        sql = "SELECT id FROM clips WHERE tg_nachricht_id IS NOT NULL ORDER BY id DESC LIMIT ?"
    else:
        sql = "SELECT id FROM entwuerfe WHERE status IN ('gesendet', 'bewertet') ORDER BY id DESC LIMIT ?"
    return [int(z["id"]) for z in con.execute(sql, (referenz,)).fetchall()]


def _modell(con: sqlite3.Connection, konfig: Konfig, art: str, version: int,
            gewichte: dict[str, float]) -> tuple[dict, list[float]] | None:
    """Schätzt a, b, c mit gegebenen Gewichten; (Parameter wie modell(), Vergleichsbasis) oder None.

    Getrennt von modell(), damit festschreiben die Gewichte nur einmal holt und die neue Erwartung gegen DIESELBE
    Basis standardisiert wird wie die Trainingsdaten."""
    e = _einstellungen(konfig)
    urteile = _urteile(con, art)
    if len(urteile) < e["mindest_urteile"]:
        return None  # früh raus, bevor Schnittlisten gelesen werden
    basis = [s for i in _gesendete(con, art, int(e["referenz"]))
             if (s := moment_score(con, konfig, art, i, gewichte)) is not None]
    if not basis:
        return None  # ohne Vergleich kein z (robust_z bräuchte mindestens einen Wert)
    daten: list[tuple[float, float, int]] = []  # (z, rezept, Urteil)
    median = mad = 0.0
    for ziel_id, gut in urteile:
        score = moment_score(con, konfig, art, ziel_id, gewichte)
        if score is None:
            continue  # Objekt ohne Score (z. B. Schnittliste weg) kann nichts lehren
        # z zur Laufzeit neu, nicht aus erwartungen: sonst hätte das Modell vor der ersten Erwartung nie Daten
        z, median, mad = publikum.robust_z(score, basis, minimum=e["mad_minimum"])
        daten.append((z, REZEPT, gut))
    if len(daten) < e["mindest_urteile"]:
        return None

    # Gradientenabstieg auf dem mittleren Log-Verlust + l2/2·(a² + b²); c (Grundneigung) wird nicht bestraft.
    # Alle drei Knöpfe werden je Schritt gleichzeitig verschoben, feste Reihenfolge der Daten → deterministisch.
    a, b, c = e["start_a"], e["start_b"], e["start_c"]
    n = len(daten)
    for _ in range(int(e["schritte"])):
        ga = gb = gc = 0.0
        for z, rezept, gut in daten:
            fehler = _sigma(a * z + b * rezept + c) - gut  # > 0: zu optimistisch, < 0: zu pessimistisch
            ga += fehler * z
            gb += fehler * rezept
            gc += fehler
        a, b, c = (a - e["lernrate"] * (ga / n + e["l2"] * a),
                   b - e["lernrate"] * (gb / n + e["l2"] * b),
                   c - e["lernrate"] * (gc / n))
    return ({"a": a, "b": b, "c": c, "n_urteile": n, "median": median, "mad": mad, "gewichte_version": version},
            basis)


def modell(con: sqlite3.Connection, konfig: Konfig, art: str) -> dict | None:
    """Geschätzte Parameter {"a", "b", "c", "n_urteile", "median", "mad"}; None unter mindest_urteile. Paket E.

    Zusätzlich "gewichte_version" (mit welchen Gewichten die Moment-Scores gerechnet sind).
    Ablauf: Gewichte per lernen.aktuelle; Vergleichsbasis = Moment-Scores der letzten [erwartung].referenz gesendeten
    derselben Art; für jedes geurteilte Objekt z = publikum.robust_z(score, basis, minimum=[erwartung].mad_minimum);
    dann [erwartung].schritte Schritte Gradientenabstieg (lernrate, l2 auf a und b) ab start_a/start_b/start_c.
    n_urteile zählt die geurteilten Objekte mit Score; median/mad beschreiben die Vergleichsbasis (mad gemessen,
    vor dem Minimum).
    Rückgabe: dict oder None – weniger als mindest_urteile geurteilte Objekte mit Score, oder noch nichts gesendet.
    Fehler: ValueError bei unbekannter Art oder fehlendem Schlüssel in [erwartung].
    Beispiel: 10 Urteile, 3 freigegebene Triple (Score 6) und 7 verworfene Einzelkills (Score 1) → a > 0, c < 0:
    ein neues Triple bekommt über 50 %, ein neuer Einzelkill darunter.
    """
    _pruefe_art(art)
    version, gewichte = lernen.aktuelle(con, konfig)
    ergebnis = _modell(con, konfig, art, version, gewichte)
    return None if ergebnis is None else ergebnis[0]


# --- Festschreiben und Lesen ------------------------------------------------------------------------------------

def festschreiben(con: sqlite3.Connection, konfig: Konfig, art: str, ziel_id: int) -> float | None:
    """Erwartung beim Senden festschreiben (INSERT … ON CONFLICT (art, ziel_id) DO NOTHING).

    Gibt den GESPEICHERTEN Wert zurück (auch beim zweiten Aufruf den ersten); None unter mindest_urteile (dann keine
    Zeile). Paket E.

    Gibt es schon eine Zeile, wird nichts gerechnet (nur gelesen). Sonst: Gewichte einmal per lernen.aktuelle,
    Modell schätzen, Moment-Score des Objekts gegen dieselbe Basis standardisieren, p = σ(a·z + b·0 + c), Zeile mit
    grundlage = JSON {score, z, a, b, c, rezept, gewichte_version, n_urteile, median, mad} und erstellt = jetzt
    (ISO-UTC) anlegen. Aufrufer: Clip-Bot (sende_outbox) und Lern-Bot (_sende_entwuerfe), jeweils VOR send_video.
    Rückgabe: Wahrscheinlichkeit 0..1 oder None (zu wenige Urteile, noch nichts gesendet, Objekt ohne Score).
    Fehler: ValueError bei unbekannter Art/fehlender Konfig; sqlite3-Fehler gehen an den Aufrufer.
    Beispiel: a = 1,1, c = −0,2, z = 1,35 → σ(1,285) ≈ 0,78 → im Bot „Erwartung: ✅ 78 %“.
    """
    _pruefe_art(art)
    if (vorhanden := gespeichert(con, art, ziel_id)) is not None:
        return vorhanden
    version, gewichte = lernen.aktuelle(con, konfig)  # einmal holen, durchreichen (Leitplanke 7)
    ergebnis = _modell(con, konfig, art, version, gewichte)
    if ergebnis is None:
        return None
    m, basis = ergebnis
    score = moment_score(con, konfig, art, ziel_id, gewichte)
    if score is None:
        return None
    z, median, mad = publikum.robust_z(score, basis, minimum=_einstellungen(konfig)["mad_minimum"])
    wahrschein = _sigma(m["a"] * z + m["b"] * REZEPT + m["c"])
    grundlage = {"score": score, "z": z, "a": m["a"], "b": m["b"], "c": m["c"], "rezept": 0,
                 "gewichte_version": version, "n_urteile": m["n_urteile"], "median": median, "mad": mad}
    con.execute(
        """INSERT INTO erwartungen (art, ziel_id, wahrschein, grundlage, erstellt) VALUES (?, ?, ?, ?, ?)
           ON CONFLICT (art, ziel_id) DO NOTHING""",
        (art, ziel_id, wahrschein, json.dumps(grundlage, sort_keys=True), iso(jetzt())))
    return gespeichert(con, art, ziel_id)  # der gespeicherte Wert – war ein anderer schneller, gilt dessen Zahl


def gespeichert(con: sqlite3.Connection, art: str, ziel_id: int) -> float | None:
    """Nur lesen: festgeschriebene Wahrscheinlichkeit oder None. Paket E.

    Für /offen und die Bildunterschrift nach einem Klick – dort wird nie gerechnet.
    Fehler: ValueError bei unbekannter Art. Beispiel: gespeichert(con, "clip", 17) → 0.78."""
    _pruefe_art(art)
    zeile = con.execute("SELECT wahrschein FROM erwartungen WHERE art = ? AND ziel_id = ?", (art, ziel_id)).fetchone()
    return None if zeile is None else float(zeile["wahrschein"])


# --- Trefferquote --------------------------------------------------------------------------------------------------

def trefferquote(con: sqlite3.Connection, art: str, letzte: int | None = None) -> tuple[int, int]:
    """(Treffer, geurteilte Erwartungen) einer Art, optional nur die letzten n nach erwartungen.erstellt. Paket E.

    Gezählt werden nur Erwartungen, deren Objekt schon ein Urteil hat (Clip: BEWERTET oder verworfen; Entwurf: eine
    Zeile in entwurf_bewertungen). Treffer = (wahrschein ≥ 0,5) == positiv. „Letzte“ nach erstellt absteigend, bei
    gleicher Zeit nach ziel_id (Annahme S2-A15). Rechnet nichts neu – nur festgeschriebene Zahlen zählen.
    Fehler: ValueError bei unbekannter Art oder letzte < 1.
    Beispiel: 45 geurteilte Clip-Erwartungen, davon in den letzten 20 14 Treffer → trefferquote(con, "clip", 20)
    = (14, 20).
    """
    _pruefe_art(art)
    if letzte is not None and letzte < 1:
        raise ValueError(f"letzte muss mindestens 1 sein, nicht {letzte}")
    if art == "clip":
        positiv = ", ".join("?" for _ in db.BEWERTET)
        sql = f"""SELECT e.wahrschein, CASE WHEN c.status = 'verworfen' THEN 0 ELSE 1 END AS gut
                  FROM erwartungen e JOIN clips c ON c.id = e.ziel_id
                  WHERE e.art = 'clip' AND (c.status = 'verworfen' OR c.status IN ({positiv}))"""
        parameter: list = list(db.BEWERTET)
    else:
        sql = """SELECT e.wahrschein, CASE WHEN b.daumen > 0 THEN 1 ELSE 0 END AS gut
                 FROM erwartungen e JOIN entwurf_bewertungen b ON b.entwurf_id = e.ziel_id
                 WHERE e.art = 'entwurf'"""
        parameter = []
    sql += " ORDER BY e.erstellt DESC, e.ziel_id DESC"
    if letzte is not None:
        sql += " LIMIT ?"
        parameter.append(letzte)
    zeilen = con.execute(sql, parameter).fetchall()
    treffer = sum(1 for z in zeilen if (z["wahrschein"] >= SCHWELLE) == bool(z["gut"]))
    return treffer, len(zeilen)


def _quote(treffer: int, n: int) -> str:
    """„14/20 (70 %)“ – ganze Prozent reichen, mehr Genauigkeit täuscht bei 20 Urteilen nur vor."""
    return f"{treffer}/{n} ({round(100 * treffer / n)} %)"


def trefferquote_text(con: sqlite3.Connection, konfig: Konfig) -> str:
    """Zeile(n) für /gewichte und /lernstand, z. B. „Erwartung getroffen: Clips 14/20 (70 %) · alle 30/45 …“.

    Leerer Text = nichts anzuzeigen.

    Je Art mit mindestens einer geurteilten Erwartung eine Zeile: erst die letzten 20 (nach erstellt), dahinter
    „· alle …“ – nur wenn es mehr als 20 sind (sonst stünde zweimal dieselbe Zahl da). Klartext ohne HTML; die
    Aufrufer maskieren selbst. konfig wird noch nicht gebraucht (Signatur aus dem Vertrag). Fehler: keine eigenen.
    Beispiel: „Erwartung getroffen: Clips 14/20 (70 %) · alle 34/45 (76 %)“ und darunter
    „Erwartung getroffen: Entwürfe 3/4 (75 %)“.
    """
    zeilen = []
    for art, name in (("clip", "Clips"), ("entwurf", "Entwürfe")):
        alle = trefferquote(con, art)
        if alle[1] == 0:
            continue
        zuletzt = trefferquote(con, art, letzte=LETZTE)
        zeile = f"Erwartung getroffen: {name} {_quote(*zuletzt)}"
        if alle[1] > zuletzt[1]:
            zeile += f" · alle {_quote(*alle)}"
        zeilen.append(zeile)
    return "\n".join(zeilen)
