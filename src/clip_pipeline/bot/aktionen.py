"""Was ein Button-Klick in der Datenbank auslöst. Ohne Telegram testbar.

Callback-Daten (Telegram erlaubt max. 64 Byte):
  f:<clip>   freigeben        v:<clip>   verwerfen        u:<clip>  rückgängig
  b:<battle>:a|b|s  Battle entscheiden                     n:0       nächstes Battle
  p:<clip>   Upload-Paket     y:/t:/c:<clip>  YouTube / TikTok / clip-battle.de erledigt
  hf:<highlight> / hv:<highlight>  Highlight-Video freigeben / verwerfen
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .. import db, elo
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
    if len(teile) < 2 or teile[0] not in ("f", "v", "u", "b", "n", "p", "hf", "hv", *PLATTFORM_KUERZEL):
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

# Meldungen mit diesen Schlüssel-Anfängen (Morgenprüfung, Lager-Abgleich) warten die Ruhezeit ab
LEISE_MELDUNGEN = ("puffer:", "lager:")


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


def plattform_erledigt(con: sqlite3.Connection, clip_id: int, plattform: str, konfig) -> tuple[Antwort, dict[str, bool]]:
    """Hakt eine Plattform ab. Sind alle Pflicht-Plattformen erledigt, gilt der Clip als veröffentlicht."""
    with db.transaktion(con):
        upload_stand(con, clip_id, konfig)
        con.execute(
            "UPDATE veroeffentlichungen SET erledigt = COALESCE(erledigt, ?) WHERE clip_id = ? AND plattform = ?",
            (iso(jetzt()), clip_id, plattform),
        )
        stand = upload_stand(con, clip_id, konfig)
        pflicht, _ = plattformen(konfig)
        fertig = all(stand[p] for p in pflicht)
        if fertig:
            db.status_wechsel(con, clip_id, ("freigegeben",), "veroeffentlicht")
    hinweis = f"{PLATTFORM_NAMEN.get(plattform, plattform)} ✅" + (" – veröffentlicht!" if fertig else "")
    return Antwort(hinweis, None, knoepfe_upload(clip_id, stand)), stand


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


def link_speichern(con: sqlite3.Connection, clip_id: int, url: str, konfig) -> tuple[Antwort, dict[str, bool] | None]:
    """/link <clip> <url>: Plattform am Link erkennen, abhaken und den Link merken (für clip-battle.de)."""
    plattform = plattform_aus_url(url)
    if plattform is None:
        return Antwort("Unbekannter Link – erwartet https://… von YouTube, TikTok oder clip-battle.de"), None
    zeile = db.clip(con, clip_id)
    if zeile is None or zeile["status"] not in db.BEWERTET:
        return Antwort(f"Clip #{clip_id} ist nicht freigegeben"), None
    antwort, stand = plattform_erledigt(con, clip_id, plattform, konfig)
    con.execute("UPDATE veroeffentlichungen SET url = ? WHERE clip_id = ? AND plattform = ?", (url.strip(), clip_id, plattform))
    return antwort, stand


def offene_uploads(con: sqlite3.Connection, konfig) -> list[tuple[sqlite3.Row, list[str]]]:
    """Freigegebene Clips, denen noch eine Pflicht-Plattform fehlt – das darf nicht liegen bleiben."""
    pflicht, _ = plattformen(konfig)
    ergebnis = []
    for clip in con.execute("SELECT * FROM clips WHERE status = 'freigegeben' ORDER BY id").fetchall():
        erledigt = {
            z["plattform"] for z in con.execute(
                "SELECT plattform FROM veroeffentlichungen WHERE clip_id = ? AND erledigt IS NOT NULL", (clip["id"],)
            )
        }
        fehlt = [p for p in pflicht if p not in erledigt]
        if fehlt:
            ergebnis.append((clip, fehlt))
    return ergebnis


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
