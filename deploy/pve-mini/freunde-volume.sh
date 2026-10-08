#!/usr/bin/env bash
# Auf pve-mini (Host) als root:  bash freunde-volume.sh --probe   (zeigt nur)   ·   bash freunde-volume.sh [--groesse GB]
# Mehrbenutzer (docs/MEHRBENUTZER.md, „Einmal für alle Freunde: Speicher“): EIN Volume für alle Freunde im Thin-Pool des
# Mini, im CT 102 unter /var/lib/clip-benutzer (Standard 100 GB). Getrennt von der CT-Platte (dort liegt deine
# Datenbank) und von deinem Puffer: Läuft es voll, trifft das nur die Freunde. Bis du einen Freund anlegst, merkt die
# Pipeline nichts davon.
#   1 Bestand (Pool, Puffer, CT, Ziel im CT) · 2 Volume anlegen (CT ca. 1 min aus) · 3 Wurzel im CT: root, 0711
# Die Pool-Grenze rechnet mit ganz vollem Freunde-Volume UND ganz vollem Puffer (beide füllen sich mit Videos).
# Jede Änderung wird angezeigt und erst nach deinem "j" ausgeführt. Beliebig oft wiederholbar: Fertiges wird
# übersprungen; ein früher angelegtes, nur ausgehängtes Freunde-Volume wird wieder eingehängt statt ein neues
# angelegt. Es wird nichts gelöscht. Vor jeder Änderung: Sicherung der CT-Konfig nach /root/freunde-volume/<zeit>/
# (erster Stand bleibt in .../original/) und Rückweg-Skript /root/freunde-volume/zurueck.sh (hängt nur aus).
set -euo pipefail
CT="${CT:-102}"
MP="${MP:-mp2}"                              # mp1 = Puffer; nie mp0 – rette-clips-start.sh entfernt mp0
GROESSE_GB="${GROESSE_GB:-100}"
SPEICHER="${SPEICHER:-local-lvm}"            # Proxmox-Speicher auf dem Thin-Pool ...
POOL="${POOL:-pve/data}"                     # ... und der Pool selbst (für lvs)
MAX_PROZENT="${MAX_PROZENT:-90}"             # so voll darf der Pool mit vollem Freunde-Volume und Puffer höchstens werden
ZIEL=/var/lib/clip-benutzer                  # im CT – fest: Dienst-Vorlagen und Instanz-Modus erwarten die Freunde hier
PUFFER=/srv/puffer                           # im CT – dein Puffer (zählt bei der Pool-Grenze voll)
SPERRE="${SPERRE:-/var/lib/clip-pipeline/pipeline.lock}"   # Pipeline-Sperre im CT (flock) – gilt auch für Freunde
KONF="${KONF:-/etc/pve/lxc/$CT.conf}"
ABLAGE="${ABLAGE:-/root/freunde-volume}"     # Sicherungen, Rückweg-Skript, Name des Volumes
ZEIT="$(date +%Y%m%d-%H%M%S)"
PROBE=0
GROESSE_GESETZT=0
aufruf() { echo "Aufruf: bash $0 [--probe] [--groesse GB]"; exit 2; }
while [ $# -gt 0 ]; do
  case "$1" in
    --probe) PROBE=1 ;;
    --groesse) [ $# -ge 2 ] || aufruf; GROESSE_GB="$2"; GROESSE_GESETZT=1; shift ;;
    --groesse=*) GROESSE_GB="${1#*=}"; GROESSE_GESETZT=1 ;;
    *) aufruf ;;
  esac
  shift
