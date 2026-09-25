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
Der Kindprozess `stimmung --clips` steht nicht in cli.WECKEN – er prüft den Speicher also gar nicht erst.

Ablauf in Alltagssprache: render schneidet neue Clips und ruft starte_im_hintergrund. Das startet `pipeline stimmung
--clips` im Hintergrund und kehrt sofort zurück. Der Hintergrundlauf (clips_nachziehen) übernimmt zuerst alles, was
schon in momente steht (schnell), und hört dann höchstens [merkmale].mic_je_lauf Clips mit Whisper ab – die Session
von eben zuerst. Den Rest holt der nächste Lauf oder der Timer clip-sitzungen.

Import-Regel (Plan Stufe 2, Leitplanke 7; tests/test_vertrag_stufe2.py prüft sie):
    mikro → db, merkmale, lernen, konfig      stimmung NUR innerhalb von clips_nachziehen (Funktions-Import)
Grund: stimmung importiert mikro auf Modulebene (für _speichere); andersherum entstünde ein Import-Kreis.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import sqlite3
import subprocess
import sys

from . import lernen, merkmale
from .konfig import Konfig
from .zeit import iso, jetzt

log = logging.getLogger("pipeline")

LOG_NAME = "mikro.log"   # Log des Kindprozesses, liegt neben der Datenbank (konfig.datenbank.parent)
NICE = 15                # niedrigste sinnvolle Priorität: Whisper darf den Bot und n8n-Schritte nie ausbremsen
MIC_JE_LAUF = 3          # Rückfall, falls [merkmale].mic_je_lauf fehlt (Whisper ~30–60 s je Clip, hält die Sperre)


def whisper_da() -> bool:
    """True, wenn faster-whisper installiert ist – geprüft mit importlib.util.find_spec, OHNE es zu importieren.

    Der Import (ctranslate2, Modelle) kostet Sekunden und RAM; im render-Prozess soll er nie passieren. Paket B.

    Rückgabe: bool. Fehler: keine – ein kaputter Eintrag in sys.modules (ValueError) zählt als „nicht da“.
    Beispiel: ohne `pip install faster-whisper` → False; danach → True (ohne dass das Paket geladen wird).
    """
    try:
        # find_spec sucht nur den Ort des Pakets auf der Platte; ausgeführt wird dabei nichts
        return importlib.util.find_spec("faster_whisper") is not None
    except (ImportError, ValueError):
        return False


def in_clip_uebernehmen(con: sqlite3.Connection, clip_id: int, moment_merkmale: dict,
                        gewichte: dict[str, float], version: int) -> bool:
    """Mic-Werte eines Clip-Moments in clips.merkmale übernehmen, bei vollständiger Analyse mic_stand setzen.

    Über merkmale.aktualisiere_clip(…, aus_momente(moment_merkmale), gewichte, version); mic_stand = jetzt, wenn
    merkmale.mic_vollstaendig(moment_merkmale). Rückgabe: True, wenn sich etwas geändert hat. Paket B.

    Parameter: con – offene Verbindung (läuft in der Transaktion des Aufrufers); clip_id – clips.id;
    moment_merkmale – momente.merkmale dieses Clips; gewichte, version – vom äußersten Aufrufer (lernen.aktuelle).
    Gibt es nichts zu übernehmen (z. B. nur `fehler`), bleibt der Clip ganz unberührt – auch seine Punkte.
    mic_stand wird nur gesetzt, solange er leer ist: So bleibt der Aufruf idempotent, und der Zeitstempel sagt,
    wann die Mic-Analyse zum ersten Mal vollständig war.
    Fehler: sqlite3-Fehler gehen an den Aufrufer. Unbekannte clip_id → False.
    Beispiel: {"mikro_spur": 1, "lachen": 5, "spitzen": 6} → clips.merkmale bekommt mic_lachen 3.0, spitzen 4.0
    (gedeckelt), mic_stand = jetzt, Rückgabe True; derselbe Aufruf noch einmal → False.
    """
    neue = merkmale.aus_momente(moment_merkmale)
    geaendert = bool(neue) and merkmale.aktualisiere_clip(con, clip_id, neue, gewichte, version)
    if merkmale.mic_vollstaendig(moment_merkmale):
        cursor = con.execute("UPDATE clips SET mic_stand = ?, geaendert = ? WHERE id = ? AND mic_stand IS NULL",
                             (iso(jetzt()), iso(jetzt()), clip_id))
        geaendert = geaendert or cursor.rowcount > 0
    return geaendert


