# iShares Multi-Asset Portfolio Model

This project tests whether portfolio optimization beats simple allocation rules once you judge it fairly. Every portfolio is built from nine iShares ETFs and tested **out of sample**: each month, the weights come only from the prior 36 months of data, and the portfolio is held for the following month. Trading costs are charged on every rebalance.

## Universe

| ETF | Exposure |
|---|---|
| IVV | US large cap stocks (S&P 500) |
| IJR | US small cap stocks (S&P 600) |
| EFA | Developed international stocks |
| EEM | Emerging market stocks |
| AGG | US aggregate bonds |
| TLT | 20+ year US Treasuries |
| TIP | Inflation protected Treasuries (TIPS) |
| LQD | Investment grade corporate bonds |
| IAU | Gold |

The risk-free rate is the 13-week T-bill yield (^IRX).

## Strategies

| Strategy | Rule |
|---|---|
| 60/40 | 60% IVV / 40% AGG, the standard benchmark |
| Equal Weight | 1/9 in each ETF |
| Inverse Vol | Weight each ETF by 1 / its trailing volatility, a simple form of risk parity |
| Min Variance | The optimizer picks the lowest volatility long-only mix |
| Max Sharpe | The optimizer picks the highest historical Sharpe ratio, capped at 40% per ETF |

## Method

1. Download daily adjusted prices (yfinance) and convert them to month-end total returns.
2. Walk forward one month at a time. Estimate returns and covariance on the trailing 36 months, set the target weights, then earn the next month's return.
3. Charge 10 bps on turnover, measured from the drifted weights to the new target.
4. Report CAGR, volatility, Sharpe ratio, max drawdown, worst month and turnover. Run stress tests for the 2008 financial crisis, the COVID crash and the 2022 rate shock, when stocks and bonds fell together.

## Run it

```
pip install -r requirements.txt
python portfolio.py
```

Prices are cached to `data/prices.csv`, so later runs use the same data. Use `python portfolio.py --refresh` to download fresh prices.

## Output (`results/`)

| File | Contents |
|---|---|
| `summary.md` / `summary.csv` | Performance table for all five strategies |
| `stress_tests.csv` | Total return in each stress period |
| `latest_weights.csv` | Each strategy's most recent allocation |
| `growth.png` | Growth of $10,000 (log scale) |
| `drawdowns.png` | Drawdown from prior peak |
| `weights_min_variance.png`, `weights_max_sharpe.png` | How the optimizers moved weights over time |

## Results

Out of sample period: Mar 2008 to Aug 2026 (222 months), 36 month lookback, 10 bps costs.

| | CAGR | Volatility | Sharpe | Max Drawdown | Worst Month | Annual Turnover |
|---|---|---|---|---|---|---|
| 60/40 | 8.4% | 10.0% | 0.72 | -29.6% | -10.9% | 19.6% |
| Equal Weight | 6.4% | 9.7% | 0.55 | -24.8% | -12.6% | 29.2% |
| Inverse Vol | 5.2% | 7.4% | 0.55 | -18.8% | -8.4% | 31.6% |
| Min Variance | 2.8% | 4.7% | 0.33 | -17.5% | -4.1% | 74.4% |
| Max Sharpe | 7.3% | 9.6% | 0.64 | -20.9% | -10.4% | 243.9% |

**Stress tests (total return)**

| | 60/40 | Equal Weight | Inverse Vol | Min Variance | Max Sharpe |
|---|---|---|---|---|---|
| 2008 Financial Crisis | -29.6% | -24.8% | -14.2% | -1.2% | -3.9% |
| COVID Crash | -11.6% | -9.5% | -5.1% | -2.2% | -3.5% |
| 2022 Rate Shock | -15.8% | -15.9% | -14.8% | -13.9% | -14.0% |

**What the results show**

- The simple 60/40 benchmark had the highest return and Sharpe ratio after costs. None of the other four strategies beat it on either measure.
- Max Sharpe came closest (7.3% CAGR, 0.64 Sharpe), but it turned over about 244% of the portfolio each year. The optimizer chases noisy historical returns, and the trading costs eat into the result.
- Min Variance gave the best crisis protection, losing 1.2% in 2008 versus 29.6% for 60/40, but it gave up a lot of return (2.8% CAGR) and traded 74% a year.
- In 2022 every strategy lost between 14% and 16%, because stocks and bonds fell together. The optimizers did not protect better here, since a 36 month window is slow to react when the stock/bond correlation breaks.

## Limitations

- Historical mean returns are noisy, so Max Sharpe weights can swing a lot from month to month (see its turnover).
- The 36-month window means the optimizers react late to regime changes, such as the 2022 break in the stock/bond correlation.
- ETF prices only. There are no taxes, and bid/ask costs are approximated at a flat 10 bps.
- The nine ETFs were chosen with hindsight: all of them survived and are still trading today. The results describe this universe and period, and should not be read as a forecast.
