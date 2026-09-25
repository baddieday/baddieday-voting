"""Mic-Merkmale in die Clips und der Hintergrundschritt nach render (Plan Stufe 2, Paket B; Spec §8.2, §12).

Geprüft wird:
  - der eine Weg über stimmung._speichere → mikro.in_clip_uebernehmen (mit/ohne Whisper, ohne Mikro-Spur,
    Datei-Moment ohne Clip bleibt unberührt)
  - Mic nachholen ohne Stimmungsverlust (Annahme S2-A18): Zeile mit quelle = "claude" behält Stimmung und Quelle
  - Zeilen mit Messfehler werden nicht wiederholt; Reihenfolge (Session zuerst, dann Punkte) und --max
  - anstossen: Anstoß-Datei für clip-mikro.path neben der DB, wann NICHT, kein Import von faster-whisper
  - n8n-Vertrag: stdout von `pipeline render` endet weiter mit genau einer JSON-Zeile; process/scan starten nichts
  - nichts davon weckt pve-big
Gewichte kommen aus der Test-Konfig ([vorbewertung.startgewichte] wird hier ausdrücklich gesetzt) – die Tests
verlassen sich nicht darauf, dass lernen.aktuelle fehlende Startgewichte auffüllt (Paket D).
"""

from __future__ import annotations

import contextlib
import io
import json
import builtins
import sqlite3
import unittest
from pathlib import Path
from unittest import mock

from clip_pipeline import cli, db, lernen, mikro, stimmung, verarbeitung
from clip_pipeline import konfig as konfig_modul
from clip_pipeline.zeit import iso

from tests.hilfen import HAT_FFMPEG, MitSpeicher
from tests.test_stimmung import START, video

SID = "2026-09-21_21-00-00"
ANDERE = "2026-09-20_20-00-00"
# Startgewichte für die Tests: bewusst ausgeschrieben, damit Punkte nachvollziehbar sind
GEWICHTE = {"kill_punkte": 1.0, "victory_royale": 1.0, "laenge": 0.0, "lautstaerke": 0.0, "kommentar": 0.0,
            "platzierung": 0.0, "sniper": 0.0, "nahkampf": 0.0, "bot_opfer": 0.0, "phase": 0.0, "endgame": 0.0,
            "clutch": 0.0, "mic_lachen": 1.0, "mic_jubel": 1.0, "mic_frust": 0.5, "mic_laut": 0.5, "spitzen": 0.25}


class Grundlage(MitSpeicher):
    def setUp(self):
        super().setUp()
        self.konfig.daten["material"] = {"ordner": str(self.tmp / "mini")}  # keine lokalen Kopien
        self.konfig.daten.setdefault("vorbewertung", {})["startgewichte"] = dict(GEWICHTE)
        self.konfig.daten.setdefault("merkmale", {}).update(mic=True, mic_je_lauf=3)

    def clip(self, *, match_id=SID, status="vorbewertet", punkte=1.0, datei=None, kills=1) -> int:
        cid = self.clip_anlegen(status=status, start=START, max_gruppe=kills, match_id=match_id)
        rel = f"sessions/{match_id}/clips/{cid}.mp4"
        if datei is None:  # Platzhalter: die Messung wird in diesen Tests ersetzt
            pfad = self.konfig.absolut(rel)
            pfad.parent.mkdir(parents=True, exist_ok=True)
            pfad.write_bytes(b"x")
        else:
            rel = self.konfig.relativ(datei)
        self.con.execute("UPDATE clips SET clip_pfad = ?, kills = ?, punkte = ?, quelle_start_s = 0, quelle_ende_s = 8"
                         " WHERE id = ?", (rel, kills, punkte, cid))
        return cid

    def moment_zeile(self, cid, merkmale, *, stimmung_="spannend", quelle="claude", text=None):
        self.con.execute(
            """INSERT INTO momente (schluessel, clip_id, match_id, datei, start_s, ende_s, start_utc, kills, stimmung,
                                    sicherheit, quelle, merkmale, text, erstellt, geaendert)
               VALUES (?, ?, ?, ?, 0, 8, ?, 1, ?, 0.2, ?, ?, ?, 'x', 'x')""",
            (f"clip:{cid}", cid, SID, "egal.mp4", iso(START), stimmung_, quelle, json.dumps(merkmale), text))

    def zeile(self, cid):
        return self.con.execute("SELECT * FROM momente WHERE clip_id = ?", (cid,)).fetchone()

    def clip_zeile(self, cid):
        return db.clip(self.con, cid)


# --- in_clip_uebernehmen ------------------------------------------------------------------------

