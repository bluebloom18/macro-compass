"""Open-data fetchers for the Global Macro Regime port: Yahoo Finance chart API + FRED CSV."""
import json
import subprocess
import time
import urllib.request
import urllib.parse
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
            # Date each bar in the exchange's own timezone: Yahoo stamps FX daily bars at London
            # midnight, which is 23:00 UTC the previous day during BST, so gmtime() put them a day early.
            try:
                tz = ZoneInfo(r["meta"].get("exchangeTimezoneName") or "UTC")
            except ZoneInfoNotFoundError:
                tz = None
            out = []
            for i, t in enumerate(ts):
                o, h, l, c = quote["open"][i], quote["high"][i], quote["low"][i], quote["close"][i]
                if c is None:
                    continue
                date = (datetime.fromtimestamp(t, tz).strftime("%Y-%m-%d") if tz
                        else time.strftime("%Y-%m-%d", time.gmtime(t)))
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


_FRED_DOWN = []   # series that failed every retry in this process


def fetch_fred(series_id, retries=5):
    """Returns list of dicts: [{date, open, high, low, close}] with O=H=L=C=value (level series).
    Shells out to curl rather than urllib: FRED's bot-protection silently stalls Python's
    urllib/ssl client (never returns within any timeout) but passes curl's TLS handshake fine."""
    url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=%s" % series_id
    if _FRED_DOWN:   # one series already exhausted its retries this run: don't wait again per series
        retries = 1
    last_err = None
    for attempt in range(retries):
        try:
            proc = subprocess.run(
                ["curl", "-s", "-m", "20", url],
                capture_output=True, timeout=25, check=True,
            )
            text = proc.stdout.decode("utf-8", "replace")
            lines = text.strip().splitlines()
            # During FRED site maintenance the endpoint returns an HTML page with HTTP 200 (seen
            # 2026-10-21); without this check it parsed as an empty series and the signal vanished.
            if not lines or not lines[0].lower().startswith(("observation_date,", "date,")):
                raise ValueError("FRED returned non-CSV (site maintenance?): %r" % text[:80])
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
            if not out:
                raise ValueError("FRED returned no observations for %s" % series_id)
            out.sort(key=lambda x: x["date"])
            return out
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(15 * 2 ** attempt)   # 15s, 30s, 60s, 120s: rides out a short outage
    _FRED_DOWN.append(series_id)
    raise RuntimeError("failed to fetch FRED series %s: %s" % (series_id, last_err))


def fetch_cboe(index, retries=3):
    """Cboe's public daily history CSV (DATE,OPEN,HIGH,LOW,CLOSE, MM/DD/YYYY dates) for indices
    Yahoo carries only as a live snapshot, e.g. COR3M."""
    url = "https://cdn.cboe.com/api/global/us_indices/daily_prices/%s_History.csv" % index
    last_err = None
    for attempt in range(retries):
        try:
            proc = subprocess.run(["curl", "-sL", "-m", "30", "-A", UA, url],
                                  capture_output=True, timeout=35, check=True)
            lines = proc.stdout.decode("utf-8", "replace").strip().splitlines()
            if not lines or not lines[0].upper().startswith("DATE,"):
                raise ValueError("Cboe returned non-CSV for %s: %r" % (index, lines[:1]))
            out = []
            for line in lines[1:]:
                parts = line.split(",")
                if len(parts) != 5:
                    continue
                try:
                    o, h, l, c = (float(x) for x in parts[1:])
                except ValueError:
                    continue
                date = datetime.strptime(parts[0], "%m/%d/%Y").strftime("%Y-%m-%d")
                out.append({"date": date, "open": o, "high": h, "low": l, "close": c})
            if not out:
                raise ValueError("Cboe returned no observations for %s" % index)
            out.sort(key=lambda x: x["date"])
            return out
        except Exception as e:
            last_err = e
            time.sleep(5 * (attempt + 1))
    raise RuntimeError("failed to fetch Cboe index %s: %s" % (index, last_err))


