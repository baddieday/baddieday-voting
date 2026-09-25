"""Tests für publikum.py: Posts, Messungen, Plausibilität, Hand-Eingabe und Publikums-Score (Spec §5, §6, §7.1, §13).

Alle Zeiten sind fest (T0 und Tage danach) – kein Test hängt vom heutigen Datum ab. Rechnungen werden mit
Zahlen geprüft, die man im Kopf nachrechnen kann (Beispiele aus den Docstrings von publikum.py).
"""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from clip_pipeline import big, db, konfig, publikum, shorts
from clip_pipeline.zeit import UTC, iso

from tests.hilfen import MitSpeicher

T0 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
TIKTOK_LINK = "https://www.tiktok.com/@baddieday/video/7300123456789012345"


def tag(n: float) -> datetime:
    """T0 plus n Tage – so lesen sich die Tests wie „Post an Tag 1, Messung an Tag 8“."""
    return T0 + timedelta(days=n)


class MitPublikum(MitSpeicher):
    """Test-Datenbank mit festen [publikum]-Werten (unabhängig von einer lokalen Konfiguration)."""

    def setUp(self):
        super().setUp()
        self.konfig.daten["publikum"].update(plattformen=["tiktok"], alter_tage=7, mindest_alter_tage=3, fenster=20)
        self.konfig.daten["publikum"]["gewichte"] = {"wiedergabe": 0.5, "engagement": 0.3, "reichweite": 0.2}
        self.konfig.daten["shorts"].update(endcard=True, endcard_s=2.5)
        self._naechstes_ziel = 1000

    def post(self, *, gepostet: datetime = T0, plattform: str = "tiktok", dauer_s: float = 20.0,
             art: str = "clip") -> int:
        """Legt einen Post an (je Aufruf ein neues Ziel)."""
        self._naechstes_ziel += 1
        daten = {"dauer_s": dauer_s, "rezept": publikum.rezept_fuer_clip(dauer_s), "merkmale": {}}
        post_id, neu = publikum.post_anlegen(self.con, art=art, ziel_id=self._naechstes_ziel, plattform=plattform,
                                             daten=daten, zeit=gepostet)
        self.assertTrue(neu)
        return post_id

    def messung(self, post_id: int, am: datetime, **werte) -> int:
        return publikum.speichere_messung(self.con, post_id, werte, "hand", zeit=am)

    def entwurf(self, *, rezept: str | None = None, dauer_s: float = 30.0, beats: int | None = 1,
                segmente: list | None = None, musik: dict | None = None) -> tuple[sqlite3.Row, dict]:
        parameter = {} if beats is None else {"beats_pro_schnitt": beats}
        liste = {"dauer_s": dauer_s, "parameter": parameter, "stimmung": "episch", "musik": musik,
                 "segmente": segmente if segmente is not None else []}
        pfad = self.tmp / f"entwurf-{self._naechstes_ziel}.json"
        self._naechstes_ziel += 1
        pfad.write_text(json.dumps(liste), encoding="utf-8")
        eid = self.con.execute(
            "INSERT INTO entwuerfe (name, format, schnittliste, parameter, dauer_s, erstellt, rezept)"
            " VALUES (?, 'short', ?, '{}', ?, ?, ?)", (pfad.stem, str(pfad), dauer_s, iso(T0), rezept)).lastrowid
        return self.con.execute("SELECT * FROM entwuerfe WHERE id = ?", (eid,)).fetchone(), liste

    def bewerteter_post(self, *, gepostet: datetime, r: float | None, e: float, v: float,
                        plattform: str = "tiktok") -> int:
        """Ein schon bewerteter Post für die Vergleichsbasis – nur score_teile zählt."""
        post_id = self.post(gepostet=gepostet, plattform=plattform)
        teile = {"r": r, "e": e, "v": v}
        self.con.execute("UPDATE posts SET score = 0, score_teile = ?, bewertet_utc = ? WHERE id = ?",
                         (json.dumps(teile), iso(gepostet + timedelta(days=7)), post_id))
        return post_id


# --- Komponenten und robuste Standardisierung ------------------------------------------------------------

class Komponenten(MitPublikum):
    def test_zahlenbeispiel_aus_dem_docstring(self):
        k = publikum.komponenten({"views": 1000, "likes": 50, "shares": 5, "wiedergabe_s": 6.0}, 20.0)
        self.assertAlmostEqual(k["r"], 0.3)
        self.assertAlmostEqual(k["e"], 0.06)                 # (50 + 2·5) / 1000
        self.assertAlmostEqual(k["v"], math.log(1001), places=9)
        self.assertAlmostEqual(k["v"], 6.909, places=3)
        self.assertIn("Engagement unvollständig", k["vermerke"])  # kommentare/saves fehlen → 0 (A10)

    def test_alle_zaehler_da_kein_vermerk(self):
        k = publikum.komponenten({"views": 100, "likes": 10, "kommentare": 2, "shares": 1, "saves": 3,
                                  "wiedergabe_s": 5.0}, 20.0)
        self.assertAlmostEqual(k["e"], (10 + 2 * 1 + 3 + 2) / 100)
        self.assertEqual(k["vermerke"], [])

    def test_r_aus_voll_prozent_wenn_wiedergabe_fehlt(self):
        k = publikum.komponenten({"views": 100, "voll_prozent": 34.0}, 20.0)
        self.assertAlmostEqual(k["r"], 0.34)

    def test_wiedergabe_hat_vorrang_vor_voll_prozent(self):
        k = publikum.komponenten({"views": 100, "wiedergabe_s": 10.0, "voll_prozent": 90.0}, 20.0)
        self.assertAlmostEqual(k["r"], 0.5)

    def test_r_hoechstens_1_2(self):
        k = publikum.komponenten({"views": 100, "wiedergabe_s": 29.0}, 20.0)  # 145 % – mehrfach angesehen
        self.assertEqual(k["r"], 1.2)

    def test_ohne_wiedergabe_und_voll_prozent_kein_r(self):
        self.assertIsNone(publikum.komponenten({"views": 100, "likes": 1}, 20.0)["r"])

    def test_ohne_views_kein_e_und_kein_v(self):
        k = publikum.komponenten({"likes": 5, "wiedergabe_s": 6.0}, 20.0)
        self.assertIsNone(k["e"])
        self.assertIsNone(k["v"])
        self.assertAlmostEqual(k["r"], 0.3)

    def test_null_views_teilt_nicht_durch_null(self):
        k = publikum.komponenten({"views": 0, "likes": 0, "kommentare": 0, "shares": 0, "saves": 0}, 20.0)
        self.assertEqual(k["e"], 0.0)
        self.assertEqual(k["v"], 0.0)

    def test_dauer_null_ist_ein_fehler(self):
        with self.assertRaises(ValueError):
            publikum.komponenten({"views": 10, "wiedergabe_s": 3.0}, 0.0)

    def test_sqlite_zeile_geht_auch(self):
        post_id = self.post()
        mid = self.messung(post_id, tag(7), views=1000, likes=50, shares=5, wiedergabe_s=6.0)
        zeile = self.con.execute("SELECT * FROM publikum_messungen WHERE id = ?", (mid,)).fetchone()
        self.assertAlmostEqual(publikum.komponenten(zeile, 20.0)["e"], 0.06)


