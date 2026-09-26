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

## L1–L6 · Lernschleife „Publikum“ (Spec §16 E21–E26, freigegeben im Startauftrag, 25.09.)
Arbeitstitel L1–L6 statt E21–E26: E21 ist schon „Multikills am Stück“, und Regisseur 2.0 vergibt eigene Nummern.
Geplant war, sie beim Zusammenführen mit R2.0 in fortlaufende E-Nummern umzubenennen (Annahme A2). **Florian 25.09.:
„L1–L6 ok“** – die Nummern bleiben so, auch nach dem Merge mit main (bc314f5); kein Umnummerieren.
Spec: `docs/superpowers/specs/2026-09-25-lernschleife-publikum-design.md`, Bedienung: `docs/PUBLIKUM.md`.
- **L1** (Spec E21) Publikum ist das Hauptsignal; zwei Schleifen (dein Urteil täglich, Publikum wöchentlich) speisen
  dieselben Lerner.
- **L2** (Spec E22) Eine Moment-Bewertung für Clip-Bot und Regisseur; neue Merkmale aus dem vorhandenen Replay-JSON;
  Lernen bleibt linear, paarweise, gedeckelt.
- **L3** (Spec E23) Rezepte als Stellschrauben mit Stufen; jeder dritte Post ein Experiment nach Unsicherheit; du gibst
  jeden Post frei.
- **L4** (Spec E24) Zahlen zuerst per Screenshot (claude -p, Leserecht), Display API im Sandbox-Modus als zweite
  Stufe; Wiedergabezeit gibt es nur aus der App.
- **L5** (Spec E25) Der Wochen-Analyst schlägt nur vor (Schema-geprüft), entscheidet nie; jede Hypothese wird per
  Knopf getestet.
- **L6** (Spec E26) Der Clip-Bot wird minimal angefasst: `posts` aus `/link`, Erwartungs-Zeile, Battle-Paarung nach
  Unsicherheit. n8n-Vertrag und Session-Schnittliste unverändert.

## Export-Vertrag mit Regisseur 2.0 (Lernschleife Stufe 1, 25.09.)
CLAUDE.md „Export“ plant für R2.0 Stufe 3 einen 📦 im Lern-Bot und `pipeline export` nach `/srv/puffer/export/<name>/`.
Damit daraus nicht zwei Knöpfe, zwei Callback-Präfixe und zwei Dateien werden:
- Genau **ein** 📦 = `pk:<eid>:` aus `lernbot_paket` (eingehängt im x-Zweig von `lernbot.bei_klick` über
  `knoepfe_nach_fertig`).
- Ordner `<wurzel>/<[publikum].upload_ordner = "export">/<name>/`, Datei `entwurf.UPLOAD_DATEI` = `<name>_upload.mp4`,
  Pfad in `entwuerfe.upload_pfad` (das „Fertig-Video“).
- R2.0 Stufe 3 **erweitert** `lernbot_paket.baue_paket` und `entwurf.upload_fassung` (nummerierte Einzelclips daneben,
  tägliche Sicherung von `export/` ins Lager) und baut keinen zweiten Weg.

## Annahmen im Sprint Lernschleife
Startauftrag §6 Regel 6: Wo eine Frage den Bau blockiert hätte, steht hier die getroffene Annahme. **Offen, bis
Florian sie auflöst** – was er schon entschieden hat, ist mit „→ entschieden (Florian 25.09.)“ markiert (A2, A10,
A27, A28, A40; Rückfragen R3–R5 unten). Die Nummern sind dieselben wie im Plan Stufe 1
(`docs/superpowers/plans/…-stufe-1.md`) und im Code („Annahme A11“ usw.); Stand nach der Nachbesserung durch das
Prüfer-Panel und nach Florians Antworten (`docs/SPRINT-LOG-LERNSCHLEIFE.md`).

### Stufe 1 – Plan (A1–A34) und Panel (A35–A40)
- **A1 Score-Zeitpunkt:** `bewerten` setzt den Score erst, wenn der Post ≥ `alter_tage` (7) alt ist, mit der Messung am
  nächsten an Tag 7 (nur Messungen ≥ 3 Tage) – sonst fröre ein täglicher Lauf den Score an Tag 3 ein (Spec §5 vs.
  §6.1/§14). Abnahme mit `alter_tage = 3`: Score 0 („Basis zu klein“), diese Posts behalten ihre Tag-3-Messung.
- **A2 Entscheidungsnummern:** Arbeitstitel L1–L6 (= Spec E21–E26), ~~fortlaufende E-Nummern beim Merge mit R2.0~~
  → entschieden (Florian 25.09.): „L1–L6 ok“, die Nummern bleiben.
