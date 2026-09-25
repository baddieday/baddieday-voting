"""Publikum: Posts, Publikumszahlen und der Publikums-Score (Spec §5, §6, §7.1, §10.4).

Worum es geht: Ein Short ist erst dann „gut“, wenn er auf TikTok ankommt. Dieses Modul merkt sich jeden
veröffentlichten Short als **Post** (Tabelle `posts`), sammelt seine Zahlen als Zeitreihe von **Messungen**
(`publikum_messungen` – die Zahlen wachsen in den ersten Tagen noch) und macht daraus nach einer Woche genau
**eine** Zahl: den Publikums-Score.

Die Lernidee in Alltagssprache: Ein einzelner Post mit 5 000 Views sagt wenig – vielleicht hatte TikTok an dem
Tag Lust, ihn zu verteilen, vielleicht sind gerade viele neue Follower dazugekommen. Deshalb vergleicht der Score
einen Post nur mit **deinen eigenen letzten Posts** derselben Plattform: Lag er bei der Wiedergabe, beim
Engagement und bei der Reichweite über oder unter dem, was bei dir gerade üblich ist? „Üblich“ ist der Median,
die Streuung der MAD (der Median der Abstände zum Median) – beide lassen sich von einem Ausreißer-Post nicht
umwerfen. Die Wiedergabe zählt am meisten, weil sie direkt misst, ob Auswahl und Schnitt tragen.

Ablauf:
  1. Ein Post entsteht beim Häkchen bzw. `/link` im Clip-Bot (art = clip) oder im Lern-Bot (art = entwurf):
     immer erst `post_anlegen` (ohne Link), dann – bei /link – `link_nachtragen`. `post_zu` findet ihn wieder.
  2. Zahlen kommen per Screenshot (screenshot.py) oder Hand-Eingabe (`lies_hand_eingabe`); bevor sie
     gespeichert werden, prüft `pruefe_plausibel` sie gegen die letzte Messung (`speichere_messung`).
  3. `pipeline publikum bewerten` (täglicher Timer) ruft `bewerte_alle`: für jeden fälligen Post die passende
     Messung wählen, Komponenten r/e/v ausrechnen, gegen die Vergleichsbasis standardisieren, Score speichern –
     **einmal** je Post, danach nie wieder überschrieben (hält Paare und Rezept-Stände stabil).

Alles hier ist reine Rechnung und Datenbank – kein Netz, kein Telegram, kein Rendern, kein Wecken von pve-big.
Wer eine Zeit braucht, bekommt sie als Parameter (`zeit=None` → jetzt), damit Tests feste Zeiten setzen können.
"""

from __future__ import annotations

import json
import logging
import math
import re
import sqlite3
import statistics
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from . import db, shorts
from .konfig import Konfig, KonfigFehler
from .zeit import aus_iso, iso, jetzt  # im Modul importiert, damit Tests `publikum.jetzt` ersetzen können

log = logging.getLogger("pipeline")

# Plattformen, für die es Posts geben darf. clip-battle.de ist keine: dort wird eingereicht, nicht geschaut.
PLATTFORMEN = ("tiktok", "youtube")
# Was ein Post sein kann (Spec §5, CHECK in publikum.sql): ein Einzelclip-Short oder ein Regisseur-Entwurf
ARTEN = ("clip", "entwurf")
# So heißen die Arten in Bot-Texten („Entwurf 41“, „Clip 88“) – eine Stelle für beide Lern-Bot-Module
ART_NAMEN = {"clip": "Clip", "entwurf": "Entwurf"}
# Woher eine Messung kommt (CHECK in publikum.sql)
QUELLEN = ("api", "screenshot", "hand")

# Zähler einer Messung, die mit der Zeit nur wachsen können (Plausibilität, Spec §7.1)
ZAEHLER = ("views", "likes", "kommentare", "shares", "saves")
# Alle Felder einer Messung in der Reihenfolge des Screenshot-Schemas (schemas/publikum.schema.json)
FELDER = (*ZAEHLER, "wiedergabe_s", "voll_prozent")
# So heißen die Zähler in Meldungen an dich (so stehen sie auch in der TikTok-Statistik)
ZAEHLER_NAMEN = {"views": "Views", "likes": "Likes", "kommentare": "Kommentare", "shares": "Shares",
                 "saves": "Saves"}
# Reihenfolge der Hand-Eingabe „views likes wiedergabe voll%“ (Spec §7.1) – die übrigen Felder bleiben None
HAND_FELDER = ("views", "likes", "wiedergabe_s", "voll_prozent")
# Die Hand-Eingabe in Worten, mit Einheiten – genau so in der Bitte des Lern-Bots und in jeder Fehlermeldung. Die
# Einheit steht dabei, weil die TikTok-App die Wiedergabe als „0:07“ zeigt, gebraucht werden aber Sekunden.
HAND_FORM = "Views Likes Ø-Wiedergabe-in-Sekunden Ganz-angesehen-in-%"
# Diese Zeichen bedeuten in der Hand-Eingabe „weiß ich nicht“ (Halbgeviertstrich, Bindestrich, Geviertstrich –
# das Handy macht aus „-“ gern automatisch „–“)
UNBEKANNT = ("–", "-", "—")

# --- Konstanten der Score-Formel (Spec §6). Alles, was du ändern können sollst, steht in [publikum]. ---
MAD_FAKTOR = 1.4826      # macht den MAD bei normalverteilten Daten zur Standardabweichung (Spec §6.3)
MAD_MINIMUM = 0.05       # kleiner als das wird die Streuung nicht – sonst explodiert z bei fast gleichen Posts
Z_GRENZE = 2.5           # z wird auf ±2,5 begrenzt: ein Viral-Ausreißer soll nicht alles andere überstimmen
# Unter 5 Posts in der Basis: alle z = 0, Score 0 („Basis zu klein“, kein Lernen aus dem Nichts). Genug Posts,
# aber unter 5 r-Werten: r fällt weg, e/v werden auf 0,6/0,4 hochgerechnet („Basis ohne Wiedergabe“, Annahme A11).
MINDEST_BASIS = 5
# Vermerk in score_teile, wenn der Score wegen zu kleiner Basis 0 ist – lernbot_publikum erkennt ihn an genau
# dieser Konstante (erklärt die 0 in der Meldung und in /publikum), kein zweites Literal
VERMERK_BASIS_ZU_KLEIN = "Basis zu klein"
R_MAX = 1.2              # Wiedergabe/Dauer über 120 % (mehrfach angesehen) zählt nicht noch höher
WIEDERGABE_MAX_FAKTOR = 1.5  # Plausibilität: Ø Wiedergabe über 150 % der Dauer ist ein Lesefehler (Spec §7.1)
VOLL_MAX_PROZENT = 100.0     # Plausibilität: mehr als alle Zuschauer können ein Video nicht vollständig sehen
SCORE_STELLEN = 4        # Score auf 4 Nachkommastellen: genug für Paare mit Abstand 0,5 (Stufe 2), gut lesbar
SEKUNDEN_JE_TAG = 86400  # 24 · 60 · 60 – Alter von Posts und Messungen rechnen wir in Tagen

