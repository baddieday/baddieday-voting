#!/usr/bin/env bash
# Im CT als root:  bash benutzer-anlegen.sh <name> --probe   (zeigt nur)   ·   bash benutzer-anlegen.sh <name>
# Mehrbenutzer (docs/MEHRBENUTZER.md, „Neuen Freund anlegen“): EIN Befehl richtet einen Freund ein – eigener Benutzer
# clip-<name> ohne Anmeldung, eigener Ordner /var/lib/clip-benutzer/<name> auf dem Freunde-Volume, eigener Bot, seine
# Dienste (nur für diesen Namen eingeschaltet) und zum Schluss die Prüfung, dass alles getrennt ist.
#   1 Prüfen: Name, Freunde-Volume, Code, deine Sperre · 2 Zugänge, verdeckt: Bot-Token, Epic-Konto-ID
#   3 Benutzer, Ordner, Konfig · 4 deine Sperrdatei für alle lesbar · 5 Dienst-Vorlagen · 6 Vorab-Prüfung
#   7 Einrichten in seiner Sandbox (Datenbank, Whisper-Modell, Musik) · 8 Telegram: Einladungslink, er drückt Start
#   9 Briefkasten (Stufe 2, j/N): Schlüssel, Hostschlüssel, Zeile für den vServer, Probe-Abholung in seiner Sandbox
#   10 Einschalten (das Abholen nur nach grüner Probe)
#   11 Lager (Stufe 2, j/N, nur bei wachem pve-big): freunde/<name> im Lager, Einhängepunkt, Schalter, Probe der
#      Bindung in seiner Sandbox, Rundgang clip-lager-freunde.service einmal einschalten · 12 Prüfung und Bot-Link
#   Zusatz (j/N): deine eigenen Rechte schärfen – nur chmod, nur was pipeline gehört, mit Rückweg-Skript
# Wiederholbar: Fertiges wird übersprungen, gefragt wird nur, was fehlt. Gelöscht wird nichts. Zugänge stehen nur in
# <Ordner>/.env (root:clip-<name>, 0640) – nie im Log, nie auf dem Bildschirm, nie auf einer Befehlszeile. Die
# Schlüssel für den Briefkasten liegen nur in <Ordner>/briefkasten (root:clip-<name>); gezeigt werden nur die
# öffentlichen.
# Ausschalten (Daten bleiben): bash benutzer-stilllegen.sh <name>
set -euo pipefail
BENUTZER_DIR="${BENUTZER_DIR:-/var/lib/clip-benutzer}"
PROD="${PROD:-/opt/clip-pipeline}"
REGIE="${REGIE:-/opt/clip-regie}"
UNITS="${UNITS:-/etc/systemd/system}"
PUFFER="${PUFFER:-/srv/puffer}"
FLORIAN_DIR="${FLORIAN_DIR:-/var/lib/clip-pipeline}"
RECHTE_ABLAGE="${RECHTE_ABLAGE:-/root/benutzer-rechte}"
LAGER_ABLAGE="${LAGER_ABLAGE:-/root/benutzer-lager}"   # Schritt „Lager“: alte instanz.toml und Rückweg-Skript
BK_CONF="${BK_CONF:-/etc/clip-briefkasten.conf}"   # Adressen des vServers – für alle Freunde gleich
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
  for a in "$@"; do case "$a" in ""|*[!A-Za-z0-9_./:=,@%+-]*) z="$z '$a'" ;; *) z="$z $a" ;; esac; done
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
# Mit Telegram verbunden = seine Telegram-Zahl steht in .env (unter einem der Namen, die sein Lern-Bot liest)
verbunden() { hat LEARN_BOT_ALLOWED_USER_ID || hat TELEGRAM_ALLOWED_USER_ID; }
# Einen Zugang an seine .env anhängen – nur mit dem eingebauten printf, nie auf einer Befehlszeile
env_dazu() {
  ( umask 077
    if [ -n "$(tail -c 1 "$I/.env")" ]; then echo >> "$I/.env"; fi
    printf '%s=%s\n' "$1" "$2" >> "$I/.env" )
}
# Läuft eine Einheit gerade? Auch ein Schritt (oneshot), der noch „activating“ ist – is-active -q sieht den nicht
laeuft() {
  case "$(systemctl is-active "$1" 2>/dev/null || true)" in active|activating|deactivating|reloading|refreshing) return 0 ;; esac
  return 1
}
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
# Liest einladung.json bzw. kopplung.json, die SEIN Dienst in seinen Ordner db/ geschrieben hat: root folgt keinem Link,
# liest nur eine normale Datei, höchstens 4 KB, und gibt nur Geprüftes aus – den Link nur vom laufenden Einladungs-Dienst
# (Lauf-Nummer), sonst Zahl und Vorname (nur druckbare Zeichen) durch einen Tab getrennt.
FREUND_JSON_PY="$(cat <<'PY'
import json, os, re, stat, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
art, pfad = sys.argv[1], sys.argv[2]
try:
    fd = os.open(pfad, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as datei:
        if not stat.S_ISREG(os.fstat(datei.fileno()).st_mode):
            sys.exit(1)
        daten = json.loads(datei.read(4096))
except (OSError, ValueError):
    sys.exit(1)
if not isinstance(daten, dict):
    sys.exit(1)
if art == "link":
    link = str(daten.get("link", ""))
    if not sys.argv[3] or daten.get("lauf") != sys.argv[3] or not re.fullmatch(
            r"https://t\.me/[A-Za-z0-9_]{5,32}\?start=[A-Za-z0-9_-]{16,64}", link):
        sys.exit(1)
    print(link)
    sys.exit(0)
zahl = daten.get("id")
if type(zahl) is not int or not 0 < zahl < 2 ** 52:
    sys.exit(1)
print(f"{zahl}\t" + "".join(z for z in str(daten.get("vorname", "")) if z.isprintable())[:64])
PY
)"
# Seine Telegram-Zahl aus einer Kopplung (auch aus einem früheren Lauf, z. B. nach Strg+C) – nie angezeigt
kopplung_lesen() {
  local zeile
  TG_ID=""
  TG_NAME=""
  zeile="$(python3 -I -c "$FREUND_JSON_PY" id "$I/db/kopplung.json" 2>/dev/null || true)"
  [[ "$zeile" == *$'\t'* ]] || return 0
  TG_ID="${zeile%%$'\t'*}"
  TG_NAME="${zeile#*$'\t'}"
  [[ "$TG_ID" =~ ^[1-9][0-9]{0,15}$ ]] || { TG_ID=""; TG_NAME=""; }
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

sag "1/12 Prüfen: Name, Freunde-Volume, Code, deine Sperre"
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
for v in bot scan abend einrichten pruefen koppeln abholen lager; do
  [ -f "$HIER/clip-freund-$v@.service" ] || abbruch "$HIER/clip-freund-$v@.service fehlt – bitte deploy/benutzer/ vollständig."
done
for s in benutzer-pruefen.sh benutzer-befehl.sh lager-freunde.sh clip-lager-freunde.service; do
  [ -f "$HIER/$s" ] || abbruch "$HIER/$s fehlt – bitte deploy/benutzer/ vollständig."
done
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

sag "2/12 Zugänge – verdeckt: nichts davon erscheint auf dem Bildschirm oder im Log"
TOKEN=""
EPIC=""
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
# Seine Telegram-Zahl fragt das Skript nicht ab – die kommt in Schritt 8 über den Einladungslink

sag "3/12 Benutzer $U (ohne Anmeldung), Ordner, Marken, Konfig"
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
elif [ ! -f "$I/.env" ] || [ -n "$TOKEN$EPIC" ]; then
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

sag "4/12 Deine Sperrdatei: für alle lesbar – so wartet $NAME auf dich und du auf ihn"
# Fehlt sie, lege ich sie als pipeline an – eine Instanz legt nie eine eigene an (M41)
if [ ! -e "$SPERRE" ]; then tu runuser -u pipeline -- touch "$SPERRE"; fi
if [ "$(stat -c %a "$SPERRE" 2>/dev/null || true)" = 644 ]; then echo "   $SPERRE: schon 0644"
else tu chmod 644 "$SPERRE"; fi

sag "5/12 Dienst-Vorlagen hinlegen (eingeschaltet wird in Schritt 10 nur $NAME)"
NEU=0
for q in "$HIER"/clip-freund-*@.service "$HIER"/clip-freund-*@.timer; do
  [ -f "$q" ] || continue
  z="$UNITS/${q##*/}"
  if [ ! -e "$z" ]; then tu install -m 644 "$q" "$z"; NEU=1
  elif cmp -s "$q" "$z"; then echo "   ${q##*/}: schon da"
  else echo "   ℹ️  ${q##*/} weicht vom Repo ab (von dir angepasst?) – bleibt. Vergleich: diff $z $q"; fi
done
if [ "$NEU" = 1 ]; then tu systemctl daemon-reload; fi

sag "6/12 Vorab-Prüfung (Rechte, Sperre, Zugänge) – bevor etwas eingeschaltet wird"
if [ "$PROBE" = 1 ]; then echo "   \$ bash $HIER/benutzer-pruefen.sh $NAME --vorab"
elif ! bash "$HIER/benutzer-pruefen.sh" "$NAME" --vorab < /dev/null; then
  abbruch "die Vorab-Prüfung hat etwas gefunden (siehe ❌) – nichts eingeschaltet. Danach nochmal: bash $0 $NAME"
fi

sag "7/12 Einrichten in seiner Sandbox: Datenbank, Whisper-Modell (~480 MB), Musik – ein paar Minuten"
EINRICHTEN="clip-freund-einrichten@$NAME.service"
if [ "$PROBE" = 1 ]; then echo "   \$ systemctl start $EINRICHTEN"
else
  echo "   läuft … (mitlesen: journalctl -fu $EINRICHTEN)"
  if systemctl start "$EINRICHTEN"; then RC=0; else RC=$?; fi
  zeige_log "$EINRICHTEN"
  [ "$RC" = 0 ] || abbruch "Einrichten oder die Prüfung in seiner Sandbox ging nicht (siehe oben) – nichts eingeschaltet. Log: journalctl -u $EINRICHTEN -n 50"
fi

sag "8/12 Telegram: Einladungslink – $NAME tippt ihn an und drückt Start (keine Telegram-Zahl suchen)"
# In seiner Sandbox wartet clip-freund-koppeln@ bis zu 15 min auf „/start <code>“ und schreibt seine Zahl nach
# db/kopplung.json; hier wird sie gelesen und als LEARN_BOT_ALLOWED_USER_ID in seine .env eingetragen (die liest sein
# Lern-Bot). Je Bot nur ein Empfänger: Ohne Zahl holt sein Bot nie Nachrichten ab, er geht erst in Schritt 10 an.
KOPPELN="clip-freund-koppeln@$NAME.service"
BOT="clip-freund-bot@$NAME.service"
# Einladung starten, dir den Link zeigen und warten, bis er Start drückt oder die Frist um ist. Strg+C beendet nur das
# Warten – die Einladung gilt weiter, der nächste Lauf dieses Skripts trägt seine Zahl dann ein.
koppeln() {
  local lauf="" link="" vorher="" ende=0 pid i
  if laeuft "$BOT"; then tu systemctl stop "$BOT"; fi   # ohne Zahl holt er nichts ab – sicher ist sicher
  # Gezeigt wird nur der Link des neuen Laufs (bzw. der Einladung, die schon läuft) – nie einer, der liegen blieb
  if laeuft "$KOPPELN"; then echo "   Eine Einladung von vorhin läuft noch – ich zeige ihren Link."
  else vorher="$(systemctl show -p InvocationID --value "$KOPPELN" 2>/dev/null || true)"; fi
  # Im Hintergrund: systemctl kehrt erst zurück, wenn die Einladung endet (läuft schon eine, wartet es auf diese)
  echo "   \$ systemctl start $KOPPELN"
  systemctl start "$KOPPELN" > /dev/null 2>&1 &
  pid=$!
  for ((i = 0; i < 30; i++)); do   # bis zu 1 min: Telegram nach dem Namen seines Bots fragen
    kill -0 "$pid" 2>/dev/null || ende=1
    lauf="$(systemctl show -p InvocationID --value "$KOPPELN" 2>/dev/null || true)"
    [ -z "$vorher" ] || [ "$lauf" != "$vorher" ] || lauf=""   # der neue Lauf hat noch nicht begonnen
    link="$(python3 -I -c "$FREUND_JSON_PY" link "$I/db/einladung.json" "$lauf" 2>/dev/null || true)"
    if [ -n "$link" ] || [ "$ende" = 1 ]; then break; fi
    sleep 2
  done
  if [ -z "$link" ]; then
    zeige_log "$KOPPELN"
    echo "   Der Einladungslink kam nicht (siehe oben). Log: journalctl -u $KOPPELN -n 30"
    return 0
  fi
  printf '\n   👉 Schick %s diesen Link – er gilt 15 min und nur einmal:\n\n      %s\n\n' "$NAME" "$link"
  echo "   Er tippt ihn an und drückt in Telegram auf Start. Ich warte … (Strg+C: nicht weiter warten, der Link gilt weiter)"
  UNTERBROCHEN=0
  trap 'UNTERBROCHEN=1' INT
  while [ "$UNTERBROCHEN" = 0 ] && kill -0 "$pid" 2>/dev/null; do sleep 2 || true; done
  trap - INT
  if [ "$UNTERBROCHEN" = 1 ]; then echo; echo "   Nicht weiter gewartet – die Einladung gilt weiter."
  else zeige_log "$KOPPELN"; fi
  kopplung_lesen
}
TG_ID=""
TG_NAME=""
if verbunden; then echo "   Telegram: schon verbunden – bleibt"
elif [ "$PROBE" = 1 ]; then
  echo "   \$ systemctl start $KOPPELN"
  echo "   (Probe: würde dir seinen Einladungslink zeigen und bis zu 15 min warten, bis er Start drückt)"
else
  kopplung_lesen   # schon verbunden in einem früheren Lauf?
  if [ -z "$TG_ID" ]; then koppeln; fi
  if [ -n "$TG_ID" ]; then
    env_dazu LEARN_BOT_ALLOWED_USER_ID "$TG_ID"
    ENV_NEU=1
    echo "   Telegram: verbunden (Name in Telegram: ${TG_NAME:-?}) – seine Zahl steht jetzt in $I/.env (nicht angezeigt)"
  else
    echo "   Telegram: noch nicht verbunden – sein Bot bleibt aus. Später nochmal: bash $0 $NAME (neuer Link)"
  fi
fi

sag "9/12 Briefkasten auf dem vServer (Stufe 2): seine Aufnahmen kommen von selbst"
# docs/BRIEFKASTEN.md. Der PC des Freundes lädt in sein Fach auf deinem vServer, clip-freund-abholen@ holt es alle 2 min
# über das Tailnet in seinen Puffer. Die Adressen des vServers fragt das Skript einmal für alle Freunde ($BK_CONF, gelesen
# per awk, nie ausgeführt). Schlüssel und Hostschlüssel liegen in I/briefkasten (Ordner root:clip-<name> 0750, Dateien
# 0640): Er liest sie (sein Abholer, später das PC-Paket), tauschen kann er sie nicht. Scheitert etwas, bleibt nur das
# Abholen aus – der Rest läuft weiter, der nächste Lauf setzt an derselben Stelle fort.
OKTETT='(0|[1-9][0-9]?|1[0-9]{2}|2[0-4][0-9]|25[0-5])'
TAILNET_RE="^100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.$OKTETT\.$OKTETT$"
OEFFENTLICH_RE='^[A-Za-z0-9.:-]{1,253}$'
# Wie briefkasten-freund.sh auf dem vServer: nackter Ed25519-Schlüssel, Kommentar nur aus harmlosen Zeichen
SCHLUESSEL_RE='^ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI[A-Za-z0-9+/]{43}( [A-Za-z0-9@._:+-]{1,64})?$'
HOSTKEY_RE='^AAAAC3NzaC1lZDI1NTE5AAAAI[A-Za-z0-9+/]{43}$'
BK="$I/briefkasten"
BK_TIMER="clip-freund-abholen@$NAME.timer"
BK_AN=0
conf_wert() { [ -f "$BK_CONF" ] && awk -F= -v k="$1" '$1 == k {sub(/^[^=]*=/, ""); print; exit}' "$BK_CONF"; }
adressen_ok() { [[ "$1" =~ $TAILNET_RE ]] && [[ "$2" =~ $OEFFENTLICH_RE ]] && [[ "$3" =~ ^[1-9][0-9]{0,4}$ ]] && [ "$3" -le 65535 ]; }
normal_oder_nichts() {   # root folgt keinem Link und legt nichts über einen fremden Eintrag
  if [ -L "$1" ] || { [ -e "$1" ] && [ ! -f "$1" ]; }; then abbruch "$1 ist keine normale Datei – ich ändere nichts, bitte melden."; fi
}
# [briefkasten] seiner instanz.toml: „fehlt“, „kaputt“ oder host<TAB>port<TAB>oeffentlich
TOML_BK_PY="$(cat <<'PY'
import sys, tomllib
try:
    with open(sys.argv[1], "rb") as datei:
        b = tomllib.load(datei).get("briefkasten")
except (OSError, ValueError):
    b = "kaputt"
if b is None:
    print("fehlt")
elif not isinstance(b, dict):
    print("kaputt")
else:
    print(f"{b.get('host', '')}\t{b.get('port', 2222)}\t{b.get('oeffentlich', '')}")
PY
)"
# Bliebe die instanz.toml mit dem neuen Abschnitt lesbar? (Exit ≠ 0: nein – dann wird nichts angehängt)
TOML_DAZU_PY='import sys, tomllib
alt = open(sys.argv[1], encoding="utf-8").read()
tomllib.loads(alt + ("\n" if alt and not alt.endswith("\n") else "") + sys.argv[2] + "\n")'
# Ergebnis der Probe-Abholung (Exit-Code und letzte JSON-Zeile von pipeline) -> „ok<TAB>Text“ oder „fehler<TAB>Text“
PROBE_PY="$(cat <<'PY'
import json, sys
rc, zeile = int(sys.argv[1]), sys.argv[2]
try:
    e = json.loads(zeile) if zeile else {}
