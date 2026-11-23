"""
Fails loudly if data/portfolio/results.json lacks something the #portfolio card on index.html needs,
and, with --against, if its numbers drift from a reference copy beyond the stated tolerance.

The page falls back to the static v1 text quietly when anything is off (by design), so without this
check a renamed key would leave v2 hidden while every workflow run stays green.

KEEP IN SYNC with the portfolio script in index.html (search "Portfolio model v2:"): the keys checked
here are the ones that script reads. If you change one side, change the other.

    python scripts/check_portfolio_schema.py [FILE] [--against REFERENCE]

Tolerance for --against: two numbers agree if math.isclose(a, b, rel_tol=5e-4, abs_tol=5e-4), that is
within 0.0005 or 0.05%, whichever is larger. Every other value (text, dates, counts, structure) must be
identical. This is how the workflow (Python 3.12, Linux) confirms it reproduces the committed results.
Standard library only.
"""
import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REL = ABS = 5e-4
STRATEGY_NUMS = ["sharpe", "cagr", "max_drawdown", "annual_turnover"]
SERIES_SHOWN = ["60/40", "Trend 10M", "MS-Sample", "MV-Sample", "RP-Sample"]


def fail(msg):
    sys.exit(f"SCHEMA CHECK FAILED: {msg}")


def need(obj, key, where, kind=None):
    if not isinstance(obj, dict) or key not in obj:
        fail(f"missing key '{key}' in {where}")
    v = obj[key]
    if kind == "num" and (isinstance(v, bool) or not isinstance(v, (int, float))):
        fail(f"'{key}' in {where} is not a number: {v!r}")
    if kind == "pair" and not (isinstance(v, list) and len(v) == 2 and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v)):
        fail(f"'{key}' in {where} is not a pair of numbers: {v!r}")
    return v


def check(r):
    need(r, "as_of", "results")
    period = need(r, "period", "results")
    for k in ("start", "end"):
        need(period, k, "period")
    need(period, "months", "period", "num")
    stress_periods = need(need(r, "settings", "results"), "stress_periods", "settings")

    v1 = need(r, "v1_replication", "results")
    need(v1, "period", "v1_replication")
    for side in ("published", "rerun"):
        need(need(need(v1, side, "v1_replication"), "Max Sharpe", f"v1_replication.{side}"), "Sharpe", f"{side}.Max Sharpe", "num")

    strategies = need(r, "strategies", "results")
    if not isinstance(strategies, list) or len(strategies) != 13:
        fail("'strategies' is not a list of 13")
    names = []
    for s in strategies:
        n = need(s, "name", "strategy")
        names.append(n)
        for k in STRATEGY_NUMS:
            need(s, k, n, "num")
        need(s, "sharpe_ci95", n, "pair")
        st = need(s, "stress", n)
        for p in stress_periods:
            need(st, p, f"{n}.stress", "num")
        cr = need(s, "crash", n)
        need(cr, "compounded", f"{n}.crash", "num")
        if n != "60/40":
            vs = need(s, "vs_6040", n)
            need(vs, "sharpe_diff", f"{n}.vs_6040", "num")
            need(vs, "ci95", f"{n}.vs_6040", "pair")
    for n in ["MS-Sample"] + SERIES_SHOWN:
        if n not in names:
            fail(f"strategy '{n}' missing")

    dec = need(r, "decomposition", "results")
    for k in ("rule", "estimator", "interaction"):
        need(need(dec, k, "decomposition"), "point", f"decomposition.{k}", "num")
        need(dec[k], "ci95", f"decomposition.{k}", "pair")

    ct = need(r, "crash_test", "results")
    need(ct, "months", "crash_test", "num")
    eps = need(ct, "episodes", "crash_test")
    if not isinstance(eps, list):
        fail("crash_test.episodes is not a list")
    for e in eps:
        for k in ("start", "end"):
            need(e, k, "episode")
        need(e, "months", "episode", "num")

    ser = need(r, "series", "results")
    dates = need(ser, "dates", "series")
    if not isinstance(dates, list) or len(dates) != period["months"]:
        fail("series.dates length does not match period.months")
    for k in ("growth", "drawdown"):
        block = need(ser, k, "series")
        for n in SERIES_SHOWN:
            vals = need(block, n, f"series.{k}")
            if len(vals) != len(dates) or not all(isinstance(v, (int, float)) for v in vals):
                fail(f"series.{k}.{n} has the wrong length or a non number")


def compare(a, b, path="results"):
    """Raise on the first difference beyond tolerance. Returns the largest numeric difference seen."""
    if isinstance(a, bool) or isinstance(b, bool) or not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        if type(a) is not type(b):
            fail(f"{path}: type changed ({a!r} vs {b!r})")
    if isinstance(a, dict):
        if a.keys() != b.keys():
            fail(f"{path}: keys differ: {sorted(set(a) ^ set(b))}")
        return max([compare(a[k], b[k], f"{path}.{k}") for k in a] or [0.0])
    if isinstance(a, list):
        if len(a) != len(b):
            fail(f"{path}: length {len(a)} vs {len(b)}")
        return max([compare(x, y, f"{path}[{i}]") for i, (x, y) in enumerate(zip(a, b))] or [0.0])
    if isinstance(a, (int, float)) and not isinstance(a, bool):
        if not math.isclose(a, b, rel_tol=REL, abs_tol=ABS):
            fail(f"{path}: {a} vs {b} is beyond tolerance")
        return abs(a - b)
    if a != b:
        fail(f"{path}: {a!r} vs {b!r}")
    return 0.0


def load(p):
    if not p.exists():
        fail(f"{p} is missing")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        fail(f"invalid JSON in {p}: {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file", nargs="?", default=str(ROOT / "data" / "portfolio" / "results.json"))
    ap.add_argument("--against", help="reference results.json to compare numbers with")
    args = ap.parse_args()
    r = load(Path(args.file))
    check(r)
    print(f"{args.file}: schema OK (as of {r['as_of']})")
    if args.against:
        worst = compare(load(Path(args.against)), r)
        print(f"Matches {args.against} within tolerance (largest difference {worst:.2e})")


if __name__ == "__main__":
    main()
