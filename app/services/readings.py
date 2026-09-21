"""CRUD against the per-device reading collections."""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.collection import Collection

from app.core.database import get_db
from app.core.errors import ApiError
from app.models.reading import ReadingIn, ReadingPatch
from app.services.filters import Pagination, build_filter
from app.services.naming import Target
from app.services.registry import touch_device
from app.services.schema import build_document, build_patch


def to_object_id(value: str) -> ObjectId:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        raise ApiError.bad_request("id must be a 24-character hex ObjectId", {"id": value}) from None


def serialize(document: Any) -> Any:
    """ObjectId is not JSON-serialisable; everything else FastAPI handles."""
    if isinstance(document, list):
        return [serialize(item) for item in document]
    if isinstance(document, dict):
        return {key: serialize(value) for key, value in document.items()}
    if isinstance(document, ObjectId):
        return str(document)
    return document


def collection_exists(name: str) -> bool:
    return name in get_db().list_collection_names(filter={"name": name})


def ensure_collection(target: Target) -> Collection:
    """Creates the collection and its indexes on first write. MongoDB would
    create it implicitly, but doing it explicitly lets the indexes exist from
    document one."""
    db = get_db()

    if not collection_exists(target.collection):
        try:
            db.create_collection(target.collection)
        except Exception as error:  # a concurrent request may have won the race
            if "already exists" not in str(error).lower():
                raise

        collection = db[target.collection]
        collection.create_index([("timestamp", DESCENDING)], name="timestamp_desc")
        collection.create_index([("deviceId", ASCENDING), ("timestamp", DESCENDING)], name="device_timestamp")
        if target.granularity != "instant":
            collection.create_index(
                [("period.year", DESCENDING), ("period.month", DESCENDING)], name="period_desc"
            )

    return db[target.collection]


def require_collection(target: Target) -> Collection:
    """Read-only access; 404s rather than silently creating the namespace."""
    if not collection_exists(target.collection):
        raise ApiError.not_found(
            f'collection "{target.collection}" does not exist yet',
            {
                "collection": target.collection,
                "hint": (
                    f"POST a reading to /api/readings/{target.device_type}/"
                    f"{target.index}/{target.granularity} to create it"
                ),
            },
        )
    return get_db()[target.collection]


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def create(target: Target, payload: ReadingIn | list[ReadingIn]) -> dict[str, Any]:
    items = payload if isinstance(payload, list) else [payload]
    if not items:
        raise ApiError.bad_request("request body array must not be empty")

    documents: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    for position, item in enumerate(items):
        document, item_warnings = build_document(item, target)
        documents.append(document)
        if item_warnings:
            warnings.append({"index": position, "warnings": item_warnings})

    collection = ensure_collection(target)
    result = collection.insert_many(documents, ordered=False)
    touch_device(get_db(), target)

    for document, inserted_id in zip(documents, result.inserted_ids, strict=True):
        document["_id"] = inserted_id

    return {
        "collection": target.collection,
        "insertedCount": len(result.inserted_ids),
        "warnings": warnings,
        "data": serialize(documents if isinstance(payload, list) else documents[0]),
    }