- **A3 /link im Lern-Bot** nimmt `41` und `e41`.
- **A4 Ein 📦-Knopf** (Export-Vertrag oben); die Sicherung von `export/` ins Lager kommt mit R2.0 Stufe 3.
- **A5 Nur 👍-Entwürfe** bekommen Paket, Häkchen und Post („kein Short ohne deine Freigabe“).
- **A6 Übergangs-Rezept für Entwürfe (bis Stufe 3):** hook `aufbau`, laenge nach Dauer, tempo `beat1` bei
  beats_pro_schnitt 1, sonst `beat2`, machart `regie`, experiment false. Clip-Posts wie Spec (`tempo none`).
- **A7 Längen-Stufen:** Grenzen in der Mitte der Spec-Lücken: ≤ 22,5 s kurz, ≤ 36 s mittel, sonst lang.
- **A8 Dauer eines Clip-Posts** aus der DB über `shorts.gesamtdauer(Clip-Länge)` – ohne Dateizugriff im Bot.
- **A9 Plattformen:** Posts nur für `[publikum].plattformen = ["tiktok"]` (Spec §3); YouTube per Konfig zuschaltbar;
  clip-battle.de nie.
- **A10 Fehlende Zähler** zählen im Engagement als 0 (Vermerk „Engagement unvollständig“); Messungen ohne Views zählen
  nicht für den Score. Für Hand-Posts → entschieden (Florian 25.09., R4): Die Hand-Eingabe kann Kommentare, Shares,
  Saves mitliefern; nur wo sie fehlen, gilt weiter die 0 mit Vermerk.
- **A11 Basis** = die jüngsten 20 bewerteten Posts derselben Plattform, die **vor** dem Post gepostet wurden; unter 5
  Posts Score 0 („Basis zu klein“), genug Posts, aber unter 5 r-Werten → wie „ohne Wiedergabe“ (0,6/0,4, „Basis ohne
  Wiedergabe“).
- **A12 Knöpfe ohne #Nummer:** die 5 jüngsten Posts ohne Messung in den letzten 24 h.
- **A13 Zusätzliche Callback-Daten:** `pt:<eid>:t|y` (Häkchen Entwurf), `pm:<post_id>:ok|hand` (Rückfrage).
- **A14 Upload-Fassung** über `rendere(volle_aufloesung=True, crf=20, kbit_max=…)`; VA-API ohne crf mit `-b:v`;
  bis 3 Versuche mit 0,75 · benutzter Rate.
- **A15 Ruhezeit im Lern-Bot:** eigener Filter `LEISE_LERN_MELDUNGEN = ("publikum:", "woche:")`.
- **A16 Uhrzeit** des täglichen Laufs nur im Timer (kein `[publikum].uhrzeit`, Spec §12).
- **A17 Claude im Lern-Bot-Dienst (nach dem Panel geändert):** Drop-in `ProtectHome=read-only` +
  `CLAUDE_CONFIG_DIR=/var/lib/clip-pipeline/claude` + `DISABLE_AUTOUPDATER=1`; eigene claude-Anmeldung des Dienstes.
  Das Home bleibt schreibgeschützt (dort liegen `authorized_keys` mit der Sperre des n8n-Schlüssels und die
  claude-Datei, die `decide` ohne Schutz startet). Voller Pfad in `[decide].programm` nur, wenn claude unter /home
  liegt (ermittelt mit `sudo -iu pipeline command -v claude`, nicht angenommen). Annahme dabei: claude schreibt mit
  `CLAUDE_CONFIG_DIR` nichts ins Home (geprüft mit claude 2.1.281 im Container, 🏠 am Mini). Rückfall
  `screenshot_claude = false`. (R2.)
- **A18 claude_aufruf** ist die gemeinsame Hilfe für neue Aufrufe; `decide`/`stimmung` bleiben vorerst, die
  Wochenzahl zählt deshalb nur neue Aufrufe.
- **A19 /publikum** nur im Lern-Bot.
- **A20 Neue Lern-Bot-Tests** in eigenen Dateien (Spec §13 sagt „test_lernbot.py erweitert“) – wegen paralleler Pakete
  und R2.0.
- **A21 Wartende Bilder** in `tempfile.mkdtemp` (im Dienst PrivateTmp), nie im Puffer; gelöscht nach Auswertung bzw.
  nach 10 min („Nie löschen“ gilt für Rohdaten/Clips).
