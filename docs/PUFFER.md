# Puffer auf dem Mini, Lager auf pve-big (E19) – Einführung Schritt für Schritt

Bisher liegt alles direkt auf pve-big: Jeder Zugriff der Pipeline weckt ihn, tagsüber oft mehrmals. Neu (Entscheidung
E19 in `docs/ENTSCHEIDUNGEN.md`): Die Pipeline arbeitet nur noch auf einem **Puffer** auf dem Mini, der immer läuft.
pve-big ist nur noch das **Lager** und wird höchstens einmal am Tag geweckt – um 10:00, nie nachts (sein Lüfter
soll niemanden wecken) – und nur, wenn es etwas Neues gibt.

```
Gaming-PC ──SMB, alle 2 min──► MINI · CT "clips" · /srv/puffer  (= /srv/clips)
                                 Pipeline, Bot, n8n-Aufrufe arbeiten NUR hier – wecken nie
                                       │  täglich 10:00, nur wenn Neues da ist – nie nachts
                                       │  NFS · SHA-256 · zurücklesen · Rohdaten nie überschreiben
                                       ▼
                               pve-big · Lager (ZFS-Pool/clips, im CT /srv/big/clips)
                               danach schaltet er sich selbst ab (clip-leerlauf)
```

## Begriffe

| Wort | Bedeutung |
|---|---|
| **Puffer** | eigenes 96-GB-Volume auf dem Mini, im CT `/srv/puffer`. Nach dem Umschalten zeigt `/srv/clips` darauf. Marke `.clip-puffer`. |
| **Lager** | pve-big, `ZFS-Pool/clips`, im CT `/srv/big/clips` (NFS). Marke `.clip-lager`. Nicht „Archiv“ nennen – `archiv/` sind die Multikills. |
| **Abgleich** | einmal am Tag (10:00) Puffer → Lager, jede Datei mit SHA-256 zurückgelesen: `pipeline lager abgleich` |
| **Übernahme** | einmalig Lager → Puffer vor dem Umschalten: `pipeline lager uebernehmen` |
| **Nachtruhe** | 22:00–08:00 (`[lager].nachtruhe_von/_bis`): Der Abgleich weckt pve-big dann nie. |
| **getrennter Betrieb** | `[lager].wurzel` ist gesetzt. Leer = alles exakt wie bisher – das ist der eingebaute Rückweg. |

## Bevor du anfängst

- **Sprint in main übernommen.** R3 spielt main in die Produktion ein – und damit den **ganzen Sprint „Regisseur“**,
  nicht nur E19 (der alte Stand kennt weder `pipeline big` noch `pipeline lager`). Die Host-Schritte 1–12 aus
  `docs/ABSCHLUSSBERICHT.md` gehören zu R3.
- **clip-leerlauf auf pve-big ist scharf** (`pipeline big status` zeigt `"wecken": "erlaubt"`). Sonst darf die Pipeline
  pve-big nicht wecken (Regel 3): ab R3 enden dann auch die n8n-Schritte (`render`, `highlight`) und `/paket` bei
  schlafendem pve-big mit Exit 3 (n8n-Fehleralarm); ab R5 endet der Abgleich mit Exit 3 – nichts geht verloren,
  aber nichts kommt ins Lager, und die Morgenprüfung meldet das.
- **`[big].frist` gilt ab R3** und muss spätestens beim Umschalten (R5) leer sein. Sonst weckt die Pipeline nach dem
  28.09., 20:00 nie mehr – weder für n8n noch für den Abgleich. Die Frist ist eine Sprint-Regel: leeren erst nach
  dem Sprint oder mit deinem ausdrücklichen OK. Deshalb: **R3 bis R5 am selben Tag** – oder, wenn R3 nach der Frist
  liegt, die Frist schon in R3 leeren (Schritt 12 im Abschlussbericht).
- **R1 und R5 nicht während eines Spielabends** (CT kurz aus bzw. Dienste gestoppt). **R5 und R7 direkt nacheinander.**
- Nichts davon läuft von selbst. Jedes Skript hat `--probe` (zeigt nur, ändert nichts) und fragt vor jeder Änderung.

## Überblick

| Schritt | Wo | Freigabe nötig? | Rückweg |
|---|---|---|---|
| R0 Prüfen | pve-mini, CT | nein – nur lesen | – |
| R1 Puffer anlegen | pve-mini (Host) | **ja** – Systemänderung, CT ca. 1 min aus | `puffer-zurueck.sh` (löscht nichts) |
| R2 Samba im CT | CT | **ja** – Paket samba, neuer Netzwerkdienst | `systemctl disable --now smbd` |
| R3 Code einspielen | CT | **ja** – ganzer Sprint in die Produktion, Pakete installieren | alten Stand auschecken |
| R4 Lager-Marke + Übernahme | pve-big, CT | **ja** – Datei auf pve-big, viele GB kopieren | Marke löschen; Kopien stören nicht |
| R5 Umschalten | CT | **ja** – Konfig, Link, Frist leeren | PC + Samba still, abgleichen, dann zurück (nach R8: erst R8 zurück) |
| R6 Timer | CT | **ja** – neue Timer, clip-aufraeumen aus | Timer aus |
| R7 Gaming-PC | Windows | **ja** – dein PC, du machst es selbst | psd1 zurück |
| R8 später: Lager nur lesen | pve-big | **ja** | `read only = no` |

---

## R0 · Prüfen (nur lesen)

**Was:** Den Stand ansehen, bevor sich etwas ändert.
**Warum:** Die Skripte gehen von CT 102, Thin-Pool `pve/data`, freiem `mp1`, `/srv/clips → /srv/big/clips` und der
CT-IP `192.168.178.93` aus. Weicht etwas ab: nicht raten, melden – die Skripte lassen sich per Variable anpassen
(`CT=`, `MP=`, `POOL=`, `GROESSE_GB=`).
**Freigabe nötig?** Nein.

