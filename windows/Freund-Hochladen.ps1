<#
.SYNOPSIS
  Lädt die Fortnite-Aufnahmen und Replays dieses PCs in den Briefkasten bei Florian hoch (für Freunde,
  Clip-Pipeline 4.0, docs/FREUNDE.md). Läuft alle 2 Minuten als Aufgabe „Clip-Upload“ (Freund-Einrichten.cmd).

.DESCRIPTION
  - Nimmt nur fertige Dateien: Aufnahmen 60 s unverändert, Replays 5 min – und nicht mehr zum Schreiben offen
    (Datei-Frei wie in Uebertragung.ps1). Nur Namen, die Florians Mini kennt (Nvidia App, SteelSeries Moments,
    UnsavedReplay), nur druckbares ASCII ohne [ ] * ? " ' \ und erst ab der Einrichtung minus 24 Stunden.
  - Je Datei: erst „<name>.teil“ (put, nach einem Abbruch reput = weiter ab der Stelle), dann umbenennen, danach der
    Lieferschein „<name>.lieferschein“ (Name, Größe, SHA-256, Zeit). Erst mit dem Lieferschein holt der Mini sie ab.
  - Ein Replay erst, wenn alle Aufnahmen bis 5 min nach seinem Match oben sind (höchstens 6 h warten). Aufnahmen ohne
    Replay im Abstand von 24 h bleiben auf dem PC – der Status meldet sie („Replays an?“).
  - Solange Fortnite läuft: langsam (DrosselBeimSpielenKbit, 0 = Pause). Ändert sich das, endet der Upload; der
    nächste Lauf setzt ihn mit der neuen Geschwindigkeit fort.
  - 45 min nach dem letzten Match, wenn alles bis dahin oben ist: die Abend-Datei sitzungen/session_<ID>.json mit
    {session, matches, ende_utc = Ende des letzten Matches} – als letzte Datei. Mehr als 2 h Pause trennt zwei Abende.
  - status/pc-status.json: was wartet, was übersprungen ist, Zeitzone, Fortnite an (bei Änderung, sonst alle 10 min).
  - Den Ruhezustand verhindert es nur, solange ein Upload läuft. Es löscht nie etwas – hier nicht, im Briefkasten nicht.
  Exit: 0 ok (auch: nichts zu tun) · 1 einzelne Dateien gescheitert · 2 Abbruch · 3 Briefkasten nicht erreichbar,
  nicht eingehängt oder voll (alles bleibt hier) · 4 nur -Probe: läuft gerade schon.

.EXAMPLE
  .\Freund-Hochladen.ps1 -Probe      # zeigt nur, was hochgeladen würde, und prüft die Verbindung
#>
[CmdletBinding()]
param(
    [string]$Konfig,
    [switch]$Probe
)

$ErrorActionPreference = 'Stop'
$VERSION = '2026-10-09'
$RUHE_VIDEO_S = 60
$RUHE_REPLAY_S = 300
$FENSTER_NACH_MATCH_S = 300     # Aufnahmen bis 5 min nach dem Match-Ende gehören noch zu seinem Replay
$REPLAY_WARTET_H = 6            # so lange wartet ein Replay (und die Abend-Datei) höchstens auf offene Aufnahmen
$OHNE_REPLAY_H = 24
$ABEND_VORBEI_MIN = 45
$ABEND_PAUSE_H = 2
$STATUS_MIN = 10
# Umbenennen immer auf die alte Art (rename -l): Sie überschreibt nie ein fertiges Ziel und geht mit jedem Briefkasten.
# Ohne -l nimmt sftp posix-rename, sobald der Server es anbietet - OpenSSH vor 8.6 (Ubuntu 20.04, Debian 11) bietet es
# trotz Erlaubnisliste an und verweigert es dann: Keine Datei würde fertig, obwohl -Probe grün ist (M135).
$UMBENENNEN = 'rename -l'
$FORTNITE = 'FortniteClient-Win64-Shipping'
$NVIDIA = '^(?<spiel>.+?) (?<datum>\d{4}\.\d{2}\.\d{2}) - (?<zeit>\d{2}\.\d{2}\.\d{2})\.(?<zaehler>\d+)(?:\.(?!DVR\.)(?<ereignis>[^.]+))?(?<dvr>\.DVR)?\.mp4$'
$STEELSERIES = '^(?<spiel>.+?)__(?<datum>\d{4}-\d{2}-\d{2})__(?<zeit>\d{2}-\d{2}-\d{2})[^.]*\.mp4$'
$REPLAY = '^UnsavedReplay-(?<datum>\d{4}\.\d{2}\.\d{2})-(?<zeit>\d{2}\.\d{2}\.\d{2})\.replay$'
$UTC_FORMAT = "yyyy-MM-dd'T'HH:mm:ss.fff'Z'"

# Standardpfad erst hier: unter Windows PowerShell 5.1 ist $PSScriptRoot in param()-Standardwerten leer (Aufgabe)
if (-not $Konfig) { $Konfig = Join-Path $PSScriptRoot 'freund.psd1' }
$basis = $env:LOCALAPPDATA
if (-not $basis) { $basis = [Environment]::GetFolderPath('LocalApplicationData') }
$programmOrdner = Join-Path $basis 'ClipUpload'
$daten = Join-Path $programmOrdner 'stand'          # Zustand: bleibt, wenn eine neue Version eingerichtet wird
$ablage = Join-Path $daten 'versand'                # Lieferscheine, Abend-Datei, Status – nur kleine Dateien
$logDatei = Join-Path $programmOrdner 'hochladen.log'
$erledigtDatei = Join-Path $daten 'erledigt.tsv'    # Pfad|Größe|Ticks je Datei, die mit Lieferschein oben ist
$shaDatei = Join-Path $daten 'sha.tsv'              # Schlüssel<TAB>SHA-256 (je Datei nur einmal rechnen)
$teilDatei = Join-Path $daten 'teil.tsv'            # ordner/name<TAB>Schlüssel: zu welcher Datei ein .teil gehört
$matchDatei = Join-Path $daten 'matches.tsv'        # Match-ID<TAB>Ende (UTC) je hochgeladenem Replay
$sitzungDatei = Join-Path $daten 'sitzungen.tsv'    # Abend-Datei<TAB>Match-IDs – diese Matches kommen nie wieder
$stichtagDatei = Join-Path $daten 'stichtag.txt'    # nur Dateien ab hier (Einrichtung minus 24 h)
$ordnerDatei = Join-Path $daten 'ordner.txt'        # Ordner mit Aufnahmen (setzt die Einrichtung)
$statusMerk = Join-Path $daten 'status.txt'         # wann und was zuletzt als Status ging
$befehlDatei = Join-Path $daten 'sftp-befehle.txt'
$utf8 = New-Object System.Text.UTF8Encoding($false)
$UTC_STIL = [Globalization.DateTimeStyles]::AssumeUniversal -bor [Globalization.DateTimeStyles]::AdjustToUniversal
$windows = [Environment]::OSVersion.Platform -eq 'Win32NT'
New-Item -ItemType Directory -Force -Path $daten, $ablage | Out-Null