# Längen-Stufen des Rezepts (Spec §9.1: kurz ≤ 20 s, mittel 25–32 s, lang 40–45 s). Die Spec lässt Lücken; ein
# Post fällt in die NÄCHSTE Stufe, die Grenzen liegen deshalb in der Mitte der Lücken (Annahme A7,
# docs/ENTSCHEIDUNGEN.md „Annahmen im Sprint Lernschleife“).
LAENGE_GRENZEN = ((22.5, "kurz"), (36.0, "mittel"))  # darüber: "lang"

# Pfad eines TikTok-Videolinks: /@name/video/<Ziffern>. Kurzlinks (vm.tiktok.com/ZM…) haben keine ID im Pfad.
TIKTOK_VIDEO_PFAD = re.compile(r"^/@[^/]+/video/(\d+)/?$")

# --- Anzeige: so stehen Zahlen in Bot-Texten (Rückfrage, Bestätigung, /publikum, Meldungen) – je Format EINE Stelle
# Tausender-Trenner („1 240“): schmales, geschütztes Leerzeichen (U+202F). Eindeutig, anders als Punkt oder Komma
# (1.240 ist im Englischen 1,24), und Telegram bricht die Zahl nicht mitten durch um.
TAUSENDER = "\u202f"
# Symbol je Feld einer Messung (so knapp, dass alle sieben Werte in eine Handy-Zeile passen). Für „vollständig
# angesehen“ 🏁 statt ✅ – ✅ ist im Bot schon der Knopf „Stimmt“ bzw. „erledigt“.
SYMBOLE = {"views": "👁", "likes": "❤️", "kommentare": "💬", "shares": "↗️", "saves": "🔖", "wiedergabe_s": "⏱",
           "voll_prozent": "🏁"}


# --- Kleine Helfer, öffentlich (auch lernbot_publikum und lernbot_zahlen rechnen und zeigen damit) ----------

def einstellung(konfig: Konfig, name: str):
    """Ein Wert aus [publikum]. Die Standardwerte stehen nur in config/pipeline.toml (eine Wahrheit);
    fehlt ein Schlüssel, ist die Konfiguration kaputt → KonfigFehler mit dem Namen.
    Beispiel: einstellung(konfig, "fenster") == 20.

    Diese Regel gilt für die Schlüssel, die mit der Lernschleife neu sind ([publikum], [lernbot].screenshot_…).
    Schlüssel, die es vorher gab ([sperre].warten_s, [vorschau].max_mb, [shorts].max_kbit, [puffer].rohdaten_tage,
    [zeit].zeitzone), lesen wir wie der übrige Code mit konfig.wert(…, Standard) und mit demselben Standard wie
    dort – sonst verhielte sich derselbe Schlüssel je nach Modul verschieden."""
    abschnitt = konfig.abschnitt("publikum")
    if name not in abschnitt:
        raise KonfigFehler(f"[publikum].{name} fehlt in der Konfiguration (Standard steht in config/pipeline.toml)")
    return abschnitt[name]


def alter_in_tagen(von_iso: str, bis: datetime | str) -> float:
    """Tage zwischen zwei Zeitpunkten, z. B. gepostet → gemessen. Die eine Stelle für „wie alt ist das?“.
    Beispiel: gepostet 18.09. 10:00 UTC, bis 25.09. 12:00 UTC → 7,08 (7 Tage und 2 Stunden)."""
    bis_zeit = aus_iso(bis) if isinstance(bis, str) else bis
    return (bis_zeit - aus_iso(von_iso)).total_seconds() / SEKUNDEN_JE_TAG


def anzahl_text(n: int | float) -> str:
    """Ganze Zahl für Bot-Texte, mit Tausender-Trenner TAUSENDER: 1240 → „1 240“, 5 → „5“."""
    return f"{int(n):,}".replace(",", TAUSENDER)


def dezimal_text(x: float) -> str:
    """Zahl mit höchstens einer Nachkommastelle und Komma für Bot-Texte (Sekunden, Prozent, Tage): erst auf eine
    Stelle runden, dann fällt „,0“ weg. Beispiele: 6.8 → „6,8“, 6.96 → „7“, 7.0 → „7“, 34.5 → „34,5“."""
    text = f"{float(x):.1f}"
    return (text[:-2] if text.endswith(".0") else text).replace(".", ",")


# --- Kleine Helfer, nur hier ------------------------------------------------------------


def _feld(zeile: sqlite3.Row | dict | None, name: str):
    """Liest ein Feld aus einer Datenbank-Zeile oder einem dict; fehlt es (oder die Zeile), dann None.
    So funktionieren alle Rechnungen gleich für Zeilen aus SQLite und für Werte frisch vom Screenshot."""
    if zeile is None:
        return None
    try:
        return zeile[name]
    except (KeyError, IndexError):  # dict wirft KeyError, sqlite3.Row bei unbekannter Spalte IndexError
        return None


# --- Konfiguration --------------------------------------------------------------------

def post_plattformen(konfig: Konfig) -> list[str]:
    """Plattformen, für die Posts angelegt werden ([publikum].plattformen, Standard ["tiktok"]).

    Nicht zu verwechseln mit bot.aktionen.plattformen (Pflicht-Plattformen der Clip-Veröffentlichung): Hier geht
    es nur darum, wo die Lernschleife Zahlen sammelt. Unbekannte Einträge (nicht in PLATTFORMEN, z. B.
    "clipbattle") werden ignoriert und ins Log geschrieben (Warnung). Beispiel: ["tiktok", "youtube"] → beide;
    [] → keine Posts (Lernschleife aus)."""
    ergebnis: list[str] = []
    for eintrag in einstellung(konfig, "plattformen"):
        if eintrag not in PLATTFORMEN:
            log.warning("[publikum].plattformen: %r ist keine Post-Plattform (erlaubt: %s) – ignoriert",
                        eintrag, ", ".join(PLATTFORMEN))
        elif eintrag not in ergebnis:  # doppelt eingetragen zählt einmal
            ergebnis.append(eintrag)
    return ergebnis


