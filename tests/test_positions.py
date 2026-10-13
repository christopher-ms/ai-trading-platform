"""Tests for src/trading/positions.py: stop-loss, take-profit, trailing stop."""

import unittest
from types import SimpleNamespace
from unittest import mock

from src import config
from src.trading import positions
from src.utils import dry_run


ENTRY = 100.0


def fake_position(symbol="AAPL", qty=10, entry=ENTRY, current=ENTRY):
    # Alpaca returns these fields as strings.
    return SimpleNamespace(
        symbol=symbol,
        qty=str(qty),
        avg_entry_price=str(entry),
        current_price=str(current),
    )


def fake_order(status="filled", price=ENTRY, qty=10):
    return SimpleNamespace(
        id="order-1",
        status=SimpleNamespace(value=status),
        filled_avg_price=str(price),
        filled_qty=str(qty),
    )


class PositionsTestCase(unittest.TestCase):
    """
    A fake Alpaca client plus mocks for everything check_open_positions
    calls out to. All the mocks share one parent so tests can assert on
    call order (backup stop canceled before the exit is sent).
    """

    def setUp(self):
        positions._trailing_stop_active.clear()
        positions._trailing_stops_loaded = True

        self.calls = mock.Mock()
        self.calls.submit_market_order.return_value = fake_order()
        self.calls.get_backup_stops.return_value = {}

        patches = {
            "submit_market_order": mock.patch.object(positions, "submit_market_order", self.calls.submit_market_order),
            "record_trade_executed": mock.patch.object(positions, "record_trade_executed", self.calls.record_trade_executed),
            "monitoring": mock.patch.object(positions, "monitoring", self.calls.monitoring),
            "get_backup_stops": mock.patch.object(positions.backup_stops, "get_backup_stops", self.calls.get_backup_stops),
            "ensure_backup_stop": mock.patch.object(positions.backup_stops, "ensure_backup_stop", self.calls.ensure_backup_stop),
            "cancel_backup_stops": mock.patch.object(positions.backup_stops, "cancel_backup_stops", self.calls.cancel_backup_stops),
        }
        for patcher in patches.values():
            patcher.start()
            self.addCleanup(patcher.stop)

        self.client = mock.Mock()

    def run_check(self, *held):
        self.client.get_all_positions.return_value = list(held)
        return positions.check_open_positions(self.client)

    def at_pl(self, pl_pct, symbol="AAPL"):
        return fake_position(symbol=symbol, current=ENTRY * (1 + pl_pct))


class TestStopLoss(PositionsTestCase):
    def test_small_loss_holds_and_keeps_backup_stop_at_minus_stop_loss(self):
        results = self.run_check(self.at_pl(-config.STOP_LOSS_PCT + 0.005))

        self.assertEqual(results, [])
        self.calls.submit_market_order.assert_not_called()
        self.assertAlmostEqual(
            self.calls.ensure_backup_stop.call_args.kwargs["stop_pct"], -config.STOP_LOSS_PCT
        )

    def test_loss_past_stop_closes_full_position(self):
        results = self.run_check(self.at_pl(-config.STOP_LOSS_PCT - 0.005))

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, "FILLED")
        self.assertIn("stop-loss", results[0].reason)
        self.assertEqual(self.calls.submit_market_order.call_args.kwargs["quantity"], 10.0)
        self.calls.record_trade_executed.assert_called_once()

    def test_backup_stop_canceled_before_exit_is_sent(self):
        # A resting stop holds the shares; Alpaca rejects the exit otherwise.
        self.run_check(self.at_pl(-0.10))

        names = [name for name, _, _ in self.calls.mock_calls]
        self.assertLess(names.index("cancel_backup_stops"), names.index("submit_market_order"))

    def test_gap_down_far_past_stop_still_closes(self):
        self.assertEqual(len(self.run_check(self.at_pl(-0.30))), 1)


class TestTakeProfit(PositionsTestCase):
    def test_gain_past_target_closes(self):
        results = self.run_check(self.at_pl(config.TAKE_PROFIT_PCT + 0.005))

        self.assertEqual(len(results), 1)
        self.assertIn("take-profit", results[0].reason)

    def test_gain_under_target_holds(self):
        self.assertEqual(self.run_check(self.at_pl(config.TAKE_PROFIT_PCT - 0.005)), [])


