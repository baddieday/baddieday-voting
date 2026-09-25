"""Paket E der Stufe 2: Erwartung beim Senden festschreiben, Trefferquote (Spec §10.5, Plan Paket E).

Geprüft wird:
  - moment_score: Clip = roh_score(fuer_moment(clips.merkmale, momente.merkmale)), Entwurf = Mittel über die
    eindeutigen Momente der Schnittliste, aus den momente-Zeilen neu gerechnet (nicht die gespeicherte intensitaet)
  - modell: erst ab [erwartung].mindest_urteile, Start und Schritte aus [erwartung], L2 auf b, deterministisch
  - festschreiben: rechnet nur ohne vorhandene Zeile, gibt den gespeicherten Wert zurück; unter der Mindestzahl
    keine Zeile; gespeichert liest nur
  - Urteile: Clip positiv = Status in db.BEWERTET, negativ = verworfen; Entwurf positiv = daumen > 0,
    Zusammenschnitte zählen mit (Annahme S2-A14)
  - trefferquote: letzte 20 nach erwartungen.erstellt (S2-A15) und alle, getrennt je Art; trefferquote_text
  - z gegen die letzten [erwartung].referenz gesendeten derselben Art (nach id, Annahme S2-A13)
  - `pipeline lernstand` zeigt die Quote als Zusatz; nichts weckt pve-big
"""

from __future__ import annotations

import contextlib
import io
import json
import math
import shutil
import sqlite3
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from clip_pipeline import cli, db, erwartung, konfig as konfig_modul, merkmale
from clip_pipeline.vorbewertung import MERKMALE, roh_score
from clip_pipeline.zeit import UTC, iso

from tests.hilfen import MitSpeicher

# Gewichte fest im Test (alle 17 gesetzt): Die Tests hängen weder an pipeline.toml noch daran, ob
# lernen.aktuelle fehlende Startgewichte auffüllt (Paket D).
GEWICHTE = {"kill_punkte": 1.0, "victory_royale": 5.0, "laenge": -0.5, "lautstaerke": 1.0, "kommentar": 1.0,
            "platzierung": 2.0, "sniper": 1.0, "nahkampf": 0.0, "bot_opfer": -2.0, "phase": 0.5, "endgame": 1.0,
            "clutch": 2.0, "mic_lachen": 1.0, "mic_jubel": 1.0, "mic_frust": 0.5, "mic_laut": 0.5, "spitzen": 0.25}
KILL_TABELLE = [0.0, 1.0, 3.0, 6.0, 10.0]
ERWARTUNG = {"mindest_urteile": 10, "referenz": 50, "schritte": 50, "lernrate": 0.1, "l2": 0.1,
             "start_a": 1.0, "start_b": 0.5, "start_c": 0.0, "mad_minimum": 0.25}
UHR = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


