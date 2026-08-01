#!/data/data/com.termux/files/usr/bin/bash
# Riavvia live_server.py.
# Uso: bash scripts/restart.sh
cd "$(dirname "$0")"
bash stop.sh
sleep 1
bash start.sh
