# Portfolio Model Spec, Version 2 (pre registered 2026-11-23)

Status: written and committed before any v2 result existed. The commit that adds this file ("Pre register portfolio model v2 spec") is the pre registration. The body is not edited after that commit; corrections go in a dated amendment at the bottom, as a separate commit, and the original text stays.

This is a historical backtest. No real money, and nothing here is investment advice.

## The question

Does sophisticated portfolio optimization beat a plain 60/40 mix, and what happens in a crash like 2022?

v1 (in `portfolio/v1/`, published on the site) answered with point estimates only: 60/40 had the best Sharpe (0.72), Max Sharpe came closest (0.64). v2 asks two sharper questions:

1. Are any of those differences larger than the noise in 18 years of monthly data?
2. When an optimizer does badly, is it the **rule** (what it optimizes) or the **covariance estimator** (how it measures risk) that is to blame?

## Conventions inherited from v1 (read from `portfolio/v1/portfolio.py`)

These are copied exactly. v2 must reproduce v1's published numbers on v1's own price file before any v2 number is trusted (see Replication check).

- Universe: IVV, IJR, EFA, EEM, AGG, TLT, TIP, LQD, IAU. Risk free rate: ^IRX (13 week T-bill yield).
- Prices: daily adjusted closes (total return) from Yahoo Finance via yfinance, from 2005-02-01. A month that is not complete on the last price date is dropped.
- Monthly returns: last adjusted close of each calendar month, percent change. Any month with a missing ETF return is dropped.
- Risk free rate for month t: the ^IRX close at the end of month t-1, divided by 100 and by 12 (simple, no discount basis conversion). Known at the start of the month.
- Walk forward: weights for month t use only the 36 monthly returns t-36 to t-1, and earn month t's return. First out of sample month: March 2008.
- Rebalance: monthly, back to target, for every strategy including 60/40.
- Costs: 10 basis points times turnover, where turnover is the sum of absolute differences between the new target and the drifted weights (prior target grown by month t-1's returns). The portfolio starts in cash, so month one pays for a full buy. Annual turnover excludes that first buy. Drift ignores the cost deduction (as v1).
- 60/40: 60% IVV, 40% AGG.
- Optimizer: SciPy SLSQP, start at equal weights, weights sum to 1, long only, ftol 1e-10, maxiter 1000. Result clipped at 0 and renormalized. Non-convergence warns and is counted; it does not stop the run (as v1).
- Max Sharpe: maximize (w·μ) / sqrt(w'Σw), cap 40% per ETF. μ = plain mean of the 36 monthly excess returns (return minus risk free). Σ is estimated from raw (not excess) returns. **μ is identical in every v2 variant; only Σ changes.**
- Min Variance: minimize w'Σw (Σ annualized, in squared percent, as v1), no cap.
- Metrics: CAGR; volatility = monthly std × √12; Sharpe = mean monthly excess return / its std × √12; max drawdown; worst month; stress period total returns.
- Stress periods (v1 windows, kept as is): 2008 Financial Crisis = Jun 2008 to Feb 2009; COVID Crash = Feb 2020 to Mar 2020; 2022 Rate Shock = Jan 2022 to Dec 2022. Note that "2008" on the site means this June to February window, not the calendar year.

## Data for v2

Same source and conventions, extended to the latest complete month on the run date (for the first locked run on 2026-11-23: October 2026, so March 2008 to October 2026). Saved to `data/portfolio/prices.csv` and committed. The script fails, and writes nothing, if any ticker is missing, any price is missing, non positive or non finite inside the used window, any single month return of an ETF is beyond ±60%, or ^IRX is missing for more than one month end in a row.

## Strategies (13)

### Naive (3, no covariance matrix)

1. **60/40**: as above.
2. **Equal Weight**: 1/9 each.
3. **Inverse Vol**: weight proportional to 1 / the sample standard deviation of the 36 monthly returns (as v1).

### Trend rule (1)

4. **Trend 10M**: at the end of month t-1, each ETF whose adjusted month end price is above the simple average of its last 10 month end prices (t-10 to t-1 inclusive) is held at 1/9 of the portfolio. Each ETF below or equal to its average has its 1/9 slot in cash. **Cash proxy**: cash earns the same monthly risk free rate as above (^IRX at the end of month t-1, /100/12). Cash is not an ETF, so moving between an ETF and cash costs 10 bps on the ETF leg only.

### Covariance study (9): 3 rules × 3 estimators

Rules:
- **MV**: Min Variance (as v1).
- **MS**: Max Sharpe with the 40% cap (as v1, same μ).
- **RP**: Equal risk contribution (risk parity): long only, no cap, each ETF contributes the same share of portfolio variance, w_i (Σw)_i equal for all i. Solved as the unique minimizer of ½ w'Σw − (1/9) Σ ln w_i (SciPy L-BFGS-B, w > 0), then rescaled to sum to 1. A month whose solution has risk contributions differing by more than 0.1% (relative) is counted as non-converged.

Estimators, all fitted on the same 36 monthly raw returns:
- **E1 Sample**: the plain sample covariance (pandas `.cov()`, divides by n−1), as v1.
- **E2 Ledoit-Wolf**: shrinkage toward a scaled identity matrix, scikit-learn `LedoitWolf()` with its defaults (data centered by the sample mean, shrinkage intensity estimated each month).
- **E3 EWMA**: exponentially weighted covariance inside the same 36 month window, half life 12 months. Weight for the observation k months before the newest (k = 0 to 35) is 0.5^(k/12), normalized to sum to 1; Σ = Σ_k w_k (r_k − m)(r_k − m)' with m the same weighted mean. (No bias correction; a common scale factor does not change any of the three rules' weights.)

Names: `MV-Sample`, `MV-LW`, `MV-EWMA`, `MS-Sample`, `MS-LW`, `MS-EWMA`, `RP-Sample`, `RP-LW`, `RP-EWMA`. `MV-Sample` and `MS-Sample` are v1's Min Variance and Max Sharpe.

## Evidence plan

All evidence is on out of sample monthly returns after 10 bps costs unless stated.

**(a) Stationary bootstrap.** Politis and Romano stationary bootstrap, average block length 6 months (geometric block lengths, p = 1/6), wrapping around the end of the sample, 10,000 resamples, NumPy `default_rng(20261123)`. Each resample is a vector of month indices applied to **all 13 strategies and the risk free rate at once**, so every comparison uses the same resampled months. Reported for each strategy: Sharpe with a 95% percentile interval; and Sharpe minus 60/40 Sharpe with a 95% interval, a 99.6% interval (Bonferroni, 0.05 / 12 comparisons), and the share of resamples in which the strategy beats 60/40.

**(b) Decomposition.** The 3×3 table of Sharpe ratios S[rule, estimator] is split by a two way analysis of variance without replication: share explained by rule = 3 Σ_r (row mean − grand mean)² / total; by estimator = 3 Σ_e (column mean − grand mean)² / total; interaction = the remainder Σ (S − row mean − column mean + grand mean)² / total; total = Σ (S − grand mean)². The three shares sum to 1. Computed on the actual sample (point estimate) and on every bootstrap resample; reported as the point estimate, the average over resamples, and a 95% percentile interval. Note: this says where the *differences among the nine* come from, not whether any of them is good.

**(c) Probabilistic and deflated Sharpe.** On monthly (not annualized) excess returns. PSR = Φ((SR − SR*) √(n − 1) / √(1 − skew·SR + (kurtosis − 1)/4 · SR²)), kurtosis not in excess form. PSR is reported against SR* = 0. DSR uses SR* = √V · ((1 − γ) Φ⁻¹(1 − 1/N) + γ Φ⁻¹(1 − 1/(N·e))), where V is the variance of the monthly Sharpe ratios across all N trials, γ = 0.5772 (Euler–Mascheroni), and N is the trial count from the trial log (N = 13).

**(d) Risk and robustness.** For each strategy: CAGR, volatility, Sharpe, max drawdown, Calmar (CAGR / |max drawdown|), Sortino (annualized mean excess return / (√(mean of min(excess, 0)²) × √12)), 5% CVaR (mean of the worst ⌈0.05·n⌉ monthly returns), worst month, annual turnover. Cost sensitivity: Sharpe and CAGR at 0, 10, 25 and 50 bps (weights are unchanged; only the cost charged changes). Rolling 36 month Sharpe (monthly series). Start date sensitivity: Sharpe and CAGR from each January 2008 to January 2016 through the end (the 2008 row starts March 2008, the first out of sample month); the walk forward is not restarted, the series is only cut. Stress tables for the three v1 stress periods.

**(e) Stock bond crash test.** A crash month is a month t where both IVV and AGG have a negative trailing 12 month total return measured through the end of month t (months t−11 to t). This is a label for analysis, not a trading signal, so it may use month t. Reported: the number of crash months, the number of separate episodes (runs of consecutive crash months) with their dates, and for each strategy the average monthly return, compounded return over all crash months, and difference from 60/40 in average monthly return. No confidence interval is attached if there are fewer than 3 episodes, because one or two episodes cannot support one.

## Replication check (must pass before v2 runs)

`scripts/portfolio_model.py` runs its own engine on `portfolio/v1/data/prices.csv` and compares 60/40, Equal Weight, Inverse Vol, MV-Sample and MS-Sample with `portfolio/v1/results/summary.csv`. If any CAGR, volatility, Sharpe, max drawdown or worst month differs by more than 0.0005, or annual turnover by more than 0.005, the run stops and writes nothing. The published and rerun values are stored in `results.json`.

## What counts as a result

- **No reliable difference**: the 95% bootstrap interval of (strategy Sharpe − 60/40 Sharpe) contains 0. This is the default reading. Point estimates alone are never described as one strategy beating another.
- **Suggestive**: the 95% interval excludes 0 but the 99.6% (Bonferroni) interval does not.
- **Reliable**: the 99.6% interval excludes 0.
- **Rule vs estimator**: one factor "dominates" only if the lower end of its 95% decomposition interval is above the upper end of the other two. Otherwise the decomposition is reported as unresolved.
- **Skill above zero**: DSR at or above 0.95. PSR is shown for context only.
- **Crash protection**: described only as what happened in the counted episodes, never as a general property, unless there are at least 3 episodes.

## Trial log

N = 13 trials: the 13 strategies above, each run once on the locked data with the settings above.

Not counted as trials, disclosed here:
- v1's five strategies are 5 of the 13. v1's own results were published and seen before this spec was written.
- Replication runs of v1 on v1's own prices (the original code, and the v2 engine) reproduce published numbers and choose nothing.
- v1's optimizer tolerance was tuned (1e-6, 1e-10, 1e-12) to fix early stopping in Min Variance, per a comment in v1. That is numerical accuracy, not a strategy choice.
- Considered and dropped before any v2 code ran, never computed: a volatility targeting overlay; an earlier 9 strategy design without the 3×3 estimator grid.

**Hindsight disclosure.** The v2 additions (shrinkage, EWMA, risk parity, the trend rule, the stock bond crash test) were chosen by people who knew how 2008, 2020 and 2022 turned out and had seen v1's results. Trend rules are widely known to have done well in 2008. Treat any v2 strategy that looks good in those windows with that in mind. The nine ETFs were also chosen with hindsight (all survived).

## Outputs

- `data/portfolio/prices.csv`: daily adjusted closes and ^IRX used for the run.
- `data/portfolio/strategy_returns.csv`: monthly net returns (10 bps) of all 13 strategies plus the risk free rate.
- `data/portfolio/results.json`: everything above, with `as_of` (last month end used), the replication table, settings, and counts of non-converged months.

## Verification

`scripts/dev/verify_with_skfolio.py` (local only, never in a workflow) compares the MV, MS and RP weights of each estimator with skfolio's on 14 fixed months (12 evenly spaced plus the first and last), and writes `docs/portfolio-verification.md`. Definitional differences are documented, not forced to agree.

## Next step (not built)

A forward, live monthly portfolio would be a separate spec with its own start date, in the style of `docs/paper-trading-spec.md`. Nothing in v2 is a live track record.

## Amendments

### Amendment 1 (2026-11-23)

The body above is unchanged. No rule value changes.

1. **Crash test interval** (fills a gap in evidence item (e), which said an interval is attached with 3 or more episodes but did not say how). The 95% interval for a strategy's average crash month return minus 60/40's uses the same 10,000 stationary bootstrap resamples as (a): in each resample, average the difference over the resampled months that are crash months; resamples with no crash month are skipped; take the 2.5 and 97.5 percentiles.
2. **Disclosure.** This was written after the first full run, so the crash month count (13 months in 3 episodes: 2008-10, 2016-01, 2022-05 to 2023-03) and the point estimates of every strategy had been seen. The method is the most direct reuse of (a) and was not chosen by comparing alternatives. With two of the three episodes one month long, the interval mostly reflects 2022 to 2023.

### Amendment 2 (2026-11-23)

The body above is unchanged. No rule value or method changes.

1. **Canonical numbers.** The first run of `.github/workflows/run-portfolio.yml` (Python 3.12 on Linux, exact package pins) is the canonical source of the v2 numbers. Until then, `data/portfolio/results.json` is the local run (Python 3.14 on Windows, same package pins). The workflow re-runs the analysis on the committed `data/portfolio/prices.csv` without downloading anything, and fails if any number differs from the committed file by more than 0.0005 or 0.05%, whichever is larger (`scripts/check_portfolio_schema.py`); text, dates, counts and structure must be identical. If it passes, it commits its own output, and later runs must reproduce that.
2. **Why.** Optimizer results can differ in the sixth decimal between platforms and library builds. The v1 rerun showed this: 40 of v1's 45 published figures were identical at six decimals, and the other five (optimizer outputs) differed by at most 0.000007.
3. **Labels.** The stress window called "2008 Financial Crisis" in v1 is shown on the site as "Jun 2008 to Feb 2009", its actual dates. The window itself is unchanged.
