"""Big Picture: market-health regimes across 29 equity markets, plus CFTC positioning in 44 futures.

Layout follows WallStreetCourier's "Big Picture" page (weather map, regime table, CoT table). The
scores are our own, from daily closes only; their indicator set is proprietary and not copied.

Market health, per market and per horizon, 0-100 (50 = no trend):
  Short (S)  ~1 month   price vs 20d EMA, 10d return, signed R2 of 20d trend, up days 20d, RSI 14
  Mid (M)    ~3 months  price vs 50d SMA, 20d EMA vs 50d EMA, 50d SMA slope, 63d return,
                        signed R2 of 63d trend, up days 63d, position in 63d range
  Long (L)   ~1 year    price vs 200d SMA, 50d vs 200d SMA, 200d SMA slope, 252d return,
                        signed R2 of 252d trend, position in 252d range
Trend inputs are divided by the market's own daily volatility (60d stdev) times sqrt(horizon), then
mapped to 0-100 with the normal CDF, so markets with different volatility score on one scale.
Up-day shares use the binomial z. R2 inputs are 50 + 50 * sign(slope) * R2. Range positions and RSI
are already 0-100. A horizon's health is the mean of its inputs. Total = mean(S, M, L).

Regime, on the mid (x) and short (y) health plane:
  M >= 50 and S >= 50                 Very High Reward   (risk-on)
  M >= 50, S < 50, S + M >= 100       High Reward        (risk-on)
  M < 50, S >= 50, S + M >= 100       Increasing Reward  (risk-on)
  M < 50, S >= 50, S + M < 100        High Risk          (risk-off)
  M >= 50, S < 50, S + M < 100        Increasing Risk    (risk-off)
  M < 50 and S < 50                   Very High Risk     (risk-off)
Long-term bull: L >= 50.

CoT: CFTC legacy futures-only report, non-commercial (large speculator) net position. Index =
(net - min) / (max - min) * 100 over the trailing 1, 3 and 5 years of weekly reports. >= 80 bullish,
<= 20 bearish, on the 3-year index. Positions are as of Tuesday, published the following Friday.

Writes bigpic_doc.json (db: regime/bigpic). render.py embeds it as __BIGPIC_JSON__.
"""
import bisect
import datetime as dt
import json
import math
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request

from fetch import UA

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(HERE, "cache")
OUT = os.path.join(HERE, "bigpic_doc.json")

# key, name, region, Yahoo symbol, note
MARKETS = [
    ("world", "MSCI World", "Global", "^990100-USD-STRD", "MSCI World price index, USD"),
    ("spx", "S&P 500", "North America", "^GSPC", None),
    ("ndx", "Nasdaq 100", "North America", "^NDX", None),
    ("nya", "NYSE Composite", "North America", "^NYA", None),
    ("tsx", "S&P/TSX Composite", "North America", "^GSPTSE", None),
    ("rut", "Russell 2000", "North America", "^RUT", None),
    ("dax", "DAX", "Europe", "^GDAXI", None),
    ("cac", "CAC 40", "Europe", "^FCHI", None),
    ("mib", "FTSE MIB", "Europe", "FTSEMIB.MI", None),
    ("ibex", "IBEX 35", "Europe", "^IBEX", None),
    ("sxxp", "STOXX 600", "Europe", "^STOXX", None),
    ("ftse", "FTSE 100", "Europe", "^FTSE", None),
    ("asx", "S&P/ASX 200", "Asia Pacific", "^AXJO", None),
    ("nky", "Nikkei 225", "Asia Pacific", "^N225", None),
    ("csi", "CSI 300", "Emerging", "ASHR", "ASHR ETF (CSI 300, USD): Yahoo has no CSI 300 history"),
    ("hsi", "Hang Seng", "Emerging", "^HSI", None),
    ("em", "Emerging Markets", "Emerging", "EEM", "EEM ETF (MSCI EM)"),
    ("xlb", "Materials", "US sectors", "XLB", None),
    ("xlre", "Real Estate", "US sectors", "XLRE", None),
    ("xlu", "Utilities", "US sectors", "XLU", None),
    ("xle", "Energy", "US sectors", "XLE", None),
    ("xlp", "Consumer Staples", "US sectors", "XLP", None),
    ("xli", "Industrials", "US sectors", "XLI", None),
    ("xlv", "Health Care", "US sectors", "XLV", None),
    ("xlk", "Technology", "US sectors", "XLK", None),
    ("xlf", "Financials", "US sectors", "XLF", None),
    ("xly", "Consumer Discretionary", "US sectors", "XLY", None),
    ("xlc", "Communication Services", "US sectors", "XLC", None),
    ("gdx", "Gold Miners", "US sectors", "GDX", None),
]