Auf pve-mini (Host):
```bash
lvs pve/data                                                     # Data% und Meta% des Thin-Pools
pct config 102 | grep -E '^(rootfs|mp[0-9]|unused|lxc.mount)'    # mp1 muss frei sein
pct exec 102 -- readlink /srv/clips                              # heute: /srv/big/clips
pct exec 102 -- ip -4 -br addr show eth0                         # IP des CT (angenommen: 192.168.178.93)
df -h /
```
Im CT:
```bash
sudo -u pipeline /opt/clip-pipeline/.venv/bin/pipeline big status   # "wecken": "erlaubt"?
# (kennt der alte main-Stand "big" noch nicht: /opt/clip-regie/.venv/bin/pipeline big status)
```
**Rückweg:** keiner nötig.
**Was du lernst:** Einen Thin-Pool lesen. *Data%* = belegte Datenblöcke, *Meta%* = die Buchführung darüber. Läuft
einer der beiden voll, können **alle** Gäste auf pve-mini nicht mehr schreiben – deshalb die 90-%-Grenze in R1.

---

## R1 · Puffer anlegen (pve-mini)

**Was:** Ein 96-GB-Volume im Thin-Pool, im CT als `/srv/puffer`, mit den Ordnern aus `[lager].ordner` und den Marken
`.clip-puffer` und `.clip-speicher`. Dazu ein Timer, der alle 15 min den Füllstand des Pools nach
`/mnt/big/lvm-status.txt` schreibt (im CT `/srv/big/lvm-status.txt`, liest die Morgenprüfung).
**Warum:** Die Pipeline soll auf einem Speicher arbeiten, der immer an ist. Ein eigenes Volume statt eines Ordners
auf der CT-Platte hält Clips und System getrennt: Läuft der Puffer voll, läuft der CT trotzdem weiter.
**Freigabe nötig?** Ja – Änderung am Host, der CT ist ca. 1 min aus.

1. Skripte auf den Host holen. `git fetch` holt nur, die Produktion bleibt unverändert:
   ```bash
   BRANCH=main        # vor dem Merge: sprint-regisseur
   pct exec 102 -- runuser -l pipeline -c "git -C /opt/clip-pipeline fetch -q origin +refs/heads/$BRANCH:refs/remotes/origin/$BRANCH"
   pct exec 102 -- sh -c "mkdir -p /root/e19 && runuser -u pipeline -- git -C /opt/clip-pipeline archive origin/$BRANCH deploy | tar -x -C /root/e19"
   mkdir -p /root/puffer
   for f in puffer-einrichten.sh puffer-zurueck.sh clip-lvm-status clip-lvm-status.service clip-lvm-status.timer; do
     pct pull 102 "/root/e19/deploy/pve-mini/$f" "/root/puffer/$f"
   done
   ```
2. Erst ansehen, was passieren würde: `bash /root/puffer/puffer-einrichten.sh --probe`
3. Dann echt: `bash /root/puffer/puffer-einrichten.sh` – es fragt vor jedem Schritt (`j` = ja).
   Das Skript bricht ab, wenn der Pool mit ganz vollem Puffer über 90 % käme, wenn `mp1` schon anders belegt ist
   oder gerade ein Pipeline-Schritt läuft. Die CT-Konfig sichert es vorher nach `/root/puffer-original/`.

**Prüfen:**
```bash
pct config 102 | grep mp1           # mp1: local-lvm:vm-102-disk-N,mp=/srv/puffer,backup=0,mountoptions=noatime;discard,size=96G
pct exec 102 -- ls -la /srv/puffer  # 8 Ordner, .clip-puffer, .clip-speicher – alles pipeline
cat /mnt/big/lvm-status.txt         # zeit=…, data_prozent=…, meta_prozent=…, root_frei_gb=…
```
Die Pipeline arbeitet unverändert auf pve-big weiter – `/srv/clips` zeigt noch dorthin.

**Rückweg:** `bash /root/puffer/puffer-zurueck.sh` (auch mit `--probe`). Es hängt den Puffer aus und schaltet den
Timer ab. Das Volume bleibt als `unusedN` in der CT-Konfig – die Daten bleiben erhalten. Den Befehl zum endgültigen
Löschen zeigt es nur an. Es verweigert, solange `/srv/clips` auf den Puffer zeigt.

**Was du lernst:**
- *Thin-Provisioning:* 96 GB sind versprochen, belegt wird nur, was drinsteht. Deshalb rechnet das Skript mit
  „Puffer ganz voll“.
- `backup=0`: Das vzdump-Backup des CT bleibt klein – die Clips sichert der Abgleich ins Lager.
  `noatime`: Lesen schreibt nichts. `discard`: Gelöschtes geht an den Pool zurück.
- `tune2fs -m 0`: ext4 hält sonst 5 % (ca. 4,8 GB) für root zurück – auf einer reinen Datenplatte verschenkt.
  Das geht nur bei **ausgeschaltetem CT**: Proxmox legt das ext4 mit *MMP* (Multi-Mount-Protection) an. Solange es
  eingehängt ist, schreibt der Kernel regelmäßig ein Lebenszeichen in einen MMP-Block, und tune2fs verweigert
  („MMP: device currently active“). Zu Recht: Der Kernel hält den Superblock im Speicher und schreibt ihn selbst
  zurück – wer gleichzeitig am Kernel vorbei aufs Gerät schreibt, riskiert ein kaputtes Dateisystem. Deshalb schaltet
  das Skript die Reserve in Schritt 3 ab, solange der CT ohnehin aus ist. Bei einer Wiederholung mit laufendem CT
  zeigt es nur den Befehl fürs nächste Wartungsfenster (`pct shutdown 102; tune2fs -m 0 …; pct start 102`).
  Nur lesen (`tune2fs -l`) geht auch eingehängt.
- *Unprivilegierter CT:* pipeline (UID 1000) ist draußen UID 101000. Deshalb legt das Skript die Ordner **im** CT an
  (`pct exec`), nicht vom Host aus.
- `/mnt/big` ist ein lokaler Ordner des Hosts (im CT `/srv/big`); nur `/mnt/big/clips` darunter ist das NFS von
  pve-big. Die Statusdatei zu schreiben, weckt also niemanden.

---

## R2 · Samba im CT

**Was:** Freigabe `\\192.168.178.93\clips` auf `/srv/puffer`, Benutzer `gamingpc`.
**Warum:** Der Gaming-PC kopiert bisher per SMB auf pve-big. Ab R7 kopiert er auf den Mini, der immer läuft – kein
Wecken mehr tagsüber.
**Freigabe nötig?** Ja – Paketinstallation (`samba`) und ein neuer Netzwerkdienst.

