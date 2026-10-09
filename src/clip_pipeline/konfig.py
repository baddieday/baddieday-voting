"""Konfiguration laden: config/pipeline.toml plus Umgebungsvariablen aus .env.

Mehrbenutzer (M1, Schritt 2): Mit CLIP_INSTANZ=/var/lib/clip-benutzer/<name> lädt lade() die Instanz eines Freundes –
nur seine eigenen Werte (lade_instanz). Ohne die Variable (Florian) läuft alles wie bisher.
"""

from __future__ import annotations

import ipaddress
import os
import re
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


def lies_env(pfad: Path) -> dict[str, str]:
    """KEY=WERT-Zeilen einer .env als dict, ohne die Umgebung anzufassen (fehlt die Datei: leer). Bei doppelten
    Namen gilt die erste Zeile – wie bei lade_env."""
    werte: dict[str, str] = {}
    if not pfad.is_file():
        return werte
    for zeile in pfad.read_text(encoding="utf-8").splitlines():
        zeile = zeile.strip()
        if not zeile or zeile.startswith("#") or "=" not in zeile:
            continue
        schluessel, wert = zeile.split("=", 1)
        werte.setdefault(schluessel.strip(), wert.strip().strip('"').strip("'"))
    return werte


def lade_env(pfad: Path) -> None:
    """Liest KEY=WERT-Zeilen. Bereits gesetzte Umgebungsvariablen haben Vorrang."""
    for schluessel, wert in lies_env(pfad).items():
        os.environ.setdefault(schluessel, wert)


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

    @property
    def instanz(self) -> Path | None:
        """Ordner der Instanz eines Freundes (M1) – None bei Florian (ohne CLIP_INSTANZ geladen). Steht in den Daten
        ([instanz].wurzel, nur lade_instanz setzt ihn), damit jede Kopie der Konfig ihn behält."""
        wurzel = str(self.wert("instanz.wurzel", "") or "").strip()
        return Path(wurzel) if wurzel else None

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
            if (frist := str(self.wert("big.frist", "") or "").strip()) and _vorbei(frist):
                raise SpeicherOffline(f"Speicher-Host schläft; Wecken ist seit {frist} gesperrt ([big].frist)")
            if self.wert("big.alter_weckweg_nur_mit_aus", True):
                from . import big  # erst hier: big importiert konfig

                if grund := big.darf_wecken(self):
                    raise SpeicherOffline(f"Speicher-Host schläft und wird nicht geweckt: {grund}")
            sende_wake_on_lan(mac)
            ende = time.monotonic() + float(self.wert("speicher.wecken_warten_s", 180))
            while not self._host_erreichbar():
                if time.monotonic() > ende:
                    raise SpeicherOffline(f"Speicher-Host {self.wert('speicher.host')} ist nach Wake-on-LAN nicht aufgewacht")
                time.sleep(5)
                # erneut senden: fährt der Host gerade noch herunter, verpufft ein einzelnes Paket
                sende_wake_on_lan(mac)
            # Der NFS-Mount braucht nach dem Aufwachen einen Moment, bis er wieder antwortet
            while not self._markierung_da() and time.monotonic() < ende:
                time.sleep(5)
        if not self._markierung_da():
            raise SpeicherOffline(
                f"Markierungsdatei {self.wurzel / self.daten['speicher']['markierung']} fehlt – Speicher nicht eingehängt?"
            )

    # --- Getrennter Betrieb (E19): Puffer auf dem Mini, Lager auf pve-big ---------------

    @property
    def getrennt(self) -> bool:
        """[lager].wurzel gesetzt: [speicher] ist dann der Puffer auf dem Mini, das Lager liegt auf pve-big.
        Leer = wie bisher ([speicher] ist direkt pve-big)."""
        return bool(str(self.wert("lager.wurzel", "") or "").strip())

    @property
    def lager_wurzel(self) -> Path:
        if not self.getrennt:
            raise KonfigFehler("kein getrennter Betrieb: [lager].wurzel leer")
        return Path(str(self.wert("lager.wurzel")).strip())

    def _lager_markierung(self) -> Path:
        return self.lager_wurzel / str(self.wert("lager.markierung", ".clip-lager"))

    def _lager_markierung_da(self) -> bool:
        return self._lager_markierung().is_file()  # nur stat() – Lesen hielte pve-big per NFS wach

    def _lager_host(self) -> str:
        from . import big  # erst hier: big importiert konfig

        return big.host(self)

    def lager_erreichbar(self) -> bool:
        """Kurzer TCP-Versuch auf pve-big ([big].host, sonst [speicher].host) : [lager].port.
        Ohne Host True – dann entscheidet nur die Markierung."""
        host = self._lager_host()
        if not host:
            return True
        try:
            with socket.create_connection((host, int(self.wert("lager.port", 2049))), timeout=2):
                return True
        except OSError:
            return False

    def pruefe_lager(self, warten_s: float = 0) -> None:
        """Wirft SpeicherOffline, wenn das Lager nicht erreichbar oder nicht eingehängt ist (Markierung fehlt).
        warten_s: nach dem Wecken so lange warten (alle 5 s) – der NFS-Mount kommt erst etwas später."""
        markierung = self._lager_markierung()  # ohne getrennten Betrieb: sofort KonfigFehler
        ende = time.monotonic() + float(warten_s)
        while True:
            # Erst den Host fragen: an einem hängenden NFS-Mount (pve-big schläft) nichts anfassen
            erreichbar = self.lager_erreichbar()
            if erreichbar and markierung.is_file():
                return
            if time.monotonic() >= ende:
                break
            time.sleep(5)
        if not erreichbar:
            raise SpeicherOffline(f"Lager-Host {self._lager_host()} schläft oder ist nicht erreichbar")
        raise SpeicherOffline(f"Markierungsdatei {markierung} fehlt – Lager nicht eingehängt?")

    def pruefe_getrennt(self, mit_lager: bool = True) -> None:
        """Wirft KonfigFehler mit Klartext, wenn Puffer und Lager verwechselt werden könnten.

        Nur stat()/exists – nie Inhalte lesen (NFS OPEN/READ hielte pve-big wach). mit_lager=False prüft nur die
        Puffer-Seite (vor dem Wecken: das Lager nicht anfassen). Mit Lager: schläft pve-big, SpeicherOffline
        statt am hängenden Mount zu warten."""
        puffer = self.wurzel
        lager = self.lager_wurzel
        m_puffer = str(self.wert("puffer.markierung", ".clip-puffer"))
        m_lager = str(self.wert("lager.markierung", ".clip-lager"))
        if not puffer.is_dir():
            raise KonfigFehler(f"Puffer {puffer} ([speicher].wurzel) gibt es nicht")
        if not (puffer / m_puffer).is_file():
            raise KonfigFehler(f"Im Puffer {puffer} fehlt {m_puffer} – ist {puffer} wirklich der Puffer auf dem Mini?")
        if os.path.lexists(puffer / m_lager):
            raise KonfigFehler(f"Im Puffer {puffer} liegt {m_lager} – Puffer und Lager vertauscht?")
        if not mit_lager:
            return
        if not self.lager_erreichbar():
            raise SpeicherOffline(f"Lager-Host {self._lager_host()} schläft – Lager kann nicht geprüft werden")
        if not lager.is_dir():
            raise KonfigFehler(f"Lager {lager} ([lager].wurzel) gibt es nicht")
        if os.path.samefile(puffer, lager):
            raise KonfigFehler(f"Puffer {puffer} und Lager {lager} sind derselbe Ordner – Link /srv/clips prüfen")
        if _dateisystem(puffer) == _dateisystem(lager):
            raise KonfigFehler(f"Puffer {puffer} und Lager {lager} liegen auf demselben Dateisystem – "
                               "ist das Lager (NFS von pve-big) eingehängt?")
        if not (lager / m_lager).is_file():
            raise KonfigFehler(f"Im Lager {lager} fehlt {m_lager} – Lager nicht eingehängt oder falscher Ordner?")
        if os.path.lexists(lager / m_puffer):
            raise KonfigFehler(f"Im Lager {lager} liegt {m_puffer} – Puffer und Lager vertauscht?")


