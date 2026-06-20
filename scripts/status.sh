#!/data/data/com.termux/files/usr/bin/bash
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR"
if [ -f bot.pid ] && ps -p "$(cat bot.pid)" > /dev/null 2>&1; then
  echo "Running. PID=$(cat bot.pid)"
else
  echo "Not running (PID file missing or process dead)."
fi

# runit servis durumu (kuruluysa)
if command -v sv > /dev/null 2>&1; then
  echo "--- runit servis ---"
  sv status rssbot 2>/dev/null || echo "rssbot servisi bulunamadı (runit kurulmamış olabilir)"
fi

if [ -f logs/bot.log ]; then
  echo "--- Last 20 log lines ---"
  tail -n 20 logs/bot.log
fi
