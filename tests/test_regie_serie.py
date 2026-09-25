"""Regisseur: Multikills am Stück – Anfang bei der ersten Aktion (mein Umhauen), Serie als ein Stück,
lange Pausen per Jump-Cut übersprungen. Alte Momente ohne aktion_sekunden verhalten sich wie vorher."""

import json
import subprocess
import unittest
from pathlib import Path

from clip_pipeline import entwurf, regie
from clip_pipeline.zeit import iso, jetzt

from tests.hilfen import HAT_FFMPEG
from tests.regie_hilfen import FARBEN, MOMENTE, MitRegieMaterial, farbe_bei, naechste_farbe

# Team-Wipe aus einem echten Match (relativ zum Clip, anonymisiert): zwei Gegner umgehauen, 17 s Pause,
# dann der Wipe – alle drei Kills in 0,2 s.
WIPE = {"kill_sekunden": [28.74, 28.81, 28.91], "aktion_sekunden": [8.0, 11.78, 28.91]}
# Serie, die nicht einmal mit Jump-Cut in serie_max_s passt: 10 Aktionen je 3,5 s (keine Lücke > 4 s)
LANG = {"kill_sekunden": [round(40.0 + 0.1 * i, 1) for i in range(10)],
        "aktion_sekunden": [round(2.0 + 3.5 * i, 1) for i in range(10)]}
BEATS = [round(0.4 * i, 3) for i in range(1, 900)]  # 150 BPM


def kandidat(schluessel: str, mk: dict, *, dauer: float = 40.0, stimmung: str = "episch", punkte: float = 5.0,
             match: str = "m1", p: dict | None = None) -> regie.Kandidat:
    """Wie regie.kandidaten, nur ohne Datenbank."""
    p = {**regie.PARAMETER, **(p or {})}
    kern, muss, grund = regie._kern(mk, dauer, p)
    kern, muss, teile, teile_muss, serie = regie._teile(mk, dauer, kern, muss, p)
    return regie.Kandidat(schluessel, f"/x/{schluessel}.mp4", dauer, stimmung, punkte, punkte, None, match,
                          kern, muss, grund, mk, teile=teile, teile_muss=teile_muss, serie=serie)


def liste_um(segmente: list[dict], fmt: str = "short") -> dict:
    """Minimale Schnittliste um Segmente herum – für pruefe_liste."""
    return {"version": 3, "art": "regie", "name": "test", "format": fmt, "aufloesung": [1080, 1920], "fps": 30,
            "dauer_s": segmente[-1]["zeit_ende"], "stimmung": "episch", "parameter": {}, "musik": None,
            "overlay": None, "bogen": [], "segmente": segmente, "hinweise": [], "erstellt": "x"}


