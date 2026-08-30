# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Setup (Windows; `venv` already exists at the repo root):

```
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and set `FINNHUB_API_KEY`, `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `ANTHROPIC_API_KEY`.

Run (executes real paper trades against Alpaca during market hours — see Architecture):

```
python -m src.main
```

There is no test suite, linter, or formatter configured in this repo (no `pytest`/`ruff`/`flake8` config, no CI). Verify changes by importing the touched module directly, e.g. `venv/Scripts/python.exe -c "import src.trading.risk"` — the system `python` on PATH will not have `alpaca-py`/`anthropic` installed, only the venv's will.

## Architecture

Pipeline, one full cycle per ticker in `STOCKS` (`src/config.py`), driven by `src/main.py`:

```
market history (yfinance) -> technical indicators -> news (Finnhub company-news)
  -> Claude signal generation -> risk evaluation -> live quote (Finnhub, yfinance fallback) -> Alpaca paper order
```

- `src/main.py`: `run_forever()` is the process entry point — an infinite `time.sleep(RUN_INTERVAL_MINUTES * 60)` loop around `run()`, with no external scheduler dependency. Each `run()` call, in order: rolls `src/utils/monitoring.py`'s daily summary over if a new Eastern trading day has started; no-ops entirely outside regular NYSE hours (`src/utils/market_hours.py`, Mon–Fri 9:30–16:00 America/New_York); runs `monitoring.run_health_checks()` (Finnhub, Alpaca, Anthropic) and skips the *entire* cycle if any is down, rather than failing partway through the ticker loop; otherwise calls `src/trading/positions.py::check_open_positions()` to close any position that has breached stop-loss/take-profit/trailing-stop; then checks the daily loss limit; then, only if trading isn't halted for the day, loops `analyze_stock()` over every ticker. Position monitoring always runs ahead of and independent of the daily-loss-limit gate, so a halted day still protects existing positions — only the search for *new* trades stops.
- `src/analysis/technical.py` + `src/data/market.py` + `src/data/news.py` + `src/data/finnhub_client.py`: pure data-gathering, no trading logic. `market.py::get_market_data()` still pulls historical daily bars from yfinance — Finnhub's `/stock/candle` endpoint has moved behind a paid plan for US equities, so there's no free replacement for the multi-day history `technical.py`'s indicators need. `market.py::get_live_price()` and `news.py::get_stock_news()` both go through `finnhub_client.py`, which wraps the Finnhub SDK with rate-limit-aware exponential backoff (1s/2s/4s) on 429s and logs every call via `monitoring.log_api_call()`. `get_live_price()` falls back to yfinance's last close if Finnhub fails for any reason; `get_stock_news()` has no fallback and just propagates the failure (same as the old NewsAPI integration), which `analyze_stock()` in `main.py` already catches and treats as "no headlines this cycle."
- `src/analysis/signals.py::generate_signal()`: sends indicators + last-4h headlines to Claude (`client.messages.parse` with a pydantic `output_format`, model `claude-opus-5`) and gets back a structured `TradingSignal`. The system prompt is an f-string that reads `MIN_TRADE_CONFIDENCE` from `src/config.py` at import time rather than hardcoding it, so the model's stated execution-gate threshold can never drift from the actual gate in `risk.py`. Every call logs its token usage via `monitoring.log_claude_usage()`; an `anthropic.RateLimitError` is caught, logged clearly, and re-raised so `main.py`'s existing per-ticker exception handling skips just that one stock for this run.
- `src/trading/risk.py`: `evaluate_signal()` runs a fixed, short-circuiting sequence of checks (market hours → daily loss limit → confidence threshold → daily trade count → open position limit) and returns a `RiskDecision`. Position sizing (`get_position_size_pct`) scales `MAX_POSITION_SIZE_PCT` by a confidence bracket from `CONFIDENCE_POSITION_SIZE_TIERS` (higher-confidence signals get a larger fraction). Daily trade count and daily-loss-limit state live in a module-level `_DailyState` singleton, keyed off the Eastern calendar date.
- `src/trading/positions.py::check_open_positions()`: runs every cycle before new signals are evaluated. Tracks per-symbol trailing-stop activation in an in-memory `set`; once a position's unrealized gain hits `TRAILING_STOP_TRIGGER_PCT`, its effective stop moves to breakeven for the life of that position.
- `src/trading/executor.py::execute_signal()`: the single entry point from the AI layer to a real order — runs `evaluate_signal`, prices the order via `market.py::get_live_price()` (Finnhub `/quote`, falling back to yfinance), sizes via `calculate_position_size`, submits a market order to Alpaca (`paper=True` is hardcoded; there is no live-trading path), and polls briefly for fill status. Every outcome (SKIPPED/REJECTED/FILLED/PENDING/ERROR) is returned as a `TradeResult` rather than raised, so one bad signal never kills the loop over tickers.
- Both `executor.py` (signal-driven trades) and `positions.py` (automatic stop-loss/take-profit/trailing exits) call `risk.py::record_trade_executed()` — both count toward the same `MAX_DAILY_TRADES` — and both call `monitoring.record_trade_result()` (every outcome, for the daily summary) and `monitoring.record_alpaca_order_outcome()` (only outcomes from an order actually submitted to Alpaca, for consecutive-failure tracking).
- `src/utils/monitoring.py`: tracks API call volume and Finnhub's rolling calls-per-minute (warning at `FINNHUB_RATE_LIMIT_WARNING_FRACTION` of `FINNHUB_RATE_LIMIT_PER_MINUTE`), Claude token usage per call, and Alpaca order success/failure; runs the three service health checks `main.py::run()` gates on; and logs a full daily summary (API calls per service, trade outcomes, token usage, every warning/error, start-vs-end portfolio value) the first time a new Eastern trading day is detected. `send_metric_to_cloudwatch()` is a structured logging placeholder — every other function in the module routes its metrics through it, so wiring real CloudWatch in later (via Terraform/boto3) is a one-function change.

