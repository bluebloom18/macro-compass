"""Risk Barometer: 8-pillar weekly risk-on/risk-off score on free data (Yahoo + FRED).

A reconstruction of the Lion Global Investors "Risk Barometer Index" layout (8 pillars,
red/amber/green cells, one averaged Risk Index), whose methodology isn't published.
Reverse-engineered from a 5-week sample of their table: cells are a continuous score
bucketed into colours (same colours, different index values), the index is the equal-weight
pillar mean, and the red/amber line on the index sits between -0.30 and -0.44.

Each input is sampled weekly (Friday close, or the latest bar for the current week), turned
into a 3-year rolling z-score, signed so positive = risk-on, and clipped to [-1, 1]. A pillar
is the mean of its inputs; the Risk Index is the mean of the 8 pillars. Cells are coloured
Risk On / Neutral / Off at +/-1/3.

Writes risk_doc.json, the dashboard doc (db: regime/risk). --refresh refetches the raw data
(otherwise the cache/ copy is reused).
"""
import datetime as dt
import json
import math
import os
import sys

from fetch import fetch_yahoo, fetch_fred

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache", "risk_barometer_raw.json")

# key -> (source, symbol, release lag in calendar days, so a print is only used once public).
# NFCI/WEI: week ending Fri/Sat, out the following Wed/Thu. WALCL/WTREGEN: Wednesday level,
# out Thursday. UMCSENT: FRED posts the monthly final with roughly a month's delay.
SOURCES = {
    "spx":    ("yahoo", "^GSPC", 0),
    "vix":    ("yahoo", "^VIX", 0),
    "vix3m":  ("yahoo", "^VIX3M", 0),
    "move":   ("yahoo", "^MOVE", 0),
    "dxy":    ("yahoo", "DX-Y.NYB", 0),
    "audjpy": ("yahoo", "AUDJPY=X", 0),
    "emfx":   ("yahoo", "CEW", 0),
    "gold":   ("yahoo", "GC=F", 0),
    "hyg":    ("yahoo", "HYG", 0),
    "lqd":    ("yahoo", "LQD", 0),
    "emb":    ("yahoo", "EMB", 0),
    "ief":    ("yahoo", "IEF", 0),
    "us10y":  ("fred", "DGS10", 1),
    "nfci":   ("fred", "NFCI", 5),
    "walcl":  ("fred", "WALCL", 1),
    "tga":    ("fred", "WTREGEN", 1),
    "rrp":    ("fred", "RRPONTSYD", 1),
    "wei":    ("fred", "WEI", 5),
    "umich":  ("fred", "UMCSENT", 30),
}

# Pillar -> inputs: (series, transform, param, sign).
#   level: the value itself          chg: change over `param` weeks
#   ma:    deviation from its `param`-week moving average
# Changes/deviations are log for LOG_SERIES (prices), plain differences otherwise.
# sign = +1 if a rising value is risk-on, -1 if risk-off.
PILLARS = [
    # WEI 4w change stands in for the (non-free) Citi surprise index; UMich for sentiment
    ("Economic Surprise & Sentiment", [("wei", "chg", 4, +1), ("umich", "level", 0, +1)]),
    ("Liquidity",                     [("nfci", "chg", 4, -1), ("netliq", "chg", 4, +1)]),
    # ETF/IEF ratios rather than FRED's ICE BofA OAS, which only serve the last 3 years
    ("Credit Spreads",                [("hyg_ief", "chg", 4, +1), ("lqd_ief", "chg", 4, +1),
                                       ("emb_ief", "chg", 4, +1)]),
    ("Rates",                         [("us10y", "chg", 4, -1), ("move", "level", 0, -1)]),
    # no free survey/CFTC feed: proxy trend-follower (CTA) positioning by S&P vs its 10w average
    ("Equity Market Positioning",     [("spx", "ma", 10, +1)]),
    ("Equity Volatility",             [("vix", "level", 0, -1), ("vix_ts", "level", 0, -1)]),
    ("Foreign Exchange",              [("dxy", "chg", 4, -1), ("audjpy", "chg", 4, +1),
                                       ("emfx", "chg", 4, +1)]),
    # gold stretched high vs its 3y history = hedging demand = risk-off
    ("Gold",                          [("gold", "level", 0, -1)]),
]