def _dateisystem(pfad: Path) -> int:
    """Gerätenummer des Dateisystems (st_dev). Eigene Funktion, damit Tests sie ersetzen können
    (zwei Temp-Ordner liegen immer auf demselben Dateisystem)."""
    return os.stat(pfad).st_dev


def _vorbei(zeitpunkt: str) -> bool:
    """Liegt ein ISO-Zeitpunkt (mit Zone) in der Vergangenheit?"""
    from datetime import datetime, timezone

    grenze = datetime.fromisoformat(zeitpunkt.replace("Z", "+00:00"))
    return datetime.now(timezone.utc) >= (grenze if grenze.tzinfo else grenze.replace(tzinfo=timezone.utc))


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
    # Freund (M1) – ohne die Variable (Florian) alles wie bisher. Gesetzt, aber leer, ist ein KonfigFehler, nie still
    # Florians Konfig mit seiner .env (M42).
    if "CLIP_INSTANZ" in os.environ:
        return lade_instanz(os.environ["CLIP_INSTANZ"], pfad)
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


# --- Instanz-Modus (Mehrbenutzer M1, Schritt 2) -------------------------------------------------------------------
# Ein Freund läuft als eigene Instanz: CLIP_INSTANZ=/var/lib/clip-benutzer/<name> = I (setzen nur die systemd-Vorlagen).
# Dann gilt nur, was ihm gehört: Startwissen aus der Repo-pipeline.toml (nie die lokal.toml daneben – Florians Rechner,
# Lager, pve-big), darüber I/instanz.toml mit wenigen erlaubten Abschnitten, Zugänge nur aus I/.env, jeder Datenpfad
# fest unter I. Was nicht passt, ist ein KonfigFehler (pipeline: Exit 2 mit JSON), bevor irgendetwas geschrieben wird.

