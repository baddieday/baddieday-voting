"""Lern-Bot ohne Netzwerk: 📦 Upload-Paket, Häkchen je Plattform und /link für Entwürfe (Spec §10.4, Paket c).

Fake-Telegram wie in tests/test_lernbot.py (FakeBot/FakeQuery von dort, hier nur erweitert um send_document,
Knöpfe an send_message und edit_message_text). Entwürfe werden von Hand angelegt (Schnittliste als JSON, leere
Moment-Dateien) und `entwurf.rendere` durch eine Attrappe ersetzt – den echten Render mit ffmpeg prüft
tests/test_upload_paket.py. Alle Zeiten fest (lernbot_paket.jetzt ersetzt).
"""

from __future__ import annotations

import asyncio
import json
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from clip_pipeline import big, entwurf, konfig, publikum, regie_lernen
from clip_pipeline import sperre as sperre_modul
from clip_pipeline.sperre import sperre
from clip_pipeline.zeit import UTC, iso

from tests.hilfen import MitSpeicher

try:
    import telegram  # noqa: F401

    from clip_pipeline import lernbot, lernbot_paket
    from tests.test_lernbot import FakeBot, FakeQuery
except ImportError:
    lernbot = lernbot_paket = None
    FakeBot = FakeQuery = object

T0 = datetime(2026, 9, 25, 18, 0, tzinfo=UTC)
TIKTOK = "https://www.tiktok.com/@baddieday/video/7300123456789012345"
QUELLE = "Song: A & B <Remix>\nMusic provided by NoCopyrightSounds"


class PaketBot(FakeBot):
    """FakeBot aus test_lernbot plus Dateien und Knöpfe an Textnachrichten."""

    def __init__(self):
        super().__init__()
        self.dokumente, self.nachrichten = [], []

    async def send_document(self, chat, document=None, **kw):
        self.dokumente.append({"chat": chat, "inhalt": document.read(), **kw})

    async def send_message(self, chat, text, **kw):
        await super().send_message(chat, text, **kw)
        self.nachrichten.append({"chat": chat, "text": text, **kw})


class PaketQuery(FakeQuery):
    def __init__(self, daten, von=42):
        super().__init__(daten, von)
        self.texte = []

    async def edit_message_text(self, text, **kw):
        self.texte.append({"text": text, **kw})


def knopf_daten(markup) -> list[str]:
    return [] if markup is None else [b.callback_data for reihe in markup.inline_keyboard for b in reihe]