done
[[ "$GROESSE_GB" =~ ^[1-9][0-9]{0,4}$ ]] || { echo "Größe in ganzen GB, z. B. --groesse 60"; exit 2; }
[[ "$MP" =~ ^mp[1-9][0-9]{0,2}$ ]] || { echo "MP muss mp1, mp2, … sein (mp0 entfernt rette-clips-start.sh)"; exit 2; }

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
# Einhängepunkt, der auf $1 zeigt, als "mp2 local-lvm:vm-102-disk-2" (leer, wenn keiner)
mp_auf() {
  aktiv | awk -v z="mp=$1" '/^mp[0-9]+:/ {
    n = split($2, t, ","); for (i = 2; i <= n; i++) if (t[i] == z) { sub(/:$/, "", $1); print $1, t[1]; exit } }'
}
# Früher hier angelegtes Freunde-Volume, das jetzt nur ausgehängt ist (unusedN) – leer, wenn keins
altes_volume() {
  [ -s "$ABLAGE/volume" ] || return 0
  aktiv | awk -F': ' -v v="$(head -n 1 "$ABLAGE/volume")" '$1 ~ /^unused[0-9]+$/ && $2 == v {print v; exit}'
}
ct_laeuft() { [ "$(pct status "$CT" | awk '{print $2}')" = running ]; }
# Zustand aus $LESEN in Worten
klartext() {
  case "$1" in
    fehlt) echo "gibt es noch nicht" ;;
    leer) echo "leerer Ordner auf der CT-Platte" ;;
    voll) echo "Ordner auf der CT-Platte, NICHT leer" ;;
    "eingehaengt root 711 getrennt") echo "eingehängt, eigener Speicher, root, 0711" ;;
    eingehaengt*getrennt) local besitzer rechte; read -r _ besitzer rechte _ <<< "$1"
      echo "eingehängt, eigener Speicher, Besitzer $besitzer, Rechte $rechte" ;;
    eingehaengt*) echo "eingehängt, aber auf demselben Speicher wie / oder $PUFFER" ;;
    "") echo "(nachsehen ging nicht)" ;;
    *) echo "unbekannter Aufbau" ;;
  esac
}
zahl() { [[ "${1:-}" =~ ^[0-9]+([.][0-9]+)?$ ]]; }
# Skript im CT mit Ziel und Puffer als $1/$2 (Ausgabe ohne \r des Terminals)
im_ct_sh() { pct exec "$CT" -- sh -c "$1" sh "$ZIEL" "$PUFFER" | tr -d '\r'; }
# shellcheck disable=SC2016  # beide Skripte laufen im CT mit eigenen Argumenten – $1/$2 sind dort Ziel und Puffer
LESEN='z=$1
if [ -L "$z" ]; then echo anders
elif findmnt -rn "$z" >/dev/null 2>&1; then
  g=getrennt
  for p in / "$2"; do
    if [ -e "$p" ] && [ "$(stat -c %d "$p")" = "$(stat -c %d "$z")" ]; then g=gleich; fi
  done
  echo "eingehaengt $(stat -c "%U %a" "$z") $g"
elif [ ! -e "$z" ]; then echo fehlt
elif [ ! -d "$z" ]; then echo anders
elif [ -n "$(ls -A "$z")" ]; then echo voll
else echo leer
fi'
# shellcheck disable=SC2016
AENDERN='set -e
z=$1
findmnt -rn "$z" >/dev/null || { echo "$z ist nicht eingehängt – ich ändere nichts auf der CT-Platte."; exit 1; }
for p in / "$2"; do
  if [ -e "$p" ] && [ "$(stat -c %d "$p")" = "$(stat -c %d "$z")" ]; then
    echo "$z liegt auf demselben Speicher wie $p – ich ändere nichts, bitte melden."; exit 1
  fi
done
chown root:root "$z"
chmod 711 "$z"
ls -ld "$z"'

# Rückweg-Skript: findet das Volume selbst (Einhängepunkt auf $ZIEL), hängt es nur aus – Proxmox behält es als
# unusedN, die Daten bleiben. Ein neuer Lauf von freunde-volume.sh hängt genau dieses Volume wieder ein.
rueckweg() {
  printf '#!/usr/bin/env bash\n'
  printf '# Rückweg zu freunde-volume.sh: hängt das Freunde-Volume (%s im CT %s) aus. Es wird NICHTS gelöscht.\n' \
    "$ZIEL" "$CT"
  printf '# Auf pve-mini als root:  bash %s --probe   (zeigt nur)   ·   bash %s\n' "$ABLAGE/zurueck.sh" "$ABLAGE/zurueck.sh"
  printf 'set -euo pipefail\n'
  printf 'CT=%q\nKONF=%q\nZIEL=%q\nSPERRE=%q\nABLAGE=%q\n' "$CT" "$KONF" "$ZIEL" "$SPERRE" "$ABLAGE"
  cat <<'RUECKWEG'
PROBE=0
case "${1:-}" in
  --probe) PROBE=1 ;;
  "") ;;
  *) echo "Aufruf: bash $0 [--probe]"; exit 2 ;;
