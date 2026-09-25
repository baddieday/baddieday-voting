"""Regisseur 2.0, Filtergraph mit Effekten (effekt_filter.py, entwurf.filtergraph) – reiner Text, ohne Video.

Spielbild clean (25.09.): Zoom nur auf dem Spielbild (nie Hintergrund, nie Text), Texte im Short nur im unscharfen
Rand, im 16:9 kein Zähler und der Titel nur in der Blende. Aus = heute: ohne Effekte ist der Graph zeichengleich."""

import math
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from clip_pipeline import effekt_filter, effekte, entwurf, regie, sfx

from tests.effekt_hilfen import filter_namen, mini_liste, ohne_effekte
from tests.hilfen import HAT_FFMPEG
from tests.regie_hilfen import MOMENTE, MitRegieMaterial
from tests.test_effekte_plan import fx_konfig, moment, plane, seg

SCHRIFT = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
EFFEKT_FILTER = ("eval=frame", "colorcorrect", "avgblur", "chromashift", "noise=", "[sfx", "adelay", "KILLS", "KILL'")

# So sah der Graph vor dem Regisseur 2.0 aus (nach Stufe 0) – Effekte aus muss genau das ergeben. „Heute“ heißt
# nach §0: derselbe Graph wie ohne das Feld „effekte“. Stufe 0 (fps zuerst, Unschärfe auf Viertelgröße) gilt immer,
# auch mit an = false – ihr Rückweg ist ihr eigener Commit (docs/REGIE.md, „Ausschalten“)
HEUTE_SHORT = ";".join([
    "[0:v]fps=30,split=2[hg0][vg0]",
    "[hg0]scale=180:320:force_original_aspect_ratio=increase,crop=180:320,boxblur=5:2,scale=720:1280,"
    "eq=brightness=-0.08[hgb0]",
    "[vg0]scale=720:-2[vgs0]",
    "[hgb0][vgs0]overlay=(W-w)/2:(H-h)/2,format=yuv420p,setsar=1,settb=AVTB[v0]",
    "[0:a:0][0:a:1]amix=inputs=2:normalize=0,aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,apad,"
    "atrim=0:4.400[a0]",
    "[1:v]fps=30,split=2[hg1][vg1]",
    "[hg1]scale=180:320:force_original_aspect_ratio=increase,crop=180:320,boxblur=5:2,scale=720:1280,"
    "eq=brightness=-0.08[hgb1]",
    "[vg1]scale=720:-2[vgs1]",
    "[hgb1][vgs1]overlay=(W-w)/2:(H-h)/2,format=yuv420p,setsar=1,settb=AVTB[v1]",
    "[1:a:0]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,apad,atrim=0:4.550[a1]",
    "[2:v]fps=30,split=2[hg2][vg2]",
    "[hg2]scale=180:320:force_original_aspect_ratio=increase,crop=180:320,boxblur=5:2,scale=720:1280,"
    "eq=brightness=-0.08[hgb2]",
    "[vg2]scale=720:-2[vgs2]",
    "[hgb2][vgs2]overlay=(W-w)/2:(H-h)/2,format=yuv420p,setsar=1,settb=AVTB[v2]",
    "anullsrc=r=48000:cl=stereo,atrim=0:4.150,aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a2]",
    "[v0][v1]xfade=transition=fade:duration=0.8000:offset=3.6000[vx1]",
    "[a0][a1]acrossfade=d=0.8000:c1=tri:c2=tri[ax1]",
    "[vx1][v2]xfade=transition=wipeleft:duration=0.3000:offset=7.8500[vx2]",
    "[ax1][a2]acrossfade=d=0.3000:c1=tri:c2=tri[ax2]",
    "[vx2]drawtext=fontfile='/f/DejaVuSans-Bold.ttf':text='clip-battle.de':fontsize=38:fontcolor=white@0.9:borderw=4:"
    "bordercolor=black@0.6:x=(w-text_w)/2:y=h*0.12[vtext]",
    "[vtext]null[vout]",
    "[ax2]asplit=2[spiel][schluessel]",
    "[3:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,volume=0.35,atrim=0:12.000,"
    "afade=t=in:d=0.5,afade=t=out:st=10.000:d=2[mus]",
    "[mus][schluessel]sidechaincompress=threshold=0.05:ratio=6:attack=20:release=400[leiser]",
    "[spiel][leiser]amix=inputs=2:normalize=0,alimiter=limit=0.95,apad[aout]",
])
HEUTE_16_9 = ";".join([
    "[0:v]fps=30,scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,"
    "format=yuv420p,setsar=1,settb=AVTB[v0]",
    "[0:a:0][0:a:1]amix=inputs=2:normalize=0,aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,apad,"
    "atrim=0:4.400[a0]",
    "[1:v]fps=30,scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,"
    "format=yuv420p,setsar=1,settb=AVTB[v1]",
    "[1:a:0][1:a:1]amix=inputs=2:normalize=0,aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,apad,"
    "atrim=0:4.550[a1]",
    "[2:v]fps=30,scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,"
    "format=yuv420p,setsar=1,settb=AVTB[v2]",
    "[2:a:0]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,apad,atrim=0:4.150[a2]",
    "[v0][v1]xfade=transition=fade:duration=0.8000:offset=3.6000[vx1]",
    "[a0][a1]acrossfade=d=0.8000:c1=tri:c2=tri[ax1]",
    "[vx1][v2]xfade=transition=wipeleft:duration=0.3000:offset=7.8500[vx2]",
    "[ax1][a2]acrossfade=d=0.3000:c1=tri:c2=tri[ax2]",
    "[vx2]null[vout]",
    "[ax2]apad[aout]",
])


