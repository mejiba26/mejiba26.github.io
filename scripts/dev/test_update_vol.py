"""
Offline tests for scripts/update_vol.py. SYNTHETIC PRICES, NOT A TRACK RECORD. No network.

    python scripts/dev/test_update_vol.py             # run every test
    python scripts/dev/test_update_vol.py --mutants   # break the script on purpose, one thing at a
                                                      # time, and confirm the tests notice each break

The tests drive the real main() with the Yahoo download and the clock replaced, and data/vol
replaced by a temp folder. Nothing here touches data/vol or reads real market history.
"""
import contextlib
import csv
import importlib.util
import io
import json
import math
import os
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import check_vol_schema as S  # noqa: E402

# The mutant runner points this at a deliberately broken copy.
_spec = importlib.util.spec_from_file_location("update_vol", os.environ.get("UPDATE_VOL_PATH", SCRIPTS / "update_vol.py"))
U = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(U)

UTC = timezone.utc
START = date.fromisoformat(U.START_DATE)
FRIDAY = U.prev_trading_day(START)          # the close that produces the first forecast
ORIG = {k: getattr(U, k) for k in ("fetch", "utc_now", "LIVE_DIR", "HISTORY_START", "DOWNLOAD_WAIT_S")}


def trading_days(a, b):
    out, d = [], a
    while d <= b:
        if U.is_trading_day(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def evening(d):
    return datetime(d.year, d.month, d.day, 23, 10, tzinfo=UTC)   # after the close, the scheduled run time


def synthetic_ranges(n, seed):
    """Persistent log ranges with a slow and a fast part, roughly like real ones."""
    rng = np.random.default_rng(seed)
    slow = np.zeros(n)
    for i in range(1, n):
        slow[i] = 0.985 * slow[i - 1] + 0.06 * rng.normal()
    return np.exp(-4.6 + slow + 0.30 * rng.normal(size=n))


class Sim:
    """A fake Yahoo plus a fake clock. fetch() serves every bar whose session has opened, so the bar of a
    day still in progress is visible, exactly the case the partial bar guard exists for."""

    def __init__(self, tmp, seed=11, last=date(2027, 3, 31)):
        self.days = trading_days(date(2021, 1, 4), last)
        self.opens = [U.open_utc(d) for d in self.days]
        idx = pd.DatetimeIndex([pd.Timestamp(d) for d in self.days])
        low = {t: np.full(len(idx), 50.0 * (j + 1)) for j, t in enumerate(U.UNIVERSE)}
        high = {t: low[t] * np.exp(synthetic_ranges(len(idx), seed + j)) for j, t in enumerate(U.UNIVERSE)}
        self.high, self.low = pd.DataFrame(high, index=idx), pd.DataFrame(low, index=idx)
        self.now, self.out = None, Path(tmp) / "vol"
        U.LIVE_DIR, U.HISTORY_START, U.DOWNLOAD_WAIT_S = self.out, "2021-01-04", 0
        U.fetch, U.utc_now = self.fetch, lambda: self.now

    def fetch(self):
        n = sum(o <= self.now for o in self.opens)
        return self.high.iloc[:n].copy(), self.low.iloc[:n].copy()

    def run(self, at):
        self.now = at
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            U.main()
        return buf.getvalue()

    def snap(self):
        return {f: (self.out / f).read_bytes() for f in U.FILES} if self.out.exists() else None

    def rows(self, name):
        with open(self.out / name, newline="") as f:
            return list(csv.DictReader(f))

    def scores(self):
        return json.loads((self.out / "scores.json").read_text())


def restore():
    for k, v in ORIG.items():
        setattr(U, k, v)


def numeric(rows, keys):
    return [tuple(r[k] for k in keys) for r in rows]


def exits(fn):
    try:
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            fn()
    except SystemExit as e:
        return str(e.code)
    return None


# ------------------------------------------------------------------ independent second implementations
def indep_quantile(values, q):
    """Linear interpolation between order statistics, written out by hand."""
    v = sorted(values)
    pos = q * (len(v) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (pos - lo)


def indep_ewma_point(y, i, lam=0.94, seed_n=22):
    """Closed form: the seed decays, and each later day carries weight (1 - lam) * lam ** age."""
    total = lam ** (i - (seed_n - 1)) * (sum(y[:seed_n]) / seed_n)
    for j in range(seed_n, i + 1):
        total += (1 - lam) * lam ** (i - j) * y[j]
    return total


_indep_beta = {}


def indep_har_point(series, i):
    """pandas rolling means, the refit week found with timestamps, and the normal equations instead of lstsq."""
    day = series.index[i]
    monday = day - pd.Timedelta(days=day.weekday())
    if monday not in _indep_beta:
        hist = series[series.index < monday]
        df = pd.DataFrame({"d": hist, "w": hist.rolling(5).mean(), "m": hist.rolling(22).mean(), "t": hist.shift(-1)}).dropna()
        x = np.column_stack([np.ones(len(df)), df["d"], df["w"], df["m"]])
        _indep_beta[monday] = np.linalg.solve(x.T @ x, x.T @ df["t"].to_numpy())
    known = series.iloc[:i + 1]
    b = _indep_beta[monday]
    return b[0] + b[1] * known.iloc[-1] + b[2] * known.iloc[-5:].mean() + b[3] * known.iloc[-22:].mean()


def indep_interval(point_fn, y, i, n_resid=500):
    resid = [y[s] - point_fn(s - 1) for s in range(i - n_resid + 1, i + 1)]
    p = point_fn(i)
    return tuple(max(p + indep_quantile(resid, q), 0.0) for q in (0.5, 0.1, 0.9))


def indep_dm(a, b, lag=5):
    d = [abs(x) - abs(z) for x, z in zip(a, b)]
    n = len(d)
    mean = sum(d) / n
    gamma = lambda k: sum((d[t] - mean) * (d[t - k] - mean) for t in range(k, n)) / n
    lrv = gamma(0) + 2 * sum((1 - k / (lag + 1)) * gamma(k) for k in range(1, lag + 1))
    stat = mean / math.sqrt(lrv / n)
    return stat, math.erfc(abs(stat) / math.sqrt(2))


# ------------------------------------------------------------------ tests
def test_first_forecast_is_the_friday_before_start(tmp):
    sim = Sim(tmp)
    thursday = U.prev_trading_day(FRIDAY)
    out = sim.run(evening(thursday))
    assert "Download OK" in out and "Before the first forecast date" in out, out
    assert not sim.out.exists(), "nothing may be written before the first forecast date"
    # Friday while the market is open: Yahoo shows a partial Friday bar, which must not be used.
    sim.run(datetime(FRIDAY.year, FRIDAY.month, FRIDAY.day, 16, 0, tzinfo=UTC))
    assert not sim.out.exists(), "a partial bar produced a forecast"
    sim.run(evening(FRIDAY))
    rows = sim.rows("forecasts.csv")
    assert len(rows) == len(U.UNIVERSE) * len(U.MODELS) == 16, len(rows)
    assert {r["forecast_for_date"] for r in rows} == {U.START_DATE}, "the first forecast must be for START_DATE"
    assert {r["status"] for r in rows} == {"ok"}
    assert sim.rows("realized.csv") == [], "nothing is graded before START_DATE"
    sc = sim.scores()
    assert sc["as_of"] == FRIDAY.isoformat() and sc["start_date"] == U.START_DATE
    assert all(sc["counts"][t]["graded"] == 0 and sc["counts"][t]["pending"] == 1 for t in U.UNIVERSE)
    S.check(sim.out)


def test_rerun_changes_nothing(tmp):
    sim = Sim(tmp)
    days = trading_days(FRIDAY, FRIDAY + timedelta(days=12))
    for d in days:
        sim.run(evening(d))
    before = sim.snap()
    assert "Nothing new" in sim.run(evening(days[-1]) + timedelta(minutes=30))
    assert "Nothing new" in sim.run(evening(days[-1]) + timedelta(hours=9))   # next morning, before the open
    assert sim.snap() == before, "a rerun changed a file"
    assert len(sim.rows("forecasts.csv")) == 16 * len(days)


def test_split_run_equals_one_continuous_run(tmp):
    days = trading_days(FRIDAY, FRIDAY + timedelta(days=45))
    a = Sim(Path(tmp) / "a")
    for d in days:
        a.run(evening(d))
    fa, ra = a.rows("forecasts.csv"), a.rows("realized.csv")
    restore()
    b = Sim(Path(tmp) / "b")
    b.run(evening(days[0]))
    b.run(evening(days[-1]))                     # one run covers every day in between
    fb, rb = b.rows("forecasts.csv"), b.rows("realized.csv")
    keys = ("forecast_for_date", "ticker", "model", "median", "p10", "p90")
    assert numeric(fa, keys) == numeric(fb, keys), "forecast values depend on how the days were split across runs"
    rk = ("date", "ticker", "high", "low", "log_range")
    assert numeric(ra, rk) == numeric(rb, rk), "realized rows depend on how the days were split across runs"
    assert len(ra) == 4 * (len(days) - 1)
    # The catch up run made its forecasts after the fact, so they are late and never scored.
    assert [r["status"] for r in fb].count("ok") == 32 and a.scores()["counts"]["SPY"]["graded"] == len(days) - 1
    assert b.scores()["counts"]["SPY"]["graded"] == 1 and b.scores()["counts"]["SPY"]["late"] == len(days) - 2


def test_no_look_ahead(tmp):
    days = trading_days(date(2021, 1, 4), date(2027, 3, 31))
    dates = np.array(days, dtype="datetime64[D]")
    y = synthetic_ranges(len(days), 5)
    rng = np.random.default_rng(1)
    for i in (1400, 1403, 1451, len(days) - 30):
        base, _ = U.forecast(dates, y, i)
        changed = y.copy()
        changed[i + 1:] = changed[i + 1:] * rng.uniform(0.2, 5.0, len(y) - i - 1)   # rewrite the future
        again, _ = U.forecast(dates, changed, i)
        assert again == base, f"the forecast made on day {i} changed when later data changed"
        short, _ = U.forecast(dates[:i + 1], y[:i + 1], i)
        assert short == base, "the forecast depends on how much later history was passed in"
        earlier = y.copy()
        earlier[i] *= 1.5                        # sanity: it does depend on the last known day
        assert U.forecast(dates, earlier, i)[0] != base


def test_intervals_cover_80_percent_on_a_known_distribution(tmp):
    days = trading_days(date(2015, 1, 2), date(2025, 12, 31))
    dates = np.array(days, dtype="datetime64[D]")
    rng = np.random.default_rng(42)
    mu, sigma = -4.5, 0.4
    y = np.exp(mu + sigma * rng.normal(size=len(days)))       # independent lognormal days
    origins = range(1400, len(days) - 1)
    hit = {m: 0 for m in U.MODEL_IDS}
    below = {m: 0 for m in U.MODEL_IDS}
    width = []
    for i in origins:
        fc, _ = U.forecast(dates, y, i)
        for m, (med, p10, p90) in fc.items():
            hit[m] += p10 <= y[i + 1] <= p90
            below[m] += y[i + 1] < med
        width.append(fc["roll22"][2] - fc["roll22"][1])
    n = len(origins)
    for m in U.MODEL_IDS:
        cover, under = hit[m] / n, below[m] / n
        assert abs(cover - 0.80) < 0.03, f"{m}: coverage {cover:.3f} over {n} days, expected about 0.80"
        assert abs(under - 0.50) < 0.04, f"{m}: actual was below the median {under:.3f} of the time, expected about 0.50"
    # Known answer for the rolling mean model: its error is one lognormal day minus the mean of 22 others.
    big = np.exp(mu + sigma * rng.normal(size=(200_000, 23)))
    err = big[:, 0] - big[:, 1:].mean(axis=1)
    true_width = np.quantile(err, 0.9) - np.quantile(err, 0.1)
    assert abs(np.mean(width) / true_width - 1) < 0.08, f"interval width {np.mean(width):.5f} vs true {true_width:.5f}"


def test_second_implementation_of_ewma_and_har_matches(tmp):
    days = trading_days(date(2021, 1, 4), date(2027, 3, 31))
    dates = np.array(days, dtype="datetime64[D]")
    y = synthetic_ranges(len(days), 9)
    series = pd.Series(y, index=pd.DatetimeIndex([pd.Timestamp(d) for d in days]))
    _indep_beta.clear()
    for i in (1380, 1466, len(days) - 3):       # a Friday, mid week and near the end, whatever they fall on
        fc, fit = U.forecast(dates, y, i)
        e = indep_interval(lambda s: indep_ewma_point(y, s), y, i)
        h = indep_interval(lambda s: indep_har_point(series, s), y, i)
        for name, mine, theirs in (("ewma", e, fc["ewma"]), ("har", h, fc["har"])):
            gap = max(abs(a - b) for a, b in zip(mine, theirs))
            assert gap < 1e-9, f"{name} on day {i}: independent {mine} vs script {theirs}"
        monday = series.index[i] - pd.Timedelta(days=series.index[i].weekday())
        assert max(abs(a - b) for a, b in zip(_indep_beta[monday], fit["coef"])) < 1e-7, "HAR coefficients differ"
        assert fit["fit_through"] == str(series.index[series.index < monday][-1].date())
    # The simple models by hand as well.
    i = 1500
    fc, _ = U.forecast(dates, y, i)
    naive = indep_interval(lambda s: y[s], y, i)
    roll = indep_interval(lambda s: sum(y[s - 21:s + 1]) / 22, y, i)
    assert max(abs(a - b) for a, b in zip(naive + roll, fc["naive"] + fc["roll22"])) < 1e-9


def test_late_forecasts_are_recorded_and_never_scored(tmp):
    sim = Sim(tmp)
    days = trading_days(FRIDAY, FRIDAY + timedelta(days=16))
    skipped = days[3]                             # no run that evening
    for d in days:
        if d == skipped:
            continue
        if d == days[4]:
            # The missed run finally happens the next morning, 5 minutes after the open: too late.
            sim.run(U.open_utc(d) + timedelta(minutes=5))
        sim.run(evening(d))
    rows = sim.rows("forecasts.csv")
    late = {r["forecast_for_date"] for r in rows if r["status"] == "late"}
    assert late == {days[4].isoformat()}, late
    assert len(rows) == 16 * len(days), "the late forecast must still be recorded"
    sc = sim.scores()
    for t in U.UNIVERSE:
        assert sc["counts"][t]["late"] == 1 and sc["counts"][t]["graded"] == len(days) - 2, sc["counts"][t]
        assert sc["scores"][t]["har"]["since_start"]["n"] == len(days) - 2
    S.check(sim.out)
    # The boundary: one second before the open is on time, the open itself is late.
    restore()
    for offset, want in ((-1, "ok"), (0, "late")):
        with tempfile.TemporaryDirectory() as t2:
            s2 = Sim(t2)
            s2.run(evening(FRIDAY))
            s2.run(evening(days[1]))
            s2.run(U.open_utc(days[3]) + timedelta(seconds=offset))   # the evening run for days[2] was missed
            got = {r["status"] for r in s2.rows("forecasts.csv") if r["forecast_for_date"] == days[3].isoformat()}
            assert got == {want}, (offset, got)
        restore()


def test_holidays_and_the_trading_calendar(tmp):
    closed_2026 = {date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3), date(2026, 5, 25),
                   date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7), date(2026, 11, 26), date(2026, 12, 25)}
    closed_2027 = {date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15), date(2027, 3, 26), date(2027, 5, 31),
                   date(2027, 6, 18), date(2027, 7, 5), date(2027, 9, 6), date(2027, 11, 25), date(2027, 12, 24)}
    assert U.holidays(2026) == closed_2026, sorted(U.holidays(2026) ^ closed_2026)
    assert U.holidays(2027) == closed_2027, sorted(U.holidays(2027) ^ closed_2027)
    assert date(2027, 12, 31) not in U.holidays(2027) and date(2028, 1, 1) not in U.holidays(2028)   # Saturday New Year
    assert U.open_utc(date(2026, 11, 23)).hour == 14 and U.open_utc(date(2027, 6, 1)).hour == 13
    assert FRIDAY == date(2026, 11, 20) and START.weekday() == 0
    sim = Sim(tmp)
    for d in trading_days(FRIDAY, date(2026, 11, 27)):
        sim.run(evening(d))
    targets = sorted({r["forecast_for_date"] for r in sim.rows("forecasts.csv")})
    assert targets == ["2026-11-23", "2026-11-24", "2026-11-25", "2026-11-27", "2026-11-30"], targets   # Thanksgiving skipped