INSTANZ_NAME = re.compile(r"[a-z][a-z0-9-]{1,26}")   # „clip-<name>“ bleibt ein gültiger Linux-Benutzername (≤ 32)
INSTANZ_MARKE = ".clip-benutzer"                     # liegt in I und nennt den Namen – sonst ist I kein Instanz-Ordner
# Fliegt vor allem anderen aus der Umgebung: Zugänge, die ein Freund nie erbt (auch nicht bei einem Aufruf von Hand)
FREMDE_ZUGAENGE = ("TELEGRAM_", "LEARN_BOT_", "TIKTOK_", "YOUTUBE_", "CLAUDE_", "ANTHROPIC_", "CLIP_EPIC_ID")
# Nur diese Namen darf I/.env setzen. Das Claude-Token kommt nie in die Umgebung – claude_aufruf liest es beim Aufruf.
INSTANZ_ENV = ("TELEGRAM_", "LEARN_BOT_", "TIKTOK_", "YOUTUBE_", "CLIP_EPIC_ID")
CLAUDE_TOKEN_NAME = "CLAUDE_CODE_OAUTH_TOKEN"
NICHT_MIT_INSTANZ = ("CLIP_KONFIG", "CLIP_SPEICHER", "CLIP_DATENBANK")
# instanz.toml: Abschnitt → erlaubte Schlüssel (None = die, die die Repo-pipeline.toml dort kennt)
INSTANZ_ERLAUBT: dict[str, tuple[str, ...] | None] = {
    "schnitt": None, "zeit": None, "merkmale.waffen": None, "sperre": ("datei", "warten_s"), "instanz": ("claude",),
    # Stufe 2 (M93): Briefkasten auf dem vServer – trägt benutzer-anlegen.sh ein; Benutzer und Schlüssel sind fest
    "briefkasten": ("host", "port", "oeffentlich", "drossel_kbit", "loeschen", "karenz_h")}
