"""Merkmale eines Moments: was im Replay und auf dem Mikro passiert ist, als Zahlen (Spec §8.1, §8.2).

Warum: Bisher kannte die Vorbewertung nur Kill-Punkte, Sieg und Länge. Ein Double gegen zwei Bots und ein Double
im Endgame nach einem Clutch bekamen dieselben Punkte. Hier entstehen die zwölf neuen Merkmale:
  - aus dem Replay (REPLAY_MERKMALE): Platzierung, Sniper-/Nahkampf-Anteil, Bot-Opfer, Match-Phase, Endgame, Clutch
  - aus Mikro und Spielton (MIC_MERKMALE): Lachen, Jubel, Frust, laute Mikro-Spitzen, Spielton-Spitzen

Lernidee in Alltagssprache: Ein Merkmal ist eine Frage an den Clip („wie viele Opfer waren Bots?“), die Antwort ist
eine Zahl. Die Gewichte sagen, wie viel jede Antwort wert ist – der Score ist die Summe (vorbewertung.roh_score).
Was die Gewichte richtig finden, lernt lernen.py aus deinen Urteilen und dem Publikum.

„Fehlt = unbekannt“ (Annahme S2-A17): Steht ein Schlüssel in clips.merkmale, wurde er gemessen (auch 0). Fehlt er,
ist er unbekannt – z. B. weil die Mic-Analyse noch nicht lief. Beim Bewerten zählt unbekannt wie 0; beim Lernen
wird ein unbekanntes Merkmal nicht verglichen (sonst lernten die Gewichte „analysiert gegen nicht analysiert“).
Ausnahme: Eine Aufnahme ohne Mikro-Spur ist gemessen – dort wurde nichts gesagt, die Mic-Werte sind 0.

clips.merkmale bleibt ein flaches Dict aus Zahlen (db.merkmale wendet float() an). Geschrieben wird es nur über
aktualisiere_clip – eine Stelle.

Import-Regel (Plan Stufe 2, Leitplanke 7; tests/test_vertrag_stufe2.py prüft sie):
    merkmale → db, replay, vorbewertung, zeit, konfig      nie: lernen, mikro, stimmung, verarbeitung
Gewichte und Gewichts-Version holt der äußerste Aufrufer einmal (lernen.aktuelle) und reicht sie hierher durch.
Nichts hier weckt pve-big: gelesen wird nur im Puffer (sessions/<ID>/replay.json) und in der Datenbank.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta

from . import db, replay, vorbewertung
from .konfig import Konfig
from .replay import Match
from .zeit import aus_iso, iso, jetzt

log = logging.getLogger("pipeline")

MIC_DECKEL = 3        # Wortzähler und Mikro-Spitzen: mehr als 3 sagt nichts Neues (Spec §8.1)
SPITZEN_DECKEL = 4    # Spielton-Spitzen (Spec §8.1)
REPLAY_MERKMALE = ("platzierung", "sniper", "nahkampf", "bot_opfer", "phase", "endgame", "clutch")
MIC_MERKMALE = ("mic_lachen", "mic_jubel", "mic_frust", "mic_laut", "spitzen")

# Die eine Zuordnung momente.merkmale → Mic-Merkmal: (Merkmal, Schlüssel in momente, Deckel).
# Die ersten vier hängen am Mikro; spitzen misst den Spielton (Spur 0) und gibt es auch ohne Mikro.
_AUS_MOMENTEN = (("mic_lachen", "lachen", MIC_DECKEL), ("mic_jubel", "jubel", MIC_DECKEL),
                 ("mic_frust", "frust", MIC_DECKEL), ("mic_laut", "jubel_laut", MIC_DECKEL),
                 ("spitzen", "spitzen", SPITZEN_DECKEL))
_MIKRO_MERKMALE = ("mic_lachen", "mic_jubel", "mic_frust", "mic_laut")


def _ist_zahl(wert: object) -> bool:
    """Nur echte Zahlen werden Merkmale. bool ist in Python ein int (True == 1) – ein Ja/Nein-Feld soll aber nicht
    still als Merkmal durchrutschen; Listen (spitzen_s), Texte (fehler) und None sind nie Merkmale."""
    return isinstance(wert, (int, float)) and not isinstance(wert, bool)


def nur_zahlen(werte: dict) -> dict[str, float]:
    """Die Zahlen eines Dicts als float, alles andere fällt weg – die eine Regel dafür (auch lernen nutzt sie).

    Parameter: werte – ein beliebiges Dict (z. B. momente.merkmale oder eingefrorene posts.merkmale).
    Rückgabe: neues Dict nur mit echten Zahlen (bool, Listen, Texte, None fallen weg). Fehler: keine.
    Beispiel: {"a": 1, "b": [2], "c": "x", "d": True} → {"a": 1.0}."""
    return {k: float(v) for k, v in werte.items() if _ist_zahl(v)}


def aus_momente(moment_merkmale: dict) -> dict[str, float]:
    """Die eine Zuordnung momente.merkmale → Mic-Merkmale (Spec §8.1), je gedeckelt.

    mic_lachen = min(lachen, 3) · mic_jubel = min(jubel, 3) · mic_frust = min(frust, 3) · mic_laut = min(jubel_laut, 3)
    · spitzen = min(spitzen, 4). `spitzen` in momente ist der ROHE Zähler, hier wird er gedeckelt.
    Fehlende Schlüssel fehlen auch im Ergebnis (unbekannt, nicht 0). Ausnahme ohne Mikro: steht `mikro_spur` im Dict,
    ist None und es gibt kein `fehler`, dann sind die vier mic_* = 0.0 (gemessen: kein Mikro, nichts gesagt).
    Beispiel: {"lachen": 5, "spitzen": 2} → {"mic_lachen": 3.0, "spitzen": 2.0}. Paket A1.

    Parameter: moment_merkmale – momente.merkmale eines Moments, wie stimmung.merkmale es schreibt (andere
    Schlüssel wie kills, energie, spitzen_s werden übergangen). Rückgabe: neues Dict, nur MIC_MERKMALE, als float.
    Fehler: keine – Werte, die keine Zahl sind, gelten als unbekannt und fehlen im Ergebnis.
    Beispiel ohne Mikro: {"mikro_spur": None, "spitzen": 6} → die vier mic_* = 0.0 und "spitzen": 4.0.
    """
    ergebnis: dict[str, float] = {}
    for merkmal, schluessel, deckel in _AUS_MOMENTEN:
        wert = moment_merkmale.get(schluessel)
        # `spitzen` in momente ist der ROHE Zähler (der Regisseur und Claude sehen ihn so); gedeckelt wird nur hier
        if _ist_zahl(wert):
            ergebnis[merkmal] = float(min(wert, deckel))
    if _ohne_mikro(moment_merkmale):
        # Gemessen, nicht unbekannt: ohne Mikro-Spur wurde nichts gesagt (Annahme S2-A17)
        ergebnis.update(dict.fromkeys(_MIKRO_MERKMALE, 0.0))
    return ergebnis


def _ohne_mikro(moment_merkmale: dict) -> bool:
    """Sicher kein Mikro: mikro_spur steht da und ist None, und die Messung ist nicht gescheitert."""
    return ("mikro_spur" in moment_merkmale and moment_merkmale["mikro_spur"] is None
            and "fehler" not in moment_merkmale)


def mic_vollstaendig(moment_merkmale: dict) -> bool:
    """True, wenn die Mic-Analyse dieses Moments fertig ist – Regel für clips.mic_stand (Annahme S2-A6).

    ("lachen" in mk) or ("mikro_spur" in mk and mk["mikro_spur"] is None and "fehler" not in mk).
    stimmung.merkmale schreibt mikro_spur = bestand.mikro_spur(...), das ist None ohne Mikro; 0 wäre ein echter
    Spur-Index. Bei einem Messfehler fehlt mikro_spur ganz, dafür steht `fehler` da.
    Beispiel: {"mikro_spur": 1, "jubel_laut": 2} → False (Mikro da, Whisper lief nicht). Paket A1.

    Parameter: moment_merkmale – momente.merkmale eines Moments. Rückgabe: bool. Fehler: keine.
    Weitere Beispiele: {"lachen": 0} → True (Whisper lief, nichts Lustiges) · {"mikro_spur": None} → True ·
    {"mikro_spur": 0} → False (0 ist die erste Spur, kein „kein Mikro“) · {"fehler": "…"} → False.
    """
    # `lachen` schreibt nur woerter() nach einem Whisper-Lauf – daran erkennt man die fertige Analyse
    return "lachen" in moment_merkmale or _ohne_mikro(moment_merkmale)


def fuer_moment(clip_merkmale: dict | None, moment_merkmale: dict | None,
                kill_tabelle: list[float]) -> dict[str, float]:
    """Merkmale eines Regisseur-Moments für roh_score (Spec §8.2).

    Clip-Moment (clip_merkmale nicht None): Zahlen aus clips.merkmale, darüber aus_momente(moment_merkmale) – die
    Mic-Werte aus momente gewinnen, weil jünger. Datei-Moment (clip_merkmale None): kill_punkte =
    punkte_fuer(max_gruppe, kill_tabelle), victory_royale aus momente, dazu aus_momente; keine Replay-Merkmale, kein
    laenge, kein lautstaerke (Annahme S2-A9: Datei-Momente haben weder Match noch Kills, stimmung.py:173).
    Nur Zahlen im Ergebnis, Listen und Texte werden verworfen.
    Beispiel: (None, {"max_gruppe": 2, "lachen": 1}, [0, 1, 3, 6, 10]) → {"kill_punkte": 3.0, "victory_royale": 0.0,
    "mic_lachen": 1.0}. Paket A1.

    Parameter: clip_merkmale – clips.merkmale (db.merkmale) oder None für einen Datei-Moment; moment_merkmale –
    momente.merkmale oder None (noch nicht analysiert); kill_tabelle – [vorbewertung].kill_punkte.
    Rückgabe: neues Dict (die Eingaben bleiben unverändert). Fehler: keine; fehlt max_gruppe, zählt es 0 (keine
    Kills), fehlt victory_royale, zählt es 0.
    Beispiel Clip-Moment: ({"kill_punkte": 3, "mic_laut": 1}, {"mikro_spur": 1, "jubel_laut": 3}, …)
    → {"kill_punkte": 3.0, "mic_laut": 3.0}.
    """
    momente = moment_merkmale or {}
    if clip_merkmale is not None:
        ergebnis = nur_zahlen(clip_merkmale)
    else:
        max_gruppe = momente.get("max_gruppe", 0)
        sieg = momente.get("victory_royale", 0)
        ergebnis = {
            "kill_punkte": vorbewertung.punkte_fuer(int(max_gruppe) if _ist_zahl(max_gruppe) else 0, kill_tabelle),
            "victory_royale": float(sieg) if _ist_zahl(sieg) else 0.0,
        }
    ergebnis.update(aus_momente(momente))  # Mic-Werte aus momente sind jünger als die in clips.merkmale
    return ergebnis


def aktualisiere_clip(con: sqlite3.Connection, clip_id: int, neue: dict[str, float],
                      gewichte: dict[str, float], version: int) -> bool:
    """Mischt die Zahlen aus `neue` in clips.merkmale – die einzige Stelle, die clips.merkmale ändert.

    Nie Listen; fehlende Schlüssel werden NICHT mit 0 angelegt. Bei Status `vorbewertet` (noch nicht gesendet,
    Annahme S2-A5) werden auch punkte, begruendung und gewichte_version neu gesetzt; sonst bleiben sie, wie der Bot
    sie gezeigt hat. Idempotent, läuft in der Transaktion des Aufrufers, holt keine Gewichte selbst.
    Rückgabe: True, wenn sich etwas geändert hat. Unbekannte clip_id → False. Paket A1.

    Parameter: con – offene Verbindung; clip_id – clips.id; neue – Merkmal → Wert (nur int/float werden übernommen,
    bool, Listen, Texte und None fallen weg); gewichte, version – vom äußersten Aufrufer (lernen.aktuelle).
    Punkte und Begründung rechnet vorbewertung.bewerte mit clips.titel – genau wie render.
    Fehler: sqlite3-Fehler gehen an den Aufrufer (seine Transaktion rollt dann zurück).
    Beispiel: Clip `vorbewertet`, merkmale {"kill_punkte": 1}, neue {"bot_opfer": 1.0}, Gewichte kill_punkte 1 und
    bot_opfer −2, version 7 → merkmale {"kill_punkte": 1, "bot_opfer": 1.0}, punkte −1.0, gewichte_version 7, True.
    Derselbe Aufruf noch einmal → False.
    """
    zeile = db.clip(con, clip_id)
    if zeile is None:
        return False
    alt = json.loads(zeile["merkmale"])
    gemischt = dict(alt)
    gemischt.update(nur_zahlen(neue))
    # 1 == 1.0 in Python: ein int aus render und derselbe Wert als float sind keine Änderung
    merkmale_neu = gemischt != alt
    punkte, begruendung, gewichte_version = zeile["punkte"], zeile["begruendung"], zeile["gewichte_version"]
    if zeile["status"] == "vorbewertet":
        # Noch nicht gesendet (Annahme S2-A5): Punkte dürfen sich ändern. Danach bleibt, was der Bot gezeigt hat.
        punkte, begruendung = vorbewertung.bewerte(gemischt, gewichte, zeile["titel"])
        gewichte_version = version
    if not merkmale_neu and (punkte, begruendung, gewichte_version) == (
            zeile["punkte"], zeile["begruendung"], zeile["gewichte_version"]):
        return False
    con.execute("UPDATE clips SET merkmale = ?, punkte = ?, begruendung = ?, gewichte_version = ?, geaendert = ?"
                " WHERE id = ?",
                (json.dumps(gemischt), punkte, begruendung, gewichte_version, iso(jetzt()), clip_id))
    return True


# --- Replay-Merkmale (Paket A2) -----------------------------------------------------------------------------

WAFFEN_KATEGORIEN = ("sniper", "nahkampf", "sonstige")  # Reihenfolge = Vorrang, falls eine Zahl doppelt eingetragen ist
# Rückfallwerte wie in config/pipeline.toml [merkmale] – nur für Konfigs ohne den Abschnitt (alte lokal.toml, Tests)
ENDGAME_SPIELER = 10
CLUTCH_VOR_S = 30.0
CLUTCH_NACH_S = 10.0
# Kill-Zeiten aus clips.kill_zeiten sind ISO-Texte mit Millisekunden (zeit.iso) – beim Zurücklesen kann eine
# Zeit bis knapp 1 ms vom Replay-Zeitpunkt abweichen. Zwei eigene Kills liegen nie so dicht, ohne gleich zu sein.
ZUORDNUNG_TOLERANZ = timedelta(milliseconds=1)


def _waffen_listen(konfig: Konfig) -> dict[str, set[int]]:
    """[merkmale.waffen] als Kategorie → Menge von GunType-Zahlen; fehlt der Abschnitt, sind alle Listen leer."""
    eintraege = konfig.wert("merkmale.waffen", {}) or {}
    return {kategorie: {int(n) for n in (eintraege.get(kategorie) or [])} for kategorie in WAFFEN_KATEGORIEN}


def waffen_kategorie(waffe: int | None, konfig: Konfig) -> str:
    """Waffen-Kategorie "sniper" | "nahkampf" | "sonstige" aus [merkmale.waffen] (Listen von GunType-ZAHLEN, replay2json).

    Rein (keine DB, keine Meldung). Unbekannte Zahl und None → "sonstige".
    Beispiel: waffe 7, [merkmale.waffen] sniper = [7] → "sniper". Paket A2.

    Parameter: waffe – GunType-Zahl eines Kills (MeinEreignis.waffe) oder None; konfig – liest nur
    [merkmale.waffen]. Rückgabe: Kategorie als Text. Fehler: keine; fehlt der Abschnitt, ist alles "sonstige".
    Steht eine Zahl in zwei Listen, gewinnt die erste in der Reihenfolge sniper, nahkampf, sonstige.
    Hinweis: FortniteReplayReader 3.1.0 liest GunType nur als Byte ohne Namen – welche Zahl welche Waffe ist,
    zeigt erst die Kalibrierung mit `pipeline replay <datei>` (docs/PUBLIKUM.md). Bis dahin ist alles "sonstige".
    """
    if waffe is None:
        return "sonstige"
    for kategorie, zahlen in _waffen_listen(konfig).items():
        if waffe in zahlen:
            return kategorie
    return "sonstige"


def _zugeordnet(ereignis_zeit: datetime, kill_zeiten: list[datetime]) -> bool:
    """Gehört ein Kill-Ereignis zu diesem Kandidaten? Gleicher Zeitpunkt (bis auf die ISO-Rundung)."""
    return any(abs(ereignis_zeit - z) <= ZUORDNUNG_TOLERANZ for z in kill_zeiten)


def _anteil(anzahl: int, gesamt: int) -> float:
    """Anteil 0..1; ohne Kills 0 (nichts da, das Sniper oder Bot sein könnte)."""
    return anzahl / gesamt if gesamt else 0.0


def _phase(zeitpunkt: datetime, match: Match) -> float:
    """Lage im Match: 0 = Replay-Start … 1 = Replay-Ende, auf 0..1 begrenzt (Annahme S2-A3).
    Das Replay beginnt, wenn ich ins Match komme, und endet, wenn ich es verlasse – es ist die Länge MEINES Matches,
    nicht die des ganzen Matches. Ohne Länge (laenge_ms 0) ist die Phase 0."""
    laenge_s = (match.ende_utc - match.start_utc).total_seconds()
    if laenge_s <= 0:
        return 0.0
    return min(1.0, max(0.0, (zeitpunkt - match.start_utc).total_seconds() / laenge_s))


def _ist_clutch(kill_zeit: datetime, ereignisse: list, vor: timedelta, nach: timedelta) -> bool:
    """Clutch: Ich war in [T − vor, T] selbst am Boden und bin in (T, T + nach] nicht gestorben.
    Endet das Replay vor T + nach, gibt es dort kein tod-Ereignis – das zählt als „nicht gestorben“."""
    am_boden = any(e.art == "knock_erlitten" and kill_zeit - vor <= e.zeit_utc <= kill_zeit for e in ereignisse)
    gestorben = any(e.art == "tod" and kill_zeit < e.zeit_utc <= kill_zeit + nach for e in ereignisse)
    return am_boden and not gestorben


def aus_replay(match: Match | None, kill_zeiten: list[datetime], konfig: Konfig) -> dict[str, float]:
    """Die sieben REPLAY_MERKMALE eines Kandidaten (Spec §8.1) – rein, liest nur [merkmale] und [merkmale.waffen].

    Kills ↔ Ereignisse über den Zeitpunkt (gleiche Zeit = gleicher Kandidat, auch beim Team-Wipe).
    platzierung = 1/platz (unbekannt 0) · sniper/nahkampf/bot_opfer = Anteil der Kills (0 Kills → 0) ·
    phase = (erste Aktion − Replay-Start) / Replay-Länge, 0..1 (Länge MEINES Replays, Annahme S2-A3) ·
    endgame = 1, wenn bei einem Kill verbleibend ≤ [merkmale].endgame_spieler ·
    clutch = 1, wenn knock_erlitten in [T − clutch_vor_s, T] und kein tod in (T, T + clutch_nach_s].
    Beispiel: 2 Kills, einer davon ein Bot, Platz 4 → {"platzierung": 0.25, "bot_opfer": 0.5, …}.

    Unbekanntes FEHLT, statt als 0 dazustehen („Schlüssel vorhanden = gemessen“, Annahme S2-A17). Sonst hielte
    nachtragen_replay den Clip für fertig und holte die echten Werte nie nach. Die Fälle:
      - kein Match (Replay unlesbar) → {}
      - Rekorder-Rückfall (match.ich_quelle None; die Ereignisse gehören nicht sicher zu mir) → nur platzierung,
        und nur, wenn sie bekannt ist
      - Kill-Zeiten da, aber keinem Ereignis zugeordnet (Clips von vor der Kill-Regel vom 24.09.: ich habe
        erledigt, der Teammate hatte umgehauen) → nur platzierung (falls bekannt) und phase, dazu log.warning
      - alle drei Listen in [merkmale.waffen] leer (noch nicht kalibriert) → sniper und nahkampf fehlen

    Parameter: match – aus replay.lies/match_aus_json oder None (Replay unlesbar); kill_zeiten – die Kill-Zeitpunkte
    des Kandidaten (vorbewertung.Kandidat.kill_zeiten oder clips.kill_zeiten; beim Team-Wipe steht dieselbe Zeit
    mehrfach da, jedes Kill-Ereignis zählt trotzdem genau einmal); konfig – [merkmale], [merkmale.waffen].
    Rückgabe: höchstens die sieben Schlüssel als float (siehe oben, welche fehlen dürfen). Fehler: keine.
    Einzelheiten: T ist der Kill-Zeitpunkt, wie ihn die Kill-Regel festlegt (beim Teammate-Finish mein Umhauen).
    Die Phase misst an der ersten AKTION (mein Umhauen) der zugeordneten Kills; ist kein Kill zugeordnet, am
    frühesten Wert aus kill_zeiten. bot_opfer zählt nur sichere Bots (opfer_bot True; null = kein Bot).
    Beispiel Clutch: 20 s vor dem Kill umgehauen, danach 10 s überlebt → clutch 1.0.
    """
    if match is None:
        return {}  # Replay unlesbar: nichts ist bekannt
    # platzierung ist je Match gleich – bekannt, sobald das Replay sie hat (None oder 0 = unbekannt)
    bekannt = {"platzierung": 1.0 / match.platzierung} if match.platzierung else {}
    if match.ich_quelle is None:
        # Rekorder-Rückfall: Die Kills stammen vom Rekorder, die Replay-Ereignisse gehören nicht sicher zu mir
        return bekannt

    kills = [e for e in match.ereignisse if e.art == "kill" and _zugeordnet(e.zeit_utc, kill_zeiten)]
    if kill_zeiten and not kills:
        # Kill-Zeiten ohne Ereignis: Waffen, Opfer, Endgame, Clutch sind unbekannt – nur die Lage im Match
        # (aus der frühesten Kill-Zeit) und die Platzierung stimmen trotzdem
        log.warning("Replay-Merkmale: keine der %d Kill-Zeiten passt zu einem Ereignis (Clip von vor der "
                    "Kill-Regel?) – nur platzierung und phase", len(kill_zeiten))
        return bekannt | {"phase": _phase(min(kill_zeiten), match)}

    ergebnis = dict.fromkeys(REPLAY_MERKMALE, 0.0) | bekannt
    kategorien = [waffen_kategorie(e.waffe, konfig) for e in kills]
    if any(_waffen_listen(konfig).values()):
        ergebnis["sniper"] = _anteil(kategorien.count("sniper"), len(kills))
        ergebnis["nahkampf"] = _anteil(kategorien.count("nahkampf"), len(kills))
    else:
        # [merkmale.waffen] noch nicht kalibriert: jede Waffe wäre „sonstige“ – das ist unbekannt, nicht 0
        del ergebnis["sniper"], ergebnis["nahkampf"]
    ergebnis["bot_opfer"] = _anteil(sum(1 for e in kills if e.opfer_bot is True), len(kills))

    erste = min((e.aktion for e in kills), default=min(kill_zeiten, default=None))
    if erste is not None:
        ergebnis["phase"] = _phase(erste, match)

    grenze = int(konfig.wert("merkmale.endgame_spieler", ENDGAME_SPIELER))
    if any(e.verbleibend is not None and e.verbleibend <= grenze for e in kills):
        ergebnis["endgame"] = 1.0

    vor = timedelta(seconds=float(konfig.wert("merkmale.clutch_vor_s", CLUTCH_VOR_S)))
    nach = timedelta(seconds=float(konfig.wert("merkmale.clutch_nach_s", CLUTCH_NACH_S)))
    if any(_ist_clutch(e.zeit_utc, match.ereignisse, vor, nach) for e in kills):
        ergebnis["clutch"] = 1.0
    return ergebnis


def _unbekannte_waffen(konfig: Konfig, match: Match | None) -> list[int]:
    """GunType-Zahlen meiner Kills, die in keiner Liste von [merkmale.waffen] stehen, aufsteigend und ohne Doppel.
    Rekorder-Rückfall und kein Match: keine (die Ereignisse gehören dann nicht sicher zu mir)."""
    if match is None or match.ich_quelle is None:
        return []
    bekannt = set().union(*_waffen_listen(konfig).values())
    return sorted({e.waffe for e in match.ereignisse if e.art == "kill" and e.waffe is not None} - bekannt)


def _waffen_text(sid: str, zahlen: list[int]) -> str:
    return (f"Neue Waffen-Nummern in Match {sid}: {', '.join(map(str, zahlen))} – zählen als sonstige. "
            "Eintragen in config/lokal.toml [merkmale.waffen]; bestimmen mit `pipeline replay <datei>`, "
            "docs/PUBLIKUM.md")


def melde_unbekannte_waffen(con: sqlite3.Connection, konfig: Konfig, sid: str, match: Match | None) -> list[int]:
    """Neue GunType-Zahlen meiner Kills als EINE Sammelmeldung je Session (Annahme S2-A4).

    Schon gemeldete Zahlen stehen als Vermerk `merkmale:waffe:<n>` in meldungen (mit gesendet = erstellt, der Bot
    verschickt sie nie). Die Sammelmeldung `merkmale:waffen:<sid>:<n1>-<n2>…` (mit den neuen Zahlen im Schlüssel)
    wartet die Ruhezeit ab (LEISE_MELDUNGEN). Die Zahlen gehören in den Schlüssel, weil db.meldung einen schon
    vorhandenen Schlüssel still übergeht: Wird replay.json derselben Session neu erzeugt und bringt eine weitere
    neue Zahl, käme sonst keine Meldung – der Vermerk stünde aber schon da.
    Rückgabe: die neu gemeldeten Zahlen, aufsteigend. Beispiel: Kills mit 12, 27, 12, nichts eingetragen → [12, 27],
    Schlüssel „merkmale:waffen:s1:12-27“.

    Parameter: con – offene Verbindung; läuft in der Transaktion des Aufrufers (analyze, nachtragen_replay);
    konfig – [merkmale.waffen]; sid – Session-ID für Schlüssel und Text; match – wie bei aus_replay.
    Fehler: sqlite3-Fehler gehen an den Aufrufer. Warum Vermerke statt einer Meldung je Zahl: Die erste Kalibrierung
    brächte sonst Dutzende Nachrichten; so kommt je Match höchstens eine, und nur mit Zahlen, die neu sind.
    Beispiel zweites Match mit 27 und 31 nach dem ersten → [31], Meldung „… in Match s2: 31 – …“.
    """
    unbekannt = _unbekannte_waffen(konfig, match)
    if not unbekannt:
        return []
    schon = {z["schluessel"] for z in con.execute(
        f"SELECT schluessel FROM meldungen WHERE schluessel IN ({', '.join('?' * len(unbekannt))})",
        [f"merkmale:waffe:{n}" for n in unbekannt])}
    neu = [n for n in unbekannt if f"merkmale:waffe:{n}" not in schon]
    if not neu:
        return []
    zeit = iso(jetzt())
    for n in neu:
        # gesendet = erstellt: ein Vermerk, keine Nachricht – aktionen.faellige_meldungen sieht nur gesendet IS NULL
        con.execute("INSERT OR IGNORE INTO meldungen (schluessel, text, erstellt, gesendet) VALUES (?, ?, ?, ?)",
                    (f"merkmale:waffe:{n}", f"Waffen-Nummer {n} gemeldet (Match {sid})", zeit, zeit))
    db.meldung(con, f"merkmale:waffen:{sid}:{'-'.join(map(str, neu))}", _waffen_text(sid, neu))
    return neu


def _fehlen_replay_merkmale(merkmale_json: str) -> bool:
    """Fehlt einem Clip mindestens eines der sieben Replay-Merkmale? (Schlüssel fehlt = nie gemessen)"""
    try:
        vorhanden = json.loads(merkmale_json)
    except json.JSONDecodeError:
        return True
    return not isinstance(vorhanden, dict) or any(m not in vorhanden for m in REPLAY_MERKMALE)


def _match_aus_puffer(konfig: Konfig, sid: str) -> Match | None:
    """sessions/<ID>/replay.json aus dem Puffer lesen (Muster stimmung._eigene_ereignisse, aber ohne material.lokal).

    Bewusst nur konfig.ordner("sessions"): Im getrennten Betrieb ist das der Puffer auf dem Mini. Kein Rückfall auf
    das Lager – ein Zugriff dort hielte pve-big per NFS wach oder hinge, wenn es schläft. None = fehlt/unlesbar."""
    pfad = konfig.ordner("sessions") / sid / "replay.json"
    if not pfad.is_file():
        return None
    try:
        roh = json.loads(pfad.read_text(encoding="utf-8"))
        return replay.match_aus_json(roh, sid, zonen_name=konfig.wert("zeit.zeitzone", "Europe/Berlin"),
                                     start_ist_ortszeit=bool(konfig.wert("zeit.replay_start_ist_ortszeit", True)))
    except (json.JSONDecodeError, replay.ReplayFehler, ValueError, TypeError, AttributeError) as e:
        log.warning("replay.json %s: %s", sid, e)
        return None


def nachtragen_replay(con: sqlite3.Connection, konfig: Konfig, gewichte: dict[str, float], version: int, *,
                      session: str | None = None) -> dict:
    """Replay-Merkmale für Clips nachtragen, denen sie fehlen (`pipeline merkmale nachtragen`).

    Liest sessions/<ID>/replay.json im Puffer, ordnet clips.kill_zeiten zu (Toleranz 1 ms wegen ISO), schreibt über
    aktualisiere_clip, meldet je Session unbekannte Waffen. Fehlt die Datei: zählen, nicht abbrechen. Weckt nie.
    Idempotent. Rückgabe {"clips", "geaendert", "ohne_replay", "waffen_gemeldet"}. Paket A2.

    Parameter: con – offene Verbindung (ohne offene Transaktion: je Session eine eigene); konfig – Pfade,
    [merkmale]; gewichte, version – vom äußersten Aufrufer (cli._cmd_merkmale per lernen.aktuelle); session – nur
    diese Session (schon geprüfte ID), None = alle. Rückgabe: clips = Clips, denen Replay-Merkmale fehlten;
    geaendert = davon tatsächlich geändert; ohne_replay = davon ohne lesbare replay.json (bleiben unbekannt und
    werden beim nächsten Lauf wieder versucht); waffen_gemeldet = neu gemeldete GunType-Zahlen, aufsteigend.
    Fehler: sqlite3-Fehler brechen ab (die Transaktion der Session rollt zurück, fertige Sessions bleiben).
    Punkte ändern sich nur bei Status vorbewertet (aktualisiere_clip, Annahme S2-A5).
    Ein Clip, dem nach dem Lauf noch Replay-Merkmale fehlen (z. B. sniper/nahkampf vor der Waffen-Kalibrierung,
    Rekorder-Rückfall), zählt beim nächsten Lauf wieder unter „clips“ – so holt der Lauf nach der Kalibrierung
    die Werte von selbst nach.
    Beispiel: 3 Clips ohne Replay-Merkmale, einer ohne replay.json → {"clips": 3, "geaendert": 2, "ohne_replay": 1,
    "waffen_gemeldet": [12]}; der zweite Lauf → {"clips": 1, "geaendert": 0, "ohne_replay": 1, …}.
    """
    if session:
        zeilen = con.execute("SELECT id, match_id, kill_zeiten, merkmale FROM clips WHERE match_id = ? ORDER BY id",
                             (session,)).fetchall()
    else:
        zeilen = con.execute("SELECT id, match_id, kill_zeiten, merkmale FROM clips ORDER BY match_id, id").fetchall()
    je_session: dict[str, list[sqlite3.Row]] = {}
    for z in zeilen:
        if _fehlen_replay_merkmale(z["merkmale"]):
            je_session.setdefault(z["match_id"], []).append(z)

    ergebnis = {"clips": 0, "geaendert": 0, "ohne_replay": 0, "waffen_gemeldet": []}
    gemeldet: set[int] = set()
    for sid, clips in je_session.items():
        ergebnis["clips"] += len(clips)
        match = _match_aus_puffer(konfig, sid)
        if match is None:
            ergebnis["ohne_replay"] += len(clips)
            continue
        with db.transaktion(con):
            for c in clips:
                kill_zeiten = [aus_iso(z) for z in json.loads(c["kill_zeiten"] or "[]")]
                if aktualisiere_clip(con, c["id"], aus_replay(match, kill_zeiten, konfig), gewichte, version):
                    ergebnis["geaendert"] += 1
            gemeldet.update(melde_unbekannte_waffen(con, konfig, sid, match))
    ergebnis["waffen_gemeldet"] = sorted(gemeldet)
    return ergebnis
