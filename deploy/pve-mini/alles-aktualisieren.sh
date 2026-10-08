#!/usr/bin/env bash
# Alles daheim auf den neuesten Stand – EIN Befehl, auf pve-mini (Host-Shell) als root:
#   curl -fsSL https://raw.githubusercontent.com/baddieday/baddieday-voting/main/deploy/pve-mini/alles-aktualisieren.sh | bash
# Was passiert (dauert ~1 min, solange gerade nichts rendert):
#   1. Sicherung: Code-Stand beider Checkouts + Datenbank-Kopie nach /var/lib/clip-pipeline/vor-update-<Zeit>.*
#   2. Holen: origin/main (Produktion) und den neuesten Sprint-Stand (Lern-Bot)
#   3. Warten, bis kein Render läuft (Pipeline-Sperre, höchstens WARTEN_MIN), dann unter der Sperre umstellen:
#      /opt/clip-pipeline → main, /opt/clip-regie → neuester Stand, neue Dienste (clip-mikro, clip-sitzungen,
#      clip-publikum), Bots neu starten
#   4. Probe: Dienste laufen, Datenbank antwortet; Rückweg liegt als Skript bereit
# Weckt pve-big nie, löscht nichts, fasst .env und lokal.toml nicht an. Beliebig oft wiederholbar.
# Optional:  … | NCS_GENRES=hart bash      (Musik nachladen: techno, hardcore, electronic-rock, dance-rock, midtempo-bass)
#            … | WARTEN_MIN=30 bash        (länger auf ein laufendes Render warten, Standard 10)
set -uo pipefail

haupt() {
CT="${CT:-102}"
PROD="${PROD:-/opt/clip-pipeline}"
REGIE="${REGIE:-/opt/clip-regie}"
VAR_DIR="${VAR_DIR:-/var/lib/clip-pipeline}"
UNIT_DIR="${UNIT_DIR:-/etc/systemd/system}"
BRANCH="${BRANCH:-main}"
REGIE_BRANCH="${REGIE_BRANCH:-lernschleife-publikum}"
WARTEN_MIN="${WARTEN_MIN:-10}"; case "$WARTEN_MIN" in ''|*[!0-9]*) WARTEN_MIN=10;; esac
STEMPEL="$(date +%Y%m%d-%H%M%S)"

sag() { printf '\n== %s\n' "$*"; }
im_ct() { pct exec "$CT" -- "$@"; }
# git als pipeline, fragt nie nach (kein Terminal-Prompt, SSH nur mit Schlüssel)
git_p() { im_ct runuser -u pipeline -- env GIT_TERMINAL_PROMPT=0 GIT_SSH_COMMAND='ssh -o BatchMode=yes' git "$@"; }

command -v pct >/dev/null || { echo "pct fehlt – das Skript läuft auf dem Host pve-mini, nicht im Container."; return 1; }
[ "$(pct status "$CT" 2>/dev/null | awk '{print $2}')" = running ] || { echo "CT $CT läuft nicht – erst: pct start $CT"; return 1; }
im_ct test -d "$PROD/.git" || { echo "$PROD fehlt im CT $CT – falscher Container? (CT=… bash)"; return 1; }
MIT_REGIE=0; im_ct test -e "$REGIE/.git" && MIT_REGIE=1

sag "1/5 Sicherung (Code-Stand + Datenbank)"
ALT_PROD="$(git_p -C "$PROD" rev-parse HEAD)" || { echo "git in $PROD geht nicht – nichts geändert."; return 1; }
ALT_REGIE=""; [ "$MIT_REGIE" = 1 ] && ALT_REGIE="$(git_p -C "$REGIE" rev-parse HEAD)"
# Datenbank-Pfad aus der Konfiguration der Produktion (lokal.toml kann ihn verlegen), sonst der Standard
DB="$(im_ct runuser -u pipeline -- "$PROD/.venv/bin/python" -c \
  'from clip_pipeline import konfig; print(konfig.lade().datenbank)' 2>/dev/null | tail -n 1)"
[ -n "$DB" ] || DB="$VAR_DIR/pipeline.db"
SPERRE="${DB%.*}.lock"
im_ct test -f "$DB" || { echo "Datenbank $DB nicht gefunden – nichts geändert."; return 1; }
SICH="$(dirname "$DB")/vor-update-$STEMPEL"
GROESSE="$(im_ct stat -c %s "$DB")"; FREI="$(im_ct df --output=avail -B1 "$(dirname "$DB")" | tail -n 1 | tr -d ' ')"
[ "${FREI:-0}" -gt $(( ${GROESSE:-0} * 2 + 50000000 )) ] || { echo "Zu wenig Platz für die Sicherung neben $DB – nichts geändert."; return 1; }
# sqlite3-Backup: stimmige Kopie, auch während Bots schreiben
im_ct runuser -u pipeline -- python3 -c '
import sqlite3, sys
quelle = sqlite3.connect(sys.argv[1], timeout=60); ziel = sqlite3.connect(sys.argv[2])
quelle.backup(ziel); ziel.close(); quelle.close()' "$DB" "$SICH.db" \
  || { echo "Datenbank-Sicherung fehlgeschlagen – nichts geändert."; return 1; }
