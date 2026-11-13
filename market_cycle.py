"""Market cycle (study 05): a monthly nowcast of the balance between growth and financial risk.

Composite = simple average of z-scored legs, each signed so positive = supportive for equities:
  Leading indicators  OECD composite leading indicator, G20, amplitude adjusted (OECD SDMX API)
  Yield curve         12-month change in the US 10y minus 2y spread (FRED T10Y2Y, monthly mean);
                      steepening = supportive. The level is a poor cycle gauge after 2022-24's long
                      inversion; the change tracks the published composite's curve leg far better.
  Credit              12-month change in Moody's Baa minus 10y spread (FRED BAA10Y), sign flipped
                      (widening = negative), averaged, where available, with the 6-month change in the
                      US and euro high-yield spreads (FRED BAMLH0A0HYM2, BAMLHE00EHYIOAS) z-scored over
                      their own history. FRED keeps only three years of the ICE high-yield series, so
                      Baa carries the long history and high yield adds the recent signal.
  Earnings sentiment  net analyst EPS revision breadth across ~110 large caps worldwide, from
                      Yahoo's earnings-trend data. Yahoo keeps no history, so it is collected daily
                      into data/eps_breadth.csv. Shown on its own and kept out of the composite until
                      EARN_MIN_MONTHS of monthly readings exist; until EARN_Z_MONTHS it is scaled on a
                      fixed range (breadth runs -1..+1) instead of a z-score.

z-scores use an expanding window from each series' start (at least 60 months), so no reading uses
later data. Phase, from the composite's 3-month average and that average's change over 3 months:
  Expansion (>= 0, rising)  Slowdown (>= 0, falling)  Contraction (< 0, falling)  Recovery (< 0, rising)

Correction-or-crisis checks (the damaging past selloffs came with growth and credit already broken):
  1. earnings sentiment negative (breadth < 0)
  2. credit z below -1 while repo funding is tight (SOFR minus IORB above 10bp, FRED SOFR, IORB)
A nowcast, not a return forecast: the backtest table shows S&P 500 returns after each phase.

Writes cycle_doc.json (db: regime/cycle). render.py embeds it as __CYCLE_JSON__.
Run:  python3 market_cycle.py            (fetch, append today's breadth, write the doc)
      python3 market_cycle.py --no-earn  (skip the Yahoo breadth pull)
"""
import csv
import datetime as dt
import json
import math
import os
import subprocess
import sys
import time
import urllib.parse

from fetch import fetch_fred, fetch_yahoo

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "cycle_doc.json")
EPS_CSV = os.path.join(HERE, "data", "eps_breadth.csv")
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
MIN_MONTHS = 60
EARN_MIN_MONTHS = 12
EARN_Z_MONTHS = 36
OECD_CLI = ("https://sdmx.oecd.org/public/rest/data/OECD.SDD.STES,DSD_STES@DF_CLI,/G20.M.LI...AA...H"
            "?startPeriod=1980-01&dimensionAtObservation=AllDimensions&format=csvfilewithlabels")

# Large caps with broad analyst coverage: US mega and large caps across sectors, then Europe, Japan,
# Asia ex-Japan. Yahoo symbols.
UNIVERSE = (
    "AAPL MSFT NVDA AMZN GOOGL META AVGO TSLA BRK-B JPM LLY V UNH XOM MA COST HD PG JNJ WMT NFLX "
    "ABBV BAC CRM ORCL CVX KO MRK AMD PEP ADBE TMO LIN CSCO ACN MCD WFC ABT IBM GE QCOM TXN INTU "
    "CAT AMAT DHR VZ NOW PFE CMCSA UNP NEE PM SPGI RTX HON LOW GS AMGN COP BKNG ISRG MU LRCX "
    "ASML.AS SAP.DE NOVO-B.CO MC.PA NESN.SW ROG.SW AZN.L SHEL.L HSBA.L SIE.DE TTE.PA OR.PA ALV.DE "
    "SAN.MC ULVR.L RMS.PA SU.PA BNP.PA DTE.DE AIR.PA "
    "7203.T 6758.T 8306.T 6861.T 9984.T 8035.T 6501.T 9432.T 8058.T 4063.T "
    "2330.TW 2317.TW 2454.TW 005930.KS 000660.KS 0700.HK 9988.HK 1299.HK 3690.HK 0939.HK "
    "RELIANCE.NS HDFCBANK.NS INFY.NS TCS.NS D05.SI BHP.AX CBA.AX"
).split()


# ---------- data ----------

def monthly(rows, how="mean"):
    """{YYYY-MM: value} from daily rows, mean (or last) per calendar month."""
    acc = {}
    for r in rows:
        acc.setdefault(r["date"][:7], []).append(r["close"])
    return {m: (sum(v) / len(v) if how == "mean" else v[-1]) for m, v in acc.items()}


