"""
DynamoDB persistence layer.

Every function here is best-effort and write-behind: DynamoDB is a
durability layer under state that already lives correctly in memory (see
_DailyState in src/trading/risk.py, _trailing_stop_active in
src/trading/positions.py, and _MonitoringState in src/utils/monitoring.py)
- never something the trading loop depends on reading mid-cycle. Every
function below catches all boto3/botocore errors internally and returns
None/False on failure rather than raising, so callers never need their
own try/except, and a DynamoDB outage degrades to "this state isn't
durable right now" instead of interrupting trading. For the same reason,
DynamoDB health is deliberately NOT part of monitoring.run_health_checks().

Tables (created once via scripts/setup_dynamodb.py, never on app boot):
- DYNAMODB_DAILY_RISK_STATE_TABLE: PK trading_date. Mirrors risk.py's
  _DailyState; written synchronously on every mutation since
  halted_for_loss is safety-critical.
- DYNAMODB_TRAILING_STOPS_TABLE: PK symbol. One item per symbol with an
  active trailing stop; deleted once the stop is cleared or the position
  closes.
- DYNAMODB_MONITORING_STATE_TABLE: PK tracking_date. Mirrors
  monitoring.py's _MonitoringState, checkpointed once per run() cycle
  rather than on every counter mutation (pure observability data, not a
  safety gate).
- DYNAMODB_TRADE_HISTORY_TABLE: PK symbol, SK executed_at (ISO-8601).
  Append-only log of every TradeResult, from both executor.py and
  positions.py. Has a by-date GSI (PK trading_date, SK executed_at).
"""

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import boto3
from botocore.config import Config

from src.config import (
    AWS_REGION,
    DYNAMODB_DAILY_RISK_STATE_TABLE,
    DYNAMODB_ENABLED,
    DYNAMODB_ENDPOINT_URL,
    DYNAMODB_MONITORING_STATE_TABLE,
    DYNAMODB_TRADE_HISTORY_TABLE,
    DYNAMODB_TRAILING_STOPS_TABLE,
)


logger = logging.getLogger(__name__)

# Fail fast rather than let a DynamoDB outage stall the synchronous
# trading loop: one attempt, short timeouts.
_BOTO_CONFIG = Config(
    connect_timeout=3,
    read_timeout=3,
    retries={"max_attempts": 1},
)

_resource = None


def _get_resource():
    """
    Lazily build the boto3 DynamoDB resource.

    Returns:
        The boto3 DynamoDB resource, or None if DYNAMODB_ENABLED is false
        or the resource could not be constructed.
    """
    global _resource

    if not DYNAMODB_ENABLED:
        return None

    if _resource is None:
        try:
            kwargs: dict[str, Any] = {"region_name": AWS_REGION, "config": _BOTO_CONFIG}
            if DYNAMODB_ENDPOINT_URL:
                kwargs["endpoint_url"] = DYNAMODB_ENDPOINT_URL
            _resource = boto3.resource("dynamodb", **kwargs)
        except Exception as error:
            logger.warning("DynamoDB | unable to build client: %s", error)
            return None

    return _resource


def _table(name: str):
    resource = _get_resource()
    return resource.Table(name) if resource is not None else None


