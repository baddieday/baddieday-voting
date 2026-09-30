"""Nachrichtentexte (HTML für Telegram). Reine Funktionen – ohne Telegram testbar."""

from __future__ import annotations

import json
from datetime import date
from html import escape

from ..auto_freigabe import war_automatisch
from ..erwartung import anzeige
from ..lernen import Ergebnis, anzeige_zeilen, datenbasis_text
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


def _feld(zeile, name: str):
    """Spalte lesen, wenn es sie gibt (alte Zeilen, dicts aus Tests)."""
    return zeile[name] if name in zeile.keys() else None


def _status_zeile(clip) -> str | None:
    """Status-Zeile mit Herkunft der Entscheidung (Auto-Freigabe 30.09.):
    „✅ <b>Freigegeben</b> · 🤖 automatisch – Erwartung 91 % ≥ Stufe 85 % (27/29 richtig)“,
    „🗑️ <b>Aussortiert</b> · 🤖 automatisch – … · bleibt für Shorts und Highlight nutzbar“,
    nach deinem Tipp „… · von dir bestätigt“ bzw. „… · von dir (Automatik korrigiert)“."""
    status = clip["status"]
    zeile = STATUS_ZEILE.get(status)
    if zeile is None:
        return None
    quelle = _feld(clip, "freigabe_quelle")
    if quelle == "auto" and status in ("freigegeben", "verworfen"):
        if status == "verworfen":
            zeile = "🗑️ <b>Aussortiert</b>"
        zeile += f" · 🤖 automatisch – {escape(str(_feld(clip, 'auto_grund') or ''))}"
        if status == "verworfen":
            zeile += " · bleibt für Shorts und Highlight nutzbar"
    elif quelle == "du" and status in ("freigegeben", "verworfen") and war_automatisch(clip):
        zeile += " · von dir bestätigt" if status == _feld(clip, "auto_vorschlag") else " · von dir (Automatik korrigiert)"
    return zeile


def _quelle(pfad: str) -> str:
    from ..quellen import erkenne

    erkennung = erkenne(pfad.rsplit("/", 1)[-1])
    return QUELLEN_NAMEN.get(erkennung.quelle, "?") if erkennung else "?"


def clip_text(clip, match, zonen_name: str, erwartung: float | None = None, auto_zeile: str | None = None) -> str:
    """Bildunterschrift eines Clips im Clip-Bot (HTML), höchstens 1024 Zeichen (Telegram-Grenze für Videos).

    erwartung: festgeschriebene Wahrscheinlichkeit (erwartung.gespeichert) oder None. Die Zeile steht unter den
    Punkten: „Erwartung: ✅ 78 %“, „Erwartung: 🗑️ 65 %“ (Wahrscheinlichkeit 0,35) bzw. „Erwartung: noch keine“
    (Spec §10.6, Format in erwartung.anzeige). Wird der Text zu lang – etwa mit allen 17 Merkmalen in der
    Begründung –, wird nur die Begründung gekürzt (mit „…“); Titel, Punkte, Erwartung und Status bleiben ganz.
    Gekürzt wird der Klartext VOR dem Maskieren, damit nie ein HTML-Zeichen wie &amp; zerschnitten wird.
    Beispiel: Begründung mit 3000 Zeichen → Text genau bis 1024 Zeichen, Begründung endet auf „…“.
    auto_zeile: Klartext-Zusatz der Auto-Freigabe für offene Clips (auto_freigabe.hinweis_zeile: ⏰/🎲/👀), steht
    unter der Status-Zeile; die 🤖-Zeile entschiedener Clips hängt an der Status-Zeile selbst (_status_zeile)."""
    caption_max = 1024  # Telegram: Bildunterschrift eines Videos höchstens 1024 Zeichen
    zeiten = [aus_iso(z) for z in json.loads(clip["kill_zeiten"])]
    serie = max(gruppiere(zeiten, 10.0), key=len)
    sekunden = max(1, round((serie[-1] - serie[0]).total_seconds()))
    kills = f"{clip['kills']} Kill" + ("s" if clip["kills"] != 1 else "")
    if len(serie) > 1:
        kills += f" · {len(serie)} in {sekunden} s"
    vorher = [
        f"🎬 <b>Clip #{clip['id']} · {escape(clip['titel'])}</b>",
        f"⭐ Vorbewertung: <b>{zahl(clip['punkte'])}</b> Punkte",
        f"🔮 {anzeige(erwartung, ja='✅', nein='🗑️')}",
        f"🔫 {kills}" + (f" · Platz {match['platzierung']}" if match and match["platzierung"] else ""),
        f"🕒 {utc_zu_lokal(zeiten[0], zonen_name):%d.%m. %H:%M} · {_quelle(clip['quelle_pfad'])}",
    ]
    nachher = []
    if match and match["kill_quelle"] not in (None, "replay"):
        nachher.append("⚠️ ohne Replay-Daten – Kills geschätzt")
    if zeile := _status_zeile(clip):
        nachher.append(zeile)
    if auto_zeile:
        nachher.append(escape(auto_zeile))
    # Platz für die Begründung = Grenze minus alles andere (Zeilenumbrüche und <i></i> mitgezählt)
    platz = caption_max - len("\n".join(vorher + ["<i></i>"] + nachher))
    begruendung = clip["begruendung"]
    if len(escape(begruendung)) > platz:
        begruendung = begruendung[:platz]  # maskiert wird es höchstens länger, nie kürzer
        while begruendung and len(escape(begruendung)) + 1 > platz:  # + 1 für „…“
            begruendung = begruendung[:-1]
        begruendung = escape(begruendung.rstrip()) + "…"
    else:
        begruendung = escape(begruendung)
    return "\n".join(vorher + [f"<i>{begruendung}</i>"] + nachher)


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
    """/gewichte im Clip-Bot (Spec §8.3): Tabelle der 17 Gewichte (Start, aktuell, Δ), darunter beide
    Sortier-Quoten (du · Publikum), die Paare je Quelle, die Clips ohne Mic-Analyse und – wenn ihr auseinander
    liegt – „Du magst X, das Publikum Y“. Die Zeilen unter der Tabelle kommen aus lernen.anzeige_zeilen (eine
    Stelle für Bot und CLI). Die Erwartungs-Zeile hängt bot/app.cmd_gewichte an.
    Beispiel-Fuß: „Sortier-Quote Publikum: 64 % (Start 50 %) – 3 Paare, zählt für die Schranke erst ab 10“."""
    zeilen = [f"{'Merkmal':<15}{'Start':>7}{'Aktuell':>9}{'Δ':>7}"]
    for m in MERKMALE:
        delta = e.werte[m] - e.start[m]
        zeilen.append(
            f"{MERKMAL_NAMEN[m]:<15}{zahl(e.start[m], 2):>7}{zahl(e.werte[m], 2):>9}"
            f"{('+' if delta > 0 else '') + zahl(delta, 2) if abs(delta) > 1e-9 else '0':>7}"
        )
    kopf = [
        f"🧠 <b>Vorbewertung – Gewichte</b> (Version {version})",
        escape(datenbasis_text(e)),
        f"Status: {escape(e.grund)} · Vertrauen {round(e.vertrauen * 100)} %",
    ]
    fuss = [escape(z) for z in anzeige_zeilen(e)]
    return "\n".join(kopf) + "\n<pre>" + escape("\n".join(zeilen)) + "</pre>\n" + "\n".join(fuss)


