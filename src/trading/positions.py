"""
Stop-loss, take-profit, and trailing-stop monitoring for open positions.

check_open_positions runs once per trading cycle, before any new signal
is evaluated, so a position that has moved against (or strongly in favor
of) the account since the last cycle is closed out immediately rather
than waiting on the next AI signal for that symbol.
"""

import logging

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide

from src.config import STOP_LOSS_PCT, TAKE_PROFIT_PCT, TRAILING_STOP_TRIGGER_PCT
from src.trading.executor import TradeResult, submit_market_order
from src.trading.risk import record_trade_executed
from src.utils import monitoring


logger = logging.getLogger(__name__)

# Symbols whose stop-loss has been moved to breakeven under the trailing
# rule. In-memory only - see _DailyState's docstring in src/trading/risk.py
# for why that's an acceptable simplification here.
_trailing_stop_active: set[str] = set()


def _close_position(
    client: TradingClient,
    symbol: str,
    qty: float,
    reason: str,
) -> TradeResult:
    """
    Submit a market sell for an entire position and report the outcome.

    Args:
        client: Alpaca trading client.
        symbol: Stock ticker symbol.
        qty: Full share quantity currently held.
        reason: Human-readable explanation for why this position is closing.

    Returns:
        A TradeResult describing what happened.
    """
    logger.info("%s | closing position: %s", symbol, reason)

    try:
        order = submit_market_order(
            client, symbol=symbol, side=OrderSide.SELL, quantity=qty
        )
    except Exception as error:
        result = TradeResult(
            status="ERROR",
            symbol=symbol,
            action="SELL",
            reason=f"Failed to submit exit order ({reason}): {error}",
        )
        logger.error("%s | %s | %s", result.symbol, result.status, result.reason)
        monitoring.record_trade_result(result.status)
        monitoring.record_alpaca_order_outcome(success=False, reason=result.reason)
        return result

    if order.status.value == "filled":
        record_trade_executed()
        result = TradeResult(
            status="FILLED",
            symbol=symbol,
            action="SELL",
            reason=reason,
            order_id=str(order.id),
            filled_price=float(order.filled_avg_price),
            filled_qty=float(order.filled_qty),
        )
        monitoring.record_alpaca_order_outcome(success=True)
    elif order.status.value == "rejected":
        result = TradeResult(
            status="REJECTED",
            symbol=symbol,
            action="SELL",
            reason=f"{reason}, but Alpaca rejected the exit order.",
            order_id=str(order.id),
        )
        monitoring.record_alpaca_order_outcome(success=False, reason=result.reason)
    else:
        result = TradeResult(
            status="PENDING",
            symbol=symbol,
            action="SELL",
            reason=f"{reason}; exit order not yet filled (status={order.status.value}).",
            order_id=str(order.id),
        )

    logger.info("%s | %s | %s", result.symbol, result.status, result.reason)
    monitoring.record_trade_result(result.status)
    return result


def check_open_positions(client: TradingClient) -> list[TradeResult]:
    """
    Evaluate every open position against stop-loss, take-profit, and the
    trailing-stop rule, closing out any that have breached a threshold.

    Rules, checked per position:
    - Once unrealized P/L reaches TRAILING_STOP_TRIGGER_PCT, that
      position's effective stop moves from -STOP_LOSS_PCT to breakeven
      (0%) and stays there for the life of the position.
    - If unrealized P/L falls to or below the effective stop, sell.
    - Otherwise, if unrealized P/L reaches TAKE_PROFIT_PCT, sell.

    Args:
        client: Alpaca trading client.

    Returns:
        A TradeResult for every position closed this cycle (empty if none).
    """
    positions = client.get_all_positions()
    live_symbols = {position.symbol for position in positions}
    _trailing_stop_active.intersection_update(live_symbols)

    results = []

    for position in positions:
        symbol = position.symbol
        qty = float(position.qty)
        entry_price = float(position.avg_entry_price)
        current_price = float(position.current_price)
        pl_pct = (current_price - entry_price) / entry_price

        if pl_pct >= TRAILING_STOP_TRIGGER_PCT:
            _trailing_stop_active.add(symbol)

        trailing_active = symbol in _trailing_stop_active
        effective_stop_pct = 0.0 if trailing_active else -STOP_LOSS_PCT

        if pl_pct <= effective_stop_pct:
            stop_label = (
                "trailing stop (breakeven)"
                if trailing_active
                else f"stop-loss (-{STOP_LOSS_PCT:.1%})"
            )
            reason = f"{stop_label} triggered at {pl_pct:+.2%} unrealized"
            results.append(_close_position(client, symbol, qty, reason))
            _trailing_stop_active.discard(symbol)
        elif pl_pct >= TAKE_PROFIT_PCT:
            reason = f"take-profit (+{TAKE_PROFIT_PCT:.1%}) triggered at {pl_pct:+.2%} unrealized"
            results.append(_close_position(client, symbol, qty, reason))
            _trailing_stop_active.discard(symbol)

    return results
