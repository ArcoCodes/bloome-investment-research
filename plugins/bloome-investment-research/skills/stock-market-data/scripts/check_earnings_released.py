# /// script
# requires-python = ">=3.10"
# dependencies = ["yfinance", "httpx"]
# ///
"""Check if earnings have been released for a given symbol.

Usage: python scripts/check_earnings_released.py GOOGL
       python scripts/check_earnings_released.py 0700.HK
Output: JSON with symbol, released (bool), release_time, source.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta

from provider_runtime import require_provider_module

yf = require_provider_module("financials", "yfinance")

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False


def is_hk_symbol(symbol: str) -> bool:
    return symbol.upper().endswith(".HK")


def check_us_earnings_released(symbol: str) -> dict:
    """Check if US stock earnings have been released.

    Detection methods:
    1. yfinance: Check if latest quarterly results are recent (within 3 days)
    2. SEC EDGAR: Check for recent 10-Q/10-K/8-K filings
    """
    result = {
        "symbol": symbol,
        "released": False,
        "release_time": None,
        "source": None,
        "details": {},
    }

    # Method 1: yfinance - check latest earnings data
    try:
        ticker = yf.Ticker(symbol)

        # Check quarterly earnings history
        earnings = ticker.quarterly_earnings
        if earnings is not None and not earnings.empty:
            # Get the most recent quarter
            latest_date = earnings.index[-1]
            if hasattr(latest_date, "to_pydatetime"):
                latest_date = latest_date.to_pydatetime()
            elif isinstance(latest_date, str):
                latest_date = datetime.strptime(str(latest_date)[:10], "%Y-%m-%d")

            # If latest earnings data is very recent (within last 3 days), it just released
            if isinstance(latest_date, datetime):
                days_old = (datetime.now() - latest_date).days
                if days_old <= 3:
                    actual_eps = None
                    if "Earnings" in earnings.columns:
                        actual_eps = float(earnings["Earnings"].iloc[-1])

                    result["released"] = True
                    result["release_time"] = latest_date.strftime("%Y-%m-%d")
                    result["source"] = "yfinance_quarterly"
                    result["details"] = {
                        "period": str(earnings.index[-1]),
                        "actual_eps": actual_eps,
                    }
                    return result

        # Check income statement for recent filing
        income = ticker.quarterly_income_stmt
        if income is not None and not income.empty:
            latest_col = income.columns[0]
            if hasattr(latest_col, "to_pydatetime"):
                col_date = latest_col.to_pydatetime()
            else:
                col_date = datetime.strptime(str(latest_col)[:10], "%Y-%m-%d")

            days_old = (datetime.now() - col_date).days
            if days_old <= 90:  # Within last quarter
                result["details"]["latest_income_stmt_period"] = str(latest_col)[:10]

    except Exception as e:
        result["details"]["yfinance_error"] = str(e)

    # Method 2: SEC EDGAR - check recent filings
    if HTTPX_AVAILABLE:
        try:
            edgar_result = _check_sec_edgar(symbol)
            if edgar_result and edgar_result.get("released"):
                result.update(edgar_result)
                return result
        except Exception as e:
            result["details"]["edgar_error"] = str(e)

    return result


def _check_sec_edgar(symbol: str) -> dict | None:
    """Check SEC EDGAR for recent earnings-related filings (10-Q, 10-K, 8-K)."""
    try:
        # Get CIK from ticker
        url = f"https://efts.sec.gov/LATEST/search-index?q=%22{symbol}%22&dateRange=custom&startdt={(datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')}&enddt={datetime.now().strftime('%Y-%m-%d')}&forms=10-Q,10-K,8-K"

        resp = httpx.get(
            f"https://efts.sec.gov/LATEST/search-index?q=%22{symbol}%22&forms=10-Q,10-K&dateRange=custom&startdt={(datetime.now() - timedelta(days=5)).strftime('%Y-%m-%d')}",
            headers={
                "User-Agent": "NovarkBot research@novark.ai",
                "Accept": "application/json",
            },
            timeout=10.0,
        )

        if resp.status_code != 200:
            # Try EDGAR full-text search API
            resp = httpx.get(
                f"https://efts.sec.gov/LATEST/search-index?q=%22{symbol}%22&forms=10-Q,10-K",
                headers={
                    "User-Agent": "NovarkBot research@novark.ai",
                    "Accept": "application/json",
                },
                timeout=10.0,
            )

        if resp.status_code == 200:
            data = resp.json()
            hits = data.get("hits", {}).get("hits", [])
            for hit in hits[:5]:
                source = hit.get("_source", {})
                form_type = source.get("form_type", "")
                filed_date = source.get("file_date", "")

                if form_type in ("10-Q", "10-K") and filed_date:
                    filed = datetime.strptime(filed_date, "%Y-%m-%d")
                    if (datetime.now() - filed).days <= 5:
                        return {
                            "released": True,
                            "release_time": filed_date,
                            "source": f"sec_edgar_{form_type.lower()}",
                            "details": {
                                "form_type": form_type,
                                "filing_date": filed_date,
                            },
                        }

    except Exception:
        pass

    return None


def check_hk_earnings_released(symbol: str) -> dict:
    """Check if HK stock earnings/results have been released.

    Detection methods:
    1. HKEXnews: Check for recent results announcements
    2. yfinance: Check latest financial data
    """
    result = {
        "symbol": symbol,
        "released": False,
        "release_time": None,
        "source": None,
        "details": {},
    }

    code = symbol.upper().replace(".HK", "").lstrip("0") or "0"

    # Method 1: HKEX Disclosure
    if HTTPX_AVAILABLE:
        try:
            url = "https://www1.hkexnews.hk/search/titlesearch.xhtml"
            # Search for results announcements in the last 7 days
            from_date = (datetime.now() - timedelta(days=7)).strftime("%Y%m%d")
            to_date = datetime.now().strftime("%Y%m%d")

            params = {
                "lang": "EN",
                "stock": code.zfill(5),
                "category": "0",
                "subcategory": "-2",
                "title": "results",
                "from": from_date,
                "to": to_date,
                "sortDir": "d",  # descending (newest first)
                "sortByDate": "releaseDate",
                "rowRange": "1-5",
            }

            resp = httpx.get(url, params=params, timeout=15.0, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
            })

            if resp.status_code == 200:
                html = resp.text

                # Check for results-related announcements
                results_keywords = [
                    "annual results", "interim results", "quarterly results",
                    "final results", "preliminary results",
                    "\u5168\u5e74\u4e1a\u7ee9", "\u4e2d\u671f\u4e1a\u7ee9", "\u5b63\u5ea6\u4e1a\u7ee9",
                ]

                html_lower = html.lower()
                found_results = any(kw in html_lower for kw in results_keywords)

                if found_results:
                    # Extract date
                    date_matches = re.findall(r"(\d{4}/\d{2}/\d{2})", html)
                    release_date = None
                    if date_matches:
                        for date_str in date_matches:
                            try:
                                d = datetime.strptime(date_str, "%Y/%m/%d")
                                if (datetime.now() - d).days <= 7:
                                    release_date = d.strftime("%Y-%m-%d")
                                    break
                            except ValueError:
                                continue

                    result["released"] = True
                    result["release_time"] = release_date
                    result["source"] = "hkex_disclosure"
                    return result

        except Exception as e:
            result["details"]["hkex_error"] = str(e)

    # Method 2: yfinance fallback
    try:
        ticker = yf.Ticker(symbol)
        earnings = ticker.quarterly_earnings
        if earnings is not None and not earnings.empty:
            latest_date = earnings.index[-1]
            if hasattr(latest_date, "to_pydatetime"):
                latest_date = latest_date.to_pydatetime()
            elif isinstance(latest_date, str):
                latest_date = datetime.strptime(str(latest_date)[:10], "%Y-%m-%d")

            if isinstance(latest_date, datetime):
                days_old = (datetime.now() - latest_date).days
                if days_old <= 7:
                    result["released"] = True
                    result["release_time"] = latest_date.strftime("%Y-%m-%d")
                    result["source"] = "yfinance"
                    return result

    except Exception as e:
        result["details"]["yfinance_error"] = str(e)

    return result


def check_earnings_released(symbol: str) -> dict:
    """Main function: check if earnings have been released for any symbol."""
    symbol = symbol.upper()

    base = {
        "symbol": symbol,
        "market": "hk" if is_hk_symbol(symbol) else "us",
        "checked_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    if is_hk_symbol(symbol):
        result = check_hk_earnings_released(symbol)
    else:
        result = check_us_earnings_released(symbol)

    base.update(result)
    return base


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Usage: check_earnings_released.py SYMBOL"}))
        sys.exit(1)

    symbol = sys.argv[1].upper()
    result = check_earnings_released(symbol)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
