#!/bin/bash
# Upload code from local Mac to Hetzner server.
# Usage: bash deploy/upload_to_server.sh root@YOUR_SERVER_IP
#
# This script rsyncs the project to /opt/rays on the server.
# It copies email_config.json (which is gitignored) since it's needed.

set -e

if [ -z "$1" ]; then
    echo "Usage: $0 root@SERVER_IP"
    exit 1
fi

TARGET="$1"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> Uploading to $TARGET:/opt/rays/"
echo "    From: $PROJECT_DIR"

rsync -avz --progress \
    --exclude 'venv/' \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    --exclude '.DS_Store' \
    --exclude '.git/' \
    --exclude 'data/breadth_cache.json' \
    --exclude 'data/stockcharts/' \
    --exclude 'data/barchart_pc.png' \
    --exclude '*.bak' \
    "$PROJECT_DIR/" "$TARGET:/opt/rays/"

echo ""
echo "==> Setting ownership to rays user..."
ssh "$TARGET" "chown -R rays:rays /opt/rays && chmod +x /opt/rays/deploy/*.sh /opt/rays/run_daily.py"

echo ""
echo "✓ Upload complete!"
echo "Next: ssh $TARGET 'bash /opt/rays/deploy/finalize.sh'"
