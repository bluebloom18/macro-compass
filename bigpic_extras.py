"""Big Picture extras: smart-money-flow proxy, index breadth proxy and backtests.

Called from big_picture.py; each part returns a dict for bigpic_doc.json and fails on its own
without stopping the rest.

Smart money flow proxy (DJIA). The classic Smart Money Index takes yesterday's value, subtracts
the market's move in the first 30 minutes and adds the move in the last hour. Yahoo keeps hourly
bars for only about two years and 30-minute bars for 60 days, so we use hourly bars:
  morning   = previous close -> 10:30 (first hourly bar, so it includes the overnight gap)
  afternoon = 14:30 -> close (last 90 minutes)
  SMI_t     = SMI_{t-1} + 100 * (ln(1 + afternoon) - ln(1 + morning))
Each day's values are appended to data/smart_money.csv so the history keeps growing past Yahoo's
two-year window.

Breadth proxy. Share of today's S&P 500 and Nasdaq-100 members above their 50- and 200-day
averages. Members come from Wikipedia (S&P 500) and Nasdaq's API (Nasdaq-100). Using today's
members for past dates carries survivorship bias: stocks that dropped out are missing.

Backtests.
  Regimes: each market held when the rule is on and in 3-month T-bills (^IRX) when off; the
    position is set at one close and earns the next day's return; 5bp per switch.
    Rules: risk on (the six regimes), long-term bull (L >= 50), total health >= 50.
  CoT: forward 4- and 13-week returns of the front-month future after each report, entered at the
    close of the first trading day after Friday's publication, by 3-year index bucket.
  Smart money and breadth: forward 20-day index returns by signal.
"""
import bisect
import concurrent.futures as cf
import csv
import datetime as dt
import json
import math
import os
import re
import statistics
import subprocess
import time
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

from fetch import UA

HERE = os.path.dirname(os.path.abspath(__file__))
SMI_CSV = os.path.join(HERE, "data", "smart_money.csv")
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"


# ---------- shared ----------

def yahoo_chart(symbol, interval="1d", period1=946684800):
    url = "https://query1.finance.yahoo.com/v8/finance/chart/%s?period1=%d&period2=%d&interval=%s" % (
        urllib.parse.quote(symbol, safe=""), period1, int(time.time()) + 86400, interval)
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())["chart"]["result"][0]
        except Exception as e:
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("yahoo %s %s: %s" % (symbol, interval, last))


def daily_closes(symbol, period1=946684800):
    """[(date, close)] in the exchange timezone; empty list if the symbol has no data."""
    res = yahoo_chart(symbol, "1d", period1)
    tz = ZoneInfo(res["meta"].get("exchangeTimezoneName") or "UTC")
    out = {}
    for t, c in zip(res.get("timestamp") or [], res["indicators"]["quote"][0]["close"]):
        if c is not None:
            out[dt.datetime.fromtimestamp(t, tz).strftime("%Y-%m-%d")] = c
    return sorted(out.items())


