"""Publikum: Posts, Publikumszahlen und der Publikums-Score (Spec §5, §6, §7.1, §10.4).

Worum es geht: Ein Short ist erst dann „gut“, wenn er auf TikTok ankommt. Dieses Modul merkt sich jeden
veröffentlichten Short als **Post** (Tabelle `posts`), sammelt seine Zahlen als Zeitreihe von **Messungen**
(`publikum_messungen` – die Zahlen wachsen in den ersten Tagen noch) und macht daraus nach einer Woche genau
**eine** Zahl: den Publikums-Score.

Die Lernidee in Alltagssprache: Ein einzelner Post mit 5 000 Views sagt wenig – vielleicht hatte TikTok an dem
Tag Lust, ihn zu verteilen, vielleicht sind gerade viele neue Follower dazugekommen. Deshalb vergleicht der Score
einen Post nur mit **deinen eigenen letzten Posts** derselben Plattform: Lag er bei der Wiedergabe, beim
Engagement und bei der Reichweite über oder unter dem, was bei dir gerade üblich ist? „Üblich“ ist der Median,
die Streuung der MAD (mittlere absolute Abweichung vom Median) – beide lassen sich von einem Ausreißer-Post
nicht umwerfen. Die Wiedergabe zählt am meisten, weil sie direkt misst, ob Auswahl und Schnitt tragen.

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

Vertrag Stufe 1 (Skelett): Signaturen und Docstrings sind verbindlich, die Rümpfe baut Paket (a).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from . import shorts  # noqa: F401 – clip_post_daten rechnet die Dauer mit shorts.gesamtdauer
from .konfig import Konfig
from .zeit import aus_iso, iso, jetzt  # noqa: F401 – im Modul importiert, damit Tests `publikum.jetzt` ersetzen können

# Plattformen, für die es Posts geben darf. clip-battle.de ist keine: dort wird eingereicht, nicht geschaut.
PLATTFORMEN = ("tiktok", "youtube")

# Zähler einer Messung, die mit der Zeit nur wachsen können (Plausibilität, Spec §7.1)
ZAEHLER = ("views", "likes", "kommentare", "shares", "saves")
# Alle Felder einer Messung in der Reihenfolge des Screenshot-Schemas (schemas/publikum.schema.json)
FELDER = (*ZAEHLER, "wiedergabe_s", "voll_prozent")

# --- Konstanten der Score-Formel (Spec §6). Alles, was du ändern können sollst, steht in [publikum]. ---
MAD_FAKTOR = 1.4826      # macht den MAD bei normalverteilten Daten zur Standardabweichung (Spec §6.3)
MAD_MINIMUM = 0.05       # kleiner als das wird die Streuung nicht – sonst explodiert z bei fast gleichen Posts
Z_GRENZE = 2.5           # z wird auf ±2,5 begrenzt: ein Viral-Ausreißer soll nicht alles andere überstimmen
MINDEST_BASIS = 5        # unter 5 Vergleichswerten je Komponente: z = 0 (kein Lernen aus dem Nichts)
R_MAX = 1.2              # Wiedergabe/Dauer über 120 % (mehrfach angesehen) zählt nicht noch höher
WIEDERGABE_MAX_FAKTOR = 1.5  # Plausibilität: Ø Wiedergabe über 150 % der Dauer ist ein Lesefehler (Spec §7.1)

# Längen-Stufen des Rezepts (Spec §9.1: kurz ≤ 20 s, mittel 25–32 s, lang 40–45 s). Die Spec lässt Lücken; ein
# Post fällt in die NÄCHSTE Stufe, die Grenzen liegen deshalb in der Mitte der Lücken (Annahme A7 im Plan).
LAENGE_GRENZEN = ((22.5, "kurz"), (36.0, "mittel"))  # darüber: "lang"


# --- Konfiguration --------------------------------------------------------------------

def post_plattformen(konfig: Konfig) -> list[str]:
    """Plattformen, für die Posts angelegt werden ([publikum].plattformen, Standard ["tiktok"]).

    Nicht zu verwechseln mit bot.aktionen.plattformen (Pflicht-Plattformen der Clip-Veröffentlichung): Hier geht
    es nur darum, wo die Lernschleife Zahlen sammelt. Unbekannte Einträge (nicht in PLATTFORMEN, z. B.
    "clipbattle") werden ignoriert und ins Log geschrieben (Warnung). Beispiel: ["tiktok", "youtube"] → beide;
    [] → keine Posts (Lernschleife aus)."""
    raise NotImplementedError


# --- Rezept und Merkmale eines Posts (Stufe 1: abgeleitet, ab Stufe 3 aus rezepte.py) --------

def laenge_stufe(dauer_s: float) -> str:
    """Längen-Stufe eines Videos: "kurz" (bis 22,5 s), "mittel" (bis 36 s) oder "lang".

    Beispiel: laenge_stufe(18.0) == "kurz", laenge_stufe(31.0) == "mittel", laenge_stufe(52.0) == "lang"."""
    raise NotImplementedError


def rezept_fuer_clip(dauer_s: float) -> dict:
    """Rezept eines über den Clip-Bot geposteten Einzelclip-Shorts (Spec §10.4):
    {"hook": "stark_zuerst", "laenge": laenge_stufe(dauer_s), "tempo": "none", "machart": "roh",
     "experiment": False}. „none“: ein Einzelclip hat keine Schnitte auf dem Beat."""
    raise NotImplementedError


def rezept_fuer_entwurf(entwurf: sqlite3.Row, liste: dict) -> dict:
    """Rezept eines Regisseur-Entwurfs. Hat der Entwurf schon eines (`entwuerfe.rezept`, ab Stufe 3), gilt das.
    Sonst abgeleitet (Stufe 1, Annahme A6): hook "aufbau" (der heutige Bogen), laenge nach `liste["dauer_s"]`,
    tempo "beat1" bei beats_pro_schnitt 1, sonst "beat2" (auch bei 4), machart "regie", experiment False."""
    raise NotImplementedError


def clip_post_daten(con: sqlite3.Connection, konfig: Konfig, clip_id: int) -> dict:
    """Alles, was ein Post für einen Einzelclip braucht, nur aus der Datenbank (kein Dateizugriff – der Clip-Bot
    darf nicht an einem hängenden NFS stehen bleiben):
    {"dauer_s": …, "rezept": {…}, "merkmale": {…}}.

    dauer_s = shorts.gesamtdauer(Clip-Länge, konfig) mit Clip-Länge = quelle_ende_s − quelle_start_s – dieselbe
    Rechnung, mit der der Short gerendert wird (Endcard, Überblendung; ohne Endcard nur der Clip, Annahme A8).
    Beispiel: Clip 0–20 s, Endcard 2,5 s → 22,0 s. merkmale = {"momente": [{"clip_id", "merkmale"}],
    "hook_moment": …} aus clips.merkmale. KeyError, wenn der Clip fehlt."""
    raise NotImplementedError


def entwurf_post_daten(con: sqlite3.Connection, konfig: Konfig, entwurf_id: int) -> dict:
    """Wie clip_post_daten, für einen Regisseur-Entwurf. Liest die Schnittliste (liegt auf dem Mini, nicht im
    Lager): dauer_s aus der Liste, rezept über rezept_fuer_entwurf, merkmale = Momente der Segmente in
    Reihenfolge, "hook_moment" = Moment des ersten Segments, "stimmung", "musik" {titel, quelle}.
    KeyError, wenn der Entwurf fehlt."""
    raise NotImplementedError


# --- Posts ------------------------------------------------------------------------------

def ziel(art: str, ziel_id: int) -> str:
    """NULL-sicherer Schlüssel eines Posts (Spec §5): ziel("clip", 5) == "clip:5". ValueError bei fremder art."""
    raise NotImplementedError


def video_id_aus_url(url: str) -> str | None:
    """Video-ID aus einem TikTok-Link: "https://www.tiktok.com/@name/video/7300123456789" → "7300123456789".
    Kurzlinks (vm.tiktok.com/…) und YouTube-Links → None (Stufe 4 ordnet dann per Zeit und Dauer zu)."""
    raise NotImplementedError


def post_anlegen(con: sqlite3.Connection, *, art: str, ziel_id: int, plattform: str, daten: dict,
                 zeit: datetime | None = None) -> tuple[int, bool]:
    """Legt den Post an – je (plattform, ziel) genau einmal (INSERT … ON CONFLICT DO NOTHING, E3).

    daten: Ergebnis von clip_post_daten/entwurf_post_daten. gepostet_utc = zeit (Häkchen bzw. /link). Gibt es den
    Post schon, bleibt alles, wie es ist (auch gepostet_utc = der erste Zeitpunkt). Einen Link trägt nur
    link_nachtragen ein – eine Regel für den Link, nicht zwei. Rückgabe: (post_id, neu angelegt?), z. B. (17, True)
    beim ersten Häkchen, (17, False) beim Doppelklick. Läuft in der Transaktion des Aufrufers (öffnet selbst
    keine). ValueError bei unbekannter plattform/art."""
    raise NotImplementedError


def link_nachtragen(con: sqlite3.Connection, post_id: int, url: str) -> None:
    """Setzt url und (falls ablesbar) video_id eines Posts. Ein vorhandener Link wird durch einen neuen ersetzt
    (Tippfehler korrigieren), video_id nur überschrieben, wenn der neue Link eine enthält."""
    raise NotImplementedError


def post(con: sqlite3.Connection, post_id: int) -> sqlite3.Row | None:
    """Eine Zeile aus posts oder None."""
    raise NotImplementedError


def post_zu(con: sqlite3.Connection, art: str, ziel_id: int, plattform: str) -> sqlite3.Row | None:
    """Der Post eines Clips bzw. Entwurfs auf einer Plattform (über den Schlüssel ziel), sonst None.
    Beispiel: post_zu(con, "entwurf", 41, "tiktok") → Zeile mit id 17. Die eine Stelle, an der Clip-Bot und
    Lern-Bot nachsehen, ob es einen Post schon gibt (Checkliste, /link) – kein eigenes SQL in den Bots."""
    raise NotImplementedError


def posts_ohne_messung(con: sqlite3.Connection, *, stunden: float = 24, grenze: int = 5,
                       zeit: datetime | None = None) -> list[sqlite3.Row]:
    """Die jüngsten `grenze` Posts, die in den letzten `stunden` keine Messung bekommen haben – für die Knöpfe,
    wenn ein Screenshot ohne „#Nummer“ kommt (Spec §7.1, Annahme A12). Neueste zuerst."""
    raise NotImplementedError


