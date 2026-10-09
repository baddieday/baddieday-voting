#!/usr/bin/env bash
# Auf dem vServer als root:  bash briefkasten-einrichten.sh --mini-ip <IP> --probe   (zeigt nur)   ·   ohne --probe: echt
# Mehrbenutzer, Stufe 2 (docs/BRIEFKASTEN.md): der Briefkasten der Freunde – eine EIGENE sshd-Instanz nur für SFTP auf
# Port 2222 (--port), mit eigener Konfig, eigenem Hostschlüssel und eigenem Dienst briefkasten-sshd. Der normale
# SSH-Zugang (/etc/ssh, ssh.service) und n8n bleiben unberührt. Je Freund ein Fach: briefkasten-freund.sh.
#   1 Prüfen: root, systemd, kein Container, Loop-Geräte, sshd-Version, tailscale0, Port, Platz
#   2 Gruppe briefkasten · 3 Ordner, Hostschlüssel, briefkasten.conf · 4 sshd-Konfig und Dienst · 5 ufw (nur nach j)
#   6 Einschalten (nur nach j) · 7 Prüfung (briefkasten-pruefen.sh)
# Zwei Profile: Auf der öffentlichen Adresse gilt nur der Schlüssel des PCs (schreiben und umbenennen – nicht lesen,
# nicht löschen); auf der Tailnet-Adresse des vServers nur der Schlüssel des Mini, nur von seiner Adresse, nur lesen.
# Jede Änderung wird angezeigt und erst nach deinem "j" ausgeführt. Beliebig oft wiederholbar: Fertiges wird
# übersprungen, eine geänderte Fassung (neuer Port, neue Tailnet-Adresse) erst nach "j" – die alte wird vorher nach
# /root/briefkasten/sicherung/<zeit>/ gesichert. Gelöscht wird nichts. Rückweg: /root/briefkasten/zurueck.sh (schaltet
# nur den Dienst aus).
set -euo pipefail
BK_ETC="${BK_ETC:-/etc/briefkasten}"            # Konfig, Hostschlüssel, Schlüssel der Freunde (alles root)
BK_SRV="${BK_SRV:-/srv/briefkasten}"            # bilder/ (ein Bild je Fach) und fach/ (die chroots)
UNITS="${UNITS:-/etc/systemd/system}"
ABLAGE="${ABLAGE:-/root/briefkasten}"           # Rückweg-Skript und Sicherungen
SYSTEMD_LAUF="${SYSTEMD_LAUF:-/run/systemd/system}"
PRIVSEP="${PRIVSEP:-/run/sshd}"                 # teilt er sich mit dem normalen SSH – nur anlegen, nie entfernen
DIENST=briefkasten-sshd
HIER="$(cd "$(dirname "$0")" && pwd)"
ZEIT="$(date +%Y%m%d-%H%M%S)"
GB=1000000000                                    # 1 GB = 10^9 Byte, wie df -H
MIN_FACH_GB=8
# Hochladen: genau das, was der PC braucht (put/reput in .teil, rename, df). Nicht dabei: read, remove, mkdir, rmdir,
# setstat/fsetstat, symlink, hardlink, posix-rename (überschriebe fertige Dateien), copy-data.
ERLAUBT_HOCHLADEN="open,close,write,lstat,fstat,stat,opendir,readdir,realpath,rename,statvfs,fstatvfs,fsync,limits,expand-path"
# Tailnet-Adressen (100.64.0.0/10) – ohne führende Nullen
OKTETT='(0|[1-9][0-9]?|1[0-9]{2}|2[0-4][0-9]|25[0-5])'
TAILNET_RE="^100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.$OKTETT\.$OKTETT$"
PROBE=0
PORT=""
MINI_IP=""
aufruf() { echo "Aufruf: bash $0 [--mini-ip <Tailnet-IP des CT clips>] [--port 2222] [--probe]"; exit 2; }
while [ $# -gt 0 ]; do
  case "$1" in
    --probe) PROBE=1 ;;
    --port) [ $# -ge 2 ] || aufruf; PORT="$2"; shift ;;
    --port=*) PORT="${1#*=}" ;;
    --mini-ip) [ $# -ge 2 ] || aufruf; MINI_IP="$2"; shift ;;
    --mini-ip=*) MINI_IP="${1#*=}" ;;
    *) aufruf ;;
  esac
  shift
