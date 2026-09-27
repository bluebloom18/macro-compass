"""Signal definitions for the open-data port of the Global Macro Regime indicator.

Each entry: key -> dict(label, kind, weights=(goldilocks, reflation, inflation, deflation), ...)
kind is one of: "yahoo" (single symbol), "ratio" (two yahoo symbols, A/B), "fred" (FRED series id).
`invert_yield` marks the three signals sourced from a bond-ETF PRICE as a proxy for a YIELD:
price direction is the inverse of yield direction, so the weight tuple must be the complement of
what the original (yield-sourced) signal used - see the review notes for why.
"""

SIGNALS = {
    "spx":     dict(label="S&P 500 Index",              kind="yahoo", symbol="^GSPC",   weights=(1, 1, 0, 0)),
    "rut":     dict(label="Russell 2000 Index",          kind="yahoo", symbol="^RUT",    weights=(1, 1, 0, 0)),
    "sxxp":    dict(label="STOXX 600 Index",             kind="yahoo", symbol="^STOXX",  weights=(1, 1, 0, 0)),
    "ni225":   dict(label="Nikkei 225 Index",            kind="yahoo", symbol="^N225",   weights=(1, 1, 0, 0)),
    "hsi":     dict(label="Hang Seng Index",             kind="yahoo", symbol="^HSI",    weights=(1, 1, 0, 0)),
    "mme1":    dict(label="Emerging Markets (EEM proxy)", kind="yahoo", symbol="EEM",    weights=(1, 1, 0, 0),
                     note="Original used ICE MME1! EM futures; EEM ETF is the open-data equivalent."),
    "beta":    dict(label="High Beta / Low Volatility",  kind="ratio", symbolA="SPHB", symbolB="SPLV", weights=(1, 1, 0, 0)),
    "cycl":    dict(label="Cyclicals / Defensives",       kind="ratio", symbolA="XLY", symbolB="XLP", weights=(1, 1, 0, 0)),
    "vix":     dict(label="S&P 500 Volatility Index",     kind="yahoo", symbol="^VIX",   weights=(0, 0, 1, 1)),
    "move":    dict(label="US Bond Volatility Index",     kind="yahoo", symbol="^MOVE",  weights=(0, 0, 1, 1)),
    # cor3m (3M Implied Correlation) dropped: ^COR3M has no historical series on Yahoo, only a
    # live snapshot (1 bar for any range). Rather than fake a substitute, this runs on 29 signals.
    "brn1":    dict(label="Brent Crude Oil Futures",      kind="yahoo", symbol="BZ=F",   weights=(0, 1, 1, 0)),
    "dba":     dict(label="Agricultural Commodities",     kind="yahoo", symbol="DBA",    weights=(0, 1, 1, 0)),
    "dbb":     dict(label="Industrial Metals",            kind="yahoo", symbol="DBB",    weights=(1, 1, 0, 0)),
    "si1gc1":  dict(label="Silver / Gold Ratio",          kind="ratio", symbolA="SI=F", symbolB="GC=F", weights=(1, 1, 0, 0)),
    "hg1":     dict(label="Copper Futures",               kind="yahoo", symbol="HG=F",   weights=(1, 1, 0, 0)),
    "btc":     dict(label="Bitcoin",                      kind="yahoo", symbol="BTC-USD", weights=(1, 1, 0, 0)),
    "dxy":     dict(label="US Dollar Index",              kind="yahoo", symbol="DX-Y.NYB", weights=(0, 0, 1, 1)),
    "audusd":  dict(label="AUD/USD FX Rate",              kind="yahoo", symbol="AUDUSD=X", weights=(1, 1, 0, 0)),
    "eurusd":  dict(label="EUR/USD FX Rate",              kind="yahoo", symbol="EURUSD=X", weights=(1, 1, 0, 0)),
    "gbpusd":  dict(label="GBP/USD FX Rate",              kind="yahoo", symbol="GBPUSD=X", weights=(1, 1, 0, 0)),
    "eu10y":   dict(label="German 10Y Bund Yield",        kind="yahoo", symbol="EXHD.DE", weights=(1, 0, 0, 1), invert_yield=True,
                     note="Proxy: iShares eb.rexx Germany 5.5-10.5yr Govt Bond ETF price (inverse of yield); weights complemented."),
    "gb10y":   dict(label="UK 10Y Gilt Yield",            kind="yahoo", symbol="IGLT.L", weights=(1, 0, 0, 1), invert_yield=True,
                     note="Proxy: iShares Core UK Gilts UCITS ETF price (inverse of yield); weights complemented."),
    "jp10y":   dict(label="Japan 10Y JGB Yield",          kind="yahoo", symbol="236A.T", weights=(1, 0, 0, 1), invert_yield=True,
                     note="Proxy: iShares 7-10Y Japan Govt Bond ETF price (inverse of yield); weights complemented."),
    "us02y":   dict(label="US 2Y Treasury Yield",         kind="fred", series="DGS2",  weights=(0, 1, 1, 0)),
    "us10y":   dict(label="US 10Y Treasury Yield",        kind="fred", series="DGS10", weights=(0, 1, 1, 0)),
    "t10yie":  dict(label="US 10Y Breakeven Rate",        kind="fred", series="T10YIE", weights=(0, 1, 1, 0)),
    "dyoas":   dict(label="US Distressed Index OAS",      kind="fred", series="BAMLH0A3HYC", weights=(0, 0, 1, 1)),
    "hyoas":   dict(label="US High Yield Index OAS",      kind="fred", series="BAMLH0A0HYM2", weights=(0, 0, 1, 1)),
    "igoas":   dict(label="US Corporate Index OAS",       kind="fred", series="BAMLC0A0CM", weights=(0, 0, 1, 1)),
}

MASTER_CALENDAR_SYMBOL = "^GSPC"

DEFAULTS = dict(
    signal_type="Trend", cross_type="EMA", fast_input=30, slow_input=60, cross_margin=0.3,
    vol_type="ATR", vol_input=10, vol_factor=3.0, neutral_active=True, smooth_input=1,
)
