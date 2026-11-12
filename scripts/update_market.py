"""
Pulls the Federal Reserve's federal funds target range from FRED and writes
data/market.json.

The Fed policy item in the Market View section reads that file when the page
loads. Runs on a schedule via GitHub Actions
(.github/workflows/update-market.yml), or run it yourself:

    python scripts/update_market.py

Needs no API key and no third party packages. It uses the public FRED CSV
download for two daily series:

    DFEDTARU  federal funds target range, upper limit
    DFEDTARL  federal funds target range, lower limit

The script exits non zero on any problem (no data, malformed rows, an odd
range, data that is too old) and leaves the existing file alone, so a broken
fetch fails the Actions run instead of publishing a wrong number.
"""

import csv
import io
import json
import sys
import time
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

SERIES_UPPER = "DFEDTARU"
SERIES_LOWER = "DFEDTARL"
FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={id}&cosd={start}"
# Enough history to find the most recent change even after a long hold.
HISTORY_START = "2023-01-01"

# FRED carries the latest value forward every calendar day, so the newest
# observation should be at most a few days old.
MAX_AGE_DAYS = 10

OUT = Path(__file__).resolve().parent.parent / "data" / "market.json"

DOWNLOAD_TRIES, DOWNLOAD_WAIT_S = 3, 5


def parse_series(text: str, series_id: str) -> dict:
    """FRED CSV text -> {date: float}. Missing observations ('.' or blank) are skipped."""
    rows = list(csv.reader(io.StringIO(text)))
    if len(rows) < 2:
        raise ValueError(f"{series_id}: no data rows")
    header = rows[0]
    if len(header) < 2 or header[1].strip() != series_id:
        raise ValueError(f"{series_id}: unexpected header {header}")
    out = {}
    for r in rows[1:]:
        if len(r) < 2 or r[1].strip() in ("", "."):
            continue
        d = date.fromisoformat(r[0].strip())  # raises on a malformed date
        out[d] = float(r[1])
    if not out:
        raise ValueError(f"{series_id}: every observation is empty")
    return out


def compute(upper: dict, lower: dict, today: date) -> dict:
    """Build the market.json payload from the two series."""
    days = sorted(set(upper) & set(lower))
    if not days:
        raise ValueError("upper and lower series share no dates")
    as_of = days[-1]
    if (today - as_of).days > MAX_AGE_DAYS:
        raise ValueError(f"newest observation {as_of} is more than {MAX_AGE_DAYS} days old")

    hi, lo = upper[as_of], lower[as_of]
    width = round(hi - lo, 4)
    if not (0 <= lo < hi <= 15) or not (0 < width <= 1):
        raise ValueError(f"implausible target range {lo} to {hi}")

    # Walk back to the first day the current range was in place.
    i = len(days) - 1
    while i > 0 and upper[days[i - 1]] == hi and lower[days[i - 1]] == lo:
        i -= 1

    fed = {"lower": lo, "upper": hi, "last_change": None}
    if i > 0:  # the range changed at least once inside the history window
        prev = days[i - 1]
        bp = round((hi - upper[prev]) * 100)
        if bp == 0:  # only the lower limit moved: not a policy change we describe
            raise ValueError("range changed with no move in the upper limit")
        fed["last_change"] = {
            "date": days[i].isoformat(),
            "direction": "increase" if bp > 0 else "cut",
            "bp": abs(bp),
            "prior_lower": lower[prev],
            "prior_upper": upper[prev],
        }

    return {
        "as_of": as_of.isoformat(),
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "Federal Reserve target range via FRED (DFEDTARU, DFEDTARL)",
        "fed": fed,
    }


def fetch(series_id: str) -> str:
    url = FRED_CSV.format(id=series_id, start=HISTORY_START)
    err = ""
    for attempt in range(1, DOWNLOAD_TRIES + 1):
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                return resp.read().decode("utf-8")
        except Exception as e:  # network error: retry, then fail loudly
            err = f"{type(e).__name__}: {e}"
            print(f"Fetch attempt {attempt}/{DOWNLOAD_TRIES} for {series_id} failed: {err}", file=sys.stderr)
            if attempt < DOWNLOAD_TRIES:
                time.sleep(DOWNLOAD_WAIT_S)
    sys.exit(f"FRED download failed for {series_id} after {DOWNLOAD_TRIES} attempts ({err}). Leaving the existing file alone.")


def main():
    try:
        upper = parse_series(fetch(SERIES_UPPER), SERIES_UPPER)
        lower = parse_series(fetch(SERIES_LOWER), SERIES_LOWER)
        result = compute(upper, lower, date.today())
    except ValueError as e:
        sys.exit(f"Bad FRED data: {e}. Leaving the existing file alone.")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2) + "\n")
    f = result["fed"]
    print(f"Wrote {OUT} (as of {result['as_of']}). Target range {f['lower']} to {f['upper']}, last change {f['last_change']}")


if __name__ == "__main__":
    main()
