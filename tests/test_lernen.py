"""Tests für Paket D der Stufe 2: Lernen aus drei Paar-Quellen, getrennte Trefferquoten, /gewichte (Spec §8.3).

Was hier geprüft wird:
  - Jedes Paar trägt eine Quelle und ein Gewicht (Battle 1,0 · Freigabe [lernen].gewicht_freigabe · Publikum 1,0).
  - Publikums-Paare: nur echte Scores (ohne „Basis zu klein“), gleiche Plattform und Art, genug Abstand, nicht
    derselbe Moment, höchstens [publikum].max_paare jüngste.
  - „Fehlt = unbekannt“ (Annahme S2-A17): ein neues Merkmal, das auf einer Seite fehlt, wird nicht verglichen.
  - Zwei Quoten (du · Publikum); die Schranke prüft die Publikums-Quote erst ab [lernen].mindest_publikum_paare.
  - /gewichte (Bot und CLI) zeigt beide Quoten, die Paare je Quelle und die Clips ohne Mic-Analyse.
  - `pipeline publikum bewerten` lernt danach neu (Annahme S2-A12).

Posts werden direkt per SQL angelegt (kein Umweg über den Lern-Bot); alle Zeiten sind fest.
"""

from __future__ import annotations

import contextlib
import io
import json
from datetime import datetime, timedelta
from unittest import mock

from clip_pipeline import cli, lernen, publikum
from clip_pipeline import merkmale as merkmal_modul
from clip_pipeline.bot import texte
from clip_pipeline.lernen import Paar
from clip_pipeline.vorbewertung import MERKMAL_NAMEN, MERKMALE, roh_score
from clip_pipeline.zeit import UTC, iso

from tests.hilfen import MitSpeicher

T0 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def alt(kill: float = 1.0, laut: float = 0.0, **neu: float) -> dict:
    """Merkmale wie vor Stufe 2 (die alten fünf), dazu wahlweise neue Schlüssel."""
    return {"kill_punkte": kill, "victory_royale": 0.0, "laenge": 0.0, "lautstaerke": laut, "kommentar": 0.0, **neu}


class MitPosts(MitSpeicher):
    """Speicher mit festen Lern-Einstellungen (unabhängig von einer lokalen Konfiguration) und Post-Helfern."""

    def setUp(self):
        super().setUp()
        self.konfig.daten["lernen"].update(mindestens=20, voll_vertrauen=60, schritt=0.05, durchlaeufe=5,
                                           leine_anteil=0.5, leine_minimum=0.5, max_paare_pro_abend=20,
                                           gewicht_freigabe=0.5, mindest_publikum_paare=10)
        self.konfig.daten["publikum"].update(paar_abstand=0.5, max_paare=200)

    def post(self, *, score: float | None, art: str = "clip", clip_id: int | None = None,
             entwurf_id: int | None = None, plattform: str = "tiktok", gepostet: datetime = T0,
             merkmale: dict | None = None, vermerke: list[str] | None = None, bewertet: bool = True) -> int:
        """Ein Post per SQL. Clip-Post: merkmale wie clip_post_daten (Hook = der Clip). Entwurf-Post: merkmale
        muss übergeben werden (momente + hook_moment)."""
        if art == "clip":
            ziel_id = clip_id
            moment = f"clip:{clip_id}"
            zeile = self.con.execute("SELECT merkmale FROM clips WHERE id = ?", (clip_id,)).fetchone()
            eingefroren = json.loads(zeile["merkmale"]) if zeile else {}
            merkmale = merkmale or {"momente": [{"moment": moment, "clip_id": clip_id, "merkmale": eingefroren}],
                                    "hook_moment": moment}
        else:
            ziel_id = entwurf_id
        teile = json.dumps({"vermerke": vermerke or []})
        cursor = self.con.execute(
            """INSERT INTO posts (art, ziel, clip_id, entwurf_id, plattform, gepostet_utc, dauer_s, rezept, merkmale,
                                  experiment, score, score_teile, bewertet_utc, erstellt)
               VALUES (?, ?, ?, ?, ?, ?, 20.0, '{}', ?, 0, ?, ?, ?, ?)""",
            (art, f"{art}:{ziel_id}", clip_id if art == "clip" else None, entwurf_id if art == "entwurf" else None,
             plattform, iso(gepostet), json.dumps(merkmale), score if bewertet else None,
             teile if bewertet else None, iso(gepostet + timedelta(days=7)) if bewertet else None, iso(gepostet)))
        return int(cursor.lastrowid)

    def clip_post(self, merkmale: dict, score: float, *, tag: int = 0, **extra) -> tuple[int, int]:
        """Clip (Status gesendet – zählt nicht als Freigabe-Paar) plus Post. Rückgabe (clip_id, post_id)."""
        cid = self.clip_anlegen(status="gesendet", merkmale=merkmale, match_id=f"p{tag}",
                                start=datetime(2026, 8, 1, 19, 0, tzinfo=UTC) + timedelta(days=tag))
        return cid, self.post(score=score, clip_id=cid, gepostet=T0 + timedelta(days=tag), **extra)

    def entwurf_post(self, entwurf_id: int, hook: str, momente: list[dict], score: float, *, tag: int = 0) -> int:
        return self.post(score=score, art="entwurf", entwurf_id=entwurf_id, gepostet=T0 + timedelta(days=tag),
                         merkmale={"momente": momente, "hook_moment": hook})

    def abend(self, tag: int, anzahl: int) -> None:
        """Du gibst immer den lauteren Clip frei – bei gleichen Kills (wie test_elo_lernen_bot)."""
        start = datetime(2026, 9, tag, 19, 0, tzinfo=UTC)
        for i in range(anzahl):
            laut = i % 2 == 0
            self.clip_anlegen(status="freigegeben" if laut else "verworfen", start=start + timedelta(minutes=i),
                              merkmale=alt(3.0, 0.9 if laut else 0.1), match_id=f"m{tag}")

    def battle(self, gewinner: int, verlierer: int) -> None:
        self.con.execute("INSERT INTO battles (clip_a, clip_b, erstellt, entschieden, ergebnis) VALUES (?, ?, ?, ?, 'a')",
                         (gewinner, verlierer, iso(T0), iso(T0)))