class MitErwartung(MitSpeicher):
    """Speicher + feste Gewichte (Version 3) + feste [erwartung]-Werte + Hilfen zum Anlegen von Urteilen."""

    def setUp(self):
        super().setUp()
        assert set(GEWICHTE) == set(MERKMALE)
        self.konfig.daten["erwartung"] = dict(ERWARTUNG)
        self.konfig.daten["vorbewertung"]["kill_punkte"] = list(KILL_TABELLE)
        self.con.execute(
            "INSERT INTO gewichte (version, werte, datenbasis, vertrauen, erstellt) VALUES (3, ?, 0, 0, ?)",
            (json.dumps(GEWICHTE), iso(UHR)))
        uhr = mock.patch.object(erwartung, "jetzt", return_value=UHR)
        uhr.start()
        self.addCleanup(uhr.stop)

    # --- Clips ---
    def clip(self, status: str, kill_punkte: float, *, gesendet: bool = True) -> int:
        cid = self.clip_anlegen(status=status, merkmale={"kill_punkte": kill_punkte, "laenge": 0.0})
        if gesendet:
            self.con.execute("UPDATE clips SET tg_nachricht_id = ? WHERE id = ?", (1000 + cid, cid))
        return cid

    def urteile_clips(self, gut: int = 5, schlecht: int = 5) -> None:
        """Gute Clips haben hohe Scores (6, 10), schlechte niedrige (1) – das Modell lernt a > 0."""
        for i in range(gut):
            self.clip("freigegeben", 6.0 if i % 2 else 10.0)
        for _ in range(schlecht):
            self.clip("verworfen", 1.0)

    # --- Entwürfe ---
    def moment(self, schluessel: str, mk: dict, clip_id: int | None = None) -> None:
        self.con.execute(
            """INSERT INTO momente (schluessel, clip_id, datei, start_s, ende_s, kills, stimmung, sicherheit, quelle,
                                    merkmale, erstellt, geaendert)
               VALUES (?, ?, '/x.mp4', 0, 10, 0, 'episch', 0.8, 'regel', ?, ?, ?)""",
            (schluessel, clip_id, json.dumps(mk), iso(UHR), iso(UHR)))

    def entwurf(self, momente: list[str], *, status: str = "gesendet", daumen: int | None = None,
                fmt: str = "short", intensitaet: float = 999.0) -> int:
        nr = self.con.execute("SELECT COUNT(*) FROM entwuerfe").fetchone()[0] + 1
        liste = self.tmp / "regie" / f"e{nr}.json"
        liste.parent.mkdir(parents=True, exist_ok=True)
        segmente = [{"moment": m, "intensitaet": intensitaet} for m in momente]
        liste.write_text(json.dumps({"format": fmt, "segmente": segmente}), encoding="utf-8")
        eid = self.con.execute(
            "INSERT INTO entwuerfe (name, format, schnittliste, parameter, status, tg_nachricht_id, erstellt)"
            " VALUES (?, ?, ?, '{}', ?, ?, ?)",
            (f"e{nr}", fmt, str(liste), status, 500 + nr if status in ("gesendet", "bewertet") else None,
             iso(UHR))).lastrowid
        if daumen is not None:
            self.con.execute("INSERT INTO entwurf_bewertungen (entwurf_id, daumen, erstellt, geaendert)"
                             " VALUES (?, ?, ?, ?)", (eid, daumen, iso(UHR), iso(UHR)))
            self.con.execute("UPDATE entwuerfe SET status = 'bewertet' WHERE id = ?", (eid,))
        return int(eid)

    def zeile(self, art: str, ziel_id: int) -> sqlite3.Row | None:
        return self.con.execute("SELECT * FROM erwartungen WHERE art = ? AND ziel_id = ?", (art, ziel_id)).fetchone()


class MomentScore(MitErwartung):
    def test_clip_aus_clip_und_momente_merkmalen(self):
        cid = self.clip("vorbewertet", 3.0, gesendet=False)
        self.con.execute("UPDATE clips SET merkmale = ? WHERE id = ?",
                         (json.dumps({"kill_punkte": 3.0, "laenge": 1.0, "mic_lachen": 0.0}), cid))
        # Mic-Werte aus momente gewinnen (jünger): lachen 2 → mic_lachen 2
        self.moment(f"clip:{cid}", {"mikro_spur": 1, "lachen": 2, "spitzen_s": [1.0]}, cid)
        # 3·1 + 1·(−0,5) + 2·1 = 4,5
        self.assertEqual(erwartung.moment_score(self.con, self.konfig, "clip", cid, GEWICHTE), 4.5)
        erwartet = roh_score(merkmale.fuer_moment({"kill_punkte": 3.0, "laenge": 1.0, "mic_lachen": 0.0},
                                                  {"mikro_spur": 1, "lachen": 2}, KILL_TABELLE), GEWICHTE)
        self.assertEqual(erwartung.moment_score(self.con, self.konfig, "clip", cid, GEWICHTE), erwartet)

    def test_clip_ohne_moment_zeile(self):
        cid = self.clip("vorbewertet", 6.0, gesendet=False)
        self.assertEqual(erwartung.moment_score(self.con, self.konfig, "clip", cid, GEWICHTE), 6.0)

    def test_unbekannt_und_falsche_art(self):
        self.assertIsNone(erwartung.moment_score(self.con, self.konfig, "clip", 999, GEWICHTE))
        self.assertIsNone(erwartung.moment_score(self.con, self.konfig, "entwurf", 999, GEWICHTE))
        with self.assertRaises(ValueError):
            erwartung.moment_score(self.con, self.konfig, "post", 1, GEWICHTE)

    def test_entwurf_mittel_ueber_eindeutige_momente_neu_gerechnet(self):
        cid = self.clip("gesendet", 6.0)
        self.moment(f"clip:{cid}", {"spitzen": 2}, cid)          # 6 + 2·0,25 = 6,5
        self.moment("datei:1", {"max_gruppe": 2, "lachen": 1})   # Double 3 + Lachen 1 = 4,0
        # clip:… kommt dreimal vor (Jump-Cut-Teile und Hook), zählt aber einmal; unbekannter Moment fällt weg.
        # Die gespeicherte intensitaet (999) spielt keine Rolle.
        eid = self.entwurf([f"clip:{cid}", f"clip:{cid}", "datei:1", f"clip:{cid}", "datei:gibtsnicht"])
        self.assertEqual(erwartung.moment_score(self.con, self.konfig, "entwurf", eid, GEWICHTE), (6.5 + 4.0) / 2)

    def test_entwurf_ohne_lesbare_schnittliste_oder_momente(self):
        eid = self.entwurf(["datei:gibtsnicht"])
        self.assertIsNone(erwartung.moment_score(self.con, self.konfig, "entwurf", eid, GEWICHTE))
        self.moment("datei:1", {"max_gruppe": 1})
        eid = self.entwurf(["datei:1"])
        Path(self.con.execute("SELECT schnittliste FROM entwuerfe WHERE id = ?", (eid,)).fetchone()[0]).unlink()
        self.assertIsNone(erwartung.moment_score(self.con, self.konfig, "entwurf", eid, GEWICHTE))


