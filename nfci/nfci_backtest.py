"""NFCI risk-on/off backtest: hold SPY when financial conditions are easing, T-bills when tightening.

NFCI (Chicago Fed National Financial Conditions Index, FRED: NFCI) is weekly, dated the
Friday the week ends, published the following Wednesday before the open. Positive = tighter
than average. A position set at close t earns the return from t to t+1.

Two information sets:
  * revised  - today's FRED history. Re-estimated every week, so each past value has been
               revised using later data; optimistic. A reading drives positions from the
               close of its release Wednesday (Friday + 5 days).
  * realtime - ALFRED vintages (May 2011 onward, cached by fetch_vintages.sh): at each
               Thursday close the rules see only the vintage published by then, and hold that
               position until the next Thursday. The same Thursday schedule is also run on
               the revised history ("revised-matched"), so the gap between the two is purely
               the effect of revisions.

Risk-on leg: SPY dividend-adjusted (total return). Risk-off leg: 3M T-bill (FRED DTB3),
accrued daily. Switching costs COST_BP per switch. Writes nfci_results.json.
"""
import csv
import datetime as dt
import glob
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from fetch import fetch_fred  # noqa: E402
from backtest import fetch_adjclose  # noqa: E402

COST_BP = float(os.environ.get("COST_BP", "5"))
RELEASE_LAG_DAYS = 5          # Friday as-of -> Wednesday release
HERE = os.path.dirname(os.path.abspath(__file__))
VINTAGE_DIR = os.path.join(HERE, "cache", "vintages")


def d(s):
    return dt.date.fromisoformat(s)


# ---------- signal rules: weekly NFCI history (oldest..latest) -> True on / False off / None warm-up
def rule_level(th):
    return lambda h: h[-1] < th


def rule_ma(n):
    def f(h):
        if len(h) < n:
            return None
        return h[-1] <= sum(h[-n:]) / n          # at/below its own MA = conditions easing
    return f


def rule_roc(n):
    def f(h):
        if len(h) <= n:
            return None
        return h[-1] - h[-1 - n] <= 0            # NFCI flat/falling over n weeks = easing
    return f


def rule_roc_band(band):
    """Position set by the most recent weekly move larger than +-band (easing -> on,
    tightening -> off); smaller moves keep it. Starts risk-on."""
    def f(h):
        for k in range(len(h) - 1, 0, -1):
            ch = h[k] - h[k - 1]
            if ch < -band:
                return True
            if ch > band:
                return False
        return True
    return f


RULES = {"Level < 0": rule_level(0.0), "Level < -0.25": rule_level(-0.25)}
for n in (4, 8, 13, 26, 52):
    RULES["MA %dw" % n] = rule_ma(n)
for n in (1, 4, 8, 13, 26):
    RULES["ROC %dw" % n] = rule_roc(n)
BAND = 0.02
RULES["ROC 1w band 0.02"] = rule_roc_band(BAND)


def load_vintages():
    out = []
    for path in sorted(glob.glob(os.path.join(VINTAGE_DIR, "*.csv"))):
        with open(path) as f:
            rows = [(r[0], float(r[1])) for r in csv.reader(f) if r and r[0][:1].isdigit() and r[1] not in (".", "")]
        if rows:
            out.append((os.path.basename(path)[:-4], rows))
    return out


def daily_returns(spy, tbill):
    days = sorted(spy)
    tb_sorted = sorted(tbill)
    cash_ret, spy_ret, j, rate = [0.0], [0.0], 0, tbill[tb_sorted[0]]
    for i, day in enumerate(days):
        while j < len(tb_sorted) and tb_sorted[j] <= day:
            rate = tbill[tb_sorted[j]]
            j += 1
        if i == 0:
            continue
        gap = (d(day) - d(days[i - 1])).days
        cash_ret.append((1 + rate / 100) ** (gap / 365) - 1)
        spy_ret.append(spy[day] / spy[days[i - 1]] - 1)
    return days, spy_ret, cash_ret