# --- Paare aus drei Quellen ----------------------------------------------------------------------------------

class DreiQuellen(MitPosts):
    def test_quellen_und_gewichte(self):
        self.abend(1, 4)                                   # 2 freigegeben × 2 verworfen = 4 Freigabe-Paare
        a = self.clip_anlegen(status="gesendet", merkmale=alt(3.0), match_id="b1")
        b = self.clip_anlegen(status="gesendet", merkmale=alt(1.0), match_id="b1")
        self.battle(a, b)
        self.clip_post(alt(1.0), 1.0, tag=1)
        self.clip_post(alt(3.0), 0.0, tag=2)
        self.konfig.daten["lernen"]["mindestens"] = 1
        with mock.patch.object(lernen, "trainiere", wraps=lernen.trainiere) as spion:
            e = lernen.berechne(self.con, self.konfig)
        self.assertEqual(e.paare_je_quelle, {"battle": 1, "freigabe": 4, "publikum": 1})
        # Datenbasis = 4 Freigaben/Verwerfungen + 1 Battle + 2 Posts in Publikums-Paaren (die Battle-Clips und
        # Post-Clips sind „gesendet“, also keine Freigabe)
        self.assertEqual((e.datenbasis, e.freigaben, e.battles), (7, 4, 1))
        self.assertEqual({(p.art, p.gewicht) for p in spion.call_args.args[0]},
                         {("battle", 1.0), ("freigabe", 0.5), ("publikum", 1.0)})

    def test_freigabe_gewicht_kommt_aus_der_konfig(self):
        self.abend(1, 4)
        self.konfig.daten["lernen"]["gewicht_freigabe"] = 0.25
        with mock.patch.object(lernen, "trainiere", wraps=lernen.trainiere) as spion:
            self.konfig.daten["lernen"]["mindestens"] = 1
            lernen.berechne(self.con, self.konfig)
        paare = spion.call_args.args[0]
        self.assertEqual({p.gewicht for p in paare if p.art == "freigabe"}, {0.25})

    def test_reihenfolge_battles_freigaben_publikum(self):
        self.abend(1, 2)
        a = self.clip_anlegen(status="gesendet", merkmale=alt(3.0), match_id="b1")
        b = self.clip_anlegen(status="gesendet", merkmale=alt(1.0), match_id="b1")
        self.battle(a, b)
        self.clip_post(alt(1.0), 1.0, tag=1)
        self.clip_post(alt(3.0), 0.0, tag=2)
        self.konfig.daten["lernen"]["mindestens"] = 1
        with mock.patch.object(lernen, "trainiere", wraps=lernen.trainiere) as spion:
            lernen.berechne(self.con, self.konfig)
        self.assertEqual([p.art for p in spion.call_args.args[0]], ["battle", "freigabe", "publikum"])


# --- trainiere und trefferquote --------------------------------------------------------------------------------