# --- Rezept und Merkmale eines Posts (Stufe 1: abgeleitet, ab Stufe 3 aus rezepte.py) --------

def laenge_stufe(dauer_s: float) -> str:
    """Längen-Stufe eines Videos: "kurz" (bis 22,5 s), "mittel" (bis 36 s) oder "lang".

    Beispiel: laenge_stufe(18.0) == "kurz", laenge_stufe(31.0) == "mittel", laenge_stufe(52.0) == "lang"."""
    for grenze, stufe in LAENGE_GRENZEN:
        if dauer_s <= grenze:
            return stufe
    return "lang"


def rezept_fuer_clip(dauer_s: float) -> dict:
    """Rezept eines über den Clip-Bot geposteten Einzelclip-Shorts (Spec §10.4):
    {"hook": "stark_zuerst", "laenge": laenge_stufe(dauer_s), "tempo": "none", "machart": "roh",
     "experiment": False}. „none“: ein Einzelclip hat keine Schnitte auf dem Beat."""
    return {"hook": "stark_zuerst", "laenge": laenge_stufe(dauer_s), "tempo": "none", "machart": "roh",
            "experiment": False}


def rezept_fuer_entwurf(entwurf: sqlite3.Row, liste: dict) -> dict:
    """Rezept eines Regisseur-Entwurfs. Hat der Entwurf schon eines (`entwuerfe.rezept`, ab Stufe 3), gilt das.
    Sonst abgeleitet (Stufe 1, Annahme A6): hook "aufbau" (der heutige Bogen), laenge nach `liste["dauer_s"]`,
    tempo "beat1" bei beats_pro_schnitt 1, sonst "beat2" (auch bei 4), machart "regie", experiment False.

    Beispiel: Schnittliste 30 s mit beats_pro_schnitt 2 → {"hook": "aufbau", "laenge": "mittel",
    "tempo": "beat2", "machart": "regie", "experiment": False}. Kaputtes JSON in entwuerfe.rezept → ValueError."""
    gespeichert = _feld(entwurf, "rezept")
    if gespeichert:
        return json.loads(gespeichert)
    # Fehlt der Parameter (alte Schnittliste), gilt der Startwert des Regisseurs: 1 (regie.PARAMETER)
    beats = int((liste.get("parameter") or {}).get("beats_pro_schnitt", 1))
    return {"hook": "aufbau", "laenge": laenge_stufe(float(liste["dauer_s"])),
            "tempo": "beat1" if beats == 1 else "beat2", "machart": "regie", "experiment": False}


def clip_post_daten(con: sqlite3.Connection, konfig: Konfig, clip_id: int) -> dict:
    """Alles, was ein Post für einen Einzelclip braucht, nur aus der Datenbank (kein Dateizugriff – der Clip-Bot
    darf nicht an einem hängenden NFS stehen bleiben):
    {"dauer_s": …, "rezept": {…}, "merkmale": {…}}.

    dauer_s = shorts.gesamtdauer(Clip-Länge, konfig) mit Clip-Länge = quelle_ende_s − quelle_start_s – dieselbe
    Rechnung, mit der der Short gerendert wird (Endcard, Überblendung; ohne Endcard nur der Clip, Annahme A8).
    Beispiel: Clip 0–20 s, Endcard 2,5 s → 22,0 s. merkmale = {"momente": [{"clip_id", "merkmale"}],
    "hook_moment": …} aus clips.merkmale. KeyError, wenn der Clip fehlt.

    Jeder Moment trägt zusätzlich seinen Schlüssel "moment" = "clip:<id>" – derselbe Schlüssel wie in der Tabelle
    momente und in Schnittlisten, so sehen Clip- und Entwurfs-Posts gleich aus (hook_moment ist dieser Schlüssel)."""
    zeile = db.clip(con, clip_id)
    if zeile is None:
        raise KeyError(f"Clip {clip_id} gibt es nicht")
    clip_laenge = float(zeile["quelle_ende_s"]) - float(zeile["quelle_start_s"])
    # auf Millisekunden gerundet: mehr gibt die Schnittliste auch nicht her, und 22.0 statt 21.999999999
    dauer_s = round(shorts.gesamtdauer(clip_laenge, konfig), 3)
    moment = f"clip:{clip_id}"
    return {"dauer_s": dauer_s, "rezept": rezept_fuer_clip(dauer_s),
            "merkmale": {"momente": [{"moment": moment, "clip_id": clip_id,
                                      "merkmale": json.loads(zeile["merkmale"])}],
                         "hook_moment": moment}}


def _moment_merkmale(con: sqlite3.Connection, moment: str, clip_id: int | None) -> dict:
    """Merkmale eines Moments: bei einem Clip aus clips.merkmale (dieselben Merkmale wie im Clip-Bot, so bleiben
    Clip- und Entwurfs-Posts vergleichbar), sonst aus momente.merkmale; nichts gefunden → {}."""
    if clip_id is not None and (zeile := db.clip(con, int(clip_id))) is not None:
        return json.loads(zeile["merkmale"])
    zeile = con.execute("SELECT merkmale FROM momente WHERE schluessel = ?", (moment,)).fetchone()
    return json.loads(zeile["merkmale"]) if zeile else {}


