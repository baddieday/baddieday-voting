"""🥱 = der Schnitt langweilt (07.10., Florian: „Ich tippe ❌ → 🥱 und sehe das gleiche Video mit anderen Schnitten“):
dieselbe Szene unter mehreren Schlüsseln (szenen.py), die neue Fassung ist anders geschnitten, behält die stärkere
Hälfte und tauscht die schwächere gegen neue Szenen – erst vom Abend, dann starke ungesehene früherer Abende."""

import asyncio
import json
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from clip_pipeline import regeln, stile, szenen
from clip_pipeline.zeit import iso, jetzt

from tests.hilfen import HAT_FFMPEG, MitSpeicher
from tests.regie_hilfen import MitRegieMaterial
from tests.test_stufe1 import FakeBot, FakeQuery

try:
    from clip_pipeline import lernbot
except ImportError:  # python-telegram-bot fehlt
    lernbot = None

# (Stimmung, Kills in Serie, Kill-Sekunden, Match) – a1/a2 = der Abend, f1 = ein früherer Abend
SZENEN = [("episch", 4, [6.0, 8.0, 10.0, 12.5], "a1"), ("episch", 3, [5.0, 7.0, 9.0], "a2"),      # 1, 2 stark
          ("spannend", 2, [7.0, 9.5], "a1"), ("spannend", 2, [6.0, 10.0], "a2"),                    # 3, 4 schwächer
          ("episch", 2, [5.0, 7.0], "a2"),                                                          # 5 = Szene von 2
          ("spannend", 1, [8.0], "a1"), ("lustig", 1, [9.0], "a2"), ("spannend", 1, [7.0], "a1"),   # 6–8 ungesehen
          ("episch", 3, [5.0, 7.0, 9.0], "f1"), ("spannend", 2, [8.0, 12.0], "f1"),                 # 9 gezeigt, 10 neu
          ("spannend", 1, [11.0], "f1")]                                                            # 11 schwach
START = ["2026-10-06T20:00:00Z", "2026-10-06T20:30:00Z", "2026-10-06T20:05:00Z", "2026-10-06T20:35:00Z",
         "2026-10-06T20:30:03Z", "2026-10-06T20:10:00Z", "2026-10-06T20:40:00Z", "2026-10-06T20:15:00Z",
         "2026-10-01T20:00:00Z", "2026-10-01T20:10:00Z", "2026-10-01T20:20:00Z"]


