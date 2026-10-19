"""
Builds a weekly markdown scaffold for a Market Note from the energy data.

Reads data/energy.json for this week's figures and data/eia_history.json for
the history behind them, compares the current week to the prior week in both
level and percentile, and writes notes/draft-YYYY-MM-DD.md.

The numbers are filled in. The thinking is not: Thesis, Caveats and What
would change my mind come through as empty headings with last week's text
quoted underneath, so there is something to react to rather than a blank page.

    python scripts/draft_note.py
    python scripts/draft_note.py --force      # rewrite today's draft

Runs from .github/workflows/update-energy.yml straight after update_eia.py.

A draft that already exists is never overwritten without --force. Once the
file has prose in it, it is the only copy of that prose, and a second run of
the workflow on the same day would otherwise destroy it.

Percentiles are point in time
-----------------------------
This week's rank comes from energy.json, so it matches the card exactly. Last
week's rank is recomputed by ranking last week's value in the five years
ending *last week*, which is what the card would have said at the time. The
window therefore rolls by one observation between the two, which is the
honest comparison: it is the difference between two published figures, not
the same figure measured twice.
"""

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import update_eia as ue

ROOT = Path(__file__).resolve().parent.parent
ENERGY = ROOT / "data" / "energy.json"
NOTES = ROOT / "notes"

FLAG_POINTS = 5.0        # a percentile move bigger than this gets called out
WEEK_DAYS = 7            # how far back "the prior week" is

SECTIONS = ["Thesis", "Caveats", "What would change my mind"]
WRITE_MARK = "<!-- write below this line; next week quotes it back -->"
REF_MARK = "<!-- last week, for reference; not carried forward -->"


# ------------------------------------------------------------- formatting

def fmt(value, units: str) -> str:
    if units in ("thousand barrels", "thousand barrels per day"):
        return f"{value:,.0f}"
    if units == "dollars per barrel":
        return f"${value:,.2f}"
    if units == "dollars per gallon":
        return f"${value:,.3f}"
    if units == "days":
        return f"{value:,.1f}"
    return f"{value:,.2f}"


def signed(value, units: str) -> str:
    body = fmt(abs(value), units)
    if units.startswith("dollars"):
        body = body.lstrip("$")
        return f"{'+' if value >= 0 else '-'}${body}"
    return f"{'+' if value >= 0 else '-'}{body}"


def short_units(units: str) -> str:
    return {
        "thousand barrels": "k bbl",
        "thousand barrels per day": "k bbl/d",
        "dollars per barrel": "$/bbl",
        "dollars per gallon": "$/gal",
        "days": "days",
    }.get(units, units)


# -------------------------------------------------------------- comparing

def prior_index(series: list, latest: date) -> int:
    """Index of the last observation at least a week before `latest`.

    Weekly series land on the previous release. Daily price series step back
    about five sessions rather than one, so "week over week" means the same
    span for every metric on the page.
    """
    cutoff = latest - timedelta(days=WEEK_DAYS)
    for i in range(len(series) - 1, -1, -1):
        if series[i][0] <= cutoff:
            return i
    return -1


def compare(label: str, node: dict, series: list) -> dict:
    units = node["units"]
    now_value = node["value"]
    now_rank = node["percentile"]["rank"]
    latest = series[-1][0]

    if latest.isoformat() != node["period"]:
        raise RuntimeError(
            f"{label}: history ends {latest} but energy.json says "
            f"{node['period']}. Rebuild the cache with --backfill."
        )

    idx = prior_index(series, latest)
    if idx < 0:
        raise RuntimeError(f"{label}: no observation a week before {latest}.")

    then_date, then_value = series[idx]
    # Rank last week's value in the five years ending last week.
    then_rank = ue.percentile(series[: idx + 1], then_value, units)["rank"]

    return {
        "label": label,
        "units": units,
        "period": latest,
        "prior_period": then_date,
        "value": now_value,
        "prior_value": then_value,
        "d_value": now_value - then_value,
        "rank": now_rank,
        "prior_rank": then_rank,
        "d_rank": now_rank - then_rank,
        "n": node["percentile"]["n"],
    }


def build(doc: dict, hist: dict) -> list:
    """Every metric on the card, derived ones rebuilt from their inputs."""
    cover = ue.derive(hist["distillate"], hist["distillate_supplied"],
                      lambda stock, demand: stock / demand if demand > 0 else None)
    crack = ue.derive(hist["ulsd"], hist["wti"],
                      lambda ulsd, wti: ulsd * ue.GAL_PER_BBL - wti)
    bw = ue.derive(hist["brent"], hist["wti"], lambda brent, wti: brent - wti)

    stocks, prices, spreads = doc["stocks"], doc["prices"], doc["spreads"]
    pairs = [
        ("Crude stocks ex-SPR", stocks["crude_ex_spr"], hist["crude_ex_spr"]),
        ("Cushing stocks", stocks["cushing"], hist["cushing"]),
        ("US field production", doc["production"]["crude"], hist["crude_production"]),
        ("Distillate stocks", stocks["distillate"], hist["distillate"]),
        ("Distillate days of cover", stocks["distillate"]["days_of_cover"], cover),
        ("WTI spot", prices["wti"], hist["wti"]),
        ("Brent spot", prices["brent"], hist["brent"]),
        ("NY Harbor ULSD", prices["ulsd"], hist["ulsd"]),
        ("Diesel crack (ULSD - WTI)", spreads["diesel_crack"], crack),
        ("Brent - WTI", spreads["brent_wti"], bw),
    ]
    return [compare(label, node, series) for label, node, series in pairs]


# --------------------------------------------------- last week's own words

def previous_draft(today_file: Path):
    drafts = sorted(p for p in NOTES.glob("draft-*.md") if p != today_file)
    return drafts[-1] if drafts else None


