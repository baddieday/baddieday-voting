"""Cutter-Kritik (Regisseur 3.0, 30.09., Florian: „er muss das selbst erkennen und lernen“) – seit Cutter-Maßstab 1.0
(30.09., Florian: „mach die Schnittregeln professionell“) am fertig gerenderten Video.

Jeder fertige Entwurf bekommt eine Note von 0 bis 100 – ohne dein 👍/👎:
  1. Messung (messung.py, ein ffmpeg-Durchlauf mit Bordmitteln): Lautheit, Bewegung im Spielbild, Szenenwechsel,
     Schwarz, Standbild, Blitze, Stille … Die Schnittliste liefert nur, was man im Bild nicht sieht: Kills, Beats und
     was geplant war. Geometrie und Ton-Angleichung kommen aus dem Render-Sidecar `<entwurf>.render.json`.
  2. 14 Kriterien mit stetigen Kurven und 6 K.O.-Tore (kriterien.py). Fehlt die Messung, rechnen die Kriterien mit
     Plan-Näherung (quelle "plan"), die Tore stehen dann auf None (kein Deckel).
  3. Claude als Senior-Cutter (optional, [regie.kritik].ki, zählt gegen das Abo, höchstens ki_pro_tag am Tag) –
     **blind**: er sieht Kontaktbogen (20 Bilder an redaktionellen Punkten), Wellenform und den Plan, aber nie
     Teilnoten, Note oder Messwerte (sonst lernte der Maßstab aus sich selbst, massstab.py Regel 1).
Note: M_f = gewichtetes Mittel der Teilnoten mit den gelernten Faktoren (massstab.faktoren), Tor verletzt → ≤ 40;
N = (1 − κ)·M_f + κ·K mit K = KI-Note und κ = 0,5·a_KI (so stark zählt der KI-Cutter gerade als Lehrer).
Gespeichert: score = N, regel_score = M_f (nur Anzeige und Entscheidungen, nie Training), teile, plan_teile, tore,
mess_version, ki_version, massstab_version. Daraus lernt der Bot selbst:
  - massstab: die Gewichte der Kriterien (aus KI-Cutter, Publikum, deinem 👍/👎 – nie aus score/regel_score)
  - stile.waehle: welcher Schnittstil öfter kommt (Thompson-Sampling auf den neu gerechneten Noten)
  - regie_lernen: die Gründe des KI-Cutters wirken wie deine Knöpfe (Tempo, Länge, Effekte, Momente)
  - lernbot.pruefe_auto_verwerfen: Tor verletzt oder Note unter schwelle() → du siehst den Entwurf gar nicht
Das Publikum (autonom.py) bleibt das Hauptsignal, sobald veröffentlichte Videos Zahlen haben.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import statistics
from pathlib import Path

from . import claude_aufruf, kriterien, medien, messung, schema
from .konfig import Konfig
from .zeit import iso, jetzt

log = logging.getLogger("pipeline")
GRUENDE = ("hektisch", "lang", "kurz", "langweilig", "effekte_viel", "action", "abgeschnitten", "musik")
BILDER = 20                    # Standbilder im Kontaktbogen (5×4)
KACHELN = (5, 4)
BILD_B = 240                   # Breite je Standbild
MOMENTWECHSEL_MAX = 8          # so viele Momentwechsel bekommen ein eigenes Bild
WELLE = "1200x160"             # wellenform.png: die KI hört nichts, sieht so aber Stille, Pegelsprünge und das Ende


def _kills(liste: dict) -> list[float]:
    """Kill-Zeitpunkte im fertigen Video (Sekunden), aus kill_s der Segmente (Quellzeit) – Hook zählt nicht."""
    from .effekte import auf_zeitleiste

    zeiten = []
    segmente = [s for s in liste.get("segmente") or [] if s.get("rolle") != "hook"]
    mit_anker = any(s.get("kill_s") or s.get("tod_s") is not None for s in segmente)
    for s in segmente:
        # Effekte aus (⚙️): kein kill_s – dann die Muss-Spanne (erste Aktion … letzter Kill) als Näherung;
        # Fail-Momente (05.10.): der sichtbare Tod (tod_s) zählt wie ein Kill
        punkte = s.get("kill_s") or ([s["tod_s"]] if s.get("tod_s") is not None else []) \
            or ([] if mit_anker else list(s.get("muss") or []))
        for k in punkte:
            if s["quelle_start_s"] - 1e-6 <= k <= s["quelle_ende_s"] + 1e-6:
                zeiten.append(auf_zeitleiste(s, float(k)))
    return sorted(zeiten)


def regeln(liste: dict, format_regeln: tuple[float, float] | None = None) -> dict:
    """Plan-Näherung (Wrapper um kriterien.plan_teile, alte Rückgabe): {"score": 0..100, "teile": {Kriterium: 0..1}}
    – nur die Kriterien, die der Plan hergibt, mit den Startgewichten. format_regeln bleibt aus Kompatibilität;
    die Länge prüft jetzt das Tor „technik“ (regie.pruefe_dauer) am Video."""
    teile = kriterien.plan_teile(liste)
    fmt = liste.get("format") if liste.get("format") in kriterien.START else "short"
    return {"score": kriterien.note(teile, None, fmt, None), "teile": teile}


# --- Kontaktbogen und Plan für den KI-Cutter ------------------------------------------------------------------

def bilder_zeiten(liste: dict, dauer_s: float | None = None, fps: float | None = None) -> list[float]:
    """BILDER Zeitpunkte an redaktionellen Punkten (Spec §5.1): 0 / 0,5 / 1 / 2 / 3 s, 0,2 s nach jedem Momentwechsel
    (bis 8), Höhepunkt ± 0,3 s, die letzten 0,5 s – der Rest gleichmäßig verteilt. Je Bild höchstens ein Zeitpunkt,
    sortiert, auf 2 Stellen. Beispiel 45-s-Short: [0.0, 0.5, 1.0, 2.0, 3.0, 8.2, …, 44.5, 44.95]."""
    dauer = float(dauer_s or liste.get("dauer_s") or 0.0)
    fps = float(fps or liste.get("fps") or 30)
    if dauer <= 0:
        return []
    letztes = max(0.0, dauer - 1.0 / fps)
    punkte = [0.0, 0.5, 1.0, 2.0, 3.0]
    wechsel, vorher = [], None
    for s in liste.get("segmente") or []:
        if vorher is not None and s.get("moment") != vorher and s.get("rolle") != "hook":
            wechsel.append(float(s.get("zeit_start", 0.0)) + 0.2)
        vorher = s.get("moment")
    punkte += wechsel[:MOMENTWECHSEL_MAX]
    try:
        t_p = kriterien.ereignisse(liste, None).t_p
    except (KeyError, TypeError, ValueError):
        t_p = None
    if t_p is not None:
        punkte += [t_p - 0.3, t_p + 0.3]
    punkte += [dauer - 0.5, letztes]
    belegt: dict[int, float] = {}
    for t in punkte:
        t = min(letztes, max(0.0, t))
        belegt.setdefault(round(t * fps), t)
        if len(belegt) >= BILDER:
            break
    for n in (BILDER + 1, 2 * BILDER + 1, 4 * BILDER + 1):   # Rest gleichmäßig auffüllen (je Bild nur einmal)
        for i in range(1, n):
            if len(belegt) >= BILDER:
                break
            t = min(letztes, i * dauer / n)
            belegt.setdefault(round(t * fps), t)
    return sorted(round(t, 2) for t in belegt.values())[:BILDER]


def kontaktbogen(video: Path, ordner: Path, liste: dict, zeiten: list[float] | None = None) -> Path:
    """kontaktbogen.jpg: BILDER Standbilder (je BILD_B px breit, 5×4) an den Zeiten aus bilder_zeiten – ein einziger
    ffmpeg-Aufruf (select auf je genau ein Bild, tile). Die Zeiten stehen als bilder_s in plan.json."""
    ziel = ordner / "kontaktbogen.jpg"
    fps = float(liste.get("fps") or 30)
    halb = 0.5 / fps
    auswahl = "+".join(f"between(t,{t - halb:.4f},{t + halb - 1e-4:.4f})"
                       for t in (zeiten if zeiten is not None else bilder_zeiten(liste, fps=fps)))
    spalten, zeilen = KACHELN
    medien.fuehre_aus(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(video),
                       "-vf", f"select='{auswahl}',scale={BILD_B}:-2,tile={spalten}x{zeilen}",
                       "-frames:v", "1", "-q:v", "4", str(ziel)], "Kontaktbogen", timeout=120)
    return ziel


def wellenform(video: Path, ordner: Path) -> Path | None:
    """wellenform.png des Tons (showwavespic) – None ohne Tonstrom oder wenn ffmpeg scheitert."""
    ziel = ordner / "wellenform.png"
    try:
        medien.fuehre_aus(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(video),
                           "-filter_complex", f"[0:a:0]aformat=channel_layouts=mono,showwavespic=s={WELLE}",
                           "-frames:v", "1", str(ziel)], "Wellenform", timeout=120)
    except medien.MedienFehler as fehler:
        log.info("Wellenform %s: %s", video.name, str(fehler)[:120])
        return None
    return ziel if ziel.is_file() else None


def plan(liste: dict, bilder_s: list[float] | None = None) -> dict:
    """Kurzfassung der Schnittliste für den KI-Cutter (keine Pfade). Blind (massstab.py Regel 1): keine Teilnoten,
    keine Note, keine Messwerte und kein auf_beat (das ist eine Behauptung des Plans, keine Beobachtung)."""
    p = liste.get("parameter") or {}
    return {"format": liste.get("format"), "dauer_s": liste.get("dauer_s"), "stil": p.get("stil"),
            "rahmen_zoom": p.get("rahmen_zoom", 1.0), "stimmung": liste.get("stimmung"),
            "musik": {k: (liste.get("musik") or {}).get(k) for k in ("titel", "bpm")},
            "kills_im_video_s": [round(k, 1) for k in _kills(liste)],
            "bilder_s": list(bilder_s or []),
            "segmente": [{"von_s": round(float(s["zeit_start"]), 2), "bis_s": round(float(s["zeit_ende"]), 2),
                          "rolle": s.get("rolle"), "stimmung": s.get("stimmung"),
                          "uebergang": (s.get("uebergang") or {}).get("art"), "zeitlupe": bool(s.get("lupe")),
                          "effekte": [e.get("art") for e in s.get("effekte") or []][:12]}
                         for s in liste.get("segmente") or []]}


def _vorlage(konfig: Konfig) -> str:
    pfad = konfig.projektpfad(str(konfig.wert("regie.kritik.prompt", "templates/kritik-prompt.txt")))
    return pfad.read_text(encoding="utf-8")


def ki_version(konfig: Konfig) -> str:
    """sha256(Prompt + Schema)[:12] – KI-Noten werden nur innerhalb derselben Version verglichen (massstab)."""
    text = _vorlage(konfig) + json.dumps(schema.lade("kritik"), sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _ki_heute(con: sqlite3.Connection) -> int:
    return con.execute("SELECT COUNT(*) FROM kritiken WHERE ki_score IS NOT NULL AND substr(erstellt, 1, 10) = ?",
                       (iso(jetzt())[:10],)).fetchone()[0]


def ki_urteil(con: sqlite3.Connection, konfig: Konfig, liste: dict, video: Path, ordner: Path) -> tuple[dict | None, str | None]:
    """(Urteil, Hinweis) – Urteil None, wenn aus, über dem Tageslimit, ohne Video oder Claude scheiterte. Aus ist sie
    auch bei einem Freund ohne eigenen Claude-Zugang (M1) – dann kein Kontaktbogen unter der gemeinsamen Sperre."""
    if not konfig.wert("regie.kritik.ki", True) or not claude_aufruf.ki_moeglich(konfig):
        return None, None
    if _ki_heute(con) >= int(konfig.wert("regie.kritik.ki_pro_tag", 20)):
        return None, "KI-Cutter: Tageslimit erreicht – nur Messung"
    if not video.is_file():
        return None, "KI-Cutter: Video fehlt – nur Plan"
    ordner.mkdir(parents=True, exist_ok=True)
    try:
        zeiten = bilder_zeiten(liste, medien.probe(video).dauer_s or None)   # die echte Länge des Videos
        kontaktbogen(video, ordner, liste, zeiten)
    except medien.MedienFehler as fehler:
        return None, f"KI-Cutter: kein Kontaktbogen ({str(fehler)[:60]})"
    wellenform(video, ordner)
    (ordner / "plan.json").write_text(json.dumps(plan(liste, zeiten), ensure_ascii=False, indent=1),
                                      encoding="utf-8")
    antwort = claude_aufruf.frage_json(konfig, _vorlage(konfig), ordner, schema_name="kritik",
                                       timeout_s=float(konfig.wert("regie.kritik.timeout_s", 180)))
    try:
        claude_aufruf.protokolliere(con, "kritik", antwort)
    except Exception:  # Zählen ist Nebensache
        log.exception("Kritik protokollieren")
    if antwort.daten is None:
        return None, f"KI-Cutter: {antwort.hinweis}"
    return antwort.daten, None


# --- Messung und Geometrie ------------------------------------------------------------------------------------

def _mit_beats(con: sqlite3.Connection, liste: dict) -> dict:
    """liste["musik"]["beats"] = tracks.beats (Musikzeit) – kriterien zieht musik.start_s selbst ab."""
    m = liste.get("musik")
    if not m or m.get("beats") or not m.get("track_id"):
        return liste
    z = con.execute("SELECT beats FROM tracks WHERE id = ?", (m["track_id"],)).fetchone()
    if z is not None and z["beats"]:
        try:
            liste = {**liste, "musik": {**m, "beats": json.loads(z["beats"])}}
        except json.JSONDecodeError:
            pass
    return liste


def geometrie(konfig: Konfig, liste: dict, video: Path) -> dict:
    """Render-Sidecar `<video>.render.json` (b, h, band, spiel_anteile, normiert …) plus Ziel-Lautheit, Plattformen
    und Zeichenbreite. Fehlt es (alter Entwurf): Geometrie aus entwurf.rahmen_grenze + medien.probe neu rechnen,
    normiert = False (Tor „ton“ dann None). Ohne lesbares Video: nur die Zusatzwerte."""
    from . import entwurf

    g: dict = {}
    try:
        g = json.loads(video.with_suffix(".render.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        try:
            info = medien.probe(video)
            quellen = []
            for s in liste.get("segmente") or []:
                try:
                    q = medien.probe(Path(s["datei"]))
                    quellen.append((q.breite, q.hoehe))
                except (medien.MedienFehler, OSError, KeyError, ValueError):
                    quellen.append((0, 0))
            b, h = info.breite, info.hoehe
            rahmen = entwurf.rahmen_grenze(liste, b, h, quellen)
            g = {**entwurf.geometrie(liste, b, h, quellen, rahmen), "normiert": False}
        except (medien.MedienFehler, OSError, ValueError, KeyError):
            g = {"normiert": False}
    g.setdefault("ziel_lufs", entwurf.ton_einstellungen(konfig)["lufs"])
    g.setdefault("plattformen", list(konfig.wert("regie.massstab.plattformen", kriterien.PLATTFORMEN)))
    g.setdefault("zeichenbreite", entwurf._zeichenbreite(konfig))
    return g


def _messung(video: Path, ordner: Path, g: dict, neu: bool) -> messung.Messung | None:
    """messung.json im Ordner kritik-<id>/, sonst neu messen (und ablegen). Älter als das Video → neu messen."""
    if not video.is_file():
        return None
    datei = ordner / "messung.json"
    if not neu and datei.is_file() and datei.stat().st_mtime >= video.stat().st_mtime:
        if (m := messung.lade(datei)) is not None and m.version >= 1:
            return m
    band = tuple(g["band"]) if isinstance(g.get("band"), list) and len(g["band"]) == 2 else None
    stems = Path(g["stems"]) if g.get("stems") else ordner / "stems.mka"
    try:
        dauer = medien.probe(video).dauer_s
    except (medien.MedienFehler, OSError, ValueError):
        dauer = 60.0
    m = messung.messe(video, ordner, band=band, stems=stems if stems.is_file() else None,
                      timeout_s=max(180.0, 4 * dauer))
    if m.version >= 1:
        messung.speichere(m, datei)
    elif m.fehler:
        log.warning("Messung %s: %s", video.name, "; ".join(m.fehler)[:300])
    return m


def _naht(video: Path, g: dict, fps: float) -> float | None:
    """Loop-Naht (Short): SSIM des Spielbild-Bands, letztes gegen erstes Bild."""
    band = tuple(g["band"]) if isinstance(g.get("band"), list) and len(g["band"]) == 2 else None
    return messung.ssim(video, 0.0, -1.2 / max(1.0, fps), band)


# --- Note ----------------------------------------------------------------------------------------------------

def gesamt(m_f: float, ki: float | None, kappa: float, tor: bool) -> float:
    """N = (1 − κ)·M_f + κ·K (ohne K: M_f); Tor verletzt → höchstens kriterien.DECKEL, auch mit KI."""
    n = m_f if ki is None else (1 - kappa) * m_f + kappa * ki
    return round(min(n, kriterien.DECKEL) if tor else n, 1)


def note_jetzt(teile: dict, tore: dict | None, fmt: str, ki: float | None, *, faktoren: dict, kappa: float,
               start: dict | None = None) -> tuple[float, float]:
    """(M_f, N) aus gespeicherten Teilnoten mit den AKTUELLEN Faktoren (stile.statistik, Anzeige)."""
    m_f = kriterien.note(teile, tore, fmt, faktoren, start=start)
    return m_f, gesamt(m_f, ki, kappa, kriterien.tor_verletzt(tore) is not None)


def _befund_text(k: str, t, befund: str) -> str:
    name = kriterien.KRITERIEN[k]["name"]
    wert = kriterien.teilwert(t)
    text = f"{name} {wert:.2f}".replace(".", ",") if wert is not None else name
    return text + (f" ({befund})" if befund else "")


def bewerte(con: sqlite3.Connection, konfig: Konfig, entwurf_id: int, *, neu_messen: bool = False, ki: bool = True,
            lernen: bool = True) -> dict:
    """Benotet einen gerenderten Entwurf und speichert das Ergebnis in kritiken (überschreibt ein altes):
    Sidecar → Messung (messung.json oder neu) → Teilnoten/Tore → KI-Cutter blind → Note mit den aktuellen Faktoren
    → speichern → massstab.aktualisiere (lernen=False: der Aufrufer lernt am Ende einmal, z. B. --nachmessen).
    ki=False (Nachmessen): kein neuer Claude-Aufruf – ein vorhandenes KI-Urteil samt ki_version bleibt stehen.
    Rückgabe wie bisher (entwurf, score, regel_score, ki_score, gruende, staerken, schwaechen, hinweis) plus teile,
    tore, mess_version, tor (Text des verletzten Tors oder None) und verluste."""
    from . import massstab

    zeile = con.execute("SELECT * FROM entwuerfe WHERE id = ?", (entwurf_id,)).fetchone()
    if zeile is None:
        raise ValueError(f"Entwurf #{entwurf_id} gibt es nicht")
    liste = _mit_beats(con, json.loads(Path(zeile["schnittliste"]).read_text(encoding="utf-8")))
    fmt = liste.get("format") if liste.get("format") in kriterien.START else "short"
    video = Path(zeile["datei"] or "")
    ordner = Path(zeile["schnittliste"]).parent / f"kritik-{entwurf_id}"
    g = geometrie(konfig, liste, video) if video.is_file() else {"normiert": False}
    m = _messung(video, ordner, g, neu_messen)
    mess_version = m.version if m is not None else 0
    if m is not None and mess_version >= 1 and fmt == "short" and "naht" not in g:
        g["naht"] = _naht(video, g, float(m.fps or liste.get("fps") or 30))
    teile = kriterien.teilnoten(liste, m, g, fmt)
    tore = kriterien.tore(liste, m, g)
    plan_teile = kriterien.plan_teile(liste)

    alt = con.execute("SELECT * FROM kritiken WHERE entwurf_id = ?", (entwurf_id,)).fetchone()
    alt_details = json.loads(alt["details"] or "{}") if alt is not None else {}
    # Blinder Lehrer (Spec §5.1 Regel 1): eigener Ordner NUR mit Kontaktbogen, Wellenform und plan.json – in
    # kritik-<id>/ liegen messung.json und stems.mka, die claude -p mit Leserecht sonst lesen könnte (Review 30.09.)
    urteil, hinweis = ki_urteil(con, konfig, liste, video, ordner / "ki") if ki else (None, None)
    erstellt = iso(jetzt())
    if urteil is not None:
        k_score = float(urteil["score"])
        gruende = list(dict.fromkeys(g_ for g_ in urteil.get("gruende", []) if g_ in GRUENDE))
        staerken, schwaechen, version = urteil.get("staerken", []), urteil.get("schwaechen", []), ki_version(konfig)
    elif alt is not None and alt["ki_score"] is not None:   # altes KI-Urteil behalten (Nachmessen, Tageslimit)
        k_score, gruende = float(alt["ki_score"]), json.loads(alt["gruende"] or "[]")
        staerken, schwaechen = alt_details.get("staerken", []), alt_details.get("schwaechen", [])
        version = alt["ki_version"] if "ki_version" in alt.keys() else None
        erstellt = alt["erstellt"]      # _ki_heute zählt nach erstellt – ein behaltenes Urteil zählt nicht neu
    else:
        k_score, gruende, staerken, schwaechen, version = None, [], [], [], None
    daumen = (1 if k_score >= float(konfig.wert("regie.kritik.gut_ab", 60)) else -1) if k_score is not None else None

    start = massstab.start_gewichte(konfig)
    faktoren = massstab.faktoren(con)
    m_f, score = note_jetzt(teile, tore, fmt, k_score, faktoren=faktoren, kappa=massstab.ki_gewicht(con), start=start)
    tor = kriterien.tor_verletzt(tore)
    verluste = [[k, p, _befund_text(k, teile.get(k), b)] for k, p, b in
                kriterien.verluste(teile, fmt, faktoren, start=start)]
    anteil = kriterien.gemessen_anteil(teile, fmt, start=start)
    details = {"staerken": staerken, "schwaechen": schwaechen, "hinweis": hinweis,
               "befunde": {k: t.befund for k, t in teile.items() if t is not None and t.befund},
               "rohwerte": {k: t.roh for k, t in teile.items() if t is not None and t.roh},
               "verluste": verluste, "tor": tor["text"] if tor else None, "gemessen": round(anteil, 3),
               "normiert": bool(g.get("normiert")), "mess_fehler": (m.fehler[:5] if m is not None else [])}
    zeile_mv = con.execute("SELECT COALESCE(MAX(version), 0) FROM massstab").fetchone()[0]
    con.execute("""INSERT INTO kritiken (entwurf_id, score, regel_score, ki_score, daumen, gruende, details, erstellt,
                                         teile, plan_teile, tore, mess_version, ki_version, massstab_version)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (entwurf_id) DO UPDATE SET score = excluded.score, regel_score = excluded.regel_score,
                       ki_score = excluded.ki_score, daumen = excluded.daumen, gruende = excluded.gruende,
                       details = excluded.details, erstellt = excluded.erstellt, teile = excluded.teile,
                       plan_teile = excluded.plan_teile, tore = excluded.tore, mess_version = excluded.mess_version,
                       ki_version = excluded.ki_version, massstab_version = excluded.massstab_version""",
                (entwurf_id, score, m_f, k_score, daumen, json.dumps(gruende),
                 json.dumps(details, ensure_ascii=False), erstellt, json.dumps(kriterien.als_json(teile)),
                 json.dumps(plan_teile), json.dumps(tore, ensure_ascii=False), mess_version, version, zeile_mv or None))
    if lernen:
        massstab.nachziehen(con, konfig, f"Kritik #{entwurf_id}")
    return {"entwurf": entwurf_id, "score": score, "regel_score": m_f, "ki_score": k_score, "gruende": gruende,
            "teile": kriterien.als_json(teile), "tore": tore, "mess_version": mess_version,
            "tor": tor["text"] if tor else None, **details}


def kritik_zeile(con: sqlite3.Connection, entwurf_id: int) -> str | None:
    """Für die Bildunterschrift: „🧐 Cutter 64/100 · schwächstes: Leerlauf 0,42 (0:21–0:26 ohne Aktion) · Hook 0,55
    (erste Aktion bei 2,9 s)“, mit Tor „🧐 Cutter 40/100 · Tor: Blitze 7/s (Grenze 3)“ (None ohne Kritik)."""
    z = con.execute("SELECT * FROM kritiken WHERE entwurf_id = ?", (entwurf_id,)).fetchone()
    if z is None:
        return None
    d = json.loads(z["details"] or "{}")
    mv = z["mess_version"] if "mess_version" in z.keys() else None
    zusatz = [] if z["ki_score"] is None else [f"KI {z['ki_score']:.0f}"]
    if mv is None:                                      # alte Kritik vor dem Maßstab
        zusatz.append("Regeln")
    elif mv < 1:
        zusatz.append("nur Plan")
    elif d.get("gemessen", 1.0) < kriterien.GEMESSEN_MIN:
        zusatz.append("teilweise gemessen")
    teile = [f"🧐 Cutter {z['score']:.0f}/100" + (f" ({', '.join(zusatz)})" if zusatz else "")]
    if d.get("tor"):
        teile.append(f"Tor: {d['tor']}")
    for i, (_k, _p, text) in enumerate(d.get("verluste") or []):
        teile.append(("schwächstes: " if i == 0 else "") + text)
    if mv is None:
        if d.get("staerken"):
            teile.append("➕ " + d["staerken"][0])
        if d.get("schwaechen"):
            teile.append("➖ " + d["schwaechen"][0])
    return " · ".join(teile)


def schwelle(con: sqlite3.Connection, konfig: Konfig, fmt: str) -> float:
    """Aussortier-Schwelle (Spec §4): Q20 der Note N über die letzten 40 gemessenen, lautheits-angeglichenen
    Entwürfe des Formats, begrenzt auf [schwelle_min (40), [regie.kritik].schwelle (50)]. 0 = keine Schwelle:
    [regie.kritik].schwelle ≤ 0 oder weniger als schwelle_ab_n (20) gemessene Entwürfe – dann wirken nur die Tore."""
    oben = float(konfig.wert("regie.kritik.schwelle", 50) or 0)
    if oben <= 0:
        return 0.0
    quantil = float(konfig.wert("regie.massstab.schwelle_quantil", 0.2))
    unten = min(oben, float(konfig.wert("regie.massstab.schwelle_min", 40)))
    ab_n = int(konfig.wert("regie.massstab.schwelle_ab_n", 20))
    noten = []
    for z in con.execute("""SELECT k.score, k.details FROM kritiken k JOIN entwuerfe e ON e.id = k.entwurf_id
                             WHERE e.format = ? AND k.mess_version >= 1 AND e.variante IS NULL
                             ORDER BY k.entwurf_id DESC""", (fmt,)):
        try:
            if json.loads(z["details"] or "{}").get("normiert"):
                noten.append(float(z["score"]))
        except (ValueError, AttributeError):
            continue
        if len(noten) >= 40:
            break
    if len(noten) < max(2, ab_n):
        return 0.0
    noten.sort()
    pos = quantil * (len(noten) - 1)                       # lineare Interpolation zwischen den Rängen
    q = noten[int(pos)] + (noten[min(len(noten) - 1, int(pos) + 1)] - noten[int(pos)]) * (pos - int(pos))
    return round(max(unten, min(oben, q)), 1)


def abnahme(con: sqlite3.Connection) -> dict:
    """Abnahme S1 (Spec §4) über alle gemessenen Entwürfe: M_f-Mittel je Stil, σ(M_f), Spannweite der Stil-Mittel
    und die Kriterien, die nicht trennen (σ(t_k) < 0,1). Ziel: Spannweite ≥ 15, σ ≥ 8 (erst ab 30 Entwürfen)."""
    je_stil: dict[str, list[float]] = {}
    alle: list[float] = []
    werte: dict[str, list[float]] = {}
    for z in con.execute("""SELECT k.regel_score, k.teile, e.parameter FROM kritiken k
                              JOIN entwuerfe e ON e.id = k.entwurf_id WHERE k.mess_version >= 1"""):
        alle.append(float(z["regel_score"]))
        try:
            stil = (json.loads(z["parameter"] or "{}") or {}).get("stil") or "ohne"
            teile = json.loads(z["teile"] or "{}")
        except ValueError:
            continue
        je_stil.setdefault(stil, []).append(float(z["regel_score"]))
        for k in kriterien.KRITERIEN:
            if (t := kriterien.teilwert((teile or {}).get(k))) is not None:
                werte.setdefault(k, []).append(t)
    mittel = {s: round(statistics.mean(v), 1) for s, v in je_stil.items()}
    sigma = round(statistics.pstdev(alle), 1) if len(alle) > 1 else None
    spannweite = round(max(mittel.values()) - min(mittel.values()), 1) if len(mittel) > 1 else None
    trennt_nicht = sorted(k for k, v in werte.items() if len(v) > 1 and statistics.pstdev(v) < 0.1)
    ok = None if len(alle) < 30 else bool((spannweite or 0) >= 15 and (sigma or 0) >= 8)
    return {"n": len(alle), "stile": mittel, "sigma": sigma, "spannweite": spannweite, "ok": ok,
            "trennt_nicht": trennt_nicht}