def _curl_text(url, retries=3, what="series"):
    last_err = None
    for attempt in range(retries):
        try:
            proc = subprocess.run(["curl", "-sL", "-m", "60", "-A", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", url],
                                  capture_output=True, timeout=70, check=True)
            text = proc.stdout.decode("utf-8-sig", "replace")
            if not text.strip() or text.lstrip().lower().startswith(("<!doctype", "<html")):
                raise ValueError("non-CSV response: %r" % text[:80])
            return text
        except Exception as e:
            last_err = e
            time.sleep(10 * (attempt + 1))
    raise RuntimeError("failed to fetch %s: %s" % (what, last_err))


def _level_rows(pairs, what):
    out = {}
    for date, v in pairs:
        out[date] = {"date": date, "open": v, "high": v, "low": v, "close": v}
    if len(out) < 500:
        raise ValueError("%s: only %d observations" % (what, len(out)))
    return [out[k] for k in sorted(out)]


def fetch_boe(series_code):
    """Bank of England IADB daily series as CSV (e.g. IUDMNZC, 10y nominal zero-coupon gilt yield).
    Close-only, published about two business days late. Needs a browser-like user agent."""
    # Two requests: a query starting in 2000 came back cut off ~a week short (seen 2026-11-17, likely a
    # cached response), while one starting in 2015 ran to the latest release.
    pairs = []
    for frm, to in (("01/Jan/2000", "31/Dec/2014"), ("01/Jan/2015", "now")):
        url = ("https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp?csv.x=yes"
               "&Datefrom=%s&Dateto=%s&SeriesCodes=%s&CSVF=TN&UsingCodes=Y&VPD=Y&VFD=N" % (frm, to, series_code))
        lines = _curl_text(url, what="BoE " + series_code).strip().splitlines()
        if not lines[0].upper().startswith("DATE"):
            raise ValueError("BoE returned non-CSV for %s: %r" % (series_code, lines[0][:80]))
        for line in lines[1:]:
            parts = line.split(",")
            try:
                pairs.append((datetime.strptime(parts[0].strip(), "%d %b %Y").strftime("%Y-%m-%d"), float(parts[1])))
            except (ValueError, IndexError):
                continue
    return _level_rows(pairs, "BoE " + series_code)


def fetch_bbk(series_key):
    """Deutsche Bundesbank statistics API daily series as CSV, e.g.
    BBSIS/D.I.ZAR.ZI.EUR.S1311.B.A604.R10XX.R.A.A._Z._Z.A (10y Bund yield from the term structure).
    Close-only; weekend rows carry '.'."""
    url = "https://api.statistiken.bundesbank.de/rest/download/%s?format=csv&lang=en" % series_key
    text = _curl_text(url, what="Bundesbank " + series_key)
    pairs = []
    for line in text.splitlines():
        parts = line.split(",")
        if len(parts) < 2 or len(parts[0]) != 10 or parts[0][4] != "-":
            continue
        try:
            pairs.append((parts[0], float(parts[1])))
        except ValueError:
            continue
    return _level_rows(pairs, "Bundesbank " + series_key)


def fetch_mof(tenor="10Y"):
    """Japan Ministry of Finance JGB benchmark yields: the full history file plus the current-month
    file (the history file stops at the end of last month). Close-only."""
    base = "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/"
    pairs = []
    for url in (base + "historical/jgbcme_all.csv", base + "jgbcme.csv"):
        lines = _curl_text(url, what="MoF JGB").splitlines()
        hdr = next((i for i, l in enumerate(lines) if l.startswith("Date,")), None)
        if hdr is None:
            raise ValueError("MoF file has no Date header: %s" % url)
        col = lines[hdr].split(",").index(tenor)
        for line in lines[hdr + 1:]:
            parts = line.split(",")
            try:
                y, m, d = (int(x) for x in parts[0].split("/"))
                pairs.append(("%04d-%02d-%02d" % (y, m, d), float(parts[col])))
            except (ValueError, IndexError):
                continue
    return _level_rows([p for p in pairs if p[0] >= "2000-01-01"], "MoF JGB " + tenor)


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