class Modell(MitErwartung):
    def test_unter_mindest_urteilen_kein_modell(self):
        self.urteile_clips(gut=5, schlecht=4)
        self.clip("gesendet", 3.0)       # gesendet, aber noch ohne Urteil – zählt nicht
        self.clip("vorbewertet", 3.0, gesendet=False)
        self.assertIsNone(erwartung.modell(self.con, self.konfig, "clip"))
        self.clip("verworfen", 1.0)
        m = erwartung.modell(self.con, self.konfig, "clip")
        self.assertEqual(m["n_urteile"], 10)

    def test_bewertet_zaehlt_positiv_verworfen_negativ(self):
        for status in ("freigegeben", "veroeffentlicht", "im_highlight"):
            self.clip(status, 10.0)
        for _ in range(7):
            self.clip("verworfen", 1.0)
        m = erwartung.modell(self.con, self.konfig, "clip")
        self.assertEqual(m["n_urteile"], 10)
        self.assertGreater(m["a"], ERWARTUNG["start_a"])   # hohe Scores → Freigabe: a wächst
        self.assertLess(m["c"], 0)                          # 7 von 10 verworfen: Grundneigung negativ

    def test_start_und_schritte_aus_der_konfig(self):
        self.urteile_clips()
        self.konfig.daten["erwartung"].update(schritte=0, start_a=0.7, start_b=0.2, start_c=-0.3)
        m = erwartung.modell(self.con, self.konfig, "clip")
        self.assertEqual((m["a"], m["b"], m["c"]), (0.7, 0.2, -0.3))

    def test_l2_zieht_b_zur_null(self):
        # rezept ist in Stufe 2 immer 0: b bekommt nur die L2-Strafe, also b·(1 − lernrate·l2) je Schritt
        self.urteile_clips()
        m = erwartung.modell(self.con, self.konfig, "clip")
        self.assertAlmostEqual(m["b"], 0.5 * (1 - 0.1 * 0.1) ** 50, places=12)

    def test_deterministisch(self):
        self.urteile_clips(gut=6, schlecht=6)
        eins = erwartung.modell(self.con, self.konfig, "clip")
        self.assertEqual(eins, erwartung.modell(self.con, self.konfig, "clip"))
        # Kopie der Datenbank → dieselben a, b, c
        kopie = sqlite3.connect(":memory:")
        self.con.backup(kopie)
        kopie.row_factory = sqlite3.Row
        self.assertEqual(eins, erwartung.modell(kopie, self.konfig, "clip"))
        kopie.close()
        self.assertTrue(set(eins) >= {"a", "b", "c", "n_urteile", "median", "mad"})

    def test_entwuerfe_mit_zusammenschnitt(self):
        self.moment("datei:1", {"max_gruppe": 3})   # 6 Punkte
        self.moment("datei:2", {"max_gruppe": 1})   # 1 Punkt
        for i in range(9):
            gut = i % 2 == 0
            self.entwurf(["datei:1" if gut else "datei:2"], daumen=1 if gut else -1)
        self.entwurf(["datei:1"], status="gesendet")  # ohne Urteil
        self.assertIsNone(erwartung.modell(self.con, self.konfig, "entwurf"))
        self.entwurf(["datei:2"], daumen=-1, fmt="zusammenschnitt")  # Zusammenschnitte zählen mit (S2-A14)
        m = erwartung.modell(self.con, self.konfig, "entwurf")
        self.assertEqual(m["n_urteile"], 10)
        self.assertGreater(m["a"], ERWARTUNG["start_a"])

    def test_falsche_art(self):
        with self.assertRaises(ValueError):
            erwartung.modell(self.con, self.konfig, "post")

    def test_fehlender_konfig_wert_ist_eine_klare_meldung(self):
        del self.konfig.daten["erwartung"]["lernrate"]
        self.urteile_clips()
        with self.assertRaises(ValueError) as fehler:
            erwartung.modell(self.con, self.konfig, "clip")
        self.assertIn("erwartung.lernrate", str(fehler.exception))