def entwurf_post_daten(con: sqlite3.Connection, konfig: Konfig, entwurf_id: int) -> dict:
    """Wie clip_post_daten, für einen Regisseur-Entwurf. Liest die Schnittliste (liegt auf dem Mini, nicht im
    Lager): dauer_s aus der Liste, rezept über rezept_fuer_entwurf, merkmale = Momente der Segmente in
    Reihenfolge, "hook_moment" = Moment des ersten Segments, "stimmung", "musik" {titel, quelle}.
    KeyError, wenn der Entwurf fehlt.

    Ein Moment mit Jump-Cut besteht aus mehreren Segmenten – er steht trotzdem nur einmal in "momente" (an der
    Stelle seines ersten Auftritts). Je Moment: {"moment", "clip_id", "merkmale"}. Beispiel: Segmente
    clip:7, clip:7, clip:3 → momente [clip:7, clip:3], hook_moment "clip:7". Fehlt die Schnittliste-Datei:
    FileNotFoundError (der Aufrufer meldet es; ohne Liste gibt es keine ehrliche Dauer)."""
    zeile = con.execute("SELECT * FROM entwuerfe WHERE id = ?", (entwurf_id,)).fetchone()
    if zeile is None:
        raise KeyError(f"Entwurf {entwurf_id} gibt es nicht")
    liste = json.loads(Path(zeile["schnittliste"]).read_text(encoding="utf-8"))
    momente: list[dict] = []
    gesehen: set[str] = set()
    for segment in liste.get("segmente", []):
        if segment["moment"] in gesehen:
            continue
        gesehen.add(segment["moment"])
        momente.append({"moment": segment["moment"], "clip_id": segment.get("clip_id"),
                        "merkmale": _moment_merkmale(con, segment["moment"], segment.get("clip_id"))})
    musik = liste.get("musik")
    return {"dauer_s": float(liste["dauer_s"]), "rezept": rezept_fuer_entwurf(zeile, liste),
            "merkmale": {"momente": momente, "hook_moment": momente[0]["moment"] if momente else None,
                         "stimmung": liste.get("stimmung"),
                         "musik": {"titel": musik["titel"], "quelle": musik["quelle"]} if musik else None}}


# --- Posts ------------------------------------------------------------------------------

def ziel(art: str, ziel_id: int) -> str:
    """NULL-sicherer Schlüssel eines Posts (Spec §5): ziel("clip", 5) == "clip:5". ValueError bei fremder art."""
    if art not in ARTEN:
        raise ValueError(f"Unbekannte Post-Art {art!r} (erlaubt: {', '.join(ARTEN)})")
    return f"{art}:{int(ziel_id)}"


def video_id_aus_url(url: str) -> str | None:
    """Video-ID aus einem TikTok-Link: "https://www.tiktok.com/@name/video/7300123456789" → "7300123456789".
    Kurzlinks (vm.tiktok.com/…) und YouTube-Links → None (Stufe 4 ordnet dann per Zeit und Dauer zu)."""
    teile = urlsplit(url.strip())
    host = (teile.hostname or "").lower()
    if host != "tiktok.com" and not host.endswith(".tiktok.com"):  # www., m. – aber nicht „nichttiktok.com“
        return None
    treffer = TIKTOK_VIDEO_PFAD.match(teile.path)
    return treffer.group(1) if treffer else None


def post_anlegen(con: sqlite3.Connection, *, art: str, ziel_id: int, plattform: str, daten: dict,
                 zeit: datetime | None = None) -> tuple[int, bool]:
    """Legt den Post an – je (plattform, ziel) genau einmal (INSERT … ON CONFLICT DO NOTHING, E3).

    daten: Ergebnis von clip_post_daten/entwurf_post_daten. gepostet_utc = zeit (Häkchen bzw. /link). Gibt es den
    Post schon, bleibt alles, wie es ist (auch gepostet_utc = der erste Zeitpunkt). Einen Link trägt nur
    link_nachtragen ein – eine Regel für den Link, nicht zwei. Rückgabe: (post_id, neu angelegt?), z. B. (17, True)
    beim ersten Häkchen, (17, False) beim Doppelklick. Läuft in der Transaktion des Aufrufers (öffnet selbst
    keine). ValueError bei unbekannter plattform/art – und bei dauer_s ≤ 0 (der Score teilt durch die Dauer)."""
    if plattform not in PLATTFORMEN:
        raise ValueError(f"Unbekannte Plattform {plattform!r} für einen Post (erlaubt: {', '.join(PLATTFORMEN)})")
    schluessel = ziel(art, ziel_id)
    dauer_s = float(daten["dauer_s"])
    if not dauer_s > 0:  # so formuliert, dass auch NaN durchfällt
        raise ValueError(f"Post {schluessel}: Dauer {dauer_s} s ist keine Videolänge")
    zeitpunkt = iso(zeit or jetzt())
    rezept = daten["rezept"]
    cursor = con.execute(
        """INSERT INTO posts (art, ziel, clip_id, entwurf_id, plattform, gepostet_utc, dauer_s, rezept, merkmale,
                              experiment, erstellt)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (plattform, ziel) DO NOTHING""",
        (art, schluessel, ziel_id if art == "clip" else None, ziel_id if art == "entwurf" else None, plattform,
         zeitpunkt, dauer_s, json.dumps(rezept, ensure_ascii=False), json.dumps(daten["merkmale"], ensure_ascii=False),
         int(bool(rezept.get("experiment"))), zeitpunkt),
    )
    zeile = con.execute("SELECT id FROM posts WHERE plattform = ? AND ziel = ?", (plattform, schluessel)).fetchone()
    return int(zeile["id"]), cursor.rowcount == 1


def link_nachtragen(con: sqlite3.Connection, post_id: int, url: str) -> None:
    """Setzt url und (falls ablesbar) video_id eines Posts. Ein vorhandener Link wird durch einen neuen ersetzt
    (Tippfehler korrigieren), video_id nur überschrieben, wenn der neue Link eine enthält.

    Läuft in der Transaktion des Aufrufers. ValueError bei leerem Link oder unbekanntem Post (dann ist nichts
    geändert). Welche Plattform ein Link ist, prüft der Aufrufer vorher (bot.aktionen.plattform_aus_url)."""
    url = url.strip()
    if not url:
        raise ValueError("Leerer Link")
    cursor = con.execute("UPDATE posts SET url = ?, video_id = COALESCE(?, video_id) WHERE id = ?",
                         (url, video_id_aus_url(url), post_id))
    if cursor.rowcount != 1:
        raise ValueError(f"Post #{post_id} gibt es nicht")


def post(con: sqlite3.Connection, post_id: int) -> sqlite3.Row | None:
    """Eine Zeile aus posts oder None."""
    return con.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()


def post_zu(con: sqlite3.Connection, art: str, ziel_id: int, plattform: str) -> sqlite3.Row | None:
    """Der Post eines Clips bzw. Entwurfs auf einer Plattform (über den Schlüssel ziel), sonst None.
    Beispiel: post_zu(con, "entwurf", 41, "tiktok") → Zeile mit id 17. Die eine Stelle, an der Clip-Bot und
    Lern-Bot nachsehen, ob es einen Post schon gibt (Checkliste, /link) – kein eigenes SQL in den Bots."""
    return con.execute("SELECT * FROM posts WHERE plattform = ? AND ziel = ?",
                       (plattform, ziel(art, ziel_id))).fetchone()


