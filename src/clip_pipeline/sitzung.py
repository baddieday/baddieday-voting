"""Session vorbei (`pipeline sitzungen`, Timer alle 10 min): Gaming-PC meldet das Ende eines Spielabends.

Der Windows-Helfer schreibt sitzungen/session_<zeit>.json (Match-IDs des Abends) auf den Speicher – oder der Mini
erkennt das Abend-Ende selbst (auto_abend: 45 min kein neues Match). Hier:
  1. neue Dateien lesen – pve-big wird dafür NICHT geweckt (schläft er, nächster Versuch beim nächsten Timer)
  2. warten, bis n8n alle Matches verarbeitet hat (höchstens `warten_h`, danach mit Hinweis weiter)
  3. Stimmung für neue Momente, dann ein Short nur aus den Momenten dieses Abends -> der Lern-Bot schickt ihn
Die Datei selbst bleibt liegen (sie ist klein; verarbeitet wird über die Tabelle `sitzungen`).

Stufe 1 (07.10., Florian: „Bot macht alles … nach dem Zocken … lieber kein Video“): Vor der Arbeit eine Statuszeile
(Lern-Meldung abend:<sitzung>), gebaut wird mit deinen Regeln (regeln.anwenden: Länge, Effekte, nur starke Szenen).
Reichen die starken Szenen nicht, kommt kein Video, sondern „kein:<sitzung>“ mit dem Grund; bei einem Fehler
„fehler:<sitzung>“. Der Lern-Bot ersetzt die Statuszeile durch das Video bzw. schreibt sie zu diesem Satz um.

08.10. (Florian: „autonom … besser und schneller als mit der Hand“): Scheitert im einfachen Modus erst das Rendern,
ist der Abend nicht verloren – der nächste Timer-Lauf rendert den Entwurf einmal auf der CPU nach (_nachholen); erst
wenn auch das scheitert, kommt „fehler:<sitzung>“, ohne Versprechen. Unter /experte wie bisher.
Stufe 4 (08.10., Florian: „Ja, auffüllen“): Reicht der Abend nicht, füllt regie.erstelle im einfachen Modus mit nie
gezeigten starken Szenen früherer Abende auf (höchstens die Hälfte, Anfang vom Abend); „kein:<sitzung>“ kommt nur
noch, wenn auch das nicht reicht – der Satz sagt dann, warum.
Stufe 4, Nachtrag (08.10.): Kommt für den neuesten Abend ohne Video danach noch etwas an (Clip-Dateien, Matches, die
n8n erst später fertig hat, ein Match, das der PC erst beim nächsten Start schickt), baut der Timer das Video bis
NACHTRAG_H nach dem Abend selbst nach (_nachtrag) – vorher blieb es für immer bei „kein Video“. Nur einfacher Modus.
"""

from __future__ import annotations

import copy
import json
import logging
import sqlite3
from datetime import timedelta
from pathlib import Path

from . import db, einstellungen, entwurf, fail, lernen, material, regeln, regie, regie_lernen, stimmung
from .konfig import Konfig
from .medien import MedienFehler
from .verarbeitung import SESSION_ID
from .zeit import aus_iso, iso, jetzt, spielabend

log = logging.getLogger("pipeline")

LUECKE = timedelta(hours=2)   # mehr Pause zwischen zwei Matches: ein anderer Abend (auto_abend, _mit_spaeten)
NACHTRAG_H = 24               # Stufe 4: so lange nach dem Abend-Ende zählt spät angekommenes Material noch


