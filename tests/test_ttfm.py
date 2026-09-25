from datetime import datetime, time

import pytest

from strategies.ttfm.v1.ttfm import (
    Bar,
    FVG,
    ProtectedSwing,
    TTFMConfig,
    build_setup,
    fvg_from_three,
    is_a_plus,
    is_in_allowed_session,
    score_confluences,
)


def dt(h=10, m=0):
    return datetime(2026, 1, 5, h, m)


def test_bullish_fvg_detection():
    a = Bar(dt(), 100, 105, 99, 104)
    b = Bar(dt(10, 5), 104, 110, 103, 109)
    c = Bar(dt(10, 10), 109, 115, 106, 114)
    fvg = fvg_from_three(a, b, c)
    assert fvg is not None
    assert fvg.direction == "long"
    assert fvg.low == 105
    assert fvg.high == 106


def test_builds_exact_three_r_long_setup():
    cfg = TTFMConfig(rr=3.0, fvg_entry="midpoint")
    fvg = FVG("long", dt(), 105, 107)
    swing = ProtectedSwing("long", dt(10, 15), 103, 105)
    setup = build_setup(
        direction="long",
        created_at=dt(10, 20),
        fvg=fvg,
        protected_swing=swing,
        config=cfg,
    )
    assert setup is not None
    assert setup.entry == 106
    assert setup.stop == 103
    assert setup.target == 115
    assert setup.rr == 3.0


def test_rejects_target_without_three_r_structural_room():
    cfg = TTFMConfig(rr=3.0)
    fvg = FVG("short", dt(), 93, 95)
    swing = ProtectedSwing("short", dt(10, 15), 97, 95)
    setup = build_setup(
        direction="short",
        created_at=dt(10, 20),
        fvg=fvg,
        protected_swing=swing,
        config=cfg,
        structural_target=91,
    )
    assert setup is None


def test_wrong_direction_is_rejected():
    cfg = TTFMConfig()
    fvg = FVG("long", dt(), 105, 107)
    swing = ProtectedSwing("short", dt(10, 15), 110, 108)
    assert build_setup(
        direction="long",
        created_at=dt(10, 20),
        fvg=fvg,
        protected_swing=swing,
        config=cfg,
    ) is None


def test_session_gate():
    cfg = TTFMConfig(
        london_am=(time(2, 0), time(5, 0)),
        london_pm=(time(7, 0), time(10, 0)),
        ny_am=(time(7, 0), time(11, 0)),
    )
    assert is_in_allowed_session(time(3, 0), cfg)
    assert is_in_allowed_session(time(8, 30), cfg)
    assert not is_in_allowed_session(time(23, 0), cfg)


def test_a_plus_is_confluence_score_not_smt_only():
    confluences = {
        "smt": True,
        "clean_protected_swing": True,
        "fvg_at_poi": True,
        "premium_discount": True,
        "untouched_draw": True,
    }
    assert score_confluences(confluences) == 7
    assert is_a_plus(confluences)
    assert not is_a_plus({"smt": True})