```bash
pct enter 102                                          # auf pve-mini: Shell im CT
bash /root/e19/deploy/mini/samba-einrichten.sh --probe
bash /root/e19/deploy/mini/samba-einrichten.sh         # Passwort für gamingpc selbst wählen – nie ins Repo, nie in den Chat
exit
```
Die `smb.conf` kommt aus `deploy/mini/smb-puffer.conf` und wird vor dem Neustart mit `testparm` geprüft. Die alte
liegt danach in `/root/samba-original/`.

**Prüfen:**
- Im CT: `ss -ltn | grep ':445'` – nur `127.0.0.1` und die Heimnetz-IP, **nicht** die Tailscale-Adresse (100.x).
- Auf dem PC (PowerShell): `Test-NetConnection 192.168.178.93 -Port 445` → `TcpTestSucceeded : True`.
- Explorer: `\\192.168.178.93\clips` als `gamingpc` öffnen. Im obersten Ordner eine Testdatei anlegen, im CT
  `ls -l /srv/puffer` ansehen (Besitzer pipeline), dann die Testdatei wieder löschen.

**Rückweg:** `systemctl disable --now smbd` (die Freigabe ist sofort weg). Ganz entfernen nur mit OK:
`apt-get purge samba`, `userdel gamingpc`.

**Was du lernst:**
- `force user = pipeline`: Egal, wer sich anmeldet – die Dateien gehören pipeline, die Pipeline darf sie verarbeiten.
- `veto files` vergleicht **jeden Namen auf jeder Ebene**: `/highlights/` würde auch `eingang\nvidia\highlights`
  still verschwinden lassen. Deshalb steht dort nur `.aktiv`.
- `interfaces` + `bind interfaces only` + `hosts allow`: nur Heimnetz, nicht über Tailscale; kein Gast, nur SMB3.
  `interfaces = 127.0.0.1 192.168.178.0/24` statt `eth0`: So lauscht Samba nur auf der IPv4-Adresse im Heimnetz – mit
  `eth0` hörte es auch auf dessen IPv6-Adressen, darunter eine öffentliche. Prüfen: `ss -ltn | grep ':445'`.
- `testparm` prüft die Konfiguration, **bevor** sie gilt. `strict sync`: Samba bestätigt ein „Speichern“ des PCs
  erst, wenn es auf der Platte ist.
- Systembenutzer mit `nologin`: kann sich nicht per SSH anmelden; nur Samba kennt sein Passwort.

---

## R3 · Code einspielen

**Was:** Die Produktion `/opt/clip-pipeline` auf main bringen – das ist der **ganze Sprint „Regisseur“ plus E19**,
nicht nur E19 (der heutige Stand liegt gut zwei Dutzend Commits dahinter). Den Sprint-Stand `/opt/clip-regie`
(Lern-Bot) ebenso, falls es ihn gibt.
**Warum:** Die neuen Befehle (`pipeline lager …`, `pipeline puffer …`) und die Schutzprüfungen stecken im Code.
**Was sich dabei ändert** – mehr als E19:
- Der E19-Teil schläft noch: Solange `[lager].wurzel` leer ist, gibt es keinen Abgleich und keinen Puffer-Betrieb.
- Der Sprint wirkt **sofort**: Ab jetzt gilt Regel 3 auch für die n8n-Schritte (`[big].alter_weckweg_nur_mit_aus`).
  pve-big wird nur noch geweckt, wenn sein Herunterfahren gesichert ist (`pipeline big pruefen` hat geklappt).
  Sonst enden `render`, `highlight` und `/paket` bei schlafendem pve-big mit Exit 3, und n8n schlägt Alarm.
- **Ab jetzt gilt `[big].frist`** (28.09., 20:00): Danach weckt **nichts** mehr, bis die Frist leer ist.
- Neue Tabellen in der Datenbank (siehe Rückweg), neue Pakete (numpy, faster-whisper).

**Freigabe nötig?** Ja – Produktion ändern, Pakete installieren; liegt R3 nach der Frist, zusätzlich die Frist leeren
(Sprint-Regel – nur mit deinem ausdrücklichen OK).

**Zuerst sichern** – vor allem anderen, auch vor den Host-Schritten unten und vor jedem `git pull`. Nur beim ersten
Mal (`[ -e … ] ||`): Wiederholst du den Block, bleibt die erste Sicherung stehen – sonst stünde dort schon der neue
Stand, und der Rückweg stellte nichts zurück.
```bash
# im CT als root
cd /opt/clip-pipeline
[ -e /var/lib/clip-pipeline/vor-e19.sha ] || sudo -u pipeline git rev-parse HEAD | tee /var/lib/clip-pipeline/vor-e19.sha
[ -e /var/lib/clip-pipeline/vor-e19.db ] || sudo -u pipeline sqlite3 /var/lib/clip-pipeline/pipeline.db ".backup /var/lib/clip-pipeline/vor-e19.db"
cat /var/lib/clip-pipeline/vor-e19.sha                             # der Stand VOR R3 – für den Rückweg
```

**Dann:** die Host-Schritte 1–12 aus `docs/ABSCHLUSSBERICHT.md` („Nötige Host-Änderungen“) – die Befehle unten
sind nur deren Schritt 1. Ohne die Schritte 3, 4 und 8–10 (SSH-Schlüssel, `lokal.toml`, `clip-big-steuer`,
`pipeline big pruefen`) weckt die Produktion pve-big ab R3 gar nicht mehr. Schritt 5 bringt den Wächter-Timer, der
pve-big nach getaner Arbeit wieder abschaltet.
**Frist:** R3 bis R5 am selben Tag – R5 leert die Frist. Liegt R3 nach dem 28.09., 20:00, gehört Schritt 12 gleich
hierher: in beiden `lokal.toml` unter `[big]` `frist = ""` (vorhandenen Abschnitt ändern, siehe R5).

