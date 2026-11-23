"""
iShares Multi-Asset Portfolio Model
-----------------------------------
Builds long-only portfolios from nine iShares ETFs and tests them out of sample
with a walk-forward backtest: every month, weights are set using ONLY the prior
36 months of data, then held for the next month. Transaction costs are charged
on turnover.

Strategies compared
  1. 60/40            60% IVV (US stocks) / 40% AGG (US bonds), the classic benchmark
  2. Equal Weight     1/9 in every ETF
  3. Inverse Vol      weight each ETF by 1 / its volatility (simple risk parity)
  4. Min Variance     lowest possible portfolio volatility (optimizer)
  5. Max Sharpe       highest historical Sharpe ratio (optimizer), capped at 40% per ETF

Run
  python portfolio.py             # downloads prices with yfinance, caches to data/
  python portfolio.py --refresh   # force a fresh download

Outputs go to results/ (summary table, stress tests, charts, latest weights).
"""

import argparse
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.optimize import minimize

# ---------------------------------------------------------------- settings
TICKERS = {
    "IVV": "US large cap stocks (S&P 500)",
    "IJR": "US small cap stocks (S&P 600)",
    "EFA": "Developed international stocks",
    "EEM": "Emerging market stocks",
    "AGG": "US aggregate bonds",
    "TLT": "20+ year US Treasuries",
    "TIP": "Inflation protected Treasuries (TIPS)",
    "LQD": "Investment grade corporate bonds",
    "IAU": "Gold",
}
RATE_TICKER = "^IRX"      # 13 week T-bill yield, used as the risk-free rate
START = "2005-02-01"      # IAU (the youngest ETF) starts trading Jan 2005
LOOKBACK = 36             # months of history used to set each month's weights
COST_BPS = 10             # transaction cost per $1 traded, in basis points
MAX_WEIGHT = 0.40         # cap per ETF for the Max Sharpe optimizer

STRESS_PERIODS = {
    "2008 Financial Crisis": ("2008-06", "2009-02"),
    "COVID Crash": ("2020-02", "2020-03"),
    "2022 Rate Shock": ("2022-01", "2022-12"),
}

# pandas 2.2+ uses "ME" for month end; older versions use "M"
try:
    pd.Series([1.0], index=pd.to_datetime(["2020-01-31"])).resample("ME").last()
    MONTH_END = "ME"
except ValueError:
    MONTH_END = "M"

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
OUT = os.path.join(HERE, "results")


# ---------------------------------------------------------------- data
def load_prices(refresh=False):
    """Daily adjusted closes for the ETFs plus the T-bill yield, cached to CSV."""
    path = os.path.join(DATA, "prices.csv")
    if os.path.exists(path) and not refresh:
        return pd.read_csv(path, index_col=0, parse_dates=True)
    import yfinance as yf
    os.makedirs(DATA, exist_ok=True)
    raw = yf.download(list(TICKERS) + [RATE_TICKER], start=START,
                      auto_adjust=True, progress=False)["Close"]
    raw.to_csv(path)
    return raw


def monthly_returns(prices):
    """Month end total returns for each ETF and the monthly risk-free rate."""
    # Drop the current month if it isn't finished yet, so a partial month
    # isn't treated as a full month of returns.
    last_day = prices.index[-1]
    if last_day < last_day + pd.offsets.BMonthEnd(0):
        prices = prices[prices.index < last_day.to_period("M").to_timestamp()]
    etf = prices[list(TICKERS)].resample(MONTH_END).last()
    rets = etf.pct_change().dropna(how="all").dropna()
    # Risk-free for each month = the T-bill yield known at the START of that
    # month (last month's closing yield), so nothing uses future information.
    rf = prices[RATE_TICKER].resample(MONTH_END).last().shift(1) / 100 / 12   # annual % -> monthly decimal
    rf = rf.reindex(rets.index).ffill().fillna(0)
    return rets, rf


# ---------------------------------------------------------------- weights
def w_sixty_forty(hist, rf_hist):
    w = pd.Series(0.0, index=hist.columns)
    w["IVV"], w["AGG"] = 0.60, 0.40
    return w


