"""Ein `claude -p`-Aufruf mit JSON-Antwort und Schema-Prüfung – einmal sauber, statt in jedem Modul neu.

Warum es das gibt: Das Muster steht schon zweimal im Code (verarbeitung.frage_claude für `decide`,
stimmung.frage_claude für die Stimmung). Die Lernschleife braucht es zweimal mehr (Screenshot, Wochen-Analyst).
Statt einer dritten und vierten Kopie gibt es diese Hilfsfunktion; die beiden alten Stellen bleiben vorerst,
wie sie sind (der `decide`-Pfad gehört zum n8n-Vertrag und wird in diesem Sprint nicht angefasst).

Was beim Aufruf passiert (wie bisher, CLAUDE.md „KI-Entscheidungen“):
  1. `shutil.which([decide].programm)` – ohne claude kein Aufruf, sondern ein Hinweis.
  2. `claude -p --output-format json --allowedTools Read --no-session-persistence <auftrag>` im Arbeitsordner.
     Claude darf nur LESEN und sieht nur Dateien in diesem Ordner (deshalb legt der Aufrufer Bild bzw. Dossier
     dort hinein). --no-session-persistence: claude speichert den Verlauf sonst unter ~/.claude/projects/ – mit
     dem gelesenen Bild. Das wäre ein Bildarchiv durch die Hintertür (Spec §7.1: „kein Bildarchiv“). Kennt die
     claude-Version auf dem Mini den Schalter nicht, endet der Aufruf mit Exit ≠ 0 → Hinweis, Hand-Eingabe
     (docs/PUBLIKUM.md nennt die Prüfung `claude --help | grep no-session-persistence`, 🏠).
  3. Die Hülle ist JSON mit `is_error` und `result`; aus `result` wird das erste {…} gelesen – mit
     verarbeitung._json_aus_text wie in stimmung.py (keine dritte Kopie).
  4. Die Antwort wird gegen `src/clip_pipeline/schemas/<schema_name>.schema.json` geprüft.
Jeder Fehler (kein claude, Timeout, Exit ≠ 0, Limit erreicht, kein JSON, Schema verletzt) endet NICHT in einer
Ausnahme, sondern in (None, Hinweis, roh) – der Aufrufer entscheidet über den Rückfall (Hand-Eingabe, Regeln).

Die Funktion fasst keine Datenbank an (sie läuft in einem Thread). Mitzählen (Tabelle `ereignisse`,
art = 'claude', Spec §12) macht der Aufrufer im Haupt-Thread mit `protokolliere`.

Logs: nur Hinweis und Dauer – nie `result`, nie stdout/stderr von claude (dort stehen die gelesenen Zahlen bzw.
Inhalte des Arbeitsordners); die Rohantwort gehört nur in publikum_messungen.roh.

Reihenfolge der Schalter: `claude --help` beschreibt `--allowedTools <tools...>` – der Schalter nimmt also mehrere
Werte, bis zum nächsten Schalter. Damit der Auftrag sicher nicht als weiterer Werkzeug-Name gelesen wird, steht
`--no-session-persistence` zwischen „Read“ und dem Auftrag, und der Auftrag kommt ganz am Ende.
"""

from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from . import db, schema
from .konfig import Konfig
from .verarbeitung import _json_aus_text  # erstes {…} aus einem Text – dieselbe Hilfe wie für decide und Stimmung

log = logging.getLogger("pipeline")

# Die Schalter jedes Aufrufs (Erklärung im Modul-Docstring). Der Auftrag kommt als letztes Argument dahinter.
SCHALTER = ("-p", "--output-format", "json", "--allowedTools", "Read", "--no-session-persistence")
# In `ereignisse.art` – danach zählt die Wochenzahl der Claude-Aufrufe (Spec §12)
EREIGNIS_ART = "claude"


@dataclass
class ClaudeAntwort:
    daten: dict | None   # geprüfte Antwort oder None
    hinweis: str | None  # warum es nichts gab (für Log und Bot-Text), sonst None
    roh: str | None      # vollständiger Text aus `result` (nicht gekürzt) – für publikum_messungen.roh


