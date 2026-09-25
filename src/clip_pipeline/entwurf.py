"""Rendern einer Regie-Schnittliste (`pipeline render-entwurf <id> [--final | --messen]`).

Entwurf (auf dem Mini): klein genug für Telegram (< 48 MB), 720p bzw. 720×1280, VA-API wenn vorhanden, sonst CPU.
Final (auf pve-big, NVENC): volle Auflösung. Der Mini legt dafür einen Auftrag an, pve-big rendert ihn per
`clip-big-steuer final <name>` und fährt danach herunter (siehe big.py, deploy/big/).

Aufbau des FFmpeg-Filtergraphen:
  1. je Segment: Ausschnitt mit Übergangs-Griffen, zuerst feste Bildrate, dann Bild auf Zielgröße (16:9 mit Rand
     bzw. 9:16 mit unscharfem Hintergrund); Ton: alle Spuren (Spiel + Mikro) zu Stereo gemischt
  2. Verketten mit xfade/acrossfade – die Mitte jedes Übergangs liegt genau auf der Segmentgrenze (dem Beat)
  3. Effekte (Regisseur 2.0, nur mit liste["effekte"].an): Zoom je Segment nur auf dem Spielbild, Look (Short je
     Segment, 16:9 danach auf das ganze Bild), nach den Übergängen Blenden-Filter und Kill-Titel/Zähler
     (effekt_filter.py) – der Plan steht in der Schnittliste
  4. Musik ab ihrem Versatz, geduckt unter dem Spielton (sidechaincompress), ein- und ausgeblendet; die Klänge des
     Plans (sfx.py) kommen danach dazu
  5. Short: Schriftzug "clip-battle.de"
Ohne Effekte (an = false oder version 3) ist der Graph zeichengleich mit dem von vorher.
"""

from __future__ import annotations

import json
import math
import shutil
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path

from . import effekt_filter, effekte, musik, sfx, shorts
from .konfig import Konfig, KonfigFehler
from .medien import MedienFehler, fuehre_aus, probe

ENTWURF_KURZE_SEITE = 720
UEBERHANG_S = 0.2
ENTWURF_KBIT = 4000
# Der Filtergraph ist EIN Argument auf der Befehlszeile – Linux erlaubt je Argument höchstens 128 KB
MAX_GRAPH = 64_000


def schnitt_dauer(fps: int) -> float:
    """Harter Schnitt = Übergang über ein einziges Bild. Aufrunden! Ist die Dauer auch nur minimal kürzer als
    ein Bild (1/30 -> "0.0333"), bricht xfade still ab und das Video endet nach dem ersten Segment."""
    return math.ceil(10000 / fps) / 10000


def _griffe(segmente: list[dict], fps: int) -> list[tuple[float, float]]:
    """(vorne, hinten) je Segment: was zusätzlich aus der Quelle genommen wird, damit die Übergänge die
    Zeitleiste nicht verkürzen. Weiche Übergänge: je die halbe Dauer vorne und hinten (Mitte auf dem Beat).
    Harter Schnitt: das vorherige Segment bekommt das eine Übergangsbild dazu."""
    ergebnis = []
    for i, s in enumerate(segmente):
        vorne = s["uebergang"]["dauer_s"] / 2 if i > 0 and s["uebergang"]["art"] != "schnitt" else 0.0
        hinten = 0.0
        if i + 1 < len(segmente):
            naechster = segmente[i + 1]["uebergang"]
            hinten = schnitt_dauer(fps) if naechster["art"] == "schnitt" else naechster["dauer_s"] / 2
        ergebnis.append((vorne, hinten))
    return ergebnis


def _gerade(x: float) -> int:
    return int(round(x / 2) * 2)


