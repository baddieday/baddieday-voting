"""Highlight-Video alle 2 Wochen (`pipeline highlight --id ID --tage 14`).

Auswahl: freigegebene Clips des Zeitraums, die noch in keinem Highlight waren, sortiert nach
Ranglisten-Wert (Elo) und Punkten; das Beste kommt zum Schluss.
Schnitt: alle Clips auf ein Format (1920×1080, feste Bildrate), weiche Übergänge (xfade/acrossfade).
Musik: nur Titel mit Lizenzvermerk (<titel>.lizenz.txt daneben). Sie wird automatisch leiser, wenn im
Spiel etwas los ist ("Ducking" mit sidechaincompress).
"""

from __future__ import annotations

import json
import sqlite3
import zlib
from datetime import datetime, timedelta
from pathlib import Path

from . import db, elo
from .konfig import Konfig
from .medien import MedienFehler, fuehre_aus, probe, vorschau
from .verarbeitung import SessionFehler, pruefe_id
from .zeit import iso, jetzt

MUSIK_ENDUNGEN = {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".flac", ".opus"}


def auswahl(con: sqlite3.Connection, konfig: Konfig, tage: int, heute: datetime | None = None) -> list[sqlite3.Row]:
    grenze = (heute or jetzt()) - timedelta(days=tage)
    platzhalter = ", ".join("?" for _ in db.BEWERTET)
    zeilen = con.execute(
        f"""SELECT * FROM clips WHERE status IN ({platzhalter}) AND highlight_id IS NULL
               AND clip_pfad IS NOT NULL AND start_utc >= ?""",
        (*db.BEWERTET, iso(grenze)),
    ).fetchall()

    def wert(z):
        return (elo.ranglisten_wert(z["elo"], z["elo_rd"]), z["punkte"], -z["id"])

    max_clips = int(konfig.wert("highlight.max_clips", 12))
    max_dauer = float(konfig.wert("highlight.max_dauer_s", 180))
    gewaehlt, summe = [], 0.0
    for z in sorted(zeilen, key=wert, reverse=True):
        dauer = z["quelle_ende_s"] - z["quelle_start_s"]
        if len(gewaehlt) >= max_clips or (gewaehlt and summe + dauer > max_dauer):
            continue
        gewaehlt.append(z)
        summe += dauer
    return sorted(gewaehlt, key=wert)  # Steigerung: das Beste zuletzt


def musik_waehlen(konfig: Konfig, hid: str) -> tuple[Path, str] | None:
    """Nur Titel mit nicht-leerer <titel>.lizenz.txt. Welcher Titel: fest aus der ID abgeleitet."""
    ordner = konfig.ordner("musik")
    if not ordner.is_dir():
        return None
    titel = []
    for datei in sorted(ordner.iterdir()):
        lizenz = datei.with_name(datei.stem + ".lizenz.txt")
        if datei.suffix.lower() in MUSIK_ENDUNGEN and lizenz.is_file() and lizenz.read_text(encoding="utf-8").strip():
            titel.append((datei, lizenz.read_text(encoding="utf-8").strip()))
    if not titel:
        return None
    return titel[zlib.crc32(hid.encode()) % len(titel)]


