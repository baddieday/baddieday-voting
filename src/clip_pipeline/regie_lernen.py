"""Lernen aus deinen Bewertungen der Entwürfe (👍/👎 + Gründe) – wirkt beim nächsten `compose`.

Wie beim Lernen der Vorbewertung wird alles bei jedem Aufruf komplett aus den gespeicherten Bewertungen neu
berechnet (deterministisch, jederzeit nachvollziehbar). Jede Regel verschiebt einen Parameter um einen kleinen,
begrenzten Schritt:

  zu hektisch         Segmente länger (+15 %), Übergänge länger (+10 %); ab +30 % nur jeden 2., ab +70 % jeden 4. Beat;
                      effekt_hektik ×0,9 (0,3 … 1,3): Beat-Akzente und Impacts (Blitz, Wackeln, RGB) schwächer
  zu lang             Ziel-Dauer −10 % (höchstens bis 60 %)
  zu kurz             Ziel-Dauer +11 % (÷0,9); Short: Start 45 s, immer 30–75 s (regie.dauer_grenzen)
  abgeschnitten       mehr Vorlauf (+0,5 s) und Nachlauf (+0,3 s) um die Kills
  Musik passt nicht   dieser Titel bekommt einen Abzug (−1 je Nennung)
  Stimmung getroffen  die Hauptstimmung bekommt Bonus (+0,5), und die Musik-Ziele dieser Stimmung rücken
                      20 % in Richtung des benutzten Titels (so lernt der Regisseur, welche Musik passt)
  👍 / 👎 allein      Hauptstimmung ±0,25 – erst ab `mindestens` Bewertungen
  Clips langweilig    jeder Moment dieses Entwurfs −1
  je Moment           👍: +0,5 für jeden Moment im Entwurf; 👎 ohne Grund: −0,5 (bei 👎 mit Grund lag es an
                      Musik/Tempo/Länge/Schnitt, nicht an den Clips); begrenzt auf ±3
  zu viele Effekte    Effekt-Stärke der Hauptstimmung ×0,85 (0,1 … 1,5); zusammen mit "mehr Action": nichts
  mehr Action         Effekt-Stärke der Hauptstimmung ×1,15; eine Vorgabe 0 (Effekte aus) bleibt 0
"""

from __future__ import annotations

import copy
import json
import logging
import sqlite3

from . import db, effekte
from .konfig import Konfig
from .musik import ZIEL
from .regie import FORMATE, PARAMETER, dauer_grenzen, format_regeln, momente_grenzen, ziel_dauer

log = logging.getLogger("pipeline")

# Neue Gründe immer hinten anhängen: gespeicherte Bewertungen nennen die Schlüssel
GRUENDE = {
    "musik": "🎵 Musik passt nicht",
    "hektisch": "😵 zu hektisch",
    "getroffen": "🎯 Stimmung getroffen",
    "lang": "⏳ zu lang",
    "abgeschnitten": "✂️ abgeschnitten",
    "langweilig": "🥱 Clips langweilig",
    "effekte_viel": "🎆 zu viele Effekte",
    "action": "💥 mehr Action",
    "kurz": "⏱️ zu kurz",       # 27.09.: Gegenstück zu „zu lang“ – vorher konnte dauer_faktor nur fallen
}


# Gründe, die den Inhalt betreffen (gelten für beide Formate); alle anderen sind Schnitt-Gründe je Format
INHALT_GRUENDE = {"musik", "getroffen", "langweilig"}


def _grenze(wert: float, unten: float, oben: float) -> float:
    return round(max(unten, min(oben, wert)), 3)