done

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
stand() { if [ -e "$1" ]; then stat -c '%U:%G %a' "$1"; fi; }
setze() {
  if [ "$(stand "$1")" = "$2 $3" ]; then return 0; fi
  tu chown "$2" "$1"
  tu chmod "$3" "$1"
}
ordner() {
  [ ! -L "$1" ] || abbruch "$1 ist ein Link – ich ändere nichts, bitte melden."
  if [ -e "$1" ] && [ ! -d "$1" ]; then abbruch "$1 ist kein Ordner – ich ändere nichts, bitte melden."; fi
  if [ -d "$1" ]; then echo "   $1: schon da"; else tu mkdir "$1"; fi
  setze "$1" root:root "$2"
}
# Datei mit festem Inhalt (root, Modus $2). Fehlt sie: schreiben. Weicht sie ab: Unterschied zeigen, erst nach j
# ersetzen, die alte Fassung vorher sichern. Mit Prüfbefehl ($4 …): die neue Fassung liegt erst als <ziel>.neu daneben
# und muss die Prüfung bestehen. NEU=1, wenn geschrieben wurde. Rückgabe 1: bleibt anders (du hast nein gesagt).
NEU=0
datei() {
  local ziel="$1" modus="$2" inhalt="$3" fehler
  shift 3
  NEU=0
  [ ! -L "$ziel" ] || abbruch "$ziel ist ein Link – ich ändere nichts, bitte melden."
  if [ -f "$ziel" ] && [ "$(cat "$ziel")" = "$inhalt" ]; then
    echo "   $ziel: schon da"
    setze "$ziel" root:root "$modus"
    return 0
  fi
  if [ "$PROBE" = 1 ]; then
    if [ -e "$ziel" ]; then
      echo "   $ziel weicht ab:"
      diff -u "$ziel" <(printf '%s\n' "$inhalt") | sed 's/^/   | /' || true
      printf '   ? %s  -> Probe: angenommen ja\n' "$ziel so ersetzen? (die alte Fassung wird gesichert)"
    else
      echo "   (Probe: würde $ziel schreiben$([ $# = 0 ] || echo ", vorher geprüft mit ${1##*/} ${*:2}"):)"
      printf '%s\n' "$inhalt" | sed 's/^/   | /'
    fi
    return 0
  fi
  ( umask 077; printf '%s\n' "$inhalt" > "$ziel.neu" )
  if [ $# -gt 0 ] && ! fehler="$("$@" "$ziel.neu" 2>&1)"; then
    printf '%s\n' "$fehler" | sed 's/^/   | /'
    abbruch "${1##*/} lehnt die neue Fassung ab (siehe oben) – nichts in Betrieb genommen. Zum Ansehen: $ziel.neu. Bitte melden."
  fi
  if [ -e "$ziel" ]; then
    echo "   $ziel weicht ab:"
    diff -u "$ziel" "$ziel.neu" | sed 's/^/   | /' || true
    if ! frage "$ziel so ersetzen? (die alte Fassung wird gesichert)"; then echo "   bleibt, wie es ist"; return 1; fi
    mkdir -p "$ABLAGE/sicherung/$ZEIT"
    cp -a "$ziel" "$ABLAGE/sicherung/$ZEIT/"
    echo "   alte Fassung gesichert: $ABLAGE/sicherung/$ZEIT/${ziel##*/}"
  fi
  mv -f "$ziel.neu" "$ziel"
  echo "   $ziel geschrieben"
  setze "$ziel" root:root "$modus"
  NEU=1
}
# Platz auf dem Dateisystem von $1 (oder dem nächsten vorhandenen Ordner darüber): GESAMT, FREI, RESERVE, PASST_GB
# (so viel dürfen alle Fächer noch zusammen bekommen – das System behält 15 % und mindestens 10 GB, damit n8n nie
# leidet)
platz() {
  local p="$1" werte
  while [ ! -e "$p" ]; do p="$(dirname "$p")"; done
  werte="$(df -B1 --output=size,used,avail "$p" | tail -n 1)"
  read -r GESAMT _ FREI <<< "$werte"
  [[ "${GESAMT:-}" =~ ^[0-9]+$ && "${FREI:-}" =~ ^[0-9]+$ ]] || abbruch "df $p lieferte Unerwartetes: '$werte'."
  RESERVE=$(( GESAMT * 15 / 100 ))
  [ "$RESERVE" -ge $(( 10 * GB )) ] || RESERVE=$(( 10 * GB ))
  PASST_GB=0
  [ "$FREI" -le "$RESERVE" ] || PASST_GB=$(( (FREI - RESERVE) / GB ))
}
conf_wert() { [ -f "$BK_ETC/briefkasten.conf" ] && awk -F= -v k="$1" '$1 == k {print $2; exit}' "$BK_ETC/briefkasten.conf"; }

[ "$(id -u)" = 0 ] || { echo "Bitte als root auf dem vServer ausführen."; exit 1; }
if [ "$PROBE" = 1 ]; then echo "PROBE: Ich zeige nur, was ich tun würde, und ändere nichts."; fi

sag "1/7 Prüfen: systemd, Virtualisierung, Loop-Geräte, sshd, Tailscale, Port, Platz"
[ -d "$SYSTEMD_LAUF" ] || abbruch "kein systemd – der Briefkasten braucht einen eigenen Dienst. Bitte melden."
for p in ip ss losetup systemd-detect-virt; do
  command -v "$p" >/dev/null || abbruch "$p fehlt (Pakete iproute2, mount, systemd) – bitte melden."
done
VIRT="$(systemd-detect-virt 2>/dev/null || true)"
if systemd-detect-virt --container -q 2>/dev/null; then
  abbruch "der vServer ist ein Container ($VIRT) – dort gibt es meist keine Loop-Geräte für feste Fächer. Ich ändere nichts. Bitte melden (Rückfall wäre ein eigenes Volume beim Hoster)."
fi
echo "Virtualisierung: ${VIRT:-keine erkannt} (kein Container)"
LOOP="$(losetup -f 2>/dev/null || true)"
[[ "$LOOP" == /dev/loop* ]] || abbruch "kein freies Loop-Gerät (losetup -f) – ohne kann ich keine Fächer fester Größe anlegen. Ich ändere nichts. Bitte melden."
echo "Loop-Geräte: ja ($LOOP ist frei)"
SSHD="$(command -v sshd || true)"
[ -n "$SSHD" ] && [ "${SSHD:0:1}" = / ] \
  || abbruch "sshd fehlt – erst installieren: apt install openssh-server (danach dieses Skript noch einmal)."
VERSION="$("$SSHD" -V 2>&1 | grep -oE 'OpenSSH_[0-9]+\.[0-9]+' | head -n 1 || true)"
[[ "$VERSION" =~ ^OpenSSH_([0-9]+)\.([0-9]+)$ ]] || abbruch "die Version von $SSHD ließ sich nicht lesen – bitte melden."
HAUPT="${BASH_REMATCH[1]}"
NEBEN="${BASH_REMATCH[2]}"
# Ab 8 – auch 8.0–8.5: Dort bietet der Briefkasten posix-rename trotz Erlaubnisliste an (und verweigert es); das
# PC-Programm benennt deshalb immer mit "rename -l" um, das jede Version kennt (M135, mit echtem 8.2p1 geprüft).
[ "$HAUPT" -ge 8 ] || abbruch "$VERSION ist zu alt (mindestens OpenSSH 8) – erst: apt update && apt upgrade"
STRAFEN=0
if [ "$HAUPT" -gt 9 ] || { [ "$HAUPT" = 9 ] && [ "$NEBEN" -ge 8 ]; }; then STRAFEN=1; fi
if [ "$STRAFEN" = 1 ]; then echo "sshd: $SSHD ($VERSION) – bremst Fehlversuche, das Tailnet ausgenommen"
else echo "sshd: $SSHD ($VERSION)"; fi
TAILNET_IP="$(ip -4 -o addr show dev tailscale0 2>/dev/null \
  | awk '{for (i = 1; i < NF; i++) if ($i == "inet") {split($(i + 1), a, "/"); print a[1]; exit}}' || true)"
if ! [[ "$TAILNET_IP" =~ $TAILNET_RE ]]; then
  abbruch "tailscale0 hat keine Tailnet-Adresse (100.64.0.0/10) – läuft Tailscale im Userspace-Modus oder gar nicht? Dann kann ich Abholen und Hochladen nicht trennen. Ich ändere nichts. Bitte melden (tailscale status)."
fi
echo "Tailnet-Adresse des vServers: $TAILNET_IP (tailscale0) – nur hier darf der Mini abholen"
MINI_ALT="$(conf_wert MINI_IP || true)"
[ -n "$MINI_IP" ] || MINI_IP="$MINI_ALT"
[ -n "$MINI_IP" ] || abbruch "die Tailnet-Adresse des CT clips fehlt: --mini-ip <IP> (im CT: tailscale ip -4)."
[[ "$MINI_IP" =~ $TAILNET_RE ]] || abbruch "--mini-ip $MINI_IP ist keine Tailnet-Adresse (100.64.0.0/10)."
[ "$MINI_IP" != "$TAILNET_IP" ] || abbruch "--mini-ip ist die Adresse des vServers selbst – gemeint ist der CT clips."
echo "Mini (CT clips): $MINI_IP – nur von dort gilt sein Schlüssel"
[ -n "$PORT" ] || PORT="$(conf_wert PORT || true)"
[ -n "$PORT" ] || PORT=2222
{ [[ "$PORT" =~ ^[1-9][0-9]{3,4}$ ]] && [ "$PORT" -le 65535 ]; } || abbruch "--port $PORT: bitte 1024–65535."
if [ "$(systemctl is-active "$DIENST" 2>/dev/null || true)" = active ]; then
  echo "Port $PORT: $DIENST läuft schon"
elif [ -n "$(ss -ltnH "sport = :$PORT" 2>/dev/null || true)" ]; then
  abbruch "auf Port $PORT lauscht schon etwas anderes (ss -ltnp 'sport = :$PORT') – anderen Port wählen, z. B. --port 2223."
else
  echo "Port $PORT: frei"
fi
platz "$BK_SRV"
echo "Platz: $(( FREI / GB )) von $(( GESAMT / GB )) GB frei; das System behält $(( RESERVE / GB )) GB (15 %, mindestens 10 GB)"
if [ "$PASST_GB" -lt "$MIN_FACH_GB" ]; then
  abbruch "für Fächer bleiben nur $PASST_GB GB – ein Fach braucht mindestens $MIN_FACH_GB GB. Ich ändere nichts. Erst Platz schaffen, bitte melden."
fi
echo "Für Fächer frei: $PASST_GB GB (je Freund mindestens $MIN_FACH_GB, Standard 20)"
if ! frage "Briefkasten jetzt einrichten bzw. vervollständigen (Gruppe briefkasten, $BK_ETC, $BK_SRV, Dienst $DIENST)?"; then
  echo "Abgebrochen – nichts verändert."; exit 1
fi

sag "2/7 Gruppe briefkasten (nur ihre Mitglieder kommen herein)"
if getent group briefkasten >/dev/null; then echo "   Gruppe briefkasten: schon da"
else tu groupadd --system briefkasten; fi

sag "3/7 Ordner, Hostschlüssel, briefkasten.conf"
# Rückweg zuerst – gilt auch, wenn danach etwas schiefgeht
rueckweg() {
  printf '#!/usr/bin/env bash\n'
  printf '# Rückweg zu briefkasten-einrichten.sh: schaltet den Briefkasten aus. Gelöscht wird NICHTS – Fächer, Schlüssel\n'
  printf '# und Konfig bleiben; wieder an: systemctl enable --now %s (oder briefkasten-einrichten.sh noch einmal).\n' "$DIENST"
  printf '# Auf dem vServer als root:  bash %s --probe   (zeigt nur)   ·   bash %s\n' "$ABLAGE/zurueck.sh" "$ABLAGE/zurueck.sh"
  printf 'set -euo pipefail\nDIENST=%q\n' "$DIENST"
  cat <<'RUECKWEG'
PROBE=0
case "${1:-}" in
  --probe) PROBE=1 ;;
  "") ;;
  *) echo "Aufruf: bash $0 [--probe]"; exit 2 ;;