def posts_ohne_messung(con: sqlite3.Connection, *, stunden: float = 24, grenze: int = 5,
                       zeit: datetime | None = None) -> list[sqlite3.Row]:
    """Die jüngsten `grenze` Posts, die in den letzten `stunden` keine Messung bekommen haben – für die Knöpfe,
    wenn ein Screenshot ohne „#Nummer“ kommt (Spec §7.1, Annahme A12). Neueste zuerst.
    Beispiel: 7 Posts, einer heute Morgen gemessen → die 5 jüngsten der übrigen 6."""
    seit = iso((zeit or jetzt()) - timedelta(hours=stunden))
    return con.execute(
        """SELECT p.* FROM posts p
            WHERE NOT EXISTS (SELECT 1 FROM publikum_messungen m WHERE m.post_id = p.id AND m.gemessen_utc >= ?)
            ORDER BY p.gepostet_utc DESC, p.id DESC
            LIMIT ?""",
        (seit, grenze),
    ).fetchall()


# --- Messungen --------------------------------------------------------------------------

def _hand_wert(text: str, feld: str) -> int | float | None:
    """Ein Wert der Hand-Eingabe: „–“ → None, sonst eine nicht negative, endliche Zahl; Zähler ganzzahlig.
    Beim Anteil „ganz angesehen“ darf ein „%“ dahinter stehen (die Form nennt „in %“, „34%“ meint 34)."""
    if text in UNBEKANNT:
        return None
    if feld == "voll_prozent":
        text = text.removesuffix("%")
    try:
        wert = float(text.replace(",", "."))
    except ValueError:
        if feld == "wiedergabe_s":  # die TikTok-App zeigt „0:07“ – gebraucht werden Sekunden, das sagen wir auch
            raise ValueError(f"Ø Wiedergabe bitte in Sekunden, z. B. 7 oder 6,8 – nicht „{text}“.") from None
        raise ValueError(f"„{text}“ ist keine Zahl.") from None
    if not math.isfinite(wert):  # float() liest auch „nan“ und „inf“ – das sind keine Zahlen von TikTok
        raise ValueError(f"„{text}“ ist keine gültige Zahl.")
    if wert < 0:
        raise ValueError(f"„{text}“ ist negativ – Zahlen von TikTok sind nie kleiner als 0.")
    if feld in ZAEHLER:
        if not wert.is_integer():
            raise ValueError(f"{ZAEHLER_NAMEN[feld]} müssen eine ganze Zahl sein, nicht „{text}“ "
                             "(1.240 bitte als 1240 schreiben).")
        return int(wert)
    return wert


def lies_hand_eingabe(text: str) -> dict:
    """Hand-Eingabe `views likes wiedergabe voll%` (Spec §7.1, in Worten HAND_FORM), Leerzeichen-getrennt, „–“
    oder „-“ = unbekannt, Komma als Dezimaltrenner erlaubt, „34%“ geht auch. Beispiel: "1240 61 6,8 34" →
    {"views": 1240, "likes": 61, "wiedergabe_s": 6.8, "voll_prozent": 34.0, übrige Felder None}.
    Nur die vier Felder – kommentare/shares/saves bleiben None (zählen im Engagement als 0, A10; offene
    Rückfrage R4, docs/ENTSCHEIDUNGEN.md). ValueError mit verständlichem Text bei falscher Anzahl, keiner Zahl
    (bei der Wiedergabe mit dem Hinweis „in Sekunden“) oder negativen Werten.
    Geprüft wird danach wie beim Screenshot mit pruefe_plausibel (der Lern-Bot fragt bei einem Verstoß nach).
    Views und Likes müssen ganze Zahlen sein – so fällt „1.240“ (Tausenderpunkt) auf, statt als 1,24 zu gelten."""
    teile = text.split()
    if len(teile) != len(HAND_FELDER):
        raise ValueError(f"Bitte genau vier Werte: {HAND_FORM} – z. B. „1240 61 6,8 34“, „–“ für unbekannt.")
    werte: dict = {feld: None for feld in FELDER}
    for feld, teil in zip(HAND_FELDER, teile):
        werte[feld] = _hand_wert(teil, feld)
    return werte


def letzte_messung(con: sqlite3.Connection, post_id: int) -> sqlite3.Row | None:
    """Die jüngste Messung eines Posts (nach gemessen_utc) oder None."""
    return con.execute(
        "SELECT * FROM publikum_messungen WHERE post_id = ? ORDER BY gemessen_utc DESC, id DESC LIMIT 1", (post_id,)
    ).fetchone()


def pruefe_plausibel(werte: dict, letzte: sqlite3.Row | dict | None, dauer_s: float) -> list[str]:
    """Fachliche Prüfung einer neuen Messung (Spec §7.1). Leere Liste = plausibel, sonst je Verstoß ein Satz:
      - kein Zähler (views, likes, kommentare, shares, saves) kleiner als in der letzten Messung
      - wiedergabe_s ≤ 1,5 · dauer_s
      - voll_prozent ≤ 100
    Fehlende Werte (None) werden nicht geprüft. Beispiel: views 900 nach zuvor 1240 → ["Views gesunken: 1 240 → 900"].
    Die Zahlen stehen wie in jeder Anzeige (anzahl_text, dezimal_text) – die Rückfrage zeigt sie neben den
    gelesenen Werten, dort soll dieselbe Zahl nicht in zwei Schreibweisen stehen."""
    verstoesse: list[str] = []
    # Regel 1: Zähler wachsen nur – eine gesunkene Zahl ist fast immer ein Lesefehler (falsche Zeile im Bild)
    for feld in ZAEHLER:
        neu, alt = werte.get(feld), _feld(letzte, feld)
        if neu is not None and alt is not None and neu < alt:
            verstoesse.append(f"{ZAEHLER_NAMEN[feld]} gesunken: {anzahl_text(alt)} → {anzahl_text(neu)}")
    # Regel 2: Ø Wiedergabe höchstens 1,5 × Videolänge (wer mehrfach schaut, kommt selten weit darüber)
    wiedergabe = werte.get("wiedergabe_s")
    if wiedergabe is not None and wiedergabe > WIEDERGABE_MAX_FAKTOR * dauer_s:
        verstoesse.append(f"Ø Wiedergabe {dezimal_text(wiedergabe)} s ist länger als 1,5 × Videolänge "
                          f"({dezimal_text(dauer_s)} s)")
    # Regel 3: „vollständig angesehen“ ist ein Anteil der Zuschauer – höchstens 100 %
    voll = werte.get("voll_prozent")
    if voll is not None and voll > VOLL_MAX_PROZENT:
        verstoesse.append(f"Vollständig angesehen {dezimal_text(voll)} % – mehr als 100 % geht nicht")
    return verstoesse


