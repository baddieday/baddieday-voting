"""Der Regisseur (`pipeline compose`): aus Momenten mit Stimmung wird eine Schnittliste (JSON).

Formate:
  zusammenschnitt  16:9, 3–5 min je nach Material
  short            9:16, 30–45 s, unscharfer Rand, Schriftzug "clip-battle.de"

Schritte (jeder für sich nachvollziehbar, Zahlen in PARAMETER und [regie] der Konfig):
  1. Auswahl     Punkte je Moment: dieselbe Bewertung wie im Clip-Bot (Spec §8.2: vorbewertung.roh_score über
                 merkmale.fuer_moment mit den aktuellen Gewichten – Kill-Serie, Victory, Replay- und Mic-Merkmale),
                 dazu Elo und gelernte Vorlieben (Stimmung und je Moment aus deinen 👍/👎).
                 Abwechslung: Wer im letzten Entwurf war, verliert 70 % seiner Punkte, im vorletzten 35 % usw.
                 (zusammen höchstens 100 %) – so kommen nicht immer dieselben Momente, die stärksten aber
                 regelmäßig wieder. Als Anteil, weil die Punkte weit streuen (Einzelkill 1, Vierfach-Kill 10,
                 Victory +5): ein fester Abzug ließe die stärksten immer vorn.
                 Verworfene Clips nie; höchstens n Momente aus demselben Match.
  2. Bogen       Einstieg = zweitstärkster Moment (Hook), dann steigend, bei ~60 % eine Atempause
                 (lustig/chill), der stärkste zum Schluss. Keine gleiche Stimmung / kein gleiches Match
                 zweimal hintereinander, wenn es sich vermeiden lässt.
  3. Musik       passend zur vorherrschenden Stimmung (Quellen-Stimmung, Energie relativ zur Bibliothek,
                 Tempo); Start so versetzt, dass der "Drop" des Titels auf den Höhepunkt fällt.
  4. Schnitt     jede Grenze auf einem Beat (jedem n-ten); kein Kill wird abgeschnitten. Mit Aktions-Zeiten
                 (merkmale.aktion_sekunden = mein Umhauen) beginnt der Moment vor der ersten Aktion; eine Serie
                 (≥ 2 Kills) bleibt ein Stück bis serie_max_s, Pausen > luecke_max_s zwischen zwei Aktionen
                 werden per Jump-Cut übersprungen (Teile desselben Moments, harter Schnitt).
  5. Übergänge   je Stimmung des folgenden Moments; die Mitte des Übergangs liegt genau auf dem Beat.
                 Mit Effekten (Regisseur 2.0): Rotation aus effekte.PROFIL, in einen epischen/spannenden Höhepunkt
                 ein harter Schnitt auf den Drop.
  6. Effekte     effekte.plane: Zoom-Punch, Kill-Titel, Zähler, Klänge, Look – als Plan in der Schnittliste
                 (version 4). Aus mit [regie.effekte] an = false: Schnitt und Übergänge wie vorher.
Die Schnittliste wird gegen schemas/regie.schema.json und fachlich geprüft, bevor sie gespeichert wird.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from . import effekte, entwurf, lernen, schema
from .db import BEWERTET
from .konfig import Konfig
from .merkmale import fuer_moment
from .musik import ZIEL
from .vorbewertung import roh_score
from .zeit import iso, jetzt

log = logging.getLogger("pipeline")

FORMATE = {
    # Dauer gesamt, Segmentlänge, Serie am Stück (Multikill, Teile per Jump-Cut zusammen), Auflösung
    "zusammenschnitt": {"min_s": 180.0, "max_s": 300.0, "seg_min_s": 3.0, "seg_max_s": 25.0, "serie_max_s": 30.0,
                        "b": 1920, "h": 1080},
    "short": {"min_s": 30.0, "max_s": 45.0, "seg_min_s": 2.5, "seg_max_s": 12.0, "serie_max_s": 20.0,
              "b": 1080, "h": 1920},
}
# Rangfolge der Stimmungen. Seit Stufe 2 (Spec §8.2) nicht mehr Teil der Momentstärke, nur noch Tiebreak bei der
# Musikwahl (gleich lange Anteile: die "stärkere" Stimmung bestimmt die Musik). Rückfrage S2-R2
# (docs/ENTSCHEIDUNGEN.md) ist offen: eventuell kommt sie als Stimmungs-Bonus in `punkte` zurück.
STIMMUNG_WERT = {"episch": 3.0, "spannend": 2.0, "lustig": 1.5, "frustriert": 1.0, "chill": 0.5}
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
    "luecke_max_s": 4.0,          # längere Pause zwischen zwei Aktionen -> Jump-Cut
    "effekt_staerke": {},         # Stimmung -> Faktor auf alle Effekte, fehlt = 1,0 ("zu viele Effekte" ×0,85,
                                  # "mehr Action" ×1,15)
    "effekt_hektik": 1.0,         # dämpft Beat-Akzente und Glitch-Übergänge ("zu hektisch" ×0,9)
}
ABWECHSLUNG_FENSTER = 12          # so viele letzte Entwürfe zählen für die Abwechslung
# Jump-Cut: so lange bleibt das Bild nach der Aktion vor der Lücke, so früh vor der nächsten geht es weiter.
# Wirksame Lücke mindestens NACH + VOR + 0,5 s – so überlappen sich die Teile nie, auch wenn Gelerntes sie verkleinert.
SPRUNG_NACH_S = 1.5
SPRUNG_VOR_S = 2.0
# Schnittliste version 4 (Regisseur 2.0): Grenzen der fachlichen Prüfung
MAX_EREIGNISSE = 400
MAX_LUPEN = 1                     # Standard von [regie.effekte].max_lupen
HOOK_MAX_S = 2.5
V4_FELDER = ("rolle", "kill_s", "lupe", "effekte")


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
    teile: list[tuple[float, float]] = field(default_factory=list)       # Quelle je Teil (Jump-Cut); leer = [kern]
    teile_muss: list[tuple[float, float]] = field(default_factory=list)  # Muss-Zone je Teil
    serie: bool = False            # ≥ 2 Kills mit Aktions-Zeiten: bleibt ein Stück, bis serie_max_s
    max_gruppe: int = 0            # größte Kill-Serie (wie Bot und Elo zählen) – Kill-Titel
    victory: bool = False          # Victory Royale – Titel VICTORY ROYALE

    def __post_init__(self) -> None:
        if not self.teile:  # alte Momente: genau ein Teil = Kern (wie bisher)
            self.teile, self.teile_muss = [self.kern], [self.muss]

    @property
    def kern_laenge(self) -> float:
        """Gewünschte Länge im Video: Summe der Teile (ohne die übersprungenen Lücken)."""
        return sum(b - a for a, b in self.teile)

    @property
    def min_laenge(self) -> float:
        """Kürzeste Länge ohne angeschnittene Action: außen bis zur Muss-Zone, innere Teile ganz."""
        if len(self.teile) == 1:
            return max(0.5, self.muss[1] - self.muss[0])
        innen = sum(b - a for a, b in self.teile[1:-1])
        return (self.teile[0][1] - self.teile_muss[0][0]) + innen + (self.teile_muss[-1][1] - self.teile[-1][0])

    def max_laenge(self, fmt: dict) -> float:
        return fmt["serie_max_s"] if self.serie else fmt["seg_max_s"]


# --- 1. Auswahl ----------------------------------------------------------------------

def _aktionen(mk: dict) -> list[float] | None:
    """Aktions-Zeitpunkte parallel zu kill_sekunden (mein Umhauen, sonst der Kill selbst).
    None bei alten Momenten ohne aktion_sekunden oder wenn die Listen nicht zusammenpassen."""
    kills, aktionen = mk.get("kill_sekunden") or [], mk.get("aktion_sekunden")
    if not kills or not isinstance(aktionen, list) or len(aktionen) != len(kills):
        return None
    # Umhauen kommt nie nach dem Kill – so bleibt der letzte Kill auch das Ende der Action
    return [float(k) if a is None else min(float(a), float(k)) for k, a in zip(kills, aktionen)]


def _kern(mk: dict, dauer: float, p: dict) -> tuple[tuple[float, float], tuple[float, float], str]:
    kills = sorted(mk.get("kill_sekunden") or [])
    if kills:
        aktionen = _aktionen(mk)
        erste = min(kills[0], *aktionen) if aktionen else kills[0]  # mit Aktionen: ab dem ersten Umhauen
        kern = (erste - p["puffer_vor_s"], kills[-1] + p["puffer_nach_s"])
        muss = (erste - 1.0, kills[-1] + 0.5)
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


def anker(mk: dict, dauer: float) -> list[float]:
    """Jump-Cut-Anker: Aktionen und Kills, sortiert, eindeutig, hinten auf den nutzbaren Teil geklemmt.
    Aktionen vor dem Dateibeginn bleiben negativ: sie grenzen die Lücke davor ab (sprung_teile lässt, was ganz vor
    der Datei liegt, weg), statt als künstlicher Anker bei 0 einen Schnipsel zu erzeugen.
    Auch die Kills, damit kein Erledigen in einer übersprungenen Lücke landet. Leer bei alten Momenten."""
    aktionen = _aktionen(mk)
    if aktionen is None:
        return []
    nutzbar = max(0.5, dauer - 0.25)
    return sorted({round(min(float(t), nutzbar), 3) for t in [*aktionen, *mk["kill_sekunden"]]})


def sprung_teile(anker_: list[float], kern: tuple[float, float], luecke_max_s: float
                 ) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """(Teile, Muss je Teil) in der Quelle. Lücke x -> y > luecke_max_s: Schnitt bei x + 1,5 s, weiter bei y − 2,0 s.
    Der erste Teil beginnt am Kern-Anfang, der letzte endet am Kern-Ende. Gruppen ganz vor dem Dateibeginn
    (Anker < 0) fallen weg – der nächste Teil beginnt dann 2,0 s vor seiner Aktion (nicht vor 0).
    Ohne Anker: genau ein Teil = Kern."""
    punkte = sorted(set(anker_))
    if not punkte:
        return [kern], [kern]
    luecke = max(float(luecke_max_s), SPRUNG_NACH_S + SPRUNG_VOR_S + 0.5)
    gruppen = [[punkte[0]]]
    for x, y in zip(punkte, punkte[1:]):
        if round(y - x, 3) > luecke:  # Anker auf ms: genau luecke_max_s ist noch keine Lücke
            gruppen.append([y])
        else:
            gruppen[-1].append(y)
    # Von einer Gruppe vor der Datei wäre nur der Rest nach ihrer Aktion zu sehen (ein Schnipsel ohne Action)
    erste = next((i for i, g in enumerate(gruppen) if g[-1] >= 0), len(gruppen) - 1)
    teile, muss = [], []
    for i, g in enumerate(gruppen[erste:], erste):
        von = kern[0] if i == 0 else max(0.0, g[0] - SPRUNG_VOR_S)
        bis = kern[1] if i == len(gruppen) - 1 else g[-1] + SPRUNG_NACH_S
        teile.append((round(von, 3), round(bis, 3)))
        muss.append((round(max(von, g[0] - 1.0), 3), round(min(bis, g[-1] + 0.5), 3)))
    return teile, muss


def _teile(mk: dict, dauer: float, kern: tuple[float, float], muss: tuple[float, float], p: dict
           ) -> tuple[tuple[float, float], tuple[float, float], list[tuple[float, float]], list[tuple[float, float]],
                      bool]:
    """(Kern, Muss, Teile, Muss je Teil, Serie?) eines Moments. Alte Momente ohne aktion_sekunden: ein Teil = Kern,
    keine Serie. Bleibt ein Teil übrig, weil alles davor vor dem Dateibeginn lag, sind Kern und Muss dieser Teil."""
    punkte = anker(mk, dauer)
    if not punkte:
        return kern, muss, [kern], [muss], False
    teile, teile_muss = sprung_teile(punkte, kern, float(p.get("luecke_max_s", PARAMETER["luecke_max_s"])))
    if len(teile) == 1:
        if teile[0][0] > kern[0] + 1e-3:  # Aktionen vor der Datei übersprungen: ein Stück ab 2 s vor der Aktion
            kern, muss = teile[0], teile_muss[0]
        else:
            teile_muss = [muss]  # ein Stück: dieselbe Muss-Zone wie ohne Jump-Cut
    return kern, muss, teile, teile_muss, len(mk.get("kill_sekunden") or []) >= 2


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


def kandidaten(con: sqlite3.Connection, p: dict, frueher: list[list[str]] | None = None, *,
               gewichte: dict[str, float], kill_tabelle: list[float]) -> list[Kandidat]:
    """Alle Momente mit Stimmung als Kandidaten: Stärke, Punkte für die Auswahl, Kern und Teile für den Schnitt.

    Stärke (`intensitaet`, für Bogen, Hook und Kürzen) = der Moment-Score, dieselbe Bewertung wie im Clip-Bot
    (Spec §8.2): round(roh_score(fuer_moment(clips.merkmale, momente.merkmale, kill_tabelle), gewichte), 2).
    Clip-Momente rechnen mit clips.merkmale (Replay-Merkmale, Länge; Bot-Opfer senken die Stärke), Datei-Momente
    (ohne Clip) mit max_gruppe, Victory und den Mic-Werten (Annahme S2-A9). Die Stimmung zählt nicht mehr mit.
    Punkte (Auswahl) = Stärke + Stimmungs-Bonus (gelernt) + 1 (Clip freigegeben/veröffentlicht/im Highlight)
    + (Elo − 1500)/100 + Moment-Bonus (gelernt) − Abwechslungs-Abzug.

    Parameter: con – offene Verbindung; p – Regie-Parameter (PARAMETER plus Gelerntes); frueher – Momente der
    letzten Entwürfe, neuester zuerst (gezeigte_momente); gewichte – Merkmal → Gewicht, holt der Aufrufer einmal per
    lernen.aktuelle (Leitplanke 7); kill_tabelle – [vorbewertung].kill_punkte (Index = Kills in der Serie).
    Rückgabe: Kandidaten in der Reihenfolge der momente-id. Verworfene Clips und fehlende Dateien fallen weg.
    Fehler: keine eigenen; fehlt ein Gewicht, zählt das Merkmal 0. Die Stärke kann negativ sein (Bot-Opfer, Länge).
    Beispiel: Clip-Moment, clips.merkmale {"kill_punkte": 3, "bot_opfer": 1}, momente {"spitzen": 2}, Startgewichte
    (kill_punkte 1, bot_opfer −2, spitzen 0,25), Status gesendet, Elo 1500 → intensitaet 1.5, punkte 1.5.
    """
    schon = abwechslung(frueher or [], float(p.get("abwechslung", 0.0)))
    zeilen = con.execute(
        """SELECT m.*, c.status AS clip_status, c.elo AS elo, c.merkmale AS clip_merkmale,
                  c.max_gruppe AS max_gruppe, c.victory_royale AS victory_royale
             FROM momente m LEFT JOIN clips c ON c.id = m.clip_id
            ORDER BY m.id"""
    ).fetchall()
    ergebnis = []
    for z in zeilen:
        if z["clip_status"] == "verworfen" or not Path(z["datei"]).is_file():
            continue
        mk = json.loads(z["merkmale"])
        # Kill-/Aktions-Sekunden zählen ab Dateibeginn (Vertrag mit stimmung.py) – nutzbar ist die Datei bis ende_s.
        # Bisher ist start_s immer 0; ein Nachschnitt mit start_s > 0 darf den Anlauf davor mitnehmen.
        dauer = float(z["ende_s"])
        # max_gruppe/victory nur für die Effekt-Titel (Kill-Titel, VICTORY ROYALE) – wie Bot und Elo zählen
        gruppe = int(z["max_gruppe"] if z["max_gruppe"] is not None else mk.get("max_gruppe", 0) or 0)
        victory = int(z["victory_royale"] or mk.get("victory_royale", 0) or 0)
        # Ohne Clip (LEFT JOIN liefert NULL) ist es ein Datei-Moment: fuer_moment rechnet dann über max_gruppe
        clip_mk = json.loads(z["clip_merkmale"]) if z["clip_merkmale"] is not None else None
        # Die eine Formel (Leitplanke 3); auf 2 Stellen wie bisher – Bogen und Schnittliste zeigen diese Zahl
        intensitaet = round(roh_score(fuer_moment(clip_mk, mk, kill_tabelle), gewichte), 2)
        punkte = intensitaet + float(p["stimmung_bonus"].get(z["stimmung"], 0.0))
        if z["clip_status"] in BEWERTET:  # freigegeben, veröffentlicht, im Highlight: von dir für gut befunden
            punkte += 1.0
        if z["elo"] is not None:
            punkte += (float(z["elo"]) - 1500.0) / 100.0
        punkte += float(p.get("moment_bonus", {}).get(z["schluessel"], 0.0))
        anteil, gezeigt = schon.get(z["schluessel"], (0.0, 0))
        abzug = round(anteil * max(punkte, 1.0), 2)  # auch schwache Momente (< 1 Punkt) verlieren etwas
        kern, muss, grund = _kern(mk, dauer, p)
        kern, muss, teile, teile_muss, serie = _teile(mk, dauer, kern, muss, p)
        if len(teile) > 1:
            grund += f", {len(teile)} Teile (Jump-Cut)"
        ergebnis.append(Kandidat(z["schluessel"], z["datei"], dauer, z["stimmung"], intensitaet,
                                 round(punkte - abzug, 2), z["clip_id"], z["match_id"], kern, muss, grund, mk,
                                 abzug, gezeigt, teile, teile_muss, serie, max_gruppe=gruppe, victory=bool(victory)))
    return ergebnis


def plan_laenge(k: Kandidat, fmt: dict, seg_min: float) -> float:
    """Ungefähre Länge im Video (vor dem Einrasten auf den Beat). Eine Serie wird nie unter ihre Mindestlänge
    gekürzt – auch wenn sie länger als serie_max_s ist (im Zusammenschnitt kommt sie dann ganz)."""
    laenge = min(k.max_laenge(fmt), max(seg_min, k.kern_laenge))
    return max(laenge, k.min_laenge) if (k.serie or len(k.teile) > 1) else laenge


def serie_zu_lang(k: Kandidat, fmt: dict) -> bool:
    """Serie, die selbst mit Jump-Cuts nicht in serie_max_s passt (im Short: nicht wählen statt zerteilen)."""
    return k.serie and k.min_laenge > fmt["serie_max_s"] + 1e-6


def waehle(kandidaten_: list[Kandidat], fmt: dict, p: dict) -> tuple[list[Kandidat], float, list[str]]:
    """Beste Momente, bis die Ziel-Dauer erreicht ist. Ziel richtet sich nach dem Material."""
    hinweise = []
    seg_min = fmt["seg_min_s"] * p["seg_min_faktor"]
    laenge = lambda k: plan_laenge(k, fmt, seg_min)  # noqa: E731
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

def _mehrteilig(k: Kandidat, t: float, raster: list[float], fmt: dict, seg_min: float, nutzbar: float
                ) -> list[tuple[float, float, tuple[float, float], float, float, bool]]:
    """Moment mit Jump-Cuts: [(Quelle von, bis, Muss, Zeit von, bis, auf dem Beat)] je Teil.
    Die Schnittpunkte zwischen den Teilen liegen fest (an der Action). Beweglich sind nur der Anfang des ersten
    Teils (mehr oder weniger Anlauf) und das Ende des letzten – dieses rastet auf den Beat ein."""
    teile, tm = k.teile, k.teile_muss
    innen = sum(b - a for a, b in teile[1:-1])
    erst_min, erst_max = teile[0][1] - tm[0][0], teile[0][1]                  # Teil 1: bis zur Muss-Zone / zum Dateianfang
    letzt_min, letzt_max = tm[-1][1] - teile[-1][0], nutzbar - teile[-1][0]   # letzter Teil: Muss-Ende / Dateiende
    min_ges, max_ges = erst_min + innen + letzt_min, erst_max + innen + letzt_max
    wunsch = min(max_ges, k.max_laenge(fmt), max(seg_min, k.kern_laenge, min_ges))
    unten, oben = max(seg_min, min_ges), min(max_ges, max(wunsch, min_ges) + 2.0)
    t = round(t, 3)  # alles auf ms: Quelle und Zeitleiste bleiben exakt gleich lang
    passend = [b for b in raster if unten - 1e-6 <= b - t <= oben + 1e-6]
    ende = round(naechster(passend, t + wunsch) if passend else t + max(min(wunsch, max_ges), min(unten, max_ges)), 3)
    laenge = ende - t
    # wie bei einem Stück: 60 % der Abweichung vorne (lieber etwas mehr Anlauf), der Rest hinten
    erst = (teile[0][1] - teile[0][0]) + 0.6 * (laenge - k.kern_laenge)
    erst = round(min(max(erst, erst_min, laenge - innen - letzt_max), erst_max, laenge - innen - letzt_min), 3)
    stuecke, z = [], t
    for (von, bis), muss in zip([(teile[0][1] - erst, teile[0][1]), *teile[1:-1]], tm[:-1]):
        stuecke.append((von, bis, muss, z, z + (bis - von), False))
        z += bis - von
    stuecke.append((teile[-1][0], teile[-1][0] + (ende - z), tm[-1], z, ende, bool(passend)))
    return stuecke


def plane_zeitleiste(reihe: list[Kandidat], raster: list[float], fmt: dict, p: dict, fps: int,
                     fx: dict | None = None) -> list[dict]:
    """Legt Segmentgrenzen auf Beats. Gibt Segmente mit Quelle (start/ende) und Zeitleiste (zeit_*) zurück.
    Ein Moment mit Jump-Cuts wird zu mehreren aufeinanderfolgenden Segmenten (Feld `teil`, harter Schnitt).
    fx: effekte.einstellungen() – ohne (oder an = false) Übergänge wie bisher (UEBERGANG)."""
    fx = fx or {"an": False}
    seg_min = fmt["seg_min_s"] * p["seg_min_faktor"]
    segmente, t = [], 0.0
    je_stimmung: dict[str, int] = {}   # Rotation der Übergänge: frühere Momente derselben Stimmung
    glitches = 0
    for k in reihe:
        # Am Dateiende 0,25 s frei lassen: Schnitt/Übergang brauchen dort noch Bilder (entwurf._griffe, UEBERHANG_S)
        nutzbar = max(0.5, k.dauer_s - 0.25)
        if len(k.teile) > 1:
            stuecke = _mehrteilig(k, t, raster, fmt, seg_min, nutzbar)
        else:
            muss_laenge = max(0.5, k.muss[1] - k.muss[0])
            wunsch = min(k.max_laenge(fmt), max(seg_min, k.kern_laenge, muss_laenge))
            wunsch = min(wunsch, nutzbar)
            unten, oben = max(seg_min, muss_laenge), min(nutzbar, max(wunsch, muss_laenge) + 2.0)
            passend = [b for b in raster if unten - 1e-6 <= b - t <= oben + 1e-6]
            ende = naechster(passend, t + wunsch) if passend else t + max(min(wunsch, nutzbar), min(unten, nutzbar))
            laenge = ende - t
            # Quelle: Kern mittig, lieber etwas mehr Anlauf; die Muss-Zone bleibt immer drin
            extra = laenge - k.kern_laenge
            start = k.kern[0] - 0.6 * extra
            start = min(start, k.muss[0])
            start = max(start, k.muss[1] - laenge)
            start = max(0.0, min(start, nutzbar - laenge))
            stuecke = [(start, start + laenge, k.muss, t, ende, bool(passend))]
        for teil, (von, bis, muss, z_von, z_bis, auf_beat) in enumerate(stuecke, 1):
            nr = len(segmente) + 1
            if teil == 1 and nr > 1:
                art, dauer = effekte.uebergang(k.stimmung, je_stimmung.get(k.stimmung, 0), k is reihe[-1], p,
                                               bool(fx["an"]), glitches, profil_=fx.get("profile", {}).get(k.stimmung),
                                               max_glitch=int(fx.get("max_glitch", 1)))
                glitches += art == "glitch"
                uebergang = {"art": art, "dauer_s": dauer}
            else:  # erstes Segment, Jump-Cut innerhalb des Moments
                uebergang = {"art": "schnitt", "dauer_s": 0.0}
            segment = {
                "nr": nr, "moment": k.schluessel, "clip_id": k.clip_id, "match_id": k.match_id, "datei": k.datei,
                "stimmung": k.stimmung, "intensitaet": k.intensitaet, "grund": k.grund,
                "punkte": k.punkte, "abzug": k.abzug, "gezeigt": k.gezeigt,
                "quelle_start_s": round(von, 3), "quelle_ende_s": round(bis, 3),
                "quelle_dauer_s": round(k.dauer_s, 3), "muss": [round(muss[0], 3), round(muss[1], 3)],
                "zeit_start": round(z_von, 3), "zeit_ende": round(z_bis, 3),
                "uebergang": uebergang,
                "auf_beat": auf_beat,
            }
            if len(stuecke) > 1:
                segment["teil"] = teil
            segmente.append(segment)
        t = stuecke[-1][4]
        je_stimmung[k.stimmung] = je_stimmung.get(k.stimmung, 0) + 1
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


def pruefe_liste(liste: dict, *, max_lupen: int = MAX_LUPEN) -> list[str]:
    """Schema plus fachliche Regeln: Zeitleiste lückenlos, Quelle im Video, kein Kill angeschnitten, Dauer im Rahmen,
    Teile eines Moments (Jump-Cut) direkt hintereinander, aus derselben Datei, vorwärts, mit hartem Schnitt.
    version 4 (Effekte): Ereignisse im Quellfenster ihres Segments mit ihren Pflichtfeldern, höchstens
    MAX_EREIGNISSE; Zeitlupe im Segment (Länge mit Zuschlag), höchstens max_lupen; Hook nur vorn (Stufe 4)."""
    fehler = schema.pruefe(liste, schema.lade("regie"))
    if fehler:
        return fehler
    v4 = liste["version"] >= 4
    if not v4 and "effekte" in liste:
        fehler.append("effekte erst ab version 4")
    t, vorher, ereignisse, lupen, hooks = 0.0, None, 0, 0, []
    for n, s in enumerate(liste["segmente"]):
        felder = [f for f in V4_FELDER if f in s]
        if not v4 and felder:
            fehler.append(f"Segment {s['nr']}: {', '.join(felder)} erst ab version 4")
        if abs(s["zeit_start"] - t) > 1e-3:
            fehler.append(f"Segment {s['nr']}: Lücke in der Zeitleiste")
        t = s["zeit_ende"]
        if s.get("teil", 1) > 1 and (vorher is None or vorher["moment"] != s["moment"] or vorher["datei"] != s["datei"]
                                     or vorher.get("teil") != s["teil"] - 1 or s["uebergang"]["art"] != "schnitt"
                                     or s["quelle_start_s"] < vorher["quelle_ende_s"] - 1e-3):
            fehler.append(f"Segment {s['nr']}: Teil {s['teil']} passt nicht zum vorigen Teil")
        vorher = s
        if s["quelle_start_s"] < -1e-3 or s["quelle_ende_s"] > s["quelle_dauer_s"] + 1e-3:
            fehler.append(f"Segment {s['nr']}: außerhalb des Videos")
        if s["quelle_start_s"] > s["muss"][0] + 1e-3 or s["quelle_ende_s"] < s["muss"][1] - 1e-3:
            fehler.append(f"Segment {s['nr']}: schneidet die Action an")
        qs, qe, zuschlag = s["quelle_start_s"], s["quelle_ende_s"], 0.0
        if lupe := s.get("lupe"):
            lupen += 1
            if not (qs + 0.1 - 1e-3 <= lupe["ab_s"] < lupe["bis_s"] <= qe - 0.1 + 1e-3) \
                    or lupe["bis_s"] - lupe["ab_s"] > 1.5 + 1e-3:
                fehler.append(f"Segment {s['nr']}: Zeitlupe außerhalb des Segments oder länger als 1,5 s")
            zuschlag = (lupe["bis_s"] - lupe["ab_s"]) * (1 / lupe["faktor"] - 1)
        if abs((s["zeit_ende"] - s["zeit_start"]) - ((qe - qs) + zuschlag)) > 1e-3:
            fehler.append(f"Segment {s['nr']}: Länge Quelle ≠ Zeitleiste")
        for k in s.get("kill_s") or []:
            if not qs - 1e-3 <= k <= qe + 1e-3:
                fehler.append(f"Segment {s['nr']}: Kill bei {k} außerhalb des Segments")
        for e in s.get("effekte") or []:
            if not qs - 1e-3 <= e["t_s"] <= qe + 1e-3:
                fehler.append(f"Segment {s['nr']}: Effekt {e['art']} bei {e['t_s']} außerhalb des Segments")
            pflicht = {"titel": "text", "zaehler": "zahl", "sfx": "klang"}.get(e["art"])
            if pflicht and pflicht not in e:
                fehler.append(f"Segment {s['nr']}: Effekt {e['art']} ohne {pflicht}")
        ereignisse += len(s.get("effekte") or [])
        if s.get("rolle") == "hook":
            hooks.append((n, s))
    if abs(t - liste["dauer_s"]) > 1e-3:
        fehler.append("Gesamtdauer stimmt nicht")
    if ereignisse > MAX_EREIGNISSE:
        fehler.append(f"{ereignisse} Effekt-Ereignisse – höchstens {MAX_EREIGNISSE}")
    if lupen > max_lupen:
        fehler.append(f"{lupen} Zeitlupen – höchstens {max_lupen}")
    for n, s in hooks:
        if n != 0 or len(hooks) > 1:
            fehler.append(f"Segment {s['nr']}: Hook nur als erstes Segment und höchstens einer")
        if s["zeit_ende"] - s["zeit_start"] > HOOK_MAX_S + 1e-3:
            fehler.append(f"Segment {s['nr']}: Hook länger als {HOOK_MAX_S} s")
        if len(liste["segmente"]) < 2 or s["moment"] != liste["segmente"][-1]["moment"] or not s.get("kill_s"):
            fehler.append(f"Segment {s['nr']}: Hook ohne Kill oder nicht aus dem Höhepunkt")
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
    fx, fx_hinweise = effekte.einstellungen(konfig)
    frueher = gezeigte_momente(con)
    # Gewichte einmal holen und durchreichen (Leitplanke 7); die Kill-Tabelle ist dieselbe wie im Clip-Bot
    _version, gewichte = lernen.aktuelle(con, konfig)
    kill_tabelle = [float(x) for x in konfig.wert("vorbewertung.kill_punkte")]
    alle = [k for k in kandidaten(con, p, frueher, gewichte=gewichte, kill_tabelle=kill_tabelle)
            if nur_matches is None or k.match_id in nur_matches]
    if not alle:
        raise RegieFehler("Keine Momente mit Stimmung" + (" in diesen Matches" if nur_matches else "")
                          + " – erst `pipeline stimmung`")
    # Short: eine Serie, die selbst mit Jump-Cuts nicht in serie_max_s passt, wird nicht gewählt statt zerteilt
    # (im Zusammenschnitt kommt sie ganz)
    zu_lang = [k for k in alle if fmt_name == "short" and serie_zu_lang(k, fmt)]
    if zu_lang:
        alle = [k for k in alle if not any(k is z for z in zu_lang)]
        if not alle:
            raise RegieFehler(f"Alle {len(zu_lang)} Momente sind Serien, die für einen Short zu lang sind "
                              f"(> {fmt['serie_max_s']:.0f} s am Stück) – Zusammenschnitt nehmen")
    gewaehlt, ziel_s, hinweise = waehle(alle, fmt, p)
    if zu_lang:
        hinweise.append(f"{len(zu_lang)} Serie(n) zu lang für Short (> {fmt['serie_max_s']:.0f} s am Stück)")
    reihe = bogen(gewaehlt, fmt_name)

    # Vorherrschende Stimmung (nach Länge gewichtet) bestimmt die Musik
    anteile: dict[str, float] = {}
    for k in reihe:
        anteile[k.stimmung] = anteile.get(k.stimmung, 0.0) + k.kern_laenge
    # STIMMUNG_WERT nur noch als Tiebreak (Spec §8.2 nimmt ihn aus der Momentstärke; Rückfrage S2-R2 in
    # docs/ENTSCHEIDUNGEN.md ist offen)
    haupt = max(anteile, key=lambda s: (anteile[s], STIMMUNG_WERT[s]))
    track, wertung = waehle_musik(con, haupt, ziel_s, p, ziel)

    raster: list[float] = []
    schlaege: list[float] = []
    versatz = 0.0
    if track is not None:
        schlaege = json.loads(track["beats"])
        # Höhepunkt (letztes Segment) beginnt ungefähr bei ziel_s minus seiner Länge
        hoehe_start = max(0.0, ziel_s - min(reihe[-1].max_laenge(fmt), reihe[-1].kern_laenge))
        versatz = max(0.0, drop(json.loads(track["verlauf"] or "[]")) - hoehe_start)
        if versatz + ziel_s > float(track["dauer_s"]):
            versatz = max(0.0, float(track["dauer_s"]) - ziel_s)
        versatz = naechster([b for b in schlaege if b <= versatz + 1] or [0.0], versatz)
        schritt = max(1, int(p["beats_pro_schnitt"]))
        raster = [round(b - versatz, 3) for b in schlaege if b - versatz > 0.2][schritt - 1::schritt]
    else:
        hinweise.append("keine Musik in der Bibliothek – ohne Musik, Schnitte nicht auf dem Beat")

    segmente = plane_zeitleiste(reihe, raster, fmt, p, fps, fx)
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
            neue_segmente = plane_zeitleiste(neue_reihe, raster, fmt, p, fps, fx)
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
        segmente = plane_zeitleiste(reihe, raster, fmt, p, fps, fx)
    gesamt = segmente[-1]["zeit_ende"] if segmente else 0.0
    if gesamt < fmt["min_s"] - 1e-6:
        hinweise.append(f"Dauer {gesamt:.1f} s unter {fmt['min_s']:.0f} s – zu wenig Material")
    # Gezählt werden Momente, nicht Segmente (ein Moment mit Jump-Cut hat mehrere Teile, der Hook wiederholt einen)
    momente = [s for s in segmente if s.get("teil", 1) == 1 and s.get("rolle") != "hook"]
    neu = sum(1 for s in momente if s["gezeigt"] == 0)
    if frueher and len(alle) < 3 * len(momente):
        hinweise.append(f"nur {len(alle)} Momente zur Auswahl – für mehr Abwechslung mehr Clips analysieren")

    # 6. Effekte: Plan in die Segmente (Aus: Schnitt und Übergänge wie vorher, kein Plan)
    hinweise += [h for h in fx_hinweise if h not in hinweise]
    if fx["an"] and segmente:
        beats = [round(b - versatz, 3) for b in schlaege if 0 < b - versatz < gesamt]
        fx_plan = effekte.plane(segmente, reihe, p, konfig, fmt_name, fps, beats, stimmung=haupt)
    else:
        fx_plan = {"an": False}

    if name is None:
        name = basis = f"{fmt_name}-{jetzt():%Y%m%d-%H%M%S}"
        n = 1
        while con.execute("SELECT 1 FROM entwuerfe WHERE name = ?", (name,)).fetchone():
            n += 1
            name = f"{basis}-{n}"
    liste = {
        "version": 4, "art": "regie", "name": name, "format": fmt_name,
        "aufloesung": [fmt["b"], fmt["h"]], "fps": fps, "dauer_s": round(gesamt, 3),
        "stimmung": haupt, "parameter": p,
        "musik": None if track is None else {
            "track_id": track["id"], "datei": track["datei"], "titel": track["titel"], "kuenstler": track["kuenstler"],
            "quelle": track["quelle"], "bpm": track["bpm"], "start_s": round(versatz, 3),
            "pegel": float(p["musik_pegel"]), "wertung": {str(k): v for k, v in wertung.items()},
        },
        "overlay": str(konfig.wert("shorts.overlay_text", "clip-battle.de")) if fmt_name == "short" else None,
        "effekte": fx_plan,
        "bogen": [s["intensitaet"] for s in momente],
        "auswahl": {"kandidaten": len(alle), "neu": neu, "schon_gezeigt": len(momente) - neu,
                    "abwechslung": float(p.get("abwechslung", 0.0))},
        "segmente": segmente,
        "hinweise": hinweise,
        "erstellt": iso(jetzt()),
    }
    if fehler := pruefe_liste(liste, max_lupen=int(konfig.wert("regie.effekte.max_lupen", MAX_LUPEN))):
        raise RegieFehler("Schnittliste ungültig: " + "; ".join(fehler[:3]))
    # Passt der Filtergraph samt Effekten auf die Befehlszeile? Sonst scheiterte erst das Rendern.
    if fx_plan["an"] and (fehler := entwurf.graph_fehler(liste, konfig)):
        raise RegieFehler("Schnittliste zu groß: " + fehler[0])
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
            "dauer_s": round(gesamt, 1), "segmente": len(segmente), "momente": len(momente), "stimmung": haupt,
            "musik": liste["musik"]["titel"] if track else None, "neu": neu, "hinweise": hinweise}
