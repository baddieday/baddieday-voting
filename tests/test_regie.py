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
        self.konfig.daten["regie"]["vorgaben"] = {"abwechslung": 0}  # gleiche Momente: nur das Gelernte wirkt
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


@unittest.skipUnless(HAT_FFMPEG, "ffmpeg fehlt")
class ActionAmEnde(MitRegieMaterial):
    DAUER = 10.0

    def test_jubel_kurz_vor_dateiende_bricht_compose_nicht(self):
        # Instant-Replays enden direkt nach der Action: Jubel bei 9,6 s von 10 s
        self.momente_anlegen([("lustig", 0, [], "m1"), ("spannend", 1, [9.8], "m2"), ("chill", 0, [], "m3")])
        self.con.execute("UPDATE momente SET merkmale = json_set(merkmale, '$.jubel_laut_s', json('[9.6]')) "
                         "WHERE schluessel = 'datei:1'")
        liste = lies(regie.erstelle(self.con, self.konfig, "short"))
        self.assertEqual(regie.pruefe_liste(liste), [])


@unittest.skipUnless(HAT_FFMPEG, "ffmpeg fehlt")
class Vorgaben(MitRegieMaterial):
    """Deine Startwerte aus config/lokal.toml – von dort lernt der Regisseur weiter."""

    def test_vorgaben_grenzen_und_hinweise(self):
        self.konfig.daten["regie"]["vorgaben"] = {
            "seg_min_faktor": 1.4, "dauer_faktor": 5.0, "puffer_vor_s": "viel", "gibtsnicht": 1,
            "stimmung_bonus": {"lustig": 1.0, "wütend": 3}, "beats_pro_schnitt": 4,
        }
        self.konfig.daten["regie"]["musik_ziele"] = {"episch": {"bpm": 150, "energie": 2.0}}
        p, ziel, hinweise = regie_lernen.vorgaben(self.konfig)
        self.assertEqual(p["seg_min_faktor"], 1.4)
        self.assertEqual(p["dauer_faktor"], 1.0)            # auf die Grenze gestutzt
        self.assertEqual(p["puffer_vor_s"], regie.PARAMETER["puffer_vor_s"])  # keine Zahl -> ignoriert
        self.assertEqual(p["stimmung_bonus"], {"lustig": 1.0})
        self.assertEqual((ziel["episch"]["bpm"], ziel["episch"]["energie"]), (150.0, 1.0))
        self.assertEqual(len(hinweise), 3)                  # puffer_vor_s, gibtsnicht, wütend
        p2, _ = regie_lernen.aktuelle(self.con, self.konfig)
        self.assertEqual(p2["beats_pro_schnitt"], 4)        # Vorgabe ist die Untergrenze
        self.assertIn("deine Vorgabe", regie_lernen.lernstand_text(self.con, self.konfig))

    def test_vorgabe_wirkt_und_bewertung_lernt_weiter(self):
        self.momente_anlegen(MOMENTE[:10])
        self.musik_anlegen(150, "episch")
        vorher = lies(regie.erstelle(self.con, self.konfig, "short", parameter=regie_lernen.aktuelle(self.con, self.konfig)[0]))
        self.konfig.daten["regie"]["vorgaben"] = {"dauer_faktor": 0.7}
        p, ziel = regie_lernen.aktuelle(self.con, self.konfig)
        e = regie.erstelle(self.con, self.konfig, "short", parameter=p, ziel=ziel)
        self.assertLess(lies(e)["dauer_s"], vorher["dauer_s"])
        regie_lernen.bewerte(self.con, e["entwurf"], grund="lang")  # und noch kürzer gewünscht
        p2, _ = regie_lernen.aktuelle(self.con, self.konfig)
        self.assertEqual(p2["dauer_faktor"], 0.63)          # 0,7 × 0,9 – von der Vorgabe aus gelernt

    def test_cli_bewerte_und_lernstand(self):
        import contextlib
        import io
        from unittest import mock

        from clip_pipeline import cli

        self.momente_anlegen(MOMENTE[:6])
        e = regie.erstelle(self.con, self.konfig, "short")
        ausgabe = io.StringIO()
        with mock.patch("clip_pipeline.cli.lade", return_value=self.konfig), contextlib.redirect_stdout(ausgabe), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["bewerte", str(e["entwurf"]), "--schlecht", "--grund", "hektisch",
                                       "--grund", "lang"]), 0)
            self.assertEqual(cli.main(["lernstand"]), 0)
            self.assertEqual(cli.main(["bewerte", "999", "--gut"]), 1)
        zeilen = [json.loads(z) for z in ausgabe.getvalue().splitlines() if z.startswith("{")]
        self.assertEqual((zeilen[0]["daumen"], zeilen[0]["gruende"]), (-1, ["hektisch", "lang"]))
        self.assertGreater(zeilen[1]["parameter"]["seg_min_faktor"], 1.0)
        self.assertIn("unbekannt", zeilen[2]["fehler"])


