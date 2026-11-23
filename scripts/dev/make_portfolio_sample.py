"""SAMPLE DATA, NOT RESULTS. Test helper for the #portfolio card; never part of the site or a workflow.

Builds one folder per scenario, each with a copy of index.html, style.css, the v1 chart images and a
data/portfolio/results.json for that scenario, so the card can be checked in a browser:

    python scripts/dev/make_portfolio_sample.py [--out DIR]
    cd DIR && python -m http.server 8766
    open http://localhost:8766/<scenario>/index.html#portfolio

Scenarios: ok (the committed results, marked as sample), better (one strategy made to beat 60/40
reliably, to exercise that wording), missing (no results.json), malformed (truncated JSON),
badvalue (a Sharpe ratio that is text). Only ok and better should show v2; the other three must show
the static v1 text with every v2 part hidden. Output defaults to the system temp folder. It refuses
to write anywhere inside data/portfolio/.
"""
import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

MARK = "SAMPLE DATA, NOT RESULTS"
REPO = Path(__file__).resolve().parents[2]
FORBIDDEN = (REPO / "data" / "portfolio").resolve()


def main():
    ap = argparse.ArgumentParser(description=MARK)
    ap.add_argument("--out", default=str(Path(tempfile.gettempdir()) / "portfolio-sample"))
    out = Path(ap.parse_args().out).resolve()
    if out == FORBIDDEN or FORBIDDEN in out.parents:
        sys.exit(f"Refusing to write inside {FORBIDDEN}")

    real = json.loads((FORBIDDEN / "results.json").read_text(encoding="utf-8"))
    ok = dict(real, _sample=MARK)
    better = json.loads(json.dumps(ok))
    s = next(x for x in better["strategies"] if x["name"] == "Trend 10M")
    s["vs_6040"]["ci95"] = [0.05, 0.4]
    s["vs_6040"]["sharpe_diff"] = 0.2
    bad = json.loads(json.dumps(ok))
    bad["strategies"][0]["sharpe"] = "0.71"
    text = json.dumps(ok)
    scenarios = {"ok": json.dumps(ok), "better": json.dumps(better), "missing": None,
                 "malformed": text[: len(text) // 2], "badvalue": json.dumps(bad)}

    for name, content in scenarios.items():
        d = out / name
        (d / "data" / "portfolio").mkdir(parents=True, exist_ok=True)
        for f in ("index.html", "style.css", "growth.png", "drawdowns.png"):
            shutil.copy(REPO / f, d / f)
        target = d / "data" / "portfolio" / "results.json"
        if content is None:
            target.unlink(missing_ok=True)
        else:
            target.write_text(content, encoding="utf-8")
        (d / "SAMPLE_DATA.txt").write_text(MARK + "\n", encoding="utf-8")
    print(f"{MARK}\nWrote {', '.join(scenarios)} to {out}\n  cd {out} && python -m http.server 8766")


if __name__ == "__main__":
    main()
