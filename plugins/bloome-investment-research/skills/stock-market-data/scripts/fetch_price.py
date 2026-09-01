# /// script
# requires-python = ">=3.10"
# dependencies = ["yfinance"]
# ///
"""Fetch research-grade current/delayed prices and volume.

Usage: python scripts/fetch_price.py GOOGL [AAPL MSFT ...]
Output: JSON with regular-session price, volume, and yfinance pre/post-market fields when available.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from cache_utils import load_cache, save_cache
from data_contract import attach_contract, leaf_paths, normalize_time, parse_instant, source_record, utc_now
from provider_runtime import (
    ProviderUnavailable,
    provider_market_metadata,
    require_provider_module,
    route,
)
from security_master import build_security_identity, infer_market, local_symbol
from snapshot_store import save_snapshot


def _fetch_yahoo_raw(symbol: str) -> dict:
    try:
        yf = require_provider_module("prices", "yfinance")
        ticker = yf.Ticker(symbol)
        history = ticker.history(period="30d")
        try:
            history_metadata = ticker.get_history_metadata()
        except Exception:
            history_metadata = {}
        return {
            "kind": "yfinance",
            "ticker": ticker,
            "fast_info": ticker.fast_info,
            "history": history,
            "history_metadata": history_metadata,
        }
    except Exception as exc:
        raise ProviderUnavailable(str(exc)) from exc


def _cn_provider_code(symbol: str, market: str) -> str:
    code = local_symbol(symbol)
    if market not in {"CN-SH", "CN-SZ"} or not re.fullmatch(r"\d{6}", code):
        raise ProviderUnavailable(f"Unsupported mainland China symbol: {symbol}")
    return ("sh" if market == "CN-SH" else "sz") + code


def _cn_quote_time(value: str, pattern: str) -> str:
    parsed = datetime.strptime(value, pattern).replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _request_gb18030(url: str, *, referer: str | None = None) -> str:
    headers = {"User-Agent": "Mozilla/5.0"}
    if referer:
        headers["Referer"] = referer
    request = Request(url, headers=headers)
    with urlopen(request, timeout=10) as response:
        return response.read().decode("gb18030", errors="replace")


def _parse_tencent_quote(text: str, source_url: str) -> dict:
    match = re.search(r'="(?P<body>.*)"', text.strip())
    fields = match.group("body").split("~") if match else []
    if len(fields) < 31 or not fields[3] or not fields[4]:
        raise ProviderUnavailable("Tencent quote payload is missing required fields")
    return {
        "kind": "normalized_quote",
        "price": float(fields[3]),
        "prev_close": float(fields[4]),
        "volume": int(float(fields[6]) * 100),
        "name": fields[1] or None,
        "quote_time": _cn_quote_time(fields[30], "%Y%m%d%H%M%S"),
        "market_timestamp_source": "tencent_quote_field_30",
        "volume_basis": "reported_lots_x100",
        "source_url": source_url,
    }


def _parse_sina_quote(text: str, source_url: str) -> dict:
    match = re.search(r'="(?P<body>.*)"', text.strip())
    fields = match.group("body").split(",") if match else []
    if len(fields) < 32 or not fields[2] or not fields[3]:
        raise ProviderUnavailable("Sina quote payload is missing required fields")
    return {
        "kind": "normalized_quote",
        "price": float(fields[3]),
        "prev_close": float(fields[2]),
        "volume": int(float(fields[8])),
        "name": fields[0] or None,
        "quote_time": _cn_quote_time(f"{fields[30]} {fields[31]}", "%Y-%m-%d %H:%M:%S"),
        "market_timestamp_source": "sina_quote_date_time_fields",
        "volume_basis": "reported_shares",
        "source_url": source_url,
    }


def _fetch_price_raw(provider: str, symbol: str, market: str) -> dict:
    if provider == "yfinance":
        return _fetch_yahoo_raw(symbol)
    try:
        code = _cn_provider_code(symbol, market)
        if provider == "tencent":
            url = f"https://qt.gtimg.cn/q={code}"
            return _parse_tencent_quote(_request_gb18030(url), url)
        if provider == "sina":
            url = f"https://hq.sinajs.cn/list={code}"
            return _parse_sina_quote(
                _request_gb18030(url, referer="https://finance.sina.com.cn/"), url
            )
        raise ProviderUnavailable(f"price adapter not implemented for {provider}")
    except ProviderUnavailable:
        raise
    except Exception as exc:
        raise ProviderUnavailable(f"{provider} quote request failed: {exc}") from exc


def fetch_price(symbol: str) -> dict:
    """Fetch current price info for a single symbol, including volume."""
    try:
        market = infer_market(symbol)
        routed = route(
            "prices", lambda provider: _fetch_price_raw(provider, symbol, market), market=market
        )
        raw = routed.value
        ticker = raw.get("ticker")

        if raw["kind"] == "normalized_quote":
            price = raw["price"]
            prev_close = raw["prev_close"]
        else:
            fi = raw["fast_info"]
            price = fi.last_price
            prev_close = fi.previous_close
        if price and prev_close:
            price = round(float(price), 2)
            prev_close = round(float(prev_close), 2)
            change_pct = round((price - prev_close) / prev_close * 100, 2) if prev_close else None
        else:
            return {"symbol": symbol, "error": "no price data available"}

        retrieved_at = utc_now()
        history_metadata = raw.get("history_metadata") or {}
        quote_time = raw.get("quote_time") or normalize_time(
            history_metadata.get("regularMarketTime")
        )

        # volume: still needs daily history (for 20d avg volume)
        hist = raw.get("history")

        result = {
            "price": round(price, 2),
            "prev_close": round(prev_close, 2),
            "change_pct": change_pct,
            "data_time": quote_time,
            "data_status": "research_grade_current_or_delayed",
        }

        # Volume data
        if raw["kind"] == "normalized_quote":
            result["volume"] = raw["volume"]
            result["volume_basis"] = raw["volume_basis"]
        elif hist is not None and "Volume" in hist.columns and len(hist) >= 2:
            volume = int(hist["Volume"].iloc[-1])
            # 20-day average volume (or available days if < 20)
            vol_series = hist["Volume"].iloc[:-1]  # exclude today
            if len(vol_series) > 0:
                avg_vol = float(vol_series.tail(20).mean())
                vol_ratio = round(volume / avg_vol, 2) if avg_vol > 0 else None
            else:
                avg_vol = None
                vol_ratio = None

            result["volume"] = volume
            result["volume_avg_20d"] = int(avg_vol) if avg_vol else None
            result["volume_ratio"] = vol_ratio
            # Volume description for quick interpretation
            if vol_ratio is not None:
                if vol_ratio < 0.5:
                    result["volume_desc"] = "extremely low"  # potential reversal signal
                elif vol_ratio < 0.8:
                    result["volume_desc"] = "contracting"    # below average
                elif vol_ratio <= 1.3:
                    result["volume_desc"] = "normal"
                elif vol_ratio <= 2.0:
                    result["volume_desc"] = "elevated"       # above average
                else:
                    result["volume_desc"] = "extreme"        # very high — breakout or climax

        # Name + yfinance extended-hours data from info (best-effort).
        # Keep regular-session and extended-hours prices separate: fast_info.last_price
        # is not a reliable substitute for an explicitly labeled post-market quote.
        try:
            if ticker is None:
                raise AttributeError("provider has no yfinance ticker metadata")
            info = ticker.info
            name = info.get("shortName") or info.get("longName")
            if name:
                result["name"] = name
            pre_price = info.get("preMarketPrice")
            if pre_price and pre_price > 0:
                pre_price = float(pre_price)
                pre_change = round(((pre_price - prev_close) / prev_close) * 100, 2) if prev_close else 0
                result["pre_market_price"] = round(pre_price, 2)
                result["pre_market_change_pct"] = pre_change
                result["pre_market_time"] = normalize_time(info.get("preMarketTime"))
                result["pre_market_provider"] = "yfinance"

            post_price = info.get("postMarketPrice")
            if post_price and post_price > 0:
                post_price = float(post_price)
                regular_close = float(price)
                post_change = (
                    round(((post_price - regular_close) / regular_close) * 100, 2)
                    if regular_close
                    else None
                )
                result["post_market_price"] = round(post_price, 2)
                result["post_market_change_pct"] = post_change
                result["post_market_time"] = normalize_time(info.get("postMarketTime"))
                result["post_market_basis"] = "latest_regular_session_price"
                result["post_market_basis_price"] = round(regular_close, 2)
                result["post_market_provider"] = "yfinance"
        except Exception:
            pass

        identity = build_security_identity(
            symbol,
            company_name=result.get("name") or raw.get("name"),
            market=market,
            provider_symbols={routed.provider: symbol},
        )
        if raw.get("name") and "name" not in result:
            result["name"] = raw["name"]
        declared = provider_market_metadata(routed.provider, "prices", market)
        availability_status = declared.get("availability_status", "unknown")
        observed_staleness_seconds = None
        if quote_time:
            observed_staleness_seconds = max(
                0, int((parse_instant(retrieved_at) - parse_instant(quote_time)).total_seconds())
            )
        availability = {
            "status": availability_status,
            "declared_delay_seconds": declared.get("declared_delay_seconds"),
            "delay_basis": declared.get("delay_basis", "unknown"),
            "delay_source_url": declared.get("delay_source_url"),
            "service_level": declared.get("service_level", "unspecified"),
            "observed_staleness_seconds": observed_staleness_seconds,
            "observed_staleness_note": "Includes time outside market hours; it is not feed latency.",
            "required_freshness_basis": "latest_completed_exchange_session_not_wall_clock_age",
            "wall_clock_age_alone_is_stale": False,
            "market_timestamp_source": raw.get("market_timestamp_source") or (
                "yfinance.history_metadata.regularMarketTime" if quote_time else "unknown"
            ),
        }
        result["data_status"] = (
            "research_grade_delayed"
            if availability_status == "delayed"
            else "research_grade_near_real_time_unofficial"
            if availability_status == "near_real_time_unofficial"
            else "research_grade_current_or_delayed"
        )
        result["quote_availability"] = availability
        result["identity"] = identity
        source = source_record(
            routed.provider,
            dataset="prices",
            source_url=raw.get("source_url") or f"https://finance.yahoo.com/quote/{symbol}",
            effective_at=quote_time,
            published_at=retrieved_at,
            published_at_basis="first_observed",
            retrieved_at=retrieved_at,
            adjustment="unadjusted_quote; history_provider_default",
            quality="aggregator",
            availability=availability,
            fields=leaf_paths(result),
        )
        contracted = attach_contract(
            result,
            sources=[source],
            provider_attempts=routed.attempts,
            fallback_used=routed.fallback_used,
        )
        save_snapshot("prices", identity["security"]["security_id"], contracted)
        return contracted
    except Exception as e:
        return {"symbol": symbol, "error": str(e)}


def main():
    force = "--force" in sys.argv
    symbols = [a for a in sys.argv[1:] if a != "--force"]
    if not symbols:
        print(json.dumps({"error": "Usage: fetch_price.py SYMBOL [SYMBOL ...] [--force]"}))
        sys.exit(1)

    result = {}
    for sym in symbols:
        sym = sym.upper()
        if not force:
            cached = load_cache("price", sym)
            if cached is not None:
                result[sym] = cached
                continue
        data = fetch_price(sym)
        if data:
            result[sym] = data
            if "error" not in data:
                save_cache("price", sym, data)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
