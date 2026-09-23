"""Nachrichtentexte (HTML für Telegram). Reine Funktionen – ohne Telegram testbar."""

from __future__ import annotations

import json
from datetime import date
from html import escape

from ..lernen import Ergebnis
from ..vorbewertung import MERKMAL_NAMEN, MERKMALE, gruppiere, zahl
from ..zeit import aus_iso, utc_zu_lokal

QUELLEN_NAMEN = {
    "nvidia_highlight": "Nvidia Highlight",
    "nvidia_dvr": "Nvidia Videobeweis",
    "nvidia_aufnahme": "Nvidia Aufnahme",
    "steelseries": "SteelSeries",
}
STATUS_ZEILE = {
    "freigegeben": "✅ <b>Freigegeben</b>",
    "verworfen": "🗑️ <b>Verworfen</b>",
    "veroeffentlicht": "📢 <b>Veröffentlicht</b>",
    "im_highlight": "🎞️ <b>Im Highlight-Video</b>",
}


def _quelle(pfad: str) -> str:
    from ..quellen import erkenne

    erkennung = erkenne(pfad.rsplit("/", 1)[-1])
    return QUELLEN_NAMEN.get(erkennung.quelle, "?") if erkennung else "?"


def clip_text(clip, match, zonen_name: str) -> str:
    zeiten = [aus_iso(z) for z in json.loads(clip["kill_zeiten"])]
    serie = max(gruppiere(zeiten, 10.0), key=len)
    sekunden = max(1, round((serie[-1] - serie[0]).total_seconds()))
    kills = f"{clip['kills']} Kill" + ("s" if clip["kills"] != 1 else "")
    if len(serie) > 1:
        kills += f" · {len(serie)} in {sekunden} s"
    zeilen = [
        f"🎬 <b>Clip #{clip['id']} · {escape(clip['titel'])}</b>",
        f"⭐ Vorbewertung: <b>{zahl(clip['punkte'])}</b> Punkte",
        f"🔫 {kills}" + (f" · Platz {match['platzierung']}" if match and match["platzierung"] else ""),
        f"🕒 {utc_zu_lokal(zeiten[0], zonen_name):%d.%m. %H:%M} · {_quelle(clip['quelle_pfad'])}",
        f"<i>{escape(clip['begruendung'])}</i>",
    ]
    if match and match["kill_quelle"] not in (None, "replay"):
        zeilen.append("⚠️ ohne Replay-Daten – Kills geschätzt")
    if zeile := STATUS_ZEILE.get(clip["status"]):
        zeilen.append(zeile)
    return "\n".join(zeilen)


def battle_frage(battle_id: int, a, b) -> str:
    return (
        f"⚔️ <b>Battle #{battle_id}</b> – welcher Clip ist besser?\n"
        f"A: #{a['id']} {escape(a['titel'])} (Elo {round(a['elo'])})\n"
        f"B: #{b['id']} {escape(b['titel'])} (Elo {round(b['elo'])})"
    )


def battle_ergebnis(battle) -> str:
    if battle["ergebnis"] == "s":
        return f"⚔️ Battle #{battle['id']}: übersprungen 🤷"
    gewinner = "A" if battle["ergebnis"] == "a" else "B"
    da = battle["elo_a_nachher"] - battle["elo_a_vorher"]
    db_ = battle["elo_b_nachher"] - battle["elo_b_vorher"]
    return (
        f"⚔️ Battle #{battle['id']}: <b>{gewinner} gewinnt</b>\n"
        f"A #{battle['clip_a']}: {round(battle['elo_a_nachher'])} ({'+' if da >= 0 else ''}{round(da)})\n"
        f"B #{battle['clip_b']}: {round(battle['elo_b_nachher'])} ({'+' if db_ >= 0 else ''}{round(db_)})"
    )


def rangliste_text(zeilen: list, saison: tuple[date, date]) -> str:
    kopf = f"🏆 <b>Rangliste</b> {saison[0]:%d.%m.} – {saison[1]:%d.%m.%Y}"
    if not zeilen:
        return kopf + "\nNoch keine freigegebenen Clips in dieser Saison."
    teile = [kopf]
    for platz, z in enumerate(zeilen, 1):
        teile.append(f"{platz}. #{z['id']} {escape(z['titel'])} – Elo {round(z['elo'])} ({z['battles']} Battles)")
    return "\n".join(teile)


