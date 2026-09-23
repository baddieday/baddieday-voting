"""Shorts im Hochformat (1080×1920) für YouTube Shorts und TikTok.

Aufbau des FFmpeg-Filtergraphen:
  1. Layout: "unschaerfe" (ganzes Bild + unscharfer Hintergrund), "zuschnitt" (Mitte)
     oder "mit-cam" (Cam-Ausschnitt oben, Gameplay unten)
  2. Overlay "clip-battle.de" (Links sind in Shorts/TikTok nicht klickbar -> im Bild zeigen)
  3. Endcard: 2,5 s "Wer gewinnt das Battle? Stimm ab auf clip-battle.de", weich überblendet
Alle Tonspuren (Spiel + Mikro) werden gemischt und am Ende ausgeblendet.
"""

from __future__ import annotations

from pathlib import Path

from .konfig import Konfig
from .medien import MedienFehler, fuehre_aus, probe

B, H = 1080, 1920
UEBERBLENDUNG = 0.5


def schrift(konfig: Konfig) -> Path:
    for kandidat in konfig.wert("shorts.schriften", []):
        if Path(kandidat).is_file():
            return Path(kandidat)
    raise MedienFehler("Keine Schriftdatei gefunden – [shorts].schriften in der Konfiguration ergänzen")


def _filterpfad(pfad: Path) -> str:
    """Pfade in FFmpeg-Filtern: '/' statt '\\' und ':' maskieren (C:/ -> C\\:/)."""
    return "'" + str(pfad).replace("\\", "/").replace(":", "\\:") + "'"


def _text(text: str) -> str:
    """Text für drawtext maskieren."""
    for zeichen in ("\\", "'", ":", "%", ","):
        text = text.replace(zeichen, "\\" + zeichen)
    return f"'{text}'"


def _layout(layout: str, konfig: Konfig) -> str:
    """Filterkette von [0:v] zu [bild] mit 1080×1920."""
    if layout == "zuschnitt":
        return f"[0:v]crop=ih*9/16:ih,scale={B}:{H}[bild]"
    if layout == "mit-cam":
        cam = konfig.abschnitt("shorts").get("cam") or {}
        try:
            x, y, w, h = (int(cam[k]) for k in ("x", "y", "b", "h"))
        except (KeyError, TypeError, ValueError):
            raise MedienFehler("Layout mit-cam braucht [shorts.cam] x, y, b, h (Cam-Ausschnitt in Pixeln)") from None
        cam_h = int(H * 0.35)
        spiel_h = H - cam_h
        return (
            f"[0:v]split=2[c][s];"
            f"[c]crop={w}:{h}:{x}:{y},scale={B}:{cam_h}:force_original_aspect_ratio=increase,crop={B}:{cam_h}[oben];"
            f"[s]crop=ih*{B}/{spiel_h}:ih,scale={B}:{spiel_h}[unten];"
            f"[oben][unten]vstack[bild]"
        )
    # Standard: Hintergrund füllt unscharf den Rest. zoom > 1 schneidet links/rechts ab -> Spielbild größer
    zoom = max(1.0, float(konfig.wert("shorts.zoom", 1.0)))
    return (
        f"[0:v]split=2[hg][vg];"
        f"[hg]scale={B}:{H}:force_original_aspect_ratio=increase,crop={B}:{H},boxblur=20:2,eq=brightness=-0.08[hg2];"
        f"[vg]crop=trunc(iw/{zoom}/2)*2:ih,scale={B}:-2[vg2];"
        f"[hg2][vg2]overlay=(W-w)/2:(H-h)/2[bild]"
    )


