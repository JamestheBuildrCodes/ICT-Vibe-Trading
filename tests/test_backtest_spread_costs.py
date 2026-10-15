from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace

from backtest import Bar, apply_costs, load_csv_bars, simulate_exit, summarize
from crt_strategy import Candle


UTC = timezone.utc


def test_csv_without_spread_column_marks_spread_unavailable(tmp_path: Path):
    path = tmp_path / "EURUSD_M5.csv"
    path.write_text(
        "timestamp,open,high,low,close\n"
        "2026-01-01T00:00:00Z,1.1000,1.1010,1.0990,1.1005\n",
        encoding="utf-8",
    )

    bars = load_csv_bars(path)

    assert len(bars) == 1
    assert bars[0].spread_price is None


def test_missing_spread_does_not_become_zero_transaction_cost():
    entry_time = datetime(2026, 1, 1, 0, 15, tzinfo=UTC)
    exit_time = entry_time + timedelta(minutes=10)
    trade = SimpleNamespace(
        gross_r=2.0,
        entry_time=entry_time,
        exit_time=exit_time,
        risk_distance=0.001,
        spread_cost_r=0.0,
        commission_r=None,
        slippage_r=None,
        total_cost_r=0.0,
        net_r=2.0,
    )

    apply_costs(
        trade,
        {entry_time: None, entry_time + timedelta(minutes=5): 0.0002},
        commission_r=0.0,
        slippage_r=0.0,
    )

    assert trade.spread_cost_r is None
    assert trade.total_cost_r is None
    assert trade.net_r is None


def test_explicit_zero_spread_is_not_treated_as_missing():
    entry_time = datetime(2026, 1, 1, 0, 15, tzinfo=UTC)
    exit_time = entry_time + timedelta(minutes=5)
    trade = SimpleNamespace(
        gross_r=2.0,
        entry_time=entry_time,
        exit_time=exit_time,
        risk_distance=0.001,
        spread_cost_r=None,
        commission_r=None,
        slippage_r=None,
        total_cost_r=None,
        net_r=None,
    )

    apply_costs(
        trade,
        {entry_time: 0.0, entry_time + timedelta(minutes=5): 0.0},
        commission_r=0.0,
        slippage_r=0.0,
    )

    assert trade.spread_cost_r == 0.0
    assert trade.total_cost_r == 0.0
    assert trade.net_r == 2.0



def test_generic_spread_column_is_rejected_as_unit_ambiguous(tmp_path: Path):
    path = tmp_path / "EURUSD_M5.csv"
    path.write_text(
        "timestamp,open,high,low,close,spread\n"
        "2026-01-01T00:00:00Z,1.1000,1.1010,1.0990,1.1005,15\n",
        encoding="utf-8",
    )

    import pytest

    with pytest.raises(ValueError, match="Ambiguous 'spread' column"):
        load_csv_bars(path)


def test_spread_points_are_converted_using_explicit_point_size(tmp_path: Path):
    path = tmp_path / "EURUSD_M5.csv"
    path.write_text(
        "timestamp,open,high,low,close,spread_points,point_size\n"
        "2026-01-01T00:00:00Z,1.1000,1.1010,1.0990,1.1005,15,0.00001\n",
        encoding="utf-8",
    )

    bars = load_csv_bars(path)

    assert bars[0].spread_price == 0.00015


def test_summary_does_not_claim_full_net_r_when_closed_trade_cost_is_missing():
    trade = SimpleNamespace(
        gross_r=2.0,
        net_r=None,
        spread_cost_r=None,
        commission_r=0.0,
        slippage_r=0.0,
        exit_time=datetime(2026, 1, 1, 1, tzinfo=UTC),
        same_bar_ambiguity=False,
        exit_reason="TP",
    )

    report = summarize([trade], Counter())

    assert "Closed missing spread costs:  1" in report
    assert "Net R (all closed trades):    UNAVAILABLE" in report
    assert "Net R (known-cost subset):    +0.00R (0 trades)" in report



def test_exit_simulation_does_not_use_m5_bar_after_test_end():
    entry_time = datetime(2026, 1, 1, 0, 15, tzinfo=UTC)
    trade = SimpleNamespace(
        entry_time=entry_time,
        direction="LONG",
        stop_loss=1.0990,
        take_profit=1.1020,
        exit_time=None,
        exit_price=None,
        exit_reason="OPEN_AT_END",
        gross_r=None,
        same_bar_ambiguity=False,
    )
    # This bar hits TP, but it starts at the exclusive test cutoff.
    m5 = [
        Bar(Candle(entry_time, 1.1000, 1.1030, 1.0995, 1.1025), 0.0001)
    ]

    result = simulate_exit(trade, m5, test_end=entry_time)

    assert result.exit_time is None
    assert result.gross_r is None
    assert result.exit_reason == "OPEN_AT_END"


def test_exit_simulation_does_not_use_m5_bar_that_closes_after_test_end():
    entry_time = datetime(2026, 1, 1, 0, 10, tzinfo=UTC)
    test_end = datetime(2026, 1, 1, 0, 12, tzinfo=UTC)
    trade = SimpleNamespace(
        entry_time=entry_time,
        direction="LONG",
        stop_loss=1.0990,
        take_profit=1.1020,
        exit_time=None,
        exit_price=None,
        exit_reason="OPEN_AT_END",
        gross_r=None,
        same_bar_ambiguity=False,
    )
    # Candle opened before cutoff but closes at 00:15, beyond test_end.
    m5 = [
        Bar(Candle(entry_time, 1.1000, 1.1030, 1.0995, 1.1025), 0.0001)
    ]

    result = simulate_exit(trade, m5, test_end=test_end)

    assert result.exit_time is None
    assert result.gross_r is None
    assert result.exit_reason == "OPEN_AT_END"
