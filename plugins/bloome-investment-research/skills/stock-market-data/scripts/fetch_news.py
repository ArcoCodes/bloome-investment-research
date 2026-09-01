#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "yfinance>=0.2.36",
# ]
# ///
"""
fetch_news.py — News retrieval module
Sources: Yahoo Finance (individual stocks), Google News RSS (macro + stocks), Seeking Alpha RSS
"""

import json
import os
import re
import sys
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from xml.etree import ElementTree

from provider_runtime import require_provider_module

yf = require_provider_module("news", "yfinance")

CST = timezone(timedelta(hours=8))
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, ".."))
import tempfile
CACHE_DIR = os.environ.get("SKILL_CACHE_DIR") or os.path.join(tempfile.gettempdir(), "stock-market-data-cache", "news")
CACHE_PATH = os.path.join(CACHE_DIR, "news-cache.json")
USER_CONFIG_CACHE = os.path.join(SKILL_DIR, "cache", "user-config.json")
LOCAL_CONFIG_PATH = os.path.join(SKILL_DIR, "config", "stock-config.json")

# ── Weight definitions ────────────────────────────────────
SOURCE_WEIGHT = {
    "earnings":        3,   # Earnings
    "sec_filing":      3,   # SEC filing
    "analyst":         2,   # Investment-bank research / analyst
    "seeking_alpha":   2,   # Seeking Alpha
    "yahoo_finance":   1,   # Yahoo Finance news
    "google_news":     1,   # Google News
}

