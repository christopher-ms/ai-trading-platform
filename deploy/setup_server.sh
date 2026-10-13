#!/usr/bin/env bash
# One-time (and re-runnable) setup of the trading bot on an Ubuntu 24.04
# EC2 instance. Run on the server, from the unpacked repo:
#
#     bash deploy/setup_server.sh
#
# Installs Python 3.13 (the version the laptop venv uses) via uv, the
# pinned requirements, the CloudWatch agent, and the systemd service.
# It does NOT start the bot: do a --dry-run first (see docs/DEPLOY.md).
set -euo pipefail

APP_DIR=/home/ubuntu/ai-trading-platform
cd "$APP_DIR"

if [ ! -f .env ]; then
    echo "Missing $APP_DIR/.env - copy it up from the laptop first." >&2
    exit 1
fi

echo "== System packages"
sudo apt-get update -q
sudo apt-get install -y -q curl ca-certificates

# 1 GB swap: a t3.micro has 1 GB of RAM, and pip installing pandas/numpy
# alongside a running bot can briefly need more.
if ! swapon --show | grep -q /swapfile; then
    echo "== Adding 1 GB swap"
    sudo fallocate -l 1G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

echo "== Python 3.13 + requirements"
if ! command -v uv >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/uv" ]; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi
UV="$HOME/.local/bin/uv"
"$UV" python install 3.13
[ -d venv ] || "$UV" venv --python 3.13 venv
"$UV" pip install --python venv/bin/python -r requirements.txt

echo "== Unit tests"
venv/bin/python -m unittest discover -s tests -t .

echo "== CloudWatch agent"
if [ ! -x /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl ]; then
    curl -LsSf -o /tmp/amazon-cloudwatch-agent.deb \
        https://amazoncloudwatch-agent.s3.amazonaws.com/ubuntu/amd64/latest/amazon-cloudwatch-agent.deb
    sudo dpkg -i -E /tmp/amazon-cloudwatch-agent.deb
fi
mkdir -p logs
sudo /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
    -a fetch-config -m ec2 -s -c "file:$APP_DIR/deploy/cloudwatch-agent.json"

echo "== systemd service"
sudo cp deploy/trading-bot.service /etc/systemd/system/trading-bot.service
sudo systemctl daemon-reload
sudo systemctl enable trading-bot

echo
echo "Setup done. The service is enabled (starts on boot) but not started."
echo "Next: venv/bin/python -m src.main --dry-run, then sudo systemctl start trading-bot"