esac
sag() { printf '\n== %s\n' "$*"; }
tu() {
  local a z=""
  for a in "$@"; do case "$a" in *[!A-Za-z0-9_./:=,@%+-]*) z="$z '$a'" ;; *) z="$z $a" ;; esac; done
  printf '   $%s\n' "$z"
  [ "$PROBE" = 1 ] || "$@"
}
frage() {
  if [ "$PROBE" = 1 ]; then printf '   ? %s  -> Probe: angenommen ja\n' "$1"; return 0; fi
  local antwort=""
  read -r -p "   ? $1 [j/N] " antwort || true
  case "$antwort" in j|J|ja|Ja|JA) return 0 ;; *) return 1 ;; esac
}
aktiv() { awk '/^\[/ {exit} {print}' "$KONF"; }
ziel_mp() {
  aktiv | awk -v z="mp=$ZIEL" '/^mp[0-9]+:/ {
    n = split($2, t, ","); for (i = 2; i <= n; i++) if (t[i] == z) { sub(/:$/, "", $1); print $1, t[1]; exit } }'
}
ct_laeuft() { [ "$(pct status "$CT" | awk '{print $2}')" = running ]; }
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

sag "1/2 Bestand"
VORHANDEN="$(ziel_mp)"
if [ -z "$VORHANDEN" ]; then
  echo "Kein Einhängepunkt auf $ZIEL in CT $CT – das Freunde-Volume ist schon ausgehängt. Nichts zu tun."; exit 0
fi
MP="${VORHANDEN%% *}"; VOL="${VORHANDEN#* }"
echo "Freunde-Volume: $MP = $VOL -> $ZIEL"
ct_laeuft || { echo "CT $CT ist aus – bitte erst: pct start $CT (ich muss im CT nachsehen), dann nochmal."; exit 1; }
# Laufen noch Freunde, hinge ihnen das Volume unter den Füßen weg – erst stilllegen (ihre Daten bleiben)
if ! DIENSTE="$(pct exec "$CT" -- systemctl list-units --no-legend --plain --state=active,activating,reloading \
     'clip-freund-*' | tr -d '\r' | awk '{print $1}' | tr '\n' ' ')"; then
  echo "Im CT nachsehen ging nicht (laufen Dienste von Freunden?) – ich ändere nichts, bitte melden."; exit 1
fi
if [ -n "${DIENSTE// /}" ]; then
  echo "Im CT laufen noch Dienste von Freunden – ich ändere nichts. Erst stilllegen (ihre Daten bleiben):"
  echo "   pct exec $CT -- systemctl disable --now ${DIENSTE% }"
  exit 1
fi
if ! pct exec "$CT" -- runuser -u pipeline -- flock -n "$SPERRE" true; then
  echo "Im CT läuft gerade ein Pipeline-Schritt (Sperre $SPERRE belegt) – bitte später nochmal."; exit 1
fi

sag "2/2 Aushängen – das Volume bleibt als unusedN erhalten"
FRAGE="CT $CT herunterfahren (ca. 1 min – nicht während eines Spielabends), $MP aushängen (Daten bleiben im Volume"
if ! frage "$FRAGE $VOL) und CT wieder starten?"; then
  echo "Abgebrochen – nichts verändert."; exit 1
fi
if [ "$PROBE" = 0 ]; then
  SICH="$ABLAGE/zurueck-$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$SICH" && cp -a "$KONF" "$SICH/" && echo "Sicherung der CT-Konfig: $SICH"
  HERUNTERGEFAHREN=1
fi
tu pct shutdown "$CT" --timeout 180
tu pct set "$CT" --delete "$MP"
tu pct start "$CT"
if [ "$PROBE" = 0 ]; then
  if aktiv | grep -qF "$VOL"; then aktiv | grep -F "$VOL"
  else echo "ACHTUNG: $VOL steht nicht mehr in der CT-Konfig – bitte melden (pvesm list ${VOL%%:*})."; fi
fi

