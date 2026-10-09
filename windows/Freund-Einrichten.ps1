<#
.SYNOPSIS
  Richtet das Hochladen deiner Fortnite-Aufnahmen zu Florian ein – einmal, ohne Admin, ohne Installation.
  Am einfachsten: Freund-Einrichten.cmd doppelklicken (vorher das ZIP aus deinem Bot entpacken).

.DESCRIPTION
  - kopiert das Programm nach %LOCALAPPDATA%\ClipUpload und entsperrt die Dateien (Unblock-File)
  - gibt nur dir Rechte an deinem Schlüssel (sonst nimmt sftp.exe ihn nicht)
  - prüft, ob sftp.exe da ist (OpenSSH-Client, ab Werk dabei)
  - sucht Fortnite-Aufnahmen im Videos-Ordner; findet es keine, fragt es einmal nach dem Ordner
  - lädt nur Aufnahmen ab heute minus 24 Stunden hoch, nichts Älteres
  - prüft die Verbindung zu Florians Briefkasten
  - richtet die Aufgabe „Clip-Upload“ ein: alle 2 Minuten und bei der Anmeldung, als du, ohne Fenster
  Entfernen: Freund-Einrichten.cmd /entfernen – nur die Aufgabe, deine Dateien bleiben. Gelöscht wird nie etwas.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)][string]$Befehl,
    [string]$Ordner,            # Ordner mit Aufnahmen (statt suchen und fragen)
    [switch]$OhneAufgabe        # nur kopieren und prüfen, keine Aufgabe (Tests, Ausprobieren)
)

$ErrorActionPreference = 'Stop'
$AUFGABE = 'Clip-Upload'
$DATEIEN = @('Freund-Hochladen.ps1', 'Freund-Einrichten.ps1', 'Freund-Einrichten.cmd', 'freund.psd1', 'pc', 'known_hosts')
$basis = $env:LOCALAPPDATA
if (-not $basis) { $basis = [Environment]::GetFolderPath('LocalApplicationData') }
$ziel = Join-Path $basis 'ClipUpload'
$stand = Join-Path $ziel 'stand'
$windows = [Environment]::OSVersion.Platform -eq 'Win32NT'
$utf8 = New-Object System.Text.UTF8Encoding($false)

function Sag([string]$text) { Write-Host $text }

function Hat-Fortnite([string]$ordner) {
    if (-not $ordner -or -not (Test-Path -LiteralPath $ordner -PathType Container)) { return $false }
    $treffer = Get-ChildItem -LiteralPath $ordner -Filter '*.mp4' -File -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like 'Fortnite*' } | Select-Object -First 1
    return [bool]$treffer
}

function Frage-Ordner([string]$start) {
    # Einmal fragen (Auswahlfenster). Abbrechen = beim Videos-Ordner bleiben.
    try {
        Add-Type -AssemblyName System.Windows.Forms
        $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
        $dialog.Description = 'Wo speichert deine Aufnahme-App (Nvidia App, SteelSeries Moments) die Fortnite-Videos?'
        if ($start) { $dialog.SelectedPath = $start }
        if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { return $dialog.SelectedPath }
    } catch {
        Sag "   (Auswahlfenster ging nicht: $($_.Exception.Message))"
    }
    return $null
}

function Entferne-Aufgabe {
    if (-not (Get-Command Get-ScheduledTask -ErrorAction SilentlyContinue)) { Sag 'Aufgaben gibt es nur unter Windows.'; return 1 }
    if (Get-ScheduledTask -TaskName $AUFGABE -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $AUFGABE -Confirm:$false
        Sag "[OK] Aufgabe '$AUFGABE' entfernt. Deine Dateien bleiben in $ziel, deine Aufnahmen sowieso."
    } else {
        Sag "Die Aufgabe '$AUFGABE' gibt es nicht – nichts zu tun."
    }
    return 0
}