- **A22 Stolperdraht** vom R2.0-Branch byte-gleich per `git show 0f91d98:<pfad>`.
- **A23 Keine neuen Secrets** in Stufe 1; `.env.example` unverändert.
- **A24 Migration** über `db.MIGRATIONEN` in `db.verbinde` (Spec nennt `db.migriere`, das es nicht gibt).
- **A25 Schema-Ort** `src/clip_pipeline/schemas/publikum.schema.json`, tolerant (Zusatzfelder erlaubt); die
  100-%-Regel steht nur in `pruefe_plausibel` (Rückfrage statt Ablehnung).
- **A26 Nur Shorts** bekommen Paket, Häkchen und Post (ein Zusammenschnitt hätte ≈ 1 Mbit/s, kein Publikumssignal).
- **A27 Entwurfs-Checkliste** nur für Post-Plattformen, ohne clip-battle.de → entschieden (Florian 25.09., R5): keine
  clip-battle.de-Checkliste für Entwürfe; bleibt, wie gebaut.
- **A28 Hand-Eingabe** wird wie ein Screenshot gegen die letzte Messung geprüft; `#17 1240 61 6.8 34` geht ohne Bild
  – seit Florians Antwort (R4) auch mit sieben Werten `#17 1240 61 6.8 34 3 5 2`, geprüft werden dann alle fünf
  Zähler.
- **A29 Ein offener Vorgang** im Lern-Bot (nach dem Panel präzisiert): Ein neues Foto bzw. ein neuer „#17 …“-Text
  ersetzt ihn, ein altes Bild wird sofort gelöscht, und der Bot sagt, was verworfen wurde (Bild, Rückfrage – „NICHT
  gespeichert“ – oder Hand-Eingabe); kein Hinweis bei einer Korrektur desselben Posts. Klicks, die nicht passen →
  „Schon erledigt.“ (bleibt auch für den Doppelklick nach ✅ richtig).
- **A30 Bildformate:** Foto (JPEG) sowie JPG/PNG/WebP als Datei; HEIC → „Bitte als Foto schicken“.
- **A31 Upload-Fassung nur im getrennten Betrieb** (E19), sonst KonfigFehler.
- **A32 Kein Sitzungsverlauf:** `claude -p --no-session-persistence` (sonst ein Bildarchiv unter ~/.claude/projects).
- **A33 Ende-zu-Ende** in eigener Datei `tests/test_ende_zu_ende_publikum.py` (wie A20).
- **A34 Claude-Wochenzahl** bis Stufe 3 als letzte Zeile von `/publikum` (Spec §12 nennt `/lernstand`).
- **A35 Clip-Bot: Häkchen, Link und Post in EINER Transaktion.** Scheitert der Post aus fachlichem Grund (POST_FEHLER),
  bleibt auch das Häkchen weg; der Bot nennt den Grund, derselbe Knopf geht nach der Behebung nochmal („lieber ein
  sichtbarer Fehler als ein Post, der still fehlt“). Die Checkliste nennt je Post die Post-Nummer (für „#17“ am
  Screenshot). Beim Altbestand zählt das erste Häkchen als `gepostet_utc`. Spec §10.4/E26 sagen nur „zusätzlich“ –
  niedrig priorisierte Frage an Florian: ist „kein Häkchen ohne Post“ so gewollt? Notausgang: `docs/PUBLIKUM.md`.
- **A36 „Reaktionen je View“** statt „Likes je View“ (Spec §11.1) für die Komponente e in Bot, Doku und später im
  Wochenbericht – e enthält Likes, 2 · Shares, Saves und Kommentare; der Name widerspräche sonst den eigenen Zahlen.
- **A37 Zahlen in Bot-Texten** an einer Stelle (`publikum.anzahl_text`, `dezimal_text`, `SYMBOLE`): Tausender mit
  schmalem geschütztem Leerzeichen (U+202F), auch in den Verstößen der Rückfrage („Views gesunken: 2 000 → 1 240“);
  „ganz angesehen“ mit 🏁 statt ✅ (✅ ist der Knopf „Stimmt“/„erledigt“).
- **A38 Knöpfe gehören zu ihrer Nachricht:** `pl:`/`pm:` gelten nur aus der Nachricht, die für genau diesen Vorgang
  gefragt hat (message_id im Vorgang). Die Callback-Daten bleiben wie in Spec §7.1; eine Vorgangsnummer darin wäre
  nach einem Neustart des Bots nicht sicher. Annahme: Updates laufen nacheinander (kein `concurrent_updates`).
- **A39 Claude-Zählung:** Gezählt wird nur, was gegen das Abo lief (`ClaudeAntwort.gestartet`). Kein claude gefunden,
  Programm startet nicht (OSError), Schema/Programm nicht eingestellt → zählt nicht; ein Timeout zählt (claude lief).
