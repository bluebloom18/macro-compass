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

# Monthly-downsampled full history (last trading day on/before each month-end)
monthly = {"dates": [], "g": [], "r": [], "i": [], "de": [], "regime_code": []}
seen_months = set()
for idx in range(n - 1, -1, -1):
    ym = dates[idx][:7]
    if ym in seen_months:
        continue
    seen_months.add(ym)
    monthly["dates"].append(dates[idx])
    monthly["g"].append(d["goldilocks_pct"][idx])
    monthly["r"].append(d["reflation_pct"][idx])
    monthly["i"].append(d["inflation_pct"][idx])
    monthly["de"].append(d["deflation_pct"][idx])
    monthly["regime_code"].append(d["regime_history"][idx])
for k in monthly:
    monthly[k].reverse()

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
    json.dump(dict(latest=latest, monthly=monthly, recent=recent), f)

# Split into the two documents the artifact's DB expects (collection "regime",
# doc ids "latest" and "history") so the caller can write_db these files as-is.
with open("latest_doc.json", "w") as f:
    json.dump(latest, f)
with open("history_doc.json", "w") as f:
    json.dump(dict(monthly=monthly, recent=recent), f)

print("Wrote latest_doc.json and history_doc.json for collection 'regime'.")
print("latest:", json.dumps(latest, indent=2)[:2000])
print("\nmonthly points:", len(monthly["dates"]))
print("recent points:", len(recent["dates"]))
