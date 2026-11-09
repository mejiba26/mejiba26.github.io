# Paper Trading Spec, Version 1 (DRAFT, not yet locked)

Status: draft for Brian's review. Every value marked PROPOSED is a placeholder until Brian confirms it. Once confirmed, this file gets committed with a start date and is not edited again except through a dated amendment at the bottom. That commit is the pre registration: the rules were fixed before any trade was made.

This is a simulation. No real money, no broker connection, and nothing here is investment advice.

## Purpose

Test whether a simple, documented swing trading rule set can produce a credible signal in two linked areas, aerospace and defense and oil, and show the results honestly, including losses and periods where it trails a plain buy and hold. A machine learning challenger comes later (see Phase 2) and only earns a place if it beats the baseline out of sample.

## Style: swing trading

Decisions are made once a day, after the close, using daily bars only. Positions are held for days to a few weeks. There is no intraday trading. Trades are capped so the portfolio cannot drift into overtrading.

## Universe (PROPOSED)

Aerospace and defense: RTX, LMT, NOC, GD, and ITA (iShares U.S. Aerospace and Defense ETF).
Oil: XLE (energy sector ETF), USO (WTI futures ETF, note it carries roll cost), XOM.
Cash earns 0 percent in the simulation (conservative).

Nothing outside this list is traded in Version 1. Other sectors come only after this section works.

## Swing rules (PROPOSED)

After each close, for each name not already held:

Entry, all four must be true:
1. Uptrend: close above both its 50 day and 200 day average.
2. Pullback: 14 day RSI below 40.
3. Confirmation: today's close is above yesterday's high (the pullback has stopped falling).
4. Capacity: the portfolio has an open slot and has not hit the weekly entry cap.

Fill: the next trading day's open, plus a 10 basis point cost (same assumption as the iShares model).

Exit, first one that happens:
1. Stop: close at or below entry minus 2 times the 14 day ATR.
2. Target: close at or above entry plus 3 times the 14 day ATR.
3. Trend break: close below the 50 day average.
4. Time stop: 20 trading days after entry.

Exits are filled at the next trading day's open, with the same cost.

Long only. No shorting, no leverage.

## Position sizing and risk tiers (PROPOSED values, Brian to set)

Size each trade by risk: dollars at risk equals the tier's risk per trade times current portfolio equity, and shares equal dollars at risk divided by the stop distance, capped at the tier's maximum weight.

| | Safe | Medium | High |
|---|---|---|---|
| Risk per trade (percent of equity) | 0.5% | 1% | 2% |
| Max weight per position | 20% | 30% | 40% |
| Max open positions | 3 | 4 | 3 |
| Max total invested | 50% | 80% | 100% |
| New entries per week (cap) | 2 | 2 | 2 |

Each portfolio starts with 100,000 hypothetical dollars.

## Benchmarks

ITA buy and hold, SPY buy and hold, and an equal weight of the full universe, all with the same start date.

## Logging and audit

- data/paper/ledger.csv: one row per simulated trade (date, portfolio, ticker, side, price, shares, cost, reason such as entry, stop, target, trend break, time stop).
- data/paper/equity.json: daily portfolio value for each portfolio and each benchmark, with an as_of date.
- A GitHub Actions workflow runs weekdays after the close, marks to market, checks exits, looks for entries, and commits the result. The commit history is the time stamped record.
- Failure behavior follows the rest of the site: the workflow fails loudly, the page hides the section quietly if data is missing or stale.

## What the page must show

Start date, as of date, equity curves against benchmarks, drawdown, win rate, average win and loss, trade count and holding period, the full trade log, and the rules above in plain language. It must also show periods where the strategy loses or lags.

## Known limits

- Buying pullbacks has its own failure mode: a pullback can be the start of a bigger decline. The confirmation rule and the stop reduce this but do not remove it.
- The universe is small and swing trades are infrequent, so it will take months to collect enough trades to judge anything. Early results will be noisy.
- Fills at the open ignore slippage on large orders, and USO ignores roll yield effects beyond what its price already reflects.
- Paper results do not predict real results.

## Phase 2 (after Version 1 runs cleanly)

Machine learning challenger that predicts the forward return of a swing setup from momentum, volatility, oil and energy changes, and rates. Evaluated walk forward and out of sample against this baseline. Published whether or not it wins.

## Amendments

None yet.
