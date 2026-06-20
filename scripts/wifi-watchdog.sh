#!/usr/bin/env bash
# WiFi watchdog: bağlantı kopuksa DNS'i yeniler. Cron ile 3 dakikada bir çalıştırılır.
TIMESTAMP="$(date '+%Y-%m-%d %H:%M:%S')"
PING_HOST="8.8.8.8"

if ping -c 1 -W 5 "$PING_HOST" > /dev/null 2>&1; then
  echo "$TIMESTAMP [wifi-watchdog] OK"
else
  echo "$TIMESTAMP [wifi-watchdog] No connectivity — flushing DNS cache..."
  # Termux'ta DNS yenileme için ağı yeniden başlat (root gerekmez)
  if command -v termux-wifi-enable > /dev/null 2>&1; then
    termux-wifi-enable false
    sleep 2
    termux-wifi-enable true
    sleep 3
  fi
  # İkinci deneme
  if ping -c 1 -W 5 "$PING_HOST" > /dev/null 2>&1; then
    echo "$TIMESTAMP [wifi-watchdog] Recovered after WiFi reset"
  else
    echo "$TIMESTAMP [wifi-watchdog] Still no connectivity"
  fi
fi
