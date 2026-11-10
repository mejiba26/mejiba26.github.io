# Paper Trading Spec, Version 1 (LOCKED 2026-11-09)

Status: locked on 2026-11-09. Every rule value in this file was confirmed by Brian at the lock. This file is not edited again except through a dated amendment at the bottom. The commit that locked it ("Lock paper trading spec v1") is the pre registration: the rules were fixed before any trade was made.

START_DATE: 2026-11-16 (a Monday). It is also the `START_DATE` constant in `scripts/paper_trade.py`, which refuses to write live files if it is unset and refuses a start date more than five days in the past.

This is a simulation. No real money, no broker connection, and nothing here is investment advice.

## Purpose

Test whether a simple, documented swing trading rule set can produce a credible signal in two linked areas, aerospace and defense and oil, and show the results honestly, including losses and periods where it trails a plain buy and hold. A machine learning challenger comes later (see Phase 2) and only earns a place if it beats the baseline out of sample.

## Style: swing trading

Decisions are made once a day, after the close, using daily bars only. Positions are held for days to a few weeks. There is no intraday trading. Trades are capped so the portfolio cannot drift into overtrading.

## Universe

Aerospace and defense: RTX, LMT, NOC, GD, and ITA (iShares U.S. Aerospace and Defense ETF).
Oil: XLE (energy sector ETF), USO (WTI futures ETF, note it carries roll cost), XOM.
Cash earns 0 percent in the simulation (conservative).

Nothing outside this list is traded in Version 1. Other sectors come only after this section works.

## Swing rules

After each close, for each name not already held:

Entry, all four must be true:
1. Uptrend: close above both its 50 day and 200 day average.
2. Pullback: close at least 1.5 times the 14 day ATR below the highest High of the last 10 trading days, including today. (Set by Brian. RSI is not used for entry; see the trial log for why the original RSI rule was dropped.)
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

## Position sizing and risk tiers

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

ITA buy and hold, SPY buy and hold, and an equal weight of the full universe (no rebalancing). All three are bought at the open of the first trading day on or after START_DATE, with the same 10 basis point cost as the strategy, and are measured as total return (see Implementation rules). They do not wait for the strategy's first trade.

## Logging and audit

- data/paper/ledger.csv: one row per simulated trade (date, portfolio, ticker, side, price, shares, cost, reason such as entry, stop, target, trend break, time stop). It is append only. Dividends are also logged here as rows with side `DIV` (price is the dividend per share, cost is 0, reason is `dividend`), so every cash movement is auditable. Trade statistics ignore `DIV` rows.
- data/paper/equity.json: daily portfolio value for each portfolio and each benchmark, with an as_of date. Append only.
- data/paper/state.json: the simulator's working state (cash, open positions, pending orders, benchmark values, last processed date). It is what makes a run resume exactly where the last one stopped and makes a rerun on the same day change nothing.
- Prices are raw (not dividend adjusted) closes and opens from Yahoo Finance via yfinance. Only days after the last processed date are ever added, so later restatements by Yahoo cannot rewrite recorded trades.
- A GitHub Actions workflow runs weekdays after the close, marks to market, checks exits, looks for entries, and commits the result. The commit history is the time stamped record.
- Failure behavior follows the rest of the site: the workflow fails loudly, the page hides the section quietly if data is missing or stale.

## What the page must show

Start date, as of date, equity curves against benchmarks, drawdown, win rate, average win and loss, trade count and holding period, the full trade log, and the rules above in plain language. It must also show periods where the strategy loses or lags.

## Known limits

- Buying pullbacks has its own failure mode: a pullback can be the start of a bigger decline. The confirmation rule and the stop reduce this but do not remove it.
- The universe is small and swing trades are infrequent, so it will take months to collect enough trades to judge anything. Early results will be noisy.
- Fills at the open ignore slippage on large orders, and USO ignores roll yield effects beyond what its price already reflects.
- Stops are checked on the close and filled at the next open, so a gap can lose more than the planned risk. In test replays under the discarded RSI rule, the worst realised loss ran up to about 25 percent above the planned risk per trade.
- Entries sit close to the 50 day average, so the trend break exit can trigger before the 2 ATR stop. In those test replays the stop almost never fired. This was not re-measured under the final entry rule.
- Paper results do not predict real results.

