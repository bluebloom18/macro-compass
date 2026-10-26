# Weekly macro debate: routine instructions

The "Macro Compass weekly debate" cloud routine follows this file every Monday at 07:00 Singapore
time (Sunday 23:00 UTC). That is about 45 minutes after the daily refresh has written Friday's
close into the Macro Compass database.

The routine writes one new **edition** of the debate page
<https://claude.ai/artifact/2oL3DoQuEie7vWd5WxbMfd> and emails it. The page renders whatever is
in its database (collection `debate`), so the routine never republishes the page itself.

Run every python step from the repo root with a Bash timeout of at least 600000 ms.

## 1. Tools

Load these with ToolSearch before starting: `ArtifactData`, `WebSearch`, `WebFetch`, and the Gmail
send tool (query `gmail send_message`). If ArtifactData is missing, stop and report it.

## 2. This week's readings

1. Read the Macro Compass database with ArtifactData `get`, url
   `https://claude.ai/artifact/UEmVNVipYMysgeGjCV8DPm`, collection `regime`, doc ids `latest` and
   `risk`, `out_dir` `/tmp/mc_db`. Copy `/tmp/mc_db/regime/latest.json` to `latest_doc.json` and
   `/tmp/mc_db/regime/risk.json` to `risk_doc.json` in the repo root. Check `latest.asof` and
   `risk.asof` are within the last 4 days and `latest.complete` is true. If not, run
   `python3 build.py && python3 finalize.py && python3 risk_barometer.py --refresh` instead and
   use their output, and say so in the report.
2. Run `python3 weekly_debate/debate_data.py`. It writes `weekly_debate/debate_data.json`: the
   market table, regime vote counts per axis, the five-session vote changes and the last five
   Risk Barometer weeks. Those numbers are fixed; the narrative must agree with them.

## 3. Last week's edition

ArtifactData `get` on url `https://claude.ai/artifact/2oL3DoQuEie7vWd5WxbMfd`, collection
`debate`, doc id `index`, `out_dir` `/tmp/debate_db`. Note its `version`. If it lists editions,
also `get` the newest edition (its `id` is the doc id) into the same folder. You need it for the
`last_week` review and for continuity: keep what still holds, change what the week changed, and
say plainly when the call has changed.

## 4. Research the week

Use WebSearch (`standard` mode; `extended` only when a standard result is thin or stale) and
WebFetch on the best pages. Cover:

- What moved markets in the past week: the big macro news, central banks, data releases.
- The drivers behind the regime: oil and geopolitics, yields and inflation data, the dollar,
  credit spreads, whatever the vote changes in `debate_data.json` point to.
- Earnings: the season's blended growth, beat rate and forward P/E (FactSet's weekly update).
- Sentiment and positioning: the latest AAII survey, CNN Fear & Greed, the latest BofA Global
  Fund Manager Survey (monthly; say which month), and anything solid on hedge-fund or CTA
  positioning.
- The week ahead: Fed and other central-bank meetings, key data and big earnings, with dates.

The cloud sandbox may block direct page fetches (WebFetch fails with `ENOTFOUND`, curl gets a
403). Try WebFetch once. If it fails, don't keep retrying: work from WebSearch results instead,
using `extended` mode for anything that matters. Run at least 12 searches, one topic per search,
so each number comes from a result that states it. Cite the URL of the result that gave you the
number. Take your time: a good edition takes several minutes of research, not two.

Rules: every number in the narrative comes from `debate_data.json` or from a source you list.
Check every date against its source on its own; don't let a date drift to fit the story. If two
sources disagree, use the primary one or leave the number out.

## 5. Write the narrative

Write `weekly_debate/narrative.json` following `weekly_debate/SCHEMA.md`.
`weekly_debate/narrative_example.json` is the first edition (26 Oct 2026); match its length,
structure and tone.

Tone: plain and human. Short sentences, one idea each. No stacked hedges, no analyst jargon
piles, no em-dash asides, no "not X, but Y" framing. Explain the mechanism in everyday words.

- `id` and `written`: today's date (YYYY-MM-DD, Singapore time).
- `headline`: the week's call in one line, with no colon-then-reveal.
- `last_week`: 1 to 3 sentences on how last week's bottom line played out, including where it
  was wrong. Use `null` only when there is no previous edition.
- Bear and bull: 4 to 6 points each, each with a bold claim (`title`) and the evidence (`text`).
  Give both sides their strongest case.
- `positioning`: a judgment call. Keep it consistent with the regime and the evidence. When it
  changes from last week, say why in the `why` text.
- `special_report`: leave it out unless these instructions or the user's request for this run ask
  for one (see SCHEMA.md). Never copy last week's special report forward.
- `callout`: what to watch on the Macro Compass dashboard for the turn (which regime it would
  most likely move to first, and which signals would have to flip).

## 6. Assemble

`python3 weekly_debate/assemble.py --narrative weekly_debate/narrative.json --prev-index /tmp/debate_db/debate/index.json`
(drop `--prev-index` if there was no index). If it prints `NARRATIVE INVALID`, fix the narrative
and run it again. It writes `edition_doc.json`, `index_doc.json` and `email.html` in
`weekly_debate/`.

## 7. Publish to the page database

One ArtifactData `batch` on url `https://claude.ai/artifact/2oL3DoQuEie7vWd5WxbMfd` with:

- `{"op":"set","collection":"debate","doc_id":"<id>","file_path":"<abs path to edition_doc.json>"}`.
  Add `if_version` only if that edition already exists (a re-run on the same day).
- `{"op":"set","collection":"debate","doc_id":"index","file_path":"<abs path to index_doc.json>","if_version":<index version from step 3>}`.
  Omit `if_version` only when the index did not exist.

On a version conflict, re-read the named document, update `if_version` and resend once.

## 8. Email

Send one email with the Gmail send tool: to `["lienberluen@lionglobalinvestors.com"]`, cc
`["lienbl@gmail.com"]`, subject `Weekly macro debate (<d Mon YYYY>): <headline>`, `htmlBody` = the
contents of `weekly_debate/email.html` exactly, and a short plain-text `body` with the headline,
the bottom line and the page link. If the Gmail tool is unavailable or the send fails, say so in
the report.

## 9. Finish

Commit nothing. `narrative.json`, `debate_data.json`, `edition_doc.json`, `index_doc.json`,
`email.html`, `latest_doc.json` and `risk_doc.json` are gitignored scratch files. If a stop hook
asks you to commit untracked files, delete the scratch files instead.

Report: the edition id and headline, the regime and Risk Index used, whether the database
readings were fresh or rebuilt, the number of sources, whether the database write and the email
succeeded, and anything you could not verify.

Do not change any pipeline code, the page, or this file.
