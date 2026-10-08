#!/usr/bin/env bash
# Im CT als root:  bash benutzer-anlegen.sh <name> --probe   (zeigt nur)   ·   bash benutzer-anlegen.sh <name>
# Mehrbenutzer (docs/MEHRBENUTZER.md, „Neuen Freund anlegen“): EIN Befehl richtet einen Freund ein – eigener Benutzer
# clip-<name> ohne Anmeldung, eigener Ordner /var/lib/clip-benutzer/<name> auf dem Freunde-Volume, eigener Bot, seine
# Dienste (nur für diesen Namen eingeschaltet) und zum Schluss die Prüfung, dass alles getrennt ist.
#   1 Prüfen: Name, Freunde-Volume, Code, deine Sperre · 2 Zugänge, verdeckt: Bot-Token, Epic-Konto-ID, Telegram-Zahl
#   3 Benutzer, Ordner, Konfig · 4 deine Sperrdatei für alle lesbar · 5 Dienst-Vorlagen · 6 Vorab-Prüfung
#   7 Einrichten in seiner Sandbox (Datenbank, Whisper-Modell, Musik) · 8 Einschalten · 9 Prüfung und Bot-Link
#   Zusatz (j/N): deine eigenen Rechte schärfen – nur chmod, mit Rückweg-Skript
# Wiederholbar: Fertiges wird übersprungen, gefragt wird nur, was fehlt. Gelöscht wird nichts. Zugänge stehen nur in
# <Ordner>/.env (root:clip-<name>, 0640) – nie im Log, nie auf dem Bildschirm, nie auf einer Befehlszeile.
# Ausschalten (Daten bleiben): bash benutzer-stilllegen.sh <name>
set -euo pipefail
BENUTZER_DIR="${BENUTZER_DIR:-/var/lib/clip-benutzer}"
PROD="${PROD:-/opt/clip-pipeline}"
REGIE="${REGIE:-/opt/clip-regie}"
UNITS="${UNITS:-/etc/systemd/system}"
PUFFER="${PUFFER:-/srv/puffer}"
FLORIAN_DIR="${FLORIAN_DIR:-/var/lib/clip-pipeline}"
RECHTE_ABLAGE="${RECHTE_ABLAGE:-/root/benutzer-rechte}"
HIER="$(cd "$(dirname "$0")" && pwd)"
ZEIT="$(date +%Y%m%d-%H%M%S)"
NAME=""
PROBE=0
aufruf() { echo "Aufruf: bash $0 <name> [--probe]   (Name: 2–27 Zeichen a-z, 0-9, -, vorn ein Buchstabe)"; exit 2; }
while [ $# -gt 0 ]; do
  case "$1" in
    --probe) PROBE=1 ;;
    -*) aufruf ;;
    *) [ -z "$NAME" ] || aufruf; NAME="$1" ;;
  esac
  shift
done
[[ "$NAME" =~ ^[a-z][a-z0-9-]{1,26}$ ]] || aufruf
I="$BENUTZER_DIR/$NAME"
U="clip-$NAME"

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
abbruch() { echo "Abgebrochen – $1"; exit 1; }
# Python als pipeline mit sauberer Umgebung – so sieht es auch dein Dienst (deine wirksame Konfig, nie die eines Freundes)
florian() { (cd / && runuser -u pipeline -- env -i PATH=/usr/bin:/bin LANG=C.UTF-8 "$PROD/.venv/bin/python" -I -c "$@"); }
# Steht ein Zugang schon in seiner .env? (nur ob – der Wert wird nie gelesen oder gezeigt)
hat() { [ -f "$I/.env" ] && grep -Eq "^[[:space:]]*$1=[^[:space:]]" "$I/.env"; }
stand() { if [ -e "$1" ]; then stat -c '%U:%G %a' "$1"; fi; }
# Besitzer und Rechte setzen – nur, was abweicht
setze() {
  if [ "$(stand "$1")" = "$2 $3" ]; then echo "   $1: schon richtig ($2 $3)"; return 0; fi
  tu chown "$2" "$1"
  tu chmod "$3" "$1"
}
ordner() {
  [ ! -L "$1" ] || abbruch "$1 ist ein Link – ich ändere nichts, bitte melden."
  if [ -e "$1" ] && [ ! -d "$1" ]; then abbruch "$1 ist kein Ordner – ich ändere nichts, bitte melden."; fi
  [ -d "$1" ] || tu mkdir "$1"
  setze "$1" "$2" "$3"
}
zeige_log() {
  local lauf
  journalctl --sync 2>/dev/null || true
  lauf="$(systemctl show -p InvocationID --value "$1" 2>/dev/null || true)"
  [ -n "$lauf" ] || return 0
  journalctl -o cat --no-pager "_SYSTEMD_INVOCATION_ID=$lauf" 2>/dev/null | grep -v '^{' | tail -n 25 | sed 's/^/   | /' || true
}

