"""pve-big steuern: Status, Herunterfahren, "Halten"-Marken, Frist – das Sicherheitsnetz.

Grundsatz (Sprint-Regel 3): pve-big nur wecken, wenn er danach sicher wieder ausgeht.
  - Herunterfahren läuft per SSH mit eigenem Schlüssel. Auf pve-big ist der Schlüssel per
    command="…/clip-big-steuer" festgenagelt: erlaubt sind nur "status" und "aus" (deploy/big/).
  - Geweckt wird nur, wenn "status" über diesen Weg schon einmal geklappt hat.
  - Nach der Frist ([big].frist) wird nicht mehr geweckt, und der Wächter fährt pve-big einmal
    unbedingt herunter.

Der Wächter (`pipeline big waechter`, systemd-Timer alle 10 min) fährt pve-big herunter, wenn KEIN
Auftrag läuft. "Auftrag" heißt:
  1. eine Halten-Marke ist noch gültig (z. B. Material-Kopie oder finales Rendern), oder
  2. die Pipeline-Sperre (flock) ist belegt (prepare/analyze/…/highlight laufen), oder
  3. pve-big meldet selbst Arbeit: SMB-Verbindungen (Gaming-PC kopiert) oder ein laufender ffmpeg, oder
  4. pve-big ist erst seit kurzem wach (jemand hat ihn gerade geweckt und braucht ihn gleich).
"""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import subprocess
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator

from .konfig import Konfig, sende_wake_on_lan
from .sperre import GEHALTEN, _freigeben, _versuche
from .zeit import aus_iso, iso, jetzt

log = logging.getLogger("pipeline")
NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")


class BigFehler(RuntimeError):
    pass


class WeckenVerboten(BigFehler):
    """Wecken ist nicht erlaubt (Frist vorbei oder Herunterfahren nicht gesichert)."""


# --- Konfiguration ----------------------------------------------------------------

def _b(konfig: Konfig, name: str, standard=None):
    return konfig.wert(f"big.{name}", standard)


def host(konfig: Konfig) -> str:
    return str(_b(konfig, "host", "") or konfig.wert("speicher.host", "") or "").strip()


def frist(konfig: Konfig) -> datetime | None:
    text = str(_b(konfig, "frist", "") or "").strip()
    return aus_iso(text) if text else None


def frist_vorbei(konfig: Konfig, zeit: datetime | None = None) -> bool:
    grenze = frist(konfig)
    return grenze is not None and (zeit or jetzt()) >= grenze


def _datenordner(konfig: Konfig) -> Path:
    return Path(str(_b(konfig, "zustand_ordner", "") or konfig.datenbank.parent))


def halten_ordner(konfig: Konfig) -> Path:
    return _datenordner(konfig) / "big-halten"


def zustand_datei(konfig: Konfig) -> Path:
    return _datenordner(konfig) / "big-zustand.json"


