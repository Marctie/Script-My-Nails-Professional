#!/data/data/com.termux/files/usr/bin/bash
# Avvia live_server.py in background, come i bot Telegram su Termux.
# Uso: bash scripts/start.sh
set -e
cd "$(dirname "$0")/.."

if [ -f logs/live_server.pid ] && kill -0 "$(cat logs/live_server.pid)" 2>/dev/null; then
  echo "live_server e' gia' in esecuzione (PID $(cat logs/live_server.pid))."
  exit 0
fi

mkdir -p logs
source venv/bin/activate 2>/dev/null || true
nohup python app/live_server.py >> logs/live_server.log 2>&1 &
echo "live_server avviato (PID $!)."
