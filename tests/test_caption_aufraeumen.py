import contextlib
import io
import json
import os
import time
import unittest
from datetime import datetime, timedelta
from unittest import mock

from clip_pipeline import aufraeumen, caption, cli, db
from clip_pipeline.konfig import KonfigFehler
from clip_pipeline.sperre import Gesperrt, sperre
from clip_pipeline.zeit import UTC

from tests.hilfen import MitSpeicher


class Caption(MitSpeicher):
    def test_komplette_caption_aus_fakten(self):
        cid = self.clip_anlegen(max_gruppe=3)  # Kills im Abstand von 3 s -> 6 s Serie
        self.con.execute("UPDATE clips SET typ = 'triple' WHERE id = ?", (cid,))
        clip = db.clip(self.con, cid)
        text = caption.baue(clip, db.match(self.con, clip["match_id"]), self.konfig)
        self.assertTrue(text.startswith("Triple Kill in 6 Sekunden") or text.startswith("3 Eliminierungen in 6"), text)
        self.assertIn("clip-battle.de", text)
        self.assertIn("#triplekill", text)

    def test_platzhalter_streng(self):
        with self.assertRaises(caption.CaptionFehler):
            caption.fuelle("{beschreibung} {gibtsnicht}", {"beschreibung": "x"})

    def test_ki_darf_keine_zahlen_erfinden(self):
        fakten = {"kills": 3, "sekunden": 6, "typ": "triple", "victory_royale": False, "platzierung": 3, "kills_match": 7}
        self.assertTrue(caption.pruefe_ki_text("Triple in 6 Sekunden, Platz 3 🔥", fakten, 150))
        self.assertFalse(caption.pruefe_ki_text("Triple in 4 Sekunden mit der Pumpgun", fakten, 150))
        self.assertFalse(caption.pruefe_ki_text("x" * 151, fakten, 150))


