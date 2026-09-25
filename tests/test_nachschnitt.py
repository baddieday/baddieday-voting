"""Nachschnitt (`pipeline momente nachschneiden`): vorhandene Multikill-Momente ab der ersten Aktion neu schneiden.

Aufbau wie im getrennten Betrieb (E19): Speicher = Puffer (mit .clip-puffer), daneben ein Lager (mit .clip-lager).
Das Replay ist anonymisiert und dem echten Team-Wipe nachgebaut: drei Gegner umgehauen (10,0 s, 14,0 s, 25,0 s in
der Aufnahme), alle drei sterben beim Wipe (25,2 s). Der Bot-Clip beginnt wie bisher 8 s vor dem Kill (17,2 s) –
das erste Umhauen fehlt darin. Neu: ab 8 s vor dem ersten Umhauen (2,0 s) bis zum alten Ende (30,2 s).
"""

import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

from clip_pipeline import cli, nachschnitt, regie, stimmung
from clip_pipeline.konfig import KonfigFehler
from clip_pipeline.medien import probe
from clip_pipeline.zeit import iso, jetzt

from tests.hilfen import HAT_FFMPEG, MitSpeicher, testvideo

SITZUNG = "2026-09-20_20-00-00"
QUELLE = "eingang/steelseries/Fortnite__2026-09-20__20-05-00.mp4"
REPLAY_VOR_S = 300.0  # das Replay beginnt 5 min vor der Aufnahme
ROT, BLAU = (230, 30, 30), (30, 30, 230)
# (Sekunde in der Aufnahme, Täter, Opfer, umgehauen?) – Team-Wipe wie im echten Match 2026-09-13
WIPE = [(10.0, "ICH", "GEGNER-A", True), (14.0, "ICH", "GEGNER-B", True), (25.0, "ICH", "GEGNER-C", True),
        (25.2, "ICH", "GEGNER-A", False), (25.2, "ICH", "GEGNER-B", False), (25.2, "ICH", "GEGNER-C", False)]
MERKMALE = {"kills": 3, "max_gruppe": 3, "victory_royale": 0, "tod": 0, "umgehauen": 0,
            "kill_sekunden": [8.0, 8.0, 8.0], "tod_sekunde": None, "dauer_s": 13.0, "mikro_spur": 1,
            "spitzen_s": [5.9], "spitzen": 1, "energie": 0.24, "jubel_laut_s": [9.7, 11.1], "jubel_laut": 2,
            "lachen": 0, "jubel": 0, "frust": 0, "punkte": {"episch": 6.0, "lustig": 0.0, "spannend": 2.1,
                                                              "frustriert": 0.0, "chill": 1.0}}


def zweifarbig(ziel: Path, dauer: float, wechsel_s: float) -> Path:
    """Aufnahme: bis wechsel_s rot, danach blau (zwei Tonspuren) – so sieht man, wo geschnitten wurde."""
    ziel.parent.mkdir(parents=True, exist_ok=True)
    farbe = lambda f: "0x%02x%02x%02x" % f  # noqa: E731
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", f"color=c={farbe(ROT)}:s=320x180:r=30:d={wechsel_s}",
                    "-f", "lavfi", "-i", f"color=c={farbe(BLAU)}:s=320x180:r=30:d={dauer - wechsel_s}",
                    "-f", "lavfi", "-i", f"sine=frequency=300:duration={dauer}",
                    "-f", "lavfi", "-i", f"sine=frequency=600:duration={dauer}",
                    "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]", "-map", "2:a", "-map", "3:a",
                    "-metadata:s:a:0", "handler_name=Game", "-metadata:s:a:1", "handler_name=Chat",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                    str(ziel)], check=True)
    return ziel


def farbe_bei(video: Path, sekunde: float) -> tuple[int, int, int]:
    roh = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{sekunde:.3f}", "-i", str(video),
                          "-frames:v", "1", "-vf", "scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True).stdout
    return roh[0], roh[1], roh[2]


def ist_rot(rgb) -> bool:
    return sum((a - b) ** 2 for a, b in zip(rgb, ROT)) < sum((a - b) ** 2 for a, b in zip(rgb, BLAU))


def fingerabdruck(pfad: Path) -> tuple[str, int]:
    return hashlib.sha256(pfad.read_bytes()).hexdigest(), pfad.stat().st_mtime_ns