function Log([string]$text) {
    $zeile = '{0:yyyy-MM-dd HH:mm:ss}  {1}' -f (Get-Date), $text
    Write-Host $zeile
    try {
        if ((Test-Path -LiteralPath $logDatei) -and (Get-Item -LiteralPath $logDatei).Length -gt 5MB) {
            Move-Item -LiteralPath $logDatei "$logDatei.alt" -Force   # einfache Log-Rotation
        }
        [IO.File]::AppendAllText($logDatei, $zeile + [Environment]::NewLine, $utf8)
    } catch { Write-Warning -Message "Log nicht geschrieben: $($_.Exception.Message)" }
}

# „Jetzt“ nur über Get-Date (ohne Parameter) – die Tests stellen so die Uhr
function Jetzt-Utc { (Get-Date).ToUniversalTime() }

function Utc-Text([datetime]$zeit) {
    # wie iso() in der Pipeline: 2026-09-24T20:15:33.123Z
    $zeit.ToUniversalTime().ToString($UTC_FORMAT, [Globalization.CultureInfo]::InvariantCulture)
}

function Aus-Utc-Text([string]$text) {
    [DateTime]::ParseExact($text, $UTC_FORMAT, [Globalization.CultureInfo]::InvariantCulture, $UTC_STIL)
}

function Unix-Ms([datetime]$zeitUtc) {
    [long][Math]::Floor(($zeitUtc.ToUniversalTime() - (New-Object DateTime(1970, 1, 1, 0, 0, 0, [DateTimeKind]::Utc))).TotalMilliseconds)
}

function Versatz-Min([datetime]$zeitUtc) { [int][TimeZoneInfo]::Local.GetUtcOffset($zeitUtc.ToUniversalTime()).TotalMinutes }

function Lies-Zeilen([string]$datei) {
    if (-not (Test-Path -LiteralPath $datei)) { return @() }
    return @([IO.File]::ReadAllLines($datei) | Where-Object { $_ })
}

function Notiere([string]$datei, [string]$zeile) {
    # Zeile sofort auf die Platte (UTF-8 ohne BOM) – ein Abbruch danach verliert nichts
    if (-not $Probe) { [IO.File]::AppendAllLines($datei, [string[]]@($zeile)) }
}

function Lies-Zuordnung([string]$datei) {
    # Schlüssel<TAB>Wert je Zeile, die letzte Zeile gewinnt
    $m = @{}
    foreach ($z in (Lies-Zeilen $datei)) {
        $i = $z.LastIndexOf("`t")
        if ($i -gt 0) { $m[$z.Substring(0, $i)] = $z.Substring($i + 1) }
    }
    return $m
}

function Datei-Schluessel($datei) { '{0}|{1}|{2}' -f $datei.FullName, $datei.Length, $datei.LastWriteTimeUtc.Ticks }

function Datei-Frei([string]$pfad) {
    # Hält noch ein Programm die Datei zum Schreiben offen (Fortnite im Match, ein Rekorder beim Speichern)? Auf den
    # Zeitstempel allein ist kein Verlass. Öffnen mit FileShare 'Read' klappt nur, wenn gerade niemand schreibt.
    try { $strom = [IO.File]::Open($pfad, 'Open', 'Read', 'Read'); $strom.Dispose(); return $true }
    catch { return $false }
}

function Name-Ok([string]$name) {
    # wie briefkasten.name_ok auf dem Mini: druckbares ASCII, kein Punkt vorn, keine [ ] * ? " ' \ /, höchstens 200
    if ($name.Length -lt 1 -or $name.Length -gt 200 -or $name.StartsWith('.')) { return $false }
    foreach ($z in $name.ToCharArray()) {
        if ([int]$z -lt 32 -or [int]$z -gt 126) { return $false }
    }
    return $name.IndexOfAny([char[]]'[]*?"''\/') -lt 0
}

function Datum-Ok([string]$text, [string]$format) {
    $d = [datetime]::MinValue
    return [datetime]::TryParseExact($text, $format, [Globalization.CultureInfo]::InvariantCulture,
        [Globalization.DateTimeStyles]::None, [ref]$d)
}

function Art-Video([string]$name) {
    # 'ok' = eine Aufnahme, die der Mini kennt (quellen.erkenne) · 'name' = sieht nach Fortnite aus, aber der Name passt
    # nicht (Umlaute, Klammern …) · $null = etwas anderes (andere Spiele, PNG)
    if ($name -notlike '*.mp4') { return $null }
    $fortnite = $name -like 'fortnite*'
    if (-not (Name-Ok $name)) { if ($fortnite) { return 'name' } else { return $null } }
    foreach ($muster in @(@{ re = $NVIDIA; format = 'yyyy.MM.dd HH.mm.ss' }, @{ re = $STEELSERIES; format = 'yyyy-MM-dd HH-mm-ss' })) {
        if ($name -match $muster.re) {
            $m = $Matches
            if (-not $m['spiel'].Trim().ToLowerInvariant().StartsWith('fortnite')) { return $null }
            if (Datum-Ok "$($m['datum']) $($m['zeit'])" $muster.format) { return 'ok' }
            return 'name'   # ein Datum, das es nicht gibt – daran stürzte scan auf dem Mini sonst ab
        }
    }
    if ($fortnite) { return 'name' }
    return $null
}

function Art-Replay([string]$name) {
    if ($name -notlike '*.replay') { return $null }
    if ((Name-Ok $name) -and $name -match $REPLAY) {
        if (Datum-Ok "$($Matches['datum']) $($Matches['zeit'])" 'yyyy.MM.dd HH.mm.ss') { return 'ok' }
        return 'name'
    }
    if ($name -like 'UnsavedReplay*') { return 'name' }
    return $null
}

function Match-Id([string]$name) {
    # UnsavedReplay-2026.09.23-20.15.33.replay -> 2026-09-23_20-15-33 (wie in der Pipeline)
    $null = $name -match '^UnsavedReplay-(\d{4})\.(\d{2})\.(\d{2})-(\d{2})\.(\d{2})\.(\d{2})\.replay$'
    return '{0}-{1}-{2}_{3}-{4}-{5}' -f $Matches[1], $Matches[2], $Matches[3], $Matches[4], $Matches[5], $Matches[6]
}

