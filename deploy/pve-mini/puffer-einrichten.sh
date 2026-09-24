#!/usr/bin/env bash
# Auf pve-mini (Host) als root:  bash puffer-einrichten.sh --probe   (zeigt nur)   ·   bash puffer-einrichten.sh
# E19, Schritt R1 in docs/PUFFER.md: legt den PUFFER an – ein eigenes Volume im Thin-Pool des Mini, im CT 102 unter
# /srv/puffer. Die Pipeline merkt davon noch nichts: /srv/clips zeigt weiter auf pve-big, bis du in R5 umschaltest.
#   1 Bestand (Pool, CT) · 2 Sicherung · 3 CT aus, Volume anlegen · 4 CT an · 5 tune2fs -m 0 · 6 Ordner + Marken
#   · 7 lvm-status-Timer
# Jede Änderung wird angezeigt und erst nach deinem "j" ausgeführt. Beliebig oft wiederholbar: Fertiges wird
# übersprungen. Es wird nichts gelöscht. Sicherung der CT-Konfig: /root/puffer-original/ (erster Lauf, bleibt für
# immer) und /root/puffer-<zeit>/. Rückweg: bash puffer-zurueck.sh (hängt den Puffer aus, löscht nichts).
# Daneben müssen liegen: clip-lvm-status, clip-lvm-status.service, clip-lvm-status.timer (alle aus deploy/pve-mini/).
set -euo pipefail
CT="${CT:-102}"
MP="${MP:-mp1}"                              # Einhängepunkt in der CT-Konfig (mp0 war früher das NFS)
GROESSE_GB="${GROESSE_GB:-96}"
SPEICHER="${SPEICHER:-local-lvm}"            # Proxmox-Speicher auf dem Thin-Pool ...
POOL="${POOL:-pve/data}"                     # ... und der Pool selbst (für lvs)
MAX_PROZENT="${MAX_PROZENT:-90}"             # so voll darf der Pool mit ganz vollem Puffer höchstens werden
ZIEL=/srv/puffer                             # im CT
# = [lager].ordner in config/pipeline.toml (tests/test_deploy_puffer.py prüft, dass beide gleich bleiben)
ORDNER="eingang replays sessions highlights sitzungen musik archiv sicherung"
SPERRE="${SPERRE:-/var/lib/clip-pipeline/pipeline.lock}"   # Pipeline-Sperre im CT (flock)
KONF="${KONF:-/etc/pve/lxc/$CT.conf}"
SBIN="${SBIN:-/usr/local/sbin}"
UNITS="${UNITS:-/etc/systemd/system}"
ORIGINAL="${ORIGINAL:-/root/puffer-original}"
SICH="${SICH:-/root/puffer-$(date +%Y%m%d-%H%M%S)}"
HIER="$(cd "$(dirname "$0")" && pwd)"
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
# Einhängepunkt, der schon auf $ZIEL zeigt, als "mp1 local-lvm:vm-102-disk-1" (leer, wenn keiner)
puffer_mp() {
  aktiv | awk -v z="mp=$ZIEL" '/^mp[0-9]+:/ {
    n = split($2, t, ","); for (i = 2; i <= n; i++) if (t[i] == z) { sub(/:$/, "", $1); print $1, t[1]; exit } }'
}
ct_laeuft() { [ "$(pct status "$CT" | awk '{print $2}')" = running ]; }
zahl() { [[ "${1:-}" =~ ^[0-9]+([.][0-9]+)?$ ]]; }
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
for f in clip-lvm-status clip-lvm-status.service clip-lvm-status.timer; do
  [ -f "$HIER/$f" ] || { echo "$HIER/$f fehlt – bitte alle Dateien aus deploy/pve-mini/ neben dieses Skript legen."; exit 1; }
done
if [ "$PROBE" = 1 ]; then echo "PROBE: Ich zeige nur, was ich tun würde, und ändere nichts."; fi

sag "1/7 Bestand: Thin-Pool $POOL und CT $CT"
VORHANDEN="$(puffer_mp)"
if [ -n "$VORHANDEN" ]; then
  MP="${VORHANDEN%% *}"
  echo "Der Puffer ist schon eingehängt: $MP = ${VORHANDEN#* } -> $ZIEL"
elif aktiv | grep -q "^$MP:"; then
  echo "$MP ist in der CT-Konfig schon für etwas anderes belegt:"; aktiv | grep "^$MP:"
  echo "Ich ändere nichts. Anderen Einhängepunkt wählen, z. B.:  MP=mp2 bash $0"; exit 1
