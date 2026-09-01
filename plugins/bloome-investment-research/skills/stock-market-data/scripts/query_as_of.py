# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Query a previously captured, publication-safe market-data snapshot."""

from __future__ import annotations

import argparse
import json

from snapshot_store import query_as_of


def main() -> None:
    parser = argparse.ArgumentParser(description="Strict point-in-time snapshot lookup")
    parser.add_argument("dataset")
    parser.add_argument("security_id")
    parser.add_argument("--as-of", required=True, help="ISO date or timestamp")
    args = parser.parse_args()
    try:
        result = query_as_of(args.dataset, args.security_id, args.as_of)
    except LookupError as exc:
        result = {"error": str(exc), "point_in_time_safe": False}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
