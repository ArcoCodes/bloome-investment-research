# /// script
# requires-python = ">=3.10"
# dependencies = ["httpx", "yfinance"]
# ///
"""Search for a stock ticker by company name or keyword.

Data source: novark CDN tickers.json (Sina Finance, covers US + HK with Chinese names).
HK ticker output is formatted for yfinance (e.g. 0700.HK, not 00700).

Usage:
    python scripts/search_ticker.py "Tencent"
    python scripts/search_ticker.py "minimax" --market hk
    python scripts/search_ticker.py "apple" --market us

Output: JSON with matching symbols and names.
"""

from __future__ import annotations

import argparse
import json

import httpx

from data_contract import attach_contract, leaf_paths, source_record, utc_now
from provider_runtime import ProviderUnavailable, require_provider_module, route
from security_master import build_security_identity

TICKERS_URL = "https://cdn.novark.vip/tickers.json"


def load_tickers() -> list[dict]:
    """Download tickers.json from CDN."""
    resp = httpx.get(TICKERS_URL, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _load_from_provider(provider: str) -> list[dict]:
    if provider != "novark":
        raise ProviderUnavailable(f"security master adapter not implemented for {provider}")
    try:
        return load_tickers()
    except Exception as exc:
        raise ProviderUnavailable(str(exc)) from exc


def _market_from_yahoo(symbol: str, exchange: str) -> str:
    upper = symbol.upper()
    exchange_upper = exchange.upper()
    exchange_markets = {
        "NMS": "US", "NGM": "US", "NCM": "US", "NYQ": "US", "ASE": "US",
        "PCX": "US", "BTS": "US", "OQB": "US", "OQX": "US", "PNK": "US",
        "HKG": "HK", "SHH": "CN", "SHZ": "CN", "JPX": "JP",
        "KSC": "KR", "KOE": "KR", "TOR": "CA", "VAN": "CA",
        "FRA": "DE", "GER": "DE", "BKK": "TH",
    }
    if exchange_upper in exchange_markets:
        return exchange_markets[exchange_upper]
    if upper.endswith(".HK"):
        return "HK"
    if upper.endswith((".SS", ".SZ")):
        return "CN"
    if upper.endswith(".T"):
        return "JP"
    if upper.endswith((".KS", ".KQ")):
        return "KR"
    suffix_markets = {".TO": "CA", ".V": "CA", ".F": "DE", ".DE": "DE", ".BK": "TH"}
    for suffix, market in suffix_markets.items():
        if upper.endswith(suffix):
            return market
    return "OTHER"


def _rank_match(item: dict, query: str) -> tuple[int, int, str]:
    q = query.casefold().strip()
    symbol = item.get("symbol", "").casefold()
    name = item.get("name", "").casefold()
    if symbol == q:
        rank = 0
    elif name == q:
        rank = 1
    elif name.startswith(q):
        rank = 2
    elif symbol.startswith(q):
        rank = 3
    else:
        rank = 4
    return rank, len(name), symbol


def search_provider(provider: str, query: str, market: str | None = None) -> list[dict]:
    """Search one explicitly configured security-master provider."""
    if provider == "novark":
        matches = search(_load_from_provider(provider), query, market=market)
    elif provider == "yfinance":
        try:
            yf = require_provider_module("security_master", provider)
            quotes = yf.Search(query, max_results=10, news_count=0).quotes
            matches = []
            for quote in quotes:
                symbol = str(quote.get("symbol") or "").upper()
                if not symbol:
                    continue
                item_market = _market_from_yahoo(symbol, str(quote.get("exchange") or ""))
                if market and item_market.casefold() != market.casefold():
                    continue
                matches.append({
                    "symbol": symbol,
                    "name": quote.get("longname") or quote.get("shortname") or symbol,
                    "market": item_market,
                })
        except Exception as exc:
            raise ProviderUnavailable(str(exc)) from exc
    else:
        raise ProviderUnavailable(f"security master adapter not implemented for {provider}")
    if not matches:
        raise ProviderUnavailable("no matching securities")
    return sorted(matches, key=lambda item: _rank_match(item, query))


def _to_yf_symbol(code: str, market: str) -> str:
    """Convert sina-style code to yfinance symbol.

    Sina HK codes are 5-digit zero-padded (00700), but yfinance
    needs 4-digit (0700.HK). Strip one leading zero when the code
    is 5 digits and starts with '0'.
    """
    if market == "HK":
        # 00700 -> 0700.HK, 03690 -> 3690.HK, 09988 -> 9988.HK
        stripped = code.lstrip("0") or "0"
        # yfinance HK tickers are 4 digits: pad back to 4
        stripped = stripped.zfill(4)
        return f"{stripped}.HK"
    return code


def search(tickers: list[dict], query: str, market: str | None = None,
           max_results: int = 10) -> list[dict]:
    """Search tickers by name or symbol. Supports Chinese and English."""
    query_lower = query.lower().strip()
    matches = []

    for t in tickers:
        sym = t.get("s", "")
        name = t.get("n", "")
        mkt = t.get("m", "")

        if market == "hk" and mkt != "HK":
            continue
        if market == "us" and mkt != "US":
            continue

        if query_lower in sym.lower() or query_lower in name.lower():
            matches.append({
                "symbol": _to_yf_symbol(sym, mkt),
                "name": name,
                "market": mkt,
            })

        if len(matches) >= max_results:
            break

    return matches


def main():
    parser = argparse.ArgumentParser(description="Search for stock tickers")
    parser.add_argument("query", help="Company name or keyword to search")
    parser.add_argument("--market", choices=["us", "hk"], help="Filter by market")
    args = parser.parse_args()

    try:
        routed = route(
            "security_master",
            lambda provider: search_provider(provider, args.query, market=args.market),
        )
    except Exception as e:
        print(json.dumps({"error": f"Failed to load tickers: {e}", "matches": []},
                         ensure_ascii=False))
        return

    matches = routed.value
    for match in matches:
        match["identity"] = build_security_identity(
            match["symbol"],
            company_name=match["name"],
            market=match["market"],
            provider_symbols={routed.provider: match["symbol"]},
        )
    result = {"query": args.query, "matches": matches}
    if not matches:
        result["hint"] = "No result found. Try another keyword (English name, short name, or full name)."
    retrieved = utc_now()
    source = source_record(
        routed.provider,
        dataset="security_master",
        source_url=(
            TICKERS_URL if routed.provider == "novark"
            else f"https://finance.yahoo.com/lookup?s={args.query}"
        ),
        effective_at=retrieved,
        published_at=None,
        retrieved_at=retrieved,
        quality="aggregator",
        fields=leaf_paths(result),
    )
    result = attach_contract(
        result,
        sources=[source],
        provider_attempts=routed.attempts,
        fallback_used=routed.fallback_used,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
