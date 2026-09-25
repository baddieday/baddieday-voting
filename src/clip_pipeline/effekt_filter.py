"""ffmpeg-Bausteine des Regisseurs 2.0 – übersetzen den Effekt-Plan der Schnittliste in Filter, entscheiden nichts.

Was wann wie stark kommt, steht in der Schnittliste (effekte.plane). Hier wird es nur in ffmpeg-Filter übersetzt:

  XFADE        eigene Übergangsnamen -> eingebaute xfade-Arten (whip -> slideleft, zoom -> zoomin …)
  zoom()       Punch, Beat-Akzent, Meme: nur das SPIELBILD wird vergrößert (je Segment, vor dem Einsetzen in den
               unscharfen Hintergrund bzw. vor dem Rand) – nie der Hintergrund, nie ein Text
  look()       Farblook der Hauptstimmung: eq (Kontrast, Sättigung, Helligkeit) + colorcorrect (Farbstich in
               Schatten und Lichtern). Short: je Segment auf Spielbild und kleinem Hintergrund, 16:9: einmal global
  fenster()    Filter nur während einer Übergangsblende: Whip = waagrechte Bewegungsunschärfe, Glitch =
               Farbversatz + Rauschen (Glitch gibt es nur als Übergang, nie mitten im Spielbild)
  texte()      Kill-Titel und Zähler, animiert (wachsen kurz über ihre Größe hinaus, blenden ein und aus, gleiten
               leicht hinein). Short: nur im unscharfen Rand über/unter dem Spielbild. 16:9: nur der Titel, genau
               während der Blende (der Planer legt ihn dorthin), kein Zähler
  global_kette() (Look) -> Fensterfilter -> Texte: alles, was nach der xfade-Kette auf das ganze Bild wirkt

Zahlen stehen fest mit höchstens 4 Nachkommastellen im Graphen (nie 1e-05), Ausdrücke in '…'.
"""

from __future__ import annotations

from pathlib import Path

from . import effekte, shorts

# Eigene Übergangsnamen -> xfade. Harter Schnitt = fade über ein einziges Bild (siehe entwurf.schnitt_dauer)
XFADE = {"schnitt": "fade", "whip": "slideleft", "zoom": "zoomin", "glitch": "pixelize", "squeeze": "squeezeh",
         "dissolve": "dissolve"}

# Zoom je Art: (Zoom je Stärke 1, hinein s, halten s) – zurück in der restlichen Dauer, weich ((1−u)²)
ZOOM_FORM = {"punch": (0.25, 0.05, 0.0), "akzent": (0.06, 0.05, 0.0), "meme": (0.20, 0.08, 0.60)}

# Looks: eq-Werte (1 = neutral, brightness 0 = neutral) und Farbstich (colorcorrect: Verschiebung von Rot-/Blau-Anteil
# rl/bl in den Schatten, rh/bh in den Lichtern, dazwischen gleitend). Beides rechnet direkt in YUV – curves rechnete
# jedes Bild zweimal in RGB um (gemessen: 3 ms statt 1,2 ms je Bild bei 720×1280, auf dem Mini zählt das).
LOOKS = {
    "cinematic": ({"contrast": 1.08, "saturation": 1.10},         # Teal/Orange: Schatten blaugrün, Lichter warm
                  {"rl": -0.05, "bl": 0.05, "rh": 0.05, "bh": -0.05}),
    "kalt": ({"contrast": 1.08, "saturation": 0.95}, {"rl": -0.012, "bl": 0.025, "rh": -0.015, "bh": 0.012}),
    "warm": ({"saturation": 1.2, "brightness": 0.02}, {"rl": 0.02, "bl": -0.012, "rh": 0.02, "bh": -0.022}),
    "entsaettigt": ({"saturation": 0.55, "brightness": -0.03}, {"bl": 0.012, "bh": 0.005}),  # kühl, etwas dunkler
    "soft": ({"contrast": 0.97}, {"rl": 0.012, "rh": 0.012}),                                # leicht warm
}

