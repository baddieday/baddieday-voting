"""Stufe 2, Paket C: Der Regisseur nutzt die gemeinsame Bewertung (Spec §8.2).

Die Momentstärke (`intensitaet`) ist jetzt derselbe Score wie im Clip-Bot: vorbewertung.roh_score über
merkmale.fuer_moment. Clip-Momente rechnen mit clips.merkmale (Bot-Opfer senkt die Stärke), Datei-Momente mit
max_gruppe und den Mic-Werten. Die Effekt-Titel (Kill-Titel, VICTORY ROYALE, Serien) hängen weiter nur an
max_gruppe/victory und bleiben gleich (Goldtest). Gewichte werden explizit übergeben (Paket D füllt
lernen.aktuelle parallel auf).
"""

import json
import unittest

from clip_pipeline import db, lernbot, regie, vorbewertung
from clip_pipeline.merkmale import fuer_moment
from clip_pipeline.zeit import iso, jetzt

from tests.hilfen import HAT_FFMPEG, MitSpeicher
from tests.regie_hilfen import MOMENTE, MitRegieMaterial
from tests.test_effekte_plan import plane, seg, titel
from tests.test_regie_serie import WIPE, kandidat

KILL = [0, 1, 3, 6, 10]
# Startgewichte aus config/pipeline.toml, hier fest – die Tests sollen nicht von gelernten Gewichten abhängen
GEWICHTE = {"kill_punkte": 1.0, "victory_royale": 5.0, "laenge": -0.5, "lautstaerke": 1.0, "kommentar": 1.0,
            "platzierung": 2.0, "sniper": 1.0, "nahkampf": 0.0, "bot_opfer": -2.0, "phase": 0.5, "endgame": 1.0,
            "clutch": 2.0, "mic_lachen": 1.0, "mic_jubel": 1.0, "mic_frust": 0.5, "mic_laut": 0.5, "spitzen": 0.25}
# Ein Clip, wie render ihn heute schreibt, plus die sieben Replay-Merkmale = 0
CLIP_NULL = {"kill_punkte": 3.0, "victory_royale": 0.0, "laenge": 0.0, "lautstaerke": 0.0, "kommentar": 0.0,
             "platzierung": 0.0, "sniper": 0.0, "nahkampf": 0.0, "bot_opfer": 0.0, "phase": 0.0, "endgame": 0.0,
             "clutch": 0.0}


class MitMomenten(MitSpeicher):
    """Momente direkt in die Datenbank; die Datei muss nur existieren (kandidaten prüft is_file)."""

    def moment(self, schluessel, mk, *, clip_id=None, stimmung="spannend", match="m1", dauer=20.0):
        datei = self.tmp / "momente" / f"{schluessel.replace(':', '_')}.mp4"
        datei.parent.mkdir(parents=True, exist_ok=True)
        datei.touch()
        self.con.execute(
            """INSERT INTO momente (schluessel, match_id, clip_id, datei, start_s, ende_s, kills, stimmung, sicherheit,
                                    quelle, merkmale, erstellt, geaendert)
               VALUES (?, ?, ?, ?, 0, ?, ?, ?, 0.8, 'regel', ?, ?, ?)""",
            (schluessel, match, clip_id, str(datei), dauer, len(mk.get("kill_sekunden") or []), stimmung,
             json.dumps(mk), iso(jetzt()), iso(jetzt())))

    def kandidaten(self, gewichte=GEWICHTE, p=None, frueher=None):
        p = {**regie.PARAMETER, "abwechslung": 0.0, **(p or {})}
        return {k.schluessel: k for k in regie.kandidaten(self.con, p, frueher, gewichte=gewichte, kill_tabelle=KILL)}


