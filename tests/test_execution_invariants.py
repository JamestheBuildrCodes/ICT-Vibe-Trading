from datetime import datetime, timezone

from backtest import Trade, Bar, simulate_exit, apply_costs
from crt3c_strategy import FIXED_RR
from dataclasses import replace

UTC = timezone.utc


def make_trade(direction="long"):
    return Trade(
        trade_id="T1", symbol="TEST", setup_id="S1", direction=direction,
        session="london_am", source_timeframe="M15", stop_source="C3_RUNNING_EXTREME",
        c3_start=datetime(2026, 1, 1, 8, tzinfo=UTC),
        c3_end=datetime(2026, 1, 1, 12, tzinfo=UTC),
        reaction_time=datetime(2026, 1, 1, 8, 30, tzinfo=UTC),
        entry_time=datetime(2026, 1, 1, 8, 35, tzinfo=UTC),
        entry_price=1.1050, stop_loss=1.1000, take_profit=1.1150,
        risk_distance=0.0050,
        parent_fvg_lower=1.103, parent_fvg_upper=1.105,
        execution_fvg_lower=1.103, execution_fvg_upper=1.105,
        execution_fvg_formed_time=datetime(2026, 1, 1, 7, 45, tzinfo=UTC),
        execution_fvg_third_high=1.109, execution_fvg_third_low=1.100,
        used_5m_refinement=False,
    )


def test_pure_sl_is_minus_one_r():
    t = make_trade("long")
    from crt_strategy import Candle
    bars = [Bar(Candle(datetime(2026, 1, 1, 8, 35, tzinfo=UTC), 1.1050, 1.1060, 1.0990, 1.1040))]
    simulate_exit(t, bars)
    assert t.exit_reason == "SL"
    assert t.gross_r == -1.0


def test_pure_tp_is_plus_two_r():
    t = make_trade("long")
    from crt_strategy import Candle
    bars = [Bar(Candle(datetime(2026, 1, 1, 8, 35, tzinfo=UTC), 1.1050, 1.1160, 1.1040, 1.1150))]
    simulate_exit(t, bars)
    assert t.exit_reason == "TP"
    assert t.gross_r == FIXED_RR


def test_same_bar_is_ambiguous_not_fabricated_loss():
    t = make_trade("long")
    from crt_strategy import Candle
    bars = [Bar(Candle(datetime(2026, 1, 1, 8, 35, tzinfo=UTC), 1.1050, 1.1160, 1.0990, 1.1100))]
    simulate_exit(t, bars)
    assert t.same_bar_ambiguity is True
    assert t.gross_r is None


def test_costs_are_separate_from_gross_r():
    t = make_trade("long")
    t.exit_reason = "TP"
    t.exit_time = datetime(2026, 1, 1, 9, tzinfo=UTC)
    t.gross_r = 2.0
    spreads = {t.reaction_time: 0.00010, datetime(2026, 1, 1, 8, 55, tzinfo=UTC): 0.00010}
    apply_costs(t, spreads, commission_r=0.05, slippage_r=0.02)
    assert t.gross_r == 2.0
    assert t.net_r == 2.0 - t.total_cost_r
    assert t.total_cost_r > 0