REGIMES = ["Very High Reward", "High Reward", "Increasing Reward",
           "Increasing Risk", "High Risk", "Very High Risk"]   # index 0-2 risk-on, 3-5 risk-off

# group, CFTC legacy contract code, short code, name
COT = [
    ("Currencies", "232741", "AD", "Australian dollar"),
    ("Currencies", "133741", "BTC", "Bitcoin"),
    ("Currencies", "096742", "BP", "British pound"),
    ("Currencies", "090741", "CD", "Canadian dollar"),
    ("Currencies", "099741", "EC", "Euro"),
    ("Currencies", "097741", "JY", "Japanese yen"),
    ("Currencies", "095741", "MP", "Mexican peso"),
    ("Currencies", "092741", "SF", "Swiss franc"),
    ("Currencies", "098662", "DX", "US dollar index"),
    ("Energies", "067651", "CL", "WTI crude oil"),
    ("Energies", "023651", "NG", "Natural gas"),
    ("Energies", "111659", "RB", "RBOB gasoline"),
    ("Grains", "002602", "ZC", "Corn"),
    ("Grains", "005602", "ZS", "Soybeans"),
    ("Grains", "026603", "ZM", "Soybean meal"),
    ("Grains", "007601", "ZL", "Soybean oil"),
    ("Grains", "001602", "ZW", "Wheat (Chicago SRW)"),
    ("Grains", "001612", "KE", "Wheat (Kansas HRW)"),
    ("Grains", "001626", "MWE", "Wheat (spring)"),
    ("Grains", "039601", "ZR", "Rough rice"),
    ("Indices", "13874A", "ES", "S&P 500 e-mini"),
    ("Indices", "209742", "NQ", "Nasdaq 100 mini"),
    ("Indices", "124603", "YM", "Dow mini"),
    ("Indices", "239742", "RTY", "Russell 2000 mini"),
    ("Indices", "240743", "NIY", "Nikkei 225 (yen)"),
    ("Indices", "1170E1", "VX", "VIX futures"),
    ("Livestock", "057642", "LE", "Live cattle"),
    ("Livestock", "061641", "GF", "Feeder cattle"),
    ("Livestock", "054642", "HE", "Lean hogs"),
    ("Metals", "088691", "GC", "Gold"),
    ("Metals", "084691", "SI", "Silver"),
    ("Metals", "085692", "HG", "Copper"),
    ("Metals", "076651", "PL", "Platinum"),
    ("Metals", "075651", "PA", "Palladium"),
    ("Rates", "134741", "SR3", "3M SOFR"),
    ("Rates", "042601", "ZT", "2Y Treasury note"),
    ("Rates", "044601", "ZF", "5Y Treasury note"),
    ("Rates", "043602", "ZN", "10Y Treasury note"),
    ("Rates", "020601", "ZB", "Treasury bond"),
    ("Softs", "073732", "CC", "Cocoa"),
    ("Softs", "083731", "KC", "Coffee"),
    ("Softs", "080732", "SB", "Sugar"),
    ("Softs", "033661", "CT", "Cotton"),
    ("Softs", "040701", "OJ", "Orange juice"),
]

HIST_DAYS = 260      # daily history kept for the weather-map date stepper
VOL_WIN = 60
WARMUP = 252 + 20    # bars before a market is scored


# ---------- data ----------

def fetch_closes(symbol, period1=946684800):
    """Daily closes since 2000 as [(date, close)], dated in the exchange timezone.
    Explicit period1/period2: range=max silently returns coarser-than-daily bars."""
    url = "https://query1.finance.yahoo.com/v8/finance/chart/%s?period1=%d&period2=%d&interval=1d" % (
        urllib.parse.quote(symbol, safe=""), period1, int(time.time()) + 86400)
    last = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                res = json.loads(r.read())["chart"]["result"][0]
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(res["meta"].get("exchangeTimezoneName") or "UTC")
            closes = res["indicators"]["quote"][0]["close"]
            out = {}
            for t, c in zip(res.get("timestamp") or [], closes):
                if c is None:
                    continue
                out[dt.datetime.fromtimestamp(t, tz).strftime("%Y-%m-%d")] = c
            rows = sorted(out.items())
            if len(rows) < 300:
                raise ValueError("only %d bars" % len(rows))
            return rows
        except Exception as e:
            last = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError("yahoo %s: %s" % (symbol, last))


