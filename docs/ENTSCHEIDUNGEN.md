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
Er setzt jede Minute den Zeitstempel der **leeren** Datei `<clips>/.leerlauf-scharf`. Sieht der Mini dort per
`stat()` ein frisches Lebenszeichen, darf er pve-big wecken (`big.darf_wecken`) – auch ohne SSH-Steuerung. Das erfüllt Regel 3: geweckt wird nur,
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

**Nachtrag 24.09. (vor der ersten Installation gefunden):** Die erste Fassung schrieb jede Minute eine
Statusdatei `.leerlauf.json` in den Clips-Ordner und der Lern-Bot las sie alle 30 s. Beides hätte pve-big für
immer wachgehalten: Das Schreiben zählt clip-leerlauf selbst als ZFS-Schreibzugriff, das Lesen über NFS als
OPEN/READ. Jetzt: leere Marke, nur der Zeitstempel wird gesetzt (utime zählt weder ZFS noch nfsd), und der Mini
schaut nur nach (stat = GETATTR, zählt nicht).

## E17 · Prüfung des Abschalt-Mechanismus vor der Installation (24.09.)
Weil schon zwei Fehler pve-big für immer wachgehalten hätten, habe ich den ganzen Mechanismus (clip-leerlauf,
einrichten.sh, Weck-Logik auf dem Mini, Lern-Bot, Windows-Helfer) unabhängig prüfen lassen: 5 Blickwinkel,
jeder Befund von 2 Gegenprüfern angegriffen. 19 Befunde, 9 bestätigt, alle behoben:
1. **Vergessene Konsole hält ewig wach.** Eine offene Web-Shell (login + Proxmox-Aufgabe „vncshell“) zählte immer.
   Jetzt zählt eine Sitzung nur, wenn in den letzten LEERLAUF_MIN Minuten getippt wurde (Zugriffszeit des
   Terminals, wie `who -u`); Konsolen-Aufgaben sind keine Arbeit an sich. einrichten.sh sagt: Fenster schließen.
2. **Gaming-PC weckte alle 2 min** (ab Dienstag): Uebertragung.ps1 weckte vor der Suche nach neuen Dateien.
   Jetzt erst suchen, nur mit Arbeit (oder fälliger Session-Datei) wecken.
3. **Ein einziges WoL-Paket** verpufft, wenn pve-big gerade noch herunterfährt → in jeder Warterunde erneut
   (Pipeline, wach_halten, regie-starten.sh).
4. **Lücke vor dem Ausschalten:** Die Zähler werden jetzt zuletzt gelesen (nach pvesh/qm/pct, die Sekunden
   dauern). Ein Rest-Fenster von Bruchteilen einer Sekunde bleibt; ein Auftrag, der genau dann startet,
   scheitert sauber und lässt sich wiederholen (keine Daten in Gefahr).
5. **Weck-Erlaubnis nie zurückgenommen:** Sieht der Mini pve-big wach und eingehängt, aber im Probelauf oder
   10 min ohne frische Marke, erlischt die Erlaubnis.
6. **Herzschlag ohne Obergrenze:** HERZSCHLAG_MAX_H stand nur in der Konfig. Jetzt trägt der Dateiname die
   Startzeit; ein Schritt, der länger als 4 h läuft (hängt), hält pve-big nicht mehr wach.
7. **Prüfsummen nur im Chat:** regie-starten.sh gibt den Einfüge-Block jetzt selbst aus, mit Summen aus dem
   git-Stand im CT (nicht von der Freigabe) – er kann nicht mehr veralten.
8. regie-starten.sh sagt am Ende (auch bei Abbruch), ob pve-big sich selbst abschaltet, sonst Block + Hinweis.
Verworfen (Gegenprüfer überzeugt, dass es auf diesem Aufbau nicht passiert), u. a.: Herzschlag von
`pipeline sitzungen` ohne Arbeit, pgrep sieht Prozesse in LXC-Gästen, scp/rsync ohne Terminal.

## E18 · Neuer Datenweg: einmal pro Abend wecken, VPS als Arbeits- und Backup-Kopie (entschieden 24.09., Umsetzung folgt)
> **Stufe 1 ersetzt durch E19** (24./25.09.): Statt den Abend auf dem Gaming-PC zu erkennen, puffert der Mini;
> pve-big wird nur noch nachts zum Abgleich geweckt. Die VPS-Kopie entfällt vorerst (siehe E19).
Mit dir abgestimmt (Energie: pve-big nicht ständig an/aus):
- **Nach jedem Spielabend weckt der Gaming-PC pve-big genau einmal** („Session vorbei“), alle neuen Aufnahmen
  kommen in einem Rutsch rüber, der Mini schneidet die Clips, danach geht pve-big aus. Kein Wecken alle 2 min.