class Trainieren(MitPosts):
    def einstellungen(self, durchlaeufe: int = 1) -> dict:
        return {"schritt": 0.1, "leine_anteil": 0.5, "leine_minimum": 0.5, "durchlaeufe": durchlaeufe}

    def test_schritt_mal_gewicht(self):
        start = dict.fromkeys(MERKMALE, 0.0)
        paar = {"besser": {"lautstaerke": 1.0}, "schlechter": {"lautstaerke": 0.0}}
        voll = lernen.trainiere([Paar(**paar, art="battle", gewicht=1.0)], start, self.einstellungen())
        halb = lernen.trainiere([Paar(**paar, art="freigabe", gewicht=0.5)], start, self.einstellungen())
        self.assertAlmostEqual(voll["lautstaerke"], 0.1)
        self.assertAlmostEqual(halb["lautstaerke"], 0.05)

    def test_marge_eins(self):
        """Ein Paar, das schon mit Abstand ≥ 1 richtig sortiert ist, ändert nichts."""
        start = dict.fromkeys(MERKMALE, 0.0) | {"kill_punkte": 1.0}
        klar = Paar({"kill_punkte": 3.0}, {"kill_punkte": 1.0}, "battle")        # Abstand 2 ≥ Marge
        self.assertEqual(lernen.trainiere([klar], start, self.einstellungen()), start)
        self.assertEqual(lernen.MARGE, 1.0)

    def test_mic_lachen_nur_auf_einer_seite_bleibt(self):
        """„Fehlt = unbekannt“: analysiert gegen nicht analysiert lernt nichts über mic_lachen."""
        start = {m: float(self.konfig.wert(f"vorbewertung.startgewichte.{m}", 0.0)) for m in MERKMALE}
        paar = Paar(alt(1.0, 0.5, mic_lachen=3.0), alt(1.0, 0.0), "battle")
        w = lernen.trainiere([paar], start, self.einstellungen(durchlaeufe=5))
        self.assertEqual(w["mic_lachen"], start["mic_lachen"])
        self.assertGreater(w["lautstaerke"], start["lautstaerke"])  # das Bekannte wird trotzdem gelernt

    def test_mic_lachen_auf_beiden_seiten_wird_gelernt(self):
        start = {m: float(self.konfig.wert(f"vorbewertung.startgewichte.{m}", 0.0)) for m in MERKMALE}
        paar = Paar(alt(1.0, mic_lachen=0.5), alt(1.0, mic_lachen=0.0), "battle")   # Vorsprung 0,5 < Marge
        w = lernen.trainiere([paar], start, self.einstellungen())
        self.assertGreater(w["mic_lachen"], start["mic_lachen"])

    def test_alte_merkmale_fehlend_zaehlen_null(self):
        """kill_punkte, victory_royale, kommentar wie vor Stufe 2: fehlt = 0 (nicht „unbekannt“).
        Befund K-3: laenge und lautstaerke gelten seitdem als unbekannt, wenn sie fehlen (Datei-Momente) – das
        Beispiel nimmt deshalb kommentar statt lautstaerke."""
        start = dict.fromkeys(MERKMALE, 0.0)
        w = lernen.trainiere([Paar({"kommentar": 1.0}, {}, "battle")], start, self.einstellungen())
        self.assertAlmostEqual(w["kommentar"], 0.1)

    def test_laenge_und_lautstaerke_fehlend_werden_nicht_verglichen(self):
        """Befund K-3: Ein Datei-Moment hat kein laenge/lautstaerke – das ist unbekannt, nicht 0."""
        datei = {"kill_punkte": 3.0, "victory_royale": 0.0, "mic_lachen": 1.0}
        clip_hook = alt(1.0, 0.8) | {"laenge": 1.5}
        d = lernen.differenz(Paar(datei, clip_hook, "publikum"))
        self.assertNotIn("laenge", d)
        self.assertNotIn("lautstaerke", d)
        self.assertEqual(d["kill_punkte"], 2.0)
        # Clip gegen Clip (Battles, Freigaben): beide Schlüssel stehen immer da und werden verglichen
        d = lernen.differenz(Paar(alt(1.0, 0.9) | {"laenge": 1.0}, alt(1.0, 0.1) | {"laenge": 1.5}, "battle"))
        self.assertEqual((d["laenge"], round(d["lautstaerke"], 4)), (-0.5, 0.8))

    def test_trefferquote_gewichtet_und_gleichstand_halb(self):
        g = {"kill_punkte": 1.0}
        richtig = Paar({"kill_punkte": 3.0}, {"kill_punkte": 1.0}, "battle", 1.0)
        falsch = Paar({"kill_punkte": 1.0}, {"kill_punkte": 3.0}, "freigabe", 0.5)
        gleich = Paar({"kill_punkte": 1.0}, {"kill_punkte": 1.0}, "battle", 1.0)
        # (1·1 + 0,5·0 + 1·0,5) / (1 + 0,5 + 1) = 1,5 / 2,5 = 0,6
        self.assertEqual(lernen.trefferquote(g, [richtig, falsch, gleich]), 0.6)
        self.assertIsNone(lernen.trefferquote(g, []))

    def test_trefferquote_vergleicht_unbekanntes_nicht(self):
        g = {"mic_lachen": 1.0}
        self.assertEqual(lernen.trefferquote(g, [Paar({"mic_lachen": 3.0}, {}, "publikum")]), 0.5)

    def test_score_ist_roh_score(self):
        # Befund E-8: lernen.score (nur ein zweiter Name für roh_score) ist gestrichen – eine Formel, ein Name
        g = {"kill_punkte": 1.0, "bot_opfer": -2.0}
        self.assertEqual(roh_score({"kill_punkte": 3.0, "bot_opfer": 0.5}, g), 2.0)
        self.assertFalse(hasattr(lernen, "score"))

    def test_nur_zahlen_eine_regel(self):
        # Befund E-7: lernen nutzt merkmale.nur_zahlen statt einer eigenen Kopie
        self.assertEqual(merkmal_modul.nur_zahlen({"a": 1, "b": [2], "c": "x", "d": True, "e": None}), {"a": 1.0})
        self.assertFalse(hasattr(lernen, "_nur_zahlen"))

    def test_paar_dicts_werden_nicht_aufgefuellt(self):
        self.clip_post({"kill_punkte": 1.0}, 1.0, tag=1)
        self.clip_post({"kill_punkte": 3.0}, 0.0, tag=2)
        (p,) = lernen.publikum_paare(self.con, self.konfig)
        self.assertNotIn("mic_lachen", p.besser)
        self.assertNotIn("mic_lachen", p.schlechter)


# --- publikum_paare --------------------------------------------------------------------------------------------

