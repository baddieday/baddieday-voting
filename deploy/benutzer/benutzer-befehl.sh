#!/usr/bin/env bash
# Im CT als root:  bash benutzer-befehl.sh <name> <pipeline-befehl …>
#   z. B.  bash benutzer-befehl.sh max process 2026-10-08_20-15-33   (ein Match mit Status fehler nachholen)
#          bash benutzer-befehl.sh max status
# Mehrbenutzer (docs/MEHRBENUTZER.md): EIN pipeline-Befehl als dieser Freund in seiner Instanz – in derselben Sandbox
# wie seine Dienste. Die Eigenschaften kommen Zeile für Zeile aus dem Sandbox-Block der installierten Vorlage
# clip-freund-bot@.service (sein Name statt %i) und gehen an systemd-run: eigener Benutzer, nur sein Ordner, von dir
# nur die Sperrdatei und das Startwissen. Ausgabe und Exit-Code wie bei pipeline selbst. Ohne Sandbox startet nichts.
set -euo pipefail
BENUTZER_DIR="${BENUTZER_DIR:-/var/lib/clip-benutzer}"
PROD="${PROD:-/opt/clip-pipeline}"
UNITS="${UNITS:-/etc/systemd/system}"
if [ $# -lt 2 ]; then
  echo "Aufruf: bash $0 <name> <pipeline-befehl …>   z. B.  bash $0 max process 2026-10-08_20-15-33"; exit 2
fi
NAME="$1"
shift
[[ "$NAME" =~ ^[a-z][a-z0-9-]{1,26}$ ]] || { echo "Name ungültig: $NAME"; exit 2; }
I="$BENUTZER_DIR/$NAME"

[ "$(id -u)" = 0 ] || { echo "Bitte als root im CT ausführen."; exit 1; }
if [ -L "$I" ] || [ ! -d "$I" ] || [ "$(head -c 64 "$I/.clip-benutzer" 2>/dev/null | tr -d '[:space:]')" != "$NAME" ]; then
  echo "$I ist kein Freund (Marke fehlt) – erst: bash benutzer-anlegen.sh $NAME"; exit 1
fi
getent passwd "clip-$NAME" >/dev/null || { echo "Benutzer clip-$NAME fehlt – erst: bash benutzer-anlegen.sh $NAME"; exit 1; }
VORLAGE="$UNITS/clip-freund-bot@.service"
[ -f "$VORLAGE" ] || { echo "$VORLAGE fehlt – erst: bash benutzer-anlegen.sh $NAME"; exit 1; }

EIGENSCHAFTEN=()
while IFS= read -r zeile; do
  case "$zeile" in ''|'#'*) continue ;; esac
  EIGENSCHAFTEN+=("${zeile//%i/$NAME}")
done < <(awk '/^# --- Sandbox:/ {f = 1; next} /^# --- Ende Sandbox ---$/ {f = 0} f' "$VORLAGE")
# Ohne Sandbox startet nichts: eigener Benutzer, eigene Instanz, deine Bereiche verdeckt, nur sein Ordner eingebunden
for muss in "User=clip-$NAME" "Group=clip-$NAME" "Environment=CLIP_INSTANZ=" "ProtectSystem=strict" \
            "ProtectHome=true" "TemporaryFileSystem=" "BindPaths=" "BindReadOnlyPaths="; do
  da=0
  for e in "${EIGENSCHAFTEN[@]}"; do [[ "$e" == "$muss"* ]] && da=1; done
  [ "$da" = 1 ] || { echo "Der Sandbox-Block in $VORLAGE ist unvollständig ($muss fehlt) – ich starte nichts."; exit 1; }
done
ARGUMENTE=()
for e in "${EIGENSCHAFTEN[@]}"; do ARGUMENTE+=(-p "$e"); done
exec systemd-run --quiet --wait --pipe --collect --unit "clip-freund-befehl-$NAME-$$" \
  --description "Clip-Pipeline: Befehl für $NAME (Freund)" "${ARGUMENTE[@]}" -- "$PROD/.venv/bin/pipeline" "$@"
