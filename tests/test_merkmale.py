"""Paket A2 der Stufe 2: Replay-Merkmale (Spec §8.1, Annahmen S2-A2, S2-A3, S2-A4, S2-A17).

Geprüft wird (Plan docs/superpowers/plans/2026-09-25-lernschleife-stufe-2.md, Paket A2):
  - replay: je Ereignis waffe, opfer_bot, verbleibend – die Kill-Regel bleibt (Goldtest in test_replay_merkmale_gold)
  - merkmale.waffen_kategorie und merkmale.aus_replay: jedes Merkmal einzeln, Grenzen, Team-Wipe, Rekorder-Rückfall
  - merkmale.melde_unbekannte_waffen: eine Sammelmeldung je Match, Vermerke nie verschickt, Ruhezeit
  - merkmale.nachtragen_replay: idempotent, fehlende replay.json, weckt nie
  - verarbeitung.analyze: Bot-Opfer kosten sichtbar Punkte; cli._cmd_replay zeigt die neuen Felder
"""

from __future__ import annotations

import contextlib
import io
import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from clip_pipeline import cli, db, konfig, merkmale, verarbeitung
from clip_pipeline.bot import aktionen
from clip_pipeline.konfig import Konfig
from clip_pipeline.merkmale import REPLAY_MERKMALE
from clip_pipeline.replay import match_aus_json
from clip_pipeline.zeit import UTC, aus_iso, iso, lokal_zu_utc

from .hilfen import MitSpeicher

START = datetime(2026, 9, 21, 19, 0, tzinfo=UTC)  # Replay-Start aller Vorlagen hier
ICH = "ICH"
SNIPER, SHOTGUN, SMG, AR = 7, 3, 4, 9       # GunType-Zahlen nur für diesen Test (echte kennt erst die Kalibrierung)
WAFFEN = {"sniper": [SNIPER], "nahkampf": [SHOTGUN, SMG], "sonstige": [AR]}
MERKMAL_KONFIG = {"endgame_spieler": 10, "clutch_vor_s": 30.0, "clutch_nach_s": 10.0, "waffen": WAFFEN}


def t(sekunde: float) -> datetime:
    return START + timedelta(seconds=sekunde)


def elim(s: float, taeter: str, opfer: str, *, knock=False, selbst=False, waffe=None, bot=None) -> dict:
    """Ein Eintrag aus replay2json `eliminierungen` (Felder wie tools/replay2json/Program.cs)."""
    return {"t_ms": round(s * 1000), "eliminator": taeter, "eliminiert": opfer, "knock": knock, "selbst": selbst,
            "eliminiert_bot": bot, "waffe": waffe}


def daten(eliminierungen: list[dict], *, platz=4, spieler=100, laenge_s=1000.0, quelle="konfig") -> dict:
    return {"replay_start": iso(START), "replay_start_kind": "Utc", "laenge_ms": round(laenge_s * 1000),
            "ich_quelle": quelle, "ich": {"epic_id": ICH, "platzierung": platz}, "spieler_gesamt": spieler,
            "eliminierungen": eliminierungen}


def match(eliminierungen: list[dict], **mehr):
    return match_aus_json(daten(eliminierungen, **mehr), "m", zonen_name="Europe/Berlin")


def test_konfig(**merkmal) -> Konfig:
    return Konfig({"merkmale": dict(MERKMAL_KONFIG, **merkmal)}, Path("test.toml"))


def kills(m) -> list:
    return [e for e in m.ereignisse if e.art == "kill"]


