#!/usr/bin/env bash
# Auf dem vServer als root (die fertige Zeile zeigt dir benutzer-anlegen.sh im CT clips):
#   bash briefkasten-freund.sh <name> --pc '<Schlüssel>' --abholen '<Schlüssel>' [--groesse GB] [--probe]
#   bash briefkasten-freund.sh <name> --groesse GB [--probe]        Fach vergrößern (nur wachsen)
#   bash briefkasten-freund.sh <name> --sperren | --entsperren       Freund aus- bzw. wieder einschalten (Daten bleiben)
# Mehrbenutzer, Stufe 2 (docs/BRIEFKASTEN.md): das Fach eines Freundes im Briefkasten – eigener Benutzer bk-<name>
# ohne Anmeldung (nur SFTP), eigenes Dateisystem fester Größe (Bild in /srv/briefkasten/bilder, Standard 20 GB,
# mindestens 8 GB, eingehängt mit noexec,nosuid,nodev) als chroot. Darin gehört die Wurzel root (Marke
# .clip-briefkasten), die Unterordner videos/ replays/ sitzungen/ status/ gehören ihm. Zwei Schlüssel, beide nur in
# root-eigenen Dateien: der des PCs (hochladen/, nur schreiben) und der des Mini (abholen/, nur lesen, nur von dessen
# Tailnet-Adresse). Läuft das Fach voll, trifft es nur ihn; das System behält immer 15 % und mindestens 10 GB frei.
# Jede Änderung wird angezeigt und erst nach deinem "j" ausgeführt. Wiederholbar: Fertiges wird übersprungen.
# Gelöscht wird nie etwas – kein Fach, kein Benutzer, keine Datei.
set -euo pipefail
BK_ETC="${BK_ETC:-/etc/briefkasten}"
BK_SRV="${BK_SRV:-/srv/briefkasten}"
ABLAGE="${ABLAGE:-/root/briefkasten}"
FSTAB="${FSTAB:-/etc/fstab}"
HIER="$(cd "$(dirname "$0")" && pwd)"
ZEIT="$(date +%Y%m%d-%H%M%S)"
GB=1000000000                                    # 1 GB = 10^9 Byte, wie df -H
MIN_FACH_GB=8
STANDARD_GB=20
UNTERORDNER="videos replays sitzungen status"
MARKE=.clip-briefkasten
OPTIONEN="loop,noexec,nosuid,nodev"
# In fstab zusätzlich: nofail (Start hängt nie am Fach), X-fstrim.notrim (das wöchentliche fstrim stanzte sonst Löcher
# ins Bild – dann wäre das Fach nicht mehr fest reserviert)
FSTAB_OPTIONEN="$OPTIONEN,nofail,X-fstrim.notrim"
# Öffentlicher Ed25519-Schlüssel, Kommentar nur aus harmlosen Zeichen – keine Optionen, kein Zeilenumbruch
SCHLUESSEL_RE='^ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI[A-Za-z0-9+/]{43}( [A-Za-z0-9@._:+-]{1,64})?$'
OKTETT='(0|[1-9][0-9]?|1[0-9]{2}|2[0-4][0-9]|25[0-5])'
TAILNET_RE="^100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.$OKTETT\.$OKTETT$"
NAME=""
PC=""
ABHOLEN=""
GROESSE=""
SPERRE=""
PROBE=0
aufruf() {
  echo "Aufruf: bash $0 <name> --pc '<Schlüssel>' --abholen '<Schlüssel>' [--groesse GB] [--probe]"
  echo "        bash $0 <name> --groesse GB [--probe]   ·   bash $0 <name> --sperren | --entsperren [--probe]"
  echo "        (Name: 2–27 Zeichen a-z, 0-9, -, vorn ein Buchstabe – wie im CT clips)"
  exit 2
}
while [ $# -gt 0 ]; do
  case "$1" in
    --probe) PROBE=1 ;;
    --pc) [ $# -ge 2 ] || aufruf; PC="$2"; shift ;;
    --abholen) [ $# -ge 2 ] || aufruf; ABHOLEN="$2"; shift ;;
    --groesse) [ $# -ge 2 ] || aufruf; GROESSE="$2"; shift ;;
    --groesse=*) GROESSE="${1#*=}" ;;
    --sperren) [ -z "$SPERRE" ] || aufruf; SPERRE=an ;;
    --entsperren) [ -z "$SPERRE" ] || aufruf; SPERRE=aus ;;
    -*) aufruf ;;
    *) [ -z "$NAME" ] || aufruf; NAME="$1" ;;
  esac
  shift
