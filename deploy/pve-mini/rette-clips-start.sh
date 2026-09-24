#!/usr/bin/env bash
# Auf pve-mini als root ausführen:  bash rette-clips-start.sh
# Macht den Start von CT "clips" unabhängig davon, ob pve-big wach ist. Beliebig oft wiederholbar.
#
# Problem: mp0 bindet /mnt/clips = NFS von pve-big. Schläft pve-big, bricht Proxmox beim Start ab
#   ("lxc-pve-prestart-hook: cannot open directory //mnt/clips: Input/output error").
# Lösung: NFS nach /mnt/big/clips; der CT bindet den LOKALEN Ordner /mnt/big (rbind,rslave). Hängt der Host
#   später NFS ein, erscheint es im laufenden CT unter /srv/big/clips; /srv/clips ist dort ein Link darauf.
#   Schläft pve-big, ist /srv/clips einfach leer (keine .clip-speicher -> Pipeline meldet "Speicher offline").
# Sicher: Es wird nichts gelöscht. Die ERSTE Sicherung bleibt für immer in /root/clips-rettung-original/,
#   dort liegt auch zurueck.sh (alter Zustand). Jeder Lauf sichert zusätzlich nach /root/clips-rettung-<zeit>/.
set -euo pipefail
CT="${CT:-102}"
ALT="${ALT:-/mnt/clips}"                    # bisheriger NFS-Einhängepunkt
BIG="${BIG:-/mnt/big}"                      # neuer lokaler Eltern-Ordner
FSTAB="${FSTAB:-/etc/fstab}"
KONF="${KONF:-/etc/pve/lxc/$CT.conf}"
SBIN="${SBIN:-/usr/local/sbin}"
UNITS="${UNITS:-/etc/systemd/system}"
ORIGINAL="${ORIGINAL:-/root/clips-rettung-original}"
EINTRAG="lxc.mount.entry: $BIG srv/big none rbind,rslave,create=dir 0 0"
sag() { printf '\n== %s\n' "$*"; }
nfs_zeile() { awk -v z="$1" '$2 == z && $3 ~ /^nfs/ {f=1} END {exit !f}' "$FSTAB"; }
aktiv() { awk '/^\[/ {exit} {print}' "$KONF"; }             # nur der aktive Abschnitt (ohne [Snapshots])
umgebaut() { aktiv | grep -qF "$EINTRAG" && ! aktiv | grep -q '^mp0:'; }

[ -f "$KONF" ] || { echo "CT-Konfig $KONF fehlt – andere ID? (pct list)"; exit 1; }
if ! nfs_zeile "$ALT" && ! nfs_zeile "$BIG/clips"; then
  echo "Kein NFS-Eintrag für $ALT in $FSTAB – unbekannter Aufbau, ich ändere nichts. Bitte melden:"
  grep -nE 'nfs|clips' "$FSTAB" || true; exit 1
fi
LAEUFT=0; [ "$(pct status "$CT" | awk '{print $2}')" = running ] && LAEUFT=1
if [ "$LAEUFT" = 1 ] && ! umgebaut; then
  echo "CT $CT läuft gerade – bitte erst: pct shutdown $CT   (danach das Skript nochmal)"; exit 1
fi

SICH="${SICH:-/root/clips-rettung-$(date +%Y%m%d-%H%M%S)}"
mkdir -p "$SICH" && cp -a "$KONF" "$FSTAB" "$SICH/"
if [ ! -d "$ORIGINAL" ]; then
  mkdir -p "$ORIGINAL" && cp -a "$KONF" "$FSTAB" "$ORIGINAL/"
  cat > "$ORIGINAL/zurueck.sh" <<ZURUECK
