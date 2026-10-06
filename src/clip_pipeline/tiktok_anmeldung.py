"""TikTok-Anmeldung für die Display API (Sandbox): Anmelde-Adresse bauen, Code gegen Tokens tauschen (30.09.),
Stand zeigen und trennen (06.10., P4).

Florian: „warum nicht, ich dachte du hast alles schon vorbereitet?!“ – die Spec (§7.2) sah /tiktok vor, gebaut war
nur das Erneuern vorhandener Tokens. Ablauf:
  1. /tiktok im Lern-Bot zeigt den Stand (status/status_text): verbunden als wer, Anmeldung hält bis wann, letzte
     API-Zahlen, Rechte. Nicht verbunden → dazu die Anmelde-Adresse (`pipeline publikum anmelden` ebenso).
  2. Adresse öffnen, mit dem TikTok-Konto zustimmen. TikTok landet auf der Rücksprung-Adresse
     ([tiktok].redirect_uri); die Seite muss nicht existieren – der Code steht in der Adresszeile.
  3. Diese Adresse (oder nur den Code) zurückschicken: /tiktok <adresse> bzw. `pipeline publikum anmelden --code …`.
     Der Tausch holt zusätzlich den Kontonamen (/v2/user/info/, Scope user.info.basic) – nur zur Anzeige.
  4. /tiktok trennen leert die Token-Datei (trennen), /tiktok neu holt eine frische Adresse.
Die Tokens landen nur in publikum-oauth.json neben der DB (0600) – nie im Chat, nie im Log, nie im Repo. Danach
erneuert publikum_adapter._token sie selbst (Access-Token 24 h, Refresh-Token etwa ein Jahr). .env braucht nur
TIKTOK_CLIENT_KEY und TIKTOK_CLIENT_SECRET.
Tausch und Erneuerung teilen sich die Sperre publikum-oauth.lock (sperre.sperre), damit nie zwei Schreiber die
Token-Datei gleichzeitig ersetzen (B20). Der Stand (status) braucht kein Netz: Token-Datei, Umgebung, Datenbank.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlencode

from . import publikum_adapter
from .konfig import Konfig
from .sperre import Gesperrt, sperre
from .zeit import UTC, aus_iso, iso, jetzt, utc_zu_lokal

AUTORISIEREN = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN = "https://open.tiktokapis.com/v2/oauth/token/"
# Kontoname nach dem Tausch – display_name braucht nur user.info.basic (tiktok-api-fakten.md, Abschnitt Nutzer)
KONTO = "https://open.tiktokapis.com/v2/user/info/?fields=open_id,display_name"
SCOPES = ("user.info.basic", "video.list")
REDIRECT = "https://clip-battle.de/tiktok/callback"
STATE = "clip-battle"
# So lange wartet der Tausch auf publikum-oauth.lock, wenn der Timer gerade den Token erneuert (Tests setzen 0)
SPERRE_WARTEN_S = 30
# Tokens, die noch in .env stehen können – nach dem Trennen nennt der Bot nur ihre Namen
ENV_TOKENS = ("TIKTOK_ACCESS_TOKEN", "TIKTOK_REFRESH_TOKEN")
ZUGANG_FEHLT = ("TIKTOK_CLIENT_KEY und TIKTOK_CLIENT_SECRET fehlen in /opt/clip-pipeline/.env – eintragen und den "
                "Bot neu starten")
VIDEO_LIST_FEHLT = ("⚠️ Das Recht video.list fehlt – ohne kommen keine Zahlen. Im Portal Display API/video.list "
                    "anhaken, dann /tiktok neu.")


class AnmeldeFehler(RuntimeError):
    """Für dich verständlicher Grund (ohne Tokens)."""


def _zugang() -> tuple[str, str]:
    client = os.environ.get("TIKTOK_CLIENT_KEY", "").strip()
    secret = os.environ.get("TIKTOK_CLIENT_SECRET", "").strip()
    if not (client and secret):
        raise AnmeldeFehler(ZUGANG_FEHLT)
    return client, secret


def redirect_uri(konfig: Konfig) -> str:
    return str(konfig.wert("tiktok.redirect_uri", REDIRECT) or REDIRECT)


def _sperre_pfad(konfig: Konfig) -> Path:
    """publikum-oauth.lock neben der Token-Datei – derselbe Pfad, den publikum_adapter._token beim Erneuern hält."""
    return publikum_adapter.cache_pfad(konfig).with_suffix(".lock")


def anmelde_url(konfig: Konfig) -> str:
    """Adresse zum Zustimmen im Browser (Scopes user.info.basic und video.list)."""
    client, _ = _zugang()
    return AUTORISIEREN + "?" + urlencode({"client_key": client, "scope": ",".join(SCOPES), "response_type": "code",
                                           "redirect_uri": redirect_uri(konfig), "state": STATE})


def code_aus(text: str) -> str:
    """Den Code aus der ganzen Rücksprung-Adresse, aus „code=…&…“ oder als reinen Code (URL-dekodiert)."""
    text = (text or "").strip()
    teil = text.split("?", 1)[1] if "?" in text else text
    werte = parse_qs(teil.split("#", 1)[0]) if "=" in teil else {}
    if "error" in werte:
        grund = (werte.get("error_description") or werte["error"])[0]
        raise AnmeldeFehler(f"TikTok meldet: {grund[:200]} – mit /tiktok neu anfangen")
    code = werte["code"][0] if "code" in werte else unquote(teil)
    if not code or any(z.isspace() for z in code) or len(code) > 1000 or "://" in code:
        raise AnmeldeFehler("Kein Code gefunden – schick die ganze Adresse, auf der TikTok gelandet ist")
    return code


def _tausch_fehler(fehler: publikum_adapter.AdapterFehler, konfig: Konfig) -> AnmeldeFehler:
    """Der Grund für dich, soweit TikTok ihn nennt (B2). Paket 1 hängt an AdapterFehler das Attribut `grund`
    (error_description) – hier nur gelesen, nie vorausgesetzt: ohne das Attribut bleibt der allgemeine Hinweis.
    „redirect“ → Rücksprung-Adresse im Portal, „client“ → Key/Secret in .env, sonst das Alter des Codes."""
    hinweis = f"{fehler} {getattr(fehler, 'grund', '')}".lower()
    if "redirect" in hinweis:
        return AnmeldeFehler(f"Tausch abgelehnt ({fehler}) – die Rücksprung-Adresse im Portal muss genau "
                             f"{redirect_uri(konfig)} sein")
    if "client" in hinweis:
        return AnmeldeFehler(f"Tausch abgelehnt ({fehler}) – TIKTOK_CLIENT_KEY/TIKTOK_CLIENT_SECRET in "
                             "/opt/clip-pipeline/.env prüfen")
    return AnmeldeFehler(f"Tausch abgelehnt ({fehler}) – der Code gilt nur wenige Minuten und nur einmal; "
                         "mit /tiktok neu eine neue Adresse holen")


def _kontoname(access_token: str) -> tuple[str | None, str | None]:
    """display_name über /v2/user/info/ – (Name, None) oder (None, "Kontoname nicht abrufbar"), wenn der Aufruf
    scheitert: die Anmeldung gilt trotzdem, der Stand zeigt dann die gekürzte open_id."""
    try:
        antwort = publikum_adapter._json(KONTO, access_token)
    except publikum_adapter.AdapterFehler:
        return None, "Kontoname nicht abrufbar"
    daten = antwort.get("data")
    nutzer = daten.get("user") if isinstance(daten, dict) else None
    name = nutzer.get("display_name") if isinstance(nutzer, dict) else None
    return (str(name) if name else None), None


def tausche(konfig: Konfig, text: str, zeit: datetime | None = None) -> dict:
    """Code → Tokens (in die private Token-Datei). Rückgabe ohne Tokens:
    {"scope", "video_list", "gueltig_bis", "refresh_tage", "konto"} und "hinweis", falls der Kontoname fehlt.
    Gespeichert werden zusätzlich refresh_expires_at (wann die Anmeldung endet, nur wenn TikTok refresh_expires_in
    liefert) und display_name – beides nur für den Stand (status).
    Tausch und Schreiben laufen unter der Sperre publikum-oauth.lock; hält der Timer sie gerade (Erneuerung),
    AnmeldeFehler „in einer Minute noch einmal“ – der Code ist dann noch nicht verbraucht.
    Fehler: AnmeldeFehler mit dem echten Grund (_tausch_fehler), ohne Tokens.
    Beispiel: tausche(konfig, "https://clip-battle.de/tiktok/callback?code=abc&state=clip-battle")["konto"]
    → "baddieday"."""
    zeit = zeit or jetzt()
    client, secret = _zugang()
    code = code_aus(text)
    try:
        with sperre(_sperre_pfad(konfig), warten_s=SPERRE_WARTEN_S):
            try:
                antwort = publikum_adapter._json(TOKEN, daten={"client_key": client, "client_secret": secret,
                                                              "code": code, "grant_type": "authorization_code",
                                                              "redirect_uri": redirect_uri(konfig)}, formular=True)
            except publikum_adapter.AdapterFehler as fehler:
                raise _tausch_fehler(fehler, konfig) from None
            if not antwort.get("access_token") or not antwort.get("refresh_token"):
                raise AnmeldeFehler("TikTok hat keine Tokens geliefert – mit /tiktok neu anfangen")
            scope = str(antwort.get("scope") or "")
            konto, hinweis = _kontoname(antwort["access_token"])
            daten = {"client_key": client, "seed": publikum_adapter.ANMELDUNG, "access_token": antwort["access_token"],
                     "refresh_token": antwort["refresh_token"],
                     "expires_at": zeit.timestamp() + float(antwort.get("expires_in", 0)),
                     "open_id": antwort.get("open_id"), "scope": scope, "display_name": konto}
            refresh_s = float(antwort.get("refresh_expires_in") or 0)
            if refresh_s > 0:
                daten["refresh_expires_at"] = zeit.timestamp() + refresh_s
            publikum_adapter.schreibe_cache(publikum_adapter.cache_pfad(konfig), daten)
    except Gesperrt:
        raise AnmeldeFehler("Der Timer erneuert gerade den Token – in einer Minute noch einmal /tiktok <adresse>") \
            from None
    ergebnis = {"scope": scope, "video_list": "video.list" in scope.split(","),
                "gueltig_bis": iso(zeit + timedelta(seconds=float(antwort.get("expires_in", 0)))),
                "refresh_tage": int(refresh_s // 86400), "konto": konto}
    if hinweis:
        ergebnis["hinweis"] = hinweis
    return ergebnis


def erfolg_text(e: dict) -> str:
    """Antwort nach dem Tausch: „✅ TikTok verbunden als baddieday (Rechte: …). …“ – ohne Kontoname ohne „als …“."""
    text = "✅ TikTok verbunden" + (f" als {e['konto']}" if e.get("konto") else "")
    text += f" (Rechte: {e['scope'] or '?'}). Die Zahlen holt der Timer clip-publikum täglich selbst"
    if e["refresh_tage"]:
        text += f"; die Anmeldung hält etwa {e['refresh_tage']} Tage"
    text += "."
    if not e["video_list"]:
        text += "\n" + VIDEO_LIST_FEHLT
    if e.get("hinweis"):
        text += f"\nℹ️ {e['hinweis']} – /tiktok zeigt stattdessen die Konto-Nummer."
    return text


# --- Stand und Trennen (06.10., B4/B16) ---------------------------------------------------------------------

def api_stand(con: sqlite3.Connection) -> dict:
    """Jüngste API-Messung und Zahl der Posts mit API-Zahlen – die eine Abfrage für /tiktok und die Kopfzeile von
    /publikum. Rückgabe {"letzte_abholung": iso-Zeit | None, "posts_mit_api": int}; ohne API-Messung (None, 0)."""
    letzte, posts = con.execute("SELECT MAX(gemessen_utc), COUNT(DISTINCT post_id) FROM publikum_messungen "
                                "WHERE quelle = 'api'").fetchone()
    return {"letzte_abholung": letzte, "posts_mit_api": int(posts or 0)}


def status(konfig: Konfig, con: sqlite3.Connection) -> dict:
    """Stand der Verbindung ohne Netz – aus Token-Datei, Umgebung und Datenbank. Rückgabe (nie Tokens):
      verbunden      Token-Datei trägt eine Anmeldung per /tiktok zum heutigen Client Key und einen Refresh-Token
      zugang         Key und Secret stehen in der Umgebung
      konto          display_name, sonst open_id gekürzt („a1b2c3…“), sonst None
      anmeldung_bis  iso-Zeit, bis zu der der Refresh-Token gilt (aus refresh_expires_at), sonst None
      scope, video_list, letzte_abholung, posts_mit_api (api_stand)
    Token-Datei kaputt (kein JSON oder kein Objekt) → {"verbunden": False, "fehler": "Token-Datei nicht lesbar"}.
    Wie viele Tage die Anmeldung noch hält, rechnet nur publikum_adapter (anmeldung_endet_tage, Paket 1) – hier
    steht nur das Datum. Beispiel nach tausche: {"verbunden": True, "konto": "baddieday", "posts_mit_api": 0, …}."""
    try:
        cache = publikum_adapter.lies_cache(publikum_adapter.cache_pfad(konfig))
    except publikum_adapter.AdapterFehler:
        cache = None
    if not isinstance(cache, dict):  # lies_cache wirft bei kaputtem JSON, reicht aber z. B. eine Liste durch
        return {"verbunden": False, "fehler": "Token-Datei nicht lesbar"}
    key = os.environ.get("TIKTOK_CLIENT_KEY", "").strip()
    zugang = bool(key and os.environ.get("TIKTOK_CLIENT_SECRET", "").strip())
    verbunden = (zugang and cache.get("seed") == publikum_adapter.ANMELDUNG and cache.get("client_key") == key
                 and bool(cache.get("refresh_token")))
    open_id = str(cache.get("open_id") or "")
    konto = cache.get("display_name") or (open_id[:6] + "…" if open_id else None)
    try:
        ablauf = float(cache.get("refresh_expires_at") or 0)
        anmeldung_bis = iso(datetime.fromtimestamp(ablauf, UTC)) if ablauf > 0 else None
    except (TypeError, ValueError, OverflowError, OSError):
        anmeldung_bis = None
    scope = str(cache.get("scope") or "")
    return {"verbunden": verbunden, "zugang": zugang, "konto": konto, "anmeldung_bis": anmeldung_bis,
            "scope": scope, "video_list": "video.list" in scope.split(","), **api_stand(con)}


def _datum(zeit_iso: str, konfig: Konfig, form: str) -> str:
    """Eine iso-Zeit als Datum in Ortszeit ([zeit].zeitzone), z. B. „05.10.2027“ oder „04.10.“."""
    return utc_zu_lokal(aus_iso(zeit_iso), konfig.wert("zeit.zeitzone", "Europe/Berlin")).strftime(form)


def status_text(s: dict, konfig: Konfig) -> str:
    """Der Stand (status) für den Chat. Verbunden:
      „✅ TikTok verbunden als baddieday · Anmeldung hält bis 05.10.2027 · letzte API-Zahlen: 04.10. (7 Posts) ·
       Rechte: user.info.basic,video.list⏎Neu verbinden: /tiktok neu · Trennen: /tiktok trennen“
    ohne Kontoname ohne „als …“, ohne Ablaufdatum „Anmeldung: Ablauf unbekannt“, ohne API-Messung „letzte
    API-Zahlen: noch keine“, ohne video.list dazu die ⚠️-Zeile. Nicht verbunden: „❌ TikTok nicht verbunden.“ (die
    Anleitung mit Adresse hängt lernbot_tiktok an); fehlen Key/Secret, steht hier die Abhilfe (.env, wie _zugang);
    kaputte Token-Datei → /tiktok trennen setzt sie zurück. Daten in Ortszeit."""
    if s.get("fehler"):
        return f"⚠️ TikTok: {s['fehler']} – /tiktok trennen setzt die Token-Datei zurück, dann /tiktok neu."
    if not s.get("zugang"):
        return f"❌ TikTok nicht verbunden – {ZUGANG_FEHLT}."
    if not s["verbunden"]:
        return "❌ TikTok nicht verbunden."
    teile = ["✅ TikTok verbunden" + (f" als {s['konto']}" if s.get("konto") else "")]
    if s.get("anmeldung_bis"):
        teile.append(f"Anmeldung hält bis {_datum(s['anmeldung_bis'], konfig, '%d.%m.%Y')}")
    else:
        teile.append("Anmeldung: Ablauf unbekannt")
    if s.get("letzte_abholung"):
        teile.append(f"letzte API-Zahlen: {_datum(s['letzte_abholung'], konfig, '%d.%m.')} ({s['posts_mit_api']} Posts)")
    else:
        teile.append("letzte API-Zahlen: noch keine")
    teile.append(f"Rechte: {s.get('scope') or '?'}")
    text = " · ".join(teile)
    if not s.get("video_list"):
        text += "\n" + VIDEO_LIST_FEHLT
    return text + "\nNeu verbinden: /tiktok neu · Trennen: /tiktok trennen"


def trennen(konfig: Konfig) -> dict:
    """Token-Datei leeren: schreibe_cache({}) – atomar, 0600 bleibt, nichts wird gelöscht. Danach gibt
    publikum_adapter._token None zurück, der Abruf zählt „ohne Zugang“.
    Kein Aufruf von /v2/oauth/revoke/: die Body-Felder dieses Endpunkts stehen nicht in den belegten API-Fakten
    (Annahme: den Zugriff entziehst du selbst in der TikTok-App; Fakten vor Erinnerung).
    Rückgabe {"getrennt": True, "env_tokens": [Namen aus ENV_TOKENS, die noch gesetzt sind]} – nur Namen, nie
    Werte: mit einem Refresh-Token in .env meldet sich der Abruf beim nächsten Lauf wieder an."""
    publikum_adapter.schreibe_cache(publikum_adapter.cache_pfad(konfig), {})
    return {"getrennt": True, "env_tokens": [name for name in ENV_TOKENS if os.environ.get(name, "").strip()]}


def trennen_text(e: dict) -> str:
    """Antwort nach trennen – mit Warnung, wenn .env noch Tokens trägt (nur die Namen)."""
    text = ("🔌 TikTok getrennt – die Token-Datei ist leer. Den Zugriff selbst entziehst du in der TikTok-App "
            "(Einstellungen → Sicherheit → Apps). Soll der Timer nicht täglich erinnern: [publikum].api_abruf = false "
            "in lokal.toml.")
    if e.get("env_tokens"):
        text += (f"\n⚠️ In .env steht noch {' und '.join(e['env_tokens'])} – damit meldet sich der Abruf beim "
                 "nächsten Lauf wieder an.")
    return text