esac
[ "$(id -u)" = 0 ] || { echo "Bitte als root auf dem vServer ausführen."; exit 1; }
if [ "$PROBE" = 1 ]; then echo "PROBE: Ich zeige nur, was ich tun würde, und ändere nichts."; fi
if ! systemctl is-enabled -q "$DIENST" 2>/dev/null && [ "$(systemctl is-active "$DIENST" 2>/dev/null || true)" != active ]; then
  echo "$DIENST ist schon aus – nichts zu tun."; exit 0
fi
echo "   \$ systemctl disable --now $DIENST"
if [ "$PROBE" = 1 ]; then echo "Probe fertig – nichts verändert. Echt:  bash $0"; exit 0; fi
antwort=""
read -r -p "   ? Briefkasten ausschalten (die Freunde laden nichts mehr hoch, der Mini holt nichts mehr ab)? [j/N] " antwort || true
case "$antwort" in j|J|ja|Ja|JA) ;; *) echo "Abgebrochen – nichts verändert."; exit 1 ;; esac
systemctl disable --now "$DIENST"
echo "Aus. Fächer, Schlüssel und Konfig bleiben – gelöscht ist nichts. Den Port kannst du in der Firewall des Hosters"
echo "(und in ufw, falls an) wieder schließen; ohne Dienst lauscht dort ohnehin nichts."
RUECKWEG
}
if [ -f "$ABLAGE/zurueck.sh" ]; then echo "   Rückweg-Skript: $ABLAGE/zurueck.sh (schon da)"
elif [ "$PROBE" = 1 ]; then echo "   (Probe: würde das Rückweg-Skript $ABLAGE/zurueck.sh schreiben)"
else
  mkdir -p "$ABLAGE" && rueckweg > "$ABLAGE/zurueck.sh" && chmod 700 "$ABLAGE/zurueck.sh"
  echo "   Rückweg-Skript geschrieben: $ABLAGE/zurueck.sh (schaltet nur aus, löscht nichts)"
