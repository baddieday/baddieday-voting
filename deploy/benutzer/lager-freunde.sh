#!/usr/bin/env bash
# Startet von selbst mit deinem Lager-Abgleich (clip-lager-freunde.service, WantedBy=clip-lager.service). Von Hand im
# CT als root nur zum Nachsehen:  bash lager-freunde.sh --probe   (zeigt, wer mitfahren würde – ändert nichts)
# Mehrbenutzer, Stufe 2 (docs/MEHRBENUTZER.md „Lager für Freunde“): Freunde mit Lager fahren bei deinem täglichen
# Abgleich mit. Dein Abgleich weckt pve-big wie immer; dieser Rundgang weckt nie.
#   1 Halten-Marke „freunde“ (30 min, als pipeline) – solange dein Abgleich läuft, alle 5 min verlängert. So fährt
#     dein Abgleich pve-big am Ende nicht herunter, wenn danach noch Freunde dran sind.
#   2 Prüfen ohne Wecken: Lager per NFS eingehängt, pve-big antwortet (Port 2049), deine Marke .clip-lager da. Sonst
#     Marke lösen, Ende – die Freunde fahren beim nächsten Mal mit.
#   3 Je Freund mit Lager ([instanz] lager = true in seiner instanz.toml, Einhängepunkt I/lager) und laufendem
#     scan-Timer nacheinander, neue Starts nur 10–18 Uhr: erneut prüfen, Marke 130 min, Herzschlag für clip-leerlauf,
#     freunde/<name> mit Marke anlegen, falls sie fehlen (nur auf deinem Lager-NFS, nie ein eigenes Dataset), dann
#     clip-freund-lager@<name> – sein Abgleich in seiner Sandbox, höchstens 2 h.
#   4 Am Ende: Marke gelöst, Herzschlag weg, Zusammenfassung /var/lib/clip-pipeline/lager-freunde.json (je Freund
#     Start, Ende, Exit, zuletzt gut; dazu wer Lager hat, seit wann und wer stillgelegt ist – für deine
#     Morgenprüfung, Thema „freunde“).
# Im Lager wird nie etwas gelöscht (nur der eigene Herzschlag verschwindet wieder). Ohne Freund mit Lager: nichts –
# nur eine schon vorhandene Zusammenfassung erfährt, dass keiner mehr Lager hat (sonst warnte die Morgenprüfung weiter).
# Intern für benutzer-anlegen.sh (Schritt „Lager“):
#   --nfs             Exit 0 = Lager eingehängt und pve-big wach, sonst 1 mit dem Grund
#   --ordner <name>   freunde/<name> und seine Marke anlegen, falls sie fehlen (nur bei bestätigtem NFS)
set -euo pipefail
BENUTZER_DIR="${BENUTZER_DIR:-/var/lib/clip-benutzer}"
PROD="${PROD:-/opt/clip-pipeline}"
FLORIAN_DIR="${FLORIAN_DIR:-/var/lib/clip-pipeline}"
LAGER_NFS="${LAGER_NFS:-/srv/big/clips}"   # dein Lager: NFS von pve-big
NFS_PORT="${NFS_PORT:-2049}"
ZEITZONE="${ZEITZONE:-Europe/Berlin}"
WARTE_S="${WARTE_S:-30}"                   # so oft nachsehen, ob dein Abgleich noch läuft
HERZ_S="${HERZ_S:-60}"                     # Herzschlag: so oft berührt (clip-leerlauf zählt 3 min, höchstens 4 h)
case "$WARTE_S" in ''|*[!0-9]*|0) WARTE_S=30 ;; esac
case "$HERZ_S" in ''|*[!0-9]*|0) HERZ_S=60 ;; esac
VON_UHR=10
BIS_UHR=18
MARKE=freunde
FLORIANS_MARKE=.clip-lager
ZUSAMMENFASSUNG="$FLORIAN_DIR/lager-freunde.json"
MODUS=rundgang
NAME=""
aufruf() { echo "Aufruf: bash $0 [--probe]   (intern: --nfs | --ordner <name>)"; exit 2; }
case "$#:${1:-}" in
  0:) ;;
  1:--probe) MODUS=probe ;;
  1:--nfs) MODUS=nfs ;;
  2:--ordner) MODUS=ordner; NAME="$2" ;;
  *) aufruf ;;
