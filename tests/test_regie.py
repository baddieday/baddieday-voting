"""Regisseur: Dauer je Format, Spannungsbogen, Schnitte auf dem Beat, Übergänge je Stimmung, Lernen."""

import json
import unittest

from clip_pipeline import regie, regie_lernen

from tests.hilfen import HAT_FFMPEG
from tests.regie_hilfen import MOMENTE, MitRegieMaterial


def lies(e):
    with open(e["datei"], encoding="utf-8") as f:
        return json.load(f)


@unittest.skipUnless(HAT_FFMPEG, "ffmpeg fehlt")
class Regisseur(MitRegieMaterial):
    def compose(self, fmt):
        p, ziel = regie_lernen.aktuelle(self.con, self.konfig)
        return regie.erstelle(self.con, self.konfig, fmt, parameter=p, ziel=ziel)

    def auf_beats(self, liste, track):
        beats = json.loads(track["beats"])
        versatz = liste["musik"]["start_s"]
        for s in liste["segmente"]:
            if s["auf_beat"]:
                self.assertTrue(any(abs(b - versatz - s["zeit_ende"]) < 0.002 for b in beats), s)

    def test_short_und_zusammenschnitt(self):
        self.momente_anlegen(MOMENTE * 2)
        # Ein verworfener Clip darf nie vorkommen – auch wenn er der stärkste Moment wäre
        verworfen = self.clip_anlegen(status="verworfen", max_gruppe=5)
        self.con.execute("UPDATE momente SET clip_id = ?, stimmung = 'episch' WHERE schluessel = 'datei:1'", (verworfen,))
        spannend = self.musik_anlegen(128, "spannend")
        episch = self.musik_anlegen(150, "episch")
        self.musik_anlegen(90, "chill")

        e = self.compose("short")
        liste = lies(e)
        self.assertEqual(regie.pruefe_liste(liste), [])
        self.assertTrue(30 <= liste["dauer_s"] <= 45, liste["dauer_s"])
        self.assertEqual((liste["aufloesung"], liste["overlay"]), ([1080, 1920], "clip-battle.de"))
        self.assertEqual(liste["musik"]["track_id"], episch["id"])  # Hauptstimmung episch -> epische Musik
        self.assertEqual(liste["bogen"][-1], max(liste["bogen"]))  # Höhepunkt zum Schluss
        self.assertNotIn("datei:1", [s["moment"] for s in liste["segmente"]])
        self.assertTrue(all(s["auf_beat"] for s in liste["segmente"]))
        self.auf_beats(liste, episch)

        e = self.compose("zusammenschnitt")
        liste = lies(e)
        self.assertEqual(regie.pruefe_liste(liste), [])
        self.assertTrue(180 <= liste["dauer_s"] <= 300, liste["dauer_s"])
        self.assertEqual(liste["aufloesung"], [1920, 1080])
        self.assertEqual(liste["musik"]["track_id"], spannend["id"])
        self.auf_beats(liste, spannend)
        segs = liste["segmente"]
        self.assertEqual(liste["bogen"][-1], max(liste["bogen"]))
        self.assertGreater(liste["bogen"][0], sorted(liste["bogen"])[len(segs) // 2])  # starker Einstieg
        for s in segs[1:]:
            if s["uebergang"]["art"] != "schnitt":
                self.assertEqual(s["uebergang"]["art"], regie.UEBERGANG[s["stimmung"]][0])
        self.assertTrue(any(s["uebergang"]["art"] == "fade" for s in segs))  # chill -> weiche Blende
        gleiche = sum(1 for a, b in zip(segs, segs[1:]) if a["stimmung"] == b["stimmung"])
        self.assertLess(gleiche, len(segs) // 3)  # Abwechslung
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM entwuerfe").fetchone()[0], 2)

    def test_lernen_wirkt_im_naechsten_lauf(self):
        self.momente_anlegen(MOMENTE)
        erster_track = self.musik_anlegen(150, "episch", name="A")
        self.musik_anlegen(146, "episch", name="B")
        vorher = lies(self.compose("short"))
        self.assertEqual(vorher["musik"]["track_id"], erster_track["id"])

        entwurf = self.con.execute("SELECT id FROM entwuerfe").fetchone()["id"]
        for grund in ("hektisch", "lang", "musik", "abgeschnitten"):
            regie_lernen.bewerte(self.con, entwurf, grund=grund)
        for _ in range(2):  # zwei weitere Entwürfe, beide "zu lang"
            regie_lernen.bewerte(self.con, self.compose("short")["entwurf"], grund="lang")
        self.assertEqual(self.con.execute("SELECT daumen FROM entwurf_bewertungen").fetchone()["daumen"], -1)
        zweiter = self.con.execute("SELECT id FROM entwuerfe").fetchone()["id"]
        regie_lernen.bewerte(self.con, zweiter, grund="hektisch")  # zweimal hektisch (gleicher Entwurf, umschalten)
        regie_lernen.bewerte(self.con, zweiter, grund="hektisch")
        p, _ = regie_lernen.aktuelle(self.con, self.konfig)
        self.assertGreater(p["puffer_vor_s"], regie.PARAMETER["puffer_vor_s"])
        self.assertLess(p["dauer_faktor"], 1.0)
        self.assertEqual(p["track_malus"], {str(erster_track["id"]): 1.0})

        nachher = lies(self.compose("short"))
        self.assertNotEqual(nachher["musik"]["track_id"], erster_track["id"])  # andere Musik
        self.assertLess(nachher["dauer_s"], vorher["dauer_s"])                 # kürzer
        mittel = lambda l: sum(s["zeit_ende"] - s["zeit_start"] for s in l["segmente"]) / len(l["segmente"])  # noqa: E731
        self.assertGreaterEqual(mittel(nachher), mittel(vorher) - 0.01)        # nicht hektischer
        self.assertIn("Musik-Abzug", regie_lernen.lernstand_text(self.con, self.konfig))

    def test_stimmung_getroffen_verschiebt_musikziel(self):
        self.momente_anlegen(MOMENTE[:6])
        self.musik_anlegen(100, "spannend")
        e = self.compose("short")
        regie_lernen.bewerte(self.con, e["entwurf"], grund="getroffen")
        p, ziel = regie_lernen.aktuelle(self.con, self.konfig)
        haupt = lies(e)["stimmung"]
        self.assertEqual(p["stimmung_bonus"][haupt], 0.5)
        self.assertLess(ziel[haupt]["bpm"], regie.ZIEL[haupt]["bpm"] + 0.01 if regie.ZIEL[haupt]["bpm"] > 100 else 999)
        self.assertEqual(self.con.execute("SELECT daumen FROM entwurf_bewertungen").fetchone()["daumen"], 1)

    def test_ohne_musik_und_ohne_momente(self):
        with self.assertRaises(regie.RegieFehler):
            self.compose("short")
        self.momente_anlegen(MOMENTE[:5])
        liste = lies(self.compose("short"))
        self.assertIsNone(liste["musik"])
        self.assertIn("keine Musik", " ".join(liste["hinweise"]))
        self.assertEqual(regie.pruefe_liste(liste), [])
