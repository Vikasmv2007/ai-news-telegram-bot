# 🤖 AI News Intelligence Telegram Bot

[![tests](https://github.com/Vikasmv2007/ai-news-telegram-bot/actions/workflows/tests.yml/badge.svg)](https://github.com/Vikasmv2007/ai-news-telegram-bot/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)

> Open-source, free-to-run AI news assistant for Telegram: Indian & global
> stock markets, technology & AI, India & world news, summarised and explained
> every day. Fork it, add your own free API keys, and get your own briefings.

Your personal news analyst on Telegram. It collects news from trusted sources,
throws away duplicates and junk, uses AI to explain **what happened, why it
matters and how markets might be affected**, and sends you clean briefings on
a schedule — automatically, every day.

**Everything can run for ₹0.** Telegram, Gemini/Groq AI, RSS news and the market
data source are all free. Claude (Anthropic) is supported but optional and paid.

---

## Contents
1. [What you get](#1-what-you-get)
2. [Cost: what's free, what's paid](#2-cost-whats-free-whats-paid)
3. [Quick start on Windows (step by step)](#3-quick-start-on-windows-step-by-step)
4. [Keep it running 24/7](#4-keep-it-running-247)
5. [Telegram commands](#5-telegram-commands)
6. [Configuration reference](#6-configuration-reference)
7. [How it works](#7-how-it-works)
8. [Testing](#8-testing)
9. [Troubleshooting](#9-troubleshooting)
10. [Limitations (honest list)](#10-limitations-honest-list)
11. [Contributing](#11-contributing)
12. [License](#12-license)

### ⚡ Fastest way to get your own bot (no PC or server, free)
1. Create a Telegram bot with **@BotFather** and get a free **Gemini** key (details in [section 3](#3-quick-start-on-windows-step-by-step)).
2. **Fork** this repository.
3. Add your keys as repository secrets and turn on `ENABLE_BRIEFINGS` (see [Option D](#option-d--github-actions-no-server-mode-free-no-card)).
4. Done: briefings arrive in your Telegram at 7:00, 9:00, 13:00, 16:00 and 20:00 IST.

---

## 1. What you get

| Time (IST) | Briefing | Contents |
|---|---|---|
| 07:00 | 🌅 Good Morning | All 4 categories + overnight global markets snapshot |
| 09:00 | 🔔 Pre-market | Indian & global market news (weekdays) |
| 13:00 | ⏱️ Midday | Only important new market developments — **skipped if nothing important** (weekdays) |
| 16:00 | 🔔 Closing summary | Indian market news + NIFTY / SENSEX / BANK NIFTY / sector snapshot (weekdays) |
| 20:00 | 🌙 Evening digest | Tech & AI, India & World, global business |
| any time | 🚨 Breaking alert | Only for importance ≥ 9/10, max 4/day, never during quiet hours (23:00–06:30) |

Every story shows: simple headline · 3–5 sentence summary (facts only) ·
*why it matters* (analysis) · who's affected · India / global market impact
(analysis, with uncertainty) · sentiment (positive / negative / mixed /
uncertain) · importance 1–10 (HIGH / MEDIUM / LOW) · source, time and a
**🔗 Read Full Article** button.

Categories: 📈 Indian Stock Market · 🌍 Global Markets · 🤖 Technology & AI · 🇮🇳 India & World.

---

## 2. Cost: what's free, what's paid

| Service | Used for | Cost | Key needed |
|---|---|---|---|
| Telegram Bot API | sending messages | **Free** | Bot token from @BotFather |
| Google Gemini API | AI summaries (default) | **Free tier** (limited requests/day) | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |
| Groq API | AI backup if Gemini is busy | **Free tier** | [console.groq.com/keys](https://console.groq.com/keys) |
| Anthropic Claude API | AI (optional) | **Paid** — needs prepaid credits | console.anthropic.com |
| RSS feeds (ET, Mint, Moneycontrol, BBC, CNBC, The Hindu, TechCrunch, …) | news | **Free** | none |
| RBI & SEBI RSS | official announcements | **Free** | none |
| Google News RSS | aggregation (incl. Reuters) | **Free** | none |
| Yahoo Finance chart data | index/stock prices | **Free**, unofficial, **delayed** | none |
| SQLite | database | **Free** (a file) | none |

**No AI key at all?** The bot still works: it sends headlines plus the
publisher's own summary text, clearly marked *"AI analysis unavailable"*.

**Free-tier budget.** With the defaults (refresh every 30 min, at most 24
articles per refresh, 8 per AI request) the bot makes about 150 AI requests a
day. Free limits change over time and differ per account. If Gemini says
"quota exceeded", the bot switches to Groq automatically (if you set a Groq
key), and otherwise uses feed text until the quota resets.

> Note: Google may use free-tier Gemini prompts to improve its products. The
> bot only sends public news headlines and descriptions, nothing personal.

---

## 3. Quick start on Windows (step by step)

### 3.1 Install the tools (once)
1. **Python 3.11 or newer** from [python.org/downloads](https://www.python.org/downloads/).
   During setup tick **"Add python.exe to PATH"**.
2. **VS Code** (you already have it).
3. Put this `news-intelligence-bot` folder somewhere simple, e.g. `C:\Bots\news-intelligence-bot`.

### 3.2 Install the project
Open the folder in VS Code → **Terminal → New Terminal**, then:

```bash
scripts\setup_windows.bat
```

This creates a virtual environment in `.venv`, installs everything, and creates your `.env` file.

(Manual alternative: `py -m venv .venv`, then `.venv\Scripts\activate`, then `pip install -r requirements-dev.txt`, then `copy .env.example .env`.)

### 3.3 Try it right now, without any keys
```bash
.venv\Scripts\python -m app.main --preview morning --test-mode
```
This prints a sample morning briefing in the terminal. All test data is marked `[SAMPLE]` and is not real news.

### 3.4 Create your Telegram bot (free, 2 minutes)
1. In Telegram, search **@BotFather** → send `/newbot` → choose a name and a username ending in `bot`.
2. BotFather replies with a token like `123456789:AAH...`. Open `.env` in VS Code and paste it:
   `TELEGRAM_BOT_TOKEN=123456789:AAH...`
3. Keep this token secret. Anyone who has it can control your bot.

### 3.5 Get a free AI key
1. Go to [aistudio.google.com/apikey](https://aistudio.google.com/apikey), sign in with Google, and click **Create API key**.
2. In `.env`, set `GEMINI_API_KEY=...`
3. Optional backup: create a free key at [console.groq.com/keys](https://console.groq.com/keys) and set `GROQ_API_KEY=...`

Check that it works:
```bash
.venv\Scripts\python -m app.main --check-ai
```

### 3.6 Find your chat ID and lock the bot to you
1. Start the bot:
   ```bash
   .venv\Scripts\python -m app.main
   ```
2. Open your bot in Telegram and send `/start`. It replies: *"This is a private bot. Your chat ID is 987654321"*.
3. Stop the bot (Ctrl+C). In `.env`, set `ALLOWED_CHAT_IDS=987654321`
4. Start it again and send `/start`. You now get the welcome message. Try `/now`.

### 3.7 Check everything
```bash
.venv\Scripts\python -m app.main --check-config
```
```bash
.venv\Scripts\python -m app.main --check-feeds
```
`--check-feeds` lists every news feed as OK or FAIL. A failing feed is
skipped automatically. You can fix or remove it in `config/sources.yaml`.

```bash
.venv\Scripts\python -m app.main --preview morning
```
This shows a real morning briefing in the terminal, built from live news and your AI key.

---

## 4. Keep it running 24/7

The bot must be running to send scheduled messages. Pick **one** option.

> ⚠️ Run only **one** copy of the bot. Two copies with the same token fight
> over messages ("Conflict" error).

### Option A — Your Windows PC (free; works while the PC is on)
1. Double-click `scripts\run_bot.bat`. It starts the bot and restarts it if it ever stops.
2. To start it automatically when you log in, open **Task Scheduler** → **Create Basic Task**:
   - Trigger: **When I log on**
   - Action: **Start a program** → `C:\Bots\news-intelligence-bot\scripts\run_bot.bat`
   - After saving, open the task's **Properties**, untick *"Stop the task if it runs longer than 3 days"*, and tick *"Run whether user is logged on or not"* if you want.
3. Stop the PC from sleeping: **Settings → System → Power → Sleep: Never** (when plugged in).

If the PC was off at 07:00 and you turn it on by 07:45, the missed briefing is
still sent (`CATCH_UP_MINUTES=45`). Later than that, it's skipped, so you never
get stale briefings.

### Option B — Docker (any computer or server)
```bash
docker compose up -d --build
```
- `restart: unless-stopped` brings it back after crashes and reboots.
- Your database lives in `./data`, so it survives restarts and upgrades.
- View logs with `docker compose logs -f`.
- Health check: `http://127.0.0.1:8080/health` returns `{"status":"ok",...}`.
- To update after changing code or config: `docker compose up -d --build`.

### Option C — Free cloud server, 24/7 (recommended for "always on")
**Oracle Cloud "Always Free"** gives you a small Linux VM that never expires.
Signup asks for a card for identity verification; Always Free resources aren't charged.
1. Create an *Always Free* VM (Ubuntu) and connect with SSH.
2. Install and copy the project:
   ```bash
   sudo apt update && sudo apt install -y python3 python3-venv git
   ```
   Copy the folder to `/home/ubuntu/news-intelligence-bot` (with `scp`, or `git clone` from your **private** repo).
3. Set it up:
   ```bash
   cd ~/news-intelligence-bot && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
   ```
   ```bash
   cp .env.example .env && nano .env
   ```
4. Run it as a service that starts on boot and restarts on failure:
   ```bash
   sudo cp deploy/newsbot.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now newsbot
   ```
   ```bash
   journalctl -u newsbot -f
   ```
Google Cloud's free e2-micro VM works the same way. (Docker also works on these VMs: install Docker, then use Option B.)

Why not Render / Railway / Fly free plans? Free web plans that **sleep when
idle**, or have no free tier, don't suit a bot that must stay connected all
day. Free plans change often, so check before relying on one.

### Option D — GitHub Actions, "no server" mode (free, no card)
GitHub runs the 5 briefings for you on a schedule. Limits: **Telegram commands
like /now don't work** in this mode (use the Run workflow menu instead), and
GitHub can start runs 5–30 minutes late.
1. **Fork** this repository (top-right **Fork** button). A **private** copy is even better: click **Use this template → Create a new repository → Private**, if available, or push your own copy to a private repo. `.env` is git-ignored, so your secrets are never uploaded.
2. In your copy, go to **Settings → Secrets and variables → Actions → Secrets → New repository secret** and add:
   `TELEGRAM_BOT_TOKEN`, `ALLOWED_CHAT_IDS`, `GEMINI_API_KEY` (optional: `GROQ_API_KEY`).
   ⚠️ Use **Repository secrets**, not "Environment secrets".
3. On the same page, open the **Variables** tab → **New repository variable**: name `ENABLE_BRIEFINGS`, value `true`. Scheduled runs stay off until you do this, so forks don't fail every day.
4. Open the **Actions** tab → enable workflows → **news-briefings** → **Run workflow** to test.

**News on demand:** Actions → news-briefings → **Run workflow** → choose `now`, `today`, `india`, `global`, `market`, `tech`, `trending` (or a scheduled briefing, which is re-sent even if it already went out today). It arrives in Telegram 1–3 minutes later.

The workflow is in `.github/workflows/briefings.yml`. Don't use it at the same time as options A–C, or you'll get each briefing twice.
In a **public** repo, GitHub pauses scheduled workflows after 60 days without commits. Re-enable it from the Actions tab if that happens.

### Never sent twice — even after restarts
Before a briefing is sent, a row *(chat, schedule, date)* is written to the
database table `slot_runs`, which allows each combination only once. A
restart, catch-up or double trigger can't send the same briefing twice. Every
delivered article is also recorded, so the same story never reaches you again
in a later scheduled briefing.

---

## 5. Telegram commands

| Command | What it does |
|---|---|
| `/start` | Welcome + features (also shows your chat ID if you're not authorised) |
| `/help` | Help and your schedule |
| `/now` | Fetches fresh news and sends stories you haven't seen yet |
| `/today` | Today's complete briefing + full market snapshot |
| `/market` | Indian & global market news + snapshot |
| `/india` / `/global` | Indian / international news |
| `/tech` | Technology & AI news |
| `/trending` | Most important stories (importance + how many outlets covered them) |
| `/watchlist` | Your companies' prices + related news. `/watchlist add INFY.NS WIPRO.NS`, `/watchlist remove TCS.NS` |
| `/settings` | Buttons to toggle each schedule, each category, breaking alerts, and layout (cards / compact) |
| `/stop` / `/resume` | Pause / resume automatic delivery |
| `/status` *(admin)* | Health: database, last refresh, AI provider state, failing feeds |
| `/preview morning` *(admin)* | Shows what a scheduled briefing would look like (without marking anything as sent) |
| `/test` *(admin)* | Sends one sample card to check formatting |

Watchlist symbols use Yahoo Finance format: NSE = `RELIANCE.NS`, BSE =
`500325.BO`, US = `NVDA`. Company names used for news matching are in
`config/sources.yaml → company_aliases`. Add your own there for better matching.

---

## 6. Configuration reference

All settings are in `.env` (see `.env.example`; every line is commented). The main ones:

| Setting | Default | Meaning |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | — | from @BotFather (**required**) |
| `ALLOWED_CHAT_IDS` | — | who may use the bot (comma-separated) |
| `ADMIN_CHAT_IDS` | = allowed | who may use `/status`, `/preview` |
| `LLM_PROVIDER` | `auto` | `auto`, `gemini`, `groq`, `anthropic`, `none` |
| `GEMINI_MODEL` | `gemini-flash-latest` | any Gemini model name your key can use |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | any Groq chat model |
| `ANTHROPIC_MODEL` | `claude-opus-5-5` | `claude-haiku-5-5` is the cheapest Claude option |
| `SCHEDULE_MORNING` … `SCHEDULE_EVENING` | 07:00 … 20:00 | delivery times (HH:MM) |
| `SCHEDULE_<NAME>_ENABLED` | `true` | switch a schedule off for everyone |
| `TIMEZONE` | `Asia/Kolkata` | any IANA timezone |
| `COLLECT_INTERVAL_MINUTES` | 30 | how often news is refreshed |
| `ITEMS_PER_CATEGORY` | 4 | stories per category per briefing |
| `BREAKING_MIN_IMPORTANCE` / `BREAKING_MAX_PER_DAY` | 9 / 4 | breaking alert threshold / limit |
| `QUIET_HOURS` | `23:00-06:30` | no breaking alerts in this window |
| `MARKET_DATA_ENABLED` | `true` | show index/price snapshots |
| `DATABASE_URL` | SQLite `data/news.db` | e.g. `postgresql+psycopg://user:pw@host/db` (also `pip install "psycopg[binary]"`) |
| `TEST_MODE` | `false` | sample news + fake AI (also `--test-mode`) |
| `HEALTH_PORT` | 0 (off) | port for `GET /health` |
| `LOG_FORMAT` | `text` | `json` for structured logs |

**News sources** are in `config/sources.yaml`. Add any RSS feed, or a Google
News search (`google_news_queries`), and give it a category.

---

## 7. How it works

```
 RSS feeds ─┐                              ┌─> Gemini (free) ─┐
 RBI/SEBI  ─┼─> collect ─> filter ─> dedupe ─> store ─> AI ──┼─> Groq (free)  ─┼─> validate ─> DB
 Google News┘   (async,     (junk,    (URL,     (SQLite)     └─> Claude (paid) ─┘   (safety)
                one failure  tips,     title,
                never stops  promos)   similarity)
                the rest)
                                                      Scheduler (IST) ──> briefing ──> Telegram
                                                      Yahoo Finance ──> market snapshot ┘
```

```
news-intelligence-bot/
├── app/
│   ├── main.py                 CLI entry point (run / preview / checks / one-shot)
│   ├── config.py               settings from .env, schedules, categories
│   ├── bot/
│   │   ├── telegram_bot.py     builds the Telegram app, menu, startup retry
│   │   └── commands.py         all /commands, authorisation, settings buttons
│   ├── collectors/
│   │   ├── rss_collector.py    RSS + Google News, parallel, fault-isolated
│   │   ├── official_sources.py RBI / SEBI handling, official-domain tagging
│   │   └── market_collector.py Yahoo Finance quotes (delayed, labelled)
│   ├── ai/
│   │   ├── providers.py        Gemini, Groq, Claude, fake (test) providers
│   │   ├── summarizer.py       batching, fallback chain, cool-downs
│   │   ├── analyzer.py         prompt, JSON validation, no-advice filter, no-AI fallback
│   │   └── classifier.py       rule-based junk filter, category, priority, watchlist
│   ├── services/
│   │   ├── news_service.py     the pipeline: collect → dedupe → store → AI
│   │   ├── deduplication.py    URL / fingerprint / similarity duplicate detection
│   │   ├── briefing.py         assembles scheduled & on-demand briefings
│   │   ├── formatter.py        Telegram HTML message layout
│   │   ├── scheduler.py        daily jobs, catch-up, breaking alerts, never-twice
│   │   ├── telegram_service.py delivery with retries / flood control
│   │   ├── health.py           /health endpoint
│   │   ├── container.py        wires everything together
│   │   └── sample_data.py      [SAMPLE] data for test mode
│   ├── database/
│   │   ├── models.py           tables: articles, user_preferences, deliveries, slot_runs, feed_status, kv
│   │   └── db.py               all database operations
│   └── utils/                  logger (token redaction), retry, http, text helpers
├── config/sources.yaml         feeds, Google News queries, watchlist aliases, market symbols
├── tests/                      pytest suite + fixtures
├── scripts/                    Windows setup / run scripts
├── deploy/newsbot.service      Linux systemd service
├── .github/workflows/          optional GitHub Actions "no server" mode
├── Dockerfile, docker-compose.yml, .env.example, requirements*.txt
```

**Technical decisions in simple words**
- **RSS instead of scraping.** Publishers offer RSS feeds for exactly this purpose. We read only the headline and short description they publish. No paywall bypassing, no scraping of article pages.
- **Rules before AI.** Free AI allows a limited number of requests a day. Cheap rules first drop junk ("top 5 stocks to buy", sponsored posts, horoscopes), then score every article, so only the best ~24 per refresh go to the AI. The AI budget is shared fairly across categories.
- **Batching.** 8 articles go into one AI request, which stretches the free quota about 8×.
- **Provider chain.** If Gemini hits its limit, the bot uses Groq; if both are down, it uses feed text. One failure never stops the bot.
- **The AI is checked, not trusted.** Its JSON is validated: scores are clamped to 1–10, and unknown categories or sentiments are reset to safe defaults. Any sentence that sounds like investment advice ("you should buy", "target price"…) is deleted in code. The AI also only sees the feed's text and is told not to add facts, and summary (facts) and analysis are kept in separate, labelled fields.
- **SQLite by default.** It's a single file, needs zero setup, and is plenty for a personal bot. PostgreSQL works by changing `DATABASE_URL`.
- **Long polling.** The bot asks Telegram for new messages, so you don't need a public URL, a domain or HTTPS.
- **Only the header notifies.** Briefing items are sent silently, so a briefing buzzes your phone once, not 15 times.

---

## 8. Testing

```bash
.venv\Scripts\python -m pytest -q
```
(Linux/macOS: `.venv/bin/python -m pytest -q`)

100 tests cover: RSS parsing (publisher feeds, Google News, broken and
undated feeds, one failing feed not stopping the others), classification and
junk filtering, AI prompt output validation and the no-advice filter, every AI
provider (Gemini/Groq request formats, rate-limit/auth/timeout errors, Claude
via a fake SDK client), the provider fallback chain, duplicate detection,
database operations, message formatting (Telegram's 4096-character limit, HTML
escaping), market data parsing, scheduling (never-twice, weekends,
skip-if-empty, retries, catch-up, quiet hours, breaking-alert limits),
Telegram command handling (authorisation, admin, watchlist validation,
settings buttons), Telegram delivery errors (flood control, bad HTML, blocked
users), the real python-telegram-bot JobQueue registration, the CLI, and a
one-shot run. No test uses the internet or any API key.

**Test mode** (no keys except optionally Telegram):
- Terminal only: `python -m app.main --preview morning --test-mode`
- Real Telegram with sample data: `python -m app.main --test-mode`

---

## 9. Troubleshooting

| Problem | Fix |
|---|---|
| Bot says "This is a private bot" | Put the chat ID it shows into `ALLOWED_CHAT_IDS` and restart |
| `Telegram rejected TELEGRAM_BOT_TOKEN` | Copy the token again from @BotFather (`/mybots` → API Token) |
| "Another copy of this bot is running" / Conflict | Stop the other copy (another terminal, Docker container, server, or the GitHub workflow) |
| Summaries say "AI analysis unavailable" | No AI key, or the free quota is used up. Run `--check-ai`, add a Groq key as backup, or wait for the quota to reset |
| `gemini: model not found` | Set `GEMINI_MODEL` to a model listed in Google AI Studio |
| A news source shows FAIL in `--check-feeds` | Publishers sometimes change feed URLs. Update or remove it in `config/sources.yaml` |
| Market snapshot "Data currently unavailable" | Yahoo's unofficial endpoint can be blocked or rate-limited. News still works. Set `MARKET_DATA_ENABLED=false` to hide it |
| Briefing times are wrong | Check `TIMEZONE=Asia/Kolkata` and your PC/server clock |
| Nothing at 13:00 | Correct behaviour: the midday update is skipped when there's nothing new and important |
| Logs | `logs/app.log` (rotated automatically). Secrets are masked in logs |

---

## 10. Limitations (honest list)

- **The automated tests use mocks**, not live services. Run `--check-feeds`, `--check-ai` and `--preview morning` once to confirm feeds and AI work from your network.
- **Feed URLs can change.** Dead feeds are skipped automatically; fix them in `config/sources.yaml`.
- **Market data is delayed and unofficial** (Yahoo Finance). Every number shows its time and source. Don't use it for trading decisions.
- **NSE/BSE announcement pages are not used directly**, because they offer no public API for this and restrict scraping. Exchange filings reach you through the news outlets that report on them. FII/DII figures likewise come only from news reports.
- **The AI can still make mistakes.** Summaries use only the feed text, and analysis is labelled as analysis. Always open the original article for anything important.
- Free AI quotas and model names change over time. Model names are configurable in `.env`.

⚠️ *This bot produces AI-generated news summaries for informational and
educational purposes. It is not financial or investment advice and never
recommends buying or selling securities.*

---

## 11. Contributing

Contributions are welcome: new news sources, better prompts, bug fixes, translations, docs.

1. Fork the repo and create a branch: `git checkout -b my-change`
2. Set up: `python -m venv .venv`, then install with `pip install -r requirements-dev.txt`
3. Make your change and add or adjust tests in `tests/`
4. Run the tests: `python -m pytest -q` (no API keys or internet needed)
5. Open a pull request describing what you changed and why

Good first contributions:
- Add or fix RSS feeds in `config/sources.yaml` (run `python -m app.main --check-feeds` to verify)
- Add company names to `company_aliases` for better watchlist matching
- Improve the AI prompt in `app/ai/analyzer.py`, keeping the no-advice and facts-only rules
- New AI providers in `app/ai/providers.py` (implement `complete_json`)

Please never commit API keys or `.env` files. See [CONTRIBUTING.md](CONTRIBUTING.md).

---

## 12. License

[MIT](LICENSE) © 2026 Vikas M.V. You are free to use, copy, modify and share this project; keep the license notice.

News content belongs to the original publishers. The bot only links to and briefly summarises publicly offered RSS items.

