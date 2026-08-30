# ai-trading-platform
AI-powered stock analysis and paper trading pipeline

## Setup

```
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and set:

- [Finnhub](https://finnhub.io/) key, for live quotes and company news
- [Alpaca](https://alpaca.markets/) paper trading API key/secret, for trade execution

```
FINNHUB_API_KEY=your_finnhub_api_key_here
ALPACA_API_KEY=your_alpaca_paper_api_key_here
ALPACA_SECRET_KEY=your_alpaca_paper_secret_key_here
```

Finnhub's free tier does not include historical price candles for US
stocks, so this system keeps `yfinance` for the multi-day price history
technical indicators need; Finnhub is used only for a real-time price
quote at order time and for company news.

## Run

```
python -m src.main
```

Runs continuously, executing one trading cycle every `RUN_INTERVAL_MINUTES`
(default 30) for as long as the process stays up. Each cycle fetches market
data and technical indicators (moving averages, RSI, MACD, volume ratio,
volatility) plus recent headlines for each ticker configured in
`src/config.py`, closes out any open position that has breached its
stop-loss/take-profit/trailing-stop, and evaluates new signals — but only
during regular U.S. market hours; outside those hours a cycle is a no-op.

## Trading

`src/config.py` defines every risk parameter as well as a single master
`RISK_LEVEL` dial (0.0–1.0) that derives sensible defaults for all of them
via `compute_risk_params()`; any individual parameter can still be
overridden by editing its line directly.

`src/trading/risk.py` defines `TradingSignal` (the shape the AI analysis
layer should produce: `action`, `symbol`, `confidence`, `reasoning`,
`risk_level`) and the risk management rules — minimum confidence,
confidence-based position sizing, max daily trades, max open positions,
daily loss limit, and market hours.

`src/trading/positions.py` monitors open positions every cycle and
automatically exits any that hit their stop-loss, take-profit, or
trailing-stop level.

`src/trading/executor.py` takes an approved `TradingSignal` and executes
it as a market order on Alpaca's paper trading API:

```python
from src.trading.risk import TradingSignal
from src.trading.executor import execute_signal

signal = TradingSignal(
    action="BUY",
    symbol="AAPL",
    confidence=0.82,
    reasoning="RSI oversold bounce with positive earnings coverage",
    risk_level="LOW",
)
result = execute_signal(signal)
```

## Monitoring

`src/utils/monitoring.py` tracks API call volume and rate-limit usage for
Finnhub, Claude token usage, and Alpaca order success/failure; runs a
health check against all three services at the start of every cycle; and
logs a daily summary (API calls, trade outcomes, token usage, warnings/
errors, start-vs-end portfolio value) once per trading day.
