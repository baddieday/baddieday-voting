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
        with patch.object(adapter, "_token", return_value="t"), \
                patch.object(adapter, "_json", side_effect=adapter.AdapterFehler("API HTTP 403")), \
                self.assertLogs("pipeline", "WARNING"):
            self.con.execute("UPDATE posts SET video_id = NULL WHERE id = ?", (pid,))
            result = adapter.abrufen(self.con, self.konfig, self.zeit + timedelta(hours=8))
        self.assertEqual(result["ohne_id"], 1)                                  # Liste gesperrt: kein Absturz

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
        self.assertNotIn("act", open(self.pfad, encoding="utf-8").read().replace("act.2", ""))  # alter Token weg
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


if __name__ == "__main__":
    unittest.main()