function Richte-Aufgabe-Ein {
    if (-not (Get-Command Register-ScheduledTask -ErrorAction SilentlyContinue)) {
        Sag '[!] Aufgaben gibt es nur unter Windows – nicht eingerichtet.'
        return $false
    }
    $skript = Join-Path $ziel 'Freund-Hochladen.ps1'
    $ps = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
    # Über „conhost --headless“ blitzt kein Fenster auf (ab Windows 10 1809), sonst -WindowStyle Hidden
    if ([Environment]::OSVersion.Version.Build -ge 17763) {
        $aktion = New-ScheduledTaskAction -Execute 'conhost.exe' `
            -Argument "--headless `"$ps`" -NoProfile -ExecutionPolicy Bypass -File `"$skript`""
    } else {
        $aktion = New-ScheduledTaskAction -Execute $ps `
            -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$skript`""
    }
    $alle2min = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 2)
    $einstellungen = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Hours 6) -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries
    $beschreibung = 'Lädt deine Fortnite-Aufnahmen in Florians Briefkasten (Clip-Pipeline). Entfernen: Freund-Einrichten.cmd /entfernen'
    try {
        $anmeldung = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
        Register-ScheduledTask -TaskName $AUFGABE -Action $aktion -Trigger @($alle2min, $anmeldung) `
            -Settings $einstellungen -Description $beschreibung -Force | Out-Null
    } catch {
        # Manche Windows lassen „bei der Anmeldung“ nur mit Admin zu – dann reicht „alle 2 Minuten“
        Register-ScheduledTask -TaskName $AUFGABE -Action $aktion -Trigger $alle2min `
            -Settings $einstellungen -Description $beschreibung -Force | Out-Null
    }
    return $true
}

if ($Befehl -and $Befehl -match '^[/-]?entfernen$') { exit (Entferne-Aufgabe) }
if ($Befehl) { Sag "Unbekannt: $Befehl (nur /entfernen)"; exit 2 }

Sag '== Clip-Upload einrichten =='
# 1 Vollständig entpackt?
$quelle = $PSScriptRoot
$fehlt = @($DATEIEN | Where-Object { -not (Test-Path -LiteralPath (Join-Path $quelle $_)) })
if ($fehlt.Count) {
    Sag "[!] Es fehlt: $($fehlt -join ', ')."
    Sag '    Erst das ZIP aus deinem Bot ganz entpacken (Rechtsklick → „Alle extrahieren“), dann hier noch einmal.'
    exit 1
}

# 2 sftp.exe (OpenSSH-Client)
$sftp = $null
if ($env:WINDIR -and (Test-Path -LiteralPath (Join-Path $env:WINDIR 'System32\OpenSSH\sftp.exe'))) {
    $sftp = Join-Path $env:WINDIR 'System32\OpenSSH\sftp.exe'
}
$konfig = Import-PowerShellDataFile -LiteralPath (Join-Path $quelle 'freund.psd1')
if ($konfig.Sftp) { $sftp = [string]$konfig.Sftp }
if (-not $sftp) {
    $c = Get-Command 'sftp.exe' -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) { $sftp = $c.Path }
}
if (-not $sftp) {
    Sag '[!] sftp.exe fehlt (OpenSSH-Client). Einmal nachinstallieren, das braucht Admin:'
    Sag '    Einstellungen → Apps → Optionale Features → Feature hinzufügen → „OpenSSH-Client“. Danach hier noch einmal.'
    exit 1
}
Sag "[OK] sftp.exe: $sftp"

# 3 Kopieren und entsperren (eine neue Version überschreibt nur das Programm, der Zustand in „stand“ bleibt)
New-Item -ItemType Directory -Force -Path $ziel, $stand | Out-Null
if ((Resolve-Path -LiteralPath $quelle).Path -ne (Resolve-Path -LiteralPath $ziel).Path) {
    foreach ($datei in $DATEIEN) { Copy-Item -LiteralPath (Join-Path $quelle $datei) -Destination (Join-Path $ziel $datei) -Force }
}
if ($windows) {   # die Markierung „aus dem Internet“ (SmartScreen) entfernen; PowerShell 7 unter Linux kann das nicht
    foreach ($datei in $DATEIEN) {
        try { Unblock-File -LiteralPath (Join-Path $ziel $datei) } catch { Sag "   ($datei nicht entsperrt: $($_.Exception.Message))" }
    }
}
Sag "[OK] Programm liegt in $ziel"

