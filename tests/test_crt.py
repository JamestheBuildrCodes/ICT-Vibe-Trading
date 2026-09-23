from strategies.crt.v1.crt import Candle, Direction, detect_crt_direction, is_c3_execution_window


def test_bullish_crt_requires_low_sweep_and_close_inside():
    c1 = Candle(high=110, low=100, close=108)
    c2 = Candle(high=109, low=99, close=103)
    assert detect_crt_direction(c1, c2) == Direction.LONG


def test_bullish_crt_invalid_without_close_inside():
    c1 = Candle(high=110, low=100, close=108)
    c2 = Candle(high=112, low=99, close=99)
    assert detect_crt_direction(c1, c2) is None


def test_bearish_crt_requires_high_sweep_and_close_inside():
    c1 = Candle(high=110, low=100, close=102)
    c2 = Candle(high=111, low=101, close=107)
    assert detect_crt_direction(c1, c2) == Direction.SHORT


def test_bearish_crt_invalid_without_close_inside():
    c1 = Candle(high=110, low=100, close=102)
    c2 = Candle(high=111, low=98, close=111)
    assert detect_crt_direction(c1, c2) is None


def test_c3_is_execution_window():
    assert is_c3_execution_window(3)
    assert not is_c3_execution_window(2)
    assert not is_c3_execution_window(4)
