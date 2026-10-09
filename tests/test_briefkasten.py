"""Mehrbenutzer, Stufe 2, Schritt 2: Abholen am Mini – `pipeline briefkasten abholen|status` (src/clip_pipeline/
briefkasten.py).

Die sftp-Attrappe (tests/sftp_attrappe.py) spielt das Abholprofil des Briefkastens gegen einen Ordner je Freund; die
Tests zeichnen jeden sftp-Aufruf am Modul auf. Geprüft wird:
  - Normal: Video, Replay und Sitzungsdatei mit Lieferschein landen im Puffer von max – Name wie im Fach, Ordner aus
    dem Namen, mtime = min(PC-Zeit, jetzt − 130 s), eine Zeile in abholung, der Status vom PC in I/db. scan legt das
    Match mit Clip an; die Sitzungsdatei kommt erst danach, sitzungen baut den Abend aus ihr, auto_abend ist aus.
  - Wichtigster Fehlerfall: Der Download bricht ab bzw. die Prüfsumme ist falsch – kein Endname, keine Zeile, das
    Replay wartet. Der nächste Lauf setzt per reget fort; nach drei falschen Prüfsummen ist die Datei aufgegeben, eine
    Zeile geht an den Freund, und das Replay kommt.
  - Isolation: fremde Muster, Punkt vorn, Platzhalter, unmögliches Datum, Unterordner, Lieferschein über 4 KB oder mit
    fremdem Namen – nichts im Puffer; eine vorhandene Datei wird nie überschrieben; nur bk-max, nur sein Schlüssel,
    nur lesende Befehle, lokal nur nach .abholen; das Fach von eva bleibt unberührt.
  - Florian: ohne CLIP_INSTANZ Exit 2, seine Dateien gleich (Fingerabdruck), seine Datenbank ohne neue Tabelle.
  - Grenzen: Bremse, nicht erreichbar, nicht eingehängt; Sitzung spätestens nach 24 h, doppelte nur vermerkt.
  - Echte Zeilen aus OpenSSH 9.6 – und, wo möglich (root, BRIEFKASTEN_SSHD), ein Lauf gegen echten sshd.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import io
import json
import os
import shlex
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from clip_pipeline import briefkasten, cli, db, medien, sitzung
from clip_pipeline import konfig as konfig_mod
from clip_pipeline.zeit import UTC, iso, jetzt, utc_zu_lokal

from tests import sftp_attrappe
from tests.hilfen import HAT_FFMPEG, MitSpeicher, als_instanz, instanz_anlegen, testvideo
from tests.test_ende_zu_ende_stufe2 import elim, falsches_replay2json, replay_json

PROJEKT = Path(__file__).resolve().parents[1]
HOST = "100.64.0.7"
ZONE = "Europe/Berlin"
VIDEO = "Fortnite 2026.10.08 - 20.15.33.02.DVR.mp4"          # Videobeweis → eingang/nvidia/aufnahmen
VIDEO_B = "Fortnite 2026.10.08 - 20.16.10.03.DVR.mp4"
REPLAY = "UnsavedReplay-2026.10.08-20.00.00.replay"
SITZUNG = "session_2026-10-08_20-00-00.json"


def abdruck(wurzel: Path, ohne: tuple[str, ...] = ()) -> dict[str, str]:
    """SHA-256 jeder Datei (und jeder Ordner) unter wurzel, relativ – Teile in ohne übergangen."""
    ergebnis = {}
    for pfad in sorted(wurzel.rglob("*")):
        name = pfad.relative_to(wurzel).as_posix()
        if any(name == o or name.startswith(f"{o}/") for o in ohne):
            continue
        ergebnis[name] = "ordner" if pfad.is_dir() else hashlib.sha256(pfad.read_bytes()).hexdigest()
    return ergebnis


class MitBriefkasten(unittest.TestCase):
    """Florians Sperrdatei, max und eva mit Briefkasten (host 100.64.0.7) und je einem Fach in der Attrappe. Jeder
    sftp-Aufruf wird mitgeschrieben (argv, Befehle). Platz ist genug (die Bremse prüft ein eigener Test)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(os.path.realpath(self._tmp.name))
        self.sperrdatei = self.tmp / "florian" / "pipeline.lock"
        self.sperrdatei.parent.mkdir()
        self.sperrdatei.touch()
        umgebung = mock.patch.dict(os.environ, {"SFTP_ATTRAPPE_WURZEL": str(self.tmp / "vserver")})
        umgebung.start()
        self.addCleanup(umgebung.stop)
        for name in ("CLIP_INSTANZ", "CLIP_KONFIG", "CLIP_SPEICHER", "CLIP_DATENBANK", "SFTP_ATTRAPPE_ABBRUCH",
                     "SFTP_ATTRAPPE_AUS", "SFTP_ATTRAPPE_DF"):
            os.environ.pop(name, None)
        self.max = self.freund("max")
        self.eva = self.freund("eva")
        self.aufrufe: list[tuple[list[str], list[str]]] = []
        echt = briefkasten._sftp

        def mitschreiben(konfig, befehle, **kwargs):
            self.aufrufe.append((briefkasten._befehl(konfig), list(befehle)))
            return echt(konfig, befehle, **kwargs)

        for p in (mock.patch.object(briefkasten, "SFTP", str(sftp_attrappe.installiere(self.tmp))),
                  mock.patch.object(briefkasten, "_sftp", side_effect=mitschreiben),
                  mock.patch.object(briefkasten, "_platz", return_value=(10**12, 2 * 10**12))):
            p.start()
            self.addCleanup(p.stop)

    def freund(self, name: str) -> Path:
        inst = instanz_anlegen(self.tmp / "freunde", name, sperre=self.sperrdatei, env=f"CLIP_EPIC_ID={name}-epic\n",
                               toml=f'[briefkasten]\nhost = "{HOST}"\n')
        (inst / "briefkasten").mkdir()
        (inst / "briefkasten" / "abholen").write_text(f"{name}-schluessel-attrappe\n", encoding="utf-8")
        (inst / "briefkasten" / "known_hosts").write_text(f"[{HOST}]:2222 ssh-ed25519 AAAA-attrappe\n")
        chroot = self.tmp / "vserver" / f"bk-{name}"
        for ordner in ("videos", "replays", "sitzungen", "status"):
            (chroot / "fach" / ordner).mkdir(parents=True)
        (chroot / "fach" / briefkasten.MARKE).write_text(f"bk-{name}\n")
        (chroot / ".schluessel").write_text(str(inst / "briefkasten" / "abholen"))   # wie abholen/bk-<name>
        return inst

    def fach(self, name: str = "max") -> Path:
        return self.tmp / "vserver" / f"bk-{name}" / "fach"

    def hochladen(self, ordner: str, datei: str, inhalt: bytes, *, freund: str = "max", mtime_ms: int | None = None,
                  roh: str | None = None, **lieferschein) -> Path:
        """Wie der PC: erst die Datei, dann ihr Lieferschein (Felder überschreibbar, roh = Lieferschein als Text)."""
        ziel = self.fach(freund) / ordner / datei
        ziel.parent.mkdir(parents=True, exist_ok=True)
        ziel.write_bytes(inhalt)
        werte = {"name": datei, "groesse": len(inhalt), "sha256": hashlib.sha256(inhalt).hexdigest().upper(),
                 "mtime_ms": mtime_ms or int((time.time() - 3600) * 1000), "utc_offset_min": 120, **lieferschein}
        (ziel.parent / f"{datei}{briefkasten.LIEFERSCHEIN}").write_text(roh if roh is not None else json.dumps(werte),
                                                                        encoding="utf-8")
        return ziel

    def pipeline(self, *argumente: str, inst: Path | None = None) -> tuple[int, dict]:
        aus = io.StringIO()
        with als_instanz(inst or self.max), contextlib.redirect_stdout(aus), \
                contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(list(argumente))
        zeilen = aus.getvalue().strip().splitlines()
        self.assertEqual(len(zeilen), 1, aus.getvalue())   # genau eine JSON-Zeile
        return code, json.loads(zeilen[0])

    def abholen(self, **umgebung: str) -> tuple[int, dict]:
        with mock.patch.dict(os.environ, umgebung):
            return self.pipeline("briefkasten", "abholen")

    def zeilen(self, inst: Path | None = None) -> dict[str, dict]:
        con = sqlite3.connect(f"file:{(inst or self.max) / 'db' / 'pipeline.db'}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            return {z["name"]: dict(z) for z in con.execute("SELECT * FROM abholung")}
        finally:
            con.close()

    def meldungen(self) -> dict[str, str]:
        con = sqlite3.connect(f"file:{self.max / 'db' / 'pipeline.db'}?mode=ro", uri=True)
        try:
            return dict(con.execute("SELECT schluessel, text FROM lern_meldungen WHERE schluessel LIKE 'briefkasten:%'"))
        finally:
            con.close()

    def puffer_dateien(self, inst: Path | None = None) -> list[str]:
        """Dateien im Puffer außer Marken und der Zwischenablage."""
        daten = (inst or self.max) / "daten"
        return sorted(p.relative_to(daten).as_posix() for p in daten.rglob("*") if p.is_file()
                      and not p.name.startswith(".clip-") and briefkasten.ABLAGE not in p.relative_to(daten).parts)


# --- Normaler Weg ------------------------------------------------------------------------------------------------------

@unittest.skipUnless(HAT_FFMPEG, "ffmpeg fehlt")
class NormalerWeg(MitBriefkasten):
    def test_video_replay_sitzung_bis_zum_abend(self):
        jetzt_s = time.time()
        t0 = datetime.fromtimestamp(int(jetzt_s) - 3 * 3600, UTC)                 # Match-Start vor 3 h
        sid = f"{utc_zu_lokal(t0, ZONE):%Y-%m-%d_%H-%M-%S}"
        replay_name = f"{utc_zu_lokal(t0, ZONE):UnsavedReplay-%Y.%m.%d-%H.%M.%S.replay}"
        ende_video = t0 + timedelta(minutes=10)                                     # Nvidia: Zeit im Namen = Ende
        video_name = f"{utc_zu_lokal(ende_video, ZONE):Fortnite %Y.%m.%d - %H.%M.%S}.22.Eliminierung.mp4"
        video = testvideo(self.tmp / "pc" / video_name, dauer=20, tonspuren=2, creation_time=ende_video)
        beginn = ende_video + timedelta(seconds=1.4 - medien.probe(video).dauer_s)   # Versatz 1,4 s ([quellen.nvidia])
        kill_ms = round((beginn + timedelta(seconds=8) - t0).total_seconds() * 1000)
        programm = falsches_replay2json(self.tmp / "replay2json", replay_json(t0, 15 * 60, [elim(kill_ms, "A", False)]))
        pc_video_ms = int(ende_video.timestamp() * 1000) + 7000
        self.hochladen("videos", video_name, video.read_bytes(), mtime_ms=pc_video_ms)
        self.hochladen("replays", replay_name, b"replay-attrappe", mtime_ms=int((jetzt_s + 600) * 1000))  # PC-Uhr vor
        sitzung_name = f"session_{sid}.json"
        self.hochladen("sitzungen", sitzung_name, json.dumps(
            {"session": f"session_{sid}", "matches": [sid], "ende_utc": iso(t0 + timedelta(minutes=15))}).encode())
        (self.fach() / "status" / "pc-status.json").write_text('{"version": "test"}', encoding="utf-8")
        (self.fach() / "videos" / f"{video_name}.teil").write_bytes(b"halb")      # ein Upload unterwegs: nie anfassen

        code, e = self.abholen()
        self.assertEqual((code, e["abgeholt"], e["wartet"], e["fehler"]), (0, 2, 1, []), e)
        puffer = self.max / "daten"
        v, r = puffer / "eingang" / "nvidia" / "highlights" / video_name, puffer / "replays" / replay_name
        self.assertEqual(v.read_bytes(), video.read_bytes())
        self.assertEqual(v.stat().st_mtime_ns // 10**6, pc_video_ms)               # die Zeit vom PC
        self.assertAlmostEqual(r.stat().st_mtime, jetzt_s - briefkasten.KAPPE_S, delta=60)   # gekappt: sofort reif
        self.assertFalse((puffer / "sitzungen" / sitzung_name).exists())          # wartet auf ihr Match
        self.assertEqual(self.puffer_dateien(), sorted([v.relative_to(puffer).as_posix(), f"replays/{replay_name}"]))
        self.assertEqual(json.loads((self.max / "db" / "pc-status.json").read_text()), {"version": "test"})
        z = self.zeilen()
        self.assertEqual({n: (z[n]["status"], z[n]["ziel"]) for n in z},
                         {video_name: ("abgeholt", f"eingang/nvidia/highlights/{video_name}"),
                          replay_name: ("abgeholt", f"replays/{replay_name}"), sitzung_name: ("offen", None)})
        self.assertEqual((z[video_name]["groesse"], z[video_name]["pc_mtime_ms"]), (len(video.read_bytes()),
                                                                                     pc_video_ms))

        # scan findet beides sofort: das Match mit Clip
        with mock.patch.object(konfig_mod.Konfig, "replay_programm", new=property(lambda k: programm)):
            code, scan = self.pipeline("scan", "--verarbeiten", "--max", "1", "--versuche", "3")
        self.assertEqual((code, scan["neue_matches"], scan["verarbeitet"][0]["clips"]), (0, [sid], 1), scan)

        # jetzt erst die Sitzungsdatei – und sitzungen baut den Abend aus ihr, nie selbst erkannt
        code, e = self.abholen()
        self.assertEqual((code, e["abgeholt"], e["wartet"]), (0, 1, 0), e)
        self.assertTrue((puffer / "sitzungen" / sitzung_name).is_file())
        with als_instanz(self.max):
            k = konfig_mod.lade()
        con = db.verbinde(k.datenbank)
        self.addCleanup(con.close)
        self.assertIs(k.wert("sitzungen.auto_abend"), False)
        selbst = konfig_mod.Konfig({**k.daten, "sitzungen": {**k.daten["sitzungen"], "auto_abend": True}}, k.quelle)
        self.assertIsNotNone(sitzung.auto_abend(con, selbst))     # ohne Briefkasten wäre das schon ein Abend …
        self.assertIsNone(sitzung.auto_abend(con, k))             # … mit Briefkasten nur die Datei vom PC
        with mock.patch.object(sitzung, "_musik", return_value=None):
            neu = sitzung.verarbeite(con, k, claude=False, whisper=False)["neu"]
        self.assertEqual([(n["sitzung"], n["matches"]) for n in neu], [(f"session_{sid}", 1)])


# --- Wichtigster Fehlerfall --------------------------------------------------------------------------------------------

class Fehlerfall(MitBriefkasten):
    def test_abbruch_setzt_fort_falsche_pruefsumme_wird_aufgegeben(self):
        alt = int((time.time() - 7200) * 1000)
        a, b = os.urandom(200_000), os.urandom(50_000)
        self.hochladen("videos", VIDEO, a, mtime_ms=alt)                          # das älteste zuerst
        self.hochladen("videos", VIDEO_B, b, mtime_ms=alt + 60_000, sha256="0" * 64)   # Prüfsumme passt nie
        self.hochladen("replays", REPLAY, b"replay", mtime_ms=alt + 120_000)
        puffer = self.max / "daten"

        code, e = self.abholen(SFTP_ATTRAPPE_ABBRUCH="70000")                    # Verbindung reißt bei 70 000 Byte
        self.assertEqual((code, e["abgeholt"], e["wartet"]), (1, 0, 3), e)
        self.assertEqual(self.puffer_dateien(), [])                               # kein Endname
        z = self.zeilen()
        self.assertEqual({n: (z[n]["status"], z[n]["versuche"]) for n in z},
                         {VIDEO: ("offen", 0), VIDEO_B: ("offen", 0), REPLAY: ("offen", 0)})   # Abriss zählt nicht
        self.assertEqual((puffer / briefkasten.ABLAGE / f"{z[VIDEO]['id']}.teil").stat().st_size, 70_000)

        # Lauf 2 setzt fort: Der Anfang im Fach ist jetzt anders – kommt die Datei trotzdem heil an, wurde nicht neu
        # geholt. B hat eine falsche Prüfsumme (Versuch 1), also wartet das Replay.
        with open(self.fach() / "videos" / VIDEO, "r+b") as f:
            f.write(b"\0" * 70_000)
        code, e = self.abholen()
        self.assertEqual((code, e["abgeholt"], e["wartet"]), (1, 1, 2), e)
        self.assertEqual((puffer / "eingang" / "nvidia" / "aufnahmen" / VIDEO).read_bytes(), a)
        self.assertIn([f'reget "videos/{VIDEO}" "{puffer / briefkasten.ABLAGE / str(z[VIDEO]["id"])}.teil"'],
                      [b for _, b in self.aufrufe])
        self.assertFalse((puffer / "replays" / REPLAY).exists())
        self.assertEqual(self.zeilen()[VIDEO_B]["versuche"], 1)

        for erwartet in ((1, 0, 2), (1, 1, 0)):                                  # B noch zweimal falsch → aufgegeben
            code, e = self.abholen()
            self.assertEqual((code, e["abgeholt"], e["wartet"]), erwartet, e)
        z = self.zeilen()
        self.assertEqual((z[VIDEO_B]["status"], z[REPLAY]["status"]), ("aufgegeben", "abgeholt"))
        self.assertEqual(self.puffer_dateien(), [f"eingang/nvidia/aufnahmen/{VIDEO}", f"replays/{REPLAY}"])
        self.assertIn(VIDEO_B, self.meldungen()[f"briefkasten:aufgegeben:{z[VIDEO_B]['id']}"])
        self.assertEqual(list((puffer / briefkasten.ABLAGE).iterdir()), [])     # keine Reste
        self.assertEqual(self.abholen()[0], 0)                                    # danach: nichts mehr zu tun


# --- Meldungen aus dem Status vom PC (Schritt 4) -----------------------------------------------------------------------

class PcMeldungen(MitBriefkasten):
    """M118: „PC verbunden“ einmal; Zeitzone falsch und Aufnahmen ohne Replay je einmal am Tag – nur aus einem frischen
    Status, nur Zahlen daraus (kein Text vom PC geht weiter)."""

    def status(self, **werte) -> None:
        daten = {"zeit_utc": iso(jetzt()), "utc_offset_min": self.soll, "uebersprungen": [], **werte}
        (self.fach() / "status" / "pc-status.json").write_text(json.dumps(daten), encoding="utf-8")

    def setUp(self):
        super().setUp()
        self.soll = int(utc_zu_lokal(jetzt(), ZONE).utcoffset().total_seconds() // 60)

    def test_verbunden_einmal_zeitzone_und_ohne_replay_je_tag_einmal(self):
        self.status()                                                       # passt alles
        self.assertEqual(self.abholen()[0], 0)
        self.assertEqual(list(self.meldungen()), ["briefkasten:pc_verbunden"])
        self.status(utc_offset_min=self.soll - 60,
                    uebersprungen=[{"grund": "ohne_replay", "anzahl": 2, "beispiel": "<b>nie im Chat</b>"},
                                   {"grund": "name", "anzahl": 5}])
        for _ in range(2):                                                  # zwei Läufe am selben Tag: je einmal
            self.assertEqual(self.abholen()[0], 0)
        tag = utc_zu_lokal(jetzt(), ZONE).date().isoformat()
        m = self.meldungen()
        self.assertEqual(sorted(m), sorted(["briefkasten:pc_verbunden", f"briefkasten:zeitzone:{tag}",
                                            f"briefkasten:ohne_replay:{tag}"]))
        self.assertIn(f"UTC+{(self.soll - 60) // 60}", m[f"briefkasten:zeitzone:{tag}"])
        self.assertIn("2 Aufnahmen ohne Replay", m[f"briefkasten:ohne_replay:{tag}"])
        self.assertNotIn("nie im Chat", "".join(m.values()))

    def test_alter_oder_kaputter_status_meldet_nichts_weiter(self):
        self.status(zeit_utc=iso(jetzt() - timedelta(days=3)), utc_offset_min=0,   # PC seit Tagen aus
                    uebersprungen=[{"grund": "ohne_replay", "anzahl": 1}])
        self.assertEqual(self.abholen()[0], 0)
        self.assertEqual(list(self.meldungen()), ["briefkasten:pc_verbunden"])
        alt = (self.max / "db" / "pc-status.json").read_bytes()
        (self.fach() / "status" / "pc-status.json").write_text("{halb geschrieben", encoding="utf-8")
        self.assertEqual(self.abholen()[0], 0)
        self.assertEqual((self.max / "db" / "pc-status.json").read_bytes(), alt)   # der alte Stand bleibt
        self.assertEqual(list(self.meldungen()), ["briefkasten:pc_verbunden"])


# --- Autorisierung und Isolation ---------------------------------------------------------------------------------------

class Isolation(MitBriefkasten):
    def test_namen_nur_nach_den_mustern_der_pipeline(self):
        with als_instanz(self.max):
            k = konfig_mod.lade()
        gut = {("videos", VIDEO): f"eingang/nvidia/aufnahmen/{VIDEO}",
               ("videos", "Fortnite 2026.10.08 - 20.15.33.02.Eliminierung.mp4"):
                   "eingang/nvidia/highlights/Fortnite 2026.10.08 - 20.15.33.02.Eliminierung.mp4",
               ("videos", "Fortnite__2026-10-08__20-15-33.mp4"): "eingang/steelseries/Fortnite__2026-10-08__20-15-33.mp4",
               ("replays", REPLAY): f"replays/{REPLAY}", ("sitzungen", SITZUNG): f"sitzungen/{SITZUNG}"}
        for (ordner, name), ziel in gut.items():
            self.assertEqual(briefkasten.ziel(k, ordner, name), ziel)
        fremd = [("videos", f"../{VIDEO}"), ("videos", f"sub/{VIDEO}"), ("videos", f".{VIDEO}"),
                 ("videos", "Fortnite [1] 2026.10.08 - 20.15.33.02.DVR.mp4"), ("videos", "Fortnite* 2026.10.08.mp4"),
                 ("videos", 'Fortnite "x" 2026.10.08 - 20.15.33.02.DVR.mp4'),
                 ("videos", "Fortnite 2026.13.45 - 20.15.33.02.DVR.mp4"),          # Datum, das es nicht gibt
                 ("videos", "Fortnite__2026-02-30__20-15-33.mp4"), ("videos", "Valorant 2026.10.08 - 20.15.33.02.mp4"),
                 ("videos", "Fortnite 2026.10.08 - 20.15.33.02.png"), ("videos", f"{VIDEO}.teil"),
                 ("videos", "Fortnite 2026.10.08 - 20.15.33.02.Größe.mp4"), ("videos", "Fortnite " + "x" * 200 + ".mp4"),
                 ("replays", "UnsavedReplay-2026.02.30-20.15.33.replay"), ("replays", "MeinReplay.replay"),
                 ("sitzungen", "session_.json"), ("sitzungen", "abend_2026-10-08_20-00-00.json"),
                 ("sitzungen", f"{SITZUNG}.teil"), ("status", "pc-status.json"), ("videos", REPLAY)]
        for ordner, name in fremd:
            with self.subTest(name=name):
                self.assertIsNone(briefkasten.ziel(k, ordner, name))

    def test_fremdes_und_ungueltiges_wird_nie_uebernommen(self):
        for ordner, name in (("videos", ".Fortnite 2026.10.08 - 20.15.33.02.DVR.mp4"),
                             ("videos", "Fortnite [1] 2026.10.08 - 20.15.33.02.DVR.mp4"),
                             ("videos", "Fortnite 2026.13.45 - 20.15.33.02.DVR.mp4"),
                             ("videos", "Fortnite 2026.10.08 - 20.15.33.02.png"), ("videos", "notiz.txt"),
                             ("replays", "MeinReplay.replay"), ("sitzungen", "abend_2026-10-08_20-00-00.json"),
                             ("videos/unterordner", VIDEO)):
            self.hochladen(ordner, name, b"fremd")
        self.hochladen("videos", VIDEO, b"x" * 10, polster="p" * 5000)           # Lieferschein über 4 KB
        self.hochladen("videos", VIDEO_B, b"x" * 10, name="Fortnite anderer Name.mp4")
        self.hochladen("replays", REPLAY, b"x", roh="{kaputt")
        self.hochladen("sitzungen", SITZUNG, b"{}", sha256="keine-pruefsumme")
        vorher = abdruck(self.max / "daten", ohne=(briefkasten.ABLAGE,))
        for lauf in range(3):
            code, e = self.abholen()
            self.assertEqual((code, e["abgeholt"]), (1, 0), e)
        self.assertEqual(abdruck(self.max / "daten", ohne=(briefkasten.ABLAGE,)), vorher)   # nichts geschrieben
        self.assertEqual(list((self.max / "daten" / briefkasten.ABLAGE).iterdir()), [])
        z = self.zeilen()
        self.assertEqual({n: (z[n]["status"], z[n]["groesse"]) for n in z},
                         {n: ("aufgegeben", None) for n in (VIDEO, VIDEO_B, REPLAY, SITZUNG)})   # fremde: keine Zeile
        self.assertEqual(len(self.meldungen()), 4)
        geholt = [shlex.split(b.lstrip("-"))[1] for _, befehle in self.aufrufe for b in befehle if "get " in b]
        self.assertEqual(set(geholt), {f"{o}/{n}{briefkasten.LIEFERSCHEIN}" for o, n in (
            ("videos", VIDEO), ("videos", VIDEO_B), ("replays", REPLAY), ("sitzungen", SITZUNG))}
                         | {"status/pc-status.json"})                          # nur Lieferscheine, nie eine Datei
        self.assertEqual(self.abholen()[0], 0)                                    # aufgegeben hält nichts mehr auf

    def test_nie_ueberschreiben_nur_eigener_zugang_nur_lesen(self):
        vorhanden = self.max / "daten" / "eingang" / "nvidia" / "aufnahmen" / VIDEO
        vorhanden.parent.mkdir(parents=True)
        vorhanden.write_bytes(b"schon da")
        self.hochladen("videos", VIDEO, b"anderer Inhalt")
        self.hochladen("videos", VIDEO_B, b"gleich")
        (self.max / "daten" / "eingang" / "nvidia" / "aufnahmen" / VIDEO_B).write_bytes(b"gleich")
        self.hochladen("replays", REPLAY, b"replay von eva", freund="eva")
        eva = (abdruck(self.eva), abdruck(self.fach("eva")))
        code, e = self.abholen()
        self.assertEqual((code, e["abgeholt"], e["vermerkt"]), (1, 0, 1), e)    # Konflikt = Fehler, gleicher Inhalt
        self.assertEqual(vorhanden.read_bytes(), b"schon da")                      # nie überschrieben
        z = self.zeilen()
        self.assertEqual((z[VIDEO]["status"], z[VIDEO_B]["status"]), ("konflikt", "vermerkt"))
        self.assertTrue((self.fach() / "videos" / VIDEO).is_file())               # bleibt im Fach
        self.assertEqual((abdruck(self.eva), abdruck(self.fach("eva"))), eva)     # eva unberührt
        self.assertFalse((self.max / "daten" / "replays" / REPLAY).exists())      # ihr Replay kommt nie zu max
        for argv, befehle in self.aufrufe:
            optionen = dict(argv[i + 1].split("=", 1) for i, a in enumerate(argv) if a == "-o")
            self.assertEqual(argv[-1], f"bk-max@{HOST}")
            self.assertEqual([argv[i + 1] for i, a in enumerate(argv) if a in ("-F", "-b", "-P", "-l")],
                             ["/dev/null", "-", "2222", "20000"])
            self.assertEqual(optionen, {"BatchMode": "yes", "IdentitiesOnly": "yes", "StrictHostKeyChecking": "yes",
                                        "IdentityFile": str(self.max / "briefkasten" / "abholen"),
                                        "UserKnownHostsFile": str(self.max / "briefkasten" / "known_hosts"),
                                        "GlobalKnownHostsFile": "/dev/null", "ConnectTimeout": "10",
                                        "ServerAliveInterval": "15", "ServerAliveCountMax": "4"})
            for b in befehle:
                teile = shlex.split(b.lstrip("-"))
                self.assertIn(teile[0], ("ls", "df", "get", "reget"), b)            # nur lesen, nie put/rm/rename
                if teile[0] in ("get", "reget"):
                    self.assertTrue(konfig_mod.liegt_in(teile[2], self.max / "daten" / briefkasten.ABLAGE), b)

    def test_instanz_toml_kann_benutzer_und_schluessel_nicht_umbiegen(self):
        for zeile in ('benutzer = "bk-eva"', f'schluessel = "{self.eva / "briefkasten" / "abholen"}"',
                      'known_hosts = "/dev/null"'):
            (self.max / "instanz.toml").write_text(f'[sperre]\ndatei = "{self.sperrdatei}"\n[briefkasten]\n'
                                                   f'host = "{HOST}"\n{zeile}\n', encoding="utf-8")
            with self.subTest(zeile=zeile):
                code, e = self.pipeline("briefkasten", "abholen")
                self.assertEqual(code, 2)
                self.assertIn("nicht erlaubt", e["fehler"])
        self.assertEqual(self.aufrufe, [])


# --- Florian unverändert -----------------------------------------------------------------------------------------------

class Florian(MitSpeicher):
    def test_ohne_instanz_exit_2_nichts_angefasst(self):
        vorher = abdruck(self.tmp)
        for aktion in ("abholen", "status"):
            aus = io.StringIO()
            with mock.patch.object(cli, "lade", return_value=self.konfig), \
                    mock.patch.object(briefkasten, "_sftp", side_effect=AssertionError("sftp bei Florian")), \
                    contextlib.redirect_stdout(aus), contextlib.redirect_stderr(io.StringIO()):
                code = cli.main(["briefkasten", aktion])
            self.assertEqual(code, 2)
            self.assertIn("nur in der Instanz eines Freundes", json.loads(aus.getvalue().strip())["hinweis"])
        with self.assertRaises(konfig_mod.KonfigFehler):              # auch direkt aufgerufen: nie bei Florian
            briefkasten.abholen(self.con, self.konfig)
        self.assertEqual(abdruck(self.tmp), vorher)                   # keine Datei, keine Sperre, keine Zeile
        tabellen = {z[0] for z in self.con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertNotIn("abholung", tabellen)
        self.assertEqual((self.konfig.wert("sitzungen.auto_abend"), self.konfig.wert("briefkasten.host")), (True, ""))
        self.assertNotIn("briefkasten", cli.WECKEN)
        self.assertFalse(cli.baue_parser().parse_args(["briefkasten", "abholen"]).sperren)   # nie die Rechen-Sperre


# --- Grenzen -----------------------------------------------------------------------------------------------------------

class Grenzen(MitBriefkasten):
    def test_nie_die_rechen_sperre_eigene_sperre_exit_4(self):
        self.hochladen("videos", VIDEO, b"video")
        with open(self.sperrdatei) as florian:                       # Florian rechnet gerade (n8n-Schritt)
            fcntl.flock(florian, fcntl.LOCK_EX)
            code, e = self.abholen()
        self.assertEqual((code, e["abgeholt"]), (0, 1), e)           # der Abholer hält ihn nie auf
        with open(briefkasten.sperrdatei(self.konfig_max()), "a") as eigene:   # ein zweiter Abholer läuft schon
            fcntl.flock(eigene, fcntl.LOCK_EX)
            code, e = self.abholen()
        self.assertEqual((code, e["fehler"]), (4, "gesperrt"))

    def konfig_max(self) -> konfig_mod.Konfig:
        with als_instanz(self.max):
            return konfig_mod.lade()

    def test_bremse_unerreichbar_nicht_eingehaengt(self):
        self.hochladen("videos", VIDEO, os.urandom(1000))
        with mock.patch.object(briefkasten, "_platz", return_value=(10 * 10**9, 100 * 10**9)):   # danach < 10 GB frei
            for _ in range(2):
                code, e = self.abholen()
                self.assertEqual((code, e["abgeholt"], e["wartet"], e["bremse"]), (0, 0, 1, True), e)
        self.assertEqual(self.puffer_dateien(), [])
        self.assertEqual([s.rsplit(":", 1)[0] for s in self.meldungen()], ["briefkasten:platz"])   # einmal am Tag

        code, e = self.abholen(SFTP_ATTRAPPE_AUS="1")
        self.assertEqual((code, e.get("unerreichbar")), (3, True), e)
        self.assertEqual(len(self.meldungen()), 1)                   # eben noch erreicht: noch keine Zeile
        stand = self.max / "db" / "briefkasten.json"
        daten = json.loads(stand.read_text())
        daten["zuletzt_erreicht"] = iso(jetzt() - timedelta(hours=25))
        stand.write_text(json.dumps(daten))
        for _ in range(2):
            self.assertEqual(self.abholen(SFTP_ATTRAPPE_AUS="1")[0], 3)
        self.assertIn("seit über einem Tag", "".join(t for s, t in self.meldungen().items() if "unerreichbar" in s))
        self.assertEqual(len(self.meldungen()), 2)

        (self.fach() / briefkasten.MARKE).unlink()                    # Fach nicht eingehängt
        code, e = self.abholen()
        self.assertEqual(code, 3)
        self.assertIn("nicht eingehängt", e["fehler"][0])
        code, e = self.pipeline("briefkasten", "status")
        self.assertEqual((code, e["offen"], e["pc_status"]), (0, 1, False), e)
        self.assertIn("nicht eingehängt", e["letzter_fehler"])

    def test_sitzung_spaetestens_nach_24_h_doppelte_nur_vermerkt(self):
        inhalt = json.dumps({"matches": ["2026-10-08_20-00-00"], "ende_utc": "2026-10-08T18:20:00Z"}).encode()
        self.hochladen("sitzungen", SITZUNG, inhalt)
        self.assertEqual(self.abholen()[1]["wartet"], 1)              # das Match ist noch nicht da
        con = sqlite3.connect(self.max / "db" / "pipeline.db")
        con.execute("UPDATE abholung SET gesehen = ?", (iso(jetzt() - timedelta(hours=25)),))
        con.commit()
        con.close()
        code, e = self.abholen()
        self.assertEqual((code, e["abgeholt"]), (0, 1), e)            # spätestens nach 24 h trotzdem
        self.assertEqual((self.max / "daten" / "sitzungen" / SITZUNG).read_bytes(), inhalt)
        doppelt = "session_2026-10-09_09-00-00.json"                  # nach einer Neuinstallation noch einmal
        self.hochladen("sitzungen", doppelt, inhalt)
        self.hochladen("sitzungen", "session_2026-10-09_10-00-00.json", b'{"matches": "kein Liste"}')
        code, e = self.abholen()
        self.assertEqual((code, e["abgeholt"], e["vermerkt"], e["aufgegeben"]), (1, 0, 1, 1), e)
        self.assertEqual(sorted(p.name for p in (self.max / "daten" / "sitzungen").iterdir()), [SITZUNG])


# --- Echte Zeilen aus OpenSSH 9.6 ----------------------------------------------------------------------------------------

# stdout des Listen-Laufs gegen echten sshd 9.6p1 (internal-sftp -R, chroot), aufgenommen am 09.10.2026 – der Ordner der
# Zwischenablage ist durch /W ersetzt. Ein Name mit Leerzeichen, Platzhaltern oder Anführungszeichen bleibt eine Zeile.
ECHT_96 = (
    'sftp> ls -1 -a /fach\n/fach/.\n/fach/..\n/fach/.clip-briefkasten\n/fach/replays\n/fach/sitzungen\n/fach/status\n'
    '/fach/videos\nsftp> -df\n        Size         Used        Avail       (root)    %Capacity\n'
    '       16384          340        16044        16044           2%\n'
    'sftp> -get status/pc-status.json "/W/I/daten/.abholen/pc-status.json"\nsftp> -ls -1 sitzungen\n'
    'sitzungen/session_2026-10-08_20-15-33.json\nsitzungen/session_2026-10-08_20-15-33.json.lieferschein\n'
    'sftp> -ls -1 replays\nreplays/UnsavedReplay-2026.10.08-20.15.33.replay.teil\nsftp> -ls -1 videos\n'
    'videos/#hash.mp4\nvideos/Fortnite 2026.10.08 - 20.15.33.02.DVR.mp4\n'
    'videos/Fortnite 2026.10.08 - 20.15.33.02.DVR.mp4.lieferschein\n'
    'videos/Fortnite 2026.10.08 - 20.15.40.03.Am Boden.mp4.teil\nvideos/a$b,c(d)+e=f&g;h.mp4\nvideos/apo\'st.mp4\n'
    'videos/back\\slash.mp4\n')


class EchteZeilen(unittest.TestCase):
    def test_listen_lauf_aus_openssh_96(self):
        befehle = briefkasten._listen_befehle(Path("/W/I/daten/.abholen/pc-status.json"))
        fach = briefkasten.lies_liste(ECHT_96, befehle)
        self.assertEqual((fach.marke, fach.prozent, fach.frei_gb), (True, 2, 0.02))
        self.assertEqual(fach.namen["sitzungen"], {"session_2026-10-08_20-15-33.json",
                                                   "session_2026-10-08_20-15-33.json.lieferschein"})
        self.assertEqual(fach.namen["replays"], {"UnsavedReplay-2026.10.08-20.15.33.replay.teil"})
        self.assertIn("Fortnite 2026.10.08 - 20.15.40.03.Am Boden.mp4.teil", fach.namen["videos"])
        self.assertEqual(len(fach.namen["videos"]), 7)
        # Ein Dateiname mit Zeilenumbruch in sitzungen/ kann keinen späteren Abschnitt vortäuschen, „/“ im Namen zählt
        # nie, und ohne Marke ist das Fach nicht eingehängt
        falsch = ECHT_96.replace("sitzungen/session_2026-10-08_20-15-33.json\n", "sitzungen/x\nsftp> -ls -1 videos\n"
                                 "videos/Fortnite 2026.10.08 - 20.20.00.04.DVR.mp4\nvideos/../boese.mp4\n")
        namen = briefkasten.lies_liste(falsch, befehle).namen
        self.assertEqual(namen["videos"], fach.namen["videos"])
        self.assertNotIn("../boese.mp4", briefkasten.lies_liste(ECHT_96 + "videos/../boese.mp4\n", befehle).namen["videos"])
        self.assertFalse(briefkasten.lies_liste(ECHT_96.replace("/fach/.clip-briefkasten\n", ""), befehle).marke)


# --- Echter sshd (nur als root, sonst übersprungen) ---------------------------------------------------------------------

STARTER = """
import os, sys
from clip_pipeline import briefkasten, cli
briefkasten.SFTP = os.environ["TEST_SFTP"]
briefkasten._platz = lambda pfad: (10**12, 2 * 10**12)   # die Platte des Testrechners zählt nicht
sys.exit(cli.main(sys.argv[1:]))
"""

TREIBER = r'''set -euo pipefail
W="$1"; REPO="$2"; SSHD_ECHT="$3"; LIBS="$4"; PY="$5"
PUBLIC=198.51.100.7; TS=100.100.1.1; MINI=100.100.1.2
python3 -I "$W/netz.py" "$TS" "$MINI" "$PUBLIC"
mount -t tmpfs -o mode=0755 tmpfs /srv
mount -t tmpfs -o mode=0755 tmpfs /run
mkdir -p /run/sshd /run/systemd/system /srv/units /srv/ablage
: > /srv/fstab
cp /etc/passwd "$W/passwd"; cp /etc/group "$W/group"; cp /etc/shadow "$W/shadow" 2>/dev/null || : > "$W/shadow"
grep -q '^sshd:' "$W/passwd" || { echo 'sshd:x:990:65534::/run/sshd:/usr/sbin/nologin' >> "$W/passwd"
                                  echo 'sshd:*:20000:0:99999:7:::' >> "$W/shadow"; }
mount --bind "$W/passwd" /etc/passwd; mount --bind "$W/group" /etc/group; mount --bind "$W/shadow" /etc/shadow
export PATH="$W/stub:$PATH" STUB="$W/stub" LIBS SSHD_ECHT TS_IP="$TS" BK_ETC=/srv/etc-briefkasten UNITS=/srv/units \
       ABLAGE=/srv/ablage FSTAB=/srv/fstab
bash "$REPO/deploy/vserver/briefkasten-einrichten.sh" --mini-ip "$MINI" > "$W/einrichten.log" 2>&1 <<< $'j\nj\nj\n'
for r in pc mini; do ssh-keygen -q -t ed25519 -N '' -C "max-$r" -f "$W/max-$r"; done
bash "$REPO/deploy/vserver/briefkasten-freund.sh" max --groesse 8 --pc "$(cat "$W/max-pc.pub")" \
  --abholen "$(cat "$W/max-mini.pub")" > "$W/freund.log" 2>&1 <<< $'j\n'
LD_LIBRARY_PATH="$LIBS" "$SSHD_ECHT" -D -e -f "$BK_ETC/sshd_config" 2> "$W/sshd.log" &
SSHD=$!
trap 'kill $SSHD 2>/dev/null || true' EXIT
for _ in $(seq 50); do python3 -I -c "import socket; socket.create_connection(('$PUBLIC', 2222), 1)" 2>/dev/null && break
  sleep 0.1; done
H="$(cut -d' ' -f1,2 "$BK_ETC/hostkey_ed25519.pub")"
printf '[%s]:2222 %s\n' "$PUBLIC" "$H" > "$W/known_hosts_pc"
I="$W/freunde/max"
cp "$W/max-mini" "$I/briefkasten/abholen"
printf '[%s]:2222 %s\n' "$TS" "$H" > "$I/briefkasten/known_hosts"
# der PC lädt hoch wie später sein Programm: .teil, umbenennen, Lieferschein (über die öffentliche Adresse)
(cd "$W/pc" && sftp -q -b "$W/pc.batch" -P 2222 -i "$W/max-pc" -F /dev/null -o IdentitiesOnly=yes -o BatchMode=yes \
   -o UserKnownHostsFile="$W/known_hosts_pc" -o GlobalKnownHostsFile=/dev/null -o StrictHostKeyChecking=yes \
   -o BindAddress="$PUBLIC" "bk-max@$PUBLIC") > "$W/pc.log" 2>&1
# der Mini holt ab: über das Tailnet, von seiner Adresse (die echte Quelladresse wählt sonst der Kernel)
printf '#!/bin/sh\nexec /usr/bin/sftp -o BindAddress=%s "$@"\n' "$MINI" > "$W/sftp-mini"
chmod 755 "$W/sftp-mini"
for n in 1 2; do
  rc=0
  env -i PATH=/usr/bin:/bin LANG=C.UTF-8 HOME="$I/cache" CLIP_INSTANZ="$I" TEST_SFTP="$W/sftp-mini" \
    PYTHONPATH="$REPO/src" PYTHONDONTWRITEBYTECODE=1 "$PY" "$W/starter.py" briefkasten abholen \
    > "$W/abholen$n.out" 2> "$W/abholen$n.err" || rc=$?
  echo "$rc" > "$W/abholen$n.rc"
done
'''


class EchterSshd(unittest.TestCase):
    """Ein Freund lädt mit echtem sftp hoch, der Mini holt mit `pipeline briefkasten abholen` gegen echten sshd 9.6 ab –
    in einem privaten Mount- und Netz-Namensraum mit den Skripten aus Schritt 1; am System ändert sich nichts."""

    @classmethod
    def setUpClass(cls):
        from tests.test_deploy_briefkasten import _echter_sshd_moeglich, echte_attrappen

        if grund := _echter_sshd_moeglich():
            raise unittest.SkipTest(grund)
        cls._tmp = tempfile.TemporaryDirectory(prefix="clip-abholen-")
        w = cls.w = Path(os.path.realpath(cls._tmp.name))
        sshd, libs = echte_attrappen(w)
        (w / "treiber.sh").write_text(TREIBER, encoding="utf-8")
        (w / "starter.py").write_text(STARTER, encoding="utf-8")
        sperre = w / "florian" / "pipeline.lock"
        sperre.parent.mkdir()
        sperre.touch()
        cls.inst = instanz_anlegen(w / "freunde", "max", sperre=sperre, toml='[briefkasten]\nhost = "100.100.1.1"\n')
        (cls.inst / "briefkasten").mkdir()
        cls.dateien = {"videos": (VIDEO, os.urandom(400_000)), "replays": (REPLAY, os.urandom(20_000)),
                       "sitzungen": (SITZUNG, json.dumps({"matches": ["2026-10-08_20-00-00"],
                                                          "ende_utc": "2026-10-08T18:20:00Z"}).encode())}
        cls.pc_ms = int((time.time() - 3600) * 1000)
        batch = []
        for ordner, (name, inhalt) in cls.dateien.items():
            (w / "pc" / ordner).mkdir(parents=True)
            (w / "pc" / ordner / name).write_bytes(inhalt)
            (w / "pc" / ordner / f"{name}.lieferschein").write_text(json.dumps(
                {"name": name, "groesse": len(inhalt), "sha256": hashlib.sha256(inhalt).hexdigest(),
                 "mtime_ms": cls.pc_ms, "utc_offset_min": 120}), encoding="utf-8")
            for n in (name, f"{name}.lieferschein"):
                batch += [f'put "{ordner}/{n}" "{ordner}/{n}.teil"', f'rename "{ordner}/{n}.teil" "{ordner}/{n}"']
        (w / "pc" / "status.json").write_text('{"version": "echt"}', encoding="utf-8")
        batch.append('put "status.json" "status/pc-status.json"')
        (w / "pc.batch").write_text("\n".join(batch) + "\n", encoding="utf-8")
        cls.lauf = subprocess.run(["timeout", "300", "unshare", "-m", "-n", "--propagation", "private", "bash",
                                   str(w / "treiber.sh"), str(w), str(PROJEKT), sshd, libs, sys.executable],
                                  capture_output=True, text=True, timeout=330)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def ergebnis(self, n: int) -> tuple[int, dict]:
        rc = self.w / f"abholen{n}.rc"
        log = "".join((self.w / f).read_text(errors="replace") for f in ("pc.log", f"abholen{n}.err", "sshd.log")
                      if (self.w / f).exists())
        self.assertTrue(rc.exists(), self.lauf.stdout + self.lauf.stderr + log)
        return int(rc.read_text()), json.loads((self.w / f"abholen{n}.out").read_text().strip().splitlines()[-1])

    def test_hochladen_und_abholen_ende_zu_ende(self):
        code, e = self.ergebnis(1)
        self.assertEqual((code, e["abgeholt"], e["wartet"], e["fehler"]), (0, 2, 1, []), e)   # Sitzung wartet
        self.assertIsInstance(e["fach_prozent"], int)                                        # echtes df gelesen
        puffer = self.inst / "daten"
        for ordner, ziel in (("videos", f"eingang/nvidia/aufnahmen/{VIDEO}"), ("replays", f"replays/{REPLAY}")):
            pfad = puffer / ziel
            self.assertEqual(pfad.read_bytes(), self.dateien[ordner][1])
            self.assertEqual(pfad.stat().st_mtime_ns // 10**6, self.pc_ms)
        self.assertEqual(json.loads((self.inst / "db" / "pc-status.json").read_text()), {"version": "echt"})
        code, e = self.ergebnis(2)
        self.assertEqual((code, e["abgeholt"], e["wartet"]), (0, 0, 1), e)                  # nichts doppelt


if __name__ == "__main__":
    unittest.main()