class Aufraeumen(MitSpeicher):
    def setUp(self):
        super().setUp()
        # Diese Klasse testet die alte Regel selbst (182 Tage recyceln) – die braucht seit B1 (26.09.) das
        # ausdrückliche Opt-in; Standard ist aus (Entscheidung 25.09., "nie automatisch löschen").
        self.konfig.daten.setdefault("aufraeumen", {})["aktiv"] = True

    def _datei(self, relativ: str, alter_tage: float):
        pfad = self.konfig.wurzel / relativ
        pfad.parent.mkdir(parents=True, exist_ok=True)
        pfad.write_bytes(b"x")
        zeit = time.time() - alter_tage * 86400
        os.utime(pfad, (zeit, zeit))
        return pfad

    def test_regel_halbes_jahr_ausser_triple(self):
        alt = datetime.now(UTC) - timedelta(days=200)
        cid = self.clip_anlegen(max_gruppe=3, start=alt)
        self.con.execute("UPDATE clips SET clip_pfad = 'sessions/m1/clips/001_triple_3k.mp4' WHERE id = ?", (cid,))
        triple = self._datei("sessions/m1/clips/001_triple_3k.mp4", 200)
        einzel = self._datei("sessions/m1/clips/002_einzel_1k.mp4", 200)
        neu = self._datei("sessions/m1/clips/003_einzel_1k.mp4", 10)
        protokoll = self._datei("sessions/m1/schnittliste.json", 200)  # JSON bleibt immer
        abgelaufen = self._datei("papierkorb/2026-01-01/sessions/alt.mp4", 1)

        aktionen = {a.pfad.name: a.ziel for a in aufraeumen.plane(self.con, self.konfig)}
        self.assertEqual(aktionen, {"001_triple_3k.mp4": "archiv", "002_einzel_1k.mp4": "papierkorb", "2026-01-01": "loeschen"})
        self.assertTrue(triple.exists() and einzel.exists())  # Probelauf ändert nichts

        aufraeumen.fuehre_aus(self.con, self.konfig, aufraeumen.plane(self.con, self.konfig))
        self.assertTrue((self.konfig.ordner("archiv") / "sessions/m1/clips/001_triple_3k.mp4").exists())
        self.assertFalse(einzel.exists())
        self.assertTrue(neu.exists() and protokoll.exists())
        self.assertFalse(abgelaufen.exists())
        self.assertEqual(db.clip(self.con, cid)["clip_pfad"], "archiv/sessions/m1/clips/001_triple_3k.mp4")

    def test_loescht_nie_ausserhalb_des_papierkorbs(self):
        with self.assertRaises(RuntimeError):
            aufraeumen.fuehre_aus(self.con, self.konfig, [aufraeumen.Aktion(self.konfig.ordner("sessions"), "loeschen", "")])

    def test_ohne_aktiv_verweigert(self):
        """B1 (26.09.): Standard ist aus (Entscheidung 25.09., „nie automatisch löschen“) – auch ohne getrennten
        Betrieb bleibt aufraeumen gesperrt, bis [aufraeumen].aktiv = true ausdrücklich gesetzt ist."""
        self._datei("sessions/m1/clips/002_einzel_1k.mp4", 200)
        self.konfig.daten["aufraeumen"]["aktiv"] = False
        with self.assertRaises(KonfigFehler) as fehler:
            aufraeumen.plane(self.con, self.konfig)
        self.assertIn("aktiv", str(fehler.exception))
        code, e = self._cli(["aufraeumen"])
        self.assertEqual((code, e["fehler"]), (2, "konfig"))
        self.assertIn("aktiv", e["hinweis"])

    def test_im_getrennten_betrieb_verweigert(self):
        """E19: im Puffer wird nichts verschoben und kein DB-Pfad umgeschrieben – Klartext, Exit 2."""
        einzel = self._datei("sessions/m1/clips/002_einzel_1k.mp4", 200)
        self._datei("papierkorb/2026-01-01/sessions/alt.mp4", 1)
        self.konfig.daten["lager"]["wurzel"] = str(self.tmp / "lager")
        with self.assertRaises(KonfigFehler) as fehler:
            aufraeumen.plane(self.con, self.konfig)
        self.assertIn("getrennten Betrieb", str(fehler.exception))
        with self.assertRaises(KonfigFehler):
            aufraeumen.fuehre_aus(self.con, self.konfig, [aufraeumen.Aktion(einzel, "papierkorb", "alt")])
        for argv in (["aufraeumen"], ["aufraeumen", "--ausfuehren", "--taeglich"]):
            code, e = self._cli(argv)
            self.assertEqual((code, e["fehler"]), (2, "konfig"))
            self.assertIn("clip-aufraeumen ausschalten", e["hinweis"])
        # Auch wenn gerade ein anderer Schritt die Pipeline-Sperre hält: sofort Exit 2 mit Klartext –
        # nicht erst warten und dann „gesperrt“ (Exit 4, im Timer kein Fehler)
        self.konfig.daten.setdefault("sperre", {})["warten_s"] = 0
        with sperre(self.konfig.datenbank.with_suffix(".lock")):
            code, e = self._cli(["aufraeumen", "--ausfuehren", "--taeglich"])
        self.assertEqual((code, e["fehler"]), (2, "konfig"))
        self.assertIn("clip-aufraeumen ausschalten", e["hinweis"])
        self.assertTrue(einzel.exists())
        self.assertTrue((self.konfig.ordner("papierkorb") / "2026-01-01").exists())
        self.assertIsNone(self.con.execute("SELECT 1 FROM ereignisse WHERE art = 'aufraeumen'").fetchone())

    def _cli(self, argv: list[str]) -> tuple[int, dict]:
        ausgabe = io.StringIO()
        with mock.patch("clip_pipeline.cli.lade", return_value=self.konfig), \
                contextlib.redirect_stdout(ausgabe), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(argv)
        return code, json.loads(ausgabe.getvalue().strip().splitlines()[-1])


class Sperre(MitSpeicher):
    def test_zweiter_lauf_wartet_oder_gibt_auf(self):
        pfad = self.tmp / "p.lock"
        with sperre(pfad):
            start = time.monotonic()
            with self.assertRaises(Gesperrt):
                with sperre(pfad, warten_s=1.5):
                    pass
            self.assertGreaterEqual(time.monotonic() - start, 1.4)  # hat gewartet
        with sperre(pfad):  # danach wieder frei
            pass

    def test_caption_nutzt_gepruefte_beschreibung(self):
        cid = self.clip_anlegen()
        self.con.execute("UPDATE clips SET beschreibung = 'Sauber gelöst 🎯' WHERE id = ?", (cid,))
        clip = db.clip(self.con, cid)
        self.assertTrue(caption.baue(clip, db.match(self.con, "m1"), self.konfig).startswith("Sauber gelöst 🎯"))


if __name__ == "__main__":
    unittest.main()