class TestTrailingStop(PositionsTestCase):
    def test_gain_past_trigger_moves_stop_to_breakeven(self):
        self.run_check(self.at_pl(config.TRAILING_STOP_TRIGGER_PCT + 0.005))

        self.assertIn("AAPL", positions._trailing_stop_active)
        self.assertEqual(self.calls.ensure_backup_stop.call_args.kwargs["stop_pct"], 0.0)

    def test_gain_under_trigger_does_not_activate(self):
        self.run_check(self.at_pl(config.TRAILING_STOP_TRIGGER_PCT - 0.005))

        self.assertNotIn("AAPL", positions._trailing_stop_active)

    def test_activated_stop_sticks_after_price_pulls_back(self):
        self.run_check(self.at_pl(0.12))
        self.run_check(self.at_pl(0.03))

        self.assertIn("AAPL", positions._trailing_stop_active)
        self.assertEqual(self.calls.ensure_backup_stop.call_args.kwargs["stop_pct"], 0.0)

    def test_fall_back_to_breakeven_closes_and_clears_flag(self):
        self.run_check(self.at_pl(0.12))

        # A -1% position would be fine under the plain -5% stop, but the
        # trailing stop is now at breakeven.
        results = self.run_check(self.at_pl(-0.01))

        self.assertEqual(len(results), 1)
        self.assertIn("trailing stop", results[0].reason)
        self.assertNotIn("AAPL", positions._trailing_stop_active)

    def test_rejected_exit_keeps_flag_and_replaces_backup_stop_at_breakeven(self):
        self.run_check(self.at_pl(0.12))
        self.calls.submit_market_order.return_value = fake_order(status="rejected")
        self.calls.ensure_backup_stop.reset_mock()

        results = self.run_check(self.at_pl(-0.01))

        self.assertEqual(results[0].status, "REJECTED")
        self.assertIn("AAPL", positions._trailing_stop_active)
        self.assertEqual(self.calls.ensure_backup_stop.call_args.kwargs["stop_pct"], 0.0)
        self.calls.record_trade_executed.assert_not_called()

    def test_errored_exit_keeps_flag_and_replaces_backup_stop(self):
        self.run_check(self.at_pl(0.12))
        self.calls.submit_market_order.side_effect = RuntimeError("connection reset")
        self.calls.ensure_backup_stop.reset_mock()

        results = self.run_check(self.at_pl(-0.01))

        self.assertEqual(results[0].status, "ERROR")
        self.assertIn("AAPL", positions._trailing_stop_active)
        self.calls.ensure_backup_stop.assert_called_once()

    def test_pending_exit_keeps_flag_and_counts_toward_daily_trades(self):
        self.run_check(self.at_pl(0.12))
        self.calls.submit_market_order.return_value = fake_order(status="accepted")
        self.calls.ensure_backup_stop.reset_mock()

        results = self.run_check(self.at_pl(-0.01))

        self.assertEqual(results[0].status, "PENDING")
        self.assertIn("AAPL", positions._trailing_stop_active)
        self.calls.record_trade_executed.assert_called_once()
        # The pending exit holds the shares itself; no backup stop re-placed.
        self.calls.ensure_backup_stop.assert_not_called()

    def test_canceled_exit_does_not_count_toward_daily_trades(self):
        self.calls.submit_market_order.return_value = fake_order(status="canceled")

        self.run_check(self.at_pl(-0.10))

        self.calls.record_trade_executed.assert_not_called()

    def test_flag_dropped_for_symbol_no_longer_held(self):
        positions._trailing_stop_active.add("MSFT")

        self.run_check(self.at_pl(0.01))

        self.assertNotIn("MSFT", positions._trailing_stop_active)


class TestBackupStopReconcile(PositionsTestCase):
    def test_orphan_backup_stops_canceled(self):
        orphan = [SimpleNamespace(id="stop-1")]
        self.calls.get_backup_stops.return_value = {"TSLA": orphan}

        self.run_check(self.at_pl(0.01))

        self.calls.cancel_backup_stops.assert_called_once_with(self.client, "TSLA", orphan)

    def test_backup_stop_fetch_failure_still_runs_stops(self):
        # The bot's own stop must not depend on reading the backup stops.
        self.calls.get_backup_stops.side_effect = RuntimeError("Alpaca 500")

        results = self.run_check(self.at_pl(-0.10))

        self.assertEqual(len(results), 1)
        self.calls.ensure_backup_stop.assert_not_called()

    def test_each_position_judged_independently(self):
        results = self.run_check(
            self.at_pl(-0.10, symbol="AAPL"),
            self.at_pl(0.02, symbol="MSFT"),
            self.at_pl(0.20, symbol="NVDA"),
        )

        self.assertEqual(sorted(r.symbol for r in results), ["AAPL", "NVDA"])


class TestDryRun(PositionsTestCase):
    def test_dry_run_never_sends_an_exit(self):
        with mock.patch.object(dry_run, "_enabled", True):
            results = self.run_check(self.at_pl(-0.10))

        self.assertEqual(results[0].status, "DRY_RUN")
        self.calls.submit_market_order.assert_not_called()
        self.calls.record_trade_executed.assert_not_called()


if __name__ == "__main__":
    unittest.main()
