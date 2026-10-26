"""Merge the week's narrative with debate_data.json into one edition document, check it,
update the edition index and render the email.

    python3 weekly_debate/assemble.py --narrative weekly_debate/narrative.json \
        [--prev-index /tmp/debate_db/debate/index.json]

Writes (all gitignored) in weekly_debate/:
  edition_doc.json  -> db collection "debate", doc id = edition id (e.g. 2026-11-02)
  index_doc.json    -> db collection "debate", doc id "index" (newest edition first)
  email.html        -> HTML email body
Exits non-zero, writing nothing, if the narrative fails validation. See SCHEMA.md."""
import argparse
import html
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE_URL = "https://claude.ai/artifact/2oL3DoQuEie7vWd5WxbMfd"

REQUIRED = {
    "id": str, "written": str, "headline": str, "bottom_line": list, "context": str,
    "axis_notes": dict, "barometer_note": str, "bear": list, "bull": list, "sentiment": dict,
    "positioning": list, "triggers": list, "callout": str, "sources": list,
}


def validate(n):
    errs = []
    for k, t in REQUIRED.items():
        if not isinstance(n.get(k), t):
            errs.append("%s missing or not %s" % (k, t.__name__))
    if errs:
        return errs
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", n["id"]):
        errs.append("id must be YYYY-MM-DD")
    if not 3 <= len(n["bottom_line"]) <= 5 or not all(isinstance(x, str) and x for x in n["bottom_line"]):
        errs.append("bottom_line: 3-5 non-empty strings")
    for side in ("bear", "bull"):
        if not 4 <= len(n[side]) <= 6 or not all(isinstance(x, dict) and x.get("title") and x.get("text") for x in n[side]):
            errs.append("%s: 4-6 items with title and text" % side)
    for k in ("growth", "inflation"):
        if not isinstance(n["axis_notes"].get(k), str):
            errs.append("axis_notes.%s missing" % k)
    s = n["sentiment"]
    for k in ("text", "retail", "pro"):
        if k not in s:
            errs.append("sentiment.%s missing" % k)
    for k in ("retail", "pro"):
        if not isinstance(s.get(k), list) or not all(isinstance(r, dict) and "k" in r and "v" in r for r in s.get(k, [])):
            errs.append("sentiment.%s: list of {k, v}" % k)
    if not all(isinstance(x, dict) and x.get("what") and x.get("why") for x in n["positioning"]):
        errs.append("positioning: items with what and why")
    if not all(isinstance(x, dict) and x.get("title") and x.get("text") for x in n["triggers"]):
        errs.append("triggers: items with title and text")
    if not n["sources"] or not all(isinstance(x, dict) and x.get("title") and re.match(r"https?://", x.get("url", "")) for x in n["sources"]):
        errs.append("sources: non-empty list of {title, url} with http(s) urls")
    lw = n.get("last_week")
    if lw is not None and not isinstance(lw, str):
        errs.append("last_week must be a string or null")
    return errs


def fmt_level(m):
    v = m["level"]
    if m["name"].startswith(("US 10Y", "US 2Y")):
        return "%.2f%%" % v
    if "spread" in m["name"]:
        return "%dbp" % round(v * 100)
    if m["name"] in ("Brent crude", "Gold"):
        return "${:,.0f}".format(v)
    if m["name"] == "Copper":
        return "$%.2f/lb" % v
    return "{:,.0f}".format(v) if v >= 1000 else "%.2f" % v


def fmt_chg(v, kind):
    if v is None:
        return ""
    sign = "+" if v > 0 else ("&minus;" if v < 0 else "")
    return sign + ("%.1f%%" % abs(v) if kind == "pct" else "%dbp" % abs(round(v)))


