# market-monitor

A cron job that watches a watchlist, paper-trades its own signals, and messages
you on Telegram. It never places a real order. There is no app and no website —
Telegram is the interface, the GitHub Actions tab is the dashboard, and
`journal/trades.csv` is the result.

## Setup

1. **Telegram bot** — message `@BotFather`, send `/newbot`, keep the token.
   Message `@userinfobot` to get your numeric chat ID.
2. **Repo** — push these files. Public repo = unlimited Actions minutes.
3. **Secrets** — Settings → Secrets and variables → Actions → add
   `TELEGRAM_TOKEN` and `TELEGRAM_CHAT_ID`. Encrypted even on a public repo.
4. **Validate the watchlist** — `pip install -r requirements.txt` then
   `python tools/validate_watchlist.py`. Fix anything marked NO DATA.
5. **Test it** — Actions tab → "market scan" → Run workflow. Check your phone.

The schedule starts itself the moment the workflow file is on `main`.

## Files

| Path | What it is |
|---|---|
| `watchlist.yml` | What gets watched, grouped by volatility tier |
| `config.yml` | Risk %, ATR multiple, target R, costs, cadence |
| `engine/rules.py` | Your conditions. Add rules here |
| `engine/paper.py` | PENDING → OPEN → CLOSED state machine |
| `scan.py` | One scan run |
| `tools/analyse.py` | Per-rule expectancy from the journal |
| `journal/trades.csv` | Every closed paper trade, append-only |

## How the paper trading is kept honest

- Fills at the **next candle's open**, never the signal price.
- If one candle spans both stop and target, the **stop** is assumed.
- The still-forming candle is dropped before any rule sees it.
- Round-trip costs are deducted, so `r_net` is what you'd actually keep.
- Skipped/duplicate signals are suppressed per symbol, per rule, per candle.

## Local run

Always from the repo root:

    pip install -r requirements.txt
    python tools/validate_watchlist.py   # tickers alive? tiers right?
    python tools/backtest.py             # how often does the rule fire?
    python scan.py --force               # --force ignores market hours
    python tools/analyse.py              # expectancy from the live journal

## Tolerating a flaky scheduler

GitHub deprioritises scheduled workflows and routinely drops runs, sometimes
for hours. The scan is built for that:

- It tracks the last candle it evaluated per symbol and works through every
  candle it missed, rather than only looking at the newest one.
- `catchup_bars` caps how far back it will look, so an outage cannot produce a
  wall of alerts.
- Signals older than `max_alert_age_bars` are written to the journal but do not
  ping your phone -- the data stays complete, the notifications stay current.
- The heartbeat fires on the first run after `heartbeat_after_ist`, once a day,
  rather than needing a run inside a narrow window.

## Minutes budget

Roughly 1,400 runs/month across both schedules. With pip caching each run is
well under a minute, so a private repo fits inside the 2,000-minute free tier —
but public removes the worry entirely.
