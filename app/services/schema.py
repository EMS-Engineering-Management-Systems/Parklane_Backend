"""Turns request payloads into stored documents, and reports what looked odd."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.core.errors import ApiError
from app.models.reading import ReadingIn, ReadingPatch
from app.services.naming import Target
from app.services.registry import find_in_catalogue, tag_map

# Fields a client is never allowed to set or overwrite directly.
IMMUTABLE_FIELDS = ("_id", "deviceId", "deviceType", "index", "granularity", "createdAt")

_SCALAR_TYPES = (int, float, bool, str)


def _as_utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def normalize_tag_key(key: str, device_type: str) -> str:
    """Tags arrive either short (`Voltage`) or fully qualified (`Transformer_Voltage`,
    `Transformer Voltage`) depending on whether the payload came from the API or
    from the BMS export. Both are reduced to the short form."""
    cleaned = "_".join(str(key).strip().replace("/", "_").split())
    prefix = f"{device_type}_"

    if cleaned.lower().startswith(prefix.lower()) and len(cleaned) > len(prefix):
        return cleaned[len(prefix) :]
    return cleaned


def validate_tags(
    raw_tags: dict[str, Any] | None, device_type: str
) -> tuple[dict[str, Any], list[str], list[dict]]:
    if raw_tags is None:
        raise ApiError.bad_request("tags is required and must be an object of tag name -> value")
    if not isinstance(raw_tags, dict):
        raise ApiError.bad_request('tags must be an object, e.g. {"Voltage": 10476, "Trip_Status": 0}')

    known = tag_map(device_type)
    tags: dict[str, Any] = {}
    unknown: list[str] = []
    out_of_range: list[dict[str, Any]] = []

    for raw_key, value in raw_tags.items():
        key = normalize_tag_key(raw_key, device_type)

        # dict values are allowed so a rolled-up summary can be written back.
        if value is not None and not isinstance(value, (*_SCALAR_TYPES, dict)):
            raise ApiError.bad_request(
                f'tag "{key}" must be a number, boolean, string, null or an aggregate object',
                {"tag": key, "received": type(value).__name__},
            )
        if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
            raise ApiError.bad_request(f'tag "{key}" must be a finite number', {"tag": key})

        tags[key] = value

        if known is None:
            continue
        definition = known.get(key)
        if definition is None:
            unknown.append(key)
        elif (
            definition["kind"] == "numeric"
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
            and not definition["min"] <= value <= definition["max"]
        ):
            out_of_range.append(
                {"tag": key, "value": value, "min": definition["min"], "max": definition["max"]}
            )

    if not tags:
        raise ApiError.bad_request("tags must contain at least one tag")

    return tags, unknown, out_of_range


def build_period(granularity: str, timestamp: datetime) -> dict[str, int] | None:
    """Monthly and yearly documents carry the period they summarise."""
    if granularity == "monthly":
        return {"year": timestamp.year, "month": timestamp.month}
    if granularity == "yearly":
        return {"year": timestamp.year}
    return None


def _warnings_for(
    target: Target, unknown: list[str], out_of_range: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []

    if unknown:
        warnings.append(
            {
                "code": "UNKNOWN_TAGS",
                "message": f"{len(unknown)} tag(s) are not in the approved tag list for {target.device_type}",
                "tags": unknown,
            }
        )
    if out_of_range:
        warnings.append(
            {
                "code": "OUT_OF_RANGE",
                "message": f"{len(out_of_range)} tag value(s) fall outside the configured engineering range",
                "values": out_of_range,
            }
        )
    if find_in_catalogue(target.device_type) is None:
        warnings.append(
            {
                "code": "UNKNOWN_DEVICE_TYPE",
                "message": (
                    f"{target.device_type} is not in the shipped catalogue; "
                    "the reading was stored without tag validation"
                ),
            }
        )
    return warnings


def build_document(
    payload: ReadingIn, target: Target, *, source: str = "api"
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Payload -> stored document, plus any non-blocking warnings."""
    timestamp = _as_utc(payload.timestamp)
    tags, unknown, out_of_range = validate_tags(payload.tags, target.device_type)
    now = datetime.now(UTC)

    document: dict[str, Any] = {
        "deviceId": target.device_id,
        "deviceType": target.device_type,
        "index": target.index,
        "granularity": target.granularity,
        "timestamp": timestamp,
        "tags": tags,
        "source": payload.source or source,
        "createdAt": now,
        "updatedAt": now,
    }

    period = build_period(target.granularity, timestamp)
    if period:
        document["period"] = period
    if payload.meta is not None:
        document["meta"] = payload.meta

    return document, _warnings_for(target, unknown, out_of_range)


def build_patch(payload: ReadingPatch, target: Target) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Partial update. Tags are set individually so untouched tags survive."""
    patch: dict[str, Any] = {"updatedAt": datetime.now(UTC)}
    unknown: list[str] = []
    out_of_range: list[dict[str, Any]] = []

    if payload.timestamp is not None:
        patch["timestamp"] = _as_utc(payload.timestamp)
        period = build_period(target.granularity, patch["timestamp"])
        if period:
            patch["period"] = period

    if payload.tags is not None:
        tags, unknown, out_of_range = validate_tags(payload.tags, target.device_type)
        for key, value in tags.items():
            patch[f"tags.{key}"] = value

    if payload.meta is not None:
        patch["meta"] = payload.meta
    if payload.source is not None:
        patch["source"] = payload.source

    if len(patch) == 1:
        raise ApiError.bad_request("no updatable fields supplied (timestamp, tags, meta, source)")

    warnings = []
    if unknown:
        warnings.append(
            {"code": "UNKNOWN_TAGS", "message": f"{len(unknown)} unknown tag(s)", "tags": unknown}
        )
    if out_of_range:
        warnings.append(
            {
                "code": "OUT_OF_RANGE",
                "message": f"{len(out_of_range)} value(s) outside the engineering range",
                "values": out_of_range,
            }
        )

    return patch, warnings
