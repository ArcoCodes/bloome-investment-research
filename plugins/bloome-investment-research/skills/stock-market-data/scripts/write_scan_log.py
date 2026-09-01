# /// script
# requires-python = ">=3.10"
# ///
"""
Scan-log writer for stock-trade-plan entry monitoring and externally scheduled position-monitor scans.
Writes one JSON object per line to `$STOCK_SCAN_LOG_DIR` or the system temporary directory, partitioned by UTC date.

Usage:
  uv run write_scan_log.py --src EO --sym AMD --px 196.3 --grade S --obs "+2.4% from entry zone"
  uv run write_scan_log.py --src PM --sym BABA --px 285.0 --pri P1 --pushed --obs "3% from stop"
  uv run write_scan_log.py --src EO --sym AMD --px 196.3 --skip qs --delta "+0.2%"

Required: --src (EO|PM), --sym, --px
EO mode: --grade (A|B|C|S), --score (number)
PM mode: --pri (P0|P1|P2|P3)
Optional: --obs, --pushed, --skip (qs=quick silent), --delta, --plan, --msg, --cooldown
"""

import argparse
import fcntl
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

LOG_DIR = Path(os.environ.get("STOCK_SCAN_LOG_DIR") or Path(tempfile.gettempdir()) / "stock-scan-logs")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--src", required=True, choices=["EO", "PM"])
    p.add_argument("--sym", required=True)
    p.add_argument("--px", required=True, type=float)
    # EO fields
    p.add_argument("--score", type=int)
    p.add_argument("--grade", choices=["A", "B", "C", "S"])
    # PM fields
    p.add_argument("--pri", choices=["P0", "P1", "P2", "P3"])
    # Common optional
    p.add_argument("--obs", default="")
    p.add_argument("--pushed", action="store_true")
    p.add_argument("--cooldown", action="store_true")
    p.add_argument("--skip", choices=["qs"])
    p.add_argument("--delta", default="")
    p.add_argument("--plan", default="")
    p.add_argument("--msg", default="")

    args = p.parse_args()

    if args.src == "EO" and not args.skip and args.grade is None:
        p.error("EO scans require --grade unless --skip is used")
    if args.src == "PM" and not args.skip and args.pri is None:
        p.error("PM scans require --pri unless --skip is used")
    if len(args.obs) > 30:
        p.error("--obs must be at most 30 characters")

    now = datetime.now(timezone.utc)
    entry = {"ts": now.isoformat(), "s": args.src, "sym": args.sym.upper(), "px": args.px}

    if args.skip:
        entry["skip"] = args.skip
        if args.delta:
            entry["d"] = args.delta
    elif args.cooldown:
        if args.src == "EO" and args.grade:
            entry["sc"] = args.score
            entry["g"] = args.grade
        elif args.src == "PM" and args.pri:
            entry["pri"] = args.pri
        entry["cooldown"] = True
        if args.obs:
            entry["obs"] = args.obs
    else:
        if args.src == "EO":
            if args.score is not None:
                entry["sc"] = args.score
            if args.grade:
                entry["g"] = args.grade
        elif args.src == "PM":
            if args.pri:
                entry["pri"] = args.pri
        if args.pushed:
            entry["pushed"] = True
        if args.obs:
            entry["obs"] = args.obs
        if args.plan:
            entry["plan"] = args.plan
        if args.msg:
            entry["msg"] = args.msg

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / f"{now.strftime('%Y-%m-%d')}.jsonl"
    with open(log_file, "a", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    print(json.dumps({"ok": True, "file": str(log_file), "entry": entry}, ensure_ascii=False))


if __name__ == "__main__":
    main()
