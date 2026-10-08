"""Freund anlegen und prüfen (Mehrbenutzer M1, Stufe 1, Schritt 7): `pipeline benutzer pruefen|einrichten`.

Läuft nur in der Instanz eines Freundes (CLIP_INSTANZ) – gestartet von den Vorlagen clip-freund-pruefen@ und
clip-freund-einrichten@ (deploy/benutzer) mit DERSELBEN Sandbox wie seine übrigen Dienste. Florian startet beides über
deploy/benutzer/benutzer-anlegen.sh bzw. benutzer-pruefen.sh (als root im CT).

pruefen – sieht nur nach: lexists, stat und os.access, dazu die NAMEN in vier lokalen Ordnern (nie Inhalte, nie das
Lager, kein Netz). Im Namensraum des Freundes dürfen Florians Bereiche (Datenbank, Claude-Anmeldung, Schlüssel für
pve-big, Puffer, Clips, Lager, .env, lokal.toml, Lern-Bot-Stand, Home) und die Ordner anderer Freunde nicht da oder
wenigstens nicht erreichbar sein. Die eigenen Ordner müssen beschreibbar sein – der Instanz-Ordner selbst, instanz.toml,
.env und die Marke nicht (sie gehören root). Die Sperrdatei muss da und nur lesbar sein; (st_dev, st_ino) gehen ins
Ergebnis, damit benutzer-pruefen.sh draußen vergleichen kann, ob es wirklich Florians Datei ist. Ob claude da ist und
ob es einen eigenen Claude-Zugang gibt, ist nur ein Hinweis (claude installiert nur Florian selbst, nie automatisch).

einrichten – wiederholbar, Fertiges wird übersprungen: Datenbank anlegen; Whisper-Modell einmal in den eigenen Cache
(HF_HOME = I/cache/huggingface, so wie die Pipeline es sonst beim ersten Gebrauch lädt); Musik seiner Genres wie im
einfachen Modus (bis 16 freie Titel, unter der gemeinsamen Sperre); danach pruefen. Whisper und Musik sind Zugabe:
scheitern sie, steht es im Ergebnis, eingerichtet ist die Instanz trotzdem (Szenen ohne Sprache, Musik kommt tagsüber
von selbst über musik.nachschub).

instanz_toml – läuft bei FLORIAN (ohne Instanz, als pipeline): die instanz.toml eines neuen Freundes aus Florians
wirksamer Konfig – nur die gemeinsame Sperre, die Rechnerwerte und die Waffen-Nummern (M15).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import stat
import tomllib
from datetime import date
from pathlib import Path
from typing import Any

from . import claude_aufruf, db, sperre
from .konfig import (INSTANZ_MARKE, INSTANZ_NAME, INSTANZ_WARTEN_S, STANDARD_KONFIG, Konfig, KonfigFehler,
                     claude_verboten, liegt_in)

log = logging.getLogger("pipeline")

# Von hier aus wird nachgesehen – Tests setzen eine Scheinwurzel (die Pfade unten gelten darunter)
WURZEL = Path("/")
# Florians Bereiche: im Namensraum eines Freundes nicht da oder nicht erreichbar (nur lstat und access)
FLORIAN_PFADE = (
    ("/var/lib/clip-pipeline/pipeline.db", "Florians Datenbank"),
    ("/var/lib/clip-pipeline/claude", "Florians Claude-Anmeldung"),
    ("/var/lib/clip-pipeline/.ssh/pve-big", "Florians Schlüssel für pve-big"),
    ("/var/lib/clip-pipeline/publikum-oauth.json", "Florians TikTok-Zugang"),
    ("/srv/puffer", "Florians Puffer"),
    ("/srv/clips", "Florians Clips"),
    ("/srv/big", "Florians Lager auf pve-big"),
    ("/opt/clip-pipeline/.env", "Florians Zugänge"),
    ("/opt/clip-pipeline/config/lokal.toml", "Florians Rechner-Konfig"),
    ("/opt/clip-regie", "Florians Lern-Bot-Stand"),
    ("/home/pipeline", "Florians Home (Claude-Anmeldung, SSH-Schlüssel)"),
)
# Lokale Ordner, in denen der Namensraum nur genau diese Namen zeigen darf (gelesen werden nur die Namen)
NUR_DAS = (("/var/lib/clip-pipeline", {"pipeline.lock"}), ("/opt/clip-pipeline/config", {"pipeline.toml"}),
           ("/srv", set()))
EIGENE_ORDNER = ("db", "daten", "regie", "musik", "material", "sfx", "cache")
NUR_LESEN = ("instanz.toml", ".env", INSTANZ_MARKE)   # gehören root – der Freund liest sie nur
MUSIK_ZIEL = 16                                      # = musik.nachschub im Abend-Lauf (2 × regeln.MUSIK_ROTATION)


def _kann(pfad: Path, modus: int) -> bool:
    """os.access – eigene Funktion, damit Tests Rechte wie für einen normalen Benutzer nachstellen können (als root
    wäre sonst alles erlaubt)."""
    return os.access(pfad, modus)


def _erreichbar(pfad: Path) -> bool:
    """Da UND für diesen Prozess erreichbar (lesen, schreiben oder – bei Ordnern – betreten)? Nur lstat und access.
    Was da ist, aber gesperrt (InaccessiblePaths, Rechte 000) oder für diesen Benutzer zu, zählt nicht."""
    if not os.path.lexists(pfad):
        return False
    if _kann(pfad, os.R_OK) or _kann(pfad, os.W_OK):
        return True
    return os.path.isdir(pfad) and _kann(pfad, os.X_OK)


def _namen(ordner: Path) -> list[str]:
    """Die Namen in einem lokalen Ordner – leer, wenn es ihn nicht gibt oder er für diesen Benutzer zu ist."""
    try:
        return sorted(os.listdir(ordner))
    except OSError:
        return []


def _unter(wurzel: Path, pfad: str) -> Path:
    return wurzel / pfad.lstrip("/")


def _instanz(konfig: Konfig) -> Path:
    if konfig.instanz is None:
        raise KonfigFehler("pipeline benutzer läuft nur in der Instanz eines Freundes (CLIP_INSTANZ)")
    return konfig.instanz


def pruefen(konfig: Konfig, wurzel: Path | None = None) -> dict:
    """Trennung im eigenen Namensraum prüfen (nur nachsehen). Ergebnis: {"ok", "name", "befunde": [{"pfad", "grund"}],
    "hinweise": [...], "sperre": {"pfad", "dev", "ino"}} – ok genau dann, wenn es keinen Befund gibt."""
    wurzel = Path(wurzel or WURZEL)
    inst = _instanz(konfig)
    befunde: list[dict[str, str]] = []
    hinweise: list[str] = []

    def befund(pfad: Path, grund: str) -> None:
        befunde.append({"pfad": str(pfad), "grund": grund})

    # 1. Florians Bereiche – im Namensraum nicht da oder nicht erreichbar
    for pfad, was in FLORIAN_PFADE:
        if _erreichbar(p := _unter(wurzel, pfad)):
            befund(p, f"{was} ist sichtbar")
    gemeldet = {b["pfad"] for b in befunde}
    for ordner, erlaubt in NUR_DAS:
        for name in _namen(_unter(wurzel, ordner)):
            p = _unter(wurzel, ordner) / name
            if name not in erlaubt and str(p) not in gemeldet and _erreichbar(p):
                befund(p, "gehört Florian und ist sichtbar")
    # 2. andere Freunde – im Namensraum gibt es nur den eigenen Ordner
    for name in _namen(inst.parent):
        if name != inst.name and INSTANZ_NAME.fullmatch(name):
            befund(inst.parent / name, "Ordner eines anderen Freundes ist sichtbar")
    # 3. eigene Ordner beschreibbar; was root gehört, nicht
    for name in EIGENE_ORDNER:
        p = inst / name
        if p.is_symlink() or not p.is_dir() or not _kann(p, os.W_OK | os.X_OK):
            befund(p, "eigener Ordner fehlt oder ist nicht beschreibbar")
    if _kann(inst, os.W_OK):
        befund(inst, "Instanz-Ordner ist beschreibbar – er gehört root (sonst ließen sich .env und instanz.toml "
                     "austauschen)")
    for name in NUR_LESEN:
        p = inst / name
        if not p.is_file():
            befund(p, "fehlt")
        elif _kann(p, os.W_OK):
            befund(p, "ist beschreibbar – gehört root, der Freund liest sie nur")
    # 4. die eine Rechen-Sperre: da, nur lesbar; dev/ino vergleicht benutzer-pruefen.sh mit Florians Datei
    datei = sperre.pfad(konfig)
    info: dict[str, Any] = {"pfad": str(datei)}
    try:
        st = os.stat(datei)
    except OSError:
        befund(datei, "Sperrdatei fehlt – ohne sie rechnet der Freund nie")
    else:
        info.update(dev=st.st_dev, ino=st.st_ino)
        if not stat.S_ISREG(st.st_mode):
            befund(datei, "Sperrdatei ist keine normale Datei")
        elif _kann(datei, os.W_OK):
            befund(datei, "Sperrdatei ist beschreibbar – sie gehört Florian, ein Freund liest sie nur")
        elif not _kann(datei, os.R_OK):
            befund(datei, "Sperrdatei ist nicht lesbar – ohne sie rechnet der Freund nie")
    # 5. Zugänge – nur ob sie da sind, nie die Werte
    if not os.environ.get("LEARN_BOT_TOKEN", "").strip():
        befund(inst / ".env", "LEARN_BOT_TOKEN fehlt – ohne eigenen Bot kommt kein Video an")
    if not str(konfig.wert("replay.ich", "") or "").strip():
        befund(inst / ".env", "CLIP_EPIC_ID fehlt – ohne Epic-Konto-ID erkennt die Pipeline die eigenen Kills nicht "
                              "sicher")
    if not os.environ.get("LEARN_BOT_ALLOWED_USER_ID", "").strip().isdigit():
        hinweise.append("Telegram-Zahl fehlt – der Bot startet erst mit ihr")
    hinweise.append(ki_hinweis(konfig))
    for name in ("HOME", "HF_HOME"):
        if not liegt_in(os.environ.get(name) or "/", inst):
            hinweise.append(f"{name} liegt nicht im eigenen Ordner – das setzen nur die Dienste clip-freund-*@")
    return {"ok": not befunde, "name": str(konfig.wert("instanz.name", inst.name)), "befunde": befunde,
            "hinweise": hinweise, "sperre": info}


def ki_hinweis(konfig: Konfig) -> str:
    """Ein Satz zur KI des Freundes – nur Hinweis, nie Befund. claude installiert Florian selbst (z. B. global per npm),
    nie automatisch; ohne eigenen Zugang bleibt die KI aus (M29/M30)."""
    eigen = str(konfig.wert("instanz.claude", "") or "").strip()
    programm = shutil.which(eigen) if eigen else shutil.which("claude", path=claude_aufruf.SUCHPFAD)
    if programm is None:
        return ("claude ist auf dem Mini nicht installiert – ohne gibt es für Freunde keine KI (Florian installiert es "
                "selbst, z. B. global per npm; nie automatisch)")
    if bereich := claude_verboten(programm):
        return f"claude liegt unter {bereich} – für Freunde nicht nutzbar (global installieren, z. B. /usr/local/bin)"
    if claude_aufruf.instanz_token(konfig) is None:
        return "claude ist da, aber kein eigener Claude-Zugang – die KI bleibt aus (Token aus `claude setup-token`)"
    return "eigener Claude-Zugang: ja"


# --- einrichten ---------------------------------------------------------------------------------------------------

def einrichten(konfig: Konfig, wurzel: Path | None = None) -> dict:
    """Datenbank, Whisper-Modell, Musik – jeweils nur, was fehlt – danach pruefen. Ergebnis wie pruefen, dazu
    "eingerichtet": {"datenbank", "whisper", "musik"} in Worten."""
    _instanz(konfig)
    da = konfig.datenbank.is_file()
    con = db.verbinde(konfig.datenbank)
    try:
        schritte = {"datenbank": "schon da" if da else "angelegt", "whisper": _whisper(konfig),
                    "musik": _musik(con, konfig)}
    finally:
        con.close()
    for was, wie in schritte.items():
        log.info("Einrichten – %s: %s", was, wie)
    return {**pruefen(konfig, wurzel), "eingerichtet": schritte}


def _whisper(konfig: Konfig) -> str:
    """Das Whisper-Modell der Konfig ([stimmung].modell) einmal in den eigenen Cache – wie es die Pipeline sonst beim
    ersten Gebrauch lädt (faster_whisper lädt über download_model in HF_HOME). Liegt es schon dort, wird nichts
    geladen. Nur mit HF_HOME im eigenen Ordner (setzt der Dienst) – sonst landete es im Home dessen, der aufruft."""
    from . import mikro

    inst = _instanz(konfig)
    if not liegt_in(os.environ.get("HF_HOME") or "/", inst):
        return "übersprungen – HF_HOME liegt nicht im eigenen Ordner (nur über clip-freund-einrichten@)"
    if not mikro.whisper_da():
        return "Whisper ist hier nicht installiert – die Szenen werden ohne Sprache gemessen"
    modell = str(konfig.wert("stimmung.modell", "small"))
    try:
        from faster_whisper.utils import download_model
    except Exception as e:  # noqa: BLE001 – kaputte Installation: Szenen ohne Sprache wie bei einem Ladefehler
        return f"Whisper lässt sich nicht laden ({type(e).__name__}) – die Szenen werden ohne Sprache gemessen"
    try:
        download_model(modell, local_files_only=True)
        return f"Modell {modell} schon da"
    except Exception:  # noqa: BLE001 – noch nicht im Cache: jetzt laden
        pass
    try:
        download_model(modell)
    except Exception as e:  # noqa: BLE001 – Netz o. Ä.: die Pipeline misst dann ohne Sprache (M37)
        log.warning("Whisper-Modell %s nicht geladen: %s", modell, e)
        return (f"Modell {modell} nicht geladen ({type(e).__name__}) – Szenen ohne Sprache, nächster Versuch beim "
                "nächsten Einrichten")
    return f"Modell {modell} geladen"


def _musik(con, konfig: Konfig) -> str:
    """Musik wie im einfachen Modus: die Genres aus einstellungen (DEINE_GENRES), bis MUSIK_ZIEL freie Titel – genau
    die Grenze, ab der musik.nachschub im Abend-Lauf (10–17 Uhr, höchstens einmal am Tag) von selbst nachlädt.
    Laden und Messen unter der gemeinsamen Sperre (wie im Abend-Lauf); der Tages-Merker des Nachschubs bleibt frei."""
    from . import einstellungen, musik  # musik braucht numpy

    k = einstellungen.anwenden(con, konfig)
    if einstellungen.experte(con, k):
        return "übersprungen – Experten-Modus (Musik von Hand)"
    genres = [g for g in k.wert("musik.genres_bevorzugt", []) or [] if g in musik.NCS_GENRES]
    frei = musik.frei_je_genre(con, genres)
    fehlen = MUSIK_ZIEL - sum(frei.values())
    if not genres or fehlen <= 0:
        return f"{sum(frei.values())} Songs der Genres schon da"
    try:
        with sperre.sperre(sperre.pfad(k), warten_s=float(k.wert("sperre.warten_s", INSTANZ_WARTEN_S)),
                           melde=log.info):
            neu = musik.ncs_genres_laden(con, k, sorted(genres, key=lambda g: frei[g]), anzahl=fehlen)
    except (sperre.Gesperrt, sperre.SperreFehler):
        return "später – gerade rechnet etwas anderes (die Musik kommt tagsüber von selbst)"
    except OSError as e:
        log.warning("Musik von NCS: %s", e)
        return f"NCS nicht erreichbar ({type(e).__name__}) – die Musik kommt tagsüber von selbst"
    return f"{len(neu)} Songs von NCS geladen"


# --- instanz.toml für einen neuen Freund (läuft bei Florian) ----------------------------------------------------------

def _toml_wert(wert: Any) -> str:
    if isinstance(wert, bool):
        return "true" if wert else "false"
    if isinstance(wert, (int, float)):
        return repr(wert)
    if isinstance(wert, str):
        return json.dumps(wert, ensure_ascii=False)   # ein JSON-String ist auch ein gültiger TOML-String
    if isinstance(wert, list):
        return "[" + ", ".join(_toml_wert(w) for w in wert) + "]"
    raise ValueError(f"Wert {wert!r} passt nicht in die instanz.toml")


def instanz_toml(konfig: Konfig, name: str) -> str:
    """Text der instanz.toml für den Freund `name` aus FLORIANS wirksamer Konfig (benutzer-anlegen.sh ruft das als
    pipeline auf): [sperre].datei = sperre.pfad (wie der Einzeiler in alles-aktualisieren.sh, M3) mit warten_s 900,
    [schnitt] encoder/vaapi_geraet und [merkmale.waffen] – nur Schlüssel, die die Repo-Konfig dort kennt (sonst lehnte
    lade_instanz die Datei ab). Keine Hosts, keine MAC, kein Lager, keine Zugänge. Läuft nur bei Florian."""
    if konfig.instanz is not None:
        raise KonfigFehler("instanz_toml liest Florians Konfig – nicht in der Instanz eines Freundes aufrufen")
    if not INSTANZ_NAME.fullmatch(name):
        raise KonfigFehler(f"Name {name!r} ungültig (2–27 Zeichen a-z, 0-9, -, vorn ein Buchstabe)")
    repo = tomllib.loads(STANDARD_KONFIG.read_text(encoding="utf-8"))
    zeilen = [f"# Konfig von {name} – geschrieben von benutzer-anlegen.sh am {date.today():%d.%m.%Y} aus Florians "
              "wirksamer Konfig.",
              "# Gehört root (root:clip-<name>, 0640): Der Freund liest sie, ändern kann er sie nicht. Erlaubt sind nur",
              "# [sperre], [schnitt], [zeit], [merkmale.waffen] und [instanz] (Vorlage: config/instanz.beispiel.toml).",
              "", "[sperre]", "# Die EINE Rechen-Sperre des Mini – Florians Datei, im Dienst nur lesbar eingebunden",
              f"datei = {_toml_wert(str(sperre.pfad(konfig)))}", f"warten_s = {INSTANZ_WARTEN_S}"]
    for abschnitt, kopf, erlaubt in (
            ("schnitt", "# Rechnerwerte wie bei Florian (sonst schnitte der Freund auf dem Prozessor)",
             ("encoder", "vaapi_geraet")),
            ("merkmale.waffen", "# Spielwissen wie bei Florian (GunType-Zahlen aus replay2json)", None)):
        bekannt = Konfig(repo, STANDARD_KONFIG).wert(abschnitt, {}) or {}
        werte = konfig.wert(abschnitt, {}) or {}
        schluessel = [s for s in (erlaubt or tuple(bekannt)) if s in bekannt and s in werte]
        if schluessel:
            zeilen += ["", f"[{abschnitt}]", kopf, *(f"{s} = {_toml_wert(werte[s])}" for s in schluessel)]
    text = "\n".join(zeilen) + "\n"
    tomllib.loads(text)   # wirft, falls doch etwas nicht passt – dann schreibt das Skript nichts
    return text