done
[[ "$NAME" =~ ^[a-z][a-z0-9-]{1,26}$ ]] || aufruf
[ -z "$GROESSE" ] || [[ "$GROESSE" =~ ^[1-9][0-9]{0,4}$ ]] || { echo "Größe in ganzen GB, z. B. --groesse 40"; exit 2; }
if [ -n "$SPERRE" ] && [ -n "$PC$ABHOLEN$GROESSE" ]; then aufruf; fi
U="bk-$NAME"
CHROOT="$BK_SRV/fach/$U"
MP="$CHROOT/fach"
BILD="$BK_SRV/bilder/$U.img"

sag() { printf '\n== %s\n' "$*"; }
tu() {
  local a z=""
  for a in "$@"; do case "$a" in ""|*[!A-Za-z0-9_./:=,@%+-]*) z="$z '$a'" ;; *) z="$z $a" ;; esac; done
  printf '   $%s\n' "$z"
  [ "$PROBE" = 1 ] || "$@"
}
frage() {
  if [ "$PROBE" = 1 ]; then printf '   ? %s  -> Probe: angenommen ja\n' "$1"; return 0; fi
  local antwort=""
  read -r -p "   ? $1 [j/N] " antwort || true
  case "$antwort" in j|J|ja|Ja|JA) return 0 ;; *) return 1 ;; esac
}
abbruch() { echo "Abgebrochen – $1"; exit 1; }
stand() { if [ -e "$1" ]; then stat -c '%U:%G %a' "$1"; fi; }
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
sichere() {
  mkdir -p "$ABLAGE/sicherung/$ZEIT"
  cp -a "$1" "$ABLAGE/sicherung/$ZEIT/$2"
  echo "   gesichert: $ABLAGE/sicherung/$ZEIT/$2"
}
conf_wert() { awk -F= -v k="$1" '$1 == k {print $2; exit}' "$BK_ETC/briefkasten.conf"; }
fingerabdruck() { printf '%s\n' "$1" | ssh-keygen -l -f - 2>/dev/null | awk '{print $2}'; }
blob() { awk '{for (i = 1; i < NF; i++) if ($i ~ /^ssh-/) {print $(i + 1); exit}}' <<< "$1"; }
# Wem gehört dieser Schlüssel schon? (Dateiname in hochladen/ bzw. abholen/ außer $2)
schon_bei() {
  local f
  for f in "$BK_ETC"/hochladen/* "$BK_ETC"/abholen/*; do
    [ -f "$f" ] && [ "$f" != "$2" ] || continue
    if grep -qF -- "$1" "$f"; then echo "${f#"$BK_ETC"/}"; return 0; fi
  done
  return 1
}
gesperrt() { getent shadow "$U" | cut -d: -f2 | grep -q '^!'; }
eingehaengt() { [ -n "$(findmnt -n -o FSTYPE -M "$MP" 2>/dev/null || true)" ]; }
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

[ "$(id -u)" = 0 ] || { echo "Bitte als root auf dem vServer ausführen."; exit 1; }
if [ "$PROBE" = 1 ]; then echo "PROBE: Ich zeige nur, was ich tun würde, und ändere nichts."; fi

sag "1/5 Prüfen: Briefkasten, Name, Schlüssel, Platz"
[ -f "$BK_ETC/briefkasten.conf" ] && [ -f "$BK_ETC/sshd_config" ] && [ -d "$BK_SRV/fach" ] && [ -d "$BK_SRV/bilder" ] \
  || abbruch "der Briefkasten ist noch nicht eingerichtet – erst: bash $HIER/briefkasten-einrichten.sh --mini-ip <IP>"
GID="$(getent group briefkasten | cut -d: -f3 || true)"
[ -n "$GID" ] || abbruch "Gruppe briefkasten fehlt – erst: bash $HIER/briefkasten-einrichten.sh"
MINI_IP="$(conf_wert MINI_IP)"
[[ "$MINI_IP" =~ $TAILNET_RE ]] \
  || abbruch "in $BK_ETC/briefkasten.conf fehlt MINI_IP oder ist keine Tailnet-Adresse – erst: briefkasten-einrichten.sh --mini-ip <IP>"
[[ "$BK_SRV" != *[[:space:]]* ]] || abbruch "$BK_SRV enthält Leerzeichen – das geht in fstab nicht."
NEUER_BENUTZER=0
if EINTRAG="$(getent passwd "$U")"; then
  IFS=: read -r _ _ _ U_GID _ _ U_SHELL <<< "$EINTRAG"
  [ "$U_GID" = "$GID" ] && [ "$U_SHELL" = /usr/sbin/nologin ] \
    || abbruch "Benutzer $U gibt es schon, aber nicht so, wie ich ihn anlege (Gruppe briefkasten, ohne Anmeldung) – ich ändere nichts."
  echo "Benutzer $U: schon da$(gesperrt && echo ' (gesperrt)')"
else
  NEUER_BENUTZER=1
fi

# Sperren / Entsperren: nur der Schalter am Benutzer – Fach und Schlüssel bleiben, wie sie sind
if [ -n "$SPERRE" ]; then
  [ "$NEUER_BENUTZER" = 0 ] || abbruch "$U gibt es nicht – nichts zu sperren."
  if [ "$SPERRE" = an ]; then
    if gesperrt; then echo "$U ist schon gesperrt – nichts zu tun."; exit 0; fi
    frage "$U sperren (keine neue Verbindung mehr, Fach und Schlüssel bleiben)?" || { echo "Abgebrochen – nichts verändert."; exit 1; }
    tu usermod -L "$U"
    echo "Gesperrt. Eine Verbindung, die gerade läuft, endet von selbst (sofort: pkill -u $U). Wieder an: --entsperren"
  else
    if ! gesperrt; then echo "$U ist nicht gesperrt – nichts zu tun."; exit 0; fi
    frage "$U wieder einschalten?" || { echo "Abgebrochen – nichts verändert."; exit 1; }
    tu usermod -U "$U"
    echo "Wieder an."
  fi
  exit 0
fi

# Schlüssel: Form, verschieden, bei keinem anderen Freund
HOCH="$BK_ETC/hochladen/$U"
AB="$BK_ETC/abholen/$U"
for paar in "PC:$PC" "Mini:$ABHOLEN"; do
  wert="${paar#*:}"
  [ -z "$wert" ] || [[ "$wert" =~ $SCHLUESSEL_RE ]] \
    || abbruch "der Schlüssel für ${paar%%:*} ist kein öffentlicher Ed25519-Schlüssel (ssh-ed25519 AAAA… [Name]) – nichts geändert."
  [ -z "$wert" ] || [ -n "$(fingerabdruck "$wert")" ] || abbruch "ssh-keygen erkennt den Schlüssel für ${paar%%:*} nicht – nichts geändert."
done
if [ "$NEUER_BENUTZER" = 1 ] || [ ! -f "$HOCH" ] || [ ! -f "$AB" ]; then
  [ -n "$PC" ] && [ -n "$ABHOLEN" ] || abbruch "für ein neues Fach brauche ich beide Schlüssel: --pc '…' --abholen '…' (die Zeile zeigt benutzer-anlegen.sh im CT clips)."
fi
if [ -n "$PC" ] && [ -n "$ABHOLEN" ] && [ "$(blob "$PC")" = "$(blob "$ABHOLEN")" ]; then
  abbruch "PC und Mini brauchen zwei verschiedene Schlüssel – nichts geändert."
fi
if [ -n "$PC" ] && WER="$(schon_bei "$(blob "$PC")" "$HOCH")"; then
  abbruch "diesen PC-Schlüssel gibt es schon ($WER) – jeder Freund bekommt ein eigenes Paar. Nichts geändert."
fi
if [ -n "$ABHOLEN" ] && WER="$(schon_bei "$(blob "$ABHOLEN")" "$AB")"; then
  abbruch "diesen Mini-Schlüssel gibt es schon ($WER) – jeder Freund bekommt ein eigenes Paar. Nichts geändert."
fi
[ -z "$PC" ] || echo "PC-Schlüssel:   $(fingerabdruck "$PC") – darf nur schreiben (öffentliche Adresse)"
[ -z "$ABHOLEN" ] || echo "Mini-Schlüssel: $(fingerabdruck "$ABHOLEN") – darf nur lesen, nur von $MINI_IP"

# Fach: neu (Platz für die ganze Größe) oder vorhanden (nur wachsen)
WACHSEN=0
[ ! -L "$BILD" ] || abbruch "$BILD ist ein Link – ich ändere nichts, bitte melden."
if [ -e "$BILD" ]; then
  [ -f "$BILD" ] || abbruch "$BILD ist keine Datei – ich ändere nichts, bitte melden."
  [ "$(blkid -p -s TYPE -o value "$BILD" 2>/dev/null || true)" = ext4 ] \
    || abbruch "$BILD hat kein ext4 – abgebrochener Lauf? Ansehen: ls -l $BILD (ich lege nie ein Dateisystem über ein vorhandenes Bild). Bitte melden."
  ALT=$(( $(stat -c %s "$BILD") ))
  echo "Fach: $BILD, $(( ALT / GB )) GB"
  if [ -n "$GROESSE" ] && [ $(( GROESSE * GB )) -gt "$ALT" ]; then
    platz "$BILD"
    if [ $(( GROESSE * GB - ALT )) -gt $(( PASST_GB * GB )) ]; then
      echo "Zu knapp: Das System behält 15 % und mindestens 10 GB frei (n8n). Ich ändere nichts."
      abbruch "So viel passt:  bash $0 $NAME --groesse $(( ALT / GB + PASST_GB ))"
    fi
    WACHSEN=1
    echo "Wachsen: $(( ALT / GB )) -> $GROESSE GB"
  elif [ -n "$GROESSE" ]; then
    echo "Nur wachsen: Das Fach hat schon $(( ALT / GB )) GB – bleibt so (verkleinern gibt es nicht)."
  fi
else
  GROESSE="${GROESSE:-$STANDARD_GB}"
  [ "$GROESSE" -ge "$MIN_FACH_GB" ] || abbruch "ein Fach braucht mindestens $MIN_FACH_GB GB (größter Abend plus Replays)."
  platz "$BK_SRV/bilder"
  echo "Platz: $(( FREI / GB )) GB frei; das System behält $(( RESERVE / GB )) GB (15 %, mindestens 10 GB) – für Fächer: $PASST_GB GB"
  if [ "$PASST_GB" -lt "$GROESSE" ]; then
    echo "Zu knapp für $GROESSE GB: Das System behält 15 % und mindestens 10 GB frei (n8n). Ich ändere nichts."
    if [ "$PASST_GB" -ge "$MIN_FACH_GB" ]; then abbruch "So viel passt:  bash $0 $NAME --groesse $PASST_GB --pc '…' --abholen '…'"
    else abbruch "auch für das kleinste Fach ($MIN_FACH_GB GB) ist kein Platz – erst Platz schaffen, bitte melden."; fi
  fi
  echo "Neues Fach: $GROESSE GB, fest reserviert"
fi
if ! frage "Fach von $NAME ($U) jetzt anlegen bzw. vervollständigen?"; then echo "Abgebrochen – nichts verändert."; exit 1; fi

sag "2/5 Benutzer $U (ohne Anmeldung, nur SFTP, Gruppe briefkasten)"
# Passwort „*“: keine Anmeldung per Passwort, aber auch nicht gesperrt („!“ sperrt bei UsePAM no ganz)
if [ "$NEUER_BENUTZER" = 1 ]; then
  tu useradd --system --gid briefkasten --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin \
    --password '*' "$U"
else
  echo "   schon da"
fi

sag "3/5 Fach: Bild, Einhängepunkt (chroot), fstab"
ordner "$CHROOT" root:root 755
ordner "$MP" root:root 755
if [ ! -e "$BILD" ]; then
  # Fest reserviert: nodiscard, sonst gäbe mkfs den Platz gleich wieder frei; je nach Dateisystem des vServers stanzt
  # mkfs beim Nullen trotzdem Löcher – das zweite fallocate füllt sie (der Inhalt bleibt, wie er ist)
  tu fallocate -l $(( GROESSE * GB )) "$BILD"
  tu mkfs.ext4 -q -m 0 -E nodiscard -L "$U" "$BILD"
  tu fallocate -l $(( GROESSE * GB )) "$BILD"
  setze "$BILD" root:root 600
fi
ZEILE="$BILD $MP ext4 $FSTAB_OPTIONEN 0 0"
VORHANDEN="$(awk -v mp="$MP" '$1 !~ /^#/ && $2 == mp' "$FSTAB")"
if [ "$VORHANDEN" = "$ZEILE" ]; then echo "   fstab: schon da"
elif [ -n "$VORHANDEN" ]; then
  abbruch "in $FSTAB steht schon eine andere Zeile für $MP – bitte ansehen: $VORHANDEN"
else
  echo "   \$ echo '$ZEILE' >> $FSTAB"
  if [ "$PROBE" = 0 ]; then
    sichere "$FSTAB" fstab
    if [ -s "$FSTAB" ] && [ -n "$(tail -c 1 "$FSTAB")" ]; then echo >> "$FSTAB"; fi   # letzte Zeile ohne Umbruch
    printf '# Briefkasten-Fach von %s (briefkasten-freund.sh)\n%s\n' "$NAME" "$ZEILE" >> "$FSTAB"
  fi
  tu systemctl daemon-reload
fi
WIEDER=0
wieder_einhaengen() {
  if [ "$WIEDER" = 1 ]; then
    echo "Das Fach ist noch ausgehängt. Wieder einhängen:  mount -t ext4 -o $OPTIONEN $BILD $MP"
  fi
}
trap wieder_einhaengen EXIT
if [ "$WACHSEN" = 1 ]; then
  frage "Fach von $NAME auf $GROESSE GB vergrößern? (kurz ausgehängt – ein laufender Upload bricht ab und setzt später fort)" \
    || abbruch "nicht vergrößert."
  if eingehaengt; then
    tu umount "$MP" || abbruch "das Fach ist gerade in Benutzung – später nochmal."
    [ "$PROBE" = 1 ] || WIEDER=1
  fi
  tu fallocate -l $(( GROESSE * GB )) "$BILD"
  if tu e2fsck -f -p "$BILD"; then RC=0; else RC=$?; fi
  if [ "$RC" -ge 4 ]; then
    WIEDER=0
    abbruch "e2fsck meldet Fehler im Fach von $NAME ($RC) – es bleibt ausgehängt, Hochladen wartet. Bitte melden."
  fi
  tu resize2fs "$BILD"
  tu fallocate -l $(( GROESSE * GB )) "$BILD"   # Löcher vom Vergrößern wieder füllen
fi

sag "4/5 Einhängen, Marke $MARKE, Unterordner ($UNTERORDNER)"
if eingehaengt; then echo "   $MP: schon eingehängt"
else tu mount -t ext4 -o "$OPTIONEN" "$BILD" "$MP"; WIEDER=0; fi
if [ "$PROBE" = 1 ] && ! eingehaengt; then
  echo "   (Probe: würde in $MP die Marke $MARKE (root) und ${UNTERORDNER// /\/, }/ für $U anlegen)"
else
  OPT="$(findmnt -n -o FSTYPE,OPTIONS -M "$MP" 2>/dev/null || true)"
  read -r FS_TYP FS_OPT <<< "$OPT"
  [ "$FS_TYP" = ext4 ] || abbruch "$MP ist nicht das ext4 des Fachs ($OPT) – ich ändere nichts, bitte melden."
  for o in noexec nosuid nodev; do
    [[ ",$FS_OPT," == *",$o,"* ]] || abbruch "$MP ist ohne $o eingehängt ($FS_OPT) – ich ändere nichts, bitte melden."
  done
  setze "$MP" root:root 755
  if [ -f "$MP/$MARKE" ]; then
    [ "$(head -c 64 "$MP/$MARKE" | tr -d '[:space:]')" = "$U" ] \
      || abbruch "in $MP liegt die Marke eines anderen Fachs – falsches Bild eingehängt? Ich ändere nichts, bitte melden."
    echo "   $MP/$MARKE: schon da"
  else
    # Nur in ein frisches Fach: außer lost+found und unseren Ordnern darf dort noch nichts liegen
    for e in "$MP"/* "$MP"/.[!.]*; do
      [ -e "$e" ] || [ -L "$e" ] || continue
      case "${e##*/}" in lost+found|videos|replays|sitzungen|status) ;;
        *) abbruch "in $MP liegt schon ${e##*/}, aber keine Marke – ich ändere nichts, bitte melden." ;; esac
    done
    if [ "$PROBE" = 1 ]; then echo "   (Probe: würde $MP/$MARKE mit $U schreiben)"
    else ( umask 022; printf '%s\n' "$U" > "$MP/$MARKE" ); echo "   $MP/$MARKE geschrieben"; fi
  fi
  [ ! -e "$MP/$MARKE" ] || setze "$MP/$MARKE" root:root 644
  for o in $UNTERORDNER; do ordner "$MP/$o" "$U:briefkasten" 700; done
