"""Regisseur 2.0/2.1, Effekt-Planer (effekte.py) und Schnittliste v4 – ohne Video, bis auf die compose-Klasse am Ende.

Im Spielbild: Zoom (punch, akzent, meme, shake), Übergänge (Mix), Tempo (Zeitlupe/Zeitraffer), Look und seit 28.09.
die Impacts flash/shake/rgb mit Stil-Rotation. Titel/Zähler im Short im unscharfen Rand, im 16:9 kein Zähler und
der Titel nur in der Blende. Anker = mein Umhauen (aktion_sekunden), sonst der Kill; ein Titel je Serie an ihrem Ende."""

import copy
import json
import random
import tempfile
import unittest
from pathlib import Path

from clip_pipeline import effekte, entwurf, konfig, regie, schema
from clip_pipeline.medien import MedienFehler

from tests.regie_hilfen import MOMENTE, MitRegieMaterial
from tests.test_regie_serie import BEATS, WIPE, kandidat, liste_um

ERLAUBT = {"punch", "akzent", "meme", "shake", "tilt", "einzug", "drift_ein", "drift_aus", "flash", "strobe", "farbpop",
           "kontrast", "hue", "negativ", "blur", "pixel", "vignette", "rgb", "titel", "zaehler", "sfx"}
ZOOM = {"punch", "akzent", "meme", "shake", "tilt", "einzug"}

# Regisseur 2.0-Verhalten für die Logik-Tests (Ketten, Titel, Budget, Akzente): ein Finisher-/Nebenstil (Punch), Beat
# nur Zoom-Puls, kein Einstieg, kein Drift, alte Akzent-Stärken. Die Katalog-Tests (Katalog2) prüfen das Neue.
KLASSISCH = {"episch": (0.5, 2.5), "spannend": (0.4, 2.5), "lustig": (0.3, 3.0), "frustriert": (0.0, None),
             "chill": (0.0, None)}


def klassisch(test: unittest.TestCase) -> None:
    from unittest import mock

    profil = copy.deepcopy(effekte.PROFIL)
    for st, (akzent, ruhe) in KLASSISCH.items():
        profil[st].update(akzent=akzent, max_ruhe_s=ruhe, einstieg=0.0, drift=0.0)
    profil["lustig"]["lupe"] = 0.0
    for patch in (mock.patch.object(effekte, "PROFIL", profil),
                  mock.patch.object(effekte, "STILE", (("punch",),)),
                  mock.patch.object(effekte, "NEBEN_STILE", (("punch",),)),
                  mock.patch.object(effekte, "BEAT_STILE", (("akzent",),))):
        patch.start()
        test.addCleanup(patch.stop)


def seg(nr, moment, qs, qe, zs, *, art="schnitt", d=0.0, stimmung="episch", teil=None, dauer=40.0):
    """Segment von Hand: Quelle qs … qe ab Zeitleiste zs, Übergang in das Segment (art, d)."""
    s = {"nr": nr, "moment": moment, "clip_id": None, "match_id": None, "datei": f"/x/{moment}.mp4",
         "stimmung": stimmung, "quelle_start_s": qs, "quelle_ende_s": qe, "quelle_dauer_s": dauer, "muss": [qs, qe],
         "zeit_start": zs, "zeit_ende": round(zs + qe - qs, 3), "uebergang": {"art": art, "dauer_s": d}}
    if teil:
        s["teil"] = teil
    return s


def moment(schluessel, mk, *, stimmung="episch", max_gruppe=0, victory=False, dauer=40.0):
    return regie.Kandidat(schluessel, f"/x/{schluessel}.mp4", dauer, stimmung, 3.0, 3.0, None, None, (0.0, dauer),
                          (0.0, dauer), "test", mk, max_gruppe=max_gruppe, victory=victory)


def fx_konfig(**effekte_):
    k = konfig.lade()
    k.daten.setdefault("regie", {})["effekte"] = {"an": True, **effekte_}
    return k


def plane(segmente, reihe, *, fmt="short", beats=(), p=None, k=None, stimmung="episch"):
    """Plan über Segmente von Hand; die Liste muss danach gültig sein."""
    p = {**regie.PARAMETER, **(p or {})}
    fx = effekte.plane(segmente, reihe, p, k or fx_konfig(), fmt, 30 if fmt == "short" else 60, list(beats),
                       stimmung=stimmung)
    liste = {**liste_um(segmente, fmt), "version": 4, "effekte": fx, "stimmung": stimmung}
    fehler = regie.pruefe_liste(liste)
    if fehler:
        raise AssertionError(fehler)
    return liste


def ereignisse(liste, art=None):
    return [(s["nr"], e) for s in liste["segmente"] for e in s.get("effekte", []) if art in (None, e["art"])]


def titel(liste):
    return [(e["text"], e["t_s"]) for _, e in ereignisse(liste, "titel")]


def ein_moment(mk, *, stimmung="episch", ende=20.0, **kw):
    """Ein Segment 0 … ende mit einem Moment."""
    return plane([seg(1, "m", 0.0, ende, 0.0, stimmung=stimmung)], [moment("m", mk, stimmung=stimmung, **kw)],
                 stimmung=stimmung)


class Ketten(unittest.TestCase):
    def setUp(self):
        klassisch(self)

    def test_ketten_wie_vorbewertung(self):
        self.assertEqual(effekte.ketten([6.0, 8.0], 10.0), [[6.0, 8.0]])
        self.assertEqual(effekte.ketten([17.0, 5.0], 10.0), [[5.0], [17.0]])
        self.assertEqual(effekte.ketten([7.3, 17.3], 10.0), [[7.3, 17.3]])   # genau 10 s (auf ms gerechnet)
        self.assertEqual(effekte.ketten([7.3, 17.4], 10.0), [[7.3], [17.4]])
        self.assertEqual(effekte.ketten([], 10.0), [])

    def test_titel_einmal_je_serie_am_ende(self):
        self.assertEqual(titel(ein_moment({"kill_sekunden": [6.0, 8.0]})), [("DOUBLE KILL", 8.1)])
        # Ü4: nicht DOUBLE und dann TRIPLE, sondern ein Titel am Ende der Serie
        self.assertEqual(titel(ein_moment({"kill_sekunden": [6.0, 8.0, 10.0]})), [("TRIPLE KILL", 10.1)])
        self.assertEqual(titel(ein_moment({"kill_sekunden": [5.0, 17.0]})), [])
        self.assertEqual(titel(ein_moment({"kill_sekunden": [4.0, 5.0, 6.0, 7.0, 8.0]})), [("PENTA KILL", 8.1)])
        self.assertEqual(titel(ein_moment({"kill_sekunden": [4.0, 5.0, 6.0, 7.0, 8.0, 9.0]})), [("MULTI KILL", 9.1)])
        # zwei Serien im selben Moment: je ein Titel
        self.assertEqual(titel(ein_moment({"kill_sekunden": [2.0, 3.0, 15.0, 16.0, 17.0]})),
                         [("DOUBLE KILL", 3.1), ("TRIPLE KILL", 17.1)])

    def test_titel_und_zaehler_zaehlen_was_man_sieht(self):
        # Ein Kill der Serie liegt vor der Datei: zwei im Bild = DOUBLE (bis 07.10. TRIPLE laut Clip, Zähler aber 2)
        liste = ein_moment({"kill_sekunden": [6.0, 8.0]}, max_gruppe=3)
        self.assertEqual(titel(liste), [("DOUBLE KILL", 8.1)])
        self.assertEqual([e["zahl"] for _, e in ereignisse(liste, "zaehler")], [1, 2])
        self.assertEqual(titel(ein_moment({"kill_sekunden": [8.0]}, max_gruppe=3)), [])
        # Mein Umhauen liegt vor dem Segment, der Kill ist im Bild: der Kill zählt (vorher fehlte er)
        segs = [seg(1, "m", 5.0, 20.0, 0.0)]
        liste = plane(segs, [moment("m", {"kill_sekunden": [7.0, 9.0], "aktion_sekunden": [2.0, 8.5]})])
        self.assertEqual([(e["t_s"], e["zahl"]) for _, e in ereignisse(liste, "zaehler")], [(7.0, 1), (8.5, 2)])
        self.assertEqual(titel(liste), [("DOUBLE KILL", 8.6)])

    def test_team_wipe_anker_ist_das_umhauen(self):
        mk = {"kill_sekunden": [12.74, 12.81, 12.91], "aktion_sekunden": [2.0, 5.78, 12.91]}
        liste = ein_moment(mk)
        punches = [(e["t_s"], e["staerke"]) for _, e in ereignisse(liste, "punch")]
        self.assertEqual(punches, [(2.0, 0.35), (5.78, 0.35), (12.91, 0.7)])     # Mini, Mini, Finisher
        self.assertEqual(titel(liste), [("TRIPLE KILL", 13.01)])                 # einmal, am Ende der Serie
        self.assertEqual([(e["t_s"], e["zahl"]) for _, e in ereignisse(liste, "zaehler")],
                         [(2.0, 1), (5.78, 2), (12.91, 3)])
        self.assertEqual(liste["segmente"][0]["kill_s"], [2.0, 5.78, 12.91])
        # Umhauen nach dem Kill gibt es nicht (wie im Schnitt): dann zählt der Kill
        liste = ein_moment({"kill_sekunden": [6.0, 8.0], "aktion_sekunden": [5.0, 9.5]})
        self.assertEqual([e["t_s"] for _, e in ereignisse(liste, "punch")], [5.0, 8.0])

    def test_victory_royale(self):
        # ersetzt den überlappenden TRIPLE KILL, ab letztem Kill + 0,4 s (höchstens 3 s)
        liste = ein_moment({"kill_sekunden": [6.0, 8.0, 10.0]}, victory=True, ende=14.0)
        self.assertEqual(titel(liste), [("VICTORY ROYALE", 10.4)])
        self.assertEqual(ereignisse(liste, "titel")[0][1]["dauer_s"], 3.0)
        # zu kurz bis zum Segmentende: mindestens 1 s, also früher beginnen
        liste = ein_moment({"kill_sekunden": [6.0, 8.0, 10.0]}, victory=True, ende=10.9)
        self.assertEqual(titel(liste), [("VICTORY ROYALE", 9.9)])
        self.assertEqual(ereignisse(liste, "titel")[0][1]["dauer_s"], 1.0)


