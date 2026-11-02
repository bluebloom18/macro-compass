# narrative.json schema

The week's written content. `assemble.py` validates it, merges it with `debate_data.json`
(markets, axes, vote changes, barometer, regime, asof) and writes the edition document.
See `narrative_example.json` for a complete example.

| Field | Type | Notes |
|---|---|---|
| `id` | `"YYYY-MM-DD"` | Edition date (Singapore time). Also the database doc id. |
| `written` | `"YYYY-MM-DD"` | Same as `id` unless re-written later. |
| `headline` | string | The week's call in one line. |
| `bottom_line` | 3-5 strings | The summary box. |
| `last_week` | string or `null` | How last week's call played out, including misses. |
| `context` | string | One paragraph: what is driving the tape. |
| `axis_notes` | `{growth, inflation}` | One sentence each under the vote bars: which signals point which way. |
| `barometer_note` | string | What changed in the Risk Gauge and macro pressure this week, and why. The gauge is the coincident stress read; macro pressure (the barometer's economic surprise, liquidity, rates and FX pillars) is the early warning, flagged when it is below its weak line while the gauge is still calm. The page no longer shows the 8-pillar barometer, so don't cite its other pillars. |
| `bear`, `bull` | 4-6 `{title, text}` | `title` is the claim, `text` the evidence. |
| `sentiment` | object | `text` (the answer), `retail_label`, `retail` [`{k, v}`], `pro_label`, `pro` [`{k, v}`], `note` (optional). |
| `positioning` | `[{what, why}]` | Judgment call. |
| `triggers` | `[{title, text}]` | Catalysts to watch. |
| `callout` | string | What to watch on the Macro Compass dashboard. |
| `sources` | `[{title, url}]` | Every source behind a number or claim. http(s) only. |

Write negatives with a plain hyphen (`-8.9`); the page turns them into minus signs. A signed % or bp change in any text field (`+10.9%`, `-12.6%`, `-31bp`) is coloured blue or red on
the page and in the email; unsigned numbers are treated as levels and stay plain.

## Optional: `special_report`

A one-off deep dive shown below the debate (with a "Special report" chip in the header). Only
include it when the user has asked for one for that week; never carry it forward. Past ones live
in `specials/` (e.g. `specials/special_ai_2026-10-26.json`, the AI bubble-vs-shortage report).

`special_report`: `{title, summary, blocks, sources}`. `blocks` is an ordered list of:

| `type` | Fields |
|---|---|
| `h` | `text`: a sub-heading |
| `p` | `text`: a paragraph |
| `callout` | `text`: a shaded takeaway box |
| `points` | `items` [`{title, text}`], `ordered` (bool) |
| `tilts` | `items` [`{what, why}`]: positioning rows |
| `debate` | `bear` / `bull` [`{title, text}`], optional `bear_title` / `bull_title` |
| `table` | `head` [strings], `rows` [[strings]], optional `note` |