class Kandidaten(MitMomenten):
    def test_clip_moment_nutzt_clips_merkmale(self):
        mk = {"max_gruppe": 2, "kill_sekunden": [6.0, 9.0], "spitzen": 2, "jubel_laut": 1, "mikro_spur": 1}
        mensch = self.clip_anlegen(max_gruppe=2, merkmale={**CLIP_NULL, "platzierung": 0.5})
        bots = self.clip_anlegen(max_gruppe=2, merkmale={**CLIP_NULL, "platzierung": 0.5, "bot_opfer": 1.0})
        self.moment("clip:1", mk, clip_id=mensch)
        self.moment("clip:2", mk, clip_id=bots)
        k = self.kandidaten()
        # 3 Kill-Punkte + 0,5·2 Platzierung + 1·0,5 mic_laut + 2·0,25 spitzen = 5,0; Bot-Opfer −2
        self.assertEqual(k["clip:1"].intensitaet, 5.0)
        self.assertEqual(k["clip:2"].intensitaet, 3.0)
        erwartet = round(vorbewertung.roh_score(
            fuer_moment(db.merkmale(db.clip(self.con, bots)), mk, KILL), GEWICHTE), 2)
        self.assertEqual(k["clip:2"].intensitaet, erwartet)                 # genau die eine Formel
        self.assertEqual(k["clip:2"].punkte, 3.0)                            # gesendet, Elo 1500: ohne Zuschlag
        self.assertEqual(k["clip:2"].merkmale, mk)                           # momente-Schlüssel für effekte.py
        self.assertEqual((k["clip:2"].max_gruppe, k["clip:2"].victory), (2, False))

    def test_mic_werte_aus_momente_gewinnen(self):
        # clips.merkmale hat alte Mic-Werte, momente die jüngeren (fuer_moment): momente zählen
        clip = self.clip_anlegen(max_gruppe=1, merkmale={**CLIP_NULL, "kill_punkte": 1.0, "mic_lachen": 3.0})
        self.moment("clip:1", {"max_gruppe": 1, "kill_sekunden": [5.0], "lachen": 1}, clip_id=clip)
        self.assertEqual(self.kandidaten()["clip:1"].intensitaet, 2.0)       # 1 Kill + 1 Lachen

    def test_datei_moment_ueber_max_gruppe_und_mic(self):
        mk = {"max_gruppe": 2, "kill_sekunden": [6.0, 9.0], "lachen": 2, "jubel_laut": 5, "spitzen": 7,
              "mikro_spur": 1}
        self.moment("datei:1", mk, stimmung="episch")
        self.moment("datei:2", mk, stimmung="chill")
        k = self.kandidaten()
        # 3 Kill-Punkte + 2 Lachen + 0,5·min(5, 3) + 0,25·min(7, 4) = 7,5 – die Stimmung zählt nicht mehr mit
        self.assertEqual((k["datei:1"].intensitaet, k["datei:2"].intensitaet), (7.5, 7.5))
        self.assertEqual(k["datei:1"].punkte, 7.5)
        self.assertIsNone(k["datei:1"].clip_id)

    def test_datei_moment_ohne_mikro_und_mit_victory(self):
        self.moment("datei:1", {"max_gruppe": 4, "victory_royale": 1, "kill_sekunden": [1.0, 2.0, 3.0, 4.0],
                                "mikro_spur": None, "spitzen": 1})
        k = self.kandidaten()["datei:1"]
        self.assertEqual(k.intensitaet, 15.25)                               # 10 + 5 + 0,25
        self.assertEqual((k.max_gruppe, k.victory), (4, True))

    def test_gleich_bewertet_clip_und_datei_moment(self):
        # Clip mit einer Kill-Gruppe, Replay-Merkmalen 0, laenge 0 (und lautstaerke 0) = Datei-Moment mit gleichem
        # max_gruppe und gleichen Mic-Werten: dieselbe Stärke, egal woher der Moment kommt
        mic = {"lachen": 1, "jubel": 2, "frust": 1, "jubel_laut": 2, "spitzen": 3, "mikro_spur": 1}
        clip = self.clip_anlegen(max_gruppe=2, merkmale=CLIP_NULL)
        self.moment("clip:1", {"max_gruppe": 2, "kill_sekunden": [6.0, 9.0], **mic}, clip_id=clip)
        self.moment("datei:1", {"max_gruppe": 2, "kill_sekunden": [6.0, 9.0], **mic})
        k = self.kandidaten()
        self.assertEqual(k["clip:1"].intensitaet, k["datei:1"].intensitaet)
        self.assertEqual(k["clip:1"].intensitaet, 3 + 1 + 2 + 0.5 + 1 + 0.75)

    def test_punkte_zuschlaege_wie_bisher(self):
        # punkte = intensitaet + Stimmungs-Bonus + 1 (freigegeben …) + Elo-Term + Moment-Bonus − Abwechslung
        clip = self.clip_anlegen(status="freigegeben", max_gruppe=1, elo=1600.0,
                                 merkmale={**CLIP_NULL, "kill_punkte": 1.0})
        self.moment("clip:1", {"max_gruppe": 1, "kill_sekunden": [5.0]}, clip_id=clip, stimmung="lustig")
        p = {"stimmung_bonus": {"lustig": 0.5}, "moment_bonus": {"clip:1": -0.25}, "abwechslung": 0.7}
        k = self.kandidaten(p=p)["clip:1"]
        self.assertEqual((k.intensitaet, k.punkte, k.abzug), (1.0, 1.0 + 0.5 + 1.0 + 1.0 - 0.25, 0.0))
        k = self.kandidaten(p=p, frueher=[["clip:1"]])["clip:1"]              # im letzten Entwurf: −70 %
        self.assertEqual((k.abzug, k.punkte), (round(0.7 * 3.25, 2), round(3.25 - round(0.7 * 3.25, 2), 2)))

    def test_negativer_score_und_abzug(self):
        # Reiner Bot-Einzelkill: 1 − 2 = −1. Abwechslung zieht wie bisher mindestens anteil·1 ab
        clip = self.clip_anlegen(max_gruppe=1, merkmale={**CLIP_NULL, "kill_punkte": 1.0, "bot_opfer": 1.0})
        self.moment("clip:1", {"max_gruppe": 1, "kill_sekunden": [5.0]}, clip_id=clip)
        k = self.kandidaten(p={"abwechslung": 0.7}, frueher=[["clip:1"]])["clip:1"]
        self.assertEqual((k.intensitaet, k.abzug, k.punkte), (-1.0, 0.7, -1.7))

    def test_ohne_gewichte_und_ohne_merkmale(self):
        # Fehlt ein Gewicht, zählt das Merkmal 0; leere Merkmale = Datei-Moment ohne Kills, Stärke 0
        self.moment("datei:1", {"max_gruppe": 3, "kill_sekunden": [1.0, 2.0, 3.0]})
        self.moment("datei:2", {})
        self.assertEqual(self.kandidaten(gewichte={})["datei:1"].intensitaet, 0.0)
        self.assertEqual(self.kandidaten()["datei:2"].intensitaet, 0.0)
        # max_gruppe über der Tabelle: letzter Wert (wie vorbewertung.punkte_fuer)
        self.moment("datei:3", {"max_gruppe": 7, "kill_sekunden": [1.0]})
        self.assertEqual(self.kandidaten()["datei:3"].intensitaet, 10.0)

    def test_verworfen_und_fehlende_datei_bleiben_draussen(self):
        clip = self.clip_anlegen(status="verworfen", max_gruppe=5, merkmale={**CLIP_NULL, "kill_punkte": 10.0})
        self.moment("clip:1", {"max_gruppe": 5, "kill_sekunden": [5.0]}, clip_id=clip)
        self.moment("datei:1", {"max_gruppe": 1, "kill_sekunden": [5.0]})
        (self.tmp / "momente" / "datei_1.mp4").unlink()
        self.assertEqual(self.kandidaten(), {})

    def test_gewichte_sind_pflicht(self):
        # Keine versteckten Standardwerte: wer kandidaten ruft, reicht Gewichte und Kill-Tabelle durch (Leitplanke 7)
        with self.assertRaises(TypeError):
            regie.kandidaten(self.con, dict(regie.PARAMETER))