- **pve-big behält alles** (Archiv, Rohdaten nie gelöscht). Zusätzlich eine Kopie auf dem **VPS** als Arbeits-
  und Backup-Kopie für 7–14 Tage; der VPS löscht seine Kopie erst, wenn Alter erreicht UND die Prüfsumme auf
  pve-big bestätigt ist. Kein Rück-Download in Blöcken nötig.
- **pve-big 1× pro Woche zur festen Zeit** für das Highlight-Video (NVENC, jede 2. Woche) und Abgleich; danach aus.
- Shorts auf dem Mini (VA-API), bei Bedarf parallel auf dem VPS.
- Upload daheim ≤ 50 Mbit/s: Wer wann wie viel hochlädt (pve-big während des Abend-Weckens, gedrosselt vom
  Mini oder nur das für Highlights Nötige), entscheide ich nach dem Messen der echten Mengen auf pve-big.
- Sprint-Regel „VPS nicht verändern“ hebst du dafür auf; n8n-Workflows bleiben, solange es geht, unverändert.
- **Zugang:** du gibst mir root auf pve-mini, pve-big und dem VPS über Tailscale SSH (Tags `tag:claude` →
  `tag:heim`, kurzlebiger Schlüssel nur in den Umgebungs-Einstellungen, nie im Chat).

## E19 · Puffer auf dem Mini, Lager auf pve-big – statt „Gaming-PC weckt einmal pro Abend“ (24.09., ersetzt E18 Stufe 1)
Du hast erlaubt, von E18 abzuweichen, wenn eine Lösung klar besser ist. Verglichen wurden zwei ausgearbeitete Varianten,
jede von zwei Gegenprüfern angegriffen (Betrieb, Datenverlust) und von drei Richtern unabhängig bewertet
(0–10 je Ziel: kein Verlust, Energie, einfach/wartungsarm, freundlich, n8n unverändert, Umsetzungsrisiko):

| Variante | CEO | Betrieb | Senior Dev |
|---|---|---|---|
| A: E18 wie beschlossen – PC sammelt, erkennt „Abend vorbei“, weckt pve-big 1×, kopiert alles | 29 | 29 | 29 |
| **B: Mini als Puffer – PC kopiert wie bisher alle 2 min, aber auf den Mini; pve-big nur nachts zum Abgleich** | **47** | **46** | **48** |

**Warum B:** Die meisten der 19 Stolperfallen von A entstehen, weil der PC erkennen muss, wann der Abend vorbei ist
(PC gleich aus, Fortnite bleibt offen, Pausen, verpuffendes WoL, Rekorder-Apps räumen auf, bevor kopiert wurde,
Nachrichtenflut in der Nacht, 12-h-Warnung, Match-Ende = jetzt). In B gibt es diese Frage nicht:
- Aufnahmen verlassen den PC wie bisher Minuten nach dem Match – auf den Mini, der immer läuft.
- Der Mini verarbeitet sofort (n8n und Produktionscode unverändert, `[speicher].host = ""` → nie Wecken).
  Vorschauen, /paket, Highlight und Lern-Bot arbeiten lokal – kein „Outbox wartet“, kein Wecken tagsüber.
- pve-big wird höchstens einmal pro Nacht (04:30) geweckt und nur, wenn es Neues gibt: Abgleich mit SHA-256 und
  Zurücklesen, danach schaltet clip-leerlauf ab. Typisch ~2,6 GB/Spieltag ≈ 1–3 min Kopieren.
- Die Logik sitzt in Python auf Linux (testbar), nicht in PowerShell auf dem am schlechtesten beobachtbaren Rechner.

**Harte Regeln (aus den Bedingungen der Richter):**
1. Puffer und Lager dürfen nie verwechselt werden: getrennte Marken (`.clip-puffer` nur im Puffer, `.clip-lager` nur im
   Lager), verschiedene Dateisysteme (st_dev), kein `samefile` – sonst bricht jeder Abgleich mit Klartext ab.
2. Rohdaten (`eingang/`, `replays/`) werden im Lager nie überschrieben oder gelöscht; Konflikte werden versioniert abgelegt.
3. Im Puffer wird in dieser Stufe nichts automatisch gelöscht (`[puffer].freigeben = false`). Freigabe bestätigter
   Rohdaten ist eine eigene spätere Stufe (B5) und braucht dein OK. 96 GB reichen für gut einen Monat.
