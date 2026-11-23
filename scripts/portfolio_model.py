"""
Portfolio model v2. Pre registered in docs/portfolio-spec.md: read that first.

Backtests 13 long only strategies on nine iShares ETFs (walk forward, monthly, 36 month
window, 10 bps costs) and writes data/portfolio/results.json and strategy_returns.csv.

    python scripts/portfolio_model.py           # use the committed data/portfolio/prices.csv
    python scripts/portfolio_model.py --fetch   # download prices first (yfinance), through
                                                # the last complete month

Conventions are copied from portfolio/v1/portfolio.py (the original, published code). Before
anything is written, this engine is run on v1's own prices and must reproduce v1's published
summary; otherwise the run stops and writes nothing. Bad or missing data also stops the run.
"""
import argparse
import json
import math
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import kurtosis, norm, skew
from sklearn.covariance import LedoitWolf

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "portfolio"
V1 = ROOT / "portfolio" / "v1"

# ---- v1 settings (portfolio/v1/portfolio.py), unchanged
TICKERS = ["IVV", "IJR", "EFA", "EEM", "AGG", "TLT", "TIP", "LQD", "IAU"]
RATE_TICKER = "^IRX"
START = "2005-02-01"
LOOKBACK = 36
COST_BPS = 10
MAX_WEIGHT = 0.40
STRESS_PERIODS = {
    "2008 Financial Crisis": ("2008-06", "2009-02"),
    "COVID Crash": ("2020-02", "2020-03"),
    "2022 Rate Shock": ("2022-01", "2022-12"),
}

# ---- v2 settings (docs/portfolio-spec.md)
HALF_LIFE = 12
TREND_MONTHS = 10
N_BOOT = 10_000
BLOCK = 6
SEED = 20261123
COST_GRID = (0, 10, 25, 50)
START_YEARS = range(2008, 2017)
N_TRIALS = 13
MAX_ABS_MONTH_RET = 0.60
RP_TOL = 1e-3
REPL_TOL = {"CAGR": 5e-4, "Volatility": 5e-4, "Sharpe": 5e-4, "Max Drawdown": 5e-4,
            "Worst Month": 5e-4, "Annual Turnover": 5e-3}


def fail(msg):
    sys.exit(f"PORTFOLIO MODEL FAILED: {msg}")


# ---------------------------------------------------------------- data
def fetch_prices(today=None):
    """Daily adjusted closes plus ^IRX from START through the last complete month."""
    import yfinance as yf
    cols = TICKERS + [RATE_TICKER]
    raw = yf.download(cols, start=START, auto_adjust=True, progress=False)
    if raw is None or raw.empty:
        fail("download returned no data")
    px = raw["Close"]
    missing = [t for t in cols if t not in px.columns or px[t].dropna().empty]
    if missing:
        fail(f"no data for {missing}")
    first_of_month = pd.Timestamp((today or date.today()).replace(day=1))
    return px.loc[px.index < first_of_month, cols]


def monthly_returns(prices, drop_partial=True):
    """v1's monthly_returns: month end total returns and the monthly risk free rate.
    drop_partial=True is v1's partial month rule, kept for the v1 replication only: it ignores market
    holidays, so data ending before a month end holiday (Memorial Day 2027-05-31) would lose a full month.
    v2 data never needs it, because fetch_prices already cuts prices to complete months."""
    missing = [t for t in TICKERS + [RATE_TICKER] if t not in prices.columns]
    if missing:
        fail(f"missing columns {missing}")
    last_day = prices.index[-1]
    if drop_partial and last_day < last_day + pd.offsets.BMonthEnd(0):
        prices = prices[prices.index < last_day.to_period("M").to_timestamp()]
    etf = prices[TICKERS].resample("ME").last()
    rets = etf.pct_change().dropna(how="all").dropna()
    rf = prices[RATE_TICKER].resample("ME").last().shift(1) / 100 / 12
    rf = rf.reindex(rets.index).ffill().fillna(0)
    return rets, rf


