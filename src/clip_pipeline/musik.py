"""Musik: Tempo, Beats und Energie messen, Titel mit Quellenangabe verwalten, NCS-Titel laden.

Analyse ohne große Bibliothek (nur numpy), damit jeder Schritt nachvollziehbar bleibt:
  1. ffmpeg dekodiert zu Mono, 22 050 Hz
  2. "Onset-Hülle": Wie stark nimmt die Lautstärke je Frequenz von Fenster zu Fenster ZU? (Spectral Flux)
     Schläge, Snares, Akkordwechsel geben Spitzen.
  3. Tempo: Autokorrelation der Hülle – in welchem Abstand wiederholen sich die Spitzen? Bevorzugt um 120 BPM.
  4. Beats: dynamische Programmierung nach D. Ellis (2007): Folge von Zeitpunkten mit starker Hülle, deren
     Abstände möglichst genau zum Tempo passen.
  5. Energie 0..1: Tempo + Dichte der Anschläge + Lautheit; dazu ein Verlauf je Sekunde (für "Drops").
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import re
import shutil
import sqlite3
import subprocess
import urllib.request
from pathlib import Path

import numpy as np

from .konfig import Konfig
from .medien import MedienFehler
from .zeit import iso, jetzt

log = logging.getLogger("pipeline")
SR = 22050
HOP = 512
NFFT = 2048
FPS = SR / HOP  # Hüllen-Werte pro Sekunde (≈ 43)
ENDUNGEN = {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".flac", ".opus"}

# Ziel-Eigenschaften je Stimmung (Startwerte; der Lern-Bot verschiebt sie)
ZIEL = {
    "episch": {"energie": 0.85, "bpm": 140},
    "spannend": {"energie": 0.70, "bpm": 128},
    "lustig": {"energie": 0.55, "bpm": 112},
    "frustriert": {"energie": 0.45, "bpm": 95},
    "chill": {"energie": 0.25, "bpm": 90},
}


# --- Analyse ----------------------------------------------------------------------

def lade_pcm(datei: Path, max_s: float | None = None) -> np.ndarray:
    befehl = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(datei)]
    if max_s:
        befehl += ["-t", str(max_s)]
    befehl += ["-ac", "1", "-ar", str(SR), "-f", "f32le", "-"]
    try:
        roh = subprocess.run(befehl, capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError) as e:
        raise MedienFehler(f"Musik {datei.name} nicht lesbar: {e}") from None
    return np.frombuffer(roh, dtype=np.float32)


def _spektrum(x: np.ndarray) -> np.ndarray:
    """Betragsspektrum je Fenster (Zeit x Frequenz)."""
    if len(x) < NFFT:
        x = np.pad(x, (0, NFFT - len(x)))
    anzahl = 1 + (len(x) - NFFT) // HOP
    fenster = np.lib.stride_tricks.as_strided(x, shape=(anzahl, NFFT), strides=(x.strides[0] * HOP, x.strides[0]))
    return np.abs(np.fft.rfft(fenster * np.hanning(NFFT), axis=1))


def onset_huelle(spektrum: np.ndarray) -> np.ndarray:
    log_mag = np.log1p(100 * spektrum)
    fluss = np.maximum(0.0, np.diff(log_mag, axis=0)).sum(axis=1)
    fluss = np.concatenate([[0.0], fluss])
    # langsames Mittel abziehen: nur Anstiege über dem örtlichen Pegel zählen
    breite = int(FPS)  # 1 s
    mittel = np.convolve(fluss, np.ones(breite) / breite, mode="same")
    huelle = np.maximum(0.0, fluss - mittel)
    std = huelle.std()
    return huelle / std if std > 0 else huelle


def tempo(huelle: np.ndarray, min_bpm: float = 60, max_bpm: float = 200) -> float:
    n = len(huelle)
    if n < FPS * 4:
        return 0.0
    h = huelle - huelle.mean()
    spektrum = np.fft.rfft(h, 2 * n)
    ak = np.fft.irfft(spektrum * np.conj(spektrum))[:n]
    lags = np.arange(int(60 * FPS / max_bpm), int(60 * FPS / min_bpm) + 1)
    bpm = 60 * FPS / lags
    gewicht = np.exp(-0.5 * (np.log2(bpm / 120.0) / 1.0) ** 2)  # Menschen hören eher um 120 BPM
    werte = ak[lags] * gewicht
    i = int(np.argmax(werte))
    # Feinjustierung zwischen zwei Lags (Parabel durch drei Punkte)
    if 0 < i < len(werte) - 1:
        a, b, c = werte[i - 1], werte[i], werte[i + 1]
        teiler = a - 2 * b + c
        versatz = 0.5 * (a - c) / teiler if teiler else 0.0
    else:
        versatz = 0.0
    return float(60 * FPS / (lags[i] + versatz))


def beats(huelle: np.ndarray, bpm: float, straffheit: float = 100.0) -> list[float]:
    """Beat-Zeitpunkte in Sekunden (Ellis 2007)."""
    if bpm <= 0 or len(huelle) == 0:
        return []
    periode = 60 * FPS / bpm
    n = len(huelle)
    punkte = huelle.astype(float).copy()
    zurueck = np.full(n, -1)
    von, bis = int(round(periode / 2)), int(round(2 * periode))
    abstaende = np.arange(von, bis + 1)
    strafe = -straffheit * np.log(abstaende / periode) ** 2
    for t in range(von, n):
        kandidaten = t - abstaende
        gueltig = kandidaten >= 0
        if not gueltig.any():
            continue
        werte = punkte[kandidaten[gueltig]] + strafe[gueltig]
        j = int(np.argmax(werte))
        punkte[t] = huelle[t] + werte[j]
        zurueck[t] = kandidaten[gueltig][j]
    # Ende: bester Punkt im letzten Takt, dann rückwärts
    t = int(n - 1 - np.argmax(punkte[::-1][: max(1, int(periode))]))
    folge = []
    while t >= 0:
        folge.append(t)
        t = int(zurueck[t])
    folge.reverse()
    # Anfang ohne Anschlag (Stille vor dem ersten Beat) weglassen
    schwelle = 0.3 * np.median(huelle[folge]) if folge else 0
    folge = [f for f in folge if huelle[max(0, f - 2):f + 3].max() >= schwelle]
    # Ein Anschlag wirkt am stärksten, wenn er in der Fenstermitte liegt -> halbe Fensterbreite dazu
    return [round(f / FPS + NFFT / (2 * SR), 3) for f in folge]


def analysiere(datei: Path) -> dict:
    x = lade_pcm(datei)
    if len(x) < SR:
        raise MedienFehler(f"Musik {datei.name} ist kürzer als 1 s")
    spektrum = _spektrum(x)
    huelle = onset_huelle(spektrum)
    bpm = tempo(huelle)
    # Oktav-Fehler abfangen: sehr langsame/schnelle Werte in den üblichen Bereich falten
    while bpm and bpm < 75:
        bpm *= 2
    while bpm > 185:
        bpm /= 2
    schlaege = beats(huelle, bpm)
    # Energie: Tempo (40 %), Dichte starker Anschläge (35 %), Lautheit (25 %)
    sekunden = x[: len(x) // SR * SR].reshape(-1, SR)
    rms = np.sqrt(np.mean(np.square(sekunden), axis=1))
    db = 20 * np.log10(np.maximum(rms, 1e-6))
    dichte = float(np.mean(huelle > 1.5))  # Anteil der Fenster mit deutlichem Anschlag
    laut = float(np.clip((np.median(db) + 40) / 32, 0, 1))  # -40 dBFS -> 0, -8 dBFS -> 1
    tempo_teil = float(np.clip((bpm - 70) / 100, 0, 1))
    energie = 0.40 * tempo_teil + 0.35 * float(np.clip(dichte / 0.12, 0, 1)) + 0.25 * laut
    # Verlauf je Sekunde (0..1): Lautheit + Anschlagdichte – zeigt Aufbau und "Drop"
    je_s = int(FPS)
    stuecke = len(huelle) // je_s
    dichte_s = huelle[: stuecke * je_s].reshape(stuecke, je_s).mean(axis=1) if stuecke else np.zeros(0)
    db_s = db[:stuecke] if len(db) >= stuecke else np.pad(db, (0, stuecke - len(db)), constant_values=-60)
    verlauf = 0.5 * np.clip((db_s + 40) / 32, 0, 1) + 0.5 * np.clip(dichte_s / (dichte_s.max() or 1), 0, 1)
    return {"dauer_s": round(len(x) / SR, 2), "bpm": round(bpm, 1), "energie": round(energie, 2),
            "beats": schlaege, "verlauf": [round(float(v), 2) for v in verlauf]}


def passende_stimmungen(bpm: float, energie: float, ziel: dict | None = None) -> list[str]:
    """Stimmungen, sortiert nach Nähe zu Energie und Tempo."""
    ziel = ziel or ZIEL

    def abstand(s: str) -> float:
        return abs(energie - ziel[s]["energie"]) + abs(bpm - ziel[s]["bpm"]) / 100

    return sorted(ziel, key=abstand)


def _stimmungen(hinweis: str | None, bpm: float, energie: float) -> list[str]:
    gemessen = passende_stimmungen(bpm, energie)
    if hinweis in ZIEL:
        return [hinweis] + [s for s in gemessen if s != hinweis][:1]
    return gemessen[:2]


def stimmung_aus_text(text: str) -> str | None:
    """#episch, #lustig … in einer Bildunterschrift."""
    treffer = re.search(r"#(episch|spannend|lustig|frustriert|chill)\b", text.lower())
    return treffer.group(1) if treffer else None


