"""Highlight-Video alle 2 Wochen (`pipeline highlight --id ID --tage 14`).

Neue Ausgaben laufen über den gemeinsamen Regisseur und seine autonome Parameterwahl.
Der n8n-Vertrag und bereits erzeugte Dateien bleiben erhalten. Die früheren Auswahl-/Filter-Helfer
bleiben für vorhandene Aufrufer verfügbar; sie werden für neue Highlights nicht mehr verwendet.
"""

from __future__ import annotations

import json
import sqlite3
import zlib
from datetime import datetime, timedelta
from pathlib import Path

from . import db, elo
from .konfig import Konfig
from .medien import probe, vorschau
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


def zu_entwurf(con: sqlite3.Connection, entwurf_id: int) -> sqlite3.Row | None:
    """Das Highlight-Video (2-Wochen-Video), das zu diesem Entwurf des Lern-Bots gehört – None, wenn keins."""
    return con.execute("SELECT * FROM highlights WHERE entwurf_id = ? ORDER BY id DESC LIMIT 1",
                       (entwurf_id,)).fetchone()


def entscheide_entwurf(con: sqlite3.Connection, entwurf_id: int, freigeben: bool) -> sqlite3.Row | None:
    """Dein ✅/❌ am 2-Wochen-Video im Lern-Bot (einfacher Modus, Stufe 3, 08.10. – der Clip-Bot zeigt es dort nicht
    mehr): ✅ = freigegeben UND hochgeladen (keine Erinnerung), ❌ = verworfen (die Clips sind wieder frei). Wie im
    Clip-Bot gilt die erste Entscheidung. None = der Entwurf gehört zu keinem Highlight-Video."""
    h = zu_entwurf(con, entwurf_id)
    if h is None:
        return None
    entscheide(con, h["id"], freigeben)
    return hochgeladen(con, h["id"]) if freigeben else zu_entwurf(con, entwurf_id)


def upload_fassung(con: sqlite3.Connection, konfig: Konfig, entwurf_id: int) -> dict | None:
    """Telegram-Fassung des 2-Wochen-Videos (Stufe 3, 08.10.): die fertige Datei (crf 20) auf [vorschau].max_mb
    verkleinert, 1080 Pixel kurze Seite – statt die Szenen neu zu schneiden wie entwurf.upload_fassung. Deren
    Rohvideos gibt der Puffer nach 14 Tagen frei (Stufe 2), und das 2-Wochen-Video reicht 14 Tage zurück: Ein ✅ ein,
    zwei Tage später endete sonst mit „Moment-Datei fehlt“. Idempotent über entwuerfe.upload_pfad, Ziel wie
    entwurf.upload_ziel. None = kein 2-Wochen-Video, nicht im getrennten Betrieb oder die fertige Datei fehlt – dann
    gilt der normale Weg. Die Pipeline-Sperre holt der Aufrufer."""
    from . import entwurf  # hier, nicht oben: wie in erstelle

    h = zu_entwurf(con, entwurf_id)
    if h is None or not konfig.getrennt:
        return None
    konfig.pruefe_getrennt(mit_lager=False)   # nur in den Puffer schreiben, nie ins Lager auf pve-big
    voll = konfig.absolut(h["datei"])
    if not voll.is_file():
        return None
    zeile = con.execute("SELECT * FROM entwuerfe WHERE id = ?", (entwurf_id,)).fetchone()
    if zeile["upload_pfad"] and Path(zeile["upload_pfad"]).is_file():
        return {"entwurf": entwurf_id, "datei": zeile["upload_pfad"], "uebersprungen": True}
    ziel = entwurf.upload_ziel(konfig, zeile)
    vorschau(voll, ziel, max_bytes=int(float(konfig.wert("vorschau.max_mb", 48)) * 1_000_000), kurze_seite=1080)
    con.execute("UPDATE entwuerfe SET upload_pfad = ? WHERE id = ?", (str(ziel), entwurf_id))
    return {"entwurf": entwurf_id, "datei": str(ziel), "uebersprungen": False}


def _mmss(sekunden: float) -> str:
    s = int(round(sekunden))
    return f"{s // 60:02d}:{s % 60:02d}"


