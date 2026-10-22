"""Nachtschicht (22.10., Florian: „ML soll den Schnitt autonom verbessern … Voten nur noch optional“): Fassungen im
Wettbewerb – gebaut, gemessen, KI-Paarvergleich, die Siegerin kommt frei, der Rest bleibt markiert; die Vergleiche
lehren geschmack (nur Unterschiede) und massstab (als KI-Paare). Planen, Rendern, Messen und Claude sind gefälscht –
geprüft wird die Steuerung."""

from __future__ import annotations

import json
import random
from datetime import timedelta
from pathlib import Path
from unittest import mock

from clip_pipeline import entwurf, geschmack, kritik, kriterien, massstab, nachtschicht, regie
from clip_pipeline.zeit import iso, jetzt

from tests.hilfen import MitSpeicher

WAHL = {"aufbau": "montage", "tempo": "schnell", "zeitlupe": "viel", "experiment": None}
BASIS_P = {**regie.PARAMETER, "stil": "montage", "seg_min_faktor": 0.75, "max_lupen": 8, "ziel_dauer_s": 65.0,
           "laenge_richtung": "kurz", "laenge_grenze_s": 65.0, "geschmack": WAHL, "rahmen_zoom": 1.22,
           "reihenfolge": "bogen", "autonom": {"version": 3, "exploration": {"variable": "musik_pegel"}}}


def liste(name: str, p: dict, dauer: float = 45.0) -> dict:
    return {"name": name, "format": "short", "dauer_s": dauer, "stimmung": "episch", "bogen": [0.5, 1.0],
            "variante": None, "musik": None, "parameter": p, "hinweise": [], "fps": 30,
            "auswahl": {"neu": 4, "schon_gezeigt": 0, "kandidaten": 4, "wiederholt": [], "nachschub": []},
            "segmente": [{"moment": m, "zeit_start": 10.0 * i, "zeit_ende": 10.0 * i + 9.0, "teil": 1, "stimmung": "episch"}
                         for i, m in enumerate(("a", "b", "c", "d"))]}


class Varianten(MitSpeicher):
    def test_jede_art_aendert_genau_ihre_schraube(self):
        arten = dict(nachtschicht.varianten(BASIS_P, 6, 12, self.konfig, "short", track_id=7))
        self.assertEqual(set(arten), set(nachtschicht.ARTEN))
        q = arten["aufbau"]
        self.assertNotEqual(q["stil"], "montage")
        self.assertEqual((q["geschmack"]["aufbau"], q["geschmack"]["experiment"]), (q["stil"], None))
        self.assertEqual(arten["tempo"]["geschmack"]["tempo"], "ruhig")
        self.assertAlmostEqual(arten["tempo"]["seg_min_faktor"], round(0.75 * 1.25 / 0.8, 3))
        self.assertEqual((arten["zeitlupe"]["max_lupen"], arten["zeitlupe"]["geschmack"]["zeitlupe"]), (2, "wenig"))
        self.assertEqual(arten["laenge"]["ziel_dauer_s"], 75.0)     # „mindestens 65 s“: nie darunter, also länger
        self.assertEqual((arten["anlauf"]["puffer_vor_s"], arten["anlauf"]["puffer_nach_s"]), (3.75, 2.25))
        self.assertEqual(arten["song"]["track_malus"], {"7": nachtschicht.SONG_MALUS})
        for art, q in arten.items():
            self.assertEqual(q["nachtschicht"], {"art": art, "basis": 12})
            self.assertIsNone(q["autonom"]["exploration"])        # der Publikums-Versuch gilt nur der ersten Fassung
        # „höchstens 55 s“ bei Ziel 55: nur kürzer; ohne Song keine Song-Fassung; wenig Platz: weniger Fassungen
        p = {**BASIS_P, "ziel_dauer_s": 55.0, "laenge_richtung": "lang", "laenge_grenze_s": 55.0}
        kurz = dict(nachtschicht.varianten(p, 6, 12, self.konfig, "short", track_id=None))
        self.assertEqual(kurz["laenge"]["ziel_dauer_s"], 45.0)
        self.assertNotIn("song", kurz)
        self.assertEqual(len(nachtschicht.varianten(BASIS_P, 2, 0, self.konfig, "short", track_id=7)), 2)


