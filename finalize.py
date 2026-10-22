import json
from config import SIGNALS

d = json.load(open("output.json"))
dates = d["dates"]
n = len(dates)
regime_code = d["regime_history"][-1]
REGIME_NAMES = {0: "None", 1: "Goldilocks", 2: "Reflation", 3: "Inflation", 4: "Deflation"}
regime_name = REGIME_NAMES[regime_code]

goldilocks_regime = regime_code == 1
reflation_regime = regime_code == 2
inflation_regime = regime_code == 3
deflation_regime = regime_code == 4

risk_on = goldilocks_regime or reflation_regime
outperform = dict(
    risk_on=risk_on,
    global_outperform=reflation_regime,
    smid_outperform=reflation_regime,
    spreads_outperform=goldilocks_regime or reflation_regime,
    short_rates_outperform=goldilocks_regime or reflation_regime or inflation_regime,
    high_yield_outperform=goldilocks_regime or reflation_regime,
    metals_outperform=goldilocks_regime or reflation_regime or deflation_regime,
    bitcoin_outperform=goldilocks_regime or reflation_regime,
    fx_outperform=goldilocks_regime or reflation_regime,
)

latest = dict(
    asof=dates[-1],
    goldilocks_pct=d["goldilocks_pct"][-1],
    reflation_pct=d["reflation_pct"][-1],
    inflation_pct=d["inflation_pct"][-1],
    deflation_pct=d["deflation_pct"][-1],
    regime_code=regime_code,
    regime_name=regime_name,
    outperform=outperform,
    per_signal=d["per_signal_last"],
    errors=d["errors"],
    n_signals_total=len(SIGNALS),
    n_signals_ready=sum(1 for s in d["per_signal_last"] if s.get("ready")),
)

# Weekly-downsampled full history (last trading day of each ISO week; the current
# week's point is the latest close, so the chart's right edge moves every day)
import datetime
weekly = {"dates": [], "g": [], "r": [], "i": [], "de": [], "regime_code": []}
seen_weeks = set()
for idx in range(n - 1, -1, -1):
    wk = datetime.date.fromisoformat(dates[idx]).isocalendar()[:2]
    if wk in seen_weeks:
        continue
    seen_weeks.add(wk)
    weekly["dates"].append(dates[idx])
    weekly["g"].append(d["goldilocks_pct"][idx])
    weekly["r"].append(d["reflation_pct"][idx])
    weekly["i"].append(d["inflation_pct"][idx])
    weekly["de"].append(d["deflation_pct"][idx])
    weekly["regime_code"].append(d["regime_history"][idx])
for k in weekly:
    weekly[k].reverse()

# Recent daily (last ~2 years)
cut = max(0, n - 504)
recent = dict(
    dates=dates[cut:],
    g=d["goldilocks_pct"][cut:],
    r=d["reflation_pct"][cut:],
    i=d["inflation_pct"][cut:],
    de=d["deflation_pct"][cut:],
    regime_code=d["regime_history"][cut:],
)

with open("artifact_seed.json", "w") as f:
    json.dump(dict(latest=latest, weekly=weekly, recent=recent), f)

# Split into the two documents the artifact's DB expects (collection "regime",
# doc ids "latest" and "history") so the caller can write_db these files as-is.
with open("latest_doc.json", "w") as f:
    json.dump(latest, f)
with open("history_doc.json", "w") as f:
    json.dump(dict(weekly=weekly, recent=recent), f)

print("Wrote latest_doc.json and history_doc.json for collection 'regime'.")
print("latest:", json.dumps(latest, indent=2)[:2000])
print("\nweekly points:", len(weekly["dates"]))
print("recent points:", len(recent["dates"]))
