
"""CRT-3C-v1.0 strategy engine.

Canonical sequence:
    4H C1 range
      -> 4H C2 sweep + close back inside
      -> 4H C3 execution window
      -> pre-existing aligned 15M FVG
      -> price touch/reaction during C3
      -> optional pre-existing/nested 5M refinement
      -> entry after reaction-candle close
      -> structural C3 execution-FVG third-candle SL
      -> fixed 2R target

Strategy only: no broker I/O and no future-candle look-ahead.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Optional, Sequence

from crt_strategy import Candle, Direction
from session_manager import Session, current_session, to_ny_time

H4_MINUTES = 240
M15_MINUTES = 15
M5_MINUTES = 5
FIXED_RR = 2.0

ALLOWED_SESSIONS = frozenset({
    Session.LONDON_AM,
    Session.NEW_YORK_AM,
    Session.LONDON_PM,
})

# Caches are keyed by candle content, not id(candles).
_FVG_DETECTION_CACHE: dict[tuple, list["FVGZone"]] = {}
_FVG_VALID_UNTIL_CACHE: dict[tuple, dict[tuple, datetime]] = {}
# Retain immutable histories alongside their fingerprints. Keeping the tuple
# prevents object-id reuse and makes the cached fingerprint safe to reuse.
_IMMUTABLE_FINGERPRINT_CACHE: dict[int, tuple[tuple, str]] = {}
# Cache the sorted view of immutable input tuples so repeated strategy
# checks do not scan the entire history just to verify its ordering.
_SORTED_CANDLE_CACHE: dict[int, tuple[tuple, tuple]] = {}
# Cache timestamp indexes for immutable, ordered candle tuples.
_CANDLE_TIMES_CACHE: dict[int, tuple[tuple, tuple[datetime, ...]]] = {}
_MAX_CACHE_ENTRIES = 256


@dataclass(frozen=True)
class CRT3CSetup:
    c1: Candle
    c2: Candle
    c3_start: datetime
    c3_end: datetime
    direction: Direction

    @property
    def setup_id(self) -> str:
        return (
            f"{self.direction.value}|{self.c1.time.isoformat()}|"
            f"{self.c2.time.isoformat()}|{self.c3_start.isoformat()}"
        )


@dataclass(frozen=True)
class FVGZone:
    direction: Direction
    upper: float
    lower: float
    first_candle: Candle
    middle_candle: Candle
    third_candle: Candle
    timeframe_minutes: int

    @property
    def size(self) -> float:
        return self.upper - self.lower

    @property
    def formed_at(self) -> datetime:
        return candle_close_time(self.third_candle, self.timeframe_minutes)

    @property
    def ce(self) -> float:
        return (self.upper + self.lower) / 2.0


@dataclass(frozen=True)
class CRT3CSignal:
    setup_id: str
    direction: Direction
    c3_start: datetime
    c3_end: datetime
    reaction_time: datetime
    entry_time: datetime
    entry_price: float
    stop_loss: float
    take_profit: float
    source_timeframe: str
    stop_source: str
    execution_fvg: FVGZone
    parent_15m_fvg: FVGZone
    nested_5m_fvg: Optional[FVGZone]
    session: Session
    reason: str

    @property
    def risk_distance(self) -> float:
        return abs(self.entry_price - self.stop_loss)


def candle_close_time(candle: Candle, timeframe_minutes: int) -> datetime:
    return candle.time + timedelta(minutes=timeframe_minutes)


def _assert_aware(dt: datetime) -> None:
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError(f"Naive datetime is not allowed: {dt!r}")


def _utc_datetime(value: datetime) -> datetime:
    """Interpret naive timestamps as UTC; normalize aware timestamps to UTC."""
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _valid_candle(candle: Candle) -> bool:
    """Check OHLC geometry and finite, positive prices."""
    values = (candle.open, candle.high, candle.low, candle.close)

    if not all(isinstance(value, (int, float)) for value in values):
        return False

    # NaN fails these comparisons; infinities fail the range checks below.
    if not all(value > 0 and value < float("inf") for value in values):
        return False

    return (
        candle.high >= candle.low
        and candle.low <= candle.open <= candle.high
        and candle.low <= candle.close <= candle.high
    )


def _sorted_candles(candles: Sequence[Candle]) -> Sequence[Candle]:
    """Return a sorted view, reusing the result for immutable tuples."""
    if isinstance(candles, tuple):
        cached = _SORTED_CANDLE_CACHE.get(id(candles))
        if cached is not None and cached[0] is candles:
            return cached[1]

        is_ordered = all(
            candles[i - 1].time <= candles[i].time
            for i in range(1, len(candles))
        )
        ordered = candles if is_ordered else tuple(
            sorted(candles, key=lambda candle: candle.time)
        )

        if len(_SORTED_CANDLE_CACHE) >= _MAX_CACHE_ENTRIES:
            _SORTED_CANDLE_CACHE.clear()
        _SORTED_CANDLE_CACHE[id(candles)] = (candles, ordered)
        return ordered

    return sorted(candles, key=lambda candle: candle.time)


def _candles_fingerprint(candles: Sequence[Candle]) -> str:
    """Fingerprint candle content, reusing hashes for immutable tuples."""
    if isinstance(candles, tuple):
        cached = _IMMUTABLE_FINGERPRINT_CACHE.get(id(candles))
        if cached is not None and cached[0] is candles:
            return cached[1]

    payload = tuple(
        (
            candle.time.isoformat(),
            candle.open,
            candle.high,
            candle.low,
            candle.close,
        )
        for candle in candles
    )
    fingerprint = sha256(repr(payload).encode("utf-8")).hexdigest()

    if isinstance(candles, tuple):
        if len(_IMMUTABLE_FINGERPRINT_CACHE) >= _MAX_CACHE_ENTRIES:
            _IMMUTABLE_FINGERPRINT_CACHE.clear()
        _IMMUTABLE_FINGERPRINT_CACHE[id(candles)] = (candles, fingerprint)

    return fingerprint


def _trim_cache(cache: dict) -> None:
    """Bound memory use by clearing a cache when it grows too large."""
    if len(cache) >= _MAX_CACHE_ENTRIES:
        cache.clear()


def is_c1_c2_contiguous(c1: Candle, c2: Candle) -> bool:
    return c2.time == candle_close_time(c1, H4_MINUTES)


def detect_crt3c(
    c1: Candle,
    c2: Candle,
    c3_start: datetime,
) -> Optional[CRT3CSetup]:
    """Detect a valid C1/C2 sweep and the immediately following C3."""
    _assert_aware(c1.time)
    _assert_aware(c2.time)
    _assert_aware(c3_start)

    if not _valid_candle(c1) or not _valid_candle(c2):
        return None

    if not is_c1_c2_contiguous(c1, c2):
        return None

    expected_c3_start = candle_close_time(c2, H4_MINUTES)
    if c3_start != expected_c3_start:
        return None

    # C2 must sweep one side and close strictly inside the full C1 range.
    close_inside = c1.low < c2.close < c1.high
    if not close_inside:
        return None

    bullish = c2.low < c1.low
    bearish = c2.high > c1.high

    # Reject no-sweep and both-sided-sweep candles.
    if bullish == bearish:
        return None

    direction = Direction.LONG if bullish else Direction.SHORT

    return CRT3CSetup(
        c1=c1,
        c2=c2,
        c3_start=c3_start,
        c3_end=c3_start + timedelta(minutes=H4_MINUTES),
        direction=direction,
    )


def _detect_fvg_from_three(
    first: Candle,
    middle: Candle,
    third: Candle,
    direction: Direction,
    timeframe_minutes: int,
) -> Optional[FVGZone]:
    if not all(_valid_candle(c) for c in (first, middle, third)):
        return None

    # The three candles must be consecutive on the requested timeframe.
    if middle.time != candle_close_time(first, timeframe_minutes):
        return None
    if third.time != candle_close_time(middle, timeframe_minutes):
        return None

    if direction == Direction.LONG and first.high < third.low:
        return FVGZone(
            direction=direction,
            upper=third.low,
            lower=first.high,
            first_candle=first,
            middle_candle=middle,
            third_candle=third,
            timeframe_minutes=timeframe_minutes,
        )

    if direction == Direction.SHORT and first.low > third.high:
        return FVGZone(
            direction=direction,
            upper=first.low,
            lower=third.high,
            first_candle=first,
            middle_candle=middle,
            third_candle=third,
            timeframe_minutes=timeframe_minutes,
        )

    return None


def detect_all_fvgs(
    candles: Sequence[Candle],
    direction: Direction,
    timeframe_minutes: int,
) -> list[FVGZone]:
    ordered = _sorted_candles(candles)
    out: list[FVGZone] = []

    for i in range(2, len(ordered)):
        fvg = _detect_fvg_from_three(
            ordered[i - 2],
            ordered[i - 1],
            ordered[i],
            direction,
            timeframe_minutes,
        )
        if fvg is not None and fvg.size > 0:
            out.append(fvg)

    return out


def _cached_detect_all_fvgs(
    candles: Sequence[Candle],
    direction: Direction,
    timeframe_minutes: int,
) -> list[FVGZone]:
    ordered = _sorted_candles(candles)
    key = (
        _candles_fingerprint(ordered),
        direction.value,
        timeframe_minutes,
    )

    if key not in _FVG_DETECTION_CACHE:
        _trim_cache(_FVG_DETECTION_CACHE)
        _FVG_DETECTION_CACHE[key] = detect_all_fvgs(
            ordered, direction, timeframe_minutes
        )

    return _FVG_DETECTION_CACHE[key]


def _fvg_key(fvg: FVGZone) -> tuple:
    return (
        fvg.formed_at.isoformat(),
        fvg.direction.value,
        fvg.timeframe_minutes,
        fvg.lower,
        fvg.upper,
        fvg.first_candle.time.isoformat(),
        fvg.middle_candle.time.isoformat(),
        fvg.third_candle.time.isoformat(),
    )


def _cached_candle_times(
    candles: Sequence[Candle],
) -> tuple[datetime, ...]:
    """Reuse timestamps for an immutable candle sequence."""
    if isinstance(candles, tuple):
        cached = _CANDLE_TIMES_CACHE.get(id(candles))
        if cached is not None and cached[0] is candles:
            return cached[1]

    times = tuple(c.time for c in candles)

    if isinstance(candles, tuple):
        if len(_CANDLE_TIMES_CACHE) >= _MAX_CACHE_ENTRIES:
            _CANDLE_TIMES_CACHE.clear()
        _CANDLE_TIMES_CACHE[id(candles)] = (candles, times)

    return times


def _build_fvg_validity_index(
    fvg: FVGZone,
    candles: Sequence[Candle],
) -> datetime:
    """Return the close time of the first completed invalidating candle."""
    ordered = _sorted_candles(candles)
    times = _cached_candle_times(ordered)

    start = bisect_left(times, fvg.third_candle.time)

    for candle in ordered[start:]:
        close_time = candle_close_time(candle, fvg.timeframe_minutes)

        # The FVG only exists after its third candle has closed.
        if close_time <= fvg.formed_at:
            continue

        if fvg.direction == Direction.LONG:
            if candle.low <= fvg.lower:
                return close_time
        else:
            if candle.high >= fvg.upper:
                return close_time

    return datetime.max.replace(tzinfo=timezone.utc)


def fvg_valid_through_time(
    fvg: FVGZone,
    candles: Sequence[Candle],
    as_of: datetime,
) -> bool:
    """Check whether a completed FVG remains valid as of the supplied time."""
    _assert_aware(as_of)

    if _utc_datetime(fvg.formed_at) > _utc_datetime(as_of):
        return False

    ordered = _sorted_candles(candles)
    key = (
        _candles_fingerprint(ordered),
        fvg.direction.value,
        fvg.timeframe_minutes,
    )

    if key not in _FVG_VALID_UNTIL_CACHE:
        _trim_cache(_FVG_VALID_UNTIL_CACHE)
        _FVG_VALID_UNTIL_CACHE[key] = {}

    index = _FVG_VALID_UNTIL_CACHE[key]
    fk = _fvg_key(fvg)

    if fk not in index:
        index[fk] = _build_fvg_validity_index(fvg, ordered)

    invalidated_at = _utc_datetime(index[fk])
    return invalidated_at > _utc_datetime(as_of)


def preexisting_valid_fvgs(
    candles: Sequence[Candle],
    direction: Direction,
    c3_start: datetime,
    timeframe_minutes: int,
) -> list[FVGZone]:
    """Return aligned FVGs formed strictly before C3 and still valid at C3."""
    _assert_aware(c3_start)
    result = []

    for fvg in _cached_detect_all_fvgs(
        candles, direction, timeframe_minutes
    ):
        if _utc_datetime(fvg.formed_at) >= _utc_datetime(c3_start):
            continue

        if fvg_valid_through_time(fvg, candles, c3_start):
            result.append(fvg)

    result.sort(key=lambda fvg: fvg.formed_at, reverse=True)
    return result


def fvg_touch_entry(
    fvg: FVGZone,
    previous_candle: Optional[Candle],
    current_candle: Candle,
) -> Optional[float]:
    """Return a deterministic boundary/open price for a valid FVG touch."""
    if not _valid_candle(current_candle):
        return None

    if current_candle.high < fvg.lower or current_candle.low > fvg.upper:
        return None

    if fvg.direction == Direction.LONG:
        # A touch through the far boundary is invalid/ambiguous.
        if current_candle.low <= fvg.lower:
            return None

        if fvg.lower <= current_candle.open <= fvg.upper:
            return current_candle.open

        if (
            previous_candle is not None
            and previous_candle.close > fvg.upper
            and current_candle.low <= fvg.upper
        ):
            return fvg.upper

    else:
        if current_candle.high >= fvg.upper:
            return None

        if fvg.lower <= current_candle.open <= fvg.upper:
            return current_candle.open

        if (
            previous_candle is not None
            and previous_candle.close < fvg.lower
            and current_candle.high >= fvg.lower
        ):
            return fvg.lower

    return None


def _nested_5m_refinement(
    parent_15m: FVGZone,
    m5_candles: Sequence[Candle],
    reaction_candle: Candle,
    direction: Direction,
) -> tuple[Optional[FVGZone], Optional[float]]:
    """Find an aligned, valid 5M FVG already formed by reaction start."""
    ordered = _sorted_candles(m5_candles)
    reaction_start = _utc_datetime(reaction_candle.time)
    eligible: list[tuple[FVGZone, float]] = []

    previous: Optional[Candle] = None
    for candle in ordered:
        if _utc_datetime(candle.time) < reaction_start:
            previous = candle
        else:
            break

    # Reuse the content-keyed cache: the M5 history is unchanged
    # throughout this backtest, so redetecting every FVG per reaction
    # candle is unnecessary.
    for fvg in _cached_detect_all_fvgs(ordered, direction, M5_MINUTES):
        if _utc_datetime(fvg.formed_at) > reaction_start:
            continue

        if not (
            fvg.lower >= parent_15m.lower
            and fvg.upper <= parent_15m.upper
        ):
            continue

        if not fvg_valid_through_time(fvg, ordered, reaction_candle.time):
            continue

        entry = fvg_touch_entry(fvg, previous, reaction_candle)
        if entry is not None:
            eligible.append((fvg, entry))

    if not eligible:
        return None, None

    eligible.sort(key=lambda pair: pair[0].formed_at, reverse=True)
    return eligible[0]


def _find_live_nested_fvg(
    parent_15m: FVGZone,
    m5_candles: Sequence[Candle],
    direction: Direction,
    current_time: datetime,
    current_price: float,
) -> Optional[FVGZone]:
    """Find an already formed nested 5M FVG containing the current quote.

    No synthetic candle is created. Only completed 5M candles are examined.
    """
    ordered = _sorted_candles(m5_candles)
    # Share the cached M5 FVG scan with nested refinement.
    candidates = _cached_detect_all_fvgs(ordered, direction, M5_MINUTES)

    eligible = []
    for fvg in candidates:
        if _utc_datetime(fvg.formed_at) > _utc_datetime(current_time):
            continue

        if not (
            fvg.lower >= parent_15m.lower
            and fvg.upper <= parent_15m.upper
        ):
            continue

        if not (fvg.lower <= current_price <= fvg.upper):
            continue

        if not fvg_valid_through_time(fvg, ordered, current_time):
            continue

        eligible.append(fvg)

    if not eligible:
        return None

    eligible.sort(key=lambda fvg: fvg.formed_at, reverse=True)
    return eligible[0]


def _fvg_candle3_stop(
    execution_fvg: FVGZone,
    direction: Direction,
) -> float:
    """Return the extreme of the execution FVG's third candle."""
    third = execution_fvg.third_candle
    return third.low if direction == Direction.LONG else third.high