class Goldtest(MitMomenten):
    """Effekt-Titel (Kill-Titel, VICTORY ROYALE, Serien) hängen an max_gruppe/victory – unverändert."""

    def test_titel_und_serien_unveraendert(self):
        sieg = self.clip_anlegen(max_gruppe=3, merkmale={**CLIP_NULL, "kill_punkte": 6.0, "bot_opfer": 1.0})
        self.con.execute("UPDATE clips SET victory_royale = 1 WHERE id = ?", (sieg,))
        vorne = self.clip_anlegen(max_gruppe=3, merkmale={**CLIP_NULL, "kill_punkte": 6.0})
        wipe = self.clip_anlegen(max_gruppe=3, merkmale=CLIP_NULL)
        self.moment("clip:sieg", {"max_gruppe": 0, "kill_sekunden": [6.0, 8.0, 10.0]}, clip_id=sieg)
        # ein Kill der Serie liegt vor der Datei: clips.max_gruppe (3) zählt, nicht momente (2)
        self.moment("clip:vorne", {"max_gruppe": 2, "kill_sekunden": [6.0, 8.0]}, clip_id=vorne)
        self.moment("clip:wipe", WIPE, clip_id=wipe, dauer=40.0)
        self.moment("datei:double", {"max_gruppe": 2, "kill_sekunden": [6.0, 8.0]})
        k = self.kandidaten()
        self.assertEqual({s: (x.max_gruppe, x.victory) for s, x in k.items()},
                         {"clip:sieg": (3, True), "clip:vorne": (3, False), "clip:wipe": (3, False),
                          "datei:double": (2, False)})
        self.assertLess(k["clip:sieg"].intensitaet, 11.0)   # Bot-Opfer senkt die Stärke, der Titel bleibt
        self.assertEqual(titel(plane([seg(1, "clip:sieg", 0.0, 14.0, 0.0)], [k["clip:sieg"]])),
                         [("VICTORY ROYALE", 10.4)])
        self.assertEqual(titel(plane([seg(1, "clip:vorne", 0.0, 20.0, 0.0)], [k["clip:vorne"]])),
                         [("TRIPLE KILL", 8.1)])
        self.assertEqual(titel(plane([seg(1, "datei:double", 0.0, 20.0, 0.0)], [k["datei:double"]])),
                         [("DOUBLE KILL", 8.1)])
        # Serie (Team-Wipe mit Umhauen): Teile, Muss-Zonen und Serie wie ohne Datenbank
        ohne_db = kandidat("clip:wipe", WIPE, dauer=40.0, p={"abwechslung": 0.0})
        w = k["clip:wipe"]
        self.assertEqual((w.kern, w.muss, w.teile, w.teile_muss, w.serie),
                         (ohne_db.kern, ohne_db.muss, ohne_db.teile, ohne_db.teile_muss, True))
        segs = regie.plane_zeitleiste([w], [], regie.FORMATE["short"], dict(regie.PARAMETER), 30)
        self.assertEqual([t for t, _ in titel(plane(segs, [w]))], ["TRIPLE KILL"])


