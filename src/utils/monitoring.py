"""
API usage tracking, rate-limit awareness, health checks, and daily
summary logging for every external service this system calls.

Nothing in here talks to Finnhub, Anthropic, or Alpaca directly - callers
in src/data/, src/analysis/, and src/trading/ report into this module
(log_api_call, log_claude_usage, record_alpaca_order_outcome, ...) and
this module decides when that activity crosses a warning threshold.

Like _DailyState in src/trading/risk.py, all state here is a module-level
singleton living only in process memory - it resets on restart and is
not shared across processes. See that file's docstring for why that's
an acceptable simplification in this system.
"""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from src.config import (
    CLAUDE_TOKEN_WARNING_THRESHOLD,
    FINNHUB_RATE_LIMIT_PER_MINUTE,
    FINNHUB_RATE_LIMIT_WARNING_FRACTION,
    MAX_CONSECUTIVE_ALPACA_FAILURES,
    MAX_CONSECUTIVE_ZERO_TRADE_RUNS,
    RUN_PORTFOLIO_DROP_WARNING_PCT,
)
from src.utils.market_hours import EASTERN_TIMEZONE

logger = logging.getLogger(__name__)

# Services with a known per-minute call cap. Only services listed here get
# proactive rate-usage warnings from log_api_call; others are just counted.
_RATE_LIMITS_PER_MINUTE = {"finnhub": FINNHUB_RATE_LIMIT_PER_MINUTE}

_RATE_WINDOW_SECONDS = 60.0


@dataclass
class _MonitoringState:
    """
    Everything monitoring.py tracks for the current Eastern calendar day.

    Resets when the date rolls over (see _reset_if_new_day), the same
    pattern src/trading/risk.py's _DailyState uses.
    """

    tracking_date: date | None = None

    # service -> timestamps (epoch seconds) of calls within the last
    # _RATE_WINDOW_SECONDS. Trimmed lazily on each log_api_call.
    recent_call_timestamps: dict[str, list[float]] = field(default_factory=dict)

    # service -> total calls made today, for the daily summary.
    daily_call_counts: dict[str, int] = field(default_factory=dict)

    claude_call_count: int = 0
    claude_input_tokens: int = 0
    claude_output_tokens: int = 0

    # TradeResult.status -> count, for the daily summary.
    daily_trade_result_counts: dict[str, int] = field(default_factory=dict)

    consecutive_alpaca_failures: int = 0
    consecutive_zero_trade_runs: int = 0

    starting_portfolio_value: float | None = None
    last_portfolio_value: float | None = None

    warnings_today: list[str] = field(default_factory=list)
    errors_today: list[str] = field(default_factory=list)


_state = _MonitoringState()


def _today() -> date:
    return datetime.now(EASTERN_TIMEZONE).date()


def _reset_if_new_day() -> None:
    """Log yesterday's summary (if any) and roll _state over on a new day."""
    today = _today()
    if _state.tracking_date == today:
        return

    if _state.tracking_date is not None:
        generate_daily_summary()

    _state.tracking_date = today
    _state.recent_call_timestamps = {}
    _state.daily_call_counts = {}
    _state.claude_call_count = 0
    _state.claude_input_tokens = 0
    _state.claude_output_tokens = 0
    _state.daily_trade_result_counts = {}
    _state.consecutive_alpaca_failures = 0
    _state.consecutive_zero_trade_runs = 0
    _state.starting_portfolio_value = None
    _state.last_portfolio_value = None
    _state.warnings_today = []
    _state.errors_today = []


def _warn(message: str) -> None:
    logger.warning(message)
    _state.warnings_today.append(message)


def _record_error(message: str) -> None:
    logger.error(message)
    _state.errors_today.append(message)


def maybe_generate_daily_summary() -> None:
    """
    Roll monitoring state over for a new trading day, logging the previous
    day's summary first if one was in progress.

    Call this once at the start of every run() cycle, before anything
    else - it's a no-op except on the first cycle of a new Eastern
    calendar day.

    Returns:
        None.
    """
    _reset_if_new_day()


