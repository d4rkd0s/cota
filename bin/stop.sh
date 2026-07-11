#!/usr/bin/env bash
pkill -f "rx-loop.sh" && echo "rx-loop stopped"
pkill -f "dashboard.py" && echo "dashboard stopped"
exit 0