def filtergraph(*, dauer: float, fps: int, tonspuren: int, layout: str, konfig: Konfig) -> tuple[str, float]:
    """Baut den kompletten Filtergraphen. Gibt (Graph, Gesamtdauer) zurück."""
    s = konfig.abschnitt("shorts")
    font = _filterpfad(schrift(konfig))
    teile = [_layout(layout, konfig)]
    kette = "[bild]"
    if s.get("overlay", True):
        teile.append(
            f"{kette}drawtext=fontfile={font}:text={_text(s.get('overlay_text', 'clip-battle.de'))}:"
            f"fontsize=58:fontcolor=white@0.9:borderw=4:bordercolor=black@0.6:"
            f"x=(w-text_w)/2:y=h*{float(s.get('overlay_y', 0.12))}[mitlogo]"
        )
        kette = "[mitlogo]"
    teile.append(f"{kette}fps={fps},format=yuv420p,settb=AVTB[haupt]")

    endcard = float(s.get("endcard_s", 2.5)) if s.get("endcard", True) else 0.0
    if endcard > 0:
        zeilen = [("Wer gewinnt das Battle?", 64, 0.40), ("Stimm ab auf", 64, 0.47), ("clip-battle.de", 120, 0.53)]
        karte = ",".join(
            f"drawtext=fontfile={font}:text={_text(t)}:fontsize={g}:fontcolor=white:x=(w-text_w)/2:y=h*{y}"
            for t, g, y in zeilen
        )
        teile.append(f"color=c=0x0f0f1a:s={B}x{H}:r={fps}:d={endcard},{karte},format=yuv420p,settb=AVTB[karte]")
        teile.append(f"[haupt][karte]xfade=transition=fade:duration={UEBERBLENDUNG}:offset={max(0.0, dauer - UEBERBLENDUNG):.3f}[v]")
        gesamt = dauer + endcard - UEBERBLENDUNG
    else:
        teile.append("[haupt]null[v]")
        gesamt = dauer

    if tonspuren:
        eingaenge = "".join(f"[0:a:{i}]" for i in range(tonspuren))
        mix = f"{eingaenge}amix=inputs={tonspuren}:normalize=0" if tonspuren > 1 else f"{eingaenge}anull"
        teile.append(f"{mix},afade=t=out:st={max(0.0, dauer - UEBERBLENDUNG):.3f}:d={UEBERBLENDUNG},apad=whole_dur={gesamt:.3f}[a]")
    return ";".join(teile), gesamt


def ziel_fuer(konfig: Konfig, clip) -> Path:
    return konfig.ordner("sessions") / clip["match_id"] / "shorts" / f"{int(clip['nr']):03d}_short.mp4"


def rendere(clip: Path, ziel: Path, konfig: Konfig, *, layout: str | None = None, max_bytes: int = 49_000_000) -> int:
    """Rendert den Short und bleibt unter max_bytes (Telegram-Grenze für Bots). Gibt die Größe zurück."""
    info = probe(clip)
    fps = max(1, min(60, round(info.fps or 30)))
    graph, gesamt = filtergraph(
        dauer=info.dauer_s, fps=fps, tonspuren=len(info.tonspuren),
        layout=layout or str(konfig.wert("shorts.layout", "unschaerfe")), konfig=konfig,
    )
    ton = ["-map", "[a]", "-c:a", "aac", "-b:a", "192k"] if info.tonspuren else ["-an"]
    # Obergrenze der Bitrate so, dass die Datei sicher unter max_bytes bleibt
    budget_kbit = int(max_bytes * 8 * 0.9 / gesamt / 1000) - (192 if info.tonspuren else 0)
    maxrate = max(1500, min(budget_kbit, int(konfig.wert("shorts.max_kbit", 12000))))
    ziel.parent.mkdir(parents=True, exist_ok=True)
    tmp = ziel.with_name(ziel.stem + ".tmp" + ziel.suffix)
    for _ in range(3):
        befehl = [
            "ffmpeg", "-hide_banner", "-nostdin", "-y", "-i", str(clip), "-filter_complex", graph,
            "-map", "[v]", *ton, "-t", f"{gesamt:.3f}",
            "-c:v", "libx264", "-preset", "medium", "-crf", str(konfig.wert("shorts.crf", 20)),
            "-maxrate", f"{maxrate}k", "-bufsize", f"{maxrate * 2}k", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", str(tmp),
        ]
        fuehre_aus(befehl, f"Short {ziel.name}")
        groesse = tmp.stat().st_size
        if groesse <= max_bytes:
            tmp.replace(ziel)
            return groesse
        maxrate = int(maxrate * 0.75)
    tmp.unlink(missing_ok=True)
    raise MedienFehler(f"Short {ziel.name} bleibt über {max_bytes // 1_000_000} MB")
