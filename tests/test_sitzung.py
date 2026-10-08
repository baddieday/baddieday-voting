"""Session vorbei: Datei vom Gaming-PC abholen, auf n8n warten, Short nur aus dem Abend, nie wecken."""

import contextlib
import io
import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from clip_pipeline import cli, einstellungen, entwurf, sitzung
from clip_pipeline.medien import MedienFehler
from clip_pipeline.zeit import UTC, iso, jetzt

from tests.hilfen import HAT_FFMPEG
from tests.regie_hilfen import MOMENTE, MitRegieMaterial


class AbendDatum(unittest.TestCase):
    """07.10.: „Abend vom …“ ist der Spielabend (Tageswechsel 06:00) wie in ⚙️ – nicht das Datum des Abend-Endes."""

    def test_abend_ueber_mitternacht_gehoert_zum_vortag(self):
        konfig = SimpleNamespace(wert=lambda schluessel, standard=None: standard)
        self.assertEqual(sitzung._tag(konfig, datetime(2026, 10, 6, 21, 30, tzinfo=UTC)), "06.10.")   # 23:30 Ortszeit
        self.assertEqual(sitzung._tag(konfig, datetime(2026, 10, 6, 23, 30, tzinfo=UTC)), "06.10.")   # 01:30 am 07.10.


