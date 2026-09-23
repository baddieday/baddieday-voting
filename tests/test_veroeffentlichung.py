import asyncio
import unittest
from datetime import timedelta
from types import SimpleNamespace

from clip_pipeline import db, medien, shorts
from clip_pipeline.bot import aktionen
from clip_pipeline.zeit import iso, jetzt

from tests.hilfen import HAT_FFMPEG, MitSpeicher, testvideo


class Uploads(MitSpeicher):
    def test_erst_youtube_und_tiktok_ergeben_veroeffentlicht(self):
        cid = self.clip_anlegen(status="freigegeben")
        self.assertEqual(aktionen.upload_stand(self.con, cid, self.konfig), {"youtube": False, "tiktok": False, "clipbattle": False})
        knoepfe = aktionen.knoepfe_upload(cid, {"youtube": True, "tiktok": False, "clipbattle": False})
        self.assertEqual([d for reihe in knoepfe for _, d in reihe], [f"t:{cid}", f"c:{cid}"])  # nur Offenes
        self.assertEqual([f for _, f in aktionen.offene_uploads(self.con, self.konfig)], [["youtube", "tiktok"]])

        antwort, _ = aktionen.plattform_erledigt(self.con, cid, "youtube", self.konfig)
        self.assertEqual(db.clip(self.con, cid)["status"], "freigegeben")
        antwort, stand = aktionen.plattform_erledigt(self.con, cid, "tiktok", self.konfig)
        self.assertIn("veröffentlicht", antwort.hinweis)
        self.assertEqual(db.clip(self.con, cid)["status"], "veroeffentlicht")
        self.assertEqual(aktionen.offene_uploads(self.con, self.konfig), [])
        self.assertEqual(stand["clipbattle"], False)  # optional, blockiert nichts

    def test_link_erkennt_plattform(self):
        self.assertEqual(aktionen.plattform_aus_url("https://youtube.com/shorts/abc"), "youtube")
        self.assertEqual(aktionen.plattform_aus_url("https://www.tiktok.com/@x/video/1"), "tiktok")
        self.assertIsNone(aktionen.plattform_aus_url("http://youtube.com/shorts/abc"))  # nur https
        self.assertIsNone(aktionen.plattform_aus_url("https://youtube.com.boese.de/x"))
        cid = self.clip_anlegen(status="freigegeben")
        _, stand = aktionen.link_speichern(self.con, cid, "https://youtu.be/abc", self.konfig)
        self.assertTrue(stand["youtube"])
        url = self.con.execute("SELECT url FROM veroeffentlichungen WHERE plattform = 'youtube'").fetchone()[0]
        self.assertEqual(url, "https://youtu.be/abc")

    def test_nach_freigabe_gibt_es_den_paket_knopf(self):
        cid = self.clip_anlegen()
        antwort = aktionen.entscheide(self.con, cid, "freigegeben")
        self.assertIn(f"p:{cid}", [d for reihe in antwort.knoepfe for _, d in reihe])

    def test_erinnerung_nur_einmal_pro_tag(self):
        from clip_pipeline.bot import app as bot_app

        cid = self.clip_anlegen(status="freigegeben")
        self.con.execute("UPDATE clips SET entschieden = ? WHERE id = ?", (iso(jetzt() - timedelta(hours=30)), cid))
        nachrichten = []

        async def send_message(chat, text, **_):
            nachrichten.append(text)

        fake = SimpleNamespace(bot_data={"con": self.con, "konfig": self.konfig, "erlaubt": 1},
                               bot=SimpleNamespace(send_message=send_message))
        self.assertTrue(asyncio.run(bot_app.erinnere(fake)))
        self.assertFalse(asyncio.run(bot_app.erinnere(fake)))
        self.assertIn("YouTube Shorts, TikTok", nachrichten[0])


class Migration(MitSpeicher):
    def test_alte_datenbank_bekommt_neue_spalte(self):
        pfad = self.tmp / "alt.db"
        alt = db.verbinde(pfad)
        alt.execute("ALTER TABLE clips DROP COLUMN short_pfad")  # so sah die erste Version aus
        alt.close()
        con = db.verbinde(pfad)
        self.assertIn("short_pfad", {z["name"] for z in con.execute("PRAGMA table_info(clips)")})
        con.close()


@unittest.skipUnless(HAT_FFMPEG, "ffmpeg fehlt")
class Short(MitSpeicher):
    def test_hochformat_mit_endcard(self):
        clip = testvideo(self.tmp / "clip.mp4", dauer=6, tonspuren=2)
        ziel = self.tmp / "short.mp4"
        groesse = shorts.rendere(clip, ziel, self.konfig)
        info = medien.probe(ziel)
        self.assertEqual((info.breite, info.hoehe, len(info.tonspuren)), (1080, 1920, 1))
        self.assertAlmostEqual(info.dauer_s, 6 + 2.5 - 0.5, delta=0.2)
        self.assertLess(groesse, 49_000_000)

    def test_mit_cam_braucht_koordinaten(self):
        clip = testvideo(self.tmp / "clip.mp4", dauer=2, tonspuren=0)
        with self.assertRaises(medien.MedienFehler):
            shorts.rendere(clip, self.tmp / "s.mp4", self.konfig, layout="mit-cam")
        self.konfig.daten["shorts"]["cam"] = {"x": 0, "y": 0, "b": 200, "h": 150}
        shorts.rendere(clip, self.tmp / "s.mp4", self.konfig, layout="mit-cam")
        self.assertEqual(medien.probe(self.tmp / "s.mp4").hoehe, 1920)


if __name__ == "__main__":
    unittest.main()
