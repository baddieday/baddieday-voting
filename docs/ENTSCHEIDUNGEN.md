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
   → 08.10.: OK von Florian, B5 umgesetzt und ab Werk an („Nichts mehr von Hand“, Stufe 2, Annahmen N9–N15, nach der Prüfung N38–N42).
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
  Wochenzahl zählt deshalb nur neue Aufrufe. ~~Ersetzt durch B4 (27.09.)~~: `decide`, `stimmung` und
  `caption.ki_beschreibung` rufen jetzt ebenfalls `claude_aufruf.frage_json` auf (vorher ohne `stdin=DEVNULL`,
  ohne `--no-session-persistence`, `caption` sogar fest `"claude"` statt `[decide].programm`) – nur der
  Unterbau ist gemeinsam, keiner der drei zählt `protokolliere` und damit in die Wochenzahl, das bleibt wie
  bisher nur Screenshot und Wochen-Analyst vorbehalten.
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

### Autonomes Publikumslernen (29.09.2026)

- Neue Bewertungen sind optional; Upload, nächste Planung und Lernen benötigen keinen Daumen.
- Publikum trainiert einen getrennten, versionierten Regie-Champion. Historische Bewertungen bleiben als
  schrumpfender Prior erhalten. Ganze Videogruppen werden zeitlich getrennt geprüft; mehrere Messungen sind
  keine zusätzlichen Videos. Cold-Start-Exploration liefert auch vor dem ersten Champion neue Varianten.
- Short 30–75 s (Startziel mindestens 45 s), Zusammenschnitt 75–120 s gelten in Planung und vollständigem Render.
- Plattformzugänge müssen autorisiert sein. TikTok/YouTube werden abgeholt, Instagram wird importiert;
  fehlende API-Metriken bleiben unbekannt. Details und Grenzen: `AUTONOMES_LERNEN.md`.

### Telegram bei Übertragungen (30.09.2026)

- Gaming-PC → Puffer und Puffer → Lager melden Start und Abschluss im bestehenden Clip-Bot, je tatsächlichem
  Übertragungslauf. Leere Timerläufe, Probeläufe und in der Nachtruhe aufgeschobene Abgleiche bleiben still.
- Gezählt werden neu erfolgreich kopierte Videos (`.mp4`, `.mkv`, `.mov`), keine Replays/Bilder oder bereits
  vorhandenen Dateien. Teilfehler und Abbrüche nennen die erfolgreiche Teilmenge.
- Der PC schreibt dauerhafte Laufberichte ohne Zugangsdaten. Bei unerreichbarem Puffer werden sie lokal behalten
  und bei der nächsten aktiven erreichbaren Übertragung nachgereicht. Der Clip-Bot liest nur den lokalen Puffer;
  die Meldungen wecken keinen Host. Nachts kommen sie sofort, aber ohne Benachrichtigungston.

### Auto-Freigabe im Clip-Bot (30.09.2026)

Florian: „nicht jeden Clip per Hand separat freigeben“. Code: `src/clip_pipeline/auto_freigabe.py`, `[auto_freigabe]`.

- **A1:** Standard `modus = "an"` – Florian will es so; weich, umkehrbar, mit Tor und Stichprobe; die DB sichert das Update.
- **A2:** Aussortieren durch den Bot ist immer weich. Nur dein 🗑️ schließt aus Regie, Highlight und Mikro aus
  (`db.hart_verworfen_sql`).
- **A3:** Entschieden wird beim ersten Senden mit den vorhandenen Merkmalen; die Mic-Analyse wird nicht abgewartet.
- **A4:** Auto-Clips werden trotzdem einzeln (ohne Ton) gesendet – für `tg_file_id` (Battles), die Vergleichsbasis der
  Erwartung und den Rückweg direkt am Clip.
- **A5:** Stichprobe deterministisch über `clip_id % stichprobe_jede` (auch für Regel-Fälle), nie ganz aus (mindestens 2).
- **A6:** Korrekturen zählen im Tor als Fehltreffer, Bestätigungen („👍 Stimmt“ auf Sofort-Clips) nicht (wohl beim
  Lernen), Schweigen zählt nie.
- **A7:** `freigabe_quelle` NULL gilt als du; keine Rückfüllung. `/paket` auf einen Auto-Clip lässt `auto` stehen.
- **A8:** ⚙️ ist eine gemeinsame Liste für beide Bots; der Clip-Bot hängt `lernbot_einstellungen.registriere` ein.
- **A9:** Kein Tageslimit für Fragen an dich – die Frist macht jede Antwort freiwillig. Später ggf. ein KATALOG-Eintrag.
- Beim Bau dazu: Die Frist trägt ihre Richtung in `auto_vorschlag` ein, wenn beim Senden keiner stand – so zeigt die
  Bildunterschrift nach deinem Tipp „von dir bestätigt“ bzw. „(Automatik korrigiert)“. „Letzte 7 Tage“ in `/auto`
  zählen nach `clips.erstellt` (es gibt keinen Sende-Zeitstempel außer `vorgelegt`, und der wandert mit /offen).

### Cutter-Maßstab 1.0, Stufe 1 (30.09.2026) – Annahmen bis Florian widerspricht

| Nr. | Annahme |
|---|---|
| A1 | −14 LUFS / −1,5 dBTP für alle Plattformen (YouTube als Sekundärquelle, TikTok/Instagram undokumentiert) |
| A2 | Strobe höchstens 2,5 Hz ist eine Sicherheitsregel (WCAG 2.3.1, TikTok-Warnung) und wird nicht gelernt |
| A3 | Safe-Zone standardmäßig TikTok + YouTube (`[regie.massstab].plattformen`) |
| A4 | Beat-Toleranz 45 ms |
| A5 | Aussortier-Schwelle = Q20 der eigenen Noten, begrenzt auf 40–50; vor 20 gemessenen Entwürfen je Format nur Tore |
| A6 | Alle Kurvenwerte und Startgewichte ohne Quelle sind Erfahrungswerte |
| A7 | `payoff` gegen `hook_staerkster`: das Lernen entscheidet, Stufe 2 löst es über den Hook-Teaser |
| A8 | β = 8, λ = 4, Mindestzahl 15 sind Startwerte ohne Messung |

Beim Zusammenführen (Anbindung) zusätzlich angenommen:
- Ein verletztes Tor deckelt auch die Gesamtnote N (mit KI-Anteil) auf 40, nicht nur M_f.
- `[regie.kritik].schwelle = 0` schaltet das Aussortieren durch den Cutter ganz ab, Tore inklusive (wie bisher
  „0 = aus“). Grund: G6(b) „doppelt“ schlägt auf synthetischem Testmaterial an; ob es auf echten Matches zu streng
  ist, zeigt erst `pipeline massstab --nachmessen`.
- `pipeline massstab --nachmessen` fragt den KI-Cutter nicht neu; ein vorhandenes KI-Urteil (samt `ki_version`,
  bei alten Urteilen NULL) und sein Zeitstempel bleiben, damit es nicht erneut ins Tageslimit zählt.
- `kritik.regeln()` liefert als Wrapper die Plan-Teilnoten des Maßstabs (Schlüssel `hook`, `payoff` …) statt der
  alten Teile `einstieg`, `action` …; der alte Regeltest ist darauf umgestellt.
- `normiert` (aus dem Sidecar) steht in `kritiken.details`; nur solche Entwürfe zählen für die Schwelle.
- Dein ✅ nach 👍/👎 zieht den Maßstab im Hintergrund nach (eigene Datenbank-Verbindung, blockiert den Bot nicht).

### 🥱 = Schnitt (07.10.2026) – Annahmen bis Florian widerspricht
Florian: „Ich tippe ❌ → 🥱 und sehe das gleiche Video mit anderen Schnitten.“ 🥱 heißt: der Schnitt langweilt, die
Szenen sind ok. Code: `szenen.py`, `regeln.neue_fassung`, `geschmack.waehle(anders=…)`, `regie.fassung_kandidaten`.

| Nr. | Annahme |
|---|---|
| Z1 | „Neu“ = in keinem Entwurf je gezeigt (je Szene); still aussortierte Entwürfe zählen nicht als gesehen |
| Z2 | Ersatz nur aus ungesehenen Szenen – auch vom Abend. Eine schon gesehene Abend-Szene kommt als Ersatz nicht zurück; reicht es nicht, kommt kein Video |
| Z3 | Mindestens eine Ersatz-Szene ist Pflicht, sonst wäre es wieder dasselbe Video |
| Z4 | Ersatz früherer Abende nur mit Match (Datei-Momente ohne Match bleiben wie im Abend-Weg draußen) |
| Z5 | Zwei Clips desselben Matches sind nie dieselbe Szene (nur der Puffer überlappt, bis 4,5 s) |
| Z6 | Fail-Momente bleiben beim Szenen-Abgleich draußen (🔥 Viral unverändert) |
| Z7 | Das andere Tempo muss die Segmentlänge um mindestens 15 % in seine Richtung ändern; es geht für diese eine Fassung auch vor dem Publikums-Modell |
| Z8 | Beim zweiten 🥱 bleibt der Abend der des ersten Videos (`parameter.fassung.abend`) |
| Z9 | Ein 👎 mit 🥱 bildet jetzt ein Paar im Cutter-Maßstab (Schnitt-Urteil), bisher nicht |

### Nichts mehr von Hand (08.10.2026) – Plan und Annahmen bis Florian widerspricht
Florian: „Wenn ich alles per Hand einstellen kann/muss, brauchen wir über das Ziel nicht weiter zu reden – es soll
autonom und besser und schneller sein als mit der Hand zu schneiden.“ Bestandsaufnahme: 4 Prüfer (64 Stellen mit
Handarbeit), 3 Pläne, 2 Richter. Jede Stufe ist für sich nutzbar:

1. **Nichts einstellen, nichts nochmal tippen** – umgesetzt am 08.10.
2. **Puffer gibt frei:** Rohvideos älter als 14 Tage, deren Kopie im Lager per Prüfsumme bestätigt ist (Florian 08.10.:
   „Solange alles ins Lager gesynct ist, darf es nach 14 Tagen vom Mini gelöscht werden.“). Keine Auto-Updates
   (Florian: „Nein, ich spiele selbst ein“). Umgesetzt am 08.10. (`lager.gib_frei`, `docs/PUFFER.md`, „B5“).
3. **Ein Weg nach dem ✅:** 2-Wochen-Video nur im Lern-Bot, ✅ legt den TikTok-Post selbst an, eindeutige Zuordnung über
   die TikTok-API, auch Flops bekommen ihre Note, das Update richtet den Zahlen-Abruf ein, 📋 zeigt, wer lehrt.
4. **Nachschub von selbst:** mehr Anlauf vor „zu kurz“ (umgesetzt 08.10., N48–N50); nie gesehene starke Szenen (Florian 08.10.: auffüllen ja –
   höchstens die Hälfte, Anfang vom Abend, höchstens 12 Tage alt; umgesetzt 08.10., N51–N59); Nachtrag bei spät eintreffendem Material (umgesetzt 08.10., N60–N65); ~~jede Szene nur in einem Video (N66–N70)~~, ersetzt durch Abwechslung mit Ermüdung (Florian 08.10.: „die Momente dürfen ruhig öfter und gemischter genutzt werden … bessere öfters zeigen aber nicht permanent“; umgesetzt 08.10., N84–N91; nach der Prüfung N92–N98); Musik lädt
   sich selbst nach (Techno, Hardstyle, Hardcore, Phonk, Brazilian Phonk; umgesetzt 08.10., N71–N74).
5. **Lernen von den Zuschauern:** Publikum lehrt Aufbau, Tempo und Zeitlupe (umgesetzt 08.10., N75–N78); ⏱️/⏳ als
   Grenze mit Richtung (umgesetzt 08.10., N79–N83; nach der Prüfung N97, N99).

Bewusst nicht: „stark“ nach gelernten Punkten (weicht „lieber kein Video als Füllmaterial“ auf), Technik-Tore im
einfachen Modus, eine vierte Geschmacks-Schraube, Auto-Update, Selbstreparatur am Server.

| Nr. | Annahme (Stufe 1) |
|---|---|
| N1 | Im einfachen Modus fest: Clips = neuester Abend, Stil = automatisch, nur starke Szenen (`einstellungen.EINFACH_FEST`, `einstellungen.fest`). Andere alte Werte (z. B. Short-Mindestlänge vom 06.10.) gelten weiter und stehen im 📋. Aus der Tabelle wird nichts gelöscht; /experte wie bisher |
| N2 | Alte Knöpfe aus früheren ⚙️-Nachrichten zeigen im einfachen Modus nur die Übersicht und ändern nichts |
| N3 | Der alte Schalter „Effekte aus“ zählt als Stufe „aus“. 😵 senkt eine Stufe, 🥱 hebt eine (nie über „normal“); „normal“ heißt im einfachen Modus „wie gelernt“ |
| N4 | Merkliste `entwurf_bewertungen.folge`: Bewertung und Regel sofort, der Neubau über die 30-s-Schleife. Höchstens eine offene neue Fassung (die zum neuesten ❌; ältere werden zusammengelegt, ihre Regel gilt). Fehler: neue Versuche nach 10 und 30 min, dann ein Satz. Nach einem Neustart nur Tipps, die jünger als 2 h sind. 🎬 während eines Baus wird gemerkt (nur im Speicher) |
| N5 | ❌ ohne Grund baut nichts neu (❌ = nicht hochladen); neu gebaut wird nach einem Grund |
| N6 | ✅ Hochladen bleibt stehen; ein zweites ✅ schickt dasselbe Paket noch einmal |
| N7 | Abend-Video: scheitert erst das Rendern, holt der nächste Timer-Lauf es einmal auf der CPU nach (nur einfacher Modus). Dauerhafte Fehler (Datei oder Musik fehlt, ungültige Länge) ergeben sofort eine Zeile |
| N8 | Bekannt, selten, nicht behoben: Ein harter Absturz zwischen Rendern und Senden einer Merklisten-Fassung kann nach dem Neustart ein zweites Video ergeben; 🥱 kündigt die neue Fassung an, auch wenn danach „keine neuen Szenen“ kommt |

| Nr. | Annahme (Stufe 2 – Puffer gibt frei, `lager.gib_frei`) |
|---|---|
| N9 | „Älter als 14 Tage“ heißt: die Aufnahme (Dateizeit) UND ihre Bestätigung im Lager. Eine spät vom PC gekommene alte Aufnahme bleibt so noch 14 Tage nach der Bestätigung im Puffer – die Pipeline braucht sie vielleicht noch |
| N10 | Zusätzlich zur Spec wird die Lager-Kopie direkt vor dem Löschen ganz zurückgelesen (Seiten-Cache verworfen) und muss dieselbe SHA-256 haben wie bei der Bestätigung – danach ist sie die einzige Kopie. Kostet je Lauf etwa die neu freigegebene Menge (~2,6 GB je Spieltag) Lesen über NFS, während pve-big ohnehin wach ist |
| N11 | Probe = der erste Abgleich, bei dem es etwas freizugeben gibt (nicht einfach der erste nach dem Update) – so siehst du echte Zahlen, bevor zum ersten Mal gelöscht wird. Sie kommt nur einmal (`ereignisse`, Art `puffer_probe`); Aus- und wieder Einschalten bringt keine zweite |
| N12 | Nur Videos (`.mp4`, `.mkv`, `.mov`) aus `eingang/`, deren Lager-Kopie auch in `eingang/` des Lagers liegt; nie Ordner, Bilder, Replays, Clips, Momente, Sessions, Exporte, Highlights, Musik, Archiv, DB-Sicherungen. Zeilen in `lager`/`aufnahmen` bleiben (in der Datenbank wird nichts gelöscht) |
| N13 | Die Zeilen (Probe, freigegeben, liegen geblieben) stehen in der Abschlussmeldung „Übertragung Puffer → Lager“ – keine eigene Nachricht. Fehlt eine Lager-Kopie oder ist sie anders, bleibt das Video liegen und die Meldung sagt es bei jedem Abgleich wieder; repariert wird nicht von selbst (das hieße ins Lager oder in die Tabelle eingreifen). Den Exit-Code des Abgleichs ändert die Freigabe nicht |
| N14 | Freigegeben wird nur, wenn der Abgleich pve-big gebraucht hat (Tage mit neuen Aufnahmen) – an Tagen ohne neue Daten wächst der Puffer auch nicht. Der Gaming-PC kopiert Freigegebenes nicht erneut (`uebertragen.tsv`, `MaxAlterTage`); ginge `uebertragen.tsv` verloren, kämen bis zu 30 Tage alte Aufnahmen noch einmal und gingen beim nächsten Abgleich wieder raus |
| N15 | Die Freigabe läuft unter der Lager-Sperre des Abgleichs, nicht unter der Pipeline-Sperre (sonst wartete sie auf jeden Render). Liest ein Schritt gerade ein über 14 Tage altes Rohvideo, liest er es zu Ende (Linux gibt die Datei erst nach dem Schließen frei); wer es danach sucht, behandelt es wie bisher als „nur im Lager“ (Nachschnitt, Fail, Regisseur) |

| Nr. | Annahme (Stufe 3 – Ein Weg nach dem ✅, Punkt „2-Wochen-Video nur im Lern-Bot, Clip-Bot wirklich still“) |
|---|---|
| N16 | „Clip-Bot still“ wie am 07.10. (keine einzelnen Szenen und Auto-Freigabe „an“) UND Lern-Bot im einfachen Modus (`bot.app._nur_probleme`). Nur dann schickt der Clip-Bot das 2-Wochen-Video nicht und vermerkt es als gesendet. Hat ein Highlight keinen Entwurf (Altbestand vor dem Regisseur), kommt es wie bisher im Clip-Bot – sonst sähest du es nie. Unter /experte oder bei lautem Clip-Bot wie bisher (in beiden Bots). Die n8n-Nachricht „🏆 Highlight-Video fertig … Freigabe im Bot.“ bleibt (n8n-Abläufe unverändert; sie stimmt weiter) |
| N17 | ✅ im Lern-Bot = freigegeben und hochgeladen, sofort beim Tipp (wie die Bewertung), nicht erst nach dem Paket. Das Paket kommt wie beim Short, aber ohne TikTok-Häkchen und ohne Post (es geht auf YouTube, das noch nicht gemessen wird); statt der Checkliste die Zeile „💾 Volle Qualität: Netzlaufwerk clips → Ordner highlights → <Datei>“, weil das Paket für Telegram auf 48 MB verkleinert ist. Deshalb heißt die Datei dort nicht „in voller Qualität“. ❌ = verworfen, die Clips sind wieder frei. Die erste Entscheidung gilt (wie im Clip-Bot) |
| N18 | Über dem 2-Wochen-Video steht im Lern-Bot „🏆 Dein 2-Wochen-Video“ (vorher sagte das nur der Clip-Bot). Die Erinnerung ans Hochladen ist im stillen einfachen Modus ganz aus – auch für ältere, im Clip-Bot freigegebene Videos; `/uploads` zeigt sie weiter |
| N19 | Übertragungen: Start und glattes Ende (PC → Puffer, Puffer → Lager) werden im stillen einfachen Modus nur als gesendet vermerkt. Glatt = kein Fehler, kein Abbruch, keine zusätzlich gesicherte Fassung, keine Probe vor dem ersten Freigeben (N11 bleibt), keine liegen gebliebenen Videos. Eine glatte Freigabe steht dann nur im Protokoll (`ereignisse`, `puffer_frei`) – ändert N13 für diesen Fall. Ob eine Meldung Routine ist, entscheidet, wer sie schreibt (`meldungen.routine`, neue Spalte über `db.MIGRATIONEN`), nicht der Bot am Text. Warnungen (Kills ohne Aufnahme, Speicher, Lager) und das Montags-Lebenszeichen kommen weiter |
| N20 | Abweichung von der Spec („das Paket wie beim Short“): Die Telegram-Fassung des 2-Wochen-Videos entsteht aus seiner fertigen Datei (auf 48 MB verkleinert, 1080 Pixel, `highlight.upload_fassung`), nicht neu aus den Szenen. Grund: Das Video reicht 14 Tage zurück, und Stufe 2 gibt Rohvideo-Szenen (`datei:`) nach 14 Tagen frei – ein ✅ ein, zwei Tage später endete sonst mit „Moment-Datei fehlt“. Nebenbei viel schneller als ein neuer Schnitt. Fehlt die fertige Datei, gilt der normale Weg. Gilt auch unter /experte (gleiches Ergebnis, nur ohne die Abhängigkeit) |

| Nr. | Annahme (Stufe 3, Punkt „✅ legt den TikTok-Post selbst an“, `lernbot_paket.posts_anlegen`) |
|---|---|
| N21 | Der Post entsteht, wenn das Paket ganz bei dir ist (Datei und Text), nicht schon beim ✅ – scheitert das Paket, gibt es nichts hochzuladen und keinen Post; der nächste Versuch der Merkliste (N4) legt ihn mit dem Paket an. `gepostet_utc` ist dieser Zeitpunkt, bis die Zuordnung zum hochgeladenen Video ihn ersetzt (nächster Punkt). Ein zweites ✅ (N6) bleibt derselbe Post mit dem ersten Zeitpunkt |
| N22 | Nur Shorts (wie in der Spec): Ein Video im Querformat, das kein 2-Wochen-Video ist (gibt es im einfachen Modus nur per `/entwurf zusammenschnitt`), bekommt dort weder Post noch Häkchen – es ginge auf YouTube, das noch nicht gemessen wird (wie N17). Unter /experte bleibt die Checkliste für Shorts und Zusammenschnitte (A5) |
| N23 | Scheitert nur das Anlegen des Posts (Schnittliste weg, Datenbank belegt), wird das schon gesendete Paket nicht wiederholt (sonst käme es doppelt) und der Post nicht nachgeholt; der Fehler steht im Log, und statt des Satzes mit den Zahlen steht nur „Lad es hoch.“ – dann kommen auch keine. Ebenso ohne Post-Plattform (`[publikum].plattformen` leer) |
| N24 | Häkchen-Knöpfe in älteren Nachrichten wirken weiter (legen den Post an bzw. sagen „vermerkt“), `/link` bleibt für Ausnahmen. „Die Zahlen hole ich mir danach selbst“ steht auch, wenn TikTok noch nicht verbunden ist – ob die Verbindung fehlt, zeigt 📋 ab dem letzten Punkt dieser Stufe |

| Nr. | Annahme (Stufe 3, Punkt „Der Bot findet dein hochgeladenes Video selbst“, `publikum_adapter._tiktok_zuordnen`) |
|---|---|
| N25 | Eindeutig = das Video ist der einzige Kandidat des Posts und der Post der einzige des Videos (±72 h um den Post – Häkchen/Link unter /experte kommt nach dem Hochladen, das Paket davor –, Länge ±2 s, schon vergebene Videos zählen nicht). Bei mehreren entscheidet die erste Zeile der Beschreibung, genau gleich nach Vereinheitlichung (Groß/klein, Leerraum, Emoji-Varianten); was frei wird, prüft derselbe Lauf neu. Die erwartete erste Zeile rechnet der Abruf wie das Paket aus der Schnittliste nach (`caption.entwurf_caption`); Clip-Posts haben keine (ihre Beschreibung kann von der KI stammen) |
| N26 | Zusätzlich zur Spec (lieber keine Zahlen als falsche): Beginnt die Beschreibung eines Videos mit einer anderen ersten Zeile als die Caption des Posts, kommt es für diesen Post nicht in Frage – auch als einziger Kandidat (z. B. ein fremdes Video mit eigener Beschreibung). Ohne Zeilenumbrüche geliefert, gilt sie nicht als Widerspruch, nur nicht als Beleg. Änderst du beim Hochladen den Anfang der Caption, gibt es für dieses Video keine Zahlen |
| N27 | Zusätzlich zur Spec: Ein Paar ohne passende erste Zeile (z. B. Video ohne Beschreibung) wird erst zugeordnet, wenn kein Video und kein Post mehr dazukommen kann (72 h nach Post und Video) – vorher nahm ein fremdes Video dem später hochgeladenen echten den Platz. Simulation (20 × 60 Tage, rund 1 300 Uploads, 90 % mit Caption, an jedem 10. Tag ein fremdes Video): falsche Zuordnungen 14 → 3, richtig zugeordnet 95 %. Solche Posts bekommen Zahlen ab Tag 3–4, der Score an Tag 7 bleibt möglich |
| N28 | Bekannt: Ein fremdes Video ohne Beschreibung mit passender Länge kann einem nie hochgeladenen Post zugeordnet werden (die 3 Fälle oben). Ohne eingefügte Caption und bei fast gleich langen Shorts bleiben viele Posts offen (Simulation: feste 45 s ohne Caption → keiner zugeordnet) – die Caption einzufügen gehört zum Hochladen |
| N29 | Gilt für jeden TikTok-Post ohne Video-Nummer, auch unter /experte (ein Post weiß nicht, in welchem Modus er entstand; die Regel ist nur strenger, nichts im Chat ändert sich). `gepostet_utc` wird bei der Zuordnung die Upload-Zeit (`create_time`); Posts mit Video-Nummer aus dem Link und frühere Zuordnungen bleiben, wie sie sind |

| Nr. | Annahme (Stufe 3, Punkt „Auch Flops bekommen ihre Zuschauer-Note“, `publikum_adapter.importiere`) |
|---|---|
| N30 | Gleiche Zahlen wie bei der letzten API-Messung werden wieder gespeichert, wenn die mindestens 20 h alt ist (`GLEICHE_ZAHLEN_NACH`, keine Einstellung) – nicht 24 h, weil der Timer bis 10 min streut und zwei Läufe auch 23 h 50 min auseinanderliegen. Ein zweiter Abruf am selben Tag (von Hand, nachgeholter Lauf) mit gleichen Zahlen bleibt ohne neue Messung; nach einem nachgeholten Lauf am Nachmittag kann so der nächste Tag ausfallen, der Score nimmt dann die Messung, die Tag 7 am nächsten liegt. Gilt für jeden API-Abruf (TikTok, YouTube) und für `pipeline publikum importieren` |
| N31 | Abweichung von der Spec: nur, solange der Post noch keinen Score hat. Der Score wird nie überschrieben; ohne Grenze bekäme jeder Post bis 180 Tage lang täglich eine Zeile (bis 100 je Lauf), und jede gespeicherte Messung lässt `autonom.aktualisieren` alle Messungen neu durchrechnen – gemessen: 100 Posts mit je 180 Messungen ≈ 2,9 s je Messung, also fast 5 min je Lauf, so lange darf `clip-publikum` laufen. Mit Grenze kommen je Flop höchstens etwa 6 Zeilen dazu |
| N32 | Ältere Flops, die bisher nie einen Score bekamen, bekommen ihn beim nächsten Lauf aus der Messung dieses Tages (z. B. Tag 20), solange sie im Abruf-Fenster liegen (`[publikum].api_max_tage`, 180) – ihre Zahlen standen seit Tag 2 still, Engagement und Reichweite sind dieselben wie an Tag 7 |