class PublikumPaare(MitPosts):
    def test_besser_ist_der_hoehere_score(self):
        leise, _ = self.clip_post(alt(1.0, 0.1), 0.2, tag=1)
        laut, _ = self.clip_post(alt(1.0, 0.9), 1.0, tag=2)
        (p,) = lernen.publikum_paare(self.con, self.konfig)
        self.assertEqual((p.besser["lautstaerke"], p.schlechter["lautstaerke"], p.art, p.gewicht),
                         (0.9, 0.1, "publikum", 1.0))

    def test_basis_zu_klein_ausgeschlossen(self):
        self.clip_post(alt(1.0), 0.0, tag=1, vermerke=[publikum.VERMERK_BASIS_ZU_KLEIN])
        self.clip_post(alt(3.0), 1.0, tag=2)
        self.assertEqual(lernen.publikum_paare(self.con, self.konfig), [])

    def test_unbewertet_ausgeschlossen(self):
        self.clip_post(alt(1.0), 0.0, tag=1, bewertet=False)
        self.clip_post(alt(3.0), 1.0, tag=2)
        self.assertEqual(lernen.publikum_paare(self.con, self.konfig), [])

    def test_abstand_plattform_und_art(self):
        self.clip_post(alt(1.0), 0.0, tag=1)
        self.clip_post(alt(2.0), 0.4, tag=2)                              # Abstand 0,4 < 0,5
        self.clip_post(alt(3.0), 2.0, tag=3, plattform="youtube")          # andere Plattform
        m = {"moment": "datei:a", "clip_id": None, "merkmale": {"kill_punkte": 6.0}}
        self.entwurf_post(1, "datei:a", [m], 3.0, tag=4)                    # andere Art
        self.assertEqual(lernen.publikum_paare(self.con, self.konfig), [])
        self.clip_post(alt(4.0), 0.5, tag=5)                                # genau 0,5 zum ersten → Paar
        paare = lernen.publikum_paare(self.con, self.konfig)
        self.assertEqual([(p.besser["kill_punkte"], p.schlechter["kill_punkte"]) for p in paare], [(4.0, 1.0)])

    def test_gleicher_moment_uebersprungen(self):
        """Zwei Entwürfe mit demselben Hook-Moment sind kein Vergleich zweier Momente."""
        cid = self.clip_anlegen(status="freigegeben", merkmale=alt(3.0), match_id="h")
        m = {"moment": f"clip:{cid}", "clip_id": cid, "merkmale": alt(3.0)}
        self.entwurf_post(1, f"clip:{cid}", [m], 0.0, tag=1)
        self.entwurf_post(2, f"clip:{cid}", [m], 2.0, tag=2)
        self.assertEqual(lernen.publikum_paare(self.con, self.konfig), [])

    def test_entwurf_nimmt_den_hook_moment_mit_aktuellen_clip_merkmalen_und_mic(self):
        hook = self.clip_anlegen(status="freigegeben", merkmale=alt(6.0, bot_opfer=0.0), match_id="h")
        anderer = self.clip_anlegen(status="freigegeben", merkmale=alt(10.0), match_id="h")
        # clips.merkmale hat sich seit dem Post geändert (nachgetragen): die aktuellen Werte zählen
        self.con.execute("UPDATE clips SET merkmale = ? WHERE id = ?",
                         (json.dumps(alt(6.0, bot_opfer=1.0)), hook))
        self.con.execute("INSERT INTO momente (schluessel, clip_id, datei, start_s, ende_s, stimmung, sicherheit,"
                         " quelle, merkmale, erstellt, geaendert) VALUES (?, ?, 'x.mp4', 0, 10, 'episch', 1, 'regel', ?, ?, ?)",
                         (f"clip:{hook}", hook, json.dumps({"lachen": 5, "mikro_spur": 1}), iso(T0), iso(T0)))
        momente = [{"moment": f"clip:{hook}", "clip_id": hook, "merkmale": alt(6.0, bot_opfer=0.0)},
                   {"moment": f"clip:{anderer}", "clip_id": anderer, "merkmale": alt(10.0)}]
        self.entwurf_post(1, f"clip:{hook}", momente, 2.0, tag=1)
        datei = {"moment": "datei:b", "clip_id": None, "merkmale": {"kill_punkte": 0.0, "victory_royale": 0.0}}
        self.entwurf_post(2, "datei:b", [datei], 0.0, tag=2)
        (p,) = lernen.publikum_paare(self.con, self.konfig)
        self.assertEqual(p.besser["kill_punkte"], 6.0)          # Hook, nicht der stärkere zweite Moment
        self.assertEqual(p.besser["bot_opfer"], 1.0)            # aktuell, nicht eingefroren
        self.assertEqual(p.besser["mic_lachen"], 3.0)           # Mic aus momente (gedeckelt)
        # Datei-Moment ohne momente-Zeile → eingefrorene Zahlen aus posts.merkmale
        self.assertEqual(p.schlechter, {"kill_punkte": 0.0, "victory_royale": 0.0})

    def test_datei_moment_mit_momente_zeile(self):
        self.con.execute("INSERT INTO momente (schluessel, clip_id, datei, start_s, ende_s, stimmung, sicherheit,"
                         " quelle, merkmale, erstellt, geaendert) VALUES ('datei:c', NULL, 'c.mp4', 0, 10, 'lustig', 1,"
                         " 'regel', ?, ?, ?)", (json.dumps({"max_gruppe": 2, "lachen": 1, "spitzen_s": [1.0]}),
                                                iso(T0), iso(T0)))
        self.entwurf_post(1, "datei:c", [{"moment": "datei:c", "clip_id": None, "merkmale": {}}], 1.0, tag=1)
        self.entwurf_post(2, "datei:d", [{"moment": "datei:d", "clip_id": None,
                                          "merkmale": {"kill_punkte": 1.0, "text": "x"}}], 0.0, tag=2)
        (p,) = lernen.publikum_paare(self.con, self.konfig)
        self.assertEqual(p.besser, {"kill_punkte": 3.0, "victory_royale": 0.0, "mic_lachen": 1.0})
        self.assertEqual(p.schlechter, {"kill_punkte": 1.0})   # nur Zahlen aus dem Eingefrorenen

    def test_datei_hook_gegen_clip_hook_verschiebt_laenge_und_lautstaerke_nicht(self):
        """Befund K-3: Publikums-Paar Datei-Moment gegen Clip-Moment – laenge/lautstaerke fehlen beim Datei-Moment,
        also lernt das Paar nichts über sie (sonst hieße es „Datei gegen Clip“ statt „kurz gegen lang“)."""
        self.con.execute("INSERT INTO momente (schluessel, clip_id, datei, start_s, ende_s, stimmung, sicherheit,"
                         " quelle, merkmale, erstellt, geaendert) VALUES ('datei:c', NULL, 'c.mp4', 0, 10, 'lustig', 1,"
                         " 'regel', ?, ?, ?)", (json.dumps({"max_gruppe": 2, "lachen": 1}), iso(T0), iso(T0)))
        # gleiche Kill-Punkte (Double = 3), damit das Paar unter MARGE bleibt und das Training wirklich schiebt
        cid = self.clip_anlegen(status="gesendet", merkmale=alt(3.0, 0.8) | {"laenge": 1.5}, match_id="p9",
                                start=datetime(2026, 8, 1, 19, 0, tzinfo=UTC))
        self.entwurf_post(1, "datei:c", [{"moment": "datei:c", "clip_id": None, "merkmale": {}}], 1.0, tag=1)
        self.entwurf_post(2, f"clip:{cid}", [{"moment": f"clip:{cid}", "clip_id": cid, "merkmale": {}}], 0.0, tag=2)
        (p,) = lernen.publikum_paare(self.con, self.konfig)
        self.assertNotIn("laenge", p.besser)
        self.assertEqual(p.schlechter["laenge"], 1.5)
        start = lernen.startgewichte(self.konfig)
        w = lernen.trainiere([p], start, self.konfig.abschnitt("lernen"))
        self.assertEqual((w["laenge"], w["lautstaerke"]), (start["laenge"], start["lautstaerke"]))

    def test_clip_geloescht_rueckfall_eingefroren(self):
        cid, _ = self.clip_post(alt(3.0), 1.0, tag=1)
        self.clip_post(alt(1.0), 0.0, tag=2)
        self.con.execute("DELETE FROM clips WHERE id = ?", (cid,))
        (p,) = lernen.publikum_paare(self.con, self.konfig)
        self.assertEqual(p.besser["kill_punkte"], 3.0)

    def test_entwurf_ohne_hook_wird_uebergangen(self):
        self.entwurf_post(1, None, [], 2.0, tag=1)
        self.entwurf_post(2, "datei:e", [{"moment": "datei:e", "clip_id": None, "merkmale": {}}], 0.0, tag=2)
        self.assertEqual(lernen.publikum_paare(self.con, self.konfig), [])

    def test_max_paare_nimmt_die_juengsten(self):
        posts = [self.clip_post(alt(float(i)), float(i), tag=i)[1] for i in range(1, 5)]  # 6 Paare
        self.konfig.daten["publikum"]["max_paare"] = 2
        paare = lernen.publikum_paare(self.con, self.konfig)
        # Alle drei Paare mit Post 4 sind gleich jung; dann entscheidet der jüngere Partner (höhere id)
        self.assertEqual([(p.besser["kill_punkte"], p.schlechter["kill_punkte"]) for p in paare],
                         [(4.0, 3.0), (4.0, 2.0)])
        self.assertEqual(len(posts), 4)

    def test_deterministisch(self):
        for i in range(1, 6):
            self.clip_post(alt(float(i), 0.1 * i), float(i) * 0.7, tag=i)
        self.abend(1, 6)
        self.konfig.daten["lernen"]["mindestens"] = 1
        self.assertEqual(lernen.publikum_paare(self.con, self.konfig), lernen.publikum_paare(self.con, self.konfig))
        self.assertEqual(lernen.berechne(self.con, self.konfig), lernen.berechne(self.con, self.konfig))


