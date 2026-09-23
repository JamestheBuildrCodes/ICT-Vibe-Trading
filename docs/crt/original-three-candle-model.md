# CRT-3C-v1.0 — Original Three-Candle Model

## 4H structure

### C1 — Range candle
C1 establishes the reference high and low.

### C2 — Manipulation / sweep candle
For a bullish setup:

- `C2.low < C1.low`
- `C2.close > C1.low`

For a bearish setup:

- `C2.high > C1.high`
- `C2.close < C1.high`

C2 must sweep the relevant C1 extreme and close back inside the C1 range.

### C3 — Execution window
C3 is the next 4H candle. It is the execution window, not another confirmation candle.

## Execution chain

`C1 range -> C2 sweep + close inside -> C3 execution window -> existing 15M FVG reaction -> optional 5M refinement -> entry`