[ "$(id -u)" = 0 ] || { echo "Bitte als root im CT ausführen."; exit 1; }
[ ! -d /etc/pve ] || { echo "Das ist der Proxmox-Host – bitte im CT ausführen (pct enter 102)."; exit 1; }
if [ "$PROBE" = 1 ]; then echo "PROBE: Ich zeige nur, was ich tun würde, und ändere nichts."; fi

sag "1/9 Prüfen: Name, Freunde-Volume, Code, deine Sperre"
# Reserviert: Namen, die man mit deinen Diensten oder Ordnern verwechseln würde (clip-bot, clip-pipeline, …)
RESERVIERT=" pipeline benutzer freund root admin "
for d in "$PROD"/deploy/systemd/clip-*.service; do
  [ -e "$d" ] || continue
  n="${d##*/clip-}"; RESERVIERT="$RESERVIERT${n%.service} "
done
case "$RESERVIERT" in
  *" $NAME "*) echo "Den Namen $NAME nehme ich nicht – clip-$NAME sähe aus wie einer deiner Dienste oder Ordner."; exit 2 ;;
esac
getent passwd pipeline >/dev/null || abbruch "Benutzer pipeline fehlt – ist das der CT clips?"
getent group render >/dev/null || abbruch "Gruppe render fehlt – ohne sie starten die Dienste nicht (Grafikchip). Bitte melden."
[ -x "$PROD/.venv/bin/pipeline" ] || abbruch "$PROD/.venv/bin/pipeline fehlt – ist die Pipeline installiert?"
for v in bot scan abend einrichten pruefen; do
  [ -f "$HIER/clip-freund-$v@.service" ] || abbruch "$HIER/clip-freund-$v@.service fehlt – bitte deploy/benutzer/ vollständig."
done
[ -f "$HIER/benutzer-pruefen.sh" ] || abbruch "$HIER/benutzer-pruefen.sh fehlt – bitte deploy/benutzer/ vollständig."
# Freunde-Volume: ein eigener Speicher – nicht die CT-Platte (deine Datenbank), nicht dein Puffer (M53)
[ -d "$BENUTZER_DIR" ] && [ ! -L "$BENUTZER_DIR" ] \
  || abbruch "$BENUTZER_DIR fehlt – erst das Freunde-Volume: freunde-volume.sh auf pve-mini (docs/MEHRBENUTZER.md)."
DEV="$(stat -c %d "$BENUTZER_DIR")"
for p in / "$PUFFER"; do
  if [ -e "$p" ] && [ "$(stat -c %d "$p")" = "$DEV" ]; then
    abbruch "$BENUTZER_DIR liegt auf demselben Speicher wie $p – erst das Freunde-Volume: freunde-volume.sh auf pve-mini (docs/MEHRBENUTZER.md, Abschnitt Speicher)."
  fi
done
[ "$(stand "$BENUTZER_DIR")" = "root:root 711" ] \
  || abbruch "$BENUTZER_DIR hat $(stand "$BENUTZER_DIR") statt root:root 711 – bitte freunde-volume.sh noch einmal laufen lassen."
echo "Freunde-Volume $BENUTZER_DIR: eigener Speicher, root, 0711"
# Sein Ordner: neu, oder schon von hier angelegt (Marke mit seinem Namen) – sonst gehört er jemand anderem
[ ! -L "$I" ] || abbruch "$I ist ein Link – ich ändere nichts, bitte melden."
if [ -d "$I" ]; then
  if [ -f "$I/.clip-benutzer" ]; then
    [ "$(head -c 64 "$I/.clip-benutzer" | tr -d '[:space:]')" = "$NAME" ] \
      || abbruch "$I/.clip-benutzer nennt einen anderen Namen – falscher Ordner? Ich ändere nichts."
    echo "$I gibt es schon – ich ergänze nur, was fehlt."
  elif [ -n "$(ls -A "$I")" ]; then
    abbruch "$I gibt es schon mit unbekanntem Inhalt (ohne Marke) – ich ändere nichts, bitte melden."
  fi
  if [ -e "$I/kein-lager" ] || [ -L "$I/kein-lager" ]; then
    abbruch "$I/kein-lager gibt es – das darf nicht sein (M10). Ich ändere nichts, bitte melden."
  fi
