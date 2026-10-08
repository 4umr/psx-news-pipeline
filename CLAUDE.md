# PSX News Pipeline — project guide (read me first)

Owner: **Umer**, Financial Analyst, runs the Telegram channel/community **"PAKISTAN INVESTORS"** (PSX investors).
Not a developer — explain in plain language, give click-by-click steps, do the technical work yourself.

## What this is
Free, fully automatic pipeline that posts PSX-moving news, official data and market briefs to a Telegram
channel 24/7. Runs on **GitHub Actions** (public repo `4umr/psx-news-pipeline`, branch `main`), not on the laptop.
- Workflow `.github/workflows/pipeline.yml`: cron every 5 min (GitHub actually runs ~every 15–20 min) +
  manual "Run workflow" with a **What to do** menu: run · test --admin-only · test · brief morning/midday/close/week_ahead · about · chats.
- `.github/workflows/keepalive.yml`: monthly commit so GitHub never pauses the schedule.
- State (what was sent, snapshots, history) = `state/state.json`, kept in the **Actions cache** (key `psx-state-v2-*`)
  with a backup copy force-pushed to the orphan branch **`state`** (~every 15 min); auto-restored if cache is lost.
- Secrets (GitHub → Settings → Secrets → Actions): `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (channel `-1004482191611`),
  `TELEGRAM_ADMIN_CHAT_ID` (Umer's *personal* chat id — NOT the bot id 8714104355; bot can't message itself).
  **Never** print/echo secrets or personal ids; Actions logs are public. Don't ask the user to paste tokens in chat.

## Code map (`pipeline/`)
- `__main__.py` CLI: `python -m pipeline run|loop|brief <name>|test [--admin-only]|chats|about [--dry-run]`.
  `--dry-run` writes messages to `out/preview.txt` and cards to `out/*.png` (no Telegram).
- `runner.py` one pass: collect → watchers → news scoring → send. Every component wrapped in `_safe()`; state always
  saved in `finally`; component-error / source-health alerts to admin after 3 consecutive failures; daily 23:45 report.
  News: `news_mode: instant` → each headline posted alone (compact), score≥8 = full post with **image card + caption**.
  Data alerts priority≥9 also get an image card (except list alerts in `NO_CARD`). WhatsApp-ready copies → admin chat.
- `watchers.py` SBP changes (policy rate, reserves, T-bill/PIB cut-offs, with sanity checks), corporate results (+FY EPS YoY
  from PSX), FIPI/LIPI (+by sector), KSE-100 moves (+intraday path recording), big stock moves & watchlist, PBS CPI/SPI
  (real numbers from the .docx/post), MPC calendar & reminders, PSX company filings (material info, insider dealing),
  unusual volume (needs ≥5 sessions of history), global moves.
- `briefs.py` text + card albums: morning 08:45, **midday 12:30**, close 17:15 (holiday detection), week_ahead Sun 19:00.
- `cards.py` matplotlib cards (`Card` class, px coords): alert_card (1080²), morning_cards/midday_cards/close_cards (2 each, 1080×1350).
  Colors: up blue `#2a78d6`, down red `#e34948`, band `#0d366b`; no emoji in images (font can't draw them); `text.parse_math=False`.
- `style.py` header/section/footer (one-line signature, **no hashtags**), `to_whatsapp()`.
- `links.py` visible links: decode Google News → publisher URL, short forms (dawn/brecorder/tribune/thenews). `common.link()`
  always renders **plain visible URLs** (hidden <a> links break when copied to WhatsApp).
- `street.py` Street View: captures brokerage forecasts (Topline, AHL, JS Global, AKD…) from news, scores vs actual CPI /
  SBP decisions → accuracy scoreboard (Sunday post). Never invent analyst track records.
- `scoring.py` keyword topics (config.yaml `topics`), dedupe (fuzzy + same-topic).
- `sources/`: `news.py` (RSS + Google News), `sbp.py` (homepage indicators, press releases, MPC calendar), `scs.py`
  (scstrade.com JSON endpoints: results, board meetings, book closures, KSE-100 view, indices, daily activity, FIPI, index
  history, 52-wk), `psx.py` (dps.psx.com.pk/company/SYMBOL filings + EPS), `pbs.py`, `forex.py` (forex.pk open market), `markets.py` (yfinance).
- `config.yaml` everything tunable: brand (name/author/title/channel_link/cards/alert_cards/whatsapp_copy), briefs times,
  thresholds, feeds, Google queries, topics + topic_meta (sectors), watchlist (empty), commodities/regional.

## Data sources that DON'T work (don't retry)
dps.psx.com.pk/announcements POST (403), SECP & IMF sites (403), APP feeds (403 from GitHub), Profit/Mettis RSS (404),
Yahoo `^KSE` (no data). Gold per tola: sources disagree — intentionally not shown.

## Dev gotchas
- Windows + Git Bash: long heredocs with Python often fail ("unexpected EOF") → write a script to the scratchpad and run it.
- Test locally with `--dry-run`; delete `state/state.json` + `out/` before committing (gitignored anyway).
- Before pushing: `git pull --rebase origin main` (keepalive may commit). Commit trailer: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Verify on GitHub via public API: `https://api.github.com/repos/4umr/psx-news-pipeline/actions/runs` (+ `/jobs`); view step logs in Chrome.
- Requirements pinned with `~=`; Actions use checkout@v4 / setup-python@v5 / cache@v4 (Node 20 warning is harmless).

## User preferences (important)
- **Free only** (no paid APIs/LLMs). Telegram (WhatsApp API is paid/ToS-risky).
- Wants: instant/fast news, everything shareable to WhatsApp, image cards, clean friendly design, **no hashtags**,
  visible links, disclaimer "info only, not investment advice", proactive ideas that give an informational edge.
- Wants things to run unattended and robustly; prefers I do the work (incl. via his Chrome) over long instructions.

## Open items (as of 2026-10-09)
1. Umer to put his **personal** chat id into `TELEGRAM_ADMIN_CHAT_ID` (bot already DM'd it to him via `chats`), then run `test --admin-only`.
2. Umer to set up **cron-job.org** 2-minute trigger (POST `https://api.github.com/repos/4umr/psx-news-pipeline/actions/workflows/pipeline.yml/dispatches`,
   headers Accept `application/vnd.github+json`, Authorization `Bearer <fine-grained PAT, Actions RW, this repo only>`,
   X-GitHub-Api-Version `2022-11-28`, body `{"ref":"main"}`) → faster news + smoother intraday charts.
3. Waiting on: channel invite link (`brand.channel_link`), watchlist stocks (`watchlist:`).
4. Verify first real midday/closing albums arrived (logs show "album of N cards sent" / "image card ... sent").
5. Ideas offered, not built: monthly macro card on CPI day, results-season tracker, weekly poll, web archive page, Urdu summaries.
