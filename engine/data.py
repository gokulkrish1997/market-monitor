"""
Candle fetching. Returns {symbol: DataFrame} with lowercase OHLCV columns,
indexed by timestamp, and with the still-forming last candle DROPPED --
acting on an incomplete candle is the classic way to fool yourself.

Crypto note: many exchanges refuse requests from datacentre IPs in certain
countries (Binance returns HTTP 451 to GitHub's US-based runners). So we try
a list of exchanges in order and use the first that actually answers.
"""
import pandas as pd

CRYPTO_TF = {"15m": "15m", "1h": "1h", "1d": "1d"}

# Exchanges differ on whether a pair is quoted in USDT or USD.
QUOTE_SWAPS = [("/USDT", "/USD"), ("/USD", "/USDT")]


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    df = df.rename(columns=str.lower)
    keep = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
    return df[keep].dropna()


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


def _resolve(markets, symbol):
    """Find how THIS exchange spells the pair (BTC/USDT vs BTC/USD)."""
    if symbol in markets:
        return symbol
    for a, b in QUOTE_SWAPS:
        if symbol.endswith(a):
            alt = symbol[: -len(a)] + b
            if alt in markets:
                return alt
    return None


def _try_exchange(name, symbols, tf, lookback_bars):
    import ccxt
    ex = getattr(ccxt, name)({"enableRateLimit": True, "timeout": 20000})
    markets = ex.load_markets()          # raises if the exchange refuses us
    out, missing = {}, []
    for sym in symbols:
        listed = _resolve(markets, sym)
        if listed is None:
            missing.append(sym)
            continue
        ohlcv = ex.fetch_ohlcv(listed, timeframe=tf, limit=lookback_bars + 2)
        df = pd.DataFrame(ohlcv, columns=["ts", "open", "high", "low", "close", "volume"])
        df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        df = df.set_index("ts")
        if len(df) > 1:
            df = df.iloc[:-1]
        # key by the name in YOUR watchlist, whatever the exchange calls it
        out[sym] = df.tail(lookback_bars)
    return out, missing


def fetch_crypto(symbols, interval="15m", lookback_bars=120, exchanges=None):
    out = {}
    if not symbols:
        return out
    tf = CRYPTO_TF.get(interval, "15m")
    exchanges = exchanges or ["kraken", "coinbase", "bitstamp", "gemini", "binanceus", "binance"]

    for name in exchanges:
        try:
            got, missing = _try_exchange(name, symbols, tf, lookback_bars)
        except Exception as e:
            msg = str(e).split("\n")[0][:120]
            print(f"  - {name}: unreachable or refused ({type(e).__name__}: {msg})")
            continue
        if got:
            print(f"  crypto source: {name} ({len(got)}/{len(symbols)} symbols)")
            if missing:
                print(f"    not listed on {name}: {', '.join(missing)}")
            return got
        print(f"  - {name}: reachable but none of the symbols are listed")

    print("  ! every crypto exchange failed - no crypto data this run")
    return out