def fetch_cli():
    for attempt in range(3):
        p = subprocess.run(["curl", "-s", "-m", "90", "-A", BROWSER_UA, OECD_CLI], capture_output=True)
        text = p.stdout.decode("utf-8", "replace")
        if text.startswith("STRUCTURE"):
            rows = list(csv.DictReader(text.splitlines()))
            out = {r["TIME_PERIOD"]: float(r["OBS_VALUE"]) for r in rows if r.get("OBS_VALUE")}
            if out:
                return out
        time.sleep(20 * (attempt + 1))
    raise RuntimeError("OECD CLI fetch failed: %r" % text[:80])


def zscores(series, sign=1, min_n=MIN_MONTHS):
    """Expanding-window z-score per month (no look-ahead). series: {month: value}."""
    out, xs = {}, []
    for m in sorted(series):
        xs.append(series[m])
        if len(xs) >= min_n:
            mu = sum(xs) / len(xs)
            sd = math.sqrt(sum((x - mu) ** 2 for x in xs) / (len(xs) - 1))
            out[m] = sign * (xs[-1] - mu) / sd if sd else 0.0
    return out


def change(series, k):
    ks = sorted(series)
    return {ks[i]: series[ks[i]] - series[ks[i - k]] for i in range(k, len(ks))}


def ffill(series, months):
    out, last = {}, None
    for m in months:
        if m in series:
            last = series[m]
        if last is not None:
            out[m] = last
    return out


# ---------- earnings revision breadth (Yahoo earnings trend) ----------

def yahoo_session():
    jar = os.path.join(HERE, "cache", "yahoo_cookies.txt")
    os.makedirs(os.path.dirname(jar), exist_ok=True)
    subprocess.run(["curl", "-s", "-m", "30", "-c", jar, "-A", BROWSER_UA, "https://fc.yahoo.com", "-o", os.devnull])
    crumb = subprocess.run(["curl", "-s", "-m", "30", "-b", jar, "-A", BROWSER_UA,
                            "https://query2.finance.yahoo.com/v1/test/getcrumb"], capture_output=True).stdout.decode().strip()
    if not crumb or "<" in crumb or len(crumb) > 40:
        raise RuntimeError("no Yahoo crumb")
    return jar, crumb


def earnings_trend(sym, jar, crumb):
    url = "https://query2.finance.yahoo.com/v10/finance/quoteSummary/%s?modules=earningsTrend&crumb=%s" % (
        urllib.parse.quote(sym, safe=""), urllib.parse.quote(crumb))
    p = subprocess.run(["curl", "-s", "-m", "30", "-b", jar, "-A", BROWSER_UA, url], capture_output=True)
    d = json.loads(p.stdout or b"{}")
    res = (d.get("quoteSummary") or {}).get("result") or []
    return {t["period"]: t for t in res[0]["earningsTrend"]["trend"]} if res else {}


def raw(t, *keys):
    for k in keys:
        t = (t or {}).get(k)
    return t.get("raw") if isinstance(t, dict) else None


def breadth_today():
    """Net revision breadth for current and next fiscal year, pooled across the universe.
    rev: (analyst raises - cuts) / (raises + cuts) over the last 30 days.
    eps: share of companies whose consensus EPS rose minus share that fell, over 30 days; the same
    from 60 to 30 and 90 to 60 days ago gives two earlier points for a first-run backfill."""
    jar, crumb = yahoo_session()
    up = down = 0
    eps = {"0-30": [0, 0, 0], "30-60": [0, 0, 0], "60-90": [0, 0, 0]}   # up, down, n
    ok = 0
    for sym in UNIVERSE:
        try:
            tr = earnings_trend(sym, jar, crumb)
        except Exception:
            tr = {}
        if not tr:
            continue
        ok += 1
        for per in ("0y", "+1y"):
            t = tr.get(per) or {}
            u, d = raw(t, "epsRevisions", "upLast30days"), raw(t, "epsRevisions", "downLast30days")
            up += u or 0
            down += d or 0
            e = [raw(t, "epsTrend", k) for k in ("current", "30daysAgo", "60daysAgo", "90daysAgo")]
            for key, (a, b) in zip(eps, ((e[0], e[1]), (e[1], e[2]), (e[2], e[3]))):
                if a is None or b is None or b == 0:
                    continue
                ch = (a - b) / abs(b)
                eps[key][2] += 1
                if ch > 0.0005:
                    eps[key][0] += 1
                elif ch < -0.0005:
                    eps[key][1] += 1
        time.sleep(0.4)
    if ok < len(UNIVERSE) // 2:
        raise RuntimeError("Yahoo earnings trend: only %d of %d names returned" % (ok, len(UNIVERSE)))
    net = lambda k: (eps[k][0] - eps[k][1]) / eps[k][2] if eps[k][2] else None
    return dict(names=ok, up=up, down=down, rev=(up - down) / (up + down) if up + down else None,
                eps=net("0-30"), eps_30_60=net("30-60"), eps_60_90=net("60-90"))


