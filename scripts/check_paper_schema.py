"""
Fails loudly if data/paper/ lacks something the paper trading card on index.html needs.

The page hides the card quietly when anything is off (by design), so without this
check a renamed key would leave the card dead while every workflow run stays green.

KEEP IN SYNC with the paper trading script in index.html (search "Paper trading:"):
the keys and the ledger header checked here are the ones that script reads. If you
change one side, change the other.

    python scripts/check_paper_schema.py [DIR]     # default: data/paper

If the folder does not exist yet (before the first run on or after START_DATE) it
does nothing and exits 0. Standard library only.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TIERS = ["safe", "medium", "high"]
SERIES_KEYS = ["safe", "medium", "high", "ITA", "SPY", "EQW"]
POS_KEYS = ["shares", "entry", "stop"]
LEDGER_HEADER = "date,portfolio,ticker,side,price,shares,cost,reason"


def fail(msg):
    sys.exit(f"SCHEMA CHECK FAILED: {msg}")


def need(obj, key, where, kind=None):
    if not isinstance(obj, dict) or key not in obj:
        fail(f"missing key '{key}' in {where}")
    v = obj[key]
    if kind == "num" and (isinstance(v, bool) or not isinstance(v, (int, float))):
        fail(f"'{key}' in {where} is not a number: {v!r}")
    return v


def load(d, name):
    p = d / name
    if not p.exists():
        fail(f"{p} is missing")
    return p


def main():
    d = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "paper"
    if not d.exists():
        print(f"{d} does not exist yet. Nothing to check.")
        return

    try:
        eq = json.loads(load(d, "equity.json").read_text())
        state = json.loads(load(d, "state.json").read_text())
    except json.JSONDecodeError as e:
        fail(f"invalid JSON: {e}")

    as_of = need(eq, "as_of", "equity.json")
    series = need(eq, "series", "equity.json")
    if not isinstance(series, list) or not series:
        fail("'series' in equity.json is empty or not a list")
    for i, row in enumerate(series):
        need(row, "date", f"equity.json series[{i}]")
        for k in SERIES_KEYS:
            need(row, k, f"equity.json series[{i}] (date {row.get('date')})", "num")
    if series[-1]["date"] != as_of:
        fail(f"equity.json as_of {as_of} does not match the last series date {series[-1]['date']}")

    ports = need(state, "ports", "state.json")
    for t in TIERS:
        pos = need(need(ports, t, "state.json ports"), "pos", f"state.json ports.{t}")
        if not isinstance(pos, dict):
            fail(f"'pos' in state.json ports.{t} is not an object")
        for tkr, p in pos.items():
            for k in POS_KEYS:
                need(p, k, f"state.json ports.{t}.pos.{tkr}", "num")

    head = load(d, "ledger.csv").read_text().splitlines()[:1]
    if head != [LEDGER_HEADER]:
        fail(f"ledger.csv header is {head!r}, expected {LEDGER_HEADER!r}")

    print(f"Schema OK: {len(series)} series rows, as_of {as_of}.")


if __name__ == "__main__":
    main()