# --- berechne: zwei Quoten, Schranke je Quote -----------------------------------------------------------------

class ZweiQuoten(MitPosts):
    """Du magst laute Clips (Freigaben). Beim Publikum gewinnt, wer mehr Kills hat, obwohl er leiser ist:
    besser hat Kill +Δ und Lautstärke −0,9·Δ. Mit den Startgewichten (je 1,0) sortiert das richtig (Σ = +0,1·Δ),
    mit Lautstärke 1,5 falsch (Σ = −0,35·Δ)."""

    def setUp(self):
        super().setUp()
        for tag in range(1, 6):
            self.abend(tag, 8)                                  # 40 Freigaben/Verwerfungen
        self.konfig.daten["lernen"]["voll_vertrauen"] = 1       # Vertrauen 1: gelernt = aktiv genutzt

    def publikum(self, scores: list[float]) -> None:
        for i, s in enumerate(scores):
            self.clip_post(alt(float(i), 0.9 * (len(scores) - 1 - i)), s, tag=i)

    def gelernt(self, **werte: float) -> dict:
        start = {m: float(self.konfig.wert(f"vorbewertung.startgewichte.{m}", 0.0)) for m in MERKMALE}
        return start | werte

    def test_getrennte_quoten(self):
        self.publikum([0.0, 1.0, 2.0, 3.0, 4.0])                # 10 Publikums-Paare
        with mock.patch.object(lernen, "trainiere", return_value=self.gelernt(lautstaerke=1.5)):
            e = lernen.berechne(self.con, self.konfig)
        self.assertEqual((e.trefferquote, e.trefferquote_start), (1.0, 1.0))
        self.assertEqual((e.trefferquote_publikum_start, e.paare_je_quelle["publikum"]), (1.0, 10))

    def test_schranke_publikum_ab_zehn_paaren(self):
        self.publikum([0.0, 1.0, 2.0, 3.0, 4.0])                # 10 Paare → Schranke prüft das Publikum
        with mock.patch.object(lernen, "trainiere", return_value=self.gelernt(lautstaerke=1.5)):
            e = lernen.berechne(self.con, self.konfig)
        self.assertFalse(e.aktiv)
        self.assertEqual(e.werte, e.start)
        self.assertIn("Publikum", e.grund)
        # angezeigt werden die Quoten der Startgewichte (die gelten ja)
        self.assertEqual(e.trefferquote_publikum, e.trefferquote_publikum_start)

    def test_schranke_publikum_unter_zehn_paaren_nicht(self):
        self.publikum([0.0, 1.0, 2.0, 3.0, 3.2])                # (3,0 ↔ 3,2) zu knapp → 9 Paare
        with mock.patch.object(lernen, "trainiere", return_value=self.gelernt(lautstaerke=1.5)):
            e = lernen.berechne(self.con, self.konfig)
        self.assertEqual(e.paare_je_quelle["publikum"], 9)
        self.assertTrue(e.aktiv, e.grund)
        self.assertEqual(e.trefferquote_publikum, 0.0)          # schlechter, aber zählt noch nicht
        self.assertEqual(e.trefferquote_publikum_start, 1.0)

    def test_schranke_nutzer(self):
        self.publikum([0.0, 1.0])
        with mock.patch.object(lernen, "trainiere", return_value=self.gelernt(lautstaerke=-0.5)):
            e = lernen.berechne(self.con, self.konfig)
        self.assertFalse(e.aktiv)
        self.assertIn("dein", e.grund)

    def test_publikums_quote_mit_einem_paar_berechnet_und_angezeigt_schranke_prueft_nicht(self):
        self.publikum([0.0, 1.0])                               # genau 1 Paar
        with mock.patch.object(lernen, "trainiere", return_value=self.gelernt(lautstaerke=1.5)):
            e = lernen.berechne(self.con, self.konfig)
        self.assertTrue(e.aktiv, e.grund)
        self.assertEqual((e.trefferquote_publikum, e.trefferquote_publikum_start), (0.0, 1.0))
        text = texte.gewichte_text(e, 3)
        self.assertIn("Sortier-Quote Publikum: 0 % (Start 100 %) – 1 Paar, zählt für die Schranke erst ab 10", text)

    def test_ohne_publikum_keine_quote(self):
        e = lernen.berechne(self.con, self.konfig)
        self.assertIsNone(e.trefferquote_publikum)
        self.assertIsNone(e.trefferquote_publikum_start)
        self.assertEqual(e.paare_je_quelle, {"battle": 0, "freigabe": 80, "publikum": 0})
        self.assertIn("Publikum: noch keine Paare", texte.gewichte_text(e, 0))

    def test_unter_mindestmenge_auch_publikum_quote(self):
        self.konfig.daten["lernen"]["mindestens"] = 1000
        self.publikum([0.0, 1.0])
        e = lernen.berechne(self.con, self.konfig)
        self.assertFalse(e.aktiv)
        self.assertEqual(e.trefferquote_publikum, 1.0)
        self.assertEqual(e.datenbasis, 42)                      # 40 + 2 Posts

    def test_echtes_lernen_mit_publikum_bleibt_deterministisch_und_speichert(self):
        self.publikum([0.0, 1.0, 2.0])
        version, e = lernen.aktualisiere(self.con, self.konfig)
        self.assertEqual(version, 1)
        zeile = self.con.execute("SELECT * FROM gewichte WHERE version = 1").fetchone()
        self.assertEqual(json.loads(zeile["quellen"]), {"battle": 0, "freigabe": 80, "publikum": 3})
        self.assertEqual(zeile["trefferquote_publikum"], e.trefferquote_publikum)
        self.assertEqual(zeile["trefferquote_publikum_start"], e.trefferquote_publikum_start)
        self.assertIsNotNone(zeile["trefferquote_publikum_start"])
        self.assertEqual(lernen.aktualisiere(self.con, self.konfig)[0], 1)


