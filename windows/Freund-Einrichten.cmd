@echo off
rem Clip-Upload einrichten: einmal doppelklicken (kein Admin noetig). Vorher das ZIP aus deinem Bot entpacken.
rem Entfernen (nur die Aufgabe, deine Dateien bleiben): Freund-Einrichten.cmd /entfernen
if not exist "%~dp0Freund-Einrichten.ps1" (
  echo Bitte zuerst entpacken: Rechtsklick auf die ZIP-Datei, "Alle extrahieren", dann hier noch einmal.
  pause
  exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Freund-Einrichten.ps1" %*
set ERGEBNIS=%ERRORLEVEL%
echo.
pause
exit /b %ERGEBNIS%
