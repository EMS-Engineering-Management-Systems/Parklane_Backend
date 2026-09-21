#!/usr/bin/env python3
"""Seed the Atal database with dummy BMS readings.

Values follow the same rules as the original generate_parklane_data.py:

    Bool Check = 1 -> 0 or 1
    Bool Check = 0 -> a value between Min and Max

Raw samples land in <device>_<index>_instant, then the monthly and yearly
collections are produced by the same rollup the API exposes.

    python -m scripts.seed
    python -m scripts.seed --rows 2000 --devices Transformer,MDB --instances 2
    python -m scripts.seed --drop --months 24
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: E402
from app.core.database import close, connect, get_db  # noqa: E402
from app.services.naming import Target  # noqa: E402
from app.services.readings import ensure_collection  # noqa: E402
from app.services.registry import find_in_catalogue, list_catalogue, touch_device  # noqa: E402
from app.services.rollup import rollup  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--rows", type=int, default=480, help="samples per device (default: 480)")
    parser.add_argument("--months", type=int, default=13, help="history window in months (default: 13)")
    parser.add_argument("--instances", type=int, default=1, help="instances per device type (default: 1)")
    parser.add_argument("--devices", type=str, default=None, help="comma-separated device types")
    parser.add_argument("--seed", type=int, default=42, help="PRNG seed, for reproducible runs")
    parser.add_argument("--drop", action="store_true", help="drop the device's collections first")
    parser.add_argument("--no-rollup", action="store_true", help="skip the monthly/yearly build")
    return parser.parse_args()


def generate_value(tag: dict, rng: random.Random) -> float | int:
    if tag["kind"] == "boolean":
        return rng.randint(0, 1)

    minimum, maximum = tag["min"], tag["max"]
    if minimum == maximum:
        return minimum
    if tag.get("integer"):
        return rng.randint(int(minimum), int(maximum))
    return round(rng.uniform(minimum, maximum), 2)


def target_for(device_type: str, index: int, granularity: str) -> Target:
    return Target(
        device_type=device_type,
        index=index,
        granularity=granularity,
        device_id=f"{device_type}_{index}",
        collection=f"{device_type.lower()}_{index}_{granularity}",
    )


def seed_device(device: dict, index: int, args: argparse.Namespace, rng: random.Random) -> tuple[str, int]:
    target = target_for(device["type"], index, settings.raw_granularity)

    if args.drop:
        for granularity in settings.granularities:
            get_db()[f"{device['type'].lower()}_{index}_{granularity}"].drop()

    collection = ensure_collection(target)

    # Spread the samples evenly across the history window so the monthly and
    # yearly rollups have something to summarise.
    end = datetime.now(UTC)
    start = end - timedelta(days=args.months * 30)
    step = (end - start) / max(1, args.rows)
    now = datetime.now(UTC)

    documents = [
        {
            "deviceId": target.device_id,
            "deviceType": target.device_type,
            "index": index,
            "granularity": target.granularity,
            "timestamp": start + step * i,
            "tags": {tag["name"]: generate_value(tag, rng) for tag in device["tags"]},
            "source": "seed",
            "createdAt": now,
            "updatedAt": now,
        }
        for i in range(args.rows)
    ]

    collection.insert_many(documents, ordered=False)
    touch_device(get_db(), target)
    return target.collection, len(documents)


def main() -> int:
    args = parse_args()

    if args.rows <= 0:
        raise SystemExit("--rows must be a positive integer")
    if args.instances <= 0:
        raise SystemExit("--instances must be a positive integer")

    if args.devices:
        catalogue = []
        for name in (item.strip() for item in args.devices.split(",") if item.strip()):
            device = find_in_catalogue(name)
            if device is None:
                raise SystemExit(f"Unknown device type: {name}")
            catalogue.append(device)
    else:
        catalogue = list_catalogue()

    connect()
    print(f'[seed] database "{settings.mongodb_db}"')
    print(f"[seed] {len(catalogue)} device type(s) x {args.instances} instance(s) x {args.rows} rows")
    if args.drop:
        print("[seed] existing collections for these devices will be dropped")

    rng = random.Random(args.seed)
    total_documents = 0
    total_collections = 0

    for device in catalogue:
        for index in range(1, args.instances + 1):
            name, inserted = seed_device(device, index, args, rng)
            total_documents += inserted
            total_collections += 1
            print(f"  {name:<44} {inserted} docs")

            if not args.no_rollup:
                for granularity in settings.rolled_granularities:
                    summary = rollup(device["type"], index, granularity)
                    total_collections += 1
                    print(f"  {summary['destination']:<44} {summary['periods']} periods")

    print(f"\n[seed] done: {total_documents} readings across {total_collections} collections")
    close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        close()
        raise SystemExit(130) from None