def bewertungen(con: sqlite3.Connection, *, mit_ki: bool = True) -> list[sqlite3.Row]:
    """Deine Bewertungen (quelle "du") und – mit_ki – die Urteile des KI-Cutters (kritik.py, quelle "ki", 30.09.):
    Sie wirken mit denselben Regeln wie deine Knöpfe, so lernt der Regisseur auch ohne dein 👍/👎. Ein Entwurf, den
    du selbst bewertet hast, zählt nur mit deinem Urteil (du hast Vorrang)."""
    felder = """e.schnittliste, e.track_id, e.format, t.bpm AS track_bpm, t.energie AS track_energie"""
    sql = f"""SELECT b.entwurf_id AS entwurf_id, b.daumen AS daumen, b.gruende AS gruende, b.erstellt AS erstellt,
                     b.geaendert AS geaendert, 'du' AS quelle, {felder}
                FROM entwurf_bewertungen b JOIN entwuerfe e ON e.id = b.entwurf_id
                LEFT JOIN tracks t ON t.id = e.track_id"""
    if mit_ki:
        sql += f"""
              UNION ALL
              SELECT k.entwurf_id, k.daumen, k.gruende, k.erstellt, k.erstellt, 'ki', {felder}
                FROM kritiken k JOIN entwuerfe e ON e.id = k.entwurf_id
                LEFT JOIN tracks t ON t.id = e.track_id
               WHERE k.daumen IS NOT NULL AND e.variante IS NULL
                 AND NOT EXISTS (SELECT 1 FROM entwurf_bewertungen b WHERE b.entwurf_id = k.entwurf_id)"""
    return con.execute(sql + " ORDER BY erstellt, entwurf_id").fetchall()


def _liste(zeile: sqlite3.Row) -> dict:
    try:
        with open(zeile["schnittliste"], encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}



# Deine Vorgaben ([regie.vorgaben] in config/lokal.toml): erlaubte Schlüssel und ihre Grenzen
VORGABE_GRENZEN = {
    "puffer_vor_s": (1.0, 6.0), "puffer_nach_s": (0.5, 4.0), "seg_min_faktor": (0.5, 2.0),
    "dauer_faktor": (0.6, 2.0), "uebergang_faktor": (0.5, 2.0), "musik_pegel": (0.0, 1.0),
    "max_je_match": (1, 10), "beats_pro_schnitt": (1, 4), "abwechslung": (0.0, 1.0),
    "effekt_hektik": (0.3, 1.3),
    "cooldown_entwuerfe": (0, 12), "frische_quote": (0.0, 1.0),   # 27.09.: Abwechslung, siehe regie.PARAMETER
}
EFFEKT_STAERKE_GRENZEN = (0.0, 1.5)    # Vorgabe je Stimmung; 0 = diese Stimmung ohne Effekte
EFFEKT_STAERKE_GELERNT = (0.1, 1.5)    # durch Bewertungen


def vorgaben(konfig: Konfig) -> tuple[dict, dict, list[str]]:
    """Startwerte aus der Konfiguration: (Parameter, Musik-Ziele, Hinweise zu ignorierten Einträgen).

    Beispiel in config/lokal.toml:
        [regie.vorgaben]
        seg_min_faktor = 1.3          # grundsätzlich ruhiger schneiden
        dauer_faktor = 0.8            # kürzer (Short: 45 s × 0,8 ≈ 36 s)
        [regie.vorgaben.stimmung_bonus]
        lustig = 1.0                  # lustige Momente bevorzugen
        [regie.vorgaben.effekt_staerke]
        chill = 0.5                   # chillige Momente mit halb so starken Effekten (0 = ohne)
        [regie.musik_ziele.episch]
        bpm = 150                     # für episch schnellere Musik
    Von hier aus lernt der Regisseur mit deinen Bewertungen weiter."""
    p, ziel, hinweise = copy.deepcopy(PARAMETER), copy.deepcopy(ZIEL), []
    for name, wert in (konfig.wert("regie.vorgaben", {}) or {}).items():
        if name == "stimmung_bonus" and isinstance(wert, dict):
            for stimmung, bonus in wert.items():
                if stimmung in ZIEL and isinstance(bonus, (int, float)):
                    p["stimmung_bonus"][stimmung] = _grenze(float(bonus), -2.0, 2.0)
                else:
                    hinweise.append(f"regie.vorgaben.stimmung_bonus.{stimmung} ignoriert")
        elif name == "effekt_staerke" and isinstance(wert, dict):
            for stimmung, faktor in wert.items():
                if stimmung in ZIEL and isinstance(faktor, (int, float)) and not isinstance(faktor, bool):
                    p["effekt_staerke"][stimmung] = _grenze(float(faktor), *EFFEKT_STAERKE_GRENZEN)
                else:
                    hinweise.append(f"regie.vorgaben.effekt_staerke.{stimmung} ignoriert")
        elif name in VORGABE_GRENZEN and isinstance(wert, (int, float)) and not isinstance(wert, bool):
            unten, oben = VORGABE_GRENZEN[name]
            p[name] = int(_grenze(wert, unten, oben)) if isinstance(PARAMETER[name], int) else _grenze(float(wert), unten, oben)
        else:
            hinweise.append(f"regie.vorgaben.{name} ignoriert (unbekannt oder keine Zahl)")
    for stimmung, werte in (konfig.wert("regie.musik_ziele", {}) or {}).items():
        if stimmung not in ZIEL or not isinstance(werte, dict):
            hinweise.append(f"regie.musik_ziele.{stimmung} ignoriert")
            continue
        if isinstance(werte.get("energie"), (int, float)):
            ziel[stimmung]["energie"] = _grenze(float(werte["energie"]), 0.0, 1.0)
        if isinstance(werte.get("bpm"), (int, float)):
            ziel[stimmung]["bpm"] = _grenze(float(werte["bpm"]), 60.0, 200.0)
    return p, ziel, hinweise