@unittest.skipIf(lernbot_paket is None, "python-telegram-bot fehlt")
class MitLernPaket(MitSpeicher):
    def setUp(self):
        super().setUp()
        # Getrennter Betrieb (E19): Puffer mit Marke, Lager nur „gesetzt“ (wird nie angefasst)
        (self.konfig.wurzel / ".clip-puffer").touch()
        self.konfig.daten["lager"]["wurzel"] = str(self.tmp / "lager")
        self.konfig.daten["publikum"].update(plattformen=["tiktok"], upload_ordner="export")
        self.konfig.daten["musik"]["ordner"] = str(self.tmp / "musik-bibliothek")
        self.konfig.daten.setdefault("regie", {})["ordner"] = str(self.tmp / "regie")
        self.konfig.daten.setdefault("lernbot", {})["naechster_nach_bewertung"] = False
        self.konfig.daten["sperre"]["warten_s"] = 5
        self.bot = PaketBot()
        self.aufgaben = []
        self.app = SimpleNamespace(bot=self.bot, bot_data={"con": self.con, "konfig": self.konfig, "erlaubt": 42},
                                   create_task=lambda koro: self.aufgaben.append(koro))
        self.context = SimpleNamespace(bot_data=self.app.bot_data, application=self.app, args=[])
        self.renders = []
        for p in (mock.patch.object(entwurf, "rendere", side_effect=self.falsches_rendere),
                  mock.patch.object(lernbot_paket, "jetzt", side_effect=lambda: self.uhr)):
            p.start()
            self.addCleanup(p.stop)
        self.uhr = T0
        self._nr = 0

    def tearDown(self):
        for koro in self.aufgaben:  # nicht ausgeführte Aufgaben schließen (sonst RuntimeWarning)
            koro.close()
        super().tearDown()

    def falsches_rendere(self, liste, ziel, k, **kw):
        # merken, ob die Pipeline-Sperre gerade gehalten wird (sie gehört ums Rendern, Leitplanke 2)
        gehalten = str(self.konfig.datenbank.with_suffix(".lock").resolve()) in sperre_modul.GEHALTEN
        # und in welchem Faden gerendert wird (im Haupt-Faden stünde der ganze Bot, Leitplanke 2)
        self.renders.append({**kw, "sperre_gehalten": gehalten, "faden": threading.current_thread()})
        ziel.parent.mkdir(parents=True, exist_ok=True)
        ziel.write_bytes(b"upload-fassung")
        return {"datei": str(ziel), "mb": 0.0, "dauer_s": liste["dauer_s"], "encoder": "libx264",
                "aufloesung": [1080, 1920]}

    def entwurf_anlegen(self, *, fmt: str = "short", daumen: int | None = 1, mit_musik: bool = True,
                        dauer_s: float = 31.0) -> int:
        """Entwurf wie aus regie.erstelle (Schnittliste v3), Moment-Dateien leer – rendere ist ersetzt."""
        self._nr += 1
        name = f"{fmt}-test-{self._nr}"
        cid = self.clip_anlegen(status="freigegeben", max_gruppe=3, match_id=f"m{self._nr}")
        self.con.execute("UPDATE clips SET typ = 'triple' WHERE id = ?", (cid,))
        segmente = []
        for i, (moment, clip_id) in enumerate(((f"clip:{cid}", cid), (f"datei:{self._nr}", None)), 1):
            datei = self.tmp / "momente" / f"{name}-{i}.mp4"
            datei.parent.mkdir(parents=True, exist_ok=True)
            datei.write_bytes(b"")
            segmente.append({"nr": i, "moment": moment, "clip_id": clip_id, "datei": str(datei),
                             "quelle_start_s": 0.0, "quelle_ende_s": 10.0, "zeit_start": 10.0 * (i - 1),
                             "zeit_ende": 10.0 * i, "uebergang": {"art": "schnitt", "dauer_s": 0.0}})
        musik = None
        if mit_musik:
            track = Path(self.konfig.daten["musik"]["ordner"]) / "titel.mp3"
            track.parent.mkdir(parents=True, exist_ok=True)
            track.write_bytes(b"")
            musik = {"datei": "titel.mp3", "titel": "Titel", "kuenstler": "NCS", "quelle": QUELLE, "bpm": 128,
                     "start_s": 0.0, "pegel": 0.3}
        liste = {"version": 3, "art": "regie", "name": name, "format": fmt,
                 "aufloesung": [1080, 1920] if fmt == "short" else [1920, 1080], "fps": 30, "dauer_s": dauer_s,
                 "stimmung": "episch", "parameter": {"beats_pro_schnitt": 1}, "musik": musik,
                 "overlay": "clip-battle.de" if fmt == "short" else None, "bogen": [4.0, 9.0],
                 "segmente": segmente, "hinweise": []}
        pfad = self.tmp / "regie" / f"{name}.json"
        pfad.parent.mkdir(parents=True, exist_ok=True)
        pfad.write_text(json.dumps(liste), encoding="utf-8")
        eid = int(self.con.execute(
            "INSERT INTO entwuerfe (name, format, schnittliste, parameter, dauer_s, status, erstellt)"
            " VALUES (?, ?, ?, '{}', ?, 'gesendet', ?)", (name, fmt, str(pfad), dauer_s, iso(T0))).lastrowid)
        if daumen is not None:
            regie_lernen.bewerte(self.con, eid, daumen=daumen)
        return eid

    def klick(self, daten: str, von: int = 42) -> PaketQuery:
        q = PaketQuery(daten, von)
        asyncio.run(lernbot_paket.bei_klick(SimpleNamespace(callback_query=q), self.context))
        return q

    def link(self, *args: str) -> list[str]:
        antworten = []

        async def reply_text(text, **_):
            antworten.append(text)

        self.context.args = list(args)
        update = SimpleNamespace(effective_message=SimpleNamespace(reply_text=reply_text))
        asyncio.run(lernbot_paket.cmd_link(update, self.context))
        return antworten

    def posts(self) -> list:
        return self.con.execute("SELECT * FROM posts ORDER BY id").fetchall()

    def aufgaben_ausfuehren(self) -> None:
        while self.aufgaben:
            asyncio.run(self.aufgaben.pop(0))


