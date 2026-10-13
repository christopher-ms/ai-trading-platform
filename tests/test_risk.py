"""Tests for src/trading/risk.py and the RISK_LEVEL dial in src/config.py."""

import unittest
from datetime import date, timedelta
from unittest import mock

from src import config
from src.trading import risk
from src.trading.risk import TradingSignal


def make_signal(action="BUY", confidence=0.95, symbol="AAPL") -> TradingSignal:
    return TradingSignal(
        action=action,
        symbol=symbol,
        confidence=confidence,
        reasoning="test",
        risk_level="MEDIUM",
    )


class RiskTestCase(unittest.TestCase):
    """Fresh daily state and an open market for every test."""

    def setUp(self):
        risk._daily_state = risk._DailyState()
        patcher = mock.patch.object(risk, "is_market_hours", return_value=True)
        self.market_hours = patcher.start()
        self.addCleanup(patcher.stop)

    def evaluate(self, signal=None, portfolio_value=100_000.0, open_positions=0, holds=False):
        return risk.evaluate_signal(
            signal or make_signal(),
            portfolio_value=portfolio_value,
            open_position_count=open_positions,
            already_holds_symbol=holds,
        )


class TestEvaluateSignal(RiskTestCase):
    def test_strong_buy_is_approved_and_capped_at_max_position_size(self):
        decision = self.evaluate(make_signal(confidence=0.95))

        self.assertTrue(decision.approved)
        self.assertAlmostEqual(decision.max_position_value, 100_000 * config.MAX_POSITION_SIZE_PCT)

    def test_hold_is_never_executed(self):
        self.assertFalse(self.evaluate(make_signal(action="HOLD", confidence=0.99)).approved)

    def test_rejected_when_market_closed(self):
        self.market_hours.return_value = False

        decision = self.evaluate()

        self.assertFalse(decision.approved)
        self.assertIn("closed", decision.reason)

    def test_market_closed_rejection_short_circuits_loss_limit(self):
        # check_daily_loss_limit sets the day's baseline as a side effect; it
        # must not run for a signal already rejected for being out of hours.
        self.market_hours.return_value = False

        self.evaluate(portfolio_value=50_000.0)

        self.assertIsNone(risk._daily_state.starting_portfolio_value)
        self.assertFalse(risk._daily_state.halted_for_loss)


class TestConfidenceThreshold(RiskTestCase):
    def test_below_threshold_rejected(self):
        decision = self.evaluate(make_signal(confidence=config.MIN_TRADE_CONFIDENCE - 0.01))

        self.assertFalse(decision.approved)
        self.assertIn("below the minimum", decision.reason)

    def test_exactly_at_threshold_approved(self):
        self.assertTrue(self.evaluate(make_signal(confidence=config.MIN_TRADE_CONFIDENCE)).approved)

    def test_highest_real_signal_so_far_would_not_trade(self):
        # ISSUES #13: the best natural signal seen so far was 0.62.
        self.assertFalse(self.evaluate(make_signal(confidence=0.62)).approved)


class TestDailyLossLimit(RiskTestCase):
    def test_first_value_of_the_day_becomes_baseline(self):
        self.evaluate(portfolio_value=100_000.0)

        self.assertEqual(risk._daily_state.starting_portfolio_value, 100_000.0)

    def test_loss_just_under_limit_still_trades(self):
        self.evaluate(portfolio_value=100_000.0)
        just_under = 100_000.0 * (1 - config.DAILY_LOSS_LIMIT_PCT) + 100

        self.assertTrue(self.evaluate(portfolio_value=just_under).approved)
        self.assertFalse(risk._daily_state.halted_for_loss)

    def test_loss_past_limit_halts(self):
        self.evaluate(portfolio_value=100_000.0)
        past_limit = 100_000.0 * (1 - config.DAILY_LOSS_LIMIT_PCT) - 100

        decision = self.evaluate(portfolio_value=past_limit)

        self.assertFalse(decision.approved)
        self.assertIn("daily loss limit", decision.reason)
        self.assertTrue(risk._daily_state.halted_for_loss)

    def test_halt_survives_an_intraday_recovery(self):
        self.evaluate(portfolio_value=100_000.0)
        self.evaluate(portfolio_value=90_000.0)

        decision = self.evaluate(portfolio_value=105_000.0)

        self.assertFalse(decision.approved)
        self.assertIn("already breached", decision.reason)

    def test_halt_blocks_even_the_strongest_signal(self):
        self.evaluate(portfolio_value=100_000.0)
        self.evaluate(portfolio_value=90_000.0)

        self.assertFalse(self.evaluate(make_signal(confidence=1.0), portfolio_value=90_000.0).approved)

    def test_halt_lifts_on_a_new_day(self):
        self.evaluate(portfolio_value=100_000.0)
        self.evaluate(portfolio_value=90_000.0)
        risk._daily_state.trading_date = risk._daily_state.trading_date - timedelta(days=1)

        decision = self.evaluate(portfolio_value=90_000.0)

        self.assertTrue(decision.approved)
        self.assertEqual(risk._daily_state.starting_portfolio_value, 90_000.0)

    def test_same_day_restart_restores_halt_from_dynamodb(self):
        # A fresh process (trading_date None) must reload halted_for_loss
        # rather than silently reopening trading on a halted day.
        saved = {"trades_executed": 3, "starting_portfolio_value": 100_000.0, "halted_for_loss": True}
        with mock.patch.object(risk.db, "load_daily_risk_state", return_value=saved):
            decision = self.evaluate(portfolio_value=100_000.0)

        self.assertFalse(decision.approved)
        self.assertEqual(risk._daily_state.trades_executed, 3)


