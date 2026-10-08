"""Kommandozeile `pipeline ...` – Vertrag mit n8n (siehe CLAUDE.md, "Schnittstelle zu n8n"):

  prepare|analyze|decide|render --session ID     highlight --id ID --tage 14
  (weitere Befehle für Handbetrieb und Timer, z. B. momente nachschneiden [--tage 14] [--probe],
   fail --session ID | --nachziehen [--tage 14], scan --verarbeiten [--max N] [--versuche N] für Freunde ohne n8n,
   benutzer pruefen|einrichten nur in der Instanz eines Freundes)

Logs gehen nach stderr; die letzte Zeile auf stdout ist genau eine JSON-Zeile.
Exit-Codes: 0 ok · 1 Fehler · 2 falscher Aufruf/Konfig · 3 Speicher offline · 4 Sperre nicht bekommen
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from pathlib import Path

from . import aufraeumen, bestand, big, caption, db, erfassung, highlight, lernen, material, replay, shorts, stimmung, verarbeitung
from . import erwartung, merkmale, mikro  # Stufe 2 (Lernschleife): Merkmale, Mic-Schritt, Erwartung
from .konfig import KonfigFehler, SpeicherOffline, lade
from .medien import MedienFehler
from .sperre import Gesperrt, SperreFehler, pfad as sperre_pfad, sperre
from .vorbewertung import MERKMAL_NAMEN, MERKMALE, zahl
from .zeit import aus_iso, iso, jetzt, utc_zu_lokal

log = logging.getLogger("pipeline")

# Befehle, die den Speicher brauchen: schläft der große Host, wird er per Wake-on-LAN geweckt
WECKEN = {"prepare", "analyze", "decide", "render", "highlight", "scan", "process", "short"}

# scan --versuche (Mehrbenutzer M34): Fehler, die nicht am Match liegen – sie zählen nie als Fehlschlag, der Lauf endet
# wie bisher (Speicher offline: Exit 3, sonst 1) und der nächste Timer-Lauf versucht es wieder
NICHT_DAS_MATCH = (SpeicherOffline, KonfigFehler, sqlite3.OperationalError)


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
        # Mic-Analyse (Whisper) im Dienst clip-mikro (Spec §8.2, §12, Rückfrage S2-R3): render schreibt nur die
        # Anstoß-Datei; ein Fehler dabei ist eine Log-Warnung – JSON-Zeile und Exit-Code bleiben gleich (n8n-Vertrag)
        mikro.anstossen(konfig, args.session)
    _json(ergebnis)
    return 0


def _cmd_highlight(args, konfig, con) -> int:
    log.info("highlight --id %s --tage %s", args.id, args.tage)
    _json(highlight.erstelle(con, konfig, args.id, args.tage))
    return 0


# --- Komfort-Befehle ------------------------------------------------------------

def _cmd_scan(args, konfig, con) -> int:
    """Ohne --max/--versuche genau wie bisher. Freunde laufen ohne n8n per Timer (Mehrbenutzer, M33–M35):
    --max N verarbeitet je Lauf nur die N ältesten offenen Matches – die gemeinsame Sperre wird so zwischen den Läufen
    frei; --versuche N gibt ein Match nach N Fehlschlägen auf (Status 'fehler'), sonst blockierte ein kaputtes ältestes
    Match alle neueren für immer. "offen" in der JSON-Zeile bleibt die volle Liste, "verarbeitet" nennt diesen Lauf."""
    ergebnis = erfassung.scan(con, konfig)
    if args.verarbeiten:
        ergebnis["verarbeitet"] = []
        offen = ergebnis["offen"]
        if args.max is not None and len(offen) > args.max:
            log.info("scan: %d von %d offenen Matches in diesem Lauf, der Rest in den nächsten", args.max, len(offen))
            offen = offen[: args.max]
        for sid in offen:
            try:
                ergebnis["verarbeitet"].append(verarbeitung.process(con, konfig, sid))
            except Exception as e:
                bekannt = isinstance(e, (verarbeitung.SessionFehler, MedienFehler, replay.ReplayFehler))
                if not bekannt and (args.versuche is None or isinstance(e, NICHT_DAS_MATCH)):
                    raise  # wie bisher: der ganze Lauf endet (main schreibt die JSON-Zeile)
                if bekannt:
                    log.error("Session %s: %s", sid, e)  # ein kaputtes Match blockiert die anderen nicht
                else:
                    log.exception("Session %s: unerwarteter Fehler", sid)   # nur mit --versuche, mit Stacktrace
                ergebnis["verarbeitet"].append(_fehlschlag(con, konfig, sid, e, args.versuche))
    _json(ergebnis)
    return 0


def _fehlschlag(con, konfig, sid: str, fehler: Exception, versuche: int | None) -> dict:
    """Eintrag in "verarbeitet" für ein gescheitertes Match – ohne --versuche wie bisher {"session", "fehler"}.
    Mit --versuche (M34/M35): jeder Fehlschlag eine Zeile in ereignisse (art 'verarbeitung_fehler'), dazu "versuch";
    ab `versuche` Fehlschlägen Status 'fehler' (scan nimmt nur 'neu'), der Grund kommt zu matches.hinweise, der Lern-Bot
    sagt es einmal ("status": "fehler"). Gelöscht wird nichts – `pipeline process <ID>` holt das Match jederzeit nach."""
    eintrag = {"session": sid, "fehler": str(fehler)[:300]}
    if versuche is None:
        return eintrag
    text = f"{type(fehler).__name__}: {fehler}"[:300]
    if con.in_transaction:   # Rest des gescheiterten Schritts – nie mitspeichern
        con.execute("ROLLBACK")
    with db.transaktion(con):
        db.protokoll(con, "verarbeitung_fehler", text, match_id=sid)
        eintrag["versuch"] = n = con.execute(
            "SELECT COUNT(*) FROM ereignisse WHERE art = 'verarbeitung_fehler' AND match_id = ?", (sid,)).fetchone()[0]
        if n >= versuche and con.execute(
                "UPDATE matches SET status = 'fehler', hinweise = COALESCE(hinweise || char(10), '') || ?, geaendert = ? "
                "WHERE id = ? AND status = 'neu'", (text, iso(jetzt()), sid)).rowcount:   # nur ein offenes Match
            db.lern_meldung(con, f"match_fehler:{sid}", _aufgegeben_text(con, konfig, sid, n))
            eintrag["status"] = "fehler"
    if "status" in eintrag:
        log.warning("Session %s: %d Fehlschläge – Status 'fehler', nachholen mit: pipeline process %s", sid, n, sid)
    return eintrag


def _aufgegeben_text(con, konfig, sid: str, n: int) -> str:
    """Lern-Meldung für ein aufgegebenes Match (M35) – ohne Fachbegriffe, mit Tag und Uhrzeit des Matches."""
    try:
        start = utc_zu_lokal(aus_iso(db.match(con, sid)["start_utc"]), konfig.wert("zeit.zeitzone", "Europe/Berlin"))
        wann = f"vom {start:%d.%m.} um {start:%H:%M} Uhr"
    except (AttributeError, TypeError, ValueError):
        wann = sid
    return (f"⚠️ Ein Match {wann} klappt nicht – ich habe es {n}-mal versucht und lasse es aus. "
            "Deine anderen Matches laufen normal weiter.")


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
    """`pipeline gewichte [--neu]`: dieselben Angaben wie /gewichte im Bot, als Klartext (Spec §8.3).
    Die Zeilen unter der Tabelle (beide Sortier-Quoten, Paare je Quelle, ohne Mic-Analyse, „Du magst …“) kommen
    aus lernen.anzeige_zeilen – eine Stelle für Bot und CLI. --neu speichert eine neue Version, wenn sich etwas
    geändert hat."""
    version, e = lernen.aktualisiere(con, konfig) if args.neu else (None, lernen.berechne(con, konfig))
    print(lernen.datenbasis_text(e))
    print(f"Status: {e.grund} · Vertrauen {round(e.vertrauen * 100)} %")
    print(f"{'Merkmal':<16}{'Start':>8}{'Aktuell':>9}")
    for m in MERKMALE:
        print(f"{MERKMAL_NAMEN[m]:<16}{zahl(e.start[m], 2):>8}{zahl(e.werte[m], 2):>9}")
    for zeile in lernen.anzeige_zeilen(e):
        print(zeile)
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
    groesse = shorts.rendere(konfig.absolut(zeile["clip_pfad"]), ziel, konfig, layout=args.layout,
                             stimmen=merkmale.stimmen_fuer_clip(con, args.clip))
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


def _cmd_sicherung(args, konfig, con) -> int:
    """Datenbank sichern (sqlite3-Backup, B1): läuft unabhängig vom Betriebsmodus, auch ohne [lager] – anders als
    der tägliche Lager-Abgleich, der die DB nur im getrennten Betrieb sichert (lager.sichere_datenbank). Reine
    Datenbank-/Speicher-Arbeit: keine Pipeline-Sperre (sperren=False), nicht in WECKEN – weckt pve-big nie."""
    from . import lager

    pfad = lager.sichere_datenbank(con, konfig)
    _json({"sicherung": pfad})
    return 0


def _cmd_kalibrieren(args, konfig, con) -> int:
    """Kalibrier-Bericht für ein echtes Match (B5, erste Stufe): Kills mit Waffen-Nummer, Merkmale, Stimmung,
    Transkript, drei Standbilder je Clip nach sessions/<ID>/kalibrierung/. Weckt nie (nur Puffer)."""
    from . import kalibrierung

    try:
        e = kalibrierung.bericht(con, konfig, args.session, bilder=not args.ohne_bilder, whisper=not args.ohne_whisper)
    except kalibrierung.KalibrierFehler as fehler:
        _json({"fehler": str(fehler)})
        return 2
    for c in e["clips"]:
        log.info("%s", kalibrierung.clip_text(c).replace("\n", " · "))
    _json({"session": e["session"], "clips": len(e["clips"]), "bilder": sum(len(c["bilder"]) for c in e["clips"]),
           "waffen_unbekannt": e["waffen_unbekannt"], "hinweise": e["hinweise"],
           "datei": f"{e['ordner']}/bericht.json"})
    return 0


def _cmd_kritik(args, konfig, con) -> int:
    """Cutter-Maßstab: einen Entwurf (neu) benoten – Messung am Video, Teilnoten, Tore, KI-Cutter (Tageslimit),
    Note mit den aktuellen Faktoren. --neu misst auch dann neu, wenn messung.json schon passt. Weckt nie."""
    from . import kritik

    try:
        e = kritik.bewerte(con, konfig, args.id, neu_messen=args.neu)
    except (ValueError, OSError) as fehler:
        _json({"fehler": str(fehler)})
        return 1
    log.info("%s", kritik.kritik_zeile(con, args.id))
    _json({"entwurf": args.id, "score": e["score"], "regel_score": e["regel_score"], "ki_score": e["ki_score"],
           "mess_version": e["mess_version"], "tor": e["tor"], "gemessen": e["gemessen"],
           "verluste": [v[0] for v in e["verluste"]]})
    return 0


def _cmd_massstab(args, konfig, con) -> int:
    """Cutter-Maßstab: Faktoren nachlernen und zeigen. --nachmessen misst alte Entwürfe, deren Video im Puffer liegt
    (nur lesen, ohne neuen KI-Aufruf – vorhandene KI-Urteile bleiben und bilden sofort Paare), neueste zuerst,
    höchstens --max; danach einmal lernen und die Abnahme (Spec §4) ausgeben. Weckt nie, löscht nichts."""
    from . import kritik, massstab

    ergebnis: dict = {}
    if args.nachmessen:
        gemessen, fehler, uebrig = 0, 0, 0
        zeilen = con.execute("""SELECT e.id, e.datei FROM entwuerfe e LEFT JOIN kritiken k ON k.entwurf_id = e.id
                                 WHERE e.datei IS NOT NULL AND COALESCE(k.mess_version, 0) < 1
                                 ORDER BY e.id DESC""").fetchall()
        for z in zeilen:
            if not Path(z["datei"]).is_file():
                continue
            if args.max is not None and gemessen + fehler >= args.max:
                uebrig += 1
                continue
            try:
                e = kritik.bewerte(con, konfig, int(z["id"]), ki=False, lernen=False)
                gemessen += 1
                log.info("Entwurf #%s: %s (Messung v%s)", z["id"], e["regel_score"], e["mess_version"])
            except Exception as ausnahme:  # noqa: BLE001 – ein kaputter alter Entwurf hält die anderen nicht auf
                fehler += 1
                log.warning("Entwurf #%s nicht messbar: %s", z["id"], ausnahme)
        ergebnis.update(gemessen=gemessen, fehler=fehler, uebrig=uebrig, abnahme=kritik.abnahme(con))
        a = ergebnis["abnahme"]
        log.info("Abnahme: %s gemessen · σ %s · Spannweite der Stile %s · %s", a["n"], a["sigma"], a["spannweite"],
                 " · ".join(f"{s} {m}" for s, m in sorted(a["stile"].items(), key=lambda x: -x[1])))
        if a["trennt_nicht"]:
            log.info("Trennt nicht (σ < 0,1): %s", ", ".join(a["trennt_nicht"]))
    version, stand = massstab.aktualisiere(con, konfig)
    if args.zeigen or not args.nachmessen:
        for zeile in massstab.lernstand_zeilen(con, konfig):
            print(zeile, file=sys.stderr)
    ergebnis.update(version=version, aktiv=bool(stand["aktiv"]), grund=stand["grund"],
                    datenbasis=stand["datenbasis"], faktoren=stand["faktoren"])
    _json(ergebnis)
    return 0


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
        ergebnis = mikro.clips_nachziehen(con, konfig, session=session, maximal=args.max)
        # Fail-Format (05.10.): neue Matches bekommen hier – nach render, im Hintergrund – ihre Fail-Momente. Nur ins
        # Log: die JSON-Zeile des Mic-Schritts bleibt gleich, ein Fehler dort kostet den Mic-Schritt nie.
        from . import fail

        log.info("Fail-Momente: %s", fail.nach_render(con, konfig, session))
        _json(ergebnis)
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
    Punkte und Begründung werden bei jedem Status neu gerechnet (Rückfrage S2-R6). Exit: 0 ok · 1 ungültige Session-ID ·
    2 kein getrennter Betrieb (lehnt main vorab ab)."""
    session = verarbeitung.pruefe_id(args.session) if args.session else None
    version, gewichte = lernen.aktuelle(con, konfig)  # einmal holen, durchreichen (Import-Regel, Leitplanke 7)
    _json({"replay": merkmale.nachtragen_replay(con, konfig, gewichte, version, session=session),
           "mic": mikro.nachtragen(con, konfig, gewichte, version, session=session)})
    return 0


