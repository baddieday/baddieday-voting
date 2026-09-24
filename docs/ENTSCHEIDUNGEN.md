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

## E5 · Was „Replays“ in Ziel 3 heißt (24.09.)
„Pro Replay die letzten 90 s verlustfrei (-c copy)“ geht nur mit Videos – Fortnite-Replays sind keine Videos
und ohnehin klein. Auslegung: **Fortnite-Replays werden immer ganz kopiert**, gekürzt werden nur
**Rohvideos** (Nvidia-/SteelSeries-Aufnahmen, meist Instant-Replays, deren Ende den Moment enthält).
Reihenfolge nach Wert: Replays → Session-Ordner (fertige Clips + JSON) → Rohvideos (neueste zuerst).
Die Kopie spiegelt die Ordner (`material/<relativer Pfad>`), damit die Pfade aus der Datenbank weiter passen.
Auch bei gekürzten Videos wird die Prüfsumme der **ganzen Quelle** gespeichert (Nachweis der Herkunft).

## E6 · Stimmung: Regeln + ein Claude-Aufruf (24.09.)
| Option | Bewertung |
|---|---|
| a) Alles per Claude | teuer (Abo-Limit), nicht nachvollziehbar, Regel „höchstens einmal pro Durchlauf“ |
| b) Trainiertes Audio-Modell (Lachen, Jubel) | keine Trainingsdaten, schwer zu erklären |
| **c) Punkte-Regeln aus Merkmalen, nur unsichere Momente gesammelt an einen `claude -p`** | ✅ |

Merkmale: Lautstärke-Verlauf (EBU R128 alle 0,1 s) von Spiel- und Mikro-Spur, Whisper-Transkript mit
Wortlisten (Lachen/Jubel/Frust), Kills/Serie/Victory/Umgehauen/Tod aus `sessions/<id>/replay.json`.
Lachen wird über Whisper erkannt („Haha“) – eine eigene akustische Lach-Erkennung wäre unzuverlässig.
Claudes Antwort wird geprüft (nur bekannte Momente, nur die fünf Wörter); die Regel bleibt im Merkmal `regel`.
Gemessen hier: Whisper „small“ int8 auf 4 Kernen: 11 s für einen 4-s-Satz inkl. Modell laden.

## E7 · Musik-Analyse und Musik-Quelle (24.09.)
**Analyse:** eigene numpy-Analyse (Spectral Flux → Autokorrelation → Beat-Tracking nach Ellis 2007) statt
`librosa` (zieht numba/llvmlite/scikit-learn nach, schwerer zu erklären). Gemessen: Klick-Spuren 90/128/150 BPM
→ 89,5/128,7/151,1; Beats ±15 ms; NCS-House-Titel 129,8 BPM.
**Energie:** Die gemessene Energie trennt laut gemasterte Titel schlecht (echte NCS-Titel alle 0,60–0,82).
Deshalb zählt die **Stimmung aus der Quelle** zuerst (NCS-Mood-Filter beim Laden bzw. `#episch` in der
Bildunterschrift beim Lern-Bot), und der Regisseur nutzt die Energie nur **relativ zur eigenen Bibliothek**.
**Quelle:** NCS (`pipeline musik ncs --stimmung X`): direkte MP3-Links, fertige Quellenangabe je Titel
(„Song: … / Music provided by NoCopyrightSounds / Free Download/Stream: …“) → `<titel>.lizenz.txt` und in die
Beschreibung. Musik liegt auf dem Mini (`/var/lib/clip-pipeline/musik`) und nie im Repo.

## E8 · Regisseur und Rendern (24.09.)
**Schnittliste:** eigenes Format (Version 3, `schemas/regie.schema.json`) statt Erweiterung der Session-
Schnittliste – die Session-Liste gehört zum n8n-Vertrag (Regel 4: nicht verändern). Vor dem Speichern
Schema + fachliche Prüfung (lückenlose Zeitleiste, Quelle im Video, kein Kill angeschnitten).
**Bogen:** Hook (zweitstärkster) → steigend → Atempause bei ~60 % → Höhepunkt zuletzt. Einfach und erklärbar;
ein gelerntes Reihenfolge-Modell bräuchte viel mehr Bewertungen.
**Beat-Schnitt:** Grenzen auf Beats des Titels ab einem Versatz, der den „Drop“ auf den Höhepunkt legt.
Übergänge sind mittig auf dem Beat: Segment i bekommt vorne/hinten je die halbe Übergangsdauer als „Griff“,
dann gilt `offset_i = bisherige Länge − Übergangsdauer`, und die Zeitleiste wird nicht kürzer.
**Zwei stille xfade-Fallen** (gefunden durch den Farb-Test, der je Segment prüft, ob der geplante Moment zu
sehen ist): (1) Schnittdauer „1 Bild“ muss aufgerundet werden (1/30 → 0.0334), sonst endet das Video nach dem
ersten Segment; (2) der erste Eingang braucht Bilder bis GANZ ans Übergangsende → 0,2 s Überhang je Eingang.
**Entwurf:** 720p, VA-API wenn `/dev/dri` da ist, sonst libx264; CRF 23, höchstens 4 Mbit/s (vorher schöpfte
er das 48-MB-Budget aus: 40 MB für 40 s). **Final:** Auftrag-JSON auf dem Speicher, pve-big rendert mit
NVENC per `clip-big-steuer final <name>`, danach sofort aus (über `big.wach_halten`).