def _valid_r_geometry(
    direction: Direction,
    entry: float,
    stop: float,
    rr: float,
) -> bool:
    if rr <= 0 or entry <= 0 or stop <= 0:
        return False

    if direction == Direction.LONG:
        return stop < entry

    return stop > entry


def _make_signal(
    setup: CRT3CSetup,
    parent_fvg: FVGZone,
    reaction_candle: Candle,
    entry_price: float,
    nested_fvg: Optional[FVGZone],
    execution_fvg: FVGZone,
    stop_loss: float,
    rr: float,
) -> Optional[CRT3CSignal]:
    if not _valid_r_geometry(
        setup.direction, entry_price, stop_loss, rr
    ):
        return None

    risk = abs(entry_price - stop_loss)
    target = (
        entry_price + risk * rr
        if setup.direction == Direction.LONG
        else entry_price - risk * rr
    )

    entry_time = candle_close_time(reaction_candle, M15_MINUTES)
    session = current_session(to_ny_time(entry_time))

    if session not in ALLOWED_SESSIONS:
        return None

    return CRT3CSignal(
        setup_id=setup.setup_id,
        direction=setup.direction,
        c3_start=setup.c3_start,
        c3_end=setup.c3_end,
        reaction_time=reaction_candle.time,
        entry_time=entry_time,
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=target,
        source_timeframe="M15",
        stop_source="EXECUTION_FVG_CANDLE3_EXTREME",
        execution_fvg=execution_fvg,
        parent_15m_fvg=parent_fvg,
        nested_5m_fvg=nested_fvg,
        session=session,
        reason=(
            "CRT-3C-v1.0 | valid 4H C1/C2 | pre-existing aligned 15M FVG "
            "reacted during C3 | "
            "direct 15M execution only"
        ),
    )


