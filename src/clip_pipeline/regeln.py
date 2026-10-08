"""Deine Regeln (Stufe 1, 07.10., Florian: „Impact wird nicht gewertet – egal ob 50 oder 100 Videos mit zu kurz oder
Clip langweilig“).

Vorher war jeder Grund unter 👎 nur eine Stimme unter vielen: Regie-Lernen, KI-Cutter, Publikums-Modell und
Stil-Auswahl rechneten alle mit, und dein Knopf wurde verwässert. Jetzt ist jeder Grund eine feste Regel mit sofortiger,
sichtbarer Wirkung – der Bot sagt dir in einem Satz, was er geändert hat:

  ⏱️ zu kurz       Länge des Videos +10 s (bis 75 s), ab jetzt Untergrenze   Einstellung regie.short_ziel_s
  ⏳ zu lang       Länge des Videos −10 s (ab 30 s), ab jetzt Obergrenze
                   (Stufe 5, 08.10., nur einfacher Modus: darüber bzw. darunter wählt das Publikums-Modell mit
                   Belegen – nie gegen deine Richtung; unter /experte bleibt die Länge fest, siehe laenge)
  🥱 langweilig    der SCHNITT langweilt (07.10.): neue Fassung mit anderem Aufbau, Tempo und Song; die stärkere
                   Hälfte der Szenen bleibt, die schwächere wird nur in dieser Fassung durch neue ersetzt – keine
                   Sperre (neue_fassung → geschmack.waehle und regie.fassung_kandidaten). 08.10.: Gegenpaar zu 😵 –
                   gesenkte Effekte eine Stufe zurück (aus → ruhig → normal, nie darüber; mehr_effekte)
  🎵 Musik         dieser Song kommt nie wieder                             Tabelle sperren (art track)
  😵 zu hektisch   Effekte eine Stufe ruhiger (wild → normal → ruhig → aus) Einstellung regie.effekt_stufe
  (Experten-Gründe: 🎆 zu viele Effekte wie hektisch, 💥 mehr Action eine Stufe wilder)

anwenden() legt die Regeln NACH allem Gelernten über die Regie-Parameter – sie gehen immer vor. Die Sperren wirken direkt
in regie.kandidaten_mit_bericht und regie.waehle_musik; alte 🥱-Sperren (grund „langweilig“, bis 07.10.) bleiben in der
Tabelle, gelten aber nicht mehr. Gründe ohne Regel (✂️ abgeschnitten, 🎯 getroffen) lernt der Bot wie bisher.
"""

from __future__ import annotations

import json
import sqlite3

from . import einstellungen, stile
from .konfig import Konfig
from .zeit import iso, jetzt

ZIEL_SCHLUESSEL = "regie.short_ziel_s"
STUFE_SCHLUESSEL = "regie.effekt_stufe"
SZENEN_SCHLUESSEL = "regie.szenen"
ZIEL_SCHRITT = 10
ZIEL_GRENZEN = (30, 75)                    # Short: 30–75 s (FORMATE["short"])
STUFEN = {0: "aus", 1: "ruhig", 2: "normal", 3: "wild"}
STANDARD_STUFE = 2
STUFE_WERTE = {1: (0.5, 0.5), 2: (1.0, 1.0), 3: (1.3, 1.3)}   # (effekt_hektik, Effekt-Stärke je Stimmung)
STIMMUNGEN = ("episch", "spannend", "lustig", "frustriert", "chill")
MUSIK_ROTATION = 8                         # so viele letzte Entwürfe lang kommt ein Song nicht wieder
REGEL_GRUENDE = ("kurz", "lang", "langweilig", "musik", "hektisch", "effekte_viel", "action")


# --- Sperren ------------------------------------------------------------------------------------------------

