# Volatility Forecast Scorecard Spec, Version 1 (locked on merge)

Status: locked on merge. The commit that merges this file into `main` is the pre registration: every model, parameter, score and test below was fixed before the first forecast was made. After that merge this file is not edited again except through a dated amendment at the bottom.

START_DATE: 2026-11-23 (a Monday). It is also the `START_DATE` constant in `scripts/update_vol.py` and in the scorecard script in `index.html`. The run after the close on Friday 2026-11-20 produces the first forecast, for Monday 2026-11-23.

This is a forecast evaluation. It makes no trading claims, holds no positions and nothing here is investment advice.

## Purpose

Publish timestamped forecasts of the next trading day's price range, then grade them against what actually happens. Two questions: are the forecast intervals calibrated, and does a fancier model (HAR) beat a simple one. The answer is published either way.

## Universe

SPY, QQQ, ITA, XLE. Clean, liquid ETFs. Futures are excluded because of roll gaps.

## Target

y(t) = ln(High / Low) of trading day t, the log range, from raw daily prices. It is a ratio inside one day, so it is safe against splits and dividends.

A forecast is made after the close of day t for day t+1, the next trading day.

## Models

Parameters are fixed here and are never tuned on live data.

1. Naive: yesterday's y.
2. Rolling mean: the mean of y over the last 22 trading days.
3. EWMA: an exponentially weighted average of y with lambda 0.94, seeded with the mean of the first 22 days of history.
4. HAR: y(t+1) = b0 + b1 * y(t) + b2 * mean(y over the last 5 days) + b3 * mean(y over the last 22 days). Ordinary least squares on an expanding window, refit every Monday using data through the prior close, with a minimum of 750 days of history.

## Intervals

For each model, the 10th, 50th and 90th percentiles of that model's own walk forward residuals over the last 500 trading days are added to its point forecast. That gives a median forecast and an 80% interval. A residual is the actual y of a day minus the forecast that model made for it the day before, so every residual uses only data known at forecast time.

## Scores

Per model and per ticker, over a rolling 60 graded days and since START_DATE:

- MAE of the log range (mean absolute error of the median forecast).
- Pinball loss at 0.1 and at 0.9.
- 80% interval coverage, the share of days where the actual y landed inside the interval. It should be near 80%.

## Timing rule

A forecast generated after the target day's regular open (14:30 UTC in winter, 13:30 UTC in summer) is marked late, recorded, and never scored. Scheduled runs land hours late, so `generated_utc` is recorded on every row.

## Start rule

Forecasts count only for target days on or after START_DATE. The run on Friday 2026-11-20 produces the first one. No backfill.

## Conclusions

- No ranking claims before 60 graded days per ticker.
- After 250 graded days, HAR is tested against the best simple baseline (Naive or Rolling mean, whichever has the lower MAE) with a Diebold Mariano test on absolute log errors, using a Newey West long run variance with lag 5.
- All four tickers' p values are reported. HAR "beats" the baseline on a ticker only if its MAE is lower and p < 0.0125 (Bonferroni across 4).
- The result is published either way.
- No model changes mid test. A change is a new version with a new start date and a new spec.

## Trial log

Every model or parameter variant tried during development is recorded here, with the count.

- Variants compared on historical or live performance before the lock: 0.
- The models and every parameter above were written down by Brian before any code existed. Development and testing used synthetic prices only (`scripts/dev/test_update_vol.py`). The script was never run on real market data before the lock, because the build environment could not reach Yahoo Finance.
- The choices listed under Implementation rules were made without looking at any data, real or synthetic, for their effect on scores.

Parameters are never chosen by comparing performance on historical replays. `scripts/update_vol.py` has no replay mode. If one is ever added, a replay is test only, is written to a scratch folder, is labeled TEST REPLAY, NOT A TRACK RECORD, is never shown on the site as results, and is added to this log through an amendment.

## Known limits

- The daily range is an imperfect proxy for risk. It ignores overnight gaps and direction.
- ETFs only, and a small ticker set.
- All four tickers are correlated, so they are not four independent tests.
- Past ranges mean little about regime shifts. Intervals built from the last 500 days will be too narrow right after a shock.
- Shortened holiday sessions are graded like any other day, although their ranges are usually smaller.
- Prices come from Yahoo Finance, which can be late or wrong. Each day's High and Low are recorded once, as first seen, and are not revised.

## Implementation rules (details the design above leaves open)

These are fixed by `scripts/update_vol.py` and are part of the pre registration.

- Data: raw daily High and Low from Yahoo Finance via yfinance (not adjusted). Every run downloads the full history from 2015-01-02, so the EWMA seed and the start of the HAR window never move. A run needs at least 1,300 trading days of history (750 for HAR, 500 for residuals, plus room for the weekly refit) and fails otherwise.
- Forecast of record: the median column (point forecast plus the median residual). MAE and the Diebold Mariano test use it.
- Percentiles: linear interpolation between order statistics (the numpy default).
- Floor: the median, the 10th and the 90th percentile forecasts are floored at zero, because a range cannot be negative.
- HAR refit: the coefficients used for a forecast made after the close of day t are fit on all days before the Monday of t's calendar week. The first usable regression row is day 22 of the history.
- Trading calendar: the next trading day is found from the scheduled NYSE holiday rules coded in the script. Early closes are trading days.
- Late and missed runs: every close since the last run gets its forecast computed from data through that close only. If the target day's open has already passed when the forecast is generated, the row is marked late and never scored.
- Unscheduled closures: if the exchange does not trade on a target day, the forecasts for that day are counted as void and never scored.
- Graded day: a target day on or after START_DATE where all four models have an on time forecast and the day's High and Low have been recorded.
- Recorded values: forecasts and realized log ranges are stored to 6 decimal places. Scores are computed from the stored values in `forecasts.csv` and `realized.csv` only, so anyone can reproduce `scores.json` from the two files.
- Pooled scores: "all four" pools every graded ticker day. The rolling window is each ticker's last 60 graded days.
- Diebold Mariano: the loss differential is |HAR error| minus |baseline error|. The long run variance uses Bartlett weights 1 - k/6 for lags 1 to 5. The p value is two sided from the normal distribution. The test uses each ticker's first 250 graded days and is run once, so its result does not move as more days arrive. The baseline is chosen on those same 250 days, which slightly favors the baseline.
- Partial bars: a bar is used only from 16:30 New York time on its own day. An earlier run drops it.
- Failing loudly: if any ticker is missing, any High or Low is missing or impossible, or Yahoo has no bar yet for a completed session, the run exits with an error and writes nothing.
- Backfill: a first run more than five days after START_DATE is refused.
- Append only: `forecasts.csv` and `realized.csv` only ever gain rows. The workflow's schema check fails if a committed row was changed or removed.

## Logging and audit

- `data/vol/forecasts.csv`: one row per target day, ticker and model (forecast_for_date, ticker, model, median, p10, p90, generated_utc, status). Append only. Status is `ok` or `late`.
- `data/vol/realized.csv`: the High, Low and log range each target day was graded on (date, ticker, high, low, log_range, recorded_utc). Append only.
- `data/vol/scores.json`: the scores, the latest forecast and an `as_of` date, rebuilt from the two CSV files on every run that adds rows.
- `data/vol/state.json`: where the last run stopped and the HAR coefficients in use.
- A GitHub Actions workflow runs weekdays after the close and commits the result. The commit history is the time stamped record.
- Failure behavior follows the rest of the site: the workflow fails loudly, the page hides the section quietly if data is missing, malformed or stale.

## Amendments

None. Nothing goes here except dated amendments made after the lock.
