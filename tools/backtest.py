#!/usr/bin/env python3
"""
How often does each rule actually fire, and what would it have returned?

Replays the rules over the last ~60 days of 15m history for the whole
watchlist, using the SAME honesty rules as the live paper trader:
  - entry at the NEXT candle's open, never the signal price
  - if one candle spans both stop and target, assume the stop hit first
  - round-trip costs deducted, so the R figures are what you would keep

Run from the repo root:   python tools/backtest.py [--days 59] [--interval 15m]

This measures the PAST. It is a sanity check on whether a rule fires often
enough to be worth watching -- not evidence that it will make money.
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import pandas as pd
import yaml

from engine import data
from engine.indicators import atr
from engine.rules import RULES

MIN_BARS = 60


def simulate(df, rule_fn, cfg, asset_class):
    """Walk the series once, opening and resolving trades exactly as live does."""
    a = atr(df, cfg["atr_period"])
    closes, i, n = [], MIN_BARS, len(df)
    stop_mult, target_r = cfg["atr_stop_mult"], cfg["target_r"]
    cost_pct = cfg["costs_pct"][asset_class]

    while i < n - 1:
        if not rule_fn(df.iloc[: i + 1]):
            i += 1
            continue

        risk = stop_mult * a.iloc[i]
        if risk <= 0 or pd.isna(risk):
            i += 1
            continue

        entry = df["open"].iloc[i + 1]          # fill at the next candle's open
        stop = entry - risk
        target = entry + target_r * risk
        cost_r = (cost_pct / 100.0) * entry / risk

        exit_reason, r_gross, held = "OPEN_AT_END", None, 0
        for j in range(i + 1, min(i + 1 + cfg["timeout_bars"], n)):
            held = j - i
            if df["low"].iloc[j] <= stop:       # pessimistic on same-candle
                exit_reason, r_gross = "STOP", -1.0
                break
            if df["high"].iloc[j] >= target:
                exit_reason, r_gross = "TARGET", target_r
                break
        else:
            if held:
                exit_reason = "TIMEOUT"
                r_gross = (df["close"].iloc[i + held] - entry) / risk

        if r_gross is not None:
            closes.append({
                "signal_time": df.index[i], "entry": entry,
                "exit_reason": exit_reason, "r_gross": round(r_gross, 3),
                "r_net": round(r_gross - cost_r, 3), "bars_held": held,
            })
            i += held + 1                        # one position at a time
        else:
            i += 1
    return closes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=59)
    ap.add_argument("--interval", default=None)
    args = ap.parse_args()

    cfg = yaml.safe_load(open("config.yml"))
    wl = yaml.safe_load(open("watchlist.yml"))
    interval = args.interval or cfg["interval"]

    pairs = []
    for section in ("equity", "crypto"):
        for tier, syms in (wl.get(section) or {}).items():
            for s in syms or []:
                pairs.append((s, tier, section))

    print(f"Fetching ~{args.days} days of {interval} history for {len(pairs)} symbols...")
    hist = {}
    eq = [s for s, _, k in pairs if k == "equity"]
    cr = [s for s, _, k in pairs if k == "crypto"]
    if eq:
        hist.update(data.history_equity(eq, interval, args.days))
    if cr:
        hist.update(data.history_crypto(cr, interval, args.days,
                                        cfg.get("crypto_exchanges")))

    rows, all_trades = [], []
    for sym, tier, klass in pairs:
        df = hist.get(sym)
        if df is None or len(df) < MIN_BARS + 2:
            rows.append({"symbol": sym, "tier": tier, "bars": 0,
                         "signals": "NO DATA"})
            continue
        for rule_name in cfg["rules"]:
            fn = RULES.get(rule_name)
            if fn is None:
                continue
            trades = simulate(df, fn, cfg, klass)
            for t in trades:
                t.update(symbol=sym, tier=tier, rule=rule_name)
            all_trades += trades
            span = (df.index[-1] - df.index[0]).days or 1
            rows.append({
                "symbol": sym, "tier": tier, "rule": rule_name,
                "bars": len(df), "days": span, "signals": len(trades),
                "per_month": round(len(trades) * 30 / span, 1),
            })

    print("\n=== HOW OFTEN IT FIRES ===")
    print(pd.DataFrame(rows).to_string(index=False))

    if not all_trades:
        print("\nNo signals at all in this window.")
        print("The rule is too strict to generate data. Loosen it, add another,")
        print("or widen the watchlist -- but decide that from this number, not a hunch.")
        return

    t = pd.DataFrame(all_trades)
    print(f"\n=== WHAT THEY WOULD HAVE RETURNED ({len(t)} trades) ===")
    summary = []
    for key, g in t.groupby("rule"):
        wins = g[g.r_net > 0]
        eq_curve = g.sort_values("signal_time").r_net.cumsum()
        summary.append({
            "rule": key, "n": len(g),
            "win%": round(100 * len(wins) / len(g), 1),
            "expectancy_R": round(g.r_net.mean(), 3),
            "total_R": round(g.r_net.sum(), 2),
            "max_dd_R": round((eq_curve - eq_curve.cummax()).min(), 2),
            "stops": int((g.exit_reason == "STOP").sum()),
            "targets": int((g.exit_reason == "TARGET").sum()),
            "timeouts": int((g.exit_reason == "TIMEOUT").sum()),
        })
    print(pd.DataFrame(summary).to_string(index=False))

    print("\nBy tier:")
    tier_rows = [{"tier": k, "n": len(g),
                  "win%": round(100 * (g.r_net > 0).mean(), 1),
                  "expectancy_R": round(g.r_net.mean(), 3)}
                 for k, g in t.groupby("tier")]
    print(pd.DataFrame(tier_rows).to_string(index=False))

    out = "journal/backtest_trades.csv"
    t.to_csv(out, index=False)
    print(f"\nFull trade list -> {out}")
    print("\nCaveat: this is one fixed window of the past, with no slippage and")
    print("no gap risk. Treat it as a frequency check, not a profit forecast.")


if __name__ == "__main__":
    main()