@contextlib.contextmanager
def beobachte(wurzel: Path):
    """Zeichnet jeden Zugriff auf, der – auch über einen Link – unter wurzel landet (stat, open, Programme)."""
    grenze = os.path.realpath(wurzel) + os.sep
    treffer: list[str] = []
    echt_stat, echt_open, echt_run = os.stat, io.open, subprocess.run

    def pruefe(arg, folgen=True):
        if isinstance(arg, (str, os.PathLike)):
            pfad = os.fspath(arg)
            echt = (os.path.realpath(pfad) if folgen else
                    os.path.join(os.path.realpath(os.path.dirname(pfad) or "."), os.path.basename(pfad)))
            if (echt + os.sep).startswith(grenze):
                treffer.append(pfad)

    def stat_(pfad, *a, **kw):
        pruefe(pfad, kw.get("follow_symlinks", True))
        return echt_stat(pfad, *a, **kw)

    def open_(pfad, *a, **kw):
        pruefe(pfad)
        return echt_open(pfad, *a, **kw)

    def run_(befehl, *a, **kw):
        for teil in befehl if isinstance(befehl, (list, tuple)) else [befehl]:
            pruefe(teil)
        return echt_run(befehl, *a, **kw)

    with mock.patch("os.stat", stat_), mock.patch("io.open", open_), mock.patch("builtins.open", open_), \
            mock.patch("subprocess.run", run_):
        yield treffer


def cli_lauf(test, argv: list[str]) -> tuple[int, dict, str]:
    """cli.main mit der Test-Konfig: (Exit, letzte stdout-Zeile als JSON, stderr)."""
    aus, fehler = io.StringIO(), io.StringIO()
    with mock.patch("clip_pipeline.cli.lade", return_value=test.konfig), \
            contextlib.redirect_stdout(aus), contextlib.redirect_stderr(fehler):
        code = cli.main(argv)
    return code, json.loads(aus.getvalue().strip().splitlines()[-1]), fehler.getvalue()