| Nr. | Annahme (Stufe 3, Punkt „Zahlen-Abruf richtet sich selbst ein, 📋 sagt ehrlich, wer lehrt“) |
|---|---|
| N33 | `alles-aktualisieren.sh` installiert `clip-publikum.{service,timer}`, wenn sie fehlen, und schaltet den Timer ein, auch wenn er schon installiert, aber aus war (wie `clip-sitzungen`); jede Einschaltung bekommt eine Zeile im Rückweg-Skript, gelöscht wird nichts. Ein Timer, der vom Repo und vom alten Stand abweicht (von dir angepasst), wird weder überschrieben noch eingeschaltet – das gilt jetzt auch für `clip-sitzungen` (vorher wurde ein angepasster, ausgeschalteter Timer trotzdem eingeschaltet). Wer den Zahlen-Abruf dauerhaft aus haben will: `[publikum].api_abruf = false` in `lokal.toml` (der Timer setzt dann nur noch Scores aus Screenshots); ein bloßes `systemctl disable` schaltet das nächste Update wieder ein – wie bei `clip-sitzungen` |
| N34 | „TikTok verbunden“ heißt ohne Netz: dieselben Bedingungen wie beim Abruf (`publikum_adapter.tiktok_verbunden` ↔ `_token`) – Access- oder Refresh-Token in `.env`, oder die Anmeldung per /tiktok in der Token-Datei mit Key und Secret in `.env`. Ob TikTok den Zugang noch annimmt (abgelaufen, entzogen), weiß erst der Abruf; dann fallen die Zahlen weg, und 📋 zeigt „n ✅-Videos nach 3 Tagen noch ohne Zahlen“. Eine unlesbare Token-Datei heißt „nicht verbunden“, nie ein Absturz. Der Lern-Bot liest dieselbe `.env` und Token-Datei wie der Abruf (`/opt/clip-regie/.env` ist ein Link auf die der Produktion, `regie-starten.sh`; die Token-Datei liegt neben der gemeinsamen Datenbank) |
| N35 | KI-Note „läuft“ = eine KI-Note in den letzten 3 Tagen; „fehlt – Claude-Anmeldung nötig“ = gesendete Shorts der letzten 3 Tage, die älter als 6 h sind (so lange darf die Note dauern: Bot baut, Pipeline rechnet, Neustart), aber keine Note; sonst „kommt mit dem nächsten Video“ – ohne frisches Video gibt es nichts, woran es sich zeigen könnte, und keine falsche Bitte an dich. Die häufigste Ursache ist die fehlende Anmeldung für den Lern-Bot-Dienst (docs/PUBLIKUM.md P2); ein volles Abo-Limit sieht genauso aus und geht von selbst vorbei |
| N36 | „✅-Video ohne Zahlen“ = TikTok-Post eines Lern-Bot-Videos (legt das Paket nach ✅ an), 3 bis 14 Tage alt, ohne jede Messung. Nur TikTok, weil nur das gemessen wird; ein Post, den du nie hochgeladen hast, zählt mit (ehrlich: es gibt keine Zahlen). Bis 14 Tage, damit ein nie hochgeladenes Video nicht monatelang im Bericht steht; ohne Video in der Woche kommt weiter kein Wochenbericht. Im 📋 steht der Zähler nur, wenn TikTok verbunden ist – sonst ist der Grund „nicht verbunden“ |
| N37 | Die Zeile ersetzt in 📋 die Zeilen „🧠 Gelernt aus n Bewertungen und n KI-Noten – Wochenbericht sonntags“ und „📊 Publikum: n Videos ausgewertet“ (die Zahl der ✅/❌ steht weiter darüber). „Ausgewertet“ wie bisher = Videos mit Zuschauer-Ergebnis (`autonom.ueberblick`), nicht jede einzelne Messung. Experten-Modus unverändert (dort gibt es weder 📋 kurz noch den Wochenbericht) |

| Nr. | Annahme (Prüfung Stufe 2 und 3, 08.10. – drei Prüfer, wichtige Befunde nachgestellt und behoben) |
|---|---|
| N38 | Die tägliche DB-Sicherung im Abgleich darf scheitern (OSError, sqlite3.Error – meist Puffer voll): Der Abgleich kopiert und gibt trotzdem frei, nur ohne Sicherung (`sicherung_fehler` im Ergebnis, eine ⚠️-Zeile in der Abschlussmeldung, keine Routine); der nächste Abgleich versucht es wieder. Die Datenbank selbst liegt nicht im Puffer. Vorher brach jeder Abgleich daran ab – nichts kopiert, nichts freigegeben, keine Meldung, der volle Puffer blieb voll (nachgestellt mit vollem tmpfs-Puffer: Tag 20–22 Exit 1; jetzt Tag 20 Exit 0, 4 alte Rohvideos weg). Ist nichts offen, steht es nur im Log und im Ergebnis; den vollen Puffer meldet die Morgenprüfung |
| N39 | Zusätzlich zu N9: Die Aufnahme muss auch 14 Tage älter sein als die jüngste bestätigte Aufnahme in `eingang/` (deren Dateizeit kommt vom PC). Springt die Uhr des Mini vor, bleiben junge Rohvideos im Puffer. Macht die Grenze nur strenger, nie lockerer; nach einer langen Pause bleibt Älteres etwas länger liegen (ohne neue Aufnahmen wächst der Puffer nicht) |
| N40 | Ändert N13: Fehlt die Lager-Kopie oder ist sie anders, wird die Bestätigung zurückgenommen (`lager.groesse = -1`, keine Zeile gelöscht) – der nächste Abgleich legt das Video neu ins Lager (fehlt → neu, anders → `name~<Zeit>` daneben, im Lager wird nie überschrieben). Danach gilt die Frist von 14 Tagen ab der neuen Bestätigung. Die ⚠️-Zeile kommt so nur bis zur neuen Kopie, nicht jeden Tag ohne Ausweg |
| N41 | Abgelehnt (lieber nichts löschen als zu viel): Kopierfehler außerhalb von `eingang/` nicht mehr als Sperre der Freigabe zu werten. „Solange alles ins Lager gesynct ist“ bleibt wörtlich – neu sagt die Abschlussmeldung dann „Alte Rohvideos lösche ich erst wieder vom Mini, wenn alles im Lager ist.“, und die Morgenprüfung bei knappem Platz „… löscht der tägliche Abgleich selbst vom Mini, sobald alles im Lager ist“ |
| N42 | Texte: „vom Mini gelöscht, die Kopie liegt sicher im Lager“ statt „freigegeben“ (im Clip-Bot heißt freigegeben „Clip angenommen“); „ab dem nächsten Abgleich“ statt „ab morgen“ (ein Abgleich von Hand am selben Tag löscht schon). Bricht die Probe ab, kommt nur die ⚠️-Zeile – sie zählt beim nächsten Abgleich neu. Ein Bind-Mount desselben Dateisystems unter `eingang/` erkennt die Prüfung nicht (gibt es im Aufbau nicht; Doku präzisiert, Code unverändert) |
| N43 | Täglicher Abruf: Posts ohne Score zuerst, die jüngsten vorn, danach die bewerteten (am längsten nicht gemessene zuerst). Vorher belegten ab etwa 100 Posts im Fenster bewertete mit festen Zahlen und nie hochgeladene die Plätze – neue Videos bekamen eine Messung und nie einen Score (Simulation: ab Post ~115). Jetzt Score an Tag 7,5 in allen Simulationen (1 oder 3 ✅ je Abend, 30 % nie hochgeladen). Sehr alte, nie hochgeladene Posts fallen bei vielen offenen ans Ende – zuordnen ließen sie sich ohnehin nicht mehr (72 h) |
| N44 | Im einfachen Modus endet die erste Zeile der Caption mit dem Songtitel (höchstens 40 Zeichen: „… · 6 Momente · 🎵 On & On“), und das Paket speichert die verschickte erste Zeile am Post (`posts.merkmale.caption_zeile`, keine Migration); die Zuordnung vergleicht mit genau ihr. Vorher hatten zwei ✅ eines Abends (gleiche Szenen, fast gleich lang) dieselbe erste Zeile – keins bekam je Zahlen, auch wenn nur eins hochgeladen war; und änderten Update, Vorlage oder Daten die Rechnung zwischen Paket und Zuordnung, fiel das echte Video heraus. Der Song wechselt von Video zu Video (`musik_rotation`, 🥱 nimmt einen anderen); ohne Musik oder bei gleichem Song bleibt es wie bisher offen statt falsch. /experte: Caption ohne Song; ältere Posts: nachgerechnet wie bisher (ändert N25 für neue Posts) |
| N45 | Nach dem Paket steht „Lad es hoch und füg den Text oben unverändert ein (Eigenes gern dahinter) – dann finde ich dein Video und hole mir die Zahlen selbst.“ (dasselbe in SO-GEHTS); bei „… noch ohne Zahlen“ in 📋 und Wochenbericht der wahrscheinliche Grund („nicht hochgeladen oder den Text dabei geändert?“). Was N26/N28 nur hier vermerkten, siehst du jetzt im Chat |
| N46 | KI-Note in 📋 nach dem gespeicherten Grund des letzten Versuchs (`kritiken.details.hinweis`): claude nicht gefunden, nicht nutzbar oder Exit → „fehlt – Claude-Anmeldung nötig“; Antwort passt nicht ins Schema, Abo- oder Tageslimit → „hakt gerade, beim nächsten Video neuer Versuch“ (ergänzt N35). Der Prompt nennt jetzt die 140-Zeichen-Grenze, an der eine echte Antwort scheiterte |
| N47 | Ändert N16: Die n8n-Nachricht „🏆 Highlight-Video fertig … Freigabe im Bot.“ ist in `2-highlight-video.json` deaktiviert wie „Match verarbeitet“ (in n8n selbst einmal von Hand, SO-GEHTS) – sie kam im Clip-Bot, das Video aber im Lern-Bot. n8n-Vertrag unverändert. Die Clip-Bot-Hilfe sagt „ab Werk“ und dass unter /experte das 2-Wochen-Video auch dort kommt. Die Kopfzeile „🎮 Dein Abend vom …“ / „🏆 Dein 2-Wochen-Video“ bleibt nach ✅/❌ stehen |

| Nr. | Annahme (Stufe 4, Punkt „Zu kurz? Erst mehr Anlauf aus denselben starken Szenen“, `regie.mehr_anlauf`) |
|---|---|
| N48 | „Zu kurz“ = der fertige Plan eines Shorts liegt unter 30 s oder mehr als 10 s unter dem Ziel (`MEHR_ANLAUF_AB_S`). Dann plant der Bot genau einmal neu mit Anlauf max(gelernt, 4 s) und Ausklang max(gelernt, 3 s) – innerhalb der Lern-Grenzen 1–6 / 0,5–4 s; die Clips haben 8 s Vorlauf und 5 s Nachlauf. Liegt das Gelernte schon darüber, gibt es keinen zweiten Plan. Dieselben Kandidaten, kein Füllmaterial (nur starke Szenen, nie Einzelkills). Gespeichert wird nur der zweite Plan, mit seinen Werten in `parameter`; das Gelernte bleibt unverändert, und kein Lerner liest diese Werte (das Publikums-Modell kennt sie nicht als Merkmal) |
| N49 | Reicht auch der zweite Plan nicht (unter 30 s), kommt „kein Video“ wie bisher – mit der Dauer des zweiten Plans, ohne ⚙️-Tipp. Lag schon der erste Plan über 30 s (nur weit unter dem Ziel) und scheitert der zweite, bleibt es beim ersten. Gilt auch für die neue Fassung nach 🥱: Fehlt ihr nur Länge, kommt sie jetzt statt „Diesmal keine neue Fassung“ (nachgestellt: 19 → 31 s). Keine eigene Zeile im Chat, nur im Log |
| N50 | Nur im einfachen Modus (`regie.geschmack` aus `einstellungen.EINFACH_FEST`, wie in `regie_lernen.aktuelle`) und nur für Shorts ohne 🔥 Viral; /experte, Zusammenschnitt und 2-Wochen-Video wie bisher. Bekannt: Hatte schon das erste Video den Anlauf, wird die Fassung nach ⏱️ aus denselben Szenen kaum länger (nachgestellt: 44,4 → 44,6 s bei Ziel 55 s) – mehr Länge bringt erst der Nachschub nie gesehener starker Szenen (nächster Punkt der Stufe 4) |

| Nr. | Annahme (Stufe 4, Punkt „Nachschub: starke Szenen, die du noch nie gesehen hast“, `regie.erstelle`) |
|---|---|
| N51 | Nachschub kommt, wenn der Abend weniger ungesehene starke Szenen hat, als ein Video braucht (4; „ungesehen“ je Szene wie `szenen.jemals_gezeigt`), oder wenn das Video auch mit mehr Anlauf zu kurz bleibt (unter 30 s oder mehr als 10 s unter dem Ziel). Abweichung von der Spec („bzw. das Ziel“): beim Ziel dieselbe 10-s-Toleranz wie beim Anlauf, und erst mehr Anlauf aus den Szenen des Abends, dann Szenen früherer Abende (Punkt 1 heißt „Erst mehr Anlauf“). Nachgestellt: 4 kurze starke Szenen bei Ziel 45 s → 44 s nur vom Abend; nach ⏱️ (Ziel 55 s) → 55 s mit einer Szene von früher |
| N52 | Nachschub = starke Szene (`regie.ist_stark`, wie Florians Regel), nie gezeigt, kein Fail, mit Match, Datei im Puffer – aus Matches, die vor dem ersten Match des Abends begannen (sonst stimmte „von früheren Abenden“ nicht) und höchstens `[puffer].rohdaten_tage` − 2 = 12 Tage alt sind (`regie.nachschub_matches`, Startzeit des Matches). Ohne lesbare Startzeit des Abends kein Nachschub |
| N53 | „Höchstens die Hälfte“ = nie mehr Szenen von früher als vom Abend (`regie.nachschub_darf`): bei Auswahl, Ersatz, Frische-Tausch, Nachlegen und Kürzen. Der Abend kommt zuerst, das Kürzen wirft Nachschub zuerst. Mit Nachschub gilt „höchstens 3 Szenen je Match“ nicht (wie im 🥱-Weg) – sonst gewänne eine alte Szene gegen die vierte vom Abend. Der Cooldown zählt Nachschub nur so weit, wie freie Szenen vom Abend ihn tragen (`regie.vorrat_s`) |
| N54 | „Anfang vom Abend“: Steht vorn eine Szene von früher, rückt die erste Szene vom Abend nach vorn, beim Bogen und beim Cold Open die stärkste (`regie.abend_vorn`); der Höhepunkt am Schluss bleibt, auch wenn er von früher ist |
| N55 | 🎬 nach dem Abend-Video: Dessen Szenen sind gesehen, also kommt Nachschub – etwa die Hälfte neue Szenen (Frische-Quote 50 % bis zum Deckel), der Rest vom Abend. Gibt es keine ungesehenen starken Szenen früherer Abende, schneidet 🎬 den Abend wie bisher neu (kein „kein Video“) |
| N56 | Die 🥱-Fassung füllte schon seit 07.10. mit starken Szenen früherer Abende auf; jetzt mit denselben Grenzen (12 Tage, vor dem Abend, Hälfte, Anfang vom Abend). Behaltene Szenen von früher (das Video hatte Nachschub) bleiben Pflicht, zählen aber als Nachschub; geht die Hälfte dann nicht auf, kommt „Diesmal keine neue Fassung“ mit Grund. Bekannt: Sind die besten Szenen eines Videos die von früher, behält 🥱 sie – die Teilung nach Stärke bleibt wie am 07.10. |
| N57 | Texte im einfachen Modus: unter „🆕 …“ die Zeile „+2 Szenen von früheren Abenden“ (bei einer: „+1 Szene von einem früheren Abend“); „kein Video“ sagt, warum auch frühere Abende nicht reichten (keine ungesehenen, nur n, oder höchstens so viele wie vom Abend); „mehr starke Szenen gab es nicht“ nach ⏱️ nur, wenn keine ungesehene starke Szene früherer Abende übrig war (`auswahl.nachschub_uebrig`); „Diesmal keine neue Fassung“ sagt „die starken Szenen der letzten Tage“ statt „früherer Abende“. Die Hilfe hat eine Zeile mehr |
| N58 | Die neue Fassung nach ❌ bleibt beim Abend: `regeln.matches_aus` zählt Szenen von früher nicht mit (`auswahl.nachschub`) – sonst wüchse der Abend um ihre Matches, und deren Einzelkills kämen nach 🥱 als Ersatz „vom Abend“. Die Szenen von früher aus dem abgelehnten Video gelten danach als gesehen; die Fassung nimmt andere. Bekannt: Ein dünner Abend wird auch nach ⏱️ nicht länger, als doppelt so viele Szenen wie vom Abend tragen (nachgestellt: 2 + 2 Szenen, 42 → 39 s bei Ziel 55 s) – mehr ginge nur gegen „höchstens die Hälfte“ |
| N59 | Nur einfacher Modus (`regie.geschmack`), nur Shorts aus einem Abend (Abend-Video, 🎬, Fassung nach ❌) mit „nur starke Szenen“; nicht 🔥 Viral, Zusammenschnitt oder 2-Wochen-Video. /experte und eine Match-Wahl dort bleiben exakt. Keine neue Einstellung; die Schnittliste bekommt nur zwei Zähl-Felder in `auswahl` (n8n-Vertrag unverändert) |

| Nr. | Annahme (Stufe 4, Punkt „Abend ohne Video: später eintreffendes Material zählt noch“, `sitzung._nachtrag`) |
|---|---|
| N60 | Geprüft wird nur der neueste Abend (nach Ende), nur wenn er mit „kein Video“ endete (`kein:<sitzung>`, keine Fehlerzeile, kein Video), bis 24 h nach seinem Ende (`NACHTRAG_H`, keine Einstellung), und nicht, solange ein neuer Abend noch auf n8n wartet (der wird gleich der neueste). Nur im einfachen Modus; unter /experte bleibt „kein Video“ wie bisher |
| N61 | „Neues Material“ = eine neue Szene aus den Matches des Abends seit dem letzten Versuch (`momente.erstellt > sitzungen.verarbeitet`, Fails zählen nicht; jeder Versuch setzt `verarbeitet` neu). Abweichung von der Spec: „ein offenes Match ist inzwischen fertig“ ist kein eigener Auslöser – ein fertiges Match bringt Neues nur über seine Szenen, ohne sie käme dasselbe heraus wie vorher. Vorher zieht der Bot die Stimmung der Clips dieser Matches nach, deren Datei schon da ist (nur dann – `stimmung.analysiere` liest dafür alle Replays) |
| N62 | Zusätzlich zur Spec: gebaut wird erst, wenn seit der jüngsten neuen Szene `[sitzungen].ruhe_min` (45 min, wie beim Erkennen des Abends) nichts dazukam – n8n arbeitet späte Matches eins nach dem anderen ab, sonst käme das Video schon nach dem ersten, und die anderen blieben draußen. Ebenso erst 45 min nach dem letzten Match-Ende des Abends (`sitzung._letztes_ende`, dieselbe Regel wie `auto_abend`): Spielst du nach einer Pause weiter, kam das Video sonst mitten im Spielen, sobald ein Match ohne Kill 45 min ohne neue Szene ließ, und die Matches danach bekamen nie eins (nachgestellt: vorher Video 40 min nach dem letzten Match, jetzt erst danach). Kommt etwas kurz vor Ablauf der 24 h, kann das Warten es aus dem Fenster schieben |
| N63 | Zusätzlich zur Spec (sonst im Standardweg wirkungslos): Ein selbst erkannter Abend (`abend_…`) kennt nur die Matches, die beim Erkennen da waren. Ein später angekommenes Match (PC früh aus: sein Replay kommt erst beim nächsten Start), das wie bei `auto_abend` höchstens 2 h vor oder nach einem Match des Abends liegt (nach hinten auch über mehrere) und in keiner anderen Sitzung steht, gehört dazu; der Versuch ergänzt es in `sitzungen.matches` (nur hinzugefügt). Ebenso Matches, wenn du nach 45 min Pause weiterspielst (das Video kommt dann erst nach dem letzten, N62) – vorher bekamen sie nie ein Abend-Video. Die Datei vom PC nennt ihre Matches selbst; dort bleibt die Liste |
| N64 | Höchstens ein Video je Abend: Hat seit dem letzten Versuch schon ein Short Szenen aus den Matches des Abends (z. B. 🎬, nachdem das Material da war), kommt kein Nachtrag – sonst zwei Videos mit fast denselben Szenen (07.10.: „warum sendet er immer 2 Videos?“). Szenen früherer Abende in jenem Video zählen nicht (`regeln.matches_aus`) |
| N65 | Reicht es wieder nicht, passiert im Chat nichts (keine zweite „kein Video“-Zeile), 📋 zeigt den neuen Grund; mehr Material später → neuer Versuch. Klappt es, wird die Statuszeile des Abends (still) zu „🎮 Nachtrag: Abend vom … – inzwischen sind weitere Szenen angekommen, ich baue dein Video.“ und verschwindet mit dem Video, eine alte „kein Video“-Zeile als eigene Nachricht auch. Scheitert erst das Rendern, gilt der Weg des Abend-Videos (einmal auf der CPU nach, dann die Fehlerzeile); scheitert schon das Planen, nur ins Log. `pipeline sitzungen` meldet zusätzlich `"nachtrag"` (Timer, nicht n8n – Vertrag unverändert) |

| Nr. | Annahme (Stufe 4, Punkt „Jede Szene nur in einem Video“, `szenen.verbraucht`, `regie.erstelle`) – **ersetzt am 08.10. durch „Abwechslung mit Ermüdung“ (N84–N91)** |
|---|---|
| N66 | ~~„Verbraucht“ = die Szene war in einem Video, das du gesehen hast (gesendet, egal wie bewertet; still aussortierte nicht) – je Szene, Clip, Nvidia und SteelSeries zählen als eine (`szenen.index`). Zusätzlich zur Spec: auch Videos, die gerade zu dir unterwegs sind (fertig gerendert und noch nicht verschickt, oder ein Abend-Video, das der Timer nachrendert) – sonst nähme ein 🎬, das auf die Pipeline-Sperre wartete, dieselben Szenen wie das Abend-Video, das direkt danach kommt. Ein Video, dessen Rendern ganz scheiterte, verbraucht nichts. Rückwirkend gilt alles schon Gezeigte; gelöscht oder gesperrt wird nichts, „verbraucht“ folgt aus den gespeicherten Entwürfen~~ → ersetzt (N84–N91) |
| N67 | ~~Nur einfacher Modus, nur Shorts ohne 🔥 Viral (Abend-Video, 🎬, Nachtrag, neue Fassung nach ❌). Dort ersetzt die Regel Abzug, Cooldown und Frische-Quote (keine früheren Entwürfe in der Auswahl): Jeder Kandidat ist ungesehen, und „Cooldown aufgehoben“ holte sonst verbrauchte Szenen zurück. Punkte, Elo und gelernte Formel sortieren weiter – nur unter den neuen Szenen; das Lernen ist unverändert. /experte, 2-Wochen-Video (Rückblick, darf Szenen aus Shorts haben) und 🔥 Viral wie bisher. Szenen, die zuerst im 2-Wochen-Video kamen, gelten danach als verbraucht (du hast sie in einem Video gesehen)~~ → ersetzt (N84–N91) |
| N68 | ~~Ändert N55: 🎬 nach dem Abend-Video nimmt nur noch Szenen, die du nicht kennst – den Rest vom Abend und bis zur Hälfte Nachschub. Reicht das nicht, kommt „🎬 Kein neues Video: …“ mit dem Grund (wie viele neu sind, wie viele du kennst) und „Sobald du wieder spielst, kommt ein neues.“ – kein Tipp zum Umstellen. Ebenso beim Abend-Video („… – heute kein Video“), wenn 🎬 die Szenen des Abends schon gezeigt hat. Nachgestellt (Test, drei Abende): Die beste Szene von Abend 1 kommt in Video 1 und nie wieder; 🎬 danach „Kein neues Video“; Abend 2 bekommt eine ungesehene Szene von Abend 1 dazu; Abend 3 mit einer starken Szene bekommt kein Video, obwohl die Szenen von Video 1 reichen würden~~ → ersetzt (N84–N91) |
| N69 | ~~Eine neue Fassung nach ❌ mit Grund ersetzt ihr Video: Sie darf dessen Szenen und die seiner Vorgänger-Fassungen wieder nehmen (Parameter `ersetzt`, `szenen.ersetzt_kette` – jede Fassung trägt die ganze Kette, keine Migration), und in ihr zählen sie nicht als gezeigt (kein Abzug). Nach ⏱️ bleiben die Szenen, und neue kommen dazu (nachgestellt: 6 → 7 Szenen statt „kein Video“). Bei 🥱 bleibt die stärkere Hälfte Pflicht und der Ersatz echt neu – auch keine Szene einer Vorgänger-Fassung, der Satz verspricht „gegen neue“. Ändert N58 für die anderen Gründe: Szenen von früher aus dem abgelehnten Video darf die Fassung wieder nehmen. Zusammengelegte Tipps (Merkliste) ersetzen nur das Video des neuesten ❌; ❌ ohne Grund baut nichts, das Video bleibt gesehen~~ → ersetzt (N84–N91) |
| N70 | ~~Texte im einfachen Modus: „🆕 n neue Szenen · m schon gezeigt“ nur noch bei einer neuen Fassung, als „· m aus dem Video davor“ (sonst ist jede Szene neu, die Zeile sagte nichts mehr); „wenig neues Material – x von y Szenen kennst du schon“ fällt weg, auch bei alten Videos nach einem Tipp. Die Hilfe hat die Zeile „🆕 Jede Szene kommt nur in einem Video“; 📋 zeigt beim letzten Abend den neuen Grund. Keine neue Einstellung~~ → ersetzt (N84–N91) |

| Nr. | Annahme (Stufe 4, Punkt „Musik füllt sich selbst auf“, `musik.nachschub`, `einstellungen.DEINE_GENRES`) |
|---|---|
| N71 | „Deine Genres“ = Techno, Hardstyle, Hardcore, Phonk, Brazilian Phonk (Florian 07.10.: „Techno/Hardstyle und Phonk“; Hardcore als nächster Verwandter von Hardstyle). Im einfachen Modus fest (`EINFACH_FEST`): nur sie bekommen den Genre-Bonus (1,5) – Rock und Midtempo nicht mehr; alte Titel bleiben und spielen weiter, wenn sie besser passen. Zusätzlich zur Spec auch im 2-Wochen-Video (`highlight._deine_musik`, nur dieser Wert – sonst galt dort die Datei mit Rock, und die neuen Titel kämen dort kaum vor). Neue NCS-Genres: Hardstyle (9, startet „episch“ wie die Multikills) und Brazilian Phonk (26, „spannend“); Phonk bleibt „frustriert“. /experte: ⚙️ bzw. Datei wie bisher, `HARTE_GENRES` bleibt (ein gespeicherter ⚙️-Wert bleibt gültig) |
| N72 | Abweichung von der Spec: Gezählt werden nur freie Titel **deiner** Genres (mit Beats, nicht per 🎵 gesperrt), nicht alle Titel. Live geprüft (08.10.) hat NCS nur 9 Techno- und 4 Hardcore-Titel, aber 45 Hardstyle, 32 Phonk und 28 Brazilian Phonk; Titel anderer Genres (z. B. bis zu 40 aus `musik ncs --genre hart` vom 27.09.) hielten die Gesamtzahl über 16 – Hardstyle und Phonk kämen nie. Unter 16 (2 × Rotation) lädt der Bot bis zu 10, abwechselnd je Genre, die knappsten zuerst; danach sind es etwa 20–25, und nachgeladen wird erst wieder, wenn deine 🎵-Sperren sie unter 16 drücken |
| N73 | Wann: am Ende jedes Laufs von `pipeline sitzungen` (Timer alle 10 min, unter der Pipeline-Sperre, nach den Abend-Videos), nur 10–17 Uhr Ortszeit und höchstens einmal am Tag – der Merker (`ereignisse`, Art `musik_nachschub`) steht VOR dem Laden. Ist NCS nicht erreichbar oder hat nichts Neues, steht es nur im Log; der nächste Versuch kommt am nächsten Tag. Nach dem Update kommen die ersten Hardstyle-/Phonk-Titel also beim nächsten Lauf zwischen 10 und 17 Uhr; ein Video davor hat noch die alten. Ein Titel, den ffmpeg nicht lesen kann, wird übersprungen (vorher brach `ncs_genres_laden` daran ab). Keine Nachricht im Chat; `pipeline sitzungen` meldet zusätzlich `"musik"` (Zahl neuer Titel; Timer, nicht n8n – Vertrag unverändert) |
| N74 | Nur einfacher Modus: Unter /experte lädt der Bot nichts von selbst (Musik wie bisher als Audiodatei mit Quellenangabe oder `pipeline musik ncs`, jetzt auch `--genre hardstyle,brazilian-phonk`). Gelöscht wird nichts – alte Titel und 🎵-Sperren bleiben; der Musik-Ordner wächst um höchstens 10 Titel (etwa 100 MB) am Tag und nur, solange weniger als 16 frei sind. Quellenangabe je Titel von der NCS-Seite (`.lizenz.txt`, `tracks.quelle`, steht im Text zum Hochladen). Welcher Titel ins Video kommt, entscheidet weiter `regie.waehle_musik` (Genre-Bonus, Rotation über 8 Videos, Sperren). `/einstellungen` zeigt im einfachen Modus „🎵 Musik: Techno, Hardstyle, Hardcore und Phonk – neue Songs hole ich mir selbst.“ |