class InClipUebernehmen(Grundlage):
    def test_mit_whisper_setzt_mic_stand_und_punkte(self):
        cid = self.clip()
        mk = {"mikro_spur": 1, "lachen": 5, "jubel": 1, "frust": 0, "jubel_laut": 2, "spitzen": 6}
        self.assertTrue(mikro.in_clip_uebernehmen(self.con, cid, mk, GEWICHTE, 4))
        c = self.clip_zeile(cid)
        merkmale = json.loads(c["merkmale"])
        self.assertEqual((merkmale["mic_lachen"], merkmale["mic_jubel"], merkmale["mic_frust"], merkmale["mic_laut"],
                          merkmale["spitzen"]), (3.0, 1.0, 0.0, 2.0, 4.0))
        self.assertIsNotNone(c["mic_stand"])
        # vorbewertet: Punkte neu = 1 (Kill) + 3 + 1 + 0 + 2·0,5 + 4·0,25 = 7
        self.assertEqual((c["punkte"], c["gewichte_version"]), (7.0, 4))
        # zweiter Aufruf: nichts mehr zu tun (idempotent)
        self.assertFalse(mikro.in_clip_uebernehmen(self.con, cid, mk, GEWICHTE, 4))

    def test_ohne_whisper_nur_laut_und_spitzen_kein_mic_stand(self):
        cid = self.clip()
        self.assertTrue(mikro.in_clip_uebernehmen(self.con, cid, {"mikro_spur": 1, "jubel_laut": 1, "spitzen": 2},
                                                  GEWICHTE, 0))
        c = self.clip_zeile(cid)
        merkmale = json.loads(c["merkmale"])
        self.assertEqual((merkmale["mic_laut"], merkmale["spitzen"]), (1.0, 2.0))
        self.assertNotIn("mic_lachen", merkmale)  # unbekannt, nicht 0
        self.assertIsNone(c["mic_stand"])

    def test_ohne_mikro_ist_vollstaendig_und_null(self):
        cid = self.clip()
        self.assertTrue(mikro.in_clip_uebernehmen(self.con, cid, {"mikro_spur": None, "spitzen": 1}, GEWICHTE, 0))
        c = self.clip_zeile(cid)
        merkmale = json.loads(c["merkmale"])
        self.assertEqual([merkmale[k] for k in ("mic_lachen", "mic_jubel", "mic_frust", "mic_laut")], [0.0] * 4)
        self.assertIsNotNone(c["mic_stand"])

    def test_messfehler_setzt_keinen_mic_stand(self):
        cid = self.clip()
        self.assertFalse(mikro.in_clip_uebernehmen(self.con, cid, {"fehler": "kaputt"}, GEWICHTE, 0))
        self.assertIsNone(self.clip_zeile(cid)["mic_stand"])

    def test_unbekannter_clip(self):
        self.assertFalse(mikro.in_clip_uebernehmen(self.con, 999, {"mikro_spur": None}, GEWICHTE, 0))


# --- stimmung._speichere / _ergaenze_mic (ohne FFmpeg) -------------------------------------------