# --- Reine Funktionen ------------------------------------------------------------------------------------

@unittest.skipIf(lernbot_paket is None, "python-telegram-bot fehlt")
class Knoepfe(unittest.TestCase):
    def test_link_argumente(self):
        self.assertEqual(lernbot_paket.lies_link_argumente(["41", TIKTOK]), (41, TIKTOK))
        self.assertEqual(lernbot_paket.lies_link_argumente(["e41", TIKTOK]), (41, TIKTOK))
        self.assertEqual(lernbot_paket.lies_link_argumente(["E7", TIKTOK]), (7, TIKTOK))
        for falsch in ([], ["41"], [TIKTOK], ["x41", TIKTOK], ["41", TIKTOK, "noch"], ["-1", TIKTOK], ["e", TIKTOK]):
            with self.assertRaises(ValueError, msg=falsch) as fehler:
                lernbot_paket.lies_link_argumente(falsch)
            self.assertIn("Aufruf: /link 41", str(fehler.exception))

    def test_checkliste_nur_offene_plattformen(self):
        k = lernbot_paket.knoepfe_checkliste(41, {"tiktok": False, "youtube": False})
        self.assertEqual(k, [[("✅ TikTok erledigt", "pt:41:t")], [("✅ YouTube Shorts erledigt", "pt:41:y")]])
        self.assertEqual(lernbot_paket.knoepfe_checkliste(41, {"tiktok": True, "youtube": False}),
                         [[("✅ YouTube Shorts erledigt", "pt:41:y")]])
        self.assertEqual(lernbot_paket.knoepfe_checkliste(41, {"tiktok": True}), [])

    def test_callback_daten_hoechstens_64_byte(self):
        riesig = 10 ** 15  # weit mehr Entwürfe, als es je geben wird
        daten = [d for reihe in lernbot_paket.knoepfe_checkliste(riesig, {"tiktok": False, "youtube": False})
                 for _, d in reihe]
        daten += [d for reihe in lernbot_paket.knoepfe_nach_fertig(riesig, {"daumen": 1}, "short") for _, d in reihe]
        self.assertTrue(all(len(d.encode()) <= 64 for d in daten), daten)

    def test_kuerzel_ohne_clip_battle(self):
        self.assertEqual(lernbot_paket.PLATTFORM_KUERZEL, {"y": "youtube", "t": "tiktok"})


class PaketErlaubt(MitLernPaket):
    def test_regeln(self):
        gut = self.entwurf_anlegen()
        self.assertIsNone(lernbot_paket.paket_erlaubt(self.con, gut))
        self.assertIn("gibt es nicht", lernbot_paket.paket_erlaubt(self.con, 999))
        self.assertIn("nur für Shorts", lernbot_paket.paket_erlaubt(self.con, self.entwurf_anlegen(fmt="zusammenschnitt")))
        schlecht = self.entwurf_anlegen(daumen=-1)
        self.assertEqual(lernbot_paket.paket_erlaubt(self.con, schlecht), f"Entwurf #{schlecht} ist nicht mit 👍 bewertet.")
        self.assertIn("nicht mit 👍", lernbot_paket.paket_erlaubt(self.con, self.entwurf_anlegen(daumen=None)))


# --- ✅ fertig im Lern-Bot zeigt 📦 (lernbot.bei_klick, x-Zweig) ---------------------------------------------

