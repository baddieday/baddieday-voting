import unittest
from datetime import datetime, timedelta

from clip_pipeline.quellen import Aufnahme
from clip_pipeline.replay import match_aus_json, match_id
from clip_pipeline.schnittliste import waehle_aufnahme
from clip_pipeline.vorbewertung import (MAX_DAUER_S, anlauf_start, bewerte, gruppiere, gruppiere_paare, kandidaten,
                                        laenge_ab_kill)
from clip_pipeline.zeit import UTC, lokal_zu_utc, spielabend
from clip_pipeline.zeitleiste import Zeitleiste, aus_replay, aus_rekordern

ICH = "0123456789ABCDEF0123456789ABCDEF"  # künstliche Epic-ID (Test)
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


def elim(t, taeter, opfer, knock=False, selbst=False):
    return {"t_ms": round(t * 1000), "eliminator": taeter, "eliminiert": opfer, "knock": knock, "selbst": selbst}


class Aktion(unittest.TestCase):
    """Aktions-Zeitpunkt je Kill = mein Umhauen; die Kill-Zeit (und damit das Zählen) bleibt wie bisher."""

    def _match(self, eliminierungen, stats):
        daten = dict(Replay.DATEN, stats_eliminierungen=stats, eliminierungen=eliminierungen)
        return match_aus_json(daten, "m", zonen_name="Europe/Berlin")

    def _sek(self, m, zeiten):
        return [round((z - m.start_utc).total_seconds(), 1) for z in zeiten]

    def test_team_wipe(self):
        # Muster aus echten Daten: ich haue bei −15,0 / −4,6 / 0 s um; das letzte Umhauen löscht das Team aus,
        # beim Wipe stehe ich als Täter aller drei Eliminierungen im Replay.
        m = self._match([
            elim(185.0, ICH, "OPFER-1", knock=True),
            elim(195.4, ICH, "OPFER-2", knock=True),
            elim(200.0, ICH, "OPFER-3", knock=True),
            elim(200.0, ICH, "OPFER-1"), elim(200.0, ICH, "OPFER-2"), elim(200.1, ICH, "OPFER-3"),
        ], 3)
        self.assertEqual(self._sek(m, m.kills), [200.0, 200.0, 200.1])  # Kill-Zeiten wie bisher
        kills = [e for e in m.ereignisse if e.art == "kill"]
        self.assertEqual(self._sek(m, [e.aktion for e in kills]), [185.0, 195.4, 200.0])
        self.assertEqual([self._sek(m, p) for p in m.kill_aktionen],
                         [[200.0, 185.0], [200.0, 195.4], [200.1, 200.0]])
        self.assertEqual([len(g) for g in gruppiere(m.kills, 10.0)], [3])  # weiter ein Triple
        self.assertEqual(m.warnungen, [])
        zl = aus_replay(m)
        self.assertEqual((self._sek(m, zl.kills), self._sek(m, zl.aktionen)),
                         ([200.0, 200.0, 200.1], [185.0, 195.4, 200.0]))

    def test_teammate_erledigt_und_alte_knocks(self):
        m = self._match([
            elim(300, ICH, "OPFER-1", knock=True), elim(304, "TEAM", "OPFER-1"),  # Teammate erledigt
            elim(400, ICH, "OPFER-2", knock=True), elim(495, ICH, "OPFER-2"),     # Umhauen > 90 s her
            elim(600, ICH, "OPFER-3"),                                             # direkter Kill
        ], 3)
        kills = [e for e in m.ereignisse if e.art == "kill"]
        self.assertEqual(self._sek(m, [e.zeit_utc for e in kills]), [300.0, 495.0, 600.0])
        self.assertEqual(kills[0].aktion, kills[0].zeit_utc)  # Aktion = Kill-Zeit (mein Umhauen)
        self.assertIsNone(kills[1].aktion_utc)                # zu alt -> keine Aktion, Kill-Zeit zählt
        self.assertEqual(kills[1].aktion, kills[1].zeit_utc)
        self.assertIsNone(kills[2].aktion_utc)
        self.assertTrue(all(e.aktion_utc is None for e in m.ereignisse if e.art != "kill"))

    def test_alte_aufrufer(self):
        from clip_pipeline.replay import MeinEreignis
        e = MeinEreignis(t(5), "kill")
        self.assertEqual((e.aktion_utc, e.aktion), (None, t(5)))
        self.assertEqual(Zeitleiste(kills=[t(1), t(2)], quelle="nvidia").kill_aktionen, [(t(1), t(1)), (t(2), t(2))])


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

    def test_ohne_aktionen_wie_bisher(self):
        k = kandidaten(Zeitleiste(kills=[t(100), t(104)], quelle="steelseries"), EINSTELLUNGEN)[0]
        self.assertEqual((k.start_utc, k.ende_utc, k.aktion_zeiten, k.hinweis), (t(92), t(109), [t(100), t(104)], None))

    def test_start_vor_der_ersten_aktion(self):
        zl = Zeitleiste(kills=[t(200), t(200), t(200.1)], quelle="replay",
                        aktionen=[t(185), t(195.4), t(200)])
        k, = kandidaten(zl, EINSTELLUNGEN)
        self.assertEqual((k.titel, k.merkmale["kill_punkte"]), ("Triple Kill", 6.0))
        self.assertEqual((k.start_utc, k.ende_utc), (t(177), t(205.1)))  # erste Aktion − 8 s, Ende wie bisher
        self.assertEqual((k.erste_aktion, k.aktion_zeiten), (t(185), [t(185), t(195.4), t(200)]))
        self.assertEqual(k.merkmale["laenge"], 0.0)  # der Anlauf kostet keine Punkte
        self.assertIsNone(k.hinweis)

    def test_aktion_parallel_zu_kills_auch_ueber_gruppen(self):
        zl = Zeitleiste(kills=[t(100), t(112)], quelle="replay", aktionen=[t(99), t(90)])
        k, = kandidaten(zl, EINSTELLUNGEN)  # altes Fenster überlappt -> ein Clip wie bisher
        self.assertEqual((k.titel, k.aktion_zeiten, k.erste_aktion), ("Einzelkill + Einzelkill", [t(99), t(90)], t(90)))
        self.assertEqual(k.start_utc, t(82))

    def test_hoechstens_60_sekunden(self):
        zl = Zeitleiste(kills=[t(100), t(105)], quelle="replay", aktionen=[t(40), t(105)])
        with self.assertLogs("pipeline", "WARNING"):
            k, = kandidaten(zl, EINSTELLUNGEN)
        self.assertEqual((k.start_utc, k.ende_utc), (t(50), t(110)))  # vorne auf 60 s gekürzt
        self.assertEqual(k.dauer_s, MAX_DAUER_S)
        self.assertIn("60 s", k.hinweis)
        # Ist schon das Kill-Fenster länger als 60 s, bleibt es beim alten Start (nie später)
        kills = [t(100 + 11 * i) for i in range(6)]  # 92 … 160 = 68 s, Fenster überlappen
        zl = Zeitleiste(kills=kills, quelle="replay", aktionen=[t(95)] + kills[1:])
        with self.assertLogs("pipeline", "WARNING"):
            k, = kandidaten(zl, EINSTELLUNGEN)
        self.assertEqual((k.start_utc, k.ende_utc, k.kills), (t(92), t(160), 6))

    def test_neue_fenster_ueberlappen_alte_nicht(self):
        zl = Zeitleiste(kills=[t(100), t(120)], quelle="replay", aktionen=[t(100), t(110)])
        liste = kandidaten(zl, EINSTELLUNGEN)
        self.assertEqual([k.titel for k in liste], ["Einzelkill", "Einzelkill"])  # Zählen wie bisher
        self.assertEqual([(k.start_utc, k.ende_utc) for k in liste], [(t(92), t(105)), (t(102), t(125))])

    def test_anlauf_ohne_kill_des_vorigen_clips(self):
        # Echtes Muster (Clip 4/5 eines Matches vom 13.09., Zeiten verschoben): Einzelkill 6,5 s vor dem ersten
        # Umhauen eines Team-Wipes. Der Anlauf des Wipes beginnt 0,5 s nach diesem Kill – nie mit ihm.
        zl = Zeitleiste(kills=[t(100), t(117.9), t(118.1), t(118.1)], quelle="replay",
                        aktionen=[t(82.4), t(106.5), t(115.9), t(118.1)])
        with self.assertLogs("pipeline", "INFO") as logs:
            eins, zwei = kandidaten(zl, EINSTELLUNGEN)
        self.assertTrue(any("früheren Clips" in z for z in logs.output))
        self.assertEqual((eins.titel, zwei.titel, zwei.merkmale["kill_punkte"]), ("Einzelkill", "Triple Kill", 6.0))
        self.assertEqual((eins.start_utc, eins.ende_utc), (t(74.4), t(105)))
        self.assertEqual((zwei.start_utc, zwei.ende_utc), (t(100.5), t(123.1)))
        self.assertEqual([z for z in eins.kill_zeiten if zwei.start_utc <= z <= zwei.ende_utc], [])
        self.assertIsNone(zwei.hinweis)
        # Liegt der fremde Kill näher als 1 s vor der Aktion, bleibt 1 s Anlauf (dann ist er mit drin)
        zl = Zeitleiste(kills=[t(100), t(117.9)], quelle="replay", aktionen=[t(100), t(100.6)])
        self.assertEqual([k.start_utc for k in kandidaten(zl, EINSTELLUNGEN)], [t(92), t(99.6)])

    def test_anlauf_start(self):
        # Eine Regel für analyze und den Nachschnitt: (Beginn, Beginn vor der Kappung, fremder Kill?)
        self.assertEqual(anlauf_start(20.0, 40.0, 8.0), (12.0, 12.0, False))
        self.assertEqual(anlauf_start(20.0, 40.0, 8.0, [13.5, 25.0]), (14.0, 14.0, True))
        self.assertEqual(anlauf_start(20.0, 40.0, 8.0, [10.0]), (12.0, 12.0, False))     # vor dem Anlauf: egal
        self.assertEqual(anlauf_start(5.0, 40.0, 8.0, fruehestens=0.0), (0.0, 0.0, False))
        self.assertEqual(anlauf_start(20.0, 40.0, 8.0, spaetestens=10.0), (10.0, 10.0, False))
        self.assertEqual(anlauf_start(20.0, 80.0, 8.0), (20.0, 12.0, False))             # auf 60 s gekappt
        self.assertEqual(anlauf_start(20.0, 80.0, 8.0, spaetestens=15.0), (15.0, 12.0, False))  # nie nach dem Kill-Fenster

    def test_gruppiere_paare_wie_gruppiere(self):
        kills = [t(100), t(106), t(113), t(140), t(140)]
        paare = list(zip(kills, [t(90), t(106), t(100), t(139), t(130)]))
        gruppen = gruppiere_paare(paare, 10.0)
        self.assertEqual([[k for k, _ in g] for g in gruppen], gruppiere(kills, 10.0))
        self.assertEqual([a for _, a in gruppen[1]], [t(130), t(139)])  # gleiche Kill-Zeit: nach Aktion

    def test_laenge_ab_kill(self):
        self.assertEqual(laenge_ab_kill(10.0, 50.0, 30.0, 8.0, 30.0), 0.0)   # 28 s ab Kill − 8
        self.assertEqual(laenge_ab_kill(0.0, 70.0, 20.0, 8.0, 30.0), 2.8)    # 58 s ab Kill − 8 (nicht 70)
        self.assertEqual(laenge_ab_kill(15.0, 60.0, 20.0, 8.0, 30.0), 1.5)   # Schnitt nach Kill − 8: ab Schnitt

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

    def test_anlauf_zaehlt_fuer_die_abdeckung(self):
        zl = Zeitleiste(kills=[t(100), t(100)], quelle="replay", aktionen=[t(88), t(100)])
        k = kandidaten(zl, EINSTELLUNGEN)[0]  # Fenster 80..105
        kurz = Aufnahme("n.mp4", "nvidia_highlight", t(84), t(106), 22, 1, 60)
        lang = Aufnahme("s.mp4", "steelseries", t(60), t(120), 60, 2, 30)
        s = waehle_aufnahme(k, [kurz, lang], ["nvidia_highlight", "steelseries"])
        self.assertEqual((s.aufnahme.pfad, s.start_s, s.ende_s), ("s.mp4", 20.0, 45.0))


if __name__ == "__main__":
    unittest.main()
