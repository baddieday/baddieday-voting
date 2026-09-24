#!/usr/bin/env bash
# Auf pve-big als root: richtet clip-leerlauf SCHARF ein (pve-big fährt nach LEERLAUF_MIN ohne Zugriff herunter).
# Aufruf aus einem Ordner mit geprüften Kopien (Prüfsummen im Einfüge-Block, siehe docs/REGIE.md):
#   bash einrichten.sh /ZFS-Pool/clips
# Beliebig oft wiederholbar. Rückgängig:  systemctl disable --now clip-leerlauf.timer
# Nur kurz aussetzen:                    touch /run/clip-halten   (bis zum nächsten Neustart)
set -euo pipefail
S="${1:?Pfad des Clips-Speichers angeben, z. B. /ZFS-Pool/clips}"
HIER="$(cd "$(dirname "$0")" && pwd)"
SBIN="${SBIN:-/usr/local/sbin}"
UNITS="${UNITS:-/etc/systemd/system}"
KONF="${KONF:-/etc/clip-leerlauf.conf}"
PVE="${PVE:-/etc/pve}"
LEERLAUF_MIN="${LEERLAUF_MIN:-20}"

[ "$(id -u)" = 0 ] || { echo "Bitte als root ausführen."; exit 1; }
[ -e "$S/.clip-speicher" ] || { echo "$S/.clip-speicher fehlt – ist $S der Clips-Ordner?"; exit 1; }
command -v python3 >/dev/null || { echo "python3 fehlt (apt install python3), danach nochmal."; exit 1; }
python3 -c 'import sys; sys.exit(sys.version_info < (3, 8))' || { echo "python3 ist zu alt (mind. 3.8)."; exit 1; }

# Eigenes ZFS-Dataset für clips? Dann zählen seine Lese-/Schreibzähler. Sonst (nur ein Ordner in einem größeren
# Dataset) würden fremde Zugriffe mitzählen -> nur NFS/SMB/Herzschlag/offene Dateien.
DATASET="$(zfs list -H -o name,mountpoint 2>/dev/null | awk -v m="$S" '$2 == m {print $1; exit}' || true)"
if [ -n "$DATASET" ]; then ZFS_ZAEHLER=1
else ZFS_ZAEHLER=0; DATASET="$(zfs list -H -o name "$S" 2>/dev/null | head -n 1 || true)"; fi

# Gäste mit Autostart laufen immer, wenn pve-big läuft – sie dürfen ihn nicht wachhalten.
# Von Hand gestartete Gäste halten ihn dagegen wach (dann arbeitest du gerade damit).
AUTOSTART="$(grep -ls '^onboot: *1' "$PVE"/qemu-server/*.conf "$PVE"/lxc/*.conf 2>/dev/null \
  | xargs -r -n 1 basename | sed 's/\.conf$//' | sort -n | tr '\n' ' ' | sed 's/ $//' || true)"

install -m 755 "$HIER/clip-leerlauf" "$SBIN/clip-leerlauf"
install -m 644 "$HIER/clip-leerlauf.service" "$HIER/clip-leerlauf.timer" "$UNITS/"
[ -f "$KONF" ] && cp -p "$KONF" "$KONF.vorher"
cat > "$KONF" <<EOF
# /etc/clip-leerlauf.conf – angelegt von einrichten.sh am $(date '+%d.%m.%Y %H:%M'); Erklärung: $SBIN/clip-leerlauf
SPEICHER=$S
DATASET=$DATASET
ZFS_ZAEHLER=$ZFS_ZAEHLER
# Minuten ohne echten Zugriff bis zum Ausschalten (später 10, wenn der Herzschlag überall läuft)
LEERLAUF_MIN=$LEERLAUF_MIN
MINDEST_WACH_MIN=10
# Gäste mit Autostart (halten pve-big nicht wach)
GAESTE_ERLAUBT=$AUTOSTART
# 0 = schaltet wirklich aus. 1 = nur protokollieren (journalctl -t clip-leerlauf)
TROCKEN=0
EOF
chmod 644 "$KONF"

if [ -z "${KEIN_SYSTEMD:-}" ]; then
  systemctl daemon-reload
  systemctl enable -q --now clip-leerlauf.timer
  systemctl start clip-leerlauf.service   # einmal sofort: schreibt den Status, den der Mini sieht
fi

echo
echo "✅ clip-leerlauf ist scharf: pve-big geht nach $LEERLAUF_MIN min ohne Zugriff aus (frühestens 10 min nach dem Start)."
echo "   Speicher $S · Dataset ${DATASET:-?} · ZFS-Zähler $([ "$ZFS_ZAEHLER" = 1 ] && echo ja || echo 'nein (clips ist kein eigenes Dataset)')"
echo "   Gäste mit Autostart (zählen nicht): ${AUTOSTART:-keine}"
if [ -z "${KEIN_SYSTEMD:-}" ] && [ -r /run/clip-leerlauf/status.json ]; then
  echo "   Gerade hält ihn wach: $(python3 -c 'import json; g = json.load(open("/run/clip-leerlauf/status.json"))["gruende"]; print(" · ".join(g) or "nichts")')"
fi
echo
echo "👉 Jetzt dieses Shell-Fenster schließen (exit oder Tab zu)."
echo "   Eine Konsole, in der getippt wird, hält pve-big wach; nach $LEERLAUF_MIN min ohne Tippen zählt sie nicht mehr."
echo "   Später nachsehen: journalctl -t clip-leerlauf --since -2h   ·   Pause bis Neustart: touch /run/clip-halten"
