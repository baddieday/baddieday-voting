#!/usr/bin/env bash
# Läuft auf pve-big als /usr/local/sbin/clip-big-steuer. Der SSH-Schlüssel des Minis ist darauf festgenagelt:
#   command="/usr/local/sbin/clip-big-steuer",restrict,from="<ip-des-mini-lxc>" ssh-ed25519 AAAA… clip-waechter
# Erlaubt sind nur zwei Wörter – alles andere wird abgewiesen:
#   status  -> eine JSON-Zeile: wie lange wach, SMB-Verbindungen (Gaming-PC kopiert?), laufende ffmpeg
#   aus     -> Host herunterfahren (Proxmox fährt laufende Gäste dabei sauber herunter)
set -u
case "${SSH_ORIGINAL_COMMAND:-}" in
  status)
    wach_s=$(cut -d' ' -f1 /proc/uptime | cut -d. -f1)
    smb=$(ss -Htn state established '( sport = :445 )' 2>/dev/null | wc -l)
    ffmpeg=$(pgrep -c -x ffmpeg 2>/dev/null || true)
    printf '{"uptime_s": %s, "smb": %s, "ffmpeg": %s}\n' "$wach_s" "$smb" "${ffmpeg:-0}"
    ;;
  aus)
    echo '{"aus": true}'
    logger -t clip-big-steuer "Herunterfahren auf Wunsch des Minis"
    systemctl poweroff
    ;;
  *)
    echo '{"fehler": "nicht erlaubt"}'
    exit 2
    ;;
esac
