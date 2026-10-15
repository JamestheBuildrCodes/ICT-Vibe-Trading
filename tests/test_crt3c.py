from datetime import datetime, timezone, timedelta

from crt_strategy import Candle, Direction
from crt3c_strategy import (
    FIXED_RR,
    detect_crt3c,
    detect_all_fvgs,
    preexisting_valid_fvgs,
    find_crt3c_signal_in_c3,
)

UTC = timezone.utc


def c(t, o, h, l, cl):
    return Candle(t, o, h, l, cl)


def test_bullish_crt3c_detection():
    t0 = datetime(2026, 1, 1, 0, tzinfo=UTC)
    c1 = c(t0, 1.1050, 1.1100, 1.1000, 1.1060)
    c2 = c(t0 + timedelta(hours=4), 1.1060, 1.1080, 1.0980, 1.1040)
    setup = detect_crt3c(c1, c2, t0 + timedelta(hours=8))
    assert setup is not None
    assert setup.direction is Direction.LONG
    assert setup.c3_end == t0 + timedelta(hours=12)


def test_bearish_crt3c_detection():
    t0 = datetime(2026, 1, 1, 0, tzinfo=UTC)
    c1 = c(t0, 1.1050, 1.1100, 1.1000, 1.1060)
    c2 = c(t0 + timedelta(hours=4), 1.1060, 1.1120, 1.1030, 1.1070)
    setup = detect_crt3c(c1, c2, t0 + timedelta(hours=8))
    assert setup is not None
    assert setup.direction is Direction.SHORT


def test_crt_requires_close_back_inside():
    t0 = datetime(2026, 1, 1, 0, tzinfo=UTC)
    c1 = c(t0, 1.1050, 1.1100, 1.1000, 1.1060)
    c2 = c(t0 + timedelta(hours=4), 1.1060, 1.1080, 1.0980, 1.0990)
    assert detect_crt3c(c1, c2, t0 + timedelta(hours=8)) is None


def make_parent_m15(start, direction=Direction.LONG):
    if direction is Direction.LONG:
        return [
            c(start, 1.1010, 1.1030, 1.1005, 1.1025),
            c(start + timedelta(minutes=15), 1.1025, 1.1060, 1.1020, 1.1055),
            c(start + timedelta(minutes=30), 1.1055, 1.1090, 1.1050, 1.1080),
        ]
    return [
        c(start, 1.1080, 1.1090, 1.1050, 1.1060),
        c(start + timedelta(minutes=15), 1.1060, 1.1070, 1.1010, 1.1020),
        c(start + timedelta(minutes=30), 1.1020, 1.1025, 1.0990, 1.1000),
    ]


def test_preexisting_fvg_is_required():
    t0 = datetime(2026, 1, 1, 0, tzinfo=UTC)
    # C3 starts at 08:00; parent FVG forms at 00:45, so it is pre-existing.
    m15 = make_parent_m15(t0)
    c3_start = t0 + timedelta(hours=8)
    fvgs = preexisting_valid_fvgs(m15, Direction.LONG, c3_start, 15)
    assert fvgs
    assert all(f.formed_at < c3_start for f in fvgs)


def test_signal_uses_completed_m15_reaction_not_m5():
    base = datetime(2026, 1, 1, 0, tzinfo=UTC)
    h4 = [
        c(base, 1.1050, 1.1100, 1.1000, 1.1060),
        c(base + timedelta(hours=4), 1.1060, 1.1080, 1.0980, 1.1040),
    ]
    setup = detect_crt3c(h4[0], h4[1], base + timedelta(hours=8))
    assert setup is not None
    m15 = make_parent_m15(base) + [
        c(base + timedelta(hours=8), 1.1060, 1.1070, 1.1040, 1.1060),
    ]
    # M5 cannot create an entry; only the completed M15 reaction may do so.
    m5 = [c(base + timedelta(hours=8, minutes=5), 1.1048, 1.1050, 1.1043, 1.1046)]
    signal = find_crt3c_signal_in_c3(setup, m15, m5)
    assert signal is not None
    assert signal.nested_5m_fvg is None
    assert signal.source_timeframe == "M15"
    assert signal.entry_time == base + timedelta(hours=8, minutes=15)
    assert signal.stop_source == "EXECUTION_FVG_CANDLE3_EXTREME"
    assert signal.stop_loss == signal.parent_15m_fvg.third_candle.low

