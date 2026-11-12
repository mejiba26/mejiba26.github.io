# CLAUDE.md

Project guidance for Claude Code working in this repository.

## What this is

`mejiba26.github.io` is Brian Mejia-Lopez's public portfolio site, hosted on
GitHub Pages at https://mejiba26.github.io. It is read by recruiters and
hiring managers. Treat reliability and visual polish as high priority: a
broken chart or a stale number on this site is worse than not shipping the
feature at all.

Brian is an MSBA candidate at Wake Forest (May 2027) with a Finance and
Economics background, targeting markets analyst, trading and equity research
roles. He has traded equity index futures independently since 2020.

## Stack and conventions

- Plain HTML and CSS. **No framework, no build step, no bundler.** Do not
  introduce React, Tailwind, npm, or a static site generator.
- `index.html` is the landing page and holds every data card. Long write ups
  get their own page under `memos/` (see Site structure below).
- `style.css` holds all styling for every page, including the memo pages and
  the print rules. Do not add a second stylesheet. Match the existing visual
  language rather than inventing a new one.
- Python lives in `scripts/`. Generated data lives in `data/`.
- Generated data files **are committed** to the repo. That is intentional:
  the commit history is the time series.

## Site structure

One landing page plus a separate page for each long write up. Nothing else.

`index.html`, in order:

1. Masthead and a bar organized by **type of work**: About, Memos, Models,
   Live Desk, Methods, Contact. The paper trading and volatility links sit
   in the same bar and stay hidden until their data exists.
2. Credentials first, and always on screen: the kicker, the headline, the
   headshot, the credentials grid (which carries the visible contact
   address), and a purpose and disclaimer line.
3. `#about` — the front door, and the first view the site lands on. Holds
   `#intro` (a written introduction; the 60 second video is kept as a
   commented out block inside it, ready to switch back on), `#bridge` and
   `#resume`.
4. `#memos` — memo cards. Each links out to its own page.
5. `#work` — Models. Keeps the old `#work` id so existing links resolve.
   Holds `#ecg`, `#portfolio` and `#futures`.
6. `#market` — Live Desk. Keeps the old `#market` id. Holds the desk index,
   every data card, the two locked simulations, and the bias statements.
7. `#methods` — numbered items, each linking to the exact file in this repo,
   plus the clone and run block.
8. `#notes` — Market Notes, still hidden until the first post.
9. `#contact`.

The Bridge is one row per job: the role, what it drilled, and the thing on
this site that shows it, with a link. Keep it that way. It is the only part
of the page that could drift into unevidenced claims about Brian, so every
row has to point at something a reader can open. The copy in `#about` is
drawn from `resume.pdf` and from claims already made elsewhere on the site;
hold any new sentence to that same standard.

`memos/rtx.html` is the first memo page and the pattern for the rest: a note
header with a rating style panel, ruled tables sourced to the paper, a
revision log, the case against, and a "what would change my mind" line. Memo
pages link `../style.css` and print to a clean PDF without further work.

## Views, not a long scroll

Sections 3 to 9 above are **views**: each carries `class="view"` and only one
is on screen at a time. The bar at the top switches between them, so the site
reads as a page rather than one long scroll. The credentials block above them
never moves.

How it works, and what to keep working:

- The script at the top of the block at the bottom of `index.html` adds
  `.js-views` to `<html>`, and only then does the stylesheet hide a view.
  **This is deliberate progressive enhancement.** With JavaScript off the bar
  is a set of plain anchor links and every section is on screen, so no
  content is ever unreachable. Do not move the hiding rule out from under
  `.js-views`.
- Any in page link opens whichever view holds its target. That is what keeps
  `#paper-trading`, `#vol-scorecard`, `#rtx` and the rest working.
- After a switch the script fires a `resize` event. The two chart scripts
  already listen for one, and a chart first drawn while its view was hidden
  has zero width, so **this is the only thing redrawing it at the real
  width.** If you change the view script, keep that event.
- Before printing, the script opens every view and every `<details>`, and
  puts them back afterwards. A print rule cannot reach inside a closed
  `<details>`, so the JavaScript has to do it.

Long reference prose inside the two locked sections is folded into
`<details class="paper-block paper-fold">` so the Live Desk opens short.
**Never fold anything that carries an "as of" date, a disclaimer or a live
figure.** Those stay in the open.

Nothing fades or slides in on scroll. `.reveal` and the observer that sets
`.is-visible` are still in `index.html`, because one of the card scripts adds
`.is-visible` itself and refers to it in a comment, but the stylesheet
neutralises both. Content appears at once.

Rules that go with this structure:

- **Never remove or rename an element id.** `#rtx`, `#ecg`, `#portfolio`,
  `#futures`, `#work`, `#market`, `#intro`, `#bridge`, `#resume`, `#contact`
  and `#notes` are all linked from somewhere. The data card ids are load
  bearing for the scripts at the bottom of `index.html`.