def email_html(doc, editions=()):
    e = html.escape
    pos, neg = "#1F5FBF", "#C0392B"
    def col(v):
        return "" if not v else ("color:%s;" % (pos if v > 0 else neg))
    rows = "".join(
        '<tr><td style="padding:4px 8px;border-bottom:1px solid #E3E6EE">%s</td>'
        '<td style="padding:4px 8px;border-bottom:1px solid #E3E6EE;text-align:right">%s</td>'
        '<td style="padding:4px 8px;border-bottom:1px solid #E3E6EE;text-align:right;%s">%s</td>'
        '<td style="padding:4px 8px;border-bottom:1px solid #E3E6EE;text-align:right;%s">%s</td></tr>'
        % (e(m["name"]), fmt_level(m), col(m["m1"]), fmt_chg(m["m1"], m["kind"]), col(m["vs_high"]), fmt_chg(m["vs_high"], m["kind"]))
        for m in doc["markets"])
    li = lambda items: "".join("<li>%s</li>" % e(x) for x in items)
    pts = lambda items: "".join("<li><b>%s</b> %s</li>" % (e(x["title"]), e(x["text"])) for x in items)
    h2 = 'style="font-family:Georgia,serif;font-size:18px;margin:18px 0 6px"'
    r = doc["regime"]
    ax = doc["axes"]
    past = [x for x in editions if x["id"] != doc["id"]][:8]
    past_html = ('<h2 %s>Past editions</h2><ul style="margin:0;padding-left:20px">%s</ul>' % (h2, "".join(
        '<li><a href="%s#e%s">%s</a> &middot; %s</li>' % (PAGE_URL, e(x["id"]), e(x["id"]), e(x.get("headline", ""))) for x in past))) if past else ""
    last_week = ('<h2 %s>Last week\'s call</h2><p style="margin:0">%s</p>' % (h2, e(doc["last_week"]))) if doc.get("last_week") else ""
    return """<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.5;color:#1D2330;max-width:720px">
<p style="margin:0 0 6px;font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:#6F7A90">Macro Compass weekly debate &middot; readings as of {asof}</p>
<h1 style="margin:0 0 6px;font-family:Georgia,serif;font-size:24px">{headline}</h1>
<p style="margin:0 0 14px">Full page with tables: <a href="{url}#e{eid}">{url}#e{eid}</a></p>
<p style="margin:0 0 14px;padding:8px 12px;background:#EDEFF5;border-radius:6px"><b>Regime:</b> {regime} ({rpct}% of votes) &nbsp;|&nbsp; growth signals {gu} rising, {gd} falling &nbsp;|&nbsp; inflation signals {iu} rising, {idn} falling &nbsp;|&nbsp; <b>Risk Barometer:</b> {ri}</p>
<h2 {h2}>Bottom line</h2><ul style="margin:0;padding-left:20px">{bottom}</ul>
{last_week}
<h2 {h2}>Where things stand</h2><p style="margin:0 0 10px">{context}</p>
<table cellpadding="0" cellspacing="0" style="border-collapse:collapse;font-size:13px;width:100%;max-width:560px">
<tr><th style="text-align:left;padding:4px 8px;color:#6F7A90">Market</th><th style="text-align:right;padding:4px 8px;color:#6F7A90">Level</th><th style="text-align:right;padding:4px 8px;color:#6F7A90">1 month</th><th style="text-align:right;padding:4px 8px;color:#6F7A90">vs 52w high</th></tr>{rows}</table>
<h2 {h2}>Bear case</h2><ol style="margin:0;padding-left:20px">{bear}</ol>
<h2 {h2}>Bull case</h2><ol style="margin:0;padding-left:20px">{bull}</ol>
<h2 {h2}>Is sentiment too bearish?</h2><p style="margin:0">{sent}</p>
<h2 {h2}>How to be positioned</h2><ul style="margin:0;padding-left:20px">{posn}</ul>
<h2 {h2}>What could cause the turn</h2><ol style="margin:0;padding-left:20px">{trig}</ol>
<p style="margin:10px 0 0;padding:8px 12px;background:#EDEFF5;border-radius:6px">{callout}</p>
{past_html}
<p style="margin:18px 0 0;font-size:12px;color:#6F7A90">Indicator readings from Macro Compass. Sources are linked on the page. Not investment advice.</p>
</div>""".format(
        past_html=past_html, eid=e(doc["id"]), asof=e(doc["asof"]), headline=e(doc["headline"]), url=PAGE_URL, regime=e(r["name"]), rpct=r["pct"][r["code"] - 1] if r["code"] else "-",
        gu=len(ax["growth"]["up"]), gd=len(ax["growth"]["down"]), iu=len(ax["inflation"]["up"]), idn=len(ax["inflation"]["down"]),
        ri=("%+.2f" % doc["barometer"]["index"][-1]) if doc["barometer"]["index"][-1] is not None else "n/a",
        h2=h2, bottom=li(doc["bottom_line"]), last_week=last_week, context=e(doc["context"]), rows=rows,
        bear=pts(doc["bear"]), bull=pts(doc["bull"]), sent=e(doc["sentiment"]["text"]),
        posn="".join("<li><b>%s:</b> %s</li>" % (e(x["what"]), e(x["why"])) for x in doc["positioning"]),
        trig=pts(doc["triggers"]), callout=e(doc["callout"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--narrative", required=True)
    ap.add_argument("--prev-index")
    a = ap.parse_args()

    n = json.load(open(a.narrative))
    errs = validate(n)
    if errs:
        print("NARRATIVE INVALID:\n  " + "\n  ".join(errs))
        sys.exit(1)
    data = json.load(open(os.path.join(HERE, "debate_data.json")))
    doc = dict(data)
    doc.update(n)

    editions = []
    if a.prev_index and os.path.exists(a.prev_index):
        prev = json.load(open(a.prev_index))
        editions = [x for x in prev.get("editions", []) if x.get("id") != doc["id"]]
    editions.insert(0, dict(id=doc["id"], asof=doc["asof"], headline=doc["headline"], regime=doc["regime"]["name"]))
    editions.sort(key=lambda x: x["id"], reverse=True)

    with open(os.path.join(HERE, "edition_doc.json"), "w") as f:
        json.dump(doc, f)
    with open(os.path.join(HERE, "index_doc.json"), "w") as f:
        json.dump(dict(editions=editions), f)
    with open(os.path.join(HERE, "email.html"), "w") as f:
        f.write(email_html(doc, editions))
    print("OK edition %s (%d editions in index); wrote edition_doc.json, index_doc.json, email.html" % (doc["id"], len(editions)))


if __name__ == "__main__":
    main()
