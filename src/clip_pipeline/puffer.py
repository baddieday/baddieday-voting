"""Morgenprüfung des Puffers (E19, Timer clip-puffer-pruefen 11:00) und `pipeline puffer status`.

Nur im getrennten Betrieb. Weckt pve-big NIE und fasst das Lager nicht an – gelesen werden nur die Tabellen `lager`
und `lager_laeufe` (dort auch der beim Abgleich gemessene Platz im Lager), der Puffer (lokal), die Pool-Datei des
Hosts ([puffer].pool_status) und sitzungen/pc-status.json vom Gaming-PC.

Themen: lager · platz · lager_platz · pool · pc · samba. Je Thema und Tag höchstens eine Meldung
(Schlüssel puffer:<thema>:<Datum>), montags ein Lebenszeichen (puffer:woche:<JJJJ-Www>) – so heißt Stille eindeutig
„alles in Ordnung“.
Verschickt werden die Meldungen vom Clip-Bot, in der Ruhezeit ([telegram].leise_von/leise_bis) erst danach.

Mehrbenutzer (Stufe 2, M131/M132): Bei Florian kommt das Thema freunde dazu – nur, wenn [puffer].freunde_volume ein
eigenes Dateisystem ist (das Freunde-Volume). Gemessen wird dort nur der freie Platz (statvfs), hineingeschaut wird nie;
dazu liest die Prüfung die Zusammenfassung des Lager-Rundgangs der Freunde (lager-freunde.json). Ohne Freunde-Volume
gibt es das Thema nicht, die Ausgabe bleibt Zeichen für Zeichen wie vorher.
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
from .konfig import INSTANZ_NAME, Konfig, KonfigFehler
from .zeit import aus_iso, jetzt, utc_zu_lokal

log = logging.getLogger("pipeline")

THEMEN = ("lager", "platz", "lager_platz", "pool", "pc", "samba")
FREUNDE = "freunde"  # nur mit Freunde-Volume, hinter THEMEN (M131)
POOL_ALT_H = 2       # ältere Pool-Datei: Host-Timer steht o. Ä. – still übergehen, kein Fehlalarm
# Ältere pc-status.json (PC aus): schon geprüft – nicht jeden Morgen wiederholen. 26 statt 24 h: 2 h Spielraum
# für den Timer. Ein Bericht kurz vor der Prüfung kommt so an höchstens zwei Morgen, aber nie gar nicht.
PC_FRISCH_H = 26
# Lager-Lauf eines Freundes mit Fehler: gemeldet, wenn er höchstens so alt ist – die Läufe enden meist zwischen 10 und
# 11 Uhr, 26 h hieße dann fast immer zweimal. Was danach noch klemmt, meldet die Tage-Regel (freunde_lager_tage).
FREUNDE_FEHLER_FRISCH_H = 24

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
                       "So läuft kein Abgleich ins Lager. Nichts verloren – ohne Abgleich wird im Puffer nichts "
                       "gelöscht.\n"
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
    elif lauf and lauf.get("nachtruhe"):
        grund = (f" Letzter Versuch {_wann(konfig, lauf['ende'] or lauf['start'])}: in der Nachtruhe, pve-big schlief "
                 "und wurde nicht geweckt.")
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
    if konfig.wert("puffer.freigeben", False) is True:  # Stufe B5: alte Rohvideos gehen nur nach dem Abgleich raus
        weiter = ("Nächster Schritt: pipeline lager status (ist alles im Lager?). Rohvideos über "
                  f"{konfig.wert('puffer.rohdaten_tage', 14)} Tage löscht der tägliche Abgleich selbst vom Mini, "
                  "sobald alles im Lager ist (Kopie geprüft) – reicht das nicht, den Puffer vergrößern "
                  "(docs/PUFFER.md)")
    else:
        weiter = ("Nächster Schritt: pipeline lager status (ist alles im Lager?), dann Platz schaffen oder den Puffer "
                  "vergrößern (docs/PUFFER.md) – automatisch gelöscht wird nichts ([puffer].freigeben = false)")
    return stand, (kopf + "\nNoch ist nichts verloren – ist der Puffer voll, bleiben neue Aufnahmen auf dem "
                   "Gaming-PC liegen.\n" + weiter)


def _lager_platz(con: sqlite3.Connection, konfig: Konfig) -> Befund:
    """Platz im Lager auf pve-big – zuletzt beim Abgleich gemessen (statvfs), hier nur aus der Tabelle: selbst messen
    hieße pve-big wecken. Noch keine Messung → still. Gelöscht wird auch im Lager nie etwas."""
    messung = lager.letzte_messung(con)
    if messung is None:
        return {"hinweis": "noch nicht gemessen (erst beim Abgleich, der pve-big braucht)"}, None
    frei = float(messung["frei_gb"])
    warnung = float(konfig.wert("puffer.lager_warnung_frei_gb", 200))
    alarm = float(konfig.wert("puffer.lager_alarm_frei_gb", 50))
    stand = {**messung, "warnung_gb": warnung, "alarm_gb": alarm}
    platz = f"{frei:.0f} GB frei" + (f" von {messung['gesamt_gb']:.0f} GB" if messung.get("gesamt_gb") else "")
    wann = f"gemessen beim Abgleich am {_wann(konfig, messung['zeit'])}"
    if frei < alarm:
        kopf = f"🚨 Lager auf pve-big fast voll: nur noch {platz} (Alarm unter {alarm:g} GB, {wann})."
    elif frei < warnung:
        kopf = f"🗄️ Lager auf pve-big wird knapp: noch {platz} (Warnung unter {warnung:g} GB, {wann})."
    else:
        return stand, None
    return stand, (kopf + "\nNichts verloren, nichts gelöscht – ist das Lager voll, bleibt Neues im Puffer liegen, bis "
                   "wieder Platz ist (dann füllt sich aber der Puffer).\nNächster Schritt: bitte Platz auf pve-big "
                   "schaffen oder die Platte erweitern (docs/PUFFER.md, „Im Alltag“). Der nächste Abgleich misst neu")


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


# --- Thema freunde (Mehrbenutzer, Stufe 2) -------------------------------------------------------

def _eingehaengt(pfad: Path) -> bool:
    """Eigenes Dateisystem (Einhängepunkt: anderes Gerät als der Ordner darüber)? Nur stat() auf den Ordner und auf
    „..“ – nie ein Blick hinein. Eigene Funktion, damit Tests sie ersetzen können (Temp-Ordner liegen auf einem
    Dateisystem)."""
    return os.path.ismount(pfad)


def _freunde_volume(konfig: Konfig) -> Path | None:
    """Das Freunde-Volume (deploy/pve-mini/freunde-volume.sh, im CT /var/lib/clip-benutzer) – nur bei Florian und nur,
    wenn [puffer].freunde_volume ein eigenes Dateisystem ist. Sonst None: Das Thema freunde gibt es dann nicht."""
    if konfig.instanz is not None:  # die Instanz eines Freundes prüft nie die anderen
        return None
    name = str(konfig.wert("puffer.freunde_volume", "") or "").strip()
    if not name:
        return None
    pfad = Path(name)
    return pfad if _eingehaengt(pfad) else None


def _text(wert, laenge: int = 200) -> str:
    """Text aus der Zusammenfassung (fremde Eingabe): nur druckbare Zeichen, gekürzt."""
    return "".join(z for z in str(wert or "") if z.isprintable())[:laenge]


def _zeitpunkt(wert) -> datetime | None:
    if not isinstance(wert, str) or not wert:
        return None
    try:
        return aus_iso(wert)
    except ValueError:
        return None


def _freunde_platz(konfig: Konfig, volume: Path) -> tuple[dict, str | None]:
    """Freier Platz auf dem Freunde-Volume – shutil.disk_usage ist statvfs, gelesen wird kein Ordner und keine Datei."""
    platte = shutil.disk_usage(volume)
    frei, gesamt = platte.free / 1e9, platte.total / 1e9
    warnung = float(konfig.wert("puffer.freunde_warnung_frei_gb", 15))
    alarm = float(konfig.wert("puffer.freunde_alarm_frei_gb", 5))
    stand = {"frei_gb": round(frei, 1), "gesamt_gb": round(gesamt, 1), "warnung_gb": warnung, "alarm_gb": alarm}
    platz = f"{frei:.1f} GB frei von {gesamt:.0f} GB"
    if frei < alarm:
        return stand, f"🚨 Speicher der Freunde fast voll: nur noch {platz} (Alarm unter {alarm:g} GB)."
    if frei < warnung:
        return stand, f"👥 Speicher der Freunde wird knapp: noch {platz} (Warnung unter {warnung:g} GB)."
    return stand, None


def _freunde_lager(konfig: Konfig, zeit: datetime) -> tuple[dict, list[str], list[str], str]:
    """Lager der Freunde aus der Zusammenfassung des Rundgangs (lager-freunde.json, deploy/benutzer/lager-freunde.sh).
    Je Freund mit Lager, der nicht stillgelegt ist, höchstens eine Zeile:
    - sein letzter Lauf ging nicht und ist höchstens FREUNDE_FEHLER_FRISCH_H alt (Exit 3/4 = pve-big ging aus bzw. sein
      Abgleich lief schon – das holt das nächste Mal nach, dafür gibt es keine Zeile), oder
    - seit freunde_lager_tage kein guter Lauf. Bezug: der letzte gute Lauf bzw. seit wann er mit Lager dabei ist (das
      jüngere von beiden – nach dem Einschalten oder Stilllegen zählt die Zeit neu). pve-big wird spätestens alle
      7 Tage geweckt (sicherung_wecken_tage), 8 Tage heißt also: Da klemmt etwas.
    Die Datei schreibt root; sie gilt trotzdem als fremde Eingabe: nur bekannte Felder, Namen nach dem Muster, Texte
    gekürzt. Fehlt sie oder ist sie unlesbar: keine Zeile, nur ein Hinweis im Stand.
    Liefert (Stand, Zeilen, Namen der Freunde mit Zeile, Zeile zum letzten Rundgang)."""
    name = str(konfig.wert("puffer.freunde_lager_status", "") or "").strip()
    if not name:
        return {"hinweis": "[puffer].freunde_lager_status leer"}, [], [], ""
    pfad = Path(name)
    try:
        daten = json.loads(pfad.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"hinweis": f"{pfad.name} fehlt – noch kein Rundgang (kein Freund mit Lager?)"}, [], [], ""
    except (OSError, ValueError) as e:
        log.warning("%s unlesbar: %s", pfad, e)
        return {"hinweis": f"{pfad.name} unlesbar"}, [], [], ""
    daten = daten if isinstance(daten, dict) else {}
    mit_lager = daten.get("mit_lager")
    if not isinstance(mit_lager, dict):
        return {"hinweis": f"{pfad.name} ohne mit_lager – noch kein Rundgang mit Stufe 2, Schritt 6"}, [], [], ""
    freunde = daten["freunde"] if isinstance(daten.get("freunde"), dict) else {}
    lauf = daten["lauf"] if isinstance(daten.get("lauf"), dict) else {}
    grenze = timedelta(days=float(konfig.wert("puffer.freunde_lager_tage", 8)))
    frisch = timedelta(hours=FREUNDE_FEHLER_FRISCH_H)
    zeilen: list[str] = []
    namen: list[str] = []
    aktiv: list[str] = []
    for freund in sorted(n for n in mit_lager if isinstance(n, str) and INSTANZ_NAME.fullmatch(n)):
        info = mit_lager[freund] if isinstance(mit_lager[freund], dict) else {}
        if info.get("stillgelegt") is True:
            continue
        aktiv.append(freund)
        seit = _zeitpunkt(info.get("seit"))
        e = freunde.get(freund) if isinstance(freunde.get(freund), dict) else {}
        ende = _zeitpunkt(e.get("ende"))
        gut = _zeitpunkt(e.get("zuletzt_ok"))
        gut_text = f"zuletzt gesichert am {_wann(konfig, e['zuletzt_ok'])}" if gut else "noch nie gesichert"
        if (e.get("ok") is not True and e.get("exit") not in (3, 4) and ende is not None
                and zeit - ende <= frisch and (seit is None or ende >= seit)):
            code = e.get("exit")
            gestartet = isinstance(code, int) and not isinstance(code, bool) and code != 0
            wie = f"Exit {code}" if gestartet else "nicht gestartet"
            hinweis = _text(e.get("hinweis")).rstrip(". ")
            zeilen.append(f"🗄️ Lager von {freund}: letzter Lauf am {_wann(konfig, e['ende'])} ging nicht ({wie}, "
                          f"{gut_text})" + (f" – {hinweis}" if hinweis else "") + ".")
            namen.append(freund)
            continue
        bezug = max((t for t in (gut, seit) if t is not None), default=None)
        if bezug is None or zeit - bezug <= grenze:
            continue
        tage = int((zeit - bezug).total_seconds() // 86400)
        if gut and bezug == gut:
            zeilen.append(f"🗄️ Lager von {freund}: seit {tage} Tagen nicht gesichert ({gut_text}).")
        else:
            zeilen.append(f"🗄️ Lager von {freund}: seit {tage} Tagen mit Lager dabei, aber seitdem nicht gesichert "
                          f"({gut_text}).")
        namen.append(freund)
    stand = {"datei": str(pfad), "mit_lager": aktiv, "zeilen": len(zeilen)}
    rundgang = ""
    if _zeitpunkt(lauf.get("ende")):
        stand["rundgang"] = {"ende": lauf["ende"], "ergebnis": _text(lauf.get("ergebnis"))}
        rundgang = (f"Letzter Rundgang der Freunde am {_wann(konfig, lauf['ende'])}: "
                    f"{_text(lauf.get('ergebnis')) or '?'}.")
    return stand, zeilen, namen, rundgang


def _freunde(konfig: Konfig, volume: Path, zeit: datetime) -> Befund:
    """Thema freunde: Platz auf dem Freunde-Volume und das Lager der Freunde – eine Meldung für beides."""
    platz_stand, platz_kopf = _freunde_platz(konfig, volume)
    lager_stand, zeilen, namen, rundgang = _freunde_lager(konfig, zeit)
    stand = {"volume": str(volume), **platz_stand, "lager": lager_stand}
    if not platz_kopf and not zeilen:
        return stand, None
    koepfe, ruhig, schritte = [], [], []
    if platz_kopf:
        koepfe.append(platz_kopf)
        ruhig.append("wird es zu knapp, holt der Mini nichts mehr ab – die Aufnahmen warten im Briefkasten und auf den "
                     "PCs der Freunde, dein Puffer ist davon nicht betroffen")
        schritte.append("auf pve-mini lvs pve/data ansehen und das Freunde-Volume vergrößern (nur wachsen), z. B. "
                        f"pct resize 102 mp2 +50G (docs/MEHRBENUTZER.md); wer wie viel belegt: im CT du -sh {volume}/*")
    if zeilen:
        koepfe += zeilen + ([rundgang] if rundgang else [])
        ruhig.append("ohne Lager bleiben ihre Aufnahmen im Puffer des Freundes, freigegeben wird dort nur, was geprüft "
                     "im Lager liegt")
        andere = f" (ebenso für {', '.join(namen[1:])})" if len(namen) > 1 else ""
        schritte.append(f"im CT bash /opt/clip-pipeline/deploy/benutzer/benutzer-pruefen.sh {namen[0]}{andere} "
                        f"(Abschnitt Lager), dann journalctl -u clip-freund-lager@{namen[0]} -n 50")
    return stand, ("\n".join(koepfe) + "\nNichts verloren – " + "; ".join(ruhig) + ".\nNächster Schritt: "
                   + "; ".join(schritte))


# --- Status und Morgenprüfung ----------------------------------------------------------------

def _getrennt(konfig: Konfig) -> None:
    if not konfig.getrennt:
        raise KonfigFehler("kein getrennter Betrieb: [lager].wurzel leer")


def status(con: sqlite3.Connection, konfig: Konfig, zeit: datetime | None = None) -> dict:
    """Alle Themen und was die Morgenprüfung melden würde (befunde). Schreibt nichts, weckt nie.
    Ein Thema, das sich nicht prüfen lässt (Programmfehler), landet in fehler – die anderen laufen weiter.
    freunde kommt nur mit Freunde-Volume dazu (als letztes Thema) – ohne bleibt alles wie vorher."""
    _getrennt(konfig)
    zeit = zeit or jetzt()
    pruefungen: dict[str, Callable[[], Befund]] = {
        "lager": lambda: _lager(con, konfig, zeit), "platz": lambda: _platz(konfig),
        "lager_platz": lambda: _lager_platz(con, konfig), "pool": lambda: _pool(konfig, zeit),
        "pc": lambda: _pc(konfig, zeit), "samba": _samba,
    }
    themen = THEMEN
    volume = _freunde_volume(konfig)
    if volume is not None:
        pruefungen[FREUNDE] = lambda: _freunde(konfig, volume, zeit)
        themen = THEMEN + (FREUNDE,)
    stand: dict = {"getrennt": True, "befunde": {}, "fehler": []}
    for thema in themen:
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


def _abgleich_hat_gemeldet(con: sqlite3.Connection, konfig: Konfig, stand: dict, tag: date) -> bool:
    """Hat sich der letzte Abgleich heute schon selbst gemeldet, meldet das Thema lager nicht dasselbe noch
    einmal. Alte Tagesmeldungen bleiben gültig. Eine fehlgeschlagene Puffer-Prüfung ist nur dann „dasselbe“, wenn
    der letzte Abgleich an genau ihr abgebrochen ist – sonst (z. B. Meldung nur wegen Rohdaten-Konflikten, danach
    Puffer nicht eingehängt) käme die
    Warnung erst mit dem nächsten Abgleich, fast einen Tag später."""
    lager_stand = stand.get("lager") or {}
    lauf = lager_stand.get("letzter_lauf") or {}
    gemeldet = con.execute("SELECT 1 FROM meldungen WHERE schluessel = ?",
                           (f"lager:{tag.isoformat()}",)).fetchone()
    if not gemeldet and lauf.get("ende") and _datum(konfig, aus_iso(lauf["ende"])) == tag:
        zeile = con.execute("SELECT id FROM lager_laeufe WHERE art = 'abgleich' AND start = ? AND ende = ? "
                            "ORDER BY id DESC LIMIT 1", (lauf["start"], lauf["ende"])).fetchone()
        if zeile:
            gemeldet = con.execute("SELECT 1 FROM meldungen WHERE schluessel = ?",
                                   (f"uebertragung:lager:{zeile['id']}:ende",)).fetchone()
    if not gemeldet:
        return False
    pruefung = lager_stand.get("pruefung")
    if pruefung == "ok":  # Befund zum Abgleich selbst (Datei-Fehler, Abbruch): hat er schon gemeldet
        return True
    return pruefung is not None and lauf.get("abbruch") == pruefung


def melde(con: sqlite3.Connection, konfig: Konfig, stand: dict, zeit: datetime | None = None) -> list[str]:
    """Befunde aus status() als Meldungen – je Thema und Tag höchstens eine; montags das Lebenszeichen.
    Liefert die Schlüssel der neu angelegten Meldungen."""
    tag = _datum(konfig, zeit or jetzt())
    neu = []
    for thema, text in stand["befunde"].items():
        if thema == "lager" and _abgleich_hat_gemeldet(con, konfig, stand, tag):
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
    """Morgenprüfung (Timer 11:00): status() + melde(). Liefert die Schlüssel der neuen Meldungen."""
    zeit = zeit or jetzt()
    return melde(con, konfig, status(con, konfig, zeit), zeit)
