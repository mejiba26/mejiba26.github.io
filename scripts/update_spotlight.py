"""
Monthly spotlight for the Market View section. Writes data/spotlight.json.

What it does, with no judgment calls and no text written by a model:

1. Takes the previous full calendar month (if the newest price is in November,
   the month is October). Start price is the close on the last trading day of
   the month before it, end price is the close on the last trading day of the
   month.
2. Computes each name's price return over that month and its excess return
   over SPY, for the 11 SPDR sector ETFs and a fixed list of large caps.
3. The spotlight stock is the large cap with the largest ABSOLUTE excess
   return versus SPY. The spotlight market is the sector ETF with the largest
   absolute excess return. Ties go to the alphabetically first ticker. This
   is a rule about what moved most, not a view on what to buy.
4. Attaches the live external drivers for the spotlight's group (oil, 10 year
   yield, dollar, VIX, copper): the change over the same month and the latest
   value. The one sentence explaining why each group is exposed to them is
   evergreen text, written once below in EXPOSURES.
5. Records the latest close and the change since month end for every name.

Runs weekdays via .github/workflows/update-spotlight.yml, or run it yourself:

    pip install yfinance pandas
    python scripts/update_spotlight.py

Prices are unadjusted closes, so the levels match what a quote screen shows,
and returns are price returns without dividends. Exits non zero on any
missing data and leaves the existing file alone.
"""

import json
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

BENCHMARK = "SPY"

# (name, group). Edit this list to change the universe; nothing else needs to change.
STOCKS = {
    "AAPL": ("Apple", "Technology"),
    "MSFT": ("Microsoft", "Technology"),
    "NVDA": ("NVIDIA", "Technology"),
    "AMZN": ("Amazon", "Consumer Discretionary"),
    "HD": ("Home Depot", "Consumer Discretionary"),
    "GOOGL": ("Alphabet", "Communication Services"),
    "META": ("Meta Platforms", "Communication Services"),
    "JPM": ("JPMorgan Chase", "Financials"),
    "XOM": ("Exxon Mobil", "Energy"),
    "CVX": ("Chevron", "Energy"),
    "LLY": ("Eli Lilly", "Health Care"),
    "UNH": ("UnitedHealth", "Health Care"),
    "WMT": ("Walmart", "Consumer Staples"),
    "PG": ("Procter & Gamble", "Consumer Staples"),
    "CAT": ("Caterpillar", "Industrials"),
    "GE": ("GE Aerospace", "Aerospace & Defense"),
    "RTX": ("RTX", "Aerospace & Defense"),
    "LMT": ("Lockheed Martin", "Aerospace & Defense"),
    "NOC": ("Northrop Grumman", "Aerospace & Defense"),
    "GD": ("General Dynamics", "Aerospace & Defense"),
}

SECTORS = {
    "XLK": ("Technology", "Technology"),
    "XLF": ("Financials", "Financials"),
    "XLE": ("Energy", "Energy"),
    "XLV": ("Health Care", "Health Care"),
    "XLI": ("Industrials", "Industrials"),
    "XLY": ("Consumer Discretionary", "Consumer Discretionary"),
    "XLP": ("Consumer Staples", "Consumer Staples"),
    "XLU": ("Utilities", "Utilities"),
    "XLB": ("Materials", "Materials"),
    "XLRE": ("Real Estate", "Real Estate"),
    "XLC": ("Communication Services", "Communication Services"),
}

# key: (label, Yahoo symbol, how the change is shown). "bp" is for yields.
DRIVERS = {
    "wti": ("WTI crude oil", "CL=F", "pct"),
    "brent": ("Brent crude oil", "BZ=F", "pct"),
    "tnx": ("10 year Treasury yield", "^TNX", "bp"),
    "usd": ("US dollar index", "DX-Y.NYB", "pct"),
    "vix": ("VIX", "^VIX", "pct"),
    "copper": ("Copper", "HG=F", "pct"),
}

