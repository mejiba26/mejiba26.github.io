"""
Fails loudly if data/vol/ lacks something the volatility scorecard on index.html needs,
or if the append only files were rewritten.

The page hides the card quietly when anything is off (by design), so without this
check a renamed key would leave the card dead while every workflow run stays green.

KEEP IN SYNC with the volatility scorecard script in index.html (search "Volatility
scorecard:"): the scores.json keys checked here are the ones that script reads. The
CSV headers, tickers, models and statuses must match scripts/update_vol.py. If you
change one side, change the other.

    python scripts/check_vol_schema.py [DIR]     # default: data/vol

If the folder does not exist yet (before the first forecast date) it does nothing and
exits 0. Standard library only.
"""
import csv
import io
import json
import math
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TICKERS = ["SPY", "QQQ", "ITA", "XLE"]
MODELS = ["naive", "roll22", "ewma", "har"]
STATUSES = ["ok", "late"]
WINDOWS = ["since_start", "rolling60"]
SCORE_KEYS = ["mae", "pinball10", "pinball90", "coverage"]
CALIBRATION_KEYS = ["at_or_below_p10", "at_or_below_median", "at_or_below_p90"]
COUNT_KEYS = ["graded", "late", "void", "pending"]
FORECAST_HEADER = "forecast_for_date,ticker,model,median,p10,p90,generated_utc,status"
REALIZED_HEADER = "date,ticker,high,low,log_range,recorded_utc"
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def fail(msg):
    sys.exit(f"SCHEMA CHECK FAILED: {msg}")


def need(obj, key, where, kind=None):
    if not isinstance(obj, dict) or key not in obj:
        fail(f"missing key '{key}' in {where}")
    v = obj[key]
    if kind == "num" and not is_num(v):
        fail(f"'{key}' in {where} is not a number: {v!r}")
    if kind == "int" and (isinstance(v, bool) or not isinstance(v, int) or v < 0):
        fail(f"'{key}' in {where} is not a count: {v!r}")
    if kind == "date" and not (isinstance(v, str) and DATE.match(v)):
        fail(f"'{key}' in {where} is not a date: {v!r}")
    return v


def is_num(v):
    return not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v)


def num(text, where):
    try:
        v = float(text)
    except ValueError:
        v = math.nan
    if not math.isfinite(v):
        fail(f"{where} is not a number: {text!r}")
    return v


def load(d, name):
    p = d / name
    if not p.exists():
        fail(f"{p} is missing")
    return p.read_text()


def rows_of(text, header, name, width):
    lines = list(csv.reader(io.StringIO(text)))
    if not lines or ",".join(lines[0]) != header:
        fail(f"{name} header is {lines[:1]!r}, expected {header!r}")
    for i, r in enumerate(lines[1:], start=2):
        if len(r) != width:
            fail(f"{name} line {i} has {len(r)} fields, expected {width}")
    return lines[1:]


def check_append_only(d, name, text):
    """The committed file must be an exact prefix of the new one. Skipped outside a git checkout."""
    try:
        rel = (d / name).resolve().relative_to(ROOT).as_posix()
        old = subprocess.run(["git", "-C", str(ROOT), "show", f"HEAD:{rel}"], capture_output=True, text=True)
    except (ValueError, OSError):
        return
    if old.returncode == 0 and not text.startswith(old.stdout):
        fail(f"{name} is append only, but rows already committed were changed or removed")


