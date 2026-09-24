#!/usr/bin/env bash
# Auf pve-mini als root ausführen:  bash rette-clips-start.sh
# Macht den Start von CT "clips" unabhängig davon, ob pve-big wach ist.
#
# Problem: mp0 bindet /mnt/clips = NFS von pve-big. Schläft pve-big, bricht Proxmox beim Start ab
#   ("lxc-pve-prestart-hook: cannot open directory //mnt/clips: Input/output error").
# Lösung: NFS nach /mnt/big/clips; der CT bindet den LOKALEN Ordner /mnt/big (rbind,rslave). Hängt der Host
#   später NFS ein, erscheint es im laufenden CT unter /srv/big/clips; /srv/clips ist dort ein Link darauf.
#   Schläft pve-big, ist /srv/clips einfach leer (keine .clip-speicher -> Pipeline meldet "Speicher offline").
# Sicher: Es wird nichts gelöscht. Sicherung von CT-Konfig und fstab unter /root/clips-rettung-<zeit>/.
set -euo pipefail
CT="${CT:-102}"
ALT="${ALT:-/mnt/clips}"                    # bisheriger NFS-Einhängepunkt
BIG="${BIG:-/mnt/big}"                      # neuer lokaler Eltern-Ordner
FSTAB="${FSTAB:-/etc/fstab}"
KONF="${KONF:-/etc/pve/lxc/$CT.conf}"
SBIN="${SBIN:-/usr/local/sbin}"
UNITS="${UNITS:-/etc/systemd/system}"
EINTRAG="lxc.mount.entry: $BIG srv/big none rbind,rslave,create=dir 0 0"
sag() { printf '\n== %s\n' "$*"; }

[ -f "$KONF" ] || { echo "CT-Konfig $KONF fehlt – andere ID? (pct list)"; exit 1; }
if [ "$(pct status "$CT" | awk '{print $2}')" = running ]; then
  echo "CT $CT läuft gerade – bitte erst: pct shutdown $CT"; exit 1
fi

SICH="${SICH:-/root/clips-rettung-$(date +%Y%m%d-%H%M%S)}"
mkdir -p "$SICH" && cp -a "$KONF" "$FSTAB" "$SICH/"
sag "Sicherung: $SICH"

sag "1/6 fstab: NFS nach $BIG/clips, davor $BIG als 'shared' Bind"
if awk -v alt="$ALT" '$2 == alt && $3 ~ /^nfs/ {f=1} END {exit !f}' "$FSTAB"; then
  awk -v alt="$ALT" -v neu="$BIG/clips" '$2 == alt && $3 ~ /^nfs/ {$2 = neu} {print}' "$FSTAB" > "$FSTAB.neu"
  cat "$FSTAB.neu" > "$FSTAB" && rm -f "$FSTAB.neu"
fi
if ! awk -v big="$BIG" '$1 == big && $2 == big {f=1} END {exit !f}' "$FSTAB"; then
  echo "$BIG $BIG none bind,shared 0 0" >> "$FSTAB"
fi
grep -E "^[^#]*[[:space:]]$BIG(/clips)?[[:space:]]" "$FSTAB" || true
if ! awk -v z="$BIG/clips" '$2 == z && $3 ~ /^nfs/ {f=1} END {exit !f}' "$FSTAB"; then
  echo "WARNUNG: kein NFS-Eintrag für $BIG/clips in $FSTAB – der CT startet trotzdem, /srv/clips bleibt leer. Bitte melden."
fi

sag "2/6 alten, toten Mount lösen (nur die Einhängung – die Clips auf pve-big bleiben unberührt)"
if findmnt -rn "$ALT" >/dev/null 2>&1; then umount -l "$ALT"; fi
mkdir -p "$BIG/clips"
systemctl daemon-reload
findmnt -rn "$BIG" >/dev/null 2>&1 || mount "$BIG"
findmnt -rn -o TARGET,PROPAGATION "$BIG"

