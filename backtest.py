"""CRT-3C-v1.0 historical backtester.

Canonical flow:
    4H C1 range -> 4H C2 sweep/close inside -> C3 window
    -> pre-existing aligned M15 FVG -> reaction during C3
    -> optional nested M5 refinement -> entry -> structural SL -> 2R TP

The backtester accepts frozen CSV exports from MT5 and can fall back to a
live MT5 history pull when --data-dir is omitted. It reports gross R and
transaction costs separately so accounting can be audited.
"""

from __future__ import annotations

import argparse
import csv
import math
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from crt_strategy import Candle, Direction
from crt3c_strategy import (
    H4_MINUTES,
    M5_MINUTES,
    FIXED_RR,
    CRT3CSignal,
    CRT3CSetup,
    detect_crt3c,
    find_crt3c_signal_in_c3,
)
from session_manager import Session

NY_TZ = ZoneInfo("America/New_York")

DEFAULT_SYMBOLS = [
    "EURUSD",
    "GBPUSD",
    "EURCHF",
    "GBPCHF",
    "EURNZD",
    "GBPNZD",
    "EURCAD",
    "GBPCAD",
]

TIMEFRAME_MINUTES = {"M5": 5, "M15": 15, "H4": 240}


@dataclass(frozen=True)
class Bar:
    candle: Candle
    # None means the source did not provide a usable spread observation.
    spread_price: Optional[float] = None


@dataclass
class Trade:
    trade_id: str
    symbol: str
    setup_id: str
    direction: str
    session: str
    source_timeframe: str
    stop_source: str
    c3_start: datetime
    c3_end: datetime
    reaction_time: datetime
    entry_time: datetime
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_distance: float
    parent_fvg_lower: float
    parent_fvg_upper: float
    execution_fvg_lower: float
    execution_fvg_upper: float
    execution_fvg_formed_time: datetime
    execution_fvg_third_high: float
    execution_fvg_third_low: float
    used_5m_refinement: bool
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    exit_reason: str = "OPEN_AT_END"
    gross_r: Optional[float] = None
    spread_cost_r: Optional[float] = None
    commission_r: Optional[float] = None
    slippage_r: Optional[float] = None
    total_cost_r: Optional[float] = None
    net_r: Optional[float] = None
    same_bar_ambiguity: bool = False


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def parse_datetime(value: str) -> datetime:
    raw = value.strip()
    if not raw:
        raise ValueError("empty timestamp")
    try:
        number = float(raw)
        if number > 10_000_000_000:
            number /= 1000.0
        return datetime.fromtimestamp(number, tz=timezone.utc)
    except ValueError:
        pass

    normalized = raw.replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _column(row: dict[str, str], *names: str) -> Optional[str]:
    lower = {k.lower().strip(): v for k, v in row.items()}
    for name in names:
        value = lower.get(name.lower())
        if value not in (None, ""):
            return value
    return None


def load_csv_bars(path: Path) -> list[Bar]:
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        headers = {name.lower().strip() for name in (reader.fieldnames or [])}
        rows = list(reader)

    has_spread_price = "spread_price" in headers
    has_spread_points = "spread_points" in headers
    has_ambiguous_spread = "spread" in headers
    if has_spread_price and has_spread_points:
        raise ValueError(
            f"Ambiguous spread columns in {path}: use either spread_price or "
            "spread_points, not both"
        )
    if has_ambiguous_spread:
        raise ValueError(
            f"Ambiguous 'spread' column in {path}. Remove it and use only "
            "'spread_price' for price differences, or 'spread_points' with a "
            "positive 'point_size' column for broker points."
        )

    bars: list[Bar] = []
    for row in rows:
        ts = _column(row, "timestamp", "time_utc", "time", "datetime", "date")
        op = _column(row, "open")
        hi = _column(row, "high")
        lo = _column(row, "low")
        cl = _column(row, "close")
        if None in (ts, op, hi, lo, cl):
            raise ValueError(f"Missing required OHLC columns in {path}")

        spread_price = None
        if has_spread_price:
            spread_raw = _column(row, "spread_price")
            spread_price = float(spread_raw) if spread_raw not in (None, "") else None
        elif has_spread_points:
            points_raw = _column(row, "spread_points")
            point_size_raw = _column(row, "point_size", "symbol_point")
            if points_raw not in (None, ""):
                if point_size_raw in (None, ""):
                    raise ValueError(
                        f"'spread_points' requires a positive 'point_size' or "
                        f"'symbol_point' column in {path}"
                    )
                points = float(points_raw)
                point_size = float(point_size_raw)
                if (
                    not math.isfinite(points) or points < 0
                    or not math.isfinite(point_size) or point_size <= 0
                ):
                    raise ValueError(
                        f"Invalid spread_points/point_size in {path}: "
                        f"{points!r}/{point_size!r}"
                    )
                spread_price = points * point_size

        if spread_price is not None and (
            not math.isfinite(spread_price) or spread_price < 0
        ):
            raise ValueError(f"Invalid spread value in {path}: {spread_price!r}")
        candle = Candle(
            time=parse_datetime(ts),
            open=float(op),
            high=float(hi),
            low=float(lo),
            close=float(cl),
        )
        bars.append(Bar(candle, spread_price))

    bars.sort(key=lambda b: b.candle.time)
    validate_bars(path, [b.candle for b in bars])
    return bars