def fetch_cot(since="2014-01-01"):
    """Weekly legacy futures-only rows for the COT codes: {code: [(date, long, short, oi)]}."""
    codes = ",".join("'%s'" % c[1] for c in COT)
    params = {
        "$select": "cftc_contract_market_code,report_date_as_yyyy_mm_dd,noncomm_positions_long_all,"
                   "noncomm_positions_short_all,open_interest_all",
        "$where": "cftc_contract_market_code in(%s) AND report_date_as_yyyy_mm_dd >= '%s'" % (codes, since),
        "$order": "report_date_as_yyyy_mm_dd",
        "$limit": "60000",
    }
    url = "https://publicreporting.cftc.gov/resource/6dca-aqww.json?" + urllib.parse.urlencode(params)
    last = None
    for attempt in range(4):
        try:
            # curl rather than urllib, as for FRED: some gov endpoints stall Python's TLS client
            proc = subprocess.run(["curl", "-s", "-m", "90", "-A", UA, url],
                                  capture_output=True, timeout=100, check=True)
            rows = json.loads(proc.stdout)
            if not isinstance(rows, list) or not rows:
                raise ValueError("CFTC returned %r" % str(rows)[:120])
            out = {}
            for r in rows:
                try:
                    out.setdefault(r["cftc_contract_market_code"].strip(), []).append((
                        r["report_date_as_yyyy_mm_dd"][:10], float(r["noncomm_positions_long_all"]),
                        float(r["noncomm_positions_short_all"]), float(r["open_interest_all"])))
                except (KeyError, ValueError):
                    continue
            for k in out:   # one row per date (keep last)
                out[k] = sorted({d: (d, a, b, c) for d, a, b, c in out[k]}.values())
            return out
        except Exception as e:
            last = e
            time.sleep(5 * (attempt + 1))
    raise RuntimeError("CFTC: %s" % last)


# ---------- indicators ----------

def ncdf(z):
    return 50.0 * (1.0 + math.erf(z / math.sqrt(2.0)))


def ema(xs, n):
    a = 2.0 / (n + 1)
    out, e = [], None
    for x in xs:
        e = x if e is None else e + a * (x - e)
        out.append(e)
    return out


def sma(xs, n):
    out, s = [], 0.0
    for i, x in enumerate(xs):
        s += x
        if i >= n:
            s -= xs[i - n]
        out.append(s / n if i >= n - 1 else None)
    return out


def rolling_std(xs, n):
    out, s, s2 = [], 0.0, 0.0
    for i, x in enumerate(xs):
        s += x
        s2 += x * x
        if i >= n:
            s -= xs[i - n]
            s2 -= xs[i - n] ** 2
        if i >= n - 1:
            m = s / n
            out.append(math.sqrt(max(s2 / n - m * m, 1e-12)))
        else:
            out.append(None)
    return out


def rolling_count_pos(xs, n):
    out, c = [], 0
    for i, x in enumerate(xs):
        c += x > 0
        if i >= n:
            c -= xs[i - n] > 0
        out.append(c if i >= n - 1 else None)
    return out


def rolling_r2(ys, n):
    """Signed R2 of a linear fit of log price on time over the last n bars, as 50 + 50*sign*R2."""
    out = [None] * len(ys)
    sx = n * (n - 1) / 2.0
    sxx = (n - 1) * n * (2 * n - 1) / 6.0
    vx = sxx - sx * sx / n
    sy = sy2 = sxy = 0.0
    for i, y in enumerate(ys):
        if i >= n:   # shift window: drop y[i-n], re-index x by -1
            yo = ys[i - n]
            sy_prev = sy
            sy -= yo
            sy2 -= yo * yo
            sxy = sxy - sy_prev + yo   # sum((x-1)*y) over remaining = sxy - sum(y) [x of dropped was 0]
        k = min(i, n - 1)
        sy += y
        sy2 += y * y
        sxy += k * y
        if i >= n - 1:
            cov = sxy - sx * sy / n
            vy = sy2 - sy * sy / n
            if vy <= 1e-18:
                out[i] = 50.0
            else:
                r2 = cov * cov / (vx * vy)
                out[i] = 50.0 + 50.0 * (1 if cov > 0 else -1) * min(r2, 1.0)
    return out


