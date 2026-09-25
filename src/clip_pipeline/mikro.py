"""Mic-Merkmale in die Clips bringen – im Hintergrund nach `render` (Spec §8.2, §12).

Was: Whisper hört die Mikro-Spur ab (stimmung.py) und zählt Lachen, Jubel, Frust; dazu kommen laute Mikro-Spitzen
und Spielton-Spitzen. Diese Zahlen landen in momente.merkmale. Hier werden sie zusätzlich in clips.merkmale
übernommen (merkmale.aus_momente → merkmale.aktualisiere_clip), und clips.mic_stand bekommt einen Zeitstempel, sobald
die Analyse vollständig ist (merkmale.mic_vollstaendig).

Warum als eigener Dienst (Rückfrage S2-R3, Florian: systemd): Whisper braucht je Clip 30–60 s. Liefe es in
`render`, wartete n8n per SSH darauf. Deshalb schreibt render nur eine kleine Anstoß-Datei (anstossen) neben die
Datenbank und endet sofort mit seiner JSON-Zeile – der n8n-Vertrag bleibt unverändert. systemd wartet mit
`clip-mikro.path` auf diese Datei und startet `clip-mikro.service` (`pipeline stimmung --clips`, Nice 15, eigene
Speichergrenze, Log im Journal). Vorteile gegenüber einem Kindprozess von render: unabhängig von der SSH-Sitzung
(logind), immer nur eine Instanz, und `clip-mikro.timer` stößt alle 30 min nach – so bleibt nichts liegen.

Lernidee: Solange ein Clip keine Mic-Analyse hat, sind seine Mic-Merkmale unbekannt (nicht 0) – /gewichte zeigt die
Zahl „ohne Mic-Analyse“, und lernen.py vergleicht sie nicht.

Nie wecken: Alles arbeitet im Puffer und in der Datenbank. Befehle ohne getrennten Betrieb lehnt cli mit Exit 2 ab.
Der Dienst-Befehl `stimmung --clips` steht nicht in cli.WECKEN – er prüft den Speicher also gar nicht erst.

Ablauf in Alltagssprache: render schneidet neue Clips und ruft anstossen. systemd sieht die geänderte Datei und
startet clip-mikro.service. Der Dienst (clips_nachziehen) übernimmt zuerst alles, was schon in momente steht
(schnell), und hört dann höchstens [merkmale].mic_je_lauf Clips mit Whisper ab, die besten zuerst. Den Rest holt der
nächste Anstoß oder der Timer. Scheitert eine Messung (kaputte Datei), bekommt die Zeile `fehler` und wird nicht
wieder gewählt (merkmale.mic_nachholen, S2-A18).

Import-Regel (Plan Stufe 2, Leitplanke 7; tests/test_vertrag_stufe2.py prüft sie):
    mikro → db, lernen, merkmale, konfig, zeit      stimmung NUR innerhalb von clips_nachziehen (Funktions-Import)
Grund: stimmung importiert mikro auf Modulebene (für _speichere); andersherum entstünde ein Import-Kreis.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import sqlite3

from . import db, lernen, merkmale
from .konfig import Konfig
from .zeit import iso, jetzt

log = logging.getLogger("pipeline")

# Anstoß-Datei neben der Datenbank; deploy/systemd/clip-mikro.path wartet genau auf diesen Namen
ANSTOSS_NAME = "mikro.anstoss"
MIC_JE_LAUF = 3          # Rückfall, falls [merkmale].mic_je_lauf fehlt (Whisper ~30–60 s je Clip, hält die Sperre)


def whisper_da() -> bool:
    """True, wenn faster-whisper installiert ist – geprüft mit importlib.util.find_spec, OHNE es zu importieren.

    Der Import (ctranslate2, Modelle) kostet Sekunden und RAM; im render-Prozess soll er nie passieren.

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
    merkmale.mic_vollstaendig(moment_merkmale). Rückgabe: True, wenn sich etwas geändert hat.

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
    Je Clip eine Transaktion (Befund K-4): clips.merkmale, Punkte und mic_stand ändern sich zusammen oder gar nicht.
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
        with db.transaktion(con):  # Aufrufer (clips_nachziehen, nachtragen) haben keine Transaktion offen
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
    """Schritt (2) nötig? Ohne Zeile immer. Mit Zeile nur, wenn Whisper da ist und merkmale.mic_nachholen(mk) –
    dieselbe Regel wie in stimmung.analysiere(nur_mic=True) (S2-A18)."""
    if mk is None:
        return True
    return mit_whisper and merkmale.mic_nachholen(mk)