def _cmd_fail(args, konfig, con) -> int:
    """Fail-Momente (05.10.): je eigenem Tod ein Moment aus dem Rohvideo im Puffer (fail.py). --session ID für ein Match,
    --nachziehen für alle der letzten --tage Tage. Danach Whisper für ein paar Fail-Momente ([fail].mic_je_lauf).
    Nur getrennter Betrieb, weckt nie, löscht nichts. Exit: 0 ok · 1 mindestens ein Fehler · 2 Konfig/Aufruf."""
    from . import fail

    try:
        if args.nachziehen:
            tage = args.tage if args.tage is not None else int(konfig.wert("puffer.rohdaten_tage", 14))
            ergebnis = fail.nachziehen(con, konfig, tage=tage)
        elif args.session:
            ergebnis = fail.fail_session(con, konfig, verarbeitung.pruefe_id(args.session))
        else:
            _json({"fehler": "Aufruf: pipeline fail --session ID oder pipeline fail --nachziehen [--tage 14]"})
            return 2
    except KonfigFehler as e:
        log.error("%s", e)
        _json({"fehler": "konfig", "hinweis": str(e)})
        return 2
    ergebnis["mic"] = fail.mic_nachziehen(con, konfig)
    _json(ergebnis)
    return 1 if ergebnis["fehler"] else 0


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
        if args.genre:  # 27.09.: nach Genre statt nach Stimmung, z. B. --genre techno,electronic-rock
            genres = musik.HART if args.genre == "hart" else [g.strip() for g in args.genre.split(",") if g.strip()]
            neu = musik.ncs_genres_laden(con, konfig, genres, args.anzahl)
        else:
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

    parameter, ziel = regie_lernen.aktuelle(con, konfig, args.format)
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

    parameter, ziel = regie_lernen.aktuelle(con, konfig, args.format)
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
    from . import massstab

    massstab.nachziehen(con, konfig, "👍/👎")         # Cutter-Maßstab: dein Urteil ist ein Lehrer
    print(regie_lernen.lernstand_text(con, konfig), file=sys.stderr)
    _json({"entwurf": args.entwurf, "daumen": b["daumen"], "gruende": json.loads(b["gruende"])})
    return 0


