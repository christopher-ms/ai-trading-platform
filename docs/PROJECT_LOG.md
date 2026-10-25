# Project Log

The story of this project step by step: what was built, in what order, and why. **Add a new entry to the bottom whenever something meaningful changes** (a feature, a config change, a milestone, a decision). For the detail on individual bugs and test runs, see [`ISSUES.md`](ISSUES.md). For how the code works today, see [`../CLAUDE.md`](../CLAUDE.md).

## What this is

An AI-assisted stock trading bot that paper-trades on Alpaca (simulated money, $100k starting balance; there is no live-money path in the code). Every 60 minutes during market hours, it goes through 14 tickers (AAPL, MSFT, NVDA, AMZN, META, GOOGL, TSLA, JPM, V, SPY, XOM, UNH, COST, CAT). For each one it pulls price history and news, computes technical indicators, asks Claude for a BUY/SELL/HOLD signal with a confidence score, and places an order only if the signal clears a set of risk checks. It also watches open positions for stop-loss, take-profit, and trailing-stop exits.

## Current status

_Last updated: 2026-10-21_

- **Working, verified for real:** data, indicators, news, Claude signals, the risk gate, the full **buy** path (handmade order → Alpaca fill, and the first autonomous trade on 2026-10-13), the backup stop on a real position, the **sell/exit** path (`_close_position` → Alpaca fill), position monitoring on a live position, and the zero-trade-streak warning.
- **Positions:** none. The META position (step 33) was closed by its backup stop at Alpaca on 2026-10-15 (step 36). Equity $99,752.99 (−0.25% since start).
- **Running on AWS EC2** as a persistent service since 2026-10-12 (step 31), one cycle every 30 minutes until step 32 changed it to 60 (`RUN_INTERVAL_MINUTES`). The dry run on the server passed. Signals now come from Claude Haiku 5.5 (step 32), redeployed and running since 2026-10-13 21:39 ET with 0 restarts. The new 14-ticker list (step 38) has been redeployed according to the user, but this hasn't been checked in the server logs yet (step 39).
- **Not yet verified:** a SELL signal from Claude, an exit triggered by the bot's own stop or target (the broker-side backup stop has fired, step 36), the loss-limit halt, the trade caps, DynamoDB restoring state across a real restart.
- **AWS:** account set up and **DynamoDB connected** (2026-10-10, step 27; `DYNAMODB_ENABLED=true`, us-east-1). **EC2 server launched** with an IAM instance role, code deployed, bot running (step 31). **CloudWatch log shipping works** (confirmed 2026-10-17, step 37): `logs/trading.log` arrives in `/trading-bot/trading.log`, kept 90 days. There are no CloudWatch metrics, alarms or dashboards yet.
- **Tests:** 63 unit tests for the risk logic, all passing (step 29). Run them with `venv/Scripts/python.exe -m unittest discover -s tests -t . -v`.
- **Known open issues:** a BUY on a stock already held is rejected by Alpaca as a wash trade (ISSUES #18; harmless, deferred); the laptop copy must stay stopped (two copies would double-trade) and its sleep setting still needs restoring; on Haiku, nothing has reached 0.70 yet (highest 0.62 on its first day, ISSUES #13).
- **Next up (roadmap agreed 2026-10-09):**
  1. ~~**Weekend of Oct 10–11: move fully to AWS.**~~ **Done 2026-10-12** (step 31; CloudWatch logs confirmed working in step 37). EC2/Lightsail server running the loop as an auto-restarting service, DynamoDB persistence on, logs shipped to CloudWatch, plus a backup stop order at Alpaca on every entry so stops hold even if the server dies. The user creates the AWS account, IAM user and billing alert; Claude does the rest. Then turn off the laptop copy and restore the laptop's sleep setting (idle sleep was 5 min on AC and battery).
  2. **In progress, Oct 14–21: watch it run live on AWS without changes** (decided 2026-10-14: no code changes or redeploys for at least a week unless something is badly wrong; the user chose to change the ticker list during it, step 38; then review Haiku's confidence levels, the META outcome, ISSUES #18 and a week of API cost), with a read-only daily log review. If it trades, confirm stops and targets fire on their own. If nothing reaches 0.70 after several sessions, investigate the threshold, prompt and indicators using real data.
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

## Phase 7: Running on AWS (Oct 13, 2026)

32. **Cheaper, less frequent signals** (Oct 13): `signals.py` switched from `claude-opus-5` to `claude-haiku-5-5` (same `effort: low`, structured output and `max_tokens`), and `RUN_INTERVAL_MINUTES` raised from 30 to 60. This halves the number of cycles per day and cuts the per-call Claude price sharply. Haiku may score confidence differently from Opus, so watch whether signals still approach the 0.70 gate (ISSUES #13). Takes effect on the server only after a redeploy and service restart.
33. **First autonomous trade** (Oct 13, 13:03 ET, still on Opus): Claude gave META BUY above the 0.70 gate. Alpaca filled 6 @ $762.89, and the backup stop was placed automatically at $724.75. That's the first time it has worked on a real position. An hour later a second META BUY (0.71) was approved by risk but rejected by Alpaca as a "potential wash trade", because the resting backup stop is an opposite-side order (ISSUES #18). It's harmless, since nothing was placed. Day totals: 144 Claude calls (~288k tokens), 158 Finnhub calls, portfolio +0.01%.
34. **First day on Haiku, hourly** (Oct 14): the step 32 redeploy was confirmed live (service restarted 21:39 ET Oct 13). 7 clean cycles, 84 Claude calls (down from 144), no errors. The highest confidence was BUY 0.62, so no trades. META fell to $729.19 (−4.4%), about 0.6% above its stop, so the first automatic stop-loss exit may come soon.
35. **Decision: hands-off for a week** (Oct 14): leave the bot running unchanged until at least Oct 21 to collect consistent data on Haiku, with only a daily read-only check. Step in only for a real problem: crashes or restarts, repeated Alpaca errors on tickers we don't hold, a stop not firing, or a loss-limit halt. ISSUES #18 is deferred to that review.
36. **First automatic stop-loss exit** (Oct 15): META opened below its stop. The backup stop at Alpaca ($724.75) fired at 9:33 ET, before the bot's first cycle of the day, and filled 6 @ $721.33. That's about $3.40/share of gap slippage past the stop, for a loss of about −$249 on the trade. This is the first real proof that the broker-side backup stop works (ISSUES #14). Account at $99,752.99, flat cash, as of Oct 16.
37. **Read-only check of Finnhub and CloudWatch** (Oct 17): over Oct 13–16, Finnhub news worked on every call except one burst on Oct 16 at 9:46 ET (502 "Wrong json format" for JPM, V, SPY, QQQ, handled as "no headlines"; the next cycle was clean). No 429s, and live quotes never fell back to yfinance. QQQ averaged 0.2 headlines per cycle, against 4–5 for single stocks, because Finnhub company-news barely covers ETFs. CloudWatch log shipping was found to be working, although steps 30–31 said it wasn't set up. It holds only the raw log: no metric filters, alarms or dashboards, and `send_metric_to_cloudwatch()` still only writes log lines.
38. **Ticker list changed** (Oct 17, the user's decision, made during the hands-off week): removed QQQ (overlaps the single tech names and gets almost no news) and AMD (overlaps NVDA); added XOM, UNH, COST and CAT to spread across energy, healthcare, consumer staples and industrials. 14 tickers now, so about 98 Claude calls a day instead of 84. Takes effect on the server only after a redeploy and service restart.
39. **New ticker list committed and pushed** (Oct 21): the step 38 change to `STOCKS`, along with the "12 tickers" → "14 tickers" updates in comments and docs, was still uncommitted. The user believes the server has been redeployed with it. To confirm, check that the next cycle in `journalctl -u trading-bot` or CloudWatch shows XOM/UNH/COST/CAT and no QQQ/AMD.
40. **Read-only CloudWatch Logs policy for the laptop's IAM user** (Oct 21): added `scripts/cloudwatch-logs-read-iam-policy.json`, kept separate from the DynamoDB policy. It allows `GetLogEvents`, `FilterLogEvents` and `DescribeLogStreams` on `/trading-bot/*`, plus account-wide `DescribeLogGroups` (AWS can't scope that action to specific groups; it returns group names only). It's for tailing the server log from the laptop without SSH: `aws logs tail /trading-bot/trading.log --since 1h --region us-east-1`. It still has to be created and attached to the IAM user in the AWS console.
