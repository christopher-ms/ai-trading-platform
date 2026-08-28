from datetime import datetime, time
from zoneinfo import ZoneInfo


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