# --- Messungen --------------------------------------------------------------------------

def lies_hand_eingabe(text: str) -> dict:
    """Hand-Eingabe `views likes wiedergabe voll%` (Spec §7.1), Leerzeichen-getrennt, „–“ oder „-“ = unbekannt,
    Komma als Dezimaltrenner erlaubt. Beispiel: "1240 61 6,8 34" →
    {"views": 1240, "likes": 61, "wiedergabe_s": 6.8, "voll_prozent": 34.0, übrige Felder None}.
    Nur die vier Felder – kommentare/shares/saves bleiben None (zählen im Engagement als 0, A10; Rückfrage an
    Florian im Plan). ValueError mit verständlichem Text bei falscher Anzahl, keiner Zahl oder negativen Werten.
    Geprüft wird danach wie beim Screenshot mit pruefe_plausibel (der Lern-Bot fragt bei einem Verstoß nach)."""
    raise NotImplementedError


def letzte_messung(con: sqlite3.Connection, post_id: int) -> sqlite3.Row | None:
    """Die jüngste Messung eines Posts (nach gemessen_utc) oder None."""
    raise NotImplementedError


def pruefe_plausibel(werte: dict, letzte: sqlite3.Row | dict | None, dauer_s: float) -> list[str]:
    """Fachliche Prüfung einer neuen Messung (Spec §7.1). Leere Liste = plausibel, sonst je Verstoß ein Satz:
      - kein Zähler (views, likes, kommentare, shares, saves) kleiner als in der letzten Messung
      - wiedergabe_s ≤ 1,5 · dauer_s
      - voll_prozent ≤ 100
    Fehlende Werte (None) werden nicht geprüft. Beispiel: views 900 nach zuvor 1240 → ["Views gesunken: 1240 → 900"]."""
    raise NotImplementedError


