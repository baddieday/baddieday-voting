"""Auto-Freigabe im Clip-Bot (30.09., Florian: „nicht jeden Clip per Hand separat freigeben“).

Vollautonom (Florian 30.09.: „muss es Referenzen geben, wenn ich sage, es soll autonom passieren?“): Standard ist
`vollautonom = true` – JEDER Clip wird beim ersten Senden sofort entschieden, ohne Referenz-Urteile von dir, ohne
Stichproben (stichprobe_jede = 0), ohne Warten. Deine Tipps bleiben freiwillig und zählen dann beim Lernen.

Wege, auf denen ein Clip entschieden wird:
  A. Sofort beim ersten Senden (bot/app.sende_outbox → vorschlag): Regel aus Replay-Fakten (Serie ≥ regel_gruppe
     oder Victory Royale → frei), die festgeschriebene Erwartung p gegen das Tor (stufe) und – vollautonom – sonst
     p ≥ 0,5 → frei, darunter weich aussortiert (ohne p: Serie ≥ 2 → frei). Der Clip wird trotzdem gesendet – ohne
     Ton, mit 🤖-Zeile und Knöpfen zum Umdrehen.
  B. Zu dir: nur mit vollautonom = false (die Fälle ohne Regel/Tor), mit stichprobe_jede ≥ 2 jede n-te
     Sofort-Entscheidung (🎲) und im Modus „probe“ alles (👀).
  C. Frist: Liegt ein Clip frist_h Stunden offen (Altbestand, Fehlerfall), entscheidet der Bot selbst (frist).

Das Tor wächst mit gemessener Genauigkeit: Eine Stufe (z. B. „ab 85 %“) gilt erst, wenn deine letzten `fenster`
Urteile mit p in diesem Band oft genug (ziel_quote) gestimmt haben. Gezählt wird nur, was DU entschieden hast –
automatische Entscheidungen sind nie Lerndaten (weder Gewichte noch Erwartung noch dieses Tor), und ein „👍 Stimmt“
auf einem Sofort-Clip zählt fürs Tor nicht (selbst ausgewählte Treffer). Korrekturen zählen als Fehltreffer.

Aussortieren durch den Bot ist immer weich: Der Clip bleibt Material für Regisseur, Highlight und Mikro
(db.hart_verworfen_sql); nur dein 🗑️ schließt aus. Gelöscht wird nichts. Reine Datenbank-Logik, ohne Telegram testbar.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from . import db, erwartung
from .konfig import Konfig
from .zeit import aus_iso, iso, jetzt, utc_zu_lokal

log = logging.getLogger("clip-bot")

STANDARD = {"modus": "an", "ziel_quote": 0.9, "verwerfen": True, "frist_h": 24, "regel_gruppe": 3,
            "stufen_frei": [0.95, 0.9, 0.85, 0.8], "stufen_weg": [0.05, 0.1, 0.15], "fenster": 30, "mindest_n": 15,
            "stichprobe_jede": 0, "frist_je_lauf": 20, "vollautonom": True}
MODI = ("an", "probe", "aus")
TRIPLE = 3              # ab Triple (oder Victory) wird nie aussortiert – egal, was regel_gruppe sagt
FRIST_SCHWELLE = 0.5    # nach der Frist: p ab hier frei, darunter weich aussortiert (wie erwartung.SCHWELLE)
ZUSAMMENFASSUNG_MAX = 30  # mehr Clips je Zeile → „+ n weitere“ (Telegram: 4096 Zeichen je Nachricht)


@dataclass(frozen=True)
class Vorschlag:
    art: str    # sofort · stichprobe · probe
    ziel: str   # freigegeben · verworfen
    grund: str  # Klartext, z. B. „Erwartung 91 % ≥ Stufe 85 % (27/29 richtig)“


def werte(konfig: Konfig) -> dict:
    """[auto_freigabe] mit Standardwerten (Konfigs ohne den Abschnitt laufen). Zahlen über float()/int(), ein
    unbekannter Modus gilt als „aus“ (im Zweifel fragt der Bot)."""
    roh = konfig.wert("auto_freigabe", {}) or {}
    w = {**STANDARD, **{k: v for k, v in roh.items() if v is not None}}
    modus = str(w["modus"]).strip().lower()
    return {
        "modus": modus if modus in MODI else "aus",
        "ziel_quote": float(w["ziel_quote"]),
        "verwerfen": bool(w["verwerfen"]),
        "frist_h": max(0, int(w["frist_h"])),
        "regel_gruppe": int(w["regel_gruppe"]),
        "stufen_frei": sorted((float(s) for s in w["stufen_frei"]), reverse=True),  # streng → locker
        "stufen_weg": sorted(float(s) for s in w["stufen_weg"]),                    # streng → locker
        "fenster": max(1, int(w["fenster"])),
        "mindest_n": max(1, int(w["mindest_n"])),
        # 0/1 = keine Stichproben (vollautonom, Standard); ab 2 jede n-te Sofort-Entscheidung zu dir
        "stichprobe_jede": max(0, int(w["stichprobe_jede"])),
        "frist_je_lauf": max(1, int(w["frist_je_lauf"])),
        "vollautonom": bool(w["vollautonom"]),
    }


def _feld(clip, name: str):
    """Spalte lesen, auch wenn sie (alte Zeile, dict aus Tests) fehlt."""
    return clip[name] if name in clip.keys() else None


def _prozent(p: float) -> str:
    return f"{round(100 * p)} %"


# --- Tor --------------------------------------------------------------------------------------------------------

def band(con: sqlite3.Connection, *, ab: float | None = None, bis: float | None = None,
         fenster: int = 30) -> tuple[int, int]:
    """(Treffer, n) deiner letzten `fenster` Urteile mit festgeschriebener Erwartung p ≥ ab (Treffer = freigegeben)
    bzw. p ≤ bis (Treffer = verworfen), sortiert wie erwartung.trefferquote (erstellt, ziel_id absteigend).

    Nur deine Urteile (freigabe_quelle nicht 'auto'); ein „👍 Stimmt“ auf einem Sofort-Clip zählt nicht (die hast du
    selbst ausgewählt). Genau eine der Grenzen ab/bis angeben, sonst ValueError."""
    if (ab is None) == (bis is None):
        raise ValueError("band: genau eine Grenze angeben (ab oder bis)")
    positiv = ", ".join("?" for _ in db.BEWERTET)
    bedingung, grenze = ("e.wahrschein >= ?", ab) if ab is not None else ("e.wahrschein <= ?", bis)
    zeilen = con.execute(
        f"""SELECT CASE WHEN c.status = 'verworfen' THEN 0 ELSE 1 END AS gut
              FROM erwartungen e JOIN clips c ON c.id = e.ziel_id
             WHERE e.art = 'clip' AND (c.status = 'verworfen' OR c.status IN ({positiv}))
               AND c.freigabe_quelle IS NOT 'auto'
               AND NOT (c.auto_art IS 'sofort' AND c.auto_vorschlag IS
                        (CASE WHEN c.status = 'verworfen' THEN 'verworfen' ELSE 'freigegeben' END))
               AND {bedingung}
             ORDER BY e.erstellt DESC, e.ziel_id DESC LIMIT ?""",
        (*db.BEWERTET, grenze, fenster)).fetchall()
    gut = sum(int(z["gut"]) for z in zeilen)
    return (gut if ab is not None else len(zeilen) - gut), len(zeilen)


def _suche(con, stufen: list[float], richtung: str, w: dict) -> tuple[float | None, list[dict]]:
    """Von der strengsten Stufe zur lockersten: zu wenig Urteile → überspringen; genug, aber unter der Ziel-Quote →
    Schluss; aktiv ist die lockerste davor mit genug Urteilen und Quote."""
    aktiv, zeilen = None, []
    for s in stufen:
        t, n = band(con, **({"ab": s} if richtung == "frei" else {"bis": s}), fenster=w["fenster"])
        if n < w["mindest_n"]:
            zeilen.append({"stufe": s, "treffer": t, "n": n, "urteil": "wenig"})
            continue
        if t / n < w["ziel_quote"]:
            zeilen.append({"stufe": s, "treffer": t, "n": n, "urteil": "nein"})
            break
        zeilen.append({"stufe": s, "treffer": t, "n": n, "urteil": "ok"})
        aktiv = s
    return aktiv, zeilen


def stufe(con: sqlite3.Connection, konfig: Konfig) -> dict:
    """Aktive Stufen {"frei_ab": 0.85 | None, "weg_bis": 0.1 | None, "zeilen": {"frei": […], "weg": […]}}.
    Einmal je Outbox-Runde neu (reines SQL): Korrekturen lassen die Automatik schrumpfen, stimmende Stichproben
    wachsen. Aussortieren nur, wenn [auto_freigabe].verwerfen an ist."""
    w = werte(konfig)
    frei_ab, frei = _suche(con, w["stufen_frei"], "frei", w)
    weg_bis, weg = _suche(con, w["stufen_weg"], "weg", w) if w["verwerfen"] else (None, [])
    return {"frei_ab": frei_ab, "weg_bis": weg_bis, "zeilen": {"frei": frei, "weg": weg}}


def _band_text(zeilen: list[dict], s: float) -> str:
    z = next((z for z in zeilen if z["stufe"] == s), None)
    return f" ({z['treffer']}/{z['n']} richtig)" if z else ""


def _schutz(clip) -> bool:
    """Triple+ oder Victory Royale: nie aussortieren."""
    return int(_feld(clip, "max_gruppe") or 0) >= TRIPLE or bool(_feld(clip, "victory_royale"))


def vorschlag(con: sqlite3.Connection, konfig: Konfig, clip, p: float | None, stand: dict) -> Vorschlag | None:
    """Was die Automatik mit einem frisch zu sendenden Clip täte, oder None (dann normal zu dir).

    Regel zuerst (Serie ≥ regel_gruppe oder Victory → frei), sonst p gegen das Tor: p ≥ frei_ab → frei;
    p ≤ weg_bis → weich aussortiert, nie bei Triple+/Victory. art: „probe“ im Modus probe, „stichprobe“ für jede
    clip_id % stichprobe_jede == 0 (deterministisch, auch für Regel-Fälle), sonst „sofort“."""
    w = werte(konfig)
    if w["modus"] == "aus":
        return None
    gruppe = int(_feld(clip, "max_gruppe") or 0)
    if _feld(clip, "victory_royale"):
        ziel, grund = "freigegeben", "Regel: Victory Royale"
    elif gruppe >= w["regel_gruppe"]:
        ziel, grund = "freigegeben", f"Regel: {gruppe}er-Serie"
    elif p is not None and stand.get("frei_ab") is not None and p >= stand["frei_ab"]:
        s = stand["frei_ab"]
        ziel = "freigegeben"
        grund = f"Erwartung {_prozent(p)} ≥ Stufe {_prozent(s)}" + _band_text(stand["zeilen"]["frei"], s)
    elif (p is not None and w["verwerfen"] and stand.get("weg_bis") is not None and p <= stand["weg_bis"]
          and not _schutz(clip)):
        s = stand["weg_bis"]
        ziel = "verworfen"
        grund = f"Erwartung {_prozent(p)} ≤ Stufe {_prozent(s)}" + _band_text(stand["zeilen"]["weg"], s)
    elif w["vollautonom"]:  # ohne Referenz-Urteile: die beste eigene Schätzung entscheidet sofort
        if p is not None:
            ziel = "freigegeben" if p >= FRIST_SCHWELLE else "verworfen"
            grund = f"Erwartung {_prozent(p)}"
        else:
            ziel = "freigegeben" if gruppe >= 2 else "verworfen"
            grund = "ohne Erwartung, " + (f"{gruppe}er-Serie" if gruppe >= 2 else "Einzelkill")
        if ziel == "verworfen" and (not w["verwerfen"] or _schutz(clip)):
            return None
    else:
        return None
    if w["modus"] == "probe":
        art = "probe"
    elif w["stichprobe_jede"] >= 2 and int(clip["id"]) % w["stichprobe_jede"] == 0:
        art = "stichprobe"
    else:
        art = "sofort"
    return Vorschlag(art, ziel, grund)


# --- Texte am Clip ---------------------------------------------------------------------------------------------

def hinweis_zeile(clip, konfig: Konfig) -> str | None:
    """Zusatzzeile (Klartext) für einen Clip, der zu dir geht bzw. offen liegt (Status vorbewertet/gesendet):
    🎲 Stichprobe, 👀 Probelauf oder „⏰ Liegen lassen ist ok …“ (nur Modus an und frist_h > 0). Sonst None – die
    🤖-Zeile entschiedener Clips baut texte.clip_text an die Status-Zeile."""
    if clip["status"] not in ("vorbewertet", "gesendet"):
        return None
    w = werte(konfig)
    art, ziel = _feld(clip, "auto_art"), _feld(clip, "auto_vorschlag")
    wort = "freigegeben" if ziel == "freigegeben" else "aussortiert"
    if art == "stichprobe":
        return f"🎲 Stichprobe – ich hätte {wort}. Dein Tipp hält mich ehrlich."
    if art == "probe":
        tun = "freigeben" if ziel == "freigegeben" else "aussortieren"
        return f"👀 Probelauf – würde automatisch {tun} ({_feld(clip, 'auto_grund') or '?'})."
    if w["modus"] == "an" and w["frist_h"] > 0:
        return f"⏰ Liegen lassen ist ok – nach {w['frist_h']} h entscheide ich selbst."
    return None


# --- Frist -----------------------------------------------------------------------------------------------------

def frist(con: sqlite3.Connection, konfig: Konfig, zeit: datetime | None = None) -> list[dict]:
    """Offene Clips (gesendet), die länger als frist_h vorliegen, selbst entscheiden – höchstens frist_je_lauf.

    Erst Altbestand: gesendet ohne vorgelegt bekommt vorgelegt = jetzt (idempotent; entschieden wird er also erst
    frist_h später). Dann je fälligem Clip: Triple+/Victory → frei; sonst p (festgeschrieben) ≥ 0,5 → frei, sonst
    weich aussortiert; ohne p: Serie ≥ 2 → frei, sonst aussortiert. Aussortieren nur, wenn verwerfen an ist – sonst
    bleibt der Clip offen. Nur im Modus „an“ und bei frist_h > 0. Vollautonom: Clips ohne auto_art (Altbestand,
    Fehlerfall) sind sofort fällig – nur Stichproben (🎲) warten die Frist ab.
    Rückgabe: [{"id", "status", "tg_nachricht_id", "grund"}] der entschiedenen Clips (für die Nachrichten)."""
    w = werte(konfig)
    if w["modus"] != "an":
        return []
    zeit = zeit or jetzt()
    con.execute("UPDATE clips SET vorgelegt = ? WHERE status = 'gesendet' AND vorgelegt IS NULL", (iso(zeit),))
    if w["frist_h"] <= 0 and not w["vollautonom"]:
        return []
    grenze = iso(zeit - timedelta(hours=w["frist_h"])) if w["frist_h"] > 0 else ""   # "" = Frist nie
    faellig = con.execute(
        "SELECT * FROM clips WHERE status = 'gesendet' AND vorgelegt IS NOT NULL"
        " AND (vorgelegt <= ? OR (? AND auto_art IS NULL)) ORDER BY vorgelegt, id",
        (grenze, int(w["vollautonom"]))).fetchall()
    ergebnis = []
    for c in faellig:
        if len(ergebnis) >= w["frist_je_lauf"]:
            break
        p = erwartung.gespeichert(con, "clip", int(c["id"]))
        sofort = w["vollautonom"] and _feld(c, "auto_art") is None and (c["vorgelegt"] or "") > grenze
        kopf = "vollautonom" if sofort else f"Frist {w['frist_h']} h ohne Antwort"
        if _schutz(c):
            ziel, grund = "freigegeben", f"{kopf} · " + ("Victory Royale" if c["victory_royale"] else
                                                         f"{c['max_gruppe']}er-Serie")
        elif p is not None:
            ziel, grund = ("freigegeben" if p >= FRIST_SCHWELLE else "verworfen"), f"{kopf} · Erwartung {_prozent(p)}"
        else:
            ziel = "freigegeben" if int(c["max_gruppe"] or 0) >= 2 else "verworfen"
            grund = f"{kopf} · ohne Erwartung, " + ("Einzelkill" if int(c["max_gruppe"] or 0) < 2 else
                                                     f"{c['max_gruppe']}er-Serie")
        if ziel == "verworfen" and not w["verwerfen"]:
            continue  # bleibt offen
        with db.transaktion(con):
            if not db.status_wechsel(con, int(c["id"]), ("gesendet",), ziel, quelle="auto", grund=grund):
                continue
            # auto_vorschlag nur, wenn beim Senden keiner stand – dann weiß die Bildunterschrift später, ob du die
            # Frist-Entscheidung bestätigt oder umgedreht hast
            con.execute("UPDATE clips SET auto_grund = ?, auto_vorschlag = COALESCE(auto_vorschlag, ?) WHERE id = ?",
                        (grund, ziel, int(c["id"])))
        ergebnis.append({"id": int(c["id"]), "status": ziel, "tg_nachricht_id": c["tg_nachricht_id"],
                         "grund": grund})
    return ergebnis


def _nummern(ids: list[int]) -> str:
    teile = [f"#{i}" for i in ids[:ZUSAMMENFASSUNG_MAX]]
    if len(ids) > ZUSAMMENFASSUNG_MAX:
        teile.append(f"+ {len(ids) - ZUSAMMENFASSUNG_MAX} weitere")
    return ", ".join(teile)


def frist_text(ergebnis: list[dict], stunden: int) -> str:
    """„⏰ 3 offene Clips nach 24 h selbst entschieden: ✅ #43, #45 · 🗑️ #48 (bleibt nutzbar). …“ (Klartext)."""
    frei = [e["id"] for e in ergebnis if e["status"] == "freigegeben"]
    weg = [e["id"] for e in ergebnis if e["status"] == "verworfen"]
    teile = ([f"✅ {_nummern(frei)}"] if frei else []) + ([f"🗑️ {_nummern(weg)} (bleibt nutzbar)"] if weg else [])
    n = len(ergebnis)
    wann = "" if all(e["grund"].startswith("vollautonom") for e in ergebnis) else f" nach {stunden} h"
    return (f"⏰ {n} offene{'r' if n == 1 else ''} Clip{'' if n == 1 else 's'}{wann} selbst entschieden: "
            + " · ".join(teile) + ". Umdrehen am Clip oder /clip <nr>.")


