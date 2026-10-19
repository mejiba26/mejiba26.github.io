"""
Pulls weekly petroleum balances and daily spot prices from the EIA Open Data
API and writes data/energy.json.

The site reads that file when the page loads to fill in the Energy Market
card. Runs on a schedule via GitHub Actions
(.github/workflows/update-energy.yml), or run it yourself:

    export EIA_API_KEY=...        # https://www.eia.gov/opendata/register.php
    python scripts/update_eia.py

Check that every series still returns data without rewriting anything:

    python scripts/update_eia.py --verify

Throw the cache away and refetch five years from scratch:

    python scripts/update_eia.py --backfill

Everything here is standard library, so the workflow needs no pip install.

History and caching
-------------------
Percentiles need five years of history for all eight series, which is far too
much to refetch every Wednesday against an API that routinely takes 15s a call
and sheds load with 504s. So the full history lives in data/eia_history.json
and each run fetches only the last OVERLAP_DAYS and merges. The overlap is
deliberate: EIA revises recent weeks, and merging lets the new values win.
Merging is keyed on the observation date, so a date can never appear twice.

Requests go to each series' v2 route filtered by the `series` facet. The older
/v2/seriesid/ shortcut is not used: it times out rather than answering.
Frequency is pinned per series and never defaulted, because the spot price
route defaults to weekly averages rather than daily closes.
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
DATA = Path(__file__).resolve().parent.parent / "data"
OUT = DATA / "energy.json"
CACHE = DATA / "eia_history.json"
CACHE_VERSION = 1

GAL_PER_BBL = 42          # ULSD is quoted in $/gal, crude in $/bbl
BAND_YEARS = 5            # how many prior years make the crude stock band
MATCH_DAYS = 14           # how far from the target week a band match may fall
PCTL_YEARS = 5            # trailing window every percentile is measured over
MIN_SAMPLE = 100          # below this a percentile is not worth quoting
FETCH_DAYS = 2010         # ~5.5 years: covers the percentile window and the
                          # band's furthest lookback (260 weeks) with margin
OVERLAP_DAYS = 70         # refetched every run so EIA revisions land
VERIFY_DAYS = 120         # --verify only needs enough to prove data flows
TIMEOUT = 150             # EIA routinely takes 15s+, and worse when busy
ATTEMPTS = 4
BACKOFF = 5               # seconds, doubled each retry

# Every series ID below was read off EIA's own series pages and then checked
# against the live API with --verify, which prints the row count and the
# description the API returns for each one.
#
# route is the v2 dataset the series lives in: wstk is weekly stocks, sndw the
# weekly supply estimates, spt the spot prices.
SERIES = {
    "crude_ex_spr": {
        "id": "WCESTUS1",
        "route": "stoc/wstk",
        "frequency": "weekly",
        "name": "U.S. Ending Stocks excluding SPR of Crude Oil",
        "units": "thousand barrels",
    },
    "cushing": {
        "id": "W_EPC0_SAX_YCUOK_MBBL",
        "route": "stoc/wstk",
        "frequency": "weekly",
        "name": "Cushing, OK Ending Stocks excluding SPR of Crude Oil",
        "units": "thousand barrels",
    },
    "crude_production": {
        "id": "WCRFPUS2",
        "route": "sum/sndw",
        "frequency": "weekly",
        "name": "U.S. Field Production of Crude Oil",
        "units": "thousand barrels per day",
    },
    "distillate": {
        "id": "WDISTUS1",
        "route": "stoc/wstk",
        "frequency": "weekly",
        "name": "U.S. Ending Stocks of Distillate Fuel Oil",
        "units": "thousand barrels",
    },
    "distillate_supplied": {
        "id": "WDIUPUS2",
        "route": "sum/sndw",
        "frequency": "weekly",
        "name": "U.S. Product Supplied of Distillate Fuel Oil",
        "units": "thousand barrels per day",
    },
    "wti": {
        "id": "RWTC",
        "route": "pri/spt",
        "frequency": "daily",
        "name": "Cushing, OK WTI Spot Price FOB",
        "units": "dollars per barrel",
    },
    "brent": {
        "id": "RBRTE",
        "route": "pri/spt",
        "frequency": "daily",
        "name": "Europe Brent Spot Price FOB",
        "units": "dollars per barrel",
    },
    "ulsd": {
        "id": "EER_EPD2DXL0_PF4_Y35NY_DPG",
        "route": "pri/spt",
        "frequency": "daily",
        "name": "New York Harbor Ultra-Low Sulfur No 2 Diesel Spot Price",
        "units": "dollars per gallon",
    },
}

# Round to what the unit is actually quoted in. A price rounded to one decimal
# stops reconciling with the spreads computed from it, which on a page someone
# is checking by hand is worse than an extra digit.
DECIMALS = {
    "dollars per gallon": 3,
    "dollars per barrel": 2,
    "days": 1,
}


def api_key():
    key = os.environ.get("EIA_API_KEY", "").strip()
    if not key:
        sys.exit(
            "EIA_API_KEY is not set. Register at "
            "https://www.eia.gov/opendata/register.php, then export it."
        )
    return key


# ---------------------------------------------------------------- fetching

def fetch(meta: dict, key: str, start: date) -> list:
    """Return observations as [(date, float)], oldest first."""
    query = urllib.parse.urlencode(
        {
            "api_key": key,
            "data[]": "value",
            "facets[series][]": meta["id"],
            # Pinned, never defaulted: spot prices default to weekly averages
            # rather than closes, and sum/sndw also publishes a four-week mean.
            "frequency": meta["frequency"],
            "start": start.isoformat(),
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


# ------------------------------------------------------------------- cache

def load_cache() -> dict:
    """Cached history per series, or {} if there is nothing usable."""
    try:
        blob = json.loads(CACHE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    if blob.get("version") != CACHE_VERSION:
        return {}
    out = {}
    for name, entry in (blob.get("series") or {}).items():
        meta = SERIES.get(name)
        # If a series ID ever changes, the cached history is a different
        # series and must not be merged into the new one.
        if not meta or entry.get("id") != meta["id"]:
            continue
        obs = []
        for pair in entry.get("obs") or []:
            try:
                obs.append((date.fromisoformat(pair[0]), float(pair[1])))
            except (ValueError, TypeError, IndexError):
                continue
        if obs:
            out[name] = sorted(obs)
    return out


def save_cache(data: dict):
    blob = {
        "version": CACHE_VERSION,
        "updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "note": "Local history cache for scripts/update_eia.py. Generated, "
                "not hand edited. Delete it or pass --backfill to rebuild.",
        "series": {
            name: {
                "id": SERIES[name]["id"],
                "obs": [[d.isoformat(), v] for d, v in obs],
            }
            for name, obs in sorted(data.items())
        },
    }
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(blob, separators=(",", ":")) + "\n")


def merge(cached: list, fresh: list, horizon: date) -> list:
    """Fresh values win, so EIA's revisions to recent weeks are picked up."""
    merged = dict(cached)
    merged.update(dict(fresh))
    return sorted((d, v) for d, v in merged.items() if d >= horizon)