def speichere_messung(con: sqlite3.Connection, post_id: int, werte: dict, quelle: str, *, roh: str | None = None,
                      zeit: datetime | None = None) -> int:
    """Speichert eine (schon geprüfte oder von dir bestätigte) Messung. quelle: api | screenshot | hand.
    roh: Claude-JSON bzw. API-Antwort als Text (für Nachprüfungen; nie ins Log). Rückgabe: id der Messung.
    ValueError bei unbekannter quelle oder unbekanntem Post. Läuft in der Transaktion des Aufrufers."""
    raise NotImplementedError


# --- Score (Spec §6) --------------------------------------------------------------------

def komponenten(messung: sqlite3.Row | dict, dauer_s: float) -> dict:
    """r, e, v einer Messung (Spec §6.2) – die einzige Stelle, an der diese Formeln stehen:
      r = min(1,2; wiedergabe_s / dauer_s), sonst voll_prozent / 100, sonst None
      e = (likes + 2·shares + saves + kommentare) / max(views, 1) – fehlende Zähler zählen 0 (Annahme A10)
      v = ln(1 + views)
    Ohne views: e und v sind None. Beispiel: views 1000, likes 50, shares 5, wiedergabe 6 s bei 20 s Dauer →
    r 0,3, e 0,06, v ≈ 6,909. Rückgabe zusätzlich "vermerke" (z. B. "Engagement unvollständig")."""
    raise NotImplementedError