fi
for o in "$BK_ETC" "$BK_ETC/hochladen" "$BK_ETC/abholen" "$BK_SRV" "$BK_SRV/fach"; do ordner "$o" 755; done
ordner "$BK_SRV/bilder" 700
HOSTKEY="$BK_ETC/hostkey_ed25519"
HOSTKEY_NEU=0
if [ -f "$HOSTKEY" ]; then echo "   Hostschlüssel: schon da"
else tu ssh-keygen -q -t ed25519 -N '' -C briefkasten -f "$HOSTKEY"; HOSTKEY_NEU=1; fi
CONF_TEXT="# Briefkasten der Freunde – von briefkasten-einrichten.sh. Ändern: das Skript mit --port bzw. --mini-ip noch einmal.
PORT=$PORT
TAILNET_IP=$TAILNET_IP
MINI_IP=$MINI_IP"
datei "$BK_ETC/briefkasten.conf" 644 "$CONF_TEXT" || abbruch "briefkasten.conf bleibt alt – sonst passte die Konfig nicht dazu."
if [ -n "$MINI_ALT" ] && [ "$MINI_ALT" != "$MINI_IP" ]; then
  echo "   ℹ️  Die Schlüssel des Mini gelten noch nur von $MINI_ALT – je Freund: briefkasten-freund.sh <name> --abholen '…'"