def find_crt3c_signal_in_c3(
    setup: CRT3CSetup,
    m15_candles: Sequence[Candle],
    m5_candles: Sequence[Candle],
    evaluation_end: Optional[datetime] = None,
    allow_5m_refinement: bool = False,
    rr: float = FIXED_RR,
) -> Optional[CRT3CSignal]:
    """Find the first confirmed M15 execution inside C3.

    M5 candles are deliberately excluded from entry detection and refinement.
    The M5 history is retained in the signature for backward compatibility.
    """
    _assert_aware(setup.c3_start)
    _assert_aware(setup.c3_end)

    end = min(evaluation_end or setup.c3_end, setup.c3_end)
    if end <= setup.c3_start:
        return None

    parents = preexisting_valid_fvgs(
        m15_candles,
        setup.direction,
        setup.c3_start,
        M15_MINUTES,
    )
    if not parents:
        return None

    ordered_m15 = _sorted_candles(m15_candles)
    previous: Optional[Candle] = None
    active_parents = list(parents)

    for candle in ordered_m15:
        close_time = candle_close_time(candle, M15_MINUTES)

        if close_time <= setup.c3_start:
            previous = candle
            continue

        if candle.time < setup.c3_start or close_time > end:
            continue

        # Do not enter on or after C3 has completed.
        if close_time >= setup.c3_end:
            continue

        if not _valid_candle(candle):
            previous = candle
            continue

        for parent in list(active_parents):
            # Remove an FVG permanently once its far boundary is reached.
            if parent.direction == Direction.LONG and candle.low <= parent.lower:
                active_parents.remove(parent)
                continue

            if parent.direction == Direction.SHORT and candle.high >= parent.upper:
                active_parents.remove(parent)
                continue

            parent_entry = fvg_touch_entry(parent, previous, candle)
            if parent_entry is None:
                continue

            # M15 is the sole entry timeframe. No M5 refinement is allowed.
            nested = None
            execution = parent

            # Entry is the completed M15 reaction candle's close.
            entry = candle.close
            stop = _fvg_candle3_stop(execution, setup.direction)

            if not _valid_r_geometry(setup.direction, entry, stop, rr):
                continue

            signal = _make_signal(
                setup=setup,
                parent_fvg=parent,
                reaction_candle=candle,
                entry_price=entry,
                nested_fvg=nested,
                execution_fvg=execution,
                stop_loss=stop,
                rr=rr,
            )

            if signal is not None:
                return signal

        previous = candle

    return None


