"""PC-Laufberichte im lokalen Puffer in die bestehende Telegram-Outbox übernehmen.

Start und Ende bleiben im selben Bericht: Auch ein Kopierlauf zwischen zwei Bot-Polls
liefert beide Meldungen. Dateien bleiben erhalten, eindeutige Outbox-Schlüssel verhindern
erneute Meldungen. Der alte Betrieb direkt auf pve-big wird hier niemals angefasst.
"""
from __future__ import annotations

import json
import logging
import re

from . import db
from .zeit import aus_iso, utc_zu_lokal

log = logging.getLogger("clip-bot")


def hole_meldungen(con, konfig) -> int:
    if not konfig.getrennt:
        return 0
    ordner = konfig.wurzel / "sitzungen" / "uebertragung"
    fertig = {r[0] for r in con.execute(
        "SELECT schluessel FROM meldungen WHERE schluessel LIKE 'uebertragung:pc:%:ende'")}
    neu = 0
    for datei in sorted(ordner.glob("*.json")):
        if not re.fullmatch(r"[0-9a-f]{32}", datei.stem):
            continue
        key = f"uebertragung:pc:{datei.stem}"
        if key + ":ende" in fertig:
            continue
        try:
            daten = json.loads(datei.read_text(encoding="utf-8-sig"))
            if daten["id"] != datei.stem or daten["status"] not in ("laeuft", "fertig", "fehler", "abgebrochen"):
                raise ValueError("ungültiger Laufbericht")
            for name in ("videos_geplant", "videos_kopiert", "dateien_kopiert", "fehler"):
                if type(daten[name]) is not int or daten[name] < 0:
                    raise ValueError("ungültiger Zähler")
            start = aus_iso(daten["start_utc"])
            ende = aus_iso(daten["ende_utc"]) if daten.get("ende_utc") else None
            if (daten["status"] == "laeuft") != (ende is None) or (ende and ende < start):
                raise ValueError("ungültige Laufzeit")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log.warning("Übertragungsbericht %s unlesbar: %s", datei.name, type(exc).__name__)
            continue
        wann = utc_zu_lokal(start, konfig.wert("zeit.zeitzone", "Europe/Berlin")).strftime("%d.%m. %H:%M")
        with db.transaktion(con):
            neu += db.meldung(con, key + ":start", (
                f"🔄 Übertragung Gaming-PC → Puffer gestartet ({wann}).\n"
                f"Videos vorgemerkt: {daten['videos_geplant']}."))
            if ende:
                ok = daten["status"] == "fertig" and not daten["fehler"]
                kopf = "✅ Übertragung Gaming-PC → Puffer abgeschlossen" if ok else (
                    "⚠️ Übertragung Gaming-PC → Puffer abgebrochen" if daten["status"] == "abgebrochen"
                    else "⚠️ Übertragung Gaming-PC → Puffer mit Fehlern beendet")
                text = (f"{kopf} (Start {wann}).\nNeu übertragene Videos: {daten['videos_kopiert']}.\n"
                        f"Dateien insgesamt: {daten['dateien_kopiert']} · Fehler: {daten['fehler']}.")
                if not ok:
                    text += "\nOffene Dateien werden beim nächsten Lauf erneut versucht."
                neu += db.meldung(con, key + ":ende", text)
    return neu