def verarbeite(con: sqlite3.Connection, konfig: Konfig, *, claude: bool = True, whisper: bool = True) -> dict:
    konfig.pruefe_speicher()  # wirft SpeicherOffline -> Exit 3, kein Wecken
    ordner = konfig.wurzel / str(konfig.wert("sitzungen.ordner", "sitzungen"))
    fertig = {z["name"] for z in con.execute("SELECT name FROM sitzungen")}
    ergebnis: dict = {"neu": [], "wartet": [], "fehler": [], "nachgeholt": _nachholen(con, konfig), "nachtrag": []}
    abende: list[tuple[str, list[str], object]] = []
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
        abende.append((name, matches, ende))
    if (selbst := auto_abend(con, konfig, [m for _, ms, _ in abende for m in ms])) is not None:
        abende.append(selbst)            # ohne Datei vom PC: der Mini hat das Abend-Ende selbst erkannt
    for name, matches, ende in abende:
        status = {z["id"]: z["status"] for z in con.execute(
            f"SELECT id, status FROM matches WHERE id IN ({','.join('?' for _ in matches) or 'NULL'})", matches)}
        offen = [m for m in matches if status.get(m) != "verarbeitet"]
        hinweis = None
        if offen:
            if jetzt() - ende < timedelta(hours=float(konfig.wert("sitzungen.warten_h", 2))):
                ergebnis["wartet"].append({"sitzung": name, "offen": offen})
                continue
            hinweis = (f"{len(offen)} Match{'' if len(offen) == 1 else 'es'} nicht verarbeitet: "
                       f"{', '.join(offen[:5])}")
        tag = _tag(konfig, ende)
        db.lern_meldung(con, f"abend:{name}", f"🎮 Abend vom {tag} erkannt ({len(matches)} Match"
                        f"{'' if len(matches) == 1 else 'es'}) – ich baue dein Video. Das dauert meist 10–30 Minuten.")
        stimmung.analysiere(con, konfig, claude=claude, whisper=whisper)
        entwurf_id = None
        k = einstellungen.anwenden(con, konfig)            # ⚙️ und deine Regeln gelten auch für das Abend-Video
        experte = einstellungen.experte(con, k)
        _lerne(con, k)
        try:
            entwurf_id = _plane(con, k, matches)
            # Die Sitzung kennt ihren Entwurf schon VOR dem Rendern: sobald er fertig ist, kann der Lern-Bot ihn als
            # „Dein Abend vom …“ schicken und die Statuszeile löschen – vorher lag dazwischen ein kleines Rennen
            _merke(con, name, matches, ende, entwurf_id, hinweis)
            entwurf.entwurf(con, k, entwurf_id)
        except regie.ZuWenigSzenen as z:   # lieber kein Video als eins mit Füllmaterial (Florian, 07.10.)
            hinweis = "; ".join(filter(None, [hinweis, str(z)]))
            db.lern_meldung(con, f"kein:{name}", kein_video_text(tag, z, len(offen), experte=experte))
        except Exception as fehler:  # noqa: BLE001 – vermerken und dir sagen, statt alle 10 min still neu zu versuchen
            hinweis = "; ".join(filter(None, [hinweis, str(fehler)]))
            entwurf_id = _panne(con, name, tag, fehler, entwurf_id, experte=experte)
        _merke(con, name, matches, ende, entwurf_id, hinweis)
        ergebnis["neu"].append({"sitzung": name, "matches": len(matches), "entwurf": entwurf_id, "hinweis": hinweis})
    if not ergebnis["wartet"]:   # wartet ein neuer Abend noch auf n8n, ist der letzte bald der ältere – kein Nachtrag
        try:
            ergebnis["nachtrag"] = _nachtrag(con, konfig, claude=claude, whisper=whisper)
        except Exception:  # noqa: BLE001 – der Nachtrag ist Zugabe; die neuen Abende oben sind schon erledigt
            log.exception("Nachtrag eines Abends ohne Video")
    return ergebnis


def _lerne(con: sqlite3.Connection, k: Konfig) -> None:
    """Deine 👍/👎 bis eben lehren die Moment-Formel (wie vor jedem Entwurf im Lern-Bot)."""
    try:
        lernen.aktualisiere(con, k)
    except Exception:  # noqa: BLE001 – das Video ist wichtiger; dann gelten die bisherigen Gewichte
        log.exception("Moment-Formel vor dem Abend-Video")


def _plane(con: sqlite3.Connection, k: Konfig, matches: list[str]) -> int:
    """Short nur aus diesen Matches, mit deinen Regeln (regeln.anwenden) – Entwurf-Nummer; Fehler wie regie.erstelle
    (ZuWenigSzenen: lieber kein Video als Füllmaterial)."""
    fmt = str(k.wert("sitzungen.format", "short"))
    parameter, ziel = regie_lernen.aktuelle(con, k, fmt)
    parameter = regeln.anwenden(con, k, fmt, parameter)
    return regie.erstelle(con, k, fmt, parameter=parameter, ziel=ziel, nur_matches=set(matches))["entwurf"]