TAILNET = ipaddress.IPv4Network("100.64.0.0/10")   # Adressen im Tailnet (Tailscale)
INSTANZ_WARTEN_S = 900   # M7: ein Freund wartet höchstens 15 min auf die Sperre (wenn instanz.toml nichts sagt)
# Florians Daten und Code: eine Instanz darf weder darin liegen noch sie umfassen (dazu der Code-Ordner PROJEKT)
FLORIAN_BEREICHE = ("/var/lib/clip-pipeline", "/srv", "/opt/clip-regie")
# Hier darf das claude-Programm eines Freundes nicht liegen (auch nicht über einen Link): Florians Bereich, Home-Ordner
CLAUDE_VERBOTEN = ("/var/lib/clip-pipeline", "/home", "/root", "/opt/clip-regie")
# Pfadwächter: Datenpfade (absolut) und Pfade relativ zu [speicher].wurzel – aufgelöst müssen alle in I liegen
DATENPFADE = ("datenbank.pfad", "speicher.wurzel", "regie.ordner", "regie.effekte.sfx_ordner", "musik.ordner",
              "material.ordner", "big.zustand_ordner", "lager.wurzel", "briefkasten.schluessel",
              "briefkasten.known_hosts")
IM_PUFFER = ("speicher.markierung", "speicher.eingang", "speicher.replays", "speicher.sessions", "speicher.highlights",
             "speicher.musik", "speicher.archiv", "speicher.papierkorb", "sitzungen.ordner", "publikum.upload_ordner",
             "puffer.markierung", "puffer.pc_status_datei")


def _unter(pfad: Path, wurzel: Path) -> bool:
    return pfad == wurzel or wurzel in pfad.parents


def liegt_in(pfad: Path | str, wurzel: Path | str) -> bool:
    """Liegt pfad – aufgelöst, mit allen Links – in wurzel (oder ist er wurzel)? Beide müssen nicht existieren."""
    return _unter(Path(os.path.realpath(pfad)), Path(os.path.realpath(wurzel)))


def claude_verboten(programm: Path | str) -> str | None:
    """Der Bereich aus CLAUDE_VERBOTEN, in dem dieses claude-Programm liegt (wörtlich oder über einen Link),
    sonst None."""
    woertlich = Path(os.path.normpath(str(programm)))
    return next((b for b in CLAUDE_VERBOTEN if _unter(woertlich, Path(b)) or liegt_in(programm, b)), None)


def lade_instanz(roh: str, pfad: Path | str | None = None) -> Konfig:
    """Konfig der Instanz roh (= CLIP_INSTANZ): zuerst fliegen fremde Zugänge aus der Umgebung, dann werden Aufruf,
    Ordner und Marke geprüft, danach gelten nur I/.env, die Repo-pipeline.toml mit I/instanz.toml, die erzwungenen
    Werte und der Pfadwächter. Liest nie PROJEKT/.env und nie die lokal.toml. Fehler: KonfigFehler mit Klartext."""
    for name in [n for n in os.environ if n.startswith(FREMDE_ZUGAENGE)]:
        del os.environ[name]
    if pfad:
        raise KonfigFehler("--konfig gilt nicht zusammen mit CLIP_INSTANZ – eine Instanz hat nur ihre instanz.toml")
    for name in NICHT_MIT_INSTANZ:
        if os.environ.get(name, "").strip():
            raise KonfigFehler(f"{name} gilt nicht zusammen mit CLIP_INSTANZ – die Pfade einer Instanz sind fest")
    inst, name = _instanz_ordner(roh)
    os.environ.update(_instanz_env(inst))
    toml = inst / "instanz.toml"
    daten = _lies_lesbar(STANDARD_KONFIG)   # Startwissen aus dem Repo – nie die lokal.toml daneben (Florians Rechner)
    eigen = _lies_lesbar(toml)              # fehlt sie: KonfigFehler
    _pruefe_erlaubt(eigen, daten, toml)
    _mische(daten, eigen)
    _erzwinge(daten, eigen, inst, name, toml)
    _pruefe_pfade(daten, inst)
    return Konfig(daten, toml)


