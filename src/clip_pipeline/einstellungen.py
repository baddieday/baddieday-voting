"""Einstellungen per Telegram (29.09., Florian: „viele Werte soll ich per Hand einstellen … ich will nicht immer den
Weg über den Server“): Was du öfter umstellst, stellst du im Lern-Bot unter ⚙️ Einstellungen um. Die Werte liegen in
der Tabelle `einstellungen` und gehen vor den Dateien: Bot → config/lokal.toml → config/pipeline.toml. „↩️ Standard“
löscht die Zeile, dann gilt wieder die Datei. Nur Schlüssel aus KATALOG werden angenommen und angewendet.

Clip-Auswahl (`lernbot.quelle`): alle Clips (Standard) · nur der neueste Spielabend · nur das neueste Match · ein
bestimmtes Match. Sie wird bei jedem Entwurf neu aufgelöst – „neuester Spielabend“ folgt also von selbst dem nächsten
Abend. Ein Spielabend endet um [zeit].tageswechsel_stunde (06:00), wie überall in der Pipeline (zeit.spielabend).

Warum Telegram und keine eigene Webseite: Der Bot ist schon da, schon auf dich allein beschränkt und erreicht das
Handy überall – ohne neuen Dienst, ohne offenen Port, ohne Anmeldung (Architekturprinzip 7: Polling, kein Webhook).
"""

from __future__ import annotations

import copy
import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from .konfig import Konfig
from .verarbeitung import SESSION_ID
from .zeit import aus_iso, iso, jetzt, spielabend, utc_zu_lokal

QUELLE = "lernbot.quelle"
NEUESTES_MATCH = "match:neuestes"
HARTE_GENRES = ["techno", "hardcore", "electronic-rock", "dance-rock", "midtempo-bass"]  # wie `musik ncs --genre hart`


@dataclass(frozen=True)
class Einstellung:
    schluessel: str                      # Punkt-Pfad in der Konfig, z. B. "lernbot.auto_schwelle"
    titel: str
    optionen: tuple[tuple[Any, str], ...]  # (Wert, Anzeige)
    standard: Any                        # gilt, wenn weder der Bot noch eine Datei etwas sagen
    hilfe: str


