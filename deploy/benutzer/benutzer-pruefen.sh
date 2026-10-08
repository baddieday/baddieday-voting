#!/usr/bin/env bash
# Im CT als root:  bash benutzer-pruefen.sh <name>   ·   bash benutzer-pruefen.sh <name> --vorab
# Mehrbenutzer (docs/MEHRBENUTZER.md, „Neuen Freund anlegen“): Ist <name> sauber von dir und den anderen Freunden
# getrennt? Ändert nichts. Gelesen werden nur die Konfig seiner Instanz und die Bot-Tokens (deine .env und die der
# Freunde) – verglichen als SHA-256, nie angezeigt.
#   1 Besitzer und Rechte: /var/lib/clip-benutzer (root, 0711), sein Ordner (root:clip-<name>, 0750), instanz.toml, .env
#     und Marke (root:clip-<name>, 0640), seine Ordner (clip-<name>, 0700), kein kein-lager
#   2 Sperre: [sperre].datei in seiner instanz.toml = deine wirksame Sperre = die in den Vorlagen eingebundene; deine
#     Sperrdatei ist für alle lesbar und nur für dich beschreibbar
#   3 Zugänge: Bot-Token da und verschieden von deinen und denen aller Freunde; Epic-Konto-ID da; mit Telegram verbunden
#     (nur Hinweis – ohne Verbindung bleibt nur sein Bot aus)
#   4 In seiner Sandbox (clip-freund-pruefen@<name>): nichts von dir und den anderen Freunden sichtbar, und die Sperre
#     dort ist genau deine Datei (Gerät und Inode)
#   5 Bot-Link
# --vorab: nur 1–3 (benutzer-anlegen.sh, bevor etwas eingeschaltet wird). Exit 0 = alles getrennt, 1 = Befund.
# Intern für benutzer-anlegen.sh:  printf '%s' "$TOKEN" | bash benutzer-pruefen.sh --token-frei <name>
#   (Exit 0 = dieser Bot-Token ist noch nirgends eingetragen, 1 = schon vergeben)
set -euo pipefail
BENUTZER_DIR="${BENUTZER_DIR:-/var/lib/clip-benutzer}"
PROD="${PROD:-/opt/clip-pipeline}"
REGIE="${REGIE:-/opt/clip-regie}"
UNITS="${UNITS:-/etc/systemd/system}"
TELEGRAM_API="${TELEGRAM_API:-https://api.telegram.org}"   # nur Tests setzen das
HIER="$(cd "$(dirname "$0")" && pwd)"
NAME=""
VORAB=0
TOKEN_FREI=0
aufruf() { echo "Aufruf: bash $0 <name> [--vorab]"; exit 2; }
while [ $# -gt 0 ]; do
  case "$1" in
    --vorab) VORAB=1 ;;
    --token-frei) TOKEN_FREI=1 ;;
    -*) aufruf ;;
    *) [ -z "$NAME" ] || aufruf; NAME="$1" ;;
  esac
  shift
done
[[ "$NAME" =~ ^[a-z][a-z0-9-]{1,26}$ ]] || aufruf
I="$BENUTZER_DIR/$NAME"

sag() { printf '\n== %s\n' "$*"; }
BEFUNDE=0
ok() { printf '   ✅ %s\n' "$*"; }
befund() { printf '   ❌ %s\n' "$*"; BEFUNDE=$((BEFUNDE + 1)); }

# Bot-Tokens vergleichen – nur in Python (nie auf einer Befehlszeile), ausgegeben werden nur Namen.
# Argumente: eigener Name, "-" (sein Token kommt von stdin statt aus seiner .env) oder "", danach wer=pfad …
TOKENS_PY="$(cat <<'PY'
import hashlib, sys
NAMEN = ("LEARN_BOT_TOKEN", "TELEGRAM_BOT_TOKEN")

def lies(pfad):
    werte = {}
    try:
        with open(pfad, encoding="utf-8") as datei:
            zeilen = datei.read().splitlines()
    except FileNotFoundError:
        return werte
    for zeile in zeilen:
        zeile = zeile.strip()
        if zeile and not zeile.startswith("#") and "=" in zeile:
            schluessel, wert = zeile.split("=", 1)
            werte.setdefault(schluessel.strip(), wert.strip().strip('"').strip("'"))
    return werte