def upsert_by_timestamp(target: Target, payload: ReadingIn) -> dict[str, Any]:
    """Idempotent write used by ingestion: one document per timestamp."""
    document, warnings = build_document(payload, target)
    collection = ensure_collection(target)

    created_at = document.pop("createdAt")

    data = collection.find_one_and_update(
        {"timestamp": document["timestamp"]},
        {"$set": document, "$setOnInsert": {"createdAt": created_at}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )

    touch_device(get_db(), target)
    return {"collection": target.collection, "warnings": warnings, "data": serialize(data)}


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def list_readings(
    target: Target,
    params: Mapping[str, str],
    pagination: Pagination,
    projection: dict[str, int] | None,
) -> dict[str, Any]:
    collection = require_collection(target)
    mongo_filter = build_filter(params)

    cursor = (
        collection.find(mongo_filter, projection)
        .sort(pagination.sort)
        .skip(pagination.skip)
        .limit(pagination.limit)
    )
    data = list(cursor)
    total = collection.count_documents(mongo_filter)

    return {
        "collection": target.collection,
        "deviceId": target.device_id,
        "filter": serialize(mongo_filter),
        "meta": pagination.meta(total),
        "data": serialize(data),
    }


def get_by_id(target: Target, reading_id: str) -> dict[str, Any]:
    collection = require_collection(target)
    document = collection.find_one({"_id": to_object_id(reading_id)})

    if document is None:
        raise ApiError.not_found(f"no reading with id {reading_id} in {target.collection}")
    return {"collection": target.collection, "data": serialize(document)}


def latest(target: Target, params: Mapping[str, str]) -> dict[str, Any]:
    collection = require_collection(target)
    document = collection.find_one(build_filter(params), sort=[("timestamp", DESCENDING)])

    if document is None:
        raise ApiError.not_found(f"{target.collection} has no readings matching the filter")
    return {"collection": target.collection, "data": serialize(document)}


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


def replace(target: Target, reading_id: str, payload: ReadingIn) -> dict[str, Any]:
    """PUT - full replace of the mutable part of the document."""
    collection = require_collection(target)
    object_id = to_object_id(reading_id)

    existing = collection.find_one({"_id": object_id})
    if existing is None:
        raise ApiError.not_found(f"no reading with id {reading_id} in {target.collection}")

    document, warnings = build_document(payload, target)
    document["createdAt"] = existing["createdAt"]

    data = collection.find_one_and_replace({"_id": object_id}, document, return_document=ReturnDocument.AFTER)
    return {"collection": target.collection, "warnings": warnings, "data": serialize(data)}


def update(target: Target, reading_id: str, payload: ReadingPatch) -> dict[str, Any]:
    """PATCH - merges the supplied fields, including individual tags."""
    collection = require_collection(target)
    patch, warnings = build_patch(payload, target)

    data = collection.find_one_and_update(
        {"_id": to_object_id(reading_id)}, {"$set": patch}, return_document=ReturnDocument.AFTER
    )
    if data is None:
        raise ApiError.not_found(f"no reading with id {reading_id} in {target.collection}")

    return {"collection": target.collection, "warnings": warnings, "data": serialize(data)}


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


def remove(target: Target, reading_id: str) -> dict[str, Any]:
    collection = require_collection(target)
    deleted = collection.find_one_and_delete({"_id": to_object_id(reading_id)})

    if deleted is None:
        raise ApiError.not_found(f"no reading with id {reading_id} in {target.collection}")
    return {"collection": target.collection, "deletedCount": 1, "data": serialize(deleted)}


def remove_many(target: Target, params: Mapping[str, str]) -> dict[str, Any]:
    collection = require_collection(target)
    mongo_filter = build_filter(params)

    if not mongo_filter and params.get("confirm") != "true":
        raise ApiError.bad_request(
            "refusing to delete every document without a filter; pass confirm=true or supply from/to",
            {"collection": target.collection},
        )

    result = collection.delete_many(mongo_filter)
    return {
        "collection": target.collection,
        "deletedCount": result.deleted_count,
        "filter": serialize(mongo_filter),
    }


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------


def stats(target: Target, params: Mapping[str, str]) -> dict[str, Any]:
    """min / max / avg / sum / last per numeric tag over the filtered window."""
    collection = require_collection(target)
    mongo_filter = build_filter(params)

    accumulators: dict[str, dict[str, float]] = {}
    count = 0
    first_timestamp: datetime | None = None
    last_timestamp: datetime | None = None

    cursor = collection.find(mongo_filter, {"tags": 1, "timestamp": 1}).sort([("timestamp", ASCENDING)])

    for document in cursor:
        count += 1
        if first_timestamp is None:
            first_timestamp = document.get("timestamp")
        last_timestamp = document.get("timestamp")

        for name, value in (document.get("tags") or {}).items():
            if not isinstance(value, (int, float)):  # bool is a subclass of int
                continue
            numeric = float(value)

            acc = accumulators.setdefault(
                name, {"count": 0, "sum": 0.0, "min": math.inf, "max": -math.inf, "last": 0.0}
            )
            acc["count"] += 1
            acc["sum"] += numeric
            acc["min"] = min(acc["min"], numeric)
            acc["max"] = max(acc["max"], numeric)
            acc["last"] = numeric

    tags = {
        name: {
            "count": int(acc["count"]),
            "min": acc["min"],
            "max": acc["max"],
            "sum": round(acc["sum"], 4),
            "avg": round(acc["sum"] / acc["count"], 4),
            "last": acc["last"],
        }
        for name, acc in accumulators.items()
    }

    return {
        "collection": target.collection,
        "count": count,
        "firstTimestamp": first_timestamp,
        "lastTimestamp": last_timestamp,
        "filter": serialize(mongo_filter),
        "tags": tags,
    }
