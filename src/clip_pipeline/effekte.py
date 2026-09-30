"""Effekt-Planer des Regisseurs 2.0: welcher Effekt wann und wie stark – reines Python, ohne ffmpeg.

Plan ≠ Render: Nur plane() (aufgerufen in regie.erstelle) entscheidet. Jedes Ereignis steht in der Schnittliste
(segmente[].effekte: Quellzeit t_s, Stärke 0..1, feste Namen aus schemas/regie.schema.json). Der Renderer übersetzt
nur – über zeitleiste() und uebergangs_fenster() – und liest nie liste["parameter"]. auf_zeitleiste()/auf_quelle()
sind die EINZIGE Umrechnung zwischen Quelle und Zeitleiste.

Im Spielbild (28.09., Florian: „ruhig viral, viele Effekte, keine doppelten … egal wie lange es rechnet“ – ersetzt
„Spielbild clean“ vom 25.09. in diesem Punkt): Übergänge (Uebergangsmix), Zoom (punch, akzent, meme, shake, tilt,
einzug), Drift (Ken-Burns je Segment), Tempo (plane_tempo: Zeitlupe, Zeitraffer), Farblook und der Katalog BILD
(Blitz, Strobe, Farb-Pop, Kontrast, Farbrad, Negativ, Blur, Pixel, Vignette) plus RGB-Stoß. Finisher, Nebenkills,
Beats und Schnitte ziehen ihre Stile aus eigenen Rotationen (Stilfolge) – nie zweimal derselbe Stil nacheinander,
je Video an anderer Stelle beginnend. Texte liegen weiter außerhalb des Spielbilds: im Short Kill-Titel und Zähler
im unscharfen Rand, im 16:9-Zusammenschnitt kein Zähler und ein Kill-Titel nur während der Übergangsblende direkt
nach dem Segment mit der Serie.

Anker = die sichtbare Aktion: mein Umhauen (merkmale.aktion_sekunden), sonst der Kill. Gezählt wird wie in der
Vorbewertung: Kette = Kills mit ≤ [vorbewertung].multikill_fenster_s Abstand (nach Kill-Zeit).

plane() in dieser Reihenfolge:
   1. sichtbar     Anker ≥ 0,1 s vom Segmentrand bzw. vom Übergangs-Griff entfernt -> segment["kill_s"]
   2. Ketten       über alle Kills des Moments; Ereignisse nur an sichtbaren Ankern
   3. Finisher     die letzte Aktion einer Kette: Stil aus STILE (11 Stile, nur mit Stärke im Profil) + Bass-Hit;
                   die anderen Kills: Stil aus NEBEN_STILE × mini_faktor + Tick
   4. Titel        einmal je Kette ab titel_ab_kette Kills, am Ende der Kette (DOUBLE … PENTA, ab 6 MULTI KILL);
                   VICTORY ROYALE ersetzt überlappende Titel. 16:9: nur in der Blende nach dem Moment (nach seinem
                   letzten Teil – Jump-Cuts liegen innerhalb der Serie)
   5. Zähler       „KILLS n“ je sichtbarem Kill, laufende Summe im Video (nur Short). Short: Titel und Zähler enden
                   spätestens am Anfang einer Zoom-Blende (xfade zoomin vergrößert das ganze Bild, das Spielbild
                   wüchse unter den Text)
   6. Tod          Wackeln (Stärke tod_punch) + Blitz + Einschlag (nur frustriert hat dafür Stärke), kein Titel
   7. Jubel        Meme-Zoom + Pop auf der ersten Jubel-Spitze (nur lustig)
   8. Riser        endet auf dem ersten Kill des Höhepunkts (wenn davor ≥ 1,5 s Video liegen)
   9. Whoosh       auf jedem weichen Übergang (nicht Schnitt, nicht Abblende über Schwarz)
   9b. Einstieg    auf jedem harten Schnitt (auch Jump-Cut, auch ganz vorn): Stil aus EINSTIEG_STILE × einstieg
   9c. Drift       jedes Segment zoomt langsam hinein bzw. heraus (abwechselnd), Stärke drift – ohne Budget
  10. Budget       Zoom-Ereignisse ≥ effekt_abstand_s auseinander (Vorrang Finisher > Meme > Punch/Einstieg > Akzent),
                   kein Zoom-Start in einer Übergangsblende
  11. Beats        Beat-Effekt aus BEAT_STILE × akzent auf einem Musik-Beat, wenn max_ruhe_s lang kein Schnitt und
                   kein anderer Treffer war
  12. Stärke unter der Schwelle fällt weg, Zeiten auf ms
Übergänge haben immer Stärke 1 (§4), auch der Glitch-Übergang.
Gelernt (regie_lernen): effekt_staerke[stimmung] wirkt auf alle Effekte, effekt_hektik nur auf HEKTISCH (Beat-Akzente
und die Impacts flash, shake, rgb).
"""

from __future__ import annotations

import bisect
import copy
import random
from dataclasses import dataclass

from . import schema
from .konfig import Konfig

RAND_S = 0.1             # sichtbar: so weit weg vom Segmentrand bzw. vom Übergangs-Griff
PUNCH_S = 0.35
AKZENT_S = 0.2
FLASH_S = 0.12           # Blitz: kurz, fällt linear ab
SHAKE_S = 0.25           # Wackeln (mit leichtem Zoom, damit kein Rand erscheint)
RGB_S = 0.15             # Farbversatz-Stoß
MEME_S = 0.83            # 0,08 s hinein, 0,6 s halten, 0,15 s zurück
# Dauer im Video je Effekt-Art (Regisseur 2.2, 28.09.: „mehr Effekte, keine doppelten, egal wie lange es rechnet“)
DAUER = {"punch": PUNCH_S, "akzent": AKZENT_S, "meme": MEME_S, "shake": SHAKE_S, "flash": FLASH_S, "rgb": RGB_S,
         "negativ": 0.1, "blur": 0.22, "strobe": 0.4, "farbpop": 0.45, "kontrast": 0.35, "hue": 0.5,
         "vignette": 0.6, "pixel": 0.12, "tilt": 0.5, "einzug": 0.35}
DAUER_MAX_S = 30.0       # Schema: dauer_s ≤ 30 (Drift läuft über ein ganzes Segment)
TITEL_VERSATZ_S = 0.1    # Titel kurz nach der Aktion
TITEL_MAX_S = 1.2
TITEL_MIN_S = 0.4        # kürzer würde er nur aufblitzen -> weglassen
VICTORY_VERSATZ_S = 0.4
VICTORY_MIN_S = 1.0
TEXT_MAX_S = 3.0         # Schema: dauer_s ≤ 3
ZAEHLER_MAX_S = 1.5
RISER_S = 1.5
MAX_KILL_S = 20          # Schema: kill_s maxItems
MAX_JE_SEGMENT = 60      # Schema: effekte maxItems
MAX_JE_LISTE = 1500      # regie.pruefe_liste (MAX_EREIGNISSE)

TITEL = {2: "DOUBLE KILL", 3: "TRIPLE KILL", 4: "QUAD KILL", 5: "PENTA KILL"}
MULTI = "MULTI KILL"      # ab 6
VICTORY = "VICTORY ROYALE"
LOOKS = ("neutral", "cinematic", "kalt", "warm", "entsaettigt", "soft")
LOOK_JE_STIMMUNG = {"episch": "cinematic", "spannend": "kalt", "lustig": "warm", "frustriert": "entsaettigt",
                    "chill": "soft"}
# Zoom-Arten (Overlay auf dem Spielbild, mit Budget): Punch, Beat-Akzent, Meme, Wackeln (Zoom 1,10 + Versatz),
# Tilt (Dutch Angle: Kippen + Zoom 1,12), Einzug (Schnitt beginnt vergrößert und zieht auf)
ZOOM = ("punch", "akzent", "meme", "shake", "tilt", "einzug")
DRIFT = ("drift_ein", "drift_aus")   # Ken-Burns über ein ganzes Segment – auch Overlay, aber ohne Budget
# Filter nur auf dem Spielbild je Segment (effekt_filter.bildfilter); rgb wirkt nach den Übergängen aufs ganze Bild
BILD = ("flash", "strobe", "farbpop", "kontrast", "hue", "negativ", "blur", "pixel", "vignette")
# gedämpft durch effekt_hektik („zu hektisch“) – alles außer Titel, Zähler, Klängen und Drift
HEKTISCH = {"akzent", "flash", "shake", "rgb", "negativ", "blur", "strobe", "farbpop", "kontrast", "hue", "vignette",
            "pixel", "tilt", "einzug"}
