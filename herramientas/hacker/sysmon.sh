#!/bin/zsh
# Monitor angosto (≤ 40 cols): CPU, RAM y tráfico de red del mini.
G=$'\e[1;32m'; D=$'\e[2;32m'; R=$'\e[0m'
bar(){ local p=$1 n=$(( $1 * 24 / 100 )); printf '%s[' "$G"; printf '%*s' $n '' | tr ' ' '#'; printf '%*s' $((24-n)) '' | tr ' ' '.'; printf ']%s %3d%%' "$R" $p; }
net(){ netstat -ib | awk '$1=="en0" && $3 ~ /Link/ {print $7, $10; exit}'; }
tput civis; trap 'tput cnorm' EXIT
read i0 o0 <<< "$(net)"
while true; do
  cpu=$(top -l 2 -n 0 -s 1 | awk '/CPU usage/ {u=$3+$5} END {printf "%d", u}')
  pages=$(sysctl -n hw.memsize); free=$(memory_pressure -Q 2>/dev/null | awk -F': ' '/free percentage/ {gsub("%","",$2); print $2}')
  mem=$(( 100 - ${free:-0} ))
  read i1 o1 <<< "$(net)"; din=$(( (i1-i0)/1024 )); dout=$(( (o1-o0)/1024 )); i0=$i1; o0=$o1
  printf '\e[H\e[2J%s> SYS mini-sat%s\n\n' "$G" "$R"
  printf 'CPU '; bar $cpu; echo
  printf 'RAM '; bar $mem; echo
  printf '\n%s NET en0  ↓ %5d KB/s  ↑ %5d KB/s%s\n' "$D" $din $dout "$R"
  printf '%s PROC %s  UP %s%s\n' "$D" "$(ps -A | wc -l | tr -d ' ')" "$(uptime | sed -E 's/.*up ([^,]*),.*/\1/')" "$R"
done