KATALOG: tuple[Einstellung, ...] = (
    Einstellung(QUELLE, "🎯 Clips",
                (("alle", "alle Clips"), ("abend", "neuester Spielabend"), (NEUESTES_MATCH, "neuestes Match")),
                "alle", "Aus welchen Clips der Lern-Bot Entwürfe baut. Ein bestimmtes Match: 📅 Match wählen."),
    Einstellung("lernbot.auto_schwelle", "🤖 Vorfilter",
                ((0.0, "aus"), (0.3, "locker"), (0.5, "mittel"), (0.7, "streng")),
                0.0, "Entwürfe mit niedriger Erwartung sortiert der Bot still aus und baut den nächsten."),
    Einstellung("regie.effekte.an", "✨ Effekte", ((True, "an"), (False, "aus")),
                True, "Zoom, Blitz, Zeitlupe, Übergänge im Spielbild."),
    Einstellung("regie.stil", "🎬 Schnittstil",
                (("auto", "automatisch (lernt selbst)"), ("montage", "⚡ Montage"), ("story", "📖 Story"),
                 ("steigerung", "📈 Steigerung"), ("kino", "🎬 Kino"), ("klassik", "🎞️ Klassik")),
                "auto", "Automatisch wählt der Bot den Stil selbst nach den Noten seines Cutter-Kritikers."),
    Einstellung("regie.kritik.ki", "🧐 KI-Cutter", ((True, "an"), (False, "aus")),
                True, "Claude benotet jeden Entwurf als Senior-Cutter (zählt gegen dein Abo, höchstens 20 am Tag)."),
    Einstellung("regie.kritik.schwelle", "🧐 Mindest-Note", ((0.0, "aus"), (40.0, "40"), (50.0, "50"), (60.0, "60"), (70.0, "70")),
                50.0, "Entwürfe mit schlechterer Cutter-Note sortiert der Bot selbst aus und baut neu."),
    Einstellung("musik.genres_bevorzugt", "🎵 Musik", ((HARTE_GENRES, "Techno/Rock bevorzugt"), ([], "alle Genres gleich")),
                [], "Techno, Hardcore und Rock bekommen bei der Musikwahl Vorrang."),
    # Auto-Freigabe im Clip-Bot (30.09.) – am Ende, damit sich die Nummern schon offener Menüs (s:o:<i>) nicht
    # verschieben. Die Typen müssen zu config/pipeline.toml passen (ziel_quote float, frist_h int), sonst lehnt
    # _erlaubt einen Wert aus der Datei ab.
    Einstellung("auto_freigabe.modus", "🤖 Auto-Freigabe",
                (("an", "an"), ("probe", "👀 nur anzeigen"), ("aus", "aus")),
                "an", "Klare Clips entscheidet der Clip-Bot selbst (🤖, ohne Ton, umdrehbar). „Nur anzeigen“ zeigt, "
                "was er täte."),
    Einstellung("auto_freigabe.ziel_quote", "🎯 Auto-Genauigkeit",
                ((0.95, "vorsichtig"), (0.9, "normal"), (0.85, "mutig")),
                0.9, "So oft muss die Erwartung bei deinen Urteilen gestimmt haben, bevor der Bot selbst entscheidet."),
    Einstellung("auto_freigabe.verwerfen", "🗑️ Auto-Aussortieren",
                ((True, "an (weich, nie Triple+/Victory)"), (False, "aus")),
                True, "Aussortierte bleiben Material für Shorts und Highlight – nur dein 🗑️ schließt aus."),
    Einstellung("auto_freigabe.frist_h", "⏰ Frist",
                ((12, "12 h"), (24, "24 h"), (48, "48 h"), (0, "nie")),
                24, "Danach entscheidet der Clip-Bot offene Clips selbst."),
    # 🔥 Viral (05.10.) – hinten angehängt (offene Menüs s:o:<i> behalten ihre Nummern)
    Einstellung("viral.variante", "🔥 Viral-Mischung",
                (("auto", "automatisch (lernt selbst)"), ("twist", "😅 Highlights + Twist"),
                 ("highlight", "⚡ nur Highlights"), ("fail", "💀 Fail-Video")),
                "auto", "Automatisch wählt der Bot die Mischung selbst nach Cutter-Noten und Publikum."),
    Einstellung("viral.ki", "🔥 KI-Einschätzung", ((True, "an"), (False, "aus")),
                True, "Claude schätzt je Moment viral/humor/spannung ein (zählt gegen dein Abo, höchstens 40 am Tag)."),
    # 06.10. (Florian: „wie kann ich die Videos wieder länger werden lassen?“) – hinten angehängt (s:o:<i> bleiben)
    Einstellung("regie.short_mindestens_s", "⏱️ Short-Mindestlänge",
                ((0.0, "automatisch (lernt)"), (45.0, "mindestens 45 s"), (55.0, "mindestens 55 s"),
                 (65.0, "mindestens 65 s"), (75.0, "75 s")),
                0.0, "Untergrenze fürs Ziel der Shorts: Lernen, KI-Cutter und Publikum dürfen nur darüber gehen."),
    # 06.10. (Florian: „das wird alles zu kompliziert“) – aus: ein Knopf, 👍/👎, vier Einstellungen; an: alles wie bisher
    Einstellung("lernbot.experte", "🔧 Experten-Modus", ((False, "aus"), (True, "an")),
                False, "An: alle Knöpfe, Befehle, Gründe und Details. Aus: ein Knopf, 👍/👎, vier Einstellungen."),
    # Stufe 1 (07.10., regeln.py): deine Regeln – ⏱️/⏳/😵 unter ❌ stellen sie um, hier siehst und änderst du sie
    Einstellung("regie.short_ziel_s", "⏱️ Short-Länge",
                ((0.0, "automatisch"), *((float(s), f"{s} s") for s in range(30, 80, 5))),
                0.0, "So lang werden deine Shorts. „⏱️ Zu kurz“ und „⏳ Zu lang“ unter ❌ verschieben das um 10 s."),
    Einstellung("regie.szenen", "🎯 Szenen",
                (("stark", "nur starke (Multikill, Clutch, Endkampf)"), ("alle", "auch Einzelkills")),
                "stark", "Nur starke Szenen: lieber kein Video als eins mit Füllmaterial."),
    Einstellung("regie.effekt_stufe", "✨ Effekte", ((0, "aus"), (1, "ruhig"), (2, "normal"), (3, "wild")),
                2, "Wie viele Effekte (Zoom, Blitz, Zeitlupe) ins Video kommen. „😵 Zu hektisch“ stellt eine Stufe ruhiger."),
    Einstellung("bot.clips_zeigen", "📨 Clip-Bot", ((False, "still (nur Warnungen)"), (True, "jede Szene schicken")),
                False, "Still: der Clip-Bot entscheidet jede Szene selbst und meldet sich nur bei Problemen."),
)
# Die vier Einstellungen im einfachen Menü (07.10.); alle anderen hinter „🔧 Alle Einstellungen“
EINFACH = ("regie.short_ziel_s", "regie.szenen", "regie.effekt_stufe", "musik.genres_bevorzugt")
# Einfacher Modus (07.10.): was ihn ausmacht – ohne Experten-Modus gelten diese Werte, egal was in Datei oder Bot steht.
# KI-Cutter und Selbst-Aussortieren machten Entwürfe langsam und unvorhersehbar, der Abendstand war eine Nachricht zu
# viel; der Stil wechselt der Reihe nach statt per Lotterie.
EINFACH_FEST = {"regie.kritik.ki": False, "regie.kritik.schwelle": 0.0, "lernbot.auto_schwelle": 0.0,
                "lernbot.abendstand": False, "regie.stil_rotation": True}
