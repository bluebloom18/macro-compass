"""Risk Gauge: coincident market-stress score from volatility, credit and the equity trend.

Built to measure risk as it happens, not to replicate a vendor table: only fast market inputs
(no monthly surveys or weekly macro prints), in three equal-weight blocks:

  Volatility    VIX level (log), VIX / VIX3M term structure, MOVE bond volatility (log)
  Credit        HYG / IEF and LQD / IEF total-return ratios, distance below their 52-week high
  Equity trend  S&P 500 distance below its 52-week high, S&P 500 vs its 50-day average

Each input is sampled weekly (Friday close, plus the latest day as the current reading) and scored
against its whole history since 2000 (expanding window, so a calm spell doesn't make a small wobble
look like a crisis) with a robust z-score, (x - median) / (1.4826 * MAD), because drawdowns and VIX
are one-sided and fat-tailed. Scores are signed so positive = risk-on, divided by 3 and capped at
+/-1; a block averages its inputs and the gauge averages the blocks.

Imported by risk_barometer.py, which embeds the result in risk_doc.json under "gauge" (db: regime/risk).
Run it directly for the validation printout.
"""
import bisect
import datetime as dt
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request

from fetch import UA

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache", "risk_gauge_raw.json")

SYMBOLS = ["^GSPC", "^VIX", "^VIX3M", "^MOVE", "HYG", "LQD", "IEF"]

# block -> inputs: (key, sign, label, source). sign = +1 if a rising value is risk-on.
BLOCKS = [
    ("Volatility",   [("vix", -1, "VIX level", "^VIX"),
                      ("vix_ts", -1, "VIX / VIX3M term structure", "^VIX/^VIX3M"),
                      ("move", -1, "Bond volatility (MOVE)", "^MOVE")]),
    ("Credit",       [("hy_dd", +1, "High yield vs Treasuries, below 52w high", "HYG/IEF"),
                      ("ig_dd", +1, "Investment grade vs Treasuries, below 52w high", "LQD/IEF")]),
    ("Equity trend", [("spx_dd", +1, "S&P 500 below 52w high", "^GSPC"),
                      ("spx_ma", +1, "S&P 500 vs 50-day average", "^GSPC")]),
]

SCALE = 3.0      # robust z of 3 = full score
MIN_OBS = 52     # weeks of history before an input is scored
# On > +0.15, Neutral, Off < -0.15, Stress < -0.40. Chosen on 2008-26 so that calm markets read
# Neutral/On and every >=10% drawdown since 2008 reaches Off (see validate()).
TIERS = dict(on=0.15, off=-0.15, stress=-0.40)


# ---------- data ----------

def fetch_daily(symbol, period1=946684800):
    """Dividend/split-adjusted daily closes since 2000 as [[date, value], ...]. Explicit
    period1/period2: range=max silently downsamples to coarser-than-daily bars."""
    url = "https://query1.finance.yahoo.com/v8/finance/chart/%s?period1=%d&period2=%d&interval=1d" % (
        urllib.parse.quote(symbol, safe=""), period1, int(time.time()))
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as resp:
                r = json.loads(resp.read())["chart"]["result"][0]
            vals = r["indicators"].get("adjclose", [{}])[0].get("adjclose") or r["indicators"]["quote"][0]["close"]
            out = {}
            for t, v in zip(r["timestamp"], vals):
                if v is not None:
                    out[time.strftime("%Y-%m-%d", time.gmtime(t))] = v
            return sorted([d, v] for d, v in out.items())
        except Exception as e:  # noqa: BLE001 - retry any network/parse error
            last = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError("fetch %s failed: %s" % (symbol, last))


def load_raw(refresh=False):
    if not refresh and os.path.exists(CACHE):
        return json.load(open(CACHE))
    raw = {}
    for sym in SYMBOLS:
        raw[sym] = fetch_daily(sym)
        print("  %-7s %5d rows  %s .. %s" % (sym, len(raw[sym]), raw[sym][0][0], raw[sym][-1][0]), file=sys.stderr)
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    json.dump(raw, open(CACHE, "w"))
    return raw


def _ratio(a, b):
    db = dict(b)
    return [(d, v / db[d]) for d, v in a if d in db and db[d]]


def _below_high(rows, n=252):
    """Log distance below the trailing n-day high, daily."""
    out, vals = [], [v for _, v in rows]
    for i, (d, v) in enumerate(rows):
        out.append((d, math.log(v / max(vals[max(0, i - n + 1):i + 1]))))
    return out


def daily_inputs(raw):
    spx, vix, vix3m = raw["^GSPC"], raw["^VIX"], dict(raw["^VIX3M"])
    sv = [v for _, v in spx]
    return {
        "vix":    [(d, math.log(v)) for d, v in vix],
        "vix_ts": [(d, v / vix3m[d]) for d, v in vix if vix3m.get(d)],
        "move":   [(d, math.log(v)) for d, v in raw["^MOVE"]],
        "hy_dd":  _below_high(_ratio(raw["HYG"], raw["IEF"])),
        "ig_dd":  _below_high(_ratio(raw["LQD"], raw["IEF"])),
        "spx_dd": _below_high(spx),
        "spx_ma": [(spx[i][0], math.log(sv[i] / (sum(sv[i - 49:i + 1]) / 50))) for i in range(49, len(spx))],
    }


