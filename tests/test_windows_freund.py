"""Mehrbenutzer, Stufe 2, Schritt 4: das PC-Programm der Freunde (windows/Freund-Hochladen.ps1, Freund-Einrichten.ps1)
unter PowerShell 7 gegen die sftp-Attrappe (tests/sftp_attrappe.py, Upload-Profil wie der Briefkasten: put, reput,
rename -l – nicht lesen, nicht löschen, kein Ziel überschreiben). Der Programmpfad steht in der freund.psd1 (Sftp), die
Uhr und „Fortnite läuft“ kommen über Testhaken: Get-Date und Get-Process werden vor dem Aufruf als globale Funktionen
überschrieben (wie in test_windows_helfer). Echte Windows PowerShell 5.1 und Win32-sftp.exe laufen hier nicht – das
prüft Florian einmal vor Ort mit -Probe; BOM und 5.1-Syntax prüft Stolperdraht51 (test_windows_helfer) für alle
windows/*.ps1 und *.psd1 von selbst.

Geprüft wird:
  - Normal: zwei Aufnahmen und ein Replay – im Fach landen die Aufnahmen vor dem Replay, je erst .teil, dann
    umbenannt, dann der Lieferschein (Größe, SHA-256, Zeit vom PC); nach 45 min Testuhr genau eine Abend-Datei mit
    ende_utc = Ende des letzten Matches; lokal wird nichts gelöscht oder geändert.
  - Wichtigster Fehlerfall: Abbruch mitten im Upload – der nächste Lauf setzt per reput fort, die Datei kommt heil an,
    nichts doppelt; dazu: noch offene oder zu junge Dateien werden nicht geladen (und ihr Replay wartet); ein
    Briefkasten mit OpenSSH vor 8.6 (bietet posix-rename an und verweigert es) nimmt trotzdem alles an, weil das
    Programm mit rename -l umbenennt (M135).
  - Übersprungen (bleibt auf dem PC, steht im Status): Aufnahme ohne Replay, Name mit [ ], größer als das halbe Fach.
  - Drossel: läuft Fortnite, lädt sftp mit -l 2000; mit 0 gar nicht.
  - Autorisierung: Das Programm benutzt nur ls, df, put, reput, rename; die Attrappe verweigert dem PC-Schlüssel
    Lesen, Löschen, Überschreiben und fremde Fächer, dem Mini-Schlüssel das Schreiben.
  - Einrichten: kopiert, merkt Ordner und Stichtag, prüft die Verbindung; ein unvollständiges ZIP bricht ab.
  - Ende zu Ende ohne Netz (mit ffmpeg): PC → Fach → Abholer → scan ergibt ein Match mit Clip.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from clip_pipeline.zeit import UTC, iso, utc_zu_lokal

from tests import sftp_attrappe

PROJEKT = Path(__file__).resolve().parents[1]
PWSH = shutil.which("pwsh") or ("/opt/pwsh/pwsh" if Path("/opt/pwsh/pwsh").is_file() else None)
ZONE = "Europe/Berlin"
VIDEO_A = "Fortnite 2026.10.08 - 20.10.00.02.DVR.mp4"
VIDEO_B = "Fortnite__2026-10-08__20-12-30.mp4"
REPLAY = "UnsavedReplay-2026.10.08-20.00.00.replay"
MATCH = "2026-10-08_20-00-00"
FORTNITE = ("function global:Get-Process { param([string[]]$Name, $ErrorAction)\n"
            "  if ($Name -contains 'FortniteClient-Win64-Shipping') { [pscustomobject]@{ Id = 4711 } } }\n")


def uhr_plus(minuten: int) -> str:
    """Testhaken: „jetzt“ für das Skript minuten später (Get-Date ohne Parameter)."""
    return f"function global:Get-Date {{ (Microsoft.PowerShell.Utility\\Get-Date).AddMinutes({minuten}) }}\n"


def sha(pfad: Path) -> str:
    return hashlib.sha256(pfad.read_bytes()).hexdigest()


def abdruck(wurzel: Path) -> dict[str, tuple[str, int]]:
    """SHA-256 und mtime jeder Datei unter wurzel – zum Nachweis, dass auf dem PC nichts gelöscht oder geändert wird."""
    return {p.relative_to(wurzel).as_posix(): (sha(p), p.stat().st_mtime_ns) for p in sorted(wurzel.rglob("*"))
            if p.is_file()}


class MitPc(unittest.TestCase):
    """Ein PC (Programm in install/, Aufnahmen in pc/Videos, Replays in appdata/FortniteGame/Saved/Demos) und ein
    Briefkasten mit dem Fach von max (vserver/bk-max/fach), in das nur der PC-Schlüssel aus install/pc hochladen darf."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(os.path.realpath(self._tmp.name))
        self.install = self.tmp / "install"
        self.install.mkdir()
        for name in ("Freund-Hochladen.ps1", "Freund-Einrichten.ps1", "Freund-Einrichten.cmd"):
            shutil.copy(PROJEKT / "windows" / name, self.install / name)
        (self.install / "pc").write_text("pc-schluessel-attrappe (kein echter Schlüssel)\n", encoding="utf-8")
        (self.install / "known_hosts").write_text("[vserver.example.org]:2222 ssh-ed25519 AAAA-attrappe\n")
        self.sftp = sftp_attrappe.installiere(self.tmp)
        self.vserver = self.tmp / "vserver"
        self.fach = self.fach_anlegen("max", self.install / "pc")
        self.videos = self.tmp / "pc" / "Videos"
        (self.videos / "NVIDIA" / "Fortnite").mkdir(parents=True)
        self.appdata = self.tmp / "appdata"
        self.demos = self.appdata / "FortniteGame" / "Saved" / "Demos"
        self.demos.mkdir(parents=True)
        self.konfig()
        self.protokoll = self.tmp / "protokoll.jsonl"
        self.jetzt = time.time()

    def fach_anlegen(self, name: str, pc_schluessel: Path) -> Path:
        chroot = self.vserver / f"bk-{name}"
        for ordner in ("videos", "replays", "sitzungen", "status"):
            (chroot / "fach" / ordner).mkdir(parents=True)
        (chroot / "fach" / ".clip-briefkasten").write_text(f"bk-{name}\n")
        (chroot / ".schluessel_pc").write_text(str(pc_schluessel))   # wie hochladen/bk-<name> auf dem vServer
        return chroot / "fach"

    def konfig(self, drossel: int = 2000, **mehr: str) -> None:
        werte = {"Adresse": "'vserver.example.org'", "Port": "2222", "Benutzer": "'bk-max'",
                 "DrosselBeimSpielenKbit": str(drossel), "Sftp": f"'{self.sftp}'", "Ordner": f"@('{self.videos}')",
                 **mehr}
        (self.install / "freund.psd1").write_text("@{\n" + "".join(f"    {k} = {v}\n" for k, v in werte.items()) + "}\n",
                                                  encoding="utf-8")

    def datei(self, pfad: Path, inhalt: bytes, alter_s: float) -> Path:
        pfad.parent.mkdir(parents=True, exist_ok=True)
        pfad.write_bytes(inhalt)
        os.utime(pfad, (self.jetzt - alter_s, self.jetzt - alter_s))
        return pfad

    def lauf(self, vorher: str = "", *argumente: str, skript: str = "Freund-Hochladen.ps1",
             **umgebung: str) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items() if not k.lower().endswith("_proxy")}
        env.update(LOCALAPPDATA=str(self.appdata), SFTP_ATTRAPPE_WURZEL=str(self.vserver),
                   SFTP_ATTRAPPE_PROTOKOLL=str(self.protokoll), TZ=ZONE, **umgebung)
        befehl = [PWSH, "-NoProfile", "-NonInteractive", "-Command",
                  f"{vorher}& '{self.install / skript}' {' '.join(argumente)}; exit $LASTEXITCODE"]
        return subprocess.run(befehl, capture_output=True, text=True, env=env, timeout=180)

    def aufrufe(self) -> list[dict]:
        if not self.protokoll.exists():
            return []
        return [json.loads(z) for z in self.protokoll.read_text(encoding="utf-8").splitlines()]

    def befehle(self) -> list[str]:
        return [b for a in self.aufrufe() for b in a["befehle"]]

    def im_fach(self) -> list[str]:
        return sorted(p.relative_to(self.fach).as_posix() for p in self.fach.rglob("*")
                      if p.is_file() and p.name != ".clip-briefkasten")

    def lieferschein(self, ordner: str, name: str) -> dict:
        return json.loads((self.fach / ordner / f"{name}.lieferschein").read_text(encoding="utf-8-sig"))

    def status(self) -> dict:
        return json.loads((self.fach / "status" / "pc-status.json").read_text(encoding="utf-8"))

    def stichtag(self, tage_zurueck: float) -> None:
        """Stichtag wie nach der Einrichtung vor so vielen Tagen (sonst: jetzt minus 24 h)."""
        stand = self.appdata / "ClipUpload" / "stand"
        stand.mkdir(parents=True, exist_ok=True)
        zeit = datetime.fromtimestamp(self.jetzt - tage_zurueck * 86400, UTC)
        (stand / "stichtag.txt").write_text(iso(zeit), encoding="utf-8")


