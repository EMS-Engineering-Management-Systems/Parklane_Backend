"""The device catalogue (static) and the device registry (dynamic)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Body, Path, Query, status
from pymongo import ASCENDING, ReturnDocument

from app.core.config import settings
from app.core.database import get_db
from app.core.errors import ApiError
from app.models.reading import DeviceIn, DevicePatch
from app.models.responses import (
    CatalogueDetailResponse,
    CatalogueResponse,
    DeviceListResponse,
    DeviceResponse,
)
from app.services.naming import (
    build_collection_name,
    build_device_id,
    normalize_device_type,
    parse_collection_name,
)
from app.services.readings import serialize
from app.services.registry import (
    DEVICES_COLLECTION,
    catalogue_summary,
    find_in_catalogue,
    list_catalogue,
)

router = APIRouter(prefix="/devices", tags=["devices"])

DeviceType = Annotated[str, Path(description="Device type, e.g. Transformer")]
DeviceIndex = Annotated[int, Path(ge=1, le=9999)]


def _collection_index() -> dict[str, list[dict[str, Any]]]:
    """Reading collections present in Atal, grouped by lower-cased deviceId."""
    index: dict[str, list[dict[str, Any]]] = {}

    for name in get_db().list_collection_names():
        parsed = parse_collection_name(name)
        if parsed is None:
            continue
        key = f"{parsed['deviceType']}_{parsed['index']}"
        index.setdefault(key, []).append(parsed)

    return index


def _collections_for(index: dict[str, list[dict[str, Any]]], device_id: str) -> list[dict[str, Any]]:
    return index.get(device_id.lower(), [])


# ---------------------------------------------------------------------------
# Catalogue - from the approved Parklane tag list, declared before /{device_type}
# ---------------------------------------------------------------------------


@router.get("/catalogue", response_model=CatalogueResponse, summary="All device types in the tag list")
def get_catalogue():
    data = [
        {
            "type": device["type"],
            "label": device["label"],
            "category": device["category"],
            "tagCount": len(device["tags"]),
            "collections": [
                build_collection_name(device["type"], 1, granularity)
                for granularity in settings.granularities
            ],
        }
        for device in list_catalogue()
    ]

    return {
        "success": True,
        **catalogue_summary(),
        "granularities": settings.granularities,
        "data": data,
    }


@router.get(
    "/catalogue/{device_type}",
    response_model=CatalogueDetailResponse,
    summary="One device type with its full tag list",
)
def get_catalogue_entry(device_type: DeviceType):
    device = find_in_catalogue(device_type)
    if device is None:
        raise ApiError.not_found(f'"{device_type}" is not in the Parklane tag catalogue')
    return {"success": True, "data": device}


# ---------------------------------------------------------------------------
# Registered devices
# ---------------------------------------------------------------------------


@router.get("", response_model=DeviceListResponse, summary="Devices known to the system")
def list_devices(
    deviceType: Annotated[str | None, Query()] = None,
    category: Annotated[str | None, Query()] = None,
    status_: Annotated[str | None, Query(alias="status")] = None,
):
    query: dict[str, Any] = {}
    if deviceType:
        query["deviceType"] = normalize_device_type(deviceType)
    if category:
        query["category"] = category
    if status_:
        query["status"] = status_

    cursor = get_db()[DEVICES_COLLECTION].find(query).sort([("deviceType", ASCENDING), ("index", ASCENDING)])
    index = _collection_index()

    data = [
        {**serialize(device), "collections": _collections_for(index, device["deviceId"])} for device in cursor
    ]
    return {"success": True, "total": len(data), "data": data}


@router.post(
    "",
    response_model=DeviceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a device ahead of any reading",
)
def create_device(payload: Annotated[DeviceIn, Body()]):
    device_type = normalize_device_type(payload.deviceType)
    device_id = build_device_id(device_type, payload.index)

    collection = get_db()[DEVICES_COLLECTION]
    if collection.find_one({"deviceId": device_id}) is not None:
        raise ApiError.conflict(f"device {device_id} is already registered", {"deviceId": device_id})

    catalogue = find_in_catalogue(device_type)
    now = datetime.now(UTC)

    document = {
        "deviceId": device_id,
        "deviceType": device_type,
        "index": payload.index,
        "label": payload.label
        or (f"{catalogue['label']} {payload.index}" if catalogue else f"{device_type} {payload.index}"),
        "category": payload.category or (catalogue["category"] if catalogue else "Uncategorized"),
        "location": payload.location,
        "description": payload.description,
        "inCatalogue": catalogue is not None,
        "status": payload.status,
        "granularities": [],
        "createdAt": now,
        "updatedAt": now,
    }

    collection.insert_one(document)
    return {"success": True, "data": serialize(document)}


@router.get(
    "/{device_type}/{index}",
    response_model=DeviceResponse,
    summary="One device, its collections and document counts",
)
def get_device(device_type: DeviceType, index: DeviceIndex):
    device_id = build_device_id(device_type, index)

    device = get_db()[DEVICES_COLLECTION].find_one({"deviceId": device_id})
    if device is None:
        raise ApiError.not_found(f"device {device_id} is not registered")

    collections = _collections_for(_collection_index(), device_id)
    counts = {
        entry["granularity"]: get_db()[entry["collection"]].count_documents({}) for entry in collections
    }

    return {
        "success": True,
        "data": {
            **serialize(device),
            "catalogue": find_in_catalogue(device["deviceType"]),
            "collections": collections,
            "documentCounts": counts,
        },
    }


def _apply_device_patch(device_id: str, payload: DevicePatch) -> dict[str, Any]:
    patch: dict[str, Any] = {
        key: value for key, value in payload.model_dump(exclude_unset=True).items() if value is not None
    }
    if not patch:
        raise ApiError.bad_request(
            "no updatable fields supplied (label, category, location, description, status, meta)"
        )
    patch["updatedAt"] = datetime.now(UTC)

    data = get_db()[DEVICES_COLLECTION].find_one_and_update(
        {"deviceId": device_id}, {"$set": patch}, return_document=ReturnDocument.AFTER
    )
    if data is None:
        raise ApiError.not_found(f"device {device_id} is not registered")
    return {"success": True, "data": serialize(data)}


@router.patch("/{device_type}/{index}", response_model=DeviceResponse, summary="Update device metadata")
def update_device(device_type: DeviceType, index: DeviceIndex, payload: Annotated[DevicePatch, Body()]):
    return _apply_device_patch(build_device_id(device_type, index), payload)


@router.put("/{device_type}/{index}", response_model=DeviceResponse, summary="Update device metadata")
def replace_device(device_type: DeviceType, index: DeviceIndex, payload: Annotated[DevicePatch, Body()]):
    return _apply_device_patch(build_device_id(device_type, index), payload)


@router.delete(
    "/{device_type}/{index}",
    response_model=DeviceResponse,
    summary="Unregister a device",
    description="Its reading collections are kept unless dropCollections=true is passed.",
)
def delete_device(
    device_type: DeviceType,
    index: DeviceIndex,
    dropCollections: Annotated[bool, Query(description="Also delete this device's data")] = False,
):
    device_id = build_device_id(device_type, index)

    deleted = get_db()[DEVICES_COLLECTION].find_one_and_delete({"deviceId": device_id})
    if deleted is None:
        raise ApiError.not_found(f"device {device_id} is not registered")

    dropped: list[str] = []
    if dropCollections:
        for entry in _collections_for(_collection_index(), device_id):
            get_db()[entry["collection"]].drop()
            dropped.append(entry["collection"])

    return {"success": True, "data": serialize(deleted), "droppedCollections": dropped}
