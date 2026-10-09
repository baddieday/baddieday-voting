#!/usr/bin/env bash
# Auf dem vServer als root:  bash briefkasten-pruefen.sh [<name>]   – liest nur, ändert nichts
# Mehrbenutzer, Stufe 2 (docs/BRIEFKASTEN.md): Zustand des Briefkastens der Freunde – Dienst, Port, Konfig (sshd -t),
# die beiden Profile (Hochladen ohne Lesen und Löschen, Abholen nur lesen und nur über das Tailnet) und je Fach:
# eingehängt, Marke, belegt, Dateien, gesperrt, Schlüssel. Ein Schlüssel bei zwei Freunden ist ein Befund.
# Exit 0 = alles in Ordnung, 1 = etwas gefunden (❌), 2 = falscher Aufruf.
set -euo pipefail
BK_ETC="${BK_ETC:-/etc/briefkasten}"
BK_SRV="${BK_SRV:-/srv/briefkasten}"
PRIVSEP="${PRIVSEP:-/run/sshd}"
DIENST=briefkasten-sshd
GB=1000000000
MARKE=.clip-briefkasten
# Was das Hochladen nie darf: lesen, löschen, Ordner, Rechte, Links, Überschreiben per posix-rename, kopieren
VERBOTEN=" read remove rmdir mkdir setstat fsetstat lsetstat symlink hardlink posix-rename copy-data "
NAME=""
case "${1:-}" in
  "") ;;
  -*) echo "Aufruf: bash $0 [<name>]"; exit 2 ;;
  *) NAME="$1"; [[ "$NAME" =~ ^[a-z][a-z0-9-]{1,26}$ ]] || { echo "Aufruf: bash $0 [<name>]"; exit 2; } ;;
