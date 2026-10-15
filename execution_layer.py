"""
Execution layer v2 - the only file allowed to touch a real broker
connection or place real orders. Orchestrates every module built so
far:

  candle_validator  -> rejects malformed/stale/duplicate data
  session_manager   -> NY DST-aware session + trading-window diagnostics
  risk_engine       -> drawdown state machine, tick-accurate sizing, guards
  crt3c_strategy    -> CRT-3C-v1.0 (4H C1/C2 -> C3 -> pre-existing 15M FVG -> optional 5M refinement)
  setup_classifier  -> gates vs optional confluence -> A+/A/rejected

Only CRT-3C-v1.0 is active in this execution layer. Legacy 1-5-9 and
rolling broad-CRT modules remain in the repository for historical reference
but are not called by the live/demo path.

SAFETY: DEMO_ONLY defaults to True and HARD-REFUSES to trade on a live
account (not just skip-sending like the old soft DRY_RUN flag) - 
ported from the MQL5 build's InpDemoOnly gate, which is stronger than
what the original Python version had.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Optional
from zoneinfo import ZoneInfo

from crt_strategy import Candle, Direction
from candle_validator import validate_candles
from crt3c_strategy import find_live_crt3c_signal
from risk_engine import (
    RiskEngineState,
    PROP_PROFILE,
    RejectionReason,
    check_risk_guards,
    calc_volume_by_risk,
    SymbolSpec,
)
from state_store import save_risk_state, load_risk_state


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("mastr_crt_hunter")


class StrategyMode(Enum):
    CRT_3C = "crt_3c_v1"


# ---- Configuration - review every value before ever going live ----
DEMO_ONLY = True
STRATEGY_MODE = StrategyMode.CRT_3C
SYMBOL = "EURUSD"

# Headway-Demo timestamps observed from the MT5 feed are currently
# encoded as broker server time at UTC+3.
BROKER_UTC_OFFSET_HOURS = 3.0

PIP_SIZE = 0.0001


def resolve_symbol(mt5, requested_symbol: str) -> str:
    """
    MT5's copy_rates_from_pos silently fails if the symbol isn't added
    to Market Watch, and many brokers suffix symbol names (e.g. EURUSD.m,
    EURUSDm). This tries the plain name and a few common suffix variants,
    selects whichever one actually exists, and raises a clear error
    listing real matches if none work.
    """
    candidates = [
        requested_symbol,
        requested_symbol + ".m",
        requested_symbol + "m",
        requested_symbol + ".raw",
    ]

    for candidate in candidates:
        info = mt5.symbol_info(candidate)
        if info is not None:
            if not info.visible:
                mt5.symbol_select(candidate, True)
            return candidate

    all_symbols = mt5.symbols_get()
    close_matches = [
        s.name
        for s in all_symbols
        if requested_symbol.upper() in s.name.upper()
    ][:10]

    raise RuntimeError(
        f"Could not find symbol '{requested_symbol}' or common variants "
        f"on this broker. Closest matches found on your account: "
        f"{close_matches or 'none'}. Update SYMBOL in execution_layer.py "
        f"to the exact name shown in your MT5 Market Watch."
    )


def connect_mt5():
    import MetaTrader5 as mt5

    if not mt5.initialize():
        raise RuntimeError(
            f"MT5 initialize() failed: {mt5.last_error()}"
        )

    account = mt5.account_info()

    if DEMO_ONLY and account is not None:
        # MT5's TRADE_MODE_DEMO enum value is 0 on ACCOUNT_INFO.
        is_demo = getattr(account, "trade_mode", None) == 0

        if not is_demo:
            mt5.shutdown()
            raise RuntimeError(
                "REFUSING TO CONNECT: DEMO_ONLY=True but this account "
                "is not a demo account. This is a hard safety gate, "
                "not a soft warning."
            )

    log.info("Connected to MT5. Account: %s", account)
    return mt5


def mt5_timestamp_to_utc(raw_timestamp: int) -> datetime:
    """
    Convert the broker's MT5 timestamp encoding into a real UTC datetime.

    Headway-Demo is currently observed to encode the returned candle/tick
    timestamps as broker server time at UTC+3. MT5 exposes the value as an
    epoch-like integer, so interpreting it directly as UTC makes the data
    appear three hours in the future.

    Example:
        broker encoded: 2026-09-16 14:00
        normalized UTC: 2026-09-16 11:00
    """
    broker_encoded_time = datetime.fromtimestamp(
        raw_timestamp,
        tz=timezone.utc,
    )

    return broker_encoded_time - timedelta(
        hours=BROKER_UTC_OFFSET_HOURS
    )


def fetch_candles(
    mt5,
    symbol: str,
    timeframe_const: int,
    count: int,
) -> list:
    """
    Fetch completed candles only and validate them.

    Position 1 intentionally skips the current/forming candle.
    """
    rates = mt5.copy_rates_from_pos(
        symbol,
        timeframe_const,
        1,
        count,
    )

    if rates is None:
        raise RuntimeError(
            f"Could not fetch rates for {symbol}: {mt5.last_error()}"
        )

    candles = [
        Candle(
            time=mt5_timestamp_to_utc(r["time"]),
            open=r["open"],
            high=r["high"],
            low=r["low"],
            close=r["close"],
        )
        for r in rates
    ]

    result = validate_candles(candles)

    if not result.valid:
        raise RuntimeError(
            f"Candle validation failed for {symbol} "
            f"{timeframe_const}: {result.reason}"
        )

    return candles


def get_symbol_spec(mt5, symbol: str) -> SymbolSpec:
    info = mt5.symbol_info(symbol)

    if info is None:
        raise RuntimeError(
            f"symbol_info returned None for {symbol}"
        )

    tick_value_loss = (
        getattr(info, "trade_tick_value_loss", None)
        or info.trade_tick_value
    )

    return SymbolSpec(
        tick_size=info.trade_tick_size,
        tick_value_loss=tick_value_loss,
        volume_min=info.volume_min,
        volume_max=info.volume_max,
        volume_step=info.volume_step,
    )


def place_order(
    mt5,
    symbol: str,
    direction: Direction,
    entry: float,
    stop: float,
    target: float,
    volume: float,
    comment: str,
):
    """The single point where money moves. Everything above this is analysis."""

    if volume <= 0:
        log.warning(
            "Computed volume is 0 - skipping order "
            "(check risk/sizing inputs)."
        )
        return None

    log.info(
        "SIGNAL: %s %s @ %.5f | SL %.5f | TP %.5f | "
        "volume %.2f | %s",
        direction.value,
        symbol,
        entry,
        stop,
        target,
        volume,
        comment,
    )

    import MetaTrader5 as mt5_mod

    order_type = (
        mt5_mod.ORDER_TYPE_BUY
        if direction == Direction.LONG
        else mt5_mod.ORDER_TYPE_SELL
    )

    request = {
        "action": mt5_mod.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": volume,
        "type": order_type,
        "price": entry,
        "sl": stop,
        "tp": target,
        "deviation": 10,
        "magic": 20260916,
        "comment": comment,
        "type_time": mt5_mod.ORDER_TIME_GTC,
        "type_filling": mt5_mod.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)

    log.info("Order result: %s", result)

    return result


def run_once(session_window=None):
    """Evaluate one completed-candle snapshot and optionally place a demo trade."""
    mt5 = connect_mt5()
    risk_state = None
    try:
        import MetaTrader5 as mt5_mod

        account = mt5.account_info()
        if account is None:
            raise RuntimeError(f"Could not read MT5 account: {mt5.last_error()}")

        utc_now = datetime.now(timezone.utc)
        ny_now = utc_now.astimezone(ZoneInfo("America/New_York"))
        today = ny_now.date()

        risk_state = load_risk_state(PROP_PROFILE, account.equity, today)
        risk_state.reset_for_new_day(account.equity, today)
        risk_state.update_drawdown_tier(account.equity)

        guard = check_risk_guards(risk_state, account.equity, utc_now)
        if guard is not None:
            log.warning("Risk guard blocked new entries: %s", guard.value)
            return

        symbol = resolve_symbol(mt5, SYMBOL)
        positions = mt5.positions_get(symbol=symbol)
        if positions:
            log.info("Existing position(s) on %s; no duplicate CRT-3C entry.", symbol)
            return

        # Position 1 in fetch_candles() excludes current/forming candles.
        h4 = fetch_candles(mt5, symbol, mt5_mod.TIMEFRAME_H4, 12)
        m15 = fetch_candles(mt5, symbol, mt5_mod.TIMEFRAME_M15, 400)
        m5 = fetch_candles(mt5, symbol, mt5_mod.TIMEFRAME_M5, 300)

        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            raise RuntimeError(f"Could not retrieve live tick for {symbol}")
        bid = float(tick.bid)
        ask = float(tick.ask)
        if bid <= 0 or ask <= 0 or ask < bid:
            raise RuntimeError(f"Invalid tick for {symbol}: bid={bid} ask={ask}")

        tick_time = mt5_timestamp_to_utc(int(getattr(tick, "time", 0))) if getattr(tick, "time", 0) else utc_now
        probe_price = (bid + ask) / 2.0

        signal = find_live_crt3c_signal(
            h4_closed=h4,
            m15_closed=m15,
            m5_closed=m5,
            current_time=tick_time,
            current_price=probe_price,
            allow_5m_refinement=False,
            rr=2.0,
        )
        if signal is None:
            log.info("No CRT-3C-v1.0 setup at %s.", tick_time)
            return

        # Convert the structural signal into an executable quote-sided price.
        entry = ask if signal.direction == Direction.LONG else bid
        stop = signal.stop_loss
        if signal.direction == Direction.LONG and not stop < entry:
            log.warning("CRT-3C invalid live long geometry after quote adjustment.")
            return
        if signal.direction == Direction.SHORT and not stop > entry:
            log.warning("CRT-3C invalid live short geometry after quote adjustment.")
            return
        risk = abs(entry - stop)
        target = entry + 2.0 * risk if signal.direction == Direction.LONG else entry - 2.0 * risk

        spec = get_symbol_spec(mt5, symbol)
        volume = calc_volume_by_risk(
            account.equity,
            risk_state.current_risk_percent(),
            entry,
            stop,
            spec,
        )
        if volume <= 0:
            log.warning("Calculated volume <= 0; skipping demo order.")
            return

        comment = f"CRT-3C-v1.0-{signal.source_timeframe}"
        log.info(
            "CRT-3C SIGNAL: %s %s entry=%.5f SL=%.5f TP=%.5f source=%s session=%s",
            signal.direction.value.upper(), symbol, entry, stop, target,
            signal.source_timeframe, signal.session.value,
        )

        result = place_order(
            mt5,
            symbol,
            signal.direction,
            entry,
            stop,
            target,
            volume,
            comment,
        )
        if result is not None and getattr(result, "retcode", None) in {
            getattr(mt5_mod, "TRADE_RETCODE_DONE", -1),
            getattr(mt5_mod, "TRADE_RETCODE_PLACED", -2),
        }:
            risk_state.record_opened_trade(utc_now)
            log.info("Demo order accepted: %s", result)
        else:
            log.warning("Demo order was not confirmed successful: %s", result)

    finally:
        if risk_state is not None:
            save_risk_state(risk_state)
        mt5.shutdown()



if __name__ == "__main__":
    run_once()