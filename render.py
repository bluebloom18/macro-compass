"""Render template.html + artifact_seed.json into a standalone macro_compass.html
for local preview. Not needed for the live artifact refresh (that writes straight
to the artifact's database via write_db) — this is just for local dev/testing."""
import json

with open("template.html") as f:
    tpl = f.read()
with open("artifact_seed.json") as f:
    seed = f.read()

json.loads(seed)  # sanity-check it's valid JSON before embedding
out = tpl.replace("__SEED_JSON__", seed)
try:
    with open("backtest.json") as f:
        bt = f.read()
    json.loads(bt)
except FileNotFoundError:
    bt = "null"  # run backtest.py first to populate the backtest section
out = out.replace("__BACKTEST_JSON__", bt)

with open("macro_compass.html", "w") as f:
    f.write(out)

print("Wrote macro_compass.html (%d bytes)" % len(out))
