"""Briefkasten eines Freundes abholen (Mehrbenutzer, Stufe 2, Schritt 2): `pipeline briefkasten abholen|status`.

Der PC eines Freundes lädt seine Aufnahmen in sein Fach im Briefkasten auf Florians vServer (docs/BRIEFKASTEN.md). Der
Mini holt sie alle 2 Minuten über Tailscale ab – nur lesend, als bk-<name> mit dem Schlüssel I/briefkasten/abholen – und
legt sie in den Puffer des Freundes, als wären sie wie bei Florian vom PC gekommen. scan und sitzungen rechnen danach
wie bisher. Nur in der Instanz eines Freundes; bei Florian endet der Befehl mit Exit 2, bevor er etwas anfasst.

Regeln (docs/ENTSCHEIDUNGEN.md M89–M93, M96):
  - Fertig ist eine Datei im Fach erst mit ihrem Lieferschein <name>.lieferschein (JSON: name, groesse, sha256,
    mtime_ms, utc_offset_min). Halbe Uploads (.teil) und Namen außerhalb der Muster der Pipeline fasst der Mini nie an;
    das Ziel im Puffer ergibt sich allein aus Ordner und Name, nie aus einem Pfad vom PC.
  - Geholt wird nach I/daten/.abholen/<nr>.teil (außerhalb von eingang/ und replays/ – scan, Lager und Bestand sehen das
    nicht), danach Größe und SHA-256 nach kaltem Zurücklesen gegen den Lieferschein. Erst dann bekommt die Datei die
    Zeit vom PC, höchstens jetzt − 130 s (für scan sofort reif), und per os.link ihren Namen – überschreibt nie.
  - Reihenfolge: gelistet wird sitzungen → replays → videos, veröffentlicht Videos (älteste zuerst) → Replays (nur, wenn
    kein gelistetes Video offen blieb) → Sitzungsdateien (erst, wenn ihre Matches verarbeitet oder aufgegeben sind,
    spätestens 24 h nach dem ersten Sehen). So findet scan zu jedem Replay alle Aufnahmen, und das Abend-Video baut
    sofort. Ein Fehler in der Datei zählt als Versuch; nach dreien ist sie „aufgegeben“ und hält nichts mehr auf.
  - Keine Rechen-Sperre (nur Ein- und Ausgabe), eigene Sperre I/db/pipeline.briefkasten.lock, weckt nie. Im Briefkasten
    wird nie etwas gelöscht. Bremse: geholt wird nur, wenn auf dem Freunde-Volume danach max(10 GB, 10 %) frei bleiben.
  - Die Tabelle `abholung` entsteht nur in der Datenbank eines Freundes.
  - Den Status, den sein PC-Programm schickt (status/pc-status.json, windows/Freund-Hochladen.ps1), legt der Mini nach
    I/db/pc-status.json; daraus kommen Zeilen an den Freund: PC verbunden (einmal), Zeitzone falsch, Aufnahmen ohne
    Replay (je einmal am Tag, M118). 📋 Stand liest ihn über pc_status/unterwegs (lernbot_pc).
"""

from __future__ import annotations

import errno
import hashlib
import json
import logging
import os
import re
import signal
import sqlite3
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from . import db, quellen, replay
from .konfig import Konfig, KonfigFehler
from .material import BLOCK
from .sperre import sperre
from .verarbeitung import SESSION_ID
from .zeit import aus_iso, iso, jetzt, utc_zu_lokal

try:
    import resource   # nur Linux/Unix – der Abholer läuft nur im CT; unter Windows bleibt das Modul importierbar
except ImportError:   # pragma: no cover
    resource = None

log = logging.getLogger("pipeline")

SFTP = "/usr/bin/sftp"              # openssh-client im CT – Tests setzen hier die Attrappe ein
ABLAGE = ".abholen"                 # Zwischenablage im Puffer (I/daten/.abholen)
FACH = "/fach"                      # Start im chroot (internal-sftp -d /fach)
MARKE = ".clip-briefkasten"         # liegt in jedem eingehängten Fach (gehört root)
LISTE = ("sitzungen", "replays", "videos")   # Reihenfolge beim Listen (M91)
LIEFERSCHEIN = ".lieferschein"
LIEFERSCHEIN_MAX = 4096             # Bytes
STATUS_MAX = 64 * 1024              # status/pc-status.json
SITZUNG_MAX = 64 * 1024             # eine Sitzungsdatei
GROESSE_MAX = 50 * 10**9            # eine Aufnahme
NAME_MAX = 200
NAME_VERBOTEN = frozenset('[]*?"\'\\')   # Platzhalter und Anführungszeichen (sftp)
SITZUNG_NAME = re.compile(r"session_[A-Za-z0-9][A-Za-z0-9._-]*\.json")
SHA = re.compile(r"[0-9a-fA-F]{64}")
UNTERORDNER = {"nvidia_highlight": "nvidia/highlights", "nvidia_dvr": "nvidia/aufnahmen",
               "nvidia_aufnahme": "nvidia/aufnahmen", "steelseries": "steelseries"}   # wie Uebertragung.ps1
KAPPE_S = 130                       # mtime höchstens jetzt − 130 s: für scan sofort reif (Ruhezeit 60/120 s)
VERSUCHE = 3
SITZUNG_SPAETESTENS_H = 24
RESERVE_BYTE = 10 * 10**9           # Bremse: so viel bleibt mindestens frei …
RESERVE_ANTEIL = 0.10               # … und 10 % des Freunde-Volumes
VOLL_PROZENT = 80
UNERREICHBAR_H = 24
LISTEN_S = 300                      # Höchstdauer der kleinen Läufe (Listen, Lieferscheine)