## E9 · Lern-Bot als eigener Dienst (24.09.)
| Option | Bewertung |
|---|---|
| a) In den bestehenden Clip-Bot einbauen | verboten (Regel 4), zudem ein Token = ein Empfänger |
| **b) Eigener Bot, eigener Token, eigener Dienst `clip-lernbot`** | ✅ gleiche Muster wie der Clip-Bot (Polling, Whitelist, Outbox-Schleife) |

Rechenintensives (compose, Rendern, Musik-Analyse) läuft in einem Thread mit **eigener** DB-Verbindung
(SQLite-Verbindungen dürfen nicht zwischen Threads wandern). Nachrichten an dich laufen über die Tabelle
`lern_meldungen` (je Schlüssel einmal): Alarme des Wächters, Abendstand, Abschlussbericht.

## E10 · „Session vorbei“ über eine Datei statt n8n (24.09.)
| Option | Bewertung |
|---|---|
| a) Neuer n8n-Webhook | verboten (Regel 4), und n8n soll nicht Zentrale sein |
| b) Direkter HTTP-Aufruf vom Gaming-PC an den Mini | neuer offener Dienst auf dem Mini, Token-Verwaltung |
| **c) Marker-Datei `sitzungen/session_<zeit>.json` über die bestehende SMB-Freigabe; Mini holt per Timer ab** | ✅ nichts Neues offen, passt zum Zielbild (Mini = Zentrale); später leicht auf eine Warteschlange umzustellen |

Standard **aus** (`SessionVorbeiMinuten = 0`); die bisherige Meldung je Match an n8n bleibt unverändert.
Getestet unter PowerShell 7.4 (Linux); auf dem Gaming-PC läuft Windows PowerShell 5.1 → dort einmal
`-Probelauf` ansehen, bevor du es einschaltest.

## E11 · Arbeitsteilung mit der Vor-Ort-Sitzung (24.09.)
Auf deinen Wunsch teile ich die Arbeit mit der Sitzung „root-b0“ (läuft als root auf dem Linux-Server).
Sie kann mir schreiben, ich ihr nicht („cloud session cannot message other sessions yet“). Deshalb stehen
meine Bitten in `docs/AUFGABEN-VOR-ORT.md`. Sie übernimmt alles, was echte Hardware braucht (A–D), ich den Code.
Regel für beide: Die Produktion unter `/opt/clip-pipeline` bleibt unberührt, getestet wird in `/opt/clip-regie`.

## E12 · Befunde der unabhängigen Prüfung (24.09.)
Ein zweiter Prüfer hat den Sprint-Code durchgesehen (2 schwere, 3 mittlere, 3 leichte Befunde). Alle behoben:
1. **Alter Weckweg** (n8n-Schritte, `/paket`, `highlight`) weckte ohne gesichertes Herunterfahren. Regel 3 geht
   vor → er weckt jetzt nur noch, wenn `big.darf_wecken` passt (`[big].alter_weckweg_nur_mit_aus = true`).
   **Folge bis zur Einrichtung der SSH-Steuerung:** Schläft pve-big, enden diese Schritte mit Exit 3 (n8n-Alarm)
   statt ihn unkontrolliert zu wecken. Der Gaming-PC weckt pve-big beim Kopieren weiterhin selbst.
   Abschaltbar, falls dir das bisherige Verhalten wichtiger ist.
2. **Final-Render hielt sich selbst für beschäftigt** (eigene flock-Sperre) → pve-big blieb danach an.
   Jetzt merkt sich `sperre.GEHALTEN`, welche Sperren der eigene Prozess hält.
3. **`final` lief als root mit Daten von der Freigabe** → Auftrag wird vollständig geprüft (Schema, Pfade
   innerhalb des Speichers, Name), und `clip-big-steuer` rendert als Benutzer `clips` (`runuser`).
