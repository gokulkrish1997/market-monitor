#!/usr/bin/env python3
"""
One scan. Fetch -> advance open paper trades -> look for new signals -> notify.

Designed to be late-tolerant: it works from completed candle timestamps, not
from "what happened in the last 15 minutes". A run that fires 25 minutes behind
schedule produces exactly the same result, just later.
"""
import sys
from datetime import datetime, timedelta, timezone

import yaml

from engine import data, notify, paper
from engine.indicators import atr
from engine.rules import RULES

IST = timezone(timedelta(hours=5, minutes=30))


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


def main():
    cfg = load_yaml("config.yml")
    wl = load_yaml("watchlist.yml")
    state = paper.load_state()

    equities = flatten(wl.get("equity"))
    cryptos = flatten(wl.get("crypto"))
    tier_of = {s: t for s, t in equities + cryptos}
    klass = {s: "equity" for s, _ in equities}
    klass.update({s: "crypto" for s, _ in cryptos})

    candles = {}
    if equity_market_open(cfg) or "--force" in sys.argv:
        candles.update(data.fetch_equity(
            [s for s, _ in equities], cfg["interval"], cfg["lookback_bars"]))
    else:
        print("Equity market closed - skipping NSE symbols.")
    candles.update(data.fetch_crypto(
        [s for s, _ in cryptos], cfg["interval"], cfg["lookback_bars"]))

    # 1. Advance anything already live BEFORE looking for new entries.
    closed = paper.update_positions(state, candles, cfg)
    for pos in closed:
        notify.send(notify.format_close(pos))
    for pos in state["positions"]:
        if pos["status"] == "OPEN" and not pos.get("announced"):
            notify.send(notify.format_fill(pos))
            pos["announced"] = True

    # 2. Look for new signals.
    signals = 0
    for symbol, df in candles.items():
        if df is None or len(df) < 60:
            continue
        candle_ts = str(df.index[-1])
        a = atr(df, cfg["atr_period"]).iloc[-1]
        for rule_name in cfg["rules"]:
            fn = RULES.get(rule_name)
            if fn is None or not fn(df):
                continue
            if paper.already_signalled(state, symbol, rule_name, candle_ts):
                continue
            if paper.has_live_position(state, symbol, rule_name):
                continue
            paper.mark_signalled(state, symbol, rule_name, candle_ts)
            paper.open_pending(
                state, symbol=symbol, asset_class=klass[symbol],
                tier=tier_of[symbol], rule=rule_name,
                candle_ts=candle_ts, atr_value=a,
            )
            pos = state["positions"][-1]
            notify.send(notify.format_signal(pos, 100 * a / df["close"].iloc[-1]))
            signals += 1

    paper.save_state(state)

    live = len(state["positions"])
    print(f"Scanned {len(candles)} symbols | {signals} new | {live} live | "
          f"{len(closed)} closed")

    # Daily heartbeat so silence is never ambiguous.
    now = datetime.now(IST)
    if now.hour == 15 and 30 <= now.minute < 50:
        notify.send(
            f"Daily heartbeat {now:%d %b}: scanned {len(candles)} symbols, "
            f"{live} positions live."
        )


if __name__ == "__main__":
    main()
