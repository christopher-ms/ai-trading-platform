import logging
from dataclasses import dataclass
from datetime import date, datetime

from src.config import (
    CONFIDENCE_POSITION_SIZE_TIERS,
    DAILY_LOSS_LIMIT_PCT,
    MAX_DAILY_TRADES,
    MAX_OPEN_POSITIONS,
    MAX_POSITION_SIZE_PCT,
    MIN_TRADE_CONFIDENCE,
)
from src.utils.market_hours import EASTERN_TIMEZONE, is_market_hours


logger = logging.getLogger(__name__)

VALID_ACTIONS = {"BUY", "SELL", "HOLD"}
VALID_RISK_LEVELS = {"LOW", "MEDIUM", "HIGH"}


@dataclass
class TradingSignal:
    """
    A trading recommendation produced by the AI analysis layer.

    Attributes:
        action: One of BUY, SELL, HOLD.
        symbol: Stock ticker symbol.
        confidence: Model confidence in the signal, between 0.0 and 1.0.
        reasoning: Human-readable explanation for the signal.
        risk_level: One of LOW, MEDIUM, HIGH.
    """

    action: str
    symbol: str
    confidence: float
    reasoning: str
    risk_level: str

    def __post_init__(self) -> None:
        self.action = self.action.upper()
        self.symbol = self.symbol.upper()
        self.risk_level = self.risk_level.upper()

        if self.action not in VALID_ACTIONS:
            raise ValueError(
                f"Invalid action '{self.action}'. Must be one of {VALID_ACTIONS}."
            )

        if self.risk_level not in VALID_RISK_LEVELS:
            raise ValueError(
                f"Invalid risk_level '{self.risk_level}'. "
                f"Must be one of {VALID_RISK_LEVELS}."
            )

        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                f"Confidence must be between 0.0 and 1.0, got {self.confidence}."
            )


@dataclass
class RiskDecision:
    """
    Outcome of running a trading signal through risk management checks.

    Attributes:
        approved: Whether the trade is cleared to execute.
        reason: Human-readable explanation for the decision.
        max_position_value: Maximum dollar amount allowed for this trade,
            set only when approved is True.
    """

    approved: bool
    reason: str
    max_position_value: float | None = None


@dataclass
class _DailyState:
    """
    Trade count and starting equity for the current Eastern calendar day.

    Lives only in process memory: main.run_forever() keeps one long-lived
    process running during market hours, so this survives across cycles
    within a day. A process restart resets it (including halted_for_loss),
    an acceptable simplification given this system has no persistent
    trade database.
    """

    trading_date: date | None = None
    trades_executed: int = 0
    starting_portfolio_value: float | None = None
    halted_for_loss: bool = False


_daily_state = _DailyState()


def _reset_daily_state_if_new_day() -> None:
    """Roll _daily_state over when the Eastern calendar date changes."""
    today = datetime.now(EASTERN_TIMEZONE).date()
    if _daily_state.trading_date != today:
        _daily_state.trading_date = today
        _daily_state.trades_executed = 0
        _daily_state.starting_portfolio_value = None
        _daily_state.halted_for_loss = False


def record_trade_executed() -> None:
    """
    Record that an order filled, for MAX_DAILY_TRADES accounting.

    Call this exactly once per filled order. Both signal-driven trades
    (src/trading/executor.py) and automatic stop-loss/take-profit exits
    (src/trading/positions.py) count toward the same daily limit, since
    both consume trading activity for the day.

    Returns:
        None.
    """
    _reset_daily_state_if_new_day()
    _daily_state.trades_executed += 1


def check_market_open() -> RiskDecision | None:
    """
    Reject trading while the market is closed.

    Returns:
        A rejecting RiskDecision if the market is closed, otherwise None
        so evaluate_signal can move on to the next check.
    """
    if not is_market_hours():
        return RiskDecision(approved=False, reason="Market is closed.")

    return None


