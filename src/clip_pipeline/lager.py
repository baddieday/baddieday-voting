"""Puffer ↔ Lager (E19): täglicher Abgleich Puffer → Lager (10:00), einmalige Übernahme Lager → Puffer.

Puffer = [speicher].wurzel auf dem Mini (hier arbeitet die Pipeline), Lager = [lager].wurzel auf pve-big (NFS).

Harte Regeln:
  - Bestätigt ist eine Datei erst, wenn ihre Kopie zurückgelesen wurde und die SHA-256 stimmt (Tabelle `lager`).
  - Rohdaten ([lager].roh: eingang/, replays/) werden im Lager nie überschrieben oder gelöscht. Liegt dort schon
    eine andere Fassung, kommt die aus dem Puffer daneben: name~<mtime_ns>.ext – und du bekommst eine Meldung.
  - pve-big wird nur geweckt, wenn im Puffer etwas offen ist. Vorher wird das Lager nicht angefasst: ein Blick
    auf den NFS-Mount eines schlafenden pve-big hinge.
  - In der Nachtruhe ([lager].nachtruhe_von/_bis) weckt der Abgleich nie – sein Lüfter soll niemanden wecken.
    Läuft pve-big ohnehin, darf abgeglichen werden.
  - Im Puffer wird nur zweierlei gelöscht: alte DB-Sicherungen (keine Rohdaten, im Lager bleiben sie) und – Stufe B5,
    Florian 08.10. – Rohvideos aus eingang/, die älter als [puffer].rohdaten_tage sind und deren Kopie im Lager
    bestätigt ist (gib_frei, nur am Ende eines fehlerfreien Abgleichs). Im Lager nie etwas: Wird es knapp, warnt die
    Morgenprüfung (freier Platz, beim Abgleich per statvfs gemessen).

Eigene Sperre <datenbank>.lager.lock (nicht die Pipeline-Sperre): Abgleich und Übernahme laufen nie doppelt,
die Pipeline arbeitet währenddessen im Puffer weiter.
"""

from __future__ import annotations

import errno
import hashlib
import itertools
import json
import logging
import os
import re
import shutil
import sqlite3
import stat
import time
from contextlib import ExitStack, contextmanager, suppress
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Iterator

from . import big, db
from .bestand import VIDEO_ENDUNGEN
from .konfig import Konfig, KonfigFehler, SpeicherOffline
from .material import BLOCK, sha256
from .sperre import sperre
from .zeit import UTC, aus_iso, im_zeitfenster, iso, jetzt, utc_zu_lokal

log = logging.getLogger("pipeline")

ORDNER = ["eingang", "replays", "sessions", "highlights", "sitzungen", "musik", "archiv", "sicherung"]
ROH = ["eingang", "replays"]
SICHERUNG = "sicherung"                               # tägliche DB-Sicherungen (steht in [lager].ordner)
SICHERUNG_NAME = re.compile(r"^pipeline-\d{4}-\d{2}-\d{2}\.db$")
TEIL = re.compile(r"^\..+\.\d+\.teil$")               # eigene Zwischendateien: .<name>.<pid>.teil
TEIL_ALT_S = 3600
LISTE_MAX = 20                                        # so viele Beispiele höchstens in der JSON-Zeile
# Bei diesen Fehlern ist das Ziel selbst gestört (NFS weg, voll) – weitere Dateien zu versuchen hilft nicht
ABBRUCH = {getattr(errno, n) for n in ("EIO", "ESTALE", "ENOTCONN", "ETIMEDOUT", "ENOSPC") if hasattr(errno, n)}


class LagerFehler(RuntimeError):
    """Eine Datei ließ sich nicht sicher kopieren (geändert, Prüfsumme) – sie bleibt unbestätigt."""


@dataclass
class Offen:
    pfad: Path        # Datei im Puffer
    relativ: str      # relativ zur Wurzel, '/' als Trenner
    groesse: int
    mtime_ns: int
    roh: bool         # eingang/, replays/: im Lager nie überschreiben
    gesehen: str      # ISO-UTC, für lager.zuerst_gesehen


# --- Hilfen -----------------------------------------------------------------------

def _roh(konfig: Konfig) -> set[str]:
    return {str(n) for n in konfig.wert("lager.roh", ROH)}


def _ordner(konfig: Konfig) -> list[str]:
    """[lager].ordner, Rohdaten zuerst: bricht ein Lauf ab, sind die wichtigsten Dateien schon drüben."""
    roh = _roh(konfig)
    return sorted((str(n) for n in konfig.wert("lager.ordner", ORDNER)), key=lambda n: n not in roh)


def _kennung(st: os.stat_result) -> tuple[int, int]:
    return st.st_size, st.st_mtime_ns


def _zwischendatei(name: str) -> bool:
    """Versteckt (.aktiv, Marken, eigene .teil) oder unfertig (x.teil, x.tmp, x.tmp.mp4 aus dem Bestand)."""
    return name.startswith(".") or name.endswith((".teil", ".tmp")) or ".tmp." in name or ".teil." in name


def _dateien(ordner: Path) -> Iterator[os.DirEntry]:
    """Alle normalen Dateien unter ordner. Links wird nie gefolgt, versteckte Ordner bleiben zu."""
    stapel = [ordner]
    while stapel:
        try:
            with os.scandir(stapel.pop()) as eintraege:
                liste = list(eintraege)
        except FileNotFoundError:
            continue
        for e in liste:
            if e.is_dir(follow_symlinks=False):
                if not e.name.startswith("."):
                    stapel.append(Path(e.path))
            elif e.is_file(follow_symlinks=False):
                yield e


def _ruhe_grenze(konfig: Konfig) -> float:
    """Unix-Zeit: jünger (mtime ODER ctime) = wird evtl. noch geschrieben ([lager].ruhe_min)."""
    return time.time() - float(konfig.wert("lager.ruhe_min", 10)) * 60


def _jung(st: os.stat_result, grenze: float) -> bool:
    return max(st.st_mtime, st.st_ctime) > grenze


def _kandidaten(konfig: Konfig, wurzel: Path, fertig: Iterable[str] = (), *,
                ruhe: bool = True) -> Iterator[tuple[str, os.stat_result, bool]]:
    """(relativ, stat, roh) aller fertigen Dateien in [lager].ordner unter wurzel.
    Jünger als [lager].ruhe_min (mtime ODER ctime) = wird evtl. noch geschrieben – außer sie steht in fertig.
    ruhe=False: auch die jungen liefern (der Aufrufer zählt sie selbst, statt sie still auszulassen)."""
    fertig = set(fertig)
    grenze = _ruhe_grenze(konfig)
    roh = _roh(konfig)
    for name in _ordner(konfig):
        treffer = []
        for e in _dateien(wurzel / name):
            if _zwischendatei(e.name):
                continue
            st = e.stat(follow_symlinks=False)
            rel = f"{name}/{Path(e.path).relative_to(wurzel / name).as_posix()}"
            if ruhe and rel not in fertig and _jung(st, grenze):
                continue
            treffer.append((rel, st, name in roh))
        yield from sorted(treffer, key=lambda t: t[0])


def _bekannt(con: sqlite3.Connection) -> dict[str, tuple[int, int]]:
    return {z["relativ"]: (z["groesse"], z["mtime_ns"]) for z in con.execute("SELECT relativ, groesse, mtime_ns FROM lager")}


def _bestaetige(con: sqlite3.Connection, relativ: str, kennung: tuple[int, int], sha: str, lager_relativ: str,
                gesehen: str) -> None:
    con.execute(
        """INSERT INTO lager (relativ, groesse, mtime_ns, sha256, bestaetigt, lager_relativ, zuerst_gesehen)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (relativ) DO UPDATE SET groesse = excluded.groesse, mtime_ns = excluded.mtime_ns,
               sha256 = excluded.sha256, bestaetigt = excluded.bestaetigt, lager_relativ = excluded.lager_relativ,
               zuerst_gesehen = COALESCE(lager.zuerst_gesehen, excluded.zuerst_gesehen)""",
        (relativ, kennung[0], kennung[1], sha, iso(jetzt()), lager_relativ, gesehen),
    )


def _heute(konfig: Konfig) -> str:
    return utc_zu_lokal(jetzt(), konfig.wert("zeit.zeitzone", "Europe/Berlin")).date().isoformat()


def _gb(n: float) -> float:
    return round(n / 1e9, 2)


