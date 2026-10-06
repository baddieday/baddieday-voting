"""Lern-Bot (`pipeline lernbot`, eigener Token LEARN_BOT_TOKEN, Polling) – getrennt vom Clip-Bot.

Was er tut:
  - nimmt Audiodateien als Musik an: Bildunterschrift = Quellenangabe (Pflicht), "#episch" o. ä. = Stimmung;
    misst Tempo und Energie und antwortet mit dem Ergebnis
  - schickt Entwürfe (Short/Zusammenschnitt) mit direktem Upload-Paket und optionalem 👍/👎-Feedback:
    Musik passt nicht · zu hektisch · Stimmung getroffen · zu lang · abgeschnitten · Clips langweilig ·
    zu viele Effekte · mehr Action (4 Reihen zu je 2 Knöpfen)
  - speichert die Bewertungen (entwurf_bewertungen) – der nächste `compose` lernt daraus (regie_lernen.py)
  - analysiert vor jedem Entwurf ein paar weitere Clips (Stimmung), damit die Auswahl wächst
  - schickt Meldungen aus lern_meldungen (Alarme, abends ein Satz zum Stand, Abschlussbericht)
Befehle: /viral · /entwurf [short|zusammenschnitt|viral|twist|highlight|fail] · /musik · /lernstand · /stand · /hilfe
🔥 /viral (05.10.): ein Knopf – der Bot wählt die Mischung selbst (viral.py), schätzt die Momente per KI ein, baut bis
zu [viral].versuche_max Fassungen, lässt den Cutter-Maßstab benoten und schickt nur die beste.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import sqlite3
import time
import tempfile
from datetime import datetime
from html import escape
from pathlib import Path

from . import (autonom, big, db, einstellungen, entwurf, erwartung, kriterien, kritik, massstab, musik, regie,
               regie_lernen, stile, stimmung, viral)
from .erwartung import anzeige as _erwartung_anzeige  # eigener Name: entwurf_text hat einen Parameter „erwartung“
from .konfig import Konfig, SpeicherOffline
from .zeit import iso, jetzt, utc_zu_lokal

log = logging.getLogger("lern-bot")
TEXT_MAX = 4000
FORMAT_NAMEN = {"short": "Short", "zusammenschnitt": "Zusammenschnitt", "viral": "🔥 Viral-Video",
                "twist": "😅 Viral-Video mit Twist", "highlight": "⚡ Viral-Video (Highlights)", "fail": "💀 Fail-Video"}
ENTWURF_ZIELE = (*regie.FORMATE, *viral.KNOEPFE)   # was /entwurf und die Knöpfe bauen können

HILFE = """<b>Autonomes Lernen des Regisseurs</b>
Veröffentlichte Videos und ihre Publikumszahlen verbessern die nächsten Entwürfe automatisch.
📦 Upload-Paket direkt am Entwurf öffnen, veröffentlichen und den Link senden. Eine Bewertung ist nicht nötig.
🎵 <b>Musik schicken:</b> Audiodatei mit Bildunterschrift = Quellenangabe
   (z. B. „Song: Künstler - Titel / Music provided by NoCopyrightSounds / …“), optional <code>#episch</code>,
   <code>#spannend</code>, <code>#lustig</code>, <code>#frustriert</code> oder <code>#chill</code>.
🔥 /viral – ein Knopf: ich wähle die Mischung (Highlights, Twist mit Fail, reines Fail-Video), benote mich selbst
   und schicke nur die beste Fassung. Fest: /entwurf <code>twist</code>, <code>highlight</code> oder <code>fail</code>
