# ICT-Vibe-Trading

Open-source framework for ICT-inspired trading concepts, systematic strategy research, backtesting, and execution.

## Architecture

`concepts -> models -> strategies -> backtesting -> execution`

## Canonical CRT Model

**CRT-3C-v1.0**

1. **C1 (4H):** establishes the range high and low.
2. **C2 (4H):** manipulation/sweep candle. A bullish setup requires `C2.low < C1.low` and `C2.close > C1.low`; a bearish setup requires `C2.high > C1.high` and `C2.close < C1.high`.
3. **C3 (4H):** the next candle and execution window. C3 is not another confirmation candle.
4. During C3, a directionally aligned **15M FVG must already exist before C3 begins** and price must react to/touch it during C3.
5. A **5M nested/refined FVG is optional**. Execution may use the 15M FVG or a 5M refinement.
6. No look-ahead and no entry outside C3.

The canonical model must not be silently replaced with additional confirmation gates.

## Status

Foundation stage. Strategy definitions, concepts, tests, and a broker-neutral backtesting engine are being built incrementally.

## Disclaimer

This project is for research and software-engineering purposes. It is not financial advice and does not guarantee trading performance.

## License

MIT