# Evergreen exposure notes. These say what the group is sensitive to, never
# which way it will move. They do not change month to month.
EXPOSURES = {
    "Technology": (["tnx", "usd"], "Long dated cash flows make valuations sensitive to the discount rate, and a large share of revenue is earned abroad, so a stronger dollar can weigh on reported sales."),
    "Communication Services": (["tnx", "vix"], "Advertising spend follows the business cycle, so growth scares tend to hit it, and growth style valuations are sensitive to interest rates."),
    "Consumer Discretionary": (["tnx", "wti"], "Big ticket spending depends on borrowing costs, and fuel prices take from household budgets."),
    "Consumer Staples": (["usd", "wti"], "Demand is steady, but input and freight costs move margins, and a stronger dollar reduces the value of foreign sales."),
    "Financials": (["tnx", "vix"], "Net interest margins depend on the level and shape of the yield curve, and market stress shows up in credit losses and trading."),
    "Energy": (["wti", "brent"], "Earnings follow crude and refined product prices, which respond to supply decisions, inventories and demand."),
    "Health Care": (["tnx", "vix"], "Demand is defensive, and policy on drug pricing and reimbursement is the main outside risk, while long duration growth names are sensitive to interest rates."),
    "Industrials": (["copper", "wti"], "Orders follow the business cycle, and metals and energy costs reach margins through suppliers."),
    "Aerospace & Defense": (["wti", "copper"], "Government budgets and contract timing set demand. Energy and metals costs can reach margins through suppliers, a link I am testing rather than assuming."),
    "Utilities": (["tnx", "wti"], "Utilities are priced like bond proxies, so yields matter, and fuel costs flow through regulated rates."),
    "Materials": (["copper", "usd"], "Metals and chemicals are priced globally, and a stronger dollar tends to pressure dollar priced commodities."),
    "Real Estate": (["tnx", "vix"], "Financing costs and valuation rates are tied to Treasury yields, and market stress tightens credit."),
}

RULE = ("Largest absolute price move versus SPY over the previous full calendar month. "
        "A rule about what moved most, not a view on what to buy.")

MIN_HISTORY_DAYS = 40
# Weekdays run, so as_of is normally 0 to 3 days old.
MAX_AGE_DAYS = 6

OUT = Path(__file__).resolve().parent.parent / "data" / "spotlight.json"
DOWNLOAD_TRIES, DOWNLOAD_WAIT_S = 3, 5


def pct(new, old):
    return round((new / old - 1) * 100, 2)


def month_bounds(prices: pd.DataFrame):
    """(start_date, end_date, 'YYYY-MM') for the previous full calendar month in the data."""
    idx = prices.index
    last = idx[-1]
    y, m = (last.year, last.month - 1) if last.month > 1 else (last.year - 1, 12)
    in_month = idx[(idx.year == y) & (idx.month == m)]
    if len(in_month) < 10:
        raise ValueError(f"only {len(in_month)} trading days of {y}-{m:02d} in the data")
    end = in_month[-1]
    before = idx[idx < in_month[0]]
    if len(before) == 0:
        raise ValueError(f"no close before {y}-{m:02d} to start from")
    return before[-1], end, f"{y}-{m:02d}"


def at(series: pd.Series, d):
    """Last non empty value on or before d."""
    s = series.loc[:d].dropna()
    if s.empty:
        raise ValueError(f"no value on or before {d}")
    return float(s.iloc[-1])


def driver_row(key, series, start, end):
    label, _sym, kind = DRIVERS[key]
    s0, s1, now = at(series, start), at(series, end), float(series.dropna().iloc[-1])
    if key == "tnx" and not (0 < now < 20 and 0 < s0 < 20 and 0 < s1 < 20):
        raise ValueError(f"10 year yield out of range ({s0}, {s1}, {now}); check the symbol's units")
    row = {"key": key, "label": label, "unit": kind, "start": round(s0, 3), "end": round(s1, 3), "now": round(now, 3)}
    if kind == "bp":
        row["month_chg"] = round((s1 - s0) * 100)
        row["since_chg"] = round((now - s1) * 100)
    else:
        row["month_chg"] = pct(s1, s0)
        row["since_chg"] = pct(now, s1)
    return row


def rows_for(universe, prices, start, end, bench_ret):
    out = []
    for t, (name, group) in universe.items():
        s = prices[t]
        p0, p1, now = at(s, start), at(s, end), float(s.dropna().iloc[-1])
        ret = pct(p1, p0)
        out.append({"ticker": t, "name": name, "group": group, "start": round(p0, 2), "end": round(p1, 2),
                    "month_ret": ret, "excess": round(ret - bench_ret, 2),
                    "now": round(now, 2), "since": pct(now, p1)})
    return out


def pick(rows):
    return sorted(rows, key=lambda r: (-abs(r["excess"]), r["ticker"]))[0]


def spotlight(row, driver_px, start, end):
    keys, why = EXPOSURES[row["group"]]
    return {**row, "why": why, "drivers": [driver_row(k, driver_px[DRIVERS[k][1]], start, end) for k in keys]}