def aktuelle(con: sqlite3.Connection, konfig: Konfig, fmt: str | None = None) -> tuple[dict, dict]:
    """(Regie-Parameter, Musik-Ziele je Stimmung): deine Vorgaben, dann alle bisherigen Bewertungen.

    fmt (27.09.): Schnitt-Werte (Dauer, Segmente, Übergänge, Anlauf, Effekte) lernen nur aus Bewertungen dieses
    Formats – ein „⏳ zu lang“ auf einen Zusammenschnitt kürzte vorher auch die Shorts. Was du inhaltlich magst
    (Momente, Stimmungen, Musik), gilt für beide. fmt None: alle Bewertungen wie bisher."""
    energien = sorted(float(z["energie"] or 0) for z in con.execute("SELECT energie FROM tracks"))
    p, ziel = _falte(bewertungen(con), konfig, energien, fmt)
    if fmt is not None:
        from . import autonom, stile

        # Schnittstil (30.09.) zuerst: relativ auf das Gelernte; das Publikumsmodell darf danach nachsteuern
        p = stile.anwenden(con, konfig, fmt, p)
        p, ziel = autonom.plan_parameter(con, konfig, fmt, p, ziel)
        grenzen = format_regeln(konfig, fmt)[0]
        # ⚙️ Short-Länge (06.10.): deine Untergrenze – nichts Gelerntes darf darunter
        mindestens = float(konfig.wert("regie.short_mindestens_s", 0.0) or 0.0) if fmt == "short" else 0.0
        if mindestens > 0:
            p["ziel_dauer_s"] = max(float(p.get("ziel_dauer_s") or 0.0), mindestens)
        if "ziel_dauer_s" in p:
            p["ziel_dauer_s"] = ziel_dauer(grenzen, 1.0, ziel_s=p["ziel_dauer_s"])
    return p, ziel


# Dein Einfluss (05.10., Florian: „mein persönlicher Impact wird zu wenig gewertet“): Ein Urteil des KI-Cutters wirkt
# nur mit diesem Anteil eines Schritts von dir – sonst überstimmt er dich allein durch die Menge (bis 20 am Tag).
KI_STAERKE = 0.34
GEGENSTUECKE = {"lang": "kurz", "kurz": "lang", "effekte_viel": "action", "action": "effekte_viel"}


def _quelle(zeile) -> str:
    try:
        return str(zeile["quelle"] or "du")
    except (KeyError, IndexError):   # Zeilen ohne Spalte (alte Aufrufer, Tests): deine
        return "du"


def _deine_richtung(zeilen: list) -> set[str]:
    """Deine letzte ausdrückliche Ansage je Gegensatzpaar („kurz“ oder „lang“, „effekte_viel“ oder „action“) – ein
    KI-Grund, der ihr widerspricht, wirkt nicht (dein Wort gilt, bis du es selbst änderst)."""
    richtung: dict[frozenset, str] = {}
    for z in zeilen:  # zeilen sind nach Zeit sortiert – die letzte Ansage gewinnt
        if _quelle(z) != "du":
            continue
        gruende = set(json.loads(z["gruende"] or "[]"))
        for g, gegen in GEGENSTUECKE.items():
            if g in gruende and gegen not in gruende:
                richtung[frozenset((g, gegen))] = g
    return set(richtung.values())


