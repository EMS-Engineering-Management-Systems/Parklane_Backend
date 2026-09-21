"""Collection and device naming.

Transformer + 1 + instant  ->  collection "transformer_1_instant"
Transformer + 1            ->  deviceId   "Transformer_1"
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from app.core.config import settings
from app.core.errors import ApiError

# Device types and tag names come straight from the BMS tag list, so allow the
# same character set: letters, digits and underscores.
DEVICE_TYPE_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
COLLECTION_PATTERN = re.compile(r"^([a-z][a-z0-9_]*)_(\d+)_([a-z]+)$")

REGISTRY_PATH = Path(__file__).resolve().parent.parent / "data" / "devices.json"
REGISTRY = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))

# Collection names are lower-case, so a device type read back out of one loses
# its casing. Mapping it through the catalogue keeps `transformer`, `Transformer`
# and `TRANSFORMER` resolving to a single deviceId.
_CANONICAL_TYPES = {device["type"].lower(): device["type"] for device in REGISTRY["devices"]}


@dataclass(frozen=True, slots=True)
class Target:
    """A resolved `{device_type}/{index}/{granularity}` route."""

    device_type: str
    index: int
    granularity: str
    device_id: str
    collection: str


def normalize_device_type(value: str) -> str:
    if not isinstance(value, str) or not DEVICE_TYPE_PATTERN.match(value.strip()):
        raise ApiError.bad_request(
            "deviceType must start with a letter and contain only letters, digits or underscores",
            {"deviceType": value},
        )
    trimmed = value.strip()
    # Devices outside the catalogue keep whatever casing the caller used.
    return _CANONICAL_TYPES.get(trimmed.lower(), trimmed)


def normalize_index(value: int | str) -> int:
    try:
        index = int(value)
    except (TypeError, ValueError):
        raise ApiError.bad_request(
            "device index must be an integer between 1 and 9999", {"index": value}
        ) from None

    if not 1 <= index <= 9999:
        raise ApiError.bad_request("device index must be an integer between 1 and 9999", {"index": value})
    return index


def normalize_granularity(value: str) -> str:
    granularity = str(value or "").strip().lower()
    if granularity not in settings.granularities:
        raise ApiError.bad_request(
            f"granularity must be one of: {', '.join(settings.granularities)}",
            {"granularity": value, "allowed": settings.granularities},
        )
    return granularity


def build_device_id(device_type: str, index: int | str) -> str:
    return f"{normalize_device_type(device_type)}_{normalize_index(index)}"


def build_collection_name(device_type: str, index: int | str, granularity: str) -> str:
    return (
        f"{normalize_device_type(device_type).lower()}"
        f"_{normalize_index(index)}"
        f"_{normalize_granularity(granularity)}"
    )


def parse_collection_name(name: str) -> dict | None:
    """Inverse of `build_collection_name`; None when the name is not ours."""
    match = COLLECTION_PATTERN.match(str(name or ""))
    if not match:
        return None

    device_type, index, granularity = match.groups()
    if granularity not in settings.granularities:
        return None

    return {
        "deviceType": device_type,
        "index": int(index),
        "granularity": granularity,
        "collection": name,
    }


def resolve_target(device_type: str, index: int | str, granularity: str) -> Target:
    """Validates and resolves the three route parameters in one step."""
    canonical = normalize_device_type(device_type)
    resolved_index = normalize_index(index)
    resolved_granularity = normalize_granularity(granularity)

    return Target(
        device_type=canonical,
        index=resolved_index,
        granularity=resolved_granularity,
        device_id=f"{canonical}_{resolved_index}",
        collection=f"{canonical.lower()}_{resolved_index}_{resolved_granularity}",
    )