class Wettbewerb(MitSpeicher):
    def setUp(self):
        super().setUp()
        self.konfig.daten["regie"]["geschmack"] = True                 # einfacher Modus
        self.konfig.daten["regie"]["ordner"] = str(self.tmp / "regie")
        self.konfig.daten["nachtschicht"] = {"an": True, "fassungen": 4, "ki": True}
        for i in range(5):   # die erste Fassung bekommt Nummer 6: die Arten beginnen dann bei „aufbau“ (ARTEN ab Nummer)
            self.con.execute("INSERT INTO entwuerfe (name, format, schnittliste, parameter, erstellt) "
                             "VALUES (?, 'short', 'x', '{}', ?)", (f"alt{i}", iso(jetzt())))
        self.basis = regie._speichere(self.con, self.konfig, liste("basis", BASIS_P))["entwurf"]
        self.assertEqual(self.basis % len(nachtschicht.ARTEN), 0)
        nachtschicht.markiere(self.con, self.basis)
        self.gerendert(self.basis)

    def name(self, eid: int) -> str:
        return self.con.execute("SELECT name FROM entwuerfe WHERE id = ?", (eid,)).fetchone()["name"]

    def marke(self, eid: int) -> str | None:
        return self.con.execute("SELECT auto_verworfen FROM entwuerfe WHERE id = ?", (eid,)).fetchone()[0]

    def gerendert(self, eid: int) -> None:
        z = self.con.execute("SELECT schnittliste FROM entwuerfe WHERE id = ?", (eid,)).fetchone()
        datei = Path(z["schnittliste"]).with_suffix(".mp4")
        datei.write_bytes(b"video")
        self.con.execute("UPDATE entwuerfe SET datei = ?, status = 'gerendert' WHERE id = ?", (str(datei), eid))

    def test_siegerin_frei_rest_markiert_lernt(self):
        scores = {"basis": 55.0, "f-aufbau": 70.0, "f-tempo": 60.0}
        gebaut: list[str] = []

        def erstelle(con, konfig, fmt, *, parameter, ziel=None, nur_matches=None, **_):
            art = parameter["nachtschicht"]["art"]
            gebaut.append(art)
            if art == "zeitlupe":
                raise regie.RegieFehler("keine Zeitlupe möglich")       # fällt aus, der Wettbewerb läuft weiter
            return regie._speichere(con, konfig, liste(f"f-{art}", parameter, dauer=50.0 + len(gebaut)))

        def rendern(con, konfig, eid):
            self.gerendert(eid)
            return {"entwurf": eid}

        def bewerte(con, konfig, eid, **_):
            return {"regel_score": scores[self.name(eid)], "tor": None}

        def paarvergleich(con, konfig, a, b, ordner):
            # die beiden besten nach Messung (aufbau 70, tempo 60) – die KI nimmt die nach Messung schlechtere
            self.assertEqual({self.name(a), self.name(b)}, {"f-aufbau", "f-tempo"})
            return ({"sieger": "a" if self.name(a) == "f-tempo" else "b", "sicherheit": 0.8, "gruende": ["langweilig"],
                     "begruendung": "Das Tempo trägt bis zum Payoff bei 0:48."}, None)

        with mock.patch.object(regie, "erstelle", side_effect=erstelle), \
                mock.patch.object(entwurf, "entwurf", side_effect=rendern), \
                mock.patch.object(kritik, "bewerte", side_effect=bewerte), \
                mock.patch.object(kritik, "ki_paarvergleich", side_effect=paarvergleich), \
                mock.patch.object(massstab, "nachziehen", return_value=None) as lernen:
            w = nachtschicht.wettbewerb(self.con, self.konfig, "short", self.basis, nur_matches={"m1"})
        self.assertEqual(gebaut, ["aufbau", "tempo", "zeitlupe"])          # ARTEN ab basis_id, eine fiel aus
        self.assertEqual(self.name(w["video"]), "f-tempo")
        self.assertEqual((w["fassungen"], w["ki"], w["art"]), (3, True, "tempo"))
        self.assertIsNone(self.marke(w["video"]))                           # die Siegerin darf zu dir
        self.assertEqual(self.marke(self.basis), nachtschicht.FASSUNG)      # die erste Fassung hat verloren
        for v in w["verlierer"]:
            self.assertEqual(self.marke(v), nachtschicht.FASSUNG)
        ki = self.con.execute("SELECT sieger, verlierer, sicherheit, text FROM wettbewerbe WHERE quelle = 'ki'").fetchall()
        self.assertEqual(len(ki), 1)
        self.assertEqual((self.name(ki[0]["sieger"]), self.name(ki[0]["verlierer"]), ki[0]["sicherheit"]),
                         ("f-tempo", "f-aufbau", 0.8))
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM wettbewerbe WHERE quelle = 'messung'").fetchone()[0], 2)
        z = self.con.execute("SELECT schnittliste FROM entwuerfe WHERE id = ?", (w["video"],)).fetchone()
        info = json.loads(Path(z["schnittliste"]).read_text(encoding="utf-8"))["wettbewerb"]
        self.assertEqual(info, {"fassungen": 3, "ki": True, "art": "tempo", "grund": "Das Tempo trägt bis zum Payoff bei 0:48."})
        lernen.assert_called_once()
        # geschmack lernt nur die Unterschiede: Tempo und Aufbau (die Siegerin „ruhig/Montage“ gegen die Fassung mit
        # anderem Aufbau), nicht die gleiche Zeitlupe
        stat = geschmack.statistik(self.con)
        g = geschmack.KI_GEWICHT * 0.8
        self.assertAlmostEqual(stat["tempo"]["ruhig"]["n"], g)
        self.assertAlmostEqual(stat["tempo"]["ruhig"]["s"], g)
        self.assertAlmostEqual(stat["tempo"]["schnell"]["n"], g)
        self.assertEqual(stat["tempo"]["schnell"]["s"], 0.0)
        self.assertAlmostEqual(stat["aufbau"]["montage"]["s"], g)
        self.assertEqual(len([o for o, w_ in stat["aufbau"].items() if w_["n"] and o != "montage"]), 1)
        self.assertEqual(sum(w_["n"] for w_ in stat["zeitlupe"].values()), 0.0)
        zeile = geschmack.wettbewerb_zeile(self.con, "2026-01-01", "2999-01-01")
        self.assertTrue(zeile.startswith("🥊 1 Video aus je 3 Fassungen · 1 KI-Vergleich · der KI-Cutter bevorzugte: "), zeile)

    def test_ohne_ki_entscheidet_die_messung_und_unsicher_lehrt_nicht(self):
        dauern = iter((50.0, 52.0, 54.0))   # verschiedene Videos – gleiche würden als „gleich“ übersprungen

        def erstelle(con, konfig, fmt, *, parameter, **_):
            return regie._speichere(con, konfig, liste(f"f-{parameter['nachtschicht']['art']}", parameter, next(dauern)))

        def bewerte(con, konfig, eid, **_):
            return {"regel_score": 80.0 if self.name(eid) == "basis" else 40.0, "tor": None}

        with mock.patch.object(regie, "erstelle", side_effect=erstelle), \
                mock.patch.object(entwurf, "entwurf", side_effect=lambda con, k, eid: self.gerendert(eid)), \
                mock.patch.object(kritik, "bewerte", side_effect=bewerte), \
                mock.patch.object(kritik, "ki_paarvergleich", return_value=({"sieger": "b", "sicherheit": 0.5,
                                                                             "gruende": [], "begruendung": ""}, None)), \
                mock.patch.object(massstab, "nachziehen", return_value=None):
            w = nachtschicht.wettbewerb(self.con, self.konfig, "short", self.basis, nur_matches=None)
        self.assertEqual((w["video"], w["ki"], w["fassungen"]), (self.basis, False, 4))
        self.assertIsNone(self.marke(self.basis))
        self.assertEqual(geschmack.wettbewerb_paare(self.con), [])        # Münzwurf lehrt nicht

    def test_aufraeumen_gibt_verwaiste_erste_fassung_frei(self):
        alt = iso(jetzt() - timedelta(hours=2))
        self.con.execute("UPDATE entwuerfe SET erstellt = ? WHERE id = ?", (alt, self.basis))
        frisch = regie._speichere(self.con, self.konfig, liste("frisch", BASIS_P))["entwurf"]
        nachtschicht.markiere(self.con, frisch)
        self.assertEqual(nachtschicht.aufraeumen(self.con), [self.basis])
        self.assertIsNone(self.marke(self.basis))
        self.assertEqual(self.marke(frisch), nachtschicht.BASIS)

    def test_paarvergleich_tageslimit_und_aus(self):
        self.con.execute("INSERT INTO wettbewerbe (video, sieger, verlierer, quelle, sicherheit, erstellt) "
                         "VALUES (?, ?, ?, 'ki', 0.9, ?)", (self.basis, self.basis, self.basis, iso(jetzt())))
        self.konfig.daten["nachtschicht"]["ki_pro_tag"] = 1
        urteil, hinweis = kritik.ki_paarvergleich(self.con, self.konfig, self.basis, self.basis, self.tmp / "w")
        self.assertIsNone(urteil)
        self.assertIn("Tageslimit", hinweis)
        self.konfig.daten["nachtschicht"]["ki"] = False
        self.assertEqual(kritik.ki_paarvergleich(self.con, self.konfig, self.basis, self.basis, self.tmp / "w"),
                         (None, None))