# --- Zusammenfassung je Match -----------------------------------------------------------------------------------

def faellige_zusammenfassungen(con: sqlite3.Connection) -> list[str]:
    """Matches, die fertig verarbeitet sind (render durch), keinen vorbewerteten Clip mehr haben, mindestens eine
    Sofort-Entscheidung enthalten und noch keine Zusammenfassung (Meldung auto:<match>) bekamen."""
    return [z["id"] for z in con.execute(
        """SELECT m.id FROM matches m
            WHERE m.status = 'verarbeitet'
              AND NOT EXISTS (SELECT 1 FROM clips c WHERE c.match_id = m.id AND c.status = 'vorbewertet')
              AND EXISTS (SELECT 1 FROM clips c WHERE c.match_id = m.id AND c.auto_art = 'sofort')
              AND NOT EXISTS (SELECT 1 FROM meldungen WHERE schluessel = 'auto:' || m.id)
            ORDER BY m.start_utc, m.id""")]


def _kurzgrund(con: sqlite3.Connection, c) -> str:
    grund = c["auto_grund"] or ""
    if grund.startswith("Regel: "):
        return "Regel " + grund[len("Regel: "):]
    p = erwartung.gespeichert(con, "clip", int(c["id"]))
    return _prozent(p) if p is not None else ""


