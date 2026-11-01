"""Do the barometer's slow macro pillars lead the Risk Gauge into risk-off?

Macro composite = mean of the barometer's Economic Surprise & Sentiment, Liquidity, Rates and FX
pillars (Gold left out: structural trend bias; market pillars left out: they overlap the gauge).
Variant "no NFCI" drops the NFCI 4w change from Liquidity, since FRED's NFCI history is revised.
Weekly, 2013-06..2026-10, from the cached raw data (run risk_barometer.py --refresh first).
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import risk_barometer as rb  # noqa: E402
import risk_gauge as rg  # noqa: E402

MACRO = ["Economic Surprise & Sentiment", "Liquidity", "Rates", "Foreign Exchange"]
OFF, H = -0.15, (4, 8, 13)

dates, s = rb.weekly_panel(rb.load_raw())
pil, _, _ = rb.compute(dates, s)
nonfci = [(n, [("netliq", "chg", 4, +1)] if n == "Liquidity" else ins) for n, ins in rb.PILLARS]
pil2, _, _ = rb.compute(dates, s, pillars=nonfci)
gd = rg.gauge_doc(rg.load_raw())
gmap = dict(zip(gd["dates"], gd["index"]))
G = [gmap.get(d) for d in dates]
SPX = s["spx"]

def mean(cols):
    return [None if None in v else sum(v) / len(v) for v in zip(*cols)]

M = {"macro composite": mean([pil[n] for n in MACRO]),
     "macro, no NFCI": mean([pil2[n] for n in MACRO])}
for n in MACRO:
    M["  " + n] = pil[n]
first = next(i for i in range(len(dates)) if G[i] is not None and all(v[i] is not None for v in M.values()))
N = len(dates)

def rank(a):
    o = sorted(range(len(a)), key=a.__getitem__); r = [0] * len(a)
    for k, i in enumerate(o): r[i] = k
    return r
def spear(a, b):
    p = [(x, y) for x, y in zip(a, b) if x is not None and y is not None]
    xs, ys = rank([x for x, _ in p]), rank([y for _, y in p]); m = (len(p) - 1) / 2
    return sum((x - m) * (y - m) for x, y in zip(xs, ys)) / math.sqrt(sum((x - m) ** 2 for x in xs) * sum((y - m) ** 2 for y in ys))
def resid(y, x):  # y minus its OLS fit on x
    p = [(a, b) for a, b in zip(x, y) if a is not None and b is not None]
    mx = sum(a for a, _ in p) / len(p); my = sum(b for _, b in p) / len(p)
    beta = sum((a - mx) * (b - my) for a, b in p) / sum((a - mx) ** 2 for a, _ in p)
    return [None if a is None or b is None else b - my - beta * (a - mx) for a, b in zip(x, y)]

def fwd(i, h):
    """Over the next h weeks: did the gauge reach risk off, worst S&P drawdown from t, S&P return."""
    gs = [G[k] for k in range(i + 1, i + h + 1)]
    return (min(gs) < OFF, min(SPX[k] / SPX[i] - 1 for k in range(i + 1, i + h + 1)), SPX[i + h] / SPX[i] - 1)

def episodes(idx):
    return sum(1 for k, i in enumerate(idx) if k == 0 or idx[k - 1] != i - 1)

def table(lo, hi, label):
    print("\n=== %s: %s .. %s ===" % (label, dates[lo], dates[hi - 1]))
    print("1) Does macro_t predict the gauge's change over the next h weeks, after controlling for gauge_t?")
    print("   (Spearman of macro with the residual change; positive = weak macro precedes a falling gauge)")
    for name, m in M.items():
        out = []
        for h in H:
            dg = [None if i + h >= hi or G[i + h] is None or G[i] is None else G[i + h] - G[i] for i in range(N)]
            r = resid(dg[lo:hi], G[lo:hi])
            out.append("%2dw %+.2f" % (h, spear(m[lo:hi], r)))
        print("   %-34s %s" % (name, "   ".join(out)))
    print("2) Calm weeks only (gauge not risk off). Macro weak (< -0.33) vs the rest:")
    print("   %-34s %-6s %9s %5s | " % ("", "", "weeks", "eps") + " | ".join("%2dw: P(gauge off) P(S&P dd<-5%%) avg ret" % h for h in H))
    for name, m in M.items():
        for tag, test in (("weak", lambda v: v < -1 / 3), ("rest", lambda v: v >= -1 / 3)):
            idx = [i for i in range(lo, hi - max(H)) if G[i] >= OFF and test(m[i])]
            if not idx:
                continue
            cells = []
            for h in H:
                f = [fwd(i, h) for i in idx]
                cells.append("%3.0f%%  %3.0f%%  %+5.1f%%" % (100 * sum(x[0] for x in f) / len(f),
                             100 * sum(x[1] < -0.05 for x in f) / len(f), 100 * sum(x[2] for x in f) / len(f)))
            print("   %-34s %-6s %9d %5d | %s" % (name if tag == "weak" else "", tag, len(idx), episodes(idx), "  |  ".join(cells)))

table(first, N, "Full sample")
mid = dates.index(next(d for d in dates if d >= "2020-01-03"))
table(first, mid, "First half")
table(mid, N, "Second half")

print("\n=== Episodes: runs of calm weeks with the macro composite < -0.33 (gaps of <= 2 weeks merged) ===")
for name in ("macro composite", "macro, no NFCI"):
    m = M[name]
    idx = [i for i in range(first, N) if G[i] >= OFF and m[i] < -1 / 3]
    eps, cur = [], [idx[0]]
    for i in idx[1:]:
        if i - cur[-1] <= 3:
            cur.append(i)
        else:
            eps.append(cur); cur = [i]
    eps.append(cur)
    hits = 0
    print("\n%s: %d episodes" % (name, len(eps)))
    for e in eps:
        i = e[0]
        nxt = next((k for k in range(i + 1, min(N, i + 14)) if G[k] is not None and G[k] < OFF), None)
        dd = min(SPX[k] / SPX[i] - 1 for k in range(i + 1, min(N, i + 14))) if i + 1 < N else None
        hits += nxt is not None
        print("  %s .. %s (%2d wk)  macro %+.2f  gauge %+.2f  -> gauge off %s  S&P worst 13w %s" % (
            dates[e[0]], dates[e[-1]], len(e), m[i], G[i],
            ("in %2d wk (%s)" % (nxt - i, dates[nxt])) if nxt else "no              ",
            "%+.1f%%" % (100 * dd) if dd is not None else "-"))
    print("  -> %d of %d episodes reached gauge risk off within 13 weeks" % (hits, len(eps)))

# base rate: any calm week, random start
calm = [i for i in range(first, N - 13) if G[i] >= OFF]
base = sum(any(G[k] < OFF for k in range(i + 1, i + 14)) for i in calm) / len(calm)
print("\nBase rate: from any calm week, gauge reaches risk off within 13 weeks %.0f%% of the time" % (100 * base))
