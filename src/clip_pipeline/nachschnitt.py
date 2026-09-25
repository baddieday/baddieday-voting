"""Vorhandene Multikill-Momente neu schneiden (`pipeline momente nachschneiden [--tage 14] [--probe]`).

Warum: Bis 25.09. begann ein Clip 8 s vor dem ersten KILL. Beim Team-Wipe fallen alle Kills auf den Wipe – das
eigentliche Umhauen lag davor und fehlte im Clip. Neu gibt es je Kill die AKTION (mein Umhauen, replay.py).
Dieser Befehl schneidet Momente 'clip:N' mit ≥ 2 Kills neu aus der Quellaufnahme im Puffer, sodass die Aktion drin ist.

Harte Regeln:
  - Nur im getrennten Betrieb (E19) und nur im Puffer: die Quelle wird über [speicher].wurzel gelesen, jede
    Pfad-Komponente darunter per lstat geprüft. Ein Link (etwa ins Lager auf pve-big) oder eine fehlende Datei
    heißt „nicht im Puffer“ – überspringen, NIE pve-big wecken, NIE /srv/big anfassen.
  - Nichts wird überschrieben oder gelöscht: neue Dateien in sessions/<ID>/momente/, Bot-Clips und die Tabelle
    clips bleiben unangetastet. Gibt es das Ziel schon, wird es übernommen (passende Dauer) oder als Fehler gemeldet.
  - Gezählt wird wie bisher (Serie, Punkte, Elo): geändert werden nur Datei und Zeiten der momente-Zeile.
    Schlüssel, Stimmung und damit der gelernte moment_bonus bleiben.
  - Idempotent: merkmale.nachschnitt.version == VERSION heißt erledigt.

Fenster in der Quelle: ab erster Aktion − [vorbewertung].puffer_vorne_s bis zum alten Ende (clips.quelle_ende_s),
höchstens 60 s (sonst vorne gekappt). Liegt im Anlauf ein Kill eines früheren Clips, beginnt das Fenster kurz danach
(höchstens 1 s vor der Aktion) – sonst stünde er in kill_sekunden dieses Moments. Dieselbe Regel gilt für neue Clips
in analyze (vorbewertung.anlauf_start). Liegt die Aktion schon in der
bisherigen Datei, wird nichts geschnitten; nur aktion_sekunden kommen dazu (neu_geschnitten: false).
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import stat
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath

from . import db, erfassung, medien, stimmung, verarbeitung
from .konfig import Konfig
from .medien import MedienFehler
from .vorbewertung import MAX_DAUER_S, anlauf_start
from .zeit import aus_iso, iso, jetzt

log = logging.getLogger("pipeline")

VERSION = 1
NACHSCHNITT = stimmung.NACHSCHNITT
TOLERANZ_S = 0.2        # so weit darf die Dauer einer (vorhandenen) Datei von der geplanten abweichen
SCHON_DRIN_S = 0.05     # beginnt der neue Schnitt höchstens so viel früher, reicht der Bot-Clip
BEISPIELE_MAX = 20
ZAEHLER = ("geprueft", "neu_geschnitten", "nur_merkmale", "schon_erledigt", "ohne_quelle", "ohne_replay",
           "ohne_aufnahme", "passt_nicht", "fehler", "gekappt_60s")

# Spalten ausdrücklich benannt: momente und clips haben gleichnamige Spalten (id, start_utc, merkmale …)
AUSWAHL = """
    SELECT m.id AS moment_id, m.schluessel, m.datei, m.start_s, m.ende_s, m.start_utc AS m_start_utc,
           m.merkmale AS m_merkmale,
           c.id AS clip_id, c.match_id, c.kills, c.status, c.start_utc AS c_start_utc, c.ende_utc AS c_ende_utc,
           c.quelle_pfad, c.quelle_start_s, c.quelle_ende_s
      FROM momente m JOIN clips c ON c.id = m.clip_id
     WHERE m.schluessel LIKE 'clip:%' AND c.kills >= 2 AND c.status != 'verworfen' AND c.start_utc >= ?
     ORDER BY c.start_utc, c.id"""


class NachschnittFehler(RuntimeError):
    """Ein Moment ließ sich nicht sicher neu schneiden (Ziel belegt, Link im Zielordner, Dauer passt nicht)."""


@dataclass
class Plan:
    schluessel: str
    moment_id: int
    clip_id: int
    match_id: str
    quelle_pfad: str
    quelle: Path | None           # geprüfte Quelle im Puffer; None = nur Merkmale (kein Schnitt nötig)
    alt_start_s: float            # Sekunde in der Quelle, bei der die bisherige Moment-Datei beginnt
    start_s: float                # neuer Beginn in der Quelle (bei nur Merkmalen = alt_start_s)
    ende_s: float                 # Ende in der Quelle (bleibt clips.quelle_ende_s)
    start_utc: datetime           # Zeitpunkt von start_s
    kill_sekunden: list[float] = field(default_factory=list)    # relativ zum (neuen) Dateibeginn
    aktion_sekunden: list[float] = field(default_factory=list)  # parallel dazu, darf < 0 sein
    ziel: Path | None = None      # neue Datei; None = nur Merkmale
    hinweis: str | None = None
    gekappt: bool = False

    @property
    def anlauf_s(self) -> float:
        return round(self.alt_start_s - self.start_s, 3)


# --- Pfade: nur im Puffer, nie über Links ------------------------------------------------------

def _pfad_teile(rel: str) -> tuple[str, ...] | None:
    """Teile eines relativen Pfads – None bei absolut, '..', '.', leer oder Backslash (nie aus der Wurzel heraus)."""
    if not isinstance(rel, str) or not rel.strip() or "\\" in rel or "\x00" in rel:
        return None
    if PurePosixPath(rel).is_absolute() or any(t in ("", ".", "..") for t in rel.split("/")):
        return None
    return PurePosixPath(rel).parts


def _quelle_im_puffer(konfig: Konfig, rel: str) -> Path | None:
    """Die Quellaufnahme im Puffer ([speicher].wurzel / rel) – oder None, wenn sie dort nicht als echte Datei liegt.

    Jede Komponente unterhalb der Wurzel wird per lstat geprüft; ein Link wird nie verfolgt (er könnte ins Lager
    auf pve-big zeigen und ihn per NFS wecken). Die Wurzel selbst darf ein Link sein (/srv/clips -> /srv/puffer).
    Bewusst nicht material.lokal: gekürzte Kopien (video_ende) beginnen mit Versatz."""
    teile = _pfad_teile(rel)
    if teile is None:
        return None
    pfad = konfig.wurzel
    for i, teil in enumerate(teile):
        pfad = pfad / teil
        try:
            st = os.lstat(pfad)
        except OSError:
            return None
        if stat.S_ISLNK(st.st_mode):
            log.info("%s: %s ist ein Link – gilt als nicht im Puffer", rel, pfad)
            return None
        if not (stat.S_ISREG(st.st_mode) if i == len(teile) - 1 else stat.S_ISDIR(st.st_mode)):
            return None
    return pfad


def _zielordner(konfig: Konfig, match_id: str) -> Path:
    """sessions/<ID>/momente/ im Puffer; vorhandene Teile dürfen keine Links sein. Legt fehlende Ordner an."""
    teile = [str(konfig.wert("speicher.sessions", "sessions")), verarbeitung.pruefe_id(match_id), "momente"]
    pfad = konfig.wurzel
    for teil in teile:
        pfad = pfad / teil
        try:
            st = os.lstat(pfad)
        except FileNotFoundError:
            os.mkdir(pfad)
            continue
        if not stat.S_ISDIR(st.st_mode):  # auch ein Link auf einen Ordner (S_ISLNK)
            raise NachschnittFehler(f"{pfad} ist kein echter Ordner im Puffer (Link?) – nichts geschrieben")
    return pfad


# --- Planen -----------------------------------------------------------------------------------

def _merkmale(text: str) -> dict:
    try:
        mk = json.loads(text or "{}")
    except json.JSONDecodeError:
        return {}
    return mk if isinstance(mk, dict) else {}


def _sekunden(eigene, start_utc: datetime, dauer_s: float, von_s: float = 0.0) -> tuple[list[float], list[float]]:
    """kill_sekunden/aktion_sekunden wie stimmung.py (gleiche Funktion, auf 0,1 s gerundet)."""
    kills, aktionen = stimmung.kill_und_aktion_sekunden(eigene, start_utc, dauer_s)
    return [round(von_s + t, 1) for t in kills], [round(von_s + t, 1) for t in aktionen]


def plane(con: sqlite3.Connection, konfig: Konfig, z: sqlite3.Row, cache: dict) -> tuple[str, Plan | None]:
    """(Ergebnis, Plan). Ergebnis = neu_geschnitten | nur_merkmale | schon_erledigt | ohne_replay |
    ohne_aufnahme | passt_nicht | ohne_quelle; einen Plan gibt es nur für die ersten beiden."""
    mk = _merkmale(z["m_merkmale"])
    eintrag = mk.get(NACHSCHNITT) if isinstance(mk.get(NACHSCHNITT), dict) else None
    if eintrag is not None and eintrag.get("version") == VERSION:
        return "schon_erledigt", None
    eigene = stimmung._eigene_ereignisse(konfig, z["match_id"], cache)  # sessions/<ID>/replay.json, wie stimmung
    if not eigene:
        return "ohne_replay", None
    aufnahme = erfassung.aufnahme_nach_pfad(con, z["quelle_pfad"])
    if aufnahme is None:
        return "ohne_aufnahme", None
    c_start, c_ende = aus_iso(z["c_start_utc"]), aus_iso(z["c_ende_utc"])
    kills = sorted((e.zeit_utc, e.aktion) for e in eigene if e.art == "kill" and c_start <= e.zeit_utc <= c_ende)
    if len(kills) < 2:
        return "passt_nicht", None

    # Bisherige Datei: der Bot-Clip (oder ein älterer Nachschnitt) – ihr Beginn in der Quelle
    alt_start = float(eintrag["quelle_start_s"]) if eintrag and "quelle_start_s" in eintrag else float(z["quelle_start_s"])
    ende = float(z["quelle_ende_s"])
    erste_aktion = aufnahme.sekunde(min(a for _, a in kills))
    vorne = float(konfig.wert("vorbewertung.puffer_vorne_s", 8.0))
    # Ein fremder Kill im Anlauf (gehört zu einem früheren Clip, z. B. ein Einzelkill Sekunden vorher) bliebe sonst
    # im Moment: doppelt im Zusammenschnitt, und sein frühes Umhauen zöge den Regisseur an den Dateianfang.
    # stimmung.py nimmt alle Kills im Fenster – deshalb beginnt das Fenster erst danach (dieselbe Regel wie analyze).
    fremde = [aufnahme.sekunde(e.zeit_utc) for e in eigene if e.art == "kill" and e.zeit_utc < c_start]
    start, ohne_kappung, fremd = anlauf_start(erste_aktion, ende, vorne, fremde, fruehestens=0.0)
    hinweis, gekappt = None, start > ohne_kappung
    if fremd:
        hinweis = (f"{z['schluessel']}: Anlauf beginnt nach einem früheren Kill "
                   f"({erste_aktion - ohne_kappung:.1f} s vor der Aktion)")
        log.info("%s", hinweis)
    if gekappt:
        kappung = (f"{z['schluessel']}: Anlauf ab der ersten Aktion wäre {ende - ohne_kappung:.0f} s lang – "
                   f"vorne auf {MAX_DAUER_S:.0f} s gekappt")
        log.warning("%s", kappung)
        hinweis = f"{hinweis}; {kappung}" if hinweis else kappung
    start = round(start, 3)
    basis = dict(schluessel=z["schluessel"], moment_id=z["moment_id"], clip_id=z["clip_id"], match_id=z["match_id"],
                 quelle_pfad=z["quelle_pfad"], alt_start_s=alt_start, ende_s=ende, hinweis=hinweis, gekappt=gekappt)

    if start >= alt_start - SCHON_DRIN_S:
        # Die Aktion liegt schon in der bisherigen Datei: kein neues Video, nur aktion_sekunden relativ zu ihr
        von = float(z["start_s"])
        zeile_start = aus_iso(z["m_start_utc"]) if z["m_start_utc"] else c_start
        k, a = _sekunden(eigene, zeile_start, float(z["ende_s"]) - von, von)
        return "nur_merkmale", Plan(**basis, quelle=None, start_s=alt_start, start_utc=zeile_start,
                                    kill_sekunden=k, aktion_sekunden=a)

    quelle = _quelle_im_puffer(konfig, z["quelle_pfad"])
    if quelle is None:
        return "ohne_quelle", None  # nur noch im Lager auf pve-big – dort wird nicht nachgesehen
    start_utc = aufnahme.start_utc + timedelta(seconds=start)
    if abs((c_start - aufnahme.start_utc).total_seconds() - float(z["quelle_start_s"])) > 0.5:
        log.warning("%s: Zeit der Aufnahme %s passt nicht zum Clip (> 0,5 s) – Aufnahme gilt",
                    z["schluessel"], z["quelle_pfad"])
    k, a = _sekunden(eigene, start_utc, ende - start)
    name = f"clip{int(z['clip_id']):05d}_{round(start * 1000)}-{round(ende * 1000)}.mp4"
    ziel = konfig.ordner("sessions") / verarbeitung.pruefe_id(z["match_id"]) / "momente" / name
    return "neu_geschnitten", Plan(**basis, quelle=quelle, start_s=start, start_utc=start_utc,
                                   kill_sekunden=k, aktion_sekunden=a, ziel=ziel)


# --- Ausführen --------------------------------------------------------------------------------

def _dauer_passt(ist: float, soll: float) -> bool:
    return abs(ist - soll) <= TOLERANZ_S


def _schneide(con: sqlite3.Connection, konfig: Konfig, plan: Plan) -> tuple[float, bool]:
    """Legt plan.ziel an (nie überschreiben). Gibt (Dauer, übernommen?) zurück."""
    soll = plan.ende_s - plan.start_s
    ordner = _zielordner(konfig, plan.match_id)
    ziel = plan.ziel = ordner / plan.ziel.name  # genau der geprüfte Pfad landet in der Datenbank
    try:
        st = os.lstat(ziel)
    except FileNotFoundError:
        st = None
    if st is not None:
        if not stat.S_ISREG(st.st_mode):
            raise NachschnittFehler(f"{ziel} gibt es schon, aber nicht als Datei – nichts überschrieben")
        ist = medien.probe(ziel).dauer_s
        if not _dauer_passt(ist, soll):
            raise NachschnittFehler(f"{ziel.name} gibt es schon mit {ist:.2f} s statt {soll:.2f} s – nicht überschrieben")
        log.info("%s: %s ist schon da (%.2f s) – übernommen", plan.schluessel, ziel.name, ist)
        return ist, True

    aufnahme = erfassung.aufnahme_nach_pfad(con, plan.quelle_pfad)
    if aufnahme is None:
        raise NachschnittFehler(f"Aufnahme {plan.quelle_pfad} ist nicht mehr erfasst")
    # Eigene Zwischendatei (versteckt, .teil: der Lager-Abgleich lässt sie aus); erst am Ende ein harter Link auf
    # den Zielnamen – os.link überschreibt nie, auch nicht, wenn das Ziel inzwischen aufgetaucht ist.
    tmp = ordner / f".{ziel.stem}.{os.getpid()}.teil{ziel.suffix}"
    try:
        verarbeitung.schneide_aufnahme(konfig, aufnahme, plan.start_s, soll, tmp)
        ist = medien.probe(tmp).dauer_s
        if not _dauer_passt(ist, soll):
            raise NachschnittFehler(f"{ziel.name}: Schnitt hat {ist:.2f} s statt {soll:.2f} s – verworfen")
        try:
            os.link(tmp, ziel)
        except FileExistsError:
            raise NachschnittFehler(f"{ziel.name} ist während des Schnitts aufgetaucht – nicht überschrieben") from None
        except OSError:  # Dateisystem ohne harte Links
            if os.path.lexists(ziel):
                raise NachschnittFehler(f"{ziel.name} gibt es schon – nicht überschrieben") from None
            os.rename(tmp, ziel)
    finally:
        for rest in (tmp, tmp.with_name(tmp.stem + ".tmp" + tmp.suffix)):  # nur eigene Zwischendateien
            rest.unlink(missing_ok=True)
    return ist, False


def _neue_merkmale(mk: dict, plan: Plan, *, alt_datei: str, alt_start_utc: str | None, neu_geschnitten: bool,
                   dauer_s: float | None = None) -> dict:
    neu = dict(mk)
    neu["kill_sekunden"], neu["aktion_sekunden"] = plan.kill_sekunden, plan.aktion_sekunden
    if neu_geschnitten:
        versatz = plan.anlauf_s  # alles aus der alten Datei liegt in der neuen so viel später
        for schluessel in ("spitzen_s", "jubel_laut_s"):
            if isinstance(neu.get(schluessel), list):
                neu[schluessel] = [round(float(t) + versatz, 1) for t in neu[schluessel]]
        if neu.get("tod_sekunde") is not None:
            neu["tod_sekunde"] = round(float(neu["tod_sekunde"]) + versatz, 1)
        neu["dauer_s"] = round(dauer_s, 1)
    neu[NACHSCHNITT] = {
        "version": VERSION, "quelle_pfad": plan.quelle_pfad, "quelle_start_s": plan.start_s,
        "quelle_ende_s": plan.ende_s, "anlauf_s": plan.anlauf_s if neu_geschnitten else 0.0,
        "alt_datei": alt_datei, "alt_start_utc": alt_start_utc, "neu_geschnitten": neu_geschnitten,
        "gekappt_60s": plan.gekappt, "zeit": iso(jetzt()),
    }
    return neu


def fuehre_aus(con: sqlite3.Connection, konfig: Konfig, plan: Plan, z: sqlite3.Row, cache: dict) -> dict:
    """Schneidet (falls nötig) und aktualisiert die momente-Zeile. Tabelle clips und Bot-Clip bleiben."""
    mk = _merkmale(z["m_merkmale"])
    zeit = iso(jetzt())
    if plan.ziel is None:
        neu = _neue_merkmale(mk, plan, alt_datei=z["datei"], alt_start_utc=z["m_start_utc"], neu_geschnitten=False)
        with db.transaktion(con):
            con.execute("UPDATE momente SET merkmale = ?, start_utc = COALESCE(start_utc, ?), geaendert = ? WHERE id = ?",
                        (json.dumps(neu, ensure_ascii=False), iso(plan.start_utc), zeit, plan.moment_id))
        return {"ergebnis": "nur_merkmale"}

    dauer, uebernommen = _schneide(con, konfig, plan)
    # Kills/Aktionen auf die tatsächliche Länge beziehen (wie stimmung.py: Fenster = start_utc … + ende_s)
    plan.kill_sekunden, plan.aktion_sekunden = _sekunden(stimmung._eigene_ereignisse(konfig, plan.match_id, cache),
                                                         plan.start_utc, dauer)
    neu = _neue_merkmale(mk, plan, alt_datei=z["datei"], alt_start_utc=z["m_start_utc"], neu_geschnitten=True,
                         dauer_s=dauer)
    with db.transaktion(con):
        con.execute(
            """UPDATE momente SET datei = ?, start_s = 0, ende_s = ?, start_utc = ?, merkmale = ?, geaendert = ?
                WHERE id = ?""",
            (str(plan.ziel), round(dauer, 3), iso(plan.start_utc), json.dumps(neu, ensure_ascii=False), zeit,
             plan.moment_id))
    log.info("%s: neu geschnitten %s (%.1f s, Anlauf +%.1f s)%s", plan.schluessel, plan.ziel.name, dauer,
             plan.anlauf_s, " – vorhandene Datei übernommen" if uebernommen else "")
    return {"ergebnis": "neu_geschnitten", "dauer_s": round(dauer, 1), "uebernommen": uebernommen}


def _beispiel(z: sqlite3.Row, ergebnis: str, plan: Plan | None = None, **mehr) -> dict:
    b = {"moment": z["schluessel"], "match": z["match_id"], "ergebnis": ergebnis}
    if plan is not None:
        b.update(anlauf_s=round(plan.anlauf_s, 1), start_s=plan.start_s, ende_s=plan.ende_s,
                 aktion_sekunden=plan.aktion_sekunden)
        if plan.ziel is not None:
            b["datei"] = plan.ziel.name
        if plan.hinweis:
            b["hinweis"] = plan.hinweis
    return {**b, **mehr}


def nachschneiden(con: sqlite3.Connection, konfig: Konfig, *, tage: int = 14, probe: bool = False) -> dict:
    """Alle passenden Momente der letzten `tage` Tage. probe=True: nur zeigen, was geschähe (schreibt nichts)."""
    konfig.pruefe_getrennt(mit_lager=False)  # KonfigFehler ohne getrennten Betrieb; nur stat() im Puffer
    ergebnis: dict = {"probe": probe, "tage": tage, **dict.fromkeys(ZAEHLER, 0), "beispiele": []}
    beispiele = ergebnis["beispiele"]
    cache: dict = {}
    grenze = iso(jetzt() - timedelta(days=tage))
    for z in con.execute(AUSWAHL, (grenze,)).fetchall():
        ergebnis["geprueft"] += 1
        try:
            art, plan = plane(con, konfig, z, cache)
        except (verarbeitung.SessionFehler, ValueError, TypeError, KeyError) as e:  # Match-ID, kaputte Zeile
            ergebnis["fehler"] += 1
            log.error("%s: %s", z["schluessel"], e)
            beispiele.append(_beispiel(z, "fehler", fehler=str(e)[:200]))
            continue
        if plan is not None and plan.gekappt:
            ergebnis["gekappt_60s"] += 1
        if plan is None or probe:
            ergebnis[art] += 1
            if art != "schon_erledigt":
                vorhanden = {"ziel_da": True} if plan and plan.ziel and os.path.lexists(plan.ziel) else {}
                beispiele.append(_beispiel(z, art, plan, **vorhanden))
            continue
        try:
            wie = fuehre_aus(con, konfig, plan, z, cache)
        except (MedienFehler, NachschnittFehler, verarbeitung.SessionFehler, OSError) as e:
            ergebnis["fehler"] += 1  # ein kaputter Moment hält die anderen nicht auf
            log.error("%s: %s", z["schluessel"], e)
            beispiele.append(_beispiel(z, "fehler", plan, fehler=str(e)[:200]))
            continue
        ergebnis[wie.pop("ergebnis")] += 1
        beispiele.append(_beispiel(z, art, plan, **wie))
    del beispiele[BEISPIELE_MAX:]
    log.info("Nachschnitt%s: %s geprüft, %s neu geschnitten, %s nur Merkmale, %s schon erledigt, %s ohne Quelle "
             "im Puffer, %s Fehler", " (Probe)" if probe else "", ergebnis["geprueft"], ergebnis["neu_geschnitten"],
             ergebnis["nur_merkmale"], ergebnis["schon_erledigt"], ergebnis["ohne_quelle"], ergebnis["fehler"])
    return ergebnis