| Nr. | Annahme (Stufe 5, Punkt „Zuschauer lehren Aufbau, Tempo und Zeitlupe“, `geschmack.statistik`) |
|---|---|
| N75 | Die Zuschauer sind im einfachen Modus der dritte Lehrer für Aufbau, Tempo und Zeitlupe (vorher lernten die drei dort gar nicht vom Publikum): Je Video mit Zuschauer-Note y (−1 … 1, `audience_ergebnisse`) zählt jede Schraube doppelt (`geschmack.ZUSCHAUER_GEWICHT` 2, Treffer (y + 1)/2) – genau wie `stile.statistik` unter /experte seit 30.09. Dein ✅/❌ zählt 1, die KI-Note 0,34. Kein Zeit- und kein Confidence-Gewicht (wie dort); TikTok und YouTube desselben Videos sind ein Video (`massstab._publikum`). Die Wahl bleibt Thompson-Sampling, „mutig“ bleibt bei jedem zweiten Video; keine neue Einstellung. Ohne Zuschauer-Noten rechnet alles genau wie vorher |
| N76 | Gezählt wird nur, was im Video wirkte (wie bei deinem ✅/❌): Eine Schraube, die das Publikums-Modell danach selbst verstellt hat (Tempo, wenn es die Schnittlänge ändert), oder die nicht wirkte (Zeitlupe bei Effekten „aus“), lernt aus diesem Video nichts – das Publikums-Modell lernt sie dann ohnehin selbst. Zuschauer nennen keinen Grund, also grenzt nichts ein (anders als bei ❌). Dein ✅ (= hochladen) und die Zuschauer-Note desselben Videos zählen beide; Experimente zählen wie jedes Video (dafür sind sie da), Fail-Videos nie (wie bisher). Ein Video zählt, sobald es eine Note hat (ab Tag 3, auch Flops, N30) |
| N77 | Bekannt und bewusst so gelassen: Verstellt das Publikums-Modell nur einen Feinwert des Aufbaus (Einstieg, Musikpegel, Hektik, Schnitte je Beat), zählt der Aufbau trotzdem – Reihenfolge und Bildgröße bleiben die des gewählten Aufbaus (`geschmack._wahl_aus` liest dann den Stil wie bei alten Entwürfen; galt schon für ✅/❌ und KI-Note). Nachgestellt mit 5 gemessenen Videos: In 12 von 12 Videos war der Aufbau so „verstellt“ – ohne diesen Rückfall lernte niemand mehr den Aufbau, sobald das Publikums-Modell Zahlen hat. Weicht für den Aufbau vom Satz „übersteuert … weder Lob noch Tadel“ (07.10.) ab |
| N78 | Wochenbericht: neue Zeile „👀 Bei den Zuschauern kommt gut an: Aufbau „erzählt“ (3 Videos) · …“ (`geschmack.zuschauer_zeile`) – nur aus den Zuschauer-Noten, höchstens drei, ab einem Anteil von 0,6 wie „👍 Kommt gut an“ (ein Video ab Note +0,4, viele ab +0,2). Gibt es Noten, aber keinen Favoriten: „👀 Bei den Zuschauern noch kein klarer Favorit (n Videos ausgewertet).“; ohne Noten keine Zeile. „👍 Kommt gut an“ und „👎 Kommt weniger an“ rechnen jetzt mit allen drei Lehrern. Nachgestellt mit der Planer-Simulation gegen den echten Code (Publikum mag „erzählt“, du tippst immer ✅, 20 Läufe): Anteil „erzählt“ in den Videos 31–60 von 24 auf 56 %, Zuschauer-Note im Schnitt +0,01 → +0,34. /experte unverändert |

| Nr. | Annahme (Stufe 5, Punkt „⏱️/⏳ als Grenze mit Richtung statt fester Länge“, `regeln.laenge`) |
|---|---|
| N79 | Richtung = deine letzte Längen-Ansage an einem Short: Grund „kurz“ bzw. „lang“ in deinen Bewertungen (ohne KI, ohne Zusammenschnitt; beide an einem Video heben sich auf wie beim Regie-Lernen), geordnet nach dem Zeitpunkt des Tipps (`geaendert`, `regie_lernen.laengen_richtung`) statt nach dem ❌ – den Grund tippst du nach dem ❌, auch an einem älteren Video. Kein neuer Schlüssel, die Zahl bleibt `regie.short_ziel_s`. Ohne Ansage (Wert aus ⚙️, zu dem du nie ⏱️/⏳ getippt hast) und unter /experte: fest wie bisher |
| N80 | Abweichung von der Spec („dazwischen gilt das Gelernte bzw. die Wahl des Publikums-Modells“): Dein Wert ist der Start fürs Publikums-Modell (`regie_lernen.aktuelle` setzt `dauer_faktor` = Wert / 45 s vor `autonom.plan_parameter`), nicht der gelernte `dauer_faktor`. Der lernt aus denselben Tipps (je ⏱️ ×1,11) und steht nach vielen alten „zu kurz“ bei 75 s – dein ⏱️ auf 55 s hätte sofort ein 75-s-Video gebracht, ohne Beleg (nachgestellt mit 100 alten „zu kurz“: mit Grenze allein 75 s, mit Start 55 s). „Mit Belegen“ = das Publikums-Modell wählt mit Zuschauerzahlen einen anderen Wert oder probiert ihn aus (jedes 7. Video). Wählt es gegen deine Richtung, gilt deine Grenze. Ohne Beleg gilt genau dein Wert, auch unter 45 s (`plan_parameter` beginnt nie unter 45 s – eine Untergrenze 40 ergab sonst 45). Dafür rundet `autonom.plan_parameter` den Start auf 0,001 s: 45 × 55/45 ergab 55,00000000000001, dann galt 55 nicht als „vorher“, und bei Grenze 55 konnte das Modell „55 s“ als Versuch eintragen, der keiner war (nachgestellt; jetzt 65 s). Unter /experte ändert das Runden nichts Sichtbares (das Ziel war schon auf 0,1 s gerundet) |
| N81 | ⏱️/⏳ zählen von dem Video, an dem du tippst – ⏱️ von der längeren, ⏳ von der kürzeren Zahl aus Ziel und echter Länge (auf 5 s gerundet; ohne beides dein Wert, sonst 45 s), vorher von deinem gespeicherten Wert. So stimmt „10 Sekunden länger/kürzer als dieses Video“ auch, wenn das Video wegen zu wenig Szenen kürzer als sein Ziel war (⏳ an 48 s bei Ziel 65: höchstens 40 statt 55) oder die letzte Szene es verlängert hat (⏱️ an 63 s bei Ziel 55: mindestens 75 statt 65). Sonst hätte ⏱️ an einem 65-s-Video, das das Publikum über deine Untergrenze 55 hinaus verlängert hat, nur 65 gesetzt (nichts geändert), und ⏳ dort hätte 45 statt 55 ergeben. Zwei ⏱️ an zwei 45-s-Videos ergeben jetzt 55 (vorher 65) – beide sagten „45 ist zu kurz“. Texte: „Shorts sind ab jetzt mindestens 55 s lang (vorher 45 s).“ bzw. „höchstens …“, 📋/Wochenbericht/`/einstellungen` „Shorts mindestens 55 s“; die Sätze an den Grenzen 30/75 s bleiben – **geändert am 08.10. durch N97** (nie gegen deine bisherige Richtung) |
| N82 | Ein Längen-Versuch des Publikums-Modells (`autonom.exploration`, Variable `ziel_dauer_s`), dessen Wert das Video am Ende nicht hat, wird für dieses Video gestrichen (`regeln._ohne_scheinversuch`) – sonst trüge `autonom.snapshot_speichern` einen nie getesteten Versuch in `lern_experimente` ein (Planer-Lauf l3). Zusätzlich zur Spec gilt das im einfachen Modus auch, wenn eine feste Länge (⚙️-Wert ohne ⏱️/⏳) oder die alte ⚙️-Mindestlänge ihn verschoben hat. Versuche in deiner Richtung bleiben (so sammelt das Modell Belege); kein Ersatz-Versuch im selben Video. /experte unverändert (dort bleibt ein überstimmter Versuch eingetragen wie bisher) |
| N83 | Nur einfacher Modus, nur Shorts. Die alte ⚙️-Mindestlänge (06.10.) gilt wie bisher nur, solange du ⏱️/⏳ nicht getippt hast – dein letzter Tipp geht vor (bis 08.10. überschrieb die feste Länge sie ebenso). Sonst brächte ⏱️ an einem 45-s-Video nach zweimal ⏳ wieder die alte Mindestlänge (z. B. 65 s) statt 55 s. Nichts gelöscht, keine Migration, keine neue Einstellung; n8n-Vertrag unverändert |

| Nr. | Annahme (Stufe 4, Punkt „Abwechslung mit Ermüdung“ – ersetzt „Jede Szene nur in einem Video“, `szenen.verlauf`, `regie.erstelle`) |
|---|---|
| N84 | Ein Video = alle Fassungen eines Videos (Familie über den Parameter `ersetzt`, kleinste Nummer). Gezählt werden Videos, die du gesehen hast oder die gerade zu dir unterwegs sind (wie N66; still aussortierte nicht) – alle Formate, auch das 2-Wochen-Video und alte Entwürfe aus dem Experten-Modus. „Letzte 3 Videos“ nach der jüngsten Fassung je Video, ohne Zeitgrenze; „Einsätze in 30 Tagen“ = Videos, deren jüngste Fassung höchstens 30 Tage alt ist (unlesbare Zeit zählt mit). Je Szene: Clip, Nvidia und SteelSeries sind eine (`szenen.index`). Nichts gespeichert, nichts gelöscht, keine Migration – alles folgt aus den Entwürfen – **geändert am 08.10. durch N92** (Sperre nur für Videos der letzten 48 h) |
| N85 | Gewicht = Punkte × 0,5^Einsätze (unter 1 Punkt ein Abzug von (1 − 0,5^n) wie unter /experte; steht als `abzug`/`gezeigt` im Segment). Reihenfolge: neue vom Abend (und Szenen des Videos, das eine Fassung ersetzt), neue früherer Abende, dann alle bekannten nach Gewicht. Weil bekannte nicht mehr nach Abend/früher sortiert sind, nimmt die Auswahl immer die beste gerade erlaubte Szene – eine übersprungene frühere kommt wieder in Frage, sobald eine vom Abend dabei ist (ohne bekannte dasselbe Ergebnis wie vorher). Abweichung: „Chance“ als Rang, nicht gewürfelt – nachvollziehbar und testbar; die Abwechslung kommt aus Sperre, Ermüdung und „Zusammenstellung“ |
| N86 | Sperre: Eine Szene aus den letzten 3 Videos ist gar kein Kandidat – nie aufgehoben (`regie.frei_von_cooldown` und die letzte Nachlege-Stufe holen im einfachen Modus nichts zurück). Dann lieber kürzer (30–75 s), Nachschub, mehr Anlauf oder kein Video. Ausnahme nur für die neue Fassung: die Szenen des Videos, das sie ersetzt (samt Vorgänger-Fassungen), sind frei und zählen nicht als Wiederholung (wie N69) – **geändert am 08.10. durch N92–N94** (Zeitgrenze, Fassung eines älteren Videos, Notfall) |
| N87 | „Gemischt“, Abweichung von der Spec: Ist eine neue (oder eigene) Szene im Video, höchstens so viele bekannte wie andere (die Hälfte des Videos) – bis zur Mindestzahl 4 dürfen es mehr sein (1 neue + 3 bekannte). Wörtlich hätte eine einzige neue Szene ein Video verhindert, das ganz aus bekannten erlaubt wäre (1 + 1 = 2 < 4). Ganz ohne neue: unbegrenzt. „Nicht dieselbe Zusammenstellung“: höchstens 2 Szenen eines Videos waren schon zusammen in einem früheren Video – immer, nicht nur ohne neue (3 bekannte aus demselben alten Video wären auch neben neuen aufgewärmt). Beides auch beim 🥱-Ersatz, Nachlegen und Kürzen (Kürzen streicht bekannte zuerst) – **geändert am 08.10. durch N94/N96** (locker im letzten Versuch, Fassungen zählen mit) |
| N88 | Abend-Video und Nachtrag bleiben beim Abend: vorn eine Szene vom Abend, höchstens die Hälfte von früheren Abenden – jetzt zählen dort neue UND bekannte früherer Abende (sonst wäre „Dein Abend vom …“ zu drei Vierteln alt); reicht es nicht, „kein Video“ wie bisher. Abweichung von der Leitplanke „Auffüllen nur mit nie gesehenen“: aufgefüllt wird zuerst mit nie gesehenen, reichen die nicht, auch mit bekannten starken – Regel „Wiederholen erlaubt, aber gebremst“ (Florians Korrektur) geht vor; Einzelkills bleiben draußen. 🎬 und neue Fassungen nach ❌ (`regie.erstelle(mischen=True)`, lernbot): erst genauso; reicht das nicht, ein zweiter Versuch gemischt aus dem Abend und früheren Abenden der letzten 12 Tage – ohne Hälfte-Grenze und ohne „vorn vom Abend“ (`parameter.mix`). Zusätzlich zur Spec: Ohne diesen Versuch käme bei 🎬 nach dem Abend-Video nie ein Video aus bekannten (die Szenen des Abends sind gesperrt) – genau den Fall „alle ausgeschöpft“ meinte Florian – **geändert am 08.10. durch N94/N95** (gemischt schon, wenn das Ziel verfehlt wird) |
| N89 | 🥱 (ändert im einfachen Modus Z2 und den 🥱-Teil von N69): Ersatz erst aus neuen Szenen (vom Abend auch Einzelkills, früherer Abende nur starke), dann aus bekannten starken – nie aus deinen letzten 3 Videos und nie aus dem abgelehnten Video samt Vorgänger-Fassungen. Der Satz sagt „… tausche ich gegen andere – zuerst neue.“; „Diesmal keine neue Fassung“ nur, wenn es auch gemischt nichts gibt. Die Fassung eines gemischten Videos ohne Szene vom Abend nimmt als Abend den, aus dem es gebaut wurde (`auswahl.abend`, `regeln.abend_aus`); `sitzung._schon_video` bleibt bei `matches_aus` (ein gemischtes Video ist nicht das Video des Abends) – **geändert am 08.10. durch N96** (Einzelkills erst nach bekannten starken) |
| N90 | Texte im einfachen Modus: „♻️ 2 Szenen kennst du schon“ (bei einer Fassung „… kennst du aus früheren Videos“); „+n Szenen von früheren Abenden“ zählt nur neue, ohne Szene vom Abend „📅 Alle Szenen von früheren Abenden“; bei einer Fassung nur „🆕 n neue Szenen · m aus dem Video davor“ bzw. „↩️ m Szenen aus dem Video davor“. „Kein neues Video“ nur, wenn auch gemischt nichts geht – mit dem Grund („… waren gerade erst in deinen letzten Videos“, „… lief gerade erst oder schon genauso zusammen“). Neue Zeile in der Hilfe. Schnittliste: `auswahl.wiederholt`, `auswahl.abend`, `auswahl.mix`, Regeln in `parameter.ermuedung` (n8n-Vertrag unverändert) |
| N91 | Werte intern in `[regie]` (`ermuedung_tage` 30, `ermuedung_faktor` 0,5, `sperre_videos` 3, `wiederholung_anteil` 0,5, `gleich_mit_video` 2), nicht im ⚙️-Katalog; Unsinniges fällt auf diese Werte zurück. /experte (Abzug, Cooldown, Frische-Quote), 2-Wochen-Video (Rückblick) und 🔥 Viral wie bisher; das 2-Wochen-Video zählt aber als gesehenes Video. Lernen unverändert. Nachgestellt (4 Abende à 5–6 starke Szenen, dann 14× 🎬): die beste Szene in Video 1, 5, 9, 13, 17, jede andere 3–4-mal, nie zweimal in vier Videos hintereinander, nie mehr als 2 Szenen aus einem früheren Video. Bekannt: Mit wenig Material kommt die beste Szene so oft, wie die Sperre zulässt (jedes 4. Video), und „Zusammenstellung“ kann ein Video verhindern („Kein neues Video …“) |

