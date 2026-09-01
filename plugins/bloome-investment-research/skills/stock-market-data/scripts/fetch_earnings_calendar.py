# /// script
# requires-python = ">=3.10"
# dependencies = ["yfinance", "httpx"]
# ///
"""Fetch next earnings date for a given symbol (US and HK stocks).

Usage: python scripts/fetch_earnings_calendar.py GOOGL
       python scripts/fetch_earnings_calendar.py 0700.HK
Output: JSON with symbol, next_earnings_date, confirmed, source.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime

from provider_runtime import ProviderUnavailable, require_provider_module, route

yf = require_provider_module("earnings_calendar", "yfinance")

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False


def is_hk_symbol(symbol: str) -> bool:
    return symbol.upper().endswith(".HK")


def fetch_earnings_yfinance(symbol: str) -> dict | None:
    """Fetch earnings date from yfinance calendar."""
    try:
        ticker = yf.Ticker(symbol)
        calendar = ticker.calendar

        if calendar is None:
            return None

        earnings_date = None
        eps_estimate = None

        if isinstance(calendar, dict):
            if "Earnings Date" in calendar:
                dates = calendar["Earnings Date"]
                if dates:
                    import pandas as pd
                    earnings_date = pd.to_datetime(
                        dates[0] if isinstance(dates, list) else dates
                    )
            if "Earnings Average" in calendar:
                eps_estimate = float(calendar["Earnings Average"])
        elif hasattr(calendar, "empty") and not calendar.empty:
            import pandas as pd
            if "Earnings Date" in calendar.columns:
                earnings_date = pd.to_datetime(calendar["Earnings Date"].iloc[0])

        if earnings_date is None:
            return None

        # Make naive
        if hasattr(earnings_date, "tzinfo") and earnings_date.tzinfo is not None:
            earnings_date = earnings_date.tz_localize(None)

        result = {
            "next_earnings_date": earnings_date.strftime("%Y-%m-%d"),
            "confirmed": True,
            "source": "yfinance",
        }
        if eps_estimate is not None:
            result["eps_estimate"] = eps_estimate

        return result

    except Exception as e:
        return {"error": f"yfinance: {e}", "source": "yfinance"}


def fetch_earnings_hkex(symbol: str) -> dict | None:
    """Fetch next earnings/results announcement date for HK stocks from HKEX."""
    if not HTTPX_AVAILABLE:
        return None

    # Extract stock code number (e.g., "0700" from "0700.HK")
    code = symbol.upper().replace(".HK", "").lstrip("0") or "0"

    try:
        # Search HKEX disclosure for financial results announcements
        url = "https://www1.hkexnews.hk/search/titlesearch.xhtml"
        params = {
            "lang": "EN",
            "stock": code.zfill(5),
            "category": "0",  # All categories
            "subcategory": "-2",
            "title": "results",
            "from": datetime.now().strftime("%Y%m%d"),
            "to": "",
            "sortDir": "a",  # ascending by date
            "sortByDate": "releaseDate",
            "rowRange": "1-5",
        }

        resp = httpx.get(url, params=params, timeout=15.0, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        })

        if resp.status_code != 200:
            return None

        # Try to extract date from response
        html = resp.text
        date_matches = re.findall(r"(\d{4}/\d{2}/\d{2})", html)
        if date_matches:
            # First future date
            for date_str in date_matches:
                try:
                    d = datetime.strptime(date_str, "%Y/%m/%d")
                    if d >= datetime.now():
                        return {
                            "next_earnings_date": d.strftime("%Y-%m-%d"),
                            "confirmed": False,
                            "source": "hkex_disclosure",
                        }
                except ValueError:
                    continue

        return None

    except Exception:
        return None


def fetch_earnings_eastmoney(symbol: str) -> dict | None:
    """Fetch earnings date from East Money for HK stocks."""
    if not HTTPX_AVAILABLE:
        return None

    code = symbol.upper().replace(".HK", "")

    try:
        # East Money HK stock financial calendar API
        url = f"https://emweb.securities.eastmoney.com/PC_HKF10/FinancialAnalysis/FinancialAnalysisAjax"
        params = {
            "code": f"0{code}",
            "type": "web",
        }

        resp = httpx.get(url, params=params, timeout=15.0, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        })

        if resp.status_code != 200:
            return None

        data = resp.json()
        if not data:
            return None

        # Try to extract latest report date info
        # East Money returns historical report dates; we can estimate next one
        if "zycwzb" in data and data["zycwzb"]:
            reports = data["zycwzb"]
            if reports:
                latest = reports[0]
                report_date = latest.get("REPORTDATE", "")
                if report_date:
                    return {
                        "latest_report_date": report_date[:10],
                        "confirmed": False,
                        "source": "eastmoney",
                        "note": "Latest available report date. Next earnings date estimated.",
                    }

        return None

    except Exception:
        return None


def fetch_earnings_calendar(symbol: str) -> dict:
    """Main function: fetch earnings calendar for any symbol."""
    symbol = symbol.upper()

    result = {
        "symbol": symbol,
        "market": "hk" if is_hk_symbol(symbol) else "us",
        "next_earnings_date": None,
        "confirmed": False,
        "source": None,
        "checked_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    def operation(provider: str) -> dict:
        if provider == "yfinance":
            candidate = fetch_earnings_yfinance(symbol)
        elif provider == "hkex" and is_hk_symbol(symbol):
            candidate = fetch_earnings_hkex(symbol)
        elif provider == "eastmoney" and is_hk_symbol(symbol):
            candidate = fetch_earnings_eastmoney(symbol)
        else:
            raise ProviderUnavailable("provider does not cover this market")
        if not candidate or candidate.get("error"):
            raise ProviderUnavailable((candidate or {}).get("error", "no earnings date"))
        if not candidate.get("next_earnings_date") and not candidate.get("latest_report_date"):
            raise ProviderUnavailable("no dated earnings record")
        return candidate

    try:
        routed = route("earnings_calendar", operation)
        result.update(routed.value)
        source_urls = {
            "yfinance": f"https://finance.yahoo.com/quote/{symbol}",
            "hkex": "https://www1.hkexnews.hk/search/titlesearch.xhtml",
            "eastmoney": "https://emweb.securities.eastmoney.com/PC_HKF10/",
        }
        result["_routing"] = {
            "provider": routed.provider,
            "attempts": list(routed.attempts),
            "fallback_used": routed.fallback_used,
            "source_url": source_urls.get(routed.provider),
        }
    except ProviderUnavailable as exc:
        result["error"] = str(exc)
        result["_routing"] = {
            "provider": None,
            "attempts": [],
            "fallback_used": False,
        }
    return result


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Usage: fetch_earnings_calendar.py SYMBOL"}))
        sys.exit(1)

    symbol = sys.argv[1].upper()
    result = fetch_earnings_calendar(symbol)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
