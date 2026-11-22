# Portfolio review: how professionals analyse a portfolio, and what the tool does

Research notes behind `pipeline/pf/` (built 22 Nov 2026). Industry risk systems (MSCI RiskMetrics / BarraOne,
Bloomberg PORT, Morningstar X-Ray and fund factsheets) look at a portfolio from the same angles. Each one below
names the standard method and where the tool shows it.

## 1. What you own (page 2)
- Weights, sectors and size against the KSE-100 (`IndexWeightCur` from scstrade's live index view).
- Concentration: Herfindahl-Hirschman index (HHI), effective number of holdings (1/HHI), top 1/5/10.
- Active share (Cremers and Petajisto 2009): above 60% is active, below 20% is close to the index.
- Liquidity: days to sell = position / (20% of the median daily value traded over 60 sessions). Buckets follow
  US SEC Rule 22e-4: illiquid holdings at most 15%. Also the stake as a share of free float.
- Calendar-year returns of today's holdings at today's weights.

## 2. How it behaved (page 3)
- Returns: total return (dividends included) and price-only return.
  - Prices are adjusted with PSX's reference prices.
  - Cash dividends are separated from bonus shares and splits using the announced payouts: ex-date and
    dividend % of face value.
  - Price-only return is the like-for-like comparison with the KSE-100, which is a price index.
- Risk-free rate: the SBP policy rate in force each day (decision table in `pf/data.py`, plus the latest
  rate from the news pipeline's state).
- Measures, each with its standard definition:
  - Sharpe (1994)
  - Sortino with downside deviation (Sortino-Price 1994)
  - Calmar
  - Ulcer index and Martin ratio
  - Omega (Keating-Shadwick 2002)
  - Jensen's alpha and Treynor
  - Tracking error and information ratio
  - Up/down capture and batting average, on monthly returns as Morningstar computes them
  - Skewness and excess kurtosis
  - Best and worst month

## 3. Value at Risk and where risk comes from (page 4)
- VaR at 95% and 99%, 1-day and 10-day (square root of time), four methods:
  - historical (last 2 years)
  - normal
  - Cornish-Fisher (Favre-Galeano 2002)
  - EWMA with lambda 0.94 (RiskMetrics 1996)
- Expected shortfall: historical and normal. Basel III prefers it to VaR.
- VaR backtest: Kupiec proportion-of-failures test (1995) and the Basel traffic light (0-4 breaches in 250
  days is green).
- Euler risk contributions: each holding's share of portfolio volatility; they add up exactly.
- Diversification ratio (Choueifaty 2008) and effective number of bets from principal components (Meucci 2009).
- Systematic vs stock-specific share, from R squared.
- Correlation matrix, with pairs above 0.8 flagged.

## 4. Stress tests on history (page 5)
- Every KSE-100 fall of 15% or more since 2008 found in the data, plus named crises that sit inside longer
  falls: COVID-19 in 2020 and the 2021-23 slide.
- Method: today's holdings bought at the top and held to the bottom.
  - A stock that was not listed yet is represented by its Blume-adjusted beta x the index; coverage is shown.
- The index's worst days.
- SBP rate cycles (hikes or cuts totalling 3 points or more).
- Named windows checked against the data: 2008 crisis (-69%), 2017-2020 bear market (-49%), COVID-19 (-37%),
  2021-23 slide (-21%), early 2026 sell-off (-23%).
- Data cleaning: a bad KSE-100 print on Sunday 7 Jan 2024 (78,824) is removed by `clean_index`.

## 5. What could happen (page 6)
- Correlated macro scenarios, the Barra style of stress test.
  - Monthly regression of each stock on the KSE-100, USD/PKR and Brent crude (60 months, Yahoo Finance).
  - A shock to some factors moves the others by their conditional expectation:
    f_U = C_US C_SS^-1 s.
  - The 90% range adds the stocks' own residual risk.
- Scenarios:
  - correction -15%, bear market -30%, crash -50%
  - rupee -20%
  - oil +50% and -40%
  - balance-of-payments crisis
  - rally +25%
- Monte Carlo of the next 12 months, 10,000 paths, stationary bootstrap of real daily returns (Politis-Romano
  1994). Two versions:
  - Normal times: re-centred on the CAPM expected return, which is the 10-year PIB yield plus beta x 5% equity
    premium (the same premium as the fair value model).
  - Crisis times: drawn only from bear-market days.

## 6. Fundamentals and income (page 7)
- P/E on the checked trailing EPS (Lab `checks.trailing`).
- P/B on the updated book value.
- Yield, ROE and debt.
- Latest result vs a year earlier.
- Both fair values (returns stay / returns fade).
- Portfolio P/E and P/B are harmonic weighted averages, as index providers compute them.
- Dividend income after 15% tax for filers, with the capital gains tax estimate.
- Earnings yield against the 10-year PIB.
- Board meetings and ex-dates in the next 30 days.
- Research Lab levels where available.

## 7. Rule book (page 8)
- SECP-regulated Pakistani equity funds, from fund offering documents (HBL, Al Habib, MCB):
  - one company max 10% of net assets or its index weight, up to 15%
  - one sector max 30% or its index weight, up to 35%
- EU UCITS 5/10/40.
- At least 15 stocks (Evans-Archer 1968, Statman 1987).
- Effective number of holdings of at least 10.
- No stock above 20% of total risk.
- Hard-to-sell holdings at most 15%.
- Volatility up to 1.25x the market.
- VaR backtest in the green zone.
- Crisis loss up to 1.2x the market's.
- No pair correlated above 0.8.
- Weak-quality holdings (loss-making, negative equity, defaulters) at most 10%.
- Shariah share, from KMI All Share membership. The full screening needs data PSX does not publish:
  - SECP cut the debt limit from 37% to 33% in February 2026.
  - Non-compliant income must stay under 5%.

These are yardsticks for a private investor, not obligations.

## 8. Other weightings of the same stocks (page 8)
Four standard risk-based portfolios, none of which needs a return forecast:
- equal weight
- minimum variance
- equal risk contribution (Maillard-Roncalli-Teiletche 2010, Spinu 2013)
- maximum diversification

They use Ledoit-Wolf (2003) shrinkage to a constant-correlation covariance. Long only, capped at 15% per stock
(looser when there are few stocks). Shown with 5-year history and the warning about hindsight.

## Tax facts used (PwC tax summaries, reviewed Aug 2026; NCCPL notice for FY26)
- Dividends: 15% final tax for persons on the Active Taxpayers List.
  - 30% for others.
  - 25% from companies paying no tax.
  - 7.5% from IPPs with pass-through dividends.
- Capital gains on listed shares:
  - bought from 1 Jul 2024: 15% for filers
  - bought 1 Jul 2022 - 30 Jun 2024: by holding period, 15% down to 0% after six years
  - bought before 1 Jul 2022: 12.5%

## Privacy
- The repository and its Actions logs are public. Holdings go from the bot chat to the workflow encrypted:
  openssl AES-256-CBC with PBKDF2, key = the bot token.
- The last portfolio is kept only inside the encrypted Lab data.
- Every holding's symbol is masked in the log while a review runs.