class Logik(unittest.TestCase):
    def test_kern_beginnt_bei_der_fruehesten_aktion(self):
        p = regie.PARAMETER
        kern, muss, grund = regie._kern(WIPE, 40.0, p)
        self.assertEqual((kern[0], muss[0]), (8.0 - p["puffer_vor_s"], 7.0))    # erstes Umhauen, nicht der Wipe
        self.assertAlmostEqual(kern[1], 28.91 + p["puffer_nach_s"])             # Ende wie bisher: letzter Kill
        self.assertAlmostEqual(muss[1], 29.41)
        self.assertEqual(grund, "3 Kill(s)")
        ohne = regie._kern({"kill_sekunden": WIPE["kill_sekunden"]}, 40.0, p)  # alte Merkmale
        self.assertAlmostEqual(ohne[0][0], 28.74 - p["puffer_vor_s"])
        # Aktion vor dem Dateibeginn: auf 0 geklemmt
        kern, muss, _ = regie._kern({"kill_sekunden": [3.0, 3.1], "aktion_sekunden": [-4.0, 3.1]}, 20.0, p)
        self.assertEqual((kern[0], muss[0]), (0.0, 0.0))

    def test_aktionen_robust(self):
        # None = kein Umhauen (der Kill selbst); Umhauen nach dem Kill gibt es nicht; unpassende Liste = alt
        self.assertEqual(regie._aktionen({"kill_sekunden": [5.0, 9.0], "aktion_sekunden": [None, 12.0]}), [5.0, 9.0])
        self.assertIsNone(regie._aktionen({"kill_sekunden": [5.0, 9.0], "aktion_sekunden": [1.0]}))
        self.assertIsNone(regie._aktionen({"kill_sekunden": [5.0]}))
        self.assertIsNone(regie._aktionen({"kill_sekunden": [], "aktion_sekunden": []}))
        # Aktion vor dem Dateibeginn bleibt negativ (kein künstlicher Anker bei 0), hinten auf die Datei geklemmt
        self.assertEqual(regie.anker({"kill_sekunden": [5.0, 5.0], "aktion_sekunden": [-2.0, 30.0]}, 20.0),
                         [-2.0, 5.0])
        self.assertEqual(regie.anker({"kill_sekunden": [25.0], "aktion_sekunden": [24.0]}, 20.0), [19.75])

    def test_aktion_vor_dem_dateibeginn(self):
        # Team-Wipe-Clip, dessen Umhauen vor der Datei liegt (alter Bot-Clip, kurze Aufnahme, 60-s-Kappung):
        # kein Schnipsel am Dateianfang, sondern ein Stück ab 2 s vor dem Wipe – wie bei der Lücke in der Datei
        for mk, dauer, stueck, muss in (
                ({"kill_sekunden": [8.0, 8.0, 8.0], "aktion_sekunden": [-7.0, -3.0, 8.0]}, 13.0, (6.0, 9.5), (7.0, 8.5)),
                ({"kill_sekunden": [8.0, 8.1, 8.2], "aktion_sekunden": [-7.0, -5.0, 8.2]}, 14.0, (6.0, 9.7), (7.0, 8.7)),
                ({"kill_sekunden": [55.0, 55.0], "aktion_sekunden": [-10.5, 54.5]}, 60.0, (52.5, 56.5), (53.5, 55.5))):
            with self.subTest(aktionen=mk["aktion_sekunden"]):
                k = kandidat("clip:1", mk, dauer=dauer)
                self.assertEqual((k.teile, k.teile_muss, k.kern, k.muss, k.serie), ([stueck], [muss], stueck, muss, True))
                self.assertFalse(regie.serie_zu_lang(k, regie.FORMATE["short"]))
                for fmt in ("short", "zusammenschnitt"):
                    for raster in (BEATS, []):
                        segs = regie.plane_zeitleiste([k], raster, regie.FORMATE[fmt], dict(regie.PARAMETER), 30)
                        self.assertEqual(regie.pruefe_liste(liste_um(segs, fmt)), [], (fmt, len(raster)))
                        self.assertEqual(len(segs), 1)
                        self.assertGreaterEqual(segs[0]["quelle_start_s"], stueck[0] - 0.1)  # nichts vom Dateianfang
                        self.assertLessEqual(segs[0]["quelle_start_s"], muss[0])
        # Reicht die Gruppe über den Dateibeginn, beginnt das Stück wie bisher bei 0
        k = kandidat("clip:2", {"kill_sekunden": [2.0, 2.0], "aktion_sekunden": [-1.0, 2.0]}, dauer=10.0)
        self.assertEqual((k.teile, k.kern[0], k.muss[0]), ([(0.0, 3.5)], 0.0, 0.0))
        # Weitere Lücke in der Datei: Jump-Cut wie gewohnt, nur der Teil vor der Datei fällt weg
        k = kandidat("clip:3", {"kill_sekunden": [12.0, 12.1], "aktion_sekunden": [-7.0, 3.0]}, dauer=20.0)
        self.assertEqual((k.teile, k.teile_muss), ([(1.0, 4.5), (10.0, 13.6)], [(2.0, 3.5), (11.0, 12.6)]))

    def test_sprung_teile_echtes_muster(self):
        teile, muss = regie.sprung_teile([8.0, 11.78, 28.74, 28.81, 28.91], (5.5, 30.41), 4.0)
        self.assertEqual(teile, [(5.5, 13.28), (26.74, 30.41)])  # 1,5 s nach der Aktion raus, 2 s vor der nächsten rein
        self.assertEqual(muss, [(7.0, 12.28), (27.74, 29.41)])
        k = kandidat("clip:1", WIPE)
        self.assertEqual(k.teile, teile)
        self.assertEqual(k.teile_muss, muss)
        self.assertTrue(k.serie)
        self.assertAlmostEqual(k.kern_laenge, 7.78 + 3.67)
        self.assertAlmostEqual(k.min_laenge, (13.28 - 7.0) + (29.41 - 26.74))

    def test_keine_luecke_ein_teil(self):
        teile, muss = regie.sprung_teile([8.0, 10.5, 14.5, 18.4], (5.5, 19.9), 4.0)  # Abstände ≤ 4 s
        self.assertEqual((teile, muss), ([(5.5, 19.9)], [(7.0, 18.9)]))
        # Gelerntes/Vorgabe darf die Lücke nicht unter NACH + VOR + 0,5 s drücken (Teile würden sich überlappen)
        self.assertEqual(len(regie.sprung_teile([8.0, 11.9], (5.5, 13.4), 1.0)[0]), 1)
        self.assertEqual(len(regie.sprung_teile([8.0, 12.1], (5.5, 13.6), 1.0)[0]), 2)
        # Genau luecke_max_s ist keine Lücke – auch wenn die Gleitkomma-Differenz knapp darüber liegt (8,3 − 4,3)
        for x in [round(0.1 * i, 1) for i in range(600)]:
            self.assertEqual(len(regie.sprung_teile([x, round(x + 4.0, 1)], (0.0, 70.0), 4.0)[0]), 1, x)
        self.assertEqual(regie.sprung_teile([], (1.0, 5.0), 4.0), ([(1.0, 5.0)], [(1.0, 5.0)]))
        k = kandidat("clip:2", {"kill_sekunden": [10.0, 10.5], "aktion_sekunden": [7.0, 10.5]})
        self.assertEqual((len(k.teile), k.teile_muss, k.serie), (1, [k.muss], True))

    def test_einzelkill_mit_fernem_umhauen(self):
        # Jump-Cut auch für einen Einzelkill – aber keine Serie: Obergrenze bleibt seg_max_s
        k = kandidat("clip:3", {"kill_sekunden": [15.0], "aktion_sekunden": [4.0]})
        self.assertEqual((len(k.teile), k.serie), (2, False))
        self.assertEqual(k.max_laenge(regie.FORMATE["short"]), regie.FORMATE["short"]["seg_max_s"])
        self.assertEqual(kandidat("clip:1", WIPE).max_laenge(regie.FORMATE["short"]), 20.0)
        self.assertEqual(kandidat("clip:1", WIPE).max_laenge(regie.FORMATE["zusammenschnitt"]), 30.0)

    def test_alte_merkmale_wie_vorher(self):
        # Sollwerte aus dem Regisseur vor der Änderung (Stand 29502a3) – gleiche Kandidaten, gleiche Beats
        ks = [kandidat(f"datei:{i}", {"kill_sekunden": kills, "tod_sekunde": 4.0 if s == "frustriert" else None},
                       dauer=20.0, stimmung=s, punkte=float(serie), match=m)
              for i, (s, serie, kills, m) in enumerate(MOMENTE, 1)]
        self.assertTrue(all(len(k.teile) == 1 and not k.serie and k.teile_muss == [k.muss] for k in ks))
        segs = regie.plane_zeitleiste(ks, BEATS, regie.FORMATE["short"], dict(regie.PARAMETER), 30)
        self.assertEqual([(s["quelle_start_s"], s["quelle_ende_s"], s["zeit_ende"]) for s in segs], [
            (3.56, 13.96, 10.4), (2.5, 10.5, 18.4), (4.56, 10.96, 24.8), (5.5, 9.5, 28.8), (3.5, 11.5, 36.8),
            (6.12, 12.92, 43.6), (6.5, 10.5, 47.6), (5.88, 13.08, 54.8), (0.12, 6.92, 61.6), (2.5, 6.5, 65.6),
            (8.5, 12.5, 69.6), (3.5, 9.5, 75.6), (6.12, 12.92, 82.4), (4.5, 8.5, 86.4), (5.88, 13.08, 93.6),
            (5.5, 13.5, 101.6)])
        self.assertFalse(any("teil" in s for s in segs))
        gewaehlt, ziel, hinweise = regie.waehle(ks, regie.FORMATE["short"], dict(regie.PARAMETER))
        self.assertEqual([k.schluessel for k in gewaehlt],
                         ["datei:1", "datei:2", "datei:12", "datei:16", "datei:3", "datei:5"])
        self.assertEqual((ziel, hinweise), (45.0, []))

    def test_plane_zeitleiste_mit_jump_cut(self):
        p = dict(regie.PARAMETER)
        vorher = kandidat("datei:1", {"kill_sekunden": [8.0]}, dauer=20.0, stimmung="lustig")
        wipe = kandidat("clip:1", WIPE)
        danach = kandidat("datei:2", {"kill_sekunden": [6.0, 8.0]}, dauer=20.0, stimmung="chill")
        for fmt in ("short", "zusammenschnitt"):
            for raster in (BEATS, []):
                segs = regie.plane_zeitleiste([vorher, wipe, danach], raster, regie.FORMATE[fmt], p, 30)
                self.assertEqual(regie.pruefe_liste(liste_um(segs, fmt)), [], (fmt, len(raster)))
                self.assertEqual([s["nr"] for s in segs], [1, 2, 3, 4])
                eins, zwei = segs[1], segs[2]
                self.assertEqual((eins["teil"], zwei["teil"]), (1, 2))
                self.assertEqual({eins["moment"], zwei["moment"], eins["datei"], zwei["datei"]},
                                 {"clip:1", "/x/clip:1.mp4"})
                self.assertEqual(zwei["uebergang"], {"art": "schnitt", "dauer_s": 0.0})    # harter Schnitt
                self.assertAlmostEqual(eins["zeit_ende"], zwei["zeit_start"], places=6)   # direkt hintereinander
                self.assertEqual(eins["quelle_ende_s"], 13.28)                             # Schnittpunkte fest
                self.assertEqual(zwei["quelle_start_s"], 26.74)
                self.assertLessEqual(eins["quelle_start_s"], 7.0)                          # erstes Umhauen drin
                self.assertGreaterEqual(zwei["quelle_ende_s"], 29.41)                      # Wipe drin
                laenge = sum(s["quelle_ende_s"] - s["quelle_start_s"] for s in (eins, zwei))
                self.assertAlmostEqual(laenge, zwei["zeit_ende"] - eins["zeit_start"], places=3)
                self.assertLessEqual(laenge, regie.FORMATE[fmt]["serie_max_s"] + 2.0 + 1e-6)
                self.assertFalse(eins["auf_beat"])                     # nur das Ende des letzten Teils rastet ein
                self.assertEqual(zwei["auf_beat"], bool(raster))
                if raster:
                    self.assertTrue(any(abs(b - zwei["zeit_ende"]) < 1e-6 for b in raster))
                self.assertNotIn("teil", segs[0])
                self.assertNotIn("teil", segs[3])

    def test_serie_bleibt_ganz_statt_gekuerzt(self):
        # Zusammenschnitt: länger als serie_max_s -> trotzdem ganz (alle Aktionen und Kills im Bild)
        k = kandidat("clip:9", LANG, dauer=60.0, punkte=10.0)
        self.assertGreater(k.min_laenge, regie.FORMATE["zusammenschnitt"]["serie_max_s"])
        self.assertTrue(regie.serie_zu_lang(k, regie.FORMATE["short"]))
        self.assertFalse(regie.serie_zu_lang(kandidat("clip:1", WIPE), regie.FORMATE["short"]))
        self.assertFalse(regie.serie_zu_lang(kandidat("datei:1", {"kill_sekunden": [8.0]}), regie.FORMATE["short"]))
        segs = regie.plane_zeitleiste([k], BEATS, regie.FORMATE["zusammenschnitt"], dict(regie.PARAMETER), 60)
        self.assertEqual(regie.pruefe_liste(liste_um(segs, "zusammenschnitt")), [])
        for t in [*LANG["aktion_sekunden"], *LANG["kill_sekunden"]]:
            self.assertTrue(any(s["quelle_start_s"] <= t <= s["quelle_ende_s"] for s in segs), t)
        self.assertGreaterEqual(segs[-1]["zeit_ende"], k.min_laenge - 1e-6)

    def test_pruefe_liste_erkennt_kaputte_teile(self):
        segs = regie.plane_zeitleiste([kandidat("clip:1", WIPE)], BEATS, regie.FORMATE["short"],
                                      dict(regie.PARAMETER), 30)
        self.assertEqual(regie.pruefe_liste(liste_um(segs)), [])

        def rueckwaerts(s):  # Teil 2 beginnt in der Quelle vor dem Ende von Teil 1 (Muss passend verschoben)
            laenge, von = s[1]["quelle_ende_s"] - s[1]["quelle_start_s"], s[0]["quelle_ende_s"] - 1.0
            s[1].update(quelle_start_s=von, quelle_ende_s=von + laenge, muss=[von + 0.1, von + 0.2])

        faelle = {
            "andere Datei": lambda s: s[1].update(datei="/x/anders.mp4"),
            "anderer Moment": lambda s: s[1].update(moment="clip:2"),
            "rückwärts": rueckwaerts,
            "Blende": lambda s: s[1].update(uebergang={"art": "fade", "dauer_s": 0.5}),
            "Lücke in der Zählung": lambda s: s[1].update(teil=3),
            "ohne Teil 1": lambda s: s[0].pop("teil"),
        }
        for was, aendern in faelle.items():
            kaputt = json.loads(json.dumps(segs))
            aendern(kaputt)
            self.assertEqual(regie.pruefe_liste(liste_um(kaputt)),
                             [f"Segment 2: Teil {kaputt[1]['teil']} passt nicht zum vorigen Teil"], was)
        kaputt = json.loads(json.dumps(segs))
        kaputt[0]["teil"] = 0
        self.assertTrue(regie.pruefe_liste(liste_um(kaputt)))  # Schema: teil ≥ 1