class NachFertig(MitLernPaket):
    def fertig(self, eid: int):
        q = FakeQuery(f"x:{eid}:")
        asyncio.run(lernbot.bei_klick(SimpleNamespace(callback_query=q), self.context))
        return q.bearbeitet[0]["reply_markup"]

    def test_daumen_hoch_short_zeigt_upload_paket(self):
        eid = self.entwurf_anlegen()
        self.assertEqual(knopf_daten(self.fertig(eid)), [f"pk:{eid}:"])

    def test_daumen_runter_und_zusammenschnitt_ohne_paket(self):
        self.assertIsNone(self.fertig(self.entwurf_anlegen(daumen=-1)))
        self.assertIsNone(self.fertig(self.entwurf_anlegen(fmt="zusammenschnitt")))


# --- 📦 Upload-Paket ----------------------------------------------------------------------------------------

class Paket(MitLernPaket):
    def test_paket_datei_caption_und_checkliste(self):
        eid = self.entwurf_anlegen()
        q = self.klick(f"pk:{eid}:")
        self.assertIn("📦", q.antworten[0])
        self.assertTrue(self.app.bot_data["paket_arbeitet"])   # schon beim Klick gesetzt (Doppelklick-Schutz)
        self.aufgaben_ausfuehren()
        (dokument,) = self.bot.dokumente
        self.assertEqual((dokument["chat"], dokument["filename"]), (42, f"clip-battle_e{eid}.mp4"))
        self.assertEqual(dokument["inhalt"], b"upload-fassung")
        self.assertIn(f"Entwurf #{eid}", dokument["caption"])
        texte = [n["text"] for n in self.bot.nachrichten]
        pre = next(t for t in texte if t.startswith("<pre>"))
        self.assertIn("#triplekill", pre)
        self.assertIn("A &amp; B &lt;Remix&gt;", pre)               # Quellenangabe drin, HTML-sicher
        liste = self.bot.nachrichten[-1]
        self.assertEqual(knopf_daten(liste["reply_markup"]), [f"pt:{eid}:t"])
        self.assertIn(f"/link {eid}", liste["text"])
        self.assertFalse(self.app.bot_data["paket_arbeitet"])
        self.assertEqual(len(self.renders), 1)
        self.assertEqual(self.renders[0]["crf"], entwurf.UPLOAD_CRF)
        self.assertTrue(self.renders[0]["sperre_gehalten"])        # gerendert wird nur unter der Sperre
        # Leitplanke 2: im Thread (asyncio.to_thread). asyncio.run läuft hier wie run_polling im Betrieb im
        # Haupt-Faden – sonst blockierte ein 📦-Klick den Bot, solange er auf die Sperre wartet und rendert.
        self.assertIsNot(self.renders[0]["faden"], threading.main_thread())
        zeile = self.con.execute("SELECT upload_pfad FROM entwuerfe WHERE id = ?", (eid,)).fetchone()
        self.assertTrue(zeile["upload_pfad"].endswith("_upload.mp4"))
        self.assertEqual(self.posts(), [])                          # das Paket allein ist noch kein Post

    def test_zweites_paket_rendert_nicht_neu(self):
        eid = self.entwurf_anlegen()
        for _ in range(2):
            self.klick(f"pk:{eid}:")
            self.aufgaben_ausfuehren()
        self.assertEqual((len(self.renders), len(self.bot.dokumente)), (1, 2))

    def test_doppelklick_waehrend_des_renderns(self):
        eid = self.entwurf_anlegen()
        self.klick(f"pk:{eid}:")
        q = self.klick(f"pk:{eid}:")                                # der erste läuft noch
        self.assertIn("schon gebaut", q.antworten[0])
        self.assertEqual(len(self.aufgaben), 1)

    def test_kein_paket_fuer_daumen_runter_zusammenschnitt_oder_unbekannt(self):
        for eid in (self.entwurf_anlegen(daumen=-1), self.entwurf_anlegen(fmt="zusammenschnitt"), 999):
            q = self.klick(f"pk:{eid}:")
            self.assertTrue(q.antworten[0], eid)
            self.assertNotIn("📦", q.antworten[0])
        self.assertEqual((self.aufgaben, self.renders), ([], []))
        self.assertFalse(self.app.bot_data.get("paket_arbeitet"))

    def test_sperre_belegt_klare_meldung(self):
        eid = self.entwurf_anlegen()
        self.konfig.daten["sperre"]["warten_s"] = 0
        with sperre(self.konfig.datenbank.with_suffix(".lock")), \
                self.assertLogs("lern-bot", "WARNING"):              # z. B. ein Entwurf rendert gerade
            asyncio.run(lernbot_paket.sende_paket(self.app, eid))
        self.assertEqual((self.bot.dokumente, self.renders), ([], []))
        self.assertIn("später", self.bot.nachrichten[-1]["text"])
        self.assertIn("📦", self.bot.nachrichten[-1]["text"])
        self.assertFalse(self.app.bot_data["paket_arbeitet"])

    def test_fehlende_moment_datei_wird_gemeldet(self):
        eid = self.entwurf_anlegen()
        liste = json.loads((self.tmp / "regie" / "short-test-1.json").read_text(encoding="utf-8"))
        Path(liste["segmente"][0]["datei"]).unlink()
        with self.assertLogs("lern-bot", "WARNING"):
            asyncio.run(lernbot_paket.sende_paket(self.app, eid))
        self.assertEqual(self.bot.dokumente, [])
        self.assertIn("⚠️ Upload-Paket", self.bot.nachrichten[-1]["text"])
        self.assertIn("Moment-Datei fehlt", self.bot.nachrichten[-1]["text"])
        self.assertFalse(self.app.bot_data["paket_arbeitet"])

    def test_unerwarteter_fehler_ohne_details_an_dich(self):
        eid = self.entwurf_anlegen()
        with mock.patch.object(lernbot_paket, "baue_paket", side_effect=RuntimeError("geheim: /pfad/mit/token")), \
                self.assertLogs("lern-bot", "ERROR"):
            asyncio.run(lernbot_paket.sende_paket(self.app, eid))
        text = self.bot.nachrichten[-1]["text"]
        self.assertIn("⚠️ Upload-Paket", text)
        self.assertNotIn("geheim", text)
        self.assertFalse(self.app.bot_data["paket_arbeitet"])

    def test_fremde_person_und_unbekannter_knopf(self):
        eid = self.entwurf_anlegen()
        self.assertEqual(self.klick(f"pk:{eid}:", von=7).antworten, ["Nicht erlaubt."])
        self.assertEqual(self.klick(f"pt:{eid}:x").antworten, ["Unbekannter Knopf."])
        self.assertEqual(self.klick("pk:abc:").antworten, ["Unbekannter Knopf."])
        self.assertEqual((self.aufgaben, self.posts()), ([], []))