NACH_SCHLUESSEL = {e.schluessel: e for e in KATALOG}


def _erlaubt(e: Einstellung, wert: Any) -> bool:
    if e.schluessel == QUELLE and isinstance(wert, str) and wert.startswith("match:"):
        return wert == NEUESTES_MATCH or bool(SESSION_ID.fullmatch(wert[len("match:"):]))
    return any(wert == w and type(wert) is type(w) for w, _ in e.optionen)


def gespeichert(con: sqlite3.Connection) -> dict[str, Any]:
    """Die im Bot gesetzten Werte – nur bekannte Schlüssel mit erlaubtem Wert (Unbekanntes wird übergangen)."""
    werte = {}
    for z in con.execute("SELECT schluessel, wert FROM einstellungen"):
        e = NACH_SCHLUESSEL.get(z["schluessel"])
        try:
            wert = json.loads(z["wert"])
        except (TypeError, ValueError):
            continue
        if e is not None and _erlaubt(e, wert):
            werte[e.schluessel] = wert
    return werte


def setze(con: sqlite3.Connection, schluessel: str, wert: Any) -> None:
    e = NACH_SCHLUESSEL.get(schluessel)
    if e is None or not _erlaubt(e, wert):
        raise ValueError(f"Einstellung {schluessel!r} kennt den Wert {wert!r} nicht")
    con.execute("""INSERT INTO einstellungen (schluessel, wert, geaendert) VALUES (?, ?, ?)
                   ON CONFLICT (schluessel) DO UPDATE SET wert = excluded.wert, geaendert = excluded.geaendert""",
                (schluessel, json.dumps(wert), iso(jetzt())))


