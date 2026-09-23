Lies zuerst CLAUDE.md vollständig und halte dich an den Abschnitt „Wie wir zusammenarbeiten“.

Das ist Phase 1: Analyse und Plan. **Noch kein Code, keine Installationen.** Die einzige Datei, die du anlegen darfst, ist PLAN.md.

1. **clips_voter studieren**
   Das Repo liegt unter ./clips_voter (falls nicht: sag mir Bescheid, ich klone es). Erkläre mir in einfachen Worten:
   - Zweck und grobe Architektur, Tech-Stack, Datenmodell
   - wie Battles, Voting und Elo genau funktionieren (Formel, K-Faktor, Saisons)
   - welche Teile sich für einen Telegram-Port direkt wiederverwenden lassen und welche nicht
   - ob das die Codebasis von clip-battle.de ist und ob es schon eine Anbindung zum Einreichen von Clips gibt

2. **Telegram-Port entwerfen** (Bot per Polling, läuft später auf dem Heimserver)
   - Neuer Clip: verkleinerte Vorschau, Vorbewertung (Punkte, Kills, Multikill), Buttons „Freigeben“ / „Verwerfen“
   - Battle-Modus: zwei meiner Clips, ich wähle den besseren → Elo wie in clips_voter
   - Wie Freigaben und Battles zum Lernsignal für die Vorbewertung werden
   - Wie Bot, n8n und Datenbank zusammenspielen, ohne dass zwei Stellen Telegram-Updates abholen
   - Welche Sprache/Bibliothek, passend zu clips_voter

3. **Vorbewertung entwerfen**
   Merkmale, Startgewichte und Multikill-Fenster wie in CLAUDE.md. Schlag ein einfaches, nachvollziehbares Lernverfahren vor, mit Mindestzahl an Bewertungen und einer Anzeige der aktuellen Gewichte in Telegram.

4. **Kill-Daten prüfen**
   Recherchiere, welcher Fortnite-Replay-Parser aktuell gepflegt wird und Eliminierungen mit Zeitstempel liefert. Beschreibe, wie wir Replay-Zeit und Videozeit abgleichen, und was wir tun, wenn der Parser nach einem Update streikt.

5. **Caption-Generator entwerfen**
   Beschreibung nur aus vorhandenen Daten, Hashtag-Vorlage aus Datei, Werbung für clip-battle.de als Text und optional als Overlay/Endcard im Video.

6. **PLAN.md schreiben**
   Gliedere nach den Stufen aus CLAUDE.md. Pro Stufe: Ziel, betroffene Dateien, wie ich teste, was ich dabei lerne. Jede Stufe muss allein nutzbar sein. Wenn eine Stufe zu groß ist, teile sie auf.

7. **Rückfragen**
   Liste höchstens 5 Rückfragen, nach Wichtigkeit sortiert.

Danach **stopp** und warte auf mein OK, bevor du irgendetwas baust.