class MitPuffer(MitSpeicher):
    """Getrennter Betrieb mit Replay, Aufnahme, Clip (Bot-Clip-Datei) und Moment – ohne Videos."""

    def setUp(self):
        super().setUp()
        self.puffer = self.konfig.wurzel
        self.lager = self.tmp / "lager"
        self.lager.mkdir()
        (self.lager / ".clip-lager").touch()
        (self.puffer / ".clip-puffer").touch()
        self.konfig.daten["lager"]["wurzel"] = str(self.lager)
        self.konfig.daten["material"] = {"ordner": str(self.tmp / "mini")}
        self.konfig.daten.setdefault("vorbewertung", {})["puffer_vorne_s"] = 8.0
        self.konfig.daten.setdefault("regie", {})["ordner"] = str(self.tmp / "regie")
        self.konfig.daten["musik"]["ordner"] = str(self.tmp / "musik")
        self.t0 = (jetzt() - timedelta(days=1)).replace(microsecond=0)  # Beginn der Aufnahme

    # --- Bausteine ------------------------------------------------------------------------------

    def replay(self, sitzung: str, ereignisse=WIPE) -> None:
        ordner = self.konfig.ordner("sessions") / sitzung
        ordner.mkdir(parents=True, exist_ok=True)
        (ordner / "replay.json").write_text(json.dumps({
            "replay_start": iso(self.t0 - timedelta(seconds=REPLAY_VOR_S)), "replay_start_kind": "Utc",
            "laenge_ms": 1_200_000, "ich_quelle": "konfig", "ich": {"epic_id": "ICH", "platzierung": 4},
            "eliminierungen": [{"t_ms": round((REPLAY_VOR_S + s) * 1000), "eliminator": taeter, "eliminiert": opfer,
                                "knock": knock, "selbst": False} for s, taeter, opfer, knock in ereignisse],
        }), encoding="utf-8")

    def aufnahme(self, rel: str = QUELLE, *, dauer: float = 40.0, datei: Path | None = None) -> None:
        """Zeile in aufnahmen; datei: Video, das in den Puffer kopiert wird (None = keine Datei)."""
        if datei is not None:
            ziel = self.puffer / rel
            ziel.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(datei, ziel)
        self.con.execute(
            """INSERT INTO aufnahmen (pfad, groesse, geaendert, quelle, start_utc, ende_utc, dauer_s, tonspuren, fps,
                                      ereignisse, erfasst) VALUES (?, 1, 1, 'steelseries', ?, ?, ?, 2, 30, '[]', ?)""",
            (rel, iso(self.t0), iso(self.t0 + timedelta(seconds=dauer)), dauer, iso(jetzt())))

    def clip(self, *, start_s: float = 17.2, ende_s: float = 30.2, rel: str = QUELLE, sitzung: str = SITZUNG,
             kills: int = 3, status: str = "freigegeben", bot_clip: Path | None = None, t0=None,
             moment: bool = True) -> int:
        """Clip wie aus render (Tabelle clips) plus Moment wie aus stimmung (Bot-Clip-Datei)."""
        t0 = t0 or self.t0
        if not self.con.execute("SELECT 1 FROM matches WHERE id = ?", (sitzung,)).fetchone():
            self.con.execute("INSERT INTO matches (id, replay_pfad, start_utc, ende_utc, erstellt, geaendert)"
                             " VALUES (?, ?, ?, ?, 'x', 'x')", (sitzung, f"replays/{sitzung}.replay", iso(t0), iso(t0)))
        nr = self.con.execute("SELECT COUNT(*) FROM clips WHERE match_id = ?", (sitzung,)).fetchone()[0] + 1
        rel_clip = f"sessions/{sitzung}/clips/{nr:03d}_triple_{kills}k.mp4"
        datei = self.puffer / rel_clip
        datei.parent.mkdir(parents=True, exist_ok=True)
        if bot_clip is not None:
            shutil.copy2(bot_clip, datei)
        else:
            datei.write_bytes(b"bot-clip")
        start, ende = t0 + timedelta(seconds=start_s), t0 + timedelta(seconds=ende_s)
        cid = self.con.execute(
            """INSERT INTO clips (match_id, nr, status, titel, typ, kills, max_gruppe, kill_zeiten, start_utc, ende_utc,
                                  quelle_pfad, quelle_start_s, quelle_ende_s, merkmale, punkte, begruendung, clip_pfad,
                                  elo, erstellt, geaendert)
               VALUES (?, ?, ?, 'Triple Kill', 'triple', ?, ?, '[]', ?, ?, ?, ?, ?, '{}', 6, 'Test', ?, 1520, 'x', 'x')""",
            (sitzung, nr, status, kills, kills, iso(start), iso(ende), rel, start_s, ende_s, rel_clip)).lastrowid
        if moment:
            self.con.execute(
                """INSERT INTO momente (schluessel, clip_id, match_id, datei, start_s, ende_s, start_utc, kills, stimmung,
                                        sicherheit, quelle, merkmale, text, erstellt, geaendert)
                   VALUES (?, ?, ?, ?, 0, ?, ?, ?, 'episch', 0.6, 'regel', ?, 'yes', 'x', 'x')""",
                (f"clip:{cid}", cid, sitzung, str(datei), round(ende_s - start_s, 3), iso(start), kills,
                 json.dumps(MERKMALE)))
        return cid

    def zeile(self, cid: int):
        return self.con.execute("SELECT * FROM momente WHERE schluessel = ?", (f"clip:{cid}",)).fetchone()

    def alle(self, tabelle: str) -> list[dict]:
        return [dict(z) for z in self.con.execute(f"SELECT * FROM {tabelle} ORDER BY rowid")]

    def dateien(self) -> dict[str, tuple[int, int]]:
        return {p.relative_to(self.tmp).as_posix(): (p.lstat().st_size, p.lstat().st_mtime_ns)
                for p in sorted(self.tmp.rglob("*")) if not p.is_dir() and not p.name.startswith("test.")}

    def ziel(self, cid: int, start_ms: int = 2000, ende_ms: int = 30200) -> Path:
        return self.konfig.ordner("sessions") / SITZUNG / "momente" / f"clip{cid:05d}_{start_ms}-{ende_ms}.mp4"


# --- Pfade: nur im Puffer, nie über Links ---------------------------------------------------------

