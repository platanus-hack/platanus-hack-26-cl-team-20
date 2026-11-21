#!/bin/zsh
# Radar de agentes vivos en Herdr (solo lectura).
G=$'\e[1;32m'; D=$'\e[2;32m'; Y=$'\e[1;33m'; R=$'\e[0m'
tput civis; trap 'tput cnorm' EXIT
while true; do
  out=$(herdr agent list 2>/dev/null | python3 "${0:A:h}/agentes.py")
  printf '\e[H\e[2J%s> AGENTS ONLINE%s\n' "$G" "$R"
  echo "$out" | while IFS= read -r l; do
    case "$l" in *RUN*) printf '%s%s%s\n' "$G" "$l" "$R";; *WAIT*) printf '%s%s%s\n' "$Y" "$l" "$R";; *) printf '%s%s%s\n' "$D" "$l" "$R";; esac
  done
  sleep 3
done