def _panne(con: sqlite3.Connection, name: str, tag: str, fehler: Exception, entwurf_id: int | None, *,
           experte: bool) -> int | None:
    """Fehler beim Bauen: Entwurf, den der nächste Lauf nachholt, oder None (dann steht die Fehlerzeile da).
    08.10.: Scheiterte erst das Rendern (z. B. hängende Grafikeinheit, Stolperstein 26.09.), behält die Sitzung im
    einfachen Modus ihren Entwurf, und der nächste Timer-Lauf rendert ihn einmal auf der CPU (_nachholen) – vorher war
    der Abend nach einem einzigen Fehler verloren, und es hieß nur „Beim nächsten Abend …“."""
    if entwurf_id is not None and not experte and not _dauerhaft(fehler):
        _logge(name, fehler, " – der nächste Lauf versucht es noch einmal auf der CPU")
        return entwurf_id
    _melde_fehler(con, name, tag, fehler, experte=experte)
    return None


def _nachtrag(con: sqlite3.Connection, konfig: Konfig, *, claude: bool = True, whisper: bool = True) -> list[dict]:
    """Stufe 4 (08.10., Florian: „autonom … besser und schneller als mit der Hand“): Endete der neueste Abend mit „kein
    Video“ und ist danach noch etwas angekommen – Clip-Dateien, Matches, die n8n erst später fertig hatte, oder ein
    Match, das der PC erst beim nächsten Start schickte (PC früh aus) –, baut der Bot das Video selbst nach. Vorher
    blieb es für immer bei „kein Video“, und du hättest 🎬 tippen müssen (Prüfer aufgeben/s5, s5b).

    Grenzen: nur im einfachen Modus; nur der neueste Abend (nie ältere, kein Video-Schwall), bis NACHTRAG_H nach seinem
    Ende; nur bei „kein:<sitzung>“ (ein Fehler beim Bauen wird nicht wiederholt); nur mit neuen Szenen seiner Matches
    seit dem letzten Versuch (Fails zählen nicht), und erst, wenn seit der jüngsten Szene und seit dem letzten
    Match-Ende [sitzungen].ruhe_min (45) vergangen sind – n8n arbeitet späte Matches eins nach dem anderen ab, und nach
    einer Pause spielst du vielleicht noch (wie bei auto_abend); höchstens ein Video je Abend – auch keins, wenn 🎬
    inzwischen eins aus seinen Matches gemacht hat. Vorher zieht er die Stimmung der Clips dieser Matches nach.
    Reicht es wieder nicht, bleibt deine Zeile „kein Video“ still stehen. Sonst wird die Statuszeile zu „🎮 Nachtrag:
    Abend vom …“, und das Video kommt wie ein Abend-Video (Panne beim Rendern: _nachholen).
    Rückgabe: [] oder [{"sitzung", "matches", "entwurf" (None = kein Video), "hinweis"}] für den versuchten Abend."""
    z = con.execute("SELECT * FROM sitzungen ORDER BY ende_utc DESC, name DESC LIMIT 1").fetchone()
    if z is None or z["entwurf_id"] is not None:
        return []
    name, ende, seit = z["name"], _zeit(z["ende_utc"]), z["verarbeitet"]
    if ende is None or jetzt() - ende >= timedelta(hours=NACHTRAG_H):
        return []
    if not _gemeldet(con, f"kein:{name}") or _gemeldet(con, f"fehler:{name}"):
        return []
    k = einstellungen.anwenden(con, konfig)
    if einstellungen.experte(con, k):
        return []                                      # /experte: wie bisher – „kein Video“ bleibt
    matches = _mit_spaeten(con, name, _ids(z["matches"]))
    if _schon_video(con, matches, seit):
        return []
    if offen := _offene_clips(con, konfig, matches):
        try:
            stimmung.analysiere(con, konfig, claude=claude, whisper=whisper, nur_clips=offen, maximal=len(offen))
        except Exception:  # noqa: BLE001 – dann eben mit den Szenen, die schon da sind
            log.exception("Nachtrag %s: Stimmung nachziehen", name)
    neueste = _neueste_szene(con, matches)
    if neueste is None or neueste <= seit:
        return []                                      # nichts Neues seit dem letzten Versuch
    ruhe = timedelta(minutes=float(konfig.wert("sitzungen.ruhe_min", 45) or 0))
    if jetzt() - aus_iso(neueste) < ruhe or jetzt() - _letztes_ende(con, matches, ende) < ruhe:
        return []                                      # vielleicht kommt gleich noch mehr, oder du spielst noch
    log.info("Nachtrag %s: neue Szenen seit %s – ich baue das Video", name, seit)
    con.execute("UPDATE sitzungen SET matches = ?, verarbeitet = ? WHERE name = ?",
                (json.dumps(matches), iso(jetzt()), name))
    tag = _tag(konfig, ende)
    _lerne(con, k)
    entwurf_id, hinweis = None, z["hinweis"]
    try:
        entwurf_id = _plane(con, k, matches)
        _merke(con, name, matches, ende, entwurf_id, None)
        hinweis = None
        db.lern_meldung(con, f"nachtrag:{name}", f"🎮 Nachtrag: Abend vom {tag} – inzwischen sind weitere Szenen "
                                                 "angekommen, ich baue dein Video. Das dauert meist 10–30 Minuten.")
        entwurf.entwurf(con, k, entwurf_id)
    except regie.ZuWenigSzenen as fehler:   # reicht noch nicht: still – deine Zeile „kein Video“ bleibt stehen
        hinweis = str(fehler)
        log.info("Nachtrag %s: %s", name, fehler)
    except Exception as fehler:  # noqa: BLE001
        if entwurf_id is None:   # schon das Planen scheiterte: Details ins Log, deine Zeile bleibt
            _logge(name, fehler, " (Nachtrag)")
        else:
            hinweis = str(fehler)
            entwurf_id = _panne(con, name, tag, fehler, entwurf_id, experte=False)
    _merke(con, name, matches, ende, entwurf_id, hinweis)
    return [{"sitzung": name, "matches": len(matches), "entwurf": entwurf_id, "hinweis": hinweis}]