class Speichern(Grundlage):
    def test_datei_moment_ohne_clip_bleibt_unberuehrt(self):
        cid = self.clip()
        vorher = dict(self.clip_zeile(cid))
        m = stimmung.Moment("datei:eingang/a.mp4", self.tmp / "a.mp4", 0.0, 5.0)
        with mock.patch.object(stimmung.mikro, "in_clip_uebernehmen") as uebernehmen:
            stimmung._speichere(self.con, m, "chill", 0.5, "regel", {"mikro_spur": None, "lachen": 2}, None,
                                gewichte=GEWICHTE, version=0)
        uebernehmen.assert_not_called()
        self.assertEqual(dict(self.clip_zeile(cid)), vorher)
        self.assertIsNotNone(self.con.execute("SELECT 1 FROM momente WHERE schluessel = 'datei:eingang/a.mp4'").fetchone())

    def test_clip_moment_geht_in_den_clip(self):
        cid = self.clip()
        m = stimmung.Moment(f"clip:{cid}", self.tmp / "a.mp4", 0.0, 5.0, START, cid, SID)
        stimmung._speichere(self.con, m, "lustig", 0.5, "regel", {"mikro_spur": 1, "lachen": 1}, "haha",
                            gewichte=GEWICHTE, version=2)
        c = self.clip_zeile(cid)
        self.assertEqual(json.loads(c["merkmale"])["mic_lachen"], 1.0)
        self.assertIsNotNone(c["mic_stand"])
        self.assertEqual(c["gewichte_version"], 2)

    def test_ergaenze_mic_nimmt_nur_mic_schluessel(self):
        cid = self.clip()
        alt = {"kills": 3, "mikro_spur": 1, "jubel_laut": 0, "spitzen": 2, "energie": 0.4, "punkte": {"episch": 1.0}}
        self.moment_zeile(cid, alt, stimmung_="episch", quelle="claude")
        m = stimmung.Moment(f"clip:{cid}", self.tmp / "a.mp4", 0.0, 8.0, START, cid, SID)
        neu = {"kills": 99, "mikro_spur": 1, "jubel_laut": 2, "jubel_laut_s": [1.0, 3.0], "spitzen": 7,
               "energie": 0.9, "lachen": 2, "jubel": 1, "frust": 0}
        stimmung._ergaenze_mic(self.con, m, neu, "haha geil", gewichte=GEWICHTE, version=0)
        z = self.zeile(cid)
        mk = json.loads(z["merkmale"])
        self.assertEqual((z["stimmung"], z["quelle"], z["sicherheit"], z["text"]), ("episch", "claude", 0.2, "haha geil"))
        self.assertEqual((mk["kills"], mk["spitzen"], mk["energie"], mk["punkte"]), (3, 2, 0.4, {"episch": 1.0}))
        self.assertEqual((mk["lachen"], mk["jubel"], mk["frust"], mk["jubel_laut"], mk["jubel_laut_s"]),
                         (2, 1, 0, 2, [1.0, 3.0]))
        self.assertIsNotNone(self.clip_zeile(cid)["mic_stand"])

    def test_ergaenze_mic_messfehler_schreibt_nur_fehler(self):
        # Befund K-2: Früher blieb die Zeile ganz unverändert – ohne `fehler` wählte der nächste Lauf sie wieder
        # (Endlosschleife). Jetzt kommt nur `fehler` dazu; Stimmung, Quelle, Text und die alten Werte bleiben.
        cid = self.clip()
        self.moment_zeile(cid, {"mikro_spur": 1, "jubel_laut": 0}, text="alt")
        vorher = dict(self.zeile(cid))
        m = stimmung.Moment(f"clip:{cid}", self.tmp / "a.mp4", 0.0, 8.0, START, cid, SID)
        stimmung._ergaenze_mic(self.con, m, {"kills": 1, "fehler": "ffprobe kaputt"}, None, gewichte=GEWICHTE,
                               version=0)
        nachher = dict(self.zeile(cid))
        self.assertEqual(json.loads(nachher.pop("merkmale")),
                         {"mikro_spur": 1, "jubel_laut": 0, "fehler": "ffprobe kaputt"})
        vorher.pop("merkmale")
        vorher.pop("geaendert")
        nachher.pop("geaendert")
        self.assertEqual(nachher, vorher)  # stimmung, quelle, sicherheit, text unverändert
        self.assertIsNone(self.clip_zeile(cid)["mic_stand"])

    def test_speichere_halb_geschrieben_gibt_es_nicht(self):
        # Befund K-4: momente-Zeile und Clip gehören zusammen – scheitert der Clip, fehlt auch die Zeile
        cid = self.clip()
        m = stimmung.Moment(f"clip:{cid}", self.tmp / "a.mp4", 0.0, 5.0, START, cid, SID)
        with mock.patch.object(stimmung.mikro, "in_clip_uebernehmen", side_effect=sqlite3.OperationalError("weg")), \
                self.assertRaises(sqlite3.OperationalError):
            stimmung._speichere(self.con, m, "lustig", 0.5, "regel", {"mikro_spur": None}, None,
                                gewichte=GEWICHTE, version=0)
        self.assertIsNone(self.zeile(cid))
        self.assertFalse(self.con.in_transaction)

    def test_ergaenze_mic_halb_geschrieben_gibt_es_nicht(self):
        cid = self.clip()
        self.moment_zeile(cid, {"mikro_spur": 1, "jubel_laut": 0})
        vorher = dict(self.zeile(cid))
        m = stimmung.Moment(f"clip:{cid}", self.tmp / "a.mp4", 0.0, 8.0, START, cid, SID)
        with mock.patch.object(stimmung.mikro, "in_clip_uebernehmen", side_effect=sqlite3.OperationalError("weg")), \
                self.assertRaises(sqlite3.OperationalError):
            stimmung._ergaenze_mic(self.con, m, {"mikro_spur": 1, "lachen": 2, "jubel": 0, "frust": 0}, "haha",
                                   gewichte=GEWICHTE, version=0)
        self.assertEqual(dict(self.zeile(cid)), vorher)
        self.assertFalse(self.con.in_transaction)

    def test_uebernehmen_halb_geschrieben_gibt_es_nicht(self):
        # Befund K-4: clips.merkmale und mic_stand eines Clips ändern sich zusammen oder gar nicht.
        # mikro.iso wird nur beim mic_stand-UPDATE gerufen – also NACH aktualisiere_clip.
        cid = self.clip()
        self.moment_zeile(cid, {"mikro_spur": 1, "lachen": 2, "jubel": 0, "frust": 0, "jubel_laut": 0})
        vorher = dict(self.clip_zeile(cid))
        with mock.patch.object(mikro, "iso", side_effect=RuntimeError("mitten drin")), \
                self.assertRaises(RuntimeError):
            mikro.nachtragen(self.con, self.konfig, GEWICHTE, 0)
        self.assertEqual(dict(self.clip_zeile(cid)), vorher)  # kein mic_lachen ohne mic_stand
        self.assertFalse(self.con.in_transaction)

    def test_analysiere_holt_gewichte_einmal(self):
        for _ in range(3):
            self.clip()
        with mock.patch.object(stimmung, "merkmale", return_value=({"mikro_spur": None}, None)), \
                mock.patch.object(stimmung.lernen, "aktuelle", return_value=(5, GEWICHTE)) as aktuelle:
            e = stimmung.analysiere(self.con, self.konfig, claude=False, whisper=False)
        self.assertEqual(e["analysiert"], 3)
        aktuelle.assert_called_once()
        versionen = {z["gewichte_version"] for z in self.con.execute("SELECT gewichte_version FROM clips")}
        self.assertEqual(versionen, {5})


