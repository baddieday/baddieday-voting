"""⚙️ Einstellungen im Lern-Bot (29.09.): Clip-Auswahl, Vorrang vor der Konfigdatei, Menü-Knöpfe."""

import json
import unittest

from clip_pipeline import einstellungen, lernbot_einstellungen, regie_lernen
from clip_pipeline.zeit import iso, jetzt
from tests.regie_hilfen import MitRegieMaterial


class Einstellungen(MitRegieMaterial):
    def setUp(self):
        super().setUp()
        # 27.09. abends · 28.09. 21:00 · 29.09. 01:30 Ortszeit (zählt noch zum Spielabend 28.09., Wechsel 06:00)
        for mid, start in (("m1", "2026-09-27T19:00:00Z"), ("m2", "2026-09-28T19:00:00Z"), ("m3", "2026-09-28T23:30:00Z")):
            self.con.execute("""INSERT INTO matches (id, replay_pfad, start_utc, ende_utc, kills, platzierung,
                                victory_royale, erstellt, geaendert) VALUES (?, ?, ?, ?, 3, 1, 1, ?, ?)""",
                             (mid, f"/r/{mid}.replay", start, start, iso(jetzt()), iso(jetzt())))
            self.con.execute("""INSERT INTO momente (schluessel, match_id, datei, start_s, ende_s, kills, stimmung,
                                sicherheit, quelle, merkmale, erstellt, geaendert)
                                VALUES (?, ?, '/x.mp4', 0, 20, 1, 'episch', 0.8, 'regel', '{}', ?, ?)""",
                             (f"datei:{mid}", mid, iso(jetzt()), iso(jetzt())))

    def auswahl(self):
        return einstellungen.quell_matches(self.con, einstellungen.anwenden(self.con, self.konfig))

    def test_clip_auswahl_und_vorrang_vor_der_datei(self):
        self.assertEqual(self.auswahl(), (None, None))                                  # Standard: alle Clips
        einstellungen.setze(self.con, "lernbot.quelle", "abend")
        ids, hinweis = self.auswahl()
        self.assertEqual(ids, {"m2", "m3"})                                              # 01:30 gehört zum Vorabend
        self.assertIn("Spielabend 28.09. (2 Matches)", hinweis)
        einstellungen.setze(self.con, "lernbot.quelle", "match:neuestes")
        self.assertEqual(self.auswahl()[0], {"m3"})
        einstellungen.setze(self.con, "lernbot.quelle", "match:m1")
        self.assertEqual(self.auswahl()[0], {"m1"})
        # Bot-Wert geht vor der Datei, die geladene Konfig bleibt unverändert; ↩️ Standard lässt die Datei gelten
        self.konfig.daten.setdefault("lernbot", {})["auto_schwelle"] = 0.2
        einstellungen.setze(self.con, "lernbot.auto_schwelle", 0.7)
        einstellungen.setze(self.con, "regie.effekte.an", False)
        k = einstellungen.anwenden(self.con, self.konfig)
        self.assertEqual((k.wert("lernbot.auto_schwelle"), k.wert("regie.effekte.an")), (0.7, False))
        self.assertEqual(self.konfig.wert("lernbot.auto_schwelle"), 0.2)
        einstellungen.zuruecksetzen(self.con, "lernbot.auto_schwelle")
        self.assertEqual(einstellungen.anwenden(self.con, self.konfig).wert("lernbot.auto_schwelle"), 0.2)

    def test_unsinn_wird_abgelehnt_oder_uebergangen(self):
        with self.assertRaises(ValueError):
            einstellungen.setze(self.con, "lernbot.quelle", "quatsch")
        with self.assertRaises(ValueError):
            einstellungen.setze(self.con, "speicher.wurzel", "/")                       # nicht im Katalog
        self.con.execute("INSERT INTO einstellungen VALUES ('speicher.wurzel', '\"/\"', 'x')")   # von Hand: übergangen
        self.assertEqual(einstellungen.anwenden(self.con, self.konfig).wurzel, self.konfig.wurzel)
        einstellungen.setze(self.con, "lernbot.quelle", "match:weg")                     # Match gibt es nicht (mehr)
        ids, hinweis = self.auswahl()
        self.assertIsNone(ids)
        self.assertIn("nicht gefunden – alle Clips", hinweis)

    def test_menue_klickweg(self):
        klick = lambda d: lernbot_einstellungen.verarbeite_klick(self.con, self.konfig, d)  # noqa: E731
        text, knoepfe, _ = klick("s:m")
        self.assertIn("🎯 Clips: alle Clips", text)
        _, knoepfe, _ = klick("s:o:0")
        self.assertIn("📅 Match wählen", [t for reihe in knoepfe for t, _d in reihe])
        text, _, antwort = klick("s:w:0:1")
        self.assertEqual(antwort, "Gespeichert")
        self.assertIn("🎯 Clips: neuester Spielabend 📱", text)
        self.assertIn("Gerade: 🎯 nur Spielabend 28.09.", text)
        _, knoepfe, _ = klick("s:l")
        self.assertEqual([d for reihe in knoepfe for _t, d in reihe][:3], ["s:x:m3", "s:x:m2", "s:x:m1"])
        text, _, _ = klick("s:x:m2")
        self.assertEqual(json.loads(self.con.execute("SELECT wert FROM einstellungen WHERE schluessel = "
                                                     "'lernbot.quelle'").fetchone()[0]), "match:m2")
        text, _, _ = klick("s:r:0")
        self.assertIn("🎯 Clips: alle Clips", text)
        for falsch in ("s:w:99:0", "s:x:../etc", "s:w:0:7", "x:1"):         # 99: jenseits des Katalogs
            with self.assertRaises(ValueError):
                klick(falsch)


    def test_auto_freigabe_im_katalog(self):
        # 30.09.: die vier Werte der Auto-Freigabe – die Datei-Werte (pipeline.toml) passen zum Typ der Optionen,
        # sonst würde ⚙️ sie als „unbekannt“ ablehnen; ein Bot-Wert kommt in auto_freigabe.werte an
        from clip_pipeline import auto_freigabe, konfig as konfig_modul

        datei = konfig_modul.lade()
        for schluessel in ("auto_freigabe.modus", "auto_freigabe.ziel_quote", "auto_freigabe.verwerfen",
                           "auto_freigabe.frist_h"):
            self.assertTrue(einstellungen._erlaubt(einstellungen.NACH_SCHLUESSEL[schluessel], datei.wert(schluessel)),
                            schluessel)
        einstellungen.setze(self.con, "auto_freigabe.frist_h", 48)
        einstellungen.setze(self.con, "auto_freigabe.modus", "probe")
        w = auto_freigabe.werte(einstellungen.anwenden(self.con, self.konfig))
        self.assertEqual((w["frist_h"], w["modus"]), (48, "probe"))
        with self.assertRaises(ValueError):
            einstellungen.setze(self.con, "auto_freigabe.frist_h", 24.0)                  # float statt int