if [ "$PROBE" = 1 ]; then sag "Probe fertig – nichts verändert. Echt:  bash $0"; else sag "Fertig."; fi
echo "Die Daten der Freunde bleiben im Volume $VOL. Wieder einhängen: freunde-volume.sh noch einmal ausführen –"
echo "es nimmt genau dieses Volume (kein neues, nichts gelöscht)."
RUECKWEG
}
# Rückweg-Skript schreiben, falls es fehlt (ein vorhandenes bleibt, wie es ist)
rueckweg_bereit() {
  if [ -f "$ABLAGE/zurueck.sh" ]; then echo "Rückweg-Skript: $ABLAGE/zurueck.sh (schon da)"
  elif [ "$PROBE" = 1 ]; then echo "   (Probe: würde das Rückweg-Skript $ABLAGE/zurueck.sh schreiben)"
  else
    mkdir -p "$ABLAGE" && rueckweg > "$ABLAGE/zurueck.sh" && chmod 700 "$ABLAGE/zurueck.sh"
    echo "Rückweg-Skript geschrieben: $ABLAGE/zurueck.sh (hängt nur aus, löscht nichts)"
  fi
}
# Name des Volumes merken – so hängt ein späterer Lauf nach dem Rückweg genau dieses wieder ein
merke_volume() {
  [ "$PROBE" = 0 ] || return 0
  [ "$(cat "$ABLAGE/volume" 2>/dev/null || true)" = "$1" ] || { mkdir -p "$ABLAGE" && printf '%s\n' "$1" > "$ABLAGE/volume"; }
}
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

sag "1/3 Bestand: CT $CT, Thin-Pool $POOL und Puffer"
ct_laeuft || { echo "CT $CT ist aus – bitte erst: pct start $CT (ich muss im CT nachsehen), dann nochmal."; exit 1; }
VORHANDEN="$(mp_auf "$ZIEL")"
ALT_VOL=""
VOL=""
if [ -n "$VORHANDEN" ]; then
  MP="${VORHANDEN%% *}"; VOL="${VORHANDEN#* }"
  echo "Das Freunde-Volume ist schon eingehängt: $MP = $VOL -> $ZIEL"
else
  ALT_VOL="$(altes_volume)"
  [ -z "$ALT_VOL" ] || echo "Früher angelegtes Freunde-Volume gefunden: $ALT_VOL (nur ausgehängt) – ich hänge es wieder ein."
  if aktiv | grep -q "^$MP:"; then
    echo "$MP ist in der CT-Konfig schon für etwas anderes belegt:"; aktiv | grep "^$MP:"
    echo "Ich ändere nichts. Anderen Einhängepunkt wählen, z. B.:  MP=mp3 bash $0"; exit 1
  fi
fi
# Im CT nachsehen (nur lesen): Ohne Volume darf $ZIEL nur fehlen oder leer sein – sonst verdeckte das Volume etwas
ZUSTAND="$(im_ct_sh "$LESEN")" || ZUSTAND=""
echo "Im CT: $ZIEL – $(klartext "$ZUSTAND")"
if [ -z "$VORHANDEN" ]; then
  case "$ZUSTAND" in
    fehlt|leer) ;;
    voll) echo "$ZIEL liegt auf der CT-Platte und ist nicht leer – das Volume würde den Inhalt verdecken."
          echo "Ich ändere nichts. Bitte melden:  pct exec $CT -- ls -la $ZIEL"; exit 1 ;;
    eingehaengt*) echo "Auf $ZIEL ist schon etwas anderes eingehängt (nicht aus der CT-Konfig)."
          echo "Ich ändere nichts – bitte melden."; exit 1 ;;
    *) echo "Unbekannter Aufbau im CT – ich ändere nichts. Bitte melden:  pct exec $CT -- ls -la $ZIEL"; exit 1 ;;
  esac
