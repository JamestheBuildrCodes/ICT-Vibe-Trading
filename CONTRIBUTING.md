# Contributing

1. Read `AGENTS.md` and the relevant strategy specification.
2. Add or update tests for behavioral changes.
3. Never introduce future-data leakage.
4. Keep broker integrations separate from strategy logic.
5. Document assumptions such as timeframe, timezone, spread and slippage.
6. Changes to entry, invalidation, confirmation, timeframe, or execution window are strategy changes, not simple refactors; version them explicitly.
