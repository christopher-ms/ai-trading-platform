"""Tests for src/trading/backup_stops.py against a fake Alpaca client."""

import unittest
from types import SimpleNamespace
from unittest import mock

from alpaca.trading.enums import OrderSide, TimeInForce

from src.trading import backup_stops


def stop_order(symbol="AAPL", qty=10, stop_price=95.0, order_id="stop-1", prefix=backup_stops.BACKUP_STOP_ID_PREFIX):
    return SimpleNamespace(
        id=order_id,
        symbol=symbol,
        qty=str(qty),
        stop_price=str(stop_price),
        client_order_id=f"{prefix}{symbol}-abc",
    )


class FakeClient:
    """Records submitted and canceled orders; cancels confirm immediately."""

    def __init__(self, open_orders=()):
        self.open_orders = list(open_orders)
        self.submitted = []
        self.canceled = []

    def get_orders(self, request):
        return self.open_orders

    def submit_order(self, request):
        self.submitted.append(request)
        return SimpleNamespace(id="new-stop")

    def cancel_order_by_id(self, order_id):
        self.canceled.append(order_id)

    def get_order_by_id(self, order_id):
        return SimpleNamespace(status=SimpleNamespace(value="canceled"))


class TestEnsureBackupStop(unittest.TestCase):
    def test_places_gtc_stop_at_stop_loss_level(self):
        client = FakeClient()

        backup_stops.ensure_backup_stop(client, "AAPL", qty=10, entry_price=100.0, stop_pct=-0.05, existing=[])

        (order,) = client.submitted
        self.assertEqual(order.stop_price, 95.0)
        self.assertEqual(order.qty, 10)
        self.assertEqual(order.side, OrderSide.SELL)
        self.assertEqual(order.time_in_force, TimeInForce.GTC)
        self.assertTrue(order.client_order_id.startswith(backup_stops.BACKUP_STOP_ID_PREFIX))

    def test_matching_stop_left_alone(self):
        client = FakeClient()

        backup_stops.ensure_backup_stop(
            client, "AAPL", qty=10, entry_price=100.0, stop_pct=-0.05, existing=[stop_order()]
        )

        self.assertEqual(client.submitted, [])
        self.assertEqual(client.canceled, [])

    def test_moves_to_breakeven(self):
        client = FakeClient()

        backup_stops.ensure_backup_stop(
            client, "AAPL", qty=10, entry_price=100.0, stop_pct=0.0, existing=[stop_order()]
        )

        self.assertEqual(client.canceled, ["stop-1"])
        self.assertEqual(client.submitted[0].stop_price, 100.0)

    def test_resizes_when_position_grows(self):
        client = FakeClient()

        backup_stops.ensure_backup_stop(
            client, "AAPL", qty=15, entry_price=100.0, stop_pct=-0.05, existing=[stop_order()]
        )

        self.assertEqual(client.canceled, ["stop-1"])
        self.assertEqual(client.submitted[0].qty, 15)

    def test_duplicate_stops_collapsed_to_one(self):
        client = FakeClient()
        existing = [stop_order(order_id="a"), stop_order(order_id="b")]

        backup_stops.ensure_backup_stop(client, "AAPL", qty=10, entry_price=100.0, stop_pct=-0.05, existing=existing)

        self.assertEqual(client.canceled, ["a", "b"])
        self.assertEqual(len(client.submitted), 1)

    def test_fractional_qty_rounded_down(self):
        client = FakeClient()

        backup_stops.ensure_backup_stop(client, "AAPL", qty=10.7, entry_price=100.0, stop_pct=-0.05, existing=[])

        self.assertEqual(client.submitted[0].qty, 10)

    def test_under_one_share_skipped(self):
        client = FakeClient()

        backup_stops.ensure_backup_stop(client, "AAPL", qty=0.4, entry_price=100.0, stop_pct=-0.05, existing=[])

        self.assertEqual(client.submitted, [])

    def test_stop_price_rounded_to_cents(self):
        client = FakeClient()

        backup_stops.ensure_backup_stop(client, "SPY", qty=12, entry_price=778.49, stop_pct=-0.05, existing=[])

        self.assertEqual(client.submitted[0].stop_price, 739.57)

    def test_alpaca_error_is_logged_not_raised(self):
        client = FakeClient()
        client.submit_order = mock.Mock(side_effect=RuntimeError("insufficient qty"))

        with self.assertLogs(backup_stops.logger, "ERROR"):
            backup_stops.ensure_backup_stop(client, "AAPL", qty=10, entry_price=100.0, stop_pct=-0.05, existing=[])


class TestGetAndCancel(unittest.TestCase):
    def test_only_tagged_orders_are_backup_stops(self):
        manual = stop_order(order_id="manual", prefix="my-own-")
        client = FakeClient([stop_order(), manual])

        stops = backup_stops.get_backup_stops(client)

        self.assertEqual([o.id for o in stops["AAPL"]], ["stop-1"])

    def test_cancel_waits_for_confirmation(self):
        client = FakeClient()

        backup_stops.cancel_backup_stops(client, "AAPL", [stop_order()])

        self.assertEqual(client.canceled, ["stop-1"])

    def test_cancel_never_touches_manual_orders(self):
        client = FakeClient([stop_order(order_id="manual", prefix="my-own-")])

        backup_stops.cancel_backup_stops(client, "AAPL")

        self.assertEqual(client.canceled, [])


if __name__ == "__main__":
    unittest.main()