def status_text(anzahl: dict[str, int], speicher: str, letzte: str | None, lager: str | None = None,
                auto: dict[str, int] | None = None) -> str:
    """lager: Zeile aus lager.status (nur im getrennten Betrieb), z. B. „Puffer 61 GB frei · Lager: … · 0 offen“.
    auto: db.anzahl_auto – „🤖 davon automatisch: ✅ n · 🗑️ m“ (nur wenn es welche gibt)."""
    namen = ["vorbewertet", "gesendet", "freigegeben", "verworfen", "veroeffentlicht", "im_highlight"]
    zeilen = ["📊 <b>Status</b>", f"Speicher: {escape(speicher)}"]
    if lager:
        zeilen.append(f"🗄️ {escape(lager)}")
    zeilen += [f"{n}: {anzahl.get(n, 0)}" for n in namen]
    if auto and (auto.get("frei") or auto.get("weg")):
        zeilen.append(f"🤖 davon automatisch: ✅ {auto.get('frei', 0)} · 🗑️ {auto.get('weg', 0)}")
    if letzte:
        zeilen.append(f"Letztes Match: {escape(letzte)}")
    return "\n".join(zeilen)


def highlight_text(h) -> str:
    zeilen = [
        f"🏆 <b>Highlight-Video {escape(h['name'])}</b>",
        f"{h['clips']} Clips · {h['dauer']} · Musik: {escape(h['musik']) if h['musik'] else 'keine'}",
        "(Vorschau in kleiner Auflösung – das volle Video liegt auf dem Speicher)",
    ]
    if h["status"] == "freigegeben" and h["hochgeladen"]:
        zeilen.append("✅ <b>Hochgeladen</b>")
    elif h["status"] == "freigegeben":
        zeilen.append(f"✅ <b>Freigegeben</b> – zum Hochladen: <code>{escape(h['datei'])}</code>")
        zeilen.append("Danach „✅ Hochgeladen“ tippen, dann erinnere ich nicht mehr daran.")
    elif h["status"] == "verworfen":
        zeilen.append("🗑️ <b>Verworfen</b> – die Clips sind wieder frei für das nächste Highlight")
    if "entwurf_id" in h.keys() and h["entwurf_id"]:
        zeilen.append(f"🧠 Autonomes Lernen: Im Lern-Bot <code>/link {h['entwurf_id']} &lt;Video-URL&gt;</code> "
                      "schicken. Dort gibt es auch das Upload-Paket ohne Bewertung.")
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


