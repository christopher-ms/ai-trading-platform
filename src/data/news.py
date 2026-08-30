import logging
from datetime import datetime, timedelta, timezone

from src.data.finnhub_client import get_company_news


logger = logging.getLogger(__name__)

NEWS_LOOKBACK_HOURS = 4


def get_stock_news(
    ticker: str,
    hours: int = NEWS_LOOKBACK_HOURS,
    max_articles: int = 5,
) -> list[dict[str, str]]:
    """
    Retrieve recent news articles related to a stock ticker.

    Finnhub's company-news date range is day-granularity only (YYYY-MM-DD),
    so this requests every calendar day the lookback window could touch
    and filters down to the real cutoff itself using each article's exact
    timestamp.

    Args:
        ticker: Stock ticker symbol.
        hours: Number of hours to search backwards.
        max_articles: Maximum number of articles to return.

    Returns:
        List of cleaned news article dictionaries, most recent first.

    Raises:
        Exception: If the Finnhub request fails.
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours)

    try:
        articles = get_company_news(
            ticker,
            from_date=cutoff.strftime("%Y-%m-%d"),
            to_date=now.strftime("%Y-%m-%d"),
        )

        recent_articles = [
            article
            for article in articles
            if datetime.fromtimestamp(article.get("datetime", 0), tz=timezone.utc)
            >= cutoff
        ]
        recent_articles.sort(key=lambda article: article.get("datetime", 0), reverse=True)
        recent_articles = recent_articles[:max_articles]

        logger.info(
            "Retrieved %d news articles for %s (last %dh).",
            len(recent_articles),
            ticker,
            hours,
        )

        return format_articles(recent_articles)

    except Exception as error:
        logger.error(
            "Failed to retrieve news for %s: %s",
            ticker,
            error,
        )
        raise


def format_articles(
    articles: list[dict],
) -> list[dict[str, str]]:
    """
    Clean raw Finnhub articles into a compact format for analysis.

    Args:
        articles: Raw articles returned by Finnhub's company-news endpoint.

    Returns:
        List containing title, source, published time, description,
        and URL for each article.
    """
    formatted_articles = []

    for article in articles:
        published_at = datetime.fromtimestamp(
            article.get("datetime", 0), tz=timezone.utc
        ).isoformat()

        formatted_articles.append(
            {
                "title": article.get("headline", ""),
                "source": article.get("source", ""),
                "published_at": published_at,
                "description": article.get("summary", "") or "",
                "url": article.get("url", ""),
            }
        )

    return formatted_articles