# --- Häkchen und /link ----------------------------------------------------------------------------------------

class Haekchen(MitLernPaket):
    def test_haekchen_legt_post_an_doppelklick_bleibt_einer(self):
        eid = self.entwurf_anlegen()
        q = self.klick(f"pt:{eid}:t")
        (p,) = self.posts()
        self.assertIn(f"Post #{p['id']}", q.antworten[0])
        self.assertEqual((p["art"], p["ziel"], p["entwurf_id"], p["plattform"], p["url"]),
                         ("entwurf", f"entwurf:{eid}", eid, "tiktok", None))
        self.assertEqual(p["gepostet_utc"], iso(T0))
        self.assertEqual(json.loads(p["rezept"])["machart"], "regie")
        self.assertEqual(p["dauer_s"], 31.0)
        self.assertEqual(knopf_daten(q.texte[0]["reply_markup"]), [])  # TikTok erledigt → kein Knopf mehr
        self.assertIn("✅ TikTok", q.texte[0]["text"])

        self.uhr = T0 + timedelta(hours=1)
        q = self.klick(f"pt:{eid}:t")                                  # Doppelklick
        self.assertIn(f"schon erledigt (Post #{p['id']})", q.antworten[0])
        (p2,) = self.posts()
        self.assertEqual(p2["gepostet_utc"], iso(T0))                  # der erste Zeitpunkt bleibt

    def test_kein_post_fuer_daumen_runter_und_zusammenschnitt(self):
        for eid in (self.entwurf_anlegen(daumen=-1), self.entwurf_anlegen(fmt="zusammenschnitt")):
            q = self.klick(f"pt:{eid}:t")
            self.assertNotIn("Post #", q.antworten[0])
            self.assertEqual(q.texte, [])
        self.assertEqual(self.posts(), [])

    def test_fehlende_schnittliste(self):
        # Startauftrag §5 „fehlende Dateien“: das Häkchen sagt es kurz, Details im Log, kein halber Post
        eid = self.entwurf_anlegen()
        (self.tmp / "regie" / "short-test-1.json").unlink()
        with self.assertLogs("lern-bot", "WARNING"):
            q = self.klick(f"pt:{eid}:t")
        self.assertEqual(q.antworten, ["⚠️ Kein Post angelegt – Details im Log."])
        self.assertEqual(q.texte, [])          # die Checkliste bleibt unverändert
        self.assertEqual(self.posts(), [])

    def test_youtube_nur_wenn_eingestellt(self):
        eid = self.entwurf_anlegen()
        q = self.klick(f"pt:{eid}:y")
        self.assertIn("[publikum].plattformen", q.antworten[0])
        self.assertEqual(self.posts(), [])
        self.konfig.daten["publikum"]["plattformen"] = ["tiktok", "youtube"]
        self.klick(f"pt:{eid}:y")
        self.assertEqual([p["plattform"] for p in self.posts()], ["youtube"])
        self.assertEqual(lernbot_paket.checkliste_stand(self.con, self.konfig, eid),
                         {"tiktok": False, "youtube": True})