4. Samba im CT blendet nur `.aktiv` aus – `veto files` vergleicht jeden Ordnernamen auf jeder Ebene, sonst verschwände
   `eingang\nvidia\highlights` still.
5. Probleme meldet eine Morgenprüfung (09:30) höchstens einmal am Tag je Thema, nachts nichts; montags ein Lebenszeichen.
6. Ohne `[lager].wurzel` verhält sich alles exakt wie vorher – das ist der eingebaute Rückweg.

**Was von A übernommen wurde:** prepare nimmt das Match-Ende aus der Replay-Zeit; der PC notiert Match-IDs, bevor eine
Datei als erledigt gilt; IDs als Text (PS 5.1); Sitzungsdatei nur ohne Kopierfehler; Statusdatei des PCs
(`sitzungen/pc-status.json`) als Rückkanal; Ruhezeit im Bot.

**Was entfällt / später:** Die VPS-Kopie aus E18 entfällt vorerst (CLAUDE.md: keine Videos auf den vServer); ein zweiter
Standort bleibt offen. Fester Wochentermin für pve-big ist unnötig (Highlight läuft auf dem Mini). Später: B5 Freigabe,
Rückgriff aufs Lager für sehr alte Dateien, Scrub-Nacht, LEERLAUF_MIN = 10.

**Einführung:** `docs/PUFFER.md` (R0–R8), jeder Host-Schritt nur mit deinem OK. Bedingungen vorher: Sprint in main
übernommen, `[big].frist` geleert, Übernahme der vorhandenen Daten mit ausdrücklichen Pfaden, Umschalten erst nach
geprüftem Abgleich.

**Nachtrag 25.09.: Abgleich tagsüber, nie nachts.** Der Lüfter von pve-big soll niemanden wecken. Der Abgleich läuft
deshalb um 10:00 statt 04:30, die Morgenprüfung um 11:00 statt 09:30. Dazu eine Nachtruhe als harte Grenze
(`[lager].nachtruhe_von/_bis`, Standard 22:00–08:00): Schläft pve-big, weckt der Abgleich ihn dann nie – auch nicht,
wenn systemd nach einem Neustart des Mini einen verpassten Lauf nachholt. Läuft er ohnehin, wird abgeglichen.
„Nachts“, „04:30“ und „09:30“ oben sind damit überholt.

## E20 · Zugang für Claude: flüchtiges Tailnet-Gerät statt Tags und Schlüssel (24.09., abweichend von E18)
Am Handy waren Tags, Policy-Umbau und Schlüssel in den Umgebungs-Einstellungen nicht machbar. Stattdessen:
- Die Cloud-Sitzung startet Tailscale **flüchtig** (`tailscaled --state=mem:`, Userspace-Netz) und meldet sich per
  **Link** an, den du antippst. Das Gerät „claude-cloud“ verschwindet 30–60 min nach Sitzungsende von selbst.
- Deine Policy hat eine SSH-Regel im **check-Modus**: deine eigenen Geräte → deine eigenen Geräte, `root` und
  Nicht-root. Jede neue Verbindung muss per Link bestätigt werden; danach gilt sie 12 h.
- Tailscale SSH ist nur auf **pve-big** und **pve-mini** (Host) an – nicht im LXC „clips“ (n8n nutzt dort normales SSH).
- `tag:heim` ist aus beiden Hosts entfernt (war in der Policy nie definiert und hielt pve-big aus dem Tailnet und
  pve-mini ohne SSH-Regel). Schlüssel-Ablauf für beide Server abgeschaltet.
- Nachteil: Als „dein Gerät“ könnte der Container für die Dauer der Sitzung alle deine Tailnet-Geräte erreichen.
  Später (am PC) enger machen: eigenes Tag für die Sitzung und eine Regel nur auf die zwei Hosts.

## E21 · Multikills am Stück: Aktions-Zeitpunkt, Serie als ein Stück, Nachschnitt (25.09.)
**Befund (echte Daten, 88 Sessions):** Double/Triple Kills waren oft nur als Einzelkills zu sehen. 21 von 37 Multikills
sind Team-Wipes: Ist der letzte Gegner eines Teams umgehauen, sterben alle umgehauenen gleichzeitig. Alle Kills haben dann
den Zeitstempel des Wipes, das eigentliche Umhauen lag 3–22 s davor. Der Clip begann 8 s vor dem ersten *Kill* und
verpasste in 15 von 21 Fällen das erste Umhauen. Der Regisseur schnitt noch enger.