🎬 /entwurf <code>short</code> oder /entwurf <code>zusammenschnitt</code> – neuen Entwurf bauen
👍/👎 bleiben freiwilliges Zusatzfeedback; danach bei Bedarf Gründe antippen und ✅ fertig.
/musik – Titel · /lernstand – autonomer Lernfortschritt · /stand – kurzer Stand
⚙️ /einstellungen – Clip-Auswahl (alle · neuester Spielabend · ein Match), Vorfilter, Effekte, Musik
🧪 /kalibrieren – neuestes Match zum Nachprüfen: je Clip 3 Standbilder, Stimmung, Kills mit Waffen-Nummer
🔗 /tiktok – TikTok-Stand und verbinden · /tiktok neu · /tiktok trennen
Kurzbefehle als Knöpfe: unter dieser Hilfe und nach ✅ fertig."""

# Kurzbefehle (27.09.): Knöpfe im Chat wie beim Bewerten (Florian: „nicht die Tastatur ersetzen“). Callback k:0:<ziel>.
# KURZBEFEHLE bleibt für Taps auf die alte Ersatz-Tastatur, bis sie weg ist (ReplyKeyboardRemove).
KURZBEFEHLE = {"🎬 Short": "short", "🎞️ Zusammenschnitt": "zusammenschnitt", "🧠 Lernstand": "lernstand",
               "📋 Stand": "stand", "📊 Publikum": "publikum", "🎵 Musik": "musik", "⚙️ Einstellungen": "einstellungen",
               "🔥 Viral-Video": "viral"}
KURZ_REIHEN = [["🔥 Viral-Video"], ["🎬 Short", "🎞️ Zusammenschnitt"], ["🧠 Lernstand", "📋 Stand"],
               ["📊 Publikum", "🎵 Musik"], ["⚙️ Einstellungen"]]
KREISE = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫"


# --- Knöpfe und Texte (ohne Telegram testbar) ------------------------------------------

def knoepfe_daumen(eid: int) -> list[list[tuple[str, str]]]:
    return [[("👍", f"d:{eid}:1"), ("👎", f"d:{eid}:-1")]]


def knoepfe_entwurf(eid: int, fmt: str, naechster: str | None = None) -> list[list[tuple[str, str]]]:
    """Upload und nächsten Entwurf ohne Bewertungsrunde erreichen. naechster: Ziel des Knopfs „Nächster Entwurf“
    (🔥 Viral: "viral"), sonst dasselbe Format."""
    from . import lernbot_paket

    return [*(lernbot_paket.knoepfe_nach_fertig(eid, None, fmt) or []),
            [("🎬 Nächster Entwurf", f"k:0:{naechster or fmt}"), ("🧠 Lernstand", "k:0:lernstand")],
            *knoepfe_daumen(eid)]


def naechstes_ziel(zeile: sqlite3.Row) -> str:
    """Was nach diesem Entwurf als Nächstes kommt: ein Viral-Video wieder als 🔥 Viral (Bot wählt neu), sonst das Format."""
    return "viral" if "variante" in zeile.keys() and zeile["variante"] else zeile["format"]


def viral_zeile(liste: dict) -> str | None:
    """„🔥 😅 Highlights mit Twist · 1 Fail · KI 6/7 Momente (viral Ø 71) · Hook ✓ · Standbild ✓“ – None ohne Viral."""
    v = liste.get("viral")
    if not isinstance(v, dict) or v.get("variante") not in viral.VARIANTEN:
        return None
    teile = [f"🔥 {viral.VARIANTEN[v['variante']]}"]
    if v.get("fails"):
        teile.append("1 Fail" if v["fails"] == 1 else f"{v['fails']} Fails")
    if v.get("ki_anteil"):
        teile.append(f"KI {round(v['ki_anteil'] * v.get('momente', 0))}/{v.get('momente', 0)} Momente"
                     + (f" (viral Ø {v['ki_viral']:.0f})" if v.get("ki_viral") is not None else ""))
    else:
        teile.append("Einschätzung per Regel")
    werkzeuge = {"hook_teaser": "Hook", "tod_lupe": "Zeitlupe am Tod", "tod_standbild": "Standbild"}
    teile += [f"{name} ✓" for w, name in werkzeuge.items() if (v.get("werkzeuge") or {}).get(w)]
    return escape(" · ".join(teile))


def knoepfe_gruende(eid: int, gewaehlt: list[str]) -> list[list[tuple[str, str]]]:
    reihen, reihe = [], []
    for schluessel, text in regie_lernen.GRUENDE.items():
        reihe.append((("☑️ " if schluessel in gewaehlt else "") + text, f"g:{eid}:{schluessel}"))
        if len(reihe) == 2:
            reihen.append(reihe)
            reihe = []
    reihen.append(reihe + [("✅ fertig", f"x:{eid}:")])
    return reihen


def parse(daten: str) -> tuple[str, int, str]:
    teile = (daten or "").split(":")
    if len(teile) != 3 or teile[0] not in ("d", "g", "x") or not teile[1].isdigit():
        raise ValueError(daten)
    return teile[0], int(teile[1]), teile[2]


def _balken(werte: list[float]) -> str:
    """Mini-Diagramm des Spannungsbogens: je Wert ein Block von ▁ (kleinster) bis █ (größter).

    Min-Max-Normierung, weil die Momentstärke seit Stufe 2 negativ sein kann (Bot-Opfer, Länge; Spec §8.2) –
    vorher teilte die Funktion durch das Maximum, und [1, -10] warf einen IndexError. Sind alle Werte gleich, gibt
    es nichts zu unterscheiden: jeder bekommt den mittleren Block ▄.
    Parameter: werte – Zahlen (liste["bogen"]). Rückgabe: Text, ein Zeichen je Wert; leere Liste → "".
    Fehler: keine. Beispiel: [2, -3, 1] → "█▁▇" (−3 ist der kleinste, 2 der größte, 1 liegt bei 80 %).
    """
    zeichen = "▁▂▃▄▅▆▇█"
    if not werte:
        return ""
    tief, hoch = min(werte), max(werte)
    if hoch == tief:
        return "▄" * len(werte)
    stufen = len(zeichen) - 1
    return "".join(zeichen[round((w - tief) / (hoch - tief) * stufen)] for w in werte)


def bewertet_zeile(liste: dict) -> str | None:
    """„🔁 Schon bewertet: ① neu ② 2× ③ neu …“ in der Reihenfolge des Videos (27.09.). Über 12 Momente nur die
    Summen. None für alte Schnittlisten ohne Zähler."""
    momente = [s for s in liste.get("segmente", []) if s.get("teil", 1) == 1 and s.get("rolle") != "hook"]
    if not momente or any("bewertet" not in s for s in momente):
        return None
    zahlen = [int(s["bewertet"]) for s in momente]
    if not any(zahlen):
        return "🔁 Alle Momente zum ersten Mal zur Bewertung"
    if len(zahlen) <= len(KREISE):
        return "🔁 Schon bewertet: " + " ".join(f"{KREISE[i]} {'neu' if n == 0 else f'{n}×'}"
                                                for i, n in enumerate(zahlen))
    neu, einmal = zahlen.count(0), zahlen.count(1)
    return f"🔁 Schon bewertet: {neu} neu · {einmal} einmal · {len(zahlen) - neu - einmal} zweimal oder öfter"


def gelernt_zeile(liste: dict) -> str | None:
    """„🧠 Aus #41: Ziel-Dauer 81% → 90% · 6 Momente kommen öfter“ – was die letzte Bewertung an diesem Entwurf
    geändert hat (regie_lernen.wirkung, 27.09.)."""
    g = liste.get("gelernt")
    if not g:
        return None
    quelle = f"#{g['entwurf']}"
    if g.get("format") and g["format"] != liste.get("format"):
        quelle += f" ({FORMAT_NAMEN.get(g['format'], g['format'])})"
    if not g["aenderungen"]:
        return f"🧠 Aus {quelle}: nichts geändert – für den Schnitt Gründe antippen"
    return f"🧠 Aus {quelle}: " + escape(" · ".join(g["aenderungen"][:4]))


def entwurf_text(zeile: sqlite3.Row, liste: dict, bewertung: sqlite3.Row | None = None,
                 erwartung: float | None = None, kritik_text: str | None = None) -> str:
    """Bildunterschrift eines Entwurfs (HTML, höchstens 1000 Zeichen).

    erwartung: festgeschriebene Wahrscheinlichkeit für 👍 (erwartung.gespeichert) oder None → „Erwartung: noch
    keine“ (Spec §10.5). Die Zeile steht VOR den Hinweisen: Die Hinweise können lang sein, und alles hinter Zeichen
    1000 schneidet der Schluss ab. Beispiel: erwartung 0,8 → „🔮 Erwartung: 👍 80 %“, 0,3 → „🔮 Erwartung: 👎 70 %“."""
    m = liste.get("musik")
    momente = len({s["moment"] for s in liste["segmente"]})  # ein Moment mit Jump-Cut hat mehrere Segmente
    teile = [f"🎬 <b>Entwurf #{zeile['id']}</b> · {FORMAT_NAMEN[liste['format']]} {liste['dauer_s']:.0f} s · "
             f"Stimmung <b>{liste['stimmung']}</b> · {momente} Momente",
             f"Bogen {_balken(liste['bogen'])}"]
    if zeile_viral := viral_zeile(liste):
        teile.insert(1, zeile_viral)
    if a := liste.get("auswahl"):
        zeile = f"🆕 {a['neu']} neue · {a['schon_gezeigt']} schon gezeigt · Auswahl aus {a['kandidaten']} Momenten"
        if a.get("gesperrt"):
            zeile += f" · {a['gesperrt']} im Cooldown"
        if a.get("ohne_datei"):
            zeile += f" · {a['ohne_datei']} ohne Datei"
        teile.append(zeile)
    auto = (liste.get("parameter") or {}).get("autonom") or {}
    if auto.get("version"):
        teile.append(f"🧠 Publikum: Lernstand v{auto['version']} · Vertrauen {round(100 * auto.get('confidence', 0))} %")
        if exp := auto.get("exploration"):
            teile.append("🔎 Gezielter Versuch: " + escape(exp["hypothese"]))
    else:
        teile += [z for z in (bewertet_zeile(liste), gelernt_zeile(liste)) if z]
    if m:
        teile.append(f"🎵 {escape(m['titel'])} – {escape(m.get('kuenstler') or '?')} ({m.get('bpm') or 0:.0f} BPM)")
    if (fx := liste.get("effekte") or {}).get("an"):  # Regisseur 2.0: Impacts = Ereignisse im Effekt-Plan
        impacts = sum(len(s.get("effekte") or []) for s in liste["segmente"])
        teile.append(f"✨ Look {escape(str(fx.get('look', 'neutral')))} · {impacts} Impacts"
                     + (" · Hook ✓" if fx.get("hook") else "")
                     + (" · Zeitlupe ✓" if any(s.get("lupe") for s in liste["segmente"]) else "")
                     + (" · Zeitraffer ✓" if any(s.get("raffer") for s in liste["segmente"]) else ""))
    if (stil := (liste.get("parameter") or {}).get("stil")) in stile.STILE:
        teile.append(f"🎬 Stil {stile.STILE[stil]['titel']} · Spielbild ×{liste['parameter'].get('rahmen_zoom', 1.0):g}")
    if kritik_text:
        teile.append(escape(kritik_text))
    teile.append(f"🔮 {_erwartung_anzeige(erwartung, ja='👍', nein='👎')}")
    for h in liste.get("hinweise", [])[:3]:
        teile.append(f"⚠️ {escape(h)}")
    if bewertung is not None:
        gruende = [regie_lernen.GRUENDE[g] for g in json.loads(bewertung["gruende"])]
        teile.append(f"Bewertet: {'👍' if bewertung['daumen'] > 0 else '👎'}" + (" · " + ", ".join(gruende) if gruende else ""))
    else:
        teile.append("📦 Bereit zum Veröffentlichen · 👍/👎 optional")
    return "\n".join(teile)[:1000]  # Bildunterschrift: max. 1024 Zeichen


def autonom_text(con: sqlite3.Connection) -> str:
    stand = autonom.ueberblick(con)
    version = stand.get("version")
    zeilen = ["🧠 AUTONOMES LERNEN",
              f"Veröffentlichte Videos: {stand['veroeffentlicht']}",
              f"Ausgewertete Videos: {stand['ausgewertet']}",
              f"Aktueller Lernstand: v{version}" if version else "Aktueller Lernstand: Startwissen",
              f"Vertrauen: {round(100 * stand['confidence'])} %"]
    if stand.get("erkenntnisse"):
        zeilen += ["", "Zuletzt gelernt:", *[f"• {e}" for e in stand["erkenntnisse"][:3]]]
    else:
        zeilen += ["", "Noch keine belastbare Publikumstendenz. Mit weiteren gemessenen Videos lerne ich dazu."]
    zeilen += ["", "Bewertungen sind optional. Neue Publikumszahlen lösen das Lernen automatisch aus."]
    return "\n".join(zeilen)


def stand_satz(con: sqlite3.Connection) -> str:
    e = con.execute("SELECT COUNT(*) AS n FROM entwuerfe").fetchone()
    daumen = con.execute(
        "SELECT SUM(CASE WHEN daumen > 0 THEN 1 ELSE 0 END) AS gut, COUNT(*) AS n FROM entwurf_bewertungen").fetchone()
    momente = con.execute("SELECT COUNT(*) FROM momente").fetchone()[0]
    tracks = con.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
    text = (autonom_text(con) + f"\n\n📋 Stand: {momente} Momente mit Stimmung, {tracks} Musiktitel, "
            f"{e['n'] or 0} Entwürfe. {daumen['n'] or 0} freiwillige Bewertungen als Start- und Zusatzwissen.")
    # B5: Transparenz, was der Auto-Filter schon allein entschieden hat (0, solange [lernbot].auto_schwelle aus ist)
    if auto := con.execute("SELECT COUNT(*) FROM entwuerfe WHERE auto_verworfen IS NOT NULL").fetchone()[0]:
        text += f" {auto} automatisch aussortiert (Cutter-Note oder Erwartung zu niedrig)."
    return text


def stuecke(text: str, groesse: int = TEXT_MAX) -> list[str]:
    """Lange Texte (Abschlussbericht) an Absätzen in Telegram-taugliche Stücke teilen."""
    teile, aktuell = [], ""
    for absatz in text.split("\n"):
        while len(absatz) > groesse:
            teile.append(absatz[:groesse])
            absatz = absatz[groesse:]
        if len(aktuell) + len(absatz) + 1 > groesse:
            teile.append(aktuell)
            aktuell = ""
        aktuell += absatz + "\n"
    if aktuell.strip():
        teile.append(aktuell)
    return [t.rstrip("\n") for t in teile if t.strip()]


# --- Arbeit im Hintergrund (eigene DB-Verbindung je Thread) ------------------------------

def speicher_da(konfig: Konfig) -> bool:
    try:
        konfig.pruefe_speicher()  # prüft zuerst per TCP, ob pve-big antwortet – hängt nicht am toten Mount
        return True
    except SpeicherOffline:
        return False


def stimmung_nachziehen(con: sqlite3.Connection, konfig: Konfig, matches: set[str] | None = None) -> dict:
    """Vor jedem Entwurf die nächsten n Clips ohne Stimmung analysieren (die besten zuerst) – so wächst die
    Auswahl mit jeder Runde, ohne pve-big dafür extra wach zu halten. Ohne Claude (der zählt gegen dein Abo).
    Rückgabe {"analysiert": n, "hinweise": [...]}: Fehler und Clips ohne Datei kommen als Hinweis in den Entwurf
    (27.09. – vorher nur im Log, der Bot zeigte nichts)."""
    n = int(konfig.wert("lernbot.stimmung_je_entwurf", 10))
    if n <= 0:
        return {"analysiert": 0, "hinweise": []}
    # Clip-Auswahl aktiv (⚙️, 29.09.): erst die Clips dieser Matches – sonst bliebe ein frischer Abend leer, weil die
    # besten Clips insgesamt zuerst drankommen. Doppelt so viele wie sonst, der Rest folgt beim nächsten Entwurf.
    nur = einstellungen.offene_clips(con, matches)[: 2 * n] if matches else None
    try:
        e = stimmung.analysiere(con, konfig, claude=False, maximal=len(nur) if nur else n, nur_clips=nur or None)
    except Exception as fehler:  # der Entwurf ist wichtiger – mit den vorhandenen Momenten weitermachen
        log.exception("Stimmung nachziehen fehlgeschlagen")
        return {"analysiert": 0,
                "hinweise": [f"Stimmung nachziehen fehlgeschlagen: {type(fehler).__name__}: {str(fehler)[:80]}"]}
    hinweise = []
    if e.get("fehlende_dateien"):
        hinweise.append(f"{e['fehlende_dateien']} Clips ohne Datei im Puffer – keine Stimmung möglich")
    if e.get("analysiert"):
        log.info("Stimmung für %s weitere Clips: %s", e["analysiert"], e.get("stimmungen"))
    return {"analysiert": int(e.get("analysiert", 0)), "hinweise": hinweise}


def baue_entwurf(konfig: Konfig, fmt: str) -> int:
    """compose + rendern. Läuft in einem Thread; SQLite-Verbindungen dürfen nicht zwischen Threads wandern.
    Schläft pve-big, wird er geweckt – aber nur, wenn er danach sicher wieder ausgeht (big.darf_wecken)."""
    from .sperre import sperre

    konfig.pruefe_speicher(wecken=True)  # wirft SpeicherOffline mit Grund, wenn Wecken nicht erlaubt ist
    con = db.verbinde(konfig.datenbank)
    try:
        konfig = einstellungen.anwenden(con, konfig)            # ⚙️ im Bot gesetzte Werte vor den Dateien (29.09.)
        nur_matches, quell_hinweis = einstellungen.quell_matches(con, konfig)
        # Rendern ist ein rechenintensiver Schritt: gleiche Sperre wie die Pipeline (nur einer gleichzeitig)
        t0 = time.monotonic()
        with sperre(konfig.datenbank.with_suffix(".lock"), warten_s=float(konfig.wert("sperre.warten_s", 7200))), \
                big.herzschlag(konfig, "lernbot"):
            t1 = time.monotonic()
            nachgezogen = stimmung_nachziehen(con, konfig, nur_matches)
            t2 = time.monotonic()
            variante = None
            if fmt in viral.KNOEPFE:   # 🔥 Viral (05.10.): Mischung wählt der Bot, Momente schätzt die KI ein
                variante = viral.waehle_variante(con, konfig) if fmt == "viral" else fmt
                nachgezogen["hinweise"] += viral.einschaetzen(con, konfig)["hinweise"]
                nur_matches, quell_hinweis = None, None   # Fails und Twists brauchen Material über mehrere Matches
            regie_fmt = "short" if variante else fmt
            parameter, ziel = regie_lernen.aktuelle(con, konfig, regie_fmt)
            if variante:
                parameter = viral.parameter(con, konfig, variante, parameter)
            try:  # nur eine Anzeige – ein Fehler hier darf den Entwurf nicht kosten
                gelernt = regie_lernen.wirkung(con, konfig, regie_fmt)
            except Exception:
                log.exception("Wirkung der letzten Bewertung")
                gelernt = None
            try:
                e = regie.erstelle(con, konfig, regie_fmt, parameter=parameter, ziel=ziel, nur_matches=nur_matches,
                                   hinweise_vorab=[*filter(None, [quell_hinweis]), *nachgezogen["hinweise"]],
                                   gelernt=gelernt, variante=variante)
            except regie.RegieFehler as fehler:
                if not nur_matches:
                    raise
                raise regie.RegieFehler(f"{fehler} – {quell_hinweis}. In ⚙️ Einstellungen auf „alle Clips“ stellen.") \
                    from fehler
            t3 = time.monotonic()
            entwurf.entwurf(con, konfig, e["entwurf"])
            try:  # Cutter-Kritik (30.09.): der Bot benotet sich selbst – ein Fehler kostet nie den Entwurf
                kritik.bewerte(con, konfig, e["entwurf"])
            except Exception:
                log.exception("Cutter-Kritik Entwurf #%s", e["entwurf"])
        # Wo die Wartezeit nach ✅ fertig bleibt (27.09.) – journalctl -u clip-lernbot | grep "gebaut in"
        log.info("Entwurf #%s gebaut in %.0f s: Sperre %.0f s · Stimmung %.0f s (%s Clips) · Schnitt %.0f s · Render %.0f s",
                 e["entwurf"], time.monotonic() - t0, t1 - t0, t2 - t1, nachgezogen["analysiert"], t3 - t2,
                 time.monotonic() - t3)
        return int(e["entwurf"])
    finally:
        con.close()


def massstab_nachziehen(konfig: Konfig) -> int | None:
    """massstab.nachziehen mit eigener Verbindung (läuft in einem Thread, nicht im Event-Loop des Bots)."""
    con = db.verbinde(konfig.datenbank)
    try:
        return massstab.nachziehen(con, konfig, "👍/👎")
    finally:
        con.close()


def pruefe_auto_verwerfen(con: sqlite3.Connection, konfig: Konfig, entwurf_id: int) -> str | None:
    """B5 ([lernbot].auto_schwelle, Florian: „nicht jeden Entwurf bewerten müssen, sagen will ich trotzdem, was
    hochgeladen wird“): None = normal senden, sonst der Grund, warum der Entwurf still aussortiert wird – kein
    Foto an dich, kein Eintrag in entwurf_bewertungen (sonst würde die Erwartung ihr eigenes Urteil bestätigen,
    statt an deinem gemessen zu werden). Sobald Publikumsevidenz vorhanden ist, entfällt dieser historische
    Vorfilter. Schwelle 0 (Standard) oder ohne Modell (zu wenige Urteile, [erwartung].mindest_urteile) lässt durch.
    Beispiel: Schwelle 0,5, Erwartung 32 % → "Erwartung 32 % unter Schwelle 50 %".
    Cutter-Maßstab 1.0 (30.09., Spec §4): zuerst die K.O.-Tore am Video („Tor: Blitze 7/s (Grenze 3)“), dann die
    Note gegen kritik.schwelle – Q20 der eigenen Notenverteilung, begrenzt auf 40–50, erst ab 20 gemessenen
    Entwürfen des Formats (vorher wirken nur die Tore)."""
    note = con.execute("SELECT k.score, k.tore, e.format FROM kritiken k JOIN entwuerfe e ON e.id = k.entwurf_id "
                       "WHERE k.entwurf_id = ?", (entwurf_id,)).fetchone()
    # 30.09.: der Bot erkennt schwache Schnitte selbst ([regie.kritik].schwelle = 0 schaltet das ganz ab, Tore inklusive)
    if note is not None and float(konfig.wert("regie.kritik.schwelle", 50) or 0) > 0:
        try:
            tor = kriterien.tor_verletzt(json.loads(note["tore"] or "null"))
        except ValueError:
            tor = None
        if tor is not None:
            return f"Tor: {tor['text']}"
        mindest = kritik.schwelle(con, konfig, note["format"])
        if mindest > 0 and note["score"] < mindest:
            return f"Cutter-Score {note['score']:.0f} unter {mindest:.0f}"
    schwelle = float(konfig.wert("lernbot.auto_schwelle", 0.0))
    if schwelle <= 0 or autonom.ueberblick(con)["ausgewertet"] > 0:
        return None
    wahrschein = erwartung.vorhersage(con, konfig, "entwurf", entwurf_id)
    if wahrschein is None or wahrschein >= schwelle:
        return None
    return f"Erwartung {round(100 * wahrschein)} % unter Schwelle {round(100 * schwelle)} %"


def beste_fassung(con: sqlite3.Connection, ids: list[int]) -> int:
    """Die Fassung mit der höchsten Cutter-Note (ohne Note zählt 0; bei Gleichstand die neuere)."""
    noten = {z["entwurf_id"]: float(z["score"]) for z in con.execute(
        f"SELECT entwurf_id, score FROM kritiken WHERE entwurf_id IN ({','.join('?' for _ in ids)})", ids)}
    return max(ids, key=lambda i: (noten.get(i, 0.0), i))


def nimm_musik(konfig: Konfig, datei: Path, bildunterschrift: str, dateiname: str) -> dict:
    con = db.verbinde(konfig.datenbank)
    try:
        titel, kuenstler, quelle = musik.aus_bildunterschrift(bildunterschrift, dateiname)
        t = musik.hinzufuegen(con, konfig, datei, titel=titel, kuenstler=kuenstler, quelle=quelle,
                              stimmung=musik.stimmung_aus_text(bildunterschrift))
        return dict(t)
    finally:
        con.close()


# --- Versand ---------------------------------------------------------------------------

def _markup(knoepfe):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=d) for t, d in reihe] for reihe in knoepfe])


def knoepfe_kurzbefehle() -> list[list[tuple[str, str]]]:
    """Kurzbefehle als Knöpfe im Chat (unter /hilfe und nach ✅ fertig)."""
    return [[(text, f"k:0:{KURZBEFEHLE[text]}") for text in reihe] for reihe in KURZ_REIHEN]


def ohne_tastatur():
    """Nimmt die Ersatz-Tastatur vom 27.09. wieder weg (Telegram behält sie sonst)."""
    from telegram import ReplyKeyboardRemove

    return ReplyKeyboardRemove()


def _liste(zeile: sqlite3.Row) -> dict:
    return json.loads(Path(zeile["schnittliste"]).read_text(encoding="utf-8"))


async def sende_entwuerfe(app) -> int:
    # Handler (/entwurf) und Schleife können gleichzeitig senden wollen -> nacheinander, sonst doppelt
    schloss = app.bot_data.setdefault("sende_schloss", asyncio.Lock())
    async with schloss:
        return await _sende_entwuerfe(app)


async def _sende_entwuerfe(app) -> int:
    con, chat, konfig = app.bot_data["con"], app.bot_data["erlaubt"], app.bot_data["konfig"]
    gesendet = 0
    for z in con.execute(
            "SELECT * FROM entwuerfe WHERE status = 'gerendert' AND auto_verworfen IS NULL ORDER BY id").fetchall():
        pfad = Path(z["datei"] or "")
        if not pfad.is_file():
            log.warning("Entwurf #%s: Datei fehlt (%s)", z["id"], pfad)
            continue
        # Erwartung VOR send_video festschreiben (Spec §10.5): sie muss feststehen, bevor du 👍/👎 drückst. Ein
        # Fehler hier hält das Senden nicht auf – der Entwurf kommt dann mit „Erwartung: noch keine“.
        try:
            wert = erwartung.festschreiben(con, konfig, "entwurf", z["id"])
        except Exception:
            log.exception("Erwartung für Entwurf #%s nicht festgeschrieben", z["id"])
            wert = None
        with pfad.open("rb") as datei:
            nachricht = await app.bot.send_video(
                chat_id=chat, video=datei, caption=entwurf_text(z, _liste(z), erwartung=wert, kritik_text=kritik.kritik_zeile(con, z["id"])), parse_mode="HTML",
                reply_markup=_markup(knoepfe_entwurf(z["id"], z["format"], naechstes_ziel(z))),
                supports_streaming=True,
                read_timeout=300, write_timeout=300, connect_timeout=30,
            )
        con.execute("UPDATE entwuerfe SET status = 'gesendet', tg_nachricht_id = ? WHERE id = ?",
                    (nachricht.message_id, z["id"]))
        gesendet += 1
    return gesendet


async def sende_meldungen(app) -> int:
    from . import lernbot_publikum  # hier, nicht oben: die Publikums-Module dürfen lernbot selbst importieren

    con, chat = app.bot_data["con"], app.bot_data["erlaubt"]
    gesendet = 0
    # Meldungen der Lernschleife (publikum:…, woche:…) warten die Ruhezeit ab, alle anderen kommen sofort (Spec §12)
    for m in lernbot_publikum.faellige_lern_meldungen(con, app.bot_data["konfig"]):
        for stueck in stuecke(m["text"]):
            await app.bot.send_message(chat, stueck)
        con.execute("UPDATE lern_meldungen SET gesendet = ? WHERE id = ?", (iso(jetzt()), m["id"]))
        gesendet += 1
    return gesendet


def abendstand(con: sqlite3.Connection, konfig: Konfig, zeit: datetime | None = None) -> bool:
    """Legt abends einmal pro Tag einen Satz zum Stand als Meldung an. True = neu angelegt."""
    lokal = utc_zu_lokal(zeit or jetzt(), konfig.wert("zeit.zeitzone", "Europe/Berlin"))
    stunde, minute = (int(x) for x in str(konfig.wert("lernbot.abend_uhrzeit", "21:00")).split(":"))
    if (lokal.hour, lokal.minute) < (stunde, minute):
        return False
    return db.lern_meldung(con, f"abend:{lokal:%Y-%m-%d}", stand_satz(con))


def blick_auf_leerlauf(app) -> None:
    """Scharfes clip-leerlauf auf pve-big gesehen? (erlaubt Wecken) – im Hintergrund und höchstens einmal
    gleichzeitig: Hängt der NFS-Mount, weil pve-big gerade ausgeht, bleibt nur dieser eine Faden stehen und
    die Schleife läuft weiter."""
    alt = app.bot_data.get("leerlauf_blick")
    if alt is not None and not alt.done():
        return
    neu = asyncio.ensure_future(asyncio.to_thread(big.merke_leerlauf, app.bot_data["konfig"]))
    neu.add_done_callback(lambda f: f.cancelled() or f.exception())  # Fehler hier sind egal
    app.bot_data["leerlauf_blick"] = neu


async def _schleife(app) -> None:
    from . import lernbot_zahlen  # hier, nicht oben: die Publikums-Module dürfen lernbot selbst importieren

    konfig = app.bot_data["konfig"]
    while True:
        blick_auf_leerlauf(app)
        # lernbot_zahlen.aufraeumen: wartende Screenshots nach 10 min verwerfen (Lernschleife, Spec §7.1)
        for aufgabe in (sende_meldungen, sende_entwuerfe, lernbot_zahlen.aufraeumen):
            try:
                await aufgabe(app)
            except Exception:
                log.exception("Fehler in %s", aufgabe.__name__)
        try:
            if konfig.wert("lernbot.abendstand", True):
                abendstand(app.bot_data["con"], konfig)
        except Exception:
            log.exception("Abendstand")
        await asyncio.sleep(float(konfig.wert("lernbot.intervall_s", 30)))


# --- Handler ---------------------------------------------------------------------------

async def cmd_hilfe(update, context) -> None:
    from . import lernbot_publikum  # hier, nicht oben: die Publikums-Module dürfen lernbot selbst importieren

    # HILFE bleibt unverändert; der Teil zur Lernschleife „Publikum“ kommt als Zusatz dahinter
    await update.effective_message.reply_text(HILFE + lernbot_publikum.HILFE_ZUSATZ, parse_mode="HTML",
                                              reply_markup=_markup(knoepfe_kurzbefehle()))


async def cmd_stand(update, context) -> None:
    await update.effective_message.reply_text(stand_satz(context.bot_data["con"]))


async def cmd_lernstand(update, context) -> None:
    await update.effective_message.reply_text(autonom_text(context.bot_data["con"]))


async def cmd_musik(update, context) -> None:
    zeilen = context.bot_data["con"].execute(
        "SELECT id, titel, kuenstler, bpm, energie, stimmungen FROM tracks ORDER BY id DESC LIMIT 30").fetchall()
    if not zeilen:
        await update.effective_message.reply_text("Noch keine Musik. Schick mir eine Audiodatei mit Quellenangabe.")
        return
    text = "\n".join(f"#{z['id']} {z['kuenstler'] or '?'} – {z['titel']} · {z['bpm'] or 0:.0f} BPM · "
                     f"E {z['energie'] or 0:.2f} · {', '.join(json.loads(z['stimmungen'] or '[]'))}" for z in zeilen)
    await update.effective_message.reply_text(text[:TEXT_MAX])


async def neuer_entwurf(app, fmt: str) -> int | None:
    """Baut einen Entwurf und schickt ihn – für /entwurf und automatisch nach jeder fertigen Bewertung.

    [lernbot].auto_schwelle > 0 (B5): baut bis zu auto_versuche_max Entwürfe, verwirft dabei jeden mit zu
    niedriger Erwartung still (pruefe_auto_verwerfen, kein Foto an dich) und zeigt dir nur den ersten, der die
    Schwelle schafft. Schafft es keiner, kommt trotzdem der letzte Versuch – sonst bekämst du irgendwann nie mehr
    etwas zu sehen, nur weil der Regisseur gerade schwach dasteht."""
    chat = app.bot_data["erlaubt"]
    if app.bot_data.get("arbeitet"):
        await app.bot.send_message(chat, "⏳ Ich baue gerade schon einen Entwurf.")
        return None
    app.bot_data["arbeitet"] = True
    try:
        con = app.bot_data["con"]
        konfig = einstellungen.anwenden(con, app.bot_data["konfig"])   # ⚙️ Vorfilter usw. (29.09.)
        wach = await asyncio.to_thread(speicher_da, konfig)
        await app.bot.send_message(chat, (f"🎬 Baue ein {FORMAT_NAMEN[fmt]} …" if fmt in viral.KNOEPFE else
                                          f"🎬 Baue einen {FORMAT_NAMEN[fmt]} …")
                                   + ("" if wach else " 💤 pve-big schläft – ich wecke ihn (bis zu 3 min)."),
                                   reply_markup=ohne_tastatur())
        ist_viral = fmt in viral.KNOEPFE
        versuche_max = max(1, int(konfig.wert("viral.versuche_max" if ist_viral else "lernbot.auto_versuche_max", 3)))
        aussortiert = []
        for versuch in range(versuche_max):
            eid = await asyncio.to_thread(baue_entwurf, konfig, fmt)
            letzter = versuch == versuche_max - 1
            grund = pruefe_auto_verwerfen(con, konfig, eid) if ist_viral or not letzter else None
            if grund is None:
                break
            con.execute("UPDATE entwuerfe SET auto_verworfen = ? WHERE id = ?", (grund, eid))
            log.info("Entwurf #%s automatisch aussortiert: %s", eid, grund)
            aussortiert.append(eid)
            if letzter:  # 🔥 Viral: keine Fassung schaffte die Hürde – die beste nach Cutter-Note kommt trotzdem
                eid = beste_fassung(con, aussortiert)
                con.execute("UPDATE entwuerfe SET auto_verworfen = NULL WHERE id = ?", (eid,))
                aussortiert.remove(eid)
        if aussortiert:
            wort = "Entwurf" if len(aussortiert) == 1 else "Entwürfe"
            await app.bot.send_message(chat, f"🤖 {len(aussortiert)} {wort} automatisch aussortiert (Cutter-Note oder "
                                             "Erwartung zu niedrig)")
        t = time.monotonic()
        await sende_entwuerfe(app)
        log.info("Entwurf #%s gesendet in %.0f s", eid, time.monotonic() - t)
        return eid
    except Exception as e:  # dir kurz sagen, was los ist – Details ins Log
        log.exception("Entwurf fehlgeschlagen")
        await app.bot.send_message(chat, f"⚠️ Entwurf fehlgeschlagen: {escape(str(e)[:300])}")
        return None
    finally:
        app.bot_data["arbeitet"] = False


async def cmd_entwurf(update, context) -> None:
    fmt = (context.args or ["short"])[0].lower()
    if fmt not in ENTWURF_ZIELE:
        await update.effective_message.reply_text("Aufruf: /entwurf short · zusammenschnitt · viral · twist · "
                                                  "highlight · fail")
        return
    # Im Hintergrund wie nach einer Bewertung: sonst stehen alle anderen Knöpfe, bis der Entwurf fertig ist
    context.application.create_task(neuer_entwurf(context.application, fmt))


async def cmd_viral(update, context) -> None:
    """🔥 /viral: ein Knopf – Mischung, Einschätzung, Fassungen und Auswahl macht der Bot selbst (im Hintergrund)."""
    context.application.create_task(neuer_entwurf(context.application, "viral"))


async def bei_kurzknopf(update, context) -> None:
    """Kurzbefehl-Knopf im Chat (k:0:<ziel>): sofort antworten, dann Entwurf im Hintergrund oder Befehl."""
    query = update.callback_query
    if query.from_user is None or query.from_user.id != context.bot_data["erlaubt"]:
        await query.answer("Nicht erlaubt.")
        return
    ziel = (query.data or "").split(":", 2)[-1]
    if ziel not in KURZBEFEHLE.values():
        await query.answer("Unbekannter Knopf.")
        return
    await query.answer(f"🎬 {FORMAT_NAMEN[ziel]} kommt …" if ziel in ENTWURF_ZIELE else None)
    await _kurzbefehl(ziel, update, context)


async def bei_kurzbefehl(update, context) -> None:
    """Tap auf die alte Ersatz-Tastatur: Tastatur wegnehmen, dann wie der Knopf."""
    ziel = KURZBEFEHLE.get((update.effective_message.text or "").strip())
    await update.effective_message.reply_text("Die Kurzbefehle sind jetzt Knöpfe im Chat (/hilfe).",
                                              reply_markup=ohne_tastatur())
    await _kurzbefehl(ziel, update, context)


async def _kurzbefehl(ziel: str | None, update, context) -> None:
    if ziel in ENTWURF_ZIELE:
        context.application.create_task(neuer_entwurf(context.application, ziel))
    elif ziel == "lernstand":
        await cmd_lernstand(update, context)
    elif ziel == "stand":
        await cmd_stand(update, context)
    elif ziel == "musik":
        await cmd_musik(update, context)
    elif ziel == "publikum":
        from . import lernbot_publikum  # hier, nicht oben: die Publikums-Module dürfen lernbot selbst importieren

        await lernbot_publikum.cmd_publikum(update, context)
    elif ziel == "einstellungen":
        from . import lernbot_einstellungen

        await lernbot_einstellungen.cmd_einstellungen(update, context)


async def bei_audio(update, context) -> None:
    nachricht = update.effective_message
    datei_info = nachricht.audio or nachricht.document
    bildunterschrift = (nachricht.caption or "").strip()
    if not bildunterschrift:
        await nachricht.reply_text("Bitte nochmal mit Bildunterschrift = Quellenangabe (Lizenz). Ohne nehme ich keine Musik an.")
        return
    name = getattr(datei_info, "file_name", None) or "titel.mp3"
    endung = Path(name).suffix.lower() or ".mp3"
    if endung not in musik.ENDUNGEN:
        await nachricht.reply_text(f"Format {endung} kenne ich nicht ({', '.join(sorted(musik.ENDUNGEN))}).")
        return
    with tempfile.TemporaryDirectory() as tmp:
        ziel = Path(tmp) / f"eingang{endung}"
        telegram_datei = await datei_info.get_file()
        await telegram_datei.download_to_drive(ziel)
        try:
            t = await asyncio.to_thread(nimm_musik, context.bot_data["konfig"], ziel, bildunterschrift, name)
        except Exception as e:
            log.exception("Musik")
            await nachricht.reply_text(f"⚠️ Konnte die Musik nicht übernehmen: {escape(str(e)[:300])}")
            return
    await nachricht.reply_text(
        f"🎵 #{t['id']} {t['kuenstler'] or '?'} – {t['titel']}\n{t['bpm']:.0f} BPM · Energie {t['energie']:.2f} · "
        f"passt zu: {', '.join(json.loads(t['stimmungen'] or '[]'))}")


async def bei_klick(update, context) -> None:
    query = update.callback_query
    con = context.bot_data["con"]
    if query.from_user is None or query.from_user.id != context.bot_data["erlaubt"]:
        await query.answer("Nicht erlaubt.")
        return
    try:
        aktion, eid, extra = parse(query.data)
    except ValueError:
        await query.answer("Unbekannter Knopf.")
        return
    start = time.monotonic()
    zeile = con.execute("SELECT * FROM entwuerfe WHERE id = ?", (eid,)).fetchone()
    if zeile is None:
        await query.answer("Entwurf unbekannt.")
        return
    if aktion == "g" and extra not in regie_lernen.GRUENDE:
        await query.answer("Unbekannter Knopf.")
        return
    # Zuerst antworten (27.09., „Buttons laden lange“): Telegram zeigt am Knopf einen Spinner, bis
    # answerCallbackQuery da ist – vorher kam die Antwort erst nach Datenbank und Caption-Aufbau. Die Texte hängen
    # nicht von der Datenbank ab; die Caption folgt gleich danach.
    weiter = bool(context.bot_data["konfig"].wert("lernbot.naechster_nach_bewertung", True))
    if aktion == "d":
        await query.answer("Danke! Gründe antippen (optional), dann ✅ fertig." if extra == "1" else
                           "Danke! Was hat gestört? Ohne Grund lernt nur die Moment-Auswahl, nicht der Schnitt.")
        geantwortet = time.monotonic()
        bewertung = regie_lernen.bewerte(con, eid, daumen=int(extra))
        knoepfe = knoepfe_gruende(eid, json.loads(bewertung["gruende"]))
    elif aktion == "g":
        await query.answer(regie_lernen.GRUENDE[extra])
        geantwortet = time.monotonic()
        bewertung = regie_lernen.bewerte(con, eid, grund=extra)
        knoepfe = knoepfe_gruende(eid, json.loads(bewertung["gruende"]))
    else:
        await query.answer("Gespeichert – der nächste Entwurf kommt gleich." if weiter
                           else "Gespeichert – fließt in den nächsten Entwurf ein.")
        geantwortet = time.monotonic()
        bewertung = con.execute("SELECT * FROM entwurf_bewertungen WHERE entwurf_id = ?", (eid,)).fetchone()
        from . import lernbot_paket  # hier, nicht oben: lernbot_paket darf lernbot selbst importieren

        knoepfe = lernbot_paket.knoepfe_nach_fertig(eid, bewertung, zeile["format"])
        knoepfe = [*(knoepfe or []), *knoepfe_kurzbefehle()]  # 27.09.: Kurzbefehle nach dem Bewerten
        if weiter:  # Lernschleife: sofort der nächste Entwurf, schon mit dieser Bewertung eingerechnet
            context.application.create_task(neuer_entwurf(context.application, naechstes_ziel(zeile)))
    gespeichert = time.monotonic()
    try:
        await query.edit_message_caption(caption=entwurf_text(zeile, _liste(zeile), bewertung,
                                                              erwartung=erwartung.gespeichert(con, "entwurf", eid),
                                                              kritik_text=kritik.kritik_zeile(con, eid)),
                                         parse_mode="HTML",
                                         reply_markup=_markup(knoepfe) if knoepfe else None)
    except Exception as fehler:  # „message is not modified“ beim Doppelklick ist normal; alles andere ins Log
        if "not modified" not in str(fehler):
            log.warning("Bildunterschrift #%s nicht aktualisiert: %s: %s", eid, type(fehler).__name__, str(fehler)[:120])
    # Wo ein Klick Zeit braucht – zum Nachmessen auf dem Mini: journalctl -u clip-lernbot | grep Knopf
    log.info("Knopf %s Entwurf #%s: Antwort %.2f s · Speichern %.2f s · Bildunterschrift %.2f s", aktion, eid,
             geantwortet - start, gespeichert - geantwortet, time.monotonic() - gespeichert)
    if aktion not in ("d", "g") and not weiter:
        # Cutter-Maßstab (30.09.): dein 👍/👎 ist ein Lehrer – nachlernen (eigene Verbindung, im Thread). Mit
        # „weiter“ erledigt das die Kritik des nächsten Entwurfs, der schon mit dieser Bewertung gebaut wird.
        await asyncio.to_thread(massstab_nachziehen, context.bot_data["konfig"])


async def bei_fehler(update, context) -> None:
    from telegram.error import Conflict

    if isinstance(context.error, Conflict):
        log.error("Ein anderes Programm holt Updates für LEARN_BOT_TOKEN ab – nur ein Empfänger erlaubt.")
        return
    log.error("Fehler im Lern-Bot", exc_info=context.error)


def anfragen(konfig: Konfig):
    """(Bot-Anfragen, getUpdates-Anfragen) für Telegram. [lernbot].nur_ipv4 (Standard an, 27.09.): Auf dem Mini lief
    der Bot über IPv6 (Fritz!Box, Telekom, Route-MTU 1492), und die lange Warteabfrage blieb hängen – Klicks kamen
    gebündelt 15–20 s später an (Journal: vier Gründe in 70 ms). Über IPv4 antwortet Telegram in unter 0,1 s."""
    import httpx
    from telegram.request import HTTPXRequest

    def transport():
        if not bool(konfig.wert("lernbot.nur_ipv4", True)):
            return {}
        return {"httpx_kwargs": {"transport": httpx.AsyncHTTPTransport(local_address="0.0.0.0")}}

    return (HTTPXRequest(connection_pool_size=256, **transport()),
            HTTPXRequest(connection_pool_size=1, read_timeout=5.0, **transport()))


def baue_app(konfig: Konfig, token: str, erlaubt: int):
    from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

    async def nach_start(app) -> None:
        app.bot_data["schleife"] = asyncio.create_task(_schleife(app))
        log.info("Lern-Bot läuft.")

    async def vor_ende(app) -> None:
        if aufgabe := app.bot_data.get("schleife"):
            aufgabe.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await aufgabe

    # concurrent_updates (27.09.): Updates laufen nebenläufig statt nacheinander. Vorher wartete jeder Klick auf den
    # vorigen (je zwei Telegram-Roundtrips) und auf Handler, die länger awaiten (Musik, Screenshot) – „Buttons laden
    # lange“. Alle Handler teilen eine SQLite-Verbindung; sie rufen sie nur synchron zwischen zwei awaits, das ist im
    # Event-Loop unkritisch. Doppelklicks fangen die Handler selbst ab (arbeitet, paket_arbeitet, message not modified).
    bot_anfragen, update_anfragen = anfragen(konfig)
    app = (Application.builder().token(token).concurrent_updates(True)
           .request(bot_anfragen).get_updates_request(update_anfragen)
           .post_init(nach_start).post_stop(vor_ende).build())
    app.bot_data.update(con=db.verbinde(konfig.datenbank), konfig=konfig, erlaubt=erlaubt)
    nur_ich = filters.User(user_id=erlaubt)
    for name, funktion in (("start", cmd_hilfe), ("hilfe", cmd_hilfe), ("help", cmd_hilfe), ("stand", cmd_stand),
                           ("lernstand", cmd_lernstand), ("musik", cmd_musik), ("entwurf", cmd_entwurf),
                           ("viral", cmd_viral)):
        app.add_handler(CommandHandler(name, funktion, filters=nur_ich))
    app.add_handler(MessageHandler(nur_ich & (filters.AUDIO | filters.Document.AUDIO), bei_audio))
    # Kurzbefehle VOR dem freien Text der Zahlen-Eingabe (lernbot_zahlen.bei_text nimmt sonst jeden Text)
    app.add_handler(MessageHandler(nur_ich & filters.Text(list(KURZBEFEHLE)), bei_kurzbefehl))
    app.add_handler(CallbackQueryHandler(bei_kurzknopf, pattern=r"^k:"))  # vor bei_klick (liest sonst k: als Entwurf)
    # Lernschleife „Publikum“ (Spec §7.1, §10.4, §14 Stufe 1): Screenshots/Hand-Eingabe, Upload-Paket und /link,
    # /publikum – eigene Module, hier nur eingehängt. VOR dem allgemeinen Klick-Handler: der liest jeden Knopf als
    # Entwurfs-Knopf; die Module melden ihre Knöpfe (pl/pm, pk/pt) mit eigenem Muster an.
    from . import (lernbot_einstellungen, lernbot_kalibrierung, lernbot_paket, lernbot_publikum, lernbot_tiktok,
                   lernbot_zahlen)

    for modul in (lernbot_zahlen, lernbot_paket, lernbot_publikum, lernbot_einstellungen, lernbot_kalibrierung,
                  lernbot_tiktok):
        modul.registriere(app, nur_ich)
    app.add_handler(CallbackQueryHandler(bei_klick))
    app.add_error_handler(bei_fehler)
    return app


def starte(konfig: Konfig) -> int:
    token = os.environ.get("LEARN_BOT_TOKEN", "").strip()
    erlaubt = (os.environ.get("LEARN_BOT_ALLOWED_USER_ID") or os.environ.get("TELEGRAM_ALLOWED_USER_ID") or "").strip()
    if not token or not erlaubt.isdigit():
        print("LEARN_BOT_TOKEN und TELEGRAM_ALLOWED_USER_ID (oder LEARN_BOT_ALLOWED_USER_ID) in .env eintragen.")
        return 2
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # würde sonst URLs mit Token loggen
    app = baue_app(konfig, token, int(erlaubt))
    # Warteabfrage 5 s statt 10: Hängt eine doch einmal, gibt der Bot sie nach ~10 s auf statt nach ~15–20 s
    app.run_polling(allowed_updates=["message", "callback_query"], drop_pending_updates=False,
                    timeout=int(konfig.wert("lernbot.poll_timeout_s", 5)))
    return 0
