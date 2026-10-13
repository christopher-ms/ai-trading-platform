# Deploying to AWS (EC2)

The bot runs as a systemd service on one small EC2 instance. systemd restarts it after a crash or a reboot, and its log file is shipped to CloudWatch Logs. DynamoDB access and log shipping both go through an **instance role**, so no AWS access keys live on the server.

Files used (all in `deploy/`):

| File | What it is |
|---|---|
| `ec2-role-policy.json` | IAM policy for the instance role: the four DynamoDB tables, plus writing to the `/trading-bot/*` log groups |
| `push.sh` | Run on the laptop. Copies the committed code (`git archive HEAD`) and `.env` to the server |
| `setup_server.sh` | Run on the server. Installs Python 3.13, the requirements, the CloudWatch agent and the service, and runs the unit tests. Safe to re-run |
| `trading-bot.service` | The systemd unit (`Restart=always`) |
| `cloudwatch-agent.json` | Ships `logs/trading.log` to the log group `/trading-bot/trading.log`, kept for 90 days |

## 1. AWS console (one time)

Region: **us-east-1**, the same region as the DynamoDB tables.

1. **IAM → Policies → Create policy → JSON:** paste `deploy/ec2-role-policy.json`. Name it `trading-bot-ec2`.
2. **IAM → Roles → Create role:** trusted entity *AWS service → EC2*. Attach `trading-bot-ec2`. Name the role `trading-bot-ec2`.
3. **EC2 → Launch instance:**
   - Name: `trading-bot`
   - AMI: **Ubuntu Server 24.04 LTS, 64-bit (x86)**. The setup script assumes Ubuntu, x86 and the `ubuntu` user.
   - Instance type: **t3.micro** (1 GB RAM is enough; the script adds 1 GB of swap)
   - Key pair: create one (`trading-bot`, .pem) and save it somewhere outside the repo, e.g. `~/.ssh/trading-bot.pem`
   - Network: allow SSH **from My IP only**. No other inbound ports: the bot only makes outbound calls.
   - Storage: the default 8 GB gp3 is fine
   - Advanced details → IAM instance profile: `trading-bot-ec2`
4. Note the instance's public IPv4 address. It changes if the instance is stopped and started; attach an Elastic IP if that gets annoying.

## 2. Deploy (from the laptop, Git Bash, repo root)

Commit first, since `push.sh` ships `HEAD` and not the working tree.

```
bash deploy/push.sh <server-ip> ~/.ssh/trading-bot.pem
ssh -i ~/.ssh/trading-bot.pem ubuntu@<server-ip> 'bash ~/ai-trading-platform/deploy/setup_server.sh'
```

## 3. Smoke test, then start

On the server (`ssh -i ~/.ssh/trading-bot.pem ubuntu@<server-ip>`):

```
cd ~/ai-trading-platform
venv/bin/python -m src.main --dry-run      # full cycle, no orders, no DynamoDB writes
sudo systemctl start trading-bot
sudo systemctl status trading-bot
journalctl -u trading-bot -f               # live output (Ctrl+C to stop watching)
```

The dry run should analyze all 12 tickers with no errors. Check that it doesn't log any `DynamoDB |` warnings: those would mean the instance role isn't attached.

**Only one copy of the bot may run at a time.** Make sure nothing is running on the laptop before starting the service (see CLAUDE.md on two processes double-trading).

## Day to day

| Task | Command (on the server) |
|---|---|
| Is it running? | `sudo systemctl status trading-bot` |
| Follow the log | `tail -f ~/ai-trading-platform/logs/trading.log` |
| Stop / start / restart | `sudo systemctl stop\|start\|restart trading-bot` |
| Deploy new code | From the laptop: `bash deploy/push.sh ...`, then on the server `bash deploy/setup_server.sh && sudo systemctl restart trading-bot` |

The same log is in **CloudWatch → Log groups → `/trading-bot/trading.log`**, readable without SSH.

Stopping the bot doesn't leave positions unprotected at the stop-loss level, because each position has a backup stop order resting at Alpaca (`src/trading/backup_stops.py`). Take-profit and trailing-stop activation only happen while the bot runs.
