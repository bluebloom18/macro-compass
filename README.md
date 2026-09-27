# Macro Compass

A 29-signal cross-asset growth/inflation regime nowcast, ported from the TradingView
indicator [Global Macro Regime](https://www.tradingview.com/script/tDChNs5m-Global-Macro-Regime/)
(© QuantitativeAlpha) to run entirely on open data — no TradingView subscription,
no API key.

**Live dashboard:** https://claude.ai/artifact/UEmVNVipYMysgeGjCV8DPm

## What it does

Same methodology as the original Pine Script: an EMA(30)/EMA(60) trend-cross signal
(ATR-scaled margin) run across cross-asset tickers, each contributing a weighted vote
toward one of four regimes — Goldilocks, Reflation, Inflation, Deflation — with a
sticky-regime aggregation so the reading doesn't flip on ties. Ported line-for-line
into Python (`model.py`) rather than approximated.

## Data sources

| Source | Used for |
|---|---|
| [Yahoo Finance chart API](https://query1.finance.yahoo.com/v8/finance/chart/) | Equities, FX, commodities, VIX, MOVE, 3M correlation, bond-ETF yield proxies |
| [FRED CSV endpoint](https://fred.stlouisfed.org/graph/fredgraph.csv) | US 2Y/10Y Treasury yields, 10Y breakeven, credit spreads (distressed/HY/IG OAS) |

Both are public, unauthenticated endpoints. FRED's endpoint blocks Python's `urllib`
client at the TLS layer for reasons that were never fully clear — `fetch.py` shells
out to `curl` for FRED requests instead.

### Deviations from the original 30-signal script

- **`COR3M` (3M implied correlation) dropped** — Yahoo's `^COR3M` ticker has no
  historical series, only a live snapshot. Rather than fabricate a substitute, this
  runs on 29 signals.
- **German/UK/Japan 10Y yields** use a duration-matched government bond ETF price
  (`EXHD.DE`, `IGLT.L`, `236A.T`) as an inverse proxy — no free daily sovereign yield
  series exists for these. Weights are complemented accordingly since price moves
  inverse to yield.
- **Emerging markets** uses the `EEM` ETF in place of the original's `ICE MME1!`
  futures contract.

Everything else — including credit spreads, breakevens, and US Treasury yields —
comes from FRED directly, which is *more* faithful than the original script's
TradingView-side ETF-ratio workaround for those series.

## Files

- `config.py` — the 29 signal definitions (source, ticker, regime weights)
- `fetch.py` — Yahoo Finance / FRED fetchers, plus ratio-symbol construction
- `align.py` — forward-fill alignment onto a common trading calendar
- `model.py` — faithful port of `signal_logic()`, `vol_stop()`, `regime_contribution()`
- `build.py` — orchestrates fetch → align → signal computation → regime aggregation
- `finalize.py` — computes the outperform table, downsamples history, writes
  `latest_doc.json` / `history_doc.json`
- `render.py` — local-only: bakes `template.html` + seed data into a standalone
  `macro_compass.html` for preview (the live artifact doesn't use this — it reads
  live from its own database)
- `template.html` — the dashboard page

## Running it

```
python3 build.py       # fetch + compute -> output.json
python3 finalize.py    # -> latest_doc.json, history_doc.json (+ artifact_seed.json)
python3 render.py      # optional: -> macro_compass.html for local preview
```

## Keeping the live dashboard current

A daily scheduled cloud routine (`Macro Compass daily refresh`, 22:00 UTC) re-runs
this pipeline and writes the result straight into the published artifact's database
via Claude's `write_db` — the dashboard updates for every viewer without anyone
needing to run anything by hand. That routine currently pulls its copy of these
files from the artifact's own published supporting files (via `read_file`), not
from this repo — the two are kept in sync by hand whenever the pipeline changes.