def _lies_lesbar(pfad: Path) -> dict:
    """_lies_toml, aber auch „nicht lesbar“ (z. B. falsche Gruppe) als KonfigFehler – Klartext statt Absturz."""
    try:
        return _lies_toml(pfad)
    except (OSError, UnicodeDecodeError) as e:
        raise KonfigFehler(f"{pfad} nicht lesbar ({getattr(e, 'strerror', None) or type(e).__name__})") from None


def _instanz_ordner(roh: str) -> tuple[Path, str]:
    """(I aufgelöst, Name). Prüft die Form, die Lage (nie in oder um Florians Bereiche) und die Marke
    I/.clip-benutzer."""
    roh = roh.strip()
    if not os.path.isabs(roh) or ".." in Path(roh).parts:
        raise KonfigFehler(f"CLIP_INSTANZ={roh!r}: nötig ist ein absoluter Pfad wie /var/lib/clip-benutzer/<name>")
    inst = Path(os.path.realpath(roh))
    name = inst.name
    if not INSTANZ_NAME.fullmatch(name):
        raise KonfigFehler(f"CLIP_INSTANZ={roh!r}: Name {name!r} ungültig "
                           "(2–27 Zeichen a-z, 0-9, -, vorn ein Buchstabe)")
    for bereich in (*FLORIAN_BEREICHE, str(PROJEKT)):
        if liegt_in(inst, bereich) or liegt_in(bereich, inst):
            raise KonfigFehler(f"Instanz {inst} überschneidet sich mit {bereich} – das gehört Florian")
    marke = inst / INSTANZ_MARKE
    try:
        inhalt = marke.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        raise KonfigFehler(f"{marke} fehlt – {inst} ist kein Instanz-Ordner "
                           "(anlegen mit benutzer-anlegen.sh)") from None
    except (OSError, UnicodeDecodeError) as e:
        raise KonfigFehler(f"{marke} nicht lesbar ({type(e).__name__})") from None
    if inhalt != name:
        raise KonfigFehler(f"{marke} nennt {inhalt!r} statt {name!r} – falscher Ordner?")
    return inst, name


def _instanz_env(inst: Path) -> dict[str, str]:
    """Was aus I/.env in die Umgebung darf (fehlt die Datei: nichts). Ein unbekannter Name ist ein KonfigFehler."""
    datei = inst / ".env"
    try:
        werte = lies_env(datei)
    except (OSError, UnicodeDecodeError) as e:
        raise KonfigFehler(f"{datei} nicht lesbar ({getattr(e, 'strerror', None) or type(e).__name__})") from None
    fremd = sorted(n for n in werte if n != CLAUDE_TOKEN_NAME and not n.startswith(INSTANZ_ENV))
    if fremd:
        raise KonfigFehler(f"{datei}: {', '.join(fremd)} gehört nicht in die .env einer Instanz")
    return {n: w for n, w in werte.items() if n != CLAUDE_TOKEN_NAME}


def _pruefe_erlaubt(eigen: dict, basis: dict, toml: Path) -> None:
    """instanz.toml darf nur Abschnitte und Schlüssel aus INSTANZ_ERLAUBT setzen. Alles andere (Pfade, Datenbank,
    Lager, pve-big, Hosts, KI-Programm …) ist ein KonfigFehler – kein stilles Übergehen."""
    def nein(was: str) -> KonfigFehler:
        return KonfigFehler(f"{toml}: {was} ist in einer instanz.toml nicht erlaubt (nur [schnitt], [zeit], "
                            "[merkmale.waffen], [sperre] datei/warten_s, [instanz] claude und [briefkasten])")

    abschnitte: dict[str, Any] = {}
    for name, inhalt in eigen.items():
        if name == "merkmale" and isinstance(inhalt, dict):
            abschnitte.update({f"merkmale.{unter}": wert for unter, wert in inhalt.items()})
        else:
            abschnitte[name] = inhalt
    for name, inhalt in abschnitte.items():
        if name not in INSTANZ_ERLAUBT or not isinstance(inhalt, dict):
            raise nein(f"[{name}]")
        schluessel = INSTANZ_ERLAUBT[name]
        if schluessel is None:
            schluessel = tuple(Konfig(basis, toml).wert(name, {}) or {})
        for s, wert in inhalt.items():
            if s not in schluessel or isinstance(wert, dict):
                raise nein(f"[{name}].{s}")