def validate_bars(path: Path, candles: list[Candle]) -> None:
    if not candles:
        raise ValueError(f"No bars in {path}")
    prev = None
    for i, c in enumerate(candles):
        if c.high < c.low or c.open <= 0 or c.close <= 0:
            raise ValueError(f"Malformed OHLC in {path} at row {i}")
        if prev is not None and c.time <= prev:
            raise ValueError(f"Non-monotonic/duplicate timestamps in {path} at row {i}")
        prev = c.time


def find_csv(data_dir: Path, symbol: str, timeframe: str) -> Path:
    candidates = [
        f"{symbol}_{timeframe}.csv",
        f"{symbol}.{timeframe}.csv",
        f"{symbol}-{timeframe}.csv",
        f"{symbol}{timeframe}.csv",
    ]
    for name in candidates:
        path = data_dir / name
        if path.exists():
            return path

    sym_upper = symbol.upper()
    tf_upper = timeframe.upper()
    matches = [
        p for p in data_dir.glob("*.csv")
        if sym_upper in p.stem.upper() and tf_upper in p.stem.upper()
    ]
    if matches:
        return sorted(matches)[0]
    raise FileNotFoundError(f"No CSV found for {symbol} {timeframe} in {data_dir}")


def load_bundle_from_csv(data_dir: Path, symbol: str) -> tuple[list[Bar], list[Bar], list[Bar]]:
    return (
        load_csv_bars(find_csv(data_dir, symbol, "M5")),
        load_csv_bars(find_csv(data_dir, symbol, "M15")),
        load_csv_bars(find_csv(data_dir, symbol, "H4")),
    )


# ---------------------------------------------------------------------------
# MT5 fallback
# ---------------------------------------------------------------------------


def resolve_mt5_symbol(mt5, requested: str) -> str:
    candidates = [requested, requested + ".", requested + ".m", requested + "m", requested + ".raw"]
    for candidate in candidates:
        info = mt5.symbol_info(candidate)
        if info is not None:
            if not info.visible:
                mt5.symbol_select(candidate, True)
            return candidate
    symbols = mt5.symbols_get() or []
    close = [s.name for s in symbols if requested.upper() in s.name.upper()]
    raise RuntimeError(f"Could not resolve MT5 symbol {requested}; matches={close[:10]}")


def mt5_load_bundle(
    mt5,
    requested_symbol: str,
    start: datetime,
    end: datetime,
) -> tuple[list[Bar], list[Bar], list[Bar], str]:
    symbol = resolve_mt5_symbol(mt5, requested_symbol)
    info = mt5.symbol_info(symbol)
    point = float(getattr(info, "point", 0.0) or 0.0)

    def fetch(tf_const: int, minutes: int) -> list[Bar]:
        rates = mt5.copy_rates_range(symbol, tf_const, start, end)
        if rates is None:
            raise RuntimeError(f"History fetch failed for {symbol} {minutes}m: {mt5.last_error()}")
        out = []
        for r in rates:
            # MetaTrader5 Python rate timestamps are Unix timestamps in UTC.
            # Do not apply a second broker-offset adjustment.
            raw_time = datetime.fromtimestamp(int(r["time"]), tz=timezone.utc)
            out.append(Bar(
                Candle(
                    raw_time,
                    float(r["open"]), float(r["high"]),
                    float(r["low"]), float(r["close"]),
                ),
                float(r["spread"]) * point if point > 0 else None,
            ))
        out.sort(key=lambda b: b.candle.time)
        validate_bars(Path(f"MT5:{symbol}:{minutes}"), [b.candle for b in out])
        return out

    import MetaTrader5 as _mt5
    return (
        fetch(_mt5.TIMEFRAME_M5, 5),
        fetch(_mt5.TIMEFRAME_M15, 15),
        fetch(_mt5.TIMEFRAME_H4, 240),
        symbol,
    )


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------