class Plan(unittest.TestCase):
    def setUp(self):
        klassisch(self)

    def test_finisher_buendel_und_zaehler(self):
        liste = ein_moment({"kill_sekunden": [6.0, 8.0, 10.0]})
        es = [e for _, e in ereignisse(liste)]
        self.assertEqual({e["art"] for e in es} - ERLAUBT, set())            # nie Blitz, Shake, Glitch
        sfx = [(e["t_s"], e["klang"], e["staerke"]) for e in es if e["art"] == "sfx"]
        # Riser endet auf dem ersten Kill des Höhepunkts (hier der einzige Moment)
        self.assertEqual(sfx, [(6.0, "riser", 0.6), (6.0, "tick", 0.5), (8.0, "tick", 0.5), (10.0, "basshit", 0.9)])
        self.assertEqual([(e["t_s"], e["staerke"], e["dauer_s"]) for e in es if e["art"] == "punch"],
                         [(6.0, 0.35, 0.35), (8.0, 0.35, 0.35), (10.0, 0.7, 0.35)])
        self.assertEqual([(e["t_s"], e["zahl"], e["dauer_s"]) for e in es if e["art"] == "zaehler"],
                         [(6.0, 1, 1.5), (8.0, 2, 1.5), (10.0, 3, 1.5)])
        self.assertEqual(liste["effekte"], {"an": True, "profil_version": 2, "look": "cinematic", "look_staerke": 0.8,
                                            "hook": False, "loop": False})

    def test_kills_in_den_griffen_ohne_ereignis(self):
        # Blende 0,8 s um die Grenze bei 10 s: sichtbar erst 0,4 + 0,1 s weg vom Schnitt
        segs = [seg(1, "a", 0.0, 10.0, 0.0, stimmung="spannend"),
                seg(2, "b", 5.0, 15.0, 10.0, art="fade", d=0.8, stimmung="spannend")]
        liste = plane(segs, [moment("a", {"kill_sekunden": [3.0, 9.55]}, stimmung="spannend"),
                             moment("b", {"kill_sekunden": [5.45, 5.55]}, stimmung="spannend")], stimmung="spannend")
        self.assertEqual(liste["segmente"][0]["kill_s"], [3.0])
        self.assertEqual(liste["segmente"][1]["kill_s"], [5.55])
        punches = [(nr, e["t_s"]) for nr, e in ereignisse(liste, "punch")]
        self.assertEqual(punches, [(1, 3.0), (2, 5.55)])
        self.assertEqual([e["zahl"] for _, e in ereignisse(liste, "zaehler")], [1, 1])   # je Szene ab 1
        # Kein Zoom-Start in einer Blende
        fenster = effekte.uebergangs_fenster(liste)
        self.assertEqual(fenster, [("fade", 9.6, 10.4, 1.0)])

    def test_zoom_budget(self):
        liste = ein_moment({"kill_sekunden": [6.0, 6.2, 6.3, 9.0]})
        zooms = sorted(e["t_s"] for _, e in ereignisse(liste) if e["art"] in ZOOM)
        self.assertEqual(zooms, [6.0, 9.0])  # zu dicht: nur der erste Mini-Punch bleibt, der Finisher immer
        self.assertEqual(len([e for _, e in ereignisse(liste, "sfx") if e["klang"] == "tick"]), 3)  # Ton bleibt
        # Finisher schlägt Meme; das Pop bleibt
        liste = ein_moment({"kill_sekunden": [6.0], "jubel_laut_s": [6.2, 12.0]}, stimmung="lustig")
        self.assertEqual([e["art"] for _, e in ereignisse(liste) if e["art"] in ZOOM], ["punch"])
        self.assertIn("pop", [e["klang"] for _, e in ereignisse(liste, "sfx")])

    def test_viele_kills_bleiben_in_den_schema_grenzen(self):
        mk = {"kill_sekunden": [round(1.0 + 0.5 * i, 2) for i in range(30)]}
        liste = ein_moment(mk)  # plane() prüft die Liste
        s = liste["segmente"][0]
        self.assertEqual((len(s["effekte"]), len(s["kill_s"])), (60, 20))
        self.assertEqual(titel(liste), [("MULTI KILL", 15.6)])          # das Wichtigste bleibt
        self.assertIn((15.5, 0.7), [(e["t_s"], e["staerke"]) for _, e in ereignisse(liste, "punch")])

    def test_akzente_auf_dem_beat(self):
        segs = [seg(1, "a", 0.0, 10.0, 0.0, stimmung="spannend"),
                seg(2, "b", 5.0, 15.0, 10.0, art="whip", d=0.25, stimmung="spannend"),
                seg(3, "c", 2.0, 12.0, 20.0, stimmung="spannend")]
        reihe = [moment(m, {"kill_sekunden": kills}, stimmung="spannend")
                 for m, kills in (("a", [4.0]), ("b", []), ("c", [6.0]))]
        beats = [round(0.1 * i, 3) for i in range(1, 300)]
        liste = plane(segs, reihe, beats=beats, stimmung="spannend")
        zooms = sorted((round(effekte.auf_zeitleiste(liste["segmente"][nr - 1], e["t_s"]), 3), e["art"])
                       for nr, e in ereignisse(liste) if e["art"] in ZOOM)
        akzente = [t for t, art in zooms if art == "akzent"]
        self.assertTrue(akzente)
        for (a, _), (b, _) in zip(zooms, zooms[1:]):
            self.assertGreaterEqual(b - a, 0.4 - 1e-9)
        for t in akzente:
            s = next(s for s in liste["segmente"] if s["zeit_start"] <= t < s["zeit_ende"])
            self.assertGreaterEqual(t - s["zeit_start"], 2.5 - 1e-6)            # max_ruhe_s nach dem Schnitt
            self.assertFalse(9.875 < t < 10.125)                               # nicht in der Blende
        vorige = [t for t, _ in zooms]
        for t in akzente:
            davor = [v for v in vorige if v < t]
            self.assertTrue(not davor or t - max(davor) >= 2.5 - 1e-6)
        # Ohne Musik keine Akzente; ein Moment ohne Kill-Zeiten bekommt nur Akzente (und Whoosh)
        self.assertFalse(ereignisse(plane(copy.deepcopy(segs), reihe, stimmung="spannend"), "akzent"))
        b = [e for nr, e in ereignisse(liste) if nr == 2]
        self.assertEqual({(e["art"], e.get("klang")) for e in b}, {("akzent", None), ("sfx", "whoosh")})
        self.assertNotIn("kill_s", liste["segmente"][1])

    def test_stimmungen(self):
        frust = ein_moment({"kill_sekunden": [6.0, 8.0], "tod_sekunde": 12.0}, stimmung="frustriert")
        self.assertEqual(titel(frust), [])                                     # kein Titel
        self.assertEqual({(e["t_s"], e["art"], e.get("klang"), e["staerke"]) for _, e in ereignisse(frust)
                          if e["t_s"] == 12.0},   # 28.09.: Tod = Wackeln (Stärke tod_punch) + Blitz + Einschlag
                         {(12.0, "shake", None, 0.3), (12.0, "flash", None, 0.5), (12.0, "sfx", "einschlag", 0.7)})
        self.assertIn((8.0, "basshit", 0.4),
                      [(e["t_s"], e["klang"], e["staerke"]) for _, e in ereignisse(frust, "sfx")])
        chill = ein_moment({"kill_sekunden": [6.0, 8.0]}, stimmung="chill")
        self.assertEqual({e["art"] for _, e in ereignisse(chill)}, {"titel"})    # kein Punch, kein Zähler, kein Ton
        lustig = ein_moment({"kill_sekunden": [6.0], "jubel_laut_s": [9.0, 12.0]}, stimmung="lustig")
        self.assertEqual([(e["t_s"], e["dauer_s"]) for _, e in ereignisse(lustig, "meme")], [(9.0, 0.83)])
        self.assertNotIn("basshit", [e["klang"] for _, e in ereignisse(lustig, "sfx")])
        for liste in (frust, chill, lustig):
            self.assertEqual({e["art"] for _, e in ereignisse(liste)} - ERLAUBT, set())

    def test_gelernte_staerke(self):
        segs = [seg(1, "a", 0.0, 10.0, 0.0, stimmung="spannend"), seg(2, "b", 0.0, 10.0, 10.0)]
        reihe = [moment("a", {"kill_sekunden": [4.0, 5.0]}, stimmung="spannend"),
                 moment("b", {"kill_sekunden": [4.0, 5.0]})]
        beats = [round(0.5 * i, 3) for i in range(1, 40)]
        liste = plane(segs, reihe, beats=beats, p={"effekt_staerke": {"episch": 0.0}}, stimmung="episch")
        self.assertEqual([e for nr, e in ereignisse(liste) if nr == 2], [])     # episch: nichts
        self.assertTrue([e for nr, e in ereignisse(liste) if nr == 1])
        self.assertEqual((liste["effekte"]["look"], liste["effekte"]["look_staerke"]), ("neutral", 0.0))
        # „zu hektisch“ dämpft die Beat-Akzente: spannend 0,4 × 0,5, episch 0,5 × 0,5
        ruhig = plane(copy.deepcopy(segs), reihe, beats=beats, p={"effekt_hektik": 0.5}, stimmung="episch")
        self.assertEqual({(nr, e["staerke"]) for nr, e in ereignisse(ruhig, "akzent")}, {(1, 0.2), (2, 0.25)})

    def test_riser_und_whoosh(self):
        segs = [seg(1, "a", 0.0, 6.0, 0.0, stimmung="spannend"),
                seg(2, "b", 4.0, 10.0, 6.0, art="whip", d=0.25, stimmung="spannend"),
                seg(3, "c", 4.0, 10.0, 12.0, art="fadeblack", d=0.5, stimmung="frustriert"),
                seg(4, "d", 4.0, 10.0, 18.0, art="fade", d=0.8, stimmung="chill"),
                seg(5, "e", 2.0, 8.0, 24.0, stimmung="episch", teil=1),
                seg(6, "e", 12.0, 16.0, 30.0, stimmung="episch", teil=2)]
        reihe = [moment("a", {}, stimmung="spannend"), moment("b", {}, stimmung="spannend"),
                 moment("c", {}, stimmung="frustriert"), moment("d", {}, stimmung="chill"),
                 moment("e", {"kill_sekunden": [6.0, 14.0], "aktion_sekunden": [3.0, 14.0]})]
        liste = plane(segs, reihe, stimmung="episch")
        whoosh = [(nr, e["t_s"], e["staerke"]) for nr, e in ereignisse(liste, "sfx") if e["klang"] == "whoosh"]
        self.assertEqual(whoosh, [(2, 4.0, 0.6), (4, 4.0, 0.25)])  # nicht bei Schnitt, Abblende, Jump-Cut
        self.assertEqual(effekte.uebergangs_fenster(liste),
                         [("whip", 5.875, 6.125, 1.0), ("fadeblack", 11.75, 12.25, 1.0), ("fade", 17.6, 18.4, 1.0)])
        riser = [(nr, e["t_s"]) for nr, e in ereignisse(liste, "sfx") if e["klang"] == "riser"]
        self.assertEqual(riser, [(5, 3.0)])                        # endet auf dem ersten Umhauen des Höhepunkts
        # Beginnt das Video mit dem Höhepunkt und liegt der Kill vor 1,5 s: kein Riser
        kurz = plane([seg(1, "e", 0.0, 8.0, 0.0)], [moment("e", {"kill_sekunden": [1.2]})])
        self.assertNotIn("riser", [e["klang"] for _, e in ereignisse(kurz, "sfx")])

    def test_zusammenschnitt_titel_nur_in_der_blende(self):
        segs = [seg(1, "a", 0.0, 14.0, 0.0), seg(2, "b", 2.0, 10.0, 14.0, art="wipeleft", d=0.3, stimmung="lustig"),
                seg(3, "c", 0.0, 12.0, 22.0, stimmung="spannend"), seg(4, "d", 0.0, 12.0, 34.0)]
        reihe = [moment("a", {"kill_sekunden": [6.0, 8.0, 10.0]}),
                 moment("b", {"kill_sekunden": [4.0, 5.0]}, stimmung="lustig"),
                 moment("c", {"kill_sekunden": [3.0, 4.0]}, stimmung="spannend"),
                 moment("d", {"kill_sekunden": [3.0, 4.0]}, victory=True)]
        liste = plane(segs, reihe, fmt="zusammenschnitt")
        self.assertEqual(ereignisse(liste, "zaehler"), [])                      # kein Zähler im 16:9
        tt = [(nr, e["text"], e["t_s"], e["dauer_s"]) for nr, e in ereignisse(liste, "titel")]
        # nur nach Segment 1: Blende wipeleft 0,3 s um 14,0 -> 13,85 … 14,15; nach 2 und 3 harter Schnitt,
        # Segment 4 ist das Ende
        self.assertEqual(tt, [(1, "TRIPLE KILL", 13.85, 0.3)])
        a, b = effekte.uebergangs_fenster(liste)[0][1:3]
        [x] = [x for x in effekte.zeitleiste(liste) if x.art == "titel"]
        self.assertAlmostEqual(x.t, a, delta=1e-6)
        self.assertAlmostEqual(x.t + x.dauer, b, delta=1e-6)

    def test_zusammenschnitt_titel_nach_dem_letzten_teil(self):
        # Kampf mit Jump-Cut: A umgehauen bei 3 s, B erledigt bei 5 s, A stirbt bei 12 s – eine Serie, die Pause
        # dazwischen ist herausgeschnitten. Der Finisher-Anker (5 s) liegt in Teil 1, danach kommt nur der Jump-Cut;
        # der Titel gehört in die Blende nach dem LETZTEN Teil (Whip 0,25 um 9,5)
        def teile(zweiter):
            return [seg(1, "e", 1.0, 6.5, 0.0, teil=1), seg(2, "e", *zweiter, 5.5, teil=2),
                    seg(3, "b", 2.0, 10.0, 9.5, art="whip", d=0.25, stimmung="lustig")]

        def titel_16_9(segs, mk):
            liste = plane(segs, [moment("e", mk), moment("b", {}, stimmung="lustig")], fmt="zusammenschnitt")
            return [(x.nr, x.text, x.t, round(x.t + x.dauer, 3)) for x in effekte.zeitleiste(liste) if x.art == "titel"]

        self.assertEqual(titel_16_9(teile((10.0, 14.0)), {"kill_sekunden": [5.0, 12.0], "aktion_sekunden": [5.0, 3.0]}),
                         [(2, "DOUBLE KILL", 9.375, 9.625)])
        # Triple in Teil 1, ein späterer Einzelkill in Teil 2 (eigene Kette): TRIPLE KILL nach dem letzten Teil
        self.assertEqual(titel_16_9(teile((16.0, 20.0)), {"kill_sekunden": [3.0, 4.0, 5.0, 18.0]}),
                         [(2, "TRIPLE KILL", 9.375, 9.625)])

    def test_short_texte_enden_vor_der_zoom_blende(self):
        # Übergang „zoom“ = xfade zoomin: vergrößert das GANZE Bild, das Spielbild wächst über den unscharfen Rand.
        # Titel und Zähler enden darum am Blendenanfang (10,0 − 0,15); bei anderen Blenden bleibt es, wie es war
        segs = [seg(1, "a", 0.0, 10.0, 0.0), seg(2, "b", 2.0, 10.0, 10.0, art="zoom", d=0.3)]
        reihe = [moment("a", {"kill_sekunden": [8.6, 9.2]}), moment("b", {})]

        def texte(liste):
            return [(x.art, x.t, round(x.t + x.dauer, 3)) for x in effekte.zeitleiste(liste)
                    if x.art in ("titel", "zaehler")]

        self.assertEqual(texte(plane(copy.deepcopy(segs), reihe)),
                         [("zaehler", 8.6, 9.2), ("zaehler", 9.2, 9.85), ("titel", 9.3, 9.85)])
        segs[1]["uebergang"] = {"art": "whip", "dauer_s": 0.3}
        self.assertEqual(texte(plane(segs, reihe)),
                         [("zaehler", 8.6, 9.2), ("zaehler", 9.2, 10.7), ("titel", 9.3, 10.0)])

    def test_hook_segmente_ohne_zaehler(self):
        # Vorgriff auf Stufe 4: ein Hook-Segment zählt nicht mit und bekommt vom Planer nichts
        segs = [seg(1, "e", 5.0, 7.0, 0.0), seg(2, "a", 0.0, 10.0, 2.0), seg(3, "e", 0.0, 10.0, 12.0)]
        segs[0]["rolle"] = "hook"
        liste = {**liste_um(segs), "version": 4}
        effekte.plane(segs, [moment("a", {"kill_sekunden": [4.0]}), moment("e", {"kill_sekunden": [6.0]})],
                      dict(regie.PARAMETER), fx_konfig(), "short", 30, [], stimmung="episch")
        self.assertNotIn("effekte", segs[0])
        self.assertEqual([e["zahl"] for _, e in ereignisse(liste, "zaehler")], [1, 1])


