"""
Offline test for scripts/paper_trade.py. SYNTHETIC PRICES, NOT A TRACK RECORD. No network.

    python scripts/dev/test_paper_trade.py

It drives the real main() live path with the Yahoo download replaced by fake prices that
"reveal" one more day each time, and data/paper replaced by a temp folder. Two assertions:

  1. A rerun on the same day changes nothing (files are byte for byte the same).
  2. A run split across several days gives exactly the same ledger, equity and state as one
     continuous run.

Break the resume logic in run() (for example change `>` to `>=` in the `todo` filter) and both fail.
"""
import json
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import paper_trade as P  # noqa: E402

TICKERS = P.UNIVERSE + [P.BENCH_SPY]
FILES = ("ledger.csv", "equity.json", "state.json")


def synthetic(start_day, warm=300, live=120, seed=7):
    """Uptrend + wave + noise, so pullback entries really happen. Index: `warm` days before start_day, then `live`."""
    idx = pd.bdate_range(end=start_day - pd.Timedelta(days=1), periods=warm).append(pd.bdate_range(start_day, periods=live))
    n, t, rng = len(idx), np.arange(len(idx)), np.random.default_rng(seed)
    cols = {k: [] for k in ("Open", "High", "Low", "Close")}
    for j in range(len(TICKERS)):
        c = np.exp(4.5 + 0.0009 * t + 0.035 * np.sin(t / (7 + j) + j) + np.cumsum(rng.normal(0, 0.009, n)))
        o = np.r_[c[0], c[:-1]] * (1 + rng.normal(0, 0.003, n))
        cols["Open"].append(o)
        cols["High"].append(np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.004, n))))
        cols["Low"].append(np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.004, n))))
        cols["Close"].append(c)
    px = {k: pd.DataFrame(np.array(v).T, index=idx, columns=TICKERS) for k, v in cols.items()}
    divs = pd.DataFrame(0.0, index=idx, columns=TICKERS)
    divs.iloc[warm + 5::9, :] = 0.3   # frequent dividends on every ticker, so some land on held positions
    return px, divs, pd.DataFrame(0.0, index=idx, columns=TICKERS)


class Sim:
    """A fake Yahoo: fetch() serves history only up to `visible_end`. Patches paper_trade for one test."""

    def __init__(self, out):
        # START_DATE must not be in the past (main refuses a backfill), so it sits a few days ahead.
        self.start = pd.bdate_range(pd.Timestamp.now().normalize() + pd.Timedelta(days=3), periods=1)[0]
        self.px, self.divs, self.splits = synthetic(self.start)
        self.out, self.visible_end = Path(out), None

    def day(self, k):   # the k-th trading day after START_DATE (0 = START_DATE)
        return self.px["Close"].index[300 + k]

    def fetch(self, start, end=None):
        lo = start - pd.Timedelta(days=P.WARMUP_DAYS)
        sel = (self.px["Close"].index >= lo) & (self.px["Close"].index <= self.visible_end)
        return ({k: v[sel] for k, v in self.px.items()}, self.divs[sel], self.splits[sel])

    def __enter__(self):
        self._old = (P.fetch, P.LIVE_DIR, P.START_DATE, sys.argv)
        P.fetch, P.LIVE_DIR, P.START_DATE, sys.argv = self.fetch, self.out, self.start.strftime("%Y-%m-%d"), ["paper_trade.py"]
        return self

    def __exit__(self, *a):
        P.fetch, P.LIVE_DIR, P.START_DATE, sys.argv = self._old

    def run_through(self, k):
        self.visible_end = self.day(k)
        P.main()

    def snapshot(self):
        return {f: (self.out / f).read_bytes() for f in FILES if (self.out / f).exists()}


def comparable(out):
    eq = json.loads((out / "equity.json").read_text())
    eq.pop("generated_utc")   # a wall clock stamp; the only field allowed to differ between runs
    return (out / "ledger.csv").read_text(), eq, json.loads((out / "state.json").read_text())


def test_rerun_same_day_changes_nothing():
    with tempfile.TemporaryDirectory() as d, Sim(Path(d) / "paper") as s:
        s.run_through(30)
        first = s.snapshot()
        assert len(first) == 3, "first run should write all three files"
        s.run_through(30)   # same day again
        assert s.snapshot() == first, "a rerun on the same day changed the files"


def test_split_run_equals_continuous_run():
    last = 119
    with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
        with Sim(Path(d1) / "paper") as cont:
            cont.run_through(last)
            ledger, eq, state = comparable(cont.out)
        assert ledger.count(",BUY,") >= 3 and ledger.count(",SELL,") >= 3 and ",DIV," in ledger, \
            "synthetic data must produce trades and dividends, or this test proves nothing"
        with Sim(Path(d2) / "paper") as chunked:
            for k in (0, 1, 4, 4, 11, 25, 26, 60, 90, last):   # includes a same day repeat
                chunked.run_through(k)
            c_ledger, c_eq, c_state = comparable(chunked.out)
        assert c_ledger == ledger, "ledger differs between the split run and the continuous run"
        assert c_eq == eq, "equity differs between the split run and the continuous run"
        assert c_state == state, "state differs between the split run and the continuous run"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"PASS {name}")
    print("ALL PASSED (synthetic prices, no network)")