if __name__ == "__main__":
    unittest.main()


class ShortLaenge(MitRegieMaterial):
    def test_untergrenze_aus_datei_und_aus_dem_bot(self):
        """06.10. (Florian: „wie kann ich die Videos wieder länger werden lassen?“): ⚙️ Short-Länge ist eine
        Untergrenze fürs Ziel – Gelerntes darf nur darüber gehen, nie über die 75 s des Formats."""
        p, _ = regie_lernen.aktuelle(self.con, self.konfig, "short")
        self.assertEqual(p["ziel_dauer_s"], 45.0)                                     # ohne Vorgabe: gelernt
        self.konfig.daten["regie"]["short_mindestens_s"] = 65.0
        p, _ = regie_lernen.aktuelle(self.con, self.konfig, "short")
        self.assertEqual(p["ziel_dauer_s"], 65.0)
        einstellungen.setze(self.con, "regie.short_mindestens_s", 75.0)              # der Bot-Wert geht vor
        p, _ = regie_lernen.aktuelle(self.con, einstellungen.anwenden(self.con, self.konfig), "short")
        self.assertEqual(p["ziel_dauer_s"], 75.0)
        with self.assertRaises(ValueError):
            einstellungen.setze(self.con, "regie.short_mindestens_s", 120.0)