elif [ -e "$I" ]; then
  abbruch "$I ist kein Ordner – ich ändere nichts, bitte melden."
fi
NEUER_BENUTZER=0
if EINTRAG="$(getent passwd "$U")"; then
  IFS=: read -r _ _ _ GID _ _ SHELL_ <<< "$EINTRAG"
  [ "$GID" = "$(getent group "$U" | cut -d: -f3)" ] && [ "$SHELL_" = /usr/sbin/nologin ] \
    || abbruch "Benutzer $U gibt es schon, aber nicht so, wie ich ihn anlege (Gruppe $U, ohne Anmeldung) – ich ändere nichts."
  echo "Benutzer $U: schon da"
else
  ! getent group "$U" >/dev/null || abbruch "Gruppe $U gibt es schon ohne Benutzer – ich ändere nichts, bitte melden."
  NEUER_BENUTZER=1
fi
# Deine wirksame Sperre (wie in alles-aktualisieren.sh) muss die sein, die die Vorlagen einbinden (M47)
GEBUNDEN="$(awk -F= '$1 == "BindReadOnlyPaths" {split($2, t, " "); print t[1]; exit}' "$HIER/clip-freund-bot@.service")"
SPERRE="$(florian 'from clip_pipeline import konfig, sperre; print(sperre.pfad(konfig.lade()))' | tail -n 1)" || SPERRE=""
[ -n "$SPERRE" ] || abbruch "deine Sperre ließ sich nicht abfragen ($PROD/.venv/bin/python als pipeline) – nichts geändert."
[ "$SPERRE" = "$GEBUNDEN" ] \
  || abbruch "deine Sperre ist $SPERRE, die Vorlagen binden $GEBUNDEN ein – $NAME rechnete neben dir her. Nichts geändert, bitte melden."
[ ! -L "$SPERRE" ] || abbruch "$SPERRE ist ein Link – ich ändere nichts, bitte melden."
echo "Deine Sperre: $SPERRE (= die in den Vorlagen eingebundene)"
TOML=""
if [ ! -f "$I/instanz.toml" ]; then
  # Aus deiner wirksamen Konfig nur Sperre, Rechnerwerte und Waffen-Nummern (benutzer.instanz_toml, M15)
  TOML="$(florian 'import sys
from clip_pipeline import benutzer, konfig
print(benutzer.instanz_toml(konfig.lade(), sys.argv[1]), end="")' "$NAME")" || TOML=""
  [ -n "$TOML" ] || abbruch "die instanz.toml ließ sich aus deiner Konfig nicht bauen – nichts geändert, bitte melden."
fi
if ! frage "Freund $NAME jetzt anlegen bzw. vervollständigen (Benutzer $U, Ordner $I, seine Dienste)?"; then
  echo "Abgebrochen – nichts verändert."; exit 1
fi

sag "2/9 Zugänge – verdeckt: nichts davon erscheint auf dem Bildschirm oder im Log"
TOKEN=""
EPIC=""
TG_ID=""
if hat LEARN_BOT_TOKEN; then echo "Bot-Token: schon da – bleibt"
elif [ "$PROBE" = 1 ]; then echo "   (Probe: würde den Bot-Token seines Bots von @BotFather verdeckt abfragen)"
else
  read -r -s -p "   Bot-Token seines Bots von @BotFather (unsichtbar): " TOKEN || true
  echo
  [[ "$TOKEN" =~ ^[0-9]{5,15}:[A-Za-z0-9_-]{30,64}$ ]] \
    || abbruch "das sieht nicht wie ein Bot-Token aus (123456789:AA…) – nichts geändert."
  printf '%s' "$TOKEN" | bash "$HIER/benutzer-pruefen.sh" --token-frei "$NAME" \
    || abbruch "diesen Bot-Token nutzt schon jemand – für jeden Freund einen eigenen Bot bei @BotFather. Nichts geändert."
  echo "Bot-Token: neu, noch nirgends eingetragen"