class RobustZ(MitPublikum):
    def test_median_mad_beispiel(self):
        z, median, mad = publikum.robust_z(5, [1, 2, 3, 4, 5])
        self.assertEqual((median, mad), (3, 1))
        self.assertAlmostEqual(z, 2 / 1.4826)
        self.assertAlmostEqual(z, 1.349, places=3)

    def test_mad_minimum_bei_realistischem_engagement(self):
        # Engagement 5–9 %: echter MAD 0,01 – gerechnet wird mit 0,05, das dämpft z um Faktor 5
        z, median, mad = publikum.robust_z(0.09, [0.05, 0.06, 0.07, 0.08, 0.09])
        self.assertAlmostEqual(median, 0.07)
        self.assertAlmostEqual(mad, 0.01)                       # ehrlich: der gemessene MAD, nicht 0,05
        self.assertAlmostEqual(z, 0.02 / (1.4826 * 0.05))
        self.assertAlmostEqual(z, 0.27, places=2)               # ohne Minimum wären es 1,35

    def test_begrenzung_auf_plus_minus_2_5(self):
        self.assertEqual(publikum.robust_z(100, [1, 2, 3, 4, 5])[0], 2.5)
        self.assertEqual(publikum.robust_z(-100, [1, 2, 3, 4, 5])[0], -2.5)

    def test_gleiche_werte_ergeben_null(self):
        self.assertEqual(publikum.robust_z(3, [3, 3, 3, 3, 3])[0], 0.0)

    def test_leere_basis_ist_ein_fehler(self):
        with self.assertRaises(ValueError):
            publikum.robust_z(1.0, [])


# --- Messung wählen, Fälligkeit, Vergleichsbasis --------------------------------------------------------

class MessungWaehlen(MitPublikum):
    def setUp(self):
        super().setUp()
        self.post_zeile = {"gepostet_utc": iso(T0)}

    @staticmethod
    def m(nr: int, tage: float, views: int | None = 100) -> dict:
        return {"id": nr, "gemessen_utc": iso(tag(tage)), "views": views}

    def test_naechste_an_sieben_tagen(self):
        messungen = [self.m(1, 3.5), self.m(2, 6.0), self.m(3, 9.0)]
        self.assertEqual(publikum.waehle_messung(self.post_zeile, messungen, self.konfig)["id"], 2)

    def test_nichts_unter_drei_tagen(self):
        self.assertIsNone(publikum.waehle_messung(self.post_zeile, [self.m(1, 1.0), self.m(2, 2.9)], self.konfig))

    def test_genau_drei_tage_zaehlt(self):
        self.assertEqual(publikum.waehle_messung(self.post_zeile, [self.m(1, 3.0)], self.konfig)["id"], 1)

    def test_ohne_views_uebersprungen(self):
        messungen = [self.m(1, 4.0), self.m(2, 7.0, views=None)]
        self.assertEqual(publikum.waehle_messung(self.post_zeile, messungen, self.konfig)["id"], 1)

    def test_gleichstand_nimmt_die_spaetere(self):
        messungen = [self.m(2, 8.0), self.m(1, 6.0)]  # beide 1 Tag von 7 entfernt, Reihenfolge egal
        self.assertEqual(publikum.waehle_messung(self.post_zeile, messungen, self.konfig)["id"], 2)

    def test_keine_messungen(self):
        self.assertIsNone(publikum.waehle_messung(self.post_zeile, [], self.konfig))


class Faellig(MitPublikum):
    def test_erst_ab_alter_tage(self):
        zeile = {"gepostet_utc": iso(T0), "bewertet_utc": None}
        self.assertFalse(publikum.ist_faellig(zeile, self.konfig, tag(6.99)))
        self.assertTrue(publikum.ist_faellig(zeile, self.konfig, tag(7)))

    def test_schon_bewertet_nie_wieder(self):
        zeile = {"gepostet_utc": iso(T0), "bewertet_utc": iso(tag(7))}
        self.assertFalse(publikum.ist_faellig(zeile, self.konfig, tag(30)))

    def test_ohne_zeit_gilt_jetzt(self):
        zeile = {"gepostet_utc": iso(T0), "bewertet_utc": None}
        with mock.patch.object(publikum, "jetzt", return_value=tag(8)):
            self.assertTrue(publikum.ist_faellig(zeile, self.konfig))
        with mock.patch.object(publikum, "jetzt", return_value=tag(2)):
            self.assertFalse(publikum.ist_faellig(zeile, self.konfig))


class Vergleichsbasis(MitPublikum):
    def test_nur_vorgaenger_derselben_plattform_juengste_zuerst(self):
        alt1 = self.bewerteter_post(gepostet=tag(1), r=0.1, e=0.01, v=1.0)
        alt2 = self.bewerteter_post(gepostet=tag(2), r=None, e=0.02, v=2.0)
        self.bewerteter_post(gepostet=tag(3), r=0.3, e=0.03, v=3.0, plattform="youtube")
        self.post(gepostet=tag(4))                                    # unbewertet → nicht in der Basis
        spaeter = self.bewerteter_post(gepostet=tag(9), r=0.9, e=0.09, v=9.0)  # nach dem Post gepostet
        ich = self.post(gepostet=tag(5))
        basis = publikum.vergleichsbasis(self.con, publikum.post(self.con, ich), self.konfig)
        self.assertEqual([b["post_id"] for b in basis], [alt2, alt1])
        self.assertEqual(basis[0], {"post_id": alt2, "r": None, "e": 0.02, "v": 2.0})
        self.assertNotIn(spaeter, [b["post_id"] for b in basis])

    def test_spaet_bewerteter_post_bleibt_bei_seinen_vorgaengern(self):
        # Der Post wird erst an Tag 40 bewertet; inzwischen sind neuere Posts bewertet – die zählen nicht.
        for i in range(5):
            self.bewerteter_post(gepostet=tag(i), r=0.5, e=0.05, v=5.0)
        ich = self.post(gepostet=tag(10))
        for i in range(5):
            self.bewerteter_post(gepostet=tag(20 + i), r=0.9, e=0.09, v=9.0)
        basis = publikum.vergleichsbasis(self.con, publikum.post(self.con, ich), self.konfig)
        self.assertEqual(len(basis), 5)
        self.assertTrue(all(b["r"] == 0.5 for b in basis))

    def test_hoechstens_fenster(self):
        self.konfig.daten["publikum"]["fenster"] = 3
        ids = [self.bewerteter_post(gepostet=tag(i), r=0.5, e=0.05, v=5.0) for i in range(6)]
        ich = self.post(gepostet=tag(10))
        basis = publikum.vergleichsbasis(self.con, publikum.post(self.con, ich), self.konfig)
        self.assertEqual([b["post_id"] for b in basis], ids[:-4:-1])  # die drei jüngsten

    def test_kaputtes_score_teile_ist_ein_fehler(self):
        kaputt = self.bewerteter_post(gepostet=tag(1), r=0.5, e=0.05, v=5.0)
        self.con.execute("UPDATE posts SET score_teile = '{kaputt' WHERE id = ?", (kaputt,))
        ich = self.post(gepostet=tag(5))
        with self.assertRaises(ValueError) as fehler:
            publikum.vergleichsbasis(self.con, publikum.post(self.con, ich), self.konfig)
        self.assertIn(f"#{kaputt}", str(fehler.exception))

    def test_score_teile_ohne_e_ist_ein_fehler(self):
        kaputt = self.bewerteter_post(gepostet=tag(1), r=0.5, e=0.05, v=5.0)
        self.con.execute("UPDATE posts SET score_teile = ? WHERE id = ?", (json.dumps({"r": 0.5, "v": 5}), kaputt))
        ich = self.post(gepostet=tag(5))
        with self.assertRaises(ValueError):
            publikum.vergleichsbasis(self.con, publikum.post(self.con, ich), self.konfig)