# --- Bibliothek -----------------------------------------------------------------------

def ordner(konfig: Konfig) -> Path:
    return Path(str(konfig.wert("musik.ordner", "/var/lib/clip-pipeline/musik")))


def sha256(pfad: Path) -> str:
    return hashlib.sha256(pfad.read_bytes()).hexdigest()


def _dateiname(kuenstler: str | None, titel: str, endung: str) -> str:
    roh = f"{kuenstler + ' - ' if kuenstler else ''}{titel}"
    return re.sub(r"[^A-Za-z0-9äöüÄÖÜß._ -]+", "_", roh).strip(" ._")[:90] + endung.lower()


def hinzufuegen(con: sqlite3.Connection, konfig: Konfig, datei: Path, *, titel: str, quelle: str,
                kuenstler: str | None = None, stimmung: str | None = None) -> sqlite3.Row:
    """Kopiert den Titel in den Musik-Ordner (Quelle bleibt), schreibt die Quellenangabe daneben
    (<name>.lizenz.txt, wie highlight.py es erwartet), misst Tempo/Energie und legt ihn in `tracks` an.

    stimmung: Hinweis aus der Quelle (NCS-Mood-Filter oder #episch in der Bildunterschrift) – zählt vor der
    Messung, weil laut gemasterte Titel sich in der gemessenen Energie kaum unterscheiden."""
    if not quelle.strip():
        raise ValueError("Ohne Quellenangabe keine Musik (Lizenz!)")
    if datei.suffix.lower() not in ENDUNGEN:
        raise ValueError(f"Kein Audioformat: {datei.suffix}")
    ziel_ordner = ordner(konfig)
    ziel_ordner.mkdir(parents=True, exist_ok=True)
    pruefsumme = sha256(datei)
    if vorhanden := con.execute("SELECT * FROM tracks WHERE sha256 = ?", (pruefsumme,)).fetchone():
        return vorhanden
    ziel = ziel_ordner / _dateiname(kuenstler, titel, datei.suffix)
    if datei.resolve() != ziel.resolve():
        shutil.copy2(datei, ziel)
    ziel.with_name(ziel.stem + ".lizenz.txt").write_text(quelle.strip() + "\n", encoding="utf-8")
    a = analysiere(ziel)
    con.execute(
        """INSERT INTO tracks (datei, titel, kuenstler, quelle, sha256, dauer_s, bpm, energie, beats, verlauf,
                               stimmungen, erstellt)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (datei) DO UPDATE SET titel = excluded.titel, kuenstler = excluded.kuenstler,
               quelle = excluded.quelle, sha256 = excluded.sha256, dauer_s = excluded.dauer_s, bpm = excluded.bpm,
               energie = excluded.energie, beats = excluded.beats, verlauf = excluded.verlauf,
               stimmungen = excluded.stimmungen""",
        (ziel.name, titel, kuenstler, quelle.strip(), pruefsumme, a["dauer_s"], a["bpm"], a["energie"],
         json.dumps(a["beats"]), json.dumps(a["verlauf"]),
         json.dumps(_stimmungen(stimmung, a["bpm"], a["energie"])), iso(jetzt())),
    )
    log.info("Musik %s: %s BPM, Energie %s", ziel.name, a["bpm"], a["energie"])
    return con.execute("SELECT * FROM tracks WHERE datei = ?", (ziel.name,)).fetchone()