class ReplayFelder(unittest.TestCase):
    """MeinEreignis bekommt waffe, opfer_bot, verbleibend – rein additiv."""

    def test_waffe_meines_umhauens_sonst_des_erledigens(self):
        m = match([
            elim(100, ICH, "A", knock=True, waffe=SNIPER), elim(104, "TEAM", "A", waffe=SHOTGUN),  # Sniper-Knock, Teammate erledigt
            elim(200, ICH, "B", knock=True, waffe=SNIPER), elim(203, ICH, "B", waffe=SMG),         # selbst erledigt: Umhauen zählt
            elim(300, ICH, "C", waffe=AR),                                                          # direkter Kill
            elim(400, "TEAM", "D", knock=True, waffe=SNIPER), elim(402, ICH, "D", waffe=SHOTGUN),   # nicht meiner
        ])
        self.assertEqual([(round((e.zeit_utc - START).total_seconds()), e.waffe) for e in kills(m)],
                         [(100, SNIPER), (203, SNIPER), (300, AR)])

    def test_bot_opfer_und_null(self):
        m = match([elim(100, ICH, "A", bot=True), elim(200, ICH, "B", bot=False), elim(300, ICH, "C", bot=None),
                   {"t_ms": 400000, "eliminator": ICH, "eliminiert": "D", "knock": False}])  # altes JSON ohne Feld
        self.assertEqual([e.opfer_bot for e in kills(m)], [True, False, None, None])

    def test_verbleibend_aus_finalen_eliminierungen(self):
        m = match([
            elim(10, "X", "Y1"), elim(20, "X", "Y2", knock=True),     # Knock zählt nicht
            elim(30, "Y3", "Y3", selbst=True),                         # Sturm zählt (selbst eingeschlossen)
            elim(40, ICH, "A"), elim(40, "X", "Y4"),                   # gleiche Zeit zählt mit (t_ms ≤ Ereignis)
        ], spieler=12)
        self.assertEqual([e.verbleibend for e in kills(m)], [12 - 4])

    def test_verbleibend_nie_negativ_und_ohne_spieler_none(self):
        eliminierungen = [elim(s, "X", f"Y{s}") for s in range(1, 6)] + [elim(10, ICH, "A")]
        self.assertEqual(kills(match(eliminierungen, spieler=3))[0].verbleibend, 0)
        self.assertIsNone(kills(match(eliminierungen, spieler=None))[0].verbleibend)

    def test_alte_aufrufer_unveraendert(self):
        from clip_pipeline.replay import MeinEreignis, _meine_ereignisse
        e = MeinEreignis(t(5), "kill")
        self.assertEqual((e.waffe, e.opfer_bot, e.verbleibend), (None, None, None))
        ereignisse = _meine_ereignisse([elim(5, ICH, "A", waffe=AR)], START, {ICH})  # ohne spieler_gesamt
        self.assertEqual((ereignisse[0].waffe, ereignisse[0].verbleibend), (AR, None))


class WaffenKategorie(unittest.TestCase):
    def test_kategorien(self):
        k = test_konfig()
        self.assertEqual([merkmale.waffen_kategorie(w, k) for w in (SNIPER, SHOTGUN, SMG, AR, 99, None)],
                         ["sniper", "nahkampf", "nahkampf", "sonstige", "sonstige", "sonstige"])

    def test_ohne_eintraege(self):
        self.assertEqual(merkmale.waffen_kategorie(SNIPER, Konfig({}, Path("x"))), "sonstige")
        self.assertEqual(merkmale.waffen_kategorie(SNIPER, test_konfig(waffen={"sniper": []})), "sonstige")


