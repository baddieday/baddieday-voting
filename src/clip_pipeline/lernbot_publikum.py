"""Lern-Bot: /publikum und die Ruhezeit für Publikums-Meldungen (Spec §12, §14 Stufe 1).

/publikum zeigt die letzten Posts mit Post-Nummer (für die Screenshots), Plattform, Alter, letzter Messung und
– sobald gesetzt – dem Publikums-Score mit seinen Teilen in Worten. So siehst du ohne Datenbank, was die
Lernschleife gerade weiß. Die letzte Zeile zählt die Claude-Aufrufe der Woche (Spec §12; /lernstand zeigt sie
ab Stufe 3 mit der Rezept-Tabelle).

Ruhezeit: Meldungen mit Schlüsseln `publikum:…` (und später `woche:…`, Stufe 5) verschickt der Lern-Bot nicht
in [telegram].leise_von … leise_bis; sie bleiben liegen und kommen danach. Die Clip-Bot-Liste
`bot.aktionen.LEISE_MELDUNGEN` gilt nur für die Tabelle `meldungen` des Clip-Bots – der Lern-Bot liest
`lern_meldungen` und brauchte deshalb einen eigenen Filter. Die Uhrzeit-Logik ist dieselbe (aktionen.ruhezeit).

Hilfe: HILFE_ZUSATZ wird von lernbot.cmd_hilfe an lernbot.HILFE angehängt (HILFE selbst bleibt unverändert, weil
Regisseur 2.0 dort ändern könnte – so kommen sich beide beim Zusammenführen nicht in die Quere).

Zeit: `jetzt` ist auf Modulebene importiert – Tests ersetzen `lernbot_publikum.jetzt` (mock.patch.object).

Vertrag Stufe 1 (Skelett): `registriere` und HILFE_ZUSATZ sind schon echt, alles andere baut Paket (e). Paket (e)
hängt auch `faellige_lern_meldungen` in lernbot.sende_meldungen und HILFE_ZUSATZ in lernbot.cmd_hilfe ein.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from .konfig import Konfig
from .zeit import iso, jetzt  # noqa: F401 – im Modul importiert, damit Tests `lernbot_publikum.jetzt` ersetzen können

# Lern-Meldungen mit diesen Schlüssel-Anfängen warten die Ruhezeit ab
LEISE_LERN_MELDUNGEN = ("publikum:", "woche:")

# Anhang an lernbot.HILFE (HTML wie dort)
HILFE_ZUSATZ = """
📊 <b>Publikum (TikTok-Zahlen):</b>
📦 Nach 👍 auf einen Short: „Upload-Paket“ – Video als Datei, Caption zum Kopieren, Häkchen je Plattform.
🔗 /link <code>41 https://www.tiktok.com/@…/video/…</code> – Post zu Entwurf 41 anlegen, der Bot nennt die Post-Nummer.
📸 Screenshot der TikTok-Statistik mit Bildunterschrift <code>#17</code> (Post-Nummer) – Claude liest die Zahlen.
✏️ Von Hand: <code>#17 1240 61 6.8 34</code> = Views, Likes, Ø Wiedergabe (s), ganz angesehen (%), „–“ = unbekannt.
/publikum – letzte Posts mit Zahlen und Score (nach 7 Tagen)"""


def faellige_lern_meldungen(con: sqlite3.Connection, konfig: Konfig, zeit: datetime | None = None) -> list[sqlite3.Row]:
    """Ungesendete lern_meldungen (id, schluessel, text) in der Reihenfolge ihrer id; in der Ruhezeit
    (aktionen.ruhezeit) ohne die mit LEISE_LERN_MELDUNGEN – die bleiben liegen und kommen nach leise_bis.
    Beispiel: um 23:30 mit Ruhezeit 23:00–08:00 → „publikum:bewertet:…“ fehlt, ein Alarm „fehler:…“ ist dabei."""
    raise NotImplementedError


def meldung_nach_bewerten(con: sqlite3.Connection, ergebnis: dict, zeit: datetime | None = None) -> bool:
    """Nach `pipeline publikum bewerten`: sind neue Scores gesetzt, eine Lern-Meldung
    `publikum:bewertet:<datum>` („📊 2 Posts bewertet: #17 +0,8 · #18 −0,3 – /publikum“). Je Tag höchstens eine
    (ON CONFLICT (schluessel) DO NOTHING); ohne neue Scores keine. True = neu angelegt."""
    raise NotImplementedError


def publikum_text(con: sqlite3.Connection, konfig: Konfig, grenze: int = 10, zeit: datetime | None = None) -> str:
    """Text für /publikum: die letzten `grenze` Posts, neueste zuerst, je Zeile z. B.
    „#17 TikTok · Entwurf 41 · 4 Tage · 👁 1 240 ⏱ 6,8 s · Score noch offen (ab 7 Tagen)“ bzw.
    „#12 TikTok · Clip 88 · 9 Tage · Score +0,8 (Wiedergabe über, Likes je View unter deinem Median)“ bzw.
    „… · Score 0 (Basis zu klein)“. Letzte Zeile: „🤖 Claude diese Woche: 3 Aufrufe“ (ereignisse art='claude',
    seit Montag 00:00 Ortszeit). Ohne Posts: kurzer Satz, wie ein Post entsteht (📦 → /link)."""
    raise NotImplementedError


async def cmd_publikum(update, context) -> None:
    """/publikum [anzahl] (Standard 10, höchstens 30). Antwort: publikum_text, bei Überlänge in Stücken
    (lernbot.stuecke). Eine ungültige Anzahl → „Aufruf: /publikum oder /publikum 20“. Wirft nicht."""
    raise NotImplementedError


def registriere(app, nur_ich) -> None:
    """Hängt /publikum in die Lern-Bot-App (aufgerufen von lernbot.baue_app)."""
    from telegram.ext import CommandHandler

    app.add_handler(CommandHandler("publikum", cmd_publikum, filters=nur_ich))
