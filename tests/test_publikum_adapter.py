"""Offizieller Abruf (publikum_adapter): Speicherweg, Blöcke, Token-Pflege, Zuordnung und Lern-Meldungen."""
import io
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock
from unittest.mock import patch
from urllib.error import HTTPError

from clip_pipeline import autonom, db, publikum, publikum_adapter as adapter
from clip_pipeline.konfig import Konfig
from clip_pipeline.zeit import UTC, iso
from tests.hilfen import MitSpeicher

ZUGANG = {"TIKTOK_CLIENT_KEY": "key123", "TIKTOK_CLIENT_SECRET": "geheim", "TIKTOK_REFRESH_TOKEN": "",
          "TIKTOK_ACCESS_TOKEN": ""}
OHNE_ZUGANG = {k: "" for k in ZUGANG}


class _Antwort(io.BytesIO):
    """Stellvertreter für die Antwort von build_opener().open(): Kontextmanager mit read()."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _opener(ergebnis):
    """build_opener-Ersatz: open() liefert ergebnis bzw. wirft es, wenn es eine Ausnahme ist."""
    def oeffnen(*a, **kw):
        if isinstance(ergebnis, BaseException):
            raise ergebnis
        return _Antwort(ergebnis)
    return mock.Mock(return_value=mock.Mock(open=oeffnen))


class Fehlertyp(unittest.TestCase):
    def test_http_fehler_nennt_code_und_grund_ohne_log_id(self):
        # B2: Display-API-Form {"error": {"code", "message", "log_id"}} – Code im Text, log_id nirgends
        body = b'{"error":{"code":"access_token_invalid","message":"m","log_id":"L1"}}'
        with patch.object(adapter, "build_opener", _opener(HTTPError("https://x", 401, "x", {}, io.BytesIO(body)))):
            with self.assertRaises(adapter.AdapterFehler) as cm:
                adapter._json("https://open.tiktokapis.com/v2/video/query/", "tok", daten={})
        self.assertEqual((cm.exception.code, str(cm.exception), cm.exception.grund),
                         ("access_token_invalid", "API HTTP 401: access_token_invalid", "m"))
        self.assertNotIn("L1", str(cm.exception) + cm.exception.grund)
        # OAuth-Form {"error": "…", "error_description": "…"}
        body = b'{"error":"invalid_request","error_description":"Redirect_uri or scope is invalid.","log_id":"L2"}'
        with patch.object(adapter, "build_opener", _opener(HTTPError("https://x", 400, "x", {}, io.BytesIO(body)))):
            with self.assertRaises(adapter.AdapterFehler) as cm:
                adapter._json("https://open.tiktokapis.com/v2/oauth/token/", daten={}, formular=True)
        self.assertEqual((cm.exception.code, str(cm.exception)), ("invalid_request", "API HTTP 400: invalid_request"))
        self.assertIn("Redirect_uri", cm.exception.grund)
        # leerer Body → wie bisher
        with patch.object(adapter, "build_opener", _opener(HTTPError("https://x", 401, "x", {}, io.BytesIO(b"")))):
            with self.assertRaises(adapter.AdapterFehler) as cm:
                adapter._json("https://open.tiktokapis.com/v2/video/query/", "tok", daten={})
        self.assertEqual((str(cm.exception), cm.exception.code, cm.exception.grund), ("API HTTP 401", "unbekannt", ""))

    def test_abgelehnt_bei_http_200(self):
        body = b'{"data":{},"error":{"code":"scope_not_authorized","message":"needs video.list","log_id":"L3"}}'
        with patch.object(adapter, "build_opener", _opener(body)):
            with self.assertRaises(adapter.AdapterFehler) as cm:
                adapter._json("https://open.tiktokapis.com/v2/video/list/", "tok", daten={})
        self.assertEqual((str(cm.exception), cm.exception.code, cm.exception.grund),
                         ("API abgelehnt: scope_not_authorized", "scope_not_authorized", "needs video.list"))

    def test_adapterfehler_bereinigt_attribute(self):
        alt = adapter.AdapterFehler("API HTTP 401")                       # message-kompatibel wie bisher
        self.assertEqual((str(alt), alt.code, alt.grund), ("API HTTP 401", "unbekannt", ""))
        krumm = adapter.AdapterFehler("x", code="Bad Code!", grund="a" * 300)
        self.assertEqual((krumm.code, len(krumm.grund)), ("unbekannt", 200))
        self.assertEqual(adapter.AdapterFehler("x", grund="zeile\nzwei").grund, "")  # nicht druckbar → leer

    def test_lies_cache_lehnt_liste_ab(self):
        # B19: eine Token-Datei mit "[]" ist kein Cache – gleicher Fehlertext wie bei ungültigem JSON
        with tempfile.TemporaryDirectory() as tmp:
            pfad = Path(tmp) / "publikum-oauth.json"
            pfad.write_text("[]", encoding="utf-8")
            with self.assertRaisesRegex(adapter.AdapterFehler, "nicht lesbar"):
                adapter.lies_cache(pfad)
            pfad.write_text("{kaputt", encoding="utf-8")
            with self.assertRaisesRegex(adapter.AdapterFehler, "nicht lesbar"):
                adapter.lies_cache(pfad)
            self.assertEqual(adapter.lies_cache(Path(tmp) / "fehlt.json"), {})


class PublikumAdapter(unittest.TestCase):
    def setUp(self):
        self.con = db.verbinde(":memory:")
        self.addCleanup(self.con.close)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        umgebung = mock.patch.dict(os.environ, OHNE_ZUGANG)   # kein Zugang aus der echten Umgebung
        umgebung.start()
        self.addCleanup(umgebung.stop)
        self.zeit = datetime(2026, 9, 29, 12, tzinfo=UTC)
        # datenbank.pfad im Temp-Ordner: sonst zeigte cache_pfad auf ./publikum-oauth.json
        self.konfig = Konfig({"publikum": {}, "datenbank": {"pfad": str(Path(self._tmp.name) / "test.db")}},
                             Path("test.toml"))
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
        werte = {"555": {"id": "555", "view_count": 10, "like_count": 1, "comment_count": 0, "share_count": 0},
                 "123456789": self.payload["data"]["videos"][0]}

        def api(url, token, daten=None, **kw):
            if "/video/list/" in url:
                return liste
            ids = daten["filters"]["video_ids"]                                 # Block-Antwort: nur die gefragten
            return {"data": {"videos": [werte[i] for i in ids if i in werte]}, "error": {"code": "ok"}}

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
