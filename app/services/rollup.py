"""Aggregates the raw (`instant`) collection into `monthly` or `yearly`.

Every numeric tag is summarised as {count, min, max, avg, sum, first, last} so
the rolled-up document keeps the same tag names as the raw one - a client can
read transformer_1_monthly and transformer_1_instant with the same code.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from pymongo import UpdateOne

from app.core.config import settings
from app.core.database import get_db
from app.core.errors import ApiError
from app.services.filters import parse_timestamp
from app.services.naming import Target, normalize_device_type, normalize_index
from app.services.readings import ensure_collection, require_collection
from app.services.registry import touch_device


def _group_key(granularity: str) -> dict[str, Any]:
    if granularity == "monthly":
        return {"year": {"$year": "$timestamp"}, "month": {"$month": "$timestamp"}}
    if granularity == "yearly":
        return {"year": {"$year": "$timestamp"}}

    raise ApiError.bad_request(
        f'cannot roll up into "{granularity}"; use monthly or yearly',
        {"allowed": settings.rolled_granularities},
    )


def _period_bounds(key: Mapping[str, int], granularity: str) -> tuple[datetime, dict[str, int]]:
    if granularity == "monthly":
        return (
            datetime(key["year"], key["month"], 1, tzinfo=UTC),
            {"year": key["year"], "month": key["month"]},
        )
    return datetime(key["year"], 1, 1, tzinfo=UTC), {"year": key["year"]}


def _summarise(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    total = sum(values)
    return {
        "count": len(values),
        "min": min(values),
        "max": max(values),
        "sum": round(total, 4),
        "avg": round(total / len(values), 4),
        "first": values[0],
        "last": values[-1],
    }


def rollup(
    device_type: str,
    index: int | str,
    granularity: str,
    window: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    group_key = _group_key(granularity)
    window = window or {}

    canonical = normalize_device_type(device_type)
    resolved_index = normalize_index(index)

    source = Target(
        device_type=canonical,
        index=resolved_index,
        granularity=settings.raw_granularity,
        device_id=f"{canonical}_{resolved_index}",
        collection=f"{canonical.lower()}_{resolved_index}_{settings.raw_granularity}",
    )
    destination = Target(
        device_type=canonical,
        index=resolved_index,
        granularity=granularity,
        device_id=source.device_id,
        collection=f"{canonical.lower()}_{resolved_index}_{granularity}",
    )

    source_collection = require_collection(source)

    match: dict[str, Any] = {}
    if window.get("from") or window.get("to"):
        bounds: dict[str, Any] = {}
        if window.get("from"):
            bounds["$gte"] = parse_timestamp(window["from"])
        if window.get("to"):
            bounds["$lte"] = parse_timestamp(window["to"])
        match["timestamp"] = bounds

    pipeline: list[dict[str, Any]] = []
    if match:
        pipeline.append({"$match": match})
    pipeline += [
        {"$sort": {"timestamp": 1}},
        {
            "$group": {
                "_id": group_key,
                "count": {"$sum": 1},
                "firstTimestamp": {"$first": "$timestamp"},
                "lastTimestamp": {"$last": "$timestamp"},
                "readings": {"$push": "$tags"},
            }
        },
        {"$sort": {"_id.year": 1, "_id.month": 1}},
    ]

    buckets = list(source_collection.aggregate(pipeline))
    if not buckets:
        return {
            "source": source.collection,
            "destination": destination.collection,
            "periods": 0,
            "upserted": 0,
            "modified": 0,
        }

    target_collection = ensure_collection(destination)
    now = datetime.now(UTC)
    operations: list[UpdateOne] = []

    for bucket in buckets:
        series: dict[str, list[float]] = {}

        for reading in bucket["readings"]:
            for name, value in (reading or {}).items():
                if not isinstance(value, (int, float)):  # bool is a subclass of int
                    continue
                series.setdefault(name, []).append(float(value))

        tags = {name: summary for name, values in series.items() if (summary := _summarise(values))}
        timestamp, period = _period_bounds(bucket["_id"], granularity)

        operations.append(
            UpdateOne(
                {"timestamp": timestamp},
                {
                    "$set": {
                        "deviceId": destination.device_id,
                        "deviceType": destination.device_type,
                        "index": destination.index,
                        "granularity": granularity,
                        "timestamp": timestamp,
                        "period": period,
                        "tags": tags,
                        "source": "rollup",
                        "meta": {
                            "sourceCollection": source.collection,
                            "sampleCount": bucket["count"],
                            "firstTimestamp": bucket["firstTimestamp"],
                            "lastTimestamp": bucket["lastTimestamp"],
                        },
                        "updatedAt": now,
                    },
                    "$setOnInsert": {"createdAt": now},
                },
                upsert=True,
            )
        )

    result = target_collection.bulk_write(operations, ordered=False)
    touch_device(get_db(), destination)

    return {
        "source": source.collection,
        "destination": destination.collection,
        "periods": len(buckets),
        "upserted": result.upserted_count,
        "modified": result.modified_count,
    }