class Pfade(MitPuffer):
    def test_quelle_im_puffer(self):
        (self.puffer / "eingang" / "a").mkdir(parents=True)
        (self.puffer / "eingang" / "a" / "x.mp4").write_bytes(b"x")
        (self.lager / "eingang" / "b").mkdir(parents=True)
        (self.lager / "eingang" / "b" / "y.mp4").write_bytes(b"y")
        (self.puffer / "eingang" / "b").symlink_to(self.lager / "eingang" / "b")            # Ordner-Link
        (self.puffer / "eingang" / "a" / "y.mp4").symlink_to(self.lager / "eingang" / "b" / "y.mp4")  # Datei-Link
        q = nachschnitt._quelle_im_puffer
        self.assertEqual(q(self.konfig, "eingang/a/x.mp4"), self.puffer / "eingang" / "a" / "x.mp4")
        for rel in ("eingang/a/fehlt.mp4", "eingang/b/y.mp4", "eingang/a/y.mp4", "eingang/a", "/etc/passwd",
                    "eingang/../eingang/a/x.mp4", "eingang/./a/x.mp4", "eingang\\a\\x.mp4", "", "eingang//a/x.mp4"):
            self.assertIsNone(q(self.konfig, rel), rel)
        # Die Wurzel selbst darf ein Link sein (/srv/clips -> /srv/puffer)
        link = self.tmp / "clips"
        link.symlink_to(self.puffer)
        self.konfig.daten["speicher"]["wurzel"] = str(link)
        self.assertEqual(q(self.konfig, "eingang/a/x.mp4"), link / "eingang" / "a" / "x.mp4")

    def test_zielordner_link_wird_abgelehnt(self):
        (self.lager / "fremd").mkdir()
        sessions = self.konfig.ordner("sessions")
        (sessions / SITZUNG).symlink_to(self.lager / "fremd")
        with self.assertRaises(nachschnitt.NachschnittFehler):
            nachschnitt._zielordner(self.konfig, SITZUNG)
        self.assertEqual(list((self.lager / "fremd").iterdir()), [])
        self.assertEqual(nachschnitt._zielordner(self.konfig, "2026-09-20_22-00-00"),
                         sessions / "2026-09-20_22-00-00" / "momente")