def gesperrt(con: sqlite3.Connection, art: str) -> set[str]:
    """Schlüssel der gesperrten Momente (art "moment") bzw. Songs (art "track", tracks.id als Text). Alte 🥱-Sperren
    (grund „langweilig“) zählen seit 07.10. nicht mehr – 🥱 ist ein Urteil über den Schnitt, nicht über die Szenen."""
    try:
        return {z["schluessel"] for z in con.execute(
            "SELECT schluessel FROM sperren WHERE art = ? AND COALESCE(grund, '') <> 'langweilig'", (art,))}
    except sqlite3.OperationalError:   # sehr alte Datenbank ohne die Tabelle
        return set()


def sperre(con: sqlite3.Connection, art: str, schluessel: list[str], grund: str) -> int:
    """Sperrt Momente bzw. Songs (doppelt egal). Rückgabe: wie viele neu gesperrt wurden."""
    neu = 0
    for s in schluessel:
        cur = con.execute("INSERT INTO sperren (art, schluessel, grund, erstellt) VALUES (?, ?, ?, ?) "
                          "ON CONFLICT (art, schluessel) DO NOTHING", (art, str(s), grund, iso(jetzt())))
        neu += cur.rowcount
    return neu


# --- Wirksame Werte -------------------------------------------------------------------------------------------

def _gesetzt(con: sqlite3.Connection, konfig: Konfig, schluessel: str):
    """Wert aus dem Bot (⚙️/❌) oder aus der Datei – None, wenn keiner gesetzt ist (dann gilt das Gelernte).
    Was der einfache Modus festlegt (einstellungen.EINFACH_FEST), gilt dort vor einer alten ⚙️-Zeile (08.10.)."""
    ist_fest, wert = einstellungen.fest(con, konfig, schluessel)
    if ist_fest:
        return wert
    bot = einstellungen.gespeichert(con)
    return bot[schluessel] if schluessel in bot else konfig.wert(schluessel, None)


def ziel_regel(con: sqlite3.Connection, konfig: Konfig) -> float | None:
    """Deine Short-Länge in s, None = automatisch (gelernt). Ob fest oder Grenze: laenge."""
    wert = _gesetzt(con, konfig, ZIEL_SCHLUESSEL)
    return float(wert) if wert else None


GRENZE_WORT = {"kurz": "mindestens", "lang": "höchstens"}


def laenge(con: sqlite3.Connection, konfig: Konfig) -> tuple[float, str | None] | None:
    """Deine Short-Länge als (Sekunden, Richtung), None ohne Regel (dann gilt das Gelernte).
    Stufe 5 (08.10., Florian: „mach doch endlich ein Video das sich immer wieder verbessert“): Im einfachen Modus ist sie
    keine feste Länge mehr. Nach deinem letzten „⏱️ zu kurz“ ist sie eine Untergrenze (Richtung "kurz": nie kürzer),
    nach „⏳ zu lang“ eine Obergrenze ("lang": nie länger); darüber bzw. darunter entscheidet das Publikums-Modell mit
    Belegen (regie_lernen.aktuelle, anwenden). Die Richtung kommt aus deinen Bewertungen
    (regie_lernen.laengen_richtung) – kein neuer Schlüssel. Richtung None = feste Länge wie bis 08.10.: unter /experte
    und für einen Wert aus ⚙️, zu dem du nie ⏱️/⏳ getippt hast."""
    ziel = ziel_regel(con, konfig)
    if not ziel:
        return None
    ziel = max(ZIEL_GRENZEN[0], min(ZIEL_GRENZEN[1], ziel))
    if einstellungen.experte(con, konfig):
        return ziel, None
    from . import regie_lernen   # hier, nicht oben: regie_lernen lädt die ganze Regie

    try:
        return ziel, regie_lernen.laengen_richtung(con)
    except sqlite3.OperationalError:   # sehr alte Datenbank ohne Entwürfe: fest wie bisher
        return ziel, None


