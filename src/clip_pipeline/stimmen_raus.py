"""Stimmen raus (23.10., Florian: „Warum sind immer noch die Tonspuren meiner Mitspieler im Video zu hören? Das darf
ich nicht veröffentlichen. Sofort raus damit außer sie werden unkenntlich gemacht durch KI generierten Inhalt.“)

Befund am echten Material (stimmen_check auf dem Mini): Die Stimmen liegen im **Spielton** (Spur 0) – der Sprachchat
läuft in Fortnite über die Spielausgabe; die Chat-Spur der SteelSeries-Aufnahme ist leer (−70 LUFS). Spuren weglassen
([regie.ton] stimmen = false) reicht deshalb nicht. Zwei Stufen, beide vor dem Mischen:

1. bereinige(): KI-Quellentrennung (Demucs htdemucs, nur CPU) auf dem Spielton-Ausschnitt jedes Segments. Die
   Stimmen-Stem fällt weg, die Spiel-Geräusche bleiben (Probe 23.10.: 50 Wörter → 0 Wörter, 41 s Rechenzeit je 60 s
   Ton auf dem Mini). Ergebnis als WAV im Cache (Schlüssel: Datei, Größe, Änderungszeit, Start, Dauer, Modell) – die
   Fassungen der Nachtschicht teilen sich die Arbeit, eine neue Fassung nach ❌ auch.
2. pruefe(): Whisper über genau die Tonstücke, die ins Video gehen (nicht über die fertige Mischung – NCS-Musik hat
   oft Gesang). Mehr als [regie.ton] stimmen_max_woerter → StimmenFehler, der Renderer gibt kein Video heraus
   (lieber kein Video als Stimmen Dritter). Ohne Demucs (Freunde-Instanz) prüft sie den rohen Spielton und hält Videos
   mit Stimmen zurück.

Demucs liegt in einer eigenen Umgebung (deploy/pve-mini/stimmen-einrichten.sh), nicht in der Pipeline-venv
(torch ≈ 1 GB). Modell-Dateien landen unter <Cache>/torch, weil das Home des Dienstes nur lesbar ist.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
from pathlib import Path

from . import medien
from .konfig import Konfig
from .medien import MedienFehler, fuehre_aus, probe

log = logging.getLogger("pipeline")

MODELL = "htdemucs"
THREADS = 6            # der Mini hat 8 Kerne – zwei bleiben für Bots und ffmpeg
MAX_WOERTER = 3        # ab so vielen erkannten Wörtern gilt ein Video als „mit Stimmen“
LOGPROB_MIN = -1.0     # Whisper-Abschnitte darunter sind meist Halluzination auf Geräuschen
_gewarnt = False
_whisper = None


class StimmenFehler(MedienFehler):
    """Im Ton, der ins Video soll, sind noch Stimmen – das Video bleibt zurück."""


def _ton(konfig: Konfig) -> dict:
    return konfig.abschnitt("regie").get("ton", {}) if isinstance(konfig.abschnitt("regie").get("ton"), dict) else {}


def _an(konfig: Konfig, schluessel: str, standard: bool) -> bool:
    wert = _ton(konfig).get(schluessel, standard)
    return bool(wert) if not isinstance(wert, str) else wert.strip().lower() in ("1", "true", "ja", "an")


def cache(konfig: Konfig) -> Path:
    """<Datenbank-Ordner>/cache/stimmen – bei Freunden in ihrer Instanz, bei dir /var/lib/clip-pipeline/cache/stimmen."""
    return konfig.datenbank.parent / "cache" / "stimmen"


def demucs_programm(konfig: Konfig) -> Path | None:
    """[regie.ton] demucs (Pfad), sonst <Datenbank-Ordner>/cache/demucs-venv/bin/demucs, sonst `demucs` im PATH."""
    eigen = str(_ton(konfig).get("demucs", "") or "").strip()
    kandidaten = [Path(eigen)] if eigen else []
    kandidaten.append(konfig.datenbank.parent / "cache" / "demucs-venv" / "bin" / "demucs")
    for k in kandidaten:
        if k.is_file() and os.access(k, os.X_OK):
            return k
    im_pfad = shutil.which("demucs")
    return Path(im_pfad) if im_pfad else None


def aktiv(konfig: Konfig) -> bool:
    """Stimmen entfernen? [regie.ton] stimmen_entfernen (ab Werk an) UND Demucs vorhanden. Fehlt Demucs, einmal warnen."""
    global _gewarnt
    if not _an(konfig, "stimmen_entfernen", True):
        return False
    if demucs_programm(konfig) is None:
        if not _gewarnt:
            log.warning("Stimmen entfernen: demucs fehlt (deploy/pve-mini/stimmen-einrichten.sh) – nur die Prüfung läuft")
            _gewarnt = True
        return False
    return True


def pruefen_aktiv(konfig: Konfig) -> bool:
    """[regie.ton] stimmen_pruefen (ab Werk an) – nur sinnvoll mit faster-whisper, sonst still aus."""
    if not _an(konfig, "stimmen_pruefen", True):
        return False
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False
    return True


def _schluessel(datei: Path, start_s: float, dauer_s: float, art: str) -> str:
    st = datei.stat()
    roh = f"{datei.resolve()}|{st.st_size}|{st.st_mtime_ns}|{start_s:.3f}|{dauer_s:.3f}|{art}"
    return hashlib.sha1(roh.encode()).hexdigest()[:24]


def ausschnitt(konfig: Konfig, datei: Path, start_s: float, dauer_s: float) -> Path | None:
    """Spur 0 von datei ab start_s für dauer_s als WAV (44,1 kHz, Stereo) im Cache – Grundlage für Demucs und für die
    Prüfung ohne Demucs. None, wenn die Datei keinen Ton hat."""
    if not probe(datei).tonspuren:
        return None
    ordner = cache(konfig)
    ordner.mkdir(parents=True, exist_ok=True)
    ziel = ordner / f"{_schluessel(datei, start_s, dauer_s, 'roh')}.wav"
    if ziel.is_file() and ziel.stat().st_size > 0:
        return ziel
    tmp = ziel.with_name(ziel.stem + ".tmp.wav")
    fuehre_aus(["ffmpeg", "-hide_banner", "-nostdin", "-nostats", "-y", "-ss", f"{max(0.0, start_s):.3f}",
                "-t", f"{dauer_s:.3f}", "-i", str(datei), "-map", "0:a:0", "-ac", "2", "-ar", "44100",
                "-c:a", "pcm_s16le", str(tmp)], f"Tonausschnitt {datei.name}")
    medien.uebernehmen(tmp, ziel)
    return ziel


def bereinige(konfig: Konfig, datei: Path, start_s: float, dauer_s: float) -> Path | None:
    """Der Spielton (Spur 0) von datei ab start_s für dauer_s OHNE Stimmen, als WAV im Cache (zweiter Aufruf: sofort).
    None, wenn die Datei keinen Ton hat. Fehler: MedienFehler (Demucs fehlt oder scheitert, ffmpeg).
    Beispiel: bereinige(k, clip, 6.283, 22.5) → …/cache/stimmen/<schlüssel>.wav"""
    programm = demucs_programm(konfig)
    if programm is None:
        raise MedienFehler("Stimmen entfernen: demucs fehlt")
    if not probe(datei).tonspuren:
        return None
    modell = str(_ton(konfig).get("demucs_modell", MODELL) or MODELL)
    ordner = cache(konfig)
    ziel = ordner / f"{_schluessel(datei, start_s, dauer_s, modell)}.wav"
    if ziel.is_file() and ziel.stat().st_size > 0:
        return ziel
    roh = ausschnitt(konfig, datei, start_s, dauer_s)
    if roh is None:
        return None
    arbeit = ordner / f"{ziel.stem}.demucs"
    shutil.rmtree(arbeit, ignore_errors=True)
    arbeit.mkdir(parents=True)
    try:
        threads = int(_ton(konfig).get("stimmen_threads", THREADS) or THREADS)
    except (TypeError, ValueError):
        threads = THREADS
    torch_home = ordner.parent / "torch"
    torch_home.mkdir(parents=True, exist_ok=True)
    befehl = ["env", f"TORCH_HOME={torch_home}", f"OMP_NUM_THREADS={threads}", f"MKL_NUM_THREADS={threads}",
              str(programm), "-n", modell, "--two-stems=vocals", "-d", "cpu", "-j", "1", "-o", str(arbeit),
              "--filename", "{stem}.{ext}", str(roh)]
    try:
        # Zeitgrenze: Demucs braucht auf dem Mini rund 0,7 s je Sekunde Ton; das Erste lädt das Modell (≈ 80 MB)
        fuehre_aus(befehl, f"Stimmen entfernen {datei.name}", timeout=max(300.0, dauer_s * 6 + 180))
        ergebnis = arbeit / modell / "no_vocals.wav"
        if not ergebnis.is_file() or ergebnis.stat().st_size == 0:
            raise MedienFehler(f"Stimmen entfernen {datei.name}: demucs schrieb kein no_vocals.wav")
        medien.uebernehmen(ergebnis, ziel)
    finally:
        shutil.rmtree(arbeit, ignore_errors=True)
    return ziel


def _modell(konfig: Konfig):
    global _whisper
    if _whisper is None:
        from faster_whisper import WhisperModel

        s = konfig.abschnitt("stimmung")
        _whisper = WhisperModel(str(s.get("modell", "small")), device=str(s.get("geraet", "cpu")),
                                compute_type=str(s.get("rechenart", "int8")), cpu_threads=int(s.get("threads", 6)))
    return _whisper


def _woerter(konfig: Konfig, wavs: list[Path]) -> tuple[int, str]:
    """Erkannte Wörter (und ein Textanfang) über alle WAVs; Abschnitte mit schwacher Wahrscheinlichkeit zählen nicht."""
    modell = _modell(konfig)
    sprache = str(konfig.wert("stimmung.sprache", "de")) or None
    gesamt, texte = 0, []
    for w in wavs:
        abschnitte, _ = modell.transcribe(str(w), language=sprache, vad_filter=True, beam_size=1)
        for a in abschnitte:
            if a.avg_logprob is not None and a.avg_logprob < LOGPROB_MIN:
                continue
            if getattr(a, "no_speech_prob", 0.0) > 0.6:
                continue
            t = a.text.strip()
            if t:
                gesamt += len(t.split())
                texte.append(t)
    return gesamt, " ".join(texte)[:120]


def pruefe(konfig: Konfig, wavs: list[Path]) -> int | None:
    """Sicherheitsnetz: Sind in den Tonstücken, die ins Video gehen, noch Stimmen? Mehr als [regie.ton]
    stimmen_max_woerter Wörter → StimmenFehler. Rückgabe: erkannte Wörter, None = Prüfung aus oder Whisper fehlt.
    Beispiel: 0 Wörter → 0 · 7 Wörter („Einer fehlt noch, oder? …“) → StimmenFehler."""
    wavs = [w for w in wavs if w is not None]
    if not wavs or not pruefen_aktiv(konfig):
        return None
    try:
        grenze = int(_ton(konfig).get("stimmen_max_woerter", MAX_WOERTER))
    except (TypeError, ValueError):
        grenze = MAX_WOERTER
    n, text = _woerter(konfig, wavs)
    if n > grenze:
        raise StimmenFehler(f"Stimmen erkannt ({n} Wörter: „{text[:60]}…“) – Video zurückgehalten, damit keine "
                            f"Stimmen Dritter hochgeladen werden")
    return n
