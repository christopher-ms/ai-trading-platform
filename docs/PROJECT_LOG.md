# Project Log

The story of this project step by step: what was built, in what order, and why. **Add a new entry to the bottom whenever something meaningful changes** (a feature, a config change, a milestone, a decision). For the detail on individual bugs and test runs, see [`ISSUES.md`](ISSUES.md). For how the code works today, see [`../CLAUDE.md`](../CLAUDE.md).

## What this is

An AI-assisted stock trading bot that paper-trades on Alpaca (simulated money, $100k starting balance; there is no live-money path in the code). Every 30 minutes during market hours, it goes through 12 tickers (AAPL, MSFT, NVDA, AMZN, META, GOOGL, TSLA, AMD, JPM, V, SPY, QQQ). For each one it pulls price history and news, computes technical indicators, asks Claude for a BUY/SELL/HOLD signal with a confidence score, and places an order only if the signal clears a set of risk checks. It also watches open positions for stop-loss, take-profit, and trailing-stop exits.

## Current status

_Last updated: 2026-10-09 (after the close)_

- **Working, verified for real:** data, indicators, news, Claude signals, the risk gate, the full **buy** path (handmade order → Alpaca fill), the **sell/exit** path (`_close_position` → Alpaca fill), position monitoring on a live position, and the zero-trade-streak warning.
- **Positions:** none. All cash, $100,002.65 (the test trade made +$2.65).
- **Nothing is running.** The loop was stopped after the close on 2026-10-09.
- **Not yet verified:** a SELL signal from Claude, an exit triggered automatically by a stop or target, the loss-limit halt, the trade caps, DynamoDB.
- **Not connected:** AWS (`DYNAMODB_ENABLED=false`).
- **Known open issues:** the laptop can't run the bot reliably (standby throttles it, and positions are unprotected when it isn't running: ISSUES #14, #16); no natural signal has reached 0.70 yet (highest so far 0.62, ISSUES #13).
- **Next up:** move the bot fully to AWS this weekend (EC2/Lightsail server + DynamoDB + CloudWatch Logs) so it runs with the laptop off, plus a backup stop order at Alpaca on every entry. Monday 2026-10-12 is the first live day on AWS.

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