class Festschreiben(MitErwartung):
    def test_unter_mindest_keine_zeile(self):
        self.urteile_clips(gut=5, schlecht=4)
        neu = self.clip("vorbewertet", 10.0, gesendet=False)
        self.assertIsNone(erwartung.festschreiben(self.con, self.konfig, "clip", neu))
        self.assertIsNone(self.zeile("clip", neu))
        self.assertIsNone(erwartung.gespeichert(self.con, "clip", neu))

    def test_schreibt_fest_und_rechnet_nie_neu(self):
        self.urteile_clips()
        neu = self.clip("vorbewertet", 10.0, gesendet=False)
        p = erwartung.festschreiben(self.con, self.konfig, "clip", neu)
        self.assertGreater(p, 0.5)
        zeile = self.zeile("clip", neu)
        self.assertEqual(zeile["wahrschein"], p)
        self.assertEqual(zeile["erstellt"], iso(UHR))
        grundlage = json.loads(zeile["grundlage"])
        self.assertEqual(set(grundlage), {"score", "z", "a", "b", "c", "rezept", "gewichte_version", "n_urteile",
                                          "median", "mad"})
        self.assertEqual((grundlage["score"], grundlage["rezept"], grundlage["gewichte_version"],
                          grundlage["n_urteile"]), (10.0, 0, 3, 10))
        m = erwartung.modell(self.con, self.konfig, "clip")
        self.assertAlmostEqual(p, 1 / (1 + math.exp(-(m["a"] * grundlage["z"] + m["c"]))), places=12)

        # Viele Urteile gegen das Modell: das Modell ändert sich, die festgeschriebene Erwartung nicht
        for _ in range(15):
            self.clip("verworfen", 10.0)
        self.assertNotEqual(erwartung.modell(self.con, self.konfig, "clip")["a"], grundlage["a"])
        self.assertEqual(erwartung.festschreiben(self.con, self.konfig, "clip", neu), p)
        self.assertEqual(tuple(self.zeile("clip", neu)), tuple(zeile))
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM erwartungen").fetchone()[0], 1)

    def test_vorhandene_zeile_wird_nur_gelesen(self):
        self.urteile_clips()
        neu = self.clip("vorbewertet", 1.0, gesendet=False)
        self.con.execute("INSERT INTO erwartungen (art, ziel_id, wahrschein, grundlage, erstellt)"
                         " VALUES ('clip', ?, 0.42, '{}', 'x')", (neu,))
        with mock.patch.object(erwartung, "modell", side_effect=AssertionError("darf nicht rechnen")), \
                mock.patch.object(erwartung, "_modell", side_effect=AssertionError("darf nicht rechnen")):
            self.assertEqual(erwartung.festschreiben(self.con, self.konfig, "clip", neu), 0.42)
        self.assertEqual(erwartung.gespeichert(self.con, "clip", neu), 0.42)

    def test_niedriger_score_niedrige_erwartung(self):
        self.urteile_clips()
        neu = self.clip("vorbewertet", 1.0, gesendet=False)
        self.assertLess(erwartung.festschreiben(self.con, self.konfig, "clip", neu), 0.5)

    def test_unbekannter_clip_keine_zeile(self):
        self.urteile_clips()
        self.assertIsNone(erwartung.festschreiben(self.con, self.konfig, "clip", 999))
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM erwartungen").fetchone()[0], 0)

    def test_z_gegen_die_letzten_referenz_gesendeten(self):
        self.urteile_clips()                      # Scores 10, 6, 10, 6, 10, 1, 1, 1, 1, 1
        for punkte in (20.0, 30.0, 40.0):         # die drei neuesten gesendeten
            self.clip("gesendet", punkte)
        self.clip("vorbewertet", 50.0, gesendet=False)  # nicht gesendet → nicht in der Basis
        self.konfig.daten["erwartung"]["referenz"] = 3
        neu = self.clip("vorbewertet", 40.0, gesendet=False)
        erwartung.festschreiben(self.con, self.konfig, "clip", neu)
        grundlage = json.loads(self.zeile("clip", neu)["grundlage"])
        self.assertEqual((grundlage["median"], grundlage["mad"]), (30.0, 10.0))
        # z = (40 − 30) / (1,4826 · 10) ≈ 0,67449 – nur mit der Basis 20/30/40 (mit 50 oder alten Clips anders)
        self.assertAlmostEqual(grundlage["z"], 10 / (1.4826 * 10))
        self.assertAlmostEqual(grundlage["z"], 0.67449, places=5)

    def test_mad_minimum_aus_der_konfig(self):
        self.urteile_clips()
        for _ in range(3):
            self.clip("gesendet", 5.0)            # Basis ohne Streuung: MAD 0 → mit dem Minimum gerechnet
        self.konfig.daten["erwartung"].update(referenz=3, mad_minimum=2.0)
        neu = self.clip("vorbewertet", 6.0, gesendet=False)
        erwartung.festschreiben(self.con, self.konfig, "clip", neu)
        grundlage = json.loads(self.zeile("clip", neu)["grundlage"])
        self.assertAlmostEqual(grundlage["z"], 1 / (1.4826 * 2.0))
        self.assertEqual(grundlage["mad"], 0.0)

    def test_entwurf(self):
        self.moment("datei:1", {"max_gruppe": 3})
        self.moment("datei:2", {"max_gruppe": 1})
        for i in range(10):
            self.entwurf(["datei:1" if i % 2 else "datei:2"], daumen=1 if i % 2 else -1)
        neu = self.entwurf(["datei:1"], status="gerendert")
        p = erwartung.festschreiben(self.con, self.konfig, "entwurf", neu)
        self.assertGreater(p, 0.5)
        self.assertEqual(erwartung.gespeichert(self.con, "entwurf", neu), p)
        self.assertIsNone(erwartung.gespeichert(self.con, "clip", neu))  # Art gehört zum Schlüssel

    def test_falsche_art(self):
        with self.assertRaises(ValueError):
            erwartung.festschreiben(self.con, self.konfig, "post", 1)
        with self.assertRaises(ValueError):
            erwartung.gespeichert(self.con, "post", 1)


