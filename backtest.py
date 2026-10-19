"""Regime backtest: compounded total return of SPY / QQQ / TLT / GLD / AAXJ over every regime spell.

A spell runs from one regime change to the next. The regime is read off the daily
close, so by default positions switch with a 1-trading-day lag (signal at close t,
held from close t+1) to avoid trading on information not yet available; LAG=0 gives
the same-close variant. Prices are Yahoo dividend/split-adjusted closes (total return).

Also runs simple regime-switching strategies (SWITCH): hold the ETF in its favourable regimes,
3M T-bills otherwise, same execution lag, COST_BP per switch, vs buy & hold.

Requires output.json from build.py. Writes backtest.json.
"""
import bisect
import datetime as dt
import json
import os
import sys
import time
import urllib.parse
import urllib.request

from fetch import UA, fetch_fred

ASSETS = {"SPY": "S&P 500", "QQQ": "Nasdaq-100", "TLT": "20Y+ Treasuries", "GLD": "Gold",
          "AAXJ": "Asia ex-Japan"}
REGIMES = {1: "Goldilocks", 2: "Reflation", 3: "Inflation", 4: "Deflation"}
LAG = int(os.environ.get("LAG", "1"))
COST_BP = 5.0
# (asset, regime codes held, label): equities risk-on in growth-up regimes, TLT in Deflation
SWITCH = [("SPY", (1, 2), "Goldilocks + Reflation"), ("QQQ", (1, 2), "Goldilocks + Reflation"),
          ("AAXJ", (1, 2), "Goldilocks + Reflation"), ("TLT", (4,), "Deflation"),
          ("TLT", (1, 4), "Goldilocks + Deflation")]


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


def switching(dates, regime, prices):
    """Daily: return on day j belongs to the regime read LAG days before j-1 (as in the spells)."""
    tb = {r["date"]: r["close"] for r in fetch_fred("DTB3")}
    tbk = sorted(tb)

    def stats(eq, yrs):
        rets = [eq[i] / eq[i - 1] - 1 for i in range(1, len(eq))]
        mu = sum(rets) / len(rets)
        vol = (sum((x - mu) ** 2 for x in rets) / (len(rets) - 1)) ** 0.5 * 252 ** 0.5
        peak, mdd = 1.0, 0.0
        for v in eq:
            peak = max(peak, v)
            mdd = min(mdd, v / peak - 1)
        cagr = eq[-1] ** (1 / yrs) - 1
        return dict(total=eq[-1] - 1, cagr=cagr, vol=vol, maxdd=mdd)

    rows = []
    first = next(i for i, r in enumerate(regime) if r)
    for sym, held, label in SWITCH:
        px = prices[sym]
        cal = [i for i in range(first, len(dates)) if dates[i] in px]
        eq_s, eq_b, inv, sw, prev = [1.0], [1.0], 0, 0, None
        for k in range(1 + LAG, len(cal)):
            j, jm = cal[k], cal[k - 1]
            on = regime[cal[k - 1 - LAG]] in held
            r_asset = px[dates[j]] / px[dates[jm]] - 1
            rate = tb[tbk[max(0, bisect.bisect_right(tbk, dates[jm]) - 1)]]
            days = (dt.date.fromisoformat(dates[j]) - dt.date.fromisoformat(dates[jm])).days
            g = 1 + (r_asset if on else (1 + rate / 100) ** (days / 365) - 1)
            if prev is not None and on != prev:
                g *= 1 - COST_BP / 1e4
                sw += 1
            prev = on
            inv += on
            eq_s.append(eq_s[-1] * g)
            eq_b.append(eq_b[-1] * (1 + r_asset))
        start, end = dates[cal[LAG]], dates[cal[-1]]
        yrs = (dt.date.fromisoformat(end) - dt.date.fromisoformat(start)).days / 365.25
        rows.append(dict(asset=sym, held=label, start=start, end=end, strategy=stats(eq_s, yrs),
                         buyhold=stats(eq_b, yrs), invested=inv / (len(eq_s) - 1), switches_per_yr=sw / yrs))
    return dict(cost_bp=COST_BP, cash="3M T-bills", rows=rows)


