# TTrades Fractal Model (TTFM) v1

## Scope

This strategy is an implementation of the **TTrades Fractal Model** as documented by TTrades' public educational material. It is intentionally separate from the canonical CRT model in this repository.

The implementation does **not** treat generic ICT concepts as independent entry signals. FVG, liquidity, protected swings, CISD, SMT, dealing ranges and higher-timeframe structure are subordinate components of the TTrades framework.

## Trading universe and sessions

- Primary use: intraday FX.
- Higher-timeframe model: **4H**.
- Execution timeframes: **15M** or **5M**.
- Allowed sessions: **London AM, London PM, New York AM**.
- Asia is disabled for this v1 execution model.
- Timezone is configurable; default is `America/New_York` because TTrades commonly frames session examples in New York time. The strategy engine must not infer session membership from the machine's local timezone.

## Core TTFM sequence

1. Establish higher-timeframe directional bias from the daily context.
2. Identify the 4H dealing range / relevant 4H swing and the direction of expected expansion.
3. Let the 4H candle form its wick. A sweep of the relevant prior 4H liquidity is evidence of wick formation; a sweep alone is **not** an entry.
4. Confirm the shift in delivery with a protected swing / CISD. The confirmation must be available before execution.
5. Move to 15M or 5M for execution.
6. Require a directionally aligned **FVG** created after the lower-timeframe confirmation and during the permitted session.
7. Enter on a retest/touch of that FVG. Do not enter merely because an FVG exists.
8. Stop goes beyond the protected swing that invalidates the setup, with a small configurable spread/buffer.
9. Default target is exactly **3R**. A setup is rejected if the structural draw on liquidity cannot provide at least 3R.
10. One position per model instance; no repeated entries from the same consumed FVG.

## Long model

- Daily context supports bullish delivery.
- 4H price forms a downside wick/sweep into relevant liquidity or a valid point of interest.
- 4H/15M structure confirms a bullish protected low through CISD/closure logic.
- 15M or 5M creates a bullish FVG after confirmation.
- Price returns into the bullish FVG during an allowed session.
- Entry = FVG retest price (configurable: proximal edge, midpoint, or first touch; default midpoint).
- Stop = below the protected low.
- Target = entry + 3 * risk.

## Short model

The exact inverse of the long model:

- bearish daily context;
- upside 4H sweep/wick;
- bearish protected high / CISD confirmation;
- bearish 15M/5M FVG;
- FVG retest during an allowed session;
- stop above protected high;
- target = entry - 3 * risk.

## A+ selection

The engine records confluences separately rather than turning every ICT concept into a mandatory gate.

**Hard gates:**

- valid daily/HTF directional context;
- valid 4H wick/sweep structure;
- protected swing/CISD confirmation;
- aligned 15M/5M FVG;
- FVG retest;
- allowed session;
- structural target supports >= 3R;
- no invalidation before entry;
- no look-ahead.

**A+ confluences:**

- SMT divergence supporting the reversal/continuation narrative;
- clean protected swing;
- FVG located at/near the relevant dealing-range level or POI;
- premium/discount alignment;
- untouched higher-timeframe draw on liquidity;
- early confirmation in the 4H candle, leaving meaningful expansion range;
- strong lower-timeframe displacement into the FVG.

SMT is a confirmation/confluence layer, **not the foundation of the model**.

## Invalidations

Reject or cancel a pending setup if:

- the protected swing is broken before entry;
- the FVG is fully invalidated before entry;
- the higher-timeframe premise is invalidated;
- the setup leaves the configured session window;
- the 3R target is no longer structurally reasonable;
- the same FVG has already been consumed by a prior entry.

## Backtest integrity

- All higher-timeframe values must be known at the timestamp being evaluated.
- A 4H candle may not use its final high/low/close before that candle has actually closed unless the feature is explicitly marked as intrabar/IC-CISD and calculated from lower-timeframe candles already available.
- FVGs must be timestamped by their creation candle and cannot be used before creation.
- Session filters use the configured timezone.
- Spread/slippage are explicit backtest parameters.

## Important

This is a research implementation. It does not claim that TTrades' model is guaranteed profitable, and it must be validated with out-of-sample and walk-forward testing before demo execution.
