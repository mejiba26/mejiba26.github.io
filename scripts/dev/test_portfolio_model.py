"""
Offline tests for scripts/portfolio_model.py. SYNTHETIC DATA, no network, writes nothing.

    python scripts/dev/test_portfolio_model.py

Checks: weights sum to 1, long only, caps respected; weights at month t use only data before t;
turnover and cost accounting (including the trend rule's cash leg); closed form two asset minimum
variance; equal risk contributions; a bootstrap interval covers a known Sharpe; decomposition shares
sum to 1; fixed seed gives identical output; bad data stops the run.
"""
import sys
import traceback
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import portfolio_model as P  # noqa: E402

T = P.TICKERS


def synth_rets(months=60, seed=1):
    rng = np.random.default_rng(seed)
    mu = np.linspace(0.002, 0.009, len(T))
    sd = np.linspace(0.01, 0.06, len(T))
    common = rng.normal(0, 0.02, (months, 1))
    x = mu + common * np.linspace(0.1, 1.0, len(T)) + rng.normal(0, 1, (months, len(T))) * sd
    idx = pd.date_range("2005-03-31", periods=months, freq="ME")
    return pd.DataFrame(x, idx, T), pd.Series(0.001, idx)


def synth_prices(seed=2):
    """Daily prices for all tickers plus ^IRX, 2005-02 to 2011-12."""
    rng = np.random.default_rng(seed)
    d = pd.bdate_range("2005-02-01", "2011-12-30")
    px = pd.DataFrame(100 * np.exp(np.cumsum(rng.normal(0.0002, 0.008, (len(d), len(T))), 0)), d, T)
    px[P.RATE_TICKER] = 2.0
    return px


def test_weights_valid():
    rets, rf = synth_rets()
    for start in (0, 10, 24):
        h, r = rets.iloc[start:start + P.LOOKBACK], rf.iloc[start:start + P.LOOKBACK]
        for name, fn in P.STRATEGIES.items():
            w, ok = fn(h, r)
            assert ok, f"{name} did not converge"
            assert (w >= -1e-12).all(), f"{name} has a short weight {w.min()}"
            if name == "Trend 10M":
                assert w.sum() <= 1 + 1e-12 and set(np.round(w * 9, 9)) <= {0.0, 1.0}, f"{name} weights {w}"
            else:
                assert abs(w.sum() - 1) < 1e-9, f"{name} sums to {w.sum()}"
            if name.startswith("MS-"):
                assert w.max() <= P.MAX_WEIGHT + 1e-9, f"{name} breaks the 40% cap: {w.max()}"


def test_no_lookahead():
    rets, rf = synth_rets(months=56)
    k = 46                                     # corrupt month k onward; weights for months <= k must not change
    bad = rets.copy()
    bad.iloc[k:] = bad.iloc[k:] * -7 + 0.05
    for name, fn in P.STRATEGIES.items():
        _, _, w1, _ = P.walk_forward(rets, rf, fn)
        _, _, w2, _ = P.walk_forward(bad, rf, fn)
        upto = rets.index[k]
        assert np.allclose(w1.loc[:upto].values, w2.loc[:upto].values, atol=1e-12), f"{name} looks ahead"


def test_turnover_and_costs():
    n = P.LOOKBACK + 2
    idx = pd.date_range("2005-03-31", periods=n, freq="ME")
    rets = pd.DataFrame(0.0, idx, T)
    rets.iloc[P.LOOKBACK, 0], rets.iloc[P.LOOKBACK, 1] = 0.10, -0.10
    rf = pd.Series(0.01, idx)
    half = np.zeros(len(T))
    half[:2] = 0.5
    g, t, _, _ = P.walk_forward(rets, rf, lambda h, r: (half, True))
    assert np.isclose(t.iloc[0], 1.0) and np.isclose(g.iloc[0], 0.0)          # buys everything from cash
    assert np.isclose(t.iloc[1], 0.1), t.iloc[1]                                # drifted to 55/45, back to 50/50
    net = P.net_of(g, t, 10)
    assert np.isclose(net.iloc[0], -0.001) and np.isclose(net.iloc[1], -0.0001)
    # cash leg: 50% in asset 0, 50% cash earning rf; selling to cash is one leg
    one = np.zeros(len(T))
    one[0] = 0.5
    g, t, _, _ = P.walk_forward(rets, rf, lambda h, r: (one, True))
    assert np.isclose(g.iloc[0], 0.5 * 0.10 + 0.5 * 0.01), g.iloc[0]
    assert np.isclose(t.iloc[0], 0.5)
    drift = 0.5 * 1.10 / (0.5 * 1.10 + 0.5 * 1.01)
    assert np.isclose(t.iloc[1], drift - 0.5), (t.iloc[1], drift - 0.5)


