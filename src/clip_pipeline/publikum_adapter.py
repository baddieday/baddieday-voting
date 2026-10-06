"""Offizielle Plattformzugänge und ein gemeinsames Metrikformat.

TikTok Display liefert keine Wiedergabezeit und kein favorites_count (Video-Felder: view_count, like_count,
comment_count, share_count – tiktok-api-fakten.md). Saves kommen nur aus Screenshot/Hand (A10: fehlend = 0).
YouTube Analytics benötigt einen autorisierten Kanal und yt-analytics.readonly. Instagram Insights kann importiert
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

from . import autonom, db, publikum
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
               "shares": "share_count"},
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


def _refresh_abgelehnt(fehler: AdapterFehler) -> bool:
    """Hat TikTok den Refresh fachlich abgelehnt (Anmeldung ungültig) – oder war nur die Verbindung gestört?
    Abgelehnt: HTTP 400/401/403 (invalid_grant, access_token_invalid, …) oder HTTP 200 mit error-Objekt („API
    abgelehnt: …“). NICHT abgelehnt: Netz/Timeout („API nicht erreichbar“), 429, 5xx, kein JSON – da hilft der
    nächste Lauf, nicht eine neue Anmeldung (Prüfer-Befund 06.10.: ein Netzaussetzer ist kein Widerruf).
    Beispiel: AdapterFehler("API HTTP 401: access_token_invalid") → True · AdapterFehler("API nicht erreichbar") → False."""
    text = str(fehler)
    return text.startswith("API abgelehnt") or text.startswith(("API HTTP 400", "API HTTP 401", "API HTTP 403"))


def _tiktok_erneuern(pfad: Path, cache: dict, client: str, secret: str, seed: str, refresh: str,
                     zeit: datetime) -> str:
    """Refresh-Aufruf und Cache schreiben (nur innerhalb der Sperre aufrufen). Übernommen werden open_id, scope,
    display_name und refresh_expires_at; scope und refresh_expires_at erneuert die Antwort, wenn sie sie enthält.
    AnmeldungUngueltig nur, wenn TikTok den Refresh ablehnt (_refresh_abgelehnt); Netz-/Serverfehler bleiben ein
    AdapterFehler („nicht erneuert“), die Anmeldung gilt weiter."""
    alt = cache if _passt(cache, client, seed) else {}
    refresh = alt.get("refresh_token") or refresh
    try:
        antwort = _json(TIKTOK_TOKEN_URL, daten={"grant_type": "refresh_token", "client_key": client,
                                                 "client_secret": secret, "refresh_token": refresh}, formular=True)
    except AdapterFehler as fehler:
        if _refresh_abgelehnt(fehler):
            raise AnmeldungUngueltig(f"TikTok-Anmeldung abgelaufen oder widerrufen ({fehler}) – im Lern-Bot /tiktok "
                                     "neu verbinden", code=fehler.code, grund=fehler.grund) from None
        # Verbindung gestört oder TikTok überlastet: Anmeldung bleibt gültig, der nächste Lauf versucht es wieder
        raise AdapterFehler(f"TikTok-Token nicht erneuert ({fehler}) – nächster Lauf versucht es wieder",
                            code=fehler.code, grund=fehler.grund) from None
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


class Zurueckgehalten(AdapterFehler):
    """Die Plattform liefert nur Nullen (Sandbox hält die Zähler zurück) – das ist keine Messung, nichts gespeichert."""


def importiere(con, konfig, post_id: int, antwort: dict, *, zeit: datetime | None = None) -> int | None:
    """Eindeutig zugeordnete API-/Exportdaten speichern und automatisch lernen (außer in autonom.gebuendelt()).

    Rückgabe: id der neuen Messung; None, wenn die Antwort keine Messfelder hat oder dieselben Werte wie die
    letzte API-Messung (idempotenter Replay desselben Exports schreibt keine zweite Messung).
    Zurueckgehalten (ein AdapterFehler), wenn alle Zähler (publikum.ZAEHLER) fehlen oder 0 sind: die Sandbox hält
    Zähler zurück – so eine „Messung“ würde eine Screenshot-Messung mit Wiedergabezeit verdrängen und den Score
    auf −2,5 einfrieren (B6). Gesunkene Zähler sind dagegen erlaubt (acd7fb1: die API darf nach unten korrigieren).
    ValueError bei unbekanntem Post. Die Post-Plattform stammt aus der DB, nicht aus frei mitgelieferten Daten.
    Beispiel: importiere(con, konfig, 17, {"data": {"videos": [{"id": "1", "view_count": 8000, …}]},
    "error": {"code": "ok"}}) → 42."""
    post = publikum.post(con, post_id)
    if post is None:
        raise ValueError(f"Post #{post_id} gibt es nicht")
    werte = normalisiere(post["plattform"], antwort)
    if not any(werte.get(feld) is not None for feld in publikum.MESSFELDER):
        return None
    if not any(werte.get(feld) for feld in publikum.ZAEHLER):
        raise Zurueckgehalten("TikTok hält die Zähler zurück (0 Views) – nicht gespeichert")
    letzte = con.execute("SELECT * FROM publikum_messungen WHERE post_id=? AND quelle='api'"
                         " ORDER BY gemessen_utc DESC,id DESC LIMIT 1", (post_id,)).fetchone()
    if letzte is not None and all(letzte[f] == werte.get(f) for f in publikum.MESSFELDER):
        if json.loads(letzte["metriken"] or "{}") == werte.get("metriken", {}):
            return None
    # API-Zähler dürfen von der Plattform korrigiert werden. Keine manuelle
    # Rückfrage wie bei OCR; vorangehende Messungen bleiben trotzdem erhalten.
    return publikum.speichere_messung(con, post_id, werte, "api", zeit=zeit, konfig=konfig,
                                     roh=json.dumps(werte, ensure_ascii=False, allow_nan=False))


TIKTOK_LISTE_URL = "https://open.tiktokapis.com/v2/video/list/"
LISTE_FELDER = "id,create_time,duration,share_url"
# Ein Video ohne Post wird erst gemeldet, wenn es so alt ist – sonst meldet der 10-Uhr-Lauf, was du vormittags noch abhakst
OHNE_POST_AB_S = 86400


def _tiktok_liste(token: str, cursor=None) -> dict:
    """Eine Seite der eigenen Videoliste (/v2/video/list/, Scope video.list, bis 20 Videos, neueste zuerst) – die
    einzige Stelle für diesen Aufruf. cursor: data.cursor der vorigen Seite (UTC-ms) für die nächste.
    Rückgabe: der data-Teil {"videos": [{id, create_time, duration, share_url}, …], "cursor": …, "has_more": bool}.
    AdapterFehler wie _json."""
    antwort = _json(TIKTOK_LISTE_URL + "?" + urlencode({"fields": LISTE_FELDER}), token,
                    daten={"max_count": 20, **({"cursor": cursor} if cursor else {})})
    return antwort.get("data") or {}


def _link_nr(post) -> str:
    """Die Nummer, die /link erwartet: im Lern-Bot die Entwurfsnummer, im Clip-Bot die Clip-Nummer."""
    return str(post["entwurf_id"] if post["art"] == "entwurf" else post["clip_id"])


def _tiktok_zuordnen(con, konfig, token: str, posts: list, zeit: datetime,
                     erste_seite: dict | None = None) -> tuple[dict[int, str], int]:
    """TikTok-Posts ohne Video-Nummer (kein Link oder Kurzlink vm.tiktok.com) selbst zuordnen (30.09., Spec §7.2):
    eigene Videoliste (_tiktok_liste; erste_seite = die schon in abrufen geholte Seite, weitere Seiten bis
    [publikum].zuordnung_seiten lädt nur die Zuordnung), je Post die Videos, die höchstens [publikum].zuordnung_stunden
    (72) vor dem Häkchen/Link bis 30 min danach erstellt wurden und deren Länge auf ±2 s passt. Genau eines → zugeordnet
    (video_id, und url nur, wenn noch keine steht). Zwei oder mehr → nicht raten (B10): der Post bleibt offen, zählt
    mehrdeutig, und du bekommst die Kandidaten. Ein Video gehört nie zu zwei Posts.
    Eine Lern-Meldung am Tag (publikum:zuordnung:<datum>) mit jeder Zuordnung („#17 → <share_url> (falsch? /link 41
    <Link>)“) und jedem Mehrdeutigen („Entwurf 41: 2 passende Videos (…) – /link 41 <Link>“) – nur, wenn es etwas
    zu sagen gibt. Rückgabe: ({post_id: video_id}, Anzahl mehrdeutig). AdapterFehler, wenn eine weitere Seite scheitert."""
    fenster = timedelta(hours=float(konfig.wert("publikum.zuordnung_stunden", 72)))
    frueheste = min(aus_iso(p["gepostet_utc"]) for p in posts) - fenster
    videos, cursor, daten = [], None, erste_seite
    for _ in range(int(konfig.wert("publikum.zuordnung_seiten", 10))):  # je 20 Videos, neueste zuerst
        if daten is None:
            daten = _tiktok_liste(token, cursor)
        seite = daten.get("videos") or []
        videos += seite
        cursor = daten.get("cursor")
        if not daten.get("has_more") or not seite or not cursor or \
                min(float(v.get("create_time") or 0) for v in seite) < frueheste.timestamp():
            break
        daten = None
    vergeben = {z[0] for z in con.execute("SELECT video_id FROM posts WHERE plattform = 'tiktok' AND video_id IS NOT NULL")}
    zugeordnet: dict[int, str] = {}
    mehrdeutig, zeilen = 0, []
    for post in sorted(posts, key=lambda z: z["gepostet_utc"]):
        gepostet = aus_iso(post["gepostet_utc"]).timestamp()
        passend = [v for v in videos if str(v.get("id") or "") and str(v["id"]) not in vergeben
                   and gepostet - fenster.total_seconds() <= float(v.get("create_time") or 0) <= gepostet + 1800
                   and abs(float(v.get("duration") or 0) - float(post["dauer_s"])) <= 2.0]
        if not passend:
            continue
        nr = _link_nr(post)
        if len(passend) > 1:
            mehrdeutig += 1
            links = ", ".join(str(v.get("share_url") or v["id"]) for v in passend)
            zeilen.append(f"{'Entwurf' if post['art'] == 'entwurf' else 'Clip'} {nr}: {len(passend)} passende Videos "
                          f"({links}) – /link {nr} <Link>")
            log.info("Publikum: Post #%s – %s passende TikTok-Videos, nicht zugeordnet", post["id"], len(passend))
            continue
        video = passend[0]
        vid = str(video["id"])
        con.execute("UPDATE posts SET video_id = ?, url = COALESCE(url, ?) WHERE id = ? AND video_id IS NULL",
                    (vid, video.get("share_url"), post["id"]))
        vergeben.add(vid)
        zugeordnet[int(post["id"])] = vid
        zeilen.append(f"#{post['id']} → {video.get('share_url') or vid} (falsch? /link {nr} <Link>)")
        log.info("Publikum: Post #%s ist TikTok-Video %s (per Zeit und Länge zugeordnet)", post["id"], vid)
    if zeilen:
        db.lern_meldung(con, f"publikum:zuordnung:{zeit:%Y-%m-%d}", "🔗 TikTok-Zuordnung:\n" + "\n".join(zeilen))
    return zugeordnet, mehrdeutig


def _tiktok_ohne_post(con, konfig, videos: list, zeit: datetime) -> int:
    """Videos der eigenen Liste, zu denen kein Post gehört (B21): keine posts.video_id (tiktok) gleich str(id),
    create_time innerhalb [publikum].zuordnung_stunden und mindestens OHNE_POST_AB_S alt. Je Video einmal eine
    Lern-Meldung publikum:ohne-post:<id> mit share_url und /link-Hinweis. Rückgabe: Anzahl solcher Videos
    (auch wenn die Meldung von einem früheren Lauf schon da ist). Nach der Zuordnung aufrufen – was gerade
    zugeordnet wurde, hat dann einen Post."""
    fenster_s = float(konfig.wert("publikum.zuordnung_stunden", 72)) * 3600
    bekannt = {z[0] for z in con.execute("SELECT video_id FROM posts WHERE plattform = 'tiktok' AND video_id IS NOT NULL")}
    anzahl = 0
    for video in videos:
        vid = str(video.get("id") or "")
        alter_s = zeit.timestamp() - float(video.get("create_time") or 0)
        if not vid or vid in bekannt or not (OHNE_POST_AB_S <= alter_s <= fenster_s):
            continue
        anzahl += 1
        link = str(video.get("share_url") or vid)
        db.lern_meldung(con, f"publikum:ohne-post:{vid}",
                        f"📎 TikTok-Video ohne Post ({int(float(video.get('duration') or 0))} s, vor "
                        f"{int(alter_s // 3600)} h): {link} – /link <entwurf> {link}")
    return anzahl




# --- Täglicher Abruf (Timer clip-publikum) ------------------------------------------------------------------------

TIKTOK_QUERY_URL = "https://open.tiktokapis.com/v2/video/query/"
QUERY_FELDER = "id,create_time,duration,view_count,like_count,comment_count,share_count"
BLOCK = 20                    # video/query nimmt höchstens 20 IDs je Anfrage (tiktok-api-fakten.md)
ABLAUF_VORWARNUNG_TAGE = 14   # so viele Tage vor dem Ende der Anmeldung kommt die Erinnerung
# Was ein Plattformaufruf oder Import werfen darf, ohne den Lauf zu beenden
API_FEHLER = (AdapterFehler, ValueError, TypeError, KeyError, OSError)


def _fehlertext(exc: BaseException) -> str:
    """Was ins Log und in die Meldung darf: der Text eines AdapterFehlers (per Konstruktion ohne Body, URL, Token).
    Von fremden Ausnahmen nur der Klassenname – auch ein Dateisystem-/JSON-Problem darf keine Inhalte einer
    Credential-Datei im Log wiedergeben."""
    return str(exc) if isinstance(exc, AdapterFehler) else type(exc).__name__


def _fehler(ergebnis: dict, exc: BaseException, vorlage: str, *args) -> None:
    """Einen fehlgeschlagenen Aufruf zählen und loggen (vorlage endet mit %s für den Fehlertext).
    Der erste AdapterFehler-Text wird ergebnis["grund"]; eine ungültige Anmeldung überschreibt ihn (sie ist die Ursache)."""
    log.warning(vorlage, *args, _fehlertext(exc))
    ergebnis["fehler"] += 1
    if isinstance(exc, AdapterFehler) and (ergebnis["grund"] is None or isinstance(exc, AnmeldungUngueltig)):
        ergebnis["grund"] = str(exc)


def _tiktok_token(konfig, zeit: datetime, ergebnis: dict) -> str | None:
    """_token("tiktok") für den Abruf: None trotz Key und Secret heißt „nicht verbunden“ (ergebnis["nicht_verbunden"],
    WARNING); AnmeldungUngueltig setzt ergebnis["zugang_ungueltig"] und fliegt weiter. Andere AdapterFehler fliegen
    unverändert."""
    try:
        token = _token("tiktok", konfig, zeit)
    except AnmeldungUngueltig:
        ergebnis["zugang_ungueltig"] = True
        raise
    if token is None and os.environ.get("TIKTOK_CLIENT_KEY", "").strip() \
            and os.environ.get("TIKTOK_CLIENT_SECRET", "").strip():
        ergebnis["nicht_verbunden"] = True
        log.warning("Publikum-API tiktok: nicht verbunden (/tiktok)")
    return token


def _tiktok_vorwarnung(con, konfig, zeit: datetime, ergebnis: dict) -> None:
    """B3: einmal je Lauf, unabhängig von fälligen Posts – Tage bis zum Ende der TikTok-Anmeldung ins Ergebnis
    (anmeldung_endet_tage), bei 0 ≤ Tage ≤ ABLAUF_VORWARNUNG_TAGE eine Lern-Meldung am Tag
    (publikum:tiktok-ablauf:<datum>). Nur für eine Anmeldung per /tiktok zur aktuellen App; unlesbare Token-Datei →
    überspringen (der Fehler kommt beim Token ohnehin)."""
    try:
        cache = lies_cache(cache_pfad(konfig))
    except AdapterFehler:
        return
    if cache.get("seed") != ANMELDUNG or cache.get("client_key") != os.environ.get("TIKTOK_CLIENT_KEY", "").strip():
        return
    tage = anmeldung_endet_tage(cache, zeit)
    ergebnis["anmeldung_endet_tage"] = tage
    if tage is not None and 0 <= tage <= ABLAUF_VORWARNUNG_TAGE:
        db.lern_meldung(con, f"publikum:tiktok-ablauf:{zeit:%Y-%m-%d}",
                        f"⏳ TikTok-Anmeldung läuft in {tage} Tagen ab – /tiktok neu verbinden")


def _importiere_post(con, konfig, post, antwort: dict, zeit: datetime, ergebnis: dict, plattform: str) -> None:
    """Eine Antwort für einen Post speichern und in genau einem Zähler verbuchen: gespeichert, unveraendert oder
    zurueckgehalten (nur Nullen, B6 – INFO, kein Fehler). Unbrauchbare Antwort (z. B. „Ungültige Metrik“) → fehler."""
    try:
        nummer = importiere(con, konfig, post["id"], antwort, zeit=zeit)
        ergebnis["gespeichert" if nummer is not None else "unveraendert"] += 1
    except Zurueckgehalten:
        ergebnis["zurueckgehalten"] += 1
        log.info("Publikum: Post #%s – Zähler zurückgehalten", post["id"])
    except API_FEHLER as exc:
        _fehler(ergebnis, exc, "Publikum-API %s, Post #%s: %s", plattform, post["id"])


def _tiktok_abrufen(con, konfig, tokens: dict, eintraege: list, zeit: datetime, ergebnis: dict) -> None:
    """Zähler der TikTok-Posts in Blöcken von BLOCK IDs über video/query (B7; eintraege = [(post, video_id), …]).
    Je Block eine Anfrage; die Antwort wird über str(id) den Posts zugeordnet und je Post wie eine Einzelantwort
    importiert (normalisiere bleibt unverändert). Fehlt eine ID in der Antwort (gelöscht/privat, B17): nicht_gefunden,
    WARNING und einmalig publikum:nicht-gefunden:<post_id> – kein fehler, der Post wird weiter täglich angefragt (eine
    billige Anfrage). Scheitert die Anfrage: fehler + 1, TikTok für den Lauf gesperrt (tokens["tiktok"] = None), die
    Posts des Blocks und alle restlichen zählen ohne_zugang. Kein Retry, kein time.sleep."""
    for anfang in range(0, len(eintraege), BLOCK):
        block = eintraege[anfang:anfang + BLOCK]
        try:
            if "tiktok" not in tokens:
                # Auch ein Fehler wird für diesen Lauf gemerkt, sonst erzeugt jeder Block einen weiteren OAuth-Aufruf.
                tokens["tiktok"] = None
                tokens["tiktok"] = _tiktok_token(konfig, zeit, ergebnis)
            token = tokens["tiktok"]
            if not token:
                ergebnis["ohne_zugang"] += len(block)
                continue
            antwort = _json(TIKTOK_QUERY_URL + "?" + urlencode({"fields": QUERY_FELDER}), token,
                            daten={"filters": {"video_ids": [vid for _, vid in block]}})
        except API_FEHLER as exc:
            _fehler(ergebnis, exc, "Publikum-API tiktok, Block ab Post #%s: %s", block[0][0]["id"])
            tokens["tiktok"] = None
            ergebnis["ohne_zugang"] += len(block)
            continue
        videos = {str(v.get("id")): v for v in ((antwort.get("data") or {}).get("videos") or []) if isinstance(v, dict)}
        for post, vid in block:
            video = videos.get(vid)
            if video is None:
                ergebnis["nicht_gefunden"] += 1
                log.warning("Publikum-API tiktok, Post #%s: Video nicht (mehr) auffindbar – gelöscht/privat? "
                            "/link korrigiert", post["id"])
                db.lern_meldung(con, f"publikum:nicht-gefunden:{post['id']}",
                                f"#{post['id']} ist auf TikTok nicht auffindbar – gelöscht? Sonst /link {_link_nr(post)} "
                                "<Link> korrigieren")
                continue
            _importiere_post(con, konfig, post, {"data": {"videos": [video]}, "error": {"code": "ok"}}, zeit, ergebnis,
                             "tiktok")


def _youtube_abrufen(con, konfig, tokens: dict, eintraege: list, zeit: datetime, ergebnis: dict) -> None:
    """YouTube-Analytics-Bericht je Post (eintraege = [(post, video_id), …]). Nach einem Fehler ist YouTube für den
    Lauf gesperrt – der fehlgeschlagene und alle weiteren Posts zählen ohne_zugang."""
    for post, video_id in eintraege:
        try:
            if "youtube" not in tokens:
                tokens["youtube"] = None
                tokens["youtube"] = _token("youtube", konfig, zeit)
            token = tokens["youtube"]
            if not token:
                ergebnis["ohne_zugang"] += 1
                continue
            query = {"ids": "channel==MINE", "startDate": aus_iso(post["gepostet_utc"]).date().isoformat(),
                     "endDate": zeit.date().isoformat(), "filters": f"video=={video_id}",
                     "metrics": "views,likes,comments,shares,averageViewDuration,averageViewPercentage,subscribersGained"}
            antwort = _json("https://youtubeanalytics.googleapis.com/v2/reports?" + urlencode(query), token)
        except API_FEHLER as exc:
            _fehler(ergebnis, exc, "Publikum-API youtube, Post #%s: %s", post["id"])
            tokens["youtube"] = None
            ergebnis["ohne_zugang"] += 1
            continue
        _importiere_post(con, konfig, post, antwort, zeit, ergebnis, "youtube")


def _rat(grund: str) -> str:
    """Was du bei diesem Fehler tun kannst – aus Code bzw. Text des ersten AdapterFehlers (die Texte aus _json tragen
    den Code: „API HTTP 401: access_token_invalid“)."""
    if "access_token_invalid" in grund or "HTTP 401" in grund:
        return "/tiktok neu verbinden"
    if "scope_not_authorized" in grund or "HTTP 403" in grund or grund == VIDEO_LIST_HINWEIS:
        return "Recht video.list und Sandbox-Target-User im Portal prüfen"
    if "rate_limit_exceeded" in grund or "HTTP 429" in grund:
        return "TikTok-Limit, morgen wieder"
    return "journalctl -u clip-publikum -n 50"


def _api_meldung(con, zeit: datetime, ergebnis: dict) -> bool:
    """Eine Lern-Meldung am Tag (publikum:api:<datum>; Präfix publikum: wartet die Ruhezeit ab –
    lernbot_publikum.LEISE_LERN_MELDUNGEN) über den Zustand des Abrufs, der erste zutreffende gewinnt:
      zugang_ungueltig → ⛔ mit dem Grund und /tiktok · nicht_verbunden → ℹ️ /tiktok · fehler → ⚠️ Anzahl, Grund,
      Posts ohne Zahlen und Rat (_rat). Zusatz bei zurueckgehalten: Sandbox liefert 0 Zähler.
    Still (False), wenn weder fehler noch nicht_verbunden noch zugang_ungueltig – Key/Secret fehlen ganz oder
    api_abruf = false ist still. Rückgabe: Meldung neu angelegt?"""
    grund = ergebnis["grund"] or "unbekannt"
    if ergebnis["zugang_ungueltig"]:
        # grund ist der Text der AnmeldungUngueltig – er nennt Ursache und Weg (/tiktok) schon
        text = f"⛔ {grund}"
    elif ergebnis["nicht_verbunden"]:
        text = "ℹ️ TikTok nicht verbunden – /tiktok im Lern-Bot, dann kommen die Zahlen von selbst"
    elif ergebnis["fehler"]:
        text = (f"⚠️ TikTok-Abruf: {ergebnis['fehler']} Fehler ({grund}), {ergebnis['ohne_zugang']} Posts ohne Zahlen – "
                f"{_rat(grund)}")
    else:
        return False
    if ergebnis["zurueckgehalten"]:
        text += f" · {ergebnis['zurueckgehalten']} Posts: Sandbox liefert 0 Zähler – Screenshot bleibt der Weg"
    return db.lern_meldung(con, f"publikum:api:{zeit:%Y-%m-%d}", text)


def abrufen(con, konfig, zeit: datetime | None = None) -> dict:
    """Timer-Einstieg (clip-publikum, täglich): Zahlen zu vorhandenen Posts holen, ohne Bewertung.

    Ablauf: fällige Posts (gepostet in den letzten [publikum].api_max_tage, letzte API-Messung älter als
    api_intervall_stunden, höchstens api_max_posts – unbewertete zuerst, B23) · Vorwarnung zum Ablauf der
    TikTok-Anmeldung (B3) · eigene Videoliste einmal (B21: Zuordnung ohne Link, Videos ohne Post) · TikTok-Zähler in
    Blöcken von BLOCK IDs (B7), YouTube einzeln · Importe gebündelt, einmal lernen am Ende (B22) · eine Lern-Meldung
    am Tag bei Problemen (B1/B5). Ohne Token kein Netz. Jeder Plattformzugang wird je Lauf einmal geholt; nach einem
    Fehler ist die Plattform für den Lauf gesperrt (kein Retry, kein Weiterhämmern) – ein defekter Zugang stoppt nicht
    die andere Plattform. Die DB ist autocommit; alle Lern-Meldungen des Abrufs entstehen hier.

    Rückgabe – immer alle Schlüssel, die JSON-Zeile bleibt stabil:
      Post-Zähler (jeder fällige Post zählt in genau einem; Ausnahme: ein Import mit unbrauchbarer Antwort zählt nur in
      fehler): gespeichert · unveraendert · zurueckgehalten (nur Nullen, B6) · nicht_gefunden (ID nicht in der
      Antwort, B17) · ohne_zugang (kein Token oder Plattform nach Fehler gesperrt) · ohne_id (kein Link, nicht zuordenbar)
      Zuordnung/Liste: zugeordnet · mehrdeutig (B10) · ohne_post (B21) · uebersprungen_intervall (noch nicht fällig, B12)
      fehler – fehlgeschlagene Anfragen (und Importe) · grund – Text des ersten AdapterFehlers, sonst None
      Meldungs-Schlüssel: anmeldung_endet_tage (int | None) · nicht_verbunden · zugang_ungueltig · meldung (Lern-Meldung
      publikum:api:<datum> neu angelegt) · deaktiviert ([publikum].api_abruf = false → alles 0/None/False).
    Beispiel: {"gespeichert": 12, "unveraendert": 3, "zurueckgehalten": 0, "nicht_gefunden": 1, "ohne_zugang": 0,
    "ohne_id": 2, "zugeordnet": 1, "mehrdeutig": 1, "ohne_post": 0, "uebersprungen_intervall": 40, "fehler": 0,
    "grund": None, "anmeldung_endet_tage": 211, "nicht_verbunden": False, "zugang_ungueltig": False,
    "meldung": False, "deaktiviert": False}."""
    zeit = zeit or jetzt()
    ergebnis = dict(gespeichert=0, unveraendert=0, zurueckgehalten=0, nicht_gefunden=0, ohne_zugang=0, ohne_id=0,
                    zugeordnet=0, mehrdeutig=0, ohne_post=0, uebersprungen_intervall=0, fehler=0, grund=None,
                    anmeldung_endet_tage=None, nicht_verbunden=False, zugang_ungueltig=False, meldung=False,
                    deaktiviert=False)
    if not konfig.wert("publikum.api_abruf", True):
        ergebnis["deaktiviert"] = True
        return ergebnis
    seit = iso(zeit - timedelta(days=float(konfig.wert("publikum.api_max_tage", 180))))
    vor = iso(zeit - timedelta(hours=float(konfig.wert("publikum.api_intervall_stunden", 6))))
    posts = con.execute("""SELECT p.* FROM posts p WHERE p.gepostet_utc>=? AND NOT EXISTS
                           (SELECT 1 FROM publikum_messungen m WHERE m.post_id=p.id AND m.quelle='api'
                            AND m.gemessen_utc>?)
                           ORDER BY (p.bewertet_utc IS NOT NULL),
                                    COALESCE((SELECT MAX(m.gemessen_utc) FROM publikum_messungen m
                                              WHERE m.post_id=p.id AND m.quelle='api'),''),
                                    p.gepostet_utc DESC,p.id DESC LIMIT ?""",
                        (seit, vor, int(konfig.wert("publikum.api_max_posts", 100)))).fetchall()
    ergebnis["uebersprungen_intervall"] = con.execute(
        """SELECT COUNT(*) FROM posts p WHERE p.gepostet_utc>=? AND EXISTS
           (SELECT 1 FROM publikum_messungen m WHERE m.post_id=p.id AND m.quelle='api' AND m.gemessen_utc>?)""",
        (seit, vor)).fetchone()[0]
    _tiktok_vorwarnung(con, konfig, zeit, ergebnis)
    tokens: dict[str, str | None] = {}
    zugeordnet: dict[int, str] = {}
    liste = None
    if con.execute("SELECT 1 FROM posts WHERE plattform = 'tiktok' AND gepostet_utc >= ? LIMIT 1", (seit,)).fetchone():
        # B21: die eigene Videoliste genau einmal je Lauf – für die Zuordnung und für Videos ohne Post. Ohne Token
        # kein Netz; scheitert der Abruf, bleibt TikTok für diesen Lauf gesperrt (kein Weiterhämmern).
        try:
            tokens["tiktok"] = None
            tokens["tiktok"] = _tiktok_token(konfig, zeit, ergebnis)
            if tokens["tiktok"]:
                liste = _tiktok_liste(tokens["tiktok"])
        except API_FEHLER as exc:
            _fehler(ergebnis, exc, "Publikum-API tiktok, Liste: %s")
            tokens["tiktok"] = None
    ohne = [p for p in posts if p["plattform"] == "tiktok" and not p["video_id"]]
    if ohne and tokens.get("tiktok"):  # Kurzlink oder gar kein Link: selbst zuordnen, statt dich nach Links zu fragen
        try:
            zugeordnet, ergebnis["mehrdeutig"] = _tiktok_zuordnen(con, konfig, tokens["tiktok"], ohne, zeit, liste)
            ergebnis["zugeordnet"] = len(zugeordnet)
        except API_FEHLER as exc:
            _fehler(ergebnis, exc, "Publikum-API tiktok, Zuordnung: %s")
            tokens["tiktok"] = None
    if liste is not None:
        ergebnis["ohne_post"] = _tiktok_ohne_post(con, konfig, liste.get("videos") or [], zeit)
    tiktok, youtube = [], []
    for post in posts:
        video_id = post["video_id"] or zugeordnet.get(int(post["id"]))
        if not video_id:
            ergebnis["ohne_id"] += 1
        elif post["plattform"] == "tiktok":
            tiktok.append((post, str(video_id)))
        elif post["plattform"] == "youtube":
            youtube.append((post, str(video_id)))
        else:
            ergebnis["ohne_zugang"] += 1
    with autonom.gebuendelt():   # B22: bis zu api_max_posts Importe, einmal lernen am Ende
        _tiktok_abrufen(con, konfig, tokens, tiktok, zeit, ergebnis)
        _youtube_abrufen(con, konfig, tokens, youtube, zeit, ergebnis)
    if ergebnis["gespeichert"]:
        autonom.aktualisieren(con, konfig)
    ergebnis["meldung"] = _api_meldung(con, zeit, ergebnis)
    return ergebnis