def w_equal(hist, rf_hist):
    return pd.Series(1 / hist.shape[1], index=hist.columns)


def w_inverse_vol(hist, rf_hist):
    inv = 1 / hist.std()
    return inv / inv.sum()


def _optimize(objective, n, cap):
    cons = ({"type": "eq", "fun": lambda w: w.sum() - 1},)
    # Tighter than SciPy's default (1e-6), which stopped Min Variance early.
    # 1e-10 matched a multi-start check in every month; 1e-12 gave the same
    # weights but triggered false "did not converge" warnings.
    res = minimize(objective, np.full(n, 1 / n), method="SLSQP",
                   bounds=[(0, cap)] * n, constraints=cons,
                   options={"ftol": 1e-10, "maxiter": 1000})
    if not res.success:
        print(f"  warning: optimizer did not converge ({res.message})")
    w = np.clip(res.x, 0, None)
    return w / w.sum()


def w_min_variance(hist, rf_hist):
    cov = hist.cov().values * 12 * 1e4   # annualized, in squared % terms, so the optimizer has something to work with
    w = _optimize(lambda w: w @ cov @ w, hist.shape[1], 1.0)
    return pd.Series(w, index=hist.columns)


def w_max_sharpe(hist, rf_hist):
    excess = hist.sub(rf_hist, axis=0)
    mu, cov = excess.mean().values, hist.cov().values

    def neg_sharpe(w):
        return -(w @ mu) / np.sqrt(w @ cov @ w)

    w = _optimize(neg_sharpe, hist.shape[1], MAX_WEIGHT)
    return pd.Series(w, index=hist.columns)


STRATEGIES = {
    "60/40": w_sixty_forty,
    "Equal Weight": w_equal,
    "Inverse Vol": w_inverse_vol,
    "Min Variance": w_min_variance,
    "Max Sharpe": w_max_sharpe,
}


# ---------------------------------------------------------------- backtest
def walk_forward(rets, rf, weight_fn):
    """Each month: set weights on the trailing LOOKBACK months, earn next month's return.
    Costs are charged on the change from the drifted weights to the new target."""
    dates = rets.index[LOOKBACK:]
    out_ret, turnover, weights = [], [], []
    drifted = pd.Series(0.0, index=rets.columns)   # starts in cash, so month one buys everything
    for t, date in enumerate(dates):
        i = LOOKBACK + t
        hist = rets.iloc[i - LOOKBACK:i]
        target = weight_fn(hist, rf.iloc[i - LOOKBACK:i])
        trade = (target - drifted).abs().sum()
        r_month = rets.iloc[i]
        gross = float(target @ r_month)
        out_ret.append(gross - trade * COST_BPS / 10_000)
        turnover.append(trade)
        weights.append(target.rename(date))
        grown = target * (1 + r_month)
        drifted = grown / grown.sum()
    return (pd.Series(out_ret, index=dates), pd.Series(turnover, index=dates),
            pd.DataFrame(weights))


# ---------------------------------------------------------------- metrics
def max_drawdown(r):
    wealth = (1 + r).cumprod()
    return (wealth / wealth.cummax() - 1).min()


def summarize(r, rf, turnover):
    years = len(r) / 12
    excess = r - rf.reindex(r.index)
    return {
        "CAGR": (1 + r).prod() ** (1 / years) - 1,
        "Volatility": r.std() * np.sqrt(12),
        "Sharpe": excess.mean() / excess.std() * np.sqrt(12),
        "Max Drawdown": max_drawdown(r),
        "Worst Month": r.min(),
        "Annual Turnover": turnover.iloc[1:].mean() * 12,   # skip the initial buy
    }


def stress_table(results):
    rows = {}
    for name, (a, b) in STRESS_PERIODS.items():
        rows[name] = {s: (1 + r.loc[a:b]).prod() - 1 if len(r.loc[a:b]) else np.nan
                      for s, r in results.items()}
    return pd.DataFrame(rows).T