def rolling_range_pos(xs, n):
    out = []
    from collections import deque
    mx, mn = deque(), deque()
    for i, x in enumerate(xs):
        while mx and xs[mx[-1]] <= x:
            mx.pop()
        mx.append(i)
        while mn and xs[mn[-1]] >= x:
            mn.pop()
        mn.append(i)
        if mx[0] <= i - n:
            mx.popleft()
        if mn[0] <= i - n:
            mn.popleft()
        if i >= n - 1:
            hi, lo = xs[mx[0]], xs[mn[0]]
            out.append(100.0 * (x - lo) / (hi - lo) if hi > lo else 50.0)
        else:
            out.append(None)
    return out


def rsi(xs, n=14):
    out, ag, al = [None], None, None
    for i in range(1, len(xs)):
        ch = xs[i] - xs[i - 1]
        g, l = max(ch, 0.0), max(-ch, 0.0)
        if ag is None:
            ag, al = g, l
        else:
            ag = (ag * (n - 1) + g) / n
            al = (al * (n - 1) + l) / n
        out.append(100.0 if al == 0 else 100.0 - 100.0 / (1 + ag / al))
    return out


def health(closes):
    """Per-bar (S, M, L) health 0-100 for one market, None until warmed up."""
    n = len(closes)
    lp = [math.log(c) for c in closes]
    ret = [0.0] + [lp[i] - lp[i - 1] for i in range(1, n)]
    vol = rolling_std(ret, VOL_WIN)
    e20, e50 = ema(closes, 20), ema(closes, 50)
    s50, s200 = sma(closes, 50), sma(closes, 200)
    up20, up63 = rolling_count_pos(ret, 20), rolling_count_pos(ret, 63)
    r2_20, r2_63, r2_252 = rolling_r2(lp, 20), rolling_r2(lp, 63), rolling_r2(lp, 252)
    rp63, rp252 = rolling_range_pos(closes, 63), rolling_range_pos(closes, 252)
    rs = rsi(closes, 14)

    def tz(x, h, i):   # vol-scaled trend input -> 0-100
        return ncdf(x / (vol[i] * math.sqrt(h)))

    def bz(k, m):      # share of up days, binomial z -> 0-100
        return ncdf((k - m / 2.0) / (math.sqrt(m) / 2.0))

    out = []
    for i in range(n):
        if i < WARMUP or vol[i] is None:
            out.append(None)
            continue
        S = [tz(math.log(closes[i] / e20[i]), 10, i), tz(lp[i] - lp[i - 10], 10, i),
             r2_20[i], bz(up20[i], 20), rs[i]]
        M = [tz(math.log(closes[i] / s50[i]), 25, i), tz(math.log(e20[i] / e50[i]), 25, i),
             tz(math.log(s50[i] / s50[i - 20]), 40, i), tz(lp[i] - lp[i - 63], 63, i),
             r2_63[i], bz(up63[i], 63), rp63[i]]
        L = [tz(math.log(closes[i] / s200[i]), 100, i), tz(math.log(s50[i] / s200[i]), 100, i),
             tz(math.log(s200[i] / s200[i - 20]), 150, i), tz(lp[i] - lp[i - 252], 252, i),
             r2_252[i], rp252[i]]
        out.append(tuple(sum(v) / len(v) for v in (S, M, L)))
    return out


def regime_of(s, m):
    if m >= 50 and s >= 50:
        return 0
    if s + m >= 100:
        return 1 if m >= 50 else 2
    if m >= 50:
        return 3
    return 4 if s >= 50 else 5


# ---------- build ----------

def build_markets():
    series = {}
    errors = {}
    for key, name, region, sym, note in MARKETS:
        try:
            rows = fetch_closes(sym)
        except Exception as e:
            errors[key] = str(e)[:160]
            print("  %-6s FAILED %s" % (key, e), file=sys.stderr)
            continue
        dates = [d for d, _ in rows]
        closes = [c for _, c in rows]
        h = health(closes)
        reg = [None if x is None else regime_of(x[0], x[1]) for x in h]
        series[key] = dict(dates=dates, closes=closes, h=h, reg=reg)
        print("  %-6s %5d bars  %s .. %s" % (key, len(rows), dates[0], dates[-1]))
    return series, errors


