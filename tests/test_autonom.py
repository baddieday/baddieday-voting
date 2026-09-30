"""Neun Kernprüfungen: Publikum -> Modell -> tatsächlich andere Planung."""
import copy
import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from clip_pipeline import audience_score, autonom, publikum, regie, regie_lernen
from clip_pipeline.zeit import UTC, iso
from tests.regie_hilfen import MitRegieMaterial


class Autonom(MitRegieMaterial):
    def setUp(self):
        super().setUp()
        self.start = datetime(2026, 1, 1, tzinfo=UTC)

    def video(self, i, dauer=65., retention=.85, hook=True, platform="tiktok", ziel_id=None):
        t = self.start + timedelta(days=i*10)
        liste = {"format": "short", "dauer_s": dauer,
                 "parameter": {"hook_staerkster": hook, "seg_min_faktor": 1.4}, "segmente": []}
        pid, _ = publikum.post_anlegen(self.con, art="entwurf", ziel_id=ziel_id or i, plattform=platform,
            daten={"dauer_s": dauer, "schnittliste": liste, "merkmale": {"momente": []}, "rezept": {}}, zeit=t)
        mid = publikum.speichere_messung(self.con, pid, {"views": 8000, "wiedergabe_s": dauer*retention,
            "voll_prozent": retention*90, "likes": 320, "shares": int(8000*retention*.04),
            "saves": int(8000*retention*.02)}, "api", zeit=t+timedelta(days=3), konfig=self.konfig)
        return pid, mid

    def lernen(self):
        for i, dauer, retention in ((1, 35, .15), (2, 65, .85), (3, 68, .92), (4, 62, .9)):
            self.video(i, dauer, retention)

    def test_publikum_verschiebt_kurze_shorts_zu_60_bis_70_und_den_plan(self):
        self.konfig.daten.setdefault("regie", {}).setdefault("vorgaben", {})["dauer_faktor"] = 35/45
        vorher, _ = regie_lernen.aktuelle(self.con, self.konfig, "short")
        self.lernen()
        nachher, ziel = regie_lernen.aktuelle(self.con, self.konfig, "short")
        self.assertGreater(nachher["ziel_dauer_s"], vorher["ziel_dauer_s"])
        self.assertTrue(60 <= nachher["ziel_dauer_s"] <= 70, nachher)
        kandidaten = [regie.Kandidat(f"datei:{i}", str(self.tmp/"quelle.mp4"), 20., "episch",
                       20.-i, 20.-i, None, f"m{i}", (4., 12.), (5., 11.5), "Test") for i in range(20)]
        with mock.patch.object(regie, "kandidaten_mit_bericht", return_value=(
                kandidaten, {"ohne_datei": 0, "ersetzt": 0, "gesperrt": 0})):
            e = regie.erstelle(self.con, self.konfig, "short", parameter=nachher, ziel=ziel)
        liste = json.loads(Path(e["datei"]).read_text(encoding="utf-8"))
        self.assertTrue(60 <= liste["dauer_s"] <= 75)
        self.assertGreater(liste["parameter"]["autonom"]["version"], 0)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM entwurf_bewertungen").fetchone()[0], 0)

    def test_hook_lernt_ohne_daumen_aus_erfolg_und_misserfolg(self):
        for i, hook, retention in ((1, False, .15), (2, True, .9), (3, True, .85), (4, True, .9)):
            self.video(i, 65, retention, hook)
        p, _ = regie_lernen.aktuelle(self.con, self.konfig, "short")
        self.assertTrue(p["hook_staerkster"])
        self.assertGreater(p["autonom"]["beitraege"]["hook_staerkster"], 0)

    def test_missing_ist_unbekannt_und_qualitaet_schlaegt_views(self):
        p = {"dauer_s": 60, "gepostet_utc": iso(self.start), "plattform": "tiktok"}
        zeit = iso(self.start+timedelta(days=3))
        schwach = audience_score.berechne(p, {"views": 50000, "wiedergabe_s": 9, "gemessen_utc": zeit})
        stark = audience_score.berechne(p, {"views": 8000, "wiedergabe_s": 51, "gemessen_utc": zeit})
        self.assertGreater(stark["score"], schwach["score"])
        self.assertNotIn("saves", stark["components"])
        self.assertIn("saves", stark["fehlend"])
        self.assertIsNone(audience_score.berechne(p, {"views": 50000})["score"])

    def test_alte_daten_bleiben_viralitaet_hat_begrenztes_gewicht(self):
        self.assertLess(autonom.zeitgewicht(180), autonom.zeitgewicht(1))
        self.assertGreaterEqual(autonom.zeitgewicht(10000), .25)
        e = {"zeit": iso(self.start), "y": 1., "confidence": 1.}
        self.assertLessEqual(autonom._gewicht(e, iso(self.start)), 1.5)
        self.lernen()
        vorher = autonom.champion(self.con)
        self.video(5, 35, 1.)
        nachher = autonom.champion(self.con)
        self.assertTrue(all(abs(w) <= .65 for w in nachher["modell"]["gewichte"].values()))
        self.assertIsNotNone(self.con.execute("SELECT * FROM lernstaende WHERE version=?", (vorher["version"],)).fetchone())

    def test_schlechter_challenger_und_gruppen_holdout(self):
        self.lernen()
        c = autonom.champion(self.con)
        daten = c["modell"]["beispiele"]
        probe = copy.deepcopy(daten[-1])
        probe.update(gruppe="entwurf:999", zeit=iso(self.start+timedelta(days=70)),
                     messzeit=iso(self.start+timedelta(days=73)))
        probe["y"] = autonom.vorhersage(c["modell"], probe["x"])
        v = autonom.validiere(daten+[probe], c["modell"], probe["messzeit"])
        self.assertFalse(v["promote"])
        self.assertFalse(set(v["training"]) & set(v["holdout"]))
        self.assertFalse({e["gruppe"] for e in daten} & set(v["holdout"]))

    def test_messung_wiederholung_und_crosspost_zaehlen_nicht_doppelt(self):
        pid, mid = self.video(1)
        version = self.con.execute("SELECT MAX(version) FROM lernstaende").fetchone()[0]
        self.assertFalse(autonom.aktualisieren(self.con)["geaendert"])
        m = dict(self.con.execute("SELECT * FROM publikum_messungen WHERE id=?", (mid,)).fetchone())
        m["metriken"] = json.loads(m["metriken"])
        again = publikum.speichere_messung(self.con, pid, m, "api", zeit=self.start+timedelta(days=13))
        self.assertEqual(again, mid)
        self.assertEqual(self.con.execute("SELECT MAX(version) FROM lernstaende").fetchone()[0], version)
        self.video(1, platform="youtube", ziel_id=1)
        self.assertEqual(len(autonom._beispiele(self.con)), 1)

    def test_snapshot_bleibt_fest_und_historische_daten_bleiben_erhalten(self):
        pid, _ = self.video(1)
        davor = self.con.execute("SELECT snapshot FROM video_lerndaten WHERE post_id=?", (pid,)).fetchone()[0]
        autonom.snapshot_speichern(self.con, pid, {"dauer_s": 1, "rezept": {}, "merkmale": {}})
        self.assertEqual(davor, self.con.execute("SELECT snapshot FROM video_lerndaten WHERE post_id=?", (pid,)).fetchone()[0])
        self.assertEqual(self.con.execute("SELECT dauer_s FROM posts WHERE id=?", (pid,)).fetchone()[0], 65)
        self.assertEqual(len(json.loads(davor)["schnittliste"]["parameter"]), 2)

    def test_sparse_api_messung_verliert_keine_watchtime(self):
        pid, mid = self.video(1)
        publikum.speichere_messung(self.con, pid, {"views": 10000, "likes": 500, "shares": 100}, "api",
                                  zeit=self.start+timedelta(days=20))
        self.assertEqual(self.con.execute("SELECT messung_id FROM audience_ergebnisse WHERE post_id=?", (pid,)).fetchone()[0], mid)
        # Ein fast unbeobachteter, vollständiger Frühscreenshot darf dagegen eine
        # spätere, deutlich verlässlichere virale API-Messung nicht festhalten.
        self.con.execute("UPDATE publikum_messungen SET views=1,likes=0,shares=0,saves=0,"
                         "gemessen_utc=? WHERE id=?", (iso(self.start+timedelta(days=10, minutes=5)), mid))
        autonom.aktualisieren(self.con)
        self.assertNotEqual(self.con.execute("SELECT messung_id FROM audience_ergebnisse WHERE post_id=?", (pid,)).fetchone()[0], mid)

    def test_exploration_aendert_eine_variable_und_speichert_ergebnis(self):
        self.lernen()
        normal, _ = regie_lernen.aktuelle(self.con, self.konfig, "short")
        for i in range(6):
            self.con.execute("INSERT INTO entwuerfe(name,format,schnittliste,parameter,erstellt) VALUES(?,'short','x','{}',?)",
                             (f"probe{i}", iso(self.start)))
        exp, _ = regie_lernen.aktuelle(self.con, self.konfig, "short")
        variable = exp["autonom"]["exploration"]["variable"]
        changed = [k for k in autonom._optionen("short") if normal.get(k) != exp.get(k)]
        self.assertEqual(changed, [variable])
        pid, _ = publikum.post_anlegen(self.con, art="entwurf", ziel_id=99, plattform="tiktok", zeit=self.start,
            daten={"dauer_s": 65, "rezept": {}, "merkmale": {},
                   "schnittliste": {"format": "short", "parameter": exp}})
        publikum.speichere_messung(self.con, pid, {"views": 8000, "wiedergabe_s": 52}, "api",
                                  zeit=self.start+timedelta(days=90))
        row = self.con.execute("SELECT * FROM lern_experimente WHERE post_id=?", (pid,)).fetchone()
        self.assertIsNotNone(row["tatsaechlich"])
        self.assertIsNotNone(row["confidence_nachher"])