def extract(text: str, heading: str) -> str:
    """What the author actually wrote under a heading.

    The quoted reference block is stripped, so drafts do not accumulate a
    deeper nest of quotes every week.
    """
    body, capturing = [], False
    for line in text.splitlines():
        if line.startswith("## "):
            if capturing:
                break
            capturing = line[3:].strip().lower() == heading.lower()
            continue
        if capturing:
            body.append(line)
    out = "\n".join(body)
    out = out.split(REF_MARK)[0]
    return out.replace(WRITE_MARK, "").strip()


def quoted(text: str) -> str:
    if not text:
        return "> _(nothing written here last week)_"
    return "\n".join(("> " + ln) if ln.strip() else ">" for ln in text.splitlines())


# --------------------------------------------------------------- rendering

def render(doc: dict, rows: list, prior: Path, today: date) -> str:
    movers = [r for r in rows if abs(r["d_rank"]) > FLAG_POINTS]
    weekly = rows[0]
    out = []
    add = out.append

    add(f"# Energy note draft — {today.isoformat()}")
    add("")
    add(f"Data as of week ending **{doc['as_of']}**. Weekly series compare "
        f"{weekly['period']} against {weekly['prior_period']}; daily series step "
        f"back about a week from their own latest close.")
    add("")
    add("Generated by `scripts/draft_note.py`. Percentiles are all-weeks ranks "
        "over the trailing five years, each measured against the data available "
        "that week. Numbers are filled in; the thinking is not.")
    add("")

    add("## Movers")
    add("")
    if movers:
        add(f"Percentile moved more than {FLAG_POINTS:.0f} points:")
        add("")
        for r in sorted(movers, key=lambda x: -abs(x["d_rank"])):
            arrow = "up" if r["d_rank"] > 0 else "down"
            add(f"- **{r['label']}** — {fmt(r['prior_value'], r['units'])} → "
                f"{fmt(r['value'], r['units'])} {short_units(r['units'])} "
                f"({signed(r['d_value'], r['units'])}), percentile "
                f"{r['prior_rank']:.1f} → {r['rank']:.1f} "
                f"({r['d_rank']:+.1f}, {arrow})")
    else:
        add(f"No metric moved more than {FLAG_POINTS:.0f} percentile points "
            f"this week.")
    add("")

    add("## Numbers")
    add("")
    add("| Metric | This week | Last week | Δ | Pct | Last pct | Δ pct | |")
    add("|---|---:|---:|---:|---:|---:|---:|:--:|")
    for r in rows:
        flag = "**!**" if abs(r["d_rank"]) > FLAG_POINTS else ""
        add(f"| {r['label']} ({short_units(r['units'])}) "
            f"| {fmt(r['value'], r['units'])} "
            f"| {fmt(r['prior_value'], r['units'])} "
            f"| {signed(r['d_value'], r['units'])} "
            f"| {r['rank']:.1f} "
            f"| {r['prior_rank']:.1f} "
            f"| {r['d_rank']:+.1f} "
            f"| {flag} |")
    add("")
    add(f"Sample for each percentile: {rows[0]['n']} weekly observations, "
        f"{rows[-1]['n']} daily. Five year band for crude stocks is a "
        f"same-week comparison and will not agree with the all-weeks "
        f"percentile; that is expected.")
    add("")

    band = doc["stocks"]["crude_ex_spr"]["five_year"]
    add(f"Crude stocks against the same week of the prior five years: "
        f"{band['min']:,} low, {band['avg']:,} average, {band['max']:,} high — "
        f"currently {band['vs_avg_pct']:+.1f}% versus that average, "
        f"{'inside' if band['in_band'] else 'outside'} the range.")
    add("")

    old = prior.read_text(encoding="utf-8") if prior else ""
    label = f"Last week ({prior.stem.replace('draft-', '')})" if prior else ""

    for heading in SECTIONS:
        add(f"## {heading}")
        add("")
        add(WRITE_MARK)
        add("")
        add("")
        add(REF_MARK)
        if prior:
            add("")
            add(f"_{label}:_")
            add("")
            add(quoted(extract(old, heading)))
        else:
            add("")
            add("> _(no previous draft found)_")
        add("")

    return "\n".join(out).rstrip() + "\n"


def main():
    force = "--force" in sys.argv[1:]

    try:
        doc = json.loads(ENERGY.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        sys.exit(f"Cannot read {ENERGY}: {e}. Run update_eia.py first.")

    hist = ue.load_cache()
    if not hist:
        sys.exit(
            f"No usable history in {ue.CACHE}. Run "
            f"'python scripts/update_eia.py --backfill' first."
        )
    missing = [k for k in ue.SERIES if k not in hist]
    if missing:
        sys.exit(f"History cache is missing {missing}. Rebuild with --backfill.")

    today = datetime.now(timezone.utc).date()
    NOTES.mkdir(parents=True, exist_ok=True)
    target = NOTES / f"draft-{today.isoformat()}.md"

    if target.exists() and not force:
        print(f"{target} already exists, leaving it alone. "
              f"Pass --force to rewrite it.")
        return

    try:
        rows = build(doc, hist)
    except RuntimeError as e:
        sys.exit(f"{e}")

    prior = previous_draft(target)
    target.write_text(render(doc, rows, prior, today), encoding="utf-8")

    movers = [r for r in rows if abs(r["d_rank"]) > FLAG_POINTS]
    print(f"Wrote {target} ({len(rows)} metrics, {len(movers)} flagged"
          + (f": {', '.join(r['label'] for r in movers)}" if movers else "")
          + ").")
    if prior:
        print(f"Quoted last week's sections from {prior.name}.")
    else:
        print("No previous draft found; reference sections left empty.")


if __name__ == "__main__":
    main()