```bash
# im CT als root
cd /opt/clip-pipeline
sudo -u pipeline git fetch -q origin && sudo -u pipeline git log --oneline HEAD..origin/main   # was alles mitkommt
sudo -u pipeline git pull --ff-only
sudo -u pipeline .venv/bin/pip install -q -e '.[whisper]'       # wie Schritt 1 im Abschlussbericht
systemctl restart clip-bot
# nur falls /opt/clip-regie existiert (Lern-Bot):
sudo -u pipeline git -C /opt/clip-regie fetch -q origin \
  && sudo -u pipeline git -C /opt/clip-regie checkout -q --detach origin/main && systemctl restart clip-lernbot
```
**Prüfen:**
```bash
sudo -u pipeline .venv/bin/pipeline status                    # läuft ohne Fehler
sudo -u pipeline .venv/bin/pipeline big status                # "wecken": "erlaubt"? Sonst weckt ab jetzt auch n8n nicht
sudo -u pipeline .venv/bin/pipeline lager status; echo $?     # Exit 2 "kein getrennter Betrieb" – richtig so
systemctl list-timers 'clip-*'                                # clip-big-waechter.timer dabei (Schritt 5)?
journalctl -u clip-bot -n 20
```
**Rückweg:** `sudo -u pipeline git checkout -q "$(cat /var/lib/clip-pipeline/vor-e19.sha)"` (stünde dort doch
schon der neue Stand: `sudo -u pipeline git reflog` zeigt, wo HEAD vorher war),
`sudo -u pipeline .venv/bin/pip install -q -e .`, die Sprint-Dienste ausschalten, die der alte Stand nicht kennt
(`systemctl disable --now clip-big-waechter.timer clip-sitzungen.timer clip-lernbot`), `systemctl restart clip-bot`.
Die Datenbank bekommt nur **neue Tabellen** – aus dem Sprint (`regie.sql`: `material`, `momente`, `entwuerfe`,
`sitzungen` …) und aus E19 (`lager`, `lager_laeufe`). Bestehende Tabellen und Spalten bleiben, wie sie sind; der alte
Code beachtet die neuen Tabellen nicht. Im Notfall `vor-e19.db` zurückkopieren – nur bei gestopptem Bot und gestoppten
Timern, und alles, was seit R3 in der Datenbank passiert ist, ist dann weg.
**Was du lernst:** `git log HEAD..origin/main` (was ein Update alles mitbringt – hier ein ganzer Sprint), `--ff-only`
(nie ungewollt mergen), `sqlite3 .backup` (stimmige Kopie im laufenden Betrieb), editierbare Installation
(`pip install -e`: der Code bleibt im Checkout, ein Neustart genügt; `[whisper]` holt die optionalen Pakete mit),
`[ -e datei ] || befehl` (nur, wenn es die Datei noch nicht gibt – ein Wiederholen überschreibt keine Sicherung).

---

## R4 · Lager markieren + Übernahme

**Was:** Auf pve-big `.clip-lager` anlegen. Dann die vorhandenen Daten einmal Lager → Puffer kopieren: alle
Ordner aus `[lager].ordner` außer `eingang/` ganz, von `eingang/` die letzten 14 Tage (`[puffer].rohdaten_tage`: so
lange hält der Puffer Rohvideos – der Regisseur braucht sie für seine Momente).
**Warum:** Nach dem Umschalten arbeitet die Pipeline nur im Puffer – Sessions, Replays, Highlights, Musik usw. müssen
dort schon liegen. Die Marken machen eine Verwechslung unmöglich: `.clip-lager` nur im Lager, `.clip-puffer` nur im
Puffer, dazu verschiedene Dateisysteme – sonst bricht jeder Abgleich mit Klartext ab und kopiert nichts.
**Freigabe nötig?** Ja – eine Datei auf pve-big; pve-big wird dafür geweckt und wach gehalten; viele GB werden
kopiert (nur kopiert: im Lager ändert sich nichts).

1. pve-big einschalten, falls er schläft. Shell öffnen (Weboberfläche → pve-big → Shell):
   ```bash
   zfs list -o name,mountpoint | grep clips          # Pfad prüfen – hier angenommen: /ZFS-Pool/clips
   test -e /ZFS-Pool/clips/.clip-speicher && touch /ZFS-Pool/clips/.clip-lager && chown clips:clips /ZFS-Pool/clips/.clip-lager
   ls -la /ZFS-Pool/clips/.clip-*                    # .clip-speicher und .clip-lager – KEIN .clip-puffer
   ```
   Danach das Fenster schließen: Eine Konsole, in der getippt wird, hält pve-big wach.
2. Im CT erst der Probelauf – er zeigt Anzahl und GB:
   ```bash
   sudo -u pipeline /opt/clip-pipeline/.venv/bin/pipeline lager uebernehmen \
     --von /srv/big/clips --nach /srv/puffer --eingang-tage 14 --probelauf
   ```
   Passt es nicht bequem (Faustregel: mehr als die Hälfte des Puffers, also über 48 GB): **nicht weitermachen,
   melden.** Dann entscheiden wir gemeinsam (weniger eingang-Tage, größerer Puffer oder alte Sessions im Lager lassen).
3. Dann echt, als eigener Dienst (läuft weiter, auch wenn du das Fenster schließt):
   ```bash
   systemd-run --unit=clip-uebernahme --uid=pipeline --gid=pipeline -p WorkingDirectory=/opt/clip-pipeline --collect \
     /opt/clip-pipeline/.venv/bin/pipeline lager uebernehmen --von /srv/big/clips --nach /srv/puffer --eingang-tage 14
   journalctl -u clip-uebernahme -f                  # Fortschritt; Strg+C beendet nur das Zuschauen
   ```

**Prüfen:** Die letzte Zeile im Journal ist eine JSON-Zeile mit `"ok": true` – „ohne Fehler“ reicht nicht: Im Lager
eben erst Geschriebenes zählt unter `"zu_jung"`, nicht unter `"fehler"`. `du -sh /srv/puffer/*` zeigt die Ordner.
Wiederholen ist jederzeit möglich – Gleiches (Größe + Zeitstempel) wird übersprungen.
**Rückweg:** Auf pve-big `rm /ZFS-Pool/clips/.clip-lager` (nur die Marke). Die Kopien im Puffer stören nicht; der
Code nutzt sie erst ab R5.
**Was du lernst:**
- Warum *zwei verschiedene* Marken plus verschiedene Dateisysteme (`st_dev`): Eine einzige Marke hätte ein
  vertauschter Link oder ein doppelt eingehängter Ordner nicht entdeckt.