# --- clips_nachziehen und nachtragen (Messung ersetzt) -------------------------------------------

def _gemessen(ohne_mikro=True):
    """Ersatz für stimmung.merkmale: merkt sich die Reihenfolge der Clips."""
    reihenfolge = []

    def messe(m, konfig, sprache):
        reihenfolge.append(m.clip_id)
        return ({"kills": m.kills, "mikro_spur": None, "spitzen": 1} if ohne_mikro
                else {"kills": m.kills, "mikro_spur": 1, "jubel_laut": 1, "spitzen": 1}), None

    return reihenfolge, messe


class Nachziehen(Grundlage):
    def test_reihenfolge_session_zuerst_dann_punkte_und_max(self):
        andere_9 = self.clip(match_id=ANDERE, punkte=9)
        andere_5 = self.clip(match_id=ANDERE, punkte=5)
        session_1 = self.clip(match_id=SID, punkte=1)
        verworfen = self.clip(match_id=SID, punkte=20, status="verworfen")
        reihenfolge, messe = _gemessen()
        with mock.patch.object(stimmung, "merkmale", side_effect=messe), \
                mock.patch.object(mikro, "whisper_da", return_value=False):
            e = mikro.clips_nachziehen(self.con, self.konfig, session=SID, maximal=2)
        self.assertEqual(reihenfolge, [session_1, andere_9])
        self.assertEqual(e, {"uebernommen": 0, "analysiert": 2, "mit_fehler": 0, "offen": 1})
        self.assertIsNone(self.clip_zeile(andere_5)["mic_stand"])
        self.assertIsNone(self.clip_zeile(verworfen)["mic_stand"])
        # ohne --max: [merkmale].mic_je_lauf
        self.konfig.daten["merkmale"]["mic_je_lauf"] = 1
        reihenfolge.clear()
        with mock.patch.object(stimmung, "merkmale", side_effect=messe), \
                mock.patch.object(mikro, "whisper_da", return_value=False):
            e = mikro.clips_nachziehen(self.con, self.konfig)
        self.assertEqual((reihenfolge, e["analysiert"], e["offen"]), ([andere_5], 1, 0))

    def test_neue_zeilen_ohne_claude(self):
        cid = self.clip()
        _, messe = _gemessen()
        with mock.patch.object(stimmung, "merkmale", side_effect=messe), \
                mock.patch.object(mikro, "whisper_da", return_value=False), \
                mock.patch.object(stimmung, "frage_claude") as claude:
            mikro.clips_nachziehen(self.con, self.konfig)
        claude.assert_not_called()
        self.assertEqual(self.zeile(cid)["quelle"], "regel")
        self.assertIsNotNone(self.clip_zeile(cid)["mic_stand"])

    def test_vorhandene_zeile_nur_uebernehmen(self):
        fertig = self.clip()
        self.moment_zeile(fertig, {"mikro_spur": 1, "lachen": 1, "jubel": 0, "frust": 0, "jubel_laut": 0})
        with mock.patch.object(stimmung, "merkmale") as messung, \
                mock.patch.object(mikro, "whisper_da", return_value=False):
            e = mikro.clips_nachziehen(self.con, self.konfig)
        messung.assert_not_called()
        self.assertEqual((e["uebernommen"], e["analysiert"], e["offen"]), (1, 0, 0))
        self.assertIsNotNone(self.clip_zeile(fertig)["mic_stand"])

    def test_zeile_mit_fehler_wird_nicht_wiederholt(self):
        kaputt = self.clip()
        self.moment_zeile(kaputt, {"kills": 1, "fehler": "ffprobe kaputt"})
        with mock.patch.object(stimmung, "merkmale") as messung, \
                mock.patch.object(mikro, "whisper_da", return_value=True), \
                mock.patch.object(stimmung.Transkription, "verfuegbar", return_value=True):
            e = mikro.clips_nachziehen(self.con, self.konfig)
            e2 = mikro.clips_nachziehen(self.con, self.konfig)
            stimmung.analysiere(self.con, self.konfig, claude=False, nur_clips=[kaputt], nur_mic=True)
        messung.assert_not_called()
        self.assertEqual(e, {"uebernommen": 0, "analysiert": 0, "mit_fehler": 0, "offen": 1})  # bleibt sichtbar
        self.assertEqual(e2, e)

    def test_unvollstaendige_zeile_ohne_whisper_bleibt_offen(self):
        cid = self.clip()
        self.moment_zeile(cid, {"mikro_spur": 1, "jubel_laut": 1})
        with mock.patch.object(stimmung, "merkmale") as messung, \
                mock.patch.object(mikro, "whisper_da", return_value=False):
            e = mikro.clips_nachziehen(self.con, self.konfig)
        messung.assert_not_called()
        self.assertEqual((e["uebernommen"], e["analysiert"], e["offen"]), (1, 0, 1))  # mic_laut übernommen
        self.assertEqual(json.loads(self.clip_zeile(cid)["merkmale"])["mic_laut"], 1.0)

    def test_messfehler_wird_gezaehlt(self):
        cid = self.clip()
        with mock.patch.object(stimmung, "merkmale", return_value=({"kills": 1, "fehler": "kaputt"}, None)), \
                mock.patch.object(mikro, "whisper_da", return_value=False):
            e = mikro.clips_nachziehen(self.con, self.konfig)
        self.assertEqual((e["analysiert"], e["mit_fehler"], e["offen"]), (1, 1, 1))
        self.assertIsNone(self.clip_zeile(cid)["mic_stand"])

    def test_messfehler_in_lautheit_bricht_den_lauf_nicht_ab(self):
        # Befund K-2: Ein kaputter Clip (ffmpeg scheitert bei der Lautheit) hielt den ganzen Lauf an – auch die
        # schon gemessenen Momente gingen verloren. Jetzt: `fehler` am kaputten Clip, die anderen sind gespeichert.
        kaputt = self.clip(punkte=9)
        gut = self.clip(punkte=1)

        def lautheit(datei, spur):
            if datei.name == f"{kaputt}.mp4":
                raise stimmung.MedienFehler("Lautheit-Verlauf kaputt: Exit 1")
            return [(0.0, -30.0)] * 10

        with mock.patch.object(stimmung.bestand, "tonspuren", return_value={"spuren": [{"nr": 0}, {"nr": 1}]}), \
                mock.patch.object(stimmung.bestand, "mikro_spur", return_value=1), \
                mock.patch.object(stimmung, "lautheit_verlauf", side_effect=lautheit), \
                mock.patch.object(stimmung.Transkription, "verfuegbar", return_value=False), \
                mock.patch.object(mikro, "whisper_da", return_value=False):
            e = mikro.clips_nachziehen(self.con, self.konfig)
        self.assertEqual((e["analysiert"], e["mit_fehler"]), (2, 1))
        self.assertIn("Lautheit-Verlauf kaputt", json.loads(self.zeile(kaputt)["merkmale"])["fehler"])
        gut_mk = json.loads(self.zeile(gut)["merkmale"])
        self.assertNotIn("fehler", gut_mk)
        self.assertEqual(gut_mk["jubel_laut"], 0)
        # nächster Lauf mit Whisper: nur der gute Clip wird nachgeholt, der kaputte belegt keinen Platz mehr
        reihenfolge, messe = _gemessen(ohne_mikro=False)
        with mock.patch.object(stimmung, "merkmale", side_effect=messe), \
                mock.patch.object(mikro, "whisper_da", return_value=True), \
                mock.patch.object(stimmung.Transkription, "verfuegbar", return_value=True):
            mikro.clips_nachziehen(self.con, self.konfig)
        self.assertEqual(reihenfolge, [gut])

    def test_nachholen_mit_messfehler_wird_nicht_wiederholt(self):
        # Befund K-2: Scheitert das Nachholen, bekommt die Zeile `fehler` – sonst würde sie jeden Lauf neu gewählt
        cid = self.clip()
        self.moment_zeile(cid, {"kills": 1, "mikro_spur": 1, "jubel_laut": 0})
        with mock.patch.object(stimmung, "merkmale", return_value=({"kills": 1, "fehler": "kaputt"}, None)) as messung, \
                mock.patch.object(mikro, "whisper_da", return_value=True), \
                mock.patch.object(stimmung.Transkription, "verfuegbar", return_value=True):
            e1 = mikro.clips_nachziehen(self.con, self.konfig)
            e2 = mikro.clips_nachziehen(self.con, self.konfig)
        self.assertEqual(messung.call_count, 1)
        self.assertEqual((e1["analysiert"], e1["mit_fehler"], e1["offen"]), (1, 1, 1))
        self.assertEqual((e2["analysiert"], e2["offen"]), (0, 1))
        z = self.zeile(cid)
        self.assertEqual((z["stimmung"], z["quelle"]), ("spannend", "claude"))
        self.assertEqual(json.loads(z["merkmale"])["fehler"], "kaputt")

    def test_offen_ist_dieselbe_zahl_wie_in_gewichte(self):
        # Befund E-5: „offen“ und „ohne Mic-Analyse“ in /gewichte kommen aus derselben Funktion
        self.clip()
        self.clip(status="verworfen")
        fertig = self.clip()
        self.moment_zeile(fertig, {"mikro_spur": None})
        with mock.patch.object(stimmung, "merkmale", return_value=({"kills": 1, "fehler": "kaputt"}, None)), \
                mock.patch.object(mikro, "whisper_da", return_value=False):
            e = mikro.clips_nachziehen(self.con, self.konfig)
        self.assertEqual(e["offen"], 1)
        self.assertEqual(db.ohne_mic_analyse(self.con), 1)
        self.assertEqual(lernen.berechne(self.con, self.konfig).ohne_mic, e["offen"])

    def test_nachtragen_nur_uebernehmen_und_session(self):
        hier = self.clip(match_id=SID)
        dort = self.clip(match_id=ANDERE)
        ohne_zeile = self.clip(match_id=SID)
        for cid in (hier, dort):
            self.moment_zeile(cid, {"mikro_spur": None, "spitzen": 3})
        with mock.patch.object(stimmung, "merkmale") as messung:
            e = mikro.nachtragen(self.con, self.konfig, GEWICHTE, 3, session=SID)
            messung.assert_not_called()
            self.assertEqual(e, {"clips": 1, "geaendert": 1})
            self.assertIsNotNone(self.clip_zeile(hier)["mic_stand"])
            self.assertIsNone(self.clip_zeile(dort)["mic_stand"])
            self.assertIsNone(self.clip_zeile(ohne_zeile)["mic_stand"])
            self.assertEqual(self.clip_zeile(hier)["gewichte_version"], 3)
            self.assertEqual(mikro.nachtragen(self.con, self.konfig, GEWICHTE, 3), {"clips": 1, "geaendert": 1})
            self.assertEqual(mikro.nachtragen(self.con, self.konfig, GEWICHTE, 3), {"clips": 0, "geaendert": 0})


