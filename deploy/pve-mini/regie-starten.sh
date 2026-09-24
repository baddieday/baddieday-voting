#!/usr/bin/env bash
# Auf pve-mini als root:  bash regie-starten.sh
# Weckt pve-big, spielt den Sprint-Stand NEBEN der Produktion ein (/opt/clip-regie im CT), lädt Musik,
# bestimmt die Stimmung der besten Clips, startet den Lern-Bot und baut den ersten Entwurf.
# Die Produktion (/opt/clip-pipeline, clip-bot, n8n-Aufrufe) bleibt unverändert. Beliebig oft wiederholbar.
set -euo pipefail
CT="${CT:-102}"
BRANCH="${BRANCH:-sprint-regisseur}"
ANZAHL="${ANZAHL:-40}"                      # so viele Clips bekommen jetzt eine Stimmung (die besten zuerst)
PROD=/opt/clip-pipeline
REGIE=/opt/clip-regie
Z="${Z:-/mnt/big/clips}"
sag() { printf '\n== %s\n' "$*"; }
im_ct() { pct exec "$CT" -- "$@"; }
# Login-Shell des Benutzers pipeline (HOME, SSH-Schlüssel, git-Zugang wie bei der Produktion); git fragt nie nach
als_pipeline() { pct exec "$CT" -- runuser -l pipeline -c "export GIT_TERMINAL_PROMPT=0 GIT_SSH_COMMAND='ssh -o BatchMode=yes'; $1"; }

[ "$(pct status "$CT" | awk '{print $2}')" = running ] || { echo "CT $CT läuft nicht – erst: pct start $CT"; exit 1; }

sag "1/6 pve-big wecken (falls er schläft) und warten, bis der Clips-Speicher da ist"
if ! findmnt -rn "$Z" >/dev/null 2>&1; then
  MAC="${WOL_MAC:-$(im_ct awk -F'"' '/^[[:space:]]*wol_mac/ {print $2; exit}' "$PROD/config/lokal.toml" 2>/dev/null || true)}"
  [ -n "$MAC" ] || { echo "Keine MAC gefunden (wol_mac in config/lokal.toml). pve-big von Hand einschalten, dann nochmal."; exit 1; }
  echo "Wake-on-LAN an $MAC"
  # Magisches Paket: 6x 0xFF, dann 16x die MAC – per UDP-Broadcast an Port 9
  perl -MSocket -e '
    ($mac, $ziel) = @ARGV; $mac =~ s/[:-]//g; length($mac) == 12 or die "MAC ungültig\n";
    socket(S, PF_INET, SOCK_DGRAM, getprotobyname("udp")) or die "socket: $!\n";
    setsockopt(S, SOL_SOCKET, SO_BROADCAST, 1);
    send(S, pack("H*", "ff" x 6 . $mac x 16), 0, sockaddr_in(9, inet_aton($ziel))) or die "senden: $!\n";
  ' "$MAC" "${WOL_ZIEL:-255.255.255.255}"
  echo "warte bis zu 6 min (der Nachzieher hängt alle 30 s ein) ..."
  for _ in $(seq "${WARTE_RUNDEN:-72}"); do findmnt -rn "$Z" >/dev/null 2>&1 && break; sleep "${WARTE_S:-5}"; done
  findmnt -rn "$Z" >/dev/null 2>&1 || { echo "pve-big ist nicht aufgewacht oder NFS antwortet nicht – einschalten und nochmal."; exit 1; }
fi
if im_ct ls /srv/clips/.clip-speicher >/dev/null 2>&1; then echo "Clips-Speicher im CT sichtbar"
else echo "Auf pve-mini eingehängt, aber im CT fehlt /srv/clips/.clip-speicher – bitte melden."; exit 1; fi

sag "2/6 Sprint-Stand nach $REGIE (neben der Produktion, als eigener git-worktree)"
if ! als_pipeline "git -C $PROD fetch -q origin +refs/heads/$BRANCH:refs/remotes/origin/$BRANCH"; then
  echo "git fetch geht nicht (Zugang zu GitHub im CT?) – bitte melden. Die Produktion ist unverändert."; exit 1
fi
if im_ct test -e "$REGIE/.git"; then
  als_pipeline "git -C $REGIE checkout -q --detach origin/$BRANCH"
else
  im_ct install -d -o pipeline -g pipeline "$REGIE"
  als_pipeline "git -C $PROD worktree add -q --detach $REGIE origin/$BRANCH"
fi
als_pipeline "git -C $REGIE log --oneline -1"

sag "3/6 Python-Umgebung (numpy, faster-whisper) – beim ersten Mal ein paar Minuten"
als_pipeline "cd $REGIE && { [ -x .venv/bin/python ] || python3 -m venv .venv; } && .venv/bin/pip install -q -e '.[whisper]'"
als_pipeline "ln -sfn $PROD/.env $REGIE/.env && { [ -f $REGIE/config/lokal.toml ] || cp $PROD/config/lokal.toml $REGIE/config/ 2>/dev/null || true; }"

sag "4/6 Musik von NCS (je Stimmung 2 Titel, mit Quellenangabe)"
als_pipeline "cd $REGIE && for s in episch spannend lustig frustriert chill; do .venv/bin/pipeline musik ncs --stimmung \$s --anzahl 2 2>/dev/null | tail -n 1; done"

sag "5/6 Stimmung für die besten $ANZAHL Clips (Whisper lädt beim ersten Mal ~480 MB)"
als_pipeline "cd $REGIE && .venv/bin/pipeline stimmung --max $ANZAHL 2>/dev/null | tail -n 1"

sag "6/6 Lern-Bot als Dienst starten und ersten Entwurf bauen"
im_ct sh -c "sed 's#/opt/clip-pipeline#$REGIE#g' $REGIE/deploy/systemd/clip-lernbot.service > /etc/systemd/system/clip-lernbot.service
  getent group render >/dev/null || sed -i '/^SupplementaryGroups=/d' /etc/systemd/system/clip-lernbot.service
  systemctl daemon-reload && systemctl enable -q clip-lernbot && systemctl restart clip-lernbot
  sleep 5; systemctl is-active clip-lernbot"
als_pipeline "cd $REGIE && .venv/bin/pipeline entwurf-neu --format short 2>/dev/null | tail -n 1"

sag "Fertig. In Telegram deinem Lern-Bot /start schreiben – der erste Entwurf kommt in ~30 s."
echo "Weitere Entwürfe: /entwurf short  oder  /entwurf zusammenschnitt"
echo "Mehr Clips mit Stimmung:  ANZAHL=100 bash $0"
echo "Log des Lern-Bots:        pct exec $CT -- journalctl -u clip-lernbot -n 30"
echo "pve-big läuft jetzt – wenn du fertig bist, auf pve-big herunterfahren (Weboberfläche → Shutdown)."
