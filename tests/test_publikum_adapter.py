"""Zwei Integrationsfälle: echter Speicherweg und begrenzter offizieller Abruf."""
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from clip_pipeline import db, publikum, publikum_adapter as adapter
from clip_pipeline.konfig import Konfig
from clip_pipeline.zeit import UTC, iso


class PublikumAdapter(unittest.TestCase):
    def setUp(self):
        self.con = db.verbinde(":memory:")
        self.addCleanup(self.con.close)
        self.zeit = datetime(2026, 9, 29, 12, tzinfo=UTC)
        self.konfig = Konfig({"publikum": {}, "datenbank": {"pfad": ":memory:"}}, Path("test.toml"))
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

    def test_ohne_link_ordnet_selbst_zu(self):
        # 30.09.: Kurzlink (vm.tiktok.com) oder gar kein Link → Zuordnung über die eigene Videoliste (Zeit + Länge)
        pid, _ = publikum.post_anlegen(self.con, art="entwurf", ziel_id=8, plattform="tiktok",
            daten={"dauer_s": 45, "format": "short", "rezept": {}, "merkmale": {}}, zeit=self.zeit - timedelta(days=1))
        publikum.link_nachtragen(self.con, pid, "https://vm.tiktok.com/ZMabc123/")
        gepostet = (self.zeit - timedelta(days=1)).timestamp()
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

        with patch.object(adapter, "_token", return_value="t"), patch.object(adapter, "_json", side_effect=api):
            result = adapter.abrufen(self.con, self.konfig, self.zeit)
        self.assertEqual((result["zugeordnet"], result["ohne_id"], result["gespeichert"]), (1, 0, 2))
        self.assertEqual(self.con.execute("SELECT video_id FROM posts WHERE id = ?", (pid,)).fetchone()[0], "555")
        with patch.object(adapter, "_token", return_value="t"), \
                patch.object(adapter, "_json", side_effect=adapter.AdapterFehler("API HTTP 403")), \
                self.assertLogs("pipeline", "WARNING"):
            self.con.execute("UPDATE posts SET video_id = NULL WHERE id = ?", (pid,))
            result = adapter.abrufen(self.con, self.konfig, self.zeit + timedelta(hours=8))
        self.assertEqual(result["ohne_id"], 1)                                  # Liste gesperrt: kein Absturz


if __name__ == "__main__":
    unittest.main()