def compute(prices: pd.DataFrame, driver_px: pd.DataFrame, today: date) -> dict:
    """prices: unadjusted closes for STOCKS, SECTORS and SPY. driver_px: closes for the driver symbols."""
    prices = prices.sort_index().dropna(how="all")
    driver_px = driver_px.sort_index().ffill()
    need = list(STOCKS) + list(SECTORS) + [BENCHMARK]
    absent = [t for t in need if t not in prices or prices[t].dropna().empty]
    if absent:
        raise ValueError(f"no prices for {absent}")
    absent = [v[1] for v in DRIVERS.values() if v[1] not in driver_px or driver_px[v[1]].dropna().empty]
    if absent:
        raise ValueError(f"no driver data for {absent}")
    tnx = driver_px[DRIVERS["tnx"][1]].dropna()
    if not (0 < tnx.min() and tnx.max() < 20):  # checked every run, not only when the yield is a spotlight driver
        raise ValueError(f"10 year yield out of range ({tnx.min()} to {tnx.max()}); check the symbol's units")
    if len(prices) < MIN_HISTORY_DAYS:
        raise ValueError(f"only {len(prices)} days of history")

    as_of = prices.index[-1].date()
    if (today - as_of).days > MAX_AGE_DAYS:
        raise ValueError(f"newest price {as_of} is more than {MAX_AGE_DAYS} days old")

    start, end, month = month_bounds(prices)
    bench = at(prices[BENCHMARK], end) / at(prices[BENCHMARK], start) - 1
    bench_ret = round(bench * 100, 2)
    bnow = float(prices[BENCHMARK].dropna().iloc[-1])

    stocks = rows_for(STOCKS, prices, start, end, bench_ret)
    sectors = rows_for(SECTORS, prices, start, end, bench_ret)
    stocks.sort(key=lambda r: -r["month_ret"])
    sectors.sort(key=lambda r: -r["month_ret"])

    return {
        "as_of": as_of.isoformat(),
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "Yahoo Finance via yfinance, unadjusted closes (price return)",
        "month": month,
        "start_date": start.date().isoformat(),
        "end_date": end.date().isoformat(),
        "rule": RULE,
        "benchmark": {"ticker": BENCHMARK, "start": round(at(prices[BENCHMARK], start), 2),
                      "end": round(at(prices[BENCHMARK], end), 2), "month_ret": bench_ret,
                      "now": round(bnow, 2), "since": pct(bnow, at(prices[BENCHMARK], end))},
        "spotlight_stock": spotlight(pick(stocks), driver_px, start, end),
        "spotlight_market": spotlight(pick(sectors), driver_px, start, end),
        "stocks": stocks,
        "sectors": sectors,
    }


def download(tickers, label):
    """Unadjusted closes. Up to DOWNLOAD_TRIES attempts, then exits non zero naming what is missing."""
    import yfinance as yf  # imported here so the offline test runs without it
    for attempt in range(1, DOWNLOAD_TRIES + 1):
        try:
            raw = yf.download(tickers, period="6mo", auto_adjust=False, progress=False, threads=False)
        except Exception as e:  # network or cache error: treat like a failed attempt
            raw, err = None, f"{type(e).__name__}: {e}"
        else:
            err = ""
        if raw is not None and not raw.empty:
            close = raw["Close"]
            missing = [t for t in tickers if t not in close or close[t].dropna().empty]
        else:
            missing = list(tickers)
        if not missing:
            return close
        print(f"{label} download attempt {attempt}/{DOWNLOAD_TRIES} missing {missing} {err}".rstrip(), file=sys.stderr)
        if attempt < DOWNLOAD_TRIES:
            time.sleep(DOWNLOAD_WAIT_S)
    sys.exit(f"{label} download failed for {missing} after {DOWNLOAD_TRIES} attempts. Leaving the existing file alone.")


def main():
    prices = download(list(STOCKS) + list(SECTORS) + [BENCHMARK], "Price")
    drivers = download([v[1] for v in DRIVERS.values()], "Driver")
    try:
        result = compute(prices, drivers, date.today())
    except ValueError as e:
        sys.exit(f"Bad data: {e}. Leaving the existing file alone.")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2) + "\n")
    print(f"Wrote {OUT} (as of {result['as_of']}, month {result['month']}). "
          f"Stock {result['spotlight_stock']['ticker']}, market {result['spotlight_market']['ticker']}")


if __name__ == "__main__":
    main()