class Balken(unittest.TestCase):
    def test_negative_werte(self):
        self.assertEqual(lernbot._balken([2, -3, 1]), "█▁▇")
        self.assertEqual(lernbot._balken([-1, -2]), "█▁")
        self.assertEqual(lernbot._balken([1, -10]), "█▁")      # warf vorher IndexError

    def test_alle_gleich_und_leer(self):
        self.assertEqual(lernbot._balken([0, 0]), "▄▄")
        self.assertEqual(lernbot._balken([-4.5]), "▄")
        self.assertEqual(lernbot._balken([]), "")

    def test_steigend(self):
        self.assertEqual(lernbot._balken([0, 1, 2, 3, 4, 5, 6, 7]), "▁▂▃▄▅▆▇█")


@unittest.skipUnless(HAT_FFMPEG, "ffmpeg fehlt")
class Erstelle(MitRegieMaterial):
    def test_negative_intensitaet_bricht_erstelle_nicht(self):
        self.momente_anlegen(MOMENTE[:8])
        # Jeder Moment gehört zu einem Clip mit lauter Bot-Opfern: Stärke 1 − 6 + Spitzen ≤ −4
        for z in self.con.execute("SELECT id, match_id FROM momente").fetchall():
            clip = self.clip_anlegen(match_id=z["match_id"],
                                     merkmale={**CLIP_NULL, "kill_punkte": 1.0, "bot_opfer": 3.0})
            self.con.execute("UPDATE momente SET clip_id = ? WHERE id = ?", (clip, z["id"]))
        self.konfig.daten["regie"]["effekte"]["an"] = True
        e = regie.erstelle(self.con, self.konfig, "short")
        with open(e["datei"], encoding="utf-8") as f:
            liste = json.load(f)
        self.assertEqual(regie.pruefe_liste(liste), [])
        self.assertTrue(liste["bogen"] and max(liste["bogen"]) < 0, liste["bogen"])
        self.assertEqual(liste["bogen"][-1], max(liste["bogen"]))            # Höhepunkt bleibt am Schluss
        zeile = self.con.execute("SELECT * FROM entwuerfe WHERE id = ?", (e["entwurf"],)).fetchone()
        self.assertIn("Bogen ", lernbot.entwurf_text(zeile, liste))


if __name__ == "__main__":
    unittest.main()
