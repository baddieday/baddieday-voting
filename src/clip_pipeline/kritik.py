"""Cutter-Kritik (Regisseur 3.0, 30.09., Florian: „er muss das selbst erkennen und lernen“).

Jeder fertige Entwurf bekommt eine Note von 0 bis 100 – ohne dein 👍/👎:
  1. Handwerksregeln aus der Schnittliste (immer, kostet nichts): Einstieg (Action in den ersten 2 s), Leerlauf
     (Zeit ohne Kill in der Nähe), Rhythmus (Schnitte auf dem Beat, abwechslungsreiche Längen), Finale (endet auf dem
     stärksten Moment), Bildfläche (Rahmen-Zoom im Short), Effekt-Dichte, Länge im Formatband.
  2. Claude als Senior-Cutter (optional, [regie.kritik].ki, zählt gegen das Abo, höchstens ki_pro_tag am Tag):
     Kontaktbogen aus dem gerenderten Video + plan.json → Note, Stärken, Schwächen und Gründe aus derselben Liste wie
     deine Knöpfe (schemas/kritik.schema.json, nur Leserechte).
Note = Mittel aus beidem (ohne KI nur die Regeln). Daraus lernt der Bot selbst:
  - stile.waehle: welcher Schnittstil öfter kommt (Thompson-Sampling auf den Noten)
  - regie_lernen: die Gründe des KI-Cutters wirken wie deine Knöpfe (Tempo, Länge, Effekte, Momente)
  - lernbot.pruefe_auto_verwerfen: Entwürfe unter [regie.kritik].schwelle siehst du gar nicht, der Bot baut neu
Das Publikum (autonom.py) bleibt das Hauptsignal, sobald veröffentlichte Videos Zahlen haben.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import statistics
from pathlib import Path

from . import claude_aufruf, medien
from .konfig import Konfig
from .zeit import iso, jetzt

log = logging.getLogger("pipeline")
GRUENDE = ("hektisch", "lang", "kurz", "langweilig", "effekte_viel", "action", "abgeschnitten", "musik")
GEWICHTE = {"einstieg": 0.2, "action": 0.25, "rhythmus": 0.15, "finale": 0.1, "bild": 0.1, "effekte": 0.1, "laenge": 0.1}
BILDER = 8   # Standbilder im Kontaktbogen


def _kills(liste: dict) -> list[float]:
    """Kill-Zeitpunkte im fertigen Video (Sekunden), aus kill_s der Segmente (Quellzeit) – Hook zählt nicht."""
    from .effekte import auf_zeitleiste

    zeiten = []
    segmente = [s for s in liste.get("segmente") or [] if s.get("rolle") != "hook"]
    mit_anker = any(s.get("kill_s") for s in segmente)
    for s in segmente:
        # Effekte aus (⚙️): kein kill_s – dann die Muss-Spanne (erste Aktion … letzter Kill) als Näherung
        punkte = s.get("kill_s") or ([] if mit_anker else list(s.get("muss") or []))
        for k in punkte:
            if s["quelle_start_s"] - 1e-6 <= k <= s["quelle_ende_s"] + 1e-6:
                zeiten.append(auf_zeitleiste(s, float(k)))
    return sorted(zeiten)


def regeln(liste: dict, format_regeln: tuple[float, float] | None = None) -> dict:
    """Handwerksnote 0..100 mit Teilnoten 0..1. format_regeln: (min_s, max_s) des Formats."""
    segmente = liste.get("segmente") or []
    dauer = float(liste.get("dauer_s") or 0.0) or 1.0
    kills = _kills(liste)
    teile = {}
    # Einstieg: die erste Action (Kill oder Hook mit Kill) – je früher, desto besser
    hook_kill = any(s.get("rolle") == "hook" and s.get("kill_s") for s in segmente)
    erste = 0.0 if hook_kill else (kills[0] if kills else dauer)
    teile["einstieg"] = 1.0 if erste <= 2.0 else 0.6 if erste <= 4.0 else 0.2
    # Action: Anteil der Zeit, die höchstens 2,5 s von einem Kill entfernt ist (Rest = Leerlauf)
    schritte = [i * 0.25 for i in range(int(dauer / 0.25) + 1)]
    nah = sum(1 for t in schritte if any(abs(t - k) <= 2.5 for k in kills)) / max(1, len(schritte))
    teile["action"] = round(min(1.0, nah / 0.6), 3)
    # Rhythmus: Schnitte auf dem Beat und nicht alle Einstellungen gleich lang (monoton wirkt billig)
    laengen = [float(s["zeit_ende"]) - float(s["zeit_start"]) for s in segmente if "zeit_ende" in s]
    beat = sum(1 for s in segmente if s.get("auf_beat")) / max(1, len(segmente))
    streuung = statistics.pstdev(laengen) / statistics.mean(laengen) if len(laengen) > 1 and statistics.mean(laengen) else 0
    teile["rhythmus"] = round(0.6 * beat + 0.4 * (1.0 if 0.2 <= streuung <= 0.9 else 0.5), 3)
    # Finale: endet auf dem stärksten Moment
    bogen = liste.get("bogen") or []
    teile["finale"] = 1.0 if bogen and bogen[-1] >= max(bogen) - 1e-6 else 0.5
    # Bildfläche: im Short ist ein größeres Spielbild lesbarer (1,0 = schmaler Streifen)
    zoom = float((liste.get("parameter") or {}).get("rahmen_zoom", 1.0) or 1.0)
    teile["bild"] = 1.0 if liste.get("format") != "short" else round(min(1.0, 0.4 + (zoom - 1.0) / 0.25 * 0.6), 3)
    # Effekt-Dichte je 10 s: kaum etwas wirkt leer, ein Gewitter wirkt billig
    ereignisse = sum(len(s.get("effekte") or []) for s in segmente) + sum(1 for s in segmente if s.get("lupe"))
    dichte = ereignisse / dauer * 10
    teile["effekte"] = 1.0 if 3 <= dichte <= 18 else 0.6 if 1.5 <= dichte <= 26 else 0.3
    if (liste.get("effekte") or {}).get("an") is False:
        teile["effekte"] = 1.0   # bewusst ausgeschaltet (⚙️) – keine Strafe
    # Länge im Formatband
    unten, oben = format_regeln or (0.0, float("inf"))
    teile["laenge"] = 1.0 if unten - 0.5 <= dauer <= oben + 0.5 else 0.0
    note = round(100 * sum(GEWICHTE[k] * v for k, v in teile.items()), 1)
    return {"score": note, "teile": teile}


def kontaktbogen(video: Path, ordner: Path, dauer_s: float) -> Path:
    """BILDER Standbilder gleichmäßig über das Video, nebeneinander (je 180 px breit)."""
    ziel = ordner / "kontaktbogen.jpg"
    rate = BILDER / max(1.0, dauer_s)
    medien.fuehre_aus(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(video),
                       "-vf", f"fps={rate:.4f},scale=180:-2,tile={BILDER}x1", "-frames:v", "1", "-q:v", "4", str(ziel)],
                      "Kontaktbogen", timeout=120)
    return ziel


def plan(liste: dict) -> dict:
    """Kurzfassung der Schnittliste für den KI-Cutter (keine Pfade)."""
    p = liste.get("parameter") or {}
    return {"format": liste.get("format"), "dauer_s": liste.get("dauer_s"), "stil": p.get("stil"),
            "rahmen_zoom": p.get("rahmen_zoom", 1.0), "stimmung": liste.get("stimmung"),
            "musik": {k: (liste.get("musik") or {}).get(k) for k in ("titel", "bpm")},
            "kills_im_video_s": [round(k, 1) for k in _kills(liste)],
            "segmente": [{"von_s": round(float(s["zeit_start"]), 2), "bis_s": round(float(s["zeit_ende"]), 2),
                          "rolle": s.get("rolle"), "stimmung": s.get("stimmung"), "auf_beat": s.get("auf_beat"),
                          "uebergang": (s.get("uebergang") or {}).get("art"), "zeitlupe": bool(s.get("lupe")),
                          "effekte": [e.get("art") for e in s.get("effekte") or []][:12]}
                         for s in liste.get("segmente") or []]}


def _ki_heute(con: sqlite3.Connection) -> int:
    return con.execute("SELECT COUNT(*) FROM kritiken WHERE ki_score IS NOT NULL AND substr(erstellt, 1, 10) = ?",
                       (iso(jetzt())[:10],)).fetchone()[0]


def ki_urteil(con: sqlite3.Connection, konfig: Konfig, liste: dict, video: Path, ordner: Path) -> tuple[dict | None, str | None]:
    """(Urteil, Hinweis) – Urteil None, wenn aus, über dem Tageslimit, ohne Video oder Claude scheiterte."""
    if not konfig.wert("regie.kritik.ki", True):
        return None, None
    if _ki_heute(con) >= int(konfig.wert("regie.kritik.ki_pro_tag", 20)):
        return None, "KI-Cutter: Tageslimit erreicht – nur Regeln"
    if not video.is_file():
        return None, "KI-Cutter: Video fehlt – nur Regeln"
    ordner.mkdir(parents=True, exist_ok=True)
    try:
        kontaktbogen(video, ordner, float(liste.get("dauer_s") or 0))
    except medien.MedienFehler as fehler:
        return None, f"KI-Cutter: kein Kontaktbogen ({str(fehler)[:60]})"
    (ordner / "plan.json").write_text(json.dumps(plan(liste), ensure_ascii=False, indent=1), encoding="utf-8")
    vorlage = konfig.projektpfad(str(konfig.wert("regie.kritik.prompt", "templates/kritik-prompt.txt")))
    antwort = claude_aufruf.frage_json(konfig, vorlage.read_text(encoding="utf-8"), ordner, schema_name="kritik",
                                       timeout_s=float(konfig.wert("regie.kritik.timeout_s", 180)))
    try:
        claude_aufruf.protokolliere(con, "kritik", antwort)
    except Exception:  # Zählen ist Nebensache
        log.exception("Kritik protokollieren")
    if antwort.daten is None:
        return None, f"KI-Cutter: {antwort.hinweis}"
    return antwort.daten, None


def bewerte(con: sqlite3.Connection, konfig: Konfig, entwurf_id: int) -> dict:
    """Benotet einen gerenderten Entwurf und speichert das Ergebnis in kritiken (überschreibt ein altes)."""
    from .regie import format_regeln

    zeile = con.execute("SELECT * FROM entwuerfe WHERE id = ?", (entwurf_id,)).fetchone()
    if zeile is None:
        raise ValueError(f"Entwurf #{entwurf_id} gibt es nicht")
    liste = json.loads(Path(zeile["schnittliste"]).read_text(encoding="utf-8"))
    fmt, _ = format_regeln(konfig, liste["format"])
    r = regeln(liste, (fmt["min_s"], fmt["max_s"]))
    video = Path(zeile["datei"] or "")
    urteil, hinweis = ki_urteil(con, konfig, liste, video, Path(zeile["schnittliste"]).parent / f"kritik-{entwurf_id}")
    ki = float(urteil["score"]) if urteil else None
    score = round((r["score"] + ki) / 2, 1) if ki is not None else r["score"]
    gruende = list(dict.fromkeys(g for g in (urteil or {}).get("gruende", []) if g in GRUENDE))
    daumen = (1 if ki >= float(konfig.wert("regie.kritik.gut_ab", 60)) else -1) if ki is not None else None
    details = {"regeln": r["teile"], "staerken": (urteil or {}).get("staerken", []),
               "schwaechen": (urteil or {}).get("schwaechen", []), "hinweis": hinweis}
    con.execute("""INSERT INTO kritiken (entwurf_id, score, regel_score, ki_score, daumen, gruende, details, erstellt)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (entwurf_id) DO UPDATE SET score = excluded.score, regel_score = excluded.regel_score,
                       ki_score = excluded.ki_score, daumen = excluded.daumen, gruende = excluded.gruende,
                       details = excluded.details, erstellt = excluded.erstellt""",
                (entwurf_id, score, r["score"], ki, daumen, json.dumps(gruende), json.dumps(details, ensure_ascii=False),
                 iso(jetzt())))
    return {"entwurf": entwurf_id, "score": score, "regel_score": r["score"], "ki_score": ki, "gruende": gruende,
            **details}


def kritik_zeile(con: sqlite3.Connection, entwurf_id: int) -> str | None:
    """Für die Bildunterschrift: „🧐 Cutter-Score 72/100 · ➕ … · ➖ …“ (None ohne Kritik)."""
    z = con.execute("SELECT score, ki_score, details FROM kritiken WHERE entwurf_id = ?", (entwurf_id,)).fetchone()
    if z is None:
        return None
    d = json.loads(z["details"] or "{}")
    teile = [f"🧐 Cutter-Score {z['score']:.0f}/100" + ("" if z["ki_score"] is not None else " (Regeln)")]
    if d.get("staerken"):
        teile.append("➕ " + d["staerken"][0])
    if d.get("schwaechen"):
        teile.append("➖ " + d["schwaechen"][0])
    return " · ".join(teile)