def pull_all(key: str, full: bool) -> tuple:
    cached = {} if full else load_cache()
    horizon = date.today() - timedelta(days=FETCH_DAYS)
    data, empty, fetched = {}, [], {}

    for name, meta in SERIES.items():
        have = cached.get(name, [])
        if have:
            # Only the tail, plus an overlap so revisions land.
            start = max(have[-1][0] - timedelta(days=OVERLAP_DAYS), horizon)
        else:
            start = horizon
        fresh = fetch(meta, key, start)
        fetched[name] = len(fresh)
        obs = merge(have, fresh, horizon)
        if not obs:
            empty.append(f"{name} ({meta['id']})")
        data[name] = obs

    if empty:
        sys.exit(
            "These series came back empty: " + ", ".join(empty)
            + ". Leaving the existing files alone."
        )
    return data, fetched, bool(cached)


# -------------------------------------------------------------- statistics

def percentile(series: list, value: float, units: str) -> dict:
    """Where value sits in the trailing PCTL_YEARS of its own series.

    Percentile rank, counting ties at half weight, so an unremarkable value
    in a flat series lands near 50 rather than at an extreme.
    """
    cutoff = series[-1][0] - timedelta(days=round(365.25 * PCTL_YEARS))
    vals = [v for d, v in series if d >= cutoff]
    n = len(vals)
    if n < MIN_SAMPLE:
        raise RuntimeError(
            f"Only {n} observations in the trailing {PCTL_YEARS} years, "
            f"need {MIN_SAMPLE} for a percentile."
        )
    below = sum(1 for v in vals if v < value)
    ties = sum(1 for v in vals if v == value)
    ordered = sorted(vals)
    mid = n // 2
    median = ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2
    places = DECIMALS.get(units, 0)
    window = [d for d, _ in series if d >= cutoff]
    return {
        "rank": round((below + 0.5 * ties) / n * 100, 1),
        "n": n,
        "years": PCTL_YEARS,
        "window_start": window[0].isoformat(),
        "window_end": series[-1][0].isoformat(),
        "min": round(ordered[0], places),
        "median": round(median, places),
        "max": round(ordered[-1], places),
    }


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


