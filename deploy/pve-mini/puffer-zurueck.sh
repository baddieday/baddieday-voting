#!/usr/bin/env bash
# Auf pve-mini (Host) als root:  bash puffer-zurueck.sh --probe   (zeigt nur)   ·   bash puffer-zurueck.sh
# Rückweg zu puffer-einrichten.sh (docs/PUFFER.md, R1): hängt den Puffer aus CT 102 aus und schaltet den
# lvm-status-Timer ab. Es wird NICHTS gelöscht: Proxmox behält das Volume als "unusedN" in der CT-Konfig – die
# Daten bleiben und lassen sich wieder einhängen. Den Befehl zum endgültigen Löschen zeigt das Skript nur an.
# Bricht ab, solange /srv/clips im CT auf den Puffer zeigt: dann arbeitet die Pipeline dort (erst R5 zurück).
set -euo pipefail
CT="${CT:-102}"
ZIEL=/srv/puffer                             # im CT
SPEICHER="${SPEICHER:-local-lvm}"
SPERRE="${SPERRE:-/var/lib/clip-pipeline/pipeline.lock}"   # Pipeline-Sperre im CT (flock)
KONF="${KONF:-/etc/pve/lxc/$CT.conf}"
SICH="${SICH:-/root/puffer-$(date +%Y%m%d-%H%M%S)}"
PROBE=0
case "${1:-}" in
  --probe) PROBE=1 ;;
  "") ;;
  *) echo "Aufruf: bash $0 [--probe]"; exit 2 ;;
esac

sag() { printf '\n== %s\n' "$*"; }
# Befehl so anzeigen, dass man ihn kopieren kann, und ausführen – im Probe-Modus nur anzeigen
tu() {
  local a z=""
  for a in "$@"; do case "$a" in *[!A-Za-z0-9_./:=,@%+-]*) z="$z '$a'" ;; *) z="$z $a" ;; esac; done
  printf '   $%s\n' "$z"
  [ "$PROBE" = 1 ] || "$@"
}
# Nachfragen: nur j/ja gilt. Im Probe-Modus wird nichts gefragt (angenommen ja, damit du alle Schritte siehst).
frage() {
  if [ "$PROBE" = 1 ]; then printf '   ? %s  -> Probe: angenommen ja\n' "$1"; return 0; fi
  local antwort=""
  read -r -p "   ? $1 [j/N] " antwort || true
  case "$antwort" in j|J|ja|Ja|JA) return 0 ;; *) return 1 ;; esac
}
aktiv() { awk '/^\[/ {exit} {print}' "$KONF"; }   # nur der aktive Abschnitt (ohne [Snapshots] und [pending])
# Einhängepunkt, der auf $ZIEL zeigt, als "mp1 local-lvm:vm-102-disk-1" (leer, wenn keiner)
puffer_mp() {
  aktiv | awk -v z="mp=$ZIEL" '/^mp[0-9]+:/ {
    n = split($2, t, ","); for (i = 2; i <= n; i++) if (t[i] == z) { sub(/:$/, "", $1); print $1, t[1]; exit } }'
}
ct_laeuft() { [ "$(pct status "$CT" | awk '{print $2}')" = running ]; }
im_ct() { pct exec "$CT" -- "$@" 2>/dev/null | tr -d '\r'; }   # Ausgabe ohne \r des Terminals
# Auch bei einem Abbruch: Was dieses Skript heruntergefahren hat, startet es wieder.
HERUNTERGEFAHREN=0
wieder_an() {
  if [ "$HERUNTERGEFAHREN" = 1 ] && ! ct_laeuft; then
    echo "CT $CT wieder starten ..."; pct start "$CT" || echo "Start fehlgeschlagen – bitte selbst: pct start $CT"
  fi
}
trap wieder_an EXIT

