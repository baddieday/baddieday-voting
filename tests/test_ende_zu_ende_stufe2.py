"""Ende-zu-Ende Stufe 2 der Lernschleife (Plan docs/superpowers/plans/2026-09-25-lernschleife-stufe-2.md, Paket F).

„Fertig, wenn (Spec §14): /gewichte zeigt beide Quoten (Nutzer, Publikum) und ein Clip mit Bot-Opfern bekommt
sichtbar weniger Punkte.“ Die Unit-Tests der Pakete A1–E prüfen jedes Stück für sich; hier wird gezeigt, dass die
Stücke zusammenpassen. Sechs Szenarien, je ein Test:

  1. Bot-Opfer: zwei künstliche Replays, die sich NUR in `eliminiert_bot` unterscheiden → prepare/analyze/decide/
     render mit echtem FFmpeg (Farbtest-Video) und gefälschtem replay2json → der Bot-Clip hat genau
     bot_opfer × 2 Punkte weniger (Startgewicht −2), die Begründung nennt „Bot-Opfer“.
  2. /gewichte zeigt beide Quoten – über das echte bot.app.cmd_gewichte mit gefälschtem Telegram.
  3. Kette: Posts → Messungen → `pipeline publikum bewerten` → ein Publikums-Paar → lernen.aktualisiere speichert
     eine neue Version (mit trefferquote_publikum und quellen) → die Erwartung eines neu gesendeten Clips rechnet
     mit den neuen Gewichten; die alte, festgeschriebene Erwartung bleibt, wie sie war.
  4. Migration: Datenbank im Stand vor Stufe 2 (gewichte-Zeile mit nur 5 Schlüsseln, mic_stand NULL) →
     db.verbinde → lernen.aktuelle füllt bot_opfer = −2 auf; `pipeline gewichte` läuft.
  5. Mic-Kindprozess: `pipeline --konfig <Testkonfig> render` im getrennten Betrieb → der (gefälschte) Popen-Aufruf
     trägt `--konfig <Testkonfig>` vor `stimmung`; stdout von render ist genau eine JSON-Zeile.
  6. `pipeline merkmale nachtragen` im getrennten Betrieb: Replay-Merkmale kommen aus sessions/<ID>/replay.json im
     Puffer, der zweite Lauf ändert nichts, nichts weckt pve-big.

Lernidee in Alltagssprache: Ein Ende-zu-Ende-Test ist eine Generalprobe. Die Bühne (Datenbank, Puffer, Konfig) ist
echt, nur was nach außen führt, ist Attrappe: Telegram, claude, das C#-Programm replay2json und der Mic-Kindprozess.

Deterministisch: feste Uhr (jetzt wird in allen beteiligten Modulen ersetzt), kein Netz, kein echtes claude.
Regisseur (Paket C) ist nicht Teil dieses Tests. Die „weckt nie“-Tests für merkmale, mikro und erwartung stehen
schon in tests/test_merkmale.py (Nachtragen.test_weckt_nie), tests/test_mikro.py (WecktNie) und
tests/test_erwartung.py (WecktNie) – hier kommt Szenario 6 als Ende-zu-Ende-Fassung dazu.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import tomllib
import unittest
from datetime import datetime, timedelta
from importlib import resources
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from clip_pipeline import cli, db, erwartung, konfig as konfig_modul, lernen, mikro, publikum, verarbeitung
from clip_pipeline.merkmale import REPLAY_MERKMALE
from clip_pipeline.vorbewertung import MERKMALE
from clip_pipeline.zeit import UTC, iso

from tests.hilfen import HAT_FFMPEG, MitSpeicher
from tests.test_ende_zu_ende import SID, mit_video_und_replay

try:  # /gewichte braucht python-telegram-bot
    from clip_pipeline.bot import app as bot_app
except ImportError:
    bot_app = None

ICH = "ICH"  # Epic-ID des eigenen Spielers in den künstlichen Replays
KEIN_PROGRAMM = "/nicht/vorhanden/claude"  # Sicherheitsnetz: ein vergessenes claude findet kein echtes Programm


# --- Hilfen --------------------------------------------------------------------------------------------------------

def elim(t_ms: int, opfer: str, bot: bool | None) -> dict:
    """Ein direkter Kill von mir (kein Umhauen) im Format von replay2json `eliminierungen`."""
    return {"t_ms": t_ms, "eliminator": ICH, "eliminiert": opfer, "knock": False, "selbst": False,
            "eliminiert_bot": bot, "waffe": None}


def replay_json(start: datetime, laenge_s: float, eliminierungen: list[dict], *, platz: int = 3) -> dict:
    """JSON, wie replay2json es liefert – nur die Felder, die match_aus_json liest."""
    return {"replay_start": iso(start), "replay_start_kind": "Utc", "laenge_ms": round(laenge_s * 1000),
            "ich_quelle": "konfig", "ich": {"epic_id": ICH, "platzierung": platz}, "spieler_gesamt": 100,
            "stats_eliminierungen": len(eliminierungen), "eliminierungen": eliminierungen}


def falsches_replay2json(ordner: Path, daten: dict) -> Path:
    """Ein ausführbares Programm, das statt des C#-Tools replay2json genau dieses JSON auf stdout schreibt.
    So läuft replay.lies_json echt (Unterprozess, Exit-Code, JSON-Prüfung) – nur der Inhalt ist erfunden."""
    ordner.mkdir(parents=True, exist_ok=True)
    (ordner / "replay.json").write_text(json.dumps(daten), encoding="utf-8")
    programm = ordner / "replay2json"
    programm.write_text(f"#!{sys.executable}\nimport pathlib, sys\n"
                        f"sys.stdout.write(pathlib.Path({str(ordner / 'replay.json')!r}).read_text())\n",
                        encoding="utf-8")
    programm.chmod(0o755)
    return programm


def toml_text(daten: dict) -> str:
    """Schreibt ein Konfig-Dict als TOML (die Standardbibliothek kann TOML nur lesen). Genug für pipeline.toml:
    Tabellen, Zahlen, Texte, Wahrheitswerte, Listen. Schlüssel immer in Anführungszeichen – das ist gültiges TOML."""
    def wert(v) -> str:
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (int, float)):
            return repr(v)
        if isinstance(v, str):
            return json.dumps(v)  # JSON-Texte sind gültige TOML-Basic-Strings
        if isinstance(v, list):
            return "[" + ", ".join(wert(x) for x in v) + "]"
        if isinstance(v, dict):
            return "{" + ", ".join(f"{json.dumps(k)} = {wert(x)}" for k, x in v.items()) + "}"
        raise TypeError(f"{type(v).__name__} lässt sich nicht als TOML schreiben")

    zeilen: list[str] = []

    def tabelle(pfad: list[str], d: dict) -> None:
        if pfad:
            zeilen.append("[" + ".".join(json.dumps(k) for k in pfad) + "]")
        zeilen.extend(f"{json.dumps(k)} = {wert(v)}" for k, v in d.items() if not isinstance(v, dict))
        for k, v in d.items():
            if isinstance(v, dict):
                tabelle(pfad + [k], v)

    tabelle([], daten)
    return "\n".join(zeilen) + "\n"


def umgebung(test: unittest.TestCase) -> MitSpeicher:
    """Ein eigener, leerer Speicher mit eigener Datenbank (MitSpeicher als Helfer, nicht als Test) – für Szenarien,
    die zwei unabhängige Welten brauchen (mit und ohne Bot-Opfer)."""
    welt = MitSpeicher()
    welt.setUp()
    test.addCleanup(welt.tearDown)
    welt.konfig.daten["decide"].update(claude=False, programm=KEIN_PROGRAMM)  # Regel-Schnittliste, nie claude
    return welt


def match_bis_decide(welt: MitSpeicher, *, bot: bool | None) -> list[datetime]:
    """Nvidia-Highlight (20 s) + Replay mit zwei Kills (Sekunde 8 und 11 im Video) → prepare, analyze, decide.
    Die Opfer sind je nach `bot` Bots oder Menschen – sonst ist alles gleich. Gibt die Kill-Zeitpunkte zurück."""
    kills = mit_video_und_replay(welt)
    start = datetime(2026, 9, 21, 19, 42, 22, tzinfo=UTC)  # = Zeitstempel im Replay-Namen (SID), Ortszeit 21:42:22
    t_ms = [round((k - start).total_seconds() * 1000) for k in kills]
    programm = falsches_replay2json(welt.tmp / "replay2json", replay_json(
        start, 15 * 60, [elim(t_ms[0], "A", bot), elim(t_ms[1], "B", bot)]))
    welt.konfig.daten["replay"].update(programm=str(programm), ich=ICH)
    verarbeitung.prepare(welt.con, welt.konfig, SID)
    analyse = verarbeitung.analyze(welt.con, welt.konfig, SID)
    welt.assertEqual((analyse["kills"], analyse["kandidaten"], analyse["kill_quelle"]), (2, 1, "replay"))
    welt.assertEqual(verarbeitung.decide(welt.con, welt.konfig, SID)["entschieden_von"], "regel")
    return kills


# --- 1. Fertig-Kriterium: Bot-Opfer kosten sichtbar Punkte -----------------------------------------------------------

@unittest.skipUnless(HAT_FFMPEG, "ffmpeg fehlt")
class BotOpfer(unittest.TestCase):
    def durchlauf(self, bot: bool) -> sqlite3.Row:
        welt = umgebung(self)
        match_bis_decide(welt, bot=bot)
        ergebnis = verarbeitung.render(welt.con, welt.konfig, SID)
        self.assertEqual((ergebnis["clips"], ergebnis["neu"], ergebnis["top_label"]), (1, 1, "Double Kill"))
        return welt.con.execute("SELECT * FROM clips").fetchone()

    def test_bot_clip_hat_bot_opfer_mal_zwei_weniger(self):
        mensch, bot = self.durchlauf(False), self.durchlauf(True)
        mk_mensch, mk_bot = json.loads(mensch["merkmale"]), json.loads(bot["merkmale"])
        # Die Replays unterscheiden sich nur in eliminiert_bot: alle anderen Merkmale sind gleich (auch die
        # gemessene Lautstärke – dasselbe Testbild), nur bot_opfer ist 1,0 statt 0,0
        self.assertEqual((mk_mensch["bot_opfer"], mk_bot["bot_opfer"]), (0.0, 1.0))
        self.assertEqual({m: v for m, v in mk_bot.items() if m != "bot_opfer"},
                         {m: v for m, v in mk_mensch.items() if m != "bot_opfer"})
        # Befund S-3: [merkmale.waffen] ist noch nicht kalibriert (leere Listen) → sniper/nahkampf unbekannt (fehlen),
        # die übrigen fünf Replay-Merkmale sind gemessen
        self.assertEqual(set(REPLAY_MERKMALE) - set(mk_bot), {"sniper", "nahkampf"})
        self.assertEqual((mk_bot["kill_punkte"], mk_bot["platzierung"]), (3.0, 1 / 3))
        # Fertig-Kriterium: Punkte-Differenz = bot_opfer × 2 (Startgewicht −2), render hat sie eingefroren
        startgewicht = lernen.startgewichte(konfig_modul.lade())["bot_opfer"]
        self.assertEqual(startgewicht, -2.0)
        self.assertAlmostEqual(mensch["punkte"] - bot["punkte"], -startgewicht * mk_bot["bot_opfer"], places=2)
        self.assertIn("Bot-Opfer 1,00 × −2,00 = −2,0", bot["begruendung"])
        self.assertNotIn("Bot-Opfer", mensch["begruendung"])  # Merkmal 0 steht nicht im Text
        self.assertEqual((mensch["gewichte_version"], bot["gewichte_version"]), (0, 0))  # Startgewichte


# --- 2. und 3.: Publikum → Paar → neue Gewichte → Erwartung ----------------------------------------------------------

P0 = datetime(2026, 9, 22, 16, 0, tzinfo=UTC)   # erster Post (18:00 Berlin), danach je eine Stunde später
BEWERTEN = P0 + timedelta(days=8)               # Timer clip-publikum: alle sieben Posts sind älter als 7 Tage
ABEND = datetime(2026, 9, 21, 19, 0, tzinfo=UTC)  # alle Clips vom selben Spielabend (Freigabe-Paare je Abend)

# Merkmale (alte fünf – so bleibt die Rechnung im Kopf nachvollziehbar):
GUT = {"kill_punkte": 1.0, "victory_royale": 0.0, "laenge": 0.0, "lautstaerke": 0.5, "kommentar": 0.0}
DOUBLE = dict(GUT, kill_punkte=3.0)  # der Clip, den das Publikum mag
SCHLECHT = dict(GUT, lautstaerke=0.0)
# Messungen am Tag 7 (views, likes, Ø Wiedergabe in s; Clip 20 s ohne Endcard). Die ersten fünf sind die
# Vergleichsbasis (Score 0, „Basis zu klein“), Post 6 ist stark, Post 7 schwach – beide mit echtem Score.
MESSUNGEN = [(1000, 50, 8.0), (1200, 60, 9.0), (1400, 70, 10.0), (1600, 80, 11.0), (1800, 90, 12.0),
             (5000, 500, 18.0), (200, 2, 2.0)]


@unittest.skipIf(bot_app is None, "python-telegram-bot fehlt")
class PublikumsKette(MitSpeicher):
    """Sieben veröffentlichte Clips mit Posts auf TikTok, 13 verworfene Clips desselben Abends (20 Urteile = genau
    [lernen].mindestens), alle im Clip-Bot gesendet. Getrennter Betrieb, eine Uhr, nichts weckt."""

    def setUp(self):
        super().setUp()
        k = self.konfig.daten
        # Feste Werte, damit der Test nicht von config/lokal.toml abhängt
        k["publikum"].update(plattformen=["tiktok"], alter_tage=7, mindest_alter_tage=3, fenster=20,
                             paar_abstand=0.5, max_paare=200)
        k["publikum"]["gewichte"] = {"wiedergabe": 0.5, "engagement": 0.3, "reichweite": 0.2}
        k["lernen"].update(mindestens=20, voll_vertrauen=60, schritt=0.05, durchlaeufe=5, leine_anteil=0.5,
                           leine_minimum=0.5, max_paare_pro_abend=20, gewicht_freigabe=0.5, mindest_publikum_paare=10)
        k["erwartung"].update(mindest_urteile=10, referenz=50)
        k["shorts"]["endcard"] = False  # Post-Dauer = Clip-Länge 20 s
        k["decide"]["programm"] = KEIN_PROGRAMM
        (self.konfig.wurzel / ".clip-puffer").touch()
        self.lager = self.tmp / "lager-auf-pve-big"
        k["lager"]["wurzel"] = str(self.lager)
        k["speicher"].update(host="pve-gross", wol_mac="aa:bb:cc:dd:ee:ff", wecken_warten_s=0)
        self.uhr = P0
        self.wol = mock.Mock()
        patches = [mock.patch.object(konfig_modul.Konfig, "_host_erreichbar", return_value=False),
                   mock.patch("clip_pipeline.konfig.sende_wake_on_lan", self.wol),
                   mock.patch("socket.create_connection", side_effect=AssertionError("Netzwerkzugriff im Test")),
                   mock.patch.object(cli, "lade", return_value=self.konfig)]
        for modul in (cli, db, lernen, erwartung, publikum, bot_app):
            patches.append(mock.patch.object(modul, "jetzt", side_effect=lambda: self.uhr))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.gute = [self.clip("veroeffentlicht", DOUBLE if i == 5 else GUT, i) for i in range(7)]
        self.schlechte = [self.clip("verworfen", SCHLECHT, 7 + i) for i in range(13)]

    def clip(self, status: str, mk: dict, minute: int) -> int:
        cid = self.clip_anlegen(status=status, start=ABEND + timedelta(minutes=minute), merkmale=dict(mk),
                                match_id="2026-09-21_21-00-00")
        self.con.execute("UPDATE clips SET tg_nachricht_id = ? WHERE id = ?", (1000 + cid, cid))  # im Bot gesendet
        return cid

    def posts_und_messungen(self) -> list[int]:
        """Je guter Clip ein TikTok-Post (eine Stunde auseinander) und eine Messung am Tag 7 (Screenshot)."""
        post_ids = []
        for i, (cid, (views, likes, wiedergabe)) in enumerate(zip(self.gute, MESSUNGEN)):
            gepostet = P0 + timedelta(hours=i)
            post_id, neu = publikum.post_anlegen(self.con, art="clip", ziel_id=cid, plattform="tiktok",
                                                 daten=publikum.clip_post_daten(self.con, self.konfig, cid),
                                                 zeit=gepostet)
            self.assertTrue(neu)
            publikum.speichere_messung(self.con, post_id, {"views": views, "likes": likes, "kommentare": 0,
                                                           "shares": 0, "saves": 0, "wiedergabe_s": wiedergabe},
                                       "screenshot", zeit=gepostet + timedelta(days=7))
            post_ids.append(post_id)
        return post_ids

    def pipeline(self, *argv: str) -> tuple[int, dict, list[str]]:
        aus = io.StringIO()
        with contextlib.redirect_stdout(aus), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(list(argv))
        zeilen = aus.getvalue().strip().splitlines()
        return code, json.loads(zeilen[-1]), zeilen

    def bewerten(self, post_ids: list[int]) -> None:
        """`pipeline publikum bewerten` wie vom Timer: 5 × Basis zu klein, dann zwei echte Scores weit auseinander."""
        self.uhr = BEWERTEN
        code, ergebnis, zeilen = self.pipeline("publikum", "bewerten")
        self.assertEqual((code, len(zeilen), ergebnis["bewertet"], ergebnis["fehler"]), (0, 1, 7, 0))
        posts = [publikum.post(self.con, p) for p in post_ids]
        vermerke = [json.loads(p["score_teile"])["vermerke"] for p in posts]
        self.assertEqual(vermerke[:5], [["Basis zu klein"]] * 5)
        self.assertEqual(vermerke[5:], [[], []])  # echte Scores
        self.assertGreaterEqual(posts[5]["score"] - posts[6]["score"], 0.5)  # ≥ [publikum].paar_abstand
        (paar,) = lernen.publikum_paare(self.con, self.konfig)
        self.assertEqual((paar.besser["kill_punkte"], paar.schlechter["kill_punkte"]), (3.0, 1.0))

    def test_gewichte_zeigt_beide_quoten(self):
        self.bewerten(self.posts_und_messungen())
        antworten = []

        async def reply_text(text, **_):
            antworten.append(text)

        update = SimpleNamespace(effective_message=SimpleNamespace(reply_text=reply_text))
        context = SimpleNamespace(bot_data={"con": self.con, "konfig": self.konfig, "erlaubt": 42})
        asyncio.run(bot_app.cmd_gewichte(update, context))
        (text,) = antworten
        zeilen = text.splitlines()
        self.assertIn("Sortier-Quote du: 100 % (Start 100 %)", zeilen)
        self.assertIn("Sortier-Quote Publikum: 100 % (Start 100 %) – 1 Paar, zählt für die Schranke erst ab 10",
                      zeilen)
        self.assertIn("Paare: 0 Battles · 20 Freigaben · 1 Publikum", zeilen)
        # 20 Urteile + die 2 Posts des Paars; Version 1 hat schon `publikum bewerten` gespeichert
        self.assertIn("Datenbasis: 22 Bewertungen (20 Freigaben/Verwerfungen, 0 Battles, 2 Posts)", zeilen)
        self.assertIn("(Version 1)", zeilen[0])
        self.wol.assert_not_called()

    def test_kette_bis_zur_geaenderten_erwartung(self):
        # Vorher: ein neuer Clip wird gesendet – die Erwartung rechnet mit den Startgewichten (Version 0)
        vorher_clip = self.clip("gesendet", GUT, 30)
        p_vorher = erwartung.festschreiben(self.con, self.konfig, "clip", vorher_clip)
        self.assertIsNotNone(p_vorher)  # 20 Urteile ≥ [erwartung].mindest_urteile
        grundlage = json.loads(self.con.execute("SELECT grundlage FROM erwartungen WHERE ziel_id = ?",
                                                (vorher_clip,)).fetchone()[0])
        self.assertEqual((grundlage["gewichte_version"], grundlage["score"]), (0, 1.5))  # 1 + 0,5 × 1

        # Post → Messungen → publikum bewerten (lernt gleich mit, Annahme S2-A12)
        self.bewerten(self.posts_und_messungen())
        zeile = self.con.execute("SELECT * FROM gewichte ORDER BY version DESC").fetchone()
        self.assertEqual(zeile["version"], 1)
        self.assertEqual((zeile["trefferquote_publikum"], zeile["trefferquote_publikum_start"]), (1.0, 1.0))
        self.assertEqual(json.loads(zeile["quellen"]), {"battle": 0, "freigabe": 20, "publikum": 1})
        self.assertEqual(zeile["datenbasis"], 22)
        version, neu = lernen.aktuelle(self.con, self.konfig)
        self.assertEqual((version, set(neu)), (1, set(MERKMALE)))
        # Freigaben mögen Lautstärke (0,5 Vorsprung < Marge 1): das Gewicht wächst bis an die Leine (1,5), mal
        # Vertrauen 22/60 → 1 + 0,3667 × 0,5 = 1,1833. Kill-Punkte sortieren das Publikums-Paar schon (Vorsprung 2)
        self.assertAlmostEqual(neu["lautstaerke"], 1.1833, places=4)
        self.assertEqual(neu["kill_punkte"], 1.0)
        # Aufruf noch einmal: nichts geändert → dieselbe Version
        self.assertEqual(lernen.aktualisiere(self.con, self.konfig)[0], 1)

        # Danach: derselbe Moment hat mit den neuen Gewichten einen anderen Score …
        start = lernen.startgewichte(self.konfig)
        nachher_clip = self.clip("gesendet", GUT, 31)
        self.assertEqual(erwartung.moment_score(self.con, self.konfig, "clip", nachher_clip, start), 1.5)
        self.assertAlmostEqual(erwartung.moment_score(self.con, self.konfig, "clip", nachher_clip, neu), 1.59165)
        # … und die Erwartung beim Senden rechnet mit Version 1 – eine andere Zahl als vorher
        self.uhr = BEWERTEN + timedelta(hours=1)
        p_nachher = erwartung.festschreiben(self.con, self.konfig, "clip", nachher_clip)
        grundlage = json.loads(self.con.execute("SELECT grundlage FROM erwartungen WHERE ziel_id = ?",
                                                (nachher_clip,)).fetchone()[0])
        self.assertEqual(grundlage["gewichte_version"], 1)
        self.assertAlmostEqual(grundlage["score"], 1.59165)
        self.assertNotAlmostEqual(p_nachher, p_vorher, places=4)
        # Festgeschrieben heißt festgeschrieben: die alte Erwartung wird nie neu gerechnet
        self.assertEqual(erwartung.festschreiben(self.con, self.konfig, "clip", vorher_clip), p_vorher)
        self.wol.assert_not_called()
        self.assertFalse(self.lager.exists())


# --- 4. Migration auf einer Datenbank vor Stufe 2 --------------------------------------------------------------------

ALTE_GEWICHTE = {"kill_punkte": 1.2, "victory_royale": 5.0, "laenge": -0.5, "lautstaerke": 1.3, "kommentar": 1.0}


class Migration(MitSpeicher):
    def setUp(self):
        super().setUp()
        self.con.close()
        self.pfad = self.tmp / "vor-stufe2.db"
        self.konfig.daten["datenbank"]["pfad"] = str(self.pfad)
        self.alte_datenbank()

    def alte_datenbank(self) -> None:
        """Stand nach Stufe 1, vor Stufe 2: alle .sql-Dateien und die Stufe-1-Spalten (mic_stand, rezept,
        upload_pfad …), aber gewichte OHNE die drei neuen Spalten. Absichtlich mit sqlite3 direkt statt db.verbinde
        (das würde sofort migrieren)."""
        alt = sqlite3.connect(self.pfad)
        for datei in ("schema.sql", "regie.sql", "lager.sql", "publikum.sql"):
            alt.executescript(resources.files("clip_pipeline").joinpath(datei).read_text(encoding="utf-8"))
        for tabelle, spalte, typ in db.MIGRATIONEN:
            if tabelle != "gewichte" and spalte not in {z[1] for z in alt.execute(f"PRAGMA table_info({tabelle})")}:
                alt.execute(f"ALTER TABLE {tabelle} ADD COLUMN {spalte} {typ}")
        alt.execute("INSERT INTO gewichte (version, werte, datenbasis, vertrauen, trefferquote, trefferquote_start,"
                    " erstellt) VALUES (3, ?, 25, 0.42, 0.8, 0.7, '2026-09-20T10:00:00.000Z')",
                    (json.dumps(ALTE_GEWICHTE),))
        alt.execute("INSERT INTO matches (id, replay_pfad, start_utc, ende_utc, erstellt, geaendert) VALUES"
                    " ('m1', 'replays/m1.replay', 'x', 'x', 'x', 'x')")
        alt.execute("INSERT INTO clips (match_id, nr, status, titel, typ, kills, max_gruppe, kill_zeiten, start_utc,"
                    " ende_utc, quelle_pfad, quelle_start_s, quelle_ende_s, merkmale, punkte, begruendung, erstellt,"
                    " geaendert) VALUES ('m1', 1, 'gesendet', 'Double Kill', 'double', 2, 2, '[]',"
                    " '2026-09-20T19:00:00.000Z', '2026-09-20T19:00:20.000Z', 'eingang/x.mp4', 0, 20, ?, 3.0,"
                    " 'Double Kill 3,0', 'x', 'x')", (json.dumps(dict(ALTE_GEWICHTE, kill_punkte=3.0)),))
        alt.commit()
        alt.close()

    def test_alte_version_wird_aufgefuellt_und_gewichte_laeuft(self):
        spalten_alt = {z[1] for z in sqlite3.connect(self.pfad).execute("PRAGMA table_info(gewichte)")}
        self.assertNotIn("quellen", spalten_alt)  # wirklich der alte Stand

        self.con = db.verbinde(self.pfad)
        self.assertLessEqual({"trefferquote_publikum", "trefferquote_publikum_start", "quellen"},
                             {z["name"] for z in self.con.execute("PRAGMA table_info(gewichte)")})
        self.assertIsNone(self.con.execute("SELECT mic_stand FROM clips").fetchone()[0])
        version, gewichte = lernen.aktuelle(self.con, self.konfig)
        self.assertEqual(version, 3)
        self.assertEqual(set(gewichte), set(MERKMALE))  # 17 statt 5
        self.assertEqual(gewichte["bot_opfer"], -2.0)   # Startgewicht aufgefüllt
        self.assertEqual({m: gewichte[m] for m in ALTE_GEWICHTE}, ALTE_GEWICHTE)  # gespeicherte bleiben

        umgebung_ = {"CLIP_SPEICHER": str(self.konfig.wurzel), "CLIP_DATENBANK": str(self.pfad)}
        for argv, gespeichert in ((["gewichte"], None), (["gewichte", "--neu"], 4)):
            with self.subTest(argv=argv):
                aus = io.StringIO()
                with mock.patch.object(cli, "lade", return_value=self.konfig), mock.patch.dict(os.environ, umgebung_), \
                        contextlib.redirect_stdout(aus), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(cli.main(argv), 0)
                zeilen = aus.getvalue().splitlines()
                self.assertTrue(any(z.startswith("Bot-Opfer") and z.split()[-2:] == ["−2,00", "−2,00"]
                                    for z in zeilen), zeilen)
                self.assertIn("Publikum: noch keine Paare", zeilen)
                self.assertIn("ohne Mic-Analyse: 1 Clip", zeilen)  # mic_stand NULL, nicht verworfen
                if gespeichert:
                    self.assertIn(f"Gespeichert als Version {gespeichert}", zeilen)
        # --neu speichert auf der migrierten Tabelle mit den neuen Spalten
        zeile = self.con.execute("SELECT * FROM gewichte WHERE version = 4").fetchone()
        self.assertEqual(json.loads(zeile["quellen"]), {"battle": 0, "freigabe": 0, "publikum": 0})
        self.assertIsNone(zeile["trefferquote_publikum"])


# --- 5. Mic-Kindprozess bekommt die Testkonfig -------------------------------------------------------------------

@unittest.skipUnless(HAT_FFMPEG, "ffmpeg fehlt")
class MigrationGleichzeitig(MitSpeicher):
    """Befund B-2: Zwei Prozesse (z. B. beide Bots nach dem Neustart) verbinden gleichzeitig mit einer alten Datenbank.
    Beide sehen die Spalte als fehlend, einer ist beim ALTER schneller – der andere darf daran nicht scheitern.
    Deterministisch nachgestellt: Vor jedem ALTER führt eine zweite Verbindung genau dieses ALTER zuerst aus."""

    def verbinde_mit_wettlauf(self, pfad: Path, fehler_statt_alter: str | None = None) -> sqlite3.Connection:
        echt = sqlite3.connect

        class Schneller(sqlite3.Connection):
            def execute(self, sql, *args):
                if sql.startswith("ALTER TABLE"):
                    if fehler_statt_alter is not None:
                        raise sqlite3.OperationalError(fehler_statt_alter)
                    anderer = echt(pfad, isolation_level=None)
                    try:
                        anderer.execute(sql)  # der andere Prozess war schneller
                    finally:
                        anderer.close()
                return super().execute(sql, *args)

        def verbinde(*args, **kwargs):
            return echt(*args, factory=Schneller, **kwargs)

        with mock.patch.object(db.sqlite3, "connect", side_effect=verbinde):
            return db.verbinde(pfad)

    def test_spalte_schon_da_wird_geschluckt(self):
        pfad = self.tmp / "wettlauf.db"
        con = self.verbinde_mit_wettlauf(pfad)  # frische Datei: jede Stufe-1/2-Spalte kommt per ALTER
        try:
            spalten = {z["name"] for z in con.execute("PRAGMA table_info(gewichte)")}
            self.assertLessEqual({"trefferquote_publikum", "quellen"}, spalten)
            self.assertIn("mic_stand", {z["name"] for z in con.execute("PRAGMA table_info(clips)")})
        finally:
            con.close()

    def test_anderer_fehler_fliegt_weiter(self):
        with self.assertRaisesRegex(sqlite3.OperationalError, "disk I/O error"):
            self.verbinde_mit_wettlauf(self.tmp / "kaputt.db", fehler_statt_alter="disk I/O error")


class MicKindprozess(unittest.TestCase):
    def test_render_startet_mic_schritt_mit_testkonfig(self):
        welt = umgebung(self)
        welt.konfig.daten["lager"]["wurzel"] = str(welt.tmp / "lager")  # getrennter Betrieb (Puffer)
        welt.konfig.daten["merkmale"].update(mic=True, mic_je_lauf=3)
        match_bis_decide(welt, bot=True)
        # Die Testkonfig als echte Datei: render liest sie über `pipeline --konfig …` selbst (cli.lade)
        testkonfig = welt.tmp / "konfig" / "test.toml"
        testkonfig.parent.mkdir()
        testkonfig.write_text(toml_text(welt.konfig.daten), encoding="utf-8")
        self.assertEqual(tomllib.loads(testkonfig.read_text(encoding="utf-8")), welt.konfig.daten)

        echt = subprocess.Popen
        mic_aufrufe = []

        def popen(befehl, *args, **kwargs):
            # Nur der Mic-Schritt ist gefälscht; ffmpeg/ffprobe laufen echt weiter
            if befehl[:1] == ["nice"]:
                mic_aufrufe.append((befehl, kwargs))
                return mock.Mock()
            return echt(befehl, *args, **kwargs)

        aus = io.StringIO()
        ohne_umgebung = {k: v for k, v in os.environ.items()
                         if k not in ("CLIP_KONFIG", "CLIP_SPEICHER", "CLIP_DATENBANK", "CLIP_EPIC_ID")}
        with mock.patch.dict(os.environ, ohne_umgebung, clear=True), \
                mock.patch("subprocess.Popen", side_effect=popen), \
                mock.patch.object(mikro, "whisper_da", return_value=True), \
                mock.patch.object(konfig_modul.Konfig, "_host_erreichbar", return_value=True), \
                mock.patch("clip_pipeline.konfig.sende_wake_on_lan") as wol, \
                contextlib.redirect_stdout(aus), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(["--konfig", str(testkonfig), "render", "--session", SID])
        self.assertEqual(code, 0)
        zeilen = aus.getvalue().splitlines()
        self.assertEqual(len(zeilen), 1, zeilen)  # n8n-Vertrag: genau eine JSON-Zeile
        ergebnis = json.loads(zeilen[0])
        self.assertEqual((ergebnis["clips"], ergebnis["neu"], ergebnis["top_label"]), (1, 1, "Double Kill"))

        (befehl, kwargs), = mic_aufrufe
        self.assertEqual(befehl[befehl.index("--konfig") + 1], str(testkonfig))
        self.assertLess(befehl.index("--konfig"), befehl.index("stimmung"))
        self.assertEqual(befehl[befehl.index("stimmung"):],
                         ["stimmung", "--clips", "--session", SID, "--max", "3"])
        self.assertEqual((kwargs["stdout"], kwargs["start_new_session"]), (subprocess.DEVNULL, True))
        self.assertTrue((welt.tmp / mikro.LOG_NAME).is_file())  # Log neben der Test-DB
        wol.assert_not_called()


# --- 6. merkmale nachtragen im getrennten Betrieb -----------------------------------------------------------------

class MerkmaleNachtragen(MitSpeicher):
    SID = "2026-09-21_20-50-00"
    START = datetime(2026, 9, 21, 18, 50, tzinfo=UTC)  # Replay-Start (UTC)
    KILL = datetime(2026, 9, 21, 19, 0, 10, tzinfo=UTC)  # Kill-Zeit des Clips (clip_anlegen: Start + 10 s)

    def setUp(self):
        super().setUp()
        self.konfig.daten["lager"]["wurzel"] = str(self.tmp / "lager")  # getrennter Betrieb
        self.konfig.daten["merkmale"]["waffen"] = {"sniper": [], "nahkampf": [], "sonstige": []}
        self.konfig.daten["speicher"].update(host="pve-gross", wol_mac="aa:bb:cc:dd:ee:ff", wecken_warten_s=0)
        alte = {"kill_punkte": 1.0, "victory_royale": 0.0, "laenge": 0.0, "lautstaerke": 0.0, "kommentar": 0.0}
        self.cid = self.clip_anlegen(status="vorbewertet", start=datetime(2026, 9, 21, 19, 0, tzinfo=UTC),
                                     match_id=self.SID, merkmale=alte)
        ordner = self.konfig.ordner("sessions") / self.SID
        ordner.mkdir(parents=True)
        t_ms = round((self.KILL - self.START).total_seconds() * 1000)
        (ordner / "replay.json").write_text(json.dumps(replay_json(self.START, 20 * 60, [elim(t_ms, "A", True)],
                                                                   platz=4)), encoding="utf-8")

    def pipeline(self, *argv: str) -> tuple[int, dict, list[str]]:
        aus = io.StringIO()
        with mock.patch.object(cli, "lade", return_value=self.konfig), \
                contextlib.redirect_stdout(aus), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(list(argv))
        zeilen = aus.getvalue().strip().splitlines()
        return code, json.loads(zeilen[-1]), zeilen

    def test_nachtragen_zweimal_weckt_nie(self):
        with mock.patch.object(konfig_modul.Konfig, "_host_erreichbar", return_value=False) as host, \
                mock.patch("clip_pipeline.konfig.sende_wake_on_lan") as wol:
            code, ergebnis, zeilen = self.pipeline("merkmale", "nachtragen")
            self.assertEqual((code, len(zeilen)), (0, 1))
            self.assertEqual(ergebnis, {"replay": {"clips": 1, "geaendert": 1, "ohne_replay": 0, "waffen_gemeldet": []},
                                        "mic": {"clips": 0, "geaendert": 0}})
            zeile = dict(db.clip(self.con, self.cid))
            mk = json.loads(zeile["merkmale"])
            self.assertEqual(set(REPLAY_MERKMALE) - set(mk), {"sniper", "nahkampf"})  # Befund S-3: Listen leer
            self.assertEqual((mk["bot_opfer"], mk["platzierung"]), (1.0, 0.25))
            # 1 Kill + Platz 0,25 × 2 − Bot 1 × 2 + Phase (10 min 10 s von 20 min ≈ 0,508) × 0,5 ≈ −0,25
            self.assertAlmostEqual(zeile["punkte"], 1 + 0.5 - 2 + 0.5 * mk["phase"], delta=0.006)
            self.assertIn("Bot-Opfer 1,00 × −2,00 = −2,0", zeile["begruendung"])
            self.assertEqual(zeile["gewichte_version"], 0)

            code, ergebnis, _ = self.pipeline("merkmale", "nachtragen")
            self.assertEqual(code, 0)
            # Befund S-3: sniper/nahkampf fehlen noch (Listen leer) → der Clip zählt weiter, geändert wird nichts
            self.assertEqual(ergebnis["replay"], {"clips": 1, "geaendert": 0, "ohne_replay": 0, "waffen_gemeldet": []})
            self.assertEqual(dict(db.clip(self.con, self.cid)), zeile)  # zweiter Lauf ändert nichts
        wol.assert_not_called()
        host.assert_not_called()
        self.assertFalse((self.tmp / "lager").exists())  # das Lager wird nie angefasst

    def test_ohne_getrennten_betrieb_exit_2(self):
        self.konfig.daten["lager"]["wurzel"] = ""
        code, ergebnis, _ = self.pipeline("merkmale", "nachtragen")
        self.assertEqual((code, ergebnis["fehler"]), (2, "konfig"))
        self.assertNotIn("bot_opfer", json.loads(db.clip(self.con, self.cid)["merkmale"]))

    def test_ungueltige_session_id(self):
        code, ergebnis, _ = self.pipeline("merkmale", "nachtragen", "--session", "../etc")
        self.assertEqual(code, 1)
        self.assertIn("Ungültige Session-ID", ergebnis["fehler"])


if __name__ == "__main__":
    unittest.main()