def zusammenfassung_text(con: sqlite3.Connection, match_id: str, konfig: Konfig) -> str:
    """Stille Zusammenfassung eines Matches (Klartext), z. B.
    „🤖 Match 28.09. 21:42 · 4 Kills · 7 Clips“ / „✅ 4 selbst freigegeben: #41 Regel 3er-Serie · #42 93 %“ / …"""
    w = werte(konfig)
    m = db.match(con, match_id)
    clips = con.execute("SELECT * FROM clips WHERE match_id = ? ORDER BY id", (match_id,)).fetchall()
    kopf = f"🤖 Match {match_id}"
    if m is not None:
        kopf = f"🤖 Match {utc_zu_lokal(aus_iso(m['start_utc']), konfig.wert('zeit.zeitzone', 'Europe/Berlin')):%d.%m. %H:%M}"
        if m["kills"] is not None:
            kopf += f" · {m['kills']} Kill" + ("" if m["kills"] == 1 else "s")
    kopf += f" · {len(clips)} Clip" + ("" if len(clips) == 1 else "s")
    sofort = [c for c in clips if c["auto_art"] == "sofort"]
    frei = [c for c in sofort if c["auto_vorschlag"] == "freigegeben"]
    weg = [c for c in sofort if c["auto_vorschlag"] == "verworfen"]
    bei_dir = len(clips) - len(sofort)

    def liste(zeilen) -> str:
        teile = [f"#{c['id']} {_kurzgrund(con, c)}".rstrip() for c in zeilen[:ZUSAMMENFASSUNG_MAX]]
        if len(zeilen) > ZUSAMMENFASSUNG_MAX:
            teile.append(f"+ {len(zeilen) - ZUSAMMENFASSUNG_MAX} weitere")
        return " · ".join(teile)

    zeilen = [kopf]
    if frei:
        zeilen.append(f"✅ {len(frei)} selbst freigegeben: {liste(frei)}")
    if weg:
        zeilen.append(f"🗑️ {len(weg)} aussortiert (bleibt für Shorts nutzbar): {liste(weg)}")
    if bei_dir:
        zeile = f"🙋 {bei_dir} bei dir – antworten oder liegen lassen"
        zeilen.append(zeile + (f", nach {w['frist_h']} h entscheide ich." if w["frist_h"] > 0 else "."))
    zeilen.append("Umdrehen direkt am Clip oder /clip <nr> · Stand: /auto")
    return "\n".join(zeilen)


