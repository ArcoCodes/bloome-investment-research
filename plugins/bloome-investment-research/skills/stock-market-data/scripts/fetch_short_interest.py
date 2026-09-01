# /// script
# requires-python = ">=3.10"
# dependencies = ["yfinance", "httpx"]
# ///
"""Fetch short selling data for US and HK stocks.

US: Uses yfinance for short interest ratio, short % of float, etc.
HK: Scrapes HKEX daily short selling turnover report.

Usage:
    python scripts/fetch_short_interest.py GOOGL
    python scripts/fetch_short_interest.py 0700.HK

Output: JSON with short selling metrics.
"""

from __future__ import annotations

import json
import re
import sys

import httpx
from provider_runtime import require_provider_module

yf = require_provider_module("positioning", "yfinance")

from cache_utils import load_cache, save_cache


HKEX_SHORT_SELL_URL = (
    "https://www.hkex.com.hk/Market-Data/Statistics/Securities-Market/"
    "Short-Selling-Turnover-Today/Short-Selling-Turnover-(Main-Board)"
    "-up-to-day-close-today?sc_lang=en"
)

HKEX_DS_LIST_PAGE = (
    "https://www.hkex.com.hk/Services/Trading/Securities/Securities-Lists/"
    "Designated-Securities-Eligible-for-Short-Selling?sc_lang=en"
)