fi
WERTE="$(LC_ALL=C lvs --noheadings --nosuffix --units g -o lv_size,data_percent,metadata_percent "$POOL")"
read -r POOL_GB DATA META <<< "$WERTE"
if ! { zahl "${POOL_GB:-}" && zahl "${DATA:-}" && zahl "${META:-}"; }; then
  echo "lvs $POOL lieferte Unerwartetes: '$WERTE' – ich ändere nichts."; exit 1
fi
echo "Pool $POOL: $POOL_GB GB · belegt: Daten $DATA % · Metadaten $META %"
if [ -z "$VORHANDEN" ]; then
  # Thin-Pool: das Volume belegt erst, was hineingeschrieben wird – gerechnet wird mit dem vollen Puffer
  VOLL="$(awk -v g="$POOL_GB" -v d="$DATA" -v p="$GROESSE_GB" 'BEGIN {printf "%.1f", d + p * 100 / g}')"
  echo "Mit ganz vollem Puffer ($GROESSE_GB GB): $VOLL % (Grenze $MAX_PROZENT %)"
  if awk -v a="$VOLL" -v b="$MAX_PROZENT" 'BEGIN {exit !(a > b)}' \
     || awk -v a="$META" -v b="$MAX_PROZENT" 'BEGIN {exit !(a >= b)}'; then
    echo "Zu knapp: Ein voller Thin-Pool legt ALLE Gäste auf pve-mini lahm. Ich ändere nichts."
    echo "Kleiner anlegen (z. B. GROESSE_GB=64 bash $0) oder erst Platz schaffen – bitte melden."
    exit 1
  fi
fi
LAEUFT=0
if ct_laeuft; then
  LAEUFT=1
  echo "CT $CT läuft · /srv/clips zeigt auf: $(pct exec "$CT" -- readlink /srv/clips 2>/dev/null | tr -d '\r' || true)"
else
  echo "CT $CT ist aus"
fi

sag "2/7 Sicherung der CT-Konfig"
if [ "$PROBE" = 1 ]; then
  echo "   (Probe: würde $KONF nach $SICH/ sichern)"
else
  mkdir -p "$SICH" && cp -a "$KONF" "$SICH/"
  if [ ! -d "$ORIGINAL" ]; then mkdir -p "$ORIGINAL" && cp -a "$KONF" "$ORIGINAL/"; fi
  echo "Sicherung: $SICH   (erster Stand, bleibt: $ORIGINAL)"
fi

# backup=0: nicht ins vzdump-Backup (die Daten sichert der Abgleich ins Lager) · noatime: Lesen schreibt nichts
# · discard: gelöschte Blöcke gehen an den Thin-Pool zurück
WERT="$SPEICHER:$GROESSE_GB,mp=$ZIEL,backup=0,mountoptions=noatime;discard"
sag "3/7 Volume anlegen: $GROESSE_GB GB auf $SPEICHER, im CT unter $ZIEL"
WAERE_AUS=0
if [ -n "$VORHANDEN" ]; then
  echo "schon da – übersprungen"
else
  if [ "$LAEUFT" = 1 ] && ! pct exec "$CT" -- runuser -u pipeline -- flock -n "$SPERRE" true; then
    echo "Im CT läuft gerade ein Pipeline-Schritt (Sperre $SPERRE belegt) – bitte später nochmal."; exit 1
  fi
  if [ "$LAEUFT" = 1 ]; then
    FRAGE="CT $CT herunterfahren (Bot und Pipeline ca. 1 min weg – nicht während eines Spielabends), Volume anlegen, CT wieder starten?"
  else
    FRAGE="Volume anlegen? (CT $CT ist schon aus – ob er danach starten soll, frage ich gleich)"
  fi
  if ! frage "$FRAGE"; then
    echo "Abgebrochen – nichts verändert."; exit 1
  fi
  if [ "$LAEUFT" = 1 ]; then
    if [ "$PROBE" = 1 ]; then WAERE_AUS=1; else HERUNTERGEFAHREN=1; fi
    tu pct shutdown "$CT" --timeout 180
  fi
  tu pct set "$CT" "--$MP" "$WERT"
  if [ "$PROBE" = 0 ]; then aktiv | grep "^$MP:"; fi
fi

sag "4/7 CT $CT starten"
CT_AN=1
if [ "$WAERE_AUS" = 0 ] && ct_laeuft; then
  echo "läuft"