def find_live_crt3c_signal(
    h4_closed: Sequence[Candle],
    m15_closed: Sequence[Candle],
    m5_closed: Sequence[Candle],
    current_time: datetime,
    current_price: float,
    allow_5m_refinement: bool = False,
    rr: float = FIXED_RR,
) -> Optional[CRT3CSignal]:
    """Emit a signal only on the close of a confirming M15 reaction candle.

    M5 history and the current quote remain in the signature for compatibility,
    but neither can trigger or refine an entry.
    """
    _assert_aware(current_time)
    if len(h4_closed) < 2 or not m15_closed:
        return None

    ordered_h4 = _sorted_candles(h4_closed)
    ordered_m15 = _sorted_candles(m15_closed)
    c1, c2 = ordered_h4[-2], ordered_h4[-1]
    c3_start = candle_close_time(c2, H4_MINUTES)
    c3_end = c3_start + timedelta(minutes=H4_MINUTES)
    now = _utc_datetime(current_time)
    if not (_utc_datetime(c3_start) <= now < _utc_datetime(c3_end)):
        return None

    setup = detect_crt3c(c1, c2, c3_start)
    if setup is None:
        return None

    latest_close = candle_close_time(ordered_m15[-1], M15_MINUTES)
    if _utc_datetime(latest_close) > now or _utc_datetime(latest_close) >= _utc_datetime(c3_end):
        return None

    signal = find_crt3c_signal_in_c3(
        setup=setup,
        m15_candles=ordered_m15,
        m5_candles=(),
        evaluation_end=latest_close,
        allow_5m_refinement=False,
        rr=rr,
    )
    if signal is None or _utc_datetime(signal.entry_time) != _utc_datetime(latest_close):
        return None
    return signal