class AusReplay(unittest.TestCase):
    K = test_konfig()

    def rechne(self, m, zeiten):
        return merkmale.aus_replay(m, zeiten, self.K)

    def test_genau_die_sieben_merkmale(self):
        m = match([elim(100, ICH, "A")])
        self.assertEqual(set(self.rechne(m, [t(100)])), set(REPLAY_MERKMALE))

    def test_platzierung(self):
        self.assertEqual(self.rechne(match([elim(100, ICH, "A")], platz=4), [t(100)])["platzierung"], 0.25)
        self.assertEqual(self.rechne(match([elim(100, ICH, "A")], platz=1), [t(100)])["platzierung"], 1.0)
        self.assertEqual(self.rechne(match([elim(100, ICH, "A")], platz=None), [t(100)])["platzierung"], 0.0)
        # je Match gleich – auch für einen Kandidaten ohne zugeordnete Kills
        self.assertEqual(self.rechne(match([elim(100, ICH, "A")], platz=4), [t(555)])["platzierung"], 0.25)

    def test_waffen_und_bot_anteile(self):
        m = match([elim(100, ICH, "A", waffe=SNIPER, bot=True), elim(105, ICH, "B", waffe=SHOTGUN, bot=False),
                   elim(108, ICH, "C", waffe=99, bot=None), elim(109, ICH, "D", waffe=SMG, bot=True)])
        r = self.rechne(m, [t(100), t(105), t(108), t(109)])
        self.assertEqual((r["sniper"], r["nahkampf"], r["bot_opfer"]), (0.25, 0.5, 0.5))  # None = kein Bot

    def test_null_kills(self):
        # Befund K-5: Ohne Kill-Zeiten gibt es nichts zuzuordnen – gemessen 0. (Kill-Zeiten OHNE Ereignis sind
        # unbekannt, siehe test_kill_zeiten_ohne_ereignis.)
        r = self.rechne(match([elim(100, ICH, "A", waffe=SNIPER, bot=True)]), [])
        self.assertEqual((r["sniper"], r["nahkampf"], r["bot_opfer"], r["endgame"], r["clutch"]), (0, 0, 0, 0, 0))

    def test_kill_zeiten_ohne_ereignis(self):
        # Befund K-5: Clip von vor der Kill-Regel vom 24.09. – seine Kill-Zeit findet kein Ereignis. Nur, was je
        # Match bzw. aus der Zeit bekannt ist: platzierung und phase; der Rest bleibt unbekannt (fehlt).
        m = match([elim(100, ICH, "A", waffe=SNIPER, bot=True)], platz=4, laenge_s=1000)
        with self.assertLogs("pipeline", "WARNING"):
            r = self.rechne(m, [t(500)])
        self.assertEqual(r, {"platzierung": 0.25, "phase": 0.5})
        with self.assertLogs("pipeline", "WARNING"):
            r = self.rechne(match([elim(100, ICH, "A")], platz=None, laenge_s=1000), [t(500)])
        self.assertEqual(r, {"phase": 0.5})

    def test_leere_waffenlisten_lassen_sniper_und_nahkampf_weg(self):
        # Befund S-3: Solange [merkmale.waffen] nicht kalibriert ist, sind sniper/nahkampf unbekannt, nicht 0
        leer = test_konfig(waffen={"sniper": [], "nahkampf": [], "sonstige": []})
        r = merkmale.aus_replay(match([elim(100, ICH, "A", waffe=SNIPER, bot=True)]), [t(100)], leer)
        self.assertEqual(set(r), set(REPLAY_MERKMALE) - {"sniper", "nahkampf"})
        self.assertEqual(r["bot_opfer"], 1.0)
        ohne_abschnitt = merkmale.aus_replay(match([elim(100, ICH, "A")]), [t(100)], Konfig({}, Path("x")))
        self.assertNotIn("sniper", ohne_abschnitt)
        # eine Liste gefüllt → gemessen (auch 0)
        teil = test_konfig(waffen={"sniper": [SNIPER], "nahkampf": [], "sonstige": []})
        r = merkmale.aus_replay(match([elim(100, ICH, "A", waffe=AR)]), [t(100)], teil)
        self.assertEqual((r["sniper"], r["nahkampf"]), (0.0, 0.0))

    def test_phase_ab_erster_aktion(self):
        # Umgehauen bei 200 s, Teammate erledigt bei 250 s: Kill-Zeit ist das Umhauen, die Phase auch
        m = match([elim(200, ICH, "A", knock=True), elim(250, "TEAM", "A")], laenge_s=1000)
        self.assertEqual(self.rechne(m, [t(200)])["phase"], 0.2)
        # selbst erledigt: Kill-Zeit 260 s, Aktion (mein Umhauen) 240 s → Phase 0,24
        m = match([elim(240, ICH, "A", knock=True), elim(260, ICH, "A")], laenge_s=1000)
        self.assertEqual(self.rechne(m, [t(260)])["phase"], 0.24)

    def test_phase_begrenzt_und_ohne_laenge(self):
        m = match([elim(1200, ICH, "A")], laenge_s=1000)  # Kill nach dem Replay-Ende (Uhr-Abweichung)
        self.assertEqual(self.rechne(m, [t(1200)])["phase"], 1.0)
        m = match([elim(100, ICH, "A")], laenge_s=0)
        self.assertEqual(self.rechne(m, [t(100)])["phase"], 0.0)

    def test_endgame_grenze_10_und_11(self):
        vorher = [elim(s, "X", f"Y{s}") for s in range(1, 90)]  # 89 fremde Eliminierungen
        # 100 Spieler − 89 − mein Kill = 10 übrig → Endgame
        self.assertEqual(self.rechne(match(vorher + [elim(100, ICH, "A")], spieler=100), [t(100)])["endgame"], 1.0)
        # 101 Spieler → 11 übrig → kein Endgame
        self.assertEqual(self.rechne(match(vorher + [elim(100, ICH, "A")], spieler=101), [t(100)])["endgame"], 0.0)
        # ohne spieler_gesamt unbekannt → 0
        self.assertEqual(self.rechne(match(vorher + [elim(100, ICH, "A")], spieler=None), [t(100)])["endgame"], 0.0)

    def test_endgame_reicht_ein_kill(self):
        vorher = [elim(s, "X", f"Y{s}") for s in range(1, 89)]  # 88
        m = match(vorher + [elim(95, ICH, "A"), elim(100, ICH, "B")], spieler=100)  # 11, dann 10 übrig
        self.assertEqual(self.rechne(m, [t(95), t(100)])["endgame"], 1.0)
        self.assertEqual(self.rechne(m, [t(95)])["endgame"], 0.0)

    def test_clutch_grenzen(self):
        def clutch(knock_vor: float, tod_nach: float | None, laenge_s: float = 1000) -> float:
            e = [elim(100 - knock_vor, "GEGNER", ICH, knock=True), elim(100, ICH, "A")]
            if tod_nach is not None:
                e.append(elim(100 + tod_nach, "GEGNER", ICH))
            return self.rechne(match(e, laenge_s=laenge_s), [t(100)])["clutch"]

        self.assertEqual(clutch(30, None), 1.0)    # 30 s vorher am Boden, nicht gestorben
        self.assertEqual(clutch(31, None), 0.0)    # zu lange her
        self.assertEqual(clutch(0, None), 1.0)     # im selben Moment am Boden: [T − 30, T] schließt T ein
        self.assertEqual(clutch(10, 10), 0.0)      # 10 s danach gestorben
        self.assertEqual(clutch(10, 11), 1.0)      # erst 11 s danach gestorben
        self.assertEqual(clutch(10, None, laenge_s=105), 1.0)  # Replay endet im Fenster: zählt als nicht gestorben

    def test_clutch_ohne_knock(self):
        self.assertEqual(self.rechne(match([elim(100, ICH, "A")]), [t(100)])["clutch"], 0.0)

    def test_team_wipe(self):
        m = match([
            elim(185, ICH, "O1", knock=True, waffe=SNIPER), elim(195, ICH, "O2", knock=True, waffe=SHOTGUN),
            elim(200, ICH, "O3", knock=True, waffe=SHOTGUN),
            elim(200, ICH, "O1", bot=True), elim(200, ICH, "O2", bot=True), elim(200, ICH, "O3", bot=False),
            elim(400, ICH, "Z", waffe=SNIPER, bot=True),  # anderer Kandidat
        ], laenge_s=1000)
        # drei Kills zur selben Zeit – kill_zeiten enthält die Zeit dreimal, jeder Kill zählt einmal
        r = self.rechne(m, [t(200), t(200), t(200)])
        self.assertEqual((round(r["bot_opfer"], 4), round(r["sniper"], 4), round(r["nahkampf"], 4)),
                         (0.6667, 0.3333, 0.6667))
        self.assertEqual(r["phase"], 0.185)  # erste Aktion = erstes Umhauen

    def test_zuordnung_toleriert_iso_rundung(self):
        m = match([elim(100.25, ICH, "A", bot=True)])
        genau = aus_iso(iso(t(100.25)))
        self.assertEqual(self.rechne(m, [genau + timedelta(microseconds=900)])["bot_opfer"], 1.0)
        # Befund K-5: 2 ms daneben = kein Ereignis zugeordnet → bot_opfer unbekannt (fehlt), nicht 0
        with self.assertLogs("pipeline", "WARNING"):
            self.assertNotIn("bot_opfer", self.rechne(m, [genau + timedelta(milliseconds=2)]))

    def test_rekorder_rueckfall_und_ohne_match(self):
        # Befund K-1: Unbekanntes fehlt (Leitplanke 4, S2-A17) statt als erfundene 0 dazustehen
        m = match([elim(100, ICH, "A", bot=True, waffe=SNIPER)], quelle=None, platz=2)
        self.assertEqual(self.rechne(m, [t(100)]), {"platzierung": 0.5})
        ohne_platz = match([elim(100, ICH, "A", bot=True, waffe=SNIPER)], quelle=None, platz=None)
        self.assertEqual(self.rechne(ohne_platz, [t(100)]), {})
        self.assertEqual(merkmale.aus_replay(None, [t(100)], self.K), {})

    def test_konfig_standardwerte(self):
        # Ohne [merkmale] gelten die Werte aus config/pipeline.toml als Rückfall (10 Spieler, 30 s, 10 s)
        vorher = [elim(s, "X", f"Y{s}") for s in range(1, 90)]
        m = match(vorher + [elim(95, "G", ICH, knock=True), elim(100, ICH, "A")], spieler=100)
        r = merkmale.aus_replay(m, [t(100)], Konfig({}, Path("x")))
        self.assertEqual((r["endgame"], r["clutch"]), (1.0, 1.0))