def test_bad_data_fails_loudly_and_writes_nothing(tmp):
    sim = Sim(tmp)
    days = trading_days(FRIDAY, FRIDAY + timedelta(days=6))
    for d in days[:3]:
        sim.run(evening(d))
    before = sim.snap()
    good_fetch = sim.fetch

    def one_ticker_blank():
        h, l = good_fetch()
        h.iloc[-1, h.columns.get_loc("XLE")] = np.nan
        return h, l
    U.fetch = one_ticker_blank
    msg = exits(lambda: sim.run(evening(days[3])))
    assert msg and "Missing or impossible High/Low for XLE" in msg, msg
    assert sim.snap() == before

    def old_bar_upside_down():
        h, l = good_fetch()
        h.iloc[700, h.columns.get_loc("ITA")] = l.iloc[700, l.columns.get_loc("ITA")] * 0.99   # High below Low, years ago
        return h, l
    U.fetch = old_bar_upside_down
    msg = exits(lambda: sim.run(evening(days[3])))
    assert msg and "Missing or impossible High/Low for ITA" in msg, msg
    assert sim.snap() == before

    def yahoo_is_a_day_behind():
        h, l = good_fetch()
        return h.iloc[:-1], l.iloc[:-1]
    U.fetch = yahoo_is_a_day_behind
    msg = exits(lambda: sim.run(evening(days[3])))
    assert msg and "no bar" in msg, msg
    assert sim.snap() == before

    # The real fetch(): a download that lacks a ticker, and one that raises, both stop the run.
    U.fetch = ORIG["fetch"]
    calls = []

    def without_xle(tickers, **kw):
        calls.append(kw)
        h, l = good_fetch()
        return pd.concat({"High": h.drop(columns="XLE"), "Low": l.drop(columns="XLE")}, axis=1)
    real_download, U.yf.download = U.yf.download, without_xle
    try:
        msg = exits(lambda: sim.run(evening(days[3])))
        assert msg and "['XLE']" in msg and len(calls) == U.DOWNLOAD_TRIES, (msg, len(calls))
        assert calls[0].get("threads") is False and calls[0].get("auto_adjust") is False
        U.yf.download = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("database is locked"))
        msg = exits(lambda: sim.run(evening(days[3])))
        assert msg and "failed" in msg, msg
    finally:
        U.yf.download = real_download
    assert sim.snap() == before, "a failed run changed a file"
    # A clean run afterwards carries on, here through the real fetch() with a Yahoo shaped frame.
    def yahoo_shaped(tickers, **kw):
        h, l = good_fetch()
        return pd.concat({"Close": l, "High": h, "Low": l, "Open": l}, axis=1)
    U.yf.download = yahoo_shaped
    try:
        sim.run(evening(days[3]))
    finally:
        U.yf.download = real_download
    assert len(sim.rows("forecasts.csv")) == 16 * 4


