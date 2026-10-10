#!/usr/bin/env python3
"""
One scan. Fetch -> advance open paper trades -> look for new signals -> notify.

Built for an unreliable scheduler. GitHub Actions regularly drops scheduled
runs, sometimes for hours. So this does NOT ask "what happened in the last 15
minutes" -- it asks "which completed candles have I not looked at yet" and
works through all of them. A run that fires six hours late finds everything it
missed; it just reports it later.

Flags:
  --force      ignore equity market hours (for local testing)
  --selftest   send a Telegram test message and exit
"""
import sys
from datetime import datetime, timedelta, timezone

import yaml

from engine import data, notify, paper
from engine.indicators import atr
from engine.rules import RULES

IST = timezone(timedelta(hours=5, minutes=30))
MIN_BARS = 60          # rules need this much history before they mean anything


def load_yaml(path):
    with open(path) as f:
        return yaml.safe_load(f)


def flatten(section):
    """{'low': [...], 'high': [...]} -> [(symbol, tier), ...]"""
    out = []
    for tier, syms in (section or {}).items():
        for s in syms or []:
            out.append((s, tier))
    return out


def equity_market_open(cfg):
    now = datetime.now(IST)
    if now.weekday() >= 5:
        return False
    o = datetime.strptime(cfg["market_hours_ist"]["equity_open"], "%H:%M").time()
    c = datetime.strptime(cfg["market_hours_ist"]["equity_close"], "%H:%M").time()
    return o <= now.time() <= c


def selftest():
    """Prove the secrets reached the runner and Telegram accepts them."""
    ok = notify.credentials_present()
    print(f"TELEGRAM_TOKEN   : {'present' if ok['token'] else 'MISSING'}")
    print(f"TELEGRAM_CHAT_ID : {'present' if ok['chat_id'] else 'MISSING'}")
    if not (ok["token"] and ok["chat_id"]):
        print("\nAdd them under Settings > Secrets and variables > Actions.")
        return 1
    sent = notify.send(
        f"Selftest OK - {datetime.now(IST):%d %b %H:%M IST}\n"
        f"Secrets are reaching the runner."
    )
    print("delivered" if sent else "Telegram REJECTED the message - check the values")
    return 0 if sent else 1


def drain_closures(state, cfg):
    """Advance live positions; announce only the ones we alerted on."""
    closed = paper.update_positions(state, drain_closures.candles, cfg)
    for pos in closed:
        if pos.get("alerted", True):
            notify.send(notify.format_close(pos))
    for pos in state["positions"]:
        if pos["status"] == "OPEN" and not pos.get("announced"):
            if pos.get("alerted", True):
                notify.send(notify.format_fill(pos))
            pos["announced"] = True
    return closed


def scan_symbol(state, symbol, df, cfg, klass, tier_of):
    """
    Check every completed candle we have not evaluated yet, oldest first.
    Returns (signals_found, signals_alerted).
    """
    stamps = [str(t) for t in df.index]
    last_seen = state["last_eval"].get(symbol)

    if last_seen is None:
        start = len(stamps) - 1               # first sight: newest candle only
    else:
        start = next((i for i, t in enumerate(stamps) if t > last_seen), len(stamps))

    # Never back-check further than catchup_bars, so a long outage or a fresh
    # watchlist entry cannot produce a wall of historical alerts.
    start = max(start, len(stamps) - cfg["catchup_bars"])

    found = alerted = 0
    for i in range(start, len(stamps)):
        if i + 1 < MIN_BARS:
            continue
        window = df.iloc[: i + 1]
        candle_ts = stamps[i]
        age = len(stamps) - 1 - i             # how many bars ago this candle was
        a = atr(window, cfg["atr_period"]).iloc[-1]

        for rule_name in cfg["rules"]:
            fn = RULES.get(rule_name)
            if fn is None or not fn(window):
                continue
            if paper.already_signalled(state, symbol, rule_name, candle_ts):
                continue
            if paper.has_live_position(state, symbol, rule_name):
                continue

            fresh = age <= cfg["max_alert_age_bars"]
            paper.mark_signalled(state, symbol, rule_name, candle_ts)
            paper.open_pending(
                state, symbol=symbol, asset_class=klass[symbol],
                tier=tier_of[symbol], rule=rule_name,
                candle_ts=candle_ts, atr_value=a, alerted=fresh,
            )
            found += 1
            pos = state["positions"][-1]
            if fresh:
                notify.send(notify.format_signal(
                    pos, 100 * a / window["close"].iloc[-1], age))
                alerted += 1
            else:
                print(f"  logged (too old to alert): {symbol} {rule_name} "
                      f"@ {candle_ts}, {age} bars ago")

    state["last_eval"][symbol] = stamps[-1]
    return found, alerted


