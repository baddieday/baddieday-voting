"""Versionierte, numerische Videomerkmale; fehlende Werte bleiben unbekannt.

Die vollständige Schnittliste bleibt zusätzlich als Snapshot erhalten. Korrelationen
über ganze Videos erlauben keine Behauptung, ein einzelner Moment sei die Ursache.
"""
from __future__ import annotations

import math

VERSION = 1
INHALT_SKALEN = {"kill_punkte": 10., "victory_royale": 1., "lautstaerke": 1.,
                 "platzierung": 100., "sniper": 1., "nahkampf": 1., "bot_opfer": 1.,
                 "phase": 1., "endgame": 1., "clutch": 1., "mic_lachen": 3.,
                 "mic_jubel": 3., "mic_frust": 3., "mic_laut": 3., "spitzen": 4.}
STEUERUNG = {"seg_min_faktor": 2., "beats_pro_schnitt": 4., "effekt_hektik": 1.3,
             "musik_pegel": 1., "hook_staerkster": 1.}


def zahl(x):
    if isinstance(x, (int, float)) and math.isfinite(x):
        return float(x)
    return None


def dauer_features(dauer: float, fmt: str) -> dict:
    zentren = (80, 95, 110, 120) if fmt == "zusammenschnitt" else (35, 45, 55, 65, 72)
    breite = 14 if fmt == "zusammenschnitt" else 8
    return {f"dauer_{z}": math.exp(-.5 * ((dauer-z)/breite)**2) for z in zentren}


def extrahiere(snapshot: dict) -> dict[str, float]:
    liste = snapshot.get("schnittliste") or {}
    p = liste.get("parameter") or snapshot.get("parameter") or {}
    mk = snapshot.get("merkmale") or {}
    fmt = snapshot.get("format") or liste.get("format") or "short"
    dauer = float(snapshot["dauer_s"])
    x = dauer_features(dauer, fmt)
    x["format_zusammenschnitt"] = float(fmt == "zusammenschnitt")
    for k, skala in STEUERUNG.items():
        if (v := zahl(p.get(k))) is not None:
            x[k] = max(0., min(1., v/skala))
    seg = liste.get("segmente") or []
    if seg:
        laengen = [float(s["zeit_ende"])-float(s["zeit_start"]) for s in seg
                   if "zeit_ende" in s and "zeit_start" in s]
        if laengen:
            x["hook_s"] = min(1., laengen[0]/15)
            x["szene_s"] = min(1., sum(laengen)/len(laengen)/20)
        x["schnitte_pro_s"] = min(1., (len(seg)-1)/max(1, dauer))
        uebergaenge = [s["uebergang"] for s in seg if isinstance(s.get("uebergang"), dict)]
        if uebergaenge:
            for art in sorted({str(u["art"]) for u in uebergaenge if "art" in u}):
                x["uebergang:"+art] = sum(u.get("art") == art for u in uebergaenge)/len(uebergaenge)
            dauern = [zahl(u.get("dauer_s")) for u in uebergaenge]
            dauern = [v for v in dauern if v is not None]
            if dauern:
                x["uebergang_s"] = min(1., sum(dauern)/len(dauern)/3.)
        # Der tatsächliche Plan benutzt Quellzeitfenster lupe/raffer, kein Feld
        # "tempo". Ihre im fertigen Video sichtbare Dauer ist (bis-ab)/faktor.
        if liste.get("version", 0) >= 4 or any("lupe" in s or "raffer" in s for s in seg):
            stumm = 0.
            for name, schluessel in (("zeitlupe", "lupe"), ("zeitraffer", "raffer")):
                sichtbar = 0.
                for s in seg:
                    fenster = s.get(schluessel) or {}
                    faktor = zahl(fenster.get("faktor"))
                    ab, bis = zahl(fenster.get("ab_s")), zahl(fenster.get("bis_s"))
                    if faktor and ab is not None and bis is not None:
                        laenge = max(0., bis-ab)/faktor
                        sichtbar += laenge
                        if fenster.get("ton") == "stumm":
                            stumm += laenge
                x[name] = min(1., sichtbar/max(1., dauer))
            # Geplanter Spielton: Renderer mischt ihn unabhängig von Musik; nur
            # explizit stumme Tempo-Fenster nehmen ihn heraus. Mikro/Chat separat.
            x["originalton"] = max(0., 1.-stumm/max(1., dauer))
        stimmen = [(float(s["zeit_ende"])-float(s["zeit_start"]), bool(s["stimmen"]))
                   for s in seg if "stimmen" in s and "zeit_start" in s and "zeit_ende" in s]
        bekannte_dauer = sum(max(0., d) for d, _ in stimmen)
        if bekannte_dauer:
            x["stimmen_anteil"] = sum(max(0., d) for d, an in stimmen if an)/bekannte_dauer
        if any("effekte" in s for s in seg):
            effekte = [e for s in seg for e in s.get("effekte", [])]
            x["effekte_pro_s"] = min(1., len(effekte)/max(1., dauer)/3.)
            staerken = [zahl(e.get("staerke")) for e in effekte]
            staerken = [v for v in staerken if v is not None]
            if staerken:
                x["effekt_staerke"] = sum(staerken)/len(staerken)
    if "overlay" in liste:
        x["overlay"] = float(bool(liste["overlay"]))
    if "effekte" in liste:
        x["effekte_an"] = float(bool((liste["effekte"] or {}).get("an")))
    musik = liste.get("musik") or mk.get("musik") or {}
    if musik.get("track_id") is not None:
        x["track:"+str(musik["track_id"])] = 1.
    if (stimmung := liste.get("stimmung") or mk.get("stimmung")):
        x["stimmung:"+stimmung] = 1.
    momente = mk.get("momente") or []
    for name, skala in INHALT_SKALEN.items():
        werte = [zahl(m.get("merkmale", {}).get(name)) for m in momente]
        bekannt = [min(1., max(-1., v/skala)) for v in werte if v is not None]
        if bekannt:
            x["inhalt:"+name] = sum(bekannt)/len(bekannt)
        if werte and werte[0] is not None:
            x["hook:"+name] = min(1., max(-1., werte[0]/skala))
    return x
