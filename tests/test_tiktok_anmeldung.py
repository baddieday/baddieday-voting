"""TikTok verbinden (30.09.): /tiktok bzw. `pipeline publikum anmelden` – Tokens nur in die private Token-Datei.
Seit 06.10. (P4): /tiktok zeigt einen Stand, kann trennen, der Code-Tausch nennt den echten Grund."""

import asyncio
import os
import stat
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from clip_pipeline import publikum, publikum_adapter, sperre, tiktok_anmeldung
from clip_pipeline.zeit import iso, jetzt, utc_zu_lokal
from tests.hilfen import MitSpeicher

ZUGANG = {"TIKTOK_CLIENT_KEY": "key123", "TIKTOK_CLIENT_SECRET": "geheim", "TIKTOK_REFRESH_TOKEN": "",
          "TIKTOK_ACCESS_TOKEN": ""}
ANTWORT = {"access_token": "act.neu", "refresh_token": "rft.neu", "expires_in": 86400,
           "refresh_expires_in": 31536000, "open_id": "o1", "scope": "user.info.basic,video.list"}
# zweite Antwort beim Tausch: /v2/user/info/ mit dem frischen Access-Token (Scope user.info.basic)
KONTO = {"data": {"user": {"open_id": "o1", "display_name": "baddieday"}}, "error": {"code": "ok"}}
AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/?client_key=key123"