fi
if hat CLIP_EPIC_ID; then echo "Epic-Konto-ID: schon da – bleibt"
elif [ "$PROBE" = 1 ]; then echo "   (Probe: würde seine Epic-Konto-ID verdeckt abfragen)"
else
  read -r -s -p "   Seine Epic-Konto-ID (32 Zeichen, epicgames.com → Konto; unsichtbar): " EPIC || true
  echo
  EPIC="${EPIC,,}"
  [[ "$EPIC" =~ ^[0-9a-f]{32}$ ]] || abbruch "eine Epic-Konto-ID hat 32 Zeichen aus 0-9 und a-f – nichts geändert."
  echo "Epic-Konto-ID: angenommen"
fi
# --- KOPPLUNG (Schritt 4, Einladungslink) ----------------------------------------------------------------------------
# Hier setzt später `pipeline benutzer koppeln` die Telegram-Zahl ein: Der Freund tippt den Link seines Bots an, der Bot
# merkt sich seine Zahl. Bis dahin fragt das Skript sie freiwillig verdeckt ab; ohne Zahl bleibt nur sein Bot aus.
if hat LEARN_BOT_ALLOWED_USER_ID; then echo "Telegram-Zahl: schon da – bleibt"
elif [ "$PROBE" = 1 ]; then echo "   (Probe: würde seine Telegram-Zahl verdeckt abfragen – Enter = später)"
else
  read -r -s -p "   Seine Telegram-Zahl (zeigt @userinfobot; Enter = später): " TG_ID || true
  echo
  [ -z "$TG_ID" ] || [[ "$TG_ID" =~ ^[0-9]{5,15}$ ]] || abbruch "eine Telegram-Zahl hat nur Ziffern – nichts geändert."
  if [ -n "$TG_ID" ]; then echo "Telegram-Zahl: angenommen"; else echo "Telegram-Zahl: später – sein Bot bleibt bis dahin aus"; fi
fi
# --- Ende KOPPLUNG ---------------------------------------------------------------------------------------------------

sag "3/9 Benutzer $U (ohne Anmeldung), Ordner, Marken, Konfig"
if [ "$NEUER_BENUTZER" = 1 ]; then
  tu useradd --system --user-group --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin "$U"
fi
ordner "$I" "root:$U" 750
# Die Marke gleich danach: Bricht ein Lauf später ab, erkennt der nächste den Ordner als seinen wieder
if [ -f "$I/.clip-benutzer" ]; then echo "   $I/.clip-benutzer: schon da"
elif [ "$PROBE" = 1 ]; then echo "   (Probe: würde $I/.clip-benutzer mit dem Namen $NAME schreiben)"
else printf '%s\n' "$NAME" > "$I/.clip-benutzer"; echo "   $I/.clip-benutzer geschrieben"; fi
setze "$I/.clip-benutzer" "root:$U" 640
for o in db daten regie musik material sfx cache; do ordner "$I/$o" "$U:$U" 700; done
# Marken und Eingang seines Puffers – als er selbst angelegt (root fasst in seinen eigenen Ordnern nichts an)
for m in .clip-speicher .clip-puffer; do
  if [ -e "$I/daten/$m" ] || [ -L "$I/daten/$m" ]; then echo "   $I/daten/$m: schon da"
  else tu runuser -u "$U" -- touch "$I/daten/$m"; fi
done
for o in eingang replays; do
  if [ -e "$I/daten/$o" ] || [ -L "$I/daten/$o" ]; then echo "   $I/daten/$o: schon da"
  else tu runuser -u "$U" -- mkdir "$I/daten/$o"; fi
done
ENV_NEU=0
if [ "$PROBE" = 1 ]; then
  if [ -f "$I/.env" ]; then echo "   $I/.env: schon da"; else echo "   (Probe: würde $I/.env mit seinen Zugängen schreiben)"; fi
elif [ ! -f "$I/.env" ] || [ -n "$TOKEN$EPIC$TG_ID" ]; then
  (
    umask 077
    if [ ! -f "$I/.env" ]; then
      printf '# Zugänge von %s – geschrieben von benutzer-anlegen.sh am %s. Gehört root, der Freund liest sie nur.\n' \
        "$NAME" "$(date +%d.%m.%Y)" > "$I/.env"
    elif [ -n "$(tail -c 1 "$I/.env")" ]; then
      echo >> "$I/.env"
    fi
    if [ -n "$TOKEN" ]; then printf 'LEARN_BOT_TOKEN=%s\n' "$TOKEN" >> "$I/.env"; fi
    if [ -n "$EPIC" ]; then printf 'CLIP_EPIC_ID=%s\n' "$EPIC" >> "$I/.env"; fi
    if [ -n "$TG_ID" ]; then printf 'LEARN_BOT_ALLOWED_USER_ID=%s\n' "$TG_ID" >> "$I/.env"; fi
  )
  ENV_NEU=1
  echo "   $I/.env geschrieben (Werte nicht angezeigt)"