class Auswahl(MitPuffer):
    """Was nicht passt, bleibt wie es ist – ohne FFmpeg (kein Moment braucht hier einen Schnitt)."""

    def test_alte_und_fremde_daten_bleiben(self):
        self.replay(SITZUNG)
        self.aufnahme()
        einzel = self.clip(kills=1)                                    # Einzelkill: nicht gewählt
        verworfen = self.clip(status="verworfen")                      # verworfen: nicht gewählt
        alt = self.clip(t0=self.t0 - timedelta(days=30))               # älter als --tage: nicht gewählt
        ohne_replay = self.clip(sitzung="2026-09-01_20-00-00")         # kein replay.json
        ohne_aufnahme = self.clip(rel="eingang/steelseries/weg.mp4")   # keine Zeile in aufnahmen
        passt_nicht = self.clip(start_s=0.0, ende_s=9.0)               # im Fenster kein Kill (DB sagt 3)
        self.con.execute(
            """INSERT INTO momente (schluessel, datei, start_s, ende_s, kills, stimmung, sicherheit, quelle, merkmale,
                                    erstellt, geaendert) VALUES ('datei:eingang/x.mp4', '/x.mp4', 0, 20, 0, 'chill',
                                    0.5, 'regel', '{}', 'x', 'x')""")
        vorher_momente, vorher_clips, vorher_dateien = self.alle("momente"), self.alle("clips"), self.dateien()
        e = nachschnitt.nachschneiden(self.con, self.konfig, tage=14)
        self.assertEqual({k: e[k] for k in nachschnitt.ZAEHLER},
                         {"geprueft": 3, "neu_geschnitten": 0, "nur_merkmale": 0, "schon_erledigt": 0, "ohne_quelle": 0,
                          "ohne_replay": 1, "ohne_aufnahme": 1, "passt_nicht": 1, "fehler": 0, "gekappt_60s": 0})
        self.assertEqual({b["moment"]: b["ergebnis"] for b in e["beispiele"]},
                         {f"clip:{ohne_replay}": "ohne_replay", f"clip:{ohne_aufnahme}": "ohne_aufnahme",
                          f"clip:{passt_nicht}": "passt_nicht"})
        self.assertEqual((self.alle("momente"), self.alle("clips"), self.dateien()),
                         (vorher_momente, vorher_clips, vorher_dateien))
        self.assertEqual(nachschnitt.nachschneiden(self.con, self.konfig, tage=0)["geprueft"], 0)
        del einzel, verworfen, alt

    def test_aktion_schon_im_clip_nur_merkmale(self):
        # Claude hat den Clip schon früh genug begonnen (1,5 s < 2,0 s) – die Quelle muss dafür nicht im Puffer sein
        self.replay(SITZUNG)
        self.aufnahme()
        cid = self.clip(start_s=1.5, ende_s=30.2)
        vorher, clips_vorher, dateien_vorher = dict(self.zeile(cid)), self.alle("clips"), self.dateien()
        e = nachschnitt.nachschneiden(self.con, self.konfig, tage=14)
        self.assertEqual((e["nur_merkmale"], e["neu_geschnitten"], e["ohne_quelle"], e["fehler"]), (1, 0, 0, 0))
        z = dict(self.zeile(cid))
        for spalte in ("datei", "start_s", "ende_s", "start_utc", "stimmung", "sicherheit", "quelle", "text", "kills"):
            self.assertEqual(z[spalte], vorher[spalte], spalte)
        mk = json.loads(z["merkmale"])
        self.assertEqual((mk["kill_sekunden"], mk["aktion_sekunden"]), ([23.7, 23.7, 23.7], [8.5, 12.5, 23.5]))
        self.assertEqual((mk["spitzen_s"], mk["dauer_s"]), (MERKMALE["spitzen_s"], MERKMALE["dauer_s"]))  # nicht verschoben
        eintrag = mk[stimmung.NACHSCHNITT]
        self.assertEqual((eintrag["version"], eintrag["neu_geschnitten"], eintrag["alt_datei"], eintrag["quelle_start_s"]),
                         (nachschnitt.VERSION, False, vorher["datei"], 1.5))
        self.assertEqual((self.alle("clips"), self.dateien()), (clips_vorher, dateien_vorher))
        e = nachschnitt.nachschneiden(self.con, self.konfig, tage=14)
        self.assertEqual((e["schon_erledigt"], e["nur_merkmale"], e["beispiele"]), (1, 0, []))

    def test_kappung_auf_60_s(self):
        # Erstes Umhauen 65,5 s vor dem Kill: ab der Aktion wären es 75,5 s – vorne auf 60 s gekappt
        self.replay(SITZUNG, [(5.0, "ICH", "GEGNER-D", True), (70.0, "ICH", "GEGNER-E", True),
                              (70.5, "ICH", "GEGNER-D", False), (70.5, "ICH", "GEGNER-E", False)])
        self.aufnahme(dauer=80.0)
        (self.puffer / QUELLE).parent.mkdir(parents=True, exist_ok=True)
        (self.puffer / QUELLE).write_bytes(b"nur fuer lstat")  # die Probe schneidet nicht
        cid = self.clip(start_s=62.5, ende_s=75.5, kills=2)
        vorher = self.alle("momente")
        with self.assertLogs("pipeline", "WARNING") as logs:
            e = nachschnitt.nachschneiden(self.con, self.konfig, tage=14, probe=True)
        self.assertTrue(any("gekappt" in z for z in logs.output))
        self.assertEqual((e["neu_geschnitten"], e["gekappt_60s"], e["probe"]), (1, 1, True))
        b = e["beispiele"][0]
        self.assertEqual((b["moment"], b["start_s"], b["ende_s"], b["anlauf_s"], b["datei"]),
                         (f"clip:{cid}", 15.5, 75.5, 47.0, f"clip{cid:05d}_15500-75500.mp4"))
        self.assertIn("60 s", b["hinweis"])
        self.assertEqual(b["aktion_sekunden"], [-10.5, 54.5])  # erste Aktion liegt vor dem gekappten Beginn
        self.assertEqual(self.alle("momente"), vorher)

    def test_fremder_kill_im_anlauf(self):
        # Echtes Muster (Match 2026-09-13, Clip 5): ein Einzelkill eines früheren Clips liegt im Anlauf des Wipes.
        # Er bliebe sonst im Moment (doppelt gezeigt, sein Umhauen zöge den Regisseur an den Dateianfang).
        (self.puffer / QUELLE).parent.mkdir(parents=True, exist_ok=True)
        (self.puffer / QUELLE).write_bytes(b"nur fuer lstat")
        self.aufnahme()
        for fremd_s, start_s, kills in ((5.0, 5.5, [19.7] * 3), (9.6, 9.0, [0.6, 16.2, 16.2, 16.2])):
            with self.subTest(fremder_kill=fremd_s):
                sitzung = f"2026-09-20_2{int(fremd_s)}-00-00"
                self.replay(sitzung, [(fremd_s - 1.0, "ICH", "GEGNER-X", True), (fremd_s, "ICH", "GEGNER-X", False),
                                      *WIPE])
                cid = self.clip(sitzung=sitzung)
                e = nachschnitt.nachschneiden(self.con, self.konfig, tage=14, probe=True)
                b = next(b for b in e["beispiele"] if b["moment"] == f"clip:{cid}")
                self.assertEqual((b["ergebnis"], b["start_s"]), ("neu_geschnitten", start_s))
                self.assertIn("früheren Kill", b["hinweis"])
                self.assertEqual(len(b["aktion_sekunden"]), len(kills))
                # 1 s vor der ersten Aktion bleibt immer: liegt der fremde Kill noch näher, bleibt er drin
                self.assertEqual(b["aktion_sekunden"][-3:], [round(t - start_s, 1) for t in (10.0, 14.0, 25.0)])
                self.con.execute("DELETE FROM momente")

    def test_ohne_getrennten_betrieb_konfigfehler(self):
        self.konfig.daten["lager"]["wurzel"] = ""
        with self.assertRaises(KonfigFehler):
            nachschnitt.nachschneiden(self.con, self.konfig, tage=14)
        self.konfig.daten["lager"]["wurzel"] = str(self.lager)
        (self.puffer / ".clip-puffer").unlink()
        with self.assertRaisesRegex(KonfigFehler, "clip-puffer"):
            nachschnitt.nachschneiden(self.con, self.konfig, tage=14)