except ValueError:
    e = {}
e = e if isinstance(e, dict) else {}

def sauber(text):
    return "".join(z for z in str(text) if z.isprintable())[:300]

fehler = e.get("fehler")
fehler = [fehler] if isinstance(fehler, str) else fehler if isinstance(fehler, list) else []
grund = sauber("; ".join(str(f) for f in fehler[:3]))
if rc in (0, 1) and "abgeholt" in e and not e.get("unerreichbar"):
    teile = ["Briefkasten erreicht"]
    if type(e.get("fach_prozent")) is int:
        teile.append(f"Fach zu {e['fach_prozent']} % voll")
    teile.append(f"{e.get('abgeholt', 0)} Datei(en) abgeholt")
    if rc == 1 and grund:
        teile.append(f"einzelne Dateien beim nächsten Lauf ({grund})")
    print("ok\t" + ", ".join(teile))
elif e.get("aus"):
    print("fehler\tkein [briefkasten] in seiner instanz.toml")
elif rc == 4:
    print("fehler\tein Abholen läuft gerade – gleich nochmal")
else:
    print("fehler\t" + (sauber(e.get("hinweis", "")) or grund or "ohne Ergebnis"))
PY
)"
briefkasten() {
  local ip oeff port rolle f p pub_abholen="<Schlüssel>" pub_pc="<Schlüssel>" kh="$BK/known_hosts"
  local kh_pc="$BK/known_hosts_pc" kh_name kh_pc_name hostkey="" zeilen h typ k rest n soll toml_bk block ausgabe rc
  local ergebnis bewertung
  if systemctl is-enabled -q "$BK_TIMER" 2>/dev/null; then
    echo "   Briefkasten: schon eingerichtet – das Abholen läuft (alle 2 min)"; BK_AN=1; return 0
  fi
  if ! frage "Briefkasten für $NAME einrichten? (Erst, wenn er auf dem vServer läuft – docs/BRIEFKASTEN.md)"; then
    echo "   Briefkasten: nicht eingerichtet – seine Aufnahmen kommen weiter nur von Hand. Später: bash $0 $NAME"
    return 0
  fi
  for p in ssh-keygen ssh-keyscan sftp; do
    command -v "$p" > /dev/null 2>&1 || { echo "   ❌ $p fehlt im CT (Paket openssh-client) – Briefkasten nicht eingerichtet."; return 0; }
  done

  # 1 Adressen des vServers: einmal fragen, für alle Freunde merken – gemerkt erst, wenn der Briefkasten dort antwortet
  #   (Teil 3 unten), so bleibt ein Tippfehler nicht hängen
  if [ -f "$BK_CONF" ]; then
    ip="$(conf_wert TAILNET_IP)"; oeff="$(conf_wert OEFFENTLICH)"; port="$(conf_wert PORT)"
    adressen_ok "$ip" "$oeff" "$port" \
      || { echo "   ❌ $BK_CONF ist unvollständig (TAILNET_IP, OEFFENTLICH, PORT) – bitte ansehen. Nichts geändert."; return 0; }
    echo "   vServer: Tailnet $ip, öffentlich $oeff, Port $port (aus $BK_CONF)"
  elif [ "$PROBE" = 1 ]; then
    echo "   (Probe: würde einmal Tailnet-Adresse, öffentlichen Namen und Port des vServers fragen und in $BK_CONF merken)"
    ip="<Tailnet-IP>"; oeff="<öffentlich>"; port=2222
  else
    echo "   Einmal für alle Freunde: die Adressen deines vServers (dort: tailscale ip -4 bzw. sein Name im Internet)."
    read -r -p "   Tailnet-Adresse des vServers (100.x.y.z): " ip || true
    read -r -p "   Öffentlicher Name oder öffentliche Adresse (dahin laden die PCs der Freunde): " oeff || true
    read -r -p "   Port des Briefkastens [2222]: " port || true
    port="${port:-2222}"
    if ! adressen_ok "$ip" "$oeff" "$port"; then
      echo "   ❌ Das passt nicht (Tailnet-Adresse 100.64.0.0/10, Name nur aus A-Z, 0-9, Punkt, Strich; Port 1–65535)."
      echo "   Nichts gemerkt, Briefkasten nicht eingerichtet. Nochmal: bash $0 $NAME"
      return 0
    fi
  fi

  # 2 Zwei Schlüsselpaare: abholen (der Mini, nur lesen) und pc (sein PC, nur hochladen). Sie bleiben bei jedem weiteren
  #   Lauf; der private Teil erscheint nie auf dem Bildschirm (das Skript liest ihn nicht einmal).
  ordner "$BK" "root:$U" 750
  for rolle in abholen pc; do
    f="$BK/$rolle"
    normal_oder_nichts "$f"
    normal_oder_nichts "$f.pub"
    if [ -f "$f" ]; then
      echo "   $f: schon da – bleibt"
      if [ ! -f "$f.pub" ]; then   # nur der öffentliche Teil fehlt (Abbruch mittendrin): aus dem privaten ableiten
        tu chmod 600 "$f"          # als root liest ssh-keygen einen root-eigenen Schlüssel nur mit 0600 (gleich wieder 0640)
        printf '   $ ssh-keygen -y -f %s > %s.pub\n' "$f" "$f"
        if [ "$PROBE" != 1 ]; then
          p="$(ssh-keygen -y -f "$f" 2> /dev/null)" || p=""
          [[ "$p" =~ $SCHLUESSEL_RE ]] || abbruch "aus $f ließ sich der öffentliche Schlüssel nicht ableiten – bitte melden."
          ( umask 077; printf '%s\n' "$p" > "$f.pub" )
        fi
      fi
    else
      tu ssh-keygen -q -t ed25519 -N '' -C "clip-$NAME-$rolle" -f "$f"
    fi
    setze "$f" "root:$U" 640
    setze "$f.pub" "root:$U" 640
  done
  if [ -f "$BK/abholen.pub" ] && [ -f "$BK/pc.pub" ]; then
    pub_abholen="$(head -n 1 "$BK/abholen.pub")"
    pub_pc="$(head -n 1 "$BK/pc.pub")"
    if ! [[ "$pub_abholen" =~ $SCHLUESSEL_RE ]] || ! [[ "$pub_pc" =~ $SCHLUESSEL_RE ]] \
       || [ "$(cut -d' ' -f2 <<< "$pub_abholen")" = "$(cut -d' ' -f2 <<< "$pub_pc")" ]; then
      echo "   ❌ Die Schlüssel in $BK passen nicht (zwei verschiedene Ed25519-Schlüssel nötig) – bitte melden."; return 0
    fi
  fi

  # 3 Hostschlüssel über das Tailnet holen (dort antwortet nur der echte vServer – WireGuard) und pinnen. Für die PCs
  #   derselbe Schlüssel unter dem öffentlichen Namen – nie übers Internet gefragt. Gepinnt wird einmal, nie still ersetzt.
  if [ "$port" = 22 ]; then kh_name="$ip"; kh_pc_name="$oeff"; else kh_name="[$ip]:$port"; kh_pc_name="[$oeff]:$port"; fi
  normal_oder_nichts "$kh"
  normal_oder_nichts "$kh_pc"
  if [ -f "$kh" ]; then
    read -r h typ k rest < "$kh" || true
    if [ "$h" = "$kh_name" ] && [ "$typ" = ssh-ed25519 ] && [[ "$k" =~ $HOSTKEY_RE ]] && [ -z "$rest" ] \
       && [ "$(grep -c . "$kh")" = 1 ]; then
      hostkey="$k"
      echo "   $kh: schon da – bleibt (Hostschlüssel gepinnt)"
    else
      echo "   ❌ $kh passt nicht zu $kh_name (anderer vServer oder Port?). Ich ändere nichts – bitte melden."; return 0
    fi
  else
    printf '   $ ssh-keyscan -T 10 -t ed25519 -p %s %s\n' "$port" "$ip"
    if [ "$PROBE" = 1 ]; then echo "   (Probe: würde den Hostschlüssel nach $kh schreiben)"
    else
      zeilen="$(ssh-keyscan -T 10 -t ed25519 -p "$port" "$ip" 2> /dev/null || true)"
      n=0
      while read -r h typ k rest; do
        if [ "$h" = "$kh_name" ] && [ "$typ" = ssh-ed25519 ] && [[ "$k" =~ $HOSTKEY_RE ]] && [ -z "$rest" ]; then
          hostkey="$k"; n=$((n + 1))
        fi
      done <<< "$zeilen"
      if [ "$n" != 1 ]; then
        echo "   ❌ Der Briefkasten antwortet über das Tailnet nicht ($ip, Port $port). Läuft er auf dem vServer (dort:"
        echo "      bash /root/briefkasten/briefkasten-pruefen.sh), und darf der CT dorthin (Tailnet-Regel, Port $port)?"
        if [ -f "$BK_CONF" ]; then echo "      Die Adressen stehen in $BK_CONF (für alle Freunde) – ein Tippfehler? Dort ändern."
        else echo "      Nichts gemerkt – beim nächsten Lauf fragt es die Adressen neu."; fi
        echo "   Das Abholen bleibt aus. Danach nochmal: bash $0 $NAME"
        return 0
      fi
      ( umask 077; printf '%s ssh-ed25519 %s\n' "$kh_name" "$hostkey" > "$kh" )
      echo "   $kh geschrieben"
    fi
  fi
  setze "$kh" "root:$U" 640
  if [ "$PROBE" != 1 ] && [ ! -f "$BK_CONF" ]; then   # der Briefkasten antwortet: Adressen für alle Freunde merken
    ( umask 022
      { printf '# Briefkasten der Freunde auf dem vServer (docs/BRIEFKASTEN.md) – geschrieben von benutzer-anlegen.sh am %s.\n' \
          "$(date +%d.%m.%Y)"
        printf 'TAILNET_IP=%s\nOEFFENTLICH=%s\nPORT=%s\n' "$ip" "$oeff" "$port"; } > "$BK_CONF" )
    echo "   $BK_CONF geschrieben (Adressen des vServers für alle Freunde)"
  fi
  if [ -n "$hostkey" ]; then
    soll="$kh_pc_name ssh-ed25519 $hostkey"
    if [ -f "$kh_pc" ] && [ "$(cat "$kh_pc")" = "$soll" ]; then echo "   $kh_pc: schon da"
    else ( umask 077; printf '%s\n' "$soll" > "$kh_pc" ); echo "   $kh_pc geschrieben (derselbe Schlüssel unter $kh_pc_name)"; fi
    echo "   Hostschlüssel des Briefkastens: $(ssh-keygen -l -f "$kh" 2> /dev/null | awk '{print $2}')"
    echo "   (derselbe wie in der Zeile Hostschlüssel am Ende von briefkasten-einrichten.sh auf dem vServer?)"
  else
    echo "   (Probe: würde $kh_pc mit demselben Hostschlüssel unter $kh_pc_name schreiben)"
  fi
  setze "$kh_pc" "root:$U" 640

  # 4 [briefkasten] in seine instanz.toml – nur anhängen (vorher geprüft), nie umschreiben. Danach erkennt sein Mini
  #   den Abend nicht mehr selbst, die Abend-Datei kommt vom PC (M93).
  if [ ! -f "$I/instanz.toml" ]; then
    echo "   (Probe: würde [briefkasten] mit host = \"$ip\", port = $port, oeffentlich = \"$oeff\" an $I/instanz.toml anhängen)"
  else
    toml_bk="$(python3 -I -c "$TOML_BK_PY" "$I/instanz.toml" 2> /dev/null)" || toml_bk="kaputt"
    if [ "$toml_bk" = fehlt ]; then
      block="$(printf '\n[briefkasten]\n# Stufe 2: sein Briefkasten auf dem vServer – eingetragen von benutzer-anlegen.sh am %s\nhost = "%s"\nport = %s\noeffentlich = "%s"' \
        "$(date +%d.%m.%Y)" "$ip" "$port" "$oeff")"
      if [ "$PROBE" = 1 ]; then
        echo "   (Probe: würde an $I/instanz.toml anhängen:)"
        printf '%s\n' "$block" | sed 's/^/   | /'
      elif python3 -I -c "$TOML_DAZU_PY" "$I/instanz.toml" "$block" 2> /dev/null; then
        ( umask 077
          if [ -n "$(tail -c 1 "$I/instanz.toml")" ]; then echo >> "$I/instanz.toml"; fi
          printf '%s\n' "$block" >> "$I/instanz.toml" )
        ENV_NEU=1   # sein Bot liest die Konfig nur beim Start
        echo "   [briefkasten] an $I/instanz.toml angehängt"
      else
        echo "   ❌ $I/instanz.toml bliebe mit [briefkasten] nicht lesbar – nichts geändert, bitte melden."; return 0
      fi
    elif [ "$toml_bk" = "$ip"$'\t'"$port"$'\t'"$oeff" ]; then
      echo "   $I/instanz.toml: [briefkasten] schon da – bleibt"
    else
      echo "   ❌ [briefkasten] in $I/instanz.toml weicht von $BK_CONF ab – ich ändere nichts, bitte melden."; return 0
    fi
  fi

  # 5 Die eine Zeile für den vServer (nur die öffentlichen Schlüssel)
  printf '\n   👉 Auf dem vServer als root ausführen – legt sein Fach und seinen Zugang an (fragt dort j/N):\n\n'
  printf "      bash /root/briefkasten/briefkasten-freund.sh %s --pc '%s' --abholen '%s'\n\n" "$NAME" "$pub_pc" "$pub_abholen"
  if [ "$PROBE" = 1 ]; then echo "   (Probe: würde warten, bis du hier Enter drückst)"
  else read -r -p "   Enter, wenn die Zeile auf dem vServer gelaufen ist (bei einem neuen Versuch reicht Enter) … " _ || true; fi

  # 6 Probe-Abholung: ein echter Lauf in seiner Sandbox (wie benutzer-befehl.sh). Erst wenn sie grün ist, geht in Schritt
  #   10 der Timer an.
  printf '   $ bash %s %s briefkasten abholen\n' "$HIER/benutzer-befehl.sh" "$NAME"
  if [ "$PROBE" = 1 ]; then echo "   (Probe: das Abholen geht erst nach einer grünen Probe-Abholung an)"; BK_AN=1; return 0; fi
  echo "   Probe-Abholung läuft … (holt auch, was schon wartet – dann dauert es länger)"
  if ausgabe="$(bash "$HIER/benutzer-befehl.sh" "$NAME" briefkasten abholen < /dev/null 2>&1)"; then rc=0; else rc=$?; fi
  printf '%s\n' "$ausgabe" | grep -v '^{' | tail -n 12 | sed 's/^/   | /' || true
  ergebnis="$(printf '%s\n' "$ausgabe" | grep '^{' | tail -n 1 || true)"
  bewertung="$(python3 -I -c "$PROBE_PY" "$rc" "$ergebnis" 2> /dev/null)" || bewertung=$'fehler\tErgebnis unlesbar'
  if [ "${bewertung%%$'\t'*}" = ok ]; then
    echo "   ✅ Probe-Abholung: ${bewertung#*$'\t'}"
    BK_AN=1
  else
    echo "   ❌ Probe-Abholung: ${bewertung#*$'\t'} (Exit $rc)"
    echo "      Häufig: Die Zeile lief auf dem vServer noch nicht (oder ohne j) · die Tailnet-Regel lässt den CT nicht auf"
    echo "      Port $port · auf dem vServer steht eine andere Mini-IP (briefkasten-einrichten.sh --mini-ip; hier im CT:"
    echo "      tailscale ip -4) · sein Fach ist nicht eingehängt (dort: bash /root/briefkasten/briefkasten-pruefen.sh $NAME)."
    echo "   Das Abholen bleibt aus. Danach nochmal: bash $0 $NAME – Schlüssel und Eintrag bleiben, nur Enter und die Probe."
  fi
}
briefkasten