def _falte(zeilen: list, konfig: Konfig, energien: list[float], fmt: str | None) -> tuple[dict, dict]:
    p, ziel, _ = vorgaben(konfig)
    deine = _deine_richtung(zeilen)
    beats_vorgabe = int(p["beats_pro_schnitt"])
    # Längen-Stimmen wirken bis an die Grenzen des Formats (Short 30–75 s), nicht darüber hinaus (28.09.)
    dauer_unten, dauer_oben = dauer_grenzen(format_regeln(konfig, fmt)[0]) if fmt else VORGABE_GRENZEN["dauer_faktor"]
    mindestens = int(konfig.wert("regie.lernen_ab", 3))
    for n, b in enumerate(zeilen, 1):
        gruende = set(json.loads(b["gruende"] or "[]"))
        w = 1.0
        if _quelle(b) != "du":
            w = KI_STAERKE
            gruende = {g for g in gruende if GEGENSTUECKE.get(g) not in deine}
        liste = _liste(b)
        haupt = liste.get("stimmung")
        # je Moment: was du magst, kommt öfter; was dich langweilt, seltener
        schritt = -1.0 if "langweilig" in gruende else 0.5 if b["daumen"] > 0 else -0.5 if not gruende else 0.0
        for m in {s.get("moment") for s in liste.get("segmente", []) if isinstance(s, dict)} - {None}:
            if schritt:
                p["moment_bonus"][m] = _grenze(p["moment_bonus"].get(m, 0.0) + schritt * w, -3.0, 3.0)
        if fmt is not None and b["format"] != fmt:
            gruende = gruende & INHALT_GRUENDE  # Schnitt-Gründe des anderen Formats wirken hier nicht
        if "hektisch" in gruende:
            p["seg_min_faktor"] = _grenze(p["seg_min_faktor"] * 1.15 ** w, 0.5, 2.0)
            p["uebergang_faktor"] = _grenze(p["uebergang_faktor"] * 1.10 ** w, 0.5, 2.0)
            p["effekt_hektik"] = _grenze(p["effekt_hektik"] * 0.9 ** w, *VORGABE_GRENZEN["effekt_hektik"])
        # Effekt-Stärke der Hauptstimmung: beide Gründe zugleich heben sich auf
        weniger, mehr = "effekte_viel" in gruende, "action" in gruende
        if haupt in ZIEL and weniger != mehr:
            alt = p["effekt_staerke"].get(haupt, 1.0)
            unten, oben = EFFEKT_STAERKE_GELERNT
            if alt > 0:  # eine Vorgabe 0 (diese Stimmung ohne Effekte) bleibt 0; eine unter 0,1 steigt nicht durch 🎆
                p["effekt_staerke"][haupt] = _grenze(alt * (0.85 if weniger else 1.15) ** w, min(unten, alt), oben)
        # „zu lang“ ×0,9 / „zu kurz“ ÷0,9 (27.09.: vorher gab es kein Gegenstück – der Faktor konnte nur fallen und
        # blieb für immer unten, auch eine Vorgabe in lokal.toml ist nur der Startwert); beide zugleich: nichts
        kuerzer, laenger = "lang" in gruende, "kurz" in gruende
        if kuerzer != laenger:
            # 28.09.: Grenzen aus dem Format (Short 0,667 … 1,667 = 30–75 s) statt fest 0,6 … 1,0 – vorher war bei
            # 45 s Schluss, und jedes weitere „zu kurz“ verpuffte ohne Wirkung
            p["dauer_faktor"] = _grenze(p["dauer_faktor"] * (0.9 if kuerzer else 1 / 0.9) ** w, dauer_unten, dauer_oben)
        if "abgeschnitten" in gruende:
            p["puffer_vor_s"] = _grenze(p["puffer_vor_s"] + 0.5 * w, 1.0, 6.0)
            p["puffer_nach_s"] = _grenze(p["puffer_nach_s"] + 0.3 * w, 0.5, 4.0)
        if "musik" in gruende and b["track_id"] is not None:
            schluessel = str(b["track_id"])
            p["track_malus"][schluessel] = _grenze(p["track_malus"].get(schluessel, 0.0) + 1.0 * w, 0.0, 5.0)
        if haupt:
            bonus = p["stimmung_bonus"].get(haupt, 0.0)
            if "getroffen" in gruende:
                bonus += 0.5 * w
                if b["track_bpm"] is not None and energien:
                    rang = sum(1 for e in energien if e < float(b["track_energie"] or 0)) / max(1, len(energien) - 1)
                    ziel[haupt]["energie"] = round(ziel[haupt]["energie"] + 0.2 * (rang - ziel[haupt]["energie"]), 3)
                    ziel[haupt]["bpm"] = round(ziel[haupt]["bpm"] + 0.2 * (float(b["track_bpm"]) - ziel[haupt]["bpm"]), 1)
            elif n >= mindestens:
                bonus += 0.25 * int(b["daumen"]) * w
            p["stimmung_bonus"][haupt] = _grenze(bonus, -2.0, 2.0)
    gelernt = 4 if p["seg_min_faktor"] >= 1.7 else 2 if p["seg_min_faktor"] >= 1.3 else 1
    p["beats_pro_schnitt"] = max(gelernt, beats_vorgabe)  # deine Vorgabe ist die Untergrenze
    return p, ziel


