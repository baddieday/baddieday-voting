"""Kommandozeile `pipeline ...` – Vertrag mit n8n (siehe CLAUDE.md, "Schnittstelle zu n8n"):

  prepare|analyze|decide|render --session ID     highlight --id ID --tage 14
  (weitere Befehle für Handbetrieb und Timer, z. B. momente nachschneiden [--tage 14] [--probe])

Logs gehen nach stderr; die letzte Zeile auf stdout ist genau eine JSON-Zeile.
Exit-Codes: 0 ok · 1 Fehler · 2 falscher Aufruf/Konfig · 3 Speicher offline · 4 Sperre nicht bekommen
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import aufraeumen, bestand, big, caption, db, erfassung, highlight, lernen, material, replay, shorts, stimmung, verarbeitung
from . import erwartung, merkmale, mikro  # Stufe 2 (Lernschleife): Merkmale, Mic-Schritt, Erwartung
from .konfig import KonfigFehler, SpeicherOffline, lade
from .medien import MedienFehler
from .sperre import Gesperrt, sperre
from .vorbewertung import MERKMAL_NAMEN, MERKMALE, zahl
from .zeit import iso, jetzt, utc_zu_lokal

log = logging.getLogger("pipeline")

# Befehle, die den Speicher brauchen: schläft der große Host, wird er per Wake-on-LAN geweckt
WECKEN = {"prepare", "analyze", "decide", "render", "highlight", "scan", "process", "short"}


def utc_heute_iso() -> str:
    return iso(jetzt().replace(hour=0, minute=0, second=0, microsecond=0))


def _json(daten: dict) -> None:
    print(json.dumps(daten, ensure_ascii=False, default=str), flush=True)


# --- Vertrag mit n8n ------------------------------------------------------------

def _cmd_schritt(args, konfig, con) -> int:
    schritt = {"prepare": verarbeitung.prepare, "analyze": verarbeitung.analyze,
               "decide": verarbeitung.decide, "render": verarbeitung.render}[args.befehl]
    log.info("%s --session %s", args.befehl, args.session)
    ergebnis = schritt(con, konfig, args.session)
    if args.befehl == "render" and ergebnis.get("neu", 0) > 0:
        # Mic-Analyse (Whisper) losgelöst im Hintergrund (Spec §8.2, §12): schreibt nichts auf unser stdout/stderr,
        # Fehler beim Start sind nur eine Log-Warnung – die JSON-Zeile und der Exit-Code bleiben gleich (n8n-Vertrag)
        mikro.starte_im_hintergrund(konfig, args.session)
    _json(ergebnis)
    return 0


def _cmd_highlight(args, konfig, con) -> int:
    log.info("highlight --id %s --tage %s", args.id, args.tage)
    _json(highlight.erstelle(con, konfig, args.id, args.tage))
    return 0


# --- Komfort-Befehle ------------------------------------------------------------

def _cmd_scan(args, konfig, con) -> int:
    ergebnis = erfassung.scan(con, konfig)
    if args.verarbeiten:
        ergebnis["verarbeitet"] = []
        for sid in ergebnis["offen"]:
            try:
                ergebnis["verarbeitet"].append(verarbeitung.process(con, konfig, sid))
            except (verarbeitung.SessionFehler, MedienFehler, replay.ReplayFehler) as e:
                log.error("Session %s: %s", sid, e)  # ein kaputtes Match blockiert die anderen nicht
                ergebnis["verarbeitet"].append({"session": sid, "fehler": str(e)[:300]})
    _json(ergebnis)
    return 0


def _cmd_process(args, konfig, con) -> int:
    _json(verarbeitung.process(con, konfig, args.session))
    return 0


def _cmd_replay(args, konfig, con) -> int:
    """Zeigt meine Ereignisse eines Replays in Ortszeit – zum Kalibrieren und Nachsehen.

    Stufe 2: je Ereignis auch Waffe (GunType-ZAHL für [merkmale.waffen]), Bot (ja/nein/? = unbekannt) und die
    verbleibenden Spieler (? ohne spieler_gesamt). So ordnet man die Zahlen an bekannten Kills zu (docs/PUBLIKUM.md)."""
    match, roh = replay.lies(Path(args.datei), konfig)
    zone = konfig.wert("zeit.zeitzone", "Europe/Berlin")
    ich = roh.get("ich") or {}
    print(f"Match {match.id} · Build {match.build}")
    print(f"Ich: {ich.get('name')} (Epic-ID {ich.get('epic_id')}, erkannt per {match.ich_quelle})")
    print(f"Platz {match.platzierung} · Kills laut Replay {match.kills_stats}")
    print(f"Start {utc_zu_lokal(match.start_utc, zone):%d.%m.%Y %H:%M:%S} · Ende {utc_zu_lokal(match.ende_utc, zone):%H:%M:%S}")
    namen = {"kill": "Kill", "knock": "Knock", "tod": "gestorben", "knock_erlitten": "selbst am Boden"}
    bot_text = {True: "ja", False: "nein", None: "?"}  # None = unbekannt (replay2json liefert null)
    for e in match.ereignisse:
        zeile = f"  {utc_zu_lokal(e.zeit_utc, zone):%H:%M:%S.%f}"[:-3] + f"  {namen.get(e.art, e.art)}"
        if e.art == "kill" and e.aktion_utc is not None and e.aktion_utc != e.zeit_utc:
            # Aktion = mein Umhauen: dort beginnt der Clip (beim Team-Wipe Sekunden vor dem Kill)
            vorher = (e.zeit_utc - e.aktion_utc).total_seconds()
            zeile += f"  (umgehauen {utc_zu_lokal(e.aktion_utc, zone):%H:%M:%S}, {vorher:.1f} s vorher)"
        waffe = "?" if e.waffe is None else e.waffe
        uebrig = "?" if e.verbleibend is None else e.verbleibend
        zeile += f"  [Waffe {waffe} · Bot {bot_text[e.opfer_bot]} · {uebrig} übrig]"
        print(zeile)
    for w in match.warnungen:
        print(f"  ⚠️ {w}")
    return 0


def _cmd_status(args, konfig, con) -> int:
    matches = {z["status"]: z["n"] for z in con.execute("SELECT status, COUNT(*) AS n FROM matches GROUP BY status")}
    ergebnis = {
        "clips": db.anzahl_je_status(con),
        "matches": matches,
        "aufnahmen": con.execute("SELECT COUNT(*) FROM aufnahmen").fetchone()[0],
    }
    try:
        konfig.pruefe_speicher()
        ergebnis["speicher"] = "ok"
    except SpeicherOffline as e:
        ergebnis["speicher"] = f"offline: {e}"
    _json(ergebnis)
    return 0


def _cmd_gewichte(args, konfig, con) -> int:
    version, e = lernen.aktualisiere(con, konfig) if args.neu else (None, lernen.berechne(con, konfig))
    print(f"Datenbasis: {e.datenbasis} Bewertungen ({e.freigaben} Freigaben/Verwerfungen, {e.battles} Battles)")
    print(f"Status: {e.grund} · Vertrauen {round(e.vertrauen * 100)} %")
    print(f"{'Merkmal':<16}{'Start':>8}{'Aktuell':>9}")
    for m in MERKMALE:
        print(f"{MERKMAL_NAMEN[m]:<16}{zahl(e.start[m], 2):>8}{zahl(e.werte[m], 2):>9}")
    if e.trefferquote is not None:
        print(f"Trefferquote {round(e.trefferquote * 100)} % (Start: {round((e.trefferquote_start or 0) * 100)} %)")
    if version is not None:
        print(f"Gespeichert als Version {version}")
    if text := erwartung.trefferquote_text(con, konfig):  # Spec §10.5 – leer, solange nichts zu zeigen ist
        print(text)
    return 0


def _cmd_caption(args, konfig, con) -> int:
    zeile = db.clip(con, args.clip)
    if zeile is None:
        raise KeyError(f"Clip {args.clip} unbekannt")
    print(caption.baue(zeile, db.match(con, zeile["match_id"]), konfig))
    return 0


def _cmd_short(args, konfig, con) -> int:
    konfig.pruefe_speicher()
    zeile = db.clip(con, args.clip)
    if zeile is None:
        raise KeyError(f"Clip {args.clip} unbekannt")
    ziel = shorts.ziel_fuer(konfig, zeile)
    groesse = shorts.rendere(konfig.absolut(zeile["clip_pfad"]), ziel, konfig, layout=args.layout)
    con.execute("UPDATE clips SET short_pfad = ? WHERE id = ?", (konfig.relativ(ziel), args.clip))
    _json({"clip": args.clip, "short": konfig.relativ(ziel), "mb": round(groesse / 1e6, 1)})
    return 0


def _cmd_aufraeumen(args, konfig, con) -> int:
    # Im getrennten Betrieb (E19) lehnt schon main ab – vor der Pipeline-Sperre, siehe _vorab_ablehnen
    if args.taeglich and con.execute(
        "SELECT 1 FROM ereignisse WHERE art = 'aufraeumen' AND zeit >= ?", (utc_heute_iso(),)
    ).fetchone():
        _json({"uebersprungen": True, "hinweis": "heute schon aufgeräumt"})
        return 0
    konfig.pruefe_speicher()
    aktionen = aufraeumen.plane(con, konfig)
    ergebnis: dict = {"probelauf": not args.ausfuehren, "geplant": {}}
    for a in aktionen:
        ergebnis["geplant"][a.ziel] = ergebnis["geplant"].get(a.ziel, 0) + 1
    if args.ausfuehren:
        ergebnis["erledigt"] = aufraeumen.fuehre_aus(con, konfig, aktionen)
        db.protokoll(con, "aufraeumen", json.dumps(ergebnis["erledigt"]))
    elif args.liste:
        ergebnis["dateien"] = [f"{a.ziel}: {konfig.relativ(a.pfad)}" for a in aktionen[:200]]
    _json(ergebnis)
    return 0


def _cmd_big(args, konfig, con) -> int:
    """Sicherheitsnetz für pve-big: status | pruefen | waechter | aus | halten | loesen."""
    if args.aktion == "halten":
        m = big.setze_marke(konfig, args.name, args.minuten, args.grund or "von Hand")
        _json({"halten": m.name, "bis": iso(m.bis)})
        return 0
    if args.aktion == "loesen":
        big.loese_marke(konfig, args.name)
        _json({"geloest": args.name})
        return 0
    if not big.host(konfig):
        _json({"fehler": "nicht_eingerichtet", "hinweis": "[big].host bzw. [speicher].host fehlt"})
        return 3
    if args.aktion == "status":
        e = big.pruefe(konfig)
        _json({"wach": e.wach, "wuerde_aus": e.aus, "grund": e.grund, "wecken": big.darf_wecken(konfig) or "erlaubt",
               "marken": [m.name for m in big.marken(konfig)], "zustand": big.lies_zustand(konfig)})
        return 0
    if args.aktion == "pruefen":
        if not big.wach(konfig):
            _json({"fehler": "schlaeft", "hinweis": "pruefen geht nur, wenn pve-big läuft"})
            return 3
        _json({"ok": True, "status": big.fern_status(konfig)})
        return 0
    if args.aktion == "aus":
        e = big.pruefe(konfig)
        if not e.wach:
            _json({"aus": True, "grund": "schläft schon"})
            return 0
        if not (e.aus or args.sofort):
            _json({"aus": False, "grund": e.grund, "hinweis": "--sofort erzwingt es"})
            return 1
        ok = big.herunterfahren(konfig, "von Hand" if args.sofort else e.grund)
        _json({"aus": ok})
        return 0 if ok else 1
    ergebnis = big.waechter(konfig)  # waechter
    if alarm := ergebnis.get("alarm"):
        log.error(alarm)
        db.lern_meldung(con, f"big-alarm:{jetzt():%Y-%m-%dT%H}", f"🚨 pve-big: {alarm} ({ergebnis['grund']})")
    elif ergebnis["aus"]:
        log.info("pve-big heruntergefahren: %s", ergebnis["grund"])
    _json(ergebnis)
    return 0


def _cmd_bestand(args, konfig, con) -> int:
    ergebnis = bestand.erstelle(konfig, weitere=[Path(p) for p in args.pfad], stichprobe=args.stichprobe,
                                messen=not args.ohne_messung)
    if args.bericht:
        Path(args.bericht).write_text(bestand.als_markdown(ergebnis), encoding="utf-8")
        log.info("Bericht: %s", args.bericht)
    _json(ergebnis)
    return 0


def _cmd_material(args, konfig, con) -> int:
    try:
        ergebnis = material.hole(konfig, con, probelauf=args.probelauf)
    except big.WeckenVerboten as e:
        _json({"fehler": "wecken_verboten", "hinweis": str(e)})
        return 3
    _json(ergebnis)
    return 1 if ergebnis.get("fehler") else 0


def _cmd_lager(args, konfig, con) -> int:
    """Puffer ↔ Lager (E19). Exit: 0 ok · 1 Datei-Fehler (Übernahme auch: Konflikt, zu jung) · 2 Aufruf/Konfig ·
    3 Lager offline/nicht geweckt · 4 Lager-Sperre belegt (Gesperrt, in main). Der Probelauf endet ohne Abbruch mit 0,
    ebenso ein Abgleich, der in der Nachtruhe nicht wecken durfte ("nachtruhe": true).
    Die Übernahme läuft vor dem Umschalten, also auch ohne [lager]."""
    from . import lager

    if args.aktion != "uebernehmen" and not konfig.getrennt:
        log.error("kein getrennter Betrieb: [lager].wurzel leer")
        _json({"fehler": "kein getrennter Betrieb: [lager].wurzel leer"})
        return 2
    try:
        if args.aktion == "status":
            stand = lager.status(con, konfig)
            log.info("%s", stand["zeile"])
            _json(stand)
            return 0 if stand["pruefung"] == "ok" else 2
        if args.aktion == "abgleich":
            ergebnis = lager.abgleich(con, konfig, probelauf=args.probelauf)
        else:
            tage = args.eingang_tage if args.eingang_tage is not None else int(konfig.wert("puffer.rohdaten_tage", 14))
            ergebnis = lager.uebernahme(con, konfig, Path(args.von), Path(args.nach), tage,
                                        probelauf=args.probelauf)
    except KonfigFehler as e:  # z. B. Puffer und Lager verwechselbar – dann wurde nichts kopiert
        log.error("%s", e)
        _json({"fehler": "konfig", "hinweis": str(e)})
        return 2
    except big.BigFehler as e:  # auch WeckenVerboten
        log.error("pve-big nicht geweckt: %s", e)
        _json({"fehler": "nicht_geweckt", "hinweis": str(e)})
        return 3
    _json(ergebnis)
    if ergebnis.get("abbruch"):
        return 3
    if ergebnis.get("probelauf"):  # zeigt nur, was geschähe
        return 0
    # zu_jung (Übernahme): im Lager wird evtl. noch geschrieben – noch nicht fertig, in ein paar Minuten wiederholen
    return 1 if ergebnis.get("fehler") or ergebnis.get("konflikte") or ergebnis.get("zu_jung") else 0


def _cmd_puffer(args, konfig, con) -> int:
    """Morgenprüfung (E19): pruefen legt Meldungen an (je Thema und Tag eine), status zeigt nur. Weckt nie.
    Exit: 0 ok · 1 ein Thema ließ sich nicht prüfen · 2 kein getrennter Betrieb."""
    from . import puffer

    if not konfig.getrennt:
        log.error("kein getrennter Betrieb: [lager].wurzel leer")
        _json({"fehler": "kein getrennter Betrieb: [lager].wurzel leer"})
        return 2
    stand = puffer.status(con, konfig)
    log.info("%s", stand["zeile"])
    if args.aktion == "status":
        _json(stand)
    else:
        neu = puffer.melde(con, konfig, stand)
        _json({"neu": neu, "befunde": stand["befunde"], "zeile": stand["zeile"], "fehler": stand["fehler"]})
    return 1 if stand["fehler"] else 0


def _cmd_stimmung(args, konfig, con) -> int:
    if args.clips:  # Mic-Schritt (Spec §8.2): nur Clips, ohne Claude; ohne getrennten Betrieb lehnt main vorab ab
        session = verarbeitung.pruefe_id(args.session) if args.session else None
        _json(mikro.clips_nachziehen(con, konfig, session=session, maximal=args.max))
        return 0
    if args.session:
        _json({"fehler": "--session gilt nur zusammen mit --clips"})
        return 2
    _json(stimmung.analysiere(con, konfig, dateien=args.dateien, neu=args.neu, claude=not args.ohne_claude,
                              whisper=not args.ohne_whisper, maximal=args.max))
    return 0


def _cmd_merkmale(args, konfig, con) -> int:
    """`pipeline merkmale nachtragen [--session ID]` (Stufe 2): Replay- und Mic-Merkmale für vorhandene Clips aus dem
    Puffer nachrechnen (sessions/<ID>/replay.json, momente-Zeilen). Ohne Whisper, weckt nie, idempotent.
    Punkte ändern sich nur bei Clips im Status vorbewertet (Annahme S2-A5). Exit: 0 ok · 1 ungültige Session-ID ·
    2 kein getrennter Betrieb (lehnt main vorab ab)."""
    session = verarbeitung.pruefe_id(args.session) if args.session else None
    version, gewichte = lernen.aktuelle(con, konfig)  # einmal holen, durchreichen (Import-Regel, Leitplanke 7)
    _json({"replay": merkmale.nachtragen_replay(con, konfig, gewichte, version, session=session),
           "mic": mikro.nachtragen(con, konfig, gewichte, version, session=session)})
    return 0


def _cmd_momente(args, konfig, con) -> int:
    """Vorhandene Multikill-Momente aus dem Puffer neu schneiden (Aktion drin). Nur getrennter Betrieb, weckt nie.
    Exit: 0 ok · 1 mindestens ein Moment mit Fehler · 2 Konfig (kein getrennter Betrieb, Puffer-Marke fehlt)."""
    from . import nachschnitt

    tage = args.tage if args.tage is not None else int(konfig.wert("puffer.rohdaten_tage", 14))
    try:
        ergebnis = nachschnitt.nachschneiden(con, konfig, tage=tage, probe=args.probe)
    except KonfigFehler as e:
        log.error("%s", e)
        _json({"fehler": "konfig", "hinweis": str(e)})
        return 2
    _json(ergebnis)
    return 1 if ergebnis["fehler"] else 0


def _cmd_musik(args, konfig, con) -> int:
    from . import musik  # braucht numpy

    if args.aktion == "analysieren":
        a = musik.analysiere(Path(args.datei))
        _json({k: v for k, v in a.items() if k not in ("beats", "verlauf")} | {"beats": len(a["beats"]),
              "stimmungen": musik.passende_stimmungen(a["bpm"], a["energie"])[:2]})
    elif args.aktion == "hinzufuegen":
        if not args.quelle:
            _json({"fehler": "--quelle (Quellenangabe/Lizenz) fehlt"})
            return 2
        t = musik.hinzufuegen(con, konfig, Path(args.datei), titel=args.titel or Path(args.datei).stem,
                              kuenstler=args.kuenstler, quelle=args.quelle)
        _json({"id": t["id"], "datei": t["datei"], "bpm": t["bpm"], "energie": t["energie"]})
    elif args.aktion == "ncs":
        neu = musik.ncs_laden(con, konfig, args.stimmung, args.anzahl)
        _json({"neu": [{"titel": t["titel"], "kuenstler": t["kuenstler"], "bpm": t["bpm"], "energie": t["energie"]}
                       for t in neu]})
    else:
        zeilen = con.execute("SELECT id, titel, kuenstler, bpm, energie, stimmungen FROM tracks ORDER BY id").fetchall()
        for z in zeilen:
            print(f"{z['id']:>3}  {z['bpm'] or 0:>5.1f} BPM  E {z['energie'] or 0:.2f}  {z['stimmungen']}  "
                  f"{z['kuenstler'] or '?'} – {z['titel']}", file=sys.stderr)
        _json({"tracks": len(zeilen)})
    return 0


def _cmd_compose(args, konfig, con) -> int:
    from . import regie, regie_lernen

    parameter, ziel = regie_lernen.aktuelle(con, konfig)
    try:
        ergebnis = regie.erstelle(con, konfig, args.format, parameter=parameter, ziel=ziel, name=args.name)
    except regie.RegieFehler as e:
        _json({"fehler": str(e)})
        return 1
    _json(ergebnis)
    return 0


def _cmd_render_entwurf(args, konfig, con) -> int:
    from . import entwurf

    if args.final:
        try:
            _json(entwurf.final_auf_big(con, konfig, args.entwurf))
        except big.WeckenVerboten as e:
            _json({"fehler": "wecken_verboten", "hinweis": str(e)})
            return 3
        except KonfigFehler as e:  # getrennter Betrieb: --final geht (noch) nicht, nichts geweckt
            log.error("%s", e)
            _json({"fehler": "konfig", "hinweis": str(e)})
            return 2
        return 0
    if args.messen:  # Renderzeit messen: Temp-Datei, danach weg, Datenbank unverändert
        _json(entwurf.messen(con, konfig, args.entwurf))
        return 0
    _json(entwurf.entwurf(con, konfig, args.entwurf))
    return 0


def _cmd_render_final(args, konfig, con) -> int:
    """Läuft auf pve-big (per clip-big-steuer): rendert einen Auftrag in voller Qualität mit NVENC."""
    from . import entwurf
    from .verarbeitung import pruefe_id

    auftrag = konfig.wurzel / str(konfig.wert("regie.auftraege", "regie/auftraege")) / f"{pruefe_id(args.name)}.json"
    _json(entwurf.fuehre_final_aus(konfig, auftrag))
    return 0


def _cmd_entwurf_neu(args, konfig, con) -> int:
    """compose + render-entwurf in einem Schritt (z. B. für einen Timer); der Lern-Bot schickt ihn dann."""
    from . import entwurf, regie, regie_lernen

    parameter, ziel = regie_lernen.aktuelle(con, konfig)
    try:
        e = regie.erstelle(con, konfig, args.format, parameter=parameter, ziel=ziel)
    except regie.RegieFehler as fehler:
        _json({"fehler": str(fehler)})
        return 1
    _json({**e, **entwurf.entwurf(con, konfig, e["entwurf"])})
    return 0


def _cmd_lernbot(args, konfig, con) -> int:
    from .lernbot import starte  # braucht python-telegram-bot

    con.close()
    return starte(konfig)


def _cmd_lernbot_sende(args, konfig, con) -> int:
    """Nachricht über den Lern-Bot (Stand, Rückfrage, Abschlussbericht). Je Schlüssel nur einmal."""
    text = Path(args.datei).read_text(encoding="utf-8") if args.datei else (args.text or "")
    if not text.strip():
        _json({"fehler": "--text oder --datei fehlt"})
        return 2
    schluessel = args.schluessel or f"hand:{jetzt():%Y-%m-%dT%H:%M:%S}"
    _json({"neu": db.lern_meldung(con, schluessel, text.strip()), "schluessel": schluessel})
    return 0


def _cmd_sitzungen(args, konfig, con) -> int:
    from . import sitzung

    _json(sitzung.verarbeite(con, konfig, claude=not args.ohne_claude))
    return 0


def _cmd_bewerte(args, konfig, con) -> int:
    """Einen Entwurf ohne Telegram bewerten (gleiche Wirkung wie die Knöpfe im Lern-Bot)."""
    from . import regie_lernen

    if con.execute("SELECT 1 FROM entwuerfe WHERE id = ?", (args.entwurf,)).fetchone() is None:
        _json({"fehler": f"Entwurf {args.entwurf} unbekannt"})
        return 1
    b = regie_lernen.bewerte(con, args.entwurf, daumen=1 if args.gut else -1)
    for grund in args.grund:
        if grund not in json.loads(b["gruende"]):
            b = regie_lernen.bewerte(con, args.entwurf, grund=grund)
    print(regie_lernen.lernstand_text(con, konfig), file=sys.stderr)
    _json({"entwurf": args.entwurf, "daumen": b["daumen"], "gruende": json.loads(b["gruende"])})
    return 0


def _cmd_lernstand(args, konfig, con) -> int:
    from . import regie_lernen

    print(regie_lernen.lernstand_text(con, konfig), file=sys.stderr)
    parameter, ziel = regie_lernen.aktuelle(con, konfig)
    _json({"parameter": parameter, "musik_ziele": ziel})
    return 0


def _cmd_bot(args, konfig, con) -> int:
    from .bot.app import starte  # erst hier: der Rest braucht python-telegram-bot nicht

    con.close()
    return starte(konfig)


def _cmd_publikum(args, konfig, con) -> int:
    """Lernschleife „Publikum“ (Spec §6, §12): `pipeline publikum bewerten` – täglich per Timer clip-publikum.

    Setzt die Publikums-Scores aller fälligen Posts (publikum.bewerte_alle: ab [publikum].alter_tage, einmal je
    Post, nie überschrieben) und legt bei neuen Scores eine Lern-Meldung an (höchstens eine am Tag). Reine
    Datenbank-Arbeit: keine Pipeline-Sperre (sperren=False), nicht in WECKEN – weckt pve-big nie.
    Ein Zeitpunkt für den ganzen Lauf: Fälligkeit, bewertet_utc und das Datum der Meldung passen zusammen.
    JSON: {"bewertet", "ohne_messung", "noch_zu_jung", "fehler", "posts": [{"id", "score"}], "meldung": bool}.
    Exit: 0 ok (auch: nichts fällig) · 1 mindestens ein Post nicht bewertbar (steht mit #Nummer im Log; die anderen
    sind trotzdem bewertet, die JSON-Zeile kommt trotzdem) · 2 Konfig ([publikum]-Schlüssel fehlt)."""
    from . import lernbot_publikum, publikum  # erst hier: die anderen Befehle brauchen die Lernschleife nicht

    zeit = jetzt()
    try:
        ergebnis = publikum.bewerte_alle(con, konfig, zeit)
    # KonfigFehler: ein [publikum]-Schlüssel fehlt ganz (auch in pipeline.toml). Ein Tippfehler in lokal.toml
    # (z. B. „alter_tag“) landet NICHT hier – dann gilt still der Standard; ein Wert, der keine Zahl ist
    # (alter_tage = "drei"), endet als ValueError in main („Unerwarteter Fehler“, Exit 1).
    except KonfigFehler as e:
        log.error("%s", e)
        _json({"fehler": "konfig", "hinweis": str(e)})
        return 2
    ergebnis["meldung"] = lernbot_publikum.meldung_nach_bewerten(con, ergebnis, zeit)
    _json(ergebnis)
    return 1 if ergebnis["fehler"] else 0


def baue_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pipeline", description="Clip-Pipeline für Fortnite-Highlights")
    p.add_argument("--konfig", help="andere Konfigurationsdatei")
    unter = p.add_subparsers(dest="befehl", required=True)

    for name, hilfe in (("prepare", "Replay finden, Aufnahmen erfassen, Session anlegen"),
                        ("analyze", "Kills und Kandidaten bestimmen (analyse.json)"),
                        ("decide", "Schnittliste festlegen, claude -p + Schema-Prüfung (schnittliste.json)"),
                        ("render", "Clips schneiden, bewerten, Vorschau, Datenbank")):
        s = unter.add_parser(name, help=hilfe)
        s.add_argument("--session", required=True)
        s.set_defaults(fn=_cmd_schritt, sperren=True)

    s = unter.add_parser("highlight", help="Highlight-Video der letzten Tage")
    s.add_argument("--id", required=True)
    s.add_argument("--tage", type=int, default=14)
    s.set_defaults(fn=_cmd_highlight, sperren=True)

    s = unter.add_parser("scan", help="neue Aufnahmen und fertige Matches erfassen")
    s.add_argument("--verarbeiten", action="store_true", help="offene Matches gleich verarbeiten")
    s.set_defaults(fn=_cmd_scan, sperren=True)

    s = unter.add_parser("process", help="alle vier Schritte für eine Session")
    s.add_argument("session")
    s.set_defaults(fn=_cmd_process, sperren=True)

    s = unter.add_parser("replay", help="Kills eines Replays anzeigen (Kalibrierung)")
    s.add_argument("datei")
    s.set_defaults(fn=_cmd_replay, sperren=False)

    s = unter.add_parser("status", help="Überblick")
    s.set_defaults(fn=_cmd_status, sperren=False)

    s = unter.add_parser("gewichte", help="Gewichte der Vorbewertung anzeigen")
    s.add_argument("--neu", action="store_true", help="neu berechnen und speichern")
    s.set_defaults(fn=_cmd_gewichte, sperren=False)

    s = unter.add_parser("caption", help="Caption für einen Clip erzeugen")
    s.add_argument("clip", type=int)
    s.set_defaults(fn=_cmd_caption, sperren=False)

    s = unter.add_parser("short", help="Short im Hochformat für einen Clip rendern")
    s.add_argument("clip", type=int)
    s.add_argument("--layout", choices=["unschaerfe", "zuschnitt", "mit-cam"], help="statt [shorts].layout")
    s.set_defaults(fn=_cmd_short, sperren=True)

    s = unter.add_parser("aufraeumen", help="alte Dateien recyceln, Multikills archivieren")
    s.add_argument("--ausfuehren", action="store_true", help="wirklich verschieben/löschen (sonst Probelauf)")
    s.add_argument("--liste", action="store_true", help="im Probelauf die Dateien auflisten")
    s.add_argument("--taeglich", action="store_true", help="nichts tun, wenn heute schon aufgeräumt wurde")
    s.set_defaults(fn=_cmd_aufraeumen, sperren=True)

    s = unter.add_parser("bestand", help="Bestandsaufnahme: Replays, Videos, Tonspuren, VA-API, Platz")
    s.add_argument("--pfad", action="append", default=[], help="zusätzlicher Ordner (mehrfach möglich)")
    s.add_argument("--stichprobe", type=int, default=5, help="Videos je Ordner, deren Tonspuren geprüft werden")
    s.add_argument("--ohne-messung", action="store_true", help="Tonspuren nicht messen (schneller)")
    s.add_argument("--bericht", help="zusätzlich als Markdown speichern, z. B. docs/BESTAND.md")
    s.set_defaults(fn=_cmd_bestand, sperren=False)

    s = unter.add_parser("material", help="Replays, Sessions und Videos von pve-big auf den Mini kopieren (1× wecken)")
    s.add_argument("--probelauf", action="store_true", help="nur zeigen, was kopiert würde (weckt nicht)")
    s.set_defaults(fn=_cmd_material, sperren=False)

    s = unter.add_parser("lager", help="Puffer ↔ Lager auf pve-big (E19): abgleich | status | uebernehmen")
    lager_befehle = s.add_subparsers(dest="aktion", required=True)
    a = lager_befehle.add_parser("abgleich", help="Puffer → Lager mit SHA-256 (weckt pve-big nur, wenn etwas offen "
                                                  "ist – nie in der Nachtruhe)")
    a.add_argument("--probelauf", action="store_true", help="nur zeigen, was offen ist (weckt nicht, kopiert nichts)")
    lager_befehle.add_parser("status", help="offene Dateien, letzter Abgleich, Puffer und Lager frei (weckt nie)")
    a = lager_befehle.add_parser("uebernehmen", help="einmalig Lager → Puffer vor dem Umschalten (docs/PUFFER.md R4/R5)")
    a.add_argument("--von", required=True, help="Lager, z. B. /srv/big/clips")
    a.add_argument("--nach", required=True, help="Puffer, z. B. /srv/puffer")
    a.add_argument("--eingang-tage", type=int, default=None,
                   help="von eingang/ nur Dateien der letzten N Tage (Standard: [puffer].rohdaten_tage = 14)")
    a.add_argument("--probelauf", action="store_true", help="nur zählen (weckt nicht, kopiert nichts)")
    s.set_defaults(fn=_cmd_lager, sperren=False)  # eigene Lager-Sperre statt der Pipeline-Sperre

    s = unter.add_parser("puffer", help="Puffer auf dem Mini (E19): pruefen (Morgenprüfung, Meldungen) | status")
    s.add_argument("aktion", choices=["pruefen", "status"])
    s.set_defaults(fn=_cmd_puffer, sperren=False)  # weckt nie, fasst das Lager nicht an

    s = unter.add_parser("stimmung", help="Stimmung je Moment (Whisper, Lautstärke, Kills, Tod; 1× claude -p)")
    s.add_argument("--dateien", action="store_true", help="auch kurze Rohvideos ohne Clip als Momente")
    s.add_argument("--neu", action="store_true", help="schon analysierte Momente neu bewerten")
    s.add_argument("--ohne-claude", action="store_true")
    s.add_argument("--ohne-whisper", action="store_true")
    s.add_argument("--max", type=int, help="höchstens so viele (die besten zuerst), Rest beim nächsten Lauf")
    s.add_argument("--clips", action="store_true",
                   help="Mic-Schritt: Mic-Merkmale in die Clips übernehmen, fehlende per Whisper (ohne Claude, nur Puffer)")
    s.add_argument("--session", help="mit --clips: diese Session zuerst")
    s.set_defaults(fn=_cmd_stimmung, sperren=True)

    s = unter.add_parser("merkmale", help="Merkmale pflegen: nachtragen (Replay- und Mic-Merkmale für vorhandene "
                                          "Clips, nur Puffer)")
    merkmale_befehle = s.add_subparsers(dest="aktion", required=True)
    a = merkmale_befehle.add_parser("nachtragen", help="fehlende Merkmale aus replay.json und momente nachrechnen "
                                                       "(ohne Whisper, weckt nie)")
    a.add_argument("--session", help="nur diese Session")
    s.set_defaults(fn=_cmd_merkmale, sperren=False)  # reine DB-/Puffer-Arbeit, kurz: keine Pipeline-Sperre, nicht in WECKEN

    s = unter.add_parser("momente", help="Momente pflegen: nachschneiden (Multikills ab der ersten Aktion, nur Puffer)")
    momente_befehle = s.add_subparsers(dest="aktion", required=True)
    a = momente_befehle.add_parser("nachschneiden", help="Momente mit ≥ 2 Kills neu aus der Quellaufnahme im Puffer "
                                                         "schneiden (neue Dateien, weckt nie)")
    a.add_argument("--tage", type=int, default=None,
                   help="nur Clips der letzten N Tage (Standard: [puffer].rohdaten_tage = 14)")
    a.add_argument("--probe", action="store_true", help="nur zeigen, was geschähe (schneidet und schreibt nichts)")
    s.set_defaults(fn=_cmd_momente, sperren=True)  # rechenintensiv: Pipeline-Sperre; nicht in WECKEN

    s = unter.add_parser("musik", help="Musik: analysieren, hinzufügen (mit Quelle), NCS laden, Liste")
    s.add_argument("aktion", choices=["analysieren", "hinzufuegen", "ncs", "liste"])
    s.add_argument("datei", nargs="?")
    s.add_argument("--titel")
    s.add_argument("--kuenstler")
    s.add_argument("--quelle", help="Quellenangabe/Lizenz – Pflicht beim Hinzufügen")
    s.add_argument("--stimmung", choices=["episch", "spannend", "lustig", "frustriert", "chill"], default="episch")
    s.add_argument("--anzahl", type=int, default=3)
    s.set_defaults(fn=_cmd_musik, sperren=False)

    s = unter.add_parser("compose", help="Regisseur: Schnittliste mit Bogen, Musik, Schnitten auf dem Beat")
    s.add_argument("--format", choices=["zusammenschnitt", "short"], default="zusammenschnitt")
    s.add_argument("--name", help="Name des Entwurfs (sonst Format + Zeit)")
    s.set_defaults(fn=_cmd_compose, sperren=False)

    s = unter.add_parser("render-entwurf", help="Entwurf eines compose-Laufs rendern (Mini) bzw. --final beauftragen")
    s.add_argument("entwurf", type=int)
    art = s.add_mutually_exclusive_group()
    art.add_argument("--final", action="store_true", help="auf pve-big in voller Qualität (NVENC): 1× wecken, danach aus")
    art.add_argument("--messen", action="store_true",
                     help="nur Renderzeit messen: Temp-Datei, danach gelöscht, Datenbank unverändert")
    s.set_defaults(fn=_cmd_render_entwurf, sperren=True)

    s = unter.add_parser("render-final", help="(auf pve-big) Auftrag in voller Qualität rendern")
    s.add_argument("name")
    s.set_defaults(fn=_cmd_render_final, sperren=False)

    s = unter.add_parser("entwurf-neu", help="compose + Entwurf rendern (der Lern-Bot schickt ihn)")
    s.add_argument("--format", choices=["zusammenschnitt", "short"], default="short")
    s.set_defaults(fn=_cmd_entwurf_neu, sperren=True)

    s = unter.add_parser("lernbot", help="Lern-Bot starten (läuft dauerhaft, LEARN_BOT_TOKEN)")
    s.set_defaults(fn=_cmd_lernbot, sperren=False)

    s = unter.add_parser("lernbot-sende", help="Nachricht über den Lern-Bot schicken (Stand, Bericht)")
    s.add_argument("--text")
    s.add_argument("--datei", help="Textdatei, z. B. docs/ABSCHLUSSBERICHT.md")
    s.add_argument("--schluessel", help="gleicher Schlüssel = nur einmal senden")
    s.set_defaults(fn=_cmd_lernbot_sende, sperren=False)

    s = unter.add_parser("sitzungen", help="'Session vorbei' vom Gaming-PC: Stimmung + Short des Abends (weckt nicht)")
    s.add_argument("--ohne-claude", action="store_true")
    s.set_defaults(fn=_cmd_sitzungen, sperren=True)

    s = unter.add_parser("bewerte", help="Entwurf bewerten ohne Telegram (👍/👎 + Gründe)")
    s.add_argument("entwurf", type=int)
    daumen = s.add_mutually_exclusive_group(required=True)
    daumen.add_argument("--gut", action="store_true", help="👍")
    daumen.add_argument("--schlecht", action="store_true", help="👎")
    from . import regie_lernen  # die Gründe gibt es nur an einer Stelle (auch für die Bot-Knöpfe)

    s.add_argument("--grund", action="append", default=[], choices=list(regie_lernen.GRUENDE))
    s.set_defaults(fn=_cmd_bewerte, sperren=False)

    s = unter.add_parser("lernstand", help="Was hat der Regisseur gelernt? (inkl. deiner Vorgaben)")
    s.set_defaults(fn=_cmd_lernstand, sperren=False)

    s = unter.add_parser("big", help="pve-big: Status, Wächter, Herunterfahren, Halten")
    s.add_argument("aktion", choices=["status", "pruefen", "waechter", "aus", "halten", "loesen"])
    s.add_argument("name", nargs="?", default="hand", help="Name der Halten-Marke")
    s.add_argument("--minuten", type=float, default=60)
    s.add_argument("--grund")
    s.add_argument("--sofort", action="store_true", help="aus: auch wenn ein Auftrag läuft")
    s.set_defaults(fn=_cmd_big, sperren=False)

    s = unter.add_parser("bot", help="Telegram-Bot starten (läuft dauerhaft)")
    s.set_defaults(fn=_cmd_bot, sperren=False)

    # Lernschleife „Publikum“ (Spec §12). Unterbefehle wie bei `lager`; `holen` (TikTok-API) kommt in Stufe 4.
    s = unter.add_parser("publikum", help="Lernschleife Publikum: bewerten (Scores ab [publikum].alter_tage, "
                                               "Standard 7 – Timer clip-publikum)")
    publikum_befehle = s.add_subparsers(dest="aktion", required=True)
    publikum_befehle.add_parser("bewerten", help="Publikums-Scores aller fälligen Posts setzen (einmal je Post, "
                                                 "weckt nie) – bei neuen Scores eine Meldung im Lern-Bot")
    s.set_defaults(fn=_cmd_publikum, sperren=False)  # reine DB-Arbeit: keine Pipeline-Sperre, nicht in WECKEN
    return p


def _vorab_ablehnen(args, konfig) -> int | None:
    """Befehle, die in dieser Konfig nicht laufen dürfen, sofort ablehnen (Exit 2) – vor der Pipeline-Sperre.
    Sonst wartete z. B. ein noch aktiver clip-aufraeumen-Timer bis zu [sperre].warten_s auf einen laufenden render
    und endete dann mit „gesperrt“ (Exit 4, im Timer kein Fehler) statt mit dem Hinweis, ihn auszuschalten."""
    # Ohne getrennten Betrieb wäre [speicher].wurzel pve-big selbst – diese Befehle arbeiten nur im Puffer (E19)
    nur_puffer = {"momente": "momente nachschneiden", "merkmale": "merkmale nachtragen"}
    if args.befehl == "stimmung" and getattr(args, "clips", False):
        nur_puffer["stimmung"] = "stimmung --clips"
    if args.befehl in nur_puffer and not konfig.getrennt:
        hinweis = f"kein getrennter Betrieb: [lager].wurzel leer – {nur_puffer[args.befehl]} arbeitet nur im Puffer (E19)"
        log.error("%s", hinweis)
        _json({"fehler": "konfig", "hinweis": hinweis})
        return 2
    if args.befehl != "aufraeumen":
        return None
    try:
        aufraeumen.pruefe_erlaubt(konfig)  # im getrennten Betrieb (E19) gesperrt
    except KonfigFehler as e:
        log.error("%s", e)
        _json({"fehler": "konfig", "hinweis": str(e)})
        return 2
    return None


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = baue_parser().parse_args(argv)
    try:
        konfig = lade(args.konfig)
    except KonfigFehler as e:
        _json({"fehler": str(e)})
        return 2
    if (code := _vorab_ablehnen(args, konfig)) is not None:
        return code
    con = db.verbinde(konfig.datenbank)
    try:
        if args.sperren:
            warten = float(konfig.wert("sperre.warten_s", 7200))
            with sperre(konfig.datenbank.with_suffix(".lock"), warten_s=warten, melde=log.info):
                if args.befehl in WECKEN:
                    konfig.pruefe_speicher(wecken=True)
                if konfig.getrennt:
                    # Getrennter Betrieb: der Schritt arbeitet nur im Puffer. Ein Herzschlag im Lager hielte
                    # pve-big per NFS wach (Stolperfalle 11) – dort schlägt nur noch big.wach_halten.
                    return args.fn(args, konfig, con)
                with big.herzschlag(konfig, args.befehl):  # hält pve-big über clip-leerlauf wach
                    return args.fn(args, konfig, con)
        return args.fn(args, konfig, con)
    # Jeder Fehler steht auch auf stderr – der n8n-Fehler-Alarm zeigt stderr an
    except SpeicherOffline as e:
        log.error("Speicher offline: %s", e)
        _json({"fehler": "speicher_offline", "hinweis": str(e)})
        return 3
    except Gesperrt as e:
        log.error("%s", e)
        _json({"fehler": "gesperrt", "hinweis": str(e)})
        return 4
    except (KeyError, verarbeitung.SessionFehler, replay.ReplayFehler, MedienFehler) as e:
        log.error("%s", e)
        _json({"fehler": str(e).strip("'\"")})
        return 1
    except Exception as e:  # Unerwartetes: trotzdem Vertrag einhalten (JSON als letzte Zeile)
        log.exception("Unerwarteter Fehler")
        _json({"fehler": f"{type(e).__name__}: {e}"})
        return 1
    finally:
        try:
            con.close()
        except Exception:
            pass