def log_api_call(service: str, endpoint: str) -> None:
    """
    Record a call to an external service for rate and volume tracking.

    Every file that calls an external API (Finnhub, Anthropic, Alpaca)
    should call this once per call, right before or after making it.
    For services with a known per-minute limit (currently just Finnhub),
    this also checks rolling usage and warns at
    FINNHUB_RATE_LIMIT_WARNING_FRACTION of the limit.

    Args:
        service: Lowercase service name, e.g. "finnhub", "anthropic", "alpaca".
        endpoint: Short endpoint label, e.g. "quote", "company_news".

    Returns:
        None.
    """
    _reset_if_new_day()

    now = datetime.now(timezone.utc).timestamp()
    _state.daily_call_counts[service] = _state.daily_call_counts.get(service, 0) + 1

    limit = _RATE_LIMITS_PER_MINUTE.get(service)
    if limit is None:
        return

    timestamps = _state.recent_call_timestamps.setdefault(service, [])
    timestamps.append(now)
    cutoff = now - _RATE_WINDOW_SECONDS
    while timestamps and timestamps[0] < cutoff:
        timestamps.pop(0)

    calls_this_minute = len(timestamps)
    warning_threshold = limit * FINNHUB_RATE_LIMIT_WARNING_FRACTION

    if calls_this_minute >= warning_threshold:
        _warn(
            f"{service} | {calls_this_minute}/{limit} calls in the last "
            f"minute ({calls_this_minute / limit:.0%} of the rate limit), "
            f"last call: {endpoint}."
        )


def log_rate_limited(service: str, endpoint: str) -> None:
    """
    Record that a call was actually rejected for being rate limited.

    Distinct from log_api_call's proactive 80%-threshold warning - this
    is for the moment a 429 is actually caught, so retry/backoff code has
    a clear, unambiguous log line to point to.

    Args:
        service: Lowercase service name, e.g. "finnhub".
        endpoint: Short endpoint label, e.g. "quote".

    Returns:
        None.
    """
    _record_error(f"{service} | rate limited on {endpoint}.")


def log_claude_usage(input_tokens: int, output_tokens: int) -> None:
    """
    Record one Claude API call's token usage.

    Warns if this single call's total tokens exceed
    CLAUDE_TOKEN_WARNING_THRESHOLD, which usually means an unexpectedly
    large prompt (e.g. a stock with far more headlines than normal).

    Args:
        input_tokens: Prompt tokens for this call.
        output_tokens: Completion tokens for this call.

    Returns:
        None.
    """
    _reset_if_new_day()

    _state.claude_call_count += 1
    _state.claude_input_tokens += input_tokens
    _state.claude_output_tokens += output_tokens

    total = input_tokens + output_tokens
    if total > CLAUDE_TOKEN_WARNING_THRESHOLD:
        _warn(
            f"anthropic | single call used {total} tokens "
            f"(input={input_tokens}, output={output_tokens}), above the "
            f"{CLAUDE_TOKEN_WARNING_THRESHOLD}-token expected range."
        )


def record_trade_result(status: str) -> None:
    """
    Tally one TradeResult's status for the daily summary.

    Call this once for every TradeResult produced, whether from a new
    AI-driven signal (src/trading/executor.py) or an automatic exit
    (src/trading/positions.py).

    Args:
        status: One of SKIPPED, REJECTED, FILLED, PENDING, ERROR.

    Returns:
        None.
    """
    _reset_if_new_day()
    _state.daily_trade_result_counts[status] = (
        _state.daily_trade_result_counts.get(status, 0) + 1
    )


