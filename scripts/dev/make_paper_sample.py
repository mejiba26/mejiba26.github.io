"""SAMPLE DATA, NOT A TRACK RECORD.

Builds fake paper trading files (same schema as data/paper/) plus a copy of the
page with a fixed clock, so the paper trading card can be tested in a browser.

    python scripts/dev/make_paper_sample.py [--out DIR]
    cd DIR && python -m http.server 8765
    open http://localhost:8765/index.html?s=mid&now=2026-12-19

Scenarios (s=): mid, zero, big, bottom, stale, bad, missing. The `now` for each is
printed. Output defaults to a folder in the system temp dir, outside the repo.
It refuses to write anywhere inside data/paper/.
"""
import argparse, csv, datetime as dt, json, random, sys, tempfile
from pathlib import Path

MARK = "SAMPLE DATA, NOT A TRACK RECORD"
REPO = Path(__file__).resolve().parents[2]
FORBIDDEN = (REPO / "data" / "paper").resolve()
U = ["RTX", "LMT", "NOC", "GD", "ITA", "XLE", "USO", "XOM"]
TIERS = ["safe", "medium", "high"]
KEYS = TIERS + ["ITA", "SPY", "EQW"]


def trading_days(n, start=dt.date(2026, 11, 16)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def write_set(root, name, series, rows, pos):
    p = root / "pdata" / name
    p.mkdir(parents=True, exist_ok=True)
    rows = sorted(rows, key=lambda r: r[0])
    with open(p / "ledger.csv", "w", newline="") as f:  # header must stay exact: the page checks it
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["date", "portfolio", "ticker", "side", "price", "shares", "cost", "reason"])
        w.writerows(rows)
    eq = {"_sample": MARK, "as_of": series[-1]["date"], "generated_utc": "sample", "start_date": series[0]["date"],
          "portfolios": TIERS, "benchmarks": ["ITA", "SPY", "EQW"], "series": series}
    state = {"_sample": MARK, "last_date": series[-1]["date"], "ports": {t: {"cash": 50000.0, "pos": pos[t]} for t in TIERS}}
    (p / "equity.json").write_text(json.dumps(eq))
    (p / "state.json").write_text(json.dumps(state))
    return p


def random_set(root, name, ntrades, n=25):
    ds = trading_days(n)
    vol = {"safe": .0015, "medium": .003, "high": .006, "ITA": .008, "SPY": .006, "EQW": .007}
    v, series = {k: 100000.0 for k in KEYS}, []
    for i, d in enumerate(ds):
        for k in v:
            v[k] *= 1 + random.gauss(.0003, vol[k]) - (0.002 if k == "high" and 8 < i < 14 else 0)
        series.append({"date": d.isoformat(), **{k: round(x, 2) for k, x in v.items()}})
    rows, pos = [], {t: {} for t in TIERS}
    for i in range(ntrades):
        e = ds[(i * 2) % (n - 6)]
        x = ds[(i * 2) % (n - 6) + 3 + i % 3] if i < ntrades - 2 or ntrades < 3 else None
        t, px = U[i % len(U)], 100 + i * 7
        out = px * (1 + random.choice([-.05, -.02, .03, .05, .04]))
        for tier, sh in (("safe", 30), ("medium", 60), ("high", 120)):
            if tier == "safe" and i % 3 == 0:
                continue
            rows.append([e.isoformat(), tier, t, "BUY", round(px, 4), sh, round(sh * px * .001, 2), "entry"])
            if x:
                rows.append([x.isoformat(), tier, t, "SELL", round(out, 4), sh, round(sh * out * .001, 2),
                             random.choice(["stop", "target", "trend break", "time stop"])])
            else:
                pos[tier][t] = {"shares": sh, "entry": round(px, 4), "stop": round(px * .95, 4), "target": round(px * 1.07, 4), "bars": 3}
    if ntrades:
        rows.append([ds[5].isoformat(), "high", "RTX", "DIV", 0.63, 120, 0.0, "dividend"])
    return write_set(root, name, series, rows, pos), ds[-1]


def bottom_set(root, name="bottom", n=30):
    """All six lines rise to about +3%, then fall and end within 2.5% of each other near the chart bottom."""
    ds = trading_days(n)
    ends = {"safe": -11.0, "medium": -12.3, "high": -10.5, "ITA": -9.8, "SPY": -10.2, "EQW": -11.5}
    peak_i, series = n // 3, []
    for i, d in enumerate(ds):
        row = {"date": d.isoformat()}
        for j, k in enumerate(KEYS):
            tgt = 3.0 + j * .3
            r = tgt * (i + 1) / (peak_i + 1) if i <= peak_i else tgt + (ends[k] - tgt) * (i - peak_i) / (n - 1 - peak_i)
            row[k] = round(100000 * (1 + r / 100 + random.gauss(0, .0008)), 2)
        series.append(row)
    for k in KEYS:  # pin the final point exactly
        series[-1][k] = round(100000 * (1 + ends[k] / 100), 2)
    return write_set(root, name, series, [], {t: {} for t in TIERS}), ds[-1]


def page_copy(root):
    """Copy of index.html whose clock and data folder come from ?now= and ?s=. Fails if the page changed."""
    s = (REPO / "index.html").read_text(encoding="utf-8")
    q = "new URLSearchParams(location.search)"
    swaps = [("const now = new Date();", f"const now = new Date({q}.get('now')+'T12:00:00');"),
             ("const BASE = 'data/paper/';", f"const BASE = 'pdata/'+{q}.get('s')+'/';")]
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
    ap.add_argument("--out", default=str(Path(tempfile.gettempdir()) / "paper-sample-NOT-A-TRACK-RECORD"))
    root = Path(ap.parse_args().out).resolve()
    if root == FORBIDDEN or FORBIDDEN in root.parents:
        sys.exit(f"Refusing to write inside {FORBIDDEN}: that folder holds the real, append-only record.")
    random.seed(4)
    root.mkdir(parents=True, exist_ok=True)
    (root / "SAMPLE_DATA_NOT_A_TRACK_RECORD.txt").write_text(MARK + "\n")
    nows = {}
    for name, ntr, n in (("mid", 8, 25), ("zero", 0, 5), ("big", 34, 60)):
        _, last = random_set(root, name, ntr, n)
        nows[name] = last + dt.timedelta(days=1)
    _, last = bottom_set(root); nows["bottom"] = last + dt.timedelta(days=1)
    p, last = random_set(root, "stale", 8); nows["stale"] = last + dt.timedelta(days=12)
    p, last = random_set(root, "bad", 8); nows["bad"] = last + dt.timedelta(days=1)
    (p / "equity.json").write_text('{"as_of": "2026-12-1')            # malformed on purpose
    p, last = random_set(root, "missing", 8); nows["missing"] = last + dt.timedelta(days=1)
    (p / "state.json").unlink()                                         # missing on purpose
    page_copy(root)
    print(f"{MARK}\nwritten to {root}\nserve: cd \"{root}\" && python -m http.server 8765")
    for k, v in nows.items():
        print(f"  index.html?s={k}&now={v}")


if __name__ == "__main__":
    main()