# Profil-Schlüssel, wenn er anders heißt als die Art
SCHLUESSEL = {"einzug": "einstieg", "drift_ein": "drift", "drift_aus": "drift"}
# Stil-Rotationen (keine zwei gleichen nacheinander; nur Stile, deren Arten im Profil Stärke haben; Start je Video
# aus der Momentfolge). Finisher = letzte Aktion einer Kette, Neben = Kills davor (× mini_faktor), Beat = Akzente auf
# der Musik (× akzent), Einstieg = jeder harte Schnitt (× einstieg)
STILE = (("punch",), ("punch", "flash"), ("shake", "rgb"), ("punch", "negativ"), ("tilt", "flash"),
         ("punch", "blur"), ("punch", "strobe"), ("shake", "hue"), ("punch", "pixel"),
         ("punch", "kontrast", "vignette"), ("tilt", "farbpop"))
NEBEN_STILE = (("punch",), ("punch", "kontrast"), ("akzent", "farbpop"), ("punch", "vignette"), ("shake",))
BEAT_STILE = (("akzent",), ("farbpop",), ("akzent", "vignette"), ("blur",), ("akzent", "kontrast"), ("hue",),
              ("akzent", "flash"))
EINSTIEG_STILE = (("einzug",), ("einzug", "flash"), ("blur",), ("einzug", "rgb"), ("negativ",),
                  ("einzug", "farbpop"), ("pixel",))
VERGROESSERND = {"zoom"}              # Übergänge, die das GANZE Bild vergrößern (xfade zoomin) – Short: Texte enden davor
# Zoom-Budget: wer gewinnt bei zu engem Abstand (Tod-Punch zählt wie ein Finisher)
RANG_FINISHER, RANG_MEME, RANG_PUNCH, RANG_AKZENT = 3, 2, 1, 0
# Blitz-Sicherheit (Cutter-Maßstab R4, 30.09., Pflicht statt Geschmack): Hell-Dunkel-Effekte beginnen mindestens
# BLITZ_ABSTAND_S auseinander (plane), regie.pruefe_liste zählt nach – höchstens BLITZE_MAX Blitze in jeder Sekunde
# (WCAG 2.3.1; TikTok warnt bei mehr). Strobe zählt anteilig mit effekt_filter.STROBE_HZ.
BLITZ = ("flash", "strobe", "negativ")
BLITZ_ABSTAND_S = 0.4
BLITZE_MAX = 3

_GEMEINSAM = {"mini_faktor": 0.5, "titel_ab_kette": 2}
# Stärken 0..1 je Stimmung (0 = aus). uebergaenge: Pool (Art, Dauer s) für die Übergänge IN Momente dieser Stimmung –
# der Uebergangsmix zieht daraus in gemischten Runden ohne direkte Wiederholung (28.09., Florian: „immer die gleichen
# Übergänge, das ist langweilig“); ohne Mix (Tests, alte Aufrufer) gilt die Reihenfolge als Rotation.
PROFIL = {
    "episch": {**_GEMEINSAM, "punch": 0.7, "titel": 1.0, "zaehler": 1.0, "akzent": 0.5, "max_ruhe_s": 2.5,
               "meme": 0.0, "tod_punch": 0.0, "basshit": 0.9, "tick": 0.5, "whoosh": 0.6, "pop": 0.0,
               "einschlag": 0.0, "riser": 0.6, "lupe": 0.8, "look": ("cinematic", 0.8),
               "uebergaenge": [("schnitt", 0.0), ("whip", 0.25), ("zoom", 0.3), ("schnitt", 0.0), ("whip_up", 0.25),
                               ("flash", 0.15), ("radial", 0.3), ("smoothleft", 0.3), ("glitch", 0.2),
                               ("coverleft", 0.3)]},
    "spannend": {**_GEMEINSAM, "punch": 0.5, "titel": 1.0, "zaehler": 0.8, "akzent": 0.4, "max_ruhe_s": 2.5,
                 "meme": 0.0, "tod_punch": 0.0, "basshit": 0.7, "tick": 0.4, "whoosh": 0.6, "pop": 0.0,
                 "einschlag": 0.0, "riser": 0.5, "lupe": 0.5, "look": ("kalt", 0.6),
                 "uebergaenge": [("whip", 0.25), ("schnitt", 0.0), ("glitch", 0.2), ("zoom", 0.3), ("whip_right", 0.25),
                                 ("hblur", 0.3), ("circleclose", 0.3), ("schnitt", 0.0), ("diagtl", 0.3),
                                 ("flash", 0.12)]},
    "lustig": {**_GEMEINSAM, "punch": 0.3, "titel": 1.0, "zaehler": 0.4, "akzent": 0.3, "max_ruhe_s": 3.0,
               "meme": 0.8, "tod_punch": 0.0, "basshit": 0.0, "tick": 0.3, "whoosh": 0.4, "pop": 0.5,
               "einschlag": 0.0, "riser": 0.0, "lupe": 0.0, "look": ("warm", 0.5),
               "uebergaenge": [("wipeleft", 0.3), ("squeeze", 0.3), ("slideleft", 0.3), ("wipeup", 0.3),
                               ("squeezev", 0.3), ("circleopen", 0.35), ("slidedown", 0.3), ("revealright", 0.3),
                               ("diagbr", 0.3)]},
    "frustriert": {**_GEMEINSAM, "punch": 0.3, "titel": 0.0, "zaehler": 0.6, "akzent": 0.0, "max_ruhe_s": None,
                   "meme": 0.0, "tod_punch": 0.3, "basshit": 0.4, "tick": 0.3, "whoosh": 0.0, "pop": 0.0,
                   "einschlag": 0.7, "riser": 0.0, "lupe": 0.6, "look": ("entsaettigt", 0.7),
                   "uebergaenge": [("fadeblack", 0.5), ("glitch", 0.2), ("hblur", 0.4), ("fade", 0.4),
                                   ("smoothdown", 0.4)]},
    "chill": {**_GEMEINSAM, "punch": 0.0, "titel": 1.0, "zaehler": 0.0, "akzent": 0.0, "max_ruhe_s": None,
              "meme": 0.0, "tod_punch": 0.0, "basshit": 0.0, "tick": 0.0, "whoosh": 0.25, "pop": 0.0,
              "einschlag": 0.0, "riser": 0.0, "lupe": 0.0, "look": ("soft", 0.3),
              "uebergaenge": [("fade", 0.8), ("dissolve", 0.6), ("smoothright", 0.8), ("circleopen", 0.7)]},
}
STAERKEN = {"mini_faktor", "punch", "titel", "zaehler", "akzent", "meme", "tod_punch", "basshit", "tick", "whoosh",
            "pop", "einschlag", "riser", "lupe", "raffer", "flash", "shake", "rgb", "negativ", "blur", "strobe",
            "farbpop", "kontrast", "hue", "vignette", "pixel", "tilt", "einstieg", "drift"}
# Regisseur 2.1/2.2 (28.09.): Tempo, Impacts, Katalog und Dichte je Stimmung (0 = aus). Florian: „das wird langweilig –
# mir ist egal, wie lange es rechnet, hauptsächlich es kommt ein sehr gutes Video raus“. Beat-Akzente kommen darum
# schon nach max_ruhe_s 0,8 s statt 2,5 s, jeder harte Schnitt bekommt einen Einstieg, jedes Segment einen Drift.
NEU = {
    "episch": {"raffer": 0.6, "flash": 0.7, "shake": 0.6, "rgb": 0.5, "negativ": 0.6, "blur": 0.7, "strobe": 0.5,
               "farbpop": 0.7, "kontrast": 0.7, "hue": 0.4, "vignette": 0.7, "pixel": 0.4, "tilt": 0.7,
               "einstieg": 0.8, "drift": 0.6, "akzent": 0.8, "max_ruhe_s": 0.8},
    "spannend": {"raffer": 0.7, "flash": 0.6, "shake": 0.7, "rgb": 0.6, "negativ": 0.7, "blur": 0.6, "strobe": 0.6,
                 "farbpop": 0.5, "kontrast": 0.8, "hue": 0.5, "vignette": 0.8, "pixel": 0.6, "tilt": 0.6,
                 "einstieg": 0.8, "drift": 0.5, "akzent": 0.8, "max_ruhe_s": 0.8},
    "lustig": {"raffer": 0.3, "flash": 0.3, "shake": 0.5, "rgb": 0.0, "negativ": 0.0, "blur": 0.3, "strobe": 0.3,
               "farbpop": 0.9, "kontrast": 0.5, "hue": 0.8, "vignette": 0.3, "pixel": 0.5, "tilt": 0.8,
               "einstieg": 0.6, "drift": 0.4, "akzent": 0.6, "max_ruhe_s": 1.2, "lupe": 0.4},
    "frustriert": {"raffer": 0.0, "flash": 0.5, "shake": 0.6, "rgb": 0.0, "negativ": 0.5, "blur": 0.6, "strobe": 0.0,
                   "farbpop": 0.0, "kontrast": 0.6, "hue": 0.0, "vignette": 0.9, "pixel": 0.3, "tilt": 0.4,
                   "einstieg": 0.5, "drift": 0.5, "akzent": 0.4, "max_ruhe_s": 2.0},
    "chill": {"raffer": 0.0, "flash": 0.0, "shake": 0.0, "rgb": 0.0, "negativ": 0.0, "blur": 0.3, "strobe": 0.0,
              "farbpop": 0.4, "kontrast": 0.0, "hue": 0.0, "vignette": 0.4, "pixel": 0.0, "tilt": 0.0,
              "einstieg": 0.3, "drift": 0.8, "akzent": 0.3, "max_ruhe_s": 2.5},
}
for _st, _werte in NEU.items():
    PROFIL[_st].update(_werte)