- **No empty sections.** A section ships when it has real content in it. No
  Pitch Decks section until a real deck exists.
- **Every project carries a stats panel and a "what would change my mind"
  line**, and both use only claims already made elsewhere on the site.
- **No invented facts, figures or credentials.** Anything that cannot be
  supported from existing site copy or a committed file is a clearly marked
  placeholder, never filler.
- **No hyphens or dashes in new site copy.** Existing copy and proper names
  keep theirs.

## Visual language

A cream research document, not a dashboard. Navy masthead over `--paper`,
hairline rules, square corners, no shadows and no gradients. Fraunces for
display, IBM Plex Sans for body, IBM Plex Mono for every number, label and
piece of furniture. Tabular figures are on globally. Teal reads positive,
brick reads negative, brass marks structure and section numbers. The palette
tokens live at the top of `style.css`; use them rather than literal colors.

Two things in the stylesheet are load bearing:

- `[hidden] { display: none !important; }` near the top. Every data card
  ships with the `hidden` attribute, and without this guard a later
  `display: grid` would reveal a card that has no data.
- The classes the inline card scripts emit (`.sector-*`, `.spot-*`,
  `.energy-*`, `.paper-*`, `.vol-*`, `.up`, `.down`, and the line colors
  `.c-safe` through `.c-eqw` and `.v-naive` through `.v-har`) are a contract
  with those scripts. Renaming one in the stylesheet breaks a card silently.

## Do not break the dev sample generators

`scripts/dev/make_paper_sample.py` and `scripts/dev/make_vol_sample.py` each
copy `index.html` and swap two exact strings, and they exit if a string is
not found exactly once. Keep these four verbatim and unique:

```
const now = new Date();
const BASE = 'data/paper/';
const vnow = new Date();
const VBASE = 'data/vol/';
```

They also insert a banner after a literal `<body>` tag and copy only
`index.html` and `style.css`, which is the other reason all styling stays in
one file.

## The data pattern (follow this)

The existing sector rotation card is the reference implementation:

1. `scripts/update_sectors.py` fetches from an external source (yfinance).
2. A GitHub Actions workflow in `.github/workflows/` runs it on a schedule
   (weekdays after the close) and commits the result.
3. It writes `data/sectors.json`.
4. `index.html` fetches that JSON on page load and renders the card.

**Any new data feature should mirror this exact shape.** Read
`scripts/update_sectors.py` and the existing workflow before writing a new
one, and copy their structure, error handling and commit step.

## Failure behavior

- **In the workflow:** fail loudly. A broken fetch should fail the Actions
  run so it shows up in the Actions tab.
- **On the page:** degrade quietly. If a JSON file is missing, malformed, or
  its `as_of` date is stale, hide the card rather than rendering an empty
  chart, a zero, or a JS error. Every data-driven card must carry a visible
  "as of" date.

## Secrets

- API keys go in GitHub Actions repository secrets and are read via
  `os.environ`. Never hardcode a key, never commit one, never write one into
  a data file or a log line.
- The EIA API key is stored as the Actions secret `EIA_API_KEY`.

## Data sources

