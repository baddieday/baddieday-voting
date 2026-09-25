"""Die Vorbewertung lernt aus deinen Entscheidungen und aus dem Publikum – einfach und nachvollziehbar.

Idee: Findest du Clip A besser als Clip B, die Formel sieht aber B vorne, werden
die Gewichte ein kleines Stück in Richtung der Merkmale verschoben, in denen A
besser war ("paarweises Nachjustieren", ein Perzeptron auf Paaren).

Paare entstehen aus drei Quellen (Spec §8.3), jede mit eigenem Gewicht:
  - Battles: Gewinner > Verlierer                                            (Gewicht 1,0 – direkter Vergleich)
  - Freigaben: jeder freigegebene Clip > jeder verworfene desselben Abends   ([lernen].gewicht_freigabe, 0,5 –
    nur ein indirekter Vergleich: beide Clips standen nie nebeneinander)
  - Publikum: zwei bewertete Posts derselben Plattform und Art, deren Scores weit genug auseinander liegen
                                                                             (Gewicht 1,0 – echte Zuschauer)

Lernidee in Alltagssprache: Jedes Paar ist eine kleine Prüfungsfrage „welcher von beiden ist besser?“. Die
Gewichte werden so lange ein bisschen verschoben, bis die Formel möglichst viele Fragen richtig beantwortet.
Wie oft sie das tut, heißt Sortier-Quote – getrennt für dich (Battles + Freigaben) und für das Publikum. So sieht
man, wenn du etwas anderes magst als deine Zuschauer.

„Fehlt = unbekannt“ (Annahme S2-A17): Die zwölf neuen Merkmale (merkmale.REPLAY_MERKMALE und MIC_MERKMALE) werden in
einem Paar nur verglichen, wenn sie auf BEIDEN Seiten gemessen sind. Sonst lernten die Gewichte „analysiert gegen
nicht analysiert“ (z. B. ein Clip mit Mic-Analyse gegen einen ohne) statt „lustig gegen nicht lustig“. Die alten
fünf Merkmale bleiben wie vor Stufe 2: fehlt = 0. Deshalb werden die Paar-Dicts nie mit 0 aufgefüllt.

Sicherungen: Mindestmenge, langsam wachsendes Vertrauen, Leine um die Startgewichte
und ein Vergleich mit den Startgewichten (nie schlechter werden) – für deine Quote immer, für die
Publikums-Quote erst ab [lernen].mindest_publikum_paare Paaren (Annahme S2-A10).
Alles wird jedes Mal komplett neu aus der Historie berechnet -> reproduzierbar.

Import-Regel (Plan Stufe 2, Leitplanke 7; tests/test_vertrag_stufe2.py prüft sie):
    lernen → db, vorbewertung, zeit, merkmale (ab Paket D)      nie: mikro, stimmung, verarbeitung
    Paket D nimmt zusätzlich publikum (nur VERMERK_BASIS_ZU_KLEIN und einstellung – eine Wahrheit für beides;
    publikum importiert lernen nicht, also kein Kreis).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field, replace
from itertools import combinations, islice, product

from . import merkmale as merkmal_modul
from . import publikum
from .db import BEWERTET, clip, merkmale, ohne_mic_analyse
from .vorbewertung import MERKMAL_NAMEN, MERKMALE, roh_score
from .zeit import aus_iso, iso, jetzt, spielabend

# Ein Paar gilt erst als „sicher richtig“ sortiert, wenn der bessere Clip mindestens 1 Punkt vorne liegt – so viel
# wie ein Einzelkill (kill_punkte 1 × Startgewicht 1). Knapp richtig sortierte Paare schieben die Gewichte weiter
# (Perzeptron mit Marge); ohne Marge bliebe jedes Paar mit Abstand 0,001 für immer auf der Kippe.
MARGE = 1.0
# Unterschiede unter einem Milliardstel sind Rundungsrauschen der Gleitkomma-Rechnung, kein echter Vorsprung
GLEICHSTAND = 1e-9
# Die zwölf neuen Merkmale: fehlt eins auf einer Seite eines Paars, wird es nicht verglichen (Annahme S2-A17)
NEUE_MERKMALE = frozenset(merkmal_modul.REPLAY_MERKMALE + merkmal_modul.MIC_MERKMALE)
QUELLEN = ("battle", "freigabe", "publikum")

log = logging.getLogger("pipeline")


@dataclass
class Paar:
    besser: dict[str, float]      # NICHT mit 0 auffüllen: fehlt = unbekannt (Annahme S2-A17)
    schlechter: dict[str, float]
    art: str  # battle | freigabe | publikum  (= Quelle, Spec §8.3)
    gewicht: float = 1.0          # battle 1,0 · freigabe [lernen].gewicht_freigabe · publikum 1,0 (Paket D)


@dataclass
class Ergebnis:
    werte: dict[str, float]
    start: dict[str, float]
    datenbasis: int  # Anzahl Bewertungen: Freigaben/Verwerfungen + Battles + Posts in Publikums-Paaren
    freigaben: int
    battles: int
    vertrauen: float
    trefferquote: float | None
    trefferquote_start: float | None
    aktiv: bool
    grund: str
    # Stufe 2 (Paket D) – Standardwerte, damit bisherige Aufrufer unverändert laufen. trefferquote oben = deine Quote.
    trefferquote_publikum: float | None = None          # ab dem 1. Publikums-Paar; None nur bei 0 Paaren
    trefferquote_publikum_start: float | None = None
    paare_je_quelle: dict[str, int] = field(default_factory=dict)   # {"battle": n, "freigabe": n, "publikum": n}
    ohne_mic: int = 0                                   # Clips ohne Mic-Analyse (mic_stand NULL, nicht verworfen)
    auseinander: str | None = None                      # „Du magst X, das Publikum Y“ (ab 10 Publikums-Paaren)
    # Paket D (Befund: nicht im Vertrag): ab so vielen Publikums-Paaren prüft die Schranke die Publikums-Quote –
    # /gewichte braucht die Zahl für „zählt für die Schranke erst ab 10“. Standard wie config/pipeline.toml.
    mindest_publikum_paare: int = 10


def startgewichte(konfig) -> dict[str, float]:
    """Startgewichte aus [vorbewertung.startgewichte] für alle MERKMALE; fehlt eins in der Konfig, zählt es 0.
    Beispiel: mit config/pipeline.toml ist startgewichte(konfig)["bot_opfer"] == −2.0."""
    return {m: float(konfig.wert(f"vorbewertung.startgewichte.{m}", 0.0)) for m in MERKMALE}


def score(gewichte: dict[str, float], merkmal_werte: dict[str, float]) -> float:
    """Score eines Clips: vorbewertung.roh_score (die eine Formel), hier nur mit vertauschter Reihenfolge der
    Parameter wie vor Stufe 2. Beispiel: score({"kill_punkte": 1, "bot_opfer": −2}, {"kill_punkte": 3,
    "bot_opfer": 0.5}) == 2.0."""
    return roh_score(merkmal_werte, gewichte)


def differenz(paar: Paar) -> dict[str, float]:
    """Merkmals-Unterschied besser − schlechter, nur über die Merkmale, die verglichen werden dürfen.

    Alte fünf Merkmale: fehlt = 0 (wie vor Stufe 2). Neue zwölf (NEUE_MERKMALE): fehlt der Schlüssel auf einer
    Seite, steht das Merkmal NICHT im Ergebnis (unbekannt wird nicht verglichen, Annahme S2-A17).
    Beispiel: besser {"kill_punkte": 3, "mic_lachen": 2}, schlechter {"kill_punkte": 1} → {"kill_punkte": 2.0,
    "victory_royale": 0.0, "laenge": 0.0, "lautstaerke": 0.0, "kommentar": 0.0} – mic_lachen fehlt.
    Rückgabe: neues Dict. Fehler: ein Wert, der keine Zahl ist → ValueError/TypeError (float())."""
    d: dict[str, float] = {}
    for m in MERKMALE:
        if m in NEUE_MERKMALE and (m not in paar.besser or m not in paar.schlechter):
            continue  # auf einer Seite nicht gemessen → kein Vergleich
        d[m] = float(paar.besser.get(m, 0.0)) - float(paar.schlechter.get(m, 0.0))
    return d


def trefferquote(gewichte: dict[str, float], paare: list[Paar]) -> float | None:
    """Gewichteter Anteil der Paare, die die Gewichte richtig herum sortieren (Gleichstand zählt halb).

    Verglichen wird der Vorsprung Σ Gewicht·Unterschied (roh_score über differenz) – so zählt ein unbekanntes neues
    Merkmal nicht mit. Jedes Paar zählt mit seinem Paar-Gewicht (Freigabe halb).
    Beispiel: ein richtiges Battle (1,0), eine falsche Freigabe (0,5), ein Gleichstand (1,0)
    → (1 + 0 + 0,5) / 2,5 = 0,6. Rückgabe auf 4 Stellen; None ohne Paare oder wenn alle Paar-Gewichte 0 sind.
    Fehler: keine eigenen."""
    summe_gewichte = sum(p.gewicht for p in paare)
    if not paare or summe_gewichte <= 0:
        return None
    punkte = 0.0
    for p in paare:
        vorsprung = roh_score(differenz(p), gewichte)
        punkte += p.gewicht * (1.0 if vorsprung > GLEICHSTAND else 0.5 if abs(vorsprung) <= GLEICHSTAND else 0.0)
    return round(punkte / summe_gewichte, 4)


def trainiere(paare: list[Paar], start: dict[str, float], einstellungen: dict) -> dict[str, float]:
    """Paarweises Nachjustieren (Perzeptron mit Marge) in fester Reihenfolge der Paare.

    Liegt der bessere Clip nicht mindestens MARGE vorne, wandert jedes verglichene Merkmal um
    schritt · Paar-Gewicht · Unterschied – danach zurück an die Leine (Start ± max(leine_minimum,
    |Start| · leine_anteil)). Unbekannte neue Merkmale (nicht in differenz) bleiben unberührt.
    Parameter: einstellungen – [lernen] mit schritt, durchlaeufe, leine_anteil, leine_minimum.
    Beispiel: Start lautstaerke 0, schritt 0,1, ein Freigabe-Paar (Gewicht 0,5) mit Unterschied 1 → 0,05.
    Rückgabe: Gewichte aller MERKMALE auf 4 Stellen. Fehler: fehlender Schlüssel in einstellungen → KeyError."""
    schritt = float(einstellungen["schritt"])
    anteil = float(einstellungen["leine_anteil"])
    minimum = float(einstellungen["leine_minimum"])
    leine = {m: max(minimum, abs(start.get(m, 0.0)) * anteil) for m in MERKMALE}
    w = {m: float(start.get(m, 0.0)) for m in MERKMALE}
    for _ in range(int(einstellungen["durchlaeufe"])):
        for p in paare:
            d = differenz(p)
            if roh_score(d, w) < MARGE:  # falsch herum oder zu knapp
                for m, unterschied in d.items():
                    w[m] += schritt * p.gewicht * unterschied
                    w[m] = min(start.get(m, 0.0) + leine[m], max(start.get(m, 0.0) - leine[m], w[m]))
    return {m: round(v, 4) for m, v in w.items()}


def sammle_paare(con: sqlite3.Connection, *, zonen_name: str, wechsel_stunde: int, max_pro_abend: int) -> tuple[list[Paar], int, int]:
    """Gibt (Paare, Anzahl entschiedener Clips, Anzahl entschiedener Battles) zurück – erst alle Battles, dann die
    Freigaben. Alle Paare tragen hier Gewicht 1,0; das Freigabe-Gewicht setzt berechne aus der Konfig."""
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


def _nur_zahlen(werte: dict) -> dict:
    """Nur die Zahlen eines eingefrorenen Merkmals-Dicts (Listen, Texte, None und bool fallen weg)."""
    return {k: v for k, v in werte.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}


def _hook_merkmale(con: sqlite3.Connection, post: sqlite3.Row, kill_tabelle: list[float]) -> tuple[str, dict] | None:
    """(Moment-Schlüssel, Merkmale) des Moments, für den ein Post steht – None, wenn es keinen gibt.

    Clip-Post: der Clip selbst. Entwurf-Post: der Hook-Moment (posts.merkmale.hook_moment, der erste Moment im
    Short). Die Merkmale kommen, wenn möglich, AKTUELL über merkmale.fuer_moment (nachgetragene Replay- und
    Mic-Werte zählen): Clip vorhanden → clips.merkmale + momente; Datei-Moment (kein Clip) mit momente-Zeile →
    fuer_moment(None, …). Rückfall: die beim Posten eingefrorenen Zahlen aus posts.merkmale (nur Zahlen).
    Beispiel: Entwurf mit hook_moment "clip:7", Clip 7 hat merkmale {"kill_punkte": 3} → ("clip:7",
    {"kill_punkte": 3.0, …Mic aus momente, falls da}). Fehler: kaputtes JSON → json.JSONDecodeError."""
    daten = json.loads(post["merkmale"])
    hook = daten.get("hook_moment") or (post["ziel"] if post["art"] == "clip" else None)
    if not hook:
        return None
    eingefroren = next((m for m in daten.get("momente") or [] if m.get("moment") == hook), None)
    clip_id = post["clip_id"] if post["art"] == "clip" else (eingefroren or {}).get("clip_id")
    clip_zeile = clip(con, int(clip_id)) if clip_id is not None else None
    moment_zeile = con.execute("SELECT merkmale FROM momente WHERE schluessel = ?", (hook,)).fetchone()
    moment_merkmale = json.loads(moment_zeile["merkmale"]) if moment_zeile else None
    if clip_zeile is not None:
        return hook, merkmal_modul.fuer_moment(json.loads(clip_zeile["merkmale"]), moment_merkmale, kill_tabelle)
    if clip_id is None and moment_merkmale is not None:
        return hook, merkmal_modul.fuer_moment(None, moment_merkmale, kill_tabelle)
    if eingefroren is None:
        return None  # nichts über diesen Moment bekannt – ein leeres Dict wäre ein erfundener Vergleich
    return hook, {k: float(v) for k, v in _nur_zahlen(eingefroren.get("merkmale") or {}).items()}


def _basis_zu_klein(post: sqlite3.Row) -> bool:
    """Score 0 wegen zu kleiner Vergleichsbasis ist keine Messung (Spec §6.3) – solche Posts bilden keine Paare.
    Fehler: kaputtes score_teile-JSON → json.JSONDecodeError (der Aufrufer überspringt den Post)."""
    teile = json.loads(post["score_teile"] or "{}")
    return publikum.VERMERK_BASIS_ZU_KLEIN in (teile.get("vermerke") or [])


def _post_moment(con: sqlite3.Connection, post: sqlite3.Row, kill_tabelle: list[float]) -> tuple[str, dict] | None:
    """(Moment, Merkmale) eines bewerteten Posts, der ein Paar bilden darf – sonst None.

    Fachliche Prüfung „ist eine Messung“: ohne „Basis zu klein“. Kaputte Daten eines einzelnen Posts (JSON in
    score_teile oder merkmale, Werte, die keine Zahl sind) halten das Lernen nicht auf: Der Post wird mit Nummer
    geloggt und übersprungen – genauso behandelt publikum.bewerte_alle kaputte Posts."""
    try:
        if _basis_zu_klein(post):
            return None
        return _hook_merkmale(con, post, kill_tabelle)
    except (ValueError, KeyError, TypeError, AttributeError) as fehler:  # JSONDecodeError ist ein ValueError
        log.warning("Lernen: Post #%s übersprungen (%s: %s)", post["id"], type(fehler).__name__, fehler)
        return None


def _publikum_paare_mit_posts(con: sqlite3.Connection, konfig) -> tuple[list[Paar], set[int]]:
    """publikum_paare plus die ids der Posts, die in den gewählten Paaren stecken (für die Datenbasis)."""
    abstand = float(publikum.einstellung(konfig, "paar_abstand"))
    maximal = int(publikum.einstellung(konfig, "max_paare"))
    kill_tabelle = list(konfig.abschnitt("vorbewertung")["kill_punkte"])
    zeilen = con.execute(
        """SELECT id, art, ziel, clip_id, plattform, gepostet_utc, score, score_teile, merkmale FROM posts
            WHERE bewertet_utc IS NOT NULL AND score IS NOT NULL ORDER BY id"""
    ).fetchall()
    gruppen: dict[tuple[str, str], list[tuple[sqlite3.Row, str, dict]]] = {}
    for zeile in zeilen:
        moment = _post_moment(con, zeile, kill_tabelle)
        if moment is not None:
            gruppen.setdefault((zeile["plattform"], zeile["art"]), []).append((zeile, *moment))

    kandidaten = []
    for posts in gruppen.values():
        for (a, moment_a, mk_a), (b, moment_b, mk_b) in combinations(posts, 2):
            if moment_a == moment_b:
                continue  # derselbe Moment (z. B. zwei Entwürfe mit gleichem Hook): kein Vergleich zweier Momente
            # GLEICHSTAND: Scores sind auf 4 Stellen gerundet; 0,7 − 0,2 ist als Gleitkommazahl 0,4999…
            if abs(a["score"] - b["score"]) + GLEICHSTAND < abstand:
                continue
            besser, schlechter = ((a, mk_a), (b, mk_b)) if a["score"] > b["score"] else ((b, mk_b), (a, mk_a))
            # Sortierschlüssel „jünger zuerst“: der jüngere Post des Paars, dann die höhere und die niedrigere id
            juenger = max(a["gepostet_utc"], b["gepostet_utc"])
            schluessel = (juenger, max(a["id"], b["id"]), min(a["id"], b["id"]))
            kandidaten.append((schluessel, besser, schlechter))
    kandidaten.sort(key=lambda k: k[0], reverse=True)
    gewaehlt = kandidaten[:maximal]
    paare = [Paar(dict(besser[1]), dict(schlechter[1]), "publikum", 1.0) for _, besser, schlechter in gewaehlt]
    post_ids = {z["id"] for _, besser, schlechter in gewaehlt for z in (besser[0], schlechter[0])}
    return paare, post_ids


def publikum_paare(con: sqlite3.Connection, konfig) -> list[Paar]:
    """Paare aus dem Publikum (Spec §8.3): je zwei bewertete Posts derselben Plattform und Art mit
    |score_A − score_B| ≥ [publikum].paar_abstand, ohne „Basis zu klein“, nicht derselbe Moment; Clip → Clip-Merkmale,
    Entwurf → Hook-Moment. Höchstens [publikum].max_paare jüngste. Paket D.

    Bewertet = bewertet_utc und score gesetzt. Merkmale je Post: aktuell über merkmale.fuer_moment (Clip:
    clips.merkmale + Mic aus momente; Entwurf: sein Hook-Moment), Rückfall die eingefrorenen Zahlen aus
    posts.merkmale (siehe _hook_merkmale). Posts ohne erkennbaren Moment fallen weg.
    Reihenfolge: jüngste zuerst nach dem jüngeren Post des Paars (gepostet_utc), bei Gleichstand nach der höheren,
    dann der niedrigeren Post-id – deterministisch. Jedes Paar: art "publikum", Gewicht 1,0.
    Beispiel: tiktok-Clip-Posts mit Scores 1,2 / 0,4 / 1,0 → ein Paar (1,2 > 0,4) und eins (1,0 > 0,4);
    1,2 ↔ 1,0 liegt unter 0,5 Abstand.
    Fehler: fehlt [publikum].paar_abstand oder max_paare → KonfigFehler (publikum.einstellung); kaputtes JSON in
    posts → json.JSONDecodeError."""
    return _publikum_paare_mit_posts(con, konfig)[0]


def _mittlere_differenz(paare: list[Paar]) -> dict[str, float]:
    """Je Merkmal der mittlere Unterschied besser − schlechter (mit Paar-Gewicht), nur über Paare, in denen das
    Merkmal verglichen wird. Beispiel: zwei Battles mit lautstaerke +0,8 und +0,4 → {"lautstaerke": 0.6, …}."""
    summen: dict[str, float] = {}
    gewichte: dict[str, float] = {}
    for p in paare:
        for m, unterschied in differenz(p).items():
            summen[m] = summen.get(m, 0.0) + p.gewicht * unterschied
            gewichte[m] = gewichte.get(m, 0.0) + p.gewicht
    return {m: summen[m] / gewichte[m] for m in summen if gewichte[m] > 0}


def auseinander_satz(nutzer: list[Paar], publikums_paare: list[Paar], mindest: int) -> str | None:
    """„Du magst X, das Publikum Y“ (Spec §8.3, Annahme S2-A11) – None, wenn ihr euch einig seid.

    Je Merkmal zählt das Vorzeichen des mittleren Unterschieds (besser − schlechter) je Quelle: positiv = diese
    Quelle mag mehr davon. X = das Merkmal mit dem größten positiven Mittel bei dir, das beim Publikum ≤ 0 ist; Y
    umgekehrt. Nur Merkmale, die in beiden Quellen verglichen wurden. Erst ab `mindest` Publikums-Paaren (vorher
    ist das Publikum zu dünn, um dir zu widersprechen) und nur, wenn es Paare von dir gibt.
    Beispiel: du +0,8 bei Lautstärke (Publikum −1,8), Publikum +2 bei Kill-Punkten (du 0)
    → "Du magst Lautstärke, das Publikum Kill-Punkte.". Fehler: keine."""
    if len(publikums_paare) < mindest or not nutzer:
        return None
    du, pub = _mittlere_differenz(nutzer), _mittlere_differenz(publikums_paare)
    beide = [m for m in MERKMALE if m in du and m in pub]
    nur_du = [m for m in beide if du[m] > GLEICHSTAND and pub[m] <= GLEICHSTAND]
    nur_pub = [m for m in beide if pub[m] > GLEICHSTAND and du[m] <= GLEICHSTAND]
    # max() nimmt bei Gleichstand das erste in MERKMALE-Reihenfolge → deterministisch
    x = MERKMAL_NAMEN[max(nur_du, key=lambda m: du[m])] if nur_du else None
    y = MERKMAL_NAMEN[max(nur_pub, key=lambda m: pub[m])] if nur_pub else None
    if x and y:
        return f"Du magst {x}, das Publikum {y}."
    if x:
        return f"Du magst {x}, das Publikum nicht."
    if y:
        return f"Das Publikum mag {y}, du nicht."
    return None


def berechne(con: sqlite3.Connection, konfig) -> Ergebnis:
    """Gewichte komplett neu aus der Historie: Battles, Freigaben, Publikum (in dieser Reihenfolge trainiert).

    Datenbasis n = Freigaben/Verwerfungen + Battles + Posts in Publikums-Paaren; unter [lernen].mindestens bleiben
    die Startgewichte. Zwei Quoten: trefferquote (du: Battles + Freigaben) und trefferquote_publikum (ab dem ersten
    Publikums-Paar, sonst None), je mit dem Wert der Startgewichte. Schranke „nie schlechter als der Start“: deine
    Quote immer, die Publikums-Quote erst ab [lernen].mindest_publikum_paare Paaren (Annahme S2-A10). Greift sie,
    gelten die Startgewichte, und die Quoten zeigen deren Werte.
    Beispiel: 40 Freigaben, 3 Posts in 3 Publikums-Paaren → datenbasis 43, paare_je_quelle {"battle": 0,
    "freigabe": 80, "publikum": 3}; eine schlechtere Publikums-Quote hält das Lernen hier noch nicht auf.
    Fehler: fehlende [lernen]-/[publikum]-Schlüssel → KeyError bzw. KonfigFehler; sqlite3-Fehler gehen durch."""
    einstellungen = konfig.abschnitt("lernen")
    start = startgewichte(konfig)
    nutzer, n_freigaben, n_battles = sammle_paare(
        con,
        zonen_name=konfig.wert("zeit.zeitzone", "Europe/Berlin"),
        wechsel_stunde=int(konfig.wert("zeit.tageswechsel_stunde", 6)),
        max_pro_abend=int(einstellungen["max_paare_pro_abend"]),
    )
    gewicht_freigabe = float(einstellungen["gewicht_freigabe"])
    nutzer = [replace(p, gewicht=gewicht_freigabe) if p.art == "freigabe" else p for p in nutzer]
    pub, post_ids = _publikum_paare_mit_posts(con, konfig)
    mindest_publikum = int(einstellungen["mindest_publikum_paare"])

    n = n_freigaben + n_battles + len(post_ids)
    vertrauen = min(1.0, n / max(1, int(einstellungen["voll_vertrauen"])))
    tq_start, tqp_start = trefferquote(start, nutzer), trefferquote(start, pub)
    je_quelle = {q: sum(1 for p in nutzer + pub if p.art == q) for q in QUELLEN}
    ohne_mic = ohne_mic_analyse(con)  # dieselbe Zählung wie „offen“ in mikro.clips_nachziehen

    def ergebnis(werte, aktiv, grund, tq, tqp):
        return Ergebnis(werte, start, n, n_freigaben, n_battles, round(vertrauen, 3), tq, tq_start, aktiv, grund,
                        trefferquote_publikum=tqp, trefferquote_publikum_start=tqp_start, paare_je_quelle=je_quelle,
                        ohne_mic=ohne_mic, auseinander=auseinander_satz(nutzer, pub, mindest_publikum),
                        mindest_publikum_paare=mindest_publikum)

    if n < int(einstellungen["mindestens"]):
        return ergebnis(dict(start), False, f"noch {int(einstellungen['mindestens']) - n} Bewertungen bis zum Lernen",
                        tq_start, tqp_start)
    gelernt = trainiere(nutzer + pub, start, einstellungen)
    werte = {m: round(start[m] + vertrauen * (gelernt[m] - start[m]), 4) for m in MERKMALE}
    tq, tqp = trefferquote(werte, nutzer), trefferquote(werte, pub)
    # Schranke 1: dein Urteil darf nie schlechter vorhergesagt werden als mit den Startgewichten
    if tq is not None and tq_start is not None and tq < tq_start:
        return ergebnis(dict(start), False, "gelernte Gewichte sortieren dein Urteil schlechter als die Startgewichte",
                        tq_start, tqp_start)
    # Schranke 2: das Publikum ebenso – aber erst ab genug Paaren (vorher ist die Quote Zufall, S2-A10)
    if len(pub) >= mindest_publikum and tqp is not None and tqp_start is not None and tqp < tqp_start:
        return ergebnis(dict(start), False, "gelernte Gewichte sortieren das Publikum schlechter als die Startgewichte",
                        tq_start, tqp_start)
    return ergebnis(werte, True, "aktiv", tq, tqp)


def anzeige_zeilen(e: Ergebnis) -> list[str]:
    """Die Zeilen unter der Gewichts-Tabelle – eine Stelle für /gewichte im Bot und `pipeline gewichte` (Klartext,
    der Bot maskiert HTML selbst). Beispiel (12 Publikums-Paare):
      Sortier-Quote du: 80 % (Start 70 %)
      Sortier-Quote Publikum: 64 % (Start 50 %) – 12 Paare
      Paare: 2 Battles · 80 Freigaben · 12 Publikum
      ohne Mic-Analyse: 3 Clips
      Du magst Lautstärke, das Publikum Kill-Punkte.
    Unter mindest_publikum_paare hängt „, zählt für die Schranke erst ab 10“ an; ohne Paare „noch keine Paare“."""
    def prozent(x: float | None) -> int:
        return round((x or 0.0) * 100)

    zeilen = []
    if e.trefferquote is not None:
        zeilen.append(f"Sortier-Quote du: {prozent(e.trefferquote)} % (Start {prozent(e.trefferquote_start)} %)")
    else:
        zeilen.append("Sortier-Quote du: noch keine Paare")
    n_pub = e.paare_je_quelle.get("publikum", 0)
    if e.trefferquote_publikum is not None:
        zeile = (f"Sortier-Quote Publikum: {prozent(e.trefferquote_publikum)} % "
                 f"(Start {prozent(e.trefferquote_publikum_start)} %) – {n_pub} {'Paar' if n_pub == 1 else 'Paare'}")
        if n_pub < e.mindest_publikum_paare:
            zeile += f", zählt für die Schranke erst ab {e.mindest_publikum_paare}"
        zeilen.append(zeile)
    else:
        zeilen.append("Publikum: noch keine Paare")
    zeilen.append(f"Paare: {e.paare_je_quelle.get('battle', 0)} Battles · {e.paare_je_quelle.get('freigabe', 0)} "
                  f"Freigaben · {n_pub} Publikum")
    zeilen.append(f"ohne Mic-Analyse: {e.ohne_mic} {'Clip' if e.ohne_mic == 1 else 'Clips'}")
    if e.auseinander:
        zeilen.append(e.auseinander)
    return zeilen


def datenbasis_text(e: Ergebnis) -> str:
    """„Datenbasis: 52 Bewertungen (40 Freigaben/Verwerfungen, 2 Battles, 10 Posts)“ – Posts = die in
    Publikums-Paaren (datenbasis − freigaben − battles)."""
    posts = e.datenbasis - e.freigaben - e.battles
    return (f"Datenbasis: {e.datenbasis} Bewertungen ({e.freigaben} Freigaben/Verwerfungen, {e.battles} Battles, "
            f"{posts} Posts)")


def aktuelle(con: sqlite3.Connection, konfig) -> tuple[int, dict[str, float]]:
    """Neueste gespeicherte Gewichte, sonst die Startgewichte (Version 0).

    Merkmale, die in der gespeicherten Version fehlen (alte Datenbank vor Stufe 2), bekommen ihr Startgewicht –
    sonst zählten neue Merkmale 0, bis `aktualisiere` wieder speichert. Beispiel: Version 4 speichert nur die alten
    fünf → aktuelle liefert (4, {…, "bot_opfer": −2.0, …}). Fehler: kaputtes JSON → json.JSONDecodeError."""
    zeile = con.execute("SELECT version, werte FROM gewichte ORDER BY version DESC LIMIT 1").fetchone()
    start = startgewichte(konfig)
    if zeile:
        return int(zeile["version"]), start | {k: float(v) for k, v in json.loads(zeile["werte"]).items()}
    return 0, start


def aktualisiere(con: sqlite3.Connection, konfig) -> tuple[int, Ergebnis]:
    """Berechnet neu und speichert eine neue Version, falls sich die Gewichte geändert haben – mit beiden Quoten
    und den Paar-Zahlen je Quelle (Spalte quellen, JSON). Rückgabe (Version, Ergebnis); unverändert → die
    bisherige Version. Beispiel: erste gelernte Gewichte → (1, …); derselbe Aufruf noch einmal → (1, …)."""
    ergebnis = berechne(con, konfig)
    version, bisher = aktuelle(con, konfig)
    if all(abs(bisher.get(m, 0.0) - ergebnis.werte[m]) < 1e-6 for m in MERKMALE):
        return version, ergebnis
    version += 1
    con.execute(
        """INSERT INTO gewichte (version, werte, datenbasis, vertrauen, trefferquote, trefferquote_start, erstellt,
                                 trefferquote_publikum, trefferquote_publikum_start, quellen)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (version, json.dumps(ergebnis.werte), ergebnis.datenbasis, ergebnis.vertrauen,
         ergebnis.trefferquote, ergebnis.trefferquote_start, iso(jetzt()), ergebnis.trefferquote_publikum,
         ergebnis.trefferquote_publikum_start, json.dumps(ergebnis.paare_je_quelle)),
    )
    return version, ergebnis
