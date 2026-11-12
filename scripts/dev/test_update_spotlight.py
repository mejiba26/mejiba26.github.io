"""
Offline test for scripts/update_spotlight.py on synthetic prices. No network.
Run: python scripts/dev/test_update_spotlight.py
"""
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import update_spotlight as u  # noqa: E402

TODAY = date(2026, 11, 12)
IDX = pd.bdate_range("2026-08-03", "2026-11-11")


def check(cond, msg):
    if not cond:
        sys.exit(f"FAIL: {msg}")
    print(f"ok   {msg}")


def raises(fn, msg):
    try:
        fn()
    except ValueError:
        print(f"ok   {msg}")
        return
    sys.exit(f"FAIL: {msg} (no ValueError)")


def build(moves=None, now_mult=None):
    """Every name sits at 100 on Sep 30 and moves linearly to 100*(1+move) by Oct 30, then to now_mult."""
    moves, now_mult = moves or {}, now_mult or {}
    cols = list(u.STOCKS) + list(u.SECTORS) + [u.BENCHMARK]
    df = pd.DataFrame(index=IDX, columns=cols, dtype=float)
    for t in cols:
        m = moves.get(t, 0.01)
        pts = {pd.Timestamp("2026-08-03"): 100.0, pd.Timestamp("2026-09-30"): 100.0,
               pd.Timestamp("2026-10-30"): 100.0 * (1 + m), pd.Timestamp("2026-11-11"): 100.0 * (1 + m) * now_mult.get(t, 1.02)}
        s = pd.Series(pts).reindex(IDX).interpolate()
        df[t] = s
    return df


def drivers(tnx=(4.0, 4.1, 4.2)):
    d = pd.DataFrame(index=IDX, dtype=float)
    base = {"CL=F": (60.0, 66.0, 63.0), "BZ=F": (64.0, 70.0, 68.0), "DX-Y.NYB": (100.0, 99.0, 101.0),
            "^VIX": (15.0, 20.0, 18.0), "HG=F": (4.0, 4.4, 4.2), "^TNX": tnx}
    for sym, (a, b, c) in base.items():
        pts = {pd.Timestamp("2026-08-03"): a, pd.Timestamp("2026-09-30"): a, pd.Timestamp("2026-10-30"): b, pd.Timestamp("2026-11-11"): c}
        d[sym] = pd.Series(pts).reindex(IDX).interpolate()
    return d


# --- window and returns -------------------------------------------------------
px = build(moves={"NOC": 0.12, "LMT": -0.03, "XLE": -0.08, "XLK": 0.02, "SPY": 0.01}, now_mult={"NOC": 1.05})
r = u.compute(px, drivers(), TODAY)
check(r["month"] == "2026-10" and r["start_date"] == "2026-09-30" and r["end_date"] == "2026-10-30", "window is Sep 30 close to Oct 30 close")
check(r["benchmark"]["month_ret"] == 1.0, "benchmark month return is 1.0%")
noc = next(x for x in r["stocks"] if x["ticker"] == "NOC")
check(noc["month_ret"] == 12.0 and noc["excess"] == 11.0, "NOC month return 12.0% and excess 11.0% over SPY")
check(noc["start"] == 100.0 and noc["end"] == 112.0 and noc["now"] == 117.6 and noc["since"] == 5.0, "NOC start, end, now and since right")
check(r["stocks"][0]["month_ret"] >= r["stocks"][-1]["month_ret"], "stock list sorted best to worst")
check(len(r["stocks"]) == len(u.STOCKS) and len(r["sectors"]) == 11, "full universe listed")

# --- the pick rule -------------------------------------------------------------
check(r["spotlight_stock"]["ticker"] == "NOC", "stock pick is the largest absolute excess (NOC, +11.0)")
check(r["spotlight_market"]["ticker"] == "XLE", "market pick uses absolute excess, so the biggest loser (XLE, -9.0) beats XLK (+1.0)")
check(r["spotlight_market"]["group"] == "Energy", "sector pick carries its group")

# a large negative move wins over a smaller positive one
neg = u.compute(build(moves={"LMT": -0.20, "NVDA": 0.15, "SPY": 0.0}), drivers(), TODAY)
check(neg["spotlight_stock"]["ticker"] == "LMT", "-20 beats +15 by absolute excess")

# ties go to the alphabetically first ticker
tie = u.compute(build(moves={"MSFT": 0.10, "AAPL": 0.10, "SPY": 0.0}), drivers(), TODAY)
check(tie["spotlight_stock"]["ticker"] == "AAPL", "tie goes to the alphabetically first ticker")

# --- drivers -------------------------------------------------------------------
drv = {d["key"]: d for d in r["spotlight_stock"]["drivers"]}
check(set(drv) == {"wti", "copper"} and r["spotlight_stock"]["group"] == "Aerospace & Defense", "A&D spotlight carries the A&D drivers")
check(drv["wti"]["month_chg"] == 10.0 and drv["wti"]["now"] == 63.0 and drv["wti"]["since_chg"] == round((63 / 66 - 1) * 100, 2), "WTI month change, latest and change since month end")
check(len(r["spotlight_stock"]["why"]) > 20, "evergreen exposure sentence attached")
tnx_sector = u.compute(build(moves={"XLK": 0.30, "SPY": 0.0}), drivers(), TODAY)["spotlight_market"]
t = {d["key"]: d for d in tnx_sector["drivers"]}["tnx"]
check(t["unit"] == "bp" and t["month_chg"] == 10 and t["since_chg"] == 10, "yield change shown in basis points (4.0 to 4.1 is +10bp)")

# --- every group has an exposure entry with valid driver keys -----------------
groups = {v[1] for v in u.STOCKS.values()} | {v[1] for v in u.SECTORS.values()}
check(groups <= set(u.EXPOSURES), "every group has an exposure note")
check(all(k in u.DRIVERS for keys, _ in u.EXPOSURES.values() for k in keys), "every exposure driver key exists")

# --- month rollover ------------------------------------------------------------
early = u.compute(px.loc[:"2026-10-05"], drivers().loc[:"2026-10-05"], date(2026, 10, 6))
check(early["month"] == "2026-09" and early["start_date"] == "2026-08-31" and early["end_date"] == "2026-09-30", "in early October the analyzed month is September")
jan = pd.bdate_range("2025-11-03", "2026-01-09")
jp = pd.DataFrame(100.0, index=jan, columns=list(u.STOCKS) + list(u.SECTORS) + [u.BENCHMARK])
jd = pd.DataFrame(1.0, index=jan, columns=[v[1] for v in u.DRIVERS.values()]); jd["^TNX"] = 4.0
jr = u.compute(jp, jd, date(2026, 1, 12))
check(jr["month"] == "2025-12" and jr["end_date"] == "2025-12-31", "in January the analyzed month is the previous December")

# --- failure cases must raise, never publish ----------------------------------
raises(lambda: u.compute(px, drivers(), date(2026, 12, 20)), "stale prices raise")
raises(lambda: u.compute(px.drop(columns=["NOC"]), drivers(), TODAY), "missing ticker raises")
bad = px.copy(); bad["LMT"] = np.nan
raises(lambda: u.compute(bad, drivers(), TODAY), "all NaN ticker raises")
raises(lambda: u.compute(px, drivers().drop(columns=["CL=F"]), TODAY), "missing driver raises")
raises(lambda: u.compute(px, drivers(tnx=(40.0, 41.0, 42.0)), TODAY), "10 year yield in the wrong units raises")
raises(lambda: u.compute(px.iloc[-20:], drivers(), TODAY), "too little history raises")
print("all passed")