def gewichte_text(e: Ergebnis, version: int) -> str:
    zeilen = [f"{'Merkmal':<15}{'Start':>7}{'Aktuell':>9}{'Δ':>7}"]
    for m in MERKMALE:
        delta = e.werte[m] - e.start[m]
        zeilen.append(
            f"{MERKMAL_NAMEN[m]:<15}{zahl(e.start[m], 2):>7}{zahl(e.werte[m], 2):>9}"
            f"{('+' if delta > 0 else '') + zahl(delta, 2) if abs(delta) > 1e-9 else '0':>7}"
        )
    kopf = [
        f"🧠 <b>Vorbewertung – Gewichte</b> (Version {version})",
        f"Datenbasis: {e.datenbasis} Bewertungen ({e.freigaben} Freigaben/Verwerfungen, {e.battles} Battles)",
        f"Status: {escape(e.grund)} · Vertrauen {round(e.vertrauen * 100)} %",
    ]
    fuss = []
    if e.trefferquote is not None:
        fuss.append(f"Trefferquote: {round(e.trefferquote * 100)} % (Startgewichte: {round((e.trefferquote_start or 0) * 100)} %)")
    return "\n".join(kopf) + "\n<pre>" + escape("\n".join(zeilen)) + "</pre>" + ("\n" + "\n".join(fuss) if fuss else "")


def status_text(anzahl: dict[str, int], speicher: str, letzte: str | None) -> str:
    namen = ["vorbewertet", "gesendet", "freigegeben", "verworfen", "veroeffentlicht", "im_highlight"]
    zeilen = ["📊 <b>Status</b>", f"Speicher: {escape(speicher)}"]
    zeilen += [f"{n}: {anzahl.get(n, 0)}" for n in namen]
    if letzte:
        zeilen.append(f"Letztes Match: {escape(letzte)}")
    return "\n".join(zeilen)


def highlight_text(h) -> str:
    zeilen = [
        f"🏆 <b>Highlight-Video {escape(h['name'])}</b>",
        f"{h['clips']} Clips · {h['dauer']} · Musik: {escape(h['musik']) if h['musik'] else 'keine'}",
        "(Vorschau in kleiner Auflösung – das volle Video liegt auf dem Speicher)",
    ]
    if h["status"] == "freigegeben":
        zeilen.append(f"✅ <b>Freigegeben</b> – zum Hochladen: <code>{escape(h['datei'])}</code>")
    elif h["status"] == "verworfen":
        zeilen.append("🗑️ <b>Verworfen</b> – die Clips sind wieder frei für das nächste Highlight")
    return "\n".join(zeilen)


def upload_text(clip, stand: dict[str, bool]) -> str:
    from .aktionen import PLATTFORM_NAMEN

    zeilen = [f"📦 <b>Upload-Checkliste Clip #{clip['id']}</b> · {escape(clip['titel'])}"]
    zeilen += [f"{'✅' if fertig else '⬜'} {PLATTFORM_NAMEN.get(p, p)}" for p, fertig in stand.items()]
    if not all(stand.values()):
        zeilen.append(
            "\n1. Datei oben speichern · 2. als Short auf YouTube und bei TikTok hochladen (Caption kopieren)"
            "\n3. In der YouTube-App: Teilen → Clip Battle · 4. hier abhaken"
        )
    return "\n".join(zeilen)


def offene_uploads_text(eintraege: list) -> str:
    from .aktionen import PLATTFORM_NAMEN

    if not eintraege:
        return "📦 Alle freigegebenen Clips sind überall hochgeladen 👍"
    zeilen = ["📦 <b>Noch nicht überall hochgeladen:</b>"]
    for clip, fehlt in eintraege:
        zeilen.append(f"#{clip['id']} {escape(clip['titel'])} – fehlt: {', '.join(PLATTFORM_NAMEN.get(p, p) for p in fehlt)}")
    zeilen.append("Paket erneut holen: /paket &lt;nummer&gt;")
    return "\n".join(zeilen)


HILFE = (
    "🎮 <b>Clip-Bot</b>\n"
    "Neue Clips kommen automatisch – mit ✅ Freigeben / 🗑️ Verwerfen entscheidest du.\n\n"
    "/battle – zwei freigegebene Clips, du wählst den besseren (Elo)\n"
    "/rangliste – Top 10 der aktuellen Saison\n"
    "/gewichte – was die Vorbewertung gelernt hat\n"
    "/offen – unentschiedene Clips erneut zeigen\n"
    "/uploads – freigegebene Clips, die noch auf YouTube/TikTok fehlen\n"
    "/paket &lt;nummer&gt; – Upload-Paket (Short + Caption) für einen Clip\n"
    "/link &lt;nummer&gt; &lt;url&gt; – YouTube-/TikTok-Link eintragen (hakt die Plattform ab)\n"
    "/status – Überblick"
)
