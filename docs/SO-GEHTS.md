# So geht's (Stand 07.10.2026)

## Was von selbst passiert
1. Du zockst. Kommt 45 Minuten lang kein neues Match dazu, gilt der Abend als vorbei (am PC musst du nichts
   einstellen), und der Lern-Bot schreibt: „🎮 Abend vom … erkannt – ich baue dein Video.“
2. Etwas später kommt das Video. Es besteht nur aus **starken Szenen** dieses Abends: Multikills, Clutches,
   Kills im Endkampf, Victory Royale. Die Statuszeile verschwindet dann.
3. Gab es zu wenig starke Szenen (ein Video braucht 4 und mindestens 30 Sekunden), kommt **kein Video**. Die
   Statuszeile sagt dann, warum.

## Was du tust
- **✅ Hochladen** – du bekommst das Video in voller Qualität und den Text zum Hochladen.
- **❌ Nicht gut** – tipp auf einen Grund. Der Bot sagt dir sofort, was er ändert, und baut eine neue Fassung:

| Grund | Was sich ändert (für immer, bis du es selbst änderst – außer bei 🥱 und 🔁) |
|---|---|
| ⏱️ Zu kurz | Shorts werden 10 Sekunden länger (höchstens 75 s) |
| ⏳ Zu lang | Shorts werden 10 Sekunden kürzer (mindestens 30 s) |
| 🥱 Langweilig | Nur die neue Fassung: anders geschnitten (anderer Aufbau, anderes Tempo, anderer Song). Die bessere Hälfte der Szenen bleibt, die schwächere tauscht er gegen Szenen, die du noch nicht gesehen hast – erst vom selben Abend, dann starke von früheren Abenden. Gibt es keine, kommt kein Video. Gesperrt wird nichts. |
| 🎵 Musik | Dieser Song kommt nie wieder |
| 😵 Zu hektisch | Effekte eine Stufe ruhiger (wild → normal → ruhig → aus) |
| 🔁 Einfach neu | Andere Fassung, gleiche Regeln |

Baut der Bot gerade noch ein Video, tipp den Grund kurz danach nochmal. Beim Highlight-Video heißt ❌ nur:
dieses Video lässt er weg – deine Short-Regeln ändert es nicht.

## Was der Bot selbst lernt
Bei jedem Video stellt er drei Dinge selbst ein und merkt sich, was ankommt:
- **Aufbau:** schnelle Montage, erzählt, Steigerung oder Kino
- **Tempo:** schnelle oder ruhige Schnitte
- **Zeitlupe:** viel oder wenig

Er lernt aus deinen ✅/❌ und aus der Note einer KI, die sich jedes Video nach dem Senden anschaut (zählt ein Drittel
so viel wie du). Dein Grund unter ❌ zählt mit: „🎵 Musik“ oder „⏱️ Zu kurz“ werden nicht dem Aufbau angelastet.
Bei jedem zweiten Video probiert er bewusst etwas Neues (ändern: ⚙️ → 🔧 → 🧪 Ausprobieren). **Deine Regeln gehen
immer vor.** Sonntags ab 18 Uhr kommt eine kurze Zusammenfassung: was gut ankommt, was weniger, was er probiert hat.

## Knöpfe
- **🎬 Neues Video** – jederzeit von Hand, aus deinem neuesten Spielabend (ändern: ⚙️ → 🎯 Clips).
- **📋 Stand** – deine Regeln und was beim letzten Abend passiert ist.
- **⚙️ Einstellungen** – Short-Länge, Szenen (nur starke oder auch Einzelkills), Effekte, Musik.
- **/experte** – alle alten Knöpfe und Details ein- oder ausschalten.

## Ruhe im Chat
Der Clip-Bot schickt keine einzelnen Szenen mehr. Er meldet sich nur bei Problemen (zum Beispiel Speicher voll) und
mit dem Highlight-Video. Nach einem Update schreibt der Lern-Bot einmal „✅ Neue Version läuft“.

Kommt pro Match noch „🎬 Match verarbeitet … Jetzt im Bot bewerten“? Diese Nachricht schickt n8n. Abschalten:
in n8n den Ablauf „1 Match verarbeiten“ öffnen, den Kasten „Telegram Info“ anklicken, **D** drücken (deaktivieren),
speichern. Die Datei `1-match-verarbeiten.json` im Repo hat das schon.
