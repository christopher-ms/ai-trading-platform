"""
Stop-loss, take-profit, and trailing-stop monitoring for open positions.

check_open_positions runs once per trading cycle, before any new signal
is evaluated, so a position that has moved against (or strongly in favor
of) the account since the last cycle is closed out immediately rather
than waiting on the next AI signal for that symbol.

It also keeps a broker-side backup stop order (backup_stops.py) in sync
with each position's effective stop, so the stop still holds at Alpaca
if this process isn't running.
"""

import logging

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide

from src.config import STOP_LOSS_PCT, TAKE_PROFIT_PCT, TRAILING_STOP_TRIGGER_PCT
from src.data import db
from src.trading import backup_stops
from src.trading.executor import TradeResult, submit_market_order
from src.trading.risk import record_trade_executed
from src.utils import dry_run, monitoring


logger = logging.getLogger(__name__)

# Symbols whose stop-loss has been moved to breakeven under the trailing
# rule. Kept in memory for the running process; every add/discard is also
# written through to DynamoDB (src/data/db.py) so a restart doesn't
# revert an active trailing stop back to a plain stop-loss. Best-effort -
# a DynamoDB outage just means that durability is temporarily lost, not
# that trailing-stop logic stops working for the running process.
_trailing_stop_active: set[str] = set()
_trailing_stops_loaded = False


def _ensure_trailing_stops_loaded() -> None:
    """Hydrate _trailing_stop_active from DynamoDB once per process."""
    global _trailing_stops_loaded
    if _trailing_stops_loaded:
        return
    _trailing_stop_active.update(db.load_trailing_stops())
    _trailing_stops_loaded = True


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
    if dry_run.is_enabled():
        result = TradeResult(
            status="DRY_RUN",
            symbol=symbol,
            action="SELL",
            reason=f"Dry run: would close {qty:g} shares ({reason}); no order sent.",
        )
        logger.info("%s | %s | %s", result.symbol, result.status, result.reason)
        monitoring.record_trade_result(result, source="stop_exit")
        return result

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
        monitoring.record_trade_result(result, source="stop_exit")
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
        # An order that hasn't filled within the poll window is still live at
        # the exchange and will most likely fill, so it consumes a
        # MAX_DAILY_TRADES slot just like a confirmed fill - nothing ever
        # observes the later fill to count it retroactively. A canceled order
        # never reached the market, so it doesn't count.
        if order.status.value != "canceled":
            record_trade_executed()

        result = TradeResult(
            status="PENDING",
            symbol=symbol,
            action="SELL",
            reason=f"{reason}; exit order not yet filled (status={order.status.value}).",
            order_id=str(order.id),
        )

    logger.info("%s | %s | %s", result.symbol, result.status, result.reason)
    monitoring.record_trade_result(result, source="stop_exit")
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
    - Otherwise, keep the position's backup stop at Alpaca in line with
      its effective stop (placing it if missing, moving it to breakeven
      once the trailing stop activates, resizing it if the position grew).

    Backup stops left behind for symbols no longer held are canceled.

    Args:
        client: Alpaca trading client.

    Returns:
        A TradeResult for every position closed this cycle (empty if none).
    """
    _ensure_trailing_stops_loaded()

    positions = client.get_all_positions()
    live_symbols = {position.symbol for position in positions}
    stale_symbols = _trailing_stop_active - live_symbols
    _trailing_stop_active.intersection_update(live_symbols)
    for symbol in stale_symbols:
        db.delete_trailing_stop(symbol)

    try:
        open_backup_stops = backup_stops.get_backup_stops(client)
    except Exception as error:
        logger.error("Failed to fetch backup stop orders: %s", error)
        open_backup_stops = None

    if open_backup_stops is not None:
        for symbol in open_backup_stops.keys() - live_symbols:
            backup_stops.cancel_backup_stops(client, symbol, open_backup_stops[symbol])

    results = []

    for position in positions:
        symbol = position.symbol
        qty = float(position.qty)
        entry_price = float(position.avg_entry_price)
        current_price = float(position.current_price)
        pl_pct = (current_price - entry_price) / entry_price

        if pl_pct >= TRAILING_STOP_TRIGGER_PCT and symbol not in _trailing_stop_active:
            _trailing_stop_active.add(symbol)
            db.save_trailing_stop(symbol)

        trailing_active = symbol in _trailing_stop_active
        effective_stop_pct = 0.0 if trailing_active else -STOP_LOSS_PCT

        if pl_pct <= effective_stop_pct:
            stop_label = (
                "trailing stop (breakeven)"
                if trailing_active
                else f"stop-loss (-{STOP_LOSS_PCT:.1%})"
            )
            reason = f"{stop_label} triggered at {pl_pct:+.2%} unrealized"
        elif pl_pct >= TAKE_PROFIT_PCT:
            reason = f"take-profit (+{TAKE_PROFIT_PCT:.1%}) triggered at {pl_pct:+.2%} unrealized"
        else:
            if open_backup_stops is not None:
                backup_stops.ensure_backup_stop(
                    client,
                    symbol,
                    qty=qty,
                    entry_price=entry_price,
                    stop_pct=effective_stop_pct,
                    existing=open_backup_stops.get(symbol, []),
                )
            continue

        # The backup stop holds the shares at Alpaca, so the exit sell would
        # be rejected for insufficient quantity while it's still open.
        backup_stops.cancel_backup_stops(
            client,
            symbol,
            open_backup_stops.get(symbol, []) if open_backup_stops is not None else None,
        )
        result = _close_position(client, symbol, qty, reason)
        results.append(result)

        # A rejected or errored exit leaves the position open with its backup
        # stop already canceled - put it back rather than leave the position
        # unprotected at the broker. (A PENDING exit is still live and holds
        # the shares itself, so there's nothing to re-place.)
        if result.status in ("REJECTED", "ERROR"):
            backup_stops.ensure_backup_stop(
                client,
                symbol,
                qty=qty,
                entry_price=entry_price,
                stop_pct=effective_stop_pct,
                existing=[],
            )

        # Only forget the trailing stop once the position is actually gone. A
        # rejected, errored, or still-unfilled exit leaves the position open,
        # and clearing the flag here would quietly revert its stop from
        # breakeven back to -STOP_LOSS_PCT on the next cycle - losing
        # protection at exactly the moment the exit already failed once.
        if result.status == "FILLED":
            _trailing_stop_active.discard(symbol)
            db.delete_trailing_stop(symbol)

    return results
