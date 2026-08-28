import logging
from datetime import datetime, time
from zoneinfo import ZoneInfo

from src.analysis.technical import calculate_indicators
from src.config import LOG_LEVEL, STOCKS
from src.data.market import get_market_data
from src.data.news import get_stock_news


EASTERN_TIMEZONE = ZoneInfo("America/New_York")


def setup_logging() -> None:
    """
    Configure application-wide Python logging.

    Returns:
        None.
    """
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def is_market_hours() -> bool:
    """
    Check whether the current time falls within regular U.S. market hours.

    Returns:
        True when the current time is Monday through Friday between
        9:30 AM and 4:00 PM Eastern Time; otherwise False.
    """
    now = datetime.now(EASTERN_TIMEZONE)

    if now.weekday() >= 5:
        return False

    market_open = time(9, 30)
    market_close = time(16, 0)

    return market_open <= now.time() <= market_close


def analyze_stock(ticker: str) -> None:
    """
    Retrieve market data, calculate technical indicators, and pull
    recent news for a stock.

    Args:
        ticker: Stock ticker symbol.

    Returns:
        None.
    """
    logger = logging.getLogger(__name__)

    try:
        data = get_market_data(ticker)
        indicators = calculate_indicators(data)

        latest_price = data["Close"].iloc[-1]

        logger.info(
            "%s | Price: $%.2f | 20MA: $%.2f | 50MA: $%.2f | "
            "RSI: %.2f | MACD: %.2f | Volume Ratio: %.2f | "
            "Volatility: %.2f",
            ticker,
            latest_price,
            indicators["moving_average_20"],
            indicators["moving_average_50"],
            indicators["rsi"],
            indicators["macd"],
            indicators["volume_ratio"],
            indicators["volatility"],
        )

    except Exception as error:
        logger.error(
            "Unable to analyze %s: %s",
            ticker,
            error,
        )
        return

    try:
        articles = get_stock_news(ticker)

        if articles:
            logger.info(
                "%s | Recent headlines: %s",
                ticker,
                " || ".join(article["title"] for article in articles),
            )
        else:
            logger.info("%s | No recent news found.", ticker)

    except Exception as error:
        logger.warning(
            "Unable to retrieve news for %s: %s",
            ticker,
            error,
        )


def run() -> None:
    """
    Run technical analysis for all configured stocks.

    Returns:
        None.
    """
    logger = logging.getLogger(__name__)

    if not is_market_hours():
        logger.info("Market is currently closed. Exiting.")
        return

    for ticker in STOCKS:
        analyze_stock(ticker)


if __name__ == "__main__":
    setup_logging()
    run()