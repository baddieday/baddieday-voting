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
from datetime import datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from . import publikum
from .zeit import aus_iso, iso, jetzt

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


def _token(plattform: str, konfig, zeit: datetime) -> str | None:
    """Access-Token bzw. automatische Erneuerung; rotierte TikTok-Refresh-Tokens
    bleiben atomar in einer privaten Datei neben der DB (Linux 0600), nie im Repo.
    """
    prefix = plattform.upper()
    direkt = os.environ.get(f"{prefix}_ACCESS_TOKEN", "").strip()
    refresh = os.environ.get(f"{prefix}_REFRESH_TOKEN", "").strip()
    client_name = "CLIENT_KEY" if plattform == "tiktok" else "CLIENT_ID"
    client = os.environ.get(f"{prefix}_{client_name}", "").strip()
    secret = os.environ.get(f"{prefix}_CLIENT_SECRET", "").strip()
    if not (refresh and client and secret):
        return direkt or None
    # YouTube gibt beim Refresh keinen neuen Refresh-Token aus. Der langlebige
    # Token bleibt nur in .env; der kurzfristige Access-Token bleibt im Speicher.
    if plattform == "youtube":
        antwort = _json("https://oauth2.googleapis.com/token", daten={"grant_type": "refresh_token",
                         "client_id": client, "client_secret": secret, "refresh_token": refresh}, formular=True)
        if not antwort.get("access_token"):
            raise AdapterFehler("YouTube-Token konnte nicht erneuert werden")
        return str(antwort["access_token"])
    cache_pfad = konfig.datenbank.parent / "publikum-oauth.json"
    seed = hashlib.sha256(refresh.encode()).hexdigest()
    cache = {}
    if cache_pfad.is_file():
        try:
            cache = json.loads(cache_pfad.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise AdapterFehler("TikTok-Token-Datei ist nicht lesbar") from None
    # Andere App-Konfiguration darf keine alten Tokens verwenden.
    if cache.get("client_key") == client and cache.get("seed") == seed:
        refresh = cache.get("refresh_token") or refresh
        if cache.get("access_token") and float(cache.get("expires_at", 0)) > zeit.timestamp() + 300:
            return str(cache["access_token"])
    antwort = _json("https://open.tiktokapis.com/v2/oauth/token/", daten={"grant_type": "refresh_token",
                     "client_key": client, "client_secret": secret, "refresh_token": refresh}, formular=True)
    if not antwort.get("access_token"):
        raise AdapterFehler("TikTok-Token konnte nicht erneuert werden")
    neu = {"client_key": client, "seed": seed, "access_token": antwort["access_token"],
           "refresh_token": antwort.get("refresh_token") or refresh,
           "expires_at": zeit.timestamp() + float(antwort.get("expires_in", 0))}
    cache_pfad.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".publikum-oauth-", suffix=".tmp", dir=cache_pfad.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(neu, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, cache_pfad)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
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


def importiere(con, konfig, post_id: int, antwort: dict, *, zeit: datetime | None = None) -> int | None:
    """Eindeutig zugeordnete API-/Exportdaten speichern und automatisch lernen.

    Idempotenter Replay desselben Exports schreibt keine zweite Messung.
    Die Post-Plattform stammt aus der DB, nicht aus frei mitgelieferten Daten.
    """
    post = publikum.post(con, post_id)
    if post is None:
        raise ValueError(f"Post #{post_id} gibt es nicht")
    werte = normalisiere(post["plattform"], antwort)
    if not any(werte.get(feld) is not None for feld in publikum.MESSFELDER):
        return None
    letzte = con.execute("SELECT * FROM publikum_messungen WHERE post_id=? AND quelle='api'"
                         " ORDER BY gemessen_utc DESC,id DESC LIMIT 1", (post_id,)).fetchone()
    if letzte is not None and all(letzte[f] == werte.get(f) for f in publikum.MESSFELDER):
        if json.loads(letzte["metriken"] or "{}") == werte.get("metriken", {}):
            return None
    # API-Zähler dürfen von der Plattform korrigiert werden. Keine manuelle
    # Rückfrage wie bei OCR; vorangehende Messungen bleiben trotzdem erhalten.
    return publikum.speichere_messung(con, post_id, werte, "api", zeit=zeit, konfig=konfig,
                                     roh=json.dumps(werte, ensure_ascii=False, allow_nan=False))


def abrufen(con, konfig, zeit: datetime | None = None) -> dict:
    """Timer-Einstieg: vorhandene Posts automatisch aktualisieren, ohne Bewertung.

    Keine Tokens → kein Netz und expliziter Zähler. Die Plattformzugänge werden
    pro Lauf einmal erneuert. Ein defekter Zugang stoppt nicht andere Plattformen.
    """
    zeit = zeit or jetzt()
    ergebnis = dict(gespeichert=0, unveraendert=0, ohne_zugang=0, ohne_id=0, fehler=0)
    if not konfig.wert("publikum.api_abruf", True):
        ergebnis["deaktiviert"] = True
        return ergebnis
    seit = iso(zeit - timedelta(days=float(konfig.wert("publikum.api_max_tage", 180))))
    vor = iso(zeit - timedelta(hours=float(konfig.wert("publikum.api_intervall_stunden", 6))))
    posts = con.execute("""SELECT p.* FROM posts p WHERE p.gepostet_utc>=? AND NOT EXISTS
                           (SELECT 1 FROM publikum_messungen m WHERE m.post_id=p.id AND m.quelle='api'
                            AND m.gemessen_utc>?) ORDER BY p.gepostet_utc DESC,p.id DESC LIMIT ?""",
                        (seit, vor, int(konfig.wert("publikum.api_max_posts", 100)))).fetchall()
    tokens = {}
    for post in posts:
        plattform = post["plattform"]
        if not post["video_id"]:
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
                                 token, daten={"filters": {"video_ids": [post["video_id"]]}})
                videos = (antwort.get("data") or {}).get("videos") or []
                if len(videos) != 1 or str(videos[0].get("id")) != post["video_id"]:
                    raise AdapterFehler("TikTok-Video nicht im autorisierten Account gefunden")
            else:
                query = {"ids": "channel==MINE", "startDate": aus_iso(post["gepostet_utc"]).date().isoformat(),
                         "endDate": zeit.date().isoformat(), "filters": f"video=={post['video_id']}",
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