- **A40 Hand-Eingabe mit Einheiten:** Bitte und Fehlertexte nennen `Views Likes Ø-Wiedergabe-in-Sekunden
  Ganz-angesehen-in-%` (`publikum.HAND_FORM`); „0:07“ oder „7s“ → „bitte in Sekunden“; „34%“ wird als 34 gelesen.
  Seit R4 ergänzt um „optional dahinter Kommentare Shares Saves“ – Bitte und Fehlertext sind jetzt EIN Text
  (`publikum.HAND_HINWEIS`, mit beiden Beispielen).

### Stufe 1 – Florians Antworten (25.09.) und was dabei neu angenommen wurde
- **R3 → MAD-Minimum je Komponente** (entschieden ist das Prinzip „je Komponente“; die drei Werte sind Annahme A44):
  neue Tabelle `[publikum.mad_minimum]`. Die Formel steht weiter genau einmal (`publikum.robust_z`, Minimum als
  Parameter); `score_fuer` gibt je Teil sein Minimum mit und schreibt die benutzten Minima in `score_teile`
  (`"mad_minimum": {"r", "e", "v"}`). Fehlt die Tabelle oder ein Teil, oder ist ein Wert keine Zahl bzw. ≤ 0 →
  KonfigFehler (CLI Exit 2), wie bei den anderen `[publikum]`-Schlüsseln.
- **R4 → Hand-Eingabe erweitert:** `views likes wiedergabe voll%` bleibt gültig; optional dahinter
  `kommentare shares saves` (je Zahl oder „–“). Gilt für die Antwort nach ✏️, den Text „#17 …“ ohne Bild, `/hilfe` und
  die Bitte um Hand-Eingabe; die Plausibilität (nicht sinken) gilt auch für die drei neuen Felder.
- **R5 → keine clip-battle.de-Checkliste** für Entwürfe (A27 bleibt).
- **L1–L6 ok** → die Arbeitstitel bleiben (A2).
- **Stufe 1 kommt jetzt über einen PR nach `main`** (Florian will sie sofort einspielen): `docs/PUBLIKUM.md`,
  Installation, geht davon aus – `git pull` aus `main` wie bisher, nicht erst nach Stufe 5.
- **A41 Hand-Eingabe: genau 4 oder 7 Werte.** 5 oder 6 Werte werden abgelehnt (mit der Erklärung), statt die fehlenden
  hinten als unbekannt zu nehmen: Sonst landete z. B. eine Shares-Zahl still bei den Kommentaren, wenn man die
  Reihenfolge verwechselt. Wer nur einen der drei kennt, schreibt „–“ für die anderen (`… 34 3 – 2`).
- **A42 Dieselbe Konfig-Prüfung für `[publikum.gewichte]`:** Gewichte und MAD-Minima liest eine Hilfsfunktion
  (`publikum._je_teil`). Folge: Fehlt ein Gewicht, gibt es jetzt KonfigFehler (Exit 2) statt eines KeyError, der als
  Fehler EINES Posts zählte (Exit 1) – eine kaputte Konfig betrifft ja jeden Post.
- **A43 Minima auch bei „Basis zu klein“ in `score_teile`:** so hat jeder gespeicherte Score dieselben Felder, und man
  sieht auch dort, mit welcher Konfig gerechnet worden wäre.
- **A44 Werte der MAD-Minima** (offen, bis Florian sie bestätigt – R3 hat nur „je Komponente“ entschieden):
  `wiedergabe = 0.05` (wie die Spec), `engagement = 0.005`, `reichweite = 0.1`; Gründe je Wert in
  `config/pipeline.toml`. Engagement: e liegt um 0,05 mit Streuung ~0,01 – das pauschale 0,05 der Spec machte einen
  Ausreißer fast wirkungslos (e 0,09 gegen 0,05 … 0,09: z 0,27 statt 1,35). **Achtung Reichweite:** 0,1 ist
  *größer* als das 0,05 der Spec, dämpft also kleine Reichweiten-Unterschiede (1 100 gegen um 1 000 Views: z 0,64
  statt 1,28, `tests.test_publikum`) – das ist eine eigene Wahl des Bauers, nicht Teil der Frage R3. Begründung:
  v = ln(1 + Views) streut in ganzen Einheiten, 0,1 ≈ 10 % mehr Views gilt als Zufall der TikTok-Verteilung.
  Bestätigt Florian 0,05 für die Reichweite, ändert sich nur `config/pipeline.toml` (und der Test).