4. Lern-Bot renderte ohne Pipeline-Sperre und konnte doppelt senden → Sperre + asyncio-Lock.
5. Action in den letzten Zehnteln einer Datei ließ jeden `compose` scheitern → Muss-Zone begrenzt.
6. `sitzungen` versuchte bei Render-Fehlern alle 10 min neu → Fehler wird vermerkt.
7. Gekürzte Material-Kopien (letzte 90 s) bekamen die Zeiten des Originals → Versatz `von_s` wird angewandt.
8. Kleinigkeiten: `status_ok` verfällt nach 14 Tagen, Markenname beim Lösen geprüft, nach einem gescheiterten
   Status-Test direkt nach dem Wecken wird trotzdem „aus“ versucht.
Bewusst **nicht** geändert: `Uebertragung.ps1` weckt pve-big weiter selbst (auch nach der Frist) – das ist dein
Spielabend, kein Sprint-Auftrag, und der Gaming-PC ist bis Dienstag aus.

## E13 · CT 102 startet unabhängig von pve-big (24.09.)
CT 102 startete nicht mehr, solange pve-big schlief: Proxmox prüft vor dem Start jede `mp*`-Quelle, und der
tote NFS-Pfad lieferte EIO. Rettung (`deploy/pve-mini/rette-clips-start.sh`, von dir ausgeführt): Der Speicher
hängt nicht mehr als `mp0`, sondern über `/mnt/big` (bind, shared) und `lxc.mount.entry … rbind,rslave` im CT;
`/srv/clips` ist ein Link auf `/srv/big/clips`. Ein Nachzieher-Timer hängt NFS alle 30 s ein, sobald Port
2049 antwortet, und nach drei Fehlschlägen wieder aus (`umount -l`). Der CT startet damit immer.

## E14 · pve-big schaltet sich selbst ab: clip-leerlauf (24.09.)
Statt „30 min ohne Lese-/Schreibzugriff“ prüft `clip-leerlauf` auf pve-big **jede Minute** echte Zugriffe
(ZFS-Datenzähler, NFS-Datenoperationen, offene NFS/SMB-Dateien, laufende Kopien, Herzschlag der Pipeline,
Render, Proxmox-Tasks, von Hand gestartete Gäste, angemeldete Menschen/Web-Konsole). Nach **20 min** Ruhe (später
10) misst er nach 15 s alles noch einmal und fährt dann herunter. Keine Aktivität sind Verbindungen,
`stat()` und Lease-Erneuerungen; eine unlesbare Quelle zählt als „wach“. Gäste mit Autostart zählen nicht.
Er meldet sich jede Minute in `<clips>/.leerlauf.json`. Sieht der Mini dort ein **scharfes** clip-leerlauf,
darf er pve-big wecken (`big.darf_wecken`) – auch ohne SSH-Steuerung. Das erfüllt Regel 3: geweckt wird nur,
was sich nachweislich selbst wieder abschaltet. Einrichtung per `deploy/big/einrichten.sh` mit fest im
Einfüge-Block eingetragenen Prüfsummen (die Freigabe ist auch vom Gaming-PC beschreibbar).

## E15 · Lernschleife im Bot (24.09.)
Nach jeder fertigen Bewertung (✅) baut der Lern-Bot sofort den nächsten Entwurf – schon mit der neuen Bewertung
eingerechnet – und analysiert vorher 10 weitere Clips (Stimmung, ohne Claude, damit dein Abo nicht belastet
wird). So wächst die Auswahl, während du bewertest, und pve-big muss dafür nicht extra wach bleiben.

## E16 · Abwechslung statt immer derselben Top-Momente (24.09.)
Befund: `compose` nahm stur die Momente mit den meisten Punkten; nur die Musik rotierte (Abzug je Nutzung).
Deshalb kamen immer dieselben Clips mit anderer Musik. Jetzt:
- Momente aus dem letzten Entwurf verlieren 70 % ihrer Punkte, aus dem vorletzten 35 % usw. Ein Anteil statt
  fester Punkte, weil die Punkte weit streuen (Einzelkill ≈ 3, Vierfach-Kill ≈ 13,5): Ein fester Abzug hätte
  die stärksten immer vorn gelassen (im Test nachgewiesen). Die besten kommen alle 2–3 Entwürfe wieder.
- Lernen je Moment: 👍 +0,5, 👎 ohne Grund −0,5, neuer Grund „🥱 Clips langweilig“ −1 (begrenzt auf ±3).
- Muss ein Short gekürzt werden, fliegt der Moment mit den wenigsten Punkten (vorher: geringste Intensität –
  dabei gingen gelernte Vorlieben verloren).
- Die Schnittliste zeigt je Segment Punkte, Abzug und wie oft er schon gezeigt wurde; der Bot schreibt
  „🆕 n neue · m schon gezeigt“.