- Die Pipeline schaut im Lager nur per `stat` nach, sie liest dort nie Inhalte ohne Grund: Lesen über NFS zählt
  clip-leerlauf als Zugriff und hielte pve-big wach.
- Kopieren mit Prüfsumme **und Zurücklesen**: Erst was im Lager nachweislich gleich ist, gilt als bestätigt.
- `systemd-run` für lange Aufgaben: Sie hängen nicht an deinem Terminal, und das Protokoll landet im Journal.

---

## R5 · Umschalten

**Was:** Dienste stoppen → 10 min warten → Delta-Übernahme (erst bei „ok“ weiter) → `lokal.toml` beider Checkouts →
`/srv/clips` auf den Puffer → prüfen → Dienste starten.
**Warum:** Ab jetzt arbeitet die Pipeline nur noch im Puffer und weckt pve-big nie (`[speicher].host` leer). Nur der
tägliche Abgleich (10:00, nie in der Nachtruhe) darf wecken – über `[big].host` und `[speicher].wol_mac`.
**Was dann nicht mehr geht:** `pipeline render-entwurf <id> --final` (Final-Render auf pve-big, `docs/REGIE.md`).
pve-big rendert aus seinem Speicher – dem Lager –, Auftrag und neue Clips lägen aber im Puffer. Der Befehl bricht
deshalb mit Exit 2 ab, ohne zu wecken; die Entwürfe vom Mini (`render-entwurf <id>` ohne `--final`) bleiben das
Endprodukt. Alte Final-Renders (`regie/`) bleiben im Lager, die Übernahme holt sie nicht.
**Freigabe nötig?** Ja – Konfiguration der Produktion, Bot ca. 15 min aus. **Pflicht:** `[big].frist` leeren (siehe
„Bevor du anfängst“).

**Vorher:** Die Übertragung auf dem Gaming-PC **pausieren** – dafür den PC einschalten; ihn einfach aus zu lassen
reicht nicht. Die Aufgabe liefe sonst beim nächsten Hochfahren innerhalb von 2 min wieder an, noch mit dem alten
Ziel, und kopierte auf pve-big, bevor du in R7 die psd1 änderst. Diese Dateien kämen nie in den Puffer (der PC
kopiert nichts zweimal). Kein Spielabend. R7 direkt nach R5.
```powershell
Disable-ScheduledTask -TaskName 'Clip-Pipeline Übertragung'
```
Was dann noch auf dem PC wartet, bleibt dort und kommt nach R7 in den Puffer.

1. **Dienste stoppen** und warten, bis kein Schritt mehr läuft (erst bei „frei“ weiter):
   ```bash
   # im CT als root
   systemctl stop clip-bot clip-lernbot clip-sitzungen.timer clip-aufraeumen.timer   # "not loaded" bei Fehlendem ist egal
   # Läuft noch ein Pipeline-Schritt (auch ein eben gestarteter clip-sitzungen.service)? Er hält die Sperre.
   if sudo -u pipeline flock -n /var/lib/clip-pipeline/pipeline.lock true; then echo frei
   else echo 'BELEGT – ein Schritt läuft noch: 1–2 min warten, dann diese zwei Zeilen nochmal'; fi
   ```
2. **10 min warten** (`[lager].ruhe_min`), dann die **Delta-Übernahme** – sie holt, was seit R4 auf pve-big
   dazugekommen ist. Was der letzte Schritt oder der PC eben erst ins Lager geschrieben hat, gilt vorher als „wird
   noch geschrieben“ und bliebe aus:
   ```bash
   sudo -u pipeline /opt/clip-pipeline/.venv/bin/pipeline lager uebernehmen --von /srv/big/clips --nach /srv/puffer --eingang-tage 14; echo "Exit $?"
   ```
   **Erst weiter bei `Exit 0` und `"ok": true`** in der JSON-Zeile darüber. Sonst:
   - `Exit 1` mit `"zu_jung"` über 0: noch einmal 10 min warten, dann wiederholen (Gleiches wird übersprungen).
   - `Exit 1` mit `"konflikte"` oder `"fehler"` über 0: nicht weitermachen, melden.
   - `Exit 3` (pve-big nicht wach geworden, z. B. wegen `[big].frist`): pve-big von Hand einschalten wie in R4,
     dann wiederholen.
   - `Exit 4`: Ein anderer Lauf hält die Lager-Sperre (z. B. die Übernahme aus R4) – warten, dann wiederholen.

   Nach dem Umschalten sieht `lager status` nur noch den Puffer – was hier fehlt, fiele später niemandem mehr auf.
3. **Konfiguration sichern und ändern** – in BEIDEN Checkouts (`/opt/clip-regie` nur, falls es ihn gibt). Gesichert
   wird nur beim ersten Mal: Wiederholst du den Block, bleibt die ursprüngliche Datei die Sicherung.
   ```bash
   [ -e /opt/clip-pipeline/config/lokal.toml.vor-e19 ] || cp -a /opt/clip-pipeline/config/lokal.toml /opt/clip-pipeline/config/lokal.toml.vor-e19
   [ -e /opt/clip-regie/config/lokal.toml.vor-e19 ] || cp -a /opt/clip-regie/config/lokal.toml /opt/clip-regie/config/lokal.toml.vor-e19
   nano /opt/clip-pipeline/config/lokal.toml
   nano /opt/clip-regie/config/lokal.toml
   ```
   In beiden `lokal.toml` soll danach stehen (vorhandene Abschnitte **ändern**, nicht ein zweites Mal anlegen – TOML
   erlaubt jeden Abschnitt nur einmal, sonst meldet `pipeline status` „Konfiguration … fehlerhaft“):
   ```toml
   [speicher]
   host = ""                   # vorher "192.168.178.51": der Puffer ist lokal – nie pingen, nie wecken
   wol_mac = "…"               # UNVERÄNDERT lassen: damit weckt der Abgleich um 10:00 pve-big

   [big]
   host = "192.168.178.51"     # pve-big (kam bisher aus [speicher].host)
   frist = ""                  # Pflicht: sonst weckt die Pipeline nach der Sprint-Frist nie mehr

   [lager]
   wurzel = "/srv/big/clips"
   ```