class TestDailyTradeLimit(RiskTestCase):
    def test_trades_allowed_until_cap(self):
        for _ in range(config.MAX_DAILY_TRADES - 1):
            risk.record_trade_executed()

        self.assertTrue(self.evaluate().approved)

    def test_rejected_at_cap(self):
        for _ in range(config.MAX_DAILY_TRADES):
            risk.record_trade_executed()

        decision = self.evaluate()

        self.assertFalse(decision.approved)
        self.assertIn("maximum", decision.reason)

    def test_count_resets_on_a_new_day(self):
        for _ in range(config.MAX_DAILY_TRADES):
            risk.record_trade_executed()
        risk._daily_state.trading_date = date(2000, 1, 3)

        self.assertTrue(self.evaluate().approved)
        self.assertEqual(risk._daily_state.trades_executed, 0)


class TestPositionLimit(RiskTestCase):
    def test_new_symbol_rejected_at_max_open_positions(self):
        decision = self.evaluate(open_positions=config.MAX_OPEN_POSITIONS)

        self.assertFalse(decision.approved)
        self.assertIn("open positions", decision.reason)

    def test_new_symbol_allowed_below_max(self):
        self.assertTrue(self.evaluate(open_positions=config.MAX_OPEN_POSITIONS - 1).approved)

    def test_adding_to_held_symbol_allowed_at_max(self):
        self.assertTrue(self.evaluate(open_positions=config.MAX_OPEN_POSITIONS, holds=True).approved)

    def test_sell_allowed_at_max(self):
        signal = make_signal(action="SELL")

        self.assertTrue(self.evaluate(signal, open_positions=config.MAX_OPEN_POSITIONS, holds=True).approved)


class TestPositionSizing(unittest.TestCase):
    def test_confidence_tiers(self):
        cases = [
            (1.00, 1.0),
            (0.90, 1.0),
            (0.89, 0.75),
            (0.80, 0.75),
            (0.79, 0.5),
            (0.70, 0.5),
        ]
        for confidence, fraction in cases:
            with self.subTest(confidence=confidence):
                self.assertAlmostEqual(
                    risk.get_position_size_pct(confidence),
                    config.MAX_POSITION_SIZE_PCT * fraction,
                )

    def test_below_every_tier_sizes_like_lowest_tier(self):
        self.assertAlmostEqual(risk.get_position_size_pct(0.5), config.MAX_POSITION_SIZE_PCT * 0.5)

    def test_buy_rounds_down_to_whole_shares(self):
        # 10% of $100k = $10k; $10k / $778.49 = 12.8 -> 12 (the real 2026-10-09 SPY order).
        qty = risk.calculate_position_size(make_signal(confidence=0.9), 778.49, 100_000.0, 0)

        self.assertEqual(qty, 12.0)

    def test_buy_too_expensive_for_one_share_sizes_to_zero(self):
        self.assertEqual(risk.calculate_position_size(make_signal(confidence=0.7), 6_000.0, 100_000.0, 0), 0.0)

    def test_sell_closes_the_whole_position(self):
        self.assertEqual(risk.calculate_position_size(make_signal(action="SELL"), 100.0, 100_000.0, 37.5), 37.5)

    def test_non_positive_price_raises(self):
        for price in (0.0, -1.0):
            with self.subTest(price=price), self.assertRaises(ValueError):
                risk.calculate_position_size(make_signal(), price, 100_000.0, 0)


class TestTradingSignalValidation(unittest.TestCase):
    def test_invalid_values_rejected(self):
        for kwargs in ({"action": "SHORT"}, {"confidence": 1.5}, {"confidence": -0.1}):
            with self.subTest(**kwargs), self.assertRaises(ValueError):
                make_signal(**kwargs)

    def test_lowercase_normalized(self):
        signal = make_signal(action="buy", symbol="aapl")

        self.assertEqual((signal.action, signal.symbol), ("BUY", "AAPL"))


class TestRiskLevelDial(unittest.TestCase):
    def test_default_reproduces_original_hand_tuned_values(self):
        params = config.compute_risk_params(0.5)

        self.assertAlmostEqual(params.stop_loss_pct, 0.05)
        self.assertAlmostEqual(params.take_profit_pct, 0.15)
        self.assertAlmostEqual(params.min_confidence, 0.70)
        self.assertAlmostEqual(params.max_position_size_pct, 0.10)
        self.assertEqual(params.max_daily_trades, 10)
        self.assertAlmostEqual(params.daily_loss_limit_pct, 0.02)

    def test_extremes(self):
        low, high = config.compute_risk_params(0.0), config.compute_risk_params(1.0)

        self.assertAlmostEqual(low.stop_loss_pct, 0.02)
        self.assertAlmostEqual(high.stop_loss_pct, 0.10)
        self.assertAlmostEqual(low.min_confidence, 0.85)
        self.assertAlmostEqual(high.min_confidence, 0.55)

    def test_out_of_range_raises(self):
        for level in (-0.1, 1.1):
            with self.subTest(level=level), self.assertRaises(ValueError):
                config.compute_risk_params(level)


if __name__ == "__main__":
    unittest.main()
