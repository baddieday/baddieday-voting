"""Cutter-Maßstab 1.0 (Spec §2, §8): Kriterien, Tore und Note ohne ffmpeg – mit handgebauter Messung."""

import types
import unittest

from clip_pipeline import kriterien


def liste(kills=(0.8, 12.0, 22.0, 33.0), dauer=40.0, segmente=4, effekte_an=True):
    """Short-Schnittliste: gleich lange Segmente mit harten Schnitten, Quellzeit = Zeitleiste."""
    laenge = dauer / segmente
    segs = []
    for i in range(segmente):
        a, b = laenge * i, laenge * (i + 1)
        segs.append({"nr": i + 1, "moment": f"m{i}", "datei": f"/v/{i}.mp4", "match_id": "x", "stimmung": "episch",
                     "quelle_start_s": a, "quelle_ende_s": b, "quelle_dauer_s": dauer, "muss": [a, b],
                     "zeit_start": a, "zeit_ende": b, "uebergang": {"art": "schnitt", "dauer_s": 0.0},
                     "intensitaet": float(i), "kill_s": [k for k in kills if a <= k < b],
                     "effekte": [{"art": "punch", "t_s": k, "dauer_s": 0.35, "staerke": 0.8}
                                 for k in kills if a <= k < b]})
    return {"format": "short", "dauer_s": dauer, "aufloesung": [720, 1280], "fps": 30, "segmente": segs,
            "effekte": {"an": effekte_an}, "musik": None, "parameter": {"rahmen_zoom": 1.0}}


def messung(dauer=40.0, fps=30, yavg=None, schnitte=(10.0, 20.0, 30.0)):
    """Ruhiges Video: gleichmäßige Bewegung, konstanter Pegel, Szenenwechsel an den Schnitten."""
    n = int(dauer * fps)
    raster = int(dauer / 0.1)
    return types.SimpleNamespace(
        version=1, dauer_s=dauer, fps=float(fps), m=[-14.0] * raster, s=[-14.0] * raster, i_lufs=-14.0, lra=6.0,
        tp_db=-1.5, ton_da=True, mv=None, mm=None, iv=None, yavg_v=[120.0] * n, sat_v=[80.0] * n,
        yavg_b=list(yavg) if yavg is not None else [120.0] * n, ydif_b=[4.0] * n, t_bild=[i / fps for i in range(n)],
        scd=[(t, 20.0) for t in schnitte], schwarz=[], stand=[], stille=[], hash=[], dauer_ton=dauer,
        dauer_bild=dauer, fehler=[])


class Kriterien(unittest.TestCase):
    def test_hook_und_leerlauf_am_video(self):
        m = messung()
        frueh = kriterien.teilnoten(liste(kills=(0.8, 12.0, 22.0, 33.0)), m, None, "short")
        spaet = kriterien.teilnoten(liste(kills=(3.5, 12.0, 22.0, 33.0)), m, None, "short")
        self.assertGreater(frueh["hook"].wert, spaet["hook"].wert)
        self.assertEqual((frueh["hook"].quelle, frueh["hook"].zeit_s), ("video", 0.8))
        # Lücke 7 s: Kills dicht bis zum Schnitt bei 10 s, dann erst wieder ab 17 s → Leerlauf 0
        dicht = [float(k) for k in range(1, 10)] + [float(k) for k in range(17, 40)]
        t = kriterien.teilnoten(liste(kills=dicht), m, None, "short")
        self.assertEqual(t["leerlauf"].wert, 0.0)
        self.assertIn("0:10–0:17", t["leerlauf"].befund)
        self.assertEqual(set(t), {k for k, v in kriterien.KRITERIEN.items() if "short" in v["formate"]})
        for fmt, w in kriterien.START.items():
            self.assertAlmostEqual(sum(w.values()), 1.0, msg=fmt)

    def test_ohne_messung_plan_rueckfall(self):
        l = liste()
        teile = kriterien.teilnoten(l, None, None, "short")          # kein KeyError, nur Plan-Näherung
        self.assertEqual({t.quelle for t in teile.values() if t}, {"plan"})
        self.assertIsNone(teile["tonmix"])                           # braucht die Messung
        self.assertEqual(kriterien.plan_teile(l), {k: t.wert for k, t in teile.items() if t})
        tore = kriterien.tore(l, None, None)
        self.assertTrue(all(v is None for v in tore.values()))       # ohne Messung kein Deckel
        n = kriterien.note(teile, tore, "short", {})
        self.assertTrue(0 < n <= 100)
        # Renormierung: ein fehlendes Kriterium zählt nicht als 0
        ohne = {k: v for k, v in teile.items() if k != "hook"}
        self.assertNotEqual(kriterien.note(ohne, tore, "short", {}), kriterien.note({**ohne, "hook": 0.0}, tore,
                                                                                         "short", {}))

    def test_blitz_tor(self):
        fps, dauer = 30, 40.0
        n = int(fps * dauer)

        def rechteck(hz):
            periode = round(fps / hz)
            return [235.0 if (i % periode) < periode / 2 else 16.0 for i in range(n)]

        l = liste()
        schnell = messung(yavg=rechteck(7.5))
        tore = kriterien.tore(l, schnell, None)
        self.assertFalse(tore["blitze"]["ok"])
        self.assertIn("Blitze", tore["blitze"]["text"])
        teile = kriterien.teilnoten(l, schnell, None, "short")
        self.assertLessEqual(kriterien.note(teile, tore, "short", {}), 40.0)
        langsam = kriterien.tore(l, messung(yavg=rechteck(2.5)), None)
        self.assertTrue(langsam["blitze"]["ok"])
        self.assertTrue(all(t is None or t["ok"] for t in langsam.values()), langsam)

    def test_verluste_nennen_befund(self):
        teile = kriterien.teilnoten(liste(kills=(3.5, 12.0)), messung(), None, "short")
        v = kriterien.verluste(teile, "short", {}, n=2)
        self.assertEqual(len(v), 2)
        self.assertGreaterEqual(v[0][1], v[1][1])
        self.assertTrue(all(k in kriterien.KRITERIEN for k, _, _ in v))


if __name__ == "__main__":
    unittest.main()