def speichere_messung(con: sqlite3.Connection, post_id: int, werte: dict, quelle: str, *, roh: str | None = None,
                      zeit: datetime | None = None) -> int:
    """Speichert eine (schon geprüfte oder von dir bestätigte) Messung. quelle: api | screenshot | hand.
    roh: Claude-JSON bzw. API-Antwort als Text (für Nachprüfungen; nie ins Log). Rückgabe: id der Messung.
    ValueError bei unbekannter quelle oder unbekanntem Post. Läuft in der Transaktion des Aufrufers.
    Felder, die in `werte` fehlen, werden als NULL (unbekannt) gespeichert."""
    if quelle not in QUELLEN:
        raise ValueError(f"Unbekannte Quelle {quelle!r} (erlaubt: {', '.join(QUELLEN)})")
    if post(con, post_id) is None:
        raise ValueError(f"Post #{post_id} gibt es nicht")
    zeitpunkt = iso(zeit or jetzt())
    spalten = ", ".join(FELDER)
    platzhalter = ", ".join("?" for _ in FELDER)
    cursor = con.execute(
        f"INSERT INTO publikum_messungen (post_id, gemessen_utc, quelle, {spalten}, roh, erstellt)"
        f" VALUES (?, ?, ?, {platzhalter}, ?, ?)",
        (post_id, zeitpunkt, quelle, *(werte.get(feld) for feld in FELDER), roh, zeitpunkt),
    )
    return int(cursor.lastrowid)


# --- Score (Spec §6) --------------------------------------------------------------------

def komponenten(messung: sqlite3.Row | dict, dauer_s: float) -> dict:
    """r, e, v einer Messung (Spec §6.2) – die einzige Stelle, an der diese Formeln stehen:
      r = min(1,2; wiedergabe_s / dauer_s), sonst voll_prozent / 100, sonst None
      e = (likes + 2·shares + saves + kommentare) / max(views, 1) – fehlende Zähler zählen 0 (Annahme A10)
      v = ln(1 + views)
    Ohne views: e und v sind None. Beispiel: views 1000, likes 50, shares 5, wiedergabe 6 s bei 20 s Dauer →
    r 0,3, e 0,06, v ≈ 6,909. Rückgabe zusätzlich "vermerke" (z. B. "Engagement unvollständig").
    ValueError bei dauer_s ≤ 0 (post_anlegen lässt so einen Post gar nicht erst zu)."""
    if not dauer_s > 0:
        raise ValueError(f"Dauer {dauer_s} s ist keine Videolänge")
    vermerke: list[str] = []

    # r – Wiedergabe: welcher Anteil des Videos im Schnitt gesehen wurde
    wiedergabe, voll = _feld(messung, "wiedergabe_s"), _feld(messung, "voll_prozent")
    if wiedergabe is not None:
        r = min(R_MAX, wiedergabe / dauer_s)
    elif voll is not None:
        r = voll / 100  # Prozent → Anteil; der schwächere Ersatz, wenn TikTok keine Ø Wiedergabe zeigt
    else:
        r = None

    # e – Engagement je View; Shares zählen doppelt (wer teilt, bringt neue Zuschauer). v – Reichweite, als
    # Logarithmus, weil 10 000 statt 1 000 Views ein ähnlich großer Schritt ist wie 1 000 statt 100.
    views = _feld(messung, "views")
    if views is None:
        e = v = None
    else:
        zaehler = {feld: _feld(messung, feld) for feld in ("likes", "kommentare", "shares", "saves")}
        if any(wert is None for wert in zaehler.values()):
            vermerke.append("Engagement unvollständig")
        n = {feld: wert or 0 for feld, wert in zaehler.items()}
        # max(views, 1): ein Post mit 0 Views hat Engagement 0, statt durch 0 zu teilen
        e = (n["likes"] + 2 * n["shares"] + n["saves"] + n["kommentare"]) / max(views, 1)
        v = math.log1p(views)  # ln(1 + views): 0 Views → 0
    return {"r": r, "e": e, "v": v, "vermerke": vermerke}


def waehle_messung(post: sqlite3.Row | dict, messungen: list, konfig: Konfig) -> sqlite3.Row | dict | None:
    """Die Messung, deren Alter (gemessen − gepostet) [publikum].alter_tage (7) am nächsten liegt – nur Messungen,
    die mindestens mindest_alter_tage (3) alt sind und views haben. Gleichstand: die spätere. Keine → None.
    Beispiel: Messungen an Tag 3,5 / 6 / 9 → Tag 6 (1 Tag Abstand); an Tag 6 und 8 → Tag 8."""
    ziel_tage = float(einstellung(konfig, "alter_tage"))
    mindest_tage = float(einstellung(konfig, "mindest_alter_tage"))
    kandidaten = []
    for messung in messungen:
        alter = alter_in_tagen(post["gepostet_utc"], messung["gemessen_utc"])
        if _feld(messung, "views") is None or alter < mindest_tage:
            continue
        # Sortierschlüssel: Abstand zum Ziel, bei Gleichstand die spätere (größeres Alter, dann größere id)
        kandidaten.append(((abs(alter - ziel_tage), -alter, -(_feld(messung, "id") or 0)), messung))
    return min(kandidaten, key=lambda k: k[0])[1] if kandidaten else None


def ist_faellig(post: sqlite3.Row | dict, konfig: Konfig, zeit: datetime | None = None) -> bool:
    """Darf der Score jetzt gesetzt werden? Nur wenn noch keiner gesetzt ist (bewertet_utc NULL) und der Post
    mindestens [publikum].alter_tage alt ist – sonst würde ein früher Score (Tag 3) eine spätere, bessere Messung
    (Tag 7) für immer verdrängen (Annahme A1)."""
    if _feld(post, "bewertet_utc") is not None:
        return False
    return alter_in_tagen(post["gepostet_utc"], zeit or jetzt()) >= float(einstellung(konfig, "alter_tage"))


