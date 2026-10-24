# Macro Compass

A 30-signal cross-asset growth/inflation regime nowcast, ported from the TradingView
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

Vote weighting: every signal with a trend casts exactly two votes (a flat signal casts none),
for the two regimes on the side of its axis it points to. 22 of the 30 signals sit on the growth
axis (equities, FX, metals, bitcoin, vol, correlation, dollar, credit spreads) and 8 on the inflation axis (oil,
agriculture, yields, breakevens), so growth dominates the call. The percentages are each
regime's share of the votes cast that day.

## Data sources

| Source | Used for |
|---|---|
| [Yahoo Finance chart API](https://query1.finance.yahoo.com/v8/finance/chart/) | Equities, FX, commodities, VIX, MOVE, US 10Y yield (`^TNX`), bond-ETF yield proxies |
| [Cboe index history CSV](https://cdn.cboe.com/api/global/us_indices/daily_prices/COR3M_History.csv) | 3M implied correlation (COR3M) |
| [FRED CSV endpoint](https://fred.stlouisfed.org/graph/fredgraph.csv) | US 2Y Treasury yield, 10Y breakeven, credit spreads (distressed/HY/IG OAS) |

Both are public, unauthenticated endpoints. FRED's endpoint blocks Python's `urllib`
client at the TLS layer for reasons that were never fully clear — `fetch.py` shells
out to `curl` for FRED requests instead.

### Deviations from the original script

All 30 of the original's signals are present; these are sourced differently:


- **German/UK/Japan 10Y yields** use a duration-matched government bond ETF price
  (`EXHD.DE`, `IGLT.L`, `236A.T`) as an inverse proxy — no free daily sovereign yield
  series exists for these. Weights are complemented accordingly since price moves
  inverse to yield.
- **Emerging markets** uses the `EEM` ETF in place of the original's `ICE MME1!`
  futures contract.

- **US 2Y yield** comes from FRED (`DGS2`), which is close-only, where the original's
  `TVC:US02Y` has full daily bars. A close-only series has a smaller ATR, so the 0.3×ATR
  trend margin is narrower. The 10Y avoids this by using Cboe's `^TNX` (full OHLC); no
  free 2Y series with intraday ranges and long history was found.
- **Bitcoin** is spot `BTC-USD` rather than `CME:BTC1!`; Yahoo continuous futures (`BZ=F`,
  `HG=F`, `SI=F`, `GC=F`) roll on different dates from TradingView's `1!` contracts.

Breakevens and credit spreads come from FRED, exactly as the original does (`FRED:` tickers).

### Matching TradingView's calculation

- Each signal is computed on its own series' trading days and then carried onto the S&P 500
  calendar, as `request.security('D', signal_logic())` does. (Filling prices onto the S&P
  calendar first would run the EMAs over the wrong number of bars for bitcoin and for
  markets with different holidays.)
- Yahoo bars are dated in the exchange's own timezone. Yahoo stamps FX daily bars at London
  midnight, i.e. 23:00 UTC during BST, so dating them in UTC put every FX bar a day early
  for half the year.
- To compare against the TradingView indicator, use a daily SPX chart with dividend
  adjustment off: the script's tickers inherit the chart's settings via `ticker.inherit`.

## Files

- `config.py` — the 30 signal definitions (source, ticker, regime weights)
- `fetch.py` — Yahoo Finance / FRED / Cboe fetchers, plus ratio-symbol construction
- `align.py` — forward-fill alignment onto a common trading calendar
- `model.py` — faithful port of `signal_logic()`, `vol_stop()`, `regime_contribution()`
- `build.py` — orchestrates fetch → align → signal computation → regime aggregation
- `risk_barometer.py` — 8-pillar Risk Barometer (see below), writes `risk_doc.json`
- `finalize.py` — computes the outperform table, samples history weekly (last trading day of
  each ISO week, plus the last ~2 years daily for the quadrant path), writes
  `latest_doc.json` / `history_doc.json`; `latest.complete` is false when any signal failed
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
- `fetch_vintages.py` — downloads the weekly ALFRED vintages into `nfci/cache/vintages/` (skips ones
  already there); run it before a full `nfci_backtest.py` run.
- `roc1w_variants.py` — 1-week-change variants (execution day, dead-band, confirmation,
  contrarian) on real-time data.
- `nfci_doc.py` — writes the dashboard doc (`regime/nfci`): full NFCI history, first-print
  weekly changes (persisted in `first_prints.csv`), `backtest_summary.json`, and `backtest_curves.json` (growth-of-100 equity curves for the same rules,
  weekly). The last two are regenerated only by a full local run and committed.

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

## Alerts

`alerts.py --prev-dir <dir>` compares tonight's `latest_doc.json` and `nfci/nfci_doc.json` with the
previous live copies (`latest.json`, `nfci.json` as saved by the refresh's database reads) and writes
`alerts.json`: a list of changes plus an email subject/body. It flags a macro regime change and any
NFCI switching-rule flip (52-week MA, 1-week change, 1-week change > 0.02), using the same rule
definitions as the dashboard tiles. It also flags an incomplete refresh: any regime signal that
failed to fetch, or a Risk Barometer / NFCI doc that wasn't produced. The refresh routine emails the
result via the Gmail connector, only when something changed.

## Keeping the live dashboard current

A daily scheduled cloud routine (`Macro Compass daily refresh`, 22:00 UTC) clones this
repo, runs `build.py` → `finalize.py` → `backtest.py` → `risk_barometer.py` → `nfci/nfci_doc.py`,
and writes the results straight into the published artifact's database (`regime/latest`,
`regime/history`, `regime/backtest`, `regime/risk`, `regime/nfci`)
via Claude's `write_db` — the dashboard updates for every viewer without anyone needing to
run anything by hand. Pipeline changes take effect on the next run once pushed to `master`;
changes to `template.html` still need a republish of the artifact page.

Data-source outages: `fetch_fred` rejects anything that isn't FRED's CSV (its site-maintenance
page comes back as HTML with HTTP 200 — seen 2026-10-21) and retries for ~4 minutes, then fails
fast for the remaining series. If any regime signal is still missing, the routine does not write
`regime/latest`, `regime/history` or `regime/backtest` — the model recomputes the whole history
every run, so a partial run would rewrite past regimes and the backtest too — and the dashboard
keeps the last complete reading while `alerts.py` emails "Refresh incomplete". The dashboard also
shows a warning naming any failed signals if a partial reading is ever live.

The routine can only push `nfci/first_prints.csv` if the Claude GitHub App has access to this
repo; otherwise the database copy (`regime/nfci`) still carries the series.