else
  echo "   $I/.env: schon da"
fi
setze "$I/.env" "root:$U" 640
if [ -f "$I/instanz.toml" ]; then echo "   $I/instanz.toml: schon da – bleibt"
elif [ "$PROBE" = 1 ]; then
  echo "   (Probe: würde $I/instanz.toml schreiben:)"
  printf '%s\n' "$TOML" | sed 's/^/   | /'
else
  ( umask 077; printf '%s\n' "$TOML" > "$I/instanz.toml" )
  echo "   $I/instanz.toml geschrieben"
fi
setze "$I/instanz.toml" "root:$U" 640

sag "4/9 Deine Sperrdatei: für alle lesbar – so wartet $NAME auf dich und du auf ihn"
# Fehlt sie, lege ich sie als pipeline an – eine Instanz legt nie eine eigene an (M41)
if [ ! -e "$SPERRE" ]; then tu runuser -u pipeline -- touch "$SPERRE"; fi
if [ "$(stat -c %a "$SPERRE" 2>/dev/null || true)" = 644 ]; then echo "   $SPERRE: schon 0644"
else tu chmod 644 "$SPERRE"; fi

sag "5/9 Dienst-Vorlagen hinlegen (eingeschaltet wird in Schritt 8 nur $NAME)"
NEU=0
for q in "$HIER"/clip-freund-*@.service "$HIER"/clip-freund-*@.timer; do
  [ -f "$q" ] || continue
  z="$UNITS/${q##*/}"
  if [ ! -e "$z" ]; then tu install -m 644 "$q" "$z"; NEU=1
  elif cmp -s "$q" "$z"; then echo "   ${q##*/}: schon da"
  else echo "   ℹ️  ${q##*/} weicht vom Repo ab (von dir angepasst?) – bleibt. Vergleich: diff $z $q"; fi
done
if [ "$NEU" = 1 ]; then tu systemctl daemon-reload; fi

sag "6/9 Vorab-Prüfung (Rechte, Sperre, Zugänge) – bevor etwas eingeschaltet wird"
if [ "$PROBE" = 1 ]; then echo "   \$ bash $HIER/benutzer-pruefen.sh $NAME --vorab"
elif ! bash "$HIER/benutzer-pruefen.sh" "$NAME" --vorab < /dev/null; then
  abbruch "die Vorab-Prüfung hat etwas gefunden (siehe ❌) – nichts eingeschaltet. Danach nochmal: bash $0 $NAME"
fi

sag "7/9 Einrichten in seiner Sandbox: Datenbank, Whisper-Modell (~480 MB), Musik – ein paar Minuten"
EINRICHTEN="clip-freund-einrichten@$NAME.service"
if [ "$PROBE" = 1 ]; then echo "   \$ systemctl start $EINRICHTEN"
else
  echo "   läuft … (mitlesen: journalctl -fu $EINRICHTEN)"
  if systemctl start "$EINRICHTEN"; then RC=0; else RC=$?; fi
  zeige_log "$EINRICHTEN"
  [ "$RC" = 0 ] || abbruch "Einrichten oder die Prüfung in seiner Sandbox ging nicht (siehe oben) – nichts eingeschaltet. Log: journalctl -u $EINRICHTEN -n 50"
fi

sag "8/9 Einschalten – nur für $NAME"
AN=("clip-freund-scan@$NAME.timer" "clip-freund-abend@$NAME.timer")
BOT="clip-freund-bot@$NAME.service"
if hat LEARN_BOT_ALLOWED_USER_ID; then AN+=("$BOT")
elif [ "$PROBE" = 1 ]; then echo "   (Probe: $BOT kommt dazu, sobald seine Telegram-Zahl in $I/.env steht)"
else echo "   $BOT bleibt aus, bis seine Telegram-Zahl da ist (dann nochmal: bash $0 $NAME)"; fi
BOT_AN=0
for e in "${AN[@]}"; do
  if systemctl is-enabled -q "$e" 2>/dev/null; then echo "   $e: schon an"
  else tu systemctl enable --now "$e"; [ "$e" != "$BOT" ] || BOT_AN=1; fi
