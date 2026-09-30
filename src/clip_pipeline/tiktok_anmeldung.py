"""TikTok-Anmeldung für die Display API (Sandbox): Anmelde-Adresse bauen, Code gegen Tokens tauschen (30.09.).

Florian: „warum nicht, ich dachte du hast alles schon vorbereitet?!“ – die Spec (§7.2) sah /tiktok vor, gebaut war
nur das Erneuern vorhandener Tokens. Ablauf:
  1. /tiktok im Lern-Bot (oder `pipeline publikum anmelden`) liefert die Anmelde-Adresse.
  2. Adresse öffnen, mit dem TikTok-Konto zustimmen. TikTok landet auf der Rücksprung-Adresse
     ([tiktok].redirect_uri); die Seite muss nicht existieren – der Code steht in der Adresszeile.
  3. Diese Adresse (oder nur den Code) zurückschicken: /tiktok <adresse> bzw. `pipeline publikum anmelden --code …`.
Die Tokens landen nur in publikum-oauth.json neben der DB (0600) – nie im Chat, nie im Log, nie im Repo. Danach
erneuert publikum_adapter._token sie selbst (Access-Token 24 h, Refresh-Token etwa ein Jahr). .env braucht nur
TIKTOK_CLIENT_KEY und TIKTOK_CLIENT_SECRET.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from urllib.parse import parse_qs, unquote, urlencode

from . import publikum_adapter
from .konfig import Konfig
from .zeit import iso, jetzt

AUTORISIEREN = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN = "https://open.tiktokapis.com/v2/oauth/token/"
SCOPES = ("user.info.basic", "video.list")
REDIRECT = "https://clip-battle.de/tiktok/callback"
STATE = "clip-battle"


class AnmeldeFehler(RuntimeError):
    """Für dich verständlicher Grund (ohne Tokens)."""


def _zugang() -> tuple[str, str]:
    client = os.environ.get("TIKTOK_CLIENT_KEY", "").strip()
    secret = os.environ.get("TIKTOK_CLIENT_SECRET", "").strip()
    if not (client and secret):
        raise AnmeldeFehler("TIKTOK_CLIENT_KEY und TIKTOK_CLIENT_SECRET fehlen in /opt/clip-pipeline/.env – "
                            "eintragen und den Bot neu starten")
    return client, secret


def redirect_uri(konfig: Konfig) -> str:
    return str(konfig.wert("tiktok.redirect_uri", REDIRECT) or REDIRECT)


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


def tausche(konfig: Konfig, text: str, zeit: datetime | None = None) -> dict:
    """Code → Tokens (in die private Token-Datei). Rückgabe ohne Tokens: {"scope", "gueltig_bis", "refresh_tage"}."""
    zeit = zeit or jetzt()
    client, secret = _zugang()
    code = code_aus(text)
    try:
        antwort = publikum_adapter._json(TOKEN, daten={"client_key": client, "client_secret": secret, "code": code,
                                                      "grant_type": "authorization_code",
                                                      "redirect_uri": redirect_uri(konfig)}, formular=True)
    except publikum_adapter.AdapterFehler as fehler:
        raise AnmeldeFehler(f"Tausch abgelehnt ({fehler}) – der Code gilt nur wenige Minuten und nur einmal; "
                            "mit /tiktok eine neue Adresse holen") from None
    if not antwort.get("access_token") or not antwort.get("refresh_token"):
        raise AnmeldeFehler("TikTok hat keine Tokens geliefert – mit /tiktok neu anfangen")
    scope = str(antwort.get("scope") or "")
    publikum_adapter.schreibe_cache(publikum_adapter.cache_pfad(konfig), {
        "client_key": client, "seed": publikum_adapter.ANMELDUNG, "access_token": antwort["access_token"],
        "refresh_token": antwort["refresh_token"], "expires_at": zeit.timestamp() + float(antwort.get("expires_in", 0)),
        "open_id": antwort.get("open_id"), "scope": scope})
    return {"scope": scope, "video_list": "video.list" in scope.split(","),
            "gueltig_bis": iso(zeit + timedelta(seconds=float(antwort.get("expires_in", 0)))),
            "refresh_tage": int(float(antwort.get("refresh_expires_in", 0)) // 86400)}


def erfolg_text(e: dict) -> str:
    text = f"✅ TikTok verbunden (Rechte: {e['scope'] or '?'}). Die Zahlen holt der Timer clip-publikum täglich selbst"
    if e["refresh_tage"]:
        text += f"; die Anmeldung hält etwa {e['refresh_tage']} Tage"
    text += "."
    if not e["video_list"]:
        text += "\n⚠️ Das Recht video.list fehlt – ohne kommen keine Zahlen. Im Portal Display API/video.list anhaken, " \
                "dann /tiktok noch einmal."
    return text