class Uebergaenge(unittest.TestCase):
    def setUp(self):
        klassisch(self)

    def test_rotation_glitch_hoehepunkt(self):
        p = dict(regie.PARAMETER)
        self.assertEqual(effekte.uebergang("spannend", 2, False, p, True, 0), ("glitch", 0.2))
        self.assertEqual(effekte.uebergang("spannend", 2, False, p, True, 1), ("whip", 0.25))  # max_glitch 1
        self.assertEqual(effekte.uebergang("spannend", 0, True, p, True, 0), ("schnitt", 0.0))  # in den Höhepunkt
        self.assertEqual(effekte.uebergang("lustig", 4, True, p, True, 0), ("squeezev", 0.3))
        self.assertEqual(effekte.uebergang("episch", 1, False, {**p, "uebergang_faktor": 2.0}, True, 0), ("whip", 0.5))
        for s, (art, d) in regie.UEBERGANG.items():  # aus = wie bisher
            self.assertEqual(effekte.uebergang(s, 1, True, p, False, 5), (art, d))

    def test_mix_ohne_wiederholung_deterministisch(self):
        # 28.09. (Florian: „immer die gleichen Übergänge“): plane_zeitleiste mischt den Pool je Stimmung – jede Art
        # einmal je Runde, nie dieselbe weiche Art zweimal nacheinander, höchstens ein Glitch, Höhepunkt = Schnitt
        def arten_fuer(ks):
            fx, _ = effekte.einstellungen(fx_konfig())
            segs = regie.plane_zeitleiste(ks, BEATS, regie.FORMATE["zusammenschnitt"], dict(regie.PARAMETER), 60, fx)
            return [s["uebergang"]["art"] for s in segs]

        ks = [kandidat(f"m{i}", {"kill_sekunden": [6.0]}, stimmung="spannend", dauer=20.0) for i in range(14)]
        arten = arten_fuer(ks)
        pool = {a for a, _ in effekte.PROFIL["spannend"]["uebergaenge"]}
        self.assertEqual(arten[0], "schnitt")                  # erstes Segment: immer Schnitt
        self.assertEqual(arten[-1], "schnitt")                 # spannender Höhepunkt: harter Schnitt
        self.assertEqual(arten.count("glitch"), 1)             # max_glitch 1, in der ersten Runde einmal gezogen
        self.assertEqual(set(arten[1:-1]), pool)               # 12 Züge aus 10 Arten: jede kam dran
        for a, b in zip(arten[1:-1], arten[2:-1]):
            if a != "schnitt":
                self.assertNotEqual(a, b, arten)               # keine doppelte weiche Art
        self.assertEqual(arten_fuer(ks), arten)                # deterministisch (Seed = Momentfolge)
        anders = arten_fuer([ks[0], *reversed(ks[1:])])
        self.assertEqual(len(anders), len(arten))
        self.assertNotEqual(anders, arten)                     # andere Momentfolge -> anderer Mix
        # Der Mix selbst mit winzigem Pool: genau ein Glitch (Deckel), danach bleibt nur die andere Art – und er
        # bleibt nie auf dem Whip-Rückfall hängen (den gibt es nur, wenn der Pool allein aus Glitch besteht)
        mix, folge, glitches = effekte.Uebergangsmix("x"), [], 0
        for _ in range(6):
            art, _d = mix.waehle("frustriert", [("glitch", 0.2), ("fadeblack", 0.5)], glitches, 1)
            glitches += art == "glitch"
            folge.append(art)
        self.assertEqual((folge.count("glitch"), folge.count("fadeblack")), (1, 5))
        nur_glitch = effekte.Uebergangsmix("y")
        self.assertEqual(nur_glitch.waehle("spannend", [("glitch", 0.2)], 0, 1), ("glitch", 0.2))
        self.assertEqual(nur_glitch.waehle("spannend", [("glitch", 0.2)], 1, 1), ("whip", 0.25))

    def test_hektik_daempft_nur_akzent(self):
        # „zu hektisch“ dämpft die Beat-Akzente und (28.09.) die Impacts flash/shake/rgb – nicht Punch, Titel,
        # Zähler, Klänge. Übergänge, auch der Glitch-Übergang, haben immer Stärke 1 (§4)
        self.assertEqual({"akzent", "flash", "shake", "rgb", "negativ", "blur", "tilt", "einzug"} - effekte.HEKTISCH,
                         set())
        self.assertFalse({"punch", "titel", "zaehler", "sfx", "drift_ein"} & effekte.HEKTISCH)
        segs = [seg(1, "a", 0.0, 10.0, 0.0, stimmung="spannend"),
                seg(2, "b", 2.0, 10.0, 10.0, art="glitch", d=0.2, stimmung="spannend"),
                seg(3, "c", 2.0, 10.0, 18.0, art="whip", d=0.25, stimmung="spannend")]
        reihe = [moment("a", {"kill_sekunden": [4.0, 5.0]}, stimmung="spannend"),
                 moment("b", {}, stimmung="spannend"), moment("c", {}, stimmung="spannend")]
        beats = [round(0.5 * i, 3) for i in range(1, 52)]
        voll = plane(copy.deepcopy(segs), reihe, beats=beats, stimmung="spannend")
        self.assertEqual(voll["segmente"][1]["uebergang"], {"art": "glitch", "dauer_s": 0.2})
        ruhig = plane(copy.deepcopy(segs), reihe, beats=beats, p={"effekt_hektik": 0.5}, stimmung="spannend")
        self.assertEqual(effekte.uebergangs_fenster(ruhig), effekte.uebergangs_fenster(voll))
        self.assertEqual([f[3] for f in effekte.uebergangs_fenster(ruhig)], [1.0, 1.0])   # Glitch, Whip
        self.assertEqual({e["staerke"] for _, e in ereignisse(ruhig, "akzent")}, {0.2})  # 0,4 × 0,5
        nicht_hektisch = lambda l: [(nr, e) for nr, e in ereignisse(l) if e["art"] not in effekte.HEKTISCH]  # noqa: E731
        self.assertEqual(nicht_hektisch(ruhig), nicht_hektisch(voll))      # Punch, Titel, Zähler, Klänge gleich
        # „zu viele Effekte“ für spannend: keine Ereignisse mehr, der Übergang bleibt, wie er ist
        wenig = plane(copy.deepcopy(segs), reihe, p={"effekt_staerke": {"spannend": 0.1}}, stimmung="spannend")
        self.assertEqual(wenig["segmente"][1]["uebergang"], {"art": "glitch", "dauer_s": 0.2})
        self.assertEqual(ereignisse(wenig), [])

    def test_aus_ist_wie_vorher(self):
        ks = [kandidat(f"datei:{i}", {"kill_sekunden": kills, "tod_sekunde": 4.0 if s == "frustriert" else None},
                       dauer=20.0, stimmung=s, punkte=float(n), match=m)
              for i, (s, n, kills, m) in enumerate(MOMENTE, 1)]
        fmt, p = regie.FORMATE["short"], dict(regie.PARAMETER)
        vorher = regie.plane_zeitleiste(ks, BEATS, fmt, p, 30)
        aus, _ = effekte.einstellungen(fx_konfig(an=False))
        self.assertEqual(regie.plane_zeitleiste(ks, BEATS, fmt, p, 30, aus), vorher)
        an, _ = effekte.einstellungen(fx_konfig())
        mit = regie.plane_zeitleiste(ks, BEATS, fmt, p, 30, an)
        self.assertNotEqual([s["uebergang"] for s in mit], [s["uebergang"] for s in vorher])
        self.assertEqual([(s["quelle_start_s"], s["zeit_ende"]) for s in mit],   # Schnitt selbst unverändert
                         [(s["quelle_start_s"], s["zeit_ende"]) for s in vorher])
        liste = liste_um(vorher)
        self.assertEqual(effekte.zeitleiste(liste), [])
        self.assertEqual(effekte.zeitleiste({**liste, "version": 4, "effekte": {"an": False}}), [])

    def test_profile_passen_zum_schema(self):
        arten = set(effekte.uebergangs_arten())
        segment = schema.lade("regie")["properties"]["segmente"]["items"]["properties"]
        text = segment["effekte"]["items"]["properties"]["text"]   # 05.10.: Muster statt Liste (Fail-Titel aus Fakten)
        looks = set(schema.lade("regie")["properties"]["effekte"]["properties"]["look"]["enum"])
        for stimmung, prof in effekte.PROFIL.items():
            self.assertEqual({a for a, _ in prof["uebergaenge"]} - arten, set(), stimmung)
            self.assertEqual(prof["look"][0], effekte.LOOK_JE_STIMMUNG[stimmung])
            self.assertEqual(effekte.STAERKEN - set(prof), set(), stimmung)
        for titel in (*effekte.TITEL.values(), effekte.MULTI, effekte.VICTORY, "PLATZ 2 – VOM BOT", "3 KILLS … UND WEG"):
            self.assertEqual(schema.pruefe(titel, text), [], titel)
        self.assertNotEqual(schema.pruefe("PLATZ 2 😭", text), [])              # DejaVu kann keine Emojis
        self.assertEqual(set(effekte.LOOKS), looks)
        self.assertEqual(set(segment["effekte"]["items"]["properties"]["art"]["enum"]), ERLAUBT)
        self.assertEqual(set(segment["effekte"]["items"]["properties"]["klang"]["enum"]),
                         {"basshit", "tick", "whoosh", "pop", "einschlag", "riser"})

    def test_konfig_ueberschreibt_profil(self):
        k = fx_konfig(schwelle="hoch", an=0, extra={"x": 1},
                      episch={"punch": 1.7, "look": "warm", "look_staerke": 0.4, "max_ruhe_s": 0,
                              "uebergaenge": [["whip", 0.3], ["schnitt", 5]], "blitz": 1.0},
                      lustig={"look": "pink", "uebergaenge": [["boom", 0.3]]}, chill=3)
        e, hinweise = effekte.einstellungen(k)
        self.assertEqual((e["an"], e["schwelle"]), (True, 0.15))            # falscher Typ -> Standard
        prof = e["profile"]["episch"]
        self.assertEqual((prof["punch"], prof["look"], prof["max_ruhe_s"]), (1.0, ("warm", 0.4), None))
        self.assertEqual(prof["uebergaenge"], [("whip", 0.3), ("schnitt", 0.0)])
        self.assertEqual(e["profile"]["lustig"], effekte.PROFIL["lustig"])
        # schwelle, an, extra, episch.blitz, lustig.look, lustig.uebergaenge, chill
        self.assertEqual(len(hinweise), 7, hinweise)
        self.assertTrue(any("regie.effekte.episch.blitz" in h for h in hinweise))
        self.assertEqual(effekte.staerke(0.1, {}, "episch", "punch"), 0.0)   # unter der Schwelle
        self.assertEqual(effekte.staerke(0.8, {"effekt_staerke": {"episch": 1.5}}, "episch", "punch"), 1.0)