4. **`/srv/clips` auf den Puffer umstellen:**
   ```bash
   ln -sfn /srv/puffer /srv/clips
   readlink /srv/clips                                                   # /srv/puffer
   ```
5. **Prüfen, dann die Dienste starten** (clip-aufraeumen NICHT – siehe R6):
   ```bash
   sudo -u pipeline /opt/clip-pipeline/.venv/bin/pipeline lager status   # getrennt: ja · Prüfung ok · offen: 0 oder wenige
   sudo -u pipeline /opt/clip-pipeline/.venv/bin/pipeline status
   systemctl start clip-bot clip-lernbot clip-sitzungen.timer
   ```
   `lager status` zählt nur, was im Puffer liegt – ob die Übernahme vollständig war, hat allein Schritt 2 gezeigt.

**Rückweg:** Die Reihenfolge ist wichtig: Erst darf nichts Neues mehr in den Puffer kommen, dann kommt alles ins
Lager, erst danach wird umgestellt. Die Samba-Freigabe zeigt fest auf `/srv/puffer`, nicht auf den Link – der PC
kopiert dorthin, bis du ihn umstellst. Was er nach dem letzten Abgleich noch kopiert, sähe die Pipeline nie, und der PC
kopiert es auch nie ein zweites Mal (er merkt sich, was schon übertragen ist).
1. **Zufluss stoppen.** Auf dem PC: `Disable-ScheduledTask -TaskName 'Clip-Pipeline Übertragung'`. Im CT:
   `systemctl stop smbd`.
2. **Dienste stoppen** und warten, bis kein Schritt mehr läuft (erst bei „frei“ weiter):
   ```bash
   systemctl stop clip-bot clip-lernbot clip-sitzungen.timer
   if sudo -u pipeline flock -n /var/lib/clip-pipeline/pipeline.lock true; then echo frei
   else echo 'BELEGT – ein Schritt läuft noch: 1–2 min warten, dann diese zwei Zeilen nochmal'; fi
   ```
3. **10 min warten** (`[lager].ruhe_min`), dann abgleichen (weckt pve-big, falls etwas offen ist). Jüngere
   Dateien gelten als „wird noch geschrieben“: Der Abgleich lässt sie aus, und `lager status` zählt sie nicht als
   offen – vorher sagt „0 offen“ also nichts.
   ```bash
   sudo -u pipeline /opt/clip-pipeline/.venv/bin/pipeline lager abgleich
   sudo -u pipeline /opt/clip-pipeline/.venv/bin/pipeline lager status   # erst bei 0 offen weiter, sonst abgleich nochmal
   ```
4. **Timer aus R6 ausschalten** – ohne `[lager].wurzel` endeten sie jeden Tag mit Exit 2 und stünden auf „failed“:
   `systemctl disable --now clip-lager.timer clip-puffer-pruefen.timer`
5. **Umstellen:** `ln -sfn /srv/big/clips /srv/clips`, dann `lokal.toml.vor-e19` in beiden Checkouts zurück nach
   `lokal.toml` kopieren. Ist der Sprint vorbei, muss `[big] frist = ""` dabei stehen bleiben (sonst weckt die
   Produktion pve-big nicht mehr) – nachsehen und ggf. wieder eintragen. Prüfen:
   `sudo -u pipeline /opt/clip-pipeline/.venv/bin/pipeline lager status; echo $?` → Exit 2 „kein getrennter Betrieb“.
6. **Wieder an:** Ist R8 schon gemacht, **zuerst** auf pve-big die Freigabe wieder beschreibbar machen (Rückweg R8):
   ```bash
   # pve-big-Shell
   nano /etc/samba/smb.conf                           # im Abschnitt [clips]: read only = no
   testparm -s >/dev/null && systemctl reload smbd
   testparm -s --section-name=clips --parameter-name='read only' 2>/dev/null   # No
   ```
   Sonst scheitert jede Kopie des PCs (steht nur in seinem Log, n8n erfährt nichts), und er weckt pve-big alle
   2 min. Dann in der psd1 des PCs das Ziel zurück auf pve-big (Rückweg R7), **erst dann**
   `Enable-ScheduledTask -TaskName 'Clip-Pipeline Übertragung'`. Im CT die Dienste starten und
   `clip-aufraeumen.timer` wieder einschalten, falls er vorher lief; smbd bleibt aus:
   ```bash
   systemctl start clip-bot clip-lernbot clip-sitzungen.timer
   ```

Die Kopien im Puffer bleiben liegen und stören nicht. Das Volume erst löschen (den Befehl zeigt `puffer-zurueck.sh`),
wenn Schritt 3 wirklich 0 offen gezeigt hat.

**Was du lernst:**
- `flock -n` endet still mit Exit 1, wenn die Sperre belegt ist. Deshalb `if … then … else … fi`: Beide Fälle sagen
  etwas, und Stille kann nicht als „frei“ missverstanden werden.
- `ln -sfn`: `-f` ersetzt, `-n` behandelt den vorhandenen Link als Datei. Ohne `-n` legte `ln` einen neuen Link
  **in** den Ordner, auf den `/srv/clips` gerade zeigt – also auf pve-big.
- Ein Symlink als Schalter: Alle Programme nutzen weiter `/srv/clips`, nur das Ziel wechselt.
- Konfig-Schichten: `pipeline.toml` (im Repo) + `lokal.toml` (je Rechner, nicht im Repo).
- Die Pfade in der Datenbank sind relativ zur Wurzel – derselbe Datensatz passt zu Puffer und Lager.

---

## R6 · Timer an, clip-aufraeumen aus

**Was:** `clip-lager.timer` (jeden Tag 10:00) und `clip-puffer-pruefen.timer` (jeden Tag 11:00) einschalten,
`clip-aufraeumen.timer` ausschalten.
**Warum:** Der Abgleich bringt einmal am Tag das Neue ins Lager – tagsüber, damit der Lüfter von pve-big niemanden
weckt (in der Nachtruhe 22:00–08:00 weckt er nie). Die Morgenprüfung meldet Probleme höchstens einmal
am Tag je Thema, montags kommt ein Lebenszeichen. Das alte Aufräumen würde Puffer-Dateien verschieben und Pfade in der
Datenbank umschreiben; im getrennten Betrieb verweigert es (Exit 2). Löschen im Puffer kommt erst mit Stufe B5 – nur
mit deinem OK.
**Freigabe nötig?** Ja – neue Timer; der Test-Abgleich weckt pve-big einmal, falls etwas offen ist.

