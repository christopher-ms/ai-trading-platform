import logging
from datetime import datetime, timedelta, timezone

from newsapi import NewsApiClient

from src.config import NEWS_API_KEY


logger = logging.getLogger(__name__)


def get_news_client() -> NewsApiClient:
    """
    Create a NewsAPI client using the configured API key.

    Returns:
        Configured NewsApiClient instance.

    Raises:
        ValueError: If the NewsAPI key is missing.
    """
    if not NEWS_API_KEY:
        raise ValueError("NEWS_API_KEY environment variable is not set.")

    return NewsApiClient(api_key=NEWS_API_KEY)


def get_stock_news(
    ticker: str,
    hours: int = 24,
    max_articles: int = 5,
) -> list[dict[str, str]]:
    """
    Retrieve recent news articles related to a stock ticker.

    Args:
        ticker: Stock ticker symbol.
        hours: Number of hours to search backwards.
        max_articles: Maximum number of articles to return.

    Returns:
        List of cleaned news article dictionaries.

    Raises:
        Exception: If the NewsAPI request fails.
    """
    try:
        client = get_news_client()

        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(hours=hours)

        response = client.get_everything(
            q=ticker,
            from_param=start_time.strftime("%Y-%m-%dT%H:%M:%S"),
            to=end_time.strftime("%Y-%m-%dT%H:%M:%S"),
            language="en",
            sort_by="publishedAt",
            page_size=max_articles,
        )

        articles = response.get("articles", [])

        logger.info(
            "Retrieved %d news articles for %s.",
            len(articles),
            ticker,
        )

        return format_articles(articles)

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
    Clean raw NewsAPI articles into a compact format for analysis.

    Args:
        articles: Raw articles returned by NewsAPI.

    Returns:
        List containing title, source, published time, description,
        and URL for each article.
    """
    formatted_articles = []

    for article in articles:
        formatted_articles.append(
            {
                "title": article.get("title", ""),
                "source": article.get("source", {}).get("name", ""),
                "published_at": article.get("publishedAt", ""),
                "description": article.get("description", "") or "",
                "url": article.get("url", ""),
            }
        )

    return formatted_articles