def robust_z(x: float, basis: list[float]) -> tuple[float, float, float]:
    """Robuste Standardisierung (Spec §6.3): z = (x − Median) / (1,4826 · max(MAD, 0,05)), auf ±2,5 begrenzt.
    Rückgabe (z, median, mad) – mad ist der gemessene MAD (vor dem Minimum), damit score_teile ehrlich bleibt.
    Beispiel: basis [1, 2, 3, 4, 5], x = 5 → Median 3, MAD 1, z = 2/1,4826 ≈ 1,349.
    Zahlenbeispiel zum MAD-Minimum: Engagement-Werte [0,05 0,06 0,07 0,08 0,09] haben MAD 0,01 – gerechnet wird mit
    0,05, x = 0,09 ergibt z = 0,02/0,0741 ≈ 0,27 statt 1,35 (offene Rückfrage R3, docs/ENTSCHEIDUNGEN.md).
    basis darf keine None enthalten (die filtert score_fuer); ValueError bei leerer basis."""
    if not basis:
        raise ValueError("Leere Vergleichsbasis – ohne Vergleich gibt es keinen z-Wert")
    median = statistics.median(basis)
    mad = statistics.median(abs(b - median) for b in basis)
    z = (x - median) / (MAD_FAKTOR * max(mad, MAD_MINIMUM))
    return max(-Z_GRENZE, min(Z_GRENZE, z)), median, mad


def _zahl_aus_teilen(teile: dict, name: str, post_id: int, *, darf_fehlen: bool = False) -> float | None:
    """Eine Komponente aus gespeicherten score_teile – mit klarer Meldung, wenn sie kaputt ist."""
    wert = teile.get(name)
    if wert is None and darf_fehlen:
        return None
    if isinstance(wert, bool) or not isinstance(wert, (int, float)) or not math.isfinite(wert):
        raise ValueError(f"Post #{post_id}: score_teile.{name} ist keine Zahl ({wert!r})")
    return float(wert)


def vergleichsbasis(con: sqlite3.Connection, post: sqlite3.Row | dict, konfig: Konfig) -> list[dict]:
    """Die Komponenten {"post_id", "r", "e", "v"} (aus score_teile) der [publikum].fenster (20) jüngsten bewerteten
    Posts derselben Plattform, die VOR diesem Post gepostet wurden (gepostet_utc < post.gepostet_utc), jüngste
    zuerst (Annahme A11). So wird ein Post nur mit seinen Vorgängern verglichen – auch wenn er spät bewertet wird –
    und das Ergebnis hängt nicht davon ab, an welchem Tag der Lauf war. Der Post selbst ist nie dabei.
    r kann None sein (Post ohne Wiedergabe). Kaputtes score_teile-JSON → ValueError (bewerte_alle zählt es als
    Fehler dieses Posts)."""
    zeilen = con.execute(
        """SELECT id, score_teile FROM posts
            WHERE plattform = ? AND bewertet_utc IS NOT NULL AND gepostet_utc < ? AND id != ?
            ORDER BY gepostet_utc DESC, id DESC
            LIMIT ?""",
        (post["plattform"], post["gepostet_utc"], post["id"], int(einstellung(konfig, "fenster"))),
    ).fetchall()
    basis = []
    for zeile in zeilen:
        try:
            teile = json.loads(zeile["score_teile"] or "")
        except json.JSONDecodeError:
            raise ValueError(f"Post #{zeile['id']}: score_teile ist kein gültiges JSON") from None
        if not isinstance(teile, dict):
            raise ValueError(f"Post #{zeile['id']}: score_teile ist kein JSON-Objekt")
        basis.append({"post_id": zeile["id"],
                      "r": _zahl_aus_teilen(teile, "r", zeile["id"], darf_fehlen=True),
                      "e": _zahl_aus_teilen(teile, "e", zeile["id"]),
                      "v": _zahl_aus_teilen(teile, "v", zeile["id"])})
    return basis


def _gewichte(konfig: Konfig, mit_r: bool) -> dict[str, float]:
    """Gewichte der Komponenten aus [publikum.gewichte]. Ohne r werden e und v auf 1 hochgerechnet (Spec §6.4):
    0,3 / (0,3 + 0,2) = 0,6 und 0,2 / 0,5 = 0,4 – so bleibt der Score auf derselben Skala."""
    g = einstellung(konfig, "gewichte")
    w = {"r": float(g["wiedergabe"]), "e": float(g["engagement"]), "v": float(g["reichweite"])}
    if mit_r:
        return w
    rest = w["e"] + w["v"]
    if not rest > 0:  # Fachliche Prüfung der Konfig: sonst hätte ein Post ohne Wiedergabe gar kein Gewicht
        raise KonfigFehler("[publikum.gewichte]: engagement + reichweite muss größer als 0 sein")
    return {"r": 0.0, "e": w["e"] / rest, "v": w["v"] / rest}


