#!/bin/bash
# Server setup script — run ONCE on a fresh Ubuntu 22.04 Hetzner server.
# Usage:
#   ssh root@YOUR_SERVER_IP
#   bash setup_server.sh

set -e

echo "========================================"
echo "Rays Dashboard — Hetzner Server Setup"
echo "========================================"
echo ""

# 1. System update
echo "==> Updating system packages..."
apt-get update -qq
apt-get upgrade -y -qq

# 2. Install required system packages
echo "==> Installing Python, nginx, system deps..."
apt-get install -y -qq \
    python3 python3-venv python3-pip \
    nginx \
    git curl wget \
    cron \
    libnss3 libxss1 libasound2t64 libxtst6 libxrandr2 \
    libgbm1 libpango-1.0-0 libpangocairo-1.0-0 libgtk-3-0 \
    fonts-liberation fonts-noto-cjk

# 3. Set timezone to NY (so cron 19:40 = 8 PM ET)
echo "==> Setting timezone to America/New_York..."
timedatectl set-timezone America/New_York
echo "Current time: $(date)"

# 4. Create app user (no sudo, runs streamlit)
echo "==> Creating 'rays' user..."
if ! id -u rays >/dev/null 2>&1; then
    useradd -m -s /bin/bash rays
fi

# 5. Create app directory
mkdir -p /opt/rays
chown rays:rays /opt/rays

# 6. Setup Python virtual env (as 'rays' user)
echo "==> Setting up Python venv..."
sudo -u rays bash <<'EOSU'
cd /opt/rays
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip --quiet
EOSU

# 7. Open firewall ports (22 SSH, 80 HTTP, 443 HTTPS)
echo "==> Configuring UFW firewall..."
ufw --force enable
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw status

echo ""
echo "========================================"
echo "✓ Server base setup complete!"
echo ""
echo "Next steps (from your Mac):"
echo "1. Upload code:    bash deploy/upload_to_server.sh root@YOUR_IP"
echo "2. SSH in:         ssh root@YOUR_IP"
echo "3. Finish setup:   bash /opt/rays/deploy/finalize.sh"
echo "========================================"