def _fsync_ordner(ordner: Path) -> None:
    """Auch der neue Name muss auf dem Datenträger stehen, nicht nur der Inhalt."""
    if os.name != "posix":
        return
    fd = os.open(ordner, os.O_RDONLY)
    try:
        os.fsync(fd)
    except OSError as e:
        if e.errno not in (errno.EINVAL, errno.EBADF, errno.ENOTSUP):  # manche Dateisysteme können das nicht
            raise
    finally:
        os.close(fd)


def _marken_da(konfig: Konfig) -> None:
    """Vor jeder Datei: sind Puffer und Lager noch eingehängt? Fehlt ein Mount, lägen die Kopien sonst im
    leeren Einhängepunkt (und füllten die Platte des Mini)."""
    if not konfig._lager_markierung_da():
        raise SpeicherOffline(f"Lager-Markierung {konfig._lager_markierung()} verschwunden – NFS von pve-big weg?")
    puffer = konfig.wurzel / str(konfig.wert("puffer.markierung", ".clip-puffer"))
    if not puffer.is_file():
        raise SpeicherOffline(f"Puffer-Markierung {puffer} verschwunden – Puffer nicht eingehängt?")


def nachtruhe(konfig: Konfig, zeit: datetime | None = None) -> bool:
    """Liegt zeit (Ortszeit) in [lager].nachtruhe_von … nachtruhe_bis? Dann weckt der Abgleich pve-big nicht.
    Leer = aus. Anders als bei [telegram].leise_* schaltet ein Tippfehler diese Grenze nicht still ab: KonfigFehler."""
    von, bis = (str(konfig.wert(f"lager.nachtruhe_{n}", s) or "") for n, s in (("von", "22:00"), ("bis", "08:00")))
    try:
        return im_zeitfenster(zeit or jetzt(), von, bis, konfig.wert("zeit.zeitzone", "Europe/Berlin"))
    except ValueError:
        raise KonfigFehler(f"[lager] nachtruhe_von/nachtruhe_bis ungültig ({von!r}/{bis!r}) – "
                           "erwartet z. B. \"22:00\", leer = aus") from None


@contextmanager
def _wach(konfig: Konfig, name: str, grund: str, wecken: bool = True) -> Iterator[None]:
    """pve-big für die Aufgabe wach halten (weckt nur, wenn nötig und erlaubt, und fährt ihn danach herunter).
    Ohne eingetragenen Host gibt es nichts zu wecken – dann entscheiden nur die Marken.
    wecken=False (Nachtruhe): nur, wenn er schon läuft – sonst WeckenVerboten."""
    if not big.host(konfig):
        yield
        return
    with big.wach_halten(konfig, name, grund, minuten=float(konfig.wert("lager.halten_min", 240)), wecken=wecken):
        yield


def _miss_platz(lager: Path, e: dict) -> None:
    """Freien Platz im Lager ins Ergebnis schreiben (lager_frei_gb, lager_gesamt_gb) – für Morgenprüfung und /status.
    Nur statvfs (über NFS ein GETATTR bzw. FSSTAT): kein Dateiinhalt, für clip-leerlauf kein Zugriff. Nur aufrufen,
    wenn das Lager eben geprüft eingehängt ist – auf dem Mount eines schlafenden pve-big hinge auch statvfs."""
    if not hasattr(os, "statvfs"):
        return
    try:
        st = os.statvfs(lager)
    except OSError as f:
        log.warning("Lager: freier Platz nicht messbar: %s", f)
        return
    e["lager_frei_gb"] = _gb(st.f_bavail * st.f_frsize)
    e["lager_gesamt_gb"] = _gb(st.f_blocks * st.f_frsize)


def raeume_teile(ordner: Iterable[Path]) -> int:
    """Reste eigener Zwischendateien (.<name>.<pid>.teil, älter als 1 h) wegräumen – z. B. nach einem Stromausfall.
    Nur in den Ordnern, in die gleich geschrieben wird (dort entstehen sie); nie etwas anderes."""
    grenze = time.time() - TEIL_ALT_S
    weg = 0
    for o in set(ordner):
        try:
            with os.scandir(o) as eintraege:
                reste = [e for e in eintraege if TEIL.match(e.name) and e.is_file(follow_symlinks=False)
                         and e.stat(follow_symlinks=False).st_mtime < grenze]
        except (FileNotFoundError, NotADirectoryError):
            continue
        for e in reste:
            try:
                os.unlink(e.path)
                weg += 1
            except OSError as fehler:
                log.warning("Rest %s nicht entfernt: %s", e.path, fehler)
    if weg:
        log.info("%d alte Zwischendatei(en) weggeräumt", weg)
    return weg


# --- Kopieren mit Prüfung -----------------------------------------------------------

def kopiere_geprueft(quelle: Path, ziel: Path, erwartet: tuple[int, int] | None = None) -> str:
    """Kopiert quelle nach ziel und liefert die SHA-256. Die Quelle wird nur gelesen.

    Die Kopie entsteht als .<name>.<pid>.teil daneben und bekommt ihren Namen erst, wenn sie zurückgelesen und
    gleich ist. Vor dem Zurücklesen wird der Seiten-Cache verworfen (POSIX_FADV_DONTNEED) – so kommt der Vergleich
    vom Datenträger bzw. vom NFS-Server und nicht aus dem eigenen Speicher. Ändert sich die Quelle währenddessen
    (Größe/mtime vorher ≠ nachher) oder weicht sie von erwartet ab: Abbruch „geändert“. Fehler: Zwischendatei weg."""
    vorher = os.stat(quelle)
    if erwartet is not None and _kennung(vorher) != erwartet:
        raise LagerFehler(f"{quelle.name}: geändert seit dem Sammeln")
    ziel.parent.mkdir(parents=True, exist_ok=True)
    teil = ziel.with_name(f".{ziel.name}.{os.getpid()}.teil")
    fd = os.open(teil, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o644)
    try:
        h = hashlib.sha256()
        menge = 0
        with open(quelle, "rb") as ein, os.fdopen(fd, "wb", closefd=False) as aus:
            while block := ein.read(BLOCK):
                h.update(block)
                aus.write(block)
                menge += len(block)
            aus.flush()
            os.fsync(fd)
        if hasattr(os, "posix_fadvise"):
            with suppress(OSError):
                os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
        os.close(fd)
        fd = -1
        if sha256(teil) != h.hexdigest():
            raise LagerFehler(f"{quelle.name}: Prüfsumme der Kopie weicht ab")
        if menge != vorher.st_size or _kennung(os.stat(quelle)) != _kennung(vorher):
            raise LagerFehler(f"{quelle.name}: während der Kopie geändert")
        os.utime(teil, ns=(vorher.st_atime_ns, vorher.st_mtime_ns))
        os.replace(teil, ziel)
    except BaseException:
        if fd >= 0:
            os.close(fd)
        with suppress(OSError):
            teil.unlink(missing_ok=True)
        raise
    _fsync_ordner(ziel.parent)
    return h.hexdigest()


def _gleicher_inhalt(quelle: Path, ziel: Path, erwartet: tuple[int, int]) -> str | None:
    """SHA-256, wenn ziel eine normale Datei mit genau dem Inhalt von quelle ist – sonst None."""
    zst = os.lstat(ziel)
    if not stat.S_ISREG(zst.st_mode) or zst.st_size != erwartet[0]:
        return None
    if _kennung(os.stat(quelle)) != erwartet:
        raise LagerFehler(f"{quelle.name}: geändert seit dem Sammeln")
    sha = sha256(quelle)
    if _kennung(os.stat(quelle)) != erwartet:
        raise LagerFehler(f"{quelle.name}: während des Vergleichs geändert")
    return sha if sha256(ziel) == sha else None


def _versionen(ziel: Path, mtime_ns: int) -> Iterator[Path]:
    """name~<mtime_ns>.ext, falls auch das belegt ist name~<mtime_ns>-2.ext …"""
    yield ziel.with_name(f"{ziel.stem}~{mtime_ns}{ziel.suffix}")
    for n in itertools.count(2):
        yield ziel.with_name(f"{ziel.stem}~{mtime_ns}-{n}{ziel.suffix}")


# --- Abgleich Puffer → Lager -------------------------------------------------------------

