"""Material vom großen Host auf den Mini kopieren (`pipeline material`) – mit einmaligem Wecken.

Harte Regel: Rohdaten werden nie gelöscht oder verschoben, nur gelesen und kopiert (mit SHA-256).

Reihenfolge (Wichtiges zuerst, falls der Platz knapp wird):
  1. Fortnite-Replays (klein, Kill-Wahrheit)
  2. Session-Ordner der Pipeline (analyse.json, schnittliste.json, geschnittene Clips)
  3. Rohvideos aus eingang/: ganz, wenn der Platz für alle reicht – sonst je Video nur die letzten 90 s,
     verlustfrei mit "-c copy" (Instant-Replays enden mit dem Moment)
Die Kopie spiegelt die Ordner der Speicher-Wurzel: material/<relativer Pfad>. So passen die Pfade aus der
Datenbank weiter (siehe lokal()).
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from . import bestand, big
from .konfig import Konfig, SpeicherOffline
from .medien import MedienFehler, fuehre_aus, probe
from .zeit import iso, jetzt

log = logging.getLogger("pipeline")
BLOCK = 4 * 1024 * 1024
SESSION_ENDUNGEN = {".json", ".mp4"}


def ordner(konfig: Konfig) -> Path:
    return Path(str(konfig.wert("material.ordner", "/var/lib/clip-pipeline/material")))


def lokal(konfig: Konfig, relativ: str) -> Path:
    """Lokale Kopie, falls vorhanden – sonst der Pfad auf dem großen Host."""
    kopie = ordner(konfig) / relativ
    return kopie if kopie.is_file() else konfig.absolut(relativ)


def sha256(pfad: Path) -> str:
    h = hashlib.sha256()
    with pfad.open("rb") as f:
        while block := f.read(BLOCK):
            h.update(block)
    return h.hexdigest()


def kopiere(quelle: Path, ziel: Path) -> tuple[str, str]:
    """Kopiert Byte für Byte, rechnet dabei die Prüfsumme der Quelle und prüft die Kopie nach.
    Erst nach bestandener Prüfung bekommt die Kopie ihren Namen. Die Quelle wird nur gelesen."""
    ziel.parent.mkdir(parents=True, exist_ok=True)
    tmp = ziel.with_name(ziel.name + ".teil")
    h = hashlib.sha256()
    with quelle.open("rb") as ein, tmp.open("wb") as aus:
        while block := ein.read(BLOCK):
            h.update(block)
            aus.write(block)
    quell_hash, ziel_hash = h.hexdigest(), sha256(tmp)
    if quell_hash != ziel_hash:
        tmp.unlink(missing_ok=True)
        raise MedienFehler(f"Prüfsumme weicht ab: {quelle}")
    shutil.copystat(quelle, tmp)
    tmp.replace(ziel)
    return quell_hash, ziel_hash


def kuerze(quelle: Path, ziel: Path, sekunden: float) -> tuple[str, str, float]:
    """Letzte `sekunden` verlustfrei (-c copy). Schnitt am Keyframe davor -> Beginn kann etwas früher liegen."""
    info = probe(quelle)
    von = max(0.0, info.dauer_s - sekunden)
    ziel.parent.mkdir(parents=True, exist_ok=True)
    tmp = ziel.with_name(ziel.stem + ".teil" + ziel.suffix)
    fuehre_aus(["ffmpeg", "-hide_banner", "-nostdin", "-y", "-ss", f"{von:.3f}", "-i", str(quelle),
                "-map", "0", "-c", "copy", "-avoid_negative_ts", "make_zero", str(tmp)], f"Kürzen {quelle.name}")
    ziel_hash = sha256(tmp)
    tmp.replace(ziel)
    return sha256(quelle), ziel_hash, von


@dataclass
class Posten:
    quelle: Path
    relativ: str
    art: str  # replay | session | video
    groesse: int


def sammle(konfig: Konfig, con: sqlite3.Connection) -> list[Posten]:
    """Alles, was noch nicht (in dieser Größe) kopiert ist – in der Reihenfolge der Wichtigkeit."""
    erledigt = {z["quelle"]: z["groesse"] for z in con.execute("SELECT quelle, groesse FROM material")}
    posten: list[Posten] = []

    def dazu(pfad: Path, art: str) -> None:
        rel = konfig.relativ(pfad)
        groesse = pfad.stat().st_size
        if erledigt.get(rel) != groesse and not pfad.name.endswith((".teil", ".tmp")) and ".tmp." not in pfad.name:
            posten.append(Posten(pfad, rel, art, groesse))

    for p in sorted(konfig.ordner("replays").rglob("*.replay")):
        dazu(p, "replay")
    sessions = konfig.ordner("sessions")
    for p in sorted(sessions.rglob("*")) if sessions.is_dir() else []:
        if p.is_file() and p.suffix.lower() in SESSION_ENDUNGEN:
            dazu(p, "session")
    videos = [p for p in konfig.ordner("eingang").rglob("*") if p.is_file() and p.suffix.lower() in bestand.VIDEO_ENDUNGEN]
    for p in sorted(videos, key=lambda p: p.stat().st_mtime, reverse=True):  # neueste zuerst
        dazu(p, "video")
    return posten


def plane(konfig: Konfig, posten: list[Posten]) -> dict:
    """Passt alles? Sonst Videos nur als letzte 90 s (Schätzung über die Bitrate)."""
    ziel = ordner(konfig)
    ziel.mkdir(parents=True, exist_ok=True)
    frei = shutil.disk_usage(ziel).free - float(konfig.wert("material.reserve_gb", 2)) * 1e9
    noetig = sum(p.groesse for p in posten)
    return {"frei_gb": round(frei / 1e9, 2), "noetig_gb": round(noetig / 1e9, 2), "videos_kuerzen": noetig > frei}


def _merke(con, p: Posten, ziel: Path, art: str, qh: str, zh: str, von: float | None) -> None:
    con.execute(
        """INSERT INTO material (quelle, ziel, art, groesse, sha256_quelle, sha256_ziel, von_s, kopiert)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (quelle) DO UPDATE SET ziel = excluded.ziel, art = excluded.art, groesse = excluded.groesse,
               sha256_quelle = excluded.sha256_quelle, sha256_ziel = excluded.sha256_ziel, von_s = excluded.von_s,
               kopiert = excluded.kopiert""",
        (p.relativ, str(ziel), art, p.groesse, qh, zh, von, iso(jetzt())),
    )


def uebertrage(konfig: Konfig, con: sqlite3.Connection, *, probelauf: bool = False) -> dict:
    posten = sammle(konfig, con)
    plan = plane(konfig, posten)
    ergebnis: dict = {**plan, "posten": len(posten), "kopiert": 0, "gekuerzt": 0, "ohne_platz": 0, "fehler": [],
                      "arten": {a: sum(1 for p in posten if p.art == a) for a in ("replay", "session", "video")}}
    if probelauf:
        return ergebnis
    sekunden = float(konfig.wert("material.ende_s", 90))
    reserve = float(konfig.wert("material.reserve_gb", 2)) * 1e9
    for p in posten:
        ziel = ordner(konfig) / p.relativ
        # Vor jeder Datei: reicht der Platz noch? (Schätzung für gekürzte Videos: Anteil an der Gesamtdauer)
        kuerzen = p.art == "video" and plan["videos_kuerzen"]
        bedarf = p.groesse
        if kuerzen:
            try:
                bedarf = int(p.groesse * min(1.0, sekunden / max(probe(p.quelle).dauer_s, 1.0)))
            except MedienFehler as e:
                ergebnis["fehler"].append(f"{p.relativ}: {str(e)[:100]}")
                continue
        if shutil.disk_usage(ordner(konfig)).free - bedarf < reserve:
            ergebnis["ohne_platz"] += 1
            continue
        try:
            if kuerzen:
                qh, zh, von = kuerze(p.quelle, ziel, sekunden)
                _merke(con, p, ziel, "video_ende", qh, zh, von)
                ergebnis["gekuerzt"] += 1
            else:
                qh, zh = kopiere(p.quelle, ziel)
                _merke(con, p, ziel, p.art, qh, zh, None)
                ergebnis["kopiert"] += 1
        except (OSError, MedienFehler) as e:
            ergebnis["fehler"].append(f"{p.relativ}: {str(e)[:100]}")
    ergebnis["gb_lokal"] = round(sum(f.stat().st_size for f in ordner(konfig).rglob("*") if f.is_file()) / 1e9, 2)
    return ergebnis


def _warte_auf_speicher(konfig: Konfig) -> None:
    """Nach dem Aufwachen braucht der NFS-Mount einen Moment."""
    ende = time.monotonic() + float(konfig.wert("speicher.wecken_warten_s", 180))
    while True:
        try:
            konfig.pruefe_speicher()
            return
        except SpeicherOffline:
            if time.monotonic() > ende:
                raise
            time.sleep(5)


def _arbeit(konfig: Konfig, con: sqlite3.Connection, probelauf: bool) -> dict:
    bericht = bestand.erstelle(konfig, stichprobe=int(konfig.wert("material.stichprobe", 5)))
    ordner(konfig).mkdir(parents=True, exist_ok=True)
    (ordner(konfig) / "bestand-big.json").write_text(json.dumps(bericht, ensure_ascii=False, indent=2), encoding="utf-8")
    (ordner(konfig) / "bestand-big.md").write_text(bestand.als_markdown(bericht), encoding="utf-8")
    ergebnis = uebertrage(konfig, con, probelauf=probelauf)
    return {**ergebnis, "probelauf": probelauf, "bestand": str(ordner(konfig) / "bestand-big.md")}


def hole(konfig: Konfig, con: sqlite3.Connection, *, probelauf: bool = False) -> dict:
    """Bestandsaufnahme von pve-big und Kopie. Schläft pve-big: genau ein Wecken, danach sofort aus.
    Läuft er schon: nur eine Halten-Marke, damit der Wächter ihn während der Kopie nicht abschaltet."""
    minuten = float(konfig.wert("material.halten_min", 240))
    try:
        konfig.pruefe_speicher()
        erreichbar = True
    except SpeicherOffline as e:
        erreichbar = False
        if konfig.getrennt:  # E19: [speicher] ist der Puffer auf dem Mini – pve-big zu wecken hülfe nicht
            raise SpeicherOffline(f"Puffer nicht bereit ({e}) – pve-big wurde nicht geweckt") from None
        if probelauf:
            return {"probelauf": True, "hinweis": f"pve-big schläft – der Probelauf weckt nicht ({e})"}
    if erreichbar:
        big.setze_marke(konfig, "material", minuten, "Material auf den Mini kopieren")
        try:
            with big.herzschlag(konfig, "material"):
                return {**_arbeit(konfig, con, probelauf), "big": "lief schon"}
        finally:
            big.loese_marke(konfig, "material")
    with big.wach_halten(konfig, "material", "Material auf den Mini kopieren", minuten=minuten):
        _warte_auf_speicher(konfig)
        return {**_arbeit(konfig, con, probelauf), "big": "geweckt und danach heruntergefahren"}
