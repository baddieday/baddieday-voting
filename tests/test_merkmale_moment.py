"""Paket A1 der Stufe 2: eine Score-Formel, Moment-Merkmale und das Schreiben in clips.merkmale.

Geprüft wird (Plan docs/superpowers/plans/2026-09-25-lernschleife-stufe-2.md, Paket A1):
  - vorbewertung.roh_score ist die eine Summe; bewerte liefert byte-gleich dasselbe wie vor Stufe 2
  - merkmale.aus_momente: Zuordnung, Deckel, „fehlt = unbekannt“, ohne Mikro gemessen = 0
  - merkmale.mic_vollstaendig: die Regel für clips.mic_stand (Annahme S2-A6)
  - merkmale.fuer_moment: Clip-Moment und Datei-Moment (Annahme S2-A9)
  - merkmale.aktualisiere_clip: je Status, idempotent, unbekannte clip_id
"""

from __future__ import annotations

import json
import unittest

from clip_pipeline import db, merkmale, vorbewertung
from clip_pipeline.vorbewertung import MERKMAL_NAMEN, MERKMALE, bewerte, roh_score, zahl

from .hilfen import MitSpeicher

# Startgewichte wie in config/pipeline.toml, fest im Test, damit eine Konfig-Änderung hier nichts verschiebt
GEWICHTE = {"kill_punkte": 1.0, "victory_royale": 5.0, "laenge": -0.5, "lautstaerke": 1.0, "kommentar": 1.0,
            "platzierung": 2.0, "sniper": 1.0, "nahkampf": 0.0, "bot_opfer": -2.0, "phase": 0.5, "endgame": 1.0,
            "clutch": 2.0, "mic_lachen": 1.0, "mic_jubel": 1.0, "mic_frust": 0.5, "mic_laut": 0.5, "spitzen": 0.25}
KILL_TABELLE = [0.0, 1.0, 3.0, 6.0, 10.0]


def bewerte_alt(mk: dict[str, float], gewichte: dict[str, float], titel: str = "Kills") -> tuple[float, str]:
    """Wörtliche Kopie von vorbewertung.bewerte vor Paket A1 – die Referenz für „byte-gleich“."""
    summe = 0.0
    teile: list[str] = []
    for merkmal in MERKMALE:
        wert = float(mk.get(merkmal, 0.0))
        gewicht = float(gewichte.get(merkmal, 0.0))
        beitrag = wert * gewicht
        summe += beitrag
        if wert == 0:
            continue
        name = titel if merkmal == "kill_punkte" else MERKMAL_NAMEN[merkmal]
        if gewicht == 1.0:
            teile.append(f"{name} {zahl(beitrag)}")
        else:
            teile.append(f"{name} {zahl(wert, 2)} × {zahl(gewicht, 2)} = {zahl(beitrag)}")
    return round(summe, 2), " · ".join(teile) or "keine Merkmale"