# Texte. Größe als Anteil der Schriftgröße; POP_*: Größe beim Erscheinen (von -> Spitze -> 1)
POP_VON, POP_SPITZE = 0.6, 1.15
TINTE = 0.9           # Höhe der Großbuchstaben (mit Unterlänge des Q) je Schriftgröße – eher zu groß geschätzt
RAND = 0.08           # schwarzer Rand je Schriftgröße
TITEL_EIN_S, TITEL_AUS_S, POP_S, GLEITEN_S = 0.08, 0.2, 0.1, 0.15
# Short (9:16): Spielbild 16:9 in der Mitte, clip-battle.de oben bei 0,12·h (entwurf.filtergraph)
LOGO_Y, LOGO_GROESSE = 0.12, 0.03
SICHER_UNTEN = 0.75   # TikTok/Shorts: unten 25 % liegen Beschreibung und Knöpfe
TITEL_GROESSE, TITEL_BREITE = 0.10, 0.76   # höchstens 0,10·b; Breite ≤ 0,76·b (rechts 12 % frei für die Knöpfe)
ZAEHLER_GROESSE = 0.04                    # × h
GLEITEN = 0.015                           # × h: so weit gleitet ein Text von außen herein
TEXT_MIN = 0.015                          # × h: kleiner wird kein Text – dann fällt er weg (kein Platz)
# 16:9: Titel mitten in der Blende
TITEL_16_9_GROESSE, TITEL_16_9_BREITE = 0.14, 0.80


def _z(x: float) -> str:
    """Zahl fest mit höchstens 4 Nachkommastellen, ohne Nullen am Ende (0.05, 2.075, 1) – nie 1e-05. Kurz halten
    lohnt sich: Bei 40 Segmenten stehen Hunderte Zahlen im Graphen, und der muss auf die Befehlszeile passen."""
    text = f"{x:.4f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def xfade_art(art: str) -> str:
    return XFADE.get(art, art)


# --- Zoom (nur Spielbild) -------------------------------------------------------------------

def zoom_faktor(zooms: list[tuple[str, float, float, float]]) -> str:
    """Z(t) als ffmpeg-Ausdruck: 1 + Σ Zoom·Fenster·Hüllkurve. zooms: (Art, Beginn k, Dauer d, Stärke), Zeiten in der
    Zeit des Filters (lokal im Segment). Hüllkurve min(hinein, zurück): hinein (t−k)/ein steigt linear,
    zurück (1−(t−k−ein−halten)/aus)² fällt weich; davor ist das zweite, danach das erste größer als 1.
    between(t,k,k+d) schaltet jeden Summanden außerhalb seines Fensters ab. scale rechnet den Ausdruck beim Einrichten
    einmal aus, vielleicht mit t = NAN – dann gilt Z = 1 (isnan)."""
    teile = []
    for art, k, d, s in zooms:
        a, ein, halten = ZOOM_FORM[art]
        k = max(0.0, k)
        aus = max(0.01, d - ein - halten)
        hinein = f"(t-{_z(k)})/{_z(ein)}"
        if halten > 0:
            hinein = f"min({hinein},1)"
        zurueck = f"pow(1-(t-{_z(k + ein + halten)})/{_z(aus)},2)"
        teile.append(f"between(t,{_z(k)},{_z(k + d)})*{_z(a * s)}*min({hinein},{zurueck})")
    return f"if(isnan(t),1,1+{'+'.join(teile)})"