def validate(prices, rets):
    """Fail loudly on anything the backtest would otherwise silently absorb."""
    months = pd.period_range(rets.index[0], rets.index[-1], freq="M")
    if len(months) != len(rets):
        fail(f"{len(months) - len(rets)} month(s) missing inside {months[0]} to {months[-1]}")
    etf = prices[TICKERS].resample("ME").last().loc[rets.index[0] - pd.offsets.MonthEnd(1):rets.index[-1]]
    if not np.isfinite(etf.values).all() or (etf.values <= 0).any():
        fail("missing, non finite or non positive month end ETF price")
    # Every ETF must have a price on each month's last trading day, or .last() silently uses a stale one.
    days = prices[TICKERS].dropna(how="all")
    last_rows = days.groupby(days.index.to_period("M")).tail(1)
    lo = (rets.index[0] - pd.offsets.MonthEnd(1)).to_period("M").start_time
    last_rows = last_rows[(last_rows.index >= lo) & (last_rows.index <= rets.index[-1])]
    stale = last_rows.isna()
    if stale.values.any():
        d = stale.index[stale.any(axis=1)][0]
        fail(f"{list(stale.columns[stale.loc[d]])} missing on the last trading day of {d:%Y-%m} ({d:%Y-%m-%d})")
    big = rets.abs() > MAX_ABS_MONTH_RET
    if big.values.any():
        r, c = np.argwhere(big.values)[0]
        fail(f"{rets.columns[c]} return {rets.iat[r, c]:.1%} in {rets.index[r]:%Y-%m} is beyond ±60%")
    irx = prices[RATE_TICKER].resample("ME").last().loc[rets.index[0] - pd.offsets.MonthEnd(1):rets.index[-1]]
    gaps = irx.isna().astype(int)
    if (gaps & gaps.shift(1, fill_value=0)).any():
        fail("^IRX missing for two month ends in a row")
    if len(rets) < LOOKBACK + 12:
        fail("not enough months")


# ---------------------------------------------------------------- covariance estimators
def cov_sample(h):
    return h.cov().values


def cov_lw(h):
    return LedoitWolf().fit(h.values).covariance_


def cov_ewma(h):
    k = np.arange(len(h))[::-1]               # 0 = newest month
    w = 0.5 ** (k / HALF_LIFE)
    w /= w.sum()
    x = h.values
    d = x - w @ x
    return (d * w[:, None]).T @ d


ESTIMATORS = {"Sample": cov_sample, "LW": cov_lw, "EWMA": cov_ewma}


# ---------------------------------------------------------------- rules (weights for one month)
def _optimize(objective, n, cap):
    """v1's optimizer, unchanged except that it returns success instead of printing."""
    cons = ({"type": "eq", "fun": lambda w: w.sum() - 1},)
    res = minimize(objective, np.full(n, 1 / n), method="SLSQP",
                   bounds=[(0, cap)] * n, constraints=cons,
                   options={"ftol": 1e-10, "maxiter": 1000})
    w = np.clip(res.x, 0, None)
    return w / w.sum(), bool(res.success)


def rule_mv(hist, rf_hist, cov):
    c = cov * 12 * 1e4
    return _optimize(lambda w: w @ c @ w, hist.shape[1], 1.0)


def rule_ms(hist, rf_hist, cov):
    mu = hist.sub(rf_hist, axis=0).mean().values
    return _optimize(lambda w: -(w @ mu) / np.sqrt(w @ cov @ w), hist.shape[1], MAX_WEIGHT)


def rule_rp(hist, rf_hist, cov):
    c = cov * 12 * 1e4
    n = len(c)
    res = minimize(lambda w: 0.5 * w @ c @ w - np.log(w).sum() / n, np.full(n, 1 / n),
                   jac=lambda w: c @ w - 1 / (n * w), method="L-BFGS-B",
                   bounds=[(1e-12, None)] * n, options={"ftol": 1e-16, "gtol": 1e-12, "maxiter": 10_000})
    w = res.x / res.x.sum()
    rc = w * (c @ w)
    return w, bool(rc.max() / rc.min() - 1 < RP_TOL)


RULES = {"MV": rule_mv, "MS": rule_ms, "RP": rule_rp}


def w_sixty_forty(hist, rf_hist):
    w = np.zeros(hist.shape[1])
    w[TICKERS.index("IVV")], w[TICKERS.index("AGG")] = 0.60, 0.40
    return w, True


def w_equal(hist, rf_hist):
    return np.full(hist.shape[1], 1 / hist.shape[1]), True


