#!/data/data/com.termux/files/usr/bin/bash
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR"
if [ -f bot.pid ]; then
  kill "$(cat bot.pid)" || true
  rm -f bot.pid
  echo "Stopped."
else
  echo "No PID file; trying to kill by name..."
  pkill -f "python src/bot.py" || true
fi
