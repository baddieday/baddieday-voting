"""Regisseur: Dauer je Format, Spannungsbogen, Schnitte auf dem Beat, Übergänge je Stimmung, Lernen."""

import json
import unittest
import unittest.mock
from pathlib import Path

from clip_pipeline import regie, regie_lernen
from clip_pipeline.konfig import Konfig

from tests.hilfen import HAT_FFMPEG
from tests.regie_hilfen import MOMENTE, MitRegieMaterial


def lies(e):
    with open(e["datei"], encoding="utf-8") as f:
        return json.load(f)


@unittest.skipUnless(HAT_FFMPEG, "ffmpeg fehlt")
class Regisseur(MitRegieMaterial):
    def compose(self, fmt):
        p, ziel = regie_lernen.aktuelle(self.con, self.konfig)
        return regie.erstelle(self.con, self.konfig, fmt, parameter=p, ziel=ziel)

    def auf_beats(self, liste, track):
        beats = json.loads(track["beats"])
        versatz = liste["musik"]["start_s"]
        for s in liste["segmente"]:
            if s["auf_beat"]:
                self.assertTrue(any(abs(b - versatz - s["zeit_ende"]) < 0.002 for b in beats), s)

    def test_short_und_zusammenschnitt(self):
        self.momente_anlegen(MOMENTE * 2)
        # Ein verworfener Clip darf nie vorkommen – auch wenn er der stärkste Moment wäre
        verworfen = self.clip_anlegen(status="verworfen", max_gruppe=5)
        self.con.execute("UPDATE momente SET clip_id = ?, stimmung = 'episch' WHERE schluessel = 'datei:1'", (verworfen,))
        spannend = self.musik_anlegen(128, "spannend")
        episch = self.musik_anlegen(150, "episch")
        self.musik_anlegen(90, "chill")

        e = self.compose("short")
        liste = lies(e)
        self.assertEqual(regie.pruefe_liste(liste), [])
        self.assertTrue(30 <= liste["dauer_s"] <= 45, liste["dauer_s"])
        self.assertEqual((liste["aufloesung"], liste["overlay"]), ([1080, 1920], "clip-battle.de"))
        self.assertEqual(liste["musik"]["track_id"], episch["id"])  # Hauptstimmung episch -> epische Musik
        self.assertEqual(liste["bogen"][-1], max(liste["bogen"]))  # Höhepunkt zum Schluss
        self.assertNotIn("datei:1", [s["moment"] for s in liste["segmente"]])
        self.assertTrue(all(s["auf_beat"] for s in liste["segmente"]))
        self.auf_beats(liste, episch)

        e = self.compose("zusammenschnitt")
        liste = lies(e)
        self.assertEqual(regie.pruefe_liste(liste), [])
        self.assertTrue(180 <= liste["dauer_s"] <= 300, liste["dauer_s"])
        self.assertEqual(liste["aufloesung"], [1920, 1080])
        self.assertEqual(liste["musik"]["track_id"], spannend["id"])
        self.auf_beats(liste, spannend)
        segs = liste["segmente"]
        self.assertEqual(liste["bogen"][-1], max(liste["bogen"]))
        self.assertGreater(liste["bogen"][0], sorted(liste["bogen"])[len(segs) // 2])  # starker Einstieg
        for s in segs[1:]:
            if s["uebergang"]["art"] != "schnitt":
                self.assertEqual(s["uebergang"]["art"], regie.UEBERGANG[s["stimmung"]][0])
        self.assertTrue(any(s["uebergang"]["art"] == "fade" for s in segs))  # chill -> weiche Blende
        gleiche = sum(1 for a, b in zip(segs, segs[1:]) if a["stimmung"] == b["stimmung"])
        self.assertLess(gleiche, len(segs) // 3)  # Abwechslung
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM entwuerfe").fetchone()[0], 2)

    def test_lernen_wirkt_im_naechsten_lauf(self):
        self.konfig.daten["regie"]["vorgaben"] = {"abwechslung": 0}  # gleiche Momente: nur das Gelernte wirkt
        self.momente_anlegen(MOMENTE)
        erster_track = self.musik_anlegen(150, "episch", name="A")
        self.musik_anlegen(146, "episch", name="B")
        vorher = lies(self.compose("short"))
        self.assertEqual(vorher["musik"]["track_id"], erster_track["id"])

        entwurf = self.con.execute("SELECT id FROM entwuerfe").fetchone()["id"]
        for grund in ("hektisch", "lang", "musik", "abgeschnitten"):
            regie_lernen.bewerte(self.con, entwurf, grund=grund)
        for _ in range(2):  # zwei weitere Entwürfe, beide "zu lang"
            regie_lernen.bewerte(self.con, self.compose("short")["entwurf"], grund="lang")
        self.assertEqual(self.con.execute("SELECT daumen FROM entwurf_bewertungen").fetchone()["daumen"], -1)
        zweiter = self.con.execute("SELECT id FROM entwuerfe").fetchone()["id"]
        regie_lernen.bewerte(self.con, zweiter, grund="hektisch")  # zweimal hektisch (gleicher Entwurf, umschalten)
        regie_lernen.bewerte(self.con, zweiter, grund="hektisch")
        p, _ = regie_lernen.aktuelle(self.con, self.konfig)
        self.assertGreater(p["puffer_vor_s"], regie.PARAMETER["puffer_vor_s"])
        self.assertLess(p["dauer_faktor"], 1.0)
        self.assertEqual(p["track_malus"], {str(erster_track["id"]): 1.0})

        nachher = lies(self.compose("short"))
        self.assertNotEqual(nachher["musik"]["track_id"], erster_track["id"])  # andere Musik
        self.assertLess(nachher["dauer_s"], vorher["dauer_s"])                 # kürzer
        mittel = lambda l: sum(s["zeit_ende"] - s["zeit_start"] for s in l["segmente"]) / len(l["segmente"])  # noqa: E731
        self.assertGreaterEqual(mittel(nachher), mittel(vorher) - 0.01)        # nicht hektischer
        self.assertIn("Musik-Abzug", regie_lernen.lernstand_text(self.con, self.konfig))

    def test_stimmung_getroffen_verschiebt_musikziel(self):
        self.momente_anlegen(MOMENTE[:6])
        self.musik_anlegen(100, "spannend")
        e = self.compose("short")
        regie_lernen.bewerte(self.con, e["entwurf"], grund="getroffen")
        p, ziel = regie_lernen.aktuelle(self.con, self.konfig)
        haupt = lies(e)["stimmung"]
        self.assertEqual(p["stimmung_bonus"][haupt], 0.5)
        self.assertLess(ziel[haupt]["bpm"], regie.ZIEL[haupt]["bpm"] + 0.01 if regie.ZIEL[haupt]["bpm"] > 100 else 999)
        self.assertEqual(self.con.execute("SELECT daumen FROM entwurf_bewertungen").fetchone()["daumen"], 1)

    def test_ohne_musik_und_ohne_momente(self):
        with self.assertRaises(regie.RegieFehler):
            self.compose("short")
        self.momente_anlegen(MOMENTE[:5])
        liste = lies(self.compose("short"))
        self.assertIsNone(liste["musik"])
        self.assertIn("keine Musik", " ".join(liste["hinweise"]))
        self.assertEqual(regie.pruefe_liste(liste), [])


@unittest.skipUnless(HAT_FFMPEG, "ffmpeg fehlt")
class ActionAmEnde(MitRegieMaterial):
    DAUER = 10.0

    def test_jubel_kurz_vor_dateiende_bricht_compose_nicht(self):
        # Instant-Replays enden direkt nach der Action: Jubel bei 9,6 s von 10 s
        self.momente_anlegen([("lustig", 0, [], "m1"), ("spannend", 1, [9.8], "m2"), ("chill", 0, [], "m3")])
        self.con.execute("UPDATE momente SET merkmale = json_set(merkmale, '$.jubel_laut_s', json('[9.6]')) "
                         "WHERE schluessel = 'datei:1'")
        liste = lies(regie.erstelle(self.con, self.konfig, "short"))
        self.assertEqual(regie.pruefe_liste(liste), [])


@unittest.skipUnless(HAT_FFMPEG, "ffmpeg fehlt")
class Vorgaben(MitRegieMaterial):
    """Deine Startwerte aus config/lokal.toml – von dort lernt der Regisseur weiter."""

    def test_vorgaben_grenzen_und_hinweise(self):
        self.konfig.daten["regie"]["vorgaben"] = {
            "seg_min_faktor": 1.4, "dauer_faktor": 5.0, "puffer_vor_s": "viel", "gibtsnicht": 1,
            "stimmung_bonus": {"lustig": 1.0, "wütend": 3}, "beats_pro_schnitt": 4,
        }
        self.konfig.daten["regie"]["musik_ziele"] = {"episch": {"bpm": 150, "energie": 2.0}}
        p, ziel, hinweise = regie_lernen.vorgaben(self.konfig)
        self.assertEqual(p["seg_min_faktor"], 1.4)
        self.assertEqual(p["dauer_faktor"], 1.0)            # auf die Grenze gestutzt
        self.assertEqual(p["puffer_vor_s"], regie.PARAMETER["puffer_vor_s"])  # keine Zahl -> ignoriert
        self.assertEqual(p["stimmung_bonus"], {"lustig": 1.0})
        self.assertEqual((ziel["episch"]["bpm"], ziel["episch"]["energie"]), (150.0, 1.0))
        self.assertEqual(len(hinweise), 3)                  # puffer_vor_s, gibtsnicht, wütend
        p2, _ = regie_lernen.aktuelle(self.con, self.konfig)
        self.assertEqual(p2["beats_pro_schnitt"], 4)        # Vorgabe ist die Untergrenze
        self.assertIn("deine Vorgabe", regie_lernen.lernstand_text(self.con, self.konfig))

    def test_vorgabe_wirkt_und_bewertung_lernt_weiter(self):
        self.momente_anlegen(MOMENTE[:10])
        self.musik_anlegen(150, "episch")
        vorher = lies(regie.erstelle(self.con, self.konfig, "short", parameter=regie_lernen.aktuelle(self.con, self.konfig)[0]))
        self.konfig.daten["regie"]["vorgaben"] = {"dauer_faktor": 0.7}
        p, ziel = regie_lernen.aktuelle(self.con, self.konfig)
        e = regie.erstelle(self.con, self.konfig, "short", parameter=p, ziel=ziel)
        self.assertLess(lies(e)["dauer_s"], vorher["dauer_s"])
        regie_lernen.bewerte(self.con, e["entwurf"], grund="lang")  # und noch kürzer gewünscht
        p2, _ = regie_lernen.aktuelle(self.con, self.konfig)
        self.assertEqual(p2["dauer_faktor"], 0.63)          # 0,7 × 0,9 – von der Vorgabe aus gelernt

    def test_cli_bewerte_und_lernstand(self):
        import contextlib
        import io
        from unittest import mock

        from clip_pipeline import cli

        self.momente_anlegen(MOMENTE[:6])
        e = regie.erstelle(self.con, self.konfig, "short")
        ausgabe = io.StringIO()
        with mock.patch("clip_pipeline.cli.lade", return_value=self.konfig), contextlib.redirect_stdout(ausgabe), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["bewerte", str(e["entwurf"]), "--schlecht", "--grund", "hektisch",
                                       "--grund", "lang"]), 0)
            self.assertEqual(cli.main(["lernstand"]), 0)
            self.assertEqual(cli.main(["bewerte", "999", "--gut"]), 1)
        zeilen = [json.loads(z) for z in ausgabe.getvalue().splitlines() if z.startswith("{")]
        self.assertEqual((zeilen[0]["daumen"], zeilen[0]["gruende"]), (-1, ["hektisch", "lang"]))
        self.assertGreater(zeilen[1]["parameter"]["seg_min_faktor"], 1.0)
        self.assertIn("unbekannt", zeilen[2]["fehler"])


@unittest.skipUnless(HAT_FFMPEG, "ffmpeg fehlt")
class Abwechslung(MitRegieMaterial):
    """Nicht immer dieselben Momente: Abzug für kürzlich Gezeigtes, Lernen je Moment aus 👍/👎."""

    def compose(self):
        p, ziel = regie_lernen.aktuelle(self.con, self.konfig)
        return lies(regie.erstelle(self.con, self.konfig, "short", parameter=p, ziel=ziel))

    @staticmethod
    def momente(liste):
        return {s["moment"] for s in liste["segmente"]}

    def test_neue_momente_und_die_besten_kommen_wieder(self):
        """Abwechslung seit 27.09.: Cooldown 3 Entwürfe (Momente aus den letzten drei sind gesperrt), danach kommen
        die stärksten wieder; mindestens die Hälfte eines Entwurfs war noch in keinem (Frische-Quote)."""
        self.momente_anlegen(MOMENTE * 2)  # 32 Momente, ein Short braucht ~5
        self.musik_anlegen(150, "episch")
        listen = [self.compose() for _ in range(6)]
        for liste in listen:
            self.assertEqual(regie.pruefe_liste(liste), [])
        eins, zwei, drei, vier, fuenf = (self.momente(listen[i]) for i in range(5))
        self.assertEqual(eins & zwei, set())                   # direkt danach: alles neu
        self.assertEqual(listen[0]["auswahl"]["neu"], len(eins))
        self.assertEqual(listen[1]["auswahl"]["schon_gezeigt"], len(eins & zwei))
        self.assertEqual((eins & drei, eins & vier), (set(), set()))   # Cooldown: drei Entwürfe lang gesperrt
        self.assertGreater(listen[1]["auswahl"]["gesperrt"], 0)
        self.assertEqual(listen[1]["auswahl"]["cooldown"], 3)
        self.assertTrue(eins & fuenf)                          # die stärksten kommen wieder, nur nicht jedes Mal
        gezeigt = {s["moment"]: s["gezeigt"] for s in listen[4]["segmente"]}
        self.assertTrue(all(gezeigt[m] >= 1 for m in eins & fuenf))
        # Frische-Quote: mindestens die Hälfte neu – solange es noch frische Momente gibt (32 Momente, ~6 je Short:
        # ab dem 4./5. Entwurf ist jeder schon einmal gezeigt, dann ist die Quote nicht mehr erfüllbar)
        for liste in listen[1:3]:
            self.assertGreaterEqual(liste["auswahl"]["neu"] * 2, liste["auswahl"]["neu"] + liste["auswahl"]["schon_gezeigt"])
        self.assertEqual(listen[1]["auswahl"]["frische_quote"], 0.5)
        # Ohne Abwechslung (Vorgabe 0): immer dieselben – so war es vorher; Cooldown und Quote sind dann auch aus
        self.konfig.daten["regie"]["vorgaben"] = {"abwechslung": 0}
        self.assertEqual(self.momente(self.compose()), self.momente(self.compose()))
        text = regie_lernen.lernstand_text(self.con, self.konfig)
        self.assertIn("deine Vorgabe", text)
        self.assertIn("Abwechslung aus (0)", text)

    def test_frische_ueberleben_kuerzen_und_nachlegen(self):
        """Review 27.09.: waehle hielt die Frische-Quote, aber Kürzen (min Punkte) und Nachlegen (max Punkte) warfen
        die frischen – meist punktschwächsten – Momente danach wieder raus (Entwurf 7: 0 frische trotz Vorrat)."""
        self.momente_anlegen(MOMENTE * 2)
        self.musik_anlegen(150, "episch")
        frisch_gewaehlt = []
        original = regie.waehle

        def waehle(ks, fmt, p):
            gewaehlt, ziel, hinweise = original(ks, fmt, p)
            frisch_gewaehlt.append(sum(1 for k in gewaehlt if k.gezeigt == 0))
            return gewaehlt, ziel, hinweise

        with unittest.mock.patch.object(regie, "waehle", waehle):
            listen = [self.compose() for _ in range(6)]
        for liste, frisch in zip(listen, frisch_gewaehlt):
            n = liste["auswahl"]["neu"] + liste["auswahl"]["schon_gezeigt"]
            soll = regie.frische_soll(n, liste["parameter"])
            self.assertGreaterEqual(liste["auswahl"]["neu"], min(frisch, soll), (liste["name"], frisch, soll))
        alle = {z[0] for z in self.con.execute("SELECT schluessel FROM momente")}
        self.assertEqual(set.union(*(self.momente(liste) for liste in listen)), alle)  # vorher fehlte nach 6 einer

    def test_nach_dem_kuerzen_wird_nachgelegt(self):
        """27.09.: Serien (bis 20 s) lassen die Auswahl über 45 s schießen; das Kürzen streicht dann einen ganzen
        Moment – vorher blieb die Lücke (Shorts 30–38 s mit 2–3 Momenten). Jetzt füllen kleinere Momente nach."""
        self.momente_anlegen(MOMENTE * 2)
        self.musik_anlegen(150, "episch")
        # Multikills mit Aktions-Zeiten = Serien ab dem ersten Umhauen (10–14 s am Stück statt 12 s Deckel)
        for z in self.con.execute("SELECT id, merkmale FROM momente").fetchall():
            mk = json.loads(z["merkmale"])
            if len(mk["kill_sekunden"]) >= 2:
                mk["aktion_sekunden"] = [round(k - 4.0, 1) for k in mk["kill_sekunden"]]
                self.con.execute("UPDATE momente SET merkmale = ? WHERE id = ?", (json.dumps(mk), z["id"]))
        for _ in range(3):
            liste = self.compose()
            self.assertEqual(regie.pruefe_liste(liste), [])
            self.assertGreaterEqual(liste["dauer_s"], 42.0, liste["dauer_s"])   # vorher ~37 s
            self.assertLessEqual(liste["dauer_s"], 45.0 + 1e-6)

    def test_auswahl_zaehlt_alle_momente(self):
        # Passt beim Auffüllen kein Moment mehr hinein, bleibt die Auswahl trotzdem vollständig
        # (Entwurf #17: nach „abgeschnitten“ Vorlauf 3,5 s -> meldete „nur 6 Momente“ statt 128)
        # Nachgestellt mit fester Testmusik: nach drei Entwürfen passt bei 3,5 s Vorlauf beim Auffüllen nichts mehr
        self.momente_anlegen(MOMENTE)
        self.musik_anlegen(150, "episch")
        p, ziel = regie_lernen.aktuelle(self.con, self.konfig)
        for vor in (1.0, 2.0, 3.0, 3.5, 4.5):
            liste = lies(regie.erstelle(self.con, self.konfig, "short", parameter={**p, "puffer_vor_s": vor}, ziel=ziel))
            self.assertEqual(liste["auswahl"]["kandidaten"], len(MOMENTE), vor)
            self.assertFalse([h for h in liste["hinweise"] if h.startswith("nur ") and f"nur {len(MOMENTE)} " not in h])

    def test_lernen_je_moment(self):
        self.momente_anlegen(MOMENTE)
        self.konfig.daten["regie"]["vorgaben"] = {"abwechslung": 0}
        p, _ = regie_lernen.aktuelle(self.con, self.konfig)
        gut, schlecht, musik, langweilig = (regie.erstelle(self.con, self.konfig, "short", parameter=p)
                                            for _ in range(4))       # ohne Abwechslung: viermal dieselben
        regie_lernen.bewerte(self.con, gut["entwurf"], daumen=1)
        p, _ = regie_lernen.aktuelle(self.con, self.konfig)
        m = self.momente(lies(gut))
        self.assertEqual({p["moment_bonus"][k] for k in m}, {0.5})
        regie_lernen.bewerte(self.con, schlecht["entwurf"], daumen=-1)        # 👎 ohne Grund: die Clips
        regie_lernen.bewerte(self.con, musik["entwurf"], grund="musik")       # 👎 wegen Musik: nicht die Clips
        p, _ = regie_lernen.aktuelle(self.con, self.konfig)
        self.assertEqual({p["moment_bonus"][k] for k in m}, {0.0})
        regie_lernen.bewerte(self.con, langweilig["entwurf"], grund="langweilig")
        p, ziel = regie_lernen.aktuelle(self.con, self.konfig)
        self.assertEqual({p["moment_bonus"][k] for k in m}, {-1.0})
        self.assertIn("weniger gern gesehen", regie_lernen.lernstand_text(self.con, self.konfig))
        danach = self.momente(lies(regie.erstelle(self.con, self.konfig, "short", parameter=p, ziel=ziel)))
        self.assertNotEqual(danach, m)                                        # andere Clips, obwohl ohne Abwechslung


class Auswahl(unittest.TestCase):
    """waehle(): Cooldown und Frische-Quote (27.09.) – ohne Datenbank und ffmpeg."""

    @staticmethod
    def k(name, punkte, *, gezeigt=0, gesperrt=False, match="m1", kern=(4.0, 12.0)):
        return regie.Kandidat(name, f"/x/{name}.mp4", 20.0, "episch", punkte, punkte, None, match, kern,
                              (kern[0] + 1.0, kern[1] - 0.5), "Test", gezeigt=gezeigt, gesperrt=gesperrt)

    def test_cooldown_sperrt_und_quote_mischt(self):
        fmt, p = regie.FORMATE["short"], dict(regie.PARAMETER)
        stark = [self.k(f"alt{i}", 10.0, gezeigt=2, match=f"a{i}") for i in range(6)]   # schon gezeigt, nicht gesperrt
        frisch = [self.k(f"neu{i}", 2.0, match=f"n{i}") for i in range(6)]               # in keinem Entwurf
        gewaehlt, _, hinweise = regie.waehle(stark + frisch, fmt, p)
        # Gierig kämen nur die starken (6 × 8 s ≥ 45 s) – die Quote tauscht die Hälfte gegen frische
        self.assertGreaterEqual(sum(1 for k in gewaehlt if k.gezeigt == 0) * 2, len(gewaehlt))
        self.assertTrue(any(k.punkte == 10.0 for k in gewaehlt))                         # die stärksten bleiben dabei
        self.assertEqual(hinweise, [])
        # Cooldown: gesperrte Momente bleiben Reserve, solange die freien reichen
        gesperrt = [self.k(f"cd{i}", 20.0, gezeigt=1, gesperrt=True, match=f"c{i}") for i in range(6)]
        gewaehlt, _, hinweise = regie.waehle(gesperrt + stark + frisch, fmt, p)
        self.assertFalse(any(k.gesperrt for k in gewaehlt))
        self.assertEqual(hinweise, [])
        # … außer das freie Material reicht nicht für einen Entwurf: dann mit Hinweis freigegeben
        gewaehlt, _, hinweise = regie.waehle(gesperrt + frisch[:2], fmt, p)
        self.assertTrue(any(k.gesperrt for k in gewaehlt))
        self.assertTrue(hinweise and hinweise[0].startswith(regie.COOLDOWN_AUFGEHOBEN), hinweise)

    def test_abwechslung_null_schaltet_alles_aus(self):
        fmt, p = regie.FORMATE["short"], {**regie.PARAMETER, "abwechslung": 0.0}
        stark = [self.k(f"alt{i}", 10.0, gezeigt=2, match=f"a{i}") for i in range(6)]
        frisch = [self.k(f"neu{i}", 2.0, match=f"n{i}") for i in range(6)]
        gewaehlt, _, _ = regie.waehle(stark + frisch, fmt, p)
        self.assertTrue(all(k.punkte == 10.0 for k in gewaehlt))                        # keine Quote: nur die besten


class FormatRegeln(unittest.TestCase):
    """[regie.formate.<format>] (27.09.): Dauern je Format aus der Konfig statt fest im Code."""

    @staticmethod
    def konfig(formate):
        return Konfig(daten={"regie": {"formate": formate}}, quelle=Path("test"))

    def test_vorgabe_und_unsinn(self):
        fmt, hinweise = regie.format_regeln(self.konfig({"short": {"max_s": 60, "serie_max_s": 30}}), "short")
        self.assertEqual((fmt["max_s"], fmt["serie_max_s"], fmt["min_s"], hinweise), (60.0, 30.0, 30.0, []))
        fmt, hinweise = regie.format_regeln(self.konfig({"short": {"max_s": 20, "quatsch": 1}}), "short")
        self.assertEqual(fmt, regie.FORMATE["short"])                                 # min 30 > max 20: Standard
        self.assertEqual(len(hinweise), 2)
        self.assertEqual(regie.format_regeln(None, "zusammenschnitt"), (regie.FORMATE["zusammenschnitt"], []))


class KandidatenDateien(MitRegieMaterial):
    """27.09.: Fehlt die Moment-Datei, springt der Bot-Clip ein (gleicher Inhalt) und alles wird gezählt – vorher
    fiel der Moment still weg. Nicht nach einem Nachschnitt (andere Zeitbasis)."""

    def zeile(self, schluessel, clip_id, datei, merkmale):
        self.con.execute(
            """INSERT INTO momente (schluessel, clip_id, match_id, datei, start_s, ende_s, kills, stimmung, sicherheit,
                                    quelle, merkmale, erstellt, geaendert)
               VALUES (?, ?, 'm1', ?, 0, 20, 1, 'episch', 0.8, 'regel', ?, 'x', 'x')""",
            (schluessel, clip_id, datei, json.dumps(merkmale)))

    def test_rueckfall_auf_den_bot_clip_und_zaehlung(self):
        clip = self.clip_anlegen(status="freigegeben")
        clip_datei = self.konfig.wurzel / "sessions" / "m1" / "clips" / "001.mp4"
        clip_datei.parent.mkdir(parents=True, exist_ok=True)
        clip_datei.write_bytes(b"x")
        self.con.execute("UPDATE clips SET clip_pfad = 'sessions/m1/clips/001.mp4' WHERE id = ?", (clip,))
        mk = {"kills": 1, "max_gruppe": 1, "kill_sekunden": [8.0], "spitzen": 1, "jubel_laut": 0}
        self.zeile(f"clip:{clip}", clip, str(self.tmp / "material-alt" / "001.mp4"), mk)     # Kopie weg → Bot-Clip
        self.zeile("datei:99", None, str(self.tmp / "weg.mp4"), mk)                             # kein Clip → weg
        self.zeile("clip:98", clip, str(self.tmp / "nachschnitt-weg.mp4"), {**mk, "nachschnitt": {"version": 1}})
        gewichte, tabelle = {"kill_punkte": 1.0}, [0, 1, 3, 6, 10]
        ks, bericht = regie.kandidaten_mit_bericht(self.con, dict(regie.PARAMETER), [], gewichte=gewichte,
                                                   kill_tabelle=tabelle, konfig=self.konfig)
        self.assertEqual([k.schluessel for k in ks], [f"clip:{clip}"])
        self.assertEqual(ks[0].datei, str(clip_datei))
        self.assertEqual(bericht, {"ohne_datei": 2, "ersetzt": 1, "gesperrt": 0})
        # Ohne konfig (alte Aufrufer, Tests): wie bisher nur überspringen
        self.assertEqual(regie.kandidaten(self.con, dict(regie.PARAMETER), gewichte=gewichte, kill_tabelle=tabelle), [])

    def test_bilanz_nur_fuer_die_matches(self):
        # Spielabend (nur_matches): die Bilanz zählt fremde Matches nicht mit – vorher stand „30 im Cooldown“ im Short
        # eines Abends, zu dem keiner davon gehörte
        mk = {"kills": 1, "max_gruppe": 1, "kill_sekunden": [8.0], "spitzen": 1, "jubel_laut": 0}
        for schluessel, match in (("datei:1", "m1"), ("datei:2", "m2"), ("datei:3", "m2")):
            datei = self.tmp / f"{schluessel.replace(':', '-')}.mp4"
            datei.write_bytes(b"x")
            self.con.execute(
                """INSERT INTO momente (schluessel, clip_id, match_id, datei, start_s, ende_s, kills, stimmung,
                                        sicherheit, quelle, merkmale, erstellt, geaendert)
                   VALUES (?, NULL, ?, ?, 0, 20, 1, 'episch', 0.8, 'regel', ?, 'x', 'x')""",
                (schluessel, match, str(datei), json.dumps(mk)))
        frueher = [["datei:1", "datei:2", "datei:3"]]  # alle drei im letzten Entwurf -> alle im Cooldown
        p = {**regie.PARAMETER, "abwechslung": 0.7, "cooldown_entwuerfe": 3}
        ks, bericht = regie.kandidaten_mit_bericht(self.con, p, frueher, gewichte={"kill_punkte": 1.0},
                                                   kill_tabelle=[0, 1, 3, 6, 10], nur_matches={"m1"})
        self.assertEqual([k.schluessel for k in ks], ["datei:1"])
        self.assertEqual(bericht["gesperrt"], 1)
        _, alles = regie.kandidaten_mit_bericht(self.con, p, frueher, gewichte={"kill_punkte": 1.0},
                                                kill_tabelle=[0, 1, 3, 6, 10])
        self.assertEqual(alles["gesperrt"], 3)


class EffekteLernen(MitRegieMaterial):
    """Stufe 2 (Regisseur 2.0): „🎆 zu viele Effekte“ / „💥 mehr Action“ je Hauptstimmung, „zu hektisch“ dämpft.
    Ohne Video: Entwürfe direkt in der Datenbank, die Schnittliste nur mit Stimmung und Momenten."""

    # (Stimmung, Momente, Daumen, Gründe, mit Musik) – nur die alten Gründe, Werte unten vom Code vor Stufe 2
    ALTE_FOLGE = [
        ("episch", ["a", "b"], 1, [], False), ("spannend", ["b", "c"], -1, ["hektisch", "lang"], False),
        ("episch", ["a", "d"], -1, ["musik"], True), ("lustig", ["e"], 1, ["getroffen"], True),
        ("episch", ["a"], -1, ["langweilig", "abgeschnitten"], False), ("spannend", ["c"], -1, [], False),
        ("chill", ["f"], 1, ["hektisch"], False), ("episch", ["b"], 1, [], False),
    ]

    def setUp(self):
        super().setUp()
        self.konfig.daten["regie"].update(vorgaben={}, lernen_ab=3)
        self.n = 0

    def entwurf(self, stimmung, gruende=(), daumen=None, momente=("a",), track=None):
        self.n += 1
        pfad = self.tmp / f"e{self.n}.json"
        pfad.write_text(json.dumps({"stimmung": stimmung, "segmente": [{"moment": m} for m in momente]}),
                        encoding="utf-8")
        eid = self.con.execute("INSERT INTO entwuerfe (name, format, schnittliste, parameter, track_id, erstellt) "
                               "VALUES (?, 'short', ?, '{}', ?, 'x')", (f"e{self.n}", str(pfad), track)).lastrowid
        if daumen is not None:
            regie_lernen.bewerte(self.con, eid, daumen=daumen)
        for g in gruende:
            regie_lernen.bewerte(self.con, eid, grund=g)
        return eid

    def p(self):
        return regie_lernen.aktuelle(self.con, self.konfig)[0]

    def test_neue_gruende_hinten(self):
        self.assertEqual(list(regie_lernen.GRUENDE)[:6],
                         ["musik", "hektisch", "getroffen", "lang", "abgeschnitten", "langweilig"])
        self.assertEqual(list(regie_lernen.GRUENDE.items())[6:],
                         [("effekte_viel", "🎆 zu viele Effekte"), ("action", "💥 mehr Action"), ("kurz", "⏱️ zu kurz")])

    def test_zu_kurz_hebt_zu_lang_wieder_auf(self):
        """27.09.: „⏳ zu lang“ ×0,9 hatte kein Gegenstück – der Faktor konnte nur fallen. „⏱️ zu kurz“ ÷0,9,
        Deckel bleibt 1,0; beide Gründe zugleich heben sich auf."""
        self.entwurf("episch", ["lang"])
        self.assertEqual(self.p()["dauer_faktor"], 0.9)
        self.entwurf("episch", ["kurz"])
        self.assertEqual(self.p()["dauer_faktor"], 1.0)
        self.entwurf("episch", ["kurz"])                                  # nie über 1,0 (max_s regelt die Länge)
        self.assertEqual(self.p()["dauer_faktor"], 1.0)
        self.entwurf("episch", ["lang", "kurz"])
        self.assertEqual(self.p()["dauer_faktor"], 1.0)
        self.entwurf("episch", ["lang"])
        self.assertIn("Ziel-Dauer bei 90% (2× „⏳ zu lang“, 2× „⏱️ zu kurz“)", regie_lernen.lernstand_text(self.con, self.konfig))
        self.assertEqual((regie.PARAMETER["effekt_staerke"], regie.PARAMETER["effekt_hektik"]), ({}, 1.0))

    def test_vorgaben_mit_grenzen(self):
        self.konfig.daten["regie"]["vorgaben"] = {
            "effekt_hektik": 5, "effekt_staerke": {"episch": 2.0, "chill": 0, "lustig": 0.7, "wütend": 1.0,
                                                   "spannend": "viel", "frustriert": True}}
        p, _, hinweise = regie_lernen.vorgaben(self.konfig)
        self.assertEqual(p["effekt_staerke"], {"episch": 1.5, "chill": 0.0, "lustig": 0.7})
        self.assertEqual(p["effekt_hektik"], 1.3)
        self.assertEqual(sorted(hinweise), [f"regie.vorgaben.effekt_staerke.{s} ignoriert"
                                            for s in ("frustriert", "spannend", "wütend")])
        self.assertEqual(regie.PARAMETER["effekt_staerke"], {})          # die Startwerte bleiben unberührt
        self.konfig.daten["regie"]["vorgaben"] = {"effekt_hektik": 0.1, "effekt_staerke": 0.5}
        p, _, hinweise = regie_lernen.vorgaben(self.konfig)
        self.assertEqual((p["effekt_hektik"], p["effekt_staerke"]), (0.3, {}))
        self.assertEqual(hinweise, ["regie.vorgaben.effekt_staerke ignoriert (unbekannt oder keine Zahl)"])

    def test_regeln_je_hauptstimmung(self):
        self.entwurf("episch", ["effekte_viel"])
        self.assertEqual(self.p()["effekt_staerke"], {"episch": 0.85})
        self.entwurf("episch", ["effekte_viel"])
        self.entwurf("spannend", ["action"])
        self.entwurf("lustig", ["effekte_viel", "action"])                # beide zugleich: nichts
        self.entwurf("chill", daumen=1)                                   # 👍/👎 ohne Grund: nichts
        self.entwurf("frustriert", daumen=-1)
        p = self.p()
        self.assertEqual(p["effekt_staerke"], {"episch": 0.722, "spannend": 1.15})
        self.assertEqual(p["effekt_hektik"], 1.0)
        self.assertEqual(self.con.execute("SELECT daumen FROM entwurf_bewertungen WHERE entwurf_id = 3")
                         .fetchone()[0], -1)                              # „mehr Action“ ist Kritik

    def test_chronologisch_mit_grenzen(self):
        for _ in range(4):
            self.entwurf("episch", ["action"])                            # 1,15 · 1,323 · 1,5 (Grenze) · 1,5
        self.entwurf("episch", ["effekte_viel"])
        self.assertEqual(self.p()["effekt_staerke"], {"episch": 1.275})   # von der Grenze aus, nicht von 1,749
        for _ in range(20):
            self.entwurf("spannend", ["effekte_viel"])
        self.assertEqual(self.p()["effekt_staerke"]["spannend"], 0.1)     # nie ganz aus durch Lernen
        self.entwurf("spannend", ["action"])
        self.assertEqual(self.p()["effekt_staerke"]["spannend"], 0.115)

    def test_hektisch_daempft_und_vorgabe_null_bleibt(self):
        self.konfig.daten["regie"]["vorgaben"] = {"effekt_staerke": {"chill": 0}, "effekt_hektik": 1.2}
        self.entwurf("chill", ["action"])
        self.entwurf("chill", ["hektisch"])
        p = self.p()
        self.assertEqual(p["effekt_staerke"], {"chill": 0.0})             # deine 0 heißt: ohne Effekte
        self.assertEqual(p["effekt_hektik"], 1.08)                        # 1,2 × 0,9
        for _ in range(15):
            self.entwurf("episch", ["hektisch"])
        self.assertEqual(self.p()["effekt_hektik"], 0.3)                  # Untergrenze
        self.konfig.daten["regie"]["vorgaben"] = {"effekt_staerke": {"lustig": 0.05}}
        self.entwurf("lustig", ["effekte_viel"])                          # unter 0,1: 🎆 hebt nie an
        self.assertEqual(self.p()["effekt_staerke"]["lustig"], 0.05)

    def test_regression_alte_bewertungen(self):
        for n, (energie, bpm) in enumerate(((0.4, 100.0), (0.8, 140.0)), 1):
            self.con.execute("INSERT INTO tracks (datei, titel, kuenstler, quelle, sha256, dauer_s, bpm, energie, "
                             "beats, verlauf, stimmungen, erstellt) VALUES (?, 'T', 'K', 'CC0', ?, 200, ?, ?, '[]', "
                             "'[]', '[]', 'x')", (f"t{n}.mp3", f"s{n}", bpm, energie))
        for stimmung, momente, daumen, gruende, musik in self.ALTE_FOLGE:
            self.entwurf(stimmung, gruende, daumen, momente, 2 if musik else None)
        p, ziel = regie_lernen.aktuelle(self.con, self.konfig)
        alt = {"abwechslung": 0.7, "beats_pro_schnitt": 2, "dauer_faktor": 0.9, "luecke_max_s": 4.0,
               "max_je_match": 3, "moment_bonus": {"a": -0.5, "b": 1.0, "c": -0.5, "e": 0.5, "f": 0.5},
               "musik_pegel": 0.35, "puffer_nach_s": 1.8, "puffer_vor_s": 3.0, "seg_min_faktor": 1.322,
               "stimmung_bonus": {"chill": 0.25, "episch": -0.25, "lustig": 0.5, "spannend": -0.25},
               "track_malus": {"2": 1.0}, "uebergang_faktor": 1.21}
        self.assertEqual({k: v for k, v in p.items() if k in alt}, alt)
        self.assertEqual(set(p) - set(alt), {"effekt_staerke", "effekt_hektik", "cooldown_entwuerfe", "frische_quote"})
        self.assertEqual((p["effekt_staerke"], p["effekt_hektik"]), ({}, 0.81))   # zweimal „zu hektisch“
        self.assertEqual(ziel["lustig"], {"energie": 0.64, "bpm": 117.6})

    def test_lernstand_zeigt_effekte(self):
        self.konfig.daten["regie"]["effekte"]["an"] = True                 # MitRegieMaterial schaltet sie aus
        self.konfig.daten["regie"]["vorgaben"] = {"effekt_staerke": {"chill": 0.5, "lustig": 0.8}}
        self.entwurf("episch", ["effekte_viel", "hektisch"])
        self.entwurf("lustig", ["action"])

        def zeile():
            return [z for z in regie_lernen.lernstand_text(self.con, self.konfig).splitlines()
                    if z.startswith("Effekte:")]

        self.assertEqual(zeile(), ["Effekte: episch 0.85 · spannend 1.0 · lustig 0.92 (Vorgabe 0.8) · "
                                   "frustriert 1.0 · chill 0.5 (deine Vorgabe) · Hektik 0.9"])
        self.konfig.daten["regie"]["effekte"]["an"] = False
        self.assertTrue(zeile()[0].endswith("Hektik 0.9 – ausgeschaltet ([regie.effekte] an = false)"))

    def test_cli_gruende_aus_einer_quelle(self):
        import argparse

        from clip_pipeline import cli

        unter = next(a for a in cli.baue_parser()._actions if isinstance(a, argparse._SubParsersAction))
        grund = next(a for a in unter.choices["bewerte"]._actions if a.dest == "grund")
        self.assertEqual(set(grund.choices), set(regie_lernen.GRUENDE))
