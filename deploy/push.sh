#!/usr/bin/env bash
# Copy the committed code and .env from the laptop to the server. Run from
# the repo root in Git Bash:
#
#     bash deploy/push.sh <server-ip> <path-to-key.pem>
#
# Ships `git archive HEAD`, so uncommitted changes are NOT deployed - commit
# first. venv/, logs/ and the server's own .env are left alone except that
# .env is overwritten with the laptop's copy.
set -euo pipefail

if [ $# -ne 2 ]; then
    echo "usage: bash deploy/push.sh <server-ip> <path-to-key.pem>" >&2
    exit 1
fi

HOST="ubuntu@$1"
KEY="$2"
APP_DIR=/home/ubuntu/ai-trading-platform
ARCHIVE=$(mktemp -t trading-bot-XXXXXX.tar.gz)

git archive --format=tar.gz -o "$ARCHIVE" HEAD
echo "Deploying $(git rev-parse --short HEAD) to $1"

scp -i "$KEY" "$ARCHIVE" "$HOST:/tmp/trading-bot.tar.gz"
scp -i "$KEY" .env "$HOST:/tmp/trading-bot.env"
ssh -i "$KEY" "$HOST" "mkdir -p $APP_DIR \
    && tar -xzf /tmp/trading-bot.tar.gz -C $APP_DIR \
    && install -m 600 /tmp/trading-bot.env $APP_DIR/.env \
    && rm /tmp/trading-bot.tar.gz /tmp/trading-bot.env"
rm -f "$ARCHIVE"

echo "Copied. On a first deploy, run: ssh -i $KEY $HOST 'bash $APP_DIR/deploy/setup_server.sh'"
echo "On later deploys: ssh -i $KEY $HOST 'bash $APP_DIR/deploy/setup_server.sh && sudo systemctl restart trading-bot'"