#!/usr/bin/env bash
# Stellt den Zustand VOR der Rettung wieder her (mp0 direkt auf den NFS-Mount – startet nur bei wachem pve-big).
set -eu
pct stop $CT 2>/dev/null || true
systemctl disable --now clips-nfs-einhaengen.timer 2>/dev/null || true
pct mount $CT >/dev/null            # Rootfs einhängen (ohne mp0), um den Link wieder zum Ordner zu machen
R=/var/lib/lxc/$CT/rootfs
if [ -L \$R/srv/clips ]; then rm \$R/srv/clips && mkdir \$R/srv/clips && chown 100000:100000 \$R/srv/clips; fi
pct unmount $CT
cp $ORIGINAL/$(basename "$KONF") $KONF
cp $ORIGINAL/fstab $FSTAB
systemctl daemon-reload
findmnt -rn $BIG/clips >/dev/null && umount -l $BIG/clips || true
findmnt -rn $BIG >/dev/null && umount -l $BIG || true
mount $ALT || echo "NFS $ALT nicht eingehängt – pve-big wecken, dann: mount $ALT"
echo "Alter Zustand wiederhergestellt. Start (nur bei wachem pve-big): pct start $CT"
ZURUECK
  chmod 700 "$ORIGINAL/zurueck.sh"
fi
sag "Sicherung: $SICH   (Original und zurueck.sh: $ORIGINAL)"

sag "1/6 fstab: $BIG als 'shared' Bind VOR dem NFS; NFS nach $BIG/clips (ohne bg)"
awk -v alt="$ALT" -v big="$BIG" '
  $1 == big && $2 == big { next }                                   # alte Bind-Zeile weg (wird neu gesetzt)
  $3 ~ /^nfs/ && ($2 == alt || $2 == big "/clips") {
    if (!b) { print big " " big " none bind,shared 0 0"; b = 1 }    # Bind-Zeile direkt davor (mount -a!)
    $2 = big "/clips"
    n = split($4, o, ","); opt = ""
    for (i = 1; i <= n; i++) if (o[i] != "bg" && o[i] !~ /^x-systemd.mount-timeout=/) opt = opt (opt ? "," : "") o[i]
    $4 = opt ",x-systemd.mount-timeout=20"
  }
  { print }' "$FSTAB" > "$SICH/fstab.neu"
cat "$SICH/fstab.neu" > "$FSTAB"
grep -E "^[^#]*[[:space:]]$BIG(/clips)?[[:space:]]" "$FSTAB"

sag "2/6 alten, toten Mount lösen (nur die Einhängung – die Clips auf pve-big bleiben unberührt)"
if findmnt -rn "$ALT" >/dev/null 2>&1; then umount -l "$ALT"; fi
# Nur findmnt (liest die Mount-Tabelle) – nie stat/mkdir auf einen evtl. toten NFS-Mount
if ! findmnt -rn "$BIG/clips" >/dev/null 2>&1; then mkdir -p "$BIG/clips"; fi
systemctl daemon-reload
findmnt -rn "$BIG" >/dev/null 2>&1 || mount "$BIG"
findmnt -rn -o TARGET,PROPAGATION "$BIG"

sag "3/6 Nachzieher: hängt NFS ein, sobald pve-big wach ist, und löst es, wenn er schläft (Timer alle 30 s)"
cat > "$SBIN/clips-nfs-einhaengen" <<'SKRIPT'
#!/bin/bash
# pve-mini: Clips-Speicher von pve-big nachziehen. Einhängen nur, wenn Port 2049 antwortet (keine hängenden
# Mount-Versuche). Antwortet pve-big 3x nacheinander (90 s) nicht, wird ein toter Mount gelöst: dann ist
# /srv/clips im CT sofort leer ("Speicher offline") statt bei jedem Zugriff lange zu hängen.
Z="${Z:-/mnt/big/clips}"; FSTAB="${FSTAB:-/etc/fstab}"; ZAEHLER="${ZAEHLER:-/run/clips-nfs-still}"
QUELLE=$(awk -v z="$Z" '$2 == z && $3 ~ /^nfs/ {print $1; exit}' "$FSTAB")
[ -n "$QUELLE" ] || exit 0
HOST="${QUELLE%%:*}"
if timeout 2 bash -c "</dev/tcp/$HOST/2049" 2>/dev/null; then
  rm -f "$ZAEHLER"
  findmnt -rn "$Z" >/dev/null 2>&1 && exit 0
  timeout 60 mount "$Z" && logger -t clips-nfs "eingehängt: $QUELLE"
