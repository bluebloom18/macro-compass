"""Variants of the 1-week NFCI rate-of-change rule, on as-published (ALFRED) data only.

Tests whether the weak real-time result for "risk on when NFCI fell week on week" is down
to execution day (Wednesday release vs Thursday), rounding noise (dead-band), whipsaw
(2-week confirmation), or whether the opposite (contrarian) reading is the one that works.
"""
import csv
import glob
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from fetch import fetch_fred  # noqa: E402
from backtest import fetch_adjclose  # noqa: E402
from nfci_backtest import daily_returns, run, window  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def load(dirname):
    out = []
    for p in sorted(glob.glob(os.path.join(HERE, "cache", dirname, "*.csv"))):
        with open(p) as f:
            vals = [float(r[1]) for r in csv.reader(f) if r and r[0][:1].isdigit() and r[1] not in (".", "")]
        out.append((os.path.basename(p)[:-4], vals))
    return out


def trend(band):
    """On after an easing week; off after a tightening week; inside +-band keep prior position."""
    def f(h, prev):
        ch = h[-1] - h[-2]
        if ch < -band:
            return 1
        if ch > band:
            return 0
        return prev
    return f


def contrarian(band):
    t = trend(band)
    return lambda h, prev: 1 - t(h, 1 - prev)


def confirm2(h, prev):
    a, b = h[-1] - h[-2], h[-2] - h[-3]
    if a < 0 and b < 0:
        return 1
    if a > 0 and b > 0:
        return 0
    return prev


VARIANTS = {
    "Trend, any change (as before)": lambda h, prev: int(h[-1] - h[-2] <= 0),
    "Trend, dead-band 0.01": trend(0.01),
    "Trend, dead-band 0.02": trend(0.02),
    "Trend, dead-band 0.05": trend(0.05),
    "Trend, 2-week confirm": confirm2,
    "Contrarian (on after tightening)": lambda h, prev: int(h[-1] - h[-2] > 0),
    "Contrarian, dead-band 0.02": contrarian(0.02),
}


def main():
    tbill = {r["date"]: r["close"] for r in fetch_fred("DTB3")}
    spy = fetch_adjclose("SPY", period1=725846400)
    days, spy_ret, cash_ret = daily_returns(spy, tbill)
    sets = {"Wed": load("vintages_wed"), "Thu": load("vintages")}
    start = max(v[0][0] for v in sets.values())

    print("As-published NFCI, %s -> %s, 5bp/switch" % (start, days[-1]))
    bh = window(days, spy_ret, cash_ret, [1] * len(days), start)
    print("%-34s %4s | %6s %6s %7s %5s %5s" % ("Rule", "Day", "CAGR", "Sharpe", "MaxDD", "Inv", "Sw/y"))
    print("%-34s %4s | %5.1f%% %6.2f %6.1f%% %4.0f%% %5.1f" % (
        "Buy & hold SPY", "", bh["cagr"] * 100, bh["sharpe"], bh["maxdd"] * 100, 100, 0))
    for name, rule in VARIANTS.items():
        for day_label, vintages in sets.items():
            by_v, prev = {}, 1
            for vdate, vals in vintages:
                prev = rule(vals, prev)
                by_v[vdate] = prev
            vd = [v for v, _ in vintages]
            sig, j = [], -1
            for day in days:
                while j + 1 < len(vd) and vd[j + 1] <= day:
                    j += 1
                sig.append(by_v[vd[j]] if j >= 0 else 1)
            r = window(days, spy_ret, cash_ret, sig, start)
            print("%-34s %4s | %5.1f%% %6.2f %6.1f%% %4.0f%% %5.1f" % (
                name, day_label, r["cagr"] * 100, r["sharpe"], r["maxdd"] * 100, r["invested"] * 100, r["switches_per_yr"]))


if __name__ == "__main__":
    main()
