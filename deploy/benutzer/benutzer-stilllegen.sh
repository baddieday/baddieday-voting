#!/usr/bin/env bash
# Im CT als root:  bash benutzer-stilllegen.sh <name> --probe   (zeigt nur)   ·   bash benutzer-stilllegen.sh <name>
# Mehrbenutzer (docs/MEHRBENUTZER.md): schaltet die Dienste eines Freundes aus – seinen Bot, seine Timer und Schritte,
# die gerade laufen (ein halb fertiges Match holt sein nächster Lauf nach). Seine Daten, sein Benutzer und seine Zugänge
# bleiben, gelöscht wird nichts. Wieder an: bash benutzer-anlegen.sh <name> (überspringt Fertiges, fragt nichts neu).
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
for e in "clip-freund-bot@$NAME.service" "clip-freund-scan@$NAME.timer" "clip-freund-abend@$NAME.timer"; do
  if systemctl is-enabled -q "$e" 2>/dev/null || systemctl is-active -q "$e" 2>/dev/null; then AN+=("$e"); fi
done
LAEUFT=()
for e in scan abend einrichten pruefen; do
  if systemctl is-active -q "clip-freund-$e@$NAME.service" 2>/dev/null; then LAEUFT+=("clip-freund-$e@$NAME.service"); fi
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