function Fortnite-Laeuft { [bool](Get-Process -Name $FORTNITE -ErrorAction SilentlyContinue) }

function Strom-Halten([bool]$an) {
    # Ruhezustand nur während eines Uploads verhindern (SetThreadExecutionState); Herunterfahren geht immer
    if (-not $windows) { return }
    if (-not $an -and -not ('ClipUpload.Strom' -as [type])) { return }   # nie gesetzt: nichts zurückzusetzen (spart Add-Type)
    try {
        if (-not ('ClipUpload.Strom' -as [type])) {
            Add-Type -Namespace ClipUpload -Name Strom -MemberDefinition `
                '[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint esFlags);'
        }
        # ES_CONTINUOUS | ES_SYSTEM_REQUIRED bzw. nur ES_CONTINUOUS (= wieder frei)
        if ($an) { $flags = [uint32]2147483649 } else { $flags = [uint32]2147483648 }
        [void][ClipUpload.Strom]::SetThreadExecutionState($flags)
    } catch { Log "Ruhezustand nicht beeinflusst: $($_.Exception.Message)" }
}

function Als-Argument([string]$a) {
    # Ein Argument für die Befehlszeile von sftp.exe (Regeln wie CommandLineToArgvW)
    if ($a.Length -gt 0 -and $a -notmatch '[\s"]') { return $a }
    $sb = New-Object System.Text.StringBuilder
    [void]$sb.Append('"')
    $schraeg = 0
    foreach ($z in $a.ToCharArray()) {
        if ($z -eq [char]'\') { $schraeg++; continue }
        if ($z -eq [char]'"') { [void]$sb.Append('\' * (2 * $schraeg + 1)); [void]$sb.Append('"'); $schraeg = 0; continue }
        if ($schraeg) { [void]$sb.Append('\' * $schraeg); $schraeg = 0 }
        [void]$sb.Append($z)
    }
    [void]$sb.Append('\' * (2 * $schraeg))
    [void]$sb.Append('"')
    return $sb.ToString()
}

function Ssh-Pfad([string]$pfad) {
    # Für -o IdentityFile=…: in Anführungszeichen (Leerzeichen im Benutzernamen), „/“ statt „\“, % verdoppelt
    return '"' + (($pfad -replace '\\', '/') -replace '%', '%%') + '"'
}

function Letzte([string]$text) {
    # die letzten zwei Zeilen von stderr – nach „Connection closed“ steht davor der Grund
    $zeilen = @((($text -replace "`r", "`n") -split "`n") | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    if (-not $zeilen.Count) { return 'ohne Meldung' }
    $teil = ($zeilen[([Math]::Max(0, $zeilen.Count - 2))..($zeilen.Count - 1)]) -join ' – '
    if ($teil.Length -gt 300) { $teil = $teil.Substring(0, 300) }
    return $teil
}

function Sftp-Aufruf([string[]]$befehle, [string]$ordner, [int]$kbit, [int]$fristS, [switch]$Beobachten) {
    # Ein sftp-Lauf mit diesen Befehlen, Arbeitsordner = ordner: Lokale Dateien stehen nur mit ihrem (ASCII-)Namen in
    # den Befehlen – so stört kein Umlaut im Benutzerordner. -Beobachten: endet Fortnite oder startet es, wird sftp
    # beendet (neue Geschwindigkeit beim nächsten Lauf). fristS > 0: danach wird sftp beendet.
    [IO.File]::WriteAllText($befehlDatei, (($befehle -join "`n") + "`n"), $utf8)
    $argumente = @('-F', 'none', '-b', $befehlDatei, '-P', [string]$script:port)
    if ($kbit -gt 0) { $argumente += @('-l', [string]$kbit) }
    $argumente += $script:sshOptionen
    $argumente += "$($script:benutzer)@$($script:adresse)"
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $script:sftp
    $psi.Arguments = (@($argumente | ForEach-Object { Als-Argument $_ }) -join ' ')
    $psi.WorkingDirectory = $ordner
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true
    $p = [System.Diagnostics.Process]::Start($psi)
    $aus = $p.StandardOutput.ReadToEndAsync()
    $err = $p.StandardError.ReadToEndAsync()
    $uhr = [Diagnostics.Stopwatch]::StartNew()
    $gewechselt = $false
    $beendet = $false
    while (-not $p.WaitForExit(2000)) {
        if ($Beobachten -and ((Fortnite-Laeuft) -ne $script:fortnite)) { $gewechselt = $true }
        if ($gewechselt -or ($fristS -gt 0 -and $uhr.Elapsed.TotalSeconds -gt $fristS)) {
            $beendet = $true
            try { $p.Kill() } catch { }
            break
        }
    }
    [void]$p.WaitForExit(10000)
    $code = 255
    if (-not $beendet) { $code = $p.ExitCode }
    $ausText = ''
    $errText = ''
    if ($aus.Wait(10000)) { $ausText = $aus.Result }
    if ($err.Wait(10000)) { $errText = $err.Result }
    if ($beendet -and -not $gewechselt) { $errText += "`nZeit abgelaufen" }
    $p.Dispose()
    return @{ code = $code; aus = $ausText; fehler = $errText; gewechselt = $gewechselt }
}

function Abschnitte([string]$ausgabe, [string[]]$befehle) {
    # stdout je Befehl: sftp schreibt vor jeden Befehl „sftp> <befehl>“. Nur die als nächste erwartete Zeile beginnt
    # einen neuen Abschnitt – ein Dateiname kann so keinen vortäuschen (wie briefkasten._abschnitte auf dem Mini).
    # erreicht = Nummer des letzten begonnenen Befehls (-1: keiner).
    $teile = @{}
    $i = -1
    foreach ($zeile in ($ausgabe -split "`n")) {
        $zeile = $zeile.TrimEnd("`r")
        if ($i + 1 -lt $befehle.Count -and $zeile -ceq "sftp> $($befehle[$i + 1])") {
            $i++
            $teile[$i] = New-Object 'System.Collections.Generic.List[string]'
        } elseif ($i -ge 0) {
            $teile[$i].Add($zeile)
        }
    }
    return @{ teile = $teile; erreicht = $i }
}

function Liste-Fach {
    # Ein Lauf, nur lesen: Marke (eingehängt?), Füllstand (df, KiB), dann die Namen in videos/ replays/ sitzungen/
    $befehle = @('ls -1 -a /fach', '-df', '-ls -1 videos', '-ls -1 replays', '-ls -1 sitzungen')
    $fach = @{ erreicht = $false; fehler = ''; groesse = $null; frei = $null; prozent = $null; namen = @{} }
    foreach ($o in 'videos', 'replays', 'sitzungen') { $fach.namen[$o] = New-Object 'System.Collections.Generic.HashSet[string]' }
    $r = Sftp-Aufruf $befehle $daten 0 300
    if ($r.code -ne 0) { $fach.fehler = "Briefkasten nicht erreichbar ($(Letzte $r.fehler))"; return $fach }
    $a = Abschnitte $r.aus $befehle
    if ($a.erreicht -lt 0 -or -not $a.teile[0].Contains('/fach/.clip-briefkasten')) {
        $fach.fehler = 'Fach im Briefkasten nicht eingehängt (.clip-briefkasten fehlt) – sag Florian Bescheid'
        return $fach
    }
    $df = @()
    if ($a.teile.ContainsKey(1)) { $df = $a.teile[1].ToArray() }
    for ($i = 0; $i -lt $df.Count - 1; $i++) {
        if ($df[$i] -match '^\s*Size\s+Used\s+Avail\s+\(root\)\s+%Capacity\s*$' -and
            $df[$i + 1] -match '^\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+\S+\s*$') {
            $fach.groesse = [long]$Matches[1] * 1024
            $fach.frei = [long]$Matches[3] * 1024
            if ([long]$Matches[1] -gt 0) { $fach.prozent = [int]([long]$Matches[2] * 100 / [long]$Matches[1]) }
            break
        }
    }
    for ($j = 0; $j -lt 3; $j++) {
        $o = @('videos', 'replays', 'sitzungen')[$j]
        if (-not $a.teile.ContainsKey($j + 2)) { continue }
        foreach ($zeile in $a.teile[$j + 2]) {
            if ($zeile.StartsWith("$o/")) {
                $n = $zeile.Substring($o.Length + 1)
                if ($n -and -not $n.Contains('/')) { [void]$fach.namen[$o].Add($n) }
            }
        }
    }
    $fach.erreicht = $true
    return $fach
}

function Fehler-Merken([string]$text) {
    $script:fehler++
    $script:letzterFehler = $text
    Log "FEHLER: $text"
}

function Sha($e) {
    if ($script:shaCache.ContainsKey($e.schluessel)) { return $script:shaCache[$e.schluessel] }
    $sha = (Get-FileHash -LiteralPath $e.datei.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    $script:shaCache[$e.schluessel] = $sha
    Notiere $shaDatei "$($e.schluessel)`t$sha"
    return $sha
}

function Schreibe-Lieferschein([string]$name, [long]$groesse, [string]$sha, [datetime]$zeitUtc) {
    # JSON wie briefkasten.pruefe_lieferschein es erwartet (höchstens 4 KB): name, groesse, sha256, mtime_ms, utc_offset_min
    $inhalt = [ordered]@{ name = $name; groesse = $groesse; sha256 = $sha; mtime_ms = (Unix-Ms $zeitUtc)
                          utc_offset_min = (Versatz-Min $zeitUtc) } | ConvertTo-Json -Compress
    # Lokal immer dieselbe Datei (versand/lieferschein.json) – es sammelt sich nichts an
    [IO.File]::WriteAllText((Join-Path $ablage 'lieferschein.json'), $inhalt, $utf8)
}

function Lieferschein-Hoch([string]$o, [string]$n) {
    # Lieferschein über .teil und Umbenennen (ein halber Lieferschein wird beim nächsten Mal einfach neu geschrieben)
    $befehle = @("put ""lieferschein.json"" ""$o/$n.lieferschein.teil""",
                 "$UMBENENNEN ""$o/$n.lieferschein.teil"" ""$o/$n.lieferschein""")
    return Sftp-Aufruf $befehle $ablage 0 300
}

function Merke-Erledigt($e, [string]$wie) {
    # Erst die Match-ID, dann der Eintrag in erledigt.tsv: Ein Abbruch dazwischen verliert kein Match (Stolperfalle 14)
    if ($e.art -eq 'replay') {
        Notiere $matchDatei "$($e.id)`t$(Utc-Text $e.zeit)"
        $script:matchEnde[$e.id] = Utc-Text $e.zeit
    }
    Notiere $erledigtDatei $e.schluessel
    [void]$script:erledigt.Add($e.schluessel)
    $e.status = 'erledigt'
    Log "$wie`: $($e.ordner)/$($e.name)"
}

function Lade-Hoch($e) {
    # Eine Datei: 'ok' (mit Lieferschein oben) · 'fehler' (nächster Lauf) · 'stopp' (Verbindung weg, Fach voll,
    # Fortnite gestartet/beendet – in diesem Lauf nichts mehr)
    $o = $e.ordner
    $n = $e.name
    $namen = $script:fach.namen[$o]
    if ($namen.Contains($n) -and $namen.Contains("$n.lieferschein")) { Merke-Erledigt $e 'schon im Briefkasten'; return 'ok' }
    $teilSchluessel = "$o/$n"
    # Oben liegt der Name schon, aber ohne Lieferschein und nicht genau diese Fassung (der Rekorder hat die Datei nach dem
    # Hochladen geändert, ein direktes Hochladen brach ab oder der Stand hier ist neu): Der Mini fasst ohne Lieferschein
    # nichts an – also die aktuelle Fassung direkt auf den Namen schreiben (Umbenennen kann kein Ziel ersetzen). „~“ vor
    # dem Schlüssel heißt: Das direkte Hochladen läuft noch – bis es fertig ist, gilt die Datei oben als unvollständig.
    $direkt = $namen.Contains($n) -and $script:teile[$teilSchluessel] -ne $e.schluessel
    if (-not $namen.Contains($n) -or $direkt) {
        if (-not $direkt -and $null -ne $script:fach.frei -and $e.datei.Length + 1MB -gt $script:fach.frei) {
            $script:fachVoll = $true
            Fehler-Merken "Briefkasten voll ($n braucht $([Math]::Ceiling($e.datei.Length / 1MB)) MB) – die Aufnahmen bleiben hier, bis wieder Platz ist"
            return 'stopp'
        }
        $sha = Sha $e
        $befehl = 'put'
        if ($direkt) {
            Notiere $teilDatei "$teilSchluessel`t~$($e.schluessel)"
            $script:teile[$teilSchluessel] = "~$($e.schluessel)"
            $befehle = @("put ""$n"" ""$o/$n""")
        } else {
            if ($namen.Contains("$n.teil") -and $script:teile[$teilSchluessel] -eq $e.schluessel) { $befehl = 'reput' }
            Notiere $teilDatei "$teilSchluessel`t$($e.schluessel)"
            $script:teile[$teilSchluessel] = $e.schluessel
            $befehle = @("$befehl ""$n"" ""$o/$n.teil""", "$UMBENENNEN ""$o/$n.teil"" ""$o/$n""")
        }
        Log "lade hoch ($befehl, $([Math]::Round($e.datei.Length / 1MB, 1)) MB$(if ($script:kbit) { ", höchstens $($script:kbit) kbit/s" })): $o/$n"
        Strom-Halten $true
        $r = Sftp-Aufruf $befehle $e.verzeichnis $script:kbit 0 -Beobachten
        if ($r.gewechselt) { Log 'Fortnite gestartet oder beendet – der Upload geht beim nächsten Lauf mit der neuen Geschwindigkeit weiter'; return 'stopp' }
        if ($r.code -ne 0) {
            $a = Abschnitte $r.aus $befehle
            if ($befehl -eq 'reput' -and $a.erreicht -eq 0 -and $r.code -eq 1 -and $r.fehler -match 'same size or larger|bigger or same size') {
                # Der .teil ist schon ganz oben (Abbruch genau vor dem Umbenennen): nur noch umbenennen
                $r = Sftp-Aufruf @($befehle[1]) $daten 0 300
            } elseif ($befehl -eq 'reput' -and $a.erreicht -eq 0 -and $r.code -eq 1 -and $r.fehler -match 'No such file') {
                Notiere $teilDatei "$teilSchluessel`t-"   # der .teil ist weg: beim nächsten Mal von vorn
                $script:teile[$teilSchluessel] = '-'
            }
        }
        if ($r.code -eq 255) {
            $script:verbindungWeg = $true
            Fehler-Merken "Verbindung abgebrochen bei $n ($(Letzte $r.fehler)) – geht beim nächsten Lauf weiter"
            return 'stopp'
        }
        if ($r.code -ne 0) {
            if ($r.fehler -match 'write remote .*Failure') {
                $script:fachVoll = $true
                Fehler-Merken "Briefkasten voll bei $n – die Aufnahmen bleiben hier, bis wieder Platz ist"
                return 'stopp'
            }
            Fehler-Merken "$n`: $(Letzte $r.fehler)"
            return 'fehler'
        }
        if ($direkt) {
            # Fertig oben – ab jetzt gilt die Datei dort als vollständig (der Lieferschein folgt gleich)
            Notiere $teilDatei "$teilSchluessel`t$($e.schluessel)"
            $script:teile[$teilSchluessel] = $e.schluessel
            Log "oben lag eine andere Fassung ohne Lieferschein – durch die aktuelle ersetzt: $o/$n"
        }
        [void]$namen.Add($n)
        $script:bytes += $e.datei.Length
        if ($null -ne $script:fach.frei -and -not $direkt) { $script:fach.frei -= $e.datei.Length }
    }
    $neu = Get-Item -LiteralPath $e.datei.FullName -ErrorAction SilentlyContinue
    if ($null -eq $neu -or (Datei-Schluessel $neu) -ne $e.schluessel) {
        Fehler-Merken "$n hat sich beim Hochladen geändert – kein Lieferschein"
        return 'fehler'
    }
    Schreibe-Lieferschein $n $e.datei.Length (Sha $e) $e.zeit
    $r = Lieferschein-Hoch $o $n
    if ($r.code -eq 255) {
        $script:verbindungWeg = $true
        Fehler-Merken "Verbindung abgebrochen beim Lieferschein von $n ($(Letzte $r.fehler))"
        return 'stopp'
    }
    if ($r.code -ne 0) { Fehler-Merken "Lieferschein von $n`: $(Letzte $r.fehler)"; return 'fehler' }
    [void]$namen.Add("$n.lieferschein")
    $script:hochgeladen++
    Merke-Erledigt $e 'oben'
    return 'ok'
}

function Offene-Videos-Bis([datetime]$grenze) {
    # Aufnahmen bis zu dieser Zeit, die noch nicht oben sind (zu jung, offen, gescheitert) – nicht die übersprungenen
    return @($script:videos | Where-Object { ($_.status -eq 'offen' -or $_.status -eq 'bereit') -and $_.zeit -le $grenze })
}

function Abende {
    # Alle Matches, die noch in keiner Abend-Datei stehen: hochgeladene (matches.tsv) und Replays, die noch kommen.
    # Nach Ende sortiert, mehr als 2 h Pause trennt zwei Abende. Rückgabe: Liste von @{ ids; ende; oben (alle?) }
    $eintraege = @{}
    foreach ($id in @($script:matchEnde.Keys)) {
        if (-not $script:inSitzung.Contains($id)) {
            try { $eintraege[$id] = @{ id = $id; ende = (Aus-Utc-Text $script:matchEnde[$id]); oben = $true } } catch { }
        }
    }
    foreach ($r in $script:replays) {
        # nur Replays, die noch hochkommen (übersprungene nie – sie hielten den Abend sonst auf)
        if (($r.status -eq 'offen' -or $r.status -eq 'bereit') -and -not $script:inSitzung.Contains($r.id) -and
            -not $eintraege.ContainsKey($r.id)) {
            $eintraege[$r.id] = @{ id = $r.id; ende = $r.zeit; oben = $false }
        }
    }
    $abende = New-Object 'System.Collections.Generic.List[object]'
    $block = $null
    foreach ($m in @($eintraege.Values | Sort-Object { $_.ende })) {
        if ($null -eq $block -or ($m.ende - $block.ende).TotalHours -gt $ABEND_PAUSE_H) {
            $block = @{ matches = (New-Object 'System.Collections.Generic.List[object]'); ende = $m.ende }
            $abende.Add($block)
        }
        $block.matches.Add($m)
        if ($m.ende -gt $block.ende) { $block.ende = $m.ende }
    }
    return , $abende
}

function Abend-Faellig($abend, [bool]$letzter, [datetime]$jetzt) {
    # Abend vorbei: 45 min seit dem letzten Match (oder ein späterer Abend begann) und alles bis dahin oben – nach 6 h
    # wartet er auf nichts mehr. Rückgabe: die Match-IDs für die Abend-Datei oder $null.
    if ($letzter -and ($jetzt - $abend.ende).TotalMinutes -lt $ABEND_VORBEI_MIN) { return $null }
    $alt = ($jetzt - $abend.ende).TotalHours -ge $REPLAY_WARTET_H
    $oben = @($abend.matches | Where-Object { $_.oben } | Sort-Object { $_.ende })
    if (-not $alt) {
        if (@($abend.matches | Where-Object { -not $_.oben }).Count) { return $null }
        if ((Offene-Videos-Bis $abend.ende.AddSeconds($FENSTER_NACH_MATCH_S)).Count) { return $null }
    }
    if (-not $oben.Count) { return $null }
    return , $oben
}

function Sende-Abend($oben) {
    # Die Abend-Datei als letzte Datei: sitzungen/session_<1. Match>.json, ende_utc = Ende des letzten Matches
    $ids = [string[]]@($oben | ForEach-Object { $_.id })
    $ende = ($oben | Sort-Object { $_.ende } | Select-Object -Last 1).ende
    $name = "session_$($ids[0]).json"
    $namen = $script:fach.namen['sitzungen']
    if (-not ($namen.Contains($name) -and $namen.Contains("$name.lieferschein"))) {
        # Stolperfalle 8 aus Uebertragung.ps1: [string[]], sonst schriebe PS 5.1 Objekte statt Texte in "matches"
        $inhalt = [ordered]@{ session = "session_$($ids[0])"; matches = $ids; ende_utc = (Utc-Text $ende) } |
            ConvertTo-Json -Compress
        $datei = Join-Path $ablage 'abend.json'
        [IO.File]::WriteAllText($datei, $inhalt, $utf8)
        $info = Get-Item -LiteralPath $datei
        Schreibe-Lieferschein $name $info.Length ((Get-FileHash -LiteralPath $datei -Algorithm SHA256).Hash.ToLowerInvariant()) $info.LastWriteTimeUtc
        $befehle = @()
        if (-not $namen.Contains($name)) {
            $befehle += @("put ""abend.json"" ""sitzungen/$name.teil""", "$UMBENENNEN ""sitzungen/$name.teil"" ""sitzungen/$name""")
        } else {
            # Oben liegt sie schon, aber ohne Lieferschein (Abbruch dazwischen): Der Mini hat sie nie angefasst. Neu
            # darüber schreiben – sonst passte der Lieferschein nicht mehr, wenn inzwischen Matches dazukamen
            $befehle += @("put ""abend.json"" ""sitzungen/$name""")
        }
        $befehle += @("put ""lieferschein.json"" ""sitzungen/$name.lieferschein.teil""",
                      "$UMBENENNEN ""sitzungen/$name.lieferschein.teil"" ""sitzungen/$name.lieferschein""")
        $r = Sftp-Aufruf $befehle $ablage 0 300
        if ($r.code -ne 0) { Fehler-Merken "Abend-Datei $name`: $(Letzte $r.fehler)"; return $false }
        [void]$namen.Add($name)
        [void]$namen.Add("$name.lieferschein")
        $script:hochgeladen++
    }
    Notiere $sitzungDatei "$name`t$($ids -join ',')"
    foreach ($id in $ids) { [void]$script:inSitzung.Add($id) }
    Log "Abend vorbei: $name ($($ids.Count) Matches, Ende $(Utc-Text $ende))"
    return $true
}

function Status-Kern {
    # Rückkanal zum Mini (status/pc-status.json, dort I/db/pc-status.json): was wartet, was übersprungen ist
    $offen = @()
    foreach ($q in 'videos', 'replays') {
        $liste = @($script:alle | Where-Object { $_.ordner -eq $q -and ($_.status -eq 'offen' -or $_.status -eq 'bereit') } |
            Sort-Object { $_.zeit })
        if ($liste.Count) { $offen += [ordered]@{ quelle = $q; anzahl = $liste.Count; aelteste_utc = (Utc-Text $liste[0].zeit) } }
    }
    $uebersprungen = @()
    foreach ($g in 'ohne_replay', 'name', 'zu_gross') {
        $liste = @($script:alle | Where-Object { $_.status -eq 'uebersprungen' -and $_.grund -eq $g } | Sort-Object { $_.zeit })
        if ($liste.Count) { $uebersprungen += [ordered]@{ grund = $g; anzahl = $liste.Count; beispiel = $liste[0].name } }
    }
    $jetzt = Jetzt-Utc
    return [ordered]@{
        rechner        = [Environment]::MachineName
        version        = $VERSION
        zeitzone       = [TimeZoneInfo]::Local.Id
        utc_offset_min = (Versatz-Min $jetzt)
        fortnite       = $script:fortnite
        hochgeladen    = [int]$script:hochgeladen
        fehler         = [int]$script:fehler
        letzter_fehler = $script:letzterFehler
        fach_voll      = $script:fachVoll
        offen          = $offen
        uebersprungen  = $uebersprungen
    }
}

function Status-Faellig([string]$kernJson) {
    $zeilen = @(Lies-Zeilen $statusMerk)
    if ($zeilen.Count -lt 2) { return $true }
    if ($zeilen[1] -cne $kernJson) { return $true }
    $letzte = [long]0
    if (-not [long]::TryParse($zeilen[0], [ref]$letzte)) { return $true }
    return ((Jetzt-Utc).Ticks - $letzte) -ge [TimeSpan]::FromMinutes($STATUS_MIN).Ticks
}

function Sende-Status {
    $kern = Status-Kern
    $kernJson = $kern | ConvertTo-Json -Compress -Depth 4   # -Depth: Standard 2 schriebe die Einträge als Text
    if (-not (Status-Faellig $kernJson)) { return $true }
    $status = [ordered]@{ zeit_utc = (Utc-Text (Jetzt-Utc)) }
    foreach ($schluessel in $kern.Keys) { $status[$schluessel] = $kern[$schluessel] }
    [IO.File]::WriteAllText((Join-Path $ablage 'pc-status.json'), ($status | ConvertTo-Json -Compress -Depth 4), $utf8)
    # Einzige Datei, die direkt auf ihren Namen geschrieben wird – der Mini nimmt nur ein gültiges JSON-Objekt
    $r = Sftp-Aufruf @('put "pc-status.json" "status/pc-status.json"') $ablage 0 120
    if ($r.code -ne 0) { Log "Status nicht gesendet: $(Letzte $r.fehler)"; return $false }
    $ticks = (Jetzt-Utc).Ticks
    [IO.File]::WriteAllLines($statusMerk, [string[]]@([string]$ticks, $kernJson))
    return $true
}

function Finde-Dateien($k) {
    # Alle Aufnahmen und Replays ab dem Stichtag, je mit Status: erledigt · uebersprungen (grund) · offen (noch nicht
    # fertig) · bereit. Dazu alle Replays überhaupt – für „Aufnahme ohne Replay“.
    $jetzt = Jetzt-Utc
    $script:replays = New-Object 'System.Collections.Generic.List[object]'
    $script:videos = New-Object 'System.Collections.Generic.List[object]'
    $replayZeiten = New-Object 'System.Collections.Generic.List[datetime]'
    if (Test-Path -LiteralPath $script:demos -PathType Container) {
        foreach ($d in @(Get-ChildItem -LiteralPath $script:demos -Filter '*.replay' -File -ErrorAction SilentlyContinue)) {
            $art = Art-Replay $d.Name
            if ($null -eq $art) { continue }
            if ($art -eq 'ok') { $replayZeiten.Add($d.LastWriteTimeUtc) }
            if ($d.LastWriteTimeUtc -lt $script:stichtag) { continue }
            $e = @{ datei = $d; name = $d.Name; ordner = 'replays'; art = 'replay'; zeit = $d.LastWriteTimeUtc
                    schluessel = (Datei-Schluessel $d); verzeichnis = $d.DirectoryName; status = 'bereit'; grund = '' }
            if ($art -eq 'ok') { $e.id = Match-Id $d.Name } else { $e.id = '' }
            if ($script:erledigt.Contains($e.schluessel)) { $e.status = 'erledigt' }
            elseif ($art -ne 'ok') { $e.status = 'uebersprungen'; $e.grund = 'name' }
            elseif ($d.Length -le 0 -or ($jetzt - $d.LastWriteTimeUtc).TotalSeconds -lt $RUHE_REPLAY_S -or
                    -not (Datei-Frei $d.FullName)) { $e.status = 'offen' }
            $script:replays.Add($e)
        }
    }
    $gesehen = New-Object 'System.Collections.Generic.HashSet[string]'
    foreach ($o in $script:ordner) {
        if (-not $o -or -not (Test-Path -LiteralPath $o -PathType Container)) { continue }
        foreach ($d in @(Get-ChildItem -LiteralPath $o -Filter '*.mp4' -File -Recurse -ErrorAction SilentlyContinue)) {
            if ($d.LastWriteTimeUtc -lt $script:stichtag -or -not $gesehen.Add($d.FullName)) { continue }
            $art = Art-Video $d.Name
            if ($null -eq $art) { continue }
            $e = @{ datei = $d; name = $d.Name; ordner = 'videos'; art = 'video'; zeit = $d.LastWriteTimeUtc; id = ''
                    schluessel = (Datei-Schluessel $d); verzeichnis = $d.DirectoryName; status = 'bereit'; grund = '' }
            $mitReplay = $false
            foreach ($z in $replayZeiten) {
                if ([Math]::Abs(($d.LastWriteTimeUtc - $z).TotalHours) -le $OHNE_REPLAY_H) { $mitReplay = $true; break }
            }
            if ($script:erledigt.Contains($e.schluessel)) { $e.status = 'erledigt' }
            elseif ($art -ne 'ok') { $e.status = 'uebersprungen'; $e.grund = 'name' }
            elseif (-not $mitReplay) { $e.status = 'uebersprungen'; $e.grund = 'ohne_replay' }
            elseif ($d.Length -le 0 -or ($jetzt - $d.LastWriteTimeUtc).TotalSeconds -lt $RUHE_VIDEO_S -or
                    -not (Datei-Frei $d.FullName)) { $e.status = 'offen' }
            $script:videos.Add($e)
        }
    }
    # .ToArray(): @() um eine generische Liste aus New-Object scheitert unter PowerShell 7 („Argument types do not match“)
    $script:alle = $script:videos.ToArray() + $script:replays.ToArray()
}

function Lies-Konfig([string]$pfad) {
    if (-not (Test-Path -LiteralPath $pfad)) { throw "Konfiguration fehlt: $pfad – im Bot /pc tippen und neu einrichten" }
    $k = Import-PowerShellDataFile -LiteralPath $pfad
    if ([string]$k.Adresse -notmatch '^[A-Za-z0-9.:-]{1,253}$') { throw "Adresse in $pfad ungültig" }
    if ([string]$k.Benutzer -notmatch '^bk-[a-z][a-z0-9-]{1,26}$') { throw "Benutzer in $pfad ungültig" }
    $port = 0
    if (-not [int]::TryParse([string]$k.Port, [ref]$port) -or $port -lt 1 -or $port -gt 65535) { throw "Port in $pfad ungültig" }
    $drossel = 0
    if (-not [int]::TryParse([string]$k.DrosselBeimSpielenKbit, [ref]$drossel) -or $drossel -lt 0) {
        throw "DrosselBeimSpielenKbit in $pfad ungültig"
    }
    return $k
}

function Sftp-Programm($k) {
    if ($k.Sftp) { return [string]$k.Sftp }
    if ($env:WINDIR) {
        $standard = Join-Path $env:WINDIR 'System32\OpenSSH\sftp.exe'
        if (Test-Path -LiteralPath $standard) { return $standard }
    }
    $c = Get-Command 'sftp.exe' -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) { return $c.Path }
    throw 'sftp.exe fehlt – Einstellungen → Apps → Optionale Features → „OpenSSH-Client“ hinzufügen (einmal mit Admin)'
}

# --- Nur ein Lauf gleichzeitig, mit niedrigem Vorrang (sftp erbt ihn): Prüfsumme und Upload stören das Spiel nicht ---
if ($windows) {
    try { [Diagnostics.Process]::GetCurrentProcess().PriorityClass = [Diagnostics.ProcessPriorityClass]::BelowNormal } catch { }
}
$mutex = New-Object System.Threading.Mutex($false, 'Local\ClipUpload')
try { $frei = $mutex.WaitOne(0) }
catch [System.Threading.AbandonedMutexException] { $frei = $true }   # ein abgebrochener Lauf: jetzt gehört er uns
if (-not $frei) {
    Write-Host 'Das Hochladen läuft gerade schon.'
    if ($Probe) { exit 4 } else { exit 0 }
}

$ergebnis = 0
$fehler = 0
$letzterFehler = $null
$hochgeladen = 0
$bytes = 0
$fachVoll = $false
$verbindungWeg = $false
try {
    $k = Lies-Konfig $Konfig
    $adresse = [string]$k.Adresse
    $port = [int]$k.Port
    $benutzer = [string]$k.Benutzer
    $schluesselDatei = Join-Path $PSScriptRoot 'pc'
    $knownHosts = Join-Path $PSScriptRoot 'known_hosts'
    foreach ($p in $schluesselDatei, $knownHosts) {
        if (-not (Test-Path -LiteralPath $p)) { throw "$p fehlt – im Bot /pc tippen und neu einrichten" }
    }
    $sftp = Sftp-Programm $k
    # Wie briefkasten._befehl auf dem Mini: keine Konfig-Datei, nur dieser Schlüssel, Hostschlüssel gepinnt, IPv4
    $sshOptionen = @('-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes', '-o', "IdentityFile=$(Ssh-Pfad $schluesselDatei)",
                     '-o', 'StrictHostKeyChecking=yes', '-o', "UserKnownHostsFile=$(Ssh-Pfad $knownHosts)",
                     '-o', "GlobalKnownHostsFile=$(Ssh-Pfad $knownHosts)", '-o', 'CheckHostIP=no',
                     '-o', 'AddressFamily=inet', '-o', 'ConnectTimeout=20', '-o', 'ServerAliveInterval=15',
                     '-o', 'ServerAliveCountMax=4')

    # Stichtag: Einrichtung minus 24 h (setzt Freund-Einrichten.ps1; fehlt er, ab jetzt minus 24 h)
    $stichtag = $null
    if (Test-Path -LiteralPath $stichtagDatei) {
        try { $stichtag = Aus-Utc-Text ([IO.File]::ReadAllText($stichtagDatei).Trim()) } catch { $stichtag = $null }
    }
    if ($null -eq $stichtag) {
        $stichtag = (Jetzt-Utc).AddHours(-24)
        if (-not $Probe) { [IO.File]::WriteAllText($stichtagDatei, (Utc-Text $stichtag), $utf8) }
    }
    if ($k.Ordner) { $ordner = @($k.Ordner | ForEach-Object { [Environment]::ExpandEnvironmentVariables([string]$_) }) }
    else { $ordner = @(Lies-Zeilen $ordnerDatei) }
    if (-not $ordner.Count) { $ordner = @([Environment]::GetFolderPath('MyVideos')) }
    if ($k.Demos) { $demos = [Environment]::ExpandEnvironmentVariables([string]$k.Demos) }
    else { $demos = Join-Path $basis 'FortniteGame\Saved\Demos' }

    $erledigt = New-Object 'System.Collections.Generic.HashSet[string]'
    foreach ($z in (Lies-Zeilen $erledigtDatei)) { [void]$erledigt.Add($z) }
    $shaCache = Lies-Zuordnung $shaDatei
    $teile = Lies-Zuordnung $teilDatei
    $matchEnde = Lies-Zuordnung $matchDatei
    $inSitzung = New-Object 'System.Collections.Generic.HashSet[string]'
    foreach ($z in (Lies-Zeilen $sitzungDatei)) {
        foreach ($id in ($z.Substring($z.IndexOf("`t") + 1) -split ',')) { if ($id) { [void]$inSitzung.Add($id) } }
    }

    Finde-Dateien $k
    $fortnite = Fortnite-Laeuft
    $kbit = 0
    if ($fortnite) { $kbit = [int]$k.DrosselBeimSpielenKbit }
    $pause = $fortnite -and $kbit -le 0
    $bereit = @($alle | Where-Object { $_.status -eq 'bereit' } |
        Sort-Object @{ Expression = { if ($_.art -eq 'replay') { $_.zeit.AddSeconds($FENSTER_NACH_MATCH_S) } else { $_.zeit } } },
                    @{ Expression = { if ($_.art -eq 'replay') { 1 } else { 0 } } })
    $jetzt = Jetzt-Utc
    $abende = Abende
    $abendFaellig = $false
    for ($i = 0; $i -lt $abende.Count; $i++) {
        if ($null -ne (Abend-Faellig $abende[$i] ($i -eq $abende.Count - 1) $jetzt)) { $abendFaellig = $true }
    }

    if ($Probe) {
        Write-Host "Briefkasten: $benutzer@$adresse, Port $port · sftp: $sftp"
        Write-Host "Aufnahmen aus: $($ordner -join ', ') · Replays aus: $demos · ab $(Utc-Text $stichtag)"
        foreach ($e in @($alle | Sort-Object { $_.zeit })) {
            $wie = $e.status
            if ($e.grund) { $wie = "übersprungen ($($e.grund))" }
            Write-Host ("  {0,-16} {1}/{2}" -f $wie, $e.ordner, $e.name)
        }
        if ($pause) { Write-Host 'Fortnite läuft – Pause beim Spielen (DrosselBeimSpielenKbit = 0).' }
        elseif ($fortnite) { Write-Host "Fortnite läuft – würde mit höchstens $kbit kbit/s hochladen." }
        $fach = Liste-Fach
        if (-not $fach.erreicht) { Write-Host "[!] $($fach.fehler)"; $ergebnis = 3 }
        else {
            $prozent = '?'
            if ($null -ne $fach.prozent) { $prozent = $fach.prozent }
            Write-Host "[OK] Verbunden mit Florians Briefkasten – Fach zu $prozent % voll, $($bereit.Count) Datei(en) bereit."
        }
    }
    elseif (-not ($bereit.Count -and -not $pause) -and -not $abendFaellig -and
            -not (Status-Faellig ((Status-Kern) | ConvertTo-Json -Compress -Depth 4))) {
        $ergebnis = 0   # nichts zu tun: keine Verbindung
    }
    else {
        $fach = Liste-Fach
        if (-not $fach.erreicht) {
            Log $fach.fehler
            $letzterFehler = $fach.fehler
            $ergebnis = 3
        } else {
            $stopp = $false
            if (-not $pause) {
                foreach ($e in $bereit) {
                    if ($null -ne $fach.groesse -and $e.datei.Length -gt $fach.groesse / 2) {
                        $e.status = 'uebersprungen'   # größer als das halbe Fach: bleibt hier (Status „zu_gross“)
                        $e.grund = 'zu_gross'
                        continue
                    }
                    if ($e.art -eq 'replay' -and ($jetzt - $e.zeit).TotalHours -lt $REPLAY_WARTET_H -and
                        (Offene-Videos-Bis $e.zeit.AddSeconds($FENSTER_NACH_MATCH_S)).Count) {
                        continue   # erst die Aufnahmen seines Matches – sonst rechnet der Mini das Match ohne sie
                    }
                    try { $wie = Lade-Hoch $e }
                    catch { Fehler-Merken "$($e.name): $($_.Exception.Message)"; $wie = 'fehler' }
                    if ($wie -eq 'stopp') { $stopp = $true; break }
                }
                Strom-Halten $false
            }
            if (-not $stopp) {
                $abende = Abende
                for ($i = 0; $i -lt $abende.Count; $i++) {
                    $ids = Abend-Faellig $abende[$i] ($i -eq $abende.Count - 1) (Jetzt-Utc)
                    if ($null -ne $ids) {
                        try { [void](Sende-Abend $ids) } catch { Fehler-Merken "Abend-Datei: $($_.Exception.Message)" }
                    }
                }
            }
            if ($fachVoll -or $verbindungWeg) { $ergebnis = 3 }
            elseif ($fehler) { $ergebnis = 1 }
            if (-not $verbindungWeg) {
                try { [void](Sende-Status) } catch { Log "Status nicht gesendet: $($_.Exception.Message)" }
            }
        }
        if ($hochgeladen) { Log ('hochgeladen: {0} Datei(en), {1:N0} MB' -f $hochgeladen, ($bytes / 1MB)) }
    }
}
catch {
    Log "ABBRUCH: $($_.Exception.Message) (Zeile $($_.InvocationInfo.ScriptLineNumber))"
    $ergebnis = 2
}
finally {
    Strom-Halten $false
    $mutex.ReleaseMutex()
    $mutex.Dispose()
}
exit $ergebnis
