"""Regisseur 3.0 (30.09.): Schnittstile, Cutter-Kritik und Selbstlernen ohne 👍/👎 – seit dem Cutter-Maßstab 1.0
am fertigen Video (Messung per Fixture statt ffmpeg-Messlauf, Spec §8)."""

import json
import unittest
from pathlib import Path
from unittest import mock

from clip_pipeline import effekt_filter, entwurf, kritik, messung, regie, regie_lernen, stile
from clip_pipeline.claude_aufruf import ClaudeAntwort
from clip_pipeline.zeit import iso, jetzt
from tests.hilfen import HAT_FFMPEG, MitSpeicher, testvideo
from tests.regie_hilfen import MOMENTE, MitRegieMaterial
from tests.test_kriterien import messung as mess_fixture


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
        if score is not None:   # gemessen (mess_version 1): eine Teilnote = score/100 → Note = score
            teile = json.dumps({"hook": {"wert": score / 100, "quelle": "video"}})
            self.con.execute("INSERT INTO kritiken (entwurf_id, score, regel_score, details, erstellt, teile, "
                             "mess_version) VALUES (?, ?, ?, '{}', 'x', ?, 1)", (eid, score, score, teile))
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
        # alte Regel-Noten (vor dem Maßstab, ohne mess_version) zählen nicht mehr
        alt = self.entwurf("kino")
        self.con.execute("INSERT INTO kritiken (entwurf_id, score, regel_score, details, erstellt) "
                         "VALUES (?, 99, 99, '{}', 'x')", (alt,))
        self.assertEqual(stile.statistik(self.con, "short")["kino"]["n"], 6)

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
    def test_regeln_als_plan_naeherung(self):
        gut = kritik.regeln(liste(kill_s=(1.0, 12.0, 22.0, 32.0, 38.0), rahmen=1.45), (30, 75))
        schwach = kritik.regeln(liste(kill_s=(35.0,), rahmen=1.0), (30, 75))
        self.assertEqual(set(gut), {"score", "teile"})                  # alte Rückgabe
        self.assertGreater(gut["teile"]["hook"], schwach["teile"]["hook"])
        self.assertGreater(gut["teile"]["spielbild"], schwach["teile"]["spielbild"])
        self.assertGreater(gut["score"], schwach["score"] + 20)
        aus = liste(kill_s=())                                          # Effekte aus (⚙️): kein kill_s, nur muss
        aus["effekte"] = {"an": False}
        for s in aus["segmente"]:
            s["muss"], s["effekte"] = [s["quelle_start_s"] + 1, s["quelle_start_s"] + 6], []
        r = kritik.regeln(aus, (30, 75))
        self.assertEqual(r["teile"]["hook"], 1.0)                       # Muss-Spanne als Näherung der Aktion
        self.assertNotIn("effektdosis", r["teile"])                     # Effekte aus: unbekannt, nicht geschenkt

    def test_plan_bleibt_blind(self):
        p = kritik.plan(liste(), [0.0, 0.5])
        text = json.dumps(p)
        for verboten in ("auf_beat", "teile", "score", "note", "lufs", "ydif"):
            self.assertNotIn(verboten, text)
        self.assertEqual(p["bilder_s"], [0.0, 0.5])
        zeiten = kritik.bilder_zeiten(liste(kill_s=(3.0, 33.0)))
        self.assertEqual((len(zeiten), zeiten[:3]), (kritik.BILDER, [0.0, 0.5, 1.0]))
        self.assertIn(32.7, zeiten)                                     # Höhepunkt − 0,3 s

    def anlegen(self, mit_video=True):
        ordner = self.tmp / "regie"
        ordner.mkdir()
        (ordner / "e.json").write_text(json.dumps(liste()), encoding="utf-8")
        video = testvideo(ordner / "e.mp4", dauer=4.0) if HAT_FFMPEG and mit_video else ordner / "fehlt.mp4"
        return self.con.execute("INSERT INTO entwuerfe (name, format, schnittliste, parameter, datei, erstellt) "
                                "VALUES ('e', 'short', ?, '{}', ?, ?)",
                                (str(ordner / "e.json"), str(video), iso(jetzt()))).lastrowid

    @unittest.skipUnless(HAT_FFMPEG, "ffmpeg fehlt")
    def test_bewerte_am_video(self):
        eid = self.anlegen()
        m = messung.Messung(**vars(mess_fixture()))                      # 40 s, ruhig, Schnitte bei 10/20/30 s
        with mock.patch.object(kritik.messung, "messe", return_value=m) as messe:
            e = kritik.bewerte(self.con, self.konfig, eid)
        messe.assert_called_once()
        z = self.con.execute("SELECT * FROM kritiken WHERE entwurf_id = ?", (eid,)).fetchone()
        teile, tore = json.loads(z["teile"]), json.loads(z["tore"])
        self.assertEqual((teile["leerlauf"]["quelle"], teile["leerlauf"]["wert"]), ("video", 0.0))   # 11–20 s leer
        self.assertEqual(set(tore), {"schwarz", "standbild", "blitze", "ton", "technik", "doppelt"})
        self.assertIsNone(tore["ton"])                                  # ohne Sidecar: nicht angeglichen
        self.assertEqual((z["mess_version"], z["score"], z["regel_score"]), (1, e["score"], e["score"]))  # ohne KI
        self.assertIn("schwächstes: ", kritik.kritik_zeile(self.con, eid))
        self.assertTrue((self.tmp / "regie" / f"kritik-{eid}" / "messung.json").is_file())
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM massstab").fetchone()[0], 1)   # nachgezogen
        with mock.patch.object(kritik.messung, "messe") as messe:       # zweiter Lauf liest messung.json
            kritik.bewerte(self.con, self.konfig, eid)
        messe.assert_not_called()

    @unittest.skipUnless(HAT_FFMPEG, "ffmpeg fehlt")
    def test_ki_cutter_urteil_wirkt_aufs_lernen(self):
        eid = self.anlegen()
        self.konfig.daten["regie"]["kritik"]["ki"] = True
        antwort = ClaudeAntwort({"score": 80, "staerken": ["starker Einstieg"], "schwaechen": ["Mitte zieht sich"],
                                 "gruende": ["hektisch", "hektisch", "unsinn"]}, None, "{}")
        m = messung.Messung(**vars(mess_fixture()))
        with mock.patch.object(kritik.claude_aufruf, "frage_json", return_value=antwort) as frage, \
                mock.patch.object(kritik.messung, "messe", return_value=m):
            e = kritik.bewerte(self.con, self.konfig, eid)
        ordner = Path(frage.call_args.args[2])
        self.assertTrue((ordner / "kontaktbogen.jpg").is_file())
        self.assertTrue((ordner / "wellenform.png").is_file())
        self.assertNotIn("auf_beat", (ordner / "plan.json").read_text(encoding="utf-8"))
        self.assertEqual((e["ki_score"], e["gruende"]), (80.0, ["hektisch"]))
        self.assertEqual(e["score"], round((e["regel_score"] + 80) / 2, 1))          # κ = 0,5 ohne Maßstab-Version
        z = self.con.execute("SELECT ki_version FROM kritiken WHERE entwurf_id = ?", (eid,)).fetchone()
        self.assertEqual(z["ki_version"], kritik.ki_version(self.konfig))
        ki = [z for z in regie_lernen.bewertungen(self.con) if z["quelle"] == "ki"]
        self.assertEqual([(z["daumen"], json.loads(z["gruende"])) for z in ki], [(1, ["hektisch"])])
        p, _ = regie_lernen.aktuelle(self.con, self.konfig)          # ohne Format: kein Stil, nur das Gelernte
        self.assertEqual(p["seg_min_faktor"], 1.15)                    # „zu hektisch“ vom KI-Cutter wirkt
        self.assertIn("🧐 Cutter", kritik.kritik_zeile(self.con, eid))
        self.assertIsNone(regie_lernen.wirkung(self.con, self.konfig, "short"))   # „🧠 Aus #n“ nur aus deinen Bewertungen
        # Nachmessen ohne KI behält das Urteil (kein neuer Claude-Aufruf)
        with mock.patch.object(kritik.claude_aufruf, "frage_json") as frage:
            e = kritik.bewerte(self.con, self.konfig, eid, ki=False)
        frage.assert_not_called()
        self.assertEqual(e["ki_score"], 80.0)

    @unittest.skipUnless(HAT_FFMPEG, "ffmpeg fehlt")
    def test_cli_nachmessen_und_kritik(self):
        import argparse
        import contextlib
        import io

        from clip_pipeline import cli

        eid = self.anlegen()
        m = messung.Messung(**vars(mess_fixture()))
        aus = io.StringIO()
        with mock.patch.object(kritik.messung, "messe", return_value=m), contextlib.redirect_stdout(aus):
            code = cli._cmd_massstab(argparse.Namespace(nachmessen=True, max=None, zeigen=False), self.konfig, self.con)
        ergebnis = json.loads(aus.getvalue().strip().splitlines()[-1])       # letzte Zeile: JSON
        self.assertEqual((code, ergebnis["gemessen"], ergebnis["abnahme"]["n"]), (0, 1, 1))
        self.assertEqual(self.con.execute("SELECT mess_version FROM kritiken WHERE entwurf_id = ?", (eid,))
                         .fetchone()[0], 1)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli._cmd_kritik(argparse.Namespace(id=999, neu=False), self.konfig, self.con), 1)

    def test_ohne_video_nur_plan(self):
        eid = self.anlegen(mit_video=False)
        with mock.patch.object(kritik.claude_aufruf, "frage_json") as frage:
            e = kritik.bewerte(self.con, self.konfig, eid)             # Testbasis: ki = false
        frage.assert_not_called()
        self.assertEqual((e["ki_score"], e["score"], e["mess_version"]), (None, e["regel_score"], 0))
        self.assertTrue(all(v is None for v in e["tore"].values()))    # ohne Messung kein Deckel
        self.assertEqual({t["quelle"] for t in e["teile"].values() if t}, {"plan"})
        self.assertEqual([z for z in regie_lernen.bewertungen(self.con) if z["quelle"] == "ki"], [])


