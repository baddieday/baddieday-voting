"""windows/Uebertragung.ps1 unter PowerShell 7 (falls installiert): Session vorbei genau einmal, Replays bleiben,
Match-IDs gehen nicht verloren, pc-status.json; dazu ein Stolperdraht für Windows PowerShell 5.1 (BOM, Syntax)."""

import fcntl
import http.server
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from datetime import UTC, datetime
from pathlib import Path

from clip_pipeline.zeit import aus_iso

PROJEKT = Path(__file__).resolve().parents[1]
SKRIPT = PROJEKT / "windows/Uebertragung.ps1"
PWSH = shutil.which("pwsh") or ("/opt/pwsh/pwsh" if Path("/opt/pwsh/pwsh").is_file() else None)
REPLAY = "UnsavedReplay-2026.09.24-20.15.33.replay"
REPLAY_ID = "2026-09-24_20-15-33"

# Ersetzt Add-Content: Beim tsv-Eintrag endet das Skript wie bei einem Absturz (exit, kein catch, nur finally).
# Funktionen gehen in PowerShell vor Cmdlets; alle anderen Aufrufe (Log) laufen normal durch.
ABBRUCH_VOR_TSV = (
    "function global:Add-Content { param([string]$Path, $Value, $Encoding)\n"
    "  if ($Path -like '*uebertragen.tsv') { exit 7 }\n"
    "  Microsoft.PowerShell.Management\\Add-Content -Path $Path -Value $Value -Encoding $Encoding }\n")
# Wie oben, aber der tsv-Eintrag wirft einen Fehler (Excel-Sperre, Virenscanner): Der Lauf fängt ihn ab und zählt ihn.
FEHLER_BEI_TSV = ABBRUCH_VOR_TSV.replace("{ exit 7 }", "{ throw 'tsv gesperrt (Test)' }")


def _alt(pfad: Path, sekunden: float = 600) -> None:
    os.utime(pfad, (time.time() - sekunden, time.time() - sekunden))


def _ordner(t: Path) -> None:
    for d in ("demos", "ziel", "appdata"):
        (t / d).mkdir()
    (t / "ziel" / ".clip-speicher").touch()


def _konfig(t: Path, quellen: list[str], **werte: str) -> None:
    """t/k.psd1 schreiben; Werte als PowerShell-Ausdruck, z. B. WebhookUrl="'http://…'"."""
    basis = {"Ziel": f"'{t}/ziel'", "ZielHost": "''", "WakeOnLanMac": "''", "WeckenWarteSekunden": "10",
             "WebhookUrl": "''", "WebhookToken": "''", "RuhezeitSekunden": "60", "SessionVorbeiMinuten": "0",
             "MaxAlterTage": "30"}
    basis.update(werte)
    zeilen = "".join(f"    {k} = {v}\n" for k, v in basis.items())
    (t / "k.psd1").write_text("@{\n" + zeilen + "    Quellen = @(\n"
                              + "".join(f"        @{{ {q} }}\n" for q in quellen) + "    )\n}\n", encoding="utf-8")


def _starte(t: Path, vorher: str = "") -> subprocess.CompletedProcess:
    """Ein Lauf mit t/k.psd1, LOCALAPPDATA im Temp-Ordner, ohne Proxy (der Test-Webhook hört auf 127.0.0.1)."""
    env = {k: v for k, v in os.environ.items() if not k.lower().endswith("_proxy")}
    env["LOCALAPPDATA"] = str(t / "appdata")
    if vorher:
        befehl = [PWSH, "-NoProfile", "-Command", f"{vorher}& '{SKRIPT}' -Konfig '{t / 'k.psd1'}'; exit $LASTEXITCODE"]
    else:
        befehl = [PWSH, "-NoProfile", "-File", str(SKRIPT), "-Konfig", str(t / "k.psd1")]
    return subprocess.run(befehl, capture_output=True, text=True, env=env, timeout=120)


def _zeilen(pfad: Path) -> list[str]:
    return [z for z in pfad.read_text(encoding="utf-8-sig").splitlines() if z] if pfad.exists() else []