sag "3/6 Nachzieher: hängt NFS ein, sobald pve-big wach ist (Timer alle 30 s)"
cat > "$SBIN/clips-nfs-einhaengen" <<'SKRIPT'
#!/bin/bash
# pve-mini: hängt den Clips-Speicher ein, sobald pve-big wach ist. Nur wenn Port 2049 antwortet –
# so entstehen bei schlafendem pve-big keine hängenden Mount-Versuche.
Z="${Z:-/mnt/big/clips}"; FSTAB="${FSTAB:-/etc/fstab}"
findmnt -rn "$Z" >/dev/null 2>&1 && exit 0
QUELLE=$(awk -v z="$Z" '$2 == z && $3 ~ /^nfs/ {print $1; exit}' "$FSTAB")
[ -n "$QUELLE" ] || exit 0
HOST="${QUELLE%%:*}"
timeout 2 bash -c "</dev/tcp/$HOST/2049" 2>/dev/null || exit 0
timeout 60 mount "$Z" && logger -t clips-nfs "eingehängt: $QUELLE"
SKRIPT
chmod 755 "$SBIN/clips-nfs-einhaengen"
printf '[Unit]\nDescription=Clips-Speicher von pve-big einhängen, sobald er wach ist\n[Service]\nType=oneshot\nExecStart=%s/clips-nfs-einhaengen\n' "$SBIN" > "$UNITS/clips-nfs-einhaengen.service"
printf '[Unit]\nDescription=Clips-Speicher alle 30 s nachziehen\n[Timer]\nOnBootSec=30s\nOnUnitActiveSec=30s\nAccuracySec=5s\n[Install]\nWantedBy=timers.target\n' > "$UNITS/clips-nfs-einhaengen.timer"
systemctl daemon-reload
systemctl enable --now clips-nfs-einhaengen.timer
Z="$BIG/clips" FSTAB="$FSTAB" "$SBIN/clips-nfs-einhaengen" || true   # sofort einmal: hängt ein, falls pve-big wach ist
findmnt -rn -o TARGET,FSTYPE "$BIG/clips" 2>/dev/null || echo "(pve-big schläft – /srv/clips bleibt leer, bis er wach ist)"

sag "4/6 CT-Konfig: mp0 raus, lokalen Ordner einbinden, Autostart an"
if grep -q '^mp0:' "$KONF"; then pct set "$CT" --delete mp0; fi
if ! grep -qF "$EINTRAG" "$KONF"; then
  # vor dem ersten [Snapshot]-Abschnitt einfügen, sonst landet es im Snapshot statt in der aktiven Konfig
  awk -v e="$EINTRAG" '!d && /^\[/ {print e; d=1} {print} END {if (!d) print e}' "$KONF" > "$SICH/konf.neu"
  cat "$SICH/konf.neu" > "$KONF"
fi
pct set "$CT" --onboot 1

sag "5/6 CT starten"
pct start "$CT"
pct status "$CT"

sag "6/6 Im CT: /srv/clips -> /srv/big/clips"
pct exec "$CT" -- sh -c '
  if [ -L /srv/clips ]; then echo "Link steht schon";
  elif [ -d /srv/clips ] && [ -z "$(ls -A /srv/clips)" ]; then rmdir /srv/clips && ln -s /srv/big/clips /srv/clips && echo "Link angelegt";
  elif [ ! -e /srv/clips ]; then ln -s /srv/big/clips /srv/clips && echo "Link angelegt";
  else echo "ACHTUNG: /srv/clips ist nicht leer – nichts verändert, bitte melden"; ls -la /srv/clips; fi
  ls -la /srv/clips /srv/big'

printf '\nLEARN_BOT_TOKEN jetzt eintragen? Einfügen + Enter (wird nicht angezeigt), nur Enter = später: '
read -rs TOKEN || TOKEN=""
echo
if [ -n "$TOKEN" ]; then
  if [[ "$TOKEN" =~ ^[0-9]{6,12}:[A-Za-z0-9_-]{30,}$ ]]; then
    # Token nur über stdin – nicht in der Befehlszeile, nicht in der History
    printf 'LEARN_BOT_TOKEN=%s\n' "$TOKEN" | pct exec "$CT" -- sh -c '
      umask 077; f=/opt/clip-pipeline/.env; grep -v "^LEARN_BOT_TOKEN=" "$f" > "$f.neu" 2>/dev/null || true
      cat >> "$f.neu" && mv "$f.neu" "$f" && chown pipeline:pipeline "$f" && chmod 600 "$f"
      echo "eingetragen: $(grep -c "^LEARN_BOT_TOKEN=" "$f") Zeile, $(stat -c "%U %a" "$f")"'
  else
    echo "Das sieht nicht wie ein Bot-Token aus – nichts eingetragen."
  fi
fi
unset TOKEN

sag "Fertig. Zurück zum alten Stand, falls nötig:"
echo "  pct stop $CT; cp $SICH/$(basename "$KONF") $KONF; cp $SICH/fstab $FSTAB"
echo "  systemctl disable --now clips-nfs-einhaengen.timer; systemctl daemon-reload"
