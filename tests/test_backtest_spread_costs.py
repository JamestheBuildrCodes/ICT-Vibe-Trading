from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace

from backtest import Bar, apply_costs, load_csv_bars
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
    exit_time = entry_time + timedelta(minutes=5)
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
        {entry_time: None, exit_time - timedelta(minutes=5): 0.0002},
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
        {entry_time: 0.0, exit_time - timedelta(minutes=5): 0.0},
        commission_r=0.0,
        slippage_r=0.0,
    )

    assert trade.spread_cost_r == 0.0
    assert trade.total_cost_r == 0.0
    assert trade.net_r == 2.0