class RohScore(unittest.TestCase):
    def test_summe_ungerundet_fehlend_null(self):
        # 3·1 + 0,5·(−2) = 2,0; phase fehlt im Gewicht → 0; laenge fehlt im Merkmal → 0
        self.assertEqual(roh_score({"kill_punkte": 3, "bot_opfer": 0.5, "phase": 0.7},
                                   {"kill_punkte": 1, "bot_opfer": -2, "laenge": -0.5}), 2.0)
        # ungerundet: 1/3 · 1 bleibt 0,333…
        self.assertEqual(roh_score({"sniper": 1 / 3}, {"sniper": 1.0}), 1 / 3)
        self.assertEqual(roh_score({}, GEWICHTE), 0.0)

    def test_summe_der_reihe_nach(self):
        # Zehnmal 0,1 der Reihe nach addiert ist 0.9999999999999999; sum() rechnet ab Python 3.12 kompensiert
        # und käme auf 1.0 – roh_score soll wie das alte bewerte der Reihe nach addieren
        mk = dict.fromkeys(MERKMALE[:10], 0.1)
        self.assertEqual(roh_score(mk, dict.fromkeys(MERKMALE, 1.0)), 0.9999999999999999)

    def test_nur_bekannte_merkmale_zaehlen(self):
        # Fremde Schlüssel (z. B. „kills“ aus momente) sind keine Merkmale und zählen nicht
        self.assertEqual(roh_score({"kills": 4, "kill_punkte": 1}, {"kills": 9, "kill_punkte": 1}), 1.0)

    def test_bewerte_unveraendert(self):
        faelle = [
            ({"kill_punkte": 6, "lautstaerke": 0.8, "laenge": 1.5}, GEWICHTE, "Triple Kill"),
            ({}, GEWICHTE, "Kills"),
            ({"kill_punkte": 1.0, "victory_royale": 0.0, "laenge": 0.0, "lautstaerke": 0.0, "kommentar": 0.0},
             GEWICHTE, "Einzelkill"),
            ({"kill_punkte": 10, "victory_royale": 1, "laenge": 2.8, "lautstaerke": 0.35}, GEWICHTE,
             "4-fach Kill + Victory Royale"),
            # Rundungsgrenzen und Kommazahlen, bei denen eine andere Summierreihenfolge auffiele
            ({"kill_punkte": 0.1, "laenge": 0.2, "lautstaerke": 0.3, "spitzen": 3, "mic_frust": 1},
             {"kill_punkte": 0.1, "laenge": 0.2, "lautstaerke": 0.3, "spitzen": 0.25, "mic_frust": 0.5}, "X"),
            ({"kill_punkte": 3, "bot_opfer": 1.0, "platzierung": 0.25, "mic_lachen": 3}, GEWICHTE, "Double Kill"),
            ({"kill_punkte": 1.005}, {"kill_punkte": 1.0}, "Einzelkill"),
        ]
        for mk, gw, titel in faelle:
            with self.subTest(mk=mk):
                self.assertEqual(bewerte(mk, gw, titel), bewerte_alt(mk, gw, titel))
                self.assertEqual(bewerte(mk, gw, titel)[0], round(roh_score(mk, gw), 2))


class AusMomente(unittest.TestCase):
    def test_zuordnung_und_deckel(self):
        mk = {"mikro_spur": 1, "lachen": 5, "jubel": 2, "frust": 4, "jubel_laut": 7, "spitzen": 9,
              "kills": 2, "energie": 0.4}
        self.assertEqual(merkmale.aus_momente(mk), {"mic_lachen": 3.0, "mic_jubel": 2.0, "mic_frust": 3.0,
                                                    "mic_laut": 3.0, "spitzen": 4.0})

    def test_spitzen_roh_und_gedeckelt(self):
        # momente.merkmale behält den rohen Zähler (Regisseur/Claude sehen ihn), das Merkmal ist gedeckelt
        mk = {"mikro_spur": 1, "spitzen": 6}
        self.assertEqual(merkmale.aus_momente(mk)["spitzen"], 4.0)
        self.assertEqual(mk["spitzen"], 6)
        self.assertEqual(merkmale.aus_momente({"mikro_spur": 1, "spitzen": 2})["spitzen"], 2.0)

    def test_fehlende_schluessel_fehlen(self):
        # Mikro da, Whisper lief nicht: Wortzähler unbekannt, nur mic_laut und spitzen gemessen
        self.assertEqual(merkmale.aus_momente({"mikro_spur": 1, "jubel_laut": 2, "spitzen": 1}),
                         {"mic_laut": 2.0, "spitzen": 1.0})
        self.assertEqual(merkmale.aus_momente({}), {})
        self.assertEqual(merkmale.aus_momente({"lachen": 5, "spitzen": 2}), {"mic_lachen": 3.0, "spitzen": 2.0})

    def test_ohne_mikro_null(self):
        self.assertEqual(merkmale.aus_momente({"mikro_spur": None, "spitzen": 1}),
                         {"mic_lachen": 0.0, "mic_jubel": 0.0, "mic_frust": 0.0, "mic_laut": 0.0, "spitzen": 1.0})

    def test_spur_null_ist_ein_mikro(self):
        # 0 ist ein echter Spur-Index, kein „kein Mikro“
        self.assertEqual(merkmale.aus_momente({"mikro_spur": 0, "spitzen": 1}), {"spitzen": 1.0})

    def test_messfehler_bleibt_unbekannt(self):
        self.assertEqual(merkmale.aus_momente({"mikro_spur": None, "fehler": "ffprobe kaputt"}), {})
        self.assertEqual(merkmale.aus_momente({"fehler": "ffprobe kaputt", "kills": 1}), {})