def run(days, spy_ret, cash_ret, sig):
    """sig[i] = position (1 SPY / 0 cash) held over (days[i], days[i+1]]. Returns stats."""
    eq, curve, switches = 1.0, [1.0], 0
    for i in range(len(days) - 1):
        if i and sig[i] != sig[i - 1]:
            eq *= 1 - COST_BP / 1e4
            switches += 1
        eq *= 1 + (spy_ret[i + 1] if sig[i] else cash_ret[i + 1])
        curve.append(eq)
    rets = [curve[k] / curve[k - 1] - 1 for k in range(1, len(curve))]
    ex = [rets[k] - cash_ret[k + 1] for k in range(len(rets))]
    yrs = (d(days[-1]) - d(days[0])).days / 365.25
    n = len(rets)
    mu, mex = sum(rets) / n, sum(ex) / n
    vol = (sum((x - mu) ** 2 for x in rets) / (n - 1)) ** 0.5 * 252 ** 0.5
    sdex = (sum((x - mex) ** 2 for x in ex) / (n - 1)) ** 0.5
    peak, mdd = 1.0, 0.0
    for v in curve:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    cagr = curve[-1] ** (1 / yrs) - 1
    return dict(cagr=cagr, vol=vol, sharpe=mex / sdex * 252 ** 0.5 if sdex else 0.0, maxdd=mdd,
                invested=sum(sig[:-1]) / (len(sig) - 1), switches_per_yr=switches / yrs,
                curve=curve)


def window(days, spy_ret, cash_ret, sig, start, end=None):
    i0 = next(i for i, x in enumerate(days) if x >= start)
    i1 = len(days) if end is None else next(i for i, x in enumerate(days) if x >= end) + 1
    return run(days[i0:i1], spy_ret[i0:i1], cash_ret[i0:i1], sig[i0:i1])


