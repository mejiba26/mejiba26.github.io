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


def main():
    tickers = list(SECTORS) + [BENCHMARK]
    data = yf.download(tickers, period="15mo", auto_adjust=True, progress=False)
    if data.empty:
        sys.exit("Download returned no data. Leaving the existing file alone.")
    prices = data["Close"]
    missing = [t for t in tickers if t not in prices or prices[t].dropna().empty]
    if missing:
        sys.exit(f"Missing data for {missing}. Leaving the existing file alone.")

    result = compute(prices)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2) + "\n")
    top = ", ".join(s["ticker"] for s in result["sectors"][:3])
    print(f"Wrote {OUT} (as of {result['as_of']}). 3M leaders: {top}")


if __name__ == "__main__":
    main()