class Szene(MitSpeicher):
    def moment(self, schluessel, start, laenge=20.0, match=None):
        self.con.execute("INSERT INTO momente (schluessel, match_id, datei, start_s, ende_s, start_utc, kills, stimmung, "
                         "sicherheit, quelle, merkmale, erstellt, geaendert) VALUES (?, ?, '/x.mp4', 0, ?, ?, 1, "
                         "'episch', 0.8, 'regel', '{}', 'x', 'x')", (schluessel, match, laenge, start))

    def test_dieselbe_szene_und_je_szene_einer(self):
        self.moment("clip:1", "2026-10-06T20:00:00Z", match="a1")
        self.moment("datei:1", "2026-10-06T20:00:16Z")          # 4 s Überlappung: dieselbe Szene
        self.moment("datei:2", "2026-10-06T20:00:34Z")          # 2 s mit datei:1 (10 %): eine andere
        self.moment("clip:2", "2026-10-06T20:00:16Z", match="a1")   # zwei Clips eines Matches: nie dieselbe
        self.moment("datei:3", None)                            # ohne Zeit: nur der eigene Schlüssel
        idx = szenen.index(self.con)
        self.assertEqual(idx, {"clip:1": {"datei:1"}, "datei:1": {"clip:1", "clip:2"}, "clip:2": {"datei:1"}})
        k = lambda s, punkte, clip=None: SimpleNamespace(schluessel=s, punkte=punkte, clip_id=clip, fail=False)  # noqa: E731
        liste = [k("datei:1", 5.0), k("clip:1", 5.0, clip=1), k("clip:2", 1.0, clip=2), k("datei:3", 0.5)]
        # Gleichstand: der mit Clip; nicht transitiv – clip:2 ist eine andere Szene als clip:1
        self.assertEqual([x.schluessel for x in szenen.eine_je_szene(liste, idx)], ["clip:1", "clip:2", "datei:3"])
        self.assertEqual([x.schluessel for x in szenen.eine_je_szene(liste, idx, {"datei:1"})], ["datei:1", "datei:3"])

    def video(self, momente, status="gesendet", *, verworfen=None, parameter="{}", erstellt="x") -> int:
        """Ein Entwurf mit diesen Momenten (Schnittliste im Testordner)."""
        pfad = self.tmp / f"v{self.con.execute('SELECT COUNT(*) FROM entwuerfe').fetchone()[0]}.json"
        pfad.write_text(json.dumps({"segmente": [{"moment": m} for m in momente]}), encoding="utf-8")
        return self.con.execute("INSERT INTO entwuerfe (name, format, schnittliste, parameter, status, erstellt, "
                                "auto_verworfen) VALUES (?, 'short', ?, ?, ?, ?, ?)",
                                (pfad.stem, str(pfad), parameter, status, erstellt, verworfen)).lastrowid

    def test_verlauf_je_video(self):
        """08.10. (Abwechslung mit Ermüdung): Fassungen eines Videos zählen als ein Video; gesperrt sind die Szenen der
        letzten n Videos, soweit sie höchstens 48 h alt sind (Prüfung 08.10.: nach einer Pause sperrte sonst ein altes
        Video), Einsätze zählen nur Videos der letzten Tage, dieselbe Szene unter zwei Schlüsseln ist eine. Für eine neue
        Fassung zählt das ersetzte Video nirgends – seine Szenen sind keine Wiederholung und frei, außer ein ANDERES
        deiner letzten Videos hat sie gerade erst gezeigt."""
        self.moment("clip:1", "2026-10-06T20:00:00Z", match="a1")
        self.moment("datei:1", "2026-10-06T20:00:16Z")                  # dieselbe Szene wie clip:1
        alt = self.video(["clip:1", "clip:2"], erstellt=iso(jetzt() - timedelta(days=40)))
        v1 = self.video(["clip:3", "datei:1"], erstellt=iso(jetzt()))
        self.video(["clip:4"])                                            # unlesbare Zeit: zählt als neu
        f1 = self.video(["clip:3", "clip:5"], parameter=json.dumps({"ersetzt": [v1]}), erstellt=iso(jetzt()))
        idx = szenen.index(self.con)
        v = szenen.verlauf(self.con, idx, tage=30, sperre=2)              # v1 + f1 ist ein Video, dann v2
        self.assertEqual(v.gesperrt, {"clip:1", "datei:1", "clip:3", "clip:4", "clip:5"})
        self.assertEqual(v.einsaetze, {"clip:1": 1, "datei:1": 1, "clip:3": 1, "clip:4": 1, "clip:5": 1})
        self.assertEqual(v.videos["clip:1"], {alt, v1})
        self.assertTrue(v.wiederholung("clip:2") and not v.wiederholung("clip:9"))
        self.assertEqual(szenen.verlauf(self.con, idx, sperre=3).gesperrt, v.gesperrt)   # das 40 Tage alte sperrt nicht
        fassung = szenen.verlauf(self.con, idx, ersetzt=[f1, v1], tage=30, sperre=2)   # neue Fassung von f1
        self.assertEqual(fassung.gesperrt, {"clip:4"})                    # clip:1 ist eigen, lief sonst nur im alten
        self.assertFalse(fassung.wiederholung("clip:1") or fassung.wiederholung("clip:5"))
        self.assertEqual(fassung.einsaetze, {"clip:4": 1})
        self.video(["clip:5"], erstellt=iso(jetzt()))                    # ein 🎬 danach hat clip:5 gezeigt
        fassung = szenen.verlauf(self.con, idx, ersetzt=[f1, v1], tage=30, sperre=2)
        self.assertEqual(fassung.gesperrt, {"clip:5", "clip:4"})          # Prüfung 08.10.: bleibt für die Fassung gesperrt

    def test_verbraucht_und_ersetzt_kette(self):
        """08.10.: verbraucht sind die Szenen aus Videos, die du gesehen hast oder die gleich kommen (fertig gerendert,
        ein Abend-Video, das der Timer nachrendert) – nicht aus still aussortierten oder gescheiterten. Eine Fassung
        ersetzt ihr Video samt dessen Vorgängern."""
        video = self.video

        video(["clip:1"], "gesendet")
        video(["clip:2"], "gerendert")                          # gleich bei dir
        video(["clip:3"], "neu")                                # Rendern gescheitert: nie gesehen
        abend = video(["clip:4"], "neu")                        # Abend-Video, das der Timer nachholt
        self.con.execute("INSERT INTO sitzungen (name, matches, entwurf_id, verarbeitet) VALUES ('s', '[]', ?, 'x')",
                         (abend,))
        video(["clip:5"], "gerendert", verworfen="Tor")         # still aussortiert
        self.assertEqual(szenen.verbraucht(self.con), {"clip:1", "clip:2", "clip:4"})
        alt = video(["clip:6"], "bewertet")
        fassung = video(["clip:7"], "bewertet", parameter=json.dumps({"ersetzt": [alt]}))
        self.assertEqual(szenen.ersetzt_kette(self.con, fassung), [fassung, alt])
        self.assertEqual(szenen.momente_der_entwuerfe(self.con, [fassung, alt]), {"clip:6", "clip:7"})
        kaputt = video([], "bewertet", parameter="kein json")
        self.assertEqual(szenen.ersetzt_kette(self.con, kaputt), [kaputt])

    def test_fassung_zweite_aufnahme_ohne_match(self):
        # Eine lautere Nvidia-Aufnahme (ohne Match) darf ihren Clip nicht als Ersatz verdrängen, und eine behaltene
        # SteelSeries-Aufnahme zählt zum Match ihres Clips (Abend) – sonst käme der frühere Abend zu früh dran
        from clip_pipeline import regie

        k = lambda s, match, punkte, stark=True: SimpleNamespace(  # noqa: E731
            schluessel=s, match_id=match, punkte=punkte, clip_id=1 if match else None, fail=False, stark=stark,
            gesperrt=False, nachschub=False)
        idx = {"datei:nv": {"clip:7"}, "clip:7": {"datei:nv"}, "datei:ss": {"clip:6"}, "clip:6": {"datei:ss"}}

        def fassung(**extra):
            alle = [k("datei:ss", None, 12.0), k("clip:6", "a1", 11.0), k("clip:7", "a1", 1.0, stark=False),
                    k("datei:nv", None, 5.0), k("clip:13", "a3", 11.0), k("clip:9", "a3", 3.5), k("clip:2", "f1", 3.5)]
            auswahl, pflicht = regie.fassung_kandidaten(
                alle, {"behalten": ["datei:ss", "clip:13"], "ohne": ["clip:9"], "abend": ["a3"]}, idx,
                {"datei:ss", "clip:13", "clip:9"}, {"min_momente": 3}, **extra)
            return [x.schluessel for x in pflicht], {x.schluessel: (x.nachschub, getattr(x, "frueher", False),
                                                                    getattr(x, "fueller", False))
                                                     for x in auswahl if not any(x is p for p in pflicht)}

        # /experte (ohne frueher_ok): der frühere Abend nur hinten an, ohne „höchstens die Hälfte“ (wie bis 08.10.)
        self.assertEqual(fassung(), (["datei:ss", "clip:13"], {"clip:7": (False, False, False),
                                                                "clip:2": (False, True, False)}))
        # einfacher Modus: Nachschub mit Hälfte-Grenze, der Einzelkill vom Abend erst nach den bekannten starken
        self.assertEqual(fassung(frueher_ok={"f1"}), (["datei:ss", "clip:13"], {"clip:7": (False, False, True),
                                                                                 "clip:2": (True, False, False)}))
        # Prüfung 08.10.: eine behaltene Szene, die gerade erst in einem anderen Video lief, ist keine Pflicht
        self.assertEqual(fassung(frueher_ok={"f1"}, gesperrt={"clip:13"})[0], ["datei:ss"])


