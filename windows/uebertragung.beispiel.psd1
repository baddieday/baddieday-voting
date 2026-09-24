# Kopiere diese Datei nach "uebertragung.psd1" und passe Ziel, ZielHost und MAC an.
@{
    # Freigabe des großen Proxmox-Hosts (UNC-Pfad oder verbundenes Laufwerk, z. B. 'Z:\')
    Ziel                = '\\pve-gross\clips'
    # Rechnername/IP für die schnelle Erreichbarkeitsprüfung (SMB-Port 445). Leer = nicht prüfen.
    ZielHost            = 'pve-gross'
    # MAC-Adresse des großen Hosts für Wake-on-LAN, z. B. 'AA-BB-CC-DD-EE-FF'. Leer = nicht wecken.
    WakeOnLanMac        = ''
    WeckenWarteSekunden = 180

    # Match-Ende an n8n melden (Vertrag: POST /webhook/match-vorbei, Header X-Pipeline-Token).
    # URL über Tailscale; leer = nicht melden. Den Token NUR hier eintragen – diese Datei ist in .gitignore.
    WebhookUrl          = 'http://<vserver-tailscale-name>:5678/webhook/match-vorbei'
    WebhookToken        = ''

    # Datei gilt als fertig, wenn sie so lange nicht mehr verändert wurde
    RuhezeitSekunden    = 60
    # "Session vorbei" an den Mini melden (Datei sitzungen\session_<zeit>.json auf dem Speicher):
    # 0 = aus. Z. B. 20 = Fortnite zu und seit 20 Minuten kein neues Match. n8n bleibt davon unberührt.
    SessionVorbeiMinuten = 0

    # Ältere Dateien ignorieren (begrenzt die allererste Übertragung; das Archiv ist ~430 GB groß)
    MaxAlterTage        = 30

    Quellen = @(
        # Nvidia Highlights (automatisch bei Kills)
        @{ Pfad = 'F:\Clips\Highlights\Fortnite'; Muster = @('*.mp4', '*.png'); Ziel = 'eingang\nvidia\highlights' }
        # Nvidia Videobeweis (Alt+F10) und manuelle Aufnahmen (Alt+F9)
        @{ Pfad = 'F:\Clips\Fortnite'; Muster = @('*.mp4'); Ziel = 'eingang\nvidia\aufnahmen' }
        # SteelSeries Moments (liegen direkt in F:\Clips) – bewusst NICHT rekursiv
        @{ Pfad = 'F:\Clips'; Muster = @('Fortnite__*.mp4'); Ziel = 'eingang\steelseries' }
        # Fortnite-Replays: nur kopieren, nie löschen; erst 2 Minuten nach Match-Ende
        # Melden = $true: nach dem Kopieren wird n8n über das Match-Ende informiert
        @{ Pfad = '%LOCALAPPDATA%\FortniteGame\Saved\Demos'; Muster = @('*.replay'); Ziel = 'replays'; NurKopieren = $true; RuhezeitSekunden = 120; Melden = $true }
    )
}