fi

sag "5/5 Schlüssel: hochladen/$U (PC, nur schreiben) und abholen/$U (Mini, nur lesen, nur von $MINI_IP)"
schluessel() {   # Datei Zeile Rolle
  local ziel="$1" zeile="$2"
  [ ! -L "$ziel" ] || abbruch "$ziel ist ein Link – ich ändere nichts, bitte melden."
  if [ -f "$ziel" ] && [ "$(cat "$ziel")" = "$zeile" ]; then echo "   $ziel: schon da"
  elif [ -f "$ziel" ]; then
    echo "   $ziel: bisher $(fingerabdruck "$(cut -d' ' -f2- "$ziel")"), neu $(fingerabdruck "$(cut -d' ' -f2- <<< "$zeile")")"
    frage "$3-Schlüssel von $NAME austauschen? (die alte Zeile wird gesichert)" || { echo "   bleibt, wie er ist"; return 0; }
    if [ "$PROBE" = 0 ]; then
      sichere "$ziel" "${ziel##*/}.$(basename "$(dirname "$ziel")")"
      ( umask 022; printf '%s\n' "$zeile" > "$ziel.neu" )
      mv -f "$ziel.neu" "$ziel"
      echo "   $ziel ausgetauscht"
    fi
  elif [ "$PROBE" = 1 ]; then echo "   (Probe: würde $ziel schreiben: ${zeile%% ssh-*} ssh-ed25519 …)"
  else
    ( umask 022; printf '%s\n' "$zeile" > "$ziel" )
    echo "   $ziel geschrieben"
  fi
  [ ! -e "$ziel" ] || setze "$ziel" root:root 644
}
if [ -n "$PC" ]; then schluessel "$HOCH" "restrict $PC" PC
else echo "   $HOCH: bleibt (kein --pc)"; fi
if [ -n "$ABHOLEN" ]; then schluessel "$AB" "from=\"$MINI_IP\",restrict $ABHOLEN" Mini
else echo "   $AB: bleibt (kein --abholen)"; fi
trap - EXIT

if [ "$PROBE" = 1 ]; then sag "Probe fertig – nichts verändert. Echt: dieselbe Zeile ohne --probe"; exit 0; fi
sag "Prüfung"
if [ -f "$HIER/briefkasten-pruefen.sh" ]; then
  bash "$HIER/briefkasten-pruefen.sh" "$NAME" < /dev/null || { echo "❌ Die Prüfung hat etwas gefunden (siehe oben)."; exit 1; }
fi
sag "Fertig: Fach von $NAME ($(( $(stat -c %s "$BILD") / GB )) GB) – $U lädt hoch, der Mini holt ab."
echo "Vergrößern:     bash $0 $NAME --groesse 40   (nur wachsen)"
echo "Ausschalten:    bash $0 $NAME --sperren      (Fach und Schlüssel bleiben)"
echo "Prüfen:         bash $HIER/briefkasten-pruefen.sh $NAME"
