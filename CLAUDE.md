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
- `index.html` is the whole site. Sections are anchored (`#rtx`, `#ecg`,
  `#portfolio`, `#futures`).
- `style.css` holds all styling. Match the existing visual language rather
  than inventing a new one.
- Python lives in `scripts/`. Generated data lives in `data/`.
- Generated data files **are committed** to the repo. That is intentional:
  the commit history is the time series.

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

## Current work: energy dashboard

Adding an energy market card to the Market View section, following the
pattern above:

- `scripts/update_eia.py` → `data/energy.json`
- Runs Wednesdays, after the EIA Weekly Petroleum Status Report
  (released ~10:30am ET).

Target metrics: commercial crude stocks excluding SPR (versus the five-year
band), Cushing stocks, US field production, distillate stocks and days of
cover, the diesel crack (ULSD versus WTI), and the Brent–WTI spread.

Pull prices from EIA as well, so the whole card has a single upstream
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

## Verify, don't assume

Before reporting a data task complete: run the script, confirm the JSON is
written with real non-empty values, and confirm the page renders it. Do not
mark something working on the strength of the code looking correct.
