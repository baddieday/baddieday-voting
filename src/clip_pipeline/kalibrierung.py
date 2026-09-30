"""`pipeline kalibrieren --session ID` und `/kalibrieren` im Lern-Bot (Queue-Punkt B5, erste Stufe, 30.09.).

Wortlisten, Schwellen, Stimmungsregeln und [merkmale.waffen] sind bisher ungeprüfte Annahmen (ANALYSE-2026-09-26 §2,
Punkt 4). Hier läuft EIN echtes Match durch: fehlende Stimmung der Clips nachziehen (Whisper, ohne Claude), dann je
Clip Kills mit Waffen-Nummer (GunType-Zahl) und Kategorie, Merkmale, Stimmung mit Sicherheit, Transkript-Anfang und
drei Standbilder (20/50/80 %). Alles landet in sessions/<ID>/kalibrierung/ (bericht.json + Bilder) – der Lern-Bot
schickt es als Album aufs Handy, damit du siehst, ob Merkmale und Stimmung zur Szene passen, und welche
Waffen-Nummern noch in [merkmale.waffen] fehlen. Bestätigen/Korrigieren per Knopf ist die nächste Stufe.

Weckt pve-big nie (nur Puffer: sessions/<ID>/replay.json, Clips im Puffer); löscht nichts (Bilder werden überschrieben).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

from . import medien, merkmale, stimmung
from .konfig import Konfig
from .verarbeitung import SESSION_ID
from .zeit import aus_iso, utc_zu_lokal

log = logging.getLogger("pipeline")
ANTEILE = (0.2, 0.5, 0.8)       # Standbilder bei 20/50/80 % des Clips
TEXT_MAX = 300                 # Transkript-Anfang je Clip


class KalibrierFehler(RuntimeError):
    pass


def _clip_datei(konfig: Konfig, z: sqlite3.Row) -> Path | None:
    for spalte in ("clip_pfad", "vorschau_pfad"):
        if z[spalte] and (pfad := konfig.absolut(z[spalte])).is_file():
            return pfad
    return None


def _standbilder(datei: Path, ordner: Path, nr: int) -> list[str]:
    dauer = medien.probe(datei).dauer_s
    bilder = []
    for k, anteil in enumerate(ANTEILE, 1):
        ziel = ordner / f"clip{nr:02d}_{k}.jpg"
        medien.fuehre_aus(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{dauer * anteil:.2f}",
                           "-i", str(datei), "-frames:v", "1", "-vf", "scale=640:-2", "-q:v", "4", str(ziel)],
                          f"Standbild Clip {nr}", timeout=60)
        bilder.append(str(ziel))
    return bilder


def bericht(con: sqlite3.Connection, konfig: Konfig, sid: str, *, bilder: bool = True, whisper: bool = True) -> dict:
    """Kalibrier-Bericht für ein Match. KalibrierFehler bei ungültiger oder unbekannter Session oder ohne Clips."""
    if not SESSION_ID.fullmatch(sid or ""):
        raise KalibrierFehler(f"Ungültige Session-ID {sid!r}")
    if con.execute("SELECT 1 FROM matches WHERE id = ?", (sid,)).fetchone() is None:
        raise KalibrierFehler(f"Match {sid} kenne ich nicht")
    clips = con.execute("SELECT * FROM clips WHERE match_id = ? ORDER BY nr", (sid,)).fetchall()
    if not clips:
        raise KalibrierFehler(f"Match {sid} hat keine Clips")
    offen = [c["id"] for c in clips if con.execute("SELECT 1 FROM momente WHERE clip_id = ?", (c["id"],)).fetchone() is None]
    hinweise = []
    if offen:
        try:  # ohne Claude: zählt nicht gegen das Abo; Whisper nur, wenn installiert
            stimmung.analysiere(con, konfig, claude=False, whisper=whisper, nur_clips=offen, maximal=len(offen))
        except Exception as fehler:  # der Bericht ist wichtiger als die Nachmessung
            log.exception("Stimmung beim Kalibrieren")
            hinweise.append(f"Stimmung nachziehen fehlgeschlagen: {type(fehler).__name__}: {str(fehler)[:80]}")
    match = merkmale._match_aus_puffer(konfig, sid)
    if match is None:
        hinweise.append("sessions/<ID>/replay.json fehlt oder ist unlesbar – Kills ohne Waffen-Nummern")
    zone = konfig.wert("zeit.zeitzone", "Europe/Berlin")
    ordner = konfig.ordner("sessions") / sid / "kalibrierung"
    ordner.mkdir(parents=True, exist_ok=True)
    eintraege = []
    for c in clips:
        start, ende = aus_iso(c["start_utc"]), aus_iso(c["ende_utc"])
        kills = [{"zeit": f"{utc_zu_lokal(e.zeit_utc, zone):%H:%M:%S}", "waffe": e.waffe,
                  "kategorie": merkmale.waffen_kategorie(e.waffe, konfig), "bot": e.opfer_bot}
                 for e in (match.ereignisse if match else []) if e.art == "kill" and start <= e.zeit_utc <= ende]
        moment = con.execute("SELECT stimmung, sicherheit, quelle, text FROM momente WHERE clip_id = ?",
                             (c["id"],)).fetchone()
        datei = _clip_datei(konfig, c)
        standbilder = []
        if bilder and datei is not None:
            try:
                standbilder = _standbilder(datei, ordner, c["nr"])
            except medien.MedienFehler as fehler:
                hinweise.append(f"Clip {c['nr']}: keine Standbilder ({str(fehler)[:80]})")
        eintraege.append({
            "nr": c["nr"], "titel": c["titel"], "punkte": c["punkte"], "status": c["status"], "kills": kills,
            "merkmale": json.loads(c["merkmale"] or "{}"), "begruendung": c["begruendung"],
            "stimmung": moment["stimmung"] if moment else None, "sicherheit": moment["sicherheit"] if moment else None,
            "stimmung_quelle": moment["quelle"] if moment else None,
            "text": ((moment["text"] or "")[:TEXT_MAX] if moment else ""), "bilder": standbilder,
            "datei_fehlt": datei is None,
        })
    ergebnis = {"session": sid, "clips": eintraege, "waffen_unbekannt": merkmale._unbekannte_waffen(konfig, match),
                "hinweise": hinweise, "ordner": str(ordner)}
    (ordner / "bericht.json").write_text(json.dumps(ergebnis, ensure_ascii=False, indent=1), encoding="utf-8")
    return ergebnis


def clip_text(e: dict) -> str:
    """Bildunterschrift je Clip (Telegram erlaubt 1024 Zeichen)."""
    zeilen = [f"#{e['nr']} {e['titel']} · {e['punkte']:g} Punkte · {e['status']}"]
    if e["stimmung"]:
        zeilen.append(f"Stimmung: {e['stimmung']} ({e['sicherheit']:.0%}, {e['stimmung_quelle']}) – passt das?")
    else:
        zeilen.append("Stimmung: noch nicht gemessen")
    for k in e["kills"]:
        bot = {True: "Bot", False: "Spieler", None: "?"}[k["bot"]]
        zeilen.append(f"🔫 {k['zeit']} Waffe {k['waffe'] if k['waffe'] is not None else '?'} ({k['kategorie']}) · {bot}")
    wichtig = {n: w for n, w in e["merkmale"].items() if isinstance(w, (int, float)) and not isinstance(w, bool) and w}
    if wichtig:
        zeilen.append("Merkmale: " + ", ".join(f"{n} {w:g}" for n, w in sorted(wichtig.items())[:12]))
    if e["text"]:
        zeilen.append(f"🎙️ „{e['text']}“")
    if e["datei_fehlt"]:
        zeilen.append("⚠️ Clip-Datei fehlt im Puffer")
    return "\n".join(zeilen)[:1024]


def schluss_text(ergebnis: dict) -> str:
    teile = [f"🧪 Kalibrierung {ergebnis['session']}: {len(ergebnis['clips'])} Clips"]
    if ergebnis["waffen_unbekannt"]:
        teile.append("Waffen-Nummern ohne Kategorie: " + ", ".join(map(str, ergebnis["waffen_unbekannt"]))
                     + " – in config/lokal.toml unter [merkmale.waffen] eintragen (sniper/nahkampf/sonstige).")
    teile += [f"⚠️ {h}" for h in ergebnis["hinweise"]]
    teile.append(f"Bericht: {ergebnis['ordner']}/bericht.json")
    return "\n".join(teile)
