import unittest
from datetime import datetime, timedelta

from clip_pipeline.quellen import Aufnahme
from clip_pipeline.replay import match_aus_json, match_id
from clip_pipeline.schnittliste import waehle_aufnahme
from clip_pipeline.vorbewertung import bewerte, gruppiere, kandidaten
from clip_pipeline.zeit import UTC, lokal_zu_utc, spielabend
from clip_pipeline.zeitleiste import Zeitleiste, aus_rekordern

ICH = "956D01EA3E7B43A0AD41096BAA0B6B9E"
EINSTELLUNGEN = {"multikill_fenster_s": 10.0, "puffer_vorne_s": 8.0, "puffer_hinten_s": 5.0,
                 "laenge_frei_s": 30.0, "kill_punkte": [0, 1, 3, 6, 10]}
GEWICHTE = {"kill_punkte": 1.0, "victory_royale": 5.0, "laenge": -0.5, "lautstaerke": 1.0, "kommentar": 1.0}


def t(sekunde: float) -> datetime:
    return datetime(2026, 9, 21, 19, 0, tzinfo=UTC) + timedelta(seconds=sekunde)


class Zeit(unittest.TestCase):
    def test_sommer_und_winterzeit(self):
        self.assertEqual(lokal_zu_utc(datetime(2026, 9, 21, 21, 42, 22), "Europe/Berlin").hour, 19)  # MESZ
        self.assertEqual(lokal_zu_utc(datetime(2026, 12, 1, 21, 42, 22), "Europe/Berlin").hour, 20)  # MEZ

    def test_spielabend_nach_mitternacht(self):
        nach_mitternacht = lokal_zu_utc(datetime(2026, 9, 23, 0, 37), "Europe/Berlin")
        self.assertEqual(spielabend(nach_mitternacht, "Europe/Berlin").isoformat(), "2026-09-22")


class Replay(unittest.TestCase):
    DATEN = {
        "replay_start": "2026-09-21T21:42:22.6170000", "replay_start_kind": "Unspecified", "laenge_ms": 1032994,
        "build": {"branch": "++Fortnite+Release-42.20", "changelist": 58011042},
        "ich_quelle": "argument", "ich": {"epic_id": ICH, "player_id": ICH, "platzierung": 3},
        "stats_eliminierungen": 2, "team_platzierung": 3,
        "eliminierungen": [
            {"t_ms": 363541, "eliminator": ICH, "eliminiert": "A", "knock": True},
            {"t_ms": 374803, "eliminator": ICH, "eliminiert": "A", "knock": False},
            {"t_ms": 400000, "eliminator": "X", "eliminiert": "Y", "knock": False},  # fremder Kill
            {"t_ms": 662357, "eliminator": ICH.lower(), "eliminiert": "B", "knock": False},
            {"t_ms": 838596, "eliminator": "C", "eliminiert": ICH, "knock": False},
        ],
    }

    def test_zeitbasis_und_meine_ereignisse(self):
        m = match_aus_json(self.DATEN, "m", zonen_name="Europe/Berlin")
        self.assertEqual(m.start_utc, datetime(2026, 9, 21, 19, 42, 22, 617000, tzinfo=UTC))  # Ortszeit -> UTC
        self.assertEqual([e.art for e in m.ereignisse], ["knock", "kill", "kill", "tod"])
        self.assertEqual(m.kills[0], m.start_utc + timedelta(milliseconds=374803))
        self.assertEqual(m.warnungen, [])

    def test_warnung_bei_abweichender_killzahl_und_fehlendem_ich(self):
        daten = dict(self.DATEN, stats_eliminierungen=5)
        self.assertIn("5 Kills", match_aus_json(daten, "m", zonen_name="Europe/Berlin").warnungen[0])
        ohne_ich = match_aus_json(dict(self.DATEN, ich=None, ich_quelle=None), "m", zonen_name="Europe/Berlin")
        self.assertEqual(ohne_ich.kills, [])
        self.assertIn("Epic-ID", ohne_ich.warnungen[0])

    def test_kill_gehoert_dem_der_umhaut(self):
        def elim(t, taeter, opfer, knock=False, selbst=False):
            return {"t_ms": t * 1000, "eliminator": taeter, "eliminiert": opfer, "knock": knock, "selbst": selbst}

        daten = dict(self.DATEN, stats_eliminierungen=4, eliminierungen=[
            elim(100, ICH, "A", knock=True), elim(104, "TEAM", "A"),       # ich haue um, Teammate erledigt -> meiner (t=100)
            elim(200, "TEAM", "B", knock=True), elim(203, ICH, "B"),       # Teammate haut um, ich erledige -> nicht meiner
            elim(300, ICH, "C", knock=True), elim(302, ICH, "C"),          # alles selbst -> meiner (t=302, das Erledigen)
            elim(400, ICH, "D"),                                           # direkter Kill -> meiner
            elim(500, ICH, "E", knock=True), elim(510, "E", "E", selbst=True),  # umgehauen, dann Sturm -> meiner (t=500)
            elim(600, "X", "F", selbst=True),                              # fremde Selbst-Eliminierung -> nicht meiner
            elim(700, ICH, "G", knock=True), elim(900, "Y", "G"),          # Umhauen zu lange her (wiederbelebt) -> nicht meiner
        ])
        m = match_aus_json(daten, "m", zonen_name="Europe/Berlin")
        sekunden = [round((k - m.start_utc).total_seconds()) for k in m.kills]
        self.assertEqual(sekunden, [100, 302, 400, 500])
        self.assertFalse(any("Kills" in w for w in m.warnungen))  # passt zur Replay-Statistik (4)

    def test_match_id(self):
        from pathlib import Path
        self.assertEqual(match_id(Path("UnsavedReplay-2026.09.21-21.42.22.replay")), "2026-09-21_21-42-22")