def update_eps_csv(today):
    rows = []
    if os.path.exists(EPS_CSV):
        with open(EPS_CSV) as f:
            rows = list(csv.DictReader(f))
    b = breadth_today()
    d0 = dt.date.fromisoformat(today)
    if not rows:   # first run: two backfilled points from the EPS-trend lookbacks (eps only)
        for lag, key in ((60, "eps_60_90"), (30, "eps_30_60")):
            if b[key] is not None:
                rows.append(dict(date=(d0 - dt.timedelta(days=lag)).isoformat(), names=b["names"], up="", down="",
                                 rev="", eps="%.4f" % b[key], source="backfill"))
    rows = [r for r in rows if r["date"] != today]
    rows.append(dict(date=today, names=b["names"], up=b["up"], down=b["down"],
                     rev="" if b["rev"] is None else "%.4f" % b["rev"],
                     eps="" if b["eps"] is None else "%.4f" % b["eps"], source="yahoo"))
    rows.sort(key=lambda r: r["date"])
    os.makedirs(os.path.dirname(EPS_CSV), exist_ok=True)
    with open(EPS_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "names", "up", "down", "rev", "eps", "source"])
        w.writeheader()
        w.writerows(rows)
    return rows


def load_eps():
    if not os.path.exists(EPS_CSV):
        return []
    with open(EPS_CSV) as f:
        return list(csv.DictReader(f))


# ---------- build ----------

def phase(z, z3):
    if z is None or z3 is None:
        return None
    if z >= 0:
        return "Expansion" if z >= z3 else "Slowdown"
    return "Recovery" if z >= z3 else "Contraction"


def fwd_stats(months, ph, spx):
    """S&P 500 price return 3, 6 and 12 months after each month-end, grouped by phase."""
    keys = sorted(spx)
    idx = {m: i for i, m in enumerate(keys)}
    out = {}
    for name in ("Expansion", "Slowdown", "Contraction", "Recovery"):
        res = {}
        for h in (3, 6, 12):
            rs = [spx[keys[idx[m] + h]] / spx[m] - 1 for m in months
                  if ph.get(m) == name and m in idx and idx[m] + h < len(keys)]
            res["h%d" % h] = dict(avg=round(100 * sum(rs) / len(rs), 1) if rs else None,
                                  hit=round(100 * sum(r > 0 for r in rs) / len(rs)) if rs else None, n=len(rs))
        res["months"] = sum(1 for m in months if ph.get(m) == name)
        out[name] = res
    allr = [spx[keys[idx[m] + 12]] / spx[m] - 1 for m in months if m in idx and idx[m] + 12 < len(keys)]
    out["All"] = dict(h12=dict(avg=round(100 * sum(allr) / len(allr), 1), hit=round(100 * sum(r > 0 for r in allr) / len(allr)), n=len(allr)))
    return out


