#!/usr/bin/env bash
# SSH-Einstieg für n8n. In ~pipeline/.ssh/authorized_keys ist der n8n-Schlüssel hierauf festgelegt:
#   command="/opt/clip-pipeline/deploy/n8n-lauf.sh",no-pty,no-port-forwarding,no-agent-forwarding ssh-ed25519 AAAA... n8n
# n8n schickt z. B. "/opt/clip-pipeline/bin/pipeline render --session 2026-09-23_20-15-33".
# Erlaubt sind nur die Befehle aus dem Vertrag (CLAUDE.md) – geprüft wird jedes Wort, ausgeführt ohne Shell.
set -u
ID='^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$'
abweisen() { echo '{"fehler": "Aufruf nicht erlaubt"}'; exit 2; }

befehl="${SSH_ORIGINAL_COMMAND:-}"
# n8n ("Working Directory") setzt ein "cd '/opt/clip-pipeline' ; " bzw. "cd /opt/clip-pipeline && " davor
befehl="$(printf '%s' "$befehl" | sed -E "s#^cd +['\"]?/opt/clip-pipeline/?['\"]? *(;|&&) *##")"
read -r -a teile <<< "$befehl"
for i in "${!teile[@]}"; do            # umschließende Anführungszeichen entfernen
  t="${teile[$i]}"; t="${t#\'}"; t="${t%\'}"; t="${t#\"}"; t="${t%\"}"; teile[$i]="$t"
done

case "${teile[0]:-}" in
  /opt/clip-pipeline/bin/pipeline|bin/pipeline|./bin/pipeline|pipeline) ;;
  *) abweisen ;;
esac
case "${teile[1]:-}" in
  prepare|analyze|decide|render)
    [ "${#teile[@]}" -eq 4 ] && [ "${teile[2]}" = "--session" ] && [[ "${teile[3]}" =~ $ID ]] || abweisen ;;
  highlight)
    [ "${#teile[@]}" -eq 6 ] && [ "${teile[2]}" = "--id" ] && [[ "${teile[3]}" =~ $ID ]] \
      && [ "${teile[4]}" = "--tage" ] && [[ "${teile[5]}" =~ ^[0-9]{1,3}$ ]] || abweisen ;;
  status)
    [ "${#teile[@]}" -eq 2 ] || abweisen ;;
  *) abweisen ;;
esac
cd /opt/clip-pipeline || exit 2
exec /opt/clip-pipeline/bin/pipeline "${teile[@]:1}"