def test_two_asset_min_variance():
    rng = np.random.default_rng(3)
    a = rng.normal(0, 0.03, 36)
    b = 0.3 * a + rng.normal(0, 0.04, 36)
    h = pd.DataFrame({"A": a, "B": b})
    c = h.cov().values
    w1 = (c[1, 1] - c[0, 1]) / (c[0, 0] + c[1, 1] - 2 * c[0, 1])
    w, ok = P.rule_mv(h, pd.Series(0.0, h.index), P.cov_sample(h))
    assert ok and 0 < w1 < 1 and abs(w[0] - w1) < 1e-5, (w, w1)


def test_risk_parity_equal_contributions():
    rets, rf = synth_rets()
    h = rets.iloc[:36]
    for est in P.ESTIMATORS.values():
        c = est(h)
        w, ok = P.rule_rp(h, rf.iloc[:36], c)
        rc = w * (c @ w)
        assert ok and rc.max() / rc.min() - 1 < 1e-6, rc


def test_bootstrap_covers_known_sharpe():
    rng = np.random.default_rng(4)
    x = rng.normal(0.008, 0.04, (240, 1))
    true_sr = 0.008 / 0.04 * np.sqrt(12)
    bs = P.boot_sharpes(x, P.stationary_indices(240, 2000, np.random.default_rng(5)))
    lo, hi = P.ci(bs[:, 0])
    assert lo < true_sr < hi, (lo, true_sr, hi)


def test_decomposition_shares():
    S = np.random.default_rng(6).normal(0.5, 0.2, (100, 3, 3))
    assert np.allclose(sum(P.anova_shares(S)), 1)
    rule_only = np.array([[0.1] * 3, [0.5] * 3, [0.9] * 3])
    r, e, i = P.anova_shares(rule_only)
    assert np.isclose(r, 1) and np.isclose(e, 0) and np.isclose(i, 0)


def test_deterministic_seed():
    x = np.random.default_rng(7).normal(0.005, 0.03, (120, 3))
    a = P.boot_sharpes(x, P.stationary_indices(120, 500, np.random.default_rng(P.SEED)))
    b = P.boot_sharpes(x, P.stationary_indices(120, 500, np.random.default_rng(P.SEED)))
    assert np.array_equal(a, b)


def test_bad_data_fails():
    px = synth_prices()
    rets, _ = P.monthly_returns(px)
    P.validate(px, rets)                                  # clean data passes
    gap = px.copy()
    gap.loc["2008-05-01":"2008-05-31", "IVV"] = np.nan
    neg = px.copy()
    neg.loc["2009-03-31":, "IVV"] = -1.0
    stale = px.copy()
    stale.loc["2009-06-26":"2009-06-30", "EEM"] = np.nan        # last 3 trading days of June missing
    cases = {"missing ticker": px.drop(columns="IAU"), "missing month": gap, "bad price": neg, "stale month end": stale}
    for label, bad in cases.items():
        try:
            r, _ = P.monthly_returns(bad)
            P.validate(bad, r)
        except SystemExit as e:
            assert str(e).startswith("PORTFOLIO MODEL FAILED"), f"{label}: unexpected message {e}"
            continue
        raise AssertionError(f"{label} was not caught")