def erstelle(con: sqlite3.Connection, konfig: Konfig, hid: str, tage: int) -> dict:
    """n8n-Vertrag, gemeinsamer autonomer Regisseur. Bestehende Exporte bleiben erhalten."""
    from . import entwurf, regie, regie_lernen, stimmung

    pruefe_id(hid)
    if tage < 1:
        raise SessionFehler("--tage muss mindestens 1 sein")
    ordner = konfig.ordner("highlights")
    video, info_datei = ordner / f"{hid}.mp4", ordner / f"{hid}.json"
    if video.is_file() and info_datei.is_file():  # idempotent
        # Bestehende Dateien nie überschreiben; unzulässige alte Längen brauchen eine neue ID.
        regie.pruefe_dauer("zusammenschnitt", probe(video).dauer_s)
        return {**json.loads(info_datei.read_text(encoding="utf-8")), "uebersprungen": True}
    konfig.pruefe_speicher()
    # Ein Urteil ist keine Eintrittskarte mehr. Bewusst (von dir) verworfene Clips bleiben ausgeschlossen, automatisch
    # aussortierte bleiben drin (30.09., db.hart_verworfen_sql).
    clips = con.execute(f"SELECT id,match_id FROM clips WHERE NOT {db.hart_verworfen_sql()} AND clip_pfad IS NOT NULL "
                        "AND start_utc >= ?", (iso(jetzt()-timedelta(days=tage)),)).fetchall()
    if not clips:
        return {"id": hid, "clips": 0, "dauer": "00:00", "hinweis": f"keine Clips der letzten {tage} Tage"}
    name = f"highlight-{hid}"
    zeile = con.execute("SELECT * FROM entwuerfe WHERE name=?", (name,)).fetchone()
    if zeile is None:
        stimmung.analysiere(con, konfig, nur_clips=[c["id"] for c in clips], claude=False, whisper=False)
        parameter, ziel = regie_lernen.aktuelle(con, konfig, "zusammenschnitt")
        e = regie.erstelle(con, konfig, "zusammenschnitt", name=name, parameter=parameter, ziel=ziel,
                           nur_matches={c["match_id"] for c in clips})
        zeile = con.execute("SELECT * FROM entwuerfe WHERE id=?", (e["entwurf"],)).fetchone()
    liste = json.loads(Path(zeile["schnittliste"]).read_text(encoding="utf-8"))
    regie.pruefe_dauer("zusammenschnitt", liste["dauer_s"])
    ordner.mkdir(parents=True, exist_ok=True)
    tmp = ordner / f"{hid}.tmp.mp4"
    # Derselbe Renderer und dieselben harten Grenzen wie bei jedem vollständigen Entwurf.
    entwurf.rendere(liste, tmp, konfig, vollstaendig=True, volle_aufloesung=True, crf=20,
                    max_bytes=2_000_000_000, kbit_max=12000)
    tmp.replace(video)
    vorschau_datei = ordner / f"{hid}.vorschau.mp4"
    vorschau(video, vorschau_datei, max_bytes=int(float(konfig.wert("vorschau.max_mb", 48)) * 1_000_000),
             kurze_seite=int(konfig.wert("vorschau.kurze_seite", 720)))

    clip_ids = list(dict.fromkeys(s["clip_id"] for s in liste["segmente"] if s.get("clip_id") is not None))
    m = liste.get("musik")
    ergebnis = {"id": hid, "entwurf": zeile["id"], "clips": len(clip_ids), "dauer": _mmss(liste["dauer_s"]),
                "datei": konfig.relativ(video), "musik": m["titel"] if m else None,
                "lizenz": m["quelle"] if m else None, "clip_ids": clip_ids,
                "hinweis": f"Publikumslernen: im Lern-Bot /link {zeile['id']} <Video-URL> schicken."}
    with db.transaktion(con):
        for cid in clip_ids:
            con.execute("UPDATE clips SET highlight_id = ?, geaendert = ? WHERE id = ?", (hid, iso(jetzt()), cid))
            db.status_wechsel(con, cid, ("veroeffentlicht",), "im_highlight")
        con.execute("UPDATE entwuerfe SET datei=?,status='gerendert' WHERE id=? AND status='neu'",
                    (str(vorschau_datei), zeile["id"]))
        con.execute(
            """INSERT OR IGNORE INTO highlights (name, datei, vorschau, clips, dauer, musik, erstellt, entwurf_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (hid, konfig.relativ(video), konfig.relativ(vorschau_datei), len(clip_ids), ergebnis["dauer"],
             ergebnis["musik"], iso(jetzt()), zeile["id"]),
        )
        db.protokoll(con, "highlight", f"{hid}: {len(clip_ids)} Clips, {ergebnis['dauer']}, Entwurf {zeile['id']}")
    info_datei.write_text(json.dumps(ergebnis, ensure_ascii=False, indent=2), encoding="utf-8")
    return ergebnis
