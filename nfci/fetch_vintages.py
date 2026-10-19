"""Download the weekly ALFRED vintages of NFCI into nfci/cache/vintages/<Thursday>.csv.

Each file is the full NFCI history exactly as published on that Thursday (the Chicago Fed
releases Wednesday morning), which is what nfci_backtest.py's real-time run trades on.
Already-downloaded vintages are skipped, so re-running only fetches new weeks. ~800 files,
a few minutes on first run. Uses curl for the same reason as fetch.fetch_fred.

Usage: python3 nfci/fetch_vintages.py
"""
import datetime as dt
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
VINTAGE_DIR = os.path.join(HERE, "cache", "vintages")
FIRST = dt.date(2011, 5, 26)       # first Thursday with an ALFRED vintage of NFCI
URL = "https://alfred.stlouisfed.org/graph/alfredgraph.csv?id=NFCI&vintage_date=%s"


def fetch(day):
    path = os.path.join(VINTAGE_DIR, day + ".csv")
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return None
    subprocess.run(["curl", "-s", "-m", "40", "--retry", "3", "-o", path, URL % day], check=True)
    with open(path) as f:
        if not f.readline().startswith("observation_date"):
            os.remove(path)
            return "bad response for " + day
    return day


def main():
    os.makedirs(VINTAGE_DIR, exist_ok=True)
    today, d, days = dt.date.today(), FIRST, []
    while d <= today:
        days.append(d.isoformat())
        d += dt.timedelta(days=7)
    with ThreadPoolExecutor(6) as ex:
        res = list(ex.map(fetch, days))
    errors = [r for r in res if r and r.startswith("bad")]
    print("%d vintages, %d new, %d errors" % (len(days), sum(1 for r in res if r and not r.startswith("bad")), len(errors)))
    for e in errors:
        print("  " + e)


if __name__ == "__main__":
    main()
