# PSX News Pipeline 📈

This pipeline sends PSX-moving news and indicators to a Telegram channel automatically, 24/7, using only free services.

## What you get

| Type | What | When |
|---|---|---|
| 🚨 Instant alerts | SBP policy rate change, **T-bill / PIB auction cut-offs** (with bps change), **SBP reserves** (week-on-week change), CPI/SPI releases, IMF, petrol/power prices, budget/tax, sovereign ratings, military escalation, sharp rupee moves | Within minutes of publication |
| 📉 Market alerts | KSE-100 intraday moves of ±1.5% / 3% / 5%, with the stocks driving it; big moves in oil, gold, DXY, S&P 500, US yields | During market hours / as they happen |
| 📊 Corporate | New results: EPS, dividend, bonus, right shares, with the PSX filing PDF. ⭐ marks KSE-100 companies | Every run |
| 🌍 Flows | FIPI / LIPI (foreign and local investors) by investor type | Evening, once published |
| 📰 News wrap | Scored and de-duplicated headlines with links, grouped by topic (max 3 per topic) | Every 30 min when there's news; held 23:30–07:30 |
| ☀️ Morning brief | 🖼️ Image card + text: at-a-glance, last close, FIPI, SBP rates (policy, KIBOR, T-bills, PIBs, reserves, USD/PKR), global markets, today's board meetings and book closures, top headlines | 08:45 Mon–Fri |
| 🔔 Closing wrap | 🖼️ Image card + text: indices, index-point contributors chart, top gainers and losers, rates, global, tomorrow's calendar, key headlines | 17:15 Mon–Fri |
| 📅 Week ahead | 🖼️ Image card + text: weekly KSE-100 change, next week's board meetings and payouts, auctions, regular data calendar | Sunday 19:00 |

Every message carries the **PAKISTAN INVESTORS** branding, hashtags (searchable in Telegram) and the disclaimer, so it can be forwarded as is. Set `brand.channel_link` in `config.yaml` to add a "Join" link.

### Sources (all free and public)
- **State Bank of Pakistan** (sbp.org.pk): policy rate, corridor, KIBOR, MTB/PIB cut-offs, reserves, USD/PKR, upcoming auctions, press releases, circulars
- **Pakistan Bureau of Statistics** (RSS): CPI inflation, weekly SPI, trade, LSM
- **SCS Trade** (scstrade.com): results and payouts, board meetings, book closures, KSE-100 constituents and point contributions, index OHLC, FIPI/LIPI
- **News RSS**: Business Recorder, Dawn, Express Tribune, The News, ProPakistani, Arab News PK
- **Google News** (14 targeted searches): picks up Reuters, Bloomberg, Mettis, Profit, Geo, ARY and others
- **Yahoo Finance**: Brent, WTI, gold, DXY, S&P 500, US 10Y, VIX, USD/CNY, Bitcoin

Numbers always come straight from the source; nothing is generated or estimated. Every news item links to its original article.

---

## Setup (about 15 minutes, one time)

### Step 1 — Create the Telegram bot
1. In Telegram, open **@BotFather** and send `/newbot`.
2. Pick a name (e.g. *PSX Market Alerts*) and a username ending in `bot`.
3. BotFather replies with a **token** like `123456789:AAH...`. Keep it secret.

### Step 2 — Create the channel
1. Telegram → New Channel (e.g. *PSX Market Updates*). Public or private both work.
2. Channel → Administrators → Add Admin → search for your bot → allow **Post Messages**.
3. Your **chat id** is:
   - **Public channel:** `@yourchannelusername`
   - **Private channel:** post any message in the channel, then run `python -m pipeline chats` (see Step 4). It prints an id like `-1001234567890`.

To also get messages in your private chat, message your bot once, run `python -m pipeline chats`, and add your personal id after a comma: `@yourchannel,123456789`.

