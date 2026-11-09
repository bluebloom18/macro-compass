# Big Picture (study 04): work in progress, 9 Nov 2026

Done: big_picture.py (29 markets, S/M/L health, 6 regimes, stability, forward-return table,
weekly breadth, 44 CFTC CoT rows) -> bigpic_doc.json (~137 KB). Runs in ~11 s.

To do:
1. Splice css.txt before `</style>` and html.txt before `<footer>` in template.html. Add a nav link
   `04 Big picture` (#study-bigpic) and change the header to "Four studies".
2. Write the JS: `let BIGPIC = __BIGPIC_JSON__;` plus renderBigPic(): tiles, a weather-map SVG
   (x=M, y=S; zones per regime_of in big_picture.py; bubble r by L; colours --r0..--r5; hover
   trail of 10 sessions from history), the date slider over history.dates, a sortable table
   (default grouped by region), the breadth chart with presets, the forward table, and the CoT
   table (group filter, 1Y/3Y/5Y cells, 26-week sparkline). Add db.doc('regime/bigpic') onSnapshot.
3. render.py: replace __BIGPIC_JSON__ with bigpic_doc.json. Add bigpic_doc.json to .gitignore.
4. Screenshot, then republish macro_compass.html (see the memory's republish mechanics).
5. Daily routine prompt: run big_picture.py and write regime/bigpic (pass if_version).
