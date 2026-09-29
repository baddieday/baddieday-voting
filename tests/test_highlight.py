import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from clip_pipeline import db, entwurf, highlight, regie, regie_lernen, stimmung
from clip_pipeline.zeit import iso, jetzt
from tests.hilfen import MitSpeicher


class Highlight(MitSpeicher):
    def test_video_nutzt_autonomen_regisseur_ohne_bewertung(self):
        cid = self.clip_anlegen(status="gesendet", start=jetzt()-timedelta(days=2))
        source = self.konfig.ordner("sessions") / "clip.mp4"
        source.write_bytes(b"clip")
        self.con.execute("UPDATE clips SET clip_pfad=? WHERE id=?", (self.konfig.relativ(source), cid))
        self.konfig.daten.setdefault("regie", {})["ordner"] = str(self.tmp / "regie")
        liste = {"format": "zusammenschnitt", "dauer_s": 90, "musik": None,
                 "parameter": {"autonom": {"version": 3}},
                 "segmente": [{"clip_id": cid, "moment": f"clip:{cid}"}]}

        def planen(con, konfig, fmt, **kwargs):
            self.assertEqual(fmt, "zusammenschnitt")
            self.assertEqual(kwargs["parameter"], {"ziel_dauer_s": 90})
            self.assertEqual(kwargs["nur_matches"], {"m1"})
            pfad = self.tmp / "liste.json"
            pfad.write_text(json.dumps(liste), encoding="utf-8")
            eid = con.execute("INSERT INTO entwuerfe (name,format,schnittliste,parameter,dauer_s,erstellt) "
                              "VALUES (?,'zusammenschnitt',?,'{}',90,?)",
                              (kwargs["name"], str(pfad), iso(jetzt()))).lastrowid
            return {"entwurf": eid}

        def render(liste_, datei, _konfig, **kwargs):
            self.assertTrue(kwargs["vollstaendig"])
            datei.write_bytes(b"render")

        def preview(_video, ziel, **_kwargs):
            ziel.write_bytes(b"preview")

        with mock.patch.object(stimmung, "analysiere", return_value={}) as analyse, \
                mock.patch.object(regie_lernen, "aktuelle", return_value=({"ziel_dauer_s": 90}, {})), \
                mock.patch.object(regie, "erstelle", side_effect=planen), \
                mock.patch.object(entwurf, "rendere", side_effect=render) as renderer, \
                mock.patch.object(highlight, "probe", return_value=SimpleNamespace(dauer_s=90)), \
                mock.patch.object(highlight, "vorschau", side_effect=preview):
            e = highlight.erstelle(self.con, self.konfig, "2026-KW39", 14)
            self.assertEqual((e["clips"], e["dauer"], e["clip_ids"]), (1, "01:30", [cid]))
            self.assertEqual(analyse.call_args.kwargs["nur_clips"], [cid])
            self.assertTrue(highlight.erstelle(self.con, self.konfig, "2026-KW39", 14)["uebersprungen"])
            self.assertEqual(renderer.call_count, 1)
        zeile = self.con.execute("SELECT * FROM highlights").fetchone()
        self.assertEqual(zeile["entwurf_id"], e["entwurf"])
        self.assertEqual(db.clip(self.con, cid)["status"], "gesendet")
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM entwurf_bewertungen").fetchone()[0], 0)
        h = highlight.entscheide(self.con, zeile["id"], freigeben=False)
        self.assertEqual(h["status"], "verworfen")
        self.assertIsNone(db.clip(self.con, cid)["highlight_id"])

    def test_ohne_clips(self):
        self.assertEqual(highlight.erstelle(self.con, self.konfig, "leer", 14)["clips"], 0)
