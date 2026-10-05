#!/bin/bash
# Finalize setup — run AFTER code is uploaded to /opt/rays
# Usage: bash /opt/rays/deploy/finalize.sh

set -e

echo "==> Installing Python dependencies (this takes 3-5 min)..."
sudo -u rays bash <<'EOSU'
cd /opt/rays
source venv/bin/activate
pip install -r requirements.txt --quiet
playwright install chromium --with-deps
EOSU

echo "==> Installing systemd service for streamlit..."
cp /opt/rays/deploy/streamlit.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable streamlit
systemctl start streamlit

# nginx is shared with etf-tracker and lives outside this repo
# (final/server/nginx.conf → /etc/nginx/sites-enabled/rays). Install it separately.

echo "==> Installing daily refresh timer (08:00 Hong Kong time)..."
cp /opt/rays/deploy/rays-daily.service /opt/rays/deploy/rays-daily.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now rays-daily.timer
systemctl list-timers rays-daily.timer --no-pager

echo ""
echo "========================================"
echo "✓ Deployment complete!"
echo ""
echo "Streamlit running at:  http://$(curl -s ifconfig.me)/sentiment/  (once nginx is configured)"
echo "Service status:        systemctl status streamlit"
echo "Daily timer:           systemctl list-timers rays-daily.timer"
echo "Daily logs:            tail -f /opt/rays/data/cron.log"
echo ""
echo "Test alert email NOW:  sudo -u rays /opt/rays/venv/bin/python /opt/rays/check_alerts.py"
echo "========================================"