TABELLE = """CREATE TABLE IF NOT EXISTS abholung (
    id INTEGER PRIMARY KEY,
    ordner TEXT NOT NULL,                  -- videos · replays · sitzungen (im Fach)
    name TEXT NOT NULL,
    groesse INTEGER,                       -- laut Lieferschein (NULL: noch keiner gültig)
    sha256 TEXT,
    pc_mtime_ms INTEGER,
    utc_offset_min INTEGER,
    matches TEXT,                          -- nur Sitzungen: JSON-Liste ihrer Matches
    gesehen TEXT NOT NULL,                 -- zuerst im Fach gesehen (UTC)
    versuche INTEGER NOT NULL DEFAULT 0,   -- gescheiterte Versuche
    grund TEXT,                            -- letzter Fehler bzw. warum aufgegeben, vermerkt oder Konflikt
    status TEXT NOT NULL DEFAULT 'offen'
        CHECK (status IN ('offen', 'abgeholt', 'vermerkt', 'aufgegeben', 'konflikt')),
    ziel TEXT,                             -- relativ zum Puffer
    abgeholt TEXT,                         -- wann erledigt (UTC)
    UNIQUE (ordner, name))"""


# --- Konfig und Orte -----------------------------------------------------------------------------------------------

def aktiv(konfig: Konfig) -> bool:
    """Hat dieser Freund einen Briefkasten ([briefkasten].host gesetzt)?"""
    return bool(str(konfig.wert("briefkasten.host", "") or "").strip())


def _nur_instanz(konfig: Konfig) -> None:
    if konfig.instanz is None:
        raise KonfigFehler("pipeline briefkasten läuft nur in der Instanz eines Freundes (CLIP_INSTANZ)")


def sperrdatei(konfig: Konfig) -> Path:
    """Eigene Sperre (I/db/pipeline.briefkasten.lock) – nie die Rechen-Sperre."""
    return konfig.datenbank.with_suffix(".briefkasten.lock")


def _stand_datei(konfig: Konfig) -> Path:
    return konfig.datenbank.parent / "briefkasten.json"


def pc_status_datei(konfig: Konfig) -> Path:
    return konfig.datenbank.parent / "pc-status.json"


def _pruefe_zugang(konfig: Konfig) -> None:
    for schluessel, was in (("schluessel", "Schlüssel"), ("known_hosts", "Hostschlüssel (known_hosts)")):
        pfad = Path(str(konfig.wert(f"briefkasten.{schluessel}", "")))
        if not os.access(pfad, os.R_OK):
            raise KonfigFehler(f"{was} für den Briefkasten fehlt oder ist nicht lesbar ({pfad}) – "
                               "benutzer-anlegen.sh, Schritt „Briefkasten“")


# --- Namen und Lieferscheine ---------------------------------------------------------------------------------------

def name_ok(name: str) -> bool:
    """Druckbares ASCII, kein Punkt vorn, keine Platzhalter oder Anführungszeichen, höchstens 200 Zeichen."""
    return (0 < len(name) <= NAME_MAX and name.isascii() and name.isprintable() and not name.startswith(".")
            and "/" not in name and not NAME_VERBOTEN.intersection(name))


def ziel(konfig: Konfig, ordner: str, name: str) -> str | None:
    """Wohin eine Datei aus dem Fach im Puffer gehört (relativ zur Wurzel) – allein aus Ordner und Name nach den Mustern
    der Pipeline. None = fremd, wird nie angefasst. Ein Datum, das es nicht gibt, ist auch fremd: scan stürzte daran ab."""
    if not name_ok(name):
        return None
    try:
        if ordner == "videos":
            erkannt = quellen.erkenne(name)
            unter = UNTERORDNER.get(erkannt.quelle) if erkannt and name.lower().endswith(".mp4") else None
            return f"{konfig.wert('speicher.eingang', 'eingang')}/{unter}/{name}" if unter else None
        if ordner == "replays":
            if not (treffer := replay.REPLAY_NAME.fullmatch(name)):
                return None
            datetime.strptime(f"{treffer['datum']} {treffer['zeit']}", "%Y.%m.%d %H.%M.%S")
            return f"{konfig.wert('speicher.replays', 'replays')}/{name}"
    except ValueError:
        return None
    if ordner == "sitzungen" and SITZUNG_NAME.fullmatch(name) and SESSION_ID.fullmatch(name.removesuffix(".json")):
        return f"{konfig.wert('sitzungen.ordner', 'sitzungen')}/{name}"
    return None


def _ganz(wert, unten: int, oben: int) -> bool:
    return isinstance(wert, int) and not isinstance(wert, bool) and unten <= wert <= oben


def pruefe_lieferschein(roh: bytes, ordner: str, name: str) -> dict:
    """Lieferschein streng prüfen: höchstens 4 KB, JSON (utf-8, BOM erlaubt), name = Dateiname, groesse 1 B–50 GB (eine
    Sitzungsdatei höchstens 64 KB), sha256 = 64 Hex-Zeichen, mtime_ms und utc_offset_min ganze Zahlen. ValueError mit
    Grund."""
    if not roh or len(roh) > LIEFERSCHEIN_MAX:
        raise ValueError(f"fehlt, ist leer oder größer als {LIEFERSCHEIN_MAX} Byte")
    daten = json.loads(roh.decode("utf-8-sig"))
    if not isinstance(daten, dict) or daten.get("name") != name:
        raise ValueError("nennt eine andere Datei")
    groesse, sha = daten.get("groesse"), daten.get("sha256")
    if not _ganz(groesse, 1, SITZUNG_MAX if ordner == "sitzungen" else GROESSE_MAX):
        raise ValueError(f"Größe {groesse!r} ungültig")
    if not isinstance(sha, str) or not SHA.fullmatch(sha):
        raise ValueError("Prüfsumme ungültig")
    if not _ganz(daten.get("mtime_ms"), 1, 10**13) or not _ganz(daten.get("utc_offset_min"), -1080, 1080):
        raise ValueError("Zeit vom PC ungültig")
    return {"groesse": groesse, "sha256": sha.lower(), "pc_mtime_ms": daten["mtime_ms"],
            "utc_offset_min": daten["utc_offset_min"]}