else
  findmnt -rn "$Z" >/dev/null 2>&1 || exit 0
  n=$(( $(cat "$ZAEHLER" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$ZAEHLER"
  if [ "$n" -ge 3 ]; then umount -l "$Z" && logger -t clips-nfs "gelöst (pve-big antwortet nicht): $QUELLE"; rm -f "$ZAEHLER"; fi
fi
SKRIPT
chmod 755 "$SBIN/clips-nfs-einhaengen"
printf '[Unit]\nDescription=Clips-Speicher von pve-big nachziehen\n[Service]\nType=oneshot\nExecStart=%s/clips-nfs-einhaengen\n' "$SBIN" > "$UNITS/clips-nfs-einhaengen.service"
printf '[Unit]\nDescription=Clips-Speicher alle 30 s nachziehen\n[Timer]\nOnBootSec=30s\nOnUnitActiveSec=30s\nAccuracySec=5s\n[Install]\nWantedBy=timers.target\n' > "$UNITS/clips-nfs-einhaengen.timer"
systemctl daemon-reload
systemctl enable --now clips-nfs-einhaengen.timer
Z="$BIG/clips" FSTAB="$FSTAB" "$SBIN/clips-nfs-einhaengen" || true   # sofort einmal: hängt ein, falls pve-big wach ist
findmnt -rn -o TARGET,FSTYPE "$BIG/clips" 2>/dev/null || echo "(pve-big schläft – /srv/clips bleibt leer, bis er wach ist)"

sag "4/6 CT-Konfig: mp0 raus, lokalen Ordner einbinden, Autostart an"
if aktiv | grep -q '^mp0:'; then pct set "$CT" --delete mp0; fi
if ! aktiv | grep -qF "$EINTRAG"; then
  # vor dem ersten [Snapshot]-Abschnitt einfügen, sonst landet es im Snapshot statt in der aktiven Konfig
  awk -v e="$EINTRAG" '!d && /^\[/ {print e; d=1} {print} END {if (!d) print e}' "$KONF" > "$SICH/konf.neu"
  cat "$SICH/konf.neu" > "$KONF"
fi
pct set "$CT" --onboot 1

sag "5/6 Im CT (noch gestoppt): /srv/clips -> /srv/big/clips"
if [ "$LAEUFT" = 0 ]; then
  pct mount "$CT" >/dev/null
  R="${ROOTFS:-/var/lib/lxc/$CT/rootfs}"
  if [ -L "$R/srv/clips" ]; then echo "Link steht schon"
  elif [ -d "$R/srv/clips" ] && [ -z "$(ls -A "$R/srv/clips")" ]; then rmdir "$R/srv/clips" && ln -s /srv/big/clips "$R/srv/clips" && echo "Link angelegt"
  elif [ ! -e "$R/srv/clips" ]; then ln -s /srv/big/clips "$R/srv/clips" && echo "Link angelegt"
  else echo "ACHTUNG: /srv/clips im CT ist nicht leer – nichts verändert, bitte melden"; ls -la "$R/srv/clips"; fi
  pct unmount "$CT"
  sag "6/6 CT starten"
  pct start "$CT"
else
  echo "CT läuft schon (umgebaut) – Link und Start übersprungen"
fi
pct status "$CT"

printf '\nLEARN_BOT_TOKEN jetzt eintragen? Einfügen + Enter (wird nicht angezeigt), nur Enter = später: '
read -rs TOKEN || TOKEN=""
echo
if [ -n "$TOKEN" ]; then
  if [[ "$TOKEN" =~ ^[0-9]{6,12}:[A-Za-z0-9_-]{30,}$ ]]; then
    # pct pull/push statt einer Pipe in pct exec (dort wäre ein Terminal im Spiel): Token nie in Befehlszeilen
    T="$(mktemp -d /run/clips-token.XXXXXX)"; chmod 700 "$T"
    pct pull "$CT" /opt/clip-pipeline/.env "$T/env" 2>/dev/null || : > "$T/env"
    { grep -v '^LEARN_BOT_TOKEN=' "$T/env" || true; printf 'LEARN_BOT_TOKEN=%s\n' "$TOKEN"; } > "$T/neu"
    pct push "$CT" "$T/neu" /opt/clip-pipeline/.env --user pipeline --group pipeline --perms 0600
    rm -rf "$T"
    echo "eingetragen (Datei gehört pipeline, Rechte 600)"
  else
    echo "Das sieht nicht wie ein Bot-Token aus – nichts eingetragen."
  fi
fi
unset TOKEN

sag "Fertig. Zurück zum Zustand vor der Rettung, falls nötig:  bash $ORIGINAL/zurueck.sh"
