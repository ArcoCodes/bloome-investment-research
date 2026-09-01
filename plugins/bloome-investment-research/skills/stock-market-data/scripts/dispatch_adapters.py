"""Adapters that normalize legacy collectors into the unified data contract."""

from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Iterable

from data_contract import attach_contract, leaf_paths, source_record, utc_now
from provider_runtime import configured_chain
from security_master import build_security_identity
from snapshot_store import save_snapshot


SOURCE_ALIASES = {
    "yahoo": "yfinance",
    "yfinance": "yfinance",
    "google": "google_news",
    "seeking alpha": "seeking_alpha",
    "eastmoney": "eastmoney",
    "\u4e1c\u65b9\u8d22\u5bcc": "eastmoney",
    "finviz": "finviz",
    "sec": "sec_edgar",
    "edgar": "sec_edgar",
    "hkex": "hkex",
    "finra": "finra",
    "tencent": "tencent",
}


def _provider(value: str | None, url: str | None = None) -> str:
    text = f"{value or ''} {url or ''}".casefold()
    for needle, provider in SOURCE_ALIASES.items():
        if needle in text:
            return provider
    return (value or "unknown").strip().casefold().replace(" ", "_")


def _time(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    text = str(value).strip()
    try:
        parsed = parsedate_to_datetime(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OverflowError):
        return text or None


def _attempts(dataset: str, observed: Iterable[str]) -> list[dict[str, str]]:
    seen = set(observed)
    return [
        {"provider": provider, "status": "success" if provider in seen else "not_observed"}
        for provider in configured_chain(dataset)
    ]


def _derived_source(dataset: str, payload: dict, now: str) -> dict:
    return source_record(
        "stock-market-data",
        dataset=dataset,
        effective_at=now,
        published_at=now,
        published_at_basis="derived_at",
        retrieved_at=now,
        quality="derived",
        fields=leaf_paths({k: v for k, v in payload.items() if k != "identity"}),
    )


def _finalize(
    dataset: str,
    symbol: str | None,
    payload: dict,
    sources: list[dict],
    *,
    attempts: list[dict[str, str]],
    fallback_used: bool = False,
) -> dict:
    result = dict(payload)
    identity = None
    if symbol:
        identity = build_security_identity(symbol, company_name=result.get("company_name"))
        result["identity"] = identity
    now = utc_now()
    contracted = attach_contract(
        result,
        sources=sources + [_derived_source(dataset, result, now)],
        provider_attempts=attempts,
        fallback_used=fallback_used,
    )
    if identity and "error" not in result:
        save_snapshot(dataset, identity["security"]["security_id"], contracted)
    return contracted


def events(symbol: str, *, price_change_pct: float = 0, force_search: bool = False,
           company_name: str = "") -> dict:
    from fetch_events import fetch_events

    payload = fetch_events(symbol, price_change_pct, force_search, company_name)
    sources = []
    observed = set()
    for index, item in enumerate(payload.get("events", [])):
        url = item.get("url")
        provider = _provider(item.get("source"), url)
        observed.add(provider)
        sources.append(source_record(
            provider, dataset="events", source_url=url,
            effective_at=item.get("date"), published_at=item.get("date"),
            published_at_basis="source_reported_date", quality="mixed",
            fields=leaf_paths(item, f"events[{index}]"),
        ))
    return _finalize("events", symbol.upper(), payload, sources,
                     attempts=_attempts("events", observed))


def news(symbols: list[str]) -> dict:
    from fetch_news import fetch_all_news

    upper = [symbol.upper() for symbol in symbols]
    payload = fetch_all_news(upper)
    sources = []
    observed = set()
    groups = [("macro", payload.get("macro", []))]
    groups.extend((f"stocks.{symbol}", payload.get("stocks", {}).get(symbol, [])) for symbol in upper)
    for prefix, items in groups:
        for index, item in enumerate(items):
            url = item.get("link") or item.get("url")
            provider = _provider(item.get("source"), url)
            observed.add(provider)
            sources.append(source_record(
                provider, dataset="news", source_url=url,
                effective_at=_time(item.get("published")),
                published_at=_time(item.get("published")),
                published_at_basis="source_reported", quality="aggregator",
                fields=leaf_paths(item, f"{prefix}[{index}]"),
            ))
    symbol = upper[0] if len(upper) == 1 else None
    return _finalize("news", symbol, payload, sources,
                     attempts=_attempts("news", observed))


def earnings_calendar(symbol: str) -> dict:
    from fetch_earnings_calendar import fetch_earnings_calendar

    payload = fetch_earnings_calendar(symbol.upper())
    routing = payload.pop("_routing", {})
    provider = payload.get("source") or routing.get("provider") or "unknown"
    provider = _provider(provider)
    checked = payload.get("checked_at") or utc_now()
    source = source_record(
        provider, dataset="earnings_calendar",
        source_url=routing.get("source_url"),
        effective_at=payload.get("next_earnings_date") or payload.get("latest_report_date"),
        published_at=checked, published_at_basis="first_observed",
        retrieved_at=checked, quality="aggregator",
        fields=leaf_paths(payload),
    )
    return _finalize(
        "earnings_calendar", symbol.upper(), payload, [source],
        attempts=routing.get("attempts") or _attempts("earnings_calendar", {provider}),
        fallback_used=bool(routing.get("fallback_used")),
    )


def short_interest(symbol: str) -> dict:
    from fetch_short_interest import fetch_hk_short, fetch_us_short

    upper = symbol.upper()
    payload = fetch_hk_short(upper) if upper.endswith(".HK") else fetch_us_short(upper)
    provider = "hkex" if upper.endswith(".HK") else "yfinance"
    effective = payload.get("date") or payload.get("date_short_interest") or utc_now()
    source = source_record(
        provider, dataset="positioning",
        source_url=(
            "https://www.hkex.com.hk/Market-Data/Securities-Prices/Equities/Short-Selling-Turnover"
            if provider == "hkex" else f"https://finance.yahoo.com/quote/{upper}"
        ),
        effective_at=effective, published_at=utc_now(),
        published_at_basis="first_observed", quality="primary" if provider == "hkex" else "aggregator",
        fields=leaf_paths(payload),
    )
    return _finalize("positioning", upper, payload, [source],
                     attempts=_attempts("positioning", {provider}))


def positioning(symbol: str, *, days: int = 30, insider_days: int = 90) -> dict:
    from fetch_options_flow import fetch_finra_short, fetch_insider_trades, fetch_options_flow

    upper = symbol.upper()
    payload = {
        "symbol": upper,
        "options_flow": fetch_options_flow(upper, days),
        "short_volume": fetch_finra_short(upper),
        "insider_trades": fetch_insider_trades(upper, insider_days),
        "fetched_at": utc_now(),
    }
    sources = [
        source_record("yfinance", dataset="positioning", source_url=f"https://finance.yahoo.com/quote/{upper}/options",
                      effective_at=payload["options_flow"].get("as_of"), published_at=payload["fetched_at"],
                      published_at_basis="first_observed", quality="aggregator",
                      fields=leaf_paths(payload["options_flow"], "options_flow")),
        source_record("finra", dataset="positioning", source_url="https://cdn.finra.org/equity/regsho/daily/",
                      effective_at=payload["short_volume"].get("latest_date"),
                      published_at=payload["fetched_at"], published_at_basis="first_observed",
                      quality="primary", fields=leaf_paths(payload["short_volume"], "short_volume")),
        source_record("sec_edgar", dataset="positioning", source_url=payload["insider_trades"].get("sec_url"),
                      effective_at=payload["fetched_at"], published_at=payload["fetched_at"],
                      published_at_basis="first_observed", quality="primary",
                      fields=leaf_paths(payload["insider_trades"], "insider_trades")),
    ]
    return _finalize("positioning", upper, payload, sources,
                     attempts=_attempts("positioning", {"yfinance", "finra", "sec_edgar"}))


def technicals(symbol: str, *, period: str = "medium") -> dict:
    from analyze_technicals import analyze

    upper = symbol.upper()
    payload = analyze(upper, period)
    sources = []
    observed = set()
    for frame, info in payload.get("data_provenance", {}).items():
        if not isinstance(info, dict) or not info.get("source"):
            continue
        provider = _provider(info.get("source"), info.get("source_url"))
        observed.add(provider)
        sources.append(source_record(
            provider, dataset="technicals", source_url=info.get("source_url"),
            effective_at=info.get("last_bar"), published_at=payload.get("as_of"),
            published_at_basis="market_bar_time", adjustment=info.get("adjustment", "unknown"),
            quality="aggregator", fields=[f"data_provenance.{frame}"],
        ))
    return _finalize("technicals", upper, payload, sources,
                     attempts=_attempts("technicals", observed))


def macro(market: str = "us") -> dict:
    from fetch_macro import (
        _compute_term_spread, assess_risk_hk, assess_risk_us, fetch_all,
        fetch_hsgt_flows, identify_scenario,
    )

    market = market.casefold()
    data, data_time = fetch_all(market)
    fund_flows = fetch_hsgt_flows() if market == "hk" else {}
    if market == "hk":
        level, factors, triggered = assess_risk_hk(data, fund_flows)
        payload = {
            "market": "hk", "data_time": data_time,
            "indices": {
                "hsi": {k: data.get("hsi", {}).get(k) for k in ("price", "change_pct")},
                "hscei": {k: data.get("hscei", {}).get(k) for k in ("price", "change_pct")},
            },
            "fx": {
                key: {k: data.get(key, {}).get(k) for k in ("price", "change_pct")}
                for key in ("usdcny", "usdhkd", "dxy")
            },
            "global": {
                key: {k: data.get(key, {}).get(k) for k in ("price", "change_pct")}
                for key in ("vix", "oil", "gold")
            },
            "fund_flows": {
                "northbound": fund_flows.get("northbound"),
                "southbound": fund_flows.get("southbound"),
            },
            "scenario": identify_scenario(data, "hk"),
            "risk_level": level, "risk_factors": factors, "triggered": triggered,
        }
        observed = {"yfinance"} | ({"eastmoney"} if fund_flows else set())
    else:
        level, factors, triggered = assess_risk_us(data)
        payload = {
            "market": "us", "data_time": data_time,
            "indices": {
                "sp500_futures": {k: data.get("sp500_futures", {}).get(k) for k in ("price", "change_pct")},
                "nasdaq_futures": {k: data.get("nasdaq_futures", {}).get(k) for k in ("price", "change_pct")},
            },
            "volatility": {"vix": {k: data.get("vix", {}).get(k) for k in ("price", "change_pct")}},
            "fx": {"dxy": {k: data.get("dxy", {}).get(k) for k in ("price", "change_pct")}},
            "commodities": {
                key: {k: data.get(key, {}).get(k) for k in ("price", "change_pct")}
                for key in ("oil", "brent", "gold", "copper")
            },
            "rates": {
                "treasury_10y": {k: data.get("treasury_10y", {}).get(k) for k in ("price", "change_pct")},
                "treasury_short": {k: data.get("treasury_short", {}).get(k) for k in ("price", "change_pct")},
                "term_spread": _compute_term_spread(data),
            },
            "scenario": identify_scenario(data, "us"),
            "risk_level": level, "risk_factors": factors, "triggered": triggered,
        }
        observed = {"yfinance"}
    sources = [source_record(
        "yfinance", dataset="macro", source_url="https://finance.yahoo.com/",
        effective_at=data_time, published_at=utc_now(), published_at_basis="first_observed",
        adjustment="provider_default", quality="aggregator", fields=leaf_paths(payload),
    )]
    if "eastmoney" in observed:
        sources.append(source_record(
            "eastmoney", dataset="macro", source_url="https://datacenter-web.eastmoney.com/",
            effective_at=data_time, published_at=utc_now(), published_at_basis="first_observed",
            quality="aggregator", fields=leaf_paths(payload.get("fund_flows", {}), "fund_flows"),
        ))
    return _finalize("macro", None, payload, sources,
                     attempts=_attempts("macro", observed))