def persistence(series):
    """Pooled across markets: P(same regime after 1 and 5 bars | regime today), plus 20-day forward
    return by regime. Uses each market's own bars from its first scored day."""
    stay1 = [[0, 0] for _ in REGIMES]
    stay5 = [[0, 0] for _ in REGIMES]
    fwd = [[] for _ in REGIMES]
    allf = []
    for s in series.values():
        reg, c = s["reg"], s["closes"]
        for i, r in enumerate(reg):
            if r is None:
                continue
            if i + 1 < len(reg):
                stay1[r][0] += reg[i + 1] == r
                stay1[r][1] += 1
            if i + 5 < len(reg):
                stay5[r][0] += reg[i + 5] == r
                stay5[r][1] += 1
            if i + 20 < len(c):
                f = c[i + 20] / c[i] - 1
                v = math.sqrt(sum(math.log(c[k] / c[k - 1]) ** 2 for k in range(i + 1, i + 21)) / 20 * 252)
                fwd[r].append((f, v))
                allf.append((f, v))
    def stats(xs):
        if not xs:
            return None
        vol = sum(v for _, v in xs) / len(xs)
        xs = sorted(f for f, _ in xs)
        m = sum(xs) / len(xs)
        return dict(n=len(xs), mean=round(100 * m, 2), median=round(100 * xs[len(xs) // 2], 2),
                    hit=round(100 * sum(x > 0 for x in xs) / len(xs), 1),
                    p5=round(100 * xs[int(0.05 * len(xs))], 2), vol=round(100 * vol, 1))
    return dict(
        stay1=[round(100 * a / b, 1) if b else None for a, b in stay1],
        stay5=[round(100 * a / b, 1) if b else None for a, b in stay5],
        fwd=[stats(x) for x in fwd], fwd_all=stats(allf),
        share=[round(100 * len(x) / max(len(allf), 1), 1) for x in fwd],
    )


def current_rows(series, pers):
    rows = []
    for key, name, region, sym, note in MARKETS:
        s = series.get(key)
        if not s:
            rows.append(dict(key=key, name=name, region=region, symbol=sym, note=note, error=True))
            continue
        i = len(s["h"]) - 1
        S, M, L = s["h"][i]
        r = s["reg"][i]
        j = i
        while j > 0 and s["reg"][j - 1] == r:
            j -= 1
        start_close = s["closes"][j - 1] if j > 0 else s["closes"][j]
        r5 = s["reg"][i - 5]
        S5, M5, L5 = s["h"][i - 5]
        prev_side = s["reg"][i - 1] <= 2
        side = r <= 2
        rows.append(dict(
            key=key, name=name, region=region, symbol=sym, note=note, date=s["dates"][i],
            close=round(s["closes"][i], 2), chg1=round(100 * (s["closes"][i] / s["closes"][i - 1] - 1), 2),
            S=round(S, 1), M=round(M, 1), L=round(L, 1), T=round((S + M + L) / 3, 1),
            regime=r, side="on" if side else "off", new=side != prev_side, switched=r != s["reg"][i - 1],
            regime5=r5, dT5=round((S + M + L) / 3 - (S5 + M5 + L5) / 3, 1),
            since=s["dates"][j], days=i - j + 1, env=round(100 * (s["closes"][i] / start_close - 1), 2),
            stab1=pers["stay1"][r], stab5=pers["stay5"][r], ltbull=L >= 50,
        ))
    return rows


def history(series):
    """Last HIST_DAYS dates of the union calendar, each market forward-filled: [S, M, L] ints."""
    cal = sorted(set(d for s in series.values() for d in s["dates"][-HIST_DAYS - 10:]))[-HIST_DAYS:]
    out = {}
    for key, s in series.items():
        idx = {d: i for i, d in enumerate(s["dates"])}
        arr, last = [], None
        for d in cal:
            if d in idx and s["h"][idx[d]] is not None:
                last = [round(v) for v in s["h"][idx[d]]]
            arr.append(last)
        out[key] = arr
    return dict(dates=cal, data=out)


def breadth(series):
    """Weekly (Friday) share of scored markets risk-on and in a long-term bull, since 2007."""
    cal = sorted(set(d for s in series.values() for d in s["dates"] if d >= "2007-01-01"))
    fridays = [d for d in cal if dt.date.fromisoformat(d).weekday() == 4]
    if cal[-1] not in fridays:
        fridays.append(cal[-1])
    on, bull, n = [], [], []
    for d in fridays:
        c_on = c_bull = c_n = 0
        for s in series.values():
            i = bisect.bisect_right(s["dates"], d) - 1
            if i < 0 or s["h"][i] is None:
                continue
            if (dt.date.fromisoformat(d) - dt.date.fromisoformat(s["dates"][i])).days > 7:
                continue   # market not trading then (e.g. before its history starts or after delisting)
            c_n += 1
            c_on += s["reg"][i] <= 2
            c_bull += s["h"][i][2] >= 50
        n.append(c_n)
        on.append(round(100 * c_on / c_n, 1) if c_n else None)
        bull.append(round(100 * c_bull / c_n, 1) if c_n else None)
    return dict(dates=fridays, on=on, bull=bull, n=n)


def cot_rows(raw):
    rows = []
    for group, code, short, name in COT:
        r = raw.get(code)
        if not r or len(r) < 60:
            rows.append(dict(group=group, code=short, name=name, error=True))
            continue
        net = [a - b for _, a, b, _ in r]
        dates = [d for d, _, _, _ in r]

        def index(i, weeks):
            w = net[max(0, i - weeks + 1): i + 1]
            lo, hi = min(w), max(w)
            return round(100 * (net[i] - lo) / (hi - lo), 1) if hi > lo else 50.0

        i = len(net) - 1
        idx3 = [index(k, 156) for k in range(max(0, i - 25), i + 1)]
        rows.append(dict(
            group=group, code=short, name=name, date=dates[i], net=int(net[i]),
            chg=int(net[i] - net[i - 1]), oi=int(r[i][3]),
            pct_oi=round(100 * net[i] / r[i][3], 1) if r[i][3] else None,
            i1=index(i, 52), i3=index(i, 156), i5=index(i, 260),
            spark=idx3, years=round(len(net) / 52, 1),
        ))
    return rows


def main():
    print("Big Picture: fetching %d markets" % len(MARKETS))
    series, errors = build_markets()
    pers = persistence(series)
    rows = current_rows(series, pers)
    ok = [r for r in rows if not r.get("error")]
    asof = max(r["date"] for r in ok)
    summary = dict(
        n=len(ok), on=sum(r["side"] == "on" for r in ok), off=sum(r["side"] == "off" for r in ok),
        new=sum(r["new"] for r in ok), switched=sum(r["switched"] for r in ok),
        bull=sum(r["ltbull"] for r in ok),
    )
    print("Big Picture: fetching CFTC positioning")
    try:
        cot_raw = fetch_cot()
        cot = cot_rows(cot_raw)
        cot_asof = max(r["date"] for r in cot if not r.get("error"))
    except Exception as e:
        errors["cot"] = str(e)[:160]
        cot, cot_asof, cot_raw = [], None, None
    import bigpic_extras as ex

    def part(label, fn):
        print("Big Picture: %s" % label)
        t0 = time.time()
        try:
            out = fn()
            print("  done in %.0fs" % (time.time() - t0))
            return out
        except Exception as e:
            errors[label] = str(e)[:160]
            print("  FAILED: %s" % e, file=sys.stderr)
            return None
    smi = part("smart money proxy", ex.smart_money)
    idxb = part("index breadth", lambda: ex.breadth(
        {k: list(zip(series[k]["dates"], series[k]["closes"])) for k in ("spx", "ndx") if k in series}))
    bt_reg = part("regime backtest", lambda: ex.regime_backtest(series))
    bt_cot = part("CoT backtest", lambda: ex.cot_backtest(COT, cot_raw)) if cot_raw else None
    doc = dict(
        asof=asof, built=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), regimes=REGIMES,
        summary=summary, markets=rows, persistence=pers, history=history(series),
        breadth=breadth(series), cot=dict(asof=cot_asof, rows=cot), errors=errors,
        smi=smi, idx_breadth=idxb, backtest=dict(regimes=bt_reg, cot=bt_cot),
    )
    with open(OUT, "w") as f:
        json.dump(doc, f, separators=(",", ":"))
    print("Wrote %s (%d bytes): %d/%d risk-on, %d switches today, %d long-term bull; CoT as of %s, %d rows"
          % (OUT, os.path.getsize(OUT), summary["on"], summary["n"], summary["new"], summary["bull"],
             cot_asof, len(cot)))
    if errors:
        print("errors: %s" % errors)


if __name__ == "__main__":
    main()
