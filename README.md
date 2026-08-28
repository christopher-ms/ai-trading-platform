# ai-trading-platform
AI-powered stock analysis and paper trading pipeline

## Setup

```
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and set your [NewsAPI](https://newsapi.org/) key:

```
NEWS_API_KEY=your_newsapi_org_key_here
```

## Run

```
python -m src.main
```

Fetches market data and technical indicators (moving averages, RSI, MACD,
volume ratio, volatility) plus recent headlines for each ticker configured
in `src/config.py`. Only runs during regular U.S. market hours.
