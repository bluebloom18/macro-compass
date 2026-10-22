"""Detect alert-worthy changes between the live dashboard's previous docs and tonight's.

Compares the macro regime (latest_doc.json vs the previous regime/latest) and the three NFCI
switching rules (nfci/nfci_doc.json vs the previous regime/nfci), using the same rule
definitions as the dashboard's Financial conditions tiles:
  * 52-week MA      - risk on when NFCI is at/below its 52-week average
  * 1-week change   - risk on when this week's (first-print) change is <= 0
  * 1-week > 0.02   - flips only on a weekly move beyond +-0.02, otherwise keeps its position

Also flags an incomplete refresh: any regime signal that failed to fetch (the routine then keeps
yesterday's regime docs live), or a Risk Barometer / NFCI doc that wasn't produced tonight.

Writes alerts.json: {"changes": [...], "subject": ..., "body": ...}; "changes" is empty when
nothing flipped. Exit code is always 0 so the refresh never blocks on this step.

Usage: python3 alerts.py --prev-dir <dir holding the previous latest.json and nfci.json>
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DASHBOARD = "https://claude.ai/artifact/UEmVNVipYMysgeGjCV8DPm"
BAND = 0.02


def load(path):
    if not path or not os.path.exists(path):
        return None
    doc = json.load(open(path))
    return doc.get("data", doc) if isinstance(doc, dict) else None   # db reads may wrap fields


def nfci_rules(doc):
    """Rule states (True = risk on) at the doc's latest week, mirroring nfDerive() in template.html."""
    v, dates = doc["v"], doc["dates"]
    fp = dict(zip(doc["fp"]["dates"], doc["fp"]["ch"]))
    band, ma_on, roc_on = True, None, None
    for i in range(len(v)):
        chg = fp.get(dates[i], v[i] - v[i - 1] if i else None)
        if chg is not None:
            roc_on = chg <= 0
            if chg < -BAND:
                band = True
            elif chg > BAND:
                band = False
        if i >= 51:
            ma_on = v[i] <= sum(v[i - 51:i + 1]) / 52
    return {"52-week MA": ma_on, "1-week change": roc_on, "1-week change > 0.02": band}, chg


def main():
    prev_dir = sys.argv[sys.argv.index("--prev-dir") + 1] if "--prev-dir" in sys.argv else None
    changes, notes = [], []

    new_latest = load(os.path.join(HERE, "latest_doc.json"))
    old_latest = load(os.path.join(prev_dir, "latest.json")) if prev_dir else None
    failed = sorted((new_latest or {}).get("errors") or {})
    if failed:
        changes.append("Refresh incomplete: %d of %d regime signals failed to fetch (%s); the dashboard keeps "
                       "the previous reading (as of %s)" % (
                           len(failed), new_latest.get("n_signals_total", 29), ", ".join(failed),
                           (old_latest or {}).get("asof", "unknown")))
    elif new_latest and old_latest:
        a, b = old_latest.get("regime_name"), new_latest.get("regime_name")
        if a and b and a != b:
            pct = ", ".join("%s %.0f%%" % (k.title(), new_latest.get(k + "_pct", 0))
                            for k in ("goldilocks", "reflation", "inflation", "deflation"))
            changes.append("Macro regime: %s -> %s (as of %s; votes %s)" % (a, b, new_latest.get("asof"), pct))
    else:
        notes.append("regime: previous or new doc missing, not compared")

    if not os.path.exists(os.path.join(HERE, "risk_doc.json")):
        changes.append("Refresh incomplete: Risk Barometer not refreshed (risk_doc.json missing)")
    new_nf = load(os.path.join(HERE, "nfci", "nfci_doc.json"))
    if not new_nf:
        changes.append("Refresh incomplete: NFCI not refreshed (nfci_doc.json missing)")
    old_nf = load(os.path.join(prev_dir, "nfci.json")) if prev_dir else None
    if new_nf and old_nf and new_nf.get("fp") and old_nf.get("fp"):
        old_r, _ = nfci_rules(old_nf)
        new_r, chg = nfci_rules(new_nf)
        for rule, on in new_r.items():
            if old_r.get(rule) is not None and on is not None and on != old_r[rule]:
                changes.append("NFCI %s rule: %s -> %s (week ending %s, NFCI %.3f, 1w change %+.3f)" % (
                    rule, "risk on" if old_r[rule] else "risk off", "risk on" if on else "risk off",
                    new_nf["asof"], new_nf["v"][-1], chg))
    else:
        notes.append("nfci: previous or new doc missing, not compared")

    if changes:
        heads = []
        for c in changes:
            h = "Refresh incomplete" if c.startswith("Refresh incomplete") else c.split(" (")[0]
            if h not in heads:
                heads.append(h)
        subject = "Macro Compass alert: " + "; ".join(heads)
        body = "\n".join(["Changes in tonight's Macro Compass refresh:", ""] + ["- " + c for c in changes]
                         + ["", "Dashboard: " + DASHBOARD])
    else:
        subject = body = None
    out = dict(changes=changes, subject=subject, body=body, notes=notes)
    json.dump(out, open(os.path.join(HERE, "alerts.json"), "w"), indent=1)
    print("%d change(s)%s" % (len(changes), "".join("\n  " + c for c in changes)))
    for n in notes:
        print("  note: " + n)


if __name__ == "__main__":
    main()