def _bild(i: int, b: int, h: int, fps: int, hochformat: bool, zoom: str = "", look: str = "") -> str:
    """fps zuerst: Alle weiteren Filter sehen nur noch die Bilder, die ins Ergebnis kommen (60 fps -> halb so viele).
    zoom: Kette aus effekt_filter.zoom – sitzt auf dem Spielbild allein (Short: vor dem Einsetzen in den unscharfen
    Hintergrund, 16:9: vor dem Rand), der Hintergrund zoomt also nie mit.
    look (nur Short): Farblook auf Spielbild und kleinem Hintergrund, bevor beide zusammengesetzt werden."""
    z = f",{zoom}" if zoom else ""
    lk = f",{look}" if look else ""
    if hochformat:  # wie shorts.py "unschaerfe": ganzes Spielbild, unscharfer Hintergrund füllt den Rand
        # Hintergrund auf b/4 × h/4 weichzeichnen (1/16 der Pixel; Radius 5 statt 20 = gleich unscharf), dann hoch
        b4, h4 = _gerade(b / 4), _gerade(h / 4)
        return (f"[{i}:v]fps={fps},split=2[hg{i}][vg{i}];"
                f"[hg{i}]scale={b4}:{h4}:force_original_aspect_ratio=increase,crop={b4}:{h4},boxblur=5:2{lk},"
                f"scale={b}:{h},eq=brightness=-0.08[hgb{i}];"
                f"[vg{i}]scale={b}:-2{z}{lk}[vgs{i}];[hgb{i}][vgs{i}]overlay=(W-w)/2:(H-h)/2,"
                f"format=yuv420p,setsar=1,settb=AVTB[v{i}]")
    return (f"[{i}:v]fps={fps},scale={b}:{h}:force_original_aspect_ratio=decrease{z},pad={b}:{h}:(ow-iw)/2:(oh-ih)/2,"
            f"format=yuv420p,setsar=1,settb=AVTB[v{i}]")


def _ton(i: int, spuren: int, dauer: float) -> str:
    einheit = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
    if spuren == 0:
        return f"anullsrc=r=48000:cl=stereo,atrim=0:{dauer:.3f},{einheit}[a{i}]"
    if spuren == 1:
        return f"[{i}:a:0]{einheit},apad,atrim=0:{dauer:.3f}[a{i}]"
    eingaenge = "".join(f"[{i}:a:{s}]" for s in range(spuren))
    return f"{eingaenge}amix=inputs={spuren}:normalize=0,{einheit},apad,atrim=0:{dauer:.3f}[a{i}]"


