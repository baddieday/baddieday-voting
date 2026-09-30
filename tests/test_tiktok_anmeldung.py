"""TikTok verbinden (30.09.): /tiktok bzw. `pipeline publikum anmelden` – Tokens nur in die private Token-Datei."""

import asyncio
import os
import stat
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from clip_pipeline import publikum_adapter, tiktok_anmeldung
from clip_pipeline.zeit import jetzt
from tests.hilfen import MitSpeicher

ZUGANG = {"TIKTOK_CLIENT_KEY": "key123", "TIKTOK_CLIENT_SECRET": "geheim", "TIKTOK_REFRESH_TOKEN": "",
          "TIKTOK_ACCESS_TOKEN": ""}
ANTWORT = {"access_token": "act.neu", "refresh_token": "rft.neu", "expires_in": 86400,
           "refresh_expires_in": 31536000, "open_id": "o1", "scope": "user.info.basic,video.list"}


class Anmeldung(MitSpeicher):
    def test_adresse_code_tausch_und_danach_selbst_erneuern(self):
        zeit = jetzt()
        with mock.patch.dict(os.environ, ZUGANG):
            url = tiktok_anmeldung.anmelde_url(self.konfig)
            self.assertIn("client_key=key123", url)
            self.assertIn("scope=user.info.basic%2Cvideo.list", url)
            self.assertIn("redirect_uri=https%3A%2F%2Fclip-battle.de%2Ftiktok%2Fcallback", url)
            rueck = "https://clip-battle.de/tiktok/callback?code=abc%2A%21x&scopes=user.info.basic&state=clip-battle"
            with mock.patch.object(publikum_adapter, "_json", return_value=ANTWORT) as api:
                e = tiktok_anmeldung.tausche(self.konfig, rueck, zeit)
            self.assertEqual(api.call_args.kwargs["daten"]["code"], "abc*!x")               # dekodiert
            self.assertEqual((e["video_list"], e["refresh_tage"]), (True, 365))
            self.assertNotIn("act.neu", str(e))                                            # keine Tokens zurück
            pfad = publikum_adapter.cache_pfad(self.konfig)
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(pfad.stat().st_mode), 0o600)
            # .env hat nur Key und Secret: der Adapter nimmt den gespeicherten Token …
            with mock.patch.object(publikum_adapter, "_json") as api:
                self.assertEqual(publikum_adapter._token("tiktok", self.konfig, zeit), "act.neu")
            api.assert_not_called()
            # … und erneuert ihn nach Ablauf selbst mit dem gespeicherten Refresh-Token
            with mock.patch.object(publikum_adapter, "_json", return_value={**ANTWORT, "access_token": "act.2"}) as api:
                self.assertEqual(publikum_adapter._token("tiktok", self.konfig, zeit + timedelta(days=2)), "act.2")
            self.assertEqual(api.call_args.kwargs["daten"]["refresh_token"], "rft.neu")

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


class ImBot(MitSpeicher):
    def test_tiktok_befehl(self):
        try:
            from clip_pipeline import lernbot_tiktok
        except ImportError:
            self.skipTest("python-telegram-bot fehlt")
        antworten = []

        async def antwort(text, **kw):
            antworten.append(text)

        def aufruf(*args):
            update = SimpleNamespace(effective_message=SimpleNamespace(reply_text=antwort))
            context = SimpleNamespace(bot_data={"konfig": self.konfig}, args=list(args))
            asyncio.run(lernbot_tiktok.cmd_tiktok(update, context))

        with mock.patch.dict(os.environ, ZUGANG):
            aufruf()
            with mock.patch.object(publikum_adapter, "_json", return_value=ANTWORT):
                aufruf("https://clip-battle.de/tiktok/callback?code=abc&state=clip-battle")
        self.assertIn("https://www.tiktok.com/v2/auth/authorize/?client_key=key123", antworten[0])
        self.assertTrue(antworten[1].startswith("✅ TikTok verbunden"))
        self.assertNotIn("act.neu", "".join(antworten))