def record_alpaca_order_outcome(success: bool, reason: str = "") -> None:
    """
    Record whether an order actually submitted to Alpaca succeeded.

    Only call this at the point an order was genuinely sent to Alpaca
    (i.e. after submit_market_order resolves, or when submitting it
    raised) - not for signals rejected earlier by risk.py, which never
    reached Alpaca at all. Warns once MAX_CONSECUTIVE_ALPACA_FAILURES
    back-to-back submissions have failed.

    Args:
        success: True if the order filled; False if Alpaca rejected it
            or submitting it raised an error.
        reason: Human-readable context, included in the warning if any.

    Returns:
        None.
    """
    _reset_if_new_day()

    if success:
        _state.consecutive_alpaca_failures = 0
        return

    _state.consecutive_alpaca_failures += 1

    if _state.consecutive_alpaca_failures >= MAX_CONSECUTIVE_ALPACA_FAILURES:
        _warn(
            f"alpaca | {_state.consecutive_alpaca_failures} consecutive "
            f"order submissions have failed. Most recent: {reason}"
        )


def record_portfolio_value(value: float) -> None:
    """
    Record the portfolio value observed at the start of a run() cycle.

    Tracks the day's starting value (first call each day, for the daily
    summary) and warns if this run's value has dropped more than
    RUN_PORTFOLIO_DROP_WARNING_PCT since the last recorded value - a
    softer, earlier signal than risk.py's DAILY_LOSS_LIMIT_PCT halt.

    Args:
        value: Current total portfolio value.

    Returns:
        None.
    """
    _reset_if_new_day()

    if _state.starting_portfolio_value is None:
        _state.starting_portfolio_value = value

    if _state.last_portfolio_value is not None and _state.last_portfolio_value > 0:
        drop_pct = (_state.last_portfolio_value - value) / _state.last_portfolio_value
        if drop_pct >= RUN_PORTFOLIO_DROP_WARNING_PCT:
            _warn(
                f"portfolio | dropped {drop_pct:.2%} since the last run "
                f"(${_state.last_portfolio_value:,.2f} -> ${value:,.2f})."
            )

    _state.last_portfolio_value = value


def check_zero_trade_streak(trades_filled_this_run: int) -> None:
    """
    Track consecutive run() cycles with zero filled trades.

    Warns once MAX_CONSECUTIVE_ZERO_TRADE_RUNS is reached, which more
    often points at a configuration problem (confidence threshold too
    strict, a bad API key silently no-op'ing, etc.) than three cycles of
    genuinely bad signals.

    Args:
        trades_filled_this_run: Number of trades that filled this cycle.

    Returns:
        None.
    """
    _reset_if_new_day()

    if trades_filled_this_run > 0:
        _state.consecutive_zero_trade_runs = 0
        return

    _state.consecutive_zero_trade_runs += 1

    if _state.consecutive_zero_trade_runs >= MAX_CONSECUTIVE_ZERO_TRADE_RUNS:
        _warn(
            f"trading | {_state.consecutive_zero_trade_runs} consecutive "
            "runs with zero filled trades - check confidence thresholds "
            "and API keys."
        )


def check_finnhub_health() -> tuple[bool, str]:
    """
    Verify Finnhub is reachable and the API key is valid.

    Makes one real /quote call (counted like any other Finnhub call),
    since Finnhub has no dedicated ping endpoint.

    Returns:
        (True, "") if healthy; (False, reason) otherwise.
    """
    from src.data import finnhub_client

    try:
        finnhub_client.get_quote("AAPL")
        return True, ""
    except Exception as error:
        return False, f"Finnhub health check failed: {error}"


def check_alpaca_health(client) -> tuple[bool, str]:
    """
    Verify the Alpaca paper trading account is accessible.

    Args:
        client: An Alpaca TradingClient.

    Returns:
        (True, "") if healthy; (False, reason) otherwise.
    """
    try:
        client.get_account()
        return True, ""
    except Exception as error:
        return False, f"Alpaca health check failed: {error}"


