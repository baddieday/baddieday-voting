"""Zwei Integrationsfälle: echter Speicherweg und begrenzter offizieller Abruf."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from clip_pipeline import caption, db, publikum, publikum_adapter as adapter
from clip_pipeline.konfig import Konfig
from clip_pipeline.zeit import UTC, iso


class PublikumAdapter(unittest.TestCase):
    def setUp(self):
        self.con = db.verbinde(":memory:")
        self.addCleanup(self.con.close)
        self.zeit = datetime(2026, 9, 29, 12, tzinfo=UTC)
        self.konfig = Konfig({"publikum": {}, "datenbank": {"pfad": ":memory:"},
                              "caption": {"vorlage": "templates/caption.txt"}}, Path("test.toml"))
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pid, _ = publikum.post_anlegen(self.con, art="entwurf", ziel_id=7, plattform="tiktok",
            daten={"dauer_s": 65, "format": "short", "rezept": {}, "merkmale": {}},
            zeit=self.zeit - timedelta(days=3))
        publikum.link_nachtragen(self.con, self.pid, "https://www.tiktok.com/@test/video/123456789")
        self.payload = {"data": {"videos": [{"id": "123456789", "view_count": 8000, "like_count": 250,
                                             "comment_count": 20, "share_count": 80}]}, "error": {"code": "ok"}}

    def test_import_missing_metrics_idempotent_and_learning_trigger(self):
        mid = adapter.importiere(self.con, self.konfig, self.pid, self.payload, zeit=self.zeit)
        messung = publikum.letzte_messung(self.con, self.pid)
        self.assertEqual(messung["id"], mid)
        self.assertEqual(messung["views"], 8000)
        self.assertIsNone(messung["saves"])
        self.assertIsNone(messung["wiedergabe_s"])
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM lernstaende").fetchone()[0], 1)
        self.assertIsNone(adapter.importiere(self.con, self.konfig, self.pid, self.payload, zeit=self.zeit))
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM publikum_messungen").fetchone()[0], 1)
        youtube = adapter.normalisiere("youtube", {"columnHeaders": [{"name": "views"},
            {"name": "averageViewPercentage"}, {"name": "averageViewDuration"}], "rows": [[8000, 85, 55.25]]})
        self.assertEqual(youtube["retention_prozent"], 85)
        self.assertEqual(youtube["wiedergabe_s"], 55.25)
        self.assertIsNone(youtube["voll_prozent"])
        instagram = adapter.normalisiere("instagram", {"data": [{"name": "saved", "values": [{"value": 7}]}]})
        self.assertEqual(instagram["saves"], 7)
        self.assertIsNone(instagram["views"])
        with self.assertRaises(adapter.AdapterFehler):
            adapter.normalisiere("tiktok", {"view_count": -10})

    def test_automatic_pull_and_api_failure_preserve_data(self):
        with patch.object(adapter, "_token", return_value="test-token"), \
                patch.object(adapter, "_json", return_value=self.payload) as request:
            result = adapter.abrufen(self.con, self.konfig, self.zeit)
        self.assertEqual(result["gespeichert"], 1)
        self.assertTrue(request.call_args.args[0].startswith("https://open.tiktokapis.com/v2/video/query/"))
        self.assertEqual(request.call_args.kwargs["daten"]["filters"]["video_ids"], ["123456789"])
        with patch.object(adapter, "_token", return_value="test-token"), \
                patch.object(adapter, "_json", side_effect=adapter.AdapterFehler("API HTTP 401")), \
                self.assertLogs("pipeline", "WARNING"):
            result = adapter.abrufen(self.con, self.konfig, self.zeit + timedelta(hours=8))
        self.assertEqual(result["fehler"], 1)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM publikum_messungen").fetchone()[0], 1)
        self.assertEqual(publikum.letzte_messung(self.con, self.pid)["gemessen_utc"], iso(self.zeit))

    def test_flop_bekommt_score_an_tag_7(self):
        """08.10. (Stufe 3, Flop): Views 180, 200, danach täglich unverändert 200. Vorher speicherte der Abruf nur Tag 1
        und 2 – nie ein Score. Jetzt zählen gleiche Zahlen einmal am Tag (der Timer streut: 10:00 bzw. 10:10), bis der
        Score steht; danach nicht mehr."""
        self.konfig.daten["publikum"].update(
            alter_tage=7, mindest_alter_tage=3, fenster=20,
            gewichte={"wiedergabe": 0.5, "engagement": 0.3, "reichweite": 0.2},
            mad_minimum={"wiedergabe": 0.05, "engagement": 0.005, "reichweite": 0.1})
        gepostet = self.zeit - timedelta(days=3)                                 # der Post aus setUp
        laeufe = [gepostet + timedelta(days=t, minutes=10 * (t % 2)) for t in range(1, 10)]
        for t, lauf in enumerate(laeufe, start=1):
            antwort = {"data": {"videos": [{"id": "123456789", "view_count": 180 if t == 1 else 200,
                                            "like_count": 9, "comment_count": 1, "share_count": 0}]}}
            with patch.object(adapter, "_token", return_value="t"), \
                    patch.object(adapter, "_json", return_value=antwort):
                adapter.abrufen(self.con, self.konfig, lauf)
            publikum.bewerte_alle(self.con, self.konfig, lauf)
        gemessen = [z[0] for z in self.con.execute("SELECT gemessen_utc FROM publikum_messungen ORDER BY gemessen_utc")]
        self.assertEqual(gemessen, [iso(lauf) for lauf in laeufe[:7]])           # Tag 1–7, nach dem Score keine mehr
        post = publikum.post(self.con, self.pid)
        self.assertEqual((post["score"], post["bewertet_utc"]), (0.0, iso(laeufe[6])))   # 0: Basis zu klein

    def test_gleiche_zahlen_am_selben_tag_nur_einmal(self):
        """Wichtigster Fehlerfall: zwei Abrufe am selben Tag (10:00, 18:00) mit gleichen Zahlen → eine Messung."""
        with patch.object(adapter, "_token", return_value="t"), \
                patch.object(adapter, "_json", return_value=self.payload):
            erst = adapter.abrufen(self.con, self.konfig, self.zeit)
            dann = adapter.abrufen(self.con, self.konfig, self.zeit + timedelta(hours=8))
        self.assertEqual((erst["gespeichert"], dann["gespeichert"], dann["unveraendert"]), (1, 0, 1))
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM publikum_messungen").fetchone()[0], 1)

    def test_junge_posts_zuerst_wenn_der_abruf_voll_ist(self):
        """N43: Ab [publikum].api_max_posts Posts im Fenster nahmen bewertete Posts mit festen Zahlen (speichern nie neu)
        und nie hochgeladene (nie gemessen) den jungen die Plätze – die bekamen keine Messung bis Tag 7 und keinen
        Score. Jetzt kommen Posts ohne Score zuerst, die jüngsten vorn."""
        self.konfig.daten["publikum"]["api_max_posts"] = 2
        alt, _ = publikum.post_anlegen(self.con, art="entwurf", ziel_id=8, plattform="tiktok",
                                       daten={"dauer_s": 45, "format": "short", "rezept": {}, "merkmale": {}},
                                       zeit=self.zeit - timedelta(days=40))
        publikum.link_nachtragen(self.con, alt, "https://www.tiktok.com/@test/video/111")
        messung = "INSERT INTO publikum_messungen (post_id, gemessen_utc, quelle, views, erstellt) VALUES (?, ?, 'api', 5, ?)"
        self.con.execute(messung, (alt, iso(self.zeit - timedelta(days=30)), iso(self.zeit - timedelta(days=30))))
        self.con.execute("UPDATE posts SET score = 0.1, bewertet_utc = ? WHERE id = ?",
                         (iso(self.zeit - timedelta(days=33)), alt))
        publikum.post_anlegen(self.con, art="entwurf", ziel_id=9, plattform="tiktok", zeit=self.zeit - timedelta(days=20),
                              daten={"dauer_s": 45, "format": "short", "rezept": {}, "merkmale": {}})  # nie hochgeladen
        gestern = iso(self.zeit - timedelta(days=1))
        self.con.execute(messung, (self.pid, gestern, gestern))                                 # der junge, gestern gemessen

        def api(url, token, daten=None, **kw):
            if "/video/list/" in url:
                return {"data": {"videos": [], "has_more": False}, "error": {"code": "ok"}}
            return {"data": {"videos": [{"id": daten["filters"]["video_ids"][0], "view_count": 9, "like_count": 1,
                                         "comment_count": 0, "share_count": 0}]}, "error": {"code": "ok"}}

        with patch.object(adapter, "_token", return_value="t"), patch.object(adapter, "_json", side_effect=api):
            result = adapter.abrufen(self.con, self.konfig, self.zeit)
        self.assertEqual((result["gespeichert"], result["ohne_id"]), (1, 1))
        self.assertEqual(publikum.letzte_messung(self.con, self.pid)["gemessen_utc"], iso(self.zeit))

    def test_ohne_link_ordnet_selbst_zu(self):
        # 30.09.: Kurzlink (vm.tiktok.com) oder gar kein Link → Zuordnung über die eigene Videoliste (Zeit + Länge).
        # 08.10.: Ohne passende erste Zeile (hier: Video ohne Beschreibung) erst, wenn das 72-h-Fenster zu ist – bis
        # dahin könnte das echte Video noch kommen. Danach zählt der Post ab dem Upload.
        pid, _ = publikum.post_anlegen(self.con, art="entwurf", ziel_id=8, plattform="tiktok",
            daten={"dauer_s": 45, "format": "short", "rezept": {}, "merkmale": {}}, zeit=self.zeit - timedelta(days=4))
        publikum.link_nachtragen(self.con, pid, "https://vm.tiktok.com/ZMabc123/")
        gepostet = (self.zeit - timedelta(days=4)).timestamp()
        liste = {"data": {"videos": [{"id": "999", "create_time": gepostet - 600, "duration": 20},      # zu kurz
                                     {"id": "555", "create_time": gepostet - 3600, "duration": 45,
                                      "share_url": "https://www.tiktok.com/@t/video/555"},
                                     {"id": "123456789", "create_time": gepostet - 60, "duration": 45}],  # vergeben
                          "has_more": False}, "error": {"code": "ok"}}
        werte = {"data": {"videos": [{"id": "555", "view_count": 10, "like_count": 1, "comment_count": 0,
                                      "share_count": 0}]}, "error": {"code": "ok"}}

        def api(url, token, daten=None, **kw):
            if "/video/list/" in url:
                return liste
            return self.payload if daten["filters"]["video_ids"] == ["123456789"] else werte

        with patch.object(adapter, "_json", side_effect=api):                    # Fenster noch offen: warten
            ohne = self.con.execute("SELECT * FROM posts WHERE id = ?", (pid,)).fetchall()
            frueh = self.zeit - timedelta(days=2)
            self.assertEqual(adapter._tiktok_zuordnen(self.con, self.konfig, "t", ohne, frueh), {})
        with patch.object(adapter, "_token", return_value="t"), patch.object(adapter, "_json", side_effect=api):
            result = adapter.abrufen(self.con, self.konfig, self.zeit)
        self.assertEqual((result["zugeordnet"], result["ohne_id"], result["gespeichert"]), (1, 0, 2))
        self.assertEqual(tuple(publikum.post(self.con, pid)[k] for k in ("video_id", "gepostet_utc")),
                         ("555", iso(datetime.fromtimestamp(gepostet - 3600, UTC))))
        with patch.object(adapter, "_token", return_value="t"), \
                patch.object(adapter, "_json", side_effect=adapter.AdapterFehler("API HTTP 403")), \
                self.assertLogs("pipeline", "WARNING"):
            self.con.execute("UPDATE posts SET video_id = NULL WHERE id = ?", (pid,))
            result = adapter.abrufen(self.con, self.konfig, self.zeit + timedelta(hours=8))
        self.assertEqual(result["ohne_id"], 1)                                  # Liste gesperrt: kein Absturz

    def entwurf_post(self, nr: int, momente: int, zeit: datetime, song: str | None = None) -> tuple[int, str]:
        """Echter Short-Entwurf mit `momente` Szenen und sein TikTok-Post, wie ihn das Paket anlegt (ohne Link).
        Mit song wie im einfachen Modus seit N44: Song in der ersten Zeile, die am Post gespeichert wird; ohne wie
        ältere Posts (die Zeile wird nachgerechnet).
        Rückgabe (post_id, Beschreibung, wie TikTok sie liefert: die Caption des Pakets, höchstens 150 Zeichen)."""
        musik = {"titel": song, "quelle": f"Song: NCS - {song}"} if song else None
        liste = {"dauer_s": 55.4, "musik": musik, "segmente": [{"moment": f"datei:{nr}-{i}"} for i in range(momente)]}
        pfad = Path(self.tmp.name) / f"e{nr}.json"
        pfad.write_text(json.dumps(liste), encoding="utf-8")
        self.con.execute("INSERT INTO entwuerfe (id, name, format, schnittliste, parameter, erstellt)"
                         " VALUES (?, ?, 'short', ?, '{}', ?)", (nr, f"e{nr}", str(pfad), iso(zeit)))
        text = caption.entwurf_caption(self.con, liste, self.konfig, song_in_zeile=bool(song))
        merkmale = {"caption_zeile": text.splitlines()[0]} if song else {}
        pid, _ = publikum.post_anlegen(self.con, art="entwurf", ziel_id=nr, plattform="tiktok", zeit=zeit,
                                       daten={"dauer_s": 55.4, "format": "short", "rezept": {}, "merkmale": merkmale})
        return pid, text[:150]

    def zuordnen(self, videos: list, zeit: datetime) -> dict:
        liste = {"data": {"videos": videos, "has_more": False}, "error": {"code": "ok"}}
        with patch.object(adapter, "_json", return_value=liste):
            ohne = self.con.execute("SELECT * FROM posts WHERE video_id IS NULL").fetchall()
            return adapter._tiktok_zuordnen(self.con, self.konfig, "t", ohne, zeit)

    def test_nur_eindeutige_paare_und_ab_upload(self):
        """08.10. (richter2, Fall R2): ✅A 20:00, ✅B 21:00, gleich lang, verschiedene erste Zeile („4 Momente“ /
        „5 Momente“). Nur A wird am nächsten Tag hochgeladen → A bekommt das Video und zählt ab dem Upload, B bleibt
        offen (die Regeln „jüngster ✅ zuerst“ hätten das Video B gegeben) – auch, wenn danach ein gleich langes
        Video mit ganz anderer Beschreibung kommt."""
        abend = datetime(2026, 9, 25, 20, tzinfo=UTC)
        a, text_a = self.entwurf_post(41, 4, abend)
        b, _ = self.entwurf_post(42, 5, abend + timedelta(hours=1))
        upload = abend + timedelta(hours=16)
        video = {"id": "777", "create_time": int(upload.timestamp()), "duration": 55, "video_description": text_a}
        self.assertEqual(self.zuordnen([video], upload + timedelta(hours=1)), {a: "777"})
        self.assertEqual(tuple(publikum.post(self.con, a)[k] for k in ("video_id", "gepostet_utc")),
                         ("777", iso(upload)))
        self.assertEqual(tuple(publikum.post(self.con, b)[k] for k in ("video_id", "gepostet_utc")),
                         (None, iso(abend + timedelta(hours=1))))
        fremd = {"id": "778", "create_time": int((abend + timedelta(hours=30)).timestamp()), "duration": 55,
                 "video_description": "Mein Setup 2026\n#gaming"}
        self.assertEqual(self.zuordnen([video, fremd], abend + timedelta(days=5)), {})

    def test_zwei_videos_eines_abends_unterscheidet_der_song(self):
        """N44 (Befund Florian-1): Abend-Video und 🎬-Video desselben Abends – gleiche 6 Szenen, fast gleich lang.
        Vorher hatten beide dieselbe erste Zeile, und keins bekam je Zahlen, auch wenn nur eins hochgeladen wurde. Jetzt
        endet sie mit dem Song, und das Paket speichert sie am Post: Ändert ein Update danach die Caption-Rechnung,
        gilt weiter die verschickte Zeile (Befund Korrekt-3)."""
        abend = datetime(2026, 9, 25, 20, tzinfo=UTC)
        a, text_a = self.entwurf_post(41, 6, abend, song="On & On")
        b, text_b = self.entwurf_post(42, 6, abend + timedelta(minutes=2), song="Blank")
        self.assertEqual(text_b.splitlines()[0], "Fortnite-Highlights: 6 Momente · 🎵 Blank")
        upload = abend + timedelta(hours=16)
        video = {"id": "777", "create_time": int(upload.timestamp()), "duration": 55, "video_description": text_b}
        with patch.object(caption, "entwurf_caption", return_value="Fortnite-Highlights: neu formuliert"):
            self.assertEqual(self.zuordnen([video], upload + timedelta(hours=1)), {b: "777"})
        self.assertIsNone(publikum.post(self.con, a)["video_id"])

    def test_gleiche_erste_zeile_bleibt_offen(self):
        """Wichtigster Fehlerfall: zwei gleich lange Posts mit gleicher erster Zeile – ob erst eins der Videos da ist
        oder beide: lieber keine Zahlen als falsche, auch Tage später nicht (die Reihenfolge-Regeln ordneten hier
        falsch zu)."""
        abend = datetime(2026, 9, 25, 20, tzinfo=UTC)
        self.entwurf_post(41, 4, abend)
        _, text = self.entwurf_post(42, 4, abend + timedelta(hours=1))
        videos = [{"id": str(700 + i), "create_time": int((abend + timedelta(hours=16 + i)).timestamp()),
                   "duration": 55, "video_description": text} for i in range(2)]
        for hochgeladen in (videos[1:], videos):
            self.assertEqual(self.zuordnen(hochgeladen, abend + timedelta(days=5)), {})
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM posts WHERE video_id IS NULL").fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main()
