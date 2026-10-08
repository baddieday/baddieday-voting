"""Stufe 2 des Umbaus (07.10., Florian: „Lernen zurück“ – aus ✅/❌ und KI-Urteil, mutig, Wochenbericht):
Aufbau, Tempo und Zeitlupe lernt geschmack.py; deine Regeln gehen danach immer vor."""

import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from clip_pipeline import db, einstellungen, geschmack, regie_lernen
from clip_pipeline.zeit import iso, jetzt

from tests.hilfen import MitSpeicher


class Geschmack(MitSpeicher):
    def setUp(self):
        super().setUp()
        self.con = db.verbinde(self.konfig.datenbank)
        self.konfig.daten["regie"]["stil"] = "auto"
        self.n = 0

    def tearDown(self):
        self.con.close()
        super().tearDown()

    def entwurf(self, wahl, daumen=None, gruende=(), ki=None, experiment=None, erstellt=None):
        self.n += 1
        p = {"stil": wahl.get("aufbau"), "geschmack": {**wahl, "experiment": experiment}}
        eid = self.con.execute("""INSERT INTO entwuerfe (name, format, schnittliste, parameter, status, datei, erstellt)
                                  VALUES (?, 'short', '/x.json', ?, 'gesendet', '/x.mp4', ?)""",
                               (f"e{self.n}", json.dumps(p), iso(erstellt or jetzt()))).lastrowid
        if daumen is not None:
            self.con.execute("INSERT INTO entwurf_bewertungen (entwurf_id, daumen, gruende, erstellt, geaendert) "
                             "VALUES (?, ?, ?, ?, ?)",
                             (eid, daumen, json.dumps(list(gruende)), iso(jetzt()), iso(jetzt())))
        if ki is not None:
            self.con.execute("INSERT INTO kritiken (entwurf_id, score, regel_score, ki_score, details, erstellt) "
                             "VALUES (?, 70, 70, ?, '{}', ?)", (eid, ki, iso(jetzt())))
        return eid

    def zuschauer(self, eid, y=None):
        """Post eines hochgeladenen Videos (legt ✅ an); mit y die Zuschauer-Note (−1 … 1) wie nach dem Abruf."""
        zeit = iso(jetzt())
        self.con.execute("""INSERT INTO posts (art, ziel, entwurf_id, plattform, gepostet_utc, dauer_s, rezept, merkmale,
                                               erstellt) VALUES ('entwurf', ?, ?, 'tiktok', ?, 45, '{}', '{}', ?)
                            ON CONFLICT (plattform, ziel) DO NOTHING""", (f"entwurf:{eid}", eid, zeit, zeit))
        if y is not None:
            pid = self.con.execute("SELECT id FROM posts WHERE ziel = ?", (f"entwurf:{eid}",)).fetchone()[0]
            mid = self.con.execute("INSERT INTO publikum_messungen (post_id, gemessen_utc, quelle, views, erstellt) "
                                   "VALUES (?, ?, 'api', 500, ?)", (pid, zeit, zeit)).lastrowid
            self.con.execute("INSERT INTO audience_ergebnisse (post_id, messung_id, input_hash, score, confidence, "
                             "teile, aktualisiert) VALUES (?, ?, 'h', ?, 0.8, '{}', ?)", (pid, mid, y, zeit))

    def test_lernt_aus_daumen_und_ki_und_gruende_grenzen_ein(self):
        gut = {"aufbau": "steigerung", "tempo": "ruhig", "zeitlupe": "wenig"}
        schlecht = {"aufbau": "kino", "tempo": "schnell", "zeitlupe": "viel"}
        for _ in range(4):
            self.entwurf(gut, daumen=1, ki=80)
            self.entwurf(schlecht, daumen=-1, ki=30)
        ohne_ki = self.entwurf(gut, daumen=-1, gruende=["musik"])   # 🎵 hat mit Aufbau/Tempo/Zeitlupe nichts zu tun
        self.assertEqual(geschmack.offen_fuer_ki(self.con), ohne_ki)           # die KI urteilt danach im Hintergrund
        self.assertIsNone(geschmack.offen_fuer_ki(self.con, {ohne_ki}))        # schon versucht: nicht alle 30 s nochmal
        stat = geschmack.statistik(self.con)
        self.assertEqual((stat["aufbau"]["steigerung"]["ja"], stat["aufbau"]["steigerung"]["nein"]), (4, 0))
        self.assertAlmostEqual(stat["aufbau"]["kino"]["n"], 4 + 4 * geschmack.KI_GEWICHT)
        self.konfig.daten.setdefault("geschmack", {})["mut"] = 0.0
        wahl = geschmack.waehle(self.con, self.konfig)
        self.assertEqual({k: wahl[k] for k in geschmack.KNOEPFE}, gut)
        self.assertIsNone(wahl["experiment"])
        # mutig: eine Schraube bewusst auf die am wenigsten erprobte Einstellung
        self.konfig.daten["geschmack"]["mut"] = 1.0
        wahl = geschmack.waehle(self.con, self.konfig)
        self.assertIn(wahl["experiment"], geschmack.KNOEPFE)
        self.assertNotEqual(wahl[wahl["experiment"]], gut[wahl["experiment"]])
        # Wochenbericht nennt, was ankommt und was nicht – ohne Videos in der Woche: Ruhe
        text = geschmack.wochen_text(self.con, self.konfig)
        self.assertIn("9 Videos · 4 ✅ · 5 ❌", text)
        self.assertIn("👍 Kommt gut an: Aufbau „Steigerung“ (4 von 4 ✅)", text)
        self.assertIn("Aufbau „Kino“ (0 von 4 ✅)", text)
        self.assertIsNone(geschmack.wochen_text(self.con, self.konfig, datetime(2020, 1, 1, tzinfo=timezone.utc)))

    def test_zuschauer_lehren_aufbau_tempo_und_zeitlupe(self):
        """Stufe 5 (08.10., Florian: „keine 100 oder 1000 Videos bewerten“): ohne einen einzigen Daumen lernt der Bot
        aus den Zuschauer-Noten – jede Schraube, die im Video wirkte, zählt doppelt."""
        self.konfig.daten.setdefault("geschmack", {})["mut"] = 0.0
        erzaehlt = {"aufbau": "story", "tempo": "ruhig", "zeitlupe": "viel"}
        gut = [self.entwurf(erzaehlt) for _ in range(4)]
        rest = [self.entwurf({"aufbau": a, "tempo": "schnell", "zeitlupe": "wenig"})
                for a in ("montage", "steigerung", "kino") for _ in range(2)]
        vorher = geschmack.statistik(self.con)
        for eid in gut + rest:                     # hochgeladen, aber noch ohne Zahlen: alles wie vor Stufe 5
            self.zuschauer(eid)
        self.assertEqual(geschmack.statistik(self.con), vorher)
        self.assertNotIn("👀", geschmack.wochen_text(self.con, self.konfig))
        for eid in gut:
            self.zuschauer(eid, 0.8)
        for eid in rest:
            self.zuschauer(eid, -0.8)
        stat = geschmack.statistik(self.con)
        story = stat["aufbau"]["story"]
        self.assertEqual((story["n"], story["zuschauer"], story["ja"] + story["nein"]), (8.0, 4, 0))
        self.assertAlmostEqual(story["s"], 8 * 0.9)                          # Treffer (y + 1)/2 = 0,9 je Video
        self.assertAlmostEqual(stat["tempo"]["schnell"]["s"], 12 * 0.1)
        wahl = geschmack.waehle(self.con, self.konfig)
        self.assertEqual({k: wahl[k] for k in geschmack.KNOEPFE}, erzaehlt)
        self.assertIn("👀 Bei den Zuschauern kommt gut an: Aufbau „erzählt“ (4 Videos) · ruhige Schnitte (4 Videos) · "
                      "viel Zeitlupe (4 Videos)", geschmack.wochen_text(self.con, self.konfig))

    def test_einfacher_modus_nutzt_geschmack_und_regeln_gehen_vor(self):
        k = einstellungen.anwenden(self.con, self.konfig)                       # einfacher Modus: geschmack an
        p, _ = regie_lernen.aktuelle(self.con, k, "short")
        self.assertIn(p["geschmack"]["aufbau"], geschmack.KNOEPFE["aufbau"])
        self.assertEqual(p["stil"], p["geschmack"]["aufbau"])
        self.assertEqual(p["max_lupen"], geschmack.ZEITLUPEN[p["geschmack"]["zeitlupe"]])
        # übersteuert das Publikums-Modell danach eine Schraube, bekommt sie weder Lob noch Tadel
        vorher = {"geschmack": {"aufbau": "kino", "tempo": "ruhig", "zeitlupe": "viel", "experiment": "tempo"},
                  "seg_min_faktor": 1.25, "max_lupen": 8}
        wahl = geschmack.nur_wirksame(vorher, {**vorher, "seg_min_faktor": 1.4}, self.konfig)
        self.assertEqual((wahl.get("tempo"), wahl["experiment"], wahl["aufbau"]), (None, None, "kino"))
        # 08.10.: ein alter fester Stil aus ⚙️ friert das Aufbau-Lernen im einfachen Modus nicht mehr ein
        p, _ = regie_lernen.aktuelle(self.con, einstellungen.anwenden(self.con, self.konfig), "short")
        fest = next(s for s in ("kino", "story") if s != p["stil"])
        einstellungen.setze(self.con, "regie.stil", fest)
        self.assertEqual(regie_lernen.aktuelle(self.con, einstellungen.anwenden(self.con, self.konfig), "short")[0]
                         ["stil"], p["stil"])
        einstellungen.setze(self.con, "lernbot.experte", True)                   # /experte: der feste Stil geht vor
        p, _ = regie_lernen.aktuelle(self.con, einstellungen.anwenden(self.con, self.konfig), "short")
        self.assertEqual(p["stil"], fest)

    def test_wochenbericht_sonntags_einmal(self):
        heute = datetime.now(timezone.utc)
        sonntag = heute + timedelta(days=7 - heute.isoweekday())               # der nächste Sonntag
        abend = sonntag.replace(hour=17, minute=30)                             # 18:30/19:30 in Berlin
        self.entwurf({"aufbau": "story", "tempo": "ruhig", "zeitlupe": "viel"}, daumen=1,
                     erstellt=abend - timedelta(hours=1))
        self.assertTrue(geschmack.wochenbericht(self.con, self.konfig, abend))
        text = self.con.execute("SELECT text FROM lern_meldungen WHERE schluessel LIKE 'woche:%'").fetchone()[0]
        self.assertIn("ich probiere weiter selbst aus", text)                   # 08.10.: keine Bitte um mehr ✅/❌
        self.assertFalse(geschmack.wochenbericht(self.con, self.konfig, abend))  # je Woche einmal
        self.assertFalse(geschmack.wochenbericht(self.con, self.konfig, sonntag.replace(hour=8)))   # vormittags nie

    def test_wer_lehrt_ki_note_und_fehlende_zahlen(self):
        """08.10.: Ob die KI-Note läuft, steht nur in vorhandenen Daten (kein Video = keine Aussage); der
        Wochenbericht nennt die Lehrer, sobald ein ✅-Video nach 3 Tagen keine Zahlen hat."""
        self.assertEqual(geschmack.ki_stand(self.con), "kommt mit dem nächsten Video")
        eid = self.entwurf({"aufbau": "story"}, daumen=1, erstellt=jetzt() - timedelta(hours=7))
        self.assertEqual(geschmack.ki_stand(self.con), "fehlt – Claude-Anmeldung nötig")   # 7 h alt, keine Note
        # N46: Der gespeicherte Grund des letzten Versuchs entscheidet – eine Antwort, die nicht ins Schema passt,
        # heißt nicht „Anmeldung nötig“ (das ginge auf dem Server einer Panne nach, die es nicht gibt)
        schema = {"hinweis": "KI-Cutter: Antwort passt nicht zum Schema: $.schwaechen[1]: länger als 140 Zeichen"}
        self.con.execute("INSERT INTO kritiken (entwurf_id, score, regel_score, details, erstellt) VALUES (?, 60, 60, ?, ?)",
                         (eid, json.dumps(schema), iso(jetzt())))
        self.assertEqual(geschmack.ki_stand(self.con), "hakt gerade, beim nächsten Video neuer Versuch")
        self.con.execute("UPDATE kritiken SET details = ? WHERE entwurf_id = ?",
                         (json.dumps({"hinweis": "KI-Cutter: claude nicht gefunden"}), eid))
        self.assertEqual(geschmack.ki_stand(self.con), "fehlt – Claude-Anmeldung nötig")
        self.assertNotIn("🧠 Lernt aus", geschmack.wochen_text(self.con, self.konfig))   # noch fehlen keine Zahlen
        vor_4_tagen = iso(jetzt() - timedelta(days=4))
        self.con.execute("""INSERT INTO posts (art, ziel, entwurf_id, plattform, gepostet_utc, dauer_s, rezept, merkmale,
                                               erstellt) VALUES ('entwurf', ?, ?, 'tiktok', ?, 45, '{}', '{}', ?)""",
                         (f"entwurf:{eid}", eid, vor_4_tagen, vor_4_tagen))
        self.entwurf({"aufbau": "kino"}, ki=72)                                         # die KI benotet wieder
        tiktok = {k: "" for k in ("TIKTOK_ACCESS_TOKEN", "TIKTOK_REFRESH_TOKEN", "TIKTOK_CLIENT_KEY",
                                  "TIKTOK_CLIENT_SECRET")}
        with mock.patch.dict(os.environ, tiktok):
            self.assertIn("🧠 Lernt aus: deinen ✅/❌ · KI-Note (läuft) · Zuschauern (TikTok nicht verbunden – "
                          "einmal /tiktok)", geschmack.wochen_text(self.con, self.konfig))
        with mock.patch.dict(os.environ, {**tiktok, "TIKTOK_ACCESS_TOKEN": "t"}):     # verbunden, Zahlen fehlen trotzdem
            self.assertIn("Zuschauern (1 ✅-Video nach 3 Tagen noch ohne Zahlen – nicht hochgeladen oder den Text "
                          "dabei geändert?)", geschmack.wochen_text(self.con, self.konfig))


if __name__ == "__main__":
    unittest.main()
