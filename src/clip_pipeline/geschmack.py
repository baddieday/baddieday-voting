"""Dein Geschmack (Stufe 2 des Umbaus, 07.10., Florian: „Lernen zurück“ – aus ✅/❌ und KI-Urteil, mutig ausprobieren,
einmal pro Woche sehen, was er gelernt hat).

Der Bot stellt bei jedem Short drei Schrauben selbst ein und merkt sich, was ankommt:

  Aufbau     ⚡ schnelle Montage · 📖 erzählt · 📈 Steigerung · 🎬 Kino   (stile.STILE)
  Tempo      schnell (Segmente ×0,8) · ruhig (×1,25)
  Zeitlupe   viel (bis 8 je Video) · wenig (höchstens 2)

Lehrer (je Video und Schraube, Beta-Verteilung je Wahl):
  - dein ✅ = Treffer, ❌ = Fehlschlag (Gewicht 1). Dein Grund grenzt ein: ⏱️/⏳/🎵 haben mit den Schrauben nichts zu
    tun (die regeln deine festen Regeln), 😵/🎆/💥 betreffen nur Tempo und Zeitlupe, 🥱 oder ❌ ohne Grund alle drei.
  - die Zuschauer (Stufe 5, 08.10., Florian: „keine 100 oder 1000 Videos bewerten“): je hochgeladenem Video mit
    Zuschauer-Note y (−1 … 1, audience_ergebnisse; TikTok und YouTube zusammen als ein Video, massstab._publikum)
    zählt jede Schraube, die im Video wirkte, doppelt – Treffer (y + 1)/2, wie stile.statistik im Experten-Modus.
  - die KI-Note des fertigen Videos (0–100, kritik.py) mit einem Drittel Gewicht (wie regie_lernen.KI_STAERKE).
Alte Bewertungen zählen sofort für den Aufbau (der Stil steht schon in den Parametern alter Entwürfe).

Wahl: Thompson-Sampling je Schraube; „mutig“ (geschmack.mut, Standard 0,5): bei jedem zweiten Video wird eine Schraube
bewusst auf ihre am wenigsten erprobte Einstellung gestellt (Experiment). Derselbe Aufbau nie dreimal hintereinander.
Nach ❌ → 🥱 (07.10., „das gleiche Video mit anderen Schnitten“): die neue Fassung bekommt einen Aufbau mit ANDERER
Reihenfolge (Montage und Kino haben beide den Bogen) und das andere Tempo, das sich wirklich spürbar unterscheidet
(_anders). Ein fester Stil ([regie].stil) gilt nur noch im Experten-Modus – im einfachen ist er fest „auto“
(einstellungen.EINFACH_FEST, 08.10.). Deine Regeln (regeln.anwenden) kommen danach – sie gehen immer vor.

Die KI schaut sich jedes gesendete Video danach im Hintergrund an (ki_nachtragen, Lern-Bot-Schleife) – das Video
kommt dadurch nicht später. Sonntags ab 18 Uhr fasst wochen_text die Woche zusammen (Lern-Meldung woche:<JJJJ-Www>).
Ehrliche Sätze (Mehrbenutzer Stufe 4, M162): „🎯 Wähle ich gerade öfter/seltener“ ist die Zahl, mit der der Bot wählt
(früher „👍 Kommt gut an / 👎 Kommt weniger an“) – kein Beleg. Was bei den Zuschauern belegt besser ankommt, sagt nur
die 📊-Zeile aus erfolg (feste Wochenzahlen, Mindestzahl, Zufall herausgerechnet); sie ersetzt „👀 Bei den Zuschauern
kommt gut an“, das schon ein einziges Video mit vorläufiger Note zum Favoriten machte.
Wer gerade lehrt – deine ✅/❌, die KI-Note, die Zuschauer – sagt lehrer_zeile (08.10.): in 📋 Stand immer, im
Wochenbericht, wenn ein ✅-Video nach 3 Tagen keine Zahlen hat.
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
ZUSCHAUER_GEWICHT = 2.0                            # je Video mit Zuschauer-Note – wie stile.statistik (Experten-Modus)
NICHT_GESCHMACK = {"kurz", "lang", "musik"}        # dafür gibt es feste Regeln – die Schrauben sind unschuldig
EFFEKT_GRUENDE = {"hektisch", "effekte_viel", "action"}
MUT_STANDARD = 0.5
SPUERBAR = 0.15                                    # 🥱: so viel muss sich die Segmentlänge in Richtung des Tempos ändern


def _wahl_aus(parameter_json: str | None) -> dict:
    try:
        p = json.loads(parameter_json or "{}") or {}
    except (TypeError, ValueError):
        return {}
    wahl = dict(p.get("geschmack") or {})
    # Alte Entwürfe: nur der Stil ist bekannt. Greift auch, wenn das Publikums-Modell nur einen Feinwert des Aufbaus
    # nachsteuerte (Musikpegel, Hektik, Einstieg …, nur_wirksame) – Reihenfolge und Bildgröße blieben die des Stils;
    # sonst lernte niemand mehr den Aufbau, sobald das Publikums-Modell Zahlen hat (N77)
    if "aufbau" not in wahl and p.get("stil") in KNOEPFE["aufbau"]:
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
            WHERE e.format = ? AND e.erstellt >= ? AND (e.variante IS NULL OR e.variante <> 'fail')
            ORDER BY e.id""", (fmt, seit or "")).fetchall()   # ein Fail-Video baut sich selbst auf (viral.py)
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
    """{Schraube: {Wahl: {"n": Gewicht, "s": Treffer-Gewicht, "ja": ✅, "nein": ❌, "zuschauer": Videos mit
    Zuschauer-Note, "zuschauer_s": Summe ihrer Treffer (y + 1)/2}}} über alle Entwürfe – aus deinen ✅/❌, den Zuschauern
    (ZUSCHAUER_GEWICHT je Video, Stufe 5) und der KI-Note. Ohne Zuschauer-Noten genau wie vor Stufe 5."""
    from . import massstab   # hier, nicht oben (wie stile.statistik): geschmack bleibt leicht, massstab lädt numpy

    stat = {k: {o: {"n": 0.0, "s": 0.0, "ja": 0, "nein": 0, "zuschauer": 0, "zuschauer_s": 0.0} for o in opt}
            for k, opt in KNOEPFE.items()}
    alle = beobachtungen(con, fmt)
    zuschauer = massstab._publikum(con, {b["id"]: None for b in alle})
    for b in alle:
        if b["daumen"] in (1, -1):
            for knopf in _schuld(b["daumen"], b["gruende"]) & set(b["wahl"]):
                w = stat[knopf][b["wahl"][knopf]]
                w["n"] += 1.0
                w["s"] += 1.0 if b["daumen"] > 0 else 0.0
                w["ja" if b["daumen"] > 0 else "nein"] += 1
        if (z := zuschauer.get(b["id"])) is not None:   # keine Gründe: jede Schraube, die im Video wirkte
            treffer = max(0.0, min(1.0, (float(z["y"]) + 1) / 2))
            for knopf, wahl in b["wahl"].items():
                w = stat[knopf][wahl]
                w["n"] += ZUSCHAUER_GEWICHT
                w["s"] += ZUSCHAUER_GEWICHT * treffer
                w["zuschauer"] += 1
                w["zuschauer_s"] += treffer
        if b["ki"] is not None:
            for knopf, wahl in b["wahl"].items():
                w = stat[knopf][wahl]
                w["n"] += KI_GEWICHT
                w["s"] += KI_GEWICHT * max(0.0, min(1.0, float(b["ki"]) / 100))
    return stat