def check_daily_loss_limit(portfolio_value: float) -> RiskDecision | None:
    """
    Halt new trading for the rest of the day once losses hit the limit.

    The first observed portfolio value each day becomes that day's
    baseline. Once halted_for_loss is set it stays set for the rest of
    the Eastern calendar day, even if the portfolio recovers above the
    threshold intraday, so a bounce doesn't immediately re-open trading
    on what's already been flagged a bad day.

    Args:
        portfolio_value: Total account portfolio value right now.

    Returns:
        A rejecting RiskDecision if the daily loss limit has been
        breached, otherwise None so evaluate_signal can move on.
    """
    _reset_daily_state_if_new_day()

    if _daily_state.starting_portfolio_value is None:
        _daily_state.starting_portfolio_value = portfolio_value

    if _daily_state.halted_for_loss:
        return RiskDecision(
            approved=False,
            reason=(
                "Trading halted for the rest of today: daily loss limit "
                "already breached."
            ),
        )

    baseline = _daily_state.starting_portfolio_value
    loss_pct = (baseline - portfolio_value) / baseline if baseline else 0.0

    if loss_pct >= DAILY_LOSS_LIMIT_PCT:
        _daily_state.halted_for_loss = True
        return RiskDecision(
            approved=False,
            reason=(
                f"Portfolio is down {loss_pct:.2%} today, breaching the "
                f"{DAILY_LOSS_LIMIT_PCT:.2%} daily loss limit; trading "
                "halted for the rest of today."
            ),
        )

    return None


def check_confidence_threshold(signal: TradingSignal) -> RiskDecision | None:
    """
    Reject signals below the minimum confidence threshold.

    Args:
        signal: The trading signal to check.

    Returns:
        A rejecting RiskDecision if confidence is too low, otherwise None
        so evaluate_signal can move on to the next check.
    """
    if signal.confidence < MIN_TRADE_CONFIDENCE:
        return RiskDecision(
            approved=False,
            reason=(
                f"Confidence {signal.confidence:.2f} is below the minimum "
                f"threshold of {MIN_TRADE_CONFIDENCE:.2f}."
            ),
        )

    return None


def check_daily_trade_limit() -> RiskDecision | None:
    """
    Reject new trades once MAX_DAILY_TRADES has been reached for today.

    Returns:
        A rejecting RiskDecision if today's trade count is at or above
        MAX_DAILY_TRADES, otherwise None so evaluate_signal can move on.
    """
    _reset_daily_state_if_new_day()

    if _daily_state.trades_executed >= MAX_DAILY_TRADES:
        return RiskDecision(
            approved=False,
            reason=(
                f"Already executed {_daily_state.trades_executed} trades "
                f"today, at the maximum of {MAX_DAILY_TRADES}."
            ),
        )

    return None


def check_position_limit(
    signal: TradingSignal,
    open_position_count: int,
    already_holds_symbol: bool,
) -> RiskDecision | None:
    """
    Reject new BUYs once the open position limit is reached.

    Adding to an existing position or selling out of one never counts
    against the limit, since neither increases the number of open
    positions.

    Args:
        signal: The trading signal to check.
        open_position_count: Number of distinct symbols currently held.
        already_holds_symbol: Whether a position in signal.symbol is
            already open.

    Returns:
        A rejecting RiskDecision if the position limit blocks this trade,
        otherwise None so evaluate_signal can move on to the next check.
    """
    if signal.action != "BUY" or already_holds_symbol:
        return None

    if open_position_count >= MAX_OPEN_POSITIONS:
        return RiskDecision(
            approved=False,
            reason=(
                f"Already at the maximum of {MAX_OPEN_POSITIONS} open "
                f"positions; cannot open a new position in {signal.symbol}."
            ),
        )

    return None


