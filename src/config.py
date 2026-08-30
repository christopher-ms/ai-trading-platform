import os
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv()


STOCKS = [
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL",
    "TSLA", "AMD", "JPM", "V", "SPY", "QQQ",
]

HISTORY_PERIOD = "3mo"

LOG_LEVEL = "INFO"

FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")

ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")

ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# How often main.run_forever() runs a full trading cycle, in minutes.
RUN_INTERVAL_MINUTES = 30

# Not scaled by RISK_LEVEL: how many distinct symbols can be held open at
# once is a portfolio-diversification limit, not a risk-per-trade dial.
MAX_OPEN_POSITIONS = 5

# Not scaled by RISK_LEVEL: once a position's unrealized gain reaches this,
# its stop-loss moves to breakeven. See src/trading/positions.py.
TRAILING_STOP_TRIGGER_PCT = 0.10

# Confidence brackets for position sizing, checked high-to-low. A signal
# gets MAX_POSITION_SIZE_PCT scaled by the fraction of the first bracket
# its confidence clears. Fixed thresholds (0.7/0.8/0.9), independent of
# RISK_LEVEL, so "how much more to risk on a higher-confidence signal"
# stays predictable even as the overall risk dial moves.
CONFIDENCE_POSITION_SIZE_TIERS = (
    (0.9, 1.0),
    (0.8, 0.75),
    (0.7, 0.5),
)


@dataclass(frozen=True)
class RiskParams:
    """
    The full set of risk parameters derived from a single RISK_LEVEL.

    Attributes:
        stop_loss_pct: Fractional loss from entry price that closes a position.
        take_profit_pct: Fractional gain from entry price that closes a position.
        min_confidence: Minimum signal confidence required to trade.
        max_position_size_pct: Ceiling on position size, as a fraction of
            portfolio value, for the highest confidence tier.
        max_daily_trades: Maximum number of filled trades allowed per day.
        daily_loss_limit_pct: Portfolio drawdown in one day that halts new
            trading until the next day.
    """

    stop_loss_pct: float
    take_profit_pct: float
    min_confidence: float
    max_position_size_pct: float
    max_daily_trades: int
    daily_loss_limit_pct: float


def compute_risk_params(risk_level: float) -> RiskParams:
    """
    Derive every risk parameter from a single 0.0-1.0 risk dial.

    Each parameter is defined at three anchor points (risk_level 0.0, 0.5,
    1.0) and piecewise-linearly interpolated between them, since the two
    halves of most ranges aren't symmetric (e.g. stop-loss tightens by 3
    points from 0.0->0.5 but widens by 5 points from 0.5->1.0). RISK_LEVEL
    0.5 reproduces this system's original hand-tuned defaults exactly, so
    raising or lowering it scales every risk knob in the same direction at
    once instead of requiring each to be tuned separately.

    Args:
        risk_level: Overall risk appetite, from 0.0 (most conservative) to
            1.0 (most aggressive).

    Returns:
        A RiskParams with every value derived from risk_level.

    Raises:
        ValueError: If risk_level is outside [0.0, 1.0].
    """
    if not 0.0 <= risk_level <= 1.0:
        raise ValueError(f"RISK_LEVEL must be between 0.0 and 1.0, got {risk_level}.")

    def scale(low: float, mid: float, high: float) -> float:
        if risk_level <= 0.5:
            return low + (mid - low) * (risk_level / 0.5)
        return mid + (high - mid) * ((risk_level - 0.5) / 0.5)

    return RiskParams(
        stop_loss_pct=scale(0.02, 0.05, 0.10),
        take_profit_pct=scale(0.08, 0.15, 0.25),
        min_confidence=scale(0.85, 0.70, 0.55),
        max_position_size_pct=scale(0.05, 0.10, 0.20),
        max_daily_trades=round(scale(5, 10, 20)),
        daily_loss_limit_pct=scale(0.01, 0.02, 0.05),
    )


# Master risk dial: 0.0 = most conservative, 1.0 = most aggressive.
# 0.5 is the balanced default and exactly reproduces the individual values
# below. Changing this one number rescales every risk parameter at once.
RISK_LEVEL = 0.5

_DEFAULT_RISK_PARAMS = compute_risk_params(RISK_LEVEL)

# Each value below defaults to RISK_LEVEL's derived setting, but can still
# be overridden individually by editing its line directly - doing so only
# detaches that one parameter from RISK_LEVEL, the rest stay linked.
STOP_LOSS_PCT = _DEFAULT_RISK_PARAMS.stop_loss_pct

TAKE_PROFIT_PCT = _DEFAULT_RISK_PARAMS.take_profit_pct

MIN_TRADE_CONFIDENCE = _DEFAULT_RISK_PARAMS.min_confidence

MAX_POSITION_SIZE_PCT = _DEFAULT_RISK_PARAMS.max_position_size_pct

MAX_DAILY_TRADES = _DEFAULT_RISK_PARAMS.max_daily_trades

DAILY_LOSS_LIMIT_PCT = _DEFAULT_RISK_PARAMS.daily_loss_limit_pct


# --- Monitoring thresholds (src/utils/monitoring.py) ---

# Finnhub's published free-tier cap. Used to compute rolling calls-per-minute
# usage and to warn before the real limit (and a 429) is hit.
FINNHUB_RATE_LIMIT_PER_MINUTE = 60

# Warn at this fraction of FINNHUB_RATE_LIMIT_PER_MINUTE rather than waiting
# for an actual 429.
FINNHUB_RATE_LIMIT_WARNING_FRACTION = 0.8

# A single generate_signal() call's total tokens (prompt + completion) above
# this is flagged as unusually large - a signal something is feeding the
# model more indicators/headlines than expected, not a hard limit.
CLAUDE_TOKEN_WARNING_THRESHOLD = 3000

# Consecutive Alpaca order submissions (rejected or errored, back to back)
# before monitoring.py warns something may be wrong with the account or
# connection, rather than treating each rejection as an isolated event.
MAX_CONSECUTIVE_ALPACA_FAILURES = 3

# Consecutive full run() cycles with zero filled trades before warning -
# likely a configuration problem (e.g. confidence threshold too strict)
# rather than three cycles' worth of genuinely bad signals.
MAX_CONSECUTIVE_ZERO_TRADE_RUNS = 3

# Warn if portfolio value drops more than this fraction within a single
# run() cycle - a earlier, softer signal than DAILY_LOSS_LIMIT_PCT, which
# only halts trading once the full day's loss is much larger.
RUN_PORTFOLIO_DROP_WARNING_PCT = 0.01
