"""Konfiguration laden: config/pipeline.toml plus Umgebungsvariablen aus .env."""

from __future__ import annotations

import os
import socket
import sys
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

def _projektordner() -> Path:
    """Projektordner mit config/: CLIP_PROJEKT, sonst der Quellordner (editierbare Installation), sonst cwd."""
    for kandidat in (os.environ.get("CLIP_PROJEKT"), Path(__file__).resolve().parents[2], Path.cwd()):
        if kandidat and (Path(kandidat) / "config" / "pipeline.toml").is_file():
            return Path(kandidat)
    return Path(__file__).resolve().parents[2]


PROJEKT = _projektordner()
STANDARD_KONFIG = PROJEKT / "config" / "pipeline.toml"


class KonfigFehler(RuntimeError):
    pass


class SpeicherOffline(RuntimeError):
    """Der Clip-Speicher (großer Host) ist nicht erreichbar oder nicht eingehängt."""


def lade_env(pfad: Path) -> None:
    """Liest KEY=WERT-Zeilen. Bereits gesetzte Umgebungsvariablen haben Vorrang."""
    if not pfad.is_file():
        return
    for zeile in pfad.read_text(encoding="utf-8").splitlines():
        zeile = zeile.strip()
        if not zeile or zeile.startswith("#") or "=" not in zeile:
            continue
        schluessel, wert = zeile.split("=", 1)
        os.environ.setdefault(schluessel.strip(), wert.strip().strip('"').strip("'"))


@dataclass
class Konfig:
    daten: dict[str, Any]
    quelle: Path

    def wert(self, pfad: str, standard: Any = None) -> Any:
        """Liest einen Wert per Punkt-Pfad, z. B. wert("vorbewertung.puffer_vorne_s")."""
        knoten: Any = self.daten
        for teil in pfad.split("."):
            if not isinstance(knoten, dict) or teil not in knoten:
                return standard
            knoten = knoten[teil]
        return knoten

    def abschnitt(self, name: str) -> dict[str, Any]:
        return self.daten.get(name, {})

    # --- Pfade -----------------------------------------------------------------

    @property
    def wurzel(self) -> Path:
        return Path(self.daten["speicher"]["wurzel"])

    def ordner(self, name: str) -> Path:
        """Unterordner des Speichers, z. B. ordner("clips")."""
        return self.wurzel / self.daten["speicher"][name]

    def relativ(self, pfad: Path) -> str:
        """Pfad relativ zur Speicher-Wurzel, immer mit '/' (gleich unter Windows und Linux)."""
        return Path(pfad).resolve().relative_to(self.wurzel.resolve()).as_posix()

    def absolut(self, relativ: str) -> Path:
        return self.wurzel / Path(relativ)

    @property
    def datenbank(self) -> Path:
        return Path(self.daten["datenbank"]["pfad"])

    def projektpfad(self, wert: str) -> Path:
        pfad = Path(wert)
        return pfad if pfad.is_absolute() else PROJEKT / pfad

    @property
    def replay_programm(self) -> Path:
        """Erstes vorhandenes Programm aus der Liste (Linux-Build auf dem Server, .exe unter Windows)."""
        eintraege = self.daten["replay"]["programm"]
        kandidaten = []
        for eintrag in [eintraege] if isinstance(eintraege, str) else eintraege:
            pfad = self.projektpfad(eintrag)
            if sys.platform == "win32" and pfad.suffix == "":
                pfad = pfad.with_suffix(".exe")
            kandidaten.append(pfad)
        return next((p for p in kandidaten if p.is_file()), kandidaten[0])

    # --- Speicher-Prüfung ------------------------------------------------------

    def _host_erreichbar(self) -> bool:
        host = str(self.wert("speicher.host", "") or "").strip()
        if not host:
            return True
        try:
            with socket.create_connection((host, int(self.wert("speicher.port", 2049))), timeout=2):
                return True
        except OSError:
            return False

    def _markierung_da(self) -> bool:
        return (self.wurzel / self.daten["speicher"]["markierung"]).is_file()

    def pruefe_speicher(self, wecken: bool = False) -> None:
        """Wirft SpeicherOffline, wenn der große Host schläft oder der Mount fehlt.

        wecken=True: schläft der Host, wird er per Wake-on-LAN geweckt und bis zu wecken_warten_s gewartet.
        """
        if not self._host_erreichbar():
            mac = str(self.wert("speicher.wol_mac", "") or "").strip()
            if not (wecken and mac):
                raise SpeicherOffline(f"Speicher-Host {self.wert('speicher.host')} schläft oder ist nicht erreichbar")
            sende_wake_on_lan(mac)
            ende = time.monotonic() + float(self.wert("speicher.wecken_warten_s", 180))
            while not self._host_erreichbar():
                if time.monotonic() > ende:
                    raise SpeicherOffline(f"Speicher-Host {self.wert('speicher.host')} ist nach Wake-on-LAN nicht aufgewacht")
                time.sleep(5)
            # Der NFS-Mount braucht nach dem Aufwachen einen Moment, bis er wieder antwortet
            while not self._markierung_da() and time.monotonic() < ende:
                time.sleep(5)
        if not self._markierung_da():
            raise SpeicherOffline(
                f"Markierungsdatei {self.wurzel / self.daten['speicher']['markierung']} fehlt – Speicher nicht eingehängt?"
            )