def bewerte(con: sqlite3.Connection, entwurf_id: int, *, daumen: int | None = None, grund: str | None = None) -> sqlite3.Row:
    """Speichert Daumen (setzt ihn) bzw. schaltet einen Grund um. Gibt die aktuelle Bewertung zurück."""
    from .zeit import iso, jetzt

    if daumen not in (None, 1, -1):
        raise ValueError("Daumen muss 1 oder -1 sein")
    if grund is not None and grund not in GRUENDE:
        raise ValueError(f"Unbekannter Grund {grund!r}")
    zeit = iso(jetzt())
    # Eine Transaktion (27.09.): ein Schreibvorgang auf die Platte statt zwei – im Lern-Bot läuft das im Event-Loop,
    # und unter Last (Rendern, Whisper) kostete jeder einzelne spürbar Zeit. Lesen und Umschalten gehören dazu.
    with db.transaktion(con):
        alt = con.execute("SELECT * FROM entwurf_bewertungen WHERE entwurf_id = ?", (entwurf_id,)).fetchone()
        gruende = json.loads(alt["gruende"]) if alt else []
        if grund:
            gruende = [g for g in gruende if g != grund] if grund in gruende else [*gruende, grund]
        # Ohne Daumen: "Stimmung getroffen" ist Lob, die anderen Gründe sind Kritik
        neuer_daumen = daumen if daumen is not None else (alt["daumen"] if alt else 1 if grund == "getroffen" else -1)
        con.execute(
            """INSERT INTO entwurf_bewertungen (entwurf_id, daumen, gruende, erstellt, geaendert) VALUES (?, ?, ?, ?, ?)
               ON CONFLICT (entwurf_id) DO UPDATE SET daumen = excluded.daumen, gruende = excluded.gruende,
                   geaendert = excluded.geaendert""",
            (entwurf_id, neuer_daumen, json.dumps(gruende), zeit, zeit),
        )
        con.execute("UPDATE entwuerfe SET status = 'bewertet' WHERE id = ?", (entwurf_id,))
    return con.execute("SELECT * FROM entwurf_bewertungen WHERE entwurf_id = ?", (entwurf_id,)).fetchone()


def _prozent(x: float) -> str:
    return f"{x:.0%}"


# (Parameter, Text aus altem und neuem Wert) – Reihenfolge = Reihenfolge in der Bildunterschrift
WIRKUNG_TEXTE = [
    ("dauer_faktor", lambda a, b: f"Ziel-Dauer {_prozent(a)} → {_prozent(b)}"),
    ("seg_min_faktor", lambda a, b: f"Schnitt {'ruhiger' if b > a else 'schneller'} (Segmente ×{b:.2f})"),
    ("beats_pro_schnitt", lambda a, b: f"Schnitt auf jeden {b}. Beat" if b > 1 else "Schnitt auf jeden Beat"),
    ("puffer_vor_s", lambda a, b: f"Anlauf {a:.1f} → {b:.1f} s"),
    ("puffer_nach_s", lambda a, b: f"Ausklang {a:.1f} → {b:.1f} s"),
    ("uebergang_faktor", lambda a, b: f"Übergänge {'weicher' if b > a else 'härter'}"),
    ("effekt_hektik", lambda a, b: f"Beat-Akzente {'stärker' if b > a else 'schwächer'}"),
]