im_ct runuser -u pipeline -- sh -c 'printf "prod %s\nregie %s\n" "$1" "$2" > "$3"' _ "$ALT_PROD" "$ALT_REGIE" "$SICH.sha"
echo "Datenbank → $SICH.db ($(( GROESSE / 1024 )) KB), Code-Stand → $SICH.sha"

sag "2/5 Holen: origin/$BRANCH und origin/$REGIE_BRANCH"
git_p -C "$PROD" fetch -q origin "+refs/heads/$BRANCH:refs/remotes/origin/$BRANCH" \
  || { echo "git fetch geht nicht (Zugang zu GitHub im CT?) – nichts geändert."; return 1; }
git_p -C "$PROD" fetch -q origin "+refs/heads/$REGIE_BRANCH:refs/remotes/origin/$REGIE_BRANCH" 2>/dev/null || true
ZIEL_PROD="$(git_p -C "$PROD" rev-parse "origin/$BRANCH")"
# Lern-Bot: der neuere von beiden Ständen (nie zurück auf einen älteren)
ZIEL_REGIE="$ZIEL_PROD"
if git_p -C "$PROD" rev-parse -q --verify "origin/$REGIE_BRANCH" >/dev/null \
   && ! git_p -C "$PROD" merge-base --is-ancestor "origin/$REGIE_BRANCH" "origin/$BRANCH"; then
  ZIEL_REGIE="$(git_p -C "$PROD" rev-parse "origin/$REGIE_BRANCH")"
fi
echo "Produktion: $(git_p -C "$PROD" rev-list --count "$ALT_PROD..$ZIEL_PROD") neue Commits"
git_p -C "$PROD" log --format='  %h %s' "$ALT_PROD..$ZIEL_PROD" | head -n 8
[ "$MIT_REGIE" = 1 ] && echo "Lern-Bot:   $(git_p -C "$PROD" rev-list --count "$ALT_REGIE..$ZIEL_REGIE") neue Commits ($(git_p -C "$PROD" log -1 --format='%h %s' "$ZIEL_REGIE"))"

sag "3/5 Umstellen (wartet höchstens $WARTEN_MIN min, bis kein Render läuft)"
# Die Sperre gehört pipeline: vorher anlegen, sonst legte flock sie als root an und die Pipeline käme nicht mehr ran
im_ct runuser -u pipeline -- touch "$SPERRE"
im_ct flock -E 75 -w $(( WARTEN_MIN * 60 )) "$SPERRE" bash -c "$INNEN" innen \
  "$PROD" "$REGIE" "$MIT_REGIE" "$ALT_PROD" "$ZIEL_PROD" "$ALT_REGIE" "$ZIEL_REGIE" "$STEMPEL" "$UNIT_DIR" "$SICH"
rc=$?
if [ "$rc" = 75 ]; then
  echo "⚠️  Nach $WARTEN_MIN min läuft immer noch etwas (Render/Highlight) – stelle trotzdem um."
  echo "    Der laufende Schritt rechnet mit dem alten Code zu Ende; schlägt er fehl, wiederholt n8n ihn."
  im_ct bash -c "$INNEN" innen \
    "$PROD" "$REGIE" "$MIT_REGIE" "$ALT_PROD" "$ZIEL_PROD" "$ALT_REGIE" "$ZIEL_REGIE" "$STEMPEL" "$UNIT_DIR" "$SICH"
  rc=$?
fi
[ "$rc" = 0 ] || { echo "❌ Umstellen ist fehlgeschlagen (Code $rc). Rückweg: pct exec $CT -- bash $SICH.zurueck.sh"; return 1; }

if [ -n "${NCS_GENRES:-}" ] && [ "$MIT_REGIE" = 1 ]; then
  case "$NCS_GENRES" in *[!a-z,-]*) echo "NCS_GENRES nur aus a-z, Komma und Bindestrich – übersprungen";; *)
    sag "Musik von NCS: $NCS_GENRES (ein paar Minuten)"
    im_ct runuser -u pipeline -- "$REGIE/.venv/bin/pipeline" musik ncs --genre "$NCS_GENRES" --anzahl 40 2>/dev/null | tail -n 1;;
  esac
fi

