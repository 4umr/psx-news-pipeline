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
1. **Admin channel id**: Umer must create a private Telegram channel, add the bot as admin, run `chats`, and put the id
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
8. Swing Phase 2+ (not built): public data-only "Small & Mid Cap Radar", pump-risk flags, filings for all liquid stocks,
   director dealings (forward-tracked only: PSX pages keep ~5 recent filings), GitHub Pages screener, intraday early movers
   (needs cron-job.org). Check the first live swing runs (`swing.yml`) and the admin chat output.
