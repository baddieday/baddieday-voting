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


class EffekteLernen(MitRegieMaterial):
    """Stufe 2 (Regisseur 2.0): „🎆 zu viele Effekte“ / „💥 mehr Action“ je Hauptstimmung, „zu hektisch“ dämpft.
    Ohne Video: Entwürfe direkt in der Datenbank, die Schnittliste nur mit Stimmung und Momenten."""

    # (Stimmung, Momente, Daumen, Gründe, mit Musik) – nur die alten Gründe, Werte unten vom Code vor Stufe 2
    ALTE_FOLGE = [
        ("episch", ["a", "b"], 1, [], False), ("spannend", ["b", "c"], -1, ["hektisch", "lang"], False),
        ("episch", ["a", "d"], -1, ["musik"], True), ("lustig", ["e"], 1, ["getroffen"], True),
        ("episch", ["a"], -1, ["langweilig", "abgeschnitten"], False), ("spannend", ["c"], -1, [], False),
        ("chill", ["f"], 1, ["hektisch"], False), ("episch", ["b"], 1, [], False),
    ]

    def setUp(self):
        super().setUp()
        self.konfig.daten["regie"].update(vorgaben={}, lernen_ab=3)
        self.n = 0

    def entwurf(self, stimmung, gruende=(), daumen=None, momente=("a",), track=None):
        self.n += 1
        pfad = self.tmp / f"e{self.n}.json"
        pfad.write_text(json.dumps({"stimmung": stimmung, "segmente": [{"moment": m} for m in momente]}),
                        encoding="utf-8")
        eid = self.con.execute("INSERT INTO entwuerfe (name, format, schnittliste, parameter, track_id, erstellt) "
                               "VALUES (?, 'short', ?, '{}', ?, 'x')", (f"e{self.n}", str(pfad), track)).lastrowid
        if daumen is not None:
            regie_lernen.bewerte(self.con, eid, daumen=daumen)
        for g in gruende:
            regie_lernen.bewerte(self.con, eid, grund=g)
        return eid

    def p(self):
        return regie_lernen.aktuelle(self.con, self.konfig)[0]

    def test_neue_gruende_hinten(self):
        self.assertEqual(list(regie_lernen.GRUENDE)[:6],
                         ["musik", "hektisch", "getroffen", "lang", "abgeschnitten", "langweilig"])
        self.assertEqual(list(regie_lernen.GRUENDE.items())[6:],
                         [("effekte_viel", "🎆 zu viele Effekte"), ("action", "💥 mehr Action")])
        self.assertEqual((regie.PARAMETER["effekt_staerke"], regie.PARAMETER["effekt_hektik"]), ({}, 1.0))

    def test_vorgaben_mit_grenzen(self):
        self.konfig.daten["regie"]["vorgaben"] = {
            "effekt_hektik": 5, "effekt_staerke": {"episch": 2.0, "chill": 0, "lustig": 0.7, "wütend": 1.0,
                                                   "spannend": "viel", "frustriert": True}}
        p, _, hinweise = regie_lernen.vorgaben(self.konfig)
        self.assertEqual(p["effekt_staerke"], {"episch": 1.5, "chill": 0.0, "lustig": 0.7})
        self.assertEqual(p["effekt_hektik"], 1.3)
        self.assertEqual(sorted(hinweise), [f"regie.vorgaben.effekt_staerke.{s} ignoriert"
                                            for s in ("frustriert", "spannend", "wütend")])
        self.assertEqual(regie.PARAMETER["effekt_staerke"], {})          # die Startwerte bleiben unberührt
        self.konfig.daten["regie"]["vorgaben"] = {"effekt_hektik": 0.1, "effekt_staerke": 0.5}
        p, _, hinweise = regie_lernen.vorgaben(self.konfig)
        self.assertEqual((p["effekt_hektik"], p["effekt_staerke"]), (0.3, {}))
        self.assertEqual(hinweise, ["regie.vorgaben.effekt_staerke ignoriert (unbekannt oder keine Zahl)"])

    def test_regeln_je_hauptstimmung(self):
        self.entwurf("episch", ["effekte_viel"])
        self.assertEqual(self.p()["effekt_staerke"], {"episch": 0.85})
        self.entwurf("episch", ["effekte_viel"])
        self.entwurf("spannend", ["action"])
        self.entwurf("lustig", ["effekte_viel", "action"])                # beide zugleich: nichts
        self.entwurf("chill", daumen=1)                                   # 👍/👎 ohne Grund: nichts
        self.entwurf("frustriert", daumen=-1)
        p = self.p()
        self.assertEqual(p["effekt_staerke"], {"episch": 0.722, "spannend": 1.15})
        self.assertEqual(p["effekt_hektik"], 1.0)
        self.assertEqual(self.con.execute("SELECT daumen FROM entwurf_bewertungen WHERE entwurf_id = 3")
                         .fetchone()[0], -1)                              # „mehr Action“ ist Kritik

    def test_chronologisch_mit_grenzen(self):
        for _ in range(4):
            self.entwurf("episch", ["action"])                            # 1,15 · 1,323 · 1,5 (Grenze) · 1,5
        self.entwurf("episch", ["effekte_viel"])
        self.assertEqual(self.p()["effekt_staerke"], {"episch": 1.275})   # von der Grenze aus, nicht von 1,749
        for _ in range(20):
            self.entwurf("spannend", ["effekte_viel"])
        self.assertEqual(self.p()["effekt_staerke"]["spannend"], 0.1)     # nie ganz aus durch Lernen
        self.entwurf("spannend", ["action"])
        self.assertEqual(self.p()["effekt_staerke"]["spannend"], 0.115)

    def test_hektisch_daempft_und_vorgabe_null_bleibt(self):
        self.konfig.daten["regie"]["vorgaben"] = {"effekt_staerke": {"chill": 0}, "effekt_hektik": 1.2}
        self.entwurf("chill", ["action"])
        self.entwurf("chill", ["hektisch"])
        p = self.p()
        self.assertEqual(p["effekt_staerke"], {"chill": 0.0})             # deine 0 heißt: ohne Effekte
        self.assertEqual(p["effekt_hektik"], 1.08)                        # 1,2 × 0,9
        for _ in range(15):
            self.entwurf("episch", ["hektisch"])
        self.assertEqual(self.p()["effekt_hektik"], 0.3)                  # Untergrenze
        self.konfig.daten["regie"]["vorgaben"] = {"effekt_staerke": {"lustig": 0.05}}
        self.entwurf("lustig", ["effekte_viel"])                          # unter 0,1: 🎆 hebt nie an
        self.assertEqual(self.p()["effekt_staerke"]["lustig"], 0.05)

    def test_regression_alte_bewertungen(self):
        for n, (energie, bpm) in enumerate(((0.4, 100.0), (0.8, 140.0)), 1):
            self.con.execute("INSERT INTO tracks (datei, titel, kuenstler, quelle, sha256, dauer_s, bpm, energie, "
                             "beats, verlauf, stimmungen, erstellt) VALUES (?, 'T', 'K', 'CC0', ?, 200, ?, ?, '[]', "
                             "'[]', '[]', 'x')", (f"t{n}.mp3", f"s{n}", bpm, energie))
        for stimmung, momente, daumen, gruende, musik in self.ALTE_FOLGE:
            self.entwurf(stimmung, gruende, daumen, momente, 2 if musik else None)
        p, ziel = regie_lernen.aktuelle(self.con, self.konfig)
        alt = {"abwechslung": 0.7, "beats_pro_schnitt": 2, "dauer_faktor": 0.9, "luecke_max_s": 4.0,
               "max_je_match": 3, "moment_bonus": {"a": -0.5, "b": 1.0, "c": -0.5, "e": 0.5, "f": 0.5},
               "musik_pegel": 0.35, "puffer_nach_s": 1.8, "puffer_vor_s": 3.0, "seg_min_faktor": 1.322,
               "stimmung_bonus": {"chill": 0.25, "episch": -0.25, "lustig": 0.5, "spannend": -0.25},
               "track_malus": {"2": 1.0}, "uebergang_faktor": 1.21}
        self.assertEqual({k: v for k, v in p.items() if k in alt}, alt)
        self.assertEqual(set(p) - set(alt), {"effekt_staerke", "effekt_hektik"})
        self.assertEqual((p["effekt_staerke"], p["effekt_hektik"]), ({}, 0.81))   # zweimal „zu hektisch“
        self.assertEqual(ziel["lustig"], {"energie": 0.64, "bpm": 117.6})

    def test_lernstand_zeigt_effekte(self):
        self.konfig.daten["regie"]["effekte"]["an"] = True                 # MitRegieMaterial schaltet sie aus
        self.konfig.daten["regie"]["vorgaben"] = {"effekt_staerke": {"chill": 0.5, "lustig": 0.8}}
        self.entwurf("episch", ["effekte_viel", "hektisch"])
        self.entwurf("lustig", ["action"])

        def zeile():
            return [z for z in regie_lernen.lernstand_text(self.con, self.konfig).splitlines()
                    if z.startswith("Effekte:")]

        self.assertEqual(zeile(), ["Effekte: episch 0.85 · spannend 1.0 · lustig 0.92 (Vorgabe 0.8) · "
                                   "frustriert 1.0 · chill 0.5 (deine Vorgabe) · Hektik 0.9"])
        self.konfig.daten["regie"]["effekte"]["an"] = False
        self.assertTrue(zeile()[0].endswith("Hektik 0.9 – ausgeschaltet ([regie.effekte] an = false)"))

    def test_cli_gruende_aus_einer_quelle(self):
        import argparse

        from clip_pipeline import cli

        unter = next(a for a in cli.baue_parser()._actions if isinstance(a, argparse._SubParsersAction))
        grund = next(a for a in unter.choices["bewerte"]._actions if a.dest == "grund")
        self.assertEqual(set(grund.choices), set(regie_lernen.GRUENDE))