# --- mit FFmpeg: echte Messung, gefälschte Transkription ------------------------------------------

@unittest.skipUnless(HAT_FFMPEG, "ffmpeg fehlt")
class MitVideo(Grundlage):
    def setUp(self):
        super().setUp()
        clips = self.konfig.ordner("sessions") / SID / "clips"
        self.cid = self.clip(datei=video(clips / "1.mp4", spiel_knall_s=(1, 3, 5)), kills=1)

    def _transkript(self, text="Hahaha, hahaha, geil"):
        return (mock.patch.object(stimmung.Transkription, "text", return_value=text),
                mock.patch.object(stimmung.Transkription, "verfuegbar", return_value=True),
                mock.patch.object(stimmung.bestand, "mikro_spur", return_value=1))

    def test_speichere_weg_mit_whisper(self):
        a, b, c = self._transkript()
        with a, b, c:
            stimmung.analysiere(self.con, self.konfig, claude=False)
        clip = self.clip_zeile(self.cid)
        mk = json.loads(clip["merkmale"])
        self.assertEqual((mk["mic_lachen"], mk["mic_jubel"], mk["spitzen"]), (2.0, 1.0, 3.0))
        self.assertIsNotNone(clip["mic_stand"])

    def test_speichere_weg_ohne_whisper(self):
        with mock.patch.object(stimmung.bestand, "mikro_spur", return_value=1):
            stimmung.analysiere(self.con, self.konfig, claude=False, whisper=False)
        clip = self.clip_zeile(self.cid)
        mk = json.loads(clip["merkmale"])
        self.assertEqual((mk["spitzen"], mk["mic_laut"]), (3.0, 0.0))
        self.assertNotIn("mic_lachen", mk)
        self.assertIsNone(clip["mic_stand"])

    def test_speichere_weg_ohne_mikro_spur(self):
        with mock.patch.object(stimmung.bestand, "mikro_spur", return_value=None):
            stimmung.analysiere(self.con, self.konfig, claude=False, whisper=False)
        clip = self.clip_zeile(self.cid)
        mk = json.loads(clip["merkmale"])
        self.assertEqual([mk[k] for k in ("mic_lachen", "mic_jubel", "mic_frust", "mic_laut")], [0.0] * 4)
        self.assertIsNotNone(clip["mic_stand"])

    def test_mic_nachholen_ohne_stimmungsverlust(self):
        # Zeile von früher: Claude hat „spannend“ gewählt, Whisper lief damals nicht (kein `lachen`)
        self.moment_zeile(self.cid, {"kills": 1, "mikro_spur": 1, "jubel_laut": 0, "spitzen": 3, "energie": 0.5,
                                     "regel": "episch"}, stimmung_="spannend", quelle="claude")
        a, b, c = self._transkript()
        with a, b, c, mock.patch.object(mikro, "whisper_da", return_value=True), \
                mock.patch.object(stimmung, "frage_claude") as claude:
            e = mikro.clips_nachziehen(self.con, self.konfig, session=SID)
        claude.assert_not_called()
        self.assertEqual((e["analysiert"], e["offen"]), (1, 0))
        z = self.zeile(self.cid)
        mk = json.loads(z["merkmale"])
        self.assertEqual((z["stimmung"], z["quelle"], z["sicherheit"]), ("spannend", "claude", 0.2))
        self.assertIn("Hahaha", z["text"])
        self.assertEqual((mk["lachen"], mk["regel"], mk["energie"]), (2, "episch", 0.5))
        self.assertIsNotNone(self.clip_zeile(self.cid)["mic_stand"])
        # zweiter Lauf: nichts mehr offen, keine neue Messung
        with mock.patch.object(stimmung, "merkmale") as messung, mock.patch.object(mikro, "whisper_da", return_value=True):
            self.assertEqual(mikro.clips_nachziehen(self.con, self.konfig)["analysiert"], 0)
        messung.assert_not_called()


