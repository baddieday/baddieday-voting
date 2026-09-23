"""Sperre: nur ein rechenintensiver Schritt gleichzeitig (Vertrag mit n8n).

Linux: flock – der Kernel gibt die Sperre automatisch frei, wenn der Prozess endet,
auch bei einem Absturz. Es bleiben also nie "tote" Sperren übrig.
Windows (nur zum Entwickeln): msvcrt.locking auf dieselbe Datei.

Ein zweiter Lauf WARTET (bis warten_s), statt sofort abzubrechen: n8n startet
render für Session B, während A noch rendert -> B läuft danach.
"""

from __future__ import annotations

import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class Gesperrt(RuntimeError):
    pass


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
    """Hält die Sperre für die Dauer des with-Blocks. warten_s = 0: sofort aufgeben."""
    pfad.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(pfad, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        ende = time.monotonic() + warten_s
        gemeldet = False
        while not _versuche(fd):
            if time.monotonic() >= ende:
                raise Gesperrt(f"Ein anderer Lauf ist aktiv (Sperre {pfad})")
            if melde and not gemeldet:
                melde("warte auf laufenden Schritt …")
                gemeldet = True
            time.sleep(1)
        try:
            yield
        finally:
            _freigeben(fd)
    finally:
        os.close(fd)