def _um(stunde: int) -> datetime:
    return lokal_zu_utc(datetime(2026, 9, 25, stunde, 0), "Europe/Berlin")


class UnbekannteWaffen(MitSpeicher):
    def setUp(self):
        super().setUp()
        self.konfig.daten["merkmale"]["waffen"] = dict(WAFFEN)
        self.konfig.daten.setdefault("telegram", {}).update(leise_von="23:00", leise_bis="08:00")

    def melde(self, sid, waffen):
        m = match([elim(100 + i, ICH, f"O{i}", waffe=w) for i, w in enumerate(waffen)])
        with db.transaktion(self.con):
            return merkmale.melde_unbekannte_waffen(self.con, self.konfig, sid, m)

    def meldungen(self):
        return {z["schluessel"]: z for z in self.con.execute("SELECT * FROM meldungen ORDER BY id")}

    def test_zwei_matches_mit_teils_gleichen_zahlen(self):
        self.assertEqual(self.melde("s1", [12, 27, 12, SNIPER, None, AR]), [12, 27])
        self.assertEqual(self.melde("s2", [27, 31, SHOTGUN]), [31])
        self.assertEqual(self.melde("s3", [27, 12]), [])  # nichts Neues → keine Meldung
        z = self.meldungen()
        self.assertEqual(sorted(z), ["merkmale:waffe:12", "merkmale:waffe:27", "merkmale:waffe:31",
                                     "merkmale:waffen:s1:12-27", "merkmale:waffen:s2:31"])  # Befund K-6
        self.assertEqual(z["merkmale:waffen:s1:12-27"]["text"],
                         "Neue Waffen-Nummern in Match s1: 12, 27 – zählen als sonstige. Eintragen in "
                         "config/lokal.toml [merkmale.waffen]; bestimmen mit `pipeline replay <datei>`, "
                         "docs/PUBLIKUM.md")
        self.assertIn("Match s2: 31 –", z["merkmale:waffen:s2:31"]["text"])
        for n in (12, 27, 31):  # Vermerke: gleich beim Anlegen als gesendet markiert
            vermerk = z[f"merkmale:waffe:{n}"]
            self.assertEqual(vermerk["gesendet"], vermerk["erstellt"])

    def test_vermerke_nie_faellig_sammelmeldung_wartet_die_ruhezeit(self):
        self.melde("s1", [12])
        tags = [z["schluessel"] for z in aktionen.faellige_meldungen(self.con, self.konfig, _um(9))]
        self.assertEqual(tags, ["merkmale:waffen:s1:12"])  # Befund K-6: Schlüssel mit den neuen Zahlen
        nachts = [z["schluessel"] for z in aktionen.faellige_meldungen(self.con, self.konfig, _um(1))]
        self.assertEqual(nachts, [])

    def test_wiederholung_meldet_nichts(self):
        self.assertEqual(self.melde("s1", [12]), [12])
        self.assertEqual(self.melde("s1", [12]), [])
        self.assertEqual(len(self.meldungen()), 2)

    def test_neue_zahl_derselben_session_wird_gemeldet(self):
        # Befund K-6: replay.json derselben Session neu erzeugt, jetzt mit einer weiteren Zahl → zweite Meldung
        self.assertEqual(self.melde("s1", [12]), [12])
        self.assertEqual(self.melde("s1", [12, 31]), [31])
        z = self.meldungen()
        self.assertEqual(sorted(k for k in z if k.startswith("merkmale:waffen:")),
                         ["merkmale:waffen:s1:12", "merkmale:waffen:s1:31"])
        self.assertIn("Match s1: 31 –", z["merkmale:waffen:s1:31"]["text"])

    def test_nur_meine_kills_und_ohne_replay(self):
        m = match([elim(100, "X", "Y", waffe=55), elim(110, ICH, "A", knock=True, waffe=66)])  # fremd, nur Knock
        self.assertEqual(merkmale.melde_unbekannte_waffen(self.con, self.konfig, "s1", m), [])
        rekorder = match([elim(100, ICH, "A", waffe=12)], quelle=None)
        self.assertEqual(merkmale.melde_unbekannte_waffen(self.con, self.konfig, "s1", rekorder), [])
        self.assertEqual(merkmale.melde_unbekannte_waffen(self.con, self.konfig, "s1", None), [])
        self.assertEqual(self.meldungen(), {})


