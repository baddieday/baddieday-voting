"""Hilfen für Effekt-Tests (Regisseur 2.0): kleine gültige v4-Listen ohne compose, Testvideos, Bild- und Tonmessung."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import numpy as np

from clip_pipeline import effekte, regie

RATE = 48000


def mini_liste(teile: list[tuple], *, fmt: str = "short", ereignisse: list[tuple] = (), look=("neutral", 0.0),
               fps: int = 30, an: bool = True, quelle_dauer: float = 8.0, musik: dict | None = None) -> dict:
    """Gültige Schnittliste version 4 aus wenigen Segmenten – ohne Datenbank und ohne compose.

    teile: (Datei, Quelle von, Quelle bis, (Übergangsart, Dauer) in dieses Segment) – beim ersten Segment egal.
    ereignisse: (Segment-nr, Art, t auf der ZEITLEISTE, Felder) – umgerechnet in Quellzeit über effekte.auf_quelle.
    Für weiche Übergänge brauchen die Quellen Griffe: Quelle von ≥ Dauer/2, Quelle bis ≤ Dateilänge − Dauer/2."""
    segmente, t = [], 0.0
    for nr, (datei, von, bis, (art, d)) in enumerate(teile, 1):
        segmente.append({
            "nr": nr, "moment": f"datei:{nr}", "clip_id": None, "match_id": None, "datei": str(datei),
            "stimmung": "episch", "quelle_start_s": von, "quelle_ende_s": bis, "quelle_dauer_s": quelle_dauer,
            "muss": [von, bis], "zeit_start": round(t, 3), "zeit_ende": round(t + bis - von, 3),
            "uebergang": {"art": art if nr > 1 else "schnitt", "dauer_s": d if nr > 1 else 0.0}})
        t += bis - von
    for nr, art, t_z, felder in ereignisse:
        s = segmente[nr - 1]
        eintrag = {"art": art, "t_s": round(effekte.auf_quelle(s, t_z), 3), "staerke": 1.0, **felder}
        s.setdefault("effekte", []).append(eintrag)
    b, h = (1080, 1920) if fmt == "short" else (1920, 1080)
    liste = {"version": 4, "art": "regie", "name": "mini", "format": fmt, "aufloesung": [b, h], "fps": fps,
             "dauer_s": round(t, 3), "stimmung": "episch", "parameter": {}, "musik": musik,
             "overlay": "clip-battle.de" if fmt == "short" else None,
             "effekte": {"an": an, "profil_version": 1, "look": look[0], "look_staerke": look[1], "hook": False,
                         "loop": False},
             "bogen": [], "segmente": segmente, "hinweise": [], "erstellt": "2026-09-25T12:00:00+00:00"}
    if fehler := regie.pruefe_liste(liste):
        raise AssertionError(fehler)
    return liste


def ohne_effekte(liste: dict) -> dict:
    """Dieselbe Liste als version 3: ohne Effekt-Plan und ohne die v4-Felder der Segmente."""
    alt = {k: v for k, v in liste.items() if k != "effekte"}
    alt["version"] = 3
    alt["segmente"] = [{k: v for k, v in s.items() if k not in regie.V4_FELDER} for s in liste["segmente"]]
    return alt


# --- Testvideos (640×360, 30 fps) --------------------------------------------------------------

def _video(ziel: Path, bild: str, dauer: float, ton: str) -> Path:
    ziel.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "lavfi", "-i", bild,
                    "-f", "lavfi", "-i", f"{ton}:d={dauer}" if ton.startswith("sine") else ton, "-t", f"{dauer}",
                    "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(ziel)], check=True)
    return ziel


GRAU = 0x40      # Grund der Testvideos (Luma ≈ 64)
QUADRAT = 90     # Seite des weißen Quadrats in der Quelle (px)


def quadrat_video(ziel: Path, dauer: float = 8.0, breite: int = 640) -> Path:
    """Dunkelgrau mit weißem Quadrat in der Mitte, Ton: Stille (Stereo) – für Zoom-, Text- und Klang-Tests.
    breite 480: eine 4:3-Aufnahme (480×360)."""
    x, y = (breite - QUADRAT) // 2, (360 - QUADRAT) // 2
    bild = (f"color=c=0x{GRAU:02x}{GRAU:02x}{GRAU:02x}:s={breite}x360:r=30:d={dauer},"
            f"drawbox=x={x}:y={y}:w={QUADRAT}:h={QUADRAT}:color=white:t=fill")
    return _video(ziel, bild, dauer, "anullsrc=r=48000:cl=stereo")


RAMPE_MAX = 200  # hellster Wert der Rampe (RGB) – Platz nach oben, damit nur die Schrift ≥ 235 ist


def rampe_video(ziel: Path, dauer: float = 6.0) -> Path:
    """Graurampe von links (0) nach rechts (RAMPE_MAX in RGB), Ton: Sinus – für den Look."""
    luma = 16 + 219 * RAMPE_MAX / 255
    bild = f"color=c=gray:s=640x360:r=30:d={dauer},format=yuv420p,geq=lum='16+{luma - 16:.3f}*X/W':cb=128:cr=128"
    return _video(ziel, bild, dauer, "sine=frequency=300")


# --- Messen ---------------------------------------------------------------------------------------

def bilder(video: Path, nummern: list[int], pix_fmt: str = "gray") -> dict[int, np.ndarray]:
    """Einzelbilder (Bildnummer -> Array h×w bzw. h×w×3) in einem ffmpeg-Aufruf."""
    info = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                           "-of", "csv=p=0", str(video)], capture_output=True, text=True, check=True).stdout
    b, h = (int(x) for x in info.strip().split(","))
    auswahl = "+".join(f"eq(n\\,{n})" for n in sorted(set(nummern)))
    roh = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(video),
                          "-vf", f"select='{auswahl}'", "-fps_mode", "passthrough", "-f", "rawvideo",
                          "-pix_fmt", pix_fmt, "-"], capture_output=True, check=True).stdout
    kanaele = 3 if pix_fmt == "rgb24" else 1
    form = (h, b, kanaele) if kanaele > 1 else (h, b)
    daten = np.frombuffer(roh, dtype=np.uint8).reshape(-1, *form)
    return dict(zip(sorted(set(nummern)), daten))


def pcm(video: Path) -> np.ndarray:
    """Ton (linker Kanal) als float in −1..1, 48 kHz."""
    roh = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(video), "-vn",
                          "-ac", "2", "-ar", str(RATE), "-f", "s16le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(roh, dtype="<i2").reshape(-1, 2)[:, 0] / 32768


def einsatz(ton: np.ndarray, schwelle_db: float, ab_s: float = 0.0) -> float:
    """Erste Sekunde ab ab_s, in der der Ton die Schwelle (dBFS) überschreitet."""
    start = int(ab_s * RATE)
    ueber = np.nonzero(np.abs(ton[start:]) >= 10 ** (schwelle_db / 20))[0]
    if not len(ueber):
        raise AssertionError(f"kein Einsatz über {schwelle_db} dBFS ab {ab_s} s")
    return (start + ueber[0]) / RATE


def filter_namen(graph: str) -> list[str]:
    """Namen aller Filter im Graphen (Labels und Werte in '…' überlesen)."""
    ohne = re.sub(r"'[^']*'", "''", graph)
    ohne = re.sub(r"\[[^\]]*\]", "", ohne)
    return [teil.split("=", 1)[0].strip() for teil in re.split(r"[;,]", ohne) if teil.strip()]