sag "10/12 Einschalten – nur für $NAME"
AN=("clip-freund-scan@$NAME.timer" "clip-freund-abend@$NAME.timer")
if [ "$BK_AN" = 1 ]; then AN+=("$BK_TIMER"); fi
if verbunden; then AN+=("$BOT")
elif [ "$PROBE" = 1 ]; then echo "   (Probe: $BOT kommt dazu, sobald $NAME mit Telegram verbunden ist)"
else echo "   $BOT bleibt aus, bis $NAME mit Telegram verbunden ist (dann nochmal: bash $0 $NAME)"; fi
BOT_AN=0
for e in "${AN[@]}"; do
  if systemctl is-enabled -q "$e" 2>/dev/null; then echo "   $e: schon an"
  else tu systemctl enable --now "$e"; [ "$e" != "$BOT" ] || BOT_AN=1; fi
done
# Neue Zugänge oder Konfig bei eingeschaltetem Bot: neu starten – er liest .env und instanz.toml nur beim Start
# (startet auch einen, den Schritt 8 angehalten hat)
if [ "$ENV_NEU" = 1 ] && [ "$BOT_AN" = 0 ] && systemctl is-enabled -q "$BOT" 2>/dev/null; then tu systemctl restart "$BOT"; fi

sag "11/12 Lager auf pve-big (Stufe 2): seine Aufnahmen für immer, sein Puffer gibt alte frei"
# docs/MEHRBENUTZER.md „Lager für Freunde“. Sein Unterordner freunde/<name> liegt in deinem Lager (kein neues Dataset,
# kein neuer Export); clip-freund-lager@<name> kopiert dorthin, wenn dein täglicher Abgleich pve-big ohnehin geweckt hat
# (Rundgang clip-lager-freunde.service – weckt nie). Danach gibt sein Puffer Rohvideos frei, die älter als 14 Tage und im
# Lager bestätigt sind (der erste Lauf zählt nur). Eingerichtet wird nur bei wachem pve-big: Ordner und Marke im Lager,
# Einhängepunkt I/lager (root, leer), Schalter [instanz] lager = true, dann die Probe der Bindung in seiner Sandbox – ist
# sie rot, geht der Schalter wieder aus. Gelöscht wird nichts; die alte instanz.toml und ein Rückweg liegen in
# $LAGER_ABLAGE.
RUNDGANG="clip-lager-freunde.service"
LAGER_AN=0
# instanz.toml: stand → an|aus|anders|kaputt · schalten <Datum> → [instanz] lager = true eintragen (nur, wenn das Ergebnis
# bis auf diesen einen Wert genau die alte Konfig ist; Besitzer und Rechte bleiben) → geschaltet|schon|anders|kaputt
LAGER_PY="$(cat <<'PY'
import os, re, sys, tomllib
modus, pfad = sys.argv[1], sys.argv[2]
try:
    with open(pfad, encoding="utf-8") as datei:
        text = datei.read()
    alt = tomllib.loads(text)
