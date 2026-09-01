# /// script
# requires-python = ">=3.10"
# dependencies = ["yfinance"]
# ///
"""Fetch fundamental data for a given symbol.

Retrieves revenue growth, profit margins, valuation multiples (PE/PS/PB),
institutional holdings changes, and sector context via yfinance.

Usage: python scripts/fetch_fundamentals.py AAPL
Output: JSON with fundamentals assessment.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone

from cache_utils import load_cache, save_cache
from data_contract import attach_contract, leaf_paths, source_record, utc_now
from provider_runtime import ProviderUnavailable, require_provider_module, route
from security_master import build_security_identity
from snapshot_store import save_snapshot


def _fetch_yahoo_raw(provider: str, symbol: str) -> dict:
    if provider != "yfinance":
        raise ProviderUnavailable(f"financials adapter not implemented for {provider}")
    try:
        yf = require_provider_module("financials", provider)
        ticker = yf.Ticker(symbol)
        return {"ticker": ticker, "info": ticker.info}
    except Exception as exc:
        raise ProviderUnavailable(str(exc)) from exc


def _sanitize_text(text: str, max_len: int = 100) -> str:
    """Sanitize external text to prevent prompt injection.

    Strips newlines, control chars, and truncates to max_len.
    Only allows letters, digits, basic punctuation, and spaces.
    """
    if not isinstance(text, str):
        return ""
    cleaned = re.sub(r'[\n\r\t\x00-\x1f\x7f]', ' ', text)
    cleaned = re.sub(r'[^\w\s\.,;:\-\(\)\/&\'\"@#%\+]', '', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned[:max_len]


def _safe_get(info: dict, key: str, default=None):
    """Get value from info dict, returning default if missing or None."""
    val = info.get(key)
    return val if val is not None else default


def _pct(value) -> float | None:
    """Convert to percentage, return None if not a number."""
    if value is None:
        return None
    try:
        return round(float(value) * 100, 2)
    except (TypeError, ValueError):
        return None


def _safe_round(value, decimals: int = 2) -> float | None:
    """Round a value safely, returning None if not numeric (handles strings like 'N/A', 'Infinity')."""
    if value is None:
        return None
    try:
        f = float(value)
        if f != f or abs(f) == float("inf"):  # NaN or Inf
            return None
        return round(f, decimals)
    except (TypeError, ValueError):
        return None


def fetch_fundamentals(symbol: str) -> dict:
    """Fetch fundamental data for a symbol."""
    try:
        routed = route("financials", lambda provider: _fetch_yahoo_raw(provider, symbol))
        ticker = routed.value["ticker"]
        info = routed.value["info"]

        if not info or info.get("quoteType") == "NONE":
            return {"symbol": symbol, "error": "No data available"}

        # --- Growth ---
        revenue_growth = _pct(_safe_get(info, "revenueGrowth"))
        earnings_growth = _pct(_safe_get(info, "earningsGrowth"))
        quarterly_revenue_growth = _pct(_safe_get(info, "revenueQuarterlyGrowth"))
        quarterly_earnings_growth = _pct(_safe_get(info, "earningsQuarterlyGrowth"))

        # --- Profitability ---
        gross_margin = _pct(_safe_get(info, "grossMargins"))
        operating_margin = _pct(_safe_get(info, "operatingMargins"))
        profit_margin = _pct(_safe_get(info, "profitMargins"))
        roe = _pct(_safe_get(info, "returnOnEquity"))
        roa = _pct(_safe_get(info, "returnOnAssets"))

        # --- Valuation ---
        pe_trailing = _safe_get(info, "trailingPE")
        pe_forward = _safe_get(info, "forwardPE")
        ps_ratio = _safe_get(info, "priceToSalesTrailing12Months")
        pb_ratio = _safe_get(info, "priceToBook")
        peg_ratio = _safe_get(info, "pegRatio")
        ev_ebitda = _safe_get(info, "enterpriseToEbitda")
        ev_revenue = _safe_get(info, "enterpriseToRevenue")

        # --- Financial health ---
        total_debt = _safe_get(info, "totalDebt")
        total_cash = _safe_get(info, "totalCash")
        free_cashflow = _safe_get(info, "freeCashflow")
        operating_cashflow = _safe_get(info, "operatingCashflow")
        debt_to_equity = _safe_get(info, "debtToEquity")
        current_ratio = _safe_get(info, "currentRatio")

        # --- Institutional ---
        inst_pct = _pct(_safe_get(info, "heldPercentInstitutions"))
        insider_pct = _pct(_safe_get(info, "heldPercentInsiders"))

        # Try to get institutional holders for recent changes
        inst_holders_change = None
        try:
            inst_holders = ticker.institutional_holders
            if inst_holders is not None and not inst_holders.empty and "Date Reported" in inst_holders.columns:
                recent = inst_holders.head(5)
                inst_holders_change = []
                for _, row in recent.iterrows():
                    entry = {
                        "holder": _sanitize_text(str(row.get("Holder", "")), 80),
                        "shares": int(row["Shares"]) if "Shares" in row else None,
                        "date_reported": str(row["Date Reported"].date()) if "Date Reported" in row else None,
                        "pct_out": round(float(row["% Out"]) * 100, 2) if "% Out" in row and row["% Out"] else None,
                    }
                    inst_holders_change.append(entry)
        except Exception:
            pass

        # --- Company info ---
        sector = _sanitize_text(_safe_get(info, "sector", ""), 50)
        industry = _sanitize_text(_safe_get(info, "industry", ""), 50)
        market_cap = _safe_get(info, "marketCap")
        company_name = _sanitize_text(
            _safe_get(info, "longName") or _safe_get(info, "shortName", symbol), 80
        )

        # --- Qualitative assessment ---
        assessment = _assess_fundamentals(
            revenue_growth=revenue_growth,
            earnings_growth=earnings_growth,
            profit_margin=profit_margin,
            operating_margin=operating_margin,
            pe_forward=pe_forward,
            peg_ratio=peg_ratio,
            debt_to_equity=debt_to_equity,
            free_cashflow=free_cashflow,
            roe=roe,
        )

        result = {
            "symbol": symbol,
            "company_name": company_name,
            "sector": sector,
            "industry": industry,
            "market_cap": market_cap,
            "growth": {
                "revenue_growth_yoy": revenue_growth,
                "earnings_growth_yoy": earnings_growth,
                "quarterly_revenue_growth": quarterly_revenue_growth,
                "quarterly_earnings_growth": quarterly_earnings_growth,
            },
            "profitability": {
                "gross_margin": gross_margin,
                "operating_margin": operating_margin,
                "profit_margin": profit_margin,
                "roe": roe,
                "roa": roa,
            },
            "valuation": {
                "pe_trailing": _safe_round(pe_trailing),
                "pe_forward": _safe_round(pe_forward),
                "ps_ratio": _safe_round(ps_ratio),
                "pb_ratio": _safe_round(pb_ratio),
                "peg_ratio": _safe_round(peg_ratio),
                "ev_ebitda": _safe_round(ev_ebitda),
                "ev_revenue": _safe_round(ev_revenue),
            },
            "financial_health": {
                "total_debt": total_debt,
                "total_cash": total_cash,
                "free_cashflow": free_cashflow,
                "operating_cashflow": operating_cashflow,
                "debt_to_equity": _safe_round(debt_to_equity),
                "current_ratio": _safe_round(current_ratio),
            },
            "ownership": {
                "institutional_pct": inst_pct,
                "insider_pct": insider_pct,
                "top_institutional_holders": inst_holders_change,
            },
            "assessment": assessment,
            "fetched_at": utc_now(),
        }
        identity = build_security_identity(
            symbol,
            company_name=company_name,
            provider_symbols={routed.provider: symbol},
        )
        result["identity"] = identity
        source = source_record(
            routed.provider,
            dataset="financials",
            source_url=f"https://finance.yahoo.com/quote/{symbol}",
            effective_at=info.get("mostRecentQuarter"),
            published_at=None,
            retrieved_at=result["fetched_at"],
            adjustment="not_applicable",
            quality="aggregator",
            fields=leaf_paths(result),
        )
        contracted = attach_contract(
            result,
            sources=[source],
            provider_attempts=routed.attempts,
            fallback_used=routed.fallback_used,
        )
        save_snapshot("financials", identity["security"]["security_id"], contracted)
        return contracted
    except Exception as e:
        return {"symbol": symbol, "error": str(e)}


def _assess_fundamentals(
    revenue_growth, earnings_growth, profit_margin, operating_margin,
    pe_forward, peg_ratio, debt_to_equity, free_cashflow, roe,
) -> dict:
    """Generate a qualitative assessment of fundamentals.

    Returns scores (0-100) for each dimension and an overall rating.
    """
    scores = {}
    flags = []

    # Growth score (0-100)
    growth_score = 50  # baseline
    if revenue_growth is not None:
        if revenue_growth > 30:
            growth_score = 90
        elif revenue_growth > 15:
            growth_score = 75
        elif revenue_growth > 5:
            growth_score = 60
        elif revenue_growth > 0:
            growth_score = 45
        elif revenue_growth > -10:
            growth_score = 30
        else:
            growth_score = 15
            flags.append("revenue_declining")

    if earnings_growth is not None and earnings_growth > 20:
        growth_score = min(100, growth_score + 10)
    scores["growth"] = growth_score

    # Profitability score (0-100)
    prof_score = 50
    if profit_margin is not None:
        if profit_margin > 25:
            prof_score = 90
        elif profit_margin > 15:
            prof_score = 75
        elif profit_margin > 5:
            prof_score = 55
        elif profit_margin > 0:
            prof_score = 40
        else:
            prof_score = 20
            flags.append("unprofitable")
    if roe is not None and roe > 20:
        prof_score = min(100, prof_score + 10)
    scores["profitability"] = prof_score

    # Valuation score (0-100, higher = cheaper / more attractive)
    val_score = 50
    if pe_forward is not None:
        if pe_forward < 0:
            val_score = 20
            flags.append("negative_earnings")
        elif pe_forward < 12:
            val_score = 85
        elif pe_forward < 20:
            val_score = 70
        elif pe_forward < 30:
            val_score = 55
        elif pe_forward < 50:
            val_score = 40
        else:
            val_score = 25
            flags.append("high_valuation")

    if peg_ratio is not None:
        if 0 < peg_ratio < 1:
            val_score = min(100, val_score + 15)
        elif peg_ratio > 3:
            val_score = max(0, val_score - 10)
    scores["valuation"] = val_score

    # Financial health score (0-100)
    health_score = 60
    if debt_to_equity is not None:
        if debt_to_equity < 0:
            # Negative equity (liabilities > assets) — structurally weak
            health_score = 25
            flags.append("negative_equity")
        elif debt_to_equity < 30:
            health_score = 85
        elif debt_to_equity < 80:
            health_score = 70
        elif debt_to_equity < 150:
            health_score = 50
        else:
            health_score = 30
            flags.append("high_debt")

    if free_cashflow is not None:
        if free_cashflow > 0:
            health_score = min(100, health_score + 10)
        else:
            health_score = max(0, health_score - 15)
            flags.append("negative_fcf")
    scores["financial_health"] = health_score

    # Overall (weighted)
    overall = (
        scores["growth"] * 0.30
        + scores["profitability"] * 0.25
        + scores["valuation"] * 0.25
        + scores["financial_health"] * 0.20
    )
    scores["overall"] = round(overall, 1)

    # Rating label
    if overall >= 75:
        rating = "strong"
    elif overall >= 60:
        rating = "good"
    elif overall >= 45:
        rating = "fair"
    elif overall >= 30:
        rating = "weak"
    else:
        rating = "poor"

    return {
        "scores": scores,
        "rating": rating,
        "flags": flags,
    }


def main():
    force = "--force" in sys.argv
    symbols = [a for a in sys.argv[1:] if a != "--force"]
    if not symbols:
        print(json.dumps({"error": "Usage: fetch_fundamentals.py SYMBOL [SYMBOL ...] [--force]"}))
        sys.exit(1)

    results = {}
    for sym in symbols:
        sym = sym.upper() if not sym.endswith(".HK") else sym.upper()
        if not force:
            cached = load_cache("fundamentals", sym)
            if cached is not None:
                results[sym] = cached
                continue
        data = fetch_fundamentals(sym)
        results[sym] = data
        if "error" not in data:
            save_cache("fundamentals", sym, data)

    print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
