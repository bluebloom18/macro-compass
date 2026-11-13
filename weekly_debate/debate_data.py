"""Numbers for the weekly macro debate page: the market table, regime vote counts per axis,
the last five Risk Gauge weeks (with macro pressure), the Risk Barometer weeks and the five-session vote changes.

Run from the repo root after build.py, finalize.py and risk_barometer.py (it reads their
output and reuses build.py's fetch cache). Writes weekly_debate/debate_data.json, which
assemble.py merges with the week's narrative."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from build import cached_fetch  # noqa: E402
from fetch import fetch_yahoo, fetch_fred  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# (label, cache key, fetcher, kind) - kind "pct" shows % changes, "bp" shows basis-point changes
MARKETS = [
    ("S&P 500",            "yahoo_^GSPC",        lambda: fetch_yahoo("^GSPC"),    "pct"),
    ("Russell 2000",       "yahoo_^RUT",         lambda: fetch_yahoo("^RUT"),     "pct"),
    ("STOXX 600",          "yahoo_^STOXX",       lambda: fetch_yahoo("^STOXX"),   "pct"),
    ("Nikkei 225",         "yahoo_^N225",        lambda: fetch_yahoo("^N225"),    "pct"),
    ("Hang Seng",          "yahoo_^HSI",         lambda: fetch_yahoo("^HSI"),     "pct"),
    ("Emerging markets (EEM)", "yahoo_EEM",      lambda: fetch_yahoo("EEM"),      "pct"),
    ("US dollar index",    "yahoo_DX-Y.NYB",     lambda: fetch_yahoo("DX-Y.NYB"), "pct"),
    ("Brent crude",        "yahoo_BZ=F",         lambda: fetch_yahoo("BZ=F"),     "pct"),
    ("Copper",             "yahoo_HG=F",         lambda: fetch_yahoo("HG=F"),     "pct"),
    ("Gold",               "yahoo_GC=F",         lambda: fetch_yahoo("GC=F"),     "pct"),
    ("US 10Y yield",       "yahoo_^TNX",         lambda: fetch_yahoo("^TNX"),     "bp"),
    ("US 2Y yield",        "fred_DGS2",          lambda: fetch_fred("DGS2"),      "bp"),
    ("US high-yield spread", "fred_BAMLH0A0HYM2", lambda: fetch_fred("BAMLH0A0HYM2"), "bp"),
    ("VIX",                "yahoo_^VIX",         lambda: fetch_yahoo("^VIX"),     "pct"),
]


def closes(rows):
    out = []
    for r in rows:
        v = r.get("close", r.get("value"))
        if v is not None:
            out.append((r["date"], float(v)))
    return out


def market_row(label, key, fn, kind):
    s = closes(cached_fetch(key, fn))
    date, last = s[-1]
    month_ago = s[-22][1] if len(s) > 21 else s[0][1]
    year = [v for _, v in s[-252:]]
    hi = max(year)
    if kind == "bp":
        chg = (last - month_ago) * 100
        vs_hi = (last - hi) * 100
    else:
        chg = (last / month_ago - 1) * 100
        vs_hi = (last / hi - 1) * 100
    return dict(name=label, date=date, level=last, kind=kind,
                m1=round(chg, 1 if kind == "pct" else 0), vs_high=round(vs_hi, 1 if kind == "pct" else 0))


def axis_counts(per_signal):
    ax = {a: {"up": [], "down": [], "flat": []} for a in ("growth", "inflation")}
    for s in per_signal:
        w = s.get("weights")
        if not w:
            continue
        a = "inflation" if (w[0] == w[3] and w[1] == w[2] and w[0] != w[1]) else "growth"
        if not s.get("ready") or not (s.get("bull") or s.get("bear")):
            ax[a]["flat"].append(s["label"])
            continue
        v = w if s["bull"] else [1 - x for x in w]
        up = (v[0] and v[1]) if a == "growth" else (v[1] and v[2])
        ax[a]["up" if up else "down"].append(s["label"])
    return ax


def cot_pattern(groups, g, crowd, h="w13"):
    """How this CoT group has done after extremes on the side that is crowded now (13-week edge, in
    sd of the contract's own moves): contrarian = moved against the crowd, follows = moved with it."""
    x = (groups or {}).get(g, {}).get(h, {}).get("bull" if crowd == "long" else "bear")
    if not x:
        return "none", None
    e = x["edge"] if crowd == "long" else -x["edge"]   # >0 = moved with the crowd
    return ("follows" if e >= 0.1 else "contrarian" if e <= -0.1 else "none"), x["edge"]


def bigpic_summary(path):
    """Compact Big Picture (study 04) readings for the debate page: our own regime scores for 29
    markets, US regime checks, smart money proxy and CoT extremes. None if the doc is missing."""
    if not os.path.exists(path):
        return None
    d = json.load(open(path))
    names = d["regimes"]
    ok = [m for m in d["markets"] if not m.get("error")]
    by_regime = [dict(regime=i, name=names[i], markets=[m["name"] for m in ok if m["regime"] == i]) for i in range(6)]
    moves = [dict(name=m["name"], before=names[m["regime5"]], after=names[m["regime"]],
                  side=m["side"], side_changed=(m["regime5"] <= 2) != (m["regime"] <= 2))
             for m in ok if m["regime5"] != m["regime"]]
    b = d["breadth"]
    us = None
    if d.get("us_checks"):
        us = []
        for k, r in d["us_checks"]["rows"].items():
            us.append(dict(name=r["name"], regime=names[r["regime"]], side=r["side"],
                           breadth=None if not r["breadth"] else r["breadth"]["p200"],
                           vol="%s %.1f (%sth pct)" % (r["vol"]["name"], r["vol"]["level"], r["vol"]["pct"]),
                           vol_state=r["vol"]["state"], credit20=r["credit"]["chg20"],
                           agree=r["agree"], views=r["views"],
                           disagree=[c for c, v in r["checks"].items() if v is False],
                           flags=[f for f, v in r["flags"].items() if v]))
    smi = None
    if d.get("smi") and d["smi"].get("latest"):
        L = d["smi"]["latest"]
        smi = dict(date=L["date"], state=L["state"], d20=L["d20"], dow20=L["dow20"])
    cot = None
    if d.get("cot") and d["cot"].get("rows"):
        groups = (d.get("backtest") or {}).get("cot", {}).get("groups") if d.get("backtest") else None
        ext = []
        for r in d["cot"]["rows"]:
            if r.get("error") or r.get("i3") is None or 20 < r["i3"] < 80:
                continue
            crowd = "long" if r["i3"] >= 80 else "short"
            pat, edge = cot_pattern(groups, r["group"], crowd)
            ext.append(dict(group=r["group"], name=r["name"], i3=r["i3"], chg=r["chg"], crowd=crowd, pattern=pat, edge=edge))
        cot = dict(asof=d["cot"]["asof"], extremes=sorted(ext, key=lambda x: (x["group"], x["name"])))
    return dict(asof=d["asof"], summary=d["summary"], by_regime=by_regime, moves_5d=moves,
                breadth=dict(dates=b["dates"][-6:], on=b["on"][-6:], bull=b["bull"][-6:]),
                us_checks=us, smi=smi, cot=cot)


def cycle_summary(path):
    """Compact Market cycle (study 05) readings for the debate page: the composite of leading
    indicators (OECD CLI, yield curve, credit, earnings revision breadth), its phase, the two
    checks and the S&P 500 record by phase. None if the doc is missing."""
    if not os.path.exists(path):
        return None
    d = json.load(open(path))
    L, raw, earn = d["legs"], d.get("raw") or {}, d.get("earn") or {}
    fund = (d.get("funding") or {}).get("series") or []
    bt = d.get("backtest") or {}
    return dict(
        asof=d["asof"], month=d["month"], composite=d["composite"], composite_3m_ago=d.get("composite_3m_ago"),
        phase=d["phase"],
        legs=dict(cli=L["cli"]["z"], curve=L["curve"]["z"], credit=L["credit"]["z"],
                  baa=(L.get("baa") or {}).get("z"), hy=(L.get("hy") or {}).get("z"),
                  earn=(L.get("earn") or {}).get("z")),
        earn_in_composite=bool(earn.get("in_composite")), earn_breadth=earn.get("latest"),
        earn_months=earn.get("months"), earn_universe=earn.get("universe"),
        raw=dict(cli=raw.get("cli"), cli_month=raw.get("cli_month"), curve=raw.get("curve"),
                 curve_12m_ago=raw.get("curve_12m_ago"),
                 hy_us=(raw.get("hy_us") or {}).get("level"), hy_us_chg_3m=(raw.get("hy_us") or {}).get("chg_3m")),
        sofr_iorb_bp=fund[-1][1] if fund else None,
        checks=[dict(label=c["label"], rule=c["rule"], trip=c["trip"]) for c in d.get("checks", [])],
        history=[dict(m=h["m"], c=h["c"], p=h["p"]) for h in d.get("history", [])[-6:]],
        backtest={k: dict(avg=v["h12"]["avg"], hit=v["h12"]["hit"], n=v["h12"]["n"]) for k, v in bt.items() if "h12" in v},
    )


def main():
    latest = json.load(open(os.path.join(ROOT, "latest_doc.json")))
    risk = json.load(open(os.path.join(ROOT, "risk_doc.json")))

    markets = []
    for label, key, fn, kind in MARKETS:
        try:
            markets.append(market_row(label, key, fn, kind))
        except Exception as e:  # a missing series drops its row, never the page
            print("market row failed:", label, e, file=sys.stderr)

    barometer = dict(
        dates=risk["dates"][-5:],
        index=[None if v is None else round(v, 2) for v in risk["index"][-5:]],
        pillars=[dict(name=p["name"], scores=[None if v is None else round(v, 2) for v in p["scores"][-5:]])
                 for p in risk["pillars"]],
        thresh=risk.get("thresh", 0.3333),
    )

    g = risk.get("gauge")
    gauge = dict(
        dates=g["dates"][-5:],
        index=g["index"][-5:],
        blocks=[dict(name=b["name"], scores=b["scores"][-5:]) for b in g["blocks"]],
        tiers=g["tiers"],
        # macro pressure: the barometer's slow macro pillars, an early warning under the gauge
        macro=dict(index=g["macro"]["index"][-5:], weak=g["macro"]["weak"]) if g.get("macro") else None,
    ) if g else None

    out = dict(
        asof=latest["asof"],
        complete=latest.get("complete", True),
        regime=dict(code=latest["regime_code"], name=latest["regime_name"],
                    pct=[round(latest[k], 1) for k in ("goldilocks_pct", "reflation_pct", "inflation_pct", "deflation_pct")]),
        axes=axis_counts(latest["per_signal"]),
        vote_changes_5d=latest.get("vote_changes_5d"),
        gauge=gauge,
        barometer=barometer,
        markets=markets,
        bigpic=bigpic_summary(os.path.join(ROOT, "bigpic_doc.json")),
        cycle=cycle_summary(os.path.join(ROOT, "cycle_doc.json")),
    )
    path = os.path.join(HERE, "debate_data.json")
    with open(path, "w") as f:
        json.dump(out, f, indent=1)
    print("Wrote", path)
    print("regime:", out["regime"]["name"], out["regime"]["pct"])
    for a, c in out["axes"].items():
        print(a, {k: len(v) for k, v in c.items()})
    if gauge:
        print("risk gauge:", gauge["dates"][-1], gauge["index"][-1])
        if gauge["macro"]:
            print("macro pressure:", gauge["macro"]["index"][-1])
    if out["bigpic"]:
        bp = out["bigpic"]
        print("big picture:", bp["asof"], "%d of %d risk on, %d long-term bull" % (bp["summary"]["on"], bp["summary"]["n"], bp["summary"]["bull"]))
    else:
        print("big picture: bigpic_doc.json missing, section left out")
    if out["cycle"]:
        cy = out["cycle"]
        print("market cycle:", cy["asof"], cy["phase"], cy["composite"], "(3m ago %s)" % cy["composite_3m_ago"],
              "checks tripped:", sum(c["trip"] for c in cy["checks"]))
    else:
        print("market cycle: cycle_doc.json missing, section left out")
    print("risk index:", barometer["dates"][-1], barometer["index"][-1])
    for m in markets:
        print("  %-24s %s %10.2f  1m %6s  vs hi %6s" % (m["name"], m["date"], m["level"], m["m1"], m["vs_high"]))


if __name__ == "__main__":
    main()