def zoom(i: int, zooms: list[tuple[str, float, float, float]]) -> str:
    """Filterkette, die das Bild vergrößert und wieder auf die alte Größe beschneidet (Mitte bleibt Mitte).
    scale mit eval=frame rechnet die Größe je Bild neu; overlay setzt das vergrößerte Bild mittig auf das
    unveränderte – das Ergebnis behält dessen Größe, was übersteht, fällt weg. (crop ginge nicht: crop kennt nur die
    Eingangsgröße vom Einrichten und schnitte dann links oben aus.) overlay arbeitet nur während eines Zooms
    (enable), sonst reicht es das unveränderte Bild ohne Rechenarbeit durch."""
    an = "+".join(f"between(t,{_z(max(0.0, k))},{_z(max(0.0, k) + d)})" for _art, k, d, _s in zooms)
    return (f"split=2[zo{i}][zs{i}];"
            f"[zs{i}]scale=w='2*trunc(iw*{zoom_faktor(zooms)}/2)':h=-2:eval=frame[zg{i}];"
            f"[zo{i}][zg{i}]overlay=(W-w)/2:(H-h)/2:enable='{an}'")


# --- Look ------------------------------------------------------------------------------------

def look(name: str, staerke: float) -> str:
    """eq + colorcorrect für einen Look, gemischt mit der Stärke s (0 = neutral, 1 = voller Look): eq-Faktor
    1+(v−1)·s, Helligkeit und Farbstich v·s. Neutral oder s = 0: kein Filter."""
    if name not in LOOKS or staerke <= 0:
        return ""
    s = min(1.0, float(staerke))
    werte, stich = LOOKS[name]
    eq = ":".join(f"{k}={_z(v * s if k == 'brightness' else 1 + (v - 1) * s)}" for k, v in werte.items())
    return f"eq={eq},colorcorrect=" + ":".join(f"{k}={_z(v * s)}" for k, v in stich.items())


# --- Fensterfilter (nur während einer Übergangsblende) ---------------------------------------------------

def _zeiten(fenster: list[tuple[float, float]]) -> str:
    return "+".join(f"between(t,{_z(a)},{_z(b)})" for a, b in fenster)


def fenster(uebergaenge: list[tuple[str, float, float, float]], b: int) -> list[str]:
    """Whip: waagrechte Unschärfe (wie ein schneller Schwenk); Glitch: Farbanteile versetzt + Rauschen
    (all_seed=1: jedes Mal gleich). Ein Filter je Art, eingeschaltet nur in den Blenden (enable).
    Alle rechnen in YUV: Ein RGB-Filter (rgbashift) ließe ffmpeg JEDES Bild umrechnen, auch außerhalb der Blende –
    gemessen 9 ms je Bild bei 720×1280, für 0,2 s Glitch. chromashift versetzt die Farbanteile direkt.
    Übergänge immer mit Stärke 1 (§4)."""
    teile = []
    if whip := [(a, e) for art, a, e, _s in uebergaenge if art == "whip"]:
        teile.append(f"avgblur=sizeX={max(1, round(48 * b / 1080))}:sizeY=1:enable='{_zeiten(whip)}'")
    if glitch := [(a, e) for art, a, e, _s in uebergaenge if art == "glitch"]:
        r = max(1, round(10 * b / 1080))  # in Farbanteil-Pixeln (halbe Breite): 20 Bildpunkte bei 1080
        an = f"enable='{_zeiten(glitch)}'"
        teile += [f"chromashift=cbh=-{r}:crh={r}:{an}", f"noise=alls=30:allf=t:all_seed=1:{an}"]
    return teile


# --- Texte ------------------------------------------------------------------------------------------