def _check_hk_shortable(code: str) -> bool | None:
    """Check if a HK stock is on the HKEX designated short selling list.

    Downloads the latest CSV from HKEX. Returns True/False, or None on error.
    The CSV has columns: No., Stock Code, Stock Short Name, Currency, Type, ...
    """
    try:
        # First get the list page to find the latest CSV URL
        r = httpx.get(
            HKEX_DS_LIST_PAGE,
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0"},
            follow_redirects=True,
        )
        if r.status_code != 200:
            return None

        # Find the latest CSV download link
        csv_match = re.search(
            r'href="(/-/media/[^"]*ds_list\d{8}\.csv)"', r.text
        )
        if not csv_match:
            return None

        csv_url = "https://www.hkex.com.hk" + csv_match.group(1)
        r2 = httpx.get(csv_url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        if r2.status_code != 200:
            return None

        # Parse CSV: Stock Code is 2nd column (index 1)
        code_int = int(code)
        for line in r2.text.split("\n"):
            parts = line.split(",")
            if len(parts) >= 2:
                try:
                    if int(parts[1].strip()) == code_int:
                        return True
                except ValueError:
                    continue
        return False
    except Exception:
        return None


def fetch_us_short(symbol: str) -> dict:
    """Fetch US stock short interest from yfinance."""
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info

        shares_short = info.get("sharesShort")
        if shares_short is None:
            return {"symbol": symbol, "market": "US", "error": "no short data available"}

        return {
            "symbol": symbol,
            "market": "US",
            "shares_short": shares_short,
            "shares_short_prior_month": info.get("sharesShortPriorMonth"),
            "short_ratio": info.get("shortRatio"),
            "short_percent_of_float": info.get("shortPercentOfFloat"),
            "float_shares": info.get("floatShares"),
            "date_short_interest": info.get("dateShortInterest"),
        }
    except Exception as e:
        return {"symbol": symbol, "market": "US", "error": str(e)}


def fetch_hk_short(symbol: str) -> dict:
    """Fetch HK stock short selling turnover from HKEX daily report.

    HKEX publishes daily short selling turnover for all designated securities.
    This gives us today's short selling shares and value, not cumulative short interest.
    """
    # Convert yfinance format (0700.HK) to HKEX code (700)
    code = symbol.replace(".HK", "").lstrip("0") or "0"

    try:
        r = httpx.get(
            HKEX_SHORT_SELL_URL,
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0"},
            follow_redirects=True,
        )
        if r.status_code != 200:
            return {"symbol": symbol, "market": "HK", "error": f"HKEX returned {r.status_code}"}

        text = r.text
        # Extract pre-formatted content
        pre_match = re.search(r"<pre[^>]*>(.*?)</pre>", text, re.DOTALL)
        if not pre_match:
            return {"symbol": symbol, "market": "HK", "error": "could not parse HKEX page"}

        clean = re.sub(r"<[^>]+>", "", pre_match.group(1))

        # Parse the target stock line
        # Format: "    700  TENCENT                3,219,500  1,775,065,850"
        # Use \s+ between name and numbers (non-greedy name won't eat digits)
        pattern = rf"^\s*{re.escape(code)}\s+(.+?)\s+([\d,]+)\s+([\d,]+)\s*$"
        stock_data = None
        for line in clean.split("\n"):
            m = re.match(pattern, line)
            if m:
                stock_data = {
                    "name": m.group(1).strip(),
                    "short_shares": int(m.group(2).replace(",", "")),
                    "short_value_hkd": int(m.group(3).replace(",", "")),
                }
                break

        if not stock_data:
            # Daily report unavailable (before market close, weekend, holiday)
            # Fall back to checking the designated short selling list
            shortable = _check_hk_shortable(code)
            result = {
                "symbol": symbol,
                "market": "HK",
                "note": "daily short selling report not yet available, checked designated list instead",
            }
            if shortable is None:
                result["shortable"] = None
                result["note"] = "could not verify: daily report unavailable and designated list check failed"
            else:
                result["shortable"] = shortable
            return result

        # Also get total market short selling for context
        # Look for "All Designated Securities" section totals (last HKD total)
        total_shares_m = re.findall(
            r"Short Selling Turnover Total Shares \(SH\)\s*:\s*([\d,]+)", clean
        )
        total_value_m = re.findall(
            r"Short Selling Turnover Total Value \(\$\)\s*:\s*HKD\s+([\d,]+)", clean
        )

        result = {
            "symbol": symbol,
            "market": "HK",
            "shortable": True,
            "name": stock_data["name"],
            "short_selling_shares_today": stock_data["short_shares"],
            "short_selling_value_hkd_today": stock_data["short_value_hkd"],
        }

        # Sections in report: (A) excl ETP, (B) ETP only, (C) all combined, then non-designated
        # Take the max value — the "all combined" total is always >= any subset
        if total_shares_m:
            result["market_total_short_shares"] = max(
                int(v.replace(",", "")) for v in total_shares_m
            )
        if total_value_m:
            total_val = max(int(v.replace(",", "")) for v in total_value_m)
            result["market_total_short_value_hkd"] = total_val
            if total_val > 0:
                result["pct_of_market_short"] = round(
                    stock_data["short_value_hkd"] / total_val * 100, 2
                )

        # Extract overall short selling as % of total market turnover
        pct_m = re.search(
            r"Short Selling of all Designated Securities as % total turnover\s*:\s*(\d+)%",
            clean,
        )
        if pct_m:
            result["market_short_pct_of_turnover"] = int(pct_m.group(1))

        return result

    except Exception as e:
        return {"symbol": symbol, "market": "HK", "error": str(e)}


def main():
    force = "--force" in sys.argv
    symbols = [a for a in sys.argv[1:] if a != "--force"]
    if not symbols:
        print(json.dumps({"error": "Usage: fetch_short_interest.py SYMBOL [SYMBOL ...] [--force]"}))
        sys.exit(1)

    result = {}
    for sym in symbols:
        sym_upper = sym.upper()
        if not force:
            cached = load_cache("short_interest", sym_upper)
            if cached is not None:
                result[sym_upper] = cached
                continue
        if sym_upper.endswith(".HK"):
            data = fetch_hk_short(sym_upper)
        else:
            data = fetch_us_short(sym_upper)
        result[sym_upper] = data
        if "error" not in data:
            save_cache("short_interest", sym_upper, data)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