class Auswahl(MitRegieMaterial):
    """compose mit Serien – ohne Videos (der Regisseur prüft nur, ob die Datei da ist) und ohne Musik."""

    def moment(self, schluessel: str, mk: dict, *, dauer: float, stimmung: str = "episch", match: str = "m1"):
        datei = self.tmp / "momente" / f"{schluessel.replace(':', '_')}.mp4"
        datei.parent.mkdir(parents=True, exist_ok=True)
        datei.touch()
        mk = {"kills": len(mk.get("kill_sekunden", [])), "max_gruppe": len(mk.get("kill_sekunden", [])), **mk}
        self.con.execute(
            """INSERT INTO momente (schluessel, match_id, datei, start_s, ende_s, kills, stimmung, sicherheit,
                                    quelle, merkmale, erstellt, geaendert)
               VALUES (?, ?, ?, 0, ?, ?, ?, 0.8, 'regel', ?, ?, ?)""",
            (schluessel, match, str(datei), dauer, mk["kills"], stimmung, json.dumps(mk), iso(jetzt()), iso(jetzt())))

    def alte_momente(self, anzahl: int):
        for i, (stimmung, serie, kills, match) in enumerate(MOMENTE[:anzahl], 1):
            self.moment(f"datei:{i}", {"kill_sekunden": kills, "max_gruppe": serie,
                                        "tod_sekunde": 4.0 if stimmung == "frustriert" else None},
                        dauer=20.0, stimmung=stimmung, match=match)

    def lies(self, e):
        return json.loads(Path(e["datei"]).read_text(encoding="utf-8"))

    def test_short_ohne_zu_lange_serie_zusammenschnitt_mit(self):
        self.alte_momente(8)
        self.moment("clip:1", WIPE, dauer=40.0, match="w1")
        self.moment("clip:9", LANG, dauer=60.0, match="w2")           # stärkster Moment – aber zu lang für Short
        short = self.lies(regie.erstelle(self.con, self.konfig, "short"))
        self.assertEqual(regie.pruefe_liste(short), [])
        momente = [s["moment"] for s in short["segmente"]]
        self.assertNotIn("clip:9", momente)
        self.assertIn("1 Serie(n) zu lang für Short (> 20 s am Stück)", short["hinweise"])
        self.assertEqual(short["auswahl"]["kandidaten"], 9)
        self.assertEqual(momente.count("clip:1"), 2)                     # Wipe: beide Teile, hintereinander
        i = momente.index("clip:1")
        self.assertEqual([short["segmente"][i]["teil"], short["segmente"][i + 1]["teil"]], [1, 2])
        # Gezählt werden Momente, nicht Segmente
        eindeutig = list(dict.fromkeys(momente))
        self.assertEqual(len(short["bogen"]), len(eindeutig))
        self.assertEqual(short["auswahl"]["neu"] + short["auswahl"]["schon_gezeigt"], len(eindeutig))
        self.assertIn("Jump-Cut", short["segmente"][i]["grund"])

        ganz = self.lies(regie.erstelle(self.con, self.konfig, "zusammenschnitt"))
        self.assertEqual(regie.pruefe_liste(ganz), [])
        lang = [s for s in ganz["segmente"] if s["moment"] == "clip:9"]
        self.assertEqual([s.get("teil") for s in lang], [1, 2])
        for t in [*LANG["aktion_sekunden"], *LANG["kill_sekunden"]]:
            self.assertTrue(any(s["quelle_start_s"] <= t <= s["quelle_ende_s"] for s in lang), t)
        self.assertEqual(ganz["auswahl"]["kandidaten"], 10)
        self.assertFalse([h for h in ganz["hinweise"] if "Serie" in h])

    def test_nur_zu_lange_serien(self):
        self.moment("clip:9", LANG, dauer=60.0)
        with self.assertRaisesRegex(regie.RegieFehler, "zu lang"):
            regie.erstelle(self.con, self.konfig, "short")
        self.assertEqual(regie.pruefe_liste(self.lies(regie.erstelle(self.con, self.konfig, "zusammenschnitt"))), [])

    def test_nachschnitt_mit_start_s(self):
        # Neu geschnittene Datei, Moment ab 5 s: Sekunden zählen ab Dateibeginn, die Datei ist bis ende_s nutzbar
        self.moment("clip:1", {k: [t + 5.0 for t in v] for k, v in WIPE.items()}, dauer=45.0)
        self.con.execute("UPDATE momente SET start_s = 5.0 WHERE schluessel = 'clip:1'")
        liste = self.lies(regie.erstelle(self.con, self.konfig, "short"))
        self.assertEqual(regie.pruefe_liste(liste), [])
        eins, zwei = liste["segmente"]
        self.assertEqual((eins["quelle_ende_s"], zwei["quelle_start_s"], zwei["quelle_dauer_s"]), (18.28, 31.74, 45.0))
        self.assertLessEqual(eins["quelle_start_s"], 12.0)

    def test_alte_momente_ohne_teile(self):
        self.alte_momente(10)
        liste = self.lies(regie.erstelle(self.con, self.konfig, "short"))
        self.assertEqual(regie.pruefe_liste(liste), [])
        self.assertFalse(any("teil" in s for s in liste["segmente"]))
        self.assertEqual(len(liste["bogen"]), len(liste["segmente"]))
        self.assertFalse([h for h in liste["hinweise"] if "Serie" in h])