# --- Hintergrundschritt ----------------------------------------------------------------------------

class MitPuffer(Grundlage):
    def setUp(self):
        super().setUp()
        self.konfig.daten["lager"]["wurzel"] = str(self.tmp / "lager")  # getrennter Betrieb (Puffer)
        self.konfig.quelle = self.tmp / "test.toml"


def verbiete_faster_whisper():
    """Befund T-3: Jeder Import von faster_whisper wirft AssertionError – unabhängig davon, ob ein früherer Test das
    Paket schon geladen hat (sys.modules zu prüfen wäre reihenfolgeabhängig)."""
    echt = builtins.__import__

    def importiere(name, *args, **kwargs):
        if name == "faster_whisper" or name.startswith("faster_whisper."):
            raise AssertionError("faster_whisper darf hier nicht importiert werden")
        return echt(name, *args, **kwargs)

    return mock.patch.object(builtins, "__import__", side_effect=importiere)


class Hintergrund(MitPuffer):

    def test_whisper_da_importiert_nicht(self):
        spec = mock.sentinel.spec
        with mock.patch("importlib.util.find_spec", return_value=spec) as finde, verbiete_faster_whisper():
            self.assertTrue(mikro.whisper_da())
        finde.assert_called_once_with("faster_whisper")
        with mock.patch("importlib.util.find_spec", return_value=None):
            self.assertFalse(mikro.whisper_da())

    def anstoss(self) -> Path:
        return self.konfig.datenbank.parent / mikro.ANSTOSS_NAME

    def test_anstoss_datei_neben_der_datenbank(self):
        # S2-R3 (Florian: systemd): render startet keinen Prozess mehr, es schreibt nur die Anstoß-Datei, auf die
        # clip-mikro.path wartet
        with mock.patch("subprocess.Popen") as popen:
            self.assertTrue(mikro.anstossen(self.konfig, SID))
            self.assertTrue(mikro.anstossen(self.konfig, "2026-09-25_21-00-00"))
        popen.assert_not_called()
        self.assertEqual(self.anstoss().read_text(encoding="utf-8").split()[0], "2026-09-25_21-00-00")

    def test_kein_anstoss(self):
        for name, mic, lager in (("mic aus", False, "x"), ("ohne Puffer", True, "")):
            with self.subTest(name):
                self.konfig.daten["merkmale"]["mic"] = mic
                self.konfig.daten["lager"]["wurzel"] = lager
                self.assertFalse(mikro.anstossen(self.konfig, SID))
                self.assertFalse(self.anstoss().exists())

    def test_schreibfehler_nur_warnung(self):
        self.konfig.daten["datenbank"]["pfad"] = str(self.tmp / "gibt-es-nicht" / "x.db")
        with self.assertLogs("pipeline", "WARNING"):
            self.assertFalse(mikro.anstossen(self.konfig, SID))


