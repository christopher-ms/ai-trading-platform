# Project Log

The story of this project step by step: what was built, in what order, and why. **Add a new entry to the bottom whenever something meaningful changes** (a feature, a config change, a milestone, a decision). For the detail on individual bugs and test runs, see [`ISSUES.md`](ISSUES.md). For how the code works today, see [`../CLAUDE.md`](../CLAUDE.md).

## What this is

An AI-assisted stock trading bot that paper-trades on Alpaca (simulated money, $100k starting balance; there is no live-money path in the code). Every 30 minutes during market hours, it goes through 12 tickers (AAPL, MSFT, NVDA, AMZN, META, GOOGL, TSLA, AMD, JPM, V, SPY, QQQ). For each one it pulls price history and news, computes technical indicators, asks Claude for a BUY/SELL/HOLD signal with a confidence score, and places an order only if the signal clears a set of risk checks. It also watches open positions for stop-loss, take-profit, and trailing-stop exits.

## Current status

_Last updated: 2026-10-12_

- **Working, verified for real:** data, indicators, news, Claude signals, the risk gate, the full **buy** path (handmade order → Alpaca fill), the **sell/exit** path (`_close_position` → Alpaca fill), position monitoring on a live position, and the zero-trade-streak warning.
- **Positions:** none. All cash, $100,002.65 (the test trade made +$2.65).
- **Running on AWS EC2** as a persistent service since 2026-10-12 (step 31), one cycle every 30 minutes (`RUN_INTERVAL_MINUTES`, confirmed). The dry run on the server passed.
- **Not yet verified:** a SELL signal from Claude, an exit triggered automatically by a stop or target, the loss-limit halt, the trade caps, DynamoDB restoring state across a real restart.
- **AWS:** account set up and **DynamoDB connected** (2026-10-10, step 27; `DYNAMODB_ENABLED=true`, us-east-1). **EC2 server launched** with an IAM instance role, code deployed, bot running (step 31). **CloudWatch log shipping is not set up yet**, so logs are only on the server (`logs/trading.log`, `journalctl -u trading-bot`).
- **Tests:** 63 unit tests for the risk logic, all passing (step 29). Run them with `venv/Scripts/python.exe -m unittest discover -s tests -t . -v`.
- **New, not yet run with a real position:** a backup stop order at Alpaca on every long position (step 26).
- **Known open issues:** CloudWatch log shipping not set up; the laptop copy must stay stopped (two copies would double-trade) and its sleep setting still needs restoring; no natural signal has reached 0.70 yet (highest so far 0.62, ISSUES #13).
- **Next up (roadmap agreed 2026-10-09):**
  1. ~~**Weekend of Oct 10–11: move fully to AWS.**~~ **Done 2026-10-12 except CloudWatch logs** (step 31). EC2/Lightsail server running the loop as an auto-restarting service, DynamoDB persistence on, logs shipped to CloudWatch, plus a backup stop order at Alpaca on every entry so stops hold even if the server dies. The user creates the AWS account, IAM user and billing alert; Claude does the rest. Then turn off the laptop copy and restore the laptop's sleep setting (idle sleep was 5 min on AC and battery).
  2. **Week of Oct 12: watch it run live on AWS** with a daily log review. If it trades, confirm stops and targets fire on their own. If nothing reaches 0.70 after several sessions, investigate the threshold, prompt and indicators using real data.
  3. ~~**In parallel: automated tests for the risk logic**~~ **Done 2026-10-12** (step 29). (stop-loss, take-profit, trailing stop, loss-limit halt, trade and position caps) using fake prices, so the safety features are proven without waiting for the market to trigger them.
  4. **Then several weeks of paper results** before judging the strategy (win rate, average gain vs. loss, vs. buy-and-hold SPY). Real money isn't on the table until then, and the code has no live-money path anyway.

---

## Phase 1: Foundation (Aug 25–26, 2026)

1. **Repo created** (`27d41d9`, Aug 25): `.gitignore` and a README.
2. **Python environment** (`938e954`, Aug 26): virtualenv and `requirements.txt`.
3. **Market data module** (`038e0bc`): `src/data/market.py` pulls daily price history from yfinance; `src/config.py` holds settings.
4. **Technical indicators** (`3d77a5f`): `src/analysis/technical.py` computes the 20/50-day moving averages, RSI, MACD, volume ratio, and volatility.

## Phase 2: The trading pipeline (Aug 28, 2026)

5. **News** (`3d4df63`): headline fetching (originally NewsAPI, later replaced by Finnhub in step 8). Also filled onboarding gaps in the README and `.env.example`.
6. **Paper trading engine and risk management** (`d5ffa91`):
   - `src/trading/executor.py` places market orders on Alpaca's paper account.
   - `src/trading/risk.py` decides whether a signal may trade: market hours, confidence threshold, daily trade cap, open-position cap, and position sizing.
   - `src/utils/market_hours.py` provides the NYSE hours check.
   - An early AAPL test order that day was canceled before reaching the market.
7. **Claude signal generation** (`50be05e`): `src/analysis/signals.py` sends indicators and headlines to Claude and gets back a structured BUY/SELL/HOLD signal with confidence, reasoning, and a risk level. This was the moment the pipeline became end to end.

## Phase 3: Hardening (Aug 29–30, 2026)

8. **Finnhub, monitoring, health checks, position management** (`cc48c01`), the biggest single change:
   - Finnhub replaced NewsAPI for news and added live quotes, with rate-limit backoff. yfinance stayed for history, because Finnhub's history endpoint is paid-only.
   - `src/trading/positions.py` added stop-loss, take-profit, and trailing-stop exits.
   - `src/utils/monitoring.py` added API-call tracking, Claude token usage, early warnings, and a daily summary.
   - `config.py` gained `RISK_LEVEL`, a single 0–1 dial that sets all the risk parameters.
   - `CLAUDE.md` was created.
9. **DynamoDB persistence** (`83a0811`): risk state, trailing stops, and monitoring counters are written to AWS so a restart doesn't wipe them, plus an append-only trade-history table. It's built, but it isn't connected to AWS yet.

## Phase 4: Review and fixes (Sep 2 – Oct 4, 2026)

10. **Health-check ordering and news label** (`f19d5a1`, Sep 2): a Finnhub or Anthropic outage no longer suspends stop-loss protection, and the news lookback label now matches reality. (ISSUES #2, #3)
11. **Full audit** (Oct 4): ran every integration for real. Confirmed the Alpaca paper account ($100k), yfinance, the indicators, and the DynamoDB graceful fallback. Found that the Finnhub and Anthropic keys were blank in `.env` (ISSUES #11). The user filled them in.
12. **Safety fixes** (`31b04a9`): trailing stops are kept after a failed exit, pending orders count toward the daily cap, holidays and early closes are respected via Alpaca's calendar, Claude's token budget was raised, and the risk checks short-circuit properly. (ISSUES #4–#8)
13. **File logging** (`db02538`): a rotating `logs/trading.log` alongside console output.
14. **First real end-to-end analysis** (Oct 4, Sunday): `analyze_stock('AAPL')` hit every live API; Claude said HOLD 0.45.
15. **NaN RSI fix, `risk_level` documented as advisory, IAM policy** (`1fd488e`). (ISSUES #9, #10)

## Phase 5: First live market-hours runs (Oct 9, 2026)

16. **First full cycle during market hours** (13:11 ET): all 12 tickers analyzed cleanly; no orders, because the best signal was TSLA BUY at 0.62 against the 0.70 gate. Spotted that the volume ratio read low on every ticker.
17. **Volume-ratio fix and `--once` flag** (`7d7eb5e`): the volume ratio now uses the last completed trading day instead of today's partial one. Added `python -m src.main --once` for manual single-cycle runs. (ISSUES #12, #13)
18. **Loop run, 13:26 to about 14:10**: volume ratios normal now, confidences slightly up, still nothing at or above 0.70. The loop was stopped.
19. **First real order** (14:15 ET): a handmade BUY SPY at confidence 0.90 went through the real executor. Risk approved it, it was sized to 12 shares (the $10k cap), and **Alpaca filled it in under a second at $778.49**. Confirmed independently on Alpaca.
20. **Documentation** (`5ae63bb`): created `docs/ISSUES.md` (every issue and every test run) and this project log.
21. **Loop restarted** (14:21 ET) to protect the SPY position and keep looking for signals until the close.
22. **Laptop power** (about 15:00 ET): found the laptop was set to sleep after 5 minutes idle and running on battery. Turned idle sleep off. The power log later showed Windows still entering Modern Standby whenever the screen turned off, which delayed one loop cycle by 24 minutes (ISSUES #16). This confirmed the laptop isn't a reliable host.
23. **Decision: move fully to AWS.** Not just DynamoDB, but run the bot itself on an always-on AWS server, so it runs with the laptop off. Task Scheduler was considered and skipped, since it only helps while the laptop is awake.
24. **First real sell** (15:45 ET): the SPY test position was closed on a schedule through the bot's own exit path. Alpaca filled 12 @ $778.71; the round trip made +$2.65. Closed rather than held over the weekend, both to test the sell side and to start the AWS move with nothing at risk.
25. **End of day 1** (16:05 ET): the loop ran 3 more cycles with no errors and no qualifying signals (highest 0.60), and the zero-trade warning fired as designed. Loop stopped. Account all cash at $100,002.65.

## Phase 6: Moving to AWS (Oct 10–11, 2026)

26. **Backup stop orders at Alpaca** (Oct 10): new `src/trading/backup_stops.py`. Every long position gets a resting GTC stop order at Alpaca at the bot's own effective stop level, so the stop-loss holds even when the bot isn't running. `positions.py` keeps it in sync every cycle (moves it to breakeven when the trailing stop activates, resizes it, cancels leftovers), and `executor.py` places it right after a BUY fills. Every sell cancels it first, since a resting stop holds the shares. Tested against a fake Alpaca client; not yet run on a real position. (ISSUES #14)
27. **DynamoDB connected** (Oct 10): the user set up AWS and created the four tables. Verified that their schemas match `scripts/setup_dynamodb.py`, turned persistence on in `.env` (`DYNAMODB_ENABLED=true`, `AWS_REGION=us-east-1`), and did a real save/read/delete round trip on every table.
28. **`--dry-run` flag** (Oct 10): `python -m src.main --dry-run` runs one full cycle at any hour (real data, news, Claude, risk and sizing) but sends no orders and writes nothing to DynamoDB. It logs what it would have done instead. Chosen over a requested `--force-run` (skip the hours check, still trade). That flag would either have done nothing, since `risk.py` has its own hours check, or, with that check bypassed too, queued real market orders to fill at Monday's open on Friday's data. First dry run: all 12 tickers analyzed, no side effects.
29. **Automated tests for the risk logic** (Oct 12): roadmap item 3. 63 stdlib `unittest` tests in `tests/` using fake prices and a fake Alpaca client: every `risk.py` gate and the sizing tiers, `positions.py`'s stop-loss, take-profit and trailing stop (including what happens when an exit is rejected, errors or stays pending), `backup_stops.py`, and the `RISK_LEVEL` dial. All pass; no bugs found. The safety features are now proven in logic without waiting for the market to trigger them.
30. **EC2 deploy kit** (Oct 12): roadmap item 1, the server half. `deploy/` holds a systemd unit (`Restart=always`), a server setup script (Python 3.13 via uv, requirements, unit tests, CloudWatch agent), a laptop-side push script (`git archive HEAD` + `.env` over scp), a CloudWatch agent config (ships `logs/trading.log`, 90-day retention), and an instance-role IAM policy (the four tables plus `/trading-bot/*` logs), so no AWS keys live on the server. Plan: Ubuntu 24.04 on a t3.micro in us-east-1. Runbook in `docs/DEPLOY.md`. Not launched yet.
31. **Bot deployed to AWS** (Oct 12, done by the user): DynamoDB tables in place, EC2 instance launched with an IAM instance role, code deployed, a `--dry-run` on the server confirmed the full pipeline works, and the bot now runs as a persistent service. Run interval confirmed at 30 minutes. **CloudWatch log shipping was not set up yet** (`deploy/cloudwatch-agent.json` is ready for it). This takes the bot off the laptop (ISSUES #16).
