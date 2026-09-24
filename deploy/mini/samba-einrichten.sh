#!/usr/bin/env bash
# Im CT "clips" als root, erst nach deinem OK (installiert das Paket samba):
#   bash samba-einrichten.sh --probe   (zeigt nur)   ·   bash samba-einrichten.sh
# E19, Schritt R2 in docs/PUFFER.md: gibt den Puffer /srv/puffer für den Gaming-PC frei – \\<IP des CT>\clips,
# Benutzer gamingpc (Systembenutzer ohne Anmeldung). Alles, was der PC schreibt, gehört pipeline (force user).
# Die smb.conf kommt aus smb-puffer.conf (liegt daneben) und wird vor dem Neustart mit testparm geprüft.
# Beliebig oft wiederholbar. Alte smb.conf: /root/samba-original/ (erster Lauf, bleibt) und /root/samba-<zeit>/.
# Rückweg: systemctl disable --now smbd   (ganz weg: apt-get purge samba; userdel gamingpc)
set -euo pipefail
PUFFER="${PUFFER:-/srv/puffer}"
SMB_KONF="${SMB_KONF:-/etc/samba/smb.conf}"
BENUTZER="${BENUTZER:-gamingpc}"
ORIGINAL="${ORIGINAL:-/root/samba-original}"
SICH="${SICH:-/root/samba-$(date +%Y%m%d-%H%M%S)}"
HIER="$(cd "$(dirname "$0")" && pwd)"
VORLAGE="$HIER/smb-puffer.conf"
PROBE=0
case "${1:-}" in
  --probe) PROBE=1 ;;
  "") ;;
  *) echo "Aufruf: bash $0 [--probe]"; exit 2 ;;
esac

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

[ "$(id -u)" = 0 ] || { echo "Bitte als root im CT ausführen."; exit 1; }
[ ! -d /etc/pve ] || { echo "Das ist der Proxmox-Host – bitte im CT ausführen (pct enter 102)."; exit 1; }
[ -f "$VORLAGE" ] || { echo "$VORLAGE fehlt – bitte deploy/mini/ vollständig neben dieses Skript legen."; exit 1; }
id pipeline >/dev/null 2>&1 || { echo "Benutzer pipeline fehlt – ist das der CT clips?"; exit 1; }
# Der Puffer muss ein eigenes, eingehängtes Volume sein – sonst schriebe der PC auf die Systemplatte des CT
findmnt -rn "$PUFFER" >/dev/null 2>&1 || { echo "$PUFFER ist nicht eingehängt – erst R1 (puffer-einrichten.sh auf pve-mini)."; exit 1; }
[ -e "$PUFFER/.clip-puffer" ] || { echo "$PUFFER/.clip-puffer fehlt – erst R1 (puffer-einrichten.sh)."; exit 1; }
[ ! -e "$PUFFER/.clip-lager" ] || { echo "$PUFFER/.clip-lager gefunden – das wäre das LAGER. Ich ändere nichts, bitte melden."; exit 1; }
if [ "$PROBE" = 1 ]; then echo "PROBE: Ich zeige nur, was ich tun würde, und ändere nichts."; fi

sag "1/5 Paket samba (ohne empfohlene Zusatzpakete)"
if command -v smbd >/dev/null 2>&1; then
  echo "schon installiert"
else
  frage "Paket samba installieren (apt-get install --no-install-recommends samba)?" || abbruch "nichts verändert."
  tu apt-get update
  tu apt-get install -y --no-install-recommends samba
  # Debian startet smbd sofort mit der Vorgabe-Konfig – bis unsere steht, lieber aus
  tu systemctl stop smbd
fi

sag "2/5 Systembenutzer $BENUTZER (keine Anmeldung, kein Home – nur für Samba)"
if id "$BENUTZER" >/dev/null 2>&1; then
  echo "gibt es schon: $(id "$BENUTZER")"
else
  frage "Systembenutzer $BENUTZER anlegen?" || abbruch "ohne Benutzer keine Freigabe."
  tu useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin "$BENUTZER"
fi

sag "3/5 Samba-Passwort für $BENUTZER (wählst du selbst – nie ins Repo, nie in den Chat; auf dem PC per cmdkey, R7)"
if [ "$PROBE" = 1 ]; then
  echo "   \$ smbpasswd -a $BENUTZER   (Probe: nicht gefragt)"
elif pdbedit -L -u "$BENUTZER" >/dev/null 2>&1; then
  if frage "$BENUTZER hat schon ein Samba-Passwort. Neu setzen?"; then smbpasswd "$BENUTZER"; else echo "bleibt"; fi
else
  smbpasswd -a "$BENUTZER"
fi

sag "4/5 smb.conf aus $(basename "$VORLAGE") – erst mit testparm prüfen, dann übernehmen"
if [ -f "$SMB_KONF" ] && cmp -s "$VORLAGE" "$SMB_KONF"; then
  echo "schon aktuell"
else
  frage "$SMB_KONF durch die Vorlage ersetzen (die alte wird gesichert)?" || abbruch "smb.conf unverändert."
  if [ "$PROBE" = 0 ] && [ -f "$SMB_KONF" ]; then
    mkdir -p "$SICH" && cp -a "$SMB_KONF" "$SICH/" && echo "Sicherung: $SICH"
    if [ ! -d "$ORIGINAL" ]; then mkdir -p "$ORIGINAL" && cp -a "$SMB_KONF" "$ORIGINAL/"; fi
  fi
  tu install -m 644 "$VORLAGE" "$SMB_KONF.neu"
  printf '   $ testparm -s %s >/dev/null\n' "$SMB_KONF.neu"
  if [ "$PROBE" = 0 ] && ! testparm -s "$SMB_KONF.neu" >/dev/null; then
    echo "testparm meldet Fehler (siehe oben) – die bisherige smb.conf gilt weiter, $SMB_KONF.neu bleibt zum Ansehen."
    exit 1
  fi
  tu mv "$SMB_KONF.neu" "$SMB_KONF"
fi

sag "5/5 smbd starten (auch nach jedem Neustart), nmbd (NetBIOS) aus"
if command -v ip >/dev/null 2>&1 && ! ip link show eth0 >/dev/null 2>&1; then
  echo "ACHTUNG: Netzwerkkarte eth0 fehlt – in smb-puffer.conf 'interfaces' anpassen (ip -br link)."
fi
frage "smbd jetzt (neu) starten?" || abbruch "smbd nicht neu gestartet."
tu systemctl enable smbd
tu systemctl restart smbd
if [ "$PROBE" = 0 ]; then
  systemctl disable --now nmbd >/dev/null 2>&1 || true
  if ! systemctl is-active --quiet smbd; then echo "smbd läuft nicht – journalctl -u smbd -n 30"; exit 1; fi
fi

IP="$(ip -4 -o addr show eth0 2>/dev/null | awk '{sub(/\/.*/, "", $4); print $4; exit}' || true)"
if [ "$PROBE" = 1 ]; then sag "Probe fertig – nichts verändert. Echt:  bash $0"; else sag "Fertig."; fi
printf 'Vom Gaming-PC:  \\\\%s\\clips   Benutzer %s (Anmeldedaten per cmdkey, docs/PUFFER.md R7)\n' \
  "${IP:-<IP des CT>}" "$BENUTZER"
echo "Prüfen:  ss -ltn | grep ':445'   ·   Protokolle: /var/log/samba/   ·   Rückweg: systemctl disable --now smbd"