def check_anthropic_health() -> tuple[bool, str]:
    """
    Verify the Anthropic API key is valid.

    Calls models.list(), a lightweight metadata endpoint that doesn't
    consume completion tokens, rather than spending a real signal-
    generation call just to check connectivity.

    Returns:
        (True, "") if healthy; (False, reason) otherwise.
    """
    from src.analysis import signals

    try:
        client = signals.get_client()
        client.models.list(limit=1)
        return True, ""
    except Exception as error:
        return False, f"Anthropic health check failed: {error}"


def run_health_checks(alpaca_client) -> tuple[bool, list[str]]:
    """
    Run all three service health checks.

    Call this once at the start of every run() cycle, after building the
    Alpaca client but before doing anything else. If any check fails,
    the caller should skip the entire cycle rather than fail mid-loop
    partway through the stock list.

    Args:
        alpaca_client: An Alpaca TradingClient.

    Returns:
        (True, []) if every service is healthy; (False, reasons) otherwise,
        where reasons lists every failing check's explanation.
    """
    checks = (
        check_finnhub_health(),
        check_alpaca_health(alpaca_client),
        check_anthropic_health(),
    )

    problems = [reason for ok, reason in checks if not ok]

    for _, reason in checks:
        if reason:
            _record_error(reason)

    return len(problems) == 0, problems


def send_metric_to_cloudwatch(metric_name: str, value: float) -> None:
    """
    Emit one monitoring metric.

    Currently just logs in a structured, greppable format. When this
    system deploys to AWS, swap the body of this one function for a
    boto3 `cloudwatch.put_metric_data(...)` call - every other function
    in this module calls this one rather than logging metrics directly,
    so that's the only place that needs to change.

    Args:
        metric_name: Metric name, e.g. "finnhub.calls_per_minute".
        value: Metric value.

    Returns:
        None.
    """
    logger.info("CLOUDWATCH_METRIC | %s=%s", metric_name, value)


def generate_daily_summary() -> None:
    """
    Log a summary of the trading day that just ended.

    Called automatically by _reset_if_new_day() the first time
    maybe_generate_daily_summary() runs on a new Eastern calendar day,
    summarizing the day before. Can also be called directly (e.g. for a
    manual mid-day check) without triggering a state reset.

    Covers: total API calls per service, trade outcomes (filled/skipped/
    rejected/pending/error), estimated Claude token usage, every warning
    and error monitoring.py recorded, and portfolio value at the start
    of the day versus the most recently observed value.

    Returns:
        None.
    """
    start = _state.starting_portfolio_value
    end = _state.last_portfolio_value
    portfolio_change = ""
    if start is not None and end is not None and start > 0:
        portfolio_change = f" ({(end - start) / start:+.2%})"

    logger.info(
        "=== Daily summary for %s ===\n"
        "API calls: %s\n"
        "Trade outcomes: %s\n"
        "Claude usage: %d calls, ~%d input tokens, ~%d output tokens\n"
        "Portfolio: start=$%s end=$%s%s\n"
        "Warnings (%d): %s\n"
        "Errors (%d): %s",
        _state.tracking_date,
        _state.daily_call_counts or "none",
        _state.daily_trade_result_counts or "none",
        _state.claude_call_count,
        _state.claude_input_tokens,
        _state.claude_output_tokens,
        f"{start:,.2f}" if start is not None else "n/a",
        f"{end:,.2f}" if end is not None else "n/a",
        portfolio_change,
        len(_state.warnings_today),
        _state.warnings_today or "none",
        len(_state.errors_today),
        _state.errors_today or "none",
    )

    for service, count in _state.daily_call_counts.items():
        send_metric_to_cloudwatch(f"{service}.daily_calls", count)
    for status, count in _state.daily_trade_result_counts.items():
        send_metric_to_cloudwatch(f"trades.{status.lower()}", count)
    send_metric_to_cloudwatch(
        "claude.daily_tokens", _state.claude_input_tokens + _state.claude_output_tokens
    )
    if start is not None and end is not None:
        send_metric_to_cloudwatch("portfolio.end_of_day_value", end)