# --- Score ---------------------------------------------------------------------------------------------

class Score(MitPublikum):
    def basis(self, n: int, n_r: int | None = None) -> list[dict]:
        """n Vergleichsposts mit r 0,1…, e 0,01…, v 1…; nur die ersten n_r haben ein r."""
        n_r = n if n_r is None else n_r
        return [{"post_id": i, "r": 0.1 * (i + 1) if i < n_r else None, "e": 0.01 * (i + 1), "v": float(i + 1)}
                for i in range(n)]

    def score(self, basis: list[dict], **werte):
        post_zeile = {"id": 99, "gepostet_utc": iso(T0), "dauer_s": 20.0}
        messung = {"id": 7, "gemessen_utc": iso(tag(7.1)), **werte}
        return publikum.score_fuer(post_zeile, [messung], basis, self.konfig)

    def test_basis_zu_klein(self):
        score, teile = self.score(self.basis(4), views=1000, likes=50, wiedergabe_s=10.0)
        self.assertEqual(score, 0.0)
        self.assertEqual((teile["z_r"], teile["z_e"], teile["z_v"]), (0.0, 0.0, 0.0))
        self.assertIn("Basis zu klein", teile["vermerke"])
        self.assertEqual(teile["basis_n"], 4)
        self.assertAlmostEqual(teile["r"], 0.5)  # Komponenten stehen trotzdem drin (für spätere Vergleiche)

    def test_basis_zu_klein_vermerk_aus_der_konstante(self):
        # lernbot_publikum erkennt „Basis zu klein“ an genau dieser Konstante – kein zweites Literal
        _, teile = self.score(self.basis(0), views=1000, likes=50, wiedergabe_s=10.0)
        self.assertEqual(teile["vermerke"][-1], publikum.VERMERK_BASIS_ZU_KLEIN)
        self.assertEqual(publikum.VERMERK_BASIS_ZU_KLEIN, "Basis zu klein")

    def test_basis_zu_klein_ohne_wiedergabe(self):
        """Spec §6.4: Der Vermerk „ohne Wiedergabe“ gilt auch, wenn der Score wegen zu kleiner Basis 0 ist –
        die gespeicherten Gewichte 0,6/0,4 brauchen den Vermerk, der sie erklärt."""
        score, teile = self.score(self.basis(4), views=1240, likes=61)
        self.assertEqual(score, 0.0)
        self.assertEqual((teile["z_r"], teile["z_e"], teile["z_v"]), (None, 0.0, 0.0))
        self.assertIn("Basis zu klein", teile["vermerke"])
        self.assertIn("ohne Wiedergabe", teile["vermerke"])
        for teil, gewicht in (("r", 0.0), ("e", 0.6), ("v", 0.4)):
            self.assertAlmostEqual(teile["gewichte"][teil], gewicht)

    def test_normaler_score_zahlenbeispiel(self):
        # Basis r 0,1…0,5 (Median 0,3, MAD 0,1), e 0,01…0,05 (Median 0,03, MAD 0,01 → Minimum 0,05), v 1…5
        score, teile = self.score(self.basis(5), views=math.e ** 5 - 1, likes=0.05 * (math.e ** 5 - 1),
                                  kommentare=0, shares=0, saves=0, wiedergabe_s=10.0)
        z_r = (0.5 - 0.3) / (1.4826 * 0.1)
        z_e = (0.05 - 0.03) / (1.4826 * 0.05)
        z_v = (5 - 3) / (1.4826 * 1)
        self.assertAlmostEqual(teile["z_r"], z_r)
        self.assertAlmostEqual(teile["z_e"], z_e)
        self.assertAlmostEqual(teile["z_v"], z_v)
        self.assertAlmostEqual(score, 0.5 * z_r + 0.3 * z_e + 0.2 * z_v, places=4)
        self.assertEqual(teile["vermerke"], [])
        self.assertAlmostEqual(teile["median_r"], 0.3)
        self.assertAlmostEqual(teile["mad_e"], 0.01)

    def test_ohne_wiedergabe_umgewichtet(self):
        score, teile = self.score(self.basis(5), views=math.e ** 5 - 1, likes=0, kommentare=0, shares=0, saves=0)
        self.assertIsNone(teile["r"])
        self.assertIsNone(teile["z_r"])
        self.assertIn("ohne Wiedergabe", teile["vermerke"])
        self.assertAlmostEqual(score, 0.6 * teile["z_e"] + 0.4 * teile["z_v"], places=4)

    def test_basis_ohne_r_werte_wie_ohne_wiedergabe(self):
        for n_r in (0, 4):
            with self.subTest(n_r=n_r):
                score, teile = self.score(self.basis(20, n_r=n_r), views=100, likes=5, kommentare=0, shares=0,
                                          saves=0, wiedergabe_s=10.0)
                self.assertAlmostEqual(teile["r"], 0.5)   # r ist gemessen, zählt aber nicht
                self.assertIsNone(teile["z_r"])
                self.assertIn("Basis ohne Wiedergabe", teile["vermerke"])
                self.assertEqual((teile["basis_n"], teile["basis_n_r"]), (20, n_r))
                self.assertAlmostEqual(score, 0.6 * teile["z_e"] + 0.4 * teile["z_v"], places=4)

    def test_fuenf_r_werte_normal(self):
        score, teile = self.score(self.basis(20, n_r=5), views=100, likes=5, kommentare=0, shares=0, saves=0,
                                  wiedergabe_s=10.0)
        self.assertIsNotNone(teile["z_r"])
        self.assertNotIn("Basis ohne Wiedergabe", teile["vermerke"])
        self.assertEqual(teile["basis_n_r"], 5)
        self.assertAlmostEqual(score, 0.5 * teile["z_r"] + 0.3 * teile["z_e"] + 0.2 * teile["z_v"], places=4)

    def test_score_teile_vollstaendig(self):
        _, teile = self.score(self.basis(6), views=100, likes=5, wiedergabe_s=10.0)
        for feld in ("r", "e", "v", "z_r", "z_e", "z_v", "median_r", "mad_r", "median_e", "mad_e", "median_v",
                     "mad_v", "messung_id", "messung_alter_tage", "basis_n", "basis_n_r", "vermerke"):
            self.assertIn(feld, teile)
        self.assertEqual(teile["messung_id"], 7)
        self.assertAlmostEqual(teile["messung_alter_tage"], 7.1)
        json.dumps(teile)  # muss als JSON speicherbar sein

    def test_keine_passende_messung(self):
        post_zeile = {"id": 99, "gepostet_utc": iso(T0), "dauer_s": 20.0}
        score, teile = publikum.score_fuer(post_zeile, [{"id": 1, "gemessen_utc": iso(tag(1)), "views": 5}],
                                           self.basis(6), self.konfig)
        self.assertIsNone(score)
        self.assertTrue(teile["vermerke"])

    def test_deterministisch(self):
        ergebnisse = {json.dumps(self.score(self.basis(9, n_r=7), views=321, likes=17, shares=2,
                                            wiedergabe_s=8.5), sort_keys=True) for _ in range(3)}
        self.assertEqual(len(ergebnisse), 1)

    def test_gewichte_aus_der_konfig(self):
        self.konfig.daten["publikum"]["gewichte"] = {"wiedergabe": 1.0, "engagement": 0.0, "reichweite": 0.0}
        score, teile = self.score(self.basis(5), views=100, likes=5, wiedergabe_s=10.0)
        self.assertAlmostEqual(score, teile["z_r"], places=4)

    def test_gewichte_ohne_engagement_und_reichweite_sind_ein_konfigfehler(self):
        self.konfig.daten["publikum"]["gewichte"] = {"wiedergabe": 1.0, "engagement": 0.0, "reichweite": 0.0}
        with self.assertRaises(konfig.KonfigFehler):
            self.score(self.basis(5), views=100, likes=5)  # ohne Wiedergabe → e und v müssten es tragen