@unittest.skipIf(PWSH is None, "pwsh fehlt")
class NormalerWeg(MitPc):
    def test_videos_vor_dem_replay_mit_lieferschein_und_dann_der_abend(self):
        a = self.datei(self.videos / "NVIDIA" / "Fortnite" / VIDEO_A, os.urandom(300_000), 1300)
        b = self.datei(self.videos / VIDEO_B, os.urandom(120_000), 1200)
        r = self.datei(self.demos / REPLAY, b"replay" * 5000, 1000)   # Match-Ende vor 1000 s
        pc_vorher = {**abdruck(self.videos), **abdruck(self.demos)}

        lauf = self.lauf()
        self.assertEqual(lauf.returncode, 0, lauf.stdout + lauf.stderr)
        puts = [b.split('"')[3] for b in self.befehle() if b.startswith("put ") and "status/" not in b]
        self.assertEqual(puts, [f"videos/{VIDEO_A}.teil", f"videos/{VIDEO_A}.lieferschein.teil",
                                f"videos/{VIDEO_B}.teil", f"videos/{VIDEO_B}.lieferschein.teil",
                                f"replays/{REPLAY}.teil", f"replays/{REPLAY}.lieferschein.teil"])   # Replay zuletzt
        self.assertEqual(self.im_fach(), sorted([f"videos/{VIDEO_A}", f"videos/{VIDEO_A}.lieferschein",
                                                 f"videos/{VIDEO_B}", f"videos/{VIDEO_B}.lieferschein",
                                                 f"replays/{REPLAY}", f"replays/{REPLAY}.lieferschein",
                                                 "status/pc-status.json"]))
        for ordner, quelle in (("videos", a), ("videos", b), ("replays", r)):
            with self.subTest(quelle.name):
                self.assertEqual((self.fach / ordner / quelle.name).read_bytes(), quelle.read_bytes())
                mtime_ms = quelle.stat().st_mtime_ns // 10**6
                versatz = utc_zu_lokal(datetime.fromtimestamp(mtime_ms / 1000, UTC), ZONE).utcoffset()
                self.assertEqual(self.lieferschein(ordner, quelle.name),
                                 {"name": quelle.name, "groesse": quelle.stat().st_size, "sha256": sha(quelle),
                                  "mtime_ms": mtime_ms, "utc_offset_min": int(versatz.total_seconds() // 60)})
        self.assertEqual({**abdruck(self.videos), **abdruck(self.demos)}, pc_vorher)   # lokal nichts gelöscht/geändert
        s = self.status()
        self.assertEqual((s["hochgeladen"], s["fehler"], s["offen"], s["uebersprungen"], s["fortnite"]),
                         (3, 0, [], [], False))
        self.assertEqual((s["zeitzone"], s["utc_offset_min"]), (ZONE, 120))
        self.assertEqual(list((self.fach / "sitzungen").iterdir()), [])   # erst 45 min nach dem Match

        lauf = self.lauf(uhr_plus(50))                                    # 50 min später: der Abend ist vorbei
        self.assertEqual(lauf.returncode, 0, lauf.stdout + lauf.stderr)
        name = f"session_{MATCH}.json"
        self.assertEqual(sorted(p.name for p in (self.fach / "sitzungen").iterdir()), [name, f"{name}.lieferschein"])
        abend = json.loads((self.fach / "sitzungen" / name).read_text(encoding="utf-8"))
        ende = datetime.fromtimestamp(r.stat().st_mtime_ns // 10**6 / 1000, UTC)
        self.assertEqual(abend, {"session": f"session_{MATCH}", "matches": [MATCH], "ende_utc": iso(ende)})
        self.assertEqual(self.lieferschein("sitzungen", name)["sha256"], sha(self.fach / "sitzungen" / name))
        anzahl = len(self.befehle())
        self.assertEqual(self.lauf(uhr_plus(70)).returncode, 0)           # danach: keine zweite Abend-Datei
        self.assertEqual(len(list((self.fach / "sitzungen").iterdir())), 2)
        self.assertFalse([b for b in self.befehle()[anzahl:] if b.startswith(("put", "reput", "rename"))
                          and "status/" not in b])
        self.assertEqual(list(self.fach.rglob("*.teil")), [])


@unittest.skipIf(PWSH is None, "pwsh fehlt")
class Fehlerfall(MitPc):
    def test_abbruch_mitten_im_upload_setzt_beim_naechsten_lauf_fort(self):
        a = self.datei(self.videos / VIDEO_A, os.urandom(300_000), 1300)
        r = self.datei(self.demos / REPLAY, b"replay" * 5000, 1000)

        lauf = self.lauf(SFTP_ATTRAPPE_ABBRUCH="100000")                 # Verbindung reißt nach 100 000 Byte
        self.assertEqual(lauf.returncode, 3, lauf.stdout + lauf.stderr)
        self.assertEqual(self.im_fach(), [f"videos/{VIDEO_A}.teil"])     # kein Endname, kein Lieferschein, kein Replay
        self.assertEqual((self.fach / "videos" / f"{VIDEO_A}.teil").stat().st_size, 100_000)

        lauf = self.lauf()                                                # Lauf 2 setzt fort (reput, nur der Rest)
        self.assertEqual(lauf.returncode, 0, lauf.stdout + lauf.stderr)
        daten = [b for b in self.befehle() if b.startswith(("put", "reput")) and VIDEO_A in b and ".lieferschein" not in b]
        self.assertEqual([b.split()[0] for b in daten], ["put", "reput"])
        self.assertEqual((self.fach / "videos" / VIDEO_A).read_bytes(), a.read_bytes())
        self.assertEqual(self.lieferschein("videos", VIDEO_A)["sha256"], sha(a))
        self.assertEqual(self.im_fach(), sorted([f"videos/{VIDEO_A}", f"videos/{VIDEO_A}.lieferschein", f"replays/{REPLAY}",
                                                 f"replays/{REPLAY}.lieferschein", "status/pc-status.json"]))
        self.assertEqual((self.fach / "replays" / REPLAY).read_bytes(), r.read_bytes())
        anzahl = len(self.befehle())
        self.assertEqual(self.lauf().returncode, 0)                       # nichts doppelt (nur der Status)
        self.assertFalse([b for b in self.befehle()[anzahl:] if b.startswith(("put", "reput")) and "status/" not in b])

    def test_offene_und_zu_junge_dateien_bleiben_und_ihr_replay_wartet(self):
        offen = self.datei(self.videos / VIDEO_A, os.urandom(50_000), 600)
        self.datei(self.videos / VIDEO_B, os.urandom(50_000), 10)        # erst 10 s alt (Ruhezeit 60 s)
        self.datei(self.demos / REPLAY, b"replay" * 100, 400)
        with offen.open("rb") as griff:
            # Unter Linux scheitert damit [IO.File]::Open(…, 'Read', 'Read') in Datei-Frei – wie unter Windows, solange
            # der Rekorder die Datei noch schreibt
            fcntl.flock(griff, fcntl.LOCK_EX)
            lauf = self.lauf()
        self.assertEqual(lauf.returncode, 0, lauf.stdout + lauf.stderr)
        self.assertEqual(self.im_fach(), ["status/pc-status.json"])      # nichts geladen – auch das Replay nicht
        self.assertEqual({o["quelle"]: o["anzahl"] for o in self.status()["offen"]}, {"videos": 2, "replays": 1})

    def test_aenderung_beim_hochladen_kommt_trotzdem_mit_lieferschein_an(self):
        # Befund B2: Ändert sich eine Aufnahme, während sie hochgeht, liegt oben eine frühere Fassung ohne Lieferschein.
        # Vorher merkte der nächste Lauf sie als erledigt – sie kam nie beim Mini an. Jetzt ersetzt er sie direkt durch
        # die aktuelle Fassung (der Mini fasst ohne Lieferschein nichts an) und schickt den Lieferschein.
        video = self.datei(self.videos / VIDEO_A, os.urandom(200_000), 600)
        self.datei(self.demos / REPLAY, os.urandom(50_000), 3000)
        huelle = self.tmp / "sftp-huelle"   # nach dem ersten put dieser Aufnahme schreibt „der Rekorder“ noch etwas dazu
        huelle.write_text(f"""#!/bin/bash
"{self.sftp}" "$@"; rc=$?
b=""; for a in "$@"; do [ "$prev" = -b ] && b="$a"; prev="$a"; done
if [ -n "$b" ] && grep -q '^put "{VIDEO_A}"' "$b" && [ ! -e "{self.tmp}/schon" ]; then
  printf 'X' >> "{video}"; touch "{self.tmp}/schon"
fi
exit $rc
""")
        huelle.chmod(0o755)
        self.konfig(Sftp=f"'{huelle}'")
        self.lauf()
        self.assertNotIn(f"videos/{VIDEO_A}.lieferschein", self.im_fach())    # geändert: noch kein Lieferschein
        lauf = self.lauf(uhr_plus(10))
        self.assertEqual(lauf.returncode, 0, lauf.stdout + lauf.stderr)
        self.assertIn(f'put "{VIDEO_A}" "videos/{VIDEO_A}"', self.befehle())   # direkt auf den Namen
        self.assertEqual((self.fach / "videos" / VIDEO_A).read_bytes(), video.read_bytes())
        self.assertEqual(self.lieferschein("videos", VIDEO_A)["sha256"], sha(video))

    def test_abend_datei_ohne_lieferschein_wird_neu_geschrieben(self):
        # Befund B4: Riss die Verbindung zwischen Abend-Datei und Lieferschein ab, lag oben eine Abend-Datei ohne
        # Lieferschein – womöglich mit weniger Matches. Vorher kam nur ein Lieferschein mit der neuen Prüfsumme dazu (der
        # Mini gab die Sitzung auf, kein Abend-Video). Jetzt schreibt der PC die Datei neu, der Lieferschein passt.
        self.datei(self.videos / VIDEO_A, os.urandom(100_000), 1300)
        self.datei(self.demos / REPLAY, b"replay" * 5000, 1000)
        self.assertEqual(self.lauf().returncode, 0)
        name = f"session_{MATCH}.json"
        alt = self.fach / "sitzungen" / name
        alt.write_text('{"session": "alt", "matches": [], "ende_utc": "2026-10-08T18:00:00Z"}', encoding="utf-8")
        lauf = self.lauf(uhr_plus(50))
        self.assertEqual(lauf.returncode, 0, lauf.stdout + lauf.stderr)
        self.assertEqual(json.loads(alt.read_text(encoding="utf-8"))["matches"], [MATCH])
        self.assertEqual(self.lieferschein("sitzungen", name)["sha256"], sha(alt))

    def test_briefkasten_vor_openssh_8_6_nimmt_trotzdem_alles_an(self):
        # Befund B1 (M135): OpenSSH vor 8.6 bietet posix-rename trotz Erlaubnisliste an und verweigert es dann – mit
        # rename ohne -l bliebe jede Datei als .teil liegen (Exit 1 in jedem Lauf), obwohl -Probe grün ist
        self.datei(self.videos / VIDEO_A, os.urandom(50_000), 1300)
        self.datei(self.demos / REPLAY, b"replay" * 100, 1000)
        for vorher in ("", uhr_plus(50)):                                 # 50 min später: die Abend-Datei
            lauf = self.lauf(vorher, SFTP_ATTRAPPE_ALT="1")
            self.assertEqual(lauf.returncode, 0, lauf.stdout + lauf.stderr)
        name = f"session_{MATCH}.json"
        self.assertEqual(self.im_fach(), sorted([f"videos/{VIDEO_A}", f"videos/{VIDEO_A}.lieferschein", f"replays/{REPLAY}",
                                                 f"replays/{REPLAY}.lieferschein", f"sitzungen/{name}",
                                                 f"sitzungen/{name}.lieferschein", "status/pc-status.json"]))
        umbenannt = [b for b in self.befehle() if b.split()[0] == "rename"]
        self.assertEqual(len(umbenannt), 6)                               # je Datei und Lieferschein, alle mit -l
        self.assertEqual([b for b in umbenannt if not b.startswith("rename -l ")], [])

        # Gegenprobe: Ohne -l verweigert dieser Briefkasten das Umbenennen – so sah der Fehler aus
        (self.tmp / "x").write_text("x")
        stapel = self.tmp / "stapel"
        stapel.write_text('put x "videos/x.teil"\nrename "videos/x.teil" "videos/x"\n')
        r = subprocess.run([str(self.sftp), "-F", "none", "-b", str(stapel), "-P", "2222", "-o", "IdentitiesOnly=yes",
                            "-o", f'IdentityFile="{self.install / "pc"}"', "-o", "StrictHostKeyChecking=yes",
                            "bk-max@vserver.example.org"], capture_output=True, text=True, cwd=self.tmp,
                           env={**os.environ, "SFTP_ATTRAPPE_WURZEL": str(self.vserver), "SFTP_ATTRAPPE_ALT": "1"})
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn('remote rename "/fach/videos/x.teil" to "/fach/videos/x": Permission denied', r.stderr)
        self.assertTrue((self.fach / "videos" / "x.teil").is_file())


@unittest.skipIf(PWSH is None, "pwsh fehlt")
class Uebersprungen(MitPc):
    def test_ohne_replay_falscher_name_und_zu_gross_bleiben_auf_dem_pc(self):
        self.stichtag(10)
        alt = self.datei(self.videos / "Fortnite 2026.10.03 - 20.00.00.01.DVR.mp4", b"x" * 1000, 5 * 86400)
        klammer = self.datei(self.videos / "Fortnite [1] 2026.10.08 - 20.11.00.02.DVR.mp4", b"y" * 1000, 1300)
        gross = self.datei(self.videos / "Fortnite 2026.10.08 - 20.12.00.03.DVR.mp4", b"z" * 2_200_000, 1250)
        gut = self.datei(self.videos / VIDEO_A, b"a" * 1000, 1200)
        self.datei(self.demos / REPLAY, b"replay", 1000)
        pc_vorher = abdruck(self.videos)

        lauf = self.lauf(SFTP_ATTRAPPE_DF="4000 0 4000")                  # Fach 4 000 KiB: 2,2 MB sind mehr als die Hälfte
        self.assertEqual(lauf.returncode, 0, lauf.stdout + lauf.stderr)
        self.assertEqual([p for p in self.im_fach() if p.startswith("videos/")],
                         [f"videos/{gut.name}", f"videos/{gut.name}.lieferschein"])
        self.assertTrue((self.fach / "replays" / REPLAY).is_file())
        self.assertEqual({u["grund"]: (u["anzahl"], u["beispiel"]) for u in self.status()["uebersprungen"]},
                         {"ohne_replay": (1, alt.name), "name": (1, klammer.name), "zu_gross": (1, gross.name)})
        self.assertEqual(abdruck(self.videos), pc_vorher)


@unittest.skipIf(PWSH is None, "pwsh fehlt")
class Drossel(MitPc):
    def test_beim_spielen_langsam_mit_null_gar_nicht(self):
        self.datei(self.videos / VIDEO_A, b"a" * 5000, 1200)
        self.datei(self.demos / REPLAY, b"replay", 1000)
        self.konfig(drossel=0)
        lauf = self.lauf(FORTNITE)
        self.assertEqual(lauf.returncode, 0, lauf.stdout + lauf.stderr)
        self.assertFalse([b for b in self.befehle() if b.startswith(("put", "reput"))
                          and "status/" not in b])                         # Pause beim Spielen
        self.assertIs(self.status()["fortnite"], True)

        self.konfig(drossel=2000)
        lauf = self.lauf(FORTNITE)
        self.assertEqual(lauf.returncode, 0, lauf.stdout + lauf.stderr)
        hoch = [a for a in self.aufrufe() if any(b.startswith("put") and ".teil" in b for b in a["befehle"])
                and not any(".lieferschein" in b for b in a["befehle"])]
        self.assertEqual(len(hoch), 2)                                     # Aufnahme und Replay …
        for aufruf in hoch:
            argv = aufruf["argv"]
            self.assertEqual(argv[argv.index("-l") + 1], "2000")           # … mit höchstens 2 Mbit/s
        self.assertTrue((self.fach / "videos" / f"{VIDEO_A}.lieferschein").is_file())


@unittest.skipIf(PWSH is None, "pwsh fehlt")
class Autorisierung(MitPc):
    def test_programm_nur_hochladen_und_die_attrappe_verweigert_den_rest(self):
        self.datei(self.videos / VIDEO_A, b"a" * 5000, 1200)
        self.datei(self.demos / REPLAY, b"replay", 1000)
        self.assertEqual(self.lauf().returncode, 0)
        verben = {b.lstrip("-").split()[0] for b in self.befehle()}
        self.assertLessEqual(verben, {"ls", "df", "put", "reput", "rename"})
        argv = self.aufrufe()[0]["argv"]
        for option in ("BatchMode=yes", "IdentitiesOnly=yes", "StrictHostKeyChecking=yes", "CheckHostIP=no",
                       "AddressFamily=inet", f'IdentityFile="{self.install / "pc"}"',
                       f'UserKnownHostsFile="{self.install / "known_hosts"}"'):
            self.assertIn(option, argv)
        self.assertEqual((argv[argv.index("-F") + 1], argv[-1]), ("none", "bk-max@vserver.example.org"))

        # Das Profil des PC-Schlüssels (wie hochladen/bk-max): nicht lesen, nicht löschen, kein Ziel überschreiben, nicht
        # in die Wurzel; derselbe Schlüssel kommt nicht in das Fach von eva; der Mini-Schlüssel darf nur lesen
        self.fach_anlegen("eva", self.tmp / "eva-pc")
        mini = self.tmp / "mini"
        mini.write_text("mini")
        (self.vserver / "bk-max" / ".schluessel").write_text(str(mini))
        (self.tmp / "x").write_text("x")

        def sftp(benutzer: str, schluessel: Path, *befehle: str) -> subprocess.CompletedProcess:
            stapel = self.tmp / "stapel"
            stapel.write_text("\n".join(befehle) + "\n")
            return subprocess.run([str(self.sftp), "-F", "none", "-b", str(stapel), "-P", "2222", "-o", "BatchMode=yes",
                                   "-o", "IdentitiesOnly=yes", "-o", f'IdentityFile="{schluessel}"', "-o",
                                   "StrictHostKeyChecking=yes", f"{benutzer}@vserver.example.org"],
                                  capture_output=True, text=True, cwd=self.tmp,
                                  env={**os.environ, "SFTP_ATTRAPPE_WURZEL": str(self.vserver)})

        pc = self.install / "pc"
        faelle = [(("bk-max", pc, f'get "videos/{VIDEO_A}" zurueck'), "read remote"),
                  (("bk-max", pc, f'rm "videos/{VIDEO_A}"'), "remote delete"),
                  (("bk-max", pc, f'rename "replays/{REPLAY}" "videos/{VIDEO_A}"'), "Failure"),
                  (("bk-max", pc, 'put x .clip-briefkasten'), "Permission denied"),
                  (("bk-max", pc, 'mkdir videos/neu'), "Permission denied"),
                  (("bk-eva", pc, 'put x videos/x.teil'), "Permission denied (publickey)"),
                  (("bk-max", mini, 'put x videos/x.teil'), "Permission denied")]
        for (benutzer, schluessel, befehl), meldung in faelle:
            with self.subTest(befehl=befehl, benutzer=benutzer):
                r = sftp(benutzer, schluessel, befehl)
                self.assertNotEqual(r.returncode, 0)
                self.assertIn(meldung, r.stderr)
        self.assertFalse((self.tmp / "zurueck").exists())
        self.assertFalse((self.fach / "videos" / "x.teil").exists())
        self.assertEqual(list((self.vserver / "bk-eva" / "fach" / "videos").iterdir()), [])


@unittest.skipIf(PWSH is None, "pwsh fehlt")
class Einrichten(MitPc):
    def test_kopiert_merkt_ordner_und_stichtag_und_prueft_die_verbindung(self):
        (self.install / "freund.psd1").write_text(
            (self.install / "freund.psd1").read_text().replace(f"    Ordner = @('{self.videos}')\n", ""))
        (self.vserver / "bk-max" / ".schluessel_pc").write_text(str(self.appdata / "ClipUpload" / "pc"))   # eingerichtet
        lauf = self.lauf("", "-Ordner", f"'{self.videos}'", "-OhneAufgabe", skript="Freund-Einrichten.ps1")
        self.assertEqual(lauf.returncode, 0, lauf.stdout + lauf.stderr)
        ziel = self.appdata / "ClipUpload"
        for name in ("Freund-Hochladen.ps1", "Freund-Einrichten.ps1", "Freund-Einrichten.cmd", "freund.psd1", "pc",
                     "known_hosts"):
            self.assertEqual((ziel / name).read_bytes(), (self.install / name).read_bytes(), name)
        self.assertEqual((ziel / "stand" / "ordner.txt").read_text(encoding="utf-8").strip(), str(self.videos))
        stichtag = datetime.fromisoformat((ziel / "stand" / "stichtag.txt").read_text().replace("Z", "+00:00"))
        self.assertAlmostEqual(stichtag.timestamp(), time.time() - 86400, delta=300)
        self.assertIn("[OK] Verbunden mit Florians Briefkasten", lauf.stdout)
        self.assertEqual(self.im_fach(), [])                              # die Probe lädt nichts hoch

    def test_unvollstaendig_entpackt_bricht_ab(self):
        (self.install / "pc").unlink()
        lauf = self.lauf("", "-OhneAufgabe", skript="Freund-Einrichten.ps1")
        self.assertEqual(lauf.returncode, 1, lauf.stdout + lauf.stderr)
        self.assertIn("Es fehlt: pc", lauf.stdout)
        self.assertFalse((self.appdata / "ClipUpload").exists())


# --- Statisch: Fallen, die Stolperdraht51 nicht sieht ----------------------------------------------------------------
# PowerShell (5.1 wie 7) nimmt „ “ ” (und ‚ ‘ ’) als Anführungszeichen: In "… „$x“ …" endet der String am „ – das ist
# gültige Syntax (Stolperdraht51 schweigt), nur fehlen in der Ausgabe dann Zeichen. Geprüft mit dem echten Tokenizer:
# Kein String darf mit so einem Zeichen beginnen oder enden. Die .cmd liest cmd.exe in der OEM-Codepage: nur ASCII.
TYPOGRAFISCH = r"""
$ErrorActionPreference = 'Stop'
$typografisch = [char[]](0x201C, 0x201D, 0x201E, 0x2018, 0x2019, 0x201A, 0x201B)
$ergebnis = [ordered]@{}
foreach ($pfad in $args) {
    $token = $null; $fehler = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile($pfad, [ref]$token, [ref]$fehler)
    $funde = @()
    foreach ($t in $token) {
        if ($t -is [System.Management.Automation.Language.StringToken]) {
            $text = $t.Extent.Text
            if ($text.Length -and ($typografisch -contains $text[0] -or $typografisch -contains $text[$text.Length - 1])) {
                $funde += "Zeile $($t.Extent.StartLineNumber)"
            }
        }
    }
    $ergebnis[$pfad] = $funde
}
$ergebnis | ConvertTo-Json -Depth 3 -Compress
"""


class Statisch(unittest.TestCase):
    SKRIPTE = sorted((PROJEKT / "windows").glob("*.ps1"))

    @unittest.skipIf(PWSH is None, "pwsh fehlt")
    def test_keine_typografischen_anfuehrungszeichen_als_stringgrenze(self):
        def funde(pfade: list[Path]) -> dict[str, list[str]]:
            with tempfile.TemporaryDirectory() as tmp:
                skript = Path(tmp) / "pruefung.ps1"
                skript.write_text(TYPOGRAFISCH, encoding="utf-8-sig")
                r = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-File", str(skript), *map(str, pfade)],
                                   capture_output=True, text=True, timeout=120)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            return json.loads(r.stdout)

        self.assertTrue(self.SKRIPTE)
        self.assertEqual({Path(p).name: f for p, f in funde(self.SKRIPTE).items() if f}, {})
        with tempfile.TemporaryDirectory() as tmp:   # greift er?
            schlecht = Path(tmp) / "schlecht.ps1"
            schlecht.write_text('Write-Host "Aufgabe \u201e$x\u201c fertig"\nWrite-Host \'gut: \u201e$x\u201c\'\n',
                                encoding="utf-8-sig")
            self.assertEqual(set(funde([schlecht])[str(schlecht)]), {"Zeile 1"})   # Zeile 2 (einfache Quotes) ist gut

    def test_cmd_nur_ascii(self):
        for pfad in (PROJEKT / "windows").glob("*.cmd"):
            with self.subTest(pfad.name):
                self.assertTrue(pfad.read_bytes().isascii(), f"{pfad.name}: nur ASCII (cmd.exe liest OEM-Codepage)")


# --- Ende zu Ende ohne Netz: PC → Fach → Abholer → scan --------------------------------------------------------------

try:
    from clip_pipeline import konfig as konfig_mod, medien

    from tests.hilfen import HAT_FFMPEG, testvideo
    from tests.test_briefkasten import MitBriefkasten
    from tests.test_ende_zu_ende_stufe2 import elim, falsches_replay2json, replay_json
except ImportError:   # pragma: no cover
    MitBriefkasten = unittest.TestCase
    HAT_FFMPEG = False


@unittest.skipIf(PWSH is None or not HAT_FFMPEG, "pwsh oder ffmpeg fehlt")
class EndeZuEnde(MitBriefkasten):
    def test_pc_fach_abholer_scan_ergibt_ein_match_mit_clip(self):
        jetzt_s = time.time()
        t0 = datetime.fromtimestamp(int(jetzt_s) - 3 * 3600, UTC)            # Match-Start vor 3 h
        sid = f"{utc_zu_lokal(t0, ZONE):%Y-%m-%d_%H-%M-%S}"
        ende_video = t0 + timedelta(minutes=10)                              # Nvidia: Zeit im Namen = Ende
        video_name = f"{utc_zu_lokal(ende_video, ZONE):Fortnite %Y.%m.%d - %H.%M.%S}.22.Eliminierung.mp4"
        pc = self.tmp / "pc"
        video = testvideo(pc / "Videos" / video_name, dauer=20, tonspuren=2, creation_time=ende_video)
        os.utime(video, (ende_video.timestamp() + 7, ende_video.timestamp() + 7))
        replay = pc / "Demos" / f"{utc_zu_lokal(t0, ZONE):UnsavedReplay-%Y.%m.%d-%H.%M.%S.replay}"
        replay.parent.mkdir()
        replay.write_bytes(b"replay-attrappe")
        os.utime(replay, (t0.timestamp() + 900, t0.timestamp() + 900))      # Match-Ende nach 15 min
        beginn = ende_video + timedelta(seconds=1.4 - medien.probe(video).dauer_s)
        kill_ms = round((beginn + timedelta(seconds=8) - t0).total_seconds() * 1000)
        programm = falsches_replay2json(self.tmp / "replay2json", replay_json(t0, 15 * 60, [elim(kill_ms, "A", False)]))

        # der PC: Programm, Schlüssel, Konfig mit der Attrappe – er lädt in dasselbe Fach, aus dem der Mini abholt
        install = pc / "ClipUpload"
        install.mkdir()
        shutil.copy(PROJEKT / "windows" / "Freund-Hochladen.ps1", install)
        (install / "pc").write_text("pc-schluessel-attrappe (kein echter Schlüssel)\n", encoding="utf-8")
        (install / "known_hosts").write_text("[vserver.example.org]:2222 ssh-ed25519 AAAA-attrappe\n")
        (self.tmp / "vserver" / "bk-max" / ".schluessel_pc").write_text(str(install / "pc"))
        (install / "freund.psd1").write_text(
            f"@{{ Adresse = 'vserver.example.org'; Port = 2222; Benutzer = 'bk-max'; DrosselBeimSpielenKbit = 2000\n"
            f"   Sftp = '{self.tmp / 'sftp'}'; Ordner = @('{pc / 'Videos'}')\n"
            f"   Demos = '{pc / 'Demos'}' }}\n", encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if not k.lower().endswith("_proxy")}
        env.update(LOCALAPPDATA=str(pc / "appdata"), TZ=ZONE)
        lauf = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-File", str(install / "Freund-Hochladen.ps1")],
                              capture_output=True, text=True, env=env, timeout=180)
        self.assertEqual(lauf.returncode, 0, lauf.stdout + lauf.stderr)
        self.assertTrue((self.fach() / "sitzungen" / f"session_{sid}.json").is_file())   # Abend lange vorbei

        # der Mini: abholen, scan – das Match mit Clip; danach kommt die Abend-Datei
        code, e = self.abholen()
        self.assertEqual((code, e["abgeholt"], e["wartet"], e["fehler"]), (0, 2, 1, []), e)
        self.assertEqual((self.max / "daten" / "eingang" / "nvidia" / "highlights" / video_name).read_bytes(),
                         video.read_bytes())
        with mock.patch.object(konfig_mod.Konfig, "replay_programm", new=property(lambda k: programm)):
            code, scan = self.pipeline("scan", "--verarbeiten", "--max", "1", "--versuche", "3")
        self.assertEqual((code, scan["neue_matches"], scan["verarbeitet"][0]["clips"]), (0, [sid], 1), scan)
        code, e = self.abholen()
        self.assertEqual((code, e["abgeholt"]), (0, 1), e)
        self.assertTrue((self.max / "daten" / "sitzungen" / f"session_{sid}.json").is_file())
        self.assertIn("briefkasten:pc_verbunden", self.meldungen())        # der Status vom PC kam mit an


if __name__ == "__main__":
    unittest.main()