@unittest.skipUnless(HAT_FFMPEG and lernbot is not None, "ffmpeg oder python-telegram-bot fehlt")
class Langweilig(MitRegieMaterial):
    def setUp(self):
        super().setUp()
        self.konfig.daten["regie"].update(szenen="stark", stil="auto")   # Standard: nur starke Szenen, Aufbau lernt
        for i, start in zip(self.momente_anlegen(SZENEN), START):
            self.con.execute("UPDATE momente SET start_utc = ? WHERE id = ?", (start, i))
        self.songs = [self.musik_anlegen(bpm, "episch", name=n)["id"] for bpm, n in ((150, "A"), (140, "B"), (160, "C"))]
        self.alt({"datei:9"})
        regeln.sperre(self.con, "moment", ["datei:1"], "langweilig")          # alte 🥱-Sperre (bis 07.10.)
        self.bot, self.aufgaben = FakeBot(), []
        self.app = SimpleNamespace(bot=self.bot, bot_data={"con": self.con, "konfig": self.konfig, "erlaubt": 42},
                                   create_task=lambda koro: self.aufgaben.append(koro))
        self.context = SimpleNamespace(bot_data=self.app.bot_data, application=self.app, args=[])

    def tearDown(self):
        for koro in self.aufgaben:
            koro.close()
        super().tearDown()

    def alt(self, momente, liste=None, name="alt"):
        """Ein früherer Entwurf, in dem diese Momente schon liefen."""
        pfad = self.tmp / f"{name}.json"
        pfad.write_text(json.dumps(liste or {"segmente": [{"moment": m} for m in sorted(momente)]}), encoding="utf-8")
        return self.con.execute("INSERT INTO entwuerfe (name, format, schnittliste, parameter, status, erstellt) VALUES "
                                "(?, 'short', ?, '{}', 'gesendet', ?)", (name, str(pfad), iso(jetzt()))).lastrowid

    def langweilig(self, eid):
        """❌ → 🥱 im einfachen Modus; gibt den Satz und die neue Fassung zurück (None = kein Video). Gestartet wird
        sie seit 08.10. von der Merkliste (lernbot.folge_starten, wie in der Schleife), gerendert wird nicht."""
        for daten in (f"d:{eid}:-1", f"g:{eid}:langweilig"):
            asyncio.run(lernbot.bei_klick(SimpleNamespace(callback_query=FakeQuery(daten)), self.context))
        satz = self.bot.texte[-1]
        vorher = self.con.execute("SELECT MAX(id) FROM entwuerfe").fetchone()[0]
        with mock.patch.object(lernbot.entwurf, "entwurf"), mock.patch.object(lernbot.kritik, "bewerte"), \
                mock.patch.object(lernbot, "sende_entwuerfe", new=mock.AsyncMock(return_value=0)):
            self.assertEqual(asyncio.run(lernbot.folge_starten(self.app)), 1)
            asyncio.run(self.aufgaben.pop())
        neu = self.con.execute("SELECT MAX(id) FROM entwuerfe").fetchone()[0]
        return satz, (neu if neu != vorher else None)

    def liste(self, eid):
        return regeln.liste_aus(self.con.execute("SELECT * FROM entwuerfe WHERE id = ?", (eid,)).fetchone())

    def test_neue_fassung_anders_geschnitten_mit_neuen_szenen(self):
        with mock.patch.object(lernbot.entwurf, "entwurf"), mock.patch.object(lernbot.kritik, "bewerte"):
            eid1 = lernbot.baue_entwurf(self.konfig, "short", {"a1", "a2"})
        self.con.execute("UPDATE entwuerfe SET status = 'gesendet' WHERE id = ?", (eid1,))
        v1 = self.liste(eid1)
        m1 = [m for m, _ in regeln._momente(v1)]
        self.assertIn("datei:1", m1)                                        # alte 🥱-Sperre wirkt nicht mehr
        self.assertFalse({"datei:2", "datei:5"} <= set(m1))                 # dieselbe Szene nie zweimal
        gut, schwach = regeln.langweilig_teilung(v1)
        satz, eid2 = self.langweilig(eid1)
        self.assertIn("2 schwächeren tausche ich gegen andere – zuerst neue", satz)   # 08.10.: auch bekannte
        self.assertNotIn("nie wieder", satz)
        v2 = self.liste(eid2)
        self.assertEqual(v2["parameter"]["ersetzt"], [eid1])               # 08.10.: ersetzt v1, darf seine Szenen
        m2 = {m for m, _ in regeln._momente(v2)}
        self.assertTrue(set(gut) <= m2, (gut, m2))                          # die stärkere Hälfte bleibt
        self.assertFalse(set(schwach) & m2)                                 # die schwächere fehlt in dieser Fassung
        ersatz = m2 - set(gut)
        self.assertTrue(ersatz and ersatz <= {"datei:6", "datei:7", "datei:8", "datei:10"}, ersatz)
        if "datei:10" in ersatz:                                            # erst der Abend, dann frühere Abende
            self.assertTrue({"datei:6", "datei:7", "datei:8"} <= m2)
        self.assertFalse({"datei:2", "datei:5"} <= m2)
        p1, p2 = v1["parameter"], v2["parameter"]
        self.assertNotEqual(stile.STILE[p2["stil"]]["reihenfolge"], stile.STILE[p1["stil"]]["reihenfolge"])
        self.assertNotEqual(p2["geschmack"]["tempo"], p1["geschmack"].get("tempo"))
        self.assertGreater(abs(p2["seg_min_faktor"] / p1["seg_min_faktor"] - 1), 0.14)   # spürbar anderes Tempo
        self.assertNotEqual(v2["musik"]["track_id"], v1["musik"]["track_id"])
        self.assertEqual([tuple(z) for z in self.con.execute("SELECT art, schluessel, grund FROM sperren")],
                         [("moment", "datei:1", "langweilig")])             # keine neue Sperre

    def test_kein_nachschub_kein_video(self):
        self.alt({"datei:6", "datei:7", "datei:8", "datei:10"}, name="alt2")
        v1 = {"format": "short", "dauer_s": 40, "stimmung": "episch", "musik": {"track_id": self.songs[0]},
              "parameter": {"stil": "montage", "reihenfolge": "bogen", "seg_min_faktor": 0.6,
                            "geschmack": {"aufbau": "montage", "tempo": "schnell", "zeitlupe": "viel"}},
              "segmente": [{"moment": f"datei:{i}", "intensitaet": st, "match_id": m}
                           for i, st, m in ((1, 10.0, "a1"), (2, 6.0, "a2"), (3, 3.0, "a1"), (4, 3.0, "a2"))]}
        eid1 = self.alt(set(), v1, name="v1")
        vorher = self.con.execute("SELECT COUNT(*) FROM entwuerfe").fetchone()[0]
        _satz, eid2 = self.langweilig(eid1)
        self.assertIsNone(eid2)
        self.assertIn("keine neue Fassung", self.bot.texte[-1])
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM entwuerfe").fetchone()[0], vorher)


