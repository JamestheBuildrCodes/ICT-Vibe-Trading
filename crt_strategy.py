"""
CRT (Candle Range Theory) + Turtle Soup strategy logic.

Implements the model described in the "CRT & TS Guide" and "1AM CRT" docs:

  1. Range candle   -> marks CRT High / CRT Low
  2. Key/session candle -> manipulation (Turtle Soup): wicks beyond the
     range candle's high or low, then CLOSES back inside the range.
     This is the CSD (Change in State of Delivery) signal.
  3. Entry:
       Model 1 (aggressive)  -> enter on the manipulation candle close,
                                 at the CSD/OB price.
       Model 2 (conservative)-> enter on the next candle, when price
                                 trades through the 50% midpoint of the
                                 manipulation candle's range.
  4. Stop loss  -> beyond the wick of the manipulation candle.
  5. Targets    -> TP1 at 50% of the CRT range, TP2 at the opposite
                    end of the CRT range (approx 1:2 - 1:3 RR, matches
                    the docs).

This module contains ONLY decision logic - no broker calls, no order
execution, no live-trading side effects. It takes OHLC data in and
returns a plain-Python signal out. Wire it to MetaTrader5 (or any
other broker API) in a separate, thin execution layer - keep the
strategy brain and the "touches real money" layer physically separate
so you can unit-test and backtest the brain without any risk.

The "1AM / 5AM / 9AM" session variants from your docs are all the same
template with a different key-candle hour, so the trigger hour is a
parameter, not hardcoded.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from enum import Enum
from typing import Optional, List


class Direction(Enum):
    LONG = "long"
    SHORT = "short"


class EntryModel(Enum):
    MODEL_1_MANIPULATION_CLOSE = "model_1"   # enter on manipulation candle close
    MODEL_2_FIFTY_PERCENT = "model_2"        # enter on 50% retrace of candle 2


@dataclass
class Candle:
    time: datetime
    open: float
    high: float
    low: float
    close: float


@dataclass
class SessionConfig:
    """
    Defines the session template from the docs. All hours are in the
    timezone your OHLC timestamps are already in - be deliberate about
    this, broker server time and NY time are NOT the same thing, and
    ICT session logic (CBDR / Asia / London) is defined in NY time.
    Convert your data to NY time before using this, or your ranges
    will be wrong.
    """
    range_candle_hour: int          # e.g. 17 for the 5PM CBDR candle
    consolidation_hour: int         # e.g. 21 for the 9PM Asia candle
    key_candle_hour: int            # e.g. 1, 5, or 9 - the manipulation candle
    candle_timeframe_minutes: int = 60   # H1 candles by default
    entry_window_start_hour: int = 2     # docs: "key time = 2am-3am"
    entry_window_end_hour: int = 3


@dataclass
class Signal:
    direction: Direction
    entry_model: EntryModel
    entry_price: float
    stop_loss: float
    take_profit_1: float   # 50% of range
    take_profit_2: float   # opposite end of range
    crt_high: float
    crt_low: float
    manipulation_candle: Candle
    reason: str


def is_turtle_soup(range_candle: Candle, key_candle: Candle) -> Optional[Direction]:
    """
    Detects Turtle Soup / CSD per the docs:
      Bullish TS: wick BELOW range_candle.low, closes ABOVE range_candle.low
      Bearish TS: wick ABOVE range_candle.high, closes BELOW range_candle.high
    Returns the resulting bias direction, or None if no manipulation occurred.
    """
    wicked_below = key_candle.low < range_candle.low
    closed_back_above = key_candle.close > range_candle.low

    wicked_above = key_candle.high > range_candle.high
    closed_back_below = key_candle.close < range_candle.high

    if wicked_below and closed_back_above:
        return Direction.LONG   # swept sell-side liquidity, expect reversal up
    if wicked_above and closed_back_below:
        return Direction.SHORT  # swept buy-side liquidity, expect reversal down
    return None


def build_signal(
    range_candle: Candle,
    key_candle: Candle,
    entry_model: EntryModel = EntryModel.MODEL_1_MANIPULATION_CLOSE,
    next_candle: Optional[Candle] = None,
    all_candles: Optional[List[Candle]] = None,
    key_candle_index: Optional[int] = None,
    require_ob_or_fvg: bool = True,
    daily_bias_context: Optional[DailyBiasContext] = None,
    fixed_rr: Optional[float] = None,
) -> Optional[Signal]:
    """
    Given the range candle and the key/manipulation candle, returns a
    Signal if a valid CRT+TS setup exists, else None.

    Model 2 requires next_candle (the candle 3 that trades through the
    50% midpoint of the manipulation candle's range) to confirm entry.

    Confluence filters (on by default when all_candles/key_candle_index
    are supplied):
      - require_ob_or_fvg: entry requires EITHER a valid Order Block
        (is_valid_order_block) OR a Fair Value Gap (detect_fvg) at the
        manipulation candle. An FVG that formed immediately after a
        valid OB (the "Unicorn Model" - Breaker + FVG) is the strongest
        case and is flagged in the signal's reason string.
      - daily_bias_context: rejects signals that fight the HTF liquidity
        draw (PDH/PDL/PWH/PWL). Skipped if not given.

    fixed_rr: when set (e.g. 2.0 for a 1:2), both take-profit levels
    are computed as a fixed R-multiple of the stop distance instead of
    the CRT-range-based 50%/opposite-end targets. Per the confirmed
    "Mastr CRT Hunter" spec, this bot uses fixed_rr=2.0 - price does
    NOT need to reach the CRT 50% or opposite boundary to be valid.
    """
    direction = is_turtle_soup(range_candle, key_candle)
    if direction is None:
        return None

    ob_valid = False
    fvg_valid = False
    unicorn = False
    if all_candles is not None and key_candle_index is not None:
        ob_valid = is_valid_order_block(all_candles, key_candle_index, direction)
        fvg_valid = detect_fvg(all_candles, key_candle_index, direction)
        if ob_valid:
            unicorn = fvg_formed_after_ob(all_candles, key_candle_index - 1, direction)

        if require_ob_or_fvg and not (ob_valid or fvg_valid):
            return None

    if daily_bias_context is not None:
        if not daily_bias_aligned(direction, daily_bias_context):
            return None

    crt_high = range_candle.high
    crt_low = range_candle.low
    range_size = crt_high - crt_low
    if range_size <= 0:
        return None

    midpoint_of_manip_candle = (key_candle.high + key_candle.low) / 2

    if entry_model == EntryModel.MODEL_1_MANIPULATION_CLOSE:
        entry_price = key_candle.close
    else:
        if next_candle is None:
            return None  # need candle 3 to confirm model 2 entry
        if direction == Direction.LONG and next_candle.low > midpoint_of_manip_candle:
            return None  # price never traded through the 50% level
        if direction == Direction.SHORT and next_candle.high < midpoint_of_manip_candle:
            return None
        entry_price = midpoint_of_manip_candle

    if direction == Direction.LONG:
        stop_loss = key_candle.low          # beyond the manipulation wick
        if fixed_rr is not None:
            risk = entry_price - stop_loss
            take_profit_1 = entry_price + risk * fixed_rr
            take_profit_2 = take_profit_1
        else:
            take_profit_1 = crt_low + range_size * 0.5
            take_profit_2 = crt_high
    else:
        stop_loss = key_candle.high
        if fixed_rr is not None:
            risk = stop_loss - entry_price
            take_profit_1 = entry_price - risk * fixed_rr
            take_profit_2 = take_profit_1
        else:
            take_profit_1 = crt_high - range_size * 0.5
            take_profit_2 = crt_low

    return Signal(
        direction=direction,
        entry_model=entry_model,
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit_1=take_profit_1,
        take_profit_2=take_profit_2,
        crt_high=crt_high,
        crt_low=crt_low,
        manipulation_candle=key_candle,
        reason=(
            f"{direction.value} Turtle Soup / CSD at HTF key level"
            + (" + Unicorn (OB+FVG)" if unicorn else
               " + valid OB" if ob_valid else
               " + FVG" if fvg_valid else "")
            + (" + daily bias aligned" if daily_bias_context is not None else "")
        ),
    )


def in_entry_window(candle_time: datetime, config: SessionConfig) -> bool:
    """Docs specify a key entry time window (e.g. 2AM-3AM) after the manipulation candle."""
    t = candle_time.time()
    return time(config.entry_window_start_hour, 0) <= t <= time(config.entry_window_end_hour, 0)


def position_size(
    account_equity: float,
    risk_percent: float,
    entry_price: float,
    stop_loss: float,
    pip_value_per_lot: float,
    pip_size: float,
) -> float:
    """
    Fixed fractional position sizing. This is the piece that actually
    controls your risk - it belongs in code, not in the AI's judgment.

    risk_percent: e.g. 0.5 for risking 0.5% of equity per trade.
    pip_value_per_lot: your broker's $ value per pip for 1.0 lot on this symbol.
    pip_size: e.g. 0.0001 for most FX pairs, 0.01 for JPY pairs.
    """
    risk_amount = account_equity * (risk_percent / 100)
    stop_distance_pips = abs(entry_price - stop_loss) / pip_size
    if stop_distance_pips <= 0:
        return 0.0
    lots = risk_amount / (stop_distance_pips * pip_value_per_lot)
    return round(lots, 2)


def detect_fvg(candles: List[Candle], end_index: int, direction: Direction) -> bool:
    """
    Detects a 3-candle Fair Value Gap ending at end_index, per the
    "Unicorn Model" doc: a gap left between the first and third candle
    of an impulsive 3-candle move.
      Bullish FVG: candles[end_index-2].high < candles[end_index].low
      Bearish FVG: candles[end_index-2].low  > candles[end_index].high
    """
    if end_index < 2 or end_index >= len(candles):
        return False
    first, third = candles[end_index - 2], candles[end_index]
    if direction == Direction.LONG:
        return first.high < third.low
    else:
        return first.low > third.high


def fvg_formed_after_ob(candles: List[Candle], ob_index: int, direction: Direction) -> bool:
    """
    Per the Unicorn Model doc, the highest-probability entries are an
    Order Block immediately followed by a Fair Value Gap (Breaker + FVG).
    Checks whether a 3-candle FVG window starts right at the OB candle:
    FVG = candles[ob_index], candles[ob_index+1], candles[ob_index+2].
    """
    fvg_end_index = ob_index + 2
    return detect_fvg(candles, fvg_end_index, direction)


def is_valid_order_block(
    candles: List[Candle], manipulation_index: int, direction: Direction
) -> bool:
    """
    Order Block validation, per the "Order Block PDF" doc:
      - A bullish OB is the last DOWN-close candle before an up move
        that runs buy-side liquidity, ideally paired with an FVG.
      - A bearish OB is the last UP-close candle before a down move
        that runs sell-side liquidity.
      - Validate using the candle BODY, not the wick: if price is in
        a hurry to move away from the body's mean-threshold (i.e. it
        doesn't linger), the OB is considered valid.

    This is a simplified, automatable approximation of a discretionary
    concept - it checks for a same-direction "hurry away" candle
    immediately preceding the manipulation candle. Treat it as a
    filter that reduces false signals, not a perfect replica of manual
    OB reading.
    """
    if manipulation_index < 1 or manipulation_index >= len(candles):
        return False

    ob_candle = candles[manipulation_index - 1]
    ob_body = abs(ob_candle.close - ob_candle.open)
    ob_range = ob_candle.high - ob_candle.low
    if ob_range <= 0:
        return False

    body_ratio = ob_body / ob_range  # "hurry away" = decisive body, not a doji/wick candle
    strong_body = body_ratio >= 0.5

    if direction == Direction.LONG:
        is_down_close = ob_candle.close < ob_candle.open
        return is_down_close and strong_body
    else:
        is_up_close = ob_candle.close > ob_candle.open
        return is_up_close and strong_body


@dataclass
class DailyBiasContext:
    """Inputs from the 'Daily Bias' doc: PDH/PDL and previous week H/L,
    used to judge which side price is more likely to draw toward."""
    previous_day_high: float
    previous_day_low: float
    previous_week_high: float
    previous_week_low: float
    current_price: float


def daily_bias_aligned(direction: Direction, context: DailyBiasContext) -> bool:
    """
    Per the Daily Bias doc: PDH/PDL and PWH/PWL are liquidity draws.
    A reversal signal is stronger when it points toward an untapped
    liquidity level rather than away from one (e.g. a LONG signal is
    more credible if PDH/PWH are still above price and untapped, since
    that's the draw on liquidity the model expects price to reach for).

    Returns True if the CRT+TS direction agrees with the higher
    timeframe draw on liquidity; False if it's fighting against it.
    """
    if direction == Direction.LONG:
        draw_above = max(context.previous_day_high, context.previous_week_high)
        return draw_above > context.current_price
    else:
        draw_below = min(context.previous_day_low, context.previous_week_low)
        return draw_below < context.current_price


def get_range_and_key_candles(
    candles: List[Candle], config: SessionConfig
) -> Optional[tuple]:
    """
    Pulls today's range candle and key candle out of a list of hourly
    candles, based on the hours configured in SessionConfig.
    Returns (range_candle, key_candle) or None if either is missing
    from the supplied data (e.g. still waiting on today's key candle).
    """
    range_candle = next(
        (c for c in candles if c.time.hour == config.range_candle_hour), None
    )
    key_candle = next(
        (c for c in candles if c.time.hour == config.key_candle_hour), None
    )
    if range_candle is None or key_candle is None:
        return None
    return range_candle, key_candle
