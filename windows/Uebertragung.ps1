<#
.SYNOPSIS
  Kopiert neue Fortnite-Aufnahmen und Replays vom Gaming-PC auf den großen Proxmox-Host.

.DESCRIPTION
  - Überträgt nur fertige Dateien (seit RuhezeitSekunden unverändert).
  - Schreibt zuerst "<name>.teil" und benennt erst nach geprüfter Größe um:
    die Pipeline sieht nie eine halbe Datei.
  - Weckt den großen Host per Wake-on-LAN, wenn er schläft – aber NUR, wenn es etwas zu kopieren gibt (oder eine
    Session-Datei fällig ist). Sonst weckte der Lauf alle 2 min pve-big, obwohl der sich nach Leerlauf abschaltet.
  - Merkt sich übertragene Dateien lokal – schnell auch bei tausenden Clips.
  - Löscht auf dem Gaming-PC nur mit -Verschieben und nur nach SHA-256-Vergleich.
    Replays werden nie gelöscht (NurKopieren).
  - Optional (SessionVorbeiMinuten > 0): Läuft Fortnite nicht mehr und kam seit N Minuten kein Match dazu,
    schreibt es EINMAL "sitzungen\session_<zeit>.json" mit den Match-IDs des Abends auf den Speicher.
    Der Mini holt die Datei ab (pipeline sitzungen) und baut daraus Stimmung + Entwurf. n8n bleibt unverändert.

.EXAMPLE
  .\Uebertragung.ps1 -Probelauf      # zeigt nur, was kopiert würde
  .\Uebertragung.ps1                 # kopiert
#>
[CmdletBinding()]
param(
    [string]$Konfig,
    [switch]$Verschieben,
    [switch]$Probelauf
)

$ErrorActionPreference = 'Stop'
# Standardpfad erst hier auflösen: unter Windows PowerShell 5.1 ist $PSScriptRoot in param()-Standardwerten leer,
# wenn das Skript mit [CmdletBinding()] per "powershell -File" (Aufgabenplanung) gestartet wird.
if (-not $Konfig) { $Konfig = Join-Path $PSScriptRoot 'uebertragung.psd1' }
$datenOrdner = Join-Path $env:LOCALAPPDATA 'ClipPipeline'
$logDatei = Join-Path $datenOrdner 'uebertragung.log'
$statusDatei = Join-Path $datenOrdner 'uebertragen.tsv'
$meldeDatei = Join-Path $datenOrdner 'zu-melden.txt'
$sessionDatei = Join-Path $datenOrdner 'session.txt'   # Match-IDs seit dem letzten "Session vorbei"
New-Item -ItemType Directory -Force -Path $datenOrdner | Out-Null

function Log([string]$text) {
    $zeile = '{0:yyyy-MM-dd HH:mm:ss}  {1}' -f (Get-Date), $text
    Write-Host $zeile
    if ((Test-Path $logDatei) -and (Get-Item $logDatei).Length -gt 5MB) {
        Move-Item $logDatei "$logDatei.alt" -Force   # einfache Log-Rotation
    }
    Add-Content -Path $logDatei -Value $zeile -Encoding UTF8
}

function Port-Offen([string]$rechner, [int]$port, [int]$ms = 1500) {
    # Schneller als Test-Path auf einen schlafenden Server (das hängt sonst ~20 s)
    $client = New-Object System.Net.Sockets.TcpClient
    try { return $client.ConnectAsync($rechner, $port).Wait($ms) -and $client.Connected }
    catch { return $false }
    finally { $client.Dispose() }
}

function Sende-WakeOnLan([string]$mac) {
    $bytes = $mac -split '[:-]' | ForEach-Object { [Convert]::ToByte($_, 16) }
    if ($bytes.Count -ne 6) { throw "MAC-Adresse '$mac' ungültig" }
    # Magisches Paket: 6x 0xFF, danach 16x die MAC-Adresse
    $paket = [byte[]](@(0xFF) * 6 + ($bytes * 16))
    $udp = New-Object System.Net.Sockets.UdpClient
    try {
        $udp.EnableBroadcast = $true
        [void]$udp.Send($paket, $paket.Length, [System.Net.IPEndPoint]::new([System.Net.IPAddress]::Broadcast, 9))
    } finally { $udp.Dispose() }
}

