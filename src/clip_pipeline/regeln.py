"""Deine Regeln (Stufe 1, 07.10., Florian: „Impact wird nicht gewertet – egal ob 50 oder 100 Videos mit zu kurz oder
Clip langweilig“).

Vorher war jeder Grund unter 👎 nur eine Stimme unter vielen: Regie-Lernen, KI-Cutter, Publikums-Modell und
Stil-Auswahl rechneten alle mit, und dein Knopf wurde verwässert. Jetzt ist jeder Grund eine feste Regel mit sofortiger,
sichtbarer Wirkung – der Bot sagt dir in einem Satz, was er geändert hat:

  ⏱️ zu kurz       Ziel der Shorts +10 s (bis 75 s)                         Einstellung regie.short_ziel_s
  ⏳ zu lang       Ziel −10 s (ab 30 s)
  🥱 langweilig    die schwächere Hälfte der Szenen kommt nie wieder        Tabelle sperren (art moment)
  🎵 Musik         dieser Song kommt nie wieder                             Tabelle sperren (art track)
  😵 zu hektisch   Effekte eine Stufe ruhiger (wild → normal → ruhig → aus) Einstellung regie.effekt_stufe
  (Experten-Gründe: 🎆 zu viele Effekte wie hektisch, 💥 mehr Action eine Stufe wilder)

anwenden() legt die Regeln NACH allem Gelernten über die Regie-Parameter – sie gehen immer vor. Die Sperren wirken direkt
in regie.kandidaten_mit_bericht und regie.waehle_musik. Gründe ohne Regel (✂️ abgeschnitten, 🎯 getroffen) lernt der Bot
wie bisher.
"""

from __future__ import annotations

import json
import sqlite3

from . import einstellungen
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
    """Schlüssel der gesperrten Momente (art "moment") bzw. Songs (art "track", tracks.id als Text)."""
    try:
        return {z["schluessel"] for z in con.execute("SELECT schluessel FROM sperren WHERE art = ?", (art,))}
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
    """Wert aus dem Bot (⚙️/❌) oder aus der Datei – None, wenn keiner gesetzt ist (dann gilt das Gelernte)."""
    bot = einstellungen.gespeichert(con)
    return bot[schluessel] if schluessel in bot else konfig.wert(schluessel, None)


def ziel_regel(con: sqlite3.Connection, konfig: Konfig) -> float | None:
    """Deine feste Short-Länge in s, None = automatisch (gelernt)."""
    wert = _gesetzt(con, konfig, ZIEL_SCHLUESSEL)
    return float(wert) if wert else None


def stufe(con: sqlite3.Connection, konfig: Konfig) -> int | None:
    """Deine Effekt-Stufe 0–3, None = nie gesetzt (dann gilt das Gelernte)."""
    wert = _gesetzt(con, konfig, STUFE_SCHLUESSEL)
    return int(wert) if wert is not None else None


def nur_starke(con: sqlite3.Connection, konfig: Konfig) -> bool:
    return str(_gesetzt(con, konfig, SZENEN_SCHLUESSEL) or "stark") != "alle"


def anwenden(con: sqlite3.Connection, konfig: Konfig, fmt: str, p: dict) -> dict:
    """Regie-Parameter mit deinen Regeln darüber (Kopie): feste Short-Länge, Effekt-Stufe, nur starke Szenen, Songs
    rotieren. Aufrufer: Lern-Bot (baue_entwurf) und der Abend-Ablauf (sitzung) – nach regie_lernen.aktuelle."""
    p = dict(p)
    if fmt == "short" and (ziel := ziel_regel(con, konfig)):
        p["ziel_dauer_s"] = max(ZIEL_GRENZEN[0], min(ZIEL_GRENZEN[1], ziel))
    s = stufe(con, konfig)
    if s in STUFE_WERTE:
        hektik, staerke = STUFE_WERTE[s]
        p["effekt_hektik"] = hektik
        p["effekt_staerke"] = {st: staerke for st in STIMMUNGEN}
    p["nur_starke"] = nur_starke(con, konfig)
    p["musik_rotation"] = MUSIK_ROTATION
    return p


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


def wende_an(con: sqlite3.Connection, konfig: Konfig, grund: str, liste: dict) -> str | None:
    """Setzt die Regel zu einem Grund und gibt die Bestätigung für dich zurück (ein Satz, ohne Fachbegriffe).
    None = für diesen Grund gibt es keine feste Regel (er wird nur gelernt).
    Beispiel: „kurz“ bei Ziel 45 s → Einstellung 55 → „⏱️ Verstanden: Shorts sind ab jetzt 55 s lang (vorher 45 s).“"""
    if grund in ("kurz", "lang"):
        p = liste.get("parameter") or {}
        alt = _rund5(ziel_regel(con, konfig) or p.get("ziel_dauer_s") or 45)
        neu = max(ZIEL_GRENZEN[0], min(ZIEL_GRENZEN[1], alt + (ZIEL_SCHRITT if grund == "kurz" else -ZIEL_SCHRITT)))
        einstellungen.setze(con, ZIEL_SCHLUESSEL, float(neu))
        if neu == alt:
            return (f"⏱️ Länger als {ZIEL_GRENZEN[1]} s geht bei Shorts nicht – das Ziel bleibt {neu} s." if grund == "kurz"
                    else f"⏱️ Kürzer als {ZIEL_GRENZEN[0]} s geht nicht – das Ziel bleibt {neu} s.")
        text = f"⏱️ Verstanden: Shorts sind ab jetzt {neu} s lang (vorher {alt} s)."
        dauer = float(liste.get("dauer_s") or 0)
        if grund == "kurz" and dauer and dauer < alt - 5:
            text += f" Dieses Video hatte nur {dauer:.0f} s – mehr starke Szenen gab es nicht."
        return text
    if grund == "langweilig":
        momente = _momente(liste)
        if not momente:
            return "🥱 Verstanden – in diesem Video fand ich keine Szenen zum Aussortieren."
        schwach = [m for m, _ in sorted(momente, key=lambda x: (x[1], x[0]))[:max(1, len(momente) // 2)]]
        sperre(con, "moment", schwach, "langweilig")
        return (f"🥱 Verstanden: Die {len(schwach)} schwächsten Szenen dieses Videos nehme ich nie wieder."
                if len(schwach) > 1 else "🥱 Verstanden: Die schwächste Szene dieses Videos nehme ich nie wieder.")
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
    """„Deine Regeln: Shorts 55 s · Effekte ruhig · nur starke Szenen · 3 Songs und 12 Szenen gesperrt“."""
    ziel = ziel_regel(con, konfig)
    s = stufe(con, konfig)
    teile = [f"Shorts {ziel:.0f} s" if ziel else "Länge automatisch",
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
    """Matches, aus denen ein Video seine Szenen hat – für die neue Fassung aus demselben Abend."""
    return {s["match_id"] for s in liste.get("segmente") or [] if isinstance(s, dict) and s.get("match_id")}