def _cmd_warum(args, konfig, con) -> int:
    """Nur lesen (warum.py): Text nach stderr, JSON-Zeile mit den Kernzahlen nach stdout."""
    from . import warum

    print(warum.text(con, konfig), file=sys.stderr)
    _json({"ok": True})
    return 0


def _cmd_lernstand(args, konfig, con) -> int:
    from . import autonom, regie_lernen

    print(regie_lernen.lernstand_text(con, konfig), file=sys.stderr)
    # Zusatz wie HILFE_ZUSATZ: der Regie-Lernstand bleibt unverändert, die Trefferquote der Erwartung (Spec §10.5)
    # kommt dahinter – nur, wenn es schon geurteilte Erwartungen gibt
    if zusatz := erwartung.trefferquote_text(con, konfig):
        print(zusatz, file=sys.stderr)
    parameter, ziel = regie_lernen.aktuelle(con, konfig, "short")
    _json({"autonom": autonom.ueberblick(con), "parameter": parameter, "musik_ziele": ziel,
           "parameter_zusammenschnitt": regie_lernen.aktuelle(con, konfig, "zusammenschnitt")[0]})
    return 0


def _cmd_benutzer(args, konfig, con) -> int:
    """Mehrbenutzer (Schritt 7): die Instanz eines Freundes prüfen (nur nachsehen) bzw. einrichten (Datenbank, Whisper,
    Musik, danach prüfen) – nur mit CLIP_INSTANZ, gestartet von clip-freund-pruefen@/-einrichten@ in seiner Sandbox.
    JSON: ok, name, befunde ({pfad, grund}), hinweise, sperre ({pfad, dev, ino}), bei einrichten auch eingerichtet.
    Exit 0 ok · 1 Befund · 2 nicht in einer Instanz. Ohne Datenbank-Verbindung von main (ohne_db): pruefen legt nichts
    an, einrichten öffnet seine Datenbank selbst."""
    from . import benutzer

    if konfig.instanz is None:
        hinweis = ("pipeline benutzer läuft nur in der Instanz eines Freundes (CLIP_INSTANZ) – über "
                   "deploy/benutzer/benutzer-anlegen.sh bzw. benutzer-pruefen.sh")
        log.error("%s", hinweis)
        _json({"fehler": "konfig", "hinweis": hinweis})
        return 2
    ergebnis = benutzer.einrichten(konfig) if args.aktion == "einrichten" else benutzer.pruefen(konfig)
    for b in ergebnis["befunde"]:
        log.error("Befund: %s – %s", b["pfad"], b["grund"])
    for hinweis in ergebnis["hinweise"]:
        log.info("Hinweis: %s", hinweis)
    _json(ergebnis)
    return 0 if ergebnis["ok"] else 1


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
    from . import autonom, lernbot_publikum, publikum, publikum_adapter

    zeit = jetzt()
    if args.aktion == "anmelden":  # 30.09.: TikTok-Konto verbinden – Tokens nur in die private Token-Datei
        from . import tiktok_anmeldung

        try:
            if not args.code:
                url = tiktok_anmeldung.anmelde_url(konfig)
                log.info("Öffnen, zustimmen, dann: pipeline publikum anmelden --code '<Adresse aus der Adresszeile>'")
                _json({"url": url, "redirect_uri": tiktok_anmeldung.redirect_uri(konfig)})
                return 0
            _json({"verbunden": True, **tiktok_anmeldung.tausche(konfig, args.code, zeit)})
            return 0
        except tiktok_anmeldung.AnmeldeFehler as e:
            log.error("%s", e)
            _json({"verbunden": False, "fehler": str(e)})
            return 1
    if args.aktion == "importieren":
        antwort = json.loads(Path(args.datei).read_text(encoding="utf-8"))
        mid = publikum_adapter.importiere(con, konfig, args.post, antwort, zeit=zeit)
        _json({"messung": mid, "autonom": autonom.ueberblick(con)})
        return 0
    api = publikum_adapter.abrufen(con, konfig, zeit=zeit)
    try:
        ergebnis = publikum.bewerte_alle(con, konfig, zeit)
    # KonfigFehler: ein [publikum]-Schlüssel fehlt ganz (auch in pipeline.toml). Ein Tippfehler in lokal.toml
    # (z. B. „alter_tag“) landet NICHT hier – dann gilt still der Standard; ein Wert, der keine Zahl ist
    # (alter_tage = "drei"), endet als ValueError in main („Unerwarteter Fehler“, Exit 1).
    except KonfigFehler as e:
        log.error("%s", e)
        _json({"fehler": "konfig", "hinweis": str(e)})
        return 2
    # Befund B-5: Die Meldung hängt nicht vom Lernen ab – deshalb VOR dem Lernen. Fliegt dort ein sqlite3-Fehler
    # durch (z. B. IntegrityError, weil der Clip-Bot gleichzeitig dieselbe Gewichts-Version schrieb), ist sie schon da.
    ergebnis["meldung"] = lernbot_publikum.meldung_nach_bewerten(con, ergebnis, zeit)
    if ergebnis["bewertet"]:
        # Annahme S2-A12: neue Publikums-Scores sind neue Paare → gleich neu lernen (wie der Clip-Bot nach jeder
        # Entscheidung). Nur ins Log – die JSON-Zeile bleibt, wie sie ist.
        # Scheitert das Lernen an kaputten Daten, bleiben die Scores trotzdem gesetzt und die JSON-Zeile kommt (Vertrag
        # mit dem Timer); sqlite3-Fehler fliegen wie überall durch.
        try:
            version, gelernt = lernen.aktualisiere(con, konfig)
            log.info("Gewichte Version %s (%s)", version, gelernt.grund)
        except (ValueError, KeyError, TypeError) as fehler:
            log.warning("Lernen nach dem Bewerten fehlgeschlagen (%s: %s)", type(fehler).__name__, fehler)
    ergebnis["api"] = api
    ergebnis["autonom"] = autonom.aktualisieren(con, konfig)
    from . import massstab

    massstab.nachziehen(con, konfig, "Publikum")   # Cutter-Maßstab: nur ins Log, die JSON-Zeile bleibt, wie sie ist
    _json(ergebnis)
    return 1 if ergebnis["fehler"] or api["fehler"] else 0


