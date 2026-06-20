#!/usr/bin/env python3
"""
Daily intraday-momentum screener for US stocks -> Telegram.

Uses TradingView's public (undocumented) scanner API — no key or login needed.
Standard library only. Reuses the same Telegram secrets as the briefing.

Env vars required:
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

The screen: liquid US names that are "in play" today — trading at 2x+ their
normal volume and up 3%+. These are momentum/day-trading CANDIDATES, not signals.
"""

import os
import sys
import json
import html
import datetime as dt
from zoneinfo import ZoneInfo
from urllib.request import urlopen, Request
from urllib.error import HTTPError

SGT = ZoneInfo("Asia/Singapore")
TELEGRAM_MAX = 3800
SCANNER_URL = "https://scanner.tradingview.com/america/scan"

# Column order matters — the response returns values in this order.
COLUMNS = ["name", "close", "change", "relative_volume_10d_calc", "RSI", "average_volume_90d_calc"]

# The screen definition. Edit thresholds here to tune it.
SCREEN = {
    "filter": [
        {"left": "exchange", "operation": "in_range", "right": ["NASDAQ", "NYSE", "AMEX"]},
        {"left": "close", "operation": "in_range", "right": [5, 200]},
        {"left": "average_volume_90d_calc", "operation": "greater", "right": 1000000},
        {"left": "relative_volume_10d_calc", "operation": "greater", "right": 2},
        {"left": "change", "operation": "greater", "right": 3},
    ],
    "options": {"lang": "en"},
    "markets": ["america"],
    "symbols": {"query": {"types": []}, "tickers": []},
    "columns": COLUMNS,
    "sort": {"sortBy": "relative_volume_10d_calc", "sortOrder": "desc"},
    "range": [0, 20],
}


def env(name):
    v = os.environ.get(name, "").strip()
    if not v:
        sys.exit(f"ERROR: missing required environment variable {name}")
    return v


def run_screen():
    body = json.dumps(SCREEN).encode("utf-8")
    req = Request(SCANNER_URL, data=body, method="POST")
    req.add_header("content-type", "application/json")
    req.add_header("user-agent",
                   "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")
    try:
        with urlopen(req, timeout=30) as r:
            data = json.load(r)
    except HTTPError as e:
        print(f"ERROR: scanner HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:600]}")
        raise

    rows = data.get("data", []) or []
    out = []
    for row in rows:
        sym = row.get("s", "")                 # e.g. "NASDAQ:AAPL"
        d = row.get("d", [])
        if len(d) < len(COLUMNS):
            continue
        out.append({
            "ticker": sym.split(":")[-1],
            "price": d[1],
            "change": d[2],
            "rvol": d[3],
            "rsi": d[4],
        })
    return out


def num(x, dp=2):
    try:
        return f"{float(x):.{dp}f}"
    except (TypeError, ValueError):
        return "-"


def compose(rows):
    now = dt.datetime.now(SGT).strftime("%a %d %b, %H:%M SGT")
    head = (f"\U0001F4C8 <b>Pre-Open Momentum Watchlist</b>\n"
            f"<i>{now} \u2014 US stocks in play</i>\n")
    if not rows:
        return head + ("\nNo stocks matched the screen right now "
                       "(US market may be closed, or nothing fits the criteria).")

    lines = ["\n<b>Top movers by relative volume:</b>"]
    for r in rows:
        lines.append(
            f"\n<b>{html.escape(r['ticker'])}</b>  ${num(r['price'])}   "
            f"{num(r['change'])}%   \u00b7  RVOL {num(r['rvol'])}x   \u00b7  RSI {num(r['rsi'], 0)}"
        )
    foot = ("\n\n<i>Watchlist only \u2014 these are candidates with unusual volume, not buy "
            "signals. Apply your own entry, stop-loss, and position size. Data delayed ~15 min. "
            "Not investment advice.</i>")
    return head + "".join(lines) + foot


def split_message(text, limit=TELEGRAM_MAX):
    chunks, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > limit:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    if cur.strip():
        chunks.append(cur)
    return chunks


def send_telegram(token, chat, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for chunk in split_message(text):
        body = json.dumps({
            "chat_id": chat,
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }).encode("utf-8")
        req = Request(url, data=body, method="POST")
        req.add_header("content-type", "application/json")
        try:
            with urlopen(req, timeout=30) as r:
                res = json.load(r)
        except HTTPError as e:
            print(f"ERROR: telegram HTTP {e.code}: {e.read().decode('utf-8', 'replace')}")
            raise
        if not res.get("ok"):
            print(f"ERROR: telegram: {res}")
            sys.exit(1)


def main():
    token = env("TELEGRAM_BOT_TOKEN")
    chat = env("TELEGRAM_CHAT_ID")
    rows = run_screen()
    print(f"Screen returned {len(rows)} matches.")
    send_telegram(token, chat, compose(rows))
    print("Sent to Telegram.")


if __name__ == "__main__":
    main()
