import logging
import time

from src.analysis.signals import generate_signal
from src.analysis.technical import calculate_indicators
from src.config import LOG_LEVEL, RUN_INTERVAL_MINUTES, STOCKS
from src.data.market import get_market_data
from src.data.news import get_stock_news
from src.trading.executor import TradeResult, execute_signal, get_portfolio_value, get_trading_client
from src.trading.positions import check_open_positions
from src.trading.risk import check_daily_loss_limit
from src.utils import monitoring
from src.utils.market_hours import is_market_hours


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


def analyze_stock(ticker: str) -> TradeResult | None:
    """
    Analyze a stock and, if warranted, trade it.

    Retrieves market data, calculates technical indicators, and pulls
    recent news; sends both to Claude to generate a trading signal; then
    runs that signal through risk management and execution.

    Args:
        ticker: Stock ticker symbol.

    Returns:
        The TradeResult from execute_signal, or None if this ticker was
        abandoned before a signal could be generated (bad market data,
        a news fetch failure, or Claude failing/being rate limited).
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
        return None

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
        articles = []

    try:
        signal = generate_signal(ticker, indicators, articles)
    except Exception as error:
        logger.error(
            "Unable to generate a trading signal for %s: %s",
            ticker,
            error,
        )
        return None

    return execute_signal(signal)


def run() -> None:
    """
    Run one full trading cycle.

    Order of operations: roll monitoring's daily summary over if a new
    Eastern trading day has started; skip entirely if the market is
    closed; otherwise verify Finnhub/Alpaca/Anthropic are all reachable;
    close out any open position that has breached stop-loss, take-profit,
    or the trailing-stop rule; then, unless today's loss limit has already
    halted new trading, evaluate every configured stock for a new signal.

    Position monitoring always runs ahead of the daily-loss-limit check
    and is never gated by it, so a halted day still protects existing
    positions - only the search for new trades stops.

    Returns:
        None.
    """
    logger = logging.getLogger(__name__)

    monitoring.maybe_generate_daily_summary()

    if not is_market_hours():
        logger.info("Market is currently closed. Skipping this cycle.")
        return

    try:
        client = get_trading_client()
    except Exception as error:
        logger.error("Unable to build Alpaca trading client: %s", error)
        return

    healthy, problems = monitoring.run_health_checks(client)
    if not healthy:
        logger.error(
            "Health check failed; skipping this cycle entirely rather than "
            "trading against a degraded service: %s",
            "; ".join(problems),
        )
        return

    check_open_positions(client)

    try:
        portfolio_value = get_portfolio_value(client)
    except Exception as error:
        logger.error("Unable to reach Alpaca to check account state: %s", error)
        return

    monitoring.record_portfolio_value(portfolio_value)

    loss_decision = check_daily_loss_limit(portfolio_value)
    if loss_decision is not None:
        logger.warning(
            "%s New signals will not be evaluated for the rest of today.",
            loss_decision.reason,
        )
        return

    trades_filled_this_run = 0
    for ticker in STOCKS:
        result = analyze_stock(ticker)
        if result is not None and result.status == "FILLED":
            trades_filled_this_run += 1

    monitoring.check_zero_trade_streak(trades_filled_this_run)


def run_forever() -> None:
    """
    Run continuously, executing one trading cycle every RUN_INTERVAL_MINUTES.

    Outside market hours, run() itself is a no-op after its is_market_hours
    check, so the loop just wakes up every RUN_INTERVAL_MINUTES, logs that
    it's skipping, and goes back to sleep - it naturally starts trading
    again once the market opens without needing a separate scheduler
    process. A single cycle's own errors are caught and logged so one bad
    cycle (e.g. a transient Alpaca outage) doesn't kill the loop.

    Returns:
        None.
    """
    logger = logging.getLogger(__name__)
    interval_seconds = RUN_INTERVAL_MINUTES * 60

    logger.info(
        "Starting scheduler: running every %d minutes during market hours.",
        RUN_INTERVAL_MINUTES,
    )

    while True:
        try:
            run()
        except Exception as error:
            logger.error("Unhandled error during trading cycle: %s", error)

        time.sleep(interval_seconds)


if __name__ == "__main__":
    setup_logging()
    run_forever()