class Anmeldung(MitSpeicher):
    def tausche(self, zeit=None):
        """Ein gelungener Tausch: Token-Antwort, dann der Kontoname."""
        rueck = "https://clip-battle.de/tiktok/callback?code=abc&state=clip-battle"
        with mock.patch.object(publikum_adapter, "_json", side_effect=[ANTWORT, KONTO]):
            return tiktok_anmeldung.tausche(self.konfig, rueck, zeit or jetzt())

    def test_adresse_code_tausch_und_danach_selbst_erneuern(self):
        zeit = jetzt()
        with mock.patch.dict(os.environ, ZUGANG):
            url = tiktok_anmeldung.anmelde_url(self.konfig)
            self.assertIn("client_key=key123", url)
            self.assertIn("scope=user.info.basic%2Cvideo.list", url)
            self.assertIn("redirect_uri=https%3A%2F%2Fclip-battle.de%2Ftiktok%2Fcallback", url)
            rueck = "https://clip-battle.de/tiktok/callback?code=abc%2A%21x&scopes=user.info.basic&state=clip-battle"
            with mock.patch.object(publikum_adapter, "_json", side_effect=[ANTWORT, KONTO]) as api:
                e = tiktok_anmeldung.tausche(self.konfig, rueck, zeit)
            self.assertEqual(api.call_args_list[0].kwargs["daten"]["code"], "abc*!x")       # dekodiert
            self.assertIn("/v2/user/info/", api.call_args_list[1].args[0])                 # Kontoname, ein Aufruf
            self.assertEqual(api.call_args_list[1].args[1], "act.neu")                     # … mit dem Access-Token
            self.assertEqual((e["video_list"], e["refresh_tage"], e["konto"]), (True, 365, "baddieday"))
            self.assertNotIn("act.neu", str(e))                                            # keine Tokens zurück
            self.assertNotIn("rft.neu", str(e))
            pfad = publikum_adapter.cache_pfad(self.konfig)
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(pfad.stat().st_mode), 0o600)
            cache = publikum_adapter.lies_cache(pfad)
            self.assertAlmostEqual(cache["refresh_expires_at"], (zeit + timedelta(days=365)).timestamp(), places=0)
            self.assertEqual(cache["display_name"], "baddieday")
            # .env hat nur Key und Secret: der Adapter nimmt den gespeicherten Token …
            with mock.patch.object(publikum_adapter, "_json") as api:
                self.assertEqual(publikum_adapter._token("tiktok", self.konfig, zeit), "act.neu")
            api.assert_not_called()
            # … und erneuert ihn nach Ablauf selbst mit dem gespeicherten Refresh-Token
            with mock.patch.object(publikum_adapter, "_json", return_value={**ANTWORT, "access_token": "act.2"}) as api:
                self.assertEqual(publikum_adapter._token("tiktok", self.konfig, zeit + timedelta(days=2)), "act.2")
            self.assertEqual(api.call_args.kwargs["daten"]["refresh_token"], "rft.neu")

    def test_erfolg_text_nennt_das_konto(self):
        with mock.patch.dict(os.environ, ZUGANG):
            e = self.tausche()
        self.assertTrue(tiktok_anmeldung.erfolg_text(e).startswith("✅ TikTok verbunden als baddieday (Rechte: "))
        self.assertTrue(tiktok_anmeldung.erfolg_text({**e, "konto": None}).startswith("✅ TikTok verbunden (Rechte: "))

    def test_kontoname_nicht_abrufbar_anmeldung_gilt_trotzdem(self):
        with mock.patch.dict(os.environ, ZUGANG):
            with mock.patch.object(publikum_adapter, "_json",
                                   side_effect=[ANTWORT, publikum_adapter.AdapterFehler("API HTTP 403")]):
                e = tiktok_anmeldung.tausche(self.konfig, "abc")
            self.assertIsNone(e["konto"])
            self.assertEqual(e["hinweis"], "Kontoname nicht abrufbar")
            self.assertTrue(e["video_list"])
            s = tiktok_anmeldung.status(self.konfig, self.con)
        self.assertTrue(s["verbunden"])
        self.assertEqual(s["konto"], "o1…")                                                # open_id gekürzt
        self.assertIn("Kontoname nicht abrufbar", tiktok_anmeldung.erfolg_text(e))

    def test_abgelehnter_code_und_fehlender_zugang(self):
        with mock.patch.dict(os.environ, ZUGANG):
            with self.assertRaisesRegex(tiktok_anmeldung.AnmeldeFehler, "TikTok meldet: access_denied"):
                tiktok_anmeldung.tausche(self.konfig, "https://clip-battle.de/tiktok/callback?error=access_denied")
            with mock.patch.object(publikum_adapter, "_json", side_effect=publikum_adapter.AdapterFehler("API HTTP 400")):
                with self.assertRaisesRegex(tiktok_anmeldung.AnmeldeFehler, "neue Adresse"):
                    tiktok_anmeldung.tausche(self.konfig, "abc")
        self.assertFalse(publikum_adapter.cache_pfad(self.konfig).exists())                # nichts gespeichert
        with mock.patch.dict(os.environ, {"TIKTOK_CLIENT_KEY": "", "TIKTOK_CLIENT_SECRET": ""}):
            with self.assertRaisesRegex(tiktok_anmeldung.AnmeldeFehler, ".env"):
                tiktok_anmeldung.anmelde_url(self.konfig)

    def test_tausch_nennt_den_echten_grund(self):
        # Paket 1 hängt an AdapterFehler das Attribut `grund` (error_description) – hier am Objekt nachgestellt
        falsche_adresse = publikum_adapter.AdapterFehler("API HTTP 400: invalid_request")
        falsche_adresse.grund = "Redirect_uri or scope is invalid."
        with mock.patch.dict(os.environ, ZUGANG):
            with mock.patch.object(publikum_adapter, "_json", side_effect=falsche_adresse):
                with self.assertRaises(tiktok_anmeldung.AnmeldeFehler) as cm:
                    tiktok_anmeldung.tausche(self.konfig, "abc")
            self.assertIn("Rücksprung-Adresse", str(cm.exception))
            self.assertIn(tiktok_anmeldung.redirect_uri(self.konfig), str(cm.exception))
            self.assertNotIn("neue Adresse", str(cm.exception))
            with mock.patch.object(publikum_adapter, "_json",
                                   side_effect=publikum_adapter.AdapterFehler("API HTTP 400: invalid_client")):
                with self.assertRaisesRegex(tiktok_anmeldung.AnmeldeFehler, r"\.env"):
                    tiktok_anmeldung.tausche(self.konfig, "abc")

    def test_tausch_waehrend_der_timer_erneuert(self):
        # publikum_adapter._token hält beim Erneuern dieselbe Sperre publikum-oauth.lock (Paket 1)
        lock = publikum_adapter.cache_pfad(self.konfig).with_suffix(".lock")
        with mock.patch.dict(os.environ, ZUGANG), mock.patch.object(tiktok_anmeldung, "SPERRE_WARTEN_S", 0), \
                sperre.sperre(lock, warten_s=0), mock.patch.object(publikum_adapter, "_json") as api:
            with self.assertRaisesRegex(tiktok_anmeldung.AnmeldeFehler, "gerade"):
                tiktok_anmeldung.tausche(self.konfig, "abc")
        api.assert_not_called()                                                            # kein Code verbraucht
        self.assertFalse(publikum_adapter.cache_pfad(self.konfig).exists())

    def test_status_ohne_netz(self):
        zeit = jetzt()
        with mock.patch.dict(os.environ, ZUGANG):
            vorher = tiktok_anmeldung.status(self.konfig, self.con)
            self.assertEqual((vorher["verbunden"], vorher.get("fehler")), (False, None))
            self.assertEqual(tiktok_anmeldung.status_text(vorher, self.konfig), "❌ TikTok nicht verbunden.")
            self.tausche(zeit)
            with mock.patch.object(publikum_adapter, "_json") as api:
                s = tiktok_anmeldung.status(self.konfig, self.con)
            api.assert_not_called()
            self.assertTrue(s["verbunden"])
            self.assertEqual((s["konto"], s["video_list"], s["posts_mit_api"], s["letzte_abholung"]),
                             ("baddieday", True, 0, None))
            self.assertTrue(s["anmeldung_bis"].startswith(iso(zeit + timedelta(days=365))[:10]))
            self.assertNotIn("act.", str(s))                                               # nie Tokens
            self.assertNotIn("rft.", str(s))
            text = tiktok_anmeldung.status_text(s, self.konfig)
            self.assertTrue(text.startswith("✅ TikTok verbunden als baddieday · Anmeldung hält bis "))
            self.assertIn(" · letzte API-Zahlen: noch keine · Rechte: user.info.basic,video.list\n", text)
            self.assertTrue(text.endswith("Neu verbinden: /tiktok neu · Trennen: /tiktok trennen"))
            self.assertNotIn("⚠️", text)
            # ohne Kontoname kein „als“, ohne Ablauf „unbekannt“, ohne video.list die Warnung
            text = tiktok_anmeldung.status_text({**s, "konto": None, "anmeldung_bis": None, "video_list": False},
                                                self.konfig)
            self.assertTrue(text.startswith("✅ TikTok verbunden · Anmeldung: Ablauf unbekannt · "))
            self.assertIn("⚠️ Das Recht video.list fehlt", text)
            # eine API-Messung → Datum (Ortszeit) und Zahl der Posts
            daten = {"dauer_s": 20.0, "rezept": publikum.rezept_fuer_clip(20.0), "merkmale": {}}
            post_id = publikum.post_anlegen(self.con, art="clip", ziel_id=1, plattform="tiktok", daten=daten,
                                            zeit=zeit - timedelta(days=1))[0]
            publikum.speichere_messung(self.con, post_id, {"views": 10}, "api", zeit=zeit)
            s = tiktok_anmeldung.status(self.konfig, self.con)
            self.assertEqual((s["letzte_abholung"], s["posts_mit_api"]), (iso(zeit), 1))
            self.konfig.daten["zeit"]["zeitzone"] = "Europe/Berlin"
            self.assertIn(f"letzte API-Zahlen: {utc_zu_lokal(zeit, 'Europe/Berlin'):%d.%m.} (1 Posts)",
                          tiktok_anmeldung.status_text(s, self.konfig))
        with mock.patch.dict(os.environ, {"TIKTOK_CLIENT_KEY": "", "TIKTOK_CLIENT_SECRET": ""}):
            s = tiktok_anmeldung.status(self.konfig, self.con)
            self.assertFalse(s["verbunden"])
            self.assertIn(".env", tiktok_anmeldung.status_text(s, self.konfig))

    def test_status_token_datei_nicht_lesbar(self):
        pfad = publikum_adapter.cache_pfad(self.konfig)
        with mock.patch.dict(os.environ, ZUGANG):
            for inhalt in ("[]", "{kaputt"):
                with self.subTest(inhalt=inhalt):
                    pfad.write_text(inhalt, encoding="utf-8")
                    s = tiktok_anmeldung.status(self.konfig, self.con)
                    self.assertFalse(s["verbunden"])
                    self.assertIn("nicht lesbar", s["fehler"])
                    self.assertIn("/tiktok trennen", tiktok_anmeldung.status_text(s, self.konfig))

    def test_trennen_wartet_auf_die_sperre(self):
        # Prüfer-Befund 06.10.: hält der Timer gerade die Sperre (Refresh), darf trennen nicht dazwischenschreiben
        from clip_pipeline.sperre import sperre
        with mock.patch.dict(os.environ, ZUGANG):
            self.tausche()
            pfad = publikum_adapter.cache_pfad(self.konfig)
            with sperre(pfad.with_suffix(".lock")), mock.patch.object(tiktok_anmeldung, "SPERRE_WARTEN_S", 0):
                with self.assertRaisesRegex(tiktok_anmeldung.AnmeldeFehler, "/tiktok trennen"):
                    tiktok_anmeldung.trennen(self.konfig)
            self.assertTrue(publikum_adapter.lies_cache(pfad).get("refresh_token"))       # nichts geleert

    def test_trennen(self):
        with mock.patch.dict(os.environ, ZUGANG):
            self.tausche()
            pfad = publikum_adapter.cache_pfad(self.konfig)
            with mock.patch.object(publikum_adapter, "_json") as api:
                e = tiktok_anmeldung.trennen(self.konfig)
            api.assert_not_called()                                                        # kein Revoke-Aufruf
            self.assertEqual(e, {"getrennt": True, "env_tokens": []})
            self.assertEqual(publikum_adapter.lies_cache(pfad), {})                        # leer, nicht gelöscht
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(pfad.stat().st_mode), 0o600)
            with mock.patch.object(publikum_adapter, "_json") as api:
                self.assertIsNone(publikum_adapter._token("tiktok", self.konfig, jetzt()))
            api.assert_not_called()
            self.assertFalse(tiktok_anmeldung.status(self.konfig, self.con)["verbunden"])
            text = tiktok_anmeldung.trennen_text(e)
            self.assertTrue(text.startswith("🔌 TikTok getrennt"))
            self.assertNotIn("⚠️", text)
        with mock.patch.dict(os.environ, {**ZUGANG, "TIKTOK_REFRESH_TOKEN": "rft.env"}):
            e = tiktok_anmeldung.trennen(self.konfig)
            self.assertEqual(e["env_tokens"], ["TIKTOK_REFRESH_TOKEN"])
            text = tiktok_anmeldung.trennen_text(e)
            self.assertIn("⚠️ In .env steht noch TIKTOK_REFRESH_TOKEN", text)
            self.assertNotIn("rft.env", text)                                              # nur Namen, nie Werte


