"""Bestandsaufnahme (`pipeline bestand`): Was liegt wo, welche Tonspuren, geht VA-API, wie viel Platz ist frei?

Weckt pve-big NICHT. Schläft er, steht das im Ergebnis – die Material-Kopie (`pipeline material`) macht die
Bestandsaufnahme von pve-big dann während ihres einen Weckens mit.

Mikro-Spur: SteelSeries speichert "Game" + "Chat", Nvidia je nach Einstellung 1 oder 2 Spuren. Ob auf der
zweiten Spur wirklich deine Stimme ist, sieht man nur am Pegel -> Stichprobe mit Lautheitsmessung.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections import Counter
from pathlib import Path

from .konfig import Konfig, SpeicherOffline
from .medien import MedienFehler, lautheit, probe

VIDEO_ENDUNGEN = {".mp4", ".mkv", ".mov"}
MIKRO_NAMEN = ("chat", "mic", "mikro", "voice", "stimme", "commentary")
STILLE_LUFS = -60.0  # darunter gilt eine Spur als leer


def _groesse(dateien: list[Path]) -> int:
    return sum(d.stat().st_size for d in dateien)


def _gb(bytes_: int) -> float:
    return round(bytes_ / 1e9, 2)


def platz(pfad: Path) -> dict | None:
    """Freier Platz des Dateisystems, auf dem pfad liegt (nächster existierender Elternordner)."""
    for kandidat in [pfad, *pfad.parents]:
        if kandidat.exists():
            nutzung = shutil.disk_usage(kandidat)
            return {"pfad": str(pfad), "frei_gb": _gb(nutzung.free), "gesamt_gb": _gb(nutzung.total)}
    return None


def vaapi(geraet: str) -> dict:
    """Prüft VA-API mit einem echten Mini-Encode (1 s Testbild) – vainfo allein sagt nicht, ob ffmpeg es kann."""
    ergebnis: dict = {"geraet": geraet, "vorhanden": Path(geraet).exists()}
    if shutil.which("vainfo") and ergebnis["vorhanden"]:
        info = subprocess.run(["vainfo", "--display", "drm", "--device", geraet], capture_output=True, text=True, check=False)
        ergebnis["h264_profil"] = "VAProfileH264" in (info.stdout + info.stderr)
    if not ergebnis["vorhanden"]:
        ergebnis["encode_ok"] = False
        ergebnis["hinweis"] = "kein Render-Gerät – im LXC dev0: /dev/dri/renderD128,gid=<render> ergänzen"
        return ergebnis
    test = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-vaapi_device", geraet, "-f", "lavfi",
         "-i", "testsrc2=size=1280x720:rate=30:duration=1", "-vf", "format=nv12,hwupload",
         "-c:v", "h264_vaapi", "-f", "null", "-"],
        capture_output=True, text=True, check=False,
    )
    ergebnis["encode_ok"] = test.returncode == 0
    if test.returncode:
        ergebnis["hinweis"] = test.stderr.strip()[-200:]
    return ergebnis


def tonspuren(datei: Path, messen: bool = True) -> dict:
    """Namen der Spuren und – für Spuren ab der zweiten – ob Signal drauf ist."""
    info = probe(datei)
    spuren = []
    for i, name in enumerate(info.tonspuren):
        spur = {"nr": i, "name": name, "mikro_name": any(m in name.lower() for m in MIKRO_NAMEN)}
        if messen and i > 0:
            try:
                integriert, spitze = lautheit(datei, spur=i, dauer_s=min(120.0, info.dauer_s))
                spur.update(lufs=round(integriert, 1), spitze=round(spitze, 1), signal=spitze > STILLE_LUFS)
            except MedienFehler:
                spur.update(signal=False)
        spuren.append(spur)
    return {"datei": str(datei), "dauer_s": round(info.dauer_s, 1), "spuren": spuren}


def mikro_spur(spuren: list[dict]) -> int | None:
    """Welche Spur ist das Mikro? Erst nach Name, sonst die zweite Spur, wenn Signal drauf ist."""
    for s in spuren:
        if s["mikro_name"] and s.get("signal", True):
            return s["nr"]
    if len(spuren) > 1 and spuren[1].get("signal"):
        return 1
    return None


def durchsuche(wurzel: Path, *, stichprobe: int = 5, messen: bool = True) -> dict:
    """Replays und Videos unter wurzel. Tonspuren nur für die neuesten `stichprobe` Videos je Ordner."""
    replays = sorted(wurzel.rglob("*.replay"))
    videos = [p for p in wurzel.rglob("*") if p.suffix.lower() in VIDEO_ENDUNGEN and p.is_file()
              and ".tmp" not in p.name and not p.name.endswith(".teil")]
    je_ordner: dict[str, list[Path]] = {}
    for v in videos:
        je_ordner.setdefault(v.parent.relative_to(wurzel).as_posix(), []).append(v)
    ordner_info = {}
    mikro = Counter()
    for name, dateien in sorted(je_ordner.items()):
        neueste = sorted(dateien, key=lambda p: p.stat().st_mtime, reverse=True)[:stichprobe]
        proben = []
        for d in neueste:
            try:
                t = tonspuren(d, messen=messen)
            except MedienFehler as e:
                proben.append({"datei": str(d), "fehler": str(e)[:120]})
                continue
            t["mikro_spur"] = mikro_spur(t["spuren"])
            mikro["ja" if t["mikro_spur"] is not None else "nein"] += 1
            proben.append(t)
        ordner_info[name] = {"anzahl": len(dateien), "gb": _gb(_groesse(dateien)), "stichprobe": proben}
    return {
        "wurzel": str(wurzel),
        "replays": {
            "anzahl": len(replays), "gb": _gb(_groesse(replays)),
            "aelteste": replays[0].name if replays else None, "neueste": replays[-1].name if replays else None,
            "ordner": sorted({p.parent.relative_to(wurzel).as_posix() for p in replays}),
        },
        "videos": {"anzahl": len(videos), "gb": _gb(_groesse(videos)), "ordner": ordner_info},
        "mikro_stichprobe": dict(mikro),
    }


def erstelle(konfig: Konfig, *, weitere: list[Path] | None = None, stichprobe: int = 5, messen: bool = True) -> dict:
    ergebnis: dict = {"speicher": None}
    try:
        konfig.pruefe_speicher()  # weckt nicht
        ergebnis["speicher"] = durchsuche(konfig.wurzel, stichprobe=stichprobe, messen=messen)
    except SpeicherOffline as e:
        ergebnis["speicher_offline"] = str(e)
    ergebnis["weitere"] = [durchsuche(p, stichprobe=stichprobe, messen=messen) for p in weitere or [] if p.is_dir()]
    material = Path(str(konfig.wert("material.ordner", "/var/lib/clip-pipeline/material")))
    ergebnis["platz"] = [x for x in (platz(konfig.wurzel), platz(konfig.datenbank.parent), platz(material)) if x]
    ergebnis["vaapi"] = vaapi(str(konfig.wert("schnitt.vaapi_geraet", "/dev/dri/renderD128")))
    ergebnis["cpu_kerne"] = os.cpu_count()
    return ergebnis


def als_markdown(b: dict) -> str:
    """Kurzer, lesbarer Bericht für docs/BESTAND.md."""
    z = ["# Bestandsaufnahme", ""]
    for teil in [b.get("speicher"), *b.get("weitere", [])]:
        if not teil:
            continue
        r, v = teil["replays"], teil["videos"]
        z += [f"## {teil['wurzel']}", "",
              f"- Replays: **{r['anzahl']}** ({r['gb']} GB), {r['aelteste']} … {r['neueste']}",
              f"- Videos: **{v['anzahl']}** ({v['gb']} GB)",
              f"- Mikro-Spur in der Stichprobe: {teil['mikro_stichprobe']}", ""]
        for name, o in v["ordner"].items():
            spuren = {", ".join(f"{s['name']}{'' if s.get('signal', True) else ' (leer)'}" for s in p["spuren"])
                      for p in o["stichprobe"] if "spuren" in p}
            z.append(f"  - `{name}`: {o['anzahl']} Dateien, {o['gb']} GB · Spuren: {' | '.join(sorted(spuren)) or '–'}")
        z.append("")
    if off := b.get("speicher_offline"):
        z += [f"- Speicher offline: {off}", ""]
    z += ["## Platz", ""] + [f"- `{p['pfad']}`: {p['frei_gb']} GB frei von {p['gesamt_gb']} GB" for p in b["platz"]]
    va = b["vaapi"]
    z += ["", "## VA-API", "", f"- Gerät {va['geraet']}: {'vorhanden' if va['vorhanden'] else 'fehlt'}, "
          f"Test-Encode {'✅' if va.get('encode_ok') else '❌'} {va.get('hinweis', '')}".rstrip(), ""]
    return "\n".join(z)
