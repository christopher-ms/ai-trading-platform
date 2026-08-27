import logging
from datetime import datetime, time
from zoneinfo import ZoneInfo

from src.config import LOG_LEVEL, STOCKS
from src.data.market import get_market_data


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


def run() -> None:
    """
    Retrieve market data for all configured stocks.

    Returns:
        None.
    """
    logger = logging.getLogger(__name__)

    if not is_market_hours():
        logger.info("Market is currently closed. Exiting.")
        return

    for ticker in STOCKS:
        try:
            data = get_market_data(ticker)

            latest_price = data["Close"].iloc[-1]

            logger.info(
                "%s latest closing price: $%.2f",
                ticker,
                latest_price,
            )

        except Exception as error:
            logger.error(
                "Unable to process %s: %s",
                ticker,
                error,
            )


if __name__ == "__main__":
    setup_logging()
    run()