# dashboard labels per input series: (label, source)
LABELS = {
    "wei":     ("Weekly Economic Index, 4w chg", "FRED WEI"),
    "umich":   ("UMich consumer sentiment", "FRED UMCSENT"),
    "nfci":    ("Financial conditions (NFCI), 4w chg", "FRED NFCI"),
    "netliq":  ("US net liquidity, 4w chg", "FRED WALCL-WTREGEN-RRP"),
    "hyg_ief": ("High yield vs Treasuries, 4w", "HYG/IEF"),
    "lqd_ief": ("Investment grade vs Treasuries, 4w", "LQD/IEF"),
    "emb_ief": ("EM debt vs Treasuries, 4w", "EMB/IEF"),
    "us10y":   ("US 10Y yield, 4w chg", "FRED DGS10"),
    "move":    ("Bond volatility (MOVE)", "^MOVE"),
    "spx":     ("S&P 500 vs 10w average", "^GSPC"),
    "vix":     ("VIX level", "^VIX"),
    "vix_ts":  ("VIX / VIX3M term structure", "^VIX/^VIX3M"),
    "dxy":     ("US dollar index, 4w chg", "DX-Y.NYB"),
    "audjpy":  ("AUD/JPY, 4w chg", "AUDJPY=X"),
    "emfx":    ("EM currencies, 4w chg", "CEW"),
    "gold":    ("Gold vs 3y history", "GC=F"),
}

LOG_SERIES = {"spx", "dxy", "audjpy", "emfx", "gold", "netliq", "hyg_ief", "lqd_ief", "emb_ief"}

PARAMS = dict(window=156, scale=1.0, thresh=1 / 3)


# ---------- data ----------

def load_raw(refresh=False):
    if not refresh and os.path.exists(CACHE):
        return json.load(open(CACHE))
    raw = {}
    for key, (kind, sym, lag) in SOURCES.items():
        rows = fetch_yahoo(sym, range_="15y") if kind == "yahoo" else fetch_fred(sym)
        raw[key] = {"lag": lag, "rows": [[r["date"], r["close"]] for r in rows]}
        print("  %-7s %-14s %5d rows  last %s" % (key, sym, len(rows), rows[-1]["date"]), file=sys.stderr)
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    json.dump(raw, open(CACHE, "w"))
    return raw


def week_ends(last_date, start="2011-01-07"):
    """Fridays from start to last_date, plus last_date itself if it isn't a Friday (the 'Current' column)."""
    d = dt.date.fromisoformat(start)
    end = dt.date.fromisoformat(last_date)
    out = []
    while d <= end:
        out.append(d.isoformat())
        d += dt.timedelta(days=7)
    if out[-1] != last_date:
        out.append(last_date)
    return out


def sample(rows, lag, dates):
    """Value known as of each date: last observation whose (obs date + lag) <= date."""
    avail = [((dt.date.fromisoformat(d) + dt.timedelta(days=lag)).isoformat(), v) for d, v in rows]
    out, j, last = [], 0, None
    for d in dates:
        while j < len(avail) and avail[j][0] <= d:
            last = avail[j][1]
            j += 1
        out.append(last)
    return out


def weekly_panel(raw, last_date=None):
    if last_date is None:
        last_date = raw["spx"]["rows"][-1][0]
    dates = week_ends(last_date)
    s = {k: sample(v["rows"], v["lag"], dates) for k, v in raw.items()}

    def combo(f, *keys):
        return [f(*vals) if None not in vals else None for vals in zip(*(s[k] for k in keys))]

    # US net liquidity, $bn: Fed balance sheet - TGA - reverse repo (WALCL/WTREGEN in $mn, RRP in $bn)
    s["netliq"] = combo(lambda w, t, r: w / 1000 - t / 1000 - r, "walcl", "tga", "rrp")
    s["vix_ts"] = combo(lambda a, b: a / b if b else None, "vix", "vix3m")
    for k in ("hyg", "lqd", "emb"):
        s[k + "_ief"] = combo(lambda a, b: a / b if b else None, k, "ief")
    return dates, s


# ---------- scoring ----------

def transform(x, kind, prm, log):
    if kind == "level":
        return list(x)
    out = [None] * len(x)
    for i in range(prm, len(x)):
        if kind == "chg":
            a, b = x[i], x[i - prm]
            if a is None or b is None:
                continue
        else:  # ma
            win = x[i - prm + 1:i + 1]
            if None in win:
                continue
            a, b = x[i], sum(win) / prm
        out[i] = (math.log(a / b) if a > 0 and b > 0 else None) if log else a - b
    return out