GEWICHTE = {"kill_punkte": 1.0, "victory_royale": 5.0, "laenge": -0.5, "lautstaerke": 1.0, "kommentar": 1.0,
            "platzierung": 2.0, "sniper": 1.0, "nahkampf": 0.0, "bot_opfer": -2.0, "phase": 0.5, "endgame": 1.0,
            "clutch": 2.0}
CLIP_START = t(100)  # clip_anlegen legt die Kills bei Start + 10 s, + 13 s … an


class Nachtragen(MitSpeicher):
    def setUp(self):
        super().setUp()
        self.konfig.daten["merkmale"]["waffen"] = dict(WAFFEN)

    def replay(self, sid, eliminierungen, **mehr):
        ordner = self.konfig.ordner("sessions") / sid
        ordner.mkdir(parents=True, exist_ok=True)
        (ordner / "replay.json").write_text(json.dumps(daten(eliminierungen, **mehr)), encoding="utf-8")

    def trage_nach(self, **kw):
        return merkmale.nachtragen_replay(self.con, self.konfig, GEWICHTE, 3, **kw)

    def test_nachtrag_idempotent(self):
        self.replay("s1", [elim(110, ICH, "A", bot=True, waffe=12), elim(113, ICH, "B", bot=True, waffe=SNIPER)],
                    platz=4)
        cid = self.clip_anlegen(status="vorbewertet", start=CLIP_START, max_gruppe=2, match_id="s1",
                                merkmale={"kill_punkte": 3.0, "victory_royale": 0.0, "laenge": 0.0,
                                          "lautstaerke": 0.0, "kommentar": 0.0})
        ergebnis = self.trage_nach()
        self.assertEqual(ergebnis, {"clips": 1, "geaendert": 1, "ohne_replay": 0, "waffen_gemeldet": [12]})
        zeile = db.clip(self.con, cid)
        mk = json.loads(zeile["merkmale"])
        self.assertEqual((mk["bot_opfer"], mk["sniper"], mk["platzierung"]), (1.0, 0.5, 0.25))
        # 3 + Platz 0,25·2 + Sniper 0,5·1 − Bot 1·2 + Phase 0,11·0,5 = 2,055 (auf 2 Stellen gerundet)
        self.assertAlmostEqual(zeile["punkte"], 2.055, delta=0.006)
        self.assertEqual(zeile["gewichte_version"], 3)
        self.assertIn("Bot-Opfer", zeile["begruendung"])
        self.assertEqual(self.trage_nach(), {"clips": 0, "geaendert": 0, "ohne_replay": 0, "waffen_gemeldet": []})

    def test_waffen_erst_nach_kalibrierung(self):
        # Befund S-3: leere [merkmale.waffen] → sniper/nahkampf fehlen; nach dem Eintragen holt nachtragen sie nach
        self.konfig.daten["merkmale"]["waffen"] = {"sniper": [], "nahkampf": [], "sonstige": []}
        self.replay("s1", [elim(110, ICH, "A", waffe=SNIPER), elim(113, ICH, "B", waffe=SHOTGUN)])
        cid = self.clip_anlegen(status="vorbewertet", start=CLIP_START, max_gruppe=2, match_id="s1")
        self.assertEqual(self.trage_nach()["geaendert"], 1)
        mk = json.loads(db.clip(self.con, cid)["merkmale"])
        self.assertNotIn("sniper", mk)
        self.assertNotIn("nahkampf", mk)
        self.assertIn("bot_opfer", mk)
        self.konfig.daten["merkmale"]["waffen"] = dict(WAFFEN)
        self.assertEqual(self.trage_nach()["clips"], 1)  # fehlt noch → wird wieder versucht
        mk = json.loads(db.clip(self.con, cid)["merkmale"])
        self.assertEqual((mk["sniper"], mk["nahkampf"]), (0.5, 0.5))

    def test_ohne_replay_zaehlen_nicht_abbrechen(self):
        self.replay("s1", [elim(110, ICH, "A", bot=True)])
        ohne = self.clip_anlegen(status="vorbewertet", start=CLIP_START, match_id="s0")
        mit = self.clip_anlegen(status="vorbewertet", start=CLIP_START, match_id="s1")
        self.assertEqual(self.trage_nach(), {"clips": 2, "geaendert": 1, "ohne_replay": 1, "waffen_gemeldet": []})
        self.assertNotIn("bot_opfer", json.loads(db.clip(self.con, ohne)["merkmale"]))  # fehlt = unbekannt
        self.assertEqual(json.loads(db.clip(self.con, mit)["merkmale"])["bot_opfer"], 1.0)
        self.assertEqual(self.trage_nach()["ohne_replay"], 1)  # wird beim nächsten Mal wieder versucht

    def test_kaputte_replay_json_zaehlt_als_ohne(self):
        ordner = self.konfig.ordner("sessions") / "s1"
        ordner.mkdir(parents=True)
        (ordner / "replay.json").write_text("{kaputt", encoding="utf-8")
        self.clip_anlegen(status="vorbewertet", start=CLIP_START, match_id="s1")
        with self.assertLogs("pipeline", "WARNING"):
            self.assertEqual(self.trage_nach()["ohne_replay"], 1)

    def test_nur_eine_session_und_gesendete_behalten_punkte(self):
        self.replay("s1", [elim(110, ICH, "A", bot=True)])
        self.replay("s2", [elim(110, ICH, "A", bot=True)])
        gesendet = self.clip_anlegen(status="gesendet", start=CLIP_START, match_id="s1")
        andere = self.clip_anlegen(status="vorbewertet", start=CLIP_START, match_id="s2")
        self.assertEqual(self.trage_nach(session="s1")["clips"], 1)
        zeile = db.clip(self.con, gesendet)
        self.assertEqual((json.loads(zeile["merkmale"])["bot_opfer"], zeile["punkte"]), (1.0, 1))  # Annahme S2-A5
        self.assertNotIn("bot_opfer", json.loads(db.clip(self.con, andere)["merkmale"]))

    def test_weckt_nie(self):
        self.replay("s1", [elim(110, ICH, "A")])
        self.clip_anlegen(status="vorbewertet", start=CLIP_START, match_id="s1")
        self.clip_anlegen(status="vorbewertet", start=CLIP_START, match_id="s0")
        self.konfig.daten["speicher"].update(host="pve-gross", wol_mac="aa:bb:cc:dd:ee:ff")
        with mock.patch.object(konfig.Konfig, "_host_erreichbar", return_value=False) as erreichbar, \
                mock.patch("clip_pipeline.konfig.sende_wake_on_lan") as wol:
            self.trage_nach()
        wol.assert_not_called()
        erreichbar.assert_not_called()