class Zeit(unittest.TestCase):
    def test_ohne_lupe(self):
        s = seg(1, "a", 4.0, 10.0, 20.0)
        self.assertEqual(effekte.auf_zeitleiste(s, 4.0), 20.0)
        self.assertAlmostEqual(effekte.auf_zeitleiste(s, 7.5), 23.5)
        self.assertAlmostEqual(effekte.auf_quelle(s, 23.5), 7.5)

    def test_mit_lupe(self):
        rnd = random.Random(1)
        for _ in range(500):
            qs = rnd.uniform(0, 20)
            qe = qs + rnd.uniform(1.0, 10.0)
            ab = rnd.uniform(qs + 0.1, qe - 0.3)
            bis = min(qe - 0.1, ab + rnd.uniform(0.1, 1.5))
            zuschlag = bis - ab  # faktor 0,5: doppelt so lang
            s = {**seg(1, "a", qs, qe, rnd.uniform(0, 60)),
                 "lupe": {"ab_s": ab, "bis_s": bis, "faktor": 0.5, "ton": "tief"}}
            s["zeit_ende"] = s["zeit_start"] + (qe - qs) + zuschlag
            self.assertAlmostEqual(effekte.auf_zeitleiste(s, qs), s["zeit_start"], delta=1e-6)
            self.assertAlmostEqual(effekte.auf_zeitleiste(s, qe), s["zeit_ende"], delta=1e-6)
            punkte = sorted(rnd.uniform(qs, qe) for _ in range(20)) + [ab, bis]
            for t in punkte:
                self.assertAlmostEqual(effekte.auf_quelle(s, effekte.auf_zeitleiste(s, t)), t, delta=1e-6)
            werte = [effekte.auf_zeitleiste(s, t) for t in sorted(punkte)]
            self.assertTrue(all(b > a - 1e-12 for a, b in zip(werte, werte[1:])))   # monoton
            mitte = (ab + bis) / 2
            steigung = (effekte.auf_zeitleiste(s, mitte + 0.01) - effekte.auf_zeitleiste(s, mitte - 0.01)) / 0.02
            self.assertAlmostEqual(steigung, 2.0, delta=1e-6)
            if qe - bis > 0.05:
                t = (bis + qe) / 2
                schritt = effekte.auf_zeitleiste(s, t + 0.01) - effekte.auf_zeitleiste(s, t)
                self.assertAlmostEqual(schritt, 0.01, delta=1e-9)                 # Steigung 1 nach der Lupe