class MassstabPaare(MitSpeicher):
    def test_ki_vergleich_wird_ki_paar_messung_nie(self):
        zufall = random.Random(3)
        start = jetzt() - timedelta(days=2)
        ids = []
        for i in range(2):
            teile = {k: {"wert": round(zufall.random(), 3), "quelle": "video"} for k in kriterien.START["short"]}
            eid = self.con.execute("INSERT INTO entwuerfe (name, format, schnittliste, parameter, erstellt) "
                                   "VALUES (?, 'short', 'x', '{}', ?)", (f"e{i}", iso(start))).lastrowid
            self.con.execute("INSERT INTO kritiken (entwurf_id, score, regel_score, details, erstellt, teile, "
                             "mess_version) VALUES (?, 50, 50, '{}', ?, ?, 1)", (eid, iso(start), json.dumps(teile)))
            ids.append(eid)
        self.con.execute("INSERT INTO wettbewerbe (video, sieger, verlierer, quelle, sicherheit, erstellt) VALUES "
                         "(?, ?, ?, 'messung', NULL, ?)", (ids[0], ids[0], ids[1], iso(start)))
        self.assertEqual([p for p in massstab.paare(self.con, self.konfig) if p.art == "ki"], [])
        self.con.execute("INSERT INTO wettbewerbe (video, sieger, verlierer, quelle, sicherheit, erstellt) VALUES "
                         "(?, ?, ?, 'ki', 0.8, ?)", (ids[0], ids[1], ids[0], iso(start)))
        ki = [p for p in massstab.paare(self.con, self.konfig) if p.art == "ki"]
        self.assertEqual([p.ids for p in ki], [(ids[1], ids[0])])
        self.assertTrue(ki[0].maske)                                       # blind: tonmix/beat_sync bleiben maskiert
