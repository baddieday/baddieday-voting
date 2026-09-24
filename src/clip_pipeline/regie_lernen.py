"""Lernen aus deinen Bewertungen der Entwürfe (👍/👎 + Gründe) – wirkt beim nächsten `compose`.

Wie beim Lernen der Vorbewertung wird alles bei jedem Aufruf komplett aus den gespeicherten Bewertungen neu
berechnet (deterministisch, jederzeit nachvollziehbar). Jede Regel verschiebt einen Parameter um einen kleinen,
begrenzten Schritt:

  zu hektisch         Segmente länger (+15 %), Übergänge länger (+10 %); ab +30 % nur jeden 2., ab +70 % jeden 4. Beat
  zu lang             Ziel-Dauer −10 % (höchstens bis 60 %)
  abgeschnitten       mehr Vorlauf (+0,5 s) und Nachlauf (+0,3 s) um die Kills
  Musik passt nicht   dieser Titel bekommt einen Abzug (−1 je Nennung)
  Stimmung getroffen  die Hauptstimmung bekommt Bonus (+0,5), und die Musik-Ziele dieser Stimmung rücken
                      20 % in Richtung des benutzten Titels (so lernt der Regisseur, welche Musik passt)
  👍 / 👎 allein      Hauptstimmung ±0,25 – erst ab `mindestens` Bewertungen
"""

from __future__ import annotations

import copy
import json
import sqlite3

from .konfig import Konfig
from .musik import ZIEL
from .regie import PARAMETER

GRUENDE = {
    "musik": "🎵 Musik passt nicht",
    "hektisch": "😵 zu hektisch",
    "getroffen": "🎯 Stimmung getroffen",
    "lang": "⏳ zu lang",
    "abgeschnitten": "✂️ abgeschnitten",
}


def _grenze(wert: float, unten: float, oben: float) -> float:
    return round(max(unten, min(oben, wert)), 3)


def bewertungen(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return con.execute(
        """SELECT b.*, e.schnittliste, e.track_id, e.format, t.bpm AS track_bpm, t.energie AS track_energie
             FROM entwurf_bewertungen b JOIN entwuerfe e ON e.id = b.entwurf_id
             LEFT JOIN tracks t ON t.id = e.track_id
            ORDER BY b.erstellt, b.entwurf_id"""
    ).fetchall()


def _hauptstimmung(zeile: sqlite3.Row) -> str | None:
    try:
        with open(zeile["schnittliste"], encoding="utf-8") as f:
            return json.load(f).get("stimmung")
    except (OSError, json.JSONDecodeError):
        return None


# Deine Vorgaben ([regie.vorgaben] in config/lokal.toml): erlaubte Schlüssel und ihre Grenzen
VORGABE_GRENZEN = {
    "puffer_vor_s": (1.0, 6.0), "puffer_nach_s": (0.5, 4.0), "seg_min_faktor": (0.5, 2.0),
    "dauer_faktor": (0.6, 1.0), "uebergang_faktor": (0.5, 2.0), "musik_pegel": (0.0, 1.0),
    "max_je_match": (1, 10), "beats_pro_schnitt": (1, 4),
}


def vorgaben(konfig: Konfig) -> tuple[dict, dict, list[str]]:
    """Startwerte aus der Konfiguration: (Parameter, Musik-Ziele, Hinweise zu ignorierten Einträgen).

    Beispiel in config/lokal.toml:
        [regie.vorgaben]
        seg_min_faktor = 1.3          # grundsätzlich ruhiger schneiden
        dauer_faktor = 0.8            # kürzer (Short: 45 s × 0,8 ≈ 36 s)
        [regie.vorgaben.stimmung_bonus]
        lustig = 1.0                  # lustige Momente bevorzugen
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


def aktuelle(con: sqlite3.Connection, konfig: Konfig) -> tuple[dict, dict]:
    """(Regie-Parameter, Musik-Ziele je Stimmung): deine Vorgaben, dann alle bisherigen Bewertungen."""
    p, ziel, _ = vorgaben(konfig)
    beats_vorgabe = int(p["beats_pro_schnitt"])
    zeilen = bewertungen(con)
    mindestens = int(konfig.wert("regie.lernen_ab", 3))
    energien = sorted(float(z["energie"] or 0) for z in con.execute("SELECT energie FROM tracks"))
    for n, b in enumerate(zeilen, 1):
        gruende = set(json.loads(b["gruende"] or "[]"))
        haupt = _hauptstimmung(b)
        if "hektisch" in gruende:
            p["seg_min_faktor"] = _grenze(p["seg_min_faktor"] * 1.15, 0.5, 2.0)
            p["uebergang_faktor"] = _grenze(p["uebergang_faktor"] * 1.10, 0.5, 2.0)
        if "lang" in gruende:
            p["dauer_faktor"] = _grenze(p["dauer_faktor"] * 0.9, 0.6, 1.0)
        if "abgeschnitten" in gruende:
            p["puffer_vor_s"] = _grenze(p["puffer_vor_s"] + 0.5, 1.0, 6.0)
            p["puffer_nach_s"] = _grenze(p["puffer_nach_s"] + 0.3, 0.5, 4.0)
        if "musik" in gruende and b["track_id"] is not None:
            schluessel = str(b["track_id"])
            p["track_malus"][schluessel] = _grenze(p["track_malus"].get(schluessel, 0.0) + 1.0, 0.0, 5.0)
        if haupt:
            bonus = p["stimmung_bonus"].get(haupt, 0.0)
            if "getroffen" in gruende:
                bonus += 0.5
                if b["track_bpm"] is not None and energien:
                    rang = sum(1 for e in energien if e < float(b["track_energie"] or 0)) / max(1, len(energien) - 1)
                    ziel[haupt]["energie"] = round(ziel[haupt]["energie"] + 0.2 * (rang - ziel[haupt]["energie"]), 3)
                    ziel[haupt]["bpm"] = round(ziel[haupt]["bpm"] + 0.2 * (float(b["track_bpm"]) - ziel[haupt]["bpm"]), 1)
            elif n >= mindestens:
                bonus += 0.25 * int(b["daumen"])
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


def lernstand_text(con: sqlite3.Connection, konfig: Konfig) -> str:
    p, ziel = aktuelle(con, konfig)
    start, start_ziel, hinweise = vorgaben(konfig)
    zeilen = bewertungen(con)
    daumen = sum(1 for z in zeilen if z["daumen"] > 0)
    teile = [f"🧠 Regie – {len(zeilen)} Bewertungen ({daumen} 👍 / {len(zeilen) - daumen} 👎)"]
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
    geaendert = [s for s in ziel if ziel[s] != ZIEL[s]]  # durch Vorgabe oder "Stimmung getroffen"
    for s in geaendert:
        teile.append(f"Musik für {s}: Energie-Rang {ziel[s]['energie']}, {ziel[s]['bpm']} BPM")
    return "\n".join(teile)