class BewerteAlle(MitPublikum):
    def test_zu_jung_dann_genau_einmal(self):
        p = self.post(gepostet=tag(0))
        self.messung(p, tag(3), views=500, likes=20, wiedergabe_s=6.0)
        ergebnis = publikum.bewerte_alle(self.con, self.konfig, tag(5))
        self.assertEqual((ergebnis["bewertet"], ergebnis["noch_zu_jung"]), (0, 1))
        self.assertIsNone(publikum.post(self.con, p)["bewertet_utc"])

        self.messung(p, tag(7), views=900, likes=40, wiedergabe_s=7.0)
        ergebnis = publikum.bewerte_alle(self.con, self.konfig, tag(8))
        self.assertEqual(ergebnis, {"bewertet": 1, "ohne_messung": 0, "noch_zu_jung": 0, "fehler": 0,
                                    "posts": [{"id": p, "score": 0.0}]})
        zeile = publikum.post(self.con, p)
        teile = json.loads(zeile["score_teile"])
        self.assertEqual(zeile["bewertet_utc"], iso(tag(8)))
        self.assertIn("Basis zu klein", teile["vermerke"])
        self.assertAlmostEqual(teile["messung_alter_tage"], 7.0)  # die Tag-7-Messung, nicht Tag 3

        # Idempotenz: zweiter Lauf (auch mit neuer Messung) ändert nichts
        self.messung(p, tag(9), views=5000, likes=400, wiedergabe_s=15.0)
        vorher = tuple(publikum.post(self.con, p))
        ergebnis = publikum.bewerte_alle(self.con, self.konfig, tag(10))
        self.assertEqual(ergebnis["bewertet"], 0)
        self.assertEqual(tuple(publikum.post(self.con, p)), vorher)

    def test_ohne_passende_messung_bleibt_unbewertet(self):
        p = self.post(gepostet=tag(0))
        self.messung(p, tag(1), views=100)          # zu früh
        self.messung(p, tag(8), likes=5)            # ohne views
        ergebnis = publikum.bewerte_alle(self.con, self.konfig, tag(9))
        self.assertEqual((ergebnis["bewertet"], ergebnis["ohne_messung"]), (0, 1))
        self.assertIsNone(publikum.post(self.con, p)["score"])

    def test_aelteste_zuerst_frueher_bewertete_zaehlen_zur_basis(self):
        ids = []
        for i in range(6):
            p = self.post(gepostet=tag(i))
            self.messung(p, tag(i + 7), views=100 * (i + 1), likes=5 * (i + 1), kommentare=0, shares=0, saves=0,
                         wiedergabe_s=4.0 + i)
            ids.append(p)
        yt = self.post(gepostet=tag(2.5), plattform="youtube")
        self.messung(yt, tag(9.5), views=99999, likes=1)
        ergebnis = publikum.bewerte_alle(self.con, self.konfig, tag(30))
        self.assertEqual(ergebnis["bewertet"], 7)
        self.assertEqual([x["id"] for x in ergebnis["posts"]], [ids[0], ids[1], ids[2], yt, ids[3], ids[4], ids[5]])
        basis_n = [json.loads(publikum.post(self.con, p)["score_teile"])["basis_n"] for p in ids]
        self.assertEqual(basis_n, [0, 1, 2, 3, 4, 5])  # YouTube zählt nicht zur TikTok-Basis
        letzter = publikum.post(self.con, ids[5])
        self.assertGreater(letzter["score"], 0)       # bester Post von allen: über dem Median
        self.assertNotIn("Basis zu klein", json.loads(letzter["score_teile"])["vermerke"])

    def test_kaputte_basis_trifft_nur_den_einen_post(self):
        kaputt = self.bewerteter_post(gepostet=tag(0), r=0.5, e=0.05, v=5.0)
        self.con.execute("UPDATE posts SET score_teile = '{kaputt' WHERE id = ?", (kaputt,))
        betroffen = self.post(gepostet=tag(1))
        self.messung(betroffen, tag(8), views=100, likes=5)
        anderer = self.post(gepostet=tag(1), plattform="youtube")
        self.messung(anderer, tag(8), views=100, likes=5)
        with self.assertLogs("pipeline", level="WARNING") as logs:
            ergebnis = publikum.bewerte_alle(self.con, self.konfig, tag(9))
        self.assertEqual((ergebnis["bewertet"], ergebnis["fehler"]), (1, 1))
        self.assertEqual(ergebnis["posts"], [{"id": anderer, "score": 0.0}])
        self.assertIsNone(publikum.post(self.con, betroffen)["bewertet_utc"])
        self.assertTrue(any(f"#{betroffen}" in z for z in logs.output))

    def test_datenbankfehler_fliegt_durch(self):
        self.con.execute("DROP TABLE publikum_messungen")
        self.post(gepostet=tag(0))
        with self.assertRaises(sqlite3.Error):
            publikum.bewerte_alle(self.con, self.konfig, tag(9))


# --- Posts ---------------------------------------------------------------------------------------------