class _Webhook(http.server.BaseHTTPRequestHandler):
    """Nimmt POST /webhook/match-vorbei an und merkt sich (Token, Body)."""
    eingang: list = []

    def do_POST(self):
        laenge = int(self.headers.get("Content-Length") or 0)
        self.eingang.append((self.path, self.headers.get("X-Pipeline-Token"), json.loads(self.rfile.read(laenge))))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args):
        pass


def _webhook(test: unittest.TestCase) -> tuple[type, str]:
    """Lokaler Test-Webhook mit eigener Eingangsliste; gibt (Klasse, URL) zurück."""
    webhook = type("Webhook", (_Webhook,), {"eingang": []})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), webhook)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    test.addCleanup(server.server_close)
    test.addCleanup(server.shutdown)
    return webhook, f"http://127.0.0.1:{server.server_port}/webhook/match-vorbei"


@unittest.skipIf(PWSH is None, "pwsh fehlt")
class SessionVorbei(unittest.TestCase):
    def test_einmal_melden_nichts_loeschen(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            for d in ("demos", "ziel", "appdata"):
                (t / d).mkdir()
            (t / "ziel" / ".clip-speicher").touch()
            alt = time.time() - 600
            for name in ("UnsavedReplay-2026.09.24-20.15.33.replay", "UnsavedReplay-2026.09.24-20.41.02.replay"):
                (t / "demos" / name).write_text("replay")
                os.utime(t / "demos" / name, (alt, alt))
            (t / "k.psd1").write_text(
                f"@{{ Ziel = '{t}/ziel'; ZielHost = ''; WakeOnLanMac = ''; WeckenWarteSekunden = 10; WebhookUrl = ''\n"
                f"   WebhookToken = ''; RuhezeitSekunden = 60; SessionVorbeiMinuten = 20; MaxAlterTage = 30\n"
                f"   Quellen = @( @{{ Pfad = '{t}/demos'; Muster = @('*.replay'); Ziel = 'replays'; NurKopieren = $true;"
                f" RuhezeitSekunden = 120; Melden = $true }} ) }}\n")

            def lauf():
                return subprocess.run([PWSH, "-NoProfile", "-File", str(PROJEKT / "windows/Uebertragung.ps1"),
                                       "-Konfig", str(t / "k.psd1")], capture_output=True, text=True,
                                      env={**os.environ, "LOCALAPPDATA": str(t / "appdata")}, timeout=120)

            self.assertEqual(lauf().returncode, 0)
            self.assertEqual(list((t / "ziel" / "sitzungen").glob("session_*.json")), [])  # gerade erst gespielt
            merk = t / "appdata" / "ClipPipeline" / "session.txt"
            os.utime(merk, (time.time() - 1500, time.time() - 1500))  # 25 min Ruhe
            self.assertEqual(lauf().returncode, 0)
            self.assertEqual(lauf().returncode, 0)
            dateien = list((t / "ziel" / "sitzungen").glob("session_*.json"))
            self.assertEqual(len(dateien), 1)
            daten = json.loads(dateien[0].read_text(encoding="utf-8-sig"))
            self.assertEqual(daten["matches"], ["2026-09-24_20-15-33", "2026-09-24_20-41-02"])
            self.assertEqual(len(list((t / "demos").iterdir())), 2)  # Rohdaten bleiben


@unittest.skipIf(PWSH is None, "pwsh fehlt")
class NurMitArbeitWecken(unittest.TestCase):
    """Der Lauf alle 2 min weckt pve-big nur, wenn es etwas zu kopieren gibt – sonst ginge er nie aus."""

    def test_ohne_neue_dateien_kein_wecken(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            for d in ("demos", "ziel", "appdata"):
                (t / d).mkdir()
            # Ziel "schläft": auf 127.0.0.1:445 hört niemand
            (t / "k.psd1").write_text(
                f"@{{ Ziel = '{t}/ziel'; ZielHost = '127.0.0.1'; WakeOnLanMac = 'aa:bb:cc:dd:ee:ff'; WeckenWarteSekunden = 1\n"
                f"   WebhookUrl = ''; WebhookToken = ''; RuhezeitSekunden = 60; SessionVorbeiMinuten = 0; MaxAlterTage = 30\n"
                f"   Quellen = @( @{{ Pfad = '{t}/demos'; Muster = @('*.replay'); Ziel = 'replays'; NurKopieren = $true }} ) }}\n")

            def lauf(*extra):
                return subprocess.run([PWSH, "-NoProfile", "-File", str(PROJEKT / "windows/Uebertragung.ps1"),
                                       "-Konfig", str(t / "k.psd1"), *extra], capture_output=True, text=True,
                                      env={**os.environ, "LOCALAPPDATA": str(t / "appdata")}, timeout=120)

            r = lauf()
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertNotIn("Wake-on-LAN", r.stdout)
            neu = t / "demos" / "UnsavedReplay-2026.09.24-20.15.33.replay"
            neu.write_text("replay")
            self.assertNotIn("Wake-on-LAN", lauf().stdout)             # noch zu frisch (Ruhezeit)
            os.utime(neu, (time.time() - 600, time.time() - 600))
            r = lauf("-Probelauf")                                       # fertig: jetzt lohnt sich das Wecken
            self.assertIn("Ziel schläft – sende Wake-on-LAN", r.stdout)
            self.assertEqual(r.returncode, 3)                            # (im Test wacht niemand auf)


@unittest.skipIf(PWSH is None, "pwsh fehlt")
class MatchIdVorErledigt(unittest.TestCase):
    """Stolperfalle 14: Bricht der Lauf nach der Replay-Kopie ab, steht die ID trotzdem in zu-melden.txt."""

    def _replay_mit_webhook(self, t: Path) -> type:
        webhook, url = _webhook(self)
        _ordner(t)
        (t / "demos" / REPLAY).write_text("replay")
        _alt(t / "demos" / REPLAY)
        _konfig(t, ["Pfad = '%s/demos'; Muster = @('*.replay'); Ziel = 'replays'; NurKopieren = $true;"
                    " RuhezeitSekunden = 120; Melden = $true" % t],
                WebhookUrl=f"'{url}'", WebhookToken="'test-token'", SessionVorbeiMinuten="20")
        return webhook

    def test_abbruch_vor_tsv_verliert_keine_meldung(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            webhook = self._replay_mit_webhook(t)
            daten = t / "appdata" / "ClipPipeline"

            for _ in range(2):                                # zweimal: IDs werden nicht doppelt notiert
                r = _starte(t, vorher=ABBRUCH_VOR_TSV)
                self.assertEqual(r.returncode, 7, r.stdout + r.stderr)
                self.assertTrue((t / "ziel" / "replays" / REPLAY).is_file())      # Kopie ist drüben
                self.assertEqual(_zeilen(daten / "zu-melden.txt"), [REPLAY_ID])   # ID ist sicher notiert
                self.assertEqual(_zeilen(daten / "session.txt"), [REPLAY_ID])
                self.assertEqual(_zeilen(daten / "uebertragen.tsv"), [])          # nicht als erledigt vermerkt
                self.assertEqual(webhook.eingang, [])                             # gemeldet wurde noch nicht

            r = _starte(t)                                    # normaler Lauf: holt die Meldung nach
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(webhook.eingang, [("/webhook/match-vorbei", "test-token", {"session": REPLAY_ID})])
            self.assertEqual(_zeilen(daten / "zu-melden.txt"), [])
            self.assertEqual(len(_zeilen(daten / "uebertragen.tsv")), 1)
            self.assertEqual(_starte(t).returncode, 0)        # nichts Neues: keine zweite Meldung
            self.assertEqual(len(webhook.eingang), 1)

    def test_tsv_fehler_meldet_trotzdem_nur_einmal(self):
        # Wirft der tsv-Eintrag (statt abzubrechen), meldet der Lauf das Match noch; die Datei steht aber im nächsten
        # Lauf wieder an. Ohne gemeldet.txt ginge die ID dann erneut an n8n – alle 2 min „Match verarbeitet“.
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            webhook = self._replay_mit_webhook(t)
            daten = t / "appdata" / "ClipPipeline"
            meldung = ("/webhook/match-vorbei", "test-token", {"session": REPLAY_ID})

            for _ in range(3):
                r = _starte(t, vorher=FEHLER_BEI_TSV)
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)          # als Kopierfehler gezählt
                self.assertIn("tsv gesperrt (Test)", r.stdout)
                self.assertEqual(_zeilen(daten / "uebertragen.tsv"), [])
                self.assertEqual(webhook.eingang, [meldung])                       # genau einmal
                self.assertEqual(_zeilen(daten / "zu-melden.txt"), [])
                self.assertEqual(_zeilen(daten / "gemeldet.txt"), [REPLAY_ID])
            self.assertEqual(_zeilen(daten / "session.txt"), [REPLAY_ID])         # auch hier nicht doppelt
            r = _starte(t, vorher=ABBRUCH_VOR_TSV)            # Abbruch vor dem Melden: nichts neu notiert
            self.assertEqual(r.returncode, 7, r.stdout + r.stderr)
            self.assertEqual(_zeilen(daten / "zu-melden.txt"), [])

            r = _starte(t)                                    # Sperre behoben: nur noch der tsv-Eintrag
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(len(_zeilen(daten / "uebertragen.tsv")), 1)
            self.assertEqual(webhook.eingang, [meldung])

    def test_absturz_nach_meldung_meldet_nicht_doppelt(self):
        # Absturz zwischen gemeldet.txt und dem Neuschreiben von zu-melden.txt: Die ID steht dann in beiden Dateien
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            webhook = self._replay_mit_webhook(t)
            daten = t / "appdata" / "ClipPipeline"
            daten.mkdir()
            for name in ("zu-melden.txt", "gemeldet.txt"):
                (daten / name).write_text(REPLAY_ID + "\n", encoding="utf-8")
            r = _starte(t)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(webhook.eingang, [])
            self.assertEqual(_zeilen(daten / "zu-melden.txt"), [])

    def test_gemeldet_txt_behaelt_die_letzten_500(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            _ordner(t)
            _konfig(t, ["Pfad = '%s/demos'; Muster = @('*.replay'); Ziel = 'replays'; Melden = $true" % t])
            datei = t / "appdata" / "ClipPipeline" / "gemeldet.txt"
            datei.parent.mkdir()
            ids = [f"2026-09-{i:04d}" for i in range(1001)]
            datei.write_text("\n".join(ids) + "\n", encoding="utf-8")
            self.assertEqual(_starte(t).returncode, 0)
            self.assertEqual(_zeilen(datei), ids[-500:])     # älteste weg, Reihenfolge bleibt
            datei.write_text("\n".join(ids[:1000]) + "\n", encoding="utf-8")
            self.assertEqual(_starte(t).returncode, 0)
            self.assertEqual(len(_zeilen(datei)), 1000)       # bis 1000 bleibt die Datei unangetastet


@unittest.skipIf(PWSH is None, "pwsh fehlt")
class SitzungNurOhneKopierfehler(unittest.TestCase):
    """Stolperfalle 13: Kopierfehler -> keine Sitzungsdatei, session.txt bleibt. Stolperfalle 8: matches sind Texte."""

    def test_kopierfehler_haelt_sitzung_zurueck(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            _ordner(t)
            (t / "clips").mkdir()
            (t / "demos" / REPLAY).write_text("replay")
            _alt(t / "demos" / REPLAY)
            _konfig(t, ["Pfad = '%s/demos'; Muster = @('*.replay'); Ziel = 'replays'; NurKopieren = $true;"
                        " RuhezeitSekunden = 120; Melden = $true" % t,
                        "Pfad = '%s/clips'; Muster = @('*.mp4'); Ziel = 'eingang'" % t],
                    SessionVorbeiMinuten="20")
            merk = t / "appdata" / "ClipPipeline" / "session.txt"
            sitzungen = t / "ziel" / "sitzungen"

            self.assertEqual(_starte(t).returncode, 0)
            self.assertEqual(_zeilen(merk), [REPLAY_ID])
            _alt(merk, 1500)                                  # 25 min Ruhe: Session wäre fällig …
            (t / "clips" / "Fortnite__1.mp4").write_text("clip")
            _alt(t / "clips" / "Fortnite__1.mp4")
            (t / "ziel" / "eingang").write_text("kein Ordner")  # … aber der Clip lässt sich nicht kopieren

            r = _starte(t)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("Sitzungsdatei später", r.stdout)
            self.assertEqual(list(sitzungen.glob("session_*.json")), [])
            self.assertEqual(_zeilen(merk), [REPLAY_ID])      # session.txt bleibt
            status = json.loads((sitzungen / "pc-status.json").read_text(encoding="utf-8"))
            self.assertEqual((status["kopiert"], status["fehler"]), (0, 1))
            self.assertIn("Fortnite__1.mp4", status["letzter_fehler"])
            self.assertEqual([(o["quelle"], o["anzahl"]) for o in status["offen"]], [(f"{t}/clips", 1)])

            (t / "ziel" / "eingang").unlink()                 # Fehler behoben
            r = _starte(t)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertTrue((t / "ziel" / "eingang" / "Fortnite__1.mp4").is_file())
            dateien = list(sitzungen.glob("session_*.json"))
            self.assertEqual(len(dateien), 1)
            matches = json.loads(dateien[0].read_text(encoding="utf-8-sig"))["matches"]
            self.assertEqual(matches, [REPLAY_ID])
            self.assertTrue(all(isinstance(m, str) for m in matches))
            self.assertFalse(merk.exists())
            status = json.loads((sitzungen / "pc-status.json").read_text(encoding="utf-8"))
            self.assertEqual((status["kopiert"], status["fehler"], status["letzter_fehler"], status["offen"]),
                             (1, 0, None, []))

    def test_matches_als_text_vor_convertto_json(self):
        # PS 5.1 gibt es hier nicht: Der Stolperdraht sichert, dass die Umwandlung nicht wieder verschwindet.
        self.assertTrue("matches = [string[]]$ids" in SKRIPT.read_text(encoding="utf-8-sig"),
                        "Sitzungsdatei: matches ohne [string[]] (Stolperfalle 8)")


@unittest.skipIf(PWSH is None, "pwsh fehlt")
class PcStatus(unittest.TestCase):
    """sitzungen/pc-status.json: Rückkanal für die Morgenprüfung auf dem Mini."""

    def test_status_nach_lauf_mit_arbeit(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            _ordner(t)
            (t / "demos" / REPLAY).write_text("replay")
            _alt(t / "demos" / REPLAY)
            # Zwei wartende Dateien in derselben Quelle: eine alte, noch zum Schreiben offene und eine zu junge
            gesperrt = t / "demos" / "UnsavedReplay-2026.09.24-20.41.02.replay"
            gesperrt.write_text("replay")
            _alt(gesperrt, 300)
            jung = t / "demos" / "UnsavedReplay-2026.09.24-21.02.10.replay"
            jung.write_text("replay")
            _alt(jung, 10)
            _konfig(t, ["Pfad = '%s/demos'; Muster = @('*.replay'); Ziel = 'replays'; NurKopieren = $true;"
                        " RuhezeitSekunden = 120; Melden = $true" % t])
            datei = t / "ziel" / "sitzungen" / "pc-status.json"

            def offen() -> list[tuple[str, int, float]]:
                return [(o["quelle"], o["anzahl"], aus_iso(o["aelteste_utc"]).timestamp())
                        for o in json.loads(datei.read_text(encoding="utf-8"))["offen"]]

            with gesperrt.open("rb") as griff:
                # Unter Linux scheitert damit [IO.File]::Open(…, 'Read', 'Read') in Datei-Frei – wie unter Windows,
                # solange Fortnite oder ein Rekorder die Datei zum Schreiben offen hält
                fcntl.flock(griff, fcntl.LOCK_EX)
                r = _starte(t)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertFalse((t / "ziel" / "replays" / gesperrt.name).exists())   # nicht kopiert …
                roh = datei.read_bytes()
                self.assertFalse(roh.startswith(b"\xef\xbb\xbf"))                  # UTF-8 ohne BOM
                status = json.loads(roh.decode("utf-8"))
                self.assertEqual(list(status), ["zeit_utc", "rechner", "kopiert", "fehler", "letzter_fehler", "offen"])
                self.assertLess(abs((datetime.now(UTC) - aus_iso(status["zeit_utc"])).total_seconds()), 300)
                self.assertTrue(isinstance(status["rechner"], str) and status["rechner"])
                self.assertEqual((status["kopiert"], status["fehler"], status["letzter_fehler"]), (1, 0, None))
                [(quelle, anzahl, aelteste)] = offen()                            # … aber offen, mit der jungen
                self.assertEqual((quelle, anzahl), (f"{t}/demos", 2))
                self.assertLess(abs(aelteste - gesperrt.stat().st_mtime), 1)      # die ÄLTESTE, nicht die letzte
                self.assertEqual([p.name for p in datei.parent.iterdir()], ["pc-status.json"])   # keine .teil-Reste

                datei.unlink()                                # Lauf ohne Arbeit fasst das Ziel nicht an
                self.assertEqual(_starte(t).returncode, 0)    # (hielte sonst pve-big per SMB wach)
                self.assertFalse(datei.exists())

            r = _starte(t)                                    # Sperre weg: jetzt wird sie kopiert
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertTrue((t / "ziel" / "replays" / gesperrt.name).is_file())
            [(quelle, anzahl, aelteste)] = offen()
            self.assertEqual((quelle, anzahl), (f"{t}/demos", 1))
            self.assertLess(abs(aelteste - jung.stat().st_mtime), 1)


# --- Stolperdraht: Der Gaming-PC startet die Skripte mit Windows PowerShell 5.1 -----------------------------------
# Ohne BOM liest 5.1 die Dateien als ANSI (Umlaute, "–" kaputt), und Syntax aus PowerShell 7 ist dort ein Parse-Fehler.
# Geprüft wird mit dem echten Parser von pwsh (Syntaxbaum): Nur er sieht auch den Code in "… $(…) …" und nach
# Here-Strings. Der Binder ordnet Parameter wie beim Aufruf zu – auch abgekürzte (% -Par) und Join-Path a b c.
PS7_PRUEFUNG = r"""
using namespace System.Management.Automation.Language
$ErrorActionPreference = 'Stop'   # jeder Fehler beendet die Prüfung (sonst bliebe eine Lücke still)
$ergebnis = [ordered]@{}
foreach ($pfad in $args) {
    $token = $null; $fehler = $null
    $ast = [Parser]::ParseFile($pfad, [ref]$token, [ref]$fehler)
    $funde = New-Object 'System.Collections.Generic.List[string]'
    foreach ($f in $fehler) { $funde.Add("Parse-Fehler: $($f.Message)") }
    foreach ($n in $ast.FindAll({ $true }, $true)) {
        if ($n -is [BinaryExpressionAst] -and $n.Operator -eq 'QuestionQuestion') {
            $funde.Add('?? (Null-Zusammenführung)')
        }
        elseif ($n -is [AssignmentStatementAst] -and $n.Operator -eq 'QuestionQuestionEquals') {
            $funde.Add('??= (Null-Zuweisung)')
        }
        elseif (($n -is [MemberExpressionAst] -or $n -is [IndexExpressionAst]) -and $n.NullConditional) {
            $funde.Add('?. / ?[ (null-bedingter Zugriff)')
        }
        elseif ($n -is [TernaryExpressionAst]) { $funde.Add('a ? b : c (Ternär)') }
        elseif ($n -is [PipelineChainAst]) { $funde.Add('&& / || (Pipeline-Kette)') }
        elseif ($n -is [CommandAst]) {
            $gebunden = [StaticParameterBinder]::BindCommand($n, $true).BoundParameters
            if ($gebunden.ContainsKey('Parallel')) { $funde.Add('ForEach-Object -Parallel') }
            if ($gebunden.ContainsKey('AsArray')) { $funde.Add('ConvertTo-Json -AsArray') }
            if ($gebunden.ContainsKey('AdditionalChildPath')) { $funde.Add('Join-Path mit 3 Argumenten') }
        }
    }
    $ergebnis[$pfad] = $funde.ToArray()
}
$ergebnis | ConvertTo-Json -Depth 3 -Compress
"""


def ps7_funde(pfade: list[Path]) -> dict[Path, list[str]]:
    """Syntax nur für PowerShell 7 je Datei (leer = läuft auch unter 5.1); ein pwsh-Aufruf für alle Dateien."""
    with tempfile.TemporaryDirectory() as tmp:
        skript = Path(tmp) / "ps7-pruefung.ps1"
        skript.write_text(PS7_PRUEFUNG, encoding="utf-8-sig")
        r = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-File", str(skript), *map(str, pfade)],
                           capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise AssertionError(f"Prüfung mit pwsh gescheitert: {r.stdout}{r.stderr}")
    daten = json.loads(r.stdout)
    return {p: daten[str(p)] for p in pfade}


def ps7_funde_code(schnipsel: list[str]) -> list[list[str]]:
    """Wie ps7_funde, für Code-Schnipsel (Selbsttest)."""
    with tempfile.TemporaryDirectory() as tmp:
        pfade = [Path(tmp) / f"schnipsel{i}.ps1" for i in range(len(schnipsel))]
        for pfad, code in zip(pfade, schnipsel):
            pfad.write_text(code, encoding="utf-8-sig")
        funde = ps7_funde(pfade)
        return [funde[p] for p in pfade]


class Stolperdraht51(unittest.TestCase):
    DATEIEN = sorted([*(PROJEKT / "windows").glob("*.ps1"), *(PROJEKT / "windows").glob("*.psd1")])

    def test_dateien_mit_bom(self):
        self.assertTrue(self.DATEIEN)
        for pfad in self.DATEIEN:
            with self.subTest(pfad.name):
                self.assertTrue(pfad.read_bytes().startswith(b"\xef\xbb\xbf"), f"{pfad.name}: UTF-8 ohne BOM")

    @unittest.skipIf(PWSH is None, "pwsh fehlt")
    def test_keine_syntax_nur_fuer_powershell_7(self):
        self.assertTrue(self.DATEIEN)
        for pfad, funde in ps7_funde(self.DATEIEN).items():
            with self.subTest(pfad.name):
                self.assertEqual(funde, [])

    @unittest.skipIf(PWSH is None, "pwsh fehlt")
    def test_stolperdraht_greift(self):
        schlecht = {
            "$a = $b ?? 'x'": "??", "$c ??= 1": "??", "$n = ${d}?.Name": "?.", "$e = ${f}?[0]": "?.",
            "$x = $y ? 1 : 2": "Ternär", "git pull && echo ok": "&&", "Test-Path x || exit 1": "&&",
            "1..3 | ForEach-Object -Parallel { $_ }": "-Parallel", "1..3 | % -Par { $_ }": "-Parallel",
            "$l | ConvertTo-Json -AsArray": "-AsArray",
            "$p = Join-Path $a 'b' 'c'": "Join-Path", "Join-Path $a -ChildPath b -AdditionalChildPath c": "Join-Path",
            # Code in "… $(…) …" und nach Here-Strings (übersah die frühere Zerlegung per regulärem Ausdruck)
            'Log "x $($a ?? 1)"': "??", 'Log "x $(Test-Path a && 3)"': "&&", 'Log "n: $($d.Name?.Length)"': "?.",
            'Log "a $("b $($c ? 1 : 2)")"': "Ternär",
            '$t = @"\nIt"s\n"@\n$b = $c ?? 1': "??", "$t = @'\nIt's\n'@\n$b = $c ? 1 : 2": "Ternär",
            "Log \"x $(if ($a) { 1 } else { 2 } && 3)\"": "Parse-Fehler",   # auch unter 7 kaputt: fällt trotzdem auf
        }
        for (code, name), funde in zip(schlecht.items(), ps7_funde_code(list(schlecht))):
            with self.subTest(code):
                self.assertTrue(any(name in f for f in funde), funde)
        gut = [
            "Get-ChildItem | Where-Object { $_ } | ? { $_.Name }",
            "$z = Join-Path (Join-Path $k.Ziel $q.Ziel) $unterpfad",
            "Join-Path -Path $a -ChildPath 'b' | Out-Null",
            "Join-Path $a 'b' -Resolve",
            "Log \"Wirklich?? a && b || c ? d : e\"   # Kommentar ?? && ||",
            "Log \"FEHLER bei $($datei.Name): $($_.Exception.Message)\"",
            "$t = @\"\nWer?? a && b || c ? d : e $($x.Name)\n\"@",
            "<# Block ?. ?? #> [IO.File]::Open($p, 'Open', 'Read', 'Read')",
            "$ids = [string[]]@(Get-Content $d -Encoding UTF8 | Where-Object { $_ })",
            "Write-Host 'Wer''s glaubt ?? ja'",
            "Invoke-RestMethod -Uri $u `\n    -Body $b",
        ]
        for code, funde in zip(gut, ps7_funde_code(gut)):
            with self.subTest(code):
                self.assertEqual(funde, [])