### Stufe 1 – Annahmen der Bauer (Pakete a–g, kurz)
- **a1** Merkmale eines Entwurf-Moments: mit clip_id aus `clips.merkmale`, sonst aus `momente.merkmale`, sonst {}; ein
  Moment mit Jump-Cut steht nur einmal in der Liste. **a2** Fehlt `beats_pro_schnitt`, gilt 1 (`regie` wird nicht
  importiert, kein Import-Kreis). **a3** Views/Likes der Hand-Eingabe ganzzahlig („1.240“ → Fehler), nan/inf abgelehnt.
  **a4** Basis < 5: z_r = 0 nur mit gemessenem r, sonst None; Vermerk „Basis zu klein“ – nach dem Panel ohne r
  zusätzlich „ohne Wiedergabe“ (Spec §6.4). **a5** In `bewerte_alle` sind nur ValueError/KeyError/TypeError Fehler
  eines Posts; sqlite3.Error und KonfigFehler brechen den Lauf ab. **a6** `posts_ohne_messung` listet auch bewertete
  Posts (Spec wörtlich).
- **b1** Wartet eine Rückfrage, gelten frei geschickte Zahlen als Korrektur von Hand. **b2** 10 min gelten für alle
  drei Vorgangsarten, jeder Schritt startet die Frist neu. **b3** Kommt Claudes Ergebnis erst nach dem Verwerfen, wird
  es nicht gespeichert, der Aufruf zählt trotzdem. **b4** Unbekannte #Nummer oder kein Post ohne Messung → Bild sofort
  verworfen, mit Hinweis. **b5** Bei Dateien zählt der mime_type vor dem Dateinamen. **b6** ~~Rückfrage/Hand-Eingabe
  werden still ersetzt~~ – nach dem Panel mit Hinweis (A29). **b7** Hinweise aus claude_aufruf/lies_zahlen gehen an
  dich (ohne Rohantwort und Zahlen; bei fehlender Prompt-Vorlage mit deren Pfad). **b8** Fehlt `[decide].programm`:
  Hinweis statt verstecktem Standard; übrige `[lernbot]`-Schlüssel sind Pflicht (KonfigFehler → Hinweis).
- **c1** Upload-Fassung prüft zusätzlich `pruefe_getrennt(mit_lager=False)` (Marke `.clip-puffer`). **c2** Bestehende
  Schlüssel mit demselben Standard wie der übrige Code; `[publikum].upload_ordner` ist Pflicht. **c3** Caption eines
  Entwurfs nur aus Fakten (Momente, Kill-Typ aus `max_gruppe`, Quellenangabe aus der Schnittliste). **c4** Caption vor
  dem Rendern; die Sperre umfasst auch „schon gerendert“. **c5** Ein zweites 📦 nach fertigem Paket schickt es nochmal
  ohne Render. **c6** Bekannte Fehler gehen mit Text (≤ 300 Zeichen) an dich, lokale Pfade gelten als unkritisch,
  unerwartete nur mit Typ. **c7** `/link` prüft erst den Link, dann `paket_erlaubt`.
- **d1** Das Häkchen legt den Post unabhängig vom Clip-Status an; `/link` lehnt nicht freigegebene Clips weiter ab.
  **d2** `gepostet_utc` = erstes Häkchen dieser Plattform (A35). **d3** Nur ValueError/KeyError/KonfigFehler werden zur
  Meldung „nicht abgehakt“. **d4** Post-Nummer als eigene Zeile unter der Checkliste. **d5** Der /link-Hinweis wird
  HTML-maskiert.
- **e1** Tests für `lernbot_publikum` in `tests/test_publikum_cli.py`. **e2** Datum im Meldungs-Schlüssel = UTC-Datum
  des Laufs. **e3** Zweite Zeile in der Meldung bei „Basis zu klein“. **e4** `/publikum` über 30 wird still gekürzt.
  **e5** KonfigFehler in `publikum bewerten` → Exit 2. **e6** Lern-Bot läuft laut Sprint-Log aus `/opt/clip-regie`,
  PUBLIKUM.md nennt beide Checkouts. **e7** `pip install -e '.[whisper]'` wie PUFFER.md R3, keine neuen Pakete.
- **f1** Der Stolperdraht prüft alles, was `git add -A` committen würde. **f2** `lokal.toml`/`uebertragung.psd1` am
  Dateinamen erkannt. **f3** `.env.<irgendwas>` gilt als geheim (außer `.env.example`). **f4** SSH-Schlüsselnamen
  inkl. FIDO-Varianten und `n8n_pipeline`, `pve-big`. **f5** In `.env.example` ist jede aktive Zeile ohne `NAME=`
  ein Befund.