def clips_nachziehen(con: sqlite3.Connection, konfig: Konfig, *, session: str | None = None,
                     maximal: int | None = None) -> dict:
    """`pipeline stimmung --clips [--session ID] [--max n]`: Mic-Analyse für Clips ohne mic_stand.

    Holt Gewichte einmal (lernen.aktuelle). (1) Clips mit momente-Zeile → nur übernehmen. (2) Clips ohne Zeile oder
    mit unvollständiger Zeile ohne `fehler` (Letztere nur mit Whisper) → stimmung.analysiere(claude=False,
    nur_clips=…, nur_mic=True, maximal=maximal or [merkmale].mic_je_lauf), Session zuerst, dann nach punkte absteigend.
    Rückgabe {"uebernommen", "analysiert", "mit_fehler", "offen"}.

    Parameter: session – diese Session zuerst (kein Filter: Übrige kommen danach dran); maximal – höchstens so viele
    Messungen in diesem Lauf (--max), sonst [merkmale].mic_je_lauf – damit die Pipeline-Sperre nicht lange belegt ist
    (ein wartender render gäbe nach [sperre].warten_s Exit 4 an n8n).
    Rückgabe: uebernommen = Clips, die Schritt (1) geändert hat · analysiert = neu gemessene Momente ·
    mit_fehler = davon Messfehler · offen = Clips (nicht verworfen), die danach noch keinen mic_stand haben
    (db.ohne_mic_analyse – dieselbe Funktion wie „ohne Mic-Analyse“ in /gewichte).
    Fehler: sqlite3-Fehler gehen an den Aufrufer; Messfehler einzelner Dateien (MedienFehler) werden gezählt, nicht
    geworfen – die Zeile bekommt `fehler`, die anderen Momente des Laufs werden trotzdem gespeichert (Befund K-2).
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
    return {"uebernommen": uebernommen, "analysiert": analysiert, "mit_fehler": mit_fehler,
            "offen": db.ohne_mic_analyse(con)}


def nachtragen(con: sqlite3.Connection, konfig: Konfig, gewichte: dict[str, float], version: int, *,
               session: str | None = None) -> dict:
    """Nur Schritt (1) von clips_nachziehen, ohne Whisper – Teil von `pipeline merkmale nachtragen`.

    Parameter: gewichte, version – holt cli._cmd_merkmale einmal (lernen.aktuelle); session – nur diese Session
    (Filter). konfig wird nicht gebraucht (Signatur wie nachtragen_replay, damit cli beide gleich aufruft).
    Rückgabe {"clips": Clips ohne mic_stand mit momente-Zeile, "geaendert": davon geändert}. Idempotent, schnell,
    weckt nie. Fehler: sqlite3-Fehler gehen an den Aufrufer.
    Beispiel: zwei Clips ohne mic_stand, einer davon ohne Mikro-Spur analysiert → {"clips": 2, "geaendert": 2};
    danach hat der ohne Mikro-Spur seinen mic_stand, der andere wartet auf Whisper → {"clips": 1, "geaendert": 0}.
    """
    return _uebernehmen(con, gewichte, version, session)


def anstossen(konfig: Konfig, sid: str) -> bool:
    """Den Mic-Schritt anstoßen: schreibt die Anstoß-Datei, auf die clip-mikro.path wartet (aufgerufen nur von
    cli._cmd_schritt nach render mit neuen Clips).

    Startet selbst keinen Prozess – das macht systemd (Rückfrage S2-R3). Nur mit [merkmale].mic (fehlt = an) und im
    getrennten Betrieb (ohne Puffer wäre [speicher] pve-big selbst). Inhalt: Session-ID und Zeit, nur zum Nachsehen
    (`cat /var/lib/clip-pipeline/mikro.anstoss`); der Dienst liest sie nicht, er nimmt die besten offenen Clips.
    Parameter: sid – die gerade gerenderte Session (von render schon geprüft). Rückgabe: True = angestoßen.
    Fehler: Kann die Datei nicht geschrieben werden, nur eine Log-Warnung und False – render bleibt unverändert,
    der Timer clip-mikro.timer holt es nach.
    Beispiel: mic an, Puffer-Betrieb → /var/lib/clip-pipeline/mikro.anstoss enthält „2026-09-25_21-00-00 …“, True.
    """
    if not konfig.wert("merkmale.mic", True):
        return False
    if not konfig.getrennt:
        return False
    ziel = konfig.datenbank.parent / ANSTOSS_NAME
    try:
        # erst in eine Nebendatei, dann umbenennen: systemd sieht nie eine halb geschriebene Datei
        neben = ziel.with_suffix(".neu")
        neben.write_text(f"{sid} {iso(jetzt())}\n", encoding="utf-8")
        neben.replace(ziel)
    except OSError as e:
        log.warning("Mic-Schritt nicht angestoßen (%s) – clip-mikro.timer holt ihn nach", e)
        return False
    log.info("Mic-Schritt angestoßen (Session %s)", sid)
    return True
