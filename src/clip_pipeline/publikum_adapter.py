"""Offizielle Plattformzugänge und ein gemeinsames Metrikformat.

TikTok Display liefert keine Watchtime/Saves. YouTube Analytics benötigt einen
autorisierten Kanal und yt-analytics.readonly. Instagram Insights kann importiert
werden; ein automatischer Instagram-Zugang wird hier ausdrücklich nicht behauptet.
Alle HTTP-Ziele sind fest; Tokens stehen nur in Authorization bzw. OAuth-Bodies.
"""

from __future__ import annotations

import json
import hashlib
import logging
import math
import os
import tempfile
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from . import caption, publikum
from .zeit import UTC, aus_iso, iso, jetzt

log = logging.getLogger("pipeline")

ALIASE = {
    "tiktok": {"views": "view_count", "likes": "like_count", "kommentare": "comment_count",
               "shares": "share_count", "saves": "favorites_count"},
    "youtube": {"views": "views", "likes": "likes", "kommentare": "comments", "shares": "shares",
                "wiedergabe_s": "averageViewDuration", "retention_prozent": "averageViewPercentage",
                "follows": "subscribersGained"},
    "instagram": {"views": "views", "likes": "likes", "kommentare": "comments", "shares": "shares",
                  "saves": "saved", "follows": "follows", "profilaufrufe": "profile_visits",
                  "rewatches": "clips_replays_count", "skip_prozent": "reels_skip_rate"},
}
ZAEHLER = {*publikum.ZAEHLER, "impressions", "follows", "profilaufrufe", "rewatches"}


class AdapterFehler(RuntimeError):
    """Absichtlich ohne URL, Antwortbody oder Tokens im Fehlertext."""


