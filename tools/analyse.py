#!/usr/bin/env python3
"""Per-rule expectancy from journal/trades.csv. Run it locally whenever."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd

df = pd.read_csv("journal/trades.csv")
if df.empty:
    raise SystemExit("No closed trades yet.")

rows = []
for (rule, tier), g in df.groupby(["rule", "tier"]):
    wins = g[g.r_net > 0]
    losses = g[g.r_net <= 0]
    win_rate = len(wins) / len(g)
    avg_w = wins.r_net.mean() if len(wins) else 0.0
    avg_l = losses.r_net.mean() if len(losses) else 0.0
    equity_curve = g.sort_values("close_time").r_net.cumsum()
    rows.append({
        "rule": rule, "tier": tier, "n": len(g),
        "win%": round(100 * win_rate, 1),
        "avg_win_R": round(avg_w, 2),
        "avg_loss_R": round(avg_l, 2),
        "expectancy_R": round(g.r_net.mean(), 3),
        "total_R": round(g.r_net.sum(), 2),
        "max_dd_R": round((equity_curve - equity_curve.cummax()).min(), 2),
    })

out = pd.DataFrame(rows).sort_values("expectancy_R", ascending=False)
print(out.to_string(index=False))
print("\nexpectancy_R is the only column that matters. Below zero = delete the rule.")
