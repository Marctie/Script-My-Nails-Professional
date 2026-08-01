#!/data/data/com.termux/files/usr/bin/bash
# Mostra se live_server e' attivo e le sue statistiche (via /api/stats).
# Uso: bash scripts/status.sh
cd "$(dirname "$0")/.."

if [ -f logs/live_server.pid ] && kill -0 "$(cat logs/live_server.pid)" 2>/dev/null; then
  echo "Stato: ATTIVO (PID $(cat logs/live_server.pid))"
else
  echo "Stato: FERMO"
fi

echo ""
echo "Statistiche (http://127.0.0.1:5001/api/stats):"
curl -s http://127.0.0.1:5001/api/stats || echo "  (server non raggiungibile in locale)"
echo ""
echo ""
echo "Ultime righe di log:"
tail -n 20 logs/live_server.log 2>/dev/null || echo "  (nessun log ancora)"
