"""Telegram output. Falls back to stdout when no token is configured."""
import os
import requests

TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


def send(text: str):
    print(text)
    if not TOKEN or not CHAT_ID:
        print("  (no Telegram credentials set - printed only)")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=20,
        )
        if r.status_code != 200:
            print(f"  ! telegram error {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"  ! telegram send failed: {e}")


def format_signal(pos, atr_pct):
    return (
        f"<b>{pos['symbol']}</b>  [{pos['tier']}]\n"
        f"Rule: {pos['rule']}\n"
        f"Signal candle: {pos['signal_time']}\n"
        f"ATR: {pos['atr_at_signal']:.2f} ({atr_pct:.2f}% of price)\n"
        f"<i>Entry fills at next candle open. Levels follow in the next run.</i>"
    )


def format_fill(pos):
    return (
        f"<b>FILLED {pos['symbol']}</b>  [{pos['tier']}] - {pos['rule']}\n"
        f"Entry  {pos['entry']}\n"
        f"Stop   {pos['stop']}\n"
        f"Target {pos['target']}\n"
        f"Size   {pos['qty']} units  (risk {pos['risk_amount']})\n"
        f"<i>Paper only. No order placed.</i>"
    )


def format_close(pos):
    icon = {"TARGET": "[+]", "STOP": "[-]", "TIMEOUT": "[=]"}.get(pos["exit_reason"], "[?]")
    return (
        f"{icon} <b>CLOSED {pos['symbol']}</b> - {pos['exit_reason']}\n"
        f"{pos['rule']} | {pos['entry']} -> {pos['exit']}\n"
        f"Net {pos['r_net']:+.2f}R after {pos['costs_pct']}% costs "
        f"({pos['bars_held']} bars)"
    )
