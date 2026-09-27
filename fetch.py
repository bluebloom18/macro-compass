"""Open-data fetchers for the Global Macro Regime port: Yahoo Finance chart API + FRED CSV."""
import json
import subprocess
import time
import urllib.request
import urllib.parse

UA = "Mozilla/5.0 (compatible; regime-model/1.0)"


def fetch_yahoo(symbol, range_="20y", interval="1d", retries=3):
    """Returns list of dicts: [{date, open, high, low, close}, ...] sorted ascending by date."""
    url = "https://query1.finance.yahoo.com/v8/finance/chart/%s?range=%s&interval=%s" % (
        urllib.parse.quote(symbol, safe=""), range_, interval)
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read())
            result = data.get("chart", {}).get("result")
            if not result:
                raise ValueError("no result for %s: %s" % (symbol, data.get("chart", {}).get("error")))
            r = result[0]
            ts = r.get("timestamp") or []
            quote = r["indicators"]["quote"][0]
            out = []
            for i, t in enumerate(ts):
                o, h, l, c = quote["open"][i], quote["high"][i], quote["low"][i], quote["close"][i]
                if c is None:
                    continue
                date = time.strftime("%Y-%m-%d", time.gmtime(t))
                out.append({
                    "date": date,
                    "open": o if o is not None else c,
                    "high": h if h is not None else c,
                    "low": l if l is not None else c,
                    "close": c,
                })
            out.sort(key=lambda x: x["date"])
            # de-dup same-day entries (keep last)
            dedup = {}
            for row in out:
                dedup[row["date"]] = row
            return sorted(dedup.values(), key=lambda x: x["date"])
        except Exception as e:
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("failed to fetch yahoo symbol %s: %s" % (symbol, last_err))


def fetch_fred(series_id, retries=3):
    """Returns list of dicts: [{date, open, high, low, close}] with O=H=L=C=value (level series).
    Shells out to curl rather than urllib: FRED's bot-protection silently stalls Python's
    urllib/ssl client (never returns within any timeout) but passes curl's TLS handshake fine."""
    url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=%s" % series_id
    last_err = None
    for attempt in range(retries):
        try:
            proc = subprocess.run(
                ["curl", "-s", "-m", "20", url],
                capture_output=True, timeout=25, check=True,
            )
            text = proc.stdout.decode("utf-8")
            lines = text.strip().splitlines()
            out = []
            for line in lines[1:]:
                parts = line.split(",")
                if len(parts) != 2:
                    continue
                date, val = parts
                if val == "." or val == "":
                    continue
                try:
                    v = float(val)
                except ValueError:
                    continue
                out.append({"date": date, "open": v, "high": v, "low": v, "close": v})
            out.sort(key=lambda x: x["date"])
            return out
        except Exception as e:
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("failed to fetch FRED series %s: %s" % (series_id, last_err))


def ratio_series(legA, legB):
    """Elementwise O/H/L/C ratio of two OHLC series aligned by forward-filled date, mirroring
    how TradingView's synthetic ticker.inherit(A/B) ratio symbols behave."""
    from align import forward_fill_join
    joined = forward_fill_join([legA, legB])
    out = []
    for date, (a, b) in joined:
        if a is None or b is None:
            continue
        if b["close"] in (0, None) or b["open"] in (0, None):
            continue
        out.append({
            "date": date,
            "open": a["open"] / b["open"] if b["open"] else None,
            "high": a["high"] / b["high"] if b["high"] else None,
            "low": a["low"] / b["low"] if b["low"] else None,
            "close": a["close"] / b["close"] if b["close"] else None,
        })
    return [r for r in out if r["close"] is not None]