class Posts(MitPublikum):
    DATEN = {"dauer_s": 22.0, "rezept": {"hook": "stark_zuerst", "laenge": "kurz", "tempo": "none",
                                         "machart": "roh", "experiment": False}, "merkmale": {"momente": []}}

    def test_ziel(self):
        self.assertEqual(publikum.ziel("clip", 5), "clip:5")
        self.assertEqual(publikum.ziel("entwurf", 41), "entwurf:41")
        with self.assertRaises(ValueError):
            publikum.ziel("highlight", 1)

    def test_anlegen_zweimal_ein_post_erster_zeitpunkt(self):
        pid, neu = publikum.post_anlegen(self.con, art="entwurf", ziel_id=41, plattform="tiktok", daten=self.DATEN,
                                         zeit=tag(1))
        self.assertTrue(neu)
        pid2, neu2 = publikum.post_anlegen(self.con, art="entwurf", ziel_id=41, plattform="tiktok",
                                           daten={**self.DATEN, "dauer_s": 99.0}, zeit=tag(2))
        self.assertEqual((pid2, neu2), (pid, False))
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM posts").fetchone()[0], 1)
        zeile = publikum.post(self.con, pid)
        self.assertEqual(zeile["gepostet_utc"], iso(tag(1)))
        self.assertEqual(zeile["dauer_s"], 22.0)
        self.assertEqual((zeile["art"], zeile["ziel"], zeile["entwurf_id"], zeile["clip_id"]),
                         ("entwurf", "entwurf:41", 41, None))
        self.assertIsNone(zeile["url"])
        self.assertEqual(json.loads(zeile["rezept"]), self.DATEN["rezept"])
        self.assertEqual(zeile["experiment"], 0)

    def test_clip_post_setzt_clip_id(self):
        pid, _ = publikum.post_anlegen(self.con, art="clip", ziel_id=5, plattform="tiktok", daten=self.DATEN,
                                       zeit=tag(1))
        zeile = publikum.post(self.con, pid)
        self.assertEqual((zeile["clip_id"], zeile["entwurf_id"], zeile["ziel"]), (5, None, "clip:5"))

    def test_experiment_aus_dem_rezept(self):
        daten = {**self.DATEN, "rezept": {**self.DATEN["rezept"], "experiment": True}}
        pid, _ = publikum.post_anlegen(self.con, art="clip", ziel_id=5, plattform="tiktok", daten=daten)
        self.assertEqual(publikum.post(self.con, pid)["experiment"], 1)

    def test_ohne_zeit_gilt_jetzt(self):
        with mock.patch.object(publikum, "jetzt", return_value=tag(3)):
            pid, _ = publikum.post_anlegen(self.con, art="clip", ziel_id=5, plattform="tiktok", daten=self.DATEN)
        self.assertEqual(publikum.post(self.con, pid)["gepostet_utc"], iso(tag(3)))

    def test_unbekannte_plattform_art_oder_dauer(self):
        with self.assertRaises(ValueError):
            publikum.post_anlegen(self.con, art="clip", ziel_id=5, plattform="clipbattle", daten=self.DATEN)
        with self.assertRaises(ValueError):
            publikum.post_anlegen(self.con, art="video", ziel_id=5, plattform="tiktok", daten=self.DATEN)
        with self.assertRaises(ValueError):
            publikum.post_anlegen(self.con, art="clip", ziel_id=5, plattform="tiktok",
                                  daten={**self.DATEN, "dauer_s": 0})
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM posts").fetchone()[0], 0)

    def test_post_zu(self):
        pid, _ = publikum.post_anlegen(self.con, art="entwurf", ziel_id=41, plattform="tiktok", daten=self.DATEN)
        self.assertEqual(publikum.post_zu(self.con, "entwurf", 41, "tiktok")["id"], pid)
        self.assertIsNone(publikum.post_zu(self.con, "entwurf", 41, "youtube"))
        self.assertIsNone(publikum.post_zu(self.con, "clip", 41, "tiktok"))
        self.assertIsNone(publikum.post(self.con, pid + 1))

    def test_link_nachtragen_und_ersetzen(self):
        pid, _ = publikum.post_anlegen(self.con, art="entwurf", ziel_id=41, plattform="tiktok", daten=self.DATEN)
        publikum.link_nachtragen(self.con, pid, "https://www.tiktok.com/@baddieday/video/111")
        publikum.link_nachtragen(self.con, pid, TIKTOK_LINK)  # Tippfehler korrigiert
        zeile = publikum.post(self.con, pid)
        self.assertEqual((zeile["url"], zeile["video_id"]), (TIKTOK_LINK, "7300123456789012345"))
        # Kurzlink ohne ablesbare ID: url ersetzt, video_id bleibt
        publikum.link_nachtragen(self.con, pid, "https://vm.tiktok.com/ZMabc123/")
        zeile = publikum.post(self.con, pid)
        self.assertEqual((zeile["url"], zeile["video_id"]), ("https://vm.tiktok.com/ZMabc123/",
                                                             "7300123456789012345"))

    def test_link_fuer_unbekannten_post(self):
        with self.assertRaises(ValueError):
            publikum.link_nachtragen(self.con, 999, TIKTOK_LINK)

    def test_video_id_aus_url(self):
        self.assertEqual(publikum.video_id_aus_url(TIKTOK_LINK), "7300123456789012345")
        self.assertEqual(publikum.video_id_aus_url("https://tiktok.com/@x.y_z/video/42?is_from_webapp=1"), "42")
        self.assertEqual(publikum.video_id_aus_url("https://m.tiktok.com/@x/video/43/"), "43")
        self.assertIsNone(publikum.video_id_aus_url("https://vm.tiktok.com/ZMabc123/"))
        self.assertIsNone(publikum.video_id_aus_url("https://youtube.com/shorts/abcDEF12345"))
        self.assertIsNone(publikum.video_id_aus_url("https://evil.example/@x/video/44"))
        self.assertIsNone(publikum.video_id_aus_url("kein link"))

    def test_posts_ohne_messung(self):
        alt = self.post(gepostet=tag(0))
        gemessen = self.post(gepostet=tag(1))
        frisch = [self.post(gepostet=tag(2 + i / 10)) for i in range(5)]
        self.messung(gemessen, tag(2.5), views=10)
        self.messung(alt, tag(1), views=10)   # älter als 24 h vor „jetzt“ → zählt als ohne Messung
        zeilen = publikum.posts_ohne_messung(self.con, zeit=tag(3))
        self.assertEqual([z["id"] for z in zeilen], frisch[::-1])        # neueste zuerst, höchstens 5
        zeilen = publikum.posts_ohne_messung(self.con, grenze=10, zeit=tag(3))
        self.assertEqual([z["id"] for z in zeilen], [*frisch[::-1], alt])
        self.assertNotIn(gemessen, [z["id"] for z in zeilen])


