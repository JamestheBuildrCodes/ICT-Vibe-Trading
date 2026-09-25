# CRT-3C-v1.0 Execution Model

Before C3 begins, an aligned 15M FVG must already exist. During C3, price must touch or react to that FVG.

A 5M nested/refined FVG may then be used for precision, but it is optional. Execution can occur from the 15M structure when no 5M refinement is required.

## Stop-loss

The SL is anchored to the first candle of the selected execution timeframe:

- Bullish trade: **low of the first 15M/5M candle**.
- Bearish trade: **high of the first 15M/5M candle**.

The first candle means the first candle of the timeframe actually used for execution/refinement. Later candles must not replace this stop anchor.

The implementation must not use future candles to decide whether a historical setup existed.

Entries after C3 ends are invalid for this model.