def sende_wake_on_lan(mac: str, broadcast: str = "255.255.255.255", port: int = 9) -> bytes:
    """Magisches Paket: 6× 0xFF, danach 16× die MAC-Adresse – per UDP-Broadcast ins lokale Netz."""
    roh = bytes.fromhex(mac.replace(":", "").replace("-", ""))
    if len(roh) != 6:
        raise ValueError(f"MAC-Adresse {mac!r} ungültig")
    paket = b"\xff" * 6 + roh * 16
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.sendto(paket, (broadcast, port))
    return paket


def _mische(basis: dict, zusatz: dict) -> dict:
    """Überschreibt einzelne Werte, Abschnitte werden zusammengeführt statt ersetzt."""
    for schluessel, wert in zusatz.items():
        if isinstance(wert, dict) and isinstance(basis.get(schluessel), dict):
            _mische(basis[schluessel], wert)
        else:
            basis[schluessel] = wert
    return basis


def _lies_toml(pfad: Path) -> dict:
    try:
        return tomllib.loads(pfad.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise KonfigFehler(f"Konfiguration {pfad} nicht gefunden") from None
    except tomllib.TOMLDecodeError as fehler:
        raise KonfigFehler(f"Konfiguration {pfad} fehlerhaft: {fehler}") from None


def lade(pfad: Path | str | None = None) -> Konfig:
    lade_env(PROJEKT / ".env")
    pfad = Path(pfad or os.environ.get("CLIP_KONFIG") or STANDARD_KONFIG)
    if not pfad.is_absolute():
        pfad = PROJEKT / pfad
    daten = _lies_toml(pfad)
    # Rechner-eigene Werte (z. B. IP und MAC des großen Hosts) stehen in lokal.toml neben der
    # Konfiguration. Die Datei ist in .gitignore – so gibt es bei "git pull" nie Konflikte.
    lokal = pfad.with_name("lokal.toml")
    if lokal.is_file():
        _mische(daten, _lies_toml(lokal))

    # Überschreibungen aus der Umgebung (praktisch zum Testen und für die .env)
    if wurzel := os.environ.get("CLIP_SPEICHER"):
        daten["speicher"]["wurzel"] = wurzel
    if datenbank := os.environ.get("CLIP_DATENBANK"):
        daten["datenbank"]["pfad"] = datenbank
    if epic_id := os.environ.get("CLIP_EPIC_ID"):
        daten["replay"]["ich"] = epic_id
    return Konfig(daten, pfad)