def main():
    today = dt.date.today().isoformat()
    warnings = []
    if "--no-earn" not in sys.argv:
        try:
            update_eps_csv(today)
        except Exception as e:
            warnings.append("earnings breadth not updated: %s" % e)
    cli = fetch_cli()
    curve = monthly(fetch_fred("T10Y2Y"))
    baa = monthly(fetch_fred("BAA10Y"))
    spx = monthly(fetch_yahoo("^GSPC", range_="40y", interval="1d"), how="last")   # range "max" comes back quarterly

    cur_m = today[:7]
    months = sorted(m for m in set(cli) | set(curve) | set(baa) if m <= cur_m)
    hy_us, hy_eu = fetch_fred("BAMLH0A0HYM2"), fetch_fred("BAMLHE00EHYIOAS")
    hy = [zscores(change(monthly(r), 6), sign=-1, min_n=24) for r in (hy_us, hy_eu)]
    zb = zscores(change(baa, 12), sign=-1)
    credit = {}
    for m, v in zb.items():
        h = [x[m] for x in hy if m in x]
        credit[m] = (v + sum(h) / len(h)) / 2 if h else v
    z = {"cli": zscores(ffill({k: v - 100 for k, v in cli.items()}, months)),
         "curve": zscores(change(curve, 12)), "credit": credit, "baa": zb,
         "hy": {m: sum(x[m] for x in hy if m in x) / sum(m in x for x in hy) for m in set(hy[0]) | set(hy[1])}}

    eps_rows = load_eps()
    em = {}
    for r in eps_rows:
        v = r.get("rev") or r.get("eps")
        if v:
            em.setdefault(r["date"][:7], []).append(float(v))
    earn_m = {m: sum(v) / len(v) for m, v in em.items()}
    earn_in = len(earn_m) >= EARN_MIN_MONTHS
    z["earn"] = zscores(earn_m, min_n=EARN_Z_MONTHS) if len(earn_m) >= EARN_Z_MONTHS else {m: 2 * v for m, v in earn_m.items()}

    legs = ["cli", "curve", "credit"] + (["earn"] if earn_in else [])
    comp = {}
    for m in months:
        vs = [z[k].get(m) for k in legs]
        if all(v is not None for v in vs[:3]):
            comp[m] = sum(v for v in vs if v is not None) / sum(v is not None for v in vs)
    cm = sorted(comp)
    sm = {m: sum(comp[x] for x in cm[max(0, i - 2):i + 1]) / len(cm[max(0, i - 2):i + 1]) for i, m in enumerate(cm)}
    ph = {m: phase(sm[m], sm[cm[i - 3]] if i >= 3 else None) for i, m in enumerate(cm)}   # 3-month average, so the phase doesn't flicker around zero

    # current readings
    last = cm[-1]
    sofr, iorb = fetch_fred("SOFR"), fetch_fred("IORB")
    ior = {r["date"]: r["close"] for r in iorb}
    fund = [(r["date"], round(100 * (r["close"] - ior[r["date"]]), 1)) for r in sofr if r["date"] in ior][-130:]
    def chg(rows, days):
        d = dt.date.fromisoformat(rows[-1]["date"]) - dt.timedelta(days=days)
        past = [r for r in rows if r["date"] <= d.isoformat()]
        return round(rows[-1]["close"] - past[-1]["close"], 2) if past else None
    earn_now = next((float(r.get("rev") or r.get("eps")) for r in reversed(eps_rows) if (r.get("rev") or r.get("eps"))), None)
    cz = z["credit"].get(last)
    checks = [
        dict(key="earn", label="Earnings sentiment negative", value=None if earn_now is None else round(earn_now, 2),
             trip=None if earn_now is None else earn_now < 0, rule="net revision breadth below 0"),
        dict(key="credit_funding", label="Credit below -1 sd with tight funding",
             value=dict(credit_z=None if cz is None else round(cz, 2), sofr_iorb_bp=fund[-1][1] if fund else None),
             trip=None if cz is None or not fund else (cz < -1 and fund[-1][1] > 10),
             rule="credit z below -1 and SOFR - IORB above 10bp"),
    ]

    hist = [dict(m=m, c=round(comp[m], 3), p=ph[m], **{k: (round(z[k][m], 3) if m in z[k] else None) for k in ("cli", "curve", "credit", "earn", "baa", "hy")})
            for m in cm if m >= "1995-01"]
    doc = dict(
        asof=today, month=last, composite=round(comp[last], 3), phase=ph[last],
        composite_3m_ago=round(comp[cm[-4]], 3) if len(cm) >= 4 else None,
        legs={k: dict(z=None if z[k].get(last) is None else round(z[k][last], 3),
                      z_3m=None if z[k].get(cm[-4]) is None else round(z[k][cm[-4]], 3)) for k in ("cli", "curve", "credit", "earn", "baa", "hy")},
        raw=dict(cli=round(ffill(cli, months)[last], 2), cli_month=max(cli), curve=round(curve.get(last, float("nan")), 2),
                 curve_12m_ago=round(curve[sorted(curve)[-13]], 2),
                 baa10y=round(baa.get(last, float("nan")), 2),
                 hy_us=dict(level=hy_us[-1]["close"], date=hy_us[-1]["date"], chg_3m=chg(hy_us, 91)),
                 hy_eu=dict(level=hy_eu[-1]["close"], date=hy_eu[-1]["date"], chg_3m=chg(hy_eu, 91))),
        earn=dict(in_composite=earn_in, months=len(earn_m), scaled="z" if len(earn_m) >= EARN_Z_MONTHS else "fixed",
                  latest=earn_now, universe=len(UNIVERSE),
                  daily=[dict(d=r["date"], rev=float(r["rev"]) if r.get("rev") else None, eps=float(r["eps"]) if r.get("eps") else None,
                              src=r.get("source")) for r in eps_rows[-400:]]),
        funding=dict(series=fund, latest=fund[-1] if fund else None),
        checks=checks, history=hist,
        backtest=fwd_stats([m for m in cm if m >= "1991-01"], ph, spx),
        warnings=warnings,
    )
    with open(OUT, "w") as f:
        json.dump(doc, f, separators=(",", ":"))
    print("cycle: %s composite %.2f (%s); legs %s; earnings breadth %s; funding %s; %s" % (
        last, comp[last], ph[last], {k: doc["legs"][k]["z"] for k in doc["legs"]}, earn_now,
        fund[-1] if fund else None, "; ".join(warnings) or "ok"))


if __name__ == "__main__":
    main()
