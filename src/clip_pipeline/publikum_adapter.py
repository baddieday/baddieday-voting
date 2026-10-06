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
import re
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from . import publikum
from .sperre import Gesperrt, sperre
from .zeit import aus_iso, iso, jetzt

log = logging.getLogger("pipeline")

# Fehlercodes der Plattform (TikTok error.code wie access_token_invalid, OAuth error wie invalid_grant): nur so
# gebaute Codes landen im Fehlertext – alles andere heißt "unbekannt", damit nie ein Body-Fragment durchrutscht.
CODE_MUSTER = re.compile(r"[a-z_]{1,40}")
GRUND_MAX = 200
FEHLER_BODY_MAX = 65_536

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


def _fehlercode(code) -> str:
    """Ein Plattform-Fehlercode, wenn er wie einer aussieht (CODE_MUSTER), sonst "unbekannt"."""
    return code if isinstance(code, str) and CODE_MUSTER.fullmatch(code) else "unbekannt"


class AdapterFehler(RuntimeError):
    """Ein Plattformzugang hat nicht geliefert – der Text ist für Log und Lern-Bot gedacht.

    Nie Body, URL, Token oder log_id im Text: der Text wird geloggt und per Telegram gezeigt.
    Attribute:
      code  – Fehlercode der Plattform (TikTok error.code bzw. OAuth error), nur ^[a-z_]{1,40}$, sonst "unbekannt"
      grund – error.message bzw. error_description, höchstens GRUND_MAX druckbare Zeichen, sonst ""
    Der Konstruktor bleibt message-kompatibel: AdapterFehler("API HTTP 401") wie bisher (code "unbekannt", grund "").
    Beispiel: AdapterFehler("API HTTP 401: access_token_invalid", code="access_token_invalid", grund="The access
    token is invalid") → str(…) == "API HTTP 401: access_token_invalid", .code == "access_token_invalid"."""

    def __init__(self, text, *, code: str = "unbekannt", grund: str = ""):
        super().__init__(text)
        self.code = _fehlercode(code)
        grund = str(grund or "")[:GRUND_MAX]
        self.grund = grund if grund.isprintable() else ""