class SelbstAussortieren(MitSpeicher):
    def gemessen(self, score, tore=None, normiert=True):
        n = self.con.execute("SELECT COUNT(*) FROM entwuerfe").fetchone()[0]
        eid = self.con.execute("INSERT INTO entwuerfe (name, format, schnittliste, parameter, erstellt) "
                               "VALUES (?, 'short', 'x', '{}', 'x')", (f"g{n}",)).lastrowid
        self.con.execute("INSERT INTO kritiken (entwurf_id, score, regel_score, details, erstellt, tore, mess_version) "
                         "VALUES (?, ?, ?, ?, 'x', ?, 1)",
                         (eid, score, score, json.dumps({"normiert": normiert}), json.dumps(tore)))
        return eid

    def test_tor_und_eigene_notenverteilung(self):
        try:
            from clip_pipeline import lernbot
        except ImportError:
            self.skipTest("python-telegram-bot fehlt")
        self.konfig.daten["regie"]["kritik"]["schwelle"] = 50.0
        eid = self.con.execute("INSERT INTO entwuerfe (name, format, schnittliste, parameter, erstellt) "
                               "VALUES ('e', 'short', 'x', '{}', 'x')").lastrowid
        self.assertIsNone(lernbot.pruefe_auto_verwerfen(self.con, self.konfig, eid))    # noch keine Note
        blitz = self.gemessen(70, {"blitze": {"ok": False, "text": "Blitze 7/s (Grenze 3)", "wert": 7}})
        self.assertEqual(lernbot.pruefe_auto_verwerfen(self.con, self.konfig, blitz), "Tor: Blitze 7/s (Grenze 3)")
        schwach = self.gemessen(42)
        self.assertIsNone(lernbot.pruefe_auto_verwerfen(self.con, self.konfig, schwach))  # < 20 gemessen: nur Tore
        for i in range(20):
            self.gemessen(40 + 2 * i)                                   # 40 … 78
        self.assertEqual(kritik.schwelle(self.con, self.konfig, "short"), 46.4)          # Q20 der eigenen Noten
        self.assertEqual(lernbot.pruefe_auto_verwerfen(self.con, self.konfig, schwach), "Cutter-Score 42 unter 46")
        self.konfig.daten["regie"]["kritik"]["schwelle"] = 0.0         # aus: der Cutter sortiert nichts aus
        self.assertIsNone(lernbot.pruefe_auto_verwerfen(self.con, self.konfig, schwach))
        self.assertIsNone(lernbot.pruefe_auto_verwerfen(self.con, self.konfig, blitz))


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
