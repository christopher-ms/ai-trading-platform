import os

from dotenv import load_dotenv


load_dotenv()


STOCKS = ["AAPL", "MSFT", "NVDA", "AMZN"]

HISTORY_PERIOD = "3mo"

LOG_LEVEL = "INFO"

NEWS_API_KEY = os.getenv("NEWS_API_KEY")

ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")

ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")

MIN_TRADE_CONFIDENCE = 0.7

MAX_POSITION_SIZE_PCT = 0.10

MAX_OPEN_POSITIONS = 5