def sammle(con: sqlite3.Connection, konfig: Konfig, *, fertig: Iterable[str] = ()) -> list[Offen]:
    """Dateien im Puffer, die (so) noch nicht im Lager bestätigt sind: keine Zeile in `lager` oder Größe/mtime
    weichen ab. Liest nur den Puffer. fertig: selbst geschriebene Dateien ohne Ruhezeit (die DB-Sicherung)."""
    bekannt = _bekannt(con)
    gesehen = iso(jetzt())
    return [Offen(konfig.wurzel / rel, rel, st.st_size, st.st_mtime_ns, roh, gesehen)
            for rel, st, roh in _kandidaten(konfig, konfig.wurzel, fertig)
            if bekannt.get(rel) != _kennung(st)]


def sichere_datenbank(con: sqlite3.Connection, konfig: Konfig) -> str:
    """Stimmige Kopie der Datenbank nach <puffer>/sicherung/pipeline-<Datum>.db (sqlite3-backup: geht auch,
    während Bot und Pipeline schreiben). Im Puffer bleiben die letzten [lager].sicherungen_behalten, ältere werden
    gelöscht – das sind keine Rohdaten, im Lager bleiben sie. Liefert den Pfad relativ zur Wurzel."""
    ordner = konfig.wurzel / SICHERUNG
    ordner.mkdir(exist_ok=True)
    raeume_teile([ordner])
    ziel = ordner / f"pipeline-{_heute(konfig)}.db"
    teil = ordner / f".{ziel.name}.{os.getpid()}.teil"
    teil.unlink(missing_ok=True)
    try:
        kopie = sqlite3.connect(teil)
        try:
            con.backup(kopie)
            kopie.execute("PRAGMA journal_mode = DELETE")  # eigenständige Datei, ohne -wal/-shm daneben
        finally:
            kopie.close()
        with open(teil, "rb+") as f:
            os.fsync(f.fileno())
        os.replace(teil, ziel)
    except BaseException:
        teil.unlink(missing_ok=True)
        raise
    behalten = max(1, int(konfig.wert("lager.sicherungen_behalten", 30)))
    for alt in sorted(p for p in ordner.iterdir() if SICHERUNG_NAME.match(p.name) and p.is_file())[:-behalten]:
        alt.unlink()
    return f"{SICHERUNG}/{ziel.name}"


def _wecken_noetig(con: sqlite3.Connection, konfig: Konfig, offen: list[Offen]) -> bool:
    """Neue Clips, Replays, Sessions … wecken pve-big. Die DB-Sicherung allein nicht jeden Tag (Ziel 2: nur an
    Tagen mit neuen Daten) – nur, wenn die letzte bestätigte älter als [lager].sicherung_wecken_tage ist (Standard 7,
    0 = nie allein). Sonst reist sie beim nächsten Wecken mit."""
    if any(not o.relativ.startswith(SICHERUNG + "/") for o in offen):
        return True
    tage = float(konfig.wert("lager.sicherung_wecken_tage", 7))
    if not offen or tage <= 0:
        return False
    letzte = con.execute("SELECT MAX(bestaetigt) FROM lager WHERE relativ LIKE ?", (SICHERUNG + "/%",)).fetchone()[0]
    return letzte is None or jetzt() - aus_iso(letzte) >= timedelta(days=tage)


def _umfang(offen: list[Offen]) -> dict:
    return {"offen": len(offen), "offen_gb": _gb(sum(o.groesse for o in offen))}


def _ins_lager(con: sqlite3.Connection, o: Offen, lager: Path) -> str:
    """Eine Datei ins Lager. Liefert kopiert | bestaetigt (war schon gleich da) | versioniert (Rohdaten-Konflikt)."""
    erwartet = (o.groesse, o.mtime_ns)
    ziel = lager / o.relativ
    art = "kopiert"
    if os.path.lexists(ziel):
        if sha := _gleicher_inhalt(o.pfad, ziel, erwartet):
            _bestaetige(con, o.relativ, erwartet, sha, o.relativ, o.gesehen)
            return "bestaetigt"
        if o.roh:  # Rohdaten nie überschreiben: daneben ablegen
            art = "versioniert"
            for ziel in _versionen(lager / o.relativ, o.mtime_ns):
                if not os.path.lexists(ziel):
                    break
                if sha := _gleicher_inhalt(o.pfad, ziel, erwartet):  # schon einmal abgelegt, nur unbestätigt
                    _bestaetige(con, o.relativ, erwartet, sha, ziel.relative_to(lager).as_posix(), o.gesehen)
                    return "bestaetigt"
        # abgeleitete Ordner (sessions/, highlights/ …): neue Fassung ersetzt die alte (per Zwischendatei)
    sha = kopiere_geprueft(o.pfad, ziel, erwartet)
    _bestaetige(con, o.relativ, erwartet, sha, ziel.relative_to(lager).as_posix(), o.gesehen)
    return art


def _melde(con: sqlite3.Connection, konfig: Konfig, e: dict, abbruch_melden: bool, verwechslung: bool) -> None:
    """Höchstens eine Meldung am Tag (Schlüssel lager:<Datum>). Der Bot hält sie in der Ruhezeit zurück.
    Nicht gemeldet: pve-big war nicht zu wecken – das ist meist vorübergehend; hält es an, meldet die Morgenprüfung."""
    if verwechslung:
        db.meldung(con, f"lager:{_heute(konfig)}", (
            f"🗄️ Lager-Abgleich abgebrochen, nichts kopiert: {e['abbruch']}\n"
            "Nichts verloren – im Puffer bleibt alles liegen. Nächster Schritt: Link /srv/clips und die Marken "
            ".clip-puffer/.clip-lager prüfen (docs/PUFFER.md, R5), dann pipeline lager abgleich --probelauf"))
        return
    teile = []
    if e.get("abbruch") and abbruch_melden:
        teile.append(f"abgebrochen – {e['abbruch']}")
    if e["fehler"]:
        teile.append(f"{e['fehler']} Datei(en) noch nicht im Lager, z. B. {e['fehler_liste'][0]}")
    if e["versioniert"]:
        teile.append(f"{e['versioniert']} Rohdatei(en) lagen im Lager schon mit anderem Inhalt "
                     f"(z. B. {e['versioniert_liste'][0]}) – nichts überschrieben, die Fassung aus dem Puffer "
                     "liegt daneben als name~<Zeit>")
    if not teile:
        return
    if e["fehler"] or (e.get("abbruch") and abbruch_melden):
        weiter = ("Nichts verloren – im Puffer bleibt alles liegen, der nächste Abgleich versucht es erneut.\n"
                  "Nachsehen: pipeline lager status")
    else:  # nur Rohdaten-Konflikte: erledigt, aber du solltest es wissen
        weiter = "Nichts verloren – beide Fassungen liegen im Lager. Nächster Schritt: bei Gelegenheit vergleichen."
    text = "🗄️ Lager-Abgleich: " + "; ".join(teile) + ".\n" + weiter
    db.meldung(con, f"lager:{_heute(konfig)}", text)


def _melde_uebertragungsende(con: sqlite3.Connection, lauf: int, e: dict, verwechslung: bool) -> None:
    """Ein Abschluss je angefangenem Lauf, auch nach Teilfehler oder Abbruch; keine rohen Fehlertexte im Chat.
    Routine (Stufe 3, 08.10. – der stille Clip-Bot vermerkt sie im einfachen Modus nur) ist er nur, wenn es nichts zu
    wissen gibt: kein Fehler, kein Abbruch, keine zusätzlich gesicherte Fassung und nichts Meldenswertes aus der
    Freigabe (_freigabe_meldenswert). Eine glatte Freigabe steht dann nur im Protokoll (ereignisse, puffer_frei)."""
    status = "abgebrochen" if e.get("abbruch") else "mit Fehlern beendet" if e["fehler"] else "abgeschlossen"
    symbol = "✅" if e["ok"] else "⚠️"
    teile = [f"{symbol} Übertragung Puffer → Lager {status}.",
             f"Neu erfolgreich übertragene Videos: {e['videos_uebertragen']}."]
    if e["videos_bestaetigt"]:
        teile.append(f"Bereits im Lager vorhandene Videos: {e['videos_bestaetigt']} (nur bestätigt).")
    if e["versioniert"]:
        teile.append(f"{e['versioniert']} Rohdatei(en) als zusätzliche Fassung gesichert – nichts überschrieben. "
                     "Nichts verloren – beide Fassungen liegen im Lager.")
    if e["fehler"]:
        teile.append(f"{e['fehler']} Datei(en) konnten nicht übertragen werden.")
    if not e["ok"]:
        teile.append("Nichts verloren – im Puffer bleibt alles liegen. "
                     + ("Puffer-/Lager-Zuordnung und .clip-puffer/.clip-lager prüfen; " if verwechslung else "")
                     + "Details: pipeline lager status")
    if e.get("freigabe_wartet"):
        teile.append("Alte Rohvideos lösche ich erst wieder vom Mini, wenn alles im Lager ist.")
    if e.get("sicherung_fehler"):
        teile.append("⚠️ Die tägliche Sicherung der Datenbank ging heute nicht (Puffer voll?) – die Datenbank selbst "
                     "ist unverändert, der nächste Abgleich versucht es wieder.")
    teile += _freigabe_zeilen(e.get("freigabe"))
    routine = (e["ok"] and not e["versioniert"] and not e.get("sicherung_fehler")
               and not _freigabe_meldenswert(e.get("freigabe")))
    db.meldung(con, f"uebertragung:lager:{lauf}:ende", "\n".join(teile), routine=routine)