esac
if [ "$MODUS" = ordner ] && ! [[ "$NAME" =~ ^[a-z][a-z0-9-]{1,26}$ ]]; then aufruf; fi

log() { printf '%s\n' "$*"; }
jetzt() { date -u +%Y-%m-%dT%H:%M:%SZ; }
# Alles auf dem NFS mit Zeitgrenze – an einem hängenden Mount bleibt der Rundgang nie stehen
t() { timeout -k 5 10 "$@"; }

# Lager eingehängt und pve-big wach? Nur nachsehen: Mount-Tabelle, kurzer TCP-Versuch, stat deiner Marke – nie wecken.
nfs_quelle() { findmnt -n -t nfs,nfs4 -o SOURCE "$LAGER_NFS" 2>/dev/null | head -n 1 || true; }
nfs_wach() {
  local quelle host
  GRUND=""
  quelle="$(nfs_quelle)"
  if [ -z "$quelle" ]; then GRUND="das Lager $LAGER_NFS ist nicht per NFS eingehängt (pve-big schläft)"; return 1; fi
  host="${quelle%:/*}"; host="${host#\[}"; host="${host%\]}"
  if ! timeout 5 bash -c 'exec 3<>"/dev/tcp/$1/$2"' _ "$host" "$NFS_PORT" 2>/dev/null; then
    GRUND="pve-big ($host) antwortet nicht auf Port $NFS_PORT"; return 1
  fi
  if ! t stat -c %i "$LAGER_NFS/$FLORIANS_MARKE" > /dev/null 2>&1; then
    GRUND="deine Marke $LAGER_NFS/$FLORIANS_MARKE fehlt oder das NFS hängt"; return 1
  fi
}

# freunde/<name> und seine Marke .clip-lager-<name> anlegen, falls sie fehlen – nur auf dem Gerät deines Lager-NFS
# (kein neues Dataset), nie über einen Link. Danach bindet clip-freund-lager@<name> genau diesen Ordner.
ordner_anlegen() {
  local n="$1" dev p marke
  GRUND=""
  dev="$(t stat -c %d "$LAGER_NFS" 2> /dev/null)" || { GRUND="$LAGER_NFS ist nicht lesbar"; return 1; }
  for p in "$LAGER_NFS/freunde" "$LAGER_NFS/freunde/$n"; do
    if [ -L "$p" ] || { [ -e "$p" ] && [ ! -d "$p" ]; }; then GRUND="$p ist kein Ordner (Link?) – bitte ansehen"; return 1; fi
    if [ ! -d "$p" ]; then
      t mkdir "$p" || { GRUND="$p ließ sich nicht anlegen"; return 1; }
      log "angelegt: $p"
    fi
    if [ "$(t stat -c %d "$p" 2> /dev/null || true)" != "$dev" ]; then
      GRUND="$p liegt nicht auf deinem Lager-NFS – kein eigenes Dataset für Freunde, bitte melden"; return 1
    fi
  done
  marke="$LAGER_NFS/freunde/$n/.clip-lager-$n"
  if [ -L "$marke" ] || { [ -e "$marke" ] && [ ! -f "$marke" ]; }; then GRUND="$marke ist keine normale Datei"; return 1; fi
  if [ ! -f "$marke" ]; then
    t touch "$marke" || { GRUND="$marke ließ sich nicht anlegen"; return 1; }
    log "Marke angelegt: $marke"
  fi
}

scan_an() { [ "$(systemctl is-active "clip-freund-scan@$1.timer" 2> /dev/null || true)" = active ]; }