def pruefe_sitzung(roh: bytes) -> list[str]:
    """Sitzungsdatei vom PC ({session, matches, ende_utc}): Matches als Liste gültiger Session-IDs (1–500), ende_utc
    lesbar. Sonst ValueError – sitzung.py bekäme sonst Unsinn."""
    daten = json.loads(roh.decode("utf-8-sig"))
    matches = daten.get("matches") if isinstance(daten, dict) else None
    if (not isinstance(matches, list) or not 0 < len(matches) <= 500
            or not all(isinstance(m, str) and SESSION_ID.fullmatch(m) for m in matches)):
        raise ValueError("Matches fehlen oder sind ungültig")
    if not isinstance(daten.get("ende_utc"), str):
        raise ValueError("ende_utc fehlt")
    aus_iso(daten["ende_utc"])   # ValueError, wenn unlesbar
    return list(dict.fromkeys(matches))


# --- sftp ----------------------------------------------------------------------------------------------------------

def _befehl(konfig: Konfig) -> list[str]:
    """Wie big.ssh_befehl: keine Konfig-Datei, nur dieser Schlüssel, Hostschlüssel gepinnt, kurze Fristen."""
    b = konfig.abschnitt("briefkasten")
    argv = [SFTP, "-F", "/dev/null", "-b", "-", "-P", str(b.get("port", 2222))]
    if (kbit := int(b.get("drossel_kbit", 0) or 0)) > 0:
        argv += ["-l", str(kbit)]
    for option in ("BatchMode=yes", "IdentitiesOnly=yes", f"IdentityFile={b['schluessel']}",
                   "StrictHostKeyChecking=yes", f"UserKnownHostsFile={b['known_hosts']}",
                   "GlobalKnownHostsFile=/dev/null", "ConnectTimeout=10", "ServerAliveInterval=15",
                   "ServerAliveCountMax=4"):
        argv += ["-o", option]
    return [*argv, f"{b['benutzer']}@{str(b['host']).strip()}"]


def _text(wert) -> str:
    return wert.decode("utf-8", "replace") if isinstance(wert, bytes) else (wert or "")


def _sftp(konfig: Konfig, befehle: list[str], *, max_datei: int, timeout: float | None) -> subprocess.CompletedProcess:
    """Ein sftp-Lauf mit diesen Befehlen. Keine lokale Datei wird größer als max_datei (RLIMIT_FSIZE): Ein zu großer
    Lieferschein oder Status füllt so nie die Platte – sftp meldet „File too large“, der Befehl scheitert."""
    grenze = None
    if resource is not None:
        hart = resource.getrlimit(resource.RLIMIT_FSIZE)[1]
        max_datei = max_datei if hart == resource.RLIM_INFINITY else min(max_datei, hart)   # nie anheben

        def grenze() -> None:
            signal.signal(signal.SIGXFSZ, signal.SIG_IGN)   # statt den Prozess zu beenden, scheitert nur das Schreiben
            resource.setrlimit(resource.RLIMIT_FSIZE, (max_datei, max_datei))

    try:
        return subprocess.run(_befehl(konfig), input="".join(f"{b}\n" for b in befehle), capture_output=True,
                              encoding="utf-8", errors="replace", timeout=timeout, preexec_fn=grenze)
    except subprocess.TimeoutExpired as e:
        return subprocess.CompletedProcess(e.cmd, 255, _text(e.stdout), _text(e.stderr) + "\nZeit abgelaufen")
    except OSError as e:
        raise KonfigFehler(f"{SFTP} lässt sich nicht starten ({e.strerror}) – openssh-client im CT?") from None


def _q(pfad: str) -> str:
    """In Anführungszeichen für die sftp-Befehlszeile (Namen sind geprüft, eigene Pfade auch)."""
    if any(z in pfad for z in '"\\\n'):
        raise KonfigFehler(f"Pfad {pfad!r} lässt sich sftp nicht übergeben")
    return f'"{pfad}"'


def _letzte(text: str) -> str:
    """Die letzten zwei Zeilen von stderr – nach „Connection closed“ steht dort der Grund."""
    zeilen = [z.strip() for z in text.replace("\r", "\n").splitlines() if z.strip()]
    return " – ".join(zeilen[-2:])[:300] or "ohne Meldung"


@dataclass
class Fach:
    """Was ein Listen-Lauf im Fach sah."""
    erreicht: bool = False
    fehler: str = ""
    marke: bool = False
    prozent: int | None = None
    frei_gb: float | None = None
    namen: dict[str, set[str]] = field(default_factory=dict)


DF_KOPF = re.compile(r"\s*Size\s+Used\s+Avail\s+\(root\)\s+%Capacity\s*")
DF_ZEILE = re.compile(r"\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+\S+\s*")


def _abschnitte(ausgabe: str, befehle: list[str]) -> list[list[str]]:
    """stdout je Befehl: sftp schreibt jeden Befehl als „sftp> <befehl>“ vor seine Ausgabe. Nur die als nächste
    erwartete Zeile beginnt einen neuen Abschnitt – ein Dateiname kann so keinen späteren vortäuschen."""
    teile: list[list[str]] = [[] for _ in befehle]
    i = -1
    for zeile in ausgabe.splitlines():
        if i + 1 < len(befehle) and zeile == f"sftp> {befehle[i + 1]}":
            i += 1
        elif i >= 0:
            teile[i].append(zeile)
    return teile


def lies_liste(ausgabe: str, befehle: list[str]) -> Fach:
    """Ausgabe des Listen-Laufs (Befehle aus _listen_befehle) lesen: Marke, Füllstand (df, KiB) und je Ordner die
    Namen. Nur Zeilen mit dem Ordner vorn zählen, und nur der Basisname; ls -l wird nie zerlegt."""
    teile = dict(zip(befehle, _abschnitte(ausgabe, befehle)))
    fach = Fach(marke=f"{FACH}/{MARKE}" in teile.get(befehle[0], []))
    df = teile.get("-df", [])
    for i, zeile in enumerate(df[:-1]):
        if DF_KOPF.fullmatch(zeile) and (werte := DF_ZEILE.fullmatch(df[i + 1])):
            groesse, belegt, frei = (int(werte[n]) for n in (1, 2, 3))
            fach.prozent = belegt * 100 // groesse if groesse else None
            fach.frei_gb = round(frei * 1024 / 1e9, 2)
            break
    for ordner in LISTE:
        vorne = f"{ordner}/"
        fach.namen[ordner] = {z[len(vorne):] for z in teile.get(f"-ls -1 {ordner}", [])
                              if z.startswith(vorne) and z[len(vorne):] and "/" not in z[len(vorne):]}
    return fach


