"""
Paper trading simulation, rules from docs/paper-trading-spec.md (DRAFT).

Each run processes only trading days after the last one recorded in
data/paper/state.json, appends to data/paper/ledger.csv, appends to
data/paper/equity.json. Running twice on the same day changes nothing.

    pip install yfinance pandas
    python scripts/paper_trade.py                       # live (needs START_DATE)
    python scripts/paper_trade.py --replay 2021-01-04 2026-11-06 --out <dir>
                                                        # TEST ONLY, not a track record

Every choice the spec does not pin down is marked "DEFAULT:" below.
Rule VALUES (1.5 ATR pullback, 2/3 ATR, 20 days, tiers, 10 bps...) are copied from the
spec unchanged. Do not edit them here; amend the spec first.
"""

import argparse
import csv
import io
import json
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

# Set at the spec lock. None would refuse to write live files.
START_DATE = "2026-11-16"  # locked with docs/paper-trading-spec.md; must match the spec

ROOT = Path(__file__).resolve().parent.parent
LIVE_DIR = ROOT / "data" / "paper"

# ---- Spec values (PROPOSED in the spec; unchanged) -------------------------
UNIVERSE = ["RTX", "LMT", "NOC", "GD", "ITA", "XLE", "USO", "XOM"]
BENCH_SPY = "SPY"
START_CASH = 100_000.0
COST = 0.0010           # 10 bps on every fill
ATR_N = 14
HIGH_N, PULLBACK_ATR = 10, 1.5   # close >= 1.5 ATR below the highest High of the last 10 bars (incl. today)
SMA_FAST, SMA_SLOW = 50, 200
STOP_ATR, TARGET_ATR = 2.0, 3.0
TIME_STOP_DAYS = 20
TIERS = {
    "safe":   dict(risk=0.005, max_w=0.20, max_pos=3, max_inv=0.50, weekly=2),
    "medium": dict(risk=0.010, max_w=0.30, max_pos=4, max_inv=0.80, weekly=2),
    "high":   dict(risk=0.020, max_w=0.40, max_pos=3, max_inv=1.00, weekly=2),
}
# ---- Defaults the spec leaves open (flagged for review) ---------------------
MOM_LOOKBACK = 252      # DEFAULT: 12m momentum = close / close 252 bars ago - 1 (user-specified ranking)
WARMUP_DAYS = 800       # DEFAULT: calendar days of history fetched before the first processed day
DOWNLOAD_TRIES, DOWNLOAD_WAIT_S = 3, 5   # download mechanics only, not a rule

PORTS = list(TIERS)
BENCHES = ["ITA", "SPY", "EQW"]


# ---------------------------------------------------------------- indicators
def build_indicators(px: dict) -> dict:
    c, h, l = px["Close"], px["High"], px["Low"]
    pc = c.shift(1)
    tr = pd.DataFrame(
        np.maximum.reduce([(h - l).values, (h - pc).abs().values, (l - pc).abs().values]),
        index=c.index, columns=c.columns,
    )
    tr.iloc[0] = (h - l).iloc[0]
    # DEFAULT: Wilder smoothing for ATR (alpha = 1/14).
    return dict(
        sma_fast=c.rolling(SMA_FAST).mean(),
        sma_slow=c.rolling(SMA_SLOW).mean(),
        high_n=h.rolling(HIGH_N).max(),
        atr=tr.ewm(alpha=1 / ATR_N, adjust=False, min_periods=ATR_N).mean(),
        mom=c / c.shift(MOM_LOOKBACK) - 1,
        prev_high=h.shift(1),
    )


# ---------------------------------------------------------------- simulation
def new_state() -> dict:
    return dict(
        last_date=None,
        bench_started=False,
        bench={"ITA": START_CASH, "SPY": START_CASH, "EQW": {t: START_CASH / len(UNIVERSE) for t in UNIVERSE}},
        ports={p: dict(cash=START_CASH, pos={}, pend_in=[], pend_out=[], week="", week_n=0) for p in PORTS},
    )


def bench_value(b):
    return sum(b.values()) if isinstance(b, dict) else b


