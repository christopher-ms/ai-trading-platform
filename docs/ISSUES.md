# Issues & Test History

A running record of every real problem this system has hit, how it was fixed, and every real test run, so nobody has to rediscover any of it. **Add to this whenever you hit a new issue or run a new test.**

## Issues

Numbered oldest first. Each says what went wrong, why it mattered, and how it was fixed (or why it's still open).

1. **Finnhub `/stock/candle` is paid-only for US equities.** Free keys get "You don't have access to this resource." Fix: historical bars stay on yfinance; Finnhub only does `/quote` and `/company-news` (both free).
2. **Health-check ordering suspended stop-losses.** A Finnhub or Anthropic outage used to skip the whole cycle, including position protection. Fix: Alpaca health is checked first and alone; Finnhub/Anthropic only gate the search for new trades (see "Health-check ordering").
3. **News window mislabeled.** The prompt hardcoded a lookback that didn't match `get_stock_news()`'s actual filter. Fix: the prompt reads `NEWS_LOOKBACK_HOURS` directly.
4. **Trailing stop reverted after a failed exit.** The breakeven flag was cleared before the exit order was confirmed, so a rejected or pending exit dropped the stop back to `-STOP_LOSS_PCT`. Fix: clear it only on a confirmed `FILLED` exit.
5. **PENDING orders weren't counted toward `MAX_DAILY_TRADES`.** The cap could be exceeded. Fix: PENDING counts as well as FILLED; only `canceled` doesn't.
6. **Holidays and early closes weren't respected.** The local clock check knows nothing about the exchange calendar. Fix: `is_market_open_per_alpaca()` is the real gate, falling back to the local check if Alpaca's clock call fails.
7. **Claude output could be truncated.** Opus 5's adaptive thinking draws from `max_tokens`, so 4096 risked cutting off the structured output. Fix: `max_tokens=16000` with `effort: "low"`.
8. **Risk checks didn't short-circuit.** `check_daily_loss_limit` ran (and could latch `halted_for_loss`) even on signals already rejected for being out of hours. Fix: each check is a lambda, so it runs only if every earlier check passed.
9. **NaN RSI on flat-price data.** 0/0 average gain/loss produced NaN, which reached Claude as "RSI: nan". Fix: `calculate_indicators()` raises on any non-finite value, and the ticker is skipped for that cycle.
10. **`signal.risk_level` looked like it affected sizing but doesn't.** Fix: documented as advisory only, and deliberately left unwired until there's trade history to validate it against.
11. **API keys were present but empty in `.env`.** Every cycle failed the health checks, so no ticker was ever analyzed, and it looked like "no signals" rather than an error. Fix: filled in the keys. When a run is silent, check the keys are non-empty before suspecting the code.
12. **(2026-10-09) Calling `run()` directly logged almost nothing.** Logging is only configured in `setup_logging()`, which only the `__main__` block calls. Fix: added `python -m src.main --once` for manual single cycles.
13. **(2026-10-09) The volume ratio was biased low on every ticker, every cycle.** During market hours yfinance's last daily bar is today's partial session, so partial-day volume was compared against full-day averages (0.24–0.81x at 1pm, near zero in the morning). Claude cited "low volume, no conviction" in nearly every HOLD. Fix: `calculate_volume_analysis()` drops the in-progress bar, which gave 0.73–1.44x on the same data. Confidences rose slightly, but on the first market-hours day nothing reached the 0.70 gate (best: TSLA/QQQ BUY at 0.60–0.62). Watch whether 0.70 is ever reached across several sessions before tuning the threshold or prompt.
14. **(2026-10-09) Open positions are unprotected whenever the loop isn't running.** Stop-loss, take-profit, and trailing-stop exits are all checked by `check_open_positions()` inside `run()`; nothing is placed at Alpaca's side. So stopping the loop, closing the terminal, a crash, or a reboot leaves every open position with no stop at all. Also, the loop is still worth running on days when no signal reaches 0.70, because it's the only thing protecting existing positions. **Status: open.** Planned fix: auto-restart via Windows Task Scheduler. A possible extra safety net is a server-side stop order at Alpaca on every entry.
15. **(2026-10-09) `check_open_positions()` is silent when nothing triggers.** It only logs when it closes a position, so a healthy check and a check that never ran look identical in the log. **Status: open, minor.** Worth a one-line per-position status log at INFO.

## Test history

| Date (ET) | What was run | Result |
|---|---|---|
| 2026-10-04 (Sun) | `analyze_stock('AAPL')` only, market closed | First real end-to-end call. Finnhub returned 0 articles; Claude said HOLD 0.45 (1,153 in / 142 out tokens); risk gate rejected it for being outside market hours. |
| 2026-10-09 13:11 | First full `run()` cycle during market hours (×2; the first had no logging, see issue 12) | All health checks passed and all 12 tickers were analyzed. 11 HOLD (0.45–0.60), TSLA BUY 0.62, rejected below 0.70. No orders. Found the volume-ratio bias (issue 13). |
| 2026-10-09 13:26 | `run_forever()` first cycle, after the volume fix | Volume ratios normal (0.73–1.44x). Confidences slightly higher but still ≤0.60; TSLA and QQQ BUY at 0.60 both rejected. No orders. Loop stopped ~14:10 since nothing could clear 0.70. |
| 2026-10-09 14:15 | **Handmade BUY SPY at confidence 0.90** through the real `execute_signal()` | First real order. Risk approved a $10k max (10% tier); priced live and sized to 12 shares; Alpaca **FILLED in under 1s @ $778.49** (order `35188d72-dfec-4bdd-aeba-acb5e7ac17e6`). Confirmed independently on Alpaca: position 12 SPY, cash $100,000 → $90,658. `check_open_positions()` ran against it with no errors and no action (−5% stop ≈ $739.57, +15% target ≈ $895.26). |

**Never yet exercised for real:** a SELL signal, any automatic exit (stop-loss / take-profit / trailing stop), the daily loss-limit halt, the `MAX_DAILY_TRADES` / `MAX_OPEN_POSITIONS` caps, and the DynamoDB persistence layer (AWS not connected).
