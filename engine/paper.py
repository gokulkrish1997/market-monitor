"""
Paper-trade lifecycle.

    signal fires  ->  PENDING   (we do not know the fill price yet)
    next candle   ->  OPEN      (filled at that candle's OPEN, honestly)
    later candles ->  CLOSED    (TARGET / STOP / TIMEOUT)

State lives in journal/open_positions.json between runs.
Closed trades are appended to journal/trades.csv and never rewritten.
"""
import csv
import json
import os
from datetime import datetime, timezone

STATE_PATH = "journal/open_positions.json"
TRADES_PATH = "journal/trades.csv"

FIELDS = [
    "id", "asset_class", "symbol", "rule", "tier",
    "signal_time", "fill_time", "close_time",
    "entry", "stop", "target", "exit", "exit_reason",
    "qty", "risk_amount", "r_gross", "costs_pct", "r_net", "bars_held",
]


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_state():
    if not os.path.exists(STATE_PATH):
        return {"positions": [], "seen": {}}
    with open(STATE_PATH) as f:
        return json.load(f)


def save_state(state):
    os.makedirs("journal", exist_ok=True)
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)


def already_signalled(state, symbol, rule, candle_ts):
    """One alert per rule per symbol per candle. Without this you get spam."""
    return state["seen"].get(f"{symbol}|{rule}") == candle_ts


def mark_signalled(state, symbol, rule, candle_ts):
    state["seen"][f"{symbol}|{rule}"] = candle_ts


def has_live_position(state, symbol, rule):
    return any(
        p["symbol"] == symbol and p["rule"] == rule and p["status"] in ("PENDING", "OPEN")
        for p in state["positions"]
    )


def open_pending(state, *, symbol, asset_class, tier, rule, candle_ts, atr_value):
    state["positions"].append({
        "id": f"{symbol}-{rule}-{candle_ts}".replace(" ", "_"),
        "symbol": symbol,
        "asset_class": asset_class,
        "tier": tier,
        "rule": rule,
        "status": "PENDING",
        "signal_time": candle_ts,
        "atr_at_signal": float(atr_value),
        "bars_held": 0,
    })


def _fill(pos, fill_price, fill_time, cfg):
    risk_per_unit = cfg["atr_stop_mult"] * pos["atr_at_signal"]
    if risk_per_unit <= 0:
        return False
    pos["entry"] = round(float(fill_price), 4)
    pos["stop"] = round(pos["entry"] - risk_per_unit, 4)
    pos["target"] = round(pos["entry"] + cfg["target_r"] * risk_per_unit, 4)
    risk_amount = cfg["capital"] * cfg["risk_pct"] / 100.0
    pos["qty"] = round(risk_amount / risk_per_unit, 6)
    pos["risk_amount"] = round(risk_amount, 2)
    pos["fill_time"] = fill_time
    pos["status"] = "OPEN"
    return True


def _close(pos, exit_price, reason, close_time, cfg):
    risk_per_unit = pos["entry"] - pos["stop"]
    r_gross = (exit_price - pos["entry"]) / risk_per_unit
    cost_pct = cfg["costs_pct"][pos["asset_class"]]
    # Costs expressed in R: (% of notional) / (risk as % of entry)
    cost_r = (cost_pct / 100.0) * pos["entry"] / risk_per_unit
    pos["exit"] = round(float(exit_price), 4)
    pos["exit_reason"] = reason
    pos["close_time"] = close_time
    pos["r_gross"] = round(r_gross, 3)
    pos["costs_pct"] = cost_pct
    pos["r_net"] = round(r_gross - cost_r, 3)
    pos["status"] = "CLOSED"


def update_positions(state, candles, cfg):
    """Advance every live position using candles that arrived since last run."""
    closed = []
    for pos in state["positions"]:
        df = candles.get(pos["symbol"])
        if df is None or df.empty:
            continue

        if pos["status"] == "PENDING":
            after = df[df.index.astype(str) > pos["signal_time"]]
            if after.empty:
                continue
            first = after.iloc[0]
            if not _fill(pos, first["open"], str(after.index[0]), cfg):
                pos["status"] = "CLOSED"
                pos["exit_reason"] = "BAD_ATR"
                closed.append(pos)
                continue
            bars = after
        else:
            bars = df[df.index.astype(str) > pos.get("last_seen", pos["fill_time"])]

        for ts, bar in bars.iterrows():
            pos["bars_held"] += 1
            pos["last_seen"] = str(ts)
            hit_stop = bar["low"] <= pos["stop"]
            hit_target = bar["high"] >= pos["target"]
            # Pessimistic: if one candle spans both levels, assume the stop first.
            if hit_stop:
                _close(pos, pos["stop"], "STOP", str(ts), cfg)
                closed.append(pos)
                break
            if hit_target:
                _close(pos, pos["target"], "TARGET", str(ts), cfg)
                closed.append(pos)
                break
            if pos["bars_held"] >= cfg["timeout_bars"]:
                _close(pos, bar["close"], "TIMEOUT", str(ts), cfg)
                closed.append(pos)
                break

    state["positions"] = [p for p in state["positions"] if p["status"] != "CLOSED"]
    if closed:
        append_trades(closed)
    return closed


def append_trades(rows):
    os.makedirs("journal", exist_ok=True)
    new_file = not os.path.exists(TRADES_PATH)
    with open(TRADES_PATH, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        if new_file:
            w.writeheader()
        for r in rows:
            w.writerow(r)