fi
if [ -z "$VORHANDEN" ] && [ -z "$ALT_VOL" ]; then
  WERTE="$(LC_ALL=C lvs --noheadings --nosuffix --units g -o lv_size,data_percent,metadata_percent "$POOL")"
  read -r POOL_GB DATA META <<< "$WERTE"
  if ! { zahl "${POOL_GB:-}" && zahl "${DATA:-}" && zahl "${META:-}"; }; then
    echo "lvs $POOL lieferte Unerwartetes: '$WERTE' – ich ändere nichts."; exit 1
  fi
  echo "Pool $POOL: $POOL_GB GB · belegt: Daten $DATA % · Metadaten $META %"
  # Der Puffer füllt sich mit Videos: was ihm noch fehlt, zählt wie belegt
  PUFFER_REST=0
  P="$(mp_auf "$PUFFER")"
  if [ -n "$P" ]; then
    W="$(LC_ALL=C lvs --noheadings --nosuffix --units g -o lv_size,data_percent "$(pvesm path "${P#* }")")"
    read -r P_GB P_PROZENT <<< "$W"
    if ! { zahl "${P_GB:-}" && zahl "${P_PROZENT:-}"; }; then
      echo "lvs für den Puffer (${P#* }) lieferte Unerwartetes: '$W' – ich ändere nichts."; exit 1
    fi
    PUFFER_REST="$(awk -v g="$P_GB" -v p="$P_PROZENT" 'BEGIN {printf "%.1f", g * (100 - p) / 100}')"
    echo "Puffer $PUFFER: $P_GB GB, belegt $P_PROZENT % – kann noch $PUFFER_REST GB wachsen"
  else
    echo "Kein Puffer auf $PUFFER in CT $CT – zählt mit 0 GB"
  fi
  # Thin-Pool: Volumes belegen erst, was hineingeschrieben wird – gerechnet wird mit beiden ganz voll
  VOLL="$(awk -v g="$POOL_GB" -v d="$DATA" -v p="$GROESSE_GB" -v r="$PUFFER_REST" \
    'BEGIN {printf "%.1f", d + (p + r) * 100 / g}')"
  echo "Mit ganz vollem Freunde-Volume ($GROESSE_GB GB) und ganz vollem Puffer: $VOLL % (Grenze $MAX_PROZENT %)"
  if awk -v a="$META" -v b="$MAX_PROZENT" 'BEGIN {exit !(a >= b)}'; then
    echo "Zu knapp: Die Metadaten des Pools sind fast voll – ein voller Thin-Pool legt ALLE Gäste auf pve-mini lahm,"
    echo "auch deine Pipeline. Ich ändere nichts. Erst Platz schaffen – bitte melden."; exit 1
  fi
  if awk -v a="$VOLL" -v b="$MAX_PROZENT" 'BEGIN {exit !(a > b)}'; then
    echo "Zu knapp: Ein voller Thin-Pool legt ALLE Gäste auf pve-mini lahm – auch deine Pipeline. Ich ändere nichts."
    PASST="$(awk -v g="$POOL_GB" -v d="$DATA" -v r="$PUFFER_REST" -v m="$MAX_PROZENT" \
      'BEGIN {x = (m - d) * g / 100 - r; printf "%d", (x > 0 ? x : 0)}')"
    if [ "$PASST" -ge 10 ]; then echo "So viel passt:  bash $0 --groesse $PASST"
    else echo "Dafür ist im Pool kein Platz – erst Platz schaffen, bitte melden."; fi
    exit 1
  fi
fi

sag "2/3 Volume für die Freunde, im CT unter $ZIEL"
NEU_ANGELEGT=0
if [ -n "$VORHANDEN" ]; then
  echo "schon da – übersprungen"
