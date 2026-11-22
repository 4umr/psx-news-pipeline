# Investment planning and portfolio recommendations: research and design

Research notes behind `pipeline/pf/plan.py`, `profile.py`, `cma.py`, `select.py` and `advice.py` (22 Nov 2026).
Companion to `docs/portfolio-review.md`, which covers the analysis of an existing portfolio.

## How the industry builds a client portfolio

1. **Know the client.** CFA Institute's Investment Policy Statement (IPS) covers:
   - objectives: return need, and risk as both ABILITY (capacity) and WILLINGNESS (tolerance)
   - constraints: time horizon, liquidity, tax, legal/regulatory, unique circumstances

   When ability and willingness disagree, the more conservative one prevails. Wealthfront scores both and weights
   the more risk-averse one more heavily, because people overstate their tolerance. In Pakistan, SECP requires
   asset management companies to assess suitability and to put every fund in a risk category (Circular 06 of 2022;
   fund reports show "Risk profile: Low / Medium / High, principal at low / medium / high risk").
2. **Strategic asset allocation first.** The mix of asset classes explains most of a portfolio's ups and downs
   (Brinson, Hood and Beebower 1986). Robo-advisers set equity by risk level and goal horizon:
   - Betterment's glide paths, by goal and years left:
     - general investing: 90% stocks at 20+ years down to 56% at the horizon
     - major purchase: 90% down to 0%
     - retirement income: 56% down to 30%
   - Wealthfront: mean-variance with Black-Litterman returns, risk score 0.5-10, asset caps of 35-45%.
3. **Capital market assumptions.** These are forward-looking building blocks, not past averages:
   - cash = today's yield
   - equities = long bond yield + equity risk premium
   - gold = inflation + the currency's expected fall
4. **Security selection** inside each asset class, then implementation:
   - lot sizes (PSX moved to one-share lots in 2024)
   - costs and taxes
   - liquidity
5. **Rebalancing and monitoring.**
   - Vanguard's research: checking yearly or half-yearly and rebalancing past a 5-point drift is a good balance of
     risk control and cost.
   - The "5/25" band: an asset class off by 5 points absolute or 25% relative.
   - Wealthfront rebalances on thresholds and uses new money first.
6. **Measure:** benchmark-relative performance and attribution (Brinson-Fachler 1985: allocation vs selection).

## Pakistani asset classes, 2008-2026 (monthly, in rupees; our data)

| Asset class | Return a year | Volatility | Worst fall | Worst 12 months | Avg in the KSE-100's worst 15% of months |
|---|---|---|---|---|---|
| KSE-100 (price index) | 14.3% | 22.8% | -64% | -62% | -9.1% |
| Gold in PKR (Comex x USD/PKR) | 16.8% | 20.4% | -34% | -21% | +3.2% |
| US dollar in PKR | 8.2% | 9.6% | -11% | -8% | +1.6% |
| T-bills (policy rate proxy) | 11.2% | 1.1% | 0% | +6% | +0.9% |

Correlation of gold with the KSE-100 is -0.12. Gold is the diversifier that pays off in crashes. Plain cash
(T-bills or money market funds) earns about 11% with no falls.

Ways to hold each:
- T-bills / money market: funds, or HBL Total Treasury ETF (HBLTETF) on PSX.
- Equity ETFs on PSX: MZNPETF (Shariah), NBPGETF, UBLPETF and others.
- Gold: physical or PMEX.

## Which stock-picking styles worked on PSX (tested 2010-2026)

Method:
- universe: monthly top 120 stocks by 60-day median value traded, price >= Rs 5, 1 year of history
- equal weight, dividends included, 201 months
- point-in-time data only: a result counts from the day after it is announced

Script idea in scratchpad `factor_test*.py`.

| Style (best fifth) | vs universe a year | t | Years better | 2010-17 / 2018-26 | Volatility (univ. 26%) | Worst fall (univ. -57%) |
|---|---|---|---|---|---|---|
| Earnings growth (last result vs a year ago) | +8.8% | 3.67 | 14/16 | +8.3 / +8.5 | 26% | -40% |
| Momentum (12 months, skip the last) | +4.7% | 1.70 | 10/17 | +5.2 / +4.0 | 26% | -47% |
| Low volatility | -0.5% | -0.2 | 9/17 | -4.8 / +3.4 | 18.5% | -34% |
| Low beta | +1.0% | 0.4 | 9/17 | +2.6 / -0.4 | 19.7% | -39% |
| Dividend yield | +0.4% | 0.2 | 10/17 | -2.7 / +3.2 | 21% | -36% |
| Earnings yield (cheap P/E) | +1.2% | 0.3 | 9/17 | 0.0 / +2.2 | 27% | -49% |
| Short-term reversal | -2.1% | -0.8 | 7/17 | | | |

What the tests show:
- **For return:** earnings growth and momentum. This matches post-earnings-announcement drift (Bernard-Thomas 1989)
  and momentum (Jegadeesh-Titman 1993), and Pakistani studies that find momentum and profitability matter.
