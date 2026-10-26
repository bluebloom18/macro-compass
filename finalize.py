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

# Day-over-day vote changes over the last five sessions (newest first): which signals changed
# state, the regime votes each one gained or lost, and the vote shares before and after.
def votes_for(state, weights):
    if state == "bull":
        v = weights
    elif state == "bear":
        v = [1 - x for x in weights]
    else:
        return []
    return [k + 1 for k in range(4) if v[k] == 1]

vote_changes = []
recent_states = d.get("signal_states_recent")
if recent_states:
    rdates = recent_states["dates"]
    off = n - len(rdates)
    pct_keys = ("goldilocks_pct", "reflation_pct", "inflation_pct", "deflation_pct")
    for j in range(len(rdates) - 1, 0, -1):
        changes, net = [], {1: 0, 2: 0, 3: 0, 4: 0}
        for key, states in recent_states["states"].items():
            before, after = states[j - 1], states[j]
            if before == after:
                continue
            w = SIGNALS[key]["weights"]
            old, new = votes_for(before, w), votes_for(after, w)
            gained = [k for k in new if k not in old]
            lost = [k for k in old if k not in new]
            for k in gained:
                net[k] += 1
            for k in lost:
                net[k] -= 1
            changes.append(dict(key=key, label=SIGNALS[key]["label"], weights=list(w),
                                before=before, after=after, gained=gained, lost=lost))
        vote_changes.append(dict(
            date=rdates[j], prev_date=rdates[j - 1],
            shares_before=[d[k][off + j - 1] for k in pct_keys],
            shares_after=[d[k][off + j] for k in pct_keys],
            regime_before=d["regime_history"][off + j - 1],
            regime_after=d["regime_history"][off + j],
            net=[net[k] for k in (1, 2, 3, 4)],
            changes=changes,
        ))

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
    vote_changes=vote_changes,
    errors=d["errors"],
    n_signals_total=len(SIGNALS),
    n_signals_ready=sum(1 for s in d["per_signal_last"] if s.get("ready")),
    complete=not d["errors"],   # False = some signals failed to fetch; the routine then keeps yesterday's docs live
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
if d["errors"]:
    print("\nINCOMPLETE: %d signal(s) failed to fetch: %s" % (len(d["errors"]), ", ".join(sorted(d["errors"]))))