def _erzwinge(daten: dict, eigen: dict, inst: Path, name: str, toml: Path) -> None:
    """Feste Werte jeder Instanz – unabhängig von Repo-Konfig und instanz.toml."""
    daten.setdefault("datenbank", {})["pfad"] = str(inst / "db" / "pipeline.db")
    daten.setdefault("speicher", {}).update(wurzel=str(inst / "daten"), host="", wol_mac="")   # Puffer, weckt nie
    daten.setdefault("regie", {})["ordner"] = str(inst / "regie")
    daten["regie"].setdefault("effekte", {})["sfx_ordner"] = str(inst / "sfx")
    daten.setdefault("musik", {})["ordner"] = str(inst / "musik")
    daten.setdefault("material", {})["ordner"] = str(inst / "material")
    big = daten.setdefault("big", {})
    big.pop("ssh", None)   # ersetzte sonst den ganzen SSH-Aufruf (Test-Schalter)
    big.update(host="", ssh_ziel="", ssh_schluessel="", frist="", zustand_ordner=str(inst / "db"))
    # Puffer-Betrieb ohne Lager (M10): ein Lager-Pfad, den es nie gibt (I gehört root) – jeder Lager-Zugriff scheitert
    daten.setdefault("lager", {}).update(wurzel=str(inst / "kein-lager"), warten_s=0)
    daten.setdefault("puffer", {}).update(freigeben=False, pool_status="")   # bei Freunden wird nichts gelöscht
    daten.setdefault("aufraeumen", {})["aktiv"] = False
    # Florians claude-Weg bleibt gebremst; ein Freund nutzt nur seinen eigenen Zugang (claude_aufruf, [instanz].claude)
    daten.setdefault("decide", {})["programm"] = ""
    if epic := os.environ.get("CLIP_EPIC_ID", "").strip():   # nur noch aus I/.env – Florians ID ist schon weg
        daten.setdefault("replay", {})["ich"] = epic

    sperre = daten.setdefault("sperre", {})
    datei = sperre.get("datei")
    if not isinstance(datei, str) or not datei.strip():
        raise KonfigFehler(f"{toml}: [sperre].datei fehlt – nötig ist die gemeinsame Rechen-Sperre des Mini")
    if not os.path.isabs(datei.strip()) or liegt_in(datei.strip(), inst):
        raise KonfigFehler(f"{toml}: [sperre].datei {datei!r} muss ein absoluter Pfad außerhalb der Instanz sein – "
                           "sonst rechnete die Instanz neben Florian her")
    if not os.path.isfile(datei.strip()):   # M41: sonst legte sperre.oeffne bei einem Tippfehler still eine eigene an
        raise KonfigFehler(f"{toml}: [sperre].datei {datei!r} fehlt oder ist nicht erreichbar – einzutragen ist "
                           "Florians Sperrdatei, eine eigene legt eine Instanz nie an")
    warten = sperre.get("warten_s")
    if "warten_s" not in eigen.get("sperre", {}):
        sperre["warten_s"] = INSTANZ_WARTEN_S
    elif isinstance(warten, bool) or not isinstance(warten, (int, float)) or warten < 0:
        raise KonfigFehler(f"{toml}: [sperre].warten_s muss eine Zahl ab 0 sein, nicht {warten!r}")

    claude = eigen.get("instanz", {}).get("claude", "")
    if not isinstance(claude, str) or (claude.strip() and not os.path.isabs(claude.strip())):
        raise KonfigFehler(f"{toml}: [instanz].claude muss ein absoluter Pfad sein, nicht {claude!r}")
    if claude.strip() and (bereich := claude_verboten(claude.strip())):
        raise KonfigFehler(f"{toml}: [instanz].claude liegt unter {bereich} – dort nie (Florians Bereich, Home-Ordner)")
    daten["instanz"] = {"wurzel": str(inst), "name": name, "claude": claude.strip()}
    _briefkasten(daten, inst, name, toml)


