"""Stufe 2 des Umbaus (07.10., Florian: „Lernen zurück“ – aus ✅/❌ und KI-Urteil, mutig, Wochenbericht):
Aufbau, Tempo und Zeitlupe lernt geschmack.py; deine Regeln gehen danach immer vor.
Mehrbenutzer Stufe 5, Schritt 3 (M193, Klasse BesterAufbau): der belegte beste Aufbau als Standard."""

import contextlib
import copy
import json
import os
import random
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from clip_pipeline import db, einstellungen, geschmack, liga, regie_lernen, stile
from clip_pipeline.konfig import Konfig
from clip_pipeline.zeit import iso, jetzt

from tests.hilfen import MitSpeicher
from tests.test_liga import LigaDaten


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
        """Ohne erstellt eine Sekunde vor jetzt: wochen_text zählt nur Entwürfe vor seinem „jetzt“ – lag der letzte in
        derselben Millisekunde, fehlte er (ein Test flackerte so in etwa 1 von 75 Läufen)."""
        self.n += 1
        p = {"stil": wahl.get("aufbau"), "geschmack": {**wahl, "experiment": experiment}}
        eid = self.con.execute("""INSERT INTO entwuerfe (name, format, schnittliste, parameter, status, datei, erstellt)
                                  VALUES (?, 'short', '/x.json', ?, 'gesendet', '/x.mp4', ?)""",
                               (f"e{self.n}", json.dumps(p), iso(erstellt or jetzt() - timedelta(seconds=1)))).lastrowid
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
        # Wochenbericht nennt, was er öfter bzw. seltener wählt – dieselbe Zahl wie früher „👍 Kommt gut an / 👎 Kommt
        # weniger an“, ehrlich benannt (Stufe 4, M162); ohne Videos in der Woche: Ruhe
        text = geschmack.wochen_text(self.con, self.konfig)
        self.assertIn("9 Videos · 4 ✅ · 5 ❌", text)
        self.assertIn("🎯 Wähle ich gerade öfter: Aufbau „Steigerung“ (4 von 4 ✅), ruhige Schnitte (4 von 4 ✅), "
                      "wenig Zeitlupe (4 von 4 ✅) · seltener: Aufbau „Kino“ (0 von 4 ✅), schnelle Schnitte (0 von 4 ✅)",
                      text)
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
        self.assertNotIn("📊", geschmack.wochen_text(self.con, self.konfig))   # Stufe 4: ohne Wochenzahlen keine Zeile
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
        # Stufe 4 (M162): Der Bot wählt das jetzt öfter und sagt es so – ein Beleg, dass es ankommt, ist das nicht
        # (vorläufige Noten, 4 gegen 6 Videos); früher stand hier „👀 Bei den Zuschauern kommt gut an: …“
        text = geschmack.wochen_text(self.con, self.konfig)
        self.assertIn("🎯 Wähle ich gerade öfter: Aufbau „erzählt“, ruhige Schnitte, viel Zeitlupe · seltener: "
                      "schnelle Schnitte, wenig Zeitlupe", text)
        for behauptung in ("👀", "Kommt gut an", "Belegt", "📊"):
            self.assertNotIn(behauptung, text)

    def test_wochenbericht_behauptet_nichts_ohne_beleg(self):
        """Mehrbenutzer Stufe 4 (Versuch E aus dem Plan): zwei Aufbauten aus derselben Verteilung, keine ✅/❌, alle
        vorläufigen Zuschauer-Noten negativ (sie messen anfangs an Startwerten), dazu KI-Noten. Vorher stand im Bericht
        „👎 Kommt weniger an: Aufbau „erzählt“ · ruhige Schnitte“ – ohne echten Unterschied. Jetzt behauptet er nichts,
        weder aus Zuschauer- noch aus KI-Noten; was der Bot seltener wählt, heißt so (M162). Belegt wäre nur, was erfolg
        aus fertigen Wochenzahlen zeigt – die gibt es hier noch nicht, also auch keine 📊-Zeile."""
        noten = {"story": (-0.53, -0.45, -0.41, -0.38), "montage": (-0.44, -0.36, -0.31, -0.28)}
        for i in range(8):
            aufbau = ("story", "montage")[i % 2]
            eid = self.entwurf({"aufbau": aufbau, "tempo": "ruhig" if aufbau == "story" else "schnell",
                                "zeitlupe": "wenig"}, ki=80 if aufbau == "montage" else 40)
            self.zuschauer(eid, noten[aufbau][i // 2])
        text = geschmack.wochen_text(self.con, self.konfig)
        for behauptung in ("Kommt gut an", "Kommt weniger an", "👀", "Belegt", "📊"):
            self.assertNotIn(behauptung, text)
        self.assertIn("🎯 Wähle ich gerade seltener: Aufbau „erzählt“, ruhige Schnitte", text)

    def test_wochenbericht_kommt_auch_wenn_erfolg_scheitert(self):
        """Stufe 4/5: Ein Fehler in erfolg bzw. der Regie-Liga (hier kaputte [erfolg.gewichte]) kostet den Sonntagsbericht
        nie – er kommt ohne Liga-Zeilen, der Fehler steht im Log."""
        self.entwurf({"aufbau": "story", "tempo": "ruhig", "zeitlupe": "viel"}, daumen=1)
        self.konfig.daten["erfolg"] = {"gewichte": {"zuschauer": -1, "follower": 0.2, "webseite": 0.3}}
        with self.assertLogs("pipeline", "ERROR") as protokoll:
            text = geschmack.wochen_text(self.con, self.konfig)
        self.assertTrue(text.startswith("🧠 Deine Woche"), text)
        self.assertIn("📏 Deine Regeln gelten weiter", text)
        for weg in ("📊", "🏅", "🔜"):
            self.assertNotIn(weg, text)
        self.assertIn("[erfolg.gewichte].zuschauer", protokoll.output[0])

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
        # Stufe 5 (M187): steuert es nur einen Feinwert des Aufbaus nach (Musikpegel), bleiben Aufbau und Versuch
        # stehen – 🧪 nennt ihn. Gelernt wird genau wie vorher, als nur_wirksame den Aufbau strich (Rückfall auf den Stil)
        vorher = {"stil": "kino", "musik_pegel": 0.3,
                  "geschmack": {"aufbau": "kino", "tempo": "ruhig", "zeitlupe": "viel", "experiment": "aufbau"}}
        wahl = geschmack.nur_wirksame(vorher, {**vorher, "musik_pegel": 0.5}, self.konfig)
        self.assertEqual((wahl["aufbau"], wahl["experiment"], wahl.get("uebersteuert")), ("kino", "aufbau", None))
        vorher_m187 = {"tempo": "ruhig", "zeitlupe": "viel", "experiment": None, "uebersteuert": ["aufbau"]}
        eid, statistik = self.entwurf(wahl, daumen=1, ki=80), []
        for gespeichert in (vorher_m187, wahl):   # so stand es bis Stufe 5 in den Parametern · so steht es jetzt
            self.con.execute("UPDATE entwuerfe SET parameter = ? WHERE id = ?",
                             (json.dumps({"stil": "kino", "geschmack": gespeichert}), eid))
            statistik.append(geschmack.statistik(self.con))
        self.assertEqual(statistik[0], statistik[1])
        self.assertIn("🧪 Ausprobiert: Aufbau „Kino“ (1×)", geschmack.wochen_text(self.con, self.konfig))
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
            self.assertIn("🧠 Lernt aus: deinen ✅/❌ (1) · KI-Note (läuft) · Zuschauern (TikTok nicht verbunden – "
                          "einmal /tiktok)", geschmack.wochen_text(self.con, self.konfig))
        with mock.patch.dict(os.environ, {**tiktok, "TIKTOK_ACCESS_TOKEN": "t"}):     # verbunden, Zahlen fehlen trotzdem
            self.assertIn("Zuschauern (1 ✅-Video nach 3 Tagen noch ohne Zahlen – nicht hochgeladen oder den Text "
                          "dabei geändert?)", geschmack.wochen_text(self.con, self.konfig))



class WahlZeile(unittest.TestCase):
    """Prüfung S4-1 (M176): „🎯 Wähle ich gerade öfter/seltener“ nennt je Schraube höchstens die Wahl, die echt vorn
    bzw. echt hinten liegt. Vorher stand bei lauter ✅ „öfter: viel Zeitlupe (3 von 3 ✅), wenig Zeitlupe (3 von 3 ✅)“ –
    gewählt wird aber je Video nur eine."""

    @staticmethod
    def w(s: float, n: float, ja: int = 0, nein: int = 0) -> dict:
        return {"s": s, "n": n, "ja": ja, "nein": nein}

    def test_gleichstand_wird_nicht_als_oefter_genannt(self):
        stat = {"zeitlupe": {"viel": self.w(3, 3, ja=3), "wenig": self.w(3, 3, ja=3)},
                "tempo": {"schnell": self.w(4, 4, ja=4), "ruhig": self.w(0, 4, nein=4)}}
        zeile = geschmack.wahl_zeile(stat)
        self.assertEqual(zeile, "🎯 Wähle ich gerade öfter: schnelle Schnitte (4 von 4 ✅) · seltener: "
                                "ruhige Schnitte (0 von 4 ✅)")
        self.assertNotIn("Zeitlupe", zeile)
        # nur Gleichstand: kein klares Bild, keine Zeile
        self.assertIsNone(geschmack.wahl_zeile({"zeitlupe": stat["zeitlupe"]}))


def waehle_main(con, konfig, fmt="short", anders=None, basis_seg=1.0):
    """geschmack.waehle, wie es auf main bad7ce9 stand (vor Stufe 5, Schritt 3) – Zeile für Zeile kopiert, nur mit
    „geschmack.“/„stile.“ vor den Helfern, die unverändert sind. Der Fehlerfall vergleicht die neue Wahl damit."""
    n = con.execute("SELECT COUNT(*) FROM entwuerfe WHERE format = ?", (fmt,)).fetchone()[0]
    zufall = random.Random(f"geschmack:{fmt}:{n}")
    stat = geschmack.statistik(con, fmt)
    wahl: dict = {}
    reihe_aufbau: list[str] = []
    for knopf, optionen in geschmack.KNOEPFE.items():
        zuege = sorted(((zufall.betavariate(1 + stat[knopf][o]["s"], 1 + stat[knopf][o]["n"] - stat[knopf][o]["s"]), o)
                        for o in optionen), reverse=True)
        wahl[knopf] = zuege[0][1]
        if knopf == "aufbau":
            reihe_aufbau = [o for _, o in zuege]
    experiment = None
    if zufall.random() < float(konfig.wert("geschmack.mut", geschmack.MUT_STANDARD)):
        knopf = zufall.choice(sorted(geschmack.KNOEPFE))
        andere = [o for o in geschmack.KNOEPFE[knopf] if o != wahl[knopf]]
        wahl[knopf] = min(andere, key=lambda o: (stat[knopf][o]["n"], zufall.random()))
        experiment = knopf
    letzte = stile._letzte(con, fmt)
    if len(letzte) == 2 and letzte[0] == letzte[1] == wahl["aufbau"]:   # nie dreimal derselbe Aufbau
        wahl["aufbau"] = max((o for o in geschmack.KNOEPFE["aufbau"] if o != wahl["aufbau"]),
                             key=lambda o: (stat["aufbau"][o]["s"] + 1) / (stat["aufbau"][o]["n"] + 2))
    fest = str(konfig.wert("regie.stil", "auto") or "auto")
    if anders:                                                           # 🥱: sichtbar anders geschnitten
        aufbauten = ([fest] if fest in stile.STILE else
                     [o for o in reihe_aufbau if stile.STILE[o]["reihenfolge"] != anders.get("reihenfolge")] or reihe_aufbau)
        wahl["aufbau"], wahl["tempo"] = geschmack._anders(anders, aufbauten, basis_seg)
        wahl["anders_als"] = anders.get("anders_als")
        experiment = None if experiment in ("aufbau", "tempo") else experiment
    if fest in stile.STILE:                                # fester Stil (nur /experte; einfach: „auto“) geht vor
        wahl["aufbau"] = fest
        experiment = None if experiment == "aufbau" else experiment
    wahl["experiment"] = experiment
    return wahl


class BesterAufbau(LigaDaten):
    """Mehrbenutzer Stufe 5, Schritt 3 (M193): Hat die Regie-Liga einen Aufbau gekrönt, ersetzt er im einfachen Modus
    nur den Thompson-Zug – „mutig“, „nie dreimal“, 🥱 und deine Regeln bleiben. Daten wie tests/test_liga.py:
    48 TikTok-Shorts, „erzählt“ mit doppelten Reaktionen, gekrönt am Sonntag der 15. Woche (14.06.)."""

    def setUp(self):
        super().setUp()
        self.konfig.daten["regie"]["stil"] = "auto"            # /experte im Fehlerfall: Thompson statt festem Stil
        self.wochen(16, lambda i, a: 2.0 if a == "story" else 1.0)
        self.wochen(2)
        with db.transaktion(self.con):                          # deine ✅ für Kino, ❌ für Montage und Steigerung
            for i, eid in enumerate(self.eids):
                if (aufbau := ("story", "montage", "kino", "steigerung")[i % 4]) != "story":
                    self.con.execute("INSERT INTO entwurf_bewertungen (entwurf_id, daumen, gruende, erstellt, geaendert) "
                                     "VALUES (?, ?, '[]', ?, ?)", (eid, 1 if aufbau == "kino" else -1, iso(jetzt()),
                                                                   iso(jetzt())))
        self.gebaut = 0

    def bauen(self, wahl: dict) -> int:
        """Den Entwurf zur Wahl anlegen – wie der Lern-Bot (Stil und Wahl in den Parametern); eine Sekunde vor jetzt,
        damit ihn der Sonntagsbericht sicher in dieser Woche zählt (wie Geschmack.entwurf)."""
        self.gebaut += 1
        return self.con.execute("INSERT INTO entwuerfe (name, format, schnittliste, parameter, status, datei, erstellt) "
                                "VALUES (?, 'short', '/x.json', ?, 'gesendet', '/x.mp4', ?)",
                                (f"neu{self.gebaut}", json.dumps({"stil": wahl["aufbau"], "geschmack": wahl}),
                                 iso(jetzt() - timedelta(seconds=1)))).lastrowid

    @staticmethod
    def gaehn(wahl: dict, eid: int) -> dict:
        """🥱 an dieses Video (regeln.neue_fassung): Reihenfolge und Tempo, wie sie wirkten."""
        return {"reihenfolge": stile.STILE[wahl["aufbau"]]["reihenfolge"], "tempo": wahl["tempo"],
                "seg_min_faktor": 1.0, "anders_als": eid}

    def test_bester_aufbau_wird_standard_und_herausforderer_bleiben(self):
        k = einstellungen.anwenden(self.con, self.konfig)              # einfacher Modus
        self.assertEqual(liga.champion(self.con, k), "story")
        wahlen, eid = [], None
        for i in range(30):
            anders = self.gaehn(wahlen[-1], eid) if i in (9, 19) else None
            vorher = waehle_main(self.con, k, anders=anders)              # derselbe Stand, Code von main
            letzte = stile._letzte(self.con, "short")
            wahl = geschmack.waehle(self.con, k, anders=anders)
            # Gezogen wird wie vorher: Tempo, Zeitlupe und „mutig“ bekommen dieselben Zufallszahlen
            self.assertEqual({s: wahl[s] for s in ("tempo", "zeitlupe", "experiment")},
                             {s: vorher[s] for s in ("tempo", "zeitlupe", "experiment")}, i)
            self.assertEqual(wahl["champion"], "story")
            if anders:                                                    # 🥱 geht vor: andere Reihenfolge
                self.assertNotEqual(stile.STILE[wahl["aufbau"]]["reihenfolge"], anders["reihenfolge"], i)
            elif wahl["experiment"] == "aufbau":                          # mutig: ein Herausforderer
                self.assertNotEqual(wahl["aufbau"], "story", i)
            elif letzte == ["story", "story"]:                            # nie dreimal: der mit deinen ✅
                self.assertEqual(wahl["aufbau"], "kino", i)
            else:
                self.assertEqual(wahl["aufbau"], "story", i)
            wahlen.append(wahl)
            eid = self.bauen(wahl)
        folge = [w["aufbau"] for w in wahlen]
        self.assertFalse([j for j in range(2, 30) if folge[j - 2] == folge[j - 1] == folge[j]], folge)
        # meistens „erzählt“ (hier 18 von 30), die anderen fordern ihn heraus – Kino am öftesten (deine ✅)
        self.assertGreaterEqual(folge.count("story"), 15)
        self.assertGreaterEqual(30 - folge.count("story"), 6)
        self.assertEqual(max(("montage", "kino", "steigerung"), key=folge.count), "kino")
        # Der echte Weg (regie_lernen.aktuelle mit Publikums-Modell und nur_wirksame) vermerkt ihn am Entwurf
        p, _ = regie_lernen.aktuelle(self.con, k, "short")
        self.assertEqual((p["geschmack"]["champion"], p["stil"]), ("story", p["geschmack"]["aufbau"]))
        # Sonntagsbericht: 🥇 steht da, 🎯 nennt keinen Aufbau mehr, kein 🤔
        text = geschmack.wochen_text(self.con, k)
        self.assertIn("🥇 Bester Aufbau: „erzählt“ (belegt seit 14.06.)", text)
        self.assertFalse([z for z in text.splitlines() if z.startswith("🤔") or (z.startswith("🎯") and "Aufbau" in z)],
                         text)

    def test_ohne_krone_bei_fehler_abgeschaltet_und_unter_experte_wie_vorher(self):
        k = einstellungen.anwenden(self.con, self.konfig)
        aus = copy.deepcopy(k.daten)
        aus["geschmack"]["champion_standard"] = False
        k_aus = Konfig(daten=aus, quelle=k.quelle)
        faelle = (("ohne besten Aufbau – „erzählt“ liegt erst vorn", k,
                   mock.patch.object(liga, "jetzt", return_value=self.stichtag(14) + timedelta(hours=1))),
                  ("Fehler der Liga", k, mock.patch.object(liga, "champion", side_effect=RuntimeError("kaputt"))),
                  ("champion_standard = false", k_aus, mock.patch.object(liga, "champion", return_value="story")),
                  ("/experte", None, mock.patch.object(liga, "champion", return_value="story")))
        wahl = eid = None
        for titel, kk, patch in faelle:
            with self.subTest(titel), patch as ersatz:
                if kk is None:
                    einstellungen.setze(self.con, "lernbot.experte", True)
                    kk = einstellungen.anwenden(self.con, self.konfig)
                with self.assertLogs("pipeline", "ERROR") if titel == "Fehler der Liga" else contextlib.nullcontext():
                    for i in range(40):                                     # 40 Wahlen in Folge gleich wie auf main
                        anders = self.gaehn(wahl, eid) if i % 10 == 9 else None
                        vorher = waehle_main(self.con, kk, anders=anders)
                        wahl = geschmack.waehle(self.con, kk, anders=anders)
                        self.assertEqual(wahl, vorher, (titel, i))
                        eid = self.bauen(wahl)
                        if i % 4 == 3:                                      # deine ✅/❌ ändern die Statistik weiter
                            self.con.execute("INSERT INTO entwurf_bewertungen (entwurf_id, daumen, gruende, "
                                             "erstellt, geaendert) VALUES (?, ?, '[]', ?, ?)",
                                             (eid, 1 if wahl["tempo"] == "ruhig" else -1, iso(jetzt()), iso(jetzt())))
                if titel in ("champion_standard = false", "/experte"):
                    ersatz.assert_not_called()                              # abgeschaltet: die Liga wird nicht gefragt
        # Ehrlich: Ist der beste Aufbau abgeschaltet, sagt der 🥇-Satz der Krönungswoche auch nicht „nehme ich meistens“
        einstellungen.zuruecksetzen(self.con, "lernbot.experte")
        krone = geschmack.wochen_text(self.con, k_aus, self.stichtag(15) + timedelta(minutes=30))
        self.assertIn("sehr wahrscheinlich kein Zufall.\n", krone)
        self.assertNotIn("Ab jetzt nehme ich ihn meistens", krone)


if __name__ == "__main__":
    unittest.main()
