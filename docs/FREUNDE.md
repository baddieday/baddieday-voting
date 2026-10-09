# Deine eigenen Fortnite-Videos – so geht's

Florian hat dir auf seinem Rechner daheim eine eigene Ecke eingerichtet. Dort schneidet ein Programm aus deinen
Fortnite-Aufnahmen automatisch kurze Videos und schickt sie dir in Telegram. Deine Videos und Aufnahmen bekommt sonst
niemand zu sehen, auch nicht die anderen Freunde. (Florian betreibt den Rechner und könnte technisch hineinschauen.)

## Was du bekommst
- **Einen eigenen Telegram-Bot.** Den Einladungslink schickt dir Florian.
- **Nach jedem Spielabend ein Video** mit deinen besten Szenen: Multikills, Siege, knappe Endkämpfe – geschnitten, mit
  Musik und Effekten, im Hochformat für TikTok und YouTube Shorts.
- **Darunter zwei Knöpfe:** ✅ *Hochladen* – du bekommst das Video und einen fertigen Text zum Hochladen. ❌ *Nicht
  gut* – ein Tipp, was nicht passt (zu kurz, langweilig, zu wild, Musik), dann kommt eine neue Fassung.
- Reichen die starken Szenen eines Abends nicht, kommt „kein Video, weil …“ – lieber kein Video als ein langweiliges.
- **Es lernt dich kennen:** Aus deinen ✅ und ❌ merkt sich das Programm, wie dir deine Videos gefallen – nur deine.

## Was du einmal tust
1. Schick Florian deine **Epic-Konto-ID** (epicgames.com → Konto → Kontoinformationen, 32 Zeichen) – daran erkennt
   das Programm in den Replays, welche Kills deine sind.
2. Florian schickt dir einen **Einladungslink** zu deinem Bot. Tipp ihn an und drück in Telegram auf **Start** – der
   Bot antwortet „✅ Verbunden!“. Der Link gilt 15 Minuten und nur einmal; ist er abgelaufen, frag nach einem neuen.
3. In Fortnite: Einstellungen → Spiel → **Replays aufzeichnen: an**.
4. Nimm wie gewohnt mit der **Nvidia App** oder **SteelSeries Moments** auf.

## Wie deine Aufnahmen zu Florian kommen
Dein PC lädt sie von selbst hoch – in deinen eigenen Briefkasten auf Florians Server im Internet. Nur dein PC kann dort
etwas hineinlegen, nur Florians Rechner daheim holt es ab. Einmal einrichten (2–3 Minuten, Windows 10 oder 11):
1. Tipp in deinem Bot auf **/pc**. Er schickt dir eine Datei `ClipUpload-<dein Name>.zip`.
2. Speicher sie auf deinem PC, Rechtsklick → **Alle extrahieren**, dann im neuen Ordner **Freund-Einrichten.cmd**
   doppelklicken. Kommt „Der Computer wurde durch Windows geschützt“: **Weitere Informationen** → **Trotzdem
   ausführen**. Am Ende steht im Fenster „[OK] Verbunden mit Florians Briefkasten“.
3. In Fortnite **Replays aufzeichnen** anlassen. Kurz danach schreibt dein Bot „✅ Dein PC ist verbunden“.

Danach läuft alles von selbst, alle 2 Minuten, ohne Fenster:
- Das Programm nimmt nur Fortnite-Aufnahmen der **Nvidia App** und von **SteelSeries Moments** und deine Replays – ab dem Tag
  der Einrichtung (einen Tag zurück), nichts Älteres. Findet die Einrichtung im Videos-Ordner keine Aufnahmen, fragt
  sie einmal nach dem Ordner.
- Solange Fortnite läuft, lädt es langsam (2 Mbit/s), damit nichts ruckelt – danach mit voller Leitung.
- **Auf deinem PC wird nie etwas gelöscht.** Aufnahmen ohne Replay bleiben dort; dein Bot erinnert dich dann an die
  Replays.
- Kein Admin, keine Installation, kein offener Port. Fehlt der „OpenSSH-Client“ (selten), sagt dir die Einrichtung,
  wo du ihn einmal nachinstallierst (dafür braucht es Admin).
- **Neue Version:** im Bot wieder /pc und Freund-Einrichten.cmd noch einmal. **Ausschalten:** in der
  Aufgabenplanung die Aufgabe „Clip-Upload“ deaktivieren (oder in der Eingabeaufforderung im Ordner
  `Freund-Einrichten.cmd /entfernen`) – deine Dateien bleiben.
- In der ZIP-Datei steckt dein Schlüssel. Er kann nur in deinen Briefkasten legen, sonst nichts – trotzdem nicht
  weitergeben.

## Was danach passiert
Nichts mehr – einfach spielen. Das Video kommt etwa 45 Minuten nach deinem letzten Match, wenn der PC so lange an
bleibt. Machst du ihn gleich aus, kommt es, sobald er wieder an ist. Du drückst nur ✅ oder ❌. Wann sich dein PC
zuletzt gemeldet hat, steht unter 📋 Stand.

## Deine Aufnahmen bleiben gesichert
Hat Florian das Lager für dich eingeschaltet, kommen deine Aufnahmen und Replays einmal am Tag zusätzlich auf seinen
großen Speicher daheim – meist am Tag nach dem Spielen, wenn er dort ohnehin seine eigenen sichert. Dort wird nie
etwas gelöscht. Erst wenn die Kopie dort nachgeprüft ist und eine Aufnahme älter als 14 Tage ist, macht sie in deiner
Ecke auf dem Rechner, der deine Videos schneidet, Platz. Deine Videos und Clips bleiben. Du musst dafür nichts tun.

## Datenschutz
Deine Aufnahmen können euren Sprachchat enthalten. Sie liegen kurz in deinem Briefkasten auf Florians gemietetem
Server (unverschlüsselt beim Anbieter; bis Florian das Löschen dort freigibt, auch länger) und dauerhaft auf Florians
Rechnern daheim – mit Lager auch in deinem eigenen Ordner auf seinem großen Speicher, den er über sein Netzlaufwerk
sehen kann. Florian kann technisch hineinschauen, die anderen Freunde nicht.

## Claude verbinden (freiwillig)
Hast du ein eigenes Claude-Abo, kann das Programm damit zusätzlich jedes Video benoten und so schneller lernen. Das
kostet dein Abo, nicht das von Florian. Ohne geht alles genauso.
1. Tipp in deinem Bot auf **/claude** – er schickt dir einen Link.
2. Öffne ihn, melde dich mit deinem Claude-Konto an und kopier den Code, den du danach siehst.
3. Schick dem Bot den Code. Er löscht die Nachricht gleich wieder und antwortet „✅ Claude verbunden“.

Der Link gilt 10 Minuten. Klappt es nicht: nochmal /claude – oder auf deinem PC `claude setup-token` ausführen und dem
Bot das Token schicken (fängt mit sk-ant-oat01- an).
