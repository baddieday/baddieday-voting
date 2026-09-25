"""Klänge für den Regisseur 2.0 (Bass-Hit, Tick, Whoosh, Pop, Einschlag, Riser) – selbst erzeugt, also lizenzfrei.

Jeder Klang ist eine Formel über t (Sekunden ab Klangbeginn), gerechnet von ffmpeg aevalsrc. Zufall nur über
random(i): Startzustand 0, also jedes Mal dieselben Samples.

  datei()      rendert einen Klang einmal als WAV in den sfx-Ordner. Der Name enthält eine Prüfsumme der Formel:
               ändert sich die Formel, entsteht eine neue Datei (alte bleiben liegen, es wird nie gelöscht).
  mischung()   baut aus den sfx-Ereignissen der Zeitleiste den Teilgraphen mit Ausgang [sfx] (rein, ohne ffmpeg).
  abmischung() letzter Schritt der Tonkette: Spielton (+ geduckte Musik) + [sfx] -> [aout]. Die Klänge kommen
               NACH dem Ducking dazu – die Musik weicht also nur dem Spielton aus, nie einem Klang.

Anker: die Stelle im Klang, die genau auf der Ereigniszeit liegen soll (Bass-Hit: Beginn = Kill, Whoosh: Mitte =
Schnitt, Riser: Ende = erster Kill des Höhepunkts). Liegt der Klangbeginn vor 0, wird vorne abgeschnitten.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from .konfig import Konfig
from .medien import MedienFehler, fuehre_aus

RATE = 48000
AUSBLENDEN_S = 0.03  # am Ende jeder WAV: kein Knacken, wenn der Klang in die Stille springt
SFX_PEGEL = 0.8  # Standard für [regie.effekte].sfx_pegel
ORDNER = "/var/lib/clip-pipeline/sfx"

# Frequenzverlauf per Phase: Ton, der von f0+df auf f0 fällt -> Phase f0*t + (df/k)*(1-exp(-k*t)) Schwingungen
_BASS = "(40*t+(80/18)*(1-exp(-18*t)))"  # 120 -> 40 Hz
_DUMPF = "(30*t+(50/12)*(1-exp(-12*t)))"  # 80 -> 30 Hz
# Rauschen mit Tiefpass 1. Ordnung: ld(0) = letzter Wert, a = Öffnung (klein = dumpf, groß = hell)
_TIEFPASS = "st(0,ld(0)+({a})*((random(1)*2-1)-ld(0)))"

# name: (Formel über t, Dauer s, Anker s, Pegel)
KLAENGE: dict[str, tuple[str, float, float, float]] = {
    # Sinus-Abfall 120 -> 40 Hz, dazu der Oberton (sonst hört man den Treffer auf Handy-Lautsprechern nicht)
    # und ein kurzer Rausch-Knack als Anschlag
    "basshit": (f"0.72*sin(2*PI*{_BASS})*exp(-4.5*t)*min(1,t/0.003)"
                f"+0.2*sin(4*PI*{_BASS})*exp(-7*t)"
                f"+0.3*(random(0)*2-1)*exp(-t/0.008)", 0.9, 0.0, 0.9),
    "tick": ("0.5*sin(2*PI*1800*t)*exp(-60*t)+0.3*(random(0)*2-1)*exp(-t/0.004)", 0.12, 0.0, 0.5),
    # Rauschen, dessen Tiefpass sich zur Mitte öffnet; Hüllkurve sin² mit Spitze in der Mitte (= auf dem Schnitt)
    "whoosh": ("1.1*" + _TIEFPASS.format(a="0.05+0.25*sin(PI*t/0.5)") + "*pow(sin(PI*t/0.5),2)", 0.5, 0.25, 0.6),
    "pop": ("0.6*sin(2*PI*(600*t+4000*t*t))*exp(-25*t)", 0.15, 0.0, 0.5),
    # dumpf und weich einsetzend (10 ms) – kein Knack
    "einschlag": (f"0.85*sin(2*PI*{_DUMPF})*exp(-4*t)*min(1,t/0.01)"
                  f"+0.2*sin(4*PI*{_DUMPF})*exp(-6*t)*min(1,t/0.01)", 0.8, 0.0, 0.8),
    # exponentieller Sweep 200 -> 1200 Hz plus heller werdendes Rauschen, lauter bis zum Ende (= Anker)
    "riser": ("(t/1.5)*(0.55*sin(2*PI*(300/log(6))*(pow(6,t/1.5)-1))+0.5*"
              + _TIEFPASS.format(a="0.02+0.35*t/1.5") + ")", 1.5, 1.5, 0.5),
}


# --- WAV-Dateien -------------------------------------------------------------------

def _quelle(name: str) -> tuple[str, str]:
    """(lavfi-Quelle, Nachbearbeitung) eines Klangs. Stereo: beide Kanäle rechnen dieselbe Formel."""
    ausdruck, dauer, _anker, _pegel = KLAENGE[name]
    quelle = f"aevalsrc=exprs='{ausdruck}':c=stereo:s={RATE}:d={dauer:.4f}"
    # curve=cub: in den letzten 5 ms nur noch ≤ 0,5 % des Pegels (linear wären es 17 % – hörbarer Knack)
    nach = (f"afade=t=out:st={dauer - AUSBLENDEN_S:.4f}:d={AUSBLENDEN_S:.4f}:curve=cub,"
            "aformat=sample_fmts=s16:channel_layouts=stereo")
    return quelle, nach


def ordner(konfig: Konfig) -> Path:
    return Path(str(konfig.wert("regie.effekte.sfx_ordner", ORDNER) or ORDNER))


def pegel(konfig: Konfig) -> float:
    """[regie.effekte].sfx_pegel, gestutzt auf 0..2 (Übersteuern fängt der Limiter am Ende ab)."""
    return max(0.0, min(2.0, float(konfig.wert("regie.effekte.sfx_pegel", SFX_PEGEL))))


def pfad(konfig: Konfig, name: str) -> Path:
    """Wo die WAV eines Klangs liegt (ohne sie zu erzeugen). Die Prüfsumme deckt Formel, Dauer und Nachbearbeitung."""
    if name not in KLAENGE:
        raise MedienFehler(f"Unbekannter Klang {name!r}")
    quelle, nach = _quelle(name)
    return ordner(konfig) / f"{name}-{hashlib.sha1(f'{quelle}|{nach}'.encode()).hexdigest()[:8]}.wav"


def datei(konfig: Konfig, name: str) -> Path:
    """WAV eines Klangs (48 kHz, Stereo, 16 bit) – erzeugt sie beim ersten Mal. Idempotent, löscht nie etwas."""
    ziel = pfad(konfig, name)
    if ziel.is_file():
        return ziel
    ziel.parent.mkdir(parents=True, exist_ok=True)
    quelle, nach = _quelle(name)
    tmp = ziel.with_name(f".{ziel.stem}.{os.getpid()}.tmp")  # eigener Name je Prozess: nie halbfertige WAVs
    # bitexact + ohne Metadaten: keine Versionskennung im Kopf -> gleiche Formel, gleiche Datei (md5)
    fuehre_aus(["ffmpeg", "-hide_banner", "-nostdin", "-y", "-f", "lavfi", "-i", quelle, "-af", nach,
                "-c:a", "pcm_s16le", "-map_metadata", "-1", "-fflags", "+bitexact", "-f", "wav", str(tmp)],
               f"Klang {name}")
    tmp.replace(ziel)
    return ziel


def eingaenge(konfig: Konfig, klaenge: list[str]) -> list[str]:
    """ffmpeg-Argumente für die Klang-Eingänge in der Reihenfolge von mischung() (erzeugt fehlende WAVs)."""
    return [x for name in klaenge for x in ("-i", str(datei(konfig, name)))]


# --- Mischung (reiner Text, ohne ffmpeg) ------------------------------------------------

def _feld(ereignis, name: str):
    """Ereignisse dürfen dicts oder Objekte (effekte.Ereignis) sein."""
    return ereignis.get(name) if isinstance(ereignis, dict) else getattr(ereignis, name, None)


def _stuecke(ereignisse, sfx_pegel: float) -> list[tuple[int, int, int, str, float]]:
    """(beginn, laenge, trim, klang, lautstaerke) aller hörbaren sfx-Ereignisse in Samples auf der Zeitleiste,
    nach Beginn sortiert (feste Reihenfolge = gleicher Graph). trim = vorne abgeschnitten (Klang begänne vor 0)."""
    ergebnis = []
    for e in ereignisse:
        klang, staerke = _feld(e, "klang"), float(_feld(e, "staerke") or 0.0)
        if _feld(e, "art") != "sfx" or klang not in KLAENGE or staerke <= 0:
            continue
        _ausdruck, dauer, anker, klang_pegel = KLAENGE[klang]
        beginn, laenge = round((float(_feld(e, "t")) - anker) * RATE), round(dauer * RATE)
        trim = max(0, -beginn)
        if trim >= laenge:  # läge ganz vor dem Video
            continue
        ergebnis.append((max(0, beginn), laenge - trim, trim, klang, klang_pegel * staerke * sfx_pegel))
    return sorted(ergebnis)


def mischung(ereignisse, erster_eingang: int, sfx_pegel: float = SFX_PEGEL) -> tuple[list[str], str]:
    """(Klänge in Eingangs-Reihenfolge, Teilgraph mit Ausgang [sfx]). Ohne hörbare sfx-Ereignisse: ([], "").

    ereignisse: z. B. effekte.zeitleiste(liste) – nur art "sfx" zählt; t auf der Zeitleiste (= Graph-Zeit).
    Eingang erster_eingang + j ist die WAV von klaenge[j] (sfx.eingaenge liefert die -i-Argumente dazu).

    Je Ereignis: Lautstärke Pegel·Stärke·sfx_pegel, Anker samplegenau auf t. Klänge, die sich nicht überlappen,
    liegen hintereinander auf einer Spur (Pause davor per adelay, dann concat); amix mischt nur die wenigen Spuren.
    Würde jeder Klang einzeln ab 0 aufgefüllt, rechnete amix N Spuren über die ganze Länge (150 Klänge auf
    300 s: 8 s statt Bruchteilen einer Sekunde)."""
    stuecke = _stuecke(ereignisse, sfx_pegel)
    if not stuecke:
        return [], ""
    klaenge = [k for k in KLAENGE if any(s[3] == k for s in stuecke)]
    teile = []
    for j, k in enumerate(klaenge):
        anzahl = sum(1 for s in stuecke if s[3] == k)
        ausgaenge = "".join(f"[sfxk{j}_{n}]" for n in range(anzahl))
        teile.append(f"[{erster_eingang + j}:a]aresample={RATE},aformat=sample_fmts=fltp:channel_layouts=stereo,"
                     f"asplit={anzahl}{ausgaenge}")
    # Spuren verteilen: jedes Stück auf die erste Spur, die bis zu seinem Beginn frei ist
    spuren: list[list[tuple[int, int]]] = []  # je Spur: (Stück, Pause davor)
    enden: list[int] = []
    for i, (beginn, laenge, *_rest) in enumerate(stuecke):
        s = next((n for n, ende in enumerate(enden) if ende <= beginn), len(spuren))
        if s == len(spuren):
            spuren.append([])
            enden.append(0)
        spuren[s].append((i, beginn - enden[s]))
        enden[s] = beginn + laenge
    benutzt = dict.fromkeys(klaenge, 0)
    spur_namen = [f"[sfxs{s}]" for s in range(len(spuren))] if len(spuren) > 1 else ["[sfx]"]
    for spur, spur_name in zip(spuren, spur_namen):
        namen = [f"[sfxp{i}]" for i, _pause in spur] if len(spur) > 1 else [spur_name]
        for (i, pause), name in zip(spur, namen):
            _beginn, _laenge, trim, klang, lautstaerke = stuecke[i]
            kette = [f"atrim=start_sample={trim},asetpts=PTS-STARTPTS"] if trim else []
            kette.append(f"volume={lautstaerke:.4f}")
            if pause:
                kette.append(f"adelay=delays={pause}S:all=1")
            teile.append(f"[sfxk{klaenge.index(klang)}_{benutzt[klang]}]{','.join(kette)}{name}")
            benutzt[klang] += 1
        if len(spur) > 1:
            teile.append(f"{''.join(namen)}concat=n={len(spur)}:v=0:a=1{spur_name}")
    if len(spuren) > 1:
        teile.append(f"{''.join(spur_namen)}amix=inputs={len(spuren)}:normalize=0:duration=longest[sfx]")
    return klaenge, ";".join(teile)


def abmischung(ton: list[str], mit_sfx: bool) -> str:
    """Letzte Zeile der Tonkette -> [aout]. ton = ["[spiel]", "[leiser]"] (mit Musik) bzw. [a] (ohne).

    Ohne Klänge zeichengleich mit heute. Mit Klängen: amix duration=first (der Spielton bestimmt die Länge),
    Limiter mit level=0 (sonst hebt er wieder auf 0 dBFS an) und latency=1 (gleicht seine 5 ms Vorlauf aus)."""
    if not mit_sfx:
        if len(ton) == 1:
            return f"{ton[0]}apad[aout]"
        return f"{''.join(ton)}amix=inputs={len(ton)}:normalize=0,alimiter=limit=0.95,apad[aout]"
    return (f"{''.join(ton)}[sfx]amix=inputs={len(ton) + 1}:normalize=0:duration=first,"
            "alimiter=limit=0.95:level=0:latency=1,apad[aout]")