class Trefferquote(MitErwartung):
    def erwartung_anlegen(self, art: str, ziel_id: int, wahrschein: float, minute: int) -> None:
        self.con.execute("INSERT INTO erwartungen (art, ziel_id, wahrschein, grundlage, erstellt) VALUES (?, ?, ?, '{}', ?)",
                         (art, ziel_id, wahrschein, iso(UHR + timedelta(minutes=minute))))

    def test_clips_letzte_20_und_alle(self):
        # 25 Clips: die ersten 5 (ältesten) daneben, die letzten 20 davon 14 getroffen
        for i in range(25):
            cid = self.clip("freigegeben", 5.0)
            if i < 5:
                wahrschein = 0.2                           # daneben (freigegeben, erwartet nicht)
            else:
                wahrschein = 0.8 if i < 19 else 0.3        # 14 Treffer, 6 daneben
            self.erwartung_anlegen("clip", cid, wahrschein, minute=i)
        # ohne Urteil zählt nicht; 0,5 zählt als „gibst frei“
        offen = self.clip("gesendet", 5.0)
        self.erwartung_anlegen("clip", offen, 0.9, minute=99)
        grenze = self.clip("verworfen", 5.0)
        self.erwartung_anlegen("clip", grenze, 0.5, minute=100)
        self.assertEqual(erwartung.trefferquote(self.con, "clip"), (14, 26))
        self.assertEqual(erwartung.trefferquote(self.con, "clip", letzte=20), (13, 20))
        self.assertEqual(erwartung.trefferquote(self.con, "entwurf"), (0, 0))
        with self.assertRaises(ValueError):
            erwartung.trefferquote(self.con, "clip", letzte=0)
        with self.assertRaises(ValueError):
            erwartung.trefferquote(self.con, "post")

    def test_letzte_nach_erstellt_nicht_nach_id(self):
        alt = self.clip("freigegeben", 5.0)
        neu = self.clip("verworfen", 5.0)
        self.erwartung_anlegen("clip", alt, 0.9, minute=10)   # Treffer, später erstellt
        self.erwartung_anlegen("clip", neu, 0.9, minute=1)    # daneben, früher erstellt
        self.assertEqual(erwartung.trefferquote(self.con, "clip", letzte=1), (1, 1))

    def test_entwuerfe(self):
        self.moment("datei:1", {"max_gruppe": 1})
        gut = self.entwurf(["datei:1"], daumen=1)
        schlecht = self.entwurf(["datei:1"], daumen=-1, fmt="zusammenschnitt")
        offen = self.entwurf(["datei:1"])
        self.erwartung_anlegen("entwurf", gut, 0.7, 1)
        self.erwartung_anlegen("entwurf", schlecht, 0.6, 2)
        self.erwartung_anlegen("entwurf", offen, 0.6, 3)
        self.assertEqual(erwartung.trefferquote(self.con, "entwurf"), (1, 2))
        self.assertEqual(erwartung.trefferquote(self.con, "clip"), (0, 0))

    def test_text(self):
        self.assertEqual(erwartung.trefferquote_text(self.con, self.konfig), "")
        for i in range(45):
            cid = self.clip("freigegeben", 5.0)
            wahrschein = 0.8 if (i < 25 and i % 5 != 0) or (i >= 25 and i < 39) else 0.2
            self.erwartung_anlegen("clip", cid, wahrschein, minute=i)
        text = erwartung.trefferquote_text(self.con, self.konfig)
        self.assertEqual(text, "Erwartung getroffen: Clips 14/20 (70 %) · alle 34/45 (76 %)")
        self.moment("datei:1", {"max_gruppe": 1})
        eid = self.entwurf(["datei:1"], daumen=1)
        self.erwartung_anlegen("entwurf", eid, 0.9, 1)
        self.assertEqual(erwartung.trefferquote_text(self.con, self.konfig).splitlines(),
                         ["Erwartung getroffen: Clips 14/20 (70 %) · alle 34/45 (76 %)",
                          "Erwartung getroffen: Entwürfe 1/1 (100 %)"])