def zuruecksetzen(con: sqlite3.Connection, schluessel: str) -> None:
    con.execute("DELETE FROM einstellungen WHERE schluessel = ?", (schluessel,))


def experte(con: sqlite3.Connection, konfig: Konfig) -> bool:
    """Experten-Modus an? (⚙️ oder [lernbot].experte; Standard aus, 06.10.)"""
    try:
        werte = gespeichert(con)
    except Exception:  # noqa: BLE001 – ohne Tabelle (alte DB, Tests ohne Regie-Schema): Datei bzw. einfach
        werte = {}
    return bool(werte["lernbot.experte"] if "lernbot.experte" in werte else konfig.wert("lernbot.experte", False))


def _setze_pfad(daten: dict, pfad: str, wert: Any) -> None:
    knoten = daten
    *oben, name = pfad.split(".")
    for teil in oben:
        if not isinstance(knoten.get(teil), dict):
            knoten[teil] = {}
        knoten = knoten[teil]
    knoten[name] = copy.deepcopy(wert)


def anwenden(con: sqlite3.Connection, konfig: Konfig) -> Konfig:
    """Konfig mit den Bot-Werten darüber (eine Kopie – die geladene Konfig bleibt, wie sie ist, damit „↩️ Standard“
    sofort wieder die Datei gelten lässt). Ohne Experten-Modus kommen die Werte aus EINFACH_FEST dazu, und Effekt-Stufe
    „aus“ schaltet die Effekte ab (07.10.)."""
    werte = gespeichert(con)
    experte = bool(werte["lernbot.experte"] if "lernbot.experte" in werte else konfig.wert("lernbot.experte", False))
    stufe = werte.get("regie.effekt_stufe", konfig.wert("regie.effekt_stufe", None))
    if not werte and experte and stufe != 0:
        return konfig
    daten = copy.deepcopy(konfig.daten)
    for pfad, wert in werte.items():
        _setze_pfad(daten, pfad, wert)
    if not experte:
        for pfad, wert in EINFACH_FEST.items():
            _setze_pfad(daten, pfad, wert)
    if stufe == 0:
        _setze_pfad(daten, "regie.effekte.an", False)
    return Konfig(daten=daten, quelle=konfig.quelle)


def aktuell(con: sqlite3.Connection, konfig: Konfig) -> list[tuple[Einstellung, Any, str]]:
    """(Einstellung, wirksamer Wert, Herkunft) – Herkunft "bot", "datei" oder "standard"."""
    bot = gespeichert(con)
    zeilen = []
    for e in KATALOG:
        if e.schluessel in bot:
            zeilen.append((e, bot[e.schluessel], "bot"))
        else:
            wert = konfig.wert(e.schluessel, None)
            zeilen.append((e, e.standard, "standard") if wert is None else (e, wert, "datei"))
    return zeilen


def anzeige(e: Einstellung, wert: Any, con: sqlite3.Connection | None = None, konfig: Konfig | None = None) -> str:
    for w, text in e.optionen:
        if w == wert:
            return text
    if e.schluessel == QUELLE and isinstance(wert, str) and wert.startswith("match:"):
        return "Match " + (_match_text(con, konfig, wert[len("match:"):]) if con is not None else wert[len("match:"):])
    return str(wert)


# --- Clip-Auswahl -------------------------------------------------------------------------

def letzte_matches(con: sqlite3.Connection, konfig: Konfig, anzahl: int = 8) -> list[sqlite3.Row]:
    """Die neuesten Matches mit Clips oder Momenten (für „📅 Match wählen“ und die Auswahl)."""
    return con.execute(
        """SELECT m.id, m.start_utc, m.kills, m.platzierung, m.victory_royale
             FROM matches m
            WHERE EXISTS (SELECT 1 FROM clips c WHERE c.match_id = m.id)
               OR EXISTS (SELECT 1 FROM momente mo WHERE mo.match_id = m.id AND mo.schluessel NOT LIKE 'fail:%')
            ORDER BY m.start_utc DESC LIMIT ?""", (anzahl,)).fetchall()