def abdruck(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

eigen, von_stdin = sys.argv[1], sys.argv[2] == "-"
alle = []   # (wer, Abdruck)
for eintrag in sys.argv[3:]:
    wer, pfad = eintrag.split("=", 1)
    if wer == eigen and von_stdin:
        continue
    werte = lies(pfad)
    alle += [(wer, abdruck(werte[n])) for n in NAMEN if werte.get(n)]
if von_stdin and (kandidat := sys.stdin.read().strip()):
    alle.append((eigen, abdruck(kandidat)))
meine = {a for wer, a in alle if wer == eigen}
fehler = 0
if not meine:
    print(f"kein Bot-Token für {eigen} (LEARN_BOT_TOKEN in seiner .env)")
    fehler = 1
gleich = sorted({wer for wer, a in alle if wer != eigen and a in meine})
for wer in gleich:
    print(f"der Bot-Token von {eigen} ist derselbe wie bei {wer} – jeder braucht einen eigenen Bot (@BotFather)")
    fehler = 1
if not von_stdin:   # auch die anderen untereinander: über alle .env verschieden
    gesehen = {}
    for wer, a in alle:
        if wer != eigen and a in gesehen and gesehen[a] != wer:
            print(f"{gesehen[a]} und {wer} haben denselben Bot-Token")
            fehler = 1
        gesehen.setdefault(a, wer)
sys.exit(fehler)
PY
)"
# Quellen für den Vergleich: deine .env (Produktion und Lern-Bot-Stand) und die .env jedes Freundes
token_quellen() {
  QUELLEN=("Florian=$PROD/.env" "Florian=$REGIE/.env")
  local ordner n
  for ordner in "$BENUTZER_DIR"/*; do
    n="${ordner##*/}"
    [ -d "$ordner" ] && [ ! -L "$ordner" ] && [[ $n =~ ^[a-z][a-z0-9-]{1,26}$ ]] || continue
    [ -f "$ordner/.env" ] && [ ! -L "$ordner/.env" ] || continue
    QUELLEN+=("$n=$ordner/.env")
  done
}

[ "$(id -u)" = 0 ] || { echo "Bitte als root im CT ausführen."; exit 1; }

if [ "$TOKEN_FREI" = 1 ]; then
  token_quellen
  python3 -I -c "$TOKENS_PY" "$NAME" - "${QUELLEN[@]}"
  exit $?
fi

# Python als pipeline mit sauberer Umgebung – so sieht es auch dein Dienst (deine wirksame Konfig)
florian() { (cd / && runuser -u pipeline -- env -i PATH=/usr/bin:/bin LANG=C.UTF-8 "$PROD/.venv/bin/python" -I -c "$@"); }
# Besitzer und Rechte: $1 Pfad, $2 Art (d|f), $3 Besitzer:Gruppe, $4 Rechte
erwarte() {
  local ist
  if [ -L "$1" ]; then befund "$1 ist ein Link"; return; fi
  if ! { { [ "$2" = d ] && [ -d "$1" ]; } || { [ "$2" = f ] && [ -f "$1" ]; }; }; then befund "$1 fehlt"; return; fi
  ist="$(stat -c '%U:%G %a' "$1")"
  if [ "$ist" = "$3 $4" ]; then ok "$1  ($ist)"; else befund "$1: $ist statt $3 $4"; fi
}

sag "1/5 Besitzer und Rechte"
getent passwd "clip-$NAME" >/dev/null || befund "Benutzer clip-$NAME fehlt"
erwarte "$BENUTZER_DIR" d root:root 711
erwarte "$I" d "root:clip-$NAME" 750
if [ -f "$I/.clip-benutzer" ] && [ "$(head -c 64 "$I/.clip-benutzer" | tr -d '[:space:]')" != "$NAME" ]; then
  befund "$I/.clip-benutzer nennt nicht $NAME"
