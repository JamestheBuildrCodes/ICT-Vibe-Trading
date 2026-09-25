"""Mechanical research implementation of the TTrades Fractal Model.

The module is deliberately broker/data-source neutral. It consumes OHLCV bars and
returns candidate setups; execution/risk sizing belongs to the outer engine.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Literal, Optional

Direction = Literal["long", "short"]


@dataclass(frozen=True)
class Bar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class FVG:
    direction: Direction
    created_at: datetime
    low: float
    high: float

    def contains(self, price: float) -> bool:
        return self.low <= price <= self.high


@dataclass(frozen=True)
class ProtectedSwing:
    direction: Direction
    timestamp: datetime
    price: float
    cisd_level: float


@dataclass
class Setup:
    direction: Direction
    created_at: datetime
    entry: float
    stop: float
    target: float
    rr: float
    timeframe: str
    fvg: FVG
    protected_swing: ProtectedSwing
    confluences: dict[str, bool] = field(default_factory=dict)


@dataclass(frozen=True)
class TTFMConfig:
    execution_timeframe: Literal["5m", "15m"] = "15m"
    rr: float = 3.0
    fvg_entry: Literal["midpoint", "proximal", "first_touch"] = "midpoint"
    stop_buffer: float = 0.0
    session_timezone: str = "America/New_York"
    # New York local session windows. These are deliberately explicit and editable.
    london_am: tuple[time, time] = (time(2, 0), time(5, 0))
    london_pm: tuple[time, time] = (time(7, 0), time(10, 0))
    ny_am: tuple[time, time] = (time(7, 0), time(10, 0))


def fvg_from_three(a: Bar, b: Bar, c: Bar) -> Optional[FVG]:
    """Return a standard three-candle FVG created by c."""
    if a.high < c.low:
        return FVG("long", c.timestamp, a.high, c.low)
    if a.low > c.high:
        return FVG("short", c.timestamp, c.high, a.low)
    return None


def fvg_entry_price(fvg: FVG, mode: str) -> float:
    if mode == "proximal":
        return fvg.low if fvg.direction == "long" else fvg.high
    if mode == "first_touch":
        return fvg.high if fvg.direction == "long" else fvg.low
    return (fvg.low + fvg.high) / 2.0


def target_from_rr(direction: Direction, entry: float, stop: float, rr: float) -> float:
    risk = abs(entry - stop)
    if risk <= 0:
        raise ValueError("entry and stop must be different")
    return entry + rr * risk if direction == "long" else entry - rr * risk


def build_setup(
    *,
    direction: Direction,
    created_at: datetime,
    fvg: FVG,
    protected_swing: ProtectedSwing,
    config: TTFMConfig,
    structural_target: Optional[float] = None,
    confluences: Optional[dict[str, bool]] = None,
) -> Optional[Setup]:
    """Build a 3R setup only when the protected swing and FVG agree."""
    if fvg.direction != direction or protected_swing.direction != direction:
        return None

    entry = fvg_entry_price(fvg, config.fvg_entry)
    if direction == "long":
        stop = protected_swing.price - config.stop_buffer
        if stop >= entry:
            return None
        target = target_from_rr(direction, entry, stop, config.rr)
        if structural_target is not None and structural_target < target:
            return None
    else:
        stop = protected_swing.price + config.stop_buffer
        if stop <= entry:
            return None
        target = target_from_rr(direction, entry, stop, config.rr)
        if structural_target is not None and structural_target > target:
            return None

    return Setup(
        direction=direction,
        created_at=created_at,
        entry=entry,
        stop=stop,
        target=target,
        rr=config.rr,
        timeframe=config.execution_timeframe,
        fvg=fvg,
        protected_swing=protected_swing,
        confluences=confluences or {},
    )


def is_in_allowed_session(local_time: time, config: TTFMConfig) -> bool:
    """Session gate; caller must supply time already converted to config timezone."""
    for start, end in (config.london_am, config.london_pm, config.ny_am):
        if start <= local_time <= end:
            return True
    return False


def score_confluences(confluences: dict[str, bool]) -> int:
    """Score optional TTFM confluence layers; hard gates are handled elsewhere."""
    weights = {
        "smt": 2,
        "clean_protected_swing": 2,
        "fvg_at_poi": 1,
        "premium_discount": 1,
        "untouched_draw": 1,
        "early_4h_confirmation": 1,
        "displacement": 1,
    }
    return sum(weight for name, weight in weights.items() if confluences.get(name, False))


def is_a_plus(confluences: dict[str, bool], minimum_score: int = 6) -> bool:
    """Classify A+ from confluence quality without making SMT the foundation."""
    return score_confluences(confluences) >= minimum_score
