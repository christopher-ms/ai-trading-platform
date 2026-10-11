"""
Broker-side backup stop orders for open positions.

check_open_positions (positions.py) only protects a position while this
process is running and polling every RUN_INTERVAL_MINUTES. If the process
dies, the host sleeps, or the network drops, nothing closes a losing
position. So every long position also gets a resting GTC stop order at
Alpaca, at the same level as the bot's own effective stop (-STOP_LOSS_PCT
below entry, or breakeven once the trailing stop has activated). Alpaca
then enforces the stop on its own, whether or not this process is alive.

Every backup stop is tagged with BACKUP_STOP_ID_PREFIX in its
client_order_id, and only orders carrying that tag are ever touched here,
so a manually placed order on the account is never canceled.

A resting stop holds its shares at Alpaca: while it's open, any other
sell for those shares is rejected for insufficient quantity. That's why
every path that sells a position (positions.py's exits, executor.py's
SELL signals) must call cancel_backup_stops() first.

Everything here is best-effort and never raises: a failure is logged and
the bot's own polling stop in positions.py remains in force, and the next
cycle's reconcile retries.
"""

import logging
import time
import uuid

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.models import Order
from alpaca.trading.requests import GetOrdersRequest, StopOrderRequest

from src.utils import dry_run


logger = logging.getLogger(__name__)

BACKUP_STOP_ID_PREFIX = "backup-stop-"

CANCEL_POLL_TIMEOUT_SECONDS = 5.0
CANCEL_POLL_INTERVAL_SECONDS = 0.5

# Order states in which an order no longer holds shares at Alpaca.
_DONE_STATUSES = {"canceled", "filled", "expired", "rejected", "replaced"}


def get_backup_stops(
    client: TradingClient,
    symbol: str | None = None,
) -> dict[str, list[Order]]:
    """
    Fetch every open backup stop order, grouped by symbol.

    Args:
        client: Alpaca trading client.
        symbol: Restrict to this symbol, or None for all symbols.

    Returns:
        Mapping of symbol to its open backup stop orders.

    Raises:
        alpaca.common.exceptions.APIError: If Alpaca can't be reached.
    """
    request = GetOrdersRequest(
        status=QueryOrderStatus.OPEN,
        side=OrderSide.SELL,
        symbols=[symbol] if symbol else None,
        limit=500,
    )
    stops: dict[str, list[Order]] = {}
    for order in client.get_orders(request):
        if (order.client_order_id or "").startswith(BACKUP_STOP_ID_PREFIX):
            stops.setdefault(order.symbol, []).append(order)
    return stops


def cancel_backup_stops(
    client: TradingClient,
    symbol: str,
    orders: list[Order] | None = None,
) -> None:
    """
    Cancel a symbol's backup stops and wait until Alpaca releases the shares.

    Cancellation is asynchronous at Alpaca, and a sell submitted while the
    stop is still open gets rejected for insufficient quantity, so this
    polls each order until it reaches a terminal state (or times out).

    Args:
        client: Alpaca trading client.
        symbol: Stock ticker symbol.
        orders: The symbol's open backup stops if already fetched, else None
            to fetch them here.
    """
    try:
        if orders is None:
            orders = get_backup_stops(client, symbol).get(symbol, [])

        if dry_run.is_enabled():
            if orders:
                logger.info(
                    "%s | dry run: would cancel %d backup stop order(s)",
                    symbol,
                    len(orders),
                )
            return

        for order in orders:
            client.cancel_order_by_id(order.id)

        deadline = time.monotonic() + CANCEL_POLL_TIMEOUT_SECONDS
        for order in orders:
            while client.get_order_by_id(order.id).status.value not in _DONE_STATUSES:
                if time.monotonic() >= deadline:
                    logger.warning(
                        "%s | backup stop %s not confirmed canceled after %.0fs",
                        symbol,
                        order.id,
                        CANCEL_POLL_TIMEOUT_SECONDS,
                    )
                    break
                time.sleep(CANCEL_POLL_INTERVAL_SECONDS)

        if orders:
            logger.info("%s | canceled %d backup stop order(s)", symbol, len(orders))
    except Exception as error:
        logger.error("%s | failed to cancel backup stop: %s", symbol, error)


def ensure_backup_stop(
    client: TradingClient,
    symbol: str,
    qty: float,
    entry_price: float,
    stop_pct: float,
    existing: list[Order] | None = None,
) -> None:
    """
    Make sure a position has exactly one backup stop at the right level.

    Does nothing if a single backup stop already matches the desired
    quantity and price; otherwise cancels whatever is there and places a
    fresh GTC stop. Short positions are skipped (this system never opens
    them, and a sell stop would be the wrong side anyway). Fractional
    shares are rounded down, since Alpaca doesn't accept GTC orders for
    fractional quantities.

    Args:
        client: Alpaca trading client.
        symbol: Stock ticker symbol.
        qty: Shares currently held.
        entry_price: Average entry price of the position.
        stop_pct: Stop level relative to entry, e.g. -0.05 for 5% below
            entry, or 0.0 for breakeven.
        existing: The symbol's open backup stops if already fetched, else
            None to fetch them here.
    """
    whole_qty = int(qty)
    if whole_qty <= 0:
        return

    stop_price = round(entry_price * (1 + stop_pct), 2)

    try:
        if existing is None:
            existing = get_backup_stops(client, symbol).get(symbol, [])

        if (
            len(existing) == 1
            and float(existing[0].qty) == whole_qty
            and float(existing[0].stop_price) == stop_price
        ):
            return

        if dry_run.is_enabled():
            logger.info(
                "%s | dry run: would place backup stop: sell %d @ stop $%.2f "
                "(replacing %d existing)",
                symbol,
                whole_qty,
                stop_price,
                len(existing),
            )
            return

        if existing:
            cancel_backup_stops(client, symbol, existing)

        order = client.submit_order(
            StopOrderRequest(
                symbol=symbol,
                qty=whole_qty,
                side=OrderSide.SELL,
                time_in_force=TimeInForce.GTC,
                stop_price=stop_price,
                client_order_id=f"{BACKUP_STOP_ID_PREFIX}{symbol}-{uuid.uuid4().hex[:12]}",
            )
        )
        logger.info(
            "%s | backup stop placed at Alpaca: sell %d @ stop $%.2f (%+.1f%% from entry $%.2f) | order_id=%s",
            symbol,
            whole_qty,
            stop_price,
            stop_pct * 100,
            entry_price,
            order.id,
        )
    except Exception as error:
        logger.error(
            "%s | failed to place backup stop (bot's own stop still applies): %s",
            symbol,
            error,
        )


def protect_position(client: TradingClient, symbol: str, stop_pct: float) -> None:
    """
    Look up a position's current size and entry, then ensure its backup stop.

    Used right after a BUY fills, and after a failed exit re-exposes a
    position whose backup stop was canceled to make way for it. No-op if
    the symbol isn't held.

    Args:
        client: Alpaca trading client.
        symbol: Stock ticker symbol.
        stop_pct: Stop level relative to entry (see ensure_backup_stop).
    """
    try:
        position = client.get_open_position(symbol)
    except Exception as error:
        logger.info("%s | no open position to protect (%s)", symbol, error)
        return

    ensure_backup_stop(
        client,
        symbol,
        qty=float(position.qty),
        entry_price=float(position.avg_entry_price),
        stop_pct=stop_pct,
    )