class Auseinander(MitPosts):
    """Du magst laut (Freigaben), das Publikum mag Kills (und leiser)."""

    def setUp(self):
        super().setUp()
        for tag in range(1, 6):
            self.abend(tag, 8)

    def test_satz_ab_zehn_publikums_paaren(self):
        for i in range(5):
            self.clip_post(alt(float(i), 0.9 * (4 - i)), float(i), tag=i)     # 10 Paare
        e = lernen.berechne(self.con, self.konfig)
        self.assertEqual(e.auseinander, "Du magst Lautstärke, das Publikum Kill-Punkte.")
        self.assertIn(e.auseinander, texte.gewichte_text(e, 1))

    def test_kein_satz_unter_zehn(self):
        for i in range(4):
            self.clip_post(alt(float(i), 0.9 * (3 - i)), float(i), tag=i)     # 6 Paare
        self.assertIsNone(lernen.berechne(self.con, self.konfig).auseinander)

    def test_kein_satz_wenn_einig(self):
        for i in range(5):
            self.clip_post(alt(3.0, 0.2 * i), float(i), tag=i)                # Publikum mag auch laut
        self.assertIsNone(lernen.berechne(self.con, self.konfig).auseinander)


# --- aktuelle, ohne_mic, alte Datenbank ------------------------------------------------------------------------