- **g1** Ende-zu-Ende durch die echten Telegram-Handler. **g2** Je Beteiligtem eine eigene DB-Verbindung. **g3**
  Getrennter Betrieb wie auf dem Mini, Wecken/Netz gepatcht. **g4** Eine feste Uhr für alle Module (auch `db.jetzt`).
  **g5** Alter DB-Stand aus `schema.sql`, `regie.sql`, `lager.sql`. **g6** Zweiter Lauf = Timer am nächsten Tag.

### Rückfragen an Florian (Stufe 1, nach Wichtigkeit) – R3–R5 entschieden am 25.09., R1, R2, A35 und A44 offen
- **R1 Öffentliches Repo – persönliche Daten:** Epic-ID, MAC, Heimnetz-IPs durch Platzhalter ersetzen? (R2.0 hat
  Epic-ID und MAC inzwischen als Variable – beim Merge prüfen.) Historie umschreiben nur mit ausdrücklichem OK.
- **R2 Zweite claude-Anmeldung für den Lern-Bot-Dienst** in `/var/lib/clip-pipeline/claude` (A17) – ok? Die frühere
  Variante „Dienst darf ins Home schreiben“ ist nach dem Panel verworfen.
- ~~**R3 MAD-Minimum 0,05** gilt für alle Komponenten gleich und dämpft das Engagement (typischer MAD 0,01–0,02) um
  Faktor 2,5–5. Ein Minimum je Komponente in `[publikum]`?~~ → **entschieden (Florian 25.09.): ja, je Komponente**
  (`[publikum.mad_minimum]`). Die Werte wiedergabe 0,05 · engagement 0,005 · reichweite 0,1 sind **Annahme A44** –
  bitte bestätigen, vor allem reichweite 0,1 (dämpft stärker als die Spec).
- ~~**R4 Hand-Eingabe** kennt nur Views/Likes/Wiedergabe/voll%; Kommentare, Shares, Saves zählen dann 0 (A10).
  Optional hinten anhängen – oder e ohne diese Zähler rechnen?~~ → **entschieden (Florian 25.09.): optional hinten
  anhängen** (`… 34 3 5 2`, A41).
- ~~**R5 clip-battle.de für Entwürfe** als Merker in der Checkliste (ohne Post)?~~ → **entschieden (Florian 25.09.):
  nein**, keine clip-battle.de-Checkliste für Entwürfe (A27).
- Niedrig: **A35** „kein Häkchen ohne Post“ im Clip-Bot so gewollt?

### Stufe 2 – Plan (S2-A1–S2-A19, Plan `docs/superpowers/plans/2026-09-25-lernschleife-stufe-2.md`)
Stand beim Vertrag (25.09.). Die Bauer-Annahmen und die Befunde des Panels kommen am Ende der Stufe dazu.
- **S2-A1** `kommentar` und `lautstaerke` bleiben in MERKMALE → 17 Merkmale; `kommentar` ist weiter immer 0.
- **S2-A2** `verbleibend` aus `eliminierungen` (knock = false) statt aus dem Killfeed (der hat kein `t_ms`).
- **S2-A3** `phase` bezieht sich auf die erste Aktion des Moments und die Länge MEINES Replays.
- **S2-A4** `[merkmale.waffen]` startet leer: alles zählt als `sonstige`. Grund: In FortniteReplayReader 3.1.0 ist
  `GunType` nur ein gelesenes Byte ohne Namen (`elim.GunType = archive.ReadByte()`, am 25.09. im Quellcode
  nachgesehen) – es gibt keine Enum zum Vorbelegen. Neue Zahlen kommen als eine Sammelmeldung je Session (Vermerk je
  Zahl, nie verschickt), die die Ruhezeit abwartet; kalibriert wird am echten System mit `pipeline replay` (🏠).
  Nach dem Panel: Solange alle drei Listen leer sind, bleiben `sniper`/`nahkampf` **unbekannt** (fehlen), damit
  `merkmale nachtragen` sie nach der Kalibrierung nachholt. Teilweise kalibriert oder neue Nummern nach einem
  Fortnite-Update: zählen als `sonstige` (gemessen 0) – dafür gibt es die Sammelmeldung.
- **S2-A5** Neue Merkmale ändern `punkte`/`begruendung` nur bei Clips im Status `vorbewertet`.
- **S2-A6** `mic_stand` wird gesetzt, wenn Whisper lief (`lachen` vorhanden) oder die Aufnahme sicher kein Mikro hat
  (`mikro_spur` None, kein `fehler`); Messfehler zählen nicht als vollständig.
