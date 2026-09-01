"""Deterministic entity -> listing -> security identity helpers."""

from __future__ import annotations

import hashlib
import re
from typing import Any


EXCHANGES = {
    "US": {"mic": "UNKNOWN", "currency": "USD", "timezone": "America/New_York"},
    "HK": {"mic": "XHKG", "currency": "HKD", "timezone": "Asia/Hong_Kong"},
    "CN-SH": {"mic": "XSHG", "currency": "CNY", "timezone": "Asia/Shanghai"},
    "CN-SZ": {"mic": "XSHE", "currency": "CNY", "timezone": "Asia/Shanghai"},
    "JP": {"mic": "XTKS", "currency": "JPY", "timezone": "Asia/Tokyo"},
    "KR": {"mic": "XKRX", "currency": "KRW", "timezone": "Asia/Seoul"},
}


def _stable_id(kind: str, *parts: str) -> str:
    canonical = "|".join(p.strip().casefold() for p in parts if p is not None)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]
    return f"{kind}_{digest}"


def infer_market(symbol: str, market_hint: str | None = None) -> str:
    if market_hint:
        hint = market_hint.upper().replace("_", "-")
        aliases = {"CN": "CN-SH" if symbol.startswith(("5", "6", "9")) else "CN-SZ", "SH": "CN-SH", "SZ": "CN-SZ"}
        return aliases.get(hint, hint)
    upper = symbol.upper()
    if upper.endswith(".HK"):
        return "HK"
    if upper.endswith(".SS"):
        return "CN-SH"
    if upper.endswith(".SZ"):
        return "CN-SZ"
    if upper.endswith(".T"):
        return "JP"
    if upper.endswith((".KS", ".KQ")):
        return "KR"
    return "US"


def local_symbol(symbol: str) -> str:
    upper = symbol.strip().upper()
    return re.sub(r"\.(HK|SS|SZ|T|KS|KQ)$", "", upper)


def build_security_identity(
    symbol: str,
    *,
    company_name: str | None = None,
    market: str | None = None,
    instrument_type: str = "common_stock",
    isin: str | None = None,
    official_entity_id: str | None = None,
    provider_symbols: dict[str, str] | None = None,
) -> dict[str, Any]:
    normalized_symbol = symbol.strip().upper()
    market_code = infer_market(normalized_symbol, market)
    exchange = EXCHANGES.get(market_code, {"mic": "UNKNOWN", "currency": None, "timezone": None})
    local = local_symbol(normalized_symbol)
    # A display name must never determine identity: names change. Until an
    # authority identifier is available, use a clearly provisional listing key.
    entity_key = official_entity_id or f"provisional:{market_code}:{local}"
    entity_id = _stable_id("ent", entity_key)
    listing_id = _stable_id("lst", entity_id, exchange["mic"], local)
    security_id = _stable_id("sec", listing_id, instrument_type, isin or "")
    return {
        "entity": {
            "entity_id": entity_id,
            "legal_name": company_name,
            "official_entity_id": official_entity_id,
            "resolution_status": "resolved" if official_entity_id else "provisional",
        },
        "listing": {
            "listing_id": listing_id,
            "entity_id": entity_id,
            "market": market_code,
            "mic": exchange["mic"],
            "local_symbol": local,
            "currency": exchange["currency"],
            "timezone": exchange["timezone"],
        },
        "security": {
            "security_id": security_id,
            "listing_id": listing_id,
            "instrument_type": instrument_type,
            "isin": isin,
            "provider_symbols": {**(provider_symbols or {}), "input": normalized_symbol},
        },
    }