def test_month_end_holiday_keeps_month():
    px = synth_prices()
    cut = px[px.index < "2011-12-30"]                      # data ends the day before the last weekday
    assert P.monthly_returns(cut, drop_partial=False)[0].index[-1] == pd.Timestamp("2011-12-31")
    assert P.monthly_returns(cut)[0].index[-1] == pd.Timestamp("2011-11-30")   # v1 rule, kept for replication


def test_trend_direction():
    idx = pd.date_range("2005-03-31", periods=P.LOOKBACK, freq="ME")
    h = pd.DataFrame(0.005, idx, T)
    h["IVV"], h["AGG"] = 0.02, -0.02                       # IVV only rises, AGG only falls
    w, _ = P.w_trend(h, pd.Series(0.0, idx))
    assert np.isclose(w[T.index("IVV")], 1 / 9) and w[T.index("AGG")] == 0, w


def test_psr_and_dsr_known_values():
    # Two point symmetric returns: skew 0, raw kurtosis 1, so PSR reduces to Phi(SR * sqrt(n - 1)).
    x = 0.01 + 0.04 * np.tile([1.0, -1.0], 60)
    sr = x.mean() / x.std(ddof=1)
    assert np.isclose(P.psr(x), P.norm.cdf(sr * np.sqrt(len(x) - 1)))
    # DSR threshold with unit variance across trials approximates the expected max of N standard normals.
    mc = np.random.default_rng(8).standard_normal((4000, 1000)).max(1).mean()
    thr = P.dsr_threshold(np.array([-1, 1]) / np.sqrt(2), 1000)
    assert abs(thr - mc) < 0.1, (thr, mc)


def test_crash_episodes():
    idx = pd.date_range("2005-01-31", periods=68, freq="ME")
    rets = pd.DataFrame(0.01, idx, T)
    rets.iloc[32, :] = rets.iloc[62, :] = -0.30            # each makes 12 months of negative trailing returns
    dates = idx[12:]
    flag, ep = P.crash_months(rets, dates)
    assert ep == [{"start": "2007-09", "end": "2008-08", "months": 12},
                  {"start": "2010-03", "end": "2010-08", "months": 6}], ep   # second runs to the last month
    assert flag.sum() == 18



def test_reproduction_compare():
    """The workflow gate: within 0.0005 or 0.05% passes; a bigger drift, a missing key or a nested drift fails."""
    import copy
    import check_portfolio_schema as C
    ref = {"as_of": "2026-10-31", "strategies": [{"name": "60/40", "sharpe": 0.71, "sharpe_ci95": [0.23, 1.26]}],
           "decomposition": {"rule": {"point": 0.915, "ci95": [0.56, 0.99]}}, "series": {"growth": {"60/40": [1.0, 2.0, 3.5]}}}

    def drift(edit):
        r = copy.deepcopy(ref)
        edit(r)
        return r

    def fails(r, label):
        try:
            C.compare(ref, r)
        except SystemExit as e:
            assert str(e).startswith("SCHEMA CHECK FAILED"), e
            return
        raise AssertionError(f"{label} passed the comparison")

    assert C.compare(ref, copy.deepcopy(ref)) == 0
    near = drift(lambda r: r["strategies"][0].__setitem__("sharpe", 0.71 + 0.0003))
    assert abs(C.compare(ref, near) - 0.0003) < 1e-12
    fails(drift(lambda r: r["strategies"][0].__setitem__("sharpe", 0.71 + 0.003)), "a 0.003 drift")
    fails(drift(lambda r: r["decomposition"].pop("rule")), "a missing key")
    fails(drift(lambda r: r["decomposition"]["rule"]["ci95"].__setitem__(1, 0.99 + 0.003)), "a nested drift")
    fails(drift(lambda r: r["series"]["growth"]["60/40"].__setitem__(2, 3.5 + 0.01)), "a drift deep in a series")


def main():
    tests = [v for k, v in globals().items() if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL {t.__name__}\n{traceback.format_exc(limit=1)}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
