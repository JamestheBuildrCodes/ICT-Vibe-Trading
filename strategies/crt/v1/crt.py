from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Direction(str, Enum):
    LONG = "long"
    SHORT = "short"


@dataclass(frozen=True)
class Candle:
    high: float
    low: float
    close: float


def detect_crt_direction(c1: Candle, c2: Candle) -> Optional[Direction]:
    """Canonical CRT C1/C2 sweep-and-close-inside detection."""
    bullish = c2.low < c1.low and c2.close > c1.low
    bearish = c2.high > c1.high and c2.close < c1.high

    if bullish and not bearish:
        return Direction.LONG
    if bearish and not bullish:
        return Direction.SHORT
    return None


def is_c3_execution_window(candle_number: int) -> bool:
    """C3 is the third candle and the only canonical execution window."""
    return candle_number == 3


def crt_stop_from_first_execution_candle(
    direction: Direction,
    first_execution_candle: Candle,
) -> float:
    """Return the CRT stop anchor from the first selected execution candle.

    Bullish CRT: SL = LOW of the first 15M/5M execution candle.
    Bearish CRT: SL = HIGH of the first 15M/5M execution candle.

    The caller must pass the first candle of the selected execution timeframe.
    No later candle may be substituted for this stop anchor.
    """
    if direction == Direction.LONG:
        return first_execution_candle.low
    return first_execution_candle.high
