#!/bin/zsh
# Banner + cuenta regresiva al fin del hacking (dom 22-nov 08:30). Ancho ≤ 40 cols para teléfono.
G=$'\e[1;32m'; D=$'\e[2;32m'; R=$'\e[0m'; END=$(date -j -f "%Y-%m-%d %H:%M" "2026-11-22 08:30" +%s)
tput civis; trap 'tput cnorm' EXIT
while true; do
  now=$(date +%s); s=$((END-now)); ((s<0)) && s=0
  printf '\e[H\e[2J%s' "$G"
  cat <<'B'
 ___ _  _   ___  __
| _ \ || | |_  )/ /
|  _/ __ |  / // _ \
|_| |_||_| /___\___/
B
  printf '%s platanus hack 26 // physical world%s\n\n' "$D" "$R"
  printf '%s T-MINUS %02d:%02d:%02d%s\n' "$G" $((s/3600)) $((s%3600/60)) $((s%60)) "$R"
  printf '%s %s  load %s%s\n' "$D" "$(date +%H:%M:%S)" "$(sysctl -n vm.loadavg | awk '{print $2}')" "$R"
  sleep 1
done