### Step 3 — Put it on GitHub (free, runs 24/7 in the cloud)
1. Create a free account at github.com.
2. Create a **new public repository** (e.g. `psx-news-pipeline`). It must be public: public repos get unlimited free run minutes. Your token stays secret in Step 4.
3. Upload all files in this folder **except** `.env`, `state/` and `out/`.
4. In the repo, go to **Settings → Secrets and variables → Actions → New repository secret** and add:
   - `TELEGRAM_BOT_TOKEN` = the token from Step 1
   - `TELEGRAM_CHAT_ID` = the id from Step 2
5. Open the **Actions** tab → enable workflows → **PSX News Pipeline** → **Run workflow**.

The first run posts a welcome message and a market snapshot. After that it runs every ~5 minutes on its own.

### Step 4 — (optional) Test on your PC first
```bash
pip install -r requirements.txt
copy .env.example .env        # then edit .env with your token and chat id
python -m pipeline test       # sends a test message
python -m pipeline run        # one full pass
```

---

## Optional extras
- **Private health alerts:** add a repository secret `TELEGRAM_ADMIN_CHAT_ID` containing your *personal* chat id. First message your bot once, then run `python -m pipeline chats` to find your id. If a source (SBP, PSX data, news, global markets) fails 3 runs in a row, you get a private warning, and another message when it recovers. The channel never sees these.
- **Watchlist:** in `config.yaml`, add symbols under `watchlist:` with a % threshold (e.g. `OGDC: 4`). You'll get an alert when they move that much intraday. Any KSE-100 stock moving 7.5% or more is always alerted.
- **Pinned intro post:** go to Actions → Run workflow → choose `about`, then pin that message in your channel.

## Commands
| Command | What it does |
|---|---|
| `python -m pipeline run` | One pass (what GitHub runs every 5 min) |
| `python -m pipeline run --dry-run` | Prints messages instead of sending them (also saved to `out/preview.txt`) |
| `python -m pipeline brief morning` | Sends a brief now (`morning`, `close`, `week_ahead`) |
| `python -m pipeline loop --every 120` | Runs continuously every 2 min (for an always-on PC or server) |
| `python -m pipeline test` | Sends a Telegram test message |
| `python -m pipeline chats` | Lists the channel and chat ids your bot can see |

## Customising (config.yaml)
- **Brief times:** `briefs:`
- **Alert sensitivity:** `kse100_move_alerts_pct`, and the `%` thresholds in `global_markets`
- **What counts as important:** `topics:`. Each topic has keywords and a score. 8+ is sent instantly, 4–7 goes into the digest, and anything lower is ignored.
- **Add a news site:** add a line under `feeds:` (any RSS URL)
- **Add a search:** add a line under `google_news:`
- **Disclaimer text:** `disclaimer:`

## Built to run unattended
Everything runs on GitHub's servers, so your laptop or phone can be off for days.
- **Isolated components:** if one source or feature breaks, only that part is skipped. You get a private warning after 3 failures in a row, and a "Recovered" note when it's fixed.
- **Progress always saved:** even on a crash, so nothing is posted twice.
- **Two copies of memory:** the GitHub cache, plus a backup in the `state` branch that's restored automatically if the cache is lost.
- **Retries:** messages that fail to send are retried for up to 6 hours.
- **Bad-data guard:** impossible numbers are never posted to the channel; you're told privately instead.
- **Pinned library versions:** an update elsewhere can't silently break the code.
- **Failure alert:** if a whole run fails (e.g. a GitHub outage), you get a private alert, at most every 3 hours.
- **Daily health report** in your private chat at 23:45 PKT: number of runs, messages sent and source health.
- **Never paused:** the regular state backups and the monthly keepalive stop GitHub pausing the schedule after 60 days.

## Speed and reliability notes
- GitHub's 5-minute schedule usually runs every 5–15 minutes; at busy times it can be later. For alerts within 1–2 minutes, run `python -m pipeline loop --every 90` on any always-on machine (an old laptop, or a free Oracle Cloud "Always Free" VM).
- The first run records what already exists and does **not** flood the channel with old news.
- If a source is down, the run continues with the others and that source is retried next run.
- If a source website changes its layout, that section will show "unavailable" and the rest keeps working.