def schluessel(art: str) -> str:
    """Profil-Schlüssel einer Effekt-Art (einzug -> einstieg, drift_* -> drift, sonst die Art selbst)."""
    return SCHLUESSEL.get(art, art)


def moegliche_stile(pool: tuple, pr: dict) -> list[tuple[str, ...]]:
    """Stile aus pool, deren Arten im Profil alle Stärke haben (punch beim Finisher zählt immer – er trägt den Bass-Hit).
    Leer, wenn keiner geht."""
    return [s for s in pool if all(a == "punch" or float(pr.get(schluessel(a), 0.0)) > 0 for a in s)]


class Stilfolge:
    """Reihum durch einen Stil-Pool, Start je Video aus dem Seed – nie zweimal derselbe Stil nacheinander, auch wenn
    zwischendurch die Stimmung (und damit die Auswahl) wechselt. Beispiel episch, Finisher: punch · punch+flash ·
    shake+rgb · punch+negativ · tilt+flash … (je Video an anderer Stelle beginnend)."""

    def __init__(self, pool: tuple, seed: str):
        self.pool, self.k, self.letzter = pool, random.Random(seed).randrange(len(pool)), None

    def naechster(self, pr: dict) -> tuple[str, ...]:
        moeglich = moegliche_stile(self.pool, pr)
        if not moeglich:
            return ()
        for _ in range(len(moeglich)):
            stil = moeglich[self.k % len(moeglich)]
            self.k += 1
            if stil != self.letzter or len(moeglich) == 1:
                break
        self.letzter = stil
        return stil


def finisher_stil(pr: dict, k: int) -> tuple[str, ...]:
    """Stil des k-ten Finishers aus STILE ohne Seed (Rotation ab dem ersten) – für Übersichten und Tests; plane()
    nutzt Stilfolge (Start je Video verschieden). Fallback ohne passenden Stil: ("punch",)."""
    moeglich = moegliche_stile(STILE, pr) or [STILE[0]]
    return moeglich[k % len(moeglich)]

# [regie.effekte]: Standardwerte und Grenzen. Weitere Schlüssel (z. B. für Export oder Hook) lesen andere Module selbst.
STANDARD = {"an": True, "profil_version": 2, "schwelle": 0.15, "effekt_abstand_s": 0.4, "max_glitch": 1,
            "max_lupen": 8, "max_raffer": 6,
            "sfx_ordner": "/var/lib/clip-pipeline/sfx", "sfx_pegel": 0.8, "titel_zeichenbreite": 0.75}
GRENZEN = {"profil_version": (1, 99), "schwelle": (0.0, 1.0), "effekt_abstand_s": (0.0, 5.0), "max_glitch": (0, 20),
           "max_lupen": (0, 20), "max_raffer": (0, 20), "sfx_pegel": (0.0, 2.0), "titel_zeichenbreite": (0.3, 1.5)}

# Tempo (28.09., Florian: „ruhig viral, mit slowmo und beschleunigt“) – Fenster in Quellsekunden
LUPE_VOR_S, LUPE_NACH_S = 0.35, 0.45   # um den Finisher: 0,8 s -> 1,6 s im Video (Faktor 0,5)
LUPE_DRAMA_S = 0.2                     # Faktor 0,25 (episch, Serie ≥ 3 oder Victory): ±0,2 s -> 1,6 s im Video
LUPE_MIN_S = 0.3                       # kleiner wird das Fenster nicht geschrumpft, dann fällt die Lupe weg
LUPE_MAX_S = 1.5                       # Prüfer: längstes Zeitlupen-Fenster
RAFFER_MAX_S = 4.0                     # längstes Zeitraffer-Fenster -> 2 s im Video (Faktor 2)
RAFFER_MIN_S = 1.5                     # kürzer lohnt kein Raffer
RAFFER_ANLAUF_S = 3.0                  # erst ab so viel Anlauf vor der ersten Aktion
RAFFER_ABSTAND_S = 0.8                 # der Raffer endet so weit vor der ersten Aktion – die sieht man normal
QUELLE_REST_S = 0.25                   # am Dateiende bleibt so viel frei (wie regie.plane_zeitleiste: nutzbar)


@dataclass
class Ereignis:
    """Ein Effekt auf der Zeitleiste (t in Sekunden im Video) – das, was der Renderer umsetzt. nr = Segment."""
    art: str
    t: float
    dauer: float
    staerke: float
    text: str | None = None
    zahl: int | None = None
    klang: str | None = None
    nr: int = 0


@dataclass
class _Plan:
    """Ereignis während der Planung: Segment-Index i, Zeit t auf der Zeitleiste, Vorrang im Zoom-Budget."""
    art: str
    i: int
    t: float
    staerke: float
    dauer: float | None = None
    text: str | None = None
    zahl: int | None = None
    klang: str | None = None
    rang: int = 0


# --- Profil und Konfiguration ------------------------------------------------------------------

def _zahl(wert) -> bool:
    return isinstance(wert, (int, float)) and not isinstance(wert, bool)


def _grenze(wert: float, unten: float, oben: float) -> float:
    return round(max(unten, min(oben, float(wert))), 3)


def uebergangs_arten() -> list[str]:
    """Erlaubte Übergangs-Arten – eine Quelle: das Schema der Schnittliste."""
    segment = schema.lade("regie")["properties"]["segmente"]["items"]
    return list(segment["properties"]["uebergang"]["properties"]["art"]["enum"])


def profil(konfig: Konfig, stimmung: str) -> tuple[dict, list[str]]:
    """PROFIL[stimmung] mit deinen Überschreibungen aus [regie.effekte.<stimmung>]: (Profil, Hinweise).
    Stärken werden auf 0..1 gestutzt; look/uebergaenge nur mit bekannten Namen, sonst Hinweis (wie vorgaben()).

    Beispiel in config/lokal.toml:
        [regie.effekte.episch]
        punch = 0.5                   # sanfterer Zoom
        look = "warm"                 # und look_staerke = 0.4
        uebergaenge = [["whip", 0.25], ["schnitt", 0]]"""
    prof = copy.deepcopy(PROFIL[stimmung])
    hinweise: list[str] = []
    roh = konfig.wert(f"regie.effekte.{stimmung}", {})
    if not isinstance(roh, dict):
        return prof, [f"regie.effekte.{stimmung} ignoriert (kein Abschnitt)"]
    arten = uebergangs_arten()
    for name, wert in roh.items():
        wo = f"regie.effekte.{stimmung}.{name}"
        if name == "look" and wert in LOOKS:
            prof["look"] = (wert, prof["look"][1])
        elif name == "look_staerke" and _zahl(wert):
            prof["look"] = (prof["look"][0], _grenze(wert, 0.0, 1.0))
        elif name == "uebergaenge" and isinstance(wert, list) and wert and all(
                isinstance(u, list) and len(u) == 2 and u[0] in arten and _zahl(u[1]) for u in wert):
            prof["uebergaenge"] = [(u[0], 0.0 if u[0] == "schnitt" else _grenze(u[1], 0.0, 3.0)) for u in wert]
        elif name == "max_ruhe_s" and _zahl(wert):
            prof["max_ruhe_s"] = None if wert <= 0 else _grenze(wert, 0.5, 10.0)   # 0 = keine Beat-Akzente
        elif name == "titel_ab_kette" and _zahl(wert):
            prof["titel_ab_kette"] = int(_grenze(wert, 2, 6))
        elif name in STAERKEN and _zahl(wert):
            prof[name] = _grenze(wert, 0.0, 1.0)
        else:
            hinweise.append(f"{wo} ignoriert (unbekannt oder ungültiger Wert)")
    return prof, hinweise