class MicVollstaendig(unittest.TestCase):
    def test_regel(self):
        faelle = [
            ({"mikro_spur": 1, "lachen": 0, "jubel": 0, "frust": 0}, True),   # Whisper lief
            ({"mikro_spur": None, "spitzen": 1}, True),                       # sicher kein Mikro
            ({"mikro_spur": 0, "jubel_laut": 1}, False),                      # Spur 0 mit Mikro, ohne Whisper
            ({"mikro_spur": 1, "jubel_laut": 2}, False),                      # Spur 1 ohne Whisper
            ({"fehler": "ffprobe kaputt", "kills": 1}, False),                # Messfehler
            ({"mikro_spur": None, "fehler": "x"}, False),                     # Fehler schlägt „kein Mikro“
            ({}, False),
        ]
        for mk, erwartet in faelle:
            with self.subTest(mk=mk):
                self.assertIs(merkmale.mic_vollstaendig(mk), erwartet)


class FuerMoment(unittest.TestCase):
    def test_datei_moment_beispiel(self):
        self.assertEqual(merkmale.fuer_moment(None, {"max_gruppe": 2, "lachen": 1}, KILL_TABELLE),
                         {"kill_punkte": 3.0, "victory_royale": 0.0, "mic_lachen": 1.0})

    def test_datei_moment_ohne_replay_merkmale(self):
        mk = {"kills": 0, "max_gruppe": 0, "victory_royale": 0, "dauer_s": 95.0, "mikro_spur": None,
              "spitzen": 7, "spitzen_s": [1.0, 2.0], "energie": 0.3}
        self.assertEqual(merkmale.fuer_moment(None, mk, KILL_TABELLE),
                         {"kill_punkte": 0.0, "victory_royale": 0.0, "mic_lachen": 0.0, "mic_jubel": 0.0,
                          "mic_frust": 0.0, "mic_laut": 0.0, "spitzen": 4.0})
        self.assertEqual(merkmale.fuer_moment(None, {"max_gruppe": 3, "victory_royale": 1}, KILL_TABELLE),
                         {"kill_punkte": 6.0, "victory_royale": 1.0})

    def test_datei_moment_ohne_momente(self):
        self.assertEqual(merkmale.fuer_moment(None, None, KILL_TABELLE), {"kill_punkte": 0.0, "victory_royale": 0.0})

    def test_clip_moment_mic_aus_momente_gewinnt(self):
        clip = {"kill_punkte": 3.0, "laenge": 0.5, "bot_opfer": 0.5, "mic_laut": 1.0, "spitzen": 1.0}
        mk = {"mikro_spur": 1, "jubel_laut": 3, "spitzen": 2, "lachen": 1, "kills": 2, "max_gruppe": 2}
        self.assertEqual(merkmale.fuer_moment(clip, mk, KILL_TABELLE),
                         {"kill_punkte": 3.0, "laenge": 0.5, "bot_opfer": 0.5, "mic_laut": 3.0, "spitzen": 2.0,
                          "mic_lachen": 1.0})
        # clips.merkmale bleibt unverändert
        self.assertEqual(clip["mic_laut"], 1.0)

    def test_clip_moment_ohne_momente(self):
        self.assertEqual(merkmale.fuer_moment({"kill_punkte": 1, "laenge": 0}, None, KILL_TABELLE),
                         {"kill_punkte": 1.0, "laenge": 0.0})

    def test_listen_und_texte_verworfen(self):
        clip = {"kill_punkte": 1.0, "notiz": "x", "zeiten": [1, 2], "ja": True}
        mk = {"max_gruppe": 1, "lachen": [1], "jubel": "viel", "spitzen": 2}
        self.assertEqual(merkmale.fuer_moment(clip, mk, KILL_TABELLE), {"kill_punkte": 1.0, "spitzen": 2.0})
        self.assertEqual(merkmale.fuer_moment(None, mk, KILL_TABELLE),
                         {"kill_punkte": 1.0, "victory_royale": 0.0, "spitzen": 2.0})


