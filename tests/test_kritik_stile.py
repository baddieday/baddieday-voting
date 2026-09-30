"""Regisseur 3.0 (30.09.): Schnittstile, Cutter-Kritik und Selbstlernen ohne 👍/👎."""

import json
import unittest
from pathlib import Path
from unittest import mock

from clip_pipeline import effekt_filter, entwurf, kritik, regie, regie_lernen, stile
from clip_pipeline.claude_aufruf import ClaudeAntwort
from clip_pipeline.zeit import iso, jetzt
from tests.hilfen import HAT_FFMPEG, MitSpeicher, testvideo
from tests.regie_hilfen import MOMENTE, MitRegieMaterial


def liste(kill_s=(3.0,), rahmen=1.0, dauer=40.0):
    """Kleine Short-Schnittliste: 4 Segmente à 10 s, Kills in Segment 1 (Quellzeit = Zeitleiste)."""
    segs = [{"nr": i + 1, "moment": f"m{i}", "zeit_start": 10.0 * i, "zeit_ende": 10.0 * (i + 1), "quelle_start_s": 10.0 * i,
             "quelle_ende_s": 10.0 * (i + 1), "auf_beat": True, "stimmung": "episch",
             "kill_s": [k for k in kill_s if 10 * i <= k <= 10 * (i + 1)],
             "effekte": [{"art": "punch", "t_s": 10.0 * i + 1}] * 2} for i in range(4)]
    return {"format": "short", "dauer_s": dauer, "bogen": [1, 2, 3, 4], "segmente": segs,
            "parameter": {"rahmen_zoom": rahmen, "stil": "montage"}, "stimmung": "episch", "musik": None}


class Stile(MitSpeicher):
    def entwurf(self, stil, score=None):
        n = self.con.execute("SELECT COUNT(*) FROM entwuerfe").fetchone()[0]
        eid = self.con.execute("INSERT INTO entwuerfe (name, format, schnittliste, parameter, erstellt) "
                               "VALUES (?, 'short', 'x', ?, 'x')", (f"e{n}", json.dumps({"stil": stil}))).lastrowid
        if score is not None:
            self.con.execute("INSERT INTO kritiken (entwurf_id, score, regel_score, details, erstellt) "
                             "VALUES (?, ?, ?, '{}', 'x')", (eid, score, score))
        return eid

    def test_fester_stil_wirkt_relativ(self):
        self.konfig.daten["regie"]["stil"] = "kino"
        p = stile.anwenden(self.con, self.konfig, "short", {**regie.PARAMETER, "seg_min_faktor": 1.1})
        self.assertEqual((p["stil"], p["rahmen_zoom"], p["reihenfolge"]), ("kino", 1.3, "bogen"))
        self.assertEqual(p["seg_min_faktor"], 1.32)                   # 1,1 gelernt × 1,2 Kino – Gelerntes bleibt
        self.assertIs(stile.anwenden(self.con, self.konfig, "zusammenschnitt", {"x": 1})["x"], 1)  # nur Short

    def test_lernt_selbst_aus_den_noten(self):
        self.konfig.daten["regie"]["stil"] = "auto"                    # Testbasis legt „klassik“ fest
        for stil in stile.STILE:
            for _ in range(6):
                self.entwurf(stil, 92 if stil == "story" else 25)
        gezogen = []
        for _ in range(12):
            s = stile.waehle(self.con, self.konfig, "short")
            gezogen.append(s)
            self.entwurf(s)                                             # ohne Note: nur die Reihenfolge zählt
        self.assertGreaterEqual(gezogen.count("story"), 6)               # gute Noten ziehen
        self.assertFalse(any(a == b == c for a, b, c in zip(gezogen, gezogen[1:], gezogen[2:])))  # nie 3× hintereinander
        self.assertIn("📖 Story 92 (6×)", stile.stil_zeile(self.con))

    def test_reihenfolgen(self):
        k = lambda n, st, m, t: regie.Kandidat(n, "x", 20, "episch", st, st, None, m, (t, t + 5), (t, t + 5), "")  # noqa: E731
        ks = [k("a", 5, "m2", 1), k("b", 1, "m1", 9), k("c", 9, "m1", 2), k("d", 3, "m1", 5)]
        self.assertEqual([x.schluessel for x in regie.bogen(ks, "short", reihenfolge="steigend")], ["b", "d", "a", "c"])
        self.assertEqual([x.schluessel for x in regie.bogen(ks, "short", reihenfolge="chronologisch")], ["d", "b", "a", "c"])
        # Hook (Stil, Experiment, Publikum) wirkt in jeder Reihenfolge: Höhepunkt zuerst (Review 30.09.)
        self.assertEqual([x.schluessel for x in regie.bogen(ks, "short", reihenfolge="steigend", hook_staerkster=True)],
                         ["c", "b", "d", "a"])
        ks[0].start_utc, ks[1].start_utc, ks[3].start_utc = "2026-09-29T20:00:00Z", "2026-09-29T21:00:00Z", "2026-09-29T22:00:00Z"
        self.assertEqual([x.schluessel for x in regie.bogen(ks, "short", reihenfolge="chronologisch")], ["a", "b", "d", "c"])


