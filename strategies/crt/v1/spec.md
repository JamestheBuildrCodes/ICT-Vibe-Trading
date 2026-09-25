# CRT-3C-v1.0 Strategy Specification

## Required sequence

1. Identify C1 on 4H.
2. Identify C2 as the required sweep and close back inside.
3. Define C3 as the next 4H execution window.
4. Confirm that an aligned 15M FVG already existed before C3 began.
5. Require price to touch/react to that FVG during C3.
6. Optionally refine execution with a nested 5M FVG.
7. Execute only inside C3.

## Stop-loss rule

The stop-loss anchor is determined by the **first candle of the selected execution timeframe**:

- **Bullish trade:** SL = the **LOW of the first 15M/5M execution candle**.
- **Bearish trade:** SL = the **HIGH of the first 15M/5M execution candle**.

If 15M is the selected execution timeframe, use the first 15M candle. If 5M is selected for the refined execution, use the first 5M candle. Do not move the stop anchor to a later execution candle or the 4H C1/C2 extreme.

## Explicit exclusions

- 15M displacement is not a mandatory confirmation gate.
- 5M refinement is not mandatory.
- A confirmation candle after C2 is not part of this canonical model.
