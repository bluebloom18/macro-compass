"""Numbers for the weekly macro debate page: the market table, regime vote counts per axis,
the last five Risk Barometer weeks and the five-session vote changes.

Run from the repo root after build.py, finalize.py and risk_barometer.py (it reads their
output and reuses build.py's fetch cache). Writes weekly_debate/debate_data.json, which
assemble.py merges with the week's narrative."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from build import cached_fetch  # noqa: E402
from fetch import fetch_yahoo, fetch_fred  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# (label, cache key, fetcher, kind) - kind "pct" shows % changes, "bp" shows basis-point changes
MARKETS = [
    ("S&P 500",            "yahoo_^GSPC",        lambda: fetch_yahoo("^GSPC"),    "pct"),
    ("Russell 2000",       "yahoo_^RUT",         lambda: fetch_yahoo("^RUT"),     "pct"),
    ("STOXX 600",          "yahoo_^STOXX",       lambda: fetch_yahoo("^STOXX"),   "pct"),
    ("Nikkei 225",         "yahoo_^N225",        lambda: fetch_yahoo("^N225"),    "pct"),
    ("Hang Seng",          "yahoo_^HSI",         lambda: fetch_yahoo("^HSI"),     "pct"),
    ("Emerging markets (EEM)", "yahoo_EEM",      lambda: fetch_yahoo("EEM"),      "pct"),
    ("US dollar index",    "yahoo_DX-Y.NYB",     lambda: fetch_yahoo("DX-Y.NYB"), "pct"),
    ("Brent crude",        "yahoo_BZ=F",         lambda: fetch_yahoo("BZ=F"),     "pct"),
    ("Copper",             "yahoo_HG=F",         lambda: fetch_yahoo("HG=F"),     "pct"),
    ("Gold",               "yahoo_GC=F",         lambda: fetch_yahoo("GC=F"),     "pct"),
    ("US 10Y yield",       "yahoo_^TNX",         lambda: fetch_yahoo("^TNX"),     "bp"),
    ("US 2Y yield",        "fred_DGS2",          lambda: fetch_fred("DGS2"),      "bp"),
    ("US high-yield spread", "fred_BAMLH0A0HYM2", lambda: fetch_fred("BAMLH0A0HYM2"), "bp"),
    ("VIX",                "yahoo_^VIX",         lambda: fetch_yahoo("^VIX"),     "pct"),
]


def closes(rows):
    out = []
    for r in rows:
        v = r.get("close", r.get("value"))
        if v is not None:
            out.append((r["date"], float(v)))
    return out


def market_row(label, key, fn, kind):
    s = closes(cached_fetch(key, fn))
    date, last = s[-1]
    month_ago = s[-22][1] if len(s) > 21 else s[0][1]
    year = [v for _, v in s[-252:]]
    hi = max(year)
    if kind == "bp":
        chg = (last - month_ago) * 100
        vs_hi = (last - hi) * 100
    else:
        chg = (last / month_ago - 1) * 100
        vs_hi = (last / hi - 1) * 100
    return dict(name=label, date=date, level=last, kind=kind,
                m1=round(chg, 1 if kind == "pct" else 0), vs_high=round(vs_hi, 1 if kind == "pct" else 0))


def axis_counts(per_signal):
    ax = {a: {"up": [], "down": [], "flat": []} for a in ("growth", "inflation")}
    for s in per_signal:
        w = s.get("weights")
        if not w:
            continue
        a = "inflation" if (w[0] == w[3] and w[1] == w[2] and w[0] != w[1]) else "growth"
        if not s.get("ready") or not (s.get("bull") or s.get("bear")):
            ax[a]["flat"].append(s["label"])
            continue
        v = w if s["bull"] else [1 - x for x in w]
        up = (v[0] and v[1]) if a == "growth" else (v[1] and v[2])
        ax[a]["up" if up else "down"].append(s["label"])
    return ax


def main():
    latest = json.load(open(os.path.join(ROOT, "latest_doc.json")))
    risk = json.load(open(os.path.join(ROOT, "risk_doc.json")))

    markets = []
    for label, key, fn, kind in MARKETS:
        try:
            markets.append(market_row(label, key, fn, kind))
        except Exception as e:  # a missing series drops its row, never the page
            print("market row failed:", label, e, file=sys.stderr)

    barometer = dict(
        dates=risk["dates"][-5:],
        index=[None if v is None else round(v, 2) for v in risk["index"][-5:]],
        pillars=[dict(name=p["name"], scores=[None if v is None else round(v, 2) for v in p["scores"][-5:]])
                 for p in risk["pillars"]],
        thresh=risk.get("thresh", 0.3333),
    )

    out = dict(
        asof=latest["asof"],
        complete=latest.get("complete", True),
        regime=dict(code=latest["regime_code"], name=latest["regime_name"],
                    pct=[round(latest[k], 1) for k in ("goldilocks_pct", "reflation_pct", "inflation_pct", "deflation_pct")]),
        axes=axis_counts(latest["per_signal"]),
        vote_changes_5d=latest.get("vote_changes_5d"),
        barometer=barometer,
        markets=markets,
    )
    path = os.path.join(HERE, "debate_data.json")
    with open(path, "w") as f:
        json.dump(out, f, indent=1)
    print("Wrote", path)
    print("regime:", out["regime"]["name"], out["regime"]["pct"])
    for a, c in out["axes"].items():
        print(a, {k: len(v) for k, v in c.items()})
    print("risk index:", barometer["dates"][-1], barometer["index"][-1])
    for m in markets:
        print("  %-24s %s %10.2f  1m %6s  vs hi %6s" % (m["name"], m["date"], m["level"], m["m1"], m["vs_high"]))


if __name__ == "__main__":
    main()