esac
[ $# -le 1 ] || { echo "Aufruf: bash $0 [<name>]"; exit 2; }

BEFUNDE=0
ok() { echo "✅ $*"; }
befund() { echo "❌ $*"; BEFUNDE=$(( BEFUNDE + 1 )); }
hinweis() { echo "ℹ️  $*"; }
sag() { printf '\n== %s\n' "$*"; }
conf_wert() { awk -F= -v k="$1" '$1 == k {print $2; exit}' "$BK_ETC/briefkasten.conf" 2>/dev/null || true; }
# Zeilen der sshd-Konfig: "<Block>\t<Schlüssel>\t<Wert>" – Block "global" oder die Match-Zeile
konfig() {
  awk '/^[[:space:]]*(#|$)/ {next}
       {k = tolower($1); v = $0; sub(/^[[:space:]]*[^[:space:]]+[[:space:]]+/, "", v)}
       k == "match" {block = v; next}
       {print (block == "" ? "global" : "Match " block) "\t" k "\t" v}' "$BK_ETC/sshd_config"
}
wert() { konfig | awk -F'\t' -v b="$1" -v k="$2" '$1 == b && $2 == k {print $3; exit}'; }

[ "$(id -u)" = 0 ] || { echo "Bitte als root ausführen (Fächer und Schlüssel gehören root)."; exit 1; }
[ -f "$BK_ETC/sshd_config" ] || { echo "❌ Kein Briefkasten eingerichtet ($BK_ETC/sshd_config fehlt) – erst: briefkasten-einrichten.sh"; exit 1; }
PORT="$(conf_wert PORT)"
TAILNET_IP="$(conf_wert TAILNET_IP)"
MINI_IP="$(conf_wert MINI_IP)"

sag "Dienst $DIENST"
ZUSTAND="$(systemctl is-active "$DIENST" 2>/dev/null || true)"
if [ "$ZUSTAND" != active ]; then
  befund "$DIENST läuft nicht (${ZUSTAND:-unbekannt}) – Log: journalctl -u $DIENST -n 30 · an: systemctl enable --now $DIENST"
elif systemctl is-enabled -q "$DIENST" 2>/dev/null; then ok "$DIENST läuft und startet mit dem vServer"
else befund "$DIENST läuft, startet aber nicht mit dem vServer – systemctl enable $DIENST"; fi
if [ -z "$PORT" ]; then befund "$BK_ETC/briefkasten.conf fehlt oder nennt keinen Port – briefkasten-einrichten.sh noch einmal"
elif ! command -v ss >/dev/null; then hinweis "ss fehlt – ob Port $PORT offen ist, kann ich nicht sehen"
elif [ -n "$(ss -ltnH "sport = :$PORT" 2>/dev/null || true)" ]; then ok "Port $PORT: offen (in der Firewall des Hosters muss er auch offen sein)"
else befund "auf Port $PORT lauscht nichts"; fi
SSHD="$(command -v sshd || true)"
if [ -z "$SSHD" ]; then befund "sshd fehlt (apt install openssh-server)"
elif FEHLER="$("$SSHD" -t -f "$BK_ETC/sshd_config" 2>&1)"; then ok "Konfig in Ordnung (sshd -t)"
else befund "sshd -t lehnt die Konfig ab: $(head -n 1 <<< "$FEHLER")"; fi
if [ -d "$PRIVSEP" ]; then ok "$PRIVSEP da"
else befund "$PRIVSEP fehlt (normaler SSH aus?) – neue Verbindungen scheitern. Abhilfe: systemctl restart $DIENST"; fi
JETZT_IP="$(ip -4 -o addr show dev tailscale0 2>/dev/null \
  | awk '{for (i = 1; i < NF; i++) if ($i == "inet") {split($(i + 1), a, "/"); print a[1]; exit}}' || true)"
if [ -z "$JETZT_IP" ]; then befund "tailscale0 hat keine Adresse – der Mini kann nicht abholen (tailscale status)"
elif [ "$JETZT_IP" != "$TAILNET_IP" ]; then befund "die Tailnet-Adresse ist jetzt $JETZT_IP, eingerichtet ist $TAILNET_IP – briefkasten-einrichten.sh noch einmal"
else ok "Tailnet-Adresse $TAILNET_IP (tailscale0)"; fi

sag "Profile"
HOCH="$(wert global forcecommand)"
LISTE=""
[[ " $HOCH " =~ \ -p\ ([^ ]+)\  ]] && LISTE="${BASH_REMATCH[1]}"
SCHLECHT=""
for e in ${LISTE//,/ }; do case "$VERBOTEN" in *" $e "*) SCHLECHT="$SCHLECHT $e" ;; esac; done
if [[ "$HOCH" != internal-sftp* ]] || [ -z "$LISTE" ] || [[ " $HOCH " == *" -P "* ]]; then
  befund "Hochladen ohne feste Erlaubnisliste (ForceCommand: ${HOCH:-keins}) – briefkasten-einrichten.sh noch einmal"
elif [ -n "$SCHLECHT" ]; then befund "Hochladen darf zu viel:$SCHLECHT – briefkasten-einrichten.sh noch einmal"
else ok "Hochladen (öffentlich): schreiben und umbenennen – nicht lesen, nicht löschen, kein posix-rename"; fi
[ "$(wert global authorizedkeysfile)" = "$BK_ETC/hochladen/%u" ] || befund "Hochladen nimmt Schlüssel nicht nur aus $BK_ETC/hochladen/"
BLOECKE="$(konfig | cut -f1 | sort -u | grep -v '^global$' || true)"
if [ "$BLOECKE" != "Match LocalAddress $TAILNET_IP" ]; then
  befund "Abholen: erwartet genau einen Block (Match LocalAddress $TAILNET_IP), gefunden: ${BLOECKE:-keinen}"
else
  AB="$(wert "Match LocalAddress $TAILNET_IP" forcecommand)"
  if [[ " $AB " == *" -R "* ]] && [[ " $AB " != *" -p "* ]] && [[ " $AB " != *" -P "* ]]; then
    ok "Abholen (nur über $TAILNET_IP, nur vom Mini $MINI_IP): nur lesen"
  else befund "Abholen darf mehr als lesen (ForceCommand: $AB)"; fi
  [ "$(wert "Match LocalAddress $TAILNET_IP" authorizedkeysfile)" = "$BK_ETC/abholen/%u" ] \
    || befund "Abholen nimmt Schlüssel nicht nur aus $BK_ETC/abholen/"
fi

# Schlüssel: jeder nur einmal (ein Paar je Freund, PC und Mini verschieden)
DOPPELT="$(for f in "$BK_ETC"/hochladen/* "$BK_ETC"/abholen/*; do
  [ -f "$f" ] || continue
  awk -v f="${f#"$BK_ETC"/}" '{for (i = 1; i < NF; i++) if ($i ~ /^ssh-/) {print $(i + 1), f; break}}' "$f"
done | sort | awk '$1 == alt {print vorher " und " $2} {alt = $1; vorher = $2}')"
if [ -n "$DOPPELT" ]; then
  while read -r zeile; do befund "derselbe Schlüssel steht in $zeile – jeder Freund braucht ein eigenes Paar"; done <<< "$DOPPELT"
fi

if [ -n "$NAME" ]; then NAMEN=("$NAME")
else
  NAMEN=()
  for d in "$BK_SRV"/fach/bk-*; do [ -d "$d" ] && NAMEN+=("${d##*/bk-}"); done
fi
if [ "${#NAMEN[@]}" = 0 ]; then sag "Fächer"; hinweis "Noch kein Fach – je Freund: briefkasten-freund.sh <name> --pc '…' --abholen '…'"; fi
for n in "${NAMEN[@]}"; do
  U="bk-$n"
  MP="$BK_SRV/fach/$U/fach"
  BILD="$BK_SRV/bilder/$U.img"
  sag "Fach $n ($U)"
  if ! EINTRAG="$(getent passwd "$U")"; then befund "Benutzer $U fehlt – briefkasten-freund.sh $n …"
  elif [ "$(cut -d: -f7 <<< "$EINTRAG")" != /usr/sbin/nologin ]; then befund "$U darf sich anmelden – soll /usr/sbin/nologin sein"
  elif getent shadow "$U" | cut -d: -f2 | grep -q '^!'; then hinweis "gesperrt – wieder an: briefkasten-freund.sh $n --entsperren"
  else ok "Benutzer $U (ohne Anmeldung, nur SFTP)"; fi
  read -r FS_TYP FS_OPT <<< "$(findmnt -n -o FSTYPE,OPTIONS -M "$MP" 2>/dev/null || true)"
  if [ "${FS_TYP:-}" != ext4 ]; then
    befund "nicht eingehängt – Hochladen scheitert (Permission denied). Einhängen: mount -t ext4 -o loop,noexec,nosuid,nodev $BILD $MP"
  else
    FEHLT=""
    for o in noexec nosuid nodev; do [[ ",$FS_OPT," == *",$o,"* ]] || FEHLT="$FEHLT $o"; done
    if [ -n "$FEHLT" ]; then befund "eingehängt ohne$FEHLT"; else ok "eingehängt (ext4, noexec, nosuid, nodev)"; fi
    if [ -f "$MP/$MARKE" ] && [ "$(head -c 64 "$MP/$MARKE" | tr -d '[:space:]')" = "$U" ]; then ok "Marke $MARKE da"
    else befund "Marke $MARKE fehlt oder nennt ein anderes Fach – falsches Bild eingehängt?"; fi
    read -r GROESSE BELEGT _ <<< "$(df -B1 --output=size,used,avail "$MP" | tail -n 1)"
    if [[ "${GROESSE:-}" =~ ^[1-9][0-9]*$ && "${BELEGT:-}" =~ ^[0-9]+$ ]]; then
      PROZENT=$(( BELEGT * 100 / GROESSE ))
      ZEILE="belegt $PROZENT % ($(( BELEGT / GB )) von $(( GROESSE / GB )) GB)"
      if [ "$PROZENT" -ge 80 ]; then hinweis "fast voll: $ZEILE – größer: briefkasten-freund.sh $n --groesse <GB>"
      else hinweis "$ZEILE"; fi
    fi
    DATEIEN=0
    TEILE=0
    AELTESTE=""
    for o in videos replays sitzungen; do
      [ -d "$MP/$o" ] || { befund "Ordner $o fehlt – briefkasten-freund.sh $n …"; continue; }
      DATEIEN=$(( DATEIEN + $(find "$MP/$o" -type f ! -name '*.teil' | wc -l) ))
      TEILE=$(( TEILE + $(find "$MP/$o" -type f -name '*.teil' | wc -l) ))
      t="$(find "$MP/$o" -type f -printf '%T@\n' | sort -n | head -n 1)"
      if [ -n "$t" ] && { [ -z "$AELTESTE" ] || [ "${t%.*}" -lt "$AELTESTE" ]; }; then AELTESTE="${t%.*}"; fi
    done
    ZEILE="$DATEIEN fertig hochgeladen"
    [ "$TEILE" = 0 ] || ZEILE="$ZEILE, $TEILE halb (.teil)"
    [ -z "$AELTESTE" ] || ZEILE="$ZEILE – älteste vom $(date -d "@$AELTESTE" +%d.%m.%Y)"
    hinweis "$ZEILE"
  fi
  if [ -f "$BILD" ]; then
    read -r B_GROESSE B_BLOECKE B_EINHEIT <<< "$(stat -c '%s %b %B' "$BILD")"
    if [ $(( B_BLOECKE * B_EINHEIT )) -lt $(( B_GROESSE * 99 / 100 )) ]; then
      befund "das Bild ist nicht mehr fest reserviert (Löcher) – wieder belegen: fallocate -l $B_GROESSE $BILD"
    fi
  else
    befund "Bild $BILD fehlt"
  fi
  HF="$BK_ETC/hochladen/$U"
  AF="$BK_ETC/abholen/$U"
  if [ -f "$HF" ] && [ "$(stat -c '%U:%G %a' "$HF")" = "root:root 644" ] && grep -q '^restrict ssh-ed25519 ' "$HF"; then
    if [ -f "$AF" ] && [ "$(stat -c '%U:%G %a' "$AF")" = "root:root 644" ] \
       && grep -qF "from=\"$MINI_IP\",restrict ssh-ed25519 " "$AF"; then
      ok "Schlüssel: PC (nur hochladen) und Mini (nur abholen, nur von $MINI_IP)"
    else befund "Mini-Schlüssel fehlt, gehört nicht root (644) oder gilt nicht nur von $MINI_IP: $AF"; fi
  else
    befund "PC-Schlüssel fehlt oder gehört nicht root (644): $HF"
  fi
done

echo
if [ "$BEFUNDE" = 0 ]; then echo "Alles in Ordnung."; exit 0; fi
echo "$BEFUNDE Befund(e) – siehe ❌ oben."
exit 1
