#!/usr/bin/env bash
# Auf pve-mini als root:  bash regie-aktualisieren.sh        (oder: BRANCH=main bash regie-aktualisieren.sh)
# Direkt aus GitHub (Repo ist öffentlich):
#   curl -fsSL https://raw.githubusercontent.com/baddieday/baddieday-voting/lernschleife-publikum/deploy/pve-mini/regie-aktualisieren.sh | bash
# Holt den Branch-Stand nach /opt/clip-regie im CT (neben der Produktion) und startet nur den Lern-Bot neu.
# Kein Wecken von pve-big, kein Whisper, keine Musik – dafür ist regie-starten.sh da. Produktion bleibt unangetastet.
set -uo pipefail
CT="${CT:-102}"; PROD=/opt/clip-pipeline; REGIE=/opt/clip-regie; BRANCH="${BRANCH:-lernschleife-publikum}"
als_pipeline() { pct exec "$CT" -- runuser -l pipeline -c "export GIT_TERMINAL_PROMPT=0 GIT_SSH_COMMAND='ssh -o BatchMode=yes'; $1"; }
[ "$(pct status "$CT" | awk '{print $2}')" = running ] || { echo "CT $CT läuft nicht – erst: pct start $CT"; exit 1; }
pct exec "$CT" -- test -e "$REGIE/.git" || { echo "$REGIE fehlt – erst einmal regie-starten.sh laufen lassen."; exit 1; }

echo "== 0/5 Vorher: Stand und Dienst"
als_pipeline "git -C $REGIE log -1 --format='%h %ad %s' --date=short"
if ! pct exec "$CT" -- sh -c "systemctl cat clip-lernbot 2>/dev/null | grep -q '^ExecStart=$REGIE/'"; then
  echo "⚠️  clip-lernbot startet NICHT aus $REGIE (systemctl cat clip-lernbot) – der neue Stand wäre wirkungslos."
  echo "    Abhilfe: einmal regie-starten.sh laufen lassen (schreibt die Unit auf $REGIE um)."; exit 1
fi

echo "== 1/5 Holen: origin/$BRANCH"
als_pipeline "git -C $PROD fetch -q origin +refs/heads/$BRANCH:refs/remotes/origin/$BRANCH" \
  || { echo "git fetch geht nicht (Zugang zu GitHub im CT?) – nichts geändert."; exit 1; }

echo "== 2/5 Umstellen (lokale Änderungen in $REGIE bleiben liegen, wenn es welche gibt)"
als_pipeline "git -C $REGIE status --porcelain | head -5"
als_pipeline "git -C $REGIE checkout -q --detach origin/$BRANCH && git -C $REGIE log -1 --format='%h %ad %s' --date=short" \
  || { echo "checkout fehlgeschlagen – nichts neu gestartet."; exit 1; }

echo "== 3/5 Paket nachziehen (editable; nur nötig, wenn Abhängigkeiten dazukamen)"
als_pipeline "cd $REGIE && .venv/bin/pip install -q -e . 2>&1 | tail -2"

echo "== 4/5 Lern-Bot neu starten"
pct exec "$CT" -- sh -c "systemctl restart clip-lernbot; sleep 6; systemctl is-active clip-lernbot; journalctl -u clip-lernbot -n 8 --no-pager -o cat"

echo "== 5/5 Probe: lernstand kennt Cooldown/Frische-Quote?"
als_pipeline "cd $REGIE && .venv/bin/pipeline lernstand 2>/dev/null | grep -iE 'cooldown|frisch|abwechslung|ziel-dauer' | head -4"
echo "== fertig – nächster Entwurf im Lern-Bot mit /entwurf oder nach ✅ fertig"