def einstellungen(konfig: Konfig) -> tuple[dict, list[str]]:
    """[regie.effekte] mit Standardwerten und den Profilen je Stimmung: (Einstellungen, Hinweise)."""
    roh = konfig.wert("regie.effekte", {}) or {}
    e, hinweise = dict(STANDARD), []
    for name, wert in (roh.items() if isinstance(roh, dict) else []):
        if name in PROFIL:
            continue  # Stimmungs-Abschnitt, siehe profil()
        if isinstance(wert, dict):
            hinweise.append(f"regie.effekte.{name} ignoriert (keine Stimmung)")
        elif name in STANDARD:
            standard = STANDARD[name]
            if isinstance(standard, (bool, str)) and type(wert) is type(standard):
                e[name] = wert
            elif name in GRENZEN and _zahl(wert):
                unten, oben = GRENZEN[name]
                e[name] = int(_grenze(wert, unten, oben)) if isinstance(standard, int) else _grenze(wert, unten, oben)
            else:
                hinweise.append(f"regie.effekte.{name} ignoriert (falscher Typ)")
    e["profile"] = {}
    for stimmung in PROFIL:
        e["profile"][stimmung], h = profil(konfig, stimmung)
        hinweise += h
    return e, hinweise


def staerke(wert: float, p: dict, stimmung: str, effekt: str, schwelle: float = STANDARD["schwelle"]) -> float:
    """Profilwert × gelernte effekt_staerke[stimmung] (× effekt_hektik bei hektischen Effekten), 0..1.
    Unter der Schwelle -> 0 (Effekt fällt weg)."""
    s = float(wert) * float((p.get("effekt_staerke") or {}).get(stimmung, 1.0))
    if effekt in HEKTISCH:
        s *= float(p.get("effekt_hektik", 1.0))
    s = max(0.0, min(1.0, s))
    return 0.0 if s < schwelle - 1e-9 else round(s, 3)


def titel_text(n: int) -> str:
    return TITEL.get(n, MULTI) if n >= 2 else ""


# --- Ketten und Anker ------------------------------------------------------------------------

def _ketten(paare: list[tuple[float, float]], fenster_s: float) -> list[list[tuple[float, float]]]:
    """(Kill, Anker)-Paare nach Kill-Zeit zu Ketten: ≤ fenster_s zum vorherigen Kill (auf ms: 17,3 − 7,3 = 10)."""
    gruppen: list[list[tuple[float, float]]] = []
    for paar in sorted(paare):
        if gruppen and round(paar[0] - gruppen[-1][-1][0], 3) <= fenster_s:
            gruppen[-1].append(paar)
        else:
            gruppen.append([paar])
    return gruppen


def ketten(kill_s: list[float], fenster_s: float) -> list[list[float]]:
    """Float-Variante von vorbewertung.gruppiere: Kette = Kills mit höchstens fenster_s Abstand zum vorherigen."""
    return [[k for k, _ in kette] for kette in _ketten([(float(k), float(k)) for k in kill_s], fenster_s)]


def kills_mit_anker(mk: dict) -> list[tuple[float, float]]:
    """(Kill, Anker) je Kill, nach Kill-Zeit sortiert. Anker = mein Umhauen (wie im Schnitt, regie._aktionen),
    bei alten Momenten ohne aktion_sekunden der Kill selbst."""
    from .regie import _aktionen  # regie importiert dieses Modul

    kills = [float(k) for k in mk.get("kill_sekunden") or []]
    aktionen = _aktionen(mk)
    return sorted(zip(kills, aktionen if aktionen is not None else kills))


# --- Übergänge -----------------------------------------------------------------------------

class Uebergangsmix:
    """Zieht die Übergänge eines Entwurfs aus dem Pool je Stimmung (Profil „uebergaenge“).

    Deterministisch aus einem Seed (regie: die Momentfolge) – derselbe Entwurf wird immer gleich gebaut, ein anderer
    bekommt andere Übergänge. Gemischte Runden: jede Art des Pools kommt einmal dran, bevor eine wiederkommt; nie
    zweimal dieselbe weiche Art nacheinander (harte Schnitte dürfen sich folgen); höchstens max_glitch Glitches,
    danach die nächste Art der Runde. Beispiel: Pool [whip, schnitt, glitch] → z. B. glitch, whip, schnitt | schnitt,
    whip, (glitch gesperrt) …"""

    def __init__(self, seed: str):
        self._rnd = random.Random(seed)
        self._runden: dict[str, list[tuple[str, float]]] = {}
        self.letzte: str | None = None

    def waehle(self, stimmung: str, pool: list[tuple[str, float]], glitch_zaehler: int,
               max_glitch: int) -> tuple[str, float]:
        def ohne_gedeckelte(runde: list[tuple[str, float]]) -> list[tuple[str, float]]:
            return [x for x in runde if x[0] != "glitch"] if glitch_zaehler >= max_glitch else runde

        rest = ohne_gedeckelte(self._runden.get(stimmung) or [])
        if not rest:  # Runde aufgebraucht (oder nur Gedeckeltes übrig): neue Runde mischen
            rest = list(pool)
            self._rnd.shuffle(rest)
            rest = ohne_gedeckelte(rest)
        if not rest:  # Pool besteht nur aus Glitch
            self._runden[stimmung], self.letzte = rest, "whip"
            return "whip", 0.25
        # nie dieselbe weiche Art nacheinander; geht es nicht anders (winziger Pool), die erste der Runde
        n = next((n for n, (art, _) in enumerate(rest) if art == "schnitt" or art != self.letzte), 0)
        art, dauer = rest.pop(n)
        self._runden[stimmung] = rest
        self.letzte = art
        return art, dauer


def uebergang(stimmung: str, index: int, ist_hoehepunkt: bool, p: dict, an: bool, glitch_zaehler: int, *,
              profil_: dict | None = None, max_glitch: int = STANDARD["max_glitch"],
              mix: Uebergangsmix | None = None) -> tuple[str, float]:
    """Übergang in einen Moment: (Art, Dauer s). Aus: regie.UEBERGANG wie bisher. An: aus dem Pool des Profils –
    mit mix (regie.plane_zeitleiste) gemischt ohne Wiederholung (Uebergangsmix), ohne mix als Rotation
    (index = frühere Momente derselben Stimmung, mehr als max_glitch Glitches -> Whip); in einen
    epischen/spannenden Höhepunkt harter Schnitt auf den Drop. Dauer × uebergang_faktor."""
    if not an:
        from .regie import UEBERGANG

        art, dauer = UEBERGANG[stimmung]
    elif ist_hoehepunkt and stimmung in ("episch", "spannend"):
        art, dauer = "schnitt", 0.0
        if mix is not None:
            mix.letzte = art
    elif mix is not None:
        art, dauer = mix.waehle(stimmung, (profil_ or PROFIL[stimmung])["uebergaenge"], glitch_zaehler, max_glitch)
    else:
        folge = (profil_ or PROFIL[stimmung])["uebergaenge"]
        art, dauer = folge[index % len(folge)]
        if art == "glitch" and glitch_zaehler >= max_glitch:
            art, dauer = "whip", 0.25
    return art, round(dauer * float(p.get("uebergang_faktor", 1.0)), 3)


def uebergangs_fenster(liste: dict) -> list[tuple[str, float, float, float]]:
    """(Art, von, bis, Stärke) je weichem Übergang auf der Zeitleiste: die Blende um die Segmentgrenze.
    Harte Schnitte (auch Jump-Cuts innerhalb einer Serie) haben kein Fenster. Stärke: bei Übergängen immer 1 (§4)."""
    fenster = []
    for i, s in enumerate(liste["segmente"]):
        u = s["uebergang"]
        if i > 0 and u["art"] != "schnitt" and u["dauer_s"] > 0:
            d = u["dauer_s"]
            fenster.append((u["art"], round(s["zeit_start"] - d / 2, 3), round(s["zeit_start"] + d / 2, 3), 1.0))
    return fenster