# Zusammenfassung für benutzer-pruefen.sh und deine Morgenprüfung (puffer.py). Argumente: Datei, Start, Ende, Ergebnis
# (Start leer = den letzten Rundgang stehen lassen), Freunde mit Lager, davon stillgelegt (je durch Leerzeichen
# getrennt), dann je Freund mit Lauf eine Zeile. Freunde ohne Lauf behalten ihren Eintrag. mit_lager: Name → seit wann
# mit Lager und nicht stillgelegt (neu, Schalter wieder an oder wieder aktiv: dieser Rundgang), stillgelegt ja/nein.
# Die Datei liegt in deinem Ordner (gehört pipeline): nie einem Link folgen.
ERGEBNISSE=()   # je Freund: Name, Start, Ende, systemctl, Exit, Hinweis (Tab getrennt)
ZUSAMMEN_PY="$(cat <<'PY'
import json, os, sys
pfad, start, ende, ergebnis, mit, still = sys.argv[1:7]

def lies(name):
    try:
        with os.fdopen(os.open(name, os.O_RDONLY | os.O_NOFOLLOW), encoding="utf-8") as datei:
            daten = json.load(datei)
    except (OSError, ValueError):
        return {}
    return daten if isinstance(daten, dict) else {}

alt = lies(pfad)
freunde, alt_mit, lauf = ({**alt[k]} if isinstance(alt.get(k), dict) else {} for k in ("freunde", "mit_lager", "lauf"))

def zahl(text):
    return int(text) if text.lstrip("-").isdigit() else None

for zeile in sys.argv[7:]:
    name, s, e, rc, code, hinweis = (zeile.split("\t") + [""] * 6)[:6]
    vorher = freunde.get(name) if isinstance(freunde.get(name), dict) else {}
    ok = rc == "0" and code == "0"
    freunde[name] = {"start": s, "ende": e, "rc": zahl(rc), "exit": zahl(code), "ok": ok,
                     "zuletzt_ok": e if ok else vorher.get("zuletzt_ok"), "hinweis": hinweis or None}
stillgelegt = set(still.split())
mit_lager = {}
for name in mit.split():
    vorher = alt_mit.get(name) if isinstance(alt_mit.get(name), dict) else {}
    ruht = name in stillgelegt
    weiter = bool(vorher.get("seit")) and (ruht or vorher.get("stillgelegt") is not True)
    mit_lager[name] = {"seit": vorher["seit"] if weiter else start, "stillgelegt": ruht}
if start:
    lauf = {"start": start, "ende": ende, "ergebnis": ergebnis}
fd = os.open(pfad + ".neu", os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
with os.fdopen(fd, "w", encoding="utf-8") as datei:
    json.dump({"lauf": lauf, "mit_lager": mit_lager, "freunde": freunde}, datei, ensure_ascii=False, indent=2)
    datei.write("\n")
PY
)"
zusammenfassung() {
  local n still=()
  for n in "${FREUNDE[@]}"; do scan_an "$n" || still+=("$n"); done
  python3 -I -c "$ZUSAMMEN_PY" "$ZUSAMMENFASSUNG" "$LAUF_START" "$(jetzt)" "$ENDE" "${FREUNDE[*]}" "${still[*]}" \
    "${ERGEBNISSE[@]}" \
    && chown -h pipeline:pipeline "$ZUSAMMENFASSUNG.neu" && mv -fT "$ZUSAMMENFASSUNG.neu" "$ZUSAMMENFASSUNG"
}

[ "$(id -u)" = 0 ] || { echo "Bitte als root im CT ausführen."; exit 1; }
case "$MODUS" in
  nfs)
    if nfs_wach; then log "Lager eingehängt ($(nfs_quelle)), pve-big ist wach"; exit 0; fi
    log "$GRUND"; exit 1 ;;
  ordner)
    nfs_wach || { log "$GRUND"; exit 1; }
    ordner_anlegen "$NAME" || { log "$GRUND"; exit 1; }
    log "$LAGER_NFS/freunde/$NAME mit Marke .clip-lager-$NAME ist da"; exit 0 ;;
esac

# Freunde mit Lager: Marke nennt den Namen, [instanz] lager = true (nur gelesen, als root), Einhängepunkt I/lager
SCHALTER_PY='import sys, tomllib
try:
    with open(sys.argv[1], "rb") as datei:
        i = tomllib.load(datei).get("instanz")
except (OSError, ValueError):
    sys.exit(1)
