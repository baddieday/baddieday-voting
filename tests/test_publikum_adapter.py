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
        self.konfig = Konfig({"publikum": {"alter_tage": 7, "mindest_alter_tage": 3},
                              "datenbank": {"pfad": str(Path(self._tmp.name) / "test.db")}}, Path("test.toml"))
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
        meldung = self.meldung("publikum:zuordnung:2026-09-29")
        self.assertIn(f"#{pid} → https://www.tiktok.com/@t/video/555", meldung)   # B10: nachprüfbar, korrigierbar
        self.assertIn("/link 8 ", meldung)                                         # Entwurfsnummer, nicht Post-Nummer
        with patch.object(adapter, "_token", return_value="t"), \
                patch.object(adapter, "_json", side_effect=adapter.AdapterFehler("API HTTP 403")) as api, \
                self.assertLogs("pipeline", "WARNING"):
            self.con.execute("UPDATE posts SET video_id = NULL WHERE id = ?", (pid,))
            result = adapter.abrufen(self.con, self.konfig, self.zeit + timedelta(hours=8))
        self.assertEqual((result["ohne_id"], result["fehler"]), (1, 1))         # Liste gesperrt: kein Absturz
        self.assertEqual(api.call_count, 1)                                     # … und kein weiterer Aufruf

    def meldung(self, schluessel: str) -> str | None:
        zeile = self.con.execute("SELECT text FROM lern_meldungen WHERE schluessel = ?", (schluessel,)).fetchone()
        return zeile[0] if zeile else None

    def test_mehrdeutig_fragt_statt_zu_raten(self):
        # B10: zwei Entwürfe gleicher Länge zur selben Zeit gepostet, zwei passende Videos – wer ist wer? Du sagst es.
        a, b = self.post(31, tage=1, dauer=45), self.post(32, tage=1, dauer=45)
        gepostet = (self.zeit - timedelta(days=1)).timestamp()
        liste = {"data": {"videos": [
            {"id": "701", "create_time": gepostet - 300, "duration": 45, "share_url": "https://www.tiktok.com/@t/video/701"},
            {"id": "702", "create_time": gepostet - 900, "duration": 45, "share_url": "https://www.tiktok.com/@t/video/702"},
        ], "has_more": False}, "error": {"code": "ok"}}
        with patch.object(adapter, "_token", return_value="t"), patch.object(adapter, "_json", side_effect=self.api({}, liste)):
            result = adapter.abrufen(self.con, self.konfig, self.zeit)
        self.assertEqual((result["zugeordnet"], result["mehrdeutig"], result["ohne_id"]), (0, 2, 2))
        meldung = self.meldung("publikum:zuordnung:2026-09-29")
        self.assertIn("https://www.tiktok.com/@t/video/701", meldung)
        self.assertIn("https://www.tiktok.com/@t/video/702", meldung)
        self.assertIn("Entwurf 31", meldung)
        self.assertIsNone(self.con.execute("SELECT video_id FROM posts WHERE id = ?", (a,)).fetchone()[0])
        with patch.object(adapter, "_token", return_value="t"), patch.object(adapter, "_json", side_effect=self.api({}, liste)):
            adapter.abrufen(self.con, self.konfig, self.zeit + timedelta(hours=8))
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM lern_meldungen WHERE schluessel LIKE 'publikum:zuordnung:%'")
                         .fetchone()[0], 1)                                      # einmal am Tag

    def test_fremdes_video_ohne_post_wird_gemeldet(self):
        # B21: ein Video auf TikTok, zu dem kein Post gehört – vermutlich vergessen, /link zu schicken
        pid = self.post(41, tage=1.5, dauer=45)
        jetzt_s = self.zeit.timestamp()
        liste = {"data": {"videos": [
            {"id": "801", "create_time": jetzt_s - 30 * 3600, "duration": 58, "share_url": "https://www.tiktok.com/@t/video/801"},
            {"id": "802", "create_time": jetzt_s - 2 * 3600, "duration": 58, "share_url": "https://www.tiktok.com/@t/video/802"},
            {"id": "803", "create_time": jetzt_s - 36.5 * 3600, "duration": 45, "share_url": "https://www.tiktok.com/@t/video/803"},
        ], "has_more": False}, "error": {"code": "ok"}}
        videos = {"803": {"id": "803", "view_count": 50, "like_count": 5, "comment_count": 0, "share_count": 0},
                  "123456789": self.payload["data"]["videos"][0]}
        fake = self.api(videos, liste)
        with patch.object(adapter, "_token", return_value="t"), patch.object(adapter, "_json", side_effect=fake):
            result = adapter.abrufen(self.con, self.konfig, self.zeit)
        self.assertEqual((result["ohne_post"], result["zugeordnet"]), (1, 1))   # 802 zu jung, 803 im selben Lauf zugeordnet
        meldung = self.meldung("publikum:ohne-post:801")
        self.assertIn("https://www.tiktok.com/@t/video/801", meldung)
        self.assertIn("vor 30 h", meldung)
        self.assertIsNone(self.meldung("publikum:ohne-post:802"))
        self.assertIsNone(self.meldung("publikum:ohne-post:803"))
        self.assertEqual(self.con.execute("SELECT video_id FROM posts WHERE id = ?", (pid,)).fetchone()[0], "803")
        with patch.object(adapter, "_token", return_value="t"), patch.object(adapter, "_json", side_effect=fake):
            adapter.abrufen(self.con, self.konfig, self.zeit + timedelta(hours=8))
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM lern_meldungen WHERE schluessel LIKE 'publikum:ohne-post:%'")
                         .fetchone()[0], 1)
        # Listenabruf scheitert → fehler 1, TikTok für den Lauf gesperrt (die Posts zählen ohne_zugang)
        with patch.object(adapter, "_token", return_value="t"), \
                patch.object(adapter, "_json", side_effect=adapter.AdapterFehler("API HTTP 429: rate_limit_exceeded",
                                                                                 code="rate_limit_exceeded")) as api, \
                self.assertLogs("pipeline", "WARNING"):
            result = adapter.abrufen(self.con, self.konfig, self.zeit + timedelta(hours=16))
        self.assertEqual((result["fehler"], result["ohne_zugang"], api.call_count), (1, 2, 1))

    def post(self, ziel_id: int, *, tage: float = 3, dauer: float = 65, video_id: str | None = None) -> int:
        """Ein TikTok-Post von vor `tage` Tagen; mit video_id auch gleich der Link dazu."""
        pid, _ = publikum.post_anlegen(self.con, art="entwurf", ziel_id=ziel_id, plattform="tiktok",
            daten={"dauer_s": dauer, "format": "short", "rezept": {}, "merkmale": {}},
            zeit=self.zeit - timedelta(days=tage))
        if video_id:
            publikum.link_nachtragen(self.con, pid, f"https://www.tiktok.com/@test/video/{video_id}")
        return pid

    @staticmethod
    def api(videos: dict, liste: dict | None = None):
        """_json-Ersatz: die Liste für /video/list/, für /video/query/ je Block die gefragten Videos aus `videos`."""
        aufrufe = []

        def fake(url, token=None, daten=None, **kw):
            if "/video/list/" in url:
                return liste or {"data": {"videos": [], "has_more": False}, "error": {"code": "ok"}}
            ids = daten["filters"]["video_ids"]
            aufrufe.append(list(ids))
            return {"data": {"videos": [videos[i] for i in ids if i in videos]}, "error": {"code": "ok"}}
        fake.aufrufe = aufrufe
        return fake

    def lernstaende(self) -> int:
        return self.con.execute("SELECT COUNT(*) FROM lernstaende").fetchone()[0]

    def test_nullen_werden_zurueckgehalten(self):
        # B6: Sandbox liefert 0/0/0/0 – das ist keine Messung und darf den Screenshot (mit Wiedergabezeit) nicht verdrängen
        publikum.speichere_messung(self.con, self.pid, {"views": 1240, "likes": 40, "wiedergabe_s": 12.5}, "screenshot",
                                   zeit=self.zeit - timedelta(days=3) + timedelta(days=6.5), konfig=self.konfig)
        vorher = self.lernstaende()
        nullen = {"data": {"videos": [{"id": "123456789", "view_count": 0, "like_count": 0, "comment_count": 0,
                                       "share_count": 0}]}, "error": {"code": "ok"}}
        tag7 = self.zeit - timedelta(days=3) + timedelta(days=7)
        with self.assertRaisesRegex(adapter.Zurueckgehalten, "nicht gespeichert"):
            adapter.importiere(self.con, self.konfig, self.pid, nullen, zeit=tag7)
        self.assertTrue(issubclass(adapter.Zurueckgehalten, adapter.AdapterFehler))
        messungen = self.con.execute("SELECT * FROM publikum_messungen WHERE post_id = ?", (self.pid,)).fetchall()
        self.assertEqual((len(messungen), self.lernstaende()), (1, vorher))
        gewaehlt = publikum.waehle_messung(publikum.post(self.con, self.pid), messungen, self.konfig)
        self.assertEqual((gewaehlt["quelle"], gewaehlt["views"]), ("screenshot", 1240))
        # gesunkene Zähler sind erlaubt (acd7fb1: die API darf nach unten korrigieren)
        kleiner = {"data": {"videos": [{"id": "123456789", "view_count": 900, "like_count": 30, "comment_count": 0,
                                        "share_count": 0}]}, "error": {"code": "ok"}}
        self.assertIsNotNone(adapter.importiere(self.con, self.konfig, self.pid, kleiner, zeit=tag7))
        # im Abruf: zählt zurueckgehalten, kein Fehler, kein Exit 1
        nullen_video = {"123456789": nullen["data"]["videos"][0]}
        with patch.object(adapter, "_token", return_value="t"), patch.object(adapter, "_json", side_effect=self.api(nullen_video)), \
                self.assertLogs("pipeline", "INFO") as logs:
            result = adapter.abrufen(self.con, self.konfig, tag7 + timedelta(hours=8))
        self.assertEqual((result["zurueckgehalten"], result["fehler"], result["gespeichert"]), (1, 0, 0))
        self.assertTrue(any("zurückgehalten" in z for z in logs.output), logs.output)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM publikum_messungen").fetchone()[0], 2)

    def test_bloecke_von_20_ids(self):
        # B7: 45 Posts → 3 Anfragen (20 + 20 + 5) statt 45
        videos = {"123456789": self.payload["data"]["videos"][0]}
        for i in range(44):
            vid = str(900_000 + i)
            self.post(100 + i, video_id=vid, tage=2 + i % 5)
            videos[vid] = {"id": vid, "view_count": 100 + i, "like_count": 3, "comment_count": 0, "share_count": 1}
        fake = self.api(videos)
        with patch.object(adapter, "_token", return_value="t"), patch.object(adapter, "_json", side_effect=fake):
            result = adapter.abrufen(self.con, self.konfig, self.zeit)
        self.assertEqual((result["gespeichert"], result["fehler"], result["meldung"]), (45, 0, False))
        self.assertEqual([len(ids) for ids in fake.aufrufe], [20, 20, 5])
        self.assertEqual(sorted(i for ids in fake.aufrufe for i in ids), sorted(videos))

    def test_blockfehler_sperrt_den_lauf_und_meldet_einmal(self):
        videos = {}
        for i in range(44):
            vid = str(900_000 + i)
            self.post(100 + i, video_id=vid, tage=2)
        fehler = adapter.AdapterFehler("API HTTP 429: rate_limit_exceeded", code="rate_limit_exceeded")
        liste = {"data": {"videos": [], "has_more": False}, "error": {"code": "ok"}}

        def api(url, token=None, daten=None, **kw):
            if "/video/list/" in url:
                return liste
            videos.setdefault("aufrufe", []).append(1)
            raise fehler

        with patch.object(adapter, "_token", return_value="t"), patch.object(adapter, "_json", side_effect=api), \
                self.assertLogs("pipeline", "WARNING") as logs:
            result = adapter.abrufen(self.con, self.konfig, self.zeit)
        self.assertEqual((len(videos["aufrufe"]), result["fehler"], result["ohne_zugang"]), (1, 1, 45))
        self.assertIn("429", result["grund"])
        self.assertTrue(any("API HTTP 429" in z for z in logs.output), logs.output)
        self.assertTrue(result["meldung"])
        meldung = self.meldung("publikum:api:2026-09-29")
        self.assertIn("morgen wieder", meldung)
        self.assertIn("45 Posts ohne Zahlen", meldung)
        with patch.object(adapter, "_token", return_value="t"), patch.object(adapter, "_json", side_effect=api), \
                self.assertLogs("pipeline", "WARNING"):
            result = adapter.abrufen(self.con, self.konfig, self.zeit + timedelta(hours=1))
        self.assertFalse(result["meldung"])
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM lern_meldungen WHERE schluessel LIKE 'publikum:api:%'")
                         .fetchone()[0], 1)

    def test_nicht_verbunden_wird_einmal_gemeldet(self):
        # B1/B5: Key und Secret da, aber nie /tiktok gemacht – statt stiller ohne_zugang-Zähler ein Hinweis im Lern-Bot
        with mock.patch.dict(os.environ, ZUGANG), patch.object(adapter, "_json") as api, \
                self.assertLogs("pipeline", "WARNING") as logs:
            result = adapter.abrufen(self.con, self.konfig, self.zeit)
        api.assert_not_called()
        self.assertEqual((result["ohne_zugang"], result["fehler"], result["nicht_verbunden"], result["meldung"]),
                         (1, 0, True, True))
        self.assertTrue(any("nicht verbunden" in z for z in logs.output), logs.output)
        self.assertIn("/tiktok", self.meldung("publikum:api:2026-09-29"))
        # ohne Key/Secret (setUp): still – wer die API gar nicht nutzt, bekommt keine Erinnerung
        with patch.object(adapter, "_json") as api:
            result = adapter.abrufen(self.con, self.konfig, self.zeit + timedelta(days=1))
        self.assertEqual((result["ohne_zugang"], result["nicht_verbunden"], result["meldung"]), (1, False, False))
        self.assertIsNone(self.meldung("publikum:api:2026-09-30"))
        # Token ungültig (401) → kein „nicht verbunden“, sondern ⚠️ mit Rat
        fehler = adapter.AdapterFehler("API HTTP 401", code="access_token_invalid")
        with patch.object(adapter, "_token", side_effect=fehler), self.assertLogs("pipeline", "WARNING"):
            result = adapter.abrufen(self.con, self.konfig, self.zeit + timedelta(days=2))
        self.assertEqual((result["fehler"], result["nicht_verbunden"], result["ohne_zugang"]), (1, False, 1))
        meldung = self.meldung("publikum:api:2026-10-01")
        self.assertTrue(meldung.startswith("⚠️"), meldung)
        self.assertIn("/tiktok neu verbinden", meldung)

    def test_video_nicht_gefunden_ist_kein_fehler(self):
        # B17: gelöschtes oder privates Video → Hinweis einmal, Exit bleibt 0, der Nachbar im Block wird gespeichert
        pid2 = self.post(51, video_id="555", tage=2)
        videos = {"555": {"id": "555", "view_count": 10, "like_count": 1, "comment_count": 0, "share_count": 0}}
        with patch.object(adapter, "_token", return_value="t"), patch.object(adapter, "_json", side_effect=self.api(videos)), \
                self.assertLogs("pipeline", "WARNING") as logs:
            result = adapter.abrufen(self.con, self.konfig, self.zeit)
        self.assertEqual((result["fehler"], result["nicht_gefunden"], result["gespeichert"]), (0, 1, 1))
        self.assertTrue(any(f"#{self.pid}" in z and "auffindbar" in z for z in logs.output), logs.output)
        self.assertIn("/link 7 ", self.meldung(f"publikum:nicht-gefunden:{self.pid}"))
        self.assertIsNotNone(publikum.letzte_messung(self.con, pid2))
        with patch.object(adapter, "_token", return_value="t"), patch.object(adapter, "_json", side_effect=self.api(videos)), \
                self.assertLogs("pipeline", "WARNING"):
            result = adapter.abrufen(self.con, self.konfig, self.zeit + timedelta(hours=8))
        self.assertEqual(result["nicht_gefunden"], 1)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM lern_meldungen WHERE schluessel LIKE 'publikum:nicht-gefunden:%'")
                         .fetchone()[0], 1)

    def test_unbewertete_posts_zuerst(self):
        # B23: 150 Posts, Platz für 100 – die 50 noch unbewerteten müssen jedes Mal dabei sein
        self.konfig.daten["publikum"]["api_max_posts"] = 100
        videos, unbewertet = {"123456789": self.payload["data"]["videos"][0]}, {str(123456789)}
        with autonom.gebuendelt():   # 100 Messungen anlegen, ohne 100-mal zu lernen
            for i in range(100):
                vid = str(700_000 + i)
                pid = self.post(200 + i, video_id=vid, tage=20)
                videos[vid] = {"id": vid, "view_count": 500, "like_count": 20, "comment_count": 1, "share_count": 2}
                publikum.speichere_messung(self.con, pid, {"views": 500, "likes": 20, "kommentare": 1, "shares": 2}, "api",
                                           zeit=self.zeit - timedelta(days=10), konfig=self.konfig)
                self.con.execute("UPDATE posts SET bewertet_utc = ?, score = 0 WHERE id = ?", (iso(self.zeit), pid))
            for i in range(49):
                vid = str(800_000 + i)
                self.post(300 + i, video_id=vid, tage=3)
                videos[vid] = {"id": vid, "view_count": 50 + i, "like_count": 2, "comment_count": 0, "share_count": 0}
                unbewertet.add(vid)
        for lauf in (self.zeit, self.zeit + timedelta(hours=8)):
            fake = self.api(videos)
            with patch.object(adapter, "_token", return_value="t"), patch.object(adapter, "_json", side_effect=fake):
                result = adapter.abrufen(self.con, self.konfig, lauf)
            gefragt = {i for ids in fake.aufrufe for i in ids}
            self.assertEqual(len(gefragt), 100)
            self.assertTrue(unbewertet <= gefragt, unbewertet - gefragt)
            self.assertEqual(result["fehler"], 0)

    def test_intervall_zaehler_und_deaktiviert(self):
        # B12: ein Post mit frischer API-Messung wird nicht angefragt – und steht als Zahl in der JSON-Zeile
        pid2 = self.post(61, video_id="555", tage=2)
        publikum.speichere_messung(self.con, pid2, {"views": 10}, "api", zeit=self.zeit - timedelta(hours=2), konfig=self.konfig)
        videos = {"123456789": self.payload["data"]["videos"][0]}
        fake = self.api(videos)
        with patch.object(adapter, "_token", return_value="t"), patch.object(adapter, "_json", side_effect=fake):
            result = adapter.abrufen(self.con, self.konfig, self.zeit)
        self.assertEqual((result["uebersprungen_intervall"], result["gespeichert"]), (1, 1))
        self.assertEqual(fake.aufrufe, [["123456789"]])
        schluessel = {"gespeichert", "unveraendert", "zurueckgehalten", "nicht_gefunden", "ohne_zugang", "ohne_id",
                      "zugeordnet", "mehrdeutig", "ohne_post", "uebersprungen_intervall", "fehler", "grund",
                      "anmeldung_endet_tage", "nicht_verbunden", "zugang_ungueltig", "meldung", "deaktiviert"}
        self.assertEqual(set(result), schluessel)                                # JSON-Zeile stabil
        self.konfig.daten["publikum"]["api_abruf"] = False
        with patch.object(adapter, "_json") as api:
            result = adapter.abrufen(self.con, self.konfig, self.zeit)
        api.assert_not_called()
        self.assertTrue(result.pop("deaktiviert"))
        self.assertEqual(set(result), schluessel - {"deaktiviert"})
        self.assertFalse(any(result[k] for k in result))                         # alle Zähler 0 / False / None

    def test_gebuendelt_lernt_einmal_am_ende(self):
        # B22: der Timer-Lauf importiert bis zu 100 Posts – eine lernstaende-Version je Lauf, nicht je Import
        with autonom.gebuendelt():
            self.assertEqual(autonom.aktualisieren(self.con, self.konfig), {"geaendert": False, "aufgeschoben": True})
            adapter.importiere(self.con, self.konfig, self.pid, self.payload, zeit=self.zeit)
            self.assertEqual(self.lernstaende(), 0)
        self.assertTrue(autonom.aktualisieren(self.con, self.konfig)["geaendert"])
        self.assertEqual(self.lernstaende(), 1)
        videos = {}
        for ziel_id, vid, views in ((21, "201", 500), (22, "202", 7000), (23, "203", 150)):
            self.post(ziel_id, video_id=vid, tage=4 + ziel_id % 3)
            videos[vid] = {"id": vid, "view_count": views, "like_count": views // 10, "comment_count": 1, "share_count": 2}
        videos["123456789"] = {**self.payload["data"]["videos"][0], "view_count": 9000}
        vorher = self.lernstaende()
        with patch.object(adapter, "_token", return_value="t"), patch.object(adapter, "_json", side_effect=self.api(videos)):
            result = adapter.abrufen(self.con, self.konfig, self.zeit + timedelta(hours=8))
        self.assertEqual(result["gespeichert"], 4)
        self.assertEqual(self.lernstaende(), vorher + 1)


class Token(MitSpeicher):
    """_token für TikTok mit einer Anmeldung per /tiktok (seed anmeldung) in der privaten Token-Datei."""

    def setUp(self):
        super().setUp()
        self.zeit = datetime(2026, 9, 29, 12, tzinfo=UTC)
        umgebung = mock.patch.dict(os.environ, ZUGANG)
        umgebung.start()
        self.addCleanup(umgebung.stop)
        self.pfad = adapter.cache_pfad(self.konfig)

    def cache(self, **extra) -> dict:
        daten = {"client_key": "key123", "seed": adapter.ANMELDUNG, "access_token": "act", "refresh_token": "rft",
                 "expires_at": (self.zeit + timedelta(hours=20)).timestamp(), "scope": "user.info.basic,video.list",
                 "open_id": "o1", "display_name": "Baddie", **extra}
        adapter.schreibe_cache(self.pfad, daten)
        return daten

    def test_anmeldung_endet_tage(self):
        self.assertIsNone(adapter.anmeldung_endet_tage({}, self.zeit))                           # Alt-Cache
        in_zehn = (self.zeit + timedelta(days=10, hours=5)).timestamp()
        self.assertEqual(adapter.anmeldung_endet_tage({"refresh_expires_at": in_zehn}, self.zeit), 10)
        vorbei = (self.zeit - timedelta(hours=1)).timestamp()
        self.assertEqual(adapter.anmeldung_endet_tage({"refresh_expires_at": vorbei}, self.zeit), -1)

    def test_scope_ohne_video_list_sperrt_ohne_netz(self):
        # B18: Zustimmung ohne video.list → klarer Hinweis statt täglich HTTP 403
        self.cache(scope="user.info.basic")
        with patch.object(adapter, "_json") as api:
            with self.assertRaisesRegex(adapter.AdapterFehler, "video.list") as cm:
                adapter._token("tiktok", self.konfig, self.zeit)
        api.assert_not_called()
        self.assertEqual(cm.exception.code, "scope_not_authorized")
        self.assertTrue(issubclass(adapter.AnmeldungUngueltig, adapter.AdapterFehler))

    def test_abgelaufene_anmeldung_wirft_ohne_netz(self):
        # B3: refresh_expires_at in der Vergangenheit – ein Refresh wäre sinnlos, nur /tiktok hilft
        self.cache(refresh_expires_at=(self.zeit - timedelta(days=1)).timestamp())
        with patch.object(adapter, "_json") as api:
            with self.assertRaisesRegex(adapter.AnmeldungUngueltig, "/tiktok"):
                adapter._token("tiktok", self.konfig, self.zeit)
        api.assert_not_called()

    def test_refresh_fuehrt_ablauf_scope_und_namen_fort(self):
        alt_ablauf = (self.zeit + timedelta(days=100)).timestamp()
        self.cache(expires_at=(self.zeit - timedelta(minutes=1)).timestamp(), refresh_expires_at=alt_ablauf)
        antwort = {"access_token": "act.2", "refresh_token": "rft.2", "expires_in": 86400}
        with patch.object(adapter, "_json", return_value=antwort) as api:
            self.assertEqual(adapter._token("tiktok", self.konfig, self.zeit), "act.2")
        self.assertEqual(api.call_args.kwargs["daten"]["refresh_token"], "rft")
        cache = adapter.lies_cache(self.pfad)
        self.assertEqual(cache["refresh_expires_at"], alt_ablauf)                       # ohne refresh_expires_in: bleibt
        self.assertEqual((cache["display_name"], cache["scope"], cache["refresh_token"]),
                         ("Baddie", "user.info.basic,video.list", "rft.2"))
        self.assertNotIn("act", self.pfad.read_text(encoding="utf-8").replace("act.2", ""))   # alter Token weg
        # mit refresh_expires_in und neuem Scope → beides neu
        antwort = {**antwort, "access_token": "act.3", "refresh_expires_in": 31536000,
                   "scope": "user.info.basic,video.list,user.info.profile"}
        spaeter = self.zeit + timedelta(days=2)
        with patch.object(adapter, "_json", return_value=antwort):
            self.assertEqual(adapter._token("tiktok", self.konfig, spaeter), "act.3")
        cache = adapter.lies_cache(self.pfad)
        self.assertEqual(cache["refresh_expires_at"], spaeter.timestamp() + 31536000)
        self.assertEqual(cache["scope"], "user.info.basic,video.list,user.info.profile")
        self.assertEqual(adapter.anmeldung_endet_tage(cache, spaeter), 365)

    def test_refresh_scheitert_heisst_anmeldung_ungueltig(self):
        self.cache(expires_at=(self.zeit - timedelta(minutes=1)).timestamp())
        fehler = adapter.AdapterFehler("API HTTP 400: invalid_grant", code="invalid_grant")
        with patch.object(adapter, "_json", side_effect=fehler):
            with self.assertRaisesRegex(adapter.AnmeldungUngueltig, "invalid_grant.*\\/tiktok") as cm:
                adapter._token("tiktok", self.konfig, self.zeit)
        self.assertEqual(cm.exception.code, "invalid_grant")

    def test_netzfehler_beim_refresh_ist_keine_ungueltige_anmeldung(self):
        # Prüfer-Befund 06.10.: Netzaussetzer, 429 und 5xx sind kein Widerruf – nächster Lauf versucht es wieder
        self.cache(expires_at=(self.zeit - timedelta(minutes=1)).timestamp(),
                   refresh_expires_at=(self.zeit + timedelta(days=200)).timestamp())
        for text in ("API nicht erreichbar", "API HTTP 429: rate_limit_exceeded", "API HTTP 503",
                     "API liefert kein gültiges JSON"):
            with self.subTest(text):
                with patch.object(adapter, "_json", side_effect=adapter.AdapterFehler(text)):
                    with self.assertRaisesRegex(adapter.AdapterFehler, "nicht erneuert") as cm:
                        adapter._token("tiktok", self.konfig, self.zeit)
                self.assertNotIsInstance(cm.exception, adapter.AnmeldungUngueltig)
        self.assertEqual(adapter.lies_cache(self.pfad)["refresh_token"], "rft")   # Anmeldung unangetastet
        # fachliche Ablehnung bleibt eine ungültige Anmeldung
        with patch.object(adapter, "_json", side_effect=adapter.AdapterFehler("API HTTP 401: access_token_invalid",
                                                                              code="access_token_invalid")):
            with self.assertRaises(adapter.AnmeldungUngueltig):
                adapter._token("tiktok", self.konfig, self.zeit)

    def test_sperre_zweites_lesen_spart_den_aufruf(self):
        # B20: Bot und Timer erneuern nicht gleichzeitig – in der Sperre noch einmal lesen
        abgelaufen = self.cache(expires_at=(self.zeit - timedelta(minutes=1)).timestamp())
        frisch = {**abgelaufen, "access_token": "act.frisch", "expires_at": (self.zeit + timedelta(hours=23)).timestamp()}
        with patch.object(adapter, "lies_cache", side_effect=[abgelaufen, frisch]), patch.object(adapter, "_json") as api:
            self.assertEqual(adapter._token("tiktok", self.konfig, self.zeit), "act.frisch")
        api.assert_not_called()
        # Sperre belegt (anderer Lauf erneuert gerade) → nach warten_s aufgeben, verständlicher Fehler
        from clip_pipeline.sperre import sperre
        with sperre(self.pfad.with_suffix(".lock")), patch.object(adapter, "SPERRE_WARTEN_S", 0), \
                patch.object(adapter, "_json") as api:
            with self.assertRaisesRegex(adapter.AdapterFehler, "gerade erneuert"):
                adapter._token("tiktok", self.konfig, self.zeit)
        api.assert_not_called()

    # --- im Abruf -----------------------------------------------------------------------------------------------

    LEER = {"data": {"videos": [], "has_more": False}, "error": {"code": "ok"}}

    def tiktok_post(self) -> int:
        pid, _ = publikum.post_anlegen(self.con, art="entwurf", ziel_id=7, plattform="tiktok",
            daten={"dauer_s": 65, "format": "short", "rezept": {}, "merkmale": {}}, zeit=self.zeit - timedelta(days=3))
        publikum.link_nachtragen(self.con, pid, "https://www.tiktok.com/@test/video/123456789")
        return pid

    def meldungen(self, praefix: str) -> list:
        return [z[0] for z in self.con.execute("SELECT text FROM lern_meldungen WHERE schluessel LIKE ? ORDER BY id",
                                               (praefix + "%",))]

    def test_vorwarnung_vor_dem_ablauf(self):
        # B3: 14 Tage vorher erinnert der Lern-Bot – ohne Erinnerung käme das Ende der Anmeldung (365 Tage) still
        self.tiktok_post()
        self.cache(refresh_expires_at=(self.zeit + timedelta(days=10, hours=3)).timestamp())
        with patch.object(adapter, "_json", return_value=self.LEER):
            result = adapter.abrufen(self.con, self.konfig, self.zeit)
        self.assertEqual((result["anmeldung_endet_tage"], result["zugang_ungueltig"]), (10, False))
        self.assertIn("/tiktok", self.meldungen("publikum:tiktok-ablauf:")[0])
        self.assertEqual(self.meldungen("publikum:api:"), [])
        self.cache(refresh_expires_at=(self.zeit + timedelta(days=30)).timestamp())
        with patch.object(adapter, "_json", return_value=self.LEER):
            result = adapter.abrufen(self.con, self.konfig, self.zeit + timedelta(days=1))
        self.assertEqual((result["anmeldung_endet_tage"], len(self.meldungen("publikum:tiktok-ablauf:"))), (29, 1))

    def test_abgelaufene_anmeldung_im_abruf(self):
        self.tiktok_post()
        self.cache(refresh_expires_at=(self.zeit - timedelta(days=1)).timestamp())
        with patch.object(adapter, "_json") as api, self.assertLogs("pipeline", "WARNING"):
            result = adapter.abrufen(self.con, self.konfig, self.zeit)
        api.assert_not_called()
        self.assertEqual((result["fehler"], result["zugang_ungueltig"], result["ohne_zugang"]), (1, True, 1))
        meldung = self.meldungen("publikum:api:")[0]
        self.assertTrue(meldung.startswith("⛔"), meldung)
        self.assertIn("/tiktok", meldung)

    def test_scope_ohne_video_list_im_abruf(self):
        # B18: statt täglich HTTP 403 ein Hinweis, was im Portal fehlt
        self.tiktok_post()
        self.cache(scope="user.info.basic")
        with patch.object(adapter, "_json") as api, self.assertLogs("pipeline", "WARNING"):
            result = adapter.abrufen(self.con, self.konfig, self.zeit)
        api.assert_not_called()
        self.assertEqual(result["fehler"], 1)
        meldung = self.meldungen("publikum:api:")[0]
        self.assertIn("video.list", meldung)
        self.assertIn("Portal", meldung)

    def test_kaputte_token_datei_im_abruf(self):
        # B19: "[]" in der Token-Datei → fehler 1 statt Absturz des Timers, Datei bleibt, wie sie ist
        self.tiktok_post()
        self.pfad.write_text("[]", encoding="utf-8")
        with patch.object(adapter, "_json") as api, self.assertLogs("pipeline", "WARNING"):
            result = adapter.abrufen(self.con, self.konfig, self.zeit)
        api.assert_not_called()
        self.assertEqual((result["fehler"], result["ohne_zugang"]), (1, 1))
        self.assertEqual(self.pfad.read_text(encoding="utf-8"), "[]")


if __name__ == "__main__":
    unittest.main()