def _in_fenster(t: float, fenster: list[tuple[float, float]]) -> bool:
    return any(a + 1e-6 < t < b - 1e-6 for a, b in fenster)


# --- Zeit: die einzige Umrechnung Quelle <-> Zeitleiste ----------------------------------------------

def tempo_fenster(seg: dict) -> list[tuple[float, float, float]]:
    """(ab, bis, faktor) der Tempo-Fenster eines Segments in Quellzeit, aufsteigend – der Zeitraffer (raffer, im
    Anlauf) liegt vor der Zeitlupe (lupe, um den Finisher). Leer ohne beides."""
    return sorted((float(w["ab_s"]), float(w["bis_s"]), float(w["faktor"]))
                  for w in (seg.get("raffer"), seg.get("lupe")) if w)


def _zuschlag(seg: dict, t_q: float) -> float:
    return sum((min(t_q, bis) - ab) * (1 / f - 1) for ab, bis, f in tempo_fenster(seg) if t_q > ab)


def auf_zeitleiste(seg: dict, t_q: float) -> float:
    """Quellzeit (Sekunden in der Moment-Datei) -> Zeit im Video. Ein Tempo-Fenster dehnt (Zeitlupe, Faktor < 1)
    bzw. staucht (Zeitraffer, Faktor > 1) ab_s … bis_s um 1/faktor."""
    return seg["zeit_start"] + (t_q - seg["quelle_start_s"]) + _zuschlag(seg, t_q)


def auf_quelle(seg: dict, t_z: float) -> float:
    """Umkehrung von auf_zeitleiste."""
    u, q = t_z - seg["zeit_start"], seg["quelle_start_s"]   # Rest im Segment, laufende Quellposition
    for ab, bis, f in tempo_fenster(seg):
        if u <= ab - q:
            return q + u
        u -= ab - q
        gedehnt = (bis - ab) / f
        if u <= gedehnt:
            return ab + u * f
        u -= gedehnt
        q = bis
    return q + u


def zeitleiste(liste: dict) -> list[Ereignis]:
    """Alle Effekt-Ereignisse auf der Zeitleiste, sortiert. Leer, wenn die Effekte aus sind (oder version 3).
    dauer = Dauer im Video (bei sfx 0: der Klang hat seine eigene Länge)."""
    if not (liste.get("effekte") or {}).get("an"):
        return []
    ergebnis = []
    for s in liste["segmente"]:
        for e in s.get("effekte") or []:
            ergebnis.append(Ereignis(e["art"], round(auf_zeitleiste(s, e["t_s"]), 3), float(e.get("dauer_s", 0.0)),
                                     float(e["staerke"]), e.get("text"), e.get("zahl"), e.get("klang"), s["nr"]))
    return sorted(ergebnis, key=lambda e: (e.t, e.nr, e.art, e.klang or ""))


# --- Planer -------------------------------------------------------------------------------

def _sichtbar(segmente: list[dict], i: int) -> tuple[float, float]:
    """Quellfenster, in dem ein Anker zu sehen ist: RAND_S weg vom Rand, bei weichen Übergängen zusätzlich die
    halbe Blende (dort mischen sich zwei Bilder)."""
    s = segmente[i]
    r_v = s["uebergang"]["dauer_s"] / 2 if i > 0 and s["uebergang"]["art"] != "schnitt" else 0.0
    return s["quelle_start_s"] + r_v + RAND_S, s["quelle_ende_s"] - _r_hinten(segmente, i) - RAND_S


def _r_hinten(segmente: list[dict], i: int) -> float:
    if i + 1 >= len(segmente):
        return 0.0
    u = segmente[i + 1]["uebergang"]
    return u["dauer_s"] / 2 if u["art"] != "schnitt" else 0.0


def _wo(grenzen: list[tuple[float, float]], idx: list[int], t_q: float) -> int | None:
    """Das Segment aus idx, in dessen sichtbarem Quellfenster t_q liegt (None: in keinem)."""
    return next((i for i in idx if grenzen[i][0] - 1e-6 <= t_q <= grenzen[i][1] + 1e-6), None)


def _je_moment(segmente: list[dict]) -> dict[str, list[int]]:
    """Moment -> seine Segment-Indizes (ohne Hook, der plant selbst)."""
    ergebnis: dict[str, list[int]] = {}
    for i, s in enumerate(segmente):
        if s.get("rolle") != "hook":
            ergebnis.setdefault(s["moment"], []).append(i)
    return ergebnis


# --- Tempo: Zeitlupe und Zeitraffer -----------------------------------------------------------

def _lupe_setzen(segmente: list[dict], grenzen: list, i: int, ab: float, bis: float, faktor: float) -> bool:
    """Zeitlupe ab … bis (Quelle) in Segment i. Die Zeitleiste bleibt: die Quelle wird hinten um den Zuschlag
    gekürzt – nie in die Muss-Zone, nie ins Fenster, nie in den hinteren Griff. Passt es nicht, schrumpft das
    Fenster um den Anker (bis LUPE_MIN_S), sonst keine Lupe. Rückgabe: gesetzt?"""
    s = segmente[i]
    ab, bis = max(ab, grenzen[i][0]), min(bis, grenzen[i][1])
    mitte = (ab + bis) / 2
    for _ in range(4):
        if bis - ab < LUPE_MIN_S - 1e-9:
            return False
        zuschlag = (bis - ab) * (1 / faktor - 1)
        qe_neu = s["quelle_ende_s"] - zuschlag
        if qe_neu >= max(s["muss"][1], bis + RAND_S + _r_hinten(segmente, i)) - 1e-6:
            s["lupe"] = {"ab_s": round(ab, 3), "bis_s": round(bis, 3), "faktor": faktor, "ton": "tief"}
            s["quelle_ende_s"] = round(qe_neu, 3)
            return True
        ab, bis = mitte - (mitte - ab) * 0.7, mitte + (bis - mitte) * 0.7
    return False


def _raffer_setzen(segmente: list[dict], i: int, ab: float, bis: float) -> bool:
    """Zeitraffer (Faktor 2) ab … bis (Quelle) in Segment i: das Fenster wird halb so lang, dafür nimmt das Segment
    hinten die andere Hälfte mehr Quelle – nur so weit, wie die Datei (QUELLE_REST_S, hinterer Griff) und ein
    folgender Teil desselben Moments es hergeben. Bleibt weniger als RAFFER_MIN_S Fenster, kein Raffer."""
    s = segmente[i]
    frei = s["quelle_dauer_s"] - QUELLE_REST_S - _r_hinten(segmente, i) - s["quelle_ende_s"]
    if i + 1 < len(segmente) and segmente[i + 1]["moment"] == s["moment"] and segmente[i + 1].get("teil", 1) > 1:
        frei = min(frei, segmente[i + 1]["quelle_start_s"] - s["quelle_ende_s"])
    if lupe := s.get("lupe"):  # der Raffer endet vor der Zeitlupe
        bis = min(bis, lupe["ab_s"])
    laenge = min(bis - ab, RAFFER_MAX_S, 2 * frei)
    if laenge < RAFFER_MIN_S - 1e-9:
        return False
    s["raffer"] = {"ab_s": round(bis - laenge, 3), "bis_s": round(bis, 3), "faktor": 2.0, "ton": "tempo"}
    s["quelle_ende_s"] = round(s["quelle_ende_s"] + laenge / 2, 3)
    return True