def main():
    nfci = [(r["date"], r["close"]) for r in fetch_fred("NFCI")]
    tbill = {r["date"]: r["close"] for r in fetch_fred("DTB3")}
    spy = fetch_adjclose("SPY", period1=725846400)          # 1993-01-01
    days, spy_ret, cash_ret = daily_returns(spy, tbill)
    vintages = load_vintages()
    values = [v for _, v in nfci]
    asofs = [a for a, _ in nfci]

    # --- revised history, Wednesday-release timing
    sig_rev = {}
    k = 0
    released_count = []
    for day in days:
        while k < len(nfci) and (d(asofs[k]) + dt.timedelta(days=RELEASE_LAG_DAYS)).isoformat() <= day:
            k += 1
        released_count.append(k)
    for name, rule in RULES.items():
        sig_rev[name] = [rule(values[:c]) if c else None for c in released_count]

    # --- Thursday schedule: real-time vintages vs revised history cut at the same last week
    vdates = [v for v, _ in vintages]
    rt_by_v, rm_by_v = {}, {}
    band_prev = True
    for vdate, rows in vintages:
        hist_rt = [v for _, v in rows]
        last_asof = rows[-1][0]
        cut = sum(1 for a in asofs if a <= last_asof)
        hist_rm = values[:cut]
        rt_by_v[vdate] = {n: r(hist_rt) for n, r in RULES.items()}
        rm_by_v[vdate] = {n: r(hist_rm) for n, r in RULES.items()}
        # the band rule is path-dependent: in real time you act only on the move published
        # this week and otherwise keep your position; re-deriving it from each vintage's full
        # (revised) history would let revisions to old weeks flip it with no new move
        ch = hist_rt[-1] - hist_rt[-2]
        band_prev = True if ch < -BAND else False if ch > BAND else band_prev
        rt_by_v[vdate]["ROC 1w band 0.02"] = band_prev
    sig_rt, sig_rm = {n: [] for n in RULES}, {n: [] for n in RULES}
    j = -1
    for day in days:
        while j + 1 < len(vdates) and vdates[j + 1] <= day:
            j += 1
        for n in RULES:
            sig_rt[n].append(rt_by_v[vdates[j]][n] if j >= 0 else None)
            sig_rm[n].append(rm_by_v[vdates[j]][n] if j >= 0 else None)

    def as_int(sig):
        return [None if s is None else int(s) for s in sig]

    rev_start = days[max(next(i for i, s in enumerate(sig) if s is not None) for sig in sig_rev.values())]
    rt_start = vdates[0]
    hold, cash = [1] * len(days), [0] * len(days)

    out = dict(cost_bp=COST_BP, revised={}, realtime={}, revised_matched={},
               periods=dict(revised=[rev_start, days[-1]], realtime=[rt_start, days[-1]]))
    for label, sig in [("Buy & hold SPY", hold), ("Cash (T-bills)", cash)]:
        out["revised"][label] = window(days, spy_ret, cash_ret, sig, rev_start)
        out["realtime"][label] = window(days, spy_ret, cash_ret, sig, rt_start)
        out["revised_matched"][label] = dict(out["realtime"][label])   # copy: curves are thinned per block
    for n in RULES:
        out["revised"][n] = window(days, spy_ret, cash_ret, as_int(sig_rev[n]), rev_start)
        out["realtime"][n] = window(days, spy_ret, cash_ret, as_int(sig_rt[n]), rt_start)
        out["revised_matched"][n] = window(days, spy_ret, cash_ret, as_int(sig_rm[n]), rt_start)
    out["latest"] = dict(asof=asofs[-1], nfci=values[-1], vintage=vdates[-1],
                         realtime_signals={n: bool(rt_by_v[vdates[-1]][n]) for n in RULES})

    # equity curves thinned to every 5th trading day; each block has its own date axis
    out["curve_dates"] = {}
    for block, start in (("revised", rev_start), ("realtime", rt_start), ("revised_matched", rt_start)):
        i0 = next(i for i, x in enumerate(days) if x >= start)
        step = int(os.environ.get("CURVE_STEP", "5"))
        out["curve_dates"][block] = days[i0:][::step]
        for n, r in out[block].items():
            r["curve"] = [round(v, 5) for v in r["curve"][::step]]
    json.dump(out, open(os.path.join(HERE, "nfci_results.json"), "w"))

    def table(block, title):
        a, b = out["periods"]["revised" if block == "revised" else "realtime"]
        print("\n%s  (%s -> %s, %gbp/switch)" % (title, a, b, COST_BP))
        print("%-16s %6s %6s %6s %7s %5s %5s" % ("Strategy", "CAGR", "Vol", "Sharpe", "MaxDD", "Inv", "Sw/y"))
        for n, r in out[block].items():
            print("%-16s %5.1f%% %5.1f%% %6.2f %6.1f%% %4.0f%% %5.1f" % (
                n, r["cagr"] * 100, r["vol"] * 100, r["sharpe"], r["maxdd"] * 100, r["invested"] * 100, r["switches_per_yr"]))

    table("revised", "A. REVISED history, Wednesday release timing")
    print("\nB vs C: same window, same Thursday schedule - only difference is revised vs as-published data")
    print("%-16s | %6s %6s %7s | %6s %6s %7s" % ("Strategy", "C CAGR", "Sharpe", "MaxDD", "B CAGR", "Sharpe", "MaxDD"))
    for n in out["realtime"]:
        c, b = out["revised_matched"][n], out["realtime"][n]
        print("%-16s | %5.1f%% %6.2f %6.1f%% | %5.1f%% %6.2f %6.1f%%" % (
            n, c["cagr"] * 100, c["sharpe"], c["maxdd"] * 100, b["cagr"] * 100, b["sharpe"], b["maxdd"] * 100))
    print("\nLatest NFCI %.3f (week ending %s, vintage %s); real-time signals:" % (values[-1], asofs[-1], vdates[-1]),
          {n: ("ON" if v else "off") for n, v in out["latest"]["realtime_signals"].items()})


if __name__ == "__main__":
    main()