def aus_bildunterschrift(text: str, dateiname: str) -> tuple[str, str | None, str]:
    """Lern-Bot: Bildunterschrift = Quellenangabe. Titel/Künstler aus "Song: Künstler - Titel" oder dem Dateinamen."""
    quelle = re.sub(r"#(episch|spannend|lustig|frustriert|chill)\b", "", text, flags=re.I).strip()
    treffer = re.search(r"Song:\s*(.+?)\s+-\s+(.+?)(?:\s*\[|\n|$)", quelle)
    if treffer:
        return treffer.group(2).strip(), treffer.group(1).strip(), quelle
    stamm = Path(dateiname).stem
    if " - " in stamm:
        kuenstler, titel = stamm.split(" - ", 1)
        return titel.strip(), kuenstler.strip(), quelle
    return stamm, None, quelle


# --- NCS ------------------------------------------------------------------------------

NCS = "https://ncs.io"
_ZEILE = re.compile(r'data-url="(?P<url>https://ncsmusic\.s3[^"]+\.mp3)".*?data-artistraw="(?P<kuenstler>[^"]*)"'
                    r'.*?data-track="(?P<titel>[^"]*)".*?<a href="/(?P<slug>[A-Za-z0-9_-]+)">', re.S)
_QUELLE = re.compile(r'<p class="p-copy" id="panel-copy2">(.*?)</p>', re.S)


