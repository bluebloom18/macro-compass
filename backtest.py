"""Regime backtest: compounded total return of SPY / QQQ / TLT / GLD over every regime spell.

A spell runs from one regime change to the next. The regime is read off the daily
close, so by default positions switch with a 1-trading-day lag (signal at close t,
held from close t+1) to avoid trading on information not yet available; LAG=0 gives
the same-close variant. Prices are Yahoo dividend/split-adjusted closes (total return).

Requires output.json from build.py. Writes backtest.json.
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

from fetch import UA

ASSETS = {"SPY": "S&P 500", "QQQ": "Nasdaq-100", "TLT": "20Y+ Treasuries", "GLD": "Gold"}
REGIMES = {1: "Goldilocks", 2: "Reflation", 3: "Inflation", 4: "Deflation"}
LAG = int(os.environ.get("LAG", "1"))


def fetch_adjclose(symbol, period1=946684800):
    # explicit period1/period2: range=max silently downsamples to coarser-than-daily bars
    url = "https://query1.finance.yahoo.com/v8/finance/chart/%s?period1=%d&period2=%d&interval=1d" % (
        urllib.parse.quote(symbol), period1, int(time.time()))
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        r = json.loads(resp.read())["chart"]["result"][0]
    adj = r["indicators"]["adjclose"][0]["adjclose"]
    out = {}
    for t, v in zip(r["timestamp"], adj):
        if v is not None:
            out[time.strftime("%Y-%m-%d", time.gmtime(t))] = v
    return out


def main():
    d = json.load(open("output.json"))
    dates, regime = d["dates"], d["regime_history"]
    prices = {s: fetch_adjclose(s) for s in ASSETS}

    # common calendar: model dates where every asset has a price
    idx = [i for i, dt in enumerate(dates) if all(dt in prices[s] for s in ASSETS)]
    cal = [dates[i] for i in idx]
    reg = [regime[i] for i in idx]
    px = {s: [prices[s][dt] for dt in cal] for s in ASSETS}
    n = len(cal)

    # spells: [start, end) in calendar indices, shifted by LAG for execution
    first = next(i for i in range(n) if reg[i] != 0)
    bounds = [first] + [i for i in range(first + 1, n) if reg[i] != reg[i - 1]] + [n - 1]
    spells = []
    for a, b in zip(bounds[:-1], bounds[1:]):
        ea, eb = min(a + LAG, n - 1), min(b + LAG, n - 1)
        if eb <= ea:
            continue
        sp = dict(regime=reg[a], name=REGIMES[reg[a]], signal_date=cal[a], start=cal[ea], end=cal[eb],
                  days=eb - ea, open=(b == n - 1))
        for s in ASSETS:
            sp[s] = px[s][eb] / px[s][ea] - 1
        spells.append(sp)

    years_total = (n - 1 - min(first + LAG, n - 1)) / 252
    summary = {}
    for code, name in REGIMES.items():
        ss = [sp for sp in spells if sp["regime"] == code]
        days = sum(sp["days"] for sp in ss)
        row = dict(spells=len(ss), days=days, share_of_time=days / (n - 1 - first - LAG))
        for s in ASSETS:
            comp = 1.0
            for sp in ss:
                comp *= 1 + sp[s]
            row[s] = dict(compounded=comp - 1,
                          annualized=comp ** (252 / days) - 1 if days else None,
                          avg_per_spell=sum(sp[s] for sp in ss) / len(ss) if ss else None,
                          hit_rate=sum(1 for sp in ss if sp[s] > 0) / len(ss) if ss else None)
        summary[name] = row
    s0 = min(first + LAG, n - 1)
    buyhold = {s: dict(compounded=px[s][-1] / px[s][s0] - 1,
                       annualized=(px[s][-1] / px[s][s0]) ** (1 / years_total) - 1) for s in ASSETS}

    out = dict(lag=LAG, start=cal[s0], end=cal[-1], assets=ASSETS, spells=spells,
               summary=summary, buyhold=buyhold)
    def rnd(o):
        if isinstance(o, float):
            return round(o, 6)
        if isinstance(o, dict):
            return {k: rnd(v) for k, v in o.items()}
        if isinstance(o, list):
            return [rnd(v) for v in o]
        return o
    json.dump(rnd(out), open("backtest.json", "w"), separators=(",", ":"))

    print("Backtest %s -> %s, lag %d day(s), %d spells" % (cal[s0], cal[-1], LAG, len(spells)), file=sys.stderr)
    hdr = "%-11s %6s %6s %5s" % ("Regime", "spells", "days", "time")
    for s in ASSETS:
        hdr += " | %s comp   ann   hit" % s
    print(hdr)
    for name, r in summary.items():
        line = "%-11s %6d %6d %4.0f%%" % (name, r["spells"], r["days"], r["share_of_time"] * 100)
        for s in ASSETS:
            a = r[s]
            line += " | %+8.0f%% %+5.1f%% %3.0f%%" % (a["compounded"] * 100, a["annualized"] * 100, a["hit_rate"] * 100)
        print(line)
    line = "%-11s %6s %6s %5s" % ("Buy & hold", "", "", "")
    for s in ASSETS:
        line += " | %+8.0f%% %+5.1f%%     " % (buyhold[s]["compounded"] * 100, buyhold[s]["annualized"] * 100)
    print(line)


if __name__ == "__main__":
    main()