def _lokal(konfig: Konfig | None, start_utc: str):
    return utc_zu_lokal(aus_iso(start_utc), (konfig.wert("zeit.zeitzone", "Europe/Berlin") if konfig else "Europe/Berlin"))


def _match_text(con: sqlite3.Connection, konfig: Konfig | None, match_id: str) -> str:
    z = con.execute("SELECT id, start_utc, kills, platzierung, victory_royale FROM matches WHERE id = ?",
                    (match_id,)).fetchone()
    return match_zeile(z, konfig) if z else match_id


def match_zeile(z: sqlite3.Row, konfig: Konfig | None) -> str:
    """z. B. „28.09. 21:42 · 3 Kills · #1 👑“."""
    teile = [f"{_lokal(konfig, z['start_utc']):%d.%m. %H:%M}"]
    if z["kills"] is not None:
        teile.append(f"{z['kills']} Kill" + ("" if z["kills"] == 1 else "s"))
    if z["platzierung"]:
        teile.append(f"#{z['platzierung']}" + (" 👑" if z["victory_royale"] else ""))
    return " · ".join(teile)


def quell_matches(con: sqlite3.Connection, konfig: Konfig) -> tuple[set[str] | None, str | None]:
    """(Match-IDs für regie.erstelle(nur_matches=…) oder None = alle, Hinweis für den Entwurf)."""
    quelle = str(konfig.wert(QUELLE, "alle") or "alle")
    if quelle == "alle":
        return None, None
    alle = con.execute(
        """SELECT m.id, m.start_utc, m.kills, m.platzierung, m.victory_royale FROM matches m
            WHERE EXISTS (SELECT 1 FROM clips c WHERE c.match_id = m.id)
               OR EXISTS (SELECT 1 FROM momente mo WHERE mo.match_id = m.id AND mo.schluessel NOT LIKE 'fail:%')
            ORDER BY m.start_utc DESC""").fetchall()
    if not alle:
        return None, "⚠️ Clip-Auswahl: keine Matches gefunden – alle Clips"
    if quelle == "abend":
        zone, wechsel = konfig.wert("zeit.zeitzone", "Europe/Berlin"), int(konfig.wert("zeit.tageswechsel_stunde", 6))
        abend = spielabend(aus_iso(alle[0]["start_utc"]), zone, wechsel)
        ids = {z["id"] for z in alle if spielabend(aus_iso(z["start_utc"]), zone, wechsel) == abend}
        return ids, f"🎯 nur Spielabend {abend:%d.%m.} ({len(ids)} Match{'' if len(ids) == 1 else 'es'})"
    if quelle == NEUESTES_MATCH:
        return {alle[0]["id"]}, f"🎯 nur neuestes Match {match_zeile(alle[0], konfig)}"
    match_id = quelle[len("match:"):] if quelle.startswith("match:") else ""
    treffer = next((z for z in alle if z["id"] == match_id), None)
    if treffer is None:
        return None, f"⚠️ Clip-Auswahl: Match {match_id or quelle} nicht gefunden – alle Clips"
    return {match_id}, f"🎯 nur Match {match_zeile(treffer, konfig)}"


def offene_clips(con: sqlite3.Connection, matches: set[str]) -> list[int]:
    """Clips dieser Matches ohne Stimmung (die besten zuerst) – damit ein frischer Spielabend nicht leer bleibt."""
    platz = ",".join("?" for _ in matches)
    return [z["id"] for z in con.execute(
        f"""SELECT c.id FROM clips c WHERE c.match_id IN ({platz})
               AND NOT EXISTS (SELECT 1 FROM momente mo WHERE mo.clip_id = c.id)
             ORDER BY c.punkte DESC, c.id""", sorted(matches))]