def bucket_stats(xs):
    if not xs:
        return None
    s = sorted(xs)
    return dict(n=len(s), mean=round(100 * sum(s) / len(s), 2), median=round(100 * s[len(s) // 2], 2),
                hit=round(100 * sum(x > 0 for x in s) / len(s), 1))


def fwd_return(dates, closes, i, n):
    return closes[i + n] / closes[i] - 1 if i + n < len(closes) else None


# ---------- smart money flow proxy ----------

def smart_money():
    res = yahoo_chart("^DJI", "60m", int(time.time()) - 729 * 86400)
    tz = ZoneInfo("America/New_York")
    q = res["indicators"]["quote"][0]
    days = {}
    for t, o, c in zip(res["timestamp"], q["open"], q["close"]):
        if c is None:
            continue
        d = dt.datetime.fromtimestamp(t, tz)
        days.setdefault(d.strftime("%Y-%m-%d"), []).append((d.strftime("%H:%M"), o, c))
    rows = {}
    prev_close = None
    for day in sorted(days):
        bars = sorted(days[day])
        close = bars[-1][2]
        first = next((b for b in bars if b[0] == "09:30"), None)
        late = next((b for b in bars if b[0] == "13:30"), None)   # bar 13:30-14:30; its close = 14:30
        if prev_close and first and late and len(bars) >= 6:
            rows[day] = (first[2] / prev_close - 1, close / late[2] - 1, close)
        prev_close = close
    # merge with the archive; fresh Yahoo values win for overlapping days
    arch = {}
    if os.path.exists(SMI_CSV):
        with open(SMI_CSV) as f:
            for r in csv.DictReader(f):
                arch[r["date"]] = (float(r["morning"]), float(r["afternoon"]), float(r["close"]))
    today = dt.datetime.now(tz).strftime("%Y-%m-%d")
    done = {d: v for d, v in rows.items() if d < today or dt.datetime.now(tz).hour >= 17}  # skip a live session
    arch.update(done)
    os.makedirs(os.path.dirname(SMI_CSV), exist_ok=True)
    with open(SMI_CSV, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "morning", "afternoon", "close"])
        for d in sorted(arch):
            m, a, c = arch[d]
            w.writerow([d, "%.6f" % m, "%.6f" % a, "%.2f" % c])
    dates = sorted(arch)
    # raw SMI drifts down because the morning leg includes the overnight gain, which is positive on
    # average; the signal uses the SMI net of its average daily move to date (no look-ahead)
    smi, adj, s, sa, tot = [], [], 0.0, 0.0, 0.0
    for k, d in enumerate(dates):
        m, a, _ = arch[d]
        x = 100 * (math.log1p(a) - math.log1p(m))
        s += x
        sa += x - (tot / k if k >= 60 else x)   # first 60 days: no drift estimate yet, flat
        tot += x
        smi.append(round(s, 3))
        adj.append(round(sa, 3))
    closes = [arch[d][2] for d in dates]
    # signal: 20-day change in SMI and in the Dow; divergence = Dow up while SMI down
    N = 20
    fwd = dict(smi_up=[], smi_down=[], div_bear=[], div_bull=[], all=[])
    state = []
    for i in range(len(dates)):
        if i < N:
            state.append(None)
            continue
        if i < 60 + N:
            state.append(None)
            continue
        ds, dd = adj[i] - adj[i - N], closes[i] / closes[i - N] - 1
        st = ("div_bear" if dd > 0 and ds < 0 else "div_bull" if dd < 0 and ds > 0
              else "smi_up" if ds > 0 else "smi_down")
        state.append(st)
        f = fwd_return(dates, closes, i, N)
        if f is not None:
            fwd["all"].append(f)
            fwd["smi_up" if ds > 0 else "smi_down"].append(f)
            if st.startswith("div"):
                fwd[st].append(f)
    i = len(dates) - 1
    return dict(
        dates=dates, smi=smi, adj=adj, close=[round(c, 2) for c in closes],
        drift=round(tot / len(dates), 4), state=state,
        latest=dict(date=dates[i], smi=smi[i], adj=adj[i], d20=round(adj[i] - adj[i - N], 2),
                    dow20=round(100 * (closes[i] / closes[i - N] - 1), 2), state=state[i],
                    morning=round(100 * arch[dates[i]][0], 2), afternoon=round(100 * arch[dates[i]][1], 2)),
        backtest={k: bucket_stats(v) for k, v in fwd.items()}, horizon=N,
        start=dates[0], days=len(dates),
    )


# ---------- breadth proxy ----------

def spx_members():
    proc = subprocess.run(["curl", "-sL", "-m", "40", "-A", UA,
                           "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"],
                          capture_output=True, timeout=50, check=True)
    h = proc.stdout.decode("utf-8", "replace")
    i = h.index('id="constituents"')
    t = h[i:h.index("</table>", i)]
    out = []
    for r in re.findall(r"<tr[^>]*>(.*?)</tr>", t, re.S)[1:]:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)
        if cells:
            out.append(re.sub("<[^>]+>", "", cells[0]).strip().replace(".", "-"))
    if len(out) < 480:
        raise ValueError("S&P 500 list has %d names" % len(out))
    return out


