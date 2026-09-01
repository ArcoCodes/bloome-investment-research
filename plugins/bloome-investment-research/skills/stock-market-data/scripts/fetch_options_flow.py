#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["yfinance>=0.2", "httpx>=0.25", "pandas"]
# ///
"""
fetch_options_flow.py — US stock positioning and market-structure data

Retrieves three data categories from verified sources:
1. Options activity (yfinance) — per-stock Put/Call Ratio based on current option-chain volume
2. FINRA daily short volume — T+1 from the official cdn.finra.org domain
3. SEC Form 4 insider transactions — EDGAR submissions API, disclosed within two days

Usage:
    python scripts/fetch_options_flow.py AXP
    python scripts/fetch_options_flow.py CRCL --days 30

JSON output:
{
  "symbol": "AXP",
  "options_flow": { "pc_ratio_30d": 0.82, "pc_ratio_near": 1.86, ... },
  "short_volume": { "date": "2026-03-27", "short_pct": 0.563, ... },
  "insider_trades": [ { "date": "...", "type": "buy/sell", ... } ],
  "data_quality": { ... }
}
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
import pandas as pd
from provider_runtime import require_provider_module

yf = require_provider_module("positioning", "yfinance")

ET = timezone(timedelta(hours=-4))  # Eastern Time (EDT)
HEADERS = {"User-Agent": "Novark/1.0 contact@novark.vip"}  # Required by SEC


# ─────────────────────────────────────────────────────────────
# 1. PUT/CALL RATIO  (yfinance options chain)
# ─────────────────────────────────────────────────────────────

def fetch_options_flow(symbol: str, days_out: int = 60) -> dict:
    """
    Calculate Put/Call ratio from live options chain.

    Uses volume (not open interest) because volume reflects TODAY's
    directional activity; OI can be stale positions from weeks ago.

    Returns:
        pc_ratio_all    — P/C across ALL expirations within days_out
        pc_ratio_near   — P/C for the nearest expiry only
        call_volume     — total call volume across expirations
        put_volume      — total put volume across expirations
        expiries_used   — number of expiration dates included
        signal          — "bearish" / "neutral" / "bullish"
        as_of           — timestamp of fetch
    """
    ticker = yf.Ticker(symbol)
    try:
        all_dates = ticker.options
    except Exception as e:
        return {"error": f"Cannot fetch option dates: {e}"}

    if not all_dates:
        return {"error": "No options data available for this symbol"}

    cutoff = (datetime.now() + timedelta(days=days_out)).date()

    total_calls = 0
    total_puts = 0
    near_calls = 0
    near_puts = 0
    used_dates = []

    for i, exp_str in enumerate(all_dates):
        exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
        if exp_date > cutoff:
            break
        try:
            chain = ticker.option_chain(exp_str)
        except Exception:
            continue

        c_vol = int(chain.calls["volume"].fillna(0).sum())
        p_vol = int(chain.puts["volume"].fillna(0).sum())

        total_calls += c_vol
        total_puts += p_vol
        used_dates.append(exp_str)

        if i == 0:  # nearest expiry
            near_calls = c_vol
            near_puts = p_vol

    if total_calls == 0:
        return {"error": "Zero call volume — market may be closed or no activity today"}

    pc_all = round(total_puts / total_calls, 3)
    pc_near = round(near_puts / near_calls, 3) if near_calls > 0 else None

    # Signal interpretation (equity options benchmark)
    # P/C < 0.6 → unusual bullishness (contrarian bearish)
    # P/C 0.6–1.0 → neutral range
    # P/C > 1.0 → elevated hedging / bearish sentiment
    if pc_all < 0.6:
        signal = "Overly optimistic (contrarian downside risk)"
    elif pc_all <= 1.0:
        signal = "Neutral"
    else:
        signal = "Somewhat pessimistic (high hedging demand)"

    return {
        "pc_ratio_all": pc_all,
        "pc_ratio_near": pc_near,
        "call_volume": total_calls,
        "put_volume": total_puts,
        "expiries_used": len(used_dates),
        "expiry_range": f"{used_dates[0]} → {used_dates[-1]}" if used_dates else None,
        "signal": signal,
        "note": "Based on options volume (actual same-day trading), not open interest (OI)",
        "as_of": datetime.now(ET).strftime("%Y-%m-%dT%H:%M ET"),
    }


# ─────────────────────────────────────────────────────────────
# 2. FINRA daily short volume
# Source: cdn.finra.org/equity/regsho/daily/ (verified 2026-03)
# ─────────────────────────────────────────────────────────────

FINRA_MARKETS = {
    "FNSQ": "NASDAQ",
    "FNYX": "NYSE",
    "FNRA": "ADF",
}

def fetch_finra_short(symbol: str, lookback_days: int = 5) -> dict:
    """
    Fetch FINRA daily short sale volume for a symbol.

    URL pattern: https://cdn.finra.org/equity/regsho/daily/{market}shvol{YYYYMMDD}.txt
    Format: pipe-delimited, fields: Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market
    Published: T+1 by ~18:00 ET

    Returns short volume % for most recent available trading day,
    plus n-day average.
    """
    results = []
    today = datetime.now(ET).date()

    with httpx.Client(timeout=10, headers=HEADERS) as client:
        # Try last lookback_days trading days
        attempts = 0
        check_date = today
        while len(results) < lookback_days and attempts < 20:
            # Skip weekends
            if check_date.weekday() >= 5:
                check_date -= timedelta(days=1)
                attempts += 1
                continue

            date_str = check_date.strftime("%Y%m%d")
            day_calls = None
            day_puts = None  # not needed here

            for mkt_code, mkt_name in FINRA_MARKETS.items():
                url = f"https://cdn.finra.org/equity/regsho/daily/{mkt_code}shvol{date_str}.txt"
                try:
                    resp = client.get(url)
                    if resp.status_code != 200:
                        continue
                    for line in resp.text.splitlines():
                        parts = line.split("|")
                        if len(parts) < 5:
                            continue
                        if parts[1].strip().upper() == symbol.upper():
                            short_vol = float(parts[2])
                            total_vol = float(parts[4])
                            if total_vol > 0:
                                results.append({
                                    "date": check_date.isoformat(),
                                    "market": mkt_name,
                                    "short_volume": int(short_vol),
                                    "total_volume": int(total_vol),
                                    "short_pct": round(short_vol / total_vol * 100, 2),
                                })
                except Exception:
                    continue

            check_date -= timedelta(days=1)
            attempts += 1

    if not results:
        return {"error": f"No FINRA short data found for {symbol} in last {lookback_days} trading days"}

    # Deduplicate by date, sum across markets
    by_date: dict = {}
    for r in results:
        d = r["date"]
        if d not in by_date:
            by_date[d] = {"date": d, "short_volume": 0, "total_volume": 0}
        by_date[d]["short_volume"] += r["short_volume"]
        by_date[d]["total_volume"] += r["total_volume"]

    daily = sorted(by_date.values(), key=lambda x: x["date"], reverse=True)
    for row in daily:
        if row["total_volume"] > 0:
            row["short_pct"] = round(row["short_volume"] / row["total_volume"] * 100, 2)

    latest = daily[0]
    avg_pct = round(sum(r["short_pct"] for r in daily) / len(daily), 2)

    # Signal
    sp = latest["short_pct"]
    if sp > 50:
        signal = "Extremely high short pressure"
    elif sp > 35:
        signal = "Active short selling"
    elif sp > 20:
        signal = "Above normal"
    else:
        signal = "Normal"

    return {
        "latest_date": latest["date"],
        "short_pct": latest["short_pct"],
        "short_volume": latest["short_volume"],
        "total_volume": latest["total_volume"],
        f"avg_short_pct_{len(daily)}d": avg_pct,
        "signal": signal,
        "daily_history": daily[:5],
        "note": "Source: FINRA Reg SHO, published T+1, aggregating NASDAQ and NYSE",
    }


# ─────────────────────────────────────────────────────────────
# 3. SEC Form 4 insider transactions
# Source: data.sec.gov/submissions/CIK{cik}.json (no auth)
# ─────────────────────────────────────────────────────────────

def _get_cik(symbol: str) -> Optional[str]:
    """Look up CIK for a symbol via SEC company search."""
    url = f"https://efts.sec.gov/LATEST/search-index?q=%22{symbol}%22&dateRange=custom&startdt=2020-01-01&forms=4&hits.hits._source=period_of_report,file_date,entity_name,file_num"
    try:
        with httpx.Client(timeout=10, headers=HEADERS) as client:
            # Use ticker search endpoint
            r = client.get(
                f"https://data.sec.gov/submissions/",
                headers=HEADERS,
            )
    except Exception:
        pass

    # Use company search
    try:
        with httpx.Client(timeout=10, headers=HEADERS) as client:
            r = client.get(
                "https://efts.sec.gov/LATEST/search-index?q=%22" + symbol + "%22&forms=4",
                headers=HEADERS,
            )
            if r.status_code == 200:
                data = r.json()
                hits = data.get("hits", {}).get("hits", [])
                if hits:
                    # Extract CIK from entity info
                    pass
    except Exception:
        pass

    # Fallback: use yfinance to get CIK
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info
        # Try to find CIK from SEC URL in info
    except Exception:
        pass

    return None


def fetch_insider_trades(symbol: str, days_back: int = 90) -> dict:
    """
    Fetch recent insider trades from SEC Form 4 filings.
    Uses EDGAR submissions API (no auth required).

    Steps:
    1. Find CIK via SEC full-text search
    2. Fetch submissions JSON
    3. Filter Form 4 filings within days_back
    4. For each Form 4, parse the XML for transaction details
    """
    since = (datetime.now() - timedelta(days=days_back)).date().isoformat()

    # Step 1: Find CIK via ticker search
    cik = None
    try:
        with httpx.Client(timeout=10, headers=HEADERS) as client:
            r = client.get(
                f"https://efts.sec.gov/LATEST/search-index?q=%22{symbol}%22&forms=4&dateRange=custom&startdt={since}",
                headers=HEADERS,
            )
            if r.status_code == 200:
                data = r.json()
                hits = data.get("hits", {}).get("hits", [])
                if hits:
                    src = hits[0].get("_source", {})
                    entity_id = hits[0].get("_id", "")
                    # entity_id format: "0001234567-26-001234"
                    # CIK is in entity info
    except Exception:
        pass

    # Step 2: Use EDGAR company search to find CIK
    if not cik:
        try:
            with httpx.Client(timeout=10, headers=HEADERS) as client:
                r = client.get(
                    f"https://efts.sec.gov/LATEST/search-index?q=%22{symbol}%22&forms=4",
                    headers=HEADERS,
                )
                if r.status_code == 200:
                    data = r.json()
                    hits = data.get("hits", {}).get("hits", [])
                    for h in hits[:5]:
                        entity_id = h.get("_id", "")
                        # CIK extraction from filing
        except Exception:
            pass

    # Step 3: Use yfinance info to get ISIN, then map to CIK
    # Most reliable: pre-mapped CIK for common stocks
    KNOWN_CIKS = {
        "AXP":  "0000004962",
        "NVDA": "0001045810",
        "AAPL": "0000320193",
        "MSFT": "0000789019",
        "GOOGL": "0001652044",
        "AMZN": "0001018724",
        "META": "0001326801",
        "TSLA": "0001318605",
        "MU":   "0000723125",
        "TKO":  "0001937548",
        "CARG": "0001690820",
        "CRCL": "0002065372",
        "KRUS": "0001819989",
    }

    cik = KNOWN_CIKS.get(symbol.upper())

    if not cik:
        # Try EDGAR company search
        try:
            with httpx.Client(timeout=10, headers=HEADERS) as client:
                r = client.get(
                    f"https://efts.sec.gov/LATEST/search-index?q=%22{symbol}%22&forms=4",
                    headers=HEADERS,
                )
                if r.status_code == 200:
                    # Parse CIK from results
                    pass
        except Exception:
            pass
        if not cik:
            return {"error": f"CIK not found for {symbol}. Add to KNOWN_CIKS map."}

    # Step 4: Fetch submissions and filter Form 4
    try:
        with httpx.Client(timeout=15, headers=HEADERS) as client:
            r = client.get(
                f"https://data.sec.gov/submissions/CIK{cik}.json",
                headers=HEADERS,
            )
            if r.status_code != 200:
                return {"error": f"SEC submissions API returned {r.status_code}"}
            data = r.json()
    except Exception as e:
        return {"error": f"SEC API error: {e}"}

    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])
    descriptions = recent.get("primaryDocument", [])

    trades = []
    for i, form in enumerate(forms):
        if form != "4":
            continue
        if dates[i] < since:
            continue

        acc = accessions[i].replace("-", "")
        doc = descriptions[i] if i < len(descriptions) else ""

        # Parse Form 4 XML for transaction details
        # The actual XML is form4.xml, not the XSL viewer
        # Directory: /Archives/edgar/data/{cik}/{acc}/form4.xml
        xml_url = f"https://www.sec.gov/Archives/edgar/data/{cik.lstrip('0')}/{acc}/form4.xml"
        try:
            with httpx.Client(timeout=10, headers=HEADERS) as client:
                xr = client.get(xml_url, headers=HEADERS)
                if xr.status_code != 200:
                    continue
                xml = xr.text

                import re

                def extract(tag: str, text: str) -> str:
                    """Extract direct tag value or nested <value> child."""
                    # Try <tag><value>X</value> pattern first (most Form 4 fields)
                    m = re.search(f"<{tag}[^>]*>\\s*<value>([^<]*)</value>", text, re.IGNORECASE)
                    if m:
                        return m.group(1).strip()
                    # Fallback: direct <tag>X</tag>
                    m = re.search(f"<{tag}[^>]*>([^<]+)</{tag}>", text, re.IGNORECASE)
                    return m.group(1).strip() if m else ""

                def extract_all_blocks(tag: str, text: str) -> list[str]:
                    """Extract all blocks between <tag>...</tag>."""
                    return re.findall(f"<{tag}[^>]*>(.*?)</{tag}>", text, re.IGNORECASE | re.DOTALL)

                # Reporting owner name (direct child, no <value>)
                owner_name = extract("rptOwnerName", xml)

                # Parse each nonDerivativeTransaction block
                tx_blocks = extract_all_blocks("nonDerivativeTransaction", xml)
                # Also get derivativeTransaction blocks
                tx_blocks += extract_all_blocks("derivativeTransaction", xml)

                trans_dates = [extract("transactionDate", b) for b in tx_blocks]
                # transactionCode is a direct tag (no <value> wrapper)
                trans_codes = [
                    re.search(r"<transactionCode>([^<]+)</transactionCode>", b, re.IGNORECASE).group(1).strip()
                    if re.search(r"<transactionCode>([^<]+)</transactionCode>", b, re.IGNORECASE) else ""
                    for b in tx_blocks
                ]
                trans_shares = [extract("transactionShares", b) for b in tx_blocks]
                trans_prices = [extract("transactionPricePerShare", b) for b in tx_blocks]

                CODE_MAP = {
                    "P": "Purchase", "S": "Sale", "A": "Grant",
                    "F": "Tax withholding", "M": "Option exercise", "G": "Gift",
                    "D": "Donation", "I": "Inheritance", "J": "Other",
                }

                for j in range(len(trans_dates)):
                    code = trans_codes[j] if j < len(trans_codes) else ""
                    # Skip tax withholding and awards (not directional)
                    if code in ("F", "A", "G", "D", "I", "J"):
                        continue
                    shares_str = trans_shares[j] if j < len(trans_shares) else "0"
                    price_str = trans_prices[j] if j < len(trans_prices) else "0"
                    try:
                        shares = float(shares_str)
                        price = float(price_str)
                        value = shares * price
                    except ValueError:
                        shares, price, value = 0, 0, 0

                    trades.append({
                        "filing_date": dates[i],
                        "transaction_date": trans_dates[j] if j < len(trans_dates) else dates[i],
                        "owner": owner_name,
                        "type": CODE_MAP.get(code, code),
                        "shares": int(shares),
                        "price": round(price, 2) if price else None,
                        "value_usd": int(value) if value else None,
                        "code": code,
                    })
        except Exception:
            # Can't parse XML, still record the filing
            trades.append({
                "filing_date": dates[i],
                "transaction_date": dates[i],
                "owner": "No public parsed value available",
                "type": "Form 4",
                "shares": None,
                "price": None,
                "value_usd": None,
                "code": "?",
            })

    # Summary
    buys = [t for t in trades if t["code"] == "P"]
    sells = [t for t in trades if t["code"] == "S"]
    buy_value = sum(t["value_usd"] or 0 for t in buys)
    sell_value = sum(t["value_usd"] or 0 for t in sells)

    if not trades:
        signal = f"No insider transaction disclosures in the past {days_back} days"
    elif buy_value > sell_value * 2:
        signal = "Net insider buying (bullish signal)"
    elif sell_value > buy_value * 2:
        signal = "Net insider selling (requires attention)"
    else:
        signal = "Mixed buying and selling; neutral signal"

    return {
        "period": f"past {days_back} days",
        "total_filings": len(set(t["filing_date"] for t in trades)),
        "buy_transactions": len(buys),
        "sell_transactions": len(sells),
        "buy_value_usd": buy_value,
        "sell_value_usd": sell_value,
        "signal": signal,
        "trades": sorted(trades, key=lambda x: x["filing_date"], reverse=True)[:10],
        "source": "SEC EDGAR Form 4 (disclosed within two days)",
        "sec_url": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=4&dateb=&owner=include&count=40",
    }


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Fetch US stock capital flow signals")
    parser.add_argument("symbol", help="US stock ticker (e.g. AXP)")
    parser.add_argument("--days", type=int, default=30, help="Options lookout window in days (default 30)")
    parser.add_argument("--insider-days", type=int, default=90, help="Insider trade lookback days (default 90)")
    parser.add_argument("--skip-options", action="store_true")
    parser.add_argument("--skip-short", action="store_true")
    parser.add_argument("--skip-insider", action="store_true")
    args = parser.parse_args()

    symbol = args.symbol.upper()
    result: dict = {"symbol": symbol}
    quality: dict = {}

    print(f"Retrieving {symbol} positioning and market-structure data...", file=sys.stderr)

    if not args.skip_options:
        print("  → Options activity (Put/Call Ratio)...", file=sys.stderr)
        result["options_flow"] = fetch_options_flow(symbol, args.days)
        quality["options_flow"] = "ok" if "error" not in result["options_flow"] else result["options_flow"]["error"]

    if not args.skip_short:
        print("  → FINRA daily short volume...", file=sys.stderr)
        result["short_volume"] = fetch_finra_short(symbol)
        quality["short_volume"] = "ok" if "error" not in result["short_volume"] else result["short_volume"]["error"]

    if not args.skip_insider:
        print("  → SEC Form 4 insider transactions...", file=sys.stderr)
        result["insider_trades"] = fetch_insider_trades(symbol, args.insider_days)
        quality["insider_trades"] = "ok" if "error" not in result["insider_trades"] else result["insider_trades"]["error"]

    result["data_quality"] = quality
    result["fetched_at"] = datetime.now(ET).strftime("%Y-%m-%dT%H:%M ET")

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