@unittest.skipUnless(HAT_FFMPEG, "ffmpeg fehlt")
class Abwechslung(MitRegieMaterial):
    """Nicht immer dieselben Momente: Abzug für kürzlich Gezeigtes, Lernen je Moment aus 👍/👎."""

    def compose(self):
        p, ziel = regie_lernen.aktuelle(self.con, self.konfig)
        return lies(regie.erstelle(self.con, self.konfig, "short", parameter=p, ziel=ziel))

    @staticmethod
    def momente(liste):
        return {s["moment"] for s in liste["segmente"]}

    def test_neue_momente_und_die_besten_kommen_wieder(self):
        self.momente_anlegen(MOMENTE * 2)  # 32 Momente, ein Short braucht ~5
        self.musik_anlegen(150, "episch")
        listen = [self.compose() for _ in range(4)]
        for liste in listen:
            self.assertEqual(regie.pruefe_liste(liste), [])
        eins, zwei, drei = (self.momente(listen[i]) for i in range(3))
        self.assertEqual(eins & zwei, set())                   # direkt danach: alles neu
        self.assertEqual(listen[0]["auswahl"]["neu"], len(eins))
        self.assertEqual(listen[1]["auswahl"]["schon_gezeigt"], len(eins & zwei))
        self.assertTrue(eins & drei)                           # die stärksten kommen wieder, nur nicht jedes Mal
        gezeigt = {s["moment"]: s["gezeigt"] for s in listen[2]["segmente"]}
        self.assertTrue(all(gezeigt[m] >= 1 for m in eins & drei))
        # Ohne Abwechslung (Vorgabe 0): immer dieselben – so war es vorher
        self.konfig.daten["regie"]["vorgaben"] = {"abwechslung": 0}
        self.assertEqual(self.momente(self.compose()), self.momente(self.compose()))
        self.assertIn("deine Vorgabe", regie_lernen.lernstand_text(self.con, self.konfig))

    def test_auswahl_zaehlt_alle_momente(self):
        # Passt beim Auffüllen kein Moment mehr hinein, bleibt die Auswahl trotzdem vollständig
        # (Entwurf #17: nach „abgeschnitten“ Vorlauf 3,5 s -> meldete „nur 6 Momente“ statt 128)
        # Nachgestellt mit fester Testmusik: nach drei Entwürfen passt bei 3,5 s Vorlauf beim Auffüllen nichts mehr
        self.momente_anlegen(MOMENTE)
        self.musik_anlegen(150, "episch")
        p, ziel = regie_lernen.aktuelle(self.con, self.konfig)
        for vor in (1.0, 2.0, 3.0, 3.5, 4.5):
            liste = lies(regie.erstelle(self.con, self.konfig, "short", parameter={**p, "puffer_vor_s": vor}, ziel=ziel))
            self.assertEqual(liste["auswahl"]["kandidaten"], len(MOMENTE), vor)
            self.assertFalse([h for h in liste["hinweise"] if h.startswith("nur ") and f"nur {len(MOMENTE)} " not in h])

    def test_lernen_je_moment(self):
        self.momente_anlegen(MOMENTE)
        self.konfig.daten["regie"]["vorgaben"] = {"abwechslung": 0}
        p, _ = regie_lernen.aktuelle(self.con, self.konfig)
        gut, schlecht, musik, langweilig = (regie.erstelle(self.con, self.konfig, "short", parameter=p)
                                            for _ in range(4))       # ohne Abwechslung: viermal dieselben
        regie_lernen.bewerte(self.con, gut["entwurf"], daumen=1)
        p, _ = regie_lernen.aktuelle(self.con, self.konfig)
        m = self.momente(lies(gut))
        self.assertEqual({p["moment_bonus"][k] for k in m}, {0.5})
        regie_lernen.bewerte(self.con, schlecht["entwurf"], daumen=-1)        # 👎 ohne Grund: die Clips
        regie_lernen.bewerte(self.con, musik["entwurf"], grund="musik")       # 👎 wegen Musik: nicht die Clips
        p, _ = regie_lernen.aktuelle(self.con, self.konfig)
        self.assertEqual({p["moment_bonus"][k] for k in m}, {0.0})
        regie_lernen.bewerte(self.con, langweilig["entwurf"], grund="langweilig")
        p, ziel = regie_lernen.aktuelle(self.con, self.konfig)
        self.assertEqual({p["moment_bonus"][k] for k in m}, {-1.0})
        self.assertIn("weniger gern gesehen", regie_lernen.lernstand_text(self.con, self.konfig))
        danach = self.momente(lies(regie.erstelle(self.con, self.konfig, "short", parameter=p, ziel=ziel)))
        self.assertNotEqual(danach, m)                                        # andere Clips, obwohl ohne Abwechslung