def plane_tempo(segmente: list[dict], reihe: list, p: dict, konfig: Konfig, fmt_name: str) -> dict:
    """Speed-Ramps in die Segmente (Felder lupe und raffer, vor effekte.plane): Zeitlupe um den Finisher der
    längsten Kette je Moment (Faktor 0,5; episch mit Serie ≥ 3 oder Victory 0,25), Zeitraffer (Faktor 2) über einen
    langen Anlauf vor der ersten Aktion – beides darf im selben Segment liegen (schnell hin, langsam auf den Kill).
    Höchstens [regie.effekte].max_lupen bzw. max_raffer je Video – Vorrang: der
    Höhepunkt, dann die längere Serie, dann mehr Punkte; Raffer: der längere Anlauf. Stärke wie jeder Effekt:
    Profil (lupe, raffer) × gelernte effekt_staerke, unter der Schwelle keiner. Die Zeitleiste (Beats) bleibt –
    _lupe_setzen/_raffer_setzen passen nur die Quelle an. Rückgabe {"lupen": n, "raffer": n}."""
    e, _ = einstellungen(konfig)
    prof, schwelle = e["profile"], e["schwelle"]
    kette_s = float(konfig.wert("vorbewertung.multikill_fenster_s", 10.0))
    momente = {k.schluessel: k for k in reihe}
    for s in segmente:  # neu planen = von vorn
        s.pop("lupe", None)
        s.pop("raffer", None)
    grenzen = [_sichtbar(segmente, i) for i in range(len(segmente))]
    lupen: list[tuple[tuple, int, float, float, float]] = []
    raffer: list[tuple[float, int, float, float]] = []
    for moment, idx in _je_moment(segmente).items():
        k = momente.get(moment)
        paare = kills_mit_anker(k.merkmale if k is not None else {})
        if not paare:
            continue
        alle = _ketten(paare, kette_s)
        kette = alle[max(range(len(alle)), key=lambda j: (len(alle[j]), j))]
        a = max(anker for _, anker in kette)  # der Finisher: die letzte Aktion der längsten Kette
        if (i := _wo(grenzen, idx, a)) is not None:
            st = segmente[i]["stimmung"]
            if staerke(prof[st]["lupe"], p, st, "lupe", schwelle) > 0:
                anzahl = max(len(kette), int(k.max_gruppe or 0))
                drama = st == "episch" and (anzahl >= 3 or bool(k.victory))
                faktor, vor, nach = (0.25, LUPE_DRAMA_S, LUPE_DRAMA_S) if drama else (0.5, LUPE_VOR_S, LUPE_NACH_S)
                lupen.append(((0 if k is reihe[-1] else 1, -anzahl, -float(k.punkte), i), i, a - vor, a + nach, faktor))
        erster = min(anker for _, anker in paare)
        if (i := _wo(grenzen, idx, erster)) is not None:
            st = segmente[i]["stimmung"]
            if staerke(prof[st]["raffer"], p, st, "raffer", schwelle) > 0:
                ab, bis = grenzen[i][0], erster - RAFFER_ABSTAND_S
                if bis - ab >= RAFFER_ANLAUF_S - 1e-9:
                    raffer.append((bis - ab, i, ab, bis))
    n_lupen = n_raffer = 0
    for _vorrang, i, ab, bis, faktor in sorted(lupen, key=lambda x: x[0]):
        if n_lupen >= int(e["max_lupen"]):
            break
        n_lupen += _lupe_setzen(segmente, grenzen, i, ab, bis, faktor)
    for _anlauf, i, ab, bis in sorted(raffer, key=lambda x: (-x[0], x[1])):
        if n_raffer >= int(e["max_raffer"]):
            break
        n_raffer += _raffer_setzen(segmente, i, ab, bis)
    return {"lupen": n_lupen, "raffer": n_raffer}


def _wichtig(e: _Plan) -> int:
    """Was bei zu vielen Ereignissen (Schema-Grenzen) zuletzt wegfällt."""
    if e.art == "titel":
        return 7
    if e.art == "sfx":
        return {"basshit": 6, "einschlag": 6, "riser": 5, "whoosh": 5, "pop": 4}.get(e.klang or "", 1)
    if e.art in ("punch", "shake", "tilt"):
        return 6 if e.rang >= RANG_FINISHER else 2
    return {"meme": 4, "zaehler": 3, "flash": 3, "rgb": 3, "negativ": 3, "blur": 3, "strobe": 3, "pixel": 3,
            "einzug": 2, "hue": 2, "farbpop": 2, "kontrast": 2, "vignette": 2, "drift_ein": 1, "drift_aus": 1
            }.get(e.art, 0)