- **S2-A7** Mic-Schritt als losgelöster Kindprozess aus `render` (nice 15, `--konfig` der render-Konfig, Log
  `mikro.log` neben der Datenbank, `mic_je_lauf = 3`). Nach dem Panel richtiggestellt: `clip-sitzungen` misst nur
  einmal je Spielabend Clips ohne momente-Zeile; unvollständige Mic-Werte holt nur `stimmung --clips` nach (das
  nächste render nimmt Reste aller Sessions mit, sonst von Hand). Eigene systemd-Einheit: Rückfrage S2-R3.
- **S2-A8** Der Mic-Schritt legt neue momente-Zeilen ohne Claude an; sie bekommen keine spätere Claude-Nachprüfung.
- **S2-A9** Abweichung von Spec §8.1: Datei-Momente (ohne Clip) bekommen keine Replay-Merkmale, kein `laenge`, kein
  `lautstaerke` – sie haben weder Match noch Kills (`stimmung.momente_aus_dateien`).
- **S2-A10** Publikums-Quote wird ab dem ersten Paar angezeigt; die Schranke prüft sie erst ab
  `[lernen].mindest_publikum_paare` (10).
- **S2-A11** „Du magst X, das Publikum Y“ über das Vorzeichen des mittleren Merkmals-Unterschieds je Quelle.
- **S2-A12** Nach `pipeline publikum bewerten` mit neuen Scores wird neu gelernt (`lernen.aktualisiere`). Die eine
  Zeile in `cli._cmd_publikum` wandert mit Paket G nach Paket D (Stufe 2 braucht sie für die Publikums-Paare).
- **S2-A13** „Letzte 50 gesendete“ = nach id (kein Sende-Zeitstempel); Standardisierung robust wie der
  Publikums-Score (`publikum.robust_z`, eine Formel, eigenes Minimum `[erwartung].mad_minimum`).
- **S2-A14** Zusammenschnitt-Entwürfe bekommen auch eine Erwartung und zählen bei den Entwürfen mit.
- **S2-A15** Erwartungs-Treffer „letzte 20“ nach `erwartungen.erstellt`.
- **S2-A16** MAD-Minimum je Komponente des Publikums-Scores: **nicht in dieser Sitzung** – Paket G (MAD-Minimum,
  erweiterte Hand-Eingabe) baut eine andere Sitzung in Stufe 1 ein. Stufe 2 fügt nur den Parameter `minimum` an
  `publikum.robust_z` hinzu (Standard wie bisher).
- **S2-A17** „Fehlt = unbekannt“: beim Lernen wird ein neues Merkmal, das auf einer Seite eines Paars fehlt, nicht
  verglichen; beim Bewerten zählt es 0. Ohne Mikro gelten die Mic-Werte als gemessen = 0.
- **S2-A18** Fehlt einer momente-Zeile die Mic-Analyse, holt der Mic-Schritt nur die Mic-Werte nach; Stimmung,
  Sicherheit und Quelle bleiben. Zeilen mit Messfehler werden nicht wiederholt.
- **S2-A19** Neue Testdateien je Paket statt Erweiterung von `test_ende_zu_ende.py`/`test_publikum.py`.

### Stufe 2 – Panel und Bauer (nach dem Prüfer-Panel, 25.09.)
- **S2-A20 „Unbekannt“ hat Vorrang vor „Rest 0“ (Befund K-1/S-1):** Ohne lesbares Replay liefert `aus_replay` nichts,
  beim Rekorder-Rückfall nur `platzierung` (falls bekannt); Clips, deren Kill-Zeiten keinem Ereignis zugeordnet werden
  (vor der Kill-Regel vom 24.09. gerendert, K-5), nur `platzierung` und `phase` plus Log-Warnung. Die Plan-Zeile A2
  „Rest 0“ widersprach Leitplanke 4/S2-A17 – gemessene Nullen hätte `merkmale nachtragen` nie repariert.
- **S2-A21 Datei-Momente beim Lernen (K-3):** Fehlen `laenge`/`lautstaerke` auf einer Seite eines Paars, werden sie
  nicht verglichen (wie die neuen Merkmale). Im Regisseur bleibt S2-A9.
- **S2-A22 Mic-Messfehler (K-2):** Scheitert Lautheit, WAV oder Whisper, bekommt der Moment `fehler` und wird nicht
  wieder gewählt; der Lauf geht weiter. Beim Nachholen bleibt die alte Zeile, nur `fehler` kommt dazu.
- **S2-A23 Erwartungs-Anzeige:** Unter 50 % zeigt der Bot das Gegenteil mit dessen Sicherheit („🗑️ 65 %“, im
  Lern-Bot 👎); „· alle …“ erscheint erst ab mehr als 20 Urteilen (vorher gleich „letzte 20“).