sys.exit(0 if isinstance(i, dict) and i.get("lager") is True else 1)'
FREUNDE=()
for ordner in "$BENUTZER_DIR"/*; do
  n="${ordner##*/}"
  [ -d "$ordner" ] && [ ! -L "$ordner" ] && [[ $n =~ ^[a-z][a-z0-9-]{1,26}$ ]] || continue
  [ -f "$ordner/.clip-benutzer" ] && [ ! -L "$ordner/.clip-benutzer" ] || continue
  [ "$(head -c 64 "$ordner/.clip-benutzer" | tr -d '[:space:]')" = "$n" ] || continue
  [ -f "$ordner/instanz.toml" ] && [ ! -L "$ordner/instanz.toml" ] || continue
  python3 -I -c "$SCHALTER_PY" "$ordner/instanz.toml" 2> /dev/null || continue
  if [ -L "$ordner/lager" ] || [ ! -d "$ordner/lager" ]; then
    log "ℹ️  $n: Lager-Schalter an, aber $ordner/lager fehlt – übersprungen (bash benutzer-anlegen.sh $n)"; continue
  fi
  FREUNDE+=("$n")
done
if [ "${#FREUNDE[@]}" = 0 ]; then
  # Hatte früher ein Freund Lager, steht er noch in der Zusammenfassung – sonst warnte deine Morgenprüfung weiter.
  # Nur diese Liste wird geleert (der letzte Rundgang und die Läufe bleiben); keine Marke, kein Start.
  if [ "$MODUS" = rundgang ] && [ -f "$ZUSAMMENFASSUNG" ]; then
    LAUF_START=""
    ENDE=""
    zusammenfassung || log "⚠️  $ZUSAMMENFASSUNG ließ sich nicht schreiben"
  fi
  log "Kein Freund mit Lager – nichts zu tun."; exit 0
fi
if ! systemctl cat clip-freund-lager@.service > /dev/null 2>&1; then
  log "Die Vorlage clip-freund-lager@.service fehlt – erst das Update (alles-aktualisieren.sh). Nichts getan."; exit 0
fi