def _uebernehmen(con: sqlite3.Connection, gewichte: dict[str, float], version: int,
                 session: str | None) -> dict:
    """Schritt (1): Clips ohne mic_stand, die schon eine momente-Zeile haben → nur übernehmen (kein Whisper).

    Auch verworfene Clips: Das kostet nichts, und ihre Merkmale zählen beim Lernen aus Freigaben mit.
    Rückgabe {"clips": betrachtet, "geaendert": davon geändert}."""
    sql = ("SELECT c.id, m.merkmale FROM clips c JOIN momente m ON m.clip_id = c.id"
           " WHERE c.mic_stand IS NULL")
    werte: tuple = ()
    if session is not None:
        sql += " AND c.match_id = ?"
        werte = (session,)
    betrachtet = geaendert = 0
    for zeile in con.execute(sql + " ORDER BY c.id", werte).fetchall():
        mk = _lies(zeile["merkmale"])
        if mk is None:
            continue
        betrachtet += 1
        geaendert += in_clip_uebernehmen(con, int(zeile["id"]), mk, gewichte, version)
    return {"clips": betrachtet, "geaendert": geaendert}


def _lies(text: str | None) -> dict | None:
    """momente.merkmale als Dict; kaputtes JSON oder etwas anderes als ein Dict → None (Zeile wird übergangen)."""
    try:
        mk = json.loads(text or "")
    except json.JSONDecodeError:
        return None
    return mk if isinstance(mk, dict) else None


def _braucht_messung(mk: dict | None, mit_whisper: bool) -> bool:
    """Schritt (2) nötig? Ohne Zeile immer. Mit Zeile nur, wenn Whisper da ist, die Analyse unvollständig ist und die
    Messung nicht gescheitert war – eine kaputte Datei soll nicht jeden Lauf einen Platz belegen (S2-A18)."""
    if mk is None:
        return True
    return mit_whisper and not merkmale.mic_vollstaendig(mk) and "fehler" not in mk


def clips_nachziehen(con: sqlite3.Connection, konfig: Konfig, *, session: str | None = None,
                     maximal: int | None = None) -> dict:
    """`pipeline stimmung --clips [--session ID] [--max n]`: Mic-Analyse für Clips ohne mic_stand.

    Holt Gewichte einmal (lernen.aktuelle). (1) Clips mit momente-Zeile → nur übernehmen. (2) Clips ohne Zeile oder
    mit unvollständiger Zeile ohne `fehler` (Letztere nur mit Whisper) → stimmung.analysiere(claude=False,
    nur_clips=…, nur_mic=True, maximal=maximal or [merkmale].mic_je_lauf), Session zuerst, dann nach punkte absteigend.
    Rückgabe {"uebernommen", "analysiert", "mit_fehler", "offen"}. Paket B.

    Parameter: session – diese Session zuerst (kein Filter: Übrige kommen danach dran); maximal – höchstens so viele
    Messungen in diesem Lauf (--max), sonst [merkmale].mic_je_lauf – damit die Pipeline-Sperre nicht lange belegt ist
    (ein wartender render gäbe nach [sperre].warten_s Exit 4 an n8n).
    Rückgabe: uebernommen = Clips, die Schritt (1) geändert hat · analysiert = neu gemessene Momente ·
    mit_fehler = davon Messfehler · offen = Clips (nicht verworfen), die danach noch keinen mic_stand haben
    (dieselbe Zahl wie „ohne Mic-Analyse“ in /gewichte).
    Fehler: sqlite3-Fehler gehen an den Aufrufer; Messfehler einzelner Dateien werden gezählt, nicht geworfen.
    Beispiel: 5 neue Clips, mic_je_lauf 3, Whisper da → {"uebernommen": 0, "analysiert": 3, "mit_fehler": 0,
    "offen": 2}; der nächste Lauf holt die letzten zwei.
    """
    # Funktions-Import (Import-Regel, Leitplanke 7): stimmung importiert mikro auf Modulebene – andersherum
    # entstünde ein Import-Kreis. Hier unten ist stimmung längst geladen.
    from . import stimmung

    version, gewichte = lernen.aktuelle(con, konfig)  # einmal holen, durchreichen
    uebernommen = _uebernehmen(con, gewichte, version, None)["geaendert"]

    mit_whisper = whisper_da()
    kandidaten = con.execute(
        """SELECT c.id, m.merkmale FROM clips c LEFT JOIN momente m ON m.clip_id = c.id
            WHERE c.mic_stand IS NULL AND c.status != 'verworfen' AND c.clip_pfad IS NOT NULL
            ORDER BY CASE WHEN c.match_id = ? THEN 0 ELSE 1 END, c.punkte DESC, c.id""", (session,)).fetchall()
    ids = [int(z["id"]) for z in kandidaten
           if _braucht_messung(_lies(z["merkmale"]) if z["merkmale"] is not None else None, mit_whisper)]
    analysiert = mit_fehler = 0
    if ids:
        n = maximal if maximal is not None else int(konfig.wert("merkmale.mic_je_lauf", MIC_JE_LAUF))
        e = stimmung.analysiere(con, konfig, claude=False, nur_clips=ids, nur_mic=True, maximal=n)
        analysiert, mit_fehler = int(e["analysiert"]), int(e["mit_fehler"])
    offen = con.execute("SELECT COUNT(*) FROM clips WHERE mic_stand IS NULL AND status != 'verworfen'").fetchone()[0]
    return {"uebernommen": uebernommen, "analysiert": analysiert, "mit_fehler": mit_fehler, "offen": int(offen)}