def filtergraph(liste: dict, spuren: list[int], *, b: int, h: int, musik_eingang: int | None,
                schrift: Path | None, sfx_pegel: float = sfx.SFX_PEGEL,
                zeichenbreite: float = effekte.STANDARD["titel_zeichenbreite"],
                spiel_h: int | None = None) -> tuple[str, float]:
    """(Graph, Länge). Eingänge: 0 … n−1 die Segmente, dann die Musik (musik_eingang), dann die Klänge des Plans in
    der Reihenfolge von sfx.mischung. schrift: für clip-battle.de und die Kill-Titel (ohne: keine Texte).
    spiel_h: Höhe des höchsten Spielbilds im Short (effekt_filter.spiel_hoehe) – die Texte bleiben darüber und
    darunter; ohne Angabe 16:9."""
    segmente = liste["segmente"]
    fps = int(liste["fps"])
    hoch = liste["format"] == "short"
    griffe = _griffe(segmente, fps)
    ereignisse = effekte.zeitleiste(liste)  # leer ohne Effekte (an = false oder version 3)
    zooms = effekt_filter.zooms_je_segment(liste, ereignisse, griffe)
    look = effekt_filter.look_der_liste(liste) if hoch else ""  # 16:9: einmal global (effekt_filter.global_kette)
    teile, laengen = [], []
    for i, (s, (vorne, hinten)) in enumerate(zip(segmente, griffe)):
        laenge = (s["zeit_ende"] - s["zeit_start"]) + vorne + hinten
        laengen.append(laenge)
        teile.append(_bild(i, b, h, fps, hoch, effekt_filter.zoom(i, zooms[i]) if i in zooms else "", look))
        teile.append(_ton(i, spuren[i], laenge))
    # Verketten: offset_i = bisherige Länge − Übergangsdauer (siehe Herleitung in docs/ENTSCHEIDUNGEN.md E8)
    v, a, gesamt = "[v0]", "[a0]", laengen[0]
    for i in range(1, len(segmente)):
        u = segmente[i]["uebergang"]
        d = u["dauer_s"] if u["art"] != "schnitt" else 0.0
        art = effekt_filter.xfade_art(u["art"])
        d_x = d if d > 0 else schnitt_dauer(fps)
        teile.append(f"{v}[v{i}]xfade=transition={art}:duration={d_x:.4f}:offset={gesamt - d_x:.4f}[vx{i}]")
        # Ton gleich lang überblenden wie das Bild – sonst laufen Bild und Ton pro Schnitt auseinander
        teile.append(f"{a}[a{i}]acrossfade=d={d_x:.4f}:c1=tri:c2=tri[ax{i}]")
        gesamt += laengen[i] - d_x
        v, a = f"[vx{i}]", f"[ax{i}]"
    kette = v
    if fx := effekt_filter.global_kette(liste, ereignisse, b, h, schrift, zeichenbreite, spiel_h):
        teile.append(f"{kette}{fx}[vfx]")
        kette = "[vfx]"
    if hoch and liste.get("overlay") and schrift is not None:
        teile.append(f"{kette}drawtext=fontfile={shorts._filterpfad(schrift)}:text={shorts._text(liste['overlay'])}:"
                     f"fontsize={int(h * 0.03)}:fontcolor=white@0.9:borderw=4:bordercolor=black@0.6:"
                     f"x=(w-text_w)/2:y=h*0.12[vtext]")
        kette = "[vtext]"
    teile.append(f"{kette}null[vout]")
    # Klänge des Plans: Eingänge hinter Segmenten und Musik, Ausgang [sfx]; sie kommen NACH dem Ducking dazu
    _klaenge, sfx_teil = sfx.mischung(ereignisse, len(segmente) + (musik_eingang is not None), sfx_pegel)
    if sfx_teil:
        teile.append(sfx_teil)
    if musik_eingang is not None:
        m = liste["musik"]
        teile += [
            f"{a}asplit=2[spiel][schluessel]",
            f"[{musik_eingang}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
            f"volume={m['pegel']},atrim=0:{gesamt:.3f},afade=t=in:d=0.5,afade=t=out:st={max(0.0, gesamt - 2):.3f}:d=2[mus]",
            "[mus][schluessel]sidechaincompress=threshold=0.05:ratio=6:attack=20:release=400[leiser]",
            sfx.abmischung(["[spiel]", "[leiser]"], bool(sfx_teil)),
        ]
    else:
        teile.append(sfx.abmischung([a], bool(sfx_teil)))
    # apad + "-t gesamt" am Ausgang: Der Ton ist immer genau so lang wie das Bild. Die Mischkette (amix,
    # sidechaincompress, alimiter) verlor im Test gelegentlich bis zu 0,1 s am Ende, nicht reproduzierbar.
    return ";".join(teile), gesamt


def encoder(konfig: Konfig, final: bool) -> tuple[list[str], list[str], str]:
    """(globale Optionen, Video-Optionen, Name). Final: NVENC; Entwurf: VA-API, sonst libx264.
    Bei VA-API lädt der Filtergraph die Bilder selbst auf die GPU (hwupload) – kein -vf neben -filter_complex."""
    if final:
        return [], ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "20", "-b:v", "0"], "h264_nvenc"
    geraet = str(konfig.wert("schnitt.vaapi_geraet", "/dev/dri/renderD128"))
    if Path(geraet).exists() and konfig.wert("regie.vaapi", True):
        return ["-vaapi_device", geraet], ["-c:v", "h264_vaapi"], "h264_vaapi"
    return [], ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p"], "libx264"