function Melde-Offene($k, $zuMelden) {
    # Match-Enden an n8n melden; was nicht klappt, bleibt in zu-melden.txt für den nächsten Lauf
    if ($k.WebhookUrl -and $zuMelden.Count -and -not $Probelauf) {
        $offen = @($zuMelden | Select-Object -Unique | Where-Object { -not (Melde-Session $_ $k) })
        [IO.File]::WriteAllLines($meldeDatei, [string[]]$offen)
    }
}

function Datei-Schluessel($datei) { '{0}|{1}|{2}' -f $datei.FullName, $datei.Length, $datei.LastWriteTimeUtc.Ticks }

function Datei-Frei([string]$pfad) {
    # Hält noch ein Programm die Datei zum Schreiben offen (Fortnite während des Matches, Rekorder beim Speichern)?
    # Auf den Zeitstempel allein ist kein Verlass: Für offene Dateien zeigt Windows oft einen veralteten an.
    # Öffnen mit FileShare 'Read' klappt nur, wenn gerade niemand schreibt.
    try { $strom = [IO.File]::Open($pfad, 'Open', 'Read', 'Read'); $strom.Dispose(); return $true }
    catch { return $false }
}

function Session-Id([string]$name) {
    # UnsavedReplay-2026.09.23-20.15.33.replay -> 2026-09-23_20-15-33 (wie in der Pipeline)
    if ($name -match '^UnsavedReplay-(\d{4})\.(\d{2})\.(\d{2})-(\d{2})\.(\d{2})\.(\d{2})\.replay$') {
        return '{0}-{1}-{2}_{3}-{4}-{5}' -f $Matches[1], $Matches[2], $Matches[3], $Matches[4], $Matches[5], $Matches[6]
    }
    $stamm = [IO.Path]::GetFileNameWithoutExtension($name) -replace '[^A-Za-z0-9_-]+', '_'
    return $stamm.Substring(0, [Math]::Min(60, $stamm.Length))
}