def heute_liste(fmt: str, musik: bool) -> dict:
    arten = [("schnitt", 0.0), ("fade", 0.8), ("wipeleft", 0.3)]
    return {"format": fmt, "fps": 30, "overlay": "clip-battle.de" if fmt == "short" else None,
            "musik": {"pegel": 0.35} if musik else None,
            "segmente": [{"nr": i + 1, "zeit_start": 4.0 * i, "zeit_ende": 4.0 * (i + 1),
                          "uebergang": {"art": a, "dauer_s": d}} for i, (a, d) in enumerate(arten)]}


def voll(fmt: str = "short", *, an: bool = True, look=("cinematic", 0.8), zaehler: bool | None = None) -> dict:
    """Drei Segmente (Zeitleiste 0–3, 3–7, 7–10; Whip, dann Glitch) mit allem, was der Plan kennt."""
    kurz = fmt == "short"
    teile = [("/x/a.mp4", 1.0, 4.0, ("schnitt", 0.0)), ("/x/b.mp4", 1.0, 5.0, ("whip", 0.25)),
             ("/x/c.mp4", 1.0, 4.0, ("glitch", 0.2))]
    ereignisse = [(1, "punch", 1.5, {"dauer_s": 0.35}), (1, "sfx", 1.5, {"klang": "tick"}),
                  (2, "punch", 4.95, {"dauer_s": 0.35}), (2, "sfx", 4.95, {"klang": "basshit"}),
                  (2, "akzent", 6.0, {"dauer_s": 0.2}), (2, "sfx", 3.0, {"klang": "whoosh"}),
                  (3, "meme", 8.0, {"dauer_s": 0.83}), (3, "sfx", 8.0, {"klang": "pop"}),
                  (3, "sfx", 7.0, {"klang": "whoosh"})]
    if kurz:
        ereignisse.append((2, "titel", 5.05, {"dauer_s": 1.2, "text": "DOUBLE KILL"}))
    else:  # 16:9: Titel in der Blende nach Segment 2 (Glitch 7,0 ± 0,1)
        ereignisse.append((2, "titel", 6.9, {"dauer_s": 0.2, "text": "DOUBLE KILL"}))
    if kurz if zaehler is None else zaehler:
        ereignisse += [(1, "zaehler", 1.5, {"dauer_s": 1.5, "zahl": 1}), (2, "zaehler", 4.95, {"dauer_s": 1.5, "zahl": 2})]
    return mini_liste(teile, fmt=fmt, ereignisse=ereignisse, look=look, an=an)


def graph(liste: dict, *, musik: bool = False, schrift: Path | None = SCHRIFT) -> str:
    b, h = (720, 1280) if liste["format"] == "short" else (1280, 720)
    n = len(liste["segmente"])
    if musik:
        liste = {**liste, "musik": {"pegel": 0.3}}
    return entwurf.filtergraph(liste, [2] * n, b=b, h=h, musik_eingang=n if musik else None, schrift=schrift)[0]


def teile_mit(g: str, marke: str) -> list[str]:
    return [t for t in g.split(";") if marke in t]


def beginnt_mit(g: str, label: str) -> str:
    return next(t for t in g.split(";") if t.startswith(label))