def filtergraph(clips: list[tuple[float, int]], *, musik: bool, konfig: Konfig) -> tuple[str, float]:
    """clips = [(Dauer, Anzahl Tonspuren), ...]. Musik ist (falls vorhanden) der letzte Eingang."""
    b, h = int(konfig.wert("highlight.breite", 1920)), int(konfig.wert("highlight.hoehe", 1080))
    fps = int(konfig.wert("highlight.fps", 60))
    t = float(konfig.wert("highlight.ueberblendung_s", 0.6))
    teile = []
    for i, (dauer, spuren) in enumerate(clips):
        teile.append(
            f"[{i}:v]scale={b}:{h}:force_original_aspect_ratio=decrease,pad={b}:{h}:(ow-iw)/2:(oh-ih)/2,"
            f"setsar=1,fps={fps},format=yuv420p,settb=AVTB[v{i}]"
        )
        einheit = "aresample=48000,aformat=channel_layouts=stereo"
        if spuren == 0:
            teile.append(f"anullsrc=r=48000:cl=stereo:d={dauer:.3f}[a{i}]")
        elif spuren == 1:
            teile.append(f"[{i}:a:0]{einheit}[a{i}]")
        else:
            eingaenge = "".join(f"[{i}:a:{s}]" for s in range(spuren))
            teile.append(f"{eingaenge}amix=inputs={spuren}:normalize=0,{einheit}[a{i}]")

    if len(clips) == 1:
        teile += ["[v0]null[vout]", "[a0]anull[spiel]"]
    else:
        v, a, offset = "[v0]", "[a0]", 0.0
        for i in range(1, len(clips)):
            offset += clips[i - 1][0] - t
            ziel_v, ziel_a = ("[vout]", "[spiel]") if i == len(clips) - 1 else (f"[x{i}]", f"[y{i}]")
            teile.append(f"{v}[v{i}]xfade=transition=fade:duration={t}:offset={offset:.3f}{ziel_v}")
            teile.append(f"{a}[a{i}]acrossfade=d={t}{ziel_a}")
            v, a = f"[x{i}]", f"[y{i}]"
    gesamt = sum(d for d, _ in clips) - t * (len(clips) - 1)

    if musik:
        m = len(clips)
        pegel = float(konfig.wert("highlight.musik_pegel", 0.35))
        teile += [
            "[spiel]asplit=2[spiel1][schluessel]",
            f"[{m}:a]aresample=48000,aformat=channel_layouts=stereo,volume={pegel},atrim=0:{gesamt:.3f},"
            f"afade=t=in:d=1,afade=t=out:st={max(0.0, gesamt - 2):.3f}:d=2[musik]",
            # Ducking: die Musik wird gedrückt, sobald der Spielton laut wird
            "[musik][schluessel]sidechaincompress=threshold=0.05:ratio=8:attack=20:release=400[leiser]",
            "[spiel1][leiser]amix=inputs=2:normalize=0[aout]",
        ]
    else:
        teile.append("[spiel]anull[aout]")
    return ";".join(teile), gesamt


def entscheide(con: sqlite3.Connection, highlight_id: int, freigeben: bool) -> sqlite3.Row | None:
    """Freigabe im Bot. Verworfen: die Clips werden wieder frei für das nächste Highlight."""
    nach = "freigegeben" if freigeben else "verworfen"
    with db.transaktion(con):
        zeile = con.execute("SELECT * FROM highlights WHERE id = ?", (highlight_id,)).fetchone()
        if zeile is None:
            return None
        geaendert = con.execute(
            "UPDATE highlights SET status = ?, entschieden = ? WHERE id = ? AND status IN ('neu', 'gesendet')",
            (nach, iso(jetzt()), highlight_id),
        ).rowcount
        if geaendert and not freigeben:
            for c in con.execute("SELECT id FROM clips WHERE highlight_id = ?", (zeile["name"],)).fetchall():
                db.status_wechsel(con, c["id"], ("im_highlight",), "veroeffentlicht")
            con.execute("UPDATE clips SET highlight_id = NULL WHERE highlight_id = ?", (zeile["name"],))
    return con.execute("SELECT * FROM highlights WHERE id = ?", (highlight_id,)).fetchone()


def hochgeladen(con: sqlite3.Connection, highlight_id: int) -> sqlite3.Row | None:
    """Häkchen „✅ Hochgeladen“ am freigegebenen Highlight-Video – danach erinnert der Bot nicht mehr daran.
    Nur für freigegebene Videos; ein Doppelklick behält den ersten Zeitpunkt. None = Video nicht gefunden."""
    con.execute("UPDATE highlights SET hochgeladen = COALESCE(hochgeladen, ?) WHERE id = ? AND status = 'freigegeben'",
                (iso(jetzt()), highlight_id))
    return con.execute("SELECT * FROM highlights WHERE id = ?", (highlight_id,)).fetchone()