def _anders(anders: dict, aufbauten: list[str], basis_seg: float) -> tuple[str, str]:
    """(Aufbau, Tempo) für die Fassung nach 🥱: Tempo umgedreht (ohne gespeichertes Tempo nach dem wirksamen
    Segmentfaktor), Aufbau der erste in aufbauten, mit dem sich die wirksame Segmentlänge um mindestens SPUERBAR in
    Richtung des neuen Tempos ändert – sonst der mit der größten Änderung. Ohne diesen Wächter würde aus
    „Montage + ruhig“ (0,94) ein „Story + schnell“ (1,12): ruhiger statt schneller."""
    alt_seg = float(anders.get("seg_min_faktor") or basis_seg or 1.0)
    alt = anders.get("tempo")
    tempo = ("ruhig" if alt == "schnell" else "schnell") if alt in TEMPO else ("ruhig" if alt_seg < basis_seg else "schnell")
    richtung = 1.0 if tempo == "ruhig" else -1.0
    unten, oben = stile.GRENZEN["seg_min_faktor"]

    def aenderung(o: str) -> float:
        stil = max(unten, min(oben, basis_seg * stile.STILE[o]["faktoren"].get("seg_min_faktor", 1.0)))
        return richtung * (max(unten, min(oben, stil * TEMPO[tempo])) / alt_seg - 1.0)

    return next((o for o in aufbauten if aenderung(o) >= SPUERBAR - 1e-9), max(aufbauten, key=aenderung)), tempo