- **Abweichungen vom Vertrag (bewusst, ohne Kreis-Import, AST-Test grün):** `lernen` importiert `publikum`
  (`VERMERK_BASIS_ZU_KLEIN`, `einstellung` – keine zweite Wahrheit); `stimmung` importiert `merkmale`; `mikro`
  importiert `zeit`; neues Feld `Ergebnis.mindest_publikum_paare` (die Anzeige braucht die Zahl ohne Konfig);
  `erwartung.anzeige` für dasselbe Zeilenformat in beiden Bots; `nachtragen` liefert `{"clips", "geaendert"}`.
- **Bauer-Annahmen, kurz:** A1 `roh_score` summiert per Schleife in der Reihenfolge von MERKMALE, nicht mit `sum()`
  (seit Python 3.12 kompensiert – `bewerte` bliebe sonst nicht byte-gleich) · A2 `verbleibend` am Erledigen gezählt,
  gleiche Zeitpunkte zählen mit; `opfer_bot` null zählt nicht als Bot; Vermerk-Text „Waffen-Nummer n gemeldet“ ·
  B `mic_stand` wird nur gesetzt, solange er leer ist; `[merkmale].mic` fehlt = an; Transkript per COALESCE ·
  C `_balken` normiert vom Minimum aus (auch rein positive Bögen) · D max_paare nach dem jüngeren Post des Paars,
  Toleranz 1e-9 beim Score-Abstand, kaputte Posts werden geloggt und übersprungen · E L2-Term l2/2·(a²+b²); scheitert
  das Festschreiben, geht das Video trotzdem raus („noch keine“).

### Offene Rückfragen an Florian (Stufe 2, nach Wichtigkeit – der Bau wartet nicht)
- **S2-R1 MAD-Minimum des Publikums-Scores** (vor dem ersten echten Score): gehört inzwischen zu Paket G (andere
  Sitzung, Stufe 1).
- **S2-R2 Regisseur und Stimmung:** Der feste Stimmungswert (episch +3 … chill +0,5) zählt laut Spec nicht mehr zur
  Momentstärke; Datei-Momente ohne Kills haben dadurch Stärke ≈ 0 statt 0,5–3. So lassen, oder als Stimmungs-Bonus in
  den Punkten behalten?
- **S2-R3 Mic-Schritt:** Kindprozess aus render (heute) oder eigene systemd-Einheit? Nötig, falls logind
  `KillUserProcesses=yes` meldet (PUBLIKUM.md S4).
- **S2-R4 Datei-Momente** ohne Replay-Merkmale und Längen-Abzug (S2-A9): später über die Aufnahmezeit einem Match
  zuordnen?
- **S2-R5 `kommentar`** ist immer 0 und wird durch `mic_*` ersetzt – streichen (16 Merkmale)?
- **S2-R6 Alte Clips:** Punkte/Begründung schon gesendeter Clips beim Nachtragen neu rechnen (Bot zeigte andere
  Zahlen) oder nur die Merkmale fürs Lernen (heute)? Dazu: sollen schon gemessene `sniper`/`nahkampf` nach einer
  späteren Kalibrierung neu gerechnet werden?
- **S2-R7 Publikum gegen dich:** Ab wie vielen Publikums-Paaren darf das Publikum dein Modell blockieren (heute 10)?

### Stufe 2 – Florians Antworten (25.09.)
- **S2-R2:** Stimmungswert bleibt aus der Momentstärke draußen (wie gebaut).
- **S2-R4:** Datei-Momente später einem Match zuordnen – tendenziell ja, keine Eile.
- **S2-R5:** `kommentar` bleibt (immer 0 → ändert das Lernen nicht, Streichen brächte nichts).
- **S2-R6:** Ja – `merkmale nachtragen` rechnet Merkmale, Punkte und Begründung aller Clips neu, auch gesendeter und
  nach einer Waffen-Kalibrierung. **S2-A5 ist damit aufgehoben.**
- **S2-R3:** systemd. render schreibt nur `mikro.anstoss`; `clip-mikro.path` startet `clip-mikro.service`
  (`stimmung --clips`, Nice 15, MemoryMax 3G), `clip-mikro.timer` alle 30 min. Ersetzt den Kindprozess
  aus S2-A7 (und damit die logind-Frage).
- **Stimmen (26.09.):** Bewertung mit allen Stimmen; im Upload Mikro/Chat (ab Spur 1) nur bei Lachen, Jubel,
  lauten Mikro-Spitzen oder Stimmung „lustig“. Annahme: ohne Mic-Analyse (nichts bekannt) → nur Spielton;
  schon gerenderte Shorts (`short_pfad`) werden nicht neu gerendert.