class PostPlattformen(MitPublikum):
    def test_standard_und_zuschalten(self):
        self.assertEqual(publikum.post_plattformen(self.konfig), ["tiktok"])
        self.konfig.daten["publikum"]["plattformen"] = ["tiktok", "youtube"]
        self.assertEqual(publikum.post_plattformen(self.konfig), ["tiktok", "youtube"])
        self.konfig.daten["publikum"]["plattformen"] = []
        self.assertEqual(publikum.post_plattformen(self.konfig), [])

    def test_clipbattle_ignoriert_mit_warnung(self):
        self.konfig.daten["publikum"]["plattformen"] = ["clipbattle", "tiktok"]
        with self.assertLogs("pipeline", level="WARNING") as logs:
            self.assertEqual(publikum.post_plattformen(self.konfig), ["tiktok"])
        self.assertIn("clipbattle", "\n".join(logs.output))


# --- Messungen -----------------------------------------------------------------------------------------

class Plausibel(MitPublikum):
    LETZTE = {"views": 1240, "likes": 61, "kommentare": 3, "shares": 2, "saves": 1}

    def test_alles_gut(self):
        werte = {"views": 1300, "likes": 61, "kommentare": 3, "shares": 5, "saves": 1, "wiedergabe_s": 30.0,
                 "voll_prozent": 100.0}
        self.assertEqual(publikum.pruefe_plausibel(werte, self.LETZTE, 20.0), [])

    def test_sinkende_zaehler(self):
        fehler = publikum.pruefe_plausibel({"views": 900, "likes": 60}, self.LETZTE, 20.0)
        # \u202f: schmales geschütztes Leerzeichen als Tausendertrenner (publikum.TAUSENDER) – wie in jeder Anzeige
        self.assertEqual(fehler, ["Views gesunken: 1\u202f240 → 900", "Likes gesunken: 61 → 60"])

    def test_jeder_zaehler_darf_nicht_sinken(self):
        """Spec §7.1: keiner der fünf Zähler darf gegenüber der letzten Messung sinken. Die Felder stehen hier
        ausgeschrieben (nicht über publikum.ZAEHLER) – sonst schrumpfte der Test still mit, wenn jemand die Liste
        kürzt."""
        for feld, name in (("views", "Views"), ("likes", "Likes"), ("kommentare", "Kommentare"),
                           ("shares", "Shares"), ("saves", "Saves")):
            with self.subTest(feld=feld):
                self.assertEqual(publikum.pruefe_plausibel({feld: 2}, {feld: 3}, 20.0),
                                 [f"{name} gesunken: 3 → 2"])

    def test_zahlen_in_derselben_schreibweise_wie_die_anzeige(self):
        # Die Rückfrage zeigt Zahlen und Verstöße in EINER Nachricht – beide mit anzahl_text/dezimal_text
        fehler = publikum.pruefe_plausibel({"views": 1240, "wiedergabe_s": 6.96}, {"views": 1500}, 4.0)
        self.assertEqual(fehler, ["Views gesunken: 1\u202f500 → 1\u202f240",
                                  "Ø Wiedergabe 7 s ist länger als 1,5 × Videolänge (4 s)"])

    def test_wiedergabe_zu_lang(self):
        fehler = publikum.pruefe_plausibel({"wiedergabe_s": 30.5}, None, 20.0)
        self.assertEqual(len(fehler), 1)
        self.assertIn("Wiedergabe", fehler[0])

    def test_voll_prozent_ueber_100(self):
        fehler = publikum.pruefe_plausibel({"voll_prozent": 120.0}, None, 20.0)
        self.assertEqual(len(fehler), 1)
        self.assertIn("120", fehler[0])

    def test_none_wird_nicht_geprueft(self):
        werte = {"views": None, "likes": None, "wiedergabe_s": None, "voll_prozent": None}
        self.assertEqual(publikum.pruefe_plausibel(werte, self.LETZTE, 20.0), [])
        letzte = {"views": None, "likes": 61}
        self.assertEqual(publikum.pruefe_plausibel({"views": 5}, letzte, 20.0), [])

    def test_mit_sqlite_zeile(self):
        p = self.post()
        self.messung(p, tag(3), views=1240, likes=61)
        letzte = publikum.letzte_messung(self.con, p)
        self.assertEqual(publikum.pruefe_plausibel({"views": 900}, letzte, 20.0),
                         ["Views gesunken: 1\u202f240 → 900"])


class Messungen(MitPublikum):
    def test_speichern_und_letzte(self):
        p = self.post()
        self.assertIsNone(publikum.letzte_messung(self.con, p))
        erste = publikum.speichere_messung(self.con, p, {"views": 10}, "screenshot", roh='{"views": 10}',
                                           zeit=tag(3))
        zweite = publikum.speichere_messung(self.con, p, {"views": 20, "wiedergabe_s": 6.5}, "hand", zeit=tag(4))
        self.assertNotEqual(erste, zweite)
        letzte = publikum.letzte_messung(self.con, p)
        self.assertEqual((letzte["id"], letzte["views"], letzte["wiedergabe_s"], letzte["quelle"]),
                         (zweite, 20, 6.5, "hand"))
        self.assertIsNone(letzte["likes"])
        erste_zeile = self.con.execute("SELECT * FROM publikum_messungen WHERE id = ?", (erste,)).fetchone()
        self.assertEqual((erste_zeile["roh"], erste_zeile["gemessen_utc"]), ('{"views": 10}', iso(tag(3))))

    def test_unbekannte_quelle_oder_post(self):
        p = self.post()
        with self.assertRaises(ValueError):
            publikum.speichere_messung(self.con, p, {"views": 10}, "geraten")
        with self.assertRaises(ValueError):
            publikum.speichere_messung(self.con, p + 1, {"views": 10}, "hand")


class HandEingabe(MitPublikum):
    def test_vier_zahlen(self):
        self.assertEqual(publikum.lies_hand_eingabe("1240 61 6.8 34"),
                         {"views": 1240, "likes": 61, "kommentare": None, "shares": None, "saves": None,
                          "wiedergabe_s": 6.8, "voll_prozent": 34.0})

    def test_strich_und_komma(self):
        werte = publikum.lies_hand_eingabe("  1240   61 – 34,5 ")
        self.assertEqual((werte["wiedergabe_s"], werte["voll_prozent"]), (None, 34.5))
        self.assertEqual(publikum.lies_hand_eingabe("1240 - 6,8 -")["likes"], None)
        self.assertIsInstance(publikum.lies_hand_eingabe("1240 61 6,8 34")["views"], int)

    def test_fehler_mit_text(self):
        for text, stichwort in (("1240 61 6.8", "vier"), ("1240 61 6.8 34 5", "vier"), ("", "vier"),
                                ("1240 viele 6.8 34", "viele"), ("1240 -61 6.8 34", "negativ"),
                                ("12.5 61 6.8 34", "ganze"), ("1240 61 nan 34", "nan"),
                                ("1240 61 inf 34", "inf")):
            with self.subTest(text=text):
                with self.assertRaises(ValueError) as fehler:
                    publikum.lies_hand_eingabe(text)
                self.assertIn(stichwort, str(fehler.exception).lower())

    def test_wiedergabe_als_uhrzeit_nennt_die_einheit(self):
        # Die TikTok-App zeigt „0:07“ – die Hand-Eingabe braucht Sekunden und sagt das auch
        for text in ("1240 61 0:07 34", "1240 61 7s 34"):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, "in Sekunden"):
                publikum.lies_hand_eingabe(text)

    def test_prozentzeichen_ist_erlaubt(self):
        # Die Form nennt „in %“ – wer „34%“ tippt, meint 34
        self.assertEqual(publikum.lies_hand_eingabe("1240 61 6,8 34%")["voll_prozent"], 34.0)

    def test_falsche_anzahl_nennt_die_form_mit_einheiten(self):
        with self.assertRaises(ValueError) as fehler:
            publikum.lies_hand_eingabe("1240 61")
        self.assertIn(publikum.HAND_FORM, str(fehler.exception))
        self.assertIn("Sekunden", publikum.HAND_FORM)