## Phase 2 (after Version 1 runs cleanly)

Machine learning challenger that predicts the forward return of a swing setup from momentum, volatility, oil and energy changes, and rates. Evaluated walk forward and out of sample against this baseline. Published whether or not it wins.

## Implementation rules (details the rules above leave open)

These are fixed by the simulator and are part of the pre registration.

- Indicators: RSI is not used for entry. The 14 day ATR uses Wilder smoothing on true range (the larger of high minus low, high minus prior close, prior close minus low). The 50 and 200 day averages are simple averages of raw closes.
- Prices: raw opens, highs, lows and closes (split adjusted, not dividend adjusted).
- Order timing: signals are computed on the close; entries and exits fill at the next trading day's open.
- Ranking: when more names qualify than there are slots, they are ranked by 12 month momentum (close divided by the close 252 trading days earlier, minus 1), highest first, with the ticker name breaking ties. Capacity limits are applied in that order at the fill.
- Sizing: uses portfolio equity marked at the prior close. Shares are rounded down to whole shares. If the max weight, max total invested or available cash would be breached, the size is reduced rather than the trade skipped. A trade that rounds to less than one share is skipped.
- Weekly entry cap: counts fills per ISO calendar week, by fill date.
- Stop and target: measured from the raw open fill price (cost excluded), using the 14 day ATR as of the signal day.
- Exit priority: if more than one exit fires on the same close, the order in the exit list above is the priority (stop, target, trend break, time stop).
- Time stop: the fill day is day 0; the exit is signalled at the close of day 20 and filled at the next open.
- Cost: 10 basis points of trade value on every fill, buy or sell, recorded in dollars in the ledger.
- Dividends: credited as cash on the ex date for shares held at the prior close, including shares sold at that day's open. Cash itself earns nothing.
- Benchmark total return: each day's value changes by (close plus dividend per share) divided by the prior close, which reinvests the dividend at that close.
- Partial bars: a bar for the current day is never recorded before the close (the live run drops it). Missing price data for a processed day fails the run and leaves the files untouched.
- Backfill: the live run refuses a START_DATE more than five days in the past, so no trade can be recorded for a period before the rules were locked.

## Falsification criteria

Set by Brian at the lock. Not edited after the lock except through a dated amendment.

- No judgment on any tier until there are at least 30 closed trades. Count each signal once, since the three tiers take mostly the same trades.
- Flag a tier as failed if its peak to trough drawdown exceeds 6 percent (Safe), 12 percent (Medium), or 20 percent (High).
- Flag the strategy as failed if, after 30 closed trades, profit factor after costs is below 1.0 and the High tier's return since START_DATE trails ITA buy and hold.
- If a tier or the strategy is flagged, publish a post mortem on the page. Do not change rules mid test. Any rule change is a new version with a new start date.

## Trial log

Every entry definition tried before the one in this spec. All use the replay window 2021-01-04 to 2026-11-06 and the 8 ticker universe above. Run dates are 2026-11-09. Performance is excluded here on purpose; only signal counts are reported.

The disclosure that matters: the first two items below were run as full replays and their equity and win rate outputs were shown to Brian. The seven variants in item 3 were evaluated on signal counts only, and no performance numbers were produced for them. The choice among the seven variants was made on signal counts, plus Brian's judgment that 1.0 ATR is a shallow pullback and 2.0 ATR is too rare.

1. Original rule: 14 day RSI below 40, close above the 50 and 200 day averages, close above yesterday's high. Result: 0 signal days. Not a bug: while a ticker closed above its 50 day average, its lowest 14 day RSI over the window was between 41.0 and 42.5, so RSI below 40 and close above the 50 day average cannot occur together here.
2. Test replays with the RSI threshold raised, run to exercise the simulator logic (test only, not candidate rules): 14 day RSI below 45 gave 1 trade per tier; 14 day RSI below 50 gave 50 trades per tier. Both were checked trade by trade against an independent calculation.
3. Signal count study (trend and confirmation kept; only the pullback measure varied):