def test_no_backfill(tmp):
    sim = Sim(tmp)
    msg = exits(lambda: sim.run(evening(START + timedelta(days=U.BACKFILL_GRACE_DAYS + 1))))
    assert msg and "Refusing" in msg, msg
    assert not sim.out.exists()
    restore()
    # Inside the grace period a first run is allowed, but what it missed is late, never scored.
    s2 = Sim(Path(tmp) / "b")
    tuesday = U.next_trading_day(START)
    s2.run(evening(tuesday))
    rows = s2.rows("forecasts.csv")
    assert min(r["forecast_for_date"] for r in rows) == U.START_DATE
    assert {r["forecast_for_date"]: r["status"] for r in rows} == {
        START.isoformat(): "late", tuesday.isoformat(): "late", U.next_trading_day(tuesday).isoformat(): "ok"}


def test_scores_come_from_the_recorded_files_only(tmp):
    sim = Sim(tmp)
    days = trading_days(FRIDAY, FRIDAY + timedelta(days=100))
    for d in days:
        sim.run(evening(d))
    sc = sim.scores()
    rebuilt = U.build_scores(sim.rows("forecasts.csv"), sim.rows("realized.csv"), sc["as_of"], sc["generated_utc"])
    assert json.loads(json.dumps(rebuilt)) == sc, "scores.json is not a pure function of the two CSV files"
    # Recompute one cell by hand with pandas.
    f = pd.read_csv(sim.out / "forecasts.csv")
    r = pd.read_csv(sim.out / "realized.csv").rename(columns={"date": "forecast_for_date"})
    j = f[(f.ticker == "QQQ") & (f.model == "ewma") & (f.status == "ok")].merge(r, on=["forecast_for_date", "ticker"])
    n = len(j)
    assert n == len(days) - 1 > 60
    want = dict(
        mae=(j.log_range - j["median"]).abs().mean(),
        pinball10=np.where(j.log_range >= j.p10, 0.1 * (j.log_range - j.p10), 0.9 * (j.p10 - j.log_range)).mean(),
        pinball90=np.where(j.log_range >= j.p90, 0.9 * (j.log_range - j.p90), 0.1 * (j.p90 - j.log_range)).mean(),
        coverage=((j.log_range >= j.p10) & (j.log_range <= j.p90)).mean())
    got = sc["scores"]["QQQ"]["ewma"]["since_start"]
    assert got["n"] == n and all(abs(got[k] - want[k]) < 1e-6 for k in want), (got, want)
    last60 = j.tail(60)
    assert sc["scores"]["QQQ"]["ewma"]["rolling60"]["n"] == 60
    assert abs(sc["scores"]["QQQ"]["ewma"]["rolling60"]["mae"] - (last60.log_range - last60["median"]).abs().mean()) < 1e-6
    assert abs(sc["calibration"]["ewma"]["at_or_below_p90"] - np.mean([
        (g.log_range <= g.p90).mean() * len(g) for _, g in f[(f.model == "ewma") & (f.status == "ok")].merge(r, on=["forecast_for_date", "ticker"]).groupby("ticker")]) * 4 / (4 * n)) < 1e-6
    assert all(v is None for v in sc["dm_test"].values()), "no test before 250 graded days"
    assert sc["latest"]["forecast_for_date"] == U.next_trading_day(days[-1]).isoformat() and len(sc["latest"]["rows"]) == 16
    S.check(sim.out)


