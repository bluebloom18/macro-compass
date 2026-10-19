"""Write nfci_doc.json for the dashboard's Financial conditions section (artifact db: regime/nfci).

Contents:
  * dates / v   - full weekly NFCI history from FRED (current, revised vintage) for the level
                  and 52-week MA charts.
  * fp          - first-print weekly changes: for each week since May 2011, the week-on-week
                  change as it was first published. The weekly-change rules trade on these,
                  and they differ a lot from changes in today's smoothed history.
                  Persisted in first_prints.csv (committed). Merged, in order, from that file,
                  the ALFRED vintage cache (cache/vintages) when present, the previous live doc
                  (--prev, e.g. regime/nfci saved by read_db), and finally the newest week's
                  change from today's release. The merged series is written back to the CSV.
  * backtest    - as-published backtest summary (backtest_summary.json) and equity curves
                  (backtest_curves.json, growth of 100, weekly), both regenerated from
                  nfci_results.json after a full local run (fetch_vintages.py, then
                  nfci_backtest.py) and committed, so the daily refresh reuses them.

Usage: python3 nfci/nfci_doc.py [--prev path/to/previous_nfci_doc.json]
"""
import csv
import glob
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from fetch import fetch_fred  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
VINTAGE_DIR = os.path.join(HERE, "cache", "vintages")
FP_CSV = os.path.join(HERE, "first_prints.csv")
BT_ROWS = {"Buy & hold SPY": "Buy & hold SPY", "MA 52w": "52-week MA", "ROC 1w": "1-week change",
           "ROC 1w band 0.02": "1-week change, moves > 0.02"}


def first_prints_from_cache():
    seen = {}
    for path in sorted(glob.glob(os.path.join(VINTAGE_DIR, "*.csv"))):
        with open(path) as f:
            rows = [(r[0], float(r[1])) for r in csv.reader(f) if r and r[0][:1].isdigit() and r[1] not in (".", "")]
        if len(rows) >= 2 and rows[-1][0] not in seen:
            seen[rows[-1][0]] = round(rows[-1][1] - rows[-2][1], 3)
    return seen


def main():
    prev_path = sys.argv[sys.argv.index("--prev") + 1] if "--prev" in sys.argv else None
    rows = fetch_fred("NFCI")
    dates, vals = [r["date"] for r in rows], [round(r["close"], 3) for r in rows]

    fp, sources = {}, []
    if os.path.exists(FP_CSV):
        with open(FP_CSV) as f:
            fp.update({r["week"]: float(r["first_print_change"]) for r in csv.DictReader(f)})
        sources.append("csv")
    cached = first_prints_from_cache()
    if cached:
        fp.update(cached)
        sources.append("vintage cache")
    if prev_path and os.path.exists(prev_path):
        prev = json.load(open(prev_path))
        prev = prev.get("data", prev)            # read_db output may wrap fields
        if "fp" in prev:
            for x, c in zip(prev["fp"]["dates"], prev["fp"]["ch"]):
                fp.setdefault(x, c)
            sources.append("previous doc")
    if not fp:
        sys.exit("No first-print history (first_prints.csv, cache/vintages or --prev). Refusing to write a "
                 "doc that would silently fall back to revised weekly changes.")
    # newest week: today's release is its first print. Any week missed between the stored series
    # and today (e.g. refresh outage) cannot be recovered here and falls back to revised changes.
    if dates[-1] not in fp:
        fp[dates[-1]] = round(vals[-1] - vals[-2], 3)
    source = " + ".join(sources)
    fp_dates = sorted(fp)
    with open(FP_CSV, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["week", "first_print_change"])
        w.writerows((x, fp[x]) for x in fp_dates)

    doc = dict(asof=dates[-1], dates=dates, v=vals,
               fp=dict(dates=fp_dates, ch=[fp[x] for x in fp_dates]))
    # backtest summary: rebuilt from nfci_results.json when present (needs the vintage cache, so
    # only on a full local run), else the committed backtest_summary.json
    res_path, summ_path = os.path.join(HERE, "nfci_results.json"), os.path.join(HERE, "backtest_summary.json")
    curves_path = os.path.join(HERE, "backtest_curves.json")
    if os.path.exists(res_path):
        res = json.load(open(res_path))
        a, b = res["periods"]["realtime"]
        summary = dict(start=a, end=b, cost_bp=res["cost_bp"], rows=[
            dict(rule=label, **{k: round(res["realtime"][key][k], 4) for k in ("cagr", "sharpe", "maxdd", "invested")})
            for key, label in BT_ROWS.items()])
        json.dump(summary, open(summ_path, "w"), indent=1)
        # equity curves for the same rules, growth of 100, sampled weekly
        cd = res["curve_dates"]["realtime"]
        curves = dict(dates=cd, series=[
            dict(rule=label, v=[round(100 * x, 1) for x in res["realtime"][key]["curve"]])
            for key, label in BT_ROWS.items()])
        json.dump(curves, open(curves_path, "w"), separators=(",", ":"))
    if os.path.exists(summ_path):
        doc["backtest"] = json.load(open(summ_path))
        if os.path.exists(curves_path):
            doc["backtest"]["curves"] = json.load(open(curves_path))
    json.dump(doc, open(os.path.join(HERE, "nfci_doc.json"), "w"), separators=(",", ":"))
    print("Wrote nfci_doc.json: %d weeks through %s (latest %.3f); %d first-print changes from %s, through %s" % (
        len(dates), dates[-1], vals[-1], len(fp_dates), source, fp_dates[-1]))


if __name__ == "__main__":
    main()