class Befehl(MitPuffer):
    """`pipeline momente nachschneiden` über cli.main – Vertrag: letzte Zeile JSON, Exit-Codes."""

    def test_ohne_getrennten_betrieb_exit_2(self):
        self.konfig.daten["lager"]["wurzel"] = ""
        with mock.patch.object(nachschnitt, "nachschneiden") as lauf:
            code, antwort, _ = cli_lauf(self, ["momente", "nachschneiden"])
        self.assertEqual((code, antwort["fehler"]), (2, "konfig"))
        self.assertIn("getrennter Betrieb", antwort["hinweis"])
        lauf.assert_not_called()

    def test_puffer_marke_fehlt_exit_2(self):
        (self.puffer / ".clip-puffer").unlink()
        code, antwort, _ = cli_lauf(self, ["momente", "nachschneiden", "--probe"])
        self.assertEqual((code, antwort["fehler"]), (2, "konfig"))

    def test_probe_ueber_cli(self):
        self.replay(SITZUNG)
        self.aufnahme()
        (self.puffer / QUELLE).parent.mkdir(parents=True, exist_ok=True)
        (self.puffer / QUELLE).write_bytes(b"nur fuer lstat")
        self.clip()
        vorher = (self.alle("momente"), self.dateien())
        with self.assertLogs("pipeline", "INFO") as logs:
            code, antwort, _ = cli_lauf(self, ["momente", "nachschneiden", "--probe", "--tage", "3"])
        self.assertEqual(code, 0)
        self.assertEqual((antwort["probe"], antwort["tage"], antwort["neu_geschnitten"], antwort["geprueft"]),
                         (True, 3, 1, 1))
        self.assertTrue(any("Nachschnitt (Probe)" in z for z in logs.output))
        self.assertEqual((self.alle("momente"), self.dateien()), vorher)
        # Standard für --tage: [puffer].rohdaten_tage
        self.konfig.daten.setdefault("puffer", {})["rohdaten_tage"] = 9
        self.assertEqual(cli_lauf(self, ["momente", "nachschneiden", "--probe"])[1]["tage"], 9)


