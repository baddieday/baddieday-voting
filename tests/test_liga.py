"""Mehrbenutzer, Stufe 5, PR 1 (M178–M185): Regie-Liga – bester Aufbau nur mit Beleg, reine Rechnung, nur lesen.

Abnahme (Florian): „Benutzer können nachvollziehen, was das System ausprobiert und tatsächlich gelernt hat.“
Echter Weg wie tests/test_erfolg.py (post_anlegen, speichere_messung, bewerte_alle → eingefrorene Wochen-Note), aber
Woche für Woche wie auf dem Mini: je Woche 3 Shorts (Mo, Mi, Fr), Messungen an Tag 3 und 7, sonntags früh frieren die
fälligen Wochen-Noten ein, abends um 18 Uhr ist Stichtag. Feste Zahlen, kein Zufall – kein Test kann flackern.
"""

from __future__ import annotations

import contextlib
import io
import json
from datetime import datetime, timedelta
from unittest import mock

from clip_pipeline import autonom, cli, db, erfolg, geschmack, liga, publikum
from clip_pipeline.zeit import UTC, aus_iso, iso

from tests.hilfen import MitSpeicher

START = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)     # Sonntag 10 Uhr Ortszeit – Wochen-Noten vor dem Stichtag (18 Uhr)
AUFBAUTEN = ("story", "montage", "kino", "steigerung")
ZONE = "Europe/Berlin"