def check(d: Path):
    ftext, rtext = load(d, "forecasts.csv"), load(d, "realized.csv")
    try:
        scores = json.loads(load(d, "scores.json"))
        state = json.loads(load(d, "state.json"))
    except json.JSONDecodeError as e:
        fail(f"invalid JSON: {e}")
    start = need(scores, "start_date", "scores.json", "date")

    seen, per_day, last = set(), {}, ""
    for i, (day, t, m, med, p10, p90, stamp, status) in enumerate(rows_of(ftext, FORECAST_HEADER, "forecasts.csv", 8), start=2):
        w = f"forecasts.csv line {i}"
        if not DATE.match(day) or day < start:
            fail(f"{w}: bad or pre start forecast_for_date {day!r}")
        if day < last:
            fail(f"{w}: dates go backwards")
        last = day
        if t not in TICKERS or m not in MODELS or status not in STATUSES or not STAMP.match(stamp):
            fail(f"{w}: unexpected ticker, model, status or timestamp")
        if (day, t, m) in seen:
            fail(f"{w}: duplicate row for {day} {t} {m}")
        seen.add((day, t, m))
        per_day.setdefault((day, t), set()).add(m)
        lo, mid, hi = num(p10, w), num(med, w), num(p90, w)
        if not 0 <= lo <= mid <= hi:
            fail(f"{w}: expected 0 <= p10 <= median <= p90")
    for (day, t), ms in per_day.items():
        if ms != set(MODELS):
            fail(f"forecasts.csv: {day} {t} has models {sorted(ms)}, expected all of {MODELS}")
    if not seen:
        fail("forecasts.csv has no rows")

    seen_r = set()
    for i, (day, t, high, low, lr, stamp) in enumerate(rows_of(rtext, REALIZED_HEADER, "realized.csv", 6), start=2):
        w = f"realized.csv line {i}"
        if not DATE.match(day) or day < start or t not in TICKERS or not STAMP.match(stamp):
            fail(f"{w}: bad date, ticker or timestamp")
        if (day, t) in seen_r:
            fail(f"{w}: duplicate row for {day} {t}")
        seen_r.add((day, t))
        h, l = num(high, w), num(low, w)
        if not (h >= l > 0) or num(lr, w) < 0:
            fail(f"{w}: expected high >= low > 0 and a log range of at least 0")

    as_of = need(scores, "as_of", "scores.json", "date")
    if need(state, "last_origin", "state.json", "date") != as_of:
        fail(f"scores.json as_of {as_of} does not match state.json last_origin {state['last_origin']}")
    if need(state, "start_date", "state.json", "date") != start:
        fail("start_date differs between scores.json and state.json")
    for k in ("interval_target", "min_days_for_ranking", "dm_days", "dm_alpha"):
        need(scores, k, "scores.json", "num")
    if need(scores, "tickers", "scores.json") != TICKERS:
        fail(f"scores.json tickers are {scores['tickers']!r}, expected {TICKERS}")
    models = need(scores, "models", "scores.json")
    if not isinstance(models, list) or [need(m, "id", "scores.json models") for m in models] != MODELS:
        fail(f"scores.json models do not match {MODELS}")
    for m in models:
        if not isinstance(need(m, "name", "scores.json models"), str):
            fail("a model name in scores.json is not text")

    latest = need(scores, "latest", "scores.json")
    if need(latest, "forecast_for_date", "scores.json latest", "date") != last:
        fail(f"scores.json latest.forecast_for_date {latest['forecast_for_date']} is not the newest date in forecasts.csv ({last})")
    lrows = need(latest, "rows", "scores.json latest")
    if not isinstance(lrows, list) or {(r.get("ticker"), r.get("model")) for r in lrows if isinstance(r, dict)} != {(t, m) for t in TICKERS for m in MODELS}:
        fail("scores.json latest.rows must hold one row per ticker and model")
    for r in lrows:
        w = f"scores.json latest row {r.get('ticker')} {r.get('model')}"
        for k in ("median", "p10", "p90"):
            need(r, k, w, "num")
        if not 0 <= r["p10"] <= r["median"] <= r["p90"] or need(r, "status", w) not in STATUSES:
            fail(f"{w}: bad interval or status")

    counts = need(scores, "counts", "scores.json")
    for t in TICKERS:
        for k in COUNT_KEYS:
            need(need(counts, t, "scores.json counts"), k, f"scores.json counts.{t}", "int")
    table = need(scores, "scores", "scores.json")
    for t in TICKERS + ["ALL"]:
        for m in MODELS:
            for win in WINDOWS:
                w = f"scores.json scores.{t}.{m}.{win}"
                cell = need(need(need(table, t, "scores.json scores"), m, f"scores.json scores.{t}"), win, w)
                n = need(cell, "n", w, "int")
                for k in SCORE_KEYS:
                    v = need(cell, k, w)
                    if (n == 0 and v is not None) or (n > 0 and not is_num(v)):
                        fail(f"'{k}' in {w} is {v!r} with n = {n}")
    cal = need(scores, "calibration", "scores.json")
    for m in MODELS:
        w = f"scores.json calibration.{m}"
        n = need(need(cal, m, "scores.json calibration"), "n", w, "int")
        for k in CALIBRATION_KEYS:
            v = need(cal[m], k, w)
            if (n == 0 and v is not None) or (n > 0 and not (is_num(v) and 0 <= v <= 1)):
                fail(f"'{k}' in {w} is {v!r} with n = {n}")
    dm = need(scores, "dm_test", "scores.json")
    for t in TICKERS:
        res = need(dm, t, "scores.json dm_test")
        if res is not None:
            for k in ("mae_har", "mae_baseline"):
                need(res, k, f"scores.json dm_test.{t}", "num")
            if need(res, "baseline", f"scores.json dm_test.{t}") not in MODELS or not isinstance(need(res, "har_beats", f"scores.json dm_test.{t}"), bool):
                fail(f"scores.json dm_test.{t} has a bad baseline or verdict")
            need(res, "p_value", f"scores.json dm_test.{t}")

    check_append_only(d, "forecasts.csv", ftext)
    check_append_only(d, "realized.csv", rtext)
    return f"Schema OK: {len(seen)} forecast rows, {len(seen_r)} realized rows, as_of {as_of}."


def main():
    d = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "vol"
    if not d.exists():
        print(f"{d} does not exist yet. Nothing to check.")
        return
    print(check(d))


if __name__ == "__main__":
    main()
