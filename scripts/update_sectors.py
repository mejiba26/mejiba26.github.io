"""
Pulls prices for the 11 SPDR sector ETFs and SPY, computes 1 month,
3 month, and year to date returns, and writes data/sectors.json.

The site reads that file when the page loads to fill in the
Sector Rotation card. Runs on a schedule via GitHub Actions
(.github/workflows/update-sectors.yml), or run it yourself:

    pip install yfinance pandas
    python scripts/update_sectors.py
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

SECTORS = {
    "XLK": "Technology",
    "XLF": "Financials",
    "XLE": "Energy",
    "XLV": "Health Care",
    "XLI": "Industrials",
    "XLY": "Consumer Discretionary",
    "XLP": "Consumer Staples",
    "XLU": "Utilities",
    "XLB": "Materials",
    "XLRE": "Real Estate",
    "XLC": "Communication Services",
}
BENCHMARK = "SPY"

# Trading day lookbacks
WINDOWS = {"r1m": 21, "r3m": 63}

OUT = Path(__file__).resolve().parent.parent / "data" / "sectors.json"

# Download mechanics, same as scripts/paper_trade.py.
DOWNLOAD_TRIES, DOWNLOAD_WAIT_S = 3, 5


def pct(new, old):
    return round((new / old - 1) * 100, 2)


def compute(prices: pd.DataFrame) -> dict:
    """prices: daily adjusted closes, one column per ticker, DatetimeIndex."""
    prices = prices.sort_index().dropna(how="all")
    last_date = prices.index[-1]
    prior_year = prices[prices.index.year < last_date.year]
    if prior_year.empty:
        raise ValueError("Need data from before Jan 1 of this year for YTD.")

    def row(ticker):
        s = prices[ticker].dropna()
        if len(s) <= max(WINDOWS.values()):
            raise ValueError(f"Not enough history for {ticker}")
        out = {k: pct(s.iloc[-1], s.iloc[-1 - n]) for k, n in WINDOWS.items()}
        base = prior_year[ticker].dropna().iloc[-1]
        out["ytd"] = pct(s.iloc[-1], base)
        return out

    sectors = []
    for t, name in SECTORS.items():
        sectors.append({"ticker": t, "name": name, **row(t)})
    sectors.sort(key=lambda d: d["r3m"], reverse=True)

    return {
        "as_of": last_date.strftime("%Y-%m-%d"),
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "Yahoo Finance via yfinance, adjusted closes (total return)",
        "sorted_by": "r3m",
        "benchmark": {"ticker": BENCHMARK, "name": "S&P 500", **row(BENCHMARK)},
        "sectors": sectors,
    }


def missing_tickers(raw, tickers):
    """Tickers with no usable Close data in a yfinance download (all of them if it is empty)."""
    if raw is None or raw.empty:
        return list(tickers)
    close = raw["Close"]
    return [t for t in tickers if t not in close or close[t].dropna().empty]


def download(tickers):
    """Up to DOWNLOAD_TRIES attempts. Exits non zero, naming the tickers, if any still has no data."""
    for attempt in range(1, DOWNLOAD_TRIES + 1):
        try:
            # threads=False: parallel downloads race on yfinance's shared cache ("database is locked").
            raw = yf.download(tickers, period="15mo", auto_adjust=True, progress=False, threads=False)
        except Exception as e:  # network or cache error: treat like a failed attempt and retry
            raw, err = None, f"{type(e).__name__}: {e}"
        else:
            err = ""
        missing = missing_tickers(raw, tickers)
        if not missing:
            return raw
        print(f"Download attempt {attempt}/{DOWNLOAD_TRIES} missing {missing} {err}".rstrip(), file=sys.stderr)
        if attempt < DOWNLOAD_TRIES:
            time.sleep(DOWNLOAD_WAIT_S)
    sys.exit(f"Price download failed for {missing} after {DOWNLOAD_TRIES} attempts. Leaving the existing file alone.")


def main():
    tickers = list(SECTORS) + [BENCHMARK]
    prices = download(tickers)["Close"]

    result = compute(prices)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2) + "\n")
    top = ", ".join(s["ticker"] for s in result["sectors"][:3])
    print(f"Wrote {OUT} (as of {result['as_of']}). 3M leaders: {top}")


if __name__ == "__main__":
    main()
