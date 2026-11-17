"""Trade signals (study 06): a trend label and a regime-aware position call for ~90 ETFs.

For each ETF:
  trend    bull / neutral / bear, from the same EMA(30)/EMA(60) cross with a 0.3x ATR(60) margin
           that drives the regime model's 30 signals (model.signal_logic), on the ETF's own price.
  rsi      14-day Wilder RSI.
  role     what the ETF is for, which fixes the regimes it should be held in (ROLES): risk assets in
           Goldilocks and Reflation, duration in Goldilocks and Deflation, inflation hedges in
           Reflation and Inflation, gold in Reflation and Deflation, the dollar and safe-haven FX in
           Inflation and Deflation, cash-like bonds in all four.
  call     held in this regime: bull -> Long full, neutral -> Long half, bear -> No position.
           Not held in this regime -> No position. Long-only: shorting the out-of-favour assets lost
           money in every test (2006-26), and so did roles learned from each ETF's own regime returns
           (too noisy: a regime's average return carries a standard error of ~8%/yr for equities).
  reg_ret  the ETF's annualised log total return in each regime over its history, shown as a check.

Also writes the risk-on/risk-off read (Goldilocks + Reflation vote share now, 1 week, 1 month and 3
months ago; risk-on/risk-off switches per year, ignoring spells shorter than 15 sessions) and a
backtest of the calls for a few assets: position w in {0, .5, 1} decided at the close two sessions
back (as backtest.py's LAG=1), return = w x (asset - T-bill) + T-bill, 5bp per unit traded.

Requires output.json (build.py). Writes trades_doc.json (db doc regime/trades).
Run `python3 trade_signals.py --fit` to score the trend labels against research/private/trade_fit_ref.json (local only, not in the repo).
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

from fetch import UA, fetch_fred
from model import signal_logic, rma

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache")
os.makedirs(CACHE, exist_ok=True)

COST_BP = 5.0
# regimes (1 Goldilocks, 2 Reflation, 3 Inflation, 4 Deflation) in which each role is held
ROLES = {
    "risk": (1, 2), "duration": (1, 4), "inflation": (2, 3), "gold": (2, 4),
    "haven": (3, 4), "cash": (1, 2, 3, 4)}
ROLE_LABEL = {"risk": "Risk asset", "duration": "Duration", "inflation": "Inflation hedge",
              "gold": "Gold", "haven": "Dollar / haven", "cash": "Cash-like"}
ROLE_OVERRIDE = {   # default role by asset class: eq/crypto/credit/fx -> risk, fi -> duration
    "XLE": "inflation", "GNR": "inflation", "VEGI": "inflation", "URNM": "inflation",
    "DBA": "inflation", "PDBC": "inflation", "USO": "inflation", "TIP": "inflation", "STIP": "cash",
    "SHY": "cash", "BILS": "cash", "GLD": "gold", "SLV": "gold", "GDX": "gold", "SIL": "gold",
    "UUP": "haven", "FXY": "haven", "FXF": "haven",
    "BIZD": "risk", "CWB": "risk", "EMLC": "risk", "EMB": "risk", "HYG": "risk", "BKLN": "risk",
    "PFF": "risk", "DBB": "risk"}


def role_of(t, cls):
    return ROLE_OVERRIDE.get(t) or ("duration" if cls == "fi" else "risk")
REG_NAMES = {1: "Goldilocks", 2: "Reflation", 3: "Inflation", 4: "Deflation"}

# group, ticker, label, asset class (equity / crypto may not take half shorts)
UNIVERSE = [
    ("US equity sectors", [
        ("SPY", "USA", "eq"), ("XLC", "Communication Services", "eq"), ("XLY", "Consumer Discretionary", "eq"),
        ("XLP", "Consumer Staples", "eq"), ("XLE", "Energy", "eq"), ("XLF", "Financials", "eq"),
        ("XLV", "Health Care", "eq"), ("XLI", "Industrials", "eq"), ("XLK", "Information Technology", "eq"),
        ("XLB", "Materials", "eq"), ("XLRE", "Real Estate", "eq"), ("XLU", "Utilities", "eq")]),
    ("Equity factors", [
        ("SPHD", "Dividend payers", "eq"), ("VEGI", "Global agriculture producers", "eq"),
        ("GNR", "Global commodity producers", "eq"), ("VT", "Global equities", "eq"),
        ("IGF", "Global infrastructure", "eq"), ("PICK", "Global metals & mining", "eq"),
        ("IFGL", "Global real estate", "eq"), ("IWF", "Growth", "eq"), ("SPHB", "High beta", "eq"),
        ("SPLV", "Low volatility", "eq"), ("MAGS", "Magnificent Seven", "eq"), ("QQQ", "Mega-cap growth", "eq"),
        ("IWR", "Mid caps", "eq"), ("MTUM", "Momentum", "eq"), ("QUAL", "Quality", "eq"),
        ("SMH", "Semiconductors", "eq"), ("IWM", "Small caps", "eq"), ("IWD", "Value", "eq")]),
    ("Geographies", [
        ("EWA", "Australia", "eq"), ("EWZ", "Brazil", "eq"), ("EWC", "Canada", "eq"), ("FXI", "China", "eq"),
        ("EEM", "Emerging markets", "eq"), ("EZU", "Eurozone", "eq"), ("ACWX", "Global ex-US", "eq"),
        ("INDA", "India", "eq"), ("IDEV", "International developed", "eq"), ("EWJ", "Japan", "eq"),
        ("EWY", "Korea", "eq"), ("EWN", "Netherlands", "eq"), ("EWT", "Taiwan", "eq"),
        ("EWU", "United Kingdom", "eq"), ("EWS", "Singapore", "eq"), ("EWH", "Hong Kong", "eq"),
        ("AAXJ", "Asia ex-Japan", "eq")]),
    ("Fixed income", [
        ("STIP", "0-5y TIPS", "fi"), ("SHY", "1-3y Treasuries", "fi"), ("BILS", "3-12m T-bills", "fi"),
        ("TIP", "TIPS", "fi"), ("IEF", "7-10y Treasuries", "fi"), ("TLT", "20y+ Treasuries", "fi"),
        ("AGG", "US aggregate", "fi"), ("BIZD", "BDCs", "fi"), ("CWB", "Convertibles", "fi"),
        ("EMLC", "EM local-currency bonds", "fi"), ("EMB", "EM USD bonds", "fi"), ("HYG", "High yield", "fi"),
        ("BNDW", "Global aggregate", "fi"), ("BNDX", "Global aggregate ex-US", "fi"),
        ("BWX", "International government", "fi"), ("LQD", "Investment grade", "fi"),
        ("BKLN", "Leveraged loans", "fi"), ("MBB", "Mortgage-backed", "fi"), ("PFF", "Preferreds", "fi")]),
    ("Macro exposures", [
        ("DBA", "Agriculture", "cmd"), ("FXA", "Australian dollar", "fx"), ("DBB", "Base metals", "cmd"),
        ("BTC-USD", "Bitcoin", "crypto"), ("FXB", "British pound", "fx"), ("FXC", "Canadian dollar", "fx"),
        ("PDBC", "Commodities", "cmd"), ("USO", "Crude oil", "cmd"), ("ETH-USD", "Ethereum", "crypto"),
        ("FXE", "Euro", "fx"), ("GLD", "Gold", "cmd"), ("GDX", "Gold miners", "eq"),
        ("FXY", "Japanese yen", "fx"), ("SLV", "Silver", "cmd"), ("SIL", "Silver miners", "eq"),
        ("FXF", "Swiss franc", "fx"), ("URNM", "Uranium miners", "eq"), ("UUP", "US dollar", "fx")]),
]
BACKTEST_ASSETS = [("SPY", "S&P 500"), ("VT", "Global equities"), ("AAXJ", "Asia ex-Japan"),
                   ("HYG", "High yield"), ("TLT", "20y+ Treasuries"),
                   ("GLD", "Gold"), ("BTC-USD", "Bitcoin")]


def fetch_bars(symbol):
    """Daily OHLC + adjusted close since 2000 (explicit period1: range=max downsamples)."""
    path = os.path.join(CACHE, "ts_" + symbol.replace("/", "_") + ".json")
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < 3600 * 12:
        with open(path) as f:
            return json.load(f)
    url = "https://query1.finance.yahoo.com/v8/finance/chart/%s?period1=946684800&period2=%d&interval=1d" % (
        urllib.parse.quote(symbol, safe=""), int(time.time()))
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as resp:
                r = json.loads(resp.read())["chart"]["result"][0]
            break
        except Exception as e:   # noqa: BLE001 - retried, then raised
            last = e
            time.sleep(2 * (attempt + 1))
    else:
        raise RuntimeError("yahoo %s: %s" % (symbol, last))
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(r["meta"].get("exchangeTimezoneName") or "UTC")
    q = r["indicators"]["quote"][0]
    adj = (r["indicators"].get("adjclose") or [{}])[0].get("adjclose") or q["close"]
    rows = {}
    for i, t in enumerate(r.get("timestamp") or []):
        c = q["close"][i]
        if c is None:
            continue
        d = dt.datetime.fromtimestamp(t, tz).strftime("%Y-%m-%d")
        o, h, lo = q["open"][i], q["high"][i], q["low"][i]
        rows[d] = dict(date=d, open=o if o is not None else c, high=h if h is not None else c,
                       low=lo if lo is not None else c, close=c, adj=adj[i] if adj[i] is not None else c)
    out = [rows[k] for k in sorted(rows)]
    with open(path, "w") as f:
        json.dump(out, f)
    return out


def trend_states(bars, **kw):
    ready, bull, bear = signal_logic(bars, **kw)
    return [("bull" if bu else "bear" if be else "neutral") if rd else None
            for rd, bu, be in zip(ready, bull, bear)]


def rsi(closes, n=14):
    gains = [None] + [max(closes[i] - closes[i - 1], 0) for i in range(1, len(closes))]
    losses = [None] + [max(closes[i - 1] - closes[i], 0) for i in range(1, len(closes))]
    ag, al = rma(gains, n), rma(losses, n)
    out = []
    for g, l in zip(ag, al):
        out.append(None if g is None or l is None else (100.0 if l == 0 else 100 - 100 / (1 + g / l)))
    return out


def regime_lookup(dates, regimes):
    """Regime in force for a return ending on date d: the regime read at the close two master
    sessions before d (signal at close t, position from close t+1), as backtest.py with LAG=1."""
    def at(d):
        k = bisect.bisect_left(dates, d) - 2   # master index of two sessions before d
        return regimes[k] if k >= 0 else None
    return at


def call_for(held, state):
    if held:
        return {"bull": "long_full", "neutral": "long_half"}.get(state, "none")
    return "none"


WEIGHT = {"long_full": 1.0, "long_half": 0.5, "none": 0.0}


def regime_returns(bars, reg_at):
    """Annualised average daily log total return in each regime, and the number of days."""
    sums = {1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0}
    cnts = {1: 0, 2: 0, 3: 0, 4: 0}
    for i in range(1, len(bars)):
        reg = reg_at(bars[i]["date"])
        if reg in sums and bars[i - 1]["adj"] > 0 and bars[i]["adj"] > 0:
            sums[reg] += math.log(bars[i]["adj"] / bars[i - 1]["adj"])
            cnts[reg] += 1
    return {k: (round(sums[k] / cnts[k] * 252, 4) if cnts[k] else None) for k in sums}, cnts


def master_regime_on(dates, regimes):
    def at(d):   # regime read at the close of the last master session on or before d
        k = bisect.bisect_right(dates, d) - 1
        return regimes[k] if k >= 0 else None
    return at


def backtest(bars, states, role, reg_close, tbill):
    tbk = sorted(tbill)
    eq_s, eq_b, prev_w, rows = 1.0, 1.0, 0.0, []
    s_rets, b_rets, rf_rets = [], [], []
    start = None
    for i in range(2, len(bars)):
        d0 = bars[i - 2]["date"]   # decision close
        reg = reg_close(d0)
        st = states[i - 2]
        if st is None or not reg:
            continue
        if start is None:
            start = bars[i - 1]["date"]
        w = WEIGHT[call_for(reg in ROLES[role], st)]
        r = bars[i]["adj"] / bars[i - 1]["adj"] - 1
        k = max(0, bisect.bisect_right(tbk, bars[i - 1]["date"]) - 1)
        days = (dt.date.fromisoformat(bars[i]["date"]) - dt.date.fromisoformat(bars[i - 1]["date"])).days
        rf = (1 + tbill[tbk[k]] / 100) ** (days / 365) - 1
        g = w * (r - rf) + rf - abs(w - prev_w) * COST_BP / 1e4
        prev_w = w
        eq_s *= 1 + g
        eq_b *= 1 + r
        s_rets.append(g); b_rets.append(r); rf_rets.append(rf)
        rows.append((bars[i]["date"], eq_s, eq_b))
    if not rows:
        return None
    yrs = (dt.date.fromisoformat(rows[-1][0]) - dt.date.fromisoformat(start)).days / 365.25

    def stats(rets, curve_idx):
        eq = [1.0] + [r[curve_idx] for r in rows]
        peak, mdd = 1.0, 0.0
        for v in eq:
            peak = max(peak, v); mdd = min(mdd, v / peak - 1)
        ex = [a - b for a, b in zip(rets, rf_rets)]
        mu = sum(ex) / len(ex)
        sd = (sum((x - mu) ** 2 for x in ex) / (len(ex) - 1)) ** 0.5
        ppy = len(rets) / yrs
        return dict(cagr=round(eq[-1] ** (1 / yrs) - 1, 4), maxdd=round(mdd, 4),
                    vol=round(sd * ppy ** 0.5, 4), sharpe=round(mu / sd * ppy ** 0.5, 2) if sd else None)
    # month-end curve for the chart (keeps the db doc small)
    curve, last_wk = [], None
    for d, s, b in rows:
        wk = d[:7]
        if wk != last_wk:
            curve.append([d, round(s, 4), round(b, 4)])
            last_wk = wk
        else:
            curve[-1] = [d, round(s, 4), round(b, 4)]
    return dict(start=start, end=rows[-1][0], years=round(yrs, 1),
                strat=stats(s_rets, 1), bh=stats(b_rets, 2), curve=curve)


def roro(out):
    dates, reg = out["dates"], out["regime_history"]
    g, r = out["goldilocks_pct"], out["reflation_pct"]
    n = len(dates)
    bars = []
    for label, back in (("3 months ago", 63), ("1 month ago", 21), ("1 week ago", 5), ("Latest", 0)):
        k = n - 1 - back
        on = (g[k] or 0) + (r[k] or 0)
        bars.append(dict(label=label, date=dates[k], on=round(on, 1), off=round(100 - on, 1)))
    # risk-on/off spells, dropping flips that reverse within 15 sessions
    side = [None if x in (None, 0) else (x in (1, 2)) for x in reg]
    spells = []
    for i, s in enumerate(side):
        if s is None:
            continue
        if spells and spells[-1][0] == s:
            spells[-1][2] = i
        else:
            spells.append([s, i, i])
    merged = []
    for sp in spells:
        if merged and (sp[2] - sp[1] + 1) < 15:
            merged[-1][2] = sp[2]          # too short: fold into the previous spell
        elif merged and merged[-1][0] == sp[0]:
            merged[-1][2] = sp[2]
        else:
            merged.append(list(sp))
    switches = [dates[sp[1]] for sp in merged[1:]]
    last = dt.date.fromisoformat(dates[-1])
    per_year = []
    for yrs in (1, 3, 5, 10, 20):
        since = (last - dt.timedelta(days=round(365.25 * yrs))).isoformat()
        if since < dates[next(i for i, s in enumerate(side) if s is not None)]:
            continue
        per_year.append(dict(years=yrs, per_year=round(sum(1 for d in switches if d > since) / yrs, 1)))
    cur = merged[-1]
    return dict(bars=bars, per_year=per_year, current=dict(risk_on=cur[0], since=dates[cur[1]],
                                                            sessions=cur[2] - cur[1] + 1))


def run_fit(kw_list):
    with open(os.path.join(HERE, "research", "private", "trade_fit_ref.json")) as f:
        ref = json.load(f)
    d = ref["date"]
    for kw in kw_list:
        hit = tot = 0
        miss = []
        conf = {}
        for t, lab in ref["labels"].items():
            try:
                bars = [b for b in fetch_bars(t) if b["date"] <= d]
            except Exception as e:   # noqa: BLE001
                print("skip", t, e); continue
            st = trend_states(bars, **kw)[-1]
            tot += 1
            conf[(lab, st)] = conf.get((lab, st), 0) + 1
            if st == lab:
                hit += 1
            else:
                miss.append("%s %s/%s" % (t, lab, st))
        print(kw, "match %d/%d = %.0f%%" % (hit, tot, 100 * hit / tot))
        print("   ref->ours:", {"%s->%s" % k: v for k, v in sorted(conf.items(), key=str)})
        print("   misses:", ", ".join(miss))


def main():
    if "--fit" in sys.argv:
        run_fit([dict(), dict(fast_input=20, slow_input=50), dict(fast_input=50, slow_input=100),
                 dict(cross_margin=0.15), dict(cross_margin=0.5), dict(signal_type="Both")])
        return
    with open(os.path.join(HERE, "output.json")) as f:
        out = json.load(f)
    if out.get("errors"):
        print("output.json has failed signals; regime history may be off:", list(out["errors"]), file=sys.stderr)
    mdates, mreg = out["dates"], out["regime_history"]
    reg_ret_at = regime_lookup(mdates, mreg)
    reg_close = master_regime_on(mdates, mreg)
    regime = mreg[-1]
    tbill = {r["date"]: r["close"] for r in fetch_fred("DTB3")}

    groups, errors, cache = [], {}, {}
    for gname, items in UNIVERSE:
        rows = []
        for t, label, cls in items:
            try:
                bars = fetch_bars(t)
                if len(bars) < 80:
                    raise ValueError("only %d bars" % len(bars))
            except Exception as e:   # noqa: BLE001 - one bad ticker shouldn't sink the table
                errors[t] = str(e)
                print("  ERROR", t, e, file=sys.stderr)
                continue
            states = trend_states(bars)
            r = rsi([b["close"] for b in bars])
            ann, cnts = regime_returns(bars, reg_ret_at)
            role = role_of(t, cls)
            cache[t] = (bars, states, role)
            reg_now, reg_prev = reg_close(bars[-1]["date"]), reg_close(bars[-2]["date"])
            held = reg_now in ROLES[role]
            rows.append(dict(t=t.replace("-USD", ""), name=label, cls=cls, role=role, date=bars[-1]["date"],
                             px=round(bars[-1]["close"], 2 if bars[-1]["close"] < 1000 else 0),
                             chg1d=round(bars[-1]["close"] / bars[-2]["close"] - 1, 4),
                             state=states[-1], prev_state=states[-2], rsi=round(r[-1]) if r[-1] else None,
                             held=held, call=call_for(held, states[-1]),
                             prev_call=call_for(reg_prev in ROLES[role], states[-2]),
                             reg_ret={str(k): ann[k] for k in ann}, reg_days={str(k): cnts[k] for k in cnts},
                             since=bars[0]["date"]))
        groups.append(dict(name=gname, rows=rows))

    bt = []
    for sym, name in BACKTEST_ASSETS:
        if sym not in cache:
            continue
        bars, states, role = cache[sym]
        res = backtest(bars, states, role, reg_close, tbill)
        if res:
            res.update(sym=sym.replace("-USD", ""), name=name, role=role,
                       held_in=[REG_NAMES[k] for k in ROLES[role]])
            bt.append(res)

    allrows = [r for g in groups for r in g["rows"]]
    counts = {}
    for r in allrows:
        counts[r["state"]] = counts.get(r["state"], 0) + 1
    doc = dict(asof=mdates[-1], regime=regime, regime_name=REG_NAMES.get(regime),
               params=dict(cost_bp=COST_BP), roles={k: dict(label=ROLE_LABEL[k], held_in=list(v)) for k, v in ROLES.items()},
               counts=counts, groups=groups, roro=roro(out), backtest=bt, errors=errors,
               complete=not errors and not out.get("errors"))
    with open(os.path.join(HERE, "trades_doc.json"), "w") as f:
        json.dump(doc, f, separators=(",", ":"))
    print("Wrote trades_doc.json: %d ETFs, regime %s, %s, errors %s" % (
        len(allrows), REG_NAMES.get(regime), counts, list(errors)), file=sys.stderr)
    for b in bt:
        print("  %-4s %s-%s  calls CAGR %5.1f%% DD %5.1f%% SR %s | B&H CAGR %5.1f%% DD %5.1f%% SR %s" % (
            b["sym"], b["start"][:4], b["end"][:4], 100 * b["strat"]["cagr"], 100 * b["strat"]["maxdd"],
            b["strat"]["sharpe"], 100 * b["bh"]["cagr"], 100 * b["bh"]["maxdd"], b["bh"]["sharpe"]), file=sys.stderr)


if __name__ == "__main__":
    main()