def _listen_befehle(status_lokal: Path) -> list[str]:
    return [f"ls -1 -a {FACH}", "-df", f"-get status/pc-status.json {_q(str(status_lokal))}",
            *(f"-ls -1 {o}" for o in LISTE)]


def _liste(konfig: Konfig, ablage: Path) -> Fach:
    """Ein Lauf, nur lesen: Marke, Füllstand, Status vom PC (nach .abholen), dann sitzungen → replays → videos."""
    status_lokal = ablage / "pc-status.json"
    status_lokal.unlink(missing_ok=True)
    befehle = _listen_befehle(status_lokal)
    r = _sftp(konfig, befehle, max_datei=STATUS_MAX + 1, timeout=LISTEN_S)
    if r.returncode != 0:
        return Fach(fehler=f"Briefkasten nicht erreichbar ({_letzte(r.stderr)})")
    fach = lies_liste(r.stdout, befehle)
    if not fach.marke:
        return Fach(fehler=f"Fach im Briefkasten nicht eingehängt ({MARKE} fehlt)")
    fach.erreicht = True
    return fach


# --- Tabelle, Stand, Meldungen -------------------------------------------------------------------------------------

def _tabelle(con: sqlite3.Connection) -> None:
    """Nur in der Datenbank eines Freundes (der Befehl läuft nur dort) – Florians Datenbank bekommt sie nie."""
    con.execute(TABELLE)