# --- Puffer freigeben (Stufe B5, Florian 08.10.) -------------------------------------------------

FREI_MIN_TAGE = 1             # Schutz vor Tippfehlern: rohdaten_tage = 0 hieße „gleich nach dem Sichern löschen“
FREI_PROBE = "puffer_probe"   # ereignisse.art: der erste Lauf mit etwas zum Freigeben hat nur gezählt
FREI_ART = "puffer_frei"      # ereignisse.art: je gelöschtem Rohvideo eine Zeile
_ORDNERNAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class _Bleibt(Exception):
    """Die Datei bleibt im Puffer, nur ins Log: seit der Bestätigung geändert, kein echter Ordner/keine Datei im
    Puffer (Link, anderes Dateisystem)."""


class _KopieFehlt(Exception):
    """Die Datei bleibt im Puffer, weil ihre Kopie im Lager fehlt oder nicht mehr gleich ist – das sagt der Bot."""


@dataclass
class _Frei:
    relativ: str
    teile: tuple[str, ...]          # teile[0] = eingang
    lager_relativ: str
    lager_teile: tuple[str, ...]
    groesse: int
    mtime_ns: int
    sha256: str


def _teile(rel: object) -> tuple[str, ...] | None:
    """Teile eines relativen Pfads aus der Tabelle – None bei absolut, leer, Backslash, NUL, '.', '..' oder einem
    versteckten Teil (in versteckte Ordner steigt der Abgleich nie hinab)."""
    if not isinstance(rel, str) or not rel or rel.startswith("/") or "\\" in rel or "\x00" in rel:
        return None
    teile = tuple(rel.split("/"))
    return None if any(not t or t.startswith(".") for t in teile) else teile


def _frei_kandidaten(con: sqlite3.Connection, eingang: str, grenze: datetime, tage: float) -> list[_Frei]:
    """Nur aus der Tabelle `lager` (b: nur was dort steht, ist im Lager zurückgelesen): Videos aus eingang/, deren
    Aufnahme (mtime) UND Bestätigung im Lager älter als grenze sind (a), ältestes zuerst. Die Lager-Kopie muss im
    eingang/ des Lagers liegen – Rohdaten werden dort nie überschrieben.
    Zusätzlich (N39) muss die Aufnahme mindestens tage älter sein als die jüngste bestätigte Aufnahme in eingang/. Deren
    Dateizeit kommt vom Gaming-PC: Springt die Uhr des Mini vor, gilt die Aufnahme von gestern so nicht als alt. Das
    macht die Grenze nur strenger, nie lockerer."""
    grenze_ns = int(grenze.timestamp() * 1_000_000_000)
    zeilen = []
    for z in con.execute("SELECT relativ, groesse, mtime_ns, sha256, bestaetigt, lager_relativ, zuerst_gesehen "
                         "FROM lager ORDER BY mtime_ns, relativ"):
        teile, lager_teile = _teile(z["relativ"]), _teile(z["lager_relativ"])
        if not teile or not lager_teile or len(teile) < 2 or teile[0] != eingang or lager_teile[0] != eingang:
            continue
        zeilen.append((z, teile, lager_teile))
    if zeilen:
        neueste = max(int(z["mtime_ns"]) for z, _t, _l in zeilen)
        grenze_ns = min(grenze_ns, neueste - int(tage * 86400) * 1_000_000_000)
    liste = []
    for z, teile, lager_teile in zeilen:
        if Path(teile[-1]).suffix.lower() not in VIDEO_ENDUNGEN or _zwischendatei(teile[-1]):
            continue
        if int(z["groesse"]) < 0:  # Bestätigung zurückgenommen (Kopie im Lager fehlte): erst neu kopieren
            continue
        if not _SHA256.match(str(z["sha256"] or "")) or int(z["mtime_ns"]) >= grenze_ns:
            continue
        try:  # zuerst_gesehen ≤ bestaetigt; beide zählen, falls eine Zeile von Hand nachgetragen wurde
            gesichert = max(aus_iso(z["bestaetigt"]), aus_iso(z["zuerst_gesehen"] or z["bestaetigt"]))
        except (TypeError, ValueError):
            continue
        if gesichert < grenze:
            liste.append(_Frei(z["relativ"], teile, z["lager_relativ"], lager_teile, int(z["groesse"]),
                               int(z["mtime_ns"]), z["sha256"]))
    return liste


def _unterordner(fd: int, name: str, dev: int) -> int:
    """Unterordner name von fd öffnen: nur ein echter Ordner (lstat – ein Link ist keiner) auf dem Dateisystem dev.
    O_NOFOLLOW und der Vergleich mit dem lstat schließen aus, dass zwischen Prüfen und Öffnen etwas getauscht wurde."""
    st = os.stat(name, dir_fd=fd, follow_symlinks=False)
    if not stat.S_ISDIR(st.st_mode) or st.st_dev != dev:
        raise _Bleibt(f"{name} ist kein Ordner des Puffers (Link oder anderes Dateisystem)")
    neu = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
    if not os.path.samestat(os.fstat(neu), st):
        os.close(neu)
        raise _Bleibt(f"{name} wurde während der Prüfung getauscht")
    return neu


def _lager_kopie(lager: Path, k: _Frei) -> tuple[Path, os.stat_result]:
    """Die Kopie im Lager, nur per lstat (pve-big ist im Abgleich wach): jede Stufe ein echter Ordner (kein Link), am
    Ende eine normale Datei, gleich groß (d) – sonst _KopieFehlt. Bewusst ohne st_dev-Vergleich: Unter-Datasets von
    ZFS haben über NFS ein eigenes Gerät; dass es nicht die Puffer-Datei selbst ist, prüft _gib_eine_frei (samestat)."""
    pfad = lager
    for i, teil in enumerate(k.lager_teile):
        pfad = pfad / teil
        try:
            st = os.lstat(pfad)
        except (FileNotFoundError, NotADirectoryError):
            raise _KopieFehlt("fehlt im Lager") from None
        letzte = i == len(k.lager_teile) - 1
        if not (stat.S_ISREG(st.st_mode) if letzte else stat.S_ISDIR(st.st_mode)):
            raise _KopieFehlt("im Lager keine normale Datei")
    if st.st_size != k.groesse:
        raise _KopieFehlt(f"im Lager {st.st_size} statt {k.groesse} Bytes")
    return pfad, st


