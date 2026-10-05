import logging
from datetime import datetime, time
from zoneinfo import ZoneInfo


logger = logging.getLogger(__name__)

EASTERN_TIMEZONE = ZoneInfo("America/New_York")


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


def is_market_open_per_alpaca(client) -> bool:
    """
    Confirm against Alpaca's market clock that the market is really open.

    is_market_hours() knows only weekdays and clock time, so it reports
    open on every exchange holiday and for the whole afternoon of an
    early-close day. Alpaca's clock is the authoritative exchange
    calendar, so this is the check that gates real orders; use
    is_market_hours() only as the cheap pre-filter that avoids making
    this call overnight and at weekends.

    Args:
        client: An Alpaca TradingClient.

    Returns:
        True if Alpaca reports the market open. If the clock call itself
        fails, falls back to is_market_hours() rather than returning
        False, so a transient Alpaca error doesn't suspend stop-loss
        protection on open positions.
    """
    try:
        return bool(client.get_clock().is_open)
    except Exception as error:
        logger.warning(
            "Unable to read Alpaca's market clock (%s); falling back to the "
            "local weekday/clock check, which does not know about exchange "
            "holidays or early closes.",
            error,
        )
        return is_market_hours()
