# Stock of the Day: research checklist and build plan

Goal: every day, one company gets a full research report (cards + a page on the data hub website),
built automatically from official data. Order: all KMI-30 companies first, then the rest of the KSE-100.
No company repeats until the list is finished. Information only, no buy/sell calls.

---

## Part 1. What professional equity researchers check before picking a stock

### A. The business (what do they actually do?)
- What the company sells, to whom, and how it makes money
- Revenue split by segment / product / customer type; exports vs local sales
- Where it operates: plants, mills, fields, branches, cities, provinces, export markets
- Capacity and how much of it is used (cement tons, fertilizer tons, refinery barrels, bank branches)
- Market share and main competitors; what protects it (brand, cost, licence, location)
- Group structure: parent / sponsor group, subsidiaries, associates, joint ventures
- History and milestones (founded, listed, big expansions, mergers)

### B. The industry and Pakistan's economy
- Where the sector is in its cycle (demand, prices, capacity coming)
- Key input costs: coal, gas, furnace oil, cotton, imported raw materials
- Rupee sensitivity: importer (hurt by a weak rupee) or exporter (helped)
- Interest rates: how much debt, how much it benefits from rate cuts
- Taxes and regulation: super tax, sales tax, regulators (OGRA, NEPRA, DRAP, SBP, PTA)
- Circular debt exposure (power, E&P, OMCs, gas companies)
- Government policy, IMF programme conditions, budget measures

### C. Management, ownership and governance
- Who owns it: sponsors, government, foreigners, free float
- CEO, chairperson, board; track record of the group
- Auditor and audit opinion; related-party dealings
- Director / insider share dealings
- Dividend record and capital allocation (do they reinvest well, pay out, or waste cash?)
- Shariah status (KMI-30 membership)

### D. Financials (ideally 5 to 10 years)
- Sales growth, gross / operating / net margins
- Profit after tax and EPS trend; one-off gains or losses
- Return on equity and on capital employed
- Cash flow: operating cash vs reported profit, capex, free cash flow
- Balance sheet: debt, cash, net debt to equity, interest cover, receivables
- Working capital (inventory, receivables, payables)
- Dividends per share and payout ratio
- Quarterly trend: is the latest quarter better or worse than a year ago?

### E. Valuation
- P/E (price / earnings), P/B (price / book value), EV/EBITDA, dividend yield
- Compared with its own history, its sector and peers
- What analysts expect (target prices, EPS estimates) where published
- For groups: value of holdings (sum of the parts)

### F. Market behaviour
- Price trend (1 week to 5 years), 52-week range, distance from high
- Liquidity (value traded per day), volatility
- Index membership (KSE-100, KMI-30), foreign interest
- Upcoming events: results date, AGM, ex-dates, expansions coming online

### G. The future
- Expansion projects and timelines; new products and markets
- What management says about the outlook (directors' report, corporate briefing sessions)
- Sector outlook and policy changes ahead

### H. Risks
- Customer / supplier concentration, regulation, currency, legal cases
- Governance red flags, high debt, delayed receivables, thin trading

### What a professional report concludes with
Investment thesis, catalysts, risks, valuation and a recommendation. **We publish everything except the
recommendation**: the reader gets the full picture and decides.

---

## Part 2. What we can get for free, automatically

| Item | Source | Status |
|---|---|---|
| Business description, CEO, chairperson, company secretary, head office, website, auditor, registrar, year end | PSX company page | Ready (tested on LUCK) |
| Shares, free float, market value | PSX company page | Ready (already used) |
| Sales, profit after tax, EPS: 4 years + 4 quarters | PSX company page | Ready |
| Gross and net margin, EPS growth, PEG | PSX company page | Ready |
| EPS history back to ~2010, dividends, bonus | scstrade results history | Ready (already used) |
| Price history since 2018, returns, 52-week range, liquidity, chart | our price database | Ready |
| P/E vs sector vs its own history | our valuation research | Ready |
| Upcoming board meeting, ex-dates, recent filings | scstrade / PSX | Ready |
| KMI-30 and KSE-100 member lists | PSX index pages | Ready (tested: 30 and 100) |
| Company logo | the company's own website (from PSX page) | Likely, needs a fallback (drawn initials) |
| Segments, plants and cities, capacity, subsidiaries, history, projects, outlook, debt and cash | annual / quarterly report PDFs (financials.psx.com.pk, company sites) | Needs reading the report (see decision 1) |
| Analyst targets / consensus | not free | Only when a preview appears in news |

---

## Part 3. The daily report (what members receive)

One Telegram album (6 to 7 cards) + a permanent page on the website, every evening.

1. **Cover**: logo, name, ticker, sector, KMI-30 / KSE-100, price, market value, P/E, one-line "what they do", 1-year chart
2. **The business**: what they sell, segments, where they operate (plants / cities), group and subsidiaries, key people, auditor
3. **Ten-year story in numbers**: sales and profit bars, margins, EPS, dividends
4. **Right now**: latest quarter vs a year ago, last 4 quarters, financial health (debt, cash) when available
5. **Valuation and market**: P/E vs sector and own history, returns vs KSE-100, 52-week range, liquidity, free float
6. **Past, present, future**: key milestones, where they stand today, stated plans and projects, main risks
7. **Quick check and what to watch**: the plain-fact checklist + next results date, ex-dates, upcoming events

Every number shows its source. Footer: information only, not investment advice.

---

## Part 4. Which company each day

- Queue: KMI-30 first (30 companies, about 6 weeks of trading days), then the KSE-100 companies not yet covered
  (about 70 more). No repeats until the list is done; then a second round with fresh numbers.
- Order inside the queue: largest first, but never two companies from the same sector on consecutive days.
- Fresh results jump the queue: if a not-yet-covered company reported results in the last 3 days, it goes next
  (its report then has the newest numbers).
- Companies with results due in the next 2 days wait until after the results.
- Index changes (PSX reconstitutes indices twice a year) are picked up automatically; tickers with temporary
  suffixes (XD, XB, XR) are normalised.
- A covered-companies log is kept (with the date and website link), so nothing repeats and old reports stay findable.

---

## Part 5. Accuracy and safety rules

- Numbers only from official sources; every figure on a card has a source line.
- If a section's data is missing, the section says so instead of guessing.
- Text written from reports must pass a number check (every number must appear in the source document).
- No buy / sell / target price from us. No copying of brokers' reports.
- Logos are used only to identify the company, never altered.
- If the report fails a check, it is held and the admin chat is told; the day's slot is not skipped silently.

---

## Part 6. Build steps

1. Company profile collector (PSX page: description, people, address, website, auditor, financial tables, ratios)
2. Logo fetcher with cache and fallback
3. Index queue + covered log + daily picker
4. Report-reader for the latest annual / quarterly report (only after decision 1)
5. The 7 cards + the website report page + archive list
6. Daily schedule in GitHub Actions, dry-run previews, then go live
7. Test on 3 companies from different sectors (e.g. LUCK, MEBL, SYS) and review together before going public

---

## Decisions needed from Umer

1. **Reading annual reports**: segments, cities, subsidiaries, projects and future plans are only inside the
   report PDFs. Reading them automatically needs the free Gemini key we already have. The text would be checked
   (every number must be in the report) and written plainly. Alternative: no report reading, and the report
   covers only the numbers and the PSX description (shorter, no "future goals" section).
2. **Days**: Monday to Friday only, or 7 days a week?
3. **Time**: evening after the close (for example 8 pm), or morning before the market opens (for example 8:30 am)?
4. **Channel**: main channel, or a private test week first?
