# Roadmap

## Phase 1 — Foundation

- Repository architecture
- Canonical CRT-3C-v1.0 specification
- Strategy invariants and tests

## Phase 2 — Core concepts

- Liquidity
- Fair Value Gaps
- Order Blocks
- Breakers
- Displacement
- Market Structure
- Turtle Soup

## Phase 3 — Models

- ICT core models
- MMXM
- **TTrades Fractal Model v1 specification + mechanical research engine**
- Additional versioned CRT variants

## Phase 4 — Research engine

- Historical data adapters
- Look-ahead-safe event engine
- Position and risk models
- Metrics and reports
- Walk-forward research
- TTFM 4H -> 15M/5M historical backtest

## Phase 5 — Execution

- MT5 adapter
- Demo/paper execution
- Broker-neutral adapters
- Execution safeguards

## TTFM v1 status

The first TTFM strategy specification and broker-neutral core have been added under `strategies/ttfm/v1/`.

Current implementation covers:

- Daily/HTF directional-context gate as an upstream requirement
- 4H wick/sweep -> protected swing/CISD framework
- 15M/5M FVG retest execution
- Protected-swing invalidation
- Exact 3R target requirement
- London AM / London PM / New York AM session gate
- SMT and other TTrades-style confluences as scored confirmation layers
- Look-ahead-safe design requirements

The next phase is data integration and historical validation. No profitability claim is made until the model survives out-of-sample and walk-forward testing.