class Kritik(MitSpeicher):
    def test_handwerksregeln(self):
        gut = kritik.regeln(liste(kill_s=(1.0, 12.0, 22.0, 32.0, 38.0), rahmen=1.45), (30, 75))
        schwach = kritik.regeln(liste(kill_s=(35.0,), rahmen=1.0), (30, 75))
        self.assertEqual((gut["teile"]["einstieg"], gut["teile"]["bild"]), (1.0, 1.0))
        self.assertEqual((schwach["teile"]["einstieg"], schwach["teile"]["bild"]), (0.2, 0.4))
        self.assertGreater(gut["score"], schwach["score"] + 20)
        aus = liste(kill_s=())                                          # Effekte aus (⚙️): kein kill_s, nur muss
        aus["effekte"] = {"an": False}
        for s in aus["segmente"]:
            s["muss"], s["effekte"] = [s["quelle_start_s"] + 1, s["quelle_start_s"] + 6], []
        r = kritik.regeln(aus, (30, 75))
        self.assertEqual((r["teile"]["einstieg"], r["teile"]["action"], r["teile"]["effekte"]), (1.0, 1.0, 1.0))

    def anlegen(self):
        ordner = self.tmp / "regie"
        ordner.mkdir()
        (ordner / "e.json").write_text(json.dumps(liste()), encoding="utf-8")
        video = testvideo(ordner / "e.mp4", dauer=4.0) if HAT_FFMPEG else ordner / "fehlt.mp4"
        return self.con.execute("INSERT INTO entwuerfe (name, format, schnittliste, parameter, datei, erstellt) "
                                "VALUES ('e', 'short', ?, '{}', ?, ?)",
                                (str(ordner / "e.json"), str(video), iso(jetzt()))).lastrowid

    @unittest.skipUnless(HAT_FFMPEG, "ffmpeg fehlt")
    def test_ki_cutter_urteil_wirkt_aufs_lernen(self):
        eid = self.anlegen()
        self.konfig.daten["regie"]["kritik"]["ki"] = True
        antwort = ClaudeAntwort({"score": 80, "staerken": ["starker Einstieg"], "schwaechen": ["Mitte zieht sich"],
                                 "gruende": ["hektisch", "hektisch", "unsinn"]}, None, "{}")
        with mock.patch.object(kritik.claude_aufruf, "frage_json", return_value=antwort) as frage:
            e = kritik.bewerte(self.con, self.konfig, eid)
        self.assertTrue((Path(frage.call_args.args[2]) / "kontaktbogen.jpg").is_file())
        self.assertEqual((e["ki_score"], e["gruende"]), (80.0, ["hektisch"]))
        self.assertEqual(e["score"], round((e["regel_score"] + 80) / 2, 1))
        ki = [z for z in regie_lernen.bewertungen(self.con) if z["quelle"] == "ki"]
        self.assertEqual([(z["daumen"], json.loads(z["gruende"])) for z in ki], [(1, ["hektisch"])])
        p, _ = regie_lernen.aktuelle(self.con, self.konfig)          # ohne Format: kein Stil, nur das Gelernte
        self.assertEqual(p["seg_min_faktor"], 1.15)                    # „zu hektisch“ vom KI-Cutter wirkt
        self.assertIn("🧐 Cutter-Score", kritik.kritik_zeile(self.con, eid))
        self.assertIsNone(regie_lernen.wirkung(self.con, self.konfig, "short"))   # „🧠 Aus #n“ nur aus deinen Bewertungen

    def test_ohne_ki_nur_regeln(self):
        eid = self.anlegen()
        with mock.patch.object(kritik.claude_aufruf, "frage_json") as frage:
            e = kritik.bewerte(self.con, self.konfig, eid)             # Testbasis: ki = false
        frage.assert_not_called()
        self.assertEqual((e["ki_score"], e["score"]), (None, e["regel_score"]))
        self.assertEqual([z for z in regie_lernen.bewertungen(self.con) if z["quelle"] == "ki"], [])