class Analyze(MitSpeicher):
    """analyze schreibt die Replay-Merkmale in die Kandidaten: ein Clip mit Bot-Opfern bekommt weniger Punkte."""

    SID = "2026-09-21_21-00-00"

    def setUp(self):
        super().setUp()
        self.konfig.daten["merkmale"]["waffen"] = dict(WAFFEN)
        self.con.execute(
            "INSERT INTO matches (id, replay_pfad, start_utc, ende_utc, erstellt, geaendert)"
            " VALUES (?, 'replays/x.replay', ?, ?, 'x', 'x')", (self.SID, iso(START), iso(t(1000))))
        self.con.execute(
            """INSERT INTO aufnahmen (pfad, groesse, geaendert, quelle, start_utc, ende_utc, dauer_s, tonspuren, fps, erfasst)
               VALUES ('eingang/steelseries/a.mp4', 1, 0, 'steelseries', ?, ?, 120, 2, 30, 'x')""",
            (iso(t(60)), iso(t(180))))

    def analysiere(self, bot):
        m = match([elim(100, ICH, "A", bot=bot, waffe=42), elim(104, ICH, "B", bot=bot, waffe=SNIPER)], platz=3)
        with mock.patch("clip_pipeline.replay.lies", return_value=(m, {"test": True})):
            verarbeitung.analyze(self.con, self.konfig, self.SID)
        analyse = json.loads((verarbeitung.ordner(self.konfig, self.SID) / "analyse.json").read_text(encoding="utf-8"))
        return analyse["kandidaten"][0]

    def test_bot_opfer_kosten_punkte(self):
        mensch, bot = self.analysiere(False), self.analysiere(True)
        self.assertEqual((mensch["titel"], mensch["merkmale"]["kill_punkte"]), ("Double Kill", 3.0))
        self.assertEqual({m: bot["merkmale"][m] for m in REPLAY_MERKMALE if m != "bot_opfer"},
                         {m: mensch["merkmale"][m] for m in REPLAY_MERKMALE if m != "bot_opfer"})
        self.assertEqual((mensch["merkmale"]["bot_opfer"], bot["merkmale"]["bot_opfer"]), (0.0, 1.0))
        self.assertEqual(mensch["merkmale"]["sniper"], 0.5)
        self.assertAlmostEqual(mensch["punkte"] - bot["punkte"], 2.0)  # bot_opfer 1 × Startgewicht −2
        self.assertIn("Bot-Opfer", bot["begruendung"])
        # unbekannte Waffe 42: genau eine Sammelmeldung, auch nach dem zweiten analyze
        schluessel = [z[0] for z in self.con.execute("SELECT schluessel FROM meldungen ORDER BY id")]
        self.assertEqual(schluessel, ["merkmale:waffe:42", f"merkmale:waffen:{self.SID}:42"])  # Befund K-6

    def test_decide_reicht_durch(self):
        self.konfig.daten["decide"]["claude"] = False
        bot = self.analysiere(True)
        verarbeitung.decide(self.con, self.konfig, self.SID)
        liste = json.loads((verarbeitung.ordner(self.konfig, self.SID) / "schnittliste.json").read_text(encoding="utf-8"))
        self.assertEqual({m: liste["clips"][0]["merkmale"][m] for m in REPLAY_MERKMALE},
                         {m: bot["merkmale"][m] for m in REPLAY_MERKMALE})

    def test_rekorder_rueckfall(self):
        # Replay ohne erkannten Spieler (ich_quelle None): Kills kommen vom Rekorder, nur platzierung ist bekannt
        m = match([elim(100, ICH, "A", bot=True, waffe=42)], quelle=None, platz=2)
        self.con.execute("UPDATE aufnahmen SET ereignisse = ?",
                         (json.dumps([{"zeit": iso(t(100)), "art": "kill", "anzahl": 1, "quelle": "steelseries"}]),))
        with mock.patch("clip_pipeline.replay.lies", return_value=(m, {"test": True})):
            ergebnis = verarbeitung.analyze(self.con, self.konfig, self.SID)
        self.assertEqual((ergebnis["kill_quelle"], ergebnis["kandidaten"]), ("steelseries", 1))
        analyse = json.loads((verarbeitung.ordner(self.konfig, self.SID) / "analyse.json").read_text("utf-8"))
        mk = analyse["kandidaten"][0]["merkmale"]
        # Befund K-1: nur platzierung ist bekannt, die übrigen Replay-Merkmale fehlen (unbekannt, nicht 0)
        self.assertEqual({m: mk[m] for m in REPLAY_MERKMALE if m in mk}, {"platzierung": 0.5})
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM meldungen").fetchone()[0], 0)  # Waffe 42 nicht gemeldet


class CmdReplay(MitSpeicher):
    def test_zeigt_waffe_bot_verbleibend(self):
        roh = daten([elim(100, ICH, "A", bot=True, waffe=SNIPER), elim(200, ICH, "B", knock=True, waffe=SHOTGUN)],
                    spieler=50)
        m = match_aus_json(roh, "m", zonen_name="Europe/Berlin")
        ausgabe = io.StringIO()
        with mock.patch("clip_pipeline.replay.lies", return_value=(m, roh)), contextlib.redirect_stdout(ausgabe):
            self.assertEqual(cli._cmd_replay(SimpleNamespace(datei="x.replay"), self.konfig, self.con), 0)
        zeilen = [z for z in ausgabe.getvalue().splitlines() if z.startswith("  ")]
        self.assertIn("Waffe 7 · Bot ja · 49 übrig", zeilen[0])
        self.assertIn("Waffe 3 · Bot ? · 49 übrig", zeilen[1])


if __name__ == "__main__":
    unittest.main()
