# PSX News Pipeline — project guide (read me first)

Owner: **Umer**, Financial Analyst, runs the Telegram channel/community **"PAKISTAN INVESTORS"** (PSX investors).
Not a developer — explain in plain language, give click-by-click steps, do the technical work yourself.

## What this is
Free, fully automatic pipeline that posts PSX-moving news, official data and market briefs to a Telegram
channel 24/7. Runs on **GitHub Actions** (public repo `4umr/psx-news-pipeline`, branch `main`), not on the laptop.
- Workflow `.github/workflows/pipeline.yml`: cron every 5 min (GitHub actually runs ~every 15–20 min) +
  manual "Run workflow" with a **What to do** menu: run · test --admin-only · test · brief morning/opening/midday/close/results/macro/brokers/week_ahead · about · chats.
  Secrets also include `GEMINI_API_KEY` (set by Umer; health check on 2026-10-16: ok, gemini-2.5-flash).
- `.github/workflows/keepalive.yml`: monthly commit so GitHub never pauses the schedule.
- State (what was sent, snapshots, history) = `state/state.json`, kept in the **Actions cache** (key `psx-state-v2-*`)
  with a backup copy force-pushed to the orphan branch **`state`** (after every run); auto-restored if cache is lost.
- Secrets (GitHub → Settings → Secrets → Actions): `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (channel `-1004482191611`),
  `TELEGRAM_ADMIN_CHAT_ID` (Umer's *personal* chat id — NOT the bot id 8714104355; bot can't message itself).
  **Never** print/echo secrets or personal ids; Actions logs are public. Don't ask the user to paste tokens in chat.

## Code map (`pipeline/`)
- `__main__.py` CLI: `python -m pipeline run|loop|brief <name>|test [--admin-only]|chats|about [--dry-run]`.
  `--dry-run` writes messages to `out/preview.txt` and cards to `out/*.png` (no Telegram).
- `runner.py` one pass: collect → watchers → news scoring → send. Every component wrapped in `_safe()`; state always
  saved in `finally`; component-error / source-health alerts to admin after 3 consecutive failures; daily 23:45 report.
  **Infographic mode** (`brand.infographic: true`, default since 2026-10-09): briefs are posted ONLY as image albums
  + short caption (title + top-story source links); every data alert and every score≥8 news item = one infographic
  card + short caption (title + visible links). Quick headlines (score 4–7) stay compact text. Watchers attach a
  structured card spec in `Alert.extra["card"]` (theme, kicker, title, stats, rows, why, sectors, source, links);
  alerts without one get a fallback card parsed from the text. `infographic: false` restores old text posts.
  WhatsApp-ready text copies → admin chat.
- `watchers.py` SBP changes (policy rate, reserves, T-bill/PIB cut-offs, with sanity checks), corporate results (+FY EPS YoY
  from PSX), FIPI/LIPI (+by sector), KSE-100 moves (+intraday path recording), big stock moves & watchlist, PBS CPI/SPI
  (real numbers from the .docx/post), MPC calendar & reminders, PSX company filings (material info, insider dealing),
  unusual volume (needs ≥5 sessions of history), global moves.
- `briefs.py` card albums (Mon-Fri PKT): morning 08:45, opening bell 10:00, midday 12:30, **broker views 13:00**,
  close 17:15 (holiday detection), results tracker 18:30 (only on results days), week_ahead Sun 19:00; macro dashboard
  auto-posts right after each CPI release. AI commentary in briefs via `_ai_points()` (falls back to rule-based highlights).
- `cards.py` matplotlib infographics in the **PAKISTAN INVESTORS brand kit** (`Card` class, px coords, 1080×1560):
  ONE light scheme (warm white bg, white panels, deep green text, gold titles; `THEMES` all share `_LIGHT`, the theme
  name only sets the footer label), "PI" logo (drawn; `assets/logo.png` overrides), serif STIX titles, rounded panels,
  sparklines, footer nav icons + disclaimer. Up green `#1a8a4a` ▲ / down red `#c8372f` ▼. No author tag anywhere.
  Albums: morning = PSX market open + US market close + commodity update + top stories; midday/close = PSX card +
  movers & sectors + top stories; week_ahead = week in review + the week ahead. `alert_card(cfg, spec, when, tiles)`.
  No emoji in images (font can't draw them); `text.parse_math=False`. Preview with `--dry-run` → `out/*.png`.
- `style.py` header/section/footer (one-line signature, **no hashtags**), `to_whatsapp()`.
- `links.py` visible links: decode Google News → publisher URL, short forms (dawn/brecorder/tribune/thenews). `common.link()`
  always renders **plain visible URLs** (hidden <a> links break when copied to WhatsApp).
- `street.py` Street View: captures brokerage forecasts (Topline, AHL, JS Global, AKD…) from news, scores vs actual CPI /
  SBP decisions → accuracy scoreboard (Sunday post). Never invent analyst track records.
- `scoring.py` keyword topics (config.yaml `topics`), dedupe (fuzzy + same-topic; numbers/units and rise=gain
  normalised), US ticker-list filter ("VLO, MPC, PSX" are NYSE tickers), `is_stale()` (old dates in title/URL),
  `exclude_titles` (daily price-list pages), `max_per_global_topic_3h` flood cap.
- `analysis.py` Gemini (free tier, secret `GEMINI_API_KEY`) reads the actual article (HTML or PDF via pypdf) for
  important news: relevant?, summary, why, key facts, level, tone, sectors (fixed list). Not relevant -> admin only.
  Also returns kind (news/opinion/analysis/promo) + event_date: only fresh real news reaches main channel.
  Main channel also requires published <= `max_important_age_hours` (6). Bad key -> one admin warning/day.
  Falls back to `impact.py` rules without key / on error / rate limit. Model in config `ai.model` (auto-picks newest flash).
- `context.py` market context pack (index, movers, FIPI, policy/CPI/real rate, T-bills, KIBOR, reserves, PKR, MPC,
  global, today's headlines marked unverified, broker notes) fed to every AI call. `analysis.narrate()` writes brief
  commentary (What to watch today / What is driving the market / Why the market moved / The big picture).
  Guardrails: `numbers_ok()` rejects AI text with numbers not in the sources; advice sentences dropped; fixed
  sector + KSE-100 ticker lists; `conflicts_official`; `ai.daily_limit`. `_contradicts_official()`: rate cut/hike
  headlines with no SBP meeting today/yesterday -> admin as Unconfirmed.
- Routing: main channel = infographic updates + important news (image + short explanation caption);
  admin channel (`TELEGRAM_ADMIN_CHAT_ID`) = raw headline feed (`raw_news_to_admin`) + warnings + WhatsApp copies.
- `sources/brokers.py` broker research list from ksealert.com/Research (broker, title, PDF) -> 13:00 "Broker Views"
  card, always credited; feeds Street View. Research notes: `docs/broker-research.md`. Never copy broker posters.
- Design (2026-10-13): ONE light scheme for all cards (warm white, deep green text, gold titles); no author tag.
- `impact.py` rule-based impact engine: topic + direction words → level High/Medium/Low, tone, one-line summary,
  sectors positive/pressured. Used in every news post, news card and Top Stories card. General relationships only.
- `style.tidy()` runs on every channel post/caption/WhatsApp copy: **no emojis, no long dashes (—)** (user request).
- `sources/`: `news.py` (RSS + Google News), `sbp.py` (homepage indicators, press releases, MPC calendar), `scs.py`
  (scstrade.com JSON endpoints: results, board meetings, book closures, KSE-100 view, indices, daily activity, FIPI, index
  history, 52-wk), `psx.py` (dps.psx.com.pk/company/SYMBOL filings + EPS), `pbs.py`, `forex.py` (forex.pk open market), `markets.py` (yfinance).
- `config.yaml` everything tunable: brand (name/author/title/channel_link/cards/alert_cards/whatsapp_copy), briefs times,
  thresholds, feeds, Google queries, topics + topic_meta (sectors), watchlist (empty), commodities/regional, `swing:`.

## Fast loop (built 2026-11-10) — the fix for information delay
- Measured 2026-11-06: GitHub started the scheduled pipeline only every 55 min on average (35-82), not every 5.
- `python -m pipeline auto` (the workflow's default): inside `config.fast_loop` (Mon-Fri 08:30-18:45 PKT) it runs
  `runner.run_loop`: a light pass every 60 s (RSS feeds only, scstrade results/KSE-100 view/daily activity, PSX
  filings with an 8-page rotation + watchlist + top-10 + `watchers.results_due` = board meetings today/yesterday
  checked EVERY pass), and every 5th pass a full pass (SBP, PBS, global markets, Google News, FIPI, MPC). A due
  brief or MPC day forces full data. Each job stops after 100 min (`chunk_minutes`) or at the window end and
  writes `continue=true` to GITHUB_OUTPUT; the workflow then dispatches the next job (`actions: write`) unless a
  scheduled run is already queued in the concurrency group. Outside the window: one pass per scheduled run.
- Workflow: `timeout-minutes: 125`; python runs in the background and `.github/backup_state.sh` pushes the state
  to the `state` branch every 10 min; restore keeps the NEWER of cache vs branch (`_saved` stamp in state.json).
- **Always on since 2026-11-11** (GitHub's scheduler had degraded to 5-10 HOURS late for every workflow of this
  repo: Stock of the Day arrived at 04:00, the swing scan the next morning). `fast_loop.always_on: true`: the
  chain never stops; outside the window a full pass every `idle_seconds` (600). `runner.loop_wanted`,
  `set_continue(True/False)`. Hand-over step: `always() && !cancelled() && continue != 'false'`, waits 5 min
  after a failed job, skips if a run is already queued. So: cancel the running job by hand = chain stops (cron
  restarts it hours later); `fast_loop.enabled: false` = stops for good. Manual menu commands (brief x, test...)
  queue behind the current chunk and then hand back to `auto`. Menu option `loop --minutes 3` = quick
  hand-over test / chain starter.
- `runner._start_workflows` (config `start_workflows`): the loop dispatches swing.yml (17:40 Mon-Fri, Sunday 18:00
  weekly cards, Saturday 10:00 re-test) and stock.yml (18:00 + 20:00) on time through the GitHub API with the
  job's own GITHUB_TOKEN (env on the Run step; never logged), 90-minute grace, 3 attempts, flag `wf:...` per
  day. Their own crons stay as a backstop; each workflow is idempotent (last_scan / tries).
- Known risk: a job that is always open is a grey area of GitHub's Actions terms (free for public repos); if GitHub
  ever objects, set `fast_loop.enabled: false` and the old behaviour returns. Local test: `loop --minutes 3 --dry-run`.

## Research Lab (built 2026-11-11) — EXPERIMENT, test chat only
- Umer's idea: compile everything scstrade + other sources show about a stock, let the AI make a call, and TEST
  whether it beats simple rules. Top 5 KSE-100 weights: FFC, UBL, MEBL, ENGROH, OGDC (`lab.symbols`).
- `sources/scs_deep.py`: `snapshot(sym)` = SS_CompanySnapShot.aspx HTML, values in spans `ContentPlaceHolder1_lbl_<key>`
  (strip 24 chars; FIELDS map ~55 keys: price ranges, 1/3/6/12m returns, beta, free float, EPS ytd/last/annual,
  P/E, SCS EXPECTED P/E / growth / BV / ROE / dividend / sales, P/B, ROE/ROA, dividend yield, payout, EV/EBITDA,
  margins, current ratio, debt), EPS by year + by quarter from the highcharts arrays, insider + announcement
  tables. TRAP: SCS appends "at <today's date>" to old board-meeting notices; stripped, never used as a date.
  `technicals()` TA_TechnicalReport.aspx/resdata (MACD d/w, RSI, S1/S2/R1/R2, SMA50/100, 52w; 74 stocks);
  `signal(sym)` TA_TradingSignals.aspx/chart (index KSE100); `signal_record(sym)` TA_TradingSignalsHistory.aspx/grid
  (Buy->next Sell trades over 3 years: FFC 60% right, UBL 40, MEBL 55, ENGROH 38, OGDC 35 as of Nov 2026);
  `research(sym)` RE_Reports.aspx/chart (par, date1, date2); `opinions()` AnalystOpinionMain.aspx tables
  class sTable (sym - company / title / author + m/d/yyyy / text). Market valuation lists, stock comparison,
  heat map, MTS / Valuation Matrix PDFs exist but are not used yet.
- `lab.py`: `dossier()` = snapshot + technicals + SCS signal and its record + SCS reports + analyst notes +
  12-month prices and returns vs KSE-100 (scs price/index history) + volatility/volume + results history
  (earnings.company, NaN-safe) + board meetings + ex-dates + PSX filings + Google News 14 days
  (`lab.news_terms`) + ksealert broker titles + sector flows 4 weeks (flows.week) + news-pipeline market
  context (state branch). `call()` one Gemini request: view Outperform/Neutral/Underperform vs KSE-100 over
  20 sessions, confidence 1-5, headline, 3 thesis points, risks, catalysts, valuation/momentum/technical,
  change_view_if; every sentence passes analysis._checked against the dossier text; < 2 thesis -> no call.
- Scoring: calls in `state/lab/calls.json` (cache psx-lab-v1 + ENCRYPTED branch lab-data); `score()` = entry next
  session's OPEN, exit close after 5 and 20 sessions, minus KSE-100 over the same days; `scoreboard()` hit
  rates for the AI, SCS signal and 1-month momentum on the same dates, Outperform minus Underperform spread.
  Daily calls on the same stocks overlap: judge after ~3 months, not on a few weeks.
- `labcard.py` one-page card (two-pass height): call banner, 8 tiles, 12m price vs SMA50 vs KSE-100 rebased with
  S1/R1, quarterly EPS vs a year earlier, why / risks / coming up, signal strip, lab record line.
- Workflow `lab.yml` (dispatched by the loop at 18:20 Mon-Fri via start_workflows; backstop cron 18:30; manual
  lab / lab --force). Sends to secret TELEGRAM_RESEARCH_CHAT_ID, else the admin chat. Never the main channel.
  Local test without Gemini: build dossiers + placeholder call (cards render, scoring testable).

## Underdog method + fair value (built 2026-11-12) — Umer's way of buying small caps, tested
- Umer: big companies on fair value/fundamentals; underdogs on past season, history, all-time high and ASSET value
  (accumulate in a calculated zone, e.g. STCL below 8.5, then ride the volume when institutions/HNWIs arrive).
- Data: `data/history_long.csv.gz` (scstrade prices 2008-2026, 482 stocks, local only) and `data/bv.csv` (book
  value per share by fiscal-year label, 5 years, `scs_deep.book_values` = snapshot highcharts 'Book Value' series,
  categories latest first; latest label can be a part year). P/B point in time: BV of fiscal year Y counts from
  that year's latest results announcement (`underdog._pb`), skipped 2 years after a bonus/split (exf < 0.92).
- TESTED 2010-2026 vs similar stocks (scratch ud_test.py idea; underdog liquidity floor Rs 2m/day, Mid/Small/Micro):
  3-year price floor -2.2% over 60 sessions (t -3.4, 3/17 years +), floor + volume ignition -2.9%, deep below ATH at
  floor -1.1%, strong calendar month ~0%, season+floor no, below book alone mixed. Good results + up 3%: +4.5%; the
  same within 15% of floor +0.9% (floor adds nothing); **good results + up 3% + P/B <= 1.2: +11.3% (n 78, 2022-26,
  median +0.5%, 51% beat)**; good results + P/B <= 1.2: +5.1% (n 244, 4/5 years). => the underdog edge = ASSET
  BACKING + RESULTS TURN; floors, ATH discount, seasons are shown as information only.
- Fair value (`fairvalue.py`): COE = SBP policy rate + `fair_value.erp` (5) -> 17% now. EPV = last 4 quarters' EPS
  (from snapshot eps_quarters; SCS "expected" EPS annualised one-off quarters: refineries 6x) / COE = headline fair
  value (tested: big caps' most overvalued fifth -4.8% over 60 sessions all 4 years, small/mid 50%+ gap +4.8% all 5
  years 2022-26; but cheap P/E did not work before 2023 -> regime, say so). DDM only if payout >= 40%, g <= 8% and
  <= COE-6. NAV = book. Balance sheet per share: assets = BV / equity-to-assets (snapshot `ear`), liabilities =
  assets - BV, liab % assets, debt/equity, current/quick ratio, cash; flags skipped for banks/insurance/modarabas.
- Live (lab.yml, daily after the blue chips): `underdog.radar` scans ~380 snapshots (traded >= Rs 0.5m today, ~5 min),
  ranks fair-value gaps, lists liquid non-top-30 stocks at P/B <= 1.2 (`underdogcard.radar_card`), flags today's
  tested trigger (results in the last 2 sessions, earnings.assess GOOD, up 3%+, P/B <= 1.2; logged in calls.json
  `underdog` and scored at 5/20 sessions vs KSE-100), and a one-page `watch_card` per `lab.underdog.watch` (STCL):
  asset zone 0.8x-1.2x book (breaks below 0.9 x lower edge), assets vs liabilities tiles, 3-year floor, 52w/3y
  highs, ATH (adjusted), this/next month season, value traded vs 60-day, red flags (`underdog.trap` + balance
  sheet), next board meeting, the trigger. Blue-chip cards gain a "Fair value and balance sheet" tile panel and
  the AI dossier gets the fair value lines.

## Lab v2 (2026-11-12): send only when it matters, verified data, any stock on request
- Umer: "don't need everything in a day, give a call when the opportunity is there; self-sufficient, error free,
  any stock; underdog, fair value, assets vs liabilities 100% correct; most dependable fair value formula".
- SENDING (`lab.run`): calls for the 5 blue chips are made and LOGGED every trading day (scoring continues), but a
  card is sent only for `_bluechip_reasons`: first view, view changed, confidence rose to 4+, board meeting or
  ex-date within 5 sessions (once per event; state `calls.json` -> `sent`). Underdogs (`_underdog_reasons`, state
  `ud_state`): tested trigger, added to watch list, asset-zone state change (inside/above/below/broken), volume
  burst (3x + up 3%), new quarterly results. Friday (or --force): roundup text with all views + record + weekly
  radar card. Quiet day = nothing sent. Gemini busy -> `_call_retry` waits 30/60 s (free quota shared with news).
- ON REQUEST: lab.yml input `symbol` (e.g. STCL or STCL,LUCK) -> `lab --analyse X` -> AI view card + underdog card
  for any stock now, always sent, dated today (live prices), no market scan (`underdog.details_for`, ~20 s).
- CHECKS (`checks.py`, before anything is shown): snapshot price vs exchange close (1%, 5% in market hours),
  4 consecutive quarters, BV > 0, 0 < equity/assets <= 100, P/B vs SCS (rounding + intraday allowed),
  `trailing()`: TTM EPS from snapshot quarters + RESULTS ANNOUNCEMENTS (snapshot can be a quarter behind: STCL,
  TATM missed 1Q27), the 4 used quarters cross-checked (>10% apart = problem, 2-10% = note; older quarters differ
  where SCS restated for splits, LUCK/SYS ~80%, not checked), bonus issue in 12 months = problem. Book value
  brought up to date for missing quarters (`underdog.verify`: + EPS - dividend% x face value), labelled estimate
  (STCL 7.72 -> 6.10, P/B 1.17 -> 1.49: outside its asset zone). Stocks failing a check are not shown (logged).
- FAIR VALUE v2 (`fairvalue.compute(snap, coe, ttm, bv_hist)`): median of EPV (ttm/COE), residual income
  (BV x (ROE-g)/(COE-g), ROE = ttm/BV, g = ROE x retention capped 8% and COE-4) and DDM (payout >= 40%, last
  full-year DPS); range + "methods agree" (range <= 50% of fair). Screens/radar still rank by the tested EPV gap.
  Balance sheet at the last YEAR END (BV of the equity/assets period from book_values) so all figures share one
  date. Liquidation / net-net value NOT computed (no current assets / current liabilities in the data).
- SELF-TEST (`python -m pipeline selftest`, also run at the start of every Lab run; failure -> nothing sent +
  admin note): hand-computed fair value (EPV 58.82, RI 66.67, DDM 72, median 66.67, assets 120, liabilities 72),
  growth cap, quarter gaps, floor 324.9 on closes 1..1000, zone/break, season, ignition, trap flags, scoring
  (entry next open: +3.92% at 5 sessions), plus live source checks on FFC and OGDC.
- Delivery facts: main channel "News Feed" = Umer's (3 members, private); admin = his bot chat (confirmed).

## Daily stock scan + volume tests (2026-11-12) — Umer: "I expect some stocks daily", track volume / big investors
- VOLUME TESTED 2010-2026 vs similar stocks (scratch vol_test.py, liquid >= Rs 2m, Mid/Small/Micro): burst (3x value,
  up 3%) -1.3% over 60 sessions, 0/19 years (reliably negative); + near 3-month high -0.4%; quiet accumulation -0.7%;
  burst AFTER BAD RESULTS (last 90 days) -3.9%, 2/16 years = strong warning; burst + good results + breakout +1.5%
  (11/16, weak); burst + P/B <= 1.2 + good results +4.7% at 20 sessions (small sample). => volume is never shown alone.
- `scan.py` (in lab.run every trading day, SENT DAILY, first card; config lab.underdog.daily_scan): sections
  breakouts (results GOOD + up 3% today/yesterday, small/mid; + P/B <= 1.2 = breakout_backed), backed (radar's
  verified P/B <= 1.2 list with EPV gap >= 50% and GOOD results in the last 90 days; cyclicals with a loss-making
  5-year average marked), volume (burst + GOOD results + within 2% of the 60-session high), warnings (burst + BAD
  results), following (flags of the last 10 sessions: move since flag vs KSE-100, still within 5% of the high).
  Needs today's rows in state/prices.csv.gz: lab.yml restores the swing cache (state + data, read only) BEFORE
  the lab cache. `_last_result` judges on the QUARTER EPS vs the same quarter a year earlier (annual label = 4th
  quarter) and says so in the line. Flags logged in calls.json `scan_flags` / `calls_scan`, scored at 5/20.
- Main-channel unusual-volume card text now states the test result (no chasing). Topic fix: 'reserves' no longer
  matches oil/gas discoveries (OGDCL discovery was filed as forex reserves). lab.yml: failure alert to admin.
- Fair value adds NORMALISED EARNINGS (5-year average EPS / COE) only when the year is abnormal (losses now, or
  TTM outside 0.5-1.5x the average); RI only counted when g > 0 (else identical to EPV). STCL: losses now and on
  average (-0.11): no fair value at all, book value Rs 6.10 is the only anchor (said on the card).

## Scan research + buy/sell levels (2026-11-12) — Umer: "add all the Research Lab stuff to the daily stocks"
- Every daily-scan stock (up to `lab.underdog.research_max` 6, sections take turns: breakouts, backed, volume,
  warnings; not repeated within 5 sessions for the same reason) gets the full blue-chip card: dossier, AI view
  (logged in calls.json `calls_scan_ai`, scored at 5/20), fair value + balance sheet, technicals, chart, quarters.
  Kicker "Research Lab · daily scan"; text lines under "Full research on today's scan stocks". No AI -> NO_CALL card.
- `levels.py` on every Lab card: SETUP from three independent families, one vote each (results/scan section,
  value: P/B <= 1.2 with good results or fair value gap 30%+, AI view conf >= 3); scan warning = -2.
  BUY SIDE >= 3, LEANING BUY 1-2, MIXED 0, LEANING SELL, SELL SIDE <= -3. Technicals never vote (price patterns
  had no edge); they give the LEVELS: swing lows/highs of 5-120 sessions -> buy zone S1..S1x1.03, fails below
  (next support within 8%, else S1 x 0.95), targets R1/R2, reward to risk; sell side: sell zone R1x0.97..R1,
  support breaks below S1. `levels.technicals(px)` fills MACD/RSI/SMA for stocks SCS does not cover (74 only).
- `lab._history` now has high/low and `adjust_jumps` (a move beyond 10% / Rs 1 = bonus or split; BNL 10:1 Dec 2025).
  Quarter chart merges the verified quarters (snapshot lags). fairvalue.lines crashed for normalised-only fair
  values (no 'g'): fixed + self-test.

- Formula audit (2026-11-12): fair value, balance sheet, checks, underdog, earnings classes, price adjustment,
  P/E, scoring re-read and checked against live data (face value read from the snapshot: LUCK/SYS Rs 2, STCL 5).
  Fixed: weekly MACD used numpy 'W' weeks (Thursday-Wednesday) -> ISO weeks (`levels.weekly_closes`); annual
  result lines now show the fourth quarter (the basis of good/weak) then the full year (`earnings.eps_line`,
  also main-channel results posts). Self-test `levels_tests`: RSI 71.43 by hand, weeks, swing levels, setup
  votes, risk line 93.10, reward to risk, split adjustment, result wording. Known by design: Lab scoring
  measures the stock from the next open but the KSE-100 from the call day's close (no index open in the data).

## Fair value v3 (2026-11-13) — Umer: "FFC fair value seems inaccurate, maybe 540-545; adjust it (on principle)"
- Old: median(EPV no growth, RI, DDM on last year's DPS) at COE = policy 12 + 5 -> FFC Rs 444. Three flaws: a
  no-growth value averaged with growth values; DDM on a stale dividend (FY25 Rs 37); Gordon formulas fed THIS
  year's EPS instead of next year's; plus the overnight policy rate as the long-term rate.
- Now (`fairvalue.py` docstring): COE = 10-year PIB yield (`risk_free(sbp snap)`, 12.85% on 4 Nov 2026) + 5 = 17.85%;
  g = ROE x retention capped 8% / COE-4 (no switch at ROE = COE: the formula lowers value when ROE < COE);
  E1 = ttm x (1+g); main method RI = BV x (E1/BV - g)/(COE - g); DDM = E1 x payout/(COE - g) only if payout >= 40%
  and natural growth is not capped; normalised = 5-year average ROE x book / COE when ROE is outside 0.5-1.5x its
  average (ROE, so grown companies like UBL are not marked down); 5-year ROE a loss but ROE > COE now -> book value
  joins (refiners). EPV kept as "No-growth value" (tile when one method) and for the tested EPV-gap screens.
- Results 13 Nov: FFC 538 (price 570), UBL 465, OGDC 371, LUCK 393, MEBL 455, HUBC 261, POL 837, MARI 633,
  MCB 267, EFERT 167, SYS 46 (growth stocks above 8% are a known limit of a one-stage model). 13 hand-worked tests.

## Lab v3 (2026-11-13): growth stage, pre-open report, record, polish
- Fair value growth stage (`fairvalue._ri_two_stage`, `history()`): when fundable growth (ROE x retention) AND the
  STEADY 5-year EPS growth (no 3x jump, no unadjusted split = EPS and book fell together, rising 3+ of 5 years)
  are both >= 12% and ROE > COE: growth starts at the lower (max 25%) and fades linearly to g (<= 8%) over 5 years.
  MEBL 455 -> 543, LUCK 393 -> 471; FFC (11.7% fundable) stays 538. Boom (3x jump in a year, SAZEW): 5-year average
  EPS joins + flag "upper bound". MARI's annual EPS is NOT split-restated by SCS (2023 420 -> 2024 64): caught.
  SYS stays expensive on purpose: ROE 17% < COE and EPS grew ~11%/yr; the market prices growth it has not shown.
- PRE-OPEN (Umer chose evening + morning): `lab --morning` (lab.yml menu; loop dispatches 08:40 Mon-Fri, grace 40,
  no cron backstop: a late pre-open message is worse than none). Reads `calls.json` -> `report` (saved by the
  evening run: every blue-chip view + every scan-research stock with setup, zones, risk, targets, close, fair) and
  adds what is new since the close: PSX filings dated after the session, Google News 1 day (ticker or both core
  name words in the title), board meeting today, ex-date today/tomorrow. One card (`underdogcard.preopen_card`)
  + text; no AI, nothing re-valued; `morning_sent` = once a day.
- `lab.chunks()`: Telegram refuses > 4096 chars; every Lab message is split at blank lines (was a silent loss risk).
- `lab.scan_record()`: the scan's own record (r20, else r5) by section on the scan card + text; open scan flags are
  scored BEFORE the card is drawn. Scan lines show the tested number ("tested +5.1%"). Stock cards end with a
  data-check line (passed / problem + notes like book value updated).

## Intraday data fix (2026-11-13) — scstrade's all-stock table is NOT live
- Found from the intraday archive: MS_DailyActivity (daily_activity) changes ONCE a day, ~16:13-16:26 PKT; from
  then until the next afternoon it shows that session. So during market hours it is YESTERDAY. Victims: the
  10:30 "unusual volume" card on 12 Nov (11 Nov volumes), big moves of non-KSE-100 stocks, the results post's
  "stock x% so far today", opening/midday breadth and volume leaders, and the intraday recorder (stored nothing).
- Fix: `scs.activity_is_today(act, view)` (KSE-100 movers: table price == live CurrentPrice?) and
  `scs.live_activity(view)` -> (rows, full): full table once today's, else KSE-100 rows from the LIVE index view
  (price, change; volume None). unusual_volume now runs 15:30-18:00 only on the full table ("Unusual volume
  today"); stock_moves adds mid/small caps only when full; briefs show KSE-100 breadth and no volume figures until
  full; recorder stores live KSE-100 prices every pass. `kse100_view_cached()` 30 s memo.
- Live price for EVERY stock: PSX screener (`psx.screener()`, dps.psx.com.pk/screener, 754 stocks, price + change %,
  no volume). dps market-watch / timeseries URLs now 404. `runner._screener_probe` (11:00-13:00 once a trading
  day) compares it with the live view and stores snap `screener_probe` [{compared, same_as_live,
  same_as_prev_close}] in the public state branch: if same_as_live ~ compared, use it for live mid/small moves.
- Early-edge study (scratch early.py, 2012-2026, mid/small/micro, Rs 2m+/day, vs same-tier liquid stocks):
  live rule GOOD + up 3%: reaction day +6.2%, next-open gap +2.1%, from reaction close +8.1% vs next open +5.7%
  over 60 sessions; but 47% are locked at the upper limit (cannot buy that day). Not locked: gap +0.6%,
  close->60 +6.6 vs open->60 +5.8. GOOD stocks drift +2.7% in the 10 sessions BEFORE results. Previous result
  GOOD -> next GOOD 41% (base 23%); previous BAD -> next BAD 36%. Buying 5 sessions before results after a GOOD
  quarter: +1.0% avg to the reaction close, 15/15 years positive, halves +0.55/+1.20, but median -0.1%, 49% beat
  -> under the 0.75% cost bar, information only.

## Level alerts (built 2026-11-13) — private, bot chat, during the session
- `levelwatch.py`, called by the runner every pass (`level alerts`), weekdays 09:32-16:35, only when >= 20 KSE-100
  stocks are moving (holidays / pre-open skipped). Levels = `report` in state/lab/calls.json (evening Lab run);
  pipeline.yml restores the Lab cache READ ONLY (step "Research Lab levels"; never saved by this workflow).
- Events, once per stock per report (`state.data["level_alerts"]`, 10 days): buy side in_zone, failed (below the
  fails-below line), target1, target2; sell side sell_zone, broke (below support). One message per pass.
- Prices: KSE-100 live from the index view; other stocks from `psx.screener()` (memo 180 s) ONLY when
  `screener_live()` agrees with the live view at that moment; else KSE-100 only (said in the message).
- Config `level_alerts.enabled`. Tests: selftest levels_tests (7 events + screener check); scratch sim.py
  simulates a day (09:20 nothing, zone, sell zone, target 1, failed, target 2, broke).

## Technical analyst (built 2026-11-13) — `ta.py`, chart reading like a professional, tested
- Umer asked for a system that reads charts like an elite technical analyst. Researched what they check
  (Weinstein stages, Minervini trend template + VCP, O'Neil/Morales pocket pivots + distribution days, Wyckoff
  springs/upthrusts, RS line) and turned each into formulas (`ta.features`, docstring). Order book: not free on PSX.
- TESTED 2012-2026 (scratch ta_test.py, liquid Rs 2m+, next open, vs same-tier, after costs, both halves): on their
  own NO edge (stage 2 entry +0.9% median -5.5%, VCP 110 cases split halves, pocket pivot / accumulation +0.8-0.9%
  weak, RS leads ~0, upthrust and distribution not negative). RELIABLE WARNINGS: stage 4 entry -1.1%/60 (large
  -1.6%), Wyckoff spring -1.5%/20 (0/15 years). AS A RESULTS FILTER (results_momentum events, n 371, +4.1%):
  stage 2 +8.1% (halves +4.5/+12.7), RS top 30% +6.5% (9/12 yrs), above 200-day +5.0%; RS bottom half -0.3%,
  below 200-day +0.1%. => the chart confirms or cancels results; numbers in `ta.TESTED`.
- Live: `ta.rs_ranks()` (RS rank of every liquid stock from state/prices.csv.gz, once per Lab run, shared["rs"]),
  `ta.read(px, idx, rs)` per stock in `lab.dossier` -> d["chart"]; `ta.lines` (facts) go into the AI dossier with the
  evidence and onto every Lab card ("Chart read" panel); `ta.verdicts` = only tested votes in `levels.setup`
  (+1 confirm / -1 cancel when results are good or the stock is in a positive scan section; -1 stage 4; -1 spring).
  Self-test: verdict logic + a synthetic uptrend read as stage 2.

## Patterns + Stock of the Day upgrade (2026-11-13) — `patterns.py`, `note._tech_page`, `lab.note_analysis`
- Umer: "advanced analysis, 10-year technical analyst, deep mathematics, every pattern"; and the daily research
  (Stock of the Day AND the daily scan) on the improved engine. He chose to show EVERYTHING publicly (setup label,
  buy zone, targets, fair value) after being told about SECP research-analyst rules; disclosure text updated.
- `patterns.py`: zigzag pivots (confirmed when price moves thr = 2.5 x 20-day vol x sqrt5, clipped 5-15%; point in
  time), double bottom/top, (inverse) head and shoulders, ascending/descending triangles, bull flag, cup and handle,
  RSI divergences, Bollinger squeeze, Fibonacci bounce; maths: volume at price (POC, value area), regression
  channel (slope, R2, z), Fibonacci levels, Hurst, beta/correlation, volatility, max drawdown. `read(px, idx)`:
  completed in last 15 sessions + forming (with the line to watch).
- TESTED 2012-2026 (scratch pat_test.py): bullish patterns alone no edge (double bottom ~0, inv H&S +0.9 weak,
  asc triangle -1.4, cup -0.5); WARNINGS: head and shoulders top -3.8%/60 (2/12 yrs), bullish RSI divergence
  -1.4%/20 (2/15), bull flag outside stage 2 -1.8%/20, squeeze outside stage 2 -1.0%/20, Fibonacci bounce -1.3%/20;
  squeeze inside stage 2 +1.3%/20 (promising, info only). `patterns.verdicts`: one -1 vote max (in levels.setup).
- Lab cards: "Patterns and price mathematics" panel; AI dossier gets pattern lines + evidence.
- Stock of the Day: `lab.note_analysis(sym, cfg)` (dossier without AI, fair value with the 10-year PIB from SBP if no
  state, setup, chart, patterns, 460 sessions of prices) -> d["ta"]; note page 3 "Technical analysis and valuation"
  (chart with 50/150/200-day, support/resistance/fails-below, pattern line, Fibonacci, volume at price; setup table;
  fair value table; chart read; patterns; evidence). Pages: summary, financials, technical, business (if the annual
  report was read; disclosures on the last page). Caption adds the setup and model fair value lines.

## System review fixes (2026-11-13) — evidence: 100 runs, state, 59 editor decisions, 60 channel posts
- Stock of the Day was published at 01:00-03:00 (11-13 Nov): stock.yml's backstop cron fires hours late, after
  midnight = a new day. `sotd.run` now refuses a SCHEDULED run (GITHUB_EVENT_NAME=schedule) outside 17:30-23:00;
  the loop's 18:00 / 20:00 dispatch and manual runs are unaffected.
- Results filings ("Financial Results for the ...", `watchers.RESULTS_FILING`) skip the 30-minute filings batch.
- Filing cards price from `scs.live_activity` (the all-stock table is yesterday's during the session).
- Gemini: `analysis.models_for(cfg, research)` + `_call(..., research=)`: news = ai.model then ai.fallback_model
  (gemini-2.5-flash-lite); Research Lab + annual-report reader = ai.research_model (flash-lite) then the main model,
  so they no longer use the news quota (12 Nov: two news items fell back to "AI unavailable"). 429 / 5xx / 404 /
  time-out -> next model; counts once against ai.daily_limit.
- Open from the review (not built): read results from the PSX filing image with Gemini vision (fastest results),
  chart filter on swing results signals, score level alerts, "strong quarters repeat" (41% vs 23%) in the results
  week card, monthly Lab auto switch-off, corroborate untrusted-source news (missed: Saudi $3bn rollover 12 Nov),
  one petrol post a day, bot-chat digest (~200-300 msgs/day), 17:00 reality check of posted numbers, audience.

## Main-channel editor (built 2026-11-11) — AI tuning from the first live day
- Evidence (10 Nov 2026, from `state.json` headlines + raw run logs): 14 news posts on the channel, ~4 worth it.
  Failures: same event from 2-3 outlets (petrol cut BR+ARY, Saudi fund BR+Minute Mirror), weak sites (Investify,
  Pkrevenue daily price page, ABP Live, Pakistan Observer, Bloom Pakistan, UrduPoint), speculation ("petrol to
  drop to Rs338 tonight"), ceremonial ("envoy outlines priorities"), Bol News "PSX nears 97,000" with the index at
  179,000, 4 fuel posts in one run, and AI number-check failures still posted with rule text.
- `editor.py` gates every instant item: `before_ai` (trusted source regex `editor.trusted_sources` or official;
  `index_conflict`: a 5-6 digit level near KSE/PSX/index words, no forecast words, must be within 10% of the
  current/previous/last-5 closes; `max_per_topic_day` 3, score-10 exempt), then `analysis.analyse(..., posted=
  editor.posted_list(state))`, then `after_ai`: not relevant / stale / kind != news / `same_event` / materiality
  < `min_materiality` (4) / not confirmed unless materiality 5. No AI (`analysis.last_status` unavailable or
  error): only official or score >= 10 and no post on the topic within `topic_gap_hours`. check_failed -> admin.
- AI prompt adds `confirmed`, `materiality` 1-5 (rubric in ANALYSE) and `same_event` (must quote an ALREADY
  POSTED line; validated against the list). Data alerts are recorded too (`editor.record_alert`, ALERT_TOPIC map)
  so news about an event already shown as a card counts as the same event.
- State: `posted` (3 days) and `news_log` (400 rows: ts, t, s, sc, topic, d=posted|admin, why, mat, conf, kind,
  lvl) in the PUBLIC state branch: review decisions any time from
  raw.githubusercontent.com/4umr/psx-news-pipeline/state/state.json. Admin chat items carry "Kept off the main
  channel: <reason>". 23:45 report adds "Main channel news: N posted, M kept for you (reasons)".
- First live day (11 Nov): 5 news posts (Hormuz, OGDCL discovery, ADB $500m, petrol cut twice), 22 kept for admin.
  Fixes: `editor.burst_hours` (2): a second post on a topic within 2 h needs materiality 5 (AI same_event missed the
  ProPakistani + The Nation petrol posts 10 min apart); PSX filings are batched (`psx_filings_batch_minutes` 30,
  `state.data["filings_pending"]`, 8 filings or any "material" title flushes at once) because the every-minute
  loop produced ~20 "1 new filing on PSX" cards a day. Lab ran on time 18:20 (5 calls, 5 cards, admin chat).
- Replay test: `python -m pipeline review --hours 36` (workflow `review.yml`, manual, uses the Gemini key, reads
  the state branch, sends/saves nothing): runs the last hours' candidates in time order through the same gates
  with a simulated posted list (`editor._clock` set to each item's time) and prints a table + RESULT.
- Raw run logs readable in the in-app browser (signed in): github.com/4umr/psx-news-pipeline/commit/<full sha>/
  checks/<job id>/logs redirects to the blob; job id + sha from the public API /actions/runs/<id>/jobs.

## Official data cards (built 2026-11-11) — new sources, one infographic per release
- `sources/official.py` readers (each returns None on fetch/layout problems and has its own sanity checks):
  `fuel_prices()` PSO price page (psopk.com/en/fuels/fuel-prices: "Effective From" + PREMIER EURO 5 = petrol,
  HI-CETANE DIESEL EURO 5, SKO, LDO); `remittances()` SBP `assets/document/Homeremit_Arch.xlsx` (sheet "July 2018
  onwards", first row under "Country" = total, column labels are dates or 'Oct-26'/'Sep-26R'; needs openpyxl);
  `bop()` SBP `Balancepayment_BPM6.pdf` page 1 in pypdf layout mode (title month, "Items" header + FY label row:
  the year-to-date heading spans the last two columns; exports - imports must equal the trade balance);
  `auto_link()` + `auto_sales()` PAMA monthly "Production-Sales-<Month>-<Year>.pdf" (layout mode; every company
  has a Prod row and a Sale row with last year's months + cumulative, then this year's; company rows on the car
  page must add up to PAMA's TOTAL CARS or nothing is posted). GROUPS maps rows to INDU, HCAR, SAZEW, MTL, AGTL,
  ATLH (+ Pak Suzuki, Hyundai Nishat, unlisted). PAMA excludes Kia, Changan, MG (said on the card).
- `official.py` watchers, called in full passes (`runner`: "official data"): fuel every 20 min, remit/bop hourly,
  autos every 2 h; state in `state.data["official"][name]` (checked, key, data, fails). First sighting is silent
  unless the file's Last-Modified is under 3 days old; fuel always needs a previous list to show the change.
  8 failures in a row -> one admin note; >30% fuel move or >45% remittance MoM -> admin "data check", not posted.
  Config `official_data:` switches each one. Test: call `official.WATCHERS` functions with a fake old state.
- All four sites answer from GitHub's servers (checked with `.github/workflows/probe.yml`, a push/manual probe
  whose results are public run annotations: `/check-runs/<job id>/annotations`). SBP rejects HEAD requests (403).
- NOT available: APCMA publishes no despatch data on its site; NFDC site unreachable; OGRA/NEPRA/Finance pages
  answer but are not parsed yet (PSO covers fuel prices).

## Intraday snapshots (built 2026-11-10, first data 2026-11-11) — the dataset nobody else has
- `intraday.py`: every pass in market hours (Mon-Fri 09:15-16:45) appends changed rows of scstrade daily
  activity (time, sym, last price, cumulative volume, high, low) + the KSE-100 level to
  `state/intraday/YYYY-MM-DD.csv`; `housekeeping` gzips finished days (yesterday, or today after 17:00) and
  keeps 10 days. `scs.daily_activity()` is memoised 30 s so a pass downloads it once.
- Workflow: the pipeline cache now holds `state/state.json` + `state/intraday`; the "Archive intraday data"
  step uploads each finished day once to the GitHub release **`intraday`** (assets named YYYY-MM-DD.csv.gz,
  public URL https://github.com/4umr/psx-news-pipeline/releases/download/intraday/<file>). Nothing reads
  it yet: after ~3 months, test early unusual volume / opening gaps with the usual backtest discipline.

## Research round 3 (2026-11-10): `edge.py` — results season and dividend calendar, tested 2018-2026
- `peer_results` (sector peers strong -> laggard 5 sessions before its own results): NO edge (design half -1.4%).
  `peer_results_risk`: nothing. `div_runup` (sell before ex-date) +0.2%, `div_capture` (collect the dividend at
  the ex-date close, adjusted return includes it) +0.4% vs similar, t 2.1, 7/9 years up: real but under our
  0.75% bar after costs -> not live; good content ("stocks do not fall by the full dividend").
  `runup_momentum` (up 15%+ in 20 sessions before results): the opposite of "sell the news": +1.6% over 60
  sessions, both halves positive, but median negative, beat 43%, design-half t 1.0 -> candidate, NOT live.
- **`peer_spillover` = the finding, but weak.** The day a sector's season tally first reaches 2+ reported with
  2 in 3 strong (EPS +50%/turnaround), the sector's liquid stocks still to report within 14 days lagged similar
  stocks over the next 10 sessions. Idea was tested as a long and came out reversed, so it is a WARNING rule
  (kind risk, h 10, live). On the Oct-16 data files it looked strong (-1.4%, t -4.1); on data refreshed 2026-11-10
  it is -0.6%, t -2.6, both halves negative, 62% did worse, median -1.7%, mid caps -1.5% / large caps +0.9%,
  3 of 9 years positive -> "Weak warning". The shrink on refresh is a fragility sign: do not over-claim.
  Live: `swing._spillover_signals`: scs.results (latest 200 only) just says WHICH sectors had a result today; the
  season tally comes from every sector member's own history (`_history`); laggards = members, liquid, not
  reported in 45 days, board meeting within 14 days; `done` key `spill:{SECTOR}:{date}` = once per 45 days;
  no signal if the tally was already strong before today. Replay on 2026-10-29 showed sane tallies (16 cement
  reported, 5 strong etc.).
- Same re-test switched `pre_results_risk` OFF (Weak warning -> Not a warning) automatically.
- Scratch evaluation without downloads: build df from data/history.csv.gz + state/meta.json, `backtest.forward`,
  `edge.events`, report every horizon and both halves (script idea in the 2026-11-10 session; ~4 min).

## Swing research (Phase 1, built 2026-10-16) — PRIVATE, admin chat only
- Workflow `.github/workflows/swing.yml` (Mon-Fri 17:40 PKT + catch-ups 19:40 and 08:10; manual: swing / swing --force /
  backtest --send). Data in Actions cache `psx-swing-v1-*` + **encrypted** backup branch `swing-data` (openssl, key = bot
  token) because the signal log is private and the repo is public. Separate from the news pipeline.
- `prices.py` price database: scstrade `MS_HistoricalPrices` per stock (history back to 2018), daily append from daily
  activity. Adjusted for dividends/bonus/splits via PSX reference price: ref = close - change; ratio ref/prev close
  (consecutive sessions only, <0.997) = ex-date factor. PSX price limit: 5% until 2020-01-20, 7.5% until 2024-06-10, then
  10% (or Rs 1). Live file `state/prices.csv.gz` (~460 days), full history `data/history.csv.gz` (local, gitignored).
  `meta()` shares/free float from PSX company pages (monthly) -> size tiers by market-cap rank: Large 1-30, Mid 31-100,
  Small 101-200, Micro 201+. Liquidity floor `swing.min_adtv` Rs 10m/day.
- `earnings.py` results history per company (scstrade `MS_Announcements` with par=SYMBOL, back to ~2010): YoY on the same
  label (1Q/HY/3Q/FY/CY + yy), classes big_up/up/flat/down/big_down/turnaround/to_loss. `EVENT_SIGNALS` rules.
- `signals.py` price rules (point-in-time) + `backtest.py` (entry next open, skip if locked up all day, costs by tier for
  ideas only, edge vs same-tier liquid stocks same day, t-stat on daily means, year by year). `python -m pipeline backtest`
  -> `out/backtest/*.png|csv` and `pipeline/swing_stats.json` (track record shown on live cards; committed).
- **Findings (Jan 2018-Oct 2026, 481 stocks, ~21k trades):** price patterns (breakouts, 52w highs, upper circuits,
  dip buying, accumulation, RS, washout) = NO edge after costs vs similar stocks. Buying before the close looked good only
  because of locked upper-circuit stocks (cannot be bought). WORKS: `results_momentum` (EPS +50% YoY or turnaround AND +3%
  on results day, mid/small caps, hold 60 sessions: +6.2% vs similar, positive all 9 years, but only 50% beat; median
  +0.5%) and `results_reaction` (+5% on results day, 60 sessions: +5.0%). Weak warnings: `lower_lock` (58% did worse
  over 2 weeks) and `pre_results_risk` (board meeting within 7 days after a bad last result). `results_drop` not live.
  Mid/small filter was chosen after seeing data: live tracking must confirm. Do not over-claim.
- `swing.py` daily scan: live rules -> log in `state/swing.json`, `_track()` follows each signal to its horizon vs
  same-tier peers; Radar card + Tracker card (`cards.swing_cards`) + text to admin. Sent only if new/closed signals or Friday.
- No Gemini/AI text in this feature (user asked "no AI traces"): plain rule-based wording only.

## Phase 2 (built 2026-10-16): results edge everywhere + self-checking automation
- `earnings.assess(sym, label)` / `eps_line()`: same-period-last-year EPS for ANY company from scstrade history
  (quarter for 1Q/HY/3Q, full year for FY/CY; `norm_label` maps CY-24, 9M-26, 1HY26...). Used by:
  `watchers.corporate_results` (intraday public post now shows YoY + price move for all companies; plus private
  "Results hit" admin alert for liquid stocks with EPS +50%/turnaround), `briefs.results` (18:30 tracker for all
  companies, reaction on the day, season tally). Old `eps_yoy` (PSX page, FY only) is no longer used there.
- `watchers.psx_filings`: rotation now covers KSE-100 then every liquid stock (avg value >= swing.min_adtv via
  `avg_values()` = recorded volumes x price). Insider/material filings logged to `state.data["events"]` with price
  (for a future backtest: PSX pages keep only ~5 recent filings, so history must be collected).
- Sunday PUBLIC cards from `swing.weekly()` (workflow 18:00/20:00 PKT Sunday): "Mid & small caps this week" (liquid,
  market-cap rank 31+, adjusted week returns, value traded vs usual, big moves with no PSX filing in 2 weeks) and
  "Results week ahead" (board meetings next 7 days, liquid stocks, last result vs a year ago). Data only, no calls.
- `education_card` (out/backtest/education.png): public-ready lesson from the backtest; NOT auto-posted (owner decides).
- Robustness (user asked "error proof, accurate in future"): Saturday re-test (`backtest --notify-changes`) refreshes
  `state/swing_stats.json`; `swing.active_rules()` uses only rules whose latest verdict is in `backtest.ACTIVE`;
  `_monitor()` warns at 20 live cases and auto-pauses at 40 if the live record is the opposite of the backtest;
  `_band_ok()` pauses circuit rules if PSX changes the 10% limit; `prices.update_live` never saves partial downloads
  (attrs["stale"] -> admin note); backtest never uses an unfinished session; workflow failure -> admin message;
  fresh-start / missing data / all-rules-off -> admin note. Drill script idea: simulate scs._post=[] etc.

## Phase 3 (built 2026-10-16): why is it moving + data hub website
- `watchers.stock_moves` now covers KSE-100 + watchlist + every liquid stock (avg value >= swing.min_adtv), and
  `move_reasons()` adds the public reason: results today (with YoY), a PSX filing since yesterday (NOISE filtered),
  or a headline from the last 24h matching the ticker (case-sensitive) or the two-word company name (`_core_name`;
  single words like 'Punjab' gave false matches). Else "No company filing or news found so far".
- `site.py` + `python -m pipeline site` -> static site in out/site: index (screener: search, sector/size/liquidity
  filters, sortable), s/SYMBOL.html (returns, 12-month adjusted SVG chart, key facts, board meeting, book closures,
  results vs a year earlier), results.html (next 14 days + last 14 days), about.html. Public data only.
  Built in swing.yml after each scan; deployed by the `deploy` job (actions/deploy-pages, continue-on-error) to
  https://4umr.github.io/psx-news-pipeline/ once Pages is enabled (Settings > Pages > Source: GitHub Actions).
  Build raises on bad data -> previous site stays; admin told. Local preview: .claude/launch.json "site" (port 8765).
- Face value is not on PSX pages, so dividend YIELD is deliberately not computed (dividends shown as % of face value).

## Phase 4 (built 2026-10-16): research round 2 with a design/test split
- Verdicts now require the rule to work in BOTH 2018-22 (design) and 2023-26 (test) (`backtest.SPLIT`); report cards
  show both halves. `research.py` EXTRA_SIGNALS: earnings momentum (streak, acceleration), dividend raise, valuation
  (trailing P/E from results history: ttm = YTD EPS + last full year - YTD a year earlier; `research.valuation`, no
  P/E within 2 years (520 sessions) of a split/bonus because announced EPS mixes share counts, e.g. SYS 2025 split),
  sector flows (`flows.py`: weekly foreign + mutual fund net/gross by sector from scstrade, back to 2019).
- Result: ONLY `earn_accel` (EPS growth >= +20% and >= 30 points faster than last quarter, previous >= 0; +3.0% vs
  similar over 60 sessions, +2.3%/+3.5% in the two halves) is new and live. Sector flows: no edge. Cheap P/E worked only
  since 2023 (regime) -> rejected. Expensive+worsening failed after the split guard -> auto switched off, live=False.
- Live DB now keeps 800 days (one-time backfill in `update_live`); the valuation rule refuses to run with < 2 years.

## Phase 5 (built 2026-10-16): portfolio test, data fixes, analyst expectations
- `portfolio.py` (run inside `python -m pipeline backtest`, card `portfolio_card`): N equal slots, buy next open,
  full cost at entry, hold rule period, idle cash earns 0. Result 2018-2026: 10 slots +18.1%/yr, max fall -35.7%;
  KSE-100 (price, no dividends) +17.4%, equal-weight liquid +12.5%. Stop-loss -15% HURT (14.8%). 2025 lagged badly
  (+5.9% vs KSE +48.8%: large-cap rally). Honest framing: a way to pick mid/small caps, not a market replacement.
- `prices.adjust` fixes: (1) the source 'change' field can be wrong for weeks (STPL 2018: ref ~9 vs close ~17):
  a reference price is only trusted if the close is inside the price band around it; (2) splits happen across
  trading suspensions (SYS Jun 2025): across a gap, factors are accepted for liquid stocks (avg value >= Rs 5m).
  Portfolio entry also skips impossible overnight jumps. After fixes only 2 suspicious moves in liquid stocks.
- `expectations.py`: parses results previews ("EFERT is expected to post EPS of PKR6.58 in 3QCY26") from all news
  (+ Trade Chronicle feed + Google query), stores `state.data["expect"]` (180 days); 1Q/2Q/3Q -> 1Q27 style labels,
  4Q -> FY/CY label compared with the quarter EPS. Shown as "beat/missed expected EPS x (source) by y%" in the
  intraday results post and the Results Tracker highlights. Coverage is thin (about 1-2 previews a week).

## Phase 6 (built 2026-10-16): valuation, market health, payouts, sector results, MPC poll
- scstrade `bm_bc_exp` (book_closures 'date') is the EX-DATE ("xDate" on the page), not the book closure start:
  labels everywhere now say "ex-date (buy before this date to receive it)".
- `research.sector_pe/save_sector_history/sector_table`: weekly median trailing P/E per sector of liquid stocks
  (>= 3 stocks), history saved by the Saturday re-test to `state/sector_pe.csv` (cached + backup).
- Website: P/E column in the screener, P/E line on company pages (or why not: loss / split-bonus in 2 years),
  new sectors.html (P/E now vs usual since 2018, "cheaper than x% of weeks").
- Sunday album (`swing.weekly_extras`, each card independent, failures -> admin note): Market health (share of liquid
  stocks above 200-day average, 1-year line, 52-week highs/lows this week), Payouts coming up (ex-dates next 15
  days, liquid first), Results by sector (only if >= 8 results that week), Sector valuations (first Sunday of month).
  Week return everywhere = this week's close vs last week's close (adjusted).
- `watchers.mpc_poll` (runner, every run): anonymous channel poll 2 days before each MPC (>= 10:00, max 3 tries);
  after the decision closes it via stopPoll (waits for the new rate, or the evening after if unchanged) and posts
  how the community called it. `Sender.send_poll/stop_poll` (InputPollOption format).

## Data sources that DON'T work (don't retry)
dps.psx.com.pk/announcements POST (403), SECP & IMF sites (403), APP feeds (403 from GitHub), Profit/Mettis RSS (404),
Yahoo `^KSE` (no data). Gold per tola: sources disagree — intentionally not shown.

## Dev gotchas
- Windows + Git Bash: long heredocs with Python often fail ("unexpected EOF") → write a script to the scratchpad and run it.
- Test locally with `--dry-run`; delete `state/state.json` + `out/` before committing (gitignored anyway).
- Before pushing: `git pull --rebase origin main` (keepalive may commit). From 2026-10-16 Umer asked for "no AI traces":
  no Co-Authored-By trailer in commits, nothing AI-sounding in outputs.
- Verify on GitHub via public API: `https://api.github.com/repos/4umr/psx-news-pipeline/actions/runs` (+ `/jobs`); view step logs in Chrome.
- Requirements pinned with `~=`; Actions use checkout@v4 / setup-python@v5 / cache@v4 (Node 20 warning is harmless).

## User preferences (important)
- **Free only** (no paid APIs/LLMs). Telegram (WhatsApp API is paid/ToS-risky).
- Wants: instant/fast news, everything shareable to WhatsApp, image cards, clean friendly design, **no hashtags**,
  visible links, disclaimer "info only, not investment advice", proactive ideas that give an informational edge.
- Wants things to run unattended and robustly; prefers I do the work (incl. via his Chrome) over long instructions.

## Open items (as of 2026-10-16)
1. **Admin channel id**: CONFIRMED SET in GitHub secrets (2026-10-16 live run). Earlier note: Umer had to create a private Telegram channel, add the bot as admin, run `chats`, and put the id
   (starts -100) into the `TELEGRAM_ADMIN_CHAT_ID` secret, then run `test --admin-only`. Not confirmed done: until then the raw
   headline feed, "Unconfirmed" items, health warnings and the 23:45 report have nowhere to go (check the Telegram channel/logs).
2. **cron-job.org** 2-minute trigger (POST `https://api.github.com/repos/4umr/psx-news-pipeline/actions/workflows/pipeline.yml/dispatches`,
   headers Accept `application/vnd.github+json`, Authorization `Bearer <fine-grained PAT, Actions RW, this repo only>`,
   X-GitHub-Api-Version `2022-11-28`, body `{"ref":"main"}`) -> faster news + smoother intraday charts. Not done.
3. Waiting on Umer: channel invite link (`brand.channel_link`), **watchlist** stocks (5-10 symbols + alert % in `watchlist:`),
   real logo as transparent PNG -> `assets/logo.png`.
4. Gemini key was once pasted in chat (exposed); Umer said he added a key to GitHub: remind him to confirm it is a NEW key
   (old one deleted at aistudio.google.com/apikey). Never use/enter keys myself (rule); never echo secrets.
5. Watch the first days of live AI posts: read GitHub run logs (kept off main channel / "AI summary failed the number check"
   / "Unconfirmed" lines) and Telegram output; tune `impact.py`, `analysis.py` prompts, `config.yaml` (topics, exclude_titles).
6. Ideas not built: QR/join link on cards, weekly poll before MPC, web archive page (GitHub Pages), Urdu summaries,
   Sunday "Learn" explainer card, dividend/book-closure tracker, cron-job.org.
7. Rupee 1-month move tile on the macro dashboard appears after ~20 days of recorded SBP interbank rates (`hist.usdpkr`).
8. Swing Phase 3 ideas (not built): director-dealing backtest once `events` has ~6 months, GitHub Pages screener,
   intraday early movers (needs cron-job.org), dividend calendar, KSE-100 recomposition preview. Post education card?
   Check the first live swing runs (`swing.yml`), Sunday public cards and the admin chat output.

## Phase 7 + live verification (2026-10-16)
- GitHub Pages ENABLED (Source: GitHub Actions) by me in Umer's Chrome with his OK. Site live:
  https://4umr.github.io/psx-news-pipeline/ (deployed by swing.yml `deploy` job; `if: always()` so a later
  failing step can't block it).
- First live swing runs exposed workflow bugs, fixed: backup `ls` of not-yet-existing files killed the step (bash -e);
  Telegram curl steps failed with exit 3 (bot token secret likely has trailing whitespace): secrets are trimmed with
  `tr -d '[:space:]'` and curl ends `|| true`; caches now use whole folders (`state`, `data`) because changing
  the path list changes the cache version (silent fresh start). Backup tar = all of state/ except state.json/.bak.
- CONFIRMED 2026-11-12 with `python -m pipeline whereis` (lab.yml menu; prints chat type/name, never full ids, and
  sends a test note): bot @Newsfunnel_bot; TELEGRAM_CHAT_ID = channel "News Feed"; TELEGRAM_ADMIN_CHAT_ID = Umer's
  private chat with the bot (he sees it there); TELEGRAM_RESEARCH_CHAT_ID not set (lab cards fall back to admin).
- Company pages: `site._quick_check` (profit trend last 4 results, profitable years, dividend years, P/E vs sector
  and own usual P/E from `state/stock_pe.csv` (Saturday re-test, 5-year weekly median), 200-day trend, 12-month high,
  liquidity, PSX defaulter flag from "(Defaulter)" in the company name). No EPS growth line on purpose: bonus
  issues make multi-year EPS not comparable.
- Private radar `swing.plan_check`: slots in use vs `swing.plan_slots` (10), new ideas vs free slots, holding
  periods ending within 5 sessions, results due for open ideas (board meetings).

## Stock of the Day (built 2026-10-17) — PUBLIC, every day 18:00 PKT
- Umer's decisions: read annual reports (Gemini), every day, 18:00, straight to the main channel.
- Workflow `.github/workflows/stock.yml` (18:00 + 20:00 catch-up; manual: sotd / sotd --preview + symbol). Restores the
  swing cache read-only (prices etc.) and its own cache `psx-sotd-v1-` (path state/sotd) + plain backup branch `sotd-data`.
  Builds + deploys the website too; swing.yml also restores state/sotd before its site build and removes it before saving.
- `sotd.py`: queue = KMI-30 first then KSE-100 (`psx.index_members`, XD/XB suffixes stripped), no repeats per round,
  largest first, no same sector twice in a row, results in last 3 days first, results due within 2 days later.
  18:00 run waits if the annual report could not be read (admin note); the 20:00 run publishes with PSX numbers only.
  `_result_lines`: ALL financial figures from the PSX company page (scstrade EPS can be consolidated while PSX is
  standalone: LUCK FY26 60.78 vs 31.83). Latest period chosen with the company's year-end month (June FY: "Q3 2026" is
  older than "2026"). Multi-year charts use profit, not EPS (bonus/split). `_prune`: pages 2+ deleted after 30 days (PDF kept).
- `psx.profile()`: description, people, address, website, registrar, auditor, year end, annual/quarterly tables
  (Sales or Mark-up/Total Income for banks, Profit after Taxation, EPS; Rs thousands), ratios, shares, free float.
- `sources/reports.py`: financials.psx.com.pk `annQtrStmts.php` (name=get_comp_data, smbCode) lists every annual and
  quarterly report since ~2013; PDF via lib/DownloadPDF.php?id=.
- `reader.py`: picks ~30 relevant pages (profile, chairman/directors' report, plants, subsidiaries, segments, outlook,
  balance sheet; scoring by keywords, number-heavy pages penalised), one Gemini call (timeout 240s), JSON with page
  numbers; every sentence's numbers AND years must appear in the pages sent (`numbers_ok` + `_years_ok`), advice dropped.
  Cached per report id in state/sotd/reports. No local Gemini key: test with `sotd --preview` on GitHub (the run log
  prints "ANNUAL REPORT SUMMARY" JSON; preview goes to admin only and never to the website).
- Logos: `assets/logos/SYM.png` = 58 logos reviewed by eye (2026-10-17); automatic picks were wrong for some
  (product brands, award badges, group logos), so only reviewed files are used; others get drawn initials.
  `logo.py` fetcher is a review helper (`_LOOSE` flag), not used at publish time.
- Format (Umer, 2026-10-17: "brokerage house type, human made, professional", then "3 pages, clear, not merged"):
  `note.py` = sell-side style research note, A4 pages at 150 dpi (1240x1754), DejaVu Sans (ships with matplotlib;
  runners have no Arial). Posted as an album of page images + the same note as a PDF (`Sender.send_document`),
  NO links in the post, NO rating/target. Page 1: header band, title + factual headline (FY profit change, from PSX)
  + subhead, sidebar (key data, price performance vs KSE-100, 12-month chart, management), main (overview ending on
  a whole sentence, business mix, latest results, outlook). Page 2: financial summary, recent quarters, sales and
  profit bar charts, track record, balance sheet (only clean figures: `_figure`), coming up. Page 3: operations
  (generic "Pakistan" row dropped), capacity, group companies, milestones, outlook continued, risks, disclosures.
  No annual report read -> 2 pages, disclosures on page 2. Files: published/SYM-1..3.png + SYM.pdf + SYM.json.
  Old 7-card `cards.sotd_cards` is no longer used. Reader prompt VERSION 2 (short items, history milestones,
  standalone balance sheet figures); cache file name includes the version.
  Website: r/SYM.html (all sections with page refs + page images + PDF download) and stock-of-the-day.html archive.
