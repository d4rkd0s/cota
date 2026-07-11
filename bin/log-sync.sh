#!/usr/bin/env bash
# bin/log-sync.sh — idempotent QRZ Logbook upload (no AI at runtime). Cron-able:
#
#   */30 * * * *  /path/to/ft8-claude/bin/log-sync.sh >> /path/to/ft8-claude/data/logsync.log 2>&1
#
# Needs a QRZ "XML Logbook Data" subscription key in ~/.config/cota/qrz.key
# (chmod 600, never in the repo) — run without one for setup instructions.
# Preview without touching the network: bin/log-sync.sh --dry-run
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/bin/logsync.py" "$@"