def _sha256_frisch(pfad: Path, st: os.stat_result) -> str:
    """SHA-256 der Lager-Kopie, frisch vom NFS-Server: ohne einem Link zu folgen (O_NOFOLLOW, derselbe Eintrag wie beim
    lstat), den Seiten-Cache vorher verworfen – wie beim Zurücklesen in kopiere_geprueft."""
    fd = os.open(pfad, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_BINARY", 0))
    try:
        if not os.path.samestat(os.fstat(fd), st):
            raise _KopieFehlt("im Lager während der Prüfung getauscht")
        if hasattr(os, "posix_fadvise"):
            with suppress(OSError):
                os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
        h = hashlib.sha256()
        while block := os.read(fd, BLOCK):
            h.update(block)
        return h.hexdigest()
    finally:
        os.close(fd)


def _gib_eine_frei(con: sqlite3.Connection, konfig: Konfig, k: _Frei, eingang_fd: int, dev: int, lager: Path,
                   probe: bool) -> None:
    """Eine Datei prüfen (c, d, e) und – außer in der Probe – im Puffer löschen, genau im geprüften Ordner
    (unlink mit dir_fd, kein Pfad wird neu aufgelöst). FileNotFoundError: im Puffer schon weg (früher freigegeben)."""
    with ExitStack() as offen:
        fd = eingang_fd
        for teil in k.teile[1:-1]:
            fd = _unterordner(fd, teil, dev)
            offen.callback(os.close, fd)
        name = k.teile[-1]
        st = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if not stat.S_ISREG(st.st_mode) or st.st_dev != dev:
            raise _Bleibt("im Puffer keine normale Datei")
        if _kennung(st) != (k.groesse, k.mtime_ns):  # c) seit der Bestätigung geändert: gilt nicht als gesichert
            raise _Bleibt("seit der Bestätigung geändert")
        kopie, kopie_st = _lager_kopie(lager, k)  # d)
        if os.path.samestat(st, kopie_st):
            raise KonfigFehler(f"{k.relativ}: Puffer und Lager zeigen auf dieselbe Datei")
        if probe:
            return
        # e) zusätzlich zur Spec: Nach dem Löschen ist die Lager-Kopie die einzige – also vorher ganz zurücklesen
        if _sha256_frisch(kopie, kopie_st) != k.sha256:
            raise _KopieFehlt("im Lager mit anderer Prüfsumme")
        _marken_da(konfig)  # Puffer und Lager noch eingehängt?
        jetzt_st = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if not os.path.samestat(jetzt_st, st) or _kennung(jetzt_st) != (k.groesse, k.mtime_ns):
            raise _Bleibt("während der Prüfung geändert")
        os.unlink(name, dir_fd=fd)
    db.protokoll(con, FREI_ART, f"{k.relativ}: {k.groesse} Bytes im Puffer freigegeben – Kopie im Lager "
                                f"{k.lager_relativ}, SHA-256 {k.sha256}")
    log.info("Puffer: %s freigegeben (Kopie im Lager: %s)", k.relativ, k.lager_relativ)


def gib_frei(con: sqlite3.Connection, konfig: Konfig) -> dict | None:
    """Stufe B5 ([puffer].freigeben, Florian 08.10.: „Solange alles ins Lager gesynct ist, darf es nach 14 Tagen vom
    Mini gelöscht werden.“). Läuft nur aus abgleich() – nach einem Lauf, der das Lager erreicht hat und ohne Fehler
    durchlief, pve-big ist dann wach. None = abgeschaltet ([puffer].freigeben ist nicht true).

    Gelöscht wird nur ein Rohvideo im Puffer-Eingang (eingang/, Endung aus VIDEO_ENDUNGEN), das
      a) älter als [puffer].rohdaten_tage ist – Aufnahme (mtime) UND Bestätigung im Lager,
      b) in `lager` als bestätigt steht (SHA-256 im Lager zurückgelesen),
      c) im Puffer noch dieselbe Größe und Zeit hat wie bei der Bestätigung,
      d) dessen Lager-Kopie in diesem Lauf da und gleich groß ist und
      e) dessen Lager-Kopie jetzt beim Zurücklesen dieselbe SHA-256 hat (zusätzlich zur Spec, siehe oben).
    Jede Stufe des Pfads ist ein echter Ordner auf dem Dateisystem des Puffers (kein Link, kein fremdes Dateisystem;
    einen Bind-Mount desselben Dateisystems erkennt das nicht – den gibt es im Aufbau nicht). Nie etwas im Lager, nie
    Ordner, nie Clips, Momente, Sitzungen, Exporte, Highlights oder die Datenbank.
    Fehlt die Lager-Kopie oder ist sie anders, bleibt das Video, und seine Bestätigung wird zurückgenommen (groesse -1):
    Der nächste Abgleich legt es neu ins Lager (N40, ändert N13).
    Der erste Lauf mit etwas zum Freigeben ist nur eine Probe: zählt, löscht nichts (ereignisse 'puffer_probe').
    Rückgabe für e["freigabe"]: {"probe", "dateien", "gb" (gelöscht bzw. in der Probe: würde), "bleibt" (Lager-Kopie
    fehlt/anders), "bleibt_liste", "fehler", ("abbruch")}. Wirft nie (außer KeyboardInterrupt o. Ä.): Eine Panne
    hier macht den Abgleich nicht schlechter, es bleibt dann eben alles liegen."""
    if konfig.wert("puffer.freigeben", False) is not True:
        return None
    e: dict = {"probe": False, "dateien": 0, "gb": 0.0, "bleibt": 0, "bleibt_liste": [], "fehler": 0}
    menge = 0
    try:
        if os.name != "posix" or os.unlink not in os.supports_dir_fd:
            raise KonfigFehler("Freigabe im Puffer nur unter Linux")
        eingang = str(konfig.wert("speicher.eingang", "eingang"))
        if not _ORDNERNAME.match(eingang) or eingang not in _roh(konfig):
            raise KonfigFehler(f"[speicher].eingang {eingang!r} ist kein Rohdaten-Ordner aus [lager].roh")
        tage = float(konfig.wert("puffer.rohdaten_tage", 14))
        if not tage >= FREI_MIN_TAGE:
            raise KonfigFehler(f"[puffer].rohdaten_tage = {tage:g}: unter {FREI_MIN_TAGE} Tag gebe ich nichts frei")
        kandidaten = _frei_kandidaten(con, eingang, jetzt() - timedelta(days=tage), tage)
        if not kandidaten:
            return e
        konfig.pruefe_getrennt()  # Marken und verschiedene Dateisysteme – nur stat()
        lager = konfig.lager_wurzel
        e["probe"] = probe = con.execute("SELECT 1 FROM ereignisse WHERE art = ? LIMIT 1",
                                         (FREI_PROBE,)).fetchone() is None
        with ExitStack() as offen:
            wurzel_fd = os.open(os.path.realpath(konfig.wurzel), os.O_RDONLY | os.O_DIRECTORY)  # /srv/clips → /srv/puffer
            offen.callback(os.close, wurzel_fd)
            if not os.path.samestat(os.fstat(wurzel_fd), os.stat(konfig.wurzel)):
                raise SpeicherOffline("Puffer während der Prüfung umgehängt")
            dev = os.fstat(wurzel_fd).st_dev
            eingang_fd = _unterordner(wurzel_fd, eingang, dev)
            offen.callback(os.close, eingang_fd)
            for k in kandidaten:
                try:
                    _gib_eine_frei(con, konfig, k, eingang_fd, dev, lager, probe)
                except FileNotFoundError:
                    continue  # im Puffer schon weg (früher freigegeben) – der Normalfall für alte Zeilen
                except _Bleibt as f:
                    log.info("Puffer: %s bleibt – %s", k.relativ, f)
                    continue
                except _KopieFehlt as f:
                    log.warning("Puffer: %s bleibt – Kopie %s; der nächste Abgleich legt sie neu ins Lager",
                                k.relativ, f)
                    # N40: Bestätigung zurücknehmen, nichts löschen – sammle sieht die Datei dann wieder als offen.
                    # Fehlt die Kopie, entsteht sie neu; ist sie anders, kommt sie als name~<Zeit> daneben (_ins_lager).
                    con.execute("UPDATE lager SET groesse = -1 WHERE relativ = ? AND groesse = ? AND mtime_ns = ?",
                                (k.relativ, k.groesse, k.mtime_ns))
                    e["bleibt"] += 1
                    e["bleibt_liste"].append(f"{k.relativ}: {f}")
                    continue
                except OSError as f:
                    if f.errno in ABBRUCH:  # Lager oder Puffer gestört: nicht weiter versuchen
                        raise
                    log.warning("Puffer: %s bleibt – %s", k.relativ, f)
                    e["fehler"] += 1
                    continue
                e["dateien"] += 1
                menge += k.groesse
        if probe and e["dateien"]:
            db.protokoll(con, FREI_PROBE, f"Probe: {e['dateien']} Rohvideo(s), {_gb(menge)} GB würden freigegeben "
                                          "– nichts gelöscht")
    except Exception as f:  # Konfig, Lager weg, Programmfehler: nichts weiter löschen, der Abgleich bleibt gut
        log.error("Puffer-Freigabe abgebrochen: %s: %s", type(f).__name__, f)
        e["abbruch"] = f"{type(f).__name__}: {f}"
    e["gb"] = _gb(menge)
    e["bleibt_liste"] = e["bleibt_liste"][:LISTE_MAX]
    if e["dateien"]:
        log.info("Puffer: %d alte Rohvideo(s), %.2f GB %s", e["dateien"], e["gb"],
                 "würden freigegeben (Probe, nichts gelöscht)" if e["probe"] else "freigegeben")
    return e


def _zahl_gb(gb: float) -> str:
    return f"{gb:.1f}".replace(".", ",")


def _freigabe_meldenswert(f: dict | None) -> bool:
    """Das sollst du auch bei stillem Clip-Bot sehen (08.10.): die einmalige Probe vor dem ersten Löschen (N11), liegen
    gebliebene Videos (Kopie im Lager fehlt oder ist anders) und Fehler beim Freigeben."""
    if not f:
        return False
    return bool((f.get("probe") and f.get("dateien")) or f.get("bleibt") or f.get("fehler") or f.get("abbruch"))


def _freigabe_zeilen(f: dict | None) -> list[str]:
    """Zeilen für die Abschlussmeldung des Abgleichs (Stufe B5) – nichts, wenn es nichts zu sagen gibt."""
    if not f:
        return []
    zeilen = []
    n, gb = int(f.get("dateien") or 0), _zahl_gb(float(f.get("gb") or 0))
    videos = "1 altes Rohvideo" if n == 1 else f"{n} alte Rohvideos"
    if n and f.get("probe"):
        if not f.get("abbruch"):  # abgebrochene Probe: Sie zählt beim nächsten Abgleich neu, nur die ⚠️-Zeile unten
            zeilen.append(f"🧹 Puffer – Probe: {videos} ({gb} GB) könnte ich vom Mini löschen, "
                          f"{'die Kopie liegt' if n == 1 else 'die Kopien liegen'} sicher im Lager. Gelöscht habe ich "
                          f"noch nichts – ab dem nächsten Abgleich lösche ich {'es' if n == 1 else 'sie'}.")
    elif n:
        zeilen.append(f"🧹 Puffer: {videos} vom Mini gelöscht ({gb} GB), "
                      f"{'die Kopie liegt' if n == 1 else 'die Kopien liegen'} sicher im Lager.")
    if b := int(f.get("bleibt") or 0):
        beispiel = str((f.get("bleibt_liste") or [""])[0]).split(":", 1)[0]
        zeilen.append(f"⚠️ Puffer: {'1 altes Rohvideo bleibt' if b == 1 else f'{b} alte Rohvideos bleiben'} auf dem "
                      f"Mini – die Kopie im Lager fehlt oder ist nicht mehr gleich"
                      + (f" (z. B. {beispiel})" if beispiel else "") + ". Nichts verloren: Ich lösche "
                      + ("es" if b == 1 else "sie") + " nicht und lege " + ("es" if b == 1 else "sie")
                      + " beim nächsten Abgleich neu ins Lager.")
    if f.get("fehler") or f.get("abbruch"):
        zeilen.append("⚠️ Puffer: Nicht alles ließ sich vom Mini löschen – es bleibt liegen, beim nächsten Abgleich "
                      "versuche ich es wieder.")
    return zeilen


def abgleich(con: sqlite3.Connection, konfig: Konfig, probelauf: bool = False) -> dict:
    """Täglicher Abgleich (Timer 10:00): DB sichern, Offenes sammeln, nur dann pve-big wecken, jede Datei
    kopieren und zurücklesen. Fehler je Datei werden gezählt, der Rest läuft weiter; ist das Lager selbst weg
    (Markierung fehlt, EIO …), endet die Schleife mit "abbruch". Lief alles ohne Fehler durch, gibt gib_frei alte,
    im Lager bestätigte Rohvideos im Puffer frei (Stufe B5, "freigabe" im Ergebnis; den Exit-Code ändert sie nicht).

    Nachtruhe (auch beim Nachholen per Persistent=true nach einem Neustart): Schläft pve-big, wird er nicht geweckt –
    Ergebnis "nachtruhe": true, keine Meldung, das Offene wartet auf den nächsten Abgleich. Läuft er, wird abgeglichen.

    Ausnahmen: KonfigFehler (Puffer/Lager verwechselbar – nichts kopiert), SpeicherOffline (Lager nicht erreichbar),
    big.WeckenVerboten/BigFehler (pve-big nicht geweckt), Gesperrt (anderer Abgleich läuft)."""
    lager = konfig.lager_wurzel  # ohne getrennten Betrieb: KonfigFehler
    with sperre(big.lager_sperre(konfig)):
        ruhe = nachtruhe(konfig)  # vor dem Lauf-Eintrag: ein Tippfehler ist ein Konfig-Fehler, keine Verwechslung
        if probelauf:
            konfig.pruefe_getrennt(mit_lager=False)
            offen = sammle(con, konfig)
            return {"probelauf": True, **_umfang(offen), "wuerde_wecken": _wecken_noetig(con, konfig, offen),
                    "nachtruhe": ruhe, "dateien": [o.relativ for o in offen[:50]]}
        lauf = con.execute("INSERT INTO lager_laeufe (art, start) VALUES ('abgleich', ?)", (iso(jetzt()),)).lastrowid
        e: dict = {"offen": 0, "offen_gb": 0.0, "kopiert": 0, "bestaetigt": 0, "versioniert": 0, "fehler": 0,
                   "videos_uebertragen": 0, "videos_bestaetigt": 0,
                   "lager_gebraucht": False, "fehler_liste": [], "versioniert_liste": []}
        abbruch_melden = verwechslung = gestartet = False
        try:
            konfig.pruefe_getrennt(mit_lager=False)  # Puffer-Seite sofort; das Lager erst nach dem Wecken
            try:
                e["sicherung"] = sichere_datenbank(con, konfig)
            except (OSError, sqlite3.Error) as f:
                # Puffer voll (N38): Ohne diesen Ausweg bräche jeder Abgleich hier ab – nichts käme ins Lager, und die
                # Freigabe könnte den Puffer nie mehr leeren. Die Datenbank selbst liegt nicht im Puffer.
                log.warning("DB-Sicherung im Puffer nicht möglich (%s: %s) – Abgleich läuft ohne sie weiter",
                            type(f).__name__, f)
                e["sicherung_fehler"] = f"{type(f).__name__}: {str(f)[:150]}"
            offen = sammle(con, konfig, fertig=[e["sicherung"]] if e.get("sicherung") else [])
            e.update(_umfang(offen))
            if not _wecken_noetig(con, konfig, offen):
                log.info("Lager-Abgleich: nichts Neues (%d offen) – pve-big bleibt aus", len(offen))
                return e
            if ruhe and big.host(konfig) and not big.wach(konfig):
                e["nachtruhe"] = True
                log.info("Lager-Abgleich: Nachtruhe (%s–%s) – pve-big schläft und wird nicht geweckt, %d Datei(en) "
                         "warten auf den nächsten Abgleich", konfig.wert("lager.nachtruhe_von", "22:00"),
                         konfig.wert("lager.nachtruhe_bis", "08:00"), e["offen"])
                return e
            log.info("Lager-Abgleich: %d Datei(en), %.2f GB%s", e["offen"], e["offen_gb"],
                     " (Nachtruhe, pve-big läuft schon)" if ruhe else "")
            db.meldung(con, f"uebertragung:lager:{lauf}:start",
                       f"🗄️ Übertragung Puffer → Lager gestartet.\n"
                       f"{e['offen']} Dateien werden geprüft und bei Bedarf übertragen.", routine=True)
            gestartet = True
            with _wach(konfig, "lager", f"Lager-Abgleich ({e['offen']} Dateien)", wecken=not ruhe):
                e["lager_gebraucht"] = True
                konfig.pruefe_lager(float(konfig.wert("lager.warten_s", 240)))
                konfig.pruefe_getrennt()
                _miss_platz(lager, e)  # schon hier: bricht der Lauf ab (z. B. ENOSPC), gibt es trotzdem einen Wert
                raeume_teile({(lager / o.relativ).parent for o in offen})
                for o in offen:
                    try:
                        _marken_da(konfig)
                        art = _ins_lager(con, o, lager)
                    except SpeicherOffline as f:
                        e["abbruch"] = str(f)
                        break
                    except (OSError, LagerFehler) as f:
                        if isinstance(f, OSError) and f.errno in ABBRUCH:
                            e["abbruch"] = f"Lager gestört bei {o.relativ}: {f}"
                            break
                        log.warning("%s: %s", o.relativ, f)
                        e["fehler"] += 1
                        e["fehler_liste"].append(f"{o.relativ}: {str(f)[:150]}")
                        continue
                    e[art] += 1
                    if Path(o.relativ).suffix.lower() in VIDEO_ENDUNGEN:
                        e["videos_bestaetigt" if art == "bestaetigt" else "videos_uebertragen"] += 1
                    if art == "versioniert":
                        log.warning("%s: im Lager liegt eine andere Fassung – daneben abgelegt", o.relativ)
                        e["versioniert_liste"].append(o.relativ)
                if not e.get("abbruch"):
                    _miss_platz(lager, e)  # nach dem Kopieren: mit diesem Stand rechnen die nächsten Tage
                    # Stufe B5 („solange alles ins Lager gesynct ist“): nur nach einem Lauf ohne Fehler, pve-big wach
                    if not e["fehler"]:
                        if (frei := gib_frei(con, konfig)) is not None:
                            e["freigabe"] = frei
                    elif konfig.wert("puffer.freigeben", False) is True:
                        e["freigabe_wartet"] = True  # die Abschlussmeldung sagt, dass vorerst nichts gelöscht wird
            abbruch_melden = bool(e.get("abbruch"))
            if e.get("abbruch"):
                log.error("Lager-Abgleich abgebrochen: %s", e["abbruch"])
        except KonfigFehler as f:
            e["abbruch"] = str(f)
            verwechslung = True  # nie vorübergehend: gleich Bescheid geben
            raise
        except BaseException as f:
            e["abbruch"] = f"{type(f).__name__}: {f}"
            raise
        finally:
            e["ok"] = not e["fehler"] and not e.get("abbruch")
            e["fehler_liste"] = e["fehler_liste"][:LISTE_MAX]
            e["versioniert_liste"] = e["versioniert_liste"][:LISTE_MAX]
            con.execute("UPDATE lager_laeufe SET ende = ?, ergebnis = ? WHERE id = ?",
                        (iso(jetzt()), json.dumps(e, ensure_ascii=False), lauf))
            if gestartet:
                _melde_uebertragungsende(con, lauf, e, verwechslung)
            else:  # z. B. vertauschte Marken: wichtige Warnung schon vor einem Übertragungsstart erhalten
                _melde(con, konfig, e, abbruch_melden, verwechslung)
            log.info("Lager-Abgleich: %d kopiert, %d bestätigt, %d versioniert, %d Fehler",
                     e["kopiert"], e["bestaetigt"], e["versioniert"], e["fehler"])
    return e


# --- Übernahme Lager → Puffer (einmalig vor dem Umschalten) ----------------------------------

def _mit_pfaden(konfig: Konfig, puffer: Path, lager: Path) -> Konfig:
    """Kopie der Konfig mit ausdrücklichen Wurzeln – so gelten für die Übernahme dieselben Prüfungen
    (Marken, st_dev, samefile, Erreichbarkeit) wie im getrennten Betrieb, auch wenn [lager].wurzel noch leer ist."""
    daten = deepcopy(konfig.daten)
    daten["speicher"]["wurzel"] = str(puffer)
    daten.setdefault("lager", {})["wurzel"] = str(lager)
    return Konfig(daten, konfig.quelle)


def _plane_uebernahme(con: sqlite3.Connection, k: Konfig, eingang_tage: int) -> tuple[list, dict]:
    """Was ist zu tun? Nur stat(): kopieren (fehlt im Puffer oder dort nur eine bestätigte ältere Kopie),
    pruefen (im Puffer liegt eine unbestätigte Fassung – nie blind überschreiben), gleich (Delta: übersprungen),
    zu_jung (im Lager eben erst geschrieben – z. B. ein Pipeline-Schritt oder der PC kurz vor R5: nicht halb
    übernehmen, aber auch nicht still weglassen, sonst fehlt die Datei nach dem Umschalten unbemerkt im Puffer)."""
    von, nach = k.lager_wurzel, k.wurzel
    eingang = str(k.wert("speicher.eingang", "eingang"))
    grenze = time.time() - eingang_tage * 86400
    ruhe = _ruhe_grenze(k)
    bekannt = _bekannt(con)
    zahlen: dict = {"gleich": 0, "eingang_alt": 0, "kopieren": 0, "pruefen": 0, "zu_jung": 0, "gb": 0.0,
                    "zu_jung_liste": []}
    posten = []
    for rel, st, _roh in _kandidaten(k, von, ruhe=False):
        if rel.split("/", 1)[0] == eingang and st.st_mtime < grenze:
            zahlen["eingang_alt"] += 1
            continue
        try:
            zst = os.lstat(nach / rel)
        except FileNotFoundError:
            zst = None
        bestaetigt = zst is not None and stat.S_ISREG(zst.st_mode) and bekannt.get(rel) == _kennung(zst)
        if bestaetigt and _kennung(zst) == _kennung(st):
            zahlen["gleich"] += 1
            continue
        if _jung(st, ruhe):
            zahlen["zu_jung"] += 1
            zahlen["zu_jung_liste"].append(rel)
            continue
        art = "pruefen" if zst is not None and not bestaetigt else "kopieren"
        zahlen[art] += 1
        zahlen["gb"] += st.st_size
        posten.append((rel, st, art))
    zahlen["gb"] = _gb(zahlen["gb"])
    zahlen["zu_jung_liste"] = zahlen["zu_jung_liste"][:LISTE_MAX]
    return posten, zahlen


def _hinweis_zu_jung(k: Konfig, anzahl: int) -> str:
    return (f"{anzahl} Datei(en) im Lager jünger als {k.wert('lager.ruhe_min', 10)} min (werden evtl. noch "
            "geschrieben) – noch nicht im Puffer. In ein paar Minuten wiederholen; Gleiches wird übersprungen.")


def uebernahme(con: sqlite3.Connection, konfig: Konfig, von: Path, nach: Path, eingang_tage: int = 14,
               probelauf: bool = False) -> dict:
    """Einmalig vor dem Umschalten (docs/PUFFER.md R4/R5): Lager → Puffer mit AUSDRÜCKLICHEN Pfaden, unabhängig von
    [lager].wurzel. Alle [lager].ordner außer eingang/ ganz, von eingang/ nur Dateien der letzten eingang_tage.
    Jede Kopie gilt als bestätigt (die Quelle IST das Lager). Wiederholbar: Gleiches (Größe + mtime) wird übersprungen.
    Das Lager wird nur gelesen. Im Puffer wird eine unbestätigte, andere Fassung nie überschrieben (Konflikt).
    Im Lager eben erst Geschriebenes (jünger als [lager].ruhe_min) bleibt aus, zählt aber als zu_jung → ok False."""
    k = _mit_pfaden(konfig, puffer=Path(nach), lager=Path(von))
    with sperre(big.lager_sperre(konfig)):
        k.pruefe_getrennt(mit_lager=False)
        basis = {"von": str(von), "nach": str(nach), "eingang_tage": eingang_tage}
        if probelauf:
            try:
                k.pruefe_lager()
            except SpeicherOffline as f:
                return {**basis, "probelauf": True, "hinweis": f"{f} – der Probelauf weckt nicht"}
            k.pruefe_getrennt()
            zahlen = _plane_uebernahme(con, k, eingang_tage)[1]
            if zahlen["zu_jung"]:
                zahlen["hinweis"] = _hinweis_zu_jung(k, zahlen["zu_jung"])
            return {**basis, "probelauf": True, **zahlen}
        lauf = con.execute("INSERT INTO lager_laeufe (art, start) VALUES ('uebernahme', ?)", (iso(jetzt()),)).lastrowid
        e: dict = {**basis, "kopiert": 0, "eingetragen": 0, "konflikte": 0, "fehler": 0, "zu_jung": 0,
                   "konflikt_liste": [], "fehler_liste": [], "zu_jung_liste": []}
        try:
            with _wach(k, "uebernahme", "Übernahme Lager → Puffer"):
                k.pruefe_lager(float(k.wert("lager.warten_s", 240)))
                k.pruefe_getrennt()
                posten, zahlen = _plane_uebernahme(con, k, eingang_tage)
                e.update({s: zahlen[s] for s in ("gleich", "eingang_alt", "gb", "zu_jung", "zu_jung_liste")})
                log.info("Übernahme: %d Datei(en), %.2f GB (%d schon gleich)", len(posten), zahlen["gb"], zahlen["gleich"])
                if e["zu_jung"]:
                    e["hinweis"] = _hinweis_zu_jung(k, e["zu_jung"])
                    log.warning("%s Z. B. %s", e["hinweis"], e["zu_jung_liste"][0])
                raeume_teile({(k.wurzel / rel).parent for rel, _st, _art in posten})
                for rel, st, art in posten:
                    quelle, ziel = k.lager_wurzel / rel, k.wurzel / rel
                    try:
                        _marken_da(k)
                        if art == "pruefen":
                            sha = _gleicher_inhalt(quelle, ziel, _kennung(st))
                            if not sha:  # im Puffer liegt schon etwas anderes, nicht Bestätigtes – bleibt unangetastet
                                e["konflikte"] += 1
                                e["konflikt_liste"].append(rel)
                                log.warning("%s: im Puffer liegt eine andere, unbestätigte Fassung – nicht überschrieben", rel)
                                continue
                            e["eingetragen"] += 1
                        else:
                            sha = kopiere_geprueft(quelle, ziel, _kennung(st))
                            e["kopiert"] += 1
                        jetzt_iso = iso(jetzt())
                        _bestaetige(con, rel, _kennung(os.stat(ziel)), sha, rel, jetzt_iso)
                    except SpeicherOffline as f:
                        e["abbruch"] = str(f)
                        break
                    except (OSError, LagerFehler) as f:
                        if isinstance(f, OSError) and f.errno in ABBRUCH:
                            e["abbruch"] = f"gestört bei {rel}: {f}"
                            break
                        log.warning("%s: %s", rel, f)
                        e["fehler"] += 1
                        e["fehler_liste"].append(f"{rel}: {str(f)[:150]}")
        except BaseException as f:
            e.setdefault("abbruch", f"{type(f).__name__}: {f}")
            raise
        finally:
            e["ok"] = not (e["fehler"] or e["konflikte"] or e["zu_jung"] or e.get("abbruch"))
            e["konflikt_liste"] = e["konflikt_liste"][:LISTE_MAX]
            e["fehler_liste"] = e["fehler_liste"][:LISTE_MAX]
            con.execute("UPDATE lager_laeufe SET ende = ?, ergebnis = ? WHERE id = ?",
                        (iso(jetzt()), json.dumps(e, ensure_ascii=False), lauf))
            log.info("Übernahme: %d kopiert, %d eingetragen, %d Konflikte, %d Fehler, %d zu jung",
                     e["kopiert"], e["eingetragen"], e["konflikte"], e["fehler"], e["zu_jung"])
    return e


# --- Status ------------------------------------------------------------------------------

def _letzte_laeufe(con: sqlite3.Connection) -> tuple[dict | None, str | None]:
    """Letzter Abgleich und Ende des letzten erfolgreichen (höchstens 50 Läufe zurück). Ein Lauf, der in der
    Nachtruhe nicht wecken durfte, ist kein Erfolg: das Offene liegt dann noch im Puffer."""
    letzter = None
    for z in con.execute("SELECT start, ende, ergebnis FROM lager_laeufe WHERE art = 'abgleich' ORDER BY id DESC LIMIT 50"):
        e = json.loads(z["ergebnis"]) if z["ergebnis"] else {}
        lauf = {"start": z["start"], "ende": z["ende"], "ok": bool(z["ende"] and e.get("ok") and not e.get("nachtruhe"))}
        lauf.update({s: e[s] for s in ("offen", "kopiert", "fehler", "versioniert", "abbruch", "nachtruhe") if s in e})
        letzter = letzter or lauf
        if lauf["ok"]:
            return letzter, z["ende"]
    return letzter, None


def letzte_messung(con: sqlite3.Connection) -> dict | None:
    """Zuletzt beim Abgleich gemessener Platz im Lager: {"frei_gb", "gesamt_gb", "zeit"} oder None.
    Nur aus der Tabelle – fasst das Lager nie an (pve-big schläft meist)."""
    for z in con.execute("SELECT start, ende, ergebnis FROM lager_laeufe WHERE art = 'abgleich' AND ergebnis LIKE ? "
                         "ORDER BY id DESC LIMIT 5", ('%"lager_frei_gb"%',)):
        e = json.loads(z["ergebnis"])
        if isinstance(e.get("lager_frei_gb"), (int, float)):
            return {"frei_gb": e["lager_frei_gb"], "gesamt_gb": e.get("lager_gesamt_gb"),
                    "zeit": z["ende"] or z["start"]}
    return None


def _uhr(konfig: Konfig, zeitpunkt: str) -> str:
    zone = konfig.wert("zeit.zeitzone", "Europe/Berlin")
    lokal = utc_zu_lokal(aus_iso(zeitpunkt), zone)
    return f"{lokal:%H:%M}" if lokal.date() == utc_zu_lokal(jetzt(), zone).date() else f"{lokal:%d.%m. %H:%M}"


def _zeile(konfig: Konfig, stand: dict) -> str:
    """Eine Zeile für /status im Bot, z. B. „Puffer 61 GB frei · Lager: 1840 GB frei, letzter Abgleich 10:07 ok ·
    0 offen“. Den Platz im Lager gibt es erst nach dem ersten Abgleich, der pve-big gebraucht hat; stammt er nicht von
    heute, steht „(Stand …)“ dabei."""
    teile = []
    if stand.get("puffer_frei_gb") is not None:
        teile.append(f"Puffer {stand['puffer_frei_gb']:.0f} GB frei")
    lauf = stand.get("letzter_lauf")
    if not lauf:
        text = "noch kein Abgleich"
    elif not lauf["ende"]:
        text = f"Abgleich läuft seit {_uhr(konfig, lauf['start'])}"
    elif lauf.get("nachtruhe"):
        text = f"letzter Abgleich {_uhr(konfig, lauf['ende'])} in der Nachtruhe übersprungen"
    elif lauf["ok"]:
        text = f"letzter Abgleich {_uhr(konfig, lauf['ende'])} ok"
    elif lauf.get("abbruch"):
        text = f"letzter Abgleich {_uhr(konfig, lauf['ende'])} abgebrochen"
    else:
        text = f"letzter Abgleich {_uhr(konfig, lauf['ende'])} mit {lauf.get('fehler', 0)} Fehler(n)"
    if stand.get("lager_frei_gb") is not None:
        platz = f"{stand['lager_frei_gb']:.0f} GB frei"
        gemessen = utc_zu_lokal(aus_iso(stand["lager_gemessen"]), konfig.wert("zeit.zeitzone", "Europe/Berlin"))
        if gemessen.date().isoformat() != _heute(konfig):
            platz += f" (Stand {_uhr(konfig, stand['lager_gemessen'])})"
        text = f"{platz}, {text}"
    teile.append(f"Lager: {text}")
    if stand["pruefung"] != "ok":
        teile.append(f"⚠️ {stand['pruefung']}")
    elif stand.get("offen") is not None:
        teile.append(f"{stand['offen']} offen")
    return " · ".join(teile)


def status(con: sqlite3.Connection, konfig: Konfig) -> dict:
    """Stand für `pipeline lager status` und /status im Bot. Weckt nie und fasst das Lager nicht an – nur die
    Tabellen und der Puffer (lokal); der Platz im Lager ist der beim letzten Abgleich gemessene (lager_gemessen).
    "offen" zählt ohne die DB-Sicherungen: die warten absichtlich aufs nächste Wecken (sicherung_offen), sonst stünde
    nach jedem guten Lauf „1 offen“ da."""
    if not konfig.getrennt:
        return {"getrennt": False, "pruefung": "kein getrennter Betrieb: [lager].wurzel leer"}
    stand: dict = {"getrennt": True, "pruefung": "ok", "offen": None, "offen_gb": None, "aelteste": None,
                   "sicherung_offen": None, "puffer_frei_gb": None}
    try:
        konfig.pruefe_getrennt(mit_lager=False)
    except KonfigFehler as e:
        stand["pruefung"] = str(e)
    else:
        alle = sammle(con, konfig)
        offen = [o for o in alle if not o.relativ.startswith(SICHERUNG + "/")]
        stand.update(_umfang(offen), sicherung_offen=len(alle) - len(offen))
        if offen:
            stand["aelteste"] = iso(datetime.fromtimestamp(min(o.mtime_ns for o in offen) / 1e9, UTC))
        stand["puffer_frei_gb"] = round(shutil.disk_usage(konfig.wurzel).free / 1e9, 1)
    stand["letzter_lauf"], stand["letzter_erfolg"] = _letzte_laeufe(con)
    messung = letzte_messung(con) or {}
    stand.update(lager_frei_gb=messung.get("frei_gb"), lager_gesamt_gb=messung.get("gesamt_gb"),
                 lager_gemessen=messung.get("zeit"))
    stand["zeile"] = _zeile(konfig, stand)
    return stand