def rendere(liste: dict, ziel: Path, konfig: Konfig, *, final: bool = False, max_bytes: int = 48_000_000,
            encoder_name: str | None = None) -> dict:
    segmente = liste["segmente"]
    b, h = liste["aufloesung"]
    if not final:  # Entwurf: kurze Seite 720
        faktor = ENTWURF_KURZE_SEITE / min(b, h)
        b, h = _gerade(b * faktor), _gerade(h * faktor)
    griffe = _griffe(segmente, int(liste["fps"]))
    befehl = ["ffmpeg", "-hide_banner", "-nostdin", "-y"]
    spuren, spiel_h = [], 0
    for s, (vorne, hinten) in zip(segmente, griffe):
        datei = Path(s["datei"])
        if not datei.is_file():
            raise MedienFehler(f"Moment-Datei fehlt: {datei}")
        info = probe(datei)
        spuren.append(len(info.tonspuren))
        if info.breite and info.hoehe:  # Short: Texte über/unter dem höchsten Spielbild (4:3-Aufnahme ist höher)
            spiel_h = max(spiel_h, effekt_filter.spiel_hoehe(b, info.breite, info.hoehe))
        start = s["quelle_start_s"] - vorne
        dauer = (s["quelle_ende_s"] - s["quelle_start_s"]) + vorne + hinten
        # Überhang: xfade braucht Bilder bis GANZ ans Ende des Übergangs, sonst bricht die Ausgabe still ab.
        # Überzählige Bilder verwirft xfade; der Ton wird im Graphen exakt auf die Länge geschnitten.
        ueberhang = max(0.0, min(UEBERHANG_S, info.dauer_s - (start + dauer)))
        befehl += ["-ss", f"{max(0.0, start):.3f}", "-t", f"{dauer + ueberhang:.3f}", "-i", str(datei)]
    musik_eingang = None
    if m := liste.get("musik"):
        datei = musik.ordner(konfig) / m["datei"]
        if not datei.is_file():
            raise MedienFehler(f"Musik fehlt: {datei}")
        musik_eingang = len(segmente)
        befehl += ["-stream_loop", "-1", "-ss", f"{m['start_s']:.3f}", "-i", str(datei)]
    # Klänge des Effekt-Plans: je Klang eine WAV (wird beim ersten Mal erzeugt), Reihenfolge wie in filtergraph
    ereignisse = effekte.zeitleiste(liste)
    klaenge, _ = sfx.mischung(ereignisse, len(segmente) + (musik_eingang is not None), sfx.pegel(konfig))
    befehl += sfx.eingaenge(konfig, klaenge)
    graph, gesamt = filtergraph(liste, spuren, b=b, h=h, musik_eingang=musik_eingang, schrift=_schrift(liste, konfig),
                                sfx_pegel=sfx.pegel(konfig), zeichenbreite=_zeichenbreite(konfig),
                                spiel_h=spiel_h or None)
    if len(graph.encode()) >= MAX_GRAPH:
        raise MedienFehler(f"Entwurf {liste['name']}: Filtergraph {len(graph.encode()) // 1000} KB – höchstens "
                           f"{MAX_GRAPH // 1000} KB")
    eingang, video, name = encoder(konfig, final)
    if encoder_name == "libx264":
        eingang, video, name = [], ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p"], "libx264"
    # Entwurf: gute Qualität, aber gedeckelt – höchstens ENTWURF_KBIT und sicher unter max_bytes (Telegram).
    # (Das volle Budget auszuschöpfen hieße 40 MB für 40 s – unnötig für eine Vorschau auf dem Handy.)
    kbit = max(400, min(int(max_bytes * 8 * 0.88 / max(gesamt, 1) / 1000) - 160, ENTWURF_KBIT))
    deckel = ["-maxrate", f"{kbit}k", "-bufsize", f"{2 * kbit}k"]
    rate = [] if final else (["-b:v", f"{kbit}k", *deckel] if name == "h264_vaapi" else ["-crf", "23", *deckel])
    if name == "h264_vaapi":  # VA-API: Filter-Ausgang auf die GPU hochladen
        graph = graph.replace("null[vout]", "format=nv12,hwupload[vout]")
    ziel.parent.mkdir(parents=True, exist_ok=True)
    tmp = ziel.with_name(ziel.stem + ".tmp" + ziel.suffix)
    befehl = befehl[:4] + eingang + befehl[4:]
    befehl += ["-filter_complex", graph, "-map", "[vout]", "-map", "[aout]", "-t", f"{gesamt:.3f}", *video, *rate]
    befehl += ["-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(tmp)]
    try:
        fuehre_aus(befehl, f"Entwurf {liste['name']}")
    except MedienFehler:
        if name == "h264_vaapi" and encoder_name is None:  # VA-API streikt -> CPU
            return rendere(liste, ziel, konfig, final=final, max_bytes=max_bytes, encoder_name="libx264")
        raise
    groesse = tmp.stat().st_size
    if not final and groesse > max_bytes:
        tmp.unlink(missing_ok=True)
        raise MedienFehler(f"Entwurf {liste['name']} ist {groesse // 1_000_000} MB groß (Grenze {max_bytes // 1_000_000})")
    tmp.replace(ziel)
    return {"datei": str(ziel), "mb": round(groesse / 1e6, 1), "dauer_s": round(gesamt, 2), "encoder": name,
            "aufloesung": [b, h]}


def _schrift(liste: dict, konfig: Konfig) -> Path | None:
    """Schrift für clip-battle.de (Short) und für Kill-Titel/Zähler – nur wenn etwas zu schreiben ist."""
    texte = any(e.art in ("titel", "zaehler") for e in effekte.zeitleiste(liste))
    if texte or (liste["format"] == "short" and liste.get("overlay")):
        return shorts.schrift(konfig)
    return None


def _zeichenbreite(konfig: Konfig) -> float:
    return float(effekte.einstellungen(konfig)[0]["titel_zeichenbreite"])


def graph_fehler(liste: dict, konfig: Konfig) -> list[str]:
    """Schon beim Planen (regie.erstelle): Passt der Filtergraph der Liste auf die Befehlszeile? Gerechnet mit dem
    größten Fall – volle Auflösung, zwei Tonspuren je Segment, Schrift und Musik wie beim Rendern."""
    b, h = liste["aufloesung"]
    try:
        schrift = _schrift(liste, konfig)
    except MedienFehler:  # keine Schrift gefunden: die Länge zählt, nicht die Datei
        schrift = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
    n = len(liste["segmente"])
    graph, _ = filtergraph(liste, [2] * n, b=b, h=h, musik_eingang=n if liste.get("musik") else None, schrift=schrift,
                           sfx_pegel=sfx.pegel(konfig), zeichenbreite=_zeichenbreite(konfig))
    groesse = len(graph.encode())
    if groesse >= MAX_GRAPH:
        return [f"Filtergraph {groesse // 1000} KB – höchstens {MAX_GRAPH // 1000} KB (zu viele Effekte)"]
    return []


def _zeile(con: sqlite3.Connection, entwurf_id: int) -> sqlite3.Row:
    zeile = con.execute("SELECT * FROM entwuerfe WHERE id = ?", (entwurf_id,)).fetchone()
    if zeile is None:
        raise MedienFehler(f"Entwurf {entwurf_id} unbekannt")
    return zeile


def _max_bytes(konfig: Konfig) -> int:
    return int(float(konfig.wert("vorschau.max_mb", 48)) * 1_000_000)


def entwurf(con: sqlite3.Connection, konfig: Konfig, entwurf_id: int) -> dict:
    """Rendert den Entwurf eines compose-Laufs (idempotent) und merkt ihn in der Datenbank."""
    zeile = _zeile(con, entwurf_id)
    if zeile["datei"] and Path(zeile["datei"]).is_file():
        return {"entwurf": entwurf_id, "datei": zeile["datei"], "uebersprungen": True}
    liste = json.loads(Path(zeile["schnittliste"]).read_text(encoding="utf-8"))
    ziel = Path(zeile["schnittliste"]).with_suffix(".mp4")
    ergebnis = rendere(liste, ziel, konfig, max_bytes=_max_bytes(konfig))
    con.execute("UPDATE entwuerfe SET datei = ?, status = CASE WHEN status = 'neu' THEN 'gerendert' ELSE status END "
                "WHERE id = ?", (str(ziel), entwurf_id))
    return {"entwurf": entwurf_id, **ergebnis}


def messen(con: sqlite3.Connection, konfig: Konfig, entwurf_id: int) -> dict:
    """`render-entwurf <id> --messen`: rendert wie entwurf(), aber immer neu und in einen eigenen Temp-Ordner neben
    der Schnittliste (gleiche Platte wie der echte Entwurf), der danach wieder weg ist. Die Datenbank bleibt
    unverändert – so lässt sich die Renderzeit vor und nach einer Änderung vergleichen."""
    zeile = _zeile(con, entwurf_id)
    liste = json.loads(Path(zeile["schnittliste"]).read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix=".messen-", dir=Path(zeile["schnittliste"]).parent) as ordner:
        start = time.monotonic()
        ergebnis = rendere(liste, Path(ordner) / "messung.mp4", konfig, max_bytes=_max_bytes(konfig))
        sekunden = time.monotonic() - start
    return {"entwurf": entwurf_id, "sekunden": round(sekunden, 1), "encoder": ergebnis["encoder"],
            "dauer_s": ergebnis["dauer_s"], "aufloesung": ergebnis["aufloesung"], "mb": ergebnis["mb"]}


# --- Final auf pve-big ---------------------------------------------------------------

def final_auftrag(con: sqlite3.Connection, konfig: Konfig, entwurf_id: int) -> Path:
    """Legt den Auftrag für pve-big an: Schnittliste mit Pfaden AUF pve-big (Speicher-Wurzel) plus Musik.

    Moment-Dateien liegen auf dem Mini evtl. als Material-Kopie -> für pve-big auf den Originalpfad im Speicher
    umschreiben. Die Musik wird in den Speicher kopiert (klein)."""
    zeile = _zeile(con, entwurf_id)
    liste = json.loads(Path(zeile["schnittliste"]).read_text(encoding="utf-8"))
    material_wurzel = Path(str(konfig.wert("material.ordner", "/var/lib/clip-pipeline/material")))
    for s in liste["segmente"]:
        datei = Path(s["datei"])
        if datei.is_relative_to(material_wurzel):
            s["datei"] = datei.relative_to(material_wurzel).as_posix()
            kopie = con.execute("SELECT art, von_s FROM material WHERE quelle = ?", (s["datei"],)).fetchone()
            if kopie and kopie["art"] == "video_ende":  # gekürzte Kopie -> Zeiten auf das Original umrechnen
                von = float(kopie["von_s"] or 0)
                for feld in ("quelle_start_s", "quelle_ende_s", "quelle_dauer_s"):
                    s[feld] = round(s[feld] + von, 3)
                s["muss"] = [round(s["muss"][0] + von, 3), round(s["muss"][1] + von, 3)]
        elif datei.is_relative_to(konfig.wurzel):
            s["datei"] = konfig.relativ(datei)
        else:
            raise MedienFehler(f"{datei} liegt weder im Speicher noch in der Material-Kopie")
    auftraege = konfig.wurzel / str(konfig.wert("regie.auftraege", "regie/auftraege"))
    auftraege.mkdir(parents=True, exist_ok=True)
    if m := liste.get("musik"):
        ziel_musik = konfig.wurzel / "regie" / "musik" / m["datei"]
        ziel_musik.parent.mkdir(parents=True, exist_ok=True)
        if not ziel_musik.is_file():
            shutil.copy2(musik.ordner(konfig) / m["datei"], ziel_musik)
    datei = auftraege / f"{liste['name']}.json"
    datei.write_text(json.dumps(liste, ensure_ascii=False, indent=2), encoding="utf-8")
    return datei


def _im_speicher(konfig: Konfig, relativ: str) -> Path:
    """Relativer Pfad, der nach dem Auflösen sicher in der Speicher-Wurzel bleibt."""
    wurzel = konfig.wurzel.resolve()
    if not relativ or relativ.startswith(("/", "\\")) or ".." in Path(relativ).parts:
        raise MedienFehler(f"Pfad im Auftrag nicht erlaubt: {relativ!r}")
    pfad = (wurzel / relativ).resolve()
    if not pfad.is_relative_to(wurzel):
        raise MedienFehler(f"Pfad im Auftrag zeigt aus dem Speicher: {relativ!r}")
    return pfad


def fuehre_final_aus(konfig: Konfig, auftrag: Path) -> dict:
    """Läuft AUF pve-big (clip-big-steuer final <name>). Der Auftrag liegt auf der Freigabe – also wie fremde
    Eingabe behandeln: Schema + fachliche Prüfung, Name passend zur Datei, alle Pfade innerhalb des Speichers."""
    from . import regie
    from .verarbeitung import SESSION_ID

    liste = json.loads(auftrag.read_text(encoding="utf-8"))
    if fehler := regie.pruefe_liste(liste):  # prüft u. a. Übergangs-Arten (feste Liste) und Musik-Pegel 0..1
        raise MedienFehler("Auftrag ungültig: " + "; ".join(fehler[:3]))
    if liste["name"] != auftrag.stem or not SESSION_ID.fullmatch(liste["name"]):
        raise MedienFehler("Auftrag: Name passt nicht zur Datei")
    for s in liste["segmente"]:
        s["datei"] = str(_im_speicher(konfig, s["datei"]))
    if m := liste.get("musik"):
        if Path(m["datei"]).name != m["datei"]:
            raise MedienFehler(f"Musik-Dateiname nicht erlaubt: {m['datei']!r}")
    konfig.daten.setdefault("musik", {})["ordner"] = str(konfig.wurzel / "regie" / "musik")
    ziel = konfig.wurzel / "regie" / "final" / f"{liste['name']}.mp4"
    return rendere(liste, ziel, konfig, final=True)


def nvenc_verfuegbar() -> bool:
    try:
        text = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, check=False).stdout
    except OSError:
        return False
    return "h264_nvenc" in text


