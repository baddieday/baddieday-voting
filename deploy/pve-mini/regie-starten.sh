#!/usr/bin/env bash
# Auf pve-mini als root:  bash regie-starten.sh
# Weckt pve-big, spielt den Sprint-Stand NEBEN der Produktion ein (/opt/clip-regie im CT), legt clip-leerlauf
# für pve-big bereit, lädt Musik, bestimmt die Stimmung der besten Clips, startet den Lern-Bot und baut einen
# Entwurf. Die Produktion (/opt/clip-pipeline, clip-bot, n8n-Aufrufe) bleibt unverändert. Beliebig oft
# wiederholbar; gibt es eine neuere Version dieses Skripts im Repo, übernimmt es sie selbst.
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

# Magisches Paket: 6x 0xFF, dann 16x die MAC – per UDP-Broadcast an Port 9
wecken() {
  perl -MSocket -e '
    ($mac, $ziel) = @ARGV; $mac =~ s/[:-]//g; length($mac) == 12 or die "MAC ungültig\n";
    socket(S, PF_INET, SOCK_DGRAM, getprotobyname("udp")) or die "socket: $!\n";
    setsockopt(S, SOL_SOCKET, SO_BROADCAST, 1);
    send(S, pack("H*", "ff" x 6 . $mac x 16), 0, sockaddr_in(9, inet_aton($ziel))) or die "senden: $!\n";
  ' "$MAC" "${WOL_ZIEL:-255.255.255.255}"
}

# Einfüge-Block für pve-big. Die Prüfsummen kommen aus dem git-Stand im CT, NICHT von der Freigabe (die kann auch
# der Gaming-PC beschreiben) – so passt der Block immer zu den bereitgelegten Dateien.
SUMMEN=""
leerlauf_block() {
  [ -n "$SUMMEN" ] || return 0
  echo "---- in die Shell von pve-big einfügen (Weboberfläche → pve-big → Shell), danach das Fenster schließen ----"
  cat <<'BLOCK'
D=$(ls -d /*/clips/.einrichtung /*/*/clips/.einrichtung 2>/dev/null | head -n 1); T=$(mktemp -d) && cp "${D:?.einrichtung nicht gefunden}"/{clip-leerlauf,clip-leerlauf.service,clip-leerlauf.timer,einrichten.sh} "$T"/ && cd "$T" && sha256sum -c <<'H' && bash einrichten.sh "${D%/.einrichtung}"
BLOCK
  printf '%s\n' "$SUMMEN"
  echo "H"
  echo "---- Ende ----"
}

# Am Ende – auch bei einem Abbruch –: Schaltet sich pve-big selbst ab? (clip-leerlauf setzt jede Minute den
# Zeitstempel der leeren Datei <clips>/.leerlauf-scharf; nur nachsehen, nicht lesen – Lesen zählt als Zugriff)
schluss() {
  findmnt -rn "$Z" >/dev/null 2>&1 || return 0
  if [ -n "$(im_ct sh -c 'find /srv/clips/.leerlauf-scharf -mmin -10 2>/dev/null')" ]; then
    echo "✅ pve-big schaltet sich über clip-leerlauf selbst ab, wenn ihn keiner mehr braucht."
  else
    echo
    echo "⚠️  clip-leerlauf ist auf pve-big noch NICHT scharf – pve-big bleibt an, bis du ihn einrichtest"
    echo "    oder von Hand herunterfährst (Weboberfläche → pve-big → Shutdown)."
    leerlauf_block
  fi
}
trap schluss EXIT

sag "1/7 pve-big wecken (falls er schläft) und warten, bis der Clips-Speicher da ist"
if ! findmnt -rn "$Z" >/dev/null 2>&1; then
  MAC="${WOL_MAC:-$(im_ct awk -F'"' '/^[[:space:]]*wol_mac/ {print $2; exit}' "$PROD/config/lokal.toml" 2>/dev/null || true)}"
  [ -n "$MAC" ] || { echo "Keine MAC gefunden (wol_mac in config/lokal.toml). pve-big von Hand einschalten, dann nochmal."; exit 1; }
  echo "Wake-on-LAN an $MAC"
  wecken
  echo "warte bis zu 6 min (der Nachzieher hängt alle 30 s ein) ..."
  # je Runde erneut wecken: fährt pve-big gerade noch herunter, verpufft ein einzelnes Paket
  for _ in $(seq "${WARTE_RUNDEN:-72}"); do findmnt -rn "$Z" >/dev/null 2>&1 && break; sleep "${WARTE_S:-5}"; wecken || true; done
  findmnt -rn "$Z" >/dev/null 2>&1 || { echo "pve-big ist nicht aufgewacht oder NFS antwortet nicht – einschalten und nochmal."; exit 1; }