def _ohne_scheinversuch(p: dict) -> dict:
    """Stufe 5 (08.10.): Ein Versuch des Publikums-Modells (autonom.plan_parameter), dessen Wert das Video am Ende nicht
    hat, fällt für dieses Video weg – sonst stünde in lern_experimente ein Versuch, den es nie gab
    (autonom.snapshot_speichern): eine Länge, die deine Grenze oder die alte ⚙️-Mindestlänge verschoben hat, und
    (Prüfung 08.10.) Effekte, die deine Effekt-Stufe überstimmt, oder ein Tempo, das 🥱 umgedreht hat. Nur einfacher
    Modus; die Länge mit 0,05 s Spielraum (Rundung, 45 × 55/45)."""
    auto = p.get("autonom")
    versuch = auto.get("exploration") if isinstance(auto, dict) else None
    if not isinstance(versuch, dict) or p.get(versuch.get("variable")) is None:
        return p
    try:
        abstand = abs(float(versuch.get("wert") or 0.0) - float(p[versuch["variable"]]))
    except (TypeError, ValueError):
        return p
    if abstand > (0.05 if versuch["variable"] == "ziel_dauer_s" else 1e-6):
        p["autonom"] = {**auto, "exploration": None}   # neue Liste: die des Aufrufers bleibt, wie sie ist
    return p


def stufe(con: sqlite3.Connection, konfig: Konfig) -> int | None:
    """Deine Effekt-Stufe 0–3, None = nie gesetzt (dann gilt das Gelernte).
    08.10.: der alte Schalter „✨ Effekte aus“ (regie.effekte.an = false, ⚙️ vom 06.10. oder Datei) ohne Stufe zählt
    als Stufe „aus“ – vorher hieß es im 📋 Stand „Effekte normal“, und „😵 Zu hektisch“ schaltete sie wieder EIN."""
    wert = _gesetzt(con, konfig, STUFE_SCHLUESSEL)
    if wert is not None:
        return int(wert)
    return 0 if _gesetzt(con, konfig, "regie.effekte.an") is False else None


def nur_starke(con: sqlite3.Connection, konfig: Konfig) -> bool:
    return str(_gesetzt(con, konfig, SZENEN_SCHLUESSEL) or "stark") != "alle"


def anwenden(con: sqlite3.Connection, konfig: Konfig, fmt: str, p: dict) -> dict:
    """Regie-Parameter mit deinen Regeln darüber (Kopie): Short-Länge (fest bzw. im einfachen Modus Grenze mit
    Richtung, laenge), Effekt-Stufe, nur starke Szenen, Songs rotieren. Aufrufer: Lern-Bot (baue_entwurf) und der
    Abend-Ablauf (sitzung) – nach regie_lernen.aktuelle.
    Beispiel (einfacher Modus, Stufe 5): nach ⏱️ auf 55 s will das Publikum 65 → 65; nach ⏳ auf 45 s will es 55 → 45."""
    p = dict(p)
    einfach = not einstellungen.experte(con, konfig)
    if fmt == "short" and (regel := laenge(con, konfig)):
        ziel, richtung = regel
        gelernt = p.get("ziel_dauer_s")
        if richtung is None or gelernt is None:
            p["ziel_dauer_s"] = ziel                     # fest wie bis 08.10. (/experte, ⚙️-Wert ohne ⏱️/⏳)
        else:   # Stufe 5: Grenze in deiner Richtung – darüber bzw. darunter gilt die Wahl des Publikums-Modells
            p["ziel_dauer_s"] = (max if richtung == "kurz" else min)(float(gelernt), ziel)
        if richtung is not None:   # Prüfung 08.10.: regie.mehr_anlauf streckt nach ⏳ nie über deine Grenze, und
            p["laenge_richtung"], p["laenge_grenze_s"] = richtung, ziel   # „kürzer als deine Mindestlänge“ (regie)
    s = stufe(con, konfig)
    if s == STANDARD_STUFE and einfach:
        # Prüfung 08.10.: „normal“ heißt im einfachen Modus „wie gelernt“ (wie ohne Stufe) – sonst galt nach 😵 😵 🥱 🥱
        # überall Hektik 1,0, und der gelernte Anteil des Aufbaus (Montage 1,2, Story 0,7 …) war eingefroren
        s = None
    if s in STUFE_WERTE:   # „ruhig“ nie wilder als gelernt, „wild“ nie ruhiger – sonst wirkte der Tipp verkehrt herum
        hektik, staerke = STUFE_WERTE[s]
        wahl = min if s < STANDARD_STUFE else max if s > STANDARD_STUFE else (lambda _gelernt, regel: regel)
        gelernt = p.get("effekt_staerke") if isinstance(p.get("effekt_staerke"), dict) else {}
        p["effekt_hektik"] = wahl(float(p.get("effekt_hektik", 1.0)), hektik)
        p["effekt_staerke"] = {st: wahl(float(gelernt.get(st, 1.0)), staerke) for st in STIMMUNGEN}
    p["nur_starke"] = nur_starke(con, konfig)
    p["musik_rotation"] = MUSIK_ROTATION
    # Prüfung 08.10.: erst ganz am Ende – auch die Effekt-Stufe oben (und das 🥱-Tempo aus regie_lernen.aktuelle) kann
    # einen Versuch des Publikums-Modells überstimmt haben
    return _ohne_scheinversuch(p) if einfach else p