def final_auf_big(con: sqlite3.Connection, konfig: Konfig, entwurf_id: int) -> dict:
    """Ganzer Final-Weg vom Mini aus: Auftrag anlegen, pve-big (einmal) wecken, dort mit NVENC rendern,
    danach sofort herunterfahren. Weckt nur, wenn das Herunterfahren gesichert ist (big.wach_halten)."""
    from . import big

    if konfig.getrennt:
        # E19: Auftrag und neue Clips lägen im Puffer, pve-big rendert aber aus seinem Speicher (dem Lager) –
        # dort kämen sie nie an. Also gar nicht erst wecken (Ziel 2: tagsüber weckt nur noch der Abgleich).
        raise KonfigFehler("render-entwurf --final geht im Puffer-Betrieb ([lager].wurzel gesetzt) noch nicht: "
                           "pve-big sieht nur das Lager, Auftrag und Clips liegen im Puffer. pve-big wurde nicht "
                           f"geweckt. Den Entwurf auf dem Mini rendern: pipeline render-entwurf {entwurf_id}")
    minuten = float(konfig.wert("regie.final_halten_min", 60))
    with big.wach_halten(konfig, "final", f"Final-Render Entwurf {entwurf_id}", minuten=minuten):
        auftrag = final_auftrag(con, konfig, entwurf_id)
        antwort = big._fern(konfig, f"final {auftrag.stem}", timeout=minuten * 60)
    zeilen = [z for z in antwort.strip().splitlines() if z.strip()]
    ergebnis = json.loads(zeilen[-1]) if zeilen else {}
    ziel = konfig.relativ(konfig.wurzel / "regie" / "final" / f"{auftrag.stem}.mp4")
    con.execute("UPDATE entwuerfe SET final_datei = ? WHERE id = ?", (ziel, entwurf_id))
    return {"entwurf": entwurf_id, "final": ziel, **ergebnis}