```bash
# im CT als root
cd /opt/clip-pipeline
cp deploy/systemd/clip-lager.service deploy/systemd/clip-lager.timer \
   deploy/systemd/clip-puffer-pruefen.service deploy/systemd/clip-puffer-pruefen.timer /etc/systemd/system/
systemctl daemon-reload
systemctl disable --now clip-aufraeumen.timer
sudo -u pipeline .venv/bin/pipeline lager abgleich --probelauf     # was wäre offen?
systemctl start clip-lager.service; journalctl -u clip-lager -n 30 # einmal echt
sudo -u pipeline .venv/bin/pipeline puffer pruefen                 # Morgenprüfung einmal von Hand
systemctl enable --now clip-lager.timer clip-puffer-pruefen.timer
systemctl list-timers 'clip-*'
```
**Prüfen:** `list-timers` zeigt 10:00 (plus bis zu 10 min Zufall) und 11:00; `pipeline lager status` zeigt den
letzten Lauf mit „ok“.
**Rückweg:** `systemctl disable --now clip-lager.timer clip-puffer-pruefen.timer`. `clip-aufraeumen.timer` erst
nach dem Zurückschalten (Rückweg R5) wieder einschalten.
**Was du lernst:**
- `OnCalendar` (feste Uhrzeit), `Persistent=true` (verpasste Läufe nachholen), `RandomizedDelaySec` (nicht auf die
  Sekunde genau). Holt der Timer nach einem Neustart des Mini einen Abgleich mitten in der Nacht nach, weckt dieser
  pve-big trotzdem nicht: Die Nachtruhe prüft die Pipeline selbst, nicht systemd.
- `SuccessExitStatus=3 4`: „pve-big nicht wach geworden“ oder „anderer Abgleich läuft“ sind kein roter Zustand in
  systemd – am nächsten Tag wieder; bleibt es dabei, meldet die Morgenprüfung.
- Härtung wie bei den anderen Diensten (`ProtectSystem=strict`, nur die `ReadWritePaths` beschreibbar). Beim
  Abgleich steht dort `/srv/big` statt `/srv/big/clips`: Um 10:00 schläft pve-big meist, der Einhängepunkt ist beim
  Start noch leer – ein eigener Bind darauf würde das später eingehängte NFS für den Dienst verdecken.

---

## R7 · Gaming-PC

**Was:** Das Skript auf dem PC auf den Stand von main bringen, das Ziel der Übertragung auf den Puffer umstellen,
Anmeldedaten speichern, Probelauf, ein Test-Match.
**Warum:** Aufnahmen kommen weiter Minuten nach dem Match an – aber auf dem Mini, der immer läuft. pve-big wird
tagsüber nie mehr geweckt.
**Freigabe nötig?** Ja – dein PC, du machst es selbst.

1. **Skript aktualisieren.** Die Aufgabe startet `windows\Uebertragung.ps1` aus dem Checkout auf dem PC – R3 hat
   nur den CT aktualisiert. Das alte Skript kopiert zwar auch in den Puffer, schreibt aber kein `pc-status.json`
   (die Morgenprüfung bliebe zum PC für immer still), und die Korrekturen aus E19 fehlen.
   ```powershell
   (Get-ScheduledTask -TaskName 'Clip-Pipeline Übertragung').Actions.Arguments   # zeigt den Pfad nach -File
   cd E:\GIT\baddieday-voting                        # dieser Checkout (so angenommen)
   git branch --show-current                          # main? Sonst erst: git switch main
   git pull --ff-only                                 # Stand von main nach dem Merge
   Select-String -Path windows\Uebertragung.ps1 -Pattern 'Schreibe-PcStatus' -Quiet   # True – sonst nicht weiter
   ```
   Deine `uebertragung.psd1` steht in `.gitignore` – `git pull` lässt sie in Ruhe.
2. **Ziel umstellen** in `windows\uebertragung.psd1` (Vorlage: Block „Puffer-Betrieb (E19)“ in
   `uebertragung.beispiel.psd1`):
   ```powershell
   Ziel         = '\\192.168.178.93\clips'
   ZielHost     = '192.168.178.93'
   WakeOnLanMac = ''                 # der Mini ist immer an – nie wecken
   ```
3. **Anmelden, Probelauf, Aufgabe wieder an** – als der Windows-Benutzer, unter dem die Aufgabe läuft, im Checkout
   aus Schritt 1:
   ```powershell
   cmdkey /add:192.168.178.93 /user:gamingpc /pass                 # fragt nach dem Passwort aus R2
   Test-Path '\\192.168.178.93\clips\.clip-speicher'               # True
   powershell -ExecutionPolicy Bypass -File windows\Uebertragung.ps1 -Probelauf
   Enable-ScheduledTask -TaskName 'Clip-Pipeline Übertragung'      # in R5 pausiert – erst jetzt, nach der neuen psd1
   ```

Lief die Aufgabe seit R5 doch noch mit dem alten Ziel (im PC-Log `%LOCALAPPDATA%\ClipPipeline\uebertragung.log`
stehen Kopien nach dem R5-Zeitpunkt), liegen diese Dateien nur im Lager: melden. Eine weitere Delta-Übernahme wie in
R5 (mit `--eingang-tage` bis zu diesem Tag) holt sie in den Puffer; die betroffenen Matches stoßen wir dann neu an.

**Test-Match:** ein Match spielen, dann
- im CT nach wenigen Minuten: `ls -lt /srv/puffer/replays | head -3`,
- der Bot schickt die Clips wie gewohnt,
- `cat /srv/puffer/sitzungen/pc-status.json` zeigt den letzten Lauf des PCs,
- `pipeline lager status` zeigt Dateien als offen; am nächsten Tag nach dem Abgleich (10:00): 0 offen
  (`journalctl -u clip-lager`).

