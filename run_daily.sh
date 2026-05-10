#!/bin/bash
# Wrapper: run cache update, then alert check.
# Called by launchd daily.
cd /Users/chenjiexin/Desktop/rays
source venv/bin/activate

echo "===== $(date '+%Y-%m-%d %H:%M:%S') Starting daily run ====="
python update_cache.py
echo ""
echo "===== Running alert check ====="
python check_alerts.py
echo "===== Done ====="