**`RISK_LEVEL` (`src/config.py`)**: a single 0.0–1.0 dial. `compute_risk_params(risk_level)` piecewise-linearly interpolates `stop_loss_pct`, `take_profit_pct`, `min_confidence`, `max_position_size_pct`, `max_daily_trades`, `daily_loss_limit_pct` across three anchor points each (0.0 / 0.5 / 1.0), since the ranges aren't symmetric around the midpoint. `RISK_LEVEL = 0.5` is the default and reproduces this system's original hand-tuned values exactly. The derived values are assigned to individually-named constants (`STOP_LOSS_PCT`, etc.) that can still be overridden by editing their own line — doing so detaches only that one parameter from `RISK_LEVEL`. `MAX_OPEN_POSITIONS`, `TRAILING_STOP_TRIGGER_PCT`, and the confidence brackets in `CONFIDENCE_POSITION_SIZE_TIERS` are deliberately *not* scaled by `RISK_LEVEL` — they're fixed diversification/tiering rules rather than risk-per-trade dials.

**Gotcha — in-memory state, no persistence**: `_DailyState` (in `risk.py`), `_trailing_stop_active` (in `positions.py`), and `_MonitoringState` (in `monitoring.py`) are all process-local and reset on restart — a mid-day restart silently zeroes the daily trade counter, lifts any loss-limit halt, reverts trailing stops to plain stop-losses, and drops all of that day's monitoring counters (a partial daily summary is lost, not carried over). There is no database in this repo.

**Gotcha — market hours**: outside 9:30–16:00 America/New_York on a weekday, `run()` is a no-op by design. Don't mistake that silence for a bug when testing.

**Gotcha — Finnhub's free tier doesn't include historical candles**: `/stock/candle` for US equities now requires a paid Finnhub plan (confirmed via `finnhubio/Finnhub-API#546`) — a free key gets `{"error": "You don't have access to this resource."}` on every call. That's why `market.py::get_market_data()` still uses yfinance for the multi-day history `technical.py` needs; Finnhub is only used for the single-day `/quote` snapshot and `/company-news`, both of which are free-tier.

**Gotcha — a failed health check skips position protection too, not just new signals**: `monitoring.run_health_checks()` in `main.py::run()` runs *before* `check_open_positions()`, and makes one real call to each of Finnhub, Alpaca, and Anthropic. If any of the three is down, `run()` returns immediately — no stop-loss/take-profit/trailing-stop monitoring, no new signals, nothing — until the next scheduled cycle. This was an explicit request ("skip the run entirely rather than failing mid-cycle") but it's in real tension with this system's other stated principle that position protection runs "ahead of and independent of" every other gate: a Finnhub or Anthropic outage now also suspends protecting positions that are only exposed to Alpaca, even though `check_open_positions()` itself never touches Finnhub or Anthropic. If that tradeoff turns out to be unwanted, the fix is to run the Alpaca health check (and `check_open_positions()`) before gating on Finnhub/Anthropic, and only let those two block new-signal generation.
