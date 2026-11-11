"""
Volatility forecast scorecard, rules from docs/vol-scorecard-spec.md (LOCKED).

Every weekday after the close this publishes forecasts of the next trading day's
log range, ln(High / Low), for each ticker and model, and grades earlier forecasts
against what happened. It makes no trading claims.

    pip install yfinance pandas numpy
    python scripts/update_vol.py

Files in data/vol/ (all created on the first run on or after the first forecast date):
    forecasts.csv   append only, one row per target day, ticker and model
    realized.csv    append only, the High, Low and log range each target day was graded on
    scores.json     rebuilt from the two CSV files only, with as_of
    state.json      where the last run stopped, plus the HAR coefficients in use

A rerun on the same day changes nothing. A run that cannot get clean data for every
ticker exits non zero and writes nothing. There is no replay mode on purpose: tests
and sample data use synthetic prices (scripts/dev/), never historical performance.

Model and scoring VALUES below are copied from the spec. Do not edit them here.
A change is a new version with a new start date and a new spec.
"""

import csv
import hashlib
import io
import json
import math
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

START_DATE = "2026-11-23"  # locked with docs/vol-scorecard-spec.md; must match the spec and index.html

ROOT = Path(__file__).resolve().parent.parent
LIVE_DIR = ROOT / "data" / "vol"

# ---- Spec values (unchanged) -------------------------------------------------
UNIVERSE = ["SPY", "QQQ", "ITA", "XLE"]
MODELS = [("naive", "Naive"), ("roll22", "Rolling mean"), ("ewma", "EWMA"), ("har", "HAR")]
ROLL_N = 22                 # rolling mean window, also the EWMA seed and the HAR monthly term
EWMA_LAMBDA = 0.94
HAR_WEEK = 5
HAR_MIN_DAYS = 750          # minimum days of history behind any HAR fit
RESID_N = 500               # walk forward residuals behind every interval
QUANTILES = (0.10, 0.50, 0.90)
ROLLING_SCORE_DAYS = 60
MIN_DAYS_RANKING = 60       # no ranking claims before this many graded days per ticker
DM_DAYS, DM_LAG, DM_ALPHA = 250, 5, 0.0125   # Diebold Mariano: sample, Newey West lag, Bonferroni level
# ---- Implementation rules fixed in the spec -----------------------------------
HISTORY_START = "2015-01-02"   # every run downloads from this same day, so the EWMA seed and the HAR window never move
MIN_HISTORY = 1300             # trading days needed before the first forecast (750 + 500 + room for the weekly refit)
BACKFILL_GRACE_DAYS = 5        # same guard as paper_trade.py: a first run this long after START_DATE is refused
DOWNLOAD_TRIES, DOWNLOAD_WAIT_S = 3, 5   # download mechanics only, not a rule

MODEL_IDS = [m for m, _ in MODELS]
NY = "America/New_York"
FORECAST_HEAD = ["forecast_for_date", "ticker", "model", "median", "p10", "p90", "generated_utc", "status"]
REALIZED_HEAD = ["date", "ticker", "high", "low", "log_range", "recorded_utc"]
FILES = ("forecasts.csv", "realized.csv", "scores.json", "state.json")


def utc_now() -> datetime:
    """The only clock in this file. Tests replace it."""
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- trading calendar
def _easter(y: int) -> date:
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    g = (8 * b + 13) // 25
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 19 * l) // 433
    month = (h + l - 7 * m + 90) // 25
    return date(y, month, (h + l - 7 * m + 33 * month + 19) % 32)