else
  if ! pct exec "$CT" -- runuser -u pipeline -- flock -n "$SPERRE" true; then
    echo "Im CT läuft gerade ein Pipeline-Schritt (Sperre $SPERRE belegt) – bitte später nochmal."; exit 1
  fi
  # backup=0: nicht ins vzdump-Backup (wie der Puffer) · noatime: Lesen schreibt nichts · discard: Gelöschtes geht
  # an den Pool zurück. Ein früheres Volume wird wieder eingehängt (STORAGE:NAME), sonst ein neues (STORAGE:GB).
  if [ -n "$ALT_VOL" ]; then
    WERT="$ALT_VOL,mp=$ZIEL,backup=0,mountoptions=noatime;discard"
    WAS="das alte Freunde-Volume $ALT_VOL wieder einhängen"
  else
    WERT="$SPEICHER:$GROESSE_GB,mp=$ZIEL,backup=0,mountoptions=noatime;discard"
    WAS="Volume anlegen ($GROESSE_GB GB)"
    NEU_ANGELEGT=1
  fi
  if ! frage "CT $CT herunterfahren (Bot und Pipeline ca. 1 min weg – nicht während eines Spielabends), $WAS, CT wieder starten?"; then
    echo "Abgebrochen – nichts verändert."; exit 1
  fi
  if [ "$PROBE" = 1 ]; then
    echo "   (Probe: würde die CT-Konfig vorher nach $ABLAGE/$ZEIT/ sichern)"
  else
    mkdir -p "$ABLAGE/$ZEIT" && cp -a "$KONF" "$ABLAGE/$ZEIT/"
    if [ ! -d "$ABLAGE/original" ]; then mkdir -p "$ABLAGE/original" && cp -a "$KONF" "$ABLAGE/original/"; fi
    echo "Sicherung der CT-Konfig: $ABLAGE/$ZEIT/   (erster Stand, bleibt: $ABLAGE/original/)"
  fi
  rueckweg_bereit                            # vor der Änderung – gilt auch, wenn danach etwas schiefgeht
  [ "$PROBE" = 1 ] || HERUNTERGEFAHREN=1
  tu pct shutdown "$CT" --timeout 180
  tu pct set "$CT" "--$MP" "$WERT"
  if [ "$PROBE" = 0 ]; then
    NEU="$(mp_auf "$ZIEL")"
    [ -n "$NEU" ] || { echo "Das Volume steht nicht in der CT-Konfig – bitte melden (pct config $CT)."; exit 1; }
    MP="${NEU%% *}"; VOL="${NEU#* }"
    aktiv | grep "^$MP:"
    merke_volume "$VOL"
  fi
  if [ "$NEU_ANGELEGT" = 1 ]; then
    # Reserve für root aus (sonst 5 % weniger Platz für die Freunde) – geht nur, solange der CT aus ist (ext4-MMP,
    # docs/PUFFER.md R1). Ein früheres Volume hat das schon.
    if [ "$PROBE" = 1 ]; then GERAET="<Gerät des neuen Volumes>"; else GERAET="$(pvesm path "$VOL")"; fi
    tu tune2fs -m 0 "$GERAET" || echo "tune2fs ging nicht – die Freunde haben dann 5 % weniger Platz (unkritisch)."
  fi
  tu pct start "$CT"
fi

sag "3/3 Im CT: $ZIEL gehört root, Rechte 0711 – jeder Freund kommt nur in seinen eigenen Ordner"
zeige_aendern() { printf '%s\n' "$AENDERN" | sed 's/^/   | /'; }
if [ "$PROBE" = 1 ] && [ -z "$VORHANDEN" ]; then
  zeige_aendern
  echo "   (Probe: das Volume ist noch nicht eingehängt – das kommt gleich nach Schritt 2)"
else
  ZUSTAND="$(im_ct_sh "$LESEN")" || ZUSTAND=""
  case "$ZUSTAND" in
    "eingehaengt root 711 getrennt") echo "schon richtig – übersprungen" ;;
    eingehaengt*getrennt)
      zeige_aendern
      if frage "Das im CT ausführen?"; then
        if [ "$PROBE" = 0 ]; then im_ct_sh "$AENDERN" || { echo "Im CT ging das nicht – bitte melden."; exit 1; }; fi
      else
        echo "übersprungen"
      fi ;;
    eingehaengt*) echo "$ZIEL liegt auf demselben Speicher wie / oder $PUFFER – ich ändere nichts, bitte melden."; exit 1 ;;
    *) echo "$ZIEL ist im CT nicht eingehängt ($(klartext "$ZUSTAND")) – ich ändere nichts, bitte melden."
       exit 1 ;;
  esac
fi

if [ -n "$VORHANDEN" ]; then
  merke_volume "$VOL"
  rueckweg_bereit
fi
if [ "$GROESSE_GESETZT" = 1 ] && [ "$NEU_ANGELEGT" = 0 ]; then
  echo "--groesse gilt nur für ein neues Volume. Größer machen geht ohne Verlust (nur wachsen, vorher lvs $POOL):"
  echo "   pct resize $CT $MP ${GROESSE_GB}G"
fi
if [ "$PROBE" = 1 ]; then
  sag "Probe fertig – nichts verändert. Echt:  bash $0"
else
  sag "Fertig. Freunde-Volume: CT $CT, $MP -> $ZIEL"
  echo "Bis du einen Freund anlegst, merkt die Pipeline nichts davon. Füllt sich der Pool, warnt wie bisher deine"
  echo "Morgenprüfung."
  echo "Nachsehen:  pct config $CT | grep clip-benutzer   ·   pct exec $CT -- ls -ld $ZIEL"
  echo "Rückweg:    bash $ABLAGE/zurueck.sh   (hängt das Volume nur aus, löscht nichts)"
fi