fi

sag "4/7 sshd-Konfig ($BK_ETC/sshd_config) und Dienst $DIENST"
KONFIG="# Briefkasten der Freunde (docs/BRIEFKASTEN.md): eigene sshd-Instanz, nur SFTP, nur Schlüssel, je Freund ein Fach.
# Geschrieben von briefkasten-einrichten.sh – nicht von Hand ändern, ein neuer Lauf schreibt sie neu (die alte Fassung
# wird vorher gesichert). Der normale SSH-Zugang (/etc/ssh/sshd_config) bleibt unberührt.
ListenAddress 0.0.0.0:$PORT
HostKey $HOSTKEY
PidFile /run/$DIENST.pid
AuthenticationMethods publickey
PubkeyAuthentication yes
PasswordAuthentication no
KbdInteractiveAuthentication no
HostbasedAuthentication no
UsePAM no
PermitRootLogin no
AllowGroups briefkasten
DisableForwarding yes
PermitTTY no
PermitTunnel no
PermitUserRC no
PermitUserEnvironment no
StrictModes yes
MaxAuthTries 3
LoginGraceTime 20
MaxStartups 10:30:60
ClientAliveInterval 30
ClientAliveCountMax 4
LogLevel VERBOSE
Subsystem sftp internal-sftp
ChrootDirectory $BK_SRV/fach/%u
# Hochladen (öffentliche Adresse, nur der Schlüssel des PCs): schreiben und umbenennen – nicht lesen, nicht löschen,
# kein mkdir, keine Links, kein Überschreiben per posix-rename
AuthorizedKeysFile $BK_ETC/hochladen/%u
ForceCommand internal-sftp -d /fach -u 0077 -p $ERLAUBT_HOCHLADEN"
if [ "$STRAFEN" = 1 ]; then
  KONFIG="$KONFIG