class Ersatz(unittest.TestCase):
    def test_ersatz_frueherer_abende_nur_aus_erlaubten_matches(self):
        """Stufe 4 (08.10.): Ersatz früherer Abende auch nach 🥱 nur aus Matches, die höchstens 12 Tage alt sind
        (regie.nachschub_matches) – sonst fehlt beim ✅ das Rohvideo. Eine behaltene Szene eines früheren Abends (das
        Video hatte Nachschub) bleibt Pflicht, zählt aber als Nachschub (höchstens die Hälfte, nie vorn)."""
        from clip_pipeline import regie

        k = lambda s, match, stark=True: SimpleNamespace(  # noqa: E731
            schluessel=s, match_id=match, punkte=5.0, clip_id=1, fail=False, stark=stark, gesperrt=False,
            nachschub=False)
        alle = [k("clip:1", "a1"), k("clip:2", "a1"), k("clip:3", "a1", stark=False), k("clip:5", "o1"),
                k("clip:6", "o1"), k("clip:7", "z1")]
        fassung = {"behalten": ["clip:1", "clip:5"], "ohne": ["clip:2"], "abend": ["a1"]}
        auswahl, pflicht = regie.fassung_kandidaten(alle, fassung, {}, {"clip:1", "clip:2", "clip:5"},
                                                    {"min_momente": 3}, frueher_ok={"o1"})
        self.assertEqual({x.schluessel: x.nachschub for x in auswahl},                 # z1: zu alt
                         {"clip:1": False, "clip:3": False, "clip:5": True, "clip:6": True})
        self.assertEqual([x.schluessel for x in pflicht], ["clip:1", "clip:5"])
        with self.assertRaises(regie.KeineNeuenSzenen):                                 # nichts Erlaubtes mehr
            regie.fassung_kandidaten(alle, fassung, {}, {"clip:1", "clip:2", "clip:3", "clip:5"},
                                     {"min_momente": 3}, frueher_ok=set())

    def test_ersatz_auch_wenn_die_behaltenen_reichen(self):
        """Prüfung 07.10.: Erreichen die behaltenen Szenen schon das Ziel, kommt trotzdem neuer Ersatz dazu – zu lang
        wird es nicht: dann geht die schwächste behaltene Szene (sonst „keine neue Fassung“, obwohl Neues da ist)."""
        from clip_pipeline import regie

        k = lambda n, pk: regie.Kandidat(n, f"/x/{n}.mp4", 20.0, "episch", pk, pk, None, f"m{n}", (4.0, 12.0),  # noqa: E731
                                         (5.0, 11.5), "Test")
        pflicht = [k(f"gut{i}", 10.0 - i) for i in range(8)]
        neu = [k("neu1", 2.0), k("neu2", 1.0)]
        fmt, p = regie.FORMATE["short"], {**regie.PARAMETER, "ziel_dauer_s": 30.0, "max_je_match": 10}
        gewaehlt, _, _ = regie.waehle(pflicht + neu, fmt, p, pflicht, ersatz_min=2)
        namen = {x.schluessel for x in gewaehlt}
        self.assertTrue({"neu1", "neu2"} <= namen)
        self.assertLessEqual(len(gewaehlt), regie.momente_grenzen(fmt)[1])
        self.assertIn("gut0", namen)                                       # die stärkste behaltene bleibt


if __name__ == "__main__":
    unittest.main()
