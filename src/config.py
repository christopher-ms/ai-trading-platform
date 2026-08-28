import os

from dotenv import load_dotenv


load_dotenv()


STOCKS = ["AAPL", "MSFT", "NVDA", "AMZN"]

HISTORY_PERIOD = "3mo"

LOG_LEVEL = "INFO"

NEWS_API_KEY = os.getenv("NEWS_API_KEY")
