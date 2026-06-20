#!/data/data/com.termux/files/usr/bin/bash
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR"
nohup .venv/bin/python src/bot.py >> logs/console.out 2>&1 &
echo $! > bot.pid
echo "Started. PID=$(cat bot.pid). Logs: logs/bot.log (rotates)"