def _briefkasten(daten: dict, inst: Path, name: str, toml: Path) -> None:
    """[briefkasten] einer Instanz (Stufe 2, M93): Werte prüfen, bevor etwas geholt wird. Fest: Benutzer bk-<name>,
    Schlüssel und Hostschlüssel in I/briefkasten/. Mit host kommt das Abend-Ende als Datei vom PC (auto_abend aus)."""
    b = daten.setdefault("briefkasten", {})

    def nein(schluessel: str, text: str) -> KonfigFehler:
        return KonfigFehler(f"{toml}: [briefkasten].{schluessel} = {b.get(schluessel)!r} – {text}")

    host = b.get("host", "")
    if not isinstance(host, str):
        raise nein("host", "nötig ist die Tailnet-Adresse des vServers")
    if host := host.strip():
        try:
            im_tailnet = ipaddress.IPv4Address(host) in TAILNET
        except ValueError:
            im_tailnet = False
        if not im_tailnet:
            raise nein("host", "nötig ist die Tailnet-Adresse des vServers (100.64.0.0/10), kein Name")
    for schluessel, standard, unten, oben in (("port", 2222, 1, 65535), ("drossel_kbit", 20000, 0, 10**7)):
        wert = b.get(schluessel, standard)
        if isinstance(wert, bool) or not isinstance(wert, int) or not unten <= wert <= oben:
            raise nein(schluessel, f"nötig ist eine ganze Zahl von {unten} bis {oben}")
    oeffentlich = b.get("oeffentlich", "")
    if not isinstance(oeffentlich, str) or not re.fullmatch(r"[A-Za-z0-9.:-]{0,253}", oeffentlich.strip()):
        raise nein("oeffentlich", "nötig ist ein Name oder eine Adresse")
    if b.get("loeschen", False) is not False:   # PR 7: erst nach Florians Ja – bis dahin gibt es kein Löschen
        raise nein("loeschen", "Löschen im Briefkasten gibt es erst nach Florians Ja – bis dahin false")
    karenz = b.get("karenz_h", 24)
    if isinstance(karenz, bool) or not isinstance(karenz, (int, float)) or karenz < 0:
        raise nein("karenz_h", "nötig ist eine Zahl ab 0")
    b.update(host=host, benutzer=f"bk-{name}", schluessel=str(inst / "briefkasten" / "abholen"),
             known_hosts=str(inst / "briefkasten" / "known_hosts"))
    if host:
        daten.setdefault("sitzungen", {})["auto_abend"] = False


def _pruefe_pfade(daten: dict, inst: Path) -> None:
    """Pfadwächter: jeder Datenpfad liegt – aufgelöst, mit allen Links – in I. Den Lager-Pfad gibt es nie."""
    k = Konfig(daten, inst)   # nur zum Lesen per Punkt-Pfad
    pfade = [(n, Path(str(k.wert(n)))) for n in DATENPFADE]
    pfade += [(n, k.wurzel / str(k.wert(n))) for n in IM_PUFFER if k.wert(n)]
    for schluessel, pfad in pfade:
        if not liegt_in(pfad, inst):
            raise KonfigFehler(f"{pfad} ({schluessel}) liegt aufgelöst außerhalb der Instanz {inst} – Link?")
    if os.path.lexists(k.lager_wurzel):
        raise KonfigFehler(f"{k.lager_wurzel} gibt es – eine Instanz hat (noch) kein Lager, der Pfad muss fehlen (M10)")