class SelbstAussortieren(MitSpeicher):
    def test_schwache_note_wird_aussortiert(self):
        try:
            from clip_pipeline import lernbot
        except ImportError:
            self.skipTest("python-telegram-bot fehlt")
        eid = self.con.execute("INSERT INTO entwuerfe (name, format, schnittliste, parameter, erstellt) "
                               "VALUES ('e', 'short', 'x', '{}', 'x')").lastrowid
        self.assertIsNone(lernbot.pruefe_auto_verwerfen(self.con, self.konfig, eid))    # noch keine Note
        self.con.execute("INSERT INTO kritiken (entwurf_id, score, regel_score, details, erstellt) "
                         "VALUES (?, 42, 42, '{}', 'x')", (eid,))
        self.konfig.daten["regie"]["kritik"]["schwelle"] = 50.0
        self.assertEqual(lernbot.pruefe_auto_verwerfen(self.con, self.konfig, eid), "Cutter-Score 42 unter 50")
        self.konfig.daten["regie"]["kritik"]["schwelle"] = 40.0
        self.assertIsNone(lernbot.pruefe_auto_verwerfen(self.con, self.konfig, eid))


class Rahmen(unittest.TestCase):
    def test_spielbild_groesser_nur_im_short(self):
        self.assertEqual(entwurf.rahmen_zoom({"format": "short", "parameter": {"rahmen_zoom": 3}}), 1.6)
        self.assertEqual(entwurf.rahmen_zoom({"format": "zusammenschnitt", "parameter": {"rahmen_zoom": 1.4}}), 1.0)
        self.assertIn("scale=1044:-2,crop=720:ih", entwurf._bild(0, 720, 1280, 30, True, rahmen=1.45))
        self.assertIn("[vg0]scale=720:-2[vgs0]", entwurf._bild(0, 720, 1280, 30, True))   # 1,0: wie bisher

    def test_zoom_nur_so_weit_wie_der_titel_lesbar_bleibt(self):
        kino = {"format": "short", "parameter": {"rahmen_zoom": 1.3}}
        z = entwurf.rahmen_grenze(kino, 720, 1280, [(1920, 1080)])
        self.assertEqual(z, 1.28)                                                            # 16:9: knapp unter Kino
        spiel_h = effekt_filter.spiel_hoehe(entwurf._gerade(720 * z), 1920, 1080)
        titel = effekt_filter.lage(720, 1280, True, 11, 0.6, spiel_h)["titel"][1]
        self.assertGreaterEqual(titel, effekt_filter.TITEL_LESBAR * 1280)                   # Kill-Titel bleibt lesbar
        self.assertEqual(entwurf.rahmen_grenze(kino, 720, 1280, [(1920, 1080), (1440, 1080)]), 1.0)   # 4:3: kein Zoom
        self.assertEqual(entwurf.rahmen_grenze(kino, 1080, 1920, [(1920, 1080)]), 1.28)     # Upload-Fassung wie Entwurf


@unittest.skipUnless(HAT_FFMPEG, "ffmpeg fehlt")
class PlanerSpeichertWirksamenZoom(MitRegieMaterial):
    def test_kino_short(self):
        self.momente_anlegen(MOMENTE)
        self.musik_anlegen(150, "episch")
        self.konfig.daten["regie"]["stil"] = "kino"
        p, ziel = regie_lernen.aktuelle(self.con, self.konfig, "short")
        e = regie.erstelle(self.con, self.konfig, "short", parameter=p, ziel=ziel)
        gespeichert = json.loads(self.con.execute("SELECT parameter FROM entwuerfe WHERE id = ?", (e["entwurf"],)).fetchone()[0])
        self.assertEqual((gespeichert["stil"], gespeichert["rahmen_zoom"]), ("kino", 1.28))   # Testmaterial 640×360


if __name__ == "__main__":
    unittest.main()