def lies_zustand(konfig: Konfig) -> dict:
    try:
        return json.loads(zustand_datei(konfig).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _schreibe_zustand(konfig: Konfig, **werte) -> None:
    daten = {**lies_zustand(konfig), **werte}
    pfad = zustand_datei(konfig)
    pfad.parent.mkdir(parents=True, exist_ok=True)
    tmp = pfad.with_suffix(".tmp")
    tmp.write_text(json.dumps(daten, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(pfad)


def ssh_befehl(konfig: Konfig) -> list[str]:
    """SSH-Aufruf ohne den Befehl. [big].ssh (Liste) ersetzt alles – praktisch für Tests."""
    if eigen := _b(konfig, "ssh"):
        return [str(x) for x in eigen]
    ziel = str(_b(konfig, "ssh_ziel", "") or "").strip()
    schluessel = str(_b(konfig, "ssh_schluessel", "") or "").strip()
    if not ziel or not schluessel:
        return []
    # known_hosts liegt neben dem Schlüssel: der Dienst darf nicht ins Home-Verzeichnis schreiben
    bekannt = Path(schluessel).with_name("known_hosts")
    return ["ssh", "-i", schluessel, "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
            "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={bekannt}", "-o", "IdentitiesOnly=yes", ziel]


# --- Erreichbarkeit und Fernsteuerung ------------------------------------------------

def wach(konfig: Konfig) -> bool:
    """Ist pve-big an? Kurzer TCP-Versuch auf den SSH-Port (Standard 22)."""
    name = host(konfig)
    if not name:
        return False
    try:
        with socket.create_connection((name, int(_b(konfig, "port", 22))), timeout=2):
            return True
    except OSError:
        return False


def _fern(konfig: Konfig, befehl: str, timeout: float = 20) -> str:
    basis = ssh_befehl(konfig)
    if not basis:
        raise BigFehler("SSH zu pve-big nicht eingerichtet ([big].ssh_ziel / ssh_schluessel fehlen)")
    try:
        ergebnis = subprocess.run(basis + [befehl], capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise BigFehler(f"SSH '{befehl}' fehlgeschlagen: {type(e).__name__}") from None
    if ergebnis.returncode != 0:
        raise BigFehler(f"SSH '{befehl}' Exit {ergebnis.returncode}: {ergebnis.stderr.strip()[-200:]}")
    return ergebnis.stdout


def fern_status(konfig: Konfig) -> dict:
    """Fragt pve-big selbst: {"uptime_s": …, "smb": n, "ffmpeg": n}. Merkt sich den Erfolg."""
    zeilen = [z for z in _fern(konfig, "status").strip().splitlines() if z.strip()]
    try:
        status = json.loads(zeilen[-1])
    except (IndexError, json.JSONDecodeError):
        raise BigFehler("pve-big antwortet auf 'status' nicht mit JSON") from None
    _schreibe_zustand(konfig, status_ok=iso(jetzt()))
    return status


def herunterfahren(konfig: Konfig, grund: str, warten_s: float | None = None) -> bool:
    """Schickt "aus" und wartet, bis pve-big wirklich nicht mehr antwortet. True = aus."""
    log.info("pve-big herunterfahren: %s", grund)
    try:
        _fern(konfig, "aus", timeout=30)
    except BigFehler as e:
        # Verbindungsabbruch während des Herunterfahrens ist normal -> gleich unten prüfen
        log.info("Antwort auf 'aus': %s", e)
    ende = time.monotonic() + float(warten_s if warten_s is not None else _b(konfig, "aus_warten_s", 180))
    while wach(konfig):
        if time.monotonic() > ende:
            log.error("pve-big ist nach 'aus' noch erreichbar")
            return False
        time.sleep(5)
    _schreibe_zustand(konfig, zuletzt_aus=iso(jetzt()), aus_grund=grund)
    return True


# --- Halten-Marken ------------------------------------------------------------------

@dataclass
class Marke:
    name: str
    bis: datetime
    grund: str


def marken(konfig: Konfig, zeit: datetime | None = None) -> list[Marke]:
    """Gültige Halten-Marken. Abgelaufene werden aufgeräumt (sie sind nur eine Zeitangabe)."""
    zeit = zeit or jetzt()
    ordner = halten_ordner(konfig)
    gueltig = []
    for datei in sorted(ordner.glob("*.json")) if ordner.is_dir() else []:
        try:
            daten = json.loads(datei.read_text(encoding="utf-8"))
            marke = Marke(datei.stem, aus_iso(daten["bis"]), str(daten.get("grund", "")))
        except (json.JSONDecodeError, KeyError, ValueError):
            datei.unlink(missing_ok=True)
            continue
        if marke.bis > zeit:
            gueltig.append(marke)
        else:
            datei.unlink(missing_ok=True)
    return gueltig


def setze_marke(konfig: Konfig, name: str, minuten: float, grund: str) -> Marke:
    if not NAME.fullmatch(name):
        raise BigFehler(f"Ungültiger Markenname {name!r}")
    ordner = halten_ordner(konfig)
    ordner.mkdir(parents=True, exist_ok=True)
    marke = Marke(name, jetzt() + timedelta(minutes=minuten), grund)
    (ordner / f"{name}.json").write_text(json.dumps({"bis": iso(marke.bis), "grund": grund}, ensure_ascii=False),
                                         encoding="utf-8")
    return marke


def loese_marke(konfig: Konfig, name: str) -> None:
    if not NAME.fullmatch(name):
        raise BigFehler(f"Ungültiger Markenname {name!r}")
    (halten_ordner(konfig) / f"{name}.json").unlink(missing_ok=True)


def pipeline_beschaeftigt(konfig: Konfig) -> bool:
    """Hält gerade ein ANDERER Pipeline-Schritt die flock-Sperre? (Wir nehmen sie nur probeweise.)
    Hält dieser Prozess sie selbst (z. B. render-entwurf --final), zählt das nicht."""
    pfad = konfig.datenbank.with_suffix(".lock")
    if not pfad.exists() or str(pfad.resolve()) in GEHALTEN:
        return False
    fd = os.open(pfad, os.O_RDWR)
    try:
        if _versuche(fd):
            _freigeben(fd)
            return False
        return True
    finally:
        os.close(fd)


# --- Herzschlag für clip-leerlauf auf pve-big ----------------------------------------------

@contextmanager
def herzschlag(konfig: Konfig, name: str, intervall_s: float = 60.0) -> Iterator[None]:
    """Berührt <speicher>/.aktiv/<rechner>-<name>-<pid> jede Minute, solange der Block läuft.

    clip-leerlauf auf pve-big sieht daran, dass hier gearbeitet wird – auch in Phasen ohne Dateizugriff
    (Whisper, claude -p). Alles läuft in einem Hintergrund-Thread: ein hängender NFS-Mount blockiert den
    eigentlichen Schritt nie. Fehlt die Markierung (Speicher nicht eingehängt), wird nichts geschrieben."""
    datei = konfig.wurzel / ".aktiv" / f"{socket.gethostname()}-{name}-{os.getpid()}"
    stopp = threading.Event()

    def schlagen() -> None:
        geschrieben = False
        while True:
            try:
                if (konfig.wurzel / str(konfig.wert("speicher.markierung", ".clip-speicher"))).is_file():
                    datei.parent.mkdir(exist_ok=True)
                    datei.touch()
                    geschrieben = True
            except OSError:
                pass
            if stopp.wait(intervall_s):
                break
        if geschrieben:
            try:
                datei.unlink(missing_ok=True)
            except OSError:
                pass

    faden = threading.Thread(target=schlagen, name="herzschlag", daemon=True)
    faden.start()
    try:
        yield
    finally:
        stopp.set()
        faden.join(timeout=5)


# --- Wecken -----------------------------------------------------------------------

def darf_wecken(konfig: Konfig, zeit: datetime | None = None) -> str | None:
    """None = Wecken erlaubt, sonst der Grund dagegen."""
    if frist_vorbei(konfig, zeit):
        return f"Frist {_b(konfig, 'frist')} ist vorbei – pve-big wird nicht mehr geweckt"
    if not str(konfig.wert("speicher.wol_mac", "") or "").strip():
        return "keine MAC für Wake-on-LAN ([speicher].wol_mac)"
    if not host(konfig):
        return "kein Host für pve-big ([big].host / [speicher].host)"
    if not ssh_befehl(konfig):
        return "Herunterfahren nicht eingerichtet ([big].ssh_ziel / ssh_schluessel) – wecke nicht"
    status_ok = lies_zustand(konfig).get("status_ok")
    if not status_ok:
        return "Herunterfahren noch nie erfolgreich geprüft – erst `pipeline big pruefen`, wenn pve-big läuft"
    tage = float(_b(konfig, "status_gueltig_tage", 14))
    if (zeit or jetzt()) - aus_iso(status_ok) > timedelta(days=tage):
        return f"Steuerung zuletzt vor über {tage:.0f} Tagen geprüft – erst `pipeline big pruefen`, wenn pve-big läuft"
    return None


@contextmanager
def wach_halten(konfig: Konfig, name: str, grund: str, minuten: float = 120) -> Iterator[bool]:
    """Weckt pve-big (falls nötig und erlaubt), hält ihn für die Aufgabe wach und fährt ihn danach sofort
    herunter – außer jemand anderes braucht ihn noch oder er lief schon vorher.

    Liefert True, wenn pve-big bereitsteht. Wirft WeckenVerboten, wenn er schläft und nicht geweckt werden darf.
    """
    schon_wach = wach(konfig)
    if not schon_wach:
        if grund_dagegen := darf_wecken(konfig):
            raise WeckenVerboten(grund_dagegen)
    setze_marke(konfig, name, minuten, grund)
    schlag = herzschlag(konfig, name)
    try:
        if not schon_wach:
            log.info("wecke pve-big: %s", grund)
            _schreibe_zustand(konfig, zuletzt_geweckt=iso(jetzt()), weck_grund=grund)
            sende_wake_on_lan(str(konfig.wert("speicher.wol_mac")))
            ende = time.monotonic() + float(konfig.wert("speicher.wecken_warten_s", 180))
            while not wach(konfig):
                if time.monotonic() > ende:
                    raise BigFehler("pve-big ist nach Wake-on-LAN nicht aufgewacht")
                time.sleep(5)
            try:
                fern_status(konfig)  # geht das Herunterfahren? Sonst sofort abbrechen
            except BigFehler as e:
                loese_marke(konfig, name)
                herunterfahren(konfig, "Steuerung antwortete nach dem Wecken nicht", warten_s=60)  # Versuch
                raise BigFehler(f"pve-big wach, aber Steuerung antwortet nicht ({e}) – Abbruch") from None
        with schlag:
            yield True
    finally:
        loese_marke(konfig, name)
        if not schon_wach:
            entscheidung = pruefe(konfig)
            if entscheidung.aus:
                herunterfahren(konfig, f"{grund} erledigt")
            else:
                log.info("pve-big bleibt an: %s", entscheidung.grund)


# --- Wächter ----------------------------------------------------------------------

@dataclass
class Entscheidung:
    wach: bool
    aus: bool
    grund: str
    details: dict = field(default_factory=dict)


def pruefe(konfig: Konfig, zeit: datetime | None = None) -> Entscheidung:
    """Soll pve-big jetzt aus? Reine Entscheidung, fährt nichts herunter."""
    zeit = zeit or jetzt()
    if not wach(konfig):
        return Entscheidung(False, False, "schläft")
    zustand = lies_zustand(konfig)
    if frist_vorbei(konfig, zeit) and not zustand.get("frist_aus"):
        return Entscheidung(True, True, "Frist erreicht – einmalig unbedingt aus", {"frist": True})
    if gueltig := marken(konfig, zeit):
        return Entscheidung(True, False, "Halten: " + ", ".join(f"{m.name} bis {m.bis:%H:%M} UTC" for m in gueltig))
    if pipeline_beschaeftigt(konfig):
        return Entscheidung(True, False, "Pipeline-Schritt läuft (Sperre belegt)")
    try:
        status = fern_status(konfig)
    except BigFehler as e:
        return Entscheidung(True, False, f"Status nicht abrufbar: {e}", {"alarm": True})
    mindest = float(_b(konfig, "mindest_wach_min", 20)) * 60
    if float(status.get("uptime_s", 0)) < mindest and not _von_uns_geweckt(zustand, status, zeit):
        return Entscheidung(True, False, f"erst seit {int(status.get('uptime_s', 0)) // 60} min wach", status)
    leerlauf = status.get("leerlauf")
    if isinstance(leerlauf, dict):
        # clip-leerlauf auf pve-big weiß es genauer (echte Zugriffe statt bloßer Verbindungen)
        if leerlauf.get("gruende"):
            return Entscheidung(True, False, "pve-big meldet: " + " · ".join(leerlauf["gruende"][:3]), status)
        if float(leerlauf.get("ruhig_seit_s", 0)) < 120:
            return Entscheidung(True, False, "pve-big: Zugriff vor weniger als 2 min", status)
    elif int(status.get("smb", 0)) > 0:
        return Entscheidung(True, False, f"{status['smb']} SMB-Verbindung(en) – Gaming-PC kopiert?", status)
    if int(status.get("ffmpeg", 0)) > 0:
        return Entscheidung(True, False, "ffmpeg läuft auf pve-big", status)
    return Entscheidung(True, True, "kein Auftrag", status)


def _von_uns_geweckt(zustand: dict, status: dict, zeit: datetime) -> bool:
    """Kam der aktuelle Start von unserem Wake-on-LAN? Dann gilt die Schonfrist nach dem Aufwachen nicht –
    unser Auftrag ist ja vorbei. (WoL geht kurz vor dem Hochfahren raus: 10 min Spielraum.)"""
    if not zustand.get("zuletzt_geweckt"):
        return False
    hochgefahren = zeit - timedelta(seconds=float(status.get("uptime_s", 0)))
    return aus_iso(zustand["zuletzt_geweckt"]) >= hochgefahren - timedelta(minutes=10)


def waechter(konfig: Konfig, zeit: datetime | None = None) -> dict:
    """Ein Durchlauf des Wächters (systemd-Timer). Gibt das Ergebnis als Dict zurück."""
    entscheidung = pruefe(konfig, zeit)
    ergebnis = {"wach": entscheidung.wach, "aus": False, "grund": entscheidung.grund}
    if entscheidung.details.get("alarm"):
        ergebnis["alarm"] = "pve-big läuft, lässt sich aber nicht steuern – bitte von Hand prüfen"
    if entscheidung.aus:
        ok = herunterfahren(konfig, entscheidung.grund)
        ergebnis["aus"] = ok
        if not ok:
            ergebnis["alarm"] = "pve-big reagiert nicht auf 'aus' – bitte von Hand herunterfahren"
        elif entscheidung.details.get("frist"):
            _schreibe_zustand(konfig, frist_aus=iso(jetzt()))
    return ergebnis
