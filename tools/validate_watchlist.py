#!/usr/bin/env python3
"""
Run this FIRST, and again every few months.

  1. Confirms every ticker actually resolves (symbols change after mergers,
     demergers and renames -- a dead ticker fails silently otherwise).
  2. Measures real ATR% and turnover so you can re-tier on evidence
     instead of on somebody's guess.
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import yaml
import pandas as pd
from engine import data
from engine.indicators import atr

wl = yaml.safe_load(open("watchlist.yml"))

rows = []
for section, fetch in (("equity", data.fetch_equity), ("crypto", data.fetch_crypto)):
    pairs = [(s, t) for t, syms in (wl.get(section) or {}).items() for s in syms or []]
    got = fetch([s for s, _ in pairs], "1d", 120)
    for sym, tier in pairs:
        df = got.get(sym)
        if df is None or df.empty:
            rows.append({"symbol": sym, "listed_tier": tier, "status": "NO DATA"})
            continue
        a = atr(df, 14).iloc[-1]
        price = df["close"].iloc[-1]
        atr_pct = 100 * a / price
        measured = "low" if atr_pct < 1.5 else ("medium" if atr_pct < 2.5 else "high")
        turnover = (df["close"] * df["volume"]).tail(20).mean()
        rows.append({
            "symbol": sym, "listed_tier": tier, "status": "ok",
            "atr_pct": round(atr_pct, 2), "measured_tier": measured,
            "agrees": "yes" if measured == tier else "NO",
            "avg_turnover": f"{turnover:,.0f}",
        })

print(pd.DataFrame(rows).to_string(index=False))
print("\nAnything marked NO belongs in a different tier. Anything marked "
      "NO DATA is a dead ticker -- fix it before it silently stops scanning.")
