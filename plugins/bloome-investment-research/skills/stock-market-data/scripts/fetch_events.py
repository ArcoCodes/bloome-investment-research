# /// script
# requires-python = ">=3.10"
# dependencies = ["yfinance", "pandas", "httpx", "akshare"]
# ///
"""Fetch events for a given symbol: earnings, analyst ratings, insider activity, web search.

Supports both US stocks (Finviz + SEC EDGAR) and HK stocks (akshare).
When price anomaly or hard events detected, triggers Tavily web search for context.

Usage: python scripts/fetch_events.py GOOGL
       python scripts/fetch_events.py NVDA --price-change -5.0
       python scripts/fetch_events.py NVDA --search
       python scripts/fetch_events.py 0700.HK --price-change 4.0 --company-name Tencent
Output: JSON with events list, has_actionable flag.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
from provider_runtime import require_provider_module

yf = require_provider_module("events", "yfinance")

from cache_utils import load_cache, save_cache

# Tavily web search (optional, same sandbox)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../web-search/scripts"))
try:
    from tavily_search import search as tavily_search
    TAVILY_AVAILABLE = True
except ImportError:
    TAVILY_AVAILABLE = False


def _sanitize_text(text: str, max_len: int = 100) -> str:
    """Sanitize external text to prevent prompt injection.

    Strips newlines, control chars, and truncates to max_len.
    Only allows letters, digits, basic punctuation, and spaces.
    """
    if not isinstance(text, str):
        return ""
    # Remove newlines and control characters
    cleaned = re.sub(r'[\n\r\t\x00-\x1f\x7f]', ' ', text)
    # Only keep safe characters: letters, digits, common punctuation, spaces
    cleaned = re.sub(r'[^\w\s\.,;:\-\(\)\/&\'\"@#%\+]', '', cleaned)
    # Collapse whitespace
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned[:max_len]

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False

try:
    import akshare as ak
    AKSHARE_AVAILABLE = True
except ImportError:
    AKSHARE_AVAILABLE = False


# ---------------------------------------------------------------------------
# Entity normalization (simplified from event_normalizer.py)
# ---------------------------------------------------------------------------

FOUNDATION_PATTERNS = [r"\s+family\s+foundation$", r"\s+foundation$", r"\s+charitable\s+foundation$"]
TRUST_PATTERNS = [r"\s+revocable\s+trust$", r"\s+family\s+trust$", r"\s+trust$"]

_10B5_1_KEYWORDS = [
    "10b5-1", "10b-5-1", "rule 10b5", "trading plan",
    "pre-arranged", "pre-planned", "automatic", "pursuant to a plan",
]

LIKELY_10B5_1_TITLES = [
    "chief executive", "ceo", "chief financial", "cfo",
    "president", "director", "vice president", "vp",
]


def normalize_name(raw: str) -> str:
    """Clean and normalize a person name from SEC format."""
    cleaned = raw.lower().strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    for p in FOUNDATION_PATTERNS + TRUST_PATTERNS:
        cleaned = re.sub(p, "", cleaned, flags=re.IGNORECASE).strip()
    parts = cleaned.split()
    if len(parts) >= 2:
        return f"{parts[1].title()} {parts[0].title()}"
    return cleaned.title()


def is_likely_planned(title: str, footnotes: str = "") -> bool:
    """Check if transaction is likely a 10b5-1 planned trade."""
    fn_lower = footnotes.lower()
    for kw in _10B5_1_KEYWORDS:
        if kw in fn_lower:
            return True
    title_lower = title.lower()
    for pattern in LIKELY_10B5_1_TITLES:
        if pattern in title_lower:
            return True
    return False


# ---------------------------------------------------------------------------
# Signal strength assessment (simplified from event_validator.py)
# ---------------------------------------------------------------------------

def assess_insider_signal(transaction_type: str, value: float, title: str = "",
                          footnotes: str = "", is_10b5_1: bool = False) -> str:
    """Return signal strength: none/low/medium/high/critical."""
    is_planned = is_10b5_1 or is_likely_planned(title, footnotes)

    if transaction_type.lower() == "buy":
        return "high" if value > 500_000 else "medium"

    # Sell
    if is_planned and value < 500_000:
        return "none"
    if is_planned:
        return "low"
    if value > 10_000_000:
        return "high"
    if value > 1_000_000:
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# Event classification (per event-processing.md §3)
# ---------------------------------------------------------------------------

def classify_event(event: dict) -> str:
    """Classify event as 'hard' or 'explanatory'.

    Hard events: can trigger P0 independently (earnings miss, large insider buy).
    Explanatory events: max P2, provide context only.
    """
    etype = event.get("type", "")

    # Earnings with actual results (beat/miss) = hard
    # Only if recent (within last 3 days), not stale historical data
    if etype == "earnings" and -3 <= event.get("days_until", 999) <= 0:
        return "hard"

    # Insider buy > $500k = hard
    if etype == "insider_buy" and event.get("value", 0) > 500_000:
        return "hard"

    # Insider sell (large, unplanned) = hard
    if etype == "insider_sell":
        if not event.get("is_10b5_1", False) and event.get("value", 0) > 10_000_000:
            return "hard"

    # Analyst downgrade = hard only if recent (within 7 days)
    if etype == "analyst_rating" and event.get("action") == "downgrade":
        days_ago = event.get("days_ago")
        if days_ago is not None and days_ago <= 7:
            return "hard"
        if days_ago is None:
            return "explanatory"  # No date = can't confirm recency, treat as explanatory
        return "explanatory"

    # SEC 8-K with hard items (management change, earnings release, M&A, etc.)
    if etype == "sec_filing" and event.get("form") == "8-K":
        items = event.get("items", [])
        if any(item in SEC_8K_HARD_ITEMS for item in items):
            return "hard"

    # HK / US news classified as hard by keyword matching
    if etype in ("hk_news", "us_news") and event.get("is_hard_news"):
        days_ago = event.get("days_ago")
        if days_ago is not None and days_ago <= 3:
            return "hard"

    # Web search results with hard news keywords
    if etype == "web_search" and event.get("is_hard_news"):
        return "hard"

    # Everything else is explanatory
    return "explanatory"


SEC_8K_ITEMS = {
    "1.01": "Material agreement entered into/terminated",
    "1.02": "Material asset impairment",
    "1.03": "Bankruptcy/receivership",
    "2.01": "Material asset acquisition/disposition",
    "2.02": "Results of operations released",
    "2.03": "Material debt incurred",
    "2.04": "Off-balance-sheet arrangement triggered",
    "2.05": "Restructuring/layoffs",
    "2.06": "Material impairment/write-off",
    "3.01": "Delisting/transfer notice",
    "3.02": "Sale of unregistered securities",
    "3.03": "Material modification to security-holder rights",
    "4.01": "Auditor change",
    "4.02": "Financial statements deemed unreliable",
    "5.01": "Corporate governance/change in control",
    "5.02": "Officer/director appointment or departure",
    "5.03": "Charter/bylaws amended",
    "5.05": "Code of ethics amended",
    "5.07": "Shareholder vote results",
    "7.01": "Regulation FD disclosure",
    "8.01": "Other material event",
    "9.01": "Financial statements/exhibits",
}

# 8-K items that are hard events (can independently affect position)
SEC_8K_HARD_ITEMS = {"1.01", "1.02", "2.01", "2.02", "2.05", "2.06", "4.01", "4.02", "5.02"}

# 8-K items that are medium signal (worth noting)
SEC_8K_MEDIUM_ITEMS = {"1.03", "2.03", "3.01", "3.02", "5.01", "7.01", "8.01"}


# ---------------------------------------------------------------------------
# Source 2.5: US individual stock news (yfinance ticker.news)
# ---------------------------------------------------------------------------

_US_NEWS_HARD_KEYWORDS = [
    # management
    "resign", "depart", "fired", "ceo", "cfo", "arrest", "fraud",
    # regulatory
    "fda reject", "fda approv", "sec charges", "doj", "investigation", "lawsuit", "ban",
    # earnings/guidance
    "profit warning", "earnings miss", "guidance cut", "guidance lower", "lowers forecast",
    # structural
    "acquisition", "merger", "buyout", "privatiz", "delisted", "bankrupt", "chapter 11",
    "restructur", "layoff", "recall",
]

_US_NEWS_MEDIUM_KEYWORDS = [
    "earnings", "revenue", "guidance", "forecast", "analyst", "upgrade", "downgrade",
    "target price", "buyback", "dividend", "partnership", "contract", "deal",
    "product launch", "fda clearance", "approval", "patent",
]


def fetch_us_news(symbol: str, max_age_days: int = 3) -> list[dict]:
    """Fetch individual stock news via yfinance ticker.news.

    Returns recent news articles filtered by keyword relevance.
    """
    events = []
    try:
        ticker = yf.Ticker(symbol)
        news_items = ticker.news
        if not news_items:
            return events

        cutoff = datetime.utcnow().timestamp() - max_age_days * 86400

        for item in news_items:
            content = item.get("content", {})
            title = content.get("title", "")
            summary = content.get("summary", "") or content.get("description", "")
            pub_date_str = content.get("pubDate", "") or content.get("displayTime", "")
            provider = content.get("provider", {}).get("displayName", "")
            url = (content.get("canonicalUrl", {}) or {}).get("url", "") or \
                  (content.get("clickThroughUrl", {}) or {}).get("url", "")

            if not title:
                continue

            # Parse publish time
            pub_ts = None
            if pub_date_str:
                try:
                    pub_ts = datetime.strptime(pub_date_str[:19], "%Y-%m-%dT%H:%M:%S").timestamp()
                except (ValueError, TypeError):
                    pass

            if pub_ts and pub_ts < cutoff:
                continue

            days_ago = int((datetime.utcnow().timestamp() - pub_ts) / 86400) if pub_ts else None

            # Keyword relevance filtering
            text = (title + " " + summary).lower()
            is_hard = any(kw in text for kw in _US_NEWS_HARD_KEYWORDS)
            is_medium = any(kw in text for kw in _US_NEWS_MEDIUM_KEYWORDS)

            if not is_hard and not is_medium:
                continue

            events.append({
                "type": "us_news",
                "date": pub_date_str[:10] if pub_date_str else None,
                "days_ago": days_ago,
                "title": _sanitize_text(title, 120),
                "summary": _sanitize_text(summary, 200),
                "source": _sanitize_text(provider, 50),
                "url": _sanitize_text(url, 300),
                "signal_strength": "high" if is_hard else "medium",
                "is_hard_news": is_hard,
            })

    except Exception:
        pass

    return events[:8]


def _is_us_symbol(symbol: str) -> bool:
    """Check if symbol is a US stock (no exchange suffix)."""
    return "." not in symbol


def _get_cik(symbol: str) -> Optional[str]:
    """Look up SEC CIK number for a US ticker symbol.

    Uses the SEC company_tickers.json endpoint which maps all tickers to CIKs.
    Returns zero-padded 10-digit CIK string, or None if not found.
    """
    if not HTTPX_AVAILABLE:
        return None
    try:
        resp = httpx.get(
            "https://www.sec.gov/files/company_tickers.json",
            headers={"User-Agent": "Novark/1.0 (contact@novark.vip)"},
            timeout=10.0,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        for entry in data.values():
            if entry.get("ticker", "").upper() == symbol.upper():
                return str(entry["cik_str"]).zfill(10)
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Source 1: Earnings calendar (yfinance)
# ---------------------------------------------------------------------------

def fetch_earnings(symbol: str) -> list[dict]:
    """Fetch earnings calendar and history for a symbol."""
    events = []
    try:
        ticker = yf.Ticker(symbol)
        calendar = ticker.calendar

        if calendar is None:
            return events

        earnings_date = None
        eps_estimate = None

        if isinstance(calendar, dict):
            if "Earnings Date" in calendar:
                dates = calendar["Earnings Date"]
                if isinstance(dates, list) and len(dates) > 0:
                    earnings_date = pd.to_datetime(dates[0])
                elif not isinstance(dates, list) and dates is not None:
                    earnings_date = pd.to_datetime(dates)
            if "Earnings Average" in calendar:
                eps_estimate = float(calendar["Earnings Average"])
        elif isinstance(calendar, pd.DataFrame) and not calendar.empty:
            if "Earnings Date" in calendar.columns and len(calendar["Earnings Date"].dropna()) > 0:
                earnings_date = pd.to_datetime(calendar["Earnings Date"].dropna().iloc[0])

        if earnings_date:
            now = datetime.now()
            # Make earnings_date naive if it has timezone info
            if hasattr(earnings_date, "tzinfo") and earnings_date.tzinfo is not None:
                earnings_date = earnings_date.replace(tzinfo=None)
            days_until = (earnings_date - now).days

            event = {
                "type": "earnings",
                "date": earnings_date.strftime("%Y-%m-%d"),
                "days_until": days_until,
                "details": f"Earnings report",
            }
            if eps_estimate is not None:
                event["eps_estimate"] = eps_estimate

            # Signal: approaching earnings is actionable
            if 0 < days_until <= 14:
                event["signal_strength"] = "medium"
            else:
                event["signal_strength"] = "low"

            events.append(event)

    except Exception as e:
        events.append({"type": "earnings", "error": str(e)})

    return events


# ---------------------------------------------------------------------------
# Source 2: Analyst ratings (Finviz scraping)
# ---------------------------------------------------------------------------

def fetch_analyst_ratings(symbol: str) -> list[dict]:
    """Scrape recent analyst ratings from Finviz."""
    if not HTTPX_AVAILABLE:
        return []

    events = []
    try:
        url = f"https://finviz.com/quote.ashx?t={symbol}"
        resp = httpx.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        }, timeout=10.0)

        if resp.status_code != 200:
            return events

        html = resp.text
        table_match = re.search(
            r'<table[^>]*class="[^"]*(?:js-table-ratings|fullview-ratings-outer)[^"]*"[^>]*>(.*?)</table>',
            html, re.DOTALL | re.IGNORECASE,
        )
        if not table_match:
            return events

        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", table_match.group(1), re.DOTALL)
        now = datetime.now()
        for row in rows[:10]:
            cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
            cells_clean = [re.sub(r"<[^>]+>", "", c).strip() for c in cells]

            if len(cells_clean) < 4 or not cells_clean[0]:
                continue

            date_str = cells_clean[0]
            action = cells_clean[1] if len(cells_clean) > 1 else ""
            firm = cells_clean[2] if len(cells_clean) > 2 else ""
            rating = cells_clean[3] if len(cells_clean) > 3 else ""
            target_str = cells_clean[4] if len(cells_clean) > 4 else ""
            target_str = target_str.replace("&rarr;", "→")

            # Parse target price
            amounts = re.findall(r"\$?([\d,]+(?:\.\d+)?)", target_str)
            target_price = float(amounts[-1].replace(",", "")) if amounts else None

            # Parse date (Finviz format: "Feb-23-26" or "Mar 10")
            event_date, days_ago = _parse_finviz_date(date_str, now)

            # Skip events older than 30 days
            if days_ago is not None and days_ago > 30:
                continue

            action_lower = action.lower()
            if "upgrade" in action_lower:
                action_type = "upgrade"
                signal = "medium" if days_ago is not None and days_ago <= 7 else "low"
            elif "downgrade" in action_lower:
                action_type = "downgrade"
                signal = "medium" if days_ago is not None and days_ago <= 7 else "low"
            elif "initiated" in action_lower:
                action_type = "initiated"
                signal = "low"
            else:
                action_type = "reiterated"
                signal = "low"

            event = {
                "type": "analyst_rating",
                "date": event_date,
                "days_ago": days_ago,
                "source": _sanitize_text(firm, 50),
                "action": action_type,
                "rating": _sanitize_text(rating, 30),
                "signal_strength": signal,
            }
            if target_price:
                event["target_price"] = target_price

            events.append(event)

    except Exception:
        pass

    return events


# ---------------------------------------------------------------------------
# Source 3: Insider activity (Finviz scraping)
# ---------------------------------------------------------------------------

def fetch_insider_activity(symbol: str) -> list[dict]:
    """Scrape insider trading activity from Finviz."""
    if not HTTPX_AVAILABLE:
        return []

    events = []
    seen_insiders: dict[str, int] = {}  # dedup_key -> index in events list
    seen_name_parts: dict[str, str] = {}  # each name_part -> dedup_key
    try:
        url = f"https://finviz.com/quote.ashx?t={symbol}"
        resp = httpx.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        }, timeout=10.0)

        if resp.status_code != 200:
            return events

        html = resp.text
        now = datetime.now()
        insider_rows = re.findall(
            r'<tr[^>]*class="[^"]*fv-insider-row[^"]*"[^>]*>(.*?)</tr>',
            html, re.DOTALL | re.IGNORECASE,
        )

        for row in insider_rows[:5]:
            cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
            cells_clean = [re.sub(r"<[^>]+>", "", c).strip() for c in cells]

            if len(cells_clean) < 3:
                continue

            owner = cells_clean[0] or "Unknown"
            relationship = cells_clean[1] if len(cells_clean) > 1 else ""

            # Parse date from insider row
            event_date = None
            days_ago = None
            transaction = ""
            value = 0.0
            for cell in cells_clean[2:]:
                # Try to parse date from cells
                if event_date is None:
                    d, da = _parse_finviz_date(cell, now)
                    if d is not None:
                        event_date = d
                        days_ago = da
                        continue

                cell_lower = cell.lower()
                if "sale" in cell_lower or "sell" in cell_lower:
                    transaction = "sell"
                elif "buy" in cell_lower or "purchase" in cell_lower:
                    transaction = "buy"
                amounts = re.findall(r"\$?([\d,]+(?:\.\d+)?)", cell)
                for amt in amounts:
                    try:
                        parsed = float(amt.replace(",", ""))
                        if parsed > 10000:
                            value = max(value, parsed)
                    except ValueError:
                        pass

            if not transaction:
                if "sale" in row.lower():
                    transaction = "sell"
                elif "buy" in row.lower():
                    transaction = "buy"
                else:
                    continue

            # Skip insider events older than 30 days
            if days_ago is not None and days_ago > 30:
                continue

            name = normalize_name(owner)
            planned = is_likely_planned(relationship)
            signal = assess_insider_signal(transaction, value, relationship, is_10b5_1=planned)

            # Deduplicate by person: merge entries for the same individual.
            # Finviz often lists the same person multiple times with different
            # name formats (e.g. "Sundar Pichai", "Pichai Sundar", "Pichai").
            # We match if ANY name part overlaps with an existing entry's parts.
            dedup_key = _find_matching_key(name, seen_name_parts)
            if dedup_key is None:
                # New person
                dedup_key = name.lower()
                for part in name.lower().split():
                    seen_name_parts[part] = dedup_key

            if dedup_key in seen_insiders:
                existing_idx = seen_insiders[dedup_key]
                existing = events[existing_idx]
                # Merge: accumulate value, re-assess signal
                existing["value"] = existing["value"] + value
                existing["signal_strength"] = assess_insider_signal(
                    existing["transaction"], existing["value"],
                    existing["title"], is_10b5_1=existing["is_10b5_1"]
                )
                continue

            event = {
                "type": f"insider_{transaction}",
                "date": event_date,
                "days_ago": days_ago,
                "insider": _sanitize_text(name, 50),
                "title": _sanitize_text(relationship, 50),
                "transaction": transaction,
                "value": value,
                "is_10b5_1": planned,
                "signal_strength": signal,
            }

            seen_insiders[dedup_key] = len(events)
            events.append(event)

    except Exception:
        pass

    return events


def _find_matching_key(name: str, seen_parts: dict[str, str]) -> str | None:
    """Check if any part of this name was already seen, return its dedup key."""
    for part in name.lower().split():
        if part in seen_parts:
            return seen_parts[part]
    return None


def _parse_finviz_date(date_str: str, now: datetime) -> tuple[str | None, int | None]:
    """Parse a Finviz date string.

    Formats seen in the wild:
    - Analyst ratings: "Feb-23-26" (MMM-DD-YY)
    - Insider activity: "Mar 12" (MMM DD), "Mar 12 06:30PM", "Jan 05, 2025"

    Returns (iso_date_str, days_ago) or (None, None) if unparseable.
    """
    if not date_str or not date_str.strip():
        return None, None

    clean = date_str.strip()
    # Remove time portion if present (e.g. "Jan 05 06:30PM")
    clean = re.sub(r"\s+\d{1,2}:\d{2}\s*[AP]M", "", clean, flags=re.IGNORECASE).strip()

    # Try various Finviz date formats
    formats = [
        "%b-%d-%y",     # Feb-23-26 (analyst ratings)
        "%b-%d-%Y",     # Feb-23-2026
        "%b %d, %Y",    # Mar 10, 2025
        "%b %d %Y",     # Mar 10 2025
        "%b %d",        # Mar 10 (no year)
    ]
    for fmt in formats:
        try:
            parsed = datetime.strptime(clean, fmt)
            # If no year in format, infer: use current year, adjust if >60 days in future → last year
            if fmt == "%b %d":
                parsed = parsed.replace(year=now.year)
                if parsed > now + timedelta(days=60):
                    parsed = parsed.replace(year=now.year - 1)
            days_ago = (now - parsed).days
            return parsed.strftime("%Y-%m-%d"), max(0, days_ago)
        except ValueError:
            continue

    return None, None


# ---------------------------------------------------------------------------
# Source 4: SEC EDGAR filings (8-K, 10-K/Q, large Form 4)
# ---------------------------------------------------------------------------

def fetch_sec_filings(symbol: str) -> list[dict]:
    """Fetch recent SEC filings via EDGAR Submissions API.

    Returns material events from 8-K filings (last 30 days) and
    upcoming/recent 10-K/10-Q filing dates.
    """
    if not HTTPX_AVAILABLE:
        return []

    cik = _get_cik(symbol)
    if not cik:
        return []

    events = []
    try:
        url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        resp = httpx.get(
            url,
            headers={"User-Agent": "Novark/1.0 (contact@novark.vip)"},
            timeout=10.0,
        )
        if resp.status_code != 200:
            return events

        data = resp.json()
        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        items_list = recent.get("items", [])
        descriptions = recent.get("primaryDocDescription", [])

        now = datetime.now()
        cutoff = now - timedelta(days=30)

        for i in range(min(200, len(forms))):
            form = forms[i]
            filing_date_str = dates[i] if i < len(dates) else ""
            items_str = items_list[i] if i < len(items_list) else ""
            desc = descriptions[i] if i < len(descriptions) else ""

            try:
                filing_date = datetime.strptime(filing_date_str, "%Y-%m-%d")
            except (ValueError, TypeError):
                continue

            if filing_date < cutoff:
                break  # Dates are sorted desc, stop early

            days_ago = (now - filing_date).days

            if form == "8-K" and items_str:
                item_codes = [s.strip() for s in items_str.split(",")]
                item_descriptions = []
                is_hard = False
                is_medium = False
                for code in item_codes:
                    if code in SEC_8K_ITEMS and code != "9.01":  # Skip exhibit noise
                        item_descriptions.append(SEC_8K_ITEMS[code])
                    if code in SEC_8K_HARD_ITEMS:
                        is_hard = True
                    if code in SEC_8K_MEDIUM_ITEMS:
                        is_medium = True

                if not item_descriptions:
                    continue

                signal = "high" if is_hard else ("medium" if is_medium else "low")

                events.append({
                    "type": "sec_filing",
                    "form": "8-K",
                    "date": filing_date_str,
                    "days_ago": days_ago,
                    "items": item_codes,
                    "details": "、".join(item_descriptions),
                    "signal_strength": signal,
                    "source": "SEC EDGAR",
                })

            elif form in ("10-K", "10-Q"):
                events.append({
                    "type": "sec_filing",
                    "form": form,
                    "date": filing_date_str,
                    "days_ago": days_ago,
                    "details": f"{'Annual report' if form == '10-K' else 'Quarterly report'} filed",
                    "signal_strength": "medium" if days_ago <= 3 else "low",
                    "source": "SEC EDGAR",
                })

    except Exception:
        pass

    return events


# ---------------------------------------------------------------------------
# Source 5: HK analyst ratings (akshare — East Money/etnet)
# ---------------------------------------------------------------------------

# Rating text → normalized action mapping
_HK_RATING_MAP = {
    "\u4e70\u5165": "buy", "\u589e\u6301": "buy", "\u4f18\u4e8e\u5927\u5e02": "buy",
    "\u9ad8\u5ea6\u786e\u4fe1\u4f18\u4e8e\u5927\u5e02": "buy", "\u8dd1\u8d62\u884c\u4e1a": "buy",
    "\u6301\u6709": "hold", "\u4e2d\u6027": "hold", "\u4e0e\u5927\u5e02\u540c\u6b65": "hold",
    "\u51cf\u6301": "sell", "\u5356\u51fa": "sell", "\u843d\u540e\u5927\u5e02": "sell",
}


def _to_hk_code(symbol: str) -> str:
    """Convert '0700.HK' or '700.HK' → '00700' (5-digit zero-padded)."""
    code = symbol.split(".")[0].lstrip("0") or "0"
    return code.zfill(5)


def fetch_hk_analyst_ratings(symbol: str) -> list[dict]:
    """Fetch HK analyst ratings/target prices via akshare."""
    if not AKSHARE_AVAILABLE:
        return []

    events = []
    try:
        code = _to_hk_code(symbol)
        df = ak.stock_hk_profit_forecast_et(symbol=code)
        if df is None or df.empty:
            return events

        now = datetime.now()
        for _, row in df.iterrows():
            date_str = str(row.get("\u66f4\u65b0\u65e5\u671f", ""))
            if not date_str or date_str == "nan":
                continue

            try:
                update_date = datetime.strptime(date_str[:10], "%Y-%m-%d")
            except (ValueError, TypeError):
                continue

            days_ago = (now - update_date).days
            if days_ago > 30:
                continue

            firm = str(row.get("\u8bc1\u5238\u5546", ""))
            rating_cn = str(row.get("\u8bc4\u7ea7", ""))
            target = row.get("\u76ee\u6807\u4ef7")

            if rating_cn == "--" or not rating_cn:
                continue

            action = _HK_RATING_MAP.get(rating_cn, "other")
            # Only downgrades (sell-side) are medium signal; buy/hold are low
            signal = "medium" if action == "sell" and days_ago <= 7 else "low"

            event = {
                "type": "analyst_rating",
                "date": date_str[:10],
                "days_ago": days_ago,
                "source": _sanitize_text(firm, 50),
                "action": action,
                "rating": rating_cn,
                "signal_strength": signal,
            }
            if target and not pd.isna(target):
                event["target_price"] = float(target)

            events.append(event)

    except Exception:
        pass

    return events


# ---------------------------------------------------------------------------
# Source 6: HK news (akshare — East Money)
# ---------------------------------------------------------------------------

# Keywords that indicate actionable events (profit warning, earnings, buyback, etc.)
_HK_NEWS_HARD_KEYWORDS = [
    "\u76c8\u8b66", "\u76c8\u5229\u9884\u8b66", "\u76c8\u559c", "\u4e1a\u7ee9\u9884\u544a", "\u4e1a\u7ee9\u5feb\u62a5",
    "CEO", "\u8463\u4e8b\u957f", "\u8f9e\u4efb", "\u59d4\u4efb", "\u7ba1\u7406\u5c42\u53d8\u52a8",
    "\u56de\u8d2d", "\u589e\u6301", "\u51cf\u6301", "\u914d\u80a1", "\u4f9b\u80a1",
    "\u505c\u724c", "\u590d\u724c",
    "\u6536\u8d2d", "\u5408\u5e76", "\u79c1\u6709\u5316",
    "\u76d1\u7ba1", "\u7f5a\u6b3e", "\u5904\u7f5a", "\u8c03\u67e5",
]

_HK_NEWS_MEDIUM_KEYWORDS = [
    "\u8d22\u62a5", "\u4e1a\u7ee9", "\u4e2d\u671f\u4e1a\u7ee9", "\u5168\u5e74\u4e1a\u7ee9", "\u5b63\u5ea6\u4e1a\u7ee9",
    "\u5206\u7ea2", "\u6d3e\u606f", "\u80a1\u606f",
    "\u8bc4\u7ea7", "\u76ee\u6807\u4ef7", "\u4e0a\u8c03", "\u4e0b\u8c03",
    "\u7eb3\u5165", "\u5254\u9664", "\u6210\u5206\u80a1",
    "\u5927\u80a1\u4e1c", "\u80a1\u6743\u53d8\u52a8",
]


def fetch_hk_news(symbol: str) -> list[dict]:
    """Fetch and filter HK stock news via akshare.

    Only returns news matching actionable keywords to reduce noise.
    """
    if not AKSHARE_AVAILABLE:
        return []

    events = []
    try:
        code = _to_hk_code(symbol)
        df = ak.stock_news_em(symbol=code)
        if df is None or df.empty:
            return events

        now = datetime.now()
        seen_titles: set[str] = set()

        for _, row in df.iterrows():
            title = str(row.get("\u65b0\u95fb\u6807\u9898", ""))
            content = str(row.get("\u65b0\u95fb\u5185\u5bb9", ""))
            date_str = str(row.get("\u53d1\u5e03\u65f6\u95f4", ""))
            source = str(row.get("\u6587\u7ae0\u6765\u6e90", ""))

            if not title or title in seen_titles:
                continue
            seen_titles.add(title)

            # Parse date
            try:
                pub_date = datetime.strptime(date_str[:19], "%Y-%m-%d %H:%M:%S")
            except (ValueError, TypeError):
                continue

            days_ago = (now - pub_date).days
            if days_ago > 14:  # Only recent news
                continue

            # Keyword matching on title + content
            text = title + content
            is_hard = any(kw in text for kw in _HK_NEWS_HARD_KEYWORDS)
            is_medium = any(kw in text for kw in _HK_NEWS_MEDIUM_KEYWORDS)

            if not is_hard and not is_medium:
                continue  # Skip noise

            signal = "high" if is_hard else "medium"

            events.append({
                "type": "hk_news",
                "date": date_str[:10],
                "days_ago": days_ago,
                "title": _sanitize_text(title, 120),
                "source": _sanitize_text(source, 50),
                "signal_strength": signal,
                "is_hard_news": is_hard,
            })

    except Exception:
        pass

    return events[:10]  # Cap at 10 to avoid noise


# ---------------------------------------------------------------------------
# Source 7: Web search (Tavily — triggered by price anomaly / hard events)
# ---------------------------------------------------------------------------

_HARD_SEARCH_KEYWORDS = [
    # Management
    "ceo resign", "ceo depart", "cfo resign", "management change",
    # Regulatory/legal
    "fda reject", "fda approv", "regulatory", "investigation", "lawsuit", "banned",
    # Financial guidance
    "guidance cut", "guidance lower", "profit warning", "earnings miss",
    # M&A/structure
    "acquisition", "merger", "buyout", "privatiz", "delisted", "halt", "suspend",
    # Product/commercialization (major milestones)
    "major contract", "partnership", "fda clearance", "market approval",
    # Chinese-language keywords
    "\u8f9e\u4efb", "\u8f9e\u804c", "\u76c8\u5229\u9884\u8b66", "\u76c8\u8b66", "\u6536\u8d2d", "\u79c1\u6709\u5316", "\u505c\u724c", "\u76d1\u7ba1", "\u5904\u7f5a",
    "\u83b7\u6279", "\u6279\u51c6", "\u7981\u4ee4",
]

_MEDIUM_SEARCH_KEYWORDS = [
    # Analysts
    "downgrade", "upgrade", "target price", "analyst",
    # Shareholder returns
    "buyback", "repurchase", "dividend", "split",
    # Financial
    "earnings beat", "revenue growth", "guidance raise",
    # Product/commercialization (routine progress)
    "product launch", "new product", "partnership", "expansion",
    "commercializ", "customer win", "market entry", "rollout",
    # Chinese-language keywords
    "\u56de\u8d2d", "\u8bc4\u7ea7", "\u76ee\u6807\u4ef7", "\u4e1a\u7ee9", "\u5206\u7ea2",
    "\u65b0\u4ea7\u54c1", "\u53d1\u5e03", "\u4e0a\u7ebf", "\u5546\u4e1a\u5316", "\u5408\u4f5c", "\u7b7e\u7ea6", "\u6269\u5f20", "\u51fa\u6d77",
]


def _classify_search_result(text: str) -> tuple[str, bool]:
    """Classify a search result by keyword matching.

    Returns (signal_strength, is_hard_news).
    signal_strength: "none" / "medium" / "high"
    """
    text_lower = text.lower()
    for kw in _HARD_SEARCH_KEYWORDS:
        if kw in text_lower:
            return "high", True
    for kw in _MEDIUM_SEARCH_KEYWORDS:
        if kw in text_lower:
            return "medium", False
    return "none", False


def _build_search_queries(symbol: str, price_change_pct: float,
                          hard_events: list | None, company_name: str) -> list[str]:
    """Build search queries based on context."""
    queries = []
    is_hk = not _is_us_symbol(symbol)

    # Price-driven queries
    if abs(price_change_pct) > 5:
        direction = "drop" if price_change_pct < 0 else "surge"
        queries.append(f"{symbol} stock {direction} reason today")
    elif abs(price_change_pct) >= 3:
        queries.append(f"{symbol} stock news today")

    # Hard-event-driven queries
    if hard_events:
        for ev in hard_events:
            etype = ev.get("type", "")
            if etype == "earnings" and ev.get("days_until", 999) <= 0:
                q = datetime.now()
                quarter = (q.month - 1) // 3 + 1
                queries.append(f"{symbol} earnings results Q{quarter} {q.year}")
                break
            if etype == "sec_filing" and ev.get("form") == "8-K":
                details = ev.get("details", "")
                if details:
                    queries.append(f"{symbol} {_sanitize_text(details, 60)}")
                    break

    # HK: add Chinese query
    if is_hk and company_name:
        queries.append(f"{company_name} \u6700\u65b0\u6d88\u606f")
    elif is_hk:
        queries.append(f"{symbol} stock news today")

    # Fallback: if no queries generated but search was requested
    if not queries:
        queries.append(f"{symbol} stock news today")

    return queries


def fetch_web_events(symbol: str, price_change_pct: float = 0,
                     hard_events: list | None = None,
                     company_name: str = "") -> list[dict]:
    """Fetch web search events when triggered by price anomaly or hard events."""
    if not TAVILY_AVAILABLE:
        return []

    queries = _build_search_queries(symbol, price_change_pct, hard_events, company_name)

    results = []
    for query in queries[:2]:  # At most two searches
        try:
            raw = tavily_search(query, max_results=3, topic="news")
            results.extend(raw.get("results", []))
        except Exception:
            continue

    # Deduplicate by URL
    seen_urls: set[str] = set()
    events = []
    for r in results:
        url = r.get("url", "")
        if url in seen_urls:
            continue
        seen_urls.add(url)

        title = r.get("title", "")
        content = r.get("content", "")

        signal, is_hard = _classify_search_result(title + " " + content)
        if signal == "none":
            continue

        events.append({
            "type": "web_search",
            "title": _sanitize_text(title, 120),
            "summary": _sanitize_text(content, 200),
            "url": _sanitize_text(url, 300),
            "signal_strength": signal,
            "is_hard_news": is_hard,
        })

    return events[:6]  # Cap at 6 results


# ---------------------------------------------------------------------------
# Source 7.5: East Money individual-stock news (US and HK)
# ---------------------------------------------------------------------------

def fetch_em_news(symbol: str) -> list[dict]:
    """Fetch individual stock news from East Money (stock_news_em).

    Works for both US stocks (e.g. NVDA) and HK stocks (e.g. 0700.HK).
    Returns Chinese-language news from major Chinese financial-media sources.
    """
    if not AKSHARE_AVAILABLE:
        return []

    events = []
    try:
        # akshare uses HK 5-digit format for HK stocks
        query_sym = _to_hk_code(symbol) if not _is_us_symbol(symbol) else symbol
        df = ak.stock_news_em(symbol=query_sym)
        if df is None or df.empty:
            return events

        now = datetime.now()
        seen_titles: set[str] = set()

        for _, row in df.iterrows():
            title = str(row.get("\u65b0\u95fb\u6807\u9898", ""))
            content = str(row.get("\u65b0\u95fb\u5185\u5bb9", ""))
            date_str = str(row.get("\u53d1\u5e03\u65f6\u95f4", ""))
            source = str(row.get("\u6587\u7ae0\u6765\u6e90", ""))
            url = str(row.get("\u65b0\u95fb\u94fe\u63a5", ""))

            if not title or title in seen_titles:
                continue
            seen_titles.add(title)

            try:
                pub_date = datetime.strptime(date_str[:19], "%Y-%m-%d %H:%M:%S")
            except (ValueError, TypeError):
                continue

            days_ago = (now - pub_date).days
            if days_ago > 7:
                continue

            text = title + content
            is_hard = any(kw in text for kw in _HK_NEWS_HARD_KEYWORDS)
            is_medium = any(kw in text for kw in _HK_NEWS_MEDIUM_KEYWORDS)

            # Additional US-stock keywords
            if _is_us_symbol(symbol):
                is_hard = is_hard or any(kw.lower() in text.lower() for kw in _US_NEWS_HARD_KEYWORDS)
                is_medium = is_medium or any(kw.lower() in text.lower() for kw in _US_NEWS_MEDIUM_KEYWORDS)

            if not is_hard and not is_medium:
                continue

            events.append({
                "type": "em_news",
                "date": date_str[:10],
                "days_ago": days_ago,
                "title": _sanitize_text(title, 120),
                "summary": _sanitize_text(content, 200),
                "source": _sanitize_text(source, 50),
                "url": _sanitize_text(url, 300),
                "signal_strength": "high" if is_hard else "medium",
                "is_hard_news": is_hard,
            })

    except Exception:
        pass

    return events[:10]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def fetch_events(symbol: str, price_change_pct: float = 0,
                 force_search: bool = False, company_name: str = "") -> dict:
    """Fetch all events for a symbol and return structured JSON."""
    symbol = symbol.upper()

    earnings = fetch_earnings(symbol)

    if _is_us_symbol(symbol):
        # US: East Money Chinese news + yfinance English news + Finviz + SEC EDGAR
        em_news = fetch_em_news(symbol)
        us_news = fetch_us_news(symbol)
        analysts = fetch_analyst_ratings(symbol)
        insiders = fetch_insider_activity(symbol)
        sec_filings = fetch_sec_filings(symbol)
        all_events = earnings + em_news + us_news + analysts + insiders + sec_filings
    else:
        # HK: East Money news + analyst ratings
        em_news = fetch_em_news(symbol)
        hk_analysts = fetch_hk_analyst_ratings(symbol)
        all_events = earnings + em_news + hk_analysts

    # Classify each event as hard or explanatory
    for event in all_events:
        event["event_class"] = classify_event(event)

    # Decide whether to trigger web search
    should_search = (
        force_search
        or abs(price_change_pct) >= 3.0
        or any(e.get("event_class") == "hard" for e in all_events)
    )

    if should_search:
        hard_events = [e for e in all_events if e.get("event_class") == "hard"]
        web_events = fetch_web_events(symbol, price_change_pct, hard_events, company_name)
        for we in web_events:
            we["event_class"] = "hard" if we.get("is_hard_news") else "explanatory"
        all_events.extend(web_events)

    has_actionable = any(
        e.get("signal_strength") in ("medium", "high", "critical")
        for e in all_events
    )

    has_hard_event = any(e.get("event_class") == "hard" for e in all_events)

    return {
        "symbol": symbol,
        "events": all_events,
        "has_actionable": has_actionable,
        "has_hard_event": has_hard_event,
    }


def main():
    force = "--force" in sys.argv
    force_search = "--search" in sys.argv
    args_raw = [a for a in sys.argv[1:] if a not in ("--force", "--search")]

    # Parse --price-change and --company-name
    price_change_pct = 0.0
    company_name = ""
    positional = []
    i = 0
    while i < len(args_raw):
        if args_raw[i] == "--price-change" and i + 1 < len(args_raw):
            try:
                price_change_pct = float(args_raw[i + 1])
            except ValueError:
                pass
            i += 2
        elif args_raw[i] == "--company-name" and i + 1 < len(args_raw):
            company_name = args_raw[i + 1]
            i += 2
        else:
            positional.append(args_raw[i])
            i += 1

    if not positional:
        print(json.dumps({"error": "Usage: fetch_events.py SYMBOL [--force] [--search] [--price-change PCT] [--company-name NAME]"}))
        sys.exit(1)

    symbol = positional[0].upper()
    if not force:
        cached = load_cache("events", symbol)
        if cached is not None:
            print(json.dumps(cached, indent=2))
            return

    result = fetch_events(symbol, price_change_pct=price_change_pct,
                          force_search=force_search, company_name=company_name)
    print(json.dumps(result, indent=2))
    save_cache("events", symbol, result)


if __name__ == "__main__":
    main()