def _mmss(sekunden: float) -> str:
    s = int(round(sekunden))
    return f"{s // 60:02d}:{s % 60:02d}"


def erstelle(con: sqlite3.Connection, konfig: Konfig, hid: str, tage: int) -> dict:
    pruefe_id(hid)
    if tage < 1:
        raise SessionFehler("--tage muss mindestens 1 sein")
    ordner = konfig.ordner("highlights")
    video, info_datei = ordner / f"{hid}.mp4", ordner / f"{hid}.json"
    if video.is_file() and info_datei.is_file():  # idempotent
        return {**json.loads(info_datei.read_text(encoding="utf-8")), "uebersprungen": True}
    konfig.pruefe_speicher()

    clips = auswahl(con, konfig, tage)
    if not clips:
        return {"id": hid, "clips": 0, "dauer": "00:00", "hinweis": f"keine freigegebenen Clips der letzten {tage} Tage"}
    dateien = [konfig.absolut(c["clip_pfad"]) for c in clips]
    fehlend = [str(d) for d in dateien if not d.is_file()]
    if fehlend:
        raise MedienFehler(f"Clip-Dateien fehlen: {fehlend[:3]}")
    infos = [probe(d) for d in dateien]
    musik = musik_waehlen(konfig, hid)
    graph, gesamt = filtergraph([(i.dauer_s, len(i.tonspuren)) for i in infos], musik=musik is not None, konfig=konfig)

    befehl = ["ffmpeg", "-hide_banner", "-nostdin", "-y"]
    for d in dateien:
        befehl += ["-i", str(d)]
    if musik:
        befehl += ["-stream_loop", "-1", "-i", str(musik[0])]  # Musik bei Bedarf wiederholen
    ordner.mkdir(parents=True, exist_ok=True)
    tmp = ordner / f"{hid}.tmp.mp4"
    befehl += [
        "-filter_complex", graph, "-map", "[vout]", "-map", "[aout]", "-t", f"{gesamt:.3f}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(tmp),
    ]
    fuehre_aus(befehl, f"Highlight {hid}")
    tmp.replace(video)
    # Das volle Video ist für Telegram zu groß (> 50 MB) -> kleine Vorschau zum Freigeben im Bot
    vorschau_datei = ordner / f"{hid}.vorschau.mp4"
    vorschau(video, vorschau_datei, max_bytes=int(float(konfig.wert("vorschau.max_mb", 48)) * 1_000_000),
             kurze_seite=int(konfig.wert("vorschau.kurze_seite", 720)))

    ergebnis = {
        "id": hid, "clips": len(clips), "dauer": _mmss(gesamt), "datei": konfig.relativ(video),
        "musik": musik[0].name if musik else None, "lizenz": musik[1] if musik else None,
        "clip_ids": [c["id"] for c in clips],
    }
    if not musik:
        ergebnis["hinweis"] = "ohne Musik – keine Titel mit .lizenz.txt in musik/"
    with db.transaktion(con):
        for c in clips:
            con.execute("UPDATE clips SET highlight_id = ?, geaendert = ? WHERE id = ?", (hid, iso(jetzt()), c["id"]))
            db.status_wechsel(con, c["id"], ("veroeffentlicht",), "im_highlight")
        con.execute(
            """INSERT OR IGNORE INTO highlights (name, datei, vorschau, clips, dauer, musik, erstellt)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (hid, konfig.relativ(video), konfig.relativ(vorschau_datei), len(clips), ergebnis["dauer"],
             ergebnis["musik"], iso(jetzt())),
        )
        db.protokoll(con, "highlight", f"{hid}: {len(clips)} Clips, {ergebnis['dauer']}")
    info_datei.write_text(json.dumps(ergebnis, ensure_ascii=False, indent=2), encoding="utf-8")
    return ergebnis