# Wer sich oft falsch anmeldet, wird gebremst – das Tailnet nicht, sonst sperrte ein Fehlversuch des Mini alle aus
PerSourcePenaltyExemptList 100.64.0.0/10"
fi
KONFIG="$KONFIG
# Abholen (nur über die Tailnet-Adresse, nur der Schlüssel des Mini, nur von seiner Adresse): nur lesen
Match LocalAddress $TAILNET_IP
	AuthorizedKeysFile $BK_ETC/abholen/%u
	ForceCommand internal-sftp -d /fach -R"
# sshd -t braucht den Ordner der Rechte-Trennung – fehlt er (normaler SSH aus), lege ich ihn an wie die Unit
if [ ! -d "$PRIVSEP" ]; then tu mkdir -p -m 0755 "$PRIVSEP"; fi
datei "$BK_ETC/sshd_config" 644 "$KONFIG" "$SSHD" -t -f || abbruch "die alte sshd-Konfig bleibt – nichts weiter geändert."
KONFIG_NEU="$NEU"
UNIT="[Unit]
Description=Briefkasten der Freunde: SFTP auf Port $PORT (eigener sshd, docs/BRIEFKASTEN.md)
After=network.target

[Service]
# /run/sshd teilt er sich mit dem normalen SSH: nur anlegen, nie entfernen (deshalb kein RuntimeDirectory)
ExecStartPre=/bin/mkdir -p -m 0755 /run/sshd
ExecStartPre=$SSHD -t -f $BK_ETC/sshd_config
ExecStart=$SSHD -D -e -f $BK_ETC/sshd_config
ExecReload=$SSHD -t -f $BK_ETC/sshd_config
ExecReload=/bin/kill -HUP \$MAINPID
KillMode=process
Restart=on-failure
RestartSec=10s

