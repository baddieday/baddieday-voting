"""Cutter-Maßstab 1.0 (Spec §5, §8): Faktoren lernen aus blinden Lehrern – ohne Zirkelschluss."""

import json
import random
from datetime import timedelta

from clip_pipeline import kriterien, massstab
from clip_pipeline.zeit import iso, jetzt
from tests.hilfen import MitSpeicher

# Spalten von kritiken, die db.MIGRATIONEN (Cutter-Maßstab) anlegt – hier nur fürs Test-Setup
SPALTEN = (("teile", "TEXT"), ("plan_teile", "TEXT"), ("tore", "TEXT"), ("mess_version", "INTEGER"),
           ("ki_version", "TEXT"), ("massstab_version", "INTEGER"))


class Massstab(MitSpeicher):
    def setUp(self):
        super().setUp()
        da = {z["name"] for z in self.con.execute("PRAGMA table_info(kritiken)")}
        for name, typ in SPALTEN:
            if name not in da:
                self.con.execute(f"ALTER TABLE kritiken ADD COLUMN {name} {typ}")

    def entwuerfe(self, n, ki, seed=7):
        """n Short-Entwürfe mit zufälligen, am Video gemessenen Teilnoten; ki(teile) → KI-Note."""
        zufall = random.Random(seed)
        start = jetzt() - timedelta(days=5)
        for i in range(n):
            teile = {k: {"wert": round(zufall.random(), 3), "quelle": "video"} for k in kriterien.START["short"]}
            eid = self.con.execute("INSERT INTO entwuerfe (name, format, schnittliste, parameter, erstellt) "
                                   "VALUES (?, 'short', 'x', '{}', ?)",
                                   (f"e{i}", iso(start + timedelta(hours=2 * i)))).lastrowid
            self.con.execute("INSERT INTO kritiken (entwurf_id, score, regel_score, ki_score, daumen, details, "
                             "erstellt, teile, mess_version, ki_version) VALUES (?, 50, 50, ?, 1, '{}', ?, ?, 1, 'v1')",
                             (eid, ki(teile), iso(start + timedelta(hours=2 * i)), json.dumps(teile)))

    def test_lernt_den_hook_aus_dem_ki_cutter(self):
        self.entwuerfe(30, lambda t: round(100 * t["hook"]["wert"], 1))
        e = massstab.lerne(self.con, self.konfig)
        self.assertTrue(e["aktiv"], e["grund"])
        self.assertGreater(e["faktoren"]["hook"], 1.0)
        self.assertTrue(all(0.5 <= f <= 2.0 for f in e["faktoren"].values()))
        self.assertEqual(e["faktoren"]["hook"], max(e["faktoren"].values()))
        version, _ = massstab.aktualisiere(self.con, self.konfig)
        self.assertEqual(massstab.aktualisiere(self.con, self.konfig)[0], version)   # gleiche Paare → keine neue Version
        self.assertEqual(massstab.faktoren(self.con)["hook"], e["faktoren"]["hook"])
        self.assertTrue(massstab.lernstand_zeilen(self.con, self.konfig)[0].startswith(f"📐 Cutter-Maßstab v{version}"))

    def test_unter_mindestzahl_alles_eins(self):
        self.entwuerfe(10, lambda t: round(100 * t["hook"]["wert"], 1))
        e = massstab.lerne(self.con, self.konfig)
        self.assertFalse(e["aktiv"])
        self.assertEqual(set(e["faktoren"].values()), {1.0})
        self.assertEqual(massstab.faktoren(self.con), {k: 1.0 for k in kriterien.KRITERIEN})   # ohne Version

    def test_kein_zirkelschluss(self):
        """score/regel_score/daumen (die eigene Note) dürfen die Faktoren nicht bewegen – nur die Lehrer."""
        self.entwuerfe(30, lambda t: round(100 * t["hook"]["wert"], 1))
        vorher = massstab.lerne(self.con, self.konfig)["faktoren"]
        self.con.execute("UPDATE kritiken SET score = 0, regel_score = 100, daumen = -1")
        self.assertEqual(massstab.lerne(self.con, self.konfig)["faktoren"], vorher)
        self.con.execute("UPDATE kritiken SET score = entwurf_id % 100, regel_score = 0, daumen = 1")
        self.assertEqual(massstab.lerne(self.con, self.konfig)["faktoren"], vorher)

    def test_ki_maske_tonmix(self):
        """Hängt die KI-Note nur am Tonmix, verschiebt sie ihn trotzdem nicht – die KI hört nichts."""
        self.entwuerfe(30, lambda t: round(100 * t["tonmix"]["wert"], 1))
        e = massstab.lerne(self.con, self.konfig)
        self.assertEqual((e["faktoren"]["tonmix"], e["faktoren"]["beat_sync"]), (1.0, 1.0))