class Link(MitLernPaket):
    def test_link_mit_und_ohne_e_legt_post_an_und_nennt_die_nummer(self):
        eid = self.entwurf_anlegen()
        (antwort,) = self.link(f"e{eid}", TIKTOK)
        (p,) = self.posts()
        self.assertIn(f"Post #{p['id']}", antwort)
        self.assertIn(f"#{p['id']}", antwort.split("Bildunterschrift", 1)[1])
        self.assertEqual((p["url"], p["video_id"], p["ziel"]), (TIKTOK, "7300123456789012345", f"entwurf:{eid}"))

        anders = self.entwurf_anlegen()
        self.link(str(anders), TIKTOK.replace("7300123456789012345", "7300000000000000001"))
        self.assertEqual([p["ziel"] for p in self.posts()], [f"entwurf:{eid}", f"entwurf:{anders}"])

    def test_zweiter_link_ersetzt_den_ersten(self):
        eid = self.entwurf_anlegen()
        self.link(str(eid), "https://www.tiktok.com/@baddieday/video/111")
        (antwort,) = self.link(str(eid), TIKTOK)
        (p,) = self.posts()
        self.assertEqual((p["url"], p["video_id"]), (TIKTOK, "7300123456789012345"))
        self.assertIn("ersetzt", antwort)

    def test_link_nach_haekchen_behaelt_den_zeitpunkt(self):
        eid = self.entwurf_anlegen()
        self.klick(f"pt:{eid}:t")
        self.uhr = T0 + timedelta(hours=2)
        self.link(str(eid), TIKTOK)
        (p,) = self.posts()
        self.assertEqual((p["gepostet_utc"], p["url"]), (iso(T0), TIKTOK))

    def test_kein_post_bei_fremden_links(self):
        eid = self.entwurf_anlegen()
        (a,) = self.link(str(eid), "https://clip-battle.de/battle/7")
        self.assertIn("clip-battle.de", a)
        (b,) = self.link(str(eid), "http://www.tiktok.com/@x/video/1")    # nicht https
        (c,) = self.link(str(eid), "https://example.com/video/1")
        self.assertIn("Unbekannter Link", b)
        self.assertIn("Unbekannter Link", c)
        self.assertEqual(self.posts(), [])

    def test_kein_post_fuer_daumen_runter_zusammenschnitt_und_unbekannt(self):
        runter, zs = self.entwurf_anlegen(daumen=-1), self.entwurf_anlegen(fmt="zusammenschnitt")
        self.assertIn("nicht mit 👍", self.link(str(runter), TIKTOK)[0])
        self.assertIn("nur für Shorts", self.link(str(zs), TIKTOK)[0])
        self.assertIn("gibt es nicht", self.link("999", TIKTOK)[0])
        self.assertEqual(self.posts(), [])

    def test_falscher_aufruf(self):
        self.assertIn("Aufruf: /link", self.link()[0])
        self.assertIn("Aufruf: /link", self.link("abc", TIKTOK)[0])
        self.assertEqual(self.posts(), [])

    def test_fehler_beim_link_rollt_auch_den_post_zurueck(self):
        eid = self.entwurf_anlegen()
        with mock.patch.object(publikum, "link_nachtragen", side_effect=ValueError("kaputt")), \
                self.assertLogs("lern-bot", "WARNING"):
            (antwort,) = self.link(str(eid), TIKTOK)
        self.assertIn("⚠️", antwort)
        self.assertEqual(self.posts(), [])                              # eine Transaktion: kein halber Post
        self.assertFalse(self.con.in_transaction)

    def test_fehlende_schnittliste(self):
        eid = self.entwurf_anlegen()
        (self.tmp / "regie" / "short-test-1.json").unlink()
        with self.assertLogs("lern-bot", "WARNING"):
            (antwort,) = self.link(str(eid), TIKTOK)
        self.assertIn("⚠️", antwort)
        self.assertEqual(self.posts(), [])