def _gemeldet(con: sqlite3.Connection, schluessel: str) -> bool:
    return con.execute("SELECT 1 FROM lern_meldungen WHERE schluessel = ?", (schluessel,)).fetchone() is not None


def _ids(text: str | None) -> list[str]:
    """sitzungen.matches als Liste (kaputtes JSON: leer)."""
    try:
        werte = json.loads(text or "[]")
    except ValueError:
        return []
    return [m for m in werte if isinstance(m, str)] if isinstance(werte, list) else []


def _mit_spaeten(con: sqlite3.Connection, name: str, matches: list[str]) -> list[str]:
    """Spät angekommene Matches desselben Abends dazu (Nachtrag). Ein selbst erkannter Abend (abend_…) kennt nur die
    Matches, die beim Erkennen da waren; kam danach noch eins an (PC früh aus: sein Replay kommt erst beim nächsten
    Start), gehört es dazu, wenn es wie bei auto_abend höchstens LUECKE vor oder nach einem Match des Abends liegt und
    in keiner anderen Sitzung steht. Die Datei vom PC nennt ihre Matches selbst – dort bleibt die Liste, wie sie ist."""
    if not name.startswith("abend_"):
        return matches
    bekannt = set(matches)
    for z in con.execute("SELECT matches FROM sitzungen WHERE name != ?", (name,)):
        bekannt.update(_ids(z["matches"]))
    zeiten = {}
    for z in con.execute("SELECT id, start_utc, ende_utc FROM matches"):
        start, ende = _zeit(z["start_utc"]), _zeit(z["ende_utc"])
        if start is not None:
            zeiten[z["id"]] = (start, ende or start)
    eigene = [zeiten[m] for m in matches if m in zeiten]
    if not eigene:
        return matches
    von, bis = min(s for s, _ in eigene), max(e for _, e in eigene)
    dazu = []
    for m, (start, ende) in sorted(((m, t) for m, t in zeiten.items() if m not in bekannt), key=lambda x: x[1][0]):
        if ende >= von - LUECKE and start <= bis + LUECKE:   # nach dem Abend auch über mehrere Matches hinweg
            dazu.append(m)
            bis = max(bis, ende)
    return [*matches, *dazu]


