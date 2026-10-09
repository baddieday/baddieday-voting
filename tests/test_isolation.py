"""Mehrbenutzer, Stufe 1, Schritt 4 (PR 4): Trennung Ende-zu-Ende – drei Benutzer, ein Squad-Match, kein Zugriff
aufeinander.

Generalprobe auf echter Bühne: Florian läuft mit seiner Standard-Konfig (pipeline.toml, lokal.toml und .env in einem
Temp-Projekt wie /opt/clip-pipeline). max und eva laufen als Instanzen (CLIP_INSTANZ) aus DERSELBEN Installation, deren
lokal.toml und .env Florian gehören; in ihrer Umgebung stehen zusätzlich Florians Zugänge wie bei einem Aufruf aus
seiner Shell. Alle drei spielten dasselbe Squad-Match: gleiche Session-ID, gleicher Replay-Name, dieselben Kills im
Replay. Jeder hat aber seine eigene Aufnahme, seine eigene Epic-ID und damit seine eigenen Kills (Florian 1, max 2,
eva 3). Jeder Lauf ist ein eigener Prozess wie später im Dienst: `scan --verarbeiten` (die Freunde mit den Schaltern
ihres Timers) und danach `stimmung` für die Momente, mit echtem ffmpeg. Attrappe ist nur replay2json: Es liest das
Match aus der Replay-Datei und setzt „ich“ nach --ich wie das echte Programm. Kein Lauf startet claude: Florian hat
die KI in seiner lokal.toml aus, die Freunde haben keinen eigenen Claude-Zugang.

Geprüft wird nach jedem der drei Läufe (Spec PR 4):
  - Dateien: Der SHA-256-Abdruck aller Dateien der beiden anderen und der Installation bleibt gleich; Clips, Momente
    und der Session-Ordner entstehen nur beim Läufer.
  - Datenbank: Jede hat nur ihr Match mit ihren Kills. Die gleiche Session-ID ergibt drei vollständig verarbeitete
    Matches – nichts wird als schon fertig übersprungen.
  - Aufträge: Jeder Lauf nahm die gemeinsame Sperre (der Pfad wird mitgeschrieben); solange sie belegt ist, rechnet
    keiner (Exit 4).
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from clip_pipeline import medien
from clip_pipeline.claude_aufruf import KEIN_ZUGANG
from clip_pipeline.zeit import UTC, iso, utc_zu_lokal

from tests.hilfen import HAT_FFMPEG, instanz_anlegen, testvideo
from tests.test_ende_zu_ende_stufe2 import KEIN_PROGRAMM, elim, replay_json, toml_text

PROJEKT = Path(__file__).resolve().parents[1]
REPO_KONFIG = PROJEKT / "config" / "pipeline.toml"
ZONE = "Europe/Berlin"
T0 = datetime(2026, 10, 7, 19, 42, 22, tzinfo=UTC)   # Start des Squad-Matches (Ortszeit 21:42:22)
SID = "2026-10-07_21-42-22"                           # für alle drei dieselbe Session-ID
REPLAY_NAME = f"UnsavedReplay-{utc_zu_lokal(T0, ZONE):%Y.%m.%d-%H.%M.%S}.replay"
FREUNDE = ("max", "eva")
# Florians echte Orte auf dem Mini – kein Lauf darf sie anlegen (die Konfigs im Test zeigen alle in den Temp-Ordner)
ECHTE_ORTE = ("/var/lib/clip-benutzer", "/srv/clips", "/srv/puffer", "/srv/big")
# Florians Zugänge, wie sie in seiner Shell stehen könnten – ein Freund-Prozess muss sie verwerfen (M25)
FLORIANS_ZUGAENGE = {"TELEGRAM_BOT_TOKEN": "florian-test-clipbot", "LEARN_BOT_TOKEN": "florian-test-lernbot",
                     "CLIP_EPIC_ID": "FLORIAN-EPIC-TEST"}

# Startet die Pipeline wie bin/pipeline und schreibt den Sperrpfad jedes Laufs mit (Spec: „Aufträge“). Seit Stufe 3
# nimmt cli.main die Sperre über laufzeiten.lauf, und das ruft sperre.sperre – also wird dort mitgeschrieben.
STARTER = """
import os, sys
from clip_pipeline import cli, sperre
echte_sperre = sperre.sperre
def mitschreiben(pfad, *args, **kwargs):
    with open(os.environ["TEST_SPERRPROTOKOLL"], "a", encoding="utf-8") as f:
        f.write(os.path.realpath(pfad) + "\\n")
    return echte_sperre(pfad, *args, **kwargs)
