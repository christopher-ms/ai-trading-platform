# ai-trading-platform
AI-powered stock analysis and paper trading pipeline

## Setup

```
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and set:

- [NewsAPI](https://newsapi.org/) key, for headlines
- [Alpaca](https://alpaca.markets/) paper trading API key/secret, for trade execution

```
NEWS_API_KEY=your_newsapi_org_key_here
ALPACA_API_KEY=your_alpaca_paper_api_key_here
ALPACA_SECRET_KEY=your_alpaca_paper_secret_key_here
```

## Run

```
python -m src.main
```

Fetches market data and technical indicators (moving averages, RSI, MACD,
volume ratio, volatility) plus recent headlines for each ticker configured
in `src/config.py`. Only runs during regular U.S. market hours.

## Trading

`src/trading/risk.py` defines `TradingSignal` (the shape the AI analysis
layer should produce: `action`, `symbol`, `confidence`, `reasoning`,
`risk_level`) and the risk management rules — minimum confidence, max
position size, max open positions, market hours.

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
