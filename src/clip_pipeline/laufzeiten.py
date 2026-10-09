"""Laufzeiten (Mehrbenutzer, Stufe 3, M139–M142): wie lange jeder Rechenauftrag auf die gemeinsame Rechen-Sperre wartet
und sie hält – und `pipeline laufzeiten`, das es auswertet.

lauf() ersetzt an den sieben Sperr-Stellen (cli.main; Lern-Bot: Bau, Paket, Kalibrieren, KI-Note; Clip-Bot /paket;
benutzer einrichten) den direkten Aufruf von sperre.sperre. Es nimmt die Sperre genauso (sperre.py bleibt, wie es ist),
misst Warten und Halten mit monotoner Zeit und schreibt danach eine Zeile in `ereignisse` der EIGENEN Datenbank:
art 'lauf', match_id = Session (falls es eine gibt), text = JSON, z. B.
  {"was": "render", "ziel": "2026-10-09_20-15-33", "gewartet_s": 0.3, "gehalten_s": 14.2, "ergebnis": "ok",
   "fehler": null}
ergebnis: ok · gesperrt (Sperre nicht bekommen) · fehler; fehler: Klasse der Ausnahme oder „Exit n“. Der Lern-Bot-Bau
schreibt zusätzlich format, stimmung_s, schnitt_s und render_s.

Keine Zeile (M140): leere Läufe (unter 1 s gewartet UND gehalten, ok) und „gesperrt“ bei Aufrufern, die weniger als
60 s warten dürfen (KI-Note 5 s, /paket 0 s – sie versuchen es ohnehin gleich wieder).

Das Protokoll ändert nie, was der Auftrag tut: Gesperrt und SperreFehler fliegen danach unverändert weiter, Exit-Code,
JSON-Zeile und Ergebnis bleiben, jeder Fehler beim Schreiben steht nur im Log. Es ändert auch nie die Transaktion des
Aufrufers (M141): Nur cli.main (schliesst_gleich=True) darf eine offene Transaktion vorher verwerfen – seine Verbindung
wird direkt danach geschlossen, und das verwirft sie heute schon. Bei allen anderen bleibt eine offene Transaktion
unangetastet (kein commit, kein rollback); die Zeile geht dann über eine eigene kurze Verbindung (höchstens 2 s, nur
eine vorhandene Datenbank, mode=rw) – ist die Datenbank belegt, nur eine Logzeile. Ohne offene Transaktion wird über die
Verbindung des Aufrufers geschrieben und sofort festgeschrieben.
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
import statistics
import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator

from . import db, sperre
from .zeit import aus_iso, iso, jetzt

if TYPE_CHECKING:
    from .konfig import Konfig

log = logging.getLogger("pipeline")

ART = "lauf"
LEER_S = 1.0                    # darunter (gewartet UND gehalten, ok) keine Zeile – ein leerer Timer-Lauf
GESPERRT_AB_S = 60.0            # „gesperrt“ schreibt nur, wer mindestens so lange warten darf
GRENZE_S = 2.0                  # so lange darf das Schreiben auf eine belegte Datenbank warten
AUSREISSER_S = 2 * 3600.0       # aus Dateizeiten: Render und ✅ → Upload über 2 h zählen nicht (Nachholen, Hand-Render)
ABEND_HOECHSTENS_S = 24 * 3600.0   # Abend-Video: Nachholen bis 12 h und Nachtrag bis 24 h sind echte Wartezeiten
TEXT_MAX = 80                   # ziel und match_id höchstens so lang (Session-IDs haben höchstens 80 Zeichen)


@dataclass
class Lauf:
    """Was der Aufrufer im with-Block nachträgt: ziel (z. B. die Entwurf-Nummer, sobald es sie gibt), daten (Phasen
    des Lern-Bot-Baus) und über exit_code einen Exit ≠ 0 ohne Ausnahme (cli.main)."""

    was: str
    ziel: Any = None
    daten: dict = field(default_factory=dict)
    code: int = 0

    def exit_code(self, code: int | None) -> None:
        self.code = int(code or 0)


@contextmanager
def lauf(konfig: Konfig, was: str, ziel: Any = None, warten_s: float | None = None,
         con: sqlite3.Connection | None = None, *, match_id: str | None = None, melde=None,
         schliesst_gleich: bool = False) -> Iterator[Lauf]:
    """Rechen-Sperre wie sperre.sperre(sperre.pfad(konfig), warten_s, melde) für die Dauer des with-Blocks, danach
    eine Zeile „lauf“ (siehe oben). warten_s=None: [sperre].warten_s (7200). con: Verbindung des Aufrufers (None =
    eigene kurze Verbindung, die nie eine Datenbank anlegt). schliesst_gleich: Der Aufrufer schließt con direkt danach
    (nur cli.main) – dann darf eine offene Transaktion vor dem Schreiben verworfen werden."""
    warten = float(konfig.wert("sperre.warten_s", 7200)) if warten_s is None else float(warten_s)
    eintrag = Lauf(was, ziel)
    start = time.monotonic()
    bekommen: float | None = None
    ende: float | None = None
    fehler: BaseException | None = None
    try:
        with sperre.sperre(sperre.pfad(konfig), warten_s=warten, melde=melde):
            bekommen = time.monotonic()
            try:
                yield eintrag
            finally:
                ende = time.monotonic()
    except BaseException as e:
        fehler = e
        raise
    finally:
        gewartet = (bekommen if bekommen is not None else time.monotonic()) - start
        gehalten = (ende - bekommen) if bekommen is not None and ende is not None else 0.0
        _abschluss(konfig, con, eintrag, gewartet=gewartet, gehalten=gehalten, bekommen=bekommen is not None,
                   fehler=fehler, warten=warten, match_id=match_id, schliesst_gleich=schliesst_gleich)


def _kurz(wert: Any) -> Any:
    return wert[:TEXT_MAX] if isinstance(wert, str) else wert


def _abschluss(konfig: Konfig, con: sqlite3.Connection | None, eintrag: Lauf, *, gewartet: float, gehalten: float,
               bekommen: bool, fehler: BaseException | None, warten: float, match_id: str | None,
               schliesst_gleich: bool) -> None:
    """Ergebnis bestimmen und – wenn es etwas zu sagen gibt – die Zeile schreiben. Wirft nie (nur Log)."""
    try:
        if fehler is not None and not bekommen and isinstance(fehler, sperre.Gesperrt):
            ergebnis, grund = "gesperrt", None
        elif fehler is not None:
            ergebnis, grund = "fehler", type(fehler).__name__
        elif eintrag.code:
            ergebnis, grund = "fehler", f"Exit {eintrag.code}"
        else:
            ergebnis, grund = "ok", None
        if ergebnis == "gesperrt" and warten < GESPERRT_AB_S:
            return
        if ergebnis == "ok" and gewartet < LEER_S and gehalten < LEER_S:
            return
        kern = {"was": eintrag.was, "ziel": _kurz(eintrag.ziel), "gewartet_s": round(gewartet, 1),
                "gehalten_s": round(gehalten, 1), "ergebnis": ergebnis, "fehler": grund}
        text = json.dumps({**eintrag.daten, **kern}, ensure_ascii=False, default=str)
        schreibe(konfig, con, text, match_id=_kurz(match_id), schliesst_gleich=schliesst_gleich)
    except Exception as e:  # noqa: BLE001 – das Protokoll kostet nie den Auftrag
        log.warning("Laufzeit von %s nicht protokolliert (%s: %s)", eintrag.was, type(e).__name__, e)


def schreibe(konfig: Konfig, con: sqlite3.Connection | None, text: str, *, match_id: str | None = None,
             schliesst_gleich: bool = False) -> None:
    """Eine Zeile „lauf“ – über die Verbindung des Aufrufers, solange dort keine Transaktion offen ist (M141)."""
    if con is not None:
        offen = con.in_transaction
        if offen and schliesst_gleich:
            con.rollback()   # cli.main schließt gleich – das verwürfe die offene Transaktion ohnehin (wie heute)
            offen = False
        if not offen:
            _ueber_aufrufer(con, text, match_id)
            return
    _eigene_verbindung(Path(konfig.datenbank), text, match_id)


def _einfuegen(con: sqlite3.Connection, text: str, match_id: str | None) -> None:
    db.protokoll(con, ART, text, match_id=match_id)


def _ueber_aufrufer(con: sqlite3.Connection, text: str, match_id: str | None) -> None:
    """Auf der Verbindung des Aufrufers (keine Transaktion offen) mit höchstens GRENZE_S Warten auf eine belegte
    Datenbank; danach gilt wieder seine Wartezeit."""
    alt = int(con.execute("PRAGMA busy_timeout").fetchone()[0])
    con.execute(f"PRAGMA busy_timeout = {int(GRENZE_S * 1000)}")
    try:
        _einfuegen(con, text, match_id)
        if con.in_transaction:   # Verbindung ohne Autocommit: genau diese eine Zeile festschreiben
            con.commit()
    except BaseException:
        if con.in_transaction:   # vorher war keine offen – nur die eigene, gescheiterte Zeile zurücknehmen
            con.rollback()
        raise
    finally:
        try:
            con.execute(f"PRAGMA busy_timeout = {alt}")
        except sqlite3.Error:
            pass


def _eigene_verbindung(datenbank: Path, text: str, match_id: str | None) -> None:
    """Kurze eigene Verbindung: nur eine vorhandene Datenbank (mode=rw legt nie eine an), höchstens GRENZE_S Warten.
    Fehlt die Datenbank (benutzer einrichten vor dem Anlegen), still nichts."""
    if not datenbank.is_file():
        return
    con = sqlite3.connect(f"{datenbank.resolve().as_uri()}?mode=rw", uri=True, timeout=GRENZE_S, isolation_level=None)
    try:
        _einfuegen(con, text, match_id)
    finally:
        con.close()


# --- pipeline laufzeiten ------------------------------------------------------------------------------------------

def _zahl(wert: Any) -> float | None:
    if isinstance(wert, bool) or not isinstance(wert, (int, float)) or not math.isfinite(wert):
        return None
    return float(wert)


def _sekunden(text: Any) -> float | None:
    """ISO-Zeit aus der Datenbank als POSIX-Sekunden; None, wenn sie fehlt oder unlesbar ist."""
    try:
        return aus_iso(text).timestamp() if isinstance(text, str) and text else None
    except ValueError:
        return None


def kennzahlen(werte: list[float | None], stellen: int = 1) -> dict | None:
    """Median, p90 (nächster Rang) und Maximum – None ohne Werte. Beispiel [1, 2, 10] → 2 / 10 / 10."""
    werte = sorted(w for w in werte if w is not None)
    if not werte:
        return None
    p90 = werte[max(0, math.ceil(0.9 * len(werte)) - 1)]
    return {"median": round(statistics.median(werte), stellen), "p90": round(p90, stellen),
            "max": round(werte[-1], stellen)}


def _laeufe(con: sqlite3.Connection, seit: str) -> dict[str, list[dict]]:
    gruppen: dict[str, list[dict]] = {}
    for z in con.execute("SELECT text FROM ereignisse WHERE art = ? AND zeit >= ? ORDER BY id", (ART, seit)):
        try:
            e = json.loads(z[0] or "")
        except ValueError:
            continue
        if isinstance(e, dict) and isinstance(e.get("was"), str):
            gruppen.setdefault(e["was"], []).append(e)
    return gruppen


def _auftraege(gruppen: dict[str, list[dict]]) -> dict:
    """Je Auftragsart: n Zeilen, Halten (ohne „gesperrt“) und Warten (alle), Zahl gesperrt und fehler mit Arten."""
    ergebnis = {}
    for was, liste in sorted(gruppen.items()):
        fehler = Counter(str(e.get("fehler")) for e in liste if e.get("ergebnis") == "fehler")
        ergebnis[was] = {
            "n": len(liste),
            "gehalten_s": kennzahlen([_zahl(e.get("gehalten_s")) for e in liste if e.get("ergebnis") != "gesperrt"]),
            "gewartet_s": kennzahlen([_zahl(e.get("gewartet_s")) for e in liste]),
            "gesperrt": sum(1 for e in liste if e.get("ergebnis") == "gesperrt"),
            "fehler": sum(fehler.values()), "fehler_arten": dict(fehler) or None}
    return ergebnis


def _bau(gruppen: dict[str, list[dict]]) -> dict | None:
    """Phasen des Lern-Bot-Baus: Stimmung (Whisper + Lernen), Schnitt (Planen), Render (Rendern + Kritik)."""
    liste = gruppen.get("lernbot-bau") or []
    phasen = {p: kennzahlen([_zahl(e.get(p)) for e in liste]) for p in ("stimmung_s", "schnitt_s", "render_s")}
    return {"n": len(liste), **phasen} if liste else None


def _render(con: sqlite3.Connection, seit: str) -> tuple[dict, int]:
    """Je Encoder und Fassung (entwurf · upload) Sekunden Rechenzeit je Video-Sekunde aus den Sidecars
    `<video>.render.json`. Neu: render_s / dauer_s; ein Rückfall VA-API → CPU zählt unter „h264_vaapi→libx264“ (die
    Zeit enthält den gescheiterten Versuch). Ältere Entwürfe ohne render_s: Sidecar-mtime − entwuerfe.erstellt
    (dauer_s aus der Datenbank), über AUSREISSER_S zählt nicht. Rückgabe (Gruppen, Zahl der Rückfälle)."""
    gruppen: dict[str, dict[str, dict]] = {}
    rueckfaelle = 0
    for z in con.execute("SELECT datei, upload_pfad, erstellt, dauer_s FROM entwuerfe WHERE erstellt >= ?", (seit,)):
        for fassung, pfad in (("entwurf", z["datei"]), ("upload", z["upload_pfad"])):
            if not pfad:
                continue
            sidecar = Path(pfad).with_suffix(".render.json")
            try:
                daten = json.loads(sidecar.read_text(encoding="utf-8"))
                mtime = sidecar.stat().st_mtime
            except (OSError, ValueError):
                continue
            if not isinstance(daten, dict):
                continue
            render_s, dauer = _zahl(daten.get("render_s")), _zahl(daten.get("dauer_s"))
            encoder = str(daten.get("encoder") or "unbekannt")
            if render_s is not None and dauer:
                if daten.get("rueckfall") is True:
                    encoder, rueckfaelle = "h264_vaapi→libx264", rueckfaelle + 1
                wert, alt, mb = render_s / dauer, False, _zahl(daten.get("eingabe_mb"))
            elif fassung == "entwurf" and _zahl(z["dauer_s"]) and (erstellt := _sekunden(z["erstellt"])) is not None:
                if not 0 < mtime - erstellt <= AUSREISSER_S:
                    continue
                wert, alt, mb = (mtime - erstellt) / float(z["dauer_s"]), True, None
            else:
                continue
            g = gruppen.setdefault(encoder, {}).setdefault(fassung, {"werte": [], "alt": 0, "mb": []})
            g["werte"].append(wert)
            g["alt"] += alt
            if mb is not None:
                g["mb"].append(mb)
    ergebnis = {enc: {f: {"n": len(g["werte"]), "s_je_video_s": kennzahlen(g["werte"], 2),
                          "aus_dateizeiten": g["alt"], "eingabe_mb": kennzahlen(g["mb"])}
                      for f, g in sorted(fassungen.items())}
                for enc, fassungen in sorted(gruppen.items())}
    return ergebnis, rueckfaelle


def _ok_bis_upload(con: sqlite3.Connection, seit: str) -> dict | None:
    """✅ → Upload-Fassung fertig: Datei-mtime der Upload-Fassung − Zeit deines ✅ (entwurf_bewertungen.geaendert)."""
    werte, ausgelassen = [], 0
    for z in con.execute("""SELECT e.upload_pfad, b.geaendert FROM entwuerfe e
                              JOIN entwurf_bewertungen b ON b.entwurf_id = e.id
                             WHERE b.daumen = 1 AND e.upload_pfad IS NOT NULL AND b.geaendert >= ?""", (seit,)):
        try:
            fertig = Path(z["upload_pfad"]).stat().st_mtime
        except OSError:
            continue
        if (ok := _sekunden(z["geaendert"])) is None:
            continue
        sekunden = fertig - ok
        if 0 <= sekunden <= AUSREISSER_S:
            werte.append(sekunden)
        else:
            ausgelassen += 1
    if not werte and not ausgelassen:
        return None
    return {"n": len(werte), **(kennzahlen(werte) or {"median": None, "p90": None, "max": None}),
            "ausgelassen": ausgelassen}


def _abend(con: sqlite3.Connection, seit: str) -> dict | None:
    """Abend → Video fertig (Datei-mtime des Abend-Videos): ab „🎮 Abend erkannt“ (Lern-Meldung abend:<name>) und ab
    dem Abend-Ende (sitzungen.ende_utc); dazu wie oft es ab „erkannt“ länger als 30 min dauerte."""
    ab_erkannt, ab_ende, ausgelassen = [], [], 0
    for z in con.execute("""SELECT s.ende_utc, e.datei, m.erstellt AS erkannt FROM sitzungen s
                              JOIN entwuerfe e ON e.id = s.entwurf_id
                              LEFT JOIN lern_meldungen m ON m.schluessel = 'abend:' || s.name
                             WHERE s.verarbeitet >= ? AND e.datei IS NOT NULL""", (seit,)):
        try:
            fertig = Path(z["datei"]).stat().st_mtime
        except OSError:
            continue
        for liste, beginn in ((ab_erkannt, _sekunden(z["erkannt"])), (ab_ende, _sekunden(z["ende_utc"]))):
            if beginn is None:
                continue
            sekunden = fertig - beginn
            if 0 <= sekunden <= ABEND_HOECHSTENS_S:
                liste.append(sekunden)
            elif liste is ab_erkannt:
                ausgelassen += 1
    if not ab_erkannt and not ab_ende and not ausgelassen:
        return None
    return {"n": max(len(ab_erkannt), len(ab_ende)), "ab_erkannt_s": kennzahlen(ab_erkannt),
            "ab_ende_s": kennzahlen(ab_ende), "ueber_30_min": sum(1 for s in ab_erkannt if s > 1800),
            "ausgelassen": ausgelassen}


def _freigabe(con: sqlite3.Connection, seit: str) -> dict:
    """Freigabe-Quote: gezeigte Shorts (gesendet oder bewertet, nicht still aussortiert) mit deinem ✅."""
    z = con.execute("""SELECT COUNT(*), COALESCE(SUM(CASE WHEN b.daumen = 1 THEN 1 ELSE 0 END), 0) FROM entwuerfe e
                         LEFT JOIN entwurf_bewertungen b ON b.entwurf_id = e.id
                        WHERE e.format = 'short' AND e.status IN ('gesendet', 'bewertet') AND e.auto_verworfen IS NULL
                          AND e.erstellt >= ?""", (seit,)).fetchone()
    gezeigt, ok = int(z[0]), int(z[1])
    return {"gezeigt": gezeigt, "ok": ok, "quote": round(ok / gezeigt, 2) if gezeigt else None}


def auswertung(con: sqlite3.Connection, tage: int = 7, bis=None) -> dict:
    """`pipeline laufzeiten [--tage 7]`: nur lesen (Datenbank und Sidecars im Puffer), keine Sperre, weckt nie.
    Fehlende Werte sind null – nichts wird geschätzt."""
    seit = iso((bis or jetzt()) - timedelta(days=tage))
    laeufe = _laeufe(con, seit)
    encoder, rueckfaelle = _render(con, seit)
    return {"tage": tage, "seit": seit, "auftraege": _auftraege(laeufe), "bau": _bau(laeufe), "encoder": encoder,
            "rueckfaelle": rueckfaelle, "ok_bis_upload_s": _ok_bis_upload(con, seit),
            "abend_bis_video": _abend(con, seit), "freigabe": _freigabe(con, seit)}
