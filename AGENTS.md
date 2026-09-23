# AI Agent Rules

## Source of truth

Read the relevant strategy specification and tests before changing trading behavior. Do not silently redefine a documented model.

## Canonical CRT-3C-v1.0 invariants

- C1 is the 4H range candle.
- C2 is the 4H manipulation/sweep candle.
- Bullish C2 must sweep C1 low and close back above C1 low.
- Bearish C2 must sweep C1 high and close back below C1 high.
- C3 is the next 4H candle and the execution window.
- An aligned 15M FVG must exist before C3 begins.
- Price must react to/touch that FVG during C3.
- 5M refinement is optional.
- Entries outside C3 are invalid.
- No future data may influence a historical decision.

Do not add mandatory M15 displacement confirmation or mandatory M5 confirmation to CRT-3C-v1.0.

## Engineering rules

- Separate concepts, models, strategies, backtesting, and broker adapters.
- Version behavior changes instead of silently modifying an existing strategy.
- Keep broker/API code out of strategy definitions.
- Do not optimize toward a desired win rate or return target; report measured results honestly.
- Document timeframe, timezone, spread, slippage, fees, and look-ahead controls for backtests.