class AusIstHeute(unittest.TestCase):
    def test_zeichengleich_mit_dem_graphen_von_vorher(self):
        schrift = Path("/f/DejaVuSans-Bold.ttf")
        for fmt, musik, heute, spuren, (b, h) in (("short", True, HEUTE_SHORT, [2, 1, 0], (720, 1280)),
                                                  ("zusammenschnitt", False, HEUTE_16_9, [2, 2, 1], (1280, 720))):
            for effekte_ in (None, {"an": False}, {"an": False, "look": "cinematic", "look_staerke": 1.0}):
                liste = heute_liste(fmt, musik)
                if effekte_ is not None:
                    liste.update(version=4, effekte=effekte_)
                with self.subTest(fmt=fmt, effekte=effekte_):
                    g, _ = entwurf.filtergraph(liste, spuren, b=b, h=h, musik_eingang=3 if musik else None,
                                               schrift=schrift if fmt == "short" else None)
                    self.assertEqual(g, heute)

    def test_an_false_wie_ohne_plan(self):
        # Derselbe Plan, aber ausgeschaltet: kein einziger Effekt-Filter, gleich wie version 3 ohne Plan
        for fmt in ("short", "zusammenschnitt"):
            for musik in (False, True):
                aus = voll(fmt, an=False)
                with self.subTest(fmt=fmt, musik=musik):
                    g = graph(aus, musik=musik)
                    self.assertEqual(g, graph(ohne_effekte(aus), musik=musik))
                    for effekt in EFFEKT_FILTER:
                        self.assertNotIn(effekt, g)
                    self.assertLessEqual(g.count("drawtext"), 1)  # höchstens clip-battle.de


class Aufbau(unittest.TestCase):
    def test_reihenfolge_und_ein_ausgang(self):
        for fmt in ("short", "zusammenschnitt"):
            for musik in (False, True):
                g = graph(voll(fmt), musik=musik)
                with self.subTest(fmt=fmt, musik=musik):
                    self.assertEqual(g.count("null[vout]"), 1)
                    self.assertEqual(g.count("[vout]"), 1)
                    self.assertTrue(g.split(";")[-1].endswith("[aout]"))
                    # Look vor allen Texten – die Schrift bleibt reinweiß. 16:9: genau einmal nach dem letzten
                    # xfade. Short: je Segment auf Spielbild und kleinem Hintergrund (1/3 bzw. 1/16 der Fläche)
                    if fmt == "short":
                        self.assertEqual(g.count("colorcorrect="), 6)
                        self.assertLess(g.rindex("colorcorrect="), g.index("xfade="))
                    else:
                        self.assertEqual(g.count("colorcorrect="), 1)
                        self.assertLess(g.rindex("xfade="), g.index("colorcorrect="))
                        self.assertLess(g.index("colorcorrect="), g.index("avgblur"))
                    self.assertLess(g.rindex("colorcorrect="), g.index("drawtext"))
                    self.assertLess(g.index("chromashift"), g.index("drawtext"))
                    if fmt == "short":  # clip-battle.de ganz zuletzt, über allem
                        self.assertLess(g.rindex("KILL"), g.index("clip-battle.de"))
                    # Klänge: nach dem Ducking dazugemischt, nie im Schlüssel der Musik
                    self.assertIn("[sfx]amix=inputs=", g)
                    for zeile in teile_mit(g, "sidechaincompress"):
                        self.assertNotIn("sfx", zeile)

    def test_keine_exponenten_und_nichts_verbotenes(self):
        for fmt in ("short", "zusammenschnitt"):
            g = graph(voll(fmt), musik=True)
            with self.subTest(fmt=fmt):
                self.assertIsNone(re.search(r"\d[eE][-+]?\d", g))
                for verboten in ("minterpolate", "lut3d", "colorbalance", "vignette", "vibrance", "zoompan",
                                 "custom", "rgbashift", "curves", "fade=t=in:st", ":color=white"):
                    self.assertNotIn(verboten, g)

    @unittest.skipUnless(HAT_FFMPEG, "ffmpeg fehlt")
    def test_alle_filter_und_uebergaenge_kennt_ffmpeg(self):
        text = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True).stdout
        filter_ = {z.split()[1] for z in text.splitlines() if re.match(r"^ [T.][S.][C.] \S", z)}
        text = subprocess.run(["ffmpeg", "-hide_banner", "-h", "filter=xfade"], capture_output=True, text=True).stdout
        xfade = set(re.findall(r"^\s+(\w+)\s+-?\d+\s+\.\.FV", text, re.M))
        for fmt in ("short", "zusammenschnitt"):
            g = graph(voll(fmt), musik=True)
            with self.subTest(fmt=fmt):
                self.assertEqual(set(filter_namen(g)) - filter_, set())
        # jede Übergangsart des Schemas hat eine eingebaute xfade-Art
        self.assertEqual({effekt_filter.xfade_art(a) for a in effekte.uebergangs_arten()} - xfade, set())

    def test_xfade_namen(self):
        g = graph(voll())
        self.assertIn("xfade=transition=slideleft:duration=0.2500:offset=2.8750", g)   # whip, Mitte auf 3,0
        self.assertIn("xfade=transition=pixelize:duration=0.2000:offset=6.9000", g)    # glitch, Mitte auf 7,0
        self.assertEqual({a: effekt_filter.xfade_art(a) for a in ("whip", "zoom", "glitch", "squeeze", "dissolve",
                                                                   "schnitt", "wipeleft")},
                         {"whip": "slideleft", "zoom": "zoomin", "glitch": "pixelize", "squeeze": "squeezeh",
                          "dissolve": "dissolve", "schnitt": "fade", "wipeleft": "wipeleft"})

    def test_klaenge_als_eingaenge_hinter_der_musik(self):
        liste = voll()
        klaenge, _ = sfx.mischung(effekte.zeitleiste(liste), 4)
        self.assertEqual(klaenge, ["basshit", "tick", "whoosh", "pop"])
        g = graph(liste, musik=True)  # Eingänge: 0–2 Segmente, 3 Musik, 4… Klänge
        for j in range(len(klaenge)):
            self.assertIn(f"[{4 + j}:a]aresample=", g)
        self.assertIn("[3:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,volume=0.3", g)
        self.assertIn("[0:a]aresample", graph(liste).replace("[3:a]", "[0:a]"))  # ohne Musik ab Eingang 3
        self.assertIn("[3:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,asplit", graph(liste))


