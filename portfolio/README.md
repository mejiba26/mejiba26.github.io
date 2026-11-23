# Portfolio model

## v1/ (the original, as published)

`v1/` is the original iShares multi-asset portfolio project, copied unchanged (code, README, cached prices and results). These are the numbers the site published before v2. Do not edit anything in `v1/`.

Replication (2026-11-23): `v1/portfolio.py` was run unchanged on its own `v1/data/prices.csv` with yfinance 1.8.0, pandas 3.1.1, numpy 2.5.4, scipy 1.18.1, matplotlib 3.11.2. Of the 45 published figures (30 summary, 15 stress), 40 were identical to 6 decimals and the other 5 (optimizer outputs) were within 0.000007. One of those flips at the printed rounding: Min Variance turnover is 74.4% published and 74.3% rerun (the true value is about 74.35%). The largest difference in any monthly return was 0.0000013.

Label note: v1's stress period called "2008 Financial Crisis" covers Jun 2008 to Feb 2009, not calendar 2008. The site labels it by those dates. v1's own files are left unchanged.

## v2

The v2 study is pre registered in `docs/portfolio-spec.md`. The code is `scripts/portfolio_model.py` and its data is in `data/portfolio/`.