**Rückweg:** In der psd1 wieder die Werte für pve-big eintragen (`Ziel`, `ZielHost`, `WakeOnLanMac` wie vorher).
Ist R8 schon gemacht, vorher dessen Rückweg (`read only = no`) – sonst scheitert jede Kopie still. Das neue Skript
bleibt: Es arbeitet mit pve-big als Ziel genauso.
Der PC merkt sich je Quelldatei, was schon kopiert ist – nach dem Umstellen kopiert er nichts doppelt. Genau deshalb
nur zusammen mit dem Rückweg R5 (dort Schritt 1 und 6): Was schon im Puffer liegt, kommt nur über den Abgleich nach
pve-big, nie noch einmal vom PC.
**Was du lernst:** Der Windows-Anmeldetresor (`cmdkey`) für Freigaben; warum der PC vor dem Kopieren
`.clip-speicher` prüft (so schreibt er nie in eine falsche oder leere Freigabe); `pc-status.json` als Rückkanal vom
PC, der sonst am schlechtesten zu beobachten ist.

---

## R8 · später: Lager-Freigabe auf pve-big nur lesen

**Was:** In `/etc/samba/smb.conf` auf pve-big im Abschnitt `[clips]` `read only = yes`.
**Wann:** nach ein bis zwei Wochen stabilem Betrieb mit R7.
**Warum:** Dann schreibt nur noch der Mini ins Lager (per NFS, mit Prüfsumme). Ein falsch eingestellter PC kann
Rohdaten im Lager nicht mehr überschreiben; Lesen (z. B. alte Clips im Explorer) geht weiter.
**Freigabe nötig?** Ja – Änderung auf pve-big.

```bash
# pve-big-Shell
nano /etc/samba/smb.conf                           # im Abschnitt [clips]: read only = yes
testparm -s >/dev/null && systemctl reload smbd
```
Danach das Fenster schließen.
**Rückweg:** `read only = no`, `systemctl reload smbd`. Gehört auch in den Rückweg R5 bzw. R7 – **vor** dem
Zurückstellen des PCs (Rückweg R5, Schritt 6), sonst kann der PC nicht mehr auf pve-big kopieren.
**Was du lernst:** Mehrere Schutzschichten – Marken, Rohdaten-Regel, nur-lesen-Freigabe – fangen jeweils einen
anderen Fehler ab.

---

## Im Alltag

- **Zeiten:** Clips kommen wie bisher kurz nach dem Match. 10:00 Abgleich (weckt pve-big nur mit neuen Dateien),
  11:00 Morgenprüfung. Zwischen 23:00 und 08:00 kommen keine Meldungen.
- **Nachtruhe:** Zwischen 22:00 und 08:00 (`[lager].nachtruhe_von/_bis`, Ortszeit) weckt der Abgleich pve-big nie –
  auch nicht, wenn der Mini nachts neu startet und der Timer den verpassten Abgleich nachholt. Die JSON-Zeile
  zeigt dann `"nachtruhe": true` (Exit 0, keine Meldung), `/status` „in der Nachtruhe übersprungen“; das Offene
  bleibt im Puffer und kommt beim nächsten Abgleich um 10:00 mit. Läuft pve-big ohnehin (z. B. weil du ihn
  eingeschaltet hast), wird auch nachts abgeglichen – das macht keinen zusätzlichen Lärm.
  `pipeline lager uebernehmen` (von Hand) kennt keine Nachtruhe. Abschalten: `nachtruhe_von = ""` in `lokal.toml`.
- **Nachsehen:** `pipeline lager status` · `pipeline puffer status` · im Bot `/status`
  (z. B. „Puffer 61 GB frei · Lager: 1840 GB frei, letzter Abgleich 10:07 ok · 0 offen“). Der Platz im Lager ist
  der beim letzten Abgleich gemessene – `/status` weckt pve-big dafür nie; ist er nicht von heute, steht „(Stand …)“
  dabei.
- **Meldungen** kommen höchstens einmal am Tag je Thema, montags ein Lebenszeichen – Stille heißt: alles gut.
- **Exit-Codes** von `pipeline lager …`: 0 ok (auch: in der Nachtruhe übersprungen) · 1 einzelne Dateien
  fehlgeschlagen (beim nächsten Abgleich wieder) · 2 Aufruf oder Konfiguration (z. B. Puffer und Lager verwechselbar
  – dann wurde **nichts** kopiert – oder eine ungültige Nachtruhe) · 3 Lager offline bzw. pve-big nicht wach
  geworden · 4 ein anderer Abgleich läuft.
- **Platz:** Der Puffer wird in dieser Stufe nie automatisch geleert. 96 GB reichen bei ca. 2,6 GB je Spieltag gut
  einen Monat; die Morgenprüfung warnt unter 20 GB frei. Bestätigte Rohdaten im Puffer freizugeben (Stufe B5) kommt
  später und nur mit deinem OK.
- **Platz im Lager:** Auch auf pve-big wird nie etwas gelöscht. Stattdessen misst jeder Abgleich, der pve-big
  braucht, den freien Platz im Lager (nur `statvfs` – kein Dateiinhalt, für clip-leerlauf kein Zugriff) und legt ihn
  in `lager_laeufe` ab. Die Morgenprüfung warnt unter 200 GB frei (`[puffer].lager_warnung_frei_gb`), Alarm unter
  50 GB (`lager_alarm_frei_gb`) – höchstens einmal am Tag, mit dem Tag der Messung. Nächster Schritt dann: Platz auf
  pve-big schaffen oder die Platte erweitern. Noch keine Messung (z. B. kurz nach R6): keine Meldung. Läuft das Lager
  doch voll, bricht der Abgleich ab, und alles bleibt im Puffer, bis wieder Platz ist.

## Dateien

| Datei | Wo | Wofür |
|---|---|---|
| `deploy/pve-mini/puffer-einrichten.sh` | pve-mini (Host) | R1 |
| `deploy/pve-mini/puffer-zurueck.sh` | pve-mini (Host) | Rückweg R1 |
| `deploy/pve-mini/clip-lvm-status` (+ `.service`, `.timer`) | pve-mini (Host) | Füllstand des Pools alle 15 min |
| `deploy/mini/samba-einrichten.sh`, `deploy/mini/smb-puffer.conf` | CT | R2 |
| `deploy/systemd/clip-lager.service` / `.timer` | CT | Abgleich 10:00 (R6) |
| `deploy/systemd/clip-puffer-pruefen.service` / `.timer` | CT | Morgenprüfung 11:00 (R6) |