- **EIA Open Data API** (https://www.eia.gov/opendata/) is the primary
  energy data source. It is stable and has a real API.
- Do not add web scrapers to this site without discussing it first. Scraped
  sources (for example the Baker Hughes rig count) break without warning and
  this is a portfolio site.
- **Never guess an API series ID.** Look it up in the EIA API browser and
  verify the response is non-empty before wiring it into the page. A wrong
  series ID commonly returns an empty result rather than an error.

## Energy market card (live)

The energy card in the Market View section is finished and follows the
pattern above:

- `scripts/update_eia.py` → `data/energy.json`, with a five year history
  cache in `data/eia_history.json` for the percentile ranks.
- `.github/workflows/update-energy.yml` runs Wednesdays at 17:00 UTC, after
  the EIA Weekly Petroleum Status Report (released ~10:30am ET). It commits
  the data first, then runs `scripts/draft_note.py`, which scaffolds a weekly
  note in `_notes/` as a separate commit. `_notes/` is not published by Pages.

The card shows commercial crude stocks excluding SPR (versus the five-year
band), Cushing stocks, US field production, distillate stocks and days of
cover, the diesel crack (ULSD versus WTI), and the Brent–WTI spread. It
starts hidden and is revealed only when the data is well formed and no more
than 16 days old.

Prices come from EIA as well, so the whole card has a single upstream
dependency.

## Paper trading (LOCKED)

Simulated swing trading of aerospace and defense and oil names. It is a simulation: no real money, no broker connection, nothing here is investment advice.

Files:
- docs/paper-trading-spec.md is the pre registered rule set, locked in commit e22e6cb with START_DATE 2026-11-16.
- scripts/paper_trade.py is the simulator. update-paper.yml runs it weekdays.
- data/paper/ holds ledger.csv, equity.json and state.json.

Rules that must not be broken:
- Never change a rule value in the body of the spec. Corrections go in a dated amendment at the bottom, as a separate commit, and the locked text stays as it was.
- Constants in scripts/paper_trade.py must match the spec. START_DATE must match.
- Never edit, delete or rewrite anything in data/paper/ by hand. It is append only. No backfill of dates before the lock.
- Never change rules mid test. A rule change is a new version with a new start date and a new spec.
- If a new entry definition or variant is tested, add it to the trial log in the spec through an amendment. Report signal counts, not performance, when choosing between variants.
- Replays are for debugging only. They write to a scratch folder, are labeled TEST REPLAY, NOT A TRACK RECORD, and are never shown on the site as results.
- Any machine learning challenger must be tested walk forward and out of sample against the baseline, and results are published whether or not it wins. No LLM makes trade decisions in version 1.

Site section (when built): follow the failure behavior above. Hide it if data is missing, malformed or stale. Show the START_DATE, an as of date, the simulation disclaimer, planned risk next to realized worst loss, and link the ledger and the locked spec commit.

## Volatility scorecard (LOCKED)

A forward tested forecast evaluation of the next trading day's log range, ln(High / Low), for SPY, QQQ, ITA and XLE. It makes no trading claims and nothing here is investment advice.

Files:
- docs/vol-scorecard-spec.md is the pre registered design, locked by the commit that merged it into main, with START_DATE 2026-11-23.
- scripts/update_vol.py makes and grades the forecasts. update-vol.yml runs it weekdays, then scripts/check_vol_schema.py.
- data/vol/ holds forecasts.csv, realized.csv, scores.json and state.json.

Rules that must not be broken:
- Never change a model, parameter, score or test in the body of the spec. Corrections go in a dated amendment at the bottom, as a separate commit, and the locked text stays as it was.
- Constants in scripts/update_vol.py must match the spec. START_DATE must match in the spec, the script and index.html.
- Never edit, delete or rewrite anything in data/vol/ by hand. forecasts.csv and realized.csv are append only. No backfill of target days before START_DATE.
- Never change models mid test. A change is a new version with a new start date and a new spec.
- Record every model or parameter variant tried in the trial log through an amendment, with the count. Never choose parameters by comparing performance on historical replays.
- Any replay is test only. It writes to a scratch folder, is labeled TEST REPLAY, NOT A TRACK RECORD, and is never shown on the site as results.
- A forecast generated after the target day's open is late. It stays in the file and is never scored.
- No ranking claims before 60 graded days per ticker. The HAR test runs once at 250 graded days and is published whichever way it goes.

Site section: follow the failure behavior above. Hide it if data is missing, malformed, stale or today is before START_DATE. Show START_DATE, an as of date and "Forecast evaluation only, not trading advice". Keep the keys the page reads in step with scripts/check_vol_schema.py.

Tests: python scripts/dev/test_update_vol.py (offline, synthetic prices). Run it, and its --mutants mode, after any change to scripts/update_vol.py.

## Market View refresh (live)

Nothing in the Market View section should be hand dated. Three automated pieces:

- Fed policy item: `scripts/update_market.py` (stdlib only, FRED series DFEDTARU and DFEDTARL) writes `data/market.json`. `update-market.yml` runs Thursdays. The page hides the item if the file is missing, malformed or older than 14 days.
- Monthly spotlight: `scripts/update_spotlight.py` writes `data/spotlight.json`; `update-spotlight.yml` runs weekdays. The previous full calendar month is analyzed. The spotlight stock (from the large cap list) and spotlight market (from the 11 sector ETFs) are the names with the largest ABSOLUTE price move versus SPY, ties to the alphabetically first ticker. It is a rule about what moved most, never a recommendation. The page hides the card if the file is missing, malformed or older than 10 days.
- External factors are live driver numbers (oil, 10 year yield, dollar, VIX, copper) next to the evergreen `EXPOSURES` sentences in `scripts/update_spotlight.py`. Those sentences say what a group is sensitive to, never which way it will move. No LLM writes page text and nothing is scraped.

Tests (offline, no network): `python scripts/dev/test_update_market.py` and `python scripts/dev/test_update_spotlight.py`. Run them after any change to those scripts. Change the pick rule or universe only on purpose and say so in the commit.

## Verify, don't assume

Before reporting a data task complete: run the script, confirm the JSON is
written with real non-empty values, and confirm the page renders it. Do not
mark something working on the strength of the code looking correct.

Before reporting a layout or markup change complete: render the page in a
headless browser twice, once with sample data for every card and once with
`data/` absent, and confirm each card shows correctly, hides quietly and
logs no JS errors. **Open the Live Desk view first**, or every card will
report as hidden simply because its view is not the one on screen. Click
through every view, and check that the equity chart redraws at full width
once the Live Desk is open rather than staying at its 320px fallback. Check
desktop and a 390px phone width, and check that the page does not scroll
sideways. `scripts/dev/make_paper_sample.py` and
`scripts/dev/make_vol_sample.py` generate the sample data for the two locked
sections, including the stale, malformed and missing cases.
