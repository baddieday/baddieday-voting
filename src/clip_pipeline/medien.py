"""FFmpeg/ffprobe-Aufrufe. Hier steckt das ganze Video-Handwerk.

Alle Befehle werden als Liste gebaut (kein Shell-String), damit Leerzeichen in
Dateinamen wie "Fortnite 2026.09.21 - 21.53.34.22.Doppeleliminierung.DVR.mp4"
kein Problem sind.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


class MedienFehler(RuntimeError):
    pass


@dataclass
class Probe:
    dauer_s: float
    breite: int
    hoehe: int
    fps: float  # nominelle Bildrate (r_frame_rate)
    vfr: bool  # variable Bildrate? (Nvidia-Highlights sind VFR)
    tonspuren: list[str] = field(default_factory=list)  # Namen, z. B. ["Game", "Chat"]
    tags: dict[str, str] = field(default_factory=dict)  # Format-Tags (Metadaten)


def _bruch(text: str | None) -> float:
    if not text or text in ("0/0", "0"):
        return 0.0
    zaehler, _, nenner = text.partition("/")
    return float(zaehler) / float(nenner or 1)


def fuehre_aus(befehl: list[str], was: str, timeout: float | None = None) -> str:
    """Führt einen Befehl aus und gibt stderr zurück (FFmpeg schreibt seine Infos dorthin)."""
    try:
        ergebnis = subprocess.run(
            befehl,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        raise MedienFehler(f"{was}: Programm {befehl[0]!r} nicht gefunden") from None
    except subprocess.TimeoutExpired:
        raise MedienFehler(f"{was}: Zeitlimit überschritten") from None
    if ergebnis.returncode != 0:
        rest = "\n".join(ergebnis.stderr.strip().splitlines()[-8:])
        raise MedienFehler(f"{was} fehlgeschlagen (Exit {ergebnis.returncode}):\n{rest}")
    return ergebnis.stdout + ergebnis.stderr


def probe(pfad: Path) -> Probe:
    ausgabe = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(pfad)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if ausgabe.returncode != 0:
        raise MedienFehler(f"ffprobe {pfad.name}: {ausgabe.stderr.strip()[-300:]}")
    daten = json.loads(ausgabe.stdout or "{}")
    video = next((s for s in daten.get("streams", []) if s.get("codec_type") == "video"), {})
    audio = [s for s in daten.get("streams", []) if s.get("codec_type") == "audio"]
    nominal = _bruch(video.get("r_frame_rate"))
    mittel = _bruch(video.get("avg_frame_rate"))
    return Probe(
        dauer_s=float(daten.get("format", {}).get("duration") or 0.0),
        breite=int(video.get("width") or 0),
        hoehe=int(video.get("height") or 0),
        fps=nominal,
        vfr=bool(nominal and mittel and abs(nominal - mittel) > 0.01),
        tonspuren=[(s.get("tags") or {}).get("handler_name", f"Spur {i + 1}") for i, s in enumerate(audio)],
        tags={str(k): str(v) for k, v in (daten.get("format", {}).get("tags") or {}).items()},
    )


def _ziel_fps(fps: float) -> str:
    """VFR-Quellen bekommen ihre nominelle Rate als feste Rate (60 bleibt 60)."""
    gerundet = round(fps) if fps > 0 else 30
    return str(max(1, min(gerundet, 120)))


def _video_encoder(encoder: str, crf: int, vaapi_geraet: str) -> tuple[list[str], list[str]]:
    """Gibt (Eingangs-Optionen, Ausgangs-Optionen) für den gewählten Encoder zurück."""
    if encoder == "h264_vaapi":
        return (
            ["-vaapi_device", vaapi_geraet],
            ["-vf", "format=nv12,hwupload", "-c:v", "h264_vaapi", "-qp", str(crf)],
        )
    return [], ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf), "-pix_fmt", "yuv420p"]


def schneide(
    quelle: Path,
    start_s: float,
    dauer_s: float,
    ziel: Path,
    *,
    fps: float,
    encoder: str = "libx264",
    crf: int = 18,
    vaapi_geraet: str = "/dev/dri/renderD128",
) -> None:
    """Schneidet framegenau mit Neukodierung und konstanter Bildrate. Alle Tonspuren bleiben erhalten.

    -ss vor -i springt schnell zur Stelle; weil neu kodiert wird, ist der Schnitt trotzdem exakt.
    Mit "-c copy" würde nur an Keyframes geschnitten – bis zu mehreren Sekunden daneben.
    """
    ziel.parent.mkdir(parents=True, exist_ok=True)
    tmp = ziel.with_name(ziel.stem + ".tmp" + ziel.suffix)
    eingang, ausgang = _video_encoder(encoder, crf, vaapi_geraet)
    befehl = [
        "ffmpeg", "-hide_banner", "-nostdin", "-y", *eingang,
        "-ss", f"{max(0.0, start_s):.3f}", "-i", str(quelle), "-t", f"{dauer_s:.3f}",
        "-map", "0:v:0", "-map", "0:a?",
        *ausgang,
        "-fps_mode", "cfr", "-r", _ziel_fps(fps),
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart", str(tmp),
    ]
    fuehre_aus(befehl, f"Schnitt {ziel.name}")
    tmp.replace(ziel)  # erst am Ende umbenennen: nie halbfertige Clips


def _audio_mix(anzahl: int) -> tuple[list[str], list[str]]:
    """Telegram spielt nur die erste Tonspur ab – deshalb alle Spuren zu einer mischen."""
    if anzahl <= 0:
        return [], ["-an"]
    if anzahl == 1:
        return [], ["-map", "0:a:0", "-c:a", "aac", "-b:a", "128k"]
    eingaenge = "".join(f"[0:a:{i}]" for i in range(anzahl))
    return (
        ["-filter_complex", f"{eingaenge}amix=inputs={anzahl}:normalize=0[mix]"],
        ["-map", "[mix]", "-c:a", "aac", "-b:a", "128k"],
    )


def vorschau(quelle: Path, ziel: Path, *, max_bytes: int, kurze_seite: int = 720) -> int:
    """Rendert eine kleine Vorschau unter max_bytes. Gibt die Dateigröße zurück."""
    info = probe(quelle)
    dauer = max(info.dauer_s, 1.0)
    ton_kbit = 128 if info.tonspuren else 0
    # Bitrate so wählen, dass die Datei mit 8 % Reserve unter die Grenze passt
    video_kbit = int((max_bytes * 8 * 0.92 / dauer) / 1000) - ton_kbit
    skalierung = (
        f"scale='if(gt(iw,ih),-2,{kurze_seite})':'if(gt(iw,ih),{kurze_seite},-2)'"
    )
    ziel.parent.mkdir(parents=True, exist_ok=True)
    tmp = ziel.with_name(ziel.stem + ".tmp" + ziel.suffix)
    for _versuch in range(3):
        kbit = max(250, min(video_kbit, 6000))
        filter_opt, ton_opt = _audio_mix(len(info.tonspuren))
        befehl = [
            "ffmpeg", "-hide_banner", "-nostdin", "-y", "-i", str(quelle),
            *filter_opt,
            "-map", "0:v:0", "-vf", skalierung,
            "-c:v", "libx264", "-preset", "veryfast", "-b:v", f"{kbit}k",
            "-maxrate", f"{int(kbit * 1.3)}k", "-bufsize", f"{kbit * 2}k", "-pix_fmt", "yuv420p",
            *ton_opt, "-movflags", "+faststart", str(tmp),
        ]
        fuehre_aus(befehl, f"Vorschau {ziel.name}")
        groesse = tmp.stat().st_size
        if groesse <= max_bytes:
            tmp.replace(ziel)
            return groesse
        video_kbit = int(kbit * 0.75)
    tmp.unlink(missing_ok=True)
    raise MedienFehler(f"Vorschau {ziel.name} bleibt über {max_bytes // 1_000_000} MB")


_MOMENTAN = re.compile(r"\bM:\s*(-?\d+(?:\.\d+)?)")
_INTEGRIERT = re.compile(r"^\s*I:\s*(-?\d+(?:\.\d+)?)\s*LUFS", re.MULTILINE)


def lautheit(pfad: Path, *, spur: int = 0, start_s: float | None = None, dauer_s: float | None = None) -> tuple[float, float]:
    """Misst die Lautheit nach EBU R128: (integriert in LUFS, lautester Moment in LUFS).

    "Momentan" (M) ist die Lautheit der letzten 0,4 s – ihr Maximum zeigt die lauteste Stelle.
    """
    befehl = ["ffmpeg", "-hide_banner", "-nostdin"]
    if start_s is not None:
        befehl += ["-ss", f"{max(0.0, start_s):.3f}"]
    befehl += ["-i", str(pfad)]
    if dauer_s is not None:
        befehl += ["-t", f"{dauer_s:.3f}"]
    # framelog=info: Einzelwerte auf normaler Log-Stufe ausgeben (FFmpeg 8 zeigt "verbose" sonst nicht)
    befehl += ["-map", f"0:a:{spur}", "-af", "ebur128=framelog=info", "-f", "null", "-"]
    text = fuehre_aus(befehl, f"Lautheit {pfad.name}")
    momentan = [float(w) for w in _MOMENTAN.findall(text)]
    integriert = _INTEGRIERT.findall(text)
    if not momentan or not integriert:
        raise MedienFehler(f"Lautheit {pfad.name}: keine Messwerte")
    # Der integrierte Wert ignoriert Stille (Gating), das Maximum ist davon unberührt.
    return float(integriert[-1]), max(momentan)
