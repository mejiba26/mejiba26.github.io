# mejiba26.github.io
# Brian Mejia-Lopez | Portfolio

Professional portfolio site built for the Wake Forest MSBA program (Hybrid Track).

**Live site:** https://mejiba26.github.io

## About

MSBA candidate at Wake Forest (May 2027), Finance and Economics background, targeting markets analyst and equity research roles.

I've traded equities and MNQ/MES index futures independently since March 2020, with defined risk on every trade, which shaped how I think about companies: a business has an intrinsic value and a market price, and the gap between them is where the interesting questions live. My RTX valuation was the first time I built that view formally, and revisiting it in 2026 taught me as much as building it. I audited my original model, caught two errors, and rebuilt it. That's the kind of work I want to do full time: models built to be questioned.

## Contents

- **Intro:** a written introduction (the 60 second video is kept in the page, switched off)
- **The Bridge:** career narrative connecting past experience to target roles
- **Resume:** downloadable PDF
- **Projects**
  - [RTX Corporation Equity Valuation](https://mejiba26.github.io/#rtx): fair value of $125 to $133 per share (DCF about $128 at a 6.15% WACC, adjusted P/E about $125, EV/EBITDA about $133) against $122 on October 30, 2024, the analysis date, not a current price. A 2026 audit caught two errors and cut the DCF estimate from $175 to $128 ([memo](memos/rtx.html), [original paper](rtx-valuation.pdf))
  - [ECG Patient Matching Model](https://mejiba26.github.io/#ecg): Wake Forest School of Medicine Build-A-Thon (Sept to Oct 2026). Our team of three placed second; I led the modeling. Siamese CNN and neural network ensemble in TensorFlow/Keras, trained on 37,000+ ECGs. AUROC rose from 0.948 to 0.984 on the team's held out test set (not the official holdout). Code is in the [team repository](https://github.com/npotisit/Build-a-thon_MBNA) on a teammate's account
  - [iShares Multi-Asset Portfolio Model](https://mejiba26.github.io/#portfolio): does sophisticated portfolio optimization beat a plain 60/40 mix? Nine iShares ETFs (IVV, IJR, EFA, EEM, AGG, TLT, TIP, LQD, IAU), 36 month lookback, 10 bps costs. v1 (five strategies, Mar 2008 to Aug 2026, as published, in `portfolio/v1/`): 60/40 did best after costs (8.4% a year, 0.72 Sharpe), though the gaps were not statistically reliable, and from Jun 2008 to Feb 2009 Min Variance lost 1.2% vs. 29.6% for 60/40. The current version is the pre registered v2 (13 strategies with bootstrap confidence intervals, Mar 2008 to Oct 2026): none of the 12 others reliably beat 60/40
  - [Independent Equity and Futures Trading and Python Market Tools](https://mejiba26.github.io/#futures): equities and MNQ/MES index futures with defined stops and 2 to 5% of the account at risk per trade, plus Python screening and backtesting tools (a moving average crossover lost to buy and hold on AAPL, 31.4% vs. 56.9%)
  - Coursework: housing price regression model (545 home sales, R² = 0.680; code and data not published here)
- **Live Desk:** Fed policy, sector rotation, a monthly spotlight and the energy market, each written by a scheduled script with its own "as of" date, plus two pre registered simulations: paper trading (started Nov 16, 2026) and a volatility forecast scorecard (started Nov 23, 2026)
- **Methods:** every script behind the Live Desk and portfolio v2 numbers, with a clone and run block

## Built with

HTML and CSS, no framework. Hosted on GitHub Pages.

Sector data comes from `scripts/update_sectors.py` (Python, yfinance), which a GitHub Actions workflow runs every weekday after the close. It writes `data/sectors.json`, and the page reads that file when it loads.

Energy data comes from `scripts/update_eia.py` (Python, standard library only), which pulls crude and distillate stocks, crude production and WTI/Brent/ULSD spot prices from the EIA Open Data API. A workflow runs it Wednesdays after the Weekly Petroleum Status Report and writes `data/energy.json`. It needs an `EIA_API_KEY` ([free](https://www.eia.gov/opendata/register.php)), set as a repository secret for Actions and as an environment variable to run it locally. `python scripts/update_eia.py --verify` checks every series still returns data without rewriting the file.

The paper trading simulation follows a rule set locked before its start date in `docs/paper-trading-spec.md`. `scripts/paper_trade.py` is the simulator, a workflow (`update-paper.yml`) runs it every weekday after the close, and it appends to `data/paper/`. It is a simulation: no real money and not investment advice.

The volatility forecast scorecard publishes next day range forecasts for SPY, QQQ, ITA and XLE from four models every weekday after the close, then grades them (`scripts/update_vol.py`, `update-vol.yml`, `data/vol/`). Its models, scores and test were fixed before the first forecast in `docs/vol-scorecard-spec.md`. It is a forecast evaluation, not trading advice.

The portfolio model v2 is pre registered in `docs/portfolio-spec.md`. `scripts/portfolio_model.py` writes `data/portfolio/`, and a manual workflow (`run-portfolio.yml`) reruns it and must reproduce the committed numbers within a stated tolerance; its first run is the canonical source. The optimizer weights are checked against skfolio in `docs/portfolio-verification.md`. The stress window labelled "2008 crisis" in v1 is Jun 2008 to Feb 2009.

`scripts/dev/` holds test helpers for the paper trading card, the volatility scorecard and the portfolio model (sample data and offline tests). Nothing there runs in a workflow or on the site.

## Contact

- Email: Brian_m25@yahoo.com
- LinkedIn: [brian-mejia-lopez](https://www.linkedin.com/in/brian-mejia-lopez)
- GitHub: [mejiba26](https://github.com/mejiba26)