def plane(segmente: list[dict], reihe: list, p: dict, konfig: Konfig, fmt_name: str, fps: int,
          beats: list[float], *, stimmung: str) -> dict:
    """Füllt je Segment kill_s (sichtbare Anker) und effekte (Ereignisse in Quellzeit) und gibt liste["effekte"]
    zurück. reihe: die Kandidaten (regie.Kandidat) der Segmente; beats: alle Track-Beats − Versatz (leer ohne Musik);
    stimmung: Hauptstimmung des Videos (Look)."""
    e, _ = einstellungen(konfig)
    prof = e["profile"]
    kurz = fmt_name == "short"
    schwelle, abstand = e["schwelle"], e["effekt_abstand_s"]
    kette_s = float(konfig.wert("vorbewertung.multikill_fenster_s", 10.0))
    gleich = 1.0 / max(1, int(fps))  # näher als ein Bild = gleichzeitig
    momente = {k.schluessel: k for k in reihe}
    alle_fenster = uebergangs_fenster({"segmente": segmente})
    fenster = [(a, b) for _, a, b, _ in alle_fenster]
    gross = [(a, b) for art, a, b, _ in alle_fenster if art in VERGROESSERND]
    plan: list[_Plan] = []

    def stark(i: int, wert: float, effekt: str) -> float:
        return staerke(wert, p, segmente[i]["stimmung"], effekt, schwelle)

    def dazu(ziel: list, art: str, i: int, t: float, wert: float, **felder) -> None:
        if wert > 0:
            ziel.append(_Plan(art, i, round(t, 3), wert, **felder))

    for s in segmente:  # neu planen = von vorn
        s.pop("kill_s", None)
        s.pop("effekte", None)
    je_moment = _je_moment(segmente)  # Hook (Stufe 4) plant seine Ereignisse selbst
    grenzen = [_sichtbar(segmente, i) for i in range(len(segmente))]

    def wo(idx: list[int], t_q: float) -> int | None:
        return _wo(grenzen, idx, t_q)

    titel: list[_Plan] = []
    victory: list[_Plan] = []
    gezaehlt: list[tuple[float, int]] = []   # (Zeit, Segment) je sichtbarem Kill
    # Stil-Rotationen, je Video an anderer Stelle beginnend (Seed = Momentfolge, deterministisch)
    seed = "|".join(s["moment"] for s in segmente)
    finisher = Stilfolge(STILE, "fin" + seed)
    neben = Stilfolge(NEBEN_STILE, "neb" + seed)

    def stil_setzen(ziel: list, stil: tuple, i: int, t: float, basis: float | None, rang: int) -> None:
        """Alle Arten eines Stils zur Zeit t. basis None: jede Art mit ihrer Profil-Stärke; sonst basis × Profil-Stärke
        (die Art, deren Schlüssel die Basis selbst liefert – akzent, einstieg –, bekommt nur die Basis)."""
        pr = prof[segmente[i]["stimmung"]]
        for art in stil:
            wert = float(pr[schluessel(art)])
            if basis is not None:
                wert = basis if schluessel(art) in ("akzent", "einstieg") else basis * wert
            dazu(ziel, art, i, t, stark(i, wert, art), dauer=DAUER[art], rang=rang if art in ZOOM else 0)

    for moment, idx in je_moment.items():
        k = momente.get(moment)
        mk = k.merkmale if k is not None else {}
        paare = kills_mit_anker(mk)
        # 1. sichtbar
        for i in idx:
            if sichtbar := sorted({round(a, 3) for _, a in paare if wo([i], a) is not None}):
                segmente[i]["kill_s"] = sichtbar[:MAX_KILL_S]
        # 2./3. Ketten, Finisher
        alle = _ketten(paare, kette_s)
        laengste = max(range(len(alle)), key=lambda j: (len(alle[j]), j)) if alle else -1
        for j, kette in enumerate(alle):
            fin = max(range(len(kette)), key=lambda n: (kette[n][1], kette[n][0], n))  # letzte Aktion der Kette
            for n, (_, a) in enumerate(kette):
                if (i := wo(idx, a)) is None:
                    continue
                pr, t = prof[segmente[i]["stimmung"]], auf_zeitleiste(segmente[i], a)
                if n == fin:  # Stil-Rotation: kein Kill sieht aus wie der vorige
                    stil_setzen(plan, finisher.naechster(pr) or ("punch",), i, t, None, RANG_FINISHER)
                    dazu(plan, "sfx", i, t, stark(i, pr["basshit"], "basshit"), klang="basshit")
                else:
                    stil_setzen(plan, neben.naechster(pr) or ("punch",), i, t, float(pr["mini_faktor"]), RANG_PUNCH)
                    dazu(plan, "sfx", i, t, stark(i, pr["tick"], "tick"), klang="tick")
                gezaehlt.append((t, i))
            # 4. Titel: einmal je Kette, an ihrem Ende. Die längste Kette des Moments heißt wie seine Serie
            # (max_gruppe, wie Bot und Elo zählen; ein Kill der Serie kann vor der Datei liegen) – ein einzelner
            # sichtbarer Kill wird aber nie zum Multikill
            anzahl = len(kette)
            if j == laengste and anzahl >= 2 and k is not None:
                anzahl = max(anzahl, int(k.max_gruppe or 0))
            if (i := wo(idx, kette[fin][1])) is not None and anzahl >= prof[segmente[i]["stimmung"]]["titel_ab_kette"]:
                t = auf_zeitleiste(segmente[i], kette[fin][1]) + TITEL_VERSATZ_S
                dazu(titel, "titel", i, t, stark(i, prof[segmente[i]["stimmung"]]["titel"], "titel"),
                     text=titel_text(anzahl), rang=anzahl)
        if k is not None and k.victory and alle:
            letzte = alle[-1]
            a = max(a for _, a in letzte)
            if (i := wo(idx, a)) is not None:
                s = segmente[i]
                start, ende = auf_zeitleiste(s, a) + VICTORY_VERSATZ_S, s["zeit_ende"]
                if ende - start < VICTORY_MIN_S:
                    start = max(s["zeit_start"], ende - VICTORY_MIN_S)
                dazu(victory, "titel", i, start, stark(i, prof[s["stimmung"]]["titel"], "titel"), text=VICTORY,
                     dauer=min(TEXT_MAX_S, ende - start), rang=99)
        # 6. Tod, 7. Jubel
        if (tod := mk.get("tod_sekunde")) is not None and (i := wo(idx, float(tod))) is not None:
            pr, t = prof[segmente[i]["stimmung"]], auf_zeitleiste(segmente[i], float(tod))
            dazu(plan, "shake", i, t, stark(i, pr["tod_punch"], "shake"), dauer=SHAKE_S, rang=RANG_FINISHER)
            dazu(plan, "flash", i, t, stark(i, pr["flash"], "flash"), dauer=FLASH_S)
            dazu(plan, "sfx", i, t, stark(i, pr["einschlag"], "einschlag"), klang="einschlag")
        for jubel in sorted(float(x) for x in mk.get("jubel_laut_s") or []):
            if (i := wo(idx, jubel)) is not None:
                pr, t = prof[segmente[i]["stimmung"]], auf_zeitleiste(segmente[i], jubel)
                dazu(plan, "meme", i, t, stark(i, pr["meme"], "meme"), dauer=MEME_S, rang=RANG_MEME)
                dazu(plan, "sfx", i, t, stark(i, pr["pop"], "pop"), klang="pop")
                break

    plan += _titel_setzen(titel, victory, segmente, fenster, kurz, gross)
    if kurz:  # 5. Zähler (im 16:9 kein Zähler)
        plan += _zaehler(gezaehlt, segmente, prof, stark, gleich, gross)
    # 8. Riser in den ersten sichtbaren Kill des Höhepunkts
    if reihe and (idx := je_moment.get(reihe[-1].schluessel)):
        erste = min(((auf_zeitleiste(segmente[i], a), i) for i in idx for a in segmente[i].get("kill_s", [])),
                    default=None)
        if erste is not None and erste[0] >= RISER_S - 1e-6:
            t, i = erste
            dazu(plan, "sfx", i, t, stark(i, prof[segmente[i]["stimmung"]]["riser"], "riser"), klang="riser")
    # 9. Whoosh auf den weichen Übergängen (Spitze auf dem Schnitt)
    for i, s in enumerate(segmente[1:], 1):
        if s["uebergang"]["art"] not in ("schnitt", "fadeblack") and s["uebergang"]["dauer_s"] > 0:
            dazu(plan, "sfx", i, s["zeit_start"], stark(i, prof[s["stimmung"]]["whoosh"], "whoosh"), klang="whoosh")
    # 9b. Einstieg auf jedem harten Schnitt (auch Jump-Cut und ganz vorn) und 9c. Drift über jedes Segment
    einstieg = Stilfolge(EINSTIEG_STILE, "ein" + seed)
    for i, s in enumerate(segmente):
        if s.get("rolle") == "hook":
            continue
        pr = prof[s["stimmung"]]
        if (i == 0 or s["uebergang"]["art"] == "schnitt") and (stil := einstieg.naechster(pr)):
            stil_setzen(plan, stil, i, s["zeit_start"], float(pr["einstieg"]), RANG_PUNCH)
        art = DRIFT[i % 2]   # abwechselnd hinein und heraus
        dazu(plan, art, i, s["zeit_start"], stark(i, pr["drift"], art),
             dauer=min(DAUER_MAX_S, s["zeit_ende"] - s["zeit_start"]))
    # 10. Budget: Zoom nie in einer Blende, Abstand ≥ effekt_abstand_s; gleichzeitige Treffer-Klänge nur einmal
    zooms = []
    for z in sorted((z for z in plan if z.art in ZOOM and not _in_fenster(z.t, fenster)),
                    key=lambda z: (-z.rang, -z.staerke, z.t, z.i)):
        if all(abs(z.t - b.t) >= abstand - 1e-9 for b in zooms):
            zooms.append(z)
    plan = _klaenge_einmal([z for z in plan if z.art not in ZOOM], gleich) + zooms
    # 11. Beat-Effekte (Rotation) – nicht direkt vor/nach einem anderen Treffer
    if beats:
        belegt = [z.t for z in plan if z.art in ZOOM or z.art in BILD or z.art == "rgb"]
        plan += _akzente(beats, segmente, prof, stark, belegt, fenster, abstand, Stilfolge(BEAT_STILE, "beat" + seed))
    # 12. Blitz-Sicherheit: flash, strobe, negativ mindestens BLITZ_ABSTAND_S auseinander
    plan = _blitz_abstand([z for z in plan if z.staerke > 0])
    _speichern(plan, segmente)

    look, look_staerke = prof[stimmung]["look"]
    look_staerke = staerke(look_staerke, p, stimmung, "look", schwelle)
    return {"an": True, "profil_version": int(e["profil_version"]), "look": look if look_staerke > 0 else "neutral",
            "look_staerke": look_staerke, "hook": False, "loop": False}


def _moment_ende(segmente: list[dict], i: int) -> int:
    """Letztes Segment des Moments von Segment i: Teile nach einem Jump-Cut (teil > 1) gehören noch dazu."""
    while (i + 1 < len(segmente) and segmente[i + 1]["moment"] == segmente[i]["moment"]
           and segmente[i + 1].get("teil", 1) > 1):
        i += 1
    return i


def _bis_blende(t: float, dauer: float, gross: list[tuple[float, float]]) -> float:
    """Short: Ein Text endet spätestens am Anfang der nächsten Zoom-Blende. Dort vergrößert xfade zoomin das ganze
    Bild, das Spielbild wüchse über den unscharfen Rand unter den Text (Ü2)."""
    return min([dauer, *(max(0.0, a - t) for a, b in gross if b > t + 1e-6)])


def _titel_setzen(titel: list[_Plan], victory: list[_Plan], segmente: list[dict], fenster: list[tuple[float, float]],
                  kurz: bool, gross: list[tuple[float, float]]) -> list[_Plan]:
    """Short: Titel am Ende der Kette, bis zum nächsten Titel (≤ 1,2 s, ≤ Segmentende, ≤ Anfang einer Zoom-Blende);
    VICTORY ROYALE ersetzt überlappende. 16:9: ein Titel nur während der Blende direkt nach dem Moment (nach seinem
    letzten Teil) – bei hartem Schnitt oder am Ende keiner; wollen mehrere dorthin, gewinnt der höchste."""
    if not kurz:
        je_fenster: dict[int, _Plan] = {}
        for z in [*titel, *victory]:
            i = _moment_ende(segmente, z.i)
            if i + 1 >= len(segmente):
                continue
            u = segmente[i + 1]["uebergang"]
            if u["art"] == "schnitt" or u["dauer_s"] <= 0:
                continue
            a = segmente[i + 1]["zeit_start"] - u["dauer_s"] / 2
            neu = _Plan("titel", i, round(a, 3), z.staerke, dauer=u["dauer_s"], text=z.text, rang=z.rang)
            alt = je_fenster.get(i)
            if alt is None or (neu.rang, neu.t) > (alt.rang, alt.t):
                je_fenster[i] = neu
        return list(je_fenster.values())
    for z in titel:
        z.dauer = min(TITEL_MAX_S, segmente[z.i]["zeit_ende"] - z.t)
    for v in victory:  # ersetzt überlappende Kill-Titel
        titel = [z for z in titel if z.t + z.dauer <= v.t + 1e-6 or z.t >= v.t + v.dauer - 1e-6]
    alle = sorted([*titel, *victory], key=lambda z: z.t)
    for z, naechster in zip(alle, alle[1:]):
        z.dauer = min(z.dauer, naechster.t - z.t)
    for z in alle:
        z.dauer = _bis_blende(z.t, z.dauer, gross)
    return [z for z in alle if z.dauer >= TITEL_MIN_S - 1e-6 and not _in_fenster(z.t, fenster)]


