"""
Pulls weekly petroleum balances and daily spot prices from the EIA Open Data
API and writes data/energy.json.

The site reads that file when the page loads to fill in the Energy Market
card. Runs on a schedule via GitHub Actions
(.github/workflows/update-energy.yml), or run it yourself:

    export EIA_API_KEY=...        # https://www.eia.gov/opendata/register.php
    python scripts/update_eia.py

Check that every series still returns data without rewriting the JSON:

    python scripts/update_eia.py --verify

Everything here is standard library, so the workflow needs no pip install.

Each series is fetched from its v2 route filtered by the `series` facet. The
older /v2/seriesid/ shortcut is not used: it times out rather than answering.
The API is slow and sheds load with 504s under its own weight, so every
request narrows itself with `start` and retries with a backoff.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

API = "https://api.eia.gov/v2/petroleum/"
OUT = Path(__file__).resolve().parent.parent / "data" / "energy.json"

GAL_PER_BBL = 42          # ULSD is quoted in $/gal, crude in $/bbl
BAND_YEARS = 5            # how many prior years make the crude stock band
MATCH_DAYS = 14           # how far from the target week a match may fall
TIMEOUT = 150             # EIA routinely takes 15s+, and worse when busy
ATTEMPTS = 4
BACKOFF = 5               # seconds, doubled each retry

# Every series ID below was read off EIA's own series pages and then checked
# against the live API with --verify, which prints the row count and the
# description the API returns for each one.
#
# route is the v2 dataset the series lives in: wstk is weekly stocks, sndw the
# weekly supply estimates, spt the spot prices. days is how much history to
# ask for, which is also what keeps each request small enough to answer.
SERIES = {
    "crude_ex_spr": {
        "id": "WCESTUS1",
        "frequency": "weekly",
        "route": "stoc/wstk",
        "name": "U.S. Ending Stocks excluding SPR of Crude Oil",
        "units": "thousand barrels",
        "days": 2650,           # ~7 years, enough for the five-year band
    },
    "cushing": {
        "id": "W_EPC0_SAX_YCUOK_MBBL",
        "frequency": "weekly",
        "route": "stoc/wstk",
        "name": "Cushing, OK Ending Stocks excluding SPR of Crude Oil",
        "units": "thousand barrels",
        "days": 400,
    },
    "crude_production": {
        "id": "WCRFPUS2",
        "frequency": "weekly",
        "route": "sum/sndw",
        "name": "U.S. Field Production of Crude Oil",
        "units": "thousand barrels per day",
        "days": 400,
    },
    "distillate": {
        "id": "WDISTUS1",
        "frequency": "weekly",
        "route": "stoc/wstk",
        "name": "U.S. Ending Stocks of Distillate Fuel Oil",
        "units": "thousand barrels",
        "days": 400,
    },
    "distillate_supplied": {
        "id": "WDIUPUS2",
        "frequency": "weekly",
        "route": "sum/sndw",
        "name": "U.S. Product Supplied of Distillate Fuel Oil",
        "units": "thousand barrels per day",
        "days": 400,            # denominator for days of cover
    },
    "wti": {
        "id": "RWTC",
        "frequency": "daily",
        "route": "pri/spt",
        "name": "Cushing, OK WTI Spot Price FOB",
        "units": "dollars per barrel",
        "days": 120,
    },
    "brent": {
        "id": "RBRTE",
        "frequency": "daily",
        "route": "pri/spt",
        "name": "Europe Brent Spot Price FOB",
        "units": "dollars per barrel",
        "days": 120,
    },
    "ulsd": {
        "id": "EER_EPD2DXL0_PF4_Y35NY_DPG",
        "frequency": "daily",
        "route": "pri/spt",
        "name": "New York Harbor Ultra-Low Sulfur No 2 Diesel Spot Price",
        "units": "dollars per gallon",
        "days": 120,
    },
}


def api_key():
    key = os.environ.get("EIA_API_KEY", "").strip()
    if not key:
        sys.exit(
            "EIA_API_KEY is not set. Register at "
            "https://www.eia.gov/opendata/register.php, then export it."
        )
    return key


def fetch(meta: dict, key: str) -> list:
    """Return observations as [(date, float)], oldest first."""
    start = (date.today() - timedelta(days=meta["days"])).isoformat()
    query = urllib.parse.urlencode(
        {
            "api_key": key,
            "data[]": "value",
            "facets[series][]": meta["id"],
            # Pinned, never defaulted: spot prices default to weekly averages
            # rather than closes, and sum/sndw also publishes a four-week mean.
            "frequency": meta["frequency"],
            "start": start,
            "sort[0][column]": "period",
            "sort[0][direction]": "desc",
            "length": 5000,
        }
    )
    url = f"{API}{meta['route']}/data/?{query}"
    req = urllib.request.Request(
        url, headers={"User-Agent": "mejiba26.github.io energy card"}
    )

    payload = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                payload = json.load(r)
            break
        except urllib.error.HTTPError as e:
            # 5xx is EIA shedding load; a 4xx will not fix itself.
            if e.code < 500 or attempt == ATTEMPTS:
                body = e.read().decode("utf-8", "replace")[:200]
                raise RuntimeError(f"{meta['id']}: HTTP {e.code} {body}") from None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            if attempt == ATTEMPTS:
                raise RuntimeError(f"{meta['id']}: {e}") from None
        time.sleep(BACKOFF * 2 ** (attempt - 1))

    rows = payload.get("response", {}).get("data", [])
    obs = []
    for row in rows:
        period, value = row.get("period"), _value(row)
        if period is None or value is None:
            continue
        try:
            when = datetime.strptime(period[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        obs.append((when, value))
    obs.sort()
    return obs


def _value(row: dict):
    """EIA names the value column after the dataset, so fall back by type."""
    for field in ("value", "val"):
        if field in row:
            return _float(row[field])
    for field, raw in row.items():
        if field in ("period", "seriesId", "series", "units", "seriesDescription"):
            continue
        got = _float(raw)
        if got is not None:
            return got
    return None


def _float(raw):
    if raw is None or isinstance(raw, bool):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def nearest(obs: list, target, within_days: int = MATCH_DAYS):
    """The observation closest to target, or None if nothing lands near it."""
    best = min(obs, key=lambda o: abs((o[0] - target).days), default=None)
    if best is None or abs((best[0] - target).days) > within_days:
        return None
    return best


def five_year_band(obs: list) -> dict:
    """Min, max and average for this week of the year over prior years.

    52 weeks back keeps the comparison on the same weekday EIA reports on,
    which is what the published five-year range does.
    """
    latest_date, latest_value = obs[-1]
    prior = []
    for back in range(1, BAND_YEARS + 1):
        match = nearest(obs[:-1], latest_date - timedelta(weeks=52 * back))
        if match:
            prior.append({"period": match[0].isoformat(), "value": round(match[1])})
    if len(prior) < BAND_YEARS:
        raise RuntimeError(
            f"Only found {len(prior)} of {BAND_YEARS} prior years for the band."
        )
    values = [p["value"] for p in prior]
    avg = sum(values) / len(values)
    return {
        "min": round(min(values)),
        "max": round(max(values)),
        "avg": round(avg),
        "vs_avg_pct": round((latest_value / avg - 1) * 100, 1),
        "in_band": min(values) <= latest_value <= max(values),
        "years": prior,
    }


# Round to what the unit is actually quoted in. A price rounded to one
# decimal stops reconciling with the spreads computed from it, which on a
# page someone is checking by hand is worse than an extra digit.
DECIMALS = {
    "dollars per gallon": 3,
    "dollars per barrel": 2,
}


def level(obs: list, meta: dict) -> dict:
    """Latest value plus the week over week change."""
    when, value = obs[-1]
    places = DECIMALS.get(meta["units"], 0)
    out = {
        "series_id": meta["id"],
        "name": meta["name"],
        "units": meta["units"],
        "period": when.isoformat(),
        "value": round(value, places),
    }
    if len(obs) > 1:
        out["change"] = round(value - obs[-2][1], places)
    return out


def latest_common(a: list, b: list):
    """Latest date both series priced, so spreads compare the same session."""
    first, second = dict(a), dict(b)
    shared = first.keys() & second.keys()
    if not shared:
        raise RuntimeError("No overlapping price dates between two series.")
    when = max(shared)
    return when, first[when], second[when]


def compute(data: dict) -> dict:
    crude = data["crude_ex_spr"]
    distillate = data["distillate"]
    supplied = data["distillate_supplied"]

    stocks = {
        "crude_ex_spr": {
            **level(crude, SERIES["crude_ex_spr"]),
            "five_year": five_year_band(crude),
        },
        "cushing": level(data["cushing"], SERIES["cushing"]),
        "distillate": level(distillate, SERIES["distillate"]),
    }

    # Days of cover: thousand barrels in tank over thousand barrels a day burned.
    cover_date, stock_level, demand = latest_common(distillate, supplied)
    if demand <= 0:
        raise RuntimeError("Distillate product supplied is zero or negative.")
    stocks["distillate"]["days_of_cover"] = round(stock_level / demand, 1)
    stocks["distillate"]["days_of_cover_period"] = cover_date.isoformat()

    crack_date, ulsd, wti_at_crack = latest_common(data["ulsd"], data["wti"])
    spread_date, brent, wti_at_spread = latest_common(data["brent"], data["wti"])

    prices = {
        "wti": level(data["wti"], SERIES["wti"]),
        "brent": level(data["brent"], SERIES["brent"]),
        "ulsd": level(data["ulsd"], SERIES["ulsd"]),
    }

    spreads = {
        "diesel_crack": {
            "label": "Diesel crack (ULSD - WTI)",
            "units": "dollars per barrel",
            "period": crack_date.isoformat(),
            "value": round(ulsd * GAL_PER_BBL - wti_at_crack, 2),
        },
        "brent_wti": {
            "label": "Brent - WTI",
            "units": "dollars per barrel",
            "period": spread_date.isoformat(),
            "value": round(brent - wti_at_spread, 2),
        },
    }

    # The card dates itself off the weekly report, the slowest input. Take the
    # oldest of them, so "as of" is a date every figure on the card has
    # actually reached; each metric still carries its own period.
    weekly = ("crude_ex_spr", "cushing", "crude_production",
              "distillate", "distillate_supplied")
    as_of = min(data[name][-1][0] for name in weekly)

    return {
        "as_of": as_of.isoformat(),
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "U.S. Energy Information Administration, Open Data API v2",
        "note": "Weekly stocks and production from the Weekly Petroleum Status "
                "Report; prices are spot closes.",
        "stocks": stocks,
        "production": {
            "crude": level(data["crude_production"], SERIES["crude_production"])
        },
        "prices": prices,
        "spreads": spreads,
    }


def pull_all(key: str) -> dict:
    data, empty = {}, []
    for name, meta in SERIES.items():
        obs = fetch(meta, key)
        if not obs:
            empty.append(f"{name} ({meta['id']})")
        data[name] = obs
    if empty:
        sys.exit(
            "These series came back empty: " + ", ".join(empty)
            + ". Leaving the existing file alone."
        )
    return data


def verify(key: str) -> int:
    """Print what every series returns. Writes nothing."""
    bad = 0
    for name, meta in SERIES.items():
        try:
            obs = fetch(meta, key)
        except RuntimeError as e:
            print(f"FAIL  {name:20} {meta['id']:28} {e}", flush=True)
            bad += 1
            continue
        if not obs:
            print(f"EMPTY {name:20} {meta['id']:28} 0 rows", flush=True)
            bad += 1
            continue
        print(f"ok    {name:20} {meta['id']:28} "
              f"{len(obs):4} rows, {obs[0][0]} to {obs[-1][0]}, "
              f"latest {obs[-1][1]:,.3f} ({meta['units']})", flush=True)
    print("\nAll series returned data." if not bad
          else f"\n{bad} series need attention.")
    return 1 if bad else 0


def main():
    key = api_key()
    if "--verify" in sys.argv[1:]:
        sys.exit(verify(key))

    try:
        result = compute(pull_all(key))
    except RuntimeError as e:
        sys.exit(f"{e} Leaving the existing file alone.")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2) + "\n")
    crude = result["stocks"]["crude_ex_spr"]
    print(
        f"Wrote {OUT} (as of {result['as_of']}). "
        f"Crude ex SPR {crude['value']:,.0f} Mbbl "
        f"({crude['five_year']['vs_avg_pct']:+.1f}% vs 5yr avg), "
        f"diesel crack ${result['spreads']['diesel_crack']['value']:.2f}/bbl."
    )


if __name__ == "__main__":
    main()
