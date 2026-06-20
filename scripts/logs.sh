#!/data/data/com.termux/files/usr/bin/bash
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR"
[ -f logs/bot.log ] && tail -f logs/bot.log || echo "No logs yet."
