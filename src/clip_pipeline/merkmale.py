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

import sqlite3
from datetime import datetime

from .konfig import Konfig
from .replay import Match

MIC_DECKEL = 3        # Wortzähler und Mikro-Spitzen: mehr als 3 sagt nichts Neues (Spec §8.1)
SPITZEN_DECKEL = 4    # Spielton-Spitzen (Spec §8.1)
REPLAY_MERKMALE = ("platzierung", "sniper", "nahkampf", "bot_opfer", "phase", "endgame", "clutch")
MIC_MERKMALE = ("mic_lachen", "mic_jubel", "mic_frust", "mic_laut", "spitzen")


def aus_momente(moment_merkmale: dict) -> dict[str, float]:
    """Die eine Zuordnung momente.merkmale → Mic-Merkmale (Spec §8.1), je gedeckelt.

    mic_lachen = min(lachen, 3) · mic_jubel = min(jubel, 3) · mic_frust = min(frust, 3) · mic_laut = min(jubel_laut, 3)
    · spitzen = min(spitzen, 4). `spitzen` in momente ist der ROHE Zähler, hier wird er gedeckelt.
    Fehlende Schlüssel fehlen auch im Ergebnis (unbekannt, nicht 0). Ausnahme ohne Mikro: steht `mikro_spur` im Dict,
    ist None und es gibt kein `fehler`, dann sind die vier mic_* = 0.0 (gemessen: kein Mikro, nichts gesagt).
    Beispiel: {"lachen": 5, "spitzen": 2} → {"mic_lachen": 3.0, "spitzen": 2.0}. Paket A1."""
    raise NotImplementedError


def mic_vollstaendig(moment_merkmale: dict) -> bool:
    """True, wenn die Mic-Analyse dieses Moments fertig ist – Regel für clips.mic_stand (Annahme S2-A6).

    ("lachen" in mk) or ("mikro_spur" in mk and mk["mikro_spur"] is None and "fehler" not in mk).
    stimmung.merkmale schreibt mikro_spur = bestand.mikro_spur(...), das ist None ohne Mikro; 0 wäre ein echter
    Spur-Index. Bei einem Messfehler fehlt mikro_spur ganz, dafür steht `fehler` da.
    Beispiel: {"mikro_spur": 1, "jubel_laut": 2} → False (Mikro da, Whisper lief nicht). Paket A1."""
    raise NotImplementedError


def fuer_moment(clip_merkmale: dict | None, moment_merkmale: dict | None,
                kill_tabelle: list[float]) -> dict[str, float]:
    """Merkmale eines Regisseur-Moments für roh_score (Spec §8.2).

    Clip-Moment (clip_merkmale nicht None): Zahlen aus clips.merkmale, darüber aus_momente(moment_merkmale) – die
    Mic-Werte aus momente gewinnen, weil jünger. Datei-Moment (clip_merkmale None): kill_punkte =
    punkte_fuer(max_gruppe, kill_tabelle), victory_royale aus momente, dazu aus_momente; keine Replay-Merkmale, kein
    laenge, kein lautstaerke (Annahme S2-A9: Datei-Momente haben weder Match noch Kills, stimmung.py:173).
    Nur Zahlen im Ergebnis, Listen und Texte werden verworfen.
    Beispiel: (None, {"max_gruppe": 2, "lachen": 1}, [0, 1, 3, 6, 10]) → {"kill_punkte": 3.0, "victory_royale": 0.0,
    "mic_lachen": 1.0}. Paket A1."""
    raise NotImplementedError


def aktualisiere_clip(con: sqlite3.Connection, clip_id: int, neue: dict[str, float],
                      gewichte: dict[str, float], version: int) -> bool:
    """Mischt die Zahlen aus `neue` in clips.merkmale – die einzige Stelle, die clips.merkmale ändert.

    Nie Listen; fehlende Schlüssel werden NICHT mit 0 angelegt. Bei Status `vorbewertet` (noch nicht gesendet,
    Annahme S2-A5) werden auch punkte, begruendung und gewichte_version neu gesetzt; sonst bleiben sie, wie der Bot
    sie gezeigt hat. Idempotent, läuft in der Transaktion des Aufrufers, holt keine Gewichte selbst.
    Rückgabe: True, wenn sich etwas geändert hat. Unbekannte clip_id → False. Paket A1."""
    raise NotImplementedError


def waffen_kategorie(waffe: int | None, konfig: Konfig) -> str:
    """Waffen-Kategorie "sniper" | "nahkampf" | "sonstige" aus [merkmale.waffen] (Listen von GunType-ZAHLEN, replay2json).

    Rein (keine DB, keine Meldung). Unbekannte Zahl und None → "sonstige".
    Beispiel: waffe 7, [merkmale.waffen] sniper = [7] → "sniper". Paket A2."""
    raise NotImplementedError


def aus_replay(match: Match | None, kill_zeiten: list[datetime], konfig: Konfig) -> dict[str, float]:
    """Die sieben REPLAY_MERKMALE eines Kandidaten (Spec §8.1) – rein, liest nur [merkmale] und [merkmale.waffen].

    Kills ↔ Ereignisse über den Zeitpunkt (gleiche Zeit = gleicher Kandidat, auch beim Team-Wipe).
    platzierung = 1/platz (unbekannt 0) · sniper/nahkampf/bot_opfer = Anteil der Kills (0 Kills → 0) ·
    phase = (erste Aktion − Replay-Start) / Replay-Länge, 0..1 (Länge MEINES Replays, Annahme S2-A3) ·
    endgame = 1, wenn bei einem Kill verbleibend ≤ [merkmale].endgame_spieler ·
    clutch = 1, wenn knock_erlitten in [T − clutch_vor_s, T] und kein tod in (T, T + clutch_nach_s].
    Rekorder-Rückfall (match.ich_quelle None) oder kein Match: nur platzierung (falls bekannt), Rest 0.
    Beispiel: 2 Kills, einer davon ein Bot, Platz 4 → {"platzierung": 0.25, "bot_opfer": 0.5, …}. Paket A2."""
    raise NotImplementedError


def melde_unbekannte_waffen(con: sqlite3.Connection, konfig: Konfig, sid: str, match: Match | None) -> list[int]:
    """Neue GunType-Zahlen meiner Kills als EINE Sammelmeldung je Session (Annahme S2-A4).

    Schon gemeldete Zahlen stehen als Vermerk `merkmale:waffe:<n>` in meldungen (mit gesendet = erstellt, der Bot
    verschickt sie nie). Die Sammelmeldung `merkmale:waffen:<sid>` wartet die Ruhezeit ab (LEISE_MELDUNGEN).
    Rückgabe: die neu gemeldeten Zahlen, aufsteigend. Beispiel: Kills mit 12, 27, 12, nichts eingetragen → [12, 27].
    Paket A2."""
    raise NotImplementedError


def nachtragen_replay(con: sqlite3.Connection, konfig: Konfig, gewichte: dict[str, float], version: int, *,
                      session: str | None = None) -> dict:
    """Replay-Merkmale für Clips nachtragen, denen sie fehlen (`pipeline merkmale nachtragen`).

    Liest sessions/<ID>/replay.json im Puffer, ordnet clips.kill_zeiten zu (Toleranz 1 ms wegen ISO), schreibt über
    aktualisiere_clip, meldet je Session unbekannte Waffen. Fehlt die Datei: zählen, nicht abbrechen. Weckt nie.
    Idempotent. Rückgabe {"clips", "geaendert", "ohne_replay", "waffen_gemeldet"}. Paket A2."""
    raise NotImplementedError
