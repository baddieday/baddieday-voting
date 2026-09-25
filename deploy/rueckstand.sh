#!/usr/bin/env bash
# Rückstand abarbeiten: alle Replays unter /srv/clips/replays sequentiell durch prepare/analyze/decide/render.
# - älteste zuerst (Name = Datum), Session-ID aus UnsavedReplay-JJJJ.MM.TT-hh.mm.ss.replay -> JJJJ-MM-TT_hh-mm-ss
# - überspringt Sessions, die schon fertig sind (matches.status = 'verarbeitet' oder schnittliste.json + render-Ereignis)
# - Exit 3 (Speicher offline) / 4 (Sperre): 120 s warten, bis zu 10 Versuche je Schritt (pve-big wird per WOL geweckt)
# - Exit 1/2: Session als FEHLER loggen, nächste Session
# - Log: /var/lib/clip-pipeline/rueckstand.log (Zeit, Session, Schritt, Exit, JSON-Zeile), stderr der Schritte
#   nach rueckstand-stderr.log. Beliebig oft neu startbar.
# Aufruf: rueckstand.sh            -> abarbeiten
#         rueckstand.sh --liste    -> nur offene Sessions ausgeben (auch: RUECKSTAND_LISTE=1)
# Start als Dienst: systemd-run --unit=clip-rueckstand --uid=pipeline --gid=pipeline \
#   -p WorkingDirectory=/opt/clip-pipeline --collect /opt/clip-pipeline/deploy/rueckstand.sh
# Mic-Schritt (Lernschleife Stufe 2): Jedes render startet einen Mic-Kindprozess, der auf die Sperre wartet. Endet
# diese Unit, beendet systemd auch diese Kinder (sie liegen in ihrer cgroup) – danach einmal von Hand
# `pipeline stimmung --clips --max 5`. KillMode=process wäre schlechter: verwaiste Prozesse ohne Unit.
set -u
export LC_ALL=C
PIPE=/opt/clip-pipeline/.venv/bin/pipeline
DB=/var/lib/clip-pipeline/pipeline.db
REPLAYS=/srv/clips/replays
SESSIONS=/srv/clips/sessions
LOG=/var/lib/clip-pipeline/rueckstand.log
ERRLOG=/var/lib/clip-pipeline/rueckstand-stderr.log
LOCK=/var/lib/clip-pipeline/rueckstand.lock
WARTE_S=${RUECKSTAND_WARTE_S:-120}
VERSUCHE=${RUECKSTAND_VERSUCHE:-10}
NUR_LISTE=0
[ "${1:-}" = "--liste" ] && NUR_LISTE=1
[ "${RUECKSTAND_LISTE:-0}" = "1" ] && NUR_LISTE=1

log() { printf '%s  %s\n' "$(date +%Y-%m-%dT%H:%M:%S%z)" "$*" >> "$LOG"; }

session_id() {  # UnsavedReplay-2026.09.22-23.56.52.replay -> 2026-09-22_23-56-52
  local n=${1##*/}; n=${n#UnsavedReplay-}; n=${n%.replay}
  local d=${n%%-*} t=${n#*-}
  printf '%s_%s' "${d//./-}" "${t//./-}"
}

fertig() {  # 0 = Session ist schon komplett verarbeitet
  local id=$1 status
  status=$(sqlite3 "$DB" "select status from matches where id='$id';" 2>/dev/null)
  [ "$status" = "verarbeitet" ] && return 0
  if [ -f "$SESSIONS/$id/schnittliste.json" ]; then
    local n
    n=$(sqlite3 "$DB" "select count(*) from ereignisse where match_id='$id' and art='match' and text like 'render:%';" 2>/dev/null)
    [ "${n:-0}" -gt 0 ] && return 0
  fi
  return 1
}

# Offene Sessions einsammeln (älteste zuerst; Glob ist unter LC_ALL=C nach Namen sortiert)
offen=()
shopt -s nullglob
for f in "$REPLAYS"/UnsavedReplay-*.replay; do
  id=$(session_id "$f")
  [[ "$id" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{2}-[0-9]{2}-[0-9]{2}$ ]] || { echo "unbekannter Name: $f" >&2; continue; }
  fertig "$id" && continue
  offen+=("$id")
done
shopt -u nullglob

if [ "$NUR_LISTE" = 1 ]; then
  printf '%s\n' "${offen[@]}"
  echo "offen: ${#offen[@]}"
  exit 0
fi

# Nur ein Rückstand-Lauf gleichzeitig
exec 9>"$LOCK"
if ! flock -n 9; then echo "rueckstand.sh läuft bereits" >&2; exit 4; fi

verarbeitet=0; fehler=0; uebersprungen=0; start_ts=$(date +%s)
trap 'log "ABBRUCH (Signal) verarbeitet=$verarbeitet fehler=$fehler offen_rest=$(( ${#offen[@]} - verarbeitet - fehler - uebersprungen ))"; exit 143' TERM INT
log "START offen=${#offen[@]} warte=${WARTE_S}s versuche=$VERSUCHE pid=$$"

schritt() {  # session schritt -> Exit 0 ok, 1 Fehler (endgültig), 3/4 nach VERSUCHE Fehlversuchen
  local id=$1 s=$2 versuch=1 rc out tmp
  tmp=$(mktemp)
  while :; do
    printf '### %s %s %s (Versuch %s)\n' "$(date +%Y-%m-%dT%H:%M:%S%z)" "$id" "$s" "$versuch" >> "$ERRLOG"
    "$PIPE" "$s" --session "$id" >"$tmp" 2>>"$ERRLOG"; rc=$?
    out=$(tail -n1 "$tmp")
    log "$id  $s  exit=$rc  versuch=$versuch  $out"
    case $rc in
      0) rm -f "$tmp"; sleep 3; return 0 ;;   # kurze Pause: Webhook-Laeufe von n8n bekommen die Sperre
      3|4)
        if [ "$versuch" -ge "$VERSUCHE" ]; then rm -f "$tmp"; return $rc; fi
        versuch=$((versuch + 1)); sleep "$WARTE_S" ;;
      *) rm -f "$tmp"; return 1 ;;
    esac
  done
}

for id in "${offen[@]}"; do
  if fertig "$id"; then log "$id  UEBERSPRUNGEN (inzwischen verarbeitet)"; uebersprungen=$((uebersprungen + 1)); continue; fi
  t0=$(date +%s); ok=1
  for s in prepare analyze decide render; do
    if ! schritt "$id" "$s"; then
      log "$id  FEHLER bei $s – weiter mit nächster Session"
      ok=0; break
    fi
  done
  if [ "$ok" = 1 ]; then
    verarbeitet=$((verarbeitet + 1)); log "$id  FERTIG dauer=$(( $(date +%s) - t0 ))s"
  else
    fehler=$((fehler + 1))
  fi
done

log "ENDE verarbeitet=$verarbeitet fehler=$fehler uebersprungen=$uebersprungen offen_am_start=${#offen[@]} dauer=$(( $(date +%s) - start_ts ))s"
echo "{\"verarbeitet\": $verarbeitet, \"fehler\": $fehler, \"uebersprungen\": $uebersprungen, \"offen_am_start\": ${#offen[@]}}"
[ "$fehler" -eq 0 ]
