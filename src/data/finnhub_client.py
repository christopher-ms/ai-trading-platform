"""
Thin wrapper around the Finnhub API: authentication, rate-limit-aware
retries, and monitoring hooks, shared by src/data/market.py (live quotes)
and src/data/news.py (company news).
"""

import logging
import time

import finnhub

from src.config import FINNHUB_API_KEY
from src.utils import monitoring

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
INITIAL_BACKOFF_SECONDS = 1.0


def get_client() -> finnhub.Client:
    """
    Build a Finnhub client.

    Returns:
        A finnhub.Client configured with the API key from .env.

    Raises:
        ValueError: If FINNHUB_API_KEY is not set.
    """
    if not FINNHUB_API_KEY:
        raise ValueError("FINNHUB_API_KEY must be set in .env.")

    return finnhub.Client(api_key=FINNHUB_API_KEY)


def _call_with_backoff(endpoint: str, func, *args, **kwargs):
    """
    Call a Finnhub client method, retrying on 429s with exponential backoff.

    Every attempt - including ones that end up rate limited - is logged via
    monitoring.log_api_call. A 429 is additionally logged via
    monitoring.log_rate_limited so it's unambiguous in the logs when a
    retry was actually caused by hitting the rate limit versus some other
    Finnhub error (which is raised immediately, not retried).

    Args:
        endpoint: Short endpoint label for monitoring, e.g. "quote".
        func: Bound Finnhub client method to call.
        *args: Positional arguments for func.
        **kwargs: Keyword arguments for func.

    Returns:
        Whatever func returns.

    Raises:
        finnhub.exceptions.FinnhubAPIException: If a non-429 API error
            occurs, or a 429 is still happening after MAX_ATTEMPTS tries.
    """
    backoff = INITIAL_BACKOFF_SECONDS

    for attempt in range(1, MAX_ATTEMPTS + 1):
        monitoring.log_api_call("finnhub", endpoint)

        try:
            return func(*args, **kwargs)
        except finnhub.exceptions.FinnhubAPIException as error:
            if error.status_code != 429:
                raise

            monitoring.log_rate_limited("finnhub", endpoint)

            if attempt == MAX_ATTEMPTS:
                logger.error(
                    "Finnhub %s still rate limited after %d attempts; giving up.",
                    endpoint,
                    MAX_ATTEMPTS,
                )
                raise

            logger.warning(
                "Finnhub %s rate limited (attempt %d/%d); retrying in %.0fs.",
                endpoint,
                attempt,
                MAX_ATTEMPTS,
                backoff,
            )
            time.sleep(backoff)
            backoff *= 2


def get_quote(symbol: str) -> dict:
    """
    Fetch a real-time quote for a symbol.

    Args:
        symbol: Stock ticker symbol.

    Returns:
        Finnhub's raw quote dict: c (current price), d (change), dp
        (percent change), h (day high), l (day low), o (day open), pc
        (previous close), t (timestamp).

    Raises:
        ValueError: If FINNHUB_API_KEY is not set.
        finnhub.exceptions.FinnhubAPIException: If the request ultimately
            fails (including exhausting retries on a persistent 429).
    """
    client = get_client()
    return _call_with_backoff("quote", client.quote, symbol)


def get_company_news(symbol: str, from_date: str, to_date: str) -> list[dict]:
    """
    Fetch company news for a symbol over a date range.

    Finnhub's date range is day-granularity only (YYYY-MM-DD) - callers
    needing an hour-level window (see src/data/news.py) must widen the
    request to whole days and filter the results themselves.

    Args:
        symbol: Stock ticker symbol.
        from_date: Start date, "YYYY-MM-DD".
        to_date: End date, "YYYY-MM-DD".

    Returns:
        A list of Finnhub's raw article dicts (headline, datetime,
        source, summary, url, ...).

    Raises:
        ValueError: If FINNHUB_API_KEY is not set.
        finnhub.exceptions.FinnhubAPIException: If the request ultimately
            fails (including exhausting retries on a persistent 429).
    """
    client = get_client()
    return _call_with_backoff(
        "company_news", client.company_news, symbol, _from=from_date, to=to_date
    )