def _hole(url: str, timeout: float = 30) -> bytes:
    anfrage = urllib.request.Request(url, headers={"User-Agent": "clip-pipeline/1.0 (privat)"})
    with urllib.request.urlopen(anfrage, timeout=timeout) as antwort:
        return antwort.read()


def ncs_suche(stimmung_id: int) -> list[dict]:
    seite = _hole(f"{NCS}/music-search?q=&genre=&mood={int(stimmung_id)}").decode("utf-8", "replace")
    return [{k: html.unescape(v) for k, v in t.groupdict().items()} for t in _ZEILE.finditer(seite)]


def ncs_quelle(slug: str, kuenstler: str, titel: str) -> str:
    """Quellenangabe von der NCS-Titelseite – so will NCS sie in der Videobeschreibung sehen."""
    try:
        seite = _hole(f"{NCS}/{slug}").decode("utf-8", "replace")
        if treffer := _QUELLE.search(seite):
            text = html.unescape(re.sub(r"<br\s*/?>", "\n", treffer.group(1)))
            zeilen = [z.strip() for z in text.splitlines() if z.strip()]
            if not any("NoCopyrightSounds" in z for z in zeilen):
                zeilen.insert(1, "Music provided by NoCopyrightSounds")
            return "\n".join(zeilen)
    except OSError as e:
        log.warning("NCS-Seite %s: %s", slug, e)
    return (f"Song: {kuenstler} - {titel}\nMusic provided by NoCopyrightSounds\n"
            f"Free Download/Stream: http://ncs.io/{slug}")


def ncs_laden(con: sqlite3.Connection, konfig: Konfig, stimmung: str, anzahl: int = 3) -> list[sqlite3.Row]:
    """Lädt bis zu `anzahl` neue NCS-Titel passend zur Stimmung (NCS-Mood-Filter aus der Konfig)."""
    ids = konfig.wert(f"musik.ncs_stimmungen.{stimmung}")
    if not ids:
        raise ValueError(f"Keine NCS-Stimmungen für {stimmung!r} in [musik.ncs_stimmungen]")
    vorhanden = {(z["kuenstler"], z["titel"]) for z in con.execute("SELECT kuenstler, titel FROM tracks")}
    neu: list[sqlite3.Row] = []
    tmp = ordner(konfig) / ".laden"
    tmp.mkdir(parents=True, exist_ok=True)
    for sid in ids:
        for t in ncs_suche(int(sid)):
            if len(neu) >= anzahl:
                return neu
            if (t["kuenstler"], t["titel"]) in vorhanden:
                continue
            datei = tmp / f"{t['slug']}.mp3"
            datei.write_bytes(_hole(t["url"], timeout=120))
            try:
                neu.append(hinzufuegen(con, konfig, datei, titel=t["titel"], kuenstler=t["kuenstler"],
                                       quelle=ncs_quelle(t["slug"], t["kuenstler"], t["titel"]), stimmung=stimmung))
            finally:
                datei.unlink(missing_ok=True)
            vorhanden.add((t["kuenstler"], t["titel"]))
    return neu