def offene_uploads_text(videos: list) -> str:
    """Freigegebene Highlight-Videos ohne Häkchen (aktionen.offene_highlight_videos); einzelne Momente nie."""
    if not videos:
        return "📦 Kein Highlight-Video wartet aufs Hochladen 👍"
    zeilen = ["📦 <b>Noch nicht hochgeladen:</b>"]
    for h in videos:
        zeilen.append(f"🏆 Highlight-Video {escape(h['name'])} ({escape(h['dauer'])}) – am Video „✅ Hochgeladen“ tippen")
    return "\n".join(zeilen)


def auto_text(u: dict) -> str:
    """/auto (HTML): Modus, aktive Stufen je Band, letzte 7 Tage, Korrekturen, Stichproben, Lerndaten
    (u = auto_freigabe.ueberblick)."""
    herkunft = {"bot": " 📱", "datei": " 🗂"}.get(u["herkunft"], "")
    modus = {"an": "an", "probe": "👀 Probelauf (nur anzeigen)", "aus": "aus"}[u["modus"]]
    stand = u["stand"]
    zeilen = [f"🤖 <b>Auto-Freigabe</b>: {modus}{herkunft} · Ziel {round(100 * u['ziel_quote'])} %"]
    aktiv = []
    if stand["frei_ab"] is not None:
        aktiv.append(f"✅ ab {round(100 * stand['frei_ab'])} %")
    if stand["weg_bis"] is not None:
        aktiv.append(f"🗑️ bis {round(100 * stand['weg_bis'])} %")
    if aktiv:
        zeilen.append("Aktiv: " + " · ".join(aktiv))
    else:
        frei = stand["zeilen"]["frei"]
        fehlt = max(0, u["mindest_n"] - frei[-1]["n"]) if frei else u["mindest_n"]
        unterste = frei[-1]["stufe"] if frei else 0.8
        zeilen.append(f"Aktiv: noch keine – {fehlt} Urteile ≥ {round(100 * unterste)} % fehlen" if fehlt
                      else "Aktiv: noch keine – die Erwartung traf noch nicht oft genug")
    for name, zeichen, vergleich in (("frei", "✅", "≥"), ("weg", "🗑️", "≤")):
        for z in stand["zeilen"][name]:
            if z["urteil"] == "wenig":
                wert = f"zu wenig ({z['n']}/{u['mindest_n']})"
            else:
                wert = f"{z['treffer']}/{z['n']} ({round(100 * z['treffer'] / z['n'])} %) " + (
                    "✓" if z["urteil"] == "ok" else "✗")
            zeilen.append(f"  {zeichen} {vergleich} {round(100 * z['stufe'])} %: {wert}")
    w = u["woche"]
    zeilen.append(f"Letzte 7 Tage: ✅ sofort {w['sofort_frei']} · 🗑️ sofort {w['sofort_weg']} · ⏰ per Frist "
                  f"{w['frist']} · 🙋 an dich {w['an_dich']}, davon beantwortet {w['beantwortet']}")
    zeilen.append(f"Korrekturen: {u['umgedreht']} von {u['automatisch']} umgedreht · Stichproben: beantwortet "
                  f"{u['stichproben']} · {u['stichproben_stimmten']} stimmten")
    zeilen.append(f"Lernen zählt nur deine Urteile: {u['lernen_du']} (automatisch: {u['lernen_auto']}, zählen nicht)")
    frist = f"nach {u['frist_h']} h" if u["frist_h"] > 0 else "nie"
    zeilen.append(f"Frist: {frist} · Aussortieren: {'an (weich)' if u['verwerfen'] else 'aus'}")
    return "\n".join(zeilen)


HILFE = (
    "🎮 <b>Clip-Bot</b>\n"
    "Neue Clips entscheidet der Bot sofort selbst (🤖, ohne Ton) – du musst nichts tun. Umdrehen geht am Clip, "
    "freiwillig; deine Tipps lernt er mit.\n\n"
    "/battle – zwei freigegebene Clips, du wählst den besseren (Elo)\n"
    "/rangliste – Top 10 der aktuellen Saison\n"
    "/gewichte – was die Vorbewertung gelernt hat\n"
    "/offen – unentschiedene Clips erneut zeigen\n"
    "/auto – was die Automatik entscheidet und wie genau sie ist\n"
    "/clip &lt;nr&gt; – einen Clip erneut schicken (zum Umdrehen)\n"
    "/einstellungen – ⚙️ Werte per Knopf umstellen\n"
    "/uploads – freigegebene Highlight-Videos, die noch nicht hochgeladen sind\n"
    "/status – Überblick"
)
