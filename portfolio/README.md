# Portfolio model

## v1/ (the original, as published)

`v1/` is the original iShares multi-asset portfolio project, copied unchanged (code, README, cached prices and results). These are the numbers the site published before v2. Do not edit anything in `v1/`.

Replication (2026-11-23): `v1/portfolio.py` was run unchanged on its own `v1/data/prices.csv` with yfinance 1.8.0, pandas 3.1.1, numpy 2.5.4, scipy 1.18.1, matplotlib 3.11.2. Every published figure matched. The largest difference in any monthly return was 0.0000013 (optimizer tolerance). One rounding edge: Min Variance turnover prints 74.3% against the published 74.4% (the true value is about 74.35%).

## v2

The v2 study is pre registered in `docs/portfolio-spec.md`. The code is `scripts/portfolio_model.py` and its data is in `data/portfolio/`.