class Befehl(unittest.TestCase):
    """Der ganze ffmpeg-Aufruf aus rendere – ohne ffmpeg (probe, Klänge und Ausführung nachgestellt)."""

    @staticmethod
    def befehl(liste: dict, *, vaapi: bool = True, groesse: tuple[int, int] = (640, 360)) -> tuple[dict, list[str]]:
        """rendere mit nachgestellten Quellen der Größe `groesse`: (Ergebnis, ffmpeg-Befehl)."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            for n, s in enumerate(liste["segmente"]):
                s["datei"] = str(tmp / f"{n}.mp4")
                Path(s["datei"]).touch()
            k = fx_konfig()
            k.daten["schnitt"]["vaapi_geraet"] = str(tmp / "renderD128")
            if vaapi:
                (tmp / "renderD128").touch()
            befehle = []

            def lauf(befehl, _was, _timeout=None):
                befehle.append(befehl)
                Path(befehl[-1]).write_bytes(b"x")

            info = SimpleNamespace(tonspuren=[1, 2], dauer_s=8.0, breite=groesse[0], hoehe=groesse[1])
            with mock.patch.object(entwurf, "probe", return_value=info), \
                    mock.patch.object(entwurf, "fuehre_aus", side_effect=lauf), \
                    mock.patch.object(sfx, "datei", side_effect=lambda _k, name: Path("/sfx") / f"{name}.wav"), \
                    mock.patch.object(entwurf.shorts, "schrift", return_value=SCHRIFT):
                r = entwurf.rendere(liste, tmp / "ziel.mp4", k)
        return r, befehle[0]

    def test_4_zu_3_quelle_texte_neben_dem_hoeheren_spielbild(self):
        # Fortnite in 4:3 (1440×1080): im Short 720×540 statt 720×406 – rendere misst die Quellen, die Texte rücken mit
        _, befehl = self.befehl(voll(), vaapi=False, groesse=(1440, 1080))
        g = befehl[befehl.index("-filter_complex") + 1]
        oben, unten = effekt_filter.spielbild(720, 1280, 540)
        self.assertEqual((oben, unten), (370, 910))
        for text, art in (("DOUBLE KILL", "titel"), ("KILLS 1", "zaehler")):
            mitte, groesse = effekt_filter.lage(720, 1280, True, len(text), 0.75, 540)[art]
            teil = [t for t in re.split(r",(?=drawtext)", g) if f"text='{text}'" in t][0]
            self.assertIn(f"y='{effekt_filter._z(mitte)}-text_h/2", teil)
            halb = effekt_filter._halbe_hoehe(groesse)          # Gleiten führt vom Spielbild weg
            self.assertTrue(mitte - halb > unten or mitte + halb < oben, (text, mitte, groesse))
        # 16:9-Quelle: wie bisher
        _, befehl = self.befehl(voll(), vaapi=False)
        mitte, _ = effekt_filter.lage(720, 1280, True, len("DOUBLE KILL"), 0.75)["titel"]
        self.assertIn(f"y='{effekt_filter._z(mitte)}-text_h/2", befehl[befehl.index("-filter_complex") + 1])

    def test_vaapi_hinter_allen_effekten_und_klaenge_als_eingaenge(self):
        liste = voll()
        r, befehl = self.befehl(liste)
        self.assertEqual(r["encoder"], "h264_vaapi")
        eingaenge = [befehl[i + 1] for i, x in enumerate(befehl) if x == "-i"]
        self.assertEqual(eingaenge, [liste["segmente"][n]["datei"] for n in range(3)]
                         + [f"/sfx/{name}.wav" for name in ("basshit", "tick", "whoosh", "pop")])
        self.assertLess(befehl.index("-vaapi_device"), befehl.index("-i"))
        g = befehl[befehl.index("-filter_complex") + 1]
        self.assertEqual(g.count("[vout]"), 1)
        ende = g.index("format=nv12,hwupload[vout]")
        for effekt in ("eval=frame", "colorcorrect", "avgblur", "chromashift", "noise=", "KILLS", "DOUBLE KILL",
                       "clip-battle.de"):
            self.assertLess(g.rindex(effekt), ende, effekt)


class Zoom(unittest.TestCase):
    def test_nur_das_spielbild_zoomt(self):
        g = graph(voll())
        for i in range(3):
            with self.subTest(segment=i):
                # Short: auf dem Vordergrund, bevor er in den unscharfen Hintergrund gesetzt wird
                self.assertIn(f"[vg{i}]scale=720:-2,split=2[zo{i}][zs{i}]", g)
                zurueck = beginnt_mit(g, f"[zo{i}][zg{i}]")          # mittig auf das unveränderte Spielbild
                self.assertTrue(zurueck.startswith(f"[zo{i}][zg{i}]overlay=(W-w)/2:(H-h)/2:enable='between(t,"))
                self.assertTrue(zurueck.endswith(f"[vgs{i}]"), zurueck)
                hintergrund = beginnt_mit(g, f"[hg{i}]")
                self.assertNotIn("eval", hintergrund)
                # Look auf dem kleinen Hintergrund (vor dem Hochskalieren) und auf dem Spielbild
                self.assertIn("boxblur=5:2,eq=contrast=1.064:saturation=1.08,colorcorrect=", hintergrund)
                self.assertIn(",colorcorrect=rl=-0.04:bl=0.04:rh=0.04:bh=-0.04[vgs{i}]".replace("{i}", str(i)), zurueck)
        zoom = [t for t in g.split(";") if "eval=frame" in t]
        self.assertEqual(len(zoom), 3)
        self.assertTrue(all(t.startswith("[zs") for t in zoom), zoom)
        # nach der xfade-Kette kein Zoom mehr: Texte und Hintergrund bleiben, wie sie sind
        self.assertNotIn("eval=frame", g[g.rindex("xfade="):])
        # 16:9: das ganze Bild ist Spielbild – Zoom vor dem Rand (pad)
        g = graph(voll("zusammenschnitt"))
        self.assertIn("force_original_aspect_ratio=decrease,split=2[zo1][zs1]", g)
        self.assertIn("[zo1][zg1]overlay=(W-w)/2:(H-h)/2:enable='between(t,2.075,2.425)+between(t,3.125,3.325)',"
                      "pad=1280:720", g)

    def test_zeit_im_segment_und_staerke(self):
        g = graph(voll())
        # Segment 2 beginnt auf der Zeitleiste bei 3,0, vorne 0,125 s Whip-Griff: Punch bei 4,95 -> lokal 2,075
        zoom = beginnt_mit(g, "[zs1]")
        self.assertIn("between(t,2.075,2.425)*0.25*min((t-2.075)/0.05,pow(1-(t-2.125)/0.3,2))", zoom)  # Punch
        self.assertIn("between(t,3.125,3.325)*0.06*", zoom)                                          # Akzent 6,0
        meme = beginnt_mit(g, "[zs2]")                                      # Meme bei 8,0: vorne 0,1 (Glitch)
        self.assertIn("between(t,1.1,1.93)*0.2*min(min((t-1.1)/0.08,1),pow(1-(t-1.78)/0.15,2))", meme)
        # ohne Zoom-Ereignis kein Zoom im Segment (Look bleibt)
        liste = voll()
        liste["segmente"][0]["effekte"] = [e for e in liste["segmente"][0]["effekte"] if e["art"] != "punch"]
        self.assertIn("[vg0]scale=720:-2,eq=contrast=1.064:saturation=1.08,colorcorrect=", graph(liste))
        self.assertIn("[vg0]scale=720:-2[vgs0]", graph(liste | {"effekte": {**liste["effekte"], "look": "neutral"}}))

    def test_zoom_faktor_rechnet_richtig(self):
        # Den ffmpeg-Ausdruck mit Python nachrechnen (dieselben Funktionen, if -> wenn)
        def z(ausdruck: str, t: float) -> float:
            funktionen = {"wenn": lambda c, a, b: a if c else b, "isnan": math.isnan, "min": min, "pow": pow,
                          "between": lambda x, a, b: float(a <= x <= b), "t": t}
            return eval(ausdruck.replace("if(", "wenn("), funktionen)

        punch = effekt_filter.zoom_faktor([("punch", 2.0, 0.35, 1.0)])
        self.assertEqual(punch, "if(isnan(t),1,1+between(t,2,2.35)*0.25*min((t-2)/0.05,pow(1-(t-2.05)/0.3,2)))")
        for t, erwartet in ((1.9, 1.0), (2.0, 1.0), (2.025, 1.125), (2.05, 1.25), (2.2, 1.0625), (2.35, 1.0),
                            (2.5, 1.0), (float("nan"), 1.0)):   # NAN: beim Einrichten von scale
            self.assertAlmostEqual(z(punch, t), erwartet, places=6, msg=t)
        meme = effekt_filter.zoom_faktor([("meme", 5.0, 0.83, 0.5), ("akzent", 7.0, 0.2, 1.0)])
        for t, erwartet in ((5.04, 1.05), (5.3, 1.1), (5.68, 1.1), (5.83, 1.0), (7.05, 1.06), (7.2, 1.0)):
            self.assertAlmostEqual(z(meme, t), erwartet, places=6, msg=t)


class Texte(unittest.TestCase):
    def test_nie_im_spielbild_auch_nicht_beim_pop(self):
        texte = [*effekte.TITEL.values(), effekte.MULTI, effekte.VICTORY]
        for b, h in ((720, 1280), (1080, 1920)):
            oben, unten = effekt_filter.spielbild(b, h)
            logo_unten = h * (effekt_filter.LOGO_Y + effekt_filter.LOGO_GROESSE)
            gleiten = effekt_filter.GLEITEN * h
            with self.subTest(b=b):
                self.assertAlmostEqual(oben / h, 0.34, delta=0.01)       # Spielbild y 34 % … 66 %
                self.assertAlmostEqual(unten / h, 0.66, delta=0.01)
                for text in texte:
                    mitte, groesse = effekt_filter.lage(b, h, True, len(text), 0.75)["titel"]
                    halb = effekt_filter._halbe_hoehe(groesse)
                    self.assertGreater(mitte - halb, unten + 4, text)                 # unter dem Spielbild
                    self.assertLess(mitte + halb, h * effekt_filter.SICHER_UNTEN, text)
                    self.assertLessEqual(0.75 * len(text) * groesse * effekt_filter.POP_SPITZE, 0.76 * b + 1)
                    self.assertGreater(groesse, 0.03 * h, text)                       # trotzdem groß genug
                mitte, groesse = effekt_filter.lage(b, h, True, len("KILLS 99"), 0.75)["zaehler"]
                halb = effekt_filter._halbe_hoehe(groesse)
                self.assertLess(mitte + halb, oben - 4)                              # über dem Spielbild
                self.assertGreater(mitte - halb - gleiten, logo_unten)               # unter clip-battle.de

    def test_hoeheres_spielbild_texte_ruecken_mit_oder_fallen_weg(self):
        # Aufnahmen in 16:10, 4:3, 5:4: das Spielbild ist im Short höher, die Texte bleiben daneben. Lässt ein
        # (fast) quadratisches Spielbild keinen Platz, fällt der Text weg, statt aufs Spielbild zu kommen
        self.assertEqual([effekt_filter.spiel_hoehe(720, *q) for q in ((1920, 1080), (1920, 1200), (1440, 1080))],
                         [406, 450, 540])
        self.assertEqual(effekt_filter.spiel_hoehe(1080, 1920, 1080), 608)
        for b, h in ((720, 1280), (1080, 1920)):
            for quelle in ((1920, 1080), (1920, 1200), (1440, 1080), (1280, 1024)):
                spiel_h = effekt_filter.spiel_hoehe(b, *quelle)
                oben, unten = effekt_filter.spielbild(b, h, spiel_h)
                with self.subTest(b=b, quelle=quelle):
                    self.assertEqual(unten - oben, spiel_h)
                    for text in (effekte.VICTORY, "DOUBLE KILL"):
                        mitte, groesse = effekt_filter.lage(b, h, True, len(text), 0.75, spiel_h)["titel"]
                        self.assertGreater(mitte - effekt_filter._halbe_hoehe(groesse), unten + 2, text)
                        self.assertGreaterEqual(groesse, effekt_filter.TEXT_MIN * h, text)
                    mitte, groesse = effekt_filter.lage(b, h, True, len("KILLS 99"), 0.75, spiel_h)["zaehler"]
                    self.assertLess(mitte + effekt_filter._halbe_hoehe(groesse), oben - 2)
        quadratisch = effekt_filter.spiel_hoehe(720, 1080, 1080)
        ereignisse = [effekte.Ereignis("titel", 1.0, 1.2, 1.0, text="DOUBLE KILL"),
                      effekte.Ereignis("zaehler", 1.0, 1.5, 1.0, zahl=2)]
        texte = effekt_filter.texte(ereignisse, 720, 1280, True, SCHRIFT, 0.75, quadratisch)
        self.assertEqual(["KILLS 2" in t for t in texte], [True])        # der Titel fehlt, der Zähler passt noch

    def test_short_titel_und_zaehler_animiert(self):
        g = graph(voll())
        titel = [t for t in re.split(r",(?=drawtext)", g) if "DOUBLE KILL" in t][0]
        self.assertIn("enable='between(t,5.05,6.25)'", titel)
        self.assertIn("*if(lt((t-5.05),0.1),0.6+0.55*", titel)          # Pop-in über die Größe
        self.assertIn("alpha='1*min(1,(t-5.05)/0.08)*min(1,(6.25-t)/0.2)'", titel)
        self.assertIn("+19.2*max(0,1-(t-5.05)/0.15)", titel)            # gleitet von unten herein
        self.assertEqual(g.count("text='KILLS 1'") + g.count("text='KILLS 2'"), 2)

    def test_16_9_titel_nur_in_der_blende_und_kein_zaehler(self):
        liste = voll("zusammenschnitt", zaehler=True)   # ein Zähler im Plan wird im 16:9 nicht gezeichnet
        g = graph(liste)
        self.assertNotIn("KILLS", g)
        self.assertEqual(g.count("drawtext"), 1)
        fenster = [(a, b) for art, a, b, _ in effekte.uebergangs_fenster(liste) if art == "glitch"][0]
        self.assertEqual(fenster, (6.9, 7.1))
        titel = [t for t in re.split(r",(?=drawtext)", g) if "DOUBLE KILL" in t][0]
        self.assertIn("enable='between(t,6.9,7.1)'", titel)
        self.assertIn("y='360-text_h/2'", titel)            # mitten im Bild, ohne Gleiten
        self.assertIn(":fontsize=100.8:", titel)            # 0,2 s: ohne Pop, nur Ein-/Ausblenden

    def test_ohne_schrift_keine_texte(self):
        self.assertNotIn("drawtext", graph(voll(), schrift=None))


class LookUndBlenden(unittest.TestCase):
    def test_look_werte(self):
        self.assertEqual(effekt_filter.look("cinematic", 0.8),
                         "eq=contrast=1.064:saturation=1.08,colorcorrect=rl=-0.04:bl=0.04:rh=0.04:bh=-0.04")
        self.assertEqual(effekt_filter.look("warm", 0.5),
                         "eq=saturation=1.1:brightness=0.01,colorcorrect=rl=0.01:bl=-0.006:rh=0.01:bh=-0.011")
        self.assertEqual(effekt_filter.look("neutral", 1.0), "")
        self.assertEqual(effekt_filter.look("kalt", 0.0), "")
        self.assertEqual(set(effekt_filter.LOOKS) | {"neutral"}, set(effekte.LOOKS))
        self.assertNotIn("colorcorrect", graph(voll(look=("neutral", 0.0))))

    def test_fensterfilter_nur_in_den_blenden(self):
        g = graph(voll())
        self.assertIn("avgblur=sizeX=32:sizeY=1:enable='between(t,2.875,3.125)'", g)       # Whip 3,0 ± 0,125
        self.assertIn("chromashift=cbh=-7:crh=7:enable='between(t,6.9,7.1)'", g)          # Glitch 7,0 ± 0,1
        self.assertIn("noise=alls=30:allf=t:all_seed=1:enable='between(t,6.9,7.1)'", g)
        # Jump-Cut (harter Schnitt innerhalb einer Serie): kein Fenster, kein Filter
        liste = voll()
        liste["segmente"][1]["uebergang"] = {"art": "schnitt", "dauer_s": 0.0}
        liste["segmente"][2]["uebergang"] = {"art": "schnitt", "dauer_s": 0.0}
        g = graph(liste)
        for filter_ in ("avgblur", "chromashift", "noise="):
            self.assertNotIn(filter_, g)

    def test_glitch_blenden_immer_mit_staerke_1(self):
        # §4: Übergänge mit Stärke 1 – „zu hektisch“ dämpft nur die Beat-Akzente, nie die Glitch-Blende.
        # Mehrere Blenden je Art: ein Filter(paar) mit allen Zeitfenstern
        teile = effekt_filter.fenster([("glitch", 1.0, 1.2, 1.0), ("whip", 3.0, 3.25, 1.0),
                                       ("glitch", 9.0, 9.2, 1.0)], 1080)
        self.assertEqual(teile, ["avgblur=sizeX=48:sizeY=1:enable='between(t,3,3.25)'",
                                 "chromashift=cbh=-10:crh=10:enable='between(t,1,1.2)+between(t,9,9.2)'",
                                 "noise=alls=30:allf=t:all_seed=1:enable='between(t,1,1.2)+between(t,9,9.2)'"])


class Groesse(unittest.TestCase):
    @staticmethod
    def zusammenschnitt_40() -> dict:
        """40 Momente (300 s) mit vielen Kills, Tod, Jubel und Beats alle 0,4 s – mehr als ein echter Abend."""
        stimmungen = ["episch", "spannend", "lustig", "frustriert", "chill"]
        segmente, reihe, t, glitches, je = [], [], 0.0, 0, {}
        for n in range(40):
            st = stimmungen[n % 5]
            art, d = ("schnitt", 0.0) if n == 0 else effekte.uebergang(st, je.get(st, 0), n == 39, regie.PARAMETER,
                                                                         True, glitches)
            glitches += art == "glitch"
            je[st] = je.get(st, 0) + 1
            segmente.append(seg(n + 1, f"m{n}", 2.0, 9.5, t, art=art, d=d, stimmung=st))
            mk = {"kill_sekunden": [3.0, 4.0, 5.5, 7.0, 8.5], "tod_sekunde": 9.0 if st == "frustriert" else None,
                  "jubel_laut_s": [6.2] if st == "lustig" else []}
            reihe.append(moment(f"m{n}", mk, stimmung=st, max_gruppe=5, victory=n == 39))
            t += 7.5
        return plane(segmente, reihe, fmt="zusammenschnitt", beats=[round(0.4 * i, 3) for i in range(1, 750)])

    def test_40_segmente_unter_64_kb(self):
        liste = self.zusammenschnitt_40()
        self.assertGreater(sum(len(s.get("effekte", [])) for s in liste["segmente"]), 250)
        liste["musik"] = {"pegel": 0.3}
        g, _ = entwurf.filtergraph(liste, [2] * 40, b=1920, h=1080, musik_eingang=40, schrift=SCHRIFT)
        self.assertLess(len(g.encode()), entwurf.MAX_GRAPH, f"{len(g.encode())} Bytes")

    def test_zu_gross_meldet_der_planer(self):
        liste, k = self.zusammenschnitt_40(), fx_konfig()
        self.assertEqual(entwurf.graph_fehler(liste, k), [])
        with mock.patch.object(entwurf, "MAX_GRAPH", 10_000):
            self.assertIn("höchstens 10 KB", entwurf.graph_fehler(liste, k)[0])


class PlanerPrueftGroesse(MitRegieMaterial):
    def test_compose_bricht_ab_statt_spaeter_zu_scheitern(self):
        def leer(ziel: Path, **_) -> Path:  # Planen braucht nur die Datei, kein echtes Video
            ziel.parent.mkdir(parents=True, exist_ok=True)
            ziel.touch()
            return ziel

        with mock.patch("tests.regie_hilfen.testvideo", side_effect=leer):
            self.momente_anlegen(MOMENTE[:6])
        self.konfig.daten["regie"]["effekte"]["an"] = True
        with mock.patch.object(entwurf, "MAX_GRAPH", 1000):
            with self.assertRaisesRegex(regie.RegieFehler, "zu groß: Filtergraph"):
                regie.erstelle(self.con, self.konfig, "short")
        self.assertIsNotNone(regie.erstelle(self.con, self.konfig, "short")["entwurf"])
        self.konfig.daten["regie"]["effekte"]["an"] = False  # aus: keine Prüfung, wie vorher
        with mock.patch.object(entwurf, "MAX_GRAPH", 1000):
            self.assertIsNotNone(regie.erstelle(self.con, self.konfig, "short")["entwurf"])
