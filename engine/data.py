"""
Candle fetching. Returns {symbol: DataFrame} with lowercase OHLCV columns,
indexed by timestamp, and with the still-forming last candle DROPPED --
acting on an incomplete candle is the classic way to fool yourself.
"""
import pandas as pd

CRYPTO_TF = {"15m": "15m", "1h": "1h", "1d": "1d"}


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    df = df.rename(columns=str.lower)
    keep = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
    df = df[keep].dropna()
    return df


def fetch_equity(symbols, interval="15m", lookback_bars=120):
    import yfinance as yf
    period = {"15m": "5d", "1h": "1mo", "1d": "1y"}.get(interval, "5d")
    out = {}
    if not symbols:
        return out
    raw = yf.download(
        symbols, period=period, interval=interval,
        group_by="ticker", auto_adjust=False, progress=False, threads=True,
    )
    for sym in symbols:
        try:
            df = raw[sym] if len(symbols) > 1 else raw
            df = _clean(df)
            if len(df) > 1:
                df = df.iloc[:-1]          # drop the forming candle
            out[sym] = df.tail(lookback_bars)
        except Exception as e:            # one bad ticker must not kill the run
            print(f"  ! equity fetch failed for {sym}: {e}")
    return out


def fetch_crypto(symbols, interval="15m", lookback_bars=120):
    import ccxt
    ex = ccxt.binance({"enableRateLimit": True})
    tf = CRYPTO_TF.get(interval, "15m")
    out = {}
    for sym in symbols:
        try:
            ohlcv = ex.fetch_ohlcv(sym, timeframe=tf, limit=lookback_bars + 2)
            df = pd.DataFrame(
                ohlcv, columns=["ts", "open", "high", "low", "close", "volume"]
            )
            df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
            df = df.set_index("ts")
            if len(df) > 1:
                df = df.iloc[:-1]
            out[sym] = df.tail(lookback_bars)
        except Exception as e:
            print(f"  ! crypto fetch failed for {sym}: {e}")
    return out
