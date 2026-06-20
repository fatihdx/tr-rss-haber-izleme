#!/data/data/com.termux/files/usr/bin/bash
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR"

termux-wake-lock || true

# Create venv
if [ ! -d ".venv" ]; then
  python -m venv .venv
fi
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# First-time config
if [ ! -f "config.yaml" ]; then
  cp config.sample.yaml config.yaml
  echo "A sample config.yaml was created. Please edit it and set your token/chat_ids."
fi

mkdir -p logs

# Crontab kurulumu (cronie kurulu olmalı)
if command -v crontab > /dev/null 2>&1; then
  CRON_WATCHDOG="*/5 * * * * $SCRIPT_DIR/scripts/watchdog.sh >> $SCRIPT_DIR/logs/watchdog.log 2>&1"
  CRON_WIFI="*/3 * * * * $SCRIPT_DIR/scripts/wifi-watchdog.sh >> $SCRIPT_DIR/logs/wifi.log 2>&1"
  CRON_RESTART="0 4 * * * sv restart rssbot >> $SCRIPT_DIR/logs/watchdog.log 2>&1"
  (crontab -l 2>/dev/null | grep -v "watchdog.sh\|wifi-watchdog.sh\|sv restart rssbot"; \
   echo "$CRON_WATCHDOG"; echo "$CRON_WIFI"; echo "$CRON_RESTART") | crontab -
  echo "Crontab kuruldu. Kontrol: crontab -l"
else
  echo "UYARI: cronie bulunamadı. 'pkg install cronie' ile kurun."
fi

echo "Install complete."
