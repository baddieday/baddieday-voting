<#
.SYNOPSIS
  Richtet die Windows-Aufgabe ein, die Uebertragung.ps1 alle 2 Minuten startet.
  Einmal selbst ausführen (normale PowerShell, kein Admin nötig):
    powershell -ExecutionPolicy Bypass -File .\Aufgabe-einrichten.ps1
  Entfernen:  Unregister-ScheduledTask -TaskName 'Clip-Pipeline Übertragung'
#>
param([int]$Minuten = 2)

$skript = Join-Path $PSScriptRoot 'Uebertragung.ps1'
if (-not (Test-Path (Join-Path $PSScriptRoot 'uebertragung.psd1'))) {
    throw 'Erst uebertragung.beispiel.psd1 nach uebertragung.psd1 kopieren und anpassen.'
}
# Über "conhost --headless" starten: powershell -WindowStyle Hidden blitzt trotzdem kurz als Fenster auf.
$aktion = New-ScheduledTaskAction -Execute 'conhost.exe' `
    -Argument "--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$skript`""
$ausloeser = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes $Minuten)
$einstellungen = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 6) -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries
Register-ScheduledTask -TaskName 'Clip-Pipeline Übertragung' -Action $aktion -Trigger $ausloeser `
    -Settings $einstellungen -Description 'Kopiert Fortnite-Clips und Replays auf den großen Proxmox-Host' -Force
Write-Host "Aufgabe eingerichtet: alle $Minuten Minuten. Log: $env:LOCALAPPDATA\ClipPipeline\uebertragung.log"