def waehle(con: sqlite3.Connection, konfig: Konfig, fmt: str = "short", anders: dict | None = None,
           basis_seg: float = 1.0) -> dict:
    """Die drei Schrauben für den nächsten Entwurf (plus "experiment": welche bewusst neu probiert wird, sonst None).
    Deterministisch je Entwurf (Zufall aus Format und Anzahl der Entwürfe).
    anders (🥱, regeln.neue_fassung): Aufbau mit anderer Reihenfolge und anderes Tempo als das abgelehnte Video
    (_anders, basis_seg = gelernter Segmentfaktor vor Stil und Tempo); die Zeitlupe bleibt frei."""
    n = con.execute("SELECT COUNT(*) FROM entwuerfe WHERE format = ?", (fmt,)).fetchone()[0]
    zufall = random.Random(f"geschmack:{fmt}:{n}")
    stat = statistik(con, fmt)
    wahl: dict = {}
    reihe_aufbau: list[str] = []
    for knopf, optionen in KNOEPFE.items():
        zuege = sorted(((zufall.betavariate(1 + stat[knopf][o]["s"], 1 + stat[knopf][o]["n"] - stat[knopf][o]["s"]), o)
                        for o in optionen), reverse=True)
        wahl[knopf] = zuege[0][1]
        if knopf == "aufbau":
            reihe_aufbau = [o for _, o in zuege]
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
    if anders:                                                           # 🥱: sichtbar anders geschnitten
        aufbauten = ([fest] if fest in stile.STILE else
                     [o for o in reihe_aufbau if stile.STILE[o]["reihenfolge"] != anders.get("reihenfolge")] or reihe_aufbau)
        wahl["aufbau"], wahl["tempo"] = _anders(anders, aufbauten, basis_seg)
        wahl["anders_als"] = anders.get("anders_als")
        experiment = None if experiment in ("aufbau", "tempo") else experiment
    if fest in stile.STILE:                                # fester Stil (nur /experte; einfach: „auto“) geht vor
        wahl["aufbau"] = fest
        experiment = None if experiment == "aufbau" else experiment
    wahl["experiment"] = experiment
    return wahl


def anwenden(con: sqlite3.Connection, konfig: Konfig, fmt: str, p: dict, anders: dict | None = None) -> dict:
    """Gelernte Parameter mit den drei Schrauben (Kopie): Aufbau als Stil, Tempo relativ, Zeitlupe als Obergrenze.
    anders: siehe waehle (🥱)."""
    if fmt not in stile.FORMATE:
        return p
    wahl = waehle(con, konfig, fmt, anders=anders, basis_seg=float(p.get("seg_min_faktor", 1.0)))
    p = stile.mit_stil(p, wahl["aufbau"])
    unten, oben = stile.GRENZEN["seg_min_faktor"]
    p["seg_min_faktor"] = round(max(unten, min(oben, float(p.get("seg_min_faktor", 1.0)) * TEMPO[wahl["tempo"]])), 3)
    p["max_lupen"] = ZEITLUPEN[wahl["zeitlupe"]]
    p["geschmack"] = wahl
    return p


# Welche Parameter zu welcher Schraube gehören – ändert sie danach jemand (Publikums-Modell), zählt die Schraube nicht
GEHOERT = {"aufbau": ("stil", "reihenfolge", "rahmen_zoom", "hook_staerkster", "beats_pro_schnitt", "effekt_hektik",
                      "musik_pegel"),
           "tempo": ("seg_min_faktor",), "zeitlupe": ("max_lupen",)}