def step(state, d, i, px, ind, divs, ledger, notes):
    """Process one trading day d (row i): open fills, dividends, close checks, new signals."""
    o, c = px["Open"].loc[d], px["Close"].loc[d]
    pc = px["Close"].iloc[i - 1]
    ds = d.strftime("%Y-%m-%d")
    iso = d.isocalendar()
    week = f"{iso[0]}-W{iso[1]:02d}"

    for name, cfg in TIERS.items():
        p = state["ports"][name]
        # DEFAULT: dividends are credited as cash for shares held at the prior close,
        # including shares sold at this day's open (the holder of record keeps it).
        held_prior = {t: pos["shares"] for t, pos in p["pos"].items()}
        for t, sh in held_prior.items():
            dv = float(divs[t].loc[d])
            if dv > 0:
                p["cash"] += sh * dv
                ledger.append([ds, name, t, "DIV", round(dv, 4), sh, 0.0, "dividend"])

        # 1. Exits decided at the previous close, filled at today's open.
        for ex in p["pend_out"]:
            pos = p["pos"].pop(ex["ticker"])
            price = float(o[ex["ticker"]])
            cost = pos["shares"] * price * COST
            p["cash"] += pos["shares"] * price - cost
            ledger.append([ds, name, ex["ticker"], "SELL", round(price, 4), pos["shares"], round(cost, 2), ex["reason"]])
        p["pend_out"] = []

        # 2. Entries decided at the previous close, filled at today's open.
        if p["week"] != week:
            p["week"], p["week_n"] = week, 0  # DEFAULT: weekly cap counts fills per ISO calendar week
        # DEFAULT: sizing equity = cash + positions marked at the prior close.
        eq = p["cash"] + sum(pos["shares"] * float(pc[t]) for t, pos in p["pos"].items())
        invested = sum(pos["shares"] * float(pc[t]) for t, pos in p["pos"].items())
        for cand in p["pend_in"]:
            t = cand["ticker"]
            if len(p["pos"]) >= cfg["max_pos"]:
                notes.append(f"{ds} {name} {t}: skipped, max positions"); continue
            if p["week_n"] >= cfg["weekly"]:
                notes.append(f"{ds} {name} {t}: skipped, weekly cap"); continue
            price = float(o[t])
            shares = min(
                cfg["risk"] * eq / (STOP_ATR * cand["atr"]),       # risk-based size
                cfg["max_w"] * eq / price,                          # max weight
                (cfg["max_inv"] * eq - invested) / price,           # max total invested
                p["cash"] / (price * (1 + COST)),                   # never spend cash we lack
            )
            # DEFAULT: whole shares; cap breaches REDUCE the size rather than skip the trade.
            shares = math.floor(shares)
            if shares < 1:
                notes.append(f"{ds} {name} {t}: skipped, size < 1 share"); continue
            cost = shares * price * COST
            p["cash"] -= shares * price + cost
            invested += shares * price
            p["week_n"] += 1
            # DEFAULT: stop/target measured from the raw open fill (cost excluded), using the signal-day ATR.
            p["pos"][t] = dict(shares=shares, entry=price, stop=price - STOP_ATR * cand["atr"],
                               target=price + TARGET_ATR * cand["atr"], bars=-1)
            ledger.append([ds, name, t, "BUY", round(price, 4), shares, round(cost, 2), "entry"])
        p["pend_in"] = []

    # Benchmarks are bought at the open of the first processed day (START_DATE), same 10 bps cost.
    if not state["bench_started"]:
        state["bench_started"] = True
        k = 1.0 / (1 + COST)
        for t in ("ITA", "SPY"):
            state["bench"][t] = START_CASH * k * float(c[t]) / float(o[t])
        state["bench"]["EQW"] = {t: START_CASH / len(UNIVERSE) * k * float(c[t]) / float(o[t]) for t in UNIVERSE}
    elif state["bench_started"]:
        # Total return: raw close change plus dividend reinvested at the close.
        g = lambda t: (float(c[t]) + float(divs[t].loc[d])) / float(pc[t])
        for t in ("ITA", "SPY"):
            state["bench"][t] *= g(t)
        state["bench"]["EQW"] = {t: v * g(t) for t, v in state["bench"]["EQW"].items()}

    # 3. Close: mark, check exits, look for entries.
    row = {"date": ds}
    for name in PORTS:
        p = state["ports"][name]
        for t, pos in p["pos"].items():
            pos["bars"] += 1
            cl = float(c[t])
            # DEFAULT: if several exits fire the same close, the spec's order is the priority.
            if cl <= pos["stop"]:
                reason = "stop"
            elif cl >= pos["target"]:
                reason = "target"
            elif cl < float(ind["sma_fast"].loc[d, t]):
                reason = "trend break"
            elif pos["bars"] >= TIME_STOP_DAYS:
                # DEFAULT: fill day is bar 0; exit signalled at the close of bar 20, filled the next open.
                reason = "time stop"
            else:
                continue
            p["pend_out"].append(dict(ticker=t, reason=reason))
        row[name] = round(p["cash"] + sum(pos["shares"] * float(c[t]) for t, pos in p["pos"].items()), 2)

    cands = []
    for t in UNIVERSE:
        v = {k: ind[k].loc[d, t] for k in ("sma_fast", "sma_slow", "high_n", "atr", "mom", "prev_high")}
        if any(pd.isna(x) for x in v.values()):
            continue
        cl = float(c[t])
        if cl > v["sma_fast"] and cl > v["sma_slow"] and cl <= v["high_n"] - PULLBACK_ATR * v["atr"] and cl > v["prev_high"]:
            cands.append(dict(ticker=t, atr=float(v["atr"]), mom=float(v["mom"])))
    # DEFAULT: ranked by 12m momentum, highest first (ticker breaks ties); capacity is applied at the fill.
    cands.sort(key=lambda x: (-x["mom"], x["ticker"]))
    for name in PORTS:
        p = state["ports"][name]
        p["pend_in"] = [x for x in cands if x["ticker"] not in p["pos"]]

    row.update({k: round(bench_value(state["bench"][k]), 2) for k in BENCHES})
    state["last_date"] = ds
    return row