def _nth(y, month, weekday, n):
    d = date(y, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(days=7 * (n - 1))


def _observed(d):
    return d - timedelta(days=1) if d.weekday() == 5 else d + timedelta(days=1) if d.weekday() == 6 else d


def holidays(y: int) -> set:
    """Scheduled full day NYSE closures, by rule. Early closes are trading days."""
    h = {_nth(y, 1, 0, 3), _nth(y, 2, 0, 3), _easter(y) - timedelta(days=2),
         _nth(y, 9, 0, 1), _nth(y, 11, 3, 4), _observed(date(y, 7, 4)), _observed(date(y, 12, 25))}
    ny = date(y, 1, 1)
    if ny.weekday() == 6:
        h.add(date(y, 1, 2))
    elif ny.weekday() != 5:      # New Year's Day on a Saturday is not observed
        h.add(ny)
    last_may = date(y, 5, 31)
    h.add(last_may - timedelta(days=last_may.weekday()))   # Memorial Day, last Monday of May
    if y >= 2022:
        h.add(_observed(date(y, 6, 19)))
    return h


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in holidays(d.year)


def next_trading_day(d: date) -> date:
    d += timedelta(days=1)
    while not is_trading_day(d):
        d += timedelta(days=1)
    return d


def prev_trading_day(d: date) -> date:
    d -= timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def open_utc(d: date) -> datetime:
    """Regular open of trading day d, 09:30 New York (14:30 UTC in winter, 13:30 UTC in summer)."""
    return pd.Timestamp(f"{d.isoformat()} 09:30", tz=NY).tz_convert("UTC").to_pydatetime()


def bar_final_utc(d: date) -> datetime:
    """A daily bar is treated as final from 16:30 New York, the same cut paper_trade.py uses."""
    return pd.Timestamp(f"{d.isoformat()} 16:30", tz=NY).tz_convert("UTC").to_pydatetime()


# ---------------------------------------------------------------- models
def _roll_mean(y, k):
    out = np.full(len(y), np.nan)
    c = np.concatenate(([0.0], np.cumsum(y)))
    out[k - 1:] = (c[k:] - c[:-k]) / k
    return out


def _ewma(y):
    s = np.full(len(y), np.nan)
    s[ROLL_N - 1] = y[:ROLL_N].mean()          # seed: mean of the first 22 days
    for j in range(ROLL_N, len(y)):
        s[j] = EWMA_LAMBDA * s[j - 1] + (1.0 - EWMA_LAMBDA) * y[j]
    return s


def _week_cutoff(dates):
    """For each day, the index of the last trading day before the Monday of its calendar week.
    That is the data the Monday refit is allowed to use."""
    days = dates.astype("datetime64[D]").astype(np.int64)
    monday = days - (days + 3) % 7             # 1970-01-01 was a Thursday
    return np.searchsorted(days, monday, side="left") - 1


_FIT_CACHE = {}


def _har_fit(y, week, month, cutoff):
    """OLS of y[s+1] on 1, y[s], 5 day mean, 22 day mean, using every day through index cutoff."""
    if cutoff + 1 < HAR_MIN_DAYS:
        raise ValueError(f"HAR needs {HAR_MIN_DAYS} days of history, has {cutoff + 1}")
    key = (cutoff, hashlib.blake2b(y[:cutoff + 1].tobytes(), digest_size=16).digest())
    if key not in _FIT_CACHE:
        s = np.arange(ROLL_N - 1, cutoff)      # last row predicts y[cutoff], which is known at the cutoff
        x = np.column_stack([np.ones(len(s)), y[s], week[s], month[s]])
        _FIT_CACHE[key] = np.linalg.lstsq(x, y[s + 1], rcond=None)[0]
        if len(_FIT_CACHE) > 4000:
            _FIT_CACHE.pop(next(iter(_FIT_CACHE)))
    return _FIT_CACHE[key]


def model_points(dates, y, lo):
    """Point forecasts f[j] of y[j+1] for j >= lo, each using y[:j+1] only. Returns (points, last HAR fit)."""
    n = len(y)
    week, month = _roll_mean(y, HAR_WEEK), _roll_mean(y, ROLL_N)
    cut = _week_cutoff(dates)
    har = np.full(n, np.nan)
    beta = None
    for c in np.unique(cut[lo:]):
        beta = _har_fit(y, week, month, int(c))
        j = np.arange(lo, n)[cut[lo:] == c]
        har[j] = beta[0] + beta[1] * y[j] + beta[2] * week[j] + beta[3] * month[j]
    fit = dict(fit_through=str(dates[cut[-1]]), n_days=int(cut[-1]) + 1, coef=[round(float(b), 10) for b in beta])
    return {"naive": y.copy(), "roll22": month, "ewma": _ewma(y), "har": har}, fit


def forecast(dates, y, i):
    """Forecasts of y[i+1] for every model: {model: (median, p10, p90)}, plus the HAR fit used.

    The first line cuts off everything after day i, so nothing later can leak in. Each interval is the
    model's point forecast plus the 10th, 50th and 90th percentile of its last 500 walk forward
    residuals, floored at zero because a range cannot be negative.
    """
    dates, y = dates[:i + 1], y[:i + 1]
    if len(y) < MIN_HISTORY:
        raise ValueError(f"needs {MIN_HISTORY} trading days of history, has {len(y)}")
    lo = i - RESID_N
    points, fit = model_points(dates, y, lo)
    out = {}
    for m in MODEL_IDS:
        f = points[m]
        resid = y[lo + 1:i + 1] - f[lo:i]       # 500 residuals: each forecast was made the day before its target
        q10, q50, q90 = np.quantile(resid, QUANTILES)   # linear interpolation, the numpy default
        if not np.all(np.isfinite([f[i], q10, q50, q90])):
            raise ValueError(f"non finite forecast for model {m}")
        out[m] = tuple(max(float(f[i] + q), 0.0) for q in (q50, q10, q90))
    return out, fit


# ---------------------------------------------------------------- scoring
def pinball(y, q, tau):
    return (y - q) * tau if y >= q else (q - y) * (1.0 - tau)


def _window(rows):
    """rows: list of (actual, median, p10, p90)."""
    n = len(rows)
    if not n:
        return dict(n=0, mae=None, pinball10=None, pinball90=None, coverage=None)
    r6 = lambda v: round(v, 6)
    return dict(
        n=n,
        mae=r6(sum(abs(a - m) for a, m, _, _ in rows) / n),
        pinball10=r6(sum(pinball(a, lo, 0.1) for a, _, lo, _ in rows) / n),
        pinball90=r6(sum(pinball(a, hi, 0.9) for a, _, _, hi in rows) / n),
        coverage=r6(sum(lo <= a <= hi for a, _, lo, hi in rows) / n),
    )


def diebold_mariano(err_a, err_b, lag=DM_LAG):
    """DM test on absolute errors, Newey West (Bartlett) long run variance. Negative stat: a is better.
    Returns (stat, two sided normal p value), or (None, None) if the variance is not positive."""
    d = np.abs(np.asarray(err_a, float)) - np.abs(np.asarray(err_b, float))
    n, dbar = len(d), float(np.mean(d))
    x = d - dbar
    lrv = float(x @ x) / n
    for k in range(1, lag + 1):
        lrv += 2.0 * (1.0 - k / (lag + 1.0)) * float(x[k:] @ x[:-k]) / n
    if not lrv > 0:
        return None, None
    stat = dbar / math.sqrt(lrv / n)
    return stat, math.erfc(abs(stat) / math.sqrt(2.0))


def build_scores(frows, rrows, as_of, generated_utc):
    """Everything on the page, computed from the recorded CSV rows and nothing else."""
    realized = {(r["date"], r["ticker"]): float(r["log_range"]) for r in rrows}
    last_realized = max((r["date"] for r in rrows), default=None)
    by_day = {}
    for r in frows:
        by_day.setdefault((r["forecast_for_date"], r["ticker"]), {})[r["model"]] = r

    graded = {t: [] for t in UNIVERSE}     # per ticker: (date, actual, {model: (median, p10, p90)}), oldest first
    counts = {t: dict(graded=0, late=0, void=0, pending=0) for t in UNIVERSE}
    for (d, t), ms in sorted(by_day.items()):
        if any(ms[m]["status"] != "ok" for m in ms) or set(ms) != set(MODEL_IDS):
            counts[t]["late"] += 1
        elif (d, t) in realized:
            counts[t]["graded"] += 1
            graded[t].append((d, realized[(d, t)], {m: tuple(float(ms[m][k]) for k in ("median", "p10", "p90")) for m in MODEL_IDS}))
        elif last_realized is not None and d <= last_realized:
            counts[t]["void"] += 1          # the exchange did not trade on the target day
        else:
            counts[t]["pending"] += 1

    def rows(days, m):
        return [(a, *f[m]) for _, a, f in days]

    scores = {}
    for t in UNIVERSE:
        scores[t] = {m: dict(since_start=_window(rows(graded[t], m)),
                             rolling60=_window(rows(graded[t][-ROLLING_SCORE_DAYS:], m))) for m in MODEL_IDS}
    scores["ALL"] = {m: dict(
        since_start=_window([x for t in UNIVERSE for x in rows(graded[t], m)]),
        rolling60=_window([x for t in UNIVERSE for x in rows(graded[t][-ROLLING_SCORE_DAYS:], m)])) for m in MODEL_IDS}

    calibration = {}
    for m in MODEL_IDS:
        pool = [x for t in UNIVERSE for x in rows(graded[t], m)]
        n = len(pool)
        calibration[m] = dict(n=n, **{k: (round(sum(a <= x[j] for a, *x in pool) / n, 6) if n else None)
                                      for k, j in (("at_or_below_p10", 1), ("at_or_below_median", 0), ("at_or_below_p90", 2))})

    dm = {}
    for t in UNIVERSE:
        if len(graded[t]) < DM_DAYS:
            dm[t] = None
            continue
        first = graded[t][:DM_DAYS]          # fixed sample: the first 250 graded days, so the result never moves
        err = {m: [a - f[m][0] for _, a, f in first] for m in MODEL_IDS}
        mae = {m: sum(abs(e) for e in err[m]) / DM_DAYS for m in MODEL_IDS}
        base = "naive" if mae["naive"] <= mae["roll22"] else "roll22"
        stat, p = diebold_mariano(err["har"], err[base])
        dm[t] = dict(n=DM_DAYS, window_end=first[-1][0], baseline=base, mae_har=round(mae["har"], 6),
                     mae_baseline=round(mae[base], 6), dm_stat=None if stat is None else round(stat, 4),
                     p_value=None if p is None else round(p, 6),
                     har_beats=bool(p is not None and mae["har"] < mae[base] and p < DM_ALPHA))

    latest_day = max((r["forecast_for_date"] for r in frows), default=None)
    latest = [dict(ticker=r["ticker"], model=r["model"], median=float(r["median"]), p10=float(r["p10"]),
                   p90=float(r["p90"]), status=r["status"]) for r in frows if r["forecast_for_date"] == latest_day]
    return dict(
        version=1, start_date=START_DATE, as_of=as_of, generated_utc=generated_utc,
        source="Yahoo Finance via yfinance, raw daily High and Low",
        target="ln(High / Low) of the next trading day",
        tickers=UNIVERSE, models=[dict(id=m, name=n) for m, n in MODELS],
        interval_target=0.8, min_days_for_ranking=MIN_DAYS_RANKING, dm_days=DM_DAYS, dm_alpha=DM_ALPHA,
        latest=dict(forecast_for_date=latest_day, generated_utc=max((r["generated_utc"] for r in frows if r["forecast_for_date"] == latest_day), default=None), rows=latest),
        counts=counts, scores=scores, calibration=calibration, dm_test=dm,
    )


# ---------------------------------------------------------------- data + io
def missing_tickers(raw, tickers):
    """Tickers with no usable High data in a yfinance download (all of them if it is empty)."""
    if raw is None or raw.empty:
        return list(tickers)
    high = raw["High"]
    return [t for t in tickers if t not in high or high[t].dropna().empty]


def fetch():
    """Raw daily High and Low for the universe from HISTORY_START. Up to DOWNLOAD_TRIES attempts."""
    for attempt in range(1, DOWNLOAD_TRIES + 1):
        try:
            # threads=False: parallel downloads race on yfinance's shared cache ("database is locked").
            raw = yf.download(UNIVERSE, start=HISTORY_START, auto_adjust=False, progress=False, threads=False)
        except Exception as e:  # network or cache error: treat like a failed attempt and retry
            raw, err = None, f"{type(e).__name__}: {e}"
        else:
            err = ""
        missing = missing_tickers(raw, UNIVERSE)
        if not missing:
            break
        print(f"Download attempt {attempt}/{DOWNLOAD_TRIES} missing {missing} {err}".rstrip(), file=sys.stderr)
        if attempt < DOWNLOAD_TRIES:
            time.sleep(DOWNLOAD_WAIT_S)
    else:
        sys.exit(f"Price download failed for {missing} after {DOWNLOAD_TRIES} attempts. Leaving the existing files alone.")
    return raw["High"][UNIVERSE], raw["Low"][UNIVERSE]


def clean(high, low, now):
    """Drop a bar that is not final yet, then insist on complete, sane data. Returns (dates, {ticker: y}, high, low)."""
    if len(high) and now < bar_final_utc(high.index[-1].date()):
        high, low = high.iloc[:-1], low.iloc[:-1]   # today's bar can be partial before the close
    if not len(high) or not high.index.equals(low.index) or not high.index.is_monotonic_increasing or high.index.has_duplicates:
        sys.exit("Price history is empty or its dates are out of order. Leaving the existing files alone.")
    if pd.Timestamp(high.index[0]).date() > date.fromisoformat(HISTORY_START) + timedelta(days=7):
        sys.exit(f"Price history starts {high.index[0].date()}, expected {HISTORY_START}. Leaving the existing files alone.")
    for t in UNIVERSE:
        h, l = high[t].to_numpy(float), low[t].to_numpy(float)
        bad = ~(np.isfinite(h) & np.isfinite(l) & (l > 0) & (h >= l))
        if bad.any():
            days = [str(d.date()) for d in high.index[bad][-5:]]
            sys.exit(f"Missing or impossible High/Low for {t} on {days} ({int(bad.sum())} day(s)). Leaving the existing files alone.")
    dates = high.index.to_numpy().astype("datetime64[D]")
    y = {t: np.log(high[t].to_numpy(float) / low[t].to_numpy(float)) for t in UNIVERSE}
    return dates, y, high, low


def read_csv(path, head):
    if not path.exists():
        sys.exit(f"{path} is missing but state.json exists. Refusing to guess; restore it from git.")
    text = path.read_text()
    rows = list(csv.reader(io.StringIO(text)))
    if not rows or rows[0] != head:
        sys.exit(f"{path} has an unexpected header. Refusing to append.")
    return text, [dict(zip(head, r)) for r in rows[1:]]


def write_all(out: Path, texts: dict):
    """Temp files first, then rename, so a crash cannot leave the files out of step."""
    out.mkdir(parents=True, exist_ok=True)
    for name, text in texts.items():
        (out / (name + ".tmp")).write_text(text, newline="\n")
    for name in texts:
        os.replace(out / (name + ".tmp"), out / name)


def main():
    out, now = LIVE_DIR, utc_now()
    start = date.fromisoformat(START_DATE)
    first_origin = prev_trading_day(start)       # the close that produces the first forecast (a Friday)
    sp = out / "state.json"
    state = json.loads(sp.read_text()) if sp.exists() else None
    if state is None and now.date() > start + timedelta(days=BACKFILL_GRACE_DAYS):
        sys.exit("START_DATE is in the past; a backfill would not be pre-registered. Refusing.")
    if state is not None and state.get("start_date") != START_DATE:
        sys.exit("state.json was written under a different START_DATE. A change is a new version, not an edit.")

    dates, y, high, low = clean(*fetch(), now)
    last_bar = dates[-1].astype(date)
    n_days = len(dates)
    print(f"Download OK: {n_days} trading days per ticker, {HISTORY_START} through {last_bar}.")
    if not is_trading_day(last_bar):
        sys.exit(f"Latest bar {last_bar} is not a trading day in this script's calendar. Fix the calendar before recording anything.")
    expected = now.date() if is_trading_day(now.date()) and now >= bar_final_utc(now.date()) else None
    if expected is None:
        expected = prev_trading_day(now.date())
        while now < bar_final_utc(expected):
            expected = prev_trading_day(expected)
    if last_bar < expected:
        sys.exit(f"Yahoo has no bar for {expected} yet (latest is {last_bar}). Nothing was written. Rerun before "
                 f"{open_utc(next_trading_day(expected)):%Y-%m-%d %H:%M} UTC or that forecast will be late.")

    if state is None and last_bar < first_origin:
        print(f"Before the first forecast date ({first_origin}, for {START_DATE}). No files written.")
        return
    if n_days < MIN_HISTORY:
        sys.exit(f"Only {n_days} trading days of history, need {MIN_HISTORY}. Leaving the existing files alone.")

    if state is None:
        ftext, frows, rtext, rrows = "", [], "", []
        after_origin, after_real = first_origin - timedelta(days=1), start - timedelta(days=1)
    else:
        ftext, frows = read_csv(out / "forecasts.csv", FORECAST_HEAD)
        rtext, rrows = read_csv(out / "realized.csv", REALIZED_HEAD)
        after_origin = date.fromisoformat(state["last_origin"])
        after_real = date.fromisoformat(state["last_realized"]) if state["last_realized"] else start - timedelta(days=1)

    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    have = {(r["forecast_for_date"], r["ticker"], r["model"]) for r in frows}
    new_f, new_r, fits = [], [], (state or {}).get("har", {})
    for i in range(n_days):
        d = dates[i].astype(date)
        if d > after_origin:                     # a close not yet used as a forecast origin
            target = next_trading_day(d)
            status = "late" if now >= open_utc(target) else "ok"   # generated after the target's open: recorded, never scored
            for t in UNIVERSE:
                try:
                    fc, fits[t] = forecast(dates, y[t], i)
                except ValueError as e:
                    sys.exit(f"Cannot forecast {t} for {target}: {e}. Leaving the existing files alone.")
                for m in MODEL_IDS:
                    if (target.isoformat(), t, m) in have:
                        sys.exit(f"forecasts.csv already has {target} {t} {m}. Refusing to write it twice.")
                    med, p10, p90 = fc[m]
                    new_f.append([target.isoformat(), t, m, f"{med:.6f}", f"{p10:.6f}", f"{p90:.6f}", stamp, status])
        if d > after_real and d >= start:        # a target day whose range is now known
            for t in UNIVERSE:
                new_r.append([d.isoformat(), t, f"{float(high[t].iloc[i]):.6f}", f"{float(low[t].iloc[i]):.6f}", f"{y[t][i]:.6f}", stamp])

    if not new_f and not new_r:
        print("Nothing new to process. No files changed.")
        return

    def appended(old, head, rows):
        buf = io.StringIO(newline="")
        w = csv.writer(buf, lineterminator="\n")
        if not old:
            w.writerow(head)
        w.writerows(rows)
        return old + buf.getvalue()

    frows += [dict(zip(FORECAST_HEAD, r)) for r in new_f]
    rrows += [dict(zip(REALIZED_HEAD, r)) for r in new_r]
    new_state = dict(version=1, start_date=START_DATE, history_start=HISTORY_START, last_origin=last_bar.isoformat(),
                     last_realized=max((r["date"] for r in rrows), default=None), har=fits)
    scores = build_scores(frows, rrows, last_bar.isoformat(), stamp)
    write_all(out, {
        "forecasts.csv": appended(ftext, FORECAST_HEAD, new_f),
        "realized.csv": appended(rtext, REALIZED_HEAD, new_r),
        "scores.json": json.dumps(scores, indent=1) + "\n",
        "state.json": json.dumps(new_state, indent=1) + "\n",
    })
    late = sum(r[7] == "late" for r in new_f)
    print(f"Recorded {len(new_f)} forecast row(s) ({late} late) and {len(new_r)} realized row(s). As of {last_bar}. Wrote {out}")


if __name__ == "__main__":
    main()