[Install]
WantedBy=multi-user.target"
datei "$UNITS/$DIENST.service" 644 "$UNIT" || abbruch "die alte Unit bleibt – nichts weiter geändert."
UNIT_NEU="$NEU"
if [ "$UNIT_NEU" = 1 ] || { [ "$PROBE" = 1 ] && [ ! -f "$UNITS/$DIENST.service" ]; }; then tu systemctl daemon-reload; fi

sag "5/7 Firewall: Port $PORT/tcp"
if command -v ufw >/dev/null; then
  UFW="$(ufw status 2>/dev/null || true)"
  case "$UFW" in
    "Status: active"*)
      if grep -Eq "^$PORT/tcp[[:space:]]+ALLOW" <<< "$UFW"; then echo "   ufw: Port $PORT/tcp ist schon offen"
      elif frage "ufw: Port $PORT/tcp öffnen (sonst kommt niemand an den Briefkasten)?"; then
        tu ufw allow "$PORT/tcp" comment "Briefkasten der Freunde"
      else echo "   ufw: bleibt zu"; fi ;;
    *) echo "   ufw ist aus – es zählt nur die Firewall des Hosters" ;;
  esac
else
  echo "   ufw gibt es hier nicht – es zählt nur die Firewall des Hosters"
fi
echo "   ℹ️  In der Firewall des Hosters (Kundenmenü) TCP $PORT öffnen – das kann ich nicht für dich tun."

sag "6/7 Einschalten"
if [ "$(systemctl is-active "$DIENST" 2>/dev/null || true)" = active ]; then
  # laufende Uploads bleiben in beiden Fällen (KillMode=process), neue Verbindungen nehmen die neue Fassung
  if [ "$UNIT_NEU" = 1 ]; then tu systemctl restart "$DIENST"
  elif [ "$KONFIG_NEU" = 1 ] || [ "$HOSTKEY_NEU" = 1 ]; then tu systemctl reload "$DIENST"
  else echo "   $DIENST läuft schon"; fi
  if ! systemctl is-enabled -q "$DIENST" 2>/dev/null; then tu systemctl enable "$DIENST"; fi
elif frage "Briefkasten jetzt einschalten (Port $PORT wird öffentlich erreichbar – nur mit Schlüssel, nur SFTP)?"; then
  tu systemctl enable --now "$DIENST"
else
  echo "   bleibt aus. Später:  systemctl enable --now $DIENST"
fi

sag "7/7 Prüfung"
if [ "$PROBE" = 1 ]; then echo "   \$ bash $HIER/briefkasten-pruefen.sh"
elif [ -f "$HIER/briefkasten-pruefen.sh" ]; then
  bash "$HIER/briefkasten-pruefen.sh" < /dev/null || echo "❌ Die Prüfung hat etwas gefunden (siehe oben)."
else
  echo "   briefkasten-pruefen.sh liegt nicht neben diesem Skript – bitte mit herunterladen."
fi

if [ "$PROBE" = 1 ]; then
  ECHT="bash $0 --mini-ip $MINI_IP"
  [ "$PORT" = 2222 ] || ECHT="$ECHT --port $PORT"
  sag "Probe fertig – nichts verändert. Echt:  $ECHT"
  exit 0
fi
sag "Fertig: Briefkasten auf Port $PORT (Abholen nur über $TAILNET_IP und nur vom Mini $MINI_IP)."
echo "Hostschlüssel: $(ssh-keygen -l -f "$HOSTKEY.pub" 2>/dev/null || echo '?')"
echo "Je Freund:   bash $HIER/briefkasten-freund.sh <name> --pc '<Schlüssel>' --abholen '<Schlüssel>'"
echo "             (die fertige Zeile zeigt dir benutzer-anlegen.sh im CT clips)"
echo "Prüfen:      bash $HIER/briefkasten-pruefen.sh"
echo "Protokoll:   journalctl -u $DIENST -f"
echo "Ausschalten: bash $ABLAGE/zurueck.sh   (schaltet nur aus, löscht nichts)"