def test_diebold_mariano_and_the_verdict_rule(tmp):
    rng = np.random.default_rng(3)
    a, b = rng.normal(0, 1.0, 250), rng.normal(0, 1.2, 250)
    stat, p = U.diebold_mariano(a, b)
    istat, ip = indep_dm(list(a), list(b))
    assert abs(stat - istat) < 1e-9 and abs(p - ip) < 1e-12, (stat, istat, p, ip)
    assert U.diebold_mariano(a, a) == (None, None)

    # Recorded rows where the truth is known: SPY has HAR clearly better, QQQ a tie, ITA has HAR clearly worse.
    days = trading_days(START, START + timedelta(days=420))[:260]
    noise = {"SPY": dict(har=0.2, naive=1.0, roll22=0.6), "QQQ": dict(har=0.6, naive=1.0, roll22=0.6),
             "ITA": dict(har=1.5, naive=1.0, roll22=0.6), "XLE": dict(har=0.2, naive=0.5, roll22=0.9)}
    frows, rrows = [], []
    for d in days:
        for t in U.UNIVERSE:
            actual = 0.012 + 0.002 * rng.normal()
            rrows.append(dict(date=d.isoformat(), ticker=t, high="1", low="1", log_range=f"{actual:.6f}", recorded_utc="x"))
            for m in U.MODEL_IDS:
                med = actual + 0.004 * noise[t].get(m, 1.0) * rng.normal()
                frows.append(dict(forecast_for_date=d.isoformat(), ticker=t, model=m, median=f"{med:.6f}", p10=f"{med - .004:.6f}",
                                  p90=f"{med + .004:.6f}", generated_utc="x", status="ok"))
    cut = lambda rows, k, n: [r for r in rows if r[k] <= days[n - 1].isoformat()]
    early = U.build_scores(cut(frows, "forecast_for_date", 249), cut(rrows, "date", 249), "x", "x")
    assert all(v is None for v in early["dm_test"].values()), "the test must wait for 250 graded days"
    at250 = U.build_scores(cut(frows, "forecast_for_date", 250), cut(rrows, "date", 250), "x", "x")["dm_test"]
    at260 = U.build_scores(frows, rrows, "x", "x")["dm_test"]
    assert at250 == at260, "the test result must be frozen on the first 250 graded days"
    assert at250["SPY"]["har_beats"] is True and at250["SPY"]["baseline"] == "roll22" and at250["SPY"]["p_value"] < U.DM_ALPHA
    assert at250["QQQ"]["har_beats"] is False and at250["QQQ"]["p_value"] > U.DM_ALPHA
    assert at250["ITA"]["har_beats"] is False and at250["ITA"]["p_value"] < U.DM_ALPHA   # significant, but HAR is the worse one
    assert at250["XLE"]["baseline"] == "naive" and at250["XLE"]["n"] == 250 and at250["XLE"]["window_end"] == days[249].isoformat()