def main():
    d = json.load(open("output.json"))
    dates, regime = d["dates"], d["regime_history"]
    prices = {s: fetch_adjclose(s) for s in ASSETS}

    # common calendar: model dates where every asset that has started trading has a price.
    # An asset with a shorter history (AAXJ: Aug 2008) is None before its first price, and
    # spells it wasn't around for are left out of its stats rather than shortening everyone's
    # (the spell it starts in is counted from its first price).
    first_px = {s: min(prices[s]) for s in ASSETS}
    idx = [i for i, dt in enumerate(dates) if all(dt in prices[s] for s in ASSETS if first_px[s] <= dt)]
    cal = [dates[i] for i in idx]
    reg = [regime[i] for i in idx]
    px = {s: [prices[s].get(dt) for dt in cal] for s in ASSETS}
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
            # an asset that starts trading mid-spell is counted from its first price
            a = next((i for i in range(ea, eb) if px[s][i]), None)
            sp[s] = px[s][eb] / px[s][a] - 1 if a is not None and px[s][eb] else None
        spells.append(sp)

    summary = {}
    for code, name in REGIMES.items():
        ss = [sp for sp in spells if sp["regime"] == code]
        days = sum(sp["days"] for sp in ss)
        row = dict(spells=len(ss), days=days, share_of_time=days / (n - 1 - first - LAG))
        for s in ASSETS:
            sa = [sp for sp in ss if sp[s] is not None]
            days_a = sum(sp["days"] for sp in sa)
            comp = 1.0
            for sp in sa:
                comp *= 1 + sp[s]
            row[s] = dict(compounded=comp - 1 if sa else None,
                          annualized=comp ** (252 / days_a) - 1 if days_a else None,
                          avg_per_spell=sum(sp[s] for sp in sa) / len(sa) if sa else None,
                          hit_rate=sum(1 for sp in sa if sp[s] > 0) / len(sa) if sa else None,
                          spells=len(sa))
        summary[name] = row
    s0 = min(first + LAG, n - 1)
    buyhold, starts = {}, {}
    for s in ASSETS:
        # from the backtest start, or the asset's first trading day if it started later
        a0 = next(i for i in range(s0, n) if px[s][i])
        yrs = (n - 1 - a0) / 252
        buyhold[s] = dict(compounded=px[s][-1] / px[s][a0] - 1,
                          annualized=(px[s][-1] / px[s][a0]) ** (1 / yrs) - 1)
        starts[s] = cal[a0]

    out = dict(lag=LAG, start=cal[s0], end=cal[-1], assets=ASSETS, asset_start=starts, spells=spells,
               summary=summary, buyhold=buyhold)
    def rnd(o):
        if isinstance(o, float):
            return round(o, 6)
        if isinstance(o, dict):
            return {k: rnd(v) for k, v in o.items()}
        if isinstance(o, list):
            return [rnd(v) for v in o]
        return o
    try:
        out["switching"] = switching(dates, regime, prices)
    except Exception as e:                      # T-bill fetch etc. must not sink the main backtest
        print("switching strategies skipped: %s" % e, file=sys.stderr)
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
            line += " | %+8.0f%% %+5.1f%% %3.0f%%" % (a["compounded"] * 100, a["annualized"] * 100, a["hit_rate"] * 100) \
                if a["spells"] else " | %21s" % "n/a"
        print(line)
    line = "%-11s %6s %6s %5s" % ("Buy & hold", "", "", "")
    for s in ASSETS:
        line += " | %+8.0f%% %+5.1f%%     " % (buyhold[s]["compounded"] * 100, buyhold[s]["annualized"] * 100)
    print(line)


if __name__ == "__main__":
    main()