# --- Ein Grund unter ❌ → eine Regel ---------------------------------------------------------------------------

def _rund5(x: float) -> int:
    return int(5 * round(float(x) / 5))


def _momente(liste: dict) -> list[tuple[str, float]]:
    """(Moment, Stärke) je Szene des Videos, ohne Hook und ohne Wiederholungen, in Reihenfolge des Videos."""
    gesehen: dict[str, float] = {}
    for s in liste.get("segmente") or []:
        if not isinstance(s, dict) or s.get("rolle") == "hook" or not s.get("moment"):
            continue
        gesehen.setdefault(s["moment"], float(s.get("intensitaet") or 0.0))
    return list(gesehen.items())


def langweilig_teilung(liste: dict) -> tuple[list[str], list[str]]:
    """(behalten, ohne): die stärkere und die schwächere Hälfte der Szenen eines Videos (ohne die schwächsten
    max(1, n // 2)), jeweils in Reihenfolge des Videos. Beispiel: Stärken a 6, b 1, c 3, d 0,5 → (["a", "c"], ["b", "d"])."""
    momente = _momente(liste)
    ohne = {m for m, _ in sorted(momente, key=lambda x: (x[1], x[0]))[:max(1, len(momente) // 2)]} if momente else set()
    return [m for m, _ in momente if m not in ohne], [m for m, _ in momente if m in ohne]


def neue_fassung(liste: dict, entwurf_id: int) -> dict:
    """🥱 (07.10., Florian: „ich sehe das gleiche Video mit anderen Schnitten“): wovon sich die neue Fassung abheben muss.
    Szenen (regie.fassung_kandidaten): behalten/ohne aus langweilig_teilung, abend = die Matches des Videos – beim
    zweiten 🥱 derselbe Abend wie beim ersten (sonst wüchse er um die Ersatz-Szenen früherer Abende). Schnitt
    (geschmack.waehle): Reihenfolge, Tempo und Segmentfaktor, wie sie im Video WIRKSAM waren; Song (regie.waehle_musik)."""
    p = liste.get("parameter") or {}
    behalten, ohne = langweilig_teilung(liste)
    return {"anders_als": entwurf_id, "abend": sorted((p.get("fassung") or {}).get("abend") or abend_aus(liste)),
            "behalten": behalten, "ohne": ohne,
            "reihenfolge": p.get("reihenfolge") or (stile.STILE.get(p.get("stil")) or {}).get("reihenfolge") or "bogen",
            "tempo": (p.get("geschmack") or {}).get("tempo"), "seg_min_faktor": p.get("seg_min_faktor"),
            "track_id": (liste.get("musik") or {}).get("track_id")}


def _songs_frei(con: sqlite3.Connection) -> int:
    try:
        return int(con.execute("SELECT COUNT(*) FROM tracks WHERE beats IS NOT NULL AND CAST(id AS TEXT) NOT IN "
                               "(SELECT schluessel FROM sperren WHERE art = 'track')").fetchone()[0])
    except sqlite3.OperationalError:
        return 0


def mehr_effekte(con: sqlite3.Connection, konfig: Konfig) -> tuple[int, int] | None:
    """🥱 als Gegenpaar zu 😵 (08.10., Florian stellt nichts mehr von Hand ein): Hast du die Effekte gesenkt („ruhig“
    oder „aus“, auch mit dem alten Schalter „✨ Effekte aus“), holt 🥱 sie eine Stufe zurück Richtung „normal“ – nie
    darüber. Ohne ⚙️ blieben sie nach zweimal 😵 sonst für immer aus. Sind sie wieder an, lernt geschmack.py auch die
    Zeitlupe wieder mit (nur_wirksame). Rückgabe (vorher, nachher) oder None: nichts geändert (nie gesetzt – dann gilt
    das Gelernte und es kommt keine Zeile dazu –, „normal“ oder „wild“)."""
    alt = stufe(con, konfig)
    if alt is None or alt >= STANDARD_STUFE:
        return None
    einstellungen.setze(con, STUFE_SCHLUESSEL, alt + 1)
    return alt, alt + 1


def _satz_langweilig(con: sqlite3.Connection, konfig: Konfig, liste: dict, mehr: tuple[int, int] | None = None) -> str:
    """„🥱 Verstanden: Ich schneide es neu – anderer Aufbau, anderes Tempo, anderer Song. Die 2 besten Szenen bleiben,
    die 2 schwächeren tausche ich gegen andere – zuerst neue.“ – nur, was die neue Fassung auch wirklich anders macht.
    mehr (mehr_effekte): „… anderer Song und wieder etwas mehr Effekte: „ruhig“ statt „aus“.“ 08.10. (Abwechslung mit
    Ermüdung): „gegen andere“ statt „gegen neue“ – gibt es keine neuen, kommen bekannte starke, die länger nicht dran
    waren (regie.fassung_kandidaten)."""
    fest = str(konfig.wert("regie.stil", "auto") or "auto") in stile.STILE
    teile = ["gleicher Aufbau (fest eingestellt), anderes Tempo" if fest else "anderer Aufbau, anderes Tempo"]
    if _songs_frei(con) >= 2:
        teile.append("anderer Song")
    text = f"🥱 Verstanden: Ich schneide es neu – {', '.join(teile)}"
    if mehr:
        text += f" und wieder etwas mehr Effekte: „{STUFEN[mehr[1]]}“ statt „{STUFEN[mehr[0]]}“"
    text += "."
    behalten, ohne = langweilig_teilung(liste)
    if ohne and behalten:
        text += (" Die beste Szene bleibt" if len(behalten) == 1 else f" Die {len(behalten)} besten Szenen bleiben")
        text += (", die schwächere tausche ich gegen eine andere – am liebsten eine neue." if len(ohne) == 1
                 else f", die {len(ohne)} schwächeren tausche ich gegen andere – zuerst neue.")
    elif ohne:
        text += " Die Szene tausche ich gegen eine andere – am liebsten eine neue."
    return text


def _bisherige_grenze(con: sqlite3.Connection, konfig: Konfig, entwurf_id: int | None) -> tuple[int, str] | None:
    """Deine Längen-Grenze VOR diesem Tipp (Wert, Richtung) – None ohne Grenze oder unter /experte. Die Bewertung des
    Videos, an dem du gerade tippst, zählt nicht (der Lern-Bot speichert sie vor wende_an)."""
    ziel = ziel_regel(con, konfig)
    if not ziel or einstellungen.experte(con, konfig):
        return None
    from . import regie_lernen   # hier, nicht oben: regie_lernen lädt die ganze Regie

    try:
        richtung = regie_lernen.laengen_richtung(con, ohne=entwurf_id)
    except sqlite3.OperationalError:
        return None
    return (_rund5(max(ZIEL_GRENZEN[0], min(ZIEL_GRENZEN[1], ziel))), richtung) if richtung else None


def wende_an(con: sqlite3.Connection, konfig: Konfig, grund: str, liste: dict,
             entwurf_id: int | None = None) -> str | None:
    """Setzt die Regel zu einem Grund und gibt die Bestätigung für dich zurück (ein Satz, ohne Fachbegriffe).
    None = für diesen Grund gibt es keine feste Regel (er wird nur gelernt). Nur im einfachen Modus (lernbot._klick_einfach).
    Beispiel: „kurz“ an einem 45-s-Video → Einstellung 55 → „⏱️ Verstanden: Shorts sind ab jetzt mindestens 55 s lang
    (vorher 45 s).“ – „mindestens“/„höchstens“ seit Stufe 5 (08.10.): die Länge ist eine Grenze mit Richtung (laenge).
    entwurf_id (Prüfung 08.10.): das Video, an dem du tippst – ⏱️/⏳ verschieben deine Grenze nie gegen die Richtung,
    die du antippst: ⏱️ an einem älteren 40-s-Video bei „mindestens 55 s“ lässt 55 stehen (vorher: 50)."""
    if grund in ("kurz", "lang"):
        p = liste.get("parameter") or {}
        # Stufe 5: Schritt von dem Video, das du gesehen hast – ⏱️ von der längeren, ⏳ von der kürzeren Zahl aus Ziel
        # und echter Länge. Das Publikum kann es über deine Grenze hinaus verlängert haben (Grenze 55, Video 65), zu
        # wenig Material macht es kürzer als das Ziel; vom gespeicherten Wert aus hätte ⏱️ dann nichts geändert
        gesehen = [float(x) for x in (p.get("ziel_dauer_s"), liste.get("dauer_s")) if x]
        alt = _rund5((max if grund == "kurz" else min)(gesehen) if gesehen else (ziel_regel(con, konfig) or 45))
        neu = max(ZIEL_GRENZEN[0], min(ZIEL_GRENZEN[1], alt + (ZIEL_SCHRITT if grund == "kurz" else -ZIEL_SCHRITT)))
        bisher = _bisherige_grenze(con, konfig, entwurf_id)
        vorher = alt
        if bisher is not None and bisher[1] == grund:   # dieselbe Richtung: nie zurück
            neu = (max if grund == "kurz" else min)(neu, bisher[0])
            vorher = bisher[0]
        einstellungen.setze(con, ZIEL_SCHLUESSEL, float(neu))
        symbol = "⏱️" if grund == "kurz" else "⏳"
        if bisher is not None and bisher[1] == grund and neu == bisher[0] and neu != alt:
            return f"{symbol} Verstanden: Shorts sind schon {GRENZE_WORT[grund]} {neu} s lang."
        if neu == alt:
            return (f"⏱️ Länger als {ZIEL_GRENZEN[1]} s geht bei Shorts nicht – das Ziel bleibt {neu} s." if grund == "kurz"
                    else f"⏳ Kürzer als {ZIEL_GRENZEN[0]} s geht nicht – das Ziel bleibt {neu} s.")
        text = f"{symbol} Verstanden: Shorts sind ab jetzt {GRENZE_WORT[grund]} {neu} s lang (vorher {vorher} s)."
        dauer = float(liste.get("dauer_s") or 0)
        # Stufe 4 (08.10.): nur, wenn es wirklich keine starken Szenen früherer Abende mehr gab – sonst holt die neue
        # Fassung sie (regie.erstelle, Nachschub) und der Satz stimmte nicht. Prüfung 08.10.: auch nicht nach einer
        # 🥱-Fassung – deren schwächere Hälfte fehlte nur dort, die nächste Fassung nimmt sie wieder
        if grund == "kurz" and dauer and dauer < alt - 5 and not (liste.get("auswahl") or {}).get("nachschub_uebrig") \
                and not (p.get("fassung") or {}).get("ohne"):
            text += f" Dieses Video hatte nur {dauer:.0f} s – mehr starke Szenen gab es nicht."
        return text
    if grund == "langweilig":   # 07.10.: der Schnitt langweilt – keine Sperre; was anders wird, regelt neue_fassung
        return _satz_langweilig(con, konfig, liste, mehr_effekte(con, konfig))   # 08.10.: gesenkte Effekte zurück
    if grund == "musik":
        m = liste.get("musik") or {}
        if m.get("track_id") is None:
            return "🎵 Dieses Video hatte keine Musik."
        sperre(con, "track", [str(m["track_id"])], "musik")
        return f"🎵 Verstanden: „{m.get('titel') or 'diesen Song'}“ spiele ich nie wieder."
    if grund in ("hektisch", "effekte_viel", "action"):
        alt = stufe(con, konfig)
        alt = STANDARD_STUFE if alt is None else alt
        neu = min(3, alt + 1) if grund == "action" else max(0, alt - 1)
        einstellungen.setze(con, STUFE_SCHLUESSEL, neu)
        if neu == alt:
            return f"✨ Effekte sind schon auf „{STUFEN[neu]}“."
        return f"✨ Verstanden: Effekte ab jetzt „{STUFEN[neu]}“ (vorher „{STUFEN[alt]}“)."
    return None


def regeln_zeile(con: sqlite3.Connection, konfig: Konfig) -> str:
    """„Deine Regeln: Shorts mindestens 55 s · Effekte ruhig · nur starke Szenen · 3 Songs und 12 Szenen gesperrt“ –
    „mindestens“/„höchstens“ seit Stufe 5 (08.10.) im einfachen Modus; fest (/experte): „Shorts 55 s“."""
    regel = laenge(con, konfig)
    s = stufe(con, konfig)
    teile = [" ".join(filter(None, ("Shorts", GRENZE_WORT.get(regel[1]), f"{regel[0]:.0f} s"))) if regel
             else "Länge automatisch",
             f"Effekte {STUFEN[s]}" if s is not None else "Effekte normal",
             "nur starke Szenen" if nur_starke(con, konfig) else "auch Einzelkills"]
    songs, szenen = len(gesperrt(con, "track")), len(gesperrt(con, "moment"))
    if songs or szenen:
        teile.append(" und ".join(t for t in (f"{songs} Song{'s' if songs != 1 else ''}" if songs else "",
                                                f"{szenen} Szene{'n' if szenen != 1 else ''}" if szenen else "") if t)
                     + " gesperrt")
    return "📏 Deine Regeln: " + " · ".join(teile)


def liste_aus(zeile: sqlite3.Row) -> dict:
    """Schnittliste eines Entwurfs ({} wenn die Datei fehlt)."""
    try:
        with open(zeile["schnittliste"], encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError, TypeError):
        return {}


def matches_aus(liste: dict) -> set[str]:
    """Matches, aus denen ein Video seine Szenen hat – für die neue Fassung aus demselben Abend. Szenen früherer Abende
    (Stufe 4, auswahl.nachschub) zählen nicht: sonst wüchse der Abend der neuen Fassung um sie, und ihre Einzelkills
    kämen nach 🥱 als Ersatz „vom Abend“ dazu."""
    frueher = set((liste.get("auswahl") or {}).get("nachschub") or [])
    return {s["match_id"] for s in liste.get("segmente") or []
            if isinstance(s, dict) and s.get("match_id") and s.get("moment") not in frueher}


def abend_aus(liste: dict) -> set[str]:
    """Der Abend für die neue Fassung nach ❌: matches_aus – hat das Video keine Szene vom Abend (🎬 gemischt aus den
    letzten Tagen, 08.10.), der Abend, aus dem es gebaut wurde (auswahl.abend). Sonst fände die Fassung keine Szenen
    früherer Abende (die zählen relativ zum Abend). Nicht für sitzung._schon_video: ein gemischtes Video ohne Szene des
    Abends ist nicht sein Video."""
    return matches_aus(liste) or {m for m in (liste.get("auswahl") or {}).get("abend") or [] if isinstance(m, str)}
