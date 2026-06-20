#!/data/data/com.termux/files/usr/bin/bash
set -e
"$(dirname "$0")/stop.sh" || true
sleep 1
"$(dirname "$0")/start.sh"