| Pullback measure | Signal days | Per month (70.0 months) |
|---|---|---|
| 5 day RSI below 20 | 0 | 0.00 |
| 5 day RSI below 25 | 0 | 0.00 |
| 5 day RSI below 30 | 0 | 0.00 |
| 5 day RSI below 35 | 2 | 0.03 |
| Close at least 1.0 ATR below 10 day high | 390 | 5.57 |
| Close at least 1.5 ATR below 10 day high | 198 | 2.83 |
| Close at least 2.0 ATR below 10 day high | 95 | 1.36 |

   With no pullback filter at all, trend plus confirmation gives 2,195 signal days. Selected: 1.5 ATR below the 10 day high (highest High including today).

## Amendments

Pre lock edits (2026-11-09, before START_DATE was set, made directly to v1): the entry pullback rule was replaced (RSI removed, 1.5 ATR below the 10 day high added); the benchmark start was moved from the first fill to START_DATE; the DIV ledger rows, `state.json`, whole share rounding, the implementation rules, the trial log and the falsification section were added. Nothing after the lock goes here except dated amendments.

### Amendment 1 (2026-11-09, before START_DATE)

This amendment supersedes the statements named below. The locked body above is unchanged. No rule value changes.

1. **Sizing** (supersedes the Sizing bullet in Implementation rules). Equity used for sizing is marked at the prior close, adjusted for any exits filled at that same open. This matches `scripts/paper_trade.py`.
2. **Known limits, gap bullet** (supersedes it). Under the final entry rule, in a replay of 2021-01-04 to 2026-11-06, the worst realized single trade losses were 1.40 percent (Safe), 2.83 percent (Medium) and 4.72 percent (High) of equity. That is 2.4 to 2.8 times planned risk. The losses came from gaps through the stop and from trend break exits, which have no loss floor.
3. **Known limits, stop bullet** (supersedes it). Under the final rule the stop fired 9 times per tier in that replay, out of 114 to 120 exits per tier.
4. **Trial log disclosure** (adds to it). Before the lock, exit reason counts and worst single trade losses under the final rule were seen. No returns, equity curves or win rates under the final rule were seen.
5. **Falsification definitions** (adds to Falsification criteria; the criteria themselves are unchanged):
   - A closed trade is a ticker and entry fill date, counted as closed once every tier that entered it has exited.
   - Profit factor is computed on the High tier as gross winning trade profit divided by gross losing trade loss, after costs, excluding `DIV` rows.
   - Return versus ITA is total return including dividends: High tier equity against the ITA benchmark series, on the same as of date.

### Amendment 2 (2026-11-10, before START_DATE)

This amendment adds a safety guard. The locked body and Amendment 1 are unchanged. No rule value changes.

1. **Split guard** (adds to Implementation rules). Before processing any day, `scripts/paper_trade.py` checks Yahoo Finance's stock split data for every ticker in the universe and for SPY, on every day it is about to process. If any split ratio is nonzero, the run stops with a message naming the ticker, the date and the ratio, and writes nothing. The guard was added before START_DATE, in the commit titled "Halt paper trading on any stock split (guard only)".
2. **Why.** Yahoo restates past prices after a split, but the simulator's stored shares, entry prices and stops stay as they were. Continuing would record a stop or a loss that never happened, in an append only ledger.
3. **A split halts the run.** Every scheduled run after a split fails the same way, loudly in the Actions tab, until Brian decides how to handle it in a new dated amendment. The simulator does not adjust positions, stops, benchmarks or ledger rows on its own. Nothing is recorded for the halted days in the meantime.
4. **Scope.** The guard applies to unheld tickers and to SPY, not only to held positions, and it also applies to test replays. It checks only days not yet processed, so a split before the last processed date does not halt a later run.
5. **Disclosure.** Two splits already exist in the data, both before START_DATE and so outside the processed days: XLE 2 for 1 on 2025-12-05, and ITA 3 for 1 on 2026-11-05. Neither triggers the guard.