class LernstandCli(MitErwartung):
    def test_pipeline_lernstand_zeigt_die_quote(self):
        cid = self.clip("freigegeben", 5.0)
        self.con.execute("INSERT INTO erwartungen (art, ziel_id, wahrschein, grundlage, erstellt)"
                         " VALUES ('clip', ?, 0.8, '{}', 'x')", (cid,))
        fehler, aus = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(fehler), contextlib.redirect_stdout(aus):
            self.assertEqual(cli._cmd_lernstand(None, self.konfig, self.con), 0)
        self.assertIn("Erwartung getroffen: Clips 1/1 (100 %)", fehler.getvalue())
        self.assertIn("Effekte:", fehler.getvalue())             # der Regie-Lernstand bleibt davor
        self.assertEqual(len(aus.getvalue().strip().splitlines()), 1)  # stdout: weiter genau eine JSON-Zeile
        json.loads(aus.getvalue())

    def test_ohne_quote_kein_zusatz(self):
        fehler = io.StringIO()
        with contextlib.redirect_stderr(fehler), contextlib.redirect_stdout(io.StringIO()):
            cli._cmd_lernstand(None, self.konfig, self.con)
        self.assertNotIn("Erwartung", fehler.getvalue())


class WecktNie(MitErwartung):
    def test_weckt_nie(self):
        self.konfig.daten["speicher"].update(host="pve-gross", wol_mac="aa:bb:cc:dd:ee:ff")
        self.urteile_clips()
        neu = self.clip("vorbewertet", 10.0, gesendet=False)
        with mock.patch.object(konfig_modul.Konfig, "_host_erreichbar", return_value=False) as wach, \
                mock.patch("clip_pipeline.konfig.sende_wake_on_lan") as wol:
            erwartung.festschreiben(self.con, self.konfig, "clip", neu)
            erwartung.modell(self.con, self.konfig, "entwurf")
            erwartung.trefferquote_text(self.con, self.konfig)
        wol.assert_not_called()
        wach.assert_not_called()


if __name__ == "__main__":
    unittest.main()
