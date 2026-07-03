# 🎬 Movie Ticket Alert Bot

A Telegram bot that watches **BookMyShow** and **District** and messages you the
moment a show you care about becomes bookable. It **never books anything for
you** — it only notifies.

---

## ⚠️ Read this first: architecture reality check

This project has two separate pieces that behave differently, and it's
important you understand the split before deploying:

| Piece | What it does | Where it runs |
|---|---|---|
| `bot.py` | Handles `/start`, `/watch`, `/list`, `/remove` — the **interactive** conversation | Needs an **always-on process** somewhere |
| `checker.py` | Polls providers every few minutes and sends notifications | **GitHub Actions cron** — no VPS needed |

The "no VPS required" part of this project is true for the **polling/checking**
half, because GitHub Actions' scheduler is a good fit for "run a script every
5 minutes." It is **not** true for the interactive half: a Telegram bot that
responds to `/watch` in real time needs *some* process listening for
incoming messages continuously, and GitHub Actions jobs aren't designed to
stay alive indefinitely (they're for scheduled/triggered batch jobs). Any
architecture that claims otherwise is glossing over this.

Practical options for the interactive half, roughly in order of effort:

1. **Run `bot.py` on your own machine** whenever you want to add/remove
   alerts, then leave it stopped otherwise. Zero cost, zero setup, but you
   have to have it open to run `/watch`.
2. **Free tier of a small always-on host** (Railway, Render, Fly.io, PythonAnywhere, etc.)
   running `bot.py` with long-polling. This is the realistic "set it and
   forget it" option and most of these have free tiers sufficient for a
   single-user bot.
3. **Webhook + serverless function** (Cloudflare Workers, Vercel, AWS
   Lambda) if you want zero always-on process at all — more setup work,
   not covered by this repo out of the box.

The **checking/notifying** half (`checker.py` + the GitHub Actions workflow)
genuinely needs nothing beyond this repo and a GitHub account, once you've
subscribed to something via option 1, 2, or 3 above.

---

## How the scraping works (and its limits)

Neither BookMyShow nor District publish an official public API for
showtimes or seat availability. `providers/bookmyshow.py` and
`providers/district.py` use Playwright to load the real pages a human
visitor would see and extract data from the server-rendered JSON payload
embedded in the page (or fall back to parsing the visible DOM if that
JSON marker isn't there).

**This is inherently fragile and needs your attention before first use:**

- The exact JSON marker id and CSS selectors in the provider files are
  documented best-guesses based on how sites like this are typically built
  (Next.js-style embedded JSON), not something verified against a live
  browser session at the time this code was written.
- Before relying on this, run:
  ```bash
  python scripts/inspect_provider.py "https://in.bookmyshow.com/explore/home"
  python scripts/inspect_provider.py "https://www.district.in/movies"
  ```
  and open the resulting `inspect_output.html` / `inspect_output.json` to
  confirm (or fix) the selectors in `providers/bookmyshow.py` /
  `providers/district.py`. All the places you'd need to touch are marked
  with comments and are isolated in small `_parse_*_from_json` /
  `_parse_*_from_dom` methods.
- If a provider stops returning results across the board, or you start
  seeing repeated `ProviderError`s, the most likely cause is either a site
  redesign (fix the selectors) or the site's bot-detection flagging the
  traffic. **Do not try to add CAPTCHA-solving or fingerprint spoofing to
  work around that** — back off instead (increase the check interval,
  reduce your number of subscriptions, or pause).
- Please also actually read BookMyShow's and District's Terms of Service.
  This tool is built for light, personal, non-commercial use (checking a
  few shows every few minutes), not bulk data collection.

---

## Project structure

```
movie-alert-bot/
├── bot.py                     # Interactive Telegram bot (long-polling)
├── conversation.py            # /watch guided setup flow
├── checker.py                 # Scheduled availability checker
├── storage.py                 # JSON storage layer (atomic read/write)
├── config.py                  # Environment-variable-based settings
├── utils.py                   # Logging, retry decorator, JSON extraction helper
├── providers/
│   ├── base_provider.py       # Interface every provider implements
│   ├── playwright_provider.py # Shared browser lifecycle (no duplicated code)
│   ├── bookmyshow.py
│   ├── district.py
│   └── __init__.py            # Provider registry
├── scripts/
│   └── inspect_provider.py    # Dump a live page to fix selectors
├── requirements.txt
├── users.json / subscriptions.json / seen.json / config.json
└── .github/workflows/check.yml
```

---

## 1. Create your Telegram bot with BotFather

1. Open Telegram, search for **@BotFather**, start a chat.
2. Send `/newbot`, follow the prompts (choose a name, then a username
   ending in `bot`).
3. BotFather gives you a token like `123456789:AAExampleTokenNotReal`. Keep
   it secret — this is your `BOT_TOKEN`.
4. Optional: `/setdescription`, `/setuserpic` to make it look nice.

---

## 2. Run it locally

```bash
git clone <your-fork-url>
cd movie-alert-bot
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium

export BOT_TOKEN="123456789:AAExampleTokenNotReal"
python bot.py
```

Message your bot on Telegram, send `/start`, then `/watch` to set up your
first alert.

To manually run a single check (useful for testing before setting up
GitHub Actions):

```bash
python checker.py
```

---

## 3. Deploy to GitHub

1. Push this repo to your own GitHub repository (public or private — a
   private repo is recommended since `subscriptions.json` will contain
   Telegram user/chat IDs).
2. Go to **Settings → Secrets and variables → Actions** in your repo.
3. Add these repository secrets:
   - `BOT_TOKEN` — required. From BotFather.
   - `BOOKMYSHOW_COOKIES` — optional. Only needed if you find BMS requires
     a location/session cookie to serve consistent results (format:
     `key1=value1; key2=value2`).
   - `DISTRICT_COOKIES` — optional, same idea for District.
4. That's it for the checking half. The workflow in
   `.github/workflows/check.yml` runs `checker.py` every 5 minutes
   automatically once secrets are set.
5. Set up the interactive half (`bot.py`) using one of the options in the
   "architecture reality check" section above.

---

## 4. How the GitHub Actions workflow works

- **Trigger:** `cron: "*/5 * * * *"` (roughly every 5 minutes — GitHub's
  scheduler doesn't guarantee exact timing under load) plus
  `workflow_dispatch` so you can click **Run workflow** manually from the
  Actions tab to test it.
- **Steps:** checkout → install Python 3.12 → `pip install -r requirements.txt`
  → install the Chromium browser for Playwright → run `checker.py` → commit
  any changes to `subscriptions.json` / `seen.json` / `users.json` back to
  the repo (this is how state persists between runs without a database).
- **Concurrency:** a `concurrency` group prevents two runs from overlapping
  and racing on the JSON state files.
- **Permissions:** the workflow needs `contents: write` to commit the
  updated state files — this is already set in the workflow file.

---

## 5. Bot commands

| Command | What it does |
|---|---|
| `/start` | Welcome message |
| `/watch` | Guided setup: platform → city → movie → theatre → date, all via buttons |
| `/list` | Show your active alerts |
| `/remove` | Delete an alert (button picker) |
| `/help` | Help text |

Nothing is ever typed by hand — every choice comes from a live list fetched
from the provider at that moment.

---

## 6. Troubleshooting

**"Couldn't load cities/movies/theatres" errors** — Run
`scripts/inspect_provider.py` against the relevant URL and compare the
output to what `providers/bookmyshow.py` / `providers/district.py` expect.
Site markup changes are the most common cause; update the `_parse_*`
methods accordingly.

**GitHub Actions run fails to push state changes** — Make sure
`permissions: contents: write` is present in the workflow (it is by
default in this repo) and that Actions is allowed to push to the repo
under **Settings → Actions → General → Workflow permissions**.

**No notifications ever arrive even though shows exist** — Check the
Actions run logs (Actions tab → latest run → `Run availability checker`
step). `checker.py` logs a per-subscription line for every check; a
`ProviderError` there points at a selector/site problem, not a bot problem.

**Notifications repeat for the same show** — This shouldn't happen because
`seen.json` records every notified `show_id`, but if you manually edited or
deleted `seen.json`, expect a fresh burst of "new" notifications on the
next run — that's expected, not a bug.

**Rate limiting / Telegram "Too Many Requests" errors** — `checker.py`
sends one message per new show sequentially; if you have a huge number of
subscriptions all triggering at once, add a short `asyncio.sleep()` between
`_notify()` calls in `checker.py`.

---

## 7. Adding a new provider

1. Create `providers/yourprovider.py` implementing every method on
   `BaseProvider` (see `providers/base_provider.py`). If it's
   scraping-based, subclass `PlaywrightProvider` like the existing two
   providers do to reuse the browser-lifecycle code.
2. Register it in `providers/__init__.py`:
   ```python
   from providers.yourprovider import YourProvider
   PROVIDER_CLASSES[YourProvider.name] = YourProvider
   ```
3. That's it — `bot.py`, `conversation.py`, and `checker.py` all work
   against the `BaseProvider` interface and the registry, so nothing else
   needs to change.

---

## 8. Notification format

```
🎬 Ticket Available

Platform: BookMyShow
Movie: <Movie Name>
Theatre: <Theatre>
Date: <Date>
Time: <Time>

Book Here: <Booking URL>
```

---

## 9. Design notes

- **Storage:** plain JSON files, written atomically (temp file + rename) so
  a crash mid-write can't corrupt state. No database, as specified.
- **De-duplication:** every show gets a deterministic id
  (`provider|movie_id|theatre_id|date|time|screen`) stored in `seen.json`
  per subscription, so the same show is never notified twice.
- **Extensibility for seat-level monitoring:** `Show.extra` (a free-form
  dict) exists specifically so a future provider can attach per-seat-type
  availability without changing the `BaseProvider` interface.
- **Isolation:** the Telegram bot code (`bot.py`, `conversation.py`,
  `checker.py`) only ever imports from `providers/base_provider.py` and
  `providers/__init__.py` — never a specific provider module — so
  provider-specific logic can never leak into shared code.