class Liga(MitSpeicher):
    def setUp(self):
        super().setUp()
        self.konfig.daten["publikum"].update(plattformen=["tiktok"], alter_tage=7, mindest_alter_tage=3, fenster=20)
        self.konfig.daten["publikum"]["gewichte"] = {"wiedergabe": 0.5, "engagement": 0.3, "reichweite": 0.2}
        self.konfig.daten["erfolg"] = {"gewichte": {"zuschauer": 0.5, "follower": 0.2, "webseite": 0.3}}
        self.konfig.daten.setdefault("zeit", {})["zeitzone"] = ZONE
        self.sonntage: list[datetime] = []
        self.eids: list[int] = []
        self.offen: list[tuple] = []

    def neu(self) -> None:
        """Leere Datenbank für den nächsten Fall."""
        self.tearDown()
        self.setUp()

    def entwurf(self, i: int, aufbau: str, zeit: datetime, ersetzt: int | None = None) -> int:
        """Jedes zweite Video probiert den Aufbau bewusst (geschmack.experiment), jedes siebte lässt das
        Publikums-Modell die Musik lauter stellen (autonom.exploration)."""
        p = {"stil": aufbau, "geschmack": {"aufbau": aufbau, "tempo": ("schnell", "ruhig")[(i // 4) % 2],
                                          "zeitlupe": ("viel", "wenig")[(i // 8) % 2],
                                          "experiment": "aufbau" if i % 2 else None}}
        if i % 7 == 6:
            p["autonom"] = {"version": 0, "exploration": {"variable": "musik_pegel", "vorher": 0.3, "wert": 0.5}}
        if ersetzt is not None:                      # neue Fassung nach ❌
            p["ersetzt"] = [ersetzt]
        return self.con.execute("""INSERT INTO entwuerfe (name, format, schnittliste, parameter, status, datei, erstellt)
                                   VALUES (?, 'short', '/gibt/es/nicht.json', ?, 'gesendet', '/x.mp4', ?)""",
                                (f"e{i}", json.dumps(p), iso(zeit))).lastrowid

    def post(self, eid: int, plattform: str, zeit: datetime) -> int:
        liste = {"format": "short", "dauer_s": 55.0, "parameter": {}, "segmente": []}
        return publikum.post_anlegen(self.con, art="entwurf", ziel_id=eid, plattform=plattform, zeit=zeit,
                                     daten={"dauer_s": 55.0, "schnittliste": liste, "merkmale": {"momente": []},
                                            "rezept": {}})[0]

    @staticmethod
    def werte(i: int, faktor: float) -> dict:
        views = 800 + (i * 137) % 600
        return {"views": views, "likes": int(views * (0.045 + 0.002 * (i % 3)) * faktor),
                "kommentare": int(views * 0.003 * faktor), "shares": int(views * 0.004 * faktor)}

    def messen(self, pid: int, t: datetime, werte: dict) -> None:
        self.offen += [(t + timedelta(days=3, hours=1), pid, {f: int(v * 0.7) for f, v in werte.items()}),
                       (t + timedelta(days=7, hours=1), pid, werte)]

    def wochen(self, anzahl: int, faktor=None, messen: bool = True) -> None:
        """anzahl Wochen à 3 Shorts (Aufbau reihum, faktor(i, aufbau) = Reaktionen ×); ohne faktor nur Sonntage ohne
        neue Videos. messen=False: niemand holt Zahlen ab (Freund)."""
        with mock.patch.object(autonom, "aktualisieren", return_value={}):
            for _ in range(anzahl):
                sonntag = START + timedelta(days=7 * (len(self.sonntage) + 1))
                for tag in ((1, 3, 5) if faktor else ()):
                    i, t = len(self.eids), sonntag - timedelta(days=7 - tag)
                    aufbau = AUFBAUTEN[i % 4]
                    with db.transaktion(self.con):
                        self.eids.append(eid := self.entwurf(i, aufbau, t))
                        pid = self.post(eid, "tiktok", t)
                    if messen:
                        self.messen(pid, t, self.werte(i, faktor(i, aufbau)))
                self.offen.sort(key=lambda x: x[0])
                while self.offen and self.offen[0][0] <= sonntag:
                    zeit, pid, werte = self.offen.pop(0)
                    with db.transaktion(self.con):
                        publikum.speichere_messung(self.con, pid, werte, "api", zeit=zeit, konfig=self.konfig)
                with mock.patch.object(publikum, "jetzt", return_value=sonntag):
                    publikum.bewerte_alle(self.con, self.konfig, sonntag)
                self.sonntage.append(sonntag)

    def stichtag(self, woche: int) -> datetime:
        """Sonntag 18 Uhr Ortszeit am Ende von Woche `woche` (1 = erste Woche)."""
        return liga.letzter_stichtag(self.sonntage[woche - 1] + timedelta(hours=12), ZONE)

    def stand(self, woche: int) -> dict:
        return liga.stand(self.con, self.konfig, self.stichtag(woche))

    def vorliebe_fuer_montage(self) -> None:
        """KI-Noten (mag „schnelle Montage“) und deine ✅ für Montage, ❌ für den Rest – beides zählt nie als Beleg."""
        with db.transaktion(self.con):
            for i, eid in enumerate(self.eids):
                montage = AUFBAUTEN[i % 4] == "montage"
                self.con.execute("INSERT INTO kritiken (entwurf_id, score, regel_score, ki_score, details, erstellt) "
                                 "VALUES (?, 70, 70, ?, '{}', ?)", (eid, 95 if montage else 10, iso(START)))
                self.con.execute("INSERT INTO entwurf_bewertungen (entwurf_id, daumen, gruende, erstellt, geaendert) "
                                 "VALUES (?, ?, '[]', ?, ?)", (eid, 1 if montage else -1, iso(START), iso(START)))

    def cli(self, *argv: str) -> tuple[int, dict, str]:
        ausgabe, log = io.StringIO(), io.StringIO()
        with mock.patch("clip_pipeline.cli.lade", return_value=self.konfig), contextlib.redirect_stdout(ausgabe), \
                contextlib.redirect_stderr(log):
            code = cli.main(list(argv))
        zeilen = ausgabe.getvalue().splitlines()
        self.assertEqual(len(zeilen), 1, zeilen)                        # genau eine JSON-Zeile (Vertrag)
        return code, json.loads(zeilen[0]), log.getvalue()

    def test_echter_unterschied_wird_nach_zwei_sonntagen_bester_aufbau(self):
        self.wochen(16, lambda i, a: 2.0 if a == "story" else 1.0)    # 48 TikTok-Shorts, „erzählt“ doppelte Reaktionen
        self.wochen(2)                                                 # bis alle Wochen-Noten fest sind
        vorn, krone, ende = self.stand(14), self.stand(15), self.stand(18)

        # Am ersten Sonntag mit Beleg nur Kandidat – am nächsten bester Aufbau, seit genau diesem Stichtag
        self.assertEqual((vorn["bester"], vorn["kandidat"]["wahl"], vorn["liga_level"]), (None, "story", 2))
        self.assertEqual(vorn["ziel"], {"art": "vorn", "aufbau": "story", "gegen": "rest", "n": [8, 26]})
        self.assertEqual((krone["bester"], krone["seit"], krone["liga_level"], krone["liga_stufe"]),
                         ("story", iso(self.stichtag(15)), 3, "bester Aufbau belegt"))
        self.assertEqual((krone["ereignis"]["art"], krone["ereignis"]["n"], krone["ereignis"]["vorher"]),
                         ("bester", [9, 28], None))
        # Danach zählen nur Videos, die nach der Krönung hochgeladen wurden – die der Krönung verteidigen nie
        self.assertEqual(krone["ziel"], {"art": "herausforderer", "aufbau": "montage", "gegen": "story", "n": [0, 0],
                                         "fehlen": 16})
        self.assertEqual((krone["seit_kroenung"]["gesamt"], krone["vertrauen"]["kino"]["wort"]),
                         (0, "noch 16 Videos seit dem 14.06."))
        # Nachrechenbar: ein früherer Stichtag ist der Anfang desselben Verlaufs, nichts wird gespeichert
        self.assertEqual(ende["verlauf"][:1], vorn["verlauf"])
        self.assertEqual(ende["verlauf"], krone["verlauf"])
        self.assertEqual([(v["art"], v["aufbau"]) for v in ende["verlauf"]], [("vorn", "story"), ("bester", "story")])

        # Erfahrung = gezählte erfolg-Einheiten der Hauptplattform (48 − 5, die nur Vergleich sind)
        einheiten = erfolg.einheiten(self.con, self.konfig, self.sonntage[-1])["tiktok"]["einheiten"]
        self.assertEqual((ende["erfahrung"]["gesamt"], len(einheiten), ende["erfahrung"]["basis"]), (43, 43, [5, 5]))
        self.assertEqual(sum(ende["erfahrung"]["aufbau"].values()), 43)
        self.assertEqual(ende["level"]["aufbau"], {o: liga.level(n) for o, n in ende["erfahrung"]["aufbau"].items()})
        # Was ausprobiert wurde – mit einfachen Namen, auch der Versuch des Publikums-Modells
        self.assertEqual([v["name"] for v in vorn["versuche"]["liste"]],
                         ["Aufbau „Steigerung“", "Aufbau „schnelle Montage“", "Musik lauter"])
        self.assertEqual(self.stand(16)["versuche"]["herausforderer"], {"montage": 1, "kino": 1, "steigerung": 1})

        # Nichts davon ändert Erfahrung oder Krone: KI-Noten und ✅ für Montage, eine weitere Messung, eine Fassung
        # (deren Original schon zählt), ein YouTube-Crosspost und Sonntage ohne neue Zahlen
        self.vorliebe_fuer_montage()
        t = self.sonntage[-1] + timedelta(days=1)
        with db.transaktion(self.con):
            publikum.speichere_messung(self.con, publikum.post_zu(self.con, "entwurf", self.eids[-1], "tiktok")["id"],
                                       self.werte(47, 5.0), "api", zeit=t, konfig=self.konfig)
            fassung = self.entwurf(len(self.eids), "story", t, ersetzt=self.eids[8])
            self.messen(self.post(fassung, "tiktok", t), t, self.werte(0, 3.0))
            self.messen(self.post(self.eids[9], "youtube", t), t, {**self.werte(9, 3.0), "wiedergabe_s": 30.0,
                                                                     "follows": 2})
        self.wochen(4)
        spaeter = self.stand(22)
        for schluessel in ("bester", "seit", "verlauf", "liga_level"):
            self.assertEqual(spaeter[schluessel], ende[schluessel], schluessel)
        ohne_neu = {k: v for k, v in ende["erfahrung"].items() if k != "neu"}
        self.assertEqual({k: v for k, v in spaeter["erfahrung"].items() if k != "neu"}, ohne_neu)
        self.assertEqual(spaeter["nicht_gezaehlt"], {"Basis zu klein": 5, "weitere Fassung": 1})
        # Getrennt: Die Vorliebe, mit der der Bot wählt, zeigt jetzt auf Montage – belegt bleibt „erzählt“
        self.assertIn("öfter: Aufbau „schnelle Montage“", geschmack.wahl_zeile(geschmack.statistik(self.con)) or "")

        # pipeline erfolg: Abschnitt „Regie-Liga“ und Schlüssel liga, Exit wie bisher
        code, daten, text = self.cli("erfolg")
        self.assertEqual((code, daten["liga"]["bester"], daten["liga"]["version"], daten["version"]),
                         (0, "story", 1, 1))
        self.assertIn("Bester Aufbau: „erzählt“ – belegt seit 14.06.", text)
        self.assertNotIn("%", text.split("🥇 Regie-Liga")[1])                    # Vertrauen in Worten, nie Prozent

    def test_ohne_beleg_kein_bester_und_nur_neue_videos_loesen_ab(self):
        # 1. Nach der Krönung sind „erzählt“ und „Kino“ auf neuen Videos gleich gut: keine Ablösung – obwohl
        #    „gegen den Rest“ Kino belegt besser nennt (der 📊-Satz aus Stufe 4 würde hin und her wechseln)
        self.wochen(16, lambda i, a: 2.0 if a == "story" else 1.0)
        self.wochen(20, lambda i, a: 2.0 if a in ("story", "kino") else 1.0)
        st = self.stand(36)
        self.assertEqual([(v["art"], v["aufbau"]) for v in st["verlauf"] if v["art"] == "bester"], [("bester", "story")])
        self.assertEqual((st["bester"], st["vertrauen"]["kino"]["wort"]), ("story", "kein Unterschied sicher"))
        frisch = [e for e in erfolg.einheiten(self.con, self.konfig, self.sonntage[-1])["tiktok"]["einheiten"]
                  if aus_iso(e["gepostet"]) > aus_iso(st["seit"])]
        gegen_rest = {b["wahl"]: b["status"] for b in erfolg.vergleiche(frisch) if b["merkmal"] == "aufbau"}
        self.assertEqual(gegen_rest["kino"], erfolg.BESSER)

        # 2. Starke Videos eines anderen Aufbaus vor der Krönung lösen nicht ab: erst „erzählt“ ×2 und „Kino“ ×1,8,
        #    danach alle gleich – auch Kinos Seite zählt nur Videos seit der Krönung
        self.neu()
        self.wochen(16, lambda i, a: {"story": 2.0, "kino": 1.8}.get(a, 1.0))
        self.wochen(20, lambda i, a: 1.0)
        st = self.stand(36)
        self.assertEqual([(v["art"], v["aufbau"]) for v in st["verlauf"]], [("vorn", "story"), ("bester", "story")])
        seit = st["seit_kroenung"]["aufbau"]
        self.assertEqual(st["vertrauen"]["kino"]["n"], [seit["kino"], seit["story"]])

        # 3. Dieselben Videos ohne Effekt, dazu KI-Noten und ✅ für Montage: kein bester Aufbau, Level höchstens 2,
        #    und Zeichen für Zeichen dieselbe Liga wie ohne KI-Noten und ✅
        self.neu()
        self.wochen(16, lambda i, a: 1.0)
        self.wochen(2)
        ohne = self.stand(18)
        self.vorliebe_fuer_montage()
        mit = self.stand(18)
        self.assertEqual(mit, ohne)
        self.assertEqual((mit["bester"], mit["kandidat"], mit["verlauf"], mit["liga_level"], mit["erfahrung"]["gesamt"]),
                         (None, None, [], 2, 43))

        # 4. Freund ohne Zahlenabruf: ausprobiert wird trotzdem, Erfahrung 0, kein bester Aufbau
        self.neu()
        self.konfig.daten["instanz"] = {"wurzel": str(self.tmp), "name": "max"}
        self.wochen(16, lambda i, a: 2.0 if a == "story" else 1.0, messen=False)
        st = self.stand(16)
        self.assertEqual((st["abruf"], st["bester"], st["erfahrung"]["gesamt"], st["liga_level"], st["messungen"]),
                         (erfolg.FREUND_OHNE_ABRUF, None, 0, 1, 0))
        self.assertEqual([(v["name"], v["gezeigt"], v["hochgeladen"], v["mit_zahlen"]) for v in st["versuche"]["liste"]],
                         [("Aufbau „schnelle Montage“", 1, 1, 0), ("Aufbau „Steigerung“", 1, 1, 0)])
        self.assertEqual(st["ziel"], {"art": "erster_vergleich", "fehlen": 21})   # 16 + die 5 nur zum Vergleich

        # 5. Ein Fehler der Liga kostet pipeline erfolg nichts: liga.fehler, Log, übrige Ausgabe und Exit bleiben
        with mock.patch.object(liga, "stand", side_effect=RuntimeError("kaputt")):
            code, daten, text = self.cli("erfolg")
        self.assertEqual((code, daten["liga"], daten["plattformen"]["tiktok"]["einheiten"]),
                         (0, {"fehler": "RuntimeError: kaputt"}, 0))
        self.assertIn("Regie-Liga: nicht gerechnet", text)