def week_ends(first, last):
    """Fridays from the first Friday on/after `first` to `last`, plus `last` if it isn't a Friday."""
    d = dt.date.fromisoformat(first)
    d += dt.timedelta((4 - d.weekday()) % 7)
    out = []
    while d.isoformat() <= last:
        out.append(d.isoformat())
        d += dt.timedelta(7)
    if out[-1] != last:
        out.append(last)
    return out


def sample(rows, dates):
    """Last value on or before each date (None before the series starts)."""
    out, j, cur = [], 0, None
    for d in dates:
        while j < len(rows) and rows[j][0] <= d:
            cur = rows[j][1]
            j += 1
        out.append(cur)
    return out


# ---------- scoring ----------

def robust_z(x):
    """Expanding-window (x - median) / (1.4826 * MAD), using only data up to each point."""
    out, seen = [None] * len(x), []
    for i, v in enumerate(x):
        if v is None:
            continue
        bisect.insort(seen, v)
        n = len(seen)
        if n < MIN_OBS:
            continue
        med = seen[n // 2]
        mad = sorted(abs(s - med) for s in seen)[n // 2] * 1.4826
        out[i] = (v - med) / mad if mad else 0.0
    return out


def tier(v):
    if v is None:
        return None
    return "stress" if v < TIERS["stress"] else "off" if v < TIERS["off"] else "on" if v > TIERS["on"] else "neutral"


def compute(raw):
    """Weekly panel -> (dates, raw weekly inputs, input scores, block scores, gauge)."""
    daily = daily_inputs(raw)
    last = raw["^GSPC"][-1][0]
    dates = week_ends("2000-01-07", last)
    wk = {k: sample(rows, dates) for k, rows in daily.items()}
    scores, blocks = {}, {}
    for name, ins in BLOCKS:
        cols = []
        for key, sign, _, _ in ins:
            scores[key] = [None if z is None else max(-1.0, min(1.0, sign * z / SCALE)) for z in robust_z(wk[key])]
            cols.append(scores[key])
        blocks[name] = [None if None in v else sum(v) / len(v) for v in zip(*cols)]
    gauge = [None if None in v else sum(v) / len(v) for v in zip(*blocks.values())]
    return dates, wk, scores, blocks, gauge


# ---------- validation (coincidence with realised stress) ----------

def _spearman(a, b):
    p = [(x, y) for x, y in zip(a, b) if x is not None and y is not None]

    def ranks(v):
        order = sorted(range(len(v)), key=v.__getitem__)
        r = [0] * len(v)
        for k, i in enumerate(order):
            r[i] = k
        return r
    xs, ys = ranks([x for x, _ in p]), ranks([y for _, y in p])
    m = (len(p) - 1) / 2
    num = sum((x - m) * (y - m) for x, y in zip(xs, ys))
    return num / math.sqrt(sum((x - m) ** 2 for x in xs) * sum((y - m) ** 2 for y in ys))


def realised(raw, dates):
    """S&P 500 20-day realised vol (centred on each Friday) and distance below its 52-week high."""
    spx = raw["^GSPC"]
    lr = [(spx[i][0], math.log(spx[i][1] / spx[i - 1][1])) for i in range(1, len(spx))]
    rv = [(lr[i][0], math.sqrt(sum(x * x for _, x in lr[i - 19:i + 1]) / 20 * 252)) for i in range(19, len(lr))]
    rvw = sample(rv, dates)
    rvc = rvw[2:] + [None, None]          # the 20-day window ending 2 weeks later spans t-2w..t+2w
    dd = [None if v is None else math.exp(v) - 1 for v in sample(_below_high(spx), dates)]
    return rvc, dd, sample(spx, dates)


def validate(raw, dates, gauge):
    """Correlation with realised stress, stress/calm hit rates, drawdown episodes, forward returns by tier."""
    rvc, dd, spx = realised(raw, dates)
    first = next(i for i, v in enumerate(gauge) if v is not None)
    rng = range(first, len(dates))
    stress = [i for i in rng if rvc[i] is not None and (rvc[i] > 0.25 or dd[i] < -0.15)]
    calm = [i for i in rng if rvc[i] is not None and rvc[i] < 0.12 and dd[i] > -0.05]

    def share(ii, test):
        return round(sum(test(gauge[i]) for i in ii) / len(ii), 3) if ii else None

    # >=10% drawdowns on weekly closes: when did the gauge first reach Off / Stress, and its low.
    # An episode ends once the S&P has won back half its fall, so 2010 and 2011 count separately
    # from 2008 even though the 2007 high wasn't regained until 2013.
    episodes, i, peak = [], first, first
    while i < len(dates):
        if spx[i] >= spx[peak]:
            peak = i
        if spx[i] / spx[peak] - 1 <= -0.10:
            j, tr = i, i
            while j < len(dates) and spx[j] < (spx[peak] + spx[tr]) / 2:
                tr = j if spx[j] < spx[tr] else tr
                j += 1
            span = range(peak, min(j, len(dates)))
            first_off = next((k for k in span if gauge[k] < TIERS["off"]), None)
            first_stress = next((k for k in span if gauge[k] < TIERS["stress"]), None)
            low = min(span, key=lambda k: gauge[k])
            episodes.append({
                "peak": dates[peak], "trough": dates[tr], "dd": round(spx[tr] / spx[peak] - 1, 3),
                "first_off": dates[first_off] if first_off is not None else None,
                "off_dd": round(spx[first_off] / spx[peak] - 1, 3) if first_off is not None else None,
                "first_stress": dates[first_stress] if first_stress is not None else None,
                "low": round(gauge[low], 2), "low_date": dates[low],
                "ongoing": j >= len(dates),
            })
            i, peak = j, j
            continue
        i += 1

    fwd = []
    for name in ("stress", "off", "neutral", "on"):
        row = {"bucket": name}
        for h in (4, 13):
            r = [spx[k + h] / spx[k] - 1 for k in rng if tier(gauge[k]) == name and k + h < len(dates)]
            row["n%d" % h] = len(r)
            row["avg%d" % h] = round(sum(r) / len(r), 4) if r else None
            row["hit%d" % h] = round(sum(x > 0 for x in r) / len(r), 3) if r else None
        fwd.append(row)

    return {
        "from": dates[first],
        "corr_rv": round(-_spearman(gauge[first:], rvc[first:]), 2),
        "corr_dd": round(_spearman(gauge[first:], dd[first:]), 2),
        "stress_weeks": len(stress), "stress_off": share(stress, lambda v: v < TIERS["off"]),
        "stress_on": share(stress, lambda v: v > TIERS["on"]),
        "calm_weeks": len(calm), "calm_off": share(calm, lambda v: v < TIERS["off"]),
        "calm_on": share(calm, lambda v: v > TIERS["on"]),
        "episodes": episodes,
        "fwd": fwd,
    }


def gauge_doc(raw):
    """The "gauge" block of risk_doc.json."""
    dates, wk, scores, blocks, gauge = compute(raw)
    first = next(i for i, v in enumerate(gauge) if v is not None)

    def r2(col):
        return [None if v is None else round(v, 2) for v in col[first:]]

    last5 = slice(len(dates) - 5, len(dates))
    return {
        "asof": dates[-1],
        "tiers": TIERS,
        "dates": dates[first:],
        "index": r2(gauge),
        "blocks": [{
            "name": name,
            "scores": r2(blocks[name]),
            "inputs": [{"label": label, "source": src,
                        "last5": [None if v is None else round(v, 2) for v in scores[key][last5]]}
                       for key, _, label, src in ins],
        } for name, ins in BLOCKS],
        "validation": validate(raw, dates, gauge),
    }


def main():
    raw = load_raw(refresh="--refresh" in sys.argv)
    doc = gauge_doc(raw)
    v = doc["validation"]
    print("Risk Gauge as of %s: %+.2f (%s)" % (doc["asof"], doc["index"][-1], tier(doc["index"][-1])))
    for b in doc["blocks"]:
        print("  %-13s %+.2f" % (b["name"], b["scores"][-1]))
    print("\nValidation %s..%s" % (v["from"], doc["asof"]))
    print("  Spearman vs S&P realised vol %.2f, vs drawdown %.2f" % (v["corr_rv"], v["corr_dd"]))
    print("  stress weeks (RV>25%% or DD<-15%%): %d, Off/Stress %.0f%%, On %.0f%%" % (
        v["stress_weeks"], 100 * v["stress_off"], 100 * v["stress_on"]))
    print("  calm weeks (RV<12%% & DD>-5%%):     %d, Off/Stress %.0f%%, On %.0f%%" % (
        v["calm_weeks"], 100 * v["calm_off"], 100 * v["calm_on"]))
    print("\n  %-11s %-11s %5s  %-11s %6s  %-11s %6s" % ("peak", "trough", "dd", "first off", "dd@off", "first stress", "low"))
    for e in v["episodes"]:
        print("  %-11s %-11s %4.0f%%  %-11s %5s  %-12s %+.2f" % (
            e["peak"], e["trough"], 100 * e["dd"], e["first_off"] or "never",
            "%.0f%%" % (100 * e["off_dd"]) if e["off_dd"] is not None else "-", e["first_stress"] or "never", e["low"]))
    print("\n  S&P after each reading:")
    for r in v["fwd"]:
        print("  %-8s n=%4d  4w %+.1f%% (up %.0f%%)  13w %+.1f%% (up %.0f%%)" % (
            r["bucket"], r["n4"], 100 * (r["avg4"] or 0), 100 * (r["hit4"] or 0), 100 * (r["avg13"] or 0), 100 * (r["hit13"] or 0)))


if __name__ == "__main__":
    main()
