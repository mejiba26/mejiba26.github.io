"""
Offline test for scripts/update_market.py, using synthetic FRED CSV text.
No network. Run: python scripts/dev/test_update_market.py
"""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import update_market as m  # noqa: E402


def csv_text(series_id, pairs, header="observation_date"):
    lines = [f"{header},{series_id}"] + [f"{d.isoformat()},{v}" for d, v in pairs]
    return "\n".join(lines) + "\n"


def daily(start, end, value):
    d, out = start, []
    while d <= end:
        out.append((d, value))
        d += timedelta(days=1)
    return out


TODAY = date(2026, 11, 12)
# 3.50 to 3.75 until Sept 15, then 3.75 to 4.00 from Sept 16 (a quarter point hike).
up = daily(date(2026, 8, 1), date(2026, 9, 15), 3.75) + daily(date(2026, 9, 16), date(2026, 11, 11), 4.00)
lo = daily(date(2026, 8, 1), date(2026, 9, 15), 3.50) + daily(date(2026, 9, 16), date(2026, 11, 11), 3.75)


def check(cond, msg):
    if not cond:
        sys.exit(f"FAIL: {msg}")
    print(f"ok   {msg}")


def raises(fn, msg):
    try:
        fn()
    except ValueError:
        print(f"ok   {msg}")
        return
    sys.exit(f"FAIL: {msg} (no ValueError)")


U = m.parse_series(csv_text("DFEDTARU", up), "DFEDTARU")
L = m.parse_series(csv_text("DFEDTARL", lo), "DFEDTARL")
r = m.compute(U, L, TODAY)
check(r["as_of"] == "2026-11-11", "as_of is the newest shared date")
check(r["fed"]["lower"] == 3.75 and r["fed"]["upper"] == 4.0, "current range read correctly")
lc = r["fed"]["last_change"]
check(lc["date"] == "2026-09-16" and lc["direction"] == "increase" and lc["bp"] == 25, "last change is the Sept 16 quarter point increase")
check(lc["prior_lower"] == 3.5 and lc["prior_upper"] == 3.75, "prior range recorded")

# A cut is reported as a cut.
cut_up = daily(date(2026, 8, 1), date(2026, 9, 15), 4.00) + daily(date(2026, 9, 16), date(2026, 11, 11), 3.75)
cut_lo = daily(date(2026, 8, 1), date(2026, 9, 15), 3.75) + daily(date(2026, 9, 16), date(2026, 11, 11), 3.50)
rc = m.compute(m.parse_series(csv_text("DFEDTARU", cut_up), "DFEDTARU"), m.parse_series(csv_text("DFEDTARL", cut_lo), "DFEDTARL"), TODAY)
check(rc["fed"]["last_change"]["direction"] == "cut", "a cut is labelled cut")

# No change inside the window leaves last_change null.
flat_u = m.parse_series(csv_text("DFEDTARU", daily(date(2026, 8, 1), date(2026, 11, 11), 3.75)), "DFEDTARU")
flat_l = m.parse_series(csv_text("DFEDTARL", daily(date(2026, 8, 1), date(2026, 11, 11), 3.50)), "DFEDTARL")
check(m.compute(flat_u, flat_l, TODAY)["fed"]["last_change"] is None, "no change in window gives null last_change")

# Missing observations and the older DATE header both parse.
mixed = "DATE,DFEDTARU\n2026-11-09,.\n2026-11-10,4.00\n2026-11-11,\n"
check(m.parse_series(mixed, "DFEDTARU") == {date(2026, 11, 10): 4.0}, "dots, blanks and DATE header handled")

# Failure cases must raise, never publish.
raises(lambda: m.compute(U, L, date(2026, 12, 20)), "stale data raises")
raises(lambda: m.parse_series("observation_date,WRONG\n2026-11-10,4\n", "DFEDTARU"), "wrong header raises")
raises(lambda: m.parse_series("observation_date,DFEDTARU\n", "DFEDTARU"), "empty file raises")
raises(lambda: m.parse_series("observation_date,DFEDTARU\nnot-a-date,4\n", "DFEDTARU"), "bad date raises")
raises(lambda: m.compute({date(2026, 11, 11): 4.0}, {date(2026, 11, 11): 4.0}, TODAY), "zero width range raises")
raises(lambda: m.compute({date(2026, 11, 11): 40.0}, {date(2026, 11, 11): 39.75}, TODAY), "absurd rate raises")
raises(lambda: m.compute({date(2026, 11, 11): 4.0}, {date(2026, 11, 10): 3.75}, TODAY), "no shared dates raises")
print("all passed")