**Entscheidung:**
- **Zählen bleibt wie bisher.** Serie (Kette ≤ 10 s), Punkte, Titel, Captions, Elo und die Tabelle `clips` laufen weiter
  über den Kill-Zeitpunkt. Ein Team-Wipe-Triple bleibt ein Triple.
- **Neu je Kill: der Aktions-Zeitpunkt** = mein Umhauen dieses Gegners (höchstens 90 s alt), sonst der Kill selbst.
  Er bestimmt nur, wo ein Clip beginnt: neue Clips starten 8 s vor der ersten Aktion (höchstens 60 s, sonst vorne
  gekappt). `analyse.json`, Schnittliste und Moment-Merkmale bekommen `aktion_sekunden` parallel zu `kill_sekunden`.
- **Regisseur: Serie als ein Stück.** Ab 2 Kills darf ein Segment bis `serie_max_s` lang werden (Short 20 s,
  Zusammenschnitt 30 s). Pausen über 4 s zwischen zwei Aktionen überspringt ein Jump-Cut (1,5 s nach der vorigen
  Aktion raus, 2,0 s vor der nächsten wieder rein, harter Schnitt, gleiche Datei). Passt eine Serie auch so nicht in
  einen Short, wird sie dort nicht gewählt statt zerteilt; im Zusammenschnitt kommt sie ganz.
- **Nachschnitt vorhandener Momente:** `pipeline momente nachschneiden [--tage 14] [--probe]` schneidet Momente
  `clip:N` mit ≥ 2 Kills neu aus der Quellaufnahme im Puffer, in eigene Dateien unter `sessions/<ID>/momente/`.
  Bot-Clips, Tabelle `clips`, Stimmung und gelernter Moment-Bonus bleiben. `merkmale.nachschnitt` merkt sich, was
  geschehen ist; damit ist der Befehl idempotent, und `pipeline stimmung --neu` setzt den Moment nicht zurück.

**Harte Regeln für den Nachschnitt:** nur im getrennten Betrieb (E19, sonst Exit 2), nur im Puffer. Die Quelle wird
über `[speicher].wurzel` gelesen, jede Pfad-Komponente per `lstat` geprüft, Links werden nie verfolgt. Liegt die
Aufnahme nur noch im Lager, wird der Moment übersprungen und gezählt (`ohne_quelle`); pve-big wird nie geweckt.
Nie überschreiben: Eine vorhandene Zieldatei wird übernommen, wenn ihre Dauer passt, sonst ist es ein Fehler
(Exit 1). Neue Dateien entstehen über eine versteckte Zwischendatei und einen harten Link. Pipeline-Sperre (flock)
wie bei render.

**Abweichungen von der Vorgabe (mit Grund):**
- Liegt im Anlauf ein Kill eines *früheren* Clips (echtes Beispiel: Einzelkill 6 s vor dem ersten Umhauen eines
  Wipes), beginnen Nachschnitt und neue Clips aus `analyze` kurz danach (0,5 s, höchstens bis 1 s vor der Aktion;
  eine Regel für beide: `vorbewertung.anlauf_start`). Sonst stünde dieser Kill in
  `kill_sekunden` des Moments, käme doppelt in Zusammenschnitte, und sein frühes Umhauen zöge den Regisseur an den
  Dateianfang. `stimmung.py` nimmt alle Kills im Fenster, also hilft nur ein späterer Fensterbeginn.
- Liegt die Aktion schon im Bot-Clip (Claude hat früh genug begonnen), wird kein Video geschnitten. Es kommen nur
  `aktion_sekunden` und der Eintrag `nachschnitt` (mit `neu_geschnitten: false`) dazu; dafür muss die Quelle nicht
  im Puffer liegen.
- Das Ende bleibt das des Bot-Clips (`clips.quelle_ende_s`), damit ein von Claude gewähltes Ende erhalten bleibt.

**Reihenfolge im Betrieb (nur mit deinem OK):** Code auf den Mini, zuerst `pipeline momente nachschneiden --probe`
(zeigt Anzahl, Anlauf, fehlende Quellen), dann echt, am besten außerhalb der Spielzeit (Neu-Kodierung, rund 37
Momente). Die neuen Dateien (grob 1–2 GB) sichert der tägliche Abgleich mit `sessions/` ins Lager.
