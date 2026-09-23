# Claude / AI Coding Guidance

Use `AGENTS.md` as the repository-level source of truth.

Never replace the documented CRT model with a generic ICT/SMC interpretation. If a proposed change alters entry, invalidation, timeframe, execution window, or confirmation requirements, treat it as a strategy change and version it.

Do not add extra confirmation gates merely because they improve a backtest. Preserve the canonical model and let research compare explicit variants.

Keep strategy logic broker-neutral. MT5 and other execution adapters belong outside the strategy layer.
