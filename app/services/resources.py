"""Resource registry and generic CRUD for the `Atal_Users` database.

Sixteen collections all need the same six operations. Rather than writing them
sixteen times, each collection is described once as a `Resource` - its model,
its business key, its indexes, what you may filter on - and one engine serves
them all. Adding a collection means adding a `Resource`, nothing else.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from pydantic import BaseModel
from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.collection import Collection
from pymongo.errors import BulkWriteError, DuplicateKeyError

from app.core.database import get_users_db
from app.core.errors import ApiError
from app.core.security import PASSWORD_FIELD, hash_password
from app.models import users as m

# Never selected by any read path.
HIDDEN_FIELDS = {PASSWORD_FIELD: 0}

CONTROL_PARAMS = {"page", "limit", "sort", "order", "fields", "confirm", "q"}


@dataclass(frozen=True)
class Resource:
    """One collection in `Atal_Users`."""

    slug: str  # URL segment, e.g. "visitor-passes"
    collection: str  # MongoDB collection, e.g. "visitor_passes"
    tag: str  # OpenAPI grouping
    summary: str
    model: type[BaseModel]  # POST / PUT body
    patch_model: type[BaseModel]  # PATCH body
    key: str | None = None  # business key, unique and usable in the URL
    key_type: type = str  # residents are keyed by a number, not a string
    unique_fields: tuple[str, ...] = ()  # extra single-field unique indexes
    unique: tuple[str, ...] = ()  # compound uniqueness when `key` is not enough
    indexes: tuple[tuple[str, int], ...] = ()
    filterable: tuple[str, ...] = ()
    searchable: tuple[str, ...] = ()  # fields the ?q= substring search covers
    sort: tuple[tuple[str, int], ...] = (("_id", ASCENDING),)
    time_field: str | None = None  # enables ?from= / ?to=
    hides_password: bool = False


RESOURCES: tuple[Resource, ...] = (
    Resource(
        slug="residents",
        collection="residents",
        tag="residents",
        summary="People living in the building, keyed by a unique residentId number",
        model=m.ResidentIn,
        patch_model=m.ResidentPatch,
        key="residentId",
        key_type=int,
        # The CSV's natural key is the login; residentId is the surrogate we assign.
        unique_fields=("username",),
        filterable=("residentId", "username", "unitCode", "status"),
        searchable=("name", "username", "residentId"),
        sort=(("residentId", ASCENDING),),
        hides_password=True,
    ),
    Resource(
        slug="units",
        collection="units",
        tag="units",
        summary="Apartment specification, finance, service charges and documents",
        model=m.UnitIn,
        patch_model=m.UnitPatch,
        key="unitCode",
        filterable=("unitCode", "tower", "building", "unitType", "handoverStatus", "bedrooms"),
        searchable=("unitCode", "tower"),
        sort=(("unitCode", ASCENDING),),
    ),
    Resource(
        slug="vehicles",
        collection="vehicles",
        tag="parking",
        summary="Resident vehicles",
        model=m.VehicleIn,
        patch_model=m.VehiclePatch,
        key="plate",
        filterable=("residentId", "plate", "model", "color"),
        searchable=("plate", "model"),
        sort=(("plate", ASCENDING),),
    ),
    Resource(
        slug="parking-slots",
        collection="parking_slots",
        tag="parking",
        summary="Allocated parking bays",
        model=m.ParkingSlotIn,
        patch_model=m.ParkingSlotPatch,
        key="slotCode",
        filterable=("slotCode", "residentId", "tower", "level", "unitCode"),
        searchable=("slotCode",),
        sort=(("slotCode", ASCENDING),),
    ),
    Resource(
        slug="parking-occupancy",
        collection="parking_occupancy",
        tag="parking",
        summary="Building-wide parking and EV charger occupancy, one reading per 15 minutes",
        model=m.ParkingOccupancyIn,
        patch_model=m.ParkingOccupancyPatch,
        unique=("timestamp",),
        indexes=(("timestamp", DESCENDING),),
        filterable=("timestamp",),
        sort=(("timestamp", DESCENDING),),
        time_field="timestamp",
    ),
    Resource(
        slug="parking-slot-status",
        collection="parking_slot_status",
        tag="parking",
        summary="Whether a resident's own bay is occupied, sampled over time",
        model=m.ParkingSlotStatusIn,
        patch_model=m.ParkingSlotStatusPatch,
        unique=("residentId", "timestamp"),
        indexes=(("residentId", ASCENDING), ("timestamp", DESCENDING)),
        filterable=("residentId", "slotCode", "isActive"),
        sort=(("timestamp", DESCENDING),),
        time_field="timestamp",
    ),
    Resource(
        slug="parking-activity",
        collection="parking_activity",
        tag="parking",
        summary="Car entered, guest parked, vehicle exited",
        model=m.ParkingActivityIn,
        patch_model=m.ParkingActivityPatch,
        unique=("residentId", "type", "eventAt"),
        indexes=(("residentId", ASCENDING), ("eventAt", DESCENDING)),
        filterable=("residentId", "type", "status"),
        searchable=("guestName",),
        sort=(("eventAt", DESCENDING),),
        time_field="eventAt",
    ),
    Resource(
        slug="parking-guest-requests",
        collection="parking_guest_requests",
        tag="parking",
        summary="Requests for a guest parking slot",
        model=m.ParkingGuestRequestIn,
        patch_model=m.ParkingGuestRequestPatch,
        unique=("residentId", "date", "startTime"),
        filterable=("residentId", "status", "date"),
        sort=(("date", DESCENDING),),
        time_field="date",
    ),
    Resource(
        slug="visitor-passes",
        collection="visitor_passes",
        tag="visitors",
        summary="Issued and upcoming visitor passes",
        model=m.VisitorPassIn,
        patch_model=m.VisitorPassPatch,
        unique=("residentId", "name", "visitDate", "visitTime"),
        indexes=(("residentId", ASCENDING), ("visitDate", DESCENDING)),
        filterable=("residentId", "status", "kind", "visitDate", "carPlate"),
        searchable=("name", "carPlate"),
        sort=(("visitDate", DESCENDING),),
        time_field="visitDate",
    ),
    Resource(
        slug="visitor-pass-requests",
        collection="visitor_pass_requests",
        tag="visitors",
        summary="Pass requests submitted by a resident, before issue",
        model=m.VisitorPassRequestIn,
        patch_model=m.VisitorPassRequestPatch,
        unique=("residentId", "mobileNumber", "visitDate"),
        filterable=("residentId", "status", "visitDate"),
        searchable=("mobileNumber",),
        sort=(("visitDate", DESCENDING),),
        time_field="visitDate",
    ),
    Resource(
        slug="maintenance-requests",
        collection="maintenance_requests",
        tag="maintenance",
        summary="Maintenance tickets with their status timeline",
        model=m.MaintenanceRequestIn,
        patch_model=m.MaintenanceRequestPatch,
        key="requestId",
        indexes=(("residentId", ASCENDING), ("status", ASCENDING)),
        filterable=("requestId", "residentId", "unitCode", "status", "category"),
        searchable=("title", "category", "requestId"),
        sort=(("submittedAt", DESCENDING),),
        time_field="submittedAt",
    ),
    Resource(
        slug="community-announcements",
        collection="community_announcements",
        tag="community",
        summary="Building-wide announcements",
        model=m.AnnouncementIn,
        patch_model=m.AnnouncementPatch,
        key="key",
        filterable=("key", "date"),
        searchable=("title",),
        sort=(("date", DESCENDING),),
        time_field="date",
    ),
    Resource(
        slug="community-events",
        collection="community_events",
        tag="community",
        summary="Community events open to residents",
        model=m.EventIn,
        patch_model=m.EventPatch,
        key="key",
        filterable=("key", "date", "location"),
        searchable=("title", "location"),
        sort=(("date", ASCENDING),),
        time_field="date",
    ),
    Resource(
        slug="community-event-participation",
        collection="community_event_participation",
        tag="community",
        summary="A resident's action state on an event: registered, going, and so on",
        model=m.EventParticipationIn,
        patch_model=m.EventParticipationPatch,
        unique=("residentId", "eventKey"),
        filterable=("residentId", "eventKey", "actionState"),
        sort=(("residentId", ASCENDING),),
    ),
    Resource(
        slug="community-alerts",
        collection="community_alerts",
        tag="community",
        summary="Service alerts and schedule changes",
        model=m.AlertIn,
        patch_model=m.AlertPatch,
        key="key",
        filterable=("key", "effectiveDate"),
        searchable=("title",),
        sort=(("effectiveDate", DESCENDING),),
        time_field="effectiveDate",
    ),
    Resource(
        slug="community-polls",
        collection="community_polls",
        tag="community",
        summary="Resident polls and their shared results",
        model=m.PollIn,
        patch_model=m.PollPatch,
        key="key",
        filterable=("key",),
        searchable=("title",),
        sort=(("key", ASCENDING),),
    ),
    Resource(
        slug="community-poll-votes",
        collection="community_poll_votes",
        tag="community",
        summary="Which option each resident chose",
        model=m.PollVoteIn,
        patch_model=m.PollVotePatch,
        unique=("residentId", "pollKey"),
        filterable=("residentId", "pollKey", "optionName"),
        sort=(("residentId", ASCENDING),),
    ),
    Resource(
        slug="weather",
        collection="weather",
        tag="environment",
        summary="Site weather, one reading per 15 minutes",
        model=m.WeatherIn,
        patch_model=m.WeatherPatch,
        unique=("timestamp",),
        indexes=(("timestamp", DESCENDING),),
        filterable=("condition",),
        sort=(("timestamp", DESCENDING),),
        time_field="timestamp",
    ),
)

BY_SLUG: dict[str, Resource] = {r.slug: r for r in RESOURCES}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def serialize(value: Any) -> Any:
    """ObjectId and date are not JSON-serialisable as they stand."""
    if isinstance(value, list):
        return [serialize(item) for item in value]
    if isinstance(value, dict):
        return {key: serialize(item) for key, item in value.items()}
    if isinstance(value, ObjectId):
        return str(value)
    return value


def _encode(value: Any) -> Any:
    """Pydantic gives back `date` objects; BSON only stores `datetime`."""
    if isinstance(value, dict):
        return {key: _encode(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_encode(item) for item in value]
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    return value


def collection_for(resource: Resource) -> Collection:
    return get_users_db()[resource.collection]


def ensure_indexes(resource: Resource) -> None:
    collection = collection_for(resource)

    if resource.key:
        collection.create_index(resource.key, unique=True, name=f"{resource.key}_unique")
    if resource.unique:
        collection.create_index(
            [(f, ASCENDING) for f in resource.unique],
            unique=True,
            name="_".join(resource.unique)[:100] + "_unique",
        )
    for field_name in resource.unique_fields:
        collection.create_index(field_name, unique=True, name=f"{field_name}_unique")
    for field_name, direction in resource.indexes:
        collection.create_index([(field_name, direction)], name=f"{field_name}_idx")


def ensure_all_indexes() -> None:
    for resource in RESOURCES:
        ensure_indexes(resource)


def _cast(raw: str) -> Any:
    if raw in ("true", "True"):
        return True
    if raw in ("false", "False"):
        return False
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        return raw


def _parse_moment(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise ApiError.bad_request(
            "date must be ISO-8601, e.g. 2026-09-21 or 2026-09-21T15:16:00Z",
            {"value": value},
        ) from None
    return parsed.replace(tzinfo=None) if parsed.tzinfo is None else parsed


def build_filter(resource: Resource, params: Mapping[str, str]) -> dict[str, Any]:
    """Exact match on the declared filterable fields, a time window on the
    declared time field, and `?q=` substring search over `searchable`."""
    query: dict[str, Any] = {}

    for name in resource.filterable:
        if name in params:
            query[name] = _cast(params[name])

    if resource.time_field and (params.get("from") or params.get("to")):
        window: dict[str, Any] = {}
        if params.get("from"):
            window["$gte"] = _parse_moment(params["from"])
        if params.get("to"):
            window["$lte"] = _parse_moment(params["to"])
        query[resource.time_field] = window

    term = params.get("q")
    if term and resource.searchable:
        escaped = "".join("\\" + c if c in ".^$*+?()[]{}|\\" else c for c in term)
        query["$or"] = [{f: {"$regex": escaped, "$options": "i"}} for f in resource.searchable]

    unknown = [
        k
        for k in params
        if k not in CONTROL_PARAMS and k not in resource.filterable and k not in ("from", "to")
    ]
    if unknown:
        raise ApiError.bad_request(
            f"unknown filter(s) for {resource.slug}: {', '.join(unknown)}",
            {"allowed": sorted({*resource.filterable, "from", "to", "q", *CONTROL_PARAMS})},
        )

    return query


def _key_query(resource: Resource, identifier: str) -> dict[str, Any]:
    """Accept either the business key (`1001`, `H-601`) or the ObjectId."""
    try:
        return {"_id": ObjectId(identifier)}
    except (InvalidId, TypeError):
        pass
    if resource.key:
        if resource.key_type is int:
            try:
                return {resource.key: int(identifier)}
            except (TypeError, ValueError):
                raise ApiError.bad_request(
                    f"{resource.key} must be a number, or pass the ObjectId",
                    {"id": identifier},
                ) from None
        return {resource.key: identifier}
    raise ApiError.bad_request(f"{resource.slug} documents are addressed by ObjectId", {"id": identifier})


def _duplicate_detail(error: Exception) -> dict[str, Any]:
    """Pulls the clashing key out of a duplicate-key error, whichever form it takes."""
    details = getattr(error, "details", None) or {}

    write_errors = details.get("writeErrors") or []
    if write_errors:
        first = write_errors[0]
        return {"index": first.get("index"), "keyValue": serialize(first.get("keyValue"))}

    if "keyValue" in details:
        return {"keyValue": serialize(details["keyValue"])}
    return {"detail": str(error)[:400]}


def _projection(resource: Resource) -> dict[str, int] | None:
    return dict(HIDDEN_FIELDS) if resource.hides_password else None


def _prepare(resource: Resource, payload: BaseModel) -> dict[str, Any]:
    """Model -> stored document, hashing the password if there is one."""
    document = _encode(payload.model_dump(exclude_unset=False, exclude_none=True))

    if resource.hides_password:
        plain = document.pop("password", None)
        if plain:
            document[PASSWORD_FIELD] = hash_password(plain)

    now = datetime.now()
    document["createdAt"] = now
    document["updatedAt"] = now
    return document


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def create(resource: Resource, payload: BaseModel | list[BaseModel]) -> dict[str, Any]:
    items = payload if isinstance(payload, list) else [payload]
    if not items:
        raise ApiError.bad_request("request body array must not be empty")

    documents = [_prepare(resource, item) for item in items]
    collection = collection_for(resource)

    try:
        result = collection.insert_many(documents, ordered=True)
    except (BulkWriteError, DuplicateKeyError) as error:
        # insert_many wraps per-document failures in a BulkWriteError, so a
        # duplicate key arrives here rather than as a bare DuplicateKeyError.
        raise ApiError.conflict(
            f"a {resource.slug} document with that key already exists",
            _duplicate_detail(error),
        ) from None

    for document, inserted_id in zip(documents, result.inserted_ids, strict=True):
        document["_id"] = inserted_id
        document.pop(PASSWORD_FIELD, None)

    return {
        "resource": resource.slug,
        "collection": resource.collection,
        "insertedCount": len(result.inserted_ids),
        "data": serialize(documents if isinstance(payload, list) else documents[0]),
    }


def list_documents(
    resource: Resource, params: Mapping[str, str], page: int, limit: int, sort: list[tuple[str, int]]
) -> dict[str, Any]:
    collection = collection_for(resource)
    query = build_filter(resource, params)

    cursor = collection.find(query, _projection(resource)).sort(sort).skip((page - 1) * limit).limit(limit)
    data = list(cursor)
    total = collection.count_documents(query)
    pages = -(-total // limit) if limit else 0

    return {
        "resource": resource.slug,
        "collection": resource.collection,
        "filter": serialize(query),
        "meta": {
            "total": total,
            "page": page,
            "limit": limit,
            "pages": pages,
            "hasNext": page < pages,
            "hasPrev": page > 1,
        },
        "data": serialize(data),
    }


def get_one(resource: Resource, identifier: str) -> dict[str, Any]:
    document = collection_for(resource).find_one(_key_query(resource, identifier), _projection(resource))
    if document is None:
        raise ApiError.not_found(f"no {resource.slug} document matching {identifier!r}")

    return {
        "resource": resource.slug,
        "collection": resource.collection,
        "data": serialize(document),
    }


def replace(resource: Resource, identifier: str, payload: BaseModel) -> dict[str, Any]:
    collection = collection_for(resource)
    query = _key_query(resource, identifier)

    existing = collection.find_one(query)
    if existing is None:
        raise ApiError.not_found(f"no {resource.slug} document matching {identifier!r}")

    document = _prepare(resource, payload)
    document["createdAt"] = existing.get("createdAt", document["createdAt"])
    # A PUT without a password must not silently wipe the stored hash.
    if resource.hides_password and PASSWORD_FIELD not in document and PASSWORD_FIELD in existing:
        document[PASSWORD_FIELD] = existing[PASSWORD_FIELD]

    try:
        data = collection.find_one_and_replace(
            {"_id": existing["_id"]},
            document,
            projection=_projection(resource),
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError:
        raise ApiError.conflict("that change collides with an existing document") from None

    return {"resource": resource.slug, "collection": resource.collection, "data": serialize(data)}


def update(resource: Resource, identifier: str, payload: BaseModel) -> dict[str, Any]:
    patch = _encode(payload.model_dump(exclude_unset=True))

    if resource.hides_password:
        plain = patch.pop("password", None)
        if plain:
            patch[PASSWORD_FIELD] = hash_password(plain)

    if not patch:
        raise ApiError.bad_request("no updatable fields supplied")
    patch["updatedAt"] = datetime.now()

    try:
        data = collection_for(resource).find_one_and_update(
            _key_query(resource, identifier),
            {"$set": patch},
            projection=_projection(resource),
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError:
        raise ApiError.conflict("that change collides with an existing document") from None

    if data is None:
        raise ApiError.not_found(f"no {resource.slug} document matching {identifier!r}")

    return {"resource": resource.slug, "collection": resource.collection, "data": serialize(data)}


def delete_one(resource: Resource, identifier: str) -> dict[str, Any]:
    deleted = collection_for(resource).find_one_and_delete(
        _key_query(resource, identifier), projection=_projection(resource)
    )
    if deleted is None:
        raise ApiError.not_found(f"no {resource.slug} document matching {identifier!r}")

    return {
        "resource": resource.slug,
        "collection": resource.collection,
        "deletedCount": 1,
        "data": serialize(deleted),
    }


def delete_many(resource: Resource, params: Mapping[str, str]) -> dict[str, Any]:
    query = build_filter(resource, params)

    if not query and params.get("confirm") != "true":
        raise ApiError.bad_request(
            "refusing to delete every document without a filter; pass confirm=true",
            {"collection": resource.collection},
        )

    result = collection_for(resource).delete_many(query)
    return {
        "resource": resource.slug,
        "collection": resource.collection,
        "deletedCount": result.deleted_count,
        "filter": serialize(query),
    }


def overview() -> list[dict[str, Any]]:
    db = get_users_db()
    return [
        {
            "resource": r.slug,
            "collection": r.collection,
            "tag": r.tag,
            "summary": r.summary,
            "key": r.key or ("+".join(r.unique) if r.unique else "_id"),
            "documents": db[r.collection].count_documents({}),
            "path": f"/api/users/{r.slug}",
        }
        for r in RESOURCES
    ]
