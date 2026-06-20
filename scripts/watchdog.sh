#!/data/data/com.termux/files/usr/bin/bash
# Watchdog: Bot çalışmıyorsa yeniden başlatır. Cron ile 5 dakikada bir çalıştırılır.
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR"

TIMESTAMP="$(date '+%Y-%m-%d %H:%M:%S')"

# runit varsa runit ile yönet
if command -v sv > /dev/null 2>&1 && sv status rssbot > /dev/null 2>&1; then
  STATUS="$(sv status rssbot 2>&1)"
  if echo "$STATUS" | grep -q "^down:"; then
    echo "$TIMESTAMP [watchdog] rssbot down, starting via sv..."
    sv start rssbot
  else
    echo "$TIMESTAMP [watchdog] rssbot OK ($STATUS)"
  fi
  exit 0
fi

# runit yoksa PID dosyasıyla kontrol et
if [ -f bot.pid ]; then
  PID="$(cat bot.pid)"
  if ps -p "$PID" > /dev/null 2>&1; then
    echo "$TIMESTAMP [watchdog] Bot running. PID=$PID"
    exit 0
  fi
fi

echo "$TIMESTAMP [watchdog] Bot not running, restarting..."
nohup .venv/bin/python src/bot.py >> logs/console.out 2>&1 &
echo $! > bot.pid
echo "$TIMESTAMP [watchdog] Restarted. PID=$(cat bot.pid)"