# 4 Schlüssel nur für dich (Win32-OpenSSH lehnt einen Schlüssel ab, den andere lesen dürfen)
if ($windows) {
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    foreach ($datei in 'pc', 'known_hosts') {
        $pfad = Join-Path $ziel $datei
        & icacls.exe $pfad /inheritance:r /grant:r "*$($sid):(F)" | Out-Null
        if ($LASTEXITCODE -ne 0) { Sag "[!] Rechte an $pfad ließen sich nicht setzen (icacls $LASTEXITCODE)." }
    }
    Sag '[OK] Schlüssel: nur du darfst ihn lesen'
}

# 5 Wo liegen deine Aufnahmen?
$ordnerDatei = Join-Path $stand 'ordner.txt'
$videos = [Environment]::GetFolderPath('MyVideos')
$gewaehlt = $null
if ($Ordner) {
    $gewaehlt = $Ordner
} else {
    $bisher = @()
    if (Test-Path -LiteralPath $ordnerDatei) { $bisher = @([IO.File]::ReadAllLines($ordnerDatei) | Where-Object { $_ }) }
    foreach ($o in @($bisher) + @($videos)) {
        if (Hat-Fortnite $o) { $gewaehlt = $o; break }
    }
    if (-not $gewaehlt) {
        Sag "   Im Ordner $videos finde ich noch keine Fortnite-Aufnahmen."
        $gewaehlt = Frage-Ordner $videos
        if (-not $gewaehlt) { $gewaehlt = $videos; Sag '   Ich nehme den Videos-Ordner (mit allen Unterordnern).' }
    }
}
[IO.File]::WriteAllLines($ordnerDatei, [string[]]@($gewaehlt))
Sag "[OK] Aufnahmen aus: $gewaehlt (mit Unterordnern) · Replays aus: $(Join-Path $basis 'FortniteGame\Saved\Demos')"

# 6 Stichtag: nur Aufnahmen ab jetzt minus 24 Stunden (bleibt bei einer neuen Version, wie er war)
$stichtagDatei = Join-Path $stand 'stichtag.txt'
if (-not (Test-Path -LiteralPath $stichtagDatei)) {
    $stichtag = (Get-Date).ToUniversalTime().AddHours(-24).ToString("yyyy-MM-dd'T'HH:mm:ss.fff'Z'", [Globalization.CultureInfo]::InvariantCulture)
    [IO.File]::WriteAllText($stichtagDatei, $stichtag, $utf8)
}
Sag "[OK] Hochgeladen wird ab $([IO.File]::ReadAllText($stichtagDatei).Trim()) (UTC) – nichts Älteres"

# 7 Verbindung prüfen (zeigt auch, was es gefunden hat; lädt noch nichts hoch)
Sag ''
Sag '== Verbindung zu Florians Briefkasten =='
& (Join-Path $ziel 'Freund-Hochladen.ps1') -Probe
$probe = $LASTEXITCODE
Sag ''
if ($probe -eq 0) { Sag '[OK] Verbunden mit Florians Briefkasten.' }
elseif ($probe -eq 4) { Sag '[OK] Das Hochladen läuft gerade schon – die Verbindung steht.' }
else {
    Sag '[!] Der Briefkasten antwortet nicht. Häufig: Firewall oder Router lassen Port 2222 nicht hinaus, das Internet'
    Sag '    ist gerade weg, oder Florian hat deinen Briefkasten noch nicht freigeschaltet – sag ihm Bescheid.'
    Sag "    Die Aufgabe richte ich trotzdem ein; sie versucht es alle 2 Minuten. Log: $(Join-Path $ziel 'hochladen.log')"
}

# 8 Aufgabe
if ($OhneAufgabe) { Sag '(Aufgabe nicht eingerichtet: -OhneAufgabe)'; exit 0 }
if (Richte-Aufgabe-Ein) {
    Sag "[OK] Aufgabe '$AUFGABE': alle 2 Minuten und bei der Anmeldung, ohne Fenster."
    Sag ''
    Sag 'Fertig. Schalte in Fortnite „Replays aufzeichnen“ an (Einstellungen → Spiel). Dein Bot schreibt dir, sobald'
    Sag 'dein PC verbunden ist. Lass den PC nach dem Spielen kurz an – dann kommt dein Video schneller.'
    exit 0
}
exit 1
