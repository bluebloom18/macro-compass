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
- `risk_barometer.py` — 8-pillar Risk Barometer (see below), writes `risk_doc.json`
- `finalize.py` — computes the outperform table, downsamples history, writes
  `latest_doc.json` / `history_doc.json`
- `backtest.py` — compounds dividend-adjusted SPY / QQQ / TLT / GLD returns over every
  regime spell (1-day execution lag) and writes `backtest.json`, shown in the dashboard's
  Regime backtest section
- `render.py` — local-only: bakes `template.html` + seed data into a standalone
  `macro_compass.html` for preview (the live artifact doesn't use this — it reads
  live from its own database)
- `template.html` — the dashboard page

## Running it

```
python3 build.py       # fetch + compute -> output.json
python3 finalize.py    # -> latest_doc.json, history_doc.json (+ artifact_seed.json)
python3 backtest.py    # -> backtest.json (LAG=0 for the same-close variant)
python3 risk_barometer.py --refresh   # -> risk_doc.json (Risk Barometer section)
python3 render.py      # optional: -> macro_compass.html for local preview
```

## Financial conditions (NFCI)

`nfci/` backtests risk-on/off rules on the Chicago Fed NFCI (SPY when conditions ease,
3M T-bills when they tighten) and feeds the dashboard's Financial conditions section.

- `nfci_backtest.py` — level, moving-average and rate-of-change rules on two information
  sets: today's revised FRED history, and ALFRED real-time vintages (May 2011+, fetched into
  `nfci/cache/vintages/`, one per Thursday). The NFCI is re-estimated every week, so the
  revised history badly overstates short-lookback rules — judge rules on the real-time run.
- `roc1w_variants.py` — 1-week-change variants (execution day, dead-band, confirmation,
  contrarian) on real-time data.
- `nfci_doc.py` — writes the dashboard doc (`regime/nfci`): full NFCI history, first-print
  weekly changes (persisted in `first_prints.csv`), and `backtest_summary.json`.

## Risk Barometer

`risk_barometer.py` builds an 8-pillar weekly risk-on/risk-off score modelled on Lion Global
Investors' Risk Barometer table (Economic Surprise & Sentiment, Liquidity, Credit Spreads, Rates,
Equity Positioning, Equity Volatility, FX, Gold). Their method isn't published; this one was
reverse-engineered from a 5-week sample of their table and fitted loosely to it (index within
~0.11 on average, 18/32 cell colours), using free data only.

- Each input is sampled weekly (Friday, plus the latest day as "Current"), turned into a 3-year
  rolling z-score of its level, 4-week change or distance from a moving average, signed so
  positive = risk-on, and clipped to +/-1. A pillar averages its inputs; the Risk Index averages
  the 8 pillars. Colours at +/-0.33.
- Free-data proxies: Fed Weekly Economic Index for the (paid) Citi surprise index, S&P 500 vs its
  10-week average for trend-follower positioning, HYG/LQD/EMB vs IEF for credit spreads (FRED's
  ICE BofA OAS series only serve the last 3 years). Weekly FRED prints are lagged to their
  release date.
- It's a coincident stress gauge, not a timing signal: since 2013 the S&P 500 has done better
  over the following 4-13 weeks after risk-off readings than after risk-on ones.

## Keeping the live dashboard current

A daily scheduled cloud routine (`Macro Compass daily refresh`, 22:00 UTC) clones this
repo, runs `build.py` → `finalize.py` → `backtest.py` → `risk_barometer.py` → `nfci/nfci_doc.py`,
and writes the results straight into the published artifact's database (`regime/latest`,
`regime/history`, `regime/backtest`, `regime/risk`, `regime/nfci`)
via Claude's `write_db` — the dashboard updates for every viewer without anyone needing to
run anything by hand. Pipeline changes take effect on the next run once pushed to `master`;
changes to `template.html` still need a republish of the artifact page.
