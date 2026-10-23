#!/usr/bin/env bash
# Stimmen raus (23.10.): Demucs (KI-Quellentrennung, nur CPU) in eine eigene Umgebung neben der Datenbank legen und das
# Modell einmal laden. Läuft IM CT als root, wiederholbar, löscht nichts; die Pipeline-venv bleibt unangetastet
# (torch ≈ 1 GB). Danach entfernt der Renderer die Stimmen der Mitspieler aus dem Spielton (stimmen_raus.py).
#   pct exec 102 -- bash /opt/clip-pipeline/deploy/pve-mini/stimmen-einrichten.sh [--probe]
set -euo pipefail
CACHE="${CACHE:-/var/lib/clip-pipeline/cache}"
VENV="$CACHE/demucs-venv"
NUTZER="${NUTZER:-pipeline}"
PROBE=0; [ "${1:-}" = "--probe" ] && PROBE=1

if [ -x "$VENV/bin/demucs" ]; then
  echo "✅ Demucs schon da: $VENV/bin/demucs"
else
  echo "== Demucs einrichten unter $VENV (≈ 1,1 GB, nur CPU)"
  [ "$PROBE" = 1 ] && { echo "(Probe – nichts installiert)"; exit 0; }
  frei_mb=$(df -Pm "$CACHE" 2>/dev/null | awk 'NR==2{print $4}' || df -Pm "$(dirname "$CACHE")" | awk 'NR==2{print $4}')
  [ "${frei_mb:-0}" -ge 3000 ] || { echo "❌ Nur ${frei_mb:-?} MB frei unter $CACHE – mindestens 3 GB nötig"; exit 2; }
  install -d -o "$NUTZER" -g "$NUTZER" "$CACHE"
  runuser -u "$NUTZER" -- bash -euo pipefail -c "
    cd '$CACHE'
    [ -d demucs-venv ] || python3 -m venv demucs-venv
    ./demucs-venv/bin/pip install -q --upgrade pip
    ./demucs-venv/bin/pip install -q torch torchaudio --index-url https://download.pytorch.org/whl/cpu
    ./demucs-venv/bin/pip install -q demucs soundfile
  "
  echo "✅ installiert"
fi

# Modell einmal laden (≈ 80 MB nach $CACHE/torch) – das Home des Dienstes ist nur lesbar, deshalb TORCH_HOME
if [ "$PROBE" = 0 ]; then
  runuser -u "$NUTZER" -- bash -euo pipefail -c "
    mkdir -p '$CACHE/torch'
    t=\$(mktemp -d)
    ffmpeg -hide_banner -loglevel error -y -f lavfi -i anullsrc=r=44100:cl=stereo -t 2 \"\$t/still.wav\"
    TORCH_HOME='$CACHE/torch' OMP_NUM_THREADS=4 '$VENV/bin/demucs' -n htdemucs --two-stems=vocals -d cpu -j 1 \
      -o \"\$t/aus\" --filename '{stem}.{ext}' \"\$t/still.wav\" >/dev/null 2>&1 \
      && test -s \"\$t/aus/htdemucs/no_vocals.wav\" && echo '✅ Modell htdemucs geladen und geprüft' \
      || { echo '❌ Probe-Trennung fehlgeschlagen'; rm -rf \"\$t\"; exit 3; }
    rm -rf \"\$t\"
  "
fi
echo "Die Pipeline findet Demucs von selbst ([regie.ton] demucs leer = $VENV/bin/demucs)."