def _trade_from_signal(symbol: str, signal: CRT3CSignal, trade_id: str) -> Trade:
    execution = signal.execution_fvg
    return Trade(
        trade_id=trade_id,
        symbol=symbol,
        setup_id=signal.setup_id,
        direction=signal.direction.value,
        session=signal.session.value,
        source_timeframe=signal.source_timeframe,
        stop_source=signal.stop_source,
        c3_start=signal.c3_start,
        c3_end=signal.c3_end,
        reaction_time=signal.reaction_time,
        entry_time=signal.entry_time,
        entry_price=signal.entry_price,
        stop_loss=signal.stop_loss,
        take_profit=signal.take_profit,
        risk_distance=signal.risk_distance,
        parent_fvg_lower=signal.parent_15m_fvg.lower,
        parent_fvg_upper=signal.parent_15m_fvg.upper,
        execution_fvg_lower=execution.lower,
        execution_fvg_upper=execution.upper,
        execution_fvg_formed_time=execution.formed_at,
        execution_fvg_third_high=execution.third_candle.high,
        execution_fvg_third_low=execution.third_candle.low,
        used_5m_refinement=signal.nested_5m_fvg is not None,
    )


def simulate_exit(
    trade: Trade,
    m5: list[Bar],
    test_end: Optional[datetime] = None,
) -> Trade:
    """Simulate M5 exits without using candles that close after the test window."""
    for bar in m5:
        c = bar.candle
        if c.time < trade.entry_time:
            continue
        # A bar that closes after test_end contains out-of-window information,
        # even if the bar opened before the cutoff.
        if test_end is not None and c.time + timedelta(minutes=M5_MINUTES) > test_end:
            break

        if trade.direction == Direction.LONG.value:
            hit_sl = c.low <= trade.stop_loss
            hit_tp = c.high >= trade.take_profit
        else:
            hit_sl = c.high >= trade.stop_loss
            hit_tp = c.low <= trade.take_profit

        if not hit_sl and not hit_tp:
            continue

        trade.exit_time = c.time + timedelta(minutes=M5_MINUTES)
        if hit_sl and hit_tp:
            trade.exit_reason = "AMBIGUOUS_SAME_BAR"
            trade.same_bar_ambiguity = True
            trade.exit_price = None
            trade.gross_r = None
            return trade

        if hit_sl:
            trade.exit_reason = "SL"
            trade.exit_price = trade.stop_loss
            trade.gross_r = -1.0
        else:
            trade.exit_reason = "TP"
            trade.exit_price = trade.take_profit
            trade.gross_r = FIXED_RR
        return trade

    return trade


def apply_costs(
    trade: Trade,
    spread_by_time: dict[datetime, Optional[float]],
    commission_r: float,
    slippage_r: float,
) -> None:
    if trade.gross_r is None:
        return

    # Spread samples are timestamped at M5 candle open. Entry occurs just
    # after a completed M15 candle, aligned to an M5 open. Exit times are
    # stored as candle-close times, so map back to the triggering M5 open.
    entry_spread = spread_by_time.get(trade.entry_time)
    exit_bar_time = (
        trade.exit_time - timedelta(minutes=M5_MINUTES)
        if trade.exit_time is not None
        else None
    )
    exit_spread = spread_by_time.get(exit_bar_time) if exit_bar_time else None

    # Do not silently turn missing spread observations into free execution.
    if (
        entry_spread is None
        or exit_spread is None
        or not math.isfinite(entry_spread)
        or not math.isfinite(exit_spread)
        or entry_spread < 0
        or exit_spread < 0
    ):
        trade.spread_cost_r = None
        trade.commission_r = commission_r
        trade.slippage_r = slippage_r
        trade.total_cost_r = None
        trade.net_r = None
        return

    avg_spread = max(0.0, (entry_spread + exit_spread) / 2.0)
    trade.spread_cost_r = avg_spread / trade.risk_distance if trade.risk_distance > 0 else 0.0
    trade.commission_r = commission_r
    trade.slippage_r = slippage_r
    trade.total_cost_r = (trade.spread_cost_r or 0.0) + commission_r + slippage_r
    trade.net_r = trade.gross_r - trade.total_cost_r