except (OSError, ValueError):
    print("kaputt")
    sys.exit(0)
inst = alt.get("instanz")
wert = inst.get("lager") if isinstance(inst, dict) else None
if inst is not None and not isinstance(inst, dict):
    print("kaputt")
elif modus == "stand":
    print("an" if wert is True else "aus" if wert is None else "anders")
elif wert is True:
    print("schon")
elif wert is not None:
    print("anders")
else:
    zeile = f"lager = true   # Stufe 2: sein Lager auf pve-big – eingetragen von benutzer-anlegen.sh am {sys.argv[3]}"
    koepfe = list(re.finditer(r"(?m)^[ \t]*\[[ \t]*instanz[ \t]*\][ \t]*(#.*)?$", text))
    if inst is None:
        neu = text + ("\n" if text and not text.endswith("\n") else "") + "\n[instanz]\n" + zeile + "\n"
    elif len(koepfe) == 1:
        neu = text[:koepfe[0].end()] + "\n" + zeile + text[koepfe[0].end():]
    else:
        neu = None
    try:
        gut = neu is not None and tomllib.loads(neu) == {**alt, "instanz": {**(inst or {}), "lager": True}}
    except ValueError:
        gut = False
    if not gut:
        print("anders")
        sys.exit(0)
    st = os.stat(pfad)
    tmp = f"{pfad}.neu-{os.getpid()}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as datei:
        datei.write(neu)
    os.chown(tmp, st.st_uid, st.st_gid)
    os.chmod(tmp, st.st_mode & 0o7777)
    os.replace(tmp, pfad)
    print("geschaltet")