def ndx_members():
    proc = subprocess.run(["curl", "-s", "-m", "40", "-A", BROWSER_UA, "-H", "Accept: application/json",
                           "https://api.nasdaq.com/api/quote/list-type/nasdaq100"],
                          capture_output=True, timeout=50, check=True)
    rows = json.loads(proc.stdout)["data"]["data"]["rows"]
    out = [r["symbol"].strip().replace(".", "-") for r in rows]
    if len(out) < 95:
        raise ValueError("Nasdaq-100 list has %d names" % len(out))
    return out


def _sma_flags(closes, n):
    out, s = [], 0.0
    for i, c in enumerate(closes):
        s += c
        if i >= n:
            s -= closes[i - n]
        out.append(None if i < n - 1 else c > s / n)
    return out


def breadth(index_closes):
    """index_closes: {'spx': [(date, close)], 'ndx': [...]} from big_picture's series."""
    lists = {}
    errors = {}
    for key, fn in (("spx", spx_members), ("ndx", ndx_members)):
        try:
            lists[key] = fn()
        except Exception as e:
            errors[key] = str(e)[:160]
    syms = sorted(set(s for v in lists.values() for s in v))
    p1 = int(dt.datetime(2013, 1, 1).timestamp())

    def get(s):
        try:
            return s, daily_closes(s, p1)
        except Exception:
            return s, []
    data = {}
    with cf.ThreadPoolExecutor(8) as ex:
        for s, rows in ex.map(get, syms):
            if len(rows) >= 60:
                data[s] = rows
    flags = {}
    for s, rows in data.items():
        cl = [c for _, c in rows]
        flags[s] = ([d for d, _ in rows], _sma_flags(cl, 50), _sma_flags(cl, 200))
    out = dict(errors=errors)
    for key, members in lists.items():
        idx = index_closes.get(key) or []
        cal = [d for d, _ in idx if d >= "2014-01-01"]
        icl = dict(idx)
        p50, p200, n_ok = [], [], []
        for d in cal:
            a50 = t50 = a200 = t200 = 0
            for s in members:
                f = flags.get(s)
                if not f:
                    continue
                j = bisect.bisect_right(f[0], d) - 1
                if j < 0 or (dt.date.fromisoformat(d) - dt.date.fromisoformat(f[0][j])).days > 5:
                    continue
                if f[1][j] is not None:
                    t50 += 1
                    a50 += f[1][j]
                if f[2][j] is not None:
                    t200 += 1
                    a200 += f[2][j]
            p50.append(round(100 * a50 / t50, 1) if t50 else None)
            p200.append(round(100 * a200 / t200, 1) if t200 else None)
            n_ok.append(t200)
        # backtest: forward 20-day index return by % above 200d bucket, and divergence
        N = 20
        cls = [icl[d] for d in cal]
        buckets = {"0-20": [], "20-40": [], "40-60": [], "60-80": [], "80-100": []}
        div, alld = [], []
        for i, d in enumerate(cal):
            f = fwd_return(cal, cls, i, N)
            if f is None or p200[i] is None or i < 60:
                continue
            alld.append(f)
            b = min(int(p200[i] // 20), 4)
            buckets[list(buckets)[b]].append(f)
            # index within 2% of its 60-day high while fewer than half the members are above the 200d
            if cls[i] >= 0.98 * max(cls[i - 60:i + 1]) and p200[i] < 50:
                div.append(f)
        weekly = [i for i, d in enumerate(cal) if dt.date.fromisoformat(d).weekday() == 4 or i == len(cal) - 1]
        out[key] = dict(
            members=len(members), priced=sum(1 for s in members if s in data),
            dates=[cal[i] for i in weekly], p50=[p50[i] for i in weekly], p200=[p200[i] for i in weekly],
            latest=dict(date=cal[-1], p50=p50[-1], p200=p200[-1], p50_5=p50[-6], p200_5=p200[-6]),
            backtest=dict(buckets={k: bucket_stats(v) for k, v in buckets.items()},
                          divergence=bucket_stats(div), all=bucket_stats(alld), horizon=N),
        )
    return out


# ---------- regime backtest ----------

def strategy_stats(rets, days_in, switches, years):
    if not rets:
        return None
    eq, peak, mdd = 1.0, 1.0, 0.0
    for r in rets:
        eq *= 1 + r
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1)
    cagr = eq ** (1 / years) - 1 if years > 0 else None
    sd = statistics.pstdev(rets) * math.sqrt(252)
    return dict(cagr=round(100 * cagr, 2), vol=round(100 * sd, 1), mdd=round(100 * mdd, 1),
                inmkt=round(100 * days_in / len(rets), 1), sw=round(switches / years, 1) if years else None)


def regime_backtest(series, regimes_risk_on=(0, 1, 2), cost=0.0005):
    irx = daily_closes("^IRX")
    irx_d = [d for d, _ in irx]
    irx_v = [v for _, v in irx]
    rules = {
        "riskon": lambda h, r: r in regimes_risk_on,
        "ltbull": lambda h, r: h[2] >= 50,
        "total": lambda h, r: (h[0] + h[1] + h[2]) / 3 >= 50,
    }
    per = {}
    for key, s in series.items():
        dates, cl, H, R = s["dates"], s["closes"], s["h"], s["reg"]
        start = next(i for i, h in enumerate(H) if h is not None)
        years = (len(cl) - 1 - start) / 252
        bh = [cl[i] / cl[i - 1] - 1 for i in range(start + 1, len(cl))]
        sharpe_inputs = {}
        res = dict(start=dates[start], bh=strategy_stats(bh, len(bh), 0, years))
        cash = []
        for i in range(start + 1, len(cl)):
            j = bisect.bisect_right(irx_d, dates[i - 1]) - 1
            cash.append((irx_v[j] if j >= 0 else 0.0) / 100 / 252)
        res["cash"] = round(100 * ((1 + sum(cash) / len(cash)) ** 252 - 1), 2)
        for name, rule in rules.items():
            rets, inn, sw, prev = [], 0, 0, None
            for k, i in enumerate(range(start + 1, len(cl))):
                on = rule(H[i - 1], R[i - 1])
                r = (cl[i] / cl[i - 1] - 1) if on else cash[k]
                if prev is not None and on != prev:
                    r -= cost
                    sw += 1
                prev = on
                inn += on
                rets.append(r)
            res[name] = strategy_stats(rets, inn, sw, years)
            # Sharpe vs cash
            ex = [r - c for r, c in zip(rets, cash)]
            sd = statistics.pstdev(ex)
            res[name]["sharpe"] = round(statistics.mean(ex) / sd * math.sqrt(252), 2) if sd else None
        ex = [r - c for r, c in zip(bh, cash)]
        res["bh"]["sharpe"] = round(statistics.mean(ex) / statistics.pstdev(ex) * math.sqrt(252), 2)
        per[key] = res
    # summary across markets: medians, and how many markets beat buy & hold
    summ = {}
    for name in ["bh"] + list(rules):
        vals = [p[name] for p in per.values() if p.get(name)]
        summ[name] = {m: round(statistics.median(v[m] for v in vals), 2) for m in ("cagr", "vol", "mdd", "sharpe", "inmkt", "sw")
                      if all(v.get(m) is not None for v in vals)}
        if name != "bh":
            summ[name]["beat_sharpe"] = sum(1 for p in per.values() if p[name]["sharpe"] > p["bh"]["sharpe"])
            summ[name]["beat_cagr"] = sum(1 for p in per.values() if p[name]["cagr"] > p["bh"]["cagr"])
            summ[name]["smaller_dd"] = sum(1 for p in per.values() if p[name]["mdd"] > p["bh"]["mdd"])
    return dict(per=per, summary=summ, n=len(per), cost_bp=round(cost * 1e4, 1))


# ---------- CoT backtest ----------

COT_PRICE = {
    "AD": "6A=F", "BTC": "BTC=F", "BP": "6B=F", "CD": "6C=F", "EC": "6E=F", "JY": "6J=F", "MP": "6M=F",
    "SF": "6S=F", "DX": "DX-Y.NYB", "CL": "CL=F", "NG": "NG=F", "RB": "RB=F", "ZC": "ZC=F", "ZS": "ZS=F",
    "ZM": "ZM=F", "ZL": "ZL=F", "ZW": "ZW=F", "KE": "KE=F", "ZR": "ZR=F", "ES": "ES=F", "NQ": "NQ=F",
    "YM": "YM=F", "RTY": "RTY=F", "NIY": "NIY=F", "VX": "^VIX", "LE": "LE=F", "GF": "GF=F", "HE": "HE=F",
    "GC": "GC=F", "SI": "SI=F", "HG": "HG=F", "PL": "PL=F", "PA": "PA=F", "ZT": "ZT=F", "ZF": "ZF=F",
    "ZN": "ZN=F", "ZB": "ZB=F", "CC": "CC=F", "KC": "KC=F", "SB": "SB=F", "CT": "CT=F", "OJ": "OJ=F",
}


def cot_backtest(cot_meta, cot_raw):
    """cot_meta: [(group, code, short, name)]; cot_raw: {code: [(date, long, short, oi)]}."""
    p1 = int(dt.datetime(2014, 1, 1).timestamp())

    def get(short):
        try:
            return short, daily_closes(COT_PRICE[short], p1)
        except Exception:
            return short, []
    with cf.ThreadPoolExecutor(8) as ex:
        prices = dict(ex.map(get, [m[2] for m in cot_meta if m[2] in COT_PRICE]))
    horizons = {"w4": 20, "w13": 65}
    B = ("bull", "neutral", "bear")
    raw = {}     # short -> h -> bucket -> [f]
    tested = []
    for group, code, short, name in cot_meta:
        rows, px = cot_raw.get(code), prices.get(short)
        if not rows or not px or len(px) < 300:
            continue
        tested.append((group, short))
        pd_, pc = [d for d, _ in px], [c for _, c in px]
        net = [a - b for _, a, b, _ in rows]
        r = raw[short] = {h: {b: [] for b in B} for h in horizons}
        for k in range(156, len(rows)):
            w = net[k - 155:k + 1]
            lo, hi = min(w), max(w)
            if hi == lo:
                continue
            idx = 100 * (net[k] - lo) / (hi - lo)
            b = "bull" if idx >= 80 else "bear" if idx <= 20 else "neutral"
            entry_day = (dt.date.fromisoformat(rows[k][0]) + dt.timedelta(days=3)).isoformat()   # Friday release
            i = bisect.bisect_right(pd_, entry_day)   # first trading day after Friday
            for h, n in horizons.items():
                if i + n < len(pc):
                    r[h][b].append(pc[i + n] / pc[i] - 1)

    def summarise(shorts):
        out = {}
        for h in horizons:
            acc = {b: dict(z=[], f=[]) for b in B}
            for sh in shorts:
                allf = [f for b in B for f in raw[sh][h][b]]
                if len(allf) < 20:
                    continue
                m, sd = statistics.mean(allf), statistics.pstdev(allf) or 1
                for b in B:
                    for f in raw[sh][h][b]:
                        acc[b]["z"].append((f - m) / sd)   # vs the contract's own average move, in sd
                        acc[b]["f"].append(f)
            out[h] = {b: dict(n=len(a["f"]), hit=round(100 * sum(f > 0 for f in a["f"]) / len(a["f"]), 1),
                              edge=round(statistics.mean(a["z"]), 3)) if a["f"] else None for b, a in acc.items()}
        return out
    groups = {}
    for g, sh in tested:
        groups.setdefault(g, []).append(sh)
    return dict(
        tested=[sh for _, sh in tested], n_tested=len(tested),
        pooled=summarise([sh for _, sh in tested]),
        groups={g: summarise(v) for g, v in groups.items()},
    )