def _zaehler(gezaehlt: list[tuple[float, int]], segmente: list[dict], prof: dict, stark, gleich: float,
             gross: list[tuple[float, float]]) -> list[_Plan]:
    """„KILLS n“ je sichtbarem Kill in Zeitleisten-Reihenfolge; gleichzeitige Kills zählen zusammen. Endet vor einer
    Zoom-Blende (_bis_blende)."""
    gruppen: list[list[tuple[float, int]]] = []
    for t, i in sorted(gezaehlt):
        if gruppen and t - gruppen[-1][0][0] < gleich:
            gruppen[-1].append((t, i))
        else:
            gruppen.append([(t, i)])
    ergebnis, summe = [], 0
    for n, gruppe in enumerate(gruppen):
        summe += len(gruppe)
        t, i = gruppe[0]
        bis = gruppen[n + 1][0][0] if n + 1 < len(gruppen) else t + ZAEHLER_MAX_S
        wert = stark(i, prof[segmente[i]["stimmung"]]["zaehler"], "zaehler")
        dauer = _bis_blende(t, min(ZAEHLER_MAX_S, bis - t), gross)
        if wert > 0 and dauer > 0:  # gezählt wird auch ohne Anzeige (z. B. chill)
            ergebnis.append(_Plan("zaehler", i, round(t, 3), wert, dauer=dauer, zahl=min(99, summe)))
    return ergebnis


def _klaenge_einmal(ereignisse: list[_Plan], gleich: float) -> list[_Plan]:
    """Treffer-Klänge (Bass-Hit, Tick, Einschlag) zur selben Zeit nur einmal: der wichtigste bleibt."""
    treffer = {"einschlag": 3, "basshit": 2, "tick": 1}
    behalten: list[_Plan] = []
    for z in sorted(ereignisse, key=lambda z: (-treffer.get(z.klang or "", 0), -z.staerke, z.t)):
        if z.klang in treffer and any(b.klang in treffer and abs(b.t - z.t) < gleich for b in behalten):
            continue
        behalten.append(z)
    return behalten


def _akzente(beats: list[float], segmente: list[dict], prof: dict, stark, belegt: list[float],
             fenster: list[tuple[float, float]], abstand: float, folge: Stilfolge | None = None) -> list[_Plan]:
    """Beat-Effekt auf einem Musik-Beat, wenn max_ruhe_s lang kein Schnitt und kein anderer Treffer war – nicht in
    einer Blende, nicht kurz vor einem anderen Treffer, nicht über das Segmentende hinaus. Der Stil kommt aus der
    Rotation (folge, BEAT_STILE: Zoom-Puls, Farb-Pop, Vignette, Blur, Kontrast, Farbrad, Blitz …); ohne folge wie
    früher nur der kleine Zoom. Stärke: akzent × Profil-Stärke der Art (der Zoom-Puls: akzent)."""
    starts = [s["zeit_start"] for s in segmente]
    dauer = segmente[-1]["zeit_ende"] if segmente else 0.0
    belegt = sorted(belegt)
    ergebnis: list[_Plan] = []
    for b in sorted({round(float(x), 3) for x in beats}):
        if not 0 < b < dauer:
            continue
        i = bisect.bisect_right(starts, b) - 1
        s = segmente[i]
        pr = prof[s["stimmung"]]
        basis = float(pr["akzent"])
        if s.get("rolle") == "hook" or pr["max_ruhe_s"] is None or stark(i, basis, "akzent") <= 0:
            continue
        ruhe = pr["max_ruhe_s"]
        k = bisect.bisect_right(belegt, b)
        ende = s["zeit_ende"] - _r_hinten(segmente, i) + 1e-6
        if (b - s["zeit_start"] < ruhe - 1e-6 or (k and b - belegt[k - 1] < ruhe - 1e-6)
                or (k < len(belegt) and belegt[k] - b < abstand - 1e-9)
                or b + AKZENT_S > ende or _in_fenster(b, fenster)):
            continue
        stil = folge.naechster(pr) if folge is not None else ("akzent",)
        for art in stil:
            if b + DAUER[art] > ende:  # passt nicht mehr ins Segment
                continue
            wert = basis if art == "akzent" else basis * float(pr[schluessel(art)])
            if (w := stark(i, wert, art)) > 0:
                ergebnis.append(_Plan(art, i, b, w, dauer=DAUER[art], rang=RANG_AKZENT))
        bisect.insort(belegt, b)
    return ergebnis


def _blitz_abstand(plan: list[_Plan]) -> list[_Plan]:
    """Von Blitzen (BLITZ), die näher als BLITZ_ABSTAND_S beieinander beginnen, bleibt der stärkere (bei Gleichstand
    der frühere); alles andere bleibt unverändert. Mit Strobe-Dauer 0,4 s ergibt das höchstens 3 Blitze je Sekunde."""
    behalten: list[_Plan] = []
    for z in sorted((z for z in plan if z.art in BLITZ), key=lambda z: (-z.staerke, z.t, z.i)):
        if all(abs(z.t - b.t) >= BLITZ_ABSTAND_S - 1e-9 for b in behalten):
            behalten.append(z)
    bleibt = {id(z) for z in behalten}
    return [z for z in plan if z.art not in BLITZ or id(z) in bleibt]


def blitze(ereignisse: list[Ereignis], strobe_hz: float | None = None) -> tuple[float, float]:
    """(höchste Zahl Blitze in einem 1-s-Fenster, Beginn dieses Fensters) – für regie.pruefe_liste und die Messung.
    flash und negativ zählen je 1, strobe anteilig: strobe_hz (effekt_filter.STROBE_HZ) je Sekunde seiner Dauer im
    Fenster (0,4 s bei 2,5 Hz = 1). Die Fenster beginnen an jedem Blitz. Beispiel: flash bei 1,0/1,4/1,8 → (3, 1.0)."""
    if strobe_hz is None:
        from .effekt_filter import STROBE_HZ as strobe_hz
    b = [e for e in ereignisse if e.art in BLITZ]
    bestes = (0.0, 0.0)
    for start in sorted({e.t for e in b}):
        ende, n = start + 1.0, 0.0
        for e in b:
            if e.art == "strobe":
                n += max(0.0, min(e.t + e.dauer, ende) - max(e.t, start)) * strobe_hz
            elif start - 1e-9 <= e.t < ende - 1e-9:
                n += 1
        if n > bestes[0] + 1e-9:
            bestes = (round(n, 3), start)
    return bestes


def _speichern(ereignisse: list[_Plan], segmente: list[dict]) -> None:
    """Ereignisse als Quellzeit ins Segment (Schema-Form); zu viele -> das Unwichtigste fällt weg."""
    je_segment: dict[int, list[_Plan]] = {}
    for z in ereignisse:
        je_segment.setdefault(z.i, []).append(z)
    for i, liste in je_segment.items():
        je_segment[i] = sorted(liste, key=lambda z: (-_wichtig(z), z.t))[:MAX_JE_SEGMENT]
    alle = sorted((z for liste in je_segment.values() for z in liste), key=lambda z: (-_wichtig(z), z.t, z.i))
    for z in sorted(alle[:MAX_JE_LISTE], key=lambda z: (z.i, z.t, z.art, z.klang or "")):
        s = segmente[z.i]
        eintrag: dict = {"art": z.art,
                         "t_s": round(min(max(auf_quelle(s, z.t), s["quelle_start_s"]), s["quelle_ende_s"]), 3),
                         "staerke": round(z.staerke, 3)}
        if z.dauer is not None:
            eintrag["dauer_s"] = round(max(0.0, min(DAUER_MAX_S, z.dauer)), 3)
        for feld in ("text", "zahl", "klang"):
            if getattr(z, feld) is not None:
                eintrag[feld] = getattr(z, feld)
        s.setdefault("effekte", []).append(eintrag)
