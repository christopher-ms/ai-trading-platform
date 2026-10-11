"""
Process-wide dry-run switch, set by `python -m src.main --dry-run`.

In dry-run mode the full pipeline runs at any hour - real market data,
news, Claude signals, risk checks and position sizing - but nothing with a
lasting effect happens:

- Market-hours gates are skipped (main.py's two cycle gates and risk.py's
  per-signal check), since testing outside market hours is the point.
- No order is sent to Alpaca: executor.py and positions.py return a
  DRY_RUN TradeResult describing what they would have done, and
  backup_stops.py logs the stop orders it would have placed or canceled.
- No DynamoDB write happens (db.py's write helpers no-op). Reads still do,
  so a dry run sees the day's real trade count and loss-limit state, but
  can't consume a MAX_DAILY_TRADES slot or latch a loss-limit halt for the
  real bot.

That makes a dry run safe alongside a live loop (CLAUDE.md's warning about
two processes double-trading doesn't apply).
"""

_enabled = False


def enable() -> None:
    """Turn dry-run mode on for the rest of this process."""
    global _enabled
    _enabled = True


def is_enabled() -> bool:
    """Whether this process is a dry run."""
    return _enabled