class _KeineWeiterleitung(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise AdapterFehler("API-Weiterleitung abgelehnt")


def _fehler_felder(wert) -> tuple[str, str]:
    """(code, beschreibung) aus einer Fehlerantwort: Display-Form {"error": {"code", "message"}} oder OAuth-Form
    {"error": "…", "error_description": "…"}; ("", "") wenn keine von beiden."""
    fehler = wert.get("error") if isinstance(wert, dict) else None
    if isinstance(fehler, dict):
        return str(fehler.get("code") or ""), str(fehler.get("message") or "")
    if isinstance(fehler, str):
        return fehler, str(wert.get("error_description") or "")
    return "", ""


def _fehler_aus_body(exc: HTTPError) -> tuple[str, str]:
    """Code und Beschreibung aus dem Body einer HTTP-Fehlerantwort (höchstens FEHLER_BODY_MAX Bytes);
    ("", "") bei leerem oder ungültigem Body."""
    if getattr(exc, "fp", None) is None:
        return "", ""
    try:
        return _fehler_felder(json.loads(exc.read(FEHLER_BODY_MAX)))
    except (OSError, ValueError, UnicodeError):
        return "", ""


def _json(url: str, token: str | None = None, *, daten=None, formular=False) -> dict:
    """Ein JSON-Aufruf gegen eine feste Adresse. AdapterFehler bei HTTP-Fehler („API HTTP 401: access_token_invalid“
    – Code und grund aus dem Body, wenn TikTok einen liefert), Netzfehler, Nicht-JSON und bei HTTP 200 mit
    error.code ≠ ok („API abgelehnt: scope_not_authorized“)."""
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
        code, grund = _fehler_aus_body(exc)
        code = _fehlercode(code)
        text = f"API HTTP {exc.code}" + (f": {code}" if code != "unbekannt" else "")
        raise AdapterFehler(text, code=code, grund=grund) from None
    except (URLError, TimeoutError, OSError):
        raise AdapterFehler("API nicht erreichbar") from None
    except (ValueError, UnicodeError):
        raise AdapterFehler("API liefert kein gültiges JSON") from None
    if not isinstance(wert, dict):
        raise AdapterFehler("API liefert kein Objekt")
    fehler = wert.get("error")
    if fehler and (not isinstance(fehler, dict) or fehler.get("code") not in (None, "ok")):
        code, grund = _fehler_felder(wert)
        code = _fehlercode(code)
        raise AdapterFehler(f"API abgelehnt: {code}", code=code, grund=grund)
    return wert


ANMELDUNG = "anmeldung"   # Seed einer Anmeldung per /tiktok (tiktok_anmeldung) – geht vor dem Refresh-Token aus .env
TIKTOK_TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
VIDEO_LIST_HINWEIS = ("Das Recht video.list fehlt – ohne kommen keine Zahlen. Im Portal Display API/video.list "
                      "anhaken, dann /tiktok noch einmal.")
ANMELDUNG_UNGUELTIG = "TikTok-Anmeldung abgelaufen oder widerrufen – im Lern-Bot /tiktok neu verbinden"
# Ein Access-Token gilt noch als frisch, wenn er länger als so viele Sekunden hält (sonst erneuern)
TOKEN_RESERVE_S = 300
# So lange wartet ein Lauf auf die Sperre, wenn gerade ein anderer (Bot oder Timer) den Token erneuert
SPERRE_WARTEN_S = 60


class AnmeldungUngueltig(AdapterFehler):
    """Die TikTok-Anmeldung ist abgelaufen oder widerrufen – ein Refresh hilft nicht mehr, nur /tiktok im Lern-Bot.
    Text: ANMELDUNG_UNGUELTIG, ggf. mit dem Grund des gescheiterten Refresh in Klammern."""


def anmeldung_endet_tage(cache: dict, zeit: datetime) -> int | None:
    """Volle Tage, bis der Refresh-Token (die Anmeldung) abläuft – die einzige Stelle mit dieser Formel:
    floor((refresh_expires_at − jetzt) / 86400). Negativ = abgelaufen. None, wenn der Cache kein
    refresh_expires_at hat (Anmeldungen vor dieser Änderung: keine Prüfung) oder der Wert keine Zahl ist.
    Beispiel: Ablauf in 10 Tagen und 5 Stunden → 10; vor einer Stunde → −1."""
    try:
        ablauf = float(cache["refresh_expires_at"])
    except (KeyError, TypeError, ValueError):
        return None
    return math.floor((ablauf - zeit.timestamp()) / 86400)


def cache_pfad(konfig) -> Path:
    """Private Token-Datei neben der DB (Linux 0600, Git ignoriert sie)."""
    return konfig.datenbank.parent / "publikum-oauth.json"


def lies_cache(pfad: Path) -> dict:
    """Die private Token-Datei als dict; fehlt sie, {}. AdapterFehler („TikTok-Token-Datei ist nicht lesbar“),
    wenn sie nicht lesbar, kein JSON oder kein Objekt ist (z. B. "[]") – die Datei bleibt unangetastet."""
    if not pfad.is_file():
        return {}
    try:
        wert = json.loads(pfad.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise AdapterFehler("TikTok-Token-Datei ist nicht lesbar") from None
    if not isinstance(wert, dict):
        raise AdapterFehler("TikTok-Token-Datei ist nicht lesbar")
    return wert


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


def _passt(cache: dict, client: str, seed: str) -> bool:
    """Gehört der Cache zu dieser App und diesem Refresh-Token? Andere App-Konfiguration darf keine alten Tokens
    verwenden."""
    return cache.get("client_key") == client and cache.get("seed") == seed


def _frischer_token(cache: dict, client: str, seed: str, zeit: datetime) -> str | None:
    """Der Access-Token aus dem Cache, wenn er zur App passt und noch länger als TOKEN_RESERVE_S gilt – sonst None."""
    if _passt(cache, client, seed) and cache.get("access_token") \
            and float(cache.get("expires_at", 0)) > zeit.timestamp() + TOKEN_RESERVE_S:
        return str(cache["access_token"])
    return None


def _tiktok_erneuern(pfad: Path, cache: dict, client: str, secret: str, seed: str, refresh: str,
                     zeit: datetime) -> str:
    """Refresh-Aufruf und Cache schreiben (nur innerhalb der Sperre aufrufen). Übernommen werden open_id, scope,
    display_name und refresh_expires_at; scope und refresh_expires_at erneuert die Antwort, wenn sie sie enthält.
    AnmeldungUngueltig, wenn TikTok den Refresh ablehnt (Token widerrufen/abgelaufen) oder der Aufruf scheitert."""
    alt = cache if _passt(cache, client, seed) else {}
    refresh = alt.get("refresh_token") or refresh
    try:
        antwort = _json(TIKTOK_TOKEN_URL, daten={"grant_type": "refresh_token", "client_key": client,
                                                 "client_secret": secret, "refresh_token": refresh}, formular=True)
    except AdapterFehler as fehler:
        raise AnmeldungUngueltig(f"TikTok-Anmeldung abgelaufen oder widerrufen ({fehler}) – im Lern-Bot /tiktok neu "
                                 "verbinden", code=fehler.code) from None
    if not antwort.get("access_token"):
        raise AdapterFehler("TikTok-Token konnte nicht erneuert werden")
    neu = {k: v for k, v in alt.items() if k in ("open_id", "scope", "display_name", "refresh_expires_at")}
    neu.update(client_key=client, seed=seed, access_token=antwort["access_token"],
               refresh_token=antwort.get("refresh_token") or refresh,
               expires_at=zeit.timestamp() + float(antwort.get("expires_in", 0)),
               scope=antwort.get("scope") or alt.get("scope"))
    if antwort.get("refresh_expires_in") is not None:
        neu["refresh_expires_at"] = zeit.timestamp() + float(antwort["refresh_expires_in"])
    schreibe_cache(pfad, neu)
    return str(antwort["access_token"])


def _token(plattform: str, konfig, zeit: datetime) -> str | None:
    """Access-Token der Plattform bzw. automatische Erneuerung; None, wenn kein Zugang konfiguriert ist (dann kein Netz).

    YouTube: Refresh-Token nur in .env, Access-Token je Aufruf neu (Google gibt beim Refresh keinen neuen Refresh-Token).
    TikTok: Eine Anmeldung per /tiktok (seed ANMELDUNG) reicht – .env braucht dann nur Key und Secret. Rotierte
    Refresh-Tokens liegen atomar in der privaten Datei neben der DB (0600), nie im Repo. Reihenfolge:
      (a) Anmeldung mit gespeichertem Scope ohne video.list → AdapterFehler(VIDEO_LIST_HINWEIS, code
          scope_not_authorized) ohne Netz (Tokens aus .env ohne gespeicherten Scope: keine Prüfung)
      (b) Anmeldung abgelaufen (anmeldung_endet_tage < 0) → AnmeldungUngueltig ohne Netz
      (c) Access-Token hält noch länger als TOKEN_RESERVE_S → zurück, ohne Sperre
      (d) sonst unter der Sperre publikum-oauth.lock (Bot und Timer erneuern nie gleichzeitig) den Cache noch einmal
          lesen – frisch → der; sonst Refresh und Cache schreiben. Sperre belegt → AdapterFehler („gerade erneuert“).
    Beispiel: _token("tiktok", konfig, jetzt()) → "act.…" oder None ohne Key/Secret."""
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
    if angemeldet:
        scope = str(cache.get("scope") or "")
        if scope and "video.list" not in scope.split(","):
            raise AdapterFehler(VIDEO_LIST_HINWEIS, code="scope_not_authorized")
        tage = anmeldung_endet_tage(cache, zeit)
        if tage is not None and tage < 0:
            raise AnmeldungUngueltig(ANMELDUNG_UNGUELTIG)
    seed = ANMELDUNG if angemeldet else hashlib.sha256(refresh.encode()).hexdigest()
    token = _frischer_token(cache, client, seed, zeit)
    if token:
        return token
    try:
        with sperre(pfad.with_suffix(".lock"), warten_s=SPERRE_WARTEN_S):
            cache = lies_cache(pfad)   # ein anderer Lauf war vielleicht schneller
            return _frischer_token(cache, client, seed, zeit) or _tiktok_erneuern(pfad, cache, client, secret, seed,
                                                                                   refresh, zeit)
    except Gesperrt:
        raise AdapterFehler("TikTok-Token wird gerade erneuert – später noch einmal") from None


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


def _tiktok_zuordnen(con, konfig, token: str, posts: list, zeit: datetime) -> dict[int, str]:
    """TikTok-Posts ohne Video-Nummer (kein Link oder Kurzlink vm.tiktok.com) selbst zuordnen (30.09., Spec §7.2):
    eigene Videoliste über /v2/video/list/ (Scope video.list), je Post das Video, das höchstens
    [publikum].zuordnung_stunden (72) vor dem Häkchen/Link bis 30 min danach erstellt wurde und dessen Länge auf
    ±2 s passt – das zeitlich nächste. Ein Video gehört nie zu zwei Posts. Trägt video_id (und url, falls leer) nach.
    Rückgabe: {post_id: video_id}."""
    fenster = timedelta(hours=float(konfig.wert("publikum.zuordnung_stunden", 72)))
    frueheste = min(aus_iso(p["gepostet_utc"]) for p in posts) - fenster
    videos, cursor = [], None
    for _ in range(int(konfig.wert("publikum.zuordnung_seiten", 10))):  # je 20 Videos, neueste zuerst
        antwort = _json("https://open.tiktokapis.com/v2/video/list/?" + urlencode(
            {"fields": "id,create_time,duration,share_url"}), token,
            daten={"max_count": 20, **({"cursor": cursor} if cursor else {})})
        daten = antwort.get("data") or {}
        seite = daten.get("videos") or []
        videos += seite
        cursor = daten.get("cursor")
        if not daten.get("has_more") or not seite or not cursor or \
                min(float(v.get("create_time") or 0) for v in seite) < frueheste.timestamp():
            break
    vergeben = {z[0] for z in con.execute("SELECT video_id FROM posts WHERE plattform = 'tiktok' AND video_id IS NOT NULL")}
    zugeordnet: dict[int, str] = {}
    for post in sorted(posts, key=lambda z: z["gepostet_utc"]):
        gepostet = aus_iso(post["gepostet_utc"]).timestamp()
        passend = [v for v in videos if str(v.get("id") or "") and str(v["id"]) not in vergeben
                   and gepostet - fenster.total_seconds() <= float(v.get("create_time") or 0) <= gepostet + 1800
                   and abs(float(v.get("duration") or 0) - float(post["dauer_s"])) <= 2.0]
        if not passend:
            continue
        video = min(passend, key=lambda v: abs(gepostet - float(v["create_time"])))
        vid = str(video["id"])
        con.execute("UPDATE posts SET video_id = ?, url = COALESCE(url, ?) WHERE id = ? AND video_id IS NULL",
                    (vid, video.get("share_url"), post["id"]))
        vergeben.add(vid)
        zugeordnet[int(post["id"])] = vid
        log.info("Publikum: Post #%s ist TikTok-Video %s (per Zeit und Länge zugeordnet)", post["id"], vid)
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
