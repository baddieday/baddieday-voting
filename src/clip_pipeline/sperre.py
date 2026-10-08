"""Sperre: nur ein rechenintensiver Schritt gleichzeitig (Vertrag mit n8n).

Linux: flock – der Kernel gibt die Sperre automatisch frei, wenn der Prozess endet,
auch bei einem Absturz. Es bleiben also nie "tote" Sperren übrig.
Windows (nur zum Entwickeln): msvcrt.locking auf dieselbe Datei.

Ein zweiter Lauf WARTET (bis warten_s), statt sofort abzubrechen: n8n startet
render für Session B, während A noch rendert -> B läuft danach.

Mehrbenutzer (M1, 08.10.): EINE Rechen-Sperre für den ganzen Mini. Wo sie liegt, sagt nur pfad(konfig):
[sperre].datei, leer = wie bisher <datenbank>.lock (Florian). Ein Freund hat eine eigene Datenbank, aber dieselbe
Sperrdatei – sonst rechneten zwei Schritte gleichzeitig. Er darf Florians Datei nur lesen; flock braucht kein
Schreibrecht, die Sperre wirkt über O_RDONLY genauso (in beide Richtungen).
"""

from __future__ import annotations

import errno
import logging
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Iterator

if TYPE_CHECKING:
    from .konfig import Konfig

log = logging.getLogger("pipeline")


class Gesperrt(RuntimeError):
    pass


class SperreFehler(RuntimeError):
    """Die Sperrdatei fehlt und lässt sich nicht anlegen (oder gar nicht öffnen). Dann rechnet nichts – eine private
    Ersatzsperre daneben gibt es nie, sie hielte nur sich selbst auf."""


# Diese Fehler beim Öffnen zum Schreiben heißen „nur lesen erlaubt“ – dann reicht O_RDONLY für flock
NUR_LESEN = (errno.EACCES, errno.EPERM, errno.EROFS)

# Sperren, die DIESER Prozess gerade hält. flock auf einem zweiten Dateideskriptor derselben Datei würde auch
# gegen den eigenen Prozess "belegt" melden – so kann big.pipeline_beschaeftigt() den eigenen Lauf erkennen.
GEHALTEN: set[str] = set()


def pfad(konfig: Konfig) -> Path:
    """Die Rechen-Sperre: [sperre].datei, leer oder fehlend = <datenbank>.lock (wie bisher). Ein relativer Pfad gilt
    neben der Datenbank. Die einzige Stelle, die den Pfad ableitet."""
    datei = str(konfig.wert("sperre.datei", "") or "").strip()
    if not datei:
        return konfig.datenbank.with_suffix(".lock")
    eigen = Path(datei).expanduser()
    return eigen if eigen.is_absolute() else konfig.datenbank.parent / eigen


def oeffne(datei: Path, anlegen: bool = True) -> int:
    """Dateideskriptor für flock. Darf dieser Benutzer die Datei nicht schreiben (EACCES, EPERM, EROFS – z. B. ein
    Freund, der Florians Sperrdatei schreibgeschützt eingebunden hat), wird sie nur lesend geöffnet.
    Fehlt die Datei und lässt sie sich nicht anlegen → SperreFehler.
    anlegen=False (big._sperre_belegt): nie anlegen, eine fehlende Datei ergibt FileNotFoundError."""
    datei = Path(datei)
    try:
        if not anlegen:
            return os.open(datei, os.O_RDWR)
        datei.parent.mkdir(parents=True, exist_ok=True)
        return os.open(datei, os.O_RDWR | os.O_CREAT, 0o644)
    except OSError as fehler:
        if fehler.errno not in NUR_LESEN:
            raise
        grund = fehler.strerror
    try:
        return os.open(datei, os.O_RDONLY)
    except FileNotFoundError:
        if not anlegen:
            raise
        raise SperreFehler(f"Sperrdatei {datei} fehlt und lässt sich nicht anlegen ({grund})") from None
    except OSError as fehler:
        raise SperreFehler(f"Sperrdatei {datei} lässt sich nicht öffnen ({fehler.strerror})") from None


def _versuche(fd: int) -> bool:
    if sys.platform == "win32":
        import msvcrt

        try:
            os.lseek(fd, 0, os.SEEK_SET)  # msvcrt sperrt ab der aktuellen Position
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    import fcntl

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False


def _freigeben(fd: int) -> None:
    if sys.platform == "win32":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def sperre(pfad: Path, warten_s: float = 0.0, melde=None) -> Iterator[None]:
    """Hält die Sperre für die Dauer des with-Blocks. warten_s = 0: sofort aufgeben.
    Danach eine Zeile „Sperre gewartet x s, gehalten y s“ ins Log – die Messgrundlage, wie lange Schritte
    aufeinander warten (vor und nach dem ersten Freund, M1)."""
    fd = oeffne(pfad)
    try:
        start = time.monotonic()
        ende = start + warten_s
        gemeldet = False
        while not _versuche(fd):
            if time.monotonic() >= ende:
                raise Gesperrt(f"Ein anderer Lauf ist aktiv (Sperre {pfad})")
            if melde and not gemeldet:
                melde("warte auf laufenden Schritt …")
                gemeldet = True
            time.sleep(1)
        bekommen = time.monotonic()
        GEHALTEN.add(str(Path(pfad).resolve()))
        try:
            yield
        finally:
            GEHALTEN.discard(str(Path(pfad).resolve()))
            _freigeben(fd)
            log.info("Sperre gewartet %.1f s, gehalten %.1f s (%s)", bekommen - start, time.monotonic() - bekommen,
                     Path(pfad).name)
    finally:
        os.close(fd)