# ---------------------------------------------------------------- data + io
def missing_tickers(raw, tickers):
    """Tickers with no usable Close data in a yfinance download (all of them if it is empty)."""
    if raw is None or raw.empty:
        return list(tickers)
    close = raw["Close"]
    return [t for t in tickers if t not in close or close[t].dropna().empty]


def fetch(start: pd.Timestamp, end=None):
    tickers = UNIVERSE + [BENCH_SPY]
    begin = (start - timedelta(days=WARMUP_DAYS)).strftime("%Y-%m-%d")
    for attempt in range(1, DOWNLOAD_TRIES + 1):
        try:
            # threads=False: parallel downloads race on yfinance's shared cache ("database is locked").
            raw = yf.download(tickers, start=begin, end=end, auto_adjust=False, actions=True,
                              progress=False, threads=False)
        except Exception as e:  # network or cache error: treat like a failed attempt and retry
            raw, err = None, f"{type(e).__name__}: {e}"
        else:
            err = ""
        missing = missing_tickers(raw, tickers)
        if not missing:
            break
        print(f"Download attempt {attempt}/{DOWNLOAD_TRIES} missing {missing} {err}".rstrip(), file=sys.stderr)
        if attempt < DOWNLOAD_TRIES:
            time.sleep(DOWNLOAD_WAIT_S)
    else:
        sys.exit(f"Price download failed for {missing} after {DOWNLOAD_TRIES} attempts. Leaving the existing files alone.")
    px = {f: raw[f][tickers] for f in ("Open", "High", "Low", "Close")}
    divs = raw["Dividends"][tickers].fillna(0.0)
    splits = raw["Stock Splits"][tickers].fillna(0.0)
    return px, divs, splits


def check_splits(splits, days):
    """Guard only: halt on any split in the days about to be processed. Yahoo restates past prices after a
    split, but stored shares, entries and stops stay as they were, so continuing would book a fake loss in
    the append only ledger. Nothing is adjusted here; Brian decides how to handle a split (Amendment 2)."""
    for d in days:
        for t, ratio in splits.loc[d].items():
            if ratio and ratio != 0:
                sys.exit(f"STOP: stock split in {t} on {d.date()}, ratio {ratio:g}. Nothing was written. "
                         f"Stored shares and stops would be wrong. This run halts until a new dated amendment "
                         f"says how to handle it (see docs/paper-trading-spec.md, Amendment 2).")


def run(px, divs, splits, state, first, last):
    ind = build_indicators(px)
    idx = px["Close"].index
    todo = [(i, d) for i, d in enumerate(idx) if first <= d <= last and (state["last_date"] is None or d.strftime("%Y-%m-%d") > state["last_date"])]
    check_splits(splits, [d for _, d in todo])
    ledger, rows, notes = [], [], []
    for i, d in todo:
        if i < 1 or any(px[f].loc[d].isna().any() for f in px):
            sys.exit(f"Missing price data for {d.date()}. Leaving the existing files alone.")
        rows.append(step(state, d, i, px, ind, divs, ledger, notes))
    return ledger, rows, notes


LEDGER_HEAD = ["date", "portfolio", "ticker", "side", "price", "shares", "cost", "reason"]


