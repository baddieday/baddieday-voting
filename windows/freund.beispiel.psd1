# Vorlage: Konfig des PC-Programms eines Freundes (Clip-Pipeline 4.0, Stufe 2 – docs/FREUNDE.md).
# Die echte freund.psd1 baut der Bot des Freundes bei /pc jedes Mal frisch (src/clip_pipeline/lernbot_pc.py) – mit
# seiner Adresse, seinem Port und seinem Benutzer. Darin steht kein Geheimnis; sein Schlüssel liegt als eigene Datei
# „pc“ daneben, der Hostschlüssel des Briefkastens als „known_hosts“. Nicht von Hand ändern: neu holen mit /pc.
@{
    # Briefkasten bei Florian: öffentlicher Name des vServers, Port, eigener Benutzer bk-<name>
    Adresse  = 'vserver.example.org'
    Port     = 2222
    Benutzer = 'bk-name'

    # Solange Fortnite läuft, höchstens so schnell hochladen (kbit/s; 2000 = 2 Mbit/s), damit nichts ruckelt.
    # 0 = beim Spielen gar nicht hochladen. Ohne Fortnite: volle Leitung.
    DrosselBeimSpielenKbit = 2000

    # Nur für Ausnahmen (und Tests) – sonst weglassen:
    # Ordner = @('D:\Aufnahmen')        # Aufnahmen (sonst der bei der Einrichtung gefundene bzw. der Videos-Ordner)
    # Demos  = 'D:\Fortnite\Demos'      # Replays (sonst %LOCALAPPDATA%\FortniteGame\Saved\Demos)
    # Sftp   = 'C:\Windows\System32\OpenSSH\sftp.exe'
}