def w_inverse_vol(hist, rf_hist):
    inv = 1 / hist.std().values
    return inv / inv.sum(), True


def w_trend(hist, rf_hist):
    # Price levels for months t-10..t-1 rebuilt from returns; the ratio to their mean does not depend on the base.
    lv = (1 + hist.iloc[-TREND_MONTHS:]).cumprod()
    return (lv.iloc[-1] > lv.mean()).values / hist.shape[1], True


def _combo(rule, est):
    return lambda h, rf: rule(h, rf, est(h))


STRATEGIES = {"60/40": w_sixty_forty, "Equal Weight": w_equal, "Inverse Vol": w_inverse_vol, "Trend 10M": w_trend}
META = {"60/40": ("naive", None, None), "Equal Weight": ("naive", None, None),
        "Inverse Vol": ("naive", None, None), "Trend 10M": ("trend", None, None)}
for _r, _rf in RULES.items():
    for _e, _ef in ESTIMATORS.items():
        STRATEGIES[f"{_r}-{_e}"] = _combo(_rf, _ef)
        META[f"{_r}-{_e}"] = ("optimizer", _r, _e)
GRID = [f"{r}-{e}" for r in RULES for e in ESTIMATORS]   # rows = rules, columns = estimators
assert len(STRATEGIES) == N_TRIALS


# ---------------------------------------------------------------- backtest
def walk_forward(rets, rf, weight_fn):
    """v1's walk forward plus a cash leg (for the trend rule): weights at month t use months t-36..t-1.
    Returns gross return, turnover, weights and the count of non converged months."""
    R, RF = rets.values, rf.values
    gross, trades, W, bad = [], [], [], 0
    drifted = np.zeros(rets.shape[1])                    # starts in cash, so month one buys everything
    for i in range(LOOKBACK, len(rets)):
        target, ok = weight_fn(rets.iloc[i - LOOKBACK:i], rf.iloc[i - LOOKBACK:i])
        bad += not ok
        cash = 1 - target.sum()
        trades.append(np.abs(target - drifted).sum())     # ETF legs only; cash costs nothing
        gross.append(float(target @ R[i]) + cash * RF[i])
        W.append(target)
        grown = target * (1 + R[i])
        drifted = grown / (grown.sum() + cash * (1 + RF[i]))
    dates = rets.index[LOOKBACK:]
    return (pd.Series(gross, dates), pd.Series(trades, dates),
            pd.DataFrame(W, dates, rets.columns), bad)


def net_of(gross, trades, bps):
    return gross - trades * bps / 10_000


# ---------------------------------------------------------------- metrics
def max_drawdown(r):
    wealth = (1 + r).cumprod()
    return (wealth / wealth.cummax() - 1).min()


def cagr(r):
    return (1 + r).prod() ** (12 / len(r)) - 1


def sharpe(r, rf):
    ex = r - rf.reindex(r.index)
    return ex.mean() / ex.std() * np.sqrt(12)


def v1_summary(r, rf, turnover):
    """v1's summarize, same keys."""
    return {"CAGR": cagr(r), "Volatility": r.std() * np.sqrt(12), "Sharpe": sharpe(r, rf),
            "Max Drawdown": max_drawdown(r), "Worst Month": r.min(),
            "Annual Turnover": turnover.iloc[1:].mean() * 12}


def stress(r):
    return {k: float((1 + r.loc[a:b]).prod() - 1) for k, (a, b) in STRESS_PERIODS.items()}


def full_metrics(r, rf, turnover):
    ex = r - rf.reindex(r.index)
    mdd = max_drawdown(r)
    g = cagr(r)
    down = np.sqrt((np.minimum(ex, 0) ** 2).mean()) * np.sqrt(12)
    k = math.ceil(0.05 * len(r))
    return {"cagr": g, "volatility": r.std() * np.sqrt(12), "sharpe": sharpe(r, rf),
            "max_drawdown": mdd, "calmar": g / abs(mdd), "sortino": ex.mean() * 12 / down,
            "cvar5": np.sort(r.values)[:k].mean(), "worst_month": r.min(),
            "annual_turnover": turnover.iloc[1:].mean() * 12}


