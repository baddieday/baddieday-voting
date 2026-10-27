"""PC-Laufberichte im lokalen Puffer in die bestehende Telegram-Outbox übernehmen.

Start und Ende bleiben im selben Bericht: Auch ein Kopierlauf zwischen zwei Bot-Polls
liefert beide Meldungen. Dateien bleiben erhalten, eindeutige Outbox-Schlüssel verhindern
erneute Meldungen. Der alte Betrieb direkt auf pve-big wird hier niemals angefasst.
Start und glattes Ende sind Routine (db.meldung routine=True): Der stille Clip-Bot vermerkt sie im
einfachen Modus nur (Stufe 3, 08.10.); Fehler und Abbrüche kommen immer – aber gebündelt (beruhige, 27.10.): Fiel der
Mini aus, schrieb der PC bei jedem 2-Minuten-Lauf einen „abgebrochen“-Bericht und lieferte nach dem Ausfall alle auf
einmal – Hunderte Nachrichten.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import timedelta

from . import db
from .zeit import aus_iso, iso, jetzt, utc_zu_lokal

log = logging.getLogger("clip-bot")

PAUSE_STUNDEN = 6   # [bot].uebertragung_pause_h: dasselbe Problem meldet der Bot höchstens alle so viele Stunden


def _geschrieben(datei) -> tuple[int, str]:
    """Reihenfolge der Berichte: wie der PC sie geliefert hat (er spiegelt die ältesten zuerst), nicht nach der
    zufälligen Kennung im Dateinamen – sonst wäre „die letzte“ Meldung beim Bündeln beliebig."""
    try:
        return datei.stat().st_mtime_ns, datei.name
    except OSError:
        return 0, datei.name


def hole_meldungen(con, konfig) -> int:
    if not konfig.getrennt:
        return 0
    ordner = konfig.wurzel / "sitzungen" / "uebertragung"
    fertig = {r[0] for r in con.execute(
        "SELECT schluessel FROM meldungen WHERE schluessel LIKE 'uebertragung:pc:%:ende'")}
    neu = 0
    for datei in sorted(ordner.glob("*.json"), key=_geschrieben):
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
                f"Videos vorgemerkt: {daten['videos_geplant']}."), routine=True)
            if ende:
                ok = daten["status"] == "fertig" and not daten["fehler"]
                kopf = "✅ Übertragung Gaming-PC → Puffer abgeschlossen" if ok else (
                    "⚠️ Übertragung Gaming-PC → Puffer abgebrochen" if daten["status"] == "abgebrochen"
                    else "⚠️ Übertragung Gaming-PC → Puffer mit Fehlern beendet")
                text = (f"{kopf} (Start {wann}).\nNeu übertragene Videos: {daten['videos_kopiert']}.\n"
                        f"Dateien insgesamt: {daten['dateien_kopiert']} · Fehler: {daten['fehler']}.")
                if not ok:
                    text += "\nOffene Dateien werden beim nächsten Lauf erneut versucht."
                neu += db.meldung(con, key + ":ende", text, routine=ok)
    beruhige(con, konfig)
    return neu


def _art(text: str) -> str:
    """„⚠️ Übertragung Gaming-PC → Puffer abgebrochen“ – die Überschrift ohne „(Start …)“."""
    return text.split(" (Start", 1)[0]


def beruhige(con, konfig) -> int:
    """Bündelt Problem-Meldungen der PC-Berichte, bevor sie rausgehen (27.10., Florian: „Warum werde ich so gespammt?“).
    1. Ein Start, zu dem es schon ein Problem-Ende gibt, sagt nichts Neues – wird nur vermerkt.
    2. Gleichartige Probleme (abgebrochen / mit Fehlern) wartender Berichte: nur die letzte geht raus, mit „+n
       zusammengefasst“; die anderen sind vermerkt (routine = 2, nie gesendet). Nichts wird gelöscht.
    3. Dasselbe Problem kommt höchstens alle [bot].uebertragung_pause_h Stunden (6) wieder – ein Fehler, der jeden
       2-Minuten-Lauf neu auftritt, ist eine Meldung, keine Flut.
    Rückgabe: Anzahl vermerkter (nicht gesendeter) Meldungen."""
    try:
        pause = timedelta(hours=float(konfig.wert("bot.uebertragung_pause_h", PAUSE_STUNDEN)))
    except (TypeError, ValueError):
        pause = timedelta(hours=PAUSE_STUNDEN)
    jetzt_text = iso(jetzt())
    vermerkt = 0
    with db.transaktion(con):
        vermerkt += con.execute(
            """UPDATE meldungen SET gesendet = ?
                WHERE gesendet IS NULL AND schluessel LIKE 'uebertragung:pc:%:start'
                  AND EXISTS (SELECT 1 FROM meldungen e
                               WHERE e.schluessel = substr(meldungen.schluessel, 1, length(meldungen.schluessel) - 6)
                                                    || ':ende' AND e.routine IS NULL)""", (jetzt_text,)).rowcount
        wartend: dict[str, list] = {}
        for z in con.execute("""SELECT id, text FROM meldungen WHERE gesendet IS NULL AND routine IS NULL
                                 AND schluessel LIKE 'uebertragung:pc:%:ende' ORDER BY id"""):
            wartend.setdefault(_art(z["text"]), []).append(z)
        for art, zeilen in wartend.items():
            kurz_her = con.execute(
                """SELECT 1 FROM meldungen WHERE routine IS NULL AND gesendet >= ?
                    AND schluessel LIKE 'uebertragung:pc:%:ende' AND text LIKE ? ESCAPE '\\' LIMIT 1""",
                (iso(jetzt() - pause), art.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")).fetchone()
            behalten = None if kurz_her else zeilen[-1]
            alle = [z for z in zeilen if z is not behalten]
            for z in alle:
                con.execute("UPDATE meldungen SET gesendet = ?, routine = 2 WHERE id = ?", (jetzt_text, z["id"]))
            vermerkt += len(alle)
            if behalten is not None and alle:
                con.execute("UPDATE meldungen SET text = ? WHERE id = ?", (
                    behalten["text"] + f"\n(+{len(alle)} gleiche Meldungen zusammengefasst – der PC hat es immer "
                    f"wieder versucht.)", behalten["id"]))
    return vermerkt