def test_schema_check_catches_damage(tmp):
    sim = Sim(tmp)
    for d in trading_days(FRIDAY, FRIDAY + timedelta(days=8)):
        sim.run(evening(d))
    S.check(sim.out)
    good = sim.snap()

    def damaged(name, edit):
        for f, b in good.items():
            (sim.out / f).write_bytes(b)
        (sim.out / name).write_text(edit((sim.out / name).read_text()))
        return exits(lambda: S.check(sim.out))

    def drop_key(key):
        def edit(text):
            data = json.loads(text)
            del data[key]
            return json.dumps(data)
        return edit
    for key in ("as_of", "latest", "scores", "calibration", "counts", "models", "start_date"):
        assert damaged("scores.json", drop_key(key)), f"removing {key} from scores.json went unnoticed"
    assert damaged("scores.json", lambda t: t[:-40])
    assert damaged("forecasts.csv", lambda t: t.replace("forecast_for_date", "date", 1))
    assert damaged("forecasts.csv", lambda t: t + t.splitlines()[-1] + "\n"), "a duplicate row went unnoticed"
    assert damaged("forecasts.csv", lambda t: "\n".join(t.splitlines()[:-1]) + "\n"), "a missing model row went unnoticed"
    assert damaged("realized.csv", lambda t: t.replace(",SPY,", ",SPX,", 1))
    (sim.out / "state.json").unlink()
    assert exits(lambda: S.check(sim.out))
    # Append only: a committed file must be a prefix of the new one.
    real_root, S.ROOT = S.ROOT, Path(tmp)
    try:
        for f, b in good.items():
            (sim.out / f).write_bytes(b)
        git = lambda *a: subprocess.run(["git", "-C", str(tmp), *a], capture_output=True, text=True, check=True)
        git("init", "-q")
        git("add", "-A")
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x")
        S.check(sim.out)
        text = (sim.out / "forecasts.csv").read_text().splitlines()
        first = text[1].split(",")
        first[6] = "2026-11-01T00:00:00Z"        # quietly backdate an old forecast
        (sim.out / "forecasts.csv").write_text("\n".join([text[0], ",".join(first)] + text[2:]) + "\n")
        msg = exits(lambda: S.check(sim.out))
        assert msg and "append only" in msg, msg
    finally:
        S.ROOT = real_root