class Impacts(unittest.TestCase):
    """28.09.: Blitz, Wackeln, RGB-Stoß im Spielbild; die Finisher wechseln den Stil – kein Kill sieht aus wie der vorige."""

    def test_finisher_stile_rotieren(self):
        kills = [5.0, 17.0, 29.0, 41.0, 53.0]   # fünf Ketten (> 10 s auseinander) -> fünf Finisher
        liste = plane([seg(1, "m", 0.0, 58.0, 0.0, dauer=60.0)], [moment("m", {"kill_sekunden": kills}, dauer=60.0)])
        je_kill = [tuple(sorted(e["art"] for _, e in ereignisse(liste) if abs(e["t_s"] - k) < 1e-6
                                and e["art"] not in ("sfx", "titel", "zaehler"))) for k in kills]
        stile = {tuple(sorted(s)) for s in effekte.STILE}
        self.assertTrue(all(s in stile for s in je_kill), je_kill)            # jeder Finisher ein ganzer Stil
        self.assertEqual(len(set(je_kill)), 5, je_kill)                       # fünf Kills, fünf verschiedene Stile
        self.assertEqual([e["klang"] for _, e in ereignisse(liste, "sfx")].count("basshit"), 5)   # Bass-Hit bleibt
        # finisher_stil (ohne Seed) filtert nach dem Profil: lustig ohne RGB/Negativ, chill ohne die harten Stile
        lustig = {effekte.finisher_stil(effekte.PROFIL["lustig"], k) for k in range(20)}
        self.assertFalse([s for s in lustig if {"rgb", "negativ"} & set(s)])
        self.assertIn(("punch", "flash"), lustig)
        chill = {effekte.finisher_stil(effekte.PROFIL["chill"], k) for k in range(20)}
        self.assertFalse([s for s in chill if {"flash", "shake", "negativ", "strobe", "tilt"} & set(s)], chill)

    def test_blitz_sicherheit(self):
        # 30.09. (R4): zwölf 0,8-s-Segmente mit Einstieg, Finisher und Tod – ohne die Regel 4 Blitze in einer Sekunde
        segs = [seg(n + 1, f"m{n}", 1.0, 1.8, round(0.8 * n, 3)) for n in range(12)]
        reihe = [moment(f"m{n}", {"kill_sekunden": [1.2], "tod_sekunde": 1.5}) for n in range(12)]
        liste = plane(segs, reihe, beats=[round(0.25 * i, 3) for i in range(1, 40)])   # plane() prüft pruefe_liste
        blitz = sorted(e.t for e in effekte.zeitleiste(liste) if e.art in effekte.BLITZ)
        self.assertGreater(len(blitz), 5)                                                # Blitze bleiben, nur seltener
        self.assertGreaterEqual(min(b - a for a, b in zip(blitz, blitz[1:])), effekte.BLITZ_ABSTAND_S - 1e-3)
        self.assertLessEqual(effekte.blitze(effekte.zeitleiste(liste))[0], effekte.BLITZE_MAX)
        # Fehlerfall: vier Blitze in einer Sekunde meldet der Prüfer
        from tests.effekt_hilfen import mini_liste
        dicht = mini_liste([("/x/a.mp4", 1.0, 5.0, ("schnitt", 0.0))],     # drei Blitze sind noch erlaubt
                           ereignisse=[(1, "flash", t, {"dauer_s": 0.12}) for t in (1.0, 1.25, 1.5)])
        dicht["segmente"][0]["effekte"].append({"art": "flash", "t_s": 2.75, "staerke": 1.0, "dauer_s": 0.12})
        self.assertIn("4 Blitze in 1 s ab 1.00 s", " ".join(regie.pruefe_liste(dicht)))

    def test_tod_wackelt_und_blitzt(self):
        liste = ein_moment({"kill_sekunden": [], "tod_sekunde": 10.0}, stimmung="frustriert")
        beim_tod = [e for _, e in ereignisse(liste) if e["t_s"] == 10.0]
        self.assertEqual(sorted(e["art"] for e in beim_tod if e["art"] != "sfx"), ["flash", "shake"])
        self.assertEqual([e["klang"] for e in beim_tod if e["art"] == "sfx"], ["einschlag"])


