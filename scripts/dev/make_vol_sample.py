"""SAMPLE DATA, NOT A TRACK RECORD.

Builds fake volatility scorecard files (same schema as data/vol/) plus a copy of the
page with a fixed clock, so the scorecard can be tested in a browser. The files are
produced by the real scripts/update_vol.py, fed synthetic prices and a fake clock,
so the page is tested against exactly what the script writes.

    python scripts/dev/make_vol_sample.py [--out DIR] [--quick]
    cd DIR && python -m http.server 8766
    open http://localhost:8766/index.html?vs=mid&now=2026-12-30

Scenarios (vs=): early, mid, late, full, stale, prestart, bad, missing. The `now` for
each is printed. --quick skips `full` (about 260 simulated days). Output defaults to a
folder in the system temp dir, outside the repo. It refuses to write anywhere inside
data/vol/.
"""
import argparse
import datetime as dt
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_update_vol as T  # noqa: E402  (the fake Yahoo and fake clock live there)

MARK = "SAMPLE DATA, NOT A TRACK RECORD"
REPO = Path(__file__).resolve().parents[2]
FORBIDDEN = (REPO / "data" / "vol").resolve()
U = T.U


def simulate(root, name, days, seed, skip=()):
    """Run the real script once per evening. Days in `skip` get no evening run, so the next one is late."""
    work = root / "_work" / name
    sim = T.Sim(work, seed=seed, last=days[-1] + dt.timedelta(days=10))
    for i, d in enumerate(days):
        if d in skip:
            continue
        if i and days[i - 1] in skip:
            sim.run(U.open_utc(d) + dt.timedelta(minutes=20))   # the missed run happens after the open
        sim.run(T.evening(d))
    T.restore()
    out = root / "vdata" / name
    out.mkdir(parents=True, exist_ok=True)
    for f in U.FILES:   # the CSV headers must stay exact, so the label sits in the JSON files and beside them
        shutil.copy(work / "vol" / f, out / f)
    for f in ("scores.json", "state.json"):
        data = json.loads((out / f).read_text())
        (out / f).write_text(json.dumps({"_sample": MARK, **data}, indent=1) + "\n")
    (out / "SAMPLE_DATA_NOT_A_TRACK_RECORD.txt").write_text(MARK + "\n")
    return out, days[-1]


def page_copy(root):
    """Copy of index.html whose clock and data folder come from ?now= and ?vs=. Fails if the page changed."""
    s = (REPO / "index.html").read_text(encoding="utf-8")
    q = "new URLSearchParams(location.search)"
    swaps = [("const vnow = new Date();", f"const vnow = new Date({q}.get('now')+'T12:00:00');"),
             ("const VBASE = 'data/vol/';", f"const VBASE = 'vdata/'+{q}.get('vs')+'/';")]
    for a, b in swaps:
        if s.count(a) != 1:
            sys.exit(f"index.html no longer contains exactly one: {a}")
        s = s.replace(a, b)
    banner = f'<div style="background:#a33;color:#fff;font:700 13px monospace;padding:6px;text-align:center">{MARK}</div>'
    s = s.replace("<body>", "<body>" + banner, 1)
    (root / "index.html").write_text(s, encoding="utf-8")
    (root / "style.css").write_text((REPO / "style.css").read_text(encoding="utf-8"), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=MARK)
    ap.add_argument("--out", default=str(Path(tempfile.gettempdir()) / "vol-sample-NOT-A-TRACK-RECORD"))
    ap.add_argument("--quick", action="store_true", help="skip the 260 day scenario")
    a = ap.parse_args()
    root = Path(a.out).resolve()
    if root == FORBIDDEN or FORBIDDEN in root.parents:
        sys.exit(f"Refusing to write inside {FORBIDDEN}: that folder holds the real, append-only record.")
    root.mkdir(parents=True, exist_ok=True)
    (root / "SAMPLE_DATA_NOT_A_TRACK_RECORD.txt").write_text(MARK + "\n")
    days = T.trading_days(T.FRIDAY, T.FRIDAY + dt.timedelta(days=400))
    nows = {}
    _, last = simulate(root, "early", days[:1], 21)                    # the first forecast, nothing graded yet
    nows["early"] = T.START
    nows["prestart"] = T.START - dt.timedelta(days=2)                  # same data seen before START_DATE: hidden
    shutil.copytree(root / "vdata" / "early", root / "vdata" / "prestart", dirs_exist_ok=True)
    _, last = simulate(root, "mid", days[:27], 22); nows["mid"] = last + dt.timedelta(days=1)
    _, last = simulate(root, "late", days[:14], 23, skip={days[5], days[12]}); nows["late"] = last + dt.timedelta(days=1)
    if not a.quick:
        _, last = simulate(root, "full", days[:262], 24); nows["full"] = last + dt.timedelta(days=1)
    p, last = simulate(root, "stale", days[:27], 22); nows["stale"] = last + dt.timedelta(days=12)
    p, last = simulate(root, "bad", days[:6], 25); nows["bad"] = last + dt.timedelta(days=1)
    (p / "scores.json").write_text('{"as_of": "2026-12-0')             # malformed on purpose
    p, last = simulate(root, "missing", days[:6], 25); nows["missing"] = last + dt.timedelta(days=1)
    (p / "scores.json").unlink()                                        # missing on purpose
    shutil.rmtree(root / "_work")
    page_copy(root)
    print(f"{MARK}\nwritten to {root}\nserve: cd \"{root}\" && python -m http.server 8766")
    for k, v in nows.items():
        print(f"  index.html?vs={k}&now={v}")


if __name__ == "__main__":
    main()