class ImBot(MitSpeicher):
    def setUp(self):
        super().setUp()
        try:
            from clip_pipeline import lernbot_tiktok
        except ImportError:
            self.skipTest("python-telegram-bot fehlt")
        self.lernbot_tiktok = lernbot_tiktok
        self.antworten: list[str] = []

    def aufruf(self, *args) -> str:
        async def antwort(text, **kw):
            self.antworten.append(text)

        update = SimpleNamespace(effective_message=SimpleNamespace(reply_text=antwort))
        context = SimpleNamespace(bot_data={"konfig": self.konfig, "con": self.con}, args=list(args))
        asyncio.run(self.lernbot_tiktok.cmd_tiktok(update, context))
        return self.antworten[-1]

    def test_tiktok_befehl(self):
        with mock.patch.dict(os.environ, ZUGANG):
            ohne = self.aufruf()                                                           # /tiktok ohne Datei
            self.assertIn("❌", ohne)
            self.assertIn(AUTH_URL, ohne)
            with mock.patch.object(publikum_adapter, "_json", side_effect=[ANTWORT, KONTO]):
                verbunden = self.aufruf("https://clip-battle.de/tiktok/callback?code=abc&state=clip-battle")
            self.assertTrue(verbunden.startswith("✅ TikTok verbunden als baddieday"))
            stand = self.aufruf()                                                          # /tiktok zeigt den Stand
            self.assertIn("verbunden", stand)
            self.assertIn("als baddieday", stand)
            self.assertNotIn(AUTH_URL, stand)
            self.assertIn(AUTH_URL, self.aufruf("neu"))                                    # /tiktok neu
            with mock.patch.object(publikum_adapter, "_json") as api:
                getrennt = self.aufruf("trennen")                                          # /tiktok trennen
            api.assert_not_called()                                                        # nie als Code an TikTok
            self.assertIn("getrennt", getrennt)
            self.assertIn(AUTH_URL, self.aufruf())
        alles = "\n".join(self.antworten)
        self.assertNotIn("act.neu", alles)
        self.assertNotIn("rft.neu", alles)
