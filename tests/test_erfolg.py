"""Mehrbenutzer, Stufe 4, PR 1 (M155–M164): `pipeline erfolg` – Ziele getrennt, belegter Strategievergleich, nur lesen.

Abnahme (Florian): „Das System zeigt belegbare Unterschiede zwischen Strategien, ohne fehlende Daten zu erfinden.“
Die Zahlen gehen den echten Weg (post_anlegen, speichere_messung, bewerte_alle → eingefrorene Wochen-Note); nur das
Nachlernen bei jeder Messung (autonom.aktualisieren) ist abgeschaltet – es spielt für erfolg keine Rolle. Alle Zahlen
sind fest (kein Zufall), so kann kein Test flackern.
"""

from __future__ import annotations

import contextlib
import io
import json
from datetime import datetime, timedelta
from unittest import mock

from clip_pipeline import autonom, cli, db, erfolg, geschmack, publikum, publikum_adapter
from clip_pipeline.zeit import UTC, iso

from tests.hilfen import MitSpeicher

START = datetime(2026, 3, 2, 18, 0, tzinfo=UTC)
AUFBAUTEN = ("story", "montage", "kino", "steigerung")


class Erfolg(MitSpeicher):
    def setUp(self):
        super().setUp()
        self.konfig.daten["publikum"].update(plattformen=["tiktok"], alter_tage=7, mindest_alter_tage=3, fenster=20)
        self.konfig.daten["publikum"]["gewichte"] = {"wiedergabe": 0.5, "engagement": 0.3, "reichweite": 0.2}
        self.konfig.daten["erfolg"] = {"gewichte": {"zuschauer": 0.5, "follower": 0.2, "webseite": 0.3}}

    def entwurf(self, i: int, aufbau: str, zeit: datetime, ersetzt: int | None = None) -> int:
        p = {"stil": aufbau, "geschmack": {"aufbau": aufbau, "tempo": "schnell", "zeitlupe": "wenig",
                                          "experiment": None}}
        if ersetzt is not None:                      # neue Fassung nach ❌ (lernbot: szenen.ersetzt_kette)
            p["ersetzt"] = [ersetzt]
        return self.con.execute("""INSERT INTO entwuerfe (name, format, schnittliste, parameter, status, datei, erstellt)
                                   VALUES (?, 'short', '/gibt/es/nicht.json', ?, 'gesendet', '/x.mp4', ?)""",
                                (f"e{i}", json.dumps(p), iso(zeit))).lastrowid

    def post(self, eid: int, plattform: str, zeit: datetime) -> int:
        liste = {"format": "short", "dauer_s": 55.0, "parameter": {}, "segmente": []}
        return publikum.post_anlegen(self.con, art="entwurf", ziel_id=eid, plattform=plattform, zeit=zeit,
                                     daten={"dauer_s": 55.0, "schnittliste": liste, "merkmale": {"momente": []},
                                            "rezept": {}})[0]

    def messen(self, pid: int, zeit: datetime, werte: dict) -> None:
        for tag, anteil in ((3, 0.7), (7, 1.0)):     # zwei Messungen je Post – zählen darf nur die eingefrorene
            w = {f: int(v * anteil) if isinstance(v, int) else v for f, v in werte.items()}
            publikum.speichere_messung(self.con, pid, w, "api", zeit=zeit + timedelta(days=tag, hours=1),
                                       konfig=self.konfig)

    def videos(self, n: int, *, youtube=(), fassung: tuple[int, int] | None = None) -> datetime:
        """n TikTok-Shorts, Aufbau reihum; „erzählt“ (story) hat doppelte Reaktionen je View. youtube: diese auch auf
        YouTube (mit Wiedergabe und Followern); fassung (i, j): Video i ist eine neue Fassung von Video j."""
        eids = []
        with mock.patch.object(autonom, "aktualisieren", return_value={}):
            for i in range(n):
                aufbau = AUFBAUTEN[i % 4]
                t = START + timedelta(days=2 * i)
                faktor = 2.0 if aufbau == "story" else 1.0
                views = 800 + (i * 137) % 600
                werte = {"views": views, "likes": int(views * (0.045 + 0.002 * (i % 3)) * faktor),
                         "kommentare": int(views * 0.003 * faktor), "shares": int(views * 0.004 * faktor)}
                with db.transaktion(self.con):
                    eid = self.entwurf(i, aufbau, t, eids[fassung[1]] if fassung and fassung[0] == i else None)
                    eids.append(eid)
                    self.messen(self.post(eid, "tiktok", t), t, werte)
                    if i in youtube:   # Crosspost: auf YouTube eine eigene Einheit, nie mit TikTok zusammen
                        self.messen(self.post(eid, "youtube", t), t, {**werte, "wiedergabe_s": 30.0, "follows": 2})
        ende = START + timedelta(days=2 * n + 8)
        with mock.patch.object(publikum, "jetzt", return_value=ende):
            publikum.bewerte_alle(self.con, self.konfig, ende)
        return ende

    def cli(self, *argv: str) -> tuple[int, dict]:
        ausgabe = io.StringIO()
        with mock.patch("clip_pipeline.cli.lade", return_value=self.konfig), contextlib.redirect_stdout(ausgabe), \
                contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(list(argv))
        zeilen = ausgabe.getvalue().splitlines()
        self.assertEqual(len(zeilen), 1, zeilen)                        # genau eine JSON-Zeile (Vertrag)
        return code, json.loads(zeilen[0])

    def test_bekannter_unterschied_ist_belegt_und_fehlendes_bleibt_nicht_gemessen(self):
        ende = self.videos(48)
        b = erfolg.auswertung(self.con, self.konfig, ende)
        self.assertEqual((b["version"], b["m"], len(erfolg.VERGLEICHE)), (1, 9, 9))
        tiktok = b["plattformen"]["tiktok"]
        story = [f for f in tiktok["befunde"] if (f["merkmal"], f["wahl"]) == ("aufbau", "story")]
        self.assertEqual([f["status"] for f in story], ["belegt besser"])          # nie die Gegenrichtung
        self.assertGreater(story[0]["von"], 0)
        # TikTok-API liefert weder Wiedergabezeit noch Follower je Video, clip-battle.de zählt noch nicht: nie eine 0
        for stand in (tiktok["teilziele"]["bindung"], tiktok["ziele"]["follower"], tiktok["ziele"]["webseite"]):
            self.assertEqual(stand["status"], "nicht gemessen")
            self.assertTrue(stand["grund"])
        self.assertEqual(tiktok["vergleich"]["ziele"], ["zuschauer"])          # heute: Gesamt = Zuschauer

        # Zuschauer aus den eingefrorenen Teilen = posts.score (bei unveränderten [publikum.gewichte])
        score = {z["id"]: z["score"] for z in self.con.execute("SELECT id, score FROM posts")}
        einheiten = erfolg.einheiten(self.con, self.konfig, ende)["tiktok"]["einheiten"]
        self.assertEqual(len(einheiten), 43)                                      # 48 − 5 × „Basis zu klein“
        for e in einheiten:
            self.assertAlmostEqual(e["werte"]["zuschauer"], score[e["post"]], places=3)

        # KI-Noten (hier stark für „schnelle Montage“) ändern nichts: Belege kommen nur aus Publikumszahlen
        for (eid,) in self.con.execute("SELECT id FROM entwuerfe").fetchall():
            self.con.execute("INSERT INTO kritiken (entwurf_id, score, regel_score, ki_score, details, erstellt) "
                             "VALUES (?, 70, 70, ?, '{}', ?)", (eid, 95 if eid % 4 == 2 else 10, iso(ende)))
        self.assertEqual(erfolg.auswertung(self.con, self.konfig, ende), b)

        zeile = erfolg.zeile_einfach(b)
        self.assertIn("📊 Belegt (TikTok, 43 Videos): Aufbau „erzählt“ kommt bei den Zuschauern besser an als die "
                      "anderen Aufbauten (10 gegen 33 Videos) – sehr wahrscheinlich kein Zufall.", zeile)
        self.assertIn("Noch nicht gemessen: wie lange geschaut wird, neue Follower, Besuche auf clip-battle.de.", zeile)
        # Sonntagsbericht (Schritt 2): dieselben Zeilen statt „👀 Bei den Zuschauern …“. Was der Bot nach den KI-Noten
        # gerade öfter wählt (hier „schnelle Montage“), heißt so (🎯) – belegt ist nur, was die Zuschauer zeigen
        bericht = geschmack.wochen_text(self.con, self.konfig, START + timedelta(days=2 * 47 + 1))
        self.assertIn(zeile, bericht)
        self.assertIn("🎯 Wähle ich gerade öfter: Aufbau „schnelle Montage“", bericht)
        for behauptung in ("👀", "Kommt gut an", "Kommt weniger an"):
            self.assertNotIn(behauptung, bericht)

    def test_zu_wenig_videos_behauptet_nichts_und_kaputte_gewichte_enden_mit_exit_2(self):
        ende = self.videos(9, youtube=(6, 7, 8), fassung=(8, 6))
        with db.transaktion(self.con):   # ein Video von vorgestern: noch ohne Wochenzahlen
            self.post(self.entwurf(99, "kino", ende - timedelta(days=2)), "tiktok", ende - timedelta(days=2))
        with mock.patch.object(publikum_adapter, "tiktok_verbunden", return_value=False):
            b = erfolg.auswertung(self.con, self.konfig, ende)
        tiktok, youtube = b["plattformen"]["tiktok"], b["plattformen"]["youtube"]
        self.assertFalse([f for p in b["plattformen"].values() for f in p["befunde"] if f["status"].startswith("belegt")])
        # „Basis zu klein“ ist keine Messung (5 TikTok + 3 YouTube); die Fassung zählt einmal, der Crosspost je Plattform
        self.assertEqual(tiktok["nicht_gezaehlt"], {"Basis zu klein": 5, "weitere Fassung": 1})
        self.assertEqual(youtube["nicht_gezaehlt"], {"Basis zu klein": 3})
        self.assertEqual((tiktok["einheiten"], tiktok["wartet"], youtube["einheiten"]), (3, 1, 0))
        self.assertEqual(youtube["ziele"]["zuschauer"]["status"], "zu wenig Vergleich")
        self.assertEqual(b["abruf"], "TikTok nicht verbunden – einmal /tiktok")
        self.assertEqual(erfolg.zeile_einfach(b).splitlines()[0], "📊 Zuschauer (TikTok): 3 Videos mit fertigen "
                         "Wochenzahlen – ein erster Vergleich frühestens nach 13 weiteren.")
        # Freund: niemand holt seine Zahlen ab – pipeline erfolg sagt es, der Wochenbericht schweigt (M169)
        self.konfig.daten["instanz"] = {"wurzel": str(self.tmp), "name": "max"}
        freund = erfolg.auswertung(self.con, self.konfig, ende)
        self.assertEqual(freund["abruf"], "Zuschauerzahlen werden bei dir noch nicht abgeholt")
        self.assertIsNone(erfolg.zeile_einfach(freund))
        del self.konfig.daten["instanz"]

        code, daten = self.cli("erfolg")
        self.assertEqual((code, daten["version"], daten["plattformen"]["tiktok"]["einheiten"]), (0, 1, 3))
        self.konfig.daten["erfolg"]["gewichte"]["zuschauer"] = -1
        code, daten = self.cli("erfolg")
        self.assertEqual((code, daten["fehler"]), (2, "konfig"))
        self.assertIn("[erfolg.gewichte].zuschauer", daten["hinweis"])