def _ab_eins(text: str) -> int:
    """argparse-Typ für scan --max/--versuche: ganze Zahl ab 1 (0 hieße „nichts verarbeiten“ bzw. „sofort aufgeben“)."""
    try:
        wert = int(text)
    except ValueError:
        wert = 0
    if wert < 1:
        raise argparse.ArgumentTypeError(f"ganze Zahl ab 1 erwartet, nicht {text!r}")
    return wert


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
    # Mehrbenutzer (M33–M35): Freunde laufen ohne n8n per Timer – ohne die beiden Schalter alles wie bisher
    s.add_argument("--max", type=_ab_eins, metavar="N",
                   help="mit --verarbeiten: je Lauf nur die N ältesten offenen Matches")
    s.add_argument("--versuche", type=_ab_eins, metavar="N",
                   help="mit --verarbeiten: nach N Fehlschlägen Status 'fehler' (nachholen: pipeline process ID)")
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

    s = unter.add_parser("kalibrieren", help="ein echtes Match zum Nachprüfen: Kills mit Waffen-Nummer, Merkmale, "
                                               "Stimmung, Transkript, 3 Standbilder je Clip (B5; weckt nie)")
    s.add_argument("--session", required=True, help="Match-ID, z. B. 2026-09-28_21-42-22")
    s.add_argument("--ohne-bilder", action="store_true")
    s.add_argument("--ohne-whisper", action="store_true")
    s.set_defaults(fn=_cmd_kalibrieren, sperren=True)  # Whisper ist rechenintensiv: Pipeline-Sperre, nicht in WECKEN

    s = unter.add_parser("kritik", help="Cutter-Maßstab: einen Entwurf am fertigen Video benoten (Messung, Tore, "
                                          "KI-Cutter; weckt nie)")
    s.add_argument("--id", type=int, required=True, help="Entwurf-Nummer")
    s.add_argument("--neu", action="store_true", help="neu messen, auch wenn messung.json schon passt")
    s.set_defaults(fn=_cmd_kritik, sperren=True)  # ffmpeg-Messung ist rechenintensiv: Pipeline-Sperre

    s = unter.add_parser("massstab", help="Cutter-Maßstab: gelernte Gewichte nachziehen und zeigen; --nachmessen "
                                            "misst alte Entwürfe im Puffer (ohne KI, weckt nie)")
    s.add_argument("--nachmessen", action="store_true", help="alte Entwürfe ohne Messung am Video messen")
    s.add_argument("--max", type=int, help="mit --nachmessen: höchstens so viele (neueste zuerst)")
    s.add_argument("--zeigen", action="store_true", help="Lernstand des Maßstabs nach stderr")
    s.set_defaults(fn=_cmd_massstab, sperren=True)

    s = unter.add_parser("sicherung", help="Datenbank sichern (sqlite3-Backup nach <speicher>/sicherung/, Rotation "
                                            "[lager].sicherungen_behalten) – auch ohne getrennten Betrieb (B1)")
    s.set_defaults(fn=_cmd_sicherung, sperren=False)  # reine DB-/Speicher-Arbeit: keine Pipeline-Sperre, nicht in WECKEN

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

    s = unter.add_parser("fail", help="Fail-Momente: je eigenem Tod ein Moment aus dem Rohvideo im Puffer (weckt nie)")
    s.add_argument("--session", help="Match-ID, z. B. 2026-10-04_21-42-22")
    s.add_argument("--nachziehen", action="store_true", help="alle Matches der letzten --tage Tage mit Replay im Puffer")
    s.add_argument("--tage", type=int, default=None, help="mit --nachziehen (Standard: [puffer].rohdaten_tage = 14)")
    s.set_defaults(fn=_cmd_fail, sperren=True)  # schneidet Videos: Pipeline-Sperre; nicht in WECKEN

    s = unter.add_parser("musik", help="Musik: analysieren, hinzufügen (mit Quelle), NCS laden, Liste")
    s.add_argument("aktion", choices=["analysieren", "hinzufuegen", "ncs", "liste"])
    s.add_argument("datei", nargs="?")
    s.add_argument("--titel")
    s.add_argument("--kuenstler")
    s.add_argument("--quelle", help="Quellenangabe/Lizenz – Pflicht beim Hinzufügen")
    s.add_argument("--stimmung", choices=["episch", "spannend", "lustig", "frustriert", "chill"], default="episch")
    s.add_argument("--anzahl", type=int, default=3)
    s.add_argument("--genre", help="NCS-Genres, Komma-getrennt (techno, hardcore, electronic-rock, dance-rock, "
                                   "midtempo-bass, phonk, hardstyle, brazilian-phonk) oder „hart“ = die ersten fünf")
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

    s = unter.add_parser("warum", help="Sieht alles gleich aus? Material, Wiederholung, Lernen der Moment-Formel")
    s.set_defaults(fn=_cmd_warum, sperren=False)

    s = unter.add_parser("big", help="pve-big: Status, Wächter, Herunterfahren, Halten")
    s.add_argument("aktion", choices=["status", "pruefen", "waechter", "aus", "halten", "loesen"])
    s.add_argument("name", nargs="?", default="hand", help="Name der Halten-Marke")
    s.add_argument("--minuten", type=float, default=60)
    s.add_argument("--grund")
    s.add_argument("--sofort", action="store_true", help="aus: auch wenn ein Auftrag läuft")
    s.set_defaults(fn=_cmd_big, sperren=False)

    s = unter.add_parser("bot", help="Telegram-Bot starten (läuft dauerhaft)")
    s.set_defaults(fn=_cmd_bot, sperren=False)

    # Mehrbenutzer (Schritt 7): nur in der Instanz eines Freundes – die Vorlagen clip-freund-pruefen@/-einrichten@
    s = unter.add_parser("benutzer", help="Instanz eines Freundes (nur mit CLIP_INSTANZ): pruefen (Trennung, nur "
                                          "nachsehen) | einrichten (Datenbank, Whisper, Musik, danach pruefen)")
    s.add_argument("aktion", choices=["pruefen", "einrichten"])
    s.set_defaults(fn=_cmd_benutzer, sperren=False, ohne_db=True)   # einrichten nimmt die Sperre selbst (nur Musik)

    # Lernschleife „Publikum“ (Spec §12). Unterbefehle wie bei `lager`; `holen` (TikTok-API) kommt in Stufe 4.
    s = unter.add_parser("publikum", help="Lernschleife Publikum: bewerten (Scores ab [publikum].alter_tage, "
                                               "Standard 7 – Timer clip-publikum)")
    publikum_befehle = s.add_subparsers(dest="aktion", required=True)
    publikum_befehle.add_parser("bewerten", help="Publikums-Scores aller fälligen Posts setzen (einmal je Post, "
                                                 "weckt nie) – bei neuen Scores eine Meldung im Lern-Bot")
    anmelden = publikum_befehle.add_parser("anmelden", help="TikTok verbinden: ohne --code die Anmelde-Adresse, mit "
                                                            "--code die Adresse nach dem Zustimmen (oder den Code)")
    anmelden.add_argument("--code", default="")
    importer = publikum_befehle.add_parser("importieren", help="Plattform-JSON importieren und automatisch lernen")
    importer.add_argument("--post", type=int, required=True)
    importer.add_argument("--datei", required=True)
    s.set_defaults(fn=_cmd_publikum, sperren=False)  # reine DB-Arbeit: keine Pipeline-Sperre, nicht in WECKEN
    return p