def score_fuer(post: sqlite3.Row | dict, messungen: list, basis: list[dict],
               konfig: Konfig) -> tuple[float | None, dict]:
    """Publikums-Score eines Posts (Spec §6), deterministisch.

    Rückgabe (score, score_teile). score None = noch keine passende Messung („noch nicht bewertet“).
    score = 0,5·z_r + 0,3·z_e + 0,2·z_v ([publikum.gewichte]); ohne r: die Gewichte von e und v auf 1 hochgerechnet
    (0,3/0,5 = 0,6 und 0,2/0,5 = 0,4) mit Vermerk „ohne Wiedergabe“.

    Median und MAD je Komponente nur über die Werte der Basis, die es gibt (r fehlt bei Posts ohne Wiedergabe;
    e und v hat jeder bewertete Post, weil Messungen ohne views nicht zählen):
      - Basis unter MINDEST_BASIS Posts: alle z = 0, Score 0, Vermerk „Basis zu klein“ (Spec §6.3); ohne eigenes r
        zusätzlich „ohne Wiedergabe“ (die gespeicherten Gewichte sind dann 0,6/0,4).
      - Basis groß genug, aber unter MINDEST_BASIS r-Werten: gerechnet wie „ohne Wiedergabe“ (0,6/0,4), Vermerk
        „Basis ohne Wiedergabe“ – so bleibt der Score auf derselben Skala, statt dass z_r = 0 ihn halbiert.
    Beispiel: 20 Posts in der Basis, nur 2 davon mit r → score = 0,6·z_e + 0,4·z_v.

    score_teile (Spec §6.5): r, e, v, z_r, z_e, z_v (None, wo nicht gerechnet), median/mad je Komponente,
    messung_id, messung_alter_tage (z. B. 7,1), basis_n, basis_n_r, vermerke; dazu "gewichte" (die benutzten).
    Der Score ist auf 4 Nachkommastellen gerundet, die Teile nicht (sie sind die Basis späterer Posts)."""
    messung = waehle_messung(post, messungen, konfig)
    if messung is None:
        return None, {"vermerke": ["keine Messung ab dem Mindestalter mit Views"]}

    k = komponenten(messung, float(post["dauer_s"]))
    basis_r = [b["r"] for b in basis if b["r"] is not None]
    teile: dict = {"r": k["r"], "e": k["e"], "v": k["v"], "z_r": None, "z_e": None, "z_v": None,
                   "median_r": None, "mad_r": None, "median_e": None, "mad_e": None, "median_v": None, "mad_v": None,
                   "messung_id": _feld(messung, "id"),
                   # auf zwei Stellen: „7,1 Tage“ reicht zum Nachvollziehen, mehr ist Rauschen der Uhrzeit
                   "messung_alter_tage": round(alter_in_tagen(post["gepostet_utc"], messung["gemessen_utc"]), 2),
                   "basis_n": len(basis), "basis_n_r": len(basis_r), "vermerke": list(k["vermerke"])}

    # Fachliche Regel 1 (Spec §6.3): Unter 5 Vergleichsposts wird nichts gelernt – alle z = 0, Score 0.
    if len(basis) < MINDEST_BASIS:
        teile.update(z_r=0.0 if k["r"] is not None else None, z_e=0.0, z_v=0.0)
        teile["vermerke"].append(VERMERK_BASIS_ZU_KLEIN)
        if k["r"] is None:  # Spec §6.4: der Vermerk gilt auch, wenn der Score wegen zu kleiner Basis 0 ist
            teile["vermerke"].append("ohne Wiedergabe")
        teile["gewichte"] = _gewichte(konfig, mit_r=k["r"] is not None)
        return 0.0, teile

    teile["z_e"], teile["median_e"], teile["mad_e"] = robust_z(k["e"], [b["e"] for b in basis])
    teile["z_v"], teile["median_v"], teile["mad_v"] = robust_z(k["v"], [b["v"] for b in basis])
    # Fachliche Regel 2 (Spec §6.4): ohne eigenes r → e und v hochgerechnet.
    # Fachliche Regel 3 (Annahme A11): r gemessen, aber zu wenige r-Werte in der Basis → ebenso.
    if k["r"] is None:
        teile["vermerke"].append("ohne Wiedergabe")
    elif len(basis_r) < MINDEST_BASIS:
        teile["vermerke"].append("Basis ohne Wiedergabe")
    else:
        teile["z_r"], teile["median_r"], teile["mad_r"] = robust_z(k["r"], basis_r)

    gewichte = _gewichte(konfig, mit_r=teile["z_r"] is not None)
    teile["gewichte"] = gewichte
    # Die Score-Formel (Spec §6.4) – genau hier und nirgends sonst
    score = sum(gewichte[c] * teile[f"z_{c}"] for c in ("r", "e", "v") if teile[f"z_{c}"] is not None)
    return round(score, SCORE_STELLEN), teile


def bewerte_alle(con: sqlite3.Connection, konfig: Konfig, zeit: datetime | None = None) -> dict:
    """`pipeline publikum bewerten`: alle fälligen Posts bewerten, älteste zuerst (so gehören früher bewertete
    Posts desselben Laufs schon zur Basis der späteren). Setzt score, score_teile, bewertet_utc – nur wo
    bewertet_utc noch NULL ist (idempotent, Scores werden nie überschrieben). Jeder Post in eigener Transaktion.

    Fehler: Geht bei einem Post etwas schief (z. B. kaputtes score_teile-JSON in seiner Basis), wird das mit
    Post-Nummer und Fehlerart geloggt, der Post zählt als „fehler“ und bleibt unbewertet – weiter mit dem
    nächsten. Nur wenn die Datenbank selbst nicht geht (sqlite3.Error außerhalb eines Posts), fliegt die Ausnahme
    durch. Die CLI endet bei fehler > 0 mit Exit 1 (die JSON-Zeile kommt trotzdem).
    Rückgabe (für die JSON-Zeile der CLI): {"bewertet": n, "ohne_messung": n, "noch_zu_jung": n, "fehler": n,
    "posts": [{"id", "score"}]}, z. B. {"bewertet": 2, "ohne_messung": 1, "noch_zu_jung": 3, "fehler": 0,
    "posts": [{"id": 17, "score": 0.8}, {"id": 18, "score": -0.3}]}.

    Als „fachlicher“ Fehler eines Posts gelten ValueError, KeyError und TypeError (kaputte Daten); ein
    sqlite3.Error bedeutet, dass die Datenbank selbst nicht geht – der fliegt immer durch."""
    zeitpunkt = zeit or jetzt()
    ergebnis: dict = {"bewertet": 0, "ohne_messung": 0, "noch_zu_jung": 0, "fehler": 0, "posts": []}
    offene = con.execute("SELECT * FROM posts WHERE bewertet_utc IS NULL ORDER BY gepostet_utc, id").fetchall()
    for zeile in offene:
        if not ist_faellig(zeile, konfig, zeitpunkt):
            ergebnis["noch_zu_jung"] += 1
            continue
        try:
            with db.transaktion(con):
                messungen = con.execute("SELECT * FROM publikum_messungen WHERE post_id = ? ORDER BY gemessen_utc, id",
                                        (zeile["id"],)).fetchall()
                score, teile = score_fuer(zeile, messungen, vergleichsbasis(con, zeile, konfig), konfig)
                if score is not None:
                    # „AND bewertet_utc IS NULL“: auch ein paralleler Lauf überschreibt keinen gesetzten Score
                    con.execute("UPDATE posts SET score = ?, score_teile = ?, bewertet_utc = ?"
                                " WHERE id = ? AND bewertet_utc IS NULL",
                                (score, json.dumps(teile, ensure_ascii=False), iso(zeitpunkt), zeile["id"]))
        except (ValueError, KeyError, TypeError) as fehler:
            log.warning("Publikum: Post #%s nicht bewertet (%s: %s)", zeile["id"], type(fehler).__name__, fehler)
            ergebnis["fehler"] += 1
            continue
        if score is None:
            ergebnis["ohne_messung"] += 1
        else:
            ergebnis["bewertet"] += 1
            ergebnis["posts"].append({"id": zeile["id"], "score": score})
    if ergebnis["bewertet"] or ergebnis["fehler"]:
        log.info("Publikum: %d bewertet, %d ohne Messung, %d noch zu jung, %d Fehler", ergebnis["bewertet"],
                 ergebnis["ohne_messung"], ergebnis["noch_zu_jung"], ergebnis["fehler"])
    return ergebnis
