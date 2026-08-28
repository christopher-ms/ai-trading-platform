import logging
import time
from dataclasses import dataclass

from alpaca.common.exceptions import APIError
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.requests import StockLatestTradeRequest
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.models import Order
from alpaca.trading.requests import MarketOrderRequest

from src.config import ALPACA_API_KEY, ALPACA_SECRET_KEY
from src.trading.risk import TradingSignal, calculate_position_size, evaluate_signal


logger = logging.getLogger(__name__)

ORDER_SIDE_BY_ACTION = {
    "BUY": OrderSide.BUY,
    "SELL": OrderSide.SELL,
}

FILL_POLL_TIMEOUT_SECONDS = 5.0
FILL_POLL_INTERVAL_SECONDS = 0.5


@dataclass
class TradeResult:
    """
    Outcome of attempting to execute a trading signal.

    Attributes:
        status: One of SKIPPED, REJECTED, FILLED, PENDING, ERROR.
        symbol: Stock ticker symbol.
        action: The signal action that was attempted.
        reason: Human-readable explanation of the outcome.
        order_id: Alpaca order ID, set once an order is submitted.
        filled_price: Average fill price, set once the order fills.
        filled_qty: Filled share quantity, set once the order fills.
    """

    status: str
    symbol: str
    action: str
    reason: str
    order_id: str | None = None
    filled_price: float | None = None
    filled_qty: float | None = None


def get_trading_client() -> TradingClient:
    """
    Build an Alpaca trading client for the paper trading environment.

    Returns:
        A TradingClient configured with credentials from .env, pinned
        to Alpaca's paper trading endpoint.

    Raises:
        ValueError: If ALPACA_API_KEY or ALPACA_SECRET_KEY is not set.
    """
    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        raise ValueError(
            "ALPACA_API_KEY and ALPACA_SECRET_KEY must be set in .env."
        )

    return TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=True)


def get_data_client() -> StockHistoricalDataClient:
    """
    Build an Alpaca market data client for price lookups.

    Returns:
        A StockHistoricalDataClient configured with the same paper
        trading credentials used for order execution.

    Raises:
        ValueError: If ALPACA_API_KEY or ALPACA_SECRET_KEY is not set.
    """
    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        raise ValueError(
            "ALPACA_API_KEY and ALPACA_SECRET_KEY must be set in .env."
        )

    return StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)


def get_portfolio_value(client: TradingClient) -> float:
    """
    Fetch the account's total portfolio value.

    Args:
        client: Alpaca trading client.

    Returns:
        Total portfolio value (cash plus the market value of all
        open positions), in dollars.
    """
    account = client.get_account()
    return float(account.portfolio_value)


def get_open_position_quantities(client: TradingClient) -> dict[str, float]:
    """
    Fetch all currently open positions.

    Args:
        client: Alpaca trading client.

    Returns:
        Mapping of ticker symbol to shares held. An empty dict means
        no open positions.
    """
    positions = client.get_all_positions()
    return {position.symbol: float(position.qty) for position in positions}


def get_latest_price(data_client: StockHistoricalDataClient, symbol: str) -> float:
    """
    Fetch the latest traded price for a symbol.

    Args:
        data_client: Alpaca market data client.
        symbol: Stock ticker symbol.

    Returns:
        The most recent trade price.

    Raises:
        RuntimeError: If no trade data is returned for the symbol.
    """
    request = StockLatestTradeRequest(symbol_or_symbols=symbol)
    trades = data_client.get_stock_latest_trade(request)

    if symbol not in trades:
        raise RuntimeError(f"No latest trade data returned for {symbol}.")

    return float(trades[symbol].price)


def submit_market_order(
    client: TradingClient,
    symbol: str,
    side: OrderSide,
    quantity: float,
) -> Order:
    """
    Submit a market order and wait briefly for it to fill.

    Alpaca's paper simulator usually fills market orders within a
    second during market hours, but the object returned immediately
    after submission is often still in the "accepted" state. This
    polls get_order_by_id for a few seconds so callers can report a
    real fill price instead of a placeholder.

    Args:
        client: Alpaca trading client.
        symbol: Stock ticker symbol.
        side: OrderSide.BUY or OrderSide.SELL.
        quantity: Number of shares to trade.

    Returns:
        The order, in whatever state it reached before the poll
        timed out (filled, rejected, or still pending).

    Raises:
        alpaca.common.exceptions.APIError: If Alpaca rejects the
            order outright (e.g. insufficient buying power).
    """
    request = MarketOrderRequest(
        symbol=symbol,
        qty=quantity,
        side=side,
        time_in_force=TimeInForce.DAY,
    )

    order = client.submit_order(request)

    deadline = time.monotonic() + FILL_POLL_TIMEOUT_SECONDS

    while order.status.value not in ("filled", "rejected", "canceled"):
        if time.monotonic() >= deadline:
            break
        time.sleep(FILL_POLL_INTERVAL_SECONDS)
        order = client.get_order_by_id(order.id)

    return order


