import logging

import yfinance as yf
import pandas as pd

from src.config import HISTORY_PERIOD

logger = logging.getLogger(__name__)


def get_market_data(ticker: str) -> pd.DataFrame:
    """
    Retrieve historical market data for a stock.

    Args:
        ticker: Stock ticker symbol, such as AAPL.

    Returns:
        A pandas DataFrame containing historical market data.

    Raises:
        RuntimeError: If market data cannot be retrieved.
        ValueError: If the returned data is empty.
    """
    try:
        logger.info("Fetching market data for %s", ticker)

        stock = yf.Ticker(ticker)
        data = stock.history(period=HISTORY_PERIOD)

        if data.empty:
            raise ValueError(f"No market data returned for {ticker}")

        logger.info(
            "Retrieved %d rows of market data for %s",
            len(data),
            ticker,
        )

        return data

    except Exception as error:
        logger.error(
            "Failed to retrieve market data for %s: %s",
            ticker,
            error,
        )
        raise RuntimeError(
            f"Failed to retrieve market data for {ticker}"
        ) from error