def _stand_lesen(konfig: Konfig) -> dict:
    try:
        daten = json.loads(_stand_datei(konfig).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return daten if isinstance(daten, dict) else {}


def _schreibe(ziel: Path, inhalt: bytes) -> None:
    """Atomar: erst <ziel>.neu, dann umbenennen."""
    neu = ziel.with_name(f"{ziel.name}.neu")
    neu.write_bytes(inhalt)
    os.replace(neu, ziel)


def _melde_taeglich(con: sqlite3.Connection, konfig: Konfig, thema: str, text: str) -> None:
    """Eine Zeile an seinen Bot – je Thema höchstens einmal am Tag (Ortszeit)."""
    tag = utc_zu_lokal(jetzt(), konfig.wert("zeit.zeitzone", "Europe/Berlin")).date().isoformat()
    db.lern_meldung(con, f"briefkasten:{thema}:{tag}", text)


def _melde_unerreichbar(con: sqlite3.Connection, konfig: Konfig, stand: dict) -> None:
    seit = stand.get("zuletzt_erreicht") or stand.get("erster_versuch")
    try:
        lange = seit is not None and jetzt() - aus_iso(seit) >= timedelta(hours=UNERREICHBAR_H)
    except (TypeError, ValueError):
        lange = False
    if lange:
        _melde_taeglich(con, konfig, "unerreichbar", "⚠️ Ich komme seit über einem Tag nicht an deinen Briefkasten bei "
                        "Florian. Deine Aufnahmen gehen nicht verloren, sie warten dort oder auf deinem PC – "
                        "sag Florian Bescheid.")


def _pc_status(con: sqlite3.Connection, konfig: Konfig, lokal: Path) -> bool:
    """status/pc-status.json (vom Listen-Lauf geholt) → I/db/pc-status.json, wenn es ein JSON-Objekt ist. Kaputt oder
    halb geschrieben (der PC überschreibt die Datei direkt): Der alte Stand bleibt. Danach die Meldungen dazu."""
    try:
        if not lokal.is_file() or not 0 < lokal.stat().st_size <= STATUS_MAX:
            return False
        roh = lokal.read_bytes()
        daten = json.loads(roh.decode("utf-8-sig"))
        if not isinstance(daten, dict):
            return False
        _schreibe(pc_status_datei(konfig), roh)
    except (OSError, ValueError):
        return False
    finally:
        lokal.unlink(missing_ok=True)
    _pc_meldungen(con, konfig, daten)
    return True


def _utc_text(minuten: int) -> str:
    stunden, rest = divmod(abs(minuten), 60)
    return f"UTC{'-' if minuten < 0 else '+'}{stunden}" + (f":{rest:02d}" if rest else "")


def _pc_meldungen(con: sqlite3.Connection, konfig: Konfig, status: dict) -> None:
    """M103/M118 (Schritt 4), aus dem Status, den der PC selbst schickt (fremde Eingabe – nur Zahlen und Zeiten werden
    gelesen, nie ein Text weitergegeben):
      - „✅ Dein PC ist verbunden“ – einmal, beim ersten Status überhaupt;
      - die Zeitzone des PCs passt nicht zu [zeit].zeitzone (dann verrutschen die Clips) – einmal am Tag;
      - Aufnahmen ohne Replay („Replays an?“) – einmal am Tag.
    Die beiden letzten nur aus einem frischen Status (höchstens 24 h alt): Ein PC, der aus ist, meldet nichts Neues."""
    db.lern_meldung(con, "briefkasten:pc_verbunden", "✅ Dein PC ist verbunden – deine Aufnahmen kommen ab jetzt von "
                    "selbst zu mir.")
    try:
        zeit = aus_iso(status["zeit_utc"]) if isinstance(status.get("zeit_utc"), str) else None
    except ValueError:
        zeit = None
    if zeit is None or abs((jetzt() - zeit).total_seconds()) > 24 * 3600:
        return
    zone = str(konfig.wert("zeit.zeitzone", "Europe/Berlin"))
    versatz = status.get("utc_offset_min")
    if _ganz(versatz, -1080, 1080):
        soll = int(utc_zu_lokal(zeit, zone).utcoffset().total_seconds() // 60)
        if soll != versatz:
            _melde_taeglich(con, konfig, "zeitzone", f"🕐 Die Uhr deines PCs steht auf {_utc_text(versatz)}, ich "
                            f"rechne mit {zone} ({_utc_text(soll)}). Dann passen deine Clips nicht zu den Kills – stell "
                            "in Windows die richtige Zeitzone ein. Wohnst du woanders, sag Florian Bescheid.")
    eintraege = status.get("uebersprungen") if isinstance(status.get("uebersprungen"), list) else []
    ohne = sum(e["anzahl"] for e in eintraege
               if isinstance(e, dict) and e.get("grund") == "ohne_replay" and _ganz(e.get("anzahl"), 1, 10**6))
    if ohne:
        _melde_taeglich(con, konfig, "ohne_replay", f"🎬 Auf deinem PC liegen {ohne} Aufnahme"
                        f"{'n' if ohne != 1 else ''} ohne Replay – ist in Fortnite „Replays aufzeichnen“ an? Ohne "
                        "Replay finde ich deine Kills nicht; solche Aufnahmen bleiben auf deinem PC.")


def pc_status(konfig: Konfig) -> dict | None:
    """Der letzte Status vom PC (I/db/pc-status.json) als dict – None, wenn es keinen gibt oder er nicht lesbar ist."""
    try:
        with open(pc_status_datei(konfig), "rb") as datei:
            roh = datei.read(STATUS_MAX + 1)
        daten = json.loads(roh.decode("utf-8-sig")) if len(roh) <= STATUS_MAX else None
    except (OSError, ValueError):
        return None
    return daten if isinstance(daten, dict) else None


def unterwegs(con: sqlite3.Connection, status: dict | None) -> int:
    """Was auf dem Weg zum Puffer ist: noch auf dem PC (offen laut seinem Status) plus noch im Briefkasten (abholung
    offen). Nur zum Anzeigen (📋)."""
    n = 0
    eintraege = (status or {}).get("offen")
    for e in eintraege if isinstance(eintraege, list) else []:
        if isinstance(e, dict) and _ganz(e.get("anzahl"), 0, 10**6):
            n += e["anzahl"]
    try:
        n += con.execute("SELECT COUNT(*) FROM abholung WHERE status = 'offen'").fetchone()[0]
    except sqlite3.OperationalError:   # noch nie abgeholt: keine Tabelle
        pass
    return n


def _platz(pfad: Path) -> tuple[int, int]:
    """(frei, gesamt) in Byte auf dem Dateisystem von pfad (das Freunde-Volume)."""
    st = os.statvfs(pfad)
    return st.f_bavail * st.f_frsize, st.f_blocks * st.f_frsize


def _fsync_ordner(ordner: Path) -> None:
    fd = os.open(ordner, os.O_RDONLY)
    try:
        os.fsync(fd)
    except OSError as e:
        if e.errno not in (errno.EINVAL, errno.EBADF, errno.ENOTSUP):
            raise
    finally:
        os.close(fd)


def _sha_kalt(pfad: Path) -> str:
    """SHA-256 vom Datenträger: erst fsync, dann den Seiten-Cache verwerfen – nicht aus dem eigenen Speicher."""
    fd = os.open(pfad, os.O_RDONLY)
    try:
        try:
            os.fsync(fd)
        except OSError as e:
            if e.errno not in (errno.EINVAL, errno.EBADF, errno.ENOTSUP):
                raise
        if hasattr(os, "posix_fadvise"):
            os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
        h = hashlib.sha256()
        while block := os.read(fd, BLOCK):
            h.update(block)
        return h.hexdigest()
    finally:
        os.close(fd)


# --- Abholen -------------------------------------------------------------------------------------------------------

@dataclass
class _Lauf:
    con: sqlite3.Connection
    konfig: Konfig
    ablage: Path
    e: dict
    weiter: bool = True     # False: Verbindung weg oder Bremse – in diesem Lauf nichts mehr holen

    def zeile(self, nr: int) -> sqlite3.Row:
        return self.con.execute("SELECT * FROM abholung WHERE id = ?", (nr,)).fetchone()

    def teil(self, z) -> Path:
        return self.ablage / f"{z['id']}.teil"

    def offen(self, z) -> bool:
        """Nach einem Versuch: noch offen (zählt als wartend) → False, sonst erledigt → True."""
        if self.zeile(z["id"])["status"] == "offen":
            self.e["wartet"] += 1
            return False
        return True

    def fehlversuch(self, z, grund: str, *, teil_weg: bool = False) -> None:
        """Ein gescheiterter Versuch; nach VERSUCHE aufgegeben (nur Log und eine Zeile an den Freund)."""
        if teil_weg:
            self.teil(z).unlink(missing_ok=True)
        self.con.execute("UPDATE abholung SET versuche = versuche + 1, grund = ? WHERE id = ?", (grund, z["id"]))
        n = self.zeile(z["id"])["versuche"]
        log.warning("Briefkasten: %s/%s – %s (Versuch %d von %d)", z["ordner"], z["name"], grund, n, VERSUCHE)
        self.e["fehler"].append(f"{z['ordner']}/{z['name']}: {grund}"[:300])
        if n >= VERSUCHE:
            self.aufgeben(z, grund)

    def aufgeben(self, z, grund: str) -> None:
        self.abschluss(z, "aufgegeben", grund)
        self.e["aufgegeben"] += 1
        log.error("Briefkasten: %s/%s aufgegeben – %s", z["ordner"], z["name"], grund)
        db.lern_meldung(self.con, f"briefkasten:aufgegeben:{z['id']}",
                        f"⚠️ Eine Datei aus deinem Briefkasten konnte ich nicht übernehmen ({z['name']}) – ich lasse "
                        "sie aus. Deine anderen Aufnahmen kommen normal weiter. Sag Florian Bescheid.")

    def abschluss(self, z, status: str, grund: str | None = None, ziel_rel: str | None = None) -> None:
        self.teil(z).unlink(missing_ok=True)
        self.con.execute("UPDATE abholung SET status = ?, grund = COALESCE(?, grund), ziel = ?, abgeholt = ? "
                         "WHERE id = ?", (status, grund, ziel_rel, iso(jetzt()), z["id"]))

    def platz_ok(self, noch: int) -> bool:
        """Bremse: nur holen, wenn danach max(10 GB, 10 %) des Freunde-Volumes frei bleiben."""
        frei, gesamt = _platz(self.konfig.wurzel)
        if frei - noch >= max(RESERVE_BYTE, RESERVE_ANTEIL * gesamt):
            return True
        self.weiter, self.e["bremse"] = False, True
        log.warning("Briefkasten: nur %.1f GB frei – ich hole nichts (Bremse)", frei / 1e9)
        _melde_taeglich(self.con, self.konfig, "platz", "⚠️ Bei mir wird der Platz für deine Aufnahmen knapp – ich "
                        "hole gerade nichts ab. Sie warten sicher im Briefkasten. Sag Florian Bescheid.")
        return False


def abholen(con: sqlite3.Connection, konfig: Konfig) -> dict:
    """Ein Lauf (Timer alle 2 min). JSON: abgeholt, gb, wartet, fehler, fach_prozent (dazu vermerkt, aufgegeben,
    bremse; unerreichbar, wenn der Briefkasten oder das Fach nicht da war). Ohne host: {"aus": true}."""
    _nur_instanz(konfig)
    if not aktiv(konfig):
        return {"aus": True, "hinweis": "kein Briefkasten eingerichtet ([briefkasten].host leer)"}
    with sperre(sperrdatei(konfig), warten_s=0):
        return _abholen(con, konfig)


def _abholen(con: sqlite3.Connection, konfig: Konfig) -> dict:
    _pruefe_zugang(konfig)
    konfig.pruefe_speicher()     # Puffer-Marke: sonst SpeicherOffline (Exit 3), nichts geholt
    _tabelle(con)
    ablage = konfig.wurzel / ABLAGE
    ablage.mkdir(exist_ok=True)
    e = {"abgeholt": 0, "gb": 0.0, "wartet": 0, "fehler": [], "fach_prozent": None, "vermerkt": 0, "aufgegeben": 0,
         "bremse": False}
    stand, zeit = _stand_lesen(konfig), iso(jetzt())
    stand.setdefault("erster_versuch", zeit)
    stand["zuletzt_versucht"] = zeit
    fach = _liste(konfig, ablage)
    if not fach.erreicht:
        stand["letzter_fehler"] = fach.fehler
        _schreibe(_stand_datei(konfig), json.dumps(stand, ensure_ascii=False, indent=1).encode())
        _melde_unerreichbar(con, konfig, stand)
        log.warning("Briefkasten: %s", fach.fehler)
        return {**e, "fehler": [fach.fehler], "unerreichbar": True}
    stand.update(zuletzt_erreicht=zeit, letzter_fehler=None, fach_prozent=fach.prozent, fach_frei_gb=fach.frei_gb)
    _schreibe(_stand_datei(konfig), json.dumps(stand, ensure_ascii=False, indent=1).encode())
    _pc_status(con, konfig, ablage / "pc-status.json")
    e["fach_prozent"] = fach.prozent
    if fach.prozent is not None and fach.prozent >= VOLL_PROZENT:
        _melde_taeglich(con, konfig, "voll", f"📦 Dein Briefkasten bei Florian ist zu {fach.prozent} % voll. Ist er "
                        "ganz voll, bleiben neue Aufnahmen auf deinem PC, bis wieder Platz ist – sag Florian Bescheid.")
    lauf = _Lauf(con, konfig, ablage, e)
    offen = _kandidaten(lauf, fach)
    video_offen = False
    for z in sorted(offen["videos"], key=lambda z: (z["pc_mtime_ms"] or 0, z["name"])):   # älteste zuerst
        if not lauf.weiter:
            e["wartet"] += 1
            video_offen = True
        elif not _datei(lauf, z):
            video_offen = True
    for z in sorted(offen["replays"], key=lambda z: z["name"]):
        if video_offen or not lauf.weiter:   # ein Replay nie vor allen gelisteten Videos (sonst fehlen Clips)
            e["wartet"] += 1
        else:
            _datei(lauf, z)
    for z in sorted(offen["sitzungen"], key=lambda z: z["name"]):
        if not lauf.weiter:
            e["wartet"] += 1
        else:
            _sitzung(lauf, z)
    e["gb"], e["fehler"] = round(e["gb"], 2), e["fehler"][:20]
    log.info("Briefkasten: %d abgeholt (%.2f GB), %d warten, Fach %s %%", e["abgeholt"], e["gb"], e["wartet"],
             e["fach_prozent"])
    return e


def _kandidaten(lauf: _Lauf, fach: Fach) -> dict[str, list[sqlite3.Row]]:
    """Gelistete Paare <name> + <name>.lieferschein mit gültigem Namen, die noch offen sind (je Ordner). Neue bekommen
    eine Zeile (gesehen = jetzt); fehlt ihnen ein gültiger Lieferschein, wird er geholt und streng geprüft."""
    con = lauf.con
    bekannt = {(z["ordner"], z["name"]): z for z in con.execute("SELECT * FROM abholung")}
    gelistet: list[tuple[str, str]] = []
    for ordner in LISTE:
        namen = fach.namen.get(ordner, set())
        for name in sorted(namen):
            if f"{name}{LIEFERSCHEIN}" not in namen or ziel(lauf.konfig, ordner, name) is None:
                continue   # unterwegs (noch ohne Lieferschein), selbst ein Lieferschein oder fremd: nie anfassen
            z = bekannt.get((ordner, name))
            if z is None:
                con.execute("INSERT INTO abholung (ordner, name, gesehen) VALUES (?, ?, ?)",
                            (ordner, name, iso(jetzt())))
            elif z["status"] != "offen":
                continue
            gelistet.append((ordner, name))
    zeilen = [con.execute("SELECT * FROM abholung WHERE ordner = ? AND name = ?", p).fetchone() for p in gelistet]
    if ohne := [z for z in zeilen if z["groesse"] is None]:
        _lieferscheine(lauf, ohne)
    offen: dict[str, list[sqlite3.Row]] = {o: [] for o in LISTE}
    for z in zeilen:
        z = lauf.zeile(z["id"])
        if z["status"] == "offen":
            offen[z["ordner"]].append(z)
    return offen


def _lieferscheine(lauf: _Lauf, zeilen: list[sqlite3.Row]) -> None:
    """Die fehlenden Lieferscheine in einem Lauf holen (je höchstens 4 KB) und prüfen. Ein ungültiger zählt als Versuch."""
    lokal = [lauf.ablage / f"{z['id']}.lieferschein" for z in zeilen]
    befehle = []
    for z, pfad in zip(zeilen, lokal):
        pfad.unlink(missing_ok=True)
        befehle.append(f"-get {_q(z['ordner'] + '/' + z['name'] + LIEFERSCHEIN)} {_q(str(pfad))}")
    r = _sftp(lauf.konfig, befehle, max_datei=LIEFERSCHEIN_MAX + 1, timeout=LISTEN_S)
    try:
        if r.returncode != 0:   # Verbindung weg: zählt nicht, nächster Lauf
            lauf.weiter = False
            lauf.e["fehler"].append(f"Lieferscheine: Briefkasten nicht erreichbar ({_letzte(r.stderr)})")
            return
        for z, pfad in zip(zeilen, lokal):
            try:
                with open(pfad, "rb") as f:
                    roh = f.read(LIEFERSCHEIN_MAX + 1)
            except FileNotFoundError:
                roh = b""
            try:
                werte = pruefe_lieferschein(roh, z["ordner"], z["name"])
            except ValueError as fehler:   # auch kaputtes JSON und falsche Kodierung
                lauf.fehlversuch(z, f"Lieferschein {fehler}")
                continue
            lauf.con.execute("UPDATE abholung SET groesse = :groesse, sha256 = :sha256, pc_mtime_ms = :pc_mtime_ms, "
                             "utc_offset_min = :utc_offset_min WHERE id = :id", {**werte, "id": z["id"]})
    finally:
        for pfad in lokal:
            pfad.unlink(missing_ok=True)


def _schon_da(lauf: _Lauf, z, ziel_rel: str) -> bool:
    """Liegt im Puffer schon eine Datei unter diesem Namen? Gleicher Inhalt: nur vermerken; anderer: Konflikt – sie
    bleibt im Fach, die vorhandene wird nie überschrieben. Beides ohne die Datei erst zu holen."""
    pfad = lauf.konfig.wurzel / ziel_rel
    if not os.path.lexists(pfad):
        return False
    gleich = pfad.is_file() and not pfad.is_symlink() and pfad.stat().st_size == z["groesse"] \
        and _sha_kalt(pfad) == z["sha256"]
    _erledigt_vorhanden(lauf, z, ziel_rel, gleich)
    return True


def _erledigt_vorhanden(lauf: _Lauf, z, ziel_rel: str, gleich: bool) -> None:
    if gleich:
        lauf.abschluss(z, "vermerkt", "liegt schon im Puffer", ziel_rel)
        lauf.e["vermerkt"] += 1
        log.info("Briefkasten: %s/%s liegt schon im Puffer – nur vermerkt", z["ordner"], z["name"])
    else:
        lauf.abschluss(z, "konflikt", "anderer Inhalt unter diesem Namen im Puffer – bleibt im Fach", ziel_rel)
        lauf.e["fehler"].append(f"{z['ordner']}/{z['name']}: Konflikt – anderer Inhalt schon im Puffer")
        log.warning("Briefkasten: %s/%s – im Puffer liegt schon eine andere Datei unter diesem Namen; sie bleibt im "
                    "Fach", z["ordner"], z["name"])


def _hole(lauf: _Lauf, z) -> Path | None:
    """Sorgt für eine geprüfte Zwischendatei .abholen/<nr>.teil: reget (setzt fort), dann Größe und SHA-256 kalt gegen
    den Lieferschein. None = (noch) nicht – ein Fehler zählt als Versuch, eine abgerissene Verbindung nicht."""
    teil, groesse = lauf.teil(z), z["groesse"]
    vorher = teil.stat().st_size if teil.is_file() else 0
    if vorher > groesse:
        teil.unlink()
        vorher = 0
    if vorher < groesse:
        if not lauf.platz_ok(groesse - vorher):
            return None
        kbit = int(lauf.konfig.wert("briefkasten.drossel_kbit", 0) or 0)
        frist = LISTEN_S + 3 * (groesse - vorher) / (kbit * 125) if kbit > 0 else None
        r = _sftp(lauf.konfig, [f"reget {_q(z['ordner'] + '/' + z['name'])} {_q(str(teil))}"], max_datei=groesse,
                  timeout=frist)
        jetzt_da = teil.stat().st_size if teil.is_file() else 0
        if jetzt_da < groesse:
            if r.returncode == 255:      # Verbindung weg: zählt nicht, der nächste Lauf setzt fort
                lauf.weiter = False
                lauf.e["fehler"].append(f"{z['ordner']}/{z['name']}: Verbindung abgebrochen ({_letzte(r.stderr)})")
                log.warning("Briefkasten: %s/%s – Verbindung abgebrochen bei %d von %d Byte", z["ordner"], z["name"],
                            jetzt_da, groesse)
            elif r.returncode == 0:      # vollständig geholt, aber kleiner als versprochen
                lauf.fehlversuch(z, f"kleiner als im Lieferschein ({jetzt_da} statt {groesse} Byte)", teil_weg=True)
            elif jetzt_da == vorher:     # kein Fortschritt: liegt an der Datei
                lauf.fehlversuch(z, f"lässt sich nicht holen ({_letzte(r.stderr)})")
            return None
    if _sha_kalt(teil) != z["sha256"]:
        lauf.fehlversuch(z, "Prüfsumme weicht ab", teil_weg=True)
        return None
    return teil


def _veroeffentliche(lauf: _Lauf, z, teil: Path, ziel_rel: str) -> None:
    """mtime = min(PC-Zeit, jetzt − 130 s), dann os.link auf den Endnamen (überschreibt nie), .teil weg, Ordner fsync."""
    lauf.konfig.pruefe_speicher()     # Puffer noch eingehängt? Sonst SpeicherOffline (Exit 3) statt in den Einhängepunkt
    pfad = lauf.konfig.wurzel / ziel_rel
    pfad.parent.mkdir(parents=True, exist_ok=True)
    mtime_ns = min(z["pc_mtime_ms"] * 10**6, time.time_ns() - KAPPE_S * 10**9)   # ganzzahlig: keine Millisekunde weg
    os.utime(teil, ns=(mtime_ns, mtime_ns))
    try:
        os.link(teil, pfad)
    except FileExistsError:   # eben erst entstanden (Florian von Hand?) – wie _schon_da
        _erledigt_vorhanden(lauf, z, ziel_rel, pfad.is_file() and _sha_kalt(pfad) == z["sha256"])
        return
    _fsync_ordner(pfad.parent)
    teil.unlink()
    _fsync_ordner(lauf.ablage)          # erst dann die Zeile: nach einem Absturz bleibt kein Rest in .abholen
    lauf.abschluss(z, "abgeholt", None, ziel_rel)
    lauf.e["abgeholt"] += 1
    lauf.e["gb"] += z["groesse"] / 1e9
    log.info("Briefkasten: %s/%s → %s", z["ordner"], z["name"], ziel_rel)


def _datei(lauf: _Lauf, z) -> bool:
    """Eine Aufnahme oder ein Replay: holen, prüfen, veröffentlichen. True = erledigt (auch aufgegeben oder Konflikt)."""
    if z["groesse"] is None:            # noch ohne gültigen Lieferschein (ein Versuch ist gezählt)
        return lauf.offen(z)
    ziel_rel = ziel(lauf.konfig, z["ordner"], z["name"])
    if _schon_da(lauf, z, ziel_rel):
        return True
    if (teil := _hole(lauf, z)) is None:
        return lauf.offen(z)
    _veroeffentliche(lauf, z, teil, ziel_rel)
    return True


def _abgeholte_matches(con: sqlite3.Connection, ausser: int) -> set[str]:
    matches: set[str] = set()
    for z in con.execute("SELECT matches FROM abholung WHERE ordner = 'sitzungen' AND status IN ('abgeholt', "
                         "'vermerkt') AND matches IS NOT NULL AND id != ?", (ausser,)):
        matches.update(json.loads(z["matches"]))
    return matches


def _matches_fertig(con: sqlite3.Connection, matches: list[str]) -> bool:
    """Alle Matches verarbeitet oder aufgegeben (scan --versuche: 'fehler')?"""
    status = {z["id"]: z["status"] for z in con.execute(
        f"SELECT id, status FROM matches WHERE id IN ({','.join('?' for _ in matches)})", matches)}
    return all(status.get(m) in ("verarbeitet", "fehler") for m in matches)


def _sitzung(lauf: _Lauf, z) -> bool:
    """Eine Sitzungsdatei (Abend vorbei): holen und prüfen, dann warten, bis ihre Matches verarbeitet oder aufgegeben
    sind – spätestens 24 h nach dem ersten Sehen. Stehen alle ihre Matches schon in einer abgeholten Sitzung (z. B. nach
    einer Neuinstallation des PCs), nur vermerken. True = erledigt."""
    if z["groesse"] is None:
        return lauf.offen(z)
    ziel_rel = ziel(lauf.konfig, z["ordner"], z["name"])
    if _schon_da(lauf, z, ziel_rel):
        return True
    if (teil := _hole(lauf, z)) is None:   # klein; wartet geprüft in .abholen, bis die Matches fertig sind
        return lauf.offen(z)
    if z["matches"] is None:
        try:
            matches = pruefe_sitzung(teil.read_bytes())
        except ValueError as fehler:   # geprüft wie im Lieferschein – ein neuer Versuch brächte dasselbe
            lauf.e["fehler"].append(f"{z['ordner']}/{z['name']}: Sitzungsdatei ungültig ({fehler})")
            lauf.aufgeben(z, f"Sitzungsdatei ungültig: {fehler}")
            return True
        lauf.con.execute("UPDATE abholung SET matches = ? WHERE id = ?", (json.dumps(matches), z["id"]))
    else:
        matches = json.loads(z["matches"])
    if set(matches) <= _abgeholte_matches(lauf.con, z["id"]):
        lauf.abschluss(z, "vermerkt", "alle Matches stehen schon in einer abgeholten Sitzung")
        lauf.e["vermerkt"] += 1
        return True
    spaetestens = aus_iso(z["gesehen"]) + timedelta(hours=SITZUNG_SPAETESTENS_H)
    if not _matches_fertig(lauf.con, matches) and jetzt() < spaetestens:
        lauf.e["wartet"] += 1
        return False
    _veroeffentliche(lauf, z, teil, ziel_rel)
    return True


# --- Status --------------------------------------------------------------------------------------------------------

def status(con: sqlite3.Connection, konfig: Konfig) -> dict:
    """Nur nachsehen, ohne Netz: letzter Kontakt, Füllstand, Zahl der Dateien je Zustand, ob ein PC-Status da ist."""
    _nur_instanz(konfig)
    if not aktiv(konfig):
        return {"aus": True, "hinweis": "kein Briefkasten eingerichtet ([briefkasten].host leer)"}
    _tabelle(con)
    zahlen = {z["status"]: z["n"] for z in con.execute("SELECT status, COUNT(*) AS n FROM abholung GROUP BY status")}
    stand = _stand_lesen(konfig)
    return {"aus": False, "host": str(konfig.wert("briefkasten.host")).strip(), "port": konfig.wert("briefkasten.port"),
            "benutzer": konfig.wert("briefkasten.benutzer"),
            **{s: zahlen.get(s, 0) for s in ("abgeholt", "offen", "vermerkt", "aufgegeben", "konflikt")},
            "zuletzt_erreicht": stand.get("zuletzt_erreicht"), "zuletzt_versucht": stand.get("zuletzt_versucht"),
            "fach_prozent": stand.get("fach_prozent"), "letzter_fehler": stand.get("letzter_fehler"),
            "pc_status": pc_status_datei(konfig).is_file()}
