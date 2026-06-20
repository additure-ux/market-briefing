#!/usr/bin/env python3
"""
Daily US-stock market briefing -> Telegram.  Standard library only.

Env vars required:
  FINNHUB_API_KEY, ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
Optional:
  SESSION = "morning" | "evening"
"""

import os
import sys
import json
import html
import datetime as dt
from zoneinfo import ZoneInfo
from urllib.request import urlopen, Request
from urllib.parse import urlencode
from urllib.error import HTTPError

SGT = ZoneInfo("Asia/Singapore")
ANTHROPIC_MODEL = "claude-sonnet-4-6"
FINNHUB_HEADLINE_COUNT = 12
TELEGRAM_MAX = 3800


def env(name, required=True):
    val = os.environ.get(name, "").strip()
    if required and not val:
        sys.exit(f"ERROR: missing required environment variable {name}")
    return val


def get_json(url):
    with urlopen(url, timeout=30) as r:
        return json.load(r)


def post_json(url, headers, payload):
    req = Request(url, data=json.dumps(payload).encode("utf-8"), method="POST")
    req.add_header("content-type", "application/json")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urlopen(req, timeout=120) as r:
            return json.load(r)
    except HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        raise RuntimeError(f"HTTP {e.code}: {body}") from None


def session_label():
    forced = os.environ.get("SESSION", "").strip().lower()
    if forced in ("morning", "evening"):
        return forced
    return "morning" if dt.datetime.now(SGT).hour < 14 else "evening"


def fetch_finnhub_headlines(api_key):
    url = "https://finnhub.io/api/v1/news?" + urlencode({"category": "general", "token": api_key})
    try:
        items = get_json(url)
    except Exception as e:                       # noqa: BLE001
        print(f"WARN: Finnhub fetch failed: {e}")
        return []
    seen, out = set(), []
    for it in items:
        h = (it.get("headline") or "").strip()
        if h and h not in seen:
            seen.add(h)
            out.append({"headline": h, "source": it.get("source", ""), "url": it.get("url", "")})
        if len(out) >= FINNHUB_HEADLINE_COUNT:
            break
    return out


def build_prompt(session, headlines):
    today = dt.datetime.now(SGT).strftime("%A %d %B %Y")
    hb = "\n".join(f"- {h['headline']} ({h['source']})" for h in headlines) or "(none retrieved)"
    when = ("This is the 7am Singapore (morning) edition." if session == "morning"
            else "This is the 8pm Singapore (evening) edition, before the US market open.")
    return f"""You are writing a concise market briefing for a trader in Singapore who trades US stocks.
Today is {today} (Singapore time). {when}

Use web search to verify the latest US market moves and to build an accurate
economic calendar. Raw headlines already pulled from a news feed for context:
{hb}

Write a briefing with EXACTLY these three short sections, in this order:

<b>1. Overnight / latest move</b>
2-4 sentences: where the major US indices (S&P 500, Nasdaq, Dow, Russell 2000) closed
or where futures point, plus Treasury yields / oil if relevant.

<b>2. Top catalyst</b>
2-4 sentences on the single biggest market-moving theme right now.

<b>3. On deck today / this session</b>
A short dated list of upcoming US economic releases and notable earnings for US equities,
with consensus expectations where known.

Formatting rules (this goes to Telegram):
- Use ONLY these HTML tags: <b>, <i>, <a href="...">. No Markdown, no '#', no '*'.
- Under 1500 characters. Punchy. Facts only, no buy/sell advice.
- If US markets are closed (holiday/weekend), say so clearly."""


def fetch_claude_briefing(api_key, session, headlines):
    payload = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": 1500,
        "messages": [{"role": "user", "content": build_prompt(session, headlines)}],
        "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}],
    }
    try:
        data = post_json(
            "https://api.anthropic.com/v1/messages",
            {"x-api-key": api_key, "anthropic-version": "2023-06-01"},
            payload,
        )
    except Exception as e:                       # noqa: BLE001
        print(f"WARN: Anthropic call failed: {e}")
        return "<i>(Summary unavailable - Anthropic API error. Raw headlines below.)</i>"
    parts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
    return "\n".join(p for p in parts if p).strip() or "<i>(Empty summary returned.)</i>"


def compose(session, summary, headlines):
    now = dt.datetime.now(SGT).strftime("%a %d %b, %H:%M SGT")
    icon = "\U0001F305" if session == "morning" else "\U0001F319"
    title = "Morning Market Briefing" if session == "morning" else "Pre-US-Open Briefing"
    head = f"{icon} <b>{title}</b>\n<i>{now}</i>\n"

    raw = "\n\n<b>Raw headlines (Finnhub)</b>"
    if headlines:
        for h in headlines:
            t = html.escape(h["headline"])
            if h.get("url"):
                raw += f'\n\u2022 <a href="{html.escape(h["url"])}">{t}</a>'
            else:
                raw += f"\n\u2022 {t}"
    else:
        raw += "\n(none retrieved)"
    return f"{head}\n{summary}\n{raw}"


def split_message(text, limit=TELEGRAM_MAX):
    chunks, current = [], ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > limit:
            chunks.append(current)
            current = ""
        current += line + "\n"
    if current.strip():
        chunks.append(current)
    return chunks


def send_telegram(token, chat_id, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for chunk in split_message(text):
        res = post_json(url, {}, {
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        })
        if not res.get("ok"):
            print(f"ERROR: Telegram: {res}")
            sys.exit(1)


def main():
    finnhub_key   = env("FINNHUB_API_KEY")
    anthropic_key = env("ANTHROPIC_API_KEY")
    tg_token      = env("TELEGRAM_BOT_TOKEN")
    tg_chat       = env("TELEGRAM_CHAT_ID")

    session = session_label()
    print(f"Running {session} edition...")

    headlines = fetch_finnhub_headlines(finnhub_key)
    print(f"Got {len(headlines)} headlines.")

    summary = fetch_claude_briefing(anthropic_key, session, headlines)
    message = compose(session, summary, headlines)

    send_telegram(tg_token, tg_chat, message)
    print("Sent to Telegram.")


if __name__ == "__main__":
    main()