def _to_dynamo(value: Any) -> Any:
    """DynamoDB has no native float type; convert every float to Decimal."""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {key: _to_dynamo(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_dynamo(val) for val in value]
    return value


def _from_dynamo(value: Any) -> Any:
    """Reverse of _to_dynamo, for reading items back out."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {key: _from_dynamo(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_from_dynamo(val) for val in value]
    return value


def _put_item(table_name: str, item: dict) -> bool:
    table = _table(table_name)
    if table is None:
        return False
    try:
        table.put_item(Item=_to_dynamo(item))
        return True
    except Exception as error:
        logger.warning("DynamoDB | put_item on %s failed: %s", table_name, error)
        return False


def _get_item(table_name: str, key: dict) -> dict | None:
    table = _table(table_name)
    if table is None:
        return None
    try:
        item = table.get_item(Key=key).get("Item")
        return _from_dynamo(item) if item else None
    except Exception as error:
        logger.warning("DynamoDB | get_item on %s failed: %s", table_name, error)
        return None


def _delete_item(table_name: str, key: dict) -> bool:
    table = _table(table_name)
    if table is None:
        return False
    try:
        table.delete_item(Key=key)
        return True
    except Exception as error:
        logger.warning("DynamoDB | delete_item on %s failed: %s", table_name, error)
        return False


def _scan_all(table_name: str) -> list[dict]:
    table = _table(table_name)
    if table is None:
        return []
    try:
        items = []
        response = table.scan()
        items.extend(response.get("Items", []))
        while "LastEvaluatedKey" in response:
            response = table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
            items.extend(response.get("Items", []))
        return [_from_dynamo(item) for item in items]
    except Exception as error:
        logger.warning("DynamoDB | scan on %s failed: %s", table_name, error)
        return []


# --- Daily risk state (src/trading/risk.py) ---


def save_daily_risk_state(
    trading_date: str,
    trades_executed: int,
    starting_portfolio_value: float | None,
    halted_for_loss: bool,
) -> bool:
    """Persist risk.py's _DailyState for one Eastern calendar day."""
    return _put_item(
        DYNAMODB_DAILY_RISK_STATE_TABLE,
        {
            "trading_date": trading_date,
            "trades_executed": trades_executed,
            "starting_portfolio_value": starting_portfolio_value,
            "halted_for_loss": halted_for_loss,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )


def load_daily_risk_state(trading_date: str) -> dict | None:
    """Load risk.py's _DailyState for one Eastern calendar day, if present."""
    return _get_item(DYNAMODB_DAILY_RISK_STATE_TABLE, {"trading_date": trading_date})


# --- Trailing stops (src/trading/positions.py) ---


def save_trailing_stop(symbol: str) -> bool:
    """Record that symbol's trailing stop has moved to breakeven."""
    return _put_item(
        DYNAMODB_TRAILING_STOPS_TABLE,
        {
            "symbol": symbol,
            "active": True,
            "activated_at": datetime.now(timezone.utc).isoformat(),
        },
    )


def delete_trailing_stop(symbol: str) -> bool:
    """Clear symbol's trailing stop (position closed or stop reset)."""
    return _delete_item(DYNAMODB_TRAILING_STOPS_TABLE, {"symbol": symbol})


def load_trailing_stops() -> set[str]:
    """Load every symbol currently marked with an active trailing stop."""
    return {item["symbol"] for item in _scan_all(DYNAMODB_TRAILING_STOPS_TABLE)}


# --- Monitoring daily state (src/utils/monitoring.py) ---


def save_monitoring_state(tracking_date: str, state: dict) -> bool:
    """Checkpoint monitoring.py's _MonitoringState for one Eastern calendar day."""
    item = dict(state)
    item["tracking_date"] = tracking_date
    item["updated_at"] = datetime.now(timezone.utc).isoformat()
    return _put_item(DYNAMODB_MONITORING_STATE_TABLE, item)


def load_monitoring_state(tracking_date: str) -> dict | None:
    """Load monitoring.py's checkpointed state for one Eastern calendar day."""
    return _get_item(DYNAMODB_MONITORING_STATE_TABLE, {"tracking_date": tracking_date})


# --- Trade history (src/trading/executor.py, src/trading/positions.py) ---


def save_trade_result(
    symbol: str,
    action: str,
    status: str,
    reason: str,
    order_id: str | None,
    filled_price: float | None,
    filled_qty: float | None,
    source: str,
    trading_date: str,
) -> bool:
    """
    Append one TradeResult to the trade history audit log.

    Args:
        symbol: Stock ticker symbol.
        action: BUY, SELL, or HOLD.
        status: SKIPPED, REJECTED, FILLED, PENDING, or ERROR.
        reason: Human-readable explanation of the outcome.
        order_id: Alpaca order ID, if one was submitted.
        filled_price: Average fill price, if the order filled.
        filled_qty: Filled share quantity, if the order filled.
        source: "signal" for an AI-driven trade (src/trading/executor.py)
            or "stop_exit" for an automatic stop-loss/take-profit/
            trailing-stop close (src/trading/positions.py).
        trading_date: Eastern calendar date this result belongs to, as
            an ISO date string.

    Returns:
        True if the write succeeded, False otherwise.
    """
    return _put_item(
        DYNAMODB_TRADE_HISTORY_TABLE,
        {
            "symbol": symbol,
            "executed_at": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "status": status,
            "reason": reason,
            "order_id": order_id,
            "filled_price": filled_price,
            "filled_qty": filled_qty,
            "source": source,
            "trading_date": trading_date,
        },
    )