def get_position_size_pct(confidence: float) -> float:
    """
    Map a signal's confidence to a position size, as a fraction of
    portfolio value.

    CONFIDENCE_POSITION_SIZE_TIERS is checked high-to-low; a signal gets
    MAX_POSITION_SIZE_PCT scaled by the fraction of the first bracket its
    confidence clears (e.g. confidence 0.85 clears the 0.8 bracket, so it
    gets 75% of MAX_POSITION_SIZE_PCT).

    Args:
        confidence: Signal confidence, between 0.0 and 1.0.

    Returns:
        Position size as a fraction of portfolio value.
    """
    for threshold, fraction in CONFIDENCE_POSITION_SIZE_TIERS:
        if confidence >= threshold:
            return MAX_POSITION_SIZE_PCT * fraction

    # Cleared MIN_TRADE_CONFIDENCE (checked separately) but fell below
    # every sizing bracket - only possible when RISK_LEVEL is high enough
    # to push MIN_TRADE_CONFIDENCE under the lowest bracket (0.7). Size it
    # like the lowest bracket rather than sizing to zero.
    lowest_fraction = CONFIDENCE_POSITION_SIZE_TIERS[-1][1]
    return MAX_POSITION_SIZE_PCT * lowest_fraction


def calculate_position_size(
    signal: TradingSignal,
    price: float,
    portfolio_value: float,
    current_qty: float,
) -> float:
    """
    Determine how many shares to trade for an approved signal.

    A BUY is sized by get_position_size_pct(signal.confidence): higher
    confidence signals get a larger fraction of portfolio value, up to
    MAX_POSITION_SIZE_PCT. A SELL always closes the full existing position
    rather than partially trimming it, to keep paper trading state simple.

    Args:
        signal: The approved trading signal.
        price: Latest trade price for signal.symbol.
        portfolio_value: Total account portfolio value.
        current_qty: Shares of signal.symbol currently held (0 if none).

    Returns:
        Number of shares to trade. May be 0 if the position cap can't
        afford even one share, or if there is nothing to sell.

    Raises:
        ValueError: If price is not positive.
    """
    if price <= 0:
        raise ValueError(f"Price must be positive, got {price}.")

    if signal.action == "SELL":
        return float(current_qty)

    position_size_pct = get_position_size_pct(signal.confidence)
    max_position_value = portfolio_value * position_size_pct
    quantity = int(max_position_value // price)

    return float(quantity)


def evaluate_signal(
    signal: TradingSignal,
    portfolio_value: float,
    open_position_count: int,
    already_holds_symbol: bool,
) -> RiskDecision:
    """
    Run a trading signal through every risk management check in order.

    Checks run market hours, daily loss limit, confidence, daily trade
    count, then position limits, and stop at the first failure so the log
    clearly shows which rule blocked the trade. HOLD signals are rejected
    immediately since there is nothing to execute.

    Args:
        signal: The trading signal to evaluate.
        portfolio_value: Total account portfolio value.
        open_position_count: Number of distinct symbols currently held.
        already_holds_symbol: Whether a position in signal.symbol is
            already open.

    Returns:
        A RiskDecision. When approved, max_position_value holds the
        dollar cap for sizing the order.
    """
    logger.info(
        "Evaluating signal: %s %s | confidence=%.2f | risk=%s | %s",
        signal.action,
        signal.symbol,
        signal.confidence,
        signal.risk_level,
        signal.reasoning,
    )

    if signal.action == "HOLD":
        decision = RiskDecision(
            approved=False,
            reason="Signal action is HOLD; no trade to evaluate.",
        )
        logger.info("Decision for %s: %s", signal.symbol, decision.reason)
        return decision

    checks = (
        check_market_open(),
        check_daily_loss_limit(portfolio_value),
        check_confidence_threshold(signal),
        check_daily_trade_limit(),
        check_position_limit(signal, open_position_count, already_holds_symbol),
    )

    for decision in checks:
        if decision is not None:
            logger.warning(
                "Signal rejected for %s: %s",
                signal.symbol,
                decision.reason,
            )
            return decision

    position_size_pct = get_position_size_pct(signal.confidence)
    max_position_value = portfolio_value * position_size_pct
    decision = RiskDecision(
        approved=True,
        reason="Signal passed all risk management checks.",
        max_position_value=max_position_value,
    )
    logger.info(
        "Signal approved for %s: max position value $%.2f",
        signal.symbol,
        max_position_value,
    )
    return decision