TESTS = [v for k, v in list(globals().items()) if k.startswith("test_")]

# Each entry breaks one thing in a copy of update_vol.py. Every one must make at least one test fail.
MUTANTS = [
    ("look ahead: HAR is fitted on all the data passed in, and nothing is cut off at the forecast day",
     [("    dates, y = dates[:i + 1], y[:i + 1]\n", ""), ("beta = _har_fit(y, week, month, int(c))", "beta = _har_fit(y, week, month, len(y) - 2)")]),
    ("rerun: a close already used is forecast again", [("if d > after_origin:", "if d >= after_origin:")]),
    ("resume: a day already graded is recorded again", [("if d > after_real and d >= start:", "if d >= after_real and d >= start:")]),
    ("EWMA lambda 0.90 instead of 0.94", [("EWMA_LAMBDA = 0.94", "EWMA_LAMBDA = 0.90")]),
    ("HAR weekly term over 10 days instead of 5", [("HAR_WEEK = 5", "HAR_WEEK = 10")]),
    ("HAR refit every day instead of every Monday", [("return np.searchsorted(days, monday, side=\"left\") - 1", "return np.arange(len(days)) - 1")]),
    ("interval from the 20th and 80th percentiles", [("QUANTILES = (0.10, 0.50, 0.90)", "QUANTILES = (0.20, 0.50, 0.80)")]),
    ("residuals over 250 days instead of 500", [("RESID_N = 500", "RESID_N = 250")]),
    ("late forecasts marked ok", [('status = "late" if now >= open_utc(target) else "ok"', 'status = "ok"')]),
    ("late forecasts scored", [('if any(ms[m]["status"] != "ok" for m in ms) or set(ms) != set(MODEL_IDS):', "if set(ms) != set(MODEL_IDS):")]),
    ("first forecast one day early (a backfill)", [("first_origin = prev_trading_day(start) ", "first_origin = prev_trading_day(prev_trading_day(start)) ")]),
    ("partial bar guard removed", [("if len(high) and now < bar_final_utc(high.index[-1].date()):", "if False:")]),
    ("backfill guard removed", [("if state is None and now.date() > start + timedelta(days=BACKFILL_GRACE_DAYS):", "if False:")]),
    ("missing data tolerated", [("if bad.any():", "if False:")]),
    ("coverage counts only the upper bound", [("coverage=r6(sum(lo <= a <= hi for a, _, lo, hi in rows) / n)", "coverage=r6(sum(a <= hi for a, _, lo, hi in rows) / n)")]),
    ("Thanksgiving treated as a trading day", [("_nth(y, 9, 0, 1), _nth(y, 11, 3, 4), ", "_nth(y, 9, 0, 1), ")]),
    ("Diebold Mariano without the Newey West terms", [("for k in range(1, lag + 1):\n        lrv +=", "for k in range(0):\n        lrv +=")]),
]