class Vorbewertung(unittest.TestCase):
    def test_kette_bildet_multikills(self):
        gruppen = gruppiere([t(100), t(106), t(113), t(140)], 10.0)
        self.assertEqual([len(g) for g in gruppen], [3, 1])  # 6 s und 7 s Abstand -> Triple

    def test_kandidaten_punkte_und_victory(self):
        zl = Zeitleiste(kills=[t(100), t(106), t(113), t(300)], quelle="replay", victory_royale=True)
        liste = kandidaten(zl, EINSTELLUNGEN)
        self.assertEqual([(k.titel, k.merkmale["kill_punkte"]) for k in liste],
                         [("Triple Kill", 6.0), ("Einzelkill + Victory Royale", 1.0)])
        self.assertEqual(liste[1].merkmale["victory_royale"], 1.0)
        self.assertEqual(liste[0].start_utc, t(92))
        self.assertEqual(liste[0].ende_utc, t(118))

    def test_ueberlappende_fenster_werden_ein_clip(self):
        liste = kandidaten(Zeitleiste(kills=[t(100), t(112)], quelle="replay"), EINSTELLUNGEN)
        self.assertEqual(len(liste), 1)
        self.assertEqual((liste[0].titel, liste[0].merkmale["kill_punkte"]), ("Einzelkill + Einzelkill", 2.0))

    def test_bewertung_mit_begruendung(self):
        punkte, text = bewerte({"kill_punkte": 6, "lautstaerke": 0.8, "laenge": 1.5}, GEWICHTE, "Triple Kill")
        self.assertEqual(punkte, 6.05)
        self.assertEqual(text, "Triple Kill 6,0 · Länge 1,50 × −0,50 = −0,8 · Lautstärke 0,8")


class Rekorder(unittest.TestCase):
    def test_steelseries_vor_nvidia_und_doppelte_marker(self):
        from clip_pipeline.quellen import Ereignis
        ss = Aufnahme("s.mp4", "steelseries", t(0), t(120), 120, 2, 30, [
            Ereignis(t(110), "kill", 1, "steelseries"), Ereignis(t(114), "kill", 2, "steelseries")])
        ss2 = Aufnahme("s2.mp4", "steelseries", t(60), t(180), 120, 2, 30, [Ereignis(t(110.3), "kill", 1, "steelseries")])
        nv = Aufnahme("n.mp4", "nvidia_highlight", t(95), t(120), 25, 1, 60, [Ereignis(t(114), "kill", 2, "nvidia")])
        zl = aus_rekordern([ss, ss2, nv], t(0), t(200))
        self.assertEqual((zl.quelle, len(zl.kills)), ("steelseries", 2))  # doppelter Marker aus ss2 entfällt
        nur_nvidia = aus_rekordern([nv], t(0), t(200))
        self.assertEqual(len(nur_nvidia.kills), 2)  # "Doppeleliminierung" = 2 Kills
        self.assertTrue(nur_nvidia.warnungen)


class Auswahl(unittest.TestCase):
    def test_beste_abdeckung_dann_prioritaet(self):
        k = kandidaten(Zeitleiste(kills=[t(100), t(104)], quelle="replay"), EINSTELLUNGEN)[0]  # Fenster 92..109
        kurz = Aufnahme("n.mp4", "nvidia_highlight", t(85), t(106), 21, 1, 60)  # Ende zu früh
        lang = Aufnahme("s.mp4", "steelseries", t(0), t(120), 120, 2, 30)
        ohne = Aufnahme("x.mp4", "nvidia_highlight", t(0), t(99), 99, 1, 60)  # enthält Kill 2 nicht
        s = waehle_aufnahme(k, [kurz, ohne, lang], ["steelseries", "nvidia_highlight"])
        self.assertEqual((s.aufnahme.pfad, s.start_s, s.ende_s, s.abdeckung), ("s.mp4", 92.0, 109.0, 1.0))
        s2 = waehle_aufnahme(k, [kurz], [])
        self.assertEqual((s2.start_s, s2.ende_s), (7.0, 21.0))  # auf das Video begrenzt
        self.assertIsNone(waehle_aufnahme(k, [ohne], []))


if __name__ == "__main__":
    unittest.main()
