"""Morgenprüfung des Puffers (E19, Timer clip-puffer-pruefen 09:30) und `pipeline puffer status`.

Nur im getrennten Betrieb. Weckt pve-big NIE und fasst das Lager nicht an – gelesen werden nur die Tabelle `lager`,
der Puffer (lokal), die Pool-Datei des Hosts ([puffer].pool_status) und sitzungen/pc-status.json vom Gaming-PC.

Themen: lager · platz · pool · pc · samba. Je Thema und Tag höchstens eine Meldung (Schlüssel puffer:<thema>:<Datum>),
montags ein Lebenszeichen (puffer:woche:<JJJJ-Www>) – so heißt Stille eindeutig „alles in Ordnung“.
Verschickt werden die Meldungen vom Clip-Bot, in der Ruhezeit ([telegram].leise_von/leise_bis) erst danach.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

from . import db, lager
from .konfig import Konfig, KonfigFehler
from .zeit import aus_iso, jetzt, utc_zu_lokal

log = logging.getLogger("pipeline")

THEMEN = ("lager", "platz", "pool", "pc", "samba")
POOL_ALT_H = 2       # ältere Pool-Datei: Host-Timer steht o. Ä. – still übergehen, kein Fehlalarm
# Ältere pc-status.json (PC aus): schon geprüft – nicht jeden Morgen wiederholen. 26 statt 24 h: 2 h Spielraum
# für den Timer. Ein Bericht kurz vor der Prüfung kommt so an höchstens zwei Morgen, aber nie gar nicht.
PC_FRISCH_H = 26

Befund = tuple[dict | None, str | None]  # (Stand fürs JSON, Meldungstext oder None = alles gut)


def _zone(konfig: Konfig) -> str:
    return konfig.wert("zeit.zeitzone", "Europe/Berlin")


def _datum(konfig: Konfig, zeit: datetime) -> date:
    return utc_zu_lokal(zeit, _zone(konfig)).date()


def _wann(konfig: Konfig, zeitpunkt: str) -> str:
    return f"{utc_zu_lokal(aus_iso(zeitpunkt), _zone(konfig)):%d.%m. %H:%M}"


def _stunden(dauer: timedelta) -> int:
    return round(dauer.total_seconds() / 3600)


# --- Themen: je (Stand, Text oder None) ----------------------------------------------------

def _lager(con: sqlite3.Connection, konfig: Konfig, zeit: datetime) -> Befund:
    """Puffer-Prüfung, letzter Abgleich mit Datei-Fehlern, oder Offenes ohne Erfolg seit lager_spaetestens_h.
    Ein einzelner Fehlschlag beim Wecken ist meist vorübergehend – gemeldet wird erst, wenn es anhält."""
    stand = lager.status(con, konfig)
    if stand["pruefung"] != "ok":
        return stand, (f"🗄️ Puffer-Prüfung fehlgeschlagen: {stand['pruefung']}\n"
                       "So läuft kein Abgleich ins Lager. Nichts verloren – im Puffer wird nichts gelöscht.\n"
                       "Nächster Schritt: Link /srv/clips und die Marken .clip-puffer/.clip-lager prüfen "
                       "(docs/PUFFER.md, R5), dann pipeline lager status")
    lauf = stand["letzter_lauf"]
    if lauf and lauf["ende"] and lauf.get("fehler"):
        return stand, (f"🗄️ Lager: beim letzten Abgleich ({_wann(konfig, lauf['ende'])}) sind {lauf['fehler']} "
                       "Datei(en) nicht ins Lager gekommen.\n"
                       "Nichts verloren – sie bleiben im Puffer, der nächste Abgleich versucht es erneut.\n"
                       "Nächster Schritt: pipeline lager status (Fehlerliste), dann pipeline lager abgleich")
    if not stand["offen"]:
        return stand, None
    # Bezug: letzter erfolgreicher Abgleich – ohne einen solchen der erste Lauf überhaupt (Übernahme/Abgleich)
    seit = stand["letzter_erfolg"] or con.execute("SELECT MIN(start) FROM lager_laeufe").fetchone()[0]
    grenze = timedelta(hours=float(konfig.wert("puffer.lager_spaetestens_h", 36)))
    if seit is not None and zeit - aus_iso(seit) <= grenze:
        return stand, None
    wie_lange = f"seit {_stunden(zeit - aus_iso(seit))} h kein erfolgreicher Abgleich" if seit else "noch nie ein Abgleich"
    grund = ""
    if lauf and lauf.get("abbruch"):
        grund = f" Letzter Versuch {_wann(konfig, lauf['ende'] or lauf['start'])}: {str(lauf['abbruch'])[:200]}."
    return stand, (f"🗄️ Lager: {stand['offen']} Datei(en) ({stand['offen_gb']:.1f} GB) warten im Puffer – "
                   f"{wie_lange}.{grund}\n"
                   "Nichts verloren – alles liegt sicher im Puffer.\n"
                   "Nächster Schritt: pipeline big status und pipeline lager abgleich --probelauf ansehen, "
                   "dann pipeline lager abgleich von Hand starten")


def _platz(konfig: Konfig) -> Befund:
    if not konfig.wurzel.is_dir():
        return {"hinweis": "Puffer fehlt"}, None  # meldet schon das Thema lager (Puffer-Prüfung)
    frei = shutil.disk_usage(konfig.wurzel).free / 1e9
    warnung = float(konfig.wert("puffer.warnung_frei_gb", 20))
    alarm = float(konfig.wert("puffer.alarm_frei_gb", 8))
    stand = {"frei_gb": round(frei, 1), "warnung_gb": warnung, "alarm_gb": alarm}
    if frei < alarm:
        kopf = f"🚨 Puffer fast voll: nur noch {frei:.1f} GB frei (Alarm unter {alarm:g} GB)."
    elif frei < warnung:
        kopf = f"💾 Puffer wird knapp: noch {frei:.1f} GB frei (Warnung unter {warnung:g} GB)."
    else:
        return stand, None
    return stand, (kopf + "\nNoch ist nichts verloren – ist der Puffer voll, bleiben neue Aufnahmen auf dem "
                   "Gaming-PC liegen.\nNächster Schritt: pipeline lager status (ist alles im Lager?), dann Platz "
                   "schaffen oder den Puffer vergrößern (docs/PUFFER.md) – automatisch gelöscht wird noch nichts")


def lies_pool(pfad: Path) -> dict:
    """lvm-status.txt vom Host (deploy/pve-mini/clip-lvm-status), eine Zeile je Wert:
    zeit=<ISO> · data_prozent=<float> · meta_prozent=<float> · root_frei_gb=<int>."""
    werte = {}
    for zeile in pfad.read_text(encoding="utf-8").splitlines():
        if "=" in zeile:
            name, wert = zeile.split("=", 1)
            werte[name.strip()] = wert.strip()
    ergebnis: dict = {"zeit": werte["zeit"], "data_prozent": float(werte["data_prozent"]),
                      "meta_prozent": float(werte["meta_prozent"])}
    if werte.get("root_frei_gb"):
        ergebnis["root_frei_gb"] = float(werte["root_frei_gb"])
    aus_iso(ergebnis["zeit"])  # ValueError, wenn die Zeit kaputt ist
    return ergebnis


def _pool(konfig: Konfig, zeit: datetime) -> Befund:
    """Thin-Pool des Mini. Datei fehlt, ist unlesbar oder älter als 2 h → still übergehen."""
    name = str(konfig.wert("puffer.pool_status", "") or "").strip()
    if not name:
        return {"hinweis": "[puffer].pool_status leer"}, None
    try:
        werte = lies_pool(Path(name))
    except FileNotFoundError:
        return {"hinweis": f"{name} fehlt"}, None
    except (OSError, KeyError, ValueError) as e:
        log.info("Pool-Status %s unlesbar: %s", name, e)
        return {"hinweis": f"{name} unlesbar"}, None
    if zeit - aus_iso(werte["zeit"]) > timedelta(hours=POOL_ALT_H):
        return {**werte, "hinweis": f"älter als {POOL_ALT_H} h – übergangen"}, None
    warnung = float(konfig.wert("puffer.pool_warnung_prozent", 85))
    alarm = float(konfig.wert("puffer.pool_alarm_prozent", 90))
    daten, meta = werte["data_prozent"], werte["meta_prozent"]
    if max(daten, meta) < warnung:
        return werte, None
    kopf = "🚨 Thin-Pool des Mini fast voll" if max(daten, meta) >= alarm else "💽 Thin-Pool des Mini wird voll"
    return werte, (f"{kopf}: Daten {daten:.0f} %, Metadaten {meta:.0f} % (Warnung ab {warnung:g} %, "
                   f"Alarm ab {alarm:g} %).\nNoch ist nichts verloren – läuft der Pool voll, bleiben aber alle "
                   "Container des Mini stehen.\nNächster Schritt: auf pve-mini lvs pve/data ansehen und Platz "
                   "schaffen (alte Snapshots/Backups), notfalls den Puffer verkleinern (docs/PUFFER.md)")


def _pc(konfig: Konfig, zeit: datetime) -> Befund:
    """pc-status.json vom Gaming-PC: Kopierfehler oder Stau (älteste wartende Datei älter als pc_stau_h, gemessen
    zur Zeit des Berichts). Datei fehlt → nichts. Älter als PC_FRISCH_H (PC aus) → schon geprüft, nichts.
    Warum zur Zeit des Berichts: Geht der PC gleich nach dem Spielen aus, stehen oft noch junge oder offene Dateien
    im Bericht – sie kommen beim nächsten Einschalten. An der aktuellen Uhrzeit gemessen käme nach jeder Pause über
    pc_stau_h eine Meldung. Eine Datei, die wirklich hängt, zeigt der nächste Bericht (nächster Spielabend) als Stau."""
    pfad = konfig.wurzel / str(konfig.wert("puffer.pc_status_datei", "sitzungen/pc-status.json"))
    try:
        daten = json.loads(pfad.read_text(encoding="utf-8-sig"))
        bericht = aus_iso(daten["zeit_utc"])
        fehler = int(daten.get("fehler") or 0)
        offen = [(e.get("quelle") or "?", int(e.get("anzahl") or 0), aus_iso(e["aelteste_utc"]))
                 for e in daten.get("offen") or []]
    except FileNotFoundError:
        return None, None
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        log.warning("%s unlesbar: %s", pfad, e)
        return {"hinweis": f"{pfad.name} unlesbar: {e}"}, None
    stau_h = float(konfig.wert("puffer.pc_stau_h", 24))
    stau = [(quelle, anzahl, bericht - aelteste) for quelle, anzahl, aelteste in offen
            if bericht - aelteste > timedelta(hours=stau_h)]
    stand = {"zeit_utc": daten["zeit_utc"], "rechner": daten.get("rechner"), "fehler": fehler,
             "offen": sum(anzahl for _q, anzahl, _a in offen), "stau": sum(anzahl for _q, anzahl, _a in stau)}
    if zeit - bericht > timedelta(hours=PC_FRISCH_H):
        return {**stand, "hinweis": f"älter als {PC_FRISCH_H} h (PC aus?) – schon geprüft"}, None
    teile = []
    if fehler:
        text = f"beim letzten Lauf ({_wann(konfig, daten['zeit_utc'])}) {fehler} Datei(en) nicht übertragen"
        if daten.get("letzter_fehler"):
            text += f" – {str(daten['letzter_fehler'])[:200]}"
        teile.append(text)
    if stau:
        quelle, _anzahl, alter = max(stau, key=lambda s: s[2])
        teile.append(f"{stand['stau']} Datei(en) warten seit über {stau_h:g} h auf die Übertragung "
                     f"(die älteste seit {_stunden(alter)} h, {quelle})")
    if not teile:
        return stand, None
    wer = f"Gaming-PC ({daten['rechner']})" if daten.get("rechner") else "Gaming-PC"
    return stand, (f"🖥️ {wer}: " + "; ".join(teile) + ".\n"
                   "Nichts verloren – die Dateien liegen noch auf dem PC.\n"
                   "Nächster Schritt: am PC das Log von Uebertragung.ps1 ansehen (läuft die Aufgabe? ist eine Datei "
                   "noch geöffnet? erreicht der PC den Puffer?)")


def _samba_aktiv() -> bool | None:
    """True/False = smbd läuft (nicht). None = kein systemd oder kein Samba installiert – dann gibt es nichts zu prüfen."""
    if not shutil.which("systemctl"):
        return None
    suchpfad = os.pathsep.join(filter(None, [os.environ.get("PATH", ""), "/usr/sbin", "/sbin"]))
    if not shutil.which("smbd", path=suchpfad):
        return None
    try:
        ergebnis = subprocess.run(["systemctl", "is-active", "--quiet", "smbd"], timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("systemctl is-active smbd: %s", e)
        return None
    return ergebnis.returncode == 0


def _samba() -> Befund:
    aktiv = _samba_aktiv()
    if aktiv is None:
        return {"smbd": "nicht installiert"}, None
    if aktiv:
        return {"smbd": "aktiv"}, None
    return {"smbd": "läuft nicht"}, (
        "📁 Samba (smbd) läuft nicht – der Gaming-PC kann gerade nichts in den Puffer kopieren.\n"
        "Nichts verloren – die Aufnahmen warten auf dem PC.\n"
        "Nächster Schritt: im CT systemctl status smbd ansehen, dann systemctl restart smbd")


# --- Status und Morgenprüfung ----------------------------------------------------------------

def _getrennt(konfig: Konfig) -> None:
    if not konfig.getrennt:
        raise KonfigFehler("kein getrennter Betrieb: [lager].wurzel leer")


def status(con: sqlite3.Connection, konfig: Konfig, zeit: datetime | None = None) -> dict:
    """Alle Themen und was die Morgenprüfung melden würde (befunde). Schreibt nichts, weckt nie.
    Ein Thema, das sich nicht prüfen lässt (Programmfehler), landet in fehler – die anderen laufen weiter."""
    _getrennt(konfig)
    zeit = zeit or jetzt()
    pruefungen: dict[str, Callable[[], Befund]] = {
        "lager": lambda: _lager(con, konfig, zeit), "platz": lambda: _platz(konfig),
        "pool": lambda: _pool(konfig, zeit), "pc": lambda: _pc(konfig, zeit), "samba": _samba,
    }
    stand: dict = {"getrennt": True, "befunde": {}, "fehler": []}
    for thema in THEMEN:
        try:
            stand[thema], text = pruefungen[thema]()
        except Exception as e:  # ein kaputtes Thema darf die anderen nicht verschlucken
            log.exception("Morgenprüfung: Thema %s", thema)
            stand[thema] = {"fehler": f"{type(e).__name__}: {e}"}
            stand["fehler"].append(thema)
            text = (f"⚠️ Morgenprüfung: „{thema}“ ließ sich nicht prüfen ({type(e).__name__}: {str(e)[:150]}).\n"
                    "Nächster Schritt: pipeline puffer status")
        if text:
            stand["befunde"][thema] = text
    lager_stand = stand["lager"] if isinstance(stand["lager"], dict) else {}
    stand["zeile"] = lager_stand.get("zeile") or "Lager: Stand nicht lesbar"
    return stand


def _abgleich_hat_gemeldet(con: sqlite3.Connection, stand: dict, tag: date) -> bool:
    """Hat sich der Abgleich heute schon selbst gemeldet (lager:<Datum>), meldet das Thema lager nicht dasselbe noch
    einmal. Eine fehlgeschlagene Puffer-Prüfung ist nur dann „dasselbe“, wenn der letzte Abgleich an genau ihr
    abgebrochen ist – sonst (z. B. Meldung nur wegen Rohdaten-Konflikten, danach Puffer nicht eingehängt) käme die
    Warnung erst mit dem nächsten Abgleich, fast einen Tag später."""
    if not con.execute("SELECT 1 FROM meldungen WHERE schluessel = ?", (f"lager:{tag.isoformat()}",)).fetchone():
        return False
    lager_stand = stand.get("lager") or {}
    pruefung = lager_stand.get("pruefung")
    if pruefung == "ok":  # Befund zum Abgleich selbst (Datei-Fehler, Abbruch): hat er schon gemeldet
        return True
    return pruefung is not None and (lager_stand.get("letzter_lauf") or {}).get("abbruch") == pruefung


def melde(con: sqlite3.Connection, konfig: Konfig, stand: dict, zeit: datetime | None = None) -> list[str]:
    """Befunde aus status() als Meldungen – je Thema und Tag höchstens eine; montags das Lebenszeichen.
    Liefert die Schlüssel der neu angelegten Meldungen."""
    tag = _datum(konfig, zeit or jetzt())
    neu = []
    for thema, text in stand["befunde"].items():
        if thema == "lager" and _abgleich_hat_gemeldet(con, stand, tag):
            continue
        schluessel = f"puffer:{thema}:{tag.isoformat()}"
        if db.meldung(con, schluessel, text):
            log.warning("%s", text.replace("\n", " "))
            neu.append(schluessel)
    if tag.weekday() == 0:  # Montag: Lebenszeichen, damit Stille eindeutig ist
        jahr, woche, _ = tag.isocalendar()
        schluessel = f"puffer:woche:{jahr}-W{woche:02d}"
        if db.meldung(con, schluessel, f"💚 Wochen-Lebenszeichen: {stand['zeile']}\n"
                                        "Kommt sonst nichts, ist alles in Ordnung."):
            neu.append(schluessel)
    return neu


def pruefe_morgens(con: sqlite3.Connection, konfig: Konfig, zeit: datetime | None = None) -> list[str]:
    """Morgenprüfung (Timer 09:30): status() + melde(). Liefert die Schlüssel der neuen Meldungen."""
    zeit = zeit or jetzt()
    return melde(con, konfig, status(con, konfig, zeit), zeit)
