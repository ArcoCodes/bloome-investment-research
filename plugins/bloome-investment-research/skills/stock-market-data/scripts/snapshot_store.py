"""Immutable local snapshots and strict point-in-time lookup."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from data_contract import content_hash, parse_instant, utc_now, visible_as_of


SNAPSHOT_ROOT = Path(
    os.environ.get("STOCK_DATA_SNAPSHOT_DIR")
    or Path(tempfile.gettempdir()) / "stock-market-data-snapshots"
)


def _safe(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in value)


def save_snapshot(dataset: str, security_id: str, payload: dict) -> Path:
    digest = content_hash(payload)
    retrieved = payload.get("_meta", {}).get("retrieved_at") or utc_now()
    stamp = retrieved.replace(":", "").replace("-", "")
    folder = SNAPSHOT_ROOT / _safe(dataset) / _safe(security_id)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{stamp}_{digest[:16]}.json"
    if not path.exists():
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def query_as_of(dataset: str, security_id: str, as_of: str) -> dict[str, Any]:
    folder = SNAPSHOT_ROOT / _safe(dataset) / _safe(security_id)
    candidates: list[tuple[object, dict, Path]] = []
    for path in folder.glob("*.json") if folder.exists() else ():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        sources = payload.get("_meta", {}).get("sources", [])
        if not sources or not all(visible_as_of(source, as_of) for source in sources):
            continue
        published = max(parse_instant(source["published_at"]) for source in sources)
        candidates.append((published, payload, path))
    if not candidates:
        raise LookupError(
            f"No point-in-time-safe snapshot for dataset={dataset}, security_id={security_id}, as_of={as_of}"
        )
    _, payload, path = max(candidates, key=lambda item: item[0])
    result = dict(payload)
    result.setdefault("_meta", {})["snapshot_path"] = str(path)
    result["_meta"]["as_of"] = as_of
    result["_meta"]["point_in_time_safe"] = True
    return result