fi
if im_ct ls /srv/clips/.clip-speicher >/dev/null 2>&1; then echo "Clips-Speicher im CT sichtbar"
else echo "Auf pve-mini eingehängt, aber im CT fehlt /srv/clips/.clip-speicher – bitte melden."; exit 1; fi

sag "2/7 Sprint-Stand nach $REGIE (neben der Produktion, als eigener git-worktree)"
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

# Neuere Version dieses Skripts im Repo? Dann übernehmen und neu starten (einmal)
if [ -z "${REGIE_NEU:-}" ] && [ -f "$0" ]; then
  tmp="$(mktemp -d)"; neu="$tmp/regie.sh"
  if pct pull "$CT" "$REGIE/deploy/pve-mini/regie-starten.sh" "$neu" 2>/dev/null && [ -s "$neu" ] \
     && head -n 1 "$neu" | grep -q '^#!' && bash -n "$neu" && ! cmp -s "$neu" "$0"; then
    echo "Neue Version von $0 – übernehme sie und starte neu"
    cp "$neu" "$0.neu" && mv "$0.neu" "$0" && rm -rf "$tmp"
    REGIE_NEU=1 exec bash "$0" "$@"
  fi
  rm -rf "$tmp"
fi

sag "3/7 clip-leerlauf für pve-big bereitlegen"
if als_pipeline "install -d -m 755 /srv/clips/.einrichtung && cd $REGIE/deploy/big && cp clip-leerlauf clip-leerlauf.service clip-leerlauf.timer einrichten.sh /srv/clips/.einrichtung/"; then
  echo "liegt auf pve-big unter <clips>/.einrichtung"
  SUMMEN="$(im_ct sh -c "cd $REGIE/deploy/big && sha256sum clip-leerlauf clip-leerlauf.service clip-leerlauf.timer einrichten.sh" | tr -d '\r')"
  if [ -z "$(im_ct sh -c 'find /srv/clips/.leerlauf-scharf -mmin -10 2>/dev/null')" ]; then
    echo "Jetzt schon einrichten, während der Rest läuft:"
    leerlauf_block
  fi
else
  echo "⚠️  konnte nicht bereitlegen (Schreibrecht auf /srv/clips?) – bitte melden; der Rest läuft weiter."
fi

sag "4/7 Python-Umgebung (numpy, faster-whisper) – beim ersten Mal ein paar Minuten"
als_pipeline "cd $REGIE && { [ -x .venv/bin/python ] || python3 -m venv .venv; } && .venv/bin/pip install -q -e '.[whisper]'"
als_pipeline "ln -sfn $PROD/.env $REGIE/.env && { [ -f $REGIE/config/lokal.toml ] || cp $PROD/config/lokal.toml $REGIE/config/ 2>/dev/null || true; }"

sag "5/7 Musik von NCS (je Stimmung 2 Titel, mit Quellenangabe)"
als_pipeline "cd $REGIE && for s in episch spannend lustig frustriert chill; do .venv/bin/pipeline musik ncs --stimmung \$s --anzahl 2 2>/dev/null | tail -n 1; done"

sag "6/7 Stimmung für die nächsten $ANZAHL Clips (Whisper lädt beim ersten Mal ~480 MB)"
als_pipeline "cd $REGIE && .venv/bin/pipeline stimmung --max $ANZAHL 2>/dev/null | tail -n 1"

sag "7/7 Lern-Bot als Dienst starten und einen Entwurf bauen"
im_ct sh -c "sed 's#/opt/clip-pipeline#$REGIE#g' $REGIE/deploy/systemd/clip-lernbot.service > /etc/systemd/system/clip-lernbot.service
  getent group render >/dev/null || sed -i '/^SupplementaryGroups=/d' /etc/systemd/system/clip-lernbot.service
  systemctl daemon-reload && systemctl enable -q clip-lernbot && systemctl restart clip-lernbot
  sleep 5; systemctl is-active clip-lernbot"
als_pipeline "cd $REGIE && .venv/bin/pipeline entwurf-neu --format short 2>/dev/null | tail -n 1"

sag "Fertig. Der Entwurf kommt in ~30 s im Lern-Bot (sonst dort /start)."
echo "Nach jeder Bewertung (✅ fertig) baut der Bot den nächsten und analysiert dabei 10 weitere Clips."
echo "Mehr Clips auf einmal:  ANZAHL=100 bash $0"
echo "Log des Lern-Bots:      pct exec $CT -- journalctl -u clip-lernbot -n 30"
# (die Prüfung, ob pve-big sich selbst abschaltet, macht schluss() beim Beenden)
