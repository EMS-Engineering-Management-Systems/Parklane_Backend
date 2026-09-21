"""The device catalogue (static) and the device registry (dynamic).

The catalogue is `app/data/devices.json`, generated from the approved Parklane
tag workbook by `tools/build_registry.py`. It drives tag validation, dummy-data
generation and the `/api/devices/catalogue` endpoints.

The registry is the `devices` collection: every device the system has actually
seen. A device is added there the first time a reading is posted for it, so
equipment does not have to be provisioned ahead of commissioning.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pymongo.database import Database

from app.services.naming import REGISTRY, Target, normalize_device_type

DEVICES_COLLECTION = "devices"

_BY_TYPE: dict[str, dict[str, Any]] = {device["type"].lower(): device for device in REGISTRY["devices"]}


def list_catalogue() -> list[dict[str, Any]]:
    return REGISTRY["devices"]


def find_in_catalogue(device_type: str) -> dict[str, Any] | None:
    return _BY_TYPE.get(str(device_type or "").lower())


def tag_map(device_type: str) -> dict[str, dict[str, Any]] | None:
    """Tag metadata for one device type, keyed by short tag name."""
    device = find_in_catalogue(device_type)
    if device is None:
        return None
    return {tag["name"]: tag for tag in device["tags"]}


def catalogue_summary() -> dict[str, Any]:
    return {
        "database": REGISTRY["database"],
        "source": REGISTRY["source"],
        "deviceCount": REGISTRY["deviceCount"],
        "tagCount": REGISTRY["tagCount"],
    }


def touch_device(db: Database, target: Target) -> None:
    """Registers or refreshes a device. Called on every write so a device that
    first appears via POST becomes discoverable straight away."""
    catalogue = find_in_catalogue(target.device_type)
    now = datetime.now(UTC)

    db[DEVICES_COLLECTION].update_one(
        {"deviceId": target.device_id},
        {
            "$set": {
                "deviceType": normalize_device_type(target.device_type),
                "index": target.index,
                "updatedAt": now,
            },
            "$addToSet": {"granularities": target.granularity},
            "$setOnInsert": {
                "deviceId": target.device_id,
                "label": (
                    f"{catalogue['label']} {target.index}"
                    if catalogue
                    else f"{target.device_type} {target.index}"
                ),
                "category": catalogue["category"] if catalogue else "Uncategorized",
                "inCatalogue": catalogue is not None,
                "status": "active",
                "createdAt": now,
            },
        },
        upsert=True,
    )
