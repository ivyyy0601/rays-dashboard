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

echo "==> Installing nginx config..."
cp /opt/rays/deploy/nginx.conf /etc/nginx/sites-available/rays
ln -sf /etc/nginx/sites-available/rays /etc/nginx/sites-enabled/rays
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

echo "==> Installing daily cron job (19:40 NY time)..."
sudo -u rays bash <<'EOSU'
(crontab -l 2>/dev/null | grep -v "rays/run_daily.py"; echo "40 19 * * * /opt/rays/venv/bin/python /opt/rays/run_daily.py >> /opt/rays/data/cron.log 2>&1") | crontab -
crontab -l
EOSU

echo ""
echo "========================================"
echo "✓ Deployment complete!"
echo ""
echo "Streamlit running at:  http://$(curl -s ifconfig.me)/"
echo "Service status:        systemctl status streamlit"
echo "Cron jobs:             sudo -u rays crontab -l"
echo "Daily logs:            tail -f /opt/rays/data/cron.log"
echo ""
echo "Test alert email NOW:  sudo -u rays /opt/rays/venv/bin/python /opt/rays/check_alerts.py"
echo "========================================"