class RenderVertrag(MitPuffer):
    """cli: nur render mit neu > 0 stößt den Mic-Schritt an; stdout bleibt genau eine JSON-Zeile."""

    def _cli(self, *argv) -> tuple[int, list[str]]:
        ausgabe = io.StringIO()
        with mock.patch.object(cli, "lade", return_value=self.konfig), \
                contextlib.redirect_stdout(ausgabe), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(list(argv))
        return code, ausgabe.getvalue().splitlines()

    def _render(self, neu):
        ergebnis = {"session": SID, "clips": 2, "neu": neu, "top_label": "Double Kill", "top_score": 3}
        with mock.patch.object(verarbeitung, "render", return_value=ergebnis), \
                mock.patch("subprocess.Popen") as popen:
            code, zeilen = self._cli("render", "--session", SID)
        popen.assert_not_called()
        return code, zeilen

    def test_render_stoesst_an_und_eine_json_zeile(self):
        code, zeilen = self._render(2)
        self.assertEqual((code, len(zeilen)), (0, 1))
        self.assertEqual(json.loads(zeilen[-1])["top_score"], 3)
        self.assertTrue((self.konfig.datenbank.parent / mikro.ANSTOSS_NAME).is_file())

    def test_render_ohne_neue_clips_stoesst_nicht_an(self):
        code, zeilen = self._render(0)
        self.assertEqual((code, len(zeilen)), (0, 1))
        self.assertFalse((self.konfig.datenbank.parent / mikro.ANSTOSS_NAME).exists())

    def test_process_und_scan_stossen_nicht_an(self):
        ergebnis = {"session": SID, "clips": 2, "neu": 2}
        with mock.patch.object(verarbeitung, "process", return_value=ergebnis), \
                mock.patch.object(cli.erfassung, "scan", return_value={"offen": [SID]}), \
                mock.patch.object(mikro, "anstossen") as anstossen:
            self.assertEqual(self._cli("process", SID)[0], 0)
            self.assertEqual(self._cli("scan", "--verarbeiten")[0], 0)
        anstossen.assert_not_called()


