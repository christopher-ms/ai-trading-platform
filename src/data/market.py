import logging

import yfinance as yf
import pandas as pd

from src.config import HISTORY_PERIOD
from src.data.finnhub_client import get_quote

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


def get_live_price(ticker: str, fallback_price: float | None = None) -> float:
    """
    Get the current real-time price for a stock, for order sizing.

    Tries Finnhub's /quote endpoint first, since it reflects the current
    moment rather than the last completed bar. If that fails for any
    reason (missing key, network error, exhausted rate-limit retries),
    falls back to yfinance's last close - either a price the caller
    already has on hand (e.g. from get_market_data's history, avoiding a
    second network call), or a fresh yfinance lookup if none was given.

    Args:
        ticker: Stock ticker symbol.
        fallback_price: A recent price to use if Finnhub fails, if the
            caller already has one. If not provided, a fresh yfinance
            lookup is made instead.

    Returns:
        The current price to use for order sizing.

    Raises:
        RuntimeError: If Finnhub fails and no fallback price could be
            obtained from yfinance either.
    """
    try:
        quote = get_quote(ticker)
        price = float(quote["c"])

        if price <= 0:
            raise ValueError(f"Finnhub returned a non-positive price: {price}")

        return price

    except Exception as error:
        logger.warning(
            "Finnhub quote failed for %s (%s); falling back to yfinance last close.",
            ticker,
            error,
        )

        if fallback_price is not None:
            return fallback_price

        try:
            data = yf.Ticker(ticker).history(period="1d")
            if data.empty:
                raise ValueError(f"No fallback price data returned for {ticker}")
            return float(data["Close"].iloc[-1])
        except Exception as fallback_error:
            raise RuntimeError(
                f"Unable to get a live price for {ticker}: Finnhub failed "
                f"({error}) and the yfinance fallback also failed "
                f"({fallback_error})."
            ) from fallback_error