def psr(ex, sr_star=0.0):
    """Probabilistic Sharpe ratio on monthly excess returns (Bailey and Lopez de Prado)."""
    x = np.asarray(ex)
    sr = x.mean() / x.std(ddof=1)
    g3, g4 = skew(x), kurtosis(x, fisher=False)
    return float(norm.cdf((sr - sr_star) * np.sqrt(len(x) - 1) / np.sqrt(1 - g3 * sr + (g4 - 1) / 4 * sr ** 2)))


def dsr_threshold(monthly_srs, n_trials):
    """Expected maximum Sharpe of n_trials unskilled strategies (the deflated Sharpe benchmark)."""
    g = 0.5772156649015329
    return float(np.sqrt(np.var(monthly_srs, ddof=1))
                 * ((1 - g) * norm.ppf(1 - 1 / n_trials) + g * norm.ppf(1 - 1 / (n_trials * np.e))))


# ---------------------------------------------------------------- bootstrap and decomposition
def stationary_indices(n, n_boot, rng, block=BLOCK):
    """Politis-Romano stationary bootstrap: geometric blocks with mean `block`, wrapping at the end."""
    idx = np.empty((n_boot, n), dtype=np.int64)
    idx[:, 0] = rng.integers(0, n, n_boot)
    jump = rng.random((n_boot, n)) < 1 / block
    fresh = rng.integers(0, n, (n_boot, n))
    for t in range(1, n):
        idx[:, t] = np.where(jump[:, t], fresh[:, t], (idx[:, t - 1] + 1) % n)
    return idx


def boot_sharpes(excess, idx, chunk=500):
    """excess: months x strategies. Annualized Sharpe of every strategy in every resample."""
    out = np.empty((len(idx), excess.shape[1]))
    for s in range(0, len(idx), chunk):
        x = excess[idx[s:s + chunk]]
        out[s:s + chunk] = x.mean(1) / x.std(1, ddof=1) * np.sqrt(12)
    return out


def anova_shares(S):
    """Two way ANOVA without replication on (..., rules, estimators). Returns rule, estimator, interaction shares."""
    g = S.mean((-2, -1), keepdims=True)
    r = S.mean(-1, keepdims=True)
    c = S.mean(-2, keepdims=True)
    tot = ((S - g) ** 2).sum((-2, -1))
    rule = S.shape[-1] * ((r - g) ** 2).sum((-2, -1))
    est = S.shape[-2] * ((c - g) ** 2).sum((-2, -1))
    inter = ((S - r - c + g) ** 2).sum((-2, -1))
    return rule / tot, est / tot, inter / tot


def ci(a, lo=2.5, hi=97.5):
    return [float(np.percentile(a, lo)), float(np.percentile(a, hi))]


# ---------------------------------------------------------------- v1 replication
V1_NAMES = {"60/40": "60/40", "Equal Weight": "Equal Weight", "Inverse Vol": "Inverse Vol",
            "Min Variance": "MV-Sample", "Max Sharpe": "MS-Sample"}


def replicate_v1():
    prices = pd.read_csv(V1 / "data" / "prices.csv", index_col=0, parse_dates=True)
    pub = pd.read_csv(V1 / "results" / "summary.csv", index_col=0)
    pub_stress = pd.read_csv(V1 / "results" / "stress_tests.csv", index_col=0)
    rets, rf = monthly_returns(prices)
    rerun, rerun_stress = {}, {}
    for v1name, v2name in V1_NAMES.items():
        g, t, _, _ = walk_forward(rets, rf, STRATEGIES[v2name])
        r = net_of(g, t, COST_BPS)
        rerun[v1name] = v1_summary(r, rf, t)
        rerun_stress[v1name] = stress(r)
        for k, tol in REPL_TOL.items():
            if abs(rerun[v1name][k] - pub.loc[v1name, k]) > tol:
                fail(f"v1 replication: {v1name} {k} rerun {rerun[v1name][k]:.4f} vs published {pub.loc[v1name, k]:.4f}")
    return {
        "source": "portfolio/v1 (original code, prices and results)",
        "period": f"{rets.index[LOOKBACK]:%Y-%m} to {rets.index[-1]:%Y-%m}",
        "published": {n: {k: float(pub.loc[n, k]) for k in REPL_TOL} | {"stress": {s: float(pub_stress.loc[s, n]) for s in STRESS_PERIODS}} for n in V1_NAMES},
        "rerun": {n: {k: float(v) for k, v in rerun[n].items()} | {"stress": rerun_stress[n]} for n in V1_NAMES},
        "tolerance": REPL_TOL,
    }


