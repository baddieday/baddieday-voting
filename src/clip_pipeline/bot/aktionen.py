"""Was ein Button-Klick in der Datenbank auslöst. Ohne Telegram testbar.

Callback-Daten (Telegram erlaubt max. 64 Byte):
  f:<clip>   freigeben        v:<clip>   verwerfen        u:<clip>  rückgängig
  b:<battle>:a|b|s  Battle entscheiden                     n:0       nächstes Battle
  p:<clip>   Upload-Paket     y:/t:/c:<clip>  YouTube / TikTok / clip-battle.de erledigt
  hf:<highlight> / hv:<highlight>  Highlight-Video freigeben / verwerfen
  hu:<highlight>  Highlight-Video hochgeladen (danach keine Erinnerung mehr)

Lernschleife „Publikum“ (Spec §10.4, letzter Punkt): Das Häkchen (y:/t:) und /link legen zusätzlich den Post an
(Tabelle posts, publikum.post_anlegen) – für die Plattformen aus [publikum].plattformen, nie für clip-battle.de.
Häkchen, Link und Post landen in EINER Transaktion: entweder alles oder nichts (siehe plattform_erledigt).
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .. import db, elo, publikum
from ..konfig import KonfigFehler
from ..zeit import aus_iso, im_zeitfenster, iso, jetzt, spielabend

log = logging.getLogger("clip-bot")

Knoepfe = list[list[tuple[str, str]]]


@dataclass
class Antwort:
    hinweis: str  # kurze Bestätigung am Button
    neuer_status: str | None = None  # für den Nachrichtentext
    knoepfe: Knoepfe | None = None  # None = Knöpfe unverändert lassen


PLATTFORM_KUERZEL = {"y": "youtube", "t": "tiktok", "c": "clipbattle"}
PLATTFORM_NAMEN = {"youtube": "YouTube Shorts", "tiktok": "TikTok", "clipbattle": "clip-battle.de"}


def parse(daten: str) -> tuple[str, int, str]:
    teile = (daten or "").split(":")
    if len(teile) < 2 or teile[0] not in ("f", "v", "u", "b", "n", "p", "hf", "hv", "hu", *PLATTFORM_KUERZEL):
        raise ValueError(f"Unbekannte Callback-Daten {daten!r}")
    return teile[0], int(teile[1]), teile[2] if len(teile) > 2 else ""


def knoepfe_neu(clip_id: int) -> Knoepfe:
    return [[("✅ Freigeben", f"f:{clip_id}"), ("🗑️ Verwerfen", f"v:{clip_id}")]]


def knoepfe_entschieden(clip_id: int, status: str = "verworfen") -> Knoepfe:
    if status == "freigegeben":
        return [[("↩️ Rückgängig", f"u:{clip_id}"), ("📦 Upload-Paket", f"p:{clip_id}")]]
    return [[("↩️ Rückgängig", f"u:{clip_id}")]]


def knoepfe_battle(battle_id: int) -> Knoepfe:
    return [[("⬅️ A", f"b:{battle_id}:a"), ("🤷 Egal", f"b:{battle_id}:s"), ("B ➡️", f"b:{battle_id}:b")]]


KNOEPFE_NAECHSTES = [[("⚔️ Nächstes Battle", "n:0")]]


# --- Freigabe -------------------------------------------------------------------

def entscheide(con: sqlite3.Connection, clip_id: int, nach: str) -> Antwort:
    """Freigeben oder Verwerfen. Ein Doppelklick ändert nichts ein zweites Mal."""
    erlaubt = tuple(s for s in ("vorbewertet", "gesendet", "freigegeben", "verworfen") if s != nach)
    if db.status_wechsel(con, clip_id, erlaubt, nach):
        return Antwort("✅ Freigegeben" if nach == "freigegeben" else "🗑️ Verworfen", nach, knoepfe_entschieden(clip_id, nach))
    zeile = db.clip(con, clip_id)
    if zeile is None:
        return Antwort("Clip nicht gefunden")
    return Antwort(f"Status ist schon „{zeile['status']}“", zeile["status"], None)


def rueckgaengig(con: sqlite3.Connection, clip_id: int) -> Antwort:
    if db.status_wechsel(con, clip_id, ("freigegeben", "verworfen"), "gesendet"):
        return Antwort("↩️ Zurückgeholt", "gesendet", knoepfe_neu(clip_id))
    zeile = db.clip(con, clip_id)
    return Antwort("Geht nicht mehr" if zeile else "Clip nicht gefunden", zeile["status"] if zeile else None, None)


# --- Ruhezeit -------------------------------------------------------------------

# Meldungen mit diesen Schlüssel-Anfängen (Morgenprüfung, Lager-Abgleich, neue Waffen-Nummern nach dem nächtlichen
# analyze – Stufe 2, Annahme S2-A4) warten die Ruhezeit ab
LEISE_MELDUNGEN = ("puffer:", "lager:", "merkmale:")


def ruhezeit(konfig, zeit: datetime | None = None) -> bool:
    """Liegt zeit (Ortszeit) in [telegram].leise_von … leise_bis? Über Mitternacht erlaubt (23:00–08:00).
    Leer oder von = bis: nie leise. Ein Tippfehler in der Konfig hält keine Nachricht auf (dann nie leise)."""
    von, bis = (str(konfig.wert(f"telegram.leise_{n}", "") or "").strip() for n in ("von", "bis"))
    try:
        return im_zeitfenster(zeit or jetzt(), von, bis, konfig.wert("zeit.zeitzone", "Europe/Berlin"))
    except ValueError:
        log.warning("[telegram] leise_von/leise_bis ungültig (%r/%r) – Ruhezeit aus", von, bis)
        return False


def faellige_meldungen(con: sqlite3.Connection, konfig, zeit: datetime | None = None) -> list[sqlite3.Row]:
    """Ungesendete Meldungen; in der Ruhezeit ohne die aus Puffer/Lager – die bleiben bis leise_bis liegen."""
    zeilen = con.execute("SELECT id, schluessel, text FROM meldungen WHERE gesendet IS NULL ORDER BY id").fetchall()
    if not ruhezeit(konfig, zeit):
        return zeilen
    return [z for z in zeilen if not z["schluessel"].startswith(LEISE_MELDUNGEN)]


# --- Outbox ---------------------------------------------------------------------

def outbox(con: sqlite3.Connection, grenze: int = 5) -> list[sqlite3.Row]:
    return con.execute(
        "SELECT * FROM clips WHERE status = 'vorbewertet' ORDER BY punkte DESC, id LIMIT ?", (grenze,)
    ).fetchall()


def highlight_outbox(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return con.execute("SELECT * FROM highlights WHERE status = 'neu' ORDER BY id").fetchall()


def highlight_gesendet(con: sqlite3.Connection, highlight_id: int, nachricht_id: int) -> None:
    con.execute(
        "UPDATE highlights SET status = 'gesendet', tg_nachricht_id = ? WHERE id = ? AND status = 'neu'",
        (nachricht_id, highlight_id),
    )


def knoepfe_highlight(highlight_id: int) -> Knoepfe:
    return [[("✅ Freigeben", f"hf:{highlight_id}"), ("🗑️ Verwerfen", f"hv:{highlight_id}")]]


def knoepfe_highlight_freigegeben(highlight_id: int) -> Knoepfe:
    return [[("✅ Hochgeladen", f"hu:{highlight_id}")]]


def als_gesendet(con: sqlite3.Connection, clip_id: int, nachricht_id: int, file_id: str | None) -> None:
    with db.transaktion(con):
        con.execute(
            "UPDATE clips SET tg_nachricht_id = ?, tg_file_id = COALESCE(?, tg_file_id), geaendert = ? WHERE id = ?",
            (nachricht_id, file_id, iso(jetzt()), clip_id),
        )
        db.status_wechsel(con, clip_id, ("vorbewertet",), "gesendet")


# --- Battles --------------------------------------------------------------------

def _kandidaten(con: sqlite3.Connection) -> list[sqlite3.Row]:
    platzhalter = ", ".join("?" for _ in db.BEWERTET)
    return con.execute(
        f"SELECT * FROM clips WHERE status IN ({platzhalter}) AND tg_file_id IS NOT NULL ORDER BY id",
        db.BEWERTET,
    ).fetchall()


def neues_battle(con: sqlite3.Connection) -> tuple[int, sqlite3.Row, sqlite3.Row] | None:
    """Paarung wie in cliphub: der Clip mit den wenigsten Battles gegen den ähnlichsten Gegner.

    Nur Clips mit Telegram-file_id: so klappen Battles auch, wenn der große Host schläft.
    """
    clips = _kandidaten(con)
    if len(clips) < 2:
        return None
    letzte = con.execute("SELECT clip_a, clip_b FROM battles ORDER BY id DESC LIMIT 1").fetchone()
    letztes_paar = {letzte["clip_a"], letzte["clip_b"]} if letzte else set()
    a = min(clips, key=lambda c: (c["battles"], c["id"]))
    gegner = [c for c in clips if c["id"] != a["id"] and {a["id"], c["id"]} != letztes_paar] or [
        c for c in clips if c["id"] != a["id"]
    ]
    b = min(gegner, key=lambda c: (abs(c["elo"] - a["elo"]), c["battles"], c["id"]))
    cursor = con.execute(
        "INSERT INTO battles (clip_a, clip_b, erstellt) VALUES (?, ?, ?)", (a["id"], b["id"], iso(jetzt()))
    )
    return int(cursor.lastrowid), a, b


def merke_battle_nachricht(con: sqlite3.Connection, battle_id: int, nachricht_id: int) -> None:
    con.execute("UPDATE battles SET tg_nachricht_id = ? WHERE id = ?", (nachricht_id, battle_id))


def entscheide_battle(con: sqlite3.Connection, battle_id: int, ergebnis: str, konfig) -> tuple[Antwort, sqlite3.Row | None]:
    if ergebnis not in ("a", "b", "s"):
        return Antwort("Unbekannte Wahl"), None
    with db.transaktion(con):
        battle = con.execute("SELECT * FROM battles WHERE id = ?", (battle_id,)).fetchone()
        if battle is None:
            return Antwort("Battle nicht gefunden"), None
        if battle["ergebnis"] is not None:
            return Antwort("Schon entschieden"), battle
        a, b = db.clip(con, battle["clip_a"]), db.clip(con, battle["clip_b"])
        neu_a, neu_b = elo.battle(
            elo.Stand(a["elo"], a["elo_rd"], a["battles"]),
            elo.Stand(b["elo"], b["elo_rd"], b["battles"]),
            ergebnis,
            rd_faktor=float(konfig.wert("elo.unsicherheit_faktor", 0.94)),
            rd_min=float(konfig.wert("elo.unsicherheit_min", 75.0)),
        )
        zeit = iso(jetzt())
        for zeile, neu, sieg in ((a, neu_a, ergebnis == "a"), (b, neu_b, ergebnis == "b")):
            con.execute(
                """UPDATE clips SET elo = ?, elo_rd = ?, battles = ?,
                                    siege = siege + ?, niederlagen = niederlagen + ?, geaendert = ?
                    WHERE id = ?""",
                (neu.elo, neu.rd, neu.battles, int(sieg), int(ergebnis != "s" and not sieg), zeit, zeile["id"]),
            )
        con.execute(
            """UPDATE battles SET ergebnis = ?, elo_a_vorher = ?, elo_b_vorher = ?, elo_a_nachher = ?,
                                  elo_b_nachher = ?, entschieden = ?
                WHERE id = ? AND ergebnis IS NULL""",
            (ergebnis, a["elo"], b["elo"], neu_a.elo, neu_b.elo, zeit, battle_id),
        )
        battle = con.execute("SELECT * FROM battles WHERE id = ?", (battle_id,)).fetchone()
    hinweis = {"a": "A gewinnt", "b": "B gewinnt", "s": "Übersprungen"}[ergebnis]
    return Antwort(hinweis, None, KNOEPFE_NAECHSTES), battle


# --- Veröffentlichung -----------------------------------------------------------

def plattformen(konfig) -> tuple[list[str], list[str]]:
    return list(konfig.wert("veroeffentlichung.pflicht", ["youtube", "tiktok"])), list(
        konfig.wert("veroeffentlichung.zusatz", [])
    )


def upload_stand(con: sqlite3.Connection, clip_id: int, konfig) -> dict[str, bool]:
    """Legt fehlende Plattform-Einträge an und gibt {plattform: erledigt?} zurück."""
    pflicht, zusatz = plattformen(konfig)
    for p in pflicht + zusatz:
        con.execute("INSERT OR IGNORE INTO veroeffentlichungen (clip_id, plattform) VALUES (?, ?)", (clip_id, p))
    zeilen = con.execute("SELECT plattform, erledigt FROM veroeffentlichungen WHERE clip_id = ?", (clip_id,)).fetchall()
    stand = {z["plattform"]: z["erledigt"] is not None for z in zeilen}
    return {p: stand.get(p, False) for p in pflicht + zusatz}


def knoepfe_upload(clip_id: int, stand: dict[str, bool]) -> Knoepfe:
    kuerzel = {v: k for k, v in PLATTFORM_KUERZEL.items()}
    offen = [(f"✅ {PLATTFORM_NAMEN.get(p, p)} erledigt", f"{kuerzel[p]}:{clip_id}") for p, fertig in stand.items()
             if not fertig and p in kuerzel]
    return [[k] for k in offen]


# Telegram zeigt die Antwort auf einen Knopf (answerCallbackQuery) nur mit höchstens 200 Zeichen an
HINWEIS_MAX = 200
# So viel vom Fehlergrund kommt in die Meldung an dich – lässt neben „⚠️ YouTube Shorts nicht abgehakt …“ (rund
# 65 Zeichen) sicher Platz unter HINWEIS_MAX; den ganzen Grund samt Traceback gibt es im Log
GRUND_MAX = 100
# Fehler, mit denen die Lernschleife einen Post ablehnt (publikum.clip_post_daten, post_anlegen, link_nachtragen):
# kaputtes JSON in clips.merkmale oder Dauer ≤ 0 (ValueError), Clip oder Post fehlt (KeyError, ValueError),
# [publikum] unvollständig (KonfigFehler). Sie werden zu einer Meldung an dich. Datenbankfehler (sqlite3.Error)
# gehören absichtlich nicht dazu – die fliegen wie bisher weiter (bot.app.bei_fehler loggt sie).
POST_FEHLER = (ValueError, KeyError, KonfigFehler)


def plattform_erledigt(con: sqlite3.Connection, clip_id: int, plattform: str, konfig, *,
                       zeit: datetime | None = None) -> tuple[Antwort, dict[str, bool] | None]:
    """Hakt eine Plattform ab. Sind alle Pflicht-Plattformen erledigt, gilt der Clip als veröffentlicht.
    Für Plattformen aus [publikum].plattformen entsteht dabei auch der Post der Lernschleife (_post_anlegen).

    zeit: Zeitpunkt des Häkchens (None = jetzt; Tests setzen feste Zeiten). Rückgabe (Antwort, {plattform:
    erledigt?}), z. B. (Antwort("TikTok ✅"), {"youtube": False, "tiktok": True, "clipbattle": False}).

    Alles in EINER Transaktion: Lässt sich der Post nicht anlegen (POST_FEHLER), gibt es auch kein Häkchen – lieber
    ein sichtbarer Fehler als ein TikTok-Post, der still in der Lernschleife fehlt. Rückgabe dann (Antwort
    „⚠️ TikTok nicht abgehakt – …: <Grund>“, None); ist der Grund behoben, geht derselbe Knopf nochmal.
    Datenbankfehler fliegen weiter, auch dann ist nichts halb gespeichert."""
    try:
        with db.transaktion(con):
            antwort, stand = _abhaken(con, clip_id, plattform, konfig, zeit or jetzt())
    except POST_FEHLER as fehler:
        return _nicht_abgehakt(clip_id, plattform, fehler), None
    return antwort, stand


def _abhaken(con: sqlite3.Connection, clip_id: int, plattform: str, konfig,
             zeit: datetime) -> tuple[Antwort, dict[str, bool]]:
    """Rumpf von plattform_erledigt – OHNE eigene Transaktion, weil db.transaktion (BEGIN IMMEDIATE) sich nicht
    verschachteln lässt. So legen plattform_erledigt und link_speichern je EINE Transaktion darum.

    Schritte: Plattform-Einträge anlegen, Häkchen setzen (ein Doppelklick behält den ersten Zeitpunkt), Post für die
    Lernschleife anlegen, und sind alle Pflicht-Plattformen erledigt: Status „veröffentlicht“."""
    upload_stand(con, clip_id, konfig)
    con.execute(
        "UPDATE veroeffentlichungen SET erledigt = COALESCE(erledigt, ?) WHERE clip_id = ? AND plattform = ?",
        (iso(zeit), clip_id, plattform),
    )
    _post_anlegen(con, clip_id, plattform, konfig, zeit)
    stand = upload_stand(con, clip_id, konfig)
    pflicht, _ = plattformen(konfig)
    fertig = all(stand[p] for p in pflicht)
    if fertig:
        db.status_wechsel(con, clip_id, ("freigegeben",), "veroeffentlicht")
    hinweis = f"{PLATTFORM_NAMEN.get(plattform, plattform)} ✅" + (" – veröffentlicht!" if fertig else "")
    return Antwort(hinweis, None, knoepfe_upload(clip_id, stand)), stand


def _post_anlegen(con: sqlite3.Connection, clip_id: int, plattform: str, konfig, zeit: datetime) -> None:
    """Legt den Post der Lernschleife an (Spec §10.4): art "clip", Dauer, Rezept und Merkmale nur aus der Datenbank
    (publikum.clip_post_daten – kein Dateizugriff, der Bot bleibt nie an einem hängenden NFS oder schlafenden
    pve-big stehen). Nur für Plattformen aus [publikum].plattformen (Standard ["tiktok"]); clip-battle.de ist nie
    eine (dort wird eingereicht, nicht geschaut). Gibt es den Post schon (Doppelklick, /link nach dem Häkchen),
    bleibt er, wie er ist (publikum.post_anlegen). Läuft in der Transaktion des Aufrufers.

    gepostet_utc = Zeitpunkt des ERSTEN Häkchens dieser Plattform (veroeffentlichungen.erledigt), nicht „jetzt“:
    War ein Clip schon vor der Lernschleife abgehakt, legt ein späteres /link den Post sonst mit falschem Alter an –
    und am Alter hängt, welche Messung der Score nimmt. Beispiel: TikTok am 15.09. abgehakt, /link am 25.09. →
    gepostet_utc 15.09. Hat die Plattform keine Checklisten-Zeile (nicht in [veroeffentlichung].pflicht/zusatz),
    gilt zeit, der Zeitpunkt dieses Häkchens bzw. /link."""
    if plattform not in publikum.post_plattformen(konfig):
        return
    zeile = con.execute("SELECT erledigt FROM veroeffentlichungen WHERE clip_id = ? AND plattform = ?",
                        (clip_id, plattform)).fetchone()
    publikum.post_anlegen(con, art="clip", ziel_id=clip_id, plattform=plattform,
                          daten=publikum.clip_post_daten(con, konfig, clip_id),
                          zeit=aus_iso(zeile["erledigt"]) if zeile else zeit)


def _nicht_abgehakt(clip_id: int, plattform: str, fehler: Exception) -> Antwort:
    """Antwort, wenn Häkchen und Post zurückgenommen wurden: kurz genug für eine Knopf-Antwort (HINWEIS_MAX),
    Einzelheiten samt Traceback ins Log (dort stehen nur Datenbank-Inhalte, keine Secrets)."""
    log.error("Clip #%s: Post für %s nicht angelegt – Häkchen zurückgenommen", clip_id, plattform, exc_info=fehler)
    # args[0] statt str(): str(KeyError("x")) wäre "'x'" mit Anführungszeichen
    grund = str(fehler.args[0]) if fehler.args else type(fehler).__name__
    hinweis = f"⚠️ {PLATTFORM_NAMEN.get(plattform, plattform)} nicht abgehakt – Post fürs Lernen ging nicht: "
    return Antwort((hinweis + grund[:GRUND_MAX])[:HINWEIS_MAX])


PLATTFORM_DOMAINS = {
    "youtube": ("youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"),
    "tiktok": ("tiktok.com", "www.tiktok.com", "vm.tiktok.com", "m.tiktok.com"),
    "clipbattle": ("clip-battle.de", "www.clip-battle.de"),
}


def plattform_aus_url(url: str) -> str | None:
    from urllib.parse import urlsplit

    teile = urlsplit(url.strip())
    if teile.scheme != "https":
        return None
    return next((p for p, domains in PLATTFORM_DOMAINS.items() if (teile.hostname or "").lower() in domains), None)


def link_speichern(con: sqlite3.Connection, clip_id: int, url: str, konfig, *,
                   zeit: datetime | None = None) -> tuple[Antwort, dict[str, bool] | None]:
    """/link <clip> <url>: Plattform am Link erkennen, abhaken und den Link merken (für clip-battle.de).

    Hat die Plattform einen Post (TikTok, siehe _post_anlegen), bekommt er url und video_id (publikum.link_nachtragen);
    gab es noch kein Häkchen, entsteht der Post hier. Ein zweiter /link ersetzt einen falschen Link – in der
    Checkliste und im Post. zeit: Zeitpunkt (None = jetzt), gilt nur, wenn die Plattform noch nicht abgehakt war.

    Häkchen, Link und Post in EINER Transaktion, Fehler wie bei plattform_erledigt → („⚠️ … nicht abgehakt …“, None),
    nichts gespeichert. Unbekannter Link oder nicht freigegebener Clip → (Hinweis, None) wie bisher."""
    plattform = plattform_aus_url(url)
    if plattform is None:
        return Antwort("Unbekannter Link – erwartet https://… von YouTube, TikTok oder clip-battle.de"), None
    zeile = db.clip(con, clip_id)
    if zeile is None or zeile["status"] not in db.BEWERTET:
        return Antwort(f"Clip #{clip_id} ist nicht freigegeben"), None
    try:
        with db.transaktion(con):
            antwort, stand = _abhaken(con, clip_id, plattform, konfig, zeit or jetzt())
            con.execute("UPDATE veroeffentlichungen SET url = ? WHERE clip_id = ? AND plattform = ?",
                        (url.strip(), clip_id, plattform))
            # post_zu statt eigenem SQL: die eine Stelle, an der beide Bots nach einem Post sehen
            if (post := publikum.post_zu(con, "clip", clip_id, plattform)) is not None:
                publikum.link_nachtragen(con, post["id"], url)
    except POST_FEHLER as fehler:
        return _nicht_abgehakt(clip_id, plattform, fehler), None
    return antwort, stand


def ist_highlight_clip(clip: sqlite3.Row, konfig) -> bool:
    """Lohnt ein eigener Upload? Ab [veroeffentlichung].highlight_ab_kills Kills am Stück (Standard 3 = Triple Kill)
    oder mit Victory Royale (highlight_victory_royale). Alle anderen Freigaben dienen Bewertung, Lernen und dem
    Highlight-Video – hochladen geht trotzdem (📦), nur erinnert wird daran nicht (Entscheidung 25.09.)."""
    ab = int(konfig.wert("veroeffentlichung.highlight_ab_kills", 3))
    mit_vr = bool(konfig.wert("veroeffentlichung.highlight_victory_royale", True))
    return (clip["max_gruppe"] or 0) >= ab or (mit_vr and bool(clip["victory_royale"]))


def offene_uploads(con: sqlite3.Connection, konfig) -> list[tuple[sqlite3.Row, list[str]]]:
    """Freigegebene Highlight-Clips (ist_highlight_clip), denen noch eine Pflicht-Plattform fehlt."""
    pflicht, _ = plattformen(konfig)
    ergebnis = []
    for clip in con.execute("SELECT * FROM clips WHERE status = 'freigegeben' ORDER BY id").fetchall():
        if not ist_highlight_clip(clip, konfig):
            continue
        erledigt = {
            z["plattform"] for z in con.execute(
                "SELECT plattform FROM veroeffentlichungen WHERE clip_id = ? AND erledigt IS NOT NULL", (clip["id"],)
            )
        }
        fehlt = [p for p in pflicht if p not in erledigt]
        if fehlt:
            ergebnis.append((clip, fehlt))
    return ergebnis


def offene_highlight_videos(con: sqlite3.Connection) -> list[sqlite3.Row]:
    """Freigegebene Highlight-Videos ohne Häkchen „✅ Hochgeladen“ (highlight.hochgeladen)."""
    return con.execute(
        "SELECT * FROM highlights WHERE status = 'freigegeben' AND hochgeladen IS NULL ORDER BY id"
    ).fetchall()


# --- Rangliste ------------------------------------------------------------------

def saison(heute: date, konfig) -> tuple[date, date]:
    beginn = date.fromisoformat(str(konfig.wert("saison.start", "2026-09-21")))
    tage = int(konfig.wert("saison.tage", 14))
    nummer = (heute - beginn).days // tage
    start = beginn + timedelta(days=nummer * tage)
    return start, start + timedelta(days=tage - 1)


def rangliste(con: sqlite3.Connection, konfig, heute: date | None = None, grenze: int = 10) -> tuple[list, tuple[date, date]]:
    zone = konfig.wert("zeit.zeitzone", "Europe/Berlin")
    wechsel = int(konfig.wert("zeit.tageswechsel_stunde", 6))
    heute = heute or spielabend(jetzt(), zone, wechsel)
    von, bis = saison(heute, konfig)
    zeilen = [
        z for z in _kandidaten(con) if von <= spielabend(aus_iso(z["start_utc"]), zone, wechsel) <= bis
    ]
    zeilen.sort(key=lambda z: (-elo.ranglisten_wert(z["elo"], z["elo_rd"]), z["id"]))
    return zeilen[:grenze], (von, bis)