def derive(a: list, b: list, fn) -> list:
    """Combine two series on the dates they share, oldest first.

    Percentiles for the crack and the spreads have to be measured on the
    derived series itself, not on either input, so the whole history is
    rebuilt here rather than just the latest point.
    """
    bmap = dict(b)
    out = []
    for when, av in a:
        bv = bmap.get(when)
        if bv is None:
            continue
        got = fn(av, bv)
        if got is not None:
            out.append((when, got))
    if not out:
        raise RuntimeError("Two series share no dates, cannot derive from them.")
    return sorted(out)


# ---------------------------------------------------------------- assembly

def level(obs: list, meta: dict) -> dict:
    """Latest value, the week over week change, and its percentile."""
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
    out["percentile"] = percentile(obs, value, meta["units"])
    return out


def derived(series: list, label: str, units: str) -> dict:
    """Latest value of a computed series, with its own percentile."""
    when, value = series[-1]
    places = DECIMALS.get(units, 0)
    out = {
        "label": label,
        "units": units,
        "period": when.isoformat(),
        "value": round(value, places),
        "percentile": percentile(series, value, units),
    }
    if len(series) > 1:
        out["change"] = round(value - series[-2][1], places)
    return out


def compute(data: dict) -> dict:
    crude = data["crude_ex_spr"]
    distillate = data["distillate"]

    # Derived series over the whole history, so each percentile is measured
    # against its own past rather than against its inputs'.
    cover = derive(distillate, data["distillate_supplied"],
                   lambda stock, demand: stock / demand if demand > 0 else None)
    crack = derive(data["ulsd"], data["wti"],
                   lambda ulsd, wti: ulsd * GAL_PER_BBL - wti)
    bw = derive(data["brent"], data["wti"], lambda brent, wti: brent - wti)

    stocks = {
        "crude_ex_spr": {
            **level(crude, SERIES["crude_ex_spr"]),
            "five_year": five_year_band(crude),
        },
        "cushing": level(data["cushing"], SERIES["cushing"]),
        "distillate": {
            **level(distillate, SERIES["distillate"]),
            "days_of_cover": derived(cover, "Distillate days of cover", "days"),
        },
    }

    spreads = {
        "diesel_crack": derived(crack, "Diesel crack (ULSD - WTI)",
                                "dollars per barrel"),
        "brent_wti": derived(bw, "Brent - WTI", "dollars per barrel"),
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
        "percentile_note": f"rank is the percentile of the current value within "
                           f"the trailing {PCTL_YEARS} years of that same "
                           f"series, ties counted at half weight. Derived "
                           f"metrics are ranked against their own history.",
        "stocks": stocks,
        "production": {
            "crude": level(data["crude_production"], SERIES["crude_production"])
        },
        "prices": {
            "wti": level(data["wti"], SERIES["wti"]),
            "brent": level(data["brent"], SERIES["brent"]),
            "ulsd": level(data["ulsd"], SERIES["ulsd"]),
        },
        "spreads": spreads,
    }


# ------------------------------------------------------------------- entry

def verify(key: str) -> int:
    """Print what every series returns. Writes nothing."""
    bad = 0
    start = date.today() - timedelta(days=VERIFY_DAYS)
    for name, meta in SERIES.items():
        try:
            obs = fetch(meta, key, start)
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
    args = sys.argv[1:]
    key = api_key()
    if "--verify" in args:
        sys.exit(verify(key))

    full = "--backfill" in args
    data, fetched, had_cache = pull_all(key, full)

    try:
        result = compute(data)
    except RuntimeError as e:
        sys.exit(f"{e} Leaving the existing files alone.")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2) + "\n")
    save_cache(data)

    rows = sum(len(v) for v in data.values())
    pulled = sum(fetched.values())
    mode = "backfill" if full else ("incremental" if had_cache else "first run")
    crude = result["stocks"]["crude_ex_spr"]
    print(
        f"Wrote {OUT} (as of {result['as_of']}). "
        f"{mode}: fetched {pulled} rows, cache holds {rows} across "
        f"{len(data)} series.\n"
        f"Crude ex SPR {crude['value']:,.0f} Mbbl "
        f"({crude['five_year']['vs_avg_pct']:+.1f}% vs 5yr avg, "
        f"{crude['percentile']['rank']:.0f}th pct), "
        f"diesel crack ${result['spreads']['diesel_crack']['value']:.2f}/bbl "
        f"({result['spreads']['diesel_crack']['percentile']['rank']:.0f}th pct)."
    )


if __name__ == "__main__":
    main()