@unittest.skipUnless(HAT_FFMPEG, "ffmpeg fehlt")
class Schnitt(MitPuffer):
    """Echte Schnitte mit FFmpeg (kleine Videos). Aufnahme: bis 20 s rot, danach blau."""

    @classmethod
    def setUpClass(cls):
        cls._vorrat = tempfile.TemporaryDirectory()
        vorrat = Path(cls._vorrat.name)
        cls.video = zweifarbig(vorrat / "aufnahme.mp4", 40.0, 20.0)
        cls.bot_clip = testvideo(vorrat / "bot.mp4", dauer=13.0)

    @classmethod
    def tearDownClass(cls):
        cls._vorrat.cleanup()

    def setUp(self):
        super().setUp()
        self.replay(SITZUNG)
        self.aufnahme(datei=self.video)
        self.cid = self.clip(bot_clip=self.bot_clip)
        self.bot_datei = Path(self.zeile(self.cid)["datei"])

    def test_neuer_schnitt_und_zweiter_lauf(self):
        vorher, clips_vorher, bot_vorher = dict(self.zeile(self.cid)), self.alle("clips"), fingerabdruck(self.bot_datei)
        e = nachschnitt.nachschneiden(self.con, self.konfig, tage=14)
        self.assertEqual((e["geprueft"], e["neu_geschnitten"], e["fehler"], e["gekappt_60s"]), (1, 1, 0, 0))
        ziel = self.ziel(self.cid)
        self.assertEqual(sorted(p.name for p in ziel.parent.iterdir()), [ziel.name])  # keine Zwischendateien
        dauer = probe(ziel).dauer_s
        self.assertAlmostEqual(dauer, 28.2, delta=0.2)
        # Geschnitten ab 2,0 s der Aufnahme: bei 16,5 s (= 18,5 s) noch rot, bei 19,5 s (= 21,5 s) schon blau
        self.assertTrue(ist_rot(farbe_bei(ziel, 16.5)))
        self.assertFalse(ist_rot(farbe_bei(ziel, 19.5)))

        z = dict(self.zeile(self.cid))
        self.assertEqual((z["datei"], z["start_s"], z["start_utc"]), (str(ziel), 0.0, iso(self.t0 + timedelta(seconds=2))))
        self.assertAlmostEqual(z["ende_s"], dauer, places=3)
        for spalte in ("schluessel", "clip_id", "match_id", "kills", "stimmung", "sicherheit", "quelle", "text", "erstellt"):
            self.assertEqual(z[spalte], vorher[spalte], spalte)
        mk = json.loads(z["merkmale"])
        self.assertEqual((mk["kill_sekunden"], mk["aktion_sekunden"]), ([23.2, 23.2, 23.2], [8.0, 12.0, 23.0]))
        self.assertEqual((mk["spitzen_s"], mk["jubel_laut_s"], mk["dauer_s"]), ([21.1], [24.9, 26.3], 28.2))
        self.assertEqual((mk["kills"], mk["spitzen"], mk["punkte"], mk["tod_sekunde"]),
                         (3, 1, MERKMALE["punkte"], None))
        eintrag = mk[stimmung.NACHSCHNITT]
        self.assertEqual({k: eintrag[k] for k in ("version", "quelle_pfad", "quelle_start_s", "quelle_ende_s",
                                                  "alt_datei", "alt_start_utc", "neu_geschnitten", "anlauf_s")},
                         {"version": nachschnitt.VERSION, "quelle_pfad": QUELLE, "quelle_start_s": 2.0,
                          "quelle_ende_s": 30.2, "alt_datei": vorher["datei"], "alt_start_utc": vorher["start_utc"],
                          "neu_geschnitten": True, "anlauf_s": 15.2})
        # Bot-Clip und Tabelle clips unangetastet
        self.assertEqual((self.alle("clips"), fingerabdruck(self.bot_datei)), (clips_vorher, bot_vorher))

        # Zweiter Lauf: nichts zu tun, keine neue Datei
        nachher = (self.alle("momente"), self.dateien())
        e = nachschnitt.nachschneiden(self.con, self.konfig, tage=14)
        self.assertEqual((e["schon_erledigt"], e["neu_geschnitten"], e["fehler"]), (1, 0, 0))
        self.assertEqual((self.alle("momente"), self.dateien()), nachher)

    def test_probe_schreibt_nichts(self):
        vorher = (self.alle("momente"), self.alle("clips"), self.dateien())
        e = nachschnitt.nachschneiden(self.con, self.konfig, tage=14, probe=True)
        self.assertEqual((e["probe"], e["neu_geschnitten"]), (True, 1))
        b = e["beispiele"][0]
        self.assertEqual((b["start_s"], b["ende_s"], b["anlauf_s"], b["aktion_sekunden"]), (2.0, 30.2, 15.2, [8.0, 12.0, 23.0]))
        self.assertNotIn("ziel_da", b)
        self.assertEqual((self.alle("momente"), self.alle("clips"), self.dateien()), vorher)
        self.assertFalse((self.konfig.ordner("sessions") / SITZUNG / "momente").exists())

    def test_fehlende_quelle(self):
        # Die Aufnahme liegt nur noch im Lager: überspringen, nicht dort nachsehen
        weg = "eingang/steelseries/Fortnite__2026-09-20__19-00-00.mp4"
        self.aufnahme(weg)
        (self.lager / weg).parent.mkdir(parents=True)
        shutil.copy2(self.video, self.lager / weg)
        self.con.execute("UPDATE clips SET quelle_pfad = ? WHERE id = ?", (weg, self.cid))
        vorher = (self.alle("momente"), self.dateien())
        with beobachte(self.lager) as zugriffe:
            e = nachschnitt.nachschneiden(self.con, self.konfig, tage=14)
        self.assertEqual((e["ohne_quelle"], e["neu_geschnitten"], e["fehler"]), (1, 0, 0))
        self.assertEqual(zugriffe, [])
        self.assertEqual((self.alle("momente"), self.dateien()), vorher)

    def test_quelle_als_link_nach_aussen(self):
        # Datei-Link und Ordner-Link ins Lager: beide gelten als „nicht im Puffer“, das Ziel wird nie geöffnet
        (self.lager / "eingang" / "steelseries").mkdir(parents=True)
        draussen = self.lager / "eingang" / "steelseries" / "draussen.mp4"
        shutil.copy2(self.video, draussen)
        datei_link = "eingang/steelseries/Fortnite__2026-09-20__19-10-00.mp4"
        (self.puffer / datei_link).symlink_to(draussen)
        (self.puffer / "eingang" / "verlinkt").symlink_to(self.lager / "eingang" / "steelseries")
        ordner_link = "eingang/verlinkt/draussen.mp4"
        ids = []
        for rel in (datei_link, ordner_link):
            self.aufnahme(rel)
            ids.append(self.clip(rel=rel))
        self.con.execute("DELETE FROM momente WHERE clip_id = ?", (self.cid,))  # nur die beiden Link-Fälle
        vorher = (self.alle("momente"), self.dateien())
        with beobachte(self.lager) as zugriffe:
            e = nachschnitt.nachschneiden(self.con, self.konfig, tage=14)
        self.assertEqual((e["geprueft"], e["ohne_quelle"], e["neu_geschnitten"], e["fehler"]), (2, 2, 0, 0))
        self.assertEqual(zugriffe, [])
        self.assertEqual((self.alle("momente"), self.dateien()), vorher)

    def test_vorhandenes_ziel_mit_passender_dauer_wird_uebernommen(self):
        ziel = self.ziel(self.cid)
        testvideo(ziel, dauer=28.2)
        abdruck = fingerabdruck(ziel)
        e = nachschnitt.nachschneiden(self.con, self.konfig, tage=14)
        self.assertEqual((e["neu_geschnitten"], e["fehler"], e["beispiele"][0]["uebernommen"]), (1, 0, True))
        self.assertEqual(fingerabdruck(ziel), abdruck)  # nicht überschrieben
        self.assertEqual(self.zeile(self.cid)["datei"], str(ziel))
        self.assertEqual(sorted(p.name for p in ziel.parent.iterdir()), [ziel.name])

    def test_vorhandenes_ziel_mit_falscher_dauer_bleibt_fehler(self):
        ziel = self.ziel(self.cid)
        ziel.parent.mkdir(parents=True)
        shutil.copy2(self.bot_clip, ziel)  # 13 s statt 28,2 s
        abdruck, vorher = fingerabdruck(ziel), self.alle("momente")
        with self.assertLogs("pipeline", "ERROR") as logs:
            code, antwort, _ = cli_lauf(self, ["momente", "nachschneiden"])
        self.assertEqual((code, antwort["fehler"], antwort["neu_geschnitten"]), (1, 1, 0))
        self.assertIn("nicht überschrieben", antwort["beispiele"][0]["fehler"])
        self.assertTrue(any("nicht überschrieben" in z for z in logs.output))
        self.assertEqual((fingerabdruck(ziel), self.alle("momente")), (abdruck, vorher))

    def test_stimmung_neu_behaelt_den_nachschnitt(self):
        nachschnitt.nachschneiden(self.con, self.konfig, tage=14)
        vorher = dict(self.zeile(self.cid))
        stimmung.analysiere(self.con, self.konfig, neu=True, claude=False, whisper=False)
        z = dict(self.zeile(self.cid))
        self.assertEqual((z["datei"], z["start_s"], z["ende_s"], z["start_utc"]),
                         (vorher["datei"], 0.0, vorher["ende_s"], vorher["start_utc"]))
        mk, mk_vorher = json.loads(z["merkmale"]), json.loads(vorher["merkmale"])
        self.assertEqual(mk[stimmung.NACHSCHNITT], mk_vorher[stimmung.NACHSCHNITT])
        self.assertEqual((mk["kill_sekunden"], mk["aktion_sekunden"], mk["dauer_s"]),
                         ([23.2, 23.2, 23.2], [8.0, 12.0, 23.0], 28.2))
        # und danach ist weiter nichts zu tun
        self.assertEqual(nachschnitt.nachschneiden(self.con, self.konfig, tage=14)["schon_erledigt"], 1)

    def test_compose_short_mit_teilen(self):
        nachschnitt.nachschneiden(self.con, self.konfig, tage=14)
        e = regie.erstelle(self.con, self.konfig, "short")
        liste = json.loads(Path(e["datei"]).read_text(encoding="utf-8"))
        self.assertEqual(regie.pruefe_liste(liste), [])
        teile = [s for s in liste["segmente"] if s["moment"] == f"clip:{self.cid}"]
        self.assertEqual([s["teil"] for s in teile], [1, 2])  # erstes Umhauen … Lücke … Wipe
        self.assertEqual({s["datei"] for s in teile}, {str(self.ziel(self.cid))})
        for t in (8.0, 12.0, 23.0, 23.2):  # alle Aktionen und der Kill sind zu sehen
            self.assertTrue(any(s["quelle_start_s"] <= t <= s["quelle_ende_s"] for s in teile), t)


if __name__ == "__main__":
    unittest.main()
