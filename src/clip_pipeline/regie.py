"""Der Regisseur (`pipeline compose`): aus Momenten mit Stimmung wird eine Schnittliste (JSON).

Formate:
  zusammenschnitt  16:9, 75–120 s (Ziel 100 s, lernbar)
  short            9:16, 30–75 s (Ziel 45 s, lernbar), 4–10 Momente, unscharfer Rand, Schriftzug "clip-battle.de"

Schritte (jeder für sich nachvollziehbar, Zahlen in PARAMETER und [regie] der Konfig):
  1. Auswahl     Punkte je Moment: dieselbe Bewertung wie im Clip-Bot (Spec §8.2: vorbewertung.roh_score über
                 merkmale.fuer_moment mit den aktuellen Gewichten – Kill-Serie, Victory, Replay- und Mic-Merkmale),
                 dazu Elo und gelernte Vorlieben (Stimmung und je Moment aus deinen 👍/👎).
                 Abwechslung: Wer im letzten Entwurf war, verliert 70 % seiner Punkte, im vorletzten 35 % usw.
                 (zusammen höchstens 100 %) – so kommen nicht immer dieselben Momente, die stärksten aber
                 regelmäßig wieder. Als Anteil, weil die Punkte weit streuen (Einzelkill 1, Vierfach-Kill 10,
                 Victory +5): ein fester Abzug ließe die stärksten immer vorn.
                 Der Abzug allein reicht nicht (27.09., Simulation mit 120 Momenten: nur 18 verschiedene in 20
                 Shorts – ein 12-Punkte-Moment liegt mit 70 % Abzug immer noch vor jedem Einzelkill). Deshalb dazu:
                 Cooldown – ein Moment aus einem der letzten `cooldown_entwuerfe` Entwürfe ist gesperrt (Reserve,
                 falls das Material sonst nicht reicht); Frische-Quote – mindestens `frische_quote` der gewählten
                 Momente war in keinem Entwurf des Fensters. Beides nur bei abwechslung > 0 (0 = wie früher).
                 Verworfene Clips nie; höchstens n Momente aus demselben Match. Fehlt die Moment-Datei, nimmt
                 der Regisseur den Bot-Clip (gleicher Inhalt, ohne Nachschnitt) statt den Moment still wegzulassen.
                 Dieselbe Spielszene unter mehreren Schlüsseln (szenen.py, 07.10.) kommt je Video nur einmal, und
                 Abwechslung/Cooldown gelten je Szene. Nach ❌ → 🥱 (p["fassung"], fassung_kandidaten): die stärkere
                 Hälfte des abgelehnten Videos bleibt, die schwächere wird nur in dieser Fassung durch ungesehene
                 Szenen ersetzt (erst der Abend, dann starke früherer Abende), sonst KeineNeuenSzenen.
                 Nachschub (Stufe 4, 08.10., einfacher Modus): Reicht ein Abend nicht (weniger als 4 ungesehene starke
                 Szenen, oder auch mit mehr Anlauf zu kurz), kommen nie gezeigte starke Szenen früherer Abende dazu
                 (höchstens 12 Tage alt, nachschub_matches) – nach dem Abend, nie mehr als vom Abend (nachschub_darf),
                 nie vorn (abend_vorn). Dieselben Grenzen gelten für die 🥱-Fassung.
                 Abwechslung mit Ermüdung (08.10., einfacher Modus, ersetzt dort Abzug, Cooldown und Frische-Quote):
                 neue Szenen zuerst; eine Szene, die gerade erst (48 h) in einem deiner letzten 3 Videos lief, ist nie
                 Kandidat; bekannte verlieren je Einsatz der letzten 30 Tage die Hälfte ihrer Punkte, höchstens die
                 Hälfte des Videos bekannte (bis zur Mindestzahl 4 dürfen es mehr sein), höchstens 2 Szenen, die schon
                 zusammen in einem Video waren (szenen.verlauf, p["ermuedung"]). Eine neue Fassung nach ❌ darf die
                 Szenen des Videos nehmen, das sie ersetzt (p["ersetzt"]). Erreicht 🎬 (oder eine neue Fassung) vom
                 Abend sein Ziel nicht, mischt es aus den letzten Tagen (p["mix"]), zuletzt mit lockerer
                 Zusammenstellung (p["locker"], _mit_lockerung) – die Länge geht vor.
  2. Bogen       Einstieg = zweitstärkster Moment (Hook), dann steigend, bei ~60 % eine Atempause
                 (lustig/chill), der stärkste zum Schluss. Keine gleiche Stimmung / kein gleiches Match
                 zweimal hintereinander, wenn es sich vermeiden lässt.
  3. Musik       passend zur vorherrschenden Stimmung (Quellen-Stimmung, Energie relativ zur Bibliothek,
                 Tempo); Start so versetzt, dass der "Drop" des Titels auf den Höhepunkt fällt.
  4. Schnitt     jede Grenze auf einem Beat (jedem n-ten); kein Kill wird abgeschnitten. Mit Aktions-Zeiten
                 (merkmale.aktion_sekunden = mein Umhauen) beginnt der Moment vor der ersten Aktion; eine Serie
                 (≥ 2 Kills) bleibt ein Stück bis serie_max_s, Pausen > luecke_max_s zwischen zwei Aktionen
                 werden per Jump-Cut übersprungen (Teile desselben Moments, harter Schnitt).
                 Zu kurz (Stufe 4, 08.10., einfacher Modus): Bleibt ein Short unter 30 s oder mehr als 10 s unter dem
                 Ziel, plant der Bot einmal neu mit mehr Anlauf und Ausklang (mindestens 4 / 3 s) aus denselben
                 Szenen (mehr_anlauf); reicht auch das nicht, kommt ZuKurz – nie Füllmaterial.
  5. Übergänge   je Stimmung des folgenden Moments; die Mitte des Übergangs liegt genau auf dem Beat.
                 Mit Effekten (Regisseur 2.0): Rotation aus effekte.PROFIL, in einen epischen/spannenden Höhepunkt
                 ein harter Schnitt auf den Drop.
  6. Effekte     effekte.plane: Zoom-Punch, Kill-Titel, Zähler, Klänge, Look – als Plan in der Schnittliste
                 (version 4). Aus mit [regie.effekte] an = false: Schnitt und Übergänge wie vorher.
🔥 Viral (05.10., viral.py): erstelle(…, variante="twist"|"highlight"|"fail") baut einen Short, dessen Mischung der
Bot selbst wählt – Highlights, Highlights mit 1–2 Fails/Gags als Twist oder ein reines Fail-Video. Auswahl und
Reihenfolge nach KI-Einschätzung (viral.py) plus Gelerntem; Werkzeuge wie Hook-Teaser (Segment rolle „hook“ vorn),
Zeitlupe und Standbild am Tod schaltet der Plan (Parameter), nicht eine feste Regel. Fail-Momente (fail.py) kommen nur
in dieses Format – die normalen Shorts und Zusammenschnitte bleiben wie bisher.
Die Schnittliste wird gegen schemas/regie.schema.json und fachlich geprüft, bevor sie gespeichert wird.
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from . import effekte, entwurf, lernen, material, schema, szenen
from .db import BEWERTET, hart_verworfen
from .konfig import Konfig
from .merkmale import fuer_moment, stimmen_gebraucht
from .musik import ZIEL
from .vorbewertung import roh_score
from .zeit import aus_iso, iso, jetzt

log = logging.getLogger("pipeline")

FORMATE = {
    # Dauer gesamt, Segmentlänge, Serie am Stück (Multikill, Teile per Jump-Cut zusammen), Auflösung
    "zusammenschnitt": {"min_s": 75.0, "max_s": 120.0, "ziel_s": 100.0, "seg_min_s": 3.0, "seg_max_s": 25.0,
                        "serie_max_s": 30.0,
                        "b": 1920, "h": 1080},
    # 28.09. (Florian): Shorts 30–75 s aus 4–10 Momenten; Start-Ziel 45 s, „⏱️ zu kurz“/„⏳ zu lang“ verschieben es
    "short": {"min_s": 30.0, "max_s": 75.0, "ziel_s": 45.0, "min_momente": 4, "max_momente": 10,
              "seg_min_s": 2.5, "seg_max_s": 12.0, "serie_max_s": 20.0,
              "b": 1080, "h": 1920},
}
# Dauergrenzen sind Produktregeln; lokale Vorgaben dürfen sie nur weiter einschränken.
DAUER_GRENZEN = {"short": (30.0, 75.0), "zusammenschnitt": (75.0, 120.0)}
# Dauern je Format aus config/lokal.toml, z. B. [regie.formate.short] max_s = 60
FORMAT_SCHLUESSEL = ("min_s", "max_s", "ziel_s", "seg_min_s", "seg_max_s", "serie_max_s")


def format_regeln(konfig, fmt_name: str) -> tuple[dict, list[str]]:
    """FORMATE[fmt_name] mit Vorgaben aus [regie.formate.<format>]: min_s, max_s, seg_min_s, seg_max_s,
    serie_max_s in Sekunden. Unsinniges wird gemeldet und ignoriert; ein unstimmiger Satz (min > max, seg_max oder
    serie_max > max, seg_min > seg_max) fällt ganz auf den Standard zurück. Bis 27.09. waren die Dauern fest im
    Code – die Short-Obergrenze 45 s ließ sich nicht anheben."""
    fmt, hinweise = dict(FORMATE[fmt_name]), []
    vorgaben = (konfig.wert(f"regie.formate.{fmt_name}", {}) if konfig is not None else {}) or {}
    if not isinstance(vorgaben, dict):
        return fmt, [f"regie.formate.{fmt_name} ignoriert (kein Abschnitt)"]
    for name, wert in vorgaben.items():
        if name not in FORMAT_SCHLUESSEL or isinstance(wert, bool) or not isinstance(wert, (int, float)) \
                or not 5.0 <= float(wert) <= 600.0:
            hinweise.append(f"regie.formate.{fmt_name}.{name} ignoriert (unbekannt oder nicht 5 … 600 s)")
            continue
        fmt[name] = float(wert)
    unten, oben = DAUER_GRENZEN[fmt_name]
    for name in ("min_s", "max_s", "ziel_s"):
        if not unten <= fmt[name] <= oben:
            hinweise.append(f"regie.formate.{fmt_name}.{name} außerhalb {unten:.0f}–{oben:.0f} s – Standardwert")
            fmt[name] = FORMATE[fmt_name][name]
    if fmt["min_s"] > fmt["max_s"] or fmt["seg_min_s"] > fmt["seg_max_s"] or fmt["seg_max_s"] > fmt["max_s"] \
            or fmt["serie_max_s"] > fmt["max_s"]:
        hinweise.append(f"regie.formate.{fmt_name} unstimmig (min ≤ max, seg_min ≤ seg_max ≤ max, "
                        f"serie_max ≤ max) – Standardwerte")
        return dict(FORMATE[fmt_name]), hinweise
    # Start-Ziel immer innerhalb min_s … max_s (z. B. nur max_s = 40 gesetzt: Ziel 40 statt 45)
    fmt["ziel_s"] = min(fmt["max_s"], max(fmt["min_s"], float(fmt.get("ziel_s", fmt["max_s"]))))
    return fmt, hinweise


def pruefe_dauer(fmt_name: str, dauer_s: float) -> None:
    """Harte Grenzen für vollständige Videos; historische Listen bleiben weiterhin lesbar."""
    unten, oben = DAUER_GRENZEN[fmt_name]
    if not unten - 1e-6 <= dauer_s <= oben + 1e-6:
        raise RegieFehler(f"{fmt_name}: {dauer_s:.2f} s außerhalb {unten:.0f}–{oben:.0f} s "
                          "– mehr passendes Material wählen oder neu planen")


def dauer_grenzen(fmt: dict) -> tuple[float, float]:
    """Bereich des gelernten dauer_faktor, in dem jede Längen-Stimme noch wirkt: min_s/ziel_s … max_s/ziel_s
    (Short 30/45 … 75/45 = 0,667 … 1,667), höchstens 0,6 … 2,0. 28.09.: vorher fest 0,6 … 1,0 – bei 45 s war
    Schluss, und über 100 „⏱️ zu kurz“ verpufften ohne Wirkung."""
    z = float(fmt.get("ziel_s", fmt["max_s"]))
    return round(max(0.6, fmt["min_s"] / z), 3), round(min(2.0, fmt["max_s"] / z), 3)


def ziel_dauer(fmt: dict, dauer_faktor: float, vorrat: float | None = None, *, ziel_s: float | None = None) -> float:
    """Ziel-Dauer in s: Start-Ziel (Short 45 s; bei wenig Material 80 % davon, nie unter min_s) × gelernter
    dauer_faktor, immer innerhalb min_s … max_s (Short 30–75 s)."""
    if ziel_s is not None:
        return round(min(fmt["max_s"], max(fmt["min_s"], float(ziel_s))), 1)
    basis = float(fmt.get("ziel_s", fmt["max_s"]))
    if vorrat is not None:
        basis = min(basis, max(fmt["min_s"], 0.8 * vorrat))
    return round(min(fmt["max_s"], max(fmt["min_s"], basis * float(dauer_faktor))), 1)


def momente_grenzen(fmt: dict) -> tuple[int, int]:
    """(mindestens, höchstens) Momente je Entwurf – Short 4–10 (28.09.), Zusammenschnitt ohne Grenze."""
    return int(fmt.get("min_momente", 1)), int(fmt.get("max_momente", 10 ** 6))


# Rangfolge der Stimmungen. Seit Stufe 2 (Spec §8.2) nicht mehr Teil der Momentstärke, nur noch Tiebreak bei der
# Musikwahl (gleich lange Anteile: die "stärkere" Stimmung bestimmt die Musik). Rückfrage S2-R2
# (docs/ENTSCHEIDUNGEN.md) ist offen: eventuell kommt sie als Stimmungs-Bonus in `punkte` zurück.
STIMMUNG_WERT = {"episch": 3.0, "spannend": 2.0, "lustig": 1.5, "frustriert": 1.0, "chill": 0.5}
# Übergang in einen Moment hinein, je Stimmung: (xfade-Art, Dauer in s). "schnitt" = harter Schnitt.
UEBERGANG = {"episch": ("schnitt", 0.0), "spannend": ("schnitt", 0.0), "lustig": ("wipeleft", 0.3),
             "frustriert": ("fadeblack", 0.5), "chill": ("fade", 0.8)}

# Gelernte Regie-Parameter: Startwerte. regie_lernen.py verschiebt sie anhand deiner Bewertungen.
PARAMETER = {
    "puffer_vor_s": 2.5,          # vor dem ersten Kill ("abgeschnitten" -> mehr)
    "puffer_nach_s": 1.5,         # nach dem letzten Kill
    "seg_min_faktor": 1.0,        # Mindestlänge je Segment ("zu hektisch" -> länger)
    "beats_pro_schnitt": 1,       # nur auf jedem n-ten Beat schneiden ("zu hektisch" -> 2, 4)
    "dauer_faktor": 1.0,          # × Start-Ziel des Formats ("zu lang" kürzer, "zu kurz" länger; dauer_grenzen)
    "uebergang_faktor": 1.0,      # Länge der Übergänge
    "musik_pegel": 0.35,
    "stimmung_bonus": {},         # Stimmung -> Zusatzpunkte ("Stimmung getroffen" + 👍)
    "track_malus": {},            # Track-ID -> Abzug ("Musik passt nicht")
    "moment_bonus": {},           # Moment -> Zusatzpunkte (👍 +, 👎 ohne Grund −; 🥱 trifft den Schnitt, nicht sie)
    "abwechslung": 0.7,           # Anteil der Punkte, den ein Moment aus dem letzten Entwurf verliert (je älter: halb)
    # 27.09.: Der anteilige Abzug hält die Rotation in der Spitze (18 von 120 Momenten in 20 Shorts). Dazu deshalb
    # Cooldown (Moment aus einem der letzten n Entwürfe: gesperrt, nur Reserve) und Frische-Quote (Anteil der
    # gewählten Momente, die in keinem Entwurf des Fensters waren). Simulation: 18 → 83 verschiedene Momente, die
    # Top-10 kommen weiter regelmäßig (je 5× in 20 Shorts). Beides wirkt nur bei abwechslung > 0.
    "cooldown_entwuerfe": 3,
    "frische_quote": 0.5,
    "max_je_match": 3,
    "luecke_max_s": 4.0,          # längere Pause zwischen zwei Aktionen -> Jump-Cut
    "effekt_staerke": {},         # Stimmung -> Faktor auf alle Effekte, fehlt = 1,0 ("zu viele Effekte" ×0,85,
                                  # "mehr Action" ×1,15)
    "effekt_hektik": 1.0,         # dämpft Beat-Akzente und Glitch-Übergänge ("zu hektisch" ×0,9)
}
ABWECHSLUNG_FENSTER = 12          # so viele letzte Entwürfe zählen für die Abwechslung
# Stufe 4 (08.10., Florian: „autonom … besser und schneller als mit der Hand“): Bleibt ein Short unter 30 s oder mehr
# als MEHR_ANLAUF_AB_S unter dem Ziel, plant der Bot einmal neu – mit mindestens so viel Anlauf und Ausklang aus
# denselben Szenen (innerhalb der gelernten Grenzen 1–6 / 0,5–4 s; die Clips haben 8 s Vorlauf), statt „kein Video“.
MEHR_ANLAUF = {"puffer_vor_s": 4.0, "puffer_nach_s": 3.0}
MEHR_ANLAUF_AB_S = 10.0
# Stufe 4 (08.10., Florian: „Ja, auffüllen“): Reicht der Abend nicht, kommen nie gezeigte starke Szenen früherer
# Abende dazu (Nachschub) – höchstens die Hälfte des Videos, vorn immer eine Szene vom Abend, und nur aus Matches, die
# höchstens [puffer].rohdaten_tage − NACHSCHUB_RESERVE_TAGE alt sind: danach gibt der Puffer ihre Rohvideos frei
# (lager.gib_frei), und beim späteren ✅ fehlte die Datei fürs Upload-Paket (entwurf.upload_fassung).
NACHSCHUB_RESERVE_TAGE = 2
# Abwechslung mit Ermüdung (08.10., Florian: „die Momente dürfen ruhig öfter und gemischter genutzt werden aber nur weil
# ein Clip gut ist muss der nicht immer egal wo verwendet werden … bessere öfters zeigen aber nicht permanent“) – nur im
# einfachen Modus, Werte aus [regie] (ermuedung_regeln), bewusst nicht in ⚙️. Ersetzt dort „jede Szene nur einmal“.
# stunden (Prüfung 08.10.): die Sperre gilt nur für Videos, die höchstens so alt sind (2 Tage) – nach einer Pause
# sperrte sonst das letzte Video seine Szenen für immer. Nachgestellt: 18 h ließ die Szenen von gestern sofort wieder
# zu (größter Anteil einer Szene 25 % statt 18 %), 48 h hält die Abwechslung über Tage.
ERMUEDUNG = {"tage": 30.0, "faktor": 0.5, "sperre": 3, "stunden": 48.0, "anteil": 0.5, "gleich": 2}
# Prüfung 08.10. (Florian: „über 100-mal gesagt, dass die Shorts zu kurz sind“): Erreicht ein Short sein Ziel nicht,
# geht die Länge vor den Feinregeln der Zusammenstellung. So viel unter dem Ziel gilt er noch als lang genug
# (Beat-Raster), und so locker wird die Zusammenstellung im letzten Versuch von 🎬 und neuen Fassungen
# (_mit_lockerung): beliebig viele bekannte neben neuen, aus demselben früheren Video höchstens die Hälfte des neuen
# Videos (mindestens gleich_mit_video) – nie drei von vier Szenen aus einem alten Video.
ZIEL_TOLERANZ_S = 2.0
LOCKER = {"anteil": 1.0, "gleich_haelfte": True}
# … und gibt es so gar keinen Plan („Kein neues Video“), ein letzter Versuch, in dem das älteste der gesperrten Videos
# frei wird – nur deine letzten 2 Videos sperren (Florian: „Wenn alle Clips ausgeschöpft sind … bessere öfters zeigen
# aber nicht permanent“). Nachgestellt: Sperrte im Notfall nur das letzte Video, kam der Quad des Abend-Videos schon
# zwei Videos später wieder.
NOTFALL = {"mix": True, "locker": True, "notfall": True}
# Jump-Cut: so lange bleibt das Bild nach der Aktion vor der Lücke, so früh vor der nächsten geht es weiter.
# Wirksame Lücke mindestens NACH + VOR + 0,5 s – so überlappen sich die Teile nie, auch wenn Gelerntes sie verkleinert.
SPRUNG_NACH_S = 1.5
SPRUNG_VOR_S = 2.0
# Schnittliste version 4 (Regisseur 2.0): Grenzen der fachlichen Prüfung
MAX_EREIGNISSE = 1500
MAX_LUPEN = 20                    # obere Grenze im Prüfer (Standard in [regie.effekte].max_lupen: 8)
MAX_RAFFER = 20                   # obere Grenze im Prüfer (Standard in [regie.effekte].max_raffer: 6)
HOOK_MAX_S = 2.5
HOOK_S = 1.25                     # Hook-Teaser (05.10.): so lang, auf einen Beat zwischen HOOK_MIN_S und HOOK_LANG_S
HOOK_MIN_S, HOOK_LANG_S = 1.0, 1.5
HOOK_VOR_S = 0.8                  # so viel vor dem Anker (Finisher bzw. Tod) beginnt der Teaser
FAIL_VOR_S, FAIL_NACH_S = 6.5, 2.5  # Fail-Moment: Kern um den Tod (Anlauf, Nachlauf)
FAIL_MUSS = (2.0, 0.4)            # … und was davon nie fehlen darf (vor, nach dem Tod)
V4_FELDER = ("rolle", "kill_s", "lupe", "raffer", "effekte", "standbild", "tod_s")


class RegieFehler(RuntimeError):
    pass


def _szenen(n: int, art: str = "starke") -> str:
    """„keine starke Szene“, „nur 1 starke Szene“, „nur 3 starke Szenen“ (07.10.: vorher „nur 0 starke Szenen“)."""
    return f"keine {art} Szene" if n == 0 else f"nur {n} {art} Szene{'n' if n != 1 else ''}"


class ZuWenigSzenen(RegieFehler):
    """Stufe 1 (07.10., Florian: „lieber kein Video“): weniger starke Szenen als ein Short braucht – kein Video mit
    Füllmaterial, sondern eine klare Zeile für dich.
    schon (08.10., einfacher Modus, Abwechslung mit Ermüdung): starke Szenen, die nur fehlen, weil sie in einem deiner
    letzten Videos waren (gesperrt) – stark zählt die übrigen. Mit schon sagt der Satz „Kein neues Video …“.
    mix: auch gemischt aus den letzten Tagen (🎬, neue Fassung) reichte es nicht – stark und schon zählen dann dort.
    zusammen (Prüfung 08.10., nur mit mix): Szenen gäbe es, aber nur stark viele passen zusammen in ein Video – die
    übrigen liefen gerade erst oder schon genauso zusammen."""

    def __init__(self, stark: int, mindestens: int, gesamt: int, schon: int = 0, mix: bool = False,
                 zusammen: bool = False):
        self.stark, self.mindestens, self.gesamt, self.schon, self.mix = stark, mindestens, gesamt, schon, mix
        self.zusammen = zusammen and mix
        self.quelle: str | None = None   # „🎯 nur Match …“, wenn deine Clip-Auswahl (⚙️) das Material eingeengt hat
        # Stufe 4 (08.10.): starke Szenen früherer Abende, die in Frage kamen (None = nicht geprüft)
        self.frueher: int | None = None
        # Mit schon die Zeile für dich auch als Text der Ausnahme – 📋 zeigt sie beim letzten Abend (sitzungen.hinweis)
        super().__init__(self.kopf() if schon or mix else f"{_szenen(stark)}, ein Video braucht {mindestens}")

    def _frueher_satz(self) -> str:
        """Warum auch der Nachschub nicht reichte (Stufe 4) – ehrlich, welche Grenze es war: keine, zu wenige oder
        „höchstens die Hälfte des Videos von früher“ (dann hat der Abend selbst zu wenig). Seit der Abwechslung mit
        Ermüdung (08.10.) kommen auch bekannte starke Szenen in Frage, nur keine aus deinen letzten Videos."""
        if self.frueher is None:
            return ""
        if self.frueher == 0:
            return "Starke Szenen früherer Abende, die nicht gerade erst dran waren, gibt es gerade keine."
        if self.frueher < self.stark:
            szene = "kommt nur 1 starke Szene" if self.frueher == 1 else f"kommen nur {self.frueher} starke Szenen"
            return f"Von früheren Abenden {szene} in Frage."
        return "Von früheren Abenden nehme ich höchstens so viele Szenen dazu, wie der Abend selbst hat."

    def kopf(self) -> str:
        if self.zusammen:   # Prüfung 08.10.: gemischt nur 3 Szenen, die zusammen passen – lieber kein Video
            return (f"aus den letzten Tagen passen nur {self.stark} starke Szene{'n' if self.stark != 1 else ''} "
                    f"zusammen – die übrigen liefen gerade erst oder schon genauso zusammen, ein Video braucht "
                    f"{self.mindestens}")
        if self.schon and not self.stark:   # Prüfung 08.10.: statt „keine starke Szene (4 weitere waren …)“
            alle = "die starke Szene" if self.schon == 1 else f"alle {self.schon} starken Szenen"
            return (f"{alle} {'der letzten Tage' if self.mix else 'dieses Abends'} "
                    f"{'war' if self.schon == 1 else 'waren'} gerade erst in deinen letzten Videos")
        weitere = (f" ({self.schon} weitere {'war' if self.schon == 1 else 'waren'} gerade erst in deinen letzten "
                   "Videos)") if self.schon else ""
        if self.mix:   # 08.10.: auch gemischt aus den letzten Tagen zu wenig
            return f"aus den letzten Tagen gibt es {_szenen(self.stark)}{weitere}, ein Video braucht {self.mindestens}"
        if self.schon:   # 08.10.: die übrigen waren gerade erst in deinen letzten Videos – so schnell kommt keine wieder
            return f"{_szenen(self.stark)}{weitere}, ein Video braucht {self.mindestens}"
        return f"{_szenen(self.stark)} (Multikill, Victory, Clutch oder Endkampf), ein Video braucht {self.mindestens}"

    def _auswahl_tipp(self, experte: bool = True) -> str:
        """Welche Clips angeschaut wurden (07.10.: 🎯 Clips steht im einfachen ⚙️ – vorher „⚙️ → 🔧 → 🎯 Clips“ und
        der Rat, auf „alle Clips“ zu stellen, obwohl der neueste Abend der Standard ist). Einfacher Modus (08.10.):
        nur, was angeschaut wurde – umstellen kannst du dort nichts, das entscheidet der Bot. Gemischt (mix) sagt der
        Kopf schon „aus den letzten Tagen“."""
        if not self.quelle or self.mix:
            return ""
        if not experte:
            return f"Angeschaut habe ich: {self.quelle.removeprefix('🎯 nur ')}."
        return f"Angeschaut habe ich nur: {self.quelle.removeprefix('🎯 nur ')}. Andere Auswahl: ⚙️ → 🎯 Clips."

    def tipp(self, experte: bool = True) -> str:
        """Was helfen würde – nur, wenn es wirklich hilft (sonst leer). Die Wege über ⚙️ nur im Experten-Modus
        (08.10., Florian: „wenn ich alles per Hand einstellen muss …“). Im einfachen Modus dazu, warum auch frühere
        Abende nicht reichten (Stufe 4). Waren die übrigen gerade erst in deinen letzten Videos (schon) oder reichte es
        auch gemischt nicht (mix): kein Tipp zum Umstellen – neue Szenen bringt erst das nächste Spiel."""
        if self.schon or self.mix:
            return " ".join(filter(None, [self._auswahl_tipp(experte),
                                          self._frueher_satz() if self.stark and not self.mix else "",
                                          "Sobald du wieder spielst, kommt ein neues."]))
        if self.gesamt >= self.mindestens:
            return "Mit Einzelkills ginge es: ⚙️ → 🎯 Szenen → „auch Einzelkills“." if experte else self._frueher_satz()
        return " ".join(filter(None, [self._auswahl_tipp(experte), self._frueher_satz()]))

    def satz(self, experte: bool = True) -> str:
        """Die Zeile für dich im Lern-Bot – „Kein neues Video“, wenn die übrigen starken Szenen gerade erst in deinen
        letzten Videos waren oder es auch gemischt nicht reicht (08.10.)."""
        return f"🎬 Kein {'neues ' if self.schon or self.mix else ''}Video: {self.kopf()}. {self.tipp(experte)}".strip()


class KeineNeuenSzenen(ZuWenigSzenen):
    """🥱 (07.10., Florian: „die guten Szenen behalten, der Rest wird durch neue ersetzt“): für die neue Fassung gibt es
    nicht genug neue Szenen – weder im Abend noch starke ungesehene früherer Abende. Lieber kein Video als dasselbe.
    mix (08.10., einfacher Modus, Abwechslung mit Ermüdung): Als Ersatz kamen auch bekannte starke Szenen in Frage, nur
    keine aus deinen letzten Videos und keine aus dem abgelehnten – und auch gemischt aus den letzten Tagen reichte es
    nicht."""

    def __init__(self, ersatz: int, mindestens: int, mix: bool = False):
        super().__init__(ersatz, mindestens, ersatz, mix=mix)
        self.args = (self.kopf(),)

    def kopf(self) -> str:
        return f"{_szenen(self.stark, 'andere' if self.mix else 'neue')} als Ersatz"

    def tipp(self, experte: bool = True) -> str:
        return ""

    def satz(self, experte: bool = True) -> str:
        """Stufe 4 (08.10.): Szenen früherer Abende nur aus den letzten Tagen und höchstens so viele wie vom Abend –
        gab es noch ungesehene, die diese Grenze nicht zuließ (frueher), sagt der Satz das statt „schon gesehen“."""
        if self.mix:
            anfang = (f"Ich habe nur {self.stark} andere Szene{'n' if self.stark != 1 else ''} als Ersatz – für ein "
                      "ganzes Video reicht das nicht") if self.stark else "Andere Szenen als Ersatz habe ich nicht"
            return (f"🎬 Diesmal keine neue Fassung: {anfang}. Die übrigen starken Szenen der letzten Tage liefen gerade "
                    "erst oder schon genauso zusammen. Nach deiner nächsten Runde geht es wieder.")
        if self.stark:
            anfang = (f"Ich habe nur {self.stark} neue Szene{'n' if self.stark != 1 else ''} als Ersatz – für ein ganzes "
                      "Video reicht das nicht")
        else:
            anfang = "Neue Szenen als Ersatz habe ich nicht"
        if self.frueher:
            rest = ("Alles andere von diesem Abend hast du schon gesehen, und von früheren Abenden nehme ich höchstens "
                    "so viele Szenen dazu, wie vom Abend selbst kommen.")
        else:
            rest = "Alles andere von diesem Abend und die starken Szenen der letzten Tage hast du schon gesehen."
        return f"🎬 Diesmal keine neue Fassung: {anfang}. {rest} Nach deiner nächsten Runde geht es wieder."


class ZuKurz(ZuWenigSzenen):
    """Genug Szenen, aber zusammen zu kurz für einen Short (07.10., Florian: „fehlerhafte Texte“ – vorher kam
    „short: 19.60 s außerhalb 30–75 s – mehr passendes Material wählen oder neu planen“): dieselbe klare Zeile wie bei
    zu wenig Szenen. stark = Szenen zur Auswahl, gesamt = alle Szenen vor dem Filter „nur starke“.
    mix (08.10., 🎬 gemischt aus den letzten Tagen): Szenen gäbe es oft genug – nur liefen sie gerade erst oder schon
    genauso zusammen in einem Video (Abwechslung mit Ermüdung); das sagt der Satz statt „die Szenen ergeben nur …“."""

    def __init__(self, sekunden: float, mindest_s: float, stark: int, gesamt: int, nur_starke: bool,
                 mix: bool = False):
        self.sekunden, self.mindest_s, self.nur_starke = sekunden, mindest_s, nur_starke
        super().__init__(stark, stark, gesamt, mix=mix)
        self.args = (self.kopf(),)

    def kopf(self) -> str:
        if self.mix:
            return (f"aus den letzten Tagen kommen nur {self.sekunden:.0f} s zusammen – alles andere lief gerade erst "
                    f"oder schon genauso zusammen, ein Video braucht mindestens {self.mindest_s:.0f} s")
        return (f"die {'starken ' if self.nur_starke else ''}Szenen ergeben nur {self.sekunden:.0f} s, ein Video braucht "
                f"mindestens {self.mindest_s:.0f} s")

    def tipp(self, experte: bool = True) -> str:
        if self.mix:
            return "Sobald du wieder spielst, kommt ein neues."
        if self.nur_starke and self.gesamt > self.stark:
            return "Mit Einzelkills könnte es reichen: ⚙️ → 🎯 Szenen → „auch Einzelkills“." if experte else ""
        return self._auswahl_tipp(experte)


def ist_stark(gruppe: int, victory: bool, clip_mk: dict | None, mk: dict) -> bool:
    """Starke Szene (Stufe 1, 07.10.): Multikill (≥ 2 in Serie), Victory Royale, Clutch oder ein Kill im Endkampf.
    Einzelkills im frühen Spiel und Szenen ohne Kill zählen nicht (Spannung ohne Kill erkennt Stufe 2)."""
    werte = {**(mk or {}), **(clip_mk or {})}

    def zahl(name: str) -> float:
        try:
            return float(werte.get(name) or 0)
        except (TypeError, ValueError):
            return 0.0

    kills = max(gruppe, int(zahl("kills")), len(werte.get("kill_sekunden") or []))
    return gruppe >= 2 or bool(victory) or zahl("clutch") > 0 or (zahl("endgame") > 0 and kills >= 1)


@dataclass
class Kandidat:
    schluessel: str
    datei: str
    dauer_s: float
    stimmung: str
    intensitaet: float
    punkte: float
    clip_id: int | None
    match_id: str | None
    kern: tuple[float, float]      # gewünschter Ausschnitt (Quelle, Sekunden)
    muss: tuple[float, float]      # darf nicht angeschnitten werden (erster Kill − 1 s … letzter Kill + 0,5 s)
    grund: str
    merkmale: dict = field(default_factory=dict)
    abzug: float = 0.0             # schon gezeigt (Abwechslung): Punkte, die in `punkte` schon abgezogen sind
    gezeigt: int = 0               # in wie vielen der letzten Entwürfe
    teile: list[tuple[float, float]] = field(default_factory=list)       # Quelle je Teil (Jump-Cut); leer = [kern]
    teile_muss: list[tuple[float, float]] = field(default_factory=list)  # Muss-Zone je Teil
    serie: bool = False            # ≥ 2 Kills mit Aktions-Zeiten: bleibt ein Stück, bis serie_max_s
    max_gruppe: int = 0            # größte Kill-Serie (wie Bot und Elo zählen) – Kill-Titel
    victory: bool = False          # Victory Royale – Titel VICTORY ROYALE
    gesperrt: bool = False         # Cooldown: in einem der letzten cooldown_entwuerfe Entwürfe – nur Reserve
    stark: bool = False            # Multikill, Victory, Clutch oder Kill im Endkampf (ist_stark, Stufe 1)
    start_utc: str | None = None   # Beginn des Moments (momente.start_utc) – Reihenfolge „chronologisch“
    fail: bool = False             # Fail-Moment (fail.py): Kern um den Tod, nur im Format 🔥 Viral
    ki: dict | None = None         # Einschätzung viral/humor/spannung (viral.py; quelle ki oder regel)
    titel: str | None = None       # Titel im Rand (nur aus Fakten geprüft) – bei Fails statt eines Kill-Titels
    nachschub: bool = False        # starke Szene früherer Abende (Stufe 4, 🥱): höchstens die Hälfte, nie vorn
    # Abwechslung mit Ermüdung (08.10., einfacher Modus): frueher = von einem früheren Abend, obwohl nachschub nicht
    # mehr begrenzt (🎬 gemischt, p["mix"]); wiederholung = schon in einem früheren Video gesehen (gezeigt = Einsätze
    # der letzten Tage, abzug = Ermüdung); videos = in welchen früheren Videos (Familien, szenen.verlauf)
    frueher: bool = False
    wiederholung: bool = False
    videos: frozenset = field(default_factory=frozenset)
    # 🥱-Ersatz im einfachen Modus (Prüfung 08.10.): neue schwache Szene vom Abend (Einzelkill) – erst nach allen
    # bekannten starken (_wahl_rang), „lieber kein Füllmaterial“
    fueller: bool = False

    def __post_init__(self) -> None:
        if not self.teile:  # alte Momente: genau ein Teil = Kern (wie bisher)
            self.teile, self.teile_muss = [self.kern], [self.muss]

    @property
    def von_frueher(self) -> bool:
        """Von einem früheren Abend (Anzeige, Reihenfolge, auswahl.nachschub) – auch gemischt (frueher)."""
        return self.frueher or self.nachschub

    @property
    def kern_laenge(self) -> float:
        """Gewünschte Länge im Video: Summe der Teile (ohne die übersprungenen Lücken)."""
        return sum(b - a for a, b in self.teile)

    @property
    def min_laenge(self) -> float:
        """Kürzeste Länge ohne angeschnittene Action: außen bis zur Muss-Zone, innere Teile ganz."""
        if len(self.teile) == 1:
            return max(0.5, self.muss[1] - self.muss[0])
        innen = sum(b - a for a, b in self.teile[1:-1])
        return (self.teile[0][1] - self.teile_muss[0][0]) + innen + (self.teile_muss[-1][1] - self.teile[-1][0])

    def max_laenge(self, fmt: dict) -> float:
        return fmt["serie_max_s"] if self.serie else fmt["seg_max_s"]


# --- 1. Auswahl ----------------------------------------------------------------------

def _aktionen(mk: dict) -> list[float] | None:
    """Aktions-Zeitpunkte parallel zu kill_sekunden (mein Umhauen, sonst der Kill selbst).
    None bei alten Momenten ohne aktion_sekunden oder wenn die Listen nicht zusammenpassen."""
    kills, aktionen = mk.get("kill_sekunden") or [], mk.get("aktion_sekunden")
    if not kills or not isinstance(aktionen, list) or len(aktionen) != len(kills):
        return None
    # Umhauen kommt nie nach dem Kill – so bleibt der letzte Kill auch das Ende der Action
    return [float(k) if a is None else min(float(a), float(k)) for k, a in zip(kills, aktionen)]


def _kern(mk: dict, dauer: float, p: dict) -> tuple[tuple[float, float], tuple[float, float], str]:
    kills = sorted(mk.get("kill_sekunden") or [])
    if mk.get("fail") and mk.get("tod_sekunde") is not None:  # Fail-Moment (05.10.): Anlauf, Tod, kurzer Nachlauf
        tod = float(mk["tod_sekunde"])
        kern = (tod - float(p.get("fail_vor_s", FAIL_VOR_S)), tod + float(p.get("fail_nach_s", FAIL_NACH_S)))
        muss, grund = (tod - FAIL_MUSS[0], tod + FAIL_MUSS[1]), "Fail"
    elif kills:
        aktionen = _aktionen(mk)
        erste = min(kills[0], *aktionen) if aktionen else kills[0]  # mit Aktionen: ab dem ersten Umhauen
        kern = (erste - p["puffer_vor_s"], kills[-1] + p["puffer_nach_s"])
        muss = (erste - 1.0, kills[-1] + 0.5)
        grund = f"{len(kills)} Kill(s)"
    else:
        ereignisse = [t for t in [mk.get("tod_sekunde"), *(mk.get("jubel_laut_s") or []), *(mk.get("spitzen_s") or [])]
                      if t is not None]
        mitte = ereignisse[0] if ereignisse else dauer / 2
        kern, muss = (mitte - 4.0, mitte + 3.0), (mitte - 1.0, mitte + 1.0)
        grund = "Tod" if mk.get("tod_sekunde") is not None else ("Jubel/Spitze" if ereignisse else "Mitte")
    # Nur den nutzbaren Teil der Datei (am Ende braucht der Schnitt noch Bilder, siehe plane_zeitleiste) –
    # sonst lässt ein Moment mit Action in den letzten Zehnteln jeden compose scheitern.
    nutzbar = max(0.5, dauer - 0.25)
    klemme = lambda a, b: (max(0.0, min(a, nutzbar)), max(0.0, min(b, nutzbar)))  # noqa: E731
    return klemme(*kern), klemme(*muss), grund


def anker(mk: dict, dauer: float) -> list[float]:
    """Jump-Cut-Anker: Aktionen und Kills, sortiert, eindeutig, hinten auf den nutzbaren Teil geklemmt.
    Aktionen vor dem Dateibeginn bleiben negativ: sie grenzen die Lücke davor ab (sprung_teile lässt, was ganz vor
    der Datei liegt, weg), statt als künstlicher Anker bei 0 einen Schnipsel zu erzeugen.
    Auch die Kills, damit kein Erledigen in einer übersprungenen Lücke landet. Leer bei alten Momenten."""
    aktionen = _aktionen(mk)
    if aktionen is None:
        return []
    nutzbar = max(0.5, dauer - 0.25)
    return sorted({round(min(float(t), nutzbar), 3) for t in [*aktionen, *mk["kill_sekunden"]]})


def sprung_teile(anker_: list[float], kern: tuple[float, float], luecke_max_s: float
                 ) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """(Teile, Muss je Teil) in der Quelle. Lücke x -> y > luecke_max_s: Schnitt bei x + 1,5 s, weiter bei y − 2,0 s.
    Der erste Teil beginnt am Kern-Anfang, der letzte endet am Kern-Ende. Gruppen ganz vor dem Dateibeginn
    (Anker < 0) fallen weg – der nächste Teil beginnt dann 2,0 s vor seiner Aktion (nicht vor 0).
    Ohne Anker: genau ein Teil = Kern."""
    punkte = sorted(set(anker_))
    if not punkte:
        return [kern], [kern]
    luecke = max(float(luecke_max_s), SPRUNG_NACH_S + SPRUNG_VOR_S + 0.5)
    gruppen = [[punkte[0]]]
    for x, y in zip(punkte, punkte[1:]):
        if round(y - x, 3) > luecke:  # Anker auf ms: genau luecke_max_s ist noch keine Lücke
            gruppen.append([y])
        else:
            gruppen[-1].append(y)
    # Von einer Gruppe vor der Datei wäre nur der Rest nach ihrer Aktion zu sehen (ein Schnipsel ohne Action)
    erste = next((i for i, g in enumerate(gruppen) if g[-1] >= 0), len(gruppen) - 1)
    teile, muss = [], []
    for i, g in enumerate(gruppen[erste:], erste):
        von = kern[0] if i == 0 else max(0.0, g[0] - SPRUNG_VOR_S)
        bis = kern[1] if i == len(gruppen) - 1 else g[-1] + SPRUNG_NACH_S
        teile.append((round(von, 3), round(bis, 3)))
        muss.append((round(max(von, g[0] - 1.0), 3), round(min(bis, g[-1] + 0.5), 3)))
    return teile, muss


def _teile(mk: dict, dauer: float, kern: tuple[float, float], muss: tuple[float, float], p: dict
           ) -> tuple[tuple[float, float], tuple[float, float], list[tuple[float, float]], list[tuple[float, float]],
                      bool]:
    """(Kern, Muss, Teile, Muss je Teil, Serie?) eines Moments. Alte Momente ohne aktion_sekunden: ein Teil = Kern,
    keine Serie. Bleibt ein Teil übrig, weil alles davor vor dem Dateibeginn lag, sind Kern und Muss dieser Teil."""
    punkte = anker(mk, dauer)
    if not punkte:
        return kern, muss, [kern], [muss], False
    teile, teile_muss = sprung_teile(punkte, kern, float(p.get("luecke_max_s", PARAMETER["luecke_max_s"])))
    if len(teile) == 1:
        if teile[0][0] > kern[0] + 1e-3:  # Aktionen vor der Datei übersprungen: ein Stück ab 2 s vor der Aktion
            kern, muss = teile[0], teile_muss[0]
        else:
            teile_muss = [muss]  # ein Stück: dieselbe Muss-Zone wie ohne Jump-Cut
    return kern, muss, teile, teile_muss, len(mk.get("kill_sekunden") or []) >= 2


def gezeigte_momente(con: sqlite3.Connection, fenster: int = ABWECHSLUNG_FENSTER) -> list[list[str]]:
    """Momente der letzten Entwürfe, neuester zuerst (aus den gespeicherten Schnittlisten)."""
    ergebnis = []
    for z in con.execute("SELECT schnittliste FROM entwuerfe ORDER BY id DESC LIMIT ?", (fenster,)):
        try:
            with open(z["schnittliste"], encoding="utf-8") as f:
                ergebnis.append([s["moment"] for s in json.load(f).get("segmente", [])])
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            ergebnis.append([])  # Datei fehlt: zählt als Entwurf, aber ohne Momente
    return ergebnis


def bewertet_je_moment(con: sqlite3.Connection) -> dict[str, int]:
    """Moment -> in wie vielen bewerteten Entwürfen er schon war (alle Formate, ohne Fenster). Für die Zeile
    „🔁 Schon bewertet“ im Lern-Bot (27.09.: „damit ich ein Gefühl bekomme, was schon doppelt da war“)."""
    zaehler: dict[str, int] = {}
    for z in con.execute("SELECT e.schnittliste FROM entwurf_bewertungen b JOIN entwuerfe e ON e.id = b.entwurf_id"):
        try:
            with open(z["schnittliste"], encoding="utf-8") as f:
                momente = {s["moment"] for s in json.load(f).get("segmente", [])}
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            continue  # Datei fehlt: dieser Entwurf zählt nicht mit
        for m in momente:
            zaehler[m] = zaehler.get(m, 0) + 1
    return zaehler


def zuletzt_gezeigt(frueher: list[list[str]]) -> dict[str, int]:
    """Moment -> Alter seines jüngsten Auftritts (0 = im letzten Entwurf, 1 = im vorletzten …)."""
    alter: dict[str, int] = {}
    for a, momente in enumerate(frueher):
        for m in momente:
            alter.setdefault(m, a)
    return alter


def abwechslung(frueher: list[list[str]], anteil: float) -> dict[str, tuple[float, int]]:
    """Moment -> (Anteil der Punkte, der abgezogen wird; wie oft gezeigt).
    Letzter Entwurf: der volle Anteil, davor je Entwurf die Hälfte; zusammen höchstens 1 (= alle Punkte)."""
    schon: dict[str, tuple[float, int]] = {}
    for alter, momente in enumerate(frueher):
        for m in set(momente):
            wert, n = schon.get(m, (0.0, 0))
            schon[m] = (wert + anteil * 0.5 ** alter, n + 1)
    return {m: (round(min(1.0, w), 3), n) for m, (w, n) in schon.items()}


def _ersatz_datei(konfig: Konfig | None, clip_pfad: str | None, mk: dict) -> Path | None:
    """Der Bot-Clip als Ersatz für eine fehlende Moment-Datei (gleicher Inhalt: momente.datei war beim Anlegen
    material.lokal(clip_pfad), eine Kopie oder der Clip selbst). Nicht nach einem Nachschnitt – dessen
    kill_sekunden zählen ab der neu geschnittenen Datei (stimmung.NACHSCHNITT), nicht ab dem Bot-Clip."""
    if konfig is None or not clip_pfad or isinstance(mk.get("nachschnitt"), dict):
        return None
    ersatz = material.lokal(konfig, clip_pfad)
    return ersatz if ersatz.is_file() else None


def kandidaten(con: sqlite3.Connection, p: dict, frueher: list[list[str]] | None = None, *,
               gewichte: dict[str, float], kill_tabelle: list[float], konfig: Konfig | None = None) -> list[Kandidat]:
    """Alle Momente mit Stimmung als Kandidaten – siehe kandidaten_mit_bericht (nur die Liste)."""
    return kandidaten_mit_bericht(con, p, frueher, gewichte=gewichte, kill_tabelle=kill_tabelle, konfig=konfig)[0]


def kandidaten_mit_bericht(con: sqlite3.Connection, p: dict, frueher: list[list[str]] | None = None, *,
                           gewichte: dict[str, float], kill_tabelle: list[float], konfig: Konfig | None = None,
                           nur_matches: set[str] | None = None, fails: str = "ohne",
                           szenen_idx: dict[str, set[str]] | None = None
                           ) -> tuple[list[Kandidat], dict[str, int]]:
    """Alle Momente mit Stimmung als Kandidaten: Stärke, Punkte für die Auswahl, Kern und Teile für den Schnitt.

    Bericht (zweiter Wert): {"ohne_datei": übersprungen, weil weder Moment-Datei noch Bot-Clip da sind;
    "ersetzt": Moment-Datei fehlte, der Bot-Clip springt ein (nur mit konfig, nie nach Nachschnitt);
    "gesperrt": im Cooldown (in einem der letzten p["cooldown_entwuerfe"] Entwürfe, nur bei abwechslung > 0)};
    mit nur_matches zählt die Bilanz nur diese Matches (Spielabend).
    Bis 27.09. fielen Momente ohne Datei still weg – der Bot zeigte dann „Auswahl aus 20 Momenten“ statt 128.

    Stärke (`intensitaet`, für Bogen, Hook und Kürzen) = der Moment-Score, dieselbe Bewertung wie im Clip-Bot
    (Spec §8.2): round(roh_score(fuer_moment(clips.merkmale, momente.merkmale, kill_tabelle), gewichte), 2).
    Clip-Momente rechnen mit clips.merkmale (Replay-Merkmale, Länge; Bot-Opfer senken die Stärke), Datei-Momente
    (ohne Clip) mit max_gruppe, Victory und den Mic-Werten (Annahme S2-A9). Die Stimmung zählt nicht mehr mit.
    Punkte (Auswahl) = Stärke + Stimmungs-Bonus (gelernt) + 1 (Clip freigegeben/veröffentlicht/im Highlight)
    + (Elo − 1500)/100 + Moment-Bonus (gelernt) − Abwechslungs-Abzug. Status- und Elo-Bonus schrumpfen
    gemeinsam mit dem historischen Anteil des autonomen Modells (ohne Publikumsmodell unverändert).

    Parameter: con – offene Verbindung; p – Regie-Parameter (PARAMETER plus Gelerntes); frueher – Momente der
    letzten Entwürfe, neuester zuerst (gezeigte_momente); gewichte – Merkmal → Gewicht, holt der Aufrufer einmal per
    lernen.aktuelle (Leitplanke 7); kill_tabelle – [vorbewertung].kill_punkte (Index = Kills in der Serie).
    Rückgabe: Kandidaten in der Reihenfolge der momente-id. Verworfene Clips und fehlende Dateien fallen weg.
    Fehler: keine eigenen; fehlt ein Gewicht, zählt das Merkmal 0. Die Stärke kann negativ sein (Bot-Opfer, Länge).
    Beispiel: Clip-Moment, clips.merkmale {"kill_punkte": 3, "bot_opfer": 1}, momente {"spitzen": 2}, Startgewichte
    (kill_punkte 1, bot_opfer −2, spitzen 0,25), Status gesendet, Elo 1500 → intensitaet 1.5, punkte 1.5.
    fails (05.10.): "ohne" (Standard – normale Shorts/Zusammenschnitte sehen keine Fail-Momente), "mit" oder "nur"
    (🔥 Viral). Ein Fail-Moment hat als Stärke seinen Fail-Score (fail.fail_score mit den aktuellen [fail.gewichte]).
    szenen_idx (07.10., szenen.index): Abwechslung und Cooldown je Szene – lief dieselbe Szene unter einem anderen
    Schlüssel (Nvidia/SteelSeries neben dem Clip), gilt sie auch hier als gezeigt.
    """
    from . import fail as fail_modul  # hier: fail importiert über nachschnitt/stimmung viel, regie soll schlank laden

    anteil_ab = float(p.get("abwechslung", 0.0))
    schon = abwechslung(frueher or [], anteil_ab)
    alter = zuletzt_gezeigt(frueher or [])
    cooldown = int(p.get("cooldown_entwuerfe", 0)) if anteil_ab > 0 else 0
    historisch = max(0.0, min(1.0, float((p.get("autonom") or {}).get("historischer_anteil", 1.0))))
    bericht = {"ohne_datei": 0, "ersetzt": 0, "gesperrt": 0}
    try:  # Moment-Sperren; die alten 🥱-Zeilen (bis 07.10.) bleiben in der Tabelle, gelten aber nicht mehr
        dauerhaft = {z[0] for z in con.execute("SELECT schluessel FROM sperren WHERE art = 'moment' "
                                               "AND COALESCE(grund, '') <> 'langweilig'")}
    except sqlite3.OperationalError:
        dauerhaft = set()
    zeilen = con.execute(
        """SELECT m.*, c.status AS clip_status, c.elo AS elo, c.merkmale AS clip_merkmale,
                  c.max_gruppe AS max_gruppe, c.victory_royale AS victory_royale, c.clip_pfad AS clip_pfad,
                  c.freigabe_quelle AS clip_quelle
             FROM momente m LEFT JOIN clips c ON c.id = m.clip_id
            ORDER BY m.id"""
    ).fetchall()
    plaetze = fail_modul.plaetze(con) if fails != "ohne" else {}   # Platz wie Fortnite (07.10.)
    ergebnis = []
    for z in zeilen:
        if hart_verworfen(z["clip_status"], z["clip_quelle"]) or z["schluessel"] in dauerhaft:
            continue  # nur dein 🗑️ (bzw. eine Sperre) schließt aus – automatisch aussortierte bleiben Material
        if nur_matches is not None and z["match_id"] not in nur_matches:
            continue  # Spielabend: fremde Matches zählen auch in der Bilanz nicht mit
        mk = json.loads(z["merkmale"])
        ist_fail = fail_modul.ist_fail(z["schluessel"], mk)
        if (fails == "ohne" and ist_fail) or (fails == "nur" and not ist_fail):
            continue
        if ist_fail:
            mk = fail_modul.mit_platz(mk, z["schluessel"], plaetze, konfig)
        datei = z["datei"]
        if not Path(datei).is_file():
            ersatz = _ersatz_datei(konfig, z["clip_pfad"], mk)
            if ersatz is None:
                bericht["ohne_datei"] += 1
                log.warning("Moment %s: Datei fehlt (%s) – übersprungen", z["schluessel"], datei)
                continue
            log.info("Moment %s: Datei fehlt (%s) – nehme den Bot-Clip %s", z["schluessel"], datei, ersatz)
            datei, bericht["ersetzt"] = str(ersatz), bericht["ersetzt"] + 1
        # Kill-/Aktions-Sekunden zählen ab Dateibeginn (Vertrag mit stimmung.py) – nutzbar ist die Datei bis ende_s.
        # Bisher ist start_s immer 0; ein Nachschnitt mit start_s > 0 darf den Anlauf davor mitnehmen.
        dauer = float(z["ende_s"])
        # max_gruppe/victory nur für die Effekt-Titel (Kill-Titel, VICTORY ROYALE) – wie Bot und Elo zählen
        gruppe = int(z["max_gruppe"] if z["max_gruppe"] is not None else mk.get("max_gruppe", 0) or 0)
        victory = int(z["victory_royale"] or mk.get("victory_royale", 0) or 0)
        # Ohne Clip (LEFT JOIN liefert NULL) ist es ein Datei-Moment: fuer_moment rechnet dann über max_gruppe
        clip_mk = json.loads(z["clip_merkmale"]) if z["clip_merkmale"] is not None else None
        # Die eine Formel (Leitplanke 3); auf 2 Stellen wie bisher – Bogen und Schnittliste zeigen diese Zahl
        intensitaet = round(roh_score(fuer_moment(clip_mk, mk, kill_tabelle), gewichte), 2)
        if ist_fail:  # Fail: Fallhöhe, Erwartungsbruch, Reaktion (fail.py) – Startwert, die KI-Einschätzung kommt dazu
            intensitaet = round(fail_modul.fail_score(mk, konfig)[0], 2)
        punkte = intensitaet + float(p["stimmung_bonus"].get(z["stimmung"], 0.0))
        # freigegeben, veröffentlicht, im Highlight: von dir für gut befunden. Eine Auto-Freigabe folgt aus genau
        # diesem Score – mit Bonus zählte er doppelt (30.09.)
        if z["clip_status"] in BEWERTET and z["clip_quelle"] != "auto":
            punkte += historisch
        if z["elo"] is not None:
            punkte += historisch * (float(z["elo"]) - 1500.0) / 100.0
        punkte += float(p.get("moment_bonus", {}).get(z["schluessel"], 0.0))
        szene = {z["schluessel"], *(szenen_idx or {}).get(z["schluessel"], ())}
        anteil, gezeigt = max(schon.get(s, (0.0, 0)) for s in szene)
        abzug = round(anteil * max(punkte, 1.0), 2)  # auch schwache Momente (< 1 Punkt) verlieren etwas
        kern, muss, grund = _kern(mk, dauer, p)
        kern, muss, teile, teile_muss, serie = _teile(mk, dauer, kern, muss, p)
        if len(teile) > 1:
            grund += f", {len(teile)} Teile (Jump-Cut)"
        gesperrt = cooldown > 0 and min(alter.get(s, cooldown) for s in szene) < cooldown
        bericht["gesperrt"] += int(gesperrt)
        ergebnis.append(Kandidat(z["schluessel"], datei, dauer, z["stimmung"], intensitaet,
                                 round(punkte - abzug, 2), z["clip_id"], z["match_id"], kern, muss, grund, mk,
                                 abzug, gezeigt, teile, teile_muss, serie, max_gruppe=gruppe, victory=bool(victory),
                                 gesperrt=gesperrt, start_utc=z["start_utc"], fail=ist_fail,
                                 titel=mk.get("fail_titel") if ist_fail else None,
                                 stark=not ist_fail and ist_stark(gruppe, bool(victory), clip_mk, mk)))
    return ergebnis, bericht


def _quellmasse(dateien: set[str]) -> list[tuple[int, int]]:
    """(Breite, Höhe) je Quelldatei – für den wirksamen Rahmen-Zoom; nicht lesbare Dateien fehlen (der Renderer
    rechnet ohnehin noch einmal nach)."""
    masse = []
    for datei in sorted(dateien):
        try:
            info = entwurf.probe(Path(datei))
        except (entwurf.MedienFehler, OSError):
            continue
        masse.append((info.breite, info.hoehe))
    return masse


def plan_laenge(k: Kandidat, fmt: dict, seg_min: float) -> float:
    """Ungefähre Länge im Video (vor dem Einrasten auf den Beat). Eine Serie wird nie unter ihre Mindestlänge
    gekürzt – auch wenn sie länger als serie_max_s ist (im Zusammenschnitt kommt sie dann ganz)."""
    laenge = min(k.max_laenge(fmt), max(seg_min, k.kern_laenge))
    return max(laenge, k.min_laenge) if (k.serie or len(k.teile) > 1) else laenge


def serie_zu_lang(k: Kandidat, fmt: dict) -> bool:
    """Serie, die selbst mit Jump-Cuts nicht in serie_max_s passt (im Short: nicht wählen statt zerteilen)."""
    return k.serie and k.min_laenge > fmt["serie_max_s"] + 1e-6


def ueberhang(auswahl: list[Kandidat]) -> int:
    """Stufe 4 (08.10., Florian: „höchstens die Hälfte des Videos“): um wie viele Szenen der Nachschub früherer Abende
    die Szenen vom Abend übersteigt – ≤ 0 heißt in Ordnung. Beispiel: 2 vom Abend, 3 von früher → 1."""
    frueher = sum(1 for k in auswahl if k.nachschub)
    return frueher - (len(auswahl) - frueher)


def nachschub_darf(vorher: list[Kandidat], nachher: list[Kandidat]) -> bool:
    """Darf die Auswahl von vorher zu nachher wechseln (dazu, raus, getauscht)? Danach höchstens die Hälfte von
    früheren Abenden – war es schon mehr (nur über behaltene Szenen einer 🥱-Fassung möglich), wenigstens nicht mehr."""
    return ueberhang(nachher) <= max(0, ueberhang(vorher))


def vorrat_s(kandidaten_: list[Kandidat], fmt: dict, seg_min: float) -> float:
    """Wie lang ein Video aus diesen Kandidaten ungefähr werden kann: Szenen vom Abend ganz, Nachschub früherer Abende
    nur so oft, wie es Szenen vom Abend gibt (die längsten zuerst). Ohne Nachschub die Summe wie bisher."""
    abend = [plan_laenge(k, fmt, seg_min) for k in kandidaten_ if not k.nachschub]
    frueher = sorted((plan_laenge(k, fmt, seg_min) for k in kandidaten_ if k.nachschub), reverse=True)
    return sum(abend) + sum(frueher[:len(abend)])


COOLDOWN_AUFGEHOBEN = "Cooldown aufgehoben"


def ermuedung_regeln(konfig: Konfig | None) -> dict:
    """Die Werte der Abwechslung mit Ermüdung aus [regie] (ermuedung_tage, ermuedung_faktor, sperre_videos,
    wiederholung_anteil, gleich_mit_video) – Unsinniges fällt auf ERMUEDUNG zurück. Landet als p["ermuedung"] in den
    Parametern des Videos (so sieht jede Schnittliste, nach welchen Regeln sie gewählt wurde)."""
    regeln = dict(ERMUEDUNG)
    for name, schluessel, unten, oben in (("tage", "ermuedung_tage", 1.0, 365.0), ("faktor", "ermuedung_faktor", 0.0, 1.0),
                                          ("sperre", "sperre_videos", 0, 12), ("stunden", "sperre_stunden", 1.0, 240.0),
                                          ("anteil", "wiederholung_anteil", 0.0, 1.0),
                                          ("gleich", "gleich_mit_video", 1, 10)):
        wert = konfig.wert(f"regie.{schluessel}", None) if konfig is not None else None
        if isinstance(wert, (int, float)) and not isinstance(wert, bool) and unten <= wert <= oben:
            regeln[name] = type(ERMUEDUNG[name])(wert)
    return regeln


def _wahl_rang(k: Kandidat) -> int:
    """Reihenfolge der Auswahl: 0 neue Szene vom Abend (oder aus dem Video, das eine Fassung ersetzt), 1 neue Szene
    eines früheren Abends, 2 bekannte (einfacher Modus) – unter den bekannten zählen nur die Punkte nach Ermüdung –,
    3 Einzelkill als 🥱-Ersatz im einfachen Modus (Kandidat.fueller, nur wenn sonst nichts reicht)."""
    return 3 if k.fueller else 2 if k.wiederholung else int(k.von_frueher)


def zu_viele_bekannte(auswahl: list[Kandidat], p: dict, min_m: int) -> bool:
    """Regel „gemischt“ (einfacher Modus, p["ermuedung"]): Ist eine neue (oder eigene) Szene dabei, höchstens
    anteil (½) des Videos bekannte – bis zur Mindestzahl (min_m, Short 4) dürfen es mehr sein, sonst verhinderte eine
    einzige neue Szene ein Video, das ganz aus bekannten erlaubt wäre. Ganz ohne neue: keine Grenze (alles ausgeschöpft).
    Beispiel (½, 4): 1 neue + 3 bekannte in Ordnung, 1 + 4 zu viel; 3 + 3 in Ordnung, 3 + 4 zu viel."""
    regeln = p.get("ermuedung")
    if not regeln:
        return False
    bekannt = sum(1 for k in auswahl if k.wiederholung)
    andere = len(auswahl) - bekannt
    return andere > 0 and bekannt > max(float(regeln["anteil"]) * len(auswahl), min_m - andere) + 1e-9


def schon_zusammen(auswahl: list[Kandidat], k: Kandidat, p: dict) -> bool:
    """Regel „nicht dieselbe Zusammenstellung“ (einfacher Modus): Wären mit k mehr als gleich (2) Szenen des Videos
    schon zusammen in einem früheren Video gewesen (Kandidat.videos)? Locker (gleich_haelfte, Prüfung 08.10.): bis zur
    Hälfte des Videos mit k – bei 6 Szenen 3, bei 4 Szenen weiter 2."""
    regeln = p.get("ermuedung")
    if not regeln or not k.videos:
        return False
    andere = [x for x in auswahl if x is not k]
    grenze = int(regeln["gleich"])
    if regeln.get("gleich_haelfte"):
        grenze = max(grenze, (len(andere) + 1) // 2)
    return any(1 + sum(1 for x in andere if v in x.videos) > grenze for v in k.videos)


def bekannte_darf(auswahl: list[Kandidat], k: Kandidat, p: dict, min_m: int) -> bool:
    """Darf k zu auswahl dazu? Eine neue Szene immer; eine bekannte nur, wenn danach nicht zu viele bekannte im Video
    sind und nicht mehr als zwei Szenen, die schon zusammen in einem Video waren."""
    return not k.wiederholung or (not zu_viele_bekannte([*auswahl, k], p, min_m) and not schon_zusammen(auswahl, k, p))


def frei_von_cooldown(kandidaten_: list[Kandidat], fmt: dict, p: dict, ziel: float | None = None
                      ) -> tuple[list[Kandidat], str | None]:
    """Kandidaten ohne die gesperrten (Cooldown) – außer die freien reichen nicht bis zum Ziel (ziel, sonst min_s):
    dann alle, mit Hinweis. Eine Regel für Auswahl und Nachlegen.
    06.10. (Florian: „der Bot macht die schon wieder sackrisch kurz“): vorher reichte min_s (30 s) – bei wenig
    Material oder enger Clip-Auswahl sperrte der Cooldown so viel, dass die Shorts bei 30–35 s statt beim Ziel
    (45–75 s) endeten. Die Abwechslung bleibt trotzdem: gezeigte Momente verlieren weiter Punkte (abwechslung).
    Einfacher Modus (08.10., p["ermuedung"]): die Sperre wird nie aufgehoben – lieber kürzer, Nachschub, mehr Anlauf
    oder kein Video als eine Szene aus deinen letzten Videos."""
    frei = [k for k in kandidaten_ if not k.gesperrt]
    if len(frei) == len(kandidaten_) or p.get("ermuedung"):
        return frei, None
    seg_min = fmt["seg_min_s"] * p["seg_min_faktor"]
    # Stufe 4: Nachschub zählt nur so weit, wie freie Szenen vom Abend ihn tragen (höchstens die Hälfte des Videos)
    if vorrat_s(frei, fmt, seg_min) >= max(fmt["min_s"], float(ziel or 0.0)):
        return frei, None
    return list(kandidaten_), f"{COOLDOWN_AUFGEHOBEN} – nur {len(frei)} frische Momente"


def frische_soll(n: int, p: dict) -> int:
    """Wie viele von n gewählten Momenten frisch sein sollen (gezeigt 0) – 0, wenn Abwechslung aus ist. Im einfachen
    Modus (p["ermuedung"]) gilt statt der Frische-Quote die Regel „gemischt“ (zu_viele_bekannte)."""
    if p.get("ermuedung"):
        return 0
    quote = float(p.get("frische_quote", 0.0)) if float(p.get("abwechslung", 0.0)) > 0 else 0.0
    return math.ceil(quote * n - 1e-9) if quote > 0 and n > 0 else 0


def waehle(kandidaten_: list[Kandidat], fmt: dict, p: dict, pflicht: list[Kandidat] | None = None,
           ersatz_min: int = 0) -> tuple[list[Kandidat], float, list[str]]:
    """Beste Momente, bis die Ziel-Dauer erreicht ist. Ziel richtet sich nach dem Material.

    Cooldown: gesperrte Momente (frei_von_cooldown) bleiben Reserve. Frische-Quote (p["frische_quote"], nur bei
    abwechslung > 0): mindestens dieser Anteil der gewählten Momente war in keinem Entwurf des Fensters (gezeigt 0);
    fehlt etwas, tauscht der schwächste „alte“ gegen den stärksten frischen Moment, der die Match-Grenze einhält.
    pflicht (🔥 Viral, Twist): diese Momente sind immer dabei (auch im Cooldown) und werden nie getauscht.
    ersatz_min (🥱-Fassung): so viele Momente außerhalb der Pflicht kommen auf jeden Fall dazu, soweit vorhanden – auch
    wenn die Pflicht allein schon das Ziel erreicht; wird es dadurch zu lang, geht die schwächste Pflicht-Szene.
    Nachschub früherer Abende (Stufe 4, Kandidat.nachschub): erst nach allen Szenen vom Abend und nie mehr als Szenen
    vom Abend (nachschub_darf) – auch beim Ersatz und beim Frische-Tausch.
    Einfacher Modus (08.10., p["ermuedung"]): bekannte Szenen erst nach allen neuen (_wahl_rang, unter sich nach Punkten
    nach Ermüdung), nie zu viele und nie mehr als zwei, die schon zusammen in einem Video waren (bekannte_darf) – auch
    beim Ersatz. Gesperrte (letzte Videos) sind dort gar keine Kandidaten."""
    hinweise = []
    pflicht = list(pflicht or [])
    seg_min = fmt["seg_min_s"] * p["seg_min_faktor"]
    laenge = lambda k: plan_laenge(k, fmt, seg_min)  # noqa: E731
    # Ziel aus dem ganzen Material; reichen die freien Momente nicht bis dahin, hebt frei_von_cooldown die Sperre auf
    ziel = ziel_dauer(fmt, float(p["dauer_faktor"]), vorrat_s(kandidaten_, fmt, seg_min), ziel_s=p.get("ziel_dauer_s"))
    auswahl, hinweis = frei_von_cooldown(kandidaten_, fmt, p, ziel)
    if hinweis:
        hinweise.append(hinweis)
    vorrat = vorrat_s(auswahl, fmt, seg_min)
    min_m, max_m = momente_grenzen(fmt)
    if vorrat < fmt["min_s"]:
        hinweise.append(f"nur {vorrat:.0f} s Material – kürzer als {fmt['min_s']:.0f} s")
    max_je_match = int(p["max_je_match"])
    gewaehlt, summe, je_match = list(pflicht), sum(laenge(k) for k in pflicht), {}
    for k in pflicht:
        if k.match_id:
            je_match[k.match_id] = je_match.get(k.match_id, 0) + 1
    # Szenen früherer Abende (nachschub: Stufe 4 und 🥱-Fassung) erst, wenn der Abend nicht reicht; bekannte zuletzt
    nach_punkten = sorted((k for k in auswahl if not any(k is x for x in pflicht)),
                          key=lambda k: (_wahl_rang(k), -k.punkte, k.schluessel))

    def darf(k: Kandidat, match_grenze: bool = True) -> bool:
        if match_grenze and k.match_id and je_match.get(k.match_id, 0) >= max_je_match:
            return False
        if k.nachschub and not nachschub_darf(gewaehlt, [*gewaehlt, k]):
            return False   # Stufe 4: höchstens die Hälfte des Videos von früheren Abenden
        return bekannte_darf(gewaehlt, k, p, min_m)   # 08.10.: gemischt, nicht dieselbe Zusammenstellung

    # Immer die beste Szene, die gerade erlaubt ist: Eine übersprungene Szene eines früheren Abends kommt wieder in
    # Frage, sobald eine vom Abend dazukam – bekannte stehen seit 08.10. nach Punkten, nicht nach Abend/früher (ohne
    # bekannte dasselbe Ergebnis wie vorher)
    offen = list(nach_punkten)
    while offen and not ((summe >= ziel and len(gewaehlt) >= min_m) or len(gewaehlt) >= max_m):
        k = next((k for k in offen if darf(k)), None)
        if k is None:
            break
        offen.remove(k)
        gewaehlt.append(k)
        summe += laenge(k)
        if k.match_id:
            je_match[k.match_id] = je_match.get(k.match_id, 0) + 1
    if pflicht and ersatz_min:   # 🥱 (Prüfung 07.10.): sonst „keine neue Fassung“, obwohl Neues da wäre
        fehlt = max(0, ersatz_min - sum(1 for k in gewaehlt if not any(k is x for x in pflicht)))
        while fehlt > 0 and (k := next((k for k in offen if darf(k, match_grenze=False)), None)) is not None:
            offen.remove(k)
            gewaehlt.append(k)
            summe += laenge(k)
            fehlt -= 1
        behalten = [k for k in gewaehlt if any(k is x for x in pflicht)]
        while len(behalten) > 1 and (len(gewaehlt) > max_m or summe > fmt["max_s"]):
            # Stufe 4: eine behaltene Szene vom Abend nur, wenn danach nicht mehr als die Hälfte von früher kommt (und
            # im einfachen Modus nicht zu viele bekannte)
            frei = [b for b in behalten if nachschub_darf(gewaehlt, ohne := [x for x in gewaehlt if x is not b])
                    and not zu_viele_bekannte(ohne, p, min_m)] or behalten
            raus = min(frei, key=lambda k: (k.punkte, k.schluessel))
            behalten.remove(raus)
            gewaehlt.remove(raus)
            summe -= laenge(raus)
    soll = frische_soll(len(gewaehlt), p)
    if soll > 0:
        frische = [k for k in nach_punkten if k.gezeigt == 0 and k not in gewaehlt]
        while sum(1 for k in gewaehlt if k.gezeigt == 0) < soll and frische:
            alte = [k for k in gewaehlt if k.gezeigt > 0 and not any(k is x for x in pflicht)]
            if not alte:
                break
            raus = min(alte, key=lambda k: (k.punkte, k.schluessel))
            ohne = [x for x in gewaehlt if x is not raus]
            rein = next((k for k in frische if (not k.match_id or k.match_id == raus.match_id
                                                or je_match.get(k.match_id, 0) < max_je_match)
                         and (not k.nachschub or nachschub_darf(gewaehlt, [*ohne, k]))), None)
            if rein is None:
                break
            gewaehlt[gewaehlt.index(raus)] = rein
            frische.remove(rein)
            if raus.match_id:
                je_match[raus.match_id] -= 1
            if rein.match_id:
                je_match[rein.match_id] = je_match.get(rein.match_id, 0) + 1
    return gewaehlt, ziel, hinweise


# --- 2. Spannungsbogen -----------------------------------------------------------------

def bogen(gewaehlt: list[Kandidat], fmt_name: str, *, hook_staerkster: bool = False,
          reihenfolge: str = "bogen") -> list[Kandidat]:
    """Reihenfolge der Momente. reihenfolge (Schnittstil, stile.py): "bogen" = Hook, Steigerung, Höhepunkt am Ende;
    "steigend" = vom schwächsten zum stärksten; "chronologisch" = wie gespielt (Match, dann Zeit im Moment), der
    stärkste Moment wandert ans Ende (Höhepunkt, aus ihm kommt auch der Hook vorn)."""
    if reihenfolge in ("steigend", "chronologisch") and len(gewaehlt) > 1:
        staerkster = max(gewaehlt, key=lambda k: (k.intensitaet, k.schluessel))
        rest = [k for k in gewaehlt if k is not staerkster]
        if reihenfolge == "steigend":
            rest.sort(key=lambda k: (k.intensitaet, k.schluessel))
        else:  # wie gespielt: echte Uhrzeit des Moments (momente.start_utc), sonst Match und Zeit im Moment
            rest.sort(key=lambda k: (k.start_utc or "", k.match_id or "", k.kern[0], k.schluessel))
        # hook_staerkster (Stil, Experiment oder Publikum) wirkt auch hier – sonst lernte das Publikums-Modell aus einem
        # Wert, den das Video nicht zeigt: Cold Open, der Höhepunkt kommt zuerst, der Rest in der Reihenfolge des Stils
        return [staerkster, *rest] if hook_staerkster else [*rest, staerkster]
    if len(gewaehlt) <= 2:
        return sorted(gewaehlt, key=lambda k: k.intensitaet, reverse=hook_staerkster)
    nach_staerke = sorted(gewaehlt, key=lambda k: (-k.intensitaet, k.schluessel))
    hoehepunkt, hook, rest = nach_staerke[0], nach_staerke[1], nach_staerke[2:]
    if hook_staerkster:
        hook, hoehepunkt = hoehepunkt, hook
    mitte = sorted(rest, key=lambda k: (k.intensitaet, k.schluessel))
    # Atempause: ruhigster lustiger/chilliger Moment an ca. 60 % der Mitte
    pausen = [k for k in mitte if k.stimmung in ("lustig", "chill")]
    if pausen and len(mitte) >= 3 and fmt_name == "zusammenschnitt":
        pause = pausen[0]
        mitte.remove(pause)
        mitte.insert(int(round(len(mitte) * 0.6)), pause)
    reihe = [hook, *mitte, hoehepunkt]
    # Abwechslung: gleiche Stimmung oder gleiches Match direkt nacheinander -> mit einem späteren tauschen
    for i in range(2, len(reihe) - 1):
        vorher = reihe[i - 1]
        if reihe[i].stimmung == vorher.stimmung or (reihe[i].match_id and reihe[i].match_id == vorher.match_id):
            for j in range(i + 1, len(reihe) - 1):
                if reihe[j].stimmung != vorher.stimmung and reihe[j].match_id != vorher.match_id:
                    reihe[i], reihe[j] = reihe[j], reihe[i]
                    break
    return reihe


def abend_vorn(reihe: list[Kandidat], staerkste: bool = False) -> list[Kandidat]:
    """Stufe 4 (08.10., Florian: „der Anfang kommt immer vom Abend“): Steht vorn eine Szene eines früheren Abends
    (Nachschub), rückt eine Szene vom Abend an den Anfang – die erste in der Reihenfolge des Stils, beim Bogen und
    beim Cold Open die stärkste (dort ist der Einstieg ein Höhepunkt). Der Höhepunkt am Schluss bleibt, wenn es eine
    andere Szene vom Abend gibt. Ohne Nachschub vorn: unverändert. Vorher begann ein dünner Abend mit einer alten
    Triple (planer-knopf/p1, Fall P2)."""
    if not reihe or not reihe[0].nachschub:
        return reihe
    abend = [k for k in reihe[:-1] if not k.nachschub] or [k for k in reihe if not k.nachschub]
    if not abend:
        return reihe
    vorn = max(abend, key=lambda k: (k.intensitaet, k.schluessel)) if staerkste else abend[0]
    return [vorn, *(k for k in reihe if k is not vorn)]


# --- 3. Musik ------------------------------------------------------------------------

def _rang(werte: list[float], wert: float) -> float:
    """Anteil der Werte, die kleiner sind (0..1) – Energie relativ zur eigenen Bibliothek."""
    if len(werte) <= 1:
        return 0.5
    return sum(1 for w in werte if w < wert) / (len(werte) - 1)


def waehle_musik(con: sqlite3.Connection, stimmung: str, gesamt_s: float, p: dict, ziel: dict | None = None,
                 bevorzugt: dict[str, float] | None = None) -> tuple[sqlite3.Row | None, dict[int, float]]:
    """bevorzugt (27.09.): Genre -> Bonus ([musik].genres_bevorzugt / genre_bonus) – Titel aus diesen Genres
    schlagen die alten EDM-Titel, solange Stimmung und Tempo halbwegs passen."""
    ziel = ziel or ZIEL
    tracks = con.execute("SELECT * FROM tracks WHERE beats IS NOT NULL ORDER BY id").fetchall()
    try:  # deine Sperren (🎵 Musik, Stufe 1): dieser Song nie wieder
        gesperrt = {z[0] for z in con.execute("SELECT schluessel FROM sperren WHERE art = 'track'")}
    except sqlite3.OperationalError:
        gesperrt = set()
    tracks = [t for t in tracks if str(t["id"]) not in gesperrt]
    abgelehnt = (p.get("fassung") or {}).get("track_id")   # 🥱 (07.10.): nicht der Song des abgelehnten Videos
    tracks = [t for t in tracks if t["id"] != abgelehnt] or tracks
    rotation = min(int(p.get("musik_rotation", 0) or 0), len(tracks) - 1)
    if rotation > 0:  # Stufe 1: vorher gewann ein passender Song bis zu 13-mal hintereinander (Abzug nur 0,15 je Einsatz)
        zuletzt = {z[0] for z in con.execute(
            "SELECT track_id FROM entwuerfe WHERE track_id IS NOT NULL ORDER BY id DESC LIMIT ?", (rotation,))}
        tracks = [t for t in tracks if t["id"] not in zuletzt] or tracks
    if not tracks:
        return None, {}
    energien = [float(t["energie"] or 0) for t in tracks]
    benutzt = {z["track_id"]: z["n"] for z in con.execute(
        "SELECT track_id, COUNT(*) AS n FROM entwuerfe WHERE track_id IS NOT NULL GROUP BY track_id")}
    wertung = {}
    for t in tracks:
        stimmungen = json.loads(t["stimmungen"] or "[]")
        passt = 1.0 if stimmungen[:1] == [stimmung] else 0.5 if stimmung in stimmungen else 0.0
        energie = 1.0 - abs(_rang(energien, float(t["energie"] or 0)) - ziel[stimmung]["energie"])
        tempo = max(0.0, 1.0 - abs(float(t["bpm"] or 0) - ziel[stimmung]["bpm"]) / 80)
        wert = 2.0 * passt + 1.0 * energie + 0.6 * tempo
        wert -= float(p["track_malus"].get(str(t["id"]), 0.0))
        wert -= 0.15 * benutzt.get(t["id"], 0)              # Abwechslung zwischen Entwürfen
        wert -= 0.3 * (float(t["dauer_s"] or 0) < gesamt_s)  # müsste wiederholt werden
        wert += (bevorzugt or {}).get(t["genre"] if "genre" in t.keys() else None, 0.0)
        wertung[t["id"]] = round(wert, 3)
    beste = max(tracks, key=lambda t: (wertung[t["id"]], -t["id"]))
    return beste, wertung


def drop(verlauf: list[float]) -> float:
    """Sekunde des stärksten Anstiegs im Energie-Verlauf (Mittel der nächsten 2 s minus der letzten 4 s)."""
    besten, zeit = -1.0, 0.0
    for t in range(4, len(verlauf) - 2):
        anstieg = sum(verlauf[t:t + 2]) / 2 - sum(verlauf[t - 4:t]) / 4
        if anstieg > besten:
            besten, zeit = anstieg, float(t)
    return zeit


def naechster(werte: list[float], ziel: float) -> float:
    return min(werte, key=lambda w: abs(w - ziel)) if werte else ziel


# --- 4./5. Zeitleiste auf dem Beat, Übergänge ------------------------------------------------

def _mehrteilig(k: Kandidat, t: float, raster: list[float], fmt: dict, seg_min: float, nutzbar: float
                ) -> list[tuple[float, float, tuple[float, float], float, float, bool]]:
    """Moment mit Jump-Cuts: [(Quelle von, bis, Muss, Zeit von, bis, auf dem Beat)] je Teil.
    Die Schnittpunkte zwischen den Teilen liegen fest (an der Action). Beweglich sind nur der Anfang des ersten
    Teils (mehr oder weniger Anlauf) und das Ende des letzten – dieses rastet auf den Beat ein."""
    teile, tm = k.teile, k.teile_muss
    innen = sum(b - a for a, b in teile[1:-1])
    erst_min, erst_max = teile[0][1] - tm[0][0], teile[0][1]                  # Teil 1: bis zur Muss-Zone / zum Dateianfang
    letzt_min, letzt_max = tm[-1][1] - teile[-1][0], nutzbar - teile[-1][0]   # letzter Teil: Muss-Ende / Dateiende
    min_ges, max_ges = erst_min + innen + letzt_min, erst_max + innen + letzt_max
    wunsch = min(max_ges, k.max_laenge(fmt), max(seg_min, k.kern_laenge, min_ges))
    unten, oben = max(seg_min, min_ges), min(max_ges, max(wunsch, min_ges) + 2.0)
    t = round(t, 3)  # alles auf ms: Quelle und Zeitleiste bleiben exakt gleich lang
    passend = [b for b in raster if unten - 1e-6 <= b - t <= oben + 1e-6]
    ende = round(naechster(passend, t + wunsch) if passend else t + max(min(wunsch, max_ges), min(unten, max_ges)), 3)
    laenge = ende - t
    # wie bei einem Stück: 60 % der Abweichung vorne (lieber etwas mehr Anlauf), der Rest hinten
    erst = (teile[0][1] - teile[0][0]) + 0.6 * (laenge - k.kern_laenge)
    erst = round(min(max(erst, erst_min, laenge - innen - letzt_max), erst_max, laenge - innen - letzt_min), 3)
    stuecke, z = [], t
    for (von, bis), muss in zip([(teile[0][1] - erst, teile[0][1]), *teile[1:-1]], tm[:-1]):
        stuecke.append((von, bis, muss, z, z + (bis - von), False))
        z += bis - von
    stuecke.append((teile[-1][0], teile[-1][0] + (ende - z), tm[-1], z, ende, bool(passend)))
    return stuecke


def hook_anker(k: Kandidat) -> float | None:
    """Wo der Hook-Teaser hinschaut: beim Fail der Tod, sonst die letzte Aktion (Finisher). None = nichts Sichtbares."""
    nutzbar = max(0.5, k.dauer_s - 0.25)
    if k.fail and k.merkmale.get("tod_sekunde") is not None:
        anker = float(k.merkmale["tod_sekunde"])
    else:
        paare = effekte.kills_mit_anker(k.merkmale)
        anker = max((a for _, a in paare if a >= 0), default=None)
    return anker if anker is not None and 0.0 <= anker <= nutzbar else None


def hook_segment(k: Kandidat, raster: list[float]) -> dict | None:
    """Hook-Teaser (05.10., 🔥 Viral): 1–1,5 s aus dem Höhepunkt ganz vorn (rolle „hook“, wie pruefe_liste es kennt) –
    Ende auf einem Beat, wenn einer zwischen HOOK_MIN_S und HOOK_LANG_S liegt. Trägt kill_s (Finisher) bzw. tod_s
    (Fail) als sichtbaren Anker. None, wenn der Moment keinen Anker hat oder zu kurz ist."""
    anker = hook_anker(k)
    if anker is None:
        return None
    nutzbar = max(0.5, k.dauer_s - 0.25)
    passend = [b for b in raster if HOOK_MIN_S - 1e-6 <= b <= HOOK_LANG_S + 1e-6]
    laenge = round(naechster(passend, HOOK_S) if passend else HOOK_S, 3)
    if laenge > nutzbar:
        return None
    von = round(min(max(0.0, anker - HOOK_VOR_S), nutzbar - laenge), 3)
    bis = round(von + laenge, 3)
    segment = {"nr": 1, "moment": k.schluessel, "clip_id": k.clip_id, "match_id": k.match_id, "datei": k.datei,
               "stimmung": k.stimmung, "intensitaet": k.intensitaet, "grund": "Hook-Teaser",
               "stimmen": stimmen_gebraucht(k.merkmale, k.stimmung), "punkte": k.punkte, "abzug": k.abzug,
               "gezeigt": k.gezeigt, "quelle_start_s": von, "quelle_ende_s": bis, "quelle_dauer_s": round(k.dauer_s, 3),
               "muss": [von, bis], "zeit_start": 0.0, "zeit_ende": laenge, "uebergang": {"art": "schnitt", "dauer_s": 0.0},
               "auf_beat": bool(passend), "rolle": "hook"}
    if k.fail:
        segment["tod_s"] = round(anker, 3)
    else:
        segment["kill_s"] = [round(anker, 3)]
    return segment


def plane_zeitleiste(reihe: list[Kandidat], raster: list[float], fmt: dict, p: dict, fps: int,
                     fx: dict | None = None) -> list[dict]:
    """Legt Segmentgrenzen auf Beats. Gibt Segmente mit Quelle (start/ende) und Zeitleiste (zeit_*) zurück.
    Ein Moment mit Jump-Cuts wird zu mehreren aufeinanderfolgenden Segmenten (Feld `teil`, harter Schnitt).
    fx: effekte.einstellungen() – ohne (oder an = false) Übergänge wie bisher (UEBERGANG).
    p["hook_teaser"] (🔥 Viral): vorn ein Teaser aus dem Höhepunkt (hook_segment), der Rest beginnt danach."""
    fx = fx or {"an": False}
    seg_min = fmt["seg_min_s"] * p["seg_min_faktor"]
    segmente, t = [], 0.0
    if p.get("hook_teaser") and len(reihe) >= 2 and (hook := hook_segment(reihe[-1], raster)) is not None:
        segmente, t = [hook], hook["zeit_ende"]
    je_stimmung: dict[str, int] = {}   # Rotation der Übergänge (ohne Mix): frühere Momente derselben Stimmung
    glitches = 0
    # 28.09.: gemischte Übergänge ohne Wiederholung; Seed = Momentfolge, damit derselbe Entwurf gleich gebaut wird
    mix = effekte.Uebergangsmix("|".join(k.schluessel for k in reihe)) if fx["an"] else None
    for k in reihe:
        # Am Dateiende 0,25 s frei lassen: Schnitt/Übergang brauchen dort noch Bilder (entwurf._griffe, UEBERHANG_S)
        nutzbar = max(0.5, k.dauer_s - 0.25)
        if len(k.teile) > 1:
            stuecke = _mehrteilig(k, t, raster, fmt, seg_min, nutzbar)
        else:
            muss_laenge = max(0.5, k.muss[1] - k.muss[0])
            wunsch = min(k.max_laenge(fmt), max(seg_min, k.kern_laenge, muss_laenge))
            wunsch = min(wunsch, nutzbar)
            unten, oben = max(seg_min, muss_laenge), min(nutzbar, max(wunsch, muss_laenge) + 2.0)
            passend = [b for b in raster if unten - 1e-6 <= b - t <= oben + 1e-6]
            ende = naechster(passend, t + wunsch) if passend else t + max(min(wunsch, nutzbar), min(unten, nutzbar))
            laenge = ende - t
            # Quelle: Kern mittig, lieber etwas mehr Anlauf; die Muss-Zone bleibt immer drin
            extra = laenge - k.kern_laenge
            start = k.kern[0] - 0.6 * extra
            start = min(start, k.muss[0])
            start = max(start, k.muss[1] - laenge)
            start = max(0.0, min(start, nutzbar - laenge))
            stuecke = [(start, start + laenge, k.muss, t, ende, bool(passend))]
        for teil, (von, bis, muss, z_von, z_bis, auf_beat) in enumerate(stuecke, 1):
            nr = len(segmente) + 1
            if teil == 1 and nr > 1:
                art, dauer = effekte.uebergang(k.stimmung, je_stimmung.get(k.stimmung, 0), k is reihe[-1], p,
                                               bool(fx["an"]), glitches, profil_=fx.get("profile", {}).get(k.stimmung),
                                               max_glitch=int(fx.get("max_glitch", 1)), mix=mix)
                glitches += art == "glitch"
                uebergang = {"art": art, "dauer_s": dauer}
            else:  # erstes Segment, Jump-Cut innerhalb des Moments
                uebergang = {"art": "schnitt", "dauer_s": 0.0}
            segment = {
                "nr": nr, "moment": k.schluessel, "clip_id": k.clip_id, "match_id": k.match_id, "datei": k.datei,
                "stimmung": k.stimmung, "intensitaet": k.intensitaet, "grund": k.grund,
                # Mikro/Chat im Video nur bei Lachen, Jubel oder Gags (Florian 26.09.) – entwurf._ton liest das
                "stimmen": stimmen_gebraucht(k.merkmale, k.stimmung),
                "punkte": k.punkte, "abzug": k.abzug, "gezeigt": k.gezeigt,
                "quelle_start_s": round(von, 3), "quelle_ende_s": round(bis, 3),
                "quelle_dauer_s": round(k.dauer_s, 3), "muss": [round(muss[0], 3), round(muss[1], 3)],
                "zeit_start": round(z_von, 3), "zeit_ende": round(z_bis, 3),
                "uebergang": uebergang,
                "auf_beat": auf_beat,
            }
            if len(stuecke) > 1:
                segment["teil"] = teil
            segmente.append(segment)
        t = stuecke[-1][4]
        je_stimmung[k.stimmung] = je_stimmung.get(k.stimmung, 0) + 1
    # Übergänge brauchen "Griffe": die halbe Übergangsdauer vor und nach dem Segment aus der Quelle.
    # Gibt die Quelle das nicht her, wird der Übergang kürzer (bis hin zum harten Schnitt).
    for i in range(1, len(segmente)):
        vorher, jetzt_ = segmente[i - 1], segmente[i]
        verfuegbar = min(vorher["quelle_dauer_s"] - vorher["quelle_ende_s"], jetzt_["quelle_start_s"])
        d = min(jetzt_["uebergang"]["dauer_s"], 2 * max(0.0, verfuegbar))
        if d < 2.0 / fps:
            jetzt_["uebergang"] = {"art": "schnitt", "dauer_s": 0.0}
        else:
            jetzt_["uebergang"]["dauer_s"] = round(d, 3)
    return segmente


def pruefe_liste(liste: dict, *, max_lupen: int = MAX_LUPEN, max_raffer: int = MAX_RAFFER) -> list[str]:
    """Schema plus fachliche Regeln: Zeitleiste lückenlos, Quelle im Video, kein Kill angeschnitten, Dauer im Rahmen,
    Teile eines Moments (Jump-Cut) direkt hintereinander, aus derselben Datei, vorwärts, mit hartem Schnitt.
    version 4 (Effekte): Ereignisse im Quellfenster ihres Segments mit ihren Pflichtfeldern, höchstens
    MAX_EREIGNISSE; Tempo-Fenster (lupe) im Segment, Länge mit Zuschlag, Zeitlupe ≤ LUPE_MAX_S bzw. Zeitraffer
    ≤ RAFFER_MAX_S, höchstens max_lupen Zeitlupen und max_raffer Zeitraffer; Hook nur vorn (Stufe 4); höchstens
    effekte.BLITZE_MAX Blitze (flash, strobe, negativ) in jeder Sekunde (Blitz-Sicherheit, 30.09.)."""
    fehler = schema.pruefe(liste, schema.lade("regie"))
    if fehler:
        return fehler
    v4 = liste["version"] >= 4
    if not v4 and "effekte" in liste:
        fehler.append("effekte erst ab version 4")
    t, vorher, ereignisse, lupen, raffer, hooks = 0.0, None, 0, 0, 0, []
    for n, s in enumerate(liste["segmente"]):
        felder = [f for f in V4_FELDER if f in s]
        if not v4 and felder:
            fehler.append(f"Segment {s['nr']}: {', '.join(felder)} erst ab version 4")
        if abs(s["zeit_start"] - t) > 1e-3:
            fehler.append(f"Segment {s['nr']}: Lücke in der Zeitleiste")
        t = s["zeit_ende"]
        if s.get("teil", 1) > 1 and (vorher is None or vorher["moment"] != s["moment"] or vorher["datei"] != s["datei"]
                                     or vorher.get("teil") != s["teil"] - 1 or s["uebergang"]["art"] != "schnitt"
                                     or s["quelle_start_s"] < vorher["quelle_ende_s"] - 1e-3):
            fehler.append(f"Segment {s['nr']}: Teil {s['teil']} passt nicht zum vorigen Teil")
        vorher = s
        if s["quelle_start_s"] < -1e-3 or s["quelle_ende_s"] > s["quelle_dauer_s"] + 1e-3:
            fehler.append(f"Segment {s['nr']}: außerhalb des Videos")
        if s["quelle_start_s"] > s["muss"][0] + 1e-3 or s["quelle_ende_s"] < s["muss"][1] - 1e-3:
            fehler.append(f"Segment {s['nr']}: schneidet die Action an")
        qs, qe, zuschlag = s["quelle_start_s"], s["quelle_ende_s"], 0.0
        for feld, name, laengste in (("lupe", "Zeitlupe", effekte.LUPE_MAX_S), ("raffer", "Zeitraffer", effekte.RAFFER_MAX_S),
                                     ("standbild", "Standbild", effekte.STANDBILD_QUELLE_MAX_S)):
            if w := s.get(feld):
                lupen += feld == "lupe"
                raffer += feld == "raffer"
                if not (qs + 0.1 - 1e-3 <= w["ab_s"] < w["bis_s"] <= qe - 0.1 + 1e-3) or w["bis_s"] - w["ab_s"] > laengste + 1e-3:
                    fehler.append(f"Segment {s['nr']}: {name} außerhalb des Segments oder länger als {laengste} s")
                zuschlag += (w["bis_s"] - w["ab_s"]) * (1 / w["faktor"] - 1)
        if s.get("lupe") and s.get("raffer") and s["raffer"]["bis_s"] > s["lupe"]["ab_s"] + 1e-3:
            fehler.append(f"Segment {s['nr']}: Zeitraffer muss vor der Zeitlupe enden")
        if (halt := s.get("standbild")) and any(w and w["bis_s"] > halt["ab_s"] + 1e-3 for w in (s.get("lupe"), s.get("raffer"))):
            fehler.append(f"Segment {s['nr']}: Zeitlupe/Zeitraffer müssen vor dem Standbild enden")
        if s.get("tod_s") is not None and not qs - 1e-3 <= s["tod_s"] <= qe + 1e-3:
            fehler.append(f"Segment {s['nr']}: Tod bei {s['tod_s']} außerhalb des Segments")
        if abs((s["zeit_ende"] - s["zeit_start"]) - ((qe - qs) + zuschlag)) > 1e-3:
            fehler.append(f"Segment {s['nr']}: Länge Quelle ≠ Zeitleiste")
        for k in s.get("kill_s") or []:
            if not qs - 1e-3 <= k <= qe + 1e-3:
                fehler.append(f"Segment {s['nr']}: Kill bei {k} außerhalb des Segments")
        for e in s.get("effekte") or []:
            if not qs - 1e-3 <= e["t_s"] <= qe + 1e-3:
                fehler.append(f"Segment {s['nr']}: Effekt {e['art']} bei {e['t_s']} außerhalb des Segments")
            pflicht = {"titel": "text", "zaehler": "zahl", "sfx": "klang"}.get(e["art"])
            if pflicht and pflicht not in e:
                fehler.append(f"Segment {s['nr']}: Effekt {e['art']} ohne {pflicht}")
        ereignisse += len(s.get("effekte") or [])
        if s.get("rolle") == "hook":
            hooks.append((n, s))
    if abs(t - liste["dauer_s"]) > 1e-3:
        fehler.append("Gesamtdauer stimmt nicht")
    if ereignisse > MAX_EREIGNISSE:
        fehler.append(f"{ereignisse} Effekt-Ereignisse – höchstens {MAX_EREIGNISSE}")
    if lupen > max_lupen:
        fehler.append(f"{lupen} Zeitlupen – höchstens {max_lupen}")
    if raffer > max_raffer:
        fehler.append(f"{raffer} Zeitraffer – höchstens {max_raffer}")
    # Blitz-Sicherheit (R4): höchstens effekte.BLITZE_MAX Blitze in jeder Sekunde (Strobe anteilig)
    anzahl, ab = effekte.blitze(effekte.zeitleiste(liste))
    if anzahl > effekte.BLITZE_MAX + 1e-6:
        fehler.append(f"{anzahl:g} Blitze in 1 s ab {ab:.2f} s – höchstens {effekte.BLITZE_MAX}")
    for n, s in hooks:
        if n != 0 or len(hooks) > 1:
            fehler.append(f"Segment {s['nr']}: Hook nur als erstes Segment und höchstens einer")
        if s["zeit_ende"] - s["zeit_start"] > HOOK_MAX_S + 1e-3:
            fehler.append(f"Segment {s['nr']}: Hook länger als {HOOK_MAX_S} s")
        anker = s.get("kill_s") or s.get("tod_s") is not None   # Fail-Teaser (05.10.): der Tod ist die Aktion
        if len(liste["segmente"]) < 2 or s["moment"] != liste["segmente"][-1]["moment"] or not anker:
            fehler.append(f"Segment {s['nr']}: Hook ohne Kill/Tod oder nicht aus dem Höhepunkt")
    return fehler


# --- Hauptfunktion -----------------------------------------------------------------------

def nachschub_matches(con: sqlite3.Connection, konfig: Konfig, abend) -> set[str]:
    """Stufe 4 (08.10.): Matches, aus denen Nachschub kommen darf – begonnen vor dem ersten Match des Abends (die Zeile
    „+n Szenen von früheren Abenden“ stimmt) und höchstens [puffer].rohdaten_tage − NACHSCHUB_RESERVE_TAGE (12) Tage
    alt (sonst gibt der Puffer die Rohvideos frei, bevor du ✅ tippst). Leer ohne lesbare Startzeit des Abends.
    Beispiel: Abend ab 07.10. 20:00, heute 08.10. → alle Matches vom 26.09. bis 07.10. 19:59."""
    abend = sorted(abend or [])
    tage = float(konfig.wert("puffer.rohdaten_tage", 14) or 0) - NACHSCHUB_RESERVE_TAGE
    if not abend or tage <= 0:
        return set()

    def zeit(text):
        try:
            return aus_iso(text) if text else None
        except (TypeError, ValueError):
            return None

    platz = ",".join("?" for _ in abend)
    beginn = [t for z in con.execute(f"SELECT start_utc FROM matches WHERE id IN ({platz})", abend)
              if (t := zeit(z["start_utc"])) is not None]
    if not beginn:
        return set()
    grenze = jetzt() - timedelta(days=tage)
    return {z["id"] for z in con.execute(f"SELECT id, start_utc FROM matches WHERE id NOT IN ({platz})", abend)
            if (t := zeit(z["start_utc"])) is not None and grenze <= t < min(beginn)}


def fassung_kandidaten(alle: list[Kandidat], fassung: dict, idx: dict[str, set[str]], gezeigt: set[str],
                       fmt: dict, frueher_ok: set[str] | None = None, *, mix: bool = False,
                       gesperrt: set[str] | None = None) -> tuple[list[Kandidat], list[Kandidat]]:
    """🥱-Fassung (07.10., Florian: „die guten Szenen behalten, der Rest wird durch neue ersetzt“): (Kandidaten, Pflicht).

    Pflicht = die stärkere Hälfte des abgelehnten Videos (fassung["behalten"], je Szene). Die schwächere Hälfte
    (fassung["ohne"]) fehlt nur in dieser Fassung – keine Sperre. Ersatz nie aus gezeigt (je Szene; /experte: alle
    gesehenen, einfacher Modus seit 08.10.: deine letzten Videos und das abgelehnte samt Vorgängern): (a) aus den
    Matches des Abends (fassung["abend"]), neue auch Einzelkills; (b) starke Szenen früherer Abende (Kandidat.nachschub
    – Auswahl und Nachlegen nehmen sie erst nach dem Abend, das Kürzen wirft sie zuerst). Bekannte Szenen
    (Kandidat.wiederholung, nur einfacher Modus) nur, wenn sie stark sind – waehle nimmt sie erst nach allen neuen.
    Datei-Momente ohne Match bleiben wie im Abend-Weg draußen.
    frueher_ok (Stufe 4, nachschub_matches; None = /experte): nur aus diesen Matches darf (b) kommen. Unter /experte
    zählen Szenen früherer Abende nur für die Reihenfolge (Kandidat.frueher, ohne „höchstens die Hälfte“ – wie bis 08.10.).
    Eine behaltene Szene eines früheren Abends (das Video hatte Nachschub) bleibt Pflicht, zählt aber als Nachschub.
    Prüfung 08.10. (einfacher Modus): Eine behaltene Szene, die gerade erst in einem anderen deiner letzten Videos lief
    (gesperrt), ist keine Pflicht – sie fehlt wie die schwächere Hälfte; neue Einzelkills vom Abend kommen erst nach den
    bekannten starken (Kandidat.fueller, „lieber kein Füllmaterial“).
    Fehler: KeineNeuenSzenen (mix: Satz für den gemischten Versuch), wenn es keinen Ersatz gibt oder zusammen weniger
    Szenen als ein Video braucht."""
    behalten = szenen.erweitert(fassung.get("behalten") or [], idx)
    raus = szenen.erweitert(fassung.get("ohne") or [], idx) - behalten
    if gesperrt:   # gerade erst in einem anderen Video – keine Pflicht, fehlt in dieser Fassung
        nicht = behalten & szenen.erweitert(gesperrt, idx)
        behalten, raus = behalten - nicht, raus | nicht
    gezeigt = szenen.erweitert(gezeigt, idx)
    # Eine zweite Aufnahme ohne Match (Nvidia/SteelSeries) im Video zeigt eine Szene aus dem Match ihres Clips
    ohne_match = {k.schluessel for k in alle if not k.match_id}
    im_video = szenen.erweitert([s for s in (*(fassung.get("behalten") or []), *(fassung.get("ohne") or []))
                                 if s in ohne_match], idx)
    abend = set(fassung.get("abend") or []) | {k.match_id for k in alle if k.match_id and k.schluessel in im_video}
    # Ohne Match kommt nur eine behaltene Szene in Frage – schon VOR eine_je_szene: sonst verdrängte eine lautere
    # Nvidia-Aufnahme ihren Clip, fiele danach selbst weg, und die Szene fehlte als Ersatz ganz
    alle = szenen.eine_je_szene([k for k in alle if k.schluessel not in raus and (k.match_id or k.schluessel in behalten)],
                                idx, fassung.get("behalten") or [])
    auswahl, pflicht = [], []
    for k in alle:
        if k.fail:
            continue
        if k.schluessel in behalten:
            k.gesperrt = False            # Pflicht – sonst hebe der Cooldown die Reserve unnötig auf
            k.nachschub = bool(frueher_ok is not None and k.match_id and k.match_id not in abend)
            pflicht.append(k)
        elif k.schluessel in gezeigt or not k.match_id or (getattr(k, "wiederholung", False) and not k.stark):
            continue
        elif k.match_id not in abend:
            if not k.stark or (frueher_ok is not None and k.match_id not in frueher_ok):
                continue
            if frueher_ok is None:
                k.frueher = True       # /experte: erst nach dem Abend, ohne Hälfte-Grenze (wie bis 08.10.)
            else:
                k.nachschub = True
        elif frueher_ok is not None and not k.stark:
            k.fueller = True           # einfacher Modus: ein Einzelkill erst nach den bekannten starken
        auswahl.append(k)
    ersatz, mindestens = len(auswahl) - len(pflicht), momente_grenzen(fmt)[0]
    if not ersatz or len(auswahl) < mindestens:
        raise KeineNeuenSzenen(ersatz, mindestens, mix=mix)
    return auswahl, pflicht


def zu_kurz(gesamt: float, ziel_s: float, unten: float) -> bool:
    """Stufe 4 (08.10.): Ein Short ist zu kurz, wenn er unter `unten` (30 s) oder mehr als MEHR_ANLAUF_AB_S unter dem
    Ziel liegt – dann erst mehr Anlauf (mehr_anlauf), dann Nachschub früherer Abende."""
    return gesamt < unten - 1e-6 or gesamt < ziel_s - MEHR_ANLAUF_AB_S - 1e-6


def mehr_anlauf(p: dict, gesamt: float, ziel_s: float, unten: float) -> dict | None:
    """Stufe 4 (08.10.): Anlauf und Ausklang für den zweiten Plan eines zu kurzen Shorts – je max(gelernt, MEHR_ANLAUF).
    None: Der Plan reicht (mindestens `unten` und höchstens MEHR_ANLAUF_AB_S unter dem Ziel), oder mehr Anlauf ändert
    nichts, weil das Gelernte schon darüber liegt – so plant der zweite Plan nie ein drittes Mal.
    Nach „⏳ höchstens …“ (p["laenge_richtung"] "lang", regeln.anwenden) nur bis 30 s (Prüfung 08.10.: sonst wurde aus
    43 s mit mehr Anlauf 62 s bei „höchstens 55 s“).
    Beispiel: 32 s bei Ziel 45 s, gelernt 2,5 / 1,5 s → {"puffer_vor_s": 4.0, "puffer_nach_s": 3.0}."""
    if not zu_kurz(gesamt, ziel_s, unten) or (p.get("laenge_richtung") == "lang" and gesamt >= unten - 1e-6):
        return None
    alt = {k: float(p.get(k, PARAMETER[k])) for k in MEHR_ANLAUF}
    neu = {k: max(alt[k], wert) for k, wert in MEHR_ANLAUF.items()}
    return neu if neu != alt else None


def ordner(konfig: Konfig) -> Path:
    return Path(str(konfig.wert("regie.ordner", "/var/lib/clip-pipeline/regie")))


def ueber_grenze(liste: dict) -> bool:
    """Nach „⏳ höchstens …“ (parameter.laenge_richtung "lang"): Ist das Video über deiner Grenze (bis auf
    ZIEL_TOLERANZ_S)? Das passiert nur, wenn schon 4 Szenen länger sind (erstelle kürzt sonst darauf)."""
    p = liste.get("parameter") or {}
    return p.get("laenge_richtung") == "lang" and bool(p.get("laenge_grenze_s")) \
        and float(liste["dauer_s"]) > float(p["laenge_grenze_s"]) + ZIEL_TOLERANZ_S + 1e-6


def lang_genug(plan: dict) -> bool:
    """Erreicht ein Plan (erstelle mit _speichern=False) sein Ziel – bis auf ZIEL_TOLERANZ_S (Beat-Raster)? Über
    deiner ⏳-Grenze zählt er nicht (Prüfung 08.10.)."""
    return float(plan["liste"]["dauer_s"]) >= float(plan["ziel_s"]) - ZIEL_TOLERANZ_S - 1e-6 \
        and not ueber_grenze(plan["liste"])


def _mit_lockerung(con: sqlite3.Connection, konfig: Konfig, fmt_name: str, parameter: dict | None,
                   weiter: dict) -> dict:
    """🎬 und neue Fassungen nach ❌ im einfachen Modus (erstelle mit mischen; Prüfung 08.10., Florian: „über 100-mal
    gesagt, dass die Shorts zu kurz sind“ und „die Momente dürfen ruhig öfter und gemischter genutzt werden“): bis zu
    drei Pläne, gespeichert wird genau einer.
      1. vom Abend (wie das Abend-Video: höchstens die Hälfte von früheren Abenden, vorn eine Szene vom Abend),
      2. gemischt aus den letzten Tagen (p["mix"]),
      3. gemischt und locker (p["locker"], LOCKER): beliebig viele bekannte neben neuen, aus demselben früheren
         Video höchstens die Hälfte des neuen Videos (bei 4 Szenen weiter 2).
    Genommen wird der erste Plan, der sein Ziel erreicht (lang_genug) – Plan 1 bei 🎬 nur mit mindestens einer neuen
    Szene: Hat der Abend nichts Neues mehr, holte er sonst seine bekannten Szenen in jedes vierte Video, nur weil sie vom
    Abend sind. Erreicht keiner das Ziel, der längste (gleich lang: der frühere; bei 🎬 ohne Neues kommt Plan 1 zuletzt).
    Nach „⏳ höchstens …“ zählt nur ein Plan unter deiner Grenze (ueber_grenze), sind alle darüber, der kürzeste.
    Ein Plan unter 4 Szenen zählt nie (p["leiter"]). Vorher kam der gemischte Plan nur bei „kein Video“ – ein Abend-Plan
    bis 10 s unter dem Ziel blieb, auch wenn es gemischt gereicht hätte (Prüfung: 43 s statt 62 s bei „mindestens
    65 s“). Was immer gilt: neue Szenen zuerst, bekannte nach Ermüdung, keine aus deinen letzten 2 Videos.
    Gibt es so gar keinen Plan, ein Notfall-Versuch (NOTFALL), in dem das älteste der 3 gesperrten Videos frei wird
    (Florian: „Wenn alle Clips ausgeschöpft sind … bessere öfters zeigen aber nicht permanent“) – für die Länge allein
    wird die Sperre nie gelockert. Scheitert auch der, kommt der Fehler des dritten Versuchs („Kein neues Video …“,
    „Diesmal keine neue Fassung …“)."""
    basis = {**(parameter or {}), "leiter": True}
    fassung = isinstance(basis.get("fassung"), dict) or bool(basis.get("ersetzt"))
    plaene: list[tuple[int, dict]] = []
    fehler_: list[RegieFehler] = []

    def versuch(stufe: int, extra: dict) -> dict | None:
        try:
            return erstelle(con, konfig, fmt_name, parameter={**basis, **extra}, **weiter, _speichern=False)
        except ZuWenigSzenen as fehler:
            log.info("Versuch %d: kein Video (%s)", stufe + 1, fehler)
            fehler_.append(fehler)
        except RegieFehler as fehler:
            if stufe == 0:
                raise   # wie bisher: nur „zu wenige Szenen“ führt zum gemischten Versuch
            log.warning("Versuch %d: %s", stufe + 1, fehler)
            fehler_.append(fehler)
        return None

    for stufe, extra in enumerate(({}, {"mix": True}, {"mix": True, "locker": True})):
        if (plan := versuch(stufe, extra)) is None:
            continue
        neu = int(plan["liste"]["auswahl"].get("neu") or 0)
        if lang_genug(plan) and (stufe > 0 or fassung or neu > 0):
            return _speichere(con, konfig, plan["liste"])
        log.info("Versuch %d: %.1f s bei Ziel %.0f s (%d neue Szenen) – %s", stufe + 1, plan["liste"]["dauer_s"],
                 plan["ziel_s"], neu, "versuche weiter" if stufe < 2 else "nehme den längsten")
        plaene.append((stufe, plan))
    if not plaene:
        letzter = next((f for f in reversed(fehler_) if isinstance(f, ZuWenigSzenen)), fehler_[-1])
        if (plan := versuch(3, NOTFALL)) is None:
            raise letzter
        log.info("Notfall: %.1f s, nur deine letzten 2 Videos gesperrt", plan["liste"]["dauer_s"])
        return _speichere(con, konfig, plan["liste"])
    if not fassung and plaene[0][0] == 0 and not int(plaene[0][1]["liste"]["auswahl"].get("neu") or 0):
        plaene = [*plaene[1:], plaene[0]]   # 🎬 ohne Neues vom Abend: gleich lang heißt gemischt
    if not (im_rahmen := [x for x in plaene if not ueber_grenze(x[1]["liste"])]):
        # nach ⏳ alle über deiner Grenze (schon 4 Szenen sind länger): der kürzeste
        return _speichere(con, konfig, min(plaene, key=lambda x: float(x[1]["liste"]["dauer_s"]))[1]["liste"])
    plaene = im_rahmen
    beste = plaene[0][1]
    for _stufe, plan in plaene[1:]:
        if float(plan["liste"]["dauer_s"]) > float(beste["liste"]["dauer_s"]) + 0.5:
            beste = plan
    return _speichere(con, konfig, beste["liste"])


def _speichere(con: sqlite3.Connection, konfig: Konfig, liste: dict) -> dict:
    """Schnittliste schreiben und den Entwurf anlegen (Ende von erstelle; _mit_lockerung speichert so den gewählten
    Plan). Rückgabe wie erstelle."""
    name, fmt_name, musik = liste["name"], liste["format"], liste.get("musik")
    ziel_datei = ordner(konfig) / f"{name}.json"
    ziel_datei.parent.mkdir(parents=True, exist_ok=True)
    tmp = ziel_datei.with_suffix(".tmp")
    tmp.write_text(json.dumps(liste, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(ziel_datei)
    cur = con.execute(
        """INSERT INTO entwuerfe (name, format, schnittliste, parameter, track_id, dauer_s, erstellt, variante)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (name, fmt_name, str(ziel_datei), json.dumps(liste["parameter"], ensure_ascii=False),
         musik["track_id"] if musik else None, liste["dauer_s"], iso(jetzt()), liste.get("variante")),
    )
    momente = [s for s in liste["segmente"] if s.get("teil", 1) == 1 and s.get("rolle") != "hook"]
    return {"entwurf": cur.lastrowid, "name": name, "format": fmt_name, "datei": str(ziel_datei),
            "dauer_s": round(liste["dauer_s"], 1), "segmente": len(liste["segmente"]), "momente": len(momente),
            "stimmung": liste["stimmung"], "musik": musik["titel"] if musik else None, "neu": liste["auswahl"]["neu"],
            "hinweise": liste["hinweise"], "variante": liste.get("variante")}