def _unterschiede(vorher: dict, nachher: dict) -> list[str]:
    """Was sich zwischen zwei Parameter-Ständen geändert hat, in kurzen Sätzen (für die Bildunterschrift)."""
    teile = [text(vorher[k], nachher[k]) for k, text in WIRKUNG_TEXTE if vorher[k] != nachher[k]]
    for s in sorted(set(vorher["effekt_staerke"]) | set(nachher["effekt_staerke"])):
        a, b = vorher["effekt_staerke"].get(s, 1.0), nachher["effekt_staerke"].get(s, 1.0)
        if a != b:
            teile.append(f"Effekte {s} {'stärker' if b > a else 'schwächer'} ({b:.2f})")
    for s in sorted(set(vorher["stimmung_bonus"]) | set(nachher["stimmung_bonus"])):
        a, b = vorher["stimmung_bonus"].get(s, 0.0), nachher["stimmung_bonus"].get(s, 0.0)
        if a != b:
            teile.append(f"{s} {'öfter' if b > a else 'seltener'}")
    for t in sorted(set(nachher["track_malus"]) - set(vorher["track_malus"])
                    | {k for k in vorher["track_malus"] if nachher["track_malus"].get(k) != vorher["track_malus"][k]}):
        teile.append(f"Musik #{t} seltener")
    hoch = sum(1 for m, v in nachher["moment_bonus"].items() if v > vorher["moment_bonus"].get(m, 0.0))
    runter = sum(1 for m, v in nachher["moment_bonus"].items() if v < vorher["moment_bonus"].get(m, 0.0))
    if hoch:
        teile.append(f"{hoch} Momente kommen öfter")
    if runter:
        teile.append(f"{runter} Momente seltener")
    return teile


def wirkung(con: sqlite3.Connection, konfig: Konfig, fmt: str) -> dict | None:
    """Was deine zuletzt angefasste Bewertung am nächsten Entwurf dieses Formats geändert hat (27.09., „ich bewerte
    gefühlt ins Leere“): Parameter mit und ohne diese Bewertung, als kurze Sätze. None ohne Bewertungen.
    Beispiel: {"entwurf": 41, "format": "short", "aenderungen": ["Ziel-Dauer 81% → 90%", "6 Momente kommen öfter"]}."""
    zeilen = bewertungen(con)
    deine = [z for z in zeilen if z["quelle"] == "du"]   # KI-Urteile (30.09.) sind nie „deine letzte Bewertung“
    if not deine:
        return None
    letzte = max(deine, key=lambda z: (z["geaendert"] or z["erstellt"] or "", z["entwurf_id"]))
    energien = sorted(float(z["energie"] or 0) for z in con.execute("SELECT energie FROM tracks"))
    ohne = [z for z in zeilen if z["entwurf_id"] != letzte["entwurf_id"]]
    vorher, _ = _falte(ohne, konfig, energien, fmt)
    nachher, _ = _falte(zeilen, konfig, energien, fmt)
    return {"entwurf": int(letzte["entwurf_id"]), "format": letzte["format"],
            "aenderungen": _unterschiede(vorher, nachher)}


def dauer_zeile(zeilen: list, dauer_faktor: float, fmt: dict, *, ziel_s: float | None = None) -> str:
    """Auswertung deiner Längen-Stimmen für Shorts (28.09.): wie oft „zu kurz“/„zu lang“, wie oft beides zugleich
    (hebt sich auf) und welche Ziel-Dauer daraus folgt."""
    gruende_je = [set(json.loads(z["gruende"] or "[]")) for z in zeilen if z["format"] == "short"]
    kurz = sum(1 for g in gruende_je if "kurz" in g and "lang" not in g)
    lang = sum(1 for g in gruende_je if "lang" in g and "kurz" not in g)
    beide = sum(1 for g in gruende_je if "kurz" in g and "lang" in g)
    jetzt = ziel_dauer(fmt, dauer_faktor, ziel_s=ziel_s)
    text = (f"Short-Länge: {len(gruende_je)} Short-Bewertungen, {kurz}× „⏱️ zu kurz“, {lang}× „⏳ zu lang“"
            + (f", {beide}× beides (hebt sich auf)" if beide else "")
            + f" → Ziel {jetzt:.0f} s (Start {fmt['ziel_s']:.0f} s, erlaubt {fmt['min_s']:.0f}–{fmt['max_s']:.0f} s)")
    if jetzt >= fmt["max_s"] - 1e-6:
        text += " – Höchstwert"
    elif jetzt <= fmt["min_s"] + 1e-6:
        text += " – Mindestwert"
    return text