def write_all(out: Path, state, ledger, rows, meta):
    """Temp files first, then rename, so a crash cannot leave ledger and state out of step."""
    out.mkdir(parents=True, exist_ok=True)
    lp, ep, sp = out / "ledger.csv", out / "equity.json", out / "state.json"
    old = lp.read_text() if lp.exists() else ""
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\n")
    if not old:
        w.writerow(LEDGER_HEAD)
    w.writerows(ledger)
    eq = json.loads(ep.read_text()) if ep.exists() else {}
    series = eq.get("series", []) + rows
    eq = {**meta, "as_of": series[-1]["date"], "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
          "start_date": series[0]["date"], "portfolios": PORTS, "benchmarks": BENCHES, "series": series}
    tmp = {lp: old + buf.getvalue(), ep: json.dumps(eq, indent=1) + "\n", sp: json.dumps(state, indent=1) + "\n"}
    for p, text in tmp.items():
        Path(str(p) + ".tmp").write_text(text, newline="\n")
    for p in tmp:
        os.replace(str(p) + ".tmp", p)


def summary(ledger_rows, rows):
    trades = {p: [] for p in PORTS}
    open_ = {}
    for dt, port, t, side, price, sh, cost, reason in ledger_rows:
        if side == "BUY":
            open_[(port, t)] = (price, sh, cost)
        elif side == "SELL":
            bp, bs, bc = open_.pop((port, t))
            trades[port].append((price - bp) * sh - cost - bc)
    last = rows[-1]
    print(f"\nTEST REPLAY, NOT A TRACK RECORD. Final day {last['date']}")
    for p in PORTS:
        r = trades[p]
        wins = [x for x in r if x > 0]
        print(f"  {p:6} equity {last[p]:>11,.0f}  trades {len(r):>3}  win rate {len(wins) / max(len(r), 1):.0%}"
              f"  (price P&L only, dividends excluded)")
    print("  benchmarks: " + "  ".join(f"{b} {last[b]:,.0f}" for b in BENCHES))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", nargs=2, metavar=("START", "END"), help="TEST ONLY historical replay")
    ap.add_argument("--out", help="output dir for --replay (must not be the live dir)")
    a = ap.parse_args()

    if a.replay:
        if not a.out or Path(a.out).resolve() == LIVE_DIR.resolve():
            sys.exit("--replay needs --out pointing somewhere other than data/paper.")
        out, state = Path(a.out), new_state()
        for f in ("ledger.csv", "equity.json", "state.json"):  # a replay always starts fresh
            (out / f).unlink(missing_ok=True)
        first, last = pd.Timestamp(a.replay[0]), pd.Timestamp(a.replay[1])
        px, divs, splits = fetch(first, (last + timedelta(days=1)).strftime("%Y-%m-%d"))
        meta = dict(test=True, label="TEST REPLAY, NOT A TRACK RECORD", source="Yahoo Finance via yfinance, raw prices")
    else:
        if not START_DATE:
            sys.exit("Spec not locked: START_DATE is not set. Refusing to write live files.")
        out, first, last = LIVE_DIR, pd.Timestamp(START_DATE), pd.Timestamp.max
        sp = out / "state.json"
        state = json.loads(sp.read_text()) if sp.exists() else new_state()
        if state["last_date"] is None and first < pd.Timestamp.now().normalize() - timedelta(days=5):
            sys.exit("START_DATE is in the past; a backfill would not be pre-registered. Refusing.")
        anchor = pd.Timestamp(state["last_date"]) if state["last_date"] else first
        px, divs, splits = fetch(anchor)
        # Today's bar can be partial before the close; locking it into an append-only ledger would be wrong.
        now_et = pd.Timestamp.now(tz="America/New_York")
        if px["Close"].index[-1].date() == now_et.date() and now_et.hour * 60 + now_et.minute < 16 * 60 + 30:
            px = {k: v.iloc[:-1] for k, v in px.items()}
            divs = divs.iloc[:-1]
            splits = splits.iloc[:-1]
        meta = dict(test=False, source="Yahoo Finance via yfinance, raw prices")

    ledger, rows, notes = run(px, divs, splits, state, first, last)
    if not rows:
        print("Nothing new to process. No files changed.")
        return
    write_all(out, state, ledger, rows, meta)
    print(f"Processed {len(rows)} day(s) through {rows[-1]['date']}; {sum(r[3] in ('BUY', 'SELL') for r in ledger)} fills. Wrote {out}")
    if a.replay:
        for n in notes:
            print("  note:", n)
        summary(ledger, rows)


if __name__ == "__main__":
    main()