class WecktNie(MitPuffer):
    def test_nichts_weckt(self):
        cid = self.clip()
        self.moment_zeile(cid, {"mikro_spur": None})
        self.clip()
        _, messe = _gemessen()
        self.konfig.daten["speicher"].update(host="pve-gross", wol_mac="aa:bb:cc:dd:ee:ff")
        with mock.patch.object(konfig_modul.Konfig, "_host_erreichbar", return_value=False) as host, \
                mock.patch("clip_pipeline.konfig.sende_wake_on_lan") as wol, \
                mock.patch.object(stimmung, "merkmale", side_effect=messe), \
                mock.patch.object(mikro, "whisper_da", return_value=True), \
                mock.patch.object(stimmung.Transkription, "verfuegbar", return_value=False):
            mikro.clips_nachziehen(self.con, self.konfig)
            mikro.nachtragen(self.con, self.konfig, GEWICHTE, 0)
            mikro.in_clip_uebernehmen(self.con, cid, {"mikro_spur": None}, GEWICHTE, 0)
            mikro.anstossen(self.konfig, SID)
        wol.assert_not_called()
        host.assert_not_called()


class DienstDateien(unittest.TestCase):
    """clip-mikro.path/.service/.timer passen zum Code (Rückfrage S2-R3: systemd statt Kindprozess)."""

    SYSTEMD = Path(__file__).resolve().parent.parent / "deploy" / "systemd"

    def test_path_wartet_auf_die_anstoss_datei_neben_der_datenbank(self):
        import tomllib
        pipeline = tomllib.loads((self.SYSTEMD.parent.parent / "config" / "pipeline.toml").read_text(encoding="utf-8"))
        db_ordner = Path(pipeline["datenbank"]["pfad"]).parent
        text = (self.SYSTEMD / "clip-mikro.path").read_text(encoding="utf-8")
        self.assertIn(f"PathChanged={db_ordner / mikro.ANSTOSS_NAME}", text)
        self.assertIn("Unit=clip-mikro.service", text)

    def test_dienst_ruft_den_mic_schritt(self):
        text = (self.SYSTEMD / "clip-mikro.service").read_text(encoding="utf-8")
        self.assertIn("ExecStart=/opt/clip-pipeline/.venv/bin/pipeline stimmung --clips", text)
        self.assertIn("SuccessExitStatus=4", text)  # Sperre belegt ist kein Fehler
        self.assertIn("Nice=15", text)
        self.assertIn("OnUnitActiveSec=30min", (self.SYSTEMD / "clip-mikro.timer").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