elif [ "$HERUNTERGEFAHREN" = 1 ] || [ "$WAERE_AUS" = 1 ]; then
  tu pct start "$CT"                         # selbst heruntergefahren – das deckt die Frage aus Schritt 3 ab
elif frage "CT $CT ist aus (vielleicht absichtlich, z. B. Wartung). Starten? Ohne laufenden CT entfällt Schritt 6."; then
  tu pct start "$CT"
else
  CT_AN=0; echo "übersprungen – CT $CT bleibt aus"
fi

sag "5/7 Reserve für root abschalten (tune2fs -m 0): sonst wären 5 % des Puffers nur für root nutzbar"
VOL="$(puffer_mp | awk '{print $2}')"
if [ -z "$VOL" ]; then
  echo "   (Probe: das Volume gibt es noch nicht – danach: tune2fs -m 0 <Gerät des Volumes>)"
else
  GERAET="$(pvesm path "$VOL")"
  RESERVE="$(tune2fs -l "$GERAET" 2>/dev/null | awk -F: '/^Reserved block count/ {gsub(/[ \t]/, "", $2); print $2}' || true)"
  if [ "$RESERVE" = 0 ]; then echo "$GERAET: schon 0 – übersprungen"
  elif [ -z "$RESERVE" ]; then echo "$GERAET: tune2fs -l ging nicht (kein ext4?) – übersprungen, bitte melden"
  elif frage "tune2fs -m 0 $GERAET?"; then tu tune2fs -m 0 "$GERAET"
  else echo "übersprungen"; fi
fi

sag "6/7 Im CT: Ordner und Marken in $ZIEL (Besitzer pipeline)"
IM_CT="set -e
findmnt -rn $ZIEL >/dev/null || { echo '$ZIEL ist nicht eingehängt – ich lege nichts auf der CT-Platte an.'; exit 1; }
[ ! -e $ZIEL/.clip-lager ] || { echo '$ZIEL/.clip-lager gefunden – das wäre das LAGER. Nichts verändert, bitte melden.'; exit 1; }
cd $ZIEL
install -d -o pipeline -g pipeline -m 755 . $ORDNER
for m in .clip-puffer .clip-speicher; do [ -e \$m ] || install -o pipeline -g pipeline -m 644 /dev/null \$m; done
ls -la"
printf '%s\n' "$IM_CT" | sed 's/^/   | /'
# .clip-puffer: "hier ist der Puffer" (nie im Lager) · .clip-speicher: prüfen Pipeline und Gaming-PC (ab R5)
if [ "$CT_AN" = 0 ]; then
  echo "übersprungen – CT $CT ist aus (Schritt 4). Später, wenn er läuft: bash $0"
elif frage "Das im CT ausführen?"; then
  if [ "$PROBE" = 0 ]; then pct exec "$CT" -- sh -c "$IM_CT"; fi
else
  echo "übersprungen"
fi

sag "7/7 Füllstand des Pools für die Morgenprüfung: clip-lvm-status alle 15 min -> /mnt/big/lvm-status.txt"
if frage "clip-lvm-status nach $SBIN und den Timer nach $UNITS installieren und einschalten?"; then
  tu install -m 755 "$HIER/clip-lvm-status" "$SBIN/clip-lvm-status"
  tu install -m 644 "$HIER/clip-lvm-status.service" "$HIER/clip-lvm-status.timer" "$UNITS/"
  tu systemctl daemon-reload
  tu systemctl enable --now clip-lvm-status.timer
  tu systemctl start clip-lvm-status.service
  if [ "$PROBE" = 0 ]; then cat /mnt/big/lvm-status.txt || echo "(noch keine Datei – journalctl -u clip-lvm-status)"; fi
else
  echo "übersprungen"
fi

if [ "$PROBE" = 1 ]; then
  sag "Probe fertig – nichts verändert. Echt:  bash $0"
else
  sag "Fertig. Puffer: CT $CT, $MP -> $ZIEL"
  echo "Die Pipeline arbeitet weiter auf pve-big, bis du in R5 umschaltest (docs/PUFFER.md)."
  echo "Nachsehen:  pct exec $CT -- ls -la $ZIEL   ·   cat /mnt/big/lvm-status.txt"
  echo "Rückweg:    bash $HIER/puffer-zurueck.sh   (hängt den Puffer aus, löscht nichts)"
fi