def parse_rss(url: str, timeout: int = 10) -> list:
    """Parse a generic RSS feed."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content = resp.read()
        root = ElementTree.fromstring(content)
        items = []
        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            link  = (item.findtext("link") or "").strip()
            pub   = (item.findtext("pubDate") or "").strip()
            desc  = (item.findtext("description") or "").strip()
            desc = re.sub(r"<[^>]+>", "", desc)[:300]
            items.append({"title": title, "link": link, "published": pub, "summary": desc})
        return items
    except Exception as e:
        print(f"  ⚠️  RSS parse failed {url[:60]}... {e}", file=sys.stderr)
        return []

def classify_article(title: str, summary: str) -> str:
    """Classify an article by title keywords, which determine its weight."""
    text = (title + " " + summary).lower()
    if any(k in text for k in ["earnings", "eps", "revenue", "quarterly", "\u8d22\u62a5", "\u4e1a\u7ee9"]):
        return "earnings"
    if any(k in text for k in ["sec filing", "8-k", "10-q", "10-k", "proxy"]):
        return "sec_filing"
    if any(k in text for k in ["analyst", "upgrade", "downgrade", "price target", "buy", "sell rating", "overweight", "underweight", "\u6295\u884c", "\u8bc4\u7ea7"]):
        return "analyst"
    if "seekingalpha" in text:
        return "seeking_alpha"
    return "yahoo_finance"

def fetch_yahoo_news(ticker: str, max_items: int = 8) -> list:
    """Retrieve individual-stock news through yfinance."""
    try:
        t = yf.Ticker(ticker)
        raw = t.news or []
        results = []
        for item in raw[:max_items]:
            title   = item.get("title", "")
            link    = item.get("link", "")
            pub_ts  = item.get("providerPublishTime", 0)
            summary = item.get("summary", "") or ""
            pub_str = datetime.fromtimestamp(pub_ts, tz=CST).strftime("%Y-%m-%d %H:%M") if pub_ts else ""
            kind    = classify_article(title, summary)
            results.append({
                "source":   "yahoo_finance",
                "type":     kind,
                "weight":   SOURCE_WEIGHT.get(kind, 1),
                "ticker":   ticker,
                "title":    title,
                "link":     link,
                "published": pub_str,
                "summary":  summary[:300],
            })
        return results
    except Exception as e:
        print(f"  ⚠️  Yahoo news retrieval failed {ticker}: {e}", file=sys.stderr)
        return []

def fetch_google_news(query: str, max_items: int = 6) -> list:
    """Search Google News RSS."""
    encoded = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
    items = parse_rss(url)[:max_items]
    results = []
    for item in items:
        kind = classify_article(item["title"], item["summary"])
        results.append({
            "source":    "google_news",
            "type":      kind,
            "weight":    SOURCE_WEIGHT.get(kind, 1),
            "query":     query,
            "title":     item["title"],
            "link":      item["link"],
            "published": item["published"],
            "summary":   item["summary"],
        })
    return results

def fetch_seeking_alpha_rss(ticker: str, max_items: int = 5) -> list:
    """Retrieve Seeking Alpha RSS by ticker."""
    url = f"https://seekingalpha.com/api/sa/combined/{ticker}.xml"
    items = parse_rss(url)[:max_items]
    results = []
    for item in items:
        results.append({
            "source":    "seeking_alpha",
            "type":      "seeking_alpha",
            "weight":    SOURCE_WEIGHT["seeking_alpha"],
            "ticker":    ticker,
            "title":     item["title"],
            "link":      item["link"],
            "published": item["published"],
            "summary":   item["summary"],
        })
    return results

def _parse_pub_time(pub_str: str) -> datetime | None:
    """Attempt to parse a pubDate string as datetime."""
    formats = [
        "%a, %d %b %Y %H:%M:%S %z",   # RSS standard
        "%a, %d %b %Y %H:%M:%S GMT",
        "%Y-%m-%d %H:%M",              # our own format
    ]
    for fmt in formats:
        try:
            return datetime.strptime(pub_str, fmt)
        except (ValueError, TypeError):
            continue
    return None

def filter_recent(articles: list, hours: int = 48) -> list:
    """Filter out news older than the specified number of hours."""
    cutoff = datetime.now(CST) - timedelta(hours=hours)
    result = []
    for a in articles:
        pub = _parse_pub_time(a.get("published", ""))
        if pub is None:
            result.append(a)  # Retain when parsing fails
        elif pub.astimezone(CST) >= cutoff:
            result.append(a)
    return result

def deduplicate(articles: list) -> list:
    """Deduplicate across sources by title prefix."""
    seen = set()
    result = []
    for a in articles:
        # Use the first 40 title characters as the deduplication key
        key = a.get("title", "")[:40].lower().strip()
        if key and key in seen:
            continue
        seen.add(key)
        result.append(a)
    return result

def fetch_macro_news() -> list:
    """Macro news: Federal Reserve / economic data / geopolitics."""
    queries = [
        "Federal Reserve interest rate",
        "US economy GDP inflation",
        "China economy trade",
        "geopolitical risk market",
    ]
    results = []
    for q in queries:
        results.extend(fetch_google_news(q, max_items=3))
        time.sleep(0.3)
    return results

def fetch_all_news(tickers: list) -> dict:
    """Main entry point: retrieve all news."""
    now = datetime.now(CST)
    result = {
        "fetched_at": now.isoformat(),
        "macro": [],
        "stocks": {},
    }

    # Macro news
    print("📰 Retrieving macro news...", file=sys.stderr)
    result["macro"] = deduplicate(filter_recent(fetch_macro_news()))
    print(f"  → {len(result['macro'])} items", file=sys.stderr)

    # Individual-stock news
    for ticker in tickers:
        print(f"📰 {ticker} news...", file=sys.stderr)
        articles = []
        articles.extend(fetch_yahoo_news(ticker))
        time.sleep(0.3)
        articles.extend(fetch_google_news(f"{ticker} stock", max_items=4))
        time.sleep(0.3)
        articles.extend(fetch_seeking_alpha_rss(ticker))
        # Sort by weight
        articles.sort(key=lambda x: x["weight"], reverse=True)
        articles = deduplicate(filter_recent(articles))
        result["stocks"][ticker] = articles
        print(f"  → {len(articles)} items (highest weight: {articles[0]['weight'] if articles else 0})", file=sys.stderr)

    # Write cache
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n💾 News cache saved: {CACHE_PATH}", file=sys.stderr)

    return result

def _load_config():
    """Prefer the user-configuration cache generated by fetch_data.py."""
    if os.path.exists(USER_CONFIG_CACHE):
        with open(USER_CONFIG_CACHE, encoding="utf-8") as f:
            return json.load(f)
    if os.path.exists(LOCAL_CONFIG_PATH):
        print("⚠️ user-config.json does not exist; run fetch_data.py first", file=sys.stderr)
        with open(LOCAL_CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    print("❌ No configuration available", file=sys.stderr)
    sys.exit(1)


def main():
    import argparse
    p = argparse.ArgumentParser(description="Fetch stock + macro news (Yahoo/Google News RSS/Seeking Alpha, keyless)")
    p.add_argument("symbols", nargs="*", help="tickers, e.g. NBIS AAPL; empty = macro only")
    a = p.parse_args()
    tickers = list(dict.fromkeys(s.upper() for s in a.symbols))
    data = fetch_all_news(tickers)
    print(json.dumps(data, ensure_ascii=False, indent=1, default=str))

if __name__ == "__main__":
    main()