class AktualisiereClip(MitSpeicher):
    def zeile(self, clip_id: int):
        return db.clip(self.con, clip_id)

    def test_vorbewertet_rechnet_punkte_neu(self):
        cid = self.clip_anlegen(status="vorbewertet")
        neu = {"bot_opfer": 1.0, "spitzen": 2.0}
        with db.transaktion(self.con):
            self.assertTrue(merkmale.aktualisiere_clip(self.con, cid, neu, GEWICHTE, 7))
        z = self.zeile(cid)
        mk = json.loads(z["merkmale"])
        self.assertEqual(mk, {"kill_punkte": 1.0, "victory_royale": 0.0, "laenge": 0.0, "lautstaerke": 0.0,
                              "kommentar": 0.0, "bot_opfer": 1.0, "spitzen": 2.0})
        punkte, begruendung = vorbewertung.bewerte(mk, GEWICHTE, "Test")   # wie render, Titel aus clips.titel
        self.assertEqual((z["punkte"], z["begruendung"], z["gewichte_version"]), (punkte, begruendung, 7))
        self.assertEqual(z["punkte"], -0.5)                                 # 1 − 2 + 0,5
        self.assertIn("Bot-Opfer", z["begruendung"])

    def test_gesendet_behaelt_punkte(self):
        for status in ("gesendet", "freigegeben", "verworfen", "veroeffentlicht", "im_highlight", "neu"):
            with self.subTest(status=status):
                cid = self.clip_anlegen(status=status)
                self.assertTrue(merkmale.aktualisiere_clip(self.con, cid, {"bot_opfer": 1.0}, GEWICHTE, 7))
                z = self.zeile(cid)
                self.assertEqual(json.loads(z["merkmale"])["bot_opfer"], 1.0)
                # was der Bot gezeigt hat, bleibt (Annahme S2-A5)
                self.assertEqual((z["punkte"], z["begruendung"], z["gewichte_version"]), (1.0, "Test", 0))

    def test_zweiter_aufruf_aendert_nichts(self):
        for status in ("vorbewertet", "gesendet"):
            with self.subTest(status=status):
                cid = self.clip_anlegen(status=status)
                neu = {"platzierung": 0.25, "mic_laut": 2.0}
                self.assertTrue(merkmale.aktualisiere_clip(self.con, cid, neu, GEWICHTE, 3))
                vorher = dict(self.zeile(cid))
                self.assertFalse(merkmale.aktualisiere_clip(self.con, cid, neu, GEWICHTE, 3))
                self.assertEqual(dict(self.zeile(cid)), vorher)
                # dieselben Werte als int statt float sind keine Änderung
                self.assertFalse(merkmale.aktualisiere_clip(self.con, cid, {"mic_laut": 2}, GEWICHTE, 3))

    def test_listen_bool_und_fehlende_nicht_uebernommen(self):
        cid = self.clip_anlegen(status="gesendet")
        vorher = dict(self.zeile(cid))
        neu = {"spitzen_s": [1.0, 2.0], "tod": True, "text": "gg", "leer": None}
        self.assertFalse(merkmale.aktualisiere_clip(self.con, cid, neu, GEWICHTE, 1))
        self.assertEqual(dict(self.zeile(cid)), vorher)
        # fehlende Schlüssel werden nicht mit 0 angelegt
        self.assertTrue(merkmale.aktualisiere_clip(self.con, cid, {"clutch": 0}, GEWICHTE, 1))
        mk = json.loads(self.zeile(cid)["merkmale"])
        self.assertEqual(mk["clutch"], 0.0)
        self.assertNotIn("mic_lachen", mk)
        self.assertEqual(db.merkmale(self.zeile(cid))["clutch"], 0.0)   # bleibt flaches Zahlen-Dict

    def test_vorbewertet_neue_gewichte_ohne_neue_merkmale(self):
        # Gleiche Merkmale, aber andere Gewichte: Punkte eines noch nicht gesendeten Clips ziehen nach
        cid = self.clip_anlegen(status="vorbewertet")
        self.assertTrue(merkmale.aktualisiere_clip(self.con, cid, {}, GEWICHTE, 2))
        self.assertEqual(self.zeile(cid)["begruendung"], "Test 1,0")
        self.assertFalse(merkmale.aktualisiere_clip(self.con, cid, {}, GEWICHTE, 2))

    def test_unbekannte_clip_id(self):
        self.assertFalse(merkmale.aktualisiere_clip(self.con, 999, {"bot_opfer": 1.0}, GEWICHTE, 1))

    def test_laeuft_in_transaktion_des_aufrufers(self):
        cid = self.clip_anlegen(status="vorbewertet")
        with self.assertRaises(RuntimeError):
            with db.transaktion(self.con):
                merkmale.aktualisiere_clip(self.con, cid, {"bot_opfer": 1.0}, GEWICHTE, 7)
                raise RuntimeError("Abbruch")
        self.assertNotIn("bot_opfer", json.loads(self.zeile(cid)["merkmale"]))


if __name__ == "__main__":
    unittest.main()
