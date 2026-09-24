#!/usr/bin/env bash
# Läuft auf pve-big als /usr/local/sbin/clip-big-steuer. Der SSH-Schlüssel des Minis ist darauf festgenagelt:
#   command="/usr/local/sbin/clip-big-steuer",restrict,from="<ip-des-mini-lxc>" ssh-ed25519 AAAA… clip-waechter
# Erlaubt sind nur zwei Wörter – alles andere wird abgewiesen:
#   status  -> eine JSON-Zeile: wie lange wach, SMB-Verbindungen (Gaming-PC kopiert?), laufende ffmpeg
#   aus     -> Host herunterfahren (Proxmox fährt laufende Gäste dabei sauber herunter)
#   final <name> -> Regie-Auftrag <speicher>/regie/auftraege/<name>.json mit NVENC rendern (optional, braucht
#                   ffmpeg mit NVENC und eine Kopie des Repos mit venv unter $REGIE auf pve-big)
set -u
REGIE=/opt/clip-regie                 # Repo-Kopie auf pve-big (nur für "final")
SPEICHER=/tank/clips                  # Speicher-Wurzel AUF pve-big (anpassen: zfs list)
NAME='^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$'
befehl="${SSH_ORIGINAL_COMMAND:-}"
if [[ "$befehl" == final\ * ]]; then
  name="${befehl#final }"
  [[ "$name" =~ $NAME ]] || { echo '{"fehler": "nicht erlaubt"}'; exit 2; }
  cd "$REGIE" || exit 2
  # Nicht als root rendern: Der Auftrag kommt von der Freigabe. Benutzer "clips" besitzt den Speicher (SERVER.md A1).
  exec runuser -u clips -- env CLIP_SPEICHER="$SPEICHER" CLIP_DATENBANK=/tmp/clip-regie-final.db \
    "$REGIE/.venv/bin/pipeline" render-final "$name"
fi
case "$befehl" in
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