im_fenster() {
  local h
  h="$(TZ="$ZEITZONE" date +%H)"
  [[ "$h" =~ ^[0-9]{1,2}$ ]] || return 1   # sonst bräche die Rechnung unten den ganzen Rundgang ab
  h=$((10#$h))
  [ "$h" -ge "$VON_UHR" ] && [ "$h" -lt "$BIS_UHR" ]
}
# Läuft dein Abgleich noch (oder wartet er auf den Start)? Der Zustandstext zählt – is-active -q sieht einen laufenden
# Schritt (oneshot, „activating“) nicht.
florian_laeuft() {
  case "$(systemctl is-active clip-lager.service 2> /dev/null || true)" in
    activating|deactivating|reloading|refreshing) return 0 ;;
  esac
  systemctl list-jobs --no-legend 2> /dev/null | awk '$2 == "clip-lager.service" {f = 1} END {exit !f}'
}

if [ "$MODUS" = probe ]; then
  log "PROBE: Ich zeige nur, was der Rundgang tun würde – keine Marke, kein Ordner, kein Start."
  for n in "${FREUNDE[@]}"; do
    if scan_an "$n"; then log "   $n: Lager an – fährt mit"; else log "   $n: Lager an, aber stillgelegt (scan-Timer aus) – fährt nicht mit"; fi
  done
  if florian_laeuft; then log "Dein Lager-Abgleich läuft gerade – der Rundgang wartet, bis er fertig ist."; fi
  if nfs_wach; then log "Lager eingehängt ($(nfs_quelle)), pve-big ist wach."
  else log "Jetzt nicht: $GRUND – der Rundgang würde nichts starten (er weckt nie)."; fi
  if im_fenster; then log "Uhrzeit passt (Starts nur $VON_UHR–$BIS_UHR Uhr)."
  else log "Jetzt keine Starts: nur $VON_UHR–$BIS_UHR Uhr."; fi
  log "Probe fertig – nichts verändert."
  exit 0
fi

# --- Rundgang ---------------------------------------------------------------------------------------------------------
als_pipeline() { (cd "$PROD" && runuser -u pipeline -- "$PROD/.venv/bin/pipeline" "$@") > /dev/null; }
MARKE_GESETZT=0
halten() {
  if als_pipeline big halten "$MARKE" --minuten "$1" --grund "Freunde fahren beim Lager-Abgleich mit"; then MARKE_GESETZT=1
  else log "⚠️  Halten-Marke $MARKE ließ sich nicht setzen – pve-big könnte früher ausgehen"; fi
}
HERZ=""
HERZ_PID=""
herz_an() {
  HERZ="$LAGER_NFS/.aktiv/$(uname -n)-freund-$1-$$-$(date +%s)"
  ( t mkdir -p "${HERZ%/*}" 2> /dev/null || true
    while :; do t touch "$HERZ" 2> /dev/null || true; sleep "$HERZ_S"; done ) &
  HERZ_PID=$!
}
herz_aus() {
  if [ -n "$HERZ_PID" ]; then kill "$HERZ_PID" 2> /dev/null || true; wait "$HERZ_PID" 2> /dev/null || true; HERZ_PID=""; fi
  if [ -n "$HERZ" ]; then t rm -f -- "$HERZ" 2> /dev/null || true; HERZ=""; fi
}
aufraeumen() {
  local rc=$?
  set +e
  herz_aus
  if [ "$MARKE_GESETZT" = 1 ]; then
    als_pipeline big loesen "$MARKE" || log "⚠️  Halten-Marke ließ sich nicht lösen – sie läuft von selbst ab"
  fi
  zusammenfassung || log "⚠️  $ZUSAMMENFASSUNG ließ sich nicht schreiben"
  log "Rundgang zu Ende: $ENDE"
  exit "$rc"
}
LAUF_START="$(jetzt)"
ENDE="abgebrochen"
trap aufraeumen EXIT
trap 'exit 143' TERM INT

halten 30
VERLAENGERN=$((300 / WARTE_S))
[ "$VERLAENGERN" -ge 1 ] || VERLAENGERN=1
i=0
while florian_laeuft; do
  [ "$i" -gt 0 ] || log "Dein Lager-Abgleich läuft – ich warte, bis er fertig ist (die Marke hält pve-big danach wach)."
  sleep "$WARTE_S"
  i=$((i + 1))
  if [ $((i % VERLAENGERN)) = 0 ]; then halten 30; fi
done
if ! nfs_wach; then
  ENDE="kein Mitfahren – $GRUND"
  log "Kein Mitfahren heute: $GRUND. Die Freunde fahren beim nächsten Mal mit (ich wecke nie)."
  exit 0
fi
ENDE="fertig"
for n in "${FREUNDE[@]}"; do
  if ! scan_an "$n"; then log "ℹ️  $n: stillgelegt (scan-Timer aus) – übersprungen"; continue; fi
  if ! im_fenster; then
    ENDE="keine neuen Starts außerhalb $VON_UHR–$BIS_UHR Uhr"
    log "Jetzt keine neuen Starts mehr (nur $VON_UHR–$BIS_UHR Uhr) – die übrigen fahren beim nächsten Mal mit."; break
  fi
  if ! nfs_wach; then ENDE="abgebrochen – $GRUND"; log "Schluss für heute: $GRUND."; break; fi
  if ! ordner_anlegen "$n"; then
    log "❌ $n: $GRUND – übersprungen"
    ERGEBNISSE+=("$n"$'\t'"$(jetzt)"$'\t'"$(jetzt)"$'\t'1$'\t'$'\t'"$GRUND"); continue
  fi
  halten 130
  einheit="clip-freund-lager@$n.service"
  start="$(jetzt)"
  herz_an "$n"
  log "$n: Abgleich ins Lager läuft … (journalctl -u $einheit)"
  if systemctl start "$einheit"; then rc=0; else rc=$?; fi
  herz_aus
  code="$(systemctl show -p ExecMainStatus --value "$einheit" 2> /dev/null || true)"
  log "$n: fertig (Exit ${code:-?})"
  ERGEBNISSE+=("$n"$'\t'"$start"$'\t'"$(jetzt)"$'\t'"$rc"$'\t'"$code"$'\t')
done
