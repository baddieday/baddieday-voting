"""Mic-Merkmale in die Clips bringen – im Hintergrund nach `render` (Spec §8.2, §12).

Was: Whisper hört die Mikro-Spur ab (stimmung.py) und zählt Lachen, Jubel, Frust; dazu kommen laute Mikro-Spitzen
und Spielton-Spitzen. Diese Zahlen landen in momente.merkmale. Hier werden sie zusätzlich in clips.merkmale
übernommen (merkmale.aus_momente → merkmale.aktualisiere_clip), und clips.mic_stand bekommt einen Zeitstempel, sobald
die Analyse vollständig ist (merkmale.mic_vollstaendig).

Warum losgelöst: Whisper braucht je Clip 30–60 s. Liefe es in `render`, wartete n8n per SSH darauf. Deshalb startet
render nur einen Kindprozess (`pipeline stimmung --clips --session ID`, nice 15, eigene Sitzung, stdout/stderr nicht
am render-Prozess) und endet sofort mit seiner JSON-Zeile – der n8n-Vertrag bleibt unverändert. Das Kind holt die
Pipeline-Sperre selbst (die Sperr-Datei wird nicht vererbt, PEP 446). Rückfall ohne Code: der Timer clip-sitzungen
erfasst liegengebliebene Clips ohnehin nach ≤ 10 min (Annahme S2-A7).

Lernidee: Solange ein Clip keine Mic-Analyse hat, sind seine Mic-Merkmale unbekannt (nicht 0) – /gewichte zeigt die
Zahl „ohne Mic-Analyse“, und lernen.py vergleicht sie nicht.

Nie wecken: Alles arbeitet im Puffer und in der Datenbank. Befehle ohne getrennten Betrieb lehnt cli mit Exit 2 ab.

Import-Regel (Plan Stufe 2, Leitplanke 7; tests/test_vertrag_stufe2.py prüft sie):
    mikro → db, merkmale, lernen, konfig      stimmung NUR innerhalb von clips_nachziehen (Funktions-Import)
Grund: stimmung importiert mikro auf Modulebene (für _speichere); andersherum entstünde ein Import-Kreis.
"""

from __future__ import annotations

import sqlite3

from .konfig import Konfig

LOG_NAME = "mikro.log"   # Log des Kindprozesses, liegt neben der Datenbank (konfig.datenbank.parent)


def whisper_da() -> bool:
    """True, wenn faster-whisper installiert ist – geprüft mit importlib.util.find_spec, OHNE es zu importieren.

    Der Import (ctranslate2, Modelle) kostet Sekunden und RAM; im render-Prozess soll er nie passieren. Paket B."""
    raise NotImplementedError


def in_clip_uebernehmen(con: sqlite3.Connection, clip_id: int, moment_merkmale: dict,
                        gewichte: dict[str, float], version: int) -> bool:
    """Mic-Werte eines Clip-Moments in clips.merkmale übernehmen, bei vollständiger Analyse mic_stand setzen.

    Über merkmale.aktualisiere_clip(…, aus_momente(moment_merkmale), gewichte, version); mic_stand = jetzt, wenn
    merkmale.mic_vollstaendig(moment_merkmale). Rückgabe: True, wenn sich etwas geändert hat. Paket B."""
    raise NotImplementedError


def clips_nachziehen(con: sqlite3.Connection, konfig: Konfig, *, session: str | None = None,
                     maximal: int | None = None) -> dict:
    """`pipeline stimmung --clips [--session ID] [--max n]`: Mic-Analyse für Clips ohne mic_stand.

    Holt Gewichte einmal (lernen.aktuelle). (1) Clips mit momente-Zeile → nur übernehmen. (2) Clips ohne Zeile oder
    mit unvollständiger Zeile ohne `fehler` (Letztere nur mit Whisper) → stimmung.analysiere(claude=False,
    nur_clips=…, nur_mic=True, maximal=maximal or [merkmale].mic_je_lauf), Session zuerst, dann nach punkte absteigend.
    Rückgabe {"uebernommen", "analysiert", "mit_fehler", "offen"}. Paket B."""
    raise NotImplementedError


def nachtragen(con: sqlite3.Connection, konfig: Konfig, gewichte: dict[str, float], version: int, *,
               session: str | None = None) -> dict:
    """Nur Schritt (1) von clips_nachziehen, ohne Whisper – Teil von `pipeline merkmale nachtragen`. Paket B."""
    raise NotImplementedError


def starte_im_hintergrund(konfig: Konfig, sid: str) -> bool:
    """Startet den Mic-Schritt als losgelösten Kindprozess (aufgerufen nur von cli._cmd_schritt nach render).

    subprocess.Popen(["nice", "-n", "15", sys.executable, "-m", "clip_pipeline", "--konfig", str(konfig.quelle),
    "stimmung", "--clips", "--session", sid, "--max", str(n)], stdin/stdout=DEVNULL, stderr=Log neben der DB
    (angehängt), start_new_session=True). Nur wenn [merkmale].mic, getrennter Betrieb und whisper_da().
    Fehler beim Start → nur Log-Warnung, Rückgabe False; render bleibt unverändert. Paket B."""
    raise NotImplementedError