def einfluss_zeile(n_du: int, n_ki: int, historischer_anteil: float | None) -> str:
    """„👤 Dein Einfluss: Regie 75 % (KI-Cutter 25 %) · Publikums-Modell: dein Geschmack 60 %, Publikum 40 %“ (05.10.).
    historischer_anteil None = noch kein Publikums-Modell (dann zählt dort nur dein Geschmack)."""
    gesamt = n_du + KI_STAERKE * n_ki
    du = 100.0 * n_du / gesamt if gesamt else 100.0
    teile = [f"👤 Dein Einfluss: Regie {du:.0f} % (KI-Cutter {100 - du:.0f} %, deine Ansagen zu Länge/Effekten gelten vor)"]
    if historischer_anteil is None:
        teile.append("Publikums-Modell: noch keine Zahlen – dein Geschmack gilt")
    else:
        h = max(0.0, min(1.0, float(historischer_anteil)))
        teile.append(f"Publikums-Modell: dein Geschmack {100 * h:.0f} %, Publikum {100 * (1 - h):.0f} %")
    return " · ".join(teile)


def lernstand_text(con: sqlite3.Connection, konfig: Konfig) -> str:
    p, ziel = aktuelle(con, konfig, "short")
    start, start_ziel, hinweise = vorgaben(konfig)
    zeilen = bewertungen(con, mit_ki=False)
    daumen = sum(1 for z in zeilen if z["daumen"] > 0)
    ki = [z for z in bewertungen(con) if z["quelle"] == "ki"]
    teile = [f"🧠 Regie – {len(zeilen)} Bewertungen von dir ({daumen} 👍 / {len(zeilen) - daumen} 👎) · "
             f"{len(ki)} vom KI-Cutter ({sum(1 for z in ki if z['daumen'] > 0)} 👍)",
             "Schnitt-Werte lernen je Format (unten: Short); Momente, Stimmung und Musik gelten für beide",
             einfluss_zeile(len(zeilen), len(ki), (p.get("autonom") or {}).get("historischer_anteil"))]
    from . import stile

    teile.append(stile.stil_zeile(con, konfig=konfig))
    try:  # 🔥 Viral (05.10.): welche Mischung der Bot gerade bevorzugt – nur Anzeige
        from . import viral

        teile.append(viral.varianten_zeile(con, konfig))
    except Exception:  # noqa: BLE001 – eine kaputte Anzeige kostet nie den Lernstand
        log.exception("Viral-Varianten im Lernstand")
    try:  # Cutter-Maßstab 1.0 (Spec §5.5): was er an den Kriterien-Gewichten gelernt hat – nur Anzeige
        from . import massstab

        teile.extend(massstab.lernstand_zeilen(con, konfig))
    except Exception:  # noqa: BLE001 – eine kaputte Anzeige kostet nie den Lernstand
        log.exception("Cutter-Maßstab im Lernstand")
    for name in ("puffer_vor_s", "puffer_nach_s", "seg_min_faktor", "beats_pro_schnitt", "dauer_faktor", "uebergang_faktor"):
        s0, jetzt_ = start[name], p[name]
        herkunft = "" if s0 == PARAMETER[name] else ", deine Vorgabe"
        teile.append(f"{name}: {jetzt_}" + ("" if jetzt_ == s0 else f" (Start {s0}{herkunft})")
                     + (" (deine Vorgabe)" if jetzt_ == s0 and herkunft else ""))
    for h in hinweise:
        teile.append(f"⚠️ {h}")
    if p["stimmung_bonus"]:
        teile.append("Stimmungs-Bonus: " + ", ".join(f"{k} {v:+}" for k, v in sorted(p["stimmung_bonus"].items())))
    if p["track_malus"]:
        teile.append("Musik-Abzug: " + ", ".join(f"#{k} −{v}" for k, v in sorted(p["track_malus"].items())))
    gemocht = sum(1 for v in p["moment_bonus"].values() if v > 0)
    abgelehnt = sum(1 for v in p["moment_bonus"].values() if v < 0)
    if gemocht or abgelehnt:
        teile.append(f"Momente: {gemocht} gemocht, {abgelehnt} weniger gern gesehen")
    teile.append(f"Abwechslung: Momente aus dem letzten Entwurf verlieren {p['abwechslung']:.0%} ihrer Punkte, "
                 "je älterem Entwurf die Hälfte" + ("" if start["abwechslung"] == PARAMETER["abwechslung"]
                                                     else " (deine Vorgabe)"))
    if p["abwechslung"] > 0:
        teile.append(f"Cooldown: Momente aus den letzten {int(p['cooldown_entwuerfe'])} Entwürfen sind gesperrt · "
                     f"Frische-Quote: mind. {p['frische_quote']:.0%} der Momente eines Entwurfs waren noch in keinem"
                     + ("" if (start["cooldown_entwuerfe"], start["frische_quote"])
                        == (PARAMETER["cooldown_entwuerfe"], PARAMETER["frische_quote"]) else " (deine Vorgabe)"))
    else:
        teile.append("Abwechslung aus (0): immer die besten Momente, kein Cooldown, keine Frische-Quote")
    zs, _ = aktuelle(con, konfig, "zusammenschnitt")
    n_zs = sum(1 for z in zeilen if z["format"] == "zusammenschnitt")
    teile.append(f"Zusammenschnitt ({n_zs} Bewertungen): dauer_faktor {zs['dauer_faktor']}, "
                 f"seg_min_faktor {zs['seg_min_faktor']}, puffer_vor_s {zs['puffer_vor_s']}, "
                 f"uebergang_faktor {zs['uebergang_faktor']}")
    teile.append(dauer_zeile(zeilen, p["dauer_faktor"], format_regeln(konfig, "short")[0],
                            ziel_s=p.get("ziel_dauer_s")))
    for fmt_name, parameter in (("short", p), ("zusammenschnitt", zs)):
        fmt, fmt_hinweise = format_regeln(konfig, fmt_name)
        standard = fmt == format_regeln(None, fmt_name)[0]
        min_m, max_m = momente_grenzen(fmt)
        dauer = ziel_dauer(fmt, parameter["dauer_faktor"], ziel_s=parameter.get("ziel_dauer_s"))
        teile.append(f"{fmt_name}: {fmt['min_s']:.0f}–{fmt['max_s']:.0f} s, Ziel jetzt {dauer:.0f} s"
                     + (f", {min_m}–{max_m} Momente" if max_m < 10 ** 6 else "")
                     + f", Segment bis {fmt['seg_max_s']:.0f} s, Serie bis {fmt['serie_max_s']:.0f} s"
                     + ("" if standard else " (deine Vorgabe [regie.formate])"))
        teile += [f"⚠️ {h}" for h in fmt_hinweise]
    geaendert = [s for s in ziel if ziel[s] != ZIEL[s]]  # durch Vorgabe oder "Stimmung getroffen"
    for s in geaendert:
        teile.append(f"Musik für {s}: Energie-Rang {ziel[s]['energie']}, {ziel[s]['bpm']} BPM")
    teile.append(_effekte_zeile(p, start, bool(effekte.einstellungen(konfig)[0]["an"])))
    return "\n".join(teile)


def _effekte_zeile(p: dict, start: dict, an: bool) -> str:
    """„Effekte: episch 0.85 · … · Hektik 0.9“ – Start ist 1.0 (fehlt eine Stimmung, gilt 1.0); Vorgaben
    gekennzeichnet: „chill 0.5 (deine Vorgabe)“ bzw. gelernt „chill 0.575 (Vorgabe 0.5)“."""
    def wert(name: str, jetzt_: float, s0: float) -> str:
        if s0 == 1.0:
            return f"{name} {jetzt_}"
        return f"{name} {jetzt_} " + ("(deine Vorgabe)" if jetzt_ == s0 else f"(Vorgabe {s0})")

    werte = [wert(s, p["effekt_staerke"].get(s, 1.0), start["effekt_staerke"].get(s, 1.0)) for s in ZIEL]
    werte.append(wert("Hektik", p["effekt_hektik"], start["effekt_hektik"]))
    return "Effekte: " + " · ".join(werte) + ("" if an else " – ausgeschaltet ([regie.effekte] an = false)")