# ---------------------------------------------------------------- crash test
def crash_months(rets, dates):
    tr12 = np.expm1(np.log1p(rets[["IVV", "AGG"]]).rolling(12).sum())
    flag = ((tr12 < 0).all(axis=1) & tr12.notna().all(axis=1)).reindex(dates)
    episodes, start = [], None
    for i, d in enumerate(dates):
        if flag.iloc[i] and start is None:
            start = i
        if start is not None and (not flag.iloc[i] or i == len(dates) - 1):
            end = i if flag.iloc[i] else i - 1
            episodes.append({"start": f"{dates[start]:%Y-%m}", "end": f"{dates[end]:%Y-%m}", "months": end - start + 1})
            start = None
    return flag, episodes


# ---------------------------------------------------------------- main
def rnd(x, d=6):
    if isinstance(x, dict):
        return {k: rnd(v, d) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [rnd(v, d) for v in x]
    if isinstance(x, (float, np.floating)):
        return None if not np.isfinite(x) else round(float(x), d)
    if isinstance(x, np.integer):
        return int(x)
    return x


def run(prices):
    rets, rf = monthly_returns(prices, drop_partial=False)
    validate(prices, rets)
    repl = replicate_v1()

    gross, turn, weights, bad = {}, {}, {}, {}
    for name, fn in STRATEGIES.items():
        gross[name], turn[name], weights[name], bad[name] = walk_forward(rets, rf, fn)
    dates = gross["60/40"].index
    rf_oos = rf.reindex(dates)
    net = pd.DataFrame({n: net_of(gross[n], turn[n], COST_BPS) for n in STRATEGIES})
    excess = net.sub(rf_oos, axis=0)

    # (a) bootstrap
    idx = stationary_indices(len(dates), N_BOOT, np.random.default_rng(SEED))
    bs = boot_sharpes(excess.values, idx)
    names = list(STRATEGIES)
    base = names.index("60/40")

    # (b) decomposition
    S_point = np.array([[full_metrics(net[f"{r}-{e}"], rf, turn[f"{r}-{e}"])["sharpe"] for e in ESTIMATORS] for r in RULES])
    S_boot = bs[:, [names.index(g) for g in GRID]].reshape(N_BOOT, len(RULES), len(ESTIMATORS))
    pt, bt = anova_shares(S_point), anova_shares(S_boot)
    decomposition = {
        "rows": list(RULES), "columns": list(ESTIMATORS), "sharpe_table": S_point.tolist(),
        **{k: {"point": float(pt[i]), "boot_mean": float(bt[i].mean()), "ci95": ci(bt[i])}
           for i, k in enumerate(("rule", "estimator", "interaction"))},
    }

    # (c) PSR / DSR
    monthly_sr = (excess.mean() / excess.std()).values
    sr_star = dsr_threshold(monthly_sr, N_TRIALS)

    # (e) crash test
    flag, episodes = crash_months(rets, dates)
    crash_n = int(flag.sum())
    # Amendment 1: interval for the crash month average difference vs 60/40, from the same resamples,
    # counting only resampled months that are crash months (resamples with none are skipped).
    crash_ci = {}
    if len(episodes) >= 3:
        mask = flag.values[idx]
        cnt = mask.sum(1)
        keep = cnt > 0
        for j, n in enumerate(names):
            d = (net[n].values - net["60/40"].values)[idx]
            crash_ci[n] = ci((d * mask).sum(1)[keep] / cnt[keep])

    strategies = []
    for j, n in enumerate(names):
        r = net[n]
        m = full_metrics(r, rf, turn[n])
        d = bs[:, j] - bs[:, base]
        cr = r[flag.values]
        strategies.append({
            "name": n, "group": META[n][0], "rule": META[n][1], "estimator": META[n][2],
            **m,
            "sharpe_ci95": ci(bs[:, j]),
            "vs_6040": None if n == "60/40" else {
                "sharpe_diff": m["sharpe"] - full_metrics(net["60/40"], rf, turn["60/40"])["sharpe"],
                "ci95": ci(d), "ci996": ci(d, 0.2, 99.8), "share_beating": float((d > 0).mean())},
            "psr": psr(excess[n]), "dsr": psr(excess[n], sr_star),
            "cost_sensitivity": {str(b): {"cagr": cagr(net_of(gross[n], turn[n], b)),
                                          "sharpe": sharpe(net_of(gross[n], turn[n], b), rf)} for b in COST_GRID},
            "start_sensitivity": {str(y): {"cagr": cagr(r.loc[f"{y}-01":]), "sharpe": sharpe(r.loc[f"{y}-01":], rf)}
                                  for y in START_YEARS},
            "stress": stress(r),
            "crash": {"avg_month": float(cr.mean()) if crash_n else None,
                      "compounded": float((1 + cr).prod() - 1) if crash_n else None,
                      "avg_diff_vs_6040": float(cr.mean() - net["60/40"][flag.values].mean()) if crash_n else None,
                      "avg_diff_ci95": None if n == "60/40" else crash_ci.get(n)},
            "nonconverged_months": int(bad[n]),
            "last_weights": dict(zip(TICKERS, weights[n].iloc[-1].round(4).tolist())),
        })

    wealth = (1 + net).cumprod()
    roll = excess.rolling(36).mean() / excess.rolling(36).std() * np.sqrt(12)
    result = {
        "as_of": f"{dates[-1]:%Y-%m-%d}",
        "spec": "docs/portfolio-spec.md",
        "source": "Yahoo Finance via yfinance, adjusted closes (total return); ^IRX for the risk free rate",
        "period": {"start": f"{dates[0]:%Y-%m}", "end": f"{dates[-1]:%Y-%m}", "months": len(dates)},
        "settings": {"tickers": TICKERS, "lookback_months": LOOKBACK, "cost_bps": COST_BPS, "max_weight_ms": MAX_WEIGHT,
                     "ewma_half_life": HALF_LIFE, "trend_months": TREND_MONTHS, "bootstrap": {
                         "method": "stationary", "mean_block": BLOCK, "resamples": N_BOOT, "seed": SEED},
                     "n_trials": N_TRIALS, "dsr_monthly_sr_threshold": sr_star,
                     "stress_periods": {k: list(v) for k, v in STRESS_PERIODS.items()}},
        "v1_replication": repl,
        "strategies": strategies,
        "decomposition": decomposition,
        "crash_test": {"definition": "IVV and AGG trailing 12 month returns both negative",
                       "months": crash_n, "episodes": episodes,
                       "ci_reported": len(episodes) >= 3},
        "series": {"dates": [f"{d:%Y-%m}" for d in dates],
                   "growth": {n: wealth[n].round(4).tolist() for n in names},
                   "drawdown": {n: (wealth[n] / wealth[n].cummax() - 1).round(4).tolist() for n in names},
                   "rolling_sharpe_36m": {n: [None if np.isnan(v) else round(float(v), 3) for v in roll[n]] for n in names}},
    }
    returns_csv = net.assign(RiskFree=rf_oos)
    returns_csv.index = returns_csv.index.strftime("%Y-%m")
    return rnd(result), returns_csv


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true", help="download prices first")
    args = ap.parse_args()
    price_path = OUT / "prices.csv"
    if args.fetch:
        prices = fetch_prices()
    elif price_path.exists():
        prices = pd.read_csv(price_path, index_col=0, parse_dates=True)
    else:
        fail(f"{price_path} does not exist; run with --fetch")

    result, returns_csv = run(prices)     # fails before writing anything if data or replication is off

    OUT.mkdir(parents=True, exist_ok=True)
    if args.fetch:
        prices.to_csv(price_path)
    returns_csv.to_csv(OUT / "strategy_returns.csv", float_format="%.8f", index_label="month")
    (OUT / "results.json").write_text(json.dumps(result, indent=1) + "\n")
    print(f"Wrote {OUT} (as of {result['as_of']}, {result['period']['months']} months)")
    for s in result["strategies"]:
        lo, hi = s["sharpe_ci95"]
        print(f"  {s['name']:<13} Sharpe {s['sharpe']:.2f} [{lo:.2f}, {hi:.2f}]  CAGR {s['cagr']:.1%}")


if __name__ == "__main__":
    main()