def nur_wirksame(vorher: dict, nachher: dict, konfig: Konfig) -> dict:
    """Die Wahl ohne die Schrauben, deren Werte nach geschmack.anwenden noch geändert wurden (oder die nicht wirken:
    Zeitlupe bei ausgeschalteten Effekten) – sonst lobte ein ✅ ein Tempo, das gar nicht im Video ist."""
    wahl = dict(vorher.get("geschmack") or {})
    weg = [k for k, namen in GEHOERT.items() if k in wahl and any(vorher.get(n) != nachher.get(n) for n in namen)]
    if not konfig.wert("regie.effekte.an", True) and "zeitlupe" in wahl:
        weg.append("zeitlupe")
    for k in weg:
        wahl.pop(k, None)
    if wahl.get("experiment") in weg:
        wahl["experiment"] = None
    if weg:
        wahl["uebersteuert"] = sorted(set(weg))
    return wahl


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
    unter der Pipeline-Sperre (belegt: sperre.Gesperrt). Rückgabe: KI-Note oder None (Tageslimit, Claude-Fehler).
    Freund ohne eigenen Claude-Zugang (M1): None, ohne zu messen – die KI ist bei ihm aus.
    Laufzeit (Stufe 3): Zeile „ki-note“ nur für die Messung unter der Sperre; „gesperrt“ schreibt sie nie (5 s, M140)."""
    from . import claude_aufruf, kritik, laufzeiten

    if not claude_aufruf.ki_moeglich(konfig):
        return None
    con = db.verbinde(konfig.datenbank)
    try:
        k = einstellungen.anwenden(con, konfig)
        daten = copy.deepcopy(k.daten)   # nie die geladene Konfig ändern
        daten.setdefault("regie", {}).setdefault("kritik", {})["ki"] = True   # einfacher Modus: KI nur hier, nach dem Senden
        k = Konfig(daten=daten, quelle=k.quelle)
        # belegt: Gesperrt, der Bot probiert später
        with laufzeiten.lauf(konfig, "ki-note", ziel=entwurf_id, warten_s=5.0, con=con):
            kritik.bewerte(con, k, entwurf_id, ki=False, lernen=False)       # Messung (ffmpeg) unter der Sperre
        return kritik.bewerte(con, k, entwurf_id).get("ki_score")          # Claude ohne Sperre: neue Fassung wartet nicht
    finally:
        con.close()


# --- Wochenbericht -----------------------------------------------------------------------------------------------

def _anteil(w: dict) -> float:
    return (w["s"] + 1) / (w["n"] + 2)


def _bewertet(w: dict) -> str:
    gesamt = w["ja"] + w["nein"]
    return f" ({w['ja']} von {gesamt} ✅)" if gesamt else ""


def wahl_zeile(stat: dict[str, dict[str, dict]]) -> str | None:
    """„🎯 Wähle ich gerade öfter: Aufbau „Steigerung“ (4 von 4 ✅) · seltener: Aufbau „Kino“ (0 von 4 ✅)“ – None ohne
    klares Bild. Rechnung und Schwelle wie früher „👍 Kommt gut an / 👎 Kommt weniger an“ (Anteil ab 0,6 bzw. bis 0,4,
    je Wahl ab Gewicht 2), nur ehrlich benannt (M162): Das ist die Vorliebe, mit der der Bot wählt – gemischt aus deinen
    ✅/❌, der vorläufigen Zuschauer-Note und der KI-Note –, kein Beleg, dass etwas ankommt.
    „öfter“ nur die Wahl, die in ihrer Schraube echt vor allen anderen liegt, „seltener“ nur die echt hinterste (M176):
    Sonst stünden bei lauter ✅ „viel Zeitlupe“ und „wenig Zeitlupe“ beide unter „öfter“ – gewählt wird aber je Video eine."""
    erprobt = [(k, o, w) for k, opt in stat.items() for o, w in opt.items() if w["n"] >= 2]

    def vorn(k: str, o: str) -> bool:
        return all(_anteil(stat[k][o]) > _anteil(w) for x, w in stat[k].items() if x != o)

    def hinten(k: str, o: str) -> bool:
        return all(_anteil(stat[k][o]) < _anteil(w) for x, w in stat[k].items() if x != o)

    oefter = sorted((x for x in erprobt if _anteil(x[2]) >= 0.6 and vorn(x[0], x[1])), key=lambda x: -_anteil(x[2]))[:3]
    seltener = sorted((x for x in erprobt if _anteil(x[2]) <= 0.4 and hinten(x[0], x[1])),
                      key=lambda x: _anteil(x[2]))[:2]
    teile = [f"{wort}: " + ", ".join(NAMEN[(k, o)] + _bewertet(w) for k, o, w in liste)
             for wort, liste in (("öfter", oefter), ("seltener", seltener)) if liste]
    return "🎯 Wähle ich gerade " + " · ".join(teile) if teile else None


def erfolg_zeile(con: sqlite3.Connection, konfig: Konfig, bis) -> str | None:
    """Die 📊-Zeilen aus `pipeline erfolg` (Mehrbenutzer Stufe 4): je Plattform mit fertigen Wochenzahlen eine Zeile
    („📊 Zuschauer (TikTok): 4 Videos mit fertigen Wochenzahlen – ein erster Vergleich frühestens nach 12 weiteren.“
    bzw. „📊 Belegt (TikTok, 41 Videos): …“), darunter, was noch nicht gemessen wird. None ohne fertige Wochenzahlen und
    beim Freund ohne Abruf (M169). Ein Fehler darin kostet den Wochenbericht nie – er steht nur im Log."""
    try:
        from . import erfolg   # hier, nicht oben: erfolg liest geschmack (Import-Kreis); im try, wie jeder Fehler darin

        return erfolg.zeile_einfach(erfolg.auswertung(con, konfig, bis))
    except Exception:   # noqa: BLE001 – kaputte [erfolg.gewichte], unlesbare Zeilen …: der Bericht kommt trotzdem
        log.exception("Wochenbericht ohne Zeile „📊“ (pipeline erfolg zeigt den Fehler)")
        return None


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
    # 08.10.: ohne klares Bild keine Bitte an dich
    zeilen.append(wahl_zeile(statistik(con)) or "🤔 Noch kein klares Bild – ich probiere weiter selbst aus.")
    if belegt := erfolg_zeile(con, konfig, bis):   # Stufe 4: nur fertige Wochenzahlen, „belegt“ erst mit Mindestzahl
        zeilen.append(belegt)
    neu = sum(1 for z in woche if ((json.loads(z["parameter"] or "{}") or {}).get("geschmack") or {}).get("experiment"))
    if neu:
        zeilen.append(f"🧪 {neu}× bewusst etwas Neues ausprobiert.")
    if ohne_zahlen(con, bis):   # 08.10.: Zahlen fehlen – wer lehrt gerade, und was kannst nur du tun?
        zeilen.append(lehrer_zeile(con, konfig, bis))
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


# --- Wer lehrt (08.10., „📋 sagt ehrlich, wer lehrt“) ------------------------------------------------------------
# Nur aus vorhandenen Daten, ohne Netz: Du merkst ohne Blick ins Journal, ob KI-Note und Zuschauerzahlen ankommen.

KI_FRISCH_TAGE = 3            # wie offen_fuer_ki: die KI benotet gesendete Shorts der letzten 3 Tage
KI_SPAETESTENS_H = 6          # so lange darf ihre Note dauern (Bot baut gerade, Pipeline rechnet, Neustart)
OHNE_ZAHLEN_TAGE = (3, 14)    # Post eines ✅-Videos, 3 bis 14 Tage alt, ohne jede Messung = „ohne Zahlen“


# Gründe aus claude_aufruf, bei denen claude gar nicht lief oder sofort abbrach – dann fehlt meist die Anmeldung
# (die letzten drei gibt es nur bei einem Freund mit eigenem Zugang, M1: Programm in fremdem Bereich, Ordner, Token weg)
KI_ANMELDUNG = ("nicht gefunden", "nicht nutzbar", "Exit", "programm fehlt", "liegt unter", "Claude-Ordner",
                "Claude-Zugang")


def ki_stand(con: sqlite3.Connection, bis=None) -> str:
    """„läuft“: eine KI-Note in den letzten 3 Tagen · „hakt gerade …“: der letzte Versuch scheiterte an etwas, das von
    selbst vorbeigeht (Antwort passte nicht, Abo-Limit, Tageslimit – N46) · „fehlt – Claude-Anmeldung nötig“: claude
    lief gar nicht, oder Videos der letzten 3 Tage sind älter als 6 h und keins hat eine Note · „kommt mit dem
    nächsten Video“: kein Video, an dem es sich zeigen könnte. Der Grund steht in kritiken.details.hinweis."""
    bis = bis or jetzt()
    seit = iso(bis - timedelta(days=KI_FRISCH_TAGE))
    if con.execute("SELECT 1 FROM kritiken WHERE ki_score IS NOT NULL AND erstellt >= ? LIMIT 1", (seit,)).fetchone():
        return "läuft"
    hinweis = ""
    for z in con.execute("SELECT details FROM kritiken WHERE erstellt >= ? ORDER BY erstellt DESC LIMIT 20", (seit,)):
        try:
            hinweis = str(json.loads(z["details"] or "{}").get("hinweis") or "")
        except (ValueError, AttributeError):
            continue
        if hinweis.startswith("KI-Cutter:"):
            break
        hinweis = ""
    if hinweis and not any(s in hinweis for s in KI_ANMELDUNG):
        return "hakt gerade, beim nächsten Video neuer Versuch"
    if hinweis or con.execute("""SELECT 1 FROM entwuerfe WHERE format = 'short' AND status IN ('gesendet', 'bewertet')
                                  AND datei IS NOT NULL AND erstellt >= ? AND erstellt <= ? LIMIT 1""",
                              (seit, iso(bis - timedelta(hours=KI_SPAETESTENS_H)))).fetchone():
        return "fehlt – Claude-Anmeldung nötig"
    return "kommt mit dem nächsten Video"


def ohne_zahlen(con: sqlite3.Connection, bis=None) -> int:
    """✅-Videos, deren TikTok-Post (legt das Paket an) 3 bis 14 Tage alt ist und noch keine einzige Messung hat."""
    bis = bis or jetzt()
    ab, hoechstens = OHNE_ZAHLEN_TAGE
    return con.execute("""SELECT COUNT(*) FROM posts p WHERE p.art = 'entwurf' AND p.plattform = 'tiktok'
                            AND p.erstellt >= ? AND p.erstellt <= ?
                            AND NOT EXISTS (SELECT 1 FROM publikum_messungen m WHERE m.post_id = p.id)""",
                       (iso(bis - timedelta(days=hoechstens)), iso(bis - timedelta(days=ab)))).fetchone()[0]


def lehrer_zeile(con: sqlite3.Connection, konfig: Konfig, bis=None) -> str:
    """„🧠 Lernt aus: deinen ✅/❌ · KI-Note (läuft) · Zuschauern (4 Videos ausgewertet)“ – in 📋 Stand immer, im
    Wochenbericht, wenn ein ✅-Video nach 3 Tagen keine Zahlen hat. Fehlt etwas, das nur du einmal tun kannst (Claude
    anmelden, TikTok verbinden), steht es hier – ohne Netz, nur aus Datenbank, .env und Token-Datei.
    Freund (M1): kein TikTok-Hinweis (in Stufe 1 holt bei ihm niemand Zahlen ab); ohne eigenen Claude-Zugang
    „KI-Note: aus – verbinde dein Claude mit /claude“ (Schritt 9) statt des Anmelde-Hinweises für Florian."""
    from . import autonom, claude_aufruf, publikum_adapter   # hier, nicht oben: geschmack bleibt leicht

    freund = konfig.instanz is not None
    n = autonom.ueberblick(con)["ausgewertet"]
    teile = [f"{n} Video{'s' if n != 1 else ''} ausgewertet"] if n else []
    if freund:
        pass
    elif not publikum_adapter.tiktok_verbunden(konfig):
        teile.append("TikTok nicht verbunden – einmal /tiktok")
    elif fehlen := ohne_zahlen(con, bis):
        teile.append(f"{fehlen} ✅-Video{'s' if fehlen != 1 else ''} nach 3 Tagen noch ohne Zahlen – nicht "
                     "hochgeladen oder den Text dabei geändert?")   # N45: der wahrscheinliche Grund
    zuschauer = ", ".join(teile) or "noch kein Video ausgewertet"
    if not claude_aufruf.ki_moeglich(konfig):
        ki = "KI-Note: aus – verbinde dein Claude mit /claude"
    else:
        stand = ki_stand(con, bis)
        if freund and stand.startswith("fehlt"):
            stand = "fehlt – eigener Claude-Zugang klappt nicht, neu verbinden mit /claude"
        ki = f"KI-Note ({stand})"
    return f"🧠 Lernt aus: deinen ✅/❌ · {ki} · Zuschauern ({zuschauer})"

