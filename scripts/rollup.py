#!/usr/bin/env python3
"""Rebuild the monthly and yearly collections from the instant ones.

python -m scripts.rollup
python -m scripts.rollup --device Transformer --index 1
python -m scripts.rollup --granularity monthly --from 2026-01-01
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: E402
from app.core.database import close, connect, get_db  # noqa: E402
from app.services.naming import parse_collection_name  # noqa: E402
from app.services.rollup import rollup  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--device", type=str, default=None, help="device type, e.g. Transformer")
    parser.add_argument("--index", type=int, default=1)
    parser.add_argument("--granularity", type=str, default=None, help="monthly or yearly")
    parser.add_argument("--from", dest="from_", type=str, default=None, help="ISO date")
    parser.add_argument("--to", type=str, default=None, help="ISO date")
    return parser.parse_args()


def discover_devices() -> list[tuple[str, int]]:
    """Every device that has an instant collection to roll up."""
    found = []
    for name in get_db().list_collection_names():
        parsed = parse_collection_name(name)
        if parsed and parsed["granularity"] == settings.raw_granularity:
            found.append((parsed["deviceType"], parsed["index"]))
    return sorted(set(found))


def main() -> int:
    args = parse_args()
    connect()

    devices = [(args.device, args.index)] if args.device else discover_devices()
    targets = [args.granularity] if args.granularity else settings.rolled_granularities

    if not devices:
        print(
            '[rollup] no instant collections found - run "python -m scripts.seed" or POST some readings first'
        )
        close()
        return 0

    window = {}
    if args.from_:
        window["from"] = args.from_
    if args.to:
        window["to"] = args.to

    for device_type, index in devices:
        for granularity in targets:
            result = rollup(device_type, index, granularity, window)
            print(f"  {result['destination']:<44} {result['periods']} periods (+{result['upserted']} new)")

    print(f"\n[rollup] done: {len(devices)} device(s) x {len(targets)} granularity")
    close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