done
# Neue Zugänge bei laufendem Bot: neu starten – er liest seine .env nur beim Start
if [ "$ENV_NEU" = 1 ] && [ "$BOT_AN" = 0 ] && systemctl is-active -q "$BOT" 2>/dev/null; then tu systemctl restart "$BOT"; fi

sag "9/9 Prüfung: ist alles getrennt?"
if [ "$PROBE" = 1 ]; then echo "   \$ bash $HIER/benutzer-pruefen.sh $NAME"
elif ! bash "$HIER/benutzer-pruefen.sh" "$NAME" < /dev/null; then
  echo "❌ Die Prüfung hat etwas gefunden (siehe oben). Bis es behoben ist, ausschalten (Daten bleiben):"
  echo "   bash $HIER/benutzer-stilllegen.sh $NAME"
  exit 1
fi

sag "Zusatz: deine eigenen Dateien auch außerhalb der Sandbox schützen (nur chmod, mit Rückweg-Skript)"
# Deine Dienste laufen als pipeline und merken davon nichts. Der Ordner bleibt für andere passierbar (zur Sperrdatei),
# alles darin außer der Sperrdatei nur noch für dich; dazu deine .env und lokal.toml. /srv (Puffer, Lager) bleibt, wie es
# ist – dort sieht ein Freund in seiner Sandbox ohnehin nichts.
SCHAERFEN=()
schaerfe() {
  local alt neu
  [ -e "$1" ] && [ ! -L "$1" ] || return 0
  alt="$(stat -c %a "$1")"
  neu="$(printf '%o' $(( 8#$alt & ~8#$2 )))"
  [ "$neu" = "$alt" ] || SCHAERFEN+=("$neu $alt $1")
}
schaerfe "$FLORIAN_DIR" 066
for p in "$FLORIAN_DIR"/* "$FLORIAN_DIR"/.[!.]*; do
  [ "$p" -ef "$SPERRE" ] || schaerfe "$p" 077
done
for p in "$PROD/.env" "$REGIE/.env" "$PROD/config/lokal.toml" "$REGIE/config/lokal.toml"; do schaerfe "$p" 077; done
if [ "${#SCHAERFEN[@]}" = 0 ]; then
  echo "   schon geschärft – nichts zu tun"
else
  for e in "${SCHAERFEN[@]}"; do read -r neu alt pfad <<< "$e"; echo "   chmod $neu $pfad   (bisher $alt)"; done
  if [ "$PROBE" = 1 ]; then echo "   (Probe: würde fragen – ohne j bleibt alles, wie es ist)"
  elif frage "Deine Rechte so schärfen? (Rückweg-Skript kommt nach $RECHTE_ABLAGE)"; then
    mkdir -p "$RECHTE_ABLAGE"
    RUECK="$RECHTE_ABLAGE/zurueck-$ZEIT.sh"
    {
      printf '#!/usr/bin/env bash\n# Rückweg zu benutzer-anlegen.sh (%s): deine Rechte wie vorher.\nset -euo pipefail\n' "$ZEIT"
      for e in "${SCHAERFEN[@]}"; do read -r neu alt pfad <<< "$e"; printf 'chmod %s %q\n' "$alt" "$pfad"; done
    } > "$RUECK"
    chmod 700 "$RUECK"
    for e in "${SCHAERFEN[@]}"; do read -r neu alt pfad <<< "$e"; chmod "$neu" "$pfad"; done
    echo "   geschärft. Rückweg: bash $RUECK"
  else
    echo "   bleibt, wie es ist"
  fi
fi

if [ "$PROBE" = 1 ]; then sag "Probe fertig – nichts verändert. Echt:  bash $0 $NAME"; exit 0; fi
sag "Fertig: $NAME ist eingerichtet und von dir und den anderen Freunden getrennt."
echo "Für $NAME: docs/FREUNDE.md – den Bot-Link oben öffnen und Start tippen; in Fortnite Replays einschalten."
echo "Prüfen:            bash $HIER/benutzer-pruefen.sh $NAME"
echo "Log seines Bots:   journalctl -u $BOT -f"
echo "Match nachholen:   bash $HIER/benutzer-befehl.sh $NAME process <ID>"
echo "Ausschalten:       bash $HIER/benutzer-stilllegen.sh $NAME   (Daten bleiben)"