def _vorab_ablehnen(args, konfig) -> int | None:
    """Befehle, die in dieser Konfig nicht laufen dürfen, sofort ablehnen (Exit 2) – vor der Pipeline-Sperre.
    Sonst wartete z. B. ein noch aktiver clip-aufraeumen-Timer bis zu [sperre].warten_s auf einen laufenden render
    und endete dann mit „gesperrt“ (Exit 4, im Timer kein Fehler) statt mit dem Hinweis, ihn auszuschalten."""
    # Ohne getrennten Betrieb wäre [speicher].wurzel pve-big selbst – diese Befehle arbeiten nur im Puffer (E19)
    nur_puffer = {"momente": "momente nachschneiden", "merkmale": "merkmale nachtragen", "fail": "fail"}
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
    # ohne_db (benutzer pruefen/einrichten): keine Verbindung vorab – sonst legte schon das Nachsehen eine Datenbank an
    con = None if getattr(args, "ohne_db", False) else db.verbinde(konfig.datenbank)
    try:
        if args.sperren:
            warten = float(konfig.wert("sperre.warten_s", 7200))
            with sperre(sperre_pfad(konfig), warten_s=warten, melde=log.info):  # eine Sperre für den ganzen Mini (M1)
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
    except SperreFehler as e:  # M1: Sperrdatei fehlt und lässt sich nicht anlegen – ohne sie rechnet nichts
        log.error("%s", e)
        _json({"fehler": "konfig", "hinweis": str(e)})
        return 2
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
            if con is not None:
                con.close()
        except Exception:
            pass