# ---------------------------------------------------------------- charts
def plot_growth(results):
    fig, ax = plt.subplots(figsize=(10, 5.5))
    for name, r in results.items():
        ax.plot((1 + r).cumprod() * 10_000, label=name, lw=1.6)
    ax.set_yscale("log")
    ax.set_title("Growth of $10,000, out of sample (after costs)")
    ax.set_ylabel("Portfolio value (log scale)")
    ax.legend(frameon=False)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "growth.png"), dpi=150)
    plt.close(fig)


def plot_drawdowns(results):
    fig, ax = plt.subplots(figsize=(10, 4.5))
    for name, r in results.items():
        w = (1 + r).cumprod()
        ax.plot(w / w.cummax() - 1, label=name, lw=1.2)
    ax.set_title("Drawdown from prior peak")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _: f"{y:.0%}"))
    ax.legend(frameon=False, ncol=3)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "drawdowns.png"), dpi=150)
    plt.close(fig)


def plot_weights(weights, name):
    fig, ax = plt.subplots(figsize=(10, 4.5))
    ax.stackplot(weights.index, weights.T.values, labels=weights.columns)
    ax.set_title(f"{name}: monthly weights")
    ax.set_ylim(0, 1)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _: f"{y:.0%}"))
    ax.legend(frameon=False, ncol=9, fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.08))
    fig.tight_layout()
    fname = name.lower().replace(" ", "_").replace("/", "_")
    fig.savefig(os.path.join(OUT, f"weights_{fname}.png"), dpi=150)
    plt.close(fig)


def to_markdown(df):
    """Simple markdown table (avoids needing the tabulate package)."""
    cols = [""] + [str(c) for c in df.columns]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for idx, row in df.iterrows():
        lines.append("| " + " | ".join([str(idx)] + [str(v) for v in row]) + " |")
    return "\n".join(lines)


def fmt_pct(df):
    return df.apply(lambda col: col.map(lambda x: f"{x:.1%}"))


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="re-download prices")
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)

    rets, rf = monthly_returns(load_prices(args.refresh))
    results, turns, all_w = {}, {}, {}
    for name, fn in STRATEGIES.items():
        results[name], turns[name], all_w[name] = walk_forward(rets, rf, fn)

    summary = pd.DataFrame({n: summarize(results[n], rf, turns[n]) for n in STRATEGIES}).T
    stress = stress_table(results)
    latest = pd.DataFrame({n: all_w[n].iloc[-1] for n in STRATEGIES})

    summary.to_csv(os.path.join(OUT, "summary.csv"))
    stress.to_csv(os.path.join(OUT, "stress_tests.csv"))
    latest.to_csv(os.path.join(OUT, "latest_weights.csv"))
    pd.DataFrame(results).to_csv(os.path.join(OUT, "monthly_returns.csv"))

    fmt = fmt_pct(summary)
    fmt["Sharpe"] = summary["Sharpe"].map(lambda x: f"{x:.2f}")
    period = f"{results['60/40'].index[0]:%b %Y} to {results['60/40'].index[-1]:%b %Y}"
    with open(os.path.join(OUT, "summary.md"), "w") as f:
        f.write(f"Out of sample period: {period} ({len(results['60/40'])} months), "
                f"{LOOKBACK} month lookback, {COST_BPS} bps costs\n\n")
        f.write(to_markdown(fmt) + "\n\n**Stress tests (total return)**\n\n")
        f.write(to_markdown(fmt_pct(stress)) + "\n")

    plot_growth(results)
    plot_drawdowns(results)
    for n in ("Min Variance", "Max Sharpe"):
        plot_weights(all_w[n], n)

    print(f"Out of sample: {period}\n")
    print(fmt.to_string(), "\n")
    print("Stress tests (total return)")
    print(fmt_pct(stress).to_string(), "\n")
    print("Latest weights")
    print(fmt_pct(latest).to_string())
    print(f"\nSaved to {OUT}")


if __name__ == "__main__":
    main()