def execute_signal(signal: TradingSignal) -> TradeResult:
    """
    Run a trading signal through risk management and, if approved,
    execute it as a paper trade on Alpaca.

    This is the single entry point the AI analysis layer should call.
    Every outcome — skipped, rejected, filled, or errored — is logged
    with the signal's reasoning and returned as a TradeResult rather
    than raised, so a caller looping over multiple signals never has
    a single bad signal or API hiccup kill the run.

    Args:
        signal: The trading signal to execute.

    Returns:
        A TradeResult describing what happened.
    """
    if signal.action == "HOLD":
        result = TradeResult(
            status="SKIPPED",
            symbol=signal.symbol,
            action=signal.action,
            reason="Signal action is HOLD; no trade to execute.",
        )
        logger.info("%s | %s | %s", result.symbol, result.status, result.reason)
        return result

    try:
        client = get_trading_client()
        portfolio_value = get_portfolio_value(client)
        open_positions = get_open_position_quantities(client)
    except Exception as error:
        result = TradeResult(
            status="ERROR",
            symbol=signal.symbol,
            action=signal.action,
            reason=f"Failed to reach Alpaca to check account state: {error}",
        )
        logger.error("%s | %s | %s", result.symbol, result.status, result.reason)
        return result

    current_qty = open_positions.get(signal.symbol, 0.0)
    already_holds_symbol = signal.symbol in open_positions

    decision = evaluate_signal(
        signal,
        portfolio_value=portfolio_value,
        open_position_count=len(open_positions),
        already_holds_symbol=already_holds_symbol,
    )

    if not decision.approved:
        result = TradeResult(
            status="REJECTED",
            symbol=signal.symbol,
            action=signal.action,
            reason=decision.reason,
        )
        logger.info("%s | %s | %s", result.symbol, result.status, result.reason)
        return result

    try:
        data_client = get_data_client()
        price = get_latest_price(data_client, signal.symbol)
    except Exception as error:
        result = TradeResult(
            status="ERROR",
            symbol=signal.symbol,
            action=signal.action,
            reason=f"Failed to fetch latest price: {error}",
        )
        logger.error("%s | %s | %s", result.symbol, result.status, result.reason)
        return result

    quantity = calculate_position_size(
        signal,
        price=price,
        portfolio_value=portfolio_value,
        current_qty=current_qty,
    )

    if quantity <= 0:
        reason = (
            "Position sizing produced zero shares "
            f"(price=${price:.2f}, current_qty={current_qty})."
        )
        result = TradeResult(
            status="REJECTED",
            symbol=signal.symbol,
            action=signal.action,
            reason=reason,
        )
        logger.info("%s | %s | %s", result.symbol, result.status, result.reason)
        return result

    try:
        order = submit_market_order(
            client,
            symbol=signal.symbol,
            side=ORDER_SIDE_BY_ACTION[signal.action],
            quantity=quantity,
        )
    except APIError as error:
        result = TradeResult(
            status="ERROR",
            symbol=signal.symbol,
            action=signal.action,
            reason=f"Alpaca rejected the order request: {error}",
        )
        logger.error("%s | %s | %s", result.symbol, result.status, result.reason)
        return result
    except Exception as error:
        result = TradeResult(
            status="ERROR",
            symbol=signal.symbol,
            action=signal.action,
            reason=f"Unexpected error submitting order: {error}",
        )
        logger.error("%s | %s | %s", result.symbol, result.status, result.reason)
        return result

    if order.status.value == "rejected":
        result = TradeResult(
            status="REJECTED",
            symbol=signal.symbol,
            action=signal.action,
            reason="Order was rejected by Alpaca after submission.",
            order_id=str(order.id),
        )
        logger.warning("%s | %s | %s", result.symbol, result.status, result.reason)
        return result

    if order.status.value != "filled":
        result = TradeResult(
            status="PENDING",
            symbol=signal.symbol,
            action=signal.action,
            reason=(
                f"Order submitted but not yet filled after "
                f"{FILL_POLL_TIMEOUT_SECONDS:.0f}s (status={order.status.value})."
            ),
            order_id=str(order.id),
        )
        logger.info("%s | %s | %s", result.symbol, result.status, result.reason)
        return result

    result = TradeResult(
        status="FILLED",
        symbol=signal.symbol,
        action=signal.action,
        reason=signal.reasoning,
        order_id=str(order.id),
        filled_price=float(order.filled_avg_price),
        filled_qty=float(order.filled_qty),
    )
    logger.info(
        "%s | %s | qty=%.4f @ $%.2f | order_id=%s | %s",
        result.symbol,
        result.status,
        result.filled_qty,
        result.filled_price,
        result.order_id,
        signal.reasoning,
    )
    return result
