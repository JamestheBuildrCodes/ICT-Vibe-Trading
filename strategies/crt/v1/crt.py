
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

    def is_valid(self) -> bool:
        """Check that the candle has a coherent high/low range."""
        return (
            self.high >= self.low
            and self.low <= self.close <= self.high
        )


def detect_crt_direction(
    c1: Candle,
    c2: Candle,
) -> Optional[Direction]:
    """Detect a valid CRT C1/C2 sweep-and-close-inside setup.

    LONG:
        C2 sweeps below C1.low and closes strictly inside C1's range.

    SHORT:
        C2 sweeps above C1.high and closes strictly inside C1's range.

    Reject:
        - Invalid candle ranges.
        - A C2 close on or outside either C1 boundary.
        - A C2 candle sweeping both sides of C1.
        - Candles that do not sweep either C1 boundary.
    """
    if not c1.is_valid() or not c2.is_valid():
        return None

    if c1.high <= c1.low:
        return None

    # The close must be strictly inside the complete C1 range.
    if not (c1.low < c2.close < c1.high):
        return None

    swept_low = c2.low < c1.low
    swept_high = c2.high > c1.high

    # Reject double-sided sweeps as ambiguous.
    if swept_low and swept_high:
        return None

    if swept_low:
        return Direction.LONG

    if swept_high:
        return Direction.SHORT

    return None


def is_c3_execution_window(candle_number: int) -> bool:
    """C3 is the third candle and the only canonical execution window."""
    return candle_number == 3


def crt_stop_from_first_execution_candle(
    direction: Direction,
    first_execution_candle: Candle,
) -> float:
    """Return the stop anchor from the first selected execution candle.

    LONG: stop anchor is the candle low.
    SHORT: stop anchor is the candle high.

    The caller must supply the first candle of the selected execution
    timeframe. A later candle must not replace this anchor.
    """
    if not first_execution_candle.is_valid():
        raise ValueError("Invalid first execution candle.")

    if direction == Direction.LONG:
        return first_execution_candle.low

    if direction == Direction.SHORT:
        return first_execution_candle.high

    raise ValueError(f"Unsupported direction: {direction!r}")