| Nr. | Annahme (Prüfung Stufe 4/5 „Abwechslung mit Ermüdung“ und „⏱️/⏳ als Grenze“, zwei Prüfer, nachgestellt) |
|---|---|
| N92 | Sperre mit Zeitgrenze (ändert N84/N86): Eine Szene ist nur gesperrt, solange das Video (seine jüngste Fassung) höchstens 48 h alt ist (`[regie].sperre_stunden`, unlesbare Zeit zählt als neu). Vorher sperrte nach einer Woche Pause das letzte Video seine Szenen weiter (Prüfer: Abend nach 7 Tagen ohne Video, „gerade erst“ nach 9 Tagen). Wie vom Prüfer vorgeschlagen (2–3 Tage); 18 h ließen im Nachstellen die Szenen von gestern sofort wieder zu (größter Anteil einer Szene 25 % statt 18 %, Abstand 1). Am Tag danach bleibt ein dünner Abend daher ohne Video (lieber kein Video als Wiederholung); seine Szenen kommen später als Nachschub |
| N93 | Neue Fassung eines älteren Videos (ändert die Ausnahme in N86): Die Szenen des ersetzten Videos sind keine Wiederholung, aber nur frei, wenn sie nicht gerade erst in einem ANDEREN deiner letzten Videos liefen (`szenen.verlauf` zieht sie nicht mehr pauschal von der Sperre ab). Bei 🥱 ist eine behaltene Szene, die so gesperrt ist, keine Pflicht – sie fehlt wie die schwächere Hälfte (dann nennt der Satz „Die 2 besten Szenen bleiben“ eine zu viel; selten). Eine ⏳/⏱️-Fassung eines alten Videos kann so fast nur aus anderen Szenen bestehen – die Sperre geht vor |
| N94 | 🎬 und neue Fassungen: Lockerungs-Leiter statt „gemischt nur bei kein Video“ (ändert N87/N88, `regie._mit_lockerung`): (1) vom Abend, (2) gemischt aus den letzten 12 Tagen, (3) gemischt und locker – beliebig viele bekannte neben neuen und aus demselben früheren Video höchstens die Hälfte des neuen Videos (bei 4 Szenen weiter 2; `regie.LOCKER`). Genommen wird der erste Plan, der sein Ziel bis auf 2 s erreicht (`ZIEL_TOLERANZ_S`); Plan 1 bei 🎬 nur mit einer neuen Szene, sonst holte der Abend seine bekannten Szenen in jedes vierte Video (Prüfer: E12.1/E12.2 in #32, #37, #41, #45). Erreicht keiner das Ziel, der längste; nie unter 4 Szenen (vorher kamen 3 aus demselben alten Video). Gibt es gar keinen Plan, ein Notfall-Versuch, in dem das älteste der 3 gesperrten Videos frei wird (`regie.NOTFALL`, Vorschlag des Prüfers; „wenn alle Clips ausgeschöpft sind … bessere öfters“) – für die Länge allein wird die Sperre nie gelockert. Abweichung vom Vorschlag: mehr Anlauf bleibt in jedem Plan der erste Schritt (N48). Probe-Pläne schreiben weder Datei noch Entwurf (`erstelle(_speichern=False)`); gespeichert wird genau einer. Nachgestellt (12 Abende, dann 10× 🎬 am selben Tag ohne neues Material): 10 von 10 mit Video (38–56 s), die stärksten Szenen darin je zwei- bis dreimal, keine in zwei Videos direkt hintereinander |
| N95 | Abend-Video und jeder Plan vom Abend: Nachschub früherer Abende schon, wenn das Video sein Ziel um mehr als 2 s verfehlt (vorher erst 10 s darunter – mit „mindestens 45 s“ blieb ein Abend mit vier starken Szenen bei 40 s). Höchstens die Hälfte von früher bleibt. Mehr Anlauf weiterhin erst 10 s darunter; nach „⏳ höchstens …“ streckt er nur noch bis 30 s (`parameter.laenge_richtung`; vorher wurde aus 43 s 62 s bei höchstens 55). Bleibt ein Short unter deiner Mindestlänge, steht darunter „⚠️ kürzer als deine Mindestlänge (55 s) – mehr passende Szenen gab es gerade nicht“ (nur nach ⏱️, `parameter.laenge_grenze_s`) |
| N96 | „Nie mehr als 2 zusammen“ zählt in einer neuen Fassung auch die Szenen des ersetzten Videos (`Kandidat.videos` an jeder Szene; vorher hatte eine Fassung 4 von 6 Szenen aus einem alten Video). 🥱-Ersatz im einfachen Modus: ein neuer Einzelkill vom Abend kommt erst nach allen bekannten starken (`Kandidat.fueller`, Rang 3) – „lieber kein Füllmaterial“; ändert N89. Folge: Findet 🥱 nur Ersatz aus demselben alten Video wie die behaltenen Szenen, kommt „Diesmal keine neue Fassung“ |
| N97 | ⏱️/⏳ nie gegen deine Richtung (ändert N81): Hast du dieselbe Richtung schon getippt, zählt die weitere Grenze – ⏱️ an einem älteren 40-s-Video bei „mindestens 55 s“ bleibt 55 („⏱️ Verstanden: Shorts sind schon mindestens 55 s lang.“, vorher 50), ⏳ an einem älteren, längeren entsprechend; „vorher“ nennt dann deine bisherige Grenze. Die Richtung vor dem Tipp kommt aus deinen Bewertungen ohne die des getippten Videos (`regie_lernen.laengen_richtung(ohne=…)`, `regeln.wende_an(entwurf_id=…)`). „Mehr starke Szenen gab es nicht“ steht nicht nach einer 🥱-Fassung (ihre schwächere Hälfte fehlte nur dort) |
| N98 | Kleinkram: „🎯 nur Spielabend …“ nur, wenn keine Szene von einem früheren Abend im Video ist; „alle 4 starken Szenen … waren gerade erst in deinen letzten Videos“ statt „keine starke Szene (4 weitere …)“. /experte wie auf main: Szenen früherer Abende in der 🥱-Fassung nur hinten an, ohne Hälfte-Grenze und ohne „vorn vom Abend“ (`Kandidat.frueher`). Ein Versuch des Publikums-Modells, den die Effekt-Stufe oder das 🥱-Tempo überstimmt, fällt wie N82 weg (`regeln._ohne_scheinversuch` am Ende von `anwenden`, jede Variable). Nicht behoben (eigene Änderung): ⏳ bei 4 langen Pflicht-Szenen bleibt über der Grenze (kürzer ginge nur mit weniger als 4 Szenen oder kürzeren Teilen); das 2-Wochen-Video füllt weiter mit Einzelkills (schon vor dieser Änderung so) |
| N99 | ⏳ als echte Obergrenze (zusätzlich zur Prüfung, beim Nachstellen gefunden; Stufe 5 versprach „nie länger“, `regeln.laenge`): Im einfachen Modus halten Nachlegen und Kürzen nach „⏳ höchstens …“ deine Grenze ein (bis auf 2 s fürs Beat-Raster, `regie.erstelle` oben_s), solange 4 Szenen bleiben – vorher schoss die letzte Szene darüber (nachgestellt: 61 statt 53 s bei „höchstens 55 s“, 46 statt 39 s bei „höchstens 40 s“). Bei 🎬 und neuen Fassungen zählt ein Plan über der Grenze nicht, solange einer darunter liegt; sind alle darüber, der kürzeste (`regie.ueber_grenze`). Sind schon 4 Szenen länger, bleibt das Video länger (N98). /experte und ⏱️ unverändert |

## Mehrbenutzer (Clip-Pipeline 4.0)

Auftrag: Ein Freund von Florian bekommt auf dem Mini seine eigene, vollständig getrennte Pipeline. Abnahme von Stufe 1:
Zwei Benutzer arbeiten unabhängig und ohne Zugriff aufeinander; Florian merkt nichts. Aufbau und Stufen:
`docs/MEHRBENUTZER.md`.

### M1 · Instanz je Benutzer (08.10.2026)
Jeder Freund betreibt dieselbe Pipeline aus demselben Code als eigene, abgeschlossene Instanz: eigener Linux-Benutzer
`clip-<name>`, eigener Ordner `/var/lib/clip-benutzer/<name>` (Datenbank, Puffer, Regie, Musik, Cache), Konfig und
`.env` gehören root, genau ein eigener Lern-Bot. Gestartet wird sie nur aus systemd-Vorlagen mit `CLIP_INSTANZ`; deren
Sandbox blendet alles von Florian aus. Geteilt wird genau eins: Florians Rechen-Sperre. Florian ist die Stamm-Instanz
ohne `CLIP_INSTANZ` – bei ihm ändert sich nichts (Konfig, Umgebung, Sperrpfad, Dienste, n8n-Vertrag). Keine Tabelle
wird umgebaut, keine SQL-Anweisung geändert.

Verworfen: eine Benutzer-Spalte in Florians Datenbank (336 SQL-Anweisungen, 52 davon ohne WHERE – beide Richter gaben
der Trennung 4/10), ein eigener Container je Freund (Grafikchip, Code und Updates je Container), Freunde unter
Florians Benutzer `pipeline` (kein Schutz durch den Kernel).

Florians Antworten (08.10.) – sie gehen dem Plan vor:
1. **Weg der Aufnahmen** („Muss das auf dem Mini sein, ich hab doch einen externen n8n-Server?!“): ein Briefkasten auf
   dem vServer – ein kleiner Upload-Dienst, nicht n8n, keine Videos durch n8n. Der Mini holt über Tailscale ab und
   rechnet („Rechnen, wo die Daten liegen“, Grafikchip). Kommt mit Stufe 2 (Programm für den PC des Freundes).
2. **Speicher** („Wie bei dir, mit Lager“): Rohvideos der Freunde kommen ins Lager auf pve-big (eigener Unterordner je
   Freund) und werden im Puffer nach 14 Tagen frei, wenn die Kopie im Lager bestätigt ist – wie bei Florian (B5). Kommt
   mit Stufe 2, weil erst dann Aufnahmen von Freunden ankommen.
3. **KI** („Eigener Claude-Zugang“): Jeder Freund nutzt sein eigenes Claude-Abo. Ohne eigenen Zugang bleibt die KI bei
   ihm aus.

Leitplanken (Florian, wörtlich): „Wie kann ich Freunden das an die Hand geben, ohne dass sie einen Server oder ähnliches
brauchen?“ → „Telegram wie bei dir“; für ihn selbst „Auch einfacher“; „du baust es noch komplett kaputt, wenn du so
weiter machst. Ich wollte es simplifizieren“.

| Nr. | Annahme (bis Florian widerspricht) |
|---|---|
| M2 | Eine Rechen-Sperre für den ganzen Mini: Florians Datei `/var/lib/clip-pipeline/pipeline.lock` bleibt, wo sie ist; Freunde tragen sie als `[sperre].datei` ein und dürfen sie nur lesen. Nicht verlegt, weil Florians Lern-Bot auf einem anderen Code-Stand laufen kann und Skripte und Doku den Pfad fest nennen (`puffer-einrichten.sh`, `puffer-zurueck.sh`, `docs/PUFFER.md`) |
| M3 | `[sperre].datei` leer oder fehlend = wie bisher neben der Datenbank (`<datenbank>.lock`); ein relativer Pfad gilt neben der Datenbank. Nur `sperre.pfad` leitet den Pfad ab (ein Test wacht darüber); `alles-aktualisieren.sh` fragt ebenfalls dort nach, ein älterer Code-Stand ergibt wie bisher `<datenbank>.lock` |
| M4 | Darf ein Prozess die Sperrdatei nicht schreiben (EACCES, EPERM, EROFS), öffnet er sie nur lesend – flock wirkt so in beide Richtungen (`sperre.sperre` und der Belegt-Test in `big.py`). Fehlt sie und lässt sie sich nicht anlegen: klarer Fehler (`pipeline` endet mit Exit 2 und `{"fehler": "konfig"}`), nie eine Ersatzsperre daneben |
| M5 | `/paket` im Clip-Bot rendert jetzt unter der Sperre (vorher ganz ohne) und wartet nicht: Rechnet gerade etwas anderes, kommt „⏳ Gerade rechnet ein anderer Schritt – gleich nochmal: /paket N“. Der Clip-Bot arbeitet Nachrichten nacheinander ab; langes Warten hielte jeden Klick auf |
| M6 | Messgrundlage: Jede gehaltene Sperre schreibt beim Freigeben eine Zeile „Sperre gewartet x s, gehalten y s (Datei)“ ins Log – auch die Lager-Sperre. Wer aufgibt, schreibt keine eigene Zeile, das melden die Aufrufer wie bisher |
| M7 | Freunde warten höchstens 15 min auf die Sperre (danach übernimmt der nächste Timer-Lauf), Florian wie bisher 7200 s. Faire Reihenfolge und Vorrang kommen in Stufe 3 – **umgesetzt am 09.10. durch M147** (weich: Freunde fragen seltener, Florian wie bisher) |
| M8 | Freunde laufen in Stufe 1 ohne n8n, Clip-Bot, Mic-Schritt, Zahlen-Abruf, Lager und pve-big; das Abend-Video braucht davon nichts. Auto-Freigabe, 2-Wochen-Video, Warnungen und Kennzahlen folgen in Stufe 2 bzw. 4 |
| M9 | KI nur mit eigenem Claude-Zugang des Freundes (eigene Anmeldung in seinem Ordner, zählt gegen sein Abo). Ohne ihn ist sie aus (leeres `[decide].programm`). Florians Anmeldung gibt es im Bereich des Freundes gar nicht; sie ist nie ein Rückfall |
| M10 | Speicher in Stufe 1: Daten der Freunde nur auf einem eigenen Volume, nie auf der Container-Platte (16 GB, Florians Datenbank) und nie in Florians Puffer. Lager-Pfad `<Instanzordner>/kein-lager`, den es nie gibt (der Ordner gehört root) – jeder Lager-Zugriff scheitert sicher. Freigeben im Puffer und Aufräumen sind erzwungen aus: bei Freunden wird in Stufe 1 nichts gelöscht |
| M11 | Lager ab Stufe 2 (Florian: „wie bei dir, mit Lager“): eigener Unterordner je Freund auf pve-big, Freigabe im Puffer nach 14 Tagen bei bestätigter Kopie wie B5. Geplant: Florians täglicher Abgleich hält pve-big wach; danach läuft je Freund ein Abgleich als dessen eigener Benutzer in der Sandbox, der nur seinen eigenen Unterordner sieht. Ein Freund weckt nie |
| M12 | Weg der Aufnahmen ab Stufe 2: Briefkasten auf dem vServer (kleiner Upload-Dienst, nicht n8n), der Mini holt über Tailscale ab. In Stufe 1 entfallen die Samba-Freigabe je Freund und die eigene psd1 für Freunde; bis dahin kommen Aufnahmen eines Freundes nur von Hand (als root) in seinen Eingang – das reicht für Test und Abnahme |
| M13 | Ein Freund = ein eigener Telegram-Bot, angelegt von Florian (eigener Token, genau eine erlaubte Telegram-ID). Ein gemeinsamer Bot für alle kommt später: rund 95 Stellen im Bot wären umzubauen, und eine Telegram-Datei-ID gilt nur je Bot |
| M14 | Florian ist Betreiber (root) und kann technisch alles sehen. Die Trennung schützt die Freunde voreinander und Florians Daten vor den Freunden |
| M15 | Von Florian übernommen werden nur Rechnerwerte und Spielwissen (`[schnitt].encoder`/`vaapi_geraet`, `[merkmale.waffen]`) – ohne Encoder-Werte schnitten Freunde auf dem Prozessor und hielten die Sperre länger. Gelerntes, Regeln, Sperren und Tokens nie; Startwissen ist die Repo-Konfig |
| M16 | Für Freunde gelten dieselben Regeln: nur starke Szenen, sonst „kein Video, weil …“, einfacher Modus, Zeitzone Europe/Berlin (änderbar in `instanz.toml`). Aufgenommen wird mit Nvidia App oder SteelSeries wie bei Florian |
| M17 | Die Epic-Konto-ID ist Pflicht; der Fortnite-Name ist nur Rückfall (`replay2json` prüft Namen nur auf „enthält“) |
| M18 | Das Whisper-Modell liegt je Freund im eigenen Cache (~480 MB) und wird einmal beim Einrichten geladen; Florians Modell ist für Freunde unsichtbar |
| M19 | Prozessliste und Netz: ~~Beides wird vor Ort geprüft und nur eingeschaltet, wenn es dort funktioniert~~ – geändert nach der Prüfung (Befund B2: Florians Epic-ID steht beim Replay-Lesen auf der Befehlszeile von replay2json): `ProtectProc=invisible` steht im Sandbox-Block jeder Freund-Vorlage (und damit auch in `benutzer-befehl.sh`). Prozesse eines Freundes sehen fremde Prozesse und deren Befehlszeilen nicht. Kann der Container das nicht (Kernel ohne eigenes hidepid je Einhängung, fehlendes Recht), fällt systemd still auf das normale /proc zurück – der Dienst startet trotzdem, nur ohne diesen Schutz; vor Ort prüfen: `systemd-run --wait --pipe -p User=nobody -p ProtectProc=invisible ps -e` zeigt nur den eigenen Prozess. Eine Netzsperre fürs Heimnetz und Tailnet fehlt weiter und wird vor Ort geprüft |
| M20 | Kein Auto-Update, keine automatische Installation: Das Update legt die Vorlagen nur auf die Platte; eingeschaltet wird nur über `benutzer-anlegen.sh`, das Florian selbst startet. Gelöscht werden keine Benutzerdaten |
| M21 | Keine Schema-Migration in Stufe 1. Migrationstest heißt: Ohne `CLIP_INSTANZ` bleiben Konfig und Sperrpfad gleich (Sperrpfad: `tests/test_sperre_gemeinsam.py`); das Update sichert zusätzlich jede Instanz-Datenbank |
| M22 | Zwei Nebenbefunde werden in Stufe 1 mitbehoben: `/paket` ohne Sperre (M5) und ein Whisper-Fehler, der das Abend-Video abbricht (`sitzung.py`, umgesetzt in Schritt 3: M37) |
| M23 | Die parallel laufenden Stufen 4/5 („Nichts mehr von Hand“) kommen zuerst nach main; die Mehrbenutzer-Schritte setzen darauf auf. Berührungen nur in `sitzung.py`, `lernbot.py` und `geschmack.py` |

### Schritt 2 · Instanz-Modus (08.10.2026) – Annahmen bis Florian widerspricht
`CLIP_INSTANZ=I` lädt nur die eigenen Werte (`konfig.lade_instanz`), KI nur mit eigenem Claude-Zugang
(`claude_aufruf`). Aufbau: `docs/MEHRBENUTZER.md`, „Konfig im Instanz-Modus“. Tests: `tests/test_instanz.py`.

| Nr. | Annahme (bis Florian widerspricht) |
|---|---|
| M24 | Instanz-Modus nur über `CLIP_INSTANZ` (setzen die Dienst-Vorlagen). Ordnername a-z, 0-9, - mit 2–27 Zeichen (so bleibt `clip-<name>` ein gültiger Linux-Benutzer), Marke `.clip-benutzer` mit genau diesem Namen; der Ordner darf weder in Florians Bereichen (`/var/lib/clip-pipeline`, `/srv`, `/opt/clip-regie`, Code-Ordner) liegen noch sie umfassen. Jeder Verstoß endet wie jeder Konfig-Fehler mit Exit 2 und `{"fehler": "<Klartext>"}` – keine neue Fehlerform |
| M25 | Zugänge: Als Erstes fliegen `TELEGRAM_*`, `LEARN_BOT_*`, `TIKTOK_*`, `YOUTUBE_*`, `CLAUDE_*`, `ANTHROPIC_*` und `CLIP_EPIC_ID` aus der Umgebung (über den Plan hinaus `CLAUDE_*`/`ANTHROPIC_*`: auch Florians Token oder API-Schlüssel). Danach gilt nur `I/.env` und dort nur diese Namen plus `CLAUDE_CODE_OAUTH_TOKEN`; ein anderer Name (z. B. `CLIP_SPEICHER`) ist ein Konfig-Fehler statt still übergangen. Fehlt `I/.env`, startet der Bot nicht (Exit 2). Die Epic-ID ist in `lade` keine Pflicht – das prüft später das Anlege-/Prüfskript, sonst startete nicht einmal der Bot, um es zu sagen |
| M26 | `instanz.toml`: In `[schnitt]`, `[zeit]` und `[merkmale.waffen]` nur Schlüssel, die die Repo-Konfig dort kennt (ein Tippfehler fällt auf); `[sperre]` nur `datei`/`warten_s`, `[instanz]` nur `claude`. Eine unlesbare `instanz.toml` oder `.env` ist ein Konfig-Fehler statt eines Absturzes |
| M27 | Über den Plan hinaus erzwungen: kein `big.ssh_schluessel` (sonst stünde Florians Schlüsselpfad drin), kein `big.ssh`, kein `puffer.pool_status` (die Datei schreibt Florians Host), `lager.warten_s` = 0 (auf ein Lager, das es nie gibt, wird nicht 4 min gewartet). Zustand von pve-big in `I/db`. Gibt es `I/kein-lager` doch, ist das ein Konfig-Fehler. Der Pfadwächter prüft auch die Unterordner im Puffer |
| M28 | Fehlt `[sperre].warten_s` in der `instanz.toml`, gilt 900 s (M7) – auch ohne die Zeile, die `benutzer-anlegen.sh` später schreibt |
| M29 | KI mit eigenem Zugang (Florian 08.10., ersetzt „KI immer aus“ im Plan): Token aus `I/db/claude-token`, sonst `CLAUDE_CODE_OAUTH_TOKEN` aus `I/.env` – die Datei geht vor, weil der Freund sie später selbst per `/claude` erneuert. Erst beim Aufruf gelesen und nie in der Umgebung des Prozesses (ffmpeg und andere Kinder sehen es nicht); eine unerwartete Form (Leerzeichen, unter 20 Zeichen) heißt „kein Zugang“. claude bekommt eine eigene kleine Umgebung; `[decide].programm` ist bei Freunden leer, damit Florians Weg auch bei einer Panne nie startet |
| M30 | Ohne Token startet claude bei Freunden nie (zentral in `claude_aufruf`). Wo vorher teure Arbeit anfiele, fängt sie gar nicht erst an: KI-Note im Hintergrund (Messung unter der gemeinsamen Sperre), KI-Cutter und KI-Einschätzung für 🔥 Viral (Kontaktbögen) – das schont Florians Rechenzeit. Entscheidung, Stimmung, Beschreibung und Screenshot-Lesen bekommen den Hinweis „kein eigener Claude-Zugang“ und nehmen wie bisher Regeln bzw. Hand-Eingabe |
| M31 | 📋 Stand bei Freunden: ohne Token „KI-Note: aus (kein eigener Claude-Zugang)“; mit Token wie bei Florian, nur „fehlt – eigener Claude-Zugang klappt nicht“ statt des Anmelde-Hinweises. Kein TikTok-Hinweis und kein „ohne Zahlen“ – in Stufe 1 holt bei Freunden niemand Zahlen ab |
| M32 | HOME und Whisper-Cache des ganzen Prozesses setzt `lade` nicht um – das machen die Dienst-Vorlagen (PR 5, `HOME`/`HF_HOME` in `I/cache`); nur claude bekommt seine Umgebung von hier. Bei Florian ändert sich nichts: `lade` hat nur die Weiche am Anfang, `lade_env` liest über `lies_env` (gleiches Ergebnis, Test mit Scheinprojekt), `claude_aufruf` ruft claude wie bisher ohne eigene Umgebung auf. `cli.py` blieb unverändert – Konfig-Fehler aus `lade` enden dort schon mit Exit 2 und JSON |

### Schritt 3 · Freund-Pipeline ohne n8n (08.10.2026) – Annahmen bis Florian widerspricht
Freunde laufen ohne n8n über Timer: `scan --verarbeiten --max 1 --versuche 3` (alle 5 min) und `sitzungen` (alle
10 min, Abend-Video). Tests: `tests/test_scan_grenzen.py`, `tests/test_sitzung.py` (Whisper-Fehler, aufgegebenes Match).

| Nr. | Annahme (bis Florian widerspricht) |
|---|---|
| M33 | `scan --verarbeiten --max N`: je Lauf nur die N ältesten offenen Matches (nach Startzeit) – die gemeinsame Sperre ist so nur für ein Match belegt und zwischen den Timer-Läufen frei. In der JSON-Zeile bleibt „offen“ die volle Liste, „verarbeitet“ nennt nur diesen Lauf, der Rest steht im Log. Ohne `--max`/`--versuche` exakt wie bisher (Florian, n8n). Beide Werte ab 1; ohne `--verarbeiten` wirken sie nicht |
| M34 | `--versuche N`: Jeder Fehlschlag wird eine Zeile in `ereignisse` (`verarbeitung_fehler`, Match, Fehlertext). Gezählt wird auch Unerwartetes – sonst hielte ein kaputtes ältestes Match bei `--max 1` alle neueren für immer auf –, nie aber, was nicht am Match liegt: Speicher offline, Konfig, Datenbank belegt. Dann endet der Lauf wie bisher (Exit 3 bzw. 1), und der nächste versucht es wieder. Ohne `--versuche` beendet Unerwartetes den Lauf wie bisher (Exit 1). Gezählt werden alle Zeilen des Matches: Wer ein aufgegebenes Match von Hand auf `neu` setzt, gibt ihm genau einen weiteren Versuch |
| M35 | Ab N Fehlschlägen: Status `fehler` (der CHECK erlaubt ihn schon, keine Schema-Änderung), der Grund kommt zusätzlich in `matches.hinweise`, und der eigene Lern-Bot sagt es einmal ohne Fachbegriffe (`match_fehler:<ID>`: „⚠️ Ein Match vom 08.10. um 20:15 Uhr klappt nicht – ich habe es 3-mal versucht und lasse es aus. Deine anderen Matches laufen normal weiter.“). In der JSON-Zeile hat ein gescheiterter Eintrag dann zusätzlich „versuch“ und beim Aufgeben „status“: „fehler“. Gelöscht wird nichts; `pipeline process <ID>` (in der Instanz) holt das Match jederzeit nach – render setzt wieder `verarbeitet` |
| M36 | Abend-Video: Auf ein Match mit Status `fehler` wartet der Abend nicht die 2 h (`[sitzungen].warten_h`) – es kommt nicht mehr; der Hinweis nennt es weiter, im Satz „kein Video“ zählt es aber nicht als „noch nicht fertig“ (sein Bot sagte schon „klappt nicht“). Bei Florian setzt nichts `fehler` (n8n-Weg), dort bleibt alles gleich |
| M37 | Whisper-Fehler im Abend-Video (M22): `stimmung.analysiere` wird abgefangen wie im Lern-Bot. Scheitert die Messung mit Sprache, misst derselbe Lauf die Szenen gleich noch einmal ohne Sprache (Lautstärke, Kills) – bloßes Abfangen reicht nicht, die Clips des Abends würden sonst gar nicht zu Szenen, und es käme „kein Video“ (nachgestellt). Die Wörter fehlen dann; bei Florian holt der Mic-Schritt sie nach, sobald Whisper wieder geht, bei Freunden (Stufe 1 ohne Mic-Schritt) bleiben sie weg. Scheitert auch das, geht es mit den Szenen weiter, die schon da sind; fehlende holt der Nachtrag. Der Hinweis steht im Ergebnis und in `sitzungen.hinweis` vor dem Grund für „kein Video“ (📋 zeigt weiter den Grund). Gilt auch für den Nachtrag. Hilft auch Florian: Vorher brach jeder Lauf ab, die Statuszeile blieb bei „ich baue dein Video“, und alle 10 Minuten begann alles von vorn |

### Schritt 4 · Trennung Ende-zu-Ende geprüft (08.10.2026) – Annahmen bis Florian widerspricht
Nur Tests, kein Produktionscode: `tests/test_isolation.py` (drei Benutzer, ein Squad-Match) und
`tests/test_n8n_einstieg.py` (`deploy/n8n-lauf.sh`, bisher ohne Test). Fehler in Schritt 1–3 fanden sie keine.

| Nr. | Annahme (bis Florian widerspricht) |
|---|---|
| M38 | Generalprobe mit echten Prozessen wie im Dienst: Florian mit seiner Standard-Konfig (`pipeline.toml`, `lokal.toml`, `.env` in einer Temp-Installation), max und eva als Instanzen aus derselben Installation. Alle drei spielten dasselbe Squad-Match (gleiche Session-ID, gleicher Replay-Name), jeder mit eigener Aufnahme und eigener Epic-ID (Florian 1 Kill, max 2, eva 3); Florians Zugänge stehen absichtlich zusätzlich in der Umgebung der Freunde. Freunde laufen mit den Schaltern ihres Timers (`--max 1 --versuche 3`), danach `stimmung` für die Momente. Attrappe ist nur replay2json: Sie liest das Match aus der Replay-Datei und setzt „ich“ nach `--ich` wie das echte Programm – die feste Antwort von `falsches_replay2json` kann drei Spieler einer gemeinsamen Installation nicht unterscheiden. Laufzeit rund 20–30 s |
| M39 | Der Test prüft das Schreiben, nicht das Lesen: Im Test laufen alle unter demselben Linux-Benutzer. Dass ein Freund fremde Dateien nicht einmal lesen kann, sichern erst eigene Benutzer und die Sandbox der Dienst-Vorlagen (PR 5/7, Prüfung vor Ort). Lese-Lecks, die etwas ändern (fremde Aufnahme, fremde Epic-ID, fremdes Replay), fängt der Test über die Daten ab: Jede Datenbank hat genau ihre Aufnahme, ihre Kills und ihre Replay-Länge |
| M40 | `deploy/n8n-lauf.sh` bleibt unverändert – der Test zeigte keine Lücke. Eine zweite Zeile im SSH-Befehl liest das Skript gar nicht (ausgeführt wird höchstens der geprüfte Vertragsbefehl). Die Umgebung der SSH-Sitzung reicht es an die Pipeline weiter; sshd nimmt ab Werk nur `LANG`/`LC_*` an. Für PR 5/7 heißt das: `AcceptEnv` und `PermitUserEnvironment` nicht erweitern, sonst käme ein `CLIP_INSTANZ` von n8n bis zur Pipeline |

### Prüfung von Schritt 1–4 (08.10.2026) – Annahmen bis Florian widerspricht
Zwei kleine Lücken an der Grenze zwischen Freund und Florian, nicht blockierend (vor Ort fangen Dienst-Vorlagen und
Sandbox sie ab), jetzt auch ohne sie geschlossen. Nur `konfig.py`; Tests: `tests/test_instanz.py`.

| Nr. | Annahme (bis Florian widerspricht) |
|---|---|
| M41 | Die Sperrdatei eines Freundes (`[sperre].datei`) muss es beim Laden schon geben – sonst Konfig-Fehler (Exit 2 mit Klartext). Vorher legte ein Tippfehler im Pfad still eine eigene Sperre an, sobald der Ordner beschreibbar war (Lauf von Hand als root, Abnahme ohne Sandbox), und der Freund rechnete neben Florian her. Schärft M4 („nie eine Ersatzsperre“). Florians Datei gibt es auf dem Mini längst; auf einem neuen Rechner muss sie vor dem ersten Freund da sein (Aufgabe des Anlege-Skripts, PR 7). Ein Pfad, den es gibt, den Florian aber nicht nutzt, fällt hier nicht auf – das prüft das Prüfskript (PR 7) gegen Florians `sperre.pfad` |
| M42 | `CLIP_INSTANZ` gesetzt, aber leer oder nur Leerzeichen, ist ein Konfig-Fehler (Exit 2 mit Klartext). Vorher lief der Prozess still mit Florians `.env`, `lokal.toml` und Datenbank. Bei Florian ist die Variable gar nicht gesetzt – für ihn ändert sich nichts (ergänzt M24) |

### Schritt 5 · Dienst-Vorlagen mit Sandbox (08.10.2026) – Annahmen bis Florian widerspricht
Vorlagen je Freund in `deploy/benutzer/` (`clip-freund-bot@`, `clip-freund-scan@` + Timer, `clip-freund-abend@` +
Timer) mit demselben Sandbox-Block; `alles-aktualisieren.sh` kennt Freunde. Aufbau: `docs/MEHRBENUTZER.md`, „Dienste je
Freund“. Tests: `tests/test_deploy_benutzer.py`.

| Nr. | Annahme (bis Florian widerspricht) |
|---|---|
| M43 | Eigener Ordner `deploy/benutzer/`, nicht `deploy/systemd/` (dort schaltet das Update bestimmte neue Dienste ein). Das Update legt die Vorlagen nur hin, wenn es mindestens einen Freund gibt (Ordner unter `/var/lib/clip-benutzer` mit Marke, die seinen Namen nennt, und Benutzer `clip-<name>`) – ohne Freund läuft Florians Update genau wie bisher, ohne neue Zeile. Den ersten Freund richtet `benutzer-anlegen.sh` samt Vorlagen ein (nächster Schritt). Übernommen wird wie bei Florians Diensten (von Hand geänderte bleiben), eingeschaltet nie |
| M44 | Sicherung jeder Freundes-Datenbank im Umstell-Teil unter der Sperre, vor dem Code-Wechsel, als `clip-<name>` (er liest nur, was ihm gehört) nach `I/db/vor-update-<Zeit>.db`; nie gelöscht, eine vorhandene nie überschrieben. Nur echte Dateien: Ist die Datenbank, ihr Ordner oder eine Nebendatei (-wal, -shm, -journal) ein Link, gibt es keine Kopie – sonst könnte ein Link Florians Datenbank in den Ordner des Freundes kopieren oder das Update an einem schlafenden Netzlaufwerk hängen lassen (root folgt dort keinem Link). Platzprüfung wie bei Florian (doppelte Größe + 50 MB frei), damit eine Sicherung das Freunde-Volume nie füllt |
| M45 | Scheitert die Sicherung eines Freundes, sagt das Update es (⚠️) und stellt trotzdem um – Florians Update hängt nie an einem Freund; neue Spalten kommen bei Updates nur dazu. Laufende Freundes-Bots startet das Update nach Florians Bots neu, der Rückweg ebenso; Timer-Dienste starten bei jedem Lauf frisch |
| M46 | Über den Plan hinaus: Beide Timer-Dienste brechen nach 2 h ab (`TimeoutStartSec=2h`, wie clip-mikro) – ein hängender Lauf eines Freundes hält die gemeinsame Sperre sonst unbegrenzt, und Florian rechnete nicht mehr. `SuccessExitStatus=3 4` wie bei Florian (4 = Sperre belegt, der nächste Timer-Lauf übernimmt). Das Abend-Video läuft ohne `--ohne-claude`: ohne eigenes Token startet claude ohnehin nie (M29/M30) – **geändert am 09.10. durch M146**: 1 h statt 2 h, kürzer als Florians Wartezeit von 2 h |
| M47 | HOME und `XDG_CACHE_HOME` = `I/cache`, `HF_HOME` = `I/cache/huggingface` (wie in der Generalprobe, M32/M38), Arbeitsordner `/opt/clip-pipeline` wie bei Florian. Die Sperrdatei ist fest `/var/lib/clip-pipeline/pipeline.lock` = Florians `sperre.pfad` und der Pfad aus `config/instanz.beispiel.toml` (ein Test wacht darüber); verlegt Florian seine Datenbank in `lokal.toml`, fällt das beim Prüfskript auf (PR 7). Keine weiteren Härtungen über den Plan hinaus (Prozessliste, Netz: M19, vor Ort) |
| M48 | Kernel-Nachbau: Die Mounts werden aus dem Sandbox-Block der Vorlage gelesen und in einem privaten Mount-Namensraum über einer Scheinwurzel gesetzt, wie systemd es tut (Quellen aus der normalen Sicht, Ziele in einer eigenen Wurzel); nichts davon ist außerhalb sichtbar. Florians Ordner hat darin die Rechte von heute (0755, Datenbank 0644) – geschützt wird durch die Sandbox, nicht durch Rechte. `ProtectSystem`, `ProtectHome` und gesperrte Pfade baut er nicht nach: die prüft der Vorlagen-Test als Schlüssel, `systemd-analyze verify` auf Tippfehler |
| M49 | CI bleibt unverändert: Der Kernel-Nachbau bräuchte dort einen eigenen Schritt mit sudo, und ob er auf den GitHub-Rechnern sicher läuft, lässt sich ohne Push nicht prüfen. Dort wird er übersprungen; Vorlagen-, Update- und Skript-Tests laufen in den bestehenden drei Teilen. Den Nachbau gibt es lokal als root (hier grün) und vor Ort |

### Schritt 6 · Freunde-Volume (08.10.2026) – Annahmen bis Florian widerspricht
`deploy/pve-mini/freunde-volume.sh [--probe] [--groesse GB]` auf pve-mini: ein Volume für alle Freunde, im CT unter
`/var/lib/clip-benutzer`. Nur der Speicherteil von PR 6 – Samba je Freund und die Freund-psd1 entfallen (M12).
Anleitung: `docs/MEHRBENUTZER.md`, „Einmal für alle Freunde: Speicher“. Tests: `tests/test_deploy_benutzer.py`.

| Nr. | Annahme (bis Florian widerspricht) |
|---|---|
| M50 | Ein Volume für alle Freunde (eins je Freund kostete je Freund einen CT-Neustart), Standard 100 GB (`--groesse`), als `mp2` (`MP=` änderbar, nie `mp0`: das entfernt `rette-clips-start.sh` bei einer Wiederholung). Wie der Puffer: `backup=0` (Videos gehören nicht ins vzdump-Backup; ein Lager für Freunde kommt mit Stufe 2), `noatime`, `discard`, Reserve für root aus (`tune2fs -m 0`, solange der CT ohnehin aus ist). Die Größe ändert das Skript nie selbst; es zeigt `pct resize 102 mp2 <GB>G` (nur wachsen) |
| M51 | Pool-Grenze strenger als beim Puffer: Gerechnet wird mit ganz vollem Freunde-Volume **und** ganz vollem Puffer – was dem Puffer noch fehlt, zählt wie belegt; beide füllen sich mit Videos. Vor dem Puffer hatte der Pool laut Sprint-Log nur ~141 GB frei, 100 GB passen dann wohl nicht. Über 90 % (oder Metadaten ab 90 %) bricht das Skript ab und nennt die größte Größe, die passt (ab 10 GB). Die übrigen Volumes (CT-Platten) zählen mit ihrem heutigen Stand. Füllt sich der Pool später, warnt wie bisher die Morgenprüfung (85/90 %) |
| M52 | Der CT muss laufen (das Skript sieht im CT nach), sonst Abbruch wie in `puffer-zurueck.sh`. Ohne Volume darf `/var/lib/clip-benutzer` auf der CT-Platte nur fehlen oder leer sein – sonst verdeckte das Volume den Inhalt; ist dort schon etwas anderes eingehängt: ebenso Abbruch. Sperre und Hinweis „nicht während eines Spielabends“ wie beim Puffer. Die CT-Konfig wird erst nach dem „j“ gesichert (`/root/freunde-volume/<zeit>/`, der erste Stand bleibt in `original/`) – ein Lauf, der nichts ändert, schreibt nichts |
| M53 | Wurzel des Volumes root, 0711 (wie im Plan): Jeder Freund kommt in seinen eigenen Ordner, die Namen der anderen sieht er nicht. Gesetzt wird nur, wenn das Ziel im CT eingehängt ist und auf einem anderen Speicher liegt als `/` und der Puffer (Gerätenummer) – sonst nichts. Keine eigene Marke: `benutzer-anlegen.sh` prüft dieselbe Gerätenummer (PR 7) |
| M54 | Rückweg `/root/freunde-volume/zurueck.sh`, geschrieben vor der ersten Änderung (gilt auch, wenn danach etwas schiefgeht); ein vorhandenes bleibt. Es hängt nur aus (`pct set --delete mpN`, Proxmox behält das Volume als `unusedN`), nennt keinen Löschbefehl und weigert sich, solange Dienste von Freunden laufen oder die Sperre belegt ist. Das Skript merkt sich den Namen des Volumes (`/root/freunde-volume/volume`); ein neuer Lauf hängt genau dieses wieder ein, statt ein leeres neues anzulegen – sonst lägen die Daten der Freunde unbemerkt in einem unbenutzten Volume |

### Schritt 7 · Freund anlegen und prüfen (08.10.2026) – Annahmen bis Florian widerspricht
Ein Befehl legt einen Freund an (`deploy/benutzer/benutzer-anlegen.sh <name> [--probe]`), dazu Prüfen, Stilllegen und
ein Einzelbefehl in seiner Sandbox; in der Instanz `pipeline benutzer pruefen|einrichten` (Vorlagen
`clip-freund-pruefen@`, `clip-freund-einrichten@`). Anleitung: `docs/MEHRBENUTZER.md`, „Neuen Freund anlegen“; für
Freunde `docs/FREUNDE.md`. Tests: `tests/test_benutzer.py`, `tests/test_deploy_benutzer.py`.

| Nr. | Annahme (bis Florian widerspricht) |
|---|---|
| M55 | `pipeline benutzer pruefen\|einrichten` nur im Instanz-Modus, sonst Exit 2 mit JSON. `main` öffnet dafür keine Datenbank (`ohne_db`) – sonst legte schon das Nachsehen eine an; `einrichten` öffnet seine selbst. Gestartet nur über die Vorlagen (derselbe Sandbox-Block, `oneshot`, nie eingeschaltet, kein Timer; nach 5 min bzw. 1 h ist Schluss) |
| M56 | `pruefen` sieht nur nach: lstat und access, dazu die **Namen** in vier lokalen Ordnern – in Florians Ordner nur die Sperrdatei, in der Konfig nur `pipeline.toml`, `/srv` leer, unter den Freunden nur der eigene (über den Plan hinaus: nur so fallen andere Freunde auf, deren Namen er nicht kennt). Nie Inhalte, nie das Lager. „Sichtbar“ heißt da **und** lesbar, beschreibbar oder (Ordner) betretbar; Gesperrtes (`InaccessiblePaths`, Rechte 000) zählt nicht. Dazu Florians TikTok-Zugang (`publikum-oauth.json`), der Instanz-Ordner selbst und die Marke dürfen nicht beschreibbar sein (sonst ließen sich `.env` und `instanz.toml` austauschen). Fehlende `LEARN_BOT_TOKEN`/`CLIP_EPIC_ID` sind Befunde, eine fehlende Telegram-Zahl und alles zu claude nur Hinweise |
| M57 | `einrichten`: Datenbank; Whisper-Modell über `faster_whisper.utils.download_model` (das lädt auch `WhisperModel`) – erst `local_files_only`, so lädt ein zweiter Lauf nichts; nur mit `HF_HOME` im eigenen Ordner (setzt der Dienst), sonst landete es im Home dessen, der aufruft. Musik wie im einfachen Modus: seine Genres bis 16 freie Titel (= Grenze von `musik.nachschub`), unter der gemeinsamen Sperre, ohne den Tages-Merker des Nachschubs. Fehlt Whisper, ist NCS nicht erreichbar oder die Sperre belegt: Hinweis, eingerichtet ist die Instanz trotzdem (Szenen ohne Sprache, Musik kommt tagsüber von selbst) |
| M58 | Die `instanz.toml` eines neuen Freundes baut `benutzer.instanz_toml` aus Florians wirksamer Konfig, aufgerufen als pipeline mit leerer Umgebung (`env -i`, so sieht es sein Dienst): Sperre (`sperre.pfad`, wie der Einzeiler in `alles-aktualisieren.sh`) mit 900 s, `[schnitt]` encoder/vaapi_geraet, `[merkmale.waffen]` – nur Schlüssel, die die Repo-Konfig kennt. Ist Florians Sperre nicht die in den Vorlagen eingebundene: Abbruch (M47). Eine vorhandene `instanz.toml` bleibt |
| M59 | Reihenfolge im Anlege-Skript: alle Prüfungen (Name, Volume, Ordner, Benutzer, Sperre, Konfig) vor der einen j/N-Frage, die Zugänge vor der ersten Änderung; eingeschaltet wird erst, wenn die Vorab-Prüfung (root) und das Einrichten mit Prüfung **in seiner Sandbox** grün sind – greift die Sandbox im Container nicht, laufen keine Dienste. Reserviert: pipeline, benutzer, freund, root, admin und die Namen deiner Dienste ohne „clip-“ (bot, lernbot, lager …) |
| M60 | Zugänge: Bot-Token, Epic-Konto-ID (32 Zeichen, klein geschrieben) und Telegram-Zahl verdeckt (`read -s`), nach Form geprüft, nur per `printf` (eingebaut, nie auf einer Befehlszeile) in `I/.env` (erst 0600, dann root:clip-<name> 0640). Ein neuer Token wird **vor** dem Schreiben mit Florians `.env` (Produktion und Lern-Bot-Stand) und denen aller Freunde verglichen (über eine Pipe, als SHA-256) – je Token gibt es nur einen Empfänger, ein doppelter nähme Florians Bot die Nachrichten weg. Vorhandene Werte werden nie neu gefragt oder überschrieben, fehlende angehängt |
| M61 | Die Telegram-Zahl ist bis zum Einladungslink (nächster Schritt) freiwillig; ohne sie bleibt nur sein Bot aus, die Timer laufen. Der Block „KOPPLUNG“ im Anlege-Skript ist die Stelle, an der Schritt 4 die Kopplung einsetzt |
| M62 | root fasst nichts an, was dem Freund gehört: Puffer-Marken, `eingang/` und `replays/` legt er als er selbst an (`runuser`). Besitzer und Rechte setzt root nur am Instanz-Ordner, dessen direkten Unterordnern und seinen eigenen Dateien darin – Einträge im root-eigenen Ordner kann der Freund nicht austauschen. Vorlagen: nur fehlende hinlegen, abweichende bleiben (wie beim Update) |
| M63 | Ein zweiter Lauf ändert nichts und fragt nur das eine „j“. Das Einrichten läuft trotzdem jedes Mal mit: Es holt nur Fehlendes nach (z. B. ein Whisper-Modell, das beim ersten Mal nicht kam) und prüft die Trennung erneut |
| M64 | `benutzer-pruefen.sh` vergleicht (Gerät, Inode) der Sperrdatei aus der Sandbox mit Florians Datei draußen – so ist belegt, dass wirklich seine Datei eingebunden ist. Bot-Tokens über alle `.env` (auch Freunde untereinander), nur Namen in der Ausgabe. Bot-Link per getMe in Python (Token nie auf einer Befehlszeile); ohne Netz nur ein Hinweis. `--vorab` = ohne Dienst und Link (für das Anlege-Skript vor dem Einschalten) |
| M65 | Florians Rechte schärfen ist freiwillig (j/N, Standard nein), nur `chmod`: sein Ordner nur noch passierbar (zur Sperrdatei), alles darin außer der Sperrdatei nur für ihn, dazu `.env` und `lokal.toml` beider Checkouts. `/srv` (Puffer, Lager, Samba) bleibt unangetastet – zu viel Risiko für seinen Betrieb, und in der Sandbox gibt es `/srv` ohnehin nicht. Rückweg-Skript mit den alten Rechten unter `/root/benutzer-rechte/`, geschrieben vor der Änderung |
| M66 | `benutzer-stilllegen.sh`: Bot und Timer aus (`disable --now`), laufende Schritte stoppen (ein halb fertiges Match holt sein nächster Lauf nach); Daten, Benutzer und Zugänge bleiben, kein `userdel`. Wieder an mit `benutzer-anlegen.sh` |
| M67 | `benutzer-befehl.sh`: ein pipeline-Befehl als der Freund über `systemd-run --wait --pipe --collect` mit genau den Eigenschaften aus dem Sandbox-Block der installierten Vorlage `clip-freund-bot@` (`%i` = Name) – das Einfachste ohne vierte Vorlage und ohne Lücke (ein `runuser` ohne Sandbox sähe Florians Ordner mit den Rechten von heute, M48). Fehlt im Block ein Kern-Schlüssel, startet nichts; der Exit-Code ist der von pipeline. Ob systemd-run im Container alle Eigenschaften annimmt, wird vor Ort geprüft (wie M19) |
| M68 | Aufnahmen eines Freundes kommen in Stufe 1 nur von Hand (M12): für die Abnahme eine Kopie eines Abends von Florian, kopiert **als der Freund** (`runuser -u clip-<name> -- cp -n --preserve=timestamps …`) – Florians Puffer-Dateien sind für alle lesbar (Samba 0644), root schreibt nie in die Ordner des Freundes, bei Florian wird nichts verschoben oder gelöscht |

### Schritt 8 · Einladungslink statt Telegram-Zahl (08.10.2026) – Annahmen bis Florian widerspricht
Der Freund tippt einen Link an und drückt Start, statt seine Telegram-Zahl zu suchen: `pipeline benutzer koppeln`
(Vorlage `clip-freund-koppeln@`), gestartet von `benutzer-anlegen.sh`. Aufbau: `docs/MEHRBENUTZER.md`, „Einladungslink“.
Tests: `tests/test_koppeln.py`, `tests/test_deploy_benutzer.py`.

| Nr. | Annahme (bis Florian widerspricht) |
|---|---|
| M69 | `pipeline benutzer koppeln` nur im Instanz-Modus (sonst Exit 2, ohne Bot-Token ebenso), gestartet nur über die Vorlage `clip-freund-koppeln@` (derselbe Sandbox-Block, `oneshot`, nie eingeschaltet, nach 20 min Schluss). Telegram fragt es mit denselben Anfragen wie der Lern-Bot (`lernbot.anfragen`, IPv4 nach `[lernbot].nur_ipv4`) – keine neue Bibliothek |
| M70 | Je Bot nur ein Empfänger: koppeln läuft nur, solange keine Telegram-Zahl in `I/.env` steht (`LEARN_BOT_ALLOWED_USER_ID` oder `TELEGRAM_ALLOWED_USER_ID`) – ohne sie beendet sich sein Bot, bevor er Telegram fragt. Steht schon eine drin: Exit 1, Telegram wird gar nicht gefragt. Das Anlege-Skript hält einen trotzdem laufenden Bot an und schaltet ihn erst nach der Kopplung ein. Meldet Telegram einen zweiten Empfänger (409), bricht koppeln ab, ohne etwas zu speichern. Kein `Conflicts=` in der Vorlage: Ein versehentlicher Start hielte sonst einen laufenden Bot an |
| M71 | Einmal-Code aus `secrets` (32 Zeichen, ≈ 192 Bit), gilt 15 min und für eine Kopplung, verglichen in konstanter Zeit. Es zählt nur „/start <code>“ im Einzel-Chat von einem Menschen, der selbst schreibt (nicht weitergeleitet). Ein falscher Start bekommt einmal je Chat „Dieser Einladungslink gilt nicht (mehr) …“, alles andere keine Antwort. Gelesenes wird bei Telegram abgehakt (auch ohne Treffer) – sein Bot sieht den Code später nicht |
| M72 | Code und Link nie im Log: Der Link liegt nur in `I/db/einladung.json` (0600, mit der Lauf-Nummer von systemd); koppeln entfernt die Datei am Ende (kein Benutzer-Datum – der Code gilt danach nicht mehr). Das Anlege-Skript zeigt nur den Link des neuen Laufs (Lauf-Nummer vor dem Start gemerkt) oder einer Einladung, die schon läuft – nie einen liegen gebliebenen. httpx bleibt wie im Lern-Bot stumm (Adressen mit Bot-Token), `telegram` höchstens INFO (DEBUG zeigte die Nachrichten); die Fehlermeldung zu einem ungültigen Token (enthält ihn) wird nie weitergegeben |
| M73 | Ergebnis in `I/db/kopplung.json` (0600, atomar): Zahl, Vorname (nur druckbare Zeichen, höchstens 64), Zeit. Das Anlege-Skript liest sie als root, ohne einem Link zu folgen (nur normale Datei, höchstens 4 KB), zeigt nur den Vornamen und hängt `LEARN_BOT_ALLOWED_USER_ID=<Zahl>` per eingebautem printf an seine `.env` (bleibt root:clip-<name>, 0640) – diesen Namen liest `lernbot.starte`. Liegt schon eine `kopplung.json` (z. B. nach Strg+C), wird sie übernommen, ohne neu einzuladen |
| M74 | Ablauf: Kopplung nach dem Einrichten (Schritt 8 von 10), dann Einschalten und Prüfung. `systemctl start` läuft im Hintergrund (kehrt erst am Ende der Einladung zurück, hängt sich an eine laufende an) – mit `--no-block` sähe das Skript den Dienst kurz als „nicht laufend“. Strg+C beendet nur das Warten (die Einladung gilt weiter); die Timer gehen trotzdem an, sein Bot erst mit Zahl, ohne Kopplung bleibt nur er aus (wie M61). Die Frage nach der Telegram-Zahl entfällt (ersetzt M60/M61 in diesem Punkt). Mitbehoben: `benutzer-stilllegen.sh` hält auch eine laufende Einladung an und sieht laufende Schritte jetzt auch im Zustand „activating“ – `systemctl is-active -q` meldete einen laufenden oneshot-Schritt als aus, er lief weiter |

### Schritt 9 · Eigener Claude-Zugang per /claude (08.10.2026) – Annahmen bis Florian widerspricht
Florian: „Eigener Claude-Zugang“. Der Freund verbindet sein Claude-Abo selbst: `/claude` in seinem Bot
(`lernbot_claude`) startet `claude setup-token`, schickt den Anmelde-Link, nimmt den Code an und speichert das Token in
`I/db/claude-token`. Aufbau: `docs/MEHRBENUTZER.md`, „Eigener Claude-Zugang per /claude“; für Freunde
`docs/FREUNDE.md`. Tests: `tests/test_claude_verbinden.py`.

| Nr. | Annahme (bis Florian widerspricht) |
|---|---|
| M75 | `/claude` gibt es nur im Bot eines Freundes: `lernbot.baue_app` hängt `lernbot_claude` nur im Instanz-Modus ein, `/tiktok` nur bei Florian (Zahlen holt bei Freunden in Stufe 1 niemand ab). Florians Bot bleibt gleich – keine neuen Handler, keine Gruppe -1, Hilfe Zeichen für Zeichen wie bisher (Test). Kein eigener Knopf: `/claude` steht antippbar in Hilfe und 📋 |
| M76 | `claude setup-token` läuft in einem Pseudo-Terminal (1000 Spalten, damit der Link nicht umbricht) als Kind des Bots in eigener Prozessgruppe, mit derselben kleinen Umgebung wie `claude_aufruf` in der Instanz (`claude_aufruf.instanz_umgebung`, Programm wie dort; dazu nur `TERM`) – nichts aus der Umgebung des Bots (Bot-Token, Florians Zugänge). Gelesen wird im Event-Loop, kein Thread. Die Anmeldung ist eine eigene asyncio-Aufgabe, nicht `app.create_task` – darauf wartet `Application.stop`, ein Neustart des Bots hinge sonst bis zu 10 min; beim Beenden des Bots wird sie abgebrochen |
| M77 | Link = erste vollständige https-Adresse mit „oauth“ und „authorize“, nachdem die Steuerzeichen entfernt sind (die Oberfläche setzt Wörter per Cursor-Sprung, der Link steht zusätzlich in einer Hyperlink-Folge). Mit dem echten `claude setup-token` 2.1.294 bis zum Link nachgeprüft (nach 0,3 s, vollständig). Den Tausch Code → Token gibt es hier nur mit der Attrappe; das Token-Format (`sk-ant-oat01-…`) kommt aus der Spec |
| M78 | Nach dem Link ist die nächste Textnachricht (kein Befehl) der Code: sofort gelöscht (scheitert das, egal) und mit `\r` ins Terminal geschrieben – nur, wenn sie wie ein Code aussieht (eine Zeile, 8–2048 sichtbare Zeichen ohne Leerzeichen), sonst „sieht nicht nach dem Code aus“ und die Anmeldung wartet weiter; während der Prüfung „⏳ Einen Moment“. Texte vor dem Link oder ohne Anmeldung gehen unverändert an die übrigen Handler: Der Text-Handler steht in Gruppe -1 und beendet die Verarbeitung nur, wenn der Text zu `/claude` gehört |
| M79 | Token = `sk-ant-oat01-` und mindestens 20 Zeichen aus A-Z, a-z, 0-9, _ und -; in der Ausgabe nur mit einem Zeichen dahinter (sonst wäre es halb gelesen). Eine Fehlermeldung von claude („error“, „Press Enter to retry“ – so meldet das echte einen falschen Code) beendet das Warten sofort. Gespeichert atomar in `I/db/claude-token` (0600, eine Zeile, `claude_aufruf.token_speichern`), gelesen beim nächsten Aufruf ohne Neustart (M29). Ein neues `/claude` ersetzt ein altes Token erst, wenn das neue da ist |
| M80 | Fristen: Link 60 s, Token nach dem Code 60 s, die ganze Anmeldung 10 min ab `/claude`. Danach, bei jedem Fehler, bei einem neuen `/claude` (die alte Anmeldung endet still) und beim Beenden des Bots endet claude: TERM an seine Prozessgruppe, nach 2 s KILL, dann abgeholt. Gespeichert wird nur ein Token. Die Antwort nennt den Ausweg (Token direkt schicken); fehlt claude oder liegt es an einem verbotenen Ort: „Florian muss Claude einmal auf dem Mini installieren“ |
| M81 | Ein Token in einer Textnachricht (z. B. aus `claude setup-token` auf seinem PC) wird jederzeit gespeichert, die Nachricht gelöscht und eine laufende Anmeldung beendet – nur vom Freund selbst, wie alles in seinem Bot |
| M82 | Code, Token und Link nie im Log (nur was passiert und die Art eines Fehlers – Fehlermeldungen können Zugänge enthalten), nie in der Datenbank, nie zurück in den Chat; die Ausgabe von claude bleibt nur im Speicher der Anmeldung und wird mit ihrem Ende verworfen. Im Bot eines Freundes steht der Logger `telegram` fest auf INFO (bei DEBUG schrieb er jede Nachricht ins Log) |
| M83 | Texte: 📋 „KI-Note: aus – verbinde dein Claude mit /claude“ (ersetzt den Satz aus M31), bei einem Token, das nicht mehr klappt, „… neu verbinden mit /claude“; die Hilfe des Freundes hat eine `/claude`-Zeile (einfach angehängt, im Experten-Modus statt der `/tiktok`-Zeile); `benutzer-pruefen.sh` nennt `/claude` statt `claude setup-token` |

### Prüfung von Schritt 5–9 (08.10.2026) – Annahmen bis Florian widerspricht
Ein blockierender Befund im freiwilligen Zusatz von `benutzer-anlegen.sh`, behoben; dazu ein veralteter Kommentar in
`clip-freund-koppeln@` (Start im Hintergrund statt `--no-block`, M74). Tests: `tests/test_deploy_benutzer.py`.

| Nr. | Annahme (bis Florian widerspricht) |
|---|---|
| M84 | Florians Rechte schärfen (M65) nur an dem, was `pipeline` gehört: Alle seine Dienste laufen als pipeline, an den Rechten des Besitzers ändert sich nichts – sie merken also wirklich nichts. Gehört etwas root (z. B. eine `lokal.toml`, die root mit nano angelegt hat, PUFFER.md R5), bleibt es mit einem Hinweis, wie es ist. Vorher wurde sie root:root 0600; pipeline konnte sie nicht mehr lesen, und alle Dienste Florians wären beim Laden der Konfig abgestürzt (beide Bots, Timer, n8n-Schritte ohne JSON-Zeile), bis er das Rückweg-Skript gefunden hätte. Kein `chgrp` (änderte, wem seine Dateien gehören), keine Prüfung danach (die Regel schließt den Fall aus). Ein Freund sieht die Datei in seiner Sandbox ohnehin nicht |

### Stufe 2, Schritt 1 · Briefkasten auf dem vServer (09.10.2026) – Annahmen bis Florian widerspricht
Stufe 2 „Freunde liefern selbst“ hat sechs Schritte (Plan aus zwei Entwürfen, gewählt von einem Richter); das Löschen im
Briefkasten wird erst nach Florians Ja gebaut. Schritt 1 sind nur drei Skripte für den vServer
(`deploy/vserver/briefkasten-{einrichten,freund,pruefen}.sh`, je mit Probe, j/N und Rückweg, löschen nie), keine Zeile
Pipeline-Code; bei Florian ändert sich nichts. Anleitung: `docs/BRIEFKASTEN.md`. Tests: `tests/test_deploy_briefkasten.py`
(Attrappen; dazu ein echter sshd 9.6p1 in einem privaten Mount- und Netz-Namensraum, in der CI übersprungen). Bis zum
Ja kann im Briefkasten nichts gelöscht werden: Das Abholprofil ist nur lesend, dem Hochladen fehlt remove.

| Nr. | Annahme (bis Florian widerspricht) |
|---|---|
| M85 | Briefkasten = zweite OpenSSH-Instanz aus der Distribution auf dem vServer-Host (internal-sftp, chroot je Freund): eigener Dienst `briefkasten-sshd`, eigene Konfig und eigener Hostschlüssel in `/etc/briefkasten`, Port 2222 (`--port`). Kein Docker, keine Domain, kein TLS, kein eigener Server-Code; HTTPS/tus nur, falls Freunde Port 2222 nicht erreichen (vorher 443 prüfen). Der normale SSH-Zugang und n8n bleiben unberührt. `/run/sshd` teilen sich beide – der Dienst legt ihn nur an (kein RuntimeDirectory), `KillMode=process` lässt laufende Uploads bei Neustart und Reload weiterlaufen. Eine neue Konfig geht erst in Betrieb, wenn `sshd -t` sie als `sshd_config.neu` angenommen hat; eine abweichende alte Fassung wird nur nach j ersetzt und vorher nach `/root/briefkasten/sicherung/<zeit>/` gesichert. Zwei Profile: global (öffentlich) nur der PC-Schlüssel aus `hochladen/%u` mit fester Erlaubnisliste (open, close, write, lstat, fstat, stat, opendir, readdir, realpath, rename, statvfs, fstatvfs, fsync, limits, expand-path – also kein read, remove, mkdir, setstat, keine Links, kein posix-rename, kein copy-data); im Block `Match LocalAddress <Tailnet-IP>` nur der Mini-Schlüssel aus `abholen/%u`, `-R` (nur lesen). Rückweg `/root/briefkasten/zurueck.sh` schaltet nur den Dienst aus; ufw bekommt eine Regel nur nach j, die Firewall des Hosters öffnet Florian selbst |
| M86 | Der vServer ist eine KVM- oder VM-Maschine mit systemd, Loop-Geräten, öffentlicher IPv4 und tailscaled im TUN-Modus (Adresse an `tailscale0`, aus 100.64.0.0/10). OpenSSH ab 8 (8.0–8.5 gehen, weil das PC-Programm mit `rename -l` umbenennt, M135); ab 9.8 bremst sshd Fehlversuche (`PerSourcePenaltyExemptList 100.64.0.0/10`, sonst sperrte ein Fehlversuch des Mini alle Abholungen). Container, kein freies Loop-Gerät, kein `tailscale0`, belegter Port oder zu wenig Platz: Abbruch vor der ersten Änderung, mit dem Grund. `--mini-ip` muss eine Tailnet-Adresse sein und nicht die des vServers |
| M87 | Fach je Freund = ext4-Bild fester Größe `/srv/briefkasten/bilder/bk-<name>.img` (Standard 20 GB, mindestens 8 GB, 1 GB = 10^9 Byte, root 0600), eingehängt mit noexec,nosuid,nodev unter `/srv/briefkasten/fach/bk-<name>/fach` (chroot und Einhängepunkt root 0755; fstab mit `nofail`). Darin: Wurzel root 0755 mit Marke `.clip-briefkasten` (Inhalt `bk-<name>`), `videos/ replays/ sitzungen/ status/` gehören `bk-<name>:briefkasten` (0700), Dateien 0600 (`-u 0077`). Das System behält 15 % und mindestens 10 GB frei; passt die Größe nicht, nennt das Skript die größte, die geht. Nur wachsen (`--groesse`: aushängen, fallocate, e2fsck, resize2fs, einhängen; ist das Fach belegt, wird nichts geändert). „Fest reserviert“ heißt wirklich fest: `mkfs.ext4 -E nodiscard` und danach noch einmal `fallocate` (je nach Dateisystem des vServers stanzt mkfs beim Nullen sonst Löcher – im Test auf tmpfs nachgestellt), fstab mit `X-fstrim.notrim` (sonst gäbe das wöchentliche fstrim den Platz frei); `briefkasten-pruefen.sh` meldet ein Bild mit Löchern samt Abhilfe. Ein Dateisystem entsteht nur in einem Bild, das es eben noch nicht gab; eine fremde oder fehlende Marke im eingehängten Fach heißt Abbruch |
| M88 | Je Freund zwei Ed25519-Schlüssel in root-eigenen Dateien (0644): `hochladen/bk-<name>` = `restrict <PC-Schlüssel>`, `abholen/bk-<name>` = `from="<Mini-IP>",restrict <Mini-Schlüssel>`. Erzeugt werden sie auf dem Mini (Schritt 3), Florian trägt beide mit der angezeigten Zeile ein; das Skript zeigt beide Fingerabdrücke. Angenommen wird nur ein nackter `ssh-ed25519`-Schlüssel (keine Optionen, kein zweiter Eintrag, Name nur aus harmlosen Zeichen), PC und Mini verschieden und keiner, den schon ein anderer Freund oder die andere Rolle hat. Tauschen: dieselbe Zeile mit dem neuen Schlüssel (nach j, alte Zeile gesichert). Benutzer `bk-<name>` (Name wie im CT: 2–27 Zeichen a-z, 0-9, -): ohne Anmeldung (`/usr/sbin/nologin`), Passwort „*“ (bei `UsePAM no` sperrte „!“ das Konto ganz), Gruppe `briefkasten` (`AllowGroups`); `--sperren`/`--entsperren` = `usermod -L`/`-U`, Fach und Schlüssel bleiben |
| M106 | CLAUDE.md „keine Videos auf den vServer“ bekommt eine Ausnahme: der Briefkasten der Freunde – nur Aufnahmen von Freunden, nur auf dem Durchweg zum Mini, nie durch n8n. Prinzip 1 („Rechnen, wo die Daten liegen“) gilt für Freunde so nicht: Ihre Aufnahmen reisen übers Internet zum Mini, gerechnet wird dort |

### Stufe 2, Schritt 2 · Abholen am Mini (09.10.2026) – Annahmen bis Florian widerspricht
`pipeline briefkasten abholen|status` (`src/clip_pipeline/briefkasten.py`) holt die Aufnahmen eines Freundes aus seinem
Fach im Briefkasten in seinen Puffer – nur in seiner Instanz, nur lesend, ohne Rechen-Sperre. Eingeschaltet wird noch
nichts: Vorlage, Timer und Schlüssel kommen mit Schritt 3. Bei Florian ändert sich nichts (`[sitzungen].auto_abend`
steht ab Werk auf true, `[briefkasten].host` ist leer, seine Datenbank bekommt keine Tabelle). Tests:
`tests/test_briefkasten.py` (sftp-Attrappe `tests/sftp_attrappe.py`, echte Zeilen aus OpenSSH 9.6, dazu ein Lauf gegen
echten sshd 9.6p1 im privaten Namensraum – in der CI übersprungen) und `tests/test_instanz.py`.

| Nr. | Annahme (bis Florian widerspricht) |
|---|---|
| M89 | Fertig ist eine Datei im Fach erst mit ihrem Lieferschein `<name>.lieferschein` (JSON: `name`, `groesse`, `sha256`, `mtime_ms`, `utc_offset_min`; höchstens 4 KB, BOM erlaubt; Größe 1 B–50 GB, eine Sitzungsdatei höchstens 64 KB; Prüfsumme 64 Hex-Zeichen, groß oder klein). Der Mini holt per `reget` nach `I/daten/.abholen/<nr>.teil` (setzt nach einem Abriss fort), prüft nach fsync und verworfenem Seiten-Cache Größe und SHA-256, setzt die mtime auf min(PC-Zeit, jetzt − 130 s) und veröffentlicht per `os.link` – das überschreibt nie; Name und Inhalt bleiben, wie sie sind. Kein sftp-Lauf schreibt eine größere Datei als erwartet (Lieferschein 4 KB, Status 64 KB, Aufnahme = Größe laut Lieferschein; RLIMIT_FSIZE) – sonst füllte ein riesiger Lieferschein die Platte, bevor er geprüft ist. Ein ungültiger Lieferschein, eine falsche Prüfsumme oder eine zu kleine Datei zählen als Versuch, ein Abriss der Verbindung nicht; nach drei Versuchen ist die Datei „aufgegeben“ (Log, eine Zeile an den Freund) und hält nichts mehr auf. Liegt im Puffer schon eine Datei unter dem Namen: gleicher Inhalt → nur vermerkt; anderer → Konflikt, sie bleibt im Fach (Log, Exit 1) |
| M90 | Das Ziel im Puffer ergibt sich allein aus Ordner und Name: Nvidia-Highlights → `eingang/nvidia/highlights`, Videobeweis und manuelle Aufnahmen → `eingang/nvidia/aufnahmen`, SteelSeries → `eingang/steelseries` (wie `Uebertragung.ps1`), `UnsavedReplay-*.replay` → `replays/`, `session_<ID>.json` → `sitzungen/`. Nur `.mp4` (keine PNG), nur Fortnite, druckbares ASCII ohne `[ ] * ? " ' \`, kein Punkt vorn, höchstens 200 Zeichen, nie ein Unterordner oder ein Pfad vom PC. Zusätzlich: Ein Datum im Namen, das es nicht gibt (13. Monat, 30. Februar), ist fremd – an so einem Namen stürzte `scan` ab (nachgestellt). Fremdes bekommt keine Zeile und wird nie angefasst |
| M91 | Ein Lauf listet nur lesend: Marke `.clip-briefkasten` (fehlt sie: Fach nicht eingehängt, Exit 3), Füllstand (`df`), `status/pc-status.json` (ist es ein JSON-Objekt, kommt es nach `I/db/pc-status.json`, sonst bleibt der alte Stand), dann `sitzungen` → `replays` → `videos`. Veröffentlicht werden Videos (älteste zuerst, nach PC-Zeit) → Replays, nur wenn kein gelistetes Video offen blieb (Fehler, Bremse, Abriss) → Sitzungsdateien. Die Ausgabe von sftp wird je Befehl gelesen (die Zeile „sftp> <befehl>“ trennt; nur die als nächste erwartete zählt, ein Dateiname kann keine vortäuschen), es zählen nur Zeilen mit dem Ordner vorn; `ls -l` wird nie zerlegt. Geprüft an echten Zeilen aus OpenSSH 9.6. Der Teil auf dem PC (ein Replay erst, wenn sein Fenster oben ist) kommt mit Schritt 4 |
| M92 | Die Sitzungsdatei vom PC (`{session, matches, ende_utc}`) holt der Mini, prüft sie (1–500 gültige Match-IDs, lesbares `ende_utc` – sonst aufgegeben) und legt sie erst in `sitzungen/`, wenn alle ihre Matches verarbeitet oder aufgegeben (`fehler`) sind, spätestens 24 h nach dem ersten Sehen; bis dahin wartet sie geprüft in `.abholen`. Stehen alle ihre Matches schon in abgeholten Sitzungen (z. B. nach einer Neuinstallation des PCs), wird sie nur vermerkt. Dann baut `sitzungen` sofort „🎮 Abend vom …“ und das Video; `sitzung.py` bleibt bis auf den Schalter `auto_abend` unverändert |
| M93 | Mit `[briefkasten].host` ist `[sitzungen].auto_abend` aus (neuer Schalter, Standard true – bei Florian wie bisher): Die Aufnahmen eines Freundes kommen mit Verzug, ein selbst erkannter Abend käme zu früh. `ruhe_min` bleibt 45 und entprellt weiter den Nachtrag. In der `instanz.toml` ist `[briefkasten]` erlaubt (host, port, oeffentlich, drossel_kbit, loeschen, karenz_h) und wird geprüft: host nur als Tailnet-Adresse (100.64.0.0/10, kein Name), port 1–65535, drossel_kbit 0–10 000 000 (Standard 20 000 = 20 Mbit/s je Freund, 0 = ungedrosselt), `loeschen` muss false bleiben (Löschen im Briefkasten erst nach Florians Ja; sonst Exit 2), karenz_h ab 0. Fest und nicht einstellbar: Benutzer `bk-<name>`, Schlüssel `I/briefkasten/abholen`, `I/briefkasten/known_hosts` (auch im Pfadwächter). Kein Zugang in `.env`, nichts in der Umgebung. Ohne host sagt `briefkasten abholen` nur „aus“ (Exit 0) – sonst alles wie Stufe 1. Einspielen: erst das Update, dann der Abschnitt (sonst Exit 2 bei allen Diensten des Freundes) |
| M96 | Der Abholer nimmt nie die Rechen-Sperre (nur Ein- und Ausgabe – Florians n8n-Schritte hält er nie auf), hat eine eigene Sperre `I/db/pipeline.briefkasten.lock` (Wartezeit 0, belegt = Exit 4), weckt nie und löscht im Briefkasten nichts (das Abholprofil ist ohnehin nur lesend). Gegen den gleichzeitigen scan schützen Reihenfolge, Kappung, `os.link` und prepare, das `eingang/` direkt vor analyze neu liest. Bremse: geholt wird nur, wenn auf dem Freunde-Volume danach max(10 GB, 10 %) frei bleiben; sonst nichts und höchstens eine Zeile am Tag. Exit: 0 ok, nichts zu tun oder aus · 1 einzelne Dateien (nächster Lauf) · 2 Konfig · 3 nicht erreichbar, Fach nicht eingehängt, Puffer offline · 4 Sperre belegt. Stand des letzten Laufs in `I/db/briefkasten.json`; `briefkasten status` zeigt ihn ohne Netz. Die Tabelle `abholung` entsteht nur in der Datenbank eines Freundes; bei Florian endet `pipeline briefkasten` mit Exit 2, bevor eine Datenbank oder ein Netz berührt wird |
| M103 | Meldungen an den Freund über seinen Lern-Bot, je Thema höchstens einmal am Tag; wo nur Florian helfen kann, endet die Zeile mit „sag Florian Bescheid“. Schritt 2: Fach ab 80 % voll, Briefkasten seit 24 h nicht erreichbar oder nicht eingehängt, Platz knapp (Bremse), eine Datei aufgegeben (je Datei einmal). Mit Schritt 4 kommen dazu: PC verbunden (einmal), Zeitzone des PCs falsch, Aufnahmen ohne Replay. Florian selbst bekommt nur die Morgenprüfung („Freunde“, Schritt 6) |

### Stufe 2, Schritt 3 · Abholen einschalten (09.10.2026) – Annahmen bis Florian widerspricht
Je Freund schaltet `benutzer-anlegen.sh` (neuer Schritt 9 „Briefkasten“, j/N) das Abholen ein: Schlüssel,
Hostschlüssel, Eintrag in seiner `instanz.toml`, eine Zeile für den vServer, Probe-Abholung in seiner Sandbox, erst dann
der Timer. Neue Vorlage `clip-freund-abholen@` mit Timer; `benutzer-pruefen.sh` und `benutzer-stilllegen.sh` kennen
das Abholen. Bei Florian ändert sich nichts; das Update legt die Vorlage nur hin. Nachgeprüft mit echtem sshd 9.6:
Format von `ssh-keyscan` auf Port 2222, ein root-eigener Schlüssel mit 0640 geht als clip-<name>, nicht als root.
Tests: `tests/test_deploy_benutzer.py`. Anleitung: `docs/BRIEFKASTEN.md`, „Je Freund“ und „Abnahme von Hand“.

| Nr. | Annahme (bis Florian widerspricht) |
|---|---|
| M95 | Drossel, Teil am Mini: `clip-freund-abholen@` liest und schreibt die Platte nur im Leerlauf-Vorrang (`IOSchedulingClass=idle`); halber Vorrang beim Prozessor und Nice 10 kommen aus der Sandbox, die Leitung daheim schont `[briefkasten].drossel_kbit` (20 Mbit/s je Freund, Schritt 2). Der Teil auf dem PC (2 Mbit/s, solange Fortnite läuft) kommt mit Schritt 4 |
| M104 | Die Netzsperre M19 bleibt offen: Die Vorlage des Abholers hat keinen Netz-Zaun; er braucht nur die Tailnet-Adresse des vServers, Port 2222. Vor Ort prüfen, ob `IPAddressDeny=any` mit `IPAddressAllow=<Tailnet-IP des vServers>` im CT greift (`systemd-run --wait --pipe -p User=clip-<name> -p IPAddressDeny=any -p IPAddressAllow=<IP> …`), erst dann als Ergänzung. Der Teil für den Lager-Dienst (`PrivateNetwork=yes`) kommt mit Schritt 5 |
| M108 | Vorlage `clip-freund-abholen@` (oneshot, Sandbox-Block wörtlich, `pipeline briefkasten abholen`, Exit 3/4 kein Fehler, nach 2 h Schluss – der nächste Lauf setzt per reget fort) mit Timer alle 2 min (`OnBootSec=3min`, `OnUnitActiveSec=2min`). Das Update legt beide hin wie alle `clip-freund-*@`, eingeschaltet wird nur über `benutzer-anlegen.sh` nach einer grünen Probe-Abholung |
| M109 | Schritt 9 von 11 in `benutzer-anlegen.sh`, nach Telegram und vor dem Einschalten: j/N, Standard nein; bis der Briefkasten eingerichtet ist, fragt jeder Lauf einmal danach (ergänzt M63), danach nie wieder (Timer an = übersprungen). Scheitert etwas (Briefkasten antwortet nicht, Probe rot), bleibt nur das Abholen aus – das Skript läuft weiter, endet mit 0 und sagt es; der nächste Lauf setzt an derselben Stelle fort (Schlüssel, Hostschlüssel und Eintrag bleiben, nur Enter und die Probe) |
| M110 | Die Adressen des vServers fragt das Skript einmal für alle Freunde und merkt sie in `/etc/clip-briefkasten.conf` (root, 0644): `TAILNET_IP` (100.64.0.0/10), `OEFFENTLICH` (Name oder Adresse aus A-Z, 0-9, Punkt, Strich, Doppelpunkt), `PORT` (Standard 2222). Gemerkt erst, wenn der Briefkasten über das Tailnet antwortet – ein Tippfehler bleibt so nicht hängen. Gelesen per awk, nie ausgeführt |
| M111 | Schlüssel in `I/briefkasten` (Ordner `root:clip-<name>` 0750, alle Dateien 0640): `abholen` (der Mini, nur lesen) und `pc` (sein PC, nur hochladen; kommt in Schritt 4 ins PC-Paket), je mit `.pub`, Ed25519 ohne Passwort, Name `clip-<name>-<rolle>`. Er liest sie, tauschen kann er sie nicht. Ein vorhandener Schlüssel bleibt immer; fehlt nach einem Abbruch nur der öffentliche Teil, wird er aus dem privaten abgeleitet (dafür kurz 0600 – als root liest ssh-keygen einen root-eigenen Schlüssel nur so; nachgestellt). Der private Teil erscheint nie auf dem Bildschirm, im Log oder auf einer Befehlszeile – das Skript liest ihn nicht. Als root lässt sich ein solcher Schlüssel nicht benutzen (ssh lehnt 0640 beim Besitzer ab); für die Abnahme von Hand eine Kopie mit 0600 |
| M112 | Hostschlüssel per `ssh-keyscan -t ed25519` über das Tailnet (WireGuard: dort antwortet nur der echte vServer), angezeigt als Fingerabdruck zum Vergleich mit dem Ende von `briefkasten-einrichten.sh`. `known_hosts` = `[<Tailnet-IP>]:<Port> ssh-ed25519 …` (bei Port 22 ohne Klammern), `known_hosts_pc` = derselbe Schlüssel unter dem öffentlichen Namen – nie übers Internet gefragt. Einmal gepinnt, nie still ersetzt: Passt ein vorhandenes `known_hosts` nicht zur Adresse, ändert das Skript nichts |
| M113 | `[briefkasten]` (host, port, oeffentlich) wird an seine `instanz.toml` nur angehängt – vorher mit tomllib geprüft, nie umgeschrieben; weicht ein vorhandener Abschnitt von `/etc/clip-briefkasten.conf` ab, ändert das Skript nichts. Danach startet sein Bot neu (er liest die Konfig nur beim Start). Nach einer roten Probe bleibt der Abschnitt stehen: Sein Abend kommt dann nur noch als Datei vom PC (M93) – vorher kam bei Freunden ohnehin nichts von selbst |
| M114 | Probe-Abholung = ein echter Lauf `pipeline briefkasten abholen` in seiner Sandbox über `benutzer-befehl.sh` (holt, was schon wartet). Grün = Exit 0 oder 1 mit Ergebnis (nicht „aus“, nicht unerreichbar); sonst bleibt der Timer aus, und das Skript nennt den Grund aus dem Ergebnis und die häufigen Ursachen (Zeile auf dem vServer nicht gelaufen, Tailnet-Regel, andere Mini-IP, Fach nicht eingehängt) |
| M115 | `benutzer-pruefen.sh`, Abschnitt 5 von 6: Rechte in `I/briefkasten` (Ordner 0750, Dateien 0640, root:clip-<name>) – nicht in der Vorab-Prüfung, sonst hielte ein Lauf, der mitten im Schritt „Briefkasten“ abbrach (Schlüssel noch root:root 0600), Schritt 9 für immer auf (nachgestellt). Mit `[briefkasten]` dazu: alle sechs Dateien da, `known_hosts` passt zu host und port, kein Schlüssel doppelt (abholen ≠ pc, bei keinem anderen Freund – gelesen werden nur die öffentlichen, ohne Link, höchstens 4 KB), Abholen an, und aus seiner Sandbox (`briefkasten status`, ohne Netz; root liest keine Datei des Freundes) zuletzt erreicht, Füllstand und ob sich sein PC gemeldet hat. Ein Fehler beim letzten Abholen ist ein Befund, solange das Abholen an ist; sonst nur ein Hinweis |
| M116 | `benutzer-stilllegen.sh` schaltet auch den Abhol-Timer aus und hält ein laufendes Abholen an (ein halber Download setzt beim nächsten Mal fort). Sein Fach auf dem vServer bleibt offen; das Skript nennt `briefkasten-freund.sh <name> --sperren` |

### Stufe 2, Schritt 4 · PC-Programm für Freunde und /pc (09.10.2026) – Annahmen bis Florian widerspricht
Der Freund tippt in seinem Bot `/pc`, entpackt die Datei und startet `Freund-Einrichten.cmd` – ohne Admin, ohne
Installation. Danach lädt `windows/Freund-Hochladen.ps1` (Windows PowerShell 5.1, Aufgabe „Clip-Upload“ alle 2 min)
seine Aufnahmen und Replays in sein Fach; der Abholer (Schritt 2) nimmt sie dort. Bei Florian ändert sich nichts:
`Uebertragung.ps1` bleibt unverändert, /pc gibt es nur im Bot eines Freundes mit Briefkasten. Tests:
`tests/test_windows_freund.py` (pwsh gegen die sftp-Attrappe, jetzt auch mit dem Upload-Profil; Ende zu Ende PC → Fach
→ Abholer → scan), `tests/test_lernbot_pc.py`, `tests/test_briefkasten.py` (Meldungen). Die Meldungen der Attrappe
stammen aus einem Versuch mit echtem sshd und echtem sftp 9.6. Echte Windows PowerShell 5.1 und Win32-sftp.exe laufen
hier nicht – einmal vor Ort mit `-Probe` (docs/BRIEFKASTEN.md).

| Nr. | Annahme (bis Florian widerspricht) |
|---|---|
| M94 | Abend vorbei (PC): 45 min seit dem letzten Match-Ende und alles bis dahin oben (alle Replays des Abends, alle Aufnahmen bis 5 min nach seinem letzten Match) – dann als letzte Datei `sitzungen/session_<1. Match>.json` mit `{session, matches, ende_utc = Ende des letzten Matches}`. Match-Ende = Dateizeit des Replays (wie `erfassung.py`). Mehr als 2 h Pause trennt zwei Abende; Matches, die schon in einer geschickten Abend-Datei stehen, kommen nie in eine zweite – neue Matches danach beginnen einen neuen Abend. Ist das letzte Match über 6 h her, wartet die Abend-Datei auf keine offene Aufnahme mehr (nimmt aber nur hochgeladene Replays). Den Rest regelt der Abholer (M92) |
| M97 | Der PC löscht nie – keine Aufnahme, kein Replay, auch nichts im Briefkasten (dort kann er es auch nicht). Kein Auto-Update: Eine neue Version heißt `/pc` erneut und `Freund-Einrichten.cmd` erneut; der Zustand in `%LOCALAPPDATA%\ClipUpload\stand` bleibt dabei. Das ZIP baut der Bot bei jedem `/pc` frisch aus dem laufenden Stand |
| M117 | Drossel, Teil am PC (ergänzt M95): Solange `FortniteClient-Win64-Shipping` läuft, lädt sftp mit `-l DrosselBeimSpielenKbit` (freund.psd1, Standard 2000 = 2 Mbit/s; 0 = beim Spielen Pause), sonst ungedrosselt. Startet oder endet Fortnite mitten in einem Upload, endet sftp; der nächste Lauf setzt per `reput` mit der neuen Geschwindigkeit fort. Das Programm läuft mit niedrigem Vorrang (BelowNormal, sftp erbt ihn) – auch die Prüfsumme stört das Spiel nicht. Den Ruhezustand verhindert es nur, solange ein Upload läuft (SetThreadExecutionState); Herunterfahren geht immer |
| M118 | Meldungen aus dem Status vom PC (ergänzt M103), über seinen Lern-Bot: „✅ Dein PC ist verbunden“ einmal beim ersten Status; Zeitzone des PCs ≠ `[zeit].zeitzone` (verglichen wird der Versatz zur Zeit des Status – sonst verrutschen die Clips) und Aufnahmen ohne Replay („Replays an?“) je einmal am Tag, beides nur aus einem höchstens 24 h alten Status. Der Status ist fremde Eingabe: Gelesen werden nur Zahlen und Zeiten, kein Text daraus geht in den Chat |
| M119 | PC-Programm = `windows/Freund-Hochladen.ps1`, `Freund-Einrichten.ps1`, `Freund-Einrichten.cmd` (nur ASCII, CRLF) und die Vorlage `freund.beispiel.psd1`; Windows PowerShell 5.1, kein Admin. Einrichten: alles nach `%LOCALAPPDATA%\ClipUpload` kopieren und entsperren (Unblock-File), Rechte an Schlüssel und known_hosts nur für ihn (icacls mit seiner SID – sonst lehnt Win32-OpenSSH den Schlüssel ab), sftp.exe prüfen (fehlt er: der Weg über Optionale Features, einmal mit Admin), Fortnite-Aufnahmen im Videos-Ordner suchen (sonst einmal ein Auswahlfenster; gemerkt in `stand\ordner.txt`), Stichtag jetzt − 24 h (bleibt bei einer neuen Version), Verbindung mit `-Probe` prüfen, Aufgabe „Clip-Upload“ alle 2 min und bei der Anmeldung (`conhost --headless` ab Windows 10 1809, sonst `-WindowStyle Hidden`; lässt Windows „bei der Anmeldung“ ohne Admin nicht zu, nur alle 2 min), IgnoreNew, 6 h. `/entfernen` löscht nur die Aufgabe. Zustand in `stand\` (erledigt.tsv, sha.tsv, teil.tsv, matches.tsv, sitzungen.tsv, stichtag.txt, ordner.txt, status.txt), Log `hochladen.log` (5 MB, dann .alt). Exit 0 ok · 1 einzelne Dateien · 2 Abbruch · 3 nicht erreichbar, nicht eingehängt oder voll · 4 nur `-Probe`: läuft gerade |
| M120 | Hochladen je Datei in zwei kurzen sftp-Läufen; Arbeitsordner = Ordner der Datei bzw. `stand\versand`, in den Befehlen steht nur der (ASCII-)Name – ein Umlaut im Benutzerordner stört so nicht. Erst `put` bzw. `reput` nach `<ordner>/<name>.teil`, `rename -l` auf `<name>` (M135), dann der Lieferschein (M89, lokal immer dieselbe kleine Datei) ebenso über `.teil` und `rename -l`. `reput` nur, wenn der `.teil` laut `teil.tsv` zu genau dieser Datei (Pfad, Größe, Zeit) gehört, sonst `put` von vorn; „destination file same size or larger“ heißt: der `.teil` ist schon ganz oben, nur noch umbenennen. Steht `<name>` mit Lieferschein schon im Fach (z. B. nach einer Neuinstallation), gilt die Datei als erledigt; änderte der Rekorder sie, nachdem sie oben war, bleibt die oben liegende Fassung (nie überschrieben). sftp mit `-F none`, BatchMode, nur dieser Schlüssel, Hostschlüssel gepinnt (known_hosts aus dem Paket, auch als GlobalKnownHostsFile), CheckHostIP=no, nur IPv4 (der Briefkasten lauscht nur dort) |
| M121 | Auswahl: nur `.mp4` mit Nvidia- oder SteelSeries-Namen von Fortnite und `UnsavedReplay-*.replay` nach den Mustern der Pipeline (Namen wie M90; ein Datum, das es nicht gibt, gilt als falscher Name), nur ab dem Stichtag. Fertig = 60 s unverändert (Replay 5 min), nicht leer, nicht zum Schreiben offen (Datei-Frei aus `Uebertragung.ps1`). Reihenfolge nach Dateizeit, ein Replay zählt 5 min später; es wartet, solange eine Aufnahme bis 5 min nach seinem Ende noch nicht oben ist (höchstens 6 h). Übersprungen (bleibt auf dem PC, steht im Status): Name passt nicht, Aufnahme ohne Replay im Abstand von 24 h, größer als das halbe Fach. Fach voll (laut `df`, 1 MB Reserve, oder „write remote … Failure“) oder nicht erreichbar bzw. nicht eingehängt (Marke fehlt): Pause, alles bleibt auf dem PC, Exit 3 |
| M122 | Status `status/pc-status.json` – die einzige Datei, die direkt auf ihren Namen geschrieben wird: zeit_utc, rechner, version, zeitzone, utc_offset_min, fortnite, hochgeladen, fehler, letzter_fehler, fach_voll, offen (je Quelle Anzahl und älteste), uebersprungen (je Grund Anzahl und ein Beispiel). Bei Änderung, sonst höchstens alle 10 min; ohne Arbeit und ohne fälligen Status baut ein Lauf keine Verbindung auf. Der Mini legt ihn nach `I/db/pc-status.json` (M91) |
| M123 | `/pc` nur im Bot eines Freundes mit `[briefkasten].host` (wie `/claude` nur bei Freunden). Fehlt `oeffentlich` oder der Schlüssel, ist er kein privater OpenSSH-Schlüssel oder passt `known_hosts_pc` nicht zu `[oeffentlich]:port`: kein Paket, nur „sag Florian Bescheid“. ZIP flach: die drei Skripte, `freund.psd1` (öffentliche Adresse, Port, `bk-<name>`, Drossel – mit BOM), `pc`, `known_hosts` – nie der Abhol-Schlüssel, nichts von Florian oder anderen Freunden; der Schlüssel steht nie im Log. 📋 Stand bekommt „💻 PC: zuletzt vor … · n unterwegs“ (offen auf dem PC plus offen im Briefkasten) bzw. „noch nicht verbunden – tipp /pc“, die Hilfe eine Zeile `/pc`. Bei Florian nichts davon (Hilfe Zeichen für Zeichen gleich) |
| M124 | Testhaken und Fallen: Uhr und „Fortnite läuft“ kommen in den Tests über überschriebene `Get-Date`/`Get-Process` (das Skript ruft `Get-Date` nur ohne Parameter), sftp.exe über `Sftp` in der psd1; `tests/sftp_attrappe.py` spielt jetzt auch das Upload-Profil (put, reput, rename; nicht lesen, nicht löschen, kein Ziel überschreiben, nur in die vier Unterordner) mit den Texten des echten sftp. Zwei neue Stolperdrähte: „ “ ” als Stringgrenze (PowerShell 5.1 wie 7 nimmt sie als Anführungszeichen – in `"… „$x“ …"` fehlten sonst Zeichen) und `.cmd` nur ASCII. `@()` um eine generische Liste aus New-Object scheitert unter PowerShell 7 („Argument types do not match“) – dort `.ToArray()` |

### Stufe 2, Schritt 5 · Lager für Freunde (09.10.2026) – Annahmen bis Florian widerspricht
Freunde mit Lager fahren bei Florians täglichem Abgleich mit: Hat sein Abgleich pve-big ohnehin geweckt, sichert
`clip-freund-lager@<name>` den Puffer des Freundes in seinen Unterordner `freunde/<name>` im Lager – derselbe Code wie
bei Florian, in der Sandbox des Freundes, eingebunden ist nur dieser Unterordner. Danach gibt sein Puffer alte
Rohvideos frei wie bei Florian (B5). Eingeschaltet wird je Freund mit `benutzer-anlegen.sh`, Schritt „Lager“ (nur bei
wachem pve-big). Bei Florian ändert sich nichts: `lager.py`, `big.py` und `clip-lager.service` sind unverändert
(Prüfsummen im Test); ohne eingeschalteten Rundgang läuft alles wie bisher. Tests: `tests/test_lager_freund.py`
(Abgleich und Freigabe in einer Instanz mit Schein-NFS, falscher Ordner, Rundgang in einer Scheinwurzel),
`tests/test_deploy_benutzer.py` (Vorlagen, Kernel-Nachbau der Bindung, Update, Anlegen, Prüfen, Stilllegen). Vor Ort
offen: ob die Bindung mit NFS-Quelle und `PrivateNetwork` im unprivilegierten CT geht – das zeigt die Probe der
Bindung im Schritt „Lager“ (rot: der Schalter geht wieder aus).

| Nr. | Annahme (bis Florian widerspricht) |
|---|---|
| M98 | Lager-Schalter = `[instanz] lager = true` in der root-eigenen `instanz.toml` plus der Einhängepunkt `I/lager` (root:root 0755, leer). Dann erzwingt die Pipeline Lager `I/lager`, Marke `.clip-lager-<name>`, `warten_s` 0 und `[puffer].freigeben` true; Host, MAC und SSH bleiben leer – ein Freund weckt nie. Pfadwächter: `I/lager` ist ein echter Ordner (kein Link) und gehört außerhalb des Lager-Dienstes root (im Lager-Dienst liegt dort das NFS: anderes Gerät, die Prüfung übernimmt M101). Fehlt er bei gesetztem Schalter, endet jeder Dienst des Freundes mit Exit 2 – deshalb setzt beides nur der Schritt „Lager“. `freunde/<name>` mit Marke legen der Rundgang und (für die Probe der Bindung) der Schritt „Lager“ an – nur bei bestätigtem NFS, nur auf demselben Gerät wie `/srv/big/clips` (kein eigenes Dataset), nie über einen Link. Der Plan sah dafür nur den Rundgang vor |
| M99 | Mitfahren: `clip-lager-freunde.service` (root, oneshot, `WantedBy=clip-lager.service`, ohne `After=`, 10 h, schreibt nur in `/var/lib/clip-pipeline` und `/srv/big`) startet `deploy/benutzer/lager-freunde.sh`. Ohne Freund mit Lager tut er nichts. Sonst: Halten-Marke „freunde“ 30 min (als pipeline, `pipeline big halten`), verlängert alle 5 min, solange `clip-lager` „activating“ ist oder ein Start-Auftrag dafür wartet (nach einem Neustart wartet Florians Abgleich erst aufs Netz); dann prüfen ohne Wecken (`findmnt` nfs/nfs4 genau auf `/srv/big/clips`, TCP 2049 der NFS-Quelle, `stat` von `.clip-lager` mit Zeitgrenze) – sonst Marke lösen, Ende. Je Freund mit Schalter, Einhängepunkt und laufendem scan-Timer nacheinander, neue Starts nur 10–18 Uhr (Europe/Berlin): erneut prüfen, Marke 130 min, Herzschlag jede Minute (`.aktiv/<rechner>-freund-<name>-<pid>-<start>`; clip-leerlauf zählt ihn höchstens 4 h), Ordner und Marke, `systemctl start clip-freund-lager@<name>` (wartet). Am Ende Marke lösen und den eigenen Herzschlag entfernen. Hat Florian nichts Neues, warten die Freunde bis zu 7 Tage (`sicherung_wecken_tage`) |
| M100 | Die Freigabe im Puffer des Freundes ist B5 unverändert: nach einem fehlerfreien Lauf Rohvideos aus `eingang/`, älter als 14 Tage, mit bestätigter und eben zurückgelesener Lager-Kopie; der erste Lauf mit etwas zum Freigeben ist nur eine Probe (N39/N40). Wie bei Florian behält sein Puffer höchstens 30 DB-Sicherungen in `sicherung/` (ältere liegen im Lager). Im Lager wird nie gelöscht |
| M101 | Die Lager-Prüfungen der Instanz laufen nur auf dem Lager-Weg (`pruefe_getrennt` mit Lager, nur bei Freunden mit Schalter): `I/lager` ist laut `/proc/self/mountinfo` genau dort nfs/nfs4 eingehängt (eine Bindung zeigt den Typ ihrer Quelle), Florians `.clip-lager` und `freunde/` sind dort unsichtbar, die eigene Marke ist da – sonst KonfigFehler (Exit 2), nichts kopiert, nichts gelöscht. Fehlt die eigene Marke (z. B. der Ordner eines anderen Freundes gebunden), meldet schon die Prüfung vor dem Kopieren „nicht eingehängt“ (Exit 3) – auch dann wird nichts kopiert. Bot, scan und abend sehen nur das leere `I/lager` |
| M107 | Bekannte kleine Lücke bleibt: `big.setze_marke` schreibt nicht atomar. Liest Florians Wächter genau in diesem Moment, verwirft er die Marke; dann bricht höchstens ein Lauf eines Freundes ab, weil pve-big ausgeht – nichts geht verloren, er läuft beim nächsten Mal. Der Fix (tmp + replace) kommt erst bei Bedarf, damit `big.py` unverändert bleibt |
| M125 | Vorlage `clip-freund-lager@`: Sandbox-Block wörtlich, dazu genau `BindPaths=/srv/big/clips/freunde/%i:/var/lib/clip-benutzer/%i/lager` (ohne „-“: fehlt der Ordner, startet nichts) und `PrivateNetwork=yes` (das NFS liest der Kernel, der Abgleich braucht kein Netz – damit ist der Lager-Teil von M104 erledigt); oneshot, `pipeline lager abgleich`, Exit 3/4 kein Fehler, 2 h, Platte im Leerlauf-Vorrang, kein Timer. Das Update legt sie hin wie alle `clip-freund-*@`. Tests: genau diese eine Ausnahme vom Schreibverbot außerhalb des eigenen Ordners; Kernel-Nachbau als root: in `I/lager` nur der eigene Unterordner, `..` führt nach I, `/srv` bleibt leer, ohne die Bindung ist `I/lager` leer |
| M126 | Schritt 11 von 12 in `benutzer-anlegen.sh` (nach dem Einschalten, vor der Prüfung): Gefragt wird nur bei wachem pve-big (`lager-freunde.sh --nfs`, ohne Wecken) – sonst ein Satz, keine Frage, frühere Eingaben bleiben gleich. j: `lager-freunde.sh --ordner <name>`, Einhängepunkt, die alte `instanz.toml` nach `/root/benutzer-lager/`, `lager = true` unter `[instanz]` (nur, wenn das Ergebnis bis auf diesen Wert genau die alte Konfig ist; Besitzer und Rechte bleiben; steht dort schon ein anderer Wert, ändert das Skript nichts), dann die Probe der Bindung `benutzer-befehl.sh --lager <name> lager pruefen` (Sandbox der Lager-Vorlage samt Bindung und ohne Netz). Grün: Rückweg-Skript, Rundgang hinlegen und `systemctl enable` (nicht `--now`), auf Wunsch „Jetzt einmal sichern?“ (`start --no-block`). Rot: alte `instanz.toml` zurück, der Rundgang bleibt aus, das Skript endet mit 0. Ein weiterer Lauf sagt „schon eingerichtet“ und fragt nichts; ist der Rundgang inzwischen aus, kommt nur ein Hinweis |
| M127 | `pipeline lager pruefen` (neu, nur nachsehen, weckt nie): dieselben Prüfungen wie der Abgleich vor dem ersten Kopieren. Exit 0 ok, 2 verwechselt, falscher Ordner oder kein NFS, 3 schläft bzw. nicht eingehängt. Bei Florian ebenso nutzbar |
| M128 | Zusammenfassung `/var/lib/clip-pipeline/lager-freunde.json` (gehört pipeline, für die Morgenprüfung in Schritt 6): `lauf` mit start, ende, ergebnis und je Freund start, ende, rc, exit, ok, zuletzt_ok, hinweis; Freunde ohne Lauf behalten ihren Eintrag, ok heißt `systemctl start` 0 und Exit 0. Ohne Freund mit Lager schreibt der Rundgang nichts und setzt keine Marke. `lager-freunde.sh --probe` zeigt, wer mitfahren würde, und ändert nichts. Ergänzt in Schritt 6: `mit_lager` (M133), nie durch einen Link (M134) |
| M129 | `benutzer-pruefen.sh`, Abschnitt 6 von 7 (nicht in `--vorab`): mit Schalter der Einhängepunkt (root:root 0755 und leer, sonst Befund), Rundgang an (sonst Hinweis), stillgelegt (Hinweis) und sein letzter Lauf aus `lager-freunde.json` (gut; Exit 3/4 nur Hinweis; sonst Befund mit dem letzten guten Lauf). `benutzer-stilllegen.sh` hält auch einen laufenden Lager-Lauf an (es bleiben nur unbestätigte `.teil`-Reste); ohne laufenden scan-Timer nimmt ihn der Rundgang nicht mehr mit; sein Ordner im Lager bleibt |
| M130 | Florians Lager-Code bleibt Zeichen für Zeichen (`lager.py`, `big.py`, `clip-lager.service` und `.timer`; Prüfsummen von main d72349f im Test – ändert ihn später jemand mit Absicht, dort die neue eintragen). Der Rundgang ruft nie Florians Abgleich, weckt nie (kein Wake-on-LAN, kein `big aus`) und löscht im Lager nichts außer seinem eigenen Herzschlag. Das Update legt `clip-lager-freunde.service` nur hin, wenn es Freunde gibt, und schaltet ihn nie ein |

### Stufe 2, Schritt 6 · Florians Morgenprüfung kennt die Freunde (09.10.2026) – Annahmen bis Florian widerspricht
Florian sieht Probleme der Freunde in seiner Morgenprüfung (11 Uhr, `puffer.py`): ein neues Thema „freunde“ mit dem
Platz auf dem Freunde-Volume und dem Lager der Freunde aus der Zusammenfassung des Rundgangs. Ohne Freunde-Volume gibt
es das Thema nicht – Stand und Meldungen sind Zeichen für Zeichen wie vorher (Test gegen `puffer.py` aus main d72349f,
wo git sie liefert). Florians Units, `lager.py` und `big.py` bleiben unverändert. Tests: `tests/test_puffer.py`
(Klasse Freunde: genug Platz, knapp, Rundgang, ohne Volume, wie main; ein Wächter zählt jeden Blick ins Volume),
`tests/test_lager_freund.py` (Rundgang schreibt `mit_lager`, leert es ohne Freunde, folgt keinem Link).

| Nr. | Annahme (bis Florian widerspricht) |
|---|---|
| M105 | Die Mengen der Freunde sind unbekannt. Gerechnet ist mit Florians Messung: 2,6 GB Rohdaten je Abend, 3 Abende je Woche, 0,5 GB abgeleitet je Abend (geschätzt). Daraus: Fach im Briefkasten 20 GB (mindestens 8), Freunde-Volume 100 GB für alle, Warnung der Morgenprüfung unter 15 GB frei. Nach zwei Wochen mit dem ersten Freund wird gemessen und nachgestellt |
| M131 | Thema „freunde“ nur bei Florian (nie in der Instanz eines Freundes) und nur, wenn `[puffer].freunde_volume` (`/var/lib/clip-benutzer`) ein eigenes Dateisystem ist – Einhängepunkt nach `os.path.ismount` (nur `stat` auf den Ordner und „..“). Sonst kommt das Thema weder im Stand noch in den Meldungen vor. Gemessen wird nur der freie Platz (`statvfs`), nie ein Ordner aufgelistet oder eine Datei geöffnet. Warnung unter 15 GB, Alarm unter 5 GB (`freunde_warnung_frei_gb`, `freunde_alarm_frei_gb`; die Bremse des Abholers lässt max(10 GB, 10 %) frei); nächster Schritt `lvs pve/data`, `pct resize 102 mp2 +50G` (nur wachsen, CT und `mp2` wie in `freunde-volume.sh`), wer wie viel belegt: `du -sh /var/lib/clip-benutzer/*`. Wie jedes Thema höchstens eine Meldung am Tag, Platz und Lager zusammen in einer |
| M132 | Lager der Freunde aus `/var/lib/clip-pipeline/lager-freunde.json` (`[puffer].freunde_lager_status`) – `pipeline` darf die Konfig der Freunde nicht lesen, die Zusammenfassung ist die einzige Quelle. Je Freund mit Lager, der nicht stillgelegt ist, höchstens eine Zeile: Sein letzter Lauf ging nicht (nicht Exit 3/4 – pve-big ging aus oder sein Abgleich lief schon, das holt das nächste Mal nach) und ist höchstens 24 h alt – so kommt ein Fehler einmal, nicht zweimal (die Läufe enden meist zwischen 10 und 11 Uhr); oder seit 8 Tagen kein guter Lauf (`freunde_lager_tage`; Bezug ist das jüngere von letztem guten Lauf und „seit“; pve-big wird spätestens alle 7 Tage geweckt) – das kommt jeden Tag, bis es wieder klappt. Dazu eine Zeile zum letzten Rundgang und als nächster Schritt `benutzer-pruefen.sh <name>`, `journalctl -u clip-freund-lager@<name>`. Die Datei gilt als fremde Eingabe: nur bekannte Felder, Namen nach dem Muster der Instanzen, Texte nur druckbar und gekürzt. Fehlt sie oder ist sie unlesbar: keine Zeile, nur ein Hinweis im Stand |
| M133 | Der Rundgang schreibt zusätzlich `mit_lager`: je Freund mit Schalter und Einhängepunkt `seit` (seit wann mit Lager und nicht stillgelegt – neu, Schalter wieder an oder nach dem Stilllegen wieder aktiv: dieser Rundgang) und `stillgelegt` (scan-Timer aus) – bei jedem Rundgang, auch wenn pve-big schläft. Hat kein Freund mehr Lager, wird in einer vorhandenen Zusammenfassung nur `mit_lager` geleert (letzter Rundgang und Läufe bleiben; keine Marke, kein Start), sonst warnte die Morgenprüfung weiter. Ergänzt M128 |
| M134 | Die Zusammenfassung liegt in Florians Ordner (gehört pipeline), geschrieben wird sie von root: gelesen und geschrieben wird nie durch einen Link (`O_NOFOLLOW`), `chown -h` und `mv -T` – sonst könnte ein Link dort root eine fremde Datei überschreiben lassen. Scheitert das Schreiben, sagt es das Log; der Rundgang selbst läuft weiter |

### Stufe 2 · Prüfung (09.10.2026) – Annahmen bis Florian widerspricht
Ein blockierender Befund (B1), behoben im PC-Programm statt mit einer höheren Mindestversion auf dem vServer.
Nachgestellt mit echtem OpenSSH 8.2p1 (Ubuntu 20.04) und 9.6p1 im privaten Namensraum. Tests:
`tests/test_windows_freund.py` (Briefkasten vor 8.6 in der sftp-Attrappe, `SFTP_ATTRAPPE_ALT=1`, mit Gegenprobe) und
die Treiber mit echtem sshd in `tests/test_deploy_briefkasten.py` und `tests/test_briefkasten.py` (grün mit 9.6p1 und
8.2p1 über `BRIEFKASTEN_SSHD`).

| Nr. | Annahme (bis Florian widerspricht) |
|---|---|
| M135 | Das PC-Programm benennt immer mit `rename -l` um – die alte Art (SSH2_FXP_RENAME), die ein vorhandenes Ziel nie überschreibt („Failure“). Ohne `-l` nimmt sftp posix-rename, sobald der Server es anbietet, und OpenSSH vor 8.6 bietet es trotz Erlaubnisliste an und verweigert es dann („Permission denied“): Auf Ubuntu 20.04 (8.2p1) oder Debian 11 (8.4p1) wäre keine Datei eines Freundes je fertig geworden, obwohl Einrichten, Prüfung, Probe-Abholung und `-Probe` grün sind. Ab 8.6 schickt sftp mit und ohne `-l` dasselbe (mit `sftp -vvv` nachgesehen). `-l` kennt sftp seit 6.x, auch Win32-OpenSSH 7.7, 8.1 und 9.5 (Quelltext). Darum bleibt OpenSSH 8 die Mindestversion (M86) statt 8.6 – mit echtem 8.2p1 sind alle Fälle grün. Abnahme von Hand ebenso mit `rename -l` (`docs/BRIEFKASTEN.md`) |
| M136 | Liegt der Name im Fach, aber ohne Lieferschein und nicht genau die Fassung, die der PC zuletzt vollständig hochgeladen hat (Befund B2: die Aufnahme änderte sich beim Hochladen; ebenso ein abgebrochenes direktes Hochladen oder ein neuer Stand nach Neuinstallation), schreibt das PC-Programm die aktuelle Fassung direkt auf den Namen (`put` mit Kürzen ist im Profil erlaubt, Umbenennen kann kein Ziel ersetzen) und schickt danach den Lieferschein. Der Mini fasst ohne Lieferschein nichts an, und der Lieferschein prüft per SHA-256 genau diese Fassung. „~“ vor dem Schlüssel in `teile.tsv` heißt: direktes Hochladen läuft – bis es fertig ist, gilt die Datei oben als unvollständig. Vorher wurde sie still als erledigt gemerkt und kam nie an. |
| M137 | Ebenso die Abend-Datei (Befund B4): Liegt `sitzungen/session_<ID>.json` oben ohne Lieferschein (Abbruch zwischen Datei und Lieferschein), wird sie neu darüber geschrieben, bevor der Lieferschein kommt – sonst passte er nach weiteren Matches nicht mehr, der Mini gäbe die Sitzung auf, und für den Abend käme nie ein Video. |
| M138 | Tief verschachteltes JSON vom PC (Befund B3, z. B. 3000-mal „[“ in einem Lieferschein unter 4 KB) gilt wie jedes kaputte JSON als ungültig (`briefkasten._json`): Es zählt als Versuch und wird nach 3 Läufen aufgegeben. Vorher brach jeder Abhol-Lauf dieses Freundes mit RecursionError ab, bevor etwas geholt wurde. |

### Stufe 3, Schritt 1 · Laufzeiten messen (09.10.2026) – Annahmen bis Florian widerspricht
Stufe 3 („Hybrider Render-Manager“) nach dem Plan aus zwei Entwürfen: Der Mini bleibt der einzige Rechner; zuerst wird
gemessen. Jede der sieben Sperr-Stellen (cli.main, Lern-Bot: Bau, Paket, Kalibrieren, KI-Note; Clip-Bot /paket;
`benutzer einrichten`) nimmt die Rechen-Sperre jetzt über `laufzeiten.lauf` – `sperre.py` bleibt unverändert – und
schreibt danach eine Zeile; `pipeline laufzeiten [--tage 7]` wertet sie aus. Kein neuer Dienst, keine Tabelle, kein
Vertragsbefehl für n8n (`deploy/n8n-lauf.sh` unverändert). Tests: `tests/test_laufzeiten.py` (Zeile je Lauf, Exit 4 mit
Zeile „gesperrt“, Auswertung mit Sidecar und Dateizeiten; Schreibfehler → derselbe Exit und dieselbe JSON-Zeile; offene
Transaktion eines Bots bleibt offen), Sidecar-Felder in `tests/test_entwurf.py`, die Sperr-Prüfungen in
`test_vertrag_stufe2` und `test_isolation` fangen jetzt `clip_pipeline.sperre.sperre` ab.

| Nr. | Annahme (bis Florian widerspricht) |
|---|---|
| M139 | Laufzeiten stehen als Zeilen in `ereignisse` der eigenen Datenbank (art `lauf`, match_id = Session, falls es eine gibt), text = JSON {was, ziel, gewartet_s, gehalten_s, ergebnis ok·gesperrt·fehler, fehler}; der Lern-Bot-Bau dazu format, stimmung_s, schnitt_s und render_s (Rendern + Kritik). Keine neue Tabelle, keine Migration. „was“ ist der CLI-Befehl (render, decide, sitzungen, scan …) oder lernbot-bau, lernbot-paket, lernbot-kalibrieren, ki-note, clipbot-short, benutzer-einrichten. „gesperrt“ heißt nur: die Rechen-Sperre nicht bekommen; eine Ausnahme im Auftrag ist „fehler“ mit ihrer Klasse, ein Exit ≠ 0 ohne Ausnahme „fehler“ mit „Exit n“ |
| M140 | Geschrieben wird nur, wer mindestens 1 s gewartet oder gehalten hat oder nicht ok endete. „gesperrt“ schreiben nur Aufrufer, die mindestens 60 s warten dürfen; KI-Note (5 s) und /paket im Clip-Bot (0 s) versuchen es ohnehin gleich wieder. Etwa 20–60 Zeilen am Tag je Instanz. Ein echter Hänger schreibt keine Zeile (er endet nie) – man sieht ihn an den „gesperrt“-Zeilen der Wartenden; nicht an clip-sitzungen und clip-mikro, die beendet systemd nach 2 h knapp davor (**präzisiert am 09.10. durch M154**) |
| M141 | Das Protokoll ändert nie die Transaktion des Aufrufers. Nur cli.main darf eine offene Transaktion vor dem Schreiben verwerfen: seine Verbindung wird direkt danach geschlossen, was sie heute schon verwirft. Alle anderen (Bots, benutzer): Ist dort eine Transaktion offen, weder commit noch rollback, sondern eine eigene kurze Verbindung (höchstens 2 s, `mode=rw`, legt nie eine Datenbank an); ist die Datenbank belegt, nur eine Logzeile. Ohne offene Transaktion geht die Zeile über die Verbindung des Aufrufers und ist sofort festgeschrieben (dabei höchstens 2 s Warten, danach gilt wieder seine Wartezeit). Clip-Bot (/paket, rendert in einem Thread ohne die Bot-Verbindung) und `benutzer einrichten` schreiben immer über eine eigene kurze Verbindung – fehlt die Datenbank, still nichts. Geprüft an allen sieben Stellen: Am Ende des Sperr-Blocks ist nirgends eine Transaktion offen (alle Verbindungen ohne verstecktes BEGIN, `db.transaktion` schließt auch bei Fehlern). Jeder Fehler beim Schreiben steht nur im Log; Auftrag, Exit-Code und JSON-Zeile bleiben gleich |
| M142 | Render-Details stehen nur im vorhandenen Sidecar `<video>.render.json` (Leser nehmen nur ihre Felder): render_s (ab dem ersten Versuch, mit Lautheit), rueckfall (VA-API → CPU), eingabe_mb (Summe der Eingabedateien, das bekäme ein anderer Rechner) und dauer_s (echte Bildlänge). `laufzeiten` rechnet Sekunden je Video-Sekunde je Encoder und Fassung (entwurf, upload); ein Rückfall zählt unter „h264_vaapi→libx264“, weil seine Zeit den gescheiterten Versuch enthält. Ältere Entwürfe ohne render_s aus Dateizeiten (Sidecar-mtime − `entwuerfe.erstellt`, Länge aus der Datenbank); über 2 h (Nachholen, Hand-Render) zählt nicht. ✅ → Upload: Datei-Zeit der Upload-Fassung − Zeit deines ✅ (über 2 h: ausgelassen). Abend → Video: Datei-Zeit des Abend-Videos ab „🎮 Abend erkannt“ und ab Abend-Ende, bis 24 h (Nachholen, Nachtrag), dazu „über 30 min“ (Auslöser im Plan). Freigabe-Quote: gezeigte Shorts mit ✅. Das 2-Wochen-Video hat kein Sidecar neben seiner Datei und zählt nur über seine Zeile „highlight“. Fehlende Werte sind null |

### Stufe 3, Schritt 2 · Ausfallsicher (09.10.2026) – Annahmen bis Florian widerspricht
Abnahme „Ergebnisse bleiben korrekt, auch bei Worker-Ausfall, Wiederholung oder Verbindungsabbruch“: Unter systemd
leistete der Bestand das schon (nachgestellt); geschlossen sind vier Lücken – ffmpeg stirbt mit seinem Aufrufer, fertige
Dateien kommen per fsync auf die Platte, CPU-Rückfall beim Schneiden, Zeitgrenzen. Anders ist nur das Verhalten im
Fehlerfall; Videos, Clips, Schnittlisten, Zwischennamen, JSON-Zeilen, Exit-Codes und Texte bleiben. Tests:
`tests/test_abnahme_stufe3.py` (SIGKILL der ganzen Prozessgruppe mitten im Abend-Video → Sperre frei, kein halbes Video,
Nachholen auf der CPU, genau ein Video ±2 Bilder, dritter Lauf tut nichts; verwaistes ffmpeg aus Haupt- und
Arbeits-Thread nach höchstens 1 s weg – ohne setpriv lebt es weiter, nachgestellt; `render --session` mit abreißender
Ausgabe → Clips gespeichert, Wiederholung neu = 0, alle Dateien gleich nach SHA-256), `tests/test_medien.py`
(Todes-Signal im Kind gelesen, ohne setpriv wie bisher mit einer Logzeile, `uebernehmen`, Rückfall in `schneide`),
`test_deploy_benutzer` (1 h; das Update bringt die Zeitgrenzen nur in unveränderte Units), der Popen-Filter in
`test_ende_zu_ende_stufe2` liest das Programm nach `--`. Messung im Container (4 vCPU, nur CPU; nur als Verhältnis, M152):
setpriv +1,7 ms je Aufruf, fsync +9 ms (8 MB) bzw. +27 ms (25 MB) je Datei, derselbe 720p-Entwurf (48 s, 7 Segmente,
Effekte an) vorher 69,7 s, nachher 70,7 s (Median aus je 3 Läufen) – im Rauschen, die Läufe einer Gruppe streuen um 5 s.

| Nr. | Annahme (bis Florian widerspricht) |
|---|---|
| M143 | Jeder Befehl über `medien.fuehre_aus` startet unter Linux als `setpriv --pdeathsig KILL -- <Befehl>` (util-linux, über PATH gefunden), wenn die Probe `setpriv --pdeathsig KILL -- true` klappt – einmal je Prozess, das Ergebnis bleibt im Speicher. Fehlt setpriv oder scheitert die Probe: wie bisher ohne, mit einer Warnzeile. „Programm … nicht gefunden“ bleibt dieselbe Meldung (`shutil.which` vor dem Start). setpriv ersetzt sich durch den Befehl (gleiche PID): Wächter und Abbruch treffen weiter ffmpeg. Das Signal hängt am Thread, der den Prozess startet; `fuehre_aus` wartet im selben Thread auf das Ende – deshalb sicher, auch in den Arbeits-Threads der Bots (Kommentar an der Stelle: so lassen). Die Zwischennamen bleiben `<name>.tmp.mp4`: Die eine Sperre und das Mitsterben schließen zwei Schreiber aus; eigene Namen je Auftrag erst mit dem Worker-Vertrag. Ohne Mitsterben bleiben nur kurze Aufrufe außerhalb von `fuehre_aus` (`ffmpeg -version`/`-encoders`, ffprobe, der VA-API-Test in `bestand`) und `musik.lade_pcm` (endet ohnehin, sobald seine Pipe bricht) |
| M144 | `medien.uebernehmen(tmp, ziel)`: fsync der Datei (unter Windows mit Schreibrecht geöffnet, M153), `os.replace`, fsync des Ordners. Ein Fehler beim Ordner zählt nicht; scheitert der fsync der Datei, wird nicht umbenannt (der Endname bleibt frei, der Fehler fliegt wie bisher). Eingesetzt nur dort, wo ein fertiges Video umbenannt wird: `entwurf.rendere` (Video und Stems), `medien.schneide` (Bot-Clips, Momente, Fails), `medien.vorschau`, `shorts.rendere`, `highlight.erstelle` – dort auch die Marke `<id>.json`, geschrieben über `<id>.tmp.json` (der Lager-Abgleich lässt `.tmp.` aus); eine halbe Marke ließ vorher jeden neuen Aufruf mit Exit 1 enden. Nicht: das Zwischen-Umbenennen beim Angleichen der Lautheit (danach kommt `uebernehmen`), das Sidecar `.render.json` (nur Messhilfe), `material.kopiere`/`kuerze` und der harte Link des Nachschnitts (seine Datei kam schon über `schneide`). Kosten: unter 1 s je Video (Container +9 ms für 8 MB, +27 ms für 25 MB) |
| M145 | Der Rückfall in `medien.schneide` wirkt nur bei `[schnitt].encoder = "h264_vaapi"`: Scheitert oder hängt der Schnitt (Wächter 180 s), schneidet libx264 einmal nach – mit demselben crf (VA-API `-qp n` → `-crf n`), derselben Stelle, Länge und Bildrate, dazu eine Warnzeile, wie in `entwurf.rendere`. Scheitert auch das oder schon libx264, fliegt der MedienFehler wie bisher (kein zweiter Versuch). Wirkt bei n8n-render (Bot-Clips), Nachschnitt der Momente, Fails und `scan` der Freunde. Welcher Wert in Florians lokal.toml steht, ist im Repo nicht sichtbar; Freunde erben ihn (M15) |
| M146 | `clip-sitzungen.service` bekommt `TimeoutStartSec=2h` (wie clip-mikro; zählt ab dem Start, also mit dem Warten auf die Sperre). An der Grenze beendet systemd den ganzen Lauf; das Abend-Video steht dann auf „neu“ ohne Datei, und der nächste Timer-Lauf rendert es auf der CPU nach (`sitzung._nachholen`, bis 12 h nach dem Abend). Die Timer-Dienste der Freunde (`clip-freund-scan@`, `clip-freund-abend@`) bekommen 1 h statt 2 h (ändert M46): kürzer als Florians 7200 s Wartezeit, so treibt ein hängender Freund Florians n8n-Schritt nie bis Exit 4. Abholen und Lager der Freunde nehmen die Rechen-Sperre nicht und bleiben bei 2 h. Das Update übernimmt die neuen Zeilen nur, wo die installierte Datei genau dem alten Repo-Stand entspricht (vorhandener Mechanismus, jetzt mit Test); sonst meldet es „weicht vom Repo ab“, und Florian trägt die Zeile selbst ein. Die Lern-Bots bleiben ohne Grenze je Bau; es gibt keine Notbremse (kein `os._exit`, kein Exit 5) – **präzisiert am 09.10. durch M154** (die 2 h bleiben bewusst gleich der Wartezeit) |

### Stufe 3, Schritt 3 · Florian zuerst (09.10.2026) – Annahmen bis Florian widerspricht
M7 hatte für Stufe 3 „faire Reihenfolge und Vorrang“ zugesagt. Vorher fragte jeder Wartende jede Sekunde nach der
Rechen-Sperre: Wer drankam, war Zufall, und ein wartender Freund schnappte sich die Sperre auch in der Lücke zwischen
zwei n8n-Schritten. Jetzt fragen Freunde seltener (rund 10 Zeilen in `sperre.sperre`); Florians Prozesse bleiben, wie
sie sind. Test: `tests/test_sperre_gemeinsam.py`, `FlorianZuerst` – echte flock-Sperre, nachgebaute Uhr und
nachgebauter Zufall: Freund (Datei nur lesend über `nur_lesbar`) 0–1 s Anlauf, dann Schritte von 4–6 s, an der Frist
ein letzter Versuch, mit warten_s = 0 genau ein Versuch; Florian Schritte von 1 s ohne Anlauf, eine freie Sperre im
ersten Versuch. Messung im Container (Wegwerf-Skript): der echte Code von vorher (main 365af4a) und nachher mit
nachgebauter Uhr, je 20 000 Durchgänge – ein Freund ist bei einer Übergabe in 10 statt 50 % zuerst dran (drei Freunde 27
statt 75 %); eine n8n-Lücke von 1 s erwischt ein schon wartender Freund in 20 statt 100 % (drei 49 statt 100 %), von
0,3 s in 6 statt 30 % (17 statt 66 %), von 2 s in 40 statt 100 % (78 statt 100 %); startet sein Timer erst in der
1-s-Lücke, in 50 statt 100 %. Mit echten Prozessen und echter Sperre (Lücke 1 s, der Freund kommt während Florians
Schritt an): vorher 10 von 10, nachher 4 von 30. Preis: Ein Freund kommt nach dem Freiwerden im Mittel 2,6 statt 0,5 s
später dran (p90 4,6 statt 0,9 s), bei freier Sperre nach 0,5 s; Florian unverändert.

| Nr. | Annahme (bis Florian widerspricht) |
|---|---|
| M147 | Die Rolle an der Rechen-Sperre ergibt sich aus dem Öffnungsmodus der Sperrdatei (`sperre.nur_lesend`): nur lesend offen = Freund – bei ihm ist Florians Datei schreibgeschützt eingebunden und gehört pipeline, er kann die Rolle nicht fälschen –, schreibend = Florian. Wer schreiben darf (auch root ohne Sandbox) und unter Windows: wie bisher. Ein Freund wartet nur bei warten_s > 0 vor seinem ersten Versuch zufällig 0–1 s und fragt danach in zufälligen Abständen von 4–6 s statt jede Sekunde; er schläft nie über seine Frist hinaus, an der Frist kommt ein letzter Versuch, dann „gesperrt“ wie bisher. Florians Prozesse genau wie bisher (erster Versuch sofort, dann jede Sekunde, 7200 s); Vorrang hat jeder seiner Schritte, auch Mic-Schritt und Abend-Video. Nur monotone Zeit, keine Datei, keine Uhrzeit, keine Konfig (die Spannen stehen in `sperre.py`), Zufall aus dem Modul `random`. Weich: Ein laufender Auftrag wird nie unterbrochen, eine n8n-Lücke ist nur teilweise geschützt. Unverändert: alle Aufrufe mit warten_s = 0 (Briefkasten, Lager, /paket im Clip-Bot, die Isolationstests), die eigenen Sperren von Lager und Briefkasten und `big._sperre_belegt` (nur probeweise). `laufzeiten.lauf` nimmt die Sperre über `sperre.sperre`, „gewartet“ zählt den Anlauf mit; die Logzeile „Sperre gewartet x s, gehalten y s“ bleibt. Harter Vortritt (Marke über die Änderungszeit der Sperrdatei) erst, wenn `laufzeiten` es verlangt: Florians n8n-Schritte warten im Median über 2 min, oder das p90 seines Lern-Bot-Baus liegt über 5 min |

### Stufe 3, Schritt 4 · Vertrag für weitere Rechner und Stufenbericht (09.10.2026) – Annahmen bis Florian widerspricht
Nur Doku und ein Satz: `docs/WORKER.md` (lokal zuerst mit den sechs Fragen aus Abschnitt D, Bewertung der Rechner,
Auslöser, Vertrag v1 mit Versuchsnummer als Fencing, Zugang, Datenminimierung, Cloud-Regeln, Pflicht-Tests, erste
Auftragsart Whisper), `render-entwurf --final` in `docs/REGIE.md` als Vorläufer v0 markiert, Stufenbericht in
`docs/MEHRBENUTZER.md`. In `deploy/pve-mini/alles-aktualisieren.sh` stand „schlägt er fehl, wiederholt n8n ihn“ – das
stimmt nicht, kein SSH-Knoten in `1-match-verarbeiten.json` hat retryOnFail; jetzt: n8n meldet den Fehler, nachholen
mit `pipeline process <ID>` (kein Test prüft den Text; `bash -n`, `shellcheck -S warning` und die Tests des
Update-Skripts grün). Kein Code, kein neuer Test, kein Dienst, nichts vor Ort.

| Nr. | Annahme (bis Florian widerspricht) |
|---|---|
| M148 | Verbindungsabbruch heißt heute: n8n → Mini (SSH ohne Terminal, `no-pty` in `deploy/n8n-lauf.sh`) und beim Freund PC → Briefkasten → Mini (Stufe 2, Wiederaufnahme über den Lieferschein, `test_briefkasten`). Reißt die n8n-Verbindung mitten im Schritt ab, rechnet der Schritt zu Ende – ohne Terminal kommt kein Abbruchsignal, und `render` schreibt jeden Clip einzeln fest; nur die JSON-Zeile geht verloren (nachgestellt in `test_abnahme_stufe3`: Clips gespeichert, Wiederholung neu = 0, Dateien gleich). Reißt sie zwischen zwei Schritten ab, bleibt das Match auf `neu`: n8n wiederholt nicht von selbst, sein Fehler-Alarm meldet es, und das Abend-Video kommt nach `sitzungen.warten_h` (2 h) ohne dieses Match, mit Hinweis. Nachholen von Hand: `pipeline process <ID>`. Automatisch nachgeholt wird nur mit Florians Ja (Nachhol-Timer, offene Frage in CLAUDE.md) |
| M149 | In Stufe 3 gibt es keinen Fern- oder Cloud-Rechner und keine Auswahl-Logik – mit einem einzigen Rechner wäre ihre Antwort immer „Mini“. Der Vertrag ist Doku (v1, `docs/WORKER.md`), für Heimserver und Cloud gleich. Den Cloud-Schalter (`[worker].cloud`, Zustimmung mit Datum in lokal.toml, Monatslimit in Euro gegen ein Kassenbuch in der Datenbank) gibt es erst mit dem ersten Worker im Code, dann ab Werk aus. Gebaut wird erst bei einem Auslöser aus `pipeline laufzeiten` (Abend-Video an 3 Abenden einer Woche mehr als 30 min nach „Abend erkannt“ fertig, oder Florians Wartezeit auf die Sperre im p90 über 10 min) und mit Florians Ja; vorher kommt lokales Entlasten, dann ein Rechner im Heimnetz, die Cloud zuletzt. Die Kopien auf einem Worker nach 24 h zu löschen braucht dieses Ja ausdrücklich. Freundes-PCs und der vServer kommen als Rechner nicht in Frage (Datenschutz, Prinzip 1, M106). `render-entwurf --final` bleibt unverändert als Vorläufer v0 (im Puffer-Betrieb aus, nicht wieder einschalten; Gründe in REGIE.md) |
| M150 | Keine zentrale Auftrags-Datenbank und keine Warteschlange. Ein Auftrag ist die vorhandene Statuszeile je Instanz (`matches`, `entwuerfe`, `sitzungen`, die Merkliste `entwurf_bewertungen.folge`, `abholung`) plus seine `lauf`-Zeile (M139); Statuszeilen, Timer und die eine flock-Sperre sind zusammen die Warteschlange, mit weichem Vorrang für Florian (M147). Eine gemeinsame Auftrags-Datenbank bräuchte einen Ort, an den alle Instanzen schreiben dürfen – das widerspräche M1 (eigene Datenbank je Freund, Sperrdatei nur lesbar) |
| M151 | Ob der Short gleich in Upload-Qualität gerendert und als Vorschau geschickt wird (1080×1920 unter 48 MB; spart im Container rund 105 s je Freigabe, kostet rund 31 s = +42 % je Entwurf), gehört nicht in Stufe 3. Entschieden wird selbstständig, sobald `pipeline laufzeiten` echte Mini-Zeiten und die Freigabe-Quote zeigt – es lohnt ab etwa 30 % Freigabe |
| M152 | Alle Container-Zahlen der Stufe 3 (4 vCPU, nur Prozessor, künstliches Material, ffmpeg 6.1) gelten nur als Verhältnisse, nicht als Sekunden für den Mini: Der hat 8 Kerne und VA-API, und echte Aufnahmen haben eher höhere Bitraten. Echte Mini-Zahlen kommen aus `pipeline laufzeiten` vor Ort (`--tage 30` als Vorher-Grundlage aus Dateizeiten, `--tage 7` nach einer Woche mit dem ersten aktiven Freund). Bis dahin steht im Repo keine Renderzeit vom Mini |

### Stufe 3 · Prüfung (09.10.2026) – Annahmen bis Florian widerspricht
Ein Prüfer, zwei kleine Befunde, keiner blockierend. K1 (fsync unter Windows) ist behoben. K2 (2-h-Grenze von
clip-sitzungen) bleibt mit Begründung; die Folge steht jetzt in M140 und M146. Tests: `test_medien` (fsync wie unter
Windows nachgestellt – auf dem alten Code rot), `test_deploy_benutzer` (Florians Timer mit Sperre enden spätestens nach
seiner Wartezeit – rot, sobald jemand die Grenze darüber setzt, etwa auf 2 h 5 min). Nachgestellt mit der echten Sperre,
3 s statt 2 h, `timeout` statt systemd (Wegwerf-Skript): Grenze = Wartezeit → ein wartender Abend-Lauf wird vor seinem
Exit 4 beendet (3 von 3), ein n8n-Schritt nach einem hängenden Abend-Lauf bekommt die Sperre noch (3 von 3); Grenze =
Wartezeit + 5 s → der wartende Lauf endet mit Exit 4 (3 von 3), der n8n-Schritt aber auch (3 von 3).

| Nr. | Annahme (bis Florian widerspricht) |
|---|---|
| M153 | `medien.uebernehmen` öffnet die fertige Datei für den fsync unter Windows mit Schreibrecht (`O_RDWR`): Dort ist `os.fsync` FlushFileBuffers und scheitert auf einem nur lesend geöffneten Handle (EBADF) – seit M144 endete beim Entwickeln unter Windows jedes Rendern, bevor die Datei ihren Namen bekam. Unter Linux (Mini, Freunde, CI) bleibt es bei nur lesend. Windows selbst lief hier nicht; nachgestellt mit einem fsync, der sich wie unter Windows verhält |
| M154 | `clip-sitzungen` bleibt bei `TimeoutStartSec=2h`, gleich Florians Wartezeit von 7200 s (wie clip-mikro). Folge: Wartet ein Lauf die ganzen 2 h auf die Sperre (nur, wenn etwas anderes so lange hängt), beendet ihn systemd knapp vor seinem Exit 4 – der Dienst steht dann auf „failed (timeout)“, eine Zeile „gesperrt“ gibt es nicht. Nichts geht verloren, der Timer startet ihn wieder, und nichts schlägt Alarm (kein `OnFailure`, keine Prüfung auf fehlgeschlagene Dienste). Den Hänger zeigen dann die „gesperrt“-Zeilen von n8n-Schritten und Bots und `systemctl status clip-sitzungen`. Bewusst nicht länger (Vorschlag des Prüfers: 2 h 5 min): Hängt der Abend-Lauf selbst mit der Sperre, bekommt ein n8n-Schritt, der danach kommt, sie mit 2 h noch vor seiner Frist; mit 2 h + x endete ein n8n-Schritt, der in den ersten x kommt, mit Exit 4, und sein Match bliebe auf `neu` (M148). Ein Test hält die Regel fest: Florians Timer mit Sperre laufen nie länger als seine Wartezeit |

### Stufe 4, Schritt 1 · `pipeline erfolg` – Ziele getrennt, belegter Strategievergleich (09.10.2026) – Annahmen bis Florian widerspricht
Stufe 4 („Qualitäts- und Erfolgsmessung“) nach dem Plan aus zwei Entwürfen. Abnahme: „Das System zeigt belegbare
Unterschiede zwischen Strategien, ohne fehlende Daten zu erfinden.“ Neu ist nur ein Befehl zum Nachsehen:
`pipeline erfolg` (`src/clip_pipeline/erfolg.py`) – nur lesen, keine Sperre, weckt nie, kein Dienst, keine Tabelle, über
n8n nicht erreichbar (`deploy/n8n-lauf.sh` unverändert, ein Fall mehr in `test_n8n_einstieg`). Bots, Wochenbericht,
Lernen, Videos und Captions bleiben, wie sie sind; die Zeile für den Wochenbericht (`erfolg.zeile_einfach`) ist gebaut
und getestet, eingehängt wird sie erst in Schritt 2. Tests: `tests/test_erfolg.py` – 48 Shorts, „erzählt“ mit doppelten
Reaktionen → „belegt besser“, nie die Gegenrichtung; Bindung, Follower, Webseite „nicht gemessen“ mit Grund; KI-Noten
ändern nichts; Zuschauer = `posts.score`. 9 Shorts mit 3 Crossposts, einer Fassung und einem wartenden → kein Befund,
8 × „Basis zu klein“, die Fassung einmal, „frühestens nach 13 weiteren“; Freund ohne Abruf; kaputte Gewichte → Exit 2.
Messung (Wegwerf-Skripte, echter Score-Code): 300 Posts in 15 ms, der ganze Befehl 0,25 s. Versuch E (zwei Aufbauten aus
derselben Verteilung): heute „👎 Kommt weniger an: wenig Zeitlupe · Aufbau „erzählt““, mit `erfolg` kein „belegt“ (8 und
60 Videos). Ohne echten Unterschied bei wöchentlichem Nachsehen ein falsches „belegt“ in 3,6 % der Halbjahre und 6,3 %
der Jahre (je 1000 Läufe, 3 Shorts je Woche; nur ein Blick am Ende: 0,4 bzw. 0,8 %). Doppelte Reaktionen bei einem
Aufbau: in 100 % binnen eines Jahres belegt (Median Woche 16), ×1,6 in 99 % (Woche 22), ×1,3 in 49 % (Woche 32), je
200 Läufe.

| Nr. | Annahme (bis Florian widerspricht) |
|---|---|
| M155 | Belege kommen nur aus der eingefrorenen Wochen-Note (`posts.score_teile`: Messung nächst Tag 7, frühestens Tag 3, nie überschrieben). Die laufende Zuschauer-Note (`audience_ergebnisse`) bleibt das Lernsignal; sie vergleicht mit Startwerten, hat kein Mindestalter und driftet (Versuch F: −0,064 → −0,181 ohne eigene Änderung). Nie als Beleg: KI-Note, Cutter-Note, ✅/❌, Erwartung |
| M156 | Keine Tabelle für Zielgrößen, keine Migration. `erfolg.py` rechnet bei jedem Aufruf aus eingefrorenen Eingaben (`score_teile`, die Messung per `messung_id`) und nennt `ERFOLG_VERSION = 1` und die benutzten Gewichte in der JSON-Zeile. `posts.score`, `score_teile` und `audience_ergebnisse` bleiben Zeichen für Zeichen; eine Version 2 würde neben Version 1 gerechnet |
| M157 | `[erfolg.gewichte]` zuschauer 0,5 · follower 0,2 · webseite 0,3 – nur in `pipeline.toml` bzw. `lokal.toml`, kein Knopf. Gesamt = Σ Gewicht · Wert nur über die gemessenen Ziele mit Gewicht > 0, geteilt durch deren Gewichte; verglichen wird nur innerhalb der größten Gruppe mit denselben gemessenen Zielen. Ungültig (fehlt, keine endliche Zahl, negativ, Summe 0, unbekannter Name – ein Tippfehler in `lokal.toml` soll nicht still als Standard gelten): KonfigFehler, Exit 2. Heute auf TikTok: Gesamt = Zuschauer |
| M158 | Einheit = ein Short je Plattform; ein Crosspost ist je Plattform eine eigene Einheit. Fassungen eines Videos (Familie = kleinste Nummer aus dem Entwurf und seinem Parameter „ersetzt“, wie `szenen.verlauf`) zählen einmal: die erste zählbare in Upload-Reihenfolge, jede weitere ist „weitere Fassung“. Nicht gezählt, mit Grund: Clip-Post, kein Short (2-Wochen-Video), Fail-Video, „Basis zu klein“, Messung außerhalb Tag 4–10, unlesbare Score-Teile. Ohne Wochenzahlen „wartet“ ein Post höchstens 14 Tage ab Upload (wie `geschmack.OHNE_ZAHLEN_TAGE`), danach „keine Wochenzahlen nach 14 Tagen“ (nicht hochgeladen oder nicht gefunden) – sonst wüchse „wartet“ mit jedem nie hochgeladenen ✅-Video |
| M159 | Strategien je Plattform fest m = 9: Aufbau 4 × eine gegen die anderen, Tempo schnell gegen ruhig, Zeitlupe viel gegen wenig, Länge unter 45 / 45–60 / über 60 s je gegen den Rest. Quelle `geschmack._wahl_aus` (vom Publikums-Modell übersteuerte Schrauben fehlen dort schon) und `posts.dauer_s`; ein Video ohne diese Schraube steht im Vergleich auf keiner Seite. Mischung (Twist) nicht: Der einfache Modus wählt nie eine Variante. Neue Strategien erst in Stufe 5 mit neuem, wieder festem m |
| M160 | Je Seite mindestens 8 Videos, sonst „noch zu wenig Videos – frühestens nach k weiteren“ (k = was beiden Seiten zusammen fehlt). Ø-Länge beider Seiten höchstens 15 % auseinander (die längere höchstens 1,15 × die kürzere; nicht beim Merkmal Länge), sonst „nicht vergleichbar (Länge)“. Welch-Intervall des Mittelwertunterschieds, t-Quantil nach Cornish-Fisher (bei ν ≥ 7 höchstens 0,1 % zu klein, nachgerechnet), Bonferroni über m = 9, 1 % Irrtum je Plattform und Auswertung. „Belegt“ nur fürs Gesamtziel und nur, wenn das Intervall die 0 nicht enthält; streuen beide Seiten gar nicht, wird nichts behauptet. Teilziele (Bindung, Reaktionen, Reichweite, Follower, Webseite) nur beschreibend: n und Median je Seite. Die Konstanten stehen nur im Code |
| M161 | Wortwahl „kommt besser an“ (ein Zusammenhang, kein Beweis der Ursache: Strategien werden nicht zufällig zugeteilt, Abende sind verschieden stark, gemessen werden nur hochgeladene Videos) und „sehr wahrscheinlich kein Zufall“ statt einer Prozentzahl. Nachgerechnet (je 1000 Läufe): Wöchentliches Nachsehen hebt die 1 % je Auswertung auf 3,6 % im Halbjahr und 6,3 % im Jahr – der Plan schätzte 2,5 % und etwa 3 %. Bei zwei Wahlen (Tempo, Zeitlupe) wird immer die bessere genannt; sind auch Follower gemessen, heißt es „insgesamt“ statt „bei den Zuschauern“ |
| M163 | Die Lerner (geschmack, stile, viral, massstab, lernen, autonom) rechnen in Stufe 4 unverändert mit der laufenden Note (N75). Reife Werte und `[erfolg.gewichte]` kommen mit der Liga in Stufe 5 – ein Umbau statt zwei |
| M164 | Saves fehlen in den Zahlen der TikTok-API immer – für alle diese Posts gleich, die Reaktionen bleiben vergleichbar. Tag-7-Messungen per Screenshot sind im einfachen Modus selten und werden nicht getrennt behandelt. Follower: je 1000 Views aus derselben Messung, robust gegen bis zu `[publikum].fenster` (20) frühere bewertete Posts derselben Plattform mit Follower-Zahl (mindestens 5, kleinste Streuung 0,5). Als Grund für „nicht gemessen“ steht nur, was sicher stimmt: TikTok liefert keine Wiedergabezeit und keine Follower je Video; YouTube „nicht verbunden“, wenn `.env` keinen Zugang hat (ohne Netz geprüft); clip-battle.de zählt noch nicht |
| M165 | **Vorbereitet, gebaut erst nach Florians OK** (Zähler auf clip-battle.de): Kampagnencode = Kürzel (3 zufällige Buchstaben ohne i, l, o, je Instanz, nie der Name) + Videonummer, Profil-Code + t/y, Adresse `clip-battle.de/?k=…`; nur in der Werbezeile der Lern-Bot-Captions, gezeigt nur, wenn er im verschickten Text steht; Schalter ab Werk aus |
| M166 | **Vorbereitet, gebaut erst nach Florians OK und Rechtsprüfung:** Die Webseite zählt nur Aufrufe je Code und Tag auf der Startseite – ohne Cookie, Session, IP, User-Agent und Personenbezug; Bots, HEAD und Prefetch zählen nicht; Deckel je Code und Tag, danach 302 ohne Code; Schalter `ENABLE_CAMPAIGN_COUNTER` ab Werk aus |
| M167 | **Vorbereitet, gebaut erst nach Florians OK:** Tage vor „zählt seit“ sind nicht gemessen, ein fehlender Tag danach ist eine gemessene 0, Fenster 7 Tage ab Upload. Kanal-Codes und Kanal-Follower nur als Wochenwert je Konto, nie je Video, nie gelernt. Registrierungen bleiben „nicht gemessen“ |
| M168 | Die Prognose rechnet mit 3 hochgeladenen Shorts pro Woche (die echte Zahl ist unbekannt): ein erster Vergleich frühestens nach etwa 7 Wochen (Tempo, Zeitlupe) bzw. 12 (Aufbau); ein doppelter Unterschied ist im Median nach etwa 16 Wochen belegt, 30 % besser binnen eines Jahres nur in etwa jedem zweiten Fall |
| M169 | Freunde rechnen mit denselben Regeln und Gewichten aus dem Repo. Bei ihnen holt niemand Zuschauerzahlen ab (kein `clip-publikum` je Freund): `pipeline erfolg` sagt „Zuschauerzahlen werden bei dir noch nicht abgeholt“, `zeile_einfach` gibt nichts zurück (keine Daueransage im Wochenbericht). Bei Florian ohne TikTok-Zugang: „TikTok nicht verbunden – einmal /tiktok“ (Erkennung wie `geschmack.lehrer_zeile`, ohne Netz) |
| M170 | **Vorbereitet, gebaut erst nach Florians OK:** Der Kampagnen-Teil der Pipeline (PR 4/5 des Plans) wäre ohne OK baubar, kommt aber erst nach Florians Ja zum Zähler, parallel zur Rechtsprüfung – vorher wäre er toter Code |

M162 („Kommt gut an / Kommt weniger an“ wird „Wähle ich gerade öfter / seltener“) gehört zum Wochenbericht und steht bei
Schritt 2.

### Stufe 4, Schritt 2 · Ehrliche Sätze im Sonntagsbericht, in /lernstand und /publikum; Stufenbericht (09.10.2026) – Annahmen bis Florian widerspricht
PR 2 des Plans. Sichtbar anders nur der Sonntagsbericht (einfacher Modus) und unter /experte /lernstand und /publikum;
Lernen, 📋 Stand, Clip-Bot, Videos, Knöpfe, Captions und n8n bleiben gleich. Tests: `tests/test_geschmack.py` – Versuch E
als Test (zwei Aufbauten aus derselben Verteilung, keine ✅/❌, negative vorläufige Noten, dazu KI-Noten): kein „Kommt
gut an“, kein „Kommt weniger an“, kein „👀“, kein „Belegt“ (mit dem alten Bericht rot: „👎 Kommt weniger an: Aufbau
„erzählt“ · ruhige Schnitte“); kaputte `[erfolg.gewichte]` → der Bericht kommt trotzdem, der Fehler steht im Log (ohne
die Absicherung rot). `tests/test_erfolg.py` – mit der Fixture aus Schritt 1 steht „📊 Belegt (TikTok, 43 Videos):
Aufbau „erzählt“ …“ im Bericht. Alte Text-Asserts in `test_geschmack`, `test_publikum_cli` und
`test_ende_zu_ende_publikum` angepasst. Nachgestellt (Wegwerf-Skript, echter Sonntagsbericht über 200 Halbjahre à
26 Wochen × 3 Shorts ohne echten Unterschied, dieselben Daten vorher und nachher): vorher irgendwann eine Behauptung in
99,5 % der Halbjahre (in 28 % der Wochen, meist „👎 Kommt weniger an: …“), nachher in 5,0 % (±1,5; 0,6 % der Wochen) –
jedes Mal ein falsches „📊 Belegt“, im Rahmen der Simulation aus Schritt 1 (3,6 % ±0,6); „👍/👎/👀“ nie mehr.

| Nr. | Annahme (bis Florian widerspricht) |
|---|---|
| M162 | „👍 Kommt gut an / 👎 Kommt weniger an“ heißt jetzt „🎯 Wähle ich gerade öfter: … · seltener: …“ – dieselbe Zahl (Anteil ab 0,6 bzw. bis 0,4, je Wahl ab Gewicht 2, höchstens 3 bzw. 2 Wahlen) mit derselben Angabe „(n von m ✅)“, innerhalb einer Liste mit Komma statt „·“. Sie mischt deine ✅/❌, die vorläufige Zuschauer-Note und die KI-Note und sagt, was der Bot bevorzugt – kein Beleg. Ohne klares Bild wie bisher „🤔 Noch kein klares Bild – ich probiere weiter selbst aus.“ Der Bot kann weiter etwas bevorzugen, das die 📊-Zeile nicht „belegt“ nennt, oder umgekehrt: Die Lerner bleiben in Stufe 4 unverändert (M163) – Ausprobieren ist kein Beleg |
| M171 | /lernstand (nur /experte; derselbe Block steht unter /experte auch in 📋 Stand): „Tendenzen (nicht belegt) – belegt ist nur, was pipeline erfolg zeigt:“ statt „Zuletzt gelernt:“. Die Sätze selbst und ihre Rechnung (`autonom.ueberblick`, auch in der JSON-Zeile von `pipeline lernstand`) bleiben – sie erscheinen bei reinem Zufall so oft wie vorher (laut Plan 26/50/67 % bei 8/16/32 Videos), heißen aber jetzt so, wie sie sind. Weitere Zeilen ohne Mindestzahl (`stile.stil_zeile`, `viral.varianten_zeile` in `pipeline lernstand`: Durchschnitt mit Anzahl, kein „kommt an“) nennt der Plan nicht; sie bleiben |
| M172 | /publikum zeigt je Post zwei Werte getrennt: die feste „Wochen-Note … (fest; Teile in Worten)“ – bzw. „Wochen-Note noch offen (ab 7 Tagen)“, „… kommt beim nächsten Lauf“, „… offen (braucht eine Messung …)“ – und dahinter, nur wenn es ihn gibt, „Lernwert … (vorläufig, Vertrauen x %)“. „nur gegen Startwerte“ steht dabei, wenn kein Teil des Lernwerts einen früheren Post zum Vergleich hatte (alle `basis_n` 0); sind die gespeicherten Teile unlesbar, steht es nicht da (lieber nichts behaupten). Vorher stand dort nur „Publikumsscore …“ (der Lernwert), während die Tagesmeldung „📊 n Posts bewertet: #17 +0,8“ die Wochen-Note nannte – zwei Zahlen ohne Namen für dasselbe Video. Kopfzeile („n mit Score“), Tagesmeldung und Hilfe bleiben, wie sie sind |
| M173 | Sonntagsbericht: Die 📊-Zeilen (`erfolg.zeile_einfach` über `geschmack.erfolg_zeile`) stehen an der Stelle von „👀 …“, gerechnet zum Berichtszeitpunkt (bis wann ein Post „wartet“). Jeder Fehler darin – auch kaputte `[erfolg.gewichte]`, bei `pipeline erfolg` Exit 2 – geht nur ins Log (`log.exception`); der Bericht kommt dann ohne 📊-Zeile. Ohne fertige Wochenzahlen keine Zeile (wie bisher ohne Noten), beim Freund ohne Abruf keine (M169). `geschmack.zuschauer_zeile` ist entfallen; das Lernen von den Zuschauern (N75) bleibt |
| M174 | Stufenbericht (`docs/MEHRBENUTZER.md`): „vorher“ = Leser-Simulation des Plans (81,5–100 % der Halbjahre mit Gewinner-Behauptung) und die Nachstellung oben; „nachher“ = die Simulation aus Schritt 1 (falsches „belegt“ in 3,6 % der Halbjahre, 6,3 % der Jahre, je 1000 Läufe) und die Nachstellung. Echte Zahlen vom Mini fehlen noch (`pipeline erfolg` nach dem Update; erwartet „zu wenig Videos“ bzw. „nicht gemessen“) |

### Stufe 4 · Prüfung (09.10.2026) – Annahmen bis Florian widerspricht
Ein Prüfer, nichts Blockierendes; behoben sind die Befunde S4-3, S4-1 und S4-2 (alle nicht blockierend). Bei einem Freund holt niemand Zuschauerzahlen ab,
sein ✅ legt aber trotzdem TikTok-Posts an. `benutzer-befehl.sh <name> erfolg` sagte oben richtig „Zuschauerzahlen
werden bei dir noch nicht abgeholt“, darunter aber „2 warten noch auf ihre Wochenzahlen“, „Zuschauer: wartet auf die
Wochenzahlen (2 Videos)“ und „nicht hochgeladen oder nicht gefunden?“ – für Zahlen, die nie kommen. Der Sonntagsbericht
war nie betroffen (beim Freund ohne Zeile, M169). Test: `tests/test_erfolg.py` – zwei Posts ohne Zahlen (2 und 30 Tage
alt): beim Freund nirgends „wartet“, „warten“ oder „nicht gefunden“; derselbe Fall bei Florian bleibt beim Warten. Auf
dem alten Code rot, ebenso, wenn nur der Ziel-Status oder nur der Text alt ist (Gegenprobe, Wegwerf-Skript).

| Nr. | Annahme (bis Florian widerspricht) |
|---|---|
| M175 | Holt niemand Zahlen ab (Freund, M169), sagt `pipeline erfolg` nie „wartet“: Ohne gezählte Videos heißt das Ziel Zuschauer „nicht gemessen (Zuschauerzahlen werden bei dir noch nicht abgeholt)“, im Kopf steht „n noch ohne Wochenzahlen“ statt „n warten noch auf ihre Wochenzahlen“, und ältere Posts ohne Zahlen erklärt derselbe Satz statt „nicht hochgeladen oder nicht gefunden?“. „Zu wenig Vergleich“ bleibt davor: Das gibt es beim Freund nur, wenn er Zahlen per Screenshot oder von Hand geschickt hat und jemand `pipeline publikum` in seiner Instanz gestartet hat – dann ist es gemessen und stimmt. Die JSON-Zahlen `wartet` und `nicht_gezaehlt` bleiben roh, so wie `erfolg.einheiten` sie zählt. Bei Florian ändert sich nichts: Seine Zahlen holt der tägliche Abruf, ohne TikTok-Zugang steht oben „TikTok nicht verbunden – einmal /tiktok“ |
| M176 | „🎯 Wähle ich gerade öfter“ nennt je Schraube nur die Wahl, die echt vor allen anderen liegt, „seltener“ nur die echt hinterste – die Schwellen 0,6/0,4 bleiben zusätzlich. Vorher konnten bei lauter ✅ beide Wahlen einer Schraube unter „öfter“ stehen („viel Zeitlupe (3 von 3 ✅), wenig Zeitlupe (3 von 3 ✅)“) – unter dem alten Namen „Kommt gut an“ möglich, unter „Wähle ich öfter“ falsch, denn gewählt wird je Video eine. Bei Gleichstand fehlt die Schraube in der Zeile. Test `tests/test_geschmack.py` (WahlZeile), auf dem alten Code rot (Befund S4-1, nicht blockierend) |
| M177 | Der wichtigste Fehlerfall der Schutzregel hat einen eigenen Test: 20 gegen 20 Videos aus denselben Werten, 0,06 auseinander bei einer Streuung um 0,8 → kein Vergleich sagt „belegt“. Ein Intervall ohne Breite fiele dort durch (Befund S4-2, nicht blockierend; `tests/test_erfolg.py`) |
