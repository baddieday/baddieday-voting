"""Fail-Momente (`pipeline fail --session ID` · `pipeline fail --nachziehen [--tage 14]`), Florian 05.10.

Warum: Bis heute schnitt die Pipeline Momente nur um Kills – ein eigener Tod tauchte nur auf, wenn er zufällig in
einem Kill-Clip lag. Fails ziehen aber Zuschauer („Größter Fail der Woche“). Darum wird je eigenem Tod im Replay
eines verarbeiteten Matches ein eigener Moment geschnitten: aus dem Rohvideo mit der besten Abdeckung,
[fail].vor_s (12 s) vor dem Tod bis [fail].nach_s (3 s) danach.

Was im Moment steht (momente.merkmale, alles aus Daten, nichts erfunden):
  fail = True, tod_sekunde (Sekunde in der Datei), verbleibend beim Tod und platz = verbleibend + 1 (Annahme: Platz
  aus Spielersicht, nicht der Team-Platz), kills_vorher_30s (eigene Kills in den 30 s davor = Fallhöhe), selbst
  (Sturm/Sturz/eigene Explosion), killer_bot (vom Bot erledigt), knock_erlitten (vorher selbst umgehauen), waffe_gegner
  (GunType-Zahl) mit Kategorie. Dazu die Mikro-Messung wie bei jedem Moment (stimmung.merkmale: Spitzen, laute
  Mikro-Stellen; Whisper holt mic_nachziehen nach) und fail_score/fail_gruende/fail_titel.

Fail-Score = Startwert und Rückfall (Florian 05.10.: „kein starres Regelwerk – er soll das mit KI können und aus
Daten lernen“): Die eigentliche Einschätzung macht viral.py (KI je Moment, Tageslimit); dieser Score gilt, solange
keine KI-Einschätzung da ist. Gewichte in [fail.gewichte]:
  Fallhöhe       platz ≤ 10: platz_10 · platz ≤ 3: zusätzlich platz_3 · je Kill in den 30 s davor: kill_vorher
  Erwartungsbruch killer_bot: bot · selbst: selbst · vorher umgehauen: knock
  Reaktion       je Frust-Wort / laute Mikro-Stelle / Lachen (gedeckelt auf 3): mic_frust / mic_laut / mic_lachen
Beispiel: Platz 2 nach Triple Kill → 2 + 3 + 3 × 1 = 8.

Harte Regeln wie beim Nachschnitt (nachschnitt.py): nur im Puffer (getrennter Betrieb), jede Pfad-Komponente per
lstat geprüft, nie pve-big wecken, nie etwas überschreiben oder löschen. Idempotent: ein vorhandener Schlüssel
fail:<match>:<sekunde> (Sekunde seit Replay-Start) wird übersprungen. Ein Match ohne Tod (Victory) liefert nichts.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from . import erfassung, merkmale, nachschnitt, replay, stimmung, verarbeitung
from .konfig import Konfig
from .medien import MedienFehler
from .zeit import iso, jetzt

log = logging.getLogger("pipeline")

VERSION = 1
PRAEFIX = "fail:"
MIN_VOR_S = 3.0      # so viel Anlauf vor dem Tod muss die Aufnahme mindestens hergeben
MIN_NACH_S = 0.5     # und so viel danach
STANDARD = {"vor_s": 12.0, "nach_s": 3.0, "kills_fenster_s": 30.0, "knock_fenster_s": 30.0, "kills_deckel": 4,
            "mic_je_lauf": 2}
GEWICHTE = {"platz_10": 2.0, "platz_3": 3.0, "kill_vorher": 1.0, "bot": 3.0, "selbst": 2.5, "knock": 0.5,
            "mic_frust": 0.5, "mic_laut": 0.5, "mic_lachen": 0.75}
ZAEHLER = ("tode", "neu", "schon_da", "ohne_aufnahme", "ohne_quelle", "passt_nicht", "fehler")


def ist_fail(schluessel: str | None, mk: dict | None = None) -> bool:
    """Fail-Moment? Am Schlüssel (fail:…) oder an merkmale.fail."""
    return bool((schluessel or "").startswith(PRAEFIX) or (isinstance(mk, dict) and mk.get("fail")))


def _wert(konfig: Konfig, name: str) -> float:
    return float(konfig.wert(f"fail.{name}", STANDARD[name]))


def gewichte(konfig: Konfig | None) -> dict[str, float]:
    """[fail.gewichte] über den Startwerten (unbekannte Schlüssel und Nicht-Zahlen fallen weg)."""
    g = dict(GEWICHTE)
    roh = (konfig.wert("fail.gewichte", {}) if konfig is not None else {}) or {}
    for k, v in roh.items() if isinstance(roh, dict) else []:
        if k in g and isinstance(v, (int, float)) and not isinstance(v, bool):
            g[k] = float(v)
    return g


# --- Fakten aus dem Replay ------------------------------------------------------------------------

def tode(match: replay.Match, konfig: Konfig) -> list[dict]:
    """Je eigenem Tod die Fakten (ohne Video). Ein Match ohne Tod (Victory Royale) → [].
    Beispiel: Triple Kill 20–10 s vor dem Tod, verbleibend 1 → {"platz": 2, "kills_vorher_30s": 3, …}."""
    fenster_k = timedelta(seconds=_wert(konfig, "kills_fenster_s"))
    fenster_n = timedelta(seconds=_wert(konfig, "knock_fenster_s"))
    ergebnis = []
    for e in match.ereignisse:
        if e.art != "tod":
            continue
        kills = [k for k in match.ereignisse if k.art == "kill" and e.zeit_utc - fenster_k <= k.zeit_utc <= e.zeit_utc]
        knocks = [k for k in match.ereignisse
                  if k.art == "knock_erlitten" and e.zeit_utc - fenster_n <= k.zeit_utc <= e.zeit_utc]
        knock = knocks[-1] if knocks else None
        bot = e.eliminator_bot if e.eliminator_bot is not None else (knock.eliminator_bot if knock else None)
        waffe = knock.waffe if knock is not None and knock.waffe is not None else e.waffe
        ergebnis.append({
            "tod_utc": e.zeit_utc,
            "sekunde": max(0, round((e.zeit_utc - match.start_utc).total_seconds())),
            "verbleibend": e.verbleibend,
            "platz": e.verbleibend + 1 if e.verbleibend is not None else None,
            "kills_vorher_30s": len(kills),
            "selbst": e.selbst,
            "killer_bot": bot,
            "knock_erlitten": knock is not None,
            "waffe_gegner": waffe,
            "waffe_kategorie": merkmale.waffen_kategorie(waffe, konfig) if waffe is not None else None,
        })
    return ergebnis


def fail_score(mk: dict, konfig: Konfig | None = None) -> tuple[float, list[str]]:
    """(Score, Gründe) aus Fakten und Mikro – Startwert/Rückfall, solange keine KI-Einschätzung da ist (viral.py).
    Unbekanntes (None) zählt 0. Beispiel: Platz 2 nach Triple Kill → (8.0, ["Platz 2", "3 Kills davor"])."""
    g, deckel = gewichte(konfig), int(konfig.wert("fail.kills_deckel", STANDARD["kills_deckel"]) if konfig else 4)
    score, gruende = 0.0, []
    platz = mk.get("platz")
    if isinstance(platz, int) and platz <= 10:
        score += g["platz_10"] + (g["platz_3"] if platz <= 3 else 0.0)
        gruende.append(f"Platz {platz}")
    if (n := int(mk.get("kills_vorher_30s") or 0)) > 0:
        score += g["kill_vorher"] * min(n, deckel)
        gruende.append(f"{n} Kill{'s' if n != 1 else ''} davor")
    if mk.get("killer_bot"):
        score += g["bot"]
        gruende.append("vom Bot erledigt")
    if mk.get("selbst"):
        score += g["selbst"]
        gruende.append("selbst erledigt")
    if mk.get("knock_erlitten"):
        score += g["knock"]
    for name, schluessel in (("mic_frust", "frust"), ("mic_laut", "jubel_laut"), ("mic_lachen", "lachen")):
        if (wert := mk.get(schluessel)) and isinstance(wert, (int, float)):
            score += g[name] * min(float(wert), merkmale.MIC_DECKEL)
    return round(score, 2), gruende


def titel(mk: dict) -> str | None:
    """Titel im Rand – nur aus Daten (DejaVu kann keine Emojis, darum ohne). None = kein Titel.
    Vorrang: Platz ≤ 3 (mit Bot) · selbst · Bot · ≥ 2 Kills davor · Platz ≤ 10."""
    platz, kills = mk.get("platz"), int(mk.get("kills_vorher_30s") or 0)
    oben = isinstance(platz, int) and platz <= 3
    if oben and mk.get("killer_bot"):
        return f"PLATZ {platz} – VOM BOT"
    if oben:
        return f"PLATZ {platz}"
    if mk.get("selbst"):
        return "SELBST ERLEDIGT"
    if mk.get("killer_bot"):
        return "VOM BOT ERLEDIGT"
    if kills >= 2:
        return f"{kills} KILLS … UND WEG"
    if isinstance(platz, int) and platz <= 10:
        return f"PLATZ {platz}"
    return None


def neu_bewerten(mk: dict, konfig: Konfig | None = None) -> dict:
    """fail_score, fail_gruende, fail_titel aus den aktuellen Merkmalen (nach Mikro/Whisper erneut)."""
    score, gruende = fail_score(mk, konfig)
    return {**mk, "fail_score": score, "fail_gruende": gruende, "fail_titel": titel(mk)}


def stimmung_fuer(mk: dict) -> str:
    """frustriert, außer das Mikro lacht deutlich (dann lustig) – dieselben Regeln wie stimmung.punkte."""
    p = stimmung.punkte(mk)
    return "lustig" if p["lustig"] > p["frustriert"] else "frustriert"


# --- Aufnahme wählen und schneiden -------------------------------------------------------------------

def waehle_aufnahme(con: sqlite3.Connection, konfig: Konfig, tod: datetime) -> tuple[str, object | None, float, float]:
    """(Ergebnis, Aufnahme, start_s, ende_s): die Aufnahme im Puffer, die das Fenster vor_s/nach_s um den Tod am besten
    abdeckt (bei Gleichstand nach [auswahl].prioritaet, dann die längere). Ergebnis ok | ohne_aufnahme |
    ohne_quelle (nur im Lager) | passt_nicht (zu wenig Anlauf oder Nachlauf)."""
    vor, nach = _wert(konfig, "vor_s"), _wert(konfig, "nach_s")
    von, bis = tod - timedelta(seconds=vor), tod + timedelta(seconds=nach)
    prioritaet = list(konfig.wert("auswahl.prioritaet", []) or [])
    kandidaten = []
    for a in erfassung.aufnahmen_im_zeitraum(con, von, bis):
        if a.dauer_s <= 0 or not a.enthaelt(tod):
            continue
        anfang, ende = max(a.start_utc, von), min(a.ende_utc, bis)
        abdeckung = max(0.0, (ende - anfang).total_seconds()) / (vor + nach)
        rang = prioritaet.index(a.quelle) if a.quelle in prioritaet else len(prioritaet)
        kandidaten.append(((round(abdeckung, 2), -rang, a.dauer_s), a))
    if not kandidaten:
        return "ohne_aufnahme", None, 0.0, 0.0
    ergebnis = "ohne_quelle"
    for _, a in sorted(kandidaten, key=lambda x: x[0], reverse=True):
        if nachschnitt._quelle_im_puffer(konfig, a.pfad) is None:
            continue
        t = a.sekunde(tod)
        start, ende = round(max(0.0, t - vor), 3), round(min(a.dauer_s, t + nach), 3)
        if t - start < MIN_VOR_S or ende - t < MIN_NACH_S:
            ergebnis = "passt_nicht"
            continue
        return "ok", a, start, ende
    return ergebnis, None, 0.0, 0.0


def _messen(konfig: Konfig, datei: Path, tod_s: float, knock_s: float | None, dauer: float,
            sprache) -> tuple[dict, str | None]:
    """Mikro-/Spielton-Messung wie bei jedem Moment (stimmung.merkmale) – ohne Kills (die Fallhöhe steht in den
    Fakten), damit der Regisseur den Kern um den Tod legt und keinen Kill-Titel setzt."""
    ereignisse = [(tod_s, "tod")] + ([(knock_s, "knock_erlitten")] if knock_s is not None else [])
    m = stimmung.Moment("fail", datei, 0.0, dauer, ereignisse=sorted(ereignisse))
    return stimmung.merkmale(m, konfig, sprache)


def _vorhanden(con: sqlite3.Connection, schluessel: str) -> bool:
    return con.execute("SELECT 1 FROM momente WHERE schluessel = ?", (schluessel,)).fetchone() is not None


def fail_session(con: sqlite3.Connection, konfig: Konfig, sid: str, *, sprache=None) -> dict:
    """Fail-Momente eines Matches anlegen. Rückgabe {"session", "tode", "neu", "schon_da", …, "momente": [...]}.
    sprache: stimmung.Transkription oder None (Whisper holt mic_nachziehen nach)."""
    konfig.pruefe_getrennt(mit_lager=False)  # KonfigFehler ohne getrennten Betrieb; nur stat() im Puffer
    verarbeitung.pruefe_id(sid)
    ergebnis: dict = {"session": sid, **dict.fromkeys(ZAEHLER, 0), "momente": []}
    match = merkmale._match_aus_puffer(konfig, sid)
    if match is None:
        ergebnis["ohne_replay"] = True
        return ergebnis
    knock_fenster = timedelta(seconds=_wert(konfig, "knock_fenster_s"))
    for f in tode(match, konfig):
        ergebnis["tode"] += 1
        schluessel = f"{PRAEFIX}{sid}:{f['sekunde']}"
        if _vorhanden(con, schluessel):
            ergebnis["schon_da"] += 1
            continue
        art, aufnahme, start, ende = waehle_aufnahme(con, konfig, f["tod_utc"])
        if art != "ok":
            ergebnis[art] += 1
            log.info("%s: %s", schluessel, art)
            continue
        try:
            ergebnis["momente"].append(_anlegen(con, konfig, sid, schluessel, f, aufnahme, start, ende, match,
                                                knock_fenster, sprache))
            ergebnis["neu"] += 1
        except (MedienFehler, nachschnitt.NachschnittFehler, verarbeitung.SessionFehler, OSError) as e:
            ergebnis["fehler"] += 1  # ein kaputter Tod hält die anderen nicht auf
            log.error("%s: %s", schluessel, e)
    return ergebnis


def _anlegen(con, konfig, sid, schluessel, f, aufnahme, start, ende, match, knock_fenster, sprache) -> dict:
    name = f"fail{f['sekunde']:05d}_{round(start * 1000)}-{round(ende * 1000)}.mp4"
    plan = nachschnitt.Plan(schluessel=schluessel, moment_id=0, clip_id=0, match_id=sid, quelle_pfad=aufnahme.pfad,
                            quelle=None, alt_start_s=start, start_s=start, ende_s=ende,
                            start_utc=aufnahme.start_utc + timedelta(seconds=start),
                            ziel=konfig.ordner("sessions") / sid / "momente" / name)
    dauer, _uebernommen = nachschnitt._schneide(con, konfig, plan)   # nie überschreiben, harter Link am Ende
    tod_s = round((f["tod_utc"] - plan.start_utc).total_seconds(), 1)
    knocks = [e for e in match.ereignisse if e.art == "knock_erlitten"
              and f["tod_utc"] - knock_fenster <= e.zeit_utc <= f["tod_utc"]]
    knock_s = round((knocks[-1].zeit_utc - plan.start_utc).total_seconds(), 1) if knocks else None
    gemessen, text = _messen(konfig, plan.ziel, tod_s, knock_s if knock_s is not None and knock_s >= 0 else None,
                             dauer, sprache)
    fakten = {k: v for k, v in f.items() if k != "tod_utc"}
    mk = neu_bewerten({**gemessen, **fakten, "fail": True, "fail_version": VERSION, "tod": 1, "tod_sekunde": tod_s,
                       "kills": 0, "max_gruppe": 0, "victory_royale": 0, "dauer_s": round(dauer, 1),
                       "quelle_pfad": aufnahme.pfad, "quelle_start_s": start}, konfig)
    stimmung_ = stimmung_fuer(mk)
    zeit = iso(jetzt())
    con.execute(
        """INSERT INTO momente (schluessel, clip_id, match_id, datei, start_s, ende_s, start_utc, kills, stimmung,
                                sicherheit, quelle, merkmale, text, erstellt, geaendert)
           VALUES (?, NULL, ?, ?, 0, ?, ?, 0, ?, 0.5, 'regel', ?, ?, ?, ?)
           ON CONFLICT (schluessel) DO NOTHING""",
        (schluessel, sid, str(plan.ziel), round(dauer, 3), iso(plan.start_utc), stimmung_,
         json.dumps(mk, ensure_ascii=False), text, zeit, zeit))
    log.info("%s: Fail-Moment %s (%.1f s, Score %s, %s)", schluessel, plan.ziel.name, dauer, mk["fail_score"],
             ", ".join(mk["fail_gruende"]) or "ohne Besonderheit")
    return {"moment": schluessel, "datei": plan.ziel.name, "score": mk["fail_score"], "titel": mk["fail_titel"],
            "gruende": mk["fail_gruende"]}


def nachziehen(con: sqlite3.Connection, konfig: Konfig, *, tage: int = 14, sprache=None) -> dict:
    """Alle Matches der letzten `tage` Tage mit replay.json im Puffer (`pipeline fail --nachziehen`)."""
    konfig.pruefe_getrennt(mit_lager=False)
    grenze = iso(jetzt() - timedelta(days=tage))
    summe: dict = {"tage": tage, "matches": 0, **dict.fromkeys(ZAEHLER, 0), "momente": []}
    for z in con.execute("SELECT id FROM matches WHERE start_utc >= ? ORDER BY start_utc", (grenze,)).fetchall():
        try:
            e = fail_session(con, konfig, z["id"], sprache=sprache)
        except verarbeitung.SessionFehler as fehler:  # ID aus Altbestand, die kein Ordnername sein darf
            log.warning("Fail %s: %s", z["id"], fehler)
            continue
        if e.get("ohne_replay"):
            continue
        summe["matches"] += 1
        for k in ZAEHLER:
            summe[k] += e[k]
        summe["momente"] += e["momente"]
    del summe["momente"][20:]
    return summe


def mic_nachziehen(con: sqlite3.Connection, konfig: Konfig, *, maximal: int | None = None) -> dict:
    """Whisper für Fail-Momente nachholen (wie stimmung._ergaenze_mic für Clips): nur die Mic-Schlüssel und das
    Transkript übernehmen, danach Fail-Score, Titel und Stimmung neu. Ohne faster-whisper: nichts."""
    from . import mikro  # whisper_da prüft ohne zu laden (der Import von faster-whisper kostet Sekunden und RAM)

    if not mikro.whisper_da():
        return {"nachgeholt": 0, "hinweis": "faster-whisper nicht installiert"}
    sprache = stimmung.Transkription(konfig)
    n = maximal if maximal is not None else int(konfig.wert("fail.mic_je_lauf", STANDARD["mic_je_lauf"]))
    nachgeholt = 0
    for z in con.execute("SELECT * FROM momente WHERE schluessel LIKE ? ORDER BY id DESC", (PRAEFIX + "%",)).fetchall():
        if nachgeholt >= n:
            break
        try:
            mk = json.loads(z["merkmale"])
        except json.JSONDecodeError:
            continue
        if not isinstance(mk, dict) or not merkmale.mic_nachholen(mk) or not Path(z["datei"]).is_file():
            continue
        neu, text = _messen(konfig, Path(z["datei"]), float(mk.get("tod_sekunde") or 0.0), None,
                            float(z["ende_s"]), sprache)
        if "fehler" in neu:
            mk["fehler"] = neu["fehler"]
        else:
            mk.update({k: neu[k] for k in stimmung.MIC_SCHLUESSEL if k in neu})
            mk.update({k: neu[k] for k in ("lachen", "jubel", "frust") if k in neu})
        mk = neu_bewerten(mk, konfig)
        con.execute("UPDATE momente SET merkmale = ?, text = COALESCE(?, text), stimmung = ?, geaendert = ? "
                    "WHERE id = ?", (json.dumps(mk, ensure_ascii=False), text, stimmung_fuer(mk), iso(jetzt()),
                                     z["id"]))
        nachgeholt += 1
    return {"nachgeholt": nachgeholt}


def nach_render(con: sqlite3.Connection, konfig: Konfig, session: str | None) -> dict:
    """Automatisch im Mic-Schritt nach render (clip-mikro, `pipeline stimmung --clips`): Fail-Momente der Session
    (ohne Session: der letzten [fail].auto_tage Tage) und ein paar Whisper-Läufe. Fehler bleiben hier – der
    Mic-Schritt und seine JSON-Zeile gehen vor. [fail].an = false schaltet es ab."""
    if not konfig.wert("fail.an", True):
        return {"aus": True}
    try:
        if session:
            e = fail_session(con, konfig, session)
        else:
            e = nachziehen(con, konfig, tage=int(konfig.wert("fail.auto_tage", 2)))
        e = {k: v for k, v in e.items() if k != "momente"}
        e["mic"] = mic_nachziehen(con, konfig)
        return e
    except Exception as fehler:  # noqa: BLE001 – Fails sind Zugabe, der Mic-Schritt ist wichtiger
        log.exception("Fail-Momente nach render")
        return {"fehler": f"{type(fehler).__name__}: {str(fehler)[:160]}"}


def fakten_text(mk: dict) -> dict:
    """Die Fakten eines Fail-Moments für KI und Caption (nur Daten, keine Pfade)."""
    return {k: mk.get(k) for k in ("platz", "verbleibend", "kills_vorher_30s", "selbst", "killer_bot",
                                    "knock_erlitten", "waffe_kategorie", "fail_score")}


