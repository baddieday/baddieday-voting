# Autonomes Lernen aus Publikumsergebnissen

Neue Entwürfe brauchen keine neuen Daumen, Battles oder Moment-Bewertungen. Alte Bewertungen bleiben erhalten.
Die Veröffentlichung wählst du weiterhin selbst; das Lernen danach läuft automatisch.

## Bedienung

1. Im Lern-Bot **🎬 Short** oder **🎞️ Zusammenschnitt** wählen.
2. Direkt **📦 Upload-Paket** öffnen. Bewerten ist optional. **Nächster Entwurf** funktioniert ebenfalls ohne Urteil.
3. Das Video veröffentlichen und im Lern-Bot `/link 41 https://…` senden; `41` ist die Entwurfsnummer.
4. Eingerichtete Plattformzugänge holen Performance automatisch. Jede verwertbare neue Messung stößt Lernen an.
5. `/lernstand` zeigt veröffentlichte und ausgewertete Videos, aktiven Lernstand, Vertrauen und Tendenzen.

Auch `pipeline highlight --id … --tage 14` verwendet für neue Ausgaben den gemeinsamen Regisseur. Es entsteht
ein verknüpfter Zusammenschnitt-Entwurf mit denselben Längengrenzen und Lernmerkmalen. Der Clip-Bot nennt dessen
Nummer für `/link` im Lern-Bot. Das allgemeine **Hochgeladen**-Häkchen kennt keine Plattform und erzeugt deshalb
keinen erfundenen Plattform-Post. Bestehende Highlight-Dateien bleiben unverändert.

**Längen:** Short 30–75 Sekunden, ohne Publikumsevidenz mindestens 45 Sekunden als Planungsziel;
Zusammenschnitt 75–120 Sekunden. Planung und vollständiger Render prüfen diese Grenzen. Fehlt genug Material,
kommt eine verständliche Fehlermeldung statt eines zu kurzen veröffentlichten Videos. Technische Teilvorschauen
sind keine veröffentlichbaren vollständigen Videos.

## Automatische Zahlen einrichten

Der vorhandene tägliche Dienst `clip-publikum` ruft `pipeline publikum bewerten` auf. Der Befehl holt verfügbare
API-Daten, aktualisiert die neue Lernschleife und erhält die ältere Wochen-Auswertung. Ohne eingerichteten
Zugang erfolgen keine API-Aufrufe und es werden keine Messungen erfunden. Unter `[publikum]` steuern
`api_abruf` (Standard `true`), `api_max_tage` (180), `api_intervall_stunden` (6) und `api_max_posts` (100) den Abruf.
Posts ohne Score kommen zuerst dran (die jüngsten vorn), danach die bewerteten – so bekommt jedes neue Video seine
tägliche Messung bis zum Score an Tag 7, auch wenn mehr als `api_max_posts` Posts im Fenster liegen (08.10., N43).
Der 6-Stunden-Wert begrenzt wiederholte Abrufe; er ersetzt nicht den täglichen systemd-Zeitplan.

| Plattform | Zugang | Tatsächlich automatisch verfügbar |
|---|---|---|
| TikTok | `TIKTOK_ACCESS_TOKEN`; für Erneuerung `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET`, `TIKTOK_REFRESH_TOKEN` | Display API: Views, Likes, Kommentare, Shares. Watchtime, Completion und Saves bleiben unbekannt. |
| YouTube | `YOUTUBE_ACCESS_TOKEN`; für Erneuerung `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REFRESH_TOKEN` | YouTube Analytics: Views, Likes, Kommentare, Shares, mittlere Wiedergabedauer/-anteil, gewonnene Abonnenten. Autorisierter Kanal und `yt-analytics.readonly` nötig. |
| Instagram | JSON-Import | Gemeinsames Metrikformat und Insights-Zähler. Kein automatischer Instagram-Abruf implementiert. |

Tokens gehören in die lokale Dienstumgebung bzw. `.env`, siehe `.env.example`. Die initiale OAuth-Freigabe des
eigenen Accounts muss vorhanden sein; das Programm stellt keine Entwickler-App bereit. Rotierte TikTok-Tokens
werden atomar in `publikum-oauth.json` neben der Datenbank gespeichert (unter Linux Modus `0600`, Git ignoriert
die Datei). Plattformen ohne erneuerbaren Zugang benötigen nach Ablauf einen neuen Zugang. Fehler eines Zugangs
stoppen die anderen Plattformen nicht; die JSON-Ausgabe zählt `ohne_zugang`, `ohne_id` und `fehler` ausdrücklich.

Optional Zahlen importieren:

```sh
pipeline publikum importieren --post 17 --datei performance.json
```

Die Post-Nummer steht in `/publikum`. Beispiel für das gemeinsame Format; nur tatsächlich bekannte Felder senden:

```json
{"views":8000,"wiedergabe_s":55.25,"retention_prozent":85,"likes":430,"shares":95,"saves":70}
```

Plattformnative Feldnamen werden ebenfalls normalisiert. Ein unbekannter Wert bleibt `null` oder fehlt;
YouTubes durchschnittlicher Wiedergabeanteil wird als Retention gespeichert, nicht als Completion.
Screenshots und bestehende Texteingaben bleiben möglich. Dafür ist keine Video-Bewertung nötig.

## Was gelernt wird

Der `audience_success_score` priorisiert Watchtime/Retention, Completion, Shares und Saves. Reine Views reichen
nicht für einen Qualitätsscore; Reichweite trägt höchstens 8 Prozent bei. Verglichen wird möglichst mit älteren
Posts desselben Accounts und derselben Plattform bei ähnlichem Alter und ähnlicher Dauer. Ohne brauchbare
Vergleichsbasis gelten transparente Startwerte, keine angeblichen Plattform-Benchmarks. Kleine Stichproben und
unvollständige Metriken senken das Vertrauen. Impressions, wenn vorhanden, ersetzen die reine kumulierte Reichweite.