def nachtragen(con: sqlite3.Connection, konfig: Konfig, gewichte: dict[str, float], version: int, *,
               session: str | None = None) -> dict:
    """Nur Schritt (1) von clips_nachziehen, ohne Whisper – Teil von `pipeline merkmale nachtragen`. Paket B.

    Parameter: gewichte, version – holt cli._cmd_merkmale einmal (lernen.aktuelle); session – nur diese Session
    (Filter). konfig wird nicht gebraucht (Signatur wie nachtragen_replay, damit cli beide gleich aufruft).
    Rückgabe {"clips": Clips ohne mic_stand mit momente-Zeile, "geaendert": davon geändert}. Idempotent, schnell,
    weckt nie. Fehler: sqlite3-Fehler gehen an den Aufrufer.
    Beispiel: zwei Clips ohne mic_stand, einer davon ohne Mikro-Spur analysiert → {"clips": 2, "geaendert": 2};
    danach hat der ohne Mikro-Spur seinen mic_stand, der andere wartet auf Whisper → {"clips": 1, "geaendert": 0}.
    """
    return _uebernehmen(con, gewichte, version, session)


def starte_im_hintergrund(konfig: Konfig, sid: str) -> bool:
    """Startet den Mic-Schritt als losgelösten Kindprozess (aufgerufen nur von cli._cmd_schritt nach render).

    subprocess.Popen(["nice", "-n", "15", sys.executable, "-m", "clip_pipeline", "--konfig", str(konfig.quelle),
    "stimmung", "--clips", "--session", sid, "--max", str(n)], stdin/stdout=DEVNULL, stderr=Log neben der DB
    (angehängt), start_new_session=True). Nur wenn [merkmale].mic, getrennter Betrieb und whisper_da().
    Fehler beim Start → nur Log-Warnung, Rückgabe False; render bleibt unverändert. Paket B.

    Warum so: `--konfig` sorgt dafür, dass das Kind dieselbe Konfig und damit dieselbe Datenbank nutzt wie render
    (die Umgebung erbt es ohnehin). stdout/stderr gehen nie an den render-Prozess – n8n liest dort genau eine
    JSON-Zeile. start_new_session löst das Kind von der SSH-Sitzung von n8n. Die Pipeline-Sperre holt das Kind
    selbst: Es wartet, bis render sie freigibt (die Sperr-Datei wird nicht vererbt, PEP 446).
    Parameter: sid – die gerade gerenderte Session (von render schon geprüft). Rückgabe: True = gestartet.
    Beispiel: [merkmale].mic = true, mic_je_lauf 3, Puffer-Betrieb, faster-whisper installiert → True, im
    Hintergrund läuft `nice -n 15 python -m clip_pipeline --konfig … stimmung --clips --session <sid> --max 3`.
    """
    if not konfig.wert("merkmale.mic", True):
        return False
    if not konfig.getrennt:  # ohne Puffer wäre [speicher] pve-big selbst – der Mic-Schritt arbeitet nur im Puffer
        return False
    if not whisper_da():
        # Ohne Whisper gibt es nichts, was sich lohnt: laute Spitzen übernimmt clip-sitzungen ohnehin
        log.info("Mic-Schritt nicht gestartet: faster-whisper nicht installiert")
        return False
    n = int(konfig.wert("merkmale.mic_je_lauf", MIC_JE_LAUF))
    befehl = ["nice", "-n", str(NICE), sys.executable, "-m", "clip_pipeline", "--konfig", str(konfig.quelle),
              "stimmung", "--clips", "--session", sid, "--max", str(n)]
    log_pfad = konfig.datenbank.parent / LOG_NAME
    try:
        # "ab": anhängen – das Log erzählt die Geschichte aller Läufe. Das Kind bekommt eine eigene Kopie des
        # Dateideskriptors; wir dürfen unsere nach dem Start schließen.
        with open(log_pfad, "ab") as log_datei:
            subprocess.Popen(befehl, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log_datei,
                             start_new_session=True)
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        log.warning("Mic-Schritt nicht gestartet (%s: %s) – clip-sitzungen holt ihn nach", type(e).__name__, e)
        return False
    log.info("Mic-Schritt im Hintergrund gestartet (Session %s, höchstens %s Clips, Log %s)", sid, n, log_pfad)
    return True