class AktuelleGewichte(MitPosts):
    def test_alte_db_ohne_neue_schluessel_bekommt_startgewichte(self):
        alt_werte = {"kill_punkte": 1.2, "victory_royale": 5.0, "laenge": -0.5, "lautstaerke": 1.3, "kommentar": 1.0}
        self.con.execute("INSERT INTO gewichte (version, werte, datenbasis, vertrauen, trefferquote,"
                         " trefferquote_start, erstellt) VALUES (4, ?, 30, 0.5, 0.8, 0.7, ?)",
                         (json.dumps(alt_werte), iso(T0)))
        version, g = lernen.aktuelle(self.con, self.konfig)
        self.assertEqual(version, 4)
        self.assertEqual(g["bot_opfer"], -2.0)
        self.assertEqual(g["lautstaerke"], 1.3)                 # Gelerntes bleibt
        self.assertEqual(set(g), set(MERKMALE))

    def test_ohne_gespeicherte_version_startgewichte(self):
        version, g = lernen.aktuelle(self.con, self.konfig)
        self.assertEqual((version, g["bot_opfer"], g["kill_punkte"]), (0, -2.0, 1.0))

    def test_ohne_mic_zaehlt_nicht_verworfene_ohne_mic_stand(self):
        self.clip_anlegen(status="gesendet")
        self.clip_anlegen(status="verworfen")
        fertig = self.clip_anlegen(status="freigegeben")
        self.con.execute("UPDATE clips SET mic_stand = ? WHERE id = ?", (iso(T0), fertig))
        self.assertEqual(lernen.berechne(self.con, self.konfig).ohne_mic, 1)


# --- /gewichte (Bot-Text und CLI) --------------------------------------------------------------------------

class GewichteAnzeige(MitPosts):
    def ergebnis(self, **extra) -> lernen.Ergebnis:
        start = {m: 1.0 for m in MERKMALE}
        werte = start | {"lautstaerke": 1.25}
        felder = dict(werte=werte, start=start, datenbasis=52, freigaben=40, battles=2, vertrauen=0.5,
                      trefferquote=0.8, trefferquote_start=0.7, aktiv=True, grund="aktiv",
                      trefferquote_publikum=0.64, trefferquote_publikum_start=0.5,
                      paare_je_quelle={"battle": 2, "freigabe": 80, "publikum": 12}, ohne_mic=3,
                      auseinander="Du magst Lautstärke, das Publikum Kill-Punkte.")
        return lernen.Ergebnis(**(felder | extra))

    def test_bot_text(self):
        text = texte.gewichte_text(self.ergebnis(), 2)
        tabelle = text.split("<pre>")[1].split("</pre>")[0].splitlines()
        self.assertEqual(len(tabelle), 1 + 17)                   # Kopf + 17 Merkmale
        self.assertIn("Sortier-Quote du: 80 % (Start 70 %)", text)
        self.assertIn("Sortier-Quote Publikum: 64 % (Start 50 %) – 12 Paare", text)
        self.assertNotIn("zählt für die Schranke", text)          # ab 10 ohne Zusatz
        self.assertIn("Paare: 2 Battles · 80 Freigaben · 12 Publikum", text)
        self.assertIn("ohne Mic-Analyse: 3 Clips", text)
        self.assertIn("Du magst Lautstärke, das Publikum Kill-Punkte.", text)
        self.assertIn("10 Posts", text)                           # Datenbasis 52 = 40 + 2 + 10 Posts

    def test_bot_text_unter_zehn_publikums_paaren(self):
        e = self.ergebnis(paare_je_quelle={"battle": 0, "freigabe": 4, "publikum": 9}, auseinander=None)
        text = texte.gewichte_text(e, 1)
        self.assertIn("– 9 Paare, zählt für die Schranke erst ab 10", text)
        self.assertNotIn("Du magst", text)

    def test_bot_text_ohne_nutzer_paare(self):
        e = self.ergebnis(trefferquote=None, trefferquote_start=None, trefferquote_publikum=None,
                          trefferquote_publikum_start=None, paare_je_quelle={})
        text = texte.gewichte_text(e, 0)
        self.assertIn("Sortier-Quote du: noch keine Paare", text)
        self.assertIn("Publikum: noch keine Paare", text)

    def cli_lauf(self, *argumente: str) -> list[str]:
        aus = io.StringIO()
        with mock.patch.object(cli, "lade", return_value=self.konfig), contextlib.redirect_stdout(aus), \
                contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(["gewichte", *argumente])
        self.assertEqual(code, 0)
        return aus.getvalue().splitlines()

    def test_cli(self):
        for tag in range(1, 3):
            MitPosts.abend(self, tag, 4)
        self.clip_post(alt(1.0), 0.0, tag=1)
        self.clip_post(alt(3.0), 1.0, tag=2)
        zeilen = self.cli_lauf()
        text = "\n".join(zeilen)
        self.assertEqual(sum(1 for z in zeilen if z[:16].strip() in set(MERKMAL_NAMEN.values())), 17)
        self.assertIn("Sortier-Quote du: ", text)
        self.assertIn("Sortier-Quote Publikum: 100 % (Start 100 %) – 1 Paar, zählt für die Schranke erst ab 10", text)
        self.assertIn("Paare: 0 Battles · 8 Freigaben · 1 Publikum", text)
        self.assertIn("ohne Mic-Analyse: 6 Clips", text)   # 4 freigegeben + 2 Post-Clips (4 verworfen zählen nicht)