class _KeineWeiterleitung(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise AdapterFehler("API-Weiterleitung abgelehnt")


def _json(url: str, token: str | None = None, *, daten=None, formular=False) -> dict:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = None
    if daten is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded" if formular else "application/json"
        body = (urlencode(daten) if formular else json.dumps(daten)).encode("utf-8")
    try:
        with build_opener(_KeineWeiterleitung()).open(Request(url, data=body, headers=headers), timeout=20) as antwort:
            roh = antwort.read(3_000_001)
        if len(roh) > 3_000_000:
            raise AdapterFehler("API-Antwort zu groß")
        wert = json.loads(roh)
    except HTTPError as exc:
        raise AdapterFehler(f"API HTTP {exc.code}") from None
    except (URLError, TimeoutError, OSError):
        raise AdapterFehler("API nicht erreichbar") from None
    except (ValueError, UnicodeError):
        raise AdapterFehler("API liefert kein gültiges JSON") from None
    if not isinstance(wert, dict):
        raise AdapterFehler("API liefert kein Objekt")
    fehler = wert.get("error")
    if fehler and (not isinstance(fehler, dict) or fehler.get("code") not in (None, "ok")):
        raise AdapterFehler("API hat die Anfrage abgelehnt (Zugang/Berechtigungen prüfen)")
    return wert


ANMELDUNG = "anmeldung"   # Seed einer Anmeldung per /tiktok (tiktok_anmeldung) – geht vor dem Refresh-Token aus .env


def cache_pfad(konfig) -> Path:
    """Private Token-Datei neben der DB (Linux 0600, Git ignoriert sie)."""
    return konfig.datenbank.parent / "publikum-oauth.json"


def lies_cache(pfad: Path) -> dict:
    if not pfad.is_file():
        return {}
    try:
        return json.loads(pfad.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise AdapterFehler("TikTok-Token-Datei ist nicht lesbar") from None


def schreibe_cache(pfad: Path, daten: dict) -> None:
    """Atomar und privat (0600): erst Temp-Datei, fsync, dann ersetzen."""
    pfad.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".publikum-oauth-", suffix=".tmp", dir=pfad.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(daten, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, pfad)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _token(plattform: str, konfig, zeit: datetime) -> str | None:
    """Access-Token bzw. automatische Erneuerung; rotierte TikTok-Refresh-Tokens
    bleiben atomar in einer privaten Datei neben der DB (Linux 0600), nie im Repo.
    TikTok: Eine Anmeldung per /tiktok (seed "anmeldung") reicht – dann braucht .env nur Key und Secret.
    """
    prefix = plattform.upper()
    direkt = os.environ.get(f"{prefix}_ACCESS_TOKEN", "").strip()
    refresh = os.environ.get(f"{prefix}_REFRESH_TOKEN", "").strip()
    client_name = "CLIENT_KEY" if plattform == "tiktok" else "CLIENT_ID"
    client = os.environ.get(f"{prefix}_{client_name}", "").strip()
    secret = os.environ.get(f"{prefix}_CLIENT_SECRET", "").strip()
    pfad = cache_pfad(konfig)
    cache = lies_cache(pfad) if plattform == "tiktok" and client and secret else {}
    angemeldet = (cache.get("client_key") == client and cache.get("seed") == ANMELDUNG
                  and bool(cache.get("refresh_token")))
    if not ((refresh or angemeldet) and client and secret):
        return direkt or None
    # YouTube gibt beim Refresh keinen neuen Refresh-Token aus. Der langlebige
    # Token bleibt nur in .env; der kurzfristige Access-Token bleibt im Speicher.
    if plattform == "youtube":
        antwort = _json("https://oauth2.googleapis.com/token", daten={"grant_type": "refresh_token",
                         "client_id": client, "client_secret": secret, "refresh_token": refresh}, formular=True)
        if not antwort.get("access_token"):
            raise AdapterFehler("YouTube-Token konnte nicht erneuert werden")
        return str(antwort["access_token"])
    seed = ANMELDUNG if angemeldet else hashlib.sha256(refresh.encode()).hexdigest()
    # Andere App-Konfiguration darf keine alten Tokens verwenden.
    if cache.get("client_key") == client and cache.get("seed") == seed:
        refresh = cache.get("refresh_token") or refresh
        if cache.get("access_token") and float(cache.get("expires_at", 0)) > zeit.timestamp() + 300:
            return str(cache["access_token"])
    antwort = _json("https://open.tiktokapis.com/v2/oauth/token/", daten={"grant_type": "refresh_token",
                     "client_key": client, "client_secret": secret, "refresh_token": refresh}, formular=True)
    if not antwort.get("access_token"):
        raise AdapterFehler("TikTok-Token konnte nicht erneuert werden")
    schreibe_cache(pfad, {**{k: v for k, v in cache.items() if k in ("open_id", "scope")},
                          "client_key": client, "seed": seed, "access_token": antwort["access_token"],
                          "refresh_token": antwort.get("refresh_token") or refresh,
                          "expires_at": zeit.timestamp() + float(antwort.get("expires_in", 0))})
    return str(antwort["access_token"])


def normalisiere(plattform: str, antwort: dict) -> dict:
    """Native API-Antwort oder schon normalisierte Metriken → nullable Werte.

    Prozentfelder sind immer 0..100 (Retention darf bei Wiederholungen >100
    sein). YouTube averageViewPercentage ist Retention, KEINE Completion Rate.
    Unbekannte numerische Plattformmetriken bleiben in ``metriken`` erhalten.
    """
    if plattform not in ALIASE:
        raise ValueError("Nicht unterstützte Plattform")
    daten = dict(antwort)
    if plattform == "tiktok" and "data" in antwort:
        videos = antwort["data"].get("videos") or []
        if len(videos) != 1:
            raise AdapterFehler("TikTok-Antwort enthält nicht genau ein zugeordnetes Video")
        daten = dict(videos[0])
    elif plattform == "youtube" and "columnHeaders" in antwort:
        zeilen = antwort.get("rows") or []
        if not zeilen:
            return {feld: None for feld in publikum.MESSFELDER}
        if len(zeilen) != 1:
            raise AdapterFehler("YouTube-Import erwartet einen aggregierten Video-Bericht")
        daten = dict(zip((h["name"] for h in antwort["columnHeaders"]), zeilen[0]))
    elif plattform == "instagram" and isinstance(antwort.get("data"), list):
        daten = {}
        for eintrag in antwort["data"]:
            wert = eintrag.get("total_value", {}).get("value")
            if wert is None and eintrag.get("values"):
                wert = eintrag["values"][-1].get("value")
            daten[eintrag["name"]] = wert
    ergebnis = {}
    for feld in publikum.MESSFELDER:
        wert = daten.get(feld, daten.get(ALIASE[plattform].get(feld, feld)))
        if wert is not None:
            try:
                if isinstance(wert, bool):
                    raise ValueError
                wert = float(wert)
                if not math.isfinite(wert) or wert < 0 or (feld in ZAEHLER and not wert.is_integer()):
                    raise ValueError
                if feld in ("voll_prozent", "skip_prozent") and wert > 100:
                    raise ValueError
            except (ValueError, TypeError):
                raise AdapterFehler(f"Ungültige Metrik: {feld}") from None
            if feld in ZAEHLER:
                wert = int(wert)
        ergebnis[feld] = wert
    benutzt = set(publikum.MESSFELDER) | set(ALIASE[plattform].values())
    ergebnis["metriken"] = {k: v for k, v in daten.items() if k not in benutzt
                            and isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)}
    return ergebnis


# Gleiche Zahlen wie bei der letzten API-Messung zählen wieder als Messung, wenn die mindestens so alt ist (08.10.,
# Stufe 3 „Auch Flops bekommen ihre Zuschauer-Note“). 20 statt 24 h: Der tägliche Abruf (clip-publikum.timer, 10:00)
# streut bis zu 10 min, zwei Läufe liegen also auch einmal 23 h 50 min auseinander.
GLEICHE_ZAHLEN_NACH = timedelta(hours=20)


def importiere(con, konfig, post_id: int, antwort: dict, *, zeit: datetime | None = None) -> int | None:
    """Eindeutig zugeordnete API-/Exportdaten speichern und automatisch lernen.

    Gleiche Zahlen wie bei der letzten API-Messung schreiben keine zweite Messung (auch nicht ein zweiter Import
    desselben Exports) – außer der Post hat noch keinen Score und die letzte Messung ist mindestens
    GLEICHE_ZAHLEN_NACH (20 h) alt. Sonst bekam ein Video, dessen Zahlen ab Tag 2 stehen bleiben (der typische Flop),
    nie eine Messung ab Tag 3 und damit nie einen Score; jetzt hat es eine je Tag und an Tag 7 seinen Score. Nach dem
    Score nicht mehr: Er wird nie überschrieben, und jede gespeicherte Messung lässt autonom alles neu durchrechnen.
    Die Post-Plattform stammt aus der DB, nicht aus frei mitgelieferten Daten.
    """
    zeit = zeit or jetzt()
    post = publikum.post(con, post_id)
    if post is None:
        raise ValueError(f"Post #{post_id} gibt es nicht")
    werte = normalisiere(post["plattform"], antwort)
    if not any(werte.get(feld) is not None for feld in publikum.MESSFELDER):
        return None
    letzte = con.execute("SELECT * FROM publikum_messungen WHERE post_id=? AND quelle='api'"
                         " ORDER BY gemessen_utc DESC,id DESC LIMIT 1", (post_id,)).fetchone()
    if letzte is not None and all(letzte[f] == werte.get(f) for f in publikum.MESSFELDER) \
            and json.loads(letzte["metriken"] or "{}") == werte.get("metriken", {}):
        if post["bewertet_utc"] is not None or zeit - aus_iso(letzte["gemessen_utc"]) < GLEICHE_ZAHLEN_NACH:
            return None
    # API-Zähler dürfen von der Plattform korrigiert werden. Keine manuelle
    # Rückfrage wie bei OCR; vorangehende Messungen bleiben trotzdem erhalten.
    return publikum.speichere_messung(con, post_id, werte, "api", zeit=zeit, konfig=konfig,
                                     roh=json.dumps(werte, ensure_ascii=False, allow_nan=False))


def _erste_zeile(text) -> str:
    """Erste nicht leere Zeile eines Textes, für den Vergleich vereinheitlicht (NFKC, ohne Emoji-Varianten- und
    unsichtbare Steuerzeichen, Groß-/Kleinschreibung egal, Leerraum zusammengefasst). Beispiel:
    „Fortnite-Highlights: Triple Kill · 5 Momente⏎⏎⚔️ Wer gewinnt …“ → „fortnite-highlights: triple kill · 5 momente“.
    Kein Text → ''."""
    text = "".join(z for z in unicodedata.normalize("NFKC", str(text or ""))
                   if z not in "\ufe0e\ufe0f" and unicodedata.category(z) != "Cf")
    for zeile in text.splitlines():
        if zeile.strip():
            return " ".join(zeile.casefold().split())
    return ""


def _caption_zeile(con, konfig, post) -> str:
    """Erste Zeile der Caption, die das Upload-Paket zu diesem Post mitgab – dieselbe Rechnung wie
    lernbot_paket.baue_paket (caption.entwurf_caption aus der Schnittliste), vereinheitlicht wie _erste_zeile. Nur
    Entwürfe: Die Beschreibung eines Clips kann von der KI stammen und ließe sich nicht nachbauen. Fehlt etwas
    (Entwurf, Schnittliste, Vorlage): '' – dann gibt es keinen Beleg, nie einen Abbruch des Abrufs."""
    if post["art"] != "entwurf" or post["entwurf_id"] is None:
        return ""
    try:
        zeile = con.execute("SELECT schnittliste FROM entwuerfe WHERE id = ?", (post["entwurf_id"],)).fetchone()
        liste = json.loads(Path(zeile["schnittliste"]).read_text(encoding="utf-8"))
        return _erste_zeile(caption.entwurf_caption(con, liste, konfig))
    except Exception:  # noqa: BLE001 – ohne Caption nur kein Beleg; der Abruf der Zahlen läuft weiter
        return ""


def _tiktok_zuordnen(con, konfig, token: str, posts: list, zeit: datetime) -> dict[int, str]:
    """TikTok-Posts ohne Video-Nummer (kein Link, Kurzlink vm.tiktok.com, im einfachen Modus immer – das Paket legt
    den Post an) selbst zuordnen – nur, wenn es eindeutig ist (08.10., Stufe 3: lieber keine Zahlen als falsche).
    Eigene Videoliste über /v2/video/list/ (Scope video.list, mit video_description: höchstens 150 Zeichen, die
    erste Zeile reicht). Ein Video passt zu einem offenen Post, wenn es höchstens [publikum].zuordnung_stunden (72)
    vor oder nach dem Post erstellt wurde (Häkchen/Link unter /experte kommt nach dem Hochladen, das Paket davor), die
    Länge auf ±2 s stimmt und seine Beschreibung nicht mit einer anderen ersten Zeile beginnt als die Caption des
    Posts. Zugeordnet wird nur ein Paar, bei dem das Video der einzige Kandidat des Posts ist und der Post der einzige
    des Videos; bei mehreren entscheidet die erste Zeile (genau gleich = Beleg). Was danach frei ist, wird neu
    geprüft. Sonst bleibt der Post offen, z. B. zwei gleich lange Videos mit gleicher erster Zeile.

    Trägt video_id (und url, falls leer) nach und setzt gepostet_utc auf create_time des Videos – sonst zählte
    „Tag 7“ ab dem ✅ statt ab dem Upload. Ein Video gehört nie zu zwei Posts. Rückgabe: {post_id: video_id}."""
    fenster = float(konfig.wert("publikum.zuordnung_stunden", 72)) * 3600
    frueheste = min(aus_iso(p["gepostet_utc"]).timestamp() for p in posts) - fenster
    roh: dict[str, dict] = {}
    cursor = None
    for _ in range(int(konfig.wert("publikum.zuordnung_seiten", 10))):  # je 20 Videos, neueste zuerst
        antwort = _json("https://open.tiktokapis.com/v2/video/list/?" + urlencode(
            {"fields": "id,create_time,duration,share_url,video_description"}), token,
            daten={"max_count": 20, **({"cursor": cursor} if cursor else {})})
        daten = antwort.get("data") or {}
        seite = daten.get("videos") or []
        for v in seite:   # dasselbe Video auf zwei Seiten (neuer Upload während des Blätterns) zählt einmal
            if str(v.get("id") or ""):
                roh.setdefault(str(v["id"]), v)
        cursor = daten.get("cursor")
        if not daten.get("has_more") or not seite or not cursor or \
                min(float(v.get("create_time") or 0) for v in seite) < frueheste:
            break
    vergeben = {z[0] for z in con.execute("SELECT video_id FROM posts WHERE plattform = 'tiktok' AND video_id IS NOT NULL")}
    videos = []
    for vid, v in roh.items():
        try:
            if vid not in vergeben:
                videos.append({"id": vid, "t": float(v["create_time"]), "dauer": float(v["duration"]),
                               "url": v.get("share_url"), "zeile": _erste_zeile(v.get("video_description"))})
        except (KeyError, TypeError, ValueError):
            continue   # ohne Zeit oder Länge kein Kandidat
    offen = [{"id": int(p["id"]), "t": aus_iso(p["gepostet_utc"]).timestamp(), "dauer": float(p["dauer_s"]),
              "zeile": _caption_zeile(con, konfig, p)}
             for p in sorted(posts, key=lambda z: (z["gepostet_utc"], z["id"]))]

    def passt(p: dict, v: dict) -> bool:
        widerspricht = p["zeile"] and v["zeile"] and not v["zeile"].startswith(p["zeile"])
        return abs(v["t"] - p["t"]) <= fenster and abs(v["dauer"] - p["dauer"]) <= 2.0 and not widerspricht

    def einziger(kandidaten: list, beleg) -> dict | None:
        if len(kandidaten) > 1:   # Gleichstand: nur, wessen erste Zeile genau passt
            kandidaten = [k for k in kandidaten if beleg(k)]
        return kandidaten[0] if len(kandidaten) == 1 else None

    def beleg(p: dict, v: dict) -> bool:
        return bool(p["zeile"]) and v["zeile"] == p["zeile"]

    zugeordnet: dict[int, str] = {}
    weiter = True
    while weiter:
        weiter = False
        for p in list(offen):
            v = einziger([v for v in videos if passt(p, v)], lambda v: beleg(p, v))
            if v is None or einziger([q for q in offen if passt(q, v)], lambda q: beleg(q, v)) is not p:
                continue
            if not beleg(p, v) and zeit.timestamp() < max(p["t"], v["t"]) + fenster:
                continue   # ohne passende erste Zeile erst, wenn kein Video und kein Post mehr dazukommen kann
            offen.remove(p)
            videos.remove(v)
            weiter = True
            if con.execute("UPDATE posts SET video_id = ?, url = COALESCE(url, ?), gepostet_utc = ? WHERE id = ?"
                           " AND video_id IS NULL",
                           (v["id"], v["url"], iso(datetime.fromtimestamp(v["t"], UTC)), p["id"])).rowcount != 1:
                continue   # inzwischen per /link versorgt – dessen Video gilt
            zugeordnet[p["id"]] = v["id"]
            log.info("Publikum: Post #%s ist TikTok-Video %s (eindeutig per Zeit, Länge und Beschreibung)",
                     p["id"], v["id"])
    for p in offen:
        if n := sum(passt(p, v) for v in videos):
            log.info("Publikum: Post #%s bleibt offen – %s Video(s) kommen in Frage, noch nicht eindeutig", p["id"], n)
    return zugeordnet


def abrufen(con, konfig, zeit: datetime | None = None) -> dict:
    """Timer-Einstieg: vorhandene Posts automatisch aktualisieren, ohne Bewertung.

    Keine Tokens → kein Netz und expliziter Zähler. Die Plattformzugänge werden
    pro Lauf einmal erneuert. Ein defekter Zugang stoppt nicht andere Plattformen.
    """
    zeit = zeit or jetzt()
    ergebnis = dict(gespeichert=0, unveraendert=0, ohne_zugang=0, ohne_id=0, zugeordnet=0, fehler=0)
    if not konfig.wert("publikum.api_abruf", True):
        ergebnis["deaktiviert"] = True
        return ergebnis
    seit = iso(zeit - timedelta(days=float(konfig.wert("publikum.api_max_tage", 180))))
    vor = iso(zeit - timedelta(hours=float(konfig.wert("publikum.api_intervall_stunden", 6))))
    posts = con.execute("""SELECT p.* FROM posts p WHERE p.gepostet_utc>=? AND NOT EXISTS
                           (SELECT 1 FROM publikum_messungen m WHERE m.post_id=p.id AND m.quelle='api'
                            AND m.gemessen_utc>?)
                           ORDER BY COALESCE((SELECT MAX(m.gemessen_utc) FROM publikum_messungen m
                                              WHERE m.post_id=p.id AND m.quelle='api'),''),
                                    p.gepostet_utc DESC,p.id DESC LIMIT ?""",
                        (seit, vor, int(konfig.wert("publikum.api_max_posts", 100)))).fetchall()
    tokens = {}
    zugeordnet: dict[int, str] = {}
    ohne = [p for p in posts if p["plattform"] == "tiktok" and not p["video_id"]]
    if ohne:  # Kurzlink oder gar kein Link: selbst zuordnen, statt dich nach Links zu fragen
        try:
            tokens["tiktok"] = None
            tokens["tiktok"] = _token("tiktok", konfig, zeit)
            if tokens["tiktok"]:
                zugeordnet = _tiktok_zuordnen(con, konfig, tokens["tiktok"], ohne, zeit)
                ergebnis["zugeordnet"] = len(zugeordnet)
        except (AdapterFehler, ValueError, TypeError, KeyError, OSError) as exc:
            log.warning("Publikum-API tiktok, Zuordnung: %s", type(exc).__name__)
            ergebnis["fehler"] += 1
    for post in posts:
        plattform = post["plattform"]
        video_id = post["video_id"] or zugeordnet.get(int(post["id"]))
        if not video_id:
            ergebnis["ohne_id"] += 1
            continue
        if plattform not in ("tiktok", "youtube"):
            ergebnis["ohne_zugang"] += 1
            continue
        try:
            if plattform not in tokens:
                # Auch ein Fehler wird für diesen Lauf gemerkt, sonst erzeugt
                # jeder Post einen weiteren fehlgeschlagenen OAuth-Aufruf.
                tokens[plattform] = None
                tokens[plattform] = _token(plattform, konfig, zeit)
            token = tokens[plattform]
            if not token:
                ergebnis["ohne_zugang"] += 1
                continue
            if plattform == "tiktok":
                felder = "id,create_time,duration,view_count,like_count,comment_count,share_count"
                antwort = _json("https://open.tiktokapis.com/v2/video/query/?" + urlencode({"fields": felder}),
                                 token, daten={"filters": {"video_ids": [video_id]}})
                videos = (antwort.get("data") or {}).get("videos") or []
                if len(videos) != 1 or str(videos[0].get("id")) != video_id:
                    raise AdapterFehler("TikTok-Video nicht im autorisierten Account gefunden")
            else:
                query = {"ids": "channel==MINE", "startDate": aus_iso(post["gepostet_utc"]).date().isoformat(),
                         "endDate": zeit.date().isoformat(), "filters": f"video=={video_id}",
                         "metrics": "views,likes,comments,shares,averageViewDuration,averageViewPercentage,subscribersGained"}
                antwort = _json("https://youtubeanalytics.googleapis.com/v2/reports?" + urlencode(query), token)
            nummer = importiere(con, konfig, post["id"], antwort, zeit=zeit)
            ergebnis["gespeichert" if nummer is not None else "unveraendert"] += 1
        except (AdapterFehler, ValueError, TypeError, KeyError, OSError) as exc:
            # Nur Fehlerklasse loggen: auch ein fremdes Dateisystem-/JSON-Problem
            # darf keine Inhalte einer Credential-Datei im Log wiedergeben.
            log.warning("Publikum-API %s, Post #%s: %s", plattform, post["id"], type(exc).__name__)
            ergebnis["fehler"] += 1
    return ergebnis