class Anzeige(MitPublikum):
    """Zahlen in Bot-Texten: eine Schreibweise für alle Meldungen (Rückfrage, Bestätigung, /publikum)."""

    def test_anzahl_text(self):
        self.assertEqual(publikum.anzahl_text(1240), "1\u202f240")
        self.assertEqual(publikum.anzahl_text(1234567), "1\u202f234\u202f567")
        self.assertEqual(publikum.anzahl_text(5), "5")
        self.assertEqual(publikum.TAUSENDER, "\u202f")

    def test_dezimal_text_rundet_erst_dann_faellt_komma_null_weg(self):
        for wert, text in ((6.96, "7"), (7.0, "7"), (6.8, "6,8"), (34.5, "34,5"), (0.04, "0"), (20, "20")):
            with self.subTest(wert=wert):
                self.assertEqual(publikum.dezimal_text(wert), text)

    def test_symbole_fuer_jedes_feld_und_kein_knopf_zeichen(self):
        self.assertEqual(set(publikum.SYMBOLE), set(publikum.FELDER))
        self.assertNotIn("✅", publikum.SYMBOLE.values())  # ✅ ist im Bot der Knopf „Stimmt“ bzw. „erledigt“

    def test_art_namen(self):
        self.assertEqual(publikum.ART_NAMEN, {"clip": "Clip", "entwurf": "Entwurf"})
        self.assertEqual(set(publikum.ART_NAMEN), set(publikum.ARTEN))


class Einstellung(MitPublikum):
    def test_fehlender_schluessel_ist_konfigfehler(self):
        from clip_pipeline.konfig import KonfigFehler

        self.assertEqual(publikum.einstellung(self.konfig, "fenster"), 20)
        del self.konfig.daten["publikum"]["fenster"]
        with self.assertRaisesRegex(KonfigFehler, r"\[publikum\]\.fenster"):
            publikum.einstellung(self.konfig, "fenster")

    def test_alter_in_tagen(self):
        gepostet = iso(datetime(2026, 9, 18, 10, 0, tzinfo=UTC))
        self.assertAlmostEqual(publikum.alter_in_tagen(gepostet, datetime(2026, 9, 25, 12, 0, tzinfo=UTC)),
                               7 + 2 / 24)
        self.assertAlmostEqual(publikum.alter_in_tagen(gepostet, iso(datetime(2026, 9, 19, 10, 0, tzinfo=UTC))),
                               1.0)


# --- Rezept und Post-Daten -----------------------------------------------------------------------------

class Rezept(MitPublikum):
    def test_laenge_stufe(self):
        for dauer, stufe in ((18.0, "kurz"), (22.5, "kurz"), (22.6, "mittel"), (31.0, "mittel"), (36.0, "mittel"),
                             (36.1, "lang"), (52.0, "lang")):
            with self.subTest(dauer=dauer):
                self.assertEqual(publikum.laenge_stufe(dauer), stufe)

    def test_rezept_fuer_clip(self):
        self.assertEqual(publikum.rezept_fuer_clip(22.0),
                         {"hook": "stark_zuerst", "laenge": "kurz", "tempo": "none", "machart": "roh",
                          "experiment": False})

    def test_rezept_fuer_entwurf_abgeleitet(self):
        for beats, tempo in ((1, "beat1"), (2, "beat2"), (4, "beat2"), (None, "beat1")):
            with self.subTest(beats=beats):
                zeile, liste = self.entwurf(beats=beats, dauer_s=30.0)
                self.assertEqual(publikum.rezept_fuer_entwurf(zeile, liste),
                                 {"hook": "aufbau", "laenge": "mittel", "tempo": tempo, "machart": "regie",
                                  "experiment": False})

    def test_vorhandenes_rezept_gewinnt(self):
        eigenes = {"hook": "kalt", "laenge": "lang", "tempo": "beat2", "machart": "regie", "experiment": True}
        zeile, liste = self.entwurf(rezept=json.dumps(eigenes), dauer_s=18.0)
        self.assertEqual(publikum.rezept_fuer_entwurf(zeile, liste), eigenes)