def waehle_messung(post: sqlite3.Row | dict, messungen: list, konfig: Konfig) -> sqlite3.Row | dict | None:
    """Die Messung, deren Alter (gemessen − gepostet) [publikum].alter_tage (7) am nächsten liegt – nur Messungen,
    die mindestens mindest_alter_tage (3) alt sind und views haben. Gleichstand: die spätere. Keine → None."""
    raise NotImplementedError


def ist_faellig(post: sqlite3.Row | dict, konfig: Konfig, zeit: datetime | None = None) -> bool:
    """Darf der Score jetzt gesetzt werden? Nur wenn noch keiner gesetzt ist (bewertet_utc NULL) und der Post
    mindestens [publikum].alter_tage alt ist – sonst würde ein früher Score (Tag 3) eine spätere, bessere Messung
    (Tag 7) für immer verdrängen (Annahme A1)."""
    raise NotImplementedError


def robust_z(x: float, basis: list[float]) -> tuple[float, float, float]:
    """Robuste Standardisierung (Spec §6.3): z = (x − Median) / (1,4826 · max(MAD, 0,05)), auf ±2,5 begrenzt.
    Rückgabe (z, median, mad) – mad ist der gemessene MAD (vor dem Minimum), damit score_teile ehrlich bleibt.
    Beispiel: basis [1, 2, 3, 4, 5], x = 5 → Median 3, MAD 1, z = 2/1,4826 ≈ 1,349.
    Zahlenbeispiel zum MAD-Minimum: Engagement-Werte [0,05 0,06 0,07 0,08 0,09] haben MAD 0,01 – gerechnet wird mit
    0,05, x = 0,09 ergibt z = 0,02/0,0741 ≈ 0,27 statt 1,35 (Rückfrage an Florian im Plan).
    basis darf keine None enthalten (die filtert score_fuer); ValueError bei leerer basis."""
    raise NotImplementedError


def vergleichsbasis(con: sqlite3.Connection, post: sqlite3.Row | dict, konfig: Konfig) -> list[dict]:
    """Die Komponenten {"post_id", "r", "e", "v"} (aus score_teile) der [publikum].fenster (20) jüngsten bewerteten
    Posts derselben Plattform, die VOR diesem Post gepostet wurden (gepostet_utc < post.gepostet_utc), jüngste
    zuerst (Annahme A11). So wird ein Post nur mit seinen Vorgängern verglichen – auch wenn er spät bewertet wird –
    und das Ergebnis hängt nicht davon ab, an welchem Tag der Lauf war. Der Post selbst ist nie dabei.
    r kann None sein (Post ohne Wiedergabe). Kaputtes score_teile-JSON → ValueError (bewerte_alle zählt es als
    Fehler dieses Posts)."""
    raise NotImplementedError


def score_fuer(post: sqlite3.Row | dict, messungen: list, basis: list[dict],
               konfig: Konfig) -> tuple[float | None, dict]:
    """Publikums-Score eines Posts (Spec §6), deterministisch.

    Rückgabe (score, score_teile). score None = noch keine passende Messung („noch nicht bewertet“).
    score = 0,5·z_r + 0,3·z_e + 0,2·z_v ([publikum.gewichte]); ohne r: die Gewichte von e und v auf 1 hochgerechnet
    (0,3/0,5 = 0,6 und 0,2/0,5 = 0,4) mit Vermerk „ohne Wiedergabe“.

    Median und MAD je Komponente nur über die Werte der Basis, die es gibt (r fehlt bei Posts ohne Wiedergabe;
    e und v hat jeder bewertete Post, weil Messungen ohne views nicht zählen):
      - Basis unter MINDEST_BASIS Posts: alle z = 0, Score 0, Vermerk „Basis zu klein“ (Spec §6.3).
      - Basis groß genug, aber unter MINDEST_BASIS r-Werten: gerechnet wie „ohne Wiedergabe“ (0,6/0,4), Vermerk
        „Basis ohne Wiedergabe“ – so bleibt der Score auf derselben Skala, statt dass z_r = 0 ihn halbiert.
    Beispiel: 20 Posts in der Basis, nur 2 davon mit r → score = 0,6·z_e + 0,4·z_v.

    score_teile (Spec §6.5): r, e, v, z_r, z_e, z_v (None, wo nicht gerechnet), median/mad je Komponente,
    messung_id, messung_alter_tage (z. B. 7,1), basis_n, basis_n_r, vermerke."""
    raise NotImplementedError


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
    "posts": [{"id": 17, "score": 0.8}, {"id": 18, "score": -0.3}]}."""
    raise NotImplementedError