Eine gewichtete lineare Regression mit begrenzten Gewichten reicht für die ersten Videos. Sehr gute Qualität
erhält bis zu 1,5-faches Trainingsgewicht; Millionen Views schaffen keine unbegrenzte Wirkung. Eine gemessene
Verbindung ist eine **Tendenz, kein Kausalitätsnachweis**. Neue Ergebnisse werden stärker gewichtet: Der aktuelle
Anteil halbiert sich nach 60 Tagen, ein Viertel bleibt als Langzeitanteil. Long-Term und Recent Score bleiben
gesondert gespeichert.

Jede neue Informationslage erzeugt einen versionierten Challenger. Erst ein zeitlicher Vergleich mit bisher
ungesehenen Videogruppen kann ihn zum Champion machen. Mindestens drei Trainingsvideos plus neue Prüfvideos
sind nötig. Derselbe Entwurf auf mehreren Plattformen sowie wiederholte Messungen werden als eine Videogruppe
behandelt. Ein schlechterer Challenger ersetzt den Champion nicht. Wiederholter Import derselben Zahlen erzeugt
kein zusätzliches Trainingsbeispiel.

Historische manuelle Präferenzen werden als Startwissen benutzt. Mit wachsender Publikumsevidenz sinkt ihr
relativer Einfluss; historische Auswahlboni werden auf das Gewicht von insgesamt zwei Pseudo-Videos begrenzt.
Sobald Publikumsdaten ausgewertet sind, blockiert der frühere Daumen-Vorfilter keine neuen Entwürfe mehr.

Der aktive Lerner verändert Dauer, Schnitttempo, Einstieg, Effektdichte, Musikpegel und die Gewichtung von
Momentmerkmalen. Jeder siebte Entwurf untersucht genau eine unsichere Stellschraube zusätzlich zur aktuellen
Strategie. Hypothese, Variable, Erwartung, Ergebnis und Vertrauen davor/danach werden gespeichert. Das Publikum
bewertet diesen Versuch; eine manuelle A/B-Runde ist nicht nötig. Weitere gemessene Eigenschaften bleiben für
spätere Modelle verfügbar; nicht jede beobachtete Eigenschaft wird bereits einzeln vom Planer optimiert.

## Daten und technische Anschlüsse

| Ort | Inhalt |
|---|---|
| `posts`, `publikum_messungen` | Veröffentlichungen und unveränderte Messungshistorie; optionale zusätzliche Metriken |
| `video_lerndaten` | Eingefrorene Schnittliste mit Quellen, Reihenfolge, Szenen, Hook, Musik, Ton, Effekten, Rezept, Features und erzeugender Modellversion |
| `audience_ergebnisse` | Verwendete Messung, Success-Score, Komponenten, Datenfingerabdruck und Vertrauen |
| `lernstaende` | Version, Vorgänger, verwendete Videogruppen, Gewichte, Validierung, Champion/Challenger und zeitliche Scores |
| `lern_experimente` | Hypothese, Variable, Erwartung, tatsächliches Ergebnis und Vertrauen |

`publikum_adapter.py` hält Plattformzugänge und Normalisierung vom Scoring getrennt. `audience_score.py` berechnet
Qualität, `lern_features.py` extrahiert strukturierte Features, `autonom.py` trainiert und versioniert. Der
bestehende `regie_lernen.aktuelle`-Aufruf bindet den Champion in die Planung ein. Alte Datensätze werden ergänzt,
nicht gelöscht; fehlende historische Schnittlisten bleiben ausdrücklich unvollständig. Es gibt kein versprochenes
virales Ergebnis, sondern nachvollziehbare Anpassungen an gemessene Publikumsreaktionen.

API-Referenzen: [TikTok Video Query](https://developers.tiktok.com/docs/en/tiktok-api-v2-video-query),
[TikTok OAuth](https://developers.tiktok.com/docs/en/oauth-user-access-token-management),
[YouTube Analytics](https://developers.google.com/youtube/analytics/reference/reports/query),
[YouTube-Metriken](https://developers.google.com/youtube/analytics/metrics).

## Entwurf-Bewertungen lehren die Moment-Formel (06.10.)

Bis zum 06.10. lernte die Formel, die Momente auswählt (`lernen.py`), nur aus Clip-Bot-Freigaben, Battles und
Publikum. Dein 👍/👎 im Lern-Bot wirkte nur auf den Schnitt und auf genau die gezeigten Momente
(`moment_bonus`). Neue Momente wurden also immer mit derselben Formel gewählt.

Neu ist die vierte Paar-Quelle `entwurf` (`lernen.entwurf_paare`):
- **Stimme je Moment:** Für jeden Moment wird die Summe deiner Urteile über alle Entwürfe gebildet, in denen er war:
  - 👍 +1,
  - 👎 ohne Grund −0,5,
  - 👎 nur mit Schnitt-Gründen 0 (dazu zählt seit 07.10. auch „🥱 langweilig“ – bis dahin −1).
- **Paare:** Momente mit Plus stehen über Momenten mit Minus, die deutlichsten zuerst. Die Paare zählen halb wie eine Freigabe (`[lernen].gewicht_entwurf`).
- **Sicherungen:** Es gelten dieselben wie bisher (Mindestmenge, Leine, nie schlechter als die Startgewichte).

`🔎 /warum` zeigt, wie viele Paare daraus entstehen und was die Formel gelernt hat.
