#!/usr/bin/env bash
# Im CT als root:  bash benutzer-stilllegen.sh <name> --probe   (zeigt nur)   ·   bash benutzer-stilllegen.sh <name>
# Mehrbenutzer (docs/MEHRBENUTZER.md): schaltet die Dienste eines Freundes aus – seinen Bot, seine Timer (auch das
# Abholen aus dem Briefkasten) und Schritte, die gerade laufen (ein halb fertiges Match holt sein nächster Lauf nach, ein
# halber Download setzt beim nächsten Abholen fort, ein halber Lager-Lauf hinterlässt nur unbestätigte Reste; eine offene
# Einladung verfällt). Ohne laufenden scan-Timer nimmt ihn auch der Lager-Rundgang nicht mehr mit. Seine Daten, sein
# Benutzer und seine Zugänge bleiben, gelöscht wird nichts; sein Fach auf dem vServer und sein Ordner im Lager auch.
# Wieder an: bash benutzer-anlegen.sh <name> (überspringt Fertiges).
set -euo pipefail
BENUTZER_DIR="${BENUTZER_DIR:-/var/lib/clip-benutzer}"
HIER="$(cd "$(dirname "$0")" && pwd)"
NAME=""
PROBE=0
aufruf() { echo "Aufruf: bash $0 <name> [--probe]"; exit 2; }
while [ $# -gt 0 ]; do
  case "$1" in
    --probe) PROBE=1 ;;
    -*) aufruf ;;
    *) [ -z "$NAME" ] || aufruf; NAME="$1" ;;
  esac
  shift
done
[[ "$NAME" =~ ^[a-z][a-z0-9-]{1,26}$ ]] || aufruf

sag() { printf '\n== %s\n' "$*"; }
tu() {
  local a z=""
  for a in "$@"; do case "$a" in *[!A-Za-z0-9_./:=,@%+-]*) z="$z '$a'" ;; *) z="$z $a" ;; esac; done
  printf '   $%s\n' "$z"
  [ "$PROBE" = 1 ] || "$@"
}
# Läuft eine Einheit gerade? Auch ein Schritt (oneshot), der noch „activating“ ist – is-active -q sieht den nicht
laeuft() {
  case "$(systemctl is-active "$1" 2>/dev/null || true)" in active|activating|deactivating|reloading|refreshing) return 0 ;; esac
  return 1
}
frage() {
  if [ "$PROBE" = 1 ]; then printf '   ? %s  -> Probe: angenommen ja\n' "$1"; return 0; fi
  local antwort=""
  read -r -p "   ? $1 [j/N] " antwort || true
  case "$antwort" in j|J|ja|Ja|JA) return 0 ;; *) return 1 ;; esac
}

[ "$(id -u)" = 0 ] || { echo "Bitte als root im CT ausführen."; exit 1; }
if [ "$PROBE" = 1 ]; then echo "PROBE: Ich zeige nur, was ich tun würde, und ändere nichts."; fi
if ! systemctl cat clip-freund-bot@.service >/dev/null 2>&1; then
  echo "Die Dienst-Vorlagen der Freunde sind nicht installiert – es läuft nichts von $NAME. Nichts zu tun."; exit 0
fi

sag "Dienste von $NAME"
AN=()
for e in "clip-freund-bot@$NAME.service" "clip-freund-scan@$NAME.timer" "clip-freund-abend@$NAME.timer" \
         "clip-freund-abholen@$NAME.timer"; do
  if systemctl is-enabled -q "$e" 2>/dev/null || laeuft "$e"; then AN+=("$e"); fi
done
LAEUFT=()
for e in scan abend abholen einrichten pruefen koppeln lager; do
  if laeuft "clip-freund-$e@$NAME.service"; then LAEUFT+=("clip-freund-$e@$NAME.service"); fi
done
if [ "${#AN[@]}" = 0 ] && [ "${#LAEUFT[@]}" = 0 ]; then
  echo "schon aus – nichts zu tun. Seine Daten liegen weiter in $BENUTZER_DIR/$NAME."; exit 0
fi
echo "an: ${AN[*]:-–}"
echo "läuft gerade: ${LAEUFT[*]:-–}"
if ! frage "Alle Dienste von $NAME ausschalten? Seine Daten, sein Benutzer und seine Zugänge bleiben"; then
  echo "Abgebrochen – nichts verändert."; exit 1
fi
if [ "${#AN[@]}" -gt 0 ]; then tu systemctl disable --now "${AN[@]}"; fi
if [ "${#LAEUFT[@]}" -gt 0 ]; then tu systemctl stop "${LAEUFT[@]}"; fi

if [ "$PROBE" = 1 ]; then sag "Probe fertig – nichts verändert. Echt:  bash $0 $NAME"; exit 0; fi
sag "Fertig: $NAME ist ausgeschaltet. Seine Daten liegen weiter in $BENUTZER_DIR/$NAME."
echo "Wieder an:  bash $HIER/benutzer-anlegen.sh $NAME"
if [ -d "$BENUTZER_DIR/$NAME/briefkasten" ]; then
  echo "Sein Fach im Briefkasten bleibt offen (sein PC lädt weiter hoch). Ganz zu, auf dem vServer:"
  echo "  bash /root/briefkasten/briefkasten-freund.sh $NAME --sperren"
fi
if [ -d "$BENUTZER_DIR/$NAME/lager" ]; then
  echo "Sein Ordner im Lager (freunde/$NAME auf pve-big) bleibt; mitfahren tut er erst wieder, wenn seine Dienste an sind."
fi