sag "4/5 Probe"
sleep 6
fehler=0
for d in clip-bot clip-lernbot; do
  im_ct systemctl cat "$d" >/dev/null 2>&1 || continue
  st="$(im_ct systemctl is-active "$d")"
  if [ "$st" = active ]; then echo "✅ $d läuft"; else echo "❌ $d: $st"; fehler=1
    im_ct journalctl -u "$d" -n 15 --no-pager -o cat; fi
done
if STAT="$(im_ct runuser -u pipeline -- timeout 120 "$PROD/.venv/bin/pipeline" status 2>/dev/null | tail -n 1)" && [ -n "$STAT" ]; then
  echo "✅ Datenbank antwortet: ${STAT:0:160}"
else echo "❌ pipeline status ging nicht"; fehler=1; fi
im_ct runuser -u pipeline -- "$PROD/.venv/bin/python" -c "import importlib.util as u; print('✅ Whisper da' if u.find_spec('faster_whisper') else 'ℹ️  Whisper fehlt in der Produktion – Mic-Merkmale nur aus vorhandenen Werten')" 2>/dev/null

sag "5/5 Fertig"
echo "Produktion:  $(git_p -C "$PROD" log -1 --format='%h %ad %s' --date=short)"
[ "$MIT_REGIE" = 1 ] && echo "Lern-Bot:    $(git_p -C "$REGIE" log -1 --format='%h %ad %s' --date=short)"
echo "Rückweg (alter Code, Datenbank bleibt):  pct exec $CT -- bash $SICH.zurueck.sh"
echo "Nächster Entwurf im Lern-Bot mit /entwurf – er kommt mit den neuen Effekten."
[ "$fehler" = 0 ] || { echo "⚠️  Siehe ❌ oben – schick mir die Ausgabe."; return 1; }
}

# Läuft IM CT als root, unter der Pipeline-Sperre (bash -c "$INNEN" innen <argumente>)
INNEN='
set -uo pipefail
PROD=$1 REGIE=$2 MIT_REGIE=$3 ALT_PROD=$4 ZIEL_PROD=$5 ALT_REGIE=$6 ZIEL_REGIE=$7 STEMPEL=$8 UNITS=$9 SICH=${10}
p() { runuser -u pipeline -- env GIT_TERMINAL_PROMPT=0 "$@"; }

# Rückweg zuerst schreiben – gilt auch, wenn unten etwas schiefgeht
{ echo "#!/usr/bin/env bash"
  echo "# Rückweg vom Update $STEMPEL: alter Code, Bots neu. Die Datenbank bleibt (Kopie: $SICH.db)."
  echo "runuser -u pipeline -- git -C $PROD checkout -q -B main $ALT_PROD"
  [ "$MIT_REGIE" = 1 ] && echo "runuser -u pipeline -- git -C $REGIE checkout -q --detach $ALT_REGIE"
  echo "systemctl restart clip-bot clip-lernbot 2>/dev/null; echo zurück auf $ALT_PROD"
} > "$SICH.zurueck.sh"

# Eigene Änderungen an versionierten Dateien beiseitelegen (git stash – nichts geht verloren)
beiseite() {
  if [ -n "$(p git -C "$1" status --porcelain --untracked-files=no)" ]; then
    p git -C "$1" stash push -q -m "vor-update-$STEMPEL" && echo "ℹ️  Eigene Änderungen in $1 liegen jetzt in: git -C $1 stash list"
  fi
}

beiseite "$PROD"
if ! p git -C "$PROD" merge-base --is-ancestor "$ALT_PROD" "$ZIEL_PROD"; then
  p git -C "$PROD" branch -f "vor-update-$STEMPEL" "$ALT_PROD" && echo "ℹ️  Alter Stand hatte eigene Commits – aufgehoben im Branch vor-update-$STEMPEL"
fi
p git -C "$PROD" checkout -q -B main "$ZIEL_PROD" || { echo "checkout in $PROD fehlgeschlagen"; exit 2; }
p git -C "$PROD" branch -q --set-upstream-to=origin/main main 2>/dev/null || true
echo "Produktion → $(p git -C "$PROD" log -1 --format="%h %s")"

if [ "$MIT_REGIE" = 1 ]; then
  beiseite "$REGIE"
  p git -C "$REGIE" checkout -q --detach "$ZIEL_REGIE" || { echo "checkout in $REGIE fehlgeschlagen"; exit 2; }
  echo "Lern-Bot   → $(p git -C "$REGIE" log -1 --format="%h %s")"
fi