def calculate_drawdown(values: list[float]) -> float:
    equity = peak = max_dd = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return max_dd


def run_symbol_backtest(
    symbol: str,
    m5: list[Bar],
    m15: list[Bar],
    h4: list[Bar],
    test_start: datetime,
    test_end: datetime,
    commission_r: float,
    slippage_r: float,
    allow_5m_refinement: bool = False,
) -> tuple[list[Trade], Counter]:
    # Compatibility parameter retained for callers; this benchmark is strictly
    # M15-entry-only, so M5 refinement is intentionally never enabled.
    _ = allow_5m_refinement
    # Historical bars are fixed during a backtest. Immutable tuples allow
    # the strategy engine to safely reuse their candle fingerprints.
    m5_candles = tuple(b.candle for b in m5)
    m15_candles = tuple(b.candle for b in m15)
    h4_candles = tuple(b.candle for b in h4)
    spread_by_time = {b.candle.time: b.spread_price for b in m5}

    rejection = Counter()
    trades: list[Trade] = []
    seen_setups: set[str] = set()
    active_trade: Optional[Trade] = None

    for i in range(2, len(h4_candles)):
        c1, c2, c3 = h4_candles[i - 2], h4_candles[i - 1], h4_candles[i]
        c3_start = c3.time
        c3_end = c3.time + timedelta(minutes=H4_MINUTES)
        if c3_end <= test_start or c3_start >= test_end:
            continue

        setup = detect_crt3c(c1, c2, c3_start)
        if setup is None:
            rejection["NO_CRT_OR_BAD_H4_SEQUENCE"] += 1
            continue
        rejection["CRT_VALID"] += 1

        if setup.setup_id in seen_setups:
            rejection["DUPLICATE_SETUP"] += 1
            continue
        seen_setups.add(setup.setup_id)

        # Determine whether an aligned, valid M15 FVG existed strictly before C3.
        from crt3c_strategy import preexisting_valid_fvgs
        parents = preexisting_valid_fvgs(m15_candles, setup.direction, setup.c3_start, 15)
        if not parents:
            rejection["NO_PREEXISTING_15M_FVG"] += 1
            continue

        signal = find_crt3c_signal_in_c3(
            setup,
            m15_candles,
            m5_candles,
            evaluation_end=min(test_end, c3_end),

            allow_5m_refinement=False,
            rr=FIXED_RR,
        )
        if signal is None:
            rejection["NO_FVG_REACTION_OR_VALID_EXECUTION"] += 1
            continue

        # Only count entries whose actual entry time falls inside the test window.
        if signal.entry_time < test_start:
            rejection["ENTRY_BEFORE_TEST_START"] += 1
            continue
        if signal.entry_time >= test_end:
            rejection["ENTRY_AT_OR_AFTER_TEST_END"] += 1
            break

        # Enforce one open position per symbol. A candidate arising while the
        # previous trade is still open is measured as an overlap, not silently
        # counted as another independent position.
        if active_trade is not None:
            if active_trade.exit_time is None or signal.entry_time < active_trade.exit_time:
                rejection["OVERLAPPING_TRADE_CANDIDATE"] += 1
                continue
            active_trade = None

        trade = _trade_from_signal(symbol, signal, f"{symbol}-{len(trades)+1:05d}")
        trade = simulate_exit(trade, m5, test_end=test_end)
        apply_costs(trade, spread_by_time, commission_r, slippage_r)

        trades.append(trade)
        if trade.same_bar_ambiguity:
            # Do not invent an outcome for a candle that hit both SL and TP.
            rejection["AMBIGUOUS_SAME_BAR"] += 1

        # Retain the last trade even when it already has a simulated exit.
        # The next candidate must occur after that exit to avoid overlap.
        active_trade = trade

        if signal.entry_time >= test_end:
            break

    return trades, rejection


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def write_trade_csv(path: Path, trades: list[Trade]) -> None:
    fields = [
        "trade_id", "symbol", "setup_id", "direction", "session", "source_timeframe", "stop_source",
        "c3_start", "c3_end", "reaction_time", "entry_time", "entry_price", "stop_loss",
        "take_profit", "risk_distance", "parent_fvg_lower", "parent_fvg_upper",
        "execution_fvg_lower", "execution_fvg_upper", "execution_fvg_formed_time",
        "execution_fvg_third_high", "execution_fvg_third_low", "used_5m_refinement",
        "exit_time", "exit_price", "exit_reason", "gross_r", "spread_cost_r",
        "commission_r", "slippage_r", "total_cost_r", "net_r", "same_bar_ambiguity",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for t in trades:
            row = {field: getattr(t, field) for field in fields}
            for key, value in row.items():
                if isinstance(value, datetime):
                    row[key] = value.isoformat()
            writer.writerow(row)


def summarize(trades: list[Trade], rejection: Counter) -> str:
    closed = [t for t in trades if t.gross_r is not None]
    ambiguous = [t for t in trades if t.same_bar_ambiguity]
    open_at_end = [t for t in trades if t.exit_time is None and not t.same_bar_ambiguity]
    wins = [t for t in closed if t.gross_r > 0]
    losses = [t for t in closed if t.gross_r < 0]

    gross_values = [t.gross_r for t in closed]
    cost_known = [t for t in closed if t.net_r is not None]
    cost_missing_count = len(closed) - len(cost_known)
    net_values = [t.net_r for t in cost_known]
    gross_win = sum(v for v in gross_values if v > 0)
    gross_loss = abs(sum(v for v in gross_values if v < 0))
    net_win = sum(v for v in net_values if v > 0)
    net_loss = abs(sum(v for v in net_values if v < 0))

    gross_pf = gross_win / gross_loss if gross_loss else math.inf
    net_pf_known_subset = net_win / net_loss if net_loss else math.inf
    gross_r = sum(gross_values)
    net_r_known_subset = sum(net_values)
    net_r_all_trades = (
        f"{net_r_known_subset:+.2f}R"
        if cost_missing_count == 0
        else f"UNAVAILABLE ({cost_missing_count} closed trade(s) missing spread cost)"
    )
    spread_cost_known = sum(t.spread_cost_r for t in cost_known)
    commission_cost_known = sum(t.commission_r or 0.0 for t in cost_known)
    slippage_cost_known = sum(t.slippage_r or 0.0 for t in cost_known)

    lines = [
        "CRT HUNTER - CRT-3C-v1.0 BACKTEST",
        "=" * 72,
        "",
        f"Total entered trades:        {len(trades)}",
        f"Closed with known outcome:    {len(closed)}",
        f"Closed with known costs:      {len(cost_known)}",
        f"Closed missing spread costs:  {cost_missing_count}",
        f"Ambiguous same-bar:           {len(ambiguous)}",
        f"Open at end:                  {len(open_at_end)}",
        f"Wins:                         {len(wins)}",
        f"Losses:                       {len(losses)}",
        f"Win rate (known outcomes):    {len(wins) / len(closed) * 100:.2f}%" if closed else "Win rate (known outcomes):    0.00%",
        "",
        f"Gross R (all known outcomes): {gross_r:+.2f}R",
        f"Spread cost R (known costs): {-spread_cost_known:+.2f}R",
        f"Commission R (known costs):  {-commission_cost_known:+.2f}R",
        f"Slippage R (known costs):    {-slippage_cost_known:+.2f}R",
        f"Net R (all closed trades):    {net_r_all_trades}",
        f"Net R (known-cost subset):    {net_r_known_subset:+.2f}R ({len(cost_known)} trades)",
        f"Gross profit factor:          {gross_pf:.3f}",
        f"Net PF (known-cost subset):   {net_pf_known_subset:.3f}",
        f"Gross max drawdown:            {calculate_drawdown(gross_values):.2f}R",
        f"Net DD (known-cost subset):    {calculate_drawdown(net_values):.2f}R",
        "",
        "FIXED-R INVARIANTS",
        f"TP gross outcomes at +{FIXED_RR:.1f}R: {sum(1 for t in closed if t.exit_reason == 'TP')}",
        f"SL gross outcomes at -1.0R:    {sum(1 for t in closed if t.exit_reason == 'SL')}",
        f"Gross accounting mismatches:  {sum(1 for t in closed if t.exit_reason == 'TP' and abs(t.gross_r - FIXED_RR) > 1e-9) + sum(1 for t in closed if t.exit_reason == 'SL' and abs(t.gross_r + 1.0) > 1e-9)}",
        "",
        "REJECTIONS",
        "-" * 72,
    ]
    for reason, count in rejection.most_common():
        lines.append(f"{reason}: {count}")

    lines += [
        "",
        "MODEL",
        "- 4H C1 range",
        "- 4H C2 sweep + close back inside",
        "- 4H C3 execution window only",
        "- directionally aligned M15 FVG must pre-exist C3",
        "- FVG touch/reaction must occur during C3",
        "- M15-only entry detection; M5 candles are used only to simulate exits",
        "- structural SL = execution FVG candle 3 low/high",
        "- fixed 1:2 target",
        "- London AM + New York AM + London PM entries only",
        "- ambiguous same-bar SL/TP is not assigned a fabricated result",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="CRT-3C-v1.0 backtest")
    parser.add_argument("--data-dir", type=Path, help="Frozen OHLC CSV directory")
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS), help="Comma-separated canonical symbols")
    parser.add_argument("--start", default="2026-01-01T00:00:00+00:00")
    parser.add_argument("--end", default=datetime.now(timezone.utc).isoformat())
    parser.add_argument("--commission-r", type=float, default=0.0)
    parser.add_argument("--slippage-r", type=float, default=0.0)
    parser.add_argument(
        "--no-5m-refinement",
        action="store_true",
        help="Deprecated compatibility flag; this backtest always uses M15-only entries.",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("backtest_crt3c"))
    args = parser.parse_args()

    test_start = parse_datetime(args.start)
    test_end = parse_datetime(args.end)
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    args.output_dir.mkdir(parents=True, exist_ok=True)

    all_trades: list[Trade] = []
    total_rejection = Counter()

    mt5 = None
    if args.data_dir is None:
        import MetaTrader5 as mt5_mod
        if not mt5_mod.initialize():
            raise RuntimeError(f"MT5 initialization failed: {mt5_mod.last_error()}")
        mt5 = mt5_mod

    try:
        for symbol in symbols:
            print(f"\n=== {symbol} ===")
            if args.data_dir is not None:
                m5_bars, m15_bars, h4_bars = load_bundle_from_csv(args.data_dir, symbol)
                broker_symbol = symbol
            else:
                m5_bars, m15_bars, h4_bars, broker_symbol = mt5_load_bundle(
                    mt5, symbol, test_start - timedelta(days=30), test_end
                )

            print(f"Loaded {broker_symbol}: M5={len(m5_bars)} M15={len(m15_bars)} H4={len(h4_bars)}")
            trades, rejection = run_symbol_backtest(
                symbol,
                m5_bars,
                m15_bars,
                h4_bars,
                test_start,
                test_end,

                commission_r=args.commission_r,
                slippage_r=args.slippage_r,
                allow_5m_refinement=False,
            )
            all_trades.extend(trades)
            total_rejection.update(rejection)
            print(f"Entered={len(trades)} | Known outcomes={sum(t.gross_r is not None for t in trades)}")
            write_trade_csv(args.output_dir / f"{symbol}_trades.csv", trades)

    finally:
        if mt5 is not None:
            mt5.shutdown()

    # Aggregate report; sort by exit time for equity calculations downstream.
    all_trades.sort(key=lambda t: (t.exit_time or datetime.max.replace(tzinfo=timezone.utc), t.trade_id))
    summary = summarize(all_trades, total_rejection)
    (args.output_dir / "summary.txt").write_text(summary, encoding="utf-8")
    write_trade_csv(args.output_dir / "all_trades.csv", all_trades)
    print("\n" + summary)
    print(f"\nOutput: {args.output_dir.resolve()}")


if __name__ == "__main__":
    main()






