#!/usr/bin/env bash
# Start the FT8-Claude display + RX loop (RX only — never transmits)
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN=$ROOT/bin; DATA=$ROOT/data
[ -f "$ROOT/station.conf" ] && . "$ROOT/station.conf"
mkdir -p "$DATA"
pgrep -f "dashboard.py" >/dev/null || { nohup python3 "$BIN/dashboard.py" >>"$DATA/dashboard.log" 2>&1 & echo "dashboard started"; }
pgrep -f "rx-loop.sh"   >/dev/null || { nohup bash "$BIN/rx-loop.sh"      >>"$DATA/rx-loop.log"   2>&1 & echo "rx-loop started"; }
echo "display: http://localhost:${HTTP_PORT:-8074}"