# --- Überblick für /auto ---------------------------------------------------------------------------------------

def _richtung(status: str) -> str | None:
    if status in db.BEWERTET:
        return "freigegeben"
    return "verworfen" if status == "verworfen" else None


def war_automatisch(clip) -> bool:
    """Hat die Automatik diesen Clip irgendwann entschieden (sofort oder per Frist)?"""
    return _feld(clip, "auto_art") == "sofort" or str(_feld(clip, "auto_grund") or "").startswith("Frist")


def ueberblick(con: sqlite3.Connection, konfig: Konfig, zeit: datetime | None = None) -> dict:
    """Alles für /auto: Modus mit Herkunft, Stufen, letzte 7 Tage, Korrekturen, Stichproben, Lerndaten."""
    from . import einstellungen  # Funktions-Import: einstellungen zieht verarbeitung nach sich

    w = werte(konfig)
    herkunft = {e.schluessel: h for e, _wert, h in einstellungen.aktuell(con, konfig)}.get("auto_freigabe.modus",
                                                                                          "standard")
    seit = iso((zeit or jetzt()) - timedelta(days=7))
    clips = con.execute("SELECT * FROM clips").fetchall()
    woche = [c for c in clips if c["erstellt"] >= seit]
    an_dich = [c for c in woche if c["tg_nachricht_id"] is not None and c["auto_art"] in (None, "stichprobe", "probe")]
    automatisch = [c for c in clips if war_automatisch(c)]
    umgedreht = [c for c in automatisch if c["freigabe_quelle"] != "auto" and _richtung(c["status"]) is not None
                 and _richtung(c["status"]) != c["auto_vorschlag"]]
    stichproben = [c for c in clips if c["auto_art"] == "stichprobe" and c["freigabe_quelle"] != "auto"
                   and _richtung(c["status"]) is not None]
    return {
        "modus": w["modus"], "herkunft": herkunft, "ziel_quote": w["ziel_quote"], "verwerfen": w["verwerfen"],
        "frist_h": w["frist_h"], "mindest_n": w["mindest_n"], "stand": stufe(con, konfig),
        "woche": {
            "sofort_frei": sum(1 for c in woche if c["auto_art"] == "sofort" and c["auto_vorschlag"] == "freigegeben"),
            "sofort_weg": sum(1 for c in woche if c["auto_art"] == "sofort" and c["auto_vorschlag"] == "verworfen"),
            "frist": sum(1 for c in woche if str(c["auto_grund"] or "").startswith("Frist")),
            "an_dich": len(an_dich),
            "beantwortet": sum(1 for c in an_dich if c["freigabe_quelle"] != "auto"
                               and _richtung(c["status"]) is not None),
        },
        "umgedreht": len(umgedreht), "automatisch": len(automatisch),
        "stichproben": len(stichproben),
        "stichproben_stimmten": sum(1 for c in stichproben if _richtung(c["status"]) == c["auto_vorschlag"]),
        "lernen_du": sum(1 for c in clips if c["freigabe_quelle"] != "auto" and _richtung(c["status"]) is not None),
        "lernen_auto": sum(1 for c in clips if c["freigabe_quelle"] == "auto"),
    }