[ "$(id -u)" = 0 ] || { echo "Bitte als root auf pve-mini ausführen."; exit 1; }
command -v pct >/dev/null || { echo "pct fehlt – das Skript läuft auf dem Proxmox-Host pve-mini, nicht im CT."; exit 1; }
[ -f "$KONF" ] || { echo "CT-Konfig $KONF fehlt – andere ID? (pct list)"; exit 1; }
if [ "$PROBE" = 1 ]; then echo "PROBE: Ich zeige nur, was ich tun würde, und ändere nichts."; fi

sag "1/5 Bestand"
VORHANDEN="$(puffer_mp)"
MP=""; VOL=""
if [ -n "$VORHANDEN" ]; then
  MP="${VORHANDEN%% *}"; VOL="${VORHANDEN#* }"
  echo "Puffer: $MP = $VOL -> $ZIEL"
else
  echo "Kein Einhängepunkt auf $ZIEL in CT $CT – der Puffer ist schon ausgehängt."
fi

sag "2/5 Wohin zeigt /srv/clips im CT? (Arbeitet die Pipeline noch im Puffer, bleibt alles, wie es ist.)"
ct_laeuft || { echo "CT $CT ist aus – bitte erst: pct start $CT (ich muss im CT nachsehen), dann nochmal."; exit 1; }
# nur den Link lesen (readlink ohne -f): nie etwas unter einem evtl. toten NFS-Mount anfassen
LINK="$(im_ct readlink /srv/clips || true)"
case "$LINK" in /*|"") ;; *) LINK="/srv/$LINK" ;; esac   # relativer Link zählt ab /srv
echo "/srv/clips -> ${LINK:-(kein Link)}"
case "$LINK" in
  "")
    echo "/srv/clips ist kein Link – unbekannter Aufbau, ich ändere nichts. Bitte melden."; exit 1 ;;
  "$ZIEL"|"$ZIEL"/*)
    echo "Die Pipeline arbeitet noch im Puffer – ich ändere nichts. Erst zurückschalten (docs/PUFFER.md, Rückweg R5)."
    echo "Die Reihenfolge ist wichtig – erst darf nichts Neues mehr in den Puffer kommen, dann alles ins Lager:"
    echo "  1. Gaming-PC: Übertragung pausieren (Disable-ScheduledTask) · im CT: systemctl stop smbd"
    echo "  2. im CT: clip-bot, clip-lernbot, clip-sitzungen.timer stoppen · warten, bis die Pipeline-Sperre frei ist"
    echo "  3. 10 min warten ([lager].ruhe_min), dann pipeline lager abgleich, bis 'pipeline lager status' 0 offen zeigt"
    echo "  4. clip-lager.timer und clip-puffer-pruefen.timer ausschalten"
    echo "  5. ln -sfn /srv/big/clips /srv/clips, lokal.toml zurück"
    echo "  6. Gaming-PC zurück auf pve-big (psd1, Rückweg R7) und Übertragung wieder an, Dienste starten"
    echo "  7. dann dieses Skript nochmal"
    exit 1 ;;
esac
if [ -n "$VOL" ]; then
  echo "Im Puffer belegt: $(im_ct df -h --output=used "$ZIEL" | tail -n 1 | tr -d ' ' || true) – bleibt im Volume erhalten."
fi

sag "3/5 Samba im CT (ohne Puffer zeigte die Freigabe auf einen leeren Ordner der CT-Platte)"
SMBD_LIEF=0
if [ "$(im_ct systemctl is-active smbd || true)" = active ]; then
  SMBD_LIEF=1
  # Laut Rückweg R5 ist smbd vor dem letzten Abgleich gestoppt worden. Läuft er noch, kann der PC danach noch
  # in den Puffer kopiert haben – diese Dateien liegen dann NUR im Puffer (der PC kopiert sie nie noch einmal).
  echo "ACHTUNG: smbd läuft noch. Hat der Gaming-PC nach dem letzten Abgleich in den Puffer kopiert, liegen"
  echo "diese Dateien nur im Puffer. Das Aushängen löscht sie nicht – aber lösch das Volume danach nicht."
  if frage "smbd im CT stoppen und nicht mehr starten?"; then
    tu pct exec "$CT" -- systemctl disable --now smbd
  else
    echo "übersprungen – der Gaming-PC findet dort kein .clip-speicher und kopiert dann nichts"
  fi
else
  echo "smbd läuft nicht – nichts zu tun"
fi

sag "4/5 Puffer aushängen – das Volume bleibt als unusedN erhalten"
if [ -z "$VOL" ]; then
  echo "nichts zu tun"
else
  if ! pct exec "$CT" -- runuser -u pipeline -- flock -n "$SPERRE" true; then
    echo "Im CT läuft gerade ein Pipeline-Schritt (Sperre $SPERRE belegt) – bitte später nochmal."; exit 1
  fi
  if frage "CT $CT herunterfahren, $MP aushängen (Daten bleiben im Volume $VOL) und CT wieder starten?"; then
    if [ "$PROBE" = 0 ]; then
      mkdir -p "$SICH" && cp -a "$KONF" "$SICH/" && echo "Sicherung der CT-Konfig: $SICH"
      HERUNTERGEFAHREN=1
    fi
    tu pct shutdown "$CT" --timeout 180
    tu pct set "$CT" --delete "$MP"
    tu pct start "$CT"
    if [ "$PROBE" = 0 ]; then
      if aktiv | grep -qF "$VOL"; then aktiv | grep -F "$VOL"
      else echo "ACHTUNG: $VOL steht nicht mehr in der CT-Konfig – bitte melden (pvesm list $SPEICHER)."; fi
    fi
  else
    echo "übersprungen"
  fi
fi

sag "5/5 lvm-status-Timer auf pve-mini"
if systemctl is-enabled --quiet clip-lvm-status.timer 2>/dev/null; then
  if frage "clip-lvm-status.timer abschalten? (Die Dateien in /usr/local/sbin und /etc/systemd/system bleiben.)"; then
    tu systemctl disable --now clip-lvm-status.timer
  else
    echo "übersprungen"
  fi
else
  echo "nicht eingeschaltet – nichts zu tun"
fi

if [ "$PROBE" = 1 ]; then
  sag "Probe fertig – nichts verändert. Echt:  bash $0"
else
  sag "Fertig."
fi
if [ -n "$VOL" ]; then
  UNUSED="$(aktiv | awk -F': ' -v v="$VOL" '$1 ~ /^unused[0-9]+$/ && $2 == v {print $1; exit}')"
  echo "Wieder einhängen: Weboberfläche -> CT $CT -> Ressourcen -> unbenutzte Disk doppelklicken -> Pfad $ZIEL, Backup aus."
  echo "Endgültig löschen – NUR wenn alles im Lager ist und du den Platz zurück willst (unwiderruflich!)."
  echo "\"Alles im Lager\" heißt (Rückweg R5 in docs/PUFFER.md):"
  echo "  - Der Gaming-PC kopiert nicht mehr in den Puffer: Übertragung war vor dem letzten Abgleich pausiert,"
  printf '%s\n' '    smbd gestoppt, und die psd1 zeigt wieder auf pve-big (nicht mehr auf \\192.168.178.93\clips).'
  echo "  - Danach, nach 10 min Ruhe, zeigte 'pipeline lager status' 0 offen – und erst dann kam /srv/clips zurück."
  echo "    (Heute geht das nicht mehr nachzuprüfen: ohne [lager].wurzel endet 'pipeline lager status' mit Exit 2.)"
  if [ "$SMBD_LIEF" = 1 ]; then
    echo "  smbd lief eben noch – im Zweifel NICHT löschen: erst wieder einhängen und neuere Dateien ins Lager kopieren."
  fi
  echo "Nur dann:  pct set $CT --delete ${UNUSED:-unusedN}"
fi