@unittest.skipUnless(HAT_FFMPEG, "ffmpeg fehlt")
class Sitzung(MitRegieMaterial):
    def marker(self, name, matches, ende):
        ordner = self.konfig.wurzel / "sitzungen"
        ordner.mkdir(exist_ok=True)
        text = json.dumps({"session": name, "matches": matches, "ende_utc": iso(ende)})
        (ordner / f"{name}.json").write_bytes(b"\xef\xbb\xbf" + text.encode())  # wie Windows PowerShell 5.1

    def test_ablauf(self):
        self.momente_anlegen(MOMENTE)
        self.musik_anlegen(150, "episch")
        for m in ("m1", "m2"):
            self.con.execute("INSERT INTO matches (id, replay_pfad, start_utc, ende_utc, status, erstellt, geaendert) "
                             "VALUES (?, ?, 'x', 'x', 'verarbeitet', 'x', 'x')", (m, f"replays/{m}.replay"))
        self.marker("session_2026-09-24_23-10-00", ["m1", "m2", "m9"], jetzt())
        e = sitzung.verarbeite(self.con, self.konfig, claude=False, whisper=False)
        self.assertEqual((e["neu"], e["wartet"][0]["offen"]), ([], ["m9"]))  # m9 noch nicht von n8n verarbeitet

        self.marker("session_2026-09-24_23-10-00", ["m1", "m2", "m9"], jetzt() - timedelta(hours=3))
        e = sitzung.verarbeite(self.con, self.konfig, claude=False, whisper=False)
        neu = e["neu"][0]
        self.assertIn("m9", neu["hinweis"])
        entwurf = self.con.execute("SELECT * FROM entwuerfe WHERE id = ?", (neu["entwurf"],)).fetchone()
        liste = json.loads(Path(entwurf["schnittliste"]).read_text())
        self.assertTrue({s["match_id"] for s in liste["segmente"]} <= {"m1", "m2"})  # nur der Abend
        self.assertTrue(Path(entwurf["datei"]).is_file())  # gerendert -> der Lern-Bot schickt ihn
        self.assertEqual(sitzung.verarbeite(self.con, self.konfig, claude=False, whisper=False)["neu"], [])  # einmal

    def test_abend_ende_ohne_datei_vom_pc(self):
        """07.10. („immer noch die Clips von vor 14 Tagen, nicht die neueste Session“): ohne Datei vom PC erkennt der
        Mini das Abend-Ende selbst – 45 min kein neues Match; solange gespielt wird, wartet er."""
        self.momente_anlegen(MOMENTE)
        self.musik_anlegen(150, "episch")
        for m, vor_min in (("m1", 120), ("m2", 70)):
            self.con.execute("INSERT INTO matches (id, replay_pfad, start_utc, ende_utc, status, erstellt, geaendert) "
                             "VALUES (?, ?, ?, ?, 'verarbeitet', 'x', 'x')",
                             (m, f"replays/{m}.replay", iso(jetzt() - timedelta(minutes=vor_min + 20)),
                              iso(jetzt() - timedelta(minutes=vor_min))))
        self.assertIsNone(sitzung.auto_abend(self.con, self.konfig, ["m2"]))      # schon in einer Datei vom PC
        self.con.execute("UPDATE matches SET ende_utc = ? WHERE id = 'm2'", (iso(jetzt() - timedelta(minutes=10)),))
        self.assertIsNone(sitzung.auto_abend(self.con, self.konfig))               # vor 10 min: wird noch gespielt
        self.con.execute("UPDATE matches SET ende_utc = ? WHERE id = 'm2'", (iso(jetzt() - timedelta(minutes=70)),))
        e = sitzung.verarbeite(self.con, self.konfig, claude=False, whisper=False)
        self.assertEqual(e["neu"][0]["sitzung"], "abend_m1")
        self.assertEqual(e["neu"][0]["matches"], 2)
        self.assertEqual(sitzung.verarbeite(self.con, self.konfig, claude=False, whisper=False)["neu"], [])  # einmal

    def abend_aus_m1_m2(self, name="session_2026-10-07_23-10-00"):
        """Abend aus m1/m2 (vier starke Szenen), seit 50 min vorbei."""
        for m in ("m1", "m2"):
            self.con.execute("INSERT OR IGNORE INTO matches (id, replay_pfad, start_utc, ende_utc, status, erstellt, "
                             "geaendert) VALUES (?, ?, 'x', 'x', 'verarbeitet', 'x', 'x')", (m, f"replays/{m}.replay"))
        self.marker(name, ["m1", "m2"], jetzt() - timedelta(minutes=50))

    def fehlerzeilen(self):
        return [z["text"] for z in self.con.execute(
            "SELECT text FROM lern_meldungen WHERE schluessel LIKE 'fehler:%' ORDER BY id")]

    def test_render_panne_holt_der_naechste_lauf_nach(self):
        """08.10. (Florian: „autonom … besser und schneller als mit der Hand“): Scheitert das Rendern einmal (z. B.
        hängende Grafikeinheit), ist der Abend nicht verloren – der nächste Timer-Lauf rendert auf der CPU nach, ohne
        Fehlerzeile und ohne 🎬 von Hand."""
        self.momente_anlegen(MOMENTE[:5])                                   # alle Momente aus m1/m2
        self.musik_anlegen(150, "episch")
        self.abend_aus_m1_m2()
        echt, vaapi = entwurf.entwurf, []

        def rendern(con, k, entwurf_id):
            vaapi.append(k.wert("regie.vaapi", True))
            if len(vaapi) == 1:
                raise MedienFehler("Entwurf x: hängt – 180 s ohne Fortschritt, abgebrochen")
            return echt(con, k, entwurf_id)

        with mock.patch.object(entwurf, "entwurf", side_effect=rendern):
            erst = sitzung.verarbeite(self.con, self.konfig, claude=False, whisper=False)["neu"][0]
            self.assertEqual(self.fehlerzeilen(), [])                      # noch keine Fehlerzeile
            dann = sitzung.verarbeite(self.con, self.konfig, claude=False, whisper=False)
        self.assertEqual(dann["nachgeholt"], ["session_2026-10-07_23-10-00"])
        self.assertEqual(vaapi, [True, False])                              # der zweite Versuch auf der CPU
        z = self.con.execute("SELECT status, datei FROM entwuerfe WHERE id = ?", (erst["entwurf"],)).fetchone()
        self.assertEqual(z["status"], "gerendert")                          # die Sitzung hat ihr Video
        self.assertTrue(Path(z["datei"]).is_file())
        self.assertEqual(self.fehlerzeilen(), [])

    def test_render_scheitert_zweimal_eine_fehlerzeile(self):
        """Scheitert auch der zweite Versuch: genau eine Fehlerzeile ohne Versprechen, danach kein Versuch mehr. Fehlt
        eine Datei, hilft kein zweiter Versuch. Unter /experte wie bisher: gleich die Zeile, kein zweiter Versuch."""
        self.momente_anlegen(MOMENTE[:5])                                   # alle Momente aus m1/m2
        self.musik_anlegen(150, "episch")
        self.abend_aus_m1_m2()
        with mock.patch.object(entwurf, "entwurf", side_effect=MedienFehler("Entwurf x fehlgeschlagen (Exit 1)")) as r:
            for _ in range(3):
                sitzung.verarbeite(self.con, self.konfig, claude=False, whisper=False)
            self.assertEqual(r.call_count, 2)                               # höchstens ein zusätzlicher Render
            fehler = self.fehlerzeilen()
            self.assertEqual(len(fehler), 1)
            self.assertTrue(fehler[0].endswith("kam kein Video zustande – beim Bauen ging zweimal etwas schief."))
            self.assertIsNone(self.con.execute("SELECT entwurf_id FROM sitzungen").fetchone()[0])
            einstellungen.setze(self.con, "lernbot.experte", True)
            self.abend_aus_m1_m2("session_2026-10-08_23-10-00")
            sitzung.verarbeite(self.con, self.konfig, claude=False, whisper=False)
            self.assertEqual(r.call_count, 3)                               # /experte: ein Versuch …
        self.assertIn("Beim nächsten Abend versuche ich es wieder.", self.fehlerzeilen()[-1])  # … und gleich die Zeile
        self.assertTrue(sitzung._dauerhaft(MedienFehler("Moment-Datei fehlt: /srv/clips/momente/1.mp4")))
        self.assertFalse(sitzung._dauerhaft(MedienFehler("Entwurf x: hängt – 180 s ohne Fortschritt, abgebrochen")))

    def test_speicher_schlaeft_kein_wecken(self):
        (self.konfig.wurzel / ".clip-speicher").unlink()
        self.konfig.daten["speicher"].update(host="pve-big", wol_mac="aa:bb:cc:dd:ee:ff")
        ausgabe = io.StringIO()
        with mock.patch("clip_pipeline.cli.lade", return_value=self.konfig), \
                mock.patch("clip_pipeline.konfig.Konfig._host_erreichbar", return_value=False), \
                mock.patch("clip_pipeline.konfig.sende_wake_on_lan") as wol, \
                contextlib.redirect_stdout(ausgabe), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(["sitzungen"])
        self.assertEqual(code, 3)
        wol.assert_not_called()