class PostDaten(MitPublikum):
    def test_clip_post_daten_ohne_dateizugriff(self):
        merkmale = {"kill_punkte": 6.0, "victory_royale": 1.0, "laenge": 0.2, "lautstaerke": 0.0, "kommentar": 0.0}
        cid = self.clip_anlegen(merkmale=merkmale)
        # Pfade absichtlich ungültig: die Funktion darf keine Datei anfassen (hängender NFS im Bot)
        self.con.execute("UPDATE clips SET quelle_pfad = '/gibt/es/nicht.mp4', clip_pfad = '/gibt/es/nicht',"
                         " short_pfad = '/gibt/es/nicht' WHERE id = ?", (cid,))
        with mock.patch.object(Path, "exists", side_effect=AssertionError("Dateizugriff")), \
                mock.patch("builtins.open", side_effect=AssertionError("Dateizugriff")):
            daten = publikum.clip_post_daten(self.con, self.konfig, cid)
        self.assertEqual(daten["dauer_s"], 22.0)  # Clip 0–20 s + Endcard 2,5 s − Überblendung 0,5 s
        self.assertEqual(daten["dauer_s"], shorts.gesamtdauer(20.0, self.konfig))
        self.assertEqual(daten["rezept"], publikum.rezept_fuer_clip(22.0))
        self.assertEqual(daten["merkmale"], {"momente": [{"moment": f"clip:{cid}", "clip_id": cid,
                                                          "merkmale": merkmale}],
                                             "hook_moment": f"clip:{cid}"})

    def test_ohne_endcard_nur_der_clip(self):
        self.konfig.daten["shorts"]["endcard"] = False
        cid = self.clip_anlegen()
        self.assertEqual(publikum.clip_post_daten(self.con, self.konfig, cid)["dauer_s"], 20.0)

    def test_clip_fehlt(self):
        with self.assertRaises(KeyError):
            publikum.clip_post_daten(self.con, self.konfig, 999)

    def test_entwurf_post_daten_aus_schnittliste(self):
        c1 = self.clip_anlegen(merkmale={"kill_punkte": 3.0})
        c2 = self.clip_anlegen(merkmale={"kill_punkte": 1.0})
        self.con.execute(
            "INSERT INTO momente (schluessel, datei, start_s, ende_s, stimmung, sicherheit, quelle, merkmale,"
            " erstellt, geaendert) VALUES ('datei:x.mp4', '/x.mp4', 0, 5, 'lustig', 0.5, 'regel', ?, ?, ?)",
            (json.dumps({"mic_lachen": 2}), iso(T0), iso(T0)))
        segmente = [{"nr": 1, "moment": f"clip:{c2}", "clip_id": c2, "stimmung": "episch"},
                    {"nr": 2, "moment": f"clip:{c2}", "clip_id": c2, "stimmung": "episch"},   # Jump-Cut
                    {"nr": 3, "moment": "datei:x.mp4", "clip_id": None, "stimmung": "lustig"},
                    {"nr": 4, "moment": f"clip:{c1}", "clip_id": c1, "stimmung": "episch"}]
        musik = {"track_id": 3, "datei": "a.mp3", "titel": "Song", "kuenstler": "K", "quelle": "NCS: Song (CC)"}
        zeile, _ = self.entwurf(dauer_s=38.5, beats=2, segmente=segmente, musik=musik)
        daten = publikum.entwurf_post_daten(self.con, self.konfig, zeile["id"])
        self.assertEqual(daten["dauer_s"], 38.5)
        self.assertEqual(daten["rezept"], {"hook": "aufbau", "laenge": "lang", "tempo": "beat2", "machart": "regie",
                                           "experiment": False})
        m = daten["merkmale"]
        self.assertEqual([x["moment"] for x in m["momente"]], [f"clip:{c2}", "datei:x.mp4", f"clip:{c1}"])
        self.assertEqual(m["momente"][0]["merkmale"], {"kill_punkte": 1.0})
        self.assertEqual(m["momente"][1]["merkmale"], {"mic_lachen": 2})
        self.assertIsNone(m["momente"][1]["clip_id"])
        self.assertEqual(m["hook_moment"], f"clip:{c2}")
        self.assertEqual(m["stimmung"], "episch")
        self.assertEqual(m["musik"], {"titel": "Song", "quelle": "NCS: Song (CC)"})
        json.dumps(daten)  # muss speicherbar sein

    def test_entwurf_ohne_musik_und_segmente(self):
        zeile, _ = self.entwurf(dauer_s=15.0, segmente=[])
        m = publikum.entwurf_post_daten(self.con, self.konfig, zeile["id"])["merkmale"]
        self.assertEqual((m["momente"], m["hook_moment"], m["musik"]), ([], None, None))

    def test_entwurf_fehlt(self):
        with self.assertRaises(KeyError):
            publikum.entwurf_post_daten(self.con, self.konfig, 999)


# --- Migration und „nie wecken“ ------------------------------------------------------------------------

class Migration(MitSpeicher):
    def test_alte_datenbank_bekommt_tabellen_und_spalten(self):
        from importlib import resources
        pfad = self.tmp / "alt.db"
        alt = sqlite3.connect(pfad)
        for datei in ("schema.sql", "regie.sql", "lager.sql"):  # Stand vor der Lernschleife (ohne publikum.sql)
            alt.executescript(resources.files("clip_pipeline").joinpath(datei).read_text(encoding="utf-8"))
        alt.execute("INSERT INTO matches (id, replay_pfad, start_utc, ende_utc, erstellt, geaendert)"
                    " VALUES ('m1', 'r/m1.replay', 'a', 'b', 'c', 'd')")
        alt.execute("INSERT INTO clips (match_id, nr, titel, typ, kills, max_gruppe, kill_zeiten, start_utc, ende_utc,"
                    " quelle_pfad, quelle_start_s, quelle_ende_s, merkmale, punkte, begruendung, erstellt, geaendert)"
                    " VALUES ('m1', 1, 'T', 'einzel', 1, 1, '[]', 'a', 'b', 'x.mp4', 0, 20, '{}', 1, 'B', 'c', 'd')")
        alt.execute("INSERT INTO entwuerfe (name, format, schnittliste, parameter, erstellt)"
                    " VALUES ('short-1', 'short', '/x.json', '{}', 'c')")
        alt.commit()
        self.assertNotIn("rezept", {z[1] for z in alt.execute("PRAGMA table_info(entwuerfe)")})
        alt.close()

        for _ in range(2):  # idempotent
            con = db.verbinde(pfad)
            tabellen = {z["name"] for z in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            self.assertLessEqual({"posts", "publikum_messungen", "rezept_stand", "hypothesen", "erwartungen"},
                                 tabellen)
            self.assertIn("mic_stand", {z["name"] for z in con.execute("PRAGMA table_info(clips)")})
            spalten = {z["name"] for z in con.execute("PRAGMA table_info(entwuerfe)")}
            self.assertLessEqual({"rezept", "upload_pfad"}, spalten)
            self.assertEqual(con.execute("SELECT titel FROM clips").fetchone()[0], "T")
            self.assertEqual(con.execute("SELECT name FROM entwuerfe").fetchone()[0], "short-1")
            self.assertEqual(con.execute("SELECT COUNT(*) FROM posts").fetchone()[0], 0)
            with self.assertRaises(sqlite3.IntegrityError):  # Fremdschlüssel Messung → Post ist aktiv
                con.execute("INSERT INTO publikum_messungen (post_id, gemessen_utc, quelle, erstellt)"
                            " VALUES (999, 'a', 'hand', 'b')")
            con.close()
        # Die neuen Funktionen laufen auf der migrierten Datenbank (mit dem alten Clip)
        con = db.verbinde(pfad)
        daten = publikum.clip_post_daten(con, self.konfig, 1)
        post_id, neu = publikum.post_anlegen(con, art="clip", ziel_id=1, plattform="tiktok", daten=daten, zeit=T0)
        self.assertTrue(neu)
        self.assertEqual(publikum.post_zu(con, "clip", 1, "tiktok")["id"], post_id)
        con.close()


class NieWecken(MitPublikum):
    def test_kein_wecken_kein_netz(self):
        self.konfig.daten["speicher"].update(host="pve-gross", wol_mac="aa:bb:cc:dd:ee:ff")
        cid = self.clip_anlegen()
        zeile, _ = self.entwurf(dauer_s=20.0)
        p = self.post(gepostet=tag(0))
        self.messung(p, tag(7), views=100, likes=5)
        with mock.patch.object(konfig.Konfig, "_host_erreichbar", return_value=False), \
                mock.patch("clip_pipeline.konfig.sende_wake_on_lan") as wol, \
                mock.patch.object(big, "wach_halten") as wach, \
                mock.patch("socket.create_connection", side_effect=AssertionError("Netzwerkzugriff")):
            publikum.clip_post_daten(self.con, self.konfig, cid)
            publikum.entwurf_post_daten(self.con, self.konfig, zeile["id"])
            ergebnis = publikum.bewerte_alle(self.con, self.konfig, tag(8))
            publikum.posts_ohne_messung(self.con, zeit=tag(8))
        self.assertEqual(ergebnis["bewertet"], 1)
        wol.assert_not_called()
        wach.assert_not_called()


if __name__ == "__main__":
    import unittest
    unittest.main()
