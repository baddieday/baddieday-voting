"""Mehrbenutzer, Stufe 3, PR 1 (M139–M142): Laufzeit-Protokoll je Rechenauftrag und `pipeline laufzeiten`.

Jede Rechen-Sperre schreibt danach eine Zeile „lauf“ in die eigene Datenbank (Warten, Halten, Ergebnis) – ohne dass
sich am Auftrag etwas ändert: Exit-Code und JSON-Zeile bleiben, ein Schreibfehler steht nur im Log, und die
Transaktion eines Aufrufers bleibt, wie sie ist. Der Render-Schritt ist gefälscht; geprüft wird das Protokoll.
"""

from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import sqlite3
from datetime import timedelta
from unittest import mock

from clip_pipeline import cli, laufzeiten, sperre, verarbeitung
from clip_pipeline.konfig import Konfig
from clip_pipeline.zeit import iso, jetzt

from tests.hilfen import MitSpeicher

SID = "2026-10-09_20-15-33"


def render(con, konfig, sid):
    """Ersatz für verarbeitung.render: das Ergebnis wie im n8n-Vertrag, ohne zu schneiden."""
    return {"session": sid, "clips": 2, "top_label": "Double Kill", "top_score": 3}


class Laufzeiten(MitSpeicher):
    def cli(self, *argv: str) -> tuple[int, dict]:
        ausgabe = io.StringIO()
        with mock.patch("clip_pipeline.cli.lade", return_value=self.konfig), \
                mock.patch.object(verarbeitung, "render", side_effect=render), \
                contextlib.redirect_stdout(ausgabe), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(list(argv))
        zeilen = ausgabe.getvalue().splitlines()
        self.assertEqual(len(zeilen), 1, zeilen)                       # genau eine JSON-Zeile (Vertrag)
        return code, json.loads(zeilen[0])

    def laeufe(self, con: sqlite3.Connection | None = None) -> list[tuple[str | None, dict]]:
        return [(z["match_id"], json.loads(z["text"])) for z in (con or self.con).execute(
            "SELECT match_id, text FROM ereignisse WHERE art = 'lauf' ORDER BY id")]

    def entwurf(self, name: str, *, erstellt, dauer_s: float, sidecar: dict, sidecar_zeit: float | None = None,
                status: str = "gerendert") -> int:
        video = self.tmp / f"{name}.mp4"
        (self.tmp / f"{name}.render.json").write_text(json.dumps(sidecar), encoding="utf-8")
        if sidecar_zeit is not None:
            os.utime(self.tmp / f"{name}.render.json", (sidecar_zeit, sidecar_zeit))
        return self.con.execute("INSERT INTO entwuerfe (name, format, schnittliste, parameter, dauer_s, datei, status, "
                                "erstellt) VALUES (?, 'short', ?, '{}', ?, ?, ?, ?)",
                                (name, str(self.tmp / f"{name}.json"), dauer_s, str(video), status,
                                 iso(erstellt))).lastrowid

    def test_zeile_je_lauf_gesperrt_und_auswertung(self):
        with mock.patch.object(laufzeiten, "LEER_S", 0.0):             # der Schein-Schritt rechnet keine Sekunde
            code, daten = self.cli("render", "--session", SID)
        self.assertEqual((code, daten), (0, render(None, None, SID)))
        (match, ok), = self.laeufe()
        self.assertEqual(match, SID)
        self.assertEqual({k: ok[k] for k in ("was", "ziel", "ergebnis", "fehler")},
                         {"was": "render", "ziel": SID, "ergebnis": "ok", "fehler": None})
        self.assertGreaterEqual(ok["gehalten_s"], 0)

        # Ein anderer Schritt rechnet gerade: Exit 4 und {"fehler": "gesperrt"} wie bisher, dazu eine Zeile „gesperrt“
        self.konfig.daten["sperre"]["warten_s"] = 0
        with sperre.sperre(sperre.pfad(self.konfig), warten_s=0), mock.patch.object(laufzeiten, "GESPERRT_AB_S", 0.0):
            code, daten = self.cli("render", "--session", SID)
        self.assertEqual((code, daten["fehler"]), (4, "gesperrt"))
        self.assertEqual([(e["ergebnis"], e["gehalten_s"]) for _, e in self.laeufe()][1], ("gesperrt", 0.0))

        # Ein neuer Entwurf mit Laufzeit im Sidecar (30 s für 45 s Video), ein alter nur aus Dateizeiten (60 s für 40 s),
        # dazu dein ✅ und seine Upload-Fassung 2 min danach
        neu = self.entwurf("neu", erstellt=jetzt() - timedelta(minutes=30), dauer_s=45.0, status="bewertet",
                           sidecar={"encoder": "libx264", "render_s": 30.0, "dauer_s": 45.0, "rueckfall": False,
                                    "eingabe_mb": 120.5})
        alt = jetzt() - timedelta(minutes=20)
        self.entwurf("alt", erstellt=alt, dauer_s=40.0, sidecar={"encoder": "libx264"},
                     sidecar_zeit=alt.timestamp() + 60)
        ok_zeit = jetzt() - timedelta(minutes=10)
        self.con.execute("INSERT INTO entwurf_bewertungen (entwurf_id, daumen, erstellt, geaendert) VALUES (?, 1, ?, ?)",
                         (neu, iso(ok_zeit), iso(ok_zeit)))
        upload = self.tmp / "neu_upload.mp4"
        upload.write_bytes(b"")
        os.utime(upload, (ok_zeit.timestamp() + 120, ok_zeit.timestamp() + 120))
        self.con.execute("UPDATE entwuerfe SET upload_pfad = ? WHERE id = ?", (str(upload), neu))

        code, daten = self.cli("laufzeiten", "--tage", "7")
        self.assertEqual(code, 0)
        render_zeile = daten["auftraege"]["render"]
        self.assertEqual({k: render_zeile[k] for k in ("n", "gesperrt", "fehler")}, {"n": 2, "gesperrt": 1, "fehler": 0})
        encoder = daten["encoder"]["libx264"]["entwurf"]
        self.assertEqual((encoder["n"], encoder["aus_dateizeiten"]), (2, 1))
        self.assertEqual(encoder["s_je_video_s"], {"median": 1.08, "p90": 1.5, "max": 1.5})   # 0,67 und 1,5
        self.assertEqual(encoder["eingabe_mb"]["max"], 120.5)
        self.assertEqual((daten["ok_bis_upload_s"]["n"], daten["ok_bis_upload_s"]["median"]), (1, 120.0))
        self.assertEqual(daten["freigabe"], {"gezeigt": 1, "ok": 1, "quote": 1.0})
        self.assertIsNone(daten["abend_bis_video"])                    # fehlende Werte sind null, nichts geschätzt
        self.assertEqual(len(self.laeufe()), 2)                        # der Lesebefehl selbst schreibt nichts

    def test_schreibfehler_aendert_nichts_am_auftrag(self):
        with mock.patch.object(laufzeiten, "LEER_S", 0.0), \
                mock.patch.object(laufzeiten, "_einfuegen", side_effect=sqlite3.OperationalError("database is locked")), \
                self.assertLogs("pipeline", "WARNING") as logs:
            code, daten = self.cli("render", "--session", SID)
        self.assertEqual((code, daten), (0, render(None, None, SID)))   # Exit 0, dieselbe JSON-Zeile
        self.assertEqual(self.laeufe(), [])
        self.assertTrue(any("nicht protokolliert" in z for z in logs.output), logs.output)

    def test_offene_transaktion_des_aufrufers_bleibt_unberuehrt(self):
        """M141 (2): Ein Bot hält eine offene Transaktion – kein commit, kein rollback auf seiner Verbindung; die eigene
        kurze Verbindung findet die Datenbank belegt, also nur eine Logzeile."""
        self.con.execute("BEGIN IMMEDIATE")
        self.con.execute("INSERT INTO lern_meldungen (schluessel, text, erstellt) VALUES ('offen', 'x', 'jetzt')")
        with mock.patch.object(laufzeiten, "LEER_S", 0.0), mock.patch.object(laufzeiten, "GRENZE_S", 0.2), \
                self.assertLogs("pipeline", "WARNING") as logs:
            with laufzeiten.lauf(self.konfig, "lernbot-bau", warten_s=60, con=self.con):
                pass
        self.assertTrue(self.con.in_transaction)
        self.assertEqual(self.con.execute("SELECT text FROM lern_meldungen WHERE schluessel = 'offen'").fetchone()[0],
                         "x")
        self.assertEqual(self.laeufe(), [])                            # auch nicht in seiner Transaktion
        self.assertTrue(any("nicht protokolliert" in z for z in logs.output), logs.output)
        self.con.execute("ROLLBACK")                                   # entscheidet der Aufrufer selbst

        # Ohne offene Transaktion: über seine Verbindung, sofort festgeschrieben, seine Wartezeit bleibt
        with mock.patch.object(laufzeiten, "LEER_S", 0.0):
            with laufzeiten.lauf(self.konfig, "lernbot-bau", warten_s=60, con=self.con) as eintrag:
                eintrag.ziel, eintrag.daten["render_s"] = 7, 1.5
        self.assertFalse(self.con.in_transaction)
        lesen = sqlite3.connect(f"file:{self.konfig.datenbank}?mode=ro", uri=True)
        lesen.row_factory = sqlite3.Row
        self.addCleanup(lesen.close)
        (_, zeile), = self.laeufe(lesen)
        self.assertEqual((zeile["was"], zeile["ziel"], zeile["render_s"]), ("lernbot-bau", 7, 1.5))
        self.assertEqual(self.con.execute("PRAGMA busy_timeout").fetchone()[0], 30000)

        # Ohne Verbindung und ohne Datenbank (benutzer einrichten): nie eine anlegen
        daten = copy.deepcopy(self.konfig.daten)
        daten["datenbank"]["pfad"] = str(self.tmp / "nie.db")
        with mock.patch.object(laufzeiten, "LEER_S", 0.0):
            with laufzeiten.lauf(Konfig(daten, self.konfig.quelle), "benutzer-einrichten", warten_s=60):
                pass
        self.assertFalse((self.tmp / "nie.db").exists())
