# Entscheidungen – Sprint „Regisseur“ (24.–28.09.2026)

Jede Entscheidung: Optionen kurz abgewogen, gewählt, begründet. Neueste unten.
Zielbild (gilt für alles): **Zentrale mit Warteschlange und Postgres auf dem Mini**, der VPS ist nur Zusatz.

---

## E1 · Wo der Sprint tatsächlich läuft (24.09.)
**Befund:** Der Auftrag sagt „LXC clips auf pve-mini“. Tatsächlich läuft diese Sitzung in einem
**Cloud-Container** (Hostname `vm`, 4 Kerne, kein `/srv/clips`, kein `/dev/dri`, kein Tailscale,
keine `.env`, kein `LEARN_BOT_TOKEN`). Das Heimnetz ist von hier nicht erreichbar.

| Option | Bewertung |
|---|---|
| a) Abbrechen und nachfragen | verliert den Sprint; nichts davon ist irreversibel → nicht nötig |
| b) So tun, als liefe es auf dem Mini | verboten („nichts als fertig melden, was nicht ausprobiert ist“) |
| **c) Alles als Code bauen, hier mit künstlichem Material testen, Installation vorbereiten** | ✅ |

**Folgen:**
- pve-big wird von hier **nie** geweckt (geht technisch nicht) → Regel 3 automatisch eingehalten.
- Bestandsaufnahme, Material-Kopie und echte Stimmungsanalyse sind **Befehle, die du auf dem Mini startest**;
  hier sind sie mit nachgebauten Ordnern und Videos getestet.
- Kommunikation über den Lern-Bot ist ohne Token nicht möglich → Bericht in `docs/SPRINT-LOG.md` und im Chat.
- Jeder Punkt im Abschlussbericht ist markiert: **getestet hier** / **nur am echten System prüfbar**.

## E2 · Branch (24.09.)
Auftrag: `sprint-regisseur`. Die Sitzung hatte `claude/rc-73hbhz` vorgegeben. Der Auftrag ist ausdrücklich →
gearbeitet und gepusht wird auf **`sprint-regisseur`**.

## E3 · Datenbank für die neuen Teile (24.09.)
| Option | Bewertung |
|---|---|
| a) Postgres jetzt einführen | Postgres läuft noch nirgends; ohne Mini nicht testbar; bestehender Bot hängt an SQLite |
| b) Zweite SQLite-Datei | trennt, was zusammengehört (Clips ↔ Momente) |
| **c) Gleiche SQLite-Datei, neue Tabellen in eigener `regie.sql`, nur portables SQL** | ✅ |

Portabel heißt: `ON CONFLICT … DO NOTHING/UPDATE` statt `INSERT OR IGNORE`, Zeiten als ISO-UTC-Text,
JSON als Text, keine SQLite-Sonderfunktionen in Abfragen. Der Umzug nach Postgres betrifft dann nur
`regie.sql` (Schlüsselspalten → `GENERATED … AS IDENTITY`) und den Platzhalter `?` → `%s`.
Kein Widerspruch zum Zielbild: Die Logik (compose, Lernen) arbeitet auf JSON und Zeilen-Dicts, nicht auf SQLite.

## E4 · pve-big herunterfahren (24.09.)
| Option | Bewertung |
|---|---|
| a) Idle-Skript nur auf pve-big | braucht Host-Änderung; weiß nichts von Aufträgen auf dem Mini |
| b) Proxmox-API vom Mini (`pvesh`/API-Token) | API-Token mit Power-Rechten, mehr Angriffsfläche; Weg existiert noch nicht |
| **c) Wächter auf dem Mini + SSH mit eigenem Schlüssel und `command=`-Sperre auf pve-big** | ✅ gleiches Muster wie der n8n-Zugang (`deploy/n8n-lauf.sh`), erlaubt nur `status` und `aus` |

- „Auftrag läuft“ = Pipeline-Sperre (`flock`) ist belegt **oder** eine „Halten“-Marke ist nicht abgelaufen
  **oder** pve-big meldet SMB-Verbindungen (Gaming-PC kopiert) bzw. einen laufenden ffmpeg
  **oder** pve-big ist erst seit < 20 min wach (Gaming-PC hat ihn gerade geweckt).
- **Wecken nur, wenn Herunterfahren nachweislich geht:** Schlüssel vorhanden **und** ein `status` über SSH
  hat schon einmal geklappt. Sonst wird gar nicht geweckt (Regel 3).
- **Frist** `[big].frist = 2026-09-28T20:00+02:00`: danach weckt die Pipeline pve-big nicht mehr, und der
  Wächter fährt ihn einmal unbedingt herunter. **Nach dem Sprint musst du die Frist leeren**, sonst weckt die
  Pipeline am Dienstag nicht.
- Vorhandene Zugänge: Aus dem Repo bekannt sind nur NFS (Mini → big) und Wake-on-LAN. Ein SSH-Weg zu pve-big
  existiert noch nicht → Host-Änderung (siehe Abschlussbericht).