def rolling_z(x, window):
    out = [None] * len(x)
    for i in range(len(x)):
        if x[i] is None:
            continue
        win = [v for v in x[max(0, i - window + 1):i + 1] if v is not None]
        if len(win) < window // 2:
            continue
        m = sum(win) / len(win)
        sd = math.sqrt(sum((v - m) ** 2 for v in win) / (len(win) - 1))
        out[i] = (x[i] - m) / sd if sd > 0 else 0.0
    return out


def compute(dates, s, p=PARAMS, pillars=PILLARS):
    pillar_scores, inputs = {}, {}
    for name, ins in pillars:
        cols = []
        for key, kind, prm, sign in ins:
            z = rolling_z(transform(s[key], kind, prm, key in LOG_SERIES), p["window"])
            sc = [None if v is None else sign * max(-1.0, min(1.0, v / p["scale"])) for v in z]
            inputs["%s:%s%s" % (key, kind, prm or "")] = sc
            cols.append(sc)
        pillar_scores[name] = [None if None in v else sum(v) / len(v) for v in zip(*cols)]
    index = [None if None in v else sum(v) / len(v) for v in zip(*pillar_scores.values())]
    return pillar_scores, index, inputs


def colour(v, thresh):
    if v is None:
        return None
    return "on" if v > thresh else "off" if v < -thresh else "neutral"


def fwd_stats(dates, index, spx, thresh):
    """Forward S&P 500 price return after each weekly reading, by colour bucket."""
    out = []
    for name, test in (("off", lambda v: v < -thresh), ("neutral", lambda v: -thresh <= v <= thresh),
                       ("on", lambda v: v > thresh)):
        row = {"bucket": name}
        for h in (4, 13):
            r = [spx[i + h] / spx[i] - 1 for i, v in enumerate(index)
                 if v is not None and test(v) and i + h < len(dates) and spx[i] and spx[i + h]]
            row["n%d" % h] = len(r)
            row["avg%d" % h] = round(sum(r) / len(r), 4) if r else None
            row["hit%d" % h] = round(sum(x > 0 for x in r) / len(r), 3) if r else None
        out.append(row)
    return out


def dashboard_doc(dates, s, pillars, index, inputs, first):
    """Compact doc for the dashboard (db: regime/risk): weekly index + pillar history,
    last-5-column input scores for the drill-down, forward-return stats."""
    def r2(col):
        return [None if v is None else round(v, 2) for v in col[first:]]

    last5 = slice(len(dates) - 5, len(dates))
    return {
        "asof": dates[-1],
        "thresh": round(PARAMS["thresh"], 4),
        "window_weeks": PARAMS["window"],
        "dates": dates[first:],
        "index": r2(index),
        "pillars": [{
            "name": name,
            "scores": r2(pillars[name]),
            "inputs": [{
                "label": LABELS[key][0], "source": LABELS[key][1],
                "last5": [None if v is None else round(v, 2)
                          for v in inputs["%s:%s%s" % (key, kind, prm or "")][last5]],
            } for key, kind, prm, sign in ins],
        } for name, ins in PILLARS],
        "fwd": fwd_stats(dates[first:], index[first:], s["spx"][first:], PARAMS["thresh"]),
    }


def main():
    raw = load_raw(refresh="--refresh" in sys.argv)
    dates, s = weekly_panel(raw)
    pillars, index, inputs = compute(dates, s)
    t = PARAMS["thresh"]
    first = next(i for i, v in enumerate(index) if v is not None)
    doc = dashboard_doc(dates, s, pillars, index, inputs, first)
    with open(os.path.join(HERE, "risk_doc.json"), "w") as f:
        json.dump(doc, f, separators=(",", ":"))
    print("Wrote risk_doc.json (%d weeks from %s, %d bytes)" % (
        len(doc["dates"]), doc["dates"][0], os.path.getsize(os.path.join(HERE, "risk_doc.json"))), file=sys.stderr)
    cols = list(range(len(dates) - 5, len(dates)))
    print("%-32s" % "" + "".join("%11s" % dates[i][5:] for i in cols))
    for name, sc in list(pillars.items()) + [("Risk Index", index)]:
        print("%-32s" % name + "".join("%11s" % ("%+.2f %s" % (sc[i], colour(sc[i], t)[:3])) for i in cols))


if __name__ == "__main__":
    main()