fi
for datei in .clip-benutzer instanz.toml .env; do erwarte "$I/$datei" f "root:clip-$NAME" 640; done
for ordner in db daten regie musik material sfx cache; do erwarte "$I/$ordner" d "clip-$NAME:clip-$NAME" 700; done
if [ -e "$I/kein-lager" ] || [ -L "$I/kein-lager" ]; then befund "$I/kein-lager gibt es – das darf nicht sein (M10)"; fi

sag "2/5 Die eine Rechen-Sperre"
VORLAGE="$UNITS/clip-freund-bot@.service"
[ -f "$VORLAGE" ] || VORLAGE="$HIER/clip-freund-bot@.service"
GEBUNDEN="$(awk -F= '$1 == "BindReadOnlyPaths" {split($2, t, " "); print t[1]; exit}' "$VORLAGE")"
SPERRE="$(florian 'from clip_pipeline import konfig, sperre; print(sperre.pfad(konfig.lade()))' 2>/dev/null | tail -n 1)" || SPERRE=""
EIGENE="$(python3 -I -c 'import sys, tomllib
print(tomllib.load(open(sys.argv[1], "rb"))["sperre"]["datei"])' "$I/instanz.toml" 2>/dev/null)" || EIGENE=""
if [ -z "$SPERRE" ]; then befund "deine Sperre ließ sich nicht abfragen (pipeline-Konfig?)"
elif [ "$SPERRE" != "$GEBUNDEN" ]; then befund "deine Sperre ist $SPERRE, die Vorlagen binden $GEBUNDEN ein"
elif [ "$EIGENE" != "$SPERRE" ]; then befund "[sperre].datei von $NAME ist '$EIGENE' statt $SPERRE"
else ok "$NAME nimmt deine Sperre $SPERRE"; fi
if [ -n "$SPERRE" ]; then
  if [ -L "$SPERRE" ] || [ ! -f "$SPERRE" ]; then befund "$SPERRE fehlt oder ist ein Link"
  else
    MODUS="$(stat -c %a "$SPERRE")"
    if (( (8#$MODUS & 8#004) && !(8#$MODUS & 8#022) )); then ok "$SPERRE für alle lesbar, nur für dich beschreibbar ($MODUS)"
    else befund "$SPERRE hat Rechte $MODUS – nötig: für alle lesbar, für niemand anderen beschreibbar (0644)"; fi
  fi
fi

sag "3/5 Zugänge (nur verglichen, nie angezeigt)"
token_quellen
if MELDUNG="$(python3 -I -c "$TOKENS_PY" "$NAME" "" "${QUELLEN[@]}" < /dev/null)"; then
  ok "eigener Bot-Token, verschieden von deinen und denen der anderen Freunde"
elif [ -z "$MELDUNG" ]; then
  befund "die Bot-Tokens ließen sich nicht vergleichen (eine .env nicht lesbar?)"
else
  while IFS= read -r zeile; do [ -z "$zeile" ] || befund "$zeile"; done <<< "$MELDUNG"
fi
if grep -Eq '^[[:space:]]*CLIP_EPIC_ID=[^[:space:]]' "$I/.env" 2>/dev/null; then ok "Epic-Konto-ID eingetragen"
else befund "Epic-Konto-ID fehlt (CLIP_EPIC_ID in $I/.env)"; fi
if grep -Eq '^[[:space:]]*(LEARN_BOT|TELEGRAM)_ALLOWED_USER_ID=[0-9]' "$I/.env" 2>/dev/null; then
  ok "mit Telegram verbunden (seine Zahl ist eingetragen)"
else echo "   ℹ️  noch nicht mit Telegram verbunden – sein Bot bleibt aus. Einladungslink: bash $HIER/benutzer-anlegen.sh $NAME"; fi

if [ "$VORAB" = 1 ]; then
  if [ "$BEFUNDE" = 0 ]; then sag "Vorab-Prüfung: alles in Ordnung"; exit 0; fi
  sag "Vorab-Prüfung: $BEFUNDE Befund(e) – siehe ❌ oben"; exit 1
fi

sag "4/5 In seiner Sandbox (clip-freund-pruefen@$NAME)"
VORHER=$BEFUNDE
EINHEIT="clip-freund-pruefen@$NAME.service"
if systemctl start "$EINHEIT"; then RC=0; else RC=$?; fi
journalctl --sync 2>/dev/null || true
LAUF="$(systemctl show -p InvocationID --value "$EINHEIT" 2>/dev/null || true)"
if [ -n "$LAUF" ]; then AUSGABE="$(journalctl -o cat --no-pager "_SYSTEMD_INVOCATION_ID=$LAUF" 2>/dev/null || true)"
else AUSGABE="$(journalctl -o cat --no-pager -n 40 -u "$EINHEIT" 2>/dev/null || true)"; fi
ERGEBNIS="$(printf '%s\n' "$AUSGABE" | grep '^{' | tail -n 1 || true)"
if [ -z "$ERGEBNIS" ]; then
  befund "keine Antwort aus der Sandbox (Exit $RC) – Log: journalctl -u $EINHEIT -n 30"
else
  # Befunde und Hinweise lesbar, dazu Gerät und Inode der Sperrdatei, wie der Freund sie sieht
  while IFS=$'\t' read -r art text; do
    case "$art" in
      befund) befund "$text" ;;
      hinweis) echo "   ℹ️  $text" ;;
      sperre) INNEN="$text" ;;
    esac
  done < <(python3 -I -c 'import json, sys
d = json.loads(sys.argv[1])
for b in d.get("befunde", []):
    print("befund\t" + b.get("pfad", "?") + " – " + b.get("grund", ""))
for h in d.get("hinweise", []):
    print("hinweis\t" + h)
s = d.get("sperre", {})
print("sperre\t" + str(s.get("dev", "")) + " " + str(s.get("ino", "")))' "$ERGEBNIS")
  [ "$RC" = 0 ] || [ "$BEFUNDE" -gt "$VORHER" ] || befund "clip-freund-pruefen@$NAME endete mit Exit $RC"
  DRAUSSEN="$(stat -c '%d %i' "$SPERRE" 2>/dev/null || true)"
  if [ -n "$DRAUSSEN" ] && [ "${INNEN:-}" = "$DRAUSSEN" ]; then
    ok "die Sperre in seiner Sandbox ist deine Datei (Gerät und Inode $DRAUSSEN)"
  else befund "die Sperre in seiner Sandbox (${INNEN:-?}) ist nicht deine Datei (${DRAUSSEN:-?})"; fi
  [ "$BEFUNDE" -gt "$VORHER" ] || ok "in seiner Sandbox nichts von dir und den anderen Freunden sichtbar"
fi

sag "5/5 Bot-Link"
LINK="$(python3 -I -c 'import json, sys, urllib.request
werte = {}
for zeile in open(sys.argv[1], encoding="utf-8").read().splitlines():
    if "=" in zeile and not zeile.lstrip().startswith("#"):
        k, v = zeile.split("=", 1)
        werte.setdefault(k.strip(), v.strip().strip("\"").strip(chr(39)))
try:
    with urllib.request.urlopen(sys.argv[2] + "/bot" + werte["LEARN_BOT_TOKEN"] + "/getMe", timeout=10) as antwort:
        print("https://t.me/" + json.load(antwort)["result"]["username"])
except Exception as e:
    print("(Telegram nicht erreichbar: " + type(e).__name__ + ")")
    sys.exit(1)' "$I/.env" "$TELEGRAM_API" 2>/dev/null)" || true
echo "   Bot von $NAME: ${LINK:-(unbekannt)}"
echo "   Verbunden wird er über den Einladungslink aus benutzer-anlegen.sh; für ihn: docs/FREUNDE.md"

if [ "$BEFUNDE" = 0 ]; then
  sag "Alles getrennt: $NAME sieht nichts von dir und den anderen Freunden."
  exit 0
fi
sag "$BEFUNDE Befund(e) – siehe ❌ oben. Ausschalten (Daten bleiben): bash $HIER/benutzer-stilllegen.sh $NAME"
exit 1