def run_tests():
    failed = []
    for t in TESTS:
        with tempfile.TemporaryDirectory() as tmp:
            try:
                t(tmp)
                print(f"PASS {t.__name__}")
            except BaseException as e:   # SystemExit from the script counts as a failure too
                failed.append(t.__name__)
                line = str(e).strip().splitlines()[0][:160] if str(e).strip() else ""
                print(f"FAIL {t.__name__}: {type(e).__name__}: {line}")
            finally:
                restore()
    print("ALL PASSED (synthetic prices, no network)" if not failed else f"{len(failed)} FAILED: {', '.join(failed)}")
    return failed


def run_mutants():
    src = (SCRIPTS / "update_vol.py").read_text()
    survived = []
    with tempfile.TemporaryDirectory() as tmp:
        for name, edits in MUTANTS:
            text = src
            for old, new in edits:
                if text.count(old) != 1:
                    sys.exit(f"update_vol.py no longer contains exactly one: {old!r}. Update MUTANTS.")
                text = text.replace(old, new)
            path = Path(tmp) / "update_vol.py"
            path.write_text(text)
            r = subprocess.run([sys.executable, __file__], capture_output=True, text=True, env={**os.environ, "UPDATE_VOL_PATH": str(path)})
            caught = [l.split()[1].rstrip(":") for l in r.stdout.splitlines() if l.startswith("FAIL")]
            if r.returncode == 0 or not caught:
                survived.append(name)
            print(f"{'CAUGHT ' if caught else 'MISSED '} {name}\n         failing: {', '.join(caught) or 'none'}")
    print(f"{len(MUTANTS) - len(survived)} of {len(MUTANTS)} deliberate breaks were caught." + (f" MISSED: {survived}" if survived else ""))
    return survived


if __name__ == "__main__":
    sys.exit(1 if (run_mutants() if "--mutants" in sys.argv else run_tests()) else 0)