def maybe_heartbeat(state, cfg, scanned, live):
    """
    Fire once per day, on the FIRST run after the cutoff. The old version only
    fired inside a 20-minute window, so a dropped run meant no heartbeat -- the
    one message whose whole job is to prove the system is alive.
    """
    now = datetime.now(IST)
    cutoff = datetime.strptime(cfg["heartbeat_after_ist"], "%H:%M").time()
    today = now.strftime("%Y-%m-%d")
    if now.time() < cutoff or state.get("last_heartbeat") == today:
        return
    state["last_heartbeat"] = today
    notify.send(
        f"Heartbeat {now:%d %b %H:%M IST}\n"
        f"Scanned {scanned} symbols, {live} position(s) live. Monitor alive."
    )


def main():
    cfg = load_yaml("config.yml")
    wl = load_yaml("watchlist.yml")
    state = paper.load_state()

    equities = flatten(wl.get("equity"))
    cryptos = flatten(wl.get("crypto"))
    tier_of = {s: t for s, t in equities + cryptos}
    klass = {s: "equity" for s, _ in equities}
    klass.update({s: "crypto" for s, _ in cryptos})

    attempted = 0
    candles = {}

    if equity_market_open(cfg) or "--force" in sys.argv:
        attempted += len(equities)
        candles.update(data.fetch_equity(
            [s for s, _ in equities], cfg["interval"], cfg["lookback_bars"]))
    else:
        print("Equity market closed - skipping NSE symbols.")

    attempted += len(cryptos)
    candles.update(data.fetch_crypto(
        [s for s, _ in cryptos], cfg["interval"], cfg["lookback_bars"],
        exchanges=cfg.get("crypto_exchanges")))

    # A run that fetched nothing must NOT look like a successful run.
    if attempted and not candles:
        print(f"\nERROR: attempted {attempted} symbols, got data for none.")
        print("The scan did nothing. Failing so this shows as a red run.")
        notify.send(
            "<b>Monitor broken</b>\nFetched 0 of "
            f"{attempted} symbols this run. Check the Actions log."
        )
        return 1

    drain_closures.candles = candles

    # 1. Advance anything already live BEFORE looking for new entries.
    closed = drain_closures(state, cfg)

    # 2. Work through every candle we have not evaluated yet.
    found = alerted = 0
    for symbol, df in candles.items():
        if df is None or len(df) < MIN_BARS:
            continue
        f, a = scan_symbol(state, symbol, df, cfg, klass, tier_of)
        found += f
        alerted += a

    # 3. Backfilled signals may already have filled and closed. Settle them now
    #    rather than leaving them hanging until the next run.
    if found:
        closed += drain_closures(state, cfg)

    live = len(state["positions"])
    print(f"Scanned {len(candles)}/{attempted} symbols | {found} new "
          f"({alerted} alerted) | {live} live | {len(closed)} closed")

    maybe_heartbeat(state, cfg, len(candles), live)
    paper.save_state(state)
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    sys.exit(main())