PY
)"
# Ergebnis der Probe in seiner Sandbox (Exit-Code und letzte JSON-Zeile von pipeline lager pruefen) -> ok|fehler<TAB>Text
LAGER_PROBE_PY="$(cat <<'PY'
import json, sys
rc, zeile = int(sys.argv[1]), sys.argv[2]
try:
    e = json.loads(zeile) if zeile else {}
except ValueError:
    e = {}
e = e if isinstance(e, dict) else {}
if rc == 0 and e.get("ok") is True:
    print("ok\tsein Unterordner im Lager ist in seiner Sandbox als " + "".join(z for z in str(e.get("lager", "")) if z.isprintable())[:200] + " eingebunden")
else:
    text = e.get("hinweis") or e.get("fehler") or "ohne Ergebnis"
    print("fehler\t" + "".join(z for z in str(text) if z.isprintable())[:300])
PY
)"
lager() {
  local stand nfs ausgabe rc ergebnis bewertung sicherung rueck schalten
  stand="$(python3 -I -c "$LAGER_PY" stand "$I/instanz.toml" 2> /dev/null)" || stand="kaputt"
  [ -f "$I/instanz.toml" ] || stand="aus"   # nur in der Probe (sonst steht sie seit Schritt 3)
  if [ "$stand" = an ]; then
    ordner "$I/lager" root:root 755
    echo "   Lager: schon eingerichtet – fährt bei deinem Lager-Abgleich mit"
    LAGER_AN=1
    if [ "$PROBE" != 1 ] && ! systemctl is-enabled -q "$RUNDGANG" 2> /dev/null; then
      echo "   ℹ️  Der Rundgang ist aus (von dir ausgeschaltet?) – einschalten: systemctl enable $RUNDGANG"
    fi
    return 0
  fi
  if [ "$stand" != aus ]; then
    echo "   ❌ [instanz] lager in $I/instanz.toml ist von Hand gesetzt (nicht true) oder die Datei ist unlesbar – ich ändere nichts, bitte ansehen."
    return 0
  fi
  # Nur bei wachem pve-big (ohne zu wecken nachgesehen): Lager per NFS eingehängt, Port 2049, deine Marke .clip-lager
  if nfs="$(bash "$HIER/lager-freunde.sh" --nfs 2>&1)"; then
    echo "   ${nfs##*$'\n'}"
  elif [ "$PROBE" = 1 ]; then
    echo "   (Probe: jetzt ${nfs##*$'\n'} – eingerichtet wird erst bei wachem pve-big)"
  else
    echo "   Lager: noch aus – jetzt nicht: ${nfs##*$'\n'}."
    echo "   Eingerichtet wird es bei wachem pve-big (nach deinem 10-Uhr-Abgleich). Dann nochmal: bash $0 $NAME"
    return 0
  fi
  if ! frage "Lager für $NAME einrichten? (Seine Aufnahmen bleiben in deinem Lager; alte Rohvideos verlassen nach 14 Tagen seinen Puffer)"; then
    echo "   Lager: nicht eingerichtet – seine Rohvideos bleiben im Puffer. Später: bash $0 $NAME"
    return 0
  fi

  # 1 freunde/<name> und seine Marke im Lager – nur auf deinem Lager-NFS, nie ein eigenes Dataset
  printf '   $ bash %s --ordner %s\n' "$HIER/lager-freunde.sh" "$NAME"
  if [ "$PROBE" != 1 ]; then
    if ! ausgabe="$(bash "$HIER/lager-freunde.sh" --ordner "$NAME" 2>&1)"; then
      echo "   ❌ ${ausgabe##*$'\n'} – Lager nicht eingerichtet."; return 0
    fi
    printf '%s\n' "$ausgabe" | sed 's/^/   | /'
  fi

  # 2 Einhängepunkt: leer, root – den bindet nur clip-freund-lager@ (seine anderen Dienste sehen ihn leer)
  ordner "$I/lager" root:root 755
  if [ "$PROBE" != 1 ] && [ -n "$(ls -A "$I/lager")" ]; then
    echo "   ❌ $I/lager ist nicht leer – ich ändere nichts, bitte ansehen."; return 0
  fi

  # 3 Schalter – vorher die alte instanz.toml sichern, Rückweg dazu
  if [ "$PROBE" = 1 ]; then
    echo "   (Probe: würde $I/instanz.toml nach $LAGER_ABLAGE sichern und unter [instanz] lager = true eintragen)"
  else
    mkdir -p "$LAGER_ABLAGE"
    chmod 700 "$LAGER_ABLAGE"
    sicherung="$LAGER_ABLAGE/instanz-$NAME-vor-lager-$ZEIT.toml"
    cp -p "$I/instanz.toml" "$sicherung"
    schalten="$(python3 -I -c "$LAGER_PY" schalten "$I/instanz.toml" "$(date +%d.%m.%Y)" 2> /dev/null)" || schalten="kaputt"
    if [ "$schalten" != geschaltet ] && [ "$schalten" != schon ]; then
      echo "   ❌ [instanz] in $I/instanz.toml ließ sich nicht sicher ergänzen ($schalten) – nichts geändert, bitte ansehen."
      return 0
    fi
    echo "   [instanz] lager = true in $I/instanz.toml (vorher gesichert: $sicherung)"
  fi

  # 4 Probe der Bindung: ein echter Lauf von pipeline lager pruefen in seiner Sandbox mit der Lager-Bindung und ohne Netz
  #   (wie clip-freund-lager@) – NFS, deine Marke unsichtbar, seine Marke da. Rot: Schalter wieder aus.
  printf '   $ bash %s --lager %s lager pruefen\n' "$HIER/benutzer-befehl.sh" "$NAME"
  if [ "$PROBE" = 1 ]; then
    echo "   (Probe: grün -> der Rundgang nimmt ihn mit; rot -> der Schalter geht wieder aus)"
  else
    if ausgabe="$(bash "$HIER/benutzer-befehl.sh" --lager "$NAME" lager pruefen < /dev/null 2>&1)"; then rc=0; else rc=$?; fi
    printf '%s\n' "$ausgabe" | grep -v '^{' | tail -n 8 | sed 's/^/   | /' || true
    ergebnis="$(printf '%s\n' "$ausgabe" | grep '^{' | tail -n 1 || true)"
    bewertung="$(python3 -I -c "$LAGER_PROBE_PY" "$rc" "$ergebnis" 2> /dev/null)" || bewertung=$'fehler\tErgebnis unlesbar'
    if [ "${bewertung%%$'\t'*}" != ok ]; then
      cat "$sicherung" > "$I/instanz.toml"
      echo "   ❌ Probe der Bindung: ${bewertung#*$'\t'} (Exit $rc)"
      echo "      Häufig: pve-big ging gerade aus · BindPaths mit NFS-Quelle oder PrivateNetwork geht in diesem CT nicht"
      echo "      (bitte melden) · im Lager liegt unter freunde/$NAME etwas anderes."
      echo "   Schalter wieder aus – das Lager bleibt aus. Danach nochmal: bash $0 $NAME"
      return 0
    fi
    echo "   ✅ Probe der Bindung: ${bewertung#*$'\t'}"
    rueck="$LAGER_ABLAGE/zurueck-lager-$NAME-$ZEIT.sh"
    { printf '#!/usr/bin/env bash\n# Rückweg zu benutzer-anlegen.sh (Schritt Lager, %s): Lager für %s wieder aus (Schalter wie vorher).\n' "$ZEIT" "$NAME"
      printf '# Seine Daten im Lager (freunde/%s auf pve-big) und der leere Einhängepunkt bleiben. Den Rundgang für alle\n' "$NAME"
      printf '# schaltet aus: systemctl disable %s\nset -euo pipefail\n' "$RUNDGANG"
      printf 'cat %q > %q\n' "$sicherung" "$I/instanz.toml"; } > "$rueck"
    chmod 700 "$rueck"
    echo "   Rückweg: bash $rueck"
  fi
  LAGER_AN=1

  # 5 Rundgang einmal einschalten (für alle Freunde gleich): fährt ab jetzt bei deinem Abgleich mit, weckt nie
  if [ ! -e "$UNITS/$RUNDGANG" ]; then
    tu install -m 644 "$HIER/$RUNDGANG" "$UNITS/$RUNDGANG"
    tu systemctl daemon-reload
  elif ! cmp -s "$HIER/$RUNDGANG" "$UNITS/$RUNDGANG"; then
    echo "   ℹ️  $RUNDGANG weicht vom Repo ab (von dir angepasst?) – bleibt. Vergleich: diff $UNITS/$RUNDGANG $HIER/$RUNDGANG"
  fi
  if systemctl is-enabled -q "$RUNDGANG" 2> /dev/null; then echo "   $RUNDGANG: schon an"
  else tu systemctl enable "$RUNDGANG"; fi

  # 6 Optional sofort einmal (im Hintergrund, nur 10–18 Uhr, nur weil pve-big gerade wach ist)
  if frage "Jetzt einmal ins Lager sichern? (Rundgang im Hintergrund – weckt nie, Starts nur 10–18 Uhr)"; then
    tu systemctl start --no-block "$RUNDGANG"
    echo "   mitlesen: journalctl -fu $RUNDGANG"
  fi
}
lager

