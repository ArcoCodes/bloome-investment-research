# /// script
# requires-python = ">=3.10"
# dependencies = ["httpx", "yfinance", "pandas", "numpy", "requests", "akshare"]
# ///
"""Unified dispatcher for stock-market-data.

New callers should use this command. Legacy scripts remain compatible wrappers.
"""

from __future__ import annotations

import argparse
import json
import sys

from provider_runtime import configured_chain, load_provider_config, provider_mode
from security_master import build_security_identity
from snapshot_store import query_as_of


def _resolve(query: str, market: str | None) -> dict:
    from data_contract import attach_contract, leaf_paths, source_record, utc_now
    from provider_runtime import route
    from search_ticker import TICKERS_URL, search_provider

    routed = route(
        "security_master", lambda provider: search_provider(provider, query, market=market)
    )
    matches = routed.value
    for match in matches:
        match["identity"] = build_security_identity(
            match["symbol"], company_name=match["name"], market=match["market"],
            provider_symbols={routed.provider: match["symbol"]},
        )
    payload = {"query": query, "matches": matches}
    now = utc_now()
    return attach_contract(
        payload,
        sources=[source_record(
            routed.provider, dataset="security_master",
            source_url=(
                TICKERS_URL if routed.provider == "novark"
                else f"https://finance.yahoo.com/lookup?s={query}"
            ),
            effective_at=now, retrieved_at=now, quality="aggregator",
            fields=leaf_paths(payload),
        )],
        provider_attempts=routed.attempts,
        fallback_used=routed.fallback_used,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Unified market-data dispatcher")
    sub = parser.add_subparsers(dest="command", required=True)

    resolve_parser = sub.add_parser("resolve", help="Resolve company to entity/listing/security")
    resolve_parser.add_argument("query")
    resolve_parser.add_argument("--market", choices=["us", "hk"])

    price_parser = sub.add_parser("price", help="Research-grade current/delayed quote")
    price_parser.add_argument("symbols", nargs="+")

    fundamentals_parser = sub.add_parser("fundamentals")
    fundamentals_parser.add_argument("symbols", nargs="+")

    events_parser = sub.add_parser("events")
    events_parser.add_argument("symbol")
    events_parser.add_argument("--price-change", type=float, default=0)
    events_parser.add_argument("--search", action="store_true")
    events_parser.add_argument("--company-name", default="")

    news_parser = sub.add_parser("news")
    news_parser.add_argument("symbols", nargs="+")

    earnings_parser = sub.add_parser("earnings-calendar")
    earnings_parser.add_argument("symbol")

    short_parser = sub.add_parser("short-interest")
    short_parser.add_argument("symbol")

    positioning_parser = sub.add_parser("positioning")
    positioning_parser.add_argument("symbol")
    positioning_parser.add_argument("--days", type=int, default=30)
    positioning_parser.add_argument("--insider-days", type=int, default=90)

    technicals_parser = sub.add_parser("technicals")
    technicals_parser.add_argument("symbol")
    technicals_parser.add_argument("--period", choices=["short", "medium", "long"], default="medium")

    macro_parser = sub.add_parser("macro")
    macro_parser.add_argument("--market", choices=["us", "hk"], default="us")

    asof_parser = sub.add_parser("as-of", help="Strict historical snapshot query")
    asof_parser.add_argument("dataset")
    asof_parser.add_argument("symbol")
    asof_parser.add_argument("--as-of", required=True)

    providers_parser = sub.add_parser("providers", help="Show explicit provider policy")
    providers_parser.add_argument("dataset", nargs="?")
    providers_parser.add_argument(
        "--market", choices=["US", "HK", "CN-SH", "CN-SZ", "JP", "KR"]
    )

    args = parser.parse_args()
    try:
        if args.command == "resolve":
            output = _resolve(args.query, args.market)
        elif args.command == "price":
            from fetch_price import fetch_price

            output = {symbol.upper(): fetch_price(symbol.upper()) for symbol in args.symbols}
        elif args.command == "fundamentals":
            from fetch_fundamentals import fetch_fundamentals

            output = {symbol.upper(): fetch_fundamentals(symbol.upper()) for symbol in args.symbols}
        elif args.command == "events":
            from dispatch_adapters import events

            output = events(
                args.symbol, price_change_pct=args.price_change, force_search=args.search,
                company_name=args.company_name,
            )
        elif args.command == "news":
            from dispatch_adapters import news

            output = news(args.symbols)
        elif args.command == "earnings-calendar":
            from dispatch_adapters import earnings_calendar

            output = earnings_calendar(args.symbol)
        elif args.command == "short-interest":
            from dispatch_adapters import short_interest

            output = short_interest(args.symbol)
        elif args.command == "positioning":
            from dispatch_adapters import positioning

            output = positioning(args.symbol, days=args.days, insider_days=args.insider_days)
        elif args.command == "technicals":
            from dispatch_adapters import technicals

            output = technicals(args.symbol, period=args.period)
        elif args.command == "macro":
            from dispatch_adapters import macro

            output = macro(args.market)
        elif args.command == "as-of":
            identity = build_security_identity(args.symbol)
            output = query_as_of(args.dataset, identity["security"]["security_id"], args.as_of)
        elif args.command == "providers":
            output = (
                {
                    "dataset": args.dataset,
                    "market": args.market,
                    "mode": provider_mode(args.dataset),
                    "providers": configured_chain(args.dataset, market=args.market),
                }
                if args.dataset else load_provider_config()
            )
        else:
            raise AssertionError(args.command)
    except Exception as exc:
        output = {"error": str(exc)}
        print(json.dumps(output, ensure_ascii=False, indent=2))
        sys.exit(1)
    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