def test_opposite_fvg_does_not_satisfy_direction():
    t0 = datetime(2026, 1, 1, 0, tzinfo=UTC)
    bearish_only = make_parent_m15(t0, Direction.SHORT)
    bullish = preexisting_valid_fvgs(bearish_only, Direction.LONG, t0 + timedelta(hours=4), 15)
    assert bullish == []


def test_entry_cannot_occur_after_c3_closes():
    base = datetime(2026, 1, 1, 0, tzinfo=UTC)
    h4 = [
        c(base, 1.1050, 1.1100, 1.1000, 1.1060),
        c(base + timedelta(hours=4), 1.1060, 1.1080, 1.0980, 1.1040),
    ]
    setup = detect_crt3c(h4[0], h4[1], base + timedelta(hours=8))
    assert setup is not None
    m15 = make_parent_m15(base)
    # Put the only possible reaction after C3's 12:00 close.
    m5 = [c(base + timedelta(hours=12), 1.1070, 1.1080, 1.1045, 1.1060)]
    assert find_crt3c_signal_in_c3(setup, m15, m5) is None


def test_fvg_formed_at_c3_start_is_not_preexisting():
    base = datetime(2026, 1, 1, 0, tzinfo=UTC)
    c3_start = base + timedelta(hours=8)
    m15 = [
        c(base + timedelta(hours=7, minutes=15), 1.1000, 1.1010, 1.0990, 1.1005),
        c(base + timedelta(hours=7, minutes=30), 1.1005, 1.1015, 1.1000, 1.1012),
        c(base + timedelta(hours=7, minutes=45), 1.1012, 1.1030, 1.1020, 1.1028),
        c(c3_start, 1.1028, 1.1040, 1.1035, 1.1038),
    ]
    # The final three candles can create a bullish FVG whose third candle
    # closes exactly at C3 start. It must not qualify as pre-existing.
    fvgs = preexisting_valid_fvgs(m15, Direction.LONG, c3_start, 15)
    assert all(f.formed_at < c3_start for f in fvgs)


def test_m5_refinement_cannot_change_m15_entry():
    base = datetime(2026, 1, 1, 0, tzinfo=UTC)
    h4 = [
        c(base, 1.1050, 1.1100, 1.1000, 1.1060),
        c(base + timedelta(hours=4), 1.1060, 1.1080, 1.0980, 1.1040),
    ]
    setup = detect_crt3c(h4[0], h4[1], base + timedelta(hours=8))
    assert setup is not None
    m15 = make_parent_m15(base) + [
        c(base + timedelta(hours=8), 1.1060, 1.1070, 1.1040, 1.1060),
    ]
    # An available nested M5 FVG must not alter the M15 entry or stop.
    m5 = [
        c(base + timedelta(hours=7, minutes=40), 1.1040, 1.1042, 1.1038, 1.1040),
        c(base + timedelta(hours=7, minutes=45), 1.1040, 1.1048, 1.1039, 1.1046),
        c(base + timedelta(hours=7, minutes=50), 1.1046, 1.1050, 1.1044, 1.1048),
    ]
    signal = find_crt3c_signal_in_c3(setup, m15, m5, allow_5m_refinement=True)
    assert signal is not None
    assert signal.nested_5m_fvg is None
    assert signal.source_timeframe == "M15"
    assert signal.entry_time == base + timedelta(hours=8, minutes=15)
    assert signal.stop_loss == signal.parent_15m_fvg.third_candle.low