class Katalog2(unittest.TestCase):
    """Regisseur 2.2 (28.09., Florian: „das wird langweilig … egal wie lange es rechnet“): Dichte und Katalog – mit den
    echten Profilen (nicht klassisch)."""

    def test_stilfolge_ohne_wiederholung_und_je_video_anders(self):
        pr = effekte.PROFIL["episch"]
        folge = effekte.Stilfolge(effekte.STILE, "a")
        zuege = [folge.naechster(pr) for _ in range(30)]
        self.assertFalse([1 for x, y in zip(zuege, zuege[1:]) if x == y])                  # nie zweimal nacheinander
        self.assertEqual(set(zuege), set(effekte.moegliche_stile(effekte.STILE, pr)))     # alle kommen dran
        erster = effekte.Stilfolge(effekte.STILE, "a").naechster(pr)
        self.assertEqual(erster, zuege[0])                                               # deterministisch
        starts = {effekte.Stilfolge(effekte.STILE, f"video{n}").naechster(pr) for n in range(20)}
        self.assertGreater(len(starts), 4)                                                # je Video anders
        # Stimmungswechsel mitten in der Folge: auch dann kein Doppel
        folge = effekte.Stilfolge(effekte.NEBEN_STILE, "b")
        zuege = [folge.naechster(effekte.PROFIL[st]) for st in ["episch", "chill"] * 10]
        self.assertFalse([1 for x, y in zip(zuege, zuege[1:]) if x == y], zuege)
        self.assertEqual(effekte.Stilfolge(effekte.BEAT_STILE, "c").naechster({}), ())      # nichts geht -> leer

    def test_dichte_einstieg_drift_und_beats(self):
        segs = [seg(1, "a", 0.0, 12.0, 0.0), seg(2, "b", 0.0, 12.0, 12.0),
                seg(3, "c", 2.0, 14.0, 24.0, art="whip", d=0.25), seg(4, "d", 0.0, 12.0, 36.0, stimmung="spannend")]
        reihe = [moment("a", {"kill_sekunden": [6.0]}), moment("b", {"kill_sekunden": [3.0, 5.0, 9.0]}, max_gruppe=3),
                 moment("c", {}), moment("d", {"kill_sekunden": [8.0]}, stimmung="spannend")]
        beats = [round(0.4 * i, 3) for i in range(1, 120)]   # 150 BPM
        liste = plane(segs, reihe, beats=beats)               # plane() prüft die Liste gegen Schema und Regeln
        alle = ereignisse(liste)
        self.assertEqual({e["art"] for _, e in alle} - ERLAUBT, set())
        # Einstieg auf jedem harten Schnitt (Segment 1, 2, 4 – Segment 3 kommt per Whip): ein Effekt auf dem Quellstart
        for nr in (1, 2, 4):
            s = liste["segmente"][nr - 1]
            self.assertTrue([e for n, e in alle if n == nr and e["t_s"] == s["quelle_start_s"]
                             and e["art"] not in ("drift_ein", "drift_aus", "sfx")], nr)
        # Drift über jedes Segment, abwechselnd hinein und heraus, so lang wie das Segment
        drift = [(n, e["art"], e["dauer_s"]) for n, e in alle if e["art"].startswith("drift")]
        self.assertEqual(drift, [(1, "drift_ein", 12.0), (2, "drift_aus", 12.0), (3, "drift_ein", 12.0),
                                 (4, "drift_aus", 12.0)])
        # Beats: viele und nicht nur Zoom-Pulse (Farb-Pop, Vignette, Blur, Kontrast, Farbrad …)
        auf_beats = [e for n, e in alle if any(abs(effekte.auf_zeitleiste(liste["segmente"][n - 1], e["t_s"]) - b) < 1e-3
                                               for b in beats) and e["art"] not in ("sfx", "drift_ein", "drift_aus")]
        self.assertGreater(len({e["art"] for e in auf_beats}), 3, auf_beats)
        self.assertGreater(len([1 for _, e in alle if e["art"] != "sfx"]), 45)

    def test_chill_bleibt_ruhig(self):
        liste = ein_moment({"kill_sekunden": [6.0, 8.0]}, stimmung="chill")
        arten = {e["art"] for _, e in ereignisse(liste)}
        self.assertFalse(arten & {"negativ", "strobe", "shake", "tilt", "rgb", "pixel", "hue", "flash"}, arten)
        self.assertIn("drift_ein", arten)

    def test_gelernte_daempfung_wirkt_auf_den_katalog(self):
        mk = {"kill_sekunden": [5.0, 17.0, 29.0]}
        beats = [round(0.5 * i, 3) for i in range(1, 80)]
        voll = plane([seg(1, "m", 0.0, 38.0, 0.0)], [moment("m", mk)], beats=beats)
        ruhig = plane([seg(1, "m", 0.0, 38.0, 0.0)], [moment("m", mk)], beats=beats, p={"effekt_hektik": 0.3})
        hektisch = lambda l: [e["staerke"] for _, e in ereignisse(l) if e["art"] in effekte.HEKTISCH]   # noqa: E731
        self.assertLess(sum(hektisch(ruhig)), 0.5 * sum(hektisch(voll)))
        aus = plane([seg(1, "m", 0.0, 38.0, 0.0)], [moment("m", mk)], beats=beats,
                    p={"effekt_staerke": {"episch": 0.0}})
        self.assertEqual(ereignisse(aus), [])


class Tempo(unittest.TestCase):
    """28.09.: Zeitlupe um den Finisher, Zeitraffer über den Anlauf (effekte.plane_tempo) – die Zeitleiste (Beats)
    bleibt, nur die Quelle wird gekürzt bzw. verlängert; danach plant effekte.plane wie gehabt, die Liste ist gültig."""

    def setUp(self):
        klassisch(self)

    @staticmethod
    def material():
        segs = [seg(1, "a", 0.0, 20.0, 0.0), seg(2, "e", 2.0, 20.0, 20.0, art="whip", d=0.25)]
        segs[0]["muss"], segs[1]["muss"] = [7.0, 9.5], [9.0, 12.5]  # wie regie._kern: erster Kill − 1 … letzter + 0,5
        reihe = [moment("a", {"kill_sekunden": [8.0, 9.0]}, max_gruppe=2),               # Double -> Faktor 0,5
                 moment("e", {"kill_sekunden": [10.0, 11.0, 12.0]}, max_gruppe=3)]       # Triple, Höhepunkt -> 0,25
        return segs, reihe

    def test_lupe_auf_dem_finisher_raffer_im_anlauf(self):
        segs, reihe = self.material()
        n = effekte.plane_tempo(segs, reihe, dict(regie.PARAMETER), fx_konfig(), "short")
        self.assertEqual(n, {"lupen": 2, "raffer": 2})
        a, e = segs
        self.assertEqual(a["lupe"], {"ab_s": 8.65, "bis_s": 9.45, "faktor": 0.5, "ton": "tief"})
        self.assertEqual(a["raffer"], {"ab_s": 3.2, "bis_s": 7.2, "faktor": 2.0, "ton": "tempo"})   # 4 s vor 8,0 − 0,8
        self.assertEqual(e["lupe"], {"ab_s": 11.8, "bis_s": 12.2, "faktor": 0.25, "ton": "tief"})   # dramatisch
        self.assertEqual(e["raffer"], {"ab_s": 5.2, "bis_s": 9.2, "faktor": 2.0, "ton": "tempo"})
        # Zeitleiste unverändert; Quelle: −0,8 (Lupe) + 2,0 (Raffer) bzw. −1,2 + 2,0
        self.assertEqual((a["zeit_start"], a["zeit_ende"], e["zeit_start"], e["zeit_ende"]), (0.0, 20.0, 20.0, 38.0))
        self.assertEqual((a["quelle_ende_s"], e["quelle_ende_s"]), (21.2, 20.8))
        self.assertAlmostEqual(effekte.auf_zeitleiste(a, 9.0), 9.0 - 2.0 + 0.35)   # Finisher im Video
        liste = plane(segs, reihe)                                                 # plane + pruefe_liste
        self.assertEqual([t for _, t in [(nr, e_["t_s"]) for nr, e_ in ereignisse(liste, "punch")]][:1], [8.0])
        self.assertTrue(all(0 <= z.t <= liste["dauer_s"] for z in effekte.zeitleiste(liste)))

    def test_grenzen_schwelle_und_ohne_platz(self):
        segs, reihe = self.material()
        k = fx_konfig(max_lupen=1, max_raffer=0)
        self.assertEqual(effekte.plane_tempo(segs, reihe, dict(regie.PARAMETER), k, "short"), {"lupen": 1, "raffer": 0})
        self.assertTrue(segs[1].get("lupe") and not segs[0].get("lupe"))           # Vorrang: der Höhepunkt
        self.assertFalse([s for s in segs if s.get("raffer")])
        # „zu viele Effekte“ bis unter die Schwelle: nichts; lustig/chill haben keine Lupe im Profil
        segs, reihe = self.material()
        self.assertEqual(effekte.plane_tempo(segs, reihe, {**regie.PARAMETER, "effekt_staerke": {"episch": 0.1}},
                                             fx_konfig(), "short"), {"lupen": 0, "raffer": 0})
        segs = [seg(1, "l", 0.0, 20.0, 0.0, stimmung="lustig")]   # lustig: keine Lupe, aber ein leichter Raffer
        reihe = [moment("l", {"kill_sekunden": [9.0]}, stimmung="lustig")]
        self.assertEqual(effekte.plane_tempo(segs, reihe, dict(regie.PARAMETER), fx_konfig(), "short"),
                         {"lupen": 0, "raffer": 1})
        self.assertNotIn("lupe", segs[0])
        self.assertEqual(segs[0]["raffer"]["bis_s"], 9.0 - effekte.RAFFER_ABSTAND_S)
        # Muss-Zone bis ans Segmentende: die Quelle darf nicht gekürzt werden -> keine Lupe; kurzer Anlauf -> kein Raffer
        segs = [seg(1, "a", 0.0, 20.0, 0.0)]                                        # muss = [0, 20]
        reihe = [moment("a", {"kill_sekunden": [2.0, 19.0]}, max_gruppe=2)]
        self.assertEqual(effekte.plane_tempo(segs, reihe, dict(regie.PARAMETER), fx_konfig(), "short"),
                         {"lupen": 0, "raffer": 0})
        self.assertEqual(segs[0]["quelle_ende_s"], 20.0)