def _schon_video(con: sqlite3.Connection, matches: list[str], seit: str) -> bool:
    """Hat seit dem letzten Versuch schon ein Short Szenen dieser Matches (z. B. über 🎬 mit den spät angekommenen
    Szenen)? Dann hat der Abend sein Video – sonst kämen zwei mit fast denselben Szenen (07.10.: „warum sendet er immer
    2 Videos?“). Szenen früherer Abende im Video zählen nicht (regeln.matches_aus)."""
    for z in con.execute("SELECT schnittliste FROM entwuerfe WHERE erstellt > ? AND format = 'short' "
                         "AND auto_verworfen IS NULL", (seit,)):
        try:
            liste = json.loads(Path(z["schnittliste"]).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(liste, dict) and regeln.matches_aus(liste) & set(matches):
            return True
    return False


def _offene_clips(con: sqlite3.Connection, konfig: Konfig, matches: list[str]) -> list[int]:
    """Clips dieser Matches ohne Stimmung, deren Datei schon da ist (wie einstellungen.offene_clips, die besten zuerst;
    von dir verworfene nie) – nur dann lohnt das Nachziehen: stimmung.analysiere liest dafür alle Replays."""
    if not matches:
        return []
    platz = ",".join("?" for _ in matches)
    return [z["id"] for z in con.execute(
        f"""SELECT c.id, c.clip_pfad FROM clips c
             WHERE c.match_id IN ({platz}) AND c.clip_pfad IS NOT NULL AND NOT {db.hart_verworfen_sql("c.")}
               AND NOT EXISTS (SELECT 1 FROM momente mo WHERE mo.clip_id = c.id)
             ORDER BY c.punkte DESC, c.id""", matches).fetchall()
        if material.lokal(konfig, z["clip_pfad"]).is_file()]


def _letztes_ende(con: sqlite3.Connection, matches: list[str], ende):
    """Spätestes Match-Ende des Abends, mindestens das Abend-Ende (unlesbare Zeiten zählen nicht). Wie bei auto_abend:
    Erst ruhe_min danach ist der Abend vorbei – spielst du nach einer Pause weiter, gehören die neuen Matches dazu."""
    platz = ",".join("?" for _ in matches)
    zeiten = [_zeit(z[0]) for z in con.execute(f"SELECT ende_utc FROM matches WHERE id IN ({platz})", matches)]
    return max([ende, *(t for t in zeiten if t is not None)])


def _neueste_szene(con: sqlite3.Connection, matches: list[str]) -> str | None:
    """Wann die jüngste Szene (Moment) dieser Matches entstand – ohne Fails, die kommen ins Abend-Video nie."""
    if not matches:
        return None
    platz = ",".join("?" for _ in matches)
    return con.execute(f"SELECT MAX(erstellt) FROM momente WHERE match_id IN ({platz}) AND schluessel NOT LIKE ?",
                       [*matches, f"{fail.PRAEFIX}%"]).fetchone()[0]


def auto_abend(con: sqlite3.Connection, konfig: Konfig, schon: list[str] = ()) -> tuple[str, list[str], object] | None:
    """Abend-Ende ohne den Gaming-PC erkennen (07.10., Florian: „es kommen immer noch die Clips von vor 14 Tagen, nicht
    die neueste Session“ – die Datei vom PC kam nie, SessionVorbeiMinuten steht ab Werk auf 0). Der letzte Block von
    Matches (Lücke ≤ 2 h, Start in den letzten 18 h) gilt als Abend, wenn seit dem letzten Match-Ende
    [sitzungen].ruhe_min (45) vergangen sind und noch keine Sitzung eines seiner Matches kennt. Name: abend_<1. Match>.
    Ältere Abende werden nie nachgeholt (kein Video-Schwall nach dem Update)."""
    ruhe = float(konfig.wert("sitzungen.ruhe_min", 45) or 0)
    if ruhe <= 0:
        return None
    jetzt_utc = jetzt()
    zeilen = []                                       # (id, Start, Ende) – unlesbare Zeiten überspringen
    for z in con.execute("SELECT id, start_utc, ende_utc FROM matches WHERE start_utc >= ?",
                         (iso(jetzt_utc - timedelta(hours=18)),)):
        start, ende = _zeit(z["start_utc"]), _zeit(z["ende_utc"])
        if start is not None and start <= jetzt_utc:
            zeilen.append((z["id"], start, ende or start))
    if not zeilen:
        return None
    zeilen.sort(key=lambda x: x[1])
    block = [zeilen[-1]]
    for z in reversed(zeilen[:-1]):
        if block[0][1] - z[2] > LUECKE:               # mehr als 2 h Pause: ein früherer Abend
            break
        block.insert(0, z)
    ende = max(z[2] for z in block)
    if jetzt_utc - ende < timedelta(minutes=ruhe):
        return None                                   # vielleicht wird noch gespielt
    ids = [z[0] for z in block]
    bekannt = set(schon)
    for z in con.execute("SELECT matches FROM sitzungen"):
        try:
            bekannt.update(json.loads(z["matches"] or "[]"))
        except ValueError:
            continue
    if bekannt & set(ids):
        return None
    name = f"abend_{ids[0]}"
    return (name, ids, ende) if SESSION_ID.fullmatch(name) else None


def _zeit(text):
    try:
        return aus_iso(text) if text else None
    except (TypeError, ValueError):
        return None


def kein_video_text(tag: str, z: regie.ZuWenigSzenen, offen: int = 0, experte: bool = True) -> str:
    """„🎮 Abend vom 06.10.: nur 2 starke Szenen (…), ein Video braucht 4 – heute kein Video.“ + Grund/Tipp.
    08.10.: der Tipp über ⚙️ nur im Experten-Modus; offene Matches „waren noch nicht fertig“ (vorher „kamen nie bei
    mir an“ – sie waren da, nur nach der Wartezeit noch nicht verarbeitet)."""
    text = f"🎮 Abend vom {tag}: {z.kopf()} – heute kein Video."
    if offen:
        text += f" {offen} Match{'' if offen == 1 else 'es'} war{'' if offen == 1 else 'en'} noch nicht fertig."
    return f"{text} {z.tipp(experte)}".strip()


def _tag(konfig: Konfig, ende) -> str:
    """Der Spielabend wie überall (zeit.spielabend, Tageswechsel 06:00). 07.10.: vorher das Datum des Abend-Endes –
    ein Abend bis 01:30 hieß „Abend vom 07.10.“, während ⚙️ und 🎬 Neues Video „Spielabend 06.10.“ sagten."""
    zone, wechsel = konfig.wert("zeit.zeitzone", "Europe/Berlin"), int(konfig.wert("zeit.tageswechsel_stunde", 6))
    return f"{spielabend(ende, zone, wechsel):%d.%m.}"


def _logge(name: str, fehler: Exception, zusatz: str = "") -> None:
    """Details ins Log (die Zeile für dich bleibt ohne Fachtext) – Unerwartetes mit Stacktrace."""
    if not isinstance(fehler, (regie.RegieFehler, MedienFehler)):
        log.error("Abend-Video %s%s", name, zusatz, exc_info=fehler)
    else:
        log.warning("Abend-Video %s: %s%s", name, fehler, zusatz)


def _melde_fehler(con: sqlite3.Connection, name: str, tag: str, fehler: Exception, *, experte: bool = True,
                  zweiter: bool = False) -> None:
    """Die Zeile für dich ohne Fachtext (07.10.: vorher stand die rohe Fehlermeldung darin) – Details ins Log.
    08.10.: im einfachen Modus ohne das leere „Beim nächsten Abend versuche ich es wieder“ – den zweiten Versuch für
    diesen Abend macht der Bot schon selbst (zweiter: er ist gescheitert). Unter /experte der Satz wie bisher."""
    _logge(name, fehler)
    if experte:
        text = (f"⚠️ Für deinen Abend vom {tag} kam kein Video zustande – beim Bauen ging etwas schief. "
                "Beim nächsten Abend versuche ich es wieder.")
    else:
        text = (f"⚠️ Für deinen Abend vom {tag} kam kein Video zustande – beim Bauen ging "
                f"{'zweimal ' if zweiter else ''}etwas schief.")
    db.lern_meldung(con, f"fehler:{name}", text)


def _dauerhaft(fehler: Exception) -> bool:
    """Render-Fehler, die ein zweiter Versuch nicht behebt (08.10.): Eine Moment-Datei, die Musik oder die Schnittliste
    fehlt, oder die geplante Länge passt nicht ins Format – dann gleich die Fehlerzeile. ffmpeg, Grafikeinheit, Platte
    und Dateigröße dagegen können beim nächsten Mal (auf der CPU) klappen. Wie lernbot_paket.dauerhaft."""
    if isinstance(fehler, FileNotFoundError):
        return True
    return isinstance(fehler, MedienFehler) and str(fehler).startswith(("Moment-Datei fehlt", "Musik fehlt",
                                                                        "Ungültige Videolänge"))


def _auf_cpu(konfig: Konfig) -> Konfig:
    """Kopie der Konfig, die ohne Grafikeinheit rendert (regie.vaapi = false, entwurf.encoder -> libx264) – die
    geladene Konfig bleibt, wie sie ist."""
    daten = copy.deepcopy(konfig.daten)
    daten.setdefault("regie", {})["vaapi"] = False
    return Konfig(daten=daten, quelle=konfig.quelle)


def _nachholen(con: sqlite3.Connection, konfig: Konfig) -> list[str]:
    """Abend-Videos, deren Rendern abbrach (Update, Neustart, Strom) oder im einfachen Modus einmal scheiterte
    (verarbeite, 08.10.): Die Sitzung kennt ihren Entwurf schon, der steht aber noch auf „neu“ ohne Datei.
    entwurf.entwurf ist idempotent – einfach nochmal rendern, bis 12 h nach dem Abend; im einfachen Modus auf der CPU,
    denn die Grafikeinheit hängt sporadisch (Stolperstein 26.09.). Scheitert auch das (oder ist es zu spät), kommt die
    Fehlerzeile statt endloser Versuche alle 10 Minuten – je Abend also höchstens ein zusätzlicher Render."""
    offen = con.execute("""SELECT s.name, s.entwurf_id, s.ende_utc, s.verarbeitet, s.hinweis FROM sitzungen s
                           JOIN entwuerfe e ON e.id = s.entwurf_id WHERE e.status = 'neu' AND e.datei IS NULL""").fetchall()
    if not offen:
        return []
    k = einstellungen.anwenden(con, konfig)
    experte = einstellungen.experte(con, k)
    if not experte:
        k = _auf_cpu(k)
    grenze = jetzt() - timedelta(hours=12)
    nachgeholt = []
    for z in offen:
        tag = _tag(konfig, aus_iso(z["ende_utc"]) if z["ende_utc"] else jetzt())
        try:
            if aus_iso(z["verarbeitet"]) < grenze:
                raise MedienFehler("das Bauen wurde immer wieder unterbrochen")
            log.info("Abend-Video %s: Entwurf #%s ist noch nicht fertig – rendere ihn neu%s", z["name"],
                     z["entwurf_id"], "" if experte else " (CPU)")
            entwurf.entwurf(con, k, z["entwurf_id"])
            nachgeholt.append(z["name"])
        except Exception as fehler:  # noqa: BLE001 – wie beim ersten Versuch: vermerken und dir sagen
            con.execute("UPDATE sitzungen SET entwurf_id = NULL, hinweis = ? WHERE name = ?",
                        ("; ".join(filter(None, [z["hinweis"], str(fehler)])), z["name"]))
            _melde_fehler(con, z["name"], tag, fehler, experte=experte, zweiter=True)
    return nachgeholt


def _merke(con: sqlite3.Connection, name: str, matches: list[str], ende, entwurf_id: int | None,
           hinweis: str | None) -> None:
    """Sitzung als verarbeitet merken bzw. ihren Stand nachtragen (Entwurf, Hinweis)."""
    con.execute(
        """INSERT INTO sitzungen (name, matches, ende_utc, entwurf_id, hinweis, verarbeitet) VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT (name) DO UPDATE SET entwurf_id = excluded.entwurf_id, hinweis = excluded.hinweis""",
        (name, json.dumps(matches), iso(ende), entwurf_id, hinweis, iso(jetzt())),
    )
