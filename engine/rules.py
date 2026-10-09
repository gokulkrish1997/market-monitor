"""
Each rule takes a DataFrame of COMPLETED candles and returns True if the
signal fires on the most recent one.

Add your own here and list its name in config.yml -> rules.
Keep them boring and mechanical. The journal decides if they are any good.
"""
import pandas as pd
from engine.indicators import rsi, ema


def rsi_reversal(df: pd.DataFrame) -> bool:
    """RSI(14) crosses up through 30 while price is above the 50-EMA."""
    if len(df) < 60:
        return False
    r = rsi(df["close"], 14)
    e50 = ema(df["close"], 50)
    crossed_up = bool(r.iloc[-2] < 30 <= r.iloc[-1])
    above_trend = bool(df["close"].iloc[-1] > e50.iloc[-1])
    return crossed_up and above_trend


RULES = {
    "rsi_reversal": rsi_reversal,
}
