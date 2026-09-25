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

Vertrag Stufe 1 (Skelett): Paket (b) baut die Rümpfe.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .konfig import Konfig


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
    zum Schema: $.views: erwartet number/null, bekommen str", '{"views": "viel"}')."""
    raise NotImplementedError


def protokolliere(con: sqlite3.Connection, zweck: str, antwort: ClaudeAntwort) -> None:
    """Zählt einen Aufruf in `ereignisse` (art = 'claude', text = "<zweck>: ok" bzw. "<zweck>: <hinweis>").
    Beispiel: protokolliere(con, "screenshot", antwort) → Zeile „screenshot: ok“. Grundlage für „Claude-Aufrufe
    der Woche“ (angezeigt ab Stufe 3 in /lernstand bzw. Stufe 5 im Wochenbericht, Spec §11.1, §12). Nur der
    Hinweis, nie roh. Läuft im Event-Loop mit der Bot-Verbindung, in eigener kleiner Transaktion."""
    raise NotImplementedError
