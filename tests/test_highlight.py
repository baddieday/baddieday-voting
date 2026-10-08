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

    def test_entscheiden_im_lernbot(self):
        """Stufe 3 (08.10.): ✅ am Entwurf im Lern-Bot = freigegeben und hochgeladen; die erste Entscheidung gilt.
        Gehört der Entwurf zu keinem Highlight-Video: None, nichts geändert."""
        self.con.execute("INSERT INTO highlights (name, datei, vorschau, clips, dauer, erstellt, entwurf_id, status) "
                         "VALUES ('2026-W41', 'highlights/h.mp4', 'highlights/v.mp4', 5, '01:42', 'x', 7, 'gesendet')")
        h = highlight.entscheide_entwurf(self.con, 7, True)
        self.assertEqual(h["status"], "freigegeben")
        self.assertIsNotNone(h["hochgeladen"])
        self.assertEqual(highlight.entscheide_entwurf(self.con, 7, False)["status"], "freigegeben")
        self.assertIsNone(highlight.entscheide_entwurf(self.con, 8, False))

    def test_upload_fassung_aus_der_fertigen_datei(self):
        """Stufe 3 (08.10.): Die Telegram-Fassung des 2-Wochen-Videos entsteht aus der fertigen Datei (seine Szenen gibt
        der Puffer nach 14 Tagen frei). Fehlt die fertige Datei: None – dann gilt der normale Weg."""
        (self.konfig.wurzel / ".clip-puffer").touch()                     # getrennter Betrieb (E19)
        self.konfig.daten["lager"]["wurzel"] = str(self.tmp / "lager")
        self.konfig.daten["publikum"]["upload_ordner"] = "export"
        eid = self.con.execute("INSERT INTO entwuerfe (name, format, schnittliste, parameter, dauer_s, erstellt) "
                               "VALUES ('highlight-2026-W41', 'zusammenschnitt', 'x.json', '{}', 90, 'x')").lastrowid
        self.con.execute("INSERT INTO highlights (name, datei, vorschau, clips, dauer, erstellt, entwurf_id) VALUES "
                         "('2026-W41', 'highlights/2026-W41.mp4', 'highlights/v.mp4', 5, '01:30', 'x', ?)", (eid,))
        self.assertIsNone(highlight.upload_fassung(self.con, self.konfig, eid))       # fertige Datei fehlt
        (self.konfig.ordner("highlights") / "2026-W41.mp4").write_bytes(b"voll")

        def verkleinere(_quelle, ziel, **_kw):
            ziel.parent.mkdir(parents=True, exist_ok=True)
            ziel.write_bytes(b"klein")

        with mock.patch.object(highlight, "vorschau", side_effect=verkleinere) as v:
            e = highlight.upload_fassung(self.con, self.konfig, eid)
            self.assertTrue(highlight.upload_fassung(self.con, self.konfig, eid)["uebersprungen"])   # idempotent
        self.assertEqual(v.call_count, 1)
        self.assertEqual(v.call_args.kwargs["kurze_seite"], 1080)
        self.assertTrue(e["datei"].endswith("export/highlight-2026-W41/highlight-2026-W41_upload.mp4"))
        self.assertEqual(self.con.execute("SELECT upload_pfad FROM entwuerfe").fetchone()[0], e["datei"])