# --- pipeline publikum bewerten lernt neu (Annahme S2-A12) ---------------------------------------------------

class PublikumBewertenLerntNeu(MitPosts):
    def lauf(self, ergebnis: dict) -> int:
        with mock.patch.object(cli, "lade", return_value=self.konfig), \
                mock.patch.object(cli, "jetzt", return_value=T0), \
                mock.patch.object(publikum, "bewerte_alle", return_value=ergebnis), \
                mock.patch("clip_pipeline.lernbot_publikum.meldung_nach_bewerten", return_value=False), \
                mock.patch.object(lernen, "aktualisiere", wraps=lernen.aktualisiere) as spion, \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["publikum", "bewerten"]), 0)
        return spion.call_count

    def test_neue_scores_loesen_neulernen_aus(self):
        self.assertEqual(self.lauf({"bewertet": 2, "ohne_messung": 0, "noch_zu_jung": 0, "fehler": 0,
                                    "posts": [{"id": 1, "score": 1.0}, {"id": 2, "score": 0.0}]}), 1)

    def test_ohne_neue_scores_kein_neulernen(self):
        self.assertEqual(self.lauf({"bewertet": 0, "ohne_messung": 1, "noch_zu_jung": 0, "fehler": 0,
                                    "posts": []}), 0)


class BestehenderTestMitHalbemGewicht(MitPosts):
    """test_lernt_lautstaerke_und_bleibt_an_der_leine (test_elo_lernen_bot) mit Freigabe-Gewicht 0,5 erneut geprüft."""

    def test_lernt_lautstaerke_und_bleibt_an_der_leine(self):
        for tag in range(1, 6):
            self.abend(tag, 8)
        e = lernen.berechne(self.con, self.konfig)
        self.assertTrue(e.aktiv, e.grund)
        self.assertGreater(e.werte["lautstaerke"], e.start["lautstaerke"])
        self.assertLessEqual(e.werte["lautstaerke"], 1.5)
        self.assertGreaterEqual(e.trefferquote, e.trefferquote_start)
        self.assertEqual(lernen.aktualisiere(self.con, self.konfig)[0], 1)
        self.assertEqual(lernen.aktualisiere(self.con, self.konfig)[0], 1)
        # Mit halbem Gewicht wandert das Gewicht halb so weit je Schritt wie mit vollem
        self.konfig.daten["lernen"]["gewicht_freigabe"] = 1.0
        voll = lernen.berechne(self.con, self.konfig)
        self.assertGreaterEqual(voll.werte["lautstaerke"], e.werte["lautstaerke"])



class KaputteDaten(MitPosts):
    """Ein kaputter Post (JSON) hält das Lernen nicht auf – er wird geloggt und übersprungen."""

    def test_kaputtes_score_teile_und_merkmale(self):
        self.clip_post(alt(1.0), 0.0, tag=1)
        self.clip_post(alt(3.0), 1.0, tag=2)
        _, kaputt1 = self.clip_post(alt(5.0), 2.0, tag=3)
        _, kaputt2 = self.clip_post(alt(6.0), 3.0, tag=4)
        self.con.execute("UPDATE posts SET score_teile = '{kaputt' WHERE id = ?", (kaputt1,))
        self.con.execute("UPDATE posts SET merkmale = '{kaputt' WHERE id = ?", (kaputt2,))
        with self.assertLogs("pipeline", "WARNING") as logs:
            paare = lernen.publikum_paare(self.con, self.konfig)
        self.assertEqual([(p.besser["kill_punkte"], p.schlechter["kill_punkte"]) for p in paare], [(3.0, 1.0)])
        self.assertTrue(any(f"#{kaputt1}" in z for z in logs.output) and any(f"#{kaputt2}" in z for z in logs.output))

    def test_publikum_bewerten_kommt_trotz_lernfehler_mit_json_zeile(self):
        aus = io.StringIO()
        ergebnis = {"bewertet": 1, "ohne_messung": 0, "noch_zu_jung": 0, "fehler": 0, "posts": [{"id": 1, "score": 1}]}
        with mock.patch.object(cli, "lade", return_value=self.konfig), \
                mock.patch.object(publikum, "bewerte_alle", return_value=ergebnis), \
                mock.patch("clip_pipeline.lernbot_publikum.meldung_nach_bewerten", return_value=False), \
                mock.patch.object(lernen, "aktualisiere", side_effect=KeyError("gewicht_freigabe")), \
                contextlib.redirect_stdout(aus), self.assertLogs("pipeline", "WARNING"):
            self.assertEqual(cli.main(["publikum", "bewerten"]), 0)
        self.assertEqual(json.loads(aus.getvalue().strip().splitlines()[-1])["bewertet"], 1)
