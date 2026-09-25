"""Upload-Fassung eines Entwurfs und seine Caption (Spec §10.4, Plan Stufe 1 Paket c).

Die Upload-Fassung ist das Video, das du auf TikTok hochlädst: volle Auflösung 1080×1920, crf 20, sicher unter der
Telegram-Grenze. Geprüft wird hier:
  - ein echter Render mit Farbtest-Material (ffprobe misst 1080×1920, der Befehl hat „-crf 20“, zweiter Aufruf
    überspringt),
  - die fachlichen Prüfungen VOR dem Rendern (nur Shorts, nur getrennter Betrieb, alle Dateien da – ffmpeg startet
    dann gar nicht),
  - die Wiederholung bei zu großer Datei (rendere gepatcht: 75 % der benutzten Rate, max_bytes bleibt),
  - der Rückfall VA-API → CPU (ohne GPU: ffmpeg gepatcht),
  - nie wecken, nie Netz,
  - die Caption nur aus Fakten der Datenbank, mit Pflicht-Quellenangabe der Musik.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

from clip_pipeline import big, caption, entwurf, konfig, medien, regie
from clip_pipeline.caption import CaptionFehler
from clip_pipeline.entwurf import ZuGross
from clip_pipeline.konfig import KonfigFehler
from clip_pipeline.medien import MedienFehler

from tests.hilfen import HAT_FFMPEG, MitSpeicher
from tests.regie_hilfen import MOMENTE, MitRegieMaterial

MAX_BYTES = 48_000_000  # [vorschau].max_mb = 48 (im Test fest gesetzt)
NCS_QUELLE = "Song: Künstler - Titel [NCS Release]\nMusic provided by NoCopyrightSounds"


class MitUpload(MitRegieMaterial):
    """Getrennter Betrieb (E19): [speicher].wurzel ist der Puffer (Marke .clip-puffer), [lager].wurzel gesetzt.
    Die Werte, auf die es ankommt, stehen fest im Test – unabhängig von pipeline.toml oder lokal.toml."""

    def setUp(self):
        super().setUp()
        (self.konfig.wurzel / ".clip-puffer").touch()
        self.konfig.daten["lager"]["wurzel"] = str(self.tmp / "lager")  # wird nie angefasst (nur „gesetzt“)
        self.konfig.daten["publikum"]["upload_ordner"] = "export"
        self.konfig.daten["vorschau"]["max_mb"] = 48
        self.konfig.daten["shorts"]["max_kbit"] = 12000
        self.konfig.daten.setdefault("puffer", {})["rohdaten_tage"] = 14
        # Keine GPU im Test und kein Zufall durch /dev/dri auf dem Rechner: immer CPU (libx264), außer ein Test
        # legt ein „Gerät“ an
        self.konfig.daten["schnitt"]["vaapi_geraet"] = str(self.tmp / "kein-renderD128")

    def short_entwurf(self, momente=MOMENTE[:6], *, mit_musik: bool = False, echte_videos: bool = False) -> int:
        """Short-Entwurf über regie.erstelle. Ohne echte_videos sind die Moment-Dateien leer – das reicht, wo
        rendere bzw. ffmpeg ersetzt ist (regie.erstelle prüft nur, ob die Datei da ist), und spart je Test
        einige Sekunden ffmpeg."""
        if echte_videos:
            self.momente_anlegen(momente)
        else:
            def leer(ziel: Path, **_) -> Path:
                ziel.parent.mkdir(parents=True, exist_ok=True)
                ziel.write_bytes(b"")
                return ziel

            with mock.patch("tests.regie_hilfen.testvideo", side_effect=leer):
                self.momente_anlegen(momente)
        if mit_musik:
            self.musik_anlegen(150, "episch")
        return int(regie.erstelle(self.con, self.konfig, "short")["entwurf"])

    def zeile(self, eid: int):
        return self.con.execute("SELECT * FROM entwuerfe WHERE id = ?", (eid,)).fetchone()

    def liste(self, eid: int) -> dict:
        return json.loads(Path(self.zeile(eid)["schnittliste"]).read_text(encoding="utf-8"))


def _falsches_ergebnis(ziel: Path, dauer_s: float = 31.0) -> dict:
    """Was ein erfolgreicher rendere()-Aufruf zurückgibt – für Tests, die rendere ersetzen."""
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_bytes(b"upload")
    return {"datei": str(ziel), "mb": 0.0, "dauer_s": dauer_s, "encoder": "libx264", "aufloesung": [1080, 1920]}


# --- upload_ziel ---------------------------------------------------------------------------------------

class UploadZiel(MitUpload):
    def test_liegt_im_export_ordner_und_legt_nichts_an(self):
        eid = self.short_entwurf()
        z = self.zeile(eid)
        ziel = entwurf.upload_ziel(self.konfig, z)
        self.assertEqual(ziel, self.konfig.wurzel / "export" / z["name"] / f"{z['name']}_upload.mp4")
        self.assertFalse((self.konfig.wurzel / "export").exists())  # rechnet nur

    def test_ordner_fehlt_oder_zeigt_aus_dem_puffer_ist_ein_konfigfehler(self):
        eid = self.short_entwurf()
        for falsch in ("", "/srv/big/clips/export", "../export"):
            self.konfig.daten["publikum"]["upload_ordner"] = falsch
            with self.assertRaises(KonfigFehler, msg=falsch) as fehler:
                entwurf.upload_ziel(self.konfig, self.zeile(eid))
            self.assertIn("[publikum].upload_ordner", str(fehler.exception))
        del self.konfig.daten["publikum"]["upload_ordner"]
        with self.assertRaises(KonfigFehler):
            entwurf.upload_ziel(self.konfig, self.zeile(eid))


# --- upload_fassung: echter Render ---------------------------------------------------------------------

@unittest.skipUnless(HAT_FFMPEG, "ffmpeg fehlt")
class UploadFassungEcht(MitUpload):
    def test_1080x1920_crf_20_unter_der_grenze_und_zweiter_aufruf_ueberspringt(self):
        eid = self.short_entwurf(MOMENTE[:8], mit_musik=True, echte_videos=True)
        befehle = []
        echt = entwurf.fuehre_aus

        def lauf(befehl, was, timeout=None):
            befehle.append(befehl)
            return echt(befehl, was, timeout)

        with mock.patch.object(entwurf, "fuehre_aus", side_effect=lauf):
            r = entwurf.upload_fassung(self.con, self.konfig, eid)
        video = Path(r["datei"])
        info = medien.probe(video)
        self.assertEqual([info.breite, info.hoehe], [1080, 1920])
        self.assertEqual(r["aufloesung"], [1080, 1920])
        self.assertLess(video.stat().st_size, MAX_BYTES)
        self.assertAlmostEqual(info.dauer_s, self.liste(eid)["dauer_s"], delta=0.2)
        self.assertEqual((r["entwurf"], r["encoder"], r["versuche"], r["uebersprungen"]), (eid, "libx264", 1, False))
        self.assertEqual(video, entwurf.upload_ziel(self.konfig, self.zeile(eid)))
        (befehl,) = befehle
        self.assertEqual(befehl[befehl.index("-crf") + 1], "20")        # Spec §10.4: crf 20
        self.assertIn("-maxrate", befehl)                                 # plus Deckel aus dem Budget
        self.assertNotIn("nvenc", " ".join(befehl))                      # nie NVENC (das wäre pve-big)
        self.assertEqual(self.zeile(eid)["upload_pfad"], str(video))     # gemerkt …
        self.assertIsNone(self.zeile(eid)["datei"])                      # … der Entwurf selbst bleibt unberührt

        with mock.patch.object(entwurf, "fuehre_aus") as nochmal:        # … und beim zweiten Mal übersprungen
            r2 = entwurf.upload_fassung(self.con, self.konfig, eid)
        nochmal.assert_not_called()
        self.assertEqual((r2["datei"], r2["uebersprungen"]), (str(video), True))


# --- upload_fassung: fachliche Prüfungen vor dem Rendern ----------------------------------------------

@unittest.skipUnless(HAT_FFMPEG, "ffmpeg fehlt")
class UploadFassungPruefungen(MitUpload):
    def setUp(self):
        super().setUp()
        self.eid = self.short_entwurf()
        # Keiner dieser Fälle darf ffmpeg oder ffprobe starten
        for name in ("fuehre_aus", "probe"):
            patcher = mock.patch.object(entwurf, name, side_effect=AssertionError(f"{name} gestartet"))
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_fehlende_moment_datei(self):
        weg = Path(self.liste(self.eid)["segmente"][0]["datei"])
        weg.unlink()  # die Testdatei aus momente_anlegen
        with self.assertRaises(MedienFehler) as fehler:
            entwurf.upload_fassung(self.con, self.konfig, self.eid)
        text = str(fehler.exception)
        self.assertIn("Moment-Datei fehlt", text)
        self.assertIn(weg.name, text)
        self.assertIn("14 Tage", text)
        self.assertFalse((self.konfig.wurzel / "export").exists())
        self.assertIsNone(self.zeile(self.eid)["upload_pfad"])

    def test_fehlende_musik(self):
        liste = self.liste(self.eid)
        liste["musik"] = {"datei": "gibt-es-nicht.mp3", "titel": "T", "kuenstler": "K", "quelle": "CC0",
                          "start_s": 0.0, "pegel": 0.3, "bpm": 120}
        Path(self.zeile(self.eid)["schnittliste"]).write_text(json.dumps(liste), encoding="utf-8")
        with self.assertRaises(MedienFehler) as fehler:
            entwurf.upload_fassung(self.con, self.konfig, self.eid)
        self.assertIn("Musik fehlt", str(fehler.exception))

    def test_zusammenschnitt_nur_fuer_shorts(self):
        self.con.execute("UPDATE entwuerfe SET format = 'zusammenschnitt' WHERE id = ?", (self.eid,))
        with self.assertRaises(MedienFehler) as fehler:
            entwurf.upload_fassung(self.con, self.konfig, self.eid)
        self.assertIn("nur für Shorts", str(fehler.exception))

    def test_ohne_getrennten_betrieb_konfigfehler(self):
        self.konfig.daten["lager"]["wurzel"] = ""
        with self.assertRaises(KonfigFehler) as fehler:
            entwurf.upload_fassung(self.con, self.konfig, self.eid)
        self.assertIn("getrennten Betrieb", str(fehler.exception))

    def test_ohne_puffer_marke_konfigfehler(self):
        (self.konfig.wurzel / ".clip-puffer").unlink()  # Puffer nicht eingehängt bzw. falscher Ordner
        with self.assertRaises(KonfigFehler) as fehler:
            entwurf.upload_fassung(self.con, self.konfig, self.eid)
        self.assertIn(".clip-puffer", str(fehler.exception))

    def test_unbekannter_entwurf(self):
        with self.assertRaises(MedienFehler) as fehler:
            entwurf.upload_fassung(self.con, self.konfig, 999)
        self.assertIn("999", str(fehler.exception))

    def test_datei_verschwunden_wird_neu_gerendert(self):
        # upload_pfad gemerkt, aber die Datei ist weg (z. B. von Hand gelöscht): nicht „übersprungen“ melden
        self.con.execute("UPDATE entwuerfe SET upload_pfad = ? WHERE id = ?", (str(self.tmp / "weg.mp4"), self.eid))
        with mock.patch.object(entwurf, "rendere", side_effect=lambda l, z, k, **kw: _falsches_ergebnis(z)) as r:
            ergebnis = entwurf.upload_fassung(self.con, self.konfig, self.eid)
        self.assertEqual((r.call_count, ergebnis["uebersprungen"]), (1, False))


# --- upload_fassung: zu groß → weniger Rate --------------------------------------------------------------

@unittest.skipUnless(HAT_FFMPEG, "ffmpeg fehlt")
class UploadFassungZuGross(MitUpload):
    def setUp(self):
        super().setUp()
        self.eid = self.short_entwurf()

    def test_zweimal_zu_gross_dann_mit_75_prozent_der_benutzten_rate(self):
        ziel = entwurf.upload_ziel(self.konfig, self.zeile(self.eid))
        folge = [ZuGross("zu groß", 7000), ZuGross("zu groß", 7000), _falsches_ergebnis(ziel)]
        with mock.patch.object(entwurf, "rendere", side_effect=folge) as rendere:
            r = entwurf.upload_fassung(self.con, self.konfig, self.eid)
        aufrufe = [c.kwargs for c in rendere.call_args_list]
        self.assertEqual([a["kbit_max"] for a in aufrufe], [12000, 5250, 5250])   # 0,75 · 7000 = 5250
        self.assertEqual({a["max_bytes"] for a in aufrufe}, {MAX_BYTES})         # die Grenze bleibt
        self.assertTrue(all(a["volle_aufloesung"] and a["crf"] == 20 for a in aufrufe))
        self.assertTrue(all(c.args[1] == ziel for c in rendere.call_args_list))
        self.assertEqual((r["versuche"], r["uebersprungen"]), (3, False))
        self.assertEqual(self.zeile(self.eid)["upload_pfad"], str(ziel))

    def test_dreimal_zu_gross_ist_ein_medienfehler(self):
        with mock.patch.object(entwurf, "rendere", side_effect=ZuGross("zu groß", 7000)) as rendere:
            with self.assertRaises(MedienFehler) as fehler:
                entwurf.upload_fassung(self.con, self.konfig, self.eid)
        self.assertEqual(rendere.call_count, entwurf.UPLOAD_VERSUCHE)
        self.assertNotIsInstance(fehler.exception, ZuGross)  # eine klare Endmeldung, nicht der letzte Versuch
        self.assertIn("48 MB", str(fehler.exception))
        self.assertIsNone(self.zeile(self.eid)["upload_pfad"])

    def test_anderer_medienfehler_wird_nicht_wiederholt(self):
        with mock.patch.object(entwurf, "rendere", side_effect=MedienFehler("ffmpeg kaputt")) as rendere:
            with self.assertRaises(MedienFehler) as fehler:
                entwurf.upload_fassung(self.con, self.konfig, self.eid)
        self.assertEqual((rendere.call_count, str(fehler.exception)), (1, "ffmpeg kaputt"))


# --- upload_fassung: VA-API → CPU ------------------------------------------------------------------------

@unittest.skipUnless(HAT_FFMPEG, "ffmpeg fehlt")
class UploadFassungVaApi(MitUpload):
    """Ohne GPU nicht echt ausführbar: ffmpeg wird ersetzt, der Befehlsaufbau und der Rückfall geprüft."""

    def test_vaapi_scheitert_dann_libx264_in_1080x1920_mit_crf_20(self):
        eid = self.short_entwurf(echte_videos=True)  # rendere misst die Moment-Dateien mit ffprobe
        geraet = self.tmp / "renderD128"
        geraet.touch()  # „vorhanden“
        self.konfig.daten["schnitt"]["vaapi_geraet"] = str(geraet)
        befehle = []

        def lauf(befehl, was, timeout=None):
            befehle.append(befehl)
            if "h264_vaapi" in befehl:
                raise MedienFehler("VA-API geht nicht")
            Path(befehl[-1]).write_bytes(b"x" * 1000)  # die .tmp-Ausgabe, klein genug
            return ""

        with mock.patch.object(entwurf, "fuehre_aus", side_effect=lauf):
            r = entwurf.upload_fassung(self.con, self.konfig, eid)
        vaapi, cpu = befehle
        self.assertIn("-b:v", vaapi)                                   # VA-API kennt kein crf: Rate als -b:v
        self.assertNotIn("-crf", vaapi)
        self.assertEqual(cpu[cpu.index("-c:v") + 1], "libx264")
        self.assertEqual(cpu[cpu.index("-crf") + 1], "20")
        self.assertIn("crop=1080:1920", cpu[cpu.index("-filter_complex") + 1])  # volle Auflösung, nicht 720×1280
        self.assertNotIn("-vaapi_device", cpu)
        self.assertFalse(any("nvenc" in " ".join(b) for b in befehle))
        self.assertEqual((r["encoder"], r["aufloesung"]), ("libx264", [1080, 1920]))


# --- nie wecken --------------------------------------------------------------------------------------------

@unittest.skipUnless(HAT_FFMPEG, "ffmpeg fehlt")
class UploadFassungNieWecken(MitUpload):
    def test_kein_wecken_kein_netz(self):
        eid = self.short_entwurf()
        self.konfig.daten["speicher"].update(host="pve-gross", wol_mac="aa:bb:cc:dd:ee:ff")
        with mock.patch.object(konfig.Konfig, "_host_erreichbar", return_value=False), \
                mock.patch("clip_pipeline.konfig.sende_wake_on_lan") as wol, \
                mock.patch.object(big, "wach_halten") as wach, \
                mock.patch("socket.create_connection", side_effect=AssertionError("Netzwerkzugriff")), \
                mock.patch.object(entwurf, "rendere", side_effect=lambda l, z, k, **kw: _falsches_ergebnis(z)):
            r = entwurf.upload_fassung(self.con, self.konfig, eid)
        self.assertFalse(r["uebersprungen"])
        wol.assert_not_called()
        wach.assert_not_called()


# --- caption.entwurf_caption --------------------------------------------------------------------------------

class EntwurfCaption(MitSpeicher):
    """Die Caption stützt sich nur auf Fakten aus der Datenbank (CLAUDE.md: nichts erfinden)."""

    def setUp(self):
        super().setUp()
        self.konfig.daten["caption"]["vorlage"] = "templates/caption.txt"

    def clip(self, max_gruppe: int, *, typ: str, victory: bool = False) -> int:
        cid = self.clip_anlegen(status="freigegeben", max_gruppe=max_gruppe)
        self.con.execute("UPDATE clips SET typ = ?, victory_royale = ? WHERE id = ?", (typ, int(victory), cid))
        return cid

    @staticmethod
    def liste(segmente: list[tuple[str, int | None]], musik: dict | None = None) -> dict:
        return {"format": "short", "segmente": [{"moment": m, "clip_id": c} for m, c in segmente], "musik": musik}

    @staticmethod
    def beschreibung(text: str) -> str:
        return text.split("\n", 1)[0]  # {beschreibung} steht in der ersten Zeile der Vorlage

    def test_triple_mit_musik_quelle(self):
        einzel, triple = self.clip(1, typ="einzel"), self.clip(3, typ="triple")
        # Jump-Cut: clip:<triple> hat zwei Segmente, bleibt aber ein Moment → 3 Momente
        liste = self.liste([(f"clip:{einzel}", einzel), (f"clip:{triple}", triple), (f"clip:{triple}", triple),
                            ("datei:4", None)], musik={"titel": "Titel", "quelle": NCS_QUELLE})
        text = caption.entwurf_caption(self.con, liste, self.konfig)
        self.assertIn("Triple Kill", self.beschreibung(text))
        self.assertIn("3 Momente", self.beschreibung(text))
        self.assertIn("#triplekill", text)
        self.assertIn("clip-battle.de", text)                       # Werbung aus der Vorlage
        self.assertTrue(text.endswith("🎵 " + NCS_QUELLE))          # Quellenangabe Pflicht, eigene Zeile(n) am Ende
        self.assertLessEqual(caption._zahlen(self.beschreibung(text)), {"3"})  # keine erfundenen Zahlen

    def test_victory_royale(self):
        cid = self.clip(2, typ="double", victory=True)
        text = caption.entwurf_caption(self.con, self.liste([(f"clip:{cid}", cid)]), self.konfig)
        self.assertIn("Victory Royale", self.beschreibung(text))
        self.assertIn("Double Kill", self.beschreibung(text))
        self.assertIn("1 Moment", self.beschreibung(text))
        self.assertIn("#victoryroyale", text)
        self.assertNotIn("#doublekill", text)

    def test_mehr_als_triple(self):
        cid = self.clip(4, typ="multi")
        text = caption.entwurf_caption(self.con, self.liste([(f"clip:{cid}", cid), ("datei:2", None)]), self.konfig)
        self.assertIn("4-fach Kill", self.beschreibung(text))
        self.assertIn("#multikill", text)
        self.assertLessEqual(caption._zahlen(self.beschreibung(text)), {"4", "2"})

    def test_momente_ohne_clip_ergeben_fortnite(self):
        text = caption.entwurf_caption(self.con, self.liste([("datei:1", None), ("datei:2", None)]), self.konfig)
        self.assertIn("#gaming #fortnite #clipbattle", text)
        self.assertNotIn("Kill", self.beschreibung(text))
        self.assertIn("2 Momente", self.beschreibung(text))
        self.assertLessEqual(caption._zahlen(self.beschreibung(text)), {"2"})

    def test_unbekannter_clip_zaehlt_als_moment_ohne_gruppe(self):
        text = caption.entwurf_caption(self.con, self.liste([("clip:777", 777)]), self.konfig)
        self.assertIn("#gaming #fortnite #clipbattle", text)
        self.assertIn("1 Moment", self.beschreibung(text))

    def test_ohne_musik_keine_quellenzeile(self):
        cid = self.clip(1, typ="einzel")
        text = caption.entwurf_caption(self.con, self.liste([(f"clip:{cid}", cid)]), self.konfig)
        self.assertNotIn("🎵", text)
        self.assertIn("#elimination", text)

    def test_musik_ohne_quelle_ist_ein_fehler(self):
        for musik in ({"titel": "T", "quelle": "  "}, {"titel": "T"}):
            with self.assertRaises(CaptionFehler, msg=musik) as fehler:
                caption.entwurf_caption(self.con, self.liste([("datei:1", None)], musik=musik), self.konfig)
            self.assertIn("Quellenangabe", str(fehler.exception))

    def test_unbekannter_platzhalter_in_der_vorlage(self):
        vorlage = self.tmp / "vorlage.txt"
        vorlage.write_text("{beschreibung} {gibtsnicht}\n", encoding="utf-8")
        self.konfig.daten["caption"]["vorlage"] = str(vorlage)
        with self.assertRaises(CaptionFehler):
            caption.entwurf_caption(self.con, self.liste([("datei:1", None)]), self.konfig)

    def test_nur_lesend(self):
        cid = self.clip(3, typ="triple")
        vorher = [tuple(z) for z in self.con.execute("SELECT * FROM clips")]
        caption.entwurf_caption(self.con, self.liste([(f"clip:{cid}", cid)]), self.konfig)
        self.assertEqual([tuple(z) for z in self.con.execute("SELECT * FROM clips")], vorher)
        self.assertFalse(self.con.in_transaction)


if __name__ == "__main__":
    unittest.main()
