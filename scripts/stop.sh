#!/data/data/com.termux/files/usr/bin/bash
# Ferma live_server.py se in esecuzione.
# Uso: bash scripts/stop.sh
cd "$(dirname "$0")/.."

if [ ! -f logs/live_server.pid ]; then
  echo "live_server non risulta in esecuzione (nessun PID file)."
  exit 0
fi

PID=$(cat logs/live_server.pid)
if kill -0 "$PID" 2>/dev/null; then
  kill "$PID"
  echo "live_server fermato (PID $PID)."
else
  echo "PID $PID non attivo, pulisco il file."
fi
rm -f logs/live_server.pid