- **For risk:** low volatility, low beta and dividend yield. They earned about the market but fell much less, the
  low-volatility anomaly (Ang et al. 2006, Frazzini-Pedersen 2014).
- **Cheap P/E:** no edge in PSX, the same finding as our earlier regime test.

20-stock portfolios rebuilt every 3 months (cost assumed 0.4% a side):

| Score | Return a year | vs universe | t | Years better | Halves | Volatility | Worst fall | Sharpe | Cost a year |
|---|---|---|---|---|---|---|---|---|---|
| Growth + momentum + low volatility ("Core growth") | 32.1% | +5.7% | 1.81 | 14/17 | +4.7 / +6.6 | 21.3% | -32% | 0.85 | 1.4% |
| Growth + low volatility + yield ("Core defensive") | 27.9% | +2.4% | 0.75 | 10/17 | -2.7 / +7.0 | 18.9% | -27% | 0.79 | 1.1% |
| Low volatility + yield | 25.3% | +0.3% | 0.10 | 10/17 | | 18.3% | -29% | 0.70 | 0.8% |
| Universe (equal weight) | 24.9% | | | | | 26.4% | -57% | 0.47 | |

- Rebuilding monthly, "Core growth" was +7.0% (t 2.30, 14/17 years). Among the 60 most liquid stocks it was +7.3%
  (t 2.85).
- Caveat: the price file holds stocks listed today (survivorship), which flatters every equal-weight portfolio a
  little. The comparison with the universe is fairer than the absolute returns.

## The design (what the tool does)

### 1. Client profile (`profile.py`)
- Questions cover:
  - amount, monthly addition, horizon (years), goal
  - largest loss you could live with in a bad year
  - what you would do after a 20% fall
  - experience, income stability, age
  - cash needed within a year
  - Shariah, gold allowed, existing holdings
- Scores:
  - capacity (ability): horizon, income, age, liquidity need
  - tolerance (willingness): loss, reaction, experience, goal
- Profile = the lower of the two, on 5 levels: Conservative, Moderately conservative, Balanced, Growth, Aggressive.

### 2. Strategic allocation (`cma.py`)
- Equity starts from the profile's base (20 / 35 / 50 / 70 / 85%) and is capped by the horizon (glide path):
  - under 1 year: 0%
  - 1-2 years: 15%
  - 2-3 years: 30%
  - 3-5 years: 50%
  - 5-7 years: 70%
  - 7+ years: no cap
- Gold, when allowed: 5-10% depending on the profile.
- The rest goes to T-bills / money market. Cash needed within a year is set aside first.
- Then the **loss check**: a bootstrap of 2008-2026 monthly asset returns re-centred on today's assumptions.
  Equity is cut in 5-point steps until the 1-in-20 bad 12 months is no worse than the client's stated loss limit.
- Assumptions:
  - T-bills: today's policy rate
  - equities: 10-year PIB + 5% (the fair value model's premium)
  - gold: SBP's 5-7% inflation target midpoint + a 1% real return + nothing more
  - volatility and correlation: from 2008-2026
- Projection: a 10,000-path simulation of the plan with monthly additions gives:
  - median, bad and good outcomes
  - chance of a loss over the horizon
  - chance of beating T-bills

### 3. Equity model portfolio (`select.py`)
- Universe: liquid stocks (top 120 by value traded), KMI All Share if Shariah.
- Excluded: defaulters, negative book value, failed data checks.
- Score:
  - "Core growth" for Balanced / Growth / Aggressive
  - "Core defensive" for Conservative / Moderately conservative / income goals
- Top 15-20 by score, equal weight.
- Caps:
  - at most 30% of equity in one sector
  - at most 10% in one stock
  - position at most one day of 20% of the stock's traded value
- Whole shares (one-share lots).
- Small amounts (equity sleeve under about Rs 200,000): ETFs instead.

### 4. Checks
- The proposed portfolio goes through the full review engine.
- The plan is tested in every past crisis using the asset-class data.

### 5. Recommendations for an existing portfolio (`advice.py`)
- Concrete trades, each with its reason and the tested evidence:
  - fix the rule breaches
  - move toward the plan's allocation
  - replace holdings in the bottom fifth of the tested score with the best in the same sector
  - keep the 1-in-20 loss within the stated limit
- Sell gains are shown with an estimate of the 15% capital gains tax. Losses can be set off against gains on
  listed securities and carried forward 3 years (section 37A, applied by NCCPL).

### 6. Monitoring
- Rebalance when an asset class drifts 5 points (or 25% of its target).
- Review the equity list every quarter.
- Review the whole plan once a year, or when something changes.

## Regulation (important)
- In Pakistan, giving investment advice to clients is a licensed activity. An investment adviser is an NBFC
  licensed by SECP (NBFC Rules 2003, NBFC Regulations 2008).
- AMCs must also follow SECP's suitability and risk-categorisation rules.
- The plan is therefore written as a MODEL plan for a profile: educational, not a personal recommendation. It
  should be used with clients only under the right licence or through a licensed partner.