# Pakete nur, wenn sich die Abhängigkeiten geändert haben (sonst reicht der Neustart: editierbare Installation)
pakete() {  # $1 Checkout, $2 alter Stand, $3 neuer Stand
  [ -x "$1/.venv/bin/pip" ] || return 0
  p git -C "$1" diff --quiet "$2" "$3" -- pyproject.toml 2>/dev/null && return 0
  echo "Pakete in $1 nachziehen …"; p "$1/.venv/bin/pip" install -q -e "$1" 2>&1 | tail -n 2
}
pakete "$PROD" "$ALT_PROD" "$ZIEL_PROD"
[ "$MIT_REGIE" = 1 ] && pakete "$REGIE" "$ALT_REGIE" "$ZIEL_REGIE"

# Neue Spalten/Tabellen einmal anlegen, bevor beide Bots gleichzeitig starten
p timeout 120 "$PROD/.venv/bin/pipeline" status >/dev/null 2>&1 || echo "⚠️  pipeline status meldet einen Fehler (Probe unten zeigt mehr)"

# systemd-Dienste: geänderte übernehmen, wenn du sie nicht selbst angepasst hast; neue nur clip-mikro (Mic-Schritt),
# clip-sitzungen (Abend-Video) und clip-publikum (Zuschauerzahlen: TikTok-Abruf und Datenbank, keine Sperre, weckt nie).
# clip-aufraeumen bleibt immer aus („Nie löschen“). Der Lern-Bot läuft aus $REGIE, falls es den gibt.
lernbot_form() { if [ "$MIT_REGIE" = 1 ]; then sed "s#/opt/clip-pipeline#$REGIE#g" | { getent group render >/dev/null && cat || sed "/^SupplementaryGroups=/d"; }; else cat; fi; }
neu_an=(); eigen=(); geaendert=0
for quelle in "$PROD"/deploy/systemd/*.service "$PROD"/deploy/systemd/*.timer "$PROD"/deploy/systemd/*.path \
              "$PROD"/deploy/systemd/*.service.d/*.conf; do
  [ -f "$quelle" ] || continue
  rel="${quelle#"$PROD"/deploy/systemd/}"; name="$(basename "$quelle")"
  case "$name" in clip-aufraeumen.*) continue;; esac
  if [ "$rel" = clip-lernbot.service ]; then
    soll="$(lernbot_form < "$quelle")"; alt="$(p git -C "$PROD" show "${ALT_REGIE:-$ALT_PROD}:deploy/systemd/$rel" 2>/dev/null | lernbot_form)"
  else
    soll="$(cat "$quelle")"; alt="$(p git -C "$PROD" show "$ALT_PROD:deploy/systemd/$rel" 2>/dev/null)"
  fi
  ziel="$UNITS/$rel"
  if [ -e "$ziel" ]; then
    ist="$(cat "$ziel")"
    [ "$ist" = "$soll" ] && continue
    if [ -n "$alt" ] && [ "$ist" = "$alt" ]; then
      printf "%s\n" "$soll" > "$ziel"; geaendert=1; echo "Dienst aktualisiert: $rel"
    else
      echo "ℹ️  $rel weicht vom Repo ab (von dir angepasst?) – nicht überschrieben. Vergleich: diff $ziel $quelle"
      eigen+=("$name")
    fi
  else
    # 07.10.: ohne clip-sitzungen kam nie ein Abend-Video · 08.10.: ohne clip-publikum nie Zuschauerzahlen
    case "$name" in clip-mikro.*|clip-sitzungen.*|clip-publikum.*)
      printf "%s\n" "$soll" > "$ziel"; geaendert=1; echo "Dienst neu: $rel"
      case "$name" in *.path|*.timer) neu_an+=("$name");; esac;;
    esac
  fi
done
if [ "$geaendert" = 1 ]; then systemctl daemon-reload; fi
if [ "${#neu_an[@]}" -gt 0 ]; then
  systemctl enable -q --now "${neu_an[@]}" && echo "eingeschaltet: ${neu_an[*]}"
  echo "systemctl disable --now ${neu_an[*]}" >> "$SICH.zurueck.sh"
fi
# Abend-Video (07.10.) und Zuschauerzahlen (08.10.): diese Timer müssen laufen – auch wenn sie schon installiert, aber
# nie eingeschaltet waren. Einen von dir angepassten Timer (oben „weicht vom Repo ab“) fasst das Update nicht an.
for t in clip-sitzungen.timer clip-publikum.timer; do
  case " ${eigen[*]} " in *" $t "*) continue;; esac
  if systemctl cat "$t" >/dev/null 2>&1 && ! systemctl is-enabled -q "$t" 2>/dev/null; then
    systemctl enable -q --now "$t" && echo "eingeschaltet: $t"
    echo "systemctl disable --now $t" >> "$SICH.zurueck.sh"
  fi
done

# Beide Bots neu – sonst läge im Speicher der alte Code und auf der Platte der neue
for d in clip-bot clip-lernbot; do
  systemctl cat "$d" >/dev/null 2>&1 && systemctl restart "$d"
done
exit 0
'

haupt "$@" < /dev/null