def zweifarbig(ziel: Path, erste: tuple[int, int, int], zweite: tuple[int, int, int], dauer: float) -> Path:
    """Video, das zur Hälfte die Farbe wechselt – so sieht man im Ergebnis, ob der Jump-Cut die Lücke überspringt."""
    ziel.parent.mkdir(parents=True, exist_ok=True)
    farbe = lambda f: "0x%02x%02x%02x" % f  # noqa: E731
    halb = dauer / 2
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", f"color=c={farbe(erste)}:s=640x360:r=30:d={halb}",
                    "-f", "lavfi", "-i", f"color=c={farbe(zweite)}:s=640x360:r=30:d={halb}",
                    "-f", "lavfi", "-i", f"sine=frequency=300:duration={dauer}",
                    "-f", "lavfi", "-i", f"sine=frequency=600:duration={dauer}",
                    "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]", "-map", "2:a", "-map", "3:a",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                    str(ziel)], check=True)
    return ziel


@unittest.skipUnless(HAT_FFMPEG, "ffmpeg fehlt")
class Render(MitRegieMaterial):
    def test_entwurf_mit_jump_cut(self):
        self.momente_anlegen(MOMENTE[:2], farbig=True)
        # Wipe-Clip: bis 20 s gelb (erstes Umhauen), danach magenta (der Wipe); die Lücke dazwischen fällt weg
        datei = zweifarbig(self.tmp / "momente" / "wipe.mp4", FARBEN[3], FARBEN[4], 40.0)
        mk = {**WIPE, "kills": 3, "max_gruppe": 3}
        self.con.execute(
            """INSERT INTO momente (schluessel, match_id, datei, start_s, ende_s, kills, stimmung, sicherheit,
                                    quelle, merkmale, erstellt, geaendert)
               VALUES ('clip:1', 'w1', ?, 0, 40, 3, 'episch', 0.8, 'regel', ?, ?, ?)""",
            (str(datei), json.dumps(mk), iso(jetzt()), iso(jetzt())))
        e = regie.erstelle(self.con, self.konfig, "short")
        liste = json.loads(Path(e["datei"]).read_text(encoding="utf-8"))
        teile = [s for s in liste["segmente"] if s["moment"] == "clip:1"]
        self.assertEqual([s["teil"] for s in teile], [1, 2])
        r = entwurf.entwurf(self.con, self.konfig, e["entwurf"])
        video = Path(r["datei"])
        text = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration", "-of", "csv=p=0",
                               str(video)], capture_output=True, text=True, check=True).stdout
        dauern = {art: float(d) for art, d in (z.split(",") for z in text.split())}
        self.assertAlmostEqual(dauern["video"], liste["dauer_s"], delta=0.1)
        self.assertAlmostEqual(dauern["audio"], liste["dauer_s"], delta=0.1)
        # Mitte von Teil 1: noch vor der Lücke (gelb); Mitte von Teil 2: schon der Wipe (magenta)
        for s, farbe in zip(teile, (3, 4)):
            mitte = (s["zeit_start"] + s["zeit_ende"]) / 2
            self.assertEqual(naechste_farbe(farbe_bei(video, mitte)), farbe, s)


if __name__ == "__main__":
    unittest.main()
