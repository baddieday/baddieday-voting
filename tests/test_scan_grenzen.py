"""Mehrbenutzer, Stufe 1, Schritt 3 (M33–M35): `scan --verarbeiten --max N --versuche N` – Freunde laufen ohne n8n.

Je Lauf nur die N ältesten offenen Matches (die gemeinsame Sperre wird zwischen den Timer-Läufen frei); ein Match, das
N-mal scheitert, bekommt den Status 'fehler' und hält die neueren nicht mehr auf. Ohne die Schalter alles wie bisher
(Florian, n8n). verarbeitung.process ist gefälscht – geprüft wird die Steuerung, nicht das Schneiden.
"""

from __future__ import annotations

import contextlib
import io
import json
from datetime import datetime, timedelta
from unittest import mock

from clip_pipeline import cli, verarbeitung
from clip_pipeline.konfig import SpeicherOffline
from clip_pipeline.sperre import pfad as sperre_pfad, sperre
from clip_pipeline.zeit import UTC, iso

from tests.hilfen import MitSpeicher


class ScanGrenzen(MitSpeicher):
    def setUp(self):
        super().setUp()
        for i, mid in enumerate(("m1", "m2", "m3")):                    # m1 ist das älteste
            start = datetime(2026, 10, 8, 18 + i, 0, tzinfo=UTC)
            self.con.execute("INSERT INTO matches (id, replay_pfad, start_utc, ende_utc, erstellt, geaendert) "
                             "VALUES (?, ?, ?, ?, ?, ?)", (mid, f"replays/{mid}.replay", iso(start),
                                                           iso(start + timedelta(minutes=20)), iso(start), iso(start)))
        self.aufrufe: list[str] = []

    def falsch(self, scheitert=frozenset()):
        """Ersatz für verarbeitung.process: wie render setzt es 'verarbeitet'; Matches aus `scheitert` werfen."""
        def process(con, konfig, sid):
            self.aufrufe.append(sid)
            if sid in scheitert:
                raise verarbeitung.SessionFehler(f"Session {sid}: Replay kaputt")
            con.execute("UPDATE matches SET status = 'verarbeitet' WHERE id = ?", (sid,))
            return {"session": sid, "clips": 1, "neu": 1, "top_label": "Double Kill", "top_score": 3}
        return process

    def scan(self, *schalter, process=None) -> tuple[int, dict]:
        ausgabe = io.StringIO()
        with mock.patch("clip_pipeline.cli.lade", return_value=self.konfig), \
                mock.patch.object(verarbeitung, "process", side_effect=process or self.falsch()), \
                contextlib.redirect_stdout(ausgabe), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(["scan", "--verarbeiten", *schalter])
        zeilen = ausgabe.getvalue().splitlines()
        self.assertEqual(len(zeilen), 1, zeilen)                        # genau eine JSON-Zeile (Vertrag)
        return code, json.loads(zeilen[0])

    def status(self) -> dict[str, str]:
        return {z["id"]: z["status"] for z in self.con.execute("SELECT id, status FROM matches")}

    def fehlschlaege(self) -> int:
        return self.con.execute("SELECT COUNT(*) FROM ereignisse WHERE art = 'verarbeitung_fehler'").fetchone()[0]

    def test_max_eins_nur_das_aelteste_bisheriges_format_sperre_frei(self):
        code, ergebnis = self.scan("--max", "1")
        self.assertEqual(code, 0)
        self.assertEqual(set(ergebnis), {"speicher", "aufnahmen", "neue_matches", "offen", "verarbeitet"})
        self.assertEqual(ergebnis["offen"], ["m1", "m2", "m3"])         # „offen“ bleibt die volle Liste
        self.assertEqual(ergebnis["verarbeitet"][0]["top_score"], 3)
        self.assertEqual(self.aufrufe, ["m1"])
        self.assertEqual(self.status(), {"m1": "verarbeitet", "m2": "neu", "m3": "neu"})
        with sperre(sperre_pfad(self.konfig), warten_s=0):              # die Sperre ist nach dem Lauf frei
            pass

    def test_dreimal_gescheitert_fehler_lauf_vier_nimmt_das_naechste(self):
        for lauf in (1, 2, 3):
            code, ergebnis = self.scan("--max", "1", "--versuche", "3", process=self.falsch({"m1"}))
            eintrag = ergebnis["verarbeitet"][0]
            self.assertEqual((code, eintrag["session"], eintrag["versuch"]), (0, "m1", lauf))
            self.assertEqual(eintrag["fehler"], "Session m1: Replay kaputt")
        self.assertEqual(eintrag["status"], "fehler")
        self.assertEqual(self.status(), {"m1": "fehler", "m2": "neu", "m3": "neu"})
        self.assertEqual(self.fehlschlaege(), 3)                         # eine Zeile je Versuch
        meldung = self.con.execute("SELECT text FROM lern_meldungen WHERE schluessel = 'match_fehler:m1'").fetchone()
        self.assertEqual(meldung[0], "⚠️ Ein Match vom 08.10. um 20:00 Uhr klappt nicht – ich habe es 3-mal versucht "
                                     "und lasse es aus. Deine anderen Matches laufen normal weiter.")
        self.assertIn("SessionFehler: Session m1: Replay kaputt",
                      self.con.execute("SELECT hinweise FROM matches WHERE id = 'm1'").fetchone()[0])

        code, ergebnis = self.scan("--max", "1", "--versuche", "3", process=self.falsch({"m1"}))
        self.assertEqual((code, self.aufrufe[-1], ergebnis["offen"]), (0, "m2", ["m2", "m3"]))   # Lauf 4: das nächste

        # Nachholbar: das Match ist noch da; der letzte Schritt von `pipeline process m1` (render) macht es wieder fertig
        sitzung = verarbeitung.ordner(self.konfig, "m1")
        sitzung.mkdir(parents=True)
        (sitzung / "schnittliste.json").write_text('{"clips": [], "ohne_video": []}', encoding="utf-8")
        verarbeitung.render(self.con, self.konfig, "m1")
        self.assertEqual(self.status()["m1"], "verarbeitet")

    def test_ohne_schalter_bleibt_alles_wie_bisher(self):
        for _ in range(4):                                               # öfter als jede Grenze
            code, ergebnis = self.scan(process=self.falsch({"m1", "m2", "m3"}))
            self.assertEqual(code, 0)
        self.assertEqual(self.aufrufe, ["m1", "m2", "m3"] * 4)           # alle in jedem Lauf
        self.assertEqual(ergebnis["verarbeitet"][0], {"session": "m1", "fehler": "Session m1: Replay kaputt"})
        self.assertEqual(set(self.status().values()), {"neu"})           # nie 'fehler'
        self.assertEqual(self.fehlschlaege(), 0)

    def test_unerwartetes_zaehlt_nur_mit_versuche_speicher_offline_nie(self):
        def komisch(con, konfig, sid):
            raise ValueError("komische Replay-Daten")

        code, ergebnis = self.scan("--max", "1", process=komisch)        # ohne --versuche: wie bisher Exit 1
        self.assertEqual(code, 1)
        self.assertIn("ValueError", ergebnis["fehler"])
        code, ergebnis = self.scan("--max", "1", "--versuche", "3", process=komisch)
        self.assertEqual((code, ergebnis["verarbeitet"][0]["versuch"]), (0, 1))
        code, ergebnis = self.scan("--max", "1", "--versuche", "1",
                                   process=mock.Mock(side_effect=SpeicherOffline("Puffer nicht eingehängt")))
        self.assertEqual((code, ergebnis["fehler"]), (3, "speicher_offline"))   # liegt nicht am Match: zählt nie
        self.assertEqual((self.fehlschlaege(), self.status()["m1"]), (1, "neu"))