def frage_json(konfig: Konfig, auftrag: str, arbeitsordner: Path, *, schema_name: str,
               timeout_s: float) -> ClaudeAntwort:
    """Fragt `claude -p` mit Leserechten im arbeitsordner und prüft die Antwort gegen das Schema.

    auftrag: der feste Prompt (z. B. templates/screenshot-prompt.txt). schema_name: "publikum" →
    schemas/publikum.schema.json. timeout_s: danach wird claude abgebrochen (subprocess.TimeoutExpired → Hinweis).
    Wirft nie – jeder Fehler steht in hinweis. Beispiele: ClaudeAntwort({"views": 1240, …}, None, '{"views": 1240,
    …}') · ClaudeAntwort(None, "claude meldet Fehler (Limit?)", None) · ClaudeAntwort(None, "Antwort passt nicht
    zum Schema: $.views: erwartet number/null, bekommen str", '{"views": "viel"}').

    Ins Log (Logger „pipeline“) kommt genau eine Zeile: Zweck, Ergebnis („ok“ oder der Hinweis) und die Dauer."""
    beginn = time.monotonic()
    antwort = _frage(konfig, auftrag, arbeitsordner, schema_name, timeout_s)
    log.info("claude (%s): %s nach %.0f s", schema_name, antwort.hinweis or "ok", time.monotonic() - beginn)
    return antwort


def _frage(konfig: Konfig, auftrag: str, arbeitsordner: Path, schema_name: str, timeout_s: float) -> ClaudeAntwort:
    """Der eigentliche Aufruf, Schritt für Schritt. Jede Prüfung endet – wenn sie scheitert – mit einer
    ClaudeAntwort ohne daten; so steht an genau einer Stelle, welche Fehler es geben kann."""
    # Schema zuerst laden: Fehlt es, wäre jeder Aufruf verschwendet (zählt gegen das Abo)
    try:
        muster = schema.lade(schema_name)
    except (OSError, ValueError) as e:  # Datei fehlt bzw. ist kein JSON (JSONDecodeError ist ein ValueError)
        return ClaudeAntwort(None, f"Schema {schema_name} nicht lesbar ({type(e).__name__})", None)

    # Programm finden – ohne claude gibt es keinen Aufruf, sondern einen Hinweis (dann: Hand-Eingabe)
    name = konfig.wert("decide.programm")
    if not name:
        return ClaudeAntwort(None, "[decide].programm fehlt in der Konfiguration", None)
    programm = shutil.which(str(name))
    if programm is None:
        return ClaudeAntwort(None, "claude nicht gefunden", None)

    # Aufrufen: nur Leserecht, nur im Arbeitsordner; stdout/stderr bleiben hier (nie ins Log)
    try:
        # stdin=DEVNULL: claude -p liest zusätzlich von stdin, wenn dort etwas angeschlossen ist – so wartet es nie
        lauf = subprocess.run([programm, *SCHALTER, auftrag], cwd=arbeitsordner, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout_s,
                              check=False)
    except (OSError, subprocess.TimeoutExpired) as e:  # startet nicht bzw. hängt – run bricht claude dann ab
        return ClaudeAntwort(None, f"claude nicht nutzbar ({type(e).__name__})", None)
    if lauf.returncode != 0:
        return ClaudeAntwort(None, f"claude Exit {lauf.returncode}", None)

    # Die Hülle von --output-format json: {"is_error": …, "result": "<Text der Antwort>", …}
    try:
        huelle = json.loads(lauf.stdout)
    except json.JSONDecodeError:
        return ClaudeAntwort(None, "claude-Ausgabe kein JSON", None)
    if not isinstance(huelle, dict):
        return ClaudeAntwort(None, "claude-Ausgabe kein JSON", None)
    if huelle.get("is_error"):  # z. B. Abo-Limit erreicht – result ist dann eine Fehlermeldung, keine Antwort
        return ClaudeAntwort(None, "claude meldet Fehler (Limit?)", None)

    # Die Antwort selbst: das erste {…} im Text, geprüft gegen das Schema. roh bleibt vollständig erhalten.
    roh = str(huelle.get("result", ""))
    daten = _json_aus_text(roh)
    if daten is None:
        return ClaudeAntwort(None, "claude-Antwort ohne JSON", roh)
    if fehler := schema.pruefe(daten, muster):
        return ClaudeAntwort(None, f"Antwort passt nicht zum Schema: {fehler[0]}", roh)
    return ClaudeAntwort(daten, None, roh)


def protokolliere(con: sqlite3.Connection, zweck: str, antwort: ClaudeAntwort) -> None:
    """Zählt einen Aufruf in `ereignisse` (art = 'claude', text = "<zweck>: ok" bzw. "<zweck>: <hinweis>").
    Beispiel: protokolliere(con, "screenshot", antwort) → Zeile „screenshot: ok“. Grundlage für „Claude-Aufrufe
    der Woche“ (angezeigt ab Stufe 3 in /lernstand bzw. Stufe 5 im Wochenbericht, Spec §11.1, §12). Nur der
    Hinweis, nie roh. Läuft im Event-Loop mit der Bot-Verbindung, in eigener kleiner Transaktion – deshalb nicht
    innerhalb einer anderen db.transaktion aufrufen (BEGIN lässt sich nicht verschachteln)."""
    with db.transaktion(con):
        db.protokoll(con, EREIGNIS_ART, f"{zweck}: {antwort.hinweis or 'ok'}")