sperre.sperre = mitschreiben
sys.exit(cli.main(sys.argv[1:]))
"""

# Attrappe für tools/replay2json – wie falsches_replay2json (test_ende_zu_ende_stufe2), aber für eine Installation, die
# alle teilen: Das Match steht in der Replay-Datei (hier JSON), „ich“ kommt wie beim echten Programm aus --ich.
ATTRAPPE = """#!{python}
import json, sys
daten = json.load(open(sys.argv[1], encoding="utf-8"))
ich = sys.argv[sys.argv.index("--ich") + 1] if "--ich" in sys.argv else ""
daten["ich"] = {{"epic_id": ich, "platzierung": daten["ich"]["platzierung"]}} if ich else {{}}
daten["stats_eliminierungen"] = sum(1 for e in daten["eliminierungen"] if e["eliminator"] == ich)
print(json.dumps(daten))
"""


@dataclass(frozen=True)
class Spieler:
    epic: str               # Epic-ID (Florian: projekt/.env, Freund: I/.env)
    nvidia_s: int           # Uhrzeit im Namen seiner Nvidia-Aufnahme, Sekunden nach T0
    kills_s: tuple          # seine Kills als Sekunde in seiner Aufnahme
    replay_s: int           # Länge seines Replays – wer früher rausgeht, hat ein kürzeres
    ereignis: str           # Nvidia-Ereignis im Dateinamen
    titel: str              # erwarteter Clip-Titel


SPIELER = {
    "florian": Spieler("FLORIAN-EPIC-TEST", 330, (8.0,), 900, "Eliminierung", "Einzelkill"),
    "max": Spieler("MAX-EPIC-TEST", 450, (8.0, 11.0), 780, "Doppeleliminierung", "Double Kill"),
    "eva": Spieler("EVA-EPIC-TEST", 600, (8.0, 10.0, 12.0), 840, "Dreifacheliminierung", "Triple Kill"),
}


@dataclass
class Lauf:
    argumente: tuple
    code: int
    stdout: str
    stderr: str
    sperren: list[str]      # mitgeschriebene Sperrpfade (aufgelöst)

    @property
    def json(self) -> dict:
        return json.loads(self.stdout.strip().splitlines()[-1])

    def __str__(self) -> str:   # für Fehlermeldungen: Aufruf, Exit, Ende von stdout und stderr
        return f"{' '.join(self.argumente)} → Exit {self.code}\n{self.stdout[-1500:]}\n{self.stderr[-3000:]}"


def abdruck(wurzel: Path) -> dict[str, str]:
    """SHA-256 jeder Datei unter wurzel (relativ, mit „/“); Ordner und Links stehen mit drin – auch ein neuer leerer
    Ordner oder ein Link wäre ein Zugriff."""
    ergebnis = {}
    for pfad in sorted(wurzel.rglob("*")):
        name = pfad.relative_to(wurzel).as_posix()
        if pfad.is_symlink():
            ergebnis[name] = "link:" + os.readlink(pfad)
        elif pfad.is_dir():
            ergebnis[name] = "ordner"
        else:
            ergebnis[name] = hashlib.sha256(pfad.read_bytes()).hexdigest()
    return ergebnis


def unterschied(vorher: dict, nachher: dict) -> list[str]:
    return sorted(n for n in vorher.keys() | nachher.keys() if vorher.get(n) != nachher.get(n))


@unittest.skipUnless(HAT_FFMPEG, "ffmpeg fehlt")
class DreiBenutzerEinSquadMatch(unittest.TestCase):
    """Baut einmal auf und lässt Florian, max und eva nacheinander laufen; die Tests prüfen die Ergebnisse."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._tmp.cleanup)
        cls.basis = Path(os.path.realpath(cls._tmp.name))
        cls.projekt = cls.basis / "projekt"           # die Installation (/opt/clip-pipeline) – gehört keinem Lauf
        cls._prot = tempfile.TemporaryDirectory()     # nur für den Test (Video, Sperrprotokoll), außerhalb von basis
        cls.addClassCleanup(cls._prot.cleanup)
        cls.protokoll = Path(cls._prot.name)
        cls.echt_vorher = {p: os.path.lexists(p) for p in ECHTE_ORTE}
        cls._installation()
        cls.sperrdatei = cls.basis / "florian" / "db" / "pipeline.lock"   # = <Florians Datenbank>.lock wie heute
        cls.wurzel = {"florian": cls.basis / "florian"}
        cls.puffer = {"florian": cls.basis / "florian" / "puffer"}
        cls._florian()
        for name in FREUNDE:   # warten_s = 0: bei belegter Sperre gleich Exit 4 statt nach 900 s
            s = SPIELER[name]
            cls.wurzel[name] = instanz_anlegen(
                cls.basis / "freunde", name, sperre=cls.sperrdatei, toml="warten_s = 0\n",
                env=f"LEARN_BOT_TOKEN={name}-test-lernbot\nLEARN_BOT_ALLOWED_USER_ID=222\nCLIP_EPIC_ID={s.epic}\n")
            cls.puffer[name] = cls.wurzel[name] / "daten"
        cls._aufnahmen_und_replays()

        cls.ergebnis: dict[str, dict] = {}
        for name in ("florian", *FREUNDE):
            scan = ["scan", "--verarbeiten"] + (["--max", "1", "--versuche", "3"] if name in FREUNDE else [])
            vorher = abdruck(cls.basis)
            with cls._sperre_gehalten():                # ein anderer Schritt rechnet gerade
                gesperrt = cls._starte(name, *scan)
            laeufe = {"gesperrt": gesperrt, "scan": cls._starte(name, *scan),
                      "stimmung": cls._starte(name, "stimmung", "--ohne-claude", "--ohne-whisper")}
            cls.ergebnis[name] = {"vorher": vorher, "nachher": abdruck(cls.basis), **laeufe}
        cls.echt_nachher = {p: os.path.lexists(p) for p in ECHTE_ORTE}

    # --- Aufbau -------------------------------------------------------------------------------------------------

    @classmethod
    def _installation(cls):
        """Temp-Projekt wie /opt/clip-pipeline: Repo-pipeline.toml unverändert, replay2json als Attrappe."""
        (cls.projekt / "config").mkdir(parents=True)
        shutil.copy(REPO_KONFIG, cls.projekt / "config" / "pipeline.toml")
        programm = cls.projekt / tomllib.loads(REPO_KONFIG.read_text(encoding="utf-8"))["replay"]["programm"][0]
        programm.parent.mkdir(parents=True)
        programm.write_text(ATTRAPPE.format(python=sys.executable), encoding="utf-8")
        programm.chmod(0o755)

    @classmethod
    def _florian(cls):
        """Florians Standard-Konfig: lokal.toml und .env im Projekt; alle seine Orte im Temp-Ordner, getrennter Betrieb
        wie seit E19 (Puffer + Lager), KI aus, Sperre = <Datenbank>.lock ([sperre].datei bleibt leer)."""
        f = cls.wurzel["florian"]
        for ordner in ("db", "puffer", "lager", "regie", "musik", "material", "sfx", "home"):
            (f / ordner).mkdir(parents=True)
        for marke in (".clip-speicher", ".clip-puffer"):
            (f / "puffer" / marke).touch()
        (f / "lager" / ".clip-lager").touch()
        cls.sperrdatei.touch()   # auf dem Mini gibt es sie längst – ein Freund dürfte sie gar nicht anlegen
        lokal = {"speicher": {"wurzel": str(f / "puffer"), "host": "", "wol_mac": ""},
                 "lager": {"wurzel": str(f / "lager")},
                 "datenbank": {"pfad": str(f / "db" / "pipeline.db")},
                 "decide": {"claude": False, "programm": KEIN_PROGRAMM},
                 "sperre": {"warten_s": 0},
                 "big": {"host": "", "ssh_ziel": "", "frist": "", "zustand_ordner": str(f / "db")},
                 "regie": {"ordner": str(f / "regie"), "effekte": {"sfx_ordner": str(f / "sfx")}},
                 "musik": {"ordner": str(f / "musik")}, "material": {"ordner": str(f / "material")}}
        (cls.projekt / "config" / "lokal.toml").write_text(toml_text(lokal), encoding="utf-8")
        (cls.projekt / ".env").write_text("".join(f"{n}={w}\n" for n, w in FLORIANS_ZUGAENGE.items())
                                          + "TELEGRAM_ALLOWED_USER_ID=111\n", encoding="utf-8")

    @classmethod
    def _aufnahmen_und_replays(cls):
        """Ein Testvideo (20 s), für jeden unter seinem Nvidia-Namen in seinen Eingang kopiert. Das Replay enthält die
        Kills aller drei; jeder hat es in seinem Puffer, nur die Länge ist seine. Alles älter als die Ruhezeiten."""
        video = testvideo(cls.protokoll / "testvideo.mp4", dauer=20, tonspuren=2)
        dauer = medien.probe(video).dauer_s
        versatz = tomllib.loads(REPO_KONFIG.read_text(encoding="utf-8"))["quellen"]["nvidia"]["versatz_s"]
        kills = []
        for name, s in SPIELER.items():
            beginn = s.nvidia_s + versatz - dauer    # Sekunde nach T0, bei der seine Aufnahme beginnt
            kills += [dict(elim(round((beginn + k) * 1000), f"gegner-{name}-{i}", False), eliminator=s.epic)
                      for i, k in enumerate(s.kills_s)]
        kills.sort(key=lambda e: e["t_ms"])
        alt = time.time() - 600
        cls.aufnahme = {}
        for name, s in SPIELER.items():
            lokal = utc_zu_lokal(T0 + timedelta(seconds=s.nvidia_s), ZONE)
            cls.aufnahme[name] = lokal.strftime(f"eingang/nvidia/Fortnite %Y.%m.%d - %H.%M.%S.22.{s.ereignis}.DVR.mp4")
            ziel = cls.puffer[name] / cls.aufnahme[name]
            ziel.parent.mkdir(parents=True)
            shutil.copyfile(video, ziel)
            replay = cls.puffer[name] / "replays" / REPLAY_NAME
            replay.parent.mkdir()
            replay.write_text(json.dumps(replay_json(T0, s.replay_s, kills)), encoding="utf-8")
            for pfad in (ziel, replay):
                os.utime(pfad, (alt, alt))

    # --- Läufe --------------------------------------------------------------------------------------------------

    @classmethod
    def _umgebung(cls, name: str) -> dict[str, str]:
        """Wie im Dienst: kleine feste Umgebung, Code aus diesem Checkout, Installation = Temp-Projekt. Ein Freund
        bekommt CLIP_INSTANZ und sein HOME unter I/cache (M32) – und obendrein Florians Zugänge."""
        umgebung = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8",
                    "PYTHONPATH": str(PROJEKT / "src"), "PYTHONDONTWRITEBYTECODE": "1",
                    "CLIP_PROJEKT": str(cls.projekt)}
        if name == "florian":
            return {**umgebung, "HOME": str(cls.wurzel[name] / "home")}
        cache = cls.wurzel[name] / "cache"
        return {**umgebung, **FLORIANS_ZUGAENGE, "CLIP_INSTANZ": str(cls.wurzel[name]), "HOME": str(cache),
                "XDG_CACHE_HOME": str(cache), "HF_HOME": str(cache / "huggingface")}

    @classmethod
    def _starte(cls, name: str, *argumente: str) -> Lauf:
        protokoll = cls.protokoll / f"sperren-{name}-{time.monotonic_ns()}.txt"
        r = subprocess.run([sys.executable, "-c", STARTER, *argumente], cwd=cls.projekt, capture_output=True,
                           text=True, timeout=600, env={**cls._umgebung(name), "TEST_SPERRPROTOKOLL": str(protokoll)})
        sperren = protokoll.read_text(encoding="utf-8").splitlines() if protokoll.exists() else []
        return Lauf(argumente, r.returncode, r.stdout, r.stderr, sperren)

    @classmethod
    @contextmanager
    def _sperre_gehalten(cls):
        with open(cls.sperrdatei) as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            yield

    # --- Hilfen für die Prüfungen ------------------------------------------------------------------------------

    def gehoert(self, relativ: str) -> str | None:
        """Wem ein Pfad aus dem Abdruck von basis gehört: „projekt“ (die Installation), „florian“, „max“, „eva“ – oder
        keinem (z. B. ein neuer Ordner neben den Instanzen)."""
        for name, wurzel in (("projekt", self.projekt), *self.wurzel.items()):
            if relativ.startswith(wurzel.relative_to(self.basis).as_posix() + "/"):
                return name
        return None

    def datenbank(self, name: str) -> sqlite3.Connection:
        pfad = self.wurzel[name] / "db" / "pipeline.db"
        con = sqlite3.connect(f"file:{pfad}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        self.addCleanup(con.close)
        return con

    # --- Prüfungen ----------------------------------------------------------------------------------------------

    def test_dateien_der_anderen_bleiben_unberuehrt(self):
        for laeufer, e in self.ergebnis.items():
            with self.subTest(laeufer=laeufer):
                # der Abdruck sah bei allen vier Bereichen wirklich Dateien
                self.assertEqual({self.gehoert(n) for n in e["vorher"]} - {None}, {"projekt", *self.wurzel})
                geaendert = unterschied(e["vorher"], e["nachher"])
                self.assertTrue(geaendert)
                # SHA-256 aller Dateien der anderen und der Installation gleich; auch daneben entsteht nichts
                fremd = {n: self.gehoert(n) for n in geaendert if self.gehoert(n) != laeufer}
                self.assertEqual(fremd, {}, f"Lauf von {laeufer} änderte außerhalb seiner Wurzel")
        self.assertEqual(self.echt_nachher, self.echt_vorher)   # Florians echte Orte auf dem Mini: nichts angelegt

    def test_clips_momente_sessions_nur_beim_laeufer(self):
        for name, s in SPIELER.items():
            with self.subTest(name=name):
                for lauf in ("scan", "stimmung"):
                    self.assertEqual(self.ergebnis[name][lauf].code, 0, str(self.ergebnis[name][lauf]))
                puffer = self.puffer[name]
                self.assertEqual(sorted(p.name for p in (puffer / "sessions").iterdir()), [SID])
                sitzung = puffer / "sessions" / SID
                clips = sorted((sitzung / "clips").iterdir())
                self.assertEqual(len(clips), 1)
                self.assertTrue((sitzung / "vorschau" / "001.mp4").is_file())
                neu = set(unterschied(self.ergebnis[name]["vorher"], self.ergebnis[name]["nachher"]))
                for datei in (clips[0], sitzung / "vorschau" / "001.mp4", sitzung / "analyse.json",
                              sitzung / "schnittliste.json", sitzung / "replay.json"):
                    self.assertIn(datei.relative_to(self.basis).as_posix(), neu)   # in genau diesem Lauf entstanden
                con = self.datenbank(name)
                clip = con.execute("SELECT clip_pfad, vorschau_pfad FROM clips").fetchone()
                self.assertEqual(puffer / clip["clip_pfad"], clips[0])
                self.assertEqual(puffer / clip["vorschau_pfad"], sitzung / "vorschau" / "001.mp4")
                momente = con.execute("SELECT schluessel, datei FROM momente").fetchall()
                self.assertEqual(len(momente), 1)
                self.assertEqual(Path(momente[0]["datei"]), clips[0])   # der Moment ist der eigene Clip
                self.assertEqual(self.ergebnis[name]["stimmung"].json["analysiert"], 1)
                # replay2json bekam die eigene Epic-ID – auch bei den Freunden, obwohl Florians in der Umgebung stand
                self.assertEqual(json.loads((sitzung / "replay.json").read_text())["ich"]["epic_id"], s.epic)
        for name in FREUNDE:   # Instanz ohne Lager (M10): den Lager-Pfad gibt es nach wie vor nicht
            self.assertFalse(os.path.lexists(self.wurzel[name] / "kein-lager"))

    def test_jede_datenbank_hat_nur_ihr_match(self):
        for name, s in SPIELER.items():
            with self.subTest(name=name):
                con = self.datenbank(name)
                matches = [tuple(z) for z in con.execute(
                    "SELECT id, status, kills, platzierung, ende_utc FROM matches")]
                self.assertEqual(matches, [(SID, "verarbeitet", len(s.kills_s), 3,
                                            iso(T0 + timedelta(seconds=s.replay_s)))])   # sein Replay, seine Kills
                self.assertEqual([tuple(z) for z in con.execute("SELECT titel, kills FROM clips")],
                                 [(s.titel, len(s.kills_s))])
                self.assertEqual([z["pfad"] for z in con.execute("SELECT pfad FROM aufnahmen")],
                                 [self.aufnahme[name]])                  # nur seine eigene Aufnahme
                # gleiche Session-ID bei allen dreien: vollständig verarbeitet, nichts als „schon fertig“ übersprungen
                scan = self.ergebnis[name]["scan"].json
                self.assertEqual((scan["neue_matches"], scan["offen"]), ([SID], [SID]))
                verarbeitet = scan["verarbeitet"]
                self.assertEqual(len(verarbeitet), 1, verarbeitet)
                self.assertEqual({k: verarbeitet[0][k] for k in ("session", "clips", "neu", "top_label")},
                                 {"session": SID, "clips": 1, "neu": 1, "top_label": s.titel})
                schritte = verarbeitet[0]["schritte"]
                self.assertNotIn("uebersprungen", schritte["decide"])
                self.assertEqual((schritte["analyze"]["kill_quelle"], schritte["decide"]["entschieden_von"]),
                                 ("replay", "regel"))
                # KI: Florian hat sie aus; ein Freund ohne eigenen Zugang bekommt den Hinweis – claude startet nie
                self.assertEqual(any(KEIN_ZUGANG in h for h in schritte["decide"]["hinweise"]), name in FREUNDE)

    def test_jeder_lauf_nimmt_die_gemeinsame_sperre(self):
        gemeinsam = os.path.realpath(self.sperrdatei)
        for name, e in self.ergebnis.items():
            with self.subTest(name=name):
                gesperrt = e["gesperrt"]
                self.assertEqual(gesperrt.code, 4, str(gesperrt))   # belegt → rechnet nicht, Exit 4 wie im Vertrag
                self.assertEqual(len(gesperrt.stdout.strip().splitlines()), 1)
                self.assertEqual(gesperrt.json["fehler"], "gesperrt")
                for lauf in ("gesperrt", "scan", "stimmung"):
                    self.assertEqual(e[lauf].sperren, [gemeinsam], f"{name}/{lauf}")
                self.assertIn("Sperre gewartet", e["scan"].stderr)
        # nirgends eine zweite, private Sperre (jede Instanz hätte sonst <ihre Datenbank>.lock)
        self.assertEqual(sorted(self.basis.rglob("*.lock")), [self.sperrdatei])


if __name__ == "__main__":
    unittest.main()