# --- Einhängen in den Lern-Bot ----------------------------------------------------------------------------------

class Einhaengen(MitLernPaket):
    def test_pk_und_pt_landen_hier_link_ist_ein_befehl(self):
        from telegram import CallbackQuery, Update, User

        app = lernbot.baue_app(self.konfig, "123456:TEST", 42)
        try:
            handler = app.handlers[0]
            befehle = {c for h in handler for c in getattr(h, "commands", ())}
            self.assertIn("link", befehle)
            for daten, erwartet in (("pk:41:", lernbot_paket.bei_klick), ("pt:41:t", lernbot_paket.bei_klick),
                                    ("d:41:1", lernbot.bei_klick)):
                update = Update(1, callback_query=CallbackQuery("1", User(42, "ich", False), "c", data=daten))
                erster = next(h for h in handler if h.check_update(update))
                self.assertIs(erster.callback, erwartet, daten)
        finally:
            app.bot_data["con"].close()


# --- Nie wecken ----------------------------------------------------------------------------------------------------

class NieWecken(MitLernPaket):
    def test_paket_haekchen_und_link_wecken_nie(self):
        eid = self.entwurf_anlegen()
        self.konfig.daten["speicher"].update(host="pve-gross", wol_mac="aa:bb:cc:dd:ee:ff")
        with mock.patch.object(konfig.Konfig, "_host_erreichbar", return_value=False), \
                mock.patch("clip_pipeline.konfig.sende_wake_on_lan") as wol, \
                mock.patch.object(big, "wach_halten") as wach, \
                mock.patch("socket.create_connection", side_effect=AssertionError("Netzwerkzugriff")):
            self.klick(f"pk:{eid}:")
            self.aufgaben_ausfuehren()
            self.klick(f"pt:{eid}:t")
            self.link(str(eid), TIKTOK)
            # Ohne getrennten Betrieb wäre die Wurzel pve-big: klare Meldung statt Wecken
            self.konfig.daten["lager"]["wurzel"] = ""
            self.con.execute("UPDATE entwuerfe SET upload_pfad = NULL WHERE id = ?", (eid,))
            with self.assertLogs("lern-bot", "WARNING"):
                asyncio.run(lernbot_paket.sende_paket(self.app, eid))
        self.assertEqual(len(self.bot.dokumente), 1)
        self.assertIn("getrennten Betrieb", self.bot.nachrichten[-1]["text"])
        self.assertEqual(len(self.posts()), 1)
        wol.assert_not_called()
        wach.assert_not_called()


if __name__ == "__main__":
    unittest.main()