def spiel_hoehe(b: int, breite: int, hoehe: int) -> int:
    """Short: Höhe des Spielbilds einer Quelle breite × hoehe nach scale=b:-2 – gerundet wie ffmpeg (auf die
    nächste gerade Zahl). 16:9 bei b = 720: 406; eine 4:3-Aufnahme (1440×1080) wird 540 hoch."""
    return 2 * ((b * hoehe + breite) // (2 * breite))


def spielbild(b: int, h: int, hoehe: int | None = None) -> tuple[int, int]:
    """Short: (oben, unten) des Spielbilds in Pixeln – genau wie in entwurf._bild gerechnet (overlay setzt y auf eine
    gerade Zeile). hoehe: das höchste Spielbild der Liste (spiel_hoehe, entwurf.rendere misst die Quellen); ohne
    Angabe 16:9."""
    hoehe = min(h, hoehe or spiel_hoehe(b, 16, 9))
    oben = ((h - hoehe) // 2) & ~1
    return oben, oben + hoehe


def _halbe_hoehe(groesse: float) -> float:
    """Halbe Höhe eines Texts bei seiner größten Stelle (Pop) samt schwarzem Rand."""
    return TINTE * POP_SPITZE * groesse / 2 + RAND * groesse


def lage(b: int, h: int, hochformat: bool, zeichen: int, zeichenbreite: float,
         spiel_h: int | None = None) -> dict[str, tuple[float, float]]:
    """{"titel"/"zaehler": (Mitte y, Schriftgröße)} in Pixeln. Short: Zähler zwischen clip-battle.de und Spielbild,
    Titel zwischen Spielbild und der unteren Sicherheitszone – beide auch beim Pop mit Rand außerhalb des Spielbilds.
    spiel_h: Höhe des Spielbilds (siehe spielbild) – ein höheres Spielbild (4:3-Aufnahme) lässt weniger Platz."""
    if not hochformat:
        groesse = min(TITEL_16_9_GROESSE * h, TITEL_16_9_BREITE * b / (zeichenbreite * max(1, zeichen)))
        return {"titel": (h / 2, groesse)}
    oben, unten = spielbild(b, h, spiel_h)
    logo_unten = h * (LOGO_Y + LOGO_GROESSE)
    frei_unten = h * SICHER_UNTEN - unten
    groesse = min(TITEL_GROESSE * b, TITEL_BREITE * b / (zeichenbreite * max(1, zeichen) * POP_SPITZE),
                  0.8 * frei_unten / 2 / (TINTE * POP_SPITZE / 2 + RAND))
    zaehler = min(ZAEHLER_GROESSE * h, 0.8 * (oben - logo_unten) / 2 / (TINTE * POP_SPITZE / 2 + RAND))
    return {"titel": (unten + frei_unten / 2, groesse), "zaehler": ((logo_unten + oben) / 2, zaehler)}


def _text(schrift: Path, text: str, t: float, dauer: float, staerke: float, mitte: float, groesse: float,
          gleiten: float) -> str:
    """drawtext mit Pop-in (Größe POP_VON -> POP_SPITZE -> 1), Ein-/Ausblenden und leichtem Hineingleiten
    (gleiten px: > 0 von unten, < 0 von oben). Bei kurzen Texten laufen die Animationen entsprechend schneller;
    unter 0,3 s (Titel in einer schnellen Blende) nur Ein- und Ausblenden."""
    e = t + dauer
    pop, ein, aus, glt = (min(x, dauer / 3) for x in (POP_S, TITEL_EIN_S, TITEL_AUS_S, GLEITEN_S))
    u = f"(t-{_z(t)})"
    groesse_ = _z(groesse)
    if dauer >= 3 * POP_S:
        groesse_ = (f"'{groesse_}*if(lt({u},{_z(pop)}),{_z(POP_VON)}+{_z(POP_SPITZE - POP_VON)}*{u}/{_z(pop)},"
                    f"max(1,{_z(POP_SPITZE)}-{_z(POP_SPITZE - 1)}*({u}-{_z(pop)})/{_z(pop)}))'")
    alpha = f"{_z(min(1.0, staerke))}*min(1,{u}/{_z(ein)})*min(1,({_z(e)}-t)/{_z(aus)})"
    y = f"{_z(mitte)}-text_h/2"
    if gleiten:
        y += f"{'+' if gleiten > 0 else '-'}{_z(abs(gleiten))}*max(0,1-{u}/{_z(glt)})"
    return (f"drawtext=fontfile={shorts._filterpfad(schrift)}:text={shorts._text(text)}:fontsize={groesse_}:"
            f"fontcolor=white:alpha='{alpha}':borderw={max(1, round(RAND * groesse))}:bordercolor=black@0.7:"
            f"x=(w-text_w)/2:y='{y}':enable='between(t,{_z(t)},{_z(e)})'")


def texte(ereignisse: list, b: int, h: int, hochformat: bool, schrift: Path, zeichenbreite: float,
          spiel_h: int | None = None) -> list[str]:
    """Kill-Titel (beide Formate) und Zähler (nur Short) in Zeitreihenfolge. Lässt das Spielbild keinen Platz
    (kleiner als TEXT_MIN · h), fällt der Text weg – er käme sonst aufs Spielbild."""
    teile = []
    for e in ereignisse:
        if e.art == "titel" and e.text:
            mitte, groesse = lage(b, h, hochformat, len(e.text), zeichenbreite, spiel_h)["titel"]
            if groesse >= TEXT_MIN * h:
                teile.append(_text(schrift, e.text, e.t, e.dauer, e.staerke, mitte, groesse,
                                   GLEITEN * h if hochformat else 0.0))
        elif e.art == "zaehler" and e.zahl and hochformat:  # 16:9: kein Platz außerhalb des Spielbilds
            text = f"KILLS {int(e.zahl)}"
            mitte, groesse = lage(b, h, True, len(text), zeichenbreite, spiel_h)["zaehler"]
            if groesse >= TEXT_MIN * h:
                teile.append(_text(schrift, text, e.t, e.dauer, e.staerke, mitte, groesse, -GLEITEN * h))
    return teile


# --- Alles zusammen ----------------------------------------------------------------------------------

def zooms_je_segment(liste: dict, ereignisse: list, griffe: list[tuple[float, float]]) -> dict[int, list]:
    """Segment-Index -> Zoom-Ereignisse (Art, Beginn, Dauer, Stärke) in der Zeit des Segment-Eingangs:
    Zeitleiste − zeit_start + vorderer Griff (gilt auch mit Zeitlupe, die vor fps sitzt)."""
    index = {s.get("nr"): i for i, s in enumerate(liste["segmente"])}
    ergebnis: dict[int, list] = {}
    for e in ereignisse:
        if e.art in effekte.ZOOM and e.staerke > 0 and (i := index.get(e.nr)) is not None:
            s = liste["segmente"][i]
            ergebnis.setdefault(i, []).append((e.art, e.t - s["zeit_start"] + griffe[i][0], e.dauer, e.staerke))
    return ergebnis


def look_der_liste(liste: dict) -> str:
    """Der Look des Videos (leer ohne Effekte oder bei neutral)."""
    fx = liste.get("effekte") or {}
    return look(str(fx.get("look", "neutral")), float(fx.get("look_staerke", 0.0))) if fx.get("an") else ""


def global_kette(liste: dict, ereignisse: list, b: int, h: int, schrift: Path | None, zeichenbreite: float,
                 spiel_h: int | None = None) -> str:
    """Filterkette nach der xfade-Kette (ohne Labels): Look (nur 16:9) -> Fensterfilter -> Texte. Leer ohne Effekte.
    spiel_h: Höhe des Spielbilds im Short (siehe spielbild), ohne Angabe 16:9.
    Die Texte kommen zuletzt: Look und Blenden-Unschärfe verfärben sie nicht, sie bleiben reinweiß.
    Im Short sitzt der Look schon je Segment auf Spielbild und kleinem Hintergrund (entwurf._bild): dort ist das
    Spielbild nur ein Drittel des Bildes, der Hintergrund wird auf 1/16 der Fläche gerechnet – spart zwei Drittel."""
    fx = liste.get("effekte") or {}
    if not fx.get("an"):
        return ""
    teile = [look_der_liste(liste) if liste["format"] != "short" else ""]
    teile += fenster(effekte.uebergangs_fenster(liste), b)
    if schrift is not None:
        teile += texte(ereignisse, b, h, liste["format"] == "short", schrift, zeichenbreite, spiel_h)
    return ",".join(t for t in teile if t)
