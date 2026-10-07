# So geht's (Stand 07.10.2026)

## Was von selbst passiert
1. Du zockst. Wenn dein PC meldet, dass der Abend vorbei ist, schreibt der Lern-Bot:
   „🎮 Abend vom … erkannt – ich baue dein Video.“
2. Etwas später kommt das Video. Es besteht nur aus **starken Szenen** dieses Abends: Multikills, Clutches,
   Kills im Endkampf, Victory Royale. Die Statuszeile verschwindet dann.
3. Gab es zu wenig starke Szenen (ein Video braucht 4), kommt **kein Video**. Die Statuszeile sagt dann, warum.

## Was du tust
- **✅ Hochladen** – du bekommst das Video in voller Qualität und den Text zum Hochladen.
- **❌ Nicht gut** – tipp auf einen Grund. Der Bot sagt dir sofort, was er ändert, und baut eine neue Fassung:

| Grund | Was sich ändert (für immer, bis du es selbst änderst) |
|---|---|
| ⏱️ Zu kurz | Shorts werden 10 Sekunden länger (höchstens 75 s) |
| ⏳ Zu lang | Shorts werden 10 Sekunden kürzer (mindestens 30 s) |
| 🥱 Langweilig | Die schwächere Hälfte der Szenen dieses Videos kommt nie wieder |
| 🎵 Musik | Dieser Song kommt nie wieder |
| 😵 Zu hektisch | Effekte eine Stufe ruhiger (wild → normal → ruhig → aus) |
| 🔁 Einfach neu | Andere Fassung, gleiche Regeln |

## Knöpfe
- **🎬 Neues Video** – jederzeit von Hand.
- **📋 Stand** – deine Regeln und was beim letzten Abend passiert ist.
- **⚙️ Einstellungen** – Short-Länge, Szenen (nur starke oder auch Einzelkills), Effekte, Musik.
- **/experte** – alle alten Knöpfe und Details ein- oder ausschalten.

## Ruhe im Chat
Der Clip-Bot schickt keine einzelnen Szenen mehr. Er meldet sich nur bei Problemen (zum Beispiel Speicher voll) und
mit dem Highlight-Video. Nach einem Update schreibt der Lern-Bot einmal „✅ Neue Version läuft“.

Kommt pro Match noch „🎬 Match verarbeitet … Jetzt im Bot bewerten“? Diese Nachricht schickt n8n. Abschalten:
in n8n den Ablauf „1 Match verarbeiten“ öffnen, den Kasten „Telegram Info“ anklicken, **D** drücken (deaktivieren),
speichern. Die Datei `1-match-verarbeiten.json` im Repo hat das schon.
