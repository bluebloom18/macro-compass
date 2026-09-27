import json
import os
import sys
import time

from fetch import fetch_yahoo, fetch_fred, ratio_series
from align import forward_fill_onto
from model import signal_logic, regime_contribution
from config import SIGNALS, MASTER_CALENDAR_SYMBOL, DEFAULTS

CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")
os.makedirs(CACHE_DIR, exist_ok=True)


def cached_fetch(key, fn):
    path = os.path.join(CACHE_DIR, key + ".json")
    if os.path.exists(path) and (time.time() - os.path.getmtime(path) < 3600 * 12):
        with open(path) as f:
            return json.load(f)
    data = fn()
    with open(path, "w") as f:
        json.dump(data, f)
    return data


def fetch_signal_raw(key, spec):
    if spec["kind"] == "yahoo":
        return cached_fetch("yahoo_" + spec["symbol"].replace("/", "_"), lambda: fetch_yahoo(spec["symbol"]))
    if spec["kind"] == "fred":
        return cached_fetch("fred_" + spec["series"], lambda: fetch_fred(spec["series"]))
    if spec["kind"] == "ratio":
        a = cached_fetch("yahoo_" + spec["symbolA"].replace("/", "_"), lambda: fetch_yahoo(spec["symbolA"]))
        b = cached_fetch("yahoo_" + spec["symbolB"].replace("/", "_"), lambda: fetch_yahoo(spec["symbolB"]))
        return ratio_series(a, b)
    raise ValueError(spec["kind"])


def main():
    print("Fetching master calendar (%s)..." % MASTER_CALENDAR_SYMBOL, file=sys.stderr)
    master_raw = cached_fetch("yahoo_" + MASTER_CALENDAR_SYMBOL.replace("/", "_"),
                               lambda: fetch_yahoo(MASTER_CALENDAR_SYMBOL))
    master_dates = [r["date"] for r in master_raw]
    print("  %d trading days from %s to %s" % (len(master_dates), master_dates[0], master_dates[-1]), file=sys.stderr)

    per_signal = {}
    errors = {}
    for key, spec in SIGNALS.items():
        try:
            print("Fetching %s (%s)..." % (key, spec["label"]), file=sys.stderr)
            raw = fetch_signal_raw(key, spec)
            aligned = forward_fill_onto(raw, master_dates)
            ready, bull, bear = signal_logic(aligned, **DEFAULTS_FOR_SIGNAL_LOGIC())
            per_signal[key] = dict(ready=ready, bull=bull, bear=bear,
                                    first_date=raw[0]["date"], last_date=raw[-1]["date"], n_raw=len(raw))
        except Exception as e:
            errors[key] = str(e)
            print("  ERROR: %s" % e, file=sys.stderr)

    if errors:
        print("\n=== FETCH ERRORS ===", file=sys.stderr)
        for k, v in errors.items():
            print(" ", k, ":", v, file=sys.stderr)

    n = len(master_dates)
    goldilocks_sum = [0.0] * n
    reflation_sum = [0.0] * n
    inflation_sum = [0.0] * n
    deflation_sum = [0.0] * n
    model_available = [False] * n

    for key, spec in SIGNALS.items():
        if key not in per_signal:
            continue
        ps = per_signal[key]
        w = spec["weights"]
        for i in range(n):
            valid = ps["ready"][i]
            if valid:
                model_available[i] = True
            g, r, inf, d = regime_contribution(valid, ps["bull"][i], ps["bear"][i], w)
            goldilocks_sum[i] += g
            reflation_sum[i] += r
            inflation_sum[i] += inf
            deflation_sum[i] += d

    goldilocks_pct = [None] * n
    reflation_pct = [None] * n
    inflation_pct = [None] * n
    deflation_pct = [None] * n
    for i in range(n):
        total = goldilocks_sum[i] + reflation_sum[i] + inflation_sum[i] + deflation_sum[i]
        if model_available[i] and total > 0:
            goldilocks_pct[i] = goldilocks_sum[i] / total * 100
            reflation_pct[i] = reflation_sum[i] / total * 100
            inflation_pct[i] = inflation_sum[i] / total * 100
            deflation_pct[i] = deflation_sum[i] / total * 100

    regime_state = 0  # 0=none,1=goldilocks,2=reflation,3=inflation,4=deflation
    regime_history = [None] * n
    for i in range(n):
        if goldilocks_pct[i] is None:
            regime_history[i] = regime_state
            continue
        vals = dict(g=goldilocks_pct[i], r=reflation_pct[i], inf=inflation_pct[i], d=deflation_pct[i])
        dominant = max(vals.values())
        top = {k for k, v in vals.items() if v == dominant}
        code_of = dict(g=1, r=2, inf=3, d=4)
        current_top = any(code_of[k] == regime_state for k in top)
        if not current_top:
            # Pine's nested ternary picks goldilocks > reflation > inflation > deflation on exact ties
            for k in ("g", "r", "inf", "d"):
                if k in top:
                    regime_state = code_of[k]
                    break
        regime_history[i] = regime_state

    out = dict(
        dates=master_dates,
        goldilocks_pct=goldilocks_pct,
        reflation_pct=reflation_pct,
        inflation_pct=inflation_pct,
        deflation_pct=deflation_pct,
        regime_history=regime_history,
        per_signal_last=[],
        errors=errors,
    )

    for key, spec in SIGNALS.items():
        if key not in per_signal:
            out["per_signal_last"].append(dict(key=key, label=spec["label"], status="fetch_failed",
                                                error=errors.get(key)))
            continue
        ps = per_signal[key]
        i = n - 1
        while i >= 0 and not ps["ready"][i]:
            i -= 1
        out["per_signal_last"].append(dict(
            key=key, label=spec["label"], weights=spec["weights"],
            note=spec.get("note"), source=spec.get("symbol") or spec.get("series") or
            (spec.get("symbolA", "") + "/" + spec.get("symbolB", "")),
            ready=bool(ps["ready"][-1]),
            bull=bool(ps["bull"][i]) if i >= 0 else None,
            bear=bool(ps["bear"][i]) if i >= 0 else None,
            as_of=master_dates[i] if i >= 0 else None,
            data_through=ps["last_date"],
        ))

    with open(os.path.join(os.path.dirname(__file__), "output.json"), "w") as f:
        json.dump(out, f)
    print("\nDone. Wrote output.json (%d bars)." % n, file=sys.stderr)
    print("Latest regime state code:", regime_history[-1], file=sys.stderr)
    print("Latest pct: g=%.1f r=%.1f i=%.1f d=%.1f" % (
        goldilocks_pct[-1] or -1, reflation_pct[-1] or -1, inflation_pct[-1] or -1, deflation_pct[-1] or -1),
        file=sys.stderr)


def DEFAULTS_FOR_SIGNAL_LOGIC():
    d = dict(DEFAULTS)
    d.pop("smooth_input", None)
    return d


if __name__ == "__main__":
    main()