class SchemaV4(unittest.TestCase):
    def setUp(self):
        klassisch(self)
        self.gut = ein_moment({"kill_sekunden": [6.0, 8.0, 10.0]})

    def pruefe(self, aendern, **kw):
        liste = copy.deepcopy(self.gut)
        aendern(liste)
        return regie.pruefe_liste(liste, **kw)

    def test_v3_von_heute_gueltig(self):
        liste = liste_um([seg(1, "a", 0.0, 10.0, 0.0)])
        self.assertEqual(regie.pruefe_liste(liste), [])
        for feld, wert in (("kill_s", [1.0]), ("rolle", "moment"), ("effekte", []), ("lupe", None)):
            v3 = copy.deepcopy(liste)
            v3["segmente"][0][feld] = wert
            self.assertTrue(regie.pruefe_liste(v3), feld)
        self.assertTrue(regie.pruefe_liste({**liste, "effekte": {"an": False}}))
        self.assertEqual(regie.pruefe_liste({**liste, "version": 4, "effekte": {"an": False}}), [])

    def test_abgewiesen(self):
        erstes = lambda l: l["segmente"][0]["effekte"][0]  # noqa: E731
        dazu = lambda **e: lambda l: l["segmente"][0]["effekte"].append({"t_s": 7.0, "staerke": 1.0, **e})  # noqa: E731
        faelle = {
            "art injiziert": lambda l: erstes(l).update(art="blitz:enable=between(t,0,1)"),
            "blitz": lambda l: erstes(l).update(art="blitz"),
            "wackeln": lambda l: erstes(l).update(art="wackeln"),
            "glitch-Ereignis": lambda l: erstes(l).update(art="glitch"),
            "staerke Text": lambda l: erstes(l).update(staerke="1:x"),
            "staerke riesig": lambda l: erstes(l).update(staerke=1e9),
            "staerke NaN": lambda l: erstes(l).update(staerke=float("nan")),
            "text": lambda l: l["segmente"][0]["effekte"].append({"art": "titel", "t_s": 7.0, "staerke": 1.0,
                                                                  "text": "GODLIKE'"}),
            "unbekanntes Feld": lambda l: erstes(l).update(farbe="rot"),
            "61 Ereignisse": lambda l: l["segmente"][0]["effekte"].extend(
                [{"art": "akzent", "t_s": 1.0, "staerke": 0.5}] * (61 - len(l["segmente"][0]["effekte"]))),
            "t_s außerhalb": lambda l: erstes(l).update(t_s=25.0),
            "uebergang": lambda l: l["segmente"][0]["uebergang"].update(art="whip;x"),
            "uebergang staerke": lambda l: l["segmente"][0]["uebergang"].update(staerke=0.4),  # immer 1 (§4)
            "titel ohne text": dazu(art="titel"),
            "zaehler ohne zahl": dazu(art="zaehler"),
            "sfx ohne klang": dazu(art="sfx"),
            "klang": dazu(art="sfx", klang="../boom"),
            "zahl 0": dazu(art="zaehler", zahl=0),
            "dauer 31": lambda l: erstes(l).update(dauer_s=31.0),
            "kill_s 21": lambda l: l["segmente"][0].update(kill_s=[1.0] * 21),
            "kill_s außerhalb": lambda l: l["segmente"][0].update(kill_s=[21.0]),
            "look": lambda l: l["effekte"].update(look="warm,curves=all='0/1'"),
            "effekte ohne an": lambda l: l["effekte"].pop("an"),
            "effekte Feld": lambda l: l["effekte"].update(filter="x"),
            "rolle": lambda l: l["segmente"][0].update(rolle="intro"),
        }
        for was, aendern in faelle.items():
            self.assertTrue(self.pruefe(aendern), was)
        self.assertIn("mehr als 60 Einträge", " ".join(self.pruefe(faelle["61 Ereignisse"])))

    def test_hoechstens_1500_ereignisse(self):
        segs = [seg(i + 1, f"m{i}", 0.0, 10.0, 10.0 * i) for i in range(26)]
        liste = {**liste_um(segs), "version": 4, "effekte": {"an": True}}
        for s in segs:
            s["effekte"] = [{"art": "akzent", "t_s": 5.0, "staerke": 0.5}] * 60
        self.assertIn("1560 Effekt-Ereignisse", " ".join(regie.pruefe_liste(liste)))
        segs[0]["effekte"] = segs[0]["effekte"][:0]
        self.assertEqual(regie.pruefe_liste(liste), [])
        self.assertIn("mehr als 200 Einträge", " ".join(regie.pruefe_liste(
            {**liste_um([seg(i + 1, "m", 0.0, 1.0, float(i)) for i in range(201)])})))

    def test_lupe(self):
        def mit_lupe(ab, bis, faktor=0.5, laenge=None, feld="lupe", ton="tief"):
            s = seg(1, "a", 2.0, 10.0, 0.0)
            s[feld] = {"ab_s": ab, "bis_s": bis, "faktor": faktor, "ton": ton}
            s["zeit_ende"] = laenge if laenge is not None else round(8.0 + (bis - ab) * (1 / faktor - 1), 3)
            return {**liste_um([s]), "version": 4}
        self.assertEqual(regie.pruefe_liste(mit_lupe(6.0, 7.2)), [])                  # 8 s + 1,2 s Zuschlag
        self.assertEqual(regie.pruefe_liste(mit_lupe(6.6, 7.0, faktor=0.25)), [])     # 28.09.: dramatisch, +1,2 s
        self.assertTrue(regie.pruefe_liste(mit_lupe(6.0, 7.2, laenge=8.0)))           # Länge ohne Zuschlag
        self.assertTrue(regie.pruefe_liste(mit_lupe(6.0, 7.2, faktor=0.3)))           # nicht im enum
        self.assertTrue(regie.pruefe_liste(mit_lupe(7.2, 6.0)))                       # ab ≥ bis
        self.assertTrue(regie.pruefe_liste(mit_lupe(9.5, 9.95)))                      # zu nah am Ende
        self.assertTrue(regie.pruefe_liste(mit_lupe(4.0, 6.0)))                       # länger als 1,5 s
        # Zeitraffer (28.09.): eigenes Feld, Faktor 1,5/2, bis 4 s, Zeitleiste kürzer
        self.assertEqual(regie.pruefe_liste(mit_lupe(2.5, 6.5, faktor=2.0, feld="raffer", ton="tempo")), [])
        self.assertTrue(regie.pruefe_liste(mit_lupe(2.5, 7.0, faktor=2.0, feld="raffer", ton="tempo")))   # > 4 s
        self.assertTrue(regie.pruefe_liste(mit_lupe(2.5, 6.5, faktor=0.5, feld="raffer", ton="tempo")))   # kein Raffer-Faktor
        beides = mit_lupe(6.0, 7.0)
        beides["segmente"][0]["raffer"] = {"ab_s": 2.5, "bis_s": 5.5, "faktor": 2.0, "ton": "tempo"}
        beides["segmente"][0]["zeit_ende"] = beides["dauer_s"] = round(8.0 + 1.0 - 1.5, 3)
        self.assertEqual(regie.pruefe_liste(beides), [])
        beides["segmente"][0]["raffer"]["bis_s"] = 6.5                                # Raffer ragt in die Lupe
        self.assertIn("vor der Zeitlupe", " ".join(regie.pruefe_liste(beides)))
        zwei = mit_lupe(6.0, 7.0)
        s2 = {**copy.deepcopy(zwei["segmente"][0]), "nr": 2, "moment": "b"}
        s2["zeit_start"], s2["zeit_ende"] = zwei["segmente"][0]["zeit_ende"], zwei["segmente"][0]["zeit_ende"] + 9.0
        zwei["segmente"].append(s2)
        zwei["dauer_s"] = s2["zeit_ende"]
        self.assertIn("2 Zeitlupen", " ".join(regie.pruefe_liste(zwei, max_lupen=1)))
        self.assertEqual(regie.pruefe_liste(zwei), [])                                # Standard: 2 (28.09.)

    def test_hook(self):
        def mit_hook(pos=0, laenge=1.5, moment_="e", kill_s=(6.0,), zwei=False):
            segs = [seg(1, "a", 0.0, 10.0, 0.0), seg(2, "e", 0.0, 10.0, 10.0)]
            hook = {**seg(0, moment_, 5.0, 5.0 + laenge, 0.0), "rolle": "hook", "kill_s": list(kill_s)}
            segs.insert(pos, hook)
            if zwei:
                segs.insert(0, copy.deepcopy(hook))
            t = 0.0
            for nr, s in enumerate(segs, 1):
                d = s["zeit_ende"] - s["zeit_start"]
                s.update(nr=nr, zeit_start=round(t, 3), zeit_ende=round(t + d, 3))
                t += d
            return {**liste_um(segs), "version": 4}
        self.assertEqual(regie.pruefe_liste(mit_hook()), [])
        self.assertTrue(regie.pruefe_liste(mit_hook(pos=1)))            # an Position 2
        self.assertTrue(regie.pruefe_liste(mit_hook(laenge=3.0)))       # > 2,5 s
        self.assertTrue(regie.pruefe_liste(mit_hook(moment_="a")))      # nicht der Höhepunkt
        self.assertTrue(regie.pruefe_liste(mit_hook(kill_s=())))        # ohne Kill
        self.assertTrue(regie.pruefe_liste(mit_hook(zwei=True)))        # zwei Hooks

    def test_final_auftrag_mit_boesen_effekten(self):
        # Auf pve-big ist der Auftrag fremde Eingabe: die neuen Felder werden vor dem Rendern abgewiesen
        erstes = lambda l: l["segmente"][0]["effekte"][0]  # noqa: E731
        faelle = {
            "art": lambda l: erstes(l).update(art="punch,drawtext=textfile=/etc/passwd"),
            "staerke": lambda l: erstes(l).update(staerke="0.5:enable=1"),
            "text": lambda l: l["segmente"][0]["effekte"].append({"art": "titel", "t_s": 7.0, "staerke": 1.0,
                                                                  "text": "x':textfile=/etc/passwd"}),
            "klang": lambda l: l["segmente"][0]["effekte"].append({"art": "sfx", "t_s": 7.0, "staerke": 1.0,
                                                                   "klang": "/etc/passwd"}),
            "look": lambda l: l["effekte"].update(look="kalt;[x]"),
            "uebergang": lambda l: l["segmente"][0]["uebergang"].update(art="zoom:duration=99"),
            "uebergang staerke": lambda l: l["segmente"][0]["uebergang"].update(staerke="1:enable=1"),
            "lupe": lambda l: l["segmente"][0].update(lupe={"ab_s": 1.0, "bis_s": 2.0, "faktor": "0.5,x",
                                                            "ton": "tief"}),
        }
        with tempfile.TemporaryDirectory() as tmp:
            k = konfig.lade()
            k.daten["speicher"]["wurzel"] = tmp
            auftrag = Path(tmp) / "gut.json"
            for was, aendern in faelle.items():
                liste = {**copy.deepcopy(self.gut), "name": "gut"}
                aendern(liste)
                auftrag.write_text(json.dumps(liste))
                with self.assertRaises(MedienFehler, msg=was):
                    entwurf.fuehre_final_aus(k, auftrag)

    def test_max_items_im_pruefer(self):
        self.assertEqual(schema.pruefe([1, 2, 3], {"type": "array", "maxItems": 2}), ["$: mehr als 2 Einträge"])
        self.assertEqual(schema.pruefe([1, 2], {"type": "array", "maxItems": 2}), [])
        self.assertTrue(schema.pruefe(float("inf"), {"type": "number"}))