sag "12/12 Prüfung: ist alles getrennt?"
if [ "$PROBE" = 1 ]; then echo "   \$ bash $HIER/benutzer-pruefen.sh $NAME"
elif ! bash "$HIER/benutzer-pruefen.sh" "$NAME" < /dev/null; then
  echo "❌ Die Prüfung hat etwas gefunden (siehe oben). Bis es behoben ist, ausschalten (Daten bleiben):"
  echo "   bash $HIER/benutzer-stilllegen.sh $NAME"
  exit 1
fi

sag "Zusatz: deine eigenen Dateien auch außerhalb der Sandbox schützen (nur chmod, mit Rückweg-Skript)"
# Der Ordner bleibt für andere passierbar (zur Sperrdatei), alles darin außer der Sperrdatei nur noch für dich; dazu deine
# .env und lokal.toml. Nur, was pipeline gehört: Deine Dienste laufen alle als pipeline, und an den Rechten des Besitzers
# ändert sich nichts – sie merken davon nichts. Was root gehört (z. B. eine lokal.toml, als root mit nano angelegt), lesen
# sie über die Rechte für alle – das bleibt, wie es ist (M84). /srv (Puffer, Lager) bleibt ebenso – dort sieht ein Freund
# in seiner Sandbox ohnehin nichts.
SCHAERFEN=()
schaerfe() {
  local wer alt neu
  [ -e "$1" ] && [ ! -L "$1" ] || return 0
  read -r wer alt <<< "$(stand "$1")"
  [[ "$alt" =~ ^[0-7]+$ ]] || return 0
  neu="$(printf '%o' $(( 8#$alt & ~8#$2 )))"
  [ "$neu" != "$alt" ] || return 0
  if [ "${wer%%:*}" = pipeline ]; then SCHAERFEN+=("$neu $alt $1")
  else echo "   ℹ️  $1 gehört ${wer%%:*}, nicht pipeline – bleibt $alt, sonst könnten deine Dienste sie nicht mehr lesen"; fi
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
if verbunden; then echo "Für $NAME: docs/FREUNDE.md – sein Bot schreibt ihm ab jetzt; in Fortnite Replays einschalten."
else echo "Für $NAME: docs/FREUNDE.md. Mit Telegram verbinden: nochmal bash $0 $NAME (neuer Einladungslink)."; fi
echo "Prüfen:            bash $HIER/benutzer-pruefen.sh $NAME"
echo "Log seines Bots:   journalctl -u $BOT -f"
if [ "$BK_AN" = 1 ]; then echo "Log des Abholens:  journalctl -u clip-freund-abholen@$NAME -n 50"
else echo "Briefkasten:       noch aus – einrichten mit nochmal bash $0 $NAME (Schritt 9, docs/BRIEFKASTEN.md)"; fi
if [ "$LAGER_AN" = 1 ]; then echo "Lager:             an – fährt bei deinem Abgleich mit (journalctl -u $RUNDGANG -n 50)"
else echo "Lager:             noch aus – bei wachem pve-big nochmal bash $0 $NAME (Schritt 11)"; fi
echo "Match nachholen:   bash $HIER/benutzer-befehl.sh $NAME process <ID>"
echo "Ausschalten:       bash $HIER/benutzer-stilllegen.sh $NAME   (Daten bleiben)"