function Melde-Session([string]$sid, $k) {
    # Vertrag mit n8n: POST /webhook/match-vorbei, Header X-Pipeline-Token, Body {"session": "<ID>"}
    if ($sid -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$') { Log "Ungültige Session-ID '$sid' – nicht gemeldet"; return $true }
    $token = if ($k.WebhookToken) { $k.WebhookToken } else { $env:CLIP_PIPELINE_TOKEN }
    try {
        Invoke-RestMethod -Method Post -Uri $k.WebhookUrl -TimeoutSec 15 -ContentType 'application/json' `
            -Headers @{ 'X-Pipeline-Token' = $token } -Body (@{ session = $sid } | ConvertTo-Json -Compress) | Out-Null
        Log "Match-Ende gemeldet: $sid"
        return $true
    } catch {
        Log "Meldung an n8n fehlgeschlagen ($sid): $($_.Exception.Message) – neuer Versuch beim nächsten Lauf"
        return $false
    }
}

function Session-Faellig($k) {
    # Session vorbei = Fortnite läuft nicht UND seit SessionVorbeiMinuten ist kein Match dazugekommen.
    if ([int]$k.SessionVorbeiMinuten -le 0 -or -not (Test-Path $sessionDatei)) { return $false }
    $ids = @(Get-Content $sessionDatei -Encoding UTF8 | Where-Object { $_ } | Select-Object -Unique)
    if (-not $ids.Count) { return $false }
    if (Get-Process -Name 'FortniteClient-Win64-Shipping' -ErrorAction SilentlyContinue) { return $false }
    $ruhe = (Get-Date) - (Get-Item $sessionDatei).LastWriteTime
    return $ruhe.TotalMinutes -ge [int]$k.SessionVorbeiMinuten
}

function Pruefe-SessionVorbei($k) {
    if (-not (Session-Faellig $k)) { return }
    $ids = @(Get-Content $sessionDatei -Encoding UTF8 | Where-Object { $_ } | Select-Object -Unique)
    $name = 'session_{0:yyyy-MM-dd_HH-mm-ss}' -f (Get-Date)
    $ordner = Join-Path $k.Ziel 'sitzungen'
    New-Item -ItemType Directory -Force -Path $ordner | Out-Null
    $inhalt = [ordered]@{ session = $name; matches = $ids; ende_utc = (Get-Date).ToUniversalTime().ToString('o') } |
        ConvertTo-Json -Compress
    $ziel = Join-Path $ordner "$name.json"
    Set-Content -LiteralPath "$ziel.teil" -Value $inhalt -Encoding UTF8   # erst fertig schreiben, dann umbenennen
    Move-Item -LiteralPath "$ziel.teil" -Destination $ziel -Force
    Remove-Item -LiteralPath $sessionDatei
    Log "Session vorbei: $name ($($ids.Count) Matches)"
}

# --- Nur ein Lauf gleichzeitig ---------------------------------------------------
$mutex = New-Object System.Threading.Mutex($false, 'Local\ClipPipelineUebertragung')
if (-not $mutex.WaitOne(0)) { Write-Host 'Übertragung läuft bereits.'; exit 0 }

try {
    if (-not (Test-Path $Konfig)) { throw "Konfiguration fehlt: $Konfig (Vorlage: uebertragung.beispiel.psd1)" }
    $k = Import-PowerShellDataFile $Konfig

    # --- Was wurde schon übertragen? ----------------------------------------------
    $erledigt = New-Object 'System.Collections.Generic.HashSet[string]'
    if (Test-Path $statusDatei) { Get-Content $statusDatei -Encoding UTF8 | ForEach-Object { [void]$erledigt.Add($_) } }

    $grenzeAlt = (Get-Date).AddDays(-[int]$k.MaxAlterTage)
    $zaehler = @{ kopiert = 0; geloescht = 0; fehler = 0; bytes = 0 }
    $zuMelden = New-Object 'System.Collections.Generic.List[string]'
    if (Test-Path $meldeDatei) { Get-Content $meldeDatei -Encoding UTF8 | Where-Object { $_ } | ForEach-Object { $zuMelden.Add($_) } }

    # --- Erst hier auf dem PC klären, ob es etwas zu tun gibt (weckt nichts) ------------
    $arbeit = New-Object 'System.Collections.Generic.List[object]'
    foreach ($q in $k.Quellen) {
        $quelle = [Environment]::ExpandEnvironmentVariables($q.Pfad)
        if (-not (Test-Path $quelle)) { Log "Quelle fehlt: $quelle"; continue }
        $ruhe = if ($q.RuhezeitSekunden) { [int]$q.RuhezeitSekunden } else { [int]$k.RuhezeitSekunden }
        $grenzeJung = (Get-Date).AddSeconds(-$ruhe)
        $rekursiv = [bool]$q.Unterordner
        $dateien = foreach ($muster in $q.Muster) { Get-ChildItem -Path $quelle -Filter $muster -File -Recurse:$rekursiv }

        foreach ($datei in ($dateien | Sort-Object LastWriteTime)) {
            if ($datei.LastWriteTime -gt $grenzeJung -or $datei.LastWriteTime -lt $grenzeAlt) { continue }
            if ($erledigt.Contains((Datei-Schluessel $datei))) { continue }
            if (-not (Datei-Frei $datei.FullName)) { continue }   # wird noch geschrieben -> nächster Lauf
            $arbeit.Add(@{ q = $q; quelle = $quelle; datei = $datei })
        }
    }
    if (-not $arbeit.Count -and -not (Session-Faellig $k)) {
        Melde-Offene $k $zuMelden   # braucht nur n8n, nicht den Speicher
        exit 0                      # nichts zu kopieren: pve-big NICHT wecken
    }

    # --- Ziel erreichbar? Sonst wecken -------------------------------------------
    if ($k.ZielHost -and -not (Port-Offen $k.ZielHost 445)) {
        if (-not $k.WakeOnLanMac) { Log "Ziel $($k.ZielHost) nicht erreichbar, kein Wake-on-LAN konfiguriert."; exit 3 }
        Log "Ziel schläft – sende Wake-on-LAN an $($k.WakeOnLanMac)"
        if (-not $Probelauf) { Sende-WakeOnLan $k.WakeOnLanMac }
        $bis = (Get-Date).AddSeconds([int]$k.WeckenWarteSekunden)
        while (-not (Port-Offen $k.ZielHost 445) -and (Get-Date) -lt $bis) { Start-Sleep -Seconds 5 }
        if (-not (Port-Offen $k.ZielHost 445)) { Log 'Ziel ist nicht aufgewacht – nächster Versuch beim nächsten Lauf.'; exit 3 }
    }
    if (-not (Test-Path (Join-Path $k.Ziel '.clip-speicher'))) {
        throw "Im Ziel $($k.Ziel) fehlt die Datei .clip-speicher – falsches Laufwerk oder nicht verbunden?"
    }

    # --- Kopieren --------------------------------------------------------------------
    foreach ($eintrag in $arbeit) {
        $q, $quelle, $datei = $eintrag.q, $eintrag.quelle, $eintrag.datei
        $schluessel = Datei-Schluessel $datei
        $unterpfad = $datei.FullName.Substring($quelle.TrimEnd('\').Length).TrimStart('\')
        $ziel = Join-Path (Join-Path $k.Ziel $q.Ziel) $unterpfad
        if ($Probelauf) { Log "würde kopieren: $($datei.FullName) -> $ziel"; continue }
        try {
            New-Item -ItemType Directory -Force -Path (Split-Path $ziel) | Out-Null
            if (-not ((Test-Path $ziel) -and (Get-Item $ziel).Length -eq $datei.Length)) {
                Copy-Item -LiteralPath $datei.FullName -Destination "$ziel.teil" -Force
                if ((Get-Item -LiteralPath "$ziel.teil").Length -ne $datei.Length) { throw 'Größe stimmt nicht' }
                Move-Item -LiteralPath "$ziel.teil" -Destination $ziel -Force
                (Get-Item -LiteralPath $ziel).LastWriteTimeUtc = $datei.LastWriteTimeUtc
                $zaehler.kopiert++; $zaehler.bytes += $datei.Length
            }
            if ($Verschieben -and -not $q.NurKopieren) {
                $a = (Get-FileHash -LiteralPath $datei.FullName -Algorithm SHA256).Hash
                $b = (Get-FileHash -LiteralPath $ziel -Algorithm SHA256).Hash
                if ($a -ne $b) { throw 'SHA-256 unterschiedlich – Quelle bleibt erhalten' }
                Remove-Item -LiteralPath $datei.FullName
                $zaehler.geloescht++
            }
            Add-Content -Path $statusDatei -Value $schluessel -Encoding UTF8
            [void]$erledigt.Add($schluessel)
            if ($q.Melden) {
                $zuMelden.Add((Session-Id $datei.Name))
                if ([int]$k.SessionVorbeiMinuten -gt 0) { Add-Content -Path $sessionDatei -Value (Session-Id $datei.Name) -Encoding UTF8 }
            }
        } catch {
            $zaehler.fehler++
            Log "FEHLER bei $($datei.Name): $($_.Exception.Message)"
            Remove-Item -LiteralPath "$ziel.teil" -ErrorAction SilentlyContinue
        }
    }
    # Match-Enden an n8n melden (erst jetzt: alle Aufnahmen dieses Laufs liegen schon auf dem Server)
    Melde-Offene $k $zuMelden
    if ([int]$k.SessionVorbeiMinuten -gt 0 -and -not $Probelauf) { Pruefe-SessionVorbei $k }
    if ($zaehler.kopiert -or $zaehler.fehler -or $zaehler.geloescht) {
        Log ('kopiert {0} ({1:N0} MB), gelöscht {2}, Fehler {3}' -f $zaehler.kopiert, ($zaehler.bytes / 1MB), $zaehler.geloescht, $zaehler.fehler)
    }
    if ($zaehler.fehler) { exit 1 }
}
catch {
    Log "ABBRUCH: $($_.Exception.Message)"
    exit 2
}
finally {
    $mutex.ReleaseMutex()
    $mutex.Dispose()
}
