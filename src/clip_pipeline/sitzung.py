"""Session vorbei (`pipeline sitzungen`, Timer alle 10 min): Gaming-PC meldet das Ende eines Spielabends.

Der Windows-Helfer schreibt sitzungen/session_<zeit>.json (Match-IDs des Abends) auf den Speicher. Hier:
  1. neue Dateien lesen – pve-big wird dafür NICHT geweckt (schläft er, nächster Versuch beim nächsten Timer)
  2. warten, bis n8n alle Matches verarbeitet hat (höchstens `warten_h`, danach mit Hinweis weiter)
  3. Stimmung für neue Momente, dann ein Short nur aus den Momenten dieses Abends -> der Lern-Bot schickt ihn
Die Datei selbst bleibt liegen (sie ist klein; verarbeitet wird über die Tabelle `sitzungen`).

Stufe 1 (07.10., Florian: „Bot macht alles … nach dem Zocken … lieber kein Video“): Vor der Arbeit eine Statuszeile
(Lern-Meldung abend:<sitzung>), gebaut wird mit deinen Regeln (regeln.anwenden: Länge, Effekte, nur starke Szenen).
Reichen die starken Szenen nicht, kommt kein Video, sondern „kein:<sitzung>“ mit dem Grund; bei einem Fehler
„fehler:<sitzung>“. Der Lern-Bot ersetzt die Statuszeile durch das Video bzw. schreibt sie zu diesem Satz um.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import timedelta

from . import db, einstellungen, entwurf, lernen, regeln, regie, regie_lernen, stimmung
from .konfig import Konfig
from .medien import MedienFehler
from .verarbeitung import SESSION_ID
from .zeit import aus_iso, iso, jetzt, utc_zu_lokal

log = logging.getLogger("pipeline")


def verarbeite(con: sqlite3.Connection, konfig: Konfig, *, claude: bool = True, whisper: bool = True) -> dict:
    konfig.pruefe_speicher()  # wirft SpeicherOffline -> Exit 3, kein Wecken
    ordner = konfig.wurzel / str(konfig.wert("sitzungen.ordner", "sitzungen"))
    fertig = {z["name"] for z in con.execute("SELECT name FROM sitzungen")}
    ergebnis: dict = {"neu": [], "wartet": [], "fehler": [], "nachgeholt": _nachholen(con, konfig)}
    for datei in sorted(ordner.glob("session_*.json")) if ordner.is_dir() else []:
        name = datei.stem
        if name in fertig or not SESSION_ID.fullmatch(name):
            continue
        try:
            daten = json.loads(datei.read_text(encoding="utf-8-sig"))  # Windows schreibt mit BOM
            matches = [m for m in daten.get("matches", []) if isinstance(m, str) and SESSION_ID.fullmatch(m)]
            ende = aus_iso(daten["ende_utc"]) if daten.get("ende_utc") else jetzt()
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            ergebnis["fehler"].append(f"{name}: {e}")
            continue
        status = {z["id"]: z["status"] for z in con.execute(
            f"SELECT id, status FROM matches WHERE id IN ({','.join('?' for _ in matches) or 'NULL'})", matches)}
        offen = [m for m in matches if status.get(m) != "verarbeitet"]
        hinweis = None
        if offen:
            if jetzt() - ende < timedelta(hours=float(konfig.wert("sitzungen.warten_h", 2))):
                ergebnis["wartet"].append({"sitzung": name, "offen": offen})
                continue
            hinweis = f"{len(offen)} Match(es) nicht verarbeitet: {', '.join(offen[:5])}"
        tag = _tag(konfig, ende)
        db.lern_meldung(con, f"abend:{name}", f"🎮 Abend vom {tag} erkannt ({len(matches)} Match"
                        f"{'' if len(matches) == 1 else 'es'}) – ich baue dein Video. Das dauert meist 10–30 Minuten.")
        stimmung.analysiere(con, konfig, claude=claude, whisper=whisper)
        entwurf_id = None
        k = einstellungen.anwenden(con, konfig)            # ⚙️ und deine Regeln gelten auch für das Abend-Video
        try:  # deine 👍/👎 bis eben lehren die Moment-Formel (wie vor jedem Entwurf im Lern-Bot)
            lernen.aktualisiere(con, k)
        except Exception:  # noqa: BLE001 – das Video ist wichtiger; dann gelten die bisherigen Gewichte
            log.exception("Moment-Formel vor dem Abend-Video")
        try:
            fmt = str(k.wert("sitzungen.format", "short"))
            parameter, ziel = regie_lernen.aktuelle(con, k, fmt)
            parameter = regeln.anwenden(con, k, fmt, parameter)
            e = regie.erstelle(con, k, fmt, parameter=parameter, ziel=ziel, nur_matches=set(matches))
            entwurf_id = e["entwurf"]
            # Die Sitzung kennt ihren Entwurf schon VOR dem Rendern: sobald er fertig ist, kann der Lern-Bot ihn als
            # „Dein Abend vom …“ schicken und die Statuszeile löschen – vorher lag dazwischen ein kleines Rennen
            _merke(con, name, matches, ende, entwurf_id, hinweis)
            entwurf.entwurf(con, k, entwurf_id)
        except regie.ZuWenigSzenen as z:   # lieber kein Video als eins mit Füllmaterial (Florian, 07.10.)
            hinweis = "; ".join(filter(None, [hinweis, str(z)]))
            db.lern_meldung(con, f"kein:{name}", kein_video_text(tag, z, len(offen)))
        except Exception as fehler:  # noqa: BLE001 – vermerken und dir sagen, statt alle 10 min still neu zu versuchen
            entwurf_id = None
            hinweis = "; ".join(filter(None, [hinweis, str(fehler)]))
            _melde_fehler(con, name, tag, fehler)
        _merke(con, name, matches, ende, entwurf_id, hinweis)
        ergebnis["neu"].append({"sitzung": name, "matches": len(matches), "entwurf": entwurf_id, "hinweis": hinweis})
    return ergebnis


def kein_video_text(tag: str, z: regie.ZuWenigSzenen, offen: int = 0) -> str:
    """„🎮 Abend vom 06.10.: nur 2 starke Szenen (…), ein Video braucht 4 – heute kein Video.“ + Grund/Tipp."""
    text = f"🎮 Abend vom {tag}: {z.kopf()} – heute kein Video."
    if offen:
        text += f" {offen} Match{'' if offen == 1 else 'es'} kam{'' if offen == 1 else 'en'} nie bei mir an."
    return f"{text} {z.tipp()}".strip()


def _tag(konfig: Konfig, ende) -> str:
    return f"{utc_zu_lokal(ende, konfig.wert('zeit.zeitzone', 'Europe/Berlin')):%d.%m.}"


def _melde_fehler(con: sqlite3.Connection, name: str, tag: str, fehler: Exception) -> None:
    if not isinstance(fehler, (regie.RegieFehler, MedienFehler)):
        log.error("Abend-Video %s", name, exc_info=fehler)
    db.lern_meldung(con, f"fehler:{name}", f"⚠️ Für deinen Abend vom {tag} kam kein Video zustande: "
                                           f"{str(fehler)[:200]}. Beim nächsten Abend versuche ich es wieder.")


def _nachholen(con: sqlite3.Connection, konfig: Konfig) -> list[str]:
    """Abend-Videos, deren Rendern abbrach (Update, Neustart, Strom): Die Sitzung kennt ihren Entwurf schon, der steht
    aber noch auf „neu“ ohne Datei. entwurf.entwurf ist idempotent – einfach nochmal rendern, bis 12 h nach dem Abend.
    Danach (oder bei einem echten Fehler) kommt die Fehlerzeile statt endloser Versuche alle 10 Minuten."""
    offen = con.execute("""SELECT s.name, s.entwurf_id, s.ende_utc, s.verarbeitet, s.hinweis FROM sitzungen s
                           JOIN entwuerfe e ON e.id = s.entwurf_id WHERE e.status = 'neu' AND e.datei IS NULL""").fetchall()
    if not offen:
        return []
    k = einstellungen.anwenden(con, konfig)
    grenze = jetzt() - timedelta(hours=12)
    nachgeholt = []
    for z in offen:
        tag = _tag(konfig, aus_iso(z["ende_utc"]) if z["ende_utc"] else jetzt())
        try:
            if aus_iso(z["verarbeitet"]) < grenze:
                raise MedienFehler("das Bauen wurde immer wieder unterbrochen")
            log.info("Abend-Video %s: Rendern war unterbrochen – baue Entwurf #%s fertig", z["name"], z["entwurf_id"])
            entwurf.entwurf(con, k, z["entwurf_id"])
            nachgeholt.append(z["name"])
        except Exception as fehler:  # noqa: BLE001 – wie beim ersten Versuch: vermerken und dir sagen
            con.execute("UPDATE sitzungen SET entwurf_id = NULL, hinweis = ? WHERE name = ?",
                        ("; ".join(filter(None, [z["hinweis"], str(fehler)])), z["name"]))
            _melde_fehler(con, z["name"], tag, fehler)
    return nachgeholt


def _merke(con: sqlite3.Connection, name: str, matches: list[str], ende, entwurf_id: int | None,
           hinweis: str | None) -> None:
    """Sitzung als verarbeitet merken bzw. ihren Stand nachtragen (Entwurf, Hinweis)."""
    con.execute(
        """INSERT INTO sitzungen (name, matches, ende_utc, entwurf_id, hinweis, verarbeitet) VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT (name) DO UPDATE SET entwurf_id = excluded.entwurf_id, hinweis = excluded.hinweis""",
        (name, json.dumps(matches), iso(ende), entwurf_id, hinweis, iso(jetzt())),
    )
