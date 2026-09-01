"""Versioned provenance contract for market-data script outputs."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from typing import Any, Iterable


CONTRACT_VERSION = "1.1"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_time(value: str | int | float | date | datetime | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    text = str(value).strip()
    return text or None


def content_hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def leaf_paths(value: Any, prefix: str = "") -> list[str]:
    """Return dotted paths for every scalar field in a JSON-like value."""
    if isinstance(value, dict):
        paths: list[str] = []
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            paths.extend(leaf_paths(child, child_prefix))
        return paths
    if isinstance(value, list):
        paths = []
        for index, child in enumerate(value):
            paths.extend(leaf_paths(child, f"{prefix}[{index}]"))
        return paths or [prefix]
    return [prefix]


def source_record(
    provider: str,
    *,
    dataset: str,
    source_url: str | None = None,
    effective_at: str | date | datetime | None = None,
    published_at: str | date | datetime | None = None,
    published_at_basis: str = "source_reported",
    retrieved_at: str | date | datetime | None = None,
    adjustment: str = "not_applicable",
    quality: str = "aggregator",
    availability: dict[str, Any] | None = None,
    fields: Iterable[str] = (),
) -> dict:
    retrieved = normalize_time(retrieved_at) or utc_now()
    return {
        "provider": provider,
        "dataset": dataset,
        "source_url": source_url,
        "effective_at": normalize_time(effective_at),
        "published_at": normalize_time(published_at),
        "published_at_basis": published_at_basis if published_at is not None else "unknown",
        "retrieved_at": retrieved,
        "adjustment": adjustment,
        "quality": quality,
        "availability": dict(availability or {"status": "unknown"}),
        "fields": sorted(set(fields)),
    }


def attach_contract(
    payload: dict,
    *,
    sources: list[dict],
    provider_attempts: Iterable[dict[str, str]] = (),
    fallback_used: bool = False,
    as_of: str | None = None,
    point_in_time_safe: bool | None = None,
) -> dict:
    """Attach metadata without changing existing business fields."""
    result = dict(payload)
    retrieved = max((s.get("retrieved_at") or "" for s in sources), default=utc_now())
    if point_in_time_safe is None:
        point_in_time_safe = all(s.get("published_at") for s in sources) if as_of else True
    result["_meta"] = {
        "contract_version": CONTRACT_VERSION,
        "as_of": normalize_time(as_of),
        "retrieved_at": retrieved,
        "point_in_time_safe": bool(point_in_time_safe),
        "fallback_used": bool(fallback_used),
        "provider_attempts": list(provider_attempts),
        "sources": sources,
        "payload_sha256": content_hash(payload),
    }
    return result


def parse_instant(value: str) -> datetime:
    text = value.strip()
    if len(text) == 10:
        text += "T23:59:59+00:00"
    elif text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def visible_as_of(source: dict, as_of: str) -> bool:
    """Return true only when publication time proves the record was visible."""
    published = source.get("published_at")
    return bool(published) and parse_instant(published) <= parse_instant(as_of)