def erstelle(con: sqlite3.Connection, konfig: Konfig, fmt_name: str, *, parameter: dict | None = None,
             name: str | None = None, ziel: dict | None = None, nur_matches: set[str] | None = None,
             hinweise_vorab: list[str] | None = None, gelernt: dict | None = None,
             variante: str | None = None, nachschub: bool | None = None, mischen: bool = False,
             _speichern: bool = True) -> dict:
    """nur_matches: nur Momente aus diesen Matches (z. B. ein Spielabend).
    hinweise_vorab: Hinweise des Aufrufers (z. B. Lern-Bot: Stimmung nachziehen fehlgeschlagen) – kommen vorn in
    die Schnittliste, damit der Bot sie zeigt (er zeigt die ersten drei).
    variante (🔥 Viral, 05.10.): twist · highlight · fail (viral.VARIANTEN) – nur mit fmt_name "short". Mischung,
    KI-Bonus und Reihenfolge aus viral.py; Fail-Momente nur hier.
    Nachschub (Stufe 4, 08.10., Florian: „Ja, auffüllen“) – nur im einfachen Modus, für einen Short aus einem Abend
    (nur_matches) mit „nur starke Szenen“: Hat der Abend weniger neue starke Szenen, als ein Video braucht (4), oder
    erreicht das Video sein Ziel nicht (Prüfung 08.10.: vorher erst 10 s darunter, zu_kurz), kommen starke Szenen
    früherer Abende dazu (nachschub_matches: höchstens 12 Tage alt) – nach allen Szenen vom Abend, nie mehr als vom
    Abend, nie vorn. Gilt fürs Abend-Video, 🎬 und die neue Fassung nach ❌; /experte und eine Match-Wahl dort bleiben
    exakt.
    nachschub: None = selbst entscheiden; True/False legt es fest (der zweite Plan erbt die Entscheidung).
    Abwechslung mit Ermüdung (08.10., Florian: „die Momente dürfen ruhig öfter und gemischter genutzt werden aber nur
    weil ein Clip gut ist muss der nicht immer egal wo verwendet werden … bessere öfters zeigen aber nicht permanent“;
    ersetzt „jede Szene nur in einem Video“) – einfacher Modus, Short ohne 🔥 Viral (szenen.verlauf, p["ermuedung"]):
    eine Szene, die gerade erst (höchstens 48 h) in einem deiner letzten 3 Videos lief, ist kein Kandidat (nur
    wenn es sonst gar kein Video gäbe, sperren bei 🎬 bloß deine letzten 2 Videos); neue Szenen gehen vor; bekannte starke
    verlieren je Einsatz der letzten 30 Tage die Hälfte ihrer Punkte (Abzug), höchstens die Hälfte des Videos
    bekannte (bis zur Mindestzahl dürfen es mehr sein) und nie mehr als zwei, die schon zusammen in einem Video waren.
    Sie ersetzt dort Abzug, Cooldown und Frische-Quote des Experten-Modus (frueher leer). Reicht es nicht: Nachschub,
    mehr Anlauf, sonst ZuWenigSzenen (schon: gesperrte starke Szenen).
    p["ersetzt"] (neue Fassung nach ❌, lernbot.baue_entwurf): die Szenen dieser Videos sind für sie frei und keine
    Wiederholung; bei 🥱 kommt der Ersatz nie aus ihnen (erst neue, dann bekannte starke).
    mischen (🎬 und neue Fassungen, lernbot.baue_entwurf; einfacher Modus): _mit_lockerung – erst vom Abend, erreicht das
    nicht sein Ziel, gemischt aus den letzten Tagen (p["mix"]: ohne „höchstens die Hälfte von früheren Abenden“ und
    ohne „vorn eine Szene vom Abend“), zuletzt gemischt und locker (p["locker"]). Das Abend-Video (sitzung) mischt nie.
    2-Wochen-Video und /experte wie bisher.
    _speichern=False (nur _mit_lockerung): der fertige Plan {"liste", "ziel_s"} ohne Datei und ohne Entwurf."""
    if mischen and not (parameter or {}).get("mix"):
        weiter = dict(name=name, ziel=ziel, nur_matches=nur_matches, hinweise_vorab=hinweise_vorab, gelernt=gelernt,
                      variante=variante, nachschub=nachschub)
        if fmt_name == "short" and variante is None and konfig.wert("regie.geschmack", False):
            return _mit_lockerung(con, konfig, fmt_name, parameter, weiter)
        return erstelle(con, konfig, fmt_name, parameter=parameter, **weiter, _speichern=_speichern)
    if fmt_name not in FORMATE:
        raise RegieFehler(f"Unbekanntes Format {fmt_name!r}")
    viral = None
    if variante is not None:
        from . import viral

        if variante not in viral.VARIANTEN or fmt_name != "short":
            raise RegieFehler(f"Unbekannte Viral-Variante {variante!r} (nur als Short)")
    fmt, hinweise = format_regeln(konfig, fmt_name)
    if viral is not None:
        fmt = viral.format_fuer(fmt, variante)
    hinweise = [*(hinweise_vorab or []), *hinweise]
    p = {**PARAMETER, **(parameter or {})}
    fps = int(konfig.wert("regie.fps", 60 if fmt_name == "zusammenschnitt" else 30))
    fx, fx_hinweise = effekte.einstellungen(konfig)
    # Stufe 4 (08.10.): einfacher Modus wie bei mehr_anlauf (regie.geschmack aus einstellungen.EINFACH_FEST)
    einfach = fmt_name == "short" and viral is None and bool(konfig.wert("regie.geschmack", False))
    mix = einfach and bool(p.get("mix"))   # zweiter Versuch von 🎬 / neuer Fassung: gemischt aus den letzten Tagen
    # Abwechslung mit Ermüdung (08.10.): im einfachen Modus statt Abzug, Cooldown und Frische-Quote (szenen.verlauf) –
    # der Cooldown holte gesperrte Szenen sonst zurück, sobald Frisches fehlte („Cooldown aufgehoben“)
    frueher = [] if einfach else gezeigte_momente(con)
    # Gewichte einmal holen und durchreichen (Leitplanke 7); die Kill-Tabelle ist dieselbe wie im Clip-Bot
    _version, gewichte = lernen.aktuelle(con, konfig)
    if "historischer_anteil" in (p.get("autonom") or {}):
        anteil = max(0.0, min(1.0, float(p["autonom"]["historischer_anteil"])))
        basis = lernen.startgewichte(konfig)
        gewichte = {k: basis.get(k, 0.0) + (w - basis.get(k, 0.0)) * anteil for k, w in gewichte.items()}
    for merkmal, delta in (p.get("publikum_gewichte") or {}).items():
        if merkmal in gewichte and isinstance(delta, (int, float)):
            gewichte[merkmal] += max(-1.0, min(1.0, float(delta)))
    kill_tabelle = [float(x) for x in konfig.wert("vorbewertung.kill_punkte")]
    # 🥱 (07.10.): neue Fassung des abgelehnten Videos – Szenen aus dem Abend UND früheren Abenden (fassung_kandidaten)
    fassung = p.get("fassung") if viral is None and isinstance(p.get("fassung"), dict) else None
    idx = szenen.index(con)   # dieselbe Spielszene unter mehreren Schlüsseln (Clip, Nvidia, SteelSeries)
    verlauf: szenen.Verlauf | None = None   # einfacher Modus: was du schon gesehen hast, je Szene und je Video
    if einfach:
        p["ermuedung"] = ermuedung_regeln(konfig)   # waehle, Nachlegen und Kürzen lesen die Regeln von hier
        if p.get("locker"):   # letzter Versuch von 🎬/neuer Fassung (_mit_lockerung): die Länge geht vor
            p["ermuedung"] = {**p["ermuedung"], **LOCKER}
        if p.get("notfall"):   # Notfall (_mit_lockerung): sonst gar kein Video – das älteste gesperrte Video wird frei
            p["ermuedung"]["sperre"] = max(1, int(p["ermuedung"]["sperre"]) - 1)
        ersetzt = [i for i in (p.get("ersetzt") or []) if isinstance(i, int)]   # lernbot: Fassung nach ❌
        verlauf = szenen.verlauf(con, idx, ersetzt=ersetzt, tage=p["ermuedung"]["tage"],
                                 sperre=p["ermuedung"]["sperre"], stunden=p["ermuedung"]["stunden"])

    def ermuede(kandidaten_: list[Kandidat]) -> None:
        """Einfacher Modus: Eine bekannte Szene (nicht aus dem Video, das eine Fassung ersetzt) verliert je Einsatz der
        letzten Tage den Faktor ihrer Punkte (½: 10 → 5 → 2,5) – so kommen bessere öfter wieder als schwache, aber
        keine dauernd. Schwache (< 1 Punkt) verlieren etwas wie beim Abzug des Experten-Modus. In welchen früheren
        Videos eine Szene war (videos, für „nie dieselbe Zusammenstellung“), steht an jeder – Prüfung 08.10.: auch an
        den Szenen des ersetzten Videos, sonst nahm eine Fassung eine dritte Szene aus demselben alten Video dazu."""
        if verlauf is None:
            return
        faktor = float(p["ermuedung"]["faktor"])
        for k in kandidaten_:
            k.videos = verlauf.videos.get(k.schluessel, frozenset())
            if verlauf.wiederholung(k.schluessel):
                n = verlauf.einsaetze.get(k.schluessel, 0)
                k.wiederholung, k.gezeigt = True, n
                k.abzug = round((1.0 - faktor ** n) * max(k.punkte, 1.0), 2)
                k.punkte = round(k.punkte - k.abzug, 2)

    alle, bericht = kandidaten_mit_bericht(con, p, frueher, gewichte=gewichte, kill_tabelle=kill_tabelle, konfig=konfig,
                                           nur_matches=None if fassung else nur_matches,
                                           fails=viral.FAILS[variante] if viral else "ohne", szenen_idx=idx)
    ermuede(alle)
    if bericht["ohne_datei"]:
        hinweise.append(f"{bericht['ohne_datei']} Momente ohne Datei übersprungen"
                        + (f" ({bericht['ersetzt']} weitere: Bot-Clip statt Moment-Datei)" if bericht["ersetzt"] else ""))
    pflicht: list[Kandidat] = []
    nachschub_moeglich = einfach and fassung is None and bool(nur_matches) and bool(p.get("nur_starke"))
    pool: list[Kandidat] = []   # starke Szenen früherer Abende (Kandidat.nachschub): neue, dann bekannte
    schon = 0                   # starke Szenen, die nur fehlen, weil sie in deinen letzten Videos waren
    if fassung is not None:
        frueher_ok = nachschub_matches(con, konfig, fassung.get("abend") or []) if einfach else None
        # 🥱: Ersatz nie aus deinen letzten Videos und nie aus dem abgelehnten (samt Vorgänger-Fassungen) – erst neue,
        # dann bekannte starke. /experte: nur nie gesehene wie bisher
        tabu = verlauf.gesperrt | verlauf.eigen if verlauf is not None else szenen.jemals_gezeigt(con)
        alle, pflicht = fassung_kandidaten(alle, fassung, idx, tabu, fmt, frueher_ok, mix=mix,
                                           gesperrt=verlauf.gesperrt if verlauf is not None else None)
        p["max_je_match"] = max(int(p["max_je_match"]), momente_grenzen(fmt)[1])   # oft nur ein Match am Abend
    else:
        if verlauf is not None:   # 08.10.: nichts aus deinen letzten Videos – die Sperre wird nie aufgehoben
            schon = len(szenen.eine_je_szene([k for k in alle if k.stark and k.schluessel in verlauf.gesperrt], idx))
            alle = [k for k in alle if k.schluessel not in verlauf.gesperrt]
        alle = szenen.eine_je_szene(alle, idx)   # nie dieselbe Szene zweimal (vor dem Zählen der starken)
        if nachschub_moeglich:
            if erlaubt := nachschub_matches(con, konfig, nur_matches):
                frueher_alle, _ = kandidaten_mit_bericht(con, p, frueher, gewichte=gewichte, kill_tabelle=kill_tabelle,
                                                         konfig=konfig, nur_matches=erlaubt, szenen_idx=idx)
                ermuede(frueher_alle)
                # nie Einzelkills, nie Fails, nur mit Match – wie im 🥱-Weg; nie aus deinen letzten Videos (je Szene)
                frueher_alle = [k for k in szenen.eine_je_szene(frueher_alle, idx)
                                if k.stark and not k.fail and k.match_id]
                pool = [k for k in frueher_alle if k.schluessel not in verlauf.gesperrt]
                if mix:   # gemischt zählen auch die gesperrten der letzten Tage für „… waren gerade erst dran“
                    schon += len(frueher_alle) - len(pool)
                for k in pool:
                    k.nachschub = True
            if nachschub is None:   # Reichen die neuen starken Szenen des Abends nicht für ein Video?
                neue = sum(1 for k in alle if k.stark and not k.wiederholung)   # gesperrte sind oben schon raus
                nachschub = bool(pool) and (mix or neue < momente_grenzen(fmt)[0])
            if nachschub and pool:
                alle = [*alle, *pool]
                # erst alle Szenen vom Abend – auch wenn sie aus einem einzigen Match kommen (wie im 🥱-Weg)
                p["max_je_match"] = max(int(p["max_je_match"]), momente_grenzen(fmt)[1])
    if mix:   # 08.10.: gemischt – von früheren Abenden ohne „höchstens die Hälfte“ und ohne „vorn vom Abend“
        for k in alle:
            k.frueher, k.nachschub = k.von_frueher, False
    szenen_gesamt = sum(1 for k in alle if not k.nachschub)   # für ZuKurz: ginge es mit Einzelkills?
    if p.get("nur_starke") and viral is None and fassung is None:   # Stufe 1: lieber kein Video als Füllmaterial
        stark = [k for k in alle if k.stark]
        vom_abend = sum(1 for k in stark if not k.nachschub)
        # Stufe 4: Nachschub höchstens so viele wie Szenen vom Abend (höchstens die Hälfte des Videos)
        if vom_abend + min(len(stark) - vom_abend, vom_abend) < momente_grenzen(fmt)[0]:
            fehler = ZuWenigSzenen(vom_abend, momente_grenzen(fmt)[0], szenen_gesamt, schon, mix=mix)
            fehler.frueher = len(pool) if nachschub_moeglich else None
            raise fehler
        alle = stark
    if not alle:
        raise RegieFehler(("Keine Fail-Momente – erst `pipeline fail --nachziehen`" if variante == "fail" else
                           "Keine Momente mit Stimmung" + (" in diesen Matches" if nur_matches else "")
                           + " – erst `pipeline stimmung`"))
    if viral is not None:  # Einschätzung (KI oder Regel) an jeden Kandidaten, Twist-Momente als Pflicht
        alle, pflicht, viral_hinweise = viral.mischen(con, konfig, alle, variante, p, fmt)
        hinweise += viral_hinweise
    # Short: eine Serie, die selbst mit Jump-Cuts nicht in serie_max_s passt, wird nicht gewählt statt zerteilt
    # (im Zusammenschnitt kommt sie ganz)
    zu_lang = [k for k in alle if fmt_name == "short" and serie_zu_lang(k, fmt)]
    if zu_lang:
        alle = [k for k in alle if not any(k is z for z in zu_lang)]
        pflicht = [k for k in pflicht if not any(k is z for z in zu_lang)]
        if not alle:
            raise RegieFehler(f"Alle {len(zu_lang)} Momente sind Serien, die für einen Short zu lang sind "
                              f"(> {fmt['serie_max_s']:.0f} s am Stück) – Zusammenschnitt nehmen")
    # Pflicht-Momente nur, wenn es welche gibt (alte Aufrufer und Tests ersetzen waehle mit drei Argumenten)
    if fassung is not None:   # 🥱: mindestens so viele neue Szenen, wie schwächere getauscht werden
        gewaehlt, ziel_s, wahl_hinweise = waehle(alle, fmt, p, pflicht,
                                                 ersatz_min=max(1, len(fassung.get("ohne") or [])))
    else:
        gewaehlt, ziel_s, wahl_hinweise = waehle(alle, fmt, p, pflicht) if pflicht else waehle(alle, fmt, p)
    hinweise += wahl_hinweise
    if zu_lang:
        hinweise.append(f"{len(zu_lang)} Serie(n) zu lang für Short (> {fmt['serie_max_s']:.0f} s am Stück)")
    # Nachlegen nimmt aus demselben Vorrat wie die Auswahl: ohne die Momente im Cooldown, außer der reichte nicht
    vorrat, _ = frei_von_cooldown(alle, fmt, p, ziel_s)

    # Stufe 4: vorn immer eine Szene vom Abend – beim Bogen und beim Cold Open die stärkste (vorn ist dort ein Hook)
    vorn_staerkste = str(p.get("reihenfolge", "bogen")) == "bogen" or bool(p.get("hook_staerkster", False))

    def ordne(auswahl: list[Kandidat]) -> list[Kandidat]:
        """Reihenfolge: Bogen des Stils, bei 🔥 Viral über viral.ordne (Twist-Stellen, Fail steigend). Nachschub
        früherer Abende steht nie vorn (abend_vorn)."""
        def grund_reihe(liste_: list[Kandidat], reihenfolge: str | None) -> list[Kandidat]:
            return bogen(liste_, fmt_name, hook_staerkster=bool(p.get("hook_staerkster", False)) and not reihenfolge,
                         reihenfolge=reihenfolge or str(p.get("reihenfolge", "bogen")))
        if viral is not None:
            return viral.ordne(auswahl, variante, pflicht, grund_reihe)
        return abend_vorn(grund_reihe(auswahl, None), staerkste=vorn_staerkste)

    reihe = ordne(gewaehlt)

    # Vorherrschende Stimmung (nach Länge gewichtet) bestimmt die Musik
    anteile: dict[str, float] = {}
    for k in reihe:
        anteile[k.stimmung] = anteile.get(k.stimmung, 0.0) + k.kern_laenge
    # STIMMUNG_WERT nur noch als Tiebreak (Spec §8.2 nimmt ihn aus der Momentstärke; Rückfrage S2-R2 in
    # docs/ENTSCHEIDUNGEN.md ist offen)
    haupt = max(anteile, key=lambda s: (anteile[s], STIMMUNG_WERT[s]))
    bonus = float(konfig.wert("musik.genre_bonus", 1.5))
    track, wertung = waehle_musik(con, haupt, ziel_s, p, ziel,
                                  bevorzugt={g: bonus for g in konfig.wert("musik.genres_bevorzugt", []) or []})

    raster: list[float] = []
    schlaege: list[float] = []
    versatz = 0.0
    if track is not None:
        schlaege = json.loads(track["beats"])
        # Höhepunkt (letztes Segment) beginnt ungefähr bei ziel_s minus seiner Länge
        hoehe_start = max(0.0, ziel_s - min(reihe[-1].max_laenge(fmt), reihe[-1].kern_laenge))
        versatz = max(0.0, drop(json.loads(track["verlauf"] or "[]")) - hoehe_start)
        if versatz + ziel_s > float(track["dauer_s"]):
            versatz = max(0.0, float(track["dauer_s"]) - ziel_s)
        versatz = naechster([b for b in schlaege if b <= versatz + 1] or [0.0], versatz)
        schritt = max(1, int(p["beats_pro_schnitt"]))
        raster = [round(b - versatz, 3) for b in schlaege if b - versatz > 0.2][schritt - 1::schritt]
    else:
        hinweise.append("keine Musik in der Bibliothek – ohne Musik, Schnitte nicht auf dem Beat")

    segmente = plane_zeitleiste(reihe, raster, fmt, p, fps, fx)
    passt_nicht: list[Kandidat] = []  # eigene Liste: `alle` bleibt die ganze Auswahl (für Zählung und Hinweis)
    # Prüfung 08.10.: nach „⏳ höchstens …“ ist deine Länge eine Obergrenze (regeln.laenge: „nie länger“) – Nachlegen
    # und Kürzen halten sie ein (bis auf ZIEL_TOLERANZ_S fürs Beat-Raster), solange 4 Szenen bleiben. Vorher schoss die
    # letzte Szene darüber (61 s bei „höchstens 55 s“). Sonst max_s wie bisher.
    oben_s = float(fmt["max_s"])
    if einfach and p.get("laenge_richtung") == "lang" and p.get("laenge_grenze_s"):
        oben_s = min(oben_s, max(float(fmt["min_s"]), float(p["laenge_grenze_s"]) + ZIEL_TOLERANZ_S))

    def zu_lang(segmente_: list[dict], reihe_: list[Kandidat]) -> bool:
        """Über max_s – oder über deiner ⏳-Grenze (oben_s), solange danach noch 4 Szenen bleiben."""
        if not segmente_ or len(reihe_) <= 1:
            return False
        ende = segmente_[-1]["zeit_ende"]
        return ende > fmt["max_s"] + 1e-6 or (ende > oben_s + 1e-6 and len(reihe_) > momente_grenzen(fmt)[0])

    def nachlegen(reihe: list[Kandidat], segmente: list[dict]) -> tuple[list[Kandidat], list[dict]]:
        """Beat-Raster kürzt Segmente -> bis zum Ziel nachlegen: erst mit Match-Grenze, notfalls ohne.
        Nur Momente, die unter max_s passen (über 4 Szenen hinaus unter oben_s); die anderen merkt sich passt_nicht."""
        min_m, max_m = momente_grenzen(fmt)
        # Stufen: mit Match-Grenze, ohne – und zuletzt (06.10.) auch gesperrte Momente: lieber ein schon gezeigter
        # Moment als ein Short, der weit unter dem Ziel endet (die Schätzung vorab ist nur ungefähr). Einfacher Modus
        # (08.10.): gesperrte sind dort gar keine Kandidaten, die letzte Stufe holt nichts zurück
        for quelle, mit_grenze in ((vorrat, True), (vorrat, False), (alle, False)):
            vorher = len(gewaehlt)
            # bis zum Ziel – und bis mindestens min_momente (Short: 4), nie über max_momente (Short: 10)
            while segmente and len(gewaehlt) < max_m and (segmente[-1]["zeit_ende"] < ziel_s - 1e-6
                                                         or len(gewaehlt) < min_m):
                je_match: dict[str, int] = {}
                for k in reihe:
                    je_match[k.match_id or ""] = je_match.get(k.match_id or "", 0) + 1
                rest = [k for k in quelle if k not in gewaehlt and k not in passt_nicht
                        and (not mit_grenze or not k.match_id or je_match.get(k.match_id, 0) < int(p["max_je_match"]))
                        and (not k.nachschub or nachschub_darf(gewaehlt, [*gewaehlt, k]))   # Stufe 4: ≤ Hälfte
                        and bekannte_darf(gewaehlt, k, p, min_m)]   # 08.10.: gemischt, nicht dieselbe Zusammenstellung
                if not rest:
                    break
                # Frische-Quote (Review 27.09.): fehlt noch ein frischer Moment, kommt er vor den punktstärkeren alten
                frische = [k for k in rest if k.gezeigt == 0]
                if frische and sum(1 for k in gewaehlt if k.gezeigt == 0) < frische_soll(len(gewaehlt) + 1, p):
                    rest = frische
                naechster_ = max(rest, key=lambda k: (-_wahl_rang(k), k.punkte, k.schluessel))   # neue vor bekannten
                neue_reihe = ordne([*gewaehlt, naechster_])
                neue_segmente = plane_zeitleiste(neue_reihe, raster, fmt, p, fps, fx)
                if neue_segmente[-1]["zeit_ende"] > (oben_s if len(gewaehlt) >= min_m else fmt["max_s"]) + 1e-6:
                    passt_nicht.append(naechster_)
                    continue
                gewaehlt.append(naechster_)
                reihe, segmente = neue_reihe, neue_segmente
                if not mit_grenze and (h := f"mehr als {p['max_je_match']} Momente aus einem Match") not in hinweise:
                    hinweise.append(h)
            if quelle is alle and len(gewaehlt) > vorher and not any(COOLDOWN_AUFGEHOBEN in h for h in hinweise):
                hinweise.append(f"{COOLDOWN_AUFGEHOBEN} – sonst zu kurz")
        return reihe, segmente

    reihe, segmente = nachlegen(reihe, segmente)
    # Zu lang (Short!)? Den Moment mit den wenigsten Punkten (inkl. Gelerntem und Abwechslung) aus der Mitte
    # streichen und neu planen – der Höhepunkt am Schluss bleibt. Danach noch einmal nachlegen (27.09.): Die
    # Auswahl nimmt den letzten Moment auch dann, wenn er über das Ziel schießt (bei Serien bis 20 s) – wurde er
    # hier gestrichen, blieb die Lücke, und die Shorts endeten bei 30–38 s mit 2–3 Momenten statt bei 45 s.
    gekuerzt = False
    while zu_lang(segmente, reihe):
        # Frische-Quote (Review 27.09.): frische Momente sind meist die punktschwächsten – das Kürzen warf sie
        # als Erste wieder raus. Solange die Quote sonst fiele, wird unter den alten gestrichen.
        zur_wahl = [k for k in reihe[:-1] if not any(k is x for x in pflicht)] or reihe[:-1]  # Twist bleibt
        if fassung is not None and sum(1 for k in reihe if not any(k is x for x in pflicht)) <= 1:
            # 🥱: den letzten Ersatz schützen – lieber die schwächste behaltene Szene streichen
            zur_wahl = [k for k in reihe[:-1] if any(k is x for x in pflicht)] or zur_wahl
        # Stufe 4: nie so streichen, dass danach mehr als die Hälfte von früheren Abenden kommt (08.10.: oder zu viele
        # bekannte – bekannte streicht es ohnehin zuerst)
        zur_wahl = [k for k in zur_wahl if nachschub_darf(reihe, ohne := [x for x in reihe if x is not k])
                    and not zu_viele_bekannte(ohne, p, momente_grenzen(fmt)[0])] or zur_wahl
        alte = [k for k in zur_wahl if k.gezeigt > 0]
        if alte and sum(1 for k in reihe if k.gezeigt == 0) <= frische_soll(len(reihe) - 1, p):
            zur_wahl = alte
        raus = min(zur_wahl, key=lambda k: (-_wahl_rang(k), k.punkte, k.intensitaet, k.schluessel))
        ohne_raus = [k for k in reihe if k is not raus]
        neue_reihe = abend_vorn(ohne_raus, staerkste=vorn_staerkste) if viral is None else ohne_raus   # vorn vom Abend
        neue_segmente = plane_zeitleiste(neue_reihe, raster, fmt, p, fps, fx)
        if segmente[-1]["zeit_ende"] <= fmt["max_s"] + 1e-6 and neue_segmente[-1]["zeit_ende"] < fmt["min_s"] - 1e-6:
            break   # Prüfung 08.10.: für deine ⏳-Grenze nie unter 30 s – lieber etwas darüber
        gewaehlt.remove(raus)
        passt_nicht.append(raus)
        reihe, segmente = neue_reihe, neue_segmente
        gekuerzt = True
    if gekuerzt:
        reihe, segmente = nachlegen(reihe, segmente)
    gesamt = segmente[-1]["zeit_ende"] if segmente else 0.0
    unten = max(float(fmt["min_s"]), DAUER_GRENZEN["short"][0])
    # Szenen früherer Abende, die in Frage kamen (08.10.: auch bekannte, keine aus deinen letzten Videos) – übrig sagt,
    # ob eine neue Fassung nach ⏱️ noch welche nehmen kann („mehr starke Szenen gab es nicht“ nur ohne)
    frueher_kandidaten = [k for k in alle if k.von_frueher] or pool
    uebrig = sum(1 for k in frueher_kandidaten if not any(k is x for x in reihe))
    # Stufe 4: höchstens die Hälfte von früheren Abenden, vorn eine Szene vom Abend. Auswahl, Nachlegen und Kürzen
    # halten das ein; verletzen kann es nur eine 🥱-Fassung über behaltene Szenen eines früheren Abends – dann lieber
    # keine Fassung als ein Video, das nicht vom Abend ist (🎬 und neue Fassungen versuchen es danach gemischt).
    if reihe and (ueberhang(reihe) > 0 or reihe[0].nachschub):
        if fassung is not None:
            neue = sum(1 for k in reihe if not any(k is x for x in pflicht))
            fehler = KeineNeuenSzenen(neue, momente_grenzen(fmt)[0], mix=mix)
            fehler.frueher = len(frueher_kandidaten)
            raise fehler
        raise ZuWenigSzenen(sum(1 for k in reihe if not k.nachschub), momente_grenzen(fmt)[0], szenen_gesamt, schon,
                            mix=mix)
    # Stufe 4 (08.10.): Zu kurz? Erst einmal mit mehr Anlauf aus denselben Szenen neu planen – kein Füllmaterial; reicht
    # auch das nicht, mit Nachschub früherer Abende (hatte der Abend genug ungesehene starke Szenen, war er noch nicht
    # dabei). Nur im einfachen Modus (regie.geschmack aus einstellungen.EINFACH_FEST, wie regie_lernen.aktuelle);
    # /experte wie bisher. Bis dahin ist nichts gespeichert: der zweite Plan schreibt seine Schnittliste selbst.
    zweiter = None
    if einfach and (anlauf := mehr_anlauf(p, gesamt, ziel_s, unten)) is not None:
        log.info("Short %.1f s bei Ziel %.0f s – plane neu mit mehr Anlauf (%.1f s davor, %.1f s danach)",
                 gesamt, ziel_s, anlauf["puffer_vor_s"], anlauf["puffer_nach_s"])
        zweiter = ("Mehr Anlauf", {**(parameter or {}), **anlauf}, nachschub)
    elif einfach and nachschub is False and pool and gesamt < ziel_s - ZIEL_TOLERANZ_S - 1e-6:
        # Prüfung 08.10.: schon, wenn das Ziel nicht erreicht ist (vorher erst 10 s darunter) – nach deinem „mindestens
        # 45 s“ blieb ein Abend mit vier starken Szenen sonst bei 40 s, obwohl frühere Abende genug hatten
        log.info("Short %.1f s bei Ziel %.0f s – plane neu mit starken Szenen früherer Abende (%d da)",
                 gesamt, ziel_s, len(pool))
        zweiter = ("Nachschub", parameter, True)
    if zweiter is not None:
        was, parameter_neu, nachschub_neu = zweiter
        try:
            return erstelle(con, konfig, fmt_name, parameter=parameter_neu, name=name, ziel=ziel,
                            nur_matches=nur_matches, hinweise_vorab=hinweise_vorab, gelernt=gelernt,
                            nachschub=nachschub_neu, _speichern=_speichern)
        except RegieFehler as fehler:
            if isinstance(fehler, ZuWenigSzenen) and gesamt < unten - 1e-6:
                raise   # auch so zu kurz: kein Video wie bisher (ZuKurz, bei 🥱 KeineNeuenSzenen)
            log.warning("%s: %s – es bleibt beim ersten Plan (%.1f s)", was, fehler, gesamt)
    schon_bewertet = bewertet_je_moment(con)   # 27.09.: je Segment, wie oft der Moment schon bewertet wurde
    for s in segmente:
        s["bewertet"] = schon_bewertet.get(s["moment"], 0)
    if fassung is not None:   # 🥱: zu wenig Neues für ein ganzes Video – lieber kein Video als dasselbe
        ersatz = sum(1 for k in reihe if not any(k is x for x in pflicht))
        if not ersatz or gesamt < fmt["min_s"] - 1e-6 or len(reihe) < momente_grenzen(fmt)[0]:
            fehler = KeineNeuenSzenen(ersatz, momente_grenzen(fmt)[0], mix=mix)
            fehler.frueher = uebrig   # Stufe 4: ungesehene früherer Abende, die die Hälfte-Grenze nicht zuließ
            raise fehler
    elif (mix or p.get("leiter")) and len(reihe) < momente_grenzen(fmt)[0]:
        # Prüfung 08.10.: 🎬 und neue Fassungen nur mit mindestens 4 Szenen (Florian 28.09.: 4–10) – sonst kamen 3
        # Szenen aus demselben alten Video als „neues Video“; lieber kein Video
        raise ZuWenigSzenen(len(reihe), momente_grenzen(fmt)[0], szenen_gesamt, schon, mix=mix, zusammen=mix)
    if nachschub_n := sum(1 for k in reihe if k.von_frueher):
        log.info("Nachschub: %d von %d Szenen von früheren Abenden (%s)", nachschub_n, len(reihe),
                 ", ".join(k.schluessel for k in reihe if k.von_frueher))
    if bekannt_n := sum(1 for k in reihe if k.wiederholung):
        log.info("Bekannte Szenen: %d von %d (%s)", bekannt_n, len(reihe),
                 ", ".join(f"{k.schluessel} ×{k.gezeigt}" for k in reihe if k.wiederholung))
    if fmt_name == "short" and viral is None and gesamt < unten - 1e-6:   # 07.10.: klare Zeile statt Fachtext
        raise ZuKurz(gesamt, unten, len(alle), szenen_gesamt, bool(p.get("nur_starke")), mix=mix)
    pruefe_dauer(fmt_name, gesamt)
    if gesamt < fmt["min_s"] - 1e-6 or gesamt > fmt["max_s"] + 1e-6:
        raise RegieFehler(f"Dauer {gesamt:.1f} s außerhalb des konfigurierten Bereichs "
                          f"{fmt['min_s']:.0f}–{fmt['max_s']:.0f} s – zu wenig passendes Material")
    if len(reihe) < momente_grenzen(fmt)[0]:
        hinweise.append(f"nur {len(reihe)} Momente (Ziel mindestens {momente_grenzen(fmt)[0]}) – zu wenig passendes Material")
    grenze_s = float(p.get("laenge_grenze_s") or 0.0) if p.get("laenge_richtung") == "kurz" else 0.0
    if einfach and gesamt < grenze_s - ZIEL_TOLERANZ_S - 1e-6:
        # Prüfung 08.10. (Florian: „über 100-mal gesagt, dass die Shorts zu kurz sind“): „mindestens“ nur zusagen, wenn
        # es gehalten wird – reichen auch Nachschub, Mischen und Lockern nicht bis zu deiner Länge, steht es dabei
        hinweise.append(f"kürzer als deine Mindestlänge ({grenze_s:.0f} s) – mehr passende Szenen gab es gerade nicht")
    # Gezählt werden Momente, nicht Segmente (ein Moment mit Jump-Cut hat mehrere Teile, der Hook wiederholt einen)
    momente = [s for s in segmente if s.get("teil", 1) == 1 and s.get("rolle") != "hook"]
    # Einfacher Modus (08.10.): neu = noch nie gesehen; wiederholt = bekannt aus einem früheren Video (nicht aus dem,
    # das diese Fassung ersetzt) – „♻️ 2 Szenen kennst du schon“. Sonst wie bisher: in keinem der letzten Entwürfe
    neu = sum(1 for s in momente if (s["moment"] not in verlauf.gesehen if verlauf is not None else s["gezeigt"] == 0))
    wiederholt = [k.schluessel for k in reihe if k.wiederholung]
    if frueher and len(alle) < 3 * len(momente):
        hinweise.append(f"nur {len(alle)} Momente zur Auswahl – für mehr Abwechslung mehr Clips analysieren")

    # 6. Effekte: Plan in die Segmente (Aus: Schnitt und Übergänge wie vorher, kein Plan)
    hinweise += [h for h in fx_hinweise if h not in hinweise]
    if fx["an"] and segmente:
        beats = [round(b - versatz, 3) for b in schlaege if 0 < b - versatz < gesamt]
        effekte.plane_tempo(segmente, reihe, p, konfig, fmt_name)  # Zeitlupe/Zeitraffer zuerst: passt die Quelle an
        fx_plan = effekte.plane(segmente, reihe, p, konfig, fmt_name, fps, beats, stimmung=haupt)
        fx_plan["hook"] = any(s.get("rolle") == "hook" for s in segmente)
    else:
        fx_plan = {"an": False}
    # Fail-Momente (05.10.): sichtbarer Tod als Anker – Cutter-Maßstab (Hook, Payoff) und Bot sehen ihn wie einen Kill
    nach_schluessel = {k.schluessel: k for k in reihe}
    for s in segmente:
        k = nach_schluessel.get(s["moment"])
        if s.get("rolle") != "hook" and k is not None and k.fail and k.merkmale.get("tod_sekunde") is not None:
            tod = float(k.merkmale["tod_sekunde"])
            if s["quelle_start_s"] - 1e-6 <= tod <= s["quelle_ende_s"] + 1e-6:
                s["tod_s"] = round(tod, 3)
    # 30.09.: Rahmen-Zoom nur so weit, dass der Kill-Titel über der Bedienzone lesbar bleibt (4:3 ist höher als
    # 16:9). Gespeichert wird der WIRKSAME Wert – Kritik, Bot-Anzeige und Publikums-Modell sehen, was im Video ist.
    if fmt_name == "short" and float(p.get("rahmen_zoom", 1.0) or 1.0) > 1.0:
        p["rahmen_zoom"] = entwurf.rahmen_grenze({"format": "short", "parameter": p}, int(fmt["b"]), int(fmt["h"]),
                                                 _quellmasse({s["datei"] for s in segmente}))

    if name is None:
        name = basis = f"{('viral-' + variante) if variante else fmt_name}-{jetzt():%Y%m%d-%H%M%S}"
        n = 1
        while con.execute("SELECT 1 FROM entwuerfe WHERE name = ?", (name,)).fetchone():
            n += 1
            name = f"{basis}-{n}"
    liste = {
        "version": 4, "art": "regie", "name": name, "format": fmt_name,
        "aufloesung": [fmt["b"], fmt["h"]], "fps": fps, "dauer_s": round(gesamt, 3),
        "stimmung": haupt, "parameter": p,
        "musik": None if track is None else {
            "track_id": track["id"], "datei": track["datei"], "titel": track["titel"], "kuenstler": track["kuenstler"],
            "quelle": track["quelle"], "bpm": track["bpm"], "start_s": round(versatz, 3),
            "pegel": float(p["musik_pegel"]), "wertung": {str(k): v for k, v in wertung.items()},
        },
        "overlay": str(konfig.wert("shorts.overlay_text", "clip-battle.de")) if fmt_name == "short" else None,
        "effekte": fx_plan,
        "bogen": [s["intensitaet"] for s in momente],
        "auswahl": {"kandidaten": len(alle), "neu": neu, "schon_gezeigt": len(momente) - neu,
                    "abwechslung": float(p.get("abwechslung", 0.0)),
                    # 27.09.: Bilanz, damit der Bot zeigt, was der Regisseur wirklich sah
                    "gesperrt": bericht["gesperrt"], "ohne_datei": bericht["ohne_datei"], "ersetzt": bericht["ersetzt"],
                    "cooldown": int(p.get("cooldown_entwuerfe", 0)) if float(p.get("abwechslung", 0.0)) > 0 else 0,
                    "frische_quote": (float(p.get("frische_quote", 0.0)) if float(p.get("abwechslung", 0.0)) > 0
                                      else 0.0),
                    # Stufe 4: Szenen früherer Abende im Video (Bot-Zeile „+n Szenen …“, regeln.matches_aus) und wie
                    # viele starke dort noch übrig waren („mehr starke Szenen gab es nicht“ nur ohne)
                    **({"nachschub": [k.schluessel for k in reihe if k.von_frueher], "nachschub_uebrig": uebrig}
                       if nachschub_moeglich or (fassung is not None and einfach) else {}),
                    # 08.10. (Abwechslung mit Ermüdung): bekannte Szenen („♻️ 2 Szenen kennst du schon“), der Abend,
                    # aus dem gebaut wurde (regeln.abend_aus: neue Fassung eines gemischten Videos), gemischt
                    **({"wiederholt": wiederholt, "abend": sorted(nur_matches or []), "mix": mix}
                       if verlauf is not None else {})},
        "segmente": segmente,
        "hinweise": hinweise,
        "erstellt": iso(jetzt()),
    }
    if gelernt:  # 27.09.: was deine letzte Bewertung an diesem Entwurf geändert hat (regie_lernen.wirkung)
        liste["gelernt"] = gelernt
    if viral is not None:  # 🔥 Viral: Mischung und KI-Mittel – Bot-Text, Caption und Publikums-Lernen lesen das
        liste["variante"] = variante
        liste["viral"] = viral.bilanz(reihe, variante, p, segmente)
    if fehler := pruefe_liste(liste, max_lupen=int(fx["max_lupen"]), max_raffer=int(fx["max_raffer"])):
        raise RegieFehler("Schnittliste ungültig: " + "; ".join(fehler[:3]))
    # Passt der Filtergraph samt Effekten auf die Befehlszeile? Sonst scheiterte erst das Rendern.
    if fx_plan["an"] and (fehler := entwurf.graph_fehler(liste, konfig)):
        raise RegieFehler("Schnittliste zu groß: " + fehler[0])
    if not _speichern:   # _mit_lockerung vergleicht erst, gespeichert wird nur der gewählte Plan
        return {"liste": liste, "ziel_s": ziel_s}
    return _speichere(con, konfig, liste)
