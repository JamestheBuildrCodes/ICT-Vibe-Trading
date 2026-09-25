# TTrades Fractal Model — Source Map

This document records the public TTrades material used to translate the model into mechanical rules. The implementation is deliberately constrained to TTrades' framework rather than adding a separate ICT strategy.

## Primary TTrades material reviewed

- Intro To TTrades Fractal Model — establishes the fractal-model concept, indicator settings, overlap and foundation topics.
- TTrades Fractal Model education library — model-specific material including London, Candle 2, IC-CISD, protected swings, SMT, targets, dealing ranges and daily profiles.
- How to Trade London Using TTrades Fractal Model — daily bias -> daily wick -> 4H wick/swing -> 15M protected swing -> expansion.
- How to Trade Candle 2 — higher-timeframe sweep, CISD/protected swing and lower-timeframe execution.
- Protected Swings in Trading — protected swings as continuation structure and invalidation anchors; lower-timeframe refinement with 5M/15M.
- Stop Loss Mastery — stops beyond protected swings.
- Intracandle CISD — confirms that the higher-timeframe wick has formed before trading the body/expansion.
- TTrades Ideal Formation — stronger Candle 2/Candle 3 formations when closure also establishes a protected swing.
- How To Use SMT Divergence — SMT is a confirmation/confluence tool, not the foundation of the trade.
- How to Set Price Targets — targets should come from higher-timeframe structure/liquidity rather than arbitrary prediction.
- Understanding Dealing Ranges — dealing range is defined from the swing traded away from toward the higher-timeframe swing.
- Let The Wick Form, Trade The Body — do not anticipate the higher-timeframe expansion before the wick has formed.
- Stop Overcomplicating Trading — keep the model centered on higher-timeframe bias, Candle 2/3 structure, CISD and continuation rather than stacking unrelated indicators.

## Mechanical translation used by TTFM v1

| TTrades concept | Bot implementation |
|---|---|
| Higher-timeframe bias | Upstream daily/HTF directional gate |
| Wick formation | 4H sweep/relevant liquidity interaction |
| Candle closure / state change | Protected swing + CISD confirmation |
| Fractal execution | 15M or 5M |
| FVG | Directionally aligned execution POI |
| Entry | FVG retest/touch after confirmation |
| Invalidation | Protected swing |
| Target | Fixed 3R, subject to structural room |
| SMT | Optional high-value confluence, never standalone |
| Session | London AM, London PM, New York AM |

## Fidelity rules

1. A sweep by itself never creates an entry.
2. SMT by itself never creates an entry.
3. An FVG by itself never creates an entry.
4. The bot must wait for the model's structural confirmation before using the lower timeframe.
5. The bot must not manufacture a setup because a session is open.
6. The bot must not use future candle information in historical tests.
7. CRT logic remains separate and must not be silently substituted for TTFM.

## Research limitation

YouTube's direct playlist pages were throttled in the research environment. The translation therefore uses TTrades' official education pages/PDF-linked material and the indexed metadata for the supplied videos/playlists rather than pretending that every video transcript in every playlist was fully retrieved. This source map should be expanded whenever additional official TTrades material becomes available to the research pipeline.