class Compose(MitRegieMaterial):
    """regie.erstelle mit Effekten: version 4, gültig, deterministisch; aus = kein Plan.
    Ohne ffmpeg: compose prüft nur, ob die Moment-Datei da ist, und liest die Beats aus der Tabelle."""

    def setUp(self):
        super().setUp()
        self.konfig.daten["regie"]["effekte"]["an"] = True
        self.konfig.daten["regie"]["vorgaben"] = {"abwechslung": 0}  # zweimal dieselben Momente
        for i, (stimmung, serie, kills, match) in enumerate(MOMENTE, 1):
            datei = self.tmp / "momente" / f"{i:02d}.mp4"
            datei.parent.mkdir(parents=True, exist_ok=True)
            datei.touch()
            mk = {"kills": len(kills), "max_gruppe": serie, "kill_sekunden": kills, "spitzen": len(kills),
                  "jubel_laut": 0, "tod_sekunde": 4.0 if stimmung == "frustriert" else None,
                  "victory_royale": int(i == 1), "jubel_laut_s": [10.0] if stimmung == "lustig" else []}
            if i == 3:  # spannend, Kills 7,0 / 9,5: umgehauen bei 3,0 und 8,0
                mk["aktion_sekunden"] = [3.0, 8.0]
            self.con.execute(
                """INSERT INTO momente (schluessel, match_id, datei, start_s, ende_s, kills, stimmung, sicherheit,
                                        quelle, merkmale, erstellt, geaendert)
                   VALUES (?, ?, ?, 0, 20, ?, ?, 0.8, 'regel', ?, 'x', 'x')""",
                (f"datei:{i}", match, str(datei), len(kills), stimmung, json.dumps(mk)))
        beats = [round(0.4 * i, 3) for i in range(1, 825)]  # 150 BPM, 330 s
        self.con.execute(
            """INSERT INTO tracks (datei, titel, kuenstler, quelle, sha256, dauer_s, bpm, energie, beats, verlauf,
                                   stimmungen, erstellt) VALUES ('klick.mp3', 'Klick', 'Test', 'CC0', 'x', 330, 150,
                                   0.8, ?, ?, '["episch"]', 'x')""",
            (json.dumps(beats), json.dumps([0.3] * 40 + [0.9] * 290)))

    def compose(self, fmt="short"):
        from clip_pipeline import regie_lernen

        p, ziel = regie_lernen.aktuelle(self.con, self.konfig)
        e = regie.erstelle(self.con, self.konfig, fmt, parameter=p, ziel=ziel)
        return json.loads(Path(e["datei"]).read_text(encoding="utf-8")), e

    def test_short_deterministisch_und_gueltig(self):
        eins, e = self.compose()
        zwei, _ = self.compose()
        self.assertEqual(regie.pruefe_liste(eins), [])
        self.assertEqual((eins["version"], eins["effekte"]["an"], eins["effekte"]["look"]),
                         (4, True, effekte.LOOK_JE_STIMMUNG[eins["stimmung"]]))
        self.assertEqual([s.get("effekte") for s in eins["segmente"]], [s.get("effekte") for s in zwei["segmente"]])
        self.assertEqual([s["uebergang"] for s in eins["segmente"]], [s["uebergang"] for s in zwei["segmente"]])
        alle = [x for s in eins["segmente"] for x in s.get("effekte", [])]
        self.assertTrue(alle)
        self.assertEqual({x["art"] for x in alle} - ERLAUBT, set())
        self.assertEqual(e["momente"], len({s["moment"] for s in eins["segmente"]}))
        self.assertEqual(eins["auswahl"]["neu"] + eins["auswahl"]["schon_gezeigt"], e["momente"])
        zeit = effekte.zeitleiste(eins)
        self.assertEqual(len(zeit), len(alle))
        self.assertTrue(all(0 <= z.t <= eins["dauer_s"] for z in zeit))
        # kein Titel/Zähler während einer Zoom-Blende (dort wüchse das Spielbild unter den Text)
        gross = [(a, b) for art, a, b, _ in effekte.uebergangs_fenster(eins) if art in effekte.VERGROESSERND]
        for z in zeit:
            if z.art in ("titel", "zaehler"):
                self.assertFalse(any(z.t < b and z.t + z.dauer > a + 1e-6 for a, b in gross), z)

    def test_zusammenschnitt_ohne_zaehler(self):
        liste, _ = self.compose("zusammenschnitt")
        self.assertEqual(regie.pruefe_liste(liste), [])
        self.assertEqual(ereignisse(liste, "zaehler"), [])
        fenster = [(a, b) for _, a, b, _ in effekte.uebergangs_fenster(liste)]
        for z in effekte.zeitleiste(liste):
            if z.art == "titel":  # nur während einer Blende
                self.assertTrue(any(abs(z.t - a) < 2e-3 and abs(z.t + z.dauer - b) < 2e-3 for a, b in fenster), z)

    def test_effekt_staerke_null_ohne_ereignisse_dieser_stimmung(self):
        # Stufe 2: deine Vorgabe (oder Gelerntes) 0 für episch -> episch-Segmente ganz ohne Effekt
        self.konfig.daten["regie"]["vorgaben"] = {"abwechslung": 0, "effekt_staerke": {"episch": 0}}
        liste, _ = self.compose()
        self.assertEqual(regie.pruefe_liste(liste), [])
        episch = [s for s in liste["segmente"] if s["stimmung"] == "episch"]
        self.assertTrue(episch)
        self.assertEqual([s.get("effekte") for s in episch], [None] * len(episch))
        self.assertTrue([s for s in liste["segmente"] if s["stimmung"] != "episch" and s.get("effekte")])
        self.assertEqual(liste["parameter"]["effekt_staerke"], {"episch": 0.0})
        if liste["stimmung"] == "episch":
            self.assertEqual((liste["effekte"]["look"], liste["effekte"]["look_staerke"]), ("neutral", 0.0))

    def test_aus_ohne_plan_und_hinweis_aus_der_konfig(self):
        self.konfig.daten["regie"]["effekte"] = {"an": False, "episch": {"blitz": 1.0}}
        liste, _ = self.compose()
        self.assertEqual((liste["version"], liste["effekte"]), (4, {"an": False}))
        self.assertFalse([s for s in liste["segmente"] if "effekte" in s or "kill_s" in s])
        self.assertTrue(all(s["uebergang"]["art"] in ("schnitt", regie.UEBERGANG[s["stimmung"]][0])
                            for s in liste["segmente"]))
        self.assertIn("regie.effekte.episch.blitz ignoriert (unbekannt oder ungültiger Wert)", liste["hinweise"])


if __name__ == "__main__":
    unittest.main()
