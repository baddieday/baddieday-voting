"""Lern-Bot: Publikumszahlen per Screenshot oder Hand-Eingabe (lernbot_zahlen.py, Spec §7.1, §13).

Fake-Telegram wie in tests/test_lernbot.py (FakeBot/FakeQuery von dort, hier nur erweitert um Knöpfe, Foto-Download
und Datei-Anhänge), gefälschtes `claude` aus tests/test_screenshot.py (FakeClaude). Die Zeit steht fest
(`lernbot_zahlen.jetzt` ersetzt), jeder Test hat seinen eigenen Temp-Ordner – so lässt sich zählen, ob ein Bild
liegen geblieben ist.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from clip_pipeline import big, konfig, publikum
from clip_pipeline.zeit import UTC, iso

from tests.test_screenshot import GUELTIG, FakeClaude, MitClaudeKonfig

try:
    import telegram  # noqa: F401

    from clip_pipeline import lernbot, lernbot_zahlen
    from tests.test_lernbot import FakeBot, FakeQuery
except ImportError:  # ohne python-telegram-bot laufen diese Tests nicht
    lernbot_zahlen = None
    FakeBot = FakeQuery = object

T = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
# Künstlich zusammengesetzt, kein echter Token – so steht er in keiner Datei am Stück
FAKE_TOKEN = "123456789:" + "AA" + "test" * 8 + "x"
FAKE_URL = f"https://api.telegram.org/file/bot{FAKE_TOKEN}/photos/file_7.jpg"


class Bot(FakeBot):
    """FakeBot aus test_lernbot, der zusätzlich die Knöpfe jeder Nachricht festhält."""

    def __init__(self):
        super().__init__()
        self.nachrichten: list[tuple[str, list | None]] = []

    async def send_message(self, chat, text, **kw):
        await super().send_message(chat, text, **kw)
        markup = kw.get("reply_markup")
        knoepfe = ([[(b.text, b.callback_data) for b in reihe] for reihe in markup.inline_keyboard]
                   if markup is not None else None)
        self.nachrichten.append((text, knoepfe))


class Klick(FakeQuery):
    """FakeQuery aus test_lernbot plus „Knöpfe unter der Nachricht entfernen“."""

    def __init__(self, daten, von=42):
        super().__init__(daten, von)
        self.knoepfe_entfernt = 0

    async def edit_message_reply_markup(self, reply_markup=None, **kw):
        self.knoepfe_entfernt += 1


class TelegramDatei:
    """Wie telegram.File nach get_file(): file_path ist die Download-URL MIT dem Bot-Token."""

    def __init__(self, inhalt: bytes = b"\xff\xd8\xff\xe0", fehler: BaseException | None = None):
        self.file_path, self.inhalt, self.fehler = FAKE_URL, inhalt, fehler

    async def download_to_drive(self, custom_path=None):
        if self.fehler is not None:
            raise self.fehler
        Path(custom_path).write_bytes(self.inhalt)
        return Path(custom_path)


class Anhang:
    """Foto (PhotoSize) oder Datei (Document) einer Nachricht."""

    def __init__(self, datei: TelegramDatei | None = None, mime_type: str | None = None,
                 file_name: str | None = None):
        self.datei, self.mime_type, self.file_name = datei or TelegramDatei(), mime_type, file_name

    async def get_file(self):
        return self.datei


@unittest.skipIf(lernbot_zahlen is None, "python-telegram-bot fehlt")
class MitLernBot(MitClaudeKonfig):
    def setUp(self):
        super().setUp()
        self.konfig.daten["publikum"]["plattformen"] = ["tiktok"]
        self.bot = Bot()
        self.app = SimpleNamespace(bot=self.bot, bot_data={"con": self.con, "konfig": self.konfig, "erlaubt": 42})
        self.context = SimpleNamespace(bot_data=self.app.bot_data, application=self.app, args=[])
        patcher = mock.patch.object(lernbot_zahlen, "jetzt", return_value=T)
        patcher.start()
        self.addCleanup(patcher.stop)
        self._entwurf = 40

    # --- Bausteine ---------------------------------------------------------------------------------------------

    def post(self, *, gepostet: datetime = T - timedelta(days=3), dauer_s: float = 20.0) -> int:
        """Ein Entwurfs-Post (Entwurf 41, 42, …)."""
        self._entwurf += 1
        daten = {"dauer_s": dauer_s, "rezept": publikum.rezept_fuer_clip(dauer_s), "merkmale": {}}
        post_id, _ = publikum.post_anlegen(self.con, art="entwurf", ziel_id=self._entwurf, plattform="tiktok",
                                           daten=daten, zeit=gepostet)
        return post_id

    def alte_messung(self, post_id: int, *, am: datetime = T - timedelta(days=1), **werte) -> None:
        publikum.speichere_messung(self.con, post_id, werte, "hand", zeit=am)

    def messungen(self, post_id: int) -> list:
        return self.con.execute("SELECT * FROM publikum_messungen WHERE post_id = ? ORDER BY id",
                                (post_id,)).fetchall()

    def foto(self, caption: str | None = None, *, anhang: Anhang | None = None, als_datei: bool = False,
             von: int = 42) -> None:
        anhang = anhang or Anhang()
        nachricht = SimpleNamespace(photo=() if als_datei else (Anhang(), anhang), document=anhang if als_datei else None,
                                    caption=caption, text=None, from_user=SimpleNamespace(id=von))
        asyncio.run(lernbot_zahlen.bei_foto(SimpleNamespace(effective_message=nachricht, callback_query=None),
                                            self.context))

    def text(self, text: str) -> None:
        nachricht = SimpleNamespace(photo=(), document=None, caption=None, text=text,
                                    from_user=SimpleNamespace(id=42))
        asyncio.run(lernbot_zahlen.bei_text(SimpleNamespace(effective_message=nachricht, callback_query=None),
                                            self.context))

    def klick(self, daten: str, von: int = 42) -> Klick:
        q = Klick(daten, von)
        asyncio.run(lernbot_zahlen.bei_klick(SimpleNamespace(callback_query=q, effective_message=None),
                                             self.context))
        return q

    def letzte(self) -> str:
        return self.bot.nachrichten[-1][0]

    def alle_texte(self) -> str:
        return "\n".join(text for text, _ in self.bot.nachrichten)

    def vorgang(self) -> dict | None:
        return self.app.bot_data.get("publikum")

    def claude_zaehler(self) -> list[str]:
        return [z["text"] for z in self.con.execute("SELECT text FROM ereignisse WHERE art = 'claude' ORDER BY id")]


# --- Reine Hilfsfunktionen (ohne Telegram) --------------------------------------------------------------------

@unittest.skipIf(lernbot_zahlen is None, "python-telegram-bot fehlt")
class Hilfen(unittest.TestCase):
    def test_post_nummer_aus_text(self):
        self.assertEqual(lernbot_zahlen.post_nummer_aus_text("Stand Tag 3 #17"), 17)
        self.assertEqual(lernbot_zahlen.post_nummer_aus_text("#5"), 5)
        for text in (None, "", "ohne Nummer", "#episch", "# 17"):
            self.assertIsNone(lernbot_zahlen.post_nummer_aus_text(text))

    def test_lies_text_eingabe(self):
        nr, werte = lernbot_zahlen.lies_text_eingabe("#17 1240 61 6.8 34")
        self.assertEqual(nr, 17)
        self.assertEqual(werte, publikum.lies_hand_eingabe("1240 61 6.8 34"))
        self.assertEqual(lernbot_zahlen.lies_text_eingabe("  #3 1240 61 – 34")[1]["wiedergabe_s"], None)
        self.assertIsNone(lernbot_zahlen.lies_text_eingabe("1240 61 6.8 34"))  # ohne #Nummer: kein Messungs-Text
        self.assertIsNone(lernbot_zahlen.lies_text_eingabe("Hallo #17"))       # Nummer nicht am Anfang
        with self.assertRaisesRegex(ValueError, "genau vier Werte"):
            lernbot_zahlen.lies_text_eingabe("#17 1240 61")
        with self.assertRaisesRegex(ValueError, "genau vier Werte"):
            lernbot_zahlen.lies_text_eingabe("#17")

    def test_knoepfe_posts(self):
        posts = [{"id": 17, "art": "entwurf", "clip_id": None, "entwurf_id": 41, "plattform": "tiktok",
                  "gepostet_utc": "2026-09-25T22:30:00.000Z"},
                 {"id": 9, "art": "clip", "clip_id": 5, "entwurf_id": None, "plattform": "youtube",
                  "gepostet_utc": "2026-09-20T10:00:00.000Z"}]
        self.assertEqual(lernbot_zahlen.knoepfe_posts(posts),
                         [[("#17 · Entwurf 41 · TikTok · 25.09.", "pl:17:")],
                          [("#9 · Clip 5 · YouTube Shorts · 20.09.", "pl:9:")]])
        # Datum in Ortszeit: 22:30 UTC ist in Berlin schon der 26.
        self.assertEqual(lernbot_zahlen.knoepfe_posts(posts[:1], zeitzone="Europe/Berlin")[0][0][0],
                         "#17 · Entwurf 41 · TikTok · 26.09.")
        self.assertEqual(lernbot_zahlen.knoepfe_posts([]), [])

    def test_knoepfe_rueckfrage(self):
        self.assertEqual(lernbot_zahlen.knoepfe_rueckfrage(17),
                         [[("✅ Stimmt", "pm:17:ok"), ("✏️ Von Hand", "pm:17:hand")]])

    def test_callback_daten_hoechstens_64_byte(self):
        riesig = 10 ** 18  # weit mehr Posts, als es je geben wird
        daten = [d for reihe in lernbot_zahlen.knoepfe_rueckfrage(riesig) for _, d in reihe]
        daten += [d for reihe in lernbot_zahlen.knoepfe_posts(
            [{"id": riesig, "art": "clip", "clip_id": riesig, "entwurf_id": None, "plattform": "tiktok",
              "gepostet_utc": "2026-09-25T10:00:00.000Z"}]) for _, d in reihe]
        for d in daten:
            self.assertLessEqual(len(d.encode("utf-8")), 64, d)
            self.assertRegex(d, lernbot_zahlen.KLICK_MUSTER)

    def test_parse(self):
        self.assertEqual(lernbot_zahlen.parse("pl:17:"), ("pl", 17, ""))
        self.assertEqual(lernbot_zahlen.parse("pm:17:ok"), ("pm", 17, "ok"))
        self.assertEqual(lernbot_zahlen.parse("pm:17:hand"), ("pm", 17, "hand"))
        for falsch in ("pm:17:", "pm:17:ja", "pl:17:ok", "pl:x:", "pl:17", "d:17:1", "", None):
            with self.subTest(falsch=falsch), self.assertRaises(ValueError):
                lernbot_zahlen.parse(falsch)

    def test_werte_text(self):
        self.assertEqual(lernbot_zahlen.werte_text(GUELTIG), "👁 1 240 · ❤️ 61 · 💬 3 · ↗️ 5 · 🔖 2 · ⏱ 6,8 s · ✅ 34 %")
        leer = {feld: None for feld in publikum.FELDER}
        self.assertEqual(lernbot_zahlen.werte_text(leer), "👁 – · ❤️ – · 💬 – · ↗️ – · 🔖 – · ⏱ – · ✅ –")
        self.assertEqual(lernbot_zahlen.werte_text({**leer, "views": 1234567, "voll_prozent": 34.5}),
                         "👁 1 234 567 · ❤️ – · 💬 – · ↗️ – · 🔖 – · ⏱ – · ✅ 34,5 %")

    def test_endung_fuer(self):
        self.assertEqual(lernbot_zahlen.endung_fuer("image/png", "x.png"), ".png")
        self.assertEqual(lernbot_zahlen.endung_fuer("image/jpeg", "x.JPEG"), ".jpg")
        self.assertEqual(lernbot_zahlen.endung_fuer("image/webp", None), ".webp")
        self.assertEqual(lernbot_zahlen.endung_fuer(None, "Bild.JPEG"), ".jpg")  # ohne mime_type: Dateiname
        self.assertEqual(lernbot_zahlen.endung_fuer(None, "bild.png"), ".png")
        self.assertIsNone(lernbot_zahlen.endung_fuer("image/heic", "IMG_1.HEIC"))
        self.assertIsNone(lernbot_zahlen.endung_fuer("image/heic", "tarnung.jpg"))  # der mime_type zählt
        self.assertIsNone(lernbot_zahlen.endung_fuer(None, "IMG_1.heic"))
        self.assertIsNone(lernbot_zahlen.endung_fuer(None, None))


# --- Screenshot-Fluss -------------------------------------------------------------------------------------------

class Screenshot(MitLernBot):
    def test_foto_mit_nummer_wird_gelesen_und_gespeichert(self):
        pid = self.post()
        fake = FakeClaude()
        with fake.aktiv():
            self.foto(f"Stand Tag 3 #{pid}")
        (m,) = self.messungen(pid)
        self.assertEqual((m["quelle"], m["views"], m["likes"], m["wiedergabe_s"], m["voll_prozent"]),
                         ("screenshot", 1240, 61, 6.8, 34.0))
        self.assertEqual(m["roh"], json.dumps(GUELTIG))  # die Claude-Antwort für Nachprüfungen
        self.assertEqual(m["gemessen_utc"], iso(T))
        self.assertIn(f"💾 #{pid} gespeichert (Screenshot): 👁 1 240 · ❤️ 61", self.letzte())
        self.assertEqual(fake.aufrufe[0]["dateien"], ["screenshot.jpg"])  # das größte Foto, als JPEG
        self.assertEqual(self.temp_reste(), [])   # Bild nach der Auswertung gelöscht
        self.assertIsNone(self.vorgang())
        self.assertEqual(self.claude_zaehler(), ["screenshot: ok"])

    def test_ohne_nummer_knoepfe_dann_klick(self):
        gemessen = self.post(gepostet=T - timedelta(days=4))
        self.alte_messung(gemessen, am=T - timedelta(hours=2), views=10)
        aelter = self.post(gepostet=T - timedelta(days=3))
        neu = self.post(gepostet=T - timedelta(days=1))
        fake = FakeClaude()
        with fake.aktiv():
            self.foto(None)
        text, knoepfe = self.bot.nachrichten[-1]
        self.assertIn("Zu welchem Post", text)
        # jüngster zuerst; der vor 2 h gemessene Post fehlt
        self.assertEqual(knoepfe, [[(f"#{neu} · Entwurf 43 · TikTok · 25.09.", f"pl:{neu}:")],
                                   [(f"#{aelter} · Entwurf 42 · TikTok · 23.09.", f"pl:{aelter}:")]])
        self.assertEqual(fake.aufrufe, [])
        self.assertEqual(len(self.temp_reste()), 1)  # das Bild wartet

        with fake.aktiv():
            q = self.klick(f"pl:{aelter}:")
        self.assertEqual(q.antworten, ["Ich lese die Zahlen …"])
        self.assertEqual(q.knoepfe_entfernt, 1)
        self.assertEqual(len(self.messungen(aelter)), 1)
        self.assertEqual(self.messungen(neu), [])
        self.assertEqual(self.temp_reste(), [])
        self.assertEqual(self.klick(f"pl:{neu}:").antworten, ["Schon erledigt."])  # Doppelklick / alter Knopf

    def test_ohne_nummer_und_ohne_offenen_post(self):
        pid = self.post()
        self.alte_messung(pid, am=T - timedelta(hours=1), views=10)
        self.foto("Screenshot von heute")
        self.assertIn("keinen Post ohne Messung", self.letzte())
        self.assertEqual(self.temp_reste(), [])
        self.assertIsNone(self.vorgang())

    def test_unbekannte_post_nummer(self):
        with FakeClaude().aktiv() as (lauf, _):
            self.foto("#999")
        self.assertIn("Post #999 kenne ich nicht", self.letzte())
        lauf.assert_not_called()
        self.assertEqual(self.temp_reste(), [])

    def test_unplausibel_rueckfrage_dann_bestaetigt(self):
        pid = self.post()
        self.alte_messung(pid, views=2000, likes=50)
        with FakeClaude().aktiv():
            self.foto(f"#{pid}")
        self.assertEqual(len(self.messungen(pid)), 1)  # nichts ungeprüft gespeichert
        text, knoepfe = self.bot.nachrichten[-1]
        self.assertIn("👁 1 240", text)
        self.assertIn("Views gesunken: 2000 → 1240", text)
        self.assertIn("Stimmt das?", text)
        self.assertEqual(knoepfe, lernbot_zahlen.knoepfe_rueckfrage(pid))
        self.assertEqual(self.temp_reste(), [])  # das Bild ist schon weg, die Zahlen warten

        q = self.klick(f"pm:{pid}:ok")
        self.assertEqual(q.antworten, ["Gespeichert."])
        self.assertEqual(q.knoepfe_entfernt, 1)
        neu = self.messungen(pid)[-1]
        self.assertEqual((neu["quelle"], neu["views"], neu["roh"]), ("screenshot", 1240, json.dumps(GUELTIG)))
        self.assertIn(f"💾 #{pid} gespeichert (Screenshot)", self.letzte())
        self.assertEqual(self.klick(f"pm:{pid}:ok").antworten, ["Schon erledigt."])  # Doppelklick
        self.assertEqual(len(self.messungen(pid)), 2)

    def test_rueckfrage_dann_von_hand(self):
        pid = self.post()
        self.alte_messung(pid, views=2000)
        with FakeClaude().aktiv():
            self.foto(f"#{pid}")
        q = self.klick(f"pm:{pid}:hand")
        self.assertEqual(q.antworten, ["Dann bitte von Hand."])
        self.assertIn(f"✏️ Bitte die Zahlen für #{pid} von Hand", self.letzte())
        self.assertEqual(self.klick(f"pm:{pid}:ok").antworten, ["Schon erledigt."])  # die Rückfrage ist vorbei
        self.text("2100 70 6,8 34")
        neu = self.messungen(pid)[-1]
        self.assertEqual((neu["quelle"], neu["views"], neu["likes"], neu["roh"]), ("hand", 2100, 70, None))

    def test_zahlen_direkt_statt_knopf_bei_rueckfrage(self):
        pid = self.post()
        self.alte_messung(pid, views=2000)
        with FakeClaude().aktiv():
            self.foto(f"#{pid}")
        self.text("2100 70 6,8 34")  # statt ✏️ gleich die richtigen Zahlen
        self.assertEqual(self.messungen(pid)[-1]["views"], 2100)
        self.assertIsNone(self.vorgang())

    def test_kaputtes_json_fuehrt_direkt_zur_hand_eingabe(self):
        pid = self.post()
        with FakeClaude('{"views": 12').aktiv():
            self.foto(f"#{pid}")
        self.assertIn("Ich konnte die Zahlen nicht lesen (claude-Antwort ohne JSON)", self.letzte())
        self.assertIn(f"Bitte die Zahlen für #{pid} von Hand", self.letzte())
        self.assertEqual(self.temp_reste(), [])  # auch im Fehlerfall weg
        self.assertEqual(self.claude_zaehler(), ["screenshot: claude-Antwort ohne JSON"])
        self.assertEqual(self.messungen(pid), [])
        self.text("1240 61 6,8 34")
        self.assertEqual(self.messungen(pid)[0]["quelle"], "hand")

    def test_claude_fehler_raeumt_bild_weg(self):
        pid = self.post()
        with FakeClaude(returncode=1).aktiv():
            self.foto(f"#{pid}")
        self.assertIn("claude Exit 1", self.letzte())
        self.assertEqual(self.temp_reste(), [])
        self.assertEqual(self.vorgang()["art"], "hand")

    def test_screenshot_claude_aus_fragt_nie(self):
        self.konfig.daten["lernbot"]["screenshot_claude"] = False
        pid = self.post()
        with FakeClaude().aktiv() as (lauf, _):
            self.foto(f"#{pid}")
        lauf.assert_not_called()
        self.assertIn(f"Bitte die Zahlen für #{pid} von Hand", self.letzte())
        self.assertEqual(self.temp_reste(), [])
        self.assertEqual(self.claude_zaehler(), [])

    def test_zwei_fotos_hintereinander(self):
        self.post()
        self.foto(None)
        self.foto(None)
        self.assertEqual(len(self.temp_reste()), 1)  # nur das neue Bild wartet
        self.assertIn("🗑 Vorheriges Bild verworfen.", self.alle_texte())

    def test_png_als_datei(self):
        pid = self.post()
        fake = FakeClaude()
        with fake.aktiv():
            self.foto(f"#{pid}", anhang=Anhang(mime_type="image/png", file_name="Statistik.png"), als_datei=True)
        self.assertEqual(fake.aufrufe[0]["dateien"], ["screenshot.png"])
        self.assertEqual(len(self.messungen(pid)), 1)

    def test_heic_als_datei(self):
        pid = self.post()
        self.foto(None)  # ein wartendes Bild bleibt davon unberührt
        with FakeClaude().aktiv() as (lauf, _):
            self.foto(f"#{pid}", anhang=Anhang(mime_type="image/heic", file_name="IMG_1.HEIC"), als_datei=True)
        lauf.assert_not_called()
        self.assertIn("Bitte als Foto schicken", self.letzte())
        self.assertEqual(len(self.temp_reste()), 1)
        self.assertEqual(self.vorgang()["art"], "bild")

    def test_fremde_person(self):
        pid = self.post()
        self.foto(None)
        self.assertEqual(self.klick(f"pl:{pid}:", von=7).antworten, ["Nicht erlaubt."])
        self.assertEqual(self.vorgang()["art"], "bild")

    def test_klick_ohne_passenden_vorgang(self):
        pid = self.post()
        self.assertEqual(self.klick(f"pm:{pid}:ok").antworten, ["Schon erledigt."])
        self.assertEqual(self.klick(f"pl:{pid}:").antworten, ["Schon erledigt."])
        self.assertEqual(self.klick(f"pm:{pid}:ja").antworten, ["Unbekannter Knopf."])
        self.foto(None)
        self.assertEqual(self.klick("pl:999:").antworten, ["Post unbekannt."])
        self.assertEqual(self.vorgang()["art"], "bild")  # wartet weiter

    def test_ergebnis_verfaellt_wenn_der_vorgang_inzwischen_weg_ist(self):
        pid = self.post()
        fake = FakeClaude(waehrend=lambda: self.app.bot_data.pop("publikum", None))  # z. B. aufgeräumt
        with fake.aktiv():
            self.foto(f"#{pid}")
        self.assertEqual(self.messungen(pid), [])
        self.assertEqual(self.claude_zaehler(), ["screenshot: ok"])  # der Aufruf zählt trotzdem
        self.assertEqual(self.temp_reste(), [])
        self.assertNotIn("gespeichert", self.alle_texte())


# --- Hand-Eingabe ------------------------------------------------------------------------------------------------

class HandEingabe(MitLernBot):
    def test_text_ohne_bild_wird_gespeichert(self):
        pid = self.post()
        with FakeClaude().aktiv() as (lauf, _):
            self.text(f"#{pid} 1240 61 6.8 34")
        lauf.assert_not_called()
        (m,) = self.messungen(pid)
        self.assertEqual((m["quelle"], m["views"], m["likes"], m["wiedergabe_s"], m["voll_prozent"], m["kommentare"]),
                         ("hand", 1240, 61, 6.8, 34.0, None))
        self.assertIn(f"💾 #{pid} gespeichert (von Hand)", self.letzte())

    def test_hand_eingabe_unplausibel_fragt_nach(self):
        pid = self.post()
        self.alte_messung(pid, views=2000)
        self.text(f"#{pid} 1500 70 6,8 34")
        self.assertEqual(len(self.messungen(pid)), 1)
        self.assertIn("Views gesunken: 2000 → 1500", self.letzte())
        self.assertEqual(self.bot.nachrichten[-1][1], lernbot_zahlen.knoepfe_rueckfrage(pid))
        self.klick(f"pm:{pid}:ok")
        neu = self.messungen(pid)[-1]
        self.assertEqual((neu["quelle"], neu["views"]), ("hand", 1500))

    def test_wiedergabe_laenger_als_video_fragt_nach(self):
        pid = self.post(dauer_s=20.0)
        self.text(f"#{pid} 1240 61 45 34")  # 45 s bei einem 20-s-Video: vermutlich Tippfehler
        self.assertIn("länger als 1,5 × Videolänge", self.letzte())
        self.assertEqual(self.messungen(pid), [])

    def test_kaputte_zahlen_lassen_den_vorgang_offen(self):
        pid = self.post()
        with FakeClaude("kein JSON").aktiv():
            self.foto(f"#{pid}")
        self.text("1240 61")
        self.assertIn("genau vier Werte", self.letzte())
        self.text("1.240 61 6,8 34")  # Tausenderpunkt fällt auf
        self.assertIn("ganze Zahl", self.letzte())
        self.assertEqual(self.vorgang()["art"], "hand")
        self.text("1240 61 6,8 34")
        self.assertEqual(len(self.messungen(pid)), 1)

    def test_kaputter_messungs_text(self):
        pid = self.post()
        self.text(f"#{pid} 1240 x 6,8 34")
        self.assertIn("„x“ ist keine Zahl", self.letzte())
        self.assertEqual(self.messungen(pid), [])

    def test_unbekannter_post_im_text(self):
        self.text("#999 1240 61 6,8 34")
        self.assertIn("Post #999 kenne ich nicht", self.letzte())

    def test_freier_text_ohne_vorgang(self):
        self.text("Hallo Bot")
        self.assertIn("/hilfe", self.letzte())

    def test_neuer_text_ersetzt_wartendes_bild(self):
        pid = self.post()
        self.foto(None)
        self.text(f"#{pid} 1240 61 6,8 34")
        self.assertEqual(self.temp_reste(), [])
        self.assertIn("🗑 Vorheriges Bild verworfen.", self.alle_texte())
        self.assertEqual(len(self.messungen(pid)), 1)


# --- Aufräumen, Secrets, nie wecken, Handler-Reihenfolge ---------------------------------------------------------

class Aufraeumen(MitLernBot):
    def test_bild_wird_nach_zehn_minuten_verworfen(self):
        self.post()
        self.foto(None)
        self.assertEqual(asyncio.run(lernbot_zahlen.aufraeumen(self.app, T + timedelta(seconds=599))), 0)
        self.assertEqual(len(self.temp_reste()), 1)
        self.assertEqual(asyncio.run(lernbot_zahlen.aufraeumen(self.app, T + timedelta(seconds=601))), 1)
        self.assertEqual(self.temp_reste(), [])
        self.assertIsNone(self.vorgang())
        self.assertIn("⌛ Screenshot verworfen – bitte nochmal mit #Nummer", self.letzte())
        self.assertEqual(asyncio.run(lernbot_zahlen.aufraeumen(self.app, T + timedelta(hours=1))), 0)

    def test_offene_hand_eingabe_verfaellt_auch(self):
        pid = self.post()
        with FakeClaude("kein JSON").aktiv():
            self.foto(f"#{pid}")
        self.assertEqual(asyncio.run(lernbot_zahlen.aufraeumen(self.app, T + timedelta(minutes=11))), 1)
        self.assertIn(f"⌛ Eingabe für #{pid} verworfen", self.letzte())
        self.text("1240 61 6,8 34")  # kommt zu spät – kein Post mehr offen
        self.assertEqual(self.messungen(pid), [])
        self.assertIn("/hilfe", self.letzte())

    def test_ohne_zeit_gilt_jetzt(self):
        self.post()
        self.foto(None)
        self.assertEqual(asyncio.run(lernbot_zahlen.aufraeumen(self.app)), 0)  # jetzt() = T, nichts ist alt


class Secrets(MitLernBot):
    def test_token_aus_der_download_url_nie_in_log_oder_antwort(self):
        from telegram.error import NetworkError

        pid = self.post()
        kaputt = Anhang(TelegramDatei(fehler=NetworkError(f"Fehler beim Laden von {FAKE_URL}")))
        with self.assertLogs(level="DEBUG") as logs:
            self.foto(f"#{pid}", anhang=kaputt)
        log_text = "\n".join(logs.output)
        self.assertIn("Download fehlgeschlagen (NetworkError)", log_text)
        for geheim in (FAKE_TOKEN, FAKE_URL, "api.telegram.org"):
            self.assertNotIn(geheim, log_text)
            self.assertNotIn(geheim, self.alle_texte())
        self.assertIn("Download fehlgeschlagen", self.letzte())
        self.assertEqual(self.temp_reste(), [])
        self.assertIsNone(self.vorgang())

    def test_kein_bild_und_keine_rohantwort_im_log(self):
        pid = self.post()
        with self.assertLogs(level="DEBUG") as logs, FakeClaude(json.dumps({**GUELTIG, "notiz": "GEHEIM-ROH"})).aktiv():
            self.foto(f"#{pid}")
        log_text = "\n".join(logs.output)
        self.assertNotIn("GEHEIM-ROH", log_text)
        self.assertNotIn("1240", log_text)
        self.assertNotIn(str(self.temp), log_text)  # auch kein Pfad zum Bild


class NieWecken(MitLernBot):
    def test_ganzer_fluss_weckt_nie(self):
        self.konfig.daten["speicher"].update(host="pve-gross", wol_mac="aa:bb:cc:dd:ee:ff")
        pid = self.post()
        with mock.patch.object(konfig.Konfig, "_host_erreichbar", return_value=False), \
                mock.patch("clip_pipeline.konfig.sende_wake_on_lan") as wol, \
                mock.patch.object(big, "wach_halten") as wach, \
                mock.patch("socket.create_connection", side_effect=AssertionError("Netzwerkzugriff")):
            with FakeClaude().aktiv():
                self.foto(None)
                self.klick(f"pl:{pid}:")
                self.foto(f"#{pid}")
            self.text(f"#{pid} 1 1 1 1")
            self.klick(f"pm:{pid}:hand")
            self.text("2000 70 6,8 34")
            self.foto(None)
            asyncio.run(lernbot_zahlen.aufraeumen(self.app, T + timedelta(hours=1)))
        self.assertEqual(len(self.messungen(pid)), 3)
        wol.assert_not_called()
        wach.assert_not_called()


@unittest.skipIf(lernbot_zahlen is None, "python-telegram-bot fehlt")
class HandlerReihenfolge(MitClaudeKonfig):
    """In der echten App (lernbot.baue_app) landet jedes Update beim richtigen Handler – vor allem `pl:`/`pm:`
    nicht in lernbot.bei_klick (der läse `pl:17:` als Knopf für Entwurf 17)."""

    def setUp(self):
        super().setUp()
        self.app = lernbot.baue_app(self.konfig, "123456:TEST", 42)
        self.addCleanup(self.app.bot_data["con"].close)

    def erster_handler(self, update):
        for handler in self.app.handlers[0]:
            if handler.check_update(update) not in (None, False):
                return handler.callback
        return None

    def test_reihenfolge(self):
        from telegram import (CallbackQuery, Chat, Document, Message, MessageEntity, PhotoSize, Update,
                              User)

        from clip_pipeline import lernbot_publikum

        ich, fremd = User(42, "F", False), User(7, "X", False)
        chat = Chat(42, "private")

        def klick(daten, von=ich):
            return Update(1, callback_query=CallbackQuery("1", von, "c", data=daten))

        def nachricht(von=ich, **kw):
            return Update(2, message=Message(1, T, chat, from_user=von, **kw))

        foto = [PhotoSize("f", "u", 90, 160)]
        self.assertIs(self.erster_handler(klick("pl:17:")), lernbot_zahlen.bei_klick)
        self.assertIs(self.erster_handler(klick("pm:17:ok")), lernbot_zahlen.bei_klick)
        self.assertIs(self.erster_handler(klick("pl:17:", von=fremd)), lernbot_zahlen.bei_klick)  # prüft selbst
        self.assertIs(self.erster_handler(klick("d:17:1")), lernbot.bei_klick)
        self.assertIs(self.erster_handler(nachricht(photo=foto, caption="#17")), lernbot_zahlen.bei_foto)
        self.assertIs(self.erster_handler(nachricht(document=Document("f", "u", file_name="x.png",
                                                                      mime_type="image/png"))),
                      lernbot_zahlen.bei_foto)
        self.assertIs(self.erster_handler(nachricht(text="#17 1240 61 6.8 34")), lernbot_zahlen.bei_text)
        befehl = nachricht(text="/publikum", entities=[MessageEntity(MessageEntity.BOT_COMMAND, 0, 9)])
        befehl.message.set_bot(SimpleNamespace(username="lern_bot"))  # CommandHandler vergleicht /befehl@botname
        self.assertIs(self.erster_handler(befehl), lernbot_publikum.cmd_publikum)
        self.assertIsNone(self.erster_handler(nachricht(von=fremd, photo=foto)))  # fremde Fotos: niemand
        self.assertIsNone(self.erster_handler(nachricht(von=fremd, text="#17 1 2 3 4")))


if __name__ == "__main__":
    unittest